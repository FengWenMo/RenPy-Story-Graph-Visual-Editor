# -*- coding: utf-8 -*-
"""临时探针：plan_scene_change 的合成用例（写判据之前先手动看一眼）。"""
from storygraph.app.stage import plan_scene_change
from storygraph.story.atl import parse_transforms
from storygraph.story.beat_model import Beat
from storygraph.story.stage_snapshot import stage_snapshots

TRANSFORMS = parse_transforms('''transform bgfit:
    fit "cover"
''')


def beats():
    return [
        Beat(seq=1, label='t', text='', scene='bg big'),
        Beat(seq=2, label='t', text='', show='ling happy'),
        Beat(seq=3, label='t', text='一'),
        Beat(seq=4, label='t', text='二'),
    ]


b = beats()
e = plan_scene_change(b, 3, 'bg wide', at='bgfit',
                      transform_index=TRANSFORMS)
print('ok=', e.ok, 'reason=', e.reason)
print('added=', e.added, 'cleared=', e.cleared_sprites, e.cleared_layers)
print('order=', e.order_field, repr(e.order_before), '->', repr(e.order_after))
print('--- diff ---')
print(e.diff_text())
assert e.apply()
print('scene=', b[3].scene, b[3].scene_at)
rows = stage_snapshots(b)
print('frames 3/4 立绘=', rows[3]['state']['actors'], rows[3]['state']['actors'])
assert rows[3]['state']['actors'] == []
assert e.revert() and b[3].scene == ''

# 第二例：同一拍有 `show bg old`（会盖掉新背景）
b2 = [
    Beat(seq=1, label='t', text='', scene='bg big'),
    Beat(seq=2, label='t', text='', show='bg big', op_order='show#0'),
]
e2 = plan_scene_change(b2, 1, 'bg wide')
print('--- 第二例 ---')
print('ok=', e2.ok, 'reason=', e2.reason, 'removed=', e2.removed_show)
print(e2.diff_text())
assert e2.apply()
print('show=', repr(b2[1].show), 'op_order=', repr(b2[1].op_order))
from storygraph.story.beat_render import render_beat_to_rpy
print('渲染=', render_beat_to_rpy(b2[1]))

