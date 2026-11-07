# -*- coding: utf-8 -*-
"""
B 的真机验证：换装后预览图**真的变了**吗？

`D:\\千连万理\\sr_general`：
    lan aa happy        → 普通服装 = 不加衣服层 → 应该是一张 happy.png
    lan clothes2 happy  → 加了衣服层 + 裁剪过的脸 → 应该是**组合图**
    lan base_hand_up happy

判据（第三轮定的）：不只看"plan 里几层"，还要 `compose_sprite` 出来的**像素**
真的不同。
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from storygraph.app.layer_compose import compose_sprite        # noqa: E402
from storygraph.app.stage import sprite_plan                   # noqa: E402
from storygraph.story.layered_model import load_layer_model_cached  # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')

t0 = time.time()
model = load_layer_model_cached(str(ROOT))
print(f'模型加载 {time.time() - t0:.2f}s')

CASES = ['lan happy', 'lan aa happy', 'lan clothes2 happy',
         'lan base_hand_up happy', 'lan based']


def plan_of(spec):
    return sprite_plan(None, SimpleNamespace(image=spec), model, {}, str(ROOT))


print()
plans = {}
for spec in CASES:
    t = time.time()
    plan = plan_of(spec)
    plans[spec] = plan
    names = [Path(p).name for p in (plan.paths() if plan.ok else [])]
    print(f'{spec:22} ok={plan.ok!s:5} 层={len(names)} {names} '
          f'({time.time() - t:.2f}s)')
    if not plan.ok:
        print(f'{"":24}reason={getattr(plan, "reason", "")[:80]}')

print('\n--- 组合图（像素级）---')
paths = {}
for spec in ('lan aa happy', 'lan clothes2 happy', 'lan base_hand_up happy'):
    t = time.time()
    try:
        out = compose_sprite(plans[spec])
        paths[spec] = out
        print(f'{spec:22} -> {Path(out).name} ({time.time() - t:.2f}s)')
    except Exception as e:                       # noqa: BLE001
        print(f'{spec:22} -> 合成失败 {type(e).__name__}: {e}')

if len(paths) >= 2:
    from PIL import Image
    print('\n--- 像素抽样（左上 / 中部 / 底部）---')
    for spec, p in paths.items():
        with Image.open(p) as img:
            img = img.convert('RGBA')
            w, h = img.size
            pts = [(2, 2), (w // 2, h // 2), (2, h - 2)]
            print(f'{spec:22} size={w}x{h} '
                  f'{" ".join(str(img.getpixel(p_)) for p_ in pts)}')

    a = Path(paths.get('lan aa happy', '')).name
    b = Path(paths.get('lan clothes2 happy', '')).name
    print(f'\n普通 vs 换装：{a} vs {b} -> {"不同 ✓" if a != b else "相同 ✗"}')
