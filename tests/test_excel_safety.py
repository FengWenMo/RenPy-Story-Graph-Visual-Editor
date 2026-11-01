# -*- coding: utf-8 -*-
"""
Excel **读**那一头的安全判据（2026-11-01，第二轮审查 §5.5 的另一半）
=====================================================================

审查只说对了写盘（19 处 `wb.save()` 是裸的）。这一轮补完写盘之后，读那一头
还全是 `openpyxl.load_workbook(path)` —— 而 **xlsx 是一个 zip**，里面装什么
由写它的人决定：

  · 解压炸弹（1 MB 解开 4 GB）→ 打开就吃满内存；
  · 声明维度炸弹（`<dimension ref="A1:XFD1048576"/>`）→ `ws.max_row` 一百万，
    本项目的导入器正是按它循环的 → 卡死；
  · 宏 / 外部链接 / 嵌入对象 / 查询连接 → 读回来的东西不是"这份文件里的"；
  · 加密 / 老式 `.xls` / 改名的文件 → openpyxl 只会甩一串英文栈。

判据分三层：

  ① **真的造出这几种文件**（不是打桩）：手工 zip、OLE 头、真 `.xlsm` 结构；
  ② 该拒的拒（`UnsafeWorkbook` + 人话）、该提醒的提醒（`safeguard` 留痕）；
  ③ **横扫**：除 `excel/safety.py` 外，全项目不许直接调 `openpyxl.load_workbook`
     —— 否则"统一入口"只是口号。
"""

from __future__ import annotations

import ast
import io
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

pytest.importorskip('openpyxl')
import openpyxl                                          # noqa: E402

from storygraph.core import safeguard                     # noqa: E402
from storygraph.excel import safety                       # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    safety._CACHE.clear()
    safeguard.clear()
    yield
    safety._CACHE.clear()
    safeguard.clear()


def _mini_xlsx(path: Path, *, sheet_xml: str = None, extra: dict = None,
               payload: bytes = b'') -> Path:
    """
    手工造一个"结构上像 xlsx"的 zip。

    只给 `inspect_workbook` 看（它只读 zip 目录 + sheet 头 4 KB），
    所以不需要是 openpyxl 能打开的合法工作簿。
    """
    sheet_xml = sheet_xml or (
        '<?xml version="1.0"?><worksheet>'
        '<dimension ref="A1:B2"/><sheetData/></worksheet>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',
                   '<?xml version="1.0"?><Types/>')
        z.writestr('xl/workbook.xml', '<?xml version="1.0"?><workbook/>')
        z.writestr('xl/worksheets/sheet1.xml', sheet_xml)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
        if payload:
            z.writestr('xl/media/blob.bin', payload, compress_type=zipfile.ZIP_DEFLATED)
    return path


def _real_xlsx(path: Path, value: str = 'x') -> Path:
    wb = openpyxl.Workbook()
    wb.active['A1'] = value
    wb.save(str(path))
    return path


def _add_zip_entry(path: Path, name: str, data: bytes = b'x') -> Path:
    """往一份**真工作簿**里塞一个条目（造"带宏 / 带外链"的表）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(path) as src:
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                dst.writestr(item, src.read(item.filename))
            dst.writestr(name, data)
    path.write_bytes(buf.getvalue())
    return path


class TestNormalWorkbookPasses:
    def test_a_real_workbook_is_ok(self, tmp_path):
        p = _real_xlsx(tmp_path / 'ok.xlsx', '你好')
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is True
        assert rep['hard'] == []
        assert rep['info']['entries'] >= 3
        assert rep['info']['sheets'] == 1

    def test_open_workbook_returns_a_workbook(self, tmp_path):
        p = _real_xlsx(tmp_path / 'ok.xlsx', '内容')
        wb = safety.open_workbook(p, purpose='test')
        try:
            assert wb.active['A1'].value == '内容'
        finally:
            wb.close()
        assert safety.last_report()['ok'] is True

    def test_purpose_is_only_for_the_trail(self, tmp_path):
        p = _real_xlsx(tmp_path / 'ok.xlsx')
        safety.open_workbook(p, purpose='assets').close()

    def test_report_cache_does_not_hide_a_changed_file(self, tmp_path):
        p = _real_xlsx(tmp_path / 'ok.xlsx')
        assert safety.inspect_workbook(p)['ok'] is True
        p.write_bytes(b'not a zip at all')           # mtime 变了
        assert safety.inspect_workbook(p)['ok'] is False


class TestHardRefusals:
    def test_missing_file(self, tmp_path):
        rep = safety.inspect_workbook(tmp_path / 'nope.xlsx')
        assert rep['ok'] is False and '读不到' in rep['hard'][0]

    def test_empty_file(self, tmp_path):
        p = tmp_path / 'empty.xlsx'
        p.write_bytes(b'')
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False and '0 字节' in rep['hard'][0]

    def test_old_xls_gets_a_sentence_not_a_traceback(self, tmp_path):
        p = tmp_path / 'old.xls'
        p.write_bytes(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 100)
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False
        assert '另存为 .xlsx' in rep['hard'][0]

    def test_wrong_suffix(self, tmp_path):
        p = tmp_path / 'script.rpy'
        p.write_bytes(b'label a:\n    return\n')
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False and '.xlsx' in rep['hard'][0]

    def test_renamed_non_zip(self, tmp_path):
        p = tmp_path / 'fake.xlsx'
        p.write_bytes(b'this is not a zip')
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False and 'zip' in rep['hard'][0]

    def test_encrypted_or_ole_container(self, tmp_path):
        p = tmp_path / 'enc.xlsx'
        p.write_bytes(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 200)
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False and '复合文档' in rep['hard'][0]

    def test_zip_without_excel_structure(self, tmp_path):
        p = tmp_path / 'just.xlsx'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('hello.txt', 'hi')
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False and '不是工作簿' in rep['hard'][0]

    def test_declared_dimension_bomb(self, tmp_path):
        """`A1:XFD1048576` = 17 万亿格 —— 打开它，`ws.max_row` 就是 1048576。"""
        p = _mini_xlsx(tmp_path / 'bomb.xlsx', sheet_xml=(
            '<?xml version="1.0"?><worksheet>'
            '<dimension ref="A1:XFD1048576"/><sheetData/></worksheet>'))
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False
        assert '声明的范围' in rep['hard'][0]

    def test_uncompressed_bomb(self, tmp_path, monkeypatch):
        monkeypatch.setattr(safety, 'MAX_UNCOMPRESSED', 4096)
        monkeypatch.setattr(safety, 'MAX_ENTRY_BYTES', 4096)
        p = _mini_xlsx(tmp_path / 'bomb2.xlsx', payload=b'\x00' * 65536)
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False
        assert '解压炸弹' in rep['hard'][0] or 'MB' in rep['hard'][0]

    def test_too_many_entries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(safety, 'MAX_ENTRIES', 5)
        p = _mini_xlsx(tmp_path / 'many.xlsx',
                       extra={f'xl/x{i}.txt': 'x' for i in range(10)})
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False and '条目' in rep['hard'][0]

    def test_huge_compressed_size(self, tmp_path, monkeypatch):
        monkeypatch.setattr(safety, 'MAX_BYTES', 16)
        p = _real_xlsx(tmp_path / 'big.xlsx')
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is False and '超过上限' in rep['hard'][0]

    def test_open_workbook_raises_for_hard_issues(self, tmp_path):
        p = tmp_path / 'bad.xlsx'
        p.write_bytes(b'not a zip')
        with pytest.raises(safety.UnsafeWorkbook) as exc:
            safety.open_workbook(p)
        assert 'bad.xlsx' in str(exc.value)
        assert issubclass(safety.UnsafeWorkbook, ValueError)
        assert not issubclass(safety.UnsafeWorkbook, OSError)


class TestSoftWarnings:
    def test_macro_is_flagged_but_still_readable(self, tmp_path):
        p = _mini_xlsx(tmp_path / 'm.xlsm',
                       extra={'xl/vbaProject.bin': b'fake macro'})
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is True
        assert any('带宏' in s for s in rep['soft'])

    def test_external_links_are_flagged(self, tmp_path):
        p = _mini_xlsx(tmp_path / 'e.xlsx',
                       extra={'xl/externalLinks/externalLink1.xml': '<x/>'})
        rep = safety.inspect_workbook(p)
        assert rep['ok'] is True
        assert any('外部链接' in s for s in rep['soft'])

    def test_embeddings_are_flagged(self, tmp_path):
        p = _mini_xlsx(tmp_path / 'emb.xlsx',
                       extra={'xl/embeddings/oleObject1.bin': b'\x00' * 8})
        rep = safety.inspect_workbook(p)
        assert any('嵌入对象' in s for s in rep['soft'])

    def test_query_connections_are_flagged(self, tmp_path):
        p = _mini_xlsx(tmp_path / 'q.xlsx',
                       extra={'xl/connections.xml': '<connections/>'})
        rep = safety.inspect_workbook(p)
        assert any('外部数据连接' in s for s in rep['soft'])

    def test_soft_issues_leave_a_trail(self, tmp_path):
        p = _real_xlsx(tmp_path / 'm2.xlsx')
        _add_zip_entry(p, 'xl/vbaProject.bin')
        safeguard.clear()
        safety.open_workbook(p).close()
        assert safeguard.total() >= 1, safeguard.report()
        assert any('带宏' in (r.get('samples') or [''])[0]
                   for r in safeguard.records()), safeguard.report()


class TestFormulaCells:
    def test_formula_cells_are_counted(self, tmp_path):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws['A1'] = 1
        ws['A2'] = '=SUM(A1:A1)'
        ws['A3'] = 'text'
        assert safety.scan_sheet_formulas(ws) == 1

    def test_warn_formulas_leaves_a_trail(self):
        wb = openpyxl.Workbook()
        wb.active['A1'] = '=1+1'
        safeguard.clear()
        assert safety.warn_formulas(wb.active, purpose='import') == 1
        assert safeguard.total() >= 1


class TestDescribe:
    def test_describe_says_what_and_why(self, tmp_path):
        p = tmp_path / 'bad.xlsx'
        p.write_bytes(b'not a zip')
        text = safety.describe(safety.inspect_workbook(p))
        assert '拒绝打开' in text and '✗' in text

    def test_describe_handles_empty_report(self):
        assert '没有检查结果' in safety.describe({})


# ---------------------------------------------------------------------------
#  CLI：excel-check（读之前先体检）
# ---------------------------------------------------------------------------

class TestCliExcelCheck:
    def _bomb(self, tmp_path):
        return _mini_xlsx(tmp_path / 'bomb.xlsx', sheet_xml=(
            '<?xml version="1.0"?><worksheet>'
            '<dimension ref="A1:XFD1048576"/><sheetData/></worksheet>'))

    def test_clean_file_exits_zero(self, tmp_path):
        from storygraph.cli import main
        p = _real_xlsx(tmp_path / 'ok.xlsx')
        assert main(['excel-check', str(p)]) == 0

    def test_hard_problem_exits_one_and_names_the_file(self, tmp_path, capsys):
        from storygraph.cli import main
        p = self._bomb(tmp_path)
        assert main(['excel-check', str(p)]) == 1
        out = capsys.readouterr().out
        assert 'bomb.xlsx' in out and '打不开' in out

    def test_directory_scans_every_workbook(self, tmp_path, capsys):
        from storygraph.cli import main
        _real_xlsx(tmp_path / 'a.xlsx')
        self._bomb(tmp_path)
        assert main(['excel-check', str(tmp_path)]) == 1
        out = capsys.readouterr().out
        assert '检查了 2 份' in out

    def test_directory_without_workbooks_is_a_usage_error(self, tmp_path):
        from storygraph.cli import main
        assert main(['excel-check', str(tmp_path)]) == 2

    def test_missing_path_is_a_usage_error(self, tmp_path):
        from storygraph.cli import main
        assert main(['excel-check', str(tmp_path / 'nope.xlsx')]) == 2

    def test_json_output_is_machine_readable(self, tmp_path, capsys):
        from storygraph.cli import main
        p = self._bomb(tmp_path)
        assert main(['excel-check', str(p), '--json']) == 1
        data = json.loads(capsys.readouterr().out)
        assert data['checked'] == 1 and data['hard'][0]['name'] == 'bomb.xlsx'

    def test_soft_only_passes_unless_strict(self, tmp_path):
        from storygraph.cli import main
        p = _real_xlsx(tmp_path / 'm.xlsm')
        _add_zip_entry(p, 'xl/vbaProject.bin')
        assert main(['excel-check', str(p)]) == 0
        assert main(['excel-check', str(p), '--strict']) == 1


# ---------------------------------------------------------------------------
#  体检：不安全的表在 `validate` 里也要看得见
# ---------------------------------------------------------------------------

class TestValidateIntegration:
    def _project(self, tmp_path, *, with_bomb: bool):
        root = tmp_path / 'proj'
        game = root / 'game'
        (game / 'asset_tables').mkdir(parents=True)
        (game / 'ch01.rpy').write_text('label ch01:\n    return\n', encoding='utf-8')
        if with_bomb:
            _mini_xlsx(game / 'asset_tables' / 'bomb.xlsx', sheet_xml=(
                '<?xml version="1.0"?><worksheet>'
                '<dimension ref="A1:XFD1048576"/><sheetData/></worksheet>'))
        return root

    def test_unsafe_table_is_an_error(self, tmp_path):
        from storygraph.pipeline.validate import validate_workbook_safety
        root = self._project(tmp_path, with_bomb=True)
        issues = validate_workbook_safety(root)
        assert issues and issues[0].code == 'E307'
        assert issues[0].severity == 'error'
        assert 'bomb.xlsx' in issues[0].message

    def test_clean_project_is_silent(self, tmp_path):
        from storygraph.pipeline.validate import validate_workbook_safety
        root = self._project(tmp_path, with_bomb=False)
        assert validate_workbook_safety(root) == []

    def test_validate_all_carries_it_and_turns_red(self, tmp_path):
        from storygraph.pipeline.validate import validate_all
        root = self._project(tmp_path, with_bomb=True)
        report = validate_all([], project_root=root)
        assert not report.ok
        assert 'E307' in {i.code for i in report.issues}

    def test_macro_table_is_only_a_warning(self, tmp_path):
        from storygraph.pipeline.validate import validate_workbook_safety
        root = self._project(tmp_path, with_bomb=False)
        p = _real_xlsx(root / 'game' / 'asset_tables' / 'm.xlsm')
        _add_zip_entry(p, 'xl/vbaProject.bin')
        issues = validate_workbook_safety(root)
        assert issues and issues[0].code == 'W123'
        assert issues[0].severity == 'warning'


# ---------------------------------------------------------------------------
#  横扫：读 Excel 只能走 safety；写只能走 atomicio
# ---------------------------------------------------------------------------

_ALLOWED_READ = {'storygraph/excel/safety.py'}
_ALLOWED_WRITE_CALL = {'storygraph/core/atomicio.py',
                       'storygraph/story/xlsx_preserve.py'}


def _py_files():
    for p in sorted((ROOT / 'storygraph').rglob('*.py')):
        yield p.relative_to(ROOT).as_posix(), p


def _calls(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                base = fn.value
                name = f'{getattr(base, "id", "")}.{fn.attr}'
                out.append((name, node.lineno))
    return out


def test_only_safety_calls_load_workbook():
    """除 `excel/safety.py` 外，谁都不许直接调 `openpyxl.load_workbook`。"""
    bad = []
    for rel, path in _py_files():
        if rel in _ALLOWED_READ:
            continue
        src = path.read_text(encoding='utf-8')
        if 'load_workbook' not in src:
            continue
        for name, line in _calls(path):
            if name == 'openpyxl.load_workbook':
                bad.append(f'{rel}:{line}')
    assert not bad, ('这些地方绕过了安全检查（改用 storygraph.excel.safety.'
                     'open_workbook）：\n  ' + '\n  '.join(bad))


def test_no_direct_workbook_save_in_the_package():
    """Excel 保存只许走 `atomicio.save_workbook_atomic`。"""
    bad = []
    for rel, path in _py_files():
        if rel in _ALLOWED_WRITE_CALL:
            continue
        for name, line in _calls(path):
            if name in ('wb.save', 'workbook.save'):
                bad.append(f'{rel}:{line}')
    assert not bad, ('这些地方直接保存了工作簿（改用 atomicio.'
                     'save_workbook_atomic）：\n  ' + '\n  '.join(bad))
