# -*- coding: utf-8 -*-
"""把某个真机文件渲染成文本（给"老代码 vs 新代码"比对用）。

用法：
    python tools/_dump_render.py <repo_root> <rpy 文件名> <输出 txt>
"""
from __future__ import annotations

import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
want = sys.argv[2]
out = Path(sys.argv[3])
sys.path.insert(0, str(repo))

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
target = [f for f in sorted(discover_scripts(REAL)) if Path(f).name == want][0]
az = IncrementalRenPyAnalyzer()
pr, _ = az.analyze(files=[target], graph=None)
out.write_text(render_beats_to_rpy(beats_from_parse_result(pr)),
               encoding='utf-8')
print(f'wrote {out}')
