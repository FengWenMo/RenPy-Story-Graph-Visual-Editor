#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""① debug2：真机导出路径上标题通道的输入输出。"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import patcher as P

SRC = (
    'label start:\n'
    '    menu:\n'
    '        set seen\n'
    '        "开始":\n'
    '            e "a"\n'
    '    return\n'
)

_calls = []
_orig = P._replace_matched


def spy(old_lines, gen_bodies, **kw):
    res = _orig(old_lines, gen_bodies, **kw)
    _calls.append({
        'old': list(old_lines), 'gen': list(gen_bodies),
        'new': res[0], 'changed': res[1], 'unlanded': res[2],
        'allow_edit': kw.get('allow_edit', True),
    })
    return res


P._replace_matched = spy

tmp = Path(tempfile.mkdtemp(prefix='p1dbg2_'))
game = tmp / 'game'
game.mkdir(parents=True)
path = game / 'script.rpy'
path.write_bytes(SRC.encode('utf-8'))

from storygraph.app.editor import EditorSession

s = EditorSession(project_root=tmp)
s.load(path)
for i, b in enumerate(s.beats):
    print(f'beat[{i}] kind={b.kind!r} src={b.src_first}-{b.src_last} '
          f'text={getattr(b, "text", "")!r} raw={getattr(b, "raw", "")!r} '
          f'cond={getattr(b, "condition", "")!r}')

blocks, raw_gen = P._render_blocks(s.beats, None, SRC.split('\n'))
for i, first, last, gen, b in blocks:
    print(f'  gen[{i}] src={first}-{last}: {gen}  raw_gen={raw_gen[i]}')

fields = s.fields(0)
fields.update({'Text': '新标题'})
s.apply(0, fields, cards=s.cards(0), advanced_open=True,
        stage_loaded=fields.get('Stage'))

blocks2, raw_gen2 = P._render_blocks(s.beats, None, SRC.split('\n'))
print('--- apply 之后 ---')
for i, first, last, gen, b in blocks2:
    print(f'  gen[{i}] src={first}-{last}: {gen}  raw_gen={raw_gen2[i]}')

res = s.export(path)
print('--- export ---')
print('ok =', res.get('ok'))
print('note =', res.get('note', '')[:200])
print('blocking =', res.get('blocking', res.get('reasons', ''))[:300] if res.get('blocking', res.get('reasons')) else '')
print()
print('=== _replace_matched 调用 ===')
for c in _calls:
    print('old =', c['old'])
    print('gen =', c['gen'])
    print('new =', c['new'], '| changed =', c['changed'],
          '| unlanded =', c['unlanded'], '| allow_edit =', c['allow_edit'])
    print('---')
print('file now:')
print(path.read_bytes().decode('utf-8'))
shutil.rmtree(tmp, ignore_errors=True)
