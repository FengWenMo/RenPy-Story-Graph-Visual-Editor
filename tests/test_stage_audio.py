"""
tests/test_stage_audio.py
=========================
演出预览的**声音**（用户 2026-10-05："第一个是 bgm 未被实际播放"）。

分两层钉：
  · `app.stage`（纯函数）：素材名 → 磁盘音频文件、每一拍该放什么；
  · `ui_qt.stage_preview.StageAudio`（Qt）：BGM **持续**（同一首不重放）、
    音效/语音**一次性**（同一帧重画不重喊）、关掉声音要停干净。
"""

from __future__ import annotations

import pytest

from storygraph.app.stage import (audio_spec_name, resolve_audio_path,
                                  resolve_audio_path_info, stage_audio_cues)


class _Reg:
    def __init__(self, music=None, sounds=None, voices=None):
        self.music = music or {}
        self.sounds = sounds or {}
        self.voices = voices or {}


class _Frame:
    def __init__(self, music='', sound='', voice='', line_id='l'):
        self.music, self.sound, self.voice = music, sound, voice
        self.line_id = line_id


@pytest.fixture
def audio_project(tmp_path):
    """最小项目：`game/bgm/*.mp3` + `game/audio/sfx.ogg`。"""
    bgm = tmp_path / 'game' / 'bgm'
    audio = tmp_path / 'game' / 'audio'
    bgm.mkdir(parents=True)
    audio.mkdir(parents=True)
    (bgm / 'theme.mp3').write_bytes(b'ID3')
    (bgm / 'Empt1y.mp3').write_bytes(b'ID3')
    (audio / 'click.ogg').write_bytes(b'OggS')
    return tmp_path


class TestAudioSpecName:
    def test_plain_id_and_path_specs(self):
        assert audio_spec_name('bgm_theme') == 'bgm_theme'
        assert audio_spec_name('bgm/theme.mp3') == 'bgm/theme.mp3'

    def test_card_field_text_with_prefix_and_options(self):
        """卡片上那一格是整行文本：`[音乐] bgm/x.mp3 fadein 1.0`。"""
        assert audio_spec_name('[音乐] bgm/Empt1y.mp3 fadein 1.0') == \
            'bgm/Empt1y.mp3'
        assert audio_spec_name('[音乐] bgm/x.mp3 loop') == 'bgm/x.mp3'

    def test_empty(self):
        assert audio_spec_name('') == ''
        assert audio_spec_name(None) == ''


class TestResolveAudioPath:
    def test_registry_path_with_project_prefix(self, audio_project):
        """
        真实项目的表里存的是 `sr_general/game/bgm/x.mp3`（带项目目录），
        而我们打开的项目根就是 `…/sr_general` —— 要去掉那一段。
        """
        reg = _Reg(music={'bgm_theme': {'Path': 'proj/game/bgm/theme.mp3'}})
        got = resolve_audio_path('bgm_theme', reg, audio_project)
        assert got is not None and got.endswith('theme.mp3')
        assert 'proj/game' not in got.replace('\\', '/'), got

    def test_name_only_falls_back_to_the_audio_folders(self, audio_project):
        got = resolve_audio_path('theme', None, audio_project)
        assert got is not None and got.endswith('theme.mp3')

    def test_direct_path_spec(self, audio_project):
        got = resolve_audio_path('bgm/Empt1y.mp3', None, audio_project)
        assert got is not None and got.endswith('Empt1y.mp3')

    def test_never_returns_a_missing_file(self, audio_project):
        assert resolve_audio_path('没有这首', None, audio_project) is None
        assert resolve_audio_path('', None, audio_project) is None
        assert resolve_audio_path('bgm/nope.mp3', None, audio_project) is None

    def test_sound_kind_uses_the_sounds_table(self, audio_project):
        reg = _Reg(sounds={'click': {'Path': 'game/audio/click.ogg'}})
        got = resolve_audio_path('click', reg, audio_project, kind='sound')
        assert got is not None and got.endswith('click.ogg')


class TestStageAudioCues:
    def test_music_is_persistent_state_and_once_cues_are_not(self,
                                                             audio_project):
        reg = _Reg(music={'bgm_theme': {'Path': 'game/bgm/theme.mp3'}})
        cues = stage_audio_cues(_Frame(music='bgm_theme'), reg, audio_project)
        assert cues['music'] and cues['music'].endswith('theme.mp3')
        assert cues['once'] == []

    def test_sound_and_voice_are_one_shot(self, audio_project):
        reg = _Reg(sounds={'click': {'Path': 'game/audio/click.ogg'}})
        cues = stage_audio_cues(_Frame(sound='click', voice=''), reg,
                                audio_project)
        assert [k for k, _p in cues['once']] == ['sound']

    def test_missing_music_is_none_not_an_exception(self, audio_project):
        cues = stage_audio_cues(_Frame(music='没有这首'), None, audio_project)
        assert cues['music'] is None and cues['music_name'] == '没有这首'


class TestMissingAudioIsNotSilent:
    """
    用户 2026-10-05：

    > 这个你确定 bgm 导入了吗？……是否正确呢？查询一下，因为我这里没有声音……

    真实项目实测：脚本里写的是 `play music "bgm/夜晚，断电之后…….mp3"`，
    而磁盘上那个文件**根本不存在**（真名叫 `夜晚，断电之后，不存在的教室.mp3`
    ——作者当初把长名字省略着写，后来文件改名了，脚本没跟着改）。所以"没有声音"
    不是预览的锅；但预览**必须把它说出来**，还要能近似对上真文件。
    """

    @pytest.fixture
    def project(self, tmp_path):
        bgm = tmp_path / 'game' / 'bgm'
        bgm.mkdir(parents=True)
        (bgm / '夜晚，断电之后，不存在的教室.mp3').write_bytes(b'ID3')
        (bgm / '魅影dot1.mp3').write_bytes(b'ID3')
        return tmp_path

    def test_exact_still_wins(self, project):
        info = resolve_audio_path_info('bgm/魅影dot1.mp3', None, project)
        assert info['exact'] is True and info['approx'] is False
        assert info['path'].endswith('魅影dot1.mp3')

    def test_ellipsis_name_falls_back_to_the_real_file(self, project):
        """省略号截断的名字 → 近似匹配到真文件，并且**标出真名**。"""
        info = resolve_audio_path_info('bgm/夜晚，断电之后…….mp3', None, project)
        assert info['path'] is not None, '近似匹配没生效（用户还是听不到声音）'
        assert info['path'].endswith('夜晚，断电之后，不存在的教室.mp3')
        assert info['approx'] is True and info['exact'] is False
        assert info['matched'] == '夜晚，断电之后，不存在的教室.mp3'

    def test_strict_mode_never_approximates(self, project):
        """严格解析是"这个名字到底有没有文件"的事实来源，不许被近似污染。"""
        assert resolve_audio_path_info('bgm/夜晚，断电之后…….mp3', None, project,
                                       allow_approx=False)['path'] is None
        assert resolve_audio_path('bgm/夜晚，断电之后…….mp3', None,
                                  project) is None

    def test_unrelated_name_is_not_guessed(self, project):
        """公共前缀太短（这里根本没有）→ 不猜，老实返回找不到。"""
        assert resolve_audio_path_info('完全无关的曲子', None, project)['path'] \
            is None

    def test_ambiguity_is_not_guessed(self, tmp_path):
        """前缀一样长的两首**不同**曲子 → 宁可不出声，也不放错曲子。"""
        bgm = tmp_path / 'game' / 'bgm'
        bgm.mkdir(parents=True)
        (bgm / 'theme song A.mp3').write_bytes(b'ID3')
        (bgm / 'theme song B.mp3').write_bytes(b'ID3')
        info = resolve_audio_path_info('theme song', None, tmp_path)
        assert info['path'] is None, f'并列第一还硬挑了一首：{info}'

    def test_same_song_duplicated_on_disk_is_not_ambiguous(self, tmp_path):
        """
        真实项目里有一层**重复嵌套的 `sr_general/` 目录**，同一首曲子会在
        两处出现（文件名一模一样）—— 那是同一首歌，不能算"并列第一"。
        """
        for rel in ('game/bgm/a.mp3', 'sr_general/game/bgm/a.mp3'):
            f = tmp_path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b'ID3')
        info = resolve_audio_path_info('a', None, tmp_path, allow_approx=True)
        # 精确命中本来就该直接中（同名文件），这里锁的是"不会因为副本而放弃"
        assert info['path'] is not None

    def test_registry_path_with_project_prefix_is_preferred_over_nested_copy(
            self, tmp_path):
        """表里存 `proj/game/bgm/x.mp3`：先按"相对当前项目"解释，命中**外层**那份。"""
        outer = tmp_path / 'game' / 'bgm' / 'x.mp3'
        outer.parent.mkdir(parents=True)
        outer.write_bytes(b'ID3')
        nested = tmp_path / 'proj' / 'game' / 'bgm' / 'x.mp3'
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b'ID3')
        reg = _Reg(music={'bgm_x': {'Path': 'proj/game/bgm/x.mp3'}})
        info = resolve_audio_path_info('bgm_x', reg, tmp_path)
        assert info['path'] is not None
        assert str(outer) == info['path'], info['path']

    def test_cues_report_missing_and_approximate(self, project):
        cues = stage_audio_cues(_Frame(music='bgm/夜晚，断电之后…….mp3'), None,
                                project)
        assert cues['music'] is not None, '近似匹配应当仍然出声'
        assert cues['music_exact'] is False
        assert cues['music_matched'].endswith('不存在的教室.mp3')
        assert cues['notes'] and '近似匹配' in cues['notes'][0]
        assert cues['missing'] == [], '近似对上了就不该再报"缺失"'

    def test_cues_do_not_lie_when_nothing_matches(self, project):
        cues = stage_audio_cues(_Frame(music='bgm/nope.mp3'), None, project)
        assert cues['music'] is None
        assert cues['missing'] == ['bgm/nope.mp3']
        assert cues['notes'] and '找不到' in cues['notes'][0]

    def test_exact_hit_has_no_notes(self, project):
        """一切正常时横条上不该多出一行废话。"""
        cues = stage_audio_cues(_Frame(music='bgm/魅影dot1.mp3'), None, project)
        assert cues['music_exact'] is True and cues['notes'] == []


class _FakePlayer:
    def __init__(self, log):
        self.log = log

    def setAudioOutput(self, out):
        self.log.append(('audioOut', id(out)))

    def setLoops(self, n):
        self.log.append(('loops', n))

    def setSource(self, url):
        path = url.toLocalFile() if hasattr(url, 'toLocalFile') else str(url)
        self.log.append(('source', path.replace('\\', '/').split('/')[-1]))

    def play(self):
        self.log.append(('play', None))

    def stop(self):
        self.log.append(('stop', None))


class _FakeOut:
    def setVolume(self, v):
        pass


@pytest.fixture
def preview_audio(audio_project):
    """真正的 `StageAudio`，但播放器是假的（不在这台机器上出声）。"""
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from storygraph.ui_qt.stage_preview import StageAudio

    log = []
    audio = StageAudio(
        audio_project, registry=_Reg(music={'t': {'Path': 'game/bgm/theme.mp3'},
                                           'e': {'Path': 'game/bgm/Empt1y.mp3'}}),
        enabled=True,
        player_factory=lambda: (_FakePlayer(log), _FakeOut(),
                                _FakePlayer(log), _FakeOut()))
    audio.log = log
    return audio


class TestStageAudioPlayback:
    def test_bgm_starts_and_is_not_restarted_on_the_next_beat(self,
                                                              preview_audio):
        preview_audio.set_frame(_Frame(music='t', line_id='a'), key=('a', 0))
        plays = [c for c in preview_audio.log if c[0] == 'play']
        assert plays, '这一拍的 BGM 没有被播放'
        assert ('source', 'theme.mp3') in preview_audio.log

        preview_audio.log.clear()
        preview_audio.set_frame(_Frame(music='t', line_id='b'), key=('b', 1))
        assert [c for c in preview_audio.log if c[0] == 'play'] == [], \
            '同一首 BGM 在下一拍被重放了（应该继续放）'

    def test_changing_track_stops_and_plays_the_new_one(self, preview_audio):
        preview_audio.set_frame(_Frame(music='t', line_id='a'), key=('a', 0))
        preview_audio.log.clear()
        preview_audio.set_frame(_Frame(music='e', line_id='b'), key=('b', 1))
        kinds = [c[0] for c in preview_audio.log]
        assert 'stop' in kinds and 'play' in kinds
        assert ('source', 'Empt1y.mp3') in preview_audio.log

    def test_empty_music_stops_the_previous_track(self, preview_audio):
        preview_audio.set_frame(_Frame(music='t', line_id='a'), key=('a', 0))
        preview_audio.log.clear()
        preview_audio.set_frame(_Frame(music='', line_id='b'), key=('b', 1))
        assert [c[0] for c in preview_audio.log].count('stop') >= 1, \
            '这一拍没有音乐，但上一首还在响'

    def test_same_frame_redraw_does_not_replay_one_shot_sounds(self,
                                                               audio_project):
        """切「真实贴图」开关会重画同一帧 —— 不许把语音喊第二遍。"""
        pytest.importorskip('PySide6')
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from storygraph.ui_qt.stage_preview import StageAudio

        log = []
        reg = _Reg(sounds={'click': {'Path': 'game/audio/click.ogg'}})
        audio = StageAudio(
            audio_project, registry=reg, enabled=True,
            player_factory=lambda: (_FakePlayer(log), _FakeOut(),
                                    _FakePlayer(log), _FakeOut()))
        frame = _Frame(sound='click', line_id='same')
        audio.set_frame(frame, key=('same', 3))
        first = [c for c in log if c[0] == 'source' and 'click' in str(c[1])]
        assert first, '音效第一次就没响'
        log.clear()
        audio.set_frame(frame, key=('same', 3))          # 同一帧重画
        assert [c for c in log if 'click' in str(c)] == []

    def test_disabling_sound_stops_everything(self, preview_audio):
        preview_audio.set_frame(_Frame(music='t', line_id='a'), key=('a', 0))
        preview_audio.log.clear()
        preview_audio.set_enabled(False)
        assert [c[0] for c in preview_audio.log].count('stop') >= 1
        preview_audio.log.clear()
        preview_audio.set_frame(_Frame(music='e', line_id='b'), key=('b', 1))
        assert preview_audio.log == [], '关了声音还在放'

    def test_stop_clears_the_current_track(self, preview_audio):
        preview_audio.set_frame(_Frame(music='t', line_id='a'), key=('a', 0))
        preview_audio.stop()
        assert preview_audio._music_path is None
