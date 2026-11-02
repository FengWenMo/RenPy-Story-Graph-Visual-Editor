# -*- coding: utf-8 -*-
"""
探针 · 带美术代号（`A3`）的立绘卡长什么样（offscreen 截图）。

用户 2026-11-02："映射归映射……你也看不出来是 A3 呀"——这张图就是改完的样子：
标题是 `lan A3`（服装 A + 表情 3），chip 上是词汇表的名字，右键列表里带代号。

跑：python tools/_probe_sprite_code_card.py
出：tools/_sprite_code_card.png

⚠ 用**默认** Qt 平台跑（不要 `QT_QPA_PLATFORM=offscreen`）：离屏后端没有字体，
截出来全是方框（实测，见 `tools/_shot_custom_dialog.py` 的同一条经验）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout   # noqa: E402

from storygraph.assets import vocabulary as V                       # noqa: E402
from storygraph.core import engine_facts as EF                      # noqa: E402
from storygraph.ui_qt import theme                                  # noqa: E402
from storygraph.ui_qt.sprite_grid import SpriteCardWidget           # noqa: E402

ATTRS = {'lan': [{'group': 'expressions', 'attribute': 'angry'},
                 {'group': 'expressions', 'attribute': 'happy'},
                 {'group': 'expressions', 'attribute': 'shy'},
                 {'group': 'outfit', 'attribute': 'base_hand_up'},
                 {'group': 'outfit', 'attribute': 'clothes2'}]}
CANDS = {'lan': ['angry', 'happy', 'base_hand_up', 'clothes2', 'shy']}

VOCAB = V.Vocabulary((
    V.Term('costume', 'base_hand_up', label='第A身', code='A', char='lan'),
    V.Term('costume', 'clothes2', label='第二身', code='B', char='lan'),
    V.Term('expression', 'angry', label='愤怒', code='3', char='lan'),
    V.Term('expression', 'happy', label='开心', code='7', char='lan'),
    V.Term('expression', 'shy', label='娇羞', code='5', char='lan'),
))


class _Sym:
    """最小符号表（与 `tests/test_sprite_slots.py` 那份同形）。"""

    def __init__(self):
        self.registry = type('R', (), {'project_root': None})()
        self.project_root = None
        self.expressions = {}
        self.project_words = {'expression': set(), 'position': set()}

    def attrs_of_tag(self, tag):
        return ['angry', 'happy', 'shy', 'base_hand_up', 'clothes2']

    def expression_word_for(self, tag, attr):
        return ''


def _facts():
    return EF.build_facts({
        'attributes': [(EF.SRC_PROBE, ATTRS)],
        'candidates': [(EF.SRC_PROBE, CANDS)],
    }, collection={'parse_clean': True, 'probe_complete': True})


def _card(spec, vocabulary):
    def _for_tag(_tag):
        return []
    _for_tag.attr_groups = []
    _for_tag.engine_facts = _facts()
    _for_tag.vocabulary = vocabulary
    item = {'image': spec, 'expression': ' '.join(spec.split()[1:]),
            'position': 'bi11', 'at': '', 'speaking': False, 'order_index': 0,
            'extra_rows': []}
    card = SpriteCardWidget(item, symbols=_Sym(), choice_groups_for=_for_tag)
    card._loader = None
    card.refresh()
    return card


def main() -> int:
    app = QApplication([])
    try:                                    # 真界面的配色，截出来才像用户看到的
        app.setStyleSheet(theme.qss())
    except Exception as e:                  # noqa: BLE001 - 没样式也能看布局
        print('样式没套上：', e)
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(12, 12, 12, 12)
    left = _card('lan base_hand_up angry', VOCAB)
    right = _card('lan happy', VOCAB)       # 服装那一维空着 → 显示 `—`
    lay.addWidget(left)
    lay.addWidget(right)
    holder.resize(360, 700)
    holder.show()
    app.processEvents()
    out = Path(__file__).with_name('_sprite_code_card.png')
    holder.grab().save(str(out))
    print(f'标题：{left._name.text()!r} / {right._name.text()!r}')
    print(f'chip：{[b.text() for b in left._slot_btns]}')
    print(f'空着那维：{[b.text() for b in right._slot_btns]}')
    print(f'写到：{out}')

    # 右键那一列：这份列表是"真正挑表情"的地方，代号必须在这里认得出
    from storygraph.ui_qt.choice_picker import ChoicePickerDialog
    from storygraph.ui_qt.sprite_codes_view import slot_choices
    spec = str(left.item['image'])
    grouped = slot_choices(spec, 'lan', left._slot_rows(), VOCAB,
                           lambda g, v: left._slot_apply(spec, g, v))
    dlg = ChoicePickerDialog(
        None, f'选择立绘组合 —— {sum(len(g["values"]) for g in grouped)} 条'
              f'（{len(grouped)} 组）',
        [c for g in grouped for c in g['values']], spec, groups=grouped)
    dlg.resize(380, 560)
    dlg.show()
    app.processEvents()
    pick = Path(__file__).with_name('_sprite_code_picker.png')
    dlg.grab().save(str(pick))
    print(f'列表：{pick}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
