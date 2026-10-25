# -*- coding: utf-8 -*-
"""
第六轮下半 P0-F：**扫描资源不许抹掉策划维护的资源表**。

`write_dict_to_excel` 是资源表（images / music / sounds / voices）的唯一写入口，
而它是"整份新建再 save"。代码自己的 docstring 就记着代价："2828 行会掉到 349 行"。

判据：
  · 人自加的列（`配音演员`）还在，值跟着 ID 走；
  · 人加的工作表（`分类说明`）还在；
  · 人标的底色 / 批注还在；
  · 手写的行（有 ID 没文件 / 没有 ID 的备注）还在；
  · **只有**"记了真实文件、文件又真的不在了"那种行才删（表跟随磁盘）；
  · 回执说清楚留了几行、删了几行。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('openpyxl')
import openpyxl                                    # noqa: E402
from openpyxl.comments import Comment              # noqa: E402
from openpyxl.styles import PatternFill            # noqa: E402

from storygraph.assets.asset_registry import write_asset_excel  # noqa: E402

HEADERS = ['编号', '真实文件', '标签', '立绘属性', '类别', '备注',
           '维度', '属性值']


def _fixture(tmp_path):
    tables = tmp_path / 'game' / 'asset_tables'
    tables.mkdir(parents=True)
    imgs = tmp_path / 'game' / 'images' / 'chars'
    imgs.mkdir(parents=True)
    for name in ('a.png', 'b.png'):
        (imgs / name).write_bytes(b'\x89PNG\r\n\x1a\n')

    xlsx = tables / 'images.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADERS + ['配音演员'])
    ws.append(['a', 'game/images/chars/a.png', 'a', '', 'character',
               '第一张', '', '', '小林'])
    ws.append(['b', 'game/images/chars/b.png', 'b', '', 'character',
               '', '', '', ''])
    ws.append(['future', '', 'future', '', 'character', '还没画', '', '', '待定'])
    ws.append(['gone', 'game/images/chars/gone.png', 'gone', '', 'character',
               '', '', '', ''])
    ws.append(['', '', '', '', '', '总备注：这个项目不要 UI 图', '', '', ''])
    ws.cell(row=3, column=1).fill = PatternFill('solid', fgColor='FFF2CC')
    ws.cell(row=3, column=1).comment = Comment('这张要重画', '策划')
    extra = wb.create_sheet('分类说明')
    extra['A1'] = 'character = 角色立绘'
    wb.save(xlsx)
    return tmp_path, tables, xlsx


def _scan(tmp_path):
    def row(aid, name):
        return {'ID': aid, 'Path': f'game/images/chars/{name}', 'Tag': aid,
                'Attributes': '', 'Category': 'character', 'Notes': ''}
    return {'images': [row('a', 'a.png'), row('b', 'b.png')],
            'audio': {'music': [], 'sound': []}, 'voices': []}


def _read(xlsx):
    ws = openpyxl.load_workbook(xlsx).worksheets[0]
    header = [str(c.value or '') for c in ws[1]]
    rows = {}
    for r in range(2, ws.max_row + 1):
        aid = str(ws.cell(row=r, column=1).value or '')
        rows[aid] = [ws.cell(row=r, column=c).value
                     for c in range(1, len(header) + 1)]
    return ws, header, rows


class TestAssetTableSurvivesRescan:
    def _run(self, tmp_path):
        tmp_path, tables, xlsx = _fixture(tmp_path)
        write_asset_excel(_scan(tmp_path), tables, split_by_category=False)
        return xlsx

    def test_user_column_and_values_survive(self, tmp_path):
        xlsx = self._run(tmp_path)
        _ws, header, rows = _read(xlsx)
        assert '配音演员' in header
        assert rows['a'][header.index('配音演员')] == '小林'
        assert rows['future'][header.index('配音演员')] == '待定'

    def test_hand_written_row_without_file_survives(self, tmp_path):
        xlsx = self._run(tmp_path)
        _ws, _h, rows = _read(xlsx)
        assert 'future' in rows, f'手写占位行被抹掉了：{list(rows)}'

    def test_note_row_without_id_survives(self, tmp_path):
        xlsx = self._run(tmp_path)
        _ws, _h, rows = _read(xlsx)
        assert any('总备注' in str(v) for v in (rows.get('') or []))

    def test_row_for_deleted_file_is_dropped(self, tmp_path):
        xlsx = self._run(tmp_path)
        _ws, _h, rows = _read(xlsx)
        assert 'gone' not in rows, '文件已经不在了，这一行该跟磁盘走'

    def test_marks_and_extra_sheet_survive(self, tmp_path):
        xlsx = self._run(tmp_path)
        ws, _h, _r = _read(xlsx)
        row_b = next(r for r in range(2, ws.max_row + 1)
                     if str(ws.cell(row=r, column=1).value or '') == 'b')
        assert ws.cell(row=row_b, column=1).comment is not None
        assert ws.cell(row=row_b, column=1).comment.text == '这张要重画'
        assert str(ws.cell(row=row_b, column=1).fill.fgColor.rgb) == '00FFF2CC'
        assert '分类说明' in openpyxl.load_workbook(xlsx).sheetnames

    def test_stats_report_what_was_kept(self, tmp_path):
        from storygraph.story.xlsx_preserve import last_asset_stats
        self._run(tmp_path)
        st = last_asset_stats()
        assert st.get('kept') == 2, st
        assert st.get('dropped') == 1, st
        assert st.get('columns') == 1 and st.get('sheets') == 1

    def test_fresh_table_has_no_extra_columns(self, tmp_path):
        tables = tmp_path / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        write_asset_excel(_scan(tmp_path), tables, split_by_category=False)
        xlsx = tables / 'images.xlsx'
        _ws, header, _rows = _read(xlsx)
        assert header == HEADERS, header


class TestRenamedIdColumn:
    """
    用户把「编号」列改名之后，追加行**不许丢 ID**。

    从前 `id_col = headers.index('ID') + 1 if 'ID' in headers else 1`：
    认不出身份列就退回第 1 列，而写行是 `[row.get(h) for h in headers]` ——
    ID 键不在 headers 里，于是**每一行都没写 ID**：判重全失效，
    每扫描一次就把同一批行再追加一遍（表越扫越长，且全是重复）。
    """

    def test_id_column_is_added_back_and_rows_keep_their_ids(self, tmp_path):
        from storygraph.assets.asset_registry import append_rows_to_table
        tables = tmp_path / 'tables'
        tables.mkdir()
        xlsx = tables / 'characters.xlsx'
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['编号号', '显示名'])          # 用户把「编号」改成了「编号号」
        ws.append(['old', '旧的'])
        wb.save(xlsx)

        append_rows_to_table(tables, 'characters',
                             [{'ID': 'new', 'Name': '新的'}])

        from storygraph.assets.asset_registry import _canon_field
        ws2 = openpyxl.load_workbook(xlsx).active
        header = [c.value for c in ws2[1]]
        # 补出来的列写的是规范键（英文 `ID` 或中文 `编号` 都认）
        assert any(_canon_field(h) == 'ID' for h in header), \
            f'身份列没补回来：{header}'
        col = next(i for i, h in enumerate(header) if _canon_field(h) == 'ID')
        ids = [str(ws2.cell(row=r, column=col + 1).value or '')
               for r in range(2, ws2.max_row + 1)]
        assert 'new' in ids, f'新行没写 ID（会重复追加）：{ids}'


class TestRenamedIdColumnStillKeepsValues:
    """「编号」列被改名时，人的列值仍要按 `真实文件` 跟过去（不能连值一起丢）。"""

    def test_user_values_follow_the_file_path(self, tmp_path):
        tables = tmp_path / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        imgs = tmp_path / 'game' / 'images'
        imgs.mkdir(parents=True)
        (imgs / 'a.png').write_bytes(b'\x89PNG\r\n\x1a\n')

        import openpyxl
        xlsx = tables / 'images.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['编号号', '真实文件', '标签', '配音演员'])   # 身份列被改名
        ws.append(['x', 'game/images/a.png', 'a', '小林'])
        wb.save(xlsx)

        from storygraph.assets.asset_registry import write_asset_excel
        write_asset_excel(
            {'images': [{'ID': 'a', 'Path': 'game/images/a.png', 'Tag': 'a',
                         'Attributes': '', 'Category': 'other', 'Notes': ''}],
             'audio': {'music': [], 'sound': []}, 'voices': []},
            tables, split_by_category=False)

        ws2 = openpyxl.load_workbook(xlsx).active
        header = [str(c.value or '') for c in ws2[1]]
        assert '配音演员' in header
        assert ws2.cell(row=2, column=header.index('配音演员') + 1).value == '小林'


class TestSaveErrorIsHumanReadable:
    """写不进去时要说人话（Excel 开着是最常见原因），且**异常类型不变**。"""

    def test_permission_error_mentions_excel_and_keeps_type(self, tmp_path):
        from storygraph.story.xlsx_preserve import save_workbook

        class Boom:
            def save(self, path):
                raise PermissionError(13, 'Permission denied', str(path))

        with pytest.raises(PermissionError) as ei:
            save_workbook(Boom(), tmp_path / 'images.xlsx')
        msg = str(ei.value)
        assert 'Excel' in msg and '一个字没动' in msg

    def test_disk_full_and_readonly_also_get_a_hint(self, tmp_path):
        from storygraph.story.xlsx_preserve import _save_hint
        assert '磁盘满' in _save_hint(OSError('No space left on device'),
                                     tmp_path / 'a.xlsx')
        assert '只读' in _save_hint(OSError('Read-only file system'),
                                    tmp_path / 'a.xlsx')


class TestBackupNeverEatsTheOriginal:
    """
    `write_text_atomic(backup=True)` 从前每次都重写同一个 `名字.bak`。

    后果：`force` 覆盖手写文件 → `.bak` = 原件；**下一次**覆盖写入 →
    `.bak` 被换成"工具上一次的产物"，**原件永久没了**。
    而 force 覆盖时它是唯一的安全网。
    """

    def test_second_write_does_not_clobber_the_first_backup(self, tmp_path):
        from storygraph.story.paths import write_text_atomic
        target = tmp_path / 'ch1.rpy'
        target.write_text('原始（人写的）\n', encoding='utf-8')

        write_text_atomic(target, '第一次生成\n', backup=True)
        first_bak = tmp_path / 'ch1.rpy.bak'
        assert first_bak.read_text(encoding='utf-8') == '原始（人写的）\n'

        write_text_atomic(target, '第二次生成\n', backup=True)
        # 第一份备份**一个字都不许变**
        assert first_bak.read_text(encoding='utf-8') == '原始（人写的）\n', \
            '第一份备份（原件）被覆盖了'
        # 第二次的备份另有文件
        assert list(tmp_path.glob('ch1.rpy.bak-*')), '第二次没有留下备份'
