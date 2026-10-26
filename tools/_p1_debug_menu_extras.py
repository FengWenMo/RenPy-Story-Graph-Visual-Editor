#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""① debug：extras 菜单加标题，标题通道为什么不落地。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import patcher as P

src = '''label start:
    menu:
        set seen
        "开始":
            e "a"
    return
'''
orig = src.split('\n')
# 拍的区间：第 2 行 `    menu:`（1 基 first=2, last=2）
old = [orig[1]]
print('old_lines =', old)

# 修后单拍渲染（归一化前）：['menu:', '    "新标题"', '    set seen']
gen_raw = ['menu:', '    "新标题"', '    set seen']
gen = [P._norm(ln) for ln in gen_raw if str(ln).strip()]
print('gen_bodies =', gen)

fs = [P._squash(P._norm(ln)) for ln in orig]
print('file_squash =', fs)
print('_squash("set seen") =', P._squash('set seen'))

gb = P._body_lines(gen)
print('_body_lines =', gb)
mh = P._norm(old[0])
print('_mh =', mh, '| _gb[0] =', gb[0], '| equal =', P._norm(gb[0]) == mh)
caps = [x for x in gb[1:] if P._is_caption_line(x)]
extra = [x for x in gb[1:] if not P._is_caption_line(x)]
print('caps =', caps, '| extra =', extra)
srcs = {P._squash(y) for y in old} | set(fs)
print('extras_known =', all(P._squash(x) in srcs for x in extra))
print('len check:', len(old), '!=', len(caps) + 1, '→', len(old) != len(caps) + 1)

res = P._replace_matched(old, gen, allow_edit=True, file_squash=fs,
                         relax_speaker=False, child_pad='    ')
print()
print('== _replace_matched ==')
print('new_seg   =', res[0])
print('changed   =', res[1])
print('unlanded  =', res[2])
print('grown     =', res[3])
print('shrunk    =', res[4])
