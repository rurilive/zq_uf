import unittest

from softray import Renderer, Triangle, Vertex


class RendererTest(unittest.TestCase):
    def test_clear(self):
        renderer = Renderer(4, 4)
        self.assertEqual(renderer.color[0], (0, 0, 0))

    def test_fullscreen_triangle(self):
        renderer = Renderer(4, 4)
        tri = Triangle(Vertex(-1, -1, 1), Vertex(10, -1, 1), Vertex(-1, 10, 1), (255, 0, 0))
        renderer.render([tri])
        self.assertEqual(renderer.color[0], (255, 0, 0))
        self.assertEqual(renderer.color[-1], (255, 0, 0))

    def test_depth(self):
        renderer = Renderer(4, 4)
        far = Triangle(Vertex(-1, -1, 5), Vertex(10, -1, 5), Vertex(-1, 10, 5), (255, 0, 0))
        near = Triangle(Vertex(-1, -1, 1), Vertex(10, -1, 1), Vertex(-1, 10, 1), (0, 255, 0))
        renderer.render([far, near])
        self.assertEqual(renderer.color[0], (0, 255, 0))

    def test_shader(self):
        renderer = Renderer(2, 2, lambda u, v: (int(u * 255), int(v * 255), 0))
        tri = Triangle(Vertex(-1, -1, 1, 0, 0), Vertex(3, -1, 1, 1, 0), Vertex(-1, 3, 1, 0, 1))
        renderer.render([tri])
        self.assertEqual(renderer.color[0][2], 0)
