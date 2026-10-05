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


class TestStringSpeakerSay:
    """
    `"角色名" "台词"`（**字符串说话人**）不许被截断成 `"角色名"`。

    2026-10-05 的 P0（真实项目 `character_gallery.rpy` 2 处）：

        "系统" "你已解锁角色【万理】的人物介绍！"
          → speaker='' + text='系统'          ← 后半句根本没进模型

    触发面**比"改那一句"大得多**：模型里没有那句台词，所以这一章只要动过
    **任何一处**，增量补丁就会把那一行改写成残缺的 `"系统"`。所以这里两条都钉：
    解析层（模型里必须有那句正文）+ 文件层（改同一章的另一行，正文不许消失）。
    """

    SRC = ('# 文件头：必须保住\n'
           'label sys_note:\n'
           '    ling "开头一句"\n'
           '    $ persistent.ready = True\n'
           '    "系统" "这是一句系统提示"\n'
           '    return\n')

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

    def test_the_dialogue_is_in_the_model(self, write_rpy):
        _fp, beats = _beats_of(write_rpy, self.SRC)
        say = next((b for b in beats if '这是一句系统提示' in str(b.text or '')),
                   None)
        assert say is not None, \
            f'模型里没有那句正文：{[(b.kind, b.speaker, b.text) for b in beats]}'
        assert say.speaker == '"系统"', repr(say.speaker)

    def test_editing_another_line_keeps_it_intact(self, write_rpy):
        def edit(b):
            if b.text == '开头一句':
                b.text = '开头改过了'
                return True
            return False
        fp, res = self._patch(write_rpy, self.SRC, edit)
        text = res.text
        assert '开头改过了' in text, text
        # 那一行**逐字节**还在（残缺版本是 `    "系统"`）
        assert '    "系统" "这是一句系统提示"' in text, text

    def test_untouched_file_is_byte_identical(self, write_rpy):
        fp, res = self._patch(write_rpy, self.SRC)
        assert res.text == fp.read_text(encoding='utf-8'), res.text


class TestO1AlignmentFixes:
    """
    增量对位那一批修复（2026-10-05，导出缺陷清单 O1）。

    清单口径：40 个真实文件里先前有 **6 个**"只改一句台词"走不成增量、
    退回全量重生成（58～425 行）。逐条查出来的病因**各不相同**，所以这里
    一条一钉（每条都能单独变红）：

      · 「旁白整句换写」那条兜底从前要求**文件里所有别的拍**都认得出自己 ——
        任何一块认不出（例如菜单头那一拍，区间只有 `menu:` 一行）就整体否掉；
      · 菜单拍的单拍渲染写出了占位符 `"\\x00menu"`，与整份产物对不上；
      · `lost_new` 只比"内容行"，于是**改了菜单标题**会静默成功、标题没改；
      · `$ time = 1#1500` 的行尾注释被"代码行换正文"顺手删掉；
      · 单引号选项 `'A':` 与生成结果 `"A"` 在"去冒号 + 去引号"**同时**出现时对不上；
      · `if` 体里的拍，生成结果多一行 `if …:`，对位守卫判"不是这一拍"；
      · 删掉 `if/else` 里的一拍后，剩下的拍会被配到**另一个分支**的行上。
    """

    #: 远处有一个"认不出自己"的块（`menu:` 头 + 标题单独一行）——
    #: 旧判据会因为它而否掉整份补丁
    FAR_MENU = ('label ch01:\n'
                '    "开场"\n'
                '    menu:\n'
                '        "标题行"\n'
                '        "选项":\n'
                '            jump ch01\n'
                '    "收尾"\n'
                '    return\n')

    IF_BODY = ('label ch01:\n'
               '    "开场"\n'
               '    if game.victory:\n'
               '        "赢了"\n'
               '    else:\n'
               '        "输了"\n'
               '    return\n')

    COND_MENU = ('label ch01:\n'
                 '    "开场"\n'
                 '    menu:\n'
                 '        "标题行"\n'
                 '        "选项":\n'
                 '            jump ch01\n'
                 '    return\n')

    COMMENT_CODE = ('label ch01:\n'
                    '    "第一句"\n'
                    '    $ time = 1#1500\n'
                    '    "第二句"\n'
                    '    return\n')

    QUOTED_OPTION = ("label ch01:\n"
                     "    \"开场\"\n"
                     "    menu:\n"
                     "        '第一章':\n"
                     "            jump ch01\n"
                     "    return\n")

    def _patch(self, write_rpy, src, edit=None):
        from pathlib import Path
        fp, beats = _beats_of(write_rpy, src)
        if edit is not None:
            edit(beats)
        full = render_beats_to_rpy(beats)
        return Path(fp), patch_export(beats, fp, full)

    @staticmethod
    def _changed_lines(before: str, after: str) -> list:
        a, b = before.splitlines(), after.splitlines()
        return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]

    def test_unrelated_block_does_not_kill_the_patch(self, write_rpy):
        """
        远处那块"认不出自己"的菜单拍**不许**连累这一句旁白的改写。

        （整句换写会走到"旁白槽位被替换"那条兜底：它从前的邻居判据是
        "文件里**所有**别的拍都认得出自己" —— 菜单头那一拍的区间只有
        `menu:` 一行，本来就认不出自己 → 整份补丁被否。）
        """
        def edit(beats):
            for b in beats:
                if b.text == '收尾':
                    b.text = '完全不同的一句'
        fp, res = self._patch(write_rpy, self.FAR_MENU, edit)
        assert res.ok, res.reasons
        assert '完全不同的一句' in res.text, res.text
        diff = self._changed_lines(fp.read_text(encoding='utf-8'), res.text)
        assert len(diff) == 1, [(i, res.text.splitlines()[i]) for i in diff]

    def test_new_content_check_covers_structure_lines(self):
        """
        `lost_new` 的判据必须覆盖**整拍**，不能只比"内容行"（台词/舞台/音频）。

        菜单头那一行既不是台词、也不是舞台/音频 —— 改了标题时它是"有新内容"
        的**唯一证据**：只比内容行 → 补丁报成功而标题没改
        （真实项目 `script_pomo.rpy`：`ok=True`、`changed=0`、产物里还是旧标题）。
        """
        from storygraph.story.patcher import _has_new_content
        orig = {'menu:', '"标题行":', '"选项":', 'jump ch01'}
        assert _has_new_content(['    menu "改过的标题":'], orig)
        # 没改的结构行不算"新内容"（它在原文里逐字存在，只是分行写法不同）
        assert not _has_new_content(['    menu:', '        "标题行":'], orig)

    def test_menu_head_single_beat_has_no_placeholder(self):
        """
        菜单拍的**单拍**渲染必须与整份产物给出同一个头（不许出
        `MENU_NO_CAPTION` 占位符）——否则增量补丁的"生成内容都在产物里"
        永远找不到那一行。
        """
        from storygraph.story.beat_model import Beat, KIND_MENU
        from storygraph.story.beat_render import render_beat_to_rpy
        no_cap = Beat(seq=1, label='a', kind=KIND_MENU, text='\x00menu')
        assert [ln for ln in render_beat_to_rpy(no_cap) if ln.strip()] == ['menu:']
        cap = Beat(seq=1, label='a', kind=KIND_MENU, text='标题')
        assert 'menu "标题":' in render_beat_to_rpy(cap)

    def test_caption_edit_never_reports_success_without_landing(self, write_rpy):
        """
        改了菜单标题：要么落进产物，要么**大声退回全量** ——
        不许"报成功、标题没改"（旧实现 `ok=True`、`changed=0`）。
        """
        def edit(beats):
            for b in beats:
                if b.kind == 'menu':
                    b.text = '改过的标题'
        _fp, res = self._patch(write_rpy, self.COND_MENU, edit)
        if res.ok:
            assert '改过的标题' in res.text, res.text
        else:
            assert any('新增内容' in r or '找不到' in r for r in res.reasons), \
                res.reasons

    def test_trailing_comment_on_a_code_line_survives(self, write_rpy):
        """`$ time = 1#1500` 的行尾注释是作者的，改别的行不许把它删掉。"""
        def edit(beats):
            for b in beats:
                if b.text == '第一句':
                    b.text = '第一句改过了'
        fp, res = self._patch(write_rpy, self.COMMENT_CODE, edit)
        assert res.ok, res.reasons
        assert '$ time = 1#1500' in res.text, res.text
        diff = self._changed_lines(fp.read_text(encoding='utf-8'), res.text)
        assert len(diff) == 1, [(i, res.text.splitlines()[i]) for i in diff]

    def test_single_quoted_option_is_not_reported_as_lost(self, write_rpy):
        """`'第一章':`（单引号 + 选项冒号）与生成结果 `"第一章"` 是同一件事。"""
        def edit(beats):
            for b in beats:
                if b.text == '开场':
                    b.text = '开场改过了'
        _fp, res = self._patch(write_rpy, self.QUOTED_OPTION, edit)
        assert res.ok, res.reasons
        assert "    '第一章':" in res.text, res.text

    def test_a_narration_inside_an_if_body_still_lands(self, write_rpy):
        """`if` 体的块头不是这一拍的内容 —— 改里面那句旁白照样要能落。"""
        def edit(beats):
            for b in beats:
                if b.text == '输了':
                    b.text = '输了改过了'
        fp, res = self._patch(write_rpy, self.IF_BODY, edit)
        assert res.ok, res.reasons
        assert '输了改过了' in res.text, res.text
        assert '    if game.victory:' in res.text, res.text
        diff = self._changed_lines(fp.read_text(encoding='utf-8'), res.text)
        assert len(diff) == 1, [(i, res.text.splitlines()[i]) for i in diff]

    def test_deleting_a_branch_body_does_not_move_the_other_line(self, write_rpy):
        """
        删掉 `else` 体那一拍之后，`if` 体那句必须**还在自己的分支里**。

        旧实现把它的行号配到了 `else` 那一行（对齐键不含条件，两个分支的旁白
        长得一模一样，`difflib` 为了让后面的 `menu:` 对齐而配错了）→
        台词**搬了个分支**还报成功（`TRICKY` 实测）。
        """
        from pathlib import Path
        fp, beats = _beats_of(write_rpy, TRICKY)
        beats = [b for b in beats if b.text != '好感不够']
        for b in beats:
            b.src_first = b.src_last = 0          # 模拟从表里读回来
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.ok, res.reasons
        assert '好感不够' not in res.text, res.text
        lines = res.text.splitlines()
        i_if = next(i for i, ln in enumerate(lines)
                    if ln.strip() == 'if love >= 5:')
        i_say = next(i for i, ln in enumerate(lines) if '好感够高' in ln)
        i_else = next((i for i, ln in enumerate(lines)
                       if ln.strip() == 'else:'), None)
        # 台词必须紧跟在 `if` 之后、**在 `else` 之前**
        assert i_say > i_if, lines
        assert i_else is None or i_say < i_else, lines

    def test_a_real_condition_change_is_not_silently_dropped(self, write_rpy):
        """
        改「条件」列之后，增量补丁**不许**报成功 —— 块头不属于拍的行区间，
        报成功就等于"条件没改"（静默 no-op）。退全量是正确结果。
        """
        def edit(beats):
            for b in beats:
                if b.text == '输了':
                    b.condition = 'other_flag'
        _fp, res = self._patch(write_rpy, self.IF_BODY, edit)
        if res.ok:
            assert 'other_flag' in res.text, \
                f'报成功却没改条件：{res.text}'
        else:
            assert any('条件' in r for r in res.reasons), res.reasons


class TestMenuRowsAndTheirNotes:
    """
    菜单行（`menu:` 头 / `"选项":` 行）与**行尾注释**（2026-10-05，清单 O6）。

    清单 O6 记的现象是"裸 `menu:` 带行尾注释时该章每次导出都退回全量"，
    报出来的理由是"1 句台词在补丁结果里找不到（例：`'\\x00menu'`）"。
    按清单建议只改那句判据（跳过占位符）**会掩盖真正的病**：探针一摊开就看到
    产物里多出一条**没有冒号的重复选项行**（`"一"  # 备注` 与
    `"一":  # 备注` 并存），而正确产物应该只有后一条。

    病根是**配对键**：源里写 `"选项":`（冒号是 `menu:` 块的结构语法），
    单拍渲染从前只写 `"选项"` —— 两个形状不同键 → 生成行被当"新增内容"
    插进去、旧行又因为"台词不许删"留着 → 重复行；同一处还会导致
    **小幅改选项文案时既不落也不报**（静默 no-op）。

    另一条（同一族，`hell_edit_rounds` 轴 B 第 7 轮抓到）：**带行尾注释的
    台词行认不出自己是台词**（`_SAY_RE` 要求整行就是 `说话人 "正文"`），
    改台词正文时同样会被插成两行。注释不是内容，身份要在去掉注释后算。
    """

    #: 裸 `menu:` 头 + 选项行**都带**行尾注释（O6 的中招形状）
    NOTED_MENU = ('label t:\n'
                  '    e "先说话"  # 备注：开场\n'
                  '    menu:  # 备注：菜单\n'
                  '        "一":  # 备注：左选项\n'
                  '            e "选一"  # 备注：左台词\n'
                  '    return\n')

    #: 单引号选项行（引号写法属于作者，增量不许顺手统一成双引号）
    QUOTED = ("label t:\n"
              "    e \"开场\"\n"
              "    menu:\n"
              "        '第一章':\n"
              "            jump t\n"
              "    return\n")

    def _patch(self, write_rpy, src, edit):
        from pathlib import Path
        fp, beats = _beats_of(write_rpy, src)
        edit(beats)
        return Path(fp), patch_export(beats, fp, render_beats_to_rpy(beats))

    def test_a_menu_head_note_does_not_duplicate_the_option_row(self, write_rpy):
        """O6 的形状：改别处的台词，产物**不许**多出一条选项行。"""
        def edit(beats):
            for b in beats:
                if b.text == '先说话':
                    b.text = '改过的台词'
        fp, res = self._patch(write_rpy, self.NOTED_MENU, edit)
        assert res.ok, res.reasons
        assert '改过的台词' in res.text, res.text
        lines = [ln.strip() for ln in res.text.splitlines() if ln.strip()]
        rows = [ln for ln in lines if ln.startswith('"一"')]
        assert len(rows) == 1, lines          # 只许有一条选项行
        assert rows[0] == '"一":  # 备注：左选项', rows
        assert '\x00' not in res.text, res.text
        assert res.text.count('备注：左选项') == 1, res.text

    def test_a_small_option_edit_lands(self, write_rpy):
        """
        小幅改选项文案（相似度过得了对位守卫）必须**真的落进产物** ——
        从前是 `ok=True`、`changed=0`、文案没变（静默 no-op）。
        """
        def edit(beats):
            for b in beats:
                if b.kind in ('menu_option', 'MENU_OPTION'):
                    b.text = '一。'
        _fp, res = self._patch(write_rpy, self.NOTED_MENU, edit)
        assert res.ok, res.reasons
        assert '"一。":' in res.text, res.text
        assert '"一":' not in res.text, res.text

    def test_option_rows_keep_the_authors_quotes(self, write_rpy):
        """改别处的台词，**不许**把作者的 `'第一章':` 顺手改成双引号。"""
        from pathlib import Path
        def edit(beats):
            for b in beats:
                if b.text == '开场':
                    b.text = '开场改过了'
        fp, res = self._patch(write_rpy, self.QUOTED, edit)
        assert res.ok, res.reasons
        assert "    '第一章':" in res.text, res.text
        before = Path(fp).read_text(encoding='utf-8').splitlines()
        after = res.text.splitlines()
        diff = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
        assert len(diff) == 1, [(i, after[i]) for i in diff]

    def test_a_say_line_with_a_note_is_not_duplicated(self, write_rpy):
        """带行尾注释的台词行：改正文要**替换**那一行，不是插一行新的。"""
        def edit(beats):
            for b in beats:
                if b.text == '选一':
                    b.text = '选一改过了'
        _fp, res = self._patch(write_rpy, self.NOTED_MENU, edit)
        assert res.ok, res.reasons
        assert '"选一改过了"  # 备注：左台词' in res.text, res.text
        assert res.text.count('备注：左台词') == 1, res.text
        assert res.text.count('选一') == 1, res.text


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
        """
        表里删掉一拍：**现在真的会删掉那几行**（2026-10-05 实现增量删行）。

        以前这里断言"必须退回全量"——那是"不支持删行"时代的契约。
        现在的契约是：删掉的行必须**真的从产物里消失**，而且其余部分
        一个字节不动（下面的 `test_deleted_beat_keeps_everything_else`）。
        """
        fp, beats = _beats_of(write_rpy, TRICKY)
        beats = [b for b in beats if b.text != '好感不够']
        for b in beats:
            b.src_first = b.src_last = 0        # 模拟"从表里读回来"没有行号
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        assert res.dropped_beats >= 1, '删掉的拍没被检测出来'
        assert res.removed_lines >= 1, '没有真的删行'
        assert '好感不够' not in res.text, '删掉的台词还在产物里'
        assert res.ok, f'删行被否掉：{res.reasons}'


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


class TestInsertedBeatLands:
    """
    2026-10-05 用户追问："你预料到增加新卡片或者删除原卡片的对照了吗？"

    实测（真实项目 `chapter1_2`，214 拍）：插入一张卡片 → 新拍 `src_first=0`
    → 补丁的 `usable` 把"没有行号的拍"整个过滤掉 → **新卡片的内容静默丢失**，
    而且报告"增量成功"（替换 0 行、无原因）。全量产物里倒是有 —— 也就是说
    用户点了导出、看到"成功"，文件里却没有那句台词。

    修法：给新拍一个**插入锚点**（前一拍的末行），合并时按"插入"处理，
    缩进借锚点行（`render_beat_to_rpy` 返回的是不带基础缩进的相对行）。
    """

    SRC = (
        'label ch01:\n'
        '    "第一句"\n'
        '    "第二句"\n'
        '    "第三句"\n'
        '    return\n'
    )

    def _loaded(self, write_rpy):
        fp = write_rpy(self.SRC, 'proj/game/ch01.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        return fp, beats

    def test_inserted_beat_is_spliced_in(self, write_rpy):
        from storygraph.story.beat_model import Beat
        fp, beats = self._loaded(write_rpy)
        beats.insert(1, Beat(seq=0, label='ch01', kind='say',
                             speaker='jc', text='新插进来的一句'))
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        assert '新插进来的一句' in res.text, '插入的台词没进产物'
        assert res.inserted_lines >= 1, '没有走插入通道'
        assert res.ok, f'增量被否掉：{res.reasons}'
        # 插在了正确的位置（第二句之后、原来的"第二句"之前）
        lines = res.text.splitlines()
        idx = next(i for i, ln in enumerate(lines) if '新插进来的一句' in ln)
        assert '第一句' in lines[idx - 1], lines
        assert '第二句' in lines[idx + 1], lines
        # **缩进必须跟着锚点**（否则 parser 会把文件结构读坏）
        assert lines[idx].startswith('    '), repr(lines[idx])

    def test_inserted_beat_product_reparses_with_one_more_beat(
            self, write_rpy, tmp_path):
        from storygraph.story.beat_model import Beat
        fp, beats = self._loaded(write_rpy)
        before = len(beats)
        beats.append(Beat(seq=0, label='ch01', kind='say', speaker='jc',
                          text='追加的一句'))
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        out = tmp_path / 'patched.rpy'
        out.write_text(res.text, encoding='utf-8')
        again = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(out)))
        assert len(again) == before + 1, \
            f'插入后拍数不对：{len(again)}（期望 {before + 1}）'

    def test_unmodified_file_is_still_byte_identical(self, write_rpy):
        """插入通道不许影响"没改就一个字节不动"这条契约。"""
        fp, beats = self._loaded(write_rpy)
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.text == fp.read_text(encoding='utf-8')
        assert res.inserted_lines == 0

    def test_inserted_beat_survives_a_second_export(self, write_rpy):
        """
        **连导两次**，插入的那句必须还在（用户 2026-10-05 实测的静默丢失）。

        根因：第一次导出把锚点写进了新拍的 `src_first/src_last`，于是它不再是
        "没有行号的拍" —— **第二次导出**被当成"替换一行"处理，对位守卫判
        "这段不像这一拍"就跳过；而"什么都没改"（补丁结果 == 原文）又跳过了整体
        校验 → 报告"增量补丁：改动 0 行"，产物里那句**没了**。
        修法：锚点存成运行期字段 `insert_anchor`，`src_first` 保持 0，
        每次导出都按"插入"重算；另外加一条"新增内容没落地 → 退回全量"的兜底。
        """
        from storygraph.story.beat_model import Beat
        fp, beats = self._loaded(write_rpy)
        beats.insert(1, Beat(seq=0, label='ch01', kind='say', speaker='jc',
                             text='连导两次也要在的一句'))
        full = render_beats_to_rpy(beats)
        res1 = patch_export(beats, fp, full)
        assert '连导两次也要在的一句' in res1.text, res1.reasons
        res2 = patch_export(beats, fp, full)
        assert '连导两次也要在的一句' in res2.text, \
            f'第二次导出把它丢了：ok={res2.ok} reasons={res2.reasons}'
        assert res2.inserted_lines >= 1, '第二次导出没有走插入通道'
        # 而且**绝不许报告成功却丢内容**
        assert res2.ok or res2.reasons


class TestTableRowInsertLands:
    """
    用户 2026-10-05：

    > 增量的修改没有问题，插入功能却永远只插到前面……

    **表格那条路**（.xlsx 里插一行 → 「⤒ 从剧本表写回 .rpy」）实测抓到两个坑：

      · 表里读出来的拍**没有行号**，位置靠 `difflib` 对位现算；对位给新增行
        的"插入点"原先被当成**替换**处理 —— 插入点正好落在 `label ch01:`
        那一行时，对位守卫判"这段不像这一拍"就跳过了，而"什么都没改"时
        又跳过了整体校验 → **导出报告成功、产物里却没有那句台词**；
      · 于是"在最前面插一行"这种写法直接就没了。

    修法：对位算出来的插入点收进 `inserted_out`，交给**插入通道**（缩进借锚点
    行）；另外加一道"新增的拍一行都没落地 → 退回全量"的兜底，宁可重生成，
    也绝不报告成功却丢内容。
    """

    SRC = (
        '# 文件头注释（必须保住）\n'
        'label ch01:\n'
        '    scene bg room\n'
        '    "第一句旁白"\n'
        '    ling "有说话人的一句"\n'
        '    "第三句旁白"\n'
        '    return\n'
    )

    def _table_beats(self, write_rpy):
        """按**表格那条路**准备：解析出来之后把行号全清掉。"""
        fp = write_rpy(self.SRC, 'proj/game/ch01.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        for b in beats:
            b.src_first = b.src_last = 0
        return fp, beats

    def test_row_inserted_at_the_front_actually_lands(self, write_rpy):
        from storygraph.story.beat_model import Beat
        fp, beats = self._table_beats(write_rpy)
        beats.insert(0, Beat(seq=0, label='ch01', kind='say', speaker='jc',
                             text='表格里新插的第一行'))
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert '表格里新插的第一行' in res.text, \
            f'新增行被静默丢了：{res.reasons}'
        assert res.inserted_lines >= 1, '没有走插入通道'
        lines = res.text.splitlines()
        i = next(k for k, ln in enumerate(lines)
                 if '表格里新插的第一行' in ln)
        assert lines[i].startswith('    '), f'缩进丢了：{lines[i]!r}'
        assert lines[i - 1].strip().startswith('label'), lines[i - 1]

    def test_row_inserted_in_the_middle_lands_at_its_own_place(self, write_rpy):
        from storygraph.story.beat_model import Beat
        fp, beats = self._table_beats(write_rpy)
        # 拍序列 = [scene+第一句旁白, 有说话人的一句, 第三句旁白]
        # 插在中间（第 2 个下标）→ 应该落在"有说话人的一句"之后
        beats.insert(2, Beat(seq=0, label='ch01', kind='say', speaker='jc',
                             text='表格里新插的中间行'))
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        lines = res.text.splitlines()
        i = next(k for k, ln in enumerate(lines)
                 if '表格里新插的中间行' in ln)
        assert '有说话人的一句' in lines[i - 1], lines
        assert '第三句旁白' in lines[i + 1], lines

    def test_never_reports_success_when_a_new_row_did_not_land(self, write_rpy):
        """
        **绝不"报告成功、东西没了"**：新增的拍一行都没落地时，必须 ok=False
        （调用方据此退回全量重生成）。
        """
        from storygraph.story.beat_model import Beat
        fp, beats = self._table_beats(write_rpy)
        # 一个**诡异的锚点**（文件头那一行之前，没有前邻居）：
        # 无论补丁怎么处理，只要产物里没有这句，就不许说 ok。
        beats.insert(0, Beat(seq=0, label='不存在的label', kind='say',
                             speaker='jc', text='没有源拍可依附的一句'))
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        landed = '没有源拍可依附的一句' in res.text
        assert landed or not res.ok, \
            f'报告成功却没有那句台词：ok={res.ok} reasons={res.reasons}'


class TestInsertIntoBlockStaysStructurallyValid:
    """
    用户 2026-10-05："插入功能却永远只插到前面" 排查时抓到的一类：

    在**菜单选项拍**后面插一张卡，锚点行是 `"去屋顶看看":`（块头），
    紧贴着插出来的产物是

        menu:
            "去屋顶看看":
            "新台词"          ← 和选项同级 —— Ren'Py 要的是缩进块，直接不认
                jump ch01_roof

    修法（`_insert_anchor`）：锚点是块头时退到**最外层包围块**（`menu:`），
    把新行插在**整个块之后**、与块头同级；`label …:` 例外（插在它后面正是
    "这一章第一拍"）。
    """

    SRC = (
        'label ch01:\n'
        '    "开场旁白"\n'
        '    menu:\n'
        '        "去屋顶看看":\n'
        '            jump ch01_roof\n'
        '        "先回教室":\n'
        '            jump ch01_class\n'
        '\n'
        'label ch01_roof:\n'
        '    "屋顶"\n'
        '    return\n'
        '\n'
        'label ch01_class:\n'
        '    "教室"\n'
        '    return\n'
    )

    def _loaded(self, write_rpy):
        fp = write_rpy(self.SRC, 'proj/game/ch01.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        return fp, beats

    def _insert_after(self, write_rpy, match, text='新插的一句'):
        from storygraph.story.beat_model import Beat
        fp, beats = self._loaded(write_rpy)
        idx = next(i for i, b in enumerate(beats)
                   if match in (b.text or ''))
        beats.insert(idx + 1, Beat(seq=0, label=beats[idx].label or 'ch01',
                                   kind='say', speaker='jc', text=text))
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        return res, text

    def test_after_the_last_menu_option_it_leaves_the_block(self, write_rpy):
        res, text = self._insert_after(write_rpy, '先回教室')
        lines = res.text.splitlines()
        i = next(k for k, ln in enumerate(lines) if text in ln)
        indent = len(lines[i]) - len(lines[i].lstrip())
        assert indent == 4, \
            f'插成了块里的一句（缩进 {indent}）：{lines[max(0, i - 3):i + 2]}'
        assert 'jump ch01_class' in lines[i - 1], lines[max(0, i - 3):i + 2]
        assert res.text.index(text) < res.text.index('label ch01_roof')

    def test_after_the_first_menu_option_too(self, write_rpy):
        res, text = self._insert_after(write_rpy, '去屋顶看看')
        lines = res.text.splitlines()
        i = next(k for k, ln in enumerate(lines) if text in ln)
        indent = len(lines[i]) - len(lines[i].lstrip())
        assert indent == 4, f'缩进不对：{lines[max(0, i - 3):i + 2]}'

    def test_after_a_label_head_is_the_first_beat_of_that_label(self, write_rpy):
        """锚点是 `label ch01:` 时插在它后面 —— 别一路退到几百行以后。"""
        from storygraph.story.beat_model import Beat
        fp, beats = self._loaded(write_rpy)
        beats.insert(0, Beat(seq=0, label='ch01', kind='say', speaker='jc',
                             text='这一章的第一句'))
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        lines = res.text.splitlines()
        i = next(k for k, ln in enumerate(lines) if '这一章的第一句' in ln)
        assert lines[i - 1].strip().startswith('label'), lines[max(0, i - 3):i + 2]
        assert lines[i].startswith('    '), repr(lines[i])


class TestEditedBeatActuallyLands:

    """
    2026-10-05 用**真实项目**验证增量生成时抓到的两个 P0：

      ① 改**旁白**（没有说话人的台词）→ 导出后那句台词**静默保持原样**；
      ② 删掉一拍 → 导出后那一拍**还在文件里**（且报告"增量成功"）。

    ①的根因：`_range_looks_like` 用 `beat.text`（**当前**文本）判断"这段原文
    属于这一拍"，而用户刚把它改掉 —— 原文里当然找不到新文本，于是判成
    "源码区间对不上"跳过。有说话人的台词因为还能匹配说话人，所以一直没暴露。
    ②的根因：`dropped`（"表里删了拍"）只在 `recover_source_ranges` 里算，
    而那个函数**只在拍上没有行号时**才被调用；编辑器那条路拍上都带行号
    （载入章节 → 删卡片 → 导出），于是删拍检测整个被跳过。
    """

    SRC = (
        '# 文件头注释（必须保住）\n'
        'label ch01:\n'
        '    scene bg room\n'
        '    "第一句旁白"\n'
        '    ling "有说话人的一句"\n'
        '    "第三句旁白"\n'
        '    return\n'
    )

    @staticmethod
    def _loaded(write_rpy, src=None):
        """按**编辑器那条路**载入：走 beats_from_parse_result（带行号）。"""
        fp = write_rpy(src or TestEditedBeatActuallyLands.SRC,
                       'proj/game/ch01.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        return fp, beats

    def test_edited_narration_lands_in_the_patch(self, write_rpy):
        fp, beats = self._loaded(write_rpy)
        narr = next(b for b in beats if not (b.speaker or '').strip()
                    and (b.text or '').strip())
        narr.text = narr.text + '（改过）'
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        assert '（改过）' in res.text, \
            f'改过的旁白没进产物（stale={res.stale_beats}）'
        assert res.ok, f'增量被否掉：{res.reasons}'
        # 没动的地方逐字节不动
        assert res.text.splitlines()[0] == '# 文件头注释（必须保住）'
        assert 'ling "有说话人的一句"' in res.text

    def test_edited_narration_only_changes_one_line(self, write_rpy):
        fp, beats = self._loaded(write_rpy)
        narr = next(b for b in beats if not (b.speaker or '').strip()
                    and (b.text or '').strip())
        narr.text = narr.text + '！'
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        orig = fp.read_text(encoding='utf-8').splitlines()
        assert res.changed_lines == 1, f'改动行数不是 1：{res.changed_lines}'
        new = res.text.splitlines()
        diff = [i for i, (a, b) in enumerate(zip(orig, new)) if a != b]
        assert len(diff) == 1, f'不同的行不止一处：{diff}'

    def test_deleted_beat_keeps_everything_else(self, write_rpy):
        """
        删一拍：**只有那一拍的行消失**，其余（含文件头注释）逐字节不动。

        这是"增量删行"的核心契约 —— 全量重生成同样能删掉那一拍，
        但它会把注释和排版一起重排，那正是用户不要的。
        """
        fp, beats = self._loaded(write_rpy)
        gone = beats[1].text
        beats.pop(1)
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.ok, f'删行被否掉：{res.reasons}'
        assert res.removed_lines == 1, res.removed_lines
        before = fp.read_text(encoding='utf-8').splitlines()
        expected = [ln for ln in before if gone not in ln]
        assert res.text.splitlines() == expected, '删多了或删错了行'
        assert res.text.splitlines()[0] == '# 文件头注释（必须保住）'

    def test_deleted_beat_is_detected_even_with_line_numbers(self, write_rpy):
        """
        拍上**有行号**时也要查"表里删了拍"（编辑器那条路就是这样），
        而且现在**真的会删行**（2026-10-05 实现了增量删行）。
        """
        fp, beats = self._loaded(write_rpy)
        assert beats[0].src_first, '这条用例要的是"带行号"的拍'
        gone = beats[1].text
        beats.pop(1)                      # 删掉中间那一拍
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.dropped_beats >= 1, '删掉的拍没被检测出来'
        assert res.removed_lines >= 1, '没有真的删行'
        assert gone not in res.text, f'删掉的台词还在产物里：{gone!r}'
        assert res.ok, f'删行被否掉：{res.reasons}'

    def test_deleted_beat_is_actually_gone_after_full_fallback(self, write_rpy):
        """退回全量之后，删掉的那一拍必须真的不在产物里。"""
        fp, beats = self._loaded(write_rpy)
        gone = beats[1].text
        beats.pop(1)
        full = render_beats_to_rpy(beats)
        res = patch_export(beats, fp, full)
        final = res.text if res.ok else full
        assert gone not in final, f'删掉的台词还在产物里：{gone!r}'

    def test_empty_chapter_reason_is_distinct(self, write_rpy):
        """纯 init/python 文件没有拍 —— 原因要与"没有源文件"分开报。"""
        fp = write_rpy('init python:\n    x = 1\n', 'proj/game/pure.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        res = patch_export(beats, fp, 'generated')
        assert not res.ok
        assert any('没有解析出任何拍' in r for r in res.reasons), res.reasons
