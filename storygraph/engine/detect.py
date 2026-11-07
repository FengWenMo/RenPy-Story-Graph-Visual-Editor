"""
这是**哪个** Ren'Py —— 只读，不 import、不执行引擎代码
====================================================

> 2026-11-05（P0-1）。设计见 `docs/设计_引擎无关IR与退化链_2026-11-05.md` §1.1。

## 为什么要单独一个模块

"引擎版本"以前是 `engine_collect._engine_version()` 里几十行手写解析，散在
采集逻辑中间。三件事需要它，各有各的理由：

| 谁 | 用来干什么 |
|---|---|
| 采集 | 决定探针源码能不能跑（API 变了就换一套） |
| 退化链 | 版本太低 = 某些能力根本不存在（`False` 而不是 `None`） |
| 报告 | "你的引擎是 8.5.3，不是目录名写的 8.4.1" |

## 两个必须知道的坑

1. **目录名会骗人**：本机 `D:\\software\\renpy-8.4.1-sdk` 里实测是
   **8.5.3.26051504**（见设计文档 §1.1）。所以**读 `vc_version.py`，不看目录名**。
2. **`version_tuple` 是算出来的**：`vc_version.py` 里只有一个
   `version = '8.5.3.26051504'`；找 `version_tuple` 会永远空。

## 为什么用 `ast` 而不是 import

`renpy/vc_version.py` 里可能 import 别的东西（`renpy` 包的初始化会碰显示层）。
用 `ast.parse` + `literal_eval` 只认字面量：**不执行任何代码**，也不可能
因为对方模块有副作用而卡住。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

#: `vc_version.py` 的候选相对路径（不同版本位置略有不同）
_CANDIDATES = (
    'renpy/vc_version.py',
    'vc_version.py',
)


@dataclass(frozen=True)
class EngineVersion:
    """引擎版本（`raw` 是文件里那串原文，其余是拆出来的数字）。"""

    major: int
    minor: int
    patch: int
    commit: int
    raw: str

    def __str__(self) -> str:
        base = f'{self.major}.{self.minor}.{self.patch}'
        return f'{base}.{self.commit}' if self.commit else base

    @property
    def short(self) -> str:
        """`8.5.3`（不带 commit）—— 报告里那句话用它。"""
        return f'{self.major}.{self.minor}.{self.patch}'

    def at_least(self, major: int, minor: int = 0) -> bool:
        """`>= (major, minor)`？—— 判"这个 API 存不存在"用。"""
        return (self.major, self.minor) >= (int(major), int(minor))


def parse_version(text) -> EngineVersion | None:
    """
    `'8.5.3.26051504'` → `EngineVersion`；认不出 → `None`（**不猜**）。

    容忍的写法：`8.5.3` / `8.5.3.26051504` / `8.5` / 前后空白。
    段数不够的按 0 补（`8.5` → `8.5.0.0`）。
    """
    raw = str(text or '').strip()
    if not raw:
        return None
    parts = []
    for chunk in raw.split('.'):
        chunk = chunk.strip()
        if not chunk.isdigit():
            return None                      # 有非数字段 → 整串不认识
        parts.append(int(chunk))
    if not parts:
        return None
    while len(parts) < 4:
        parts.append(0)
    return EngineVersion(major=parts[0], minor=parts[1], patch=parts[2],
                         commit=parts[3], raw=raw)


def _literal_of(path: Path, name: str):
    """
    从 `.py` 里取一个**字面量赋值**（`name = '...'`）→ 值；没有 → `None`。

    ⚠ 只认 `ast.Constant`：`version = compute()` 这种**不认**（那要执行，
    正是这里要避免的）。认不出就返回 `None`，由调用方决定怎么办。
    """
    try:
        tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                val = node.value
                if isinstance(val, ast.Constant):
                    return val.value
    return None


def vc_version_path(sdk_root) -> Path | None:
    """`sdk_root/renpy/vc_version.py`（存在才返回）。"""
    root = Path(str(sdk_root or ''))
    if not root.is_dir():
        return None
    for rel in _CANDIDATES:
        p = root / rel
        if p.is_file():
            return p
    return None


def version_of(sdk_root) -> EngineVersion | None:
    """
    SDK 目录 → 引擎版本；读不出来 → `None`（**不猜目录名**）。

    例（本机真事）：目录叫 `renpy-8.4.1-sdk`，返回 `8.5.3.26051504`。
    """
    p = vc_version_path(sdk_root)
    if p is None:
        return None
    return parse_version(_literal_of(p, 'version'))


# ---- 第二个来源：**项目自己记的**上次运行版本 ------------------------------
#
# 为什么要第二源（2026-11-04 用户："game 有明确的 8.4.1"）：
# 用户记得项目是 8.4.1，而 SDK 目录名也叫 `renpy-8.4.1-sdk` —— 但项目
# `log.txt` / `errors.txt` 三处都写 **8.5.3.26051504**。**目录名会骗人**
# （交接文档 §7 已经点名），可"用户凭目录名判断"也是真实场景。
# 所以两个来源都读、都报；**不一致就明说**，不静默挑一个。
_VERSION_IN_LOG_RE = re.compile(r"Ren'Py\s+(\d[\d.]*)")
#: 项目根下会写版本的文件（Ren'Py 每次运行/报错都会刷新它们）
_LOG_FILES = ('log.txt', 'errors.txt', 'traceback.txt')


def project_version(project_root) -> EngineVersion | None:
    """
    项目**日志里**记的引擎版本（上次运行用的那个）；没有 → `None`。

    `log.txt` 第一段就是：`Ren'Py 8.5.3.26051504`。
    """
    root = Path(str(project_root or ''))
    if not root.is_dir():
        return None
    for name in _LOG_FILES:
        p = root / name
        if not p.is_file():
            continue
        try:
            head = p.read_text(encoding='utf-8', errors='replace')[:4096]
        except OSError:
            continue
        m = _VERSION_IN_LOG_RE.search(head)
        if m:
            got = parse_version(m.group(1))
            if got is not None:
                return got
    return None


def versions(sdk_root='', project_root='') -> dict:
    """
    两源对照 → `{sdk, project, agree, note}`（**不许静默挑一个**）。

    · 两边都有且相同 → `agree=True`；
    · 两边都有但不同 → `agree=False` + `note` 说清各自是什么
      （"SDK 是 8.5.3，但项目上次是用 8.4.1 跑的" 这种话必须能说出来）；
    · 只一边有 → `agree=None`（**不是** True：一个来源不算"对上了"）。
    """
    sdk_ver = version_of(sdk_root) if sdk_root else None
    proj_ver = project_version(project_root) if project_root else None
    if sdk_ver and proj_ver:
        agree = sdk_ver.raw == proj_ver.raw
        note = ('' if agree else
                f'SDK 是 {sdk_ver}，但项目上次是用 {proj_ver} 跑的 —— '
                f'两份记录对不上，别把它当同一件事')
    elif sdk_ver:
        agree, note = None, f'只有 SDK 版本（{sdk_ver}），项目还没跑过'
    elif proj_ver:
        agree, note = None, f'只有项目记录的版本（{proj_ver}），还没指定 SDK'
    else:
        agree, note = None, '两处都读不到版本'
    return {'sdk': sdk_ver, 'project': proj_ver, 'agree': agree, 'note': note}


__all__ = ['EngineVersion', 'parse_version', 'project_version',
           'vc_version_path', 'version_of', 'versions']
