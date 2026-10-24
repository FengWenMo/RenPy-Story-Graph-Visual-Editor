# -*- coding: utf-8 -*-
"""
判据 · **第二轮 P0**（2026-10-24 晚）
====================================

对应 `docs/审计_P0第二轮_2026-10-24.md` 的五条。全部端到端：
`EditorSession.load → 操作 → export(原地)`，断言和**源文件字节**比。

  · P0-4 `menu` 吃掉前面的台词与选项行（解析层）
  · P0-5 改控制流目标 → 多一条 call/jump（剧情跑两遍）
  · P0-6 同一件事走 API → 完全不生效（raw 被 CodeBlock 降级）
  · P0-7 移动一拍 → 顺序没变
  · P0-8 SayTail / MenuName / Label 改了不落盘

判据的最高一条（审计 §九）：
**`export(ok=True)` 之后，产物必须与"内存按渲染规则生成的文本"一致。**
所以这里基本不写"某个字段变了"，而是写**产物逐行该长什么样**。
"""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from storygraph.app.editor import EditorSession

MARKER = '# 由剧本表自动生成'


# ----------------------------------------------------------------------
# 工具
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
    """去掉生成器标记（+它后面那个空行）之后的正文行。"""
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
    """按"打开卡片 → 改几格 → 提交"的方式改一拍（前端走的就是这条路）。"""
    fields = session.fields(index)
    fields.update(changes)
    return session.apply(index, fields, cards=session.cards(index),
                         advanced_open=True,
                         stage_loaded=fields.get('Stage'))


def _export(session, path):
    res = session.export(path)
    assert res.get('ok'), res
    return res


def _exported(session, path, src):
    _export(session, path)
    return _body(_read(path))


# ----------------------------------------------------------------------
# P0-4 · menu 前面的台词与选项行（解析层丢行）
# ----------------------------------------------------------------------
SRC_MENU_WITH_SAY = (
    'label start:\n'
    '    e "台词"\n'
    '    menu:\n'
    '        "选项一":\n'
    '            e "1"\n'
    '    return\n'
)


class TestMenuKeepsNeighbours:
    def test_parse_keeps_every_line(self, tmp_path):
        """台词 / 选项行 / 选项体都在 —— 一拍都不许吞。"""
        session, _path = _project(tmp_path, SRC_MENU_WITH_SAY)
        seen = [(b.kind, (b.text or '').strip()) for b in session.beats]
        assert ('say', '台词') in seen
        assert ('menu_option', '选项一') in seen
        assert ('say', '1') in seen
        assert len(session.beats) == 5

    def test_full_render_matches_source(self, tmp_path):
        """全量渲染（导出到新路径走的就是它）与源文件逐行相同。"""
        session, _path = _project(tmp_path, SRC_MENU_WITH_SAY)
        assert _body(session.compile().rpy_text) == _body(SRC_MENU_WITH_SAY)

    def test_export_to_new_path_is_byte_identical(self, tmp_path):
        """没改动 → 导出到**新路径**也必须逐字节相同（不是零 diff）。"""
        session, path = _project(tmp_path, SRC_MENU_WITH_SAY)
        out = path.with_name('script_copy.rpy')
        _export(session, out)
        assert _read(out) == SRC_MENU_WITH_SAY

    def test_edit_after_menu_only_touches_that_line(self, tmp_path):
        """改选项体里那句台词，菜单与前面的台词一个字节都不许动。"""
        session, path = _project(tmp_path, SRC_MENU_WITH_SAY)
        idx = next(i for i, b in enumerate(session.beats)
                   if (b.text or '').strip() == '1')
        _submit(session, idx, Text='改过了')
        after = _exported(session, path, SRC_MENU_WITH_SAY)
        want = ['label start:', '    e "台词"', '    menu:',
                '        "选项一":', '            e "改过了"', '    return']
        assert after == want, _diff(want, after)


# ----------------------------------------------------------------------
# P0-5 / P0-6 · 控制流写回
# ----------------------------------------------------------------------
SRC_CALL = ('label start:\n'
            '    call side from _call_side\n'
            '    e "回来了"\n'
            '    return\n')

SRC_JUMP = ('label start:\n'
            '    jump other\n'
            '\n'
            'label other:\n'
            '    e "在这"\n'
            '    return\n')


class TestFlowWriteBack:
    def test_api_path_lands(self, tmp_path):
        """P0-6：直接 `fields()` + `apply()`（脚本/表格/AI 走的这条）。"""
        session, path = _project(tmp_path, SRC_CALL)
        _submit(session, 0, FlowTarget='other_side')
        after = _exported(session, path, SRC_CALL)
        want = ['label start:', '    call other_side from _call_side',
                '    e "回来了"', '    return']
        assert after == want, _diff(want, after)

    def test_api_path_updates_beat(self, tmp_path):
        """内存也得对：`parse_flow(beat)['target']` 必须是新目标。"""
        from storygraph.story.flow import parse_flow
        session, _path = _project(tmp_path, SRC_CALL)
        _submit(session, 0, FlowTarget='other_side')
        assert parse_flow(session.beats[0])['target'] == 'other_side'
        assert str(session.beats[0].code or '').strip() == ''

    def test_ui_style_never_duplicates_the_line(self, tmp_path):
        """P0-5：代码格与目标一起提交（前端会同步）→ 只许有一行。"""
        session, path = _project(tmp_path, SRC_CALL)
        fields = session.fields(0)
        fields['FlowTarget'] = 'other_side'
        fields['CodeBlock'] = 'call other_side from _call_side'
        session.apply(0, fields, cards=session.cards(0), advanced_open=True,
                      stage_loaded=fields.get('Stage'))
        after = _exported(session, path, SRC_CALL)
        want = ['label start:', '    call other_side from _call_side',
                '    e "回来了"', '    return']
        assert after == want, _diff(want, after)

    def test_call_to_jump_drops_from_clause(self, tmp_path):
        """call → jump：`from` 子句按 Ren'Py 语义必须清掉。"""
        session, path = _project(tmp_path, SRC_CALL)
        _submit(session, 0, FlowType='jump', FlowTarget='other')
        after = _exported(session, path, SRC_CALL)
        want = ['label start:', '    jump other', '    e "回来了"',
                '    return']
        assert after == want, _diff(want, after)

    def test_jump_target_change(self, tmp_path):
        session, path = _project(tmp_path, SRC_JUMP)
        _submit(session, 0, FlowTarget='third')
        after = _exported(session, path, SRC_JUMP)
        want = ['label start:', '    jump third', '',
                'label other:', '    e "在这"', '    return']
        assert after == want, _diff(want, after)

    def test_unchanged_export_is_byte_identical(self, tmp_path):
        """只翻页不修改：控制流那一行一个字节都不许动。"""
        session, path = _project(tmp_path, SRC_CALL)
        _submit(session, 0)
        after = _exported(session, path, SRC_CALL)
        assert after == _body(SRC_CALL), _diff(_body(SRC_CALL), after)


# ----------------------------------------------------------------------
# P0-7 · 移动一拍
# ----------------------------------------------------------------------
SRC_PLAIN = ('label start:\n'
             '    e "一"\n'
             '    e "二"\n'
             '    e "三"\n'
             '    return\n')


class TestMoveBeat:
    def test_move_first_to_last(self, tmp_path):
        session, path = _project(tmp_path, SRC_PLAIN)
        res = session.move_beat(0, 2)
        assert res.get('ok'), res
        after = _exported(session, path, SRC_PLAIN)
        want = ['label start:', '    e "二"', '    e "三"', '    e "一"',
                '    return']
        assert after == want, _diff(want, after)

    def test_move_last_to_first(self, tmp_path):
        session, path = _project(tmp_path, SRC_PLAIN)
        res = session.move_beat(2, 0)
        assert res.get('ok'), res
        after = _exported(session, path, SRC_PLAIN)
        want = ['label start:', '    e "三"', '    e "一"', '    e "二"',
                '    return']
        assert after == want, _diff(want, after)

    def test_move_across_labels_is_refused_not_mangled(self, tmp_path):
        """把一拍挪进别的 label：宁可拒（说清原因），也不许写出坏结构。"""
        session, path = _project(tmp_path, SRC_JUMP)
        session.move_beat(0, 1)              # jump 挪到另一个 label 的块里
        before = _read(path)
        res = session.export(path)
        assert not res.get('ok')
        told = ' '.join(str(x) for x in res.get('blocking') or [])
        assert 'label' in told, res
        assert _read(path) == before          # 拒绝时文件一个字节都不许动
        assert len([ln for ln in _body(_read(path))
                    if ln.strip().startswith('label ')]) == 2


# ----------------------------------------------------------------------
# P0-8 · 高级区三个字段
# ----------------------------------------------------------------------
SRC_SAY2 = ('label start:\n'
            '    e "第一句"\n'
            '    e "第二句"\n'
            '    return\n')


class TestAdvancedFields:
    def test_say_tail(self, tmp_path):
        session, path = _project(tmp_path, SRC_SAY2)
        _submit(session, 0, SayTail='(id="l1")')
        after = _exported(session, path, SRC_SAY2)
        want = ['label start:', '    e "第一句" (id="l1")',
                '    e "第二句"', '    return']
        assert after == want, _diff(want, after)

    def test_menu_name(self, tmp_path):
        src = ('label start:\n'
               '    menu:\n'
               '        "选项一":\n'
               '            e "1"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        idx = next(i for i, b in enumerate(session.beats)
                   if b.kind == 'menu')
        _submit(session, idx, MenuName='my_menu')
        after = _exported(session, path, src)
        want = ['label start:', '    menu my_menu:', '        "选项一":',
                '            e "1"', '    return']
        assert after == want, _diff(want, after)

    def test_label_rename(self, tmp_path):
        session, path = _project(tmp_path, SRC_SAY2)
        _submit(session, 0, Label='new_name')
        after = _exported(session, path, SRC_SAY2)
        want = ['label new_name:', '    e "第一句"', '    e "第二句"',
                '    return']
        assert after == want, _diff(want, after)


# ----------------------------------------------------------------------
# P1（审计 §八）· 注释多行 / 删控制流拍被拒
# ----------------------------------------------------------------------
SRC_COMMENT = ('label start:\n'
               '    # 旧注释\n'
               '    e "台词"\n'
               '    return\n')


class TestCommentLines:
    def test_one_comment_becomes_two(self, tmp_path):
        """1 行注释写成 2 行：旧的**必须被替换**，不是又加两条。"""
        session, path = _project(tmp_path, SRC_COMMENT)
        _submit(session, 0, Comment='第一行\n第二行')
        after = _exported(session, path, SRC_COMMENT)
        want = ['label start:', '    # 第一行', '    # 第二行',
                '    e "台词"', '    return']
        assert after == want, _diff(want, after)

    def test_one_comment_becomes_one(self, tmp_path):
        session, path = _project(tmp_path, SRC_COMMENT)
        _submit(session, 0, Comment='只剩一行')
        after = _exported(session, path, SRC_COMMENT)
        want = ['label start:', '    # 只剩一行', '    e "台词"',
                '    return']
        assert after == want, _diff(want, after)


class TestDeleteFlowBeat:
    SRC = ('label start:\n'
           '    jump other\n'
           '\n'
           'label other:\n'
           '    e "在这"\n'
           '    return\n')

    def test_deleting_the_only_statement_is_refused_with_a_clear_reason(
            self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        res = session.delete_beat(0)
        assert res.get('ok'), res
        before = _read(path)
        out = session.export(path)
        assert not out.get('ok'), out
        told = ' '.join(str(x) for x in out.get('blocking') or [])
        assert '一层块会一行不剩' in told, told
        assert _read(path) == before, '拒绝时文件一个字节都不许动'


# ----------------------------------------------------------------------
# 总判据：改一处，只动一行
# ----------------------------------------------------------------------
class TestOnlyOneLineMoves:
    SRC = ('label start:\n'
           '    scene bg room\n'
           '    show e happy at left\n'
           '    play music "a.ogg"\n'
           '    e "台词"\n'
           '    return\n')

    def test_edit_one_say(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        idx = next(i for i, b in enumerate(session.beats)
                   if (b.text or '').strip() == '台词')
        _submit(session, idx, Text='改过了')
        after = _exported(session, path, self.SRC)
        want = ['label start:', '    scene bg room',
                '    show e happy at left', '    play music "a.ogg"',
                '    e "改过了"', '    return']
        assert after == want, _diff(want, after)


# ----------------------------------------------------------------------
# 行数守恒（审计 §二 修法方向第 2 条）：真机上"解析出来的台词"不许比
# 全量渲染写出去的**多**。少一句就是"解析吞行"或"渲染漏行"。
# ----------------------------------------------------------------------
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


class TestLineConservation:
    @pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
    def test_no_file_renders_fewer_say_lines_than_it_parsed(self):
        from storygraph.parsing.discover import discover_scripts
        from storygraph.parsing.renpy_parser import RenPyParser, \
            ParseResultBuilder
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.story.beat_render import render_beats_to_rpy
        from storygraph.story.patcher import _line_key, _norm

        bad = []
        for f in discover_scripts(REAL):
            pr = ParseResultBuilder().build(RenPyParser().parse(str(f)))
            beats = beats_from_parse_result(pr)
            text = render_beats_to_rpy(beats)
            src = sum(1 for s in pr.statements
                      if s.kind == 'say'
                      and str(s.payload.get('text') or '').strip())
            out = sum(1 for ln in text.splitlines()
                      if ln.strip() and _line_key(_norm(ln))[0] == 'say')
            if out < src:
                bad.append((f.name, src, out))
        assert not bad, f'这些文件渲染出去的台词比解析出来的少：{bad[:8]}'
