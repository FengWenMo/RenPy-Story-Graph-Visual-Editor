"""
资源表的两条守卫（2026-11-02 压测换来的）
=========================================

背景：用户提"资源管理器还不够健壮"。压测（`tools/_probe_asset_robustness_20261102.py`）
把表弄脏之后发现两条**真会毁数据**的：

**① 「真实文件」列被删 / 改名 → 补登把同一批文件再加一遍**

    实测：2 行的表变成 4 行（`bg01`/`BG02` 之外多出 `bg01_2`/`BG02_2`），
    人填在旧行里的 `Tag` / `Category` / `备注` 留在旧行 —— 一份整理成果被
    劈成两半，而 `sync_asset_tables` 返回 `ok=True`、一个字都不提示。

    为什么"表头坏了"与"磁盘上每个文件都没登记"长得一样：补登判重靠 `Path`，
    列没了 → 每行的 `Path` 都空 → 所有文件都被判成"未登记"。
    **判据**：某张资源表*有行、但一行都没写 Path* → 拒绝补登 + 说出是哪张表。
    （按表分开判 —— 图片表坏了不该被"音乐表好好的"放行。）

**② 编号是数字 → 注册表的键混进 int**

    Excel 会把用户手打的 `001` 存成 `1`（前导零当场消失），于是 `show 001`
    解析不到、`get_asset_ids()` 返回的列表混着 `123` 与 `'abc'`（排序直接
    TypeError）。**判据**：键一律 `str`，并且"这一格不是文本"要进 warnings。

对照组（这些**本来**就该放行 / 该报，别改坏）：空表照样补登、部分行没 Path
照样补登、重复 ID 与缺 ID 仍然报 error。
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.assets.asset_registry import (ensure_asset_tables,  # noqa: E402
                                              find_asset_tables,
                                              is_real_table_file,
                                              parse_asset_tables,
                                              sync_asset_tables)

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/'
    'P5AgAAAAAElFTkSuQmCC')

PATH_HEADERS = ('真实文件', 'Path')
#: 表头别名（工具两套都认：中文标签 / 英文键）。
_HEADER_ALIASES = {
    'Path': ('Path', '真实文件'),
    '真实文件': ('真实文件', 'Path'),
    'ID': ('ID', '编号'),
    '编号': ('编号', 'ID'),
}
#: 表头别名（工具两套都认：中文标签 / 英文键）。
_HEADER_ALIASES = {
    'Path': ('Path', '真实文件'),
    '真实文件': ('真实文件', 'Path'),
    'ID': ('ID', '编号'),
    '编号': ('编号', 'ID'),
}


@pytest.fixture()
def project(tmp_path):
    """最小 renpy 项目：两张 bg 图 + 一个 bgm，并建好资源表。"""
    (tmp_path / 'game' / 'images' / 'bg').mkdir(parents=True)
    (tmp_path / 'game' / 'audio' / 'bgm').mkdir(parents=True)
    (tmp_path / 'game' / 'images' / 'bg' / 'bg01.png').write_bytes(PNG)
    (tmp_path / 'game' / 'images' / 'bg' / 'BG02.png').write_bytes(PNG)
    (tmp_path / 'game' / 'audio' / 'bgm' / 'theme.ogg').write_bytes(b'OggS')
    (tmp_path / 'game' / 'script.rpy').write_text(
        'label start:\n    "hi"\n    return\n', encoding='utf-8')
    ensure_asset_tables(tmp_path)
    return tmp_path


def _images_table(root: Path) -> Path:
    d = Path(find_asset_tables(root))
    return next(p for p in d.glob('*.xlsx')
                if is_real_table_file(p) and p.stem == 'images')


def _read(path: Path):
    wb = openpyxl.load_workbook(path, data_only=False)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    rows = [list(r) for r in ws.iter_rows(min_row=2, values_only=True)]
    wb.close()
    return headers, rows


def _write(path: Path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)
    wb.close()


def _drop_column(path: Path, name: str):
    """删掉一列。`name` 可以是英文键，也可以是中文表头（两套都认）。"""
    headers, rows = _read(path)
    for cand in _HEADER_ALIASES.get(name, (name,)):
        if cand in headers:
            idx = headers.index(cand)
            break
    else:
        raise KeyError(f'{path.name} 里没有列 {name}（表头={headers}）')
    heads = [h for i, h in enumerate(headers) if i != idx]
    new_rows = [[v for i, v in enumerate(r) if i != idx] for r in rows]
    _write(path, heads, new_rows)


class TestMissingPathColumnIsRefused:
    def test_sync_does_not_append_duplicates(self, project):
        """核心判据：表头缺「真实文件」→ 一行都不许补。"""
        table = _images_table(project)
        before = _read(table)
        _drop_column(table, '真实文件')

        rep = sync_asset_tables(project)

        after = _read(table)
        assert len(after[1]) == len(before[1]), \
            f'表被撑大了：{len(before[1])} → {len(after[1])} 行'
        assert rep.get('added') == 0
        assert rep.get('ok') is True        # 表没坏、工具没坏

    def test_the_reason_names_the_broken_table(self, project):
        table = _images_table(project)
        _drop_column(table, '真实文件')
        rep = sync_asset_tables(project)
        why = rep.get('error') or ''
        assert 'images' in why, why          # 点名是哪张表
        assert '真实文件' in why, why         # 说清缺的是什么
        assert '没有补登' in why, why         # 说清这次做了什么

    def test_english_header_also_counts(self, project):
        """表头是英文 `Path` 时删掉同样要拦（别名不许漏）。"""
        table = _images_table(project)
        _drop_column(table, 'Path')
        rep = sync_asset_tables(project)
        assert rep.get('added') == 0


class TestWhatMustStillBeAllowed:
    """对照组：别为了拦坏表把正常流程也拦住。"""

    def test_empty_table_is_still_filled(self, project):
        """空表（刚建出来 / 人清空了）→ 补登是**对的**，必须放行。"""
        table = _images_table(project)
        headers, _rows = _read(table)
        _write(table, headers, [])           # 只留表头

        rep = sync_asset_tables(project)

        assert rep.get('added', 0) >= 1
        assert not rep.get('error')

    def test_rows_without_path_alone_do_not_block(self, project):
        """部分行没填 Path（新加的行常常如此）不算表头坏了。"""
        table = _images_table(project)
        headers, rows = _read(table)
        idx = next(i for i, h in enumerate(headers) if h in PATH_HEADERS)
        rows.append(['handmade', ''] + [None] * (len(headers) - 2))
        _write(table, headers, rows)

        rep = sync_asset_tables(project)

        assert not rep.get('error'), rep.get('error')
        # ⚠ 这里**不**断言"补了 N 行"：`bg02.png` 与表里的 `BG02` 在
        # Windows 上是同一个文件，补登判重会认为它已登记（对的）。
        # 本条要钉的只是"部分行没 Path 不等于表头坏了"。

    def test_a_healthy_table_is_left_alone(self, project):
        rep = sync_asset_tables(project)
        assert rep.get('added') == 0
        assert rep.get('removed') == 0
        assert not rep.get('error')


class TestNumericIdIsNormalised:
    def _rows(self, project):
        reg = parse_asset_tables(
            Path(find_asset_tables(project)),
            project_root=project)
        return reg

    def test_numeric_id_becomes_a_string_key(self, project):
        table = _images_table(project)
        headers, rows = _read(table)
        idx = next(i for i, h in enumerate(headers) if h == '编号')
        rows[0][idx] = 123
        _write(table, headers, rows)

        reg = self._rows(project)

        assert 123 not in reg.images
        assert '123' in reg.images

    def test_it_says_the_cell_is_not_text(self, project):
        table = _images_table(project)
        headers, rows = _read(table)
        idx = next(i for i, h in enumerate(headers) if h == '编号')
        rows[0][idx] = 123
        _write(table, headers, rows)

        reg = self._rows(project)

        joined = ' | '.join(reg.warnings)
        assert '123' in joined, reg.warnings
        assert '数字' in joined, reg.warnings
        assert '001' in joined, reg.warnings     # 提醒前导零会消失

    def test_text_ids_do_not_produce_warnings(self, project):
        reg = self._rows(project)
        assert not [w for w in reg.warnings if '数字' in w]
