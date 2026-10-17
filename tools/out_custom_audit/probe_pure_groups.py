"""纯函数级探针：attr_groups / 引擎事实 / layeredimage 兜底 的标签与分组行为。

只读仓库源码；临时项目写在系统临时目录。不修改 storygraph/ tests/ docs/。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.spec_groups import parse_attr_groups       # noqa: E402
from storygraph.core import engine_facts as EF                       # noqa: E402
from storygraph.app import beats as B                                # noqa: E402
from storygraph.story.statement_registry import declare_attr_groups  # noqa: E402


class Sym:
    def __init__(self, root=None):
        self.project_root = str(root) if root else None
        self.expressions = {}
        self.project_words = {'expression': set(), 'position': set()}

    def attrs_of_tag(self, tag):
        return ['swim_suit', 'uniform', 'stand', 'wave', 'happy']

    def expression_word_for(self, tag, attr):
        return ''


def facts_two_groups():
    attrs = {
        'lan': [
            {'group': 'outfit', 'attribute': 'swim_suit'},
            {'group': 'outfit', 'attribute': 'uniform'},
            {'group': 'pose', 'attribute': 'stand'},
            {'group': 'pose', 'attribute': 'wave'},
            {'group': 'expressions', 'attribute': 'happy'},
        ],
    }
    cands = {'lan': ['swim_suit', 'uniform', 'stand', 'wave', 'happy']}
    return EF.build_facts({
        'attributes': [(EF.SRC_PROBE, attrs)],
        'candidates': [(EF.SRC_PROBE, cands)],
    }, collection={'parse_clean': True, 'probe_complete': True})


GROUPS = parse_attr_groups([
    {'name': 'outfit', 'label': '服装', 'options': ['swim_suit', 'uniform']},
    {'name': 'pose', 'label': '动作', 'options': ['stand', 'wave']},
])


def show(label, rows):
    print(f'\n[{label}] rows={len(rows)}')
    for r in rows:
        print('   group=%r label=%r value=%r display=%r options=%r'
              % (r.get('group'), r.get('label'), r.get('value'),
                 r.get('display'), r.get('options')))


print('=' * 78)
print('① 有引擎事实（outfit + pose 两个组）+ rules.json 改名服装/动作')
f = facts_two_groups()
show('facts=yes groups=服装/动作 spec=lan swim_suit stand',
     B.attr_slot_rows('lan swim_suit stand', Sym(), GROUPS, f))

print('\n' + '=' * 78)
print('② 没有引擎事实，退回 layeredimage（两个组 outfit/pose）')
tmp = Path(tempfile.mkdtemp(prefix='audit_pure_'))
game = tmp / 'game'
game.mkdir(parents=True)
(game / 'defs.rpy').write_text(
    'layeredimage lan:\n'
    "    image_format 'images/lan/{image}.png'\n"
    '    group outfit:\n'
    '        attribute swim_suit default:\n'
    "            'swim_suit'\n"
    '        attribute uniform:\n'
    "            'uniform'\n"
    '    group pose:\n'
    '        attribute stand default:\n'
    "            'stand'\n"
    '        attribute wave:\n'
    "            'wave'\n",
    encoding='utf-8')
from storygraph.story.layered_model import load_layer_model_cached  # noqa: E402
model = load_layer_model_cached(tmp)
print('   layeredimage 解析出的组：',
      [(g.name, list(g.attrs)) for g in model.images['lan'].groups])
show('facts=no layeredimage 两组 spec=lan swim_suit stand',
     B.attr_slot_rows('lan swim_suit stand', Sym(tmp), GROUPS, {},
                      layer_model=model))

print('\n' + '=' * 78)
print('③ 没有引擎事实，layeredimage 只有 outfit 一个组（rules.json 声明了 pose/动作）')
(tmp / 'game' / 'defs.rpy').write_text(
    'layeredimage lan:\n'
    "    image_format 'images/lan/{image}.png'\n"
    '    group outfit:\n'
    '        attribute swim_suit default:\n'
    "            'swim_suit'\n"
    '        attribute uniform:\n'
    "            'uniform'\n",
    encoding='utf-8')
model2 = load_layer_model_cached(tmp)
show('facts=no layeredimage 一组 spec=lan swim_suit',
     B.attr_slot_rows('lan swim_suit', Sym(tmp), GROUPS, {},
                      layer_model=model2))

print('\n' + '=' * 78)
print('④ 有引擎事实但只有一个组（outfit）—— rules.json 的 pose/动作 不能建格')
attrs = {'lan': [{'group': 'outfit', 'attribute': 'swim_suit'}]}
f1 = EF.build_facts({
    'attributes': [(EF.SRC_PROBE, attrs)],
    'candidates': [(EF.SRC_PROBE, {'lan': ['swim_suit']})],
}, collection={'parse_clean': True, 'probe_complete': True})
show('facts=一组 spec=lan swim_suit',
     B.attr_slot_rows('lan swim_suit', Sym(), GROUPS, f1))

print('\n' + '=' * 78)
print('⑤ 只写 tag、没有属性')
show('spec=lan', B.attr_slot_rows('lan', Sym(), GROUPS, facts_two_groups()))

print('\n' + '=' * 78)
print('⑥ expression_groups_for（右键候选那条路）在“没事实”时用声明分组')
for label, rows in [
    ('facts=no groups=服装/动作 spec=lan swim_suit',
     B.expression_groups_for('lan', Sym(), GROUPS, {})),
    ('facts=yes groups=服装/动作 spec=lan',
     B.expression_groups_for('lan', Sym(), GROUPS, facts_two_groups())),
]:
    print(f'\n[{label}] rows={len(rows)}')
    for r in rows:
        print('   group=%r label=%r values=%r'
              % (r.get('group'), r.get('label'), r.get('values')))

print('\n' + '=' * 78)
print('⑦ rules.json 删除/清空：有删除入口吗？')
manifest = tmp / 'storygraph.rules.json'
manifest.write_text('''{
  "attr_groups": [
    {"name": "outfit", "label": "服装", "options": ["swim_suit", "uniform"]},
    {"name": "pose", "label": "动作", "options": ["stand", "wave"]}
  ]
}
''', encoding='utf-8')
res = declare_attr_groups(tmp, [])
print('   declare_attr_groups(root, []) ->', res)
print('   文件还在吗：', manifest.exists())
print('   现有函数里有没有删除 attr_groups 的：',
      [n for n in dir(__import__('storygraph.story.statement_registry',
                                 fromlist=['x']))
       if 'attr' in n.lower() or 'group' in n.lower()])

print('\n' + '=' * 78)
print('⑧ 只删掉一栏再写回：被删的那栏会消失吗？')
from storygraph.ui_qt.column_editor import ColumnList               # noqa: E402
from PySide6.QtWidgets import QApplication                          # noqa: E402
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
app = QApplication.instance() or QApplication([])
cl = ColumnList()
cl.load_columns([{'name': 'outfit', 'label': '服装', 'options': ['swim_suit']},
                 {'name': 'pose', 'label': '动作', 'options': ['stand']}])
cl.remove_current()  # 删掉 pose 那张卡
rows = [{'name': r['key'], 'label': r['shown'],
         'options': [x for x in r['values'].replace('，', ',').split(',') if x]}
        for r in cl.rows() if r.get('key')]
print('   界面上还剩的栏：', [(r['name'], r['label']) for r in rows])
import json
before = json.loads(manifest.read_text(encoding='utf-8'))
res2 = declare_attr_groups(tmp, rows)
after = json.loads(manifest.read_text(encoding='utf-8'))
print('   declare_attr_groups ->', res2)
print('   写前 attr_groups：', [g['name'] for g in before.get('attr_groups', [])])
print('   写后 attr_groups：', [g['name'] for g in after.get('attr_groups', [])])
