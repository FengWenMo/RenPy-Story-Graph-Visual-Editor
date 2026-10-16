"""
判据 · **立绘卡一维一格**（2026-10-12）
======================================

用户报的现场（`show ye base_handdown gratified`）：

> 它明明就有两个动作……可是为什么编辑器不报呢？如果它不报的话……那么我就
> 永远无法调节 show base_handdown+表情了，我就永远只能调默认的了

病根在卡片：**一个格子**里塞了"服装 + 表情"两个词 ——
  · 当前值（两词）不在候选表（单词）里 → 点 chip 走"固定当前值"那一支
    → 用户看到的是**点了没反应**；
  · 从下拉里挑一个词 → `tag + 那个词` 重拼 → **服装被吃掉**。

修法：维度由**引擎分组**给，一维一格。这里钉住卡片的四条落地：
  ① **引擎给了几个组就几个 chip**（2026-10-16 收紧）：一个词写的拍也有服装格
     —— 那一格显示 `—`，点一下才固定到这一拍。认不出引擎分组的项目走老路；
  ② 点服装那格 → **表情不丢**；
  ③ 点表情那格 → **服装不丢**；
  ④ 右键候选 = **完整路径**（`base_handdown gratified`），按服装分栏。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import engine_facts as EF                            # noqa: E402


YE_ATTRS = {
    'ye': [{'group': 'expressions', 'attribute': 'ahah'},
           {'group': 'expressions', 'attribute': 'gratified'},
           {'group': 'expressions', 'attribute': 'happy'},
           {'group': 'outfit', 'attribute': 'base_handdown'},
           {'group': 'outfit', 'attribute': 'base_swimsuit'}],
}
YE_CANDS = {'ye': ['ahah', 'gratified', 'happy',
                   'base_handdown', 'base_swimsuit']}


def _facts():
    return EF.build_facts({
        'attributes': [(EF.SRC_PROBE, YE_ATTRS)],
        'candidates': [(EF.SRC_PROBE, YE_CANDS)],
    }, collection={'parse_clean': True, 'probe_complete': True})


class _Sym:
    """最小符号表（真机形状：属性里还混着掩码 / 切图这类内部件）。"""

    def __init__(self):
        self.registry = type('R', (), {'project_root': None})()
        self.project_root = None
        self.expressions = {}
        self.project_words = {'expression': set(), 'position': set()}

    def attrs_of_tag(self, tag):
        return ['ahah', 'gratified', 'happy', 'base_handdown', 'base_swimsuit',
                'base_handdown_mask', '_hd_cut']

    def expression_word_for(self, tag, attr):
        return ''


def _provider(tag):
    """窗口那条注入通道：**属性挂在函数上**（见 `window_card._sync_choice_groups`）。"""
    def _for_tag(_tag):
        return []
    _for_tag.attr_groups = []
    _for_tag.engine_facts = _facts()
    return _for_tag


def _card(spec, provider=True):
    from storygraph.ui_qt.sprite_grid import SpriteCardWidget
    item = {'image': spec, 'expression': ' '.join(spec.split()[1:]),
            'position': '', 'at': '', 'speaking': False, 'order_index': 0,
            'extra_rows': []}
    card = SpriteCardWidget(item, symbols=_Sym(),
                            choice_groups_for=_provider(spec.split()[0])
                            if provider else None)
    card._loader = None
    card.refresh()
    return card


class TestSlotChips:
    def test_two_dimensions_get_two_chips(self, qapp):
        card = _card('ye base_handdown gratified')
        texts = [b.text() for b in card._slot_btns]
        assert len(texts) == 2, texts
        assert any('base_handdown' in t for t in texts), texts
        assert any('gratified' in t for t in texts), texts

    def test_one_dimension_still_gets_every_engine_group(self, qapp):
        """
        ⚠⚠ **口径改了**（2026-10-16 用户实测报的 bug）：

        > 我明明配置了全局的 show 表情……为何一张卡片有服装，另一张卡片却没有啊？

        从前这条断言"一个词 → 退回单个表情 chip"（`_slot_btns == []`），
        于是 `ye happy` 连**服装格都没有**、`ye base_handdown gratified` 却有两格
        —— 同一套配置，卡片之间不一致。
        现在：引擎给了 ≥2 个组 → 每组一格；这一拍没写的维度显示 `—`
        （点一下才固定到这一拍），候选照样给全。
        """
        card = _card('ye happy')
        labels = [b.text() for b in card._slot_btns]
        assert len(labels) == 2, labels
        assert labels[0].endswith('happy'), labels
        assert labels[1].endswith('—'), ('没写的维度该显示 —', labels)
        # 点一下空格子 → 补上第一个候选，且**不吃掉表情**
        card._set_slot_value('outfit', 'base_handdown')
        assert card.item['image'] == 'ye happy base_handdown', card.item['image']

    def test_without_engine_facts_it_falls_back_to_the_layeredimage(
            self, qapp, tmp_path):
        """
        ⚠⚠ **用户 2026-10-16 报的"粘在一起了"就是这个**：

        没采过引擎事实（`facts={}`）时，从前 `window_card._sync_choice_groups`
        直接 `set_choice_groups_provider(None)` → 卡片走单格 → 两个词粘在
        一个 chip 里（`表情：ye base_handdown gratified`）。
        但这时候**项目自己的 `layeredimage` 就写着分组** —— 那一档必须走得到。

        判据：只给项目根（没有引擎事实）→ 照样一维一格。
        """
        game = tmp_path / 'game'
        game.mkdir(parents=True, exist_ok=True)
        (game / 'defs.rpy').write_text(
            'layeredimage ye:\n'
            "    image_format 'images/ye/{image}.png'\n"
            '    group expressions:\n'
            '        attribute gratified default:\n'
            "            'gratified'\n"
            '    group outfit:\n'
            '        attribute base_handdown default:\n'
            "            'base_handdown'\n",
            encoding='utf-8')

        from storygraph.ui_qt.sprite_grid import SpriteCardWidget
        item = {'image': 'ye base_handdown gratified',
                'expression': 'base_handdown gratified', 'position': '',
                'at': '', 'speaking': False, 'order_index': 0, 'extra_rows': []}

        def _for_tag(_tag):
            return []
        _for_tag.attr_groups = []
        _for_tag.engine_facts = {}                 # ← 没采过
        _for_tag.project_root = str(tmp_path)      # ← 但有项目

        card = SpriteCardWidget(item, symbols=_Sym(), choice_groups_for=_for_tag)
        card._loader = None
        card.refresh()
        texts = [b.text() for b in card._slot_btns]
        assert len(texts) == 2, ('还是一格（粘在一起）', texts)
        assert any('gratified' in t for t in texts), texts
        assert any('base_handdown' in t for t in texts), texts
        # 点服装那一格 → 表情不许丢（走上同一条路）
        card._set_slot_value('outfit', 'base_swimsuit')
        assert 'gratified' in card.item['image'], card.item['image']
        assert 'base_swimsuit' in card.item['image'], card.item['image']

    def test_no_provider_means_the_old_path(self, qapp):
        card = _card('ye base_handdown gratified', provider=False)
        assert card._slot_btns == []
        assert 'base_handdown gratified' in card._expr_btn.text()

    def test_changing_the_outfit_keeps_the_expression(self, qapp):
        """**用户那个 bug 的正面判据**：换服装不许吃掉表情。"""
        card = _card('ye base_handdown gratified')
        card._set_slot_value('outfit', 'base_swimsuit')
        assert 'gratified' in card.item['image'], card.item['image']
        assert 'base_swimsuit' in card.item['image'], card.item['image']
        assert card.item['expression'] == card.item['image'].split(' ', 1)[1]

    def test_changing_the_expression_keeps_the_outfit(self, qapp):
        card = _card('ye base_handdown gratified')
        card._set_slot_value('expressions', 'ahah')
        assert 'base_handdown' in card.item['image'], card.item['image']
        assert 'gratified' not in card.item['image'], card.item['image']

    def test_cycling_a_slot_walks_that_group_only(self, qapp):
        card = _card('ye base_handdown gratified')
        row = next(r for r in card._slot_rows() if r['group'] == 'outfit')
        card._cycle_slot('outfit')
        nxt = next(r for r in card._slot_rows() if r['group'] == 'outfit')
        assert nxt['value'] != row['value'], (row, nxt)
        assert 'gratified' in card.item['image'], card.item['image']

    def test_change_notifies_the_owner(self, qapp):
        """改完必须通知窗口重投影 —— 否则"看得见的"和"存下去的"会不一致。"""
        seen = []
        from storygraph.ui_qt.sprite_grid import SpriteCardWidget
        item = {'image': 'ye base_handdown gratified',
                'expression': 'base_handdown gratified', 'position': '',
                'at': '', 'speaking': False, 'order_index': 0, 'extra_rows': []}
        card = SpriteCardWidget(item, symbols=_Sym(),
                                on_change=lambda _c: seen.append(1),
                                choice_groups_for=_provider('ye'))
        card._loader = None
        card._set_slot_value('expressions', 'ahah')
        assert seen, '卡片改了却没通知窗口'


class TestSlotComboPicker:
    def test_combo_options_are_full_specs_grouped_by_outfit(self, qapp):
        """
        右键候选 = **完整路径**（按组给、每条都带着另一个维度），不是"一堆单词"。

        ⚠ 不钉词序：`apply_attr_slot` 是"没命中就追加"的语义，同一组里
        "换"与"加"会给出两种顺序，**两种都合法**（引擎按组取值，与顺序无关）。
        这里钉的是**结构不变量**：每条都能跑、两个维度都在、服装两个值都覆盖到。
        """
        card = _card('ye base_handdown gratified')
        grabbed = {}

        def _fake_choose(parent, title, choices, current='', groups=None):
            grabbed['choices'] = list(choices)
            grabbed['groups'] = list(groups or ())
            return choices[-1] if choices else ''

        import storygraph.ui_qt.choice_picker as CP
        orig = CP.choose_from
        CP.choose_from = _fake_choose
        try:
            card._pick_from_list('expression')
        finally:
            CP.choose_from = orig
        picks = grabbed['choices']
        assert picks, grabbed
        outfits = {'base_handdown', 'base_swimsuit'}
        for spec in picks:
            words = set(spec.split())
            assert 'ye' in words, spec
            assert words & outfits, spec                     # 服装那一维在
            assert words & {'ahah', 'gratified', 'happy'}, spec   # 表情那一维在
        assert {w for s in picks for w in s.split()} >= outfits, picks
        # 假选择器返回最后一条 → 卡片存下的就是**那一条完整路径**
        chosen = picks[-1]
        assert card.item['image'] == chosen, (card.item, chosen)
        assert card.item['expression'] == chosen.split(' ', 1)[1]
