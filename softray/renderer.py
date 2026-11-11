"""A tiny software rasterizer used by the renderer baseline."""
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


def _edge(ax, ay, bx, by, px, py):
    return (px - ax) * (by - ay) - (py - ay) * (bx - ax)


class Renderer:
    def __init__(self, width: int, height: int, shader: Optional[Callable] = None):
        self.width = width
        self.height = height
        self.shader = shader
        self.color = [(0, 0, 0)] * (width * height)
        self.depth = [float("inf")] * (width * height)

    def clear(self):
        self.color = [(0, 0, 0)] * (self.width * self.height)
        self.depth = [float("inf")] * (self.width * self.height)

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

    def render(self, triangles: List[Triangle]):
        self.clear()
        for tri in triangles:
            self.draw_triangle(tri)
