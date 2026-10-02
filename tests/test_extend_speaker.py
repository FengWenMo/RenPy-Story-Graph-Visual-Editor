"""
tests/test_extend_speaker.py
============================
`extend` 拍的说话人归属（"幽灵旁白"回归）。

背景（用户 2026-09-28 报告，我复现后**改写了根因**）：

用户的原始判断是「`show` 等无 speaker 的舞台指令覆盖了后续台词的 speaker」。
我用了 5 条独立路径验证，**这个根因不成立**：
  · `beats_from_statements` 里只有 SAY 分支给 `pending.speaker` 赋值，
    show/hide/scene 分支根本不碰它（结构上也不会碰——`Statement` 上没有
    `.speaker`，说话人在 `payload` 里）；
  · 真实章节 `chapter1_3.rpy` 第 50 拍解析出来是 `speaker='jc'`，
    与源码 `jc "铁汁啊铁汁…"` 一致；生成代码也是 `jc "…"` 而非旁白。

但报告指向的**现象是真的**，真凶是 `extend`：它在 Ren'Py 里表示
"续上一句"，渲染成 `extend "…"`（不带说话人），于是 12 条 `extend` 拍
在表里/卡片上 Speaker 全是空的，看起来跟旁白一模一样。
最典型的一处：

    #51 lyj "真不好意思啊，"              ← lyj 说半句
    #52 extend "铁汁我好像就是妹子啊…"     ← 其实是 lyj 续说，但看不出人

**改法**：给 `extend` 拍填上"最后一个说话人"作为**显示用**归属。
渲染仍然走 EXTEND 分支（`extend "…"`），**不会**生成 `lyj "…"`——
把续句变成新台词是错的。本文件把这两件事同时钉住。
"""

from pathlib import Path

import pytest

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.script_table import (
    KIND_EXTEND, KIND_NARRATION, beats_from_parse_result,
    render_beats_to_rpy,
)

# 真实项目路径统一从 `_source_paths` 取（环境变量 STORYGRAPH_REAL_PROJECT 可覆盖）
from _source_paths import REAL_CHAPTER as REAL       # noqa: E402

MINI = '''label t:
    lyj "前半句，"
    extend "后半句。"
    jc "换人说话。"
    extend "又续一句。"
    "旁白。"
    extend "旁白续写。"
'''


def parse(text, tmp_path, name='t.rpy'):
    fp = tmp_path / name
    fp.write_text(text, encoding='utf-8')
    return beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(fp)))


class TestExtendInheritsSpeaker:
    def test_extend_inherits_previous_speaker(self, tmp_path):
        beats = parse(MINI, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert len(ext) == 3
        assert ext[0].speaker == 'lyj'
        assert ext[1].speaker == 'jc'

    def test_extend_after_narration_has_no_speaker(self, tmp_path):
        """旁白后面的 extend 续的是旁白，不该硬安一个说话人上去。"""
        beats = parse(MINI, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert ext[2].speaker == '', '旁白续写被安上了说话人'

    def test_no_speaker_change_means_same_speaker(self, tmp_path):
        text = ('label t:\n    jc "一"\n    jc "二"\n'
                '    extend "三"\n')
        beats = parse(text, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert ext[0].speaker == 'jc'


class TestExtendRenderingIsUnchanged:
    """
    **这一条最关键**：`extend` 的显示归属不能改变生成代码。
    生成 `jc "又续一句。"` 会把续句变成新台词——语义错了。
    """

    def test_extend_still_renders_as_extend(self, tmp_path):
        beats = parse(MINI, tmp_path)
        out = render_beats_to_rpy(beats)
        for frag in ('extend "后半句。"', 'extend "又续一句。"',
                     'extend "旁白续写。"'):
            assert frag in out, f'缺少 {frag}：\n{out}'

    def test_no_speaker_prefix_on_extend_lines(self, tmp_path):
        beats = parse(MINI, tmp_path)
        out = render_beats_to_rpy(beats)
        for line in out.splitlines():
            s = line.strip()
            if s.startswith('extend'):
                for who in ('lyj ', 'jc '):
                    assert not s.startswith(f'extend {who}'), \
                        f'续句被安上了说话人：{s}'


class TestExtendBoundaries:
    """
    归属的三条边界规则（用户 2026-09-28 明确要求）：

      1. **跨拍重置**：遇到 `scene` / `hide` / `return` 时重置归属；
      2. **链式继承**：`A say → extend → extend`，两个 extend 都归属 A；
      3. **旁白切断链**：`A say → extend → 旁白 → extend`，第二个 extend 必须空。

    原则：**宁可标成空（策划会去补），也不要安错人。**
    """

    def test_chain_inheritance_two_extends(self, tmp_path):
        """链式继承：连续两个 extend 都归属 A。"""
        text = ('label t:\n'
                '    lyj "一"\n'
                '    extend "二"\n'
                '    extend "三"\n')
        beats = parse(text, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert len(ext) == 2
        assert [b.speaker for b in ext] == ['lyj', 'lyj']

    def test_narration_breaks_the_chain(self, tmp_path):
        """旁白切断链：第二个 extend 必须为空。"""
        text = ('label t:\n'
                '    lyj "一"\n'
                '    extend "二"\n'
                '    "旁白。"\n'
                '    extend "四"\n')
        beats = parse(text, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert len(ext) == 2
        assert ext[0].speaker == 'lyj'
        assert ext[1].speaker == '', '旁白之后的 extend 不该继承 lyj'

    def test_scene_resets_attribution(self, tmp_path):
        """换景 = 换说话语境 → 后面的 extend 归属清空。"""
        text = ('label t:\n'
                '    lyj "一"\n'
                '    scene bg room\n'
                '    extend "二"\n')
        beats = parse(text, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert ext[0].speaker == '', 'scene 之后没有重置归属'

    def test_hide_resets_attribution(self, tmp_path):
        """说话人退场了，续句归属不确定 → 宁可空着。"""
        text = ('label t:\n'
                '    lyj "一"\n'
                '    hide lyj\n'
                '    extend "二"\n')
        beats = parse(text, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert ext[0].speaker == '', 'hide 之后没有重置归属'

    def test_speaker_change_resets_attribution(self, tmp_path):
        """另一个角色插话，原说话人的续句归属不再成立。"""
        text = ('label t:\n'
                '    lyj "一"\n'
                '    jc "插一句"\n'
                '    extend "二"\n')
        beats = parse(text, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert ext[0].speaker == 'jc', '应该归属最后说话的人'

    def test_return_resets_attribution(self, tmp_path):
        """一章结束，extend 不可能跨 label 续写。"""
        text = ('label t:\n'
                '    lyj "一"\n'
                '    return\n'
                '\n'
                'label u:\n'
                '    extend "二"\n')
        beats = parse(text, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert ext[0].speaker == '', 'return 之后没有重置归属'

    def test_jump_resets_attribution(self, tmp_path):
        """跳转是控制流中断，续句归属不再成立。"""
        text = ('label t:\n'
                '    lyj "一"\n'
                '    jump other\n'
                '\n'
                'label other:\n'
                '    extend "二"\n')
        beats = parse(text, tmp_path)
        ext = [b for b in beats if b.kind == 'extend']
        assert ext[0].speaker == '', 'jump 之后没有重置归属'


class TestExtendBoundariesDoNotChangeRendering:
    """三条边界规则**只影响显示**——生成代码里 extend 永远是 extend。"""

    CHAIN = '''label t:
    lyj "一"
    extend "二"
    extend "三"
    "旁白。"
    extend "四"
    scene bg room
    extend "五"
'''

    def test_all_still_render_as_extend(self, tmp_path):
        beats = parse(self.CHAIN, tmp_path)
        out = render_beats_to_rpy(beats)
        for frag in ('extend "二"', 'extend "三"', 'extend "四"',
                     'extend "五"'):
            assert frag in out, f'{frag} 丢了：\n{out}'

    def test_no_speaker_ever_prefixed_to_extend(self, tmp_path):
        beats = parse(self.CHAIN, tmp_path)
        out = render_beats_to_rpy(beats)
        for line in out.splitlines():
            s = line.strip()
            if s.startswith('extend') and not s.startswith('extend "'):
                raise AssertionError(f'续句被安上了说话人：{s}')


class TestSpeakerMissingGuard:
    """
    防御性警告：**有台词却没有说话人**时，卡片必须显形。

    为什么需要（用户 2026-09-28 报"幽灵旁白"）：当时卡片上什么都没提示，
    只看得出 Speaker 是空的——而空着也能点「下一句」，错误就被带过去了。
    宁可吵一点，也不要让"会变成旁白"的拍静默通过。

    2026-10-01：判据搬到了 `app.beats.speaker_missing_warning`（纯函数，
    只算文案），Qt 卡片负责显示（`BeatCardEditor.set_note`）。
    以前要靠继承 tkinter 面板造 `Probe` 才能测 —— 现在直接测纯函数。
    """

    @staticmethod
    def _warn(beat):
        from storygraph.app.beats import speaker_missing_warning
        return speaker_missing_warning(beat)

    def test_warns_when_say_has_no_speaker(self):
        from storygraph.story.script_table import Beat
        note = self._warn(Beat(seq=1, kind='say', speaker='', text='有台词'))
        assert note and '旁白' in note

    def test_silent_when_speaker_present(self):
        from storygraph.story.script_table import Beat
        assert self._warn(
            Beat(seq=1, kind='say', speaker='jc', text='有台词')) == ''

    def test_silent_for_narration(self):
        """旁白本来就该没有说话人，别吵。"""
        from storygraph.story.script_table import Beat
        assert self._warn(
            Beat(seq=1, kind=KIND_NARRATION, speaker='', text='旁白')) == ''

    def test_silent_for_extend(self):
        """`extend` 本来就不带说话人（续上一句），别吵。"""
        from storygraph.story.script_table import Beat
        assert self._warn(
            Beat(seq=1, kind=KIND_EXTEND, speaker='', text='续句')) == ''

    def test_silent_when_no_text(self):
        from storygraph.story.script_table import Beat
        assert self._warn(
            Beat(seq=1, kind='say', speaker='', text='')) == ''

    def test_the_card_actually_shows_it(self, qapp, ui_window):
        """
        纯函数算得对还不够 —— 卡片必须**真的显示出来**。
        这一条盯的是"文案算了但没人接"（幽灵旁白当年就是这样：
        数据层知道、界面不知道）。
        """
        ui_window._show_index(0)
        beat = ui_window.beats[0]
        beat.speaker = ''
        beat.kind = 'say'
        beat.text = '有台词但没人说'
        ui_window._show_index(0)
        assert ui_window._panel._note_label.isVisible() or \
            ui_window._panel._note_label.text(), \
            '有台词没说话人时卡片没有任何提示'


@pytest.mark.skipif(not REAL.exists(), reason='本机没有 chapter1_3.rpy')
class TestRealChapterRegression:
    def test_beat_50_speaker_is_jc(self, tmp_path):
        """
        用户报告"#50 说话人丢了"的那一拍。

        真实数据是 `speaker='jc'`（源码第 80 行 `jc "铁汁啊铁汁…"`）。
        这条用例是**反向的**：它证明数据层没丢，防止以后有人
        为了"修幽灵旁白"而在这里加错误的兜底。
        """
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(REAL)))
        target = [b for b in beats if '铁汁啊铁汁' in (b.text or '')]
        assert target, '找不到那一拍'
        assert target[0].speaker == 'jc'
        assert target[0].kind == 'say'
        # 生成也必须是 jc 说的，不是旁白
        out = render_beats_to_rpy([target[0]])
        assert 'jc "铁汁啊铁汁' in out, out

    def test_all_extends_have_a_display_speaker_or_are_narration(self, tmp_path):
        """
        真实章节里 12 条 `extend` 现在都该有显示用说话人
        （除非它续的是旁白）。
        """
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(REAL)))
        ext = [b for b in beats if b.kind == 'extend']
        assert len(ext) == 12, f'extend 条数变了：{len(ext)}'
        named = [b for b in ext if b.speaker]
        assert len(named) == 12, \
            f'还有 extend 没有显示说话人：{[(b.line_id, b.speaker) for b in ext if not b.speaker]}'

    def test_rendering_unchanged_for_whole_chapter(self, tmp_path):
        """整章渲染里不该出现"角色 + 台词"形式的 extend。"""
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(REAL)))
        out = render_beats_to_rpy(beats)
        bad = [l for l in out.splitlines()
               if l.strip().startswith('extend ')
               and not l.strip().startswith('extend "')]
        assert not bad, f'这些 extend 被改坏了：{bad[:5]}'
