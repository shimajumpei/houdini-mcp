---
name: houdini-info
description: "Inspect the current Houdini scene with houdini-mcp read-only tools (scene info, error nodes, crowd setup) and report it in a fixed format."
---

# Houdini Scene Inspector

Houdiniの現在のシーン情報を取得し、分かりやすく報告するスキルです。
houdini-mcp MCPサーバーのツールを使用します。

## 実行内容

以下のhoudini-mcpツールを並列で呼び出し、結果をまとめて報告してください：

1. `get_scene_info` - シーン全体の情報（ファイル名、バージョン、ノード一覧）
2. `find_error_nodes` - エラーノードの検出
3. `get_crowd_info` - クラウドシミュレーション関連のノード情報（存在する場合）

## 報告フォーマット

```
## Houdini シーン情報

**ファイル**: {hip_file}
**バージョン**: {houdini_version}

### ノード構成
- /obj 配下: {ノードリスト}
- DOPネットワーク: {あれば}

### エラー
- {エラーノード一覧、なければ「エラーなし」}

### クラウド設定
- {クラウドノードの概要、なければ「クラウドセットアップなし」}
```

追加の要求があれば対応: $ARGUMENTS
