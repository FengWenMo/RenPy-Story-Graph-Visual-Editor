# -*- coding: utf-8 -*-
"""真机扫描：所有 `show <tag> ...` 里**项目没声明过的属性词**。

用的就是卡片提示那条判据（`known_tag_warning`），只读。
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.story.dsl import Symbols                       # noqa: E402
from storygraph.ui_qt.sprite_hints import known_tag_warning    # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
GAME = ROOT / 'game'

reg = load_asset_registry(ROOT)
sym = Symbols(reg)


def declared_of(tag):
    try:
        return list(sym.declared_attrs_of(tag))
    except Exception:
        return []


SHOW_RE = re.compile(r'^\s*show\s+(.+?)\s*$')
CLAUSE = {'at', 'with', 'behind', 'as', 'onlayer', 'zorder'}
hits = []
by_word = Counter()
by_tag = defaultdict(set)

for path in sorted(GAME.rglob('*.rpy')):
    if '.git' in path.parts or 'saves' in path.parts:
        continue
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        continue
    for no, raw in enumerate(lines, 1):
        text = raw.split('#', 1)[0].rstrip()
        m = SHOW_RE.match(text)
        if not m:
            continue
        for part in m.group(1).split(';'):
            toks = []
            for word in part.split():
                if word in CLAUSE:
                    break
                toks.append(word)
            if not toks:
                continue
            spec = ' '.join(toks).rstrip(':')
            if spec.startswith(('expression ', 'screen ', 'layer ')) or \
                    any(ch in spec for ch in '()"\''):
                continue
            tag = spec.split()[0]
            if not declared_of(tag):
                continue
            w = known_tag_warning(spec, sym)
            if w:
                hits.append((path, no, spec, w))
                for word in spec.split()[1:]:
                    by_word[(tag, word.split('@')[0])] += 1
                    by_tag[tag].add(word.split('@')[0])

print(f'declared tags with unknown attrs: {len(by_tag)}')
print(f'total show lines flagged: {len(hits)}\n')
print('--- 按 (tag, 未声明词) 计数 ---')
for (tag, word), n in by_word.most_common():
    print(f'  {tag:6} {word:20} x{n}')
print('\n--- 前 40 条 ---')
for path, no, spec, w in hits[:40]:
    print(f'  {path.relative_to(ROOT)}:{no}: {spec}')
    print(f'      {w[:150]}')
