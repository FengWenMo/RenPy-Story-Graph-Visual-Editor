# Unity 接入（第一片，2026-10-19）

> 这一片只解决"**导出数据够不够驱动一个播放器**"里的两个缺口：结构上下文
> 与演出时间轴。它**不是**一个 Unity 播放器，也不替代 Ren'Py 的运行时。

## 导出

```powershell
python cli.py build <项目根> --engine unity --out <输出目录>
```

产物：

| 文件 | 用途 |
|---|---|
| `UnityDialogueData.json` | `JsonUtility.FromJson<DialogueData>` 直接读；扁平标量 + 数组 |
| `story_ir.json` | 引擎中立 IR（更完整，含 stage 快照/资源/时间轴） |
| `UNITY_IMPORT.md` | 生成给程序看的字段说明（含 C# 结构） |

## `StoryIr.cs`

`docs/unity/StoryIr.cs` 是与 `to_unity()` 一一对应的参考模型。重点字段：

- `structurePath` / `structureKind` / `structureKnown`
  —— 这一拍在哪个块里（`menu:1>B` / `option`）。`structureKnown=false`
  表示老表没有结构位，**不要**把它当顶层。
- `timeline[]` —— 作者写的顺序；`order` 从 0 递增。
  `op` 为 `scene` / `show` / `hide` / `with` / `music` / `sound` / `voice` /
  `at` / `transform`。`show` 的 `at`/`clause`/`transition` 都在事件上。
- `condition` —— 累积条件；Unity 侧自己求值（工具不猜语义）。
- `targets` —— `jump` / `call` 的 label 接线。

## 两条边界

1. **`background` / `poses` / `bgm` 是算好的舞台快照**：不要自己再叠
   `scene/show/hide`，否则会翻倍；要精确重放顺序时用 `timeline`。
2. **没有资源地址**：`UnityDialogueData.json` 的 assets 只有工具里的路径/ID，
   Unity 侧要按自己的 Addressables 规则映射（工具不知道你的资源目录布局）。
