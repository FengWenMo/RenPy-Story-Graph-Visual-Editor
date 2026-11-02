# -*- coding: utf-8 -*-
"""
判据 · **第三轮 P0**（2026-10-24 深夜）
====================================

对应 `docs/审计_P0第三轮_2026-10-24.md` 的三条。写法与第二轮同一套：
`EditorSession.load → 操作 → export(原地)`，断言**产物逐行该长什么样**。

  · P0-9 菜单标题改不动（`menu:` 的标题在块里的下一行，不属任何一拍）
  · P0-9b `menu "标题":`（标题在头行）的**保真**：零改动逐字节相同
  · P0-10 删一拍会把相邻 `elif` 展平（改坏没碰过的结构行）
  · P0-11 移动拍可以越过 `return`（静默把台词变成死代码）

判据的最高一条仍是审计给的：**只动该动的行** —— 改动波及
`if / elif / else / menu` 等结构行时，一律先红。
"""
from __future__ import annotations

import difflib
import shutil
from pathlib import Path

import pytest

from storygraph.app.editor import EditorSession

MARKER = '# 由剧本表自动生成'
REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


# ----------------------------------------------------------------------
# 工具（与 `test_p0_round2` 同源：端到端 = 载入 → 改 → 原地导出）
# ----------------------------------------------------------------------
def _project(tmp_path, src: str):
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    session = EditorSession(project_root=tmp_path)
    session.load(path)
    return session, path


def _read(path):
    return open(path, encoding='utf-8', newline='').read()


def _body(text: str) -> list:
    out, skip_blank = [], False
    for ln in text.splitlines():
        if ln.startswith(MARKER):
            skip_blank = True
            continue
        if skip_blank and not ln.strip():
            skip_blank = False
            continue
        out.append(ln)
    return out


def _diff(before: list, after: list) -> str:
    return ''.join(difflib.unified_diff(before, after, '原文', '导出后', n=2))


def _submit(session, index, **changes):
    fields = session.fields(index)
    fields.update(changes)
    return session.apply(index, fields, cards=session.cards(index),
                         advanced_open=True,
                         stage_loaded=fields.get('Stage'))


def _exported(session, path, src):
    res = session.export(path)
    assert res.get('ok'), res
    return _body(_read(path))


def _idx_of(session, text):
    return next(i for i, b in enumerate(session.beats)
                if (b.text or '').strip() == text)


def _patcher_roundtrip(tmp_path, src: str):
    """不经门禁的补丁往返（`menu "标题":` 那种写法的保真检查要用）"""
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.patcher import patch_export

    game = tmp_path / 'proj' / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(path)))
    return beats, path, patch_export, render_beats_to_rpy


# ----------------------------------------------------------------------
# P0-9 · 菜单标题（块内 `"标题"` 那一行）
# ----------------------------------------------------------------------
SRC_MENU_CAPTION = (
    'label start:\n'
    '    e "进门"\n'
    '    menu:\n'
    '        "是时候和青一起学习了."\n'
    '        "开始":\n'
    '            e "好"\n'
    '    return\n'
)


class TestMenuCaption:
    def test_caption_line_is_inside_the_menu_beat(self, tmp_path):
        """判据：标题行必须落在菜单拍的行区间里（否则补丁贴不回去）。"""
        session, _p = _project(tmp_path, SRC_MENU_CAPTION)
        menu = next(b for b in session.beats if b.kind == 'menu')
        cap_no = SRC_MENU_CAPTION.splitlines().index(
            '        "是时候和青一起学习了."') + 1
        assert menu.src_first <= cap_no <= menu.src_last, (
            f'菜单拍的行区间 {menu.src_first}..{menu.src_last} '
            f'没盖住标题行 {cap_no}')

    def test_editing_the_caption_changes_only_that_line(self, tmp_path):
        session, path = _project(tmp_path, SRC_MENU_CAPTION)
        _submit(session, _idx_of(session, '是时候和青一起学习了.'),
                Text='该学习了.')
        after = _exported(session, path, SRC_MENU_CAPTION)
        want = ['label start:', '    e "进门"', '    menu:',
                '        "该学习了."', '        "开始":',
                '            e "好"', '    return']
        assert after == want, _diff(want, after)

    def test_zero_change_roundtrip_is_byte_identical(self, tmp_path):
        """没改任何东西 → 原地与新路径都必须逐字节相同。"""
        session, path = _project(tmp_path, SRC_MENU_CAPTION)
        res = session.export(path)
        assert res.get('ok'), res
        assert _read(path) == SRC_MENU_CAPTION

        out = path.with_name('copy.rpy')
        session2, path2 = _project(tmp_path / 'new', SRC_MENU_CAPTION)
        res2 = session2.export(out)
        assert res2.get('ok'), res2
        assert _read(out) == SRC_MENU_CAPTION

    def test_editing_a_body_say_changes_only_that_line(self, tmp_path):
        session, path = _project(tmp_path, SRC_MENU_CAPTION)
        _submit(session, _idx_of(session, '好'), Text='好吧')
        after = _exported(session, path, SRC_MENU_CAPTION)
        want = ['label start:', '    e "进门"', '    menu:',
                '        "是时候和青一起学习了."', '        "开始":',
                '            e "好吧"', '    return']
        assert after == want, _diff(want, after)


class TestMultiLineMenuCaption:
    """
    标题可以**多行**（Ren'Py 里 `menu:` 块的每个裸字符串行都是标题的一部分）。

    行数不变时普通配对就够；行数变了（2 行 → 1 行 / 1 行 → 2 行）就要
    "按用户写的那几行整段换"（`_replace_matched` 的菜单标题分支），
    否则多出来的旧标题行没有对手、留在原地。
    """

    SRC2 = ('label start:\n'
            '    menu:\n'
            '        "第一行标题"\n'
            '        "第二行标题"\n'
            '        "开始":\n'
            '            e "好"\n'
            '    return\n')

    SRC1 = ('label start:\n'
            '    menu:\n'
            '        "单行标题"\n'
            '        "开始":\n'
            '            e "好"\n'
            '    return\n')

    def test_two_lines_become_one(self, tmp_path):
        session, path = _project(tmp_path, self.SRC2)
        _submit(session, next(i for i, b in enumerate(session.beats)
                              if b.kind == 'menu'), Text='改过的标题')
        after = _exported(session, path, self.SRC2)
        want = ['label start:', '    menu:', '        "改过的标题"',
                '        "开始":', '            e "好"', '    return']
        assert after == want, _diff(want, after)

    def test_one_line_becomes_two(self, tmp_path):
        session, path = _project(tmp_path, self.SRC1)
        _submit(session, next(i for i, b in enumerate(session.beats)
                              if b.kind == 'menu'), Text='第一行\n第二行')
        after = _exported(session, path, self.SRC1)
        want = ['label start:', '    menu:', '        "第一行"',
                '        "第二行"', '        "开始":',
                '            e "好"', '    return']
        assert after == want, _diff(want, after)

    def test_zero_change_stays_byte_identical(self, tmp_path):
        session, path = _project(tmp_path, self.SRC2)
        assert session.export(path).get('ok')
        assert _read(path) == self.SRC2


# ----------------------------------------------------------------------
# P0-9b · `menu "标题":`（标题写在头行）的保真
#
# ⚠ 这种写法**引擎其实不收**（Ren'Py 的 `menu` 头只接受
# `menu [名] [(参数)]:`；`engine_lint` 实测 `expected ':' not found`）。
# 但解析器认它、项目里可能存在（别的工具导出来的），所以编排层必须
# **原样还回去** —— 零改动逐字节相同、改标题只动那一行；
# 能不能写盘由引擎闸门决定（那道闸门会说真话，见
# `test_export_is_refused_by_the_engine_gate`）。
# ----------------------------------------------------------------------
SRC_MENU_HEAD_CAPTION = (
    'label start:\n'
    '    e "进门"\n'
    '    menu "是时候和青一起学习了.":  # 结尾注释\n'
    '        "开始":\n'
    '            e "好"\n'
    '    return\n'
)


class TestMenuCaptionOnHeadLine:
    def test_zero_change_patch_is_byte_identical(self, tmp_path):
        beats, path, patch_export, render = _patcher_roundtrip(
            tmp_path, SRC_MENU_HEAD_CAPTION)
        res = patch_export(beats, path, render(beats), trust_ranges=True)
        assert res.ok, res.reasons
        assert res.text == SRC_MENU_HEAD_CAPTION, repr(res.text)
        assert res.changed_lines == 0

    def test_editing_the_head_caption_changes_only_that_line(self, tmp_path):
        beats, path, patch_export, render = _patcher_roundtrip(
            tmp_path, SRC_MENU_HEAD_CAPTION)
        menu = next(b for b in beats if b.kind == 'menu')
        menu.text = '该学习了.'
        res = patch_export(beats, path, render(beats), trust_ranges=True)
        assert res.ok, res.reasons
        want = SRC_MENU_HEAD_CAPTION.replace('是时候和青一起学习了.', '该学习了.')
        assert res.text == want, repr(res.text)
        assert res.changed_lines == 1

    def test_export_is_refused_by_the_engine_gate(self, tmp_path, engine_sdk):
        """改完写盘时被引擎闸门拦下（这种写法引擎读不了）—— 说真话，不静默写坏。"""
        session, path = _project(tmp_path, SRC_MENU_HEAD_CAPTION)
        _submit(session, next(i for i, b in enumerate(session.beats)
                              if b.kind == 'menu'), Text='该学习了.')
        before = _read(path)
        res = session.export(path)
        assert not res.get('ok'), res
        told = ' '.join(str(x) for x in res.get('blocking') or [])
        assert '引擎读不了' in told, told
        assert _read(path) == before, '拒绝时文件一个字节都不许动'


# ----------------------------------------------------------------------
# P0-10 · 删掉一条分支的最后一拍：**结构行一个字节不动**
# ----------------------------------------------------------------------
SRC_CHAIN4 = (
    'label start:\n'
    '    e "一"\n'
    '    if a == 1:\n'
    '        q "A"\n'
    '    elif b == 1:\n'
    '        q "B"\n'
    '    elif c == 1:\n'
    '        q "C"\n'
    '    else:\n'
    '        q "D"\n'
    '    return\n'
)


class TestDeleteBranchKeepsStructure:
    def test_middle_elif_only_loses_its_own_lines(self, tmp_path):
        session, path = _project(tmp_path, SRC_CHAIN4)
        assert session.delete_beat(_idx_of(session, 'B')).get('ok')
        after = _exported(session, path, SRC_CHAIN4)
        want = ['label start:', '    e "一"', '    if a == 1:',
                '        q "A"', '    elif c == 1:', '        q "C"',
                '    else:', '        q "D"', '    return']
        assert after == want, _diff(want, after)

    def test_chain_head_promotes_the_next_elif_by_one_word(self, tmp_path):
        """删掉链头 `if` → 紧跟的 `elif b == 1:` 变成 `if b == 1:`（只换词）。"""
        session, path = _project(tmp_path, SRC_CHAIN4)
        assert session.delete_beat(_idx_of(session, 'A')).get('ok')
        after = _exported(session, path, SRC_CHAIN4)
        want = ['label start:', '    e "一"', '    if b == 1:',
                '        q "B"', '    elif c == 1:', '        q "C"',
                '    else:', '        q "D"', '    return']
        assert after == want, _diff(want, after)

    def test_else_branch_only_loses_itself(self, tmp_path):
        session, path = _project(tmp_path, SRC_CHAIN4)
        assert session.delete_beat(_idx_of(session, 'D')).get('ok')
        after = _exported(session, path, SRC_CHAIN4)
        want = ['label start:', '    e "一"', '    if a == 1:',
                '        q "A"', '    elif b == 1:', '        q "B"',
                '    elif c == 1:', '        q "C"', '    return']
        assert after == want, _diff(want, after)

    def test_refuses_when_only_else_would_remain(self, tmp_path):
        """删掉链头后只剩 `else:`（要整块降级缩进）→ 拒绝，文件不动。"""
        src = ('label start:\n'
               '    if a:\n'
               '        q "A"\n'
               '    else:\n'
               '        q "D"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        assert session.delete_beat(_idx_of(session, 'A')).get('ok')
        before = _read(path)
        res = session.export(path)
        assert not res.get('ok'), res
        told = ' '.join(str(x) for x in res.get('blocking') or [])
        assert '一行不剩' in told, told
        assert _read(path) == before, '拒绝时文件一个字节都不许动'

    def test_refuses_when_a_later_branch_starts_with_nested_if(self, tmp_path):
        """后面那支第一条不是拍（是嵌套 `if` 头）→ 条件重编号算不出来 → 拒绝。"""
        src = ('label start:\n'
               '    if a:\n'
               '        q "A"\n'
               '    elif b:\n'
               '        q "B"\n'
               '    elif c:\n'
               '        if d:\n'
               '            q "C"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        assert session.delete_beat(_idx_of(session, 'B')).get('ok')
        before = _read(path)
        res = session.export(path)
        assert not res.get('ok'), res
        assert _read(path) == before

    @pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
    def test_real_project_pomo_keeps_its_elif_chain(self, tmp_path):
        """真机 `script_pomo.rpy`：删那句台词 → 只少那两行，`elif` 逐字节不动。"""
        src_file = REAL / 'game' / 'pomodorro' / 'script_pomo.rpy'
        if not src_file.exists():
            pytest.skip('真机 pomo 剧本不在本机')
        text = _read(src_file)
        session, path = _project(tmp_path, text)
        idx = _idx_of(session, '您可能需要休息了.')
        assert session.delete_beat(idx).get('ok')
        after = _exported(session, path, text)
        before = _body(text)
        removed = [ln for ln in before if ln not in after]
        assert removed == ['    elif breakline == 2: ',
                           '        q "您可能需要休息了."'], removed
        assert len(before) - len(after) == 2, _diff(before, after)
        # 相邻分支的头一个字都不许动
        assert '    elif breakline == 3: ' in after
        assert '    if (breakline == 3)' not in '\n'.join(after), (
            '把作者的 elif 展平成了 if —— ' + _diff(before, after))


# ----------------------------------------------------------------------
# 顺带修掉的一处静默 no-op（审计自带的 `tools/p0_scan_struct.py` 里
# "改 show 的位置"那条用例暴露的；那条用例本身打偏了 —— 它改的是
# return 那拍 —— 但顺着查下去发现真问题）：
#
# `fields()` 会算 `Position` / `Expression` 两格，而 `session.apply` 从前
# **根本不读它们**（只活在旧的 `fields_to_beat` 里）→ 任何"拿 fields()、
# 改这两格、再 apply"的调用方都是**改了等于没改、还 ok=True**。
# 修法：`cards is None` 时按字段写回（有立绘卡时卡片才是真源）。
# ----------------------------------------------------------------------
class TestDerivedFieldsWriteBack:
    SRC = ('label start:\n'
           '    scene bg room\n'
           '    show e happy at left\n'
           '    e "一"\n'
           '    return\n')

    def test_position_is_written_back(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        fields = session.fields(0)
        assert fields.get('Position') == 'left', fields.get('Position')
        fields['Position'] = 'right'
        session.apply(0, fields, cards=None, advanced_open=True,
                      stage_loaded=fields.get('Stage'))
        after = _exported(session, path, self.SRC)
        want = ['label start:', '    scene bg room',
                '    show e happy at right', '    e "一"', '    return']
        assert after == want, _diff(want, after)

    def test_expression_is_written_back(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        fields = session.fields(0)
        fields['Expression'] = 'sad'
        session.apply(0, fields, cards=None, advanced_open=True,
                      stage_loaded=fields.get('Stage'))
        after = _exported(session, path, self.SRC)
        want = ['label start:', '    scene bg room',
                '    show e sad at left', '    e "一"', '    return']
        assert after == want, _diff(want, after)

    def test_empty_or_unchanged_values_do_not_clear_anything(self, tmp_path):
        """空串 = "没有统一值"（多张立绘位置不一致），不许拿它去抹位置。"""
        session, path = _project(tmp_path, self.SRC)
        fields = session.fields(0)
        fields['Position'] = ''
        fields['Expression'] = ''
        session.apply(0, fields, cards=None, advanced_open=True,
                      stage_loaded=fields.get('Stage'))
        out = session.export(path)
        assert out.get('ok'), out
        assert _read(path) == self.SRC, '空值把位置/表情抹掉了'


# ----------------------------------------------------------------------
# P0-11 · 移动拍不许越过 `return`
# ----------------------------------------------------------------------
SRC_RETURN = ('label start:\n'
              '    e "一"\n'
              '    e "二"\n'
              '    e "三"\n'
              '    return\n')


class TestMovePastReturn:
    def test_move_after_return_is_refused(self, tmp_path):
        session, path = _project(tmp_path, SRC_RETURN)
        res = session.move_beat(0, 3)
        assert not res.get('ok'), res
        assert 'return' in (res.get('error') or ''), res
        # 内存顺序不许变（拒绝 = 什么都没发生）
        assert [b.text for b in session.beats[:3]] == ['一', '二', '三']
        assert session._chapter_doc().tree.validate() == []
        out = session.export(path)
        assert out.get('ok'), out
        assert _read(path) == SRC_RETURN, '拒绝移动后文件不许动'

    def test_moving_the_return_itself_is_refused(self, tmp_path):
        session, _path = _project(tmp_path, SRC_RETURN)
        res = session.move_beat(3, 0)
        assert not res.get('ok'), res
        assert 'return' in (res.get('error') or ''), res
        assert [b.text for b in session.beats[:3]] == ['一', '二', '三']

    def test_moves_that_stay_before_the_return_still_work(self, tmp_path):
        session, path = _project(tmp_path, SRC_RETURN)
        res = session.move_beat(2, 1)
        assert res.get('ok'), res
        after = _exported(session, path, SRC_RETURN)
        want = ['label start:', '    e "一"', '    e "三"',
                '    e "二"', '    return']
        assert after == want, _diff(want, after)

    def test_a_branch_return_stays_last_in_that_branch(self, tmp_path):
        """分支体里的 `return` 是**那一段**的最后一条 —— 插在它前面照旧可以。"""
        src = ('label start:\n'
               '    if a:\n'
               '        e "A1"\n'
               '        return\n'
               '    e "尾"\n')
        session, path = _project(tmp_path, src)
        # 把分支体里第一拍挪到 return 之后（同一条分支内）→ 拒绝
        res = session.move_beat(0, 1)
        assert not res.get('ok'), res
        out = session.export(path)
        assert out.get('ok'), out
        assert _read(path) == src
