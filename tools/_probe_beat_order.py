# -*- coding: utf-8 -*-
"""看几拍的真实字段：show / code / op_order / ordered 的相对顺序。只读。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession                  # noqa: E402
from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.story.transition_expr import beat_transitions    # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
REG = load_asset_registry(ROOT)


def show_chapter(path, lo, hi, limit=8):
    s = EditorSession(project_root=ROOT)
    s._registry_provider = lambda: REG
    s.load(path)
    s.refresh_symbols()
    print(f'\n===== {path.name}  src {lo}-{hi} =====')
    n = 0
    for b in s.beats:
        if not (lo <= (b.src_first or 0) <= hi):
            continue
        print(f'\nseq={b.seq} src={b.src_first}-{b.src_last} kind={b.kind}')
        print('  show=', repr(b.show), ' hide=', repr(b.hide),
              ' with_=', repr(b.with_))
        print('  code=', repr(b.code))
        print('  raw =', repr(b.raw))
        print('  op_order=', repr(b.op_order), ' stage_order=', repr(b.stage_order))
        print('  ordered=', b.ordered)
        print('  transitions=', [(c.source, c.expr) for c in beat_transitions(b)])
        n += 1
        if n >= limit:
            break


show_chapter(ROOT / 'game' / 'chapter' / 'chapter1_1.rpy', 244, 268, 5)
show_chapter(ROOT / 'game' / 'chapter' / 'chapter1_3.rpy', 62, 90, 5)
show_chapter(ROOT / 'game' / 'chapter' / 'chapter2_5.rpy', 120, 126, 2)
