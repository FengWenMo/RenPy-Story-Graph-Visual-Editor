# -*- coding: utf-8 -*-
"""验：引擎层在提示里的表现（真项目 + 已缓存的裁决）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from storygraph.assets.engine_images import bad_keys          # noqa: E402
from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.story.dsl import Symbols                     # noqa: E402
from storygraph.ui_qt.sprite_hints import known_tag_warning  # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')

t0 = time.time()
reg = load_asset_registry(ROOT)
sym = Symbols(reg)
print(f'Symbols 构造 {time.time() - t0:.2f}s | 引擎坏键 {len(sym.engine_bad_keys())} 个')

cases = [
    'lan aa happy',        # 引擎说不是图像
    'lan happy aa',        # 同上（顺序不同）
    'lan close_happy happy',   # 合法
    'lan ba happy',        # 根本没定义
    'ye aa care',          # 引擎也报过
    'lan happy',           # 合法
]
for spec in cases:
    w = known_tag_warning(spec, sym)
    print(f'\n{spec}')
    print('   ', (w[:110] + '…') if len(w) > 110 else (w or '（不吭声）'))
