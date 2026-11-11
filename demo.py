"""Compare the legacy per-pixel path with the tiled/early-z pipeline."""
import random
import time

from softray import Renderer, Triangle, Vertex

WIDTH, HEIGHT = 128, 96
TRIANGLES = 300
FRAMES = 3


def texture_shader(u, v):
    # Simulates an expensive textured fragment shader.
    r = int(u * 255) % 256
    g = int(v * 255) % 256
    b = int((u * u + v * v) * 511) % 256
    for _ in range(20):
        r = (r * 1103515245 + 12345) % 256
    return (r, g, b)


def build_scene(seed=0):
    rng = random.Random(seed)
    tris = []
    for _ in range(TRIANGLES):
        z = rng.uniform(0.5, 10.0)
        verts = []
        for _ in range(3):
            verts.append(Vertex(rng.uniform(-20, WIDTH + 20),
                                rng.uniform(-20, HEIGHT + 20), z,
                                rng.random(), rng.random()))
        tris.append(Triangle(*verts))
    # Front-to-back order gives the tiled pipeline its worst-case occlusion.
    tris.sort(key=lambda t: t.a.z + t.b.z + t.c.z)
    return tris


def timed(renderer, tris, mode):
    start = time.perf_counter()
    for _ in range(FRAMES):
        if mode == "tiled":
            renderer.render(tris)
        else:
            renderer.render_baseline(tris)
    return (time.perf_counter() - start) / FRAMES * 1000.0


def main():
    tris = build_scene()
    renderer = Renderer(WIDTH, HEIGHT, texture_shader, tile_size=32)

    base_ms = timed(renderer, tris, "baseline")
    base_color, base_depth = list(renderer.color), list(renderer.depth)

    tiled_ms = timed(renderer, tris, "tiled")
    assert renderer.color == base_color and renderer.depth == base_depth, \
        "tiled output differs from baseline"

    stats = renderer.stats
    print(f"baseline (per-pixel):  {base_ms:8.2f} ms/frame")
    print(f"tiled + early-z:       {tiled_ms:8.2f} ms/frame")
    print(f"speedup:               {base_ms / tiled_ms:8.2f}x")
    print(f"tile size:             {renderer.tile_size}px, "
          f"threads: {renderer.num_threads}")
    print(f"tile-culled tris:      {stats['tile_culled']}")
    print(f"early-z rejected px:   {stats['earlyz_rejected']}")
    print(f"shader calls:          {stats['shader_calls']}")
    print(f"texture cache hits:    {stats['cache_hits']}")
    print("outputs identical:     OK")


if __name__ == "__main__":
    main()

