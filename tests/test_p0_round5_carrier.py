# -*- coding: utf-8 -*-
"""
第五轮 P0 审计的判据（2026-10-25）：**载体**与**静默丢行**。

三条铁律在这一轮的落地：

  · 铁律 #1「不归己的行逐字节不动」—— 逐行还原换行符时，**行边界只认 `\\n`**
    （`str.splitlines()` 把 U+2028 / NEL / VT / FF 也当换行 → 一行变两行，
    或把真 `\\n` 换成那个字符）。判据一律"构造期望字节"，不用同索引 diff。
  · 铁律 #5「绝不静默」—— 源文件不是 UTF-8 时，**读回来再写回去**会把坏字节
    换成 U+FFFD。`apply(..., source=)` 是唯一共同末班车，必须拒写并说清坐标。
  · 铁律 #7「不许静默丢行」—— 增量补丁删掉源文件里的行时，回执必须点名。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.session_export import removal_note  # noqa: E402
from storygraph.core.textlines import (join_lines, split_eols,  # noqa: E402
                                       split_lines)
from storygraph.story.source_style import (SourceNotUtf8, detect,  # noqa: E402
                                           read_text)

ROOT = Path(__file__).resolve().parents[1]
LS = '\u2028'          # LINE SEPARATOR：最常从网页/Word 粘进来的"软回车"


# ---------------------------------------------------------------------------
#  一、切行口径：只认 \n
# ---------------------------------------------------------------------------

class TestSplitLines:
    @pytest.mark.parametrize('ch', ['\u2028', '\u2029', '\x85', '\x0b',
                                    '\x0c', '\x1c', '\x1d', '\x1e'])
    def test_exotic_separators_are_not_line_breaks(self, ch):
        text = f'a{ch}b\nc\n'
        assert split_lines(text) == [f'a{ch}b', 'c']
        # 对照：str.splitlines() 会把它切成两行（这就是 bug 的来源）
        assert len(text.splitlines()) == 3

    def test_trailing_newline_makes_no_empty_row(self):
        assert split_lines('a\nb\n') == ['a', 'b']
        assert split_lines('a\nb') == ['a', 'b']
        assert split_lines('') == []
        assert split_lines(None) == []

    def test_split_eols_tail_without_newline_is_empty_string(self):
        rows, eols = split_eols('a\r\nb\nc')
        assert rows == ['a', 'b', 'c']
        assert eols == ['\r\n', '\n', '']

    def test_join_roundtrip_is_byte_identical(self):
        for raw in ('a\r\nb\nc\n', 'a\nb\r\nc', 'a\r\nb\r\n', 'x', ''):
            rows, eols = split_eols(raw)
            assert join_lines(rows, eols) == raw


# ---------------------------------------------------------------------------
#  二、`apply(source=)` —— 所有落盘点的共同末班车
# ---------------------------------------------------------------------------

def _write(tmp_path, raw: bytes) -> Path:
    p = tmp_path / 'ch1.rpy'
    p.write_bytes(raw)
    return p


class TestApply:
    @pytest.mark.parametrize('ch', ['\u2028', '\u2029', '\x85', '\x0b', '\x0c'])
    def test_exotic_separator_in_line_does_not_get_used_as_newline(
            self, tmp_path, ch):
        raw = f'label ch1:\n    "hi{ch}there"\n    "b"\n'.encode('utf-8')
        p = _write(tmp_path, raw)
        text, style = read_text(p)
        rows = split_lines(text)
        rows[2] = '    "B"'
        out = style.apply('\n'.join(rows) + '\n', source=p).encode('utf-8')
        assert out == raw.replace(b'"b"', b'"B"'), (
            '未碰过的那一行被改了 —— 换行符被特殊字符顶掉')

    def test_non_utf8_source_is_refused_not_corrupted(self, tmp_path):
        raw = b'label ch1:\n    "a"\n    # \xe9 bad\n'
        p = _write(tmp_path, raw)
        text, style = read_text(p)
        assert '\ufffd' in text                 # 读的时候已经有损（这是已知的）
        with pytest.raises(SourceNotUtf8) as ei:
            style.apply(text, source=p)
        assert '第 ' in str(ei.value)           # 报错要带坐标
        assert p.read_bytes() == raw            # 一个字节都没动

    def test_missing_source_falls_back_quietly(self, tmp_path):
        """源文件读不到（新建文件 / 别处副本）—— 不知道就不动，不是错误。"""
        missing = tmp_path / '__不存在__.rpy'
        text, style = read_text(tmp_path / 'x.rpy')
        assert style.apply('a\n', source=missing) == 'a\n'

    def test_bom_and_crlf_still_restored(self, tmp_path):
        raw = '\ufefflabel ch1:\r\n    "a"\r\n'.encode('utf-8')
        p = _write(tmp_path, raw)
        text, style = read_text(p)
        out = style.apply(text.replace('"a"', '"b"'), source=p).encode('utf-8')
        assert out == '\ufefflabel ch1:\r\n    "b"\r\n'.encode('utf-8')
        assert detect(p).bom and detect(p).newline == '\r\n'


# ---------------------------------------------------------------------------
#  三、真实改写路径：接线（编排层）
# ---------------------------------------------------------------------------

CTL = ('label ch01_control:\r\n    call ch01\r\n    return\r\n\r\n'
       'label ch02_control:\r\n    call ch02\r\n    return\r\n')


def _project(tmp_path):
    script = tmp_path / 'game' / 'script'
    script.mkdir(parents=True)
    for name in ('ch01', 'ch02'):
        (script / f'{name}.rpy').write_text(
            f'label {name}:\n    e "{name}"\n    return\n', encoding='utf-8')
    ctl = script / 'chapter_controller.rpy'
    return ctl


class TestControlRouteCarrier:
    def _run(self, tmp_path, body: bytes):
        from storygraph.story.control_route import (apply_control_route,
                                                    plan_control_route)
        ctl = _project(tmp_path)
        ctl.write_bytes(body)
        plan = plan_control_route(str(tmp_path), 'ch01', 'ch02', 'jump')
        assert plan.ok, plan.blocking
        return ctl, plan, apply_control_route(plan)

    def test_crlf_controller_keeps_every_byte(self, tmp_path):
        ctl, plan, res = self._run(tmp_path, CTL.encode('utf-8'))
        assert res['ok']
        after = ctl.read_bytes()
        assert b'\r\n' in after and after.count(b'\n') == after.count(b'\r\n')

    def test_u2028_in_controller_is_not_turned_into_newline(self, tmp_path):
        body = CTL.replace('call ch02', f'call ch02 # 注{LS}释').encode('utf-8')
        ctl, plan, res = self._run(tmp_path, body)
        assert res['ok'], res['error']
        after = ctl.read_bytes()
        assert after.count(LS.encode()) == body.count(LS.encode())
        # 行数不能因为一个"软回车"变多
        assert after.count(b'\n') == body.count(b'\n')

    def test_non_utf8_controller_is_refused_and_untouched(self, tmp_path):
        body = CTL.encode('utf-8').replace(b'label ch02_control',
                                           b'# \xff\xfe\r\nlabel ch02_control')
        ctl, plan, res = self._run(tmp_path, body)
        assert not res['ok']
        assert 'UTF-8' in res['error']
        assert ctl.read_bytes() == body


# ---------------------------------------------------------------------------
#  四、真实改写路径：删源行
# ---------------------------------------------------------------------------

SCRIPT = '''# 删边测试
label main:
    e "第一句"
    jump side
    e "第三句"
    return

label side:
    e "支线"
    return
'''


class TestSourceLineDeleteCarrier:
    def _run(self, tmp_path, body: bytes):
        from storygraph.app.editor import EditorSession
        from storygraph.app.source_line_delete import (
            apply_source_line_delete, plan_source_line_delete)
        script = tmp_path / 'game'
        script.mkdir(parents=True)
        p = script / 'ch01.rpy'
        p.write_bytes(body)
        session = EditorSession(project_root=str(tmp_path))
        session.load(str(p))
        beat = next(b for b in session.beats
                    if str(getattr(b, 'raw', '') or '').strip()
                    .startswith('jump '))
        plan = plan_source_line_delete(str(tmp_path), str(p), beat.line_id)
        assert plan.ok, plan.blocking
        res = apply_source_line_delete(plan)
        assert res['ok'], res['error']
        return p

    def test_u2028_in_trailing_comment_survives(self, tmp_path):
        body = (SCRIPT + f'# 尾注{LS}续\n').encode('utf-8')
        p = self._run(tmp_path, body)
        after = p.read_bytes()
        assert after.count(LS.encode()) == 1
        assert b'    jump side\n' not in after
        assert after.count(b'\n') == body.count(b'\n') - 1

    def test_no_trailing_newline_stays_that_way(self, tmp_path):
        p = self._run(tmp_path, SCRIPT.rstrip('\n').encode('utf-8'))
        assert not p.read_bytes().endswith(b'\n')


# ---------------------------------------------------------------------------
#  五、静默丢行：回执必须点名
# ---------------------------------------------------------------------------

class _Res:
    def __init__(self, count=0, preview=()):
        self.removed_count = count
        self.removed_preview = tuple(preview)


class TestRemovalNote:
    def test_no_removal_says_nothing(self):
        assert removal_note(_Res()) == ''

    def test_removal_is_named_with_count_and_lines(self):
        note = removal_note(_Res(2, ['$ flag = True',
                                     'play music "bgm.ogg" fadein 1.0']))
        assert '2 行' in note
        assert '$ flag = True' in note
        assert 'play music' in note

    def test_count_falls_back_to_preview_length(self):
        note = removal_note(_Res(0, ['a', 'b']))
        assert '2 行' in note


# ---------------------------------------------------------------------------
#  六、结构性守卫：这些地方不许再出现"会改写行边界"的写法
# ---------------------------------------------------------------------------

FORBIDDEN = [
    ('storygraph/parsing/renpy_parser.py', '.splitlines()'),
    ('storygraph/app/source_line_delete.py', '.splitlines()'),
    ('storygraph/story/control_route.py', '.splitlines()'),
    ('storygraph/story/control_branch.py', '.splitlines()'),
    # undo/redo 是整份写回：不传 `source=` 就等于"整份统一成一种换行"
    ('storygraph/graph/commands.py', 'style.apply('),
]


@pytest.mark.parametrize('rel,needle', FORBIDDEN)
def test_no_line_boundary_regressions(rel, needle):
    text = (ROOT / rel).read_text(encoding='utf-8')
    if needle == 'style.apply(':
        # 允许 `apply(...)`，但每一处都得带 `source=`
        for i, line in enumerate(text.split('\n'), 1):
            if 'style.apply(' in line:
                rest = text.split('\n')[i - 1:i + 1]
                joined = '\n'.join(rest)
                assert 'source=' in joined, (
                    f'{rel}:{i} 的 style.apply 没传 source= '
                    f'（混合换行文件会被整份归一化）')
        return
    assert needle not in text, f'{rel} 还有 {needle}（行边界只认 \\n）'


# ---------------------------------------------------------------------------
#  七、端到端：编辑器导出遇到"载入之后程序员又加了行"
# ---------------------------------------------------------------------------

class TestStaleChapterExportIsNeverSilent:
    """
    **判据只有一条**：源里消失的行，要么让导出被拒，要么在回执里被点名。

    这一条比"删得对不对"重要 —— 删是设计内的（表里没有对应格子），
    静默才是 P0。从前编辑器那条路（`session_export._patch_note`）一个字不提。
    """

    SRC3 = ('# 头\n'
            'label ch1:\n'
            '    jc "第一句"\n'
            '    jc "第二句"\n'
            '    jc "第三句"\n'
            '    return\n')

    def test_dropped_lines_are_either_refused_or_named(self, tmp_path):
        import json

        from storygraph.app.editor import EditorSession
        game = tmp_path / 'game'
        game.mkdir()
        path = game / 'ch1.rpy'
        path.write_bytes(self.SRC3.encode('utf-8'))
        session = EditorSession(project_root=tmp_path)
        assert session.load(path) >= 1
        session.apply(0, {'Text': '改过的第一句'})

        # 载入之后，程序员在同一份文件里又加了两行（策划那边还没重新载入）
        new_text = self.SRC3.replace(
            '    jc "第二句"\n',
            '    jc "第二句"\n    $ flag = True\n'
            '    play music "bgm.ogg" fadein 1.0\n')
        path.write_text(new_text, encoding='utf-8')

        res = session.export(path)
        blob = json.dumps(res, ensure_ascii=False, default=str)
        refused = not res.get('ok')
        named = '删掉了源文件里' in blob
        assert refused or named, (
            '导出既没拒写，也没说删掉了哪些行 —— 这就是静默丢行\n'
            f'回执：{blob}')
