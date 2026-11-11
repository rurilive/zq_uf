from softray import Renderer, Triangle, Vertex

renderer = Renderer(8, 8)
renderer.render([Triangle(Vertex(-1, -1, 1), Vertex(9, -1, 1), Vertex(-1, 9, 1), (0, 128, 255))])
print(renderer.color[0], renderer.color[-1])
