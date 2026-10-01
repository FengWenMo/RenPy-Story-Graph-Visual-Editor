# 前端迁移方案：tkinter → PySide6

> 状态：**主体完成**（2026-10-01）。本文档是迁移与前端架构的"唯一权威"。
> 核心原则：**后端语义一个字都不重写**，只换表现层；并且把"表现层"与
> "编辑语义"彻底分开（见第二节）。

---

## 一、这次到底改了什么（一句话）

原来编辑语义住在 tkinter 里，Qt 前端只能隔着 `ui/` 目录去偷用；
现在中间多了一层 **`storygraph/app/`（框架无关的深模块）**，
两套前端（以及 CLI、测试）都只跟它打交道。

```
core / parsing / excel / assets / story / pipeline
                    ↓
                app/          ← 深模块：接口小、语义全在这里（禁止 import 任何 UI 框架）
                    ↓
        ui/ (tkinter)   ui_qt/ (PySide6)     ← 只做"摆控件 / 读表单 / 写表单"
```

---

## 二、依赖方向问题（本轮的主要修复）

### 2.1 后端反向依赖前端（已修）

`graph/` 与 `pipeline/` 里的这几个模块曾经为了弹一个提示框而 `import tkinter`：

| 位置 | 原来 | 现在 |
|---|---|---|
| `graph/commands.py` | `import tkinter.messagebox` | `from storygraph.app import notify` |
| `graph/editor_utils.py` | `import tkinter.messagebox` | `from storygraph.app import notify` |
| `pipeline/renpy_exporter.py` | `import tkinter.messagebox` | `from storygraph.app import notify` |

后果不只是"不好看"：**无头环境（CI / CLI / 服务器）根本 import 不了**，
所以 `tests/conftest.py` 里塞了 80 行 tkinter 桩来兜。现在后端不弹框了，
它只"汇报事实"：

```python
from storygraph.app import notify
notify.error('导出失败', str(e))      # 后端只管说
```

前端订阅后才决定怎么显示（Qt → 状态栏 + Toast；tkinter → messagebox；
CLI → stderr；测试 → 捕获断言）。**换前端时后端一行都不用改。**

### 2.2 编辑语义写在 UI 里（已修）

`ui/beat_editor.py` 原来 **3792 行**，其中 1000 多行是**纯逻辑**
（Beat↔字段、立绘行、表情/位置投影、资产检索…），却因为跟 tkinter
控件住在同一个文件里，导致"想用这些函数就必须 import tkinter"。

现在切成三层：

| 新模块 | 行数 | 内容 |
|---|---|---|
| `app/beats.py` | 968 | **纯逻辑**：编辑语义唯一真源（无 UI 依赖） |
| `app/stage.py` | 456 | **纯逻辑**：舞台推导 / 素材解析（无 UI 依赖） |
| `app/editor.py` | ~430 | **会话门面**：`EditorSession`（载入/投影/写回/导出/体检/分析） |
| `app/notify.py` | ~110 | 通知端口 |
| `app/picker.py` | ~150 | 资产选择器的语义层 |
| `ui/beat_editor.py` | 2955→**仅 tkinter** | 控件 + 兼容门面（re-export） |
| `ui/stage_preview.py` | 830→**422** | tkinter 预览窗口 + 兼容门面 |

**兼容门面**保证了老的 `from storygraph.ui.beat_editor import beat_to_fields`
继续可用（所以既有测试与 tkinter 版都没坏），而 Qt / CLI / 新代码
应当直接 import `storygraph.app.*`。

### 2.3 前端伸手进私有属性（已修）

`ui_qt/main_window.py` 原来这样干：

```python
self._panel._speaker.setCurrentText(...)      # 摸别人的私有控件
from storygraph.ui.beat_editor import beat_to_fields   # 隔着 tkinter 偷语义
```

现在窗口只跟 `EditorSession` 说话，卡片编辑器提供公共访问器：

```python
s = EditorSession(registry_provider=..., project_root=...)
s.load(path); s.fields(i); s.cards(i); s.apply(i, fields, cards, advanced_open)
s.export(target); s.health(); s.frames(); s.analyze_graph(graph)
```

窗口里**再也没有** `beat.xxx = ...` 赋值，也没有跨对象的 `_` 访问。

---

## 三、Qt 前端现状

`storygraph/ui_qt/`（全部 PySide6，**不 import tkinter**）：

| 文件 | 职责 |
|---|---|
| `theme.py` | `ui/tokens.py` → QSS（颜色/字体只有一个真源） |
| `status.py` | 状态栏 + **Toast 浮层** + `Notifier`（订阅 `app.notify`） |
| `thumb_loader.py` | QThreadPool 异步缩略图（关窗时安全丢弃结果） |
| `main_window.py` | 主窗口：块状编辑器（中央）+ 卡片界面（右 dock） |
| `sprite_grid.py` | 立绘网格（真缩略图 + 表情/位置 chip） |
| `graph_canvas.py` | 块状编辑器（QGraphicsScene 画节点块与连线） |
| `asset_picker.py` | **资产选择器**（分类 + 搜索 + 缩略图 + 详情） |
| `stage_preview.py` | **演出预览**（播放/暂停/跳拍/只看变化/真实贴图） |
| `dialogs.py` | 加章节 / 编辑变量操作 / 加连线 / 变量分析 |
| `help_dialog.py` | **「使用说明」窗口**（分页：上手 / 快捷键 / 保存 / 图与预览） |

### 进度

- [x] 全面扫描（UI 清单 / tkinter 触点 / 环境）
- [x] Qt 基础层：theme / thumb_loader / sprite_grid / help_dialog / main_window
- [x] 块状编辑器 Qt 版（graph_canvas）
- [x] **资产选择器**（原来 `_pick_image` 是空实现 → 换图根本没用）
- [x] **演出预览 Qt 版**（原来缺失 → 「🎬 演出预览」点了报错）
- [x] 图编辑对话框（加章节 / 编辑变量操作 / 加连线 / 变量分析）
- [x] 操作说明独立成窗（**用户明确要求**：不再占主界面）
- [x] `main.py` 默认走 Qt（PySide6 不可用时才退回 tkinter）
- [x] 架构约束（依赖方向 / 无 tkinter）**钉成测试**
- [ ] tkinter 版下线（已无用户可见入口；保留仅为过渡，见第六节）

### 入口

```bash
python main.py                 # 默认 Qt
python main.py --tk            # 旧 tkinter 界面（过渡用，已标记废弃）
```

---

## 四、这一轮修掉的真 bug（都不只是"重构"）

| 现象 | 根因 | 处置 |
|---|---|---|
| 点立绘 chip **永远没反应** | Qt 版把 `symbols` 当 `Beat` 传进 `expression_choices()`，异常被 `except` 吞掉 | 新增 `app.beats.choices_for_image(image, symbols)`，按卡自己的 image 取候选 |
| 「参与角色」左栏**恒空** | 把 **registry** 当 **engine** 传进 `participants()`（后者要 `engine.get_registry()`） | 统一走 `app.picker.RegistrySource` 适配 |
| 关窗/关对话框时 stderr **刷屏** `Signal source has been deleted` | 缩略图工作线程在 loader 的 C++ 对象销毁后还在 `emit` | `_shutting_down` 标志 + `emit` 兜 `RuntimeError` |
| `test_stability_timeout_forces_commit` **偶发失败**（全量必现、单跑必过） | 用墙钟做阈值（`elapsed > MAX_PENDING_TICKS * interval`），Windows 的 `monotonic()` 粒度约 15ms → 判定不确定；真实场景就是"改了文件偶尔要等下一拍" | 改成**数 tick 轮数**，与机器快慢无关 |
| Qt 测试在别的机器/CI **整片 skip** | 测试写死了作者机器的绝对路径 `D:\千连万理\RenPyTest\sr_general` | 新增 `tests/_ui_project.py`，**现场生成**一个小工程；真实项目仍可用 `STORYGRAPH_REAL_PROJECT` 指过去 |

---

## 五、测试

```bash
python -m pytest            # 1261 passed
```

Qt 测试 **offscreen 跑真 Qt**（比 tkinter 桩更接近真实：真事件、真布局、真字体），
并且**可移植**（不再依赖某台机器的项目路径）。锁的东西：

- 语义：出场顺序 / extras 不丢 / 多属性不压平 / 位置统一才整拍
- 会话：高级区收起**不写回** / 空网格**不清空**立绘 / 翻页是纯导航
- 键盘：回车 = 下一拍；多行台词里 ↓/↑ 要到边才翻页
- 对话框：非法 label / 自环连线 / 不可能条件都**就地**报错
- **架构**：`app/*` 与 `ui_qt/*` 都不许出现 tkinter（用 AST 扫源码）

---

## 六、关于 tkinter 版

`storygraph/ui/*.py`（tkinter）**已无用户可见入口**，保留仅为过渡：

- `main.py` 默认走 Qt；只有显式 `--tk` 或 PySide6 装不上才会用到它；
- 它是**兼容门面**的持有者（re-export `app.beats` / `app.stage`），
  一批既有测试仍从这里 import 纯逻辑 —— 这些绑定**不是第二套实现**；
- 下一步（等确认无人再用）：把 `ui/*.py` 整体挪去 `legacy/`，
  同时把那批测试的 import 改指 `storygraph.app.*`。

**判断依据**：tkinter 版的语义已经 100% 在 `app/` 里，删掉它不会再丢任何逻辑。

---

## 七、踩坑记录（Qt 特有，记下来免得再踩）

1. **在 `paint()` 里抛异常 = 直接 segfault**（没有任何 traceback）。
   例：`painter.Antialiasing` 在 PySide6 里不存在（要写
   `QPainter.RenderHint.Antialiasing`）。所以自定义 paint 必须用完整枚举路径，
   且整体包在 try/except 里（`StageCanvas.paintEvent` 就是这么写的）。
2. `QGraphicsView` 没有内置的"空白处拖动平移"，要自己接中键/空格+左键。
3. offscreen 截图中文变方块 → 设 `QT_QPA_FONTDIR=C:\Windows\Fonts`。
4. **子控件 `isVisible()` 在窗口没 `show()` 时恒为 `False`** —— 测试里
   要断言"报了错"应该看 `label.text()`，不是 `isVisible()`。
5. `QDialog.Accepted` 是**类属性**，实例上取不到（`dlg.Accepted` 会
   AttributeError）；测试里请 `from PySide6.QtWidgets import QDialog`。
6. 关窗时**不要**阻塞等 `QThreadPool` —— 解码中的图让它自然跑完、
   结果丢掉即可（`_shutting_down`）；阻塞会让"关窗"卡住。

---

## 八、验收标准

- [x] `python -m pytest` 全过（1261 passed；后端测试语义未改）
- [x] Qt 窗口在 offscreen 下能打开、载入真实章节、翻页/保存/导出
- [x] 说明窗口独立于主界面，主界面不再出现大段操作提示
- [x] `app/*` 与 `ui_qt/*` 无 tkinter 依赖（AST 测试守着）
- [x] 资产选择器可用（换图真的能换）
- [x] 演出预览可用（Qt 版，播放 / 跳拍 / 只看变化）
