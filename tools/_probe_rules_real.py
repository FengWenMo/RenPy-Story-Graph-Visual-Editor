# -*- coding: utf-8 -*-
"""只读探针：真机项目的 `show` 到底写了什么（给 rules.json 的候选列找真值）。

用法：
    python tools/_probe_rules_real.py [项目根]

输出（同时也是 `tools/_probe_rules_real.json` 的内容）：
  · chars      —— `show` 第一词的频次（角色列的真值）
  · attrs      —— 每个角色用过的属性词，按 layeredimage 分组分成 expr/outfit/undeclared
  · codes      —— 每个角色在注释里出现过的字母（A/B…）与它对应到的服装
  · files      —— 每个文件里 `show` 的行数（看哪几章是重心）

为什么要有它：rules.json 的候选列如果靠人凭记忆抄，下一次改剧本就对不上了。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.sprite_codes import (          # noqa: E402
    _CODE_RE, _SHOW_RE, _attr_words, _script_texts, parse_layeredimage)

DEFAULT_ROOT = Path(r'D:\千连万理\sr_general')


def probe(root: Path) -> dict:
    texts = _script_texts(root)
    known = {}
    for text in texts.values():
        for tag, groups in parse_layeredimage(text).items():
            slot = known.setdefault(tag, {'expr': [], 'outfit': []})
            for key in ('expr', 'outfit'):
                for name in groups[key]:
                    if name not in slot[key]:
                        slot[key].append(name)
    chars = Counter()
    attrs = defaultdict(Counter)
    codes = defaultdict(Counter)          # 角色 -> 字母 -> 属性
    files = {}
    for rel, text in sorted(texts.items()):
        n = 0
        pending = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith('#'):
                found = _CODE_RE.findall(line)
                if found:
                    pending = (found[-1][0].upper(), found[-1][1])
                continue
            m = _SHOW_RE.match(raw)
            if not m:
                pending = None
                continue
            n += 1
            char, rest = m.group(2), m.group(3)
            chars[char] += 1
            words = _attr_words(rest)
            for w in words:
                attrs[char][w] += 1
            if pending:
                letter = pending[0]
                # 只有"词属于这个角色的服装组"或"谁都没声明过"才算服装
                declared = set(known.get(char, {}).get('expr', ())) | set(
                    known.get(char, {}).get('outfit', ()))
                outfits = set(known.get(char, {}).get('outfit', ()))
                cand = [w for w in words if w in outfits
                        or (w not in declared)]
                if cand:
                    codes[char][f'{letter}->{cand[-1]}'] += 1
                pending = None
        files[rel] = n
    return {
        'root': str(root),
        'chars': dict(chars.most_common()),
        'attrs': {c: dict(v.most_common()) for c, v in attrs.items()},
        'known': known,
        'codes': {c: dict(v.most_common()) for c, v in codes.items()},
        'files': files,
    }


def main(argv) -> int:
    root = Path(argv[1]) if len(argv) > 1 else DEFAULT_ROOT
    data = probe(root)
    print('=' * 72)
    print(f'项目：{root}')
    print(f'剧本文件 {len(data["files"])} 个 / show 共 {sum(data["chars"].values())} 行')
    print('-- show 第一词（角色列） --')
    for c, n in data['chars'].items():
        print(f'  {c:>10}  {n:>5}')
    print('-- 每个角色用过的属性词 --')
    for c, words in data['attrs'].items():
        k = data['known'].get(c, {})
        expr = set(k.get('expr', ()))
        outfit = set(k.get('outfit', ()))
        tag = []
        for w in words:
            if w in expr:
                tag.append(f'{w}(表情)')
            elif w in outfit:
                tag.append(f'{w}(服装)')
            else:
                tag.append(f'{w}(未声明)')
        print(f'  {c:>10}: ' + '  '.join(tag))
    print('-- 注释代号（字母 → 服装属性） --')
    for c, votes in data['codes'].items():
        print(f'  {c:>10}: ' + '  '.join(f'{k}×{v}' for k, v in votes.items()))
    dump = Path(__file__).with_name('_probe_rules_real.json')
    dump.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n已写：{dump}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
