# -*- coding: utf-8 -*-
"""真机复现：`ye base_hand_down …` 为什么会出现在候选里，且选不出正确组合。

只读，不写项目。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.story.dsl import Symbols                       # noqa: E402
from storygraph.story.layered_model import load_layer_model_cached  # noqa: E402
from storygraph.app.beats import attr_slot_rows, apply_attr_slot     # noqa: E402
from storygraph.ui_qt.sprite_codes_view import slot_choices          # noqa: E402
from storygraph.ui_qt.sprite_hints import known_tag_warning          # noqa: E402
from storygraph.assets.vocabulary import EMPTY_VOCABULARY            # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')

t0 = time.time()
reg = load_asset_registry(ROOT)
sym = Symbols(reg)
model = load_layer_model_cached(str(ROOT))
print(f'load {time.time() - t0:.2f}s')

print('\n--- declared_attrs_of(ye) ---')
print(sorted(sym.declared_attrs_of('ye')))
print('\n--- attrs_of_tag(ye) ---')
print(sorted(sym.attrs_of_tag('ye')))
print('\n--- defined_words 里的 hand ---')
print(sorted(w for w in sym.defined_words() if 'hand' in w.lower()))
print('\n--- model.poses(ye) ---')
print((model.poses or {}).get('ye'))

SPECS = [
    'ye base_hand_down gratified',
    'ye base_handdown gratified',
    'ye base_hand_down hi',
    'ye base_handdown hi',
    'ye gratified',
]
for spec in SPECS:
    print(f'\n=== {spec} ===')
    w = known_tag_warning(spec, sym)
    print('warning:', (w[:160] + '…') if len(w) > 160 else (w or '（不吭声）'))
    rows = attr_slot_rows(spec, sym, facts=None, layer_model=model)
    for r in rows:
        print(f"  row group={r['group']!r} label={r['label']!r} "
              f"value={r['value']!r} options={r['options']} "
              f"extras={r.get('extras')}")
    grouped = slot_choices(
        spec, 'ye', rows, EMPTY_VOCABULARY,
        lambda g, v, s=spec: apply_attr_slot(
            s, g, v, symbols=sym, layer_model=model))
    print(f'  -> {sum(len(g["values"]) for g in grouped)} candidates'
          f' / {len(grouped)} groups')
    for g in grouped:
        print(f"     [{g['label']}]")
        for value, shown, _tip in g['values']:
            print(f'       {value!r:55} shown={shown!r}')
