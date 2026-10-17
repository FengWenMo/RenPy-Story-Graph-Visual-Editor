"""矩阵：服装/动作格在什么条件下出现、什么条件下整块消失。

    python tools/out_custom_audit/probe_disappear_matrix.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.spec_groups import parse_attr_groups       # noqa: E402
from storygraph.core import engine_facts as EF                       # noqa: E402
from storygraph.app import beats as B                                # noqa: E402

GROUPS = parse_attr_groups([
    {'name': 'outfit', 'label': '服装', 'options': ['swim_suit', 'uniform']},
    {'name': 'pose', 'label': '动作', 'options': ['stand', 'wave']},
])


class Sym:
    def __init__(self, root=None, attrs=None):
        self.project_root = str(root) if root else None
        self.expressions = {}
        self.project_words = {'expression': set(), 'position': set()}
        self._attrs = list(attrs or ['swim_suit', 'uniform', 'stand', 'wave'])

    def attrs_of_tag(self, tag):
        return list(self._attrs)

    def expression_word_for(self, tag, attr):
        return ''


def facts(attrs=None, cands=None):
    attrs = attrs if attrs is not None else {
        'lan': [
            {'group': 'outfit', 'attribute': 'swim_suit'},
            {'group': 'outfit', 'attribute': 'uniform'},
            {'group': 'pose', 'attribute': 'stand'},
            {'group': 'pose', 'attribute': 'wave'},
        ]}
    cands = cands if cands is not None else {
        'lan': ['swim_suit', 'uniform', 'stand', 'wave']}
    return EF.build_facts({
        'attributes': [(EF.SRC_PROBE, attrs)],
        'candidates': [(EF.SRC_PROBE, cands)],
    }, collection={'parse_clean': True, 'probe_complete': True})


def layer_model(groups):
    tmp = Path(tempfile.mkdtemp(prefix='audit_layer_'))
    game = tmp / 'game'
    game.mkdir(parents=True)
    lines = ['layeredimage lan:', "    image_format 'images/lan/{image}.png'"]
    for gname, attrs in groups:
        lines.append(f'    group {gname}:')
        for i, a in enumerate(attrs):
            lines.append(f'        attribute {a}' + (' default' if i == 0 else '') + ':')
            lines.append(f"            '{a}'")
    (game / 'defs.rpy').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    from storygraph.story.layered_model import load_layer_model_cached
    return load_layer_model_cached(tmp)


cases = []
cases.append(('spec 只有 tag、也没有分组来源', 'lan', False, None, None, {}))
cases.append(('spec 只有 tag + 引擎事实 2 组', 'lan', False, None, None,
              {'facts': facts()}))
cases.append(('spec 只有 tag + layeredimage 2 组', 'lan', False,
              [('outfit', ['swim_suit', 'uniform']),
               ('pose', ['stand', 'wave'])], None, {}))
cases.append(('引擎事实 2 组（正常）', 'lan swim_suit stand', False, None, None,
              {'facts': facts()}))
cases.append(('引擎事实只有 1 组', 'lan swim_suit', False, None, None,
              {'facts': facts(attrs={'lan': [{'group': 'outfit',
                                              'attribute': 'swim_suit'}]},
                              cands={'lan': ['swim_suit']})}))
cases.append(('没事实，layeredimage 2 组', 'lan swim_suit stand', False,
              [('outfit', ['swim_suit', 'uniform']),
               ('pose', ['stand', 'wave'])], None, {}))
cases.append(('没事实，layeredimage 1 组', 'lan swim_suit', False,
              [('outfit', ['swim_suit', 'uniform'])], None, {}))
cases.append(('没事实，也没有 layeredimage', 'lan swim_suit stand', False,
              None, None, {}))
cases.append(('symbols=None（拿不到资源表）', 'lan swim_suit stand', True,
              None, None, {'facts': facts()}))
cases.append(('引擎事实里 outfit 的候选 ∩ 组成员 = 空',
              'lan swim_suit stand', False, None, None,
              {'facts': facts(cands={'lan': ['stand', 'wave']})}))

print(f"{'场景':<32} {'行数':>4}  标签")
print('-' * 92)
for label, spec, no_sym, layer_groups, _unused, kw in cases:
    lm = layer_model(layer_groups) if layer_groups else None
    rows = B.attr_slot_rows(spec, None if no_sym else Sym(), GROUPS,
                            kw.get('facts'), layer_model=lm)
    print(f'{label:<32} {len(rows):>4}  '
          + '、'.join(f"{r['label']}={r['value'] or '—'}" for r in rows))
