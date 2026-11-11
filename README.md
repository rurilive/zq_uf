# softray

现状：全屏逐像素执行片段着色器，支持深度测试和简单三角形。

问题：模型一多帧时间顶不住，透明物体和重叠三角形顺序依赖重。

目标：按屏幕分块，粗粒度深度预判、early-z、采样缓存和并行写缓冲。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
