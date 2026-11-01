# -*- coding: utf-8 -*-
"""
storygraph.excel.safety
=======================
**打开任何工作簿之前，先问一句"这份东西安全吗"** —— 读 Excel 的唯一入口。

为什么要有（2026-11-01，第二轮外部审查 §5.5 的另一半）
-----------------------------------------------------
审查把"保护不对称"点得很准，但只说了**写**：19 处 `wb.save()` 是裸的。
这一轮补写盘（`core/atomicio.py`）之后，**读**那一头还全是 `openpyxl.load_workbook(path)` ——
而 xlsx 是一个 **zip**：里面装什么由写它的人决定。

于是有这些真实风险（都能用一份"看起来像表格"的文件做到）：

  · **解压炸弹**：1 MB 的 xlsx 解开是 4 GB 的 XML —— 打开就吃满内存，界面卡死；
  · **声明维度炸弹**：某个 sheet 的 `<dimension ref="A1:XFD1048576"/>` 一写，
    `ws.max_row` 就是 1048576 —— 任何 `for row in range(2, ws.max_row+1)` 的
    代码就地卡死（本项目的导入器正是这么写的）；
  · **宏**：`.xlsm` 里的 `vbaProject.bin`。本工具**不执行**任何宏，
    但"正在读一个带宏的表"这件事用户有权知道；
  · **外部链接 / 嵌入对象 / 查询连接**：表里的数据可能依赖别的文件或数据库，
    读回来的数字不是"这一份文件里的数字"；
  · **加密 / 老式 .xls**：openpyxl 根本读不了，报错却是一串英文栈。

所以这一层只做一件事：**在 `load_workbook` 之前把 zip 目录看一遍**（不解压，
只读 central directory + 每个 sheet 的前 4 KB），把"绝对不能开"和"开了要当心"
分开，并且**说人话**。

判据（`tests/test_excel_safety.py`）：炸弹、宏、外链、假后缀、声明维度
各造一份真的文件来试；另有一条 AST 判据扫全项目 —— **除本模块外，谁都不许
直接调 `openpyxl.load_workbook`**。

写盘那头不在这里：Excel 保存只走 `core.atomicio.save_workbook_atomic`
（后缀白名单只认 `.xlsx`）。读认 `.xlsx/.xlsm`，写只认 `.xlsx` —— 这是故意的。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 读得动的工作簿后缀（**写**只认 `.xlsx`，见 `core.atomicio.WORKBOOK_SUFFIXES`）。
READ_SUFFIXES = ('.xlsx', '.xlsm')

#: 单个工作簿的**压缩后**大小上限。表格不是视频：超过这个数基本可以断定不是表。
MAX_BYTES = 256 * 1024 * 1024

#: 解开之后的总大小上限（炸弹主要靠这条挡）。
MAX_UNCOMPRESSED = 512 * 1024 * 1024

#: 单个 zip 条目解开后的上限（一个 sheet 的 XML 不该有 256 MB）。
MAX_ENTRY_BYTES = 256 * 1024 * 1024

#: zip 条目数上限（防"几万个小文件"式的结构性炸弹）。
MAX_ENTRIES = 20000

#: 声明的格子数：超过 `HARD` 直接拒绝，超过 `WARN` 只提醒。
#: （一张 5 万行 × 40 列的真表 = 200 万格，所以提醒线不能低于它。）
MAX_DECLARED_CELLS = 8_000_000
WARN_DECLARED_CELLS = 2_000_000

#: 压缩比只作**提醒**，不作拒绝：重复度高的真表也能压到 200 倍以上。
WARN_RATIO = 200

#: 老式复合文档（OLE / 加密的 xlsx 也是它）的魔数。
_OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'

#: `<dimension ref="A1:XFD1048576"/>`（只扫每个 sheet 的前 4 KB）
_DIM_RE = re.compile(rb'<dimension[^>]*\bref="([^"]+)"')

#: `$A$1:$B$9` / `A1:B9` / `A1` 里的坐标
_CELL_RE = re.compile(r'\$?([A-Za-z]{1,3})\$?(\d{1,7})')


class UnsafeWorkbook(ValueError):
    """
    这份工作簿**不能打开**（不是"打不开"，是"打开会有代价/危险"）。

    继承 `ValueError`：调用方既有的 `except OSError`（权限/占用）不会被误触发；
    `except Exception` / `except ValueError` 能接住并翻成人话。
    """


#: 最近一次 `inspect_workbook` 的报告（CLI / 体检 / 排错用）
_LAST_REPORT: dict = {}

#: 检查结果的缓存：`(路径, 大小, mtime_ns) → 报告`。`read_meta` 这类热路径
#: 会反复打开同一张表，每读一次都扫一遍 zip 目录是白花钱。
_CACHE: 'dict[tuple, dict]' = {}
_CACHE_LIMIT = 128


def _col_index(letters: str) -> int:
    """`A` → 1、`AA` → 27、`XFD` → 16384。"""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord('A') + 1)
    return n


def _ref_cells(ref: str) -> int:
    """
    `A1:XFD1048576` → 格子数（**上界**）。认不出返回 0（不猜）。

    只取两个角：xlsx 的 dimension 永远写成左上:右下（或单格）。
    """
    hits = _CELL_RE.findall(ref or '')
    if not hits:
        return 0
    if len(hits) == 1:
        return 1
    (c1, r1), (c2, r2) = hits[0], hits[-1]
    cols = abs(_col_index(c2) - _col_index(c1)) + 1
    rows = abs(int(r2) - int(r1)) + 1
    return cols * rows


def _report(path: Path, *, hard: list, soft: list, info: dict) -> dict:
    rep = {'path': str(path), 'name': path.name, 'ok': not hard,
           'hard': list(hard), 'soft': list(soft), 'info': dict(info)}
    global _LAST_REPORT
    _LAST_REPORT = rep
    return rep


def _cache_key(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), int(st.st_size), int(st.st_mtime_ns))


def last_report() -> dict:
    """最近一次检查的报告（没检查过 → `{}`）。"""
    return dict(_LAST_REPORT)


def _inspect_uncached(path: Path) -> dict:
    hard, soft, info = [], [], {}

    suffix = path.suffix.lower()
    if suffix == '.xls':
        hard.append('这是老式 .xls 格式（openpyxl 读不了）。'
                    '用 Excel 另存为 .xlsx 再试 —— 工具不会替你转格式')
        return _report(path, hard=hard, soft=soft, info=info)
    if suffix not in READ_SUFFIXES:
        hard.append(f'不是 Excel 工作簿（只认 {" / ".join(READ_SUFFIXES)}）：'
                    f'{path.name}')
        return _report(path, hard=hard, soft=soft, info=info)

    try:
        st = path.stat()
    except OSError as e:
        hard.append(f'读不到文件：{e}')
        return _report(path, hard=hard, soft=soft, info=info)
    size = int(st.st_size)
    info['bytes'] = size
    if size == 0:
        hard.append('文件是 0 字节（空文件不是工作簿）')
        return _report(path, hard=hard, soft=soft, info=info)
    if size > MAX_BYTES:
        hard.append(f'文件 {size / 1024 / 1024:.0f} MB，超过上限 '
                    f'{MAX_BYTES / 1024 / 1024:.0f} MB —— 表格不会这么大'
                    '（是不是选错了文件？）')
        return _report(path, hard=hard, soft=soft, info=info)

    try:
        with open(path, 'rb') as fh:
            head = fh.read(8)
    except OSError as e:
        hard.append(f'读不到文件：{e}')
        return _report(path, hard=hard, soft=soft, info=info)
    if head.startswith(_OLE_MAGIC):
        hard.append('这是老式复合文档（加密的 .xlsx 或 .xls 改名）：'
                    '工具不解密、也不读这种格式。用 Excel 另存为未加密的 .xlsx')
        return _report(path, hard=hard, soft=soft, info=info)
    if not zipfile.is_zipfile(path):
        hard.append('文件不是 zip 结构 —— 它看起来不像 .xlsx（改过后缀？'
                    '或者文件坏了）')
        return _report(path, hard=hard, soft=soft, info=info)

    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        hard.append(f'打不开这个 zip（文件损坏？）：{e}')
        return _report(path, hard=hard, soft=soft, info=info)

    try:
        infos = zf.infolist()
        names = [i.filename for i in infos]
        info['entries'] = len(infos)
        if len(infos) > MAX_ENTRIES:
            hard.append(f'包里有 {len(infos)} 个条目，超过上限 {MAX_ENTRIES} —— '
                        '正常的表格只有几十个')
            return _report(path, hard=hard, soft=soft, info=info)

        total = 0
        biggest = ('', 0)
        for i in infos:
            total += int(i.file_size or 0)
            if int(i.file_size or 0) > biggest[1]:
                biggest = (i.filename, int(i.file_size or 0))
        info['uncompressed'] = total
        info['ratio'] = round(total / size, 1) if size else 0.0
        if total > MAX_UNCOMPRESSED:
            hard.append(f'解开之后有 {total / 1024 / 1024:.0f} MB，超过上限 '
                        f'{MAX_UNCOMPRESSED / 1024 / 1024:.0f} MB —— '
                        '这是解压炸弹的典型形态（压缩包很小、内容极大）')
            return _report(path, hard=hard, soft=soft, info=info)
        if biggest[1] > MAX_ENTRY_BYTES:
            hard.append(f'单个条目 {biggest[0]} 解开有 '
                        f'{biggest[1] / 1024 / 1024:.0f} MB —— 不正常')
            return _report(path, hard=hard, soft=soft, info=info)
        if info['ratio'] > WARN_RATIO and total > 8 * 1024 * 1024:
            soft.append(f'压缩比 {info["ratio"]:.0f} 倍（解开 '
                        f'{total / 1024 / 1024:.0f} MB）—— 内容重复度很高，'
                        '读起来会慢；如果不是故意的，确认一下这份表的来源')

        if '[Content_Types].xml' not in names or 'xl/workbook.xml' not in names:
            hard.append('zip 里没有 Excel 该有的结构（[Content_Types].xml / '
                        'xl/workbook.xml）—— 不是工作簿，只是碰巧是 zip')
            return _report(path, hard=hard, soft=soft, info=info)

        # —— 声明维度：**本项目的导入器按 `ws.max_row` 循环**，这一条能直接挡死
        declared = 0
        sheets = 0
        for name in names:
            if not (name.startswith('xl/worksheets/') and name.endswith('.xml')):
                continue
            sheets += 1
            try:
                with zf.open(name) as fh:
                    head_xml = fh.read(4096)
            except (OSError, zipfile.BadZipFile) as e:
                swallow('excel.safety.sheet_head', e)
                continue
            m = _DIM_RE.search(head_xml)
            if m:
                declared += _ref_cells(m.group(1).decode('ascii', 'replace'))
        info['sheets'] = sheets
        info['declared_cells'] = declared
        if declared > MAX_DECLARED_CELLS:
            hard.append(f'表头声明的范围有 {declared:,} 格（例如 A1:XFD1048576）—— '
                        '超过上限；打开它会让界面卡死')
            return _report(path, hard=hard, soft=soft, info=info)
        if declared > WARN_DECLARED_CELLS:
            soft.append(f'声明的范围有 {declared:,} 格，很大 —— '
                        '打开和重算都会慢')

        if 'xl/vbaProject.bin' in names:
            soft.append('这份工作簿**带宏**（.xlsm）。本工具只读单元格数据，'
                        '从不运行宏；但请确认这份表的来源')
        if any(n.startswith('xl/externalLinks/') for n in names):
            soft.append('表里有**外部链接**（指向别的工作簿）：'
                        '公式算出来的值可能不在这份文件里')
        if any(n.startswith('xl/embeddings/') for n in names):
            soft.append('表里有**嵌入对象**（可能是任意文件）—— 本工具不打开它们')
        if 'xl/connections.xml' in names or any(
                n.startswith('xl/queryTables/') for n in names):
            soft.append('表里有**外部数据连接**（数据库/网页查询）：'
                        '读回来的是上次保存的缓存值')
    finally:
        try:
            zf.close()
        except Exception as e:                # noqa: BLE001 - 关不掉不影响结论
            swallow('excel.safety.close', e)

    return _report(path, hard=hard, soft=soft, info=info)


def inspect_workbook(path) -> dict:
    """
    只看 zip 目录 + 每个 sheet 的头 4 KB，**不解压**，返回::

        {'path', 'name', 'ok', 'hard': [拒绝理由…], 'soft': [提醒…],
         'info': {'bytes','entries','uncompressed','ratio','sheets','declared_cells'}}

    `hard` 非空 = 不能打开；`soft` 非空 = 能打开但要当心。结果按
    `(路径, 大小, mtime_ns)` 缓存（同一张表反复读不重复扫）。
    """
    p = Path(str(path))
    key = _cache_key(p)
    if key is not None and key in _CACHE:
        rep = dict(_CACHE[key])
        global _LAST_REPORT
        _LAST_REPORT = rep
        return rep
    rep = _inspect_uncached(p)
    if key is not None:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[key] = dict(rep)
    return rep


def describe(report: dict) -> str:
    """检查报告 → 人能读的几行（CLI / 对话框用）。"""
    if not report:
        return '（没有检查结果）'
    info = report.get('info') or {}
    lines = [f'{report.get("name") or report.get("path")}：'
             f'{"可以打开" if report.get("ok") else "拒绝打开"}']
    size = info.get('bytes')
    if size:
        lines.append(f'  大小 {size / 1024:.0f} KB · 条目 {info.get("entries", "?")} · '
                     f'解开 {info.get("uncompressed", 0) / 1024 / 1024:.1f} MB · '
                     f'表 {info.get("sheets", "?")} 张')
    for msg in report.get('hard') or ():
        lines.append(f'  ✗ {msg}')
    for msg in report.get('soft') or ():
        lines.append(f'  ! {msg}')
    return '\n'.join(lines)


def open_workbook(path, *, purpose: str = '', read_only: bool = False,
                  data_only: bool = False, **kwargs):
    """
    **读 Excel 的唯一入口**：先 `inspect_workbook`，再 `openpyxl.load_workbook`。

    · `hard` 非空 → 抛 `UnsafeWorkbook`（消息就是 `describe` 那几句）；
    · `soft` 非空 → 经 `safeguard.swallow()` 留痕（`--debug` / 日志里看得见），
      **不拦**：带宏、带外链的表是真实存在的，用户有权读；
    · 其余异常原样抛（`PermissionError` 仍让调用方翻成"文件被 Excel 打开着"）。

    `purpose` 只是给留痕用的（`'assets'` / `'mapping'` / `'meta'` …）。
    """
    p = Path(str(path))
    report = inspect_workbook(p)
    if not report['ok']:
        raise UnsafeWorkbook(describe(report))
    for msg in report.get('soft') or ():
        swallow(f'excel.safety.{purpose or "read"}：{msg}', ValueError(msg))
    import openpyxl
    return openpyxl.load_workbook(str(p), read_only=read_only,
                                  data_only=data_only, **kwargs)


def scan_sheet_formulas(ws, *, limit: int = 200_000) -> int:
    """
    数一张表里有多少**公式**单元格（扫到 `limit` 格就停）。

    为什么要它：用 `data_only=False` 读回来的是公式文本（`=SUM(A1:A5)`），
    而剧本表/资源表里出现公式，意味着"这一格显示的不是你以为的那个值"。
    工具不会替你算 —— 但至少要说出来。
    """
    n = 0
    seen = 0
    try:
        for row in ws.iter_rows():
            for cell in row:
                seen += 1
                if seen > limit:
                    return n
                if getattr(cell, 'data_type', '') == 'f':
                    n += 1
    except Exception as e:                    # noqa: BLE001 - 数不动就算了
        swallow('excel.safety.scan_sheet_formulas', e)
    return n


def warn_formulas(ws, *, purpose: str = '') -> int:
    """有公式就留一笔痕，返回条数（调用方可以据此决定要不要提示用户）。"""
    n = scan_sheet_formulas(ws)
    if n:
        swallow(f'excel.safety.{purpose or "read"}：这张表有 {n} 个公式单元格',
                ValueError('读回来的是公式文本，不是计算结果'))
    return n


__all__ = ['MAX_BYTES', 'MAX_DECLARED_CELLS', 'MAX_ENTRIES', 'MAX_ENTRY_BYTES',
           'MAX_UNCOMPRESSED', 'READ_SUFFIXES', 'UnsafeWorkbook',
           'WARN_DECLARED_CELLS', 'WARN_RATIO', 'describe', 'inspect_workbook',
           'last_report', 'open_workbook', 'scan_sheet_formulas', 'warn_formulas']
