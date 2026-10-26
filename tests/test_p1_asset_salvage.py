# -*- coding: utf-8 -*-
"""
P1 讨论④：**坏表不许推平用户的工作台**（2026-10-26）。

从前：表损坏 / 被 Excel 占用 → `_quarantine_broken_tables` **挪走原件** →
`write_asset_excel` 整份重建 → 策划的列 / 行 / 页 / 批注全没，只剩一个
`.broken-` 隔离副本。用户对同类问题（P0-F）已明确表态："隔离解决不了根本问题"。

三层修法（见 `docs/P1讨论_路线敲定_2026-10-26.md`）：
  1. **被占用 ≠ 坏表**：`PermissionError` 一个字节不动，报"请先关闭 Excel"；
  2. **坏表先抢救再合并**：zip 级 `salvage_rows`（sheet XML 常常完好）→
     抢救行只进 keep 通道，不覆盖扫描行；
  3. **zip 都烂了**才隔离重建，且回执点名丢了什么、隔离副本在哪。

判据构造期望值来自实测（`tools/_p1_verify_salvage.py` 先证过抢救可行性）。
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('openpyxl')
import openpyxl                                    # noqa: E402

from storygraph.assets.asset_registry import (                  # noqa: E402
    _quarantine_broken_tables, write_asset_excel, write_dict_to_excel)
from storygraph.story.xlsx_preserve import (                    # noqa: E402
    last_asset_stats, salvage_rows)

HEADERS = ['编号', '真实文件', '标签', '立绘属性', '类别', '备注',
           '维度', '属性值']


def _good_xlsx(xlsx: Path):
    """一张策划维护过的表：自加列 + 手写行 + 无 ID 备注行。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADERS + ['配音演员'])
    ws.append(['a', 'game/images/chars/a.png', 'a', '', 'character',
               '第一张', '', '', '小林'])
    ws.append(['future', '', 'future', '', 'character', '还没画', '', '', '待定'])
    ws.append(['', '', '', '', '', '总备注：这个项目不要 UI 图', '', '', ''])
    wb.save(xlsx)


def _corrupt_member(src: Path, dst: Path, target: str):
    """把 zip 里某一项换成坏内容 —— openpyxl 拒载、sheet XML 完好。"""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w') as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == target:
                data = b'<broken>NOT-XML<'
            zout.writestr(item, data)


def _scan_only_a():
    return {'images': [{'ID': 'a', 'Path': 'game/images/chars/a.png',
                        'Tag': 'a', 'Attributes': '', 'Category': 'character',
                        'Notes': ''}],
            'audio': {'music': [], 'sound': []}, 'voices': []}


def _read_rows(xlsx: Path) -> dict:
    ws = openpyxl.load_workbook(xlsx).worksheets[0]
    header = [str(c.value or '') for c in ws[1]]
    rows = {}
    for r in range(2, ws.max_row + 1):
        aid = str(ws.cell(row=r, column=1).value or '')
        rows[aid] = {header[c - 1]: ws.cell(row=r, column=c).value
                     for c in range(1, len(header) + 1)}
    return rows


def _read_headers(xlsx: Path) -> list:
    ws = openpyxl.load_workbook(xlsx).worksheets[0]
    return [str(c.value or '') for c in ws[1]]


# =====================================================================
#  层 2：坏表先抢救，再合并
# =====================================================================

class TestSalvageThenMerge:
    def test_user_column_and_values_survive(self, tmp_path):
        """workbook.xml 损坏 → 扫描后「配音演员」列和值都还在。"""
        tables = tmp_path / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars').mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars' / 'a.png').write_bytes(b'\x89PNG')
        xlsx = tables / 'images.xlsx'
        _good_xlsx(xlsx)
        _corrupt_member(xlsx, xlsx.with_suffix('.c.xlsx'), 'xl/workbook.xml')
        shutil.move(xlsx.with_suffix('.c.xlsx'), xlsx)

        write_asset_excel(_scan_only_a(), tables, split_by_category=False)

        assert '配音演员' in _read_headers(xlsx), _read_headers(xlsx)
        rows = _read_rows(xlsx)
        assert rows['a'].get('配音演员') == '小林', rows.get('a')

    def test_handwritten_future_row_survives(self, tmp_path):
        """手写占位行（`future`，没真实文件）不许被坏表重建吃掉。"""
        tables = tmp_path / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars').mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars' / 'a.png').write_bytes(b'\x89PNG')
        xlsx = tables / 'images.xlsx'
        _good_xlsx(xlsx)
        _corrupt_member(xlsx, xlsx.with_suffix('.c.xlsx'), 'xl/workbook.xml')
        shutil.move(xlsx.with_suffix('.c.xlsx'), xlsx)

        write_asset_excel(_scan_only_a(), tables, split_by_category=False)

        rows = _read_rows(xlsx)
        assert 'future' in rows, sorted(rows)
        assert rows['future'].get('配音演员') == '待定', rows.get('future')

    def test_no_id_note_row_survives(self, tmp_path):
        tables = tmp_path / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars').mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars' / 'a.png').write_bytes(b'\x89PNG')
        xlsx = tables / 'images.xlsx'
        _good_xlsx(xlsx)
        _corrupt_member(xlsx, xlsx.with_suffix('.c.xlsx'), 'xl/workbook.xml')
        shutil.move(xlsx.with_suffix('.c.xlsx'), xlsx)

        write_asset_excel(_scan_only_a(), tables, split_by_category=False)

        headers = _read_headers(xlsx)
        ws = openpyxl.load_workbook(xlsx).worksheets[0]
        notes_col = headers.index('备注') + 1
        notes = [ws.cell(row=r, column=notes_col).value
                 for r in range(2, ws.max_row + 1)]
        assert any('总备注' in str(n or '') for n in notes), notes

    def test_receipt_says_what_could_not_be_salvaged(self, tmp_path):
        """抢救拿不到批注/底色/额外页 → 回执必须点名，不许装作保住了。"""
        tables = tmp_path / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars').mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars' / 'a.png').write_bytes(b'\x89PNG')
        xlsx = tables / 'images.xlsx'
        _good_xlsx(xlsx)
        _corrupt_member(xlsx, xlsx.with_suffix('.c.xlsx'), 'xl/workbook.xml')
        shutil.move(xlsx.with_suffix('.c.xlsx'), xlsx)

        write_asset_excel(_scan_only_a(), tables, split_by_category=False)

        stats = last_asset_stats()
        assert stats.get('rescued') is True, stats
        assert '批注' in str(stats.get('rescued_lost', '')), stats

    def test_scan_row_fields_are_not_overridden_by_salvage(self, tmp_path):
        """红线（实现派）：抢救行只进 keep 通道，扫描行的自动字段不许被冲掉。"""
        tables = tmp_path / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars').mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'chars' / 'a.png').write_bytes(b'\x89PNG')
        xlsx = tables / 'images.xlsx'
        # 表里 a 的真实文件列是**过时的**（指向别处）—— 扫描说它在 chars/a.png，
        # 抢救行说在 old/a.png。以扫描为准。
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(HEADERS)
        ws.append(['a', 'game/images/old/a.png', 'a', '', 'character',
                   '旧的', '', ''])
        wb.save(xlsx)
        _corrupt_member(xlsx, xlsx.with_suffix('.c.xlsx'), 'xl/workbook.xml')
        shutil.move(xlsx.with_suffix('.c.xlsx'), xlsx)

        write_asset_excel(_scan_only_a(), tables, split_by_category=False)

        rows = _read_rows(xlsx)
        assert rows['a'].get('真实文件') == 'game/images/chars/a.png', rows['a']
        assert rows['a'].get('备注') == '旧的', rows['a']   # 人的列照救


# =====================================================================
#  层 1：被占用 ≠ 坏表
# =====================================================================

class TestLockedIsNotBroken:
    def test_locked_file_is_not_quarantined(self, tmp_path, monkeypatch):
        """`_quarantine_broken_tables` 遇到 PermissionError 一个副本都不做。"""
        tables = tmp_path
        xlsx = tables / 'images.xlsx'
        _good_xlsx(xlsx)
        before = xlsx.read_bytes()

        import storygraph.assets.asset_registry as AR
        monkeypatch.setattr(
            AR, 'read_excel_rows',
            lambda p: (_ for _ in ()).throw(PermissionError(13, 'locked')))
        moved = _quarantine_broken_tables(tables, 'test')
        assert moved == [], moved
        assert xlsx.read_bytes() == before, '被占用的表连一个字节都不许动'

    def test_snapshot_propagates_permission_error(self, tmp_path, monkeypatch):
        """`snapshot_asset_table` 不许把锁吞成 None（那会走"推平重建"）。"""
        from storygraph.story.xlsx_preserve import snapshot_asset_table
        xlsx = tmp_path / 'images.xlsx'
        _good_xlsx(xlsx)
        before = xlsx.read_bytes()

        import openpyxl as _oxl
        def _boom(*a, **k):
            raise PermissionError(13, 'locked')
        monkeypatch.setattr(_oxl, 'load_workbook', _boom)
        with pytest.raises(PermissionError):
            snapshot_asset_table(xlsx, [])
        assert xlsx.read_bytes() == before

    def test_ensure_reports_occupied_not_rebuild(self, tmp_path, monkeypatch):
        """端到端：`ensure_asset_tables` 对锁着的表报"被占用"，零写。"""
        from storygraph.assets.asset_registry import ensure_asset_tables
        tables = tmp_path / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        (tmp_path / 'game' / 'images').mkdir(parents=True)
        (tmp_path / 'game' / 'images' / 'a.png').write_bytes(b'\x89PNG')
        xlsx = tables / 'images.xlsx'
        _good_xlsx(xlsx)
        before = xlsx.read_bytes()

        import storygraph.assets.asset_registry as AR
        def _boom(*a, **k):
            raise PermissionError(13, 'locked')
        monkeypatch.setattr(AR, 'read_excel_rows', _boom)
        monkeypatch.setattr(AR.openpyxl, 'load_workbook', _boom)
        res = ensure_asset_tables(tmp_path, parse_check=True)
        assert res['ok'] is False, res
        assert '占用' in str(res.get('error', '')), res
        assert xlsx.read_bytes() == before, '零写'
        assert not list(tables.glob('*.broken-*')), '锁着不许出隔离副本'


# =====================================================================
#  层 3：zip 都烂了才隔离重建，且要点名
# =====================================================================

class TestTruncatedZip:
    def test_quarantine_copies_and_receipt_names_loss(self, tmp_path):
        tables = tmp_path
        xlsx = tables / 'images.xlsx'
        _good_xlsx(xlsx)
        data = xlsx.read_bytes()
        xlsx.write_bytes(data[:len(data) // 2])   # 截断

        moved = _quarantine_broken_tables(tables, 'test')
        assert any('broken' in str(m) for m in moved), moved
        # 原件留着（复制不是挪走）—— 抢救/人工还有机会
        assert xlsx.exists(), '原件不许被挪走'
        # 抢救确实救不回（这是隔离重建的前提）
        assert salvage_rows(xlsx) == ([], [])

    def test_salvage_reads_openpyxl_inline_str(self, tmp_path):
        """openpyxl 写的是 `t="inlineStr"` —— 漏了这个分支就读出一片空。"""
        xlsx = tmp_path / 's.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['ID', '名称'])
        ws.append(['x1', '中文名字'])
        wb.save(xlsx)
        _corrupt_member(xlsx, xlsx.with_suffix('.c.xlsx'), 'xl/workbook.xml')
        xlsx.with_suffix('.c.xlsx').replace(xlsx)

        headers, rows = salvage_rows(xlsx)
        assert headers == ['ID', '名称'], headers
        assert rows == [{'ID': 'x1', '名称': '中文名字'}], rows
