# -*- coding: utf-8 -*-
"""
判据 · **第四轮**（2026-10-25 上午）
==================================

对应 `docs/审计_P0第四轮_2026-10-25.md`。写法与前两轮同一套：
`EditorSession.load → 操作 → export(原地)`，断言**产物逐行该长什么样**；
凡是结构行（`if / elif / else / menu:`）被波及，一律先红。

本轮覆盖：

  · P0  含自定义语句的拍，改台词/说话人/删拍都要落盘且**只动该动的那一行**
  · P1-1 无标题菜单**加标题**（以及清空/多行/带名字），标题从无到有
  · P1-2 移动拍：普通拍能挪；**跨过 `if`/`elif` 块头**的那一刀要说明白为什么拒
  · P1-3 改 `if` 条件：链头能增量改（只换那一行），`elif` 那一支给具体理由
  · P2-1 插空卡不产生行 —— 回执必须说出来
  · P2-2 `OUTSIDE_BUILD_DIR` 是提示，不许混进 `blocking` 挤掉真原因
  · P2-3 自定义语句的参数写进「代码」格 → 写回头行（不是 `$ mycue …`）
"""
from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

import pytest

from storygraph.app.editor import EditorSession

MARKER = '# 由剧本表自动生成'
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
POMO = REAL / 'game' / 'pomodorro' / 'script_pomo.rpy'

RULES_MYCUE = {'statements': {'mycue': {
    'line': 'mycue intensity int =0 layer str =camera'}}}


# ----------------------------------------------------------------------
# 工具（与 `test_p0_round2` / `round3` 同源）
# ----------------------------------------------------------------------
def _project(tmp_path, src: str, rules=None):
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    if rules is not None:
        (tmp_path / 'storygraph.rules.json').write_text(
            json.dumps(rules, ensure_ascii=False), encoding='utf-8')
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
    return _body(_read(path)), res


def _idx_of(session, text):
    return next(i for i, b in enumerate(session.beats)
                if (b.text or '').strip() == text)


def _patched_text(session, path):
    """不经门禁的补丁产物（看"只动哪一行"最准）。"""
    from storygraph.story.patcher import patch_export
    res = session.compile()
    return patch_export(session.beats, path, res.rpy_text,
                        registry=session.registry(), symbols=session.symbols,
                        trust_ranges=True)


# ======================================================================
#  P0 · 含自定义语句的拍（审计的判据原样搬进来）
# ======================================================================
class TestCustomStatementBeat:
    """
    审计第四轮 §三：`mycue …` 与台词**合成一拍**时，改台词必须落盘。

    判据（审计给的）：

        mycue intensity=3 layer=camera   ← 原样
        e "改过的"                        ← 只改这一行
    """

    SRC = ('label start:\n'
           '    mycue intensity=3 layer=camera\n'
           '    e "台词"\n'
           '    return\n')

    # ⚠ **没声明的自定义语句过不了引擎闸门**（临时工程里没有
    # `renpy.register_statement`，SDK lint 报 `expected statement`）——
    # 那不是本轮要修的东西（工具拦得对），所以"没声明"的那几条走
    # **补丁产物**看"只动哪一行"；"声明过"的走真正的 `export`。

    def test_undeclared_statement_text_edit_changes_only_the_say_line(
            self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        assert len(session.beats) == 2          # 语句 + 台词 = **一拍**
        _submit(session, 0, Text='改过的')
        patched = _patched_text(session, path)
        assert patched.ok, patched.reasons
        assert _body(patched.text) == ['label start:',
                                       '    mycue intensity=3 layer=camera',
                                       '    e "改过的"',
                                       '    return'], patched.text

    def test_declared_statement_text_edit_changes_only_the_say_line(
            self, tmp_path):
        session, path = _project(tmp_path, self.SRC, rules=RULES_MYCUE)
        _submit(session, 0, Text='改过的')
        after, _res = _exported(session, path, self.SRC)
        assert after == ['label start:',
                         '    mycue intensity=3 layer=camera',
                         '    e "改过的"',
                         '    return'], _diff(_body(self.SRC), after)

    def test_speaker_edit_lands(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        _submit(session, 0, Speaker='m')
        patched = _patched_text(session, path)
        assert patched.ok, patched.reasons
        rows = _body(patched.text)
        assert rows[2] == '    m "台词"', rows
        assert rows[1] == '    mycue intensity=3 layer=camera'

    def test_deleting_the_beat_takes_the_statement_with_it(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        assert session.delete_beat(0).get('ok')
        after, _res = _exported(session, path, self.SRC)
        assert after == ['label start:', '    return'], \
            _diff(_body(self.SRC), after)

    def test_zero_change_is_byte_identical(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        patched = _patched_text(session, path)
        assert patched.ok, patched.reasons
        assert patched.text == self.SRC, '什么都没改，一个字节都不许动'


# ======================================================================
#  P1-1 · 无标题菜单加标题（从无到有）
# ======================================================================
class TestMenuCaptionFromScratch:
    SRC = ('label start:\n'
           '    menu:\n'
           '        "开始":\n'
           '            e "a"\n'
           '        "结束":\n'
           '            e "b"\n'
           '    return\n')

    def test_add_caption_lands_inside_the_block(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        _submit(session, 0, Text='新标题')
        after, res = _exported(session, path, self.SRC)
        assert after[:3] == ['label start:', '    menu:', '        "新标题"'], \
            _diff(_body(self.SRC), after)
        # 选项与选项体一个字节不动
        assert '        "开始":' in after and '            e "a"' in after

    def test_add_two_line_caption(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        _submit(session, 0, Text='第一行\n第二行')
        after, _res = _exported(session, path, self.SRC)
        assert after[2:4] == ['        "第一行"', '        "第二行"'], \
            _diff(_body(self.SRC), after)

    def test_caption_uses_the_file_indent_style(self, tmp_path):
        """文件用 2 空格缩进时，新标题也要跟着用 2 空格（不许写死 4）。"""
        src = ('label start:\n'
               '  menu:\n'
               '    "开始":\n'
               '      e "a"\n'
               '  return\n')
        session, path = _project(tmp_path, src)
        _submit(session, 0, Text='新标题')
        after, _res = _exported(session, path, src)
        assert '    "新标题"' in after, _diff(_body(src), after)

    def test_clear_caption(self, tmp_path):
        src = ('label start:\n'
               '    menu:\n'
               '        "旧标题"\n'
               '        "开始":\n'
               '            e "a"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit(session, 0, Text='')
        after, _res = _exported(session, path, src)
        assert '        "旧标题"' not in after, _diff(_body(src), after)
        assert after[1] == '    menu:'

    def test_whitespace_only_caption_is_not_deleted(self, tmp_path):
        """
        作者的标题写成 `" "`（一个空格）：`menu_caption_text` 会 strip 成空 →
        渲染结果里没有这一行。它**不是用户删掉的标题**，一个字节都不许动。
        """
        src = ('label start:\n'
               '    menu:\n'
               '        " "\n'
               '        "开始":\n'
               '            e "a"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        res = session.export(path)
        assert res.get('ok'), res
        assert _read(path) == src, '空白标题被静默删掉了'


# ======================================================================
#  P1-3 · 改 if 条件
# ======================================================================
class TestConditionEdit:
    SRC = ('label start:\n'
           '    $ flag = True\n'
           '    if flag:\n'
           '        e "在里面"\n'
           '    return\n')

    CHAIN = ('label start:\n'
             '    if a == 1:\n'
             '        e "甲"\n'
             '    elif a == 2:\n'
             '        e "乙"\n'
             '    else:\n'
             '        e "丙"\n'
             '    return\n')

    def test_single_if_condition_lands(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        _submit(session, 1, condition='not flag')
        after, _res = _exported(session, path, self.SRC)
        assert after == ['label start:', '    $ flag = True',
                         '    if not flag:', '        e "在里面"',
                         '    return'], _diff(_body(self.SRC), after)

    def test_chain_head_condition_lands_and_keeps_elif_lines(self, tmp_path):
        session, path = _project(tmp_path, self.CHAIN)
        session.apply(0, {'condition': 'a == 9'})
        after, _res = _exported(session, path, self.CHAIN)
        assert after == ['label start:', '    if a == 9:', '        e "甲"',
                         '    elif a == 2:', '        e "乙"',
                         '    else:', '        e "丙"', '    return'], \
            _diff(_body(self.CHAIN), after)

    def test_chain_siblings_are_renumbered_in_memory(self, tmp_path):
        """
        链头一改，后面几支的**否定项**要跟着换 —— 否则"增量"与"全量"
        两条路会写出两种剧情（同一章导两次结果不同）。
        """
        session, path = _project(tmp_path, self.CHAIN)
        session.apply(0, {'condition': 'a == 9'})
        conds = [(b.condition or '').strip() for b in session.beats]
        assert conds[0] == 'a == 9'
        assert 'a == 9' in conds[1] and 'a == 1' not in conds[1], conds
        # 全量渲染出的条件必须与补丁后的文件**同一个语义**
        full = session.compile().rpy_text
        assert 'not ((a == 9))' in full, full

    def test_elif_condition_refuses_with_a_specific_reason(self, tmp_path):
        session, path = _project(tmp_path, self.CHAIN)
        session.apply(1, {'condition': 'a == 9'})
        before = _read(path)
        res = session.export(path)
        assert not res.get('ok'), res
        told = ' '.join(str(x) for x in res.get('blocking') or [])
        assert 'elif' in told and '全量' in told, told
        assert _read(path) == before, '拒绝时文件一个字节都不许动'

    def test_clearing_a_condition_says_it_is_a_structure_change(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        session.apply(1, {'condition': ''})
        before = _read(path)
        res = session.export(path)
        assert not res.get('ok'), res
        told = ' '.join(str(x) for x in res.get('blocking') or [])
        assert '清空' in told and '全量' in told, told
        assert _read(path) == before

    def test_nested_if_condition_refuses(self, tmp_path):
        """`if` 里还有 `if`：累积条件含外层，光改一行写不出用户填的那句话。"""
        src = ('label start:\n'
               '    if a:\n'
               '        if b:\n'
               '            e "里面"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        idx = _idx_of(session, '里面')
        session.apply(idx, {'condition': 'c'})
        before = _read(path)
        res = session.export(path)
        assert not res.get('ok'), res
        told = ' '.join(str(x) for x in res.get('blocking') or [])
        assert '嵌' in told or '全量' in told, told
        assert _read(path) == before


# ======================================================================
#  P1-2 · 移动拍
# ======================================================================
@pytest.mark.skipif(not POMO.exists(), reason='真机项目不在本机（换机）')
class TestMoveBeat:
    def _load(self, tmp_path):
        game = tmp_path / 'game'
        game.mkdir(parents=True, exist_ok=True)
        dst = game / POMO.name
        shutil.copyfile(POMO, dst)
        session = EditorSession(project_root=tmp_path)
        session.load(dst)
        return session, dst, _read(dst)

    def test_plain_move_lands(self, tmp_path):
        """两句普通台词互换位置 —— 这是移动最普通的一刀，必须能做。"""
        session, dst, before = self._load(tmp_path)
        assert session.move_beat(27, 28).get('ok')
        after, _res = _exported(session, dst, before)
        b1, b2 = _body(before), after
        assert b1 != b2, '移动了拍，产物却一个字节没变'
        assert len(b1) == len(b2), '只是换顺序，行数不该变'
        assert set(x.strip() for x in b1) == set(x.strip() for x in b2)

    def test_move_across_an_elif_head_explains_itself(self, tmp_path):
        """
        审计报的那一刀：把 `elif breakline == 2:` 那一支的台词挪出分支。
        那是**改剧情**（条件不再生效），必须拒绝 —— 但理由要说人话。
        """
        session, dst, before = self._load(tmp_path)
        assert session.move_beat(25, 28).get('ok')
        res = session.export(dst)
        assert not res.get('ok'), res
        told = ' '.join(str(x) for x in res.get('blocking') or [])
        assert 'elif' in told and ('条件' in told or '剧情' in told), told
        assert _read(dst) == before, '拒绝时文件一个字节都不许动'

    def test_move_with_conditions_says_order_is_semantics(self, tmp_path):
        session, dst, before = self._load(tmp_path)
        assert session.move_beat(26, 27).get('ok')
        res = session.export(dst)
        assert not res.get('ok'), res
        told = ' '.join(str(x) for x in res.get('blocking') or [])
        assert '位置' in told or '判定顺序' in told, told


# ======================================================================
#  P2-1 · 插空卡
# ======================================================================
class TestEmptyCard:
    SRC = 'label start:\n    e "一句"\n    return\n'

    def test_empty_card_is_reported_not_silently_ignored(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        assert session.insert_beat(0).get('ok')
        res = session.export(path)
        assert res.get('ok'), res
        assert _read(path) == self.SRC, '空卡本来就不产生行'
        note = str(res.get('note') or '')
        assert '空卡' in note, note

    def test_filled_card_lands(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        idx = session.insert_beat(0)['index']
        session.apply(idx, {'Speaker': 'e', 'Text': '新的一句'})
        after, _res = _exported(session, path, self.SRC)
        assert '    e "新的一句"' in after, _diff(_body(self.SRC), after)


# ======================================================================
#  P2-2 · 提示 vs 拦截
# ======================================================================
class TestWarningsAreNotBlocking:
    def test_outside_build_dir_is_a_warning_not_a_blocker(self, tmp_path,
                                                          engine_sdk):
        """
        产物写在 game/ 里只是**提示**（要挪位置），不是这次拒写的原因。
        真原因（引擎读不了产物）必须单独看得见。
        """
        src = ('label start:\n'
               '    mycue 1\n'
               '    e "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit(session, 0, Text='改过的')      # 有改动才会走到"写盘 + lint"
        res = session.export(path)
        assert not res.get('ok'), res
        codes = [str(c) for c in (res.get('codes') or [])]
        assert 'ENGINE_PARSE_ERROR' in codes, res
        assert 'OUTSIDE_BUILD_DIR' not in codes, codes
        blocking = ' '.join(str(x) for x in res.get('blocking') or [])
        assert '引擎读不了' in blocking, blocking
        warns = ' '.join(str(x) for x in res.get('warnings') or [])
        assert 'game/' in warns, res


# ======================================================================
#  P2-3 · 自定义语句的参数写进「代码」格
# ======================================================================
class TestStatementHeadViaCodeCell:
    SRC = ('label start:\n'
           '    mycue intensity=3 layer=camera\n'
           '    e "台词"\n'
           '    return\n')

    def test_declared_statement_head_is_rewritten_not_prefixed_with_dollar(
            self, tmp_path):
        session, path = _project(tmp_path, self.SRC, rules=RULES_MYCUE)
        _submit(session, 0, CodeBlock='mycue intensity=5 layer=camera')
        patched = _patched_text(session, path)
        assert patched.ok, patched.reasons
        rows = _body(patched.text)
        assert rows[1] == '    mycue intensity=5 layer=camera', rows
        assert not any(r.strip().startswith('$ mycue') for r in rows), rows

    def test_python_error_mentions_the_param_column(self, tmp_path):
        """
        同一条语句写进了**另一拍**的代码格（那一拍没有这条语句）→
        只能报 python 语法错；但必须补一句"这是自定义语句，参数请填参数格"。
        """
        src = ('label start:\n'
               '    mycue intensity=3 layer=camera\n'
               '    e "台词"\n'
               '    return\n')
        rules = {'statements': {'mycue': {'line': 'mycue intensity int =0'}}}
        game = tmp_path / 'game'
        game.mkdir(parents=True)
        path = game / 'script.rpy'
        path.write_bytes(src.encode('utf-8'))
        (tmp_path / 'storygraph.rules.json').write_text(
            json.dumps(rules, ensure_ascii=False), encoding='utf-8')
        session = EditorSession(project_root=tmp_path)
        session.load(path)
        # 给**台词那一拍**写一条 `mycue …`（它自己没有这条语句）
        _submit(session, 0, Text='台词', Speaker='e')
        session.beats[0].code = 'mycue intensity=9'
        from storygraph.story.generation_guard import evaluate_generation
        issues = evaluate_generation(session.compile().rpy_text, path,
                                     project_root=tmp_path,
                                     beats=session.beats)
        msgs = ' '.join(i.message for i in issues
                        if i.code == 'PYTHON_SYNTAX_ERROR')
        assert '参数格' in msgs, msgs
