# -*- coding: utf-8 -*-
"""
**单个 `.rpy` 文件的事实**（`FileFacts` / `scan_file`）—— `placement` 与
`scan_cache` 共用的叶子。

## 为什么单独一个模块（2026-11-01，技术债第 5 刀）

  `scan_cache.file_facts` 要调用 `placement.scan_file` 来真解析一个文件，
  而 `placement` 又在顶层导入 `scan_cache`（拿 `discover` / `fingerprint` /
  `memo`）—— 两条边合起来正好是一个 2 模块的环
  （`[2] story.placement ↔ story.scan_cache`）。

  把"读一个文件、算出 FileFacts"这段**纯函数**搬到叶子：
  `placement` → `file_facts` ← `scan_cache`，环消失。`placement` 里仍以同名
  re-export（`FileFacts` / `scan_file` / `RE_DECL` / `PERFORM_KINDS`），
  老调用点与 `__all__` 不动。

## 依赖方向（这个模块必须一直是叶子）

  只允许导入 `storygraph.core.*`；**不许**导入 `placement` / `scan_cache` /
  `parsing.renpy_parser` 的顶层（解析器那 3800 行按需在函数内导入，
  见 `scan_file`）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.core.statement import StatementKind

# ---------------------------------------------------------------------------
#  常量
# ---------------------------------------------------------------------------

#: 算作"演出"的语句种类（有这些 = 这个 label 在讲故事，不是纯路由）。
PERFORM_KINDS = frozenset({
    StatementKind.SAY, StatementKind.EXTEND, StatementKind.SCENE,
    StatementKind.SHOW, StatementKind.HIDE, StatementKind.PLAY,
    StatementKind.VOICE, StatementKind.MENU, StatementKind.MENU_OPTION,
    StatementKind.RAW,
})

#: 顶层声明：`default 名字 =` / `define 名字 =`。
#: 名字里**不许有点号** —— `define gui.accent_color` / `define config.x` 是框架配置。
RE_DECL = re.compile(
    r'^(?:default|define)\s+([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)'
    r'\s*(?:=|\()',
    re.MULTILINE)


# ---------------------------------------------------------------------------
#  文件级事实
# ---------------------------------------------------------------------------

@dataclass
class FileFacts:
    rel: str = ''
    abs_path: str = ''
    parse_ok: bool = False
    #: 顶层声明的名字（`default` / `define`）
    decl_names: list = field(default_factory=list)
    #: 这个文件里的 label（按出现顺序）
    labels: list = field(default_factory=list)
    #: 演出语句条数
    perform_stmts: int = 0
    #: **顶层编排跳转**：不在 menu 选项体里的 jump / call 条数
    top_flow_stmts: int = 0
    #: 纯控制流 label 数（没有演出语句、且有 jump/call）
    pure_flow_labels: int = 0

    @property
    def has_decl(self) -> bool:
        return bool(self.decl_names)

    @property
    def is_content(self) -> bool:
        """有演出语句 = 内容文件（也是"章节文件"）。"""
        return self.perform_stmts > 0

    @property
    def is_control_only(self) -> bool:
        """所有 label 都只做路由 → 独立控制流文件。"""
        return (bool(self.labels) and self.perform_stmts == 0
                and self.pure_flow_labels > 0)


# ---------------------------------------------------------------------------
#  扫描
# ---------------------------------------------------------------------------

def _read_text(path) -> str:
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def scan_file(path, project_root) -> FileFacts:
    """读一个 `.rpy`，产出文件级事实。解析失败时 `parse_ok=False`，不抛。"""
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser

    p = Path(path)
    root = Path(project_root)
    try:
        rel = p.relative_to(root).as_posix()
    except ValueError:
        rel = p.as_posix()

    facts = FileFacts(rel=rel, abs_path=str(p))
    facts.decl_names = RE_DECL.findall(_read_text(p))

    try:
        result = ParseResultBuilder().build(RenPyParser().parse(str(p)))
    except Exception as e:                            # noqa: BLE001 - 单个文件坏不该炸整次探测
        swallow('story.file_facts.scan_file', e)
        return facts
    facts.parse_ok = True

    statements = list(getattr(result, 'statements', None) or [])
    owners = list(getattr(result, 'statement_labels', None) or [])
    menu_opts = list(getattr(result, 'statement_menu_options', None) or [])
    facts.labels = [str(k) for k in (getattr(result, 'labels', None) or {})]

    # 按 label 分组，判断每个 label 是不是纯路由
    per_label = {}
    for i, owner in enumerate(owners):
        if i >= len(statements):
            break
        st = statements[i]
        kind = str(getattr(st, 'kind', '') or '')
        name = str(owner or '')
        if kind in PERFORM_KINDS:
            facts.perform_stmts += 1
        in_menu_option = bool(str(menu_opts[i] or '')) \
            if i < len(menu_opts) else False
        if kind in (StatementKind.JUMP, StatementKind.CALL) and not in_menu_option:
            facts.top_flow_stmts += 1
        info = per_label.setdefault(name, {'perform': 0, 'flow': 0})
        if kind in PERFORM_KINDS:
            info['perform'] += 1
        if kind in (StatementKind.JUMP, StatementKind.CALL):
            info['flow'] += 1

    facts.pure_flow_labels = sum(
        1 for info in per_label.values()
        if info['perform'] == 0 and info['flow'] > 0)
    return facts


__all__ = ['PERFORM_KINDS', 'RE_DECL', 'FileFacts', 'scan_file']
