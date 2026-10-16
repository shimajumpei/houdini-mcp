---
name: houdini-exec
description: "Run Python code inside the live Houdini session with the houdini-mcp execute_code tool. Use when the user wants to script, query, or edit Houdini with hou-module code."
---

# Houdini Code Execution

Houdini内でPythonコードを実行するスキルです。

## 実行方法

`execute_code` ツールを使用してHoudini内でPythonコードを実行します。

### 制約事項
- `hou` モジュールは自動注入される（`import hou` は不要・禁止）
- `open()` は使用不可（安全なbuiltinsのみ）
- `hou.hipFile.clear()` はブロックされる（`new_scene`ツールを使用）
- `allow_dangerous=true` を設定すると制限が緩和されるが慎重に

### よく使うパターン

**ノード情報取得:**
```python
node = hou.node("/obj/geo1")
print(node.type().name())
for c in node.children():
    print(c.name(), c.type().name())
```

**パラメータ操作:**
```python
node = hou.node("/obj/geo1/sphere1")
node.parm("rad").set(2.0)
# Expression設定
node.parm("tx").setExpression("sin($T * 360)")
```

**ジオメトリアクセス:**
```python
geo = hou.node("/obj/geo1/OUT").geometry()
print("Points:", len(geo.points()))
print("Prims:", len(geo.prims()))
```

ユーザーの指示: $ARGUMENTS
