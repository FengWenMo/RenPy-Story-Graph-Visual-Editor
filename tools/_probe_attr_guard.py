# -*- coding: utf-8 -*-
"""真机冒烟：sprite_attr_guard 对 sr_general 的几个 spec 怎么判。只读。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.story.dsl import Symbols                       # noqa: E402
from storygraph.story.beat_model import Beat                   # noqa: E402
from storygraph.app.sprite_attr_guard import (                 # noqa: E402
    scan_sprite_attrs, unknown_attrs_of_spec)

ROOT = Path(r'D:\千连万理\sr_general')
sym = Symbols(load_asset_registry(ROOT))

for spec in ('ye base_hand_down gratified', 'ye aa happy',
             'ye base_handdown happy', 'ye custom_thing happy',
             'ye base_hand_down happy at i11',
             'nobody happy'):
    print('\n', spec)
    for hit in unknown_attrs_of_spec(spec, sym):
        print('  ', hit)

beats = [
    Beat(seq=1, label='ch', text='一', show='ye base_hand_down hi'),
    Beat(seq=2, label='ch', text='二', show='ye aa happy'),
    Beat(seq=3, label='ch', text='三', show='lan aa shy; ye base_hand_down hi'),
]
res = scan_sprite_attrs(beats, sym)
print('\nchecked=%s judged=%s degraded=%s n=%s'
      % (res.checked, res.judged, res.degraded, len(res)))
print(res.report())
