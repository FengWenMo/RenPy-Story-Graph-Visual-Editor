"""
tests/test_patch_grow_shrink.py
===============================
**增量补丁能增删整行了**（用户 2026-10-05："继续实现，清除结构债，并确保没有bug产生"）。

以前最硬的一条不变量是"**行数永不变化**"（`_replace_matched` 一对一替换）。
它保住了"不改坏结构"，代价是两个真问题：

  ① **清掉一条 `show` 删不掉** —— 补丁报成功，产物里 sprite 还在（**静默错产物**）；
  ② **给控制流卡新填台词填不进去** —— 新内容既不落地，整章还得退回全量（注释全丢）。

这一版把增删做进"平整区间"，并用三条准入线保证**不产生新 bug**：

  · 区间平整（无块结构 / 单一缩进）——`menu:` / `if` 里永远不增删；
  · 配对顺序与渲染顺序一致（单调）；
  · 要增/删的那一行，其"身份"在对面不存在，且不是"同一句话的另一种写法"。

本文件把上面每一条都钉住，另外锁住这一轮抓到的**两个真 bug**：
  · `# TODO: 缺少资源 …` 是"说明"不是内容 —— 不许拿它顶掉作者原有的行
    （实测：不做这条，"什么都不改"的导出会把 12~16 行 `play music` 变成注释）；
  · `'file "c:\\a.py", line 24'` 这种**单引号台词**配不上双引号的生成行 →
    会把同一句话插成两行（现在按引号/转义归一后配对，并保留原文那一行）。
"""

from __future__ import annotations

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.patcher import _replace_matched, patch_export


def _patch(write_rpy, src, edit, name='proj/game/ch.rpy'):
    """写一份源文件 → 解析成拍 → `edit(beats)` → 打补丁。返回 (产物, 结果)。"""
    fp = write_rpy(src, name)
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(fp)))
    edit(beats)
    res = patch_export(beats, fp, render_beats_to_rpy(beats))
    return res, fp


# ----------------------------------------------------------------------
#  ① 删除：清掉一条 show，文件里那一行必须跟着走
# ----------------------------------------------------------------------

class TestShrink:
    BASE = ('label ch01:\n'
            '    scene bg room\n'
            '    show ye happy\n'
            '    yhj "第一句"\n'
            '    return\n')

    def test_cleared_show_is_removed_from_the_file(self, write_rpy):
        def edit(beats):
            for b in beats:
                if (b.show or '').strip():
                    b.show = ''
        res, _fp = _patch(write_rpy, self.BASE, edit)
        assert res.ok, res.reasons
        assert 'show ye happy' not in res.text, '清掉的 show 还留在产物里'
        assert res.shrunk_lines == 1
        assert 'yhj "第一句"' in res.text

    def test_removing_a_stage_line_keeps_comments_and_blank_lines(self, write_rpy):
        src = ('label ch01:\n'
               '    # 开场注释\n'
               '    show ye happy\n'
               '\n'
               '    yhj "第一句"\n')
        def edit(beats):
            for b in beats:
                b.show = ''
        res, _fp = _patch(write_rpy, src, edit)
        assert res.ok, res.reasons
        assert '# 开场注释' in res.text
        assert 'show ye happy' not in res.text

    def test_never_drops_a_say_or_comment_line(self, write_rpy):
        """台词与注释永不被"顺手删掉"（那是删卡片 / 手工的事）。"""
        out, changed, unlanded, grown, shrunk = _replace_matched(
            ['    # 注释', '    yhj "台词"'], [])
        assert out == ['    # 注释', '    yhj "台词"']
        assert (changed, grown, shrunk) == (0, 0, 0)


# ----------------------------------------------------------------------
#  ② 增长：控制流卡新填台词 / 给某一拍加一条 show
# ----------------------------------------------------------------------

class TestGrow:
    CTRL = ('label ch01_control:\n'
            '    call chapter1_2 from _call_chapter1_2\n'
            '    jump chapter1_3_control\n')

    def test_text_typed_on_a_control_flow_card_lands(self, write_rpy):
        def edit(beats):
            b = next(x for x in beats if 'jump' in (x.raw or ''))
            b.kind, b.speaker, b.text = 'say', 'yhj', '新填的一句'
        res, _fp = _patch(write_rpy, self.CTRL, edit)
        assert res.ok, res.reasons
        assert res.grown_lines == 1
        assert 'yhj "新填的一句"' in res.text
        # 原有的 `from` 子句一个字都不许动
        assert 'call chapter1_2 from _call_chapter1_2' in res.text

    def test_new_show_is_inserted_before_the_dialogue(self, write_rpy):
        src = ('label ch01:\n'
               '    "第一句"\n'
               '    return\n')
        def edit(beats):
            beats[0].show = 'ye happy'
        res, _fp = _patch(write_rpy, src, edit)
        assert res.ok, res.reasons
        lines = [ln.strip() for ln in res.text.splitlines()]
        assert 'show ye happy' in lines
        assert lines.index('show ye happy') < lines.index('"第一句"'), \
            '新加的 show 必须排在台词前面'

    def test_reordered_render_never_reorders_the_file(self, write_rpy):
        """配对不单调（渲染顺序与源码顺序不一致）→ 退回一对一，不增删。"""
        out, changed, unlanded, grown, shrunk = _replace_matched(
            ['    show ye happy', '    yhj "第一句"'],
            ['    yhj "第一句"', '    show ye happy'])
        assert grown == 0 and shrunk == 0, '单调性判据失效了'
        assert out[0] == '    show ye happy', '原文顺序被改了'


# ----------------------------------------------------------------------
#  ③ 代码行：改了脚本逻辑要落地（以前静默保持旧代码）
# ----------------------------------------------------------------------

class TestCodeLineEdit:
    SRC = ('label ch01:\n'
           '    $ love += 1\n'
           '    yhj "第一句"\n'
           '    return\n')

    def test_code_line_edit_replaces_in_place(self, write_rpy):
        def edit(beats):
            for b in beats:
                if 'love' in (b.code or ''):
                    b.code = 'love += 2'
        res, _fp = _patch(write_rpy, self.SRC, edit)
        assert res.ok, res.reasons
        assert '$ love += 2' in res.text and '$ love += 1' not in res.text

    def test_code_line_is_dropped_when_it_disappears(self, write_rpy):
        def edit(beats):
            for b in beats:
                if 'love' in (b.code or ''):
                    b.code = ''
        res, _fp = _patch(write_rpy, self.SRC, edit)
        assert res.ok, res.reasons
        assert '$ love' not in res.text and res.shrunk_lines == 1


# ----------------------------------------------------------------------
#  ④ 不许写出重复行 / 不许拿说明注释顶掉作者的行
# ----------------------------------------------------------------------

class TestNoDuplicates:
    def test_call_with_from_clause_is_never_duplicated(self, write_rpy):
        """生成器写 `call X`、原文是 `call X from Y` → 不许插成两行。"""
        src = ('label c:\n'
               '    call chapter1_2 from _call_chapter1_2\n')
        res, _fp = _patch(write_rpy, src, lambda beats: None)
        assert res.text.count('call chapter1_2') == 1, res.text

    def test_single_quoted_say_is_never_duplicated(self, write_rpy):
        """原文单引号、生成器双引号转义 → 同一句话，不许插第二遍。"""
        src = ('label ch01:\n'
               '    \'file "c:\\\\a.py", line 24\'\n'
               '    return\n')
        res, _fp = _patch(write_rpy, src, lambda beats: None)
        assert res.text.count('line 24') == 1, res.text
        assert res.text == _read(res), '没改却动了文件'

    def test_missing_resource_todo_never_replaces_the_authors_line(
            self, write_rpy, monkeypatch):
        """
        `# TODO: 缺少资源 …` 是说明，不是内容：

        `pipeline/compile.py` 写明的约定是"**源文件里原有的行按原样保留**"。
        实测（真实 chapter1_4）：不做这条，"什么都不改"的导出会把
        `play music "bgm/当雪融化，我在…….mp3" fadein 1.0` 变成一行 TODO 注释 ——
        整章音乐全没了。
        """
        src = ('label ch01:\n'
               '    play music "bgm/找不到的曲子.mp3" fadein 1.0\n'
               '    yhj "第一句"\n'
               '    return\n')
        fp = write_rpy(src, 'proj/game/ch.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        # 手工把这一拍的音乐换成一个资源表里没有的名字，模拟"渲染出 TODO"
        for b in beats:
            if (getattr(b, 'music', '') or '').strip():
                b.music = 'bgm/找不到的曲子.mp3'
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.ok, res.reasons
        assert 'play music "bgm/找不到的曲子.mp3" fadein 1.0' in res.text, \
            '作者原有的 play music 行被 TODO 注释顶掉了'
        assert res.text == fp.read_text(encoding='utf-8'), '没改却动了文件'


def _read(res):
    return res.source_path.read_text(encoding='utf-8')


# ----------------------------------------------------------------------
#  ⑤ 结构安全：块结构里永远不增删
# ----------------------------------------------------------------------

class TestStructureIsNeverTouched:
    BLOCK = ('label ch01:\n'
             '    if love >= 5:\n'
             '        yhj "好感够高"\n'
             '    else:\n'
             '        yhj "好感不够"\n'
             '    return\n')

    def test_no_growth_inside_a_block(self, write_rpy):
        from storygraph.story.patcher import _flat_editable_range
        assert not _flat_editable_range(['    if love >= 5:'])
        assert not _flat_editable_range(['    menu:'])
        assert not _flat_editable_range(['        yhj "A"', '            "B"'])
        assert _flat_editable_range(['    show ye happy', '    yhj "A"'])

    def test_edit_inside_if_block_replaces_only_the_line(self, write_rpy):
        def edit(beats):
            for b in beats:
                if (b.text or '').strip() == '好感够高':
                    b.text = '好感变高了'
        res, fp = _patch(write_rpy, self.BLOCK, edit)
        assert res.ok, res.reasons
        assert '好感变高了' in res.text
        # 结构原样：块头、缩进、else 分支都在
        assert '    if love >= 5:' in res.text
        assert '    else:' in res.text
        assert '        yhj "好感不够"' in res.text
        assert res.grown_lines == 0 and res.shrunk_lines == 0

    def test_menu_stays_intact(self, write_rpy):
        """
        菜单标题拍的正文在 `menu:` 的下一行（解析器的行号怪癖），所以"改标题"
        这一拍**可以**老实退回全量；但无论走哪条路，**结构一个字都不许坏**。
        """
        src = ('label ch01:\n'
               '    menu:\n'
               '        "选哪个？"\n'
               '        "A":\n'
               '            jump ch02\n')
        def edit(beats):
            for b in beats:
                if (b.text or '').strip() == '选哪个？':
                    b.text = '你想选哪个？'
        res, _fp = _patch(write_rpy, src, edit)
        assert '    menu:' in res.text, '菜单结构被破坏了'
        assert '        "A":' in res.text
        assert '            jump ch02' in res.text
        assert (res.grown_lines, res.shrunk_lines) == (0, 0), \
            '块结构里不许增删行'
        if res.ok:
            assert '你想选哪个？' in res.text


# ----------------------------------------------------------------------
#  ⑥ 不动点：产物再导一次必须逐字节不变
# ----------------------------------------------------------------------

class TestFixedPoint:
    SRC = ('label ch01:\n'
           '    # 注释\n'
           '    scene bg room\n'
           '    show ye happy at i11\n'
           '    $ love += 1\n'
           '    yhj "第一句"\n'
           '    extend "续一句"\n'
           '    "旁白一句"\n'
           '    return\n')

    def test_noop_is_byte_identical(self, write_rpy):
        fp, beats = None, None
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.story.beat_render import render_beats_to_rpy
        from storygraph.story.patcher import patch_export
        fp = write_rpy(self.SRC, 'proj/game/ch.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        original = fp.read_text(encoding='utf-8')
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.ok, res.reasons
        assert res.text == original, '什么都不改却动了文件'
        assert res.changed_lines == 0

    def test_second_export_of_the_product_changes_nothing(self, write_rpy,
                                                          tmp_path):
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.story.beat_render import render_beats_to_rpy
        from storygraph.story.patcher import patch_export
        fp = write_rpy(self.SRC, 'proj/game/ch.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        target = next(b for b in beats if (b.text or '').strip() == '第一句')
        target.text = '改过的台词'
        first = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert first.ok, first.reasons
        second_path = tmp_path / 'again.rpy'
        second_path.write_text(first.text, encoding='utf-8')
        again = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(second_path)))
        res2 = patch_export(again, second_path, render_beats_to_rpy(again))
        assert res2.ok, res2.reasons
        assert res2.text == first.text, '产物再导一次居然变了（不是不动点）'


# ----------------------------------------------------------------------
#  ⑦ 静态网抓到的真缺陷：准入判据里有一个**看不见的名字**
# ----------------------------------------------------------------------

class TestNoUndefinedNames:
    """
    `tests/test_no_undefined_names.py`（AST 静态网）在 2026-10-10 抓到：
    `_is_new_content_row` 的**函数体**用了 `old_lines`，但签名里没有它。

    后果不是"少一条判据"，而是**两个后果叠加**：
      · 走到那一行 → `NameError`（补丁直接炸）；
      · 更糟的是这条判据本来是"`# TODO: 缺少资源 …` 不许顶掉作者原有的行"
        —— 也就是说，真到那一步，作者的音乐行会被 TODO 注释顶掉。

    所以这里既钉住"不许炸"，也钉住"那条判据真的在起作用"。
    """

    def test_new_content_row_accepts_the_old_lines_it_needs(self):
        from storygraph.story.patcher import _is_new_content_row
        import inspect
        params = inspect.signature(_is_new_content_row).parameters
        assert 'old_lines' in params, (
            '函数体用了 old_lines，签名里却没有 —— 走到那一行就是 NameError')

    def test_a_todo_note_never_kills_the_authors_line(self):
        """
        `_is_new_content_row` 对"说明型 TODO + 原文里有同一个引用"必须返回
        **False**（= 不许插），且**不许抛异常**。
        """
        from storygraph.story.patcher import _is_new_content_row, _squash
        old_lines = ['    play music "bgm/找不到的曲子.mp3" fadein 1.0']
        todo = "    # TODO: 缺少资源 音乐 'bgm/找不到的曲子.mp3'"
        assert _is_new_content_row(
            todo,
            old_heads=set(),                       # 身份不撞（逼它走到那条分支）
            old_squash=[_squash(l) for l in old_lines],
            file_squash=[],
            old_lines=old_lines) is False, 'TODO 说明被当成了新内容'

