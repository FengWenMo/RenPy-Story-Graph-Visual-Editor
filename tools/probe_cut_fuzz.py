# -*- coding: utf-8 -*-
"""
模糊探针：随机造 pattern / 行，找 `_exact_spans` 的**结构性**违规
================================================================================

不看"切得对不对"（那需要语义），只看**任何输入都必须成立**的东西：

  I1 不抛异常
  I2 spans 的区间必须 a < b <= 词数，且两两不重叠（一个词不能进两个格子）
  I3 `exact=True` 时 `best_spans == spans`（成功那一屏与结果必须同源）
  I4 失败时 `best_spans` 也不许重叠 / 越界（它是要显示的）
  I5 `match_line` 的 `cells` 数量必须等于 `pattern` 数量（否则界面串位）
  I6 幂等：同一输入两次，结果完全一样
  I7 `ok=True` 时 `cells` 里每个非空值必须能由 tokens 连续片段拼出（不许造字）
  I8 报错定位矛盾：`reason` 说"第 N 格没词"，而 `best_spans` 里第 N 格非空

跑法::

    python tools/probe_cut_fuzz.py [轮数]
"""

from __future__ import annotations

import random
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P        # noqa: E402

WORDS = ['lan', 'ye', 'happy', 'sad', 'base_swimsuit', 'clothes2',
         'dirty', 'junk', '小明', '泳装', '开心', 'base', 'swimsuit',
         'at', 'left', 'x', 'y', 'ab', 'cd', 'abcd']

KINDS = ['keyword', 'variable', 'choice', 'optional', 'value']


def rand_pattern(rng):
    n = rng.randint(1, 5)
    out = []
    for i in range(n):
        kind = rng.choice(KINDS)
        opts = rng.sample(WORDS, rng.randint(0, 4))
        slot = {'kind': kind, 'column': f'c{i}', 'options': opts}
        if kind in ('keyword', 'optional'):
            slot['text'] = rng.choice(WORDS)
        if rng.random() < 0.4:
            slot['multi'] = True
        if rng.random() < 0.3:
            slot['limit'] = True
        out.append(slot)
    return out


def rand_line(rng):
    n = rng.randint(0, 7)
    toks = [rng.choice(WORDS) for _ in range(n)]
    sep = rng.choice([' ', '  ', '\t', ''] if n else [''])
    line = sep.join(toks)
    r = rng.random()
    if r < 0.1 and line:
        line = '"' + line + '"'
    elif r < 0.15:
        line = line.replace(' ', '', 1)
    return line


def spans_ok(spans, ntok):
    seen = set()
    for si, span in (spans or {}).items():
        a, b = span
        if not (0 <= a < b <= ntok):
            return f'span 越界 {si}:{span} (词数 {ntok})'
        words = set(range(a, b))
        if words & seen:
            return f'span 重叠 {si}:{span}'
        seen |= words
    return ''


def main(rounds=8000):
    rng = random.Random(20261017)
    bad = []
    slow = []
    for k in range(rounds):
        pattern = rand_pattern(rng)
        line = rand_line(rng)
        try:
            t0 = time.time()
            got = P.match_line(pattern, line)
            dt = time.time() - t0
            if dt > 0.2:
                slow.append((dt, line, len(pattern)))
            slots = P.parse_pattern(pattern)
            options = [o for s in slots for o in (s.get('options') or [])]
            options += [s['text'] for s in slots if s.get('text')]
            tokens = P._tokens(line, options=options)
            raw = P.assign_tokens(tokens, slots)
            got2 = P.match_line(pattern, line)
        except Exception:
            bad.append(('异常', line, pattern, traceback.format_exc()))
            continue
        problems = []
        if len(got['cells']) != len(pattern):
            problems.append(f'cells 数量 {len(got["cells"])} != pattern '
                            f'{len(pattern)}')
        if raw['exact'] and raw['best_spans'] != raw['spans']:
            problems.append('exact 时 best_spans != spans')
        msg = spans_ok(raw['spans'], len(tokens))
        if msg:
            problems.append(msg)
        if not raw['exact']:
            msg2 = spans_ok(raw.get('best_spans'), len(tokens))
            if msg2:
                problems.append('best ' + msg2)
        if got != got2:
            problems.append('不幂等')
        if got['ok']:
            # 真判据：每格显示的值必须**恰好**是它 span 里那几个词拼出来的，
            # 且没 span 的格子必须是空（否则界面显示与分配结果不同源）。
            for i, c in enumerate(got['cells']):
                span = raw['spans'].get(i)
                want = ' '.join(tokens[span[0]:span[1]]) if span else ''
                if c['text'] != want:
                    problems.append(f'显示不同源 第{i + 1}格 {c["text"]!r} != {want!r}')
        # I8：reason 说某格没词，但显示的格子里有值
        if not got['ok'] and '该有一个词' in got['reason']:
            shown = raw.get('best_spans') or {}
            for c in got['cells']:
                pass
            import re
            m = re.search(r'第 (\d+) 格', got['reason'])
            if m:
                i = int(m.group(1)) - 1
                if shown.get(i):
                    problems.append(f'定位矛盾：reason 说第 {i + 1} 格没词，'
                                    f'best 却给了 {shown[i]}')
        if problems:
            bad.append((problems, line, pattern, got['reason'], raw, got['cells']))

    print(f'跑了 {rounds} 轮，可疑 {len(bad)} 条，慢样本 {len(slow)} 条')
    from collections import Counter
    kinds = Counter()
    for item in bad:
        if item[0] == '异常':
            kinds['异常'] += 1
        else:
            for p in item[0]:
                kinds[p.split(' ')[0].split('：')[0]] += 1
    print('分类：', dict(kinds))
    for item in bad[:12]:
        print('-' * 70)
        print(item[1] if item[0] == '异常' else item[0])
        print('  line =', item[1] if item[0] != '异常' else item[1])
        print('  pattern =', item[2] if item[0] != '异常' else item[2])
        if item[0] == '异常':
            print(item[3])
        else:
            print('  reason =', item[3])
            print('  spans =', item[4]['spans'], ' best =', item[4].get('best_spans'))
            print('  cells =', [(c['column'], c['text']) for c in item[5]])
    # 定位矛盾单独再列一遍（它是"报错冤人"的直接证据）
    print('\n########## 定位矛盾（reason 说某格没词，界面却给了它值） ##########')
    shown = 0
    for item in bad:
        if item[0] == '异常':
            continue
        if not any('定位矛盾' in p for p in item[0]):
            continue
        print('-' * 70)
        print('  line =', item[1])
        print('  pattern =', [(s.get('kind'), s.get('column'), s.get('text'),
                             s.get('options')) for s in item[2]])
        print('  reason =', item[3])
        print('  best =', item[4].get('best_spans'),
              ' cells =', [(c['column'], c['text']) for c in item[5]])
        shown += 1
        if shown >= 10:
            break
    if slow:
        slow.sort(reverse=True)
        print('最慢：', slow[:5])


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
