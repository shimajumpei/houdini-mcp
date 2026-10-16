---
name: crowd-setup
description: "Build a Houdini crowd simulation step by step with houdini-mcp tools, from the agent definition and crowd source to the DOP network, behaviors, options, and validation."
---

# Houdini Crowd Simulation Setup

ユーザーがHoudiniでクラウドシミュレーションを構築するのを支援するスキルです。
houdini-mcp MCPサーバーのツールを使用して、エージェント定義からDOPシミュレーションまでを段階的に構築します。

## 前提条件
- Houdiniが起動済みで、hrpycサーバーが接続可能（デフォルト: localhost:18811）
- houdini-mcp MCPサーバーが利用可能

## 実行手順

以下の順序でhoudini-mcpツールを呼び出してください：

### Step 1: シーン状態確認
1. `get_scene_info` でHoudiniの接続確認とシーン状態を取得
2. `get_crowd_info` で既存のクラウドセットアップがあるか確認

### Step 2: エージェント定義
ユーザーにFBXファイルパスやキャラクター情報を確認し、以下を実行：
1. `setup_crowd_agent` でエージェント定義チェーン（Agent SOP → AgentPrep → AgentDefinitionCache）を構築
   - `agent_name`: エージェント名
   - `clip_paths`: FBXまたは.clipファイルのパスリスト
   - `parent_path`: 配置先（デフォルト: /obj）
2. 結果のノードパスを記録

### Step 3: クラウドソース
1. `setup_crowd_source` でエージェントの散布を設定
   - `parent_path`: 配置先
   - `agent_setup_path`: Step 2のエージェント定義パス
   - `source_type`: scatter（デフォルト）、formation 等
   - `count`: エージェント数

### Step 4: DOPシミュレーション
1. `setup_crowd_dop` でDOPネットワーク（crowdobject + crowdsolver + groundplane）を構築
   - `crowd_source_path`: Step 3のソースパス

### Step 5: ビヘイビア追加
ユーザーの要望に応じて：
1. `add_crowd_behavior` でステアリング行動を追加
   - 対応タイプ: wander, seek, avoid, follow_path, separate, cohesion, alignment
2. `add_crowd_state` でステートを追加
3. `add_crowd_transition` でステート遷移を追加

### Step 6: オプション
- `setup_crowd_terrain` - 地形アダプテーション
- `setup_crowd_formation` - フォーメーション配置
- `add_crowd_layer` - アニメーションレイヤ
- `configure_foot_planting` - 足のプランティング
- `add_crowd_look_at` - ルックアット制御

### Step 7: 検証
1. `validate_crowd_setup` でセットアップの問題を検出
2. `render_viewport` でビューポートレンダリングして結果を確認
3. `save_scene` でシーンを保存

## 重要な注意事項

### クリップアニメーション再生
- Agent SOPのデフォルト出力では `agentcliplayerweights=(0.0,)` のためアニメーションが再生されない
- SOPレベルでアニメーションを適用するには、VEX `setagentclips()` 関数が必要
- `setagentclips` は `setagentclipnames` / `setagentcliptimes` / `setagentclipweights` と異なり、**layer_weights も設定できる唯一のVEX関数**
- DOP crowdsolverは自動的にlayer weightsを管理するが、SOPレベルでは手動で設定が必要

### setagentclips() の呼び出し例（Prim VEX Wrangle）
```vex
float t = (@Frame - 1) / 24.0;
float clip_len = agentcliplength(0, @primnum, "clipname");
float clip_time = t % clip_len;

string names[]; push(names, "clipname");
float times[]; push(times, clip_time);
float weights[]; push(weights, 1.0);
string groups[]; push(groups, "");
int layer_ids[]; push(layer_ids, 0);
int blend_modes[]; push(blend_modes, 0);  // BLEND_INTERPOLATE
float layer_weights[]; push(layer_weights, 1.0);
int parent_ids[]; push(parent_ids, -1);

setagentclips(0, @primnum, names, times, weights,
    groups, layer_ids, blend_modes, layer_weights, parent_ids);
```

### バージョン付きノードタイプ（Houdini 20.5）
- `agentprep::3.0`, `crowdsolver::3.0`, `crowdstate::3.0`
- `crowdtrigger::2.0`, `crowdtransition::3.0`, `crowdsource::3.0`
- `agentclip::2.0`

### crowdsolver::3.0 入力構造
| Input Index | Label |
|-------------|-------|
| 0 | Crowd Object |
| 1 | Crowd Source |
| 2 | States |
| 3 | Transitions |
| 4 | Control Objects |
| 5 | Collision Objects |

### VEXの注意点
- `fmod()` は存在しない → `%` 演算子を使用
- 文字列配列リテラル `{"name"}` はエラー → `push(arr, "name")` を使用
- `setprimintrinsic` は `agentcliplayerweights` に使えない（read-only）

ユーザーの指示: $ARGUMENTS
