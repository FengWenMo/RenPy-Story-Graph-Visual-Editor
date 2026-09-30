# 前端迁移方案：tkinter → PySide6

> 状态：**进行中**（2026-09-30）。本文档是迁移的"唯一权威"，每完成一步都要回来打钩。
> 核心原则：**后端一个字都不动**（`core` / `parsing` / `excel` / `story` / `pipeline` 全部原样），
> 只换表现层。这也是为什么这次的方案敢这么写 —— 项目的**语义全部藏在后端函数里**。

---

## 一、全面扫描结果（底数）

### UI 层规模（`storygraph/ui/`，共 8563 行）

| 模块 | 行数 | 内容 | tkinter 依赖 |
|---|---|---|---|
| `beat_editor.py` | 3792 | 剧本表编辑器（主卡片区）+ 资产选择器对话框 | StringVar ×27 / Canvas / 网格 |
| `graph_view.py` | 2199 | **主窗口**（图视图 + 全部对话框） | Canvas（图）/ 十几个对话框 |
| `stage_preview.py` | 831 | 演出预览 | Canvas + 定时器 |
| `sprite_card.py` | 506 | 立绘卡片（网格 + chip） | Canvas |
| `thumb_loader.py` | 343 | 异步缩略图 | PhotoImage + after |
| `asset_panel.py` | 310 | 资产面板数据（纯逻辑，无 UI） | 无 |
| `tokens.py` | 234 | 颜色/字号/间距 | 无（**是 Qt QSS 的数据源**） |
| `scroll_frame.py` | 201 | 滚动容器 | Canvas |
| `tooltip.py` | 146 | 悬浮提示 | Toplevel |

### 需映射到 Qt 的触点

- **控件**：Toplevel→QDialog、Frame→QWidget、Canvas→QGraphicsView/QWidget、Listbox→QListWidget、
  ScrolledText→QPlainTextEdit、Checkbutton→QCheckBox、Entry→QLineEdit、Button→QPushButton、Scrollbar→QScrollArea
- **数据绑定**：StringVar ×27 → 直接用控件的 `text` / `currentText`（Qt 不需要中间层）
- **定时器**：`after()` ×16 → `QTimer` / `QTimer.singleShot`
- **异步缩略图**：PhotoImage + after → **QThreadPool + QPixmap + 信号**（比 tkinter 好做得多）
- **对话框**：messagebox ×56 → QMessageBox；filedialog ×13 → QFileDialog
- **布局**：grid/pack/place → QVBoxLayout / QHBoxLayout / QGridLayout

### 环境

- PySide6 **6.11.1 已装好**（`D:\Python\python.exe`），openpyxl 3.1.5 / Pillow 11.3.0 同环境 ✓
- **测试可跑真 Qt**：`QT_QPA_PLATFORM=offscreen`（比 tkinter 桩更真，CI 也能跑）

---

## 二、Bug 清扫（本轮已修）

| 位置 | 问题 | 处置 |
|---|---|---|
| `ui/stage_preview.py:32` | `Optional` 导入未用（重写遗留） | 已删 |
| `ui/graph_view.py:72` | `occupied_labels` 导入未用 | 已删 |
| `pipeline/build.py:29` | `stage_snapshot` 带 `# noqa: F401`（刻意 re-export） | 保留 |
| 其余 | pyflakes 只报 `script_table.py` 兼容门面的刻意 re-export | 保留（设计如此） |

> 真正的"bug"级别问题（立绘错位 / 说话人被改 / 增量补丁破坏 / 预览贴不上图 / 缺资源提示）已在
> 今天早些时候单独修复并全部带测试；见 `.workbuddy/memory/2026-09-30.md`。

---

## 三、为什么必须按"后端不动"来迁

这个项目的最强资产是**后端是纯函数**：

- `beats_from_parse_result` / `hydrate_from_dsl` / `beat_sprite_rows` / `group_sprites_by_tag`
  / `lookup_image_id` / `uniform_sprite_position` / `normalize_expression_words`
  / `apply_expression` / `apply_position` / `participants` / `beat_to_fields` / `fields_to_beat`
  / `patch_export` / `snapshot_rows` / `resolve_frame_assets` —— **全部是后端无关的纯逻辑**。

tkinter 层只做**三件事**：摆控件、读表单、把表单写回。换 Qt = 只重写这三件事，
语义一行都不用重写。这也是 1200+ 测试里绝大多数**不需要跟着改**的原因（它们测的是后端）。

**例外**：`tests/conftest.py` 的 tkinter 桩要换成 `QT_QPA_PLATFORM=offscreen`（更好）。

---

## 四、UI 重新设计（用户原话："全部都堆在右侧……另找个说明窗口放……更加整体、简洁、美观、实用"）

### 现在的问题

- 右列是一条**从上到下的长条**：说话人/台词/Scene/BGM/Voice/Comment/立绘网格/高级区/舞台快照/操作提示，
  全靠滚（实测请求高度 ~975px > 可视 600px）。
- 一大段"操作提示"文字常年占据黄金位置。

### 新布局（QMainWindow）

```
┌ 工具栏 ──────────────────────────────────────────────┐
│ 📂载入  💾保存这一拍  ⤓导出  🔍体检  🎬预览 │ ◀上一拍 [43/305] 下一拍▶ │ ❓说明 │
├────────┬──────────────────────────────────────────────┤
│ 章节   │  说话人 [下拉]    台词 [多行]                  │
│ 列表   │  ──────────────────────────────────────────  │
│        │  立绘 [网格卡片：真缩略图 + 表情/位置 chip]   │
│ ────── │  ──────────────────────────────────────────  │
│ 参与   │  背景 [下拉]  音乐 [下拉]  语音 [下拉]        │
│ 角色   │  备注 [单行]                                 │
│        │  ▸ 高级区（可折叠：SFX/代码/Label/Kind/Notes/Stage）│
└────────┴──────────────────────────────────────────────┘
  状态栏：✔ 已保存到这一拍（chapter1_3.0043）· 导出时落盘
```

- **操作提示 / 使用说明 → 独立的「说明」窗口**（工具栏 ❓ 打开，默认关闭，不占主界面）。
- 中央编辑区只留"每一拍要填的东西"；高级区可折叠。
- 配色/字号/间距来自 `ui/tokens.py`（Qt 版通过 `theme.py` 生成 QSS，**同一个真源**）。

---

## 五、Qt 基础层（已建，见 `storygraph/ui_qt/`）

| 文件 | 职责 |
|---|---|
| `theme.py` | `ui/tokens.py` → Qt StyleSheet（颜色只有一个真源） |
| `thumb_loader.py` | **QThreadPool** 异步缩略图（QPixmap），API 与 tkinter 版对齐：`request(slot, path, apply, max_size)` |
| `main_window.py` | 新主窗口（左侧章节 dock + 中央卡片编辑区 + 工具栏 + 状态栏） |
| `sprite_grid.py` | 立绘网格（QWidget + QPixmap + 表情/位置 chip） |
| `help_dialog.py` | 「说明」窗口（操作提示从主界面移到这里） |
| `main_qt.py` | 入口（`python -m storygraph.main_qt`） |

---

## 六、迁移顺序（风险与用量驱动）

1. **剧本表编辑器**（ScriptTableEditorWindow + BeatEditorPanel + 立绘卡 + 预览按钮）— 用得最多
2. 对话框（资产选择器、导出确认）
3. 图视图（`graph_view.py` 的 Canvas 图 → QGraphicsView）
4. 演出预览（`stage_preview.py` → QPainter/QGraphicsView）

每迁一个窗口：**拿真实数据跑一遍 offscreen 截图自检**（`window.grab()` → PNG），确认
"布局、真缩略图、站位、说话人高亮"都与 tkinter 版一致，才迁下一个。

### 进度（2026-09-30）

- [x] 全面扫描（UI 清单 / tkinter 触点 / 环境）
- [x] Bug 清扫（pyflakes + 两处未使用导入清理）
- [x] Qt 基础层：`ui_qt/theme.py`（tokens→QSS）/ `thumb_loader.py`（QThreadPool 异步）/
      `sprite_grid.py`（立绘网格 + chip）/ `help_dialog.py`（说明窗口）/
      `main_window.py`（重设计主窗口）/ `main_qt.py`（入口）
- [x] **offscreen 真实数据自检通过**：载入 chapter1_3（305 拍）、立绘真缩略图、
      翻页、保存、写回 show —— 见 `.pip-tmp/qt_shots/qt_1_card.png`
- [x] Qt 测试 `tests/test_ui_qt.py`（offscreen 真 Qt，11 个）
- [ ] 资产选择器（`ui_qt/asset_picker.py`，暂以 `_pick_image` 空实现兜底）
- [ ] 演出预览 Qt 版（`ui_qt/stage_preview.py`）
- [ ] 图视图 Qt 版
- [ ] 对话框批量迁移

**入口**：`python -m storygraph.main_qt [章节.rpy]`（与 tkinter 版 `main.py` 并存）。

---

## 七、验收标准（全部可自动化）

- [ ] `python -m pytest` 全过（后端测试不动）
- [ ] Qt 窗口在 offscreen 下能打开、能载入真实章节、能翻页/保存/导出
- [ ] offscreen 截图与 tkinter 版同一帧一致（背景/立绘/站位/说话人高亮）
- [ ] 说明窗口独立于主界面，主界面不再出现大段操作提示
