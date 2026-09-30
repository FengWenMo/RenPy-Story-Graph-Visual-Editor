"""
tests/test_incremental_export.py
================================
**增量导出（受控补丁）**的回归测试。

用户 2026-09-28 的决定：

> 对原有的程序做增量的修改，而不完全生成一个新的程序，会不会更好呢？
> ……保留注释，全量修改出来的东西，根本就没办法看！！！
> 目前先只支持"改台词/表情/位置"这类行内替换（覆盖 90% 日常改动）

用真实两章算出来的差距（为什么非做不可）：

| 指标 | 全量重生成 | 增量补丁 |
|---|---|---|
| 与原文相似度 | 0.8764 → 0.9246 | **0.9561** |
| 丢失的行 | 18 行（含 6 条音频参数） | **0** |
| 多出的行 | 13 行（重复 hide/scene） | **0** |
| 文件头注释 | 消失 | **保留** |
| 实际改动行数 | 全文件 | **25 行** |

这个文件锁的是"**没动的行一个字节都不变**"这条硬保证。
"""

import re

import pytest

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.patcher import patch_export

CH = r'D:\千连万理\RenPyTest\sr_general\game\chapter'
REAL = f'{CH}\\chapter1_3.rpy'

SRC = '''# 文件头注释：这一行**必须保住**
# 作者手写的说明

label ch01:
    scene bg classroom
    # 靠左站，双人对话时主角在左
    show ling happy at left
    ling "第一句"
    "旁白一句"
    ling "第三句"
    return
'''


class TestIncrementalPreservesEverything:
    """核心保证：没改的行逐字节不变。"""

    def _roundtrip(self, write_rpy, src):
        from pathlib import Path
        fp = write_rpy(src, 'proj/game/ch01.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        return Path(fp), res

    def test_header_comments_survive(self, write_rpy):
        """**用户最在意的**：文件头注释必须保住。"""
        _fp, res = self._roundtrip(write_rpy, SRC)
        assert '# 文件头注释：这一行**必须保住**' in res.text
        assert '# 作者手写的说明' in res.text

    def test_inline_comment_survives(self, write_rpy):
        _fp, res = self._roundtrip(write_rpy, SRC)
        assert '# 靠左站，双人对话时主角在左' in res.text

    def test_return_survives(self, write_rpy):
        """`return` 不属于任何一拍，但**绝不能被补丁删掉**（控制流）。"""
        _fp, res = self._roundtrip(write_rpy, SRC)
        assert re.search(r'^\s*return\s*$', res.text, re.M), res.text

    def test_label_survives(self, write_rpy):
        _fp, res = self._roundtrip(write_rpy, SRC)
        assert 'label ch01:' in res.text

    def test_dialogue_kept(self, write_rpy):
        _fp, res = self._roundtrip(write_rpy, SRC)
        for line in ('"第一句"', '"旁白一句"', '"第三句"'):
            assert line in res.text, f'{line} 丢了：\n{res.text}'

    def test_result_is_reparsable(self, write_rpy, tmp_path):
        """补丁产物必须能被自己的解析器吃回去（否则等于生成坏脚本）。"""
        fp, res = self._roundtrip(write_rpy, SRC)
        out = tmp_path / 'out.rpy'
        out.write_text(res.text, encoding='utf-8')
        p = RenPyParser()
        p.parse(out)
        assert not p.errors, p.errors


class TestIncrementalOnlyTouchesChangedLines:
    def test_unchanged_file_is_byte_identical(self, write_rpy):
        """
        **什么都没改时，产物必须与原文逐字节相同** ——
        这是"只改行内"的最强证明。
        """
        from pathlib import Path
        fp = write_rpy(SRC, 'proj/game/ch01.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        original = Path(fp).read_text(encoding='utf-8')
        assert res.text == original, (
            '没改任何东西，产物却和原文不同：\n'
            + '\n'.join(f'  原 {a!r}\n  新 {b!r}'
                        for a, b in zip(original.splitlines(),
                                        res.text.splitlines()) if a != b))

    def test_no_source_file_falls_back_to_full(self, write_rpy, tmp_path):
        """没有源文件时退回全量（不能崩，也不能丢内容）。"""
        from pathlib import Path
        fp = write_rpy(SRC, 'proj/game/ch01.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, tmp_path / 'nope.rpy', full)
        assert res.text == full
        assert res.source_path is None
        assert not res.ok


class TestCoverage:
    """覆盖率不到 0.9 就不该声称"走了增量"。"""

    def test_parsed_beats_almost_all_have_lines(self, write_rpy):
        from pathlib import Path
        fp = write_rpy(SRC, 'proj/game/ch01.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        have = [b for b in beats if getattr(b, 'src_first', 0)]
        assert len(have) == len(beats), \
            f'这些拍没有源行号：{[b.line_id for b in beats if not b.src_first]}'


# ==================================================================
#  2026-09-30 重写后的**强契约**（旧实现在这些点上全部不达标）
# ==================================================================

#: 带 `if` / `menu` / 注释 / 空行 / transform 的脚本 —— 最容易被打坏的那种
TRICKY = '''# 1.3 通过考验
# 作者手写的文件头注释

transform i11:
    xpos 0.2

label ch01:
    # 开场注释
    scene bg room

    # 该谁说话了
    lyj "第一句"

    if love >= 5:
        lyj "好感够高"
    else:
        lyj "好感不够"

    menu:
        "选哪个？"
        "A":
            jump ch02

    show ling happy at i11
    "旁白一句"
    return
'''


def _beats_of(write_rpy, src, name='proj/game/ch01.rpy'):
    from pathlib import Path
    fp = write_rpy(src, name)
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(fp)))
    return Path(fp), beats


class TestOneToOneReplacement:
    """
    改写后的核心不变量：**行数永不变化，没改的行逐字节不变**。

    旧实现是"整段区间替换 + 猜缩进"，实测在 `if` 体里把 8 空格压成 4 空格
    （**语法直接坏掉**），还因为"生成器把注释提到拍首"多插了 24 行。
    """

    def _patch(self, write_rpy, src, edit=None):
        from pathlib import Path
        fp, beats = _beats_of(write_rpy, src)
        if edit is not None:
            for b in beats:
                if edit(b):
                    break
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        return Path(fp), res

    def test_line_count_never_changes(self, write_rpy):
        fp, res = self._patch(write_rpy, TRICKY)
        original = fp.read_text(encoding='utf-8')
        assert len(res.text.splitlines()) == len(original.splitlines()), \
            '补丁不许让行数变化（增删行是下一轮的事）'

    def test_tricky_script_is_byte_identical_when_untouched(self, write_rpy):
        """注释 / 空行 / if / menu / jump / transform 一个字节都不许动。"""
        fp, res = self._patch(write_rpy, TRICKY)
        assert res.text == fp.read_text(encoding='utf-8')
        assert res.ok, res.reasons

    def test_if_body_indentation_survives_after_edit(self, write_rpy):
        """`if` 体里那行被改时，**缩进必须保持 8 空格**（结构不是补丁的事）。"""
        def edit(b):
            if b.text == '好感够高':
                b.text = '好感爆表'
                return True
            return False
        fp, res = self._patch(write_rpy, TRICKY, edit)
        assert '        lyj "好感爆表"' in res.text, res.text
        assert '\n    lyj "好感爆表"' not in res.text, '缩进被压平了'
        assert res.ok, res.reasons

    def test_menu_option_colon_survives(self, write_rpy):
        """菜单选项行 `"A":` 结尾的冒号不许被吃掉（旧实现会丢）。"""
        fp, res = self._patch(write_rpy, TRICKY)
        assert '"A":' in res.text

    def test_dialogue_edit_changes_exactly_one_line(self, write_rpy):
        def edit(b):
            if b.text == '第一句':
                b.text = '改过的台词'
                return True
            return False
        fp, res = self._patch(write_rpy, TRICKY, edit)
        a = fp.read_text(encoding='utf-8').splitlines()
        b = res.text.splitlines()
        assert len(a) == len(b)
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        assert diff == [a.index('    lyj "第一句"')], \
            f'应当只差一行，实际差这些行：{[(a[i], b[i]) for i in diff]}'

    def test_show_attribute_edit_keeps_name_spelling(self, write_rpy):
        """
        `show ling close_happy` 不会被写成 `show ling_close_happy`
        —— 那是"把能跑的脚本改坏"（`test_roundtrip` 也在守这条）。
        """
        def edit(b):
            if b.show:
                b.show = 'ling close_happy'
                return True
            return False
        fp, res = self._patch(write_rpy, TRICKY, edit)
        assert 'ling_close_happy' not in res.text, res.text

    def test_show_edit_replaces_the_same_tag_line(self, write_rpy):
        """同一角色的表情改了 → 那一行换掉，且缩进不动。"""
        def edit(b):
            if b.show:
                b.show = 'ling serious'
                return True
            return False
        fp, res = self._patch(write_rpy, TRICKY, edit)
        assert '    show ling serious' in res.text, res.text


class TestFailSafe:
    """
    **要么全对，要么完全不动**：任何可疑情况都必须退回全量，绝不写半对产物。
    """

    def test_stale_ranges_never_change_the_file(self, write_rpy):
        """
        源文件被手工改过 → 行号失效。旧实现**照样写入**（实测整体下移 1 行，
        259 行被改写）。现在的底线是**"解释得通"**：

          · 只是错位、没有真改动 → 产物必须还是原文（一个字节都不许动）
          · 错位 + 有改动 → 要么按身份配对**正确落地**，要么整份拒绝；
            **不许出现第三种结果**（改到别的行上）
        """
        fp, beats = _beats_of(write_rpy, TRICKY)
        original = fp.read_text(encoding='utf-8')

        shifted = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        for b in shifted:
            if b.src_first:
                b.src_first += 1
                b.src_last += 1
        res0 = patch_export(shifted, fp, render_beats_to_rpy(shifted))
        assert res0.text == original, '错位不许乱改原文'

        for b in shifted:
            if b.text == '第一句':
                b.text = '改过的台词'
        res1 = patch_export(shifted, fp, render_beats_to_rpy(shifted))
        a = original.splitlines()
        b = res1.text.splitlines()
        assert len(a) == len(b), '行数不许变'
        if res1.ok:
            diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
            assert diff == [a.index('    lyj "第一句"')], \
                f'只许改那一行，实际：{[(a[i], b[i]) for i in diff]}'
        else:
            assert res1.reasons, '拒绝时必须说明原因'

    def test_unplaceable_content_is_rejected(self, write_rpy):
        """
        改动**落不了地**（这一拍的区间里根本没有它的内容）→ 必须拒绝。
        否则就是"表里改了、导出没变"，而用户以为改好了。
        """
        fp, beats = _beats_of(write_rpy, TRICKY)
        victim = beats[1]
        victim.src_first = victim.src_last = 3      # 指向一个空行
        victim.text = '这一段落到哪里去呢'
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        assert not res.ok, res.text
        # 拒绝的理由要么写在校验里，要么体现在覆盖率上（对位率不足）
        assert res.reasons or res.coverage < 0.9, \
            f'reasons={res.reasons} coverage={res.coverage}'

    def test_deleted_beat_is_reported(self, write_rpy):
        """表里删掉一拍：本轮不支持删行 → 必须报出来并退回全量
        （否则源文件与表永远不一致，而用户以为删掉了）。"""
        fp, beats = _beats_of(write_rpy, TRICKY)
        beats = [b for b in beats if b.text != '好感不够']
        for b in beats:
            b.src_first = b.src_last = 0        # 模拟"从表里读回来"没有行号
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        assert res.dropped_beats >= 1
        assert not res.ok
        assert any('删' in r for r in res.reasons)


class TestRangeRecovery:
    """
    从表里读出来的拍**没有行号**（`src_first/src_last` 是运行期元数据，
    不入 Excel）→ 旧实现覆盖率恒为 0%，功能全程是死的。
    现在会解析源 .rpy 把行号找回来。
    """

    def test_ranges_are_recovered_by_content(self, write_rpy):
        from storygraph.story.patcher import recover_source_ranges
        fp, beats = _beats_of(write_rpy, TRICKY)
        for b in beats:
            b.src_first = b.src_last = 0
        matched, total, dropped = recover_source_ranges(beats, fp)
        assert matched == total == len(beats), (matched, total)
        assert dropped == 0
        assert all(b.src_first for b in beats)

    def test_recovered_patch_is_still_byte_identical(self, write_rpy):
        fp, beats = _beats_of(write_rpy, TRICKY)
        for b in beats:
            b.src_first = b.src_last = 0
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        assert res.text == fp.read_text(encoding='utf-8')
        assert res.ok, res.reasons

    def test_recovered_ranges_survive_a_text_edit(self, write_rpy):
        """对齐键**刻意不含台词正文** —— 所以改台词之后照样对得上。"""
        fp, beats = _beats_of(write_rpy, TRICKY)
        for b in beats:
            b.src_first = b.src_last = 0
        for b in beats:
            if b.text == '第一句':
                b.text = '改过的台词'
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        assert res.ok, res.reasons
        assert '"改过的台词"' in res.text
        a = fp.read_text(encoding='utf-8').splitlines()
        b = res.text.splitlines()
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        assert len(diff) == 1, [(a[i], b[i]) for i in diff]


@pytest.mark.skipif(not __import__('pathlib').Path(REAL).exists(),
                    reason='本机没有真实章节')
class TestRealChapterByteIdentity:
    """真实章节上的最强契约。"""

    def _result(self, edit=None):
        from pathlib import Path
        src = Path(REAL)
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(src)))
        if edit is not None:
            edit(beats)
        full = render_beats_to_rpy(beats)
        return src, patch_export(beats, src, full)

    def test_no_edit_is_byte_identical(self):
        """305 拍、571 行：什么都不改 → **逐字节等于原文**（旧实现差 4 行）。"""
        src, res = self._result()
        assert res.text == src.read_text(encoding='utf-8')
        assert res.changed_lines == 0
        assert res.ok, res.reasons

    def test_one_dialogue_edit_changes_one_line(self):
        def edit(beats):
            for b in beats:
                if b.text and len(b.text) > 6:
                    b.text = '改过的台词'
                    return
        src, res = self._result(edit)
        a = src.read_text(encoding='utf-8').splitlines()
        b = res.text.splitlines()
        assert len(a) == len(b), '行数不许变'
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        assert len(diff) == 1, f'应当只差 1 行，实际 {len(diff)} 行'
        assert res.ok, res.reasons

    def test_one_stage_edit_changes_one_line(self):
        def edit(beats):
            for b in beats:
                if b.show:
                    b.show = b.show.split()[0] + ' serious'
                    return
        src, res = self._result(edit)
        a = src.read_text(encoding='utf-8').splitlines()
        b = res.text.splitlines()
        assert len(a) == len(b)
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        assert len(diff) == 1, [(a[i], b[i]) for i in diff]
        assert res.ok, res.reasons


@pytest.mark.skipif(not __import__('pathlib').Path(REAL).exists(),
                    reason='本机没有真实章节')
class TestRealChapterIncremental:
    """真实章节：零丢失、零多出、相似度高于全量。"""

    def _result(self):
        from pathlib import Path
        src = Path(REAL)
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(src)))
        full = render_beats_to_rpy(beats)
        return src, full, patch_export(beats, src, full)

    def test_no_lines_lost_or_added(self):
        """**这条是核心**：全量会丢 18 行、多 13 行；增量必须都不发生。"""
        import collections
        src, _full, res = self._result()
        a = collections.Counter(l.strip() for l in
                                src.read_text(encoding='utf-8').splitlines()
                                if l.strip())
        b = collections.Counter(l.strip() for l in
                                res.text.splitlines() if l.strip())
        lost = {k: v - b.get(k, 0) for k, v in a.items() if v > b.get(k, 0)}
        added = {k: v - a.get(k, 0) for k, v in b.items() if v > a.get(k, 0)}
        assert not lost, f'增量产物丢了 {sum(lost.values())} 行：' \
                         f'{list(lost)[:5]}'
        assert not added, f'增量产物多了 {sum(added.values())} 行：' \
                          f'{list(added)[:5]}'

    def test_audio_params_intact(self):
        src, _full, res = self._result()
        orig = src.read_text(encoding='utf-8')
        for pat in (r'fadein\s+[\d.]+', r'fadeout\s+[\d.]+',
                    r'stop music\s+fadeout', r'play music'):
            assert len(re.findall(pat, orig)) == \
                len(re.findall(pat, res.text)), pat

    def test_closer_to_original_than_full_regeneration(self):
        """增量必须比全量更接近原文（否则这个功能没意义）。"""
        import difflib
        src, full, res = self._result()
        orig = src.read_text(encoding='utf-8').splitlines()
        r_full = difflib.SequenceMatcher(
            None, orig, full.splitlines()).ratio()
        r_inc = difflib.SequenceMatcher(
            None, orig, res.text.splitlines()).ratio()
        assert r_inc > r_full, f'增量 {r_inc:.4f} 没超过全量 {r_full:.4f}'

    def test_header_comment_survives(self):
        src, _full, res = self._result()
        first = src.read_text(encoding='utf-8').splitlines()[0]
        assert res.text.splitlines()[0] == first, \
            f'文件头注释丢了：{first!r} -> {res.text.splitlines()[0]!r}'

    def test_verbatim_dollar_lines(self):
        """`$persistent...`（`$` 后无空格）必须原样保住。"""
        src, _full, res = self._result()
        orig = src.read_text(encoding='utf-8')
        tight = re.findall(r'^\s*\$\S.*$', orig, re.M)
        if not tight:
            pytest.skip('这一章没有 `$x` 紧写形式')
        for t in tight:
            assert t in res.text, f'`$x` 紧写形式被改写成 `$ x`：{t!r}'

    def test_reparsable(self, tmp_path):
        _src, _full, res = self._result()
        out = tmp_path / 'patched.rpy'
        out.write_text(res.text, encoding='utf-8')
        p = RenPyParser()
        p.parse(out)
        assert not p.errors, p.errors
