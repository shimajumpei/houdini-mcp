# Houdini MCP Server — プロダクションレベル分析レポート

> 対象: `houdini-mcp` (oculairmedia/houdini-mcp)
> 分析日: 2026-02-21
> 対象ファイル: `rendering.py`, `pane_screenshot.py`, `crowd.py`, `server.py`, `__init__.py`
> Houdini: 20.5.759 / RPyC経由

---

## 1. 現状サマリー

### 完了済み
- ✅ RPyC画像転送バグ修正（6関数すべて動作確認済み）
- ✅ Crowd Helper Tools 8個（全ツールHoudiniライブテスト済み）
- ✅ Houdini 20.5バージョン付きノードタイプ対応（`startswith()`修正 x7箇所）
- ✅ ユニットテスト 385 passed, 0 failed

### 未解決
- 🔴 P0級バグ 5件（crowd.py 3件 + rendering.py 2件）
- 🟠 P1級問題 7件
- 🟡 P2級問題 7件
- ⚪ P3級問題 6件
- 🔵 Crowdワークフロー機能ギャップ（参考ドキュメントとの比較）

---

## 2. P0: クリティカルバグ（必ず修正）

### P0-1: `add_crowd_behavior` — ビヘイビアノードの配線が逆
**ファイル**: `crowd.py` L519-529
**影響**: エージェントがステアリングに全く反応しない

```python
# 現状（壊れている）
behavior_node.setInput(0, crowd_solver)  # ソルバ→ビヘイビアに配線 = 逆

# 正しい配線
crowd_solver.setInput(next_input, behavior_node)  # ビヘイビア→ソルバ入力
```

POP Steerノードはcrowd solverの**入力**として接続すべき。現状では`next_input`を計算しているのに使っていないデッドコード。

### P0-2: `setup_crowd_dop` — `soppath`にgeoコンテナパスを設定
**ファイル**: `crowd.py` L387-394
**影響**: DOP群集オブジェクトがエージェントデータを見つけられない

```python
# 現状（壊れている）
crowd_obj.parm("soppath").set(geo_parent.path())  # /obj/crowd_sim = 親コンテナ

# 正解
crowd_obj.parm("soppath").set(crowd_source_path)  # SOPノードの直パス
```

`crowdobject` DOPの`soppath`はCrowd Source SOPノードへのパスが必要。

### P0-3: 部分作成ノードのクリーンアップなし
**ファイル**: `crowd.py` 複数箇所 (`setup_crowd_agent`, `setup_crowd_dop`)
**影響**: RPyC接続断時にオーファンノードがシーンに残留 → リトライでゴミ蓄積

`nodes_created`リストが存在するが、エラー時のロールバックに使われていない。

### P0-4: `render_viewport` — `rop`変数未初期化で`finally`が`NameError`
**ファイル**: `rendering.py` L310-312, L374-400
**影響**: `/out`コンテキストが見つからない場合、`finally`で`NameError`→一時ファイルもリーク

```python
# L311の早期return後、L397でrop参照 → NameError
if rop is not None:  # rop未定義
    rop.destroy()
```

### P0-5: `render_viewport` — TOCTOU（Time of Check, Time of Use）
**ファイル**: `rendering.py` L357-372
**影響**: `path.exists()`とファイル読み込みの間にファイルが消えると未処理例外

---

## 3. P1: 重要な問題（リリース前に修正推奨）

### P1-1: `add_crowd_transition` — 入力スロットインデックス計算が不確実
**ファイル**: `crowd.py` L665-674
`node.inputs()`はNone埋めのタプルを返す → `len()`が正しいインデックスにならない場合がある

### P1-2: `setup_crowd_source` — object_mergeの`objpath1`にSOPパスを渡している
**ファイル**: `crowd.py` L297, L307
`object_merge`は**OBJレベル**のパスを期待。SOPパスでエラー。

### P1-3: `settings`/`trigger_settings`辞書のバリデーションなし
**ファイル**: `crowd.py` L532-541
型チェック・サニタイズなしで`parm.set()`に渡している。Houdiniエクスプレッションインジェクションのリスク。

### P1-4: `get_crowd_info` — `agentterraiprojection` タイポ
**ファイル**: `crowd.py` L812
正しくは `agentterrainprojection`（'n' が欠落）

### P1-5: `rendering.py` — mkstemp fdのclose失敗でリーク
**ファイル**: `rendering.py` L305-306
`remote_os.close(fd)`に`try/except`なし。

### P1-6: `rendering.py` — 4Kレンダーのサイズガードなし
**ファイル**: `rendering.py` (`_read_remote_file_as_base64`)
4096x4096で~85MBのbase64文字列がメモリに載る。上限チェックなし。

### P1-7: `_read_remote_file_as_base64` — 3回のRPCラウンドトリップ
**ファイル**: `rendering.py` L62-64
open/read → b64encode → decode の3段階。接続断で中間状態に。

---

## 4. P2: 中程度の問題

| ID | ファイル | 問題 |
|----|----------|------|
| P2-1 | `crowd.py` L733-756 | `agentterrainadaptation`が未配線（入力なし） |
| P2-2 | `crowd.py` L284-286 | 散布グリッド50x50固定（パラメータ化なし） |
| P2-3 | `crowd.py` L824-892 | `get_crowd_info`再帰スキャンがRPyC非効率（ノード毎にRPC） |
| P2-4 | `pane_screenshot.py` L274 | `QByteArray.data()` PySide2バージョン依存 |
| P2-5 | `pane_screenshot.py` L257-273 | `buffer.close()`後に`buffer.data()` — Qtバージョンでデータ消失の可能性 |
| P2-6 | `rendering.py` L397 | `rop = None`初期化漏れ（P0-4と同根） |
| P2-7 | `rendering.py` L673 | `render_quad_view`が全ビュー失敗でも`status: "success"` |

---

## 5. P3: 低優先度

| ID | 問題 |
|----|------|
| P3-1 | `TRIGGER_TYPE_MAP`で`"speed"`と`"attribute"`が同じノードタイプにマップ |
| P3-2 | ビヘイビアデフォルト名の重複（`steer_wander` → Houdini自動リネーム通知なし） |
| P3-3 | モジュールdocstringの分類ミス |
| P3-4 | TOCTOU未カプセル化 |
| P3-5 | `len(qbyte_array)`の不要RPCラウンドトリップ |
| P3-6 | `render_quad_view`のステータス不整合 |

---

## 6. 参考ドキュメントとの機能ギャップ分析

ユーザーの`motion_human_crowd/docs/houdini_crowd_knowledge/`（27ファイル）との比較。

### 6.1 現在のツールがカバーする範囲

| ワークフロー段階 | ドキュメント章 | ツール | カバー度 |
|------------------|---------------|--------|----------|
| Agent作成 | 02_agent_setup | `setup_crowd_agent` | ⚠️ 部分的 |
| クリップ設定 | 03_animation_clips | `setup_crowd_agent`内 | ⚠️ 基本のみ |
| Crowd配置 | 05_dop_simulation §1 | `setup_crowd_source` | ⚠️ Random/Scatterのみ |
| DOPネットワーク | 05_dop_simulation §2 | `setup_crowd_dop` | ✅ 基本構造 |
| Crowd State | 05_dop_simulation §3 | ❌ なし | 🔴 |
| ステアリング | 06_steering_behaviors | `add_crowd_behavior` | ⚠️ 8種対応 |
| トリガー | 07_triggers_transitions | `add_crowd_transition` | ⚠️ 基本のみ |
| 地形適応 | 09_terrain_footplanting | `setup_crowd_terrain` | ⚠️ 不完全 |
| Motion Paths (H20+) | 08_motion_paths_sop | ❌ なし | 🔴 |
| ラグドール | 10_ragdoll_physics | ❌ なし | 🔴 |
| Solaris/USD | 11_solaris_usd_rendering | ❌ なし | 🔴 |
| VEXカスタマイズ | 12_vex_customization | ❌ なし | ⚠️ 汎用ツールで対応可 |
| Clip Transition Graph | 07 §5 | ❌ なし | 🟡 |
| Diversity/Layer | 04_diversity_layers | ❌ なし | 🟡 |
| Foot Planting | 09 §3 | ❌ なし | 🟡 |
| Weight設定 | 06 §2 | ❌ なし | 🟡 |

### 6.2 重要な機能ギャップ

#### 🔴 Crowd State管理が完全に欠落
ドキュメント05章によると、DOPワークフローの核心は**Crowd State DOP**。walk/idle/runなどのステートを定義し、Mergeしてsolverの第3入力に接続する。現在のツールにはState作成機能がない。

**影響**: ステアリングビヘイビアを追加しても、Stateが定義されていないとエージェントはアニメーションしない。

#### 🔴 Motion Paths（SOP）ワークフロー非対応
Houdini 20+の新機能。DOP不要で高速イテレーション。ドキュメントの08章に詳細あり。
- `Crowd MotionPath` → `MotionPath Edit/Follow/Avoid` → `MotionPath Trigger/Transition` → `MotionPath Evaluate`

#### 🔴 ラグドール/物理連携なし
DOPワークフローのハイライト機能。Bullet Solver連携、Agent Collision Layer、Agent Configure Joints。

#### 🟡 Formation配置非対応
`setup_crowd_source`はRandom/Scatterのみ。ドキュメントではFormation（行列配置）も記載。

#### 🟡 Clip Transition Graph非対応
クリップ間の最適遷移を自動計算する機能。`add_crowd_transition`がUse Clip Transition Graphを設定できない。

#### 🟡 Agent Diversity / Layer非対応
プロップ・衣服バリエーション。Agent Layer SOPやCrowd Assign Layersが未サポート。

---

## 7. コード品質・アーキテクチャ

### 良い点 ✅
- `handle_connection_errors`デコレータの一貫した使用
- 既存ツールパターン（`ensure_connected`, `Dict[str,Any]`返却）との統一
- 詳細なdocstring
- Houdini 20.5バージョンノード対応（`startswith()`）
- ユニットテスト385件全パス

### 改善すべき点 ❌
- **エラーリカバリ**: 部分作成ノードのロールバック機構なし
- **RPyC効率**: `get_crowd_info`がノード毎にRPC（`_serialize_scene_state_fast`のhscriptバッチ手法を使うべき）
- **入力バリデーション**: `settings`辞書のサニタイズ不足
- **DOP配線の正確性**: Houdiniの接続セマンティクスの誤解（P0-1, P0-2）
- **リソースクリーンアップ**: `finally`ブロックの不完全さ

---

## 8. 修正優先度ロードマップ

### Phase 1: P0修正（必須 — ブロッカー）
1. `add_crowd_behavior`の配線方向修正
2. `setup_crowd_dop`の`soppath`修正
3. 部分作成ノードのロールバック機構追加
4. `render_viewport`の`rop = None`初期化 + `/out`チェック移動
5. `_read_remote_file_as_base64`のエラーハンドリング強化

### Phase 2: P1修正（リリース前）
1. 入力スロットインデックス計算の修正
2. `object_merge`パス修正
3. `settings`辞書バリデーション追加
4. タイポ修正（agentterrainprojection）
5. mkstemp fdリーク対策
6. サイズガード追加
7. RPCラウンドトリップ削減

### Phase 3: P2修正 + 機能追加
1. `agentterrainadaptation`配線修正
2. スキャングリッドパラメータ化
3. `get_crowd_info`バッチ化
4. `QByteArray.data()`堅牢化
5. **Crowd State管理ツール追加**（`add_crowd_state`）

### Phase 4: 機能拡張（次期リリース）
1. Motion Pathsワークフロー対応
2. Formation配置対応
3. Clip Transition Graph対応
4. Agent Diversity/Layer対応
5. ラグドール/物理連携

---

## 9. テスト要件

### 既存テスト
- 385件パス（crowd含む）
- ライブHoudiniテスト: 画像転送6関数 + crowd 8関数

### 追加テスト要件
- P0修正後の配線確認（Houdiniでノード接続方向を検証）
- `soppath`修正後のDOPシミュレーション動作確認
- ロールバック機構のRPyC切断テスト
- `render_viewport`のエッジケース（/out不在、大サイズファイル）
- 4K解像度でのサイズガード動作確認

---

## 10. 結論

**現状はプロダクションレベルではない。**

P0バグ5件は群集シミュレーションの基本機能を壊す（ビヘイビアが効かない、DOPがデータを見つけられない）。
ただし基盤アーキテクチャは堅実で、修正は局所的。Phase 1〜2を完了すれば基本機能はプロダクション品質に到達する。

参考ドキュメントが示す「完全なCrowdパイプライン」対比では、Crowd State管理の欠落が最大のギャップ。
Motion Paths対応は次期拡張として計画的に進めるべき。
