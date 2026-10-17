"""
修复后的自定义模块截图（offline / offscreen Qt）
=================================================

跑法：

    python tools/probe_custom_module_after.py

产出 `tools/out_custom_audit/after_fix/`：
  · `card_split_groups.png`  —— 卡片：服装 / 动作分格（不显示内部组名 outfit）
  · `dialog_columns.png`     —— 第③页：分栏 + 「项目里的组…」入口 + 拆分开关
  · `dialog_manage.png`      —— 第④页：清单里实际写着什么 + 删除/清空/重置

这不是 pytest：它只负责"生成一张能看的图"和打印断言，跑不成立刻看得见。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

OUT = ROOT / 'tools' / 'out_custom_audit' / 'after_fix'
OUT.mkdir(parents=True, exist_ok=True)


def _project() -> Path:
    root = Path(tempfile.mkdtemp(prefix='sg_custom_after_'))
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'defs.rpy').write_text(
        'layeredimage lan_tag:\n'
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
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'weather', 'label': '天气'}],
        'attr_groups': [
            {'name': 'outfit', 'label': '服装',
             'options': ['swim_suit', 'uniform']},
            {'name': 'pose', 'label': '动作',
             'options': ['stand', 'wave'], 'split': True},
        ],
        'host_hooks': {'trust': True},
        'my_note': '用户自己的键，原样保留',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return root


def _facts():
    from storygraph.core import engine_facts as EF
    attrs = {'lan_tag': [
        {'group': 'outfit', 'attribute': 'swim_suit'},
        {'group': 'outfit', 'attribute': 'uniform'},
        {'group': 'outfit', 'attribute': 'stand'},
        {'group': 'outfit', 'attribute': 'wave'},
    ]}
    cands = {'lan_tag': ['swim_suit', 'uniform', 'stand', 'wave']}
    return EF.build_facts({
        'attributes': [(EF.SRC_PROBE, attrs)],
        'candidates': [(EF.SRC_PROBE, cands)],
    }, collection={'parse_clean': True, 'probe_complete': True})


class _Sym:
    def attrs_of_tag(self, _tag):
        return ['swim_suit', 'uniform', 'stand', 'wave']

    def expression_word_for(self, _tag, _attr):
        return ''


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from storygraph.authoring.spec_groups import AttrGroup
    from storygraph.story import statement_registry as SR
    from storygraph.ui_qt.dialog_learn import (CustomStatementDialog,
                                               PAGE_COLUMNS, PAGE_MANAGE)
    from storygraph.ui_qt.sprite_grid import SpriteCardWidget

    app = QApplication.instance() or QApplication([])
    root = _project()
    facts = _facts()

    def _for_tag(_tag):
        return []
    _for_tag.attr_groups = [
        AttrGroup(name='outfit', label='服装',
                  options=['swim_suit', 'uniform']),
        AttrGroup(name='pose', label='动作',
                  options=['stand', 'wave'], split=True),
    ]
    _for_tag.engine_facts = facts

    item = {'image': 'lan_tag swim_suit stand', 'expression': 'swim_suit stand',
            'position': '', 'at': '', 'speaking': False,
            'order_index': 0, 'extra_rows': []}
    card = SpriteCardWidget(item, symbols=_Sym(), choice_groups_for=_for_tag)
    card._loader = None
    card.refresh()
    texts = [b.text() for b in card._slot_btns]
    print('卡片格子：', texts)
    assert any(t.startswith('服装：') for t in texts), texts
    assert any(t.startswith('动作：') for t in texts), texts
    assert not any('outfit' in t for t in texts), texts
    card.resize(460, 280)
    card.show()
    app.processEvents()
    card.grab().save(str(OUT / 'card_split_groups.png'))

    dlg = CustomStatementDialog(
        None, project_root=root, discovered=[],
        declared_groups=SR.StatementRegistry.for_project(root).attr_groups)
    dlg.resize(1000, 680)
    dlg.show()
    app.processEvents()
    dlg.tabs.setCurrentIndex(PAGE_COLUMNS)
    app.processEvents()
    dlg.grab().save(str(OUT / 'dialog_columns.png'))
    print('第③页分栏：', [r['key'] for r in dlg.columns_editor.rows()])
    print('项目组名：', dlg.columns_editor._known_keys)

    dlg.tabs.setCurrentIndex(PAGE_MANAGE)
    app.processEvents()
    dlg.grab().save(str(OUT / 'dialog_manage.png'))
    text = dlg.manage_view.toPlainText()
    print('第④页：\n' + text)
    assert 'weather' in text and '服装' in text and '动作' in text
    assert 'host_hooks' in text

    print('OK →', OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
