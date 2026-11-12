import random
import unittest

from softray import Renderer, Texture, Triangle, Vertex


def fullscreen_tri(z, color, alpha=255, u0=0.0, v0=0.0):
    # Generous geometry so the hypotenuse (x + y = 9999) clears every test
    # framebuffer; the original baseline helper only fully covered 4x4.
    return Triangle(
        Vertex(-1, -1, z, u0, v0),
        Vertex(10000, -1, z, 1.0, v0),
        Vertex(-1, 10000, z, u0, 1.0),
        color,
        alpha,
    )


class CrossTileTest(unittest.TestCase):
    """Triangles spanning several tiles must be clipped and rasterised per tile."""

    def _random_scene(self, seed, width, height, count):
        rng = random.Random(seed)
        triangles = []
        for _ in range(count):
            vertices = []
            for _ in range(3):
                vertices.append(Vertex(
                    rng.uniform(-width * 0.5, width * 1.5),
                    rng.uniform(-height * 0.5, height * 1.5),
                    rng.uniform(0.1, 10.0),
                    rng.random(),
                    rng.random(),
                ))
            triangles.append(Triangle(
                *vertices,
                (rng.randrange(256), rng.randrange(256), rng.randrange(256)),
            ))
        return triangles

    def test_tiled_matches_legacy_pixels_and_depth(self):
        for tile_size in (1, 7, 32):
            for workers in (1, 3):
                width, height = 48, 40
                scene = self._random_scene(20261112, width, height, 40)
                expected = Renderer(width, height, tile_size=tile_size, workers=1)
                expected.render_legacy(scene)
                tiled = Renderer(width, height, tile_size=tile_size, workers=workers)
                tiled.render(scene)
                self.assertEqual(tiled.color, expected.color,
                                 f"colour mismatch tile={tile_size} workers={workers}")
                self.assertEqual(tiled.depth, expected.depth,
                                 f"depth mismatch tile={tile_size} workers={workers}")

    def test_no_seam_across_tile_boundary(self):
        # A triangle whose interior crosses the 32px tile boundary: every
        # covered pixel must be painted, including the boundary columns.
        renderer = Renderer(64, 8, tile_size=32, workers=1)
        tri = Triangle(Vertex(0, 0, 1), Vertex(63, 0, 1), Vertex(0, 7, 1), (9, 9, 9))
        renderer.render([tri])
        for y in range(8):
            for x in range(64):
                # Hypotenuse runs from (63, 0) to (0, 7): x + 9y = 63; the
                # pixel-centre convention tightens it to x + 9y <= 58.
                if x + 9 * y <= 58:
                    self.assertEqual(renderer.color[y * 64 + x], (9, 9, 9),
                                     f"gap at {x},{y} on tile boundary")
                else:
                    self.assertEqual(renderer.color[y * 64 + x], (0, 0, 0))


class EdgeTileTest(unittest.TestCase):
    """Edge tiles must be clamped to the framebuffer and never index OOB."""

    def test_non_divisible_dimensions(self):
        # 33x17 with 32px tiles -> right edge tile is 1px wide, bottom row 17px high.
        width, height = 33, 17
        scene = [
            fullscreen_tri(1, (1, 2, 3)),
            Triangle(Vertex(30, 15, 0.5), Vertex(40, 15, 0.5), Vertex(30, 25, 0.5), (4, 5, 6)),
            Triangle(Vertex(-5, -5, 2), Vertex(33, -5, 2), Vertex(-5, 17, 2), (7, 8, 9)),
        ]
        tiled = Renderer(width, height, tile_size=32, workers=2)
        tiled.render(scene)
        legacy = Renderer(width, height)
        legacy.render_legacy(scene)
        self.assertEqual(tiled.color, legacy.color)
        self.assertEqual(tiled.depth, legacy.depth)
        self.assertEqual(len(tiled.color), width * height)
        self.assertEqual(len(tiled.depth), width * height)

    def test_tiny_framebuffer_and_tile_size_one(self):
        renderer = Renderer(1, 1, tile_size=32)
        renderer.render([fullscreen_tri(1, (10, 20, 30))])
        self.assertEqual(renderer.color[0], (10, 20, 30))
        renderer = Renderer(3, 3, tile_size=1, workers=4)
        renderer.render([fullscreen_tri(1, (10, 20, 30))])
        self.assertTrue(all(pixel == (10, 20, 30) for pixel in renderer.color))

    def test_invalid_construction(self):
        with self.assertRaises(ValueError):
            Renderer(4, 4, tile_size=0)
        with self.assertRaises(ValueError):
            Renderer(4, 4, workers=0)
        with self.assertRaises(ValueError):
            Renderer(0, 4)


class TransparencyTest(unittest.TestCase):
    """Transparent triangles bypass early-z, keep depth intact and blend back-to-front."""

    def test_transparent_in_front_stays_visible(self):
        renderer = Renderer(2, 2, tile_size=32)
        opaque = fullscreen_tri(10, (255, 0, 0))
        see_through = fullscreen_tri(5, (0, 0, 255), alpha=128)
        renderer.render([opaque, see_through])
        # (0*128 + 255*127)//255 = 127, (255*128 + 0)//255 = 128
        self.assertEqual(renderer.color[0], (127, 0, 128))
        # Transparency never writes depth: the opaque depth remains.
        self.assertAlmostEqual(renderer.depth[0], 10.0)

    def test_transparent_without_opaque_leaves_depth_untouched(self):
        renderer = Renderer(2, 2)
        renderer.render([fullscreen_tri(3, (10, 10, 10), alpha=64)])
        self.assertTrue(all(value == float("inf") for value in renderer.depth))
        # Blended over the black clear colour.
        self.assertEqual(renderer.color[0], (10 * 64 // 255,) * 3)

    def test_blend_order_is_back_to_front_regardless_of_submission(self):
        far = fullscreen_tri(10, (0, 255, 0), alpha=128)
        near = fullscreen_tri(5, (100, 0, 100), alpha=128)

        def render(order):
            renderer = Renderer(4, 4, tile_size=2, workers=2)
            renderer.render(list(order))
            return renderer.color[0]

        blended = render([far, near])
        # Submission order must not matter: sorting is by depth, back-to-front.
        self.assertEqual(blended, render([near, far]))
        # And it must differ from the wrong (front-to-back) ordering, proving
        # the sort actually drives blending order.
        near_over_black = (100 * 128 // 255, 0, 100 * 128 // 255)  # (50, 0, 50)
        wrong_order = (
            (0 * 128 + near_over_black[0] * 127) // 255,
            (255 * 128 + near_over_black[1] * 127) // 255,
            (0 * 128 + near_over_black[2] * 127) // 255,
        )
        far_over_black = (0, 255 * 128 // 255, 0)  # (0, 128, 0)
        correct_order = (
            (100 * 128 + far_over_black[0] * 127) // 255,
            (0 * 128 + far_over_black[1] * 127) // 255,
            (100 * 128 + far_over_black[2] * 127) // 255,
        )
        self.assertEqual(blended, correct_order)
        self.assertNotEqual(blended, wrong_order)

    def test_opaque_triangle_behind_transparency_is_hidden(self):
        renderer = Renderer(2, 2)
        see_through = fullscreen_tri(5, (0, 0, 255), alpha=128)
        hidden = fullscreen_tri(10, (255, 0, 0))
        renderer.render([see_through, hidden])
        # Opaque still wins the depth test; transparency blends over it.
        self.assertEqual(renderer.color[0], (127, 0, 128))


class EarlyZTest(unittest.TestCase):
    """Early-z runs before the shader; coarse rejection runs before rasterisation."""

    @staticmethod
    def counting_shader():
        calls = {"n": 0}

        def shader(u, v):
            calls["n"] += 1
            return (1, 2, 3)

        return shader, calls

    def test_shader_skipped_for_hidden_fragments(self):
        width, height = 8, 8
        shader, calls = self.counting_shader()
        # A single tile (tile_size > framebuffer) that the near geometry only
        # partly covers: dmax can never become finite, so coarse culling stays
        # idle and per-pixel early-z must perform every rejection.
        renderer = Renderer(width, height, shader, tile_size=32, workers=1)
        near_rect = [
            Triangle(Vertex(0, 0, 1), Vertex(4, 0, 1), Vertex(0, 8, 1), (0, 0, 0)),
            Triangle(Vertex(4, 0, 1), Vertex(4, 8, 1), Vertex(0, 8, 1), (0, 0, 0)),
        ]
        far = fullscreen_tri(9, (0, 0, 0))
        renderer.render(near_rect + [far])
        left_half = width * height // 2  # 32 pixels covered by the near rectangle
        self.assertEqual(renderer.coarse_culled, 0)
        self.assertEqual(renderer.early_z_rejected, left_half)
        self.assertEqual(renderer.fragments_covered, left_half + width * height)
        # Every visible pixel is shaded exactly once; hidden ones never are.
        self.assertEqual(renderer.shader_calls, width * height)
        self.assertEqual(calls["n"], width * height)

    def test_coarse_depth_rejects_whole_triangle(self):
        shader, _ = self.counting_shader()
        renderer = Renderer(8, 8, shader, tile_size=4)
        renderer.render([fullscreen_tri(1, (1, 1, 1)), fullscreen_tri(9, (2, 2, 2))])
        # Every tile is fully covered by the near triangle: far triangle's
        # minimum z (9) is behind each tile's dmax (1), so it is rejected
        # outright and never shades a single fragment.
        self.assertGreater(renderer.coarse_culled, 0)
        self.assertEqual(renderer.shader_calls, 64)
        self.assertEqual(renderer.color[0], (1, 2, 3))

    def test_transparent_fragments_never_early_z_rejected(self):
        shader, calls = self.counting_shader()
        renderer = Renderer(4, 4, shader, tile_size=2)
        renderer.render([
            fullscreen_tri(1, (0, 0, 0)),
            fullscreen_tri(5, (9, 9, 9), alpha=200),
        ])
        self.assertEqual(renderer.early_z_rejected, 0)
        self.assertEqual(calls["n"], 32)  # 16 opaque + 16 transparent, none skipped


class ParallelConsistencyTest(unittest.TestCase):
    """Any worker count must produce bit-identical buffers and counters."""

    def test_worker_counts_agree(self):
        rng = random.Random(42)
        width, height = 50, 33
        scene = []
        for index in range(30):
            scene.append(Triangle(
                Vertex(rng.uniform(-10, width + 10), rng.uniform(-10, height + 10),
                       rng.uniform(0.1, 9)),
                Vertex(rng.uniform(-10, width + 10), rng.uniform(-10, height + 10),
                       rng.uniform(0.1, 9)),
                Vertex(rng.uniform(-10, width + 10), rng.uniform(-10, height + 10),
                       rng.uniform(0.1, 9)),
                (rng.randrange(256), rng.randrange(256), rng.randrange(256)),
            ))
        reference = Renderer(width, height, tile_size=8, workers=1)
        reference.render(scene)
        for workers in (2, 4, 8):
            other = Renderer(width, height, tile_size=8, workers=workers)
            other.render(scene)
            self.assertEqual(other.color, reference.color, f"colour differs at workers={workers}")
            self.assertEqual(other.depth, reference.depth, f"depth differs at workers={workers}")
            for field in ("shader_calls", "coarse_culled", "early_z_rejected",
                          "fragments_covered", "cache_hits", "cache_misses"):
                self.assertEqual(getattr(other, field), getattr(reference, field),
                                 f"{field} differs at workers={workers}")

    def test_transparency_parallel_agrees(self):
        scene = [
            fullscreen_tri(8, (200, 10, 10), alpha=128),
            fullscreen_tri(4, (10, 10, 200), alpha=200),
            fullscreen_tri(2, (10, 200, 10), alpha=64),
        ]
        single = Renderer(16, 16, tile_size=5, workers=1)
        single.render(scene)
        parallel = Renderer(16, 16, tile_size=5, workers=4)
        parallel.render(scene)
        self.assertEqual(parallel.color, single.color)
        self.assertEqual(parallel.depth, single.depth)


class TextureCacheTest(unittest.TestCase):
    """The per-tile texture cache changes timing/counters only, never colours."""

    @staticmethod
    def make_texture():
        rows = [
            [(r * 16, g * 16, (r + g) * 8 % 256) for g in range(4)]
            for r in range(4)
        ]
        return Texture.from_rows(rows)

    def test_cached_and_uncached_identical(self):
        texture = self.make_texture()
        scene = [
            Triangle(Vertex(-1, -1, 1, 0, 0), Vertex(20, -1, 1, 2, 0),
                     Vertex(-1, 20, 1, 0, 2), (0, 0, 0)),
            Triangle(Vertex(2, 2, 0.5, 0.2, 0.2), Vertex(14, 3, 0.5, 0.9, 0.1),
                     Vertex(5, 13, 0.5, 0.1, 0.9), (0, 0, 0)),
        ]
        cached = Renderer(16, 16, tile_size=8, workers=2, texture=texture, use_cache=True)
        cached.render(scene)
        uncached = Renderer(16, 16, tile_size=8, workers=2, texture=texture, use_cache=False)
        uncached.render(scene)
        self.assertEqual(cached.color, uncached.color)
        self.assertGreater(cached.cache_hits, 0)
        # Every textured fragment went through the cache; uncached runs keep
        # cache counters at zero.
        self.assertEqual(cached.cache_hits + cached.cache_misses, cached.shader_calls)
        self.assertEqual(uncached.cache_hits, 0)
        self.assertEqual(uncached.cache_misses, 0)
        self.assertEqual(cached.shader_calls, uncached.shader_calls)

        # A tiny cache must still return identical colours (LRU eviction).
        tiny = Renderer(16, 16, tile_size=8, texture=texture, cache_size=2)
        tiny.render(scene)
        self.assertEqual(tiny.color, uncached.color)

    def test_texture_matches_equivalent_shader(self):
        texture = self.make_texture()
        scene = [Triangle(Vertex(-1, -1, 1, 0, 0), Vertex(9, -1, 1, 1, 0),
                          Vertex(-1, 9, 1, 0, 1), (0, 0, 0))]
        textured = Renderer(8, 8, tile_size=4, texture=texture)
        textured.render(scene)
        shaded = Renderer(8, 8, tile_size=4, shader=lambda u, v: texture.sample(u, v))
        shaded.render(scene)
        self.assertEqual(textured.color, shaded.color)

    def test_bad_texture_dimensions_rejected(self):
        with self.assertRaises(ValueError):
            Texture([(0, 0, 0), (1, 1, 1)], 4, 4)


if __name__ == "__main__":
    unittest.main()
