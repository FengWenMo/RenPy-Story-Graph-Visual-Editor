"""
单元格里的**控制字符**不许变成产物里的物理换行
================================================

这是 P0-1（`_quote` 不转义控制字符）的**同一类**，但走的是**另一个出口**。

P0-1 修的是"字符串字面量"那条出口：`e "路径 C:\\new\\test"` 里的 `\\n`
在 Beat 里是**真换行**，靠 `_quote` 转义回 `\\n`。

而下面这些列是**裸 token** 出口 —— 值被逐字拼进一行代码，不经过任何转义：

    Speaker 列 = 'ling<真换行>X'     →  ling
                                        X "第一句"        ← **产物不可解析**
    At      列 = 'left<真换行>right' →  show ling at left
                                        right              ← 同上
    Show    列 = 'ling<真换行>happy' → 拆成 `show ling` + `show happy`
                                        —— 能解析，但**立绘变成两张**

单元格里出现真换行是**真实可达**的：Excel 的 Alt+Enter、从别处粘一段
多行文本、脚本写表，都会留下它。**后两种最阴**：产物"能解析"，中途断行
产生的是一个看起来正常的短行；门禁只看产物的 Python 语法 / 图像名合法性 /
label 重名，拦不住。

处置口径（**列契约本来就是"一行一个值"**）：读表时把控制字符压成空格
（`SINGLE_LINE_BEAT_FIELDS` + `sanitize_single_line_cell`），写表时同样压
（`Beat.to_row` 收尾）—— 两侧对称，往返幂等。

⚠ **压平本身就是"改了用户的数据"**（`ling\\nX` → `ling X`）。产物确实能跑了，
但值跟他在表里写的不一样了 —— 而本项目的立场是**静默改数据比报错坏得多**。
所以清洗**必须留痕**：`Beat.sanitized_cells` 记下哪几格被改过，
`validate` 汇总成 W114，`CompileResult.summary()` 也带一句。
本文件两条都钉。

> 覆盖口径：用例**由 `SINGLE_LINE_BEAT_FIELDS` 反推**（不手抄一份清单）。
> 手抄的话"在清单里"≠"测过" —— 有人把某列挪出清单，测试不会红。
> 现在挪一列，参数化用例与分区用例会**同时**红。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import (BEAT_FIELDS, BEAT_HEADERS, Beat,
                                         SINGLE_LINE_BEAT_FIELDS,
                                         sanitize_single_line_cell)
from storygraph.story.beat_render import make_symbols, render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

SRC = ('label a:\n'
       '    show ling happy\n'
       '    ling "第一句"\n'
       '    ling "第二句"\n')

#: 多行列：换行是**内容**，刻意不在 `SINGLE_LINE_BEAT_FIELDS` 里
#: （`Opaque` = 工具不认识的块语句整块原文，换行是它的结构）
MULTILINE_FIELDS = {'Text', 'Comment', 'Stage', 'CodeBlock', 'FileBlocks',
                    'Opaque'}

#: 这些列走的是**专门的解析路径**（不是 `g()`），所以"清洗留痕"那条不适用：
#: `Seq` 直接 `int()` 解析，控制字符进不到产物里，也就没有"压成空格"这回事。
NOT_SANITIZED_VIA_G = {'Seq'}

#: 顶格、又不属于任何合法顶层语句的行 = 被断开的尾巴
_TOP_LEVEL_OK = ('label ', 'init', 'default', 'define', 'transform', 'screen',
                 'image ', 'style ', 'pass', 'return', '#', 'menu', 'if ',
                 'elif ', 'else', 'while ', 'for ', 'onlayer')

#: 每列"单元格里可能长这样"的脏值。默认 `a<换行>b`，特殊的几列单独给。
_DIRTY_OVERRIDES = {
    'Label': 'a\nb',
    'Kind': 'say\nX',
    'Condition': 'x\ny',
    'Scene': 'bg\nroom',
    'Show': 'ling\nhappy',
    'Hide': 'ling\nhappy',
    'At': 'left\nright',
    'With': 'dissolve\nfade',
    'BGM': 'bgm/a\nb.mp3',
    'BGMParams': 'fadein\n1.0',
    'SFX': 'sfx/a\nb.wav',
    'SFXParams': 'volume\n0.5',
    'Voice': 'v/a\nb.ogg',
    'Speaker': 'ling\nX',
    'LineId': 'ch01.0001\nx',
    'ContentHash': 'deadbeef\ncafe',
    'StageOrder': 'show\nhide',
    'OpOrder': 'stage\nmusic',
    'CodeBlocks': '2\n3',
    'Seq': '7\n8',
}


def _dirty(field: str) -> str:
    """这一列的脏值（一定带**真换行**，且清洗后必然不同）。"""
    return _DIRTY_OVERRIDES.get(field, 'a\nb')


#: 「Excel 列名 → Beat 字段名」：只有几个不同名，其余同名
_ATTR = {'BGM': 'music', 'SFX': 'sound', 'With': 'with_'}

#: 用例表**由清单反推**，不手抄（见文件头"覆盖口径"）
CASES = {f: _dirty(f) for f in SINGLE_LINE_BEAT_FIELDS}


def _attr_of(column: str) -> str:
    return _ATTR.get(column, column[0].lower() + column[1:])


def loose_lines(text: str) -> list:
    """产物里"顶格又不合法"的行（断行事故的指纹）。"""
    return [(i, ln) for i, ln in enumerate(text.splitlines(), 1)
            if ln.strip() and not ln[:1].isspace()
            and not ln.strip().startswith(_TOP_LEVEL_OK)]


def renderable(text: str) -> tuple:
    """产物能不能被解析器吃回去 → (语句数, 错误串)。"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'o.rpy'
        f.write_text(text, encoding='utf-8')
        try:
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            return len(pr.statements), ''
        except Exception as e:                      # noqa: BLE001
            return -1, f'{type(e).__name__}: {e}'


def _dirty_table(column: str, value: str):
    """把脏值塞进表里的某一列，返回 (读回的 beats, 渲染产物)。"""
    from openpyxl import load_workbook

    from storygraph.assets.asset_registry import AssetRegistry

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        f = tmp / 's.rpy'
        f.write_text(SRC, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        xl = tmp / 's.xlsx'
        export_beats_to_excel(beats_from_parse_result(pr), xl)

        wb = load_workbook(xl)
        ws = wb['Beats']
        headers = [c.value for c in ws[1]]
        col = headers.index(BEAT_HEADERS[column]) + 1
        for r in (2, 3, 4):                 # 覆盖有台词 / 无台词的拍
            if r <= ws.max_row:
                ws.cell(row=r, column=col).value = value
        wb.save(xl)

        back = import_beats_from_excel(xl)
        reg = AssetRegistry()
        return back, render_beats_to_rpy(back, registry=reg,
                                         symbols=make_symbols(reg))


# ---------------------------------------------------------------------------
#  1. 单行列（**全部**）：控制字符必须在进产物之前被压掉
# ---------------------------------------------------------------------------

class TestSingleLineCellsAreFlattened:

    @pytest.mark.parametrize('column', sorted(CASES))
    def test_product_has_no_broken_line(self, column):
        """产物里不许出现"被断开的尾巴"，而且必须还能解析回来。"""
        _back, out = _dirty_table(column, CASES[column])
        loose = loose_lines(out)
        assert not loose, (
            f'`{column}` 列里的换行漏成了产物里的物理换行：\n'
            f'  尾巴：{loose[:3]}\n{out}')
        n, err = renderable(out)
        assert not err, f'产物回读失败：{err}\n{out}'
        assert n > 0

    @pytest.mark.parametrize('column', sorted(CASES))
    def test_cleaning_is_recorded(self, column):
        """
        每一列的清洗都要**留痕**（`Beat.sanitized_cells`）——
        否则就是"救了用户却没告诉他"（静默改数据）。
        """
        if column in NOT_SANITIZED_VIA_G:
            pytest.skip(f'{column} 走专门解析路径，不经清洗（见常量说明）')
        back, _out = _dirty_table(column, CASES[column])
        hits = [b for b in back if column in (b.sanitized_cells or [])]
        assert hits, (
            f'`{column}` 的值被清洗了，却没记进 `sanitized_cells` —— '
            f'W114 会漏报：{[b.sanitized_cells for b in back]}')

    def test_tab_is_also_flattened(self):
        """TAB 同样危险：它会被 Ren'Py 当缩进，位置/说话人都会错。"""
        _back, out = _dirty_table('Speaker', 'ling\tX')
        assert '\t' not in out, repr(out)
        assert not loose_lines(out), out

    def test_show_value_is_not_split_into_two_statements(self):
        """`Show = 'ling\\nhappy'` 必须还是一条 `show ling happy`。

        以前它被拆成 `show ling` + `show happy` 两条 —— 产物**能解析**，
        所以门禁拦不住，但立绘变成两张（语义静默改变）。
        """
        _back, out = _dirty_table('Show', 'ling\nhappy')
        shows = [ln.strip() for ln in out.splitlines()
                 if ln.strip().startswith('show ')]
        assert shows, out
        assert 'ling happy' in out, out


# ---------------------------------------------------------------------------
#  2. 多行列**不许**被压平（换行是内容）
# ---------------------------------------------------------------------------

class TestMultiLineCellsKeepTheirNewlines:

    def test_dialogue_newline_stays_escaped(self):
        _back, out = _dirty_table('Text', '第一行\n第二行')
        assert '第一行\\n第二行' in out, out
        assert not loose_lines(out), out

    def test_comment_newline_is_kept(self):
        """注释列的多行是内容（每条都要有 `#` 前缀，但行数不减）。"""
        _back, out = _dirty_table('Comment', '第一行\n第二行')
        assert '第一行' in out and '第二行' in out, out
        assert not loose_lines(out), out

    def test_multiline_fields_are_not_cleaned(self):
        """多行列**不许**进清洗留痕（它们本来就该有换行）。"""
        back, _out = _dirty_table('Text', '第一行\n第二行')
        assert all(not (b.sanitized_cells or []) for b in back), \
            [b.sanitized_cells for b in back]


# ---------------------------------------------------------------------------
#  3. 分区与覆盖：**由清单反推**，手抄不了
# ---------------------------------------------------------------------------

class TestFieldPartition:

    def test_every_field_is_classified(self):
        """
        每一列都必须被**显式**分到"单行"或"多行"里 —— 加列时强制做决定。

        没有这条，新加一列会默认掉进"不压平"那一侧，而它的值一旦被拼进
        一行代码就是下一次 P0-1。
        """
        classified = set(SINGLE_LINE_BEAT_FIELDS) | MULTILINE_FIELDS
        missing = [f for f in BEAT_FIELDS if f not in classified]
        assert not missing, (
            f'这些列没有分类（单行 / 多行）：{missing} —— '
            f'加列时必须在 `SINGLE_LINE_BEAT_FIELDS` 或本文件的 '
            f'`MULTILINE_FIELDS` 里明确选一边')
        overlap = set(SINGLE_LINE_BEAT_FIELDS) & MULTILINE_FIELDS
        assert not overlap, f'同时被分到两边：{overlap}'

    def test_cases_cover_the_whole_list(self):
        """
        **用例表必须覆盖清单里的每一列**（"在清单里" ≠ "测过"）。

        用例表由清单反推，所以这条在"有人往清单里加了一列"时会自动带上；
        真正的价值在反方向：**有人把某列挪出清单**时，
        `test_every_field_is_classified` 会先红（它没地方可去），
        而这条会跟着红 —— 两处一起指出来。
        """
        assert set(CASES) == set(SINGLE_LINE_BEAT_FIELDS), (
            f'用例没覆盖：{sorted(set(SINGLE_LINE_BEAT_FIELDS) - set(CASES))}')

    @pytest.mark.parametrize('column', sorted(CASES))
    def test_every_case_is_actually_dirty(self, column):
        """每个用例的脏值**真的会被改**（否则这条用例是空转的）。"""
        raw = CASES[column]
        assert sanitize_single_line_cell(column, raw) != raw, (
            f'`{column}` 的用例值 {raw!r} 清洗前后一样 —— 这条用例没在测东西')

    @pytest.mark.parametrize('column', sorted(CASES))
    def test_column_has_a_header(self, column):
        """单行列都得有中文表头（没有表头就写不进表，用例会假绿）。"""
        assert column in BEAT_HEADERS, column


# ---------------------------------------------------------------------------
#  4. 读写两侧对称（否则往返一次就变样）
# ---------------------------------------------------------------------------

class TestRowRoundTripIsSymmetric:

    @pytest.mark.parametrize('column', sorted(CASES))
    def test_to_row_flattens_too(self, column):
        beat = Beat(seq=1, label='a', text='x')
        setattr(beat, _attr_of(column), CASES[column])
        row = dict(zip(BEAT_FIELDS, beat.to_row()))
        value = str(row[column])
        assert '\n' not in value and '\t' not in value, (
            f'`{column}` 写表时没压平：{value!r}（读那一侧压了 → 往返不对称）')

    def test_round_trip_is_idempotent(self):
        beat = Beat(seq=1, label='a', text='x', speaker='ling\nX')
        once = Beat.from_row(dict(zip(BEAT_FIELDS, beat.to_row())))
        twice = Beat.from_row(dict(zip(BEAT_FIELDS, once.to_row())))
        assert once.speaker == twice.speaker == 'ling X'

    def test_to_row_length_mismatch_is_loud(self):
        """加列没同步 `to_row` 时要**报错**，不许静默截断。"""
        from storygraph.story.beat_model import _flatten_single_line_cells
        with pytest.raises(ValueError):
            _flatten_single_line_cells(['只有一格'])


# ---------------------------------------------------------------------------
#  5. 清洗必须**说出口**（静默改数据是最坏的那类缺陷）
# ---------------------------------------------------------------------------

class TestCleaningIsReported:

    def test_validate_reports_w114(self):
        from storygraph.pipeline.validate import validate_level1
        back, _out = _dirty_table('Speaker', 'ling\nX')
        issues = [i for i in validate_level1(back) if i.code == 'W114']
        assert issues, '清洗了却没报 —— 静默改数据'
        assert issues[0].severity == 'warning', issues[0]
        assert '说话人' in issues[0].message, issues[0].message

    def test_validate_aggregates_instead_of_flooding(self):
        """几十行一起脏时只报**一条**（一行一条会刷屏，人就不看了）。"""
        from storygraph.pipeline.validate import validate_level1
        back, _out = _dirty_table('Show', 'ling\nhappy')
        issues = [i for i in validate_level1(back) if i.code == 'W114']
        assert len(issues) == 1, [i.message for i in issues]

    def test_compile_summary_mentions_it(self):
        from storygraph.pipeline.compile import compile_beats
        back, _out = _dirty_table('Speaker', 'ling\nX')
        summary = compile_beats(back).summary()
        assert 'W114' in summary or '压成空格' in summary, summary

    def test_ui_export_note_mentions_it(self, tmp_path):
        """
        UI 那条路（`EditorSession.export` 的 `note`）也要说 —— 它是
        "打开表 → 导出、从不体检"的人唯一会看到的地方。
        """
        from storygraph.app.editor import EditorSession

        back, _out = _dirty_table('Speaker', 'ling\nX')
        s = EditorSession(project_root=tmp_path)
        s.beats = back
        res = s.export(tmp_path / 'out.rpy')
        assert res['ok'], res
        assert '压成空格' in res['note'], res['note']

    def test_ui_export_note_is_quiet_when_clean(self, tmp_path):
        """干净的会话**不许**多一句话（通知一多就没人看）。"""
        from storygraph.app.editor import EditorSession

        back, _out = _dirty_table('Speaker', 'ling')
        s = EditorSession(project_root=tmp_path)
        s.beats = back
        res = s.export(tmp_path / 'out.rpy')
        assert res['ok'], res
        assert '压成空格' not in res['note'], res['note']

    def test_clean_table_reports_nothing(self):
        """**别误伤**：干净的表现在不该冒 W114，也不该有留痕。"""
        from storygraph.pipeline.validate import validate_level1
        from storygraph.pipeline.compile import compile_beats

        back, _out = _dirty_table('Speaker', 'ling')      # 干净值
        assert all(not (b.sanitized_cells or []) for b in back)
        assert not [i for i in validate_level1(back) if i.code == 'W114']
        assert compile_beats(back).sanitized_cells == 0

    def test_tool_written_table_is_never_dirty(self):
        """
        工具自己写出去的表**永远不脏** —— 否则每次往返都会报一条 W114。
        """
        back, _out = _dirty_table('Speaker', 'ling')
        xl_rows = [b.to_row() for b in back]
        for row in xl_rows:
            for f, v in zip(BEAT_FIELDS, row):
                if isinstance(v, str) and f in SINGLE_LINE_BEAT_FIELDS:
                    assert not any(ch in v for ch in '\n\r\t'), (f, v)
