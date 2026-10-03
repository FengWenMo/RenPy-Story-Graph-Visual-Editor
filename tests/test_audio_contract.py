"""
契约守卫：**音频的真相源只有一个**（风险清单 P2-8）
==================================================

清单那条："`bgm`/`sfx`（文本，标称真相源）与 `music*` / `sound*`（结构化）
并存"。

复验结论（2026-10-05）：**方向其实已经是单向的**，只是没测试把它钉住 ——

    beat.bgm / beat.sfx            ← 真相源（「音乐」「音效」文本列）
        ↓ _fill_audio_from_cells()   （单向填充）
    beat.music* / beat.sound*      ← 派生视图（下拉列 / 参数列）

反向（结构化 → 文本）只发生在 `to_row` / `from_row` 这两处边界上，
而且是"文本为空时才合成"。也就是说：

  · **文本列有值** → 文本说了算（策划手改的就是它）；
  · **文本列为空** → 从结构化合成（直接构造的 Beat、单测）。

这个文件把三条性质钉住：

  1. 文本列非空时，结构化字段**不可能**覆盖它；
  2. 结构化字段非空、文本列为空时，合成出的文本**语义等价**；
  3. `stop` / `queue` 伪值 + 参数**不会互相吃掉**（曾经吃掉 6 条
     `stop music fadeout 1.0` 的 fadeout）。
"""

from __future__ import annotations

import pytest

from storygraph.story.beat_model import (
    AUDIO_PSEUDO,
    Beat,
    _fill_audio_from_cells,
    compose_audio_cell,
    compose_audio_text,
)


def _beat(**kw):
    return Beat(seq=1, label='a', **kw)


class TestTextColumnWins:
    """文本列是真相源：有值就以它为准。"""

    @pytest.mark.parametrize('channel,attr,name,params', [
        ('music', 'bgm', 'spring', 'fadein 1.0'),
        ('sound', 'sfx', 'door', 'fadeout 0.5'),
    ])
    def test_nonempty_text_is_not_overwritten_by_structure(
            self, channel, attr, name, params):
        """
        文本列有值、结构化字段是别的东西时，`_fill_audio_from_cells`
        必须按**文本**改写结构化字段（而不是反过来）。
        """
        beat = _beat()
        if channel == 'music':
            beat.music, beat.music_params, beat.music_action = \
                'WRONG', 'wrong', 'play'
        else:
            beat.sound, beat.sound_params, beat.sound_action = \
                'WRONG', 'wrong', 'play'
        _fill_audio_from_cells(beat, channel, name, params)
        got = (beat.music, beat.music_params) if channel == 'music' \
            else (beat.sound, beat.sound_params)
        assert got == (name, params), got

    def test_empty_text_column_changes_nothing(self):
        """
        文本列**空**（老表没这一列 / 手工构造）时不许动结构化字段 ——
        否则"无值"会被当成"清空"，`stop` 之类的动作凭空消失。
        """
        beat = _beat(music='spring', music_params='fadein 1.0',
                     music_action='play')
        _fill_audio_from_cells(beat, 'music', '', '')
        assert (beat.music, beat.music_params, beat.music_action) == \
            ('spring', 'fadein 1.0', 'play')


class TestStructureToTextIsSemantic:
    """
    结构化 → 文本只在文本为空时发生，且必须语义等价。

    两个 composer 分工不同：

      · `compose_audio_text` → **DSL 文本**（`[音乐] spring fadein 1.0`），
        给人看 / 写进兼容列；
      · `compose_audio_cell` → **结构化列的两个格子**
        （`('spring', 'fadein 1.0')`），`_fill_audio_from_cells` 是它的逆。
    """

    @pytest.mark.parametrize('channel,kwargs,cell,dsl', [
        ('music', {'music': 'spring', 'music_params': 'fadein 1.0'},
         ('spring', 'fadein 1.0'), '[音乐] spring fadein 1.0'),
        ('sound', {'sound': 'door', 'sound_params': 'fadeout 0.5'},
         ('door', 'fadeout 0.5'), '[音效] door fadeout 0.5'),
        ('music', {'music_action': 'stop', 'music_params': 'fadeout 1.0'},
         ('（停止音乐）', 'fadeout 1.0'), '[停止音乐]'),
    ])
    def test_composers_agree_with_filler(self, channel, kwargs, cell, dsl):
        beat = _beat(**kwargs)
        assert compose_audio_cell(beat, channel) == cell
        assert compose_audio_text(beat, channel) == dsl
        # cell → 结构化：逆运算回到同一个状态
        back = _beat()
        _fill_audio_from_cells(back, channel, cell[0], cell[1])
        if channel == 'music':
            got = (back.music, back.music_params, back.music_action)
            want = (beat.music, beat.music_params, beat.music_action)
        else:
            got = (back.sound, back.sound_params, back.sound_action)
            want = (beat.sound, beat.sound_params, beat.sound_action)
        assert got == want, f'{got} != {want}'


class TestPseudoValuesKeepParams:
    """
    `stop` / `queue` 伪值 + 参数**不许互相吃掉**。

    真实事故（`chapter1_3` 6 条）：`stop music fadeout 1.0` 导出成
    「（停止音乐）」+ 空参数，读回后 fadeout 就没了。
    """

    @pytest.mark.parametrize('channel,attr,pseudo_key,params', [
        ('music', 'bgm', 'BGM', 'fadeout 1.0'),
        ('sound', 'sfx', 'SFX', 'fadeout 0.5'),
    ])
    def test_stop_pseudo_round_trips_params(self, channel, attr, pseudo_key,
                                            params):
        action = 'stop'
        beat = _beat()
        if channel == 'music':
            beat.music, beat.music_params, beat.music_action = \
                '', params, action
        else:
            beat.sound, beat.sound_params, beat.sound_action = \
                '', params, action
        cell = compose_audio_cell(beat, channel)
        assert cell[0] == {v: k for k, v in AUDIO_PSEUDO[pseudo_key].items()}[
            action], cell
        # 关键：**参数必须跟着伪值一起出来**
        assert cell[1] == params, cell
        # 文本 + 参数列读回来，两边都要在
        back = _beat()
        _fill_audio_from_cells(back, channel, cell[0], cell[1])
        got = (back.music, back.music_params, back.music_action) \
            if channel == 'music' else \
            (back.sound, back.sound_params, back.sound_action)
        assert got == ('', params, action), got

    def test_queue_pseudo_is_recognised(self):
        beat = _beat()
        beat.sound_action, beat.sound, beat.sound_params = 'queue', '', ''
        assert compose_audio_cell(beat, 'sound')[0] == \
            {v: k for k, v in AUDIO_PSEUDO['SFX'].items()}['queue']


class TestAudioSurvivesBeatRoundTrip:
    """整条链路：`Beat → row → Beat` 之后音频三个字段一致。"""

    @pytest.mark.parametrize('channel,kwargs', [
        ('music', {'music': 'spring', 'music_params': 'fadein 1.0 fadeout 2.0',
                   'music_action': 'play'}),
        ('sound', {'sound': 'door', 'sound_params': 'volume 0.5',
                   'sound_action': 'play'}),
        ('music', {'music': '', 'music_params': 'fadeout 1.0',
                   'music_action': 'stop'}),
    ])
    def test_round_trip(self, channel, kwargs):
        from storygraph.story.beat_model import BEAT_FIELDS
        beat = _beat(text='台词', **kwargs)
        row = dict(zip(BEAT_FIELDS, beat.to_row()))
        back = Beat.from_row(row)
        if channel == 'music':
            got = (back.music, back.music_params, back.music_action)
            want = (beat.music, beat.music_params, beat.music_action)
        else:
            got = (back.sound, back.sound_params, back.sound_action)
            want = (beat.sound, beat.sound_params, beat.sound_action)
        assert got == want, f'{got} != {want}'
