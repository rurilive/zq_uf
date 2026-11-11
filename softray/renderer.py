"""A tiny tiled software rasterizer with coarse depth rejection and early-z."""
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class Vertex:
    x: float
    y: float
    z: float
    u: float = 0.0
    v: float = 0.0


@dataclass
class Triangle:
    a: Vertex
    b: Vertex
    c: Vertex
    color: Tuple[int, int, int] = (255, 255, 255)
    alpha: float = 1.0


def _edge(ax, ay, bx, by, px, py):
    return (px - ax) * (by - ay) - (py - ay) * (bx - ax)


def _new_stats() -> Dict[str, int]:
    return {
        "tiles": 0,
        "tile_culled": 0,
        "earlyz_rejected": 0,
        "pixels_written": 0,
        "shader_calls": 0,
        "cache_hits": 0,
    }


class Renderer:
    def __init__(
        self,
        width: int,
        height: int,
        shader: Optional[Callable] = None,
        tile_size: int = 32,
        num_threads: Optional[int] = None,
        cache_size: int = 256,
    ):
        if tile_size <= 0:
            raise ValueError("tile_size must be positive")
        if num_threads is not None and num_threads <= 0:
            raise ValueError("num_threads must be positive")
        self.width = width
        self.height = height
        self.shader = shader
        self.tile_size = tile_size
        self.num_threads = num_threads or (os.cpu_count() or 1)
        self.cache_size = cache_size
        self.color = [(0, 0, 0)] * (width * height)
        self.depth = [float("inf")] * (width * height)
        self.stats = _new_stats()

    def clear(self):
        self.color = [(0, 0, 0)] * (self.width * self.height)
        self.depth = [float("inf")] * (self.width * self.height)

    # ------------------------------------------------------------------
    # Baseline per-pixel path (kept as the reference implementation)
    # ------------------------------------------------------------------
    def draw_triangle(self, tri: Triangle):
        min_x = max(0, int(min(tri.a.x, tri.b.x, tri.c.x)))
        max_x = min(self.width - 1, int(max(tri.a.x, tri.b.x, tri.c.x)))
        min_y = max(0, int(min(tri.a.y, tri.b.y, tri.c.y)))
        max_y = min(self.height - 1, int(max(tri.a.y, tri.b.y, tri.c.y)))
        area = _edge(tri.a.x, tri.a.y, tri.b.x, tri.b.y, tri.c.x, tri.c.y)
        if area == 0:
            return
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                px, py = x + 0.5, y + 0.5
                w0 = _edge(tri.b.x, tri.b.y, tri.c.x, tri.c.y, px, py)
                w1 = _edge(tri.c.x, tri.c.y, tri.a.x, tri.a.y, px, py)
                w2 = _edge(tri.a.x, tri.a.y, tri.b.x, tri.b.y, px, py)
                if not ((w0 >= 0 and w1 >= 0 and w2 >= 0) or (w0 <= 0 and w1 <= 0 and w2 <= 0)):
                    continue
                w0, w1, w2 = w0 / area, w1 / area, w2 / area
                z = w0 * tri.a.z + w1 * tri.b.z + w2 * tri.c.z
                index = y * self.width + x
                if z >= self.depth[index]:
                    continue
                if self.shader:
                    u = w0 * tri.a.u + w1 * tri.b.u + w2 * tri.c.u
                    v = w0 * tri.a.v + w1 * tri.b.v + w2 * tri.c.v
                    color = self.shader(u, v)
                else:
                    color = tri.color
                self.color[index] = color
                self.depth[index] = z

    def render_baseline(self, triangles: List[Triangle]):
        """Legacy full-screen per-pixel path, kept for comparison."""
        self.clear()
        for tri in triangles:
            self.draw_triangle(tri)

    # ------------------------------------------------------------------
    # Tiled pipeline
    # ------------------------------------------------------------------
    def render(self, triangles: List[Triangle]):
        self.clear()
        self.stats = _new_stats()
        triangles = list(triangles)
        opaque = [t for t in triangles if t.alpha >= 1.0]
        transparent = [t for t in triangles if t.alpha < 1.0]
        self._render_pass(opaque, write_depth=True)
        if transparent:
            # Back-to-front by centroid depth; transparent fragments never
            # write depth, so they can never be wrongly culled by early-z.
            transparent.sort(key=lambda t: -(t.a.z + t.b.z + t.c.z))
            self._render_pass(transparent, write_depth=False)

    def _tile_region_zmax(self, x0, y0, x1, y1):
        width = self.width
        depth = self.depth
        return max(
            max(depth[y * width + x0: y * width + x1])
            for y in range(y0, y1)
        )

    def _render_pass(self, triangles: List[Triangle], write_depth: bool):
        if not triangles:
            return
        ts = self.tile_size
        tiles_x = (self.width + ts - 1) // ts
        tiles_y = (self.height + ts - 1) // ts
        bins: List[List[Triangle]] = [[] for _ in range(tiles_x * tiles_y)]
        for tri in triangles:
            min_x = max(0, int(min(tri.a.x, tri.b.x, tri.c.x)))
            max_x = min(self.width - 1, int(max(tri.a.x, tri.b.x, tri.c.x)))
            min_y = max(0, int(min(tri.a.y, tri.b.y, tri.c.y)))
            max_y = min(self.height - 1, int(max(tri.a.y, tri.b.y, tri.c.y)))
            if min_x > max_x or min_y > max_y:
                continue
            for ty in range(min_y // ts, max_y // ts + 1):
                for tx in range(min_x // ts, max_x // ts + 1):
                    bins[ty * tiles_x + tx].append(tri)
        jobs = []
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                tris = bins[ty * tiles_x + tx]
                if not tris:
                    continue
                x0, y0 = tx * ts, ty * ts
                x1, y1 = min(x0 + ts, self.width), min(y0 + ts, self.height)
                jobs.append((x0, y0, x1, y1, tris, write_depth))
        self.stats["tiles"] += len(jobs)
        if self.num_threads <= 1 or len(jobs) <= 1:
            results = [self._render_tile(*job) for job in jobs]
        else:
            # Each tile writes a disjoint screen region, so tiles are race-free.
            with ThreadPoolExecutor(max_workers=self.num_threads) as pool:
                results = list(pool.map(lambda job: self._render_tile(*job), jobs))
        for res in results:
            for key, value in res.items():
                self.stats[key] += value

    def _render_tile(self, x0, y0, x1, y1, triangles, write_depth):
        stats = _new_stats()
        width = self.width
        color = self.color
        depth = self.depth
        shader = self.shader
        cache_size = self.cache_size if shader else 0
        cache: Dict[Tuple[float, float], Tuple[int, int, int]] = {}
        tile_zmax = float("inf")
        zmax_valid = False
        for tri in triangles:
            area = _edge(tri.a.x, tri.a.y, tri.b.x, tri.b.y, tri.c.x, tri.c.y)
            if area == 0:
                continue
            # Coarse tile-level depth rejection (Hi-Z style): if the nearest
            # possible fragment is farther than the tile's farthest stored
            # depth, nothing in this tile can pass the depth test.
            if not zmax_valid:
                tile_zmax = self._tile_region_zmax(x0, y0, x1, y1)
                zmax_valid = True
            if min(tri.a.z, tri.b.z, tri.c.z) >= tile_zmax:
                stats["tile_culled"] += 1
                continue
            min_x = max(x0, int(min(tri.a.x, tri.b.x, tri.c.x)))
            max_x = min(x1 - 1, int(max(tri.a.x, tri.b.x, tri.c.x)))
            min_y = max(y0, int(min(tri.a.y, tri.b.y, tri.c.y)))
            max_y = min(y1 - 1, int(max(tri.a.y, tri.b.y, tri.c.y)))
            wrote = False
            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    px, py = x + 0.5, y + 0.5
                    w0 = _edge(tri.b.x, tri.b.y, tri.c.x, tri.c.y, px, py)
                    w1 = _edge(tri.c.x, tri.c.y, tri.a.x, tri.a.y, px, py)
                    w2 = _edge(tri.a.x, tri.a.y, tri.b.x, tri.b.y, px, py)
                    if not ((w0 >= 0 and w1 >= 0 and w2 >= 0) or (w0 <= 0 and w1 <= 0 and w2 <= 0)):
                        continue
                    w0, w1, w2 = w0 / area, w1 / area, w2 / area
                    z = w0 * tri.a.z + w1 * tri.b.z + w2 * tri.c.z
                    index = y * width + x
                    # Early-z: the fragment shader only runs for pixels that
                    # pass the depth test.
                    if z >= depth[index]:
                        stats["earlyz_rejected"] += 1
                        continue
                    if shader:
                        u = w0 * tri.a.u + w1 * tri.b.u + w2 * tri.c.u
                        v = w0 * tri.a.v + w1 * tri.b.v + w2 * tri.c.v
                        key = (u, v)
                        if cache_size and key in cache:
                            pixel = cache[key]
                            stats["cache_hits"] += 1
                        else:
                            pixel = shader(u, v)
                            stats["shader_calls"] += 1
                            if cache_size:
                                if len(cache) >= cache_size:
                                    cache.clear()
                                cache[key] = pixel
                    else:
                        pixel = tri.color
                    if write_depth:
                        color[index] = pixel
                        depth[index] = z
                    else:
                        alpha = max(0.0, min(1.0, tri.alpha))
                        dst = color[index]
                        color[index] = tuple(
                            int(round(sc * alpha + dc * (1.0 - alpha)))
                            for sc, dc in zip(pixel, dst)
                        )
                    stats["pixels_written"] += 1
                    wrote = True
            if wrote and write_depth:
                zmax_valid = False
        return stats

