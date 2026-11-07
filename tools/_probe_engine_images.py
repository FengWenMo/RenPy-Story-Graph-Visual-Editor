# -*- coding: utf-8 -*-
"""验 `engine_images`：跑一次 → 写缓存 → 立刻读回（key 必须一致）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from storygraph.assets import engine_images as EI          # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
SDK = Path(r'D:\software\renpy-8.4.1-sdk')

print('缓存文件:', EI.cache_path(ROOT).name,
      '| 用 str 传进来也是同一个:',
      EI.cache_path(str(ROOT)).name == EI.cache_path(ROOT).name)

t0 = time.time()
got = EI.verdicts(ROOT, sdk=str(SDK), timeout=900, force=True)
print(f'跑一次 {time.time() - t0:.1f}s ok={got["ok"]} count={got.get("count")}')

hit = EI.cached(ROOT)
print('立刻读缓存:', hit.get('ok'), '| cached:', hit.get('cached'),
      '| 条数:', len(hit.get('invalid') or []))

keys = EI.bad_keys(ROOT)
print('归一化键数:', len(keys))
for probe in ('lan|aa|happy', 'len aa happy'.replace('len', 'lan'),  # noqa: SIM905
              'lan|close_happy|happy', 'lan|aa|close_happy'):
    print(f'  {probe:26} -> {probe in keys}')
