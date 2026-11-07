# -*- coding: utf-8 -*-
"""真机复现：切换「属性 2（outfit）」后，卡片提示条跟不跟着变。

只读，不写项目。用 offscreen，不弹窗。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('STORYGRAPH_NO_SESSION', '1')
sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from PySide6.QtWidgets import QApplication                    # noqa: E402

from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.story.dsl import Symbols                      # noqa: E402
from storygraph.story.layered_model import load_layer_model_cached  # noqa: E402
from storygraph.assets.vocabulary import EMPTY_VOCABULARY      # noqa: E402
from storygraph.ui_qt.sprite_grid import SpriteCardWidget      # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
app = QApplication.instance() or QApplication([])

reg = load_asset_registry(ROOT)
sym = Symbols(reg)
model = load_layer_model_cached(str(ROOT))


def _for_tag(_tag):
    return []


_for_tag.attr_groups = []
_for_tag.engine_facts = {}
_for_tag.layer_model = model
_for_tag.project_root = str(ROOT)
_for_tag.vocabulary = EMPTY_VOCABULARY


def make(spec):
    item = {'image': spec, 'expression': ' '.join(spec.split()[1:]),
            'position': '', 'at': '', 'speaking': False, 'order_index': 0,
            'extra_rows': []}
    card = SpriteCardWidget(item, symbols=sym, choice_groups_for=_for_tag)
    card._loader = None
    card.refresh()
    return card


def dump(tag, card):
    print(f'\n[{tag}] image={card.item.get("image")!r}')
    print(f'  hint={card._hint_text!r}')
    print(f'  thumb={card._thumb.text()!r}')
    print('  chips=' + ' | '.join(b.text() for b in card._slot_btns))


card = make('ye base_hand_down gratified')
dump('初始（源码里的错拼）', card)

for value in ('aa', 'base_handdown', 'base_swimsuit', ''):
    card = make('ye base_hand_down gratified')
    card._set_slot_value('outfit', value)
    dump(f'属性2 切到 {value!r}', card)

card = make('ye base_hand_down gratified')
card._set_slot_value('expressions', 'happy')
dump('只切属性1（表情）到 happy', card)

card = make('ye base_hand_down gratified')
card._set_slot_value('expressions', 'happy')
card._set_slot_value('outfit', 'base_handdown')
dump('先切属性1、再切属性2 到 base_handdown', card)
