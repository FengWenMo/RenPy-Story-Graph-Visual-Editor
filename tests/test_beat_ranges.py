"""
tests/test_beat_ranges.py
=========================
**拍的行区间**（`src_first/src_last`）是增量导出能成立的唯一地基。

用户 2026-10-05（"开始下一轮修改"）实测到的两类错位，都是这里出的问题：

  ① **行号被上一拍抢走**：`scene` 撞 `scene` / `play` 撞 `play` 时先"落拍"，
     而当前这条语句的行号在落拍**之前**就记进了上一拍 ——
     真实 chapter1_1 的 ppt 段（bg1_2 / pause / bg6_1 / pause / bg3_1）
     于是变成"第 18 拍 31-32、第 19 拍 33-33"，补丁把 32 行写成上一拍的背景，
     `scene bg bg3_1` 无处可放（产物里背景是错的），整体校验发现后整章退回
     全量重生成 → **手写注释全丢**。

  ② **`extend` 拍的行区间和下一拍交叠**：`extend` 自成一张卡，却不带走
     pending 里攒的舞台/代码行，于是那些行号被**下一句台词**那一拍吞掉 ——
     真实 test.rpy：extend 拍 18-18 落在下一拍 16-23 里面，补丁遇到区间交叠
     只能跳过后拍 → 对位率 71% → 又退全量。

修完之后本文件锁两条不变量：
  · **任意两拍的行区间不许交叠**（交叠 = 补丁只能放弃其中一拍）；
  · **每句台词的行号必须落在自己那张卡里**（否则改台词会改到别人那一行）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result


def _beats(write_rpy, src, name='proj/game/ch.rpy'):
    fp = write_rpy(src, name)
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(fp)))
    return fp, beats


def _ranges(beats):
    return [(int(getattr(b, 'src_first', 0) or 0),
             int(getattr(b, 'src_last', 0) or 0)) for b in beats]


def _assert_no_overlap(beats, what=''):
    spans = [r for r in _ranges(beats) if r[0]]
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            a, b = spans[i], spans[j]
            assert not (a[0] <= b[1] and a[1] >= b[0]), \
                f'{what} 第 {i + 1} 与第 {j + 1} 拍的行区间交叠：{a} / {b}'


class TestRangesAreDisjointAndContainTheirOwnLine:
    PPT = (
        'label ch01:\n'
        '    # ppt 效果\n'
        '    scene bg a\n'
        '    pause 0.1\n'
        '    scene bg b\n'
        '    pause 0.1\n'
        '    scene bg c\n'
        '    "第一句"\n'
        '    "第二句"\n'
        '    return\n'
    )

    def test_stage_lines_belong_to_the_following_line(self, write_rpy):
        """`bg c` 属于"第一句"那一拍；它不能被上一拍（bg b 那段）抢走。"""
        _fp, beats = _beats(write_rpy, self.PPT)
        first_line_beat = next(b for b in beats if b.text == '第一句')
        assert int(first_line_beat.src_first) <= 7 <= int(first_line_beat.src_last), \
            f'`scene bg c`（第 7 行）不在"第一句"那一拍里：' \
            f'{first_line_beat.src_first}-{first_line_beat.src_last}'
        _assert_no_overlap(beats, 'ppt 段')

    def test_every_say_line_is_inside_its_own_beat(self, write_rpy):
        fp, beats = _beats(write_rpy, self.PPT)
        lines = fp.read_text(encoding='utf-8').splitlines()
        for b in beats:
            text = (b.text or '').strip()
            if not text:
                continue
            first, last = int(b.src_first), int(b.src_last)
            assert any(text in ln for ln in lines[first - 1:last]), \
                f'第 {getattr(b, "seq", "?")} 拍的台词不在自己的区间里：' \
                f'{text[:12]!r} @ {first}-{last}'

    EXTEND = (
        'label ch01:\n'
        '    # 初始出场\n'
        '    show ye happy\n'
        '    $ renpy.transition(Dissolve(0.1))\n'
        '    yhj "第一句{nw}"\n'
        '\n'
        '    show ye hmph\n'
        '    $ renpy.transition(Dissolve(0.1))\n'
        '    extend "第二句"\n'
        '\n'
        '    show ye care\n'
        '    $ renpy.transition(Dissolve(0.1))\n'
        '    yhj "第三句{nw}"\n'
    )

    def test_extend_beat_takes_the_stage_lines_before_it(self, write_rpy):
        """
        `extend` 也是"这一拍"：它前面的 `show/transition` 属于它，
        不能留给下一句台词（否则两张卡的行区间会交叠）。
        """
        _fp, beats = _beats(write_rpy, self.EXTEND)
        ext = next(b for b in beats if (b.text or '').startswith('第二句'))
        # `Beat.show` 存的是图像名本身（渲染时补 `show `），所以这里比 `ye hmph`
        assert 'ye hmph' in (ext.show or ''), \
            f'extend 那张卡没拿到它前面的表情变化：{ext.show!r}'
        assert 'Dissolve' in (ext.code or ''), '代码行也没跟过来'
        # 行区间 = [show ye hmph(7), extend(9)]
        assert (int(ext.src_first), int(ext.src_last)) == (7, 9), \
            f'extend 拍的行区间不对：{ext.src_first}-{ext.src_last}'
        _assert_no_overlap(beats, 'extend 段')

    def test_extend_keeps_display_speaker(self, write_rpy):
        """显示用的说话人仍然保留（渲染成 extend，不写说话人）。"""
        _fp, beats = _beats(write_rpy, self.EXTEND)
        ext = next(b for b in beats if (b.text or '').startswith('第二句'))
        assert ext.kind == 'extend' and ext.speaker == 'yhj'


class TestNoOpPatchStaysIncremental:
    """"什么都不改"必须**保住注释**：走增量补丁、产物逐字节等于原文。"""

    TRICKY = (
        '# 文件头注释\n'
        'label ch01:\n'
        '    # ppt 效果\n'
        '    scene bg bg1_2\n'
        '    pause 0.1\n'
        '    scene bg bg3_1\n'
        '    "宿舍，食堂，教学楼，三点一线。"\n'
        '    xqlz"pass."\n'
        '    yhj"结。"   ###注意：这里作者留了个问号\n'
        '    return\n'
    )

    def test_tight_speaker_and_trailing_comment_tolerated(self, write_rpy):
        """
        `yhj"结。"`（说话人后没空格）+ 行尾作者注释，生成器写 `yhj "结。"` ——
        按逐字节比会判"生成内容找不到"→ 整章退全量（真实 chapter3_1 就是这样）。
        现在按"去掉空白后是否出现过"来判，于是走增量、文件逐字节不变。
        """
        from storygraph.story.beat_render import render_beats_to_rpy
        from storygraph.story.patcher import patch_export
        fp, beats = _beats(write_rpy, self.TRICKY)
        original = fp.read_text(encoding='utf-8')
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.ok, f'白白退回全量了：{res.reasons}'
        assert res.text == original, '没改却动了文件'
        assert '###注意' in res.text, '作者注释丢了'

    def test_edit_still_lands_and_keeps_comments(self, write_rpy):
        from storygraph.story.beat_render import render_beats_to_rpy
        from storygraph.story.patcher import patch_export
        fp, beats = _beats(write_rpy, self.TRICKY)
        for b in beats:
            if b.text.strip() == '宿舍，食堂，教学楼，三点一线。':
                b.text = '改过的台词'
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.ok, res.reasons
        assert '改过的台词' in res.text
        assert '###注意' in res.text and '# 文件头注释' in res.text


@pytest.mark.skipif(not os.environ.get('STORYGRAPH_REAL_PROJECT'),
                    reason='需要真实项目（STORYGRAPH_REAL_PROJECT）')
class TestRealProjectRanges:
    """真实项目全量扫一遍：区间不许交叠、台词必须在自己区间里。"""

    def _chapters(self):
        root = Path(os.environ['STORYGRAPH_REAL_PROJECT'])
        return [p for p in sorted((root / 'game' / 'chapter').glob('*.rpy'))
                if not p.name.endswith('.generated.rpy')
                and p.stat().st_size > 200]

    def test_all_chapters(self):
        from storygraph.app.editor import EditorSession
        bad = []
        for ch in self._chapters():
            s = EditorSession(project_root=ch.parent.parent.parent)
            s.load(ch)
            spans = [(int(getattr(b, 'src_first', 0) or 0),
                      int(getattr(b, 'src_last', 0) or 0)) for b in s.beats]
            spans = [x for x in spans if x[0]]
            for i in range(len(spans)):
                for j in range(i + 1, len(spans)):
                    a, b = spans[i], spans[j]
                    if a[0] <= b[1] and a[1] >= b[0]:
                        bad.append(f'{ch.name}: {a} 与 {b} 交叠')
        assert bad[:5] == [], f'{len(bad)} 处区间交叠'
