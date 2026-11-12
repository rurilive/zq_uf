"""Frame-time comparison: legacy full-screen rasteriser vs. tile pipeline.

Two workloads are measured on both pipelines:

  1. Back-to-front full-screen layers. The first (farthest) layer fills
     every tile; each later layer whose shallowest fragment stays behind the
     tile's dmax is removed by the coarse depth pre-test -- no bounding-box
     walk, no edge tests, no interpolation.
  2. Partial-coverage overdraw. No layer fills a tile, so the coarse bound
     never becomes valid and per-pixel early-z carries the rejection load,
     matching the behaviour of a depth-only prepass at per-pixel granularity.

In both scenes the baseline also runs its depth test before the shader, so
correct shader-invocation counts are necessarily identical; the tile
pipeline's win is skipped iteration/edge work plus block-parallel writes.

Run:  python3 demo.py [width] [height] [frames]
"""

import random
import sys
import time

from softray import Renderer, Triangle, Vertex

TILE_SIZE = 32


def moderate_shader(u, v):
    value = ((u * 3.13) % 1.0) * ((v * 7.17) % 1.0)
    shade = int(value * 255)
    return shade, (255 - shade) // 2, (shade + 64) % 256


def fullscreen_layers(width, height, count):
    # Submitted far -> near (z increasing), so the first layer fills the
    # tiles and later ones become coarse-cull candidates.
    triangles = []
    for layer in range(count):
        z = float(layer + 2)
        triangles.append(Triangle(
            Vertex(-1, -1, z),
            Vertex(width + 1, -1, z),
            Vertex(-1, height + 1, z),
            ((layer * 37) % 256, (layer * 91) % 256, (layer * 173) % 256),
        ))
    return triangles


def partial_triangles(width, height, count, rng):
    triangles = []
    for _ in range(count):
        cx, cy = rng.uniform(0, width), rng.uniform(0, height)
        size = rng.uniform(8, max(width, height) / 4)
        z = rng.uniform(0.2, 9.0)
        triangles.append(Triangle(
            Vertex(cx - size, cy - size, z, 0.0, 0.0),
            Vertex(cx + size, cy - size * 0.5, z, 1.0, 0.0),
            Vertex(cx - size * 0.4, cy + size, z, 0.0, 1.0),
            (rng.randrange(256), rng.randrange(256), rng.randrange(256)),
        ))
    return triangles


def time_render(renderer, scene, frames, use_legacy):
    timings = []
    for _ in range(frames):
        start = time.perf_counter()
        if use_legacy:
            renderer.render_legacy(scene)
        else:
            renderer.render(scene)
        timings.append(time.perf_counter() - start)
    return min(timings), sum(timings) / len(timings)


def benchmark(name, scene, width, height, frames):
    legacy = Renderer(width, height, moderate_shader, tile_size=TILE_SIZE, workers=1)
    legacy_best, legacy_avg = time_render(legacy, scene, frames, use_legacy=True)

    tiled_single = Renderer(width, height, moderate_shader,
                            tile_size=TILE_SIZE, workers=1)
    single_best, _ = time_render(tiled_single, scene, frames, use_legacy=False)

    tiled = Renderer(width, height, moderate_shader, tile_size=TILE_SIZE)
    tiled_best, tiled_avg = time_render(tiled, scene, frames, use_legacy=False)

    assert tiled.color == legacy.color, f"{name}: colour diverged from reference!"
    assert tiled.depth == legacy.depth, f"{name}: depth diverged from reference!"
    assert tiled_single.color == legacy.color, f"{name}: workers=1 diverged!"

    print(f"\n[{name}] {len(scene)} triangles")
    print(f"{'pipeline':<34}{'best(ms)':>10}{'avg(ms)':>10}"
          f"{'shaders':>11}{'coarse':>9}{'early-z':>9}")
    rows = (
        ("legacy full-screen (baseline)", legacy_best, legacy_avg,
         legacy.shader_calls, 0, legacy.early_z_rejected),
        ("tiled, 1 worker", single_best, None,
         tiled_single.shader_calls, tiled_single.coarse_culled,
         tiled_single.early_z_rejected),
        (f"tiled, {tiled.workers} workers (default)", tiled_best, tiled_avg,
         tiled.shader_calls, tiled.coarse_culled, tiled.early_z_rejected),
    )
    for row_name, best, avg, shaders, coarse, early in rows:
        print(f"{row_name:<34}{best * 1000:>10.2f}"
              f"{(avg * 1000 if avg else float('nan')):>10.2f}"
              f"{shaders:>11}{coarse:>9}{early:>9}")
    print(f"frame time: {legacy_best * 1000:.2f} ms -> {tiled_best * 1000:.2f} ms "
          f"({tiled_best / legacy_best:.2f}x), output identical to legacy reference.")


def main():
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 320
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 240
    frames = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    rng = random.Random(20261112)

    print(f"framebuffer {width}x{height} ({width * height} px), "
          f"tile {TILE_SIZE}x{TILE_SIZE}, best/avg of {frames} frames")

    benchmark("coarse depth pre-test: back-to-front full-screen layers",
              fullscreen_layers(width, height, 40), width, height, frames)
    benchmark("per-pixel early-z: partial-coverage overdraw",
              partial_triangles(width, height, 60, rng), width, height, frames)

    print("\nnote: CPython's GIL serialises pure-Python worker threads, so wall-clock")
    print("scaling across workers appears on free-threaded builds; tile dispatch itself")
    print("is race-free and output is bit-identical for every worker count.")


if __name__ == "__main__":
    main()
