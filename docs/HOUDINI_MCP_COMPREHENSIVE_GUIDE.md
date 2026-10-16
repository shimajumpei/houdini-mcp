# Houdini MCP エコシステム — 包括的リファレンス

**作成日**: 2026-03-01
**対象バージョン**: houdini-mcp 1.0.0 / Houdini 20.5.759 / FastMCP 2.14.2
**ツール数検証**: `server.py` の `@mcp.tool()` を source-of-truth として計上

---

## ツール数サマリー（source-of-truth: server.py の @mcp.tool 登録）

| サーバー | ツール数 | 内訳 |
|---------|--------|------|
| **houdini-mcp** | **69** | 読み取り専用 26 + 変更系 34（Hook対象）+ ワークフロー 9 |
| **houdini-docs-mcp** | **1** | `query_houdini_docs` |
| **api-docs** | **6** | VEX 2 + CLO3D 2 + RhinoCommon 2 |
| **合計** | **76** | 3サーバー横断 |

---

## クイックスタート（最短の成功ルート）

### 前提: Houdini 側の準備

```python
# Houdini の Python Shell で実行（毎回必要）
import hrpyc
hrpyc.start_server(port=18811)
```

### 最短フロー

```
1. [Houdini 起動 + hrpyc.start_server()]
2. [Claude Code 起動]
3. Q1/Q2 質問（RULE 1）
     Q1: 新規 or 既存？ → Q2: 補助 or 任せる？
4. ensure_project()          → .agent/ 初期化
5. read_project("all")       → spec/stages 確認
6. [ready=false なら spec.md / stages.yaml を記入]
7. plan_stage("S01", "...")   → stage 開始
8. propose_diff("S01", {...}) → diff_id 取得
9. [差分案をユーザーに提示]
10. approve_diff(diff_id)     → 変更解禁
11. [safe_set_parameter / create_node / connect_nodes 等で実装]
12. get_scene_info()          → scan_data
13. record_scan(scan_data)    → スキャン記録
14. validate_stage("S01")     → 検証
15. update_stage("S01", "validated") → update_stage("S01", "completed")
```

**ステップ 8〜15 は stage ごとに繰り返す。**

---

## 目次

0. [クイックスタート](#クイックスタート最短の成功ルート)
1. [全体アーキテクチャ](#1-全体アーキテクチャ)
2. [接続レイヤー](#2-接続レイヤー)
3. [MCP ツール完全一覧（houdini-mcp: 69ツール）](#3-mcp-ツール完全一覧houdini-mcp-69ツール)
4. [外部 MCP サーバー（houdini-docs-mcp / api-docs）](#4-外部-mcp-サーバー)
5. [日本語ヘルプ統合（include_description_ja）](#5-日本語ヘルプ統合)
6. [ワークフロー管理システム（9ツール）](#6-ワークフロー管理システム9ツール)
7. [アーキテクチャ強制層（Hooks + GateState）](#7-アーキテクチャ強制層)
8. [CLAUDE.md 運用規約（v3）](#8-claudemd-運用規約v3)
9. [実運用フロー](#9-実運用フロー)
10. [ハマりどころ集](#10-ハマりどころ集)
11. [テストインフラ](#11-テストインフラ)
12. [設定ファイル一覧](#12-設定ファイル一覧)
13. [プロジェクト構造](#13-プロジェクト構造)

---

## 1. 全体アーキテクチャ

### システム構成図

```
┌─────────────────────────────────────────────────────────────────────┐
│  Claude Code (CLI)                                                  │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ PreToolUse Hook  │  │ PostToolUse Hook │  │  CLAUDE.md v3    │  │
│  │ houdini_gate.py  │  │ houdini_post_    │  │  (9ルール)       │  │
│  │                  │  │ tool.py          │  │                  │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────────────┘  │
│           │ reads                │ writes                           │
│           ▼                      ▼                                  │
│  ┌──────────────────────────────────────────┐                      │
│  │        .agent/gate_state.json            │ ← 橋渡しファイル     │
│  └──────────────────────────────────────────┘                      │
│           ▲ writes                                                  │
└───────────┼─────────────────────────────────────────────────────────┘
            │
┌───────────┼─────────────────────────────────────────────────────────┐
│  MCP サーバー群 (stdio)                                             │
│           │                                                         │
│  ┌────────┴────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  houdini-mcp        │  │houdini-docs  │  │  api-docs        │   │
│  │  (69ツール)         │  │-mcp (1ツール)│  │  (6ツール)       │   │
│  │  FastMCP + RPyC     │  │NotebookLM    │  │  VEX/CLO3D/Rhino │   │
│  └────────┬────────────┘  └──────────────┘  └──────────────────┘   │
│           │ RPyC (port 18811)                                       │
└───────────┼─────────────────────────────────────────────────────────┘
            │
┌───────────┼─────────────────────────────────────────────────────────┐
│  Houdini 20.5                                                       │
│           │                                                         │
│  ┌────────┴────────────┐                                            │
│  │  hrpyc サーバー     │  ← Houdini Python Shell で起動            │
│  │  port 18811         │     import hrpyc; hrpyc.start_server()    │
│  │  hou モジュール公開 │                                            │
│  └─────────────────────┘                                            │
└─────────────────────────────────────────────────────────────────────┘
```

### 3つの MCP サーバー

| サーバー | ツール数 | 役割 | 接続先 |
|---------|--------|------|-------|
| **houdini-mcp** | 69 | Houdini 操作の全機能 | Houdini (RPyC) |
| **houdini-docs-mcp** | 1 | NotebookLM 経由の日本語ドキュメント検索 | Google NotebookLM |
| **api-docs** | 6 | VEX / CLO3D / RhinoCommon のAPI検索 | ローカルJSON + Web |

---

## 2. 接続レイヤー

### RPyC 接続管理（`connection.py`）

Houdini との通信は RPyC (Remote Python Call) v5.x で行う。

```
Claude Code → MCP サーバー (Python) → RPyC → Houdini Python Shell → hou モジュール
```

**重要**: rpyc は **v5.x 必須**。v6.x はプロトコル非互換。

#### 主要関数

| 関数 | 用途 |
|------|------|
| `connect(host, port)` | RPyC 接続確立（指数バックオフ＋ジッター付きリトライ） |
| `ensure_connected(host, port)` | 冪等な接続取得（切断時は自動再接続） |
| `is_connected(validate=False)` | 接続状態確認（validate=True でRPC疎通確認） |
| `ping(host, port, timeout)` | ワンショット接続テスト |
| `get_connection_info(host, port)` | Houdini バージョン・hip ファイル等の情報取得 |
| `safe_execute(func, timeout)` | タイムアウト付きRPC実行（ThreadPoolExecutor 4ワーカー） |

#### リトライ設定

| パラメータ | デフォルト値 |
|-----------|------------|
| max_retries | 3 |
| base_delay | 1.0秒 |
| max_delay | 30.0秒 |
| exponential_base | 2.0 |
| jitter | 0〜10% ランダム |
| sync_timeout | 30.0秒（個別RPC呼び出し） |
| operation_timeout | 45.0秒（ツール操作全体） |

#### カスタム例外

| 例外 | 発生条件 |
|------|---------|
| `HoudiniConnectionError` | 接続失敗 |
| `HoudiniOperationError` | 操作失敗 |
| `HoudiniOperationTimeout` | タイムアウト |

#### Houdini 側の準備（毎回必要）

```python
# Houdini の Python Shell で実行
import hrpyc
hrpyc.start_server(port=18811)
```

---

## 3. MCP ツール完全一覧（houdini-mcp: 69ツール）

`server.py` に `@mcp.tool()` で登録された全69ツール（`grep -c "@mcp.tool()" server.py` = 69 で確認済み）。

**内訳**: 読み取り専用 26 + 変更系（Hook対象）34 + ワークフロー 9 = 69

### A. シーン管理（5ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `get_scene_info` | — | hip ファイル、バージョン、全コンテキスト(/obj,/stage,/out,/mat,/ch,/img,/shop)のノード一覧 |
| `serialize_scene` | `root_path="/obj"`, `include_params=False`, `summarize=False` | シーン階層を再帰的に辞書化。`summarize=True` で AI 要約付き |
| `save_scene` | `file_path=None` | 現在の hip を保存。パス指定で別名保存 |
| `load_scene` | `file_path` | .hip ファイル読み込み（キャッシュ無効化付き） |
| `new_scene` | — | 新規シーン作成（破壊的操作） |

### B. ノード操作（8ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `create_node` | `node_type`, `parent_path="/obj"`, `name=None` | ノード作成。**引数名は `name`**（~~node_name~~ ではない） |
| `delete_node` | `node_path` | ノード削除（不可逆） |
| `get_node_info` | `node_path`, `include_params=True`, `include_input_details=True`, `include_errors=False`, `force_cook=False`, `compact=False`, `include_description_ja=False` | ノード詳細取得。日本語説明オプション付き |
| `list_children` | `node_path`, `recursive=False`, `max_depth=10`, `max_nodes=1000`, `compact=False` | 子ノード一覧（接続情報付き） |
| `find_nodes` | `root_path="/obj"`, `pattern="*"`, `node_type=None`, `max_results=100`, `offset=0` | Glob/部分文字列パターン検索（ページネーション付き） |
| `list_node_types` | `category=None`, `max_results=100`, `name_filter=None`, `offset=0` | 利用可能なノードタイプ一覧（キャッシュ済み、初回200ms→以降<1ms） |
| `find_error_nodes` | `root_path="/"`, `include_warnings=True`, `max_results=100`, `summarize=False` | クックエラー/警告のあるノードを検索。AI トリアージ付き |
| `check_connection` | — | Houdini 接続状態、バージョン、hip ファイル確認 |

### C. パラメータ操作（2ツール + safe_set_parameter※）

> ※ `safe_set_parameter` はワークフロー管理ツール（§M）と重複。ユニークツール数の計上は §M 側で行う。

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `get_parameter_schema` | `node_path`, `parm_name=None`, `max_parms=100`, `include_description_ja=False` | パラメータの型・デフォルト・範囲・メニュー項目・日本語説明を取得 |
| `set_parameter` | `node_path`, `param_name`, `value` | パラメータ設定。**引数名は `param_name`**（~~parameter_name~~ ではない）。**直接呼び出しは Hook でブロック**（schema_checked 必要） |
| `safe_set_parameter` | `node_path`, `param_name`, `value`, `diff_id`, `project_root=None` | スキーマ検証付きパラメータ設定（**推奨**）。内部で自動的に schema 取得→型/menu/range 検証→set_parameter を実行 |

#### safe_set_parameter の内部フロー

```
1. gate_state 読み込み → diff_approved を確認
2. get_parameter_schema(include_description_ja=True) → schema 取得
3. _validate_value(value, schema) → 型/menu/range 検証
   - float/int/string/toggle の型チェック
   - menu パラメータ: menu_items の value 一覧に含まれるか
   - vector: tuple_size と一致するか
   - range: min/max 範囲内か
4. gate_state の schema_checked[node_path] = True に更新
5. set_parameter(node_path, param_name, value) 実行
```

#### 返り値例（safe_set_parameter 成功時）

```json
{
  "status": "success",
  "node_path": "/obj/geo1/sphere1",
  "param_name": "radx",
  "value": 2.5,
  "schema": {
    "name": "radx",
    "type": "float",
    "default": 1.0,
    "min": 0.0,
    "max": 10.0,
    "description_ja": "X方向の半径"
  },
  "schema_validated": true,
  "diff_id": "S01_20260301_120000_a1b2c3d4"
}
```

### D. 接続操作（4ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `connect_nodes` | `src_path`, `dst_path`, `dst_input_index=0`, `src_output_index=0` | ノード接続（カテゴリ互換性チェック付き） |
| `disconnect_node_input` | `node_path`, `input_index=0` | 入力接続の切断 |
| `set_node_flags` | `node_path`, `display=None`, `render=None`, `bypass=None` | 表示/レンダー/バイパスフラグ設定 |
| `reorder_inputs` | `node_path`, `new_order` (List[int]) | Merge ノード等の入力順序変更 |

### E. ジオメトリ/コード実行（3ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `get_geo_summary` | `node_path`, `max_sample_points=100`, `include_attributes=True`, `include_groups=True`, `summarize=False` | ポイント/プリミティブ数、バウンディングボックス、アトリビュート、グループ、サンプルポイント |
| `execute_code` | `code`, `capture_diff=False`, `max_stdout_size=100000`, `max_stderr_size=100000`, `max_diff_nodes=1000`, `timeout=30`, `allow_dangerous=False` | Houdini 内で Python コード実行。安全チェック付き |
| `get_last_scene_diff` | — | 直前の `execute_code(capture_diff=True)` で記録されたシーン差分（追加/削除/変更ノード）を取得 |

#### execute_code の実行環境

- **`hou` は自動注入される**: 実行環境の globals に `{"hou": hou, ...}` として注入済み
- **`import hou` は不要（かつ失敗する場合がある）**: RPyC 経由のリモート hou モジュールは `import` では取得できない。コード内では `hou` を直接使うこと
- **`__import__` は許可されている**: `import math`, `import json` 等の標準モジュールは使用可能（危険モジュールは事前フィルタで検出）

#### execute_code の安全機能

- **危険パターン検出**: `hou.exit()`, `os.remove()`, `subprocess`, `shutil.rmtree`, `eval()`, `exec()`, `open()` を事前スキャン
- **`allow_dangerous=False`（デフォルト）** で上記パターンをブロック
- **制限付き builtins**: `exec`, `eval`, `compile`, `open`, `input`, `exit`, `quit` 等を除外したサブセットのみ提供
- **出力制限**: stdout/stderr 各100KB（デフォルト）
- **タイムアウト**: 30秒（デフォルト、最大45秒）。スレッドベースのため強制停止はできない
- **シーン差分追跡**: `capture_diff=True` で before/after を比較

### F. レンダリング（7ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `render_viewport` | `camera_position`, `camera_rotation`, `look_at`, `resolution`, `renderer="opengl"`, `output_format="png"`, `auto_frame=True`, `orthographic=False`, `karma_engine="cpu"` | ビューポートレンダリング → base64 画像 |
| `render_quad_view` | `resolution`, `renderer="opengl"`, `output_format="png"`, `orthographic=True`, `include_perspective=True`, `karma_engine="cpu"` | 4面図（Front/Left/Top/Perspective）→ 4枚の base64 画像 |
| `list_render_nodes` | — | /out のROP一覧 |
| `get_render_settings` | `rop_path` | ROP パラメータ取得（Karma/Mantra/OpenGL） |
| `set_render_settings` | `rop_path`, `settings` (Dict) | ROP パラメータ変更 |
| `create_render_node` | `rop_type`, `name=None`, `settings=None` | ROP 作成（opengl, karma, ifd） |
| `render_node_network` | `node_path="/obj"`, `fit_contents=True` | ネットワークエディタのスクリーンショット |

**RULE 4 制約**:
- `render_viewport`: **`resolution=[640, 480]` 以下を必ず指定**（デフォルト解像度は token 超過の原因）
- `render_quad_view`: **`resolution=[480, 360]` 以下を指定**
- **注意**: `render_viewport` / `render_quad_view` には `save_path` パラメータが**存在しない**（常に base64 を返す）。token 超過を防ぐには低解像度指定が唯一の手段

**capture 系ツールの token 超過対策（原則 save_path）**:
- `capture_pane_screenshot` / `capture_multiple_panes` は **原則 `save_path` を指定してファイル保存**する
- `save_path` を省略すると base64 で返るため、高解像度ペインでは token 超過の原因になる
- 確認目的の低解像度キャプチャ以外は、必ず `save_path` / `save_dir` を使う

### G. マテリアル（3ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `create_material` | `material_type="principledshader"`, `name=None`, `parent_path="/mat"`, `parameters=None` | マテリアル作成 |
| `assign_material` | `geometry_path`, `material_path`, `group=""` | マテリアル割り当て |
| `get_material_info` | `material_path` | マテリアル情報取得 |

サポートタイプ: `principledshader`（PBR）, `mtlxstandard_surface`（MaterialX）, `classicshader`, `arnold::standard_surface`

### H. ドキュメント/ヘルプ（2ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `get_houdini_help` | `help_type`, `item_name`, `timeout=10` | SideFX 公式ドキュメント取得（Web スクレイピング）。Houdini 接続不要 |
| `get_summarization_status` | — | AI 要約設定の確認 |

`help_type` のサポート値: `sop`, `obj`, `dop`, `cop2`, `chop`, `vop`, `lop`, `top`, `rop`, `vex_function`, `python_hou`

### I. UI/ペイン（4ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `capture_pane_screenshot` | `pane_type_name="NetworkEditor"`, `save_path=None`, `fit_contents=False` | ペインのスクリーンショット → base64 PNG |
| `list_visible_panes` | — | 表示中ペイン一覧（30種対応: NetworkEditor, SceneViewer, Parm 等） |
| `capture_multiple_panes` | `pane_types` (List[str]), `save_dir=None` | 複数ペインの一括キャプチャ |
| `layout_children` | `node_path`, `horizontal_spacing=2.0`, `vertical_spacing=1.0` | 子ノードの自動整列 |

**RULE 4 制約**: `capture_pane_screenshot` は先に `list_visible_panes()` でペインの存在を確認してから呼ぶこと。

### J. ネットワーク整理（3ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `set_node_color` | `node_path`, `color` (List[float]) | ノード色設定 [r,g,b] 0.0-1.0 |
| `set_node_position` | `node_path`, `x`, `y` | ネットワークエディタ上の位置設定 |
| `create_network_box` | `parent_path`, `node_paths` (List[str]), `label=""`, `color=None` | ノード群を囲むネットワークボックス作成 |

### K. キャッシュ（1ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `manage_cache` | `action="stats"` | キャッシュ操作: `stats`（統計）, `invalidate`（クリア）, `warmup`（事前読み込み） |

### L. クラウドシミュレーション（17ツール）

#### エージェント設定

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `setup_crowd_agent` | `agent_name`, `clip_paths=None`, `parent_path="/obj"`, `agent_file=None` | agent → agentclip → agentprep → agentdefinitioncache チェーン構築 |
| `setup_crowd_source` | `parent_path`, `agent_setup_path`, `source_type="scatter"`, `count=100`, `initial_state="idle"` | エージェント配置（scatter / points） |
| `setup_crowd_formation` | `parent_path`, `agent_setup_path`, `rows=5`, `columns=5`, `spacing=2.0`, `initial_state="idle"` | グリッド隊列配置 |

#### DOP シミュレーション

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `setup_crowd_dop` | `parent_path="/obj"`, `crowd_source_path=None`, `solver_substeps=1` | DOPネット作成（crowdobject, crowdsolver, groundplane, gravity） |
| `add_crowd_behavior` | `dopnet_path`, `behavior_type`, `behavior_name=None`, `settings=None` | POP Steer 追加（wander, seek, avoid, follow_path, separate, cohesion, alignment, obstacle） |
| `add_crowd_state` | `dopnet_path`, `state_name`, `clip_name=None`, `locomotion=False`, `playback_speed=1.0`, `variance=0.0` | アニメーション状態追加（walk, idle, run） |
| `add_crowd_transition` | `dopnet_path`, `from_state`, `to_state`, `trigger_type`, `trigger_settings=None` | 状態遷移追加（distance, time, random, speed, attribute, state_duration, custom） |
| `add_steer_weight` | `dopnet_path`, `behavior_name`, `weight` | 行動重み設定 |

#### 高度な設定

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `setup_crowd_terrain` | `parent_path`, `terrain_source=None`, `enable_foot_locking=True` | 地形適応 + フットロック設定 |
| `configure_foot_planting` | `agent_setup_path`, `ankle_joint="LeftAnkle"`, `toe_joint="LeftToe"`, etc. | 足接地のジョイント設定 |
| `add_crowd_look_at` | `dopnet_path`, `target_position=None`, `target_node_path=None`, etc. | 視線追跡 |
| `add_crowd_clip_transition_graph` | `parent_path`, `agent_setup_path`, `clip_names=None`, `blend_frames=10`, `auto_compute=True` | クリップ間ブレンド |
| `add_crowd_layer` | `parent_path`, `agent_setup_path`, `layer_name`, `shape_geometry_path=None`, etc. | 見た目レイヤー追加（装備、服装バリエーション） |
| `setup_crowd_ragdoll` | `agent_setup_path`, `dopnet_path`, `collision_shape="capsule"`, `create_joints=True` | ラグドール物理設定 |
| `setup_motion_path` | `parent_path`, `agent_setup_path`, `clip_name="walk"`, `clip_speed=1.0`, etc. | SOP ベースモーションパス（Houdini 20+ 対応） |

#### 調査・検証

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `get_crowd_info` | `root_path="/obj"` | 既存クラウド構成のスキャン・レポート |
| `validate_crowd_setup` | `root_path="/obj"` | 不足コンポーネント・設定問題の検出 |

### M. ワークフロー管理（9ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `ensure_project` | `project_root=None` | `.agent/` ディレクトリの初期化/検証 + gate_state 初期化 |
| `read_project` | `target="all"`, `project_root=None` | spec/stages/contracts/build_state の読み取り |
| `plan_stage` | `stage_id`, `description`, `project_root=None` | ステージ計画作成 + `pending→in_progress` 遷移 |
| `record_scan` | `scan_data` (Dict), `stage_id=None`, `project_root=None` | シーンスキャン記録 |
| `validate_stage` | `stage_id`, `scan_data=None`, `project_root=None` | plan/scan/依存の検証（ステータスは変えない） |
| `update_stage` | `stage_id`, `status`, `reason=None`, `project_root=None` | ステータス遷移（状態マシンで強制） |
| `propose_diff` | `stage_id`, `diff_spec` (Dict), `project_root=None` | 差分案提出 → diff_id 発行 |
| `approve_diff` | `diff_id`, `project_root=None` | 差分承認 → 変更系ツール解禁 |
| `safe_set_parameter` | `node_path`, `param_name`, `value`, `diff_id`, `project_root=None` | スキーマ検証付きパラメータ設定 |

### N. 接続テスト（1ツール）

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `ping_houdini` | — | 接続テスト（ワンショット） |

### ツールの分類: 読み取り専用 vs 変更系

#### 読み取り専用ツール（ワークフロー初期化前でも使用可能）

```
get_scene_info / serialize_scene / get_node_info / list_children /
find_nodes / get_geo_summary / get_parameter_schema /
list_node_types / ping_houdini / check_connection /
render_viewport / render_quad_view / capture_pane_screenshot /
list_visible_panes / capture_multiple_panes / render_node_network /
find_error_nodes / get_houdini_help / manage_cache /
get_summarization_status / get_render_settings / list_render_nodes /
get_material_info / get_crowd_info / validate_crowd_setup /
get_last_scene_diff
```

#### 変更系ツール（全5条件クリア後のみ許可、Hook でブロック）

```
create_node / delete_node / set_parameter / connect_nodes /
disconnect_node_input / reorder_inputs / set_node_flags / execute_code /
create_material / assign_material / create_render_node / set_render_settings /
setup_crowd_* / add_crowd_* / configure_foot_planting /
new_scene / save_scene / load_scene /
set_node_color / set_node_position / layout_children / create_network_box
```

計 **34ツール**が PreToolUse Hook の対象（`safe_set_parameter` は自身が内部で検証するため Hook 対象外）。

---

## 4. 外部 MCP サーバー

### 4.1 houdini-docs-mcp（NotebookLM 連携）

| 項目 | 内容 |
|------|------|
| 場所 | `~/dev-setup/houdini-docs-mcp/` |
| フレームワーク | FastMCP (Python) |
| 接続 | stdio |
| データソース | Google NotebookLM（日本語 Houdini ヘルプ + 公式ガイド収録） |
| ツール数 | 1 |

#### ツール

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `query_houdini_docs` | `question` (str) | NotebookLM に質問。日本語/英語対応。ノード、パラメータ、ワークフロー、VEX、SOP、DOP、Solaris/USD 等 |

#### MCP 設定（`~/.claude.json`）

```json
"houdini-docs-mcp": {
  "type": "stdio",
  "command": "cmd",
  "args": ["/c", "python", "-m", "houdini_docs_mcp"],
  "env": {
    "MCP_TRANSPORT": "stdio",
    "FASTMCP_LOG_LEVEL": "ERROR",
    "HOUDINI_NOTEBOOKLM_URL": "https://notebooklm.google.com/notebook/..."
  }
}
```

#### 実装の注意点

- 内部で `~/.claude/skills/notebooklm/scripts/ask_question.py` を subprocess で呼び出し
- **`stdin=subprocess.DEVNULL` 必須** — MCP サーバーの stdin は Claude Code との JSON-RPC パイプ。子プロセスが継承するとハング
- **Windows では `asyncio.to_thread(subprocess.run)`** — `asyncio.create_subprocess_exec` は ProactorEventLoop でパイプ I/O 問題あり
- タイムアウト: 180秒

### 4.2 api-docs（VEX / CLO3D / RhinoCommon）

| 項目 | 内容 |
|------|------|
| 場所 | `~/api-docs-mcp-server/` |
| フレームワーク | @modelcontextprotocol/sdk (TypeScript/Node.js) |
| 接続 | Node.js subprocess |
| データソース | ローカル JSON インデックス + Web フォールバック |
| ツール数 | 6 |

#### ツール

| ツール | パラメータ | 説明 |
|--------|-----------|------|
| `search_vex_function` | `query` | VEX 関数検索（例: "noise", "sample", "transform"） |
| `get_vex_details` | `function_name` | VEX 関数の詳細（構文、パラメータ、戻り値、使用例） |
| `search_clo3d_api` | `query` | CLO3D API 検索 |
| `get_clo3d_details` | `item` | CLO3D API 詳細 |
| `search_rhinocommon` | `query` | RhinoCommon API 検索 |
| `get_rhinocommon_details` | `item` | RhinoCommon API 詳細 |

#### データソース

| ファイル | サイズ | 内容 |
|---------|-------|------|
| `docs/houdini-vex/index.json` | 2.9 MB | 日本語 VEX ドキュメント（sidefx.com/ja/docs/） |
| `docs/clo3d/index.json` | 520 KB | CLO3D API ドキュメント |
| `docs/rhinocommon/index.json` | 4.2 MB | RhinoCommon API ドキュメント |

検索戦略: ローカル JSON → Web スクレイピング → ハードコード定義（フォールバック）

---

## 5. 日本語ヘルプ統合

### 概要

`get_parameter_schema` と `get_node_info` に `include_description_ja=True` パラメータを追加。
ローカルの日本語ヘルプファイル（**11,040件**）からパラメータ説明を取得して統合する。

### 対応ツール

| ツール | 追加フィールド |
|--------|-------------|
| `get_parameter_schema(include_description_ja=True)` | 各パラメータに `description_ja`, `menu_descriptions_ja` 追加 |
| `get_node_info(include_description_ja=True)` | `description_ja`（ノード概要）, `parameter_descriptions_ja` dict 追加 |
| `safe_set_parameter` | 内部で自動的に `include_description_ja=True` を使用 |

### ヘルプファイルの場所

```
~/Documents/houdini20.5/help/nodes/{context}/
```

環境変数 `HOUDINI_HELP_PATH` で変更可能。

### パーサーの仕組み（`help_local.py`）

```
カテゴリマッピング: Sop→sop, Object→obj, Driver→out
ファイル名解決:     box→box.txt, attribcreate::2.0→attribcreate.txt, labs::foo→labs--foo.txt
セクション解析:     @parameters セクションをインデント適応型で解析（4スペース / 0スペース 両対応）
パラメータ名解決:   #id: > #channels: > ラベル一致 の優先順位
キャッシュ:         スレッドセーフ、TTL不要（静的ファイル）
```

### 出力例

```json
{
  "name": "radx",
  "type": "float",
  "default": 1.0,
  "description_ja": "X方向の半径を指定します。",
  "menu_descriptions_ja": null
}
```

### 既知の制限

- ヘルプファイル上のラベルと Houdini API のラベルが微妙に異なるパラメータは未マッチ
- `:include` ディレクティブは未展開

---

## 6. ワークフロー管理システム（9ツール）

### 目的

Claude Code に Houdini を操作させる際「設計→承認→実装→検証」の流れを**機械的に強制**する仕組み。

### .agent/ ディレクトリ構造

```
project_root/
└── .agent/
    ├── spec.md                           ← 仕様（ユーザーが記入）
    ├── stages.yaml                       ← ステージ定義（ユーザーが記入）
    ├── contracts.yaml                    ← 将来用（未使用）
    ├── build_state.json                  ← プロジェクト状態（自動管理・手動編集禁止）
    ├── gate_state.json                   ← 強制層の状態（自動管理・手動編集禁止）
    ├── stage_plans/
    │   └── {stage_id}.md                 ← ステージ実装計画
    ├── diffs/
    │   └── {diff_id}.json                ← 差分提案の記録
    ├── scene_scans/
    │   └── {stage_id}_{timestamp}.json   ← シーンスナップショット
    └── logs/
        └── {event}_{timestamp}.json      ← 操作ログ
```

### 正本ファイルの役割

| ファイル | 役割 | 管理 |
|---------|------|------|
| `spec.md` | プロジェクト仕様（Goal / Requirements / I/O / Acceptance Criteria） | ユーザー記入 |
| `stages.yaml` | ステージ定義（id / name / purpose / depends_on / produces / consumes） | ユーザー記入 |
| `build_state.json` | 現在地メタデータ（active_stage, stages状態, artifacts） | 自動管理 |
| `gate_state.json` | 強制層の状態（workflow_initialized, diff_approved 等） | 自動管理 |
| `contracts.yaml` | 将来用。**現時点では未使用** | — |

### Readiness Gate の判定条件

`ensure_project()` が `.agent/spec.md` と `.agent/stages.yaml` を検証する:

**spec.md の条件**:
- 必須セクション: Goal, Requirements **OR** Must Have, Inputs, Outputs, Acceptance Criteria
- テンプレートのまま（"(Describe...)", "(Define...)", "(List...)", "(Specify...)"）→ **NG**
- 空行・箇条書き1行だけ → **NG**

**stages.yaml の条件**:
- `stages:` キーに非空のリスト
- 各ステージに必須フィールド: `id`, `name`, `purpose`
- リストフィールド: `depends_on`, `produces`, `consumes`

### 状態遷移ルール

```
pending → in_progress → validated → completed
              ↓
            failed → in_progress（リトライ）
```

| 遷移 | トリガー | 追加条件 |
|------|---------|---------|
| pending → in_progress | `plan_stage()` | readiness == true |
| in_progress → validated | `update_stage("validated")` | `last_validation_passed == true` |
| in_progress → failed | `update_stage("failed")` | — |
| validated → completed | `update_stage("completed")` | — |
| failed → in_progress | `plan_stage()` (再計画) | — |
| completed → * | **不可**（終端状態） | — |

### 各ツールの詳細

#### ensure_project

```python
ensure_project(project_root=None) → dict
```

- `.agent/` を bootstrap（存在しなければテンプレートファイル作成）
- 既存ファイルは上書きしない（冪等）
- サブディレクトリ作成: `stage_plans/`, `scene_scans/`, `logs/`, `diffs/`
- **gate_state 更新**: `workflow_initialized=True`, `ready_for_implementation=<readiness結果>`

返り値例:
```json
{
  "status": "success",
  "project_root": "/path/to/project",
  "bootstrapped": true,
  "ready_for_implementation": false,
  "missing_requirements": ["spec.md: Goal section is empty"],
  "next_action": "Fill in spec.md and stages.yaml"
}
```

#### read_project

```python
read_project(target="all", project_root=None) → dict
```

`target`: `"all"`, `"spec"`, `"stages"`, `"contracts"`, `"build_state"`

- 指定ファイルの内容を返す（spec.md はテキスト、stages.yaml は YAML パース済み dict）
- **gate_state 更新**: ensure_project と同じ

#### plan_stage

```python
plan_stage(stage_id, description, project_root=None) → dict
```

- readiness == false → エラー
- ステージの現在ステータスが `pending` or `failed` でないと → エラー
- `stage_plans/{stage_id}.md` に計画ファイル作成
- **gate_state リセット**: `active_stage=stage_id`, `active_stage_in_progress=True`, `diff_approved=False`, `schema_checked={}`, `error_counts={}`, `docs_referenced=False`

#### propose_diff

```python
propose_diff(stage_id, diff_spec, project_root=None) → dict
```

`diff_spec` のキー:
```json
{
  "nodes_to_add": [{"type": "sphere", "name": "my_sphere", "parent_path": "/obj/geo1"}],
  "nodes_to_delete": ["/obj/geo1/old_node"],
  "connections_to_add": [{"src_path": "...", "dst_path": "...", "dst_input_index": 0}],
  "connections_to_remove": [{"node_path": "...", "input_index": 0}],
  "params_to_change": [{"node_path": "...", "param_name": "...", "value": ...}],
  "scope": ["/obj/geo1"],
  "rationale": "なぜこの変更が必要か"
}
```

- `gate.active_stage == stage_id` かつ `active_stage_in_progress` でないと → エラー
- diff_id 生成: `{stage_id}_{timestamp}_{8桁UUID}`
- `.agent/diffs/{diff_id}.json` に保存（`approved: false`）
- **gate_state 更新**: `pending_diff_id=diff_id`, `diff_approved=False`

返り値例:
```json
{
  "status": "success",
  "diff_id": "S01_20260301_120000_a1b2c3d4",
  "stage_id": "S01",
  "diff_file": "diffs/S01_20260301_120000_a1b2c3d4.json",
  "message": "Diff proposed. Call approve_diff(diff_id) after user approval."
}
```

#### approve_diff

```python
approve_diff(diff_id, project_root=None) → dict
```

- `gate.pending_diff_id == diff_id` を検証
- **gate_state 更新**: `diff_approved=True`
- diff ファイルに `approved: true`, `approved_at: <ISO8601>` を書き込み
- **この時点で変更系ツールが解禁される**

#### record_scan

```python
record_scan(scan_data, stage_id=None, project_root=None) → dict
```

- `stage_id` 省略時は `active_stage` を使用（なければ `"unassigned"`）
- `.agent/scene_scans/{stage_id}_{timestamp}.json` に保存
- `build_state.last_scan` と `stages[stage_id].last_scan_file` を更新

#### validate_stage

```python
validate_stage(stage_id, scan_data=None, project_root=None) → dict
```

4つのチェック:
1. **plan_exists**: `stage_plans/{stage_id}.md` が存在するか
2. **scan_recorded**: last_scan_file が設定されているか
3. **artifacts_registered**: （現在は常に pass — 将来用）
4. **dependencies_completed**: `stages.yaml` の `depends_on` が全て completed か

- `last_validation_passed` を更新するが、**ステータスは変更しない**

返り値例:
```json
{
  "status": "success",
  "stage_id": "S01",
  "passed": true,
  "checks": [
    {"check": "plan_exists", "passed": true, "detail": "Plan file found: S01.md"},
    {"check": "scan_recorded", "passed": true, "detail": "Scan file recorded"},
    {"check": "artifacts_registered", "passed": true, "detail": "No artifacts required"},
    {"check": "dependencies_completed", "passed": true, "detail": "No dependencies"}
  ]
}
```

#### update_stage

```python
update_stage(stage_id, status, reason=None, project_root=None) → dict
```

- 状態マシンの遷移ルールを強制
- `"validated"` への遷移は `last_validation_passed == True` が必須
- **gate_state 更新**:
  - `completed`: active_stage=None, in_progress=False, diff_approved=False, schema_checked={}, error_counts={}, docs_referenced=False
  - `failed`: in_progress=False, diff_approved=False
  - `in_progress`（リトライ）: active_stage=stage_id, in_progress=True
  - `validated`: diff_approved=False（次バッチ用リセット）

---

## 7. アーキテクチャ強制層

CLAUDE.md のルールが読み飛ばされても、**コードレベルで変更系ツールをブロック**する4層の仕組み。

### 全体像

```
層A: PreToolUse Hook (houdini_gate.py)
     → 変更系ツール呼び出し前に gate_state.json をチェック
     → 条件未達なら deny（ツール呼び出しが拒否される）

層B: propose_diff / approve_diff
     → 差分案の提出→承認を機械的ロックで強制
     → approve なしでは変更系ツールがブロック

層C: safe_set_parameter
     → get_parameter_schema + 型/menu/range 検証 + set_parameter を一括
     → raw set_parameter は schema_checked なしでブロック

層D: PostToolUse Hook (houdini_post_tool.py)
     → エラーカウント追跡 + ドキュメント参照検出
     → 2回以上のエラー後、ドキュメント参照まで再試行ブロック
```

### 共有状態: gate_state.json

```python
@dataclass
class GateState:
    workflow_initialized: bool = False        # ensure_project() 済み
    ready_for_implementation: bool = False     # spec/stages 記入済み
    active_stage: str | None = None           # 現在の stage_id
    active_stage_in_progress: bool = False    # stage が in_progress
    pending_diff_id: str | None = None        # 承認待ち diff_id
    diff_approved: bool = False               # 差分承認済み
    schema_checked: dict[str, bool] = {}      # node_path → schema 確認済み
    error_counts: dict[str, int] = {}         # tool_name → エラー回数
    docs_referenced: bool = False             # ドキュメント参照済み
    last_updated: str = ""                    # ISO 8601
```

- **MCP ツール（サーバー側）**が書き込む
- **Hook（クライアント側）**が読み取る
- アトミック書き込み: `.tmp` → `rename()`

### 層A: PreToolUse Hook（houdini_gate.py）

場所: `~/.claude/hooks/houdini_gate.py`（2026-03 時点。Claude Code のバージョンにより設定場所が異なる場合あり）

**チェック順序**（上から順に評価、最初に失敗した時点で deny）:

| # | チェック | 失敗時のメッセージ | 次のアクション |
|---|---------|------------------|-------------|
| 1 | `workflow_initialized == True` | "Workflow not initialized" | `ensure_project()` を呼ぶ |
| 2 | `ready_for_implementation == True` | "Project not ready" | spec/stages を記入して `ensure_project()` |
| 3 | `active_stage_in_progress == True` | "No active stage" | `plan_stage()` を呼ぶ |
| 4 | `diff_approved == True` | "No approved diff" | `propose_diff()` → `approve_diff()` |
| 5 | `set_parameter` の場合: `schema_checked[node_path] == True` | "Schema not checked" | `safe_set_parameter()` または `get_parameter_schema()` |
| 6 | `error_counts[tool] >= 2 && !docs_referenced` | "Too many errors" | ドキュメント参照ツールを呼ぶ |

**deny 出力フォーマット（Claude Code hooks API）**:
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "... [Next: call propose_diff()]"
  }
}
```

**allow**: 何も出力せず `exit 0`

**Fail-open 設計**: gate_state.json が無いプロジェクトでは全て allow（非ワークフロープロジェクトを妨げない）

**Hook matcher の保守ルール**: houdini-mcp に変更系ツールを追加したら、Hook matcher の regex にも必ず追加し、`server.py` のツール登録一覧との差分チェックを行うこと（現在 34 ツール）

### 層D: PostToolUse Hook（houdini_post_tool.py）

場所: `~/.claude/hooks/houdini_post_tool.py`

全 `mcp__houdini-mcp__*` ツールの結果を監視:

| トリガー | アクション |
|---------|----------|
| `tool_output.status == "error"` | `error_counts[tool_name]++` |
| ツールが DOC_TOOLS に該当 & 成功 | `docs_referenced=True`, `error_counts={}` リセット |
| `get_parameter_schema` 成功 | `schema_checked[node_path]=True` |

**DOC_TOOLS（6つ）**:
```
get_parameter_schema / get_node_info / query_houdini_docs /
get_houdini_help / search_vex_function / get_vex_details
```

### Hook 設定（`~/.claude/settings.json`）

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "mcp__houdini-mcp__(create_node|delete_node|set_parameter|...計34ツール...)",
      "hooks": [{"type": "command", "command": "python ~/.claude/hooks/houdini_gate.py"}]
    }],
    "PostToolUse": [{
      "matcher": "mcp__houdini-mcp__.*",
      "hooks": [{"type": "command", "command": "python ~/.claude/hooks/houdini_post_tool.py"}]
    }]
  }
}
```

---

## 8. CLAUDE.md 運用規約（v3）

### ルール全体像

| ルール | 概要 | 強制レベル |
|--------|------|----------|
| **RULE 0** | 変更系ツール呼び出しの5条件 | Hook で機械的強制 |
| **RULE 1** | 入口プロトコル（Q1/Q2） | テキスト規約 |
| **RULE 2** | Workflow 初期化 | Hook で機械的強制 |
| **RULE 3** | Stage 運用（plan → diff → approve → execute → scan → validate） | Hook で機械的強制 |
| **RULE 4** | UI/Viewport の解像度制限 | テキスト規約 |
| **RULE 5** | 引数名の正確性 | テキスト規約 |
| **RULE 6** | パラメータ事前確認（safe_set_parameter） | Hook で機械的強制 |
| **RULE 7** | ドキュメント参照の義務 | Hook で機械的強制（2回エラー後） |
| **RULE 8** | 禁止事項15項目 | テキスト規約 + Hook |

### RULE 1: 入口プロトコル

Houdini の話題が出たら**何よりも先に**2問を質問する:

**Q1: プロジェクト状況**
> A) これから新規で作る
> B) 既存シーン/既存ネットワークを進める

**Q2: 進め方のスタイル**
> 1) 補助モード — 基本は自分が作業。あなたは「差分案→確認→差分だけ実行」
> 2) 任せる寄り — 基本はあなたが進める。自分が時々レビュー

**Q2=1（補助モード）**: 1操作ごとに `propose_diff` → 提示 → `approve_diff`
**Q2=2（任せる寄り）**: stage 単位でまとめて `propose_diff` → 提示 → `approve_diff`

### RULE 3-B: 差分案のフォーマット

```markdown
### 差分案: [操作の概要]

**追加ノード:**
- [タイプ] "[名前]" → [親パス]

**削除ノード:**
- [node_path]

**接続変更:**
- [src_path] output[N] → [dst_path] input[N]

**パラメータ変更:**
- [node_path] / [param_name]: [旧値] → [新値]

**影響範囲:** [触る node_path / ネットワーク]
**根拠:** [なぜこの変更が必要か]
```

### RULE 5: 間違いやすい引数名

| ツール | 正しい引数 | 間違い（使用禁止） |
|--------|-----------|-----------------|
| `create_node` | `name` | ~~node_name~~ |
| `set_parameter` | `param_name` | ~~parameter_name~~ |
| `find_error_nodes` | `root_path` | ~~path~~ |
| `capture_pane_screenshot` | `pane_type_name` | ~~pane_type~~, ~~type~~ |
| `set_node_flags` | `display`, `render`, `bypass` | ~~visible~~, ~~flag~~ |
| `connect_nodes` | `src_path`, `dst_path`, `dst_input_index`, `src_output_index` | ~~source~~, ~~destination~~ |

### RULE 7: ドキュメント参照の優先順位

```
1st: get_parameter_schema(include_description_ja=True)   ← ローカル日本語ヘルプ
2nd: get_node_info(include_description_ja=True)           ← ノード概要の日本語説明
3rd: query_houdini_docs(question)                          ← NotebookLM（houdini-docs-mcp）
4th: search_vex_function(query) / get_vex_details(name)   ← VEX（api-docs サーバー）
5th: get_houdini_help(help_type, item_name)               ← SideFX 公式サイト
```

**トリガー条件**:
1. 複雑な領域に入る直前（OpenCL / Solver / Volume / L-System / VEX / USD / KineFX / CHOP / PDG）
2. パラメータが見つからない
3. 同じ操作で2回以上エラー（**Hook で自動強制**）
4. 想定と違う挙動
5. やり直しが2回以上発生

### RULE 8: 禁止事項（15項目）

1. Q1/Q2 未完了のまま Houdini ツールを呼ぶ
2. `ready_for_implementation == false` のまま変更系ツールを呼ぶ
3. `plan_stage` 未実行のまま変更系ツールを呼ぶ
4. `propose_diff` → `approve_diff` なしで変更系ツールを呼ぶ
5. ユーザー確認なしで変更系ツールを呼ぶ（Q2=1 の場合）
6. `record_scan` / `validate_stage` を飛ばして stage を完了にする
7. `build_state.json` / `gate_state.json` を手動編集する
8. `render_viewport` をデフォルト解像度で呼ぶ
9. `get_parameter_schema` 確認せず raw `set_parameter` を呼ぶ
10. パラメータ名・引数名を推測で渡す
11. 既存シーンに対していきなり作り直しを提案する
12. `contracts.yaml` を使う
13. `execute_code` を個別ツールで実現可能な操作に使う
14. ドキュメント参照トリガーに該当する状況でドキュメントを引かない
15. エラー連続（2回以上）後にドキュメント参照せずリトライする（Hook で強制）

### execute_code の位置づけ

- **使うべき場面**: 既存の個別ツールでは実現できない操作のみ
- **使わない場面**: `create_node` + `set_parameter` + `connect_nodes` で実現できる操作
- 使う場合も `propose_diff` → `approve_diff` は必須

### セッション開始時チェックリスト（15ステップ）

```
 1. [ ] Q1（新規/既存）をユーザーに質問して確定する
 2. [ ] Q2（補助/任せる）をユーザーに質問して確定する
 3. [ ] ensure_project() を実行する
 4. [ ] read_project(target="all") を実行する
 5. [ ] ready_for_implementation を確認する
 6. [ ] false なら spec/stages を整備して true にする
 7. [ ] plan_stage() を実行して stage を開始する
 8. [ ] propose_diff(stage_id, diff_spec) を呼んで diff_id を取得する
 9. [ ] 差分案をユーザーに提示して OK を得る
10. [ ] approve_diff(diff_id) を呼んで変更を解禁する
11. [ ] safe_set_parameter() または変更系ツールを実行する
12. [ ] get_scene_info() でシーン状態を取得する
13. [ ] record_scan() でスキャンを記録する
14. [ ] validate_stage() で検証する
15. [ ] update_stage("validated") → update_stage("completed") で完了する
```

**ステップ 8〜15 は stage ごとに繰り返す。**

---

## 9. 実運用フロー

### フロー全体図

```
ユーザー「Houdini で反応拡散パターンを作りたい」
    │
    ▼
[RULE 1] Q1/Q2 質問
    │  Q1=A（新規）、Q2=2（任せる寄り）
    ▼
[RULE 2] Workflow 初期化
    │  ensure_project() → read_project("all")
    │  ready=false → spec.md / stages.yaml 下書きを提案
    │  ユーザー合意 → 書き込み
    │  ensure_project() → ready=true
    ▼
[RULE 3-A] Stage 計画
    │  plan_stage("S01", "反応拡散 SOP ネットワーク構築")
    │  gate_state: active_stage="S01", in_progress=true
    ▼
[RULE 3-B] 差分提案
    │  propose_diff("S01", {
    │    nodes_to_add: [{type:"grid",...}, {type:"solver",...}, ...],
    │    connections_to_add: [...],
    │    params_to_change: [...],
    │    rationale: "反応拡散にはGrid+Solver+VolumeWrangleが必要"
    │  })
    │  → diff_id を取得
    │
    │  ユーザーに差分案を提示
    │  ユーザー「OK」
    │
    │  approve_diff(diff_id)
    │  gate_state: diff_approved=true → 変更系ツール解禁
    ▼
[RULE 3-B + RULE 6] 実装
    │  create_node("grid", "/obj/geo1", "base_grid")
    │  safe_set_parameter("/obj/geo1/base_grid", "rows", 100, diff_id)
    │    → 内部で schema 取得→型検証→set_parameter
    │  connect_nodes("/obj/geo1/base_grid", "/obj/geo1/solver1")
    │  ...
    ▼
[RULE 3-C] 検証
    │  get_scene_info() → scan_data
    │  record_scan(scan_data, "S01")
    │  validate_stage("S01") → passed=true
    │  update_stage("S01", "validated")
    │  update_stage("S01", "completed")
    │  gate_state: リセット（次の stage 用）
    ▼
次の Stage（S02, S03...）へ
```

### エラー連続時のフロー

```
create_node("opencl_sop", ...) → エラー          error_counts["create_node"] = 1
create_node("opencl", ...) → エラー               error_counts["create_node"] = 2
    │
    │  ← 次の create_node 呼び出しは Hook が deny
    │  「Tool 'create_node' has failed 2 times. Reference documentation first.」
    ▼
get_parameter_schema("/obj/geo1", include_description_ja=True)  → 成功
    │  Post hook: docs_referenced=true, error_counts={} リセット
    ▼
create_node("opencl", ...) → 今度は通る（docs_referenced=true）
```

---

## 10. ハマりどころ集

### 接続設定

| 問題 | 原因 | 解決策 |
|------|------|-------|
| MCP サーバーが HTTP モードで起動 | `MCP_TRANSPORT=stdio` が未設定 | 環境変数で明示指定 |
| FastMCP のバナーで接続失敗 | `FASTMCP_LOG_LEVEL=ERROR` が未設定 | stderr 出力を抑制 |
| settings.json と ~/.claude.json が競合 | 両方に設定がある | `~/.claude.json` に統一 |
| Windows で起動しない | Python が直接実行できない | `cmd /c python` でラップ |
| rpyc v6 で接続失敗 | プロトコル非互換 | **rpyc v5.x を使う** |

### 引数ミス（実テストで繰り返し発生）

| ツール | よくあるミス | 正しい引数 |
|--------|-----------|-----------|
| `create_node` | `node_name="sphere"` | `name="sphere"` |
| `set_parameter` | `parameter_name="tx"` | `param_name="tx"` |
| `find_error_nodes` | `path="/"` | `root_path="/"` |
| `capture_pane_screenshot` | `pane_type="NetworkEditor"` | `pane_type_name="NetworkEditor"` |
| `connect_nodes` | `source=..., destination=...` | `src_path=..., dst_path=...` |

### Token 超過回避

| 問題 | 原因 | 解決策 |
|------|------|-------|
| render_viewport で巨大レスポンス | デフォルト解像度が高い | **`resolution=[640, 480]` 以下を必ず指定**（`save_path` パラメータは存在しない） |
| render_quad_view で巨大レスポンス | 4枚の画像 | **`resolution=[480, 360]` 以下**（`save_path` パラメータは存在しない） |
| capture_pane_screenshot で巨大レスポンス | 高解像度ペインの base64 | **原則 `save_path` を指定**してファイル保存。確認用の低解像度以外は必須 |
| capture_multiple_panes で巨大レスポンス | 複数ペインの base64 | **原則 `save_dir` を指定**してファイル保存 |

### execute_code の落とし穴

| 問題 | 原因 | 解決策 |
|------|------|-------|
| 安易に execute_code で全操作 | create_node + connect_nodes で済む操作まで Python で書く | **個別ツールを優先、execute_code は最後の手段** |
| 危険パターンでブロック | `hou.exit()`, `os.remove()` 等 | `allow_dangerous=True`（最終手段） |

### ドキュメント参照の不足

| 問題 | 解決策 |
|------|-------|
| 複雑領域で推測して進める | RULE 7 のトリガー条件に該当したら**必ず**ドキュメント参照 |
| 2回以上エラー後にリトライ | **Hook が自動ブロック**。DOC_TOOLS を呼んで解除 |
| パラメータ名を推測 | `get_parameter_schema(include_description_ja=True)` で事前確認 |

### subprocess の stdin 問題（houdini-docs-mcp）

| 問題 | 原因 | 解決策 |
|------|------|-------|
| 180秒ハングアウト | 子プロセスが MCP の stdin を継承 | `stdin=subprocess.DEVNULL` を指定 |
| Windows で asyncio エラー | ProactorEventLoop のパイプ問題 | `asyncio.to_thread(subprocess.run)` を使う |

---

## 11. テストインフラ

### テストファイル一覧

| ファイル | テスト数 | 内容 |
|---------|--------|------|
| `test_tools.py` | ~400+ | 全ツールの単体テスト（モック Houdini） |
| `test_workflow.py` | ~25 | ensure/read/plan/record/validate/update のライフサイクル |
| `test_workflow_models.py` | ~15 | StageStatus, StageState, BuildState の単体テスト |
| `test_gate_state.py` | 9 | GateState モデルのシリアライズ・ファイルI/O |
| `test_diff_approval.py` | 10 | propose_diff / approve_diff ワークフロー |
| `test_safe_set_parameter.py` | 22 | _validate_value の型/menu/range チェック |
| `test_hooks.py` | 16 | Hook スクリプトのサブプロセステスト |
| `test_help_local.py` | ~30 | 日本語ヘルプパーサー |
| `test_wiring_integration.py` | ~6 | ノード接続の統合テスト |
| `conftest.py` | — | 860行のモック基盤（MockHouNode, MockHouModule, MockGeometry 等） |
| `integration/` | ~15 | Houdini 接続必要な統合テスト（CI ではスキップ） |

**合計: 590 passed, 34 skipped（Houdini 接続必要な統合テスト）**

### テスト実行

```bash
# 全テスト
pytest tests/ -v

# ワークフロー関連のみ
pytest tests/test_gate_state.py tests/test_diff_approval.py tests/test_safe_set_parameter.py tests/test_hooks.py -v

# 統合テスト（Houdini 接続必要）
pytest tests/integration/ -v
```

### Hook テストの仕組み

Hook テストは `subprocess.run()` でスクリプトを起動し、stdin に JSON を渡して stdout/returncode を検証:

```python
def _run_gate_hook(tool_name, tool_input, gate_state, tmp_path):
    # 1. tmp_path/.agent/gate_state.json に gate_state を書き込み
    # 2. stdin に {"tool_name": ..., "tool_input": ..., "cwd": tmp_path} を渡す
    # 3. subprocess.run([sys.executable, str(GATE_SCRIPT)], input=stdin_data)
    # 4. stdout の JSON をパースして permissionDecision を確認
```

---

## 12. 設定ファイル一覧

### `~/.claude.json`（MCP サーバー設定）

```json
{
  "mcpServers": {
    "houdini-mcp": {
      "type": "stdio",
      "command": "cmd",
      "args": ["/c", "python", "-m", "houdini_mcp"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "HOUDINI_HOST": "localhost",
        "HOUDINI_PORT": "18811",
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    },
    "houdini-docs-mcp": {
      "type": "stdio",
      "command": "cmd",
      "args": ["/c", "python", "-m", "houdini_docs_mcp"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "FASTMCP_LOG_LEVEL": "ERROR",
        "HOUDINI_NOTEBOOKLM_URL": "https://notebooklm.google.com/notebook/..."
      }
    },
    "api-docs": {
      "command": "node",
      "args": ["C:\\Users\\LAB_005\\api-docs-mcp-server\\dist\\index.js"]
    }
  }
}
```

### `~/.claude/settings.json`（Hook 設定 + プラグイン）

> **注意**: Hook の設定場所は Claude Code のバージョンや環境によって異なる場合がある。
> 以下は 2026-03 時点の構成例。最新の仕様は `claude config` や
> [Claude Code 公式ドキュメント](https://docs.anthropic.com/en/docs/claude-code) を参照。

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "mcp__houdini-mcp__(create_node|delete_node|...41ツール...)",
      "hooks": [{"type": "command", "command": "python ~/.claude/hooks/houdini_gate.py"}]
    }],
    "PostToolUse": [{
      "matcher": "mcp__houdini-mcp__.*",
      "hooks": [{"type": "command", "command": "python ~/.claude/hooks/houdini_post_tool.py"}]
    }]
  }
}
```

### `pyproject.toml`（プロジェクト設定）

```toml
[project]
name = "houdini-mcp"
version = "1.0.0"
requires-python = ">=3.10"

[project.dependencies]
fastmcp = ">=0.1.0"
rpyc = ">=5.0.0,<6.0.0"    # v5.x 必須
python-dotenv = ">=1.0.0"
requests = ">=2.28.0"
beautifulsoup4 = ">=4.11.0"
PyYAML = ">=6.0"
```

---

## 13. プロジェクト構造

```
~/dev-setup/houdini-mcp-analysis/           ← メインプロジェクト
├── houdini_mcp/
│   ├── __init__.py                         # version = "0.1.0"
│   ├── __main__.py                         # python -m houdini_mcp エントリポイント
│   ├── server.py                           # FastMCP サーバー（69 @mcp.tool 登録）
│   ├── connection.py                       # RPyC 接続管理（リトライ、タイムアウト）
│   └── tools/
│       ├── __init__.py                     # 全ツールの re-export（60+関数）
│       ├── _common.py                      # 共通ユーティリティ（デコレータ、安全チェック、シリアライズ）
│       ├── nodes.py                        # ノード操作（create, get, delete, list, find）
│       ├── parameters.py                   # パラメータ操作（get_schema, set_parameter）
│       ├── scene.py                        # シーン管理（get_info, save, load, new, serialize）
│       ├── geometry.py                     # ジオメトリ（get_geo_summary）
│       ├── wiring.py                       # 接続操作（connect, disconnect, flags, reorder）
│       ├── layout.py                       # レイアウト（layout_children, color, position, network_box）
│       ├── code.py                         # コード実行（execute_code + 安全チェック）
│       ├── rendering.py                    # レンダリング（viewport, quad_view, ROP）
│       ├── materials.py                    # マテリアル（create, assign, get_info）
│       ├── crowd.py                        # クラウドシム（17ツール、90KB 最大ファイル）
│       ├── errors.py                       # エラー検出（find_error_nodes）
│       ├── help.py                         # SideFX 公式ヘルプ（Web スクレイピング）
│       ├── help_local.py                   # ★ ローカル日本語ヘルプパーサー（11,040件）
│       ├── cache.py                        # インメモリキャッシュ（NodeTypeCache, TTL 対応）
│       ├── summarization.py                # AI 要約（Claude API 経由）
│       ├── pane_screenshot.py              # ペインキャプチャ（PySide2/Qt 経由）
│       ├── hscript.py                      # 高速バッチ操作（hscript 経由、100-800x 高速化）
│       └── workflow/                       # ★ ワークフロー管理（9ツール）
│           ├── __init__.py                 # エクスポート
│           ├── models.py                   # BuildState, StageState, StageStatus, 遷移ルール
│           ├── templates.py                # spec.md / stages.yaml テンプレート
│           ├── _common.py                  # 共通ヘルパー（readiness, gate_state I/O）
│           ├── gate_state.py               # ★ GateState モデル（Hook↔MCP 橋渡し）
│           ├── ensure_project.py           # プロジェクト bootstrap
│           ├── read_project.py             # 設定読み取り
│           ├── plan_stage.py               # ステージ計画
│           ├── record_scan.py              # スキャン記録
│           ├── validate_stage.py           # 検証（4チェック）
│           ├── update_stage.py             # ステータス遷移
│           ├── propose_diff.py             # ★ 差分提案
│           ├── approve_diff.py             # ★ 差分承認
│           └── safe_set_parameter.py       # ★ スキーマ検証付きパラメータ設定
├── CLAUDE.md                               # ★ 運用規約 v3（9ルール + 15ステップチェックリスト）
├── tests/                                  # 590テスト（全パス）
│   ├── conftest.py                         # 860行のモック基盤
│   ├── test_tools.py                       # ツール単体テスト（最大ファイル）
│   ├── test_workflow.py                    # ワークフローライフサイクル
│   ├── test_workflow_models.py             # モデル単体テスト
│   ├── test_gate_state.py                  # GateState テスト
│   ├── test_diff_approval.py               # 差分承認テスト
│   ├── test_safe_set_parameter.py          # 値検証テスト
│   ├── test_hooks.py                       # Hook サブプロセステスト
│   ├── test_help_local.py                  # 日本語ヘルプテスト
│   ├── test_wiring_integration.py          # 接続統合テスト
│   └── integration/                        # Houdini 接続必要な統合テスト
├── houdini_plugin/                         # Houdini 側プラグイン
│   ├── houdini_mcp.json                    # パッケージ設定
│   ├── toolbar/houdini_mcp.shelf           # ツールバー
│   └── python/houdini_mcp_plugin/          # Houdini 側 Python モジュール
├── pyproject.toml                          # プロジェクト設定
└── docs/                                   # ドキュメント

~/.claude/hooks/                            ← ユーザーグローバル Hook
├── houdini_gate.py                         # ★ PreToolUse Hook（6チェック）
└── houdini_post_tool.py                    # ★ PostToolUse Hook（エラー追跡）

~/dev-setup/houdini-docs-mcp/              ← NotebookLM 連携サーバー
└── houdini_docs_mcp/server.py              # query_houdini_docs（1ツール）

~/api-docs-mcp-server/                     ← VEX/CLO3D/Rhino API 検索サーバー
├── dist/index.js                           # コンパイル済みサーバー
└── docs/houdini-vex/index.json             # VEX ドキュメント（2.9MB）
```

---

## チェックリスト対応表

| チェック項目 | 該当セクション |
|-------------|-------------|
| A. 全ツール網羅（カテゴリ別） | §3 全69ツール一覧 |
| A. workflow 9ツールの仕様・返り値・状態遷移・ファイル | §6 全詳細 |
| A. ローカル日本語ヘルプ統合 | §5 |
| A. 別MCP（houdini-docs-mcp, api-docs/VEX） | §4 |
| B. Q1/Q2 入口フロー | §8 RULE 1 |
| B. 補助モード②（差分案→OK→差分だけ実行） | §8 RULE 3-B + §9 実運用フロー |
| B. 無造作依頼でも強制するルール | §7 アーキテクチャ強制層 + §8 |
| C. 引数名ミス表 | §10 + §8 RULE 5 |
| C. render系 token 超過回避 | §3F + §10 |
| C. execute_code の位置づけ | §3E + §8 RULE 3-B |
| D. .agent/ ファイルの役割 | §6 正本ファイルの役割 |
| D. readiness gate の判定条件 | §6 Readiness Gate |
| D. stage の状態遷移 | §6 状態遷移ルール |
| E. パラメータ設定前の schema 確認 | §3C safe_set_parameter + §8 RULE 6 |
| E. 迷いトリガーの docs 参照順序 | §8 RULE 7 |

---

## ガイドの整合性検証手順

このガイドの数値・分類が正確であることを確認するためのコマンド:

### 1. server.py ツール登録数の確認

```bash
cd ~/dev-setup/houdini-mcp-analysis
grep -cP '@mcp\.tool\(\)' houdini_mcp/server.py
# 期待値: 69
```

### 2. ツール名の完全一覧抽出

```bash
grep -oP '@mcp\.tool\(\)\s*\n(?:async\s+)?def\s+\K\w+' houdini_mcp/server.py | sort
# このガイドの §3 ツールテーブル + §3C ワークフロー一覧と一致すること
```

### 3. Hook matcher と変更系ツール一覧の diff

```python
import re, json
with open('houdini_mcp/server.py') as f:
    server = set(re.findall(r'@mcp\.tool\(\)\s*\n(?:async\s+)?def\s+(\w+)', f.read()))
with open('C:/Users/LAB_005/.claude/settings.json') as f:
    s = json.load(f)
matcher = s['hooks']['PreToolUse'][0]['matcher']
hook = set(re.search(r'\(([^)]+)\)', matcher).group(1).split('|'))
workflow = {'ensure_project','read_project','plan_stage','record_scan',
            'validate_stage','update_stage','propose_diff','approve_diff','safe_set_parameter'}
read_only = server - hook - workflow
print(f"read_only={len(read_only)}, hook={len(hook)}, workflow={len(workflow)}, total={len(server)}")
uncategorized = server - hook - workflow - read_only
if uncategorized: print(f"UNCATEGORIZED: {uncategorized}")
# 期待値: read_only=26, hook=34, workflow=9, total=69
```

### 4. 分類の漏れチェック

上記スクリプトで `UNCATEGORIZED` が出たら、CLAUDE.md の読み取り専用リストか Hook matcher に追加が必要。
