"""
tests/test_editor_data_safety.py
================================
**逐句校对不许改数据** —— 卡片编辑器（`BeatEditorPanel` /
`ScriptTableEditorWindow`）的数据安全回归。

为什么单独开一个文件
--------------------
2026-09-30 用户报"说话人根本就不对！！！这是 chapter1_3"。拿真实数据
（`sr_general/game/chapter/chapter1_3.rpy`，305 拍）**逐句走一遍**当体检手段，
一口气挖出五类"静默改数据"的通道：

  1. 轮转把说话人写进**旁白拍** → `"旁白。"` 变成 `aaa "旁白。"`（32 处）
  2. 轮转把说话人写进**纯舞台拍**（`hide ling` 自成一行）→ 凭空多一句台词
  3. `beat.at` 被塞成"第一张立绘的位置"，渲染时又兜底套给**所有没写 `@`
     的立绘** → `show ye care` 变成 `show ye care at i11`（立绘错位）
  4. `Stage` 是结构化列的**投影**，被原样写回再 hydrate → `hide cg03`
     变成 `cg03; cg03`（305 拍里 11 拍被改写）
  5. 多属性立绘 `ling base_arms_akimbo sigh` 被"整串当成一个属性"归一
     → `sigh` 被吃掉（演出被改坏）

这些都不是"显示不对"，而是**改错数据**。所以这里逐条钉住。

验收标准（真实数据实测）：全章 305 拍逐句 `apply` 之后，`render_beats_to_rpy`
的产物**逐字节不变**。
"""

import pytest

from storygraph.story.beat_model import (
    Beat, KIND_EXTEND, KIND_MENU, KIND_NARRATION, KIND_SAY,
)
from storygraph.ui.beat_editor import (
    BeatEditorPanel, ScriptTableEditorWindow,
    normalize_expression_words, uniform_sprite_position,
)


@pytest.fixture
def symbols():
    from storygraph.story.beat_render import make_symbols
    return make_symbols(None)


class _Var:
    """最小 StringVar 替身。"""

    def __init__(self, v=''):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v or ''


# ==================================================================
#  一、翻页 = 纯导航（用户 2026-09-30 拍板）
# ==================================================================

class TestPagingIsPureNavigation:
    """
    > 回车不要自动填说话人！！！回车是下一张卡片的意思！！！

    上一轮只给轮转加了"三道闸"（不覆盖已有值 / 不写旁白 / 不写纯舞台拍），
    但用户要求的是**整个删掉** —— 只要它还挂在导航路径上，就有下次
    被改回来的机会。便利性改用「参与角色」双击（显式、只改当前拍）。
    """

    def _win(self, speakers):
        win = ScriptTableEditorWindow.__new__(ScriptTableEditorWindow)
        win.beats = [Beat(seq=i + 1, label='a', text='x', speaker=sp)
                     for i, sp in enumerate(speakers)]
        win._loaded_idx = 0
        win.apply_current_beat = lambda: None
        win._show_index = lambda i: setattr(win, '_loaded_idx', i)
        return win

    def test_rotation_helpers_are_deleted(self):
        from storygraph.ui import beat_editor
        assert not hasattr(ScriptTableEditorWindow, '_advance_speaker')
        assert not hasattr(ScriptTableEditorWindow, '_active_rotation')
        assert not hasattr(beat_editor, 'next_speaker')
        assert not hasattr(beat_editor, 'has_stage_content')

    def test_next_beat_is_pure_navigation(self):
        win = self._win(['dhy', '', 'lyj'])
        before = [(b.speaker, b.text, b.kind) for b in win.beats]
        win.next_beat()
        assert before == [(b.speaker, b.text, b.kind) for b in win.beats]
        assert win._loaded_idx == 1

    def test_prev_beat_is_pure_navigation(self):
        win = self._win(['dhy', '', 'lyj'])
        win._loaded_idx = 2
        before = [(b.speaker, b.text) for b in win.beats]
        win.prev_beat()
        assert before == [(b.speaker, b.text) for b in win.beats]
        assert win._loaded_idx == 1

    def test_explicit_fill_only_touches_the_current_beat(self):
        win = self._win(['', '', ''])
        win._highlight_speaker = lambda *a, **k: None
        win._set_note = lambda *a, **k: None
        win._panel = None
        win.fill_speaker_from_participant('lyj')
        assert [b.speaker for b in win.beats] == ['lyj', '', '']


# ==================================================================
#  二、位置不许"兜底套给别人"（立绘错位）
# ==================================================================

class TestUniformSpritePosition:
    """
    `beat.at` 是**整拍兜底位置**，渲染时套给所有没写 `@` 的 show
    （见 `beat_render._stage_lines`）。所以它只能是"大家都一样"的那个值。
    """

    def test_all_same_position_keeps_it(self):
        rows = [{'at': 'i11'}, {'at': 'i11'}]
        assert uniform_sprite_position(rows) == 'i11'

    def test_different_positions_must_not_leak(self):
        """
        真实数据实测（chapter1_3）：`show ye happy at i11` + `show ye care`
        曾经被写成 `show ye care at i11` —— 第二个人凭空多了个位置。
        """
        rows = [{'at': 'i11'}, {'at': ''}]
        assert uniform_sprite_position(rows) == '', \
            '位置不统一时必须留空，否则会兜底套到别的立绘头上'

    def test_two_different_positions_are_ambiguous(self):
        assert uniform_sprite_position([{'at': 'left'}, {'at': 'right'}]) == ''

    def test_single_sprite(self):
        assert uniform_sprite_position([{'at': 'f11'}]) == 'f11'

    def test_no_rows(self):
        assert uniform_sprite_position([]) == ''
        assert uniform_sprite_position(None) == ''


# ==================================================================
#  三、多属性立绘不能被压成一个属性
# ==================================================================

class TestMultiAttributeSprites:
    """
    `show ling base_arms_akimbo sigh` 是合法的多属性写法。卡片上的表情词
    本来就是逐属性拼的（`base_arms_akimbo 无语`），所以归一时必须**逐词**。
    整串归一 = 把 `sigh` 静默吃掉（2026-09-30 真实数据：chapter1_3 第 91 拍）。
    """

    def test_expr_with_unknown_word_does_not_match_a_thing(self, symbols):
        """`base_arms_akimbo` 没有中文词 → 原样保留；`无语` → `sigh`。"""
        words = normalize_expression_words(
            'base_arms_akimbo 无语', 'ling base_arms_akimbo sigh', symbols)
        assert words == ['base_arms_akimbo', 'sigh'] or words == [
            'base_arms_akimbo', '无语'], \
            f'逐词归一必须保住两个属性，实际 {words!r}'

    def test_single_word_is_unchanged(self, symbols):
        assert normalize_expression_words('oh', 'ling oh', symbols) == ['oh']

    def test_empty_expression(self, symbols):
        assert normalize_expression_words('', 'ling oh', symbols) == []
        assert normalize_expression_words('   ', 'ling oh', symbols) == []


# ==================================================================
#  四、写回：Stage 是投影（不许自己吃自己）
# ==================================================================

class TestCollectBeatProjectionSafety:
    """
    `collect_beat` 的字段范围与"投影不回灌"契约。
    用 `__new__` 建面板（不开窗口），只喂它需要的属性。
    """

    def _panel(self, beat, values, adv_open=False, stage_loaded=''):
        p = BeatEditorPanel.__new__(BeatEditorPanel)
        p._beat = beat
        p._adv_open = adv_open
        p._stage_loaded = stage_loaded
        p._sprite_widgets = []
        p._chapter_var = None
        p._condition_var = None
        p._wget = lambda name: values.get(name, '')
        p._symbols = lambda: __import__(
            'storygraph.story.beat_render', fromlist=['make_symbols']
        ).make_symbols(None)
        return p

    def test_scene_bgm_voice_comment_are_written_without_advanced(self):
        """
        **主区四项永远可编辑，就必须永远写回**。此前它们和高级区共用一个
        `_adv_open` 闸门，收起时改了**静默丢失**（默认就是收起）。
        """
        beat = Beat(label='c1', text='x', scene='bg bg1_2')
        values = {'Speaker': 'lyj', 'Text': 'x', 'Scene': 'bg bg9_5',
                  'BGM': '[音乐] bgm/bgm03.mp3 fadein 2.0',
                  'Voice': 'v_ling_001', 'Comment': '# 备注'}
        b = self._panel(beat, values).collect_beat()
        assert b.scene == 'bg bg9_5'
        assert b.bgm == '[音乐] bgm/bgm03.mp3 fadein 2.0'
        assert b.voice == 'v_ling_001'
        assert b.comment == '# 备注'

    def test_advanced_fields_are_left_alone_when_collapsed(self):
        """高级区收起 → 核心字段一个字都不许动（"改台词把转场弄没了"）。"""
        beat = Beat(label='c1', text='x', stage='[转场] dissolve',
                    sfx='se1', code='$ f()', kind=KIND_SAY)
        values = {'Speaker': 'jc', 'Text': 'y'}
        b = self._panel(beat, values).collect_beat()
        assert b.sfx == 'se1'
        assert b.code == '$ f()'

    def test_stage_projection_is_not_reabsorbed(self):
        """
        `Stage` 是结构化列的投影。把投影当输入再 hydrate 一次 →
        `hide cg03` 变成 `cg03; cg03`（逐句校对必然踩中）。
        """
        beat = Beat(label='c1', text='x', show='ling oh@i11', hide='cg03',
                    scene='bg bg6_2',
                    stage='[显示] 凌跃捷 oh at i11; hide cg03; scene bg bg6_2')
        values = {'Speaker': 'lyj', 'Text': 'x',
                  'Stage': beat.stage}          # 控件里就是那份投影
        b = self._panel(beat, values, stage_loaded=beat.stage).collect_beat()
        assert b.hide == 'cg03', f'退场指令被回灌成 {b.hide!r}'
        assert b.show == 'ling oh@i11', f'立绘被回灌成 {b.show!r}'

    def test_hand_written_stage_is_authoritative(self):
        """
        用户真的在 Stage 框里改了字 → 以人写的为准，并且结构化舞台字段
        要让位（否则"旧投影 + 新文本"会叠加成两份指令）。
        """
        beat = Beat(label='c1', text='x', show='ling oh', hide='cg03')
        values = {'Speaker': 'lyj', 'Text': 'x', 'Stage': 'hide cg03'}
        b = self._panel(beat, values, stage_loaded='show ling oh; hide cg03'
                        ).collect_beat()
        assert b.stage == 'hide cg03'
        assert b.hide == 'cg03', '按人写的文本重新解析应得到同一条退场'
        assert b.show == '', '结构化立绘要让位给手写的高级舞台'


# ==================================================================
#  五、滚动容器（右侧面板"看不完全"）
# ==================================================================

class TestScrollFrameWheel:
    """滚轮的增量换算（纯计算，不需要真窗口）。"""

    def _steps(self, **ev):
        from storygraph.ui.scroll_frame import ScrollFrame
        return ScrollFrame._wheel_steps(ScrollFrame.__new__(ScrollFrame),
                                        type('E', (), ev)())

    def test_windows_one_notch_down(self):
        assert self._steps(delta=-120, num=None) > 0

    def test_windows_one_notch_up(self):
        assert self._steps(delta=120, num=None) < 0

    def test_x11_buttons(self):
        assert self._steps(delta=0, num=4) < 0     # 上滚
        assert self._steps(delta=0, num=5) > 0     # 下滚

    def test_zero_delta_is_ignored(self):
        assert self._steps(delta=0, num=None) == 0
