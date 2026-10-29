"""
storygraph.core.user_path
=========================
**用户级数据落在哪个目录** —— 全工具只有这一份规则。

为什么单独一个模块（2026-10-28）
--------------------------------
`ai.json` / `session.json` / `autosave/` 三处原本各自写了一遍同样的 12 行：
Windows 走 `%LOCALAPPDATA%`、其它平台走 `$XDG_STATE_HOME`，都允许环境变量覆盖。
三份实现里有**两份忘了回退 `%APPDATA%`**（`crashlog.py` 与 `github_star.py`
是回退了的），于是同一台机器上 `crash.log` 和 `session.json` 有可能落在
两个不同目录 —— 用户想删掉"这工具留下的东西"时，不知道该删哪个。

这类"同一件事、不同出口两种待遇"是本项目历史上最高频的一类缺陷
（见 `.workbuddy/memory/MEMORY.md` 第五节）。所以把规则收成一处：
**改规则只改这里，三处调用方不用动。**

⚠ 但**规则本身一个字都没改**：三处原本就是"`%LOCALAPPDATA%` → `$XDG_STATE_HOME`
→ `~/.local/state`"，抽出来是为了防止以后各自演化，不是为了顺手换位置。
"顺手统一一下"在这里是**有害**的：位置一变，用户已有的 `session.json` /
`ai.json` 就找不到了，表现是"记忆功能坏了"。

哪些地方**故意不**走这里
------------------------
  · `core/crashlog.py`：它要的不是"一个确定位置"，而是"**一串候选**，
    第一个写得进去的算"—— exe 装在 Program Files 时旁边根本写不进去，
    必须一路退到临时目录。所以它会额外试 `%APPDATA%`，也会退到临时目录。
    那是另一件事，别硬合并（合并的代价是"日志写不出来"）。
  · `ui_qt/github_star.read_token()`：它只**读**一个现有文件，
    不负责分配位置。

**两者在常规机器上指向同一个目录**（`%LOCALAPPDATA%` 有值时都一样），
只有 `%LOCALAPPDATA%` 缺失时才分叉 —— 那是异常环境，不值得为它改动
主路径的行为。

`app_data_dir` 只**算**路径，**不建目录、不检查权限** ——
建目录是写盘那一步的事（`session_store.save` / `autosave.write` 各自负责），
因为读路径不该有副作用（`--help` 时不该在硬盘上留东西）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: 目录名的两种拼法（选哪个由 `app_data_dir` 按"位置从哪来"决定，见它的 docstring）：
#: Windows 位置用驼峰 `%LOCALAPPDATA%\StoryGraph`，XDG 位置用小写
#: `~/.local/state/storygraph`。**不要为了"整齐"把它们统一** ——
#: 统一会让老用户的配置找不到。
_WIN_DIR = 'StoryGraph'
_POSIX_DIR = 'storygraph'


def app_data_dir(*parts: str) -> Path:
    """
    返回 `…/StoryGraph/<parts…>` 或 `…/storygraph/<parts…>`（哪一种见文末 ⚠）。

    展开顺序（先到先得）：

      1. `%LOCALAPPDATA%` —— Windows 的常规位置，**不进漫游配置**
         （这类文件是"这台机器的这个人"的，跟着漫游账号跑没有意义）；
      2. `$XDG_STATE_HOME` —— Linux / macOS 的常规位置；
      3. `~/.local/state` —— 2 缺失时的兜底。

    全都拿不到（连 `$HOME` 都没有，属异常环境）→ 退回**当前目录**下的
    `storygraph/`。宁可位置难看，也不要因为算不出路径就让整个工具起不来。

    ⚠ **这里刻意不试 `%APPDATA%`**（2026-10-28 试过又撤回）：`crashlog.py` /
    `github_star.py` 会试它，但那是"多候选 + 只读"的另一件事。给主路径加上
    这一步的后果是**用户已有的文件换位置**——实测本机从 bash 启动时
    `%LOCALAPPDATA%` 是空的，一加回退，`session.json` 就从
    `~/.local/state/storygraph/` 跳到 `%APPDATA%/StoryGraph/`，
    用户的会话记忆当场"消失"。位置这类东西，**宁可难看也不要搬家**。

    ⚠ 本函数**不建目录**，也不校验可写性。需要目录存在的地方自己
    `path.parent.mkdir(parents=True, exist_ok=True)`。

    ⚠ **目录名跟着"位置从哪来"走，不跟着平台走**（这一条是实测出来的）：
    位置来自 `%LOCALAPPDATA%` → 目录叫 `StoryGraph`（Windows 的习惯）；
    位置来自 XDG / `~/.local/state` → 目录叫 `storygraph`（POSIX 的习惯）。
    听起来别扭，但**旧实现就是这样**，而位置这类东西改了就等于搬家
    （见 `tools/_check_user_path_equivalence.py` 的对照表：
    按平台统一大小写会让 Windows 上的 `~/.local/state/storygraph` 变成
    `.../StoryGraph`，用户的会话记忆当场找不到）。
    """
    if sys.platform.startswith('win'):
        base = (os.environ.get('LOCALAPPDATA') or '').strip()
        if base:
            return _join(Path(base), _WIN_DIR, parts)

    state = (os.environ.get('XDG_STATE_HOME') or '').strip()
    if state:
        return _join(Path(state), _POSIX_DIR, parts)
    try:
        home = Path.home()
    except (RuntimeError, OSError):            # 无 HOME 的极端环境
        home = Path.cwd()
    return _join(home / '.local' / 'state', _POSIX_DIR, parts)


def _join(root: Path, name: str, parts: tuple) -> Path:
    """`root/name` 再接上 `parts`（没有 `parts` 就停在 `root/name`）。"""
    out = root / name
    for part in parts:
        out = out / part
    return out
