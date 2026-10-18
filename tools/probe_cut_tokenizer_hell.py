# -*- coding: utf-8 -*-
"""
探针：把 2026-10-17「分词 + 代价路由」的**实际行为**打出来
================================================================================

用户在验收前点名的四条隐患，逐条给证据（不猜、只看真实返回值）：

  ① 中文"无空格才回退按候选切" —— 混合空格 / 最长匹配吞边界 / 贪心破坏全局可切；
  ② 引号包裹的连词（`"base swimsuit"`）—— 到底认不认；
  ③ `COST_*` 魔法数字 —— 脏数据下会不会偷词、平局怎么断；
  ④ 报错定位（`_assign_report`）与界面显示（`best_spans`）是否自相矛盾。

跑法::

    python tools/probe_cut_tokenizer_hell.py

⚠ 只读：不写任何项目文件。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P        # noqa: E402

ROLES = ['lan', 'ye', 'mm']
OUTFIT = ['clothes2', 'base_swimsuit', 'base_hands_down']
FACE = ['afraid', 'angry', 'happy', 'sad']

SHOW = {'kind': 'keyword', 'text': 'show'}


def three(*, role_multi=True, outfit_multi=True, face_multi=False):
    return [SHOW,
            {'kind': 'variable', 'column': '角色', 'options': ROLES,
             'multi': role_multi},
            {'kind': 'choice', 'column': '服装', 'options': OUTFIT,
             'multi': outfit_multi},
            {'kind': 'choice', 'column': '表情', 'options': FACE,
             'multi': face_multi}]


def run(title, pattern, line, *, show_tokens=False):
    print('=' * 78)
    print(f'### {title}')
    print(f'    line = {line!r}   pattern = {len(pattern)} 格')
    if show_tokens:
        opts = []
        for s in P.parse_pattern(pattern):
            opts.extend(s.get('options') or [])
            if s.get('text'):
                opts.append(s['text'])
        print(f'    tokens = {P._tokens(line, options=opts)!r}')
    got = P.match_line(pattern, line)
    print(f'    ok     = {got["ok"]}')
    print(f'    cells  = {[(c["column"], c["text"]) for c in got["cells"]]!r}')
    print(f'    reason = {got["reason"]!r}')
    slots = P.parse_pattern(pattern)
    raw = P.assign_tokens(P._tokens(line, options=[
        o for s in slots for o in (s.get('options') or [])] or None),
        slots)
    print(f'    exact  = {raw["exact"]}  spans={raw["spans"]!r}')
    print(f'    best   = {raw.get("best_spans")!r}')
    print(f'    bad_kw = {raw["bad_kw"]}  rejected = {raw["rejected"]!r}'
          f'  unfilled = {raw["unfilled"]!r}  used = {raw["used"]}')
    return got, raw


def main():
    print('### 基本盘：用户现场那一行（对照）')
    run('现场：三格各归各位', three(), 'show lan base_swimsuit happy')

    # ---------------------------------------------------------------- ①
    print('\n\n##########  ① 中文无空格 / 混合空格  ##########')
    cn = [SHOW,
          {'kind': 'variable', 'column': '角色', 'options': ['小明', '小红']},
          {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
          {'kind': 'choice', 'column': '表情', 'options': ['开心', '害羞']}]
    run('①-1 整行无空格（对照组，应当切对）', cn, 'show小明泳装开心',
        show_tokens=True)
    run('①-2 只要出现一个空格，中文段就不再按候选切', cn, 'show 小明泳装 开心',
        show_tokens=True)
    run('①-3 更常见的混写：句尾粘连', cn, 'show 小明 泳装开心',
        show_tokens=True)

    # 三格版本（真实 pattern 有表情格），把表情改成"可有多个"看会不会静默错值
    loose = [SHOW,
             {'kind': 'variable', 'column': '角色', 'options': ['小明']},
             {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服'],
              'multi': True},
             {'kind': 'choice', 'column': '表情', 'options': ['开心'],
              'multi': True}]
    run('①-3b 同样一行，但后两格"可有多个" → 是否静默给出错值', loose,
        'show 小明 泳装开心', show_tokens=True)

    # 最长匹配吞边界：两个候选在同一位置都能匹配
    overlap = [{'kind': 'variable', 'column': '角色', 'options': ['小明']},
               {'kind': 'choice', 'column': '服装', 'options': ['小明穿', '泳装']}]
    run('①-4 重叠候选（小明 / 小明穿）：最长匹配把"穿"吞进角色', overlap,
        '小明穿泳装', show_tokens=True)

    # 贪心最长匹配破坏"全局可切"
    greedy = [{'kind': 'choice', 'column': 'A', 'options': ['ab']},
              {'kind': 'choice', 'column': 'B', 'options': ['cd']},
              {'kind': 'choice', 'column': 'C', 'options': ['abcd']}]
    run('①-5 贪心分词造出假失败：abcd 切成 [ab, cd] 本来可解', greedy,
        'abcd', show_tokens=True)

    # ---------------------------------------------------------------- ②
    print('\n\n##########  ② 引号包裹  ##########')
    quoted = [SHOW,
              {'kind': 'variable', 'column': '角色', 'options': ROLES},
              {'kind': 'choice', 'column': '服装',
               'options': ['base swimsuit', 'base_hand_up']},
              {'kind': 'choice', 'column': '表情', 'options': FACE}]
    run('②-1 引号把含空格的候选包起来（用户明确要求）', quoted,
        'show lan "base swimsuit" happy', show_tokens=True)
    run('②-2 不加引号的写法（候选本身含空格，已知要支持）', quoted,
        'show lan base swimsuit happy', show_tokens=True)
    run('②-3 单引号', quoted, "show lan 'base swimsuit' happy",
        show_tokens=True)

    # ---------------------------------------------------------------- ③
    print('\n\n##########  ③ 代价数字  ##########')
    # 3a：脏词该给谁 —— 角色 multi 前面有自由文本
    dirty = [SHOW,
             {'kind': 'variable', 'column': '角色', 'options': ROLES,
              'multi': True},
             {'kind': 'choice', 'column': '服装', 'options': OUTFIT,
              'multi': True},
             {'kind': 'choice', 'column': '表情', 'options': FACE}]
    run('③-1 两个"可有多个"格之间夹一个谁也不认识的词', dirty,
        'show lan dirty base_swimsuit happy')
    run('③-2 两个不认识 + 一个别人的候选（看偷词是否划算）', dirty,
        'show lan dirty1 dirty2 base_swimsuit happy')
    # 3b：同价平局（COST_EXTRA == COST_UNKNOWN == 2）
    run('③-3 平局：多吃一个脏词 vs 让下一格吃', dirty,
        'show lan dirty happy')
    # 3c：候选同时也是别人格的候选
    shared = [SHOW,
              {'kind': 'variable', 'column': '角色', 'options': ['lan'],
               'multi': True},
              {'kind': 'choice', 'column': '服装',
               'options': ['happy', 'base_swimsuit'], 'multi': True},
              {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    run('③-4 happy 同时是服装与表情的候选', shared,
        'show lan base_swimsuit happy')

    # 3d：跳过垃圾词时会不会静默丢词（跳过的词不进 spans，但必须算 leftover）
    print('\n--- ③-5 跳过垃圾词时的 spans / used ----')
    slots = P.parse_pattern([SHOW,
                             {'kind': 'variable', 'column': '角色'},
                             {'kind': 'choice', 'column': '服装',
                              'options': ['base_swimsuit']}])
    got = P.assign_tokens(['show', 'lan', 'junk1', 'junk2', 'base_swimsuit'],
                          slots)
    print(f'    exact={got["exact"]} spans={got["spans"]!r} used={got["used"]}'
          f'  （tokens 共 5 个）')
    print(f'    被跳过的词有没有进 spans：'
          f'{[w for i, w in enumerate(["show","lan","junk1","junk2","base_swimsuit"]) if not any(a <= i < b for a, b in got["spans"].values())]!r}')

    # ---------------------------------------------------------------- ④
    print('\n\n##########  ④ 报错定位 vs 界面显示  ##########')
    got, raw = run('④-1 关键字拼错（bad_kw 指向第 1 格）', three(),
                   'shw lan base_swimsuit happy')
    print('    ⚠ 检查：reason 点第 1 格，界面 best_spans 怎么排的')
    got, raw = run('④-2 限死格遇到候选外的词 + 末尾多词', three(),
                   'show lan wrong happy extra')
    print('    ⚠ 检查：reason 说的和 best 里的排法是否同一件事')
    # 旧贪心把后一格候选吞进前一格，看失败屏是否复现
    got, raw = run('④-3 失败屏是否又出现"一格吃两个词"', three(),
                   'show lan base_swimsuit happy extra')
    for i, (a, b) in sorted((raw.get('best_spans') or {}).items()):
        print(f'    best 第 {i + 1} 格 = {a}..{b}')

    # ---------------------------------------------------------------- ⑤
    print('\n\n##########  ⑤ 我顺手怀疑的：性能 / 递归  ##########')
    many = [SHOW] + [
        {'kind': 'choice', 'column': f'C{i}', 'options': [f'v{i}'],
         'multi': True} for i in range(10)]
    words = ['show'] + [f'w{j}' for j in range(20)]
    t0 = time.time()
    got = P.assign_tokens(words, P.parse_pattern(many))
    print(f'    10 个连续多值格 × 20 词：exact={got["exact"]}'
          f'  耗时={time.time() - t0:.3f}s')
    long_words = ['show'] + [f'w{j}' for j in range(300)]
    t0 = time.time()
    got = P.assign_tokens(long_words, P.parse_pattern(three()))
    print(f'    300 词 × 3 格：exact={got["exact"]}  耗时={time.time() - t0:.3f}s')


if __name__ == '__main__':
    main()
