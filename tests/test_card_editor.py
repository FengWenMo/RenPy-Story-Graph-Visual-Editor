"""
tests/test_card_editor.py
=========================
「4 项卡片」的纯逻辑测试。

用户拍板的编辑范式（`docs/定位重审.md` §4 问题四）：

    卡片 = **一次做一次选择**；表格 = 批量改值。
    卡片上策划真正会改的只有 **4 样**：
        说话人 / 台词 / 表情 / 位置
    其余字段灰显或折叠——不是删掉（程序和老策划还要用），
    而是不让它们占第一眼的注意力。

前两样是 Beat 的真实属性（`speaker` / `text`）；后两样是 `show`
（`ling happy@left`）这一格的**投影**。直接让策划编辑 show 字符串，
等于要求他记住 `图@位置` 语法——那正是要消灭的负担。

这个文件锁住那对投影函数与面板字段的正确性（tkinter 是桩，测纯逻辑）。
"""

import pytest

from storygraph.ui.beat_editor import (
    BeatEditorPanel, apply_expression, apply_position, apply_sprite_rows,
    beat_expression_word, beat_position_word, beat_sprite_rows,
    beat_to_fields, expression_choices, fields_to_beat,
    inherited_state_hint, normalize_expression, normalize_position,
    position_choices,
)
from storygraph.story.script_table import Beat, make_symbols, render_beats_to_rpy


@pytest.fixture
def symbols():
    return make_symbols(None)


class _V:
    """最小 StringVar 替身（只需要 get/set）。"""

    def __init__(self, v=''):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v or ''


# ==================================================================
#  一、卡片只留 4 项；其余在高级区
# ==================================================================

class TestCardShape:
    def test_primary_fields_cover_the_daily_basics(self):
        """
        主区 = **每一拍都要看 / 要填**的字段。

        2026-09-29：从主区摘掉 `Expression` / `Position`——它们不属于主区，
        只在立绘卡内编辑（卡上的 chip，1 次点击）。
        2026-09-30：按用户要求把 `Scene` / `BGM` / `Voice` / `Comment`
        **从折叠的高级区提上来**——"背景 / 音乐 / 语音 / 备注"本来就是每一拍的
        基本信息，藏在折叠区里等于没有。
        """
        assert BeatEditorPanel.PRIMARY_FIELDS == [
            'Speaker', 'Text', 'Scene', 'BGM', 'Voice', 'Comment']

    def test_primary_and_advanced_are_disjoint_and_cover_everything(self):
        """两项加一起必须覆盖所有面板字段，且不许重叠——
        重了意味着同一个值能被两个控件改（互相打架）。"""
        primary = set(BeatEditorPanel.PRIMARY_FIELDS)
        advanced = set(BeatEditorPanel.ADVANCED_FIELDS)
        assert primary & advanced == set()
        assert set(BeatEditorPanel._ORDER) == primary | advanced

    def test_programmer_fields_are_not_primary(self):
        """
        真正**程序向**的东西不该占第一眼。

        注：`BGM` 2026-09-30 已按用户要求提到主区（策划每天要填音乐），
        不再算"程序向"；这里留下的才是该折叠的。
        """
        for name in ('CodeBlock', 'Stage', 'SFX', 'Kind', 'Notes'):
            assert name not in BeatEditorPanel.PRIMARY_FIELDS
            assert name in BeatEditorPanel.ADVANCED_FIELDS

    def test_advanced_is_collapsed_by_default(self):
        assert BeatEditorPanel.ADVANCED_FIELDS, '高级区不该是空的'


# ==================================================================
#  二、表情 / 位置：show 的投影
# ==================================================================

class TestExpressionProjection:
    def test_read_expression_from_show(self, symbols):
        b = Beat(seq=1, label='t', text='x', show='ling happy@left')
        assert beat_expression_word(b, symbols) == '开心'

    def test_lossy_word_falls_back_to_raw_attribute(self, symbols):
        """
        **实测到的事故**（2026-09-28）：

            show ling close_happy@left
              → `expression_word_for` 靠子串把 close_happy 匹配到 happy → 显示「开心」
              → 写回时「开心」→ `happy`，**close_ 丢了**
              → `show ling happy at left`   ← 演出静默变了

        所以显示词必须**自证无损**：翻回来要逐字等于原属性，
        做不到就显示原属性名（丑但准）。
        """
        b = Beat(seq=1, label='t', text='x', show='ling close_happy@left')
        assert beat_expression_word(b, symbols) == 'close_happy'

    def test_expression_roundtrip_is_byte_exact(self, symbols):
        """读出来再写回去，show 必须逐字节不变（无损才准显示人话）。"""
        for show in ('ling happy@left', 'ling close_happy@left',
                     'ling sad', 'ling base_arms_akimbo close_happy@i11'):
            b = Beat(seq=1, label='t', text='x', show=show)
            word = beat_expression_word(b, symbols)
            back = apply_expression(
                show, normalize_expression(word, b, symbols))
            assert back == show, f'{show!r} → 显示{word!r} → 写回{back!r}'

    def test_read_position_from_show(self, symbols):
        b = Beat(seq=1, label='t', text='x', show='ling close_happy@left')
        assert beat_position_word(b, symbols) == '左'

    def test_position_falls_back_to_beat_at(self, symbols):
        """`show` 里没写 `@位置` 时用 Beat 的 at（面板只给一个位置格）。"""
        b = Beat(seq=1, label='t', text='x', show='ling close_happy',
                 at='left')
        assert beat_position_word(b, symbols) == '左'

    def test_no_sprite_means_empty(self, symbols):
        b = Beat(seq=1, label='t', text='x', show='')
        assert beat_expression_word(b, symbols) == ''
        assert beat_position_word(b, symbols) == ''

    def test_unknown_expression_shows_raw_attribute(self, symbols):
        """翻不成中文也得让人看见是什么，不能显示空白。"""
        b = Beat(seq=1, label='t', text='x', show='ling zzz_unknown')
        assert beat_expression_word(b, symbols) == 'zzz_unknown'


class TestApplyExpression:
    def test_replaces_expression_keeps_position(self):
        assert apply_expression('ling happy@left', 'sad') == 'ling sad@left'

    def test_empty_clears_expression_only(self):
        assert apply_expression('ling happy@left', '') == 'ling@left'

    def test_keeps_other_sprites(self):
        out = apply_expression('ling happy@left; mary smile@right', 'sad')
        assert out == 'ling sad@left; mary smile@right'

    def test_no_sprite_is_left_alone(self):
        """没有立绘可改时不能凭空造一个——那会改变演出。"""
        assert apply_expression('', 'sad') == ''
        assert apply_expression('', '') == ''

    def test_complex_clause_is_not_touched(self):
        """clause 形态（behind/as/onlayer）结构化列表达不了，改了会丢修饰符。"""
        show = 'cg cg02_2 behind ye'
        assert apply_expression(show, 'sad') == show


class TestApplyPosition:
    def test_sets_position(self):
        assert apply_position('ling happy', 'left') == 'ling happy@left'

    def test_position_applies_to_every_sprite(self):
        out = apply_position('ling happy; mary smile', 'center')
        assert out == 'ling happy@center; mary smile@center'

    def test_empty_position_clears_it(self):
        assert apply_position('ling happy@left', '') == 'ling happy'

    def test_complex_clause_is_not_touched(self):
        show = 'cg cg02_2 behind ye'
        assert apply_position(show, 'left') == show


# ==================================================================
#  三、面板字段映射：4 项改得动，高级区不被清空
# ==================================================================

class TestFieldMapping:
    def test_roundtrip_with_canonical_values(self, symbols):
        """
        `fields_to_beat` 拿的是**规范值**（属性/transform）。
        面板负责把中文归一后再写——见 `TestNormalization`。
        """
        b = Beat(seq=3, label='ch01', speaker='e', text='你好',
                 show='eileen happy@left', music='bgm_theme')
        fields = beat_to_fields(b, symbols)
        fields['Expression'] = 'happy'
        fields['Position'] = 'left'
        back = fields_to_beat(fields, base=b)
        assert back.speaker == 'e'
        assert back.text == '你好'
        assert back.show == 'eileen happy@left'
        assert back.seq == 3

    def test_fields_expose_expression_and_position(self, symbols):
        b = Beat(seq=1, label='t', text='x', show='ling close_happy@left')
        fields = beat_to_fields(b, symbols)
        # close_happy 反推不出无损的中文词（「开心」会丢 close_），
        # 所以这里显示原属性名——丑但准，见 test_lossy_word_falls_back…
        assert fields['Expression'] == 'close_happy'
        assert fields['Position'] == '左'

    def test_direct_beat_construction_is_not_wiped(self):
        """
        `fields_to_beat` 不带这两个键时**不许动 show**——
        CLI / 单测会直接构造字段字典，清空立绘是灾难。
        """
        b = fields_to_beat({'Text': 'x', 'Kind': ''}, base=Beat(show='ling happy'))
        assert b.show == 'ling happy'

    def test_editing_only_text_keeps_show(self, symbols):
        """策划只改台词时，立绘/位置一个字节都不该动。"""
        base = Beat(seq=1, label='t', text='旧', show='ling close_happy@left')
        fields = beat_to_fields(base, symbols)
        fields['Text'] = '新'
        # 规范值原样带回 → show 不变
        fields['Expression'] = 'close_happy'
        fields['Position'] = 'left'
        back = fields_to_beat(fields, base=base)
        assert back.text == '新'
        assert back.show == 'ling close_happy@left'


# ==================================================================
#  三之二、归一：中文词绝不许进脚本
# ==================================================================

class TestNormalization:
    """
    卡片上是中文（开心 / 左），脚本里必须是属性与 transform（happy / left）。

    漏了这一步就会生成 `show eileen 开心`——Ren'Py 语法上不存在的图像。
    这类"中文漏进代码"的 bug 在真实项目里出现过 18 行，所以在这里钉死。
    """

    def test_chinese_expression_becomes_attribute(self, symbols):
        b = Beat(seq=1, label='t', text='x', show='eileen happy')
        assert normalize_expression('开心', b, symbols) == 'happy'
        assert normalize_expression('难过', b, symbols) == 'sad'

    def test_english_attribute_passes_through(self, symbols):
        b = Beat(seq=1, label='t', text='x', show='eileen happy')
        # 已经是属性名：不许改写
        assert normalize_expression('close_happy', b, symbols) == 'close_happy'

    def test_chinese_position_becomes_transform(self, symbols):
        assert normalize_position('左', symbols) == 'left'
        assert normalize_position('右', symbols) == 'right'

    def test_custom_transform_passes_through(self, symbols):
        """项目自定义 transform（i11/leftish）本来就合法，不许改写。"""
        assert normalize_position('i11', symbols) == 'i11'

    def test_empty_stays_empty(self, symbols):
        assert normalize_expression('', Beat(show='e x'), symbols) == ''
        assert normalize_position('', symbols) == ''

    def test_no_symbols_is_passthrough(self):
        b = Beat(seq=1, label='t', show='e x')
        assert normalize_expression('开心', b, None) == '开心'
        assert normalize_position('左', None) == '左'

    def test_end_to_end_no_chinese_in_show(self, symbols):
        """整条链：卡片填中文 → 写回 show → 生成代码里不许有中文。"""
        b = Beat(seq=1, label='t', text='x', show='eileen happy@left')
        expr = normalize_expression('开心', b, symbols)
        pos = normalize_position('左', symbols)
        b.show = apply_position(apply_expression(b.show, expr), pos)
        assert b.show == 'eileen happy@left'
        assert all(ord(c) < 128 for c in b.show)


# ==================================================================
#  四、候选清单：不给"这个角色根本没有的表情"
# ==================================================================

class TestSingleLineFieldsActuallyReceiveValues:
    """
    **单行字段必须真的写得进去**（幽灵空格的回归）。

    2026-09-28 实测事故：卡片上只有「台词」有值，说话人 / 表情 / 位置全空。
    根因是 `_wset` 判据写成 `elif hasattr(w, 'set')`——
    **`tk.Entry` 根本没有 `set()` 方法**（读写只能通过 textvariable），
    于是单行字段**静默**写不进去；多行用 ScrolledText 有 `insert()`，所以没受影响。

    为什么之前的测试没拦住：我用的是"记录型控件桩"，给假 Entry 加了 `set()`
    方法——**桩比真控件"更聪明"，把 bug 藏起来了**。
    这个用例改成断言"**StringVar 收到了 set() 调用**"，
    不依赖桩有没有 set()，所以不能再说谎。
    """

    class _SpyVar:
        """记录 set() 调用——不提供任何 Entry 才有的能力。"""

        def __init__(self):
            self.value = None
            self.set_calls = []

        def set(self, v):
            self.value = v
            self.set_calls.append(v)

        def get(self):
            return self.value if self.value is not None else ''

    def _panel_with_spies(self):
        from storygraph.ui.beat_editor import BeatEditorPanel

        p = BeatEditorPanel.__new__(BeatEditorPanel)
        p._beat = None
        p._multi = {'Text', 'Stage', 'BGM', 'SFX', 'CodeBlock'}
        p._widgets = {}
        p._vars = {}
        for name in BeatEditorPanel._ORDER:
            if name in p._multi:
                continue
            spy = self._SpyVar()
            p._vars[name] = spy
        return p

    def test_stringvar_receives_value(self):
        """写一个值进去，StringVar 必须收到 set()。"""
        p = self._panel_with_spies()
        p._wset('Speaker', 'jc')
        assert p._vars['Speaker'].set_calls == ['jc'], \
            'Speaker 没写进 StringVar（单行字段又静默失败了）'

    def test_wget_roundtrips_through_stringvar(self):
        """写进去再读出来必须一样——这是卡片显示值的最基本要求。"""
        p = self._panel_with_spies()
        for name, value in (('Speaker', 'lyj'), ('Comment', '备注'),
                            ('Label', 'ch01')):
            p._wset(name, value)
            assert p._wget(name) == value, f'{name} 写进去读不出来'

    def test_real_widgets_path_uses_textvariable(self):
        """
        真控件路径：**每一个**单行字段都必须登记了 StringVar。

        这一条盯的是"有人以后又把里 Entry 的 textvariable 拿掉"。
        """
        from storygraph.ui.beat_editor import BeatEditorPanel

        single = [n for n in BeatEditorPanel._ORDER
                  if n not in ('Text', 'Stage', 'BGM', 'SFX', 'CodeBlock')]
        p = self._panel_with_spies()
        missing = [n for n in single if n not in p._vars]
        assert not missing, f'这些单行字段没有 StringVar：{missing}'

    def test_does_not_rely_on_widget_having_set(self):
        """
        反面约束：**不能**再对控件本身调 `set()`。

        `tk.Entry` 没有这个方法——依赖它就会静默失败。
        这里断言"控件没有 set() 也照样能写"。
        """
        from storygraph.ui.beat_editor import BeatEditorPanel

        p = self._panel_with_spies()

        class EntryLike:
            """像真 tk.Entry：有 get 但**没有 set**，且经过 textvariable。"""

            def __init__(self, var):
                self._var = var

            def get(self):
                return self._var.get()

        p._vars['Speaker'] = p._vars.get('Speaker')
        spy = p._vars['Speaker']
        p._widgets['Speaker'] = EntryLike(spy)
        p._wset('Speaker', 'jc')
        assert p._wget('Speaker') == 'jc', \
            '控件没有 set() 时写不进去——说明还在依赖控件自身的 set()'


class TestOnlyTextEditIsSafe:
    """
    「只改台词」不能碰舞台 / 音频 / 语音 / 背景。

    这是文案最高频的动作，也是**最容易造成数据丢失**的路径：
    卡片把高级区折起来时，那些控件是只读的——如果 `collect_beat`
    把所有控件整体读一遍再覆盖，就会把 Stage / BGM / SFX / Voice 清空，
    变成"改一句台词把这一拍的音乐弄没了"。

    用户 2026-09-28 明确要求手测这一条，这里用测试先把它钉死
    （因为手测只能抽查几拍，测试能覆盖所有拍）。
    """

    @pytest.fixture
    def loaded(self):
        """模拟：面板载入一拍（含舞台/音频/语音），高级区**收起**。"""
        from storygraph.ui.beat_editor import BeatEditorPanel

        class Probe(BeatEditorPanel):
            def __init__(self, beat):
                self._beat = beat
                self._adv_open = False
                self._vals = {}
                self._note_var = None

            def _wget(self, name):
                return self._vals.get(name, '')

            def _wset(self, name, value):
                self._vals[name] = value or ''

            def _symbols(self):
                return make_symbols(None)

            def _set_note(self, text):
                pass

            def explain_stage(self):
                pass

            def _warn_if_speaker_missing(self, beat):
                pass

        return Probe

    def _full_beat(self):
        return Beat(seq=7, label='ch01', kind='say', speaker='lyj',
                    text='旧台词',
                    scene='bg classroom',
                    show='ling close_happy@left',
                    music='bgm_night', music_params='fadein 1.0',
                    sound='sfx_door', voice='v_001',
                    hide='ye@dissolve',
                    code='$ renpy.transition(None)')

    def test_changing_text_keeps_everything_else(self, loaded):
        beat = self._full_beat()
        p = loaded(beat)
        p.load_beat(beat)
        # 只改台词
        p._vals['Text'] = '新台词'
        out = p.collect_beat()

        assert out.text == '新台词'
        # 舞台 / 音频 / 语音 / 背景一个都不能丢
        assert out.show == 'ling close_happy@left', out.show
        assert out.scene == 'bg classroom', out.scene
        assert out.hide == 'ye@dissolve', out.hide
        assert out.music == 'bgm_night' and out.music_params == 'fadein 1.0'
        assert out.sound == 'sfx_door'
        assert out.voice == 'v_001'
        assert out.speaker == 'lyj'
        assert '$ renpy.transition(None)' in (out.code or '')

    def test_rendering_after_text_edit_keeps_stage_and_audio(self, loaded):
        """最终判据：改完台词后生成的代码里，舞台/音频语句一条不少。"""
        beat = self._full_beat()
        p = loaded(beat)
        p.load_beat(beat)
        p._vals['Text'] = '新台词'
        out = render_beats_to_rpy([p.collect_beat()])

        # 音频引用（bgm_night / sfx_door / v_001）**不带下划线归一**——
        # 它们是资产 ID，下划线是 ID 的一部分。
        for frag in ('play music bgm_night fadein 1.0', 'play sound sfx_door',
                     'voice v_001', 'hide ye with dissolve',
                     'scene bg_classroom',
                     '$ renpy.transition(None)'):
            assert frag in out, f'{frag} 丢了：\n{out}'
        # 图像名在无 registry 时写成下划线形态（已知 L3 差异 1），归一后比
        assert 'show ling close happy at left' in out.replace('_', ' '), out
        assert 'lyj "新台词"' in out

    def test_chinese_expression_does_not_leak_into_show(self, loaded):
        """
        手测第 3 条：卡片上的中文表情，写回后必须是 `show eileen sad at left`，
        **不能是 `show eileen 开心`**。

        多立绘改版后，表情在**立绘数组的每一行**里编辑（见 beat_sprite_rows /
        apply_sprite_rows），所以这里直接验数组 → show 的写回链。
        """
        beat = Beat(seq=1, label='t', kind='say', speaker='e',
                    text='台词', show='eileen happy@left')
        p = loaded(beat)
        p.load_beat(beat)
        # 模拟：策划在第一行把表情改成「难过」、位置改成「左」
        p._sprite_widgets = [{'frame': None,
                              'image': _V('eileen happy'),
                              'expression': _V('难过'),
                              'position': _V('左')}]
        out = p.collect_beat()
        assert all(ord(c) < 128 for c in out.show), \
            f'中文漏进 show 了：{out.show!r}'
        text = render_beats_to_rpy([out])
        # `eileen sad`（空格形态）或 `eileen_sad`（无 registry 的下划线形态）
        assert 'show eileen sad at left' in text or \
            'show eileen_sad at left' in text, text
        assert '开心' not in text and '难过' not in text, text
        # 关键是 sad 落地了（happy → sad）
        assert 'sad' in text and 'happy' not in text, text


# ==================================================================
#  四、候选清单：不给"这个角色根本没有的表情"
# ==================================================================

class TestSpriteArray:
    """
    **立绘是数组，不是单数**（用户 2026-09-28 设计意见）。

    > 一张卡片可以同时有多个立绘：主角在左、女主在右、路人居中。
    > 每个立绘独立控制位置、缩放、出入场。

    数据层**本来就支持**（`beat.show` 是 `;` 分隔列表，渲染成多条 show），
    但卡片以前只显示/编辑第一张 —— 这里锁住"数组化"的正确性。
    """

    def test_reads_all_sprites_in_order(self, symbols):
        b = Beat(seq=1, label='t', text='x',
                 show='ling happy@left; mary smile@right; bob sad@center')
        rows = beat_sprite_rows(b, symbols)
        assert [r['tag'] for r in rows] == ['ling', 'mary', 'bob']
        assert [r['position'] for r in rows] == ['左', '右', '中']
        assert [r['expression'] for r in rows] == ['开心', '笑', '难过']

    def test_roundtrip_preserves_all_sprites(self, symbols):
        show = 'ling happy@left; mary smile@right; bob sad@center'
        b = Beat(seq=1, label='t', text='x', show=show)
        rows = beat_sprite_rows(b, symbols)
        assert apply_sprite_rows(show, rows) == show

    def test_each_sprite_keeps_its_own_position(self, symbols):
        b = Beat(seq=1, label='t', text='x', show='ling happy@left; mary sad@right')
        rows = beat_sprite_rows(b, symbols)
        # 改其中一张的位置，另一张不受影响
        rows[1]['position'] = '中'
        rows[1]['at'] = normalize_position('中', symbols)
        out = apply_sprite_rows(b.show, rows)
        assert 'ling happy@left' in out
        assert 'mary sad@center' in out

    def test_complex_clause_is_preserved(self, symbols):
        """复杂子句（behind/as/onlayer）不进数组，但**重建时不能丢**。"""
        show = 'cg cg02_2 behind ye; ling happy@left'
        b = Beat(seq=1, label='t', text='x', show=show)
        rows = beat_sprite_rows(b, symbols)
        assert len(rows) == 1, '复杂子句应该被排除在数组外'
        out = apply_sprite_rows(show, rows)
        assert 'behind ye' in out, f'复杂子句被吃掉了：{out}'

    def test_empty_array_does_not_clear_sprites(self, symbols):
        """
        **空数组 ≠ 清空立绘。**

        实测踩过：`collect_beat` 里用空数组去覆盖 `b.show`，
        结果"打开一拍什么都不改、直接下一句"就把立绘抹掉了。
        """
        from storygraph.ui.beat_editor import BeatEditorPanel

        b = Beat(seq=1, label='t', text='x', show='ling happy@left')
        p = BeatEditorPanel.__new__(BeatEditorPanel)
        p._beat = b
        p._sprite_widgets = []          # 空数组
        p._adv_open = False

        class _W:
            def __init__(self, v=''):
                self.v = v

            def get(self):
                return self.v

            def set(self, v):
                self.v = v or ''

        p._widgets = {}
        p._vars = {}

        def _wget(n):
            return '台词' if n == 'Text' else ''
        p._wget = _wget
        p._symbols = lambda: symbols
        p._set_note = lambda t: None
        p._warn_if_speaker_missing = lambda b: None
        out = p.collect_beat()
        assert out.show == 'ling happy@left', \
            f'空数组把立绘清掉了：{out.show!r}'


class TestInheritedStateHint:
    """
    **持续状态默认继承**（用户 2026-09-28 设计意见）：

    > 背景、BGM、环境层这类持续状态，默认从上一张卡片继承。
    > 卡片上显示"继承：教室-黄昏"，点击才覆盖。

    关键前提：**Ren'Py 本身就是继承语义**（背景/立绘/BGM 是状态，
    不写就沿用）。所以这一条**不需要改数据模型**，只要把"当前继承到什么"
    显示出来，创作者就不必每拍重填。
    """

    def test_shows_inherited_scene_actors_music(self):
        beats = [Beat(seq=1, label='t', text='一', scene='bg classroom',
                      show='ling happy@left', music='bgm_a'),
                 Beat(seq=2, label='t', text='二')]
        hint = inherited_state_hint(beats, 1)
        assert 'bg classroom' in hint
        assert 'ling happy@left' in hint
        assert 'bgm_a' in hint

    def test_no_hint_for_first_beat(self):
        beats = [Beat(seq=1, label='t', text='一', scene='bg classroom')]
        assert inherited_state_hint(beats, 0) == ''

    def test_no_hint_when_beat_overrides_everything(self):
        """这一拍自己写了背景/立绘/音乐 → 没什么可继承的。"""
        beats = [Beat(seq=1, label='t', text='一', scene='bg classroom',
                      show='ling happy@left', music='bgm_a'),
                 Beat(seq=2, label='t', text='二', scene='bg roof',
                      show='mary sad@right', music='bgm_b')]
        assert inherited_state_hint(beats, 1) == ''

    def test_partial_override_only_mentions_the_rest(self):
        """只覆盖背景 → 提示里只该出现继承来的立绘/音乐。"""
        beats = [Beat(seq=1, label='t', text='一', scene='bg classroom',
                      show='ling happy@left', music='bgm_a'),
                 Beat(seq=2, label='t', text='二', scene='bg roof')]
        hint = inherited_state_hint(beats, 1)
        assert 'bg classroom' not in hint, '已经覆盖的背景不该说继承'
        assert 'ling happy@left' in hint
        assert 'bgm_a' in hint


class TestConditionField:
    """
    **条件显示统一入口**（用户 2026-09-28 设计意见）：

    > 台词、立绘、背景都可以有"条件显示"。UI 上统一为一个"+ 条件"按钮。

    数据层早就有 `Beat.condition`（解析时从语句取、渲染时包成
    `if <条件>:`），但**从来没暴露给策划**（既不在表里也不在卡片里）。
    这里锁住"卡片能读写它 + 渲染真的包 if"。
    """

    def test_condition_renders_as_if_block(self):
        b = Beat(seq=1, label='t', text='台词', speaker='e',
                 condition='love >= 5')
        out = render_beats_to_rpy([b])
        assert 'if love >= 5:' in out, out
        assert 'e "台词"' in out

    def test_no_condition_means_no_if(self):
        b = Beat(seq=1, label='t', text='台词', speaker='e')
        out = render_beats_to_rpy([b])
        assert 'if ' not in out, f'没有条件却生成了 if：\n{out}'

    def test_card_loads_and_writes_condition(self, symbols):
        """卡片能把条件读进来、再写回 Beat，并真的渲染成 if。"""
        from storygraph.ui.beat_editor import BeatEditorPanel

        b = Beat(seq=1, label='t', text='台词', speaker='e')
        p = BeatEditorPanel.__new__(BeatEditorPanel)
        p._condition_var = _V()
        p._beat = b
        p._sprite_widgets = []
        p._adv_open = False
        p._widgets = {}
        p._vars = {}
        # 模拟"表单里说话人与台词都有值"
        p._wget = lambda n: {'Speaker': 'e', 'Text': '台词'}.get(n, '')
        p._symbols = lambda: symbols
        p._set_note = lambda t: None
        p._warn_if_speaker_missing = lambda be: None

        p._condition_var.set('score > 10')      # 相当于在「＋ 条件」里填写
        out = p.collect_beat()
        assert out.condition == 'score > 10'
        assert 'if score > 10:' in render_beats_to_rpy([out])
        assert 'e "台词"' in render_beats_to_rpy([out])

    def test_card_clearing_condition_removes_if(self, symbols):
        from storygraph.ui.beat_editor import BeatEditorPanel

        b = Beat(seq=1, label='t', text='台词', speaker='e',
                 condition='old > 1')
        p = BeatEditorPanel.__new__(BeatEditorPanel)
        p._condition_var = _V()
        p._beat = b
        p._sprite_widgets = []
        p._adv_open = False
        p._widgets = {}
        p._vars = {}
        p._wget = lambda n: {'Speaker': 'e', 'Text': '台词'}.get(n, '')
        p._symbols = lambda: symbols
        p._set_note = lambda t: None
        p._warn_if_speaker_missing = lambda be: None

        p._condition_var.set('')                # 清空条件
        out = p.collect_beat()
        assert out.condition == ''
        assert 'if ' not in render_beats_to_rpy([out])


class TestChoices:
    def test_expression_choices_come_from_tag_attributes(self):
        """候选只取该 tag 声明过的属性，不给别的角色的表情。"""
        from storygraph.assets.asset_registry import AssetRegistry
        reg = AssetRegistry()
        reg.images = {
            'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling',
                           'Attributes': 'happy'},
            'ling_sad': {'ID': 'ling_sad', 'Tag': 'ling',
                         'Attributes': 'sad'},
            'mary_smile': {'ID': 'mary_smile', 'Tag': 'mary',
                           'Attributes': 'smile'},
        }
        sym = make_symbols(reg)
        b = Beat(seq=1, label='t', text='x', show='ling happy')
        choices = expression_choices(b, sym)
        assert 'smile' not in choices, '不该给出别的角色的表情'

    def test_expression_choices_empty_without_registry(self, symbols):
        b = Beat(seq=1, label='t', text='x', show='ling happy')
        assert expression_choices(b, symbols) == []

    def test_position_choices_include_custom_transforms(self):
        """自定义 transform 一定要在候选里——真实项目里 `i11` 比 `left` 用得多。"""
        from storygraph.assets.asset_registry import AssetRegistry
        reg = AssetRegistry()
        reg.transforms = {'i11': {'ID': 'i11', 'ATL': 'xpos 0.1'},
                          'leftish': {'ID': 'leftish', 'ATL': 'xpos 0.2'}}
        sym = make_symbols(reg)
        choices = position_choices(sym)
        assert 'i11' in choices
        assert 'leftish' in choices
        # 内置位置也在
        assert any(w in choices for w in ('左', 'left'))

    def test_position_choices_nonempty_without_registry(self, symbols):
        assert position_choices(symbols), '至少要有内置位置词'
