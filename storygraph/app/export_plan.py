"""
storygraph.app.export_plan
==========================
导出落盘的**纯判定**：默认路径、门禁代码、能不能让用户强写。

为什么单独一层（2026-11-01，服务化第三刀）
------------------------------------------
这三件事从前分住在三个地方，而且**同一条判据各有各的写法**：

  · `default_output_path` 在 `app.session_export` 里当 mixin 方法，读
    `self.script_path` / `self.project_root` —— 一个纯函数，却只能靠
    "造一个会话"来测；
  · "哪些门禁代码可以让用户**仍然覆盖**"在 `ui_qt.window_export.overwritable`
    里，而 `ui_qt.window_graph_ops` 又**手写了一遍**同一句话（拿 `codes`
    做一次成员判断，把代码名硬编码在业务代码里）；
  · 门禁问题 → 代码表那个推导式在 `session_export` 里写了两遍。

判据：**只拿 `(路径, 门禁问题, 导出结果字典)` 能不能回答？** 能回答的都在
这里，与 Qt、与会话状态都无关，`import` 也只有 `pathlib`。于是
"新增一条门禁代码时该不该给逃生舱"变成一个**显式决定**（改这个文件），
不再靠在某个对话框里记得加一句。

⚠ 这里的函数**不做判断以外的事**：不读磁盘、不写文件、不记日志。
`overridable()` 只看代码表 —— "能不能覆盖"与"覆盖之后会不会毁掉东西"是
两件事，后者在 `generation_guard.would_destroy_opaque`。
"""
from __future__ import annotations

from pathlib import Path


__all__ = ['OVERRIDABLE_CODES', 'block_codes', 'default_output_path',
           'issue_message', 'overridable']


#: 这些门禁代码是"产物本身没问题，只是有人要盖掉别人的东西" → 可以给逃生舱。
#:
#: **其余一律不给**：语法错（`PYTHON_SYNTAX_ERROR`）、重名
#: （`LABEL_COLLISION`）、中文图像标签（`CJK_IN_IMAGE_TAG`）这些是"产物
#: 本身坏了"，覆盖只会把坏脚本写进 `game/`，让游戏起不来。
OVERRIDABLE_CODES = frozenset({'HANDWRITTEN_TARGET'})

#: 没有源文件（新建章节）时，产物落在 `<project>/build/renpy/` 下的这个名字。
_NO_SOURCE_STEM = 'out'


def default_output_path(script_path, project_root) -> Path:
    """
    导出对话框的**默认路径**。

    ⚠ 2026-10-18（审计 P1-7"写回不闭环"）：从前**总是** `build/renpy/
    <stem>.generated.rpy` —— 于是策划改完卡片、导出，原章节文件**一动
    不动**，还得自己把产物搬回去。审计原话："写回落到 build/renpy/，
    不重载编辑器、不改原章节"。

    现在：**这一章有源文件就默认原地写回**（增量合并 + 安全门禁：
    对位成功时逐字节保留、只换改过的行；对不上时门禁会拦，绝不静默
    覆盖）。没有源文件（新建章节）才落到 `build/renpy/` —— 那时连
    "叫什么名字"都无从谈起，所以用 `out.generated.rpy`。

    `script_path` / `project_root` 可以是 `Path`、`str` 或 `None`：
    会话那边两个属性都可能还没设置（没加载项目就点导出）。
    """
    if script_path:
        return Path(script_path)
    base = Path(project_root or Path('.')) / 'build' / 'renpy'
    return base / f'{_NO_SOURCE_STEM}.generated.rpy'


def issue_message(issue) -> str:
    """
    门禁发现 → 一句给人看的话。

    统一在这里取：`blocking` 要交给 UI 直接显示，
    而 `GuardIssue` 有 `.message`、别的实现可能只有 `__str__`
    （单测里塞过裸字符串）。三种形态都兜住，别在调用点各写一遍。
    """
    return str(getattr(issue, 'message', None) or issue)


def block_codes(issues) -> list:
    """
    门禁问题 → **代码**表（`HANDWRITTEN_TARGET` / `PATCH_FAILED` …）。

    前端靠它决定"要不要给用户一个强制覆盖的选项"，所以这里**只认
    `.code`**，取不到就给空串（老实现就是这个口径，别改成取 str(issue)）：
    一句话塞进代码表会让 `overridable()` 永远判 False。
    """
    return [str(getattr(i, 'code', '')) for i in (issues or [])]


def overridable(result) -> bool:
    """
    这次 block **可以**让用户选择"仍然覆盖"吗？

    `result` 是 `EditorSession.export` / `export_control_flow` 的返回字典：
    只有 `ok=False` 且 `codes` 里有 `OVERRIDABLE_CODES` 里的一条才放行。
    `ok=True` 时没有"覆盖"这回事，所以恒 False。
    """
    if not isinstance(result, dict) or result.get('ok'):
        return False
    codes = set(result.get('codes') or ())
    return bool(codes & OVERRIDABLE_CODES)
