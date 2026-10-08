#!/usr/bin/env python
"""
真机对账：每个章节里**舞台指令（含独立 `with`）的顺序与条数**有没有被工具改掉
================================================================================

出处：缺口 4（"相邻舞台指令过表翻序"）—— 用户 2026-10-07 要求**正面修掉**，
不许绕开。这个探针就是"修好了没有"的真机判据（`tools/probe_stage_seq.py`
是合成形状的 252 组枚举，这里是**真机的每一个文件**）。

比的是什么：

    src_ops  = 源文件里 scene/show/hide/with 行的序列
    out_ops  = 产物（导出一次 = t1）里同样的序列

`src_ops != out_ops` 就是"工具改了演出顺序或条数"。只读，不写项目。

    python tools/probe_stage_delta_real.py [项目根]
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core.vocabulary import statement_head_token  # noqa: E402
from storygraph.parsing.discover import discover_scripts  # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel  # noqa: E402
from storygraph.story.excel_import import import_beats_from_excel  # noqa: E402

#: 顺序敏感的行头（舞台 + 独立转场）
HEADS = ('scene', 'show', 'hide', 'with')


def _ops(text: str) -> list:
    """
    文本 → 顺序敏感行的序列（跳过注释与空行；**去掉行尾注释、压空白**）。

    ⚠ 行尾注释必须去掉：工具会在 `#` 前补一个空格（`scene x #注` →
    `scene x  #注`），那是**排版**不是顺序 —— 不去掉就会把这种文件误报成
    "顺序被改"（`screens.rpy` 实测就是这么误报的）。
    """
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith('#'):
            continue
        if statement_head_token(s) in HEADS:
            s = re.sub(r'\s+#.*$', '', s).strip()
            out.append(re.sub(r'\s+', ' ', s))
    return out


def _first_diff(a: list, b: list) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f'第 {i} 条起：源 {x!r} != 产物 {y!r}'
    if len(a) != len(b):
        return f'条数不同：源 {len(a)} vs 产物 {len(b)}'
    return ''


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1
                else r'D:\千连万理\RenPyTest\sr_general')
    files = discover_scripts(root)
    tmp = Path(tempfile.mkdtemp(prefix='probe_stage_delta_'))
    bad_direct = bad_table = 0
    checked = 0
    for i, f in enumerate(files):
        src = Path(f).read_text(encoding='utf-8', errors='ignore')
        want = _ops(src)
        if not want:
            continue
        checked += 1
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        got = _ops(render_beats_to_rpy(beats))
        xl = tmp / f'{i}.xlsx'
        export_beats_to_excel(beats, xl)
        got2 = _ops(render_beats_to_rpy(import_beats_from_excel(xl)))
        if got != want:
            bad_direct += 1
            print(f'[不过表] {Path(f).name}  {_first_diff(want, got)}')
        if got2 != want:
            bad_table += 1
            print(f'[过表]   {Path(f).name}  {_first_diff(want, got2)}')
    print()
    print(f'有舞台指令的章节：{checked} 个')
    print(f'  不过表就改了顺序/条数：{bad_direct} 个')
    print(f'  过表之后改了顺序/条数：{bad_table} 个')
    return 1 if (bad_direct or bad_table) else 0


if __name__ == '__main__':
    raise SystemExit(main())
