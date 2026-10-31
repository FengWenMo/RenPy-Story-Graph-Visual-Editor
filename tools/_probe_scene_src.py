# -*- coding: utf-8 -*-
"""临时探针：看真机某个 scene 前后的原文（确认 CG 是不是真会被 scene 清掉）。"""
from pathlib import Path

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_statements

root = Path(r'd:\千连万理\sr_general')
an = IncrementalRenPyAnalyzer()
pr, _graph = an.analyze(files=discover_scripts(root), graph=None)
beats = beats_from_statements(pr.statements)
for i in range(605, 620):
    b = beats[i]
    print(i, '| scene=', repr(b.scene), '| at=', repr(b.scene_at),
          '| show=', repr(b.show), '| hide=', repr(b.hide),
          '| stage=', repr(b.stage), '| text=', repr((b.text or '')[:20]))
