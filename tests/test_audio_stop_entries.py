"""
A 方案：stop 音乐 / 音效的**入口级**守卫（2026-10-28）
====================================================
用户拍板：卡片下拉只放「（停止音乐）/（停止音效）」，**不放「（排队播放）」**
（排队必须带曲名，单放一个伪值会导成 TODO）。

这个文件钉的是"看起来设置了停止、产物却在播一首不存在的曲子"那条链：

  1. 卡片 BGM 下拉里**根本没有**伪值（Excel 候选表一直有）；
  2. 手打 `（停止音乐） fadeout 1.0` 被当成曲名 →
     `play music "（停止音乐） fadeout 1.0"` + 缺少资源 TODO；
  3. `[停止音乐] fadeout 1.0` 的 `fadeout` 被当曲名吃掉 → 裸 `stop music`
     （参数静默丢失）。

三条都在 2026-10-28 实测复现过；这里从解析 → 编译 → 渲染逐层钉住。
"""

from __future__ import annotations

import pytest

from storygraph.app.beats import audio_choice_items
from storygraph.assets.asset_registry import AssetRegistry
from storygraph.story.beat_model import (
    AUDIO_PSEUDO,
    Beat,
    _fill_audio_from_cells,
)
from storygraph.story.dsl import OK, compile_audio, parse_audio
from storygraph.story.script_table import make_symbols, render_beats_to_rpy


@pytest.fixture
def symbols():
    reg = AssetRegistry()
    reg.music = {'spring': {'ID': 'spring', 'Path': 'bgm/spring.mp3'}}
    reg.sounds = {'door': {'ID': 'door', 'Path': 'sfx/door.wav'}}
    return make_symbols(reg)


# ------------------------------------------------------------------
#  一、解析：伪值 → (action, params)，不再当曲名
# ------------------------------------------------------------------

class TestParseStopEntries:
    @pytest.mark.parametrize('text,channel,params', [
        # 卡片下拉 / 手打（伪值）
        ('（停止音乐）', 'music', ''),
        ('（停止音乐） fadeout 1.0', 'music', 'fadeout 1.0'),
        # 方括号 DSL（旧写法，参数从前会被吃掉）
        ('[停止音乐]', 'music', ''),
        ('[停止音乐] fadeout 1.0', 'music', 'fadeout 1.0'),
        # 从 .rpy 直接复制到卡片里的原生行
        ('stop music fadeout 1.0', 'music', 'fadeout 1.0'),
    ])
    def test_music_stop(self, text, channel, params):
        ops = parse_audio(text, channel)
        assert len(ops) == 1, [o.__dict__ for o in ops]
        op = ops[0]
        assert (op.action, op.name, op.params) == ('stop', '', params), \
            (op.action, op.name, op.params)

    @pytest.mark.parametrize('text,params', [
        ('（停止音效） fadeout 0.5', 'fadeout 0.5'),
        ('[停止音效] fadeout 0.5', 'fadeout 0.5'),
        ('stop sound fadeout 0.5', 'fadeout 0.5'),
    ])
    def test_sound_stop(self, text, params):
        op = parse_audio(text, 'sound')[0]
        assert (op.channel, op.action, op.name, op.params) == \
            ('sound', 'stop', '', params)

    def test_queue_pseudo_keeps_the_name(self):
        """`（排队播放）` 必须带曲名；带了名字就还是 queue + name。"""
        op = parse_audio('（排队播放） bgm03', 'music')[0]
        assert (op.action, op.name) == ('queue', 'bgm03')

    def test_bracket_queue_keeps_the_name(self):
        op = parse_audio('[音乐(queue)] bgm03', 'music')[0]
        assert (op.action, op.name) == ('queue', 'bgm03')

    def test_plain_play_is_untouched(self):
        op = parse_audio('[音乐] spring fadein 1.0', 'music')[0]
        assert (op.action, op.name, op.params) == \
            ('play', 'spring', 'fadein 1.0')

    def test_stop_word_after_verb_still_works(self):
        """旧写法 `[音乐] 停止 fadeout 2.0` 一个字节都不许变。"""
        op = parse_audio('[音乐] 停止 fadeout 2.0', 'music')[0]
        assert (op.action, op.name, op.params) == \
            ('stop', '', 'fadeout 2.0')


# ------------------------------------------------------------------
#  二、编译：伪值必须变成真正的 stop 语句
# ------------------------------------------------------------------

class TestCompileStopEntries:
    @pytest.mark.parametrize('text,channel,want', [
        ('（停止音乐） fadeout 1.0', 'music', 'stop music fadeout 1.0'),
        ('[停止音乐] fadeout 1.0', 'music', 'stop music fadeout 1.0'),
        ('[停止音乐]', 'music', 'stop music'),
        ('（停止音效） fadeout 0.5', 'sound', 'stop sound fadeout 0.5'),
    ])
    def test_compiles_to_real_stop(self, symbols, text, channel, want):
        rows = compile_audio(text, channel, symbols)
        assert len(rows) == 1
        _intent, code, status = rows[0]
        assert status == OK, code
        assert code == want

    def test_pseudo_is_never_a_play(self, symbols):
        """反向守卫：伪值绝不许翻成 `play music "（停止音乐）…"`。"""
        code = compile_audio('（停止音乐）', 'music', symbols)[0][1]
        assert not code.startswith('play '), code
        assert code == 'stop music'


# ------------------------------------------------------------------
#  三、卡片文本列：手打伪值 + 参数不再变成"播放"
# ------------------------------------------------------------------

class TestCardTextColumn:
    @pytest.mark.parametrize('text,params', [
        ('（停止音乐）', ''),
        ('（停止音乐） fadeout 1.0', 'fadeout 1.0'),
        ('[停止音乐] fadeout 1.0', 'fadeout 1.0'),
        ('stop music fadeout 1.0', 'fadeout 1.0'),
    ])
    def test_card_typed_forms(self, text, params):
        beat = Beat(seq=1, label='a')
        _fill_audio_from_cells(beat, 'music', text, '')
        assert (beat.music, beat.music_params, beat.music_action) == \
            ('', params, 'stop'), (beat.music, beat.music_params,
                                   beat.music_action)

    def test_excel_style_pseudo_plus_params_kept(self):
        """Excel 那条路（伪值 + 参数列分开）本来就对，这里只是别改坏。"""
        beat = Beat(seq=1, label='a')
        _fill_audio_from_cells(beat, 'music', '（停止音乐）', 'fadeout 1.0')
        assert (beat.music, beat.music_params, beat.music_action) == \
            ('', 'fadeout 1.0', 'stop')

    def test_sound_channel(self):
        beat = Beat(seq=1, label='a')
        _fill_audio_from_cells(beat, 'sound', '（停止音效） fadeout 0.5', '')
        assert (beat.sound, beat.sound_params, beat.sound_action) == \
            ('', 'fadeout 0.5', 'stop')


# ------------------------------------------------------------------
#  四、端到端：卡片 → 渲染，产物必须是 stop（不是 play + TODO）
# ------------------------------------------------------------------

class TestRenderStopEntries:
    def test_card_typed_stop_renders_stop(self):
        beat = Beat(seq=1, label='t', speaker='', text='……',
                    bgm='（停止音乐） fadeout 1.0')
        out = render_beats_to_rpy([beat], label_name='t')
        assert 'stop music fadeout 1.0' in out, out
        assert 'play music' not in out, out
        assert 'TODO' not in out, out

    def test_structured_stop_keeps_params(self):
        """`[停止音乐]` 文本 + 参数列：参数在渲染时照旧带出来。"""
        beat = Beat(seq=1, label='t', bgm='[停止音乐]',
                    music_params='fadeout 1.0')
        out = render_beats_to_rpy([beat], label_name='t')
        assert 'stop music fadeout 1.0' in out, out

    def test_sound_stop_renders_stop(self):
        beat = Beat(seq=1, label='t', sfx='（停止音效） fadeout 0.5')
        out = render_beats_to_rpy([beat], label_name='t')
        assert 'stop sound fadeout 0.5' in out, out


# ------------------------------------------------------------------
#  五、卡片候选：只放「停止」，不放「排队播放」
# ------------------------------------------------------------------

class TestAudioChoiceItems:
    def test_music_has_stop_first(self):
        items = audio_choice_items('music', [('spring', '春')])
        words = [w for w, _d in items]
        assert words[0] == '（停止音乐）', words
        assert 'spring' in words
        assert not any('排队' in w for w in words), words

    def test_sound_has_stop_first(self):
        items = audio_choice_items('sound', [])
        assert [w for w, _d in items] == ['（停止音效）']

    def test_stop_word_comes_from_one_vocabulary(self):
        """下拉里的词必须与 `AUDIO_PSEUDO`（表格 / Excel 同一份）逐字相同。"""
        want = {w for w, action in AUDIO_PSEUDO['BGM'].items()
                if action == 'stop'}
        got = {w for w, _d in audio_choice_items('music', [])}
        assert got == want

    def test_description_says_what_it_does(self):
        _word, desc = audio_choice_items('music', [])[0]
        assert '停止' in desc and '不播放' in desc, desc

    def test_other_kinds_are_untouched(self):
        choices = [('bg room', '背景')]
        assert audio_choice_items('image', choices) == choices
        assert audio_choice_items('voice', choices) == choices
