"""卡片编辑器审计：服装格标题、服装/动作混在一起、服装格消失条件。

    python tools/out_custom_audit/probe_card_editor.py

输出：tools/out_custom_audit/shots/*.png + 控制台断言。
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

from audit_project import (EngineStub, build_audit_project,         # noqa: E402
                           write_facts)

SHOTS = HERE / 'shots'
SHOTS.mkdir(parents=True, exist_ok=True)
PROJ = HERE / 'proj_audit'

app = QApplication.instance() or QApplication([])
from storygraph.ui_qt.main_window import MainWindowQt                # noqa: E402


def open_window(root):
    script = Path(root) / 'game' / 'script' / 'ch01.rpy'
    win = MainWindowQt(EngineStub(root), script)
    win.resize(1500, 950)
    win.show()
    app.processEvents()
    win._set_page(win.PAGE_CARD)
    app.processEvents()
    return win


def cards_now(win):
    return list(win._panel._grid._cards)


def dump_chips(card):
    rows = card._slot_rows()
    return {
        'chips': [b.text() for b in card._slot_btns],
        'single': card._expr_btn.text() if getattr(card, '_expr_btn', None) else '',
        'thumbs': card._thumb.text(),
        'rows': [{'group': r.get('group'), 'label': r.get('label'),
                  'value': r.get('value'),
                  'options': list(r.get('options') or [])} for r in rows],
    }


def capture(win, row, tag):
    win._list.setCurrentRow(row)
    app.processEvents()
    print(f'\n--- 第 {row} 行：{win._list.item(row).text()!r}')
    cards = cards_now(win)
    print('    立绘卡数量：', len(cards))
    for i, c in enumerate(cards):
        info = dump_chips(c)
        print(f'    卡{i} image={c.item.get("image")!r}  缩略图={info["thumbs"]!r}')
        print('        chips=', info['chips'])
        print('        single=', info['single'])
        print('        rows=', info['rows'])
    full = SHOTS / f'{tag}_window.png'
    win.grab().save(str(full))
    print('    截图：', full)
    if cards:
        close = SHOTS / f'{tag}_card.png'
        cards[0].grab().save(str(close))
        print('    卡片特写：', close)
    return cards


print('=' * 80)
print('A. 有 rules.json、没有引擎事实（facts={}）→ 退回项目自己的 layeredimage')
root = build_audit_project(PROJ)
win = open_window(root)
capture(win, 0, '01_no_facts_correct_tag')
capture(win, 2, '02_no_facts_mixed_group')
win.close()

print('\n' + '=' * 80)
print('A2. **没有 rules.json**（或声明没被读到）→ 引擎组名直接当标题')
rules = root / 'storygraph.rules.json'
backup = root / 'storygraph.rules.json.audit-bak'
rules.rename(backup)
from storygraph.story import statement_registry as _SR
_SR.clear_cache()                       # 外部删了清单：缓存必须作废（见报告）
win = open_window(root)
capture(win, 0, '01b_no_rules_raw_outfit')
capture(win, 3, '01c_no_rules_single_group_raw')
win.close()
backup.rename(rules)
_SR.clear_cache()

# 新增立绘那张卡：provider 会不会跟着走？
root = build_audit_project(PROJ)
win = open_window(root)
grid = win._panel._grid
picked = {'spec': 'lan_tag uniform wave'}
grid._on_pick_image = lambda _cur: picked['spec']
before = len(cards_now(win))
grid._on_add()
app.processEvents()
new_card = cards_now(win)[-1] if len(cards_now(win)) > before else None
print('\n--- 「＋ 添加立绘」新建的那张卡')
if new_card is not None:
    print('    provider =', new_card._choice_groups_for)
    print('    chips =', [b.text() for b in new_card._slot_btns])
    print('    single =', new_card._expr_btn.text()
          if getattr(new_card, '_expr_btn', None) else None)
    new_card.grab().save(str(SHOTS / '03_new_card_no_provider.png'))
    print('    截图：', SHOTS / '03_new_card_no_provider.png')
win.close()

print('\n' + '=' * 80)
print('B. 有引擎事实（lan_tag 有 outfit + pose 两个组）')
write_facts(root)
win = open_window(root)
capture(win, 0, '04_with_facts_correct_tag')
capture(win, 1, '05_wrong_tag_lan')
capture(win, 2, '06_mixed_group')
capture(win, 3, '07_single_group')
win.close()

print('\n完成。截图目录：', SHOTS)
