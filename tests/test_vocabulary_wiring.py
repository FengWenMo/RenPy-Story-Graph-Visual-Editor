# -*- coding: utf-8 -*-
"""
tests/test_vocabulary_wiring.py
===============================
**词汇表接进界面了吗**（用户 2026-10-31 第 2/3/4/5 条 —— 四处下拉/列表）

`tests/test_vocabulary.py` 钉那层逻辑、`tests/test_vocabulary_ui.py` 钉立绘
chip 与「中文叫法」表；这个文件钉的是**另外四处**：

  ② 音乐那格：屏幕上是 `18 夜`，写进 beat 的仍是资产 ID `night`；
  ③ 背景下拉：屏幕上是 `bg1.1 教室`，写进 beat 的仍是 `bg classroom`；
  ④ 说话人：屏幕上是「爱莲」，写回剧本的仍是 tag `e`；
  ⑤ 参数列（转场 / 过场调用）：候选里有词汇表登记的 `Fade(...)`，
     显示成「闪白」，而且**能搜**（`choose_from` 的筛选两栏都命中）。

以及两件**回归护栏**：

  · 会话取词汇表真的读到了项目里那份映射（从前 `_vocabulary()` 把
    `mapping_cached()` 返回的元组当对象用，异常被吞 → 真机永远是空表）；
  · 查不到的名字一律原样（词汇表是补充，不是白名单）。
"""

from __future__ import annotations

import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402

pytest.importorskip('PySide6')

from storygraph.assets import vocabulary as V  # noqa: E402

#: 与 `tests/_ui_project.py` 那个临时工程对得上的映射（真文件，不是注入的）。
DATA = {
    'schema': 'storygraph.mapping/1',
    'name': '词汇表接线',
    'characters': {'e': {'display': '爱莲'}, 'm': {'display': '玛莉'}},
    'vocabulary': [
        {'kind': 'transform', 'id': 'i11', 'label': '1人，近处'},
        {'kind': 'music', 'id': 'night', 'code': '18', 'label': '夜'},
        {'kind': 'bg', 'id': 'classroom', 'code': 'bg1.1', 'label': '教室'},
        {'kind': 'cue', 'id': 'Fade(0.2, 0.0, 0.2)', 'label': '闪白'},
    ],
}


def _write_mapping(root, data=None):
    path = root / 'storygraph.mapping.json'
    path.write_text(json.dumps(data if data is not None else DATA,
                               ensure_ascii=False, indent=1),
                    encoding='utf-8')
    return path


def _vocab():
    return V.from_mapping(DATA)


# ======================================================================
#  会话：词汇表真的从项目文件来（回归护栏）
# ======================================================================

class TestSessionVocabulary:
    def test_会话读得到项目里的映射(self, qapp, ui_project, ui_window):
        """
        ⚠ 这条钉的是**真实的读取路径**（不是测试里注入一份词汇表）。

        从前 `window_card._vocabulary()` 写的是
        `state = mapping_cached(root); state.data if state.has_data() ...`
        —— 而 `mapping_cached` 返回的是 `(data, warnings, path)` 元组，
        `has_data` 不存在 → 走默认 `lambda: False` → **永远空表**，
        且不抛异常。判据里注入词汇表的那几条全绿，真机上却一个中文都没有。
        """
        _write_mapping(ui_project)
        vocab = ui_window._session.vocabulary()
        assert vocab.label('transform', 'i11') == '1人，近处'
        assert vocab.label('music', 'night') == '18 夜'
        assert vocab.label('bg', 'bg classroom') == 'bg1.1 教室'
        assert vocab.ident('cue', '闪白') == 'Fade(0.2, 0.0, 0.2)'

    def test_角色那段的显示名也进词汇表(self, qapp, ui_project, ui_window):
        _write_mapping(ui_project)
        vocab = ui_window._session.vocabulary()
        assert vocab.label('character', 'e') == '爱莲'

    def test_rules里calls的中文名也算_映射优先(self, qapp, ui_project,
                                              ui_window):
        """
        cue 有两个登记处：映射（正本）与 `rules.json` 的 `calls[].label`。

        规则：**两边都读，映射里的说法盖过 calls 的**（映射是"改名"的
        正本）；只有 calls 写了也照样能用 —— 因为当初声明这个调用就是在
        那儿写的，逼人再抄一遍只会多一份要同步的真源。
        """
        rules = ui_project / 'storygraph.rules.json'
        rules.write_text(json.dumps({'calls': [
            {'head': 'my_wipe', 'label': '擦除',
             'columns': [{'name': '转场', 'position': 0}]},
            {'head': 'Fade(0.2, 0.0, 0.2)', 'label': '规则里的名字',
             'columns': [{'name': '秒', 'position': 0}]},
        ]}, ensure_ascii=False), encoding='utf-8')
        from storygraph.story.statement_registry import clear_cache
        clear_cache()
        _write_mapping(ui_project)
        vocab = ui_window._session.vocabulary()
        assert vocab.label('cue', 'my_wipe') == '擦除'          # 只有 calls 有
        assert vocab.label('cue', 'Fade(0.2, 0.0, 0.2)') == '闪白'  # 映射优先

    def test_没有映射时是空表_界面与从前一样(self, qapp, ui_project,
                                            ui_window):
        vocab = ui_window._session.vocabulary()
        assert vocab.terms == ()
        assert vocab.label('transform', 'i11') == 'i11'


# ======================================================================
#  ② 音乐 / ③ 背景：下拉显示人话，存的是真值
# ======================================================================

class _FakeAssets:
    """只提供 `_refresh_asset_choices` 要的那几样（mixin 的最小宿主）。"""

    #: 与 `MainWindowQt.ASSET_FIELD_KINDS` 同一份（这里是"最小宿主"，
    #: 不继承主窗口；字段名对不上会当场 `KeyError`，所以抄一份没问题）
    ASSET_FIELD_KINDS = {'Scene': 'image', 'BGM': 'music', 'Voice': 'voice',
                         'SFX': 'sound'}

    def __init__(self, panel, vocab):
        self._panel = panel
        self._vocab = vocab
        self._session = self

    # —— 会话那一侧 ——
    def asset_choices(self, kind):
        return {
            'image': [('bg classroom', '背景：教室')],
            'music': [('night', '夜 · 2:31')],
            'sound': [('door', '门')],
            'voice': [('v_ling_01', '凌 1')],
        }[kind]

    def vocabulary(self):
        return self._vocab


@pytest.fixture
def panel(qapp):
    from storygraph.ui_qt.main_window import BeatCardEditor
    p = BeatCardEditor()
    yield p
    p.deleteLater()


def _refresh(panel, vocab):
    from storygraph.ui_qt.window_assets import WindowAssetsMixin
    host = _FakeAssets(panel, vocab)
    WindowAssetsMixin._refresh_asset_choices(host)
    return host


class TestAssetCombos:
    def test_音乐显示编号和名字_存的是资产ID(self, panel):
        _refresh(panel, _vocab())
        combo = panel._widgets['BGM']
        from storygraph.ui_qt.labeled_choices import select_value
        select_value(combo, 'night')
        assert combo.currentText() == '18 夜', combo.currentText()
        # **真值仍是资产 ID**（渲染器靠它翻出真实路径）
        assert panel._get_value('BGM') == 'night', panel._get_value('BGM')
        # 说明（原 tooltip）没被丢：候选项上还挂着
        row = [i for i in range(combo.count())
               if combo.itemText(i) == '18 夜'][0]
        assert '2:31' in str(combo.itemData(row, 3) or ''), combo.itemData(row, 3)

    def test_背景那格_图像spec也认(self, panel):
        """
        `Scene` 的值是**图像 spec**（`bg classroom`），不是裸 `classroom`。

        词汇表里登记的是背景身份（`classroom`）—— `Vocabulary.label` 会再试
        最后一个词，所以显示得出来；而**写回去的仍是整串 spec**。
        """
        _refresh(panel, _vocab())
        combo = panel._widgets['Scene']
        from storygraph.ui_qt.labeled_choices import select_value
        select_value(combo, 'bg classroom')
        assert combo.currentText() == 'bg1.1 教室', combo.currentText()
        assert panel._get_value('Scene') == 'bg classroom'

    def test_没登记的原样显示(self, panel):
        _refresh(panel, _vocab())
        combo = panel._widgets['Voice']          # SFX 那格在卡片上是输入框
        from storygraph.ui_qt.labeled_choices import select_value
        select_value(combo, 'v_ling_01')
        assert combo.currentText() == 'v_ling_01'
        assert panel._get_value('Voice') == 'v_ling_01'

    def test_手写的自由值不会被翻译弄坏(self, panel):
        """词汇表只管"认识的那几个"：手打的英文/路径必须原样写回。"""
        _refresh(panel, _vocab())
        combo = panel._widgets['BGM']
        combo.setEditText('bgm/我自己加的.ogg')
        assert panel._get_value('BGM') == 'bgm/我自己加的.ogg'

    def test_把屏幕上那句手打回来也认(self, panel):
        """用户照着屏幕敲 `18 夜`，不该被当成曲名写进剧本。"""
        _refresh(panel, _vocab())
        combo = panel._widgets['BGM']
        combo.setEditText('18 夜')
        assert panel._get_value('BGM') == 'night'

    def test_带参数的曲子也显示中文(self, panel):
        """
        **复合值**（有 `fadein` 参数时 `compose_audio_text()` 写的就是这种）：

            `[音乐] night fadein 1.0`  → 屏幕上 `[音乐] 18 夜 fadein 1.0`

        ⚠ 只翻壳里面那个名字，壳（`[音乐]`）和参数（`fadein 1.0`）原样留着；
        写回去仍是原串 —— 这条不成立的话，"有参数的曲子永远是英文"，
        看起来就像只接了一半。
        """
        _refresh(panel, _vocab())
        combo = panel._widgets['BGM']
        from storygraph.ui_qt.labeled_choices import select_value
        select_value(combo, '[音乐] night fadein 1.0')
        assert combo.currentText() == '[音乐] 18 夜 fadein 1.0', \
            combo.currentText()
        assert panel._get_value('BGM') == '[音乐] night fadein 1.0'

    def test_带壳的写法_词汇表那一层(self):
        vocab = _vocab()
        assert (vocab.label_in_text('music', '[音乐] night fadein 1.0')
                == '[音乐] 18 夜 fadein 1.0')
        assert vocab.label_in_text('bg', 'bg classroom@bgfit') == 'bg1.1 教室@bgfit'
        # 认不出的原样（壳也不许被动）
        assert vocab.label_in_text('music', '[停止音乐]') == '[停止音乐]'
        assert vocab.label_in_text('music', '[音乐] 别的歌 fadein 1') \
            == '[音乐] 别的歌 fadein 1'

    def test_翻到另一拍不会留下上一拍的当前值(self, panel):
        """挂了"当前值"那一行之后换成真候选，那一行要撤掉（下拉里不留残影）。"""
        _refresh(panel, _vocab())
        combo = panel._widgets['BGM']
        from storygraph.ui_qt.labeled_choices import select_value
        select_value(combo, '[音乐] night fadein 1.0')
        assert combo.currentText().startswith('[音乐]')
        select_value(combo, 'night')
        texts = [combo.itemText(i) for i in range(combo.count())]
        assert '18 夜' in texts, texts
        assert not [t for t in texts if t.startswith('[音乐]')], texts

    def test_音频停止伪值还在最前面(self, panel):
        _refresh(panel, _vocab())
        combo = panel._widgets['BGM']
        assert combo.itemText(0).startswith('（停止'), combo.itemText(0)


# ======================================================================
#  ④ 说话人：屏幕上是名字，写回去是 tag
# ======================================================================

class TestSpeaker:
    def test_说话人显示名字_存回tag(self, panel):
        from storygraph.ui_qt.labeled_choices import pairs
        panel.set_speakers(pairs(['e', 'm', 'narrator'],
                                 {'e': '爱莲', 'm': '玛莉'}), 'e')
        assert panel._speaker.currentText() == '爱莲'
        assert panel.speaker() == 'e'
        assert panel._get_value('Speaker') == 'e'      # 与 collect_all_fields 同路

    def test_换一个人(self, panel):
        from storygraph.ui_qt.labeled_choices import pairs
        panel.set_speakers(pairs(['e', 'm'], {'e': '爱莲', 'm': '玛莉'}), 'e')
        panel._speaker.setCurrentIndex(
            [i for i in range(panel._speaker.count())
             if panel._speaker.itemText(i) == '玛莉'][0])
        assert panel.speaker() == 'm'

    def test_手写一个新角色名(self, panel):
        from storygraph.ui_qt.labeled_choices import pairs
        panel.set_speakers(pairs(['e'], {'e': '爱莲'}), 'e')
        panel.set_speaker('临时角色')
        assert panel.speaker() == '临时角色', panel.speaker()

    def test_空说话人(self, panel):
        from storygraph.ui_qt.labeled_choices import pairs
        panel.set_speakers(pairs(['e'], {'e': '爱莲'}), '')
        assert panel.speaker() == ''


# ======================================================================
#  ⑤ 参数列（转场 / 过场调用）：候选里能挑、显示成中文、能搜
# ======================================================================

def _with_beat(tmp_path):
    """造一拍 `with dissolve` 的拍（`transition` 列 `choices_from=transform`）。"""
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    src = root / 'game' / 'ch.rpy'
    src.write_text('label a:\n    scene bg classroom\n    with dissolve\n'
                   '    "x"\n', encoding='utf-8')
    beats = beats_from_parse_result(
        IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)[0])
    beat = next(b for b in beats if 'with' in str(getattr(b, 'raw', '') or ''))
    return beat


class TestSpecArgChoices:
    def test_转场候选里有过场调用(self, tmp_path):
        """
        用户第 5 条："自定义一个过场动画的调用代码，用中文映射，卡片上能选中"。

        `with` 那一格是"过场动画"的落点 —— 词汇表里 `cue` 登记的东西必须
        出现在它的候选里，否则"能选中"无从谈起。
        """
        from storygraph.app import beats as B
        beat = _with_beat(tmp_path)
        plain = B.spec_arg_choices_for(beat, None)
        vocab = _vocab()
        got = B.spec_arg_choices_for(beat, None, vocab=vocab)
        assert 'Fade(0.2, 0.0, 0.2)' in got['transition'], got
        # ⚠ **原值**：候选里存的是能写进脚本的那串，不是「闪白」
        assert '闪白' not in got['transition']
        # 没给词汇表时逐字不变（老判据靠这一点）
        assert 'Fade(0.2, 0.0, 0.2)' not in plain.get('transition', [])

    def test_列到词汇表种类的映射(self):
        from storygraph.ui_qt.window_card import arg_kind_of
        assert arg_kind_of({'choices_from': 'transform'}) == ('transform', 'cue')
        assert arg_kind_of({'choices_from': 'music'}) == ('music',)
        assert arg_kind_of({'choices': ['a']}) == ()

    def test_卡片上显示中文_写回去是原值(self, panel, tmp_path):
        from storygraph.app import beats as B
        from storygraph.ui_qt.labeled_choices import labeled
        beat = _with_beat(tmp_path)
        vocab = _vocab()
        cols, values = B.spec_arg_fields_for(beat, None)
        raw = B.spec_arg_choices_for(beat, None, vocab=vocab)
        shown = {p: labeled(v, vocab, ('transform', 'cue'))
                 for p, v in raw.items()}
        # 当前值先换成过场调用，看它显示成什么
        values = dict(values)
        values[cols[0]['header']] = 'Fade(0.2, 0.0, 0.2)'
        panel.set_spec_args(cols, values, shown)
        combo = panel._spec_widgets[cols[0]['header']]
        assert combo.currentText() == '闪白', combo.currentText()
        assert panel._get_value(cols[0]['header']) == 'Fade(0.2, 0.0, 0.2)'

    def test_参数格有个能搜的候选按钮(self, panel, tmp_path):
        from storygraph.app import beats as B
        from storygraph.ui_qt.labeled_choices import labeled
        beat = _with_beat(tmp_path)
        vocab = _vocab()
        cols, values = B.spec_arg_fields_for(beat, None)
        raw = B.spec_arg_choices_for(beat, None, vocab=vocab)
        shown = {p: labeled(v, vocab, ('transform', 'cue'))
                 for p, v in raw.items()}
        panel.set_spec_args(cols, values, shown)

        # ① 「⋯」按钮真的长出来了（24px，与资产那三格同一形态）
        from PySide6.QtWidgets import QPushButton
        buttons = [b for b in panel.findChildren(QPushButton)
                   if (b.accessibleName() or '').startswith('挑选')]
        assert buttons, '参数格右边没有"可搜索"的入口'
        assert buttons[0].width() == 24, buttons[0].width()

        import storygraph.ui_qt.choice_picker as CP
        grabbed = {}

        def fake_choose(parent, title, choices, current='', groups=None):
            grabbed['choices'] = list(choices)
            grabbed['current'] = current
            return 'Fade(0.2, 0.0, 0.2)'

        orig = CP.choose_from
        CP.choose_from = fake_choose
        try:
            header = cols[0]['header']
            panel._pick_spec_value(header, panel._spec_widgets[header],
                                   shown[cols[0]['param']])
        finally:
            CP.choose_from = orig
        from storygraph.ui_qt.labeled_choices import display_of
        assert display_of(grabbed['choices'], 'Fade(0.2, 0.0, 0.2)') == '闪白'
        combo = panel._spec_widgets[cols[0]['header']]
        assert combo.currentText() == '闪白', '选完要显示中文'
        assert panel._get_value(cols[0]['header']) == 'Fade(0.2, 0.0, 0.2)'


# ======================================================================
#  搜索：中文名与原名都能搜到同一条
# ======================================================================

class TestSearchHitsBothSides:
    def test_选择器筛选两栏都命中(self):
        from storygraph.ui_qt.choice_picker import filter_choices
        items = [('i11', '1人，近处'), ('i12', '2人，远处')]
        assert filter_choices(items, '近处') == ['i11'], '中文名要能搜'
        assert filter_choices(items, 'i11') == ['i11'], '原名也要能搜'
        assert filter_choices(items, '') == ['i11', 'i12']

    def test_选择器返回的是真值(self, qapp):
        from PySide6.QtCore import Qt
        from storygraph.ui_qt.choice_picker import ChoicePickerDialog
        dlg = ChoicePickerDialog(None, title='选', choices=[
            ('i11', '1人，近处'), ('i12', '2人，远处')], current='i12')
        try:
            assert dlg._list.currentItem().text() == '2人，远处'
            assert (dlg._list.currentItem().data(Qt.UserRole) == 'i12')
            dlg._accept_current()
            assert dlg.value == 'i12', '屏幕上显示中文，交出去的必须是原名'
        finally:
            dlg.deleteLater()

    def test_词汇表自己的搜索两种写法都行(self):
        vocab = _vocab()
        assert [t.id for t in vocab.search('闪白')] == ['Fade(0.2, 0.0, 0.2)']
        assert [t.id for t in vocab.search('fade')] == ['Fade(0.2, 0.0, 0.2)']


# ======================================================================
#  位置 chip 的候选列表也显示人话（第 1 条的"能选中"那一半）
# ======================================================================

class TestPositionPicker:
    def test_位置候选列表里带着人话(self, qapp, monkeypatch):
        import storygraph.ui_qt.sprite_grid as SG
        from storygraph.ui_qt.sprite_grid import SpriteCardWidget

        card = SpriteCardWidget({'image': 'ling happy', 'position': 'i11'})
        provider = lambda tag: []                       # noqa: E731
        provider.attr_groups = []
        provider.engine_facts = {}
        provider.vocabulary = _vocab()
        card._choice_groups_for = provider
        # 候选从哪来不是这条要测的（那是 `choices_for_image` 的老判据），
        # 这里只钉"摆进列表时是不是人话"。
        monkeypatch.setattr(SG, 'choices_for_image',
                            lambda spec, symbols: {'expression': [],
                                                   'position': ['i11', 'd11']})

        import storygraph.ui_qt.choice_picker as CP
        grabbed = {}

        def fake_choose(parent, title, choices, current='', groups=None):
            grabbed['choices'] = list(choices)
            return ''

        monkeypatch.setattr(CP, 'choose_from', fake_choose)
        try:
            card._pick_from_list('position')
        finally:
            card.deleteLater()
        from storygraph.ui_qt.labeled_choices import display_of
        assert display_of(grabbed['choices'], 'i11') == '1人，近处'
        assert display_of(grabbed['choices'], 'd11') == 'd11'
