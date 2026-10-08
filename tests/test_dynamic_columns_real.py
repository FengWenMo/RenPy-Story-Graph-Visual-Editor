"""
「每参数一列」的**真机维度**判据（camera 没有真机样本，所以要补维度）
==================================================================

`camera` 在真机 `sr_general` 里是 **0 处** —— 于是它的判据只有夹具覆盖 +
官方语法为据。这个文件把缺的维度补上，分三档：

  ① **不出现时表逐列不变**（真机维度，可自动跑）：真机确实没有 `camera`；
     不含 `with`/`pause`/`window`/`camera` 的章节，表**恰好**是基础列；
     全项目任何一章都不该冒出 `camera·` 列。
  ② **真机形状 + 出现**（把 camera 块接到一个**真实章节的副本**上）：
     列出现、值对、往返逐字节。
  ③ 顺带钉住真机里 `with`/`pause`/`window` 的列数与"没改就逐字节"
     （它们是真机里真的有的那三种）。

⚠ ① 与 ② 缺一不可：只做 ② 就不知道"没有它的时候表会不会悄悄多列"，
而那是**新功能污染老项目**最典型的样子。
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import BEAT_FIELDS
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.story.statement_registry import StatementRegistry

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')

#: 有"每参数一列"形状的语句头（`spec_args.BUILTIN_ARG_SCHEMAS` 的键）
HEADS = ('camera', 'with', 'pause', 'window')

_HEAD_RE = re.compile(r'^\s*(camera|with|pause|window)\b', re.MULTILINE)

pytestmark = pytest.mark.skipif(not REAL.is_dir(), reason='换机 skip')


def _chapters() -> list:
    return sorted((REAL / 'game').rglob('*.rpy'))


def _heads_in(text: str) -> set:
    return {m.group(1) for m in _HEAD_RE.finditer(text)}


def _pick(*, want=(), avoid=()):
    """挑一个真实章节：含 `want` 里所有头、且不含 `avoid` 里任何头。"""
    for p in _chapters():
        heads = _heads_in(p.read_text(encoding='utf-8', errors='ignore'))
        if all(h in heads for h in want) and not any(h in heads for h in avoid):
            return p
    return None


def _headers_of(beats, tmp_path, name='t.xlsx') -> list:
    x = tmp_path / name
    export_beats_to_excel(beats, x)
    import openpyxl
    wb = openpyxl.load_workbook(x)
    try:
        return [c.value for c in wb['Beats'][1]]
    finally:
        wb.close()


def _beats_of(path):
    pr = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)[0]
    return beats_from_parse_result(pr)


# ---------------------------------------------------------------------------
#  ① 不出现时表逐列不变（真机维度）
# ---------------------------------------------------------------------------

def test_real_project_has_no_camera_statement():
    """
    真机事实（写下来，免得以后有人以为"验证过 camera"）：**0 处 `camera`**。

    这一条是 ② 那条夹具判据存在的理由 —— 没有真机样本，就得自己造一个
    "真机形状 + camera"的副本。
    """
    pr, _ = IncrementalRenPyAnalyzer().analyze(
        files=discover_scripts(REAL), graph=None)
    from storygraph.core.vocabulary import statement_head_token
    heads = {statement_head_token(s.payload.get('text') or '')
             for s in pr.statements
             if s.kind == 'raw' or s.kind == 'camera'}
    assert 'camera' not in heads, '真机居然有 camera 了 —— 这条判据该改'


def test_chapter_without_those_statements_gets_exactly_the_base_columns(tmp_path):
    """
    **没有这几种语句的章节：表一列都不多**（新功能不许污染老项目）。

    ⚠ 这比"能加列"重要：加列是加法，多出来一列却是**别人表里的意外**。
    """
    p = _pick(avoid=HEADS)
    assert p is not None, '找不到"干净"的真实章节 —— 判据要换个挑法'
    headers = _headers_of(_beats_of(p), tmp_path)
    assert len(headers) == len(BEAT_FIELDS), (p.name, headers[-6:])
    # 判"这是不是动态列"要用**解析器**，不能拿 `·` 去认：基础列的中文表头里
    # 本来就有 `·`（`SpecHead` = 「自定义语句·头行（高级）」）——
    # 那是给人看的表头，`from_row` 会先经 `_HEADER_ALIASES` 映射回 `SpecHead`，
    # 不会走到动态列那条解析上。
    from storygraph.story.spec_args import is_dynamic_column
    assert not [h for h in headers if is_dynamic_column(h)], headers[-6:]


def test_no_camera_column_anywhere_in_a_chapter_table(tmp_path):
    """任何一章都不该冒出 `camera·` 列（项目里没有 camera 语句）。"""
    for p in _chapters()[:6]:
        headers = _headers_of(_beats_of(p), tmp_path, name=f'{p.stem}.xlsx')
        assert not [h for h in headers if str(h or '').startswith('camera·')], (
            p.name, headers[-4:])


# ---------------------------------------------------------------------------
#  ② 真机形状 + camera（造样本）
# ---------------------------------------------------------------------------

CAMERA_BLOCK = (
    'label probe_camera:\n'
    '    camera:\n'
    '        perspective True\n'
    '    camera background:\n'
    '        perspective True\n'
    '        xpos 0\n'
    '    "一句台词"\n'
)


def _real_shaped_project(tmp_path, extra: str):
    """
    把**一个真实章节**（含它的真实缩进与写法）复制进临时项目，再把 `extra`
    接到末尾 —— 这样夹具是"真机形状 + 我们要验的那几条语句"。
    """
    src = _pick(avoid=('camera',))
    assert src is not None
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding='utf-8')
    (game / 'ch.rpy').write_text(text.rstrip('\n') + '\n\n' + extra,
                                encoding='utf-8')
    return root, game / 'ch.rpy'


def test_camera_column_appears_on_a_real_shaped_chapter(tmp_path):
    root, path = _real_shaped_project(tmp_path, CAMERA_BLOCK)
    beats = _beats_of(path)
    headers = _headers_of(beats, tmp_path)
    assert 'camera·layer@0' in headers, headers[-6:]
    import openpyxl
    x = tmp_path / 'again.xlsx'
    export_beats_to_excel(beats, x)
    wb = openpyxl.load_workbook(x)
    try:
        ws = wb['Beats']
        col = headers.index('camera·layer@0') + 1
        vals = [ws.cell(row=r, column=col).value
                for r in range(2, ws.max_row + 1)]
    finally:
        wb.close()
    assert 'background' in vals, vals[-8:]


def test_camera_in_a_real_shaped_chapter_round_trips_byte_for_byte(tmp_path):
    root, path = _real_shaped_project(tmp_path, CAMERA_BLOCK)
    beats = _beats_of(path)
    reg = StatementRegistry.for_project(root)
    x = tmp_path / 't.xlsx'
    export_beats_to_excel(beats, x, statements=reg)
    back = import_beats_from_excel(x)
    assert render_beats_to_rpy(back) == render_beats_to_rpy(beats), (
        '过一趟表就把产物改了')


# ---------------------------------------------------------------------------
#  ③ 真机里真的有的那三种：列数 + 没改就逐字节
# ---------------------------------------------------------------------------

def test_real_chapter_with_leaf_statements_gets_their_columns(tmp_path):
    p = _pick(want=('pause',), avoid=('camera',))
    assert p is not None
    headers = _headers_of(_beats_of(p), tmp_path)
    assert 'pause·seconds@0' in headers, headers[-6:]


def test_real_chapter_leaf_columns_round_trip_byte_for_byte(tmp_path):
    """真机章节过一趟表，产物与"直接渲染"**逐字节相同**。"""
    p = _pick(want=('pause',), avoid=('camera',))
    assert p is not None
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, root / 'game' / 'ch.rpy')
    beats = _beats_of(root / 'game' / 'ch.rpy')
    reg = StatementRegistry.for_project(root)
    x = tmp_path / 't.xlsx'
    export_beats_to_excel(beats, x, statements=reg)
    back = import_beats_from_excel(x)
    assert render_beats_to_rpy(back) == render_beats_to_rpy(beats), (
        f'{p.name} 过表之后产物变了')
