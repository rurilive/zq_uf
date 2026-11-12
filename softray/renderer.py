"""Tile-based software rasterizer with coarse depth rejection and early-z.

Rendering model
---------------
The screen is split into non-overlapping square tiles (default 32x32). Every
triangle is sent to each tile its bounding box touches; cross-tile triangles
are rasterised independently inside every touched tile and bounding boxes are
clipped to the tile rectangle, so edge tiles never leave the framebuffer.

Opaque pass (per tile):
  1. Coarse depth pre-test. Once every cell of a tile has been written, the
     tile lazily tracks ``dmax`` -- the maximum depth currently stored in it.
     The triangle is clipped against the tile rectangle
     (Sutherland-Hodgman, interpolating z); when its minimum z inside the
     rectangle is >= ``dmax`` every fragment it would produce is hidden, so
     the whole triangle is rejected without touching a pixel. Partially
     filled tiles have no usable bound and skip straight to per-pixel
     early-z, which keeps the test conservative at all times.
  2. Per-pixel early-z. The depth test runs *before* the fragment shader, so
     hidden fragments never pay shading / texture cost.
  3. Colour and depth are only written by fragments that pass the test.

Transparent pass:
  Triangles with ``alpha < 255`` are sorted back-to-front and blended with
  ``src*alpha + dst*(255-alpha)`` over 255. They deliberately bypass early-z
  and never write depth: a transparent surface must stay visible even when an
  opaque surface in front of it is already in the depth buffer.

Parallelism
-----------
Tiles own disjoint framebuffer index ranges, so worker threads never touch
the same colour/depth cell. Triangle lists and textures are read-only during
rendering and every per-tile structure (depth bounds, texture cache) is
thread-local. Output is therefore bit-identical for any worker count,
including ``workers=1``.
"""

import os
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


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
    alpha: int = 255


class Texture:
    """Nearest-neighbour, edge-clamped 2D texture stored as a flat RGB list."""

    def __init__(self, pixels: List[Tuple[int, int, int]], width: int, height: int):
        if width <= 0 or height <= 0 or len(pixels) != width * height:
            raise ValueError("texture dimensions do not match pixel count")
        self.pixels = list(pixels)
        self.width = width
        self.height = height

    @classmethod
    def from_rows(cls, rows: List[List[Tuple[int, int, int]]]) -> "Texture":
        height = len(rows)
        width = len(rows[0]) if height else 0
        pixels = [texel for row in rows for texel in row]
        return cls(pixels, width, height)

    def sample(self, u: float, v: float) -> Tuple[int, int, int]:
        x = int(u * self.width)
        y = int(v * self.height)
        if x < 0:
            x = 0
        elif x >= self.width:
            x = self.width - 1
        if y < 0:
            y = 0
        elif y >= self.height:
            y = self.height - 1
        return self.pixels[y * self.width + x]


class _TileTextureCache:
    """Small per-tile LRU cache in front of Texture.sample.

    Entries are keyed by integer texel coordinates, so a hit returns exactly
    the texel the shader would have sampled anyway -- caching can never change
    the final colour, only skip repeated lookups of the same texel.
    """

    __slots__ = ("texture", "capacity", "entries", "hits", "misses")

    def __init__(self, texture: Texture, capacity: int):
        self.texture = texture
        self.capacity = capacity
        self.entries: "OrderedDict[int, Tuple[int, int, int]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def sample(self, u: float, v: float) -> Tuple[int, int, int]:
        texture = self.texture
        x = int(u * texture.width)
        y = int(v * texture.height)
        if x < 0:
            x = 0
        elif x >= texture.width:
            x = texture.width - 1
        if y < 0:
            y = 0
        elif y >= texture.height:
            y = texture.height - 1
        key = y * texture.width + x
        entries = self.entries
        if key in entries:
            entries.move_to_end(key)
            self.hits += 1
            return entries[key]
        texel = texture.pixels[key]
        entries[key] = texel
        self.misses += 1
        if len(entries) > self.capacity:
            entries.popitem(last=False)
        return texel


def _edge(ax, ay, bx, by, px, py):
    return (px - ax) * (by - ay) - (py - ay) * (bx - ax)


def _clip_min_z(tri: Triangle, x0: float, y0: float, x1: float, y1: float) -> Optional[float]:
    """Minimum z of ``tri`` inside rectangle [x0, x1) x [y0, y1).

    Sutherland-Hodgman clipping in screen space against the four rectangle
    edges, interpolating z at every intersection. Returns ``None`` when the
    triangle does not overlap the rectangle. Because z is affine over the
    triangle, the minimum over the clipped polygon is attained at one of its
    vertices.
    """
    points = [
        (tri.a.x, tri.a.y, tri.a.z),
        (tri.b.x, tri.b.y, tri.b.z),
        (tri.c.x, tri.c.y, tri.c.z),
    ]
    # (axis, bound, keep-when-greater-or-equal)
    clip_edges = ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False))
    for axis, bound, keep_ge in clip_edges:
        clipped = []
        if not points:
            return None
        previous = points[-1]
        prev_inside = previous[axis] >= bound if keep_ge else previous[axis] <= bound
        for current in points:
            cur_inside = current[axis] >= bound if keep_ge else current[axis] <= bound
            if cur_inside != prev_inside:
                # Equal coordinates can never straddle the bound, so the
                # denominator is non-zero whenever this branch is taken.
                t = (bound - previous[axis]) / (current[axis] - previous[axis])
                clipped.append((
                    previous[0] + t * (current[0] - previous[0]),
                    previous[1] + t * (current[1] - previous[1]),
                    previous[2] + t * (current[2] - previous[2]),
                ))
            if cur_inside:
                clipped.append(current)
            previous = current
            prev_inside = cur_inside
        points = clipped
        if not points:
            return None
    return min(point[2] for point in points)


class Renderer:
    def __init__(
        self,
        width: int,
        height: int,
        shader: Optional[Callable] = None,
        *,
        tile_size: int = 32,
        workers: Optional[int] = None,
        texture: Optional[Texture] = None,
        use_cache: bool = True,
        cache_size: int = 64,
    ):
        if width <= 0 or height <= 0:
            raise ValueError("framebuffer dimensions must be positive")
        if tile_size < 1:
            raise ValueError("tile_size must be >= 1")
        if workers is None:
            workers = os.cpu_count() or 1
        if workers < 1:
            raise ValueError("workers must be >= 1")
        if cache_size < 1:
            raise ValueError("cache_size must be >= 1")
        self.width = width
        self.height = height
        self.shader = shader
        self.tile_size = tile_size
        self.workers = workers
        self.texture = texture
        self.use_cache = use_cache
        self.cache_size = cache_size
        self.color: List[Tuple[int, int, int]] = [(0, 0, 0)] * (width * height)
        self.depth: List[float] = [float("inf")] * (width * height)
        # Per-frame counters, refreshed on every render() call.
        self.shader_calls = 0
        self.coarse_culled = 0
        self.early_z_rejected = 0
        self.fragments_covered = 0
        self.cache_hits = 0
        self.cache_misses = 0

    def clear(self):
        self.color = [(0, 0, 0)] * (self.width * self.height)
        self.depth = [float("inf")] * (self.width * self.height)

    # ------------------------------------------------------------------ tiles

    def _tiles(self):
        """Yield (x0, y0, x1, y1) inclusive tile rectangles, never out of bounds."""
        size = self.tile_size
        for y0 in range(0, self.height, size):
            y1 = min(y0 + size, self.height) - 1
            for x0 in range(0, self.width, size):
                x1 = min(x0 + size, self.width) - 1
                yield x0, y0, x1, y1

    def _bbox(self, tri: Triangle):
        # int() truncation matches the baseline rasteriser exactly.
        min_x = max(0, int(min(tri.a.x, tri.b.x, tri.c.x)))
        max_x = min(self.width - 1, int(max(tri.a.x, tri.b.x, tri.c.x)))
        min_y = max(0, int(min(tri.a.y, tri.b.y, tri.c.y)))
        max_y = min(self.height - 1, int(max(tri.a.y, tri.b.y, tri.c.y)))
        return min_x, max_x, min_y, max_y

    # ----------------------------------------------------------------- render

    def render(self, triangles: List[Triangle]):
        self.clear()
        opaque = []
        transparent = []
        for tri in triangles:
            if tri.alpha >= 255:
                opaque.append(tri)
            else:
                transparent.append(tri)
        # Back-to-front: largest z first. Python's sort is stable, so ties keep
        # submission order.
        transparent.sort(key=lambda tri: -(tri.a.z + tri.b.z + tri.c.z) / 3.0)

        tiles = list(self._tiles())
        stats = [self._new_stats() for _ in tiles]
        if self.workers > 1 and len(tiles) > 1:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [
                    pool.submit(self._render_tile, tile, opaque, transparent, stats[index])
                    for index, tile in enumerate(tiles)
                ]
                for future in futures:
                    future.result()
        else:
            for index, tile in enumerate(tiles):
                self._render_tile(tile, opaque, transparent, stats[index])

        self.shader_calls = sum(item["shader"] for item in stats)
        self.coarse_culled = sum(item["coarse"] for item in stats)
        self.early_z_rejected = sum(item["early_reject"] for item in stats)
        self.fragments_covered = sum(item["fragments"] for item in stats)
        self.cache_hits = sum(item["cache_hits"] for item in stats)
        self.cache_misses = sum(item["cache_misses"] for item in stats)

    def render_legacy(self, triangles: List[Triangle]):
        """Reference path: the original full-screen per-pixel rasteriser.

        Kept bit-for-bit equivalent to the baseline behaviour (alpha ignored)
        so demo.py and tests can compare against the optimised pipeline.
        """
        self.clear()
        shader_calls = 0
        for tri in triangles:
            min_x, max_x, min_y, max_y = self._bbox(tri)
            area = _edge(tri.a.x, tri.a.y, tri.b.x, tri.b.y, tri.c.x, tri.c.y)
            if area == 0:
                continue
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
                        shader_calls += 1
                    else:
                        color = tri.color
                    self.color[index] = color
                    self.depth[index] = z
        self.shader_calls = shader_calls
        self.coarse_culled = 0
        self.early_z_rejected = 0
        self.fragments_covered = shader_calls
        self.cache_hits = 0
        self.cache_misses = 0

    @staticmethod
    def _new_stats():
        return {
            "shader": 0,
            "coarse": 0,
            "early_reject": 0,
            "fragments": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

    def _new_cache(self):
        if self.texture is not None and self.use_cache:
            return _TileTextureCache(self.texture, self.cache_size)
        return None

    def _render_tile(self, tile, opaque, transparent, stats):
        x0, y0, x1, y1 = tile
        width = self.width
        depth = self.depth
        color = self.color
        shader = self.shader
        texture = self.texture
        cache = self._new_cache()

        # Coarse depth bound, lazily maintained. Two distinct states:
        #   * tile not fully covered yet -> bound is unusable (no culling;
        #     every triangle still goes through per-pixel early-z);
        #   * tile fully covered -> bound = maximum depth stored in the tile.
        # A full rescan runs only when a new fragment is written into a fully
        # covered tile, so partial-fill frames pay nothing for the bound.
        bound_valid = False
        dmax = float("inf")
        bound_dirty = False

        # ------------------------------------------------------------ opaque
        for tri in opaque:
            bx0, bx1, by0, by1 = self._bbox(tri)
            if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
                continue
            z_min = _clip_min_z(tri, x0, y0, x1 + 1, y1 + 1)
            if z_min is None:
                continue
            if bound_valid and bound_dirty:
                dmax = self._scan_tile_dmax(x0, y0, x1, y1)
                # A previously full tile can only stay full (writes replace
                # finite depths), so the rescanned bound remains valid.
                bound_dirty = False
            # Coarse depth pre-test: every fragment of this triangle is at
            # least z_min deep, and every stored fragment is at most dmax deep.
            if bound_valid and z_min >= dmax:
                stats["coarse"] += 1
                continue

            cx0 = bx0 if bx0 > x0 else x0
            cx1 = bx1 if bx1 < x1 else x1
            cy0 = by0 if by0 > y0 else y0
            cy1 = by1 if by1 < y1 else y1
            area = _edge(tri.a.x, tri.a.y, tri.b.x, tri.b.y, tri.c.x, tri.c.y)
            if area == 0:
                continue
            wrote = False
            for y in range(cy0, cy1 + 1):
                row = y * width
                for x in range(cx0, cx1 + 1):
                    px, py = x + 0.5, y + 0.5
                    w0 = _edge(tri.b.x, tri.b.y, tri.c.x, tri.c.y, px, py)
                    w1 = _edge(tri.c.x, tri.c.y, tri.a.x, tri.a.y, px, py)
                    w2 = _edge(tri.a.x, tri.a.y, tri.b.x, tri.b.y, px, py)
                    if not ((w0 >= 0 and w1 >= 0 and w2 >= 0) or (w0 <= 0 and w1 <= 0 and w2 <= 0)):
                        continue
                    stats["fragments"] += 1
                    w0, w1, w2 = w0 / area, w1 / area, w2 / area
                    z = w0 * tri.a.z + w1 * tri.b.z + w2 * tri.c.z
                    index = row + x
                    # Early-z: depth test before any shading work.
                    if z >= depth[index]:
                        stats["early_reject"] += 1
                        continue
                    if texture is not None:
                        u = w0 * tri.a.u + w1 * tri.b.u + w2 * tri.c.u
                        v = w0 * tri.a.v + w1 * tri.b.v + w2 * tri.c.v
                        if cache is not None:
                            fragment_color = cache.sample(u, v)
                        else:
                            fragment_color = texture.sample(u, v)
                    elif shader is not None:
                        u = w0 * tri.a.u + w1 * tri.b.u + w2 * tri.c.u
                        v = w0 * tri.a.v + w1 * tri.b.v + w2 * tri.c.v
                        fragment_color = shader(u, v)
                    else:
                        fragment_color = tri.color
                    stats["shader"] += 1
                    color[index] = fragment_color
                    depth[index] = z
                    wrote = True
            if wrote:
                if bound_valid:
                    bound_dirty = True
                else:
                    # Single scan doubles as the full-cover check and the
                    # initial bound computation; returns None while any cell
                    # is still untouched.
                    scanned = self._scan_tile_dmax(x0, y0, x1, y1)
                    if scanned is not None:
                        bound_valid = True
                        dmax = scanned
                        bound_dirty = False

        # ------------------------------------------------------- transparent
        # No coarse cull, no early-z, no depth write: back-to-front blending
        # must see every transparent fragment regardless of what is in front.
        for tri in transparent:
            bx0, bx1, by0, by1 = self._bbox(tri)
            if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
                continue
            cx0 = bx0 if bx0 > x0 else x0
            cx1 = bx1 if bx1 < x1 else x1
            cy0 = by0 if by0 > y0 else y0
            cy1 = by1 if by1 < y1 else y1
            area = _edge(tri.a.x, tri.a.y, tri.b.x, tri.b.y, tri.c.x, tri.c.y)
            if area == 0:
                continue
            alpha = tri.alpha
            inv_alpha = 255 - alpha
            for y in range(cy0, cy1 + 1):
                row = y * width
                for x in range(cx0, cx1 + 1):
                    px, py = x + 0.5, y + 0.5
                    w0 = _edge(tri.b.x, tri.b.y, tri.c.x, tri.c.y, px, py)
                    w1 = _edge(tri.c.x, tri.c.y, tri.a.x, tri.a.y, px, py)
                    w2 = _edge(tri.a.x, tri.a.y, tri.b.x, tri.b.y, px, py)
                    if not ((w0 >= 0 and w1 >= 0 and w2 >= 0) or (w0 <= 0 and w1 <= 0 and w2 <= 0)):
                        continue
                    stats["fragments"] += 1
                    if texture is not None:
                        u = w0 * tri.a.u + w1 * tri.b.u + w2 * tri.c.u
                        v = w0 * tri.a.v + w1 * tri.b.v + w2 * tri.c.v
                        if cache is not None:
                            source = cache.sample(u, v)
                        else:
                            source = texture.sample(u, v)
                    elif shader is not None:
                        u = w0 * tri.a.u + w1 * tri.b.u + w2 * tri.c.u
                        v = w0 * tri.a.v + w1 * tri.b.v + w2 * tri.c.v
                        source = shader(u, v)
                    else:
                        source = tri.color
                    stats["shader"] += 1
                    index = row + x
                    destination = color[index]
                    color[index] = (
                        (source[0] * alpha + destination[0] * inv_alpha) // 255,
                        (source[1] * alpha + destination[1] * inv_alpha) // 255,
                        (source[2] * alpha + destination[2] * inv_alpha) // 255,
                    )

        if cache is not None:
            stats["cache_hits"] += cache.hits
            stats["cache_misses"] += cache.misses

    def _scan_tile_dmax(self, x0, y0, x1, y1):
        """Maximum stored depth in the tile, or ``None`` if a cell is untouched.

        For the LESS depth test a triangle can be rejected wholesale only when
        its shallowest possible fragment (z_min from the clip test) is still
        behind the deepest fragment stored in the tile. An untouched cell has
        no occluding fragment, so until the tile is completely filled the
        bound is unusable and this helper returns ``None``.
        """
        depth = self.depth
        width = self.width
        bound = float("-inf")
        for y in range(y0, y1 + 1):
            row = y * width
            for x in range(x0, x1 + 1):
                value = depth[row + x]
                if value == float("inf"):
                    return None
                if value > bound:
                    bound = value
        return bound
