# -*- coding: utf-8 -*-
"""真机复现 6：错词 vs 正词，引擎实际会叠哪几层。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.story.dsl import Symbols                       # noqa: E402
from storygraph.story.layered_model import load_layer_model_cached  # noqa: E402
from storygraph.app.stage import sprite_plan                   # noqa: E402
from types import SimpleNamespace                              # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
reg = load_asset_registry(ROOT)
sym = Symbols(reg)
model = load_layer_model_cached(str(ROOT))

CASES = [
    'ye base_hand_down gratified',
    'ye base_handdown gratified',
    'ye base_hand_down hi',
    'ye base_handdown hi',
    'ye gratified',
]
for spec in CASES:
    attrs = spec.split()[1:]
    try:
        paths = sym.resolved_paths_of('ye', attrs)
    except Exception as e:  # noqa: BLE001
        paths = [f'<{type(e).__name__}: {e}>']
    plan = sprite_plan(None, SimpleNamespace(image=spec), model, {}, str(ROOT))
    plan_paths = [Path(p).name for p in (plan.paths() if plan.ok else [])]
    print(f'{spec:30} resolved={[Path(p).name for p in paths]} '
          f'plan.ok={plan.ok} plan={plan_paths} reason={getattr(plan, "reason", "")[:60]}')
