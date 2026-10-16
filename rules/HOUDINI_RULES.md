# Houdini MCP — 絶対遵守ルール v3

**このファイルのルールは全て MUST（絶対遵守）である。**
「知らなかった」「忘れた」「ユーザーが言わなかった」は免責にならない。
Houdini 関連の MCP ツールを 1 つでも呼ぶ前に、このファイルのルールを全て適用すること。

---

## RULE 0: Houdini ツール呼び出しの絶対条件

以下の Houdini MCP ツール（**変更系ツール**）を呼んでよいのは、RULE 1〜3 を**全て**満たした後だけである:

`create_node` / `delete_node` / `set_parameter` / `connect_nodes` / `disconnect_node_input` /
`reorder_inputs` / `set_node_flags` / `execute_code` / `create_material` / `assign_material` /
`create_render_node` / `set_render_settings` / `setup_crowd_*` / `add_crowd_*` / `configure_foot_planting` /
`new_scene` / `save_scene` / `load_scene` / `set_node_color` / `set_node_position` /
`layout_children` / `create_network_box`

変更系ツールを呼ぶ前に、以下が**全て完了**していなければならない:

1. RULE 1 の入口プロトコル（Q1/Q2）が確定済み
2. RULE 2 の workflow 初期化が完了済み（`ensure_project` + `read_project` 実行済み、`ready_for_implementation == true`）
3. RULE 3-A の `plan_stage` が実行済み（active_stage が in_progress）
4. RULE 3-B の `propose_diff` → ユーザー確認 → `approve_diff` が完了している
5. RULE 6 のパラメータ事前確認が完了している（set_parameter を呼ぶ場合）

**これらの条件は PreToolUse Hook（`houdini_gate.py`）で機械的に強制される。**
**条件未達の変更系ツール呼び出しは自動的にブロックされる。**

**1つでも欠けていたら変更系ツールを呼んではならない。**

**読み取り専用ツール**（以下）は、RULE 2 の途中（現状把握フェーズ）でも呼んでよい:

`get_scene_info` / `serialize_scene` / `get_node_info` / `list_children` / `find_nodes` /
`get_geo_summary` / `get_parameter_schema` / `list_node_types` / `ping_houdini` / `check_connection` / `doctor` /
`render_viewport` / `render_quad_view` / `capture_pane_screenshot` / `list_visible_panes` /
`capture_multiple_panes` / `render_node_network` / `find_error_nodes` / `get_houdini_help` /
`manage_cache` / `get_summarization_status` / `get_render_settings` / `list_render_nodes` /
`get_material_info` / `get_crowd_info` / `validate_crowd_setup` / `get_last_scene_diff`

---

## RULE 1: 入口プロトコル（最初に必ず実行）

Houdini 関連の話題が出たら、**何よりも先に**以下の 2 問をユーザーに質問する。

### Q1: プロジェクト状況（必須）

> 今回はどちらですか？
> A) これから新規で作る
> B) 既存シーン／既存ネットワークを進める

### Q2: 進め方のスタイル（必須）

> 進め方はどちら寄りですか？
> 1) 補助モード — 基本は自分が作業。あなたは「差分案→確認→差分だけ実行」
> 2) 任せる寄り — 基本はあなたが進める。自分が時々レビュー

- Q1/Q2 **両方が確定するまで** workflow ツールも Houdini ツールも呼ばない
- ユーザーがスタイルを示唆済みの場合でも Q1 は必ず明示確認する

---

## RULE 2: Workflow 初期化（Q1/Q2 確定後に必ず実行）

Q1/Q2 が確定したら、**Houdini 操作に入る前に**以下を順番に実行する。

### Step 1: プロジェクト確認
```
ensure_project(project_root?)
```

### Step 2: 全体読み取り
```
read_project(target="all", project_root?)
```

### Step 3: readiness 確認

- `ready_for_implementation == true` → RULE 3 へ進む
- `ready_for_implementation == false` → spec/stages を整備する（以下参照）

### spec/stages の整備手順

1. **Q1=B（既存）の場合のみ**: 先に `get_scene_info()` → `record_scan()` で現状を把握
2. ユーザーに聞く:
   - 今のゴール（1〜2行）
   - どこで詰まっているか / 何を変えたいか
   - 触ってよい範囲（禁止領域があれば）
3. Claude が `spec.md` と `stages.yaml` の下書きを提案する
4. **ユーザーの合意を得てから**書き込む
5. 再度 `ensure_project()` で `ready_for_implementation == true` を確認
6. **true になるまで変更系ツールを呼ばない**

---

## RULE 3: Stage 運用（実装サイクル）

readiness が true になったら、以下のサイクルを **必ず** 守る。

### 3-A: 実装前 — plan 必須

変更系ツールを呼ぶ前に:

1. `plan_stage(stage_id, description)` を実行する
2. active_stage が in_progress であることを確認する
3. **plan_stage なしで変更系ツールを呼んではならない**

### 3-B: 実装中 — 差分確認必須（propose_diff → approve_diff で機械的に担保）

**全ての変更について、実行前にユーザーへ差分案を提示し、OK を得てから実行する。**
**差分承認は `propose_diff` → `approve_diff` ツールで機械的にロックされる。**
**承認なしの変更系ツール呼び出しは PreToolUse Hook でブロックされる。**

差分案の提示手順:
```
1. propose_diff(stage_id, diff_spec) を呼ぶ → diff_id を取得
2. ユーザーに差分案を提示（下記フォーマット）
3. ユーザーが OK したら approve_diff(diff_id) を呼ぶ
4. 変更系ツールが解禁される
5. 実行が完了したら record_scan → validate_stage へ
```

差分案のフォーマット:
```
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

**Q2=1（補助モード）**: 1 操作ごとに propose_diff → 提示 → approve_diff。
**Q2=2（任せる寄り）**: stage 単位でまとめて propose_diff → 提示 → approve_diff。

**ロールバック**: `approve_diff` 実行時に `.agent/checkpoints/{diff_id}_*.hip` へ
シーンのチェックポイントが自動保存される（Houdini 接続時のみ）。
変更を巻き戻したい場合は該当チェックポイントを `load_scene` で読み込む。

**`execute_code` の使用制限:**
- `execute_code` は「既存の個別ツールでは実現できない操作」にのみ使う
- `create_node` + `set_parameter` + `connect_nodes` で実現できる操作に `execute_code` を使わない
- `execute_code` を使う場合も propose_diff → approve_diff の手順は必須

### 3-C: 実装後 — scan / validate 必須

変更系ツールで変更を加えたら、**必ず**以下を実行する:

1. `get_scene_info()` または `serialize_scene()` でシーン状態を取得
2. `record_scan(scan_data=..., stage_id=...)` でスキャンを記録
3. `validate_stage(stage_id)` で検証
4. 検証 pass → `update_stage(stage_id, "validated")`
5. 完了確認 → `update_stage(stage_id, "completed")`
6. 検証 fail → 問題を修正し、再度 scan → validate

**scan/validate を飛ばして次の stage に進んではならない。**

---

## RULE 4: UI / Viewport の安全運用

### render_viewport
- **`resolution=[640, 480]` 以下を指定する**（token 超過防止。未指定時のデフォルトも 640x480）
- `render_viewport` に `save_path` 引数は**無い**。大きな画像をファイル保存したい場合は `capture_pane_screenshot(save_path=...)` を使う
- 640x480 を超える解像度で呼ぶことを禁止する

### capture_pane_screenshot
- 呼ぶ前に **`list_visible_panes()` で有効なペインタイプを確認する**
- `pane_type_name` を推測で指定しない

### render_quad_view
- `resolution=[480, 360]` 以下を指定する

---

## RULE 5: 引数の正確性（ツール呼び出し前チェック）

**以下の正しい引数名を必ず使う。誤った引数名で呼ぶことを禁止する。**

| ツール | 正しい引数 | 間違いやすい引数（使用禁止） |
|--------|-----------|--------------------------|
| `create_node` | `name` | ~~node_name~~ |
| `set_parameter` | `param_name` | ~~parameter_name~~ |
| `find_error_nodes` | `root_path` | ~~path~~ |
| `capture_pane_screenshot` | `pane_type_name` | ~~pane_type~~, ~~type~~ |
| `set_node_flags` | `display`, `render`, `bypass` | ~~visible~~, ~~flag~~ |
| `connect_nodes` | `src_path`, `dst_path`, `dst_input_index`, `src_output_index` | ~~source~~, ~~destination~~ |

**引数名に自信がない場合:**
ツールの定義（MCP のスキーマ）を確認してから呼ぶ。推測で呼ばない。

---

## RULE 6: パラメータ事前確認の義務（safe_set_parameter 推奨）

### 推奨: `safe_set_parameter` を使う

```
safe_set_parameter(node_path, param_name, value, diff_id)
```

`safe_set_parameter` は内部で自動的に:
1. `get_parameter_schema(include_description_ja=True)` を呼んで schema を取得
2. param_name の存在、型、menu_items、range を検証
3. 検証 pass 後に `set_parameter` を実行

**raw `set_parameter` は PreToolUse Hook でブロックされる**（get_parameter_schema を事前に呼んだ場合のみ通過）。

### raw `set_parameter` を使う場合（非推奨）

```
get_parameter_schema(node_path, include_description_ja=True)  → schema 確認
set_parameter(node_path, param_name, value)
```

1. schema に存在する `param_name` だけを渡す
2. schema に無い名前を推測で渡さない
3. 型が不明な場合、schema の `type` / `default_value` を確認してから値を決める
4. メニュー型パラメータは `menu_items` の値を確認してから設定する

### `create_node` 後のパラメータ設定

ノードを作成したら、パラメータを設定する前に必ず schema を確認:
```
create_node(node_type, parent_path, name)  → node_path を取得
safe_set_parameter(node_path, param_name, value, diff_id)  → schema 確認 + 設定を一括
```

---

## RULE 7: ドキュメント参照の義務（迷いトリガー）

### 以下の状況になったら、必ずドキュメントを参照する（MUST）:

1. **複雑な領域に入る直前** — OpenCL / Solver / Volume Wrangle / L-System / VEX / USD / KineFX / CHOP / PDG
2. **パラメータが見つからない** — `get_parameter_schema` で該当なし、または型/値が不明
3. **エラーが連続する** — 同じ操作で 2 回以上エラーが出た
4. **想定と違う挙動** — ノードが生成されない / 結果が見えない / 値が反映されない
5. **やり直しが 2 回以上発生** — 同じ stage 内で同じ操作を 2 回以上やり直した

### 参照の優先順位:

```
1st: get_parameter_schema(node_path, include_description_ja=True)  ← まずローカル日本語ヘルプ
2nd: get_node_info(node_path, include_description_ja=True)          ← ノード概要の日本語説明
3rd: query_houdini_docs(question)                                    ← NotebookLM（houdini-docs-mcp）で補足
4th: search_vex_function(query) / get_vex_details(function_name)    ← VEX 関連の場合
5th: get_houdini_help(help_type, item_name)                         ← SideFX 公式サイト
```

### 参照結果の反映:

ドキュメントを参照したら、その情報を**次の差分案に必ず反映する**。
「参照した」だけで満足せず、差分案の根拠として明示すること。

---

## RULE 8: 禁止事項

以下は **いかなる状況でも禁止** である:

1. RULE 1（Q1/Q2）未完了のまま Houdini ツールを呼ぶこと
2. `ready_for_implementation == false` のまま変更系ツールを呼ぶこと
3. `plan_stage` 未実行のまま変更系ツールを呼ぶこと
4. `propose_diff` → `approve_diff` なしで変更系ツールを呼ぶこと
5. ユーザー確認なしで変更系ツールを呼ぶこと（Q2=1 の場合）
6. `record_scan` / `validate_stage` を飛ばして stage を完了にすること
7. `build_state.json` / `gate_state.json` を手動編集すること
8. `render_viewport` を 640x480 を超える解像度で呼ぶこと
9. `get_parameter_schema` を確認せず raw `set_parameter` を呼ぶこと（`safe_set_parameter` 推奨）
10. パラメータ名・引数名を推測で渡すこと
11. 既存シーンに対していきなり作り直しを提案すること
12. `contracts.yaml` を使うこと（現時点では未使用）
13. `execute_code` を個別ツールで実現可能な操作に使うこと
14. ドキュメント参照トリガー（RULE 7）に該当する状況でドキュメントを引かないこと
15. エラー連続（2回以上）後にドキュメント参照せずリトライすること（Hook で強制）

---

## 正本ファイルの役割

| ファイル | 役割 | 状態 |
|----------|------|------|
| `.agent/spec.md` | 仕様（Goal / Requirements / I/O / Acceptance Criteria） | **必須** |
| `.agent/stages.yaml` | ステージ定義（id / name / purpose / depends_on / produces / consumes） | **必須** |
| `.agent/build_state.json` | 現在地メタデータのみ。手動編集禁止 | **自動管理** |
| `.agent/gate_state.json` | アーキテクチャ強制層の状態。手動編集禁止 | **自動管理** |
| `.agent/diffs/{diff_id}.json` | 差分提案の記録 | **自動管理** |
| `.agent/contracts.yaml` | 将来用。今は触らない | 未使用 |

---

## 状態遷移ルール

```
pending → in_progress → validated → completed
              ↓
            failed → in_progress（リトライ）
```

- `plan_stage` → `pending` を `in_progress` に遷移
- `update_stage("validated")` → 直前の `validate_stage` が pass 必須
- `completed` からは戻れない

---

## セッション開始時のチェックリスト

新しいセッションで Houdini の話題が出たら、**以下を上から順に、飛ばさず実行する**:

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

**このチェックリストの順番を飛ばしてはならない。**
**ステップ 8〜15 は stage ごとに繰り返す。**
