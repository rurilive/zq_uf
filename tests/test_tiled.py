import random
import unittest

from softray import Renderer, Triangle, Vertex


def random_scene(count, width, height, seed=0, shader_uv=False):
    rng = random.Random(seed)
    tris = []
    for _ in range(count):
        z = rng.uniform(0.5, 10.0)
        verts = []
        for _ in range(3):
            u, v = (rng.random(), rng.random()) if shader_uv else (0.0, 0.0)
            verts.append(Vertex(rng.uniform(-10, width + 10),
                                rng.uniform(-10, height + 10), z, u, v))
        color = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        tris.append(Triangle(*verts, color))
    return tris


def big_triangle(z, color=(255, 255, 255), alpha=1.0, span=40):
    # Covers any screen up to roughly (span/2) x (span/2) entirely.
    return Triangle(Vertex(-1, -1, z), Vertex(span, -1, z),
                    Vertex(-1, span, z), color, alpha)


class TiledRendererTest(unittest.TestCase):
    def test_cross_tile_triangle_matches_baseline(self):
        # One huge triangle spanning every tile plus smaller ones on top.
        tris = [big_triangle(5.0, (10, 20, 30), span=140)]
        tris += random_scene(20, 64, 64, seed=1)
        tiled = Renderer(64, 64, tile_size=16, num_threads=1)
        tiled.render(tris)
        ref = Renderer(64, 64)
        ref.render_baseline(tris)
        self.assertEqual(tiled.color, ref.color)
        self.assertEqual(tiled.depth, ref.depth)
        # Corners belong to different tiles; all must be covered.
        for idx in (0, 63, 63 * 64, 64 * 64 - 1):
            self.assertNotEqual(tiled.color[idx], (0, 0, 0))

    def test_boundary_tiles_match_baseline(self):
        # 70x38 with tile 32 leaves 6x6 and 32x6 / 6x32 edge tiles.
        tris = random_scene(40, 70, 38, seed=2)
        for tile_size in (32, 7, 100):
            tiled = Renderer(70, 38, tile_size=tile_size, num_threads=1)
            tiled.render(tris)
            ref = Renderer(70, 38)
            ref.render_baseline(tris)
            self.assertEqual(tiled.color, ref.color)
            self.assertEqual(tiled.depth, ref.depth)

    def test_parallel_consistency(self):
        tris = random_scene(60, 64, 48, seed=3, shader_uv=True)
        shader = lambda u, v: (int(u * 255) % 256, int(v * 255) % 256, 0)
        single = Renderer(64, 48, shader, tile_size=16, num_threads=1)
        single.render(tris)
        for threads in (2, 4, 8):
            par = Renderer(64, 48, shader, tile_size=16, num_threads=threads)
            par.render(tris)
            self.assertEqual(single.color, par.color)
            self.assertEqual(single.depth, par.depth)

    def test_cache_does_not_change_colors(self):
        tris = random_scene(30, 32, 32, seed=4, shader_uv=True)
        shader = lambda u, v: (int(u * 255) % 256, int(v * 255) % 256, 128)
        cached = Renderer(32, 32, shader, tile_size=8, num_threads=1,
                          cache_size=256)
        cached.render(tris)
        plain = Renderer(32, 32, shader, tile_size=8, num_threads=1,
                         cache_size=0)
        plain.render(tris)
        self.assertEqual(cached.color, plain.color)
        self.assertEqual(cached.depth, plain.depth)

    def test_earlyz_and_coarse_culling(self):
        calls = []
        def shader(u, v):
            calls.append((u, v))
            return (1, 2, 3)
        renderer = Renderer(16, 16, shader, tile_size=8, num_threads=1,
                            cache_size=0)
        near = big_triangle(1.0)
        far = [big_triangle(5.0), big_triangle(6.0), big_triangle(7.0)]
        renderer.render([near] + far)
        # Every screen pixel passes depth exactly once (the near triangle).
        self.assertEqual(renderer.stats["shader_calls"], 16 * 16)
        self.assertEqual(len(calls), 16 * 16)
        # The three far triangles are fully occluded in all 4 tiles.
        self.assertEqual(renderer.stats["tile_culled"], 3 * 4)
        self.assertEqual(renderer.stats["earlyz_rejected"], 0)

    def test_earlyz_rejects_occluded_pixels(self):
        renderer = Renderer(16, 16, tile_size=8, num_threads=1)
        near = big_triangle(1.0, (255, 0, 0))
        # Slightly nearer partial triangle forces per-pixel early-z rejects
        # for the bigger one drawn afterwards.
        nearer = Triangle(Vertex(-1, -1, 0.5), Vertex(9, -1, 0.5),
                          Vertex(-1, 9, 0.5), (0, 255, 0))
        renderer.render([nearer, near])
        self.assertGreater(renderer.stats["earlyz_rejected"], 0)
        self.assertEqual(renderer.color[0], (0, 255, 0))
        self.assertEqual(renderer.color[15 * 16 + 15], (255, 0, 0))

    def test_transparent_back_to_front(self):
        blue = big_triangle(5.0, (0, 0, 255))
        green = big_triangle(3.0, (0, 255, 0), alpha=0.5)
        red = big_triangle(2.0, (255, 0, 0), alpha=0.5)
        expected = (128, 64, 64)  # red over green over blue, alpha 0.5
        for order in ([blue, green, red], [blue, red, green],
                      [red, blue, green]):
            renderer = Renderer(16, 16, tile_size=4, num_threads=1)
            renderer.render(order)
            self.assertEqual(renderer.color[0], expected)
            self.assertEqual(renderer.color[15 * 16 + 15], expected)
            # Transparent fragments must not write depth.
            self.assertEqual(renderer.depth[0], 5.0)

    def test_transparent_occluded_by_opaque(self):
        blue = big_triangle(5.0, (0, 0, 255))
        black = big_triangle(1.5, (0, 0, 0))
        red = big_triangle(2.0, (255, 0, 0), alpha=0.5)
        renderer = Renderer(16, 16, tile_size=4, num_threads=1)
        renderer.render([blue, black, red])
        # The transparent triangle is behind the opaque black one and must
        # be culled everywhere, not blended.
        self.assertEqual(renderer.color[0], (0, 0, 0))
        self.assertEqual(renderer.color[15 * 16 + 15], (0, 0, 0))

    def test_transparent_parallel_consistency(self):
        blue = big_triangle(5.0, (0, 0, 255))
        tris = [blue]
        rng = random.Random(5)
        for _ in range(10):
            z = rng.uniform(1.0, 4.0)
            color = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            tris.append(big_triangle(z, color, alpha=rng.uniform(0.2, 0.8)))
        single = Renderer(16, 16, tile_size=4, num_threads=1)
        single.render(tris)
        par = Renderer(16, 16, tile_size=4, num_threads=4)
        par.render(tris)
        self.assertEqual(single.color, par.color)
        self.assertEqual(single.depth, par.depth)


if __name__ == "__main__":
    unittest.main()

