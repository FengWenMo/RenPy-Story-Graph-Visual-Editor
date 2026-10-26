# -*- coding: utf-8 -*-
"""
P1 讨论①：**无标题 menu 加标题**的收口（2026-10-26）。

三个缺口（判据对应三节）：
  1. **单拍渲染**把菜单 extras（`set seen`）当 Python 补了 `$`、还排到 `menu:`
     前面 —— 与整份渲染 `render_menu_segments` 两套口径，增量补丁永远配不上；
  2. **嵌在 `if` 里**的菜单：生成结果带 `if 条件:` 头，标题通道拿整串比，
     永远配不上；
  3. **身份判据**只认"无标题"形态（`text == MENU_NO_CAPTION`）—— 用户加了
     标题后新标题不在原文里，判"错位"→ `allow_edit=False` → 标题通道关门。

形态矩阵与 `tools/_p1debate_impl_menu_caption.py` 对齐（两条出口同一口径，
铁律 6）。判据构造**期望全文**逐行比。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('openpyxl')

from storygraph.story.beat_model import (Beat, KIND_MENU,  # noqa: E402
                                        MENU_NO_CAPTION)
from storygraph.story.beat_render import render_beat_to_rpy  # noqa: E402


def _body(text: str) -> list:
    """剥掉生成标记头（`# 由剧本表自动生成…` + 空行）。"""
    out, skip = [], False
    for ln in text.splitlines():
        if ln.startswith('# 由剧本表自动生成'):
            skip = True
            continue
        if skip and not ln.strip():
            skip = False
            continue
        out.append(ln)
    return out


def _make_project(tmp: Path, src: str):
    game = tmp / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    return path


# =====================================================================
#  1. 单拍渲染与整份同口径：extras 原样在标题之后，不补 `$`
# =====================================================================

class TestMenuExtrasRender:
    def test_extras_are_verbatim_after_caption(self):
        """`set seen` 不是 Python —— 逐字吐在标题之后，与整份渲染同序。"""
        b = Beat(kind=KIND_MENU, text='新标题', raw='set seen')
        lines = render_beat_to_rpy(b)
        assert lines == ['menu:', '    "新标题"', '    set seen'], lines

    def test_extras_never_get_a_dollar_prefix(self):
        b = Beat(kind=KIND_MENU, text=MENU_NO_CAPTION, raw='set seen')
        lines = render_beat_to_rpy(b)
        assert all(not ln.strip().startswith('$') for ln in lines), lines
        assert '    set seen' in lines, lines

    def test_no_extras_keeps_the_old_shape(self):
        b = Beat(kind=KIND_MENU, text=MENU_NO_CAPTION, menu_name='m')
        assert render_beat_to_rpy(b) == ['menu m:'], render_beat_to_rpy(b)


# =====================================================================
#  2. 加标题：两条出口 × 形状矩阵（期望全文逐行）
# =====================================================================

SHAPES = {
    '裸 menu:': (
        'label start:\n'
        '    menu:\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n',
        'label start:\n'
        '    menu:\n'
        '        "新标题"\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n'),
    '嵌在 if 里': (
        'label start:\n'
        '    if flag:\n'
        '        menu:\n'
        '            "开始":\n'
        '                e "a"\n'
        '    return\n',
        'label start:\n'
        '    if flag:\n'
        '        menu:\n'
        '            "新标题"\n'
        '            "开始":\n'
        '                e "a"\n'
        '    return\n'),
    '带 extras（set seen）': (
        'label start:\n'
        '    menu:\n'
        '        set seen\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n',
        'label start:\n'
        '    menu:\n'
        '        "新标题"\n'
        '        set seen\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n'),
    '带名字': (
        'label start:\n'
        '    menu my_menu:\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n',
        'label start:\n'
        '    menu my_menu:\n'
        '        "新标题"\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n'),
}


def _card_path(src: str, tmp: Path) -> tuple:
    from storygraph.app.editor import EditorSession
    path = _make_project(tmp, src)
    s = EditorSession(project_root=tmp)
    s.load(path)
    fields = s.fields(0)
    fields.update({'Text': '新标题'})
    s.apply(0, fields, cards=s.cards(0), advanced_open=True,
            stage_loaded=fields.get('Stage'))
    res = s.export(path)
    return bool(res.get('ok')), path.read_bytes().decode('utf-8'), res


def _table_path(src: str, tmp: Path) -> tuple:
    """表格路：摊表 → 改「台词」格 → export_table + apply_export。"""
    import openpyxl
    from storygraph.app.tables import apply_export, export_table
    from storygraph.story.script_table import export_beats_to_excel
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser

    path = _make_project(tmp, src)
    pr = ParseResultBuilder().build(RenPyParser().parse(path))
    beats = beats_from_parse_result(pr)
    xlsx = tmp / 'script.xlsx'
    export_beats_to_excel(beats, xlsx)
    wb = openpyxl.load_workbook(xlsx)
    ws = wb['Beats'] if 'Beats' in wb.sheetnames else wb.active
    headers = [str(c.value or '') for c in ws[1]]
    col = next(headers.index(n) + 1
               for n in ('台词', 'Text', 'text') if n in headers)
    for r in range(2, ws.max_row + 1):
        v = ws.cell(row=r, column=col).value
        if not v or str(v) == '\x00menu':
            ws.cell(row=r, column=col).value = '新标题'
            break
    wb.save(xlsx)
    result = export_table(xlsx, source_rpy=path)
    applied = (apply_export(result, project_root=tmp)
               if result.get('ok') and result.get('merged_from') else result)
    text = path.read_bytes().decode('utf-8')
    ok = bool(result.get('ok')) and bool((applied or {}).get('ok', True))
    return ok, text, result


@pytest.mark.parametrize('name', sorted(SHAPES))
class TestAddTitleFromScratch:
    def test_card_path_lands_exactly(self, name, tmp_path):
        src, expect = SHAPES[name]
        ok, text, res = _card_path(src, tmp_path)
        assert ok, f'{name}: 卡片路被拒 — {res}'
        assert _body(text) == _body(expect), (
            f'{name}: 卡片路全文不符\n实际={_body(text)}\n期望={_body(expect)}')

    def test_table_path_lands_exactly(self, name, tmp_path):
        src, expect = SHAPES[name]
        ok, text, res = _table_path(src, tmp_path)
        assert _body(text) == _body(expect), (
            f'{name}: 表格路全文不符\n实际={_body(text)}\n期望={_body(expect)}')


class TestZeroEditAndGuards:
    def test_zero_edit_with_extras_is_byte_identical(self, tmp_path):
        """不改内容：带 extras 的菜单逐字节不动（生成标记除外）。"""
        src = SHAPES['带 extras（set seen）'][0]
        from storygraph.app.editor import EditorSession
        path = _make_project(tmp_path, src)
        s = EditorSession(project_root=tmp_path)
        s.load(path)
        res = s.export(path)
        assert res.get('ok'), res
        assert _body(path.read_bytes().decode('utf-8')) == _body(src)

    def test_blank_caption_survives(self, tmp_path):
        """`" "` 空白标题不许被顺手删掉（既有护栏不许回归）。"""
        src = (
            'label start:\n'
            '    menu:\n'
            '        " "\n'
            '        "开始":\n'
            '            e "a"\n'
            '    return\n'
        )
        from storygraph.app.editor import EditorSession
        path = _make_project(tmp_path, src)
        s = EditorSession(project_root=tmp_path)
        s.load(path)
        res = s.export(path)
        assert res.get('ok'), res
        assert _body(path.read_bytes().decode('utf-8')) == _body(src)
