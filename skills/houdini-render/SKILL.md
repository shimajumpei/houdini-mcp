---
name: houdini-render
description: "Render the Houdini viewport with houdini-mcp and show the image. Also covers the quad view, single-pane screenshots, and node-network renders."
---

# Houdini Viewport Render

Houdiniのビューポートをレンダリングして結果画像を表示するスキルです。

## 実行内容

1. `render_viewport` を呼び出してビューポートのスクリーンショットを取得
   - resolution: ユーザー指定があればそれを使用、なければ [1280, 720]
2. 結果の `image_base64` を表示

オプション：
- `render_quad_view` で4ビュー同時レンダリング
- `capture_pane_screenshot` でネットワークエディタ等の特定ペインをキャプチャ
- `render_node_network` でノードグラフをレンダリング

ユーザーの指示: $ARGUMENTS
