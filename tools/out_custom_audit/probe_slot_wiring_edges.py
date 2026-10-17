"""卡片 chip 接线的两个边角：换图后同数量分组、没有声明的引擎组名。

    python tools/out_custom_audit/probe_slot_wiring_edges.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from PySide6.QtWidgets import QApplication                          # noqa: E402

from storygraph.core import engine_facts as EF                       # noqa: E402


class Sym:
    def __init__(self):
        self.registry = None
        self.project_root = None
        self.expressions = {}
        self.project_words = {'expression': set(), 'position': set()}

    def attrs_of_tag(self, tag):
        return ['uniform', 'swim_suit', 'happy', 'sad']

    def expression_word_for(self, tag, attr):
        return ''


def facts_for(attrs):
    cands = {}
    for tag, rows in attrs.items():
        cands[tag] = [r['attribute'] for r in rows]
    return EF.build_facts({
        'attributes': [(EF.SRC_PROBE, attrs)],
        'candidates': [(EF.SRC_PROBE, cands)],
    }, collection={'parse_clean': True, 'probe_complete': True})


def make_card(spec, facts, groups=()):
    from storygraph.ui_qt.sprite_grid import SpriteCardWidget

    def _for_tag(_tag):
        return []

    _for_tag.engine_facts = facts
    _for_tag.attr_groups = list(groups)
    item = {'image': spec, 'expression': spec.split(' ', 1)[1]
            if ' ' in spec else '', 'position': '', 'at': '',
            'speaking': False, 'order_index': 0, 'extra_rows': []}
    card = SpriteCardWidget(item, symbols=Sym(), choice_groups_for=_for_tag)
    card._loader = None
    card.refresh()
    return card


app = QApplication.instance() or QApplication([])

print('=' * 80)
print('① 换图前后“组数相同、组名不同”：按钮闭包捕获的是旧组名')
facts = facts_for({
    'alpha': [{'group': 'outfit', 'attribute': 'uniform'},
              {'group': 'outfit', 'attribute': 'swim_suit'},
              {'group': 'expressions', 'attribute': 'happy'}],
    'beta': [{'group': 'expressions', 'attribute': 'happy'},
             {'group': 'outfit', 'attribute': 'uniform'},
             {'group': 'outfit', 'attribute': 'swim_suit'}],
})
card = make_card('alpha uniform happy', facts)
print('  换图前：', [b.text() for b in card._slot_btns], 'image=', card.item['image'])
card._apply_image_spec('beta happy uniform')
app.processEvents()
print('  换图后：', [b.text() for b in card._slot_btns], 'image=', card.item['image'])
# 点第一个按钮：它显示「表情：happy」，但闭包里是 alpha 时的 'outfit'
card._slot_btns[0].click()
app.processEvents()
print('  点第一个 chip 后：', 'image=', card.item['image'])
print('  → 期望只改表情，实际被改的是服装：',
      'swim_suit' in card.item['image'] and 'happy' in card.item['image'])

print('\n' + '=' * 80)
print('② 没有 rules.json 时，单个引擎组的组名直接当标题')
facts_one = facts_for({'solo': [{'group': 'outfit', 'attribute': 'uniform'}]})
card = make_card('solo uniform', facts_one, groups=())
print('  chips =', [b.text() for b in card._slot_btns])
print('  → 用户看到的是 outfit，而不是「服装 / 属性」')

print('\n' + '=' * 80)
print('③ 有 rules.json、但只有一条兜底声明时，无分组的 chip 叫「属性」')
from storygraph.authoring.spec_groups import parse_attr_groups     # noqa: E402
groups = parse_attr_groups([{'name': 'rest', 'label': '其他'}])
# 这个 tag 没有引擎分组 → rows=[] → fallback
facts_none = facts_for({'empty': []})
card = make_card('empty', facts_none, groups=groups)
print('  single =', card._expr_btn.text() if card._expr_btn else None)

print('\n' + '=' * 80)
print('④ 只有 rules.json 的分栏（没有引擎事实 / layeredimage）：未归组的词去哪了？')
groups = parse_attr_groups([
    {'name': 'outfit', 'label': '服装', 'options': ['uniform', 'swim_suit']},
])
card = make_card('lan swim_suit happy', facts={}, groups=groups)
print('  chips =', [b.text() for b in card._slot_btns])
print('  single =', card._expr_btn.text() if card._expr_btn else None)
print('  rows =', card._slot_rows())
print('  → happy 既不在 chip 上，也没有单独的「其他」格：',
      'happy' not in ' '.join(b.text() for b in card._slot_btns))
shot = HERE / 'shots' / '15_declared_only_hides_ungrouped.png'
shot.parent.mkdir(parents=True, exist_ok=True)
card.grab().save(str(shot))
print('  截图：', shot)
