"""
storygraph.story.table_meta
===========================
演出表自带的**来源信息**：这张表是从哪一份 `.rpy` 摊出来的。

为什么需要（2026-10-19，审计 P1-1「全量重排」）：

    rpy → xlsx → rpy 这条路上，用户不给 `--source` 时只能全量重生成。
    而全量重生成只认得"拍"：整份文件的空行、行尾空格、CRLF 布局都会变
    （真机 52 个文件里 30 个有差异；`definitions.rpy` 1120 → 1288 行）。
    这不是"算错"，是**没有底稿**——但它完全不必发生：表就是从那份
    `.rpy` 生出来的，路径记在表里，导回时就能自动走增量（没改的行
    逐字节保住），用户根本不需要知道 `--source` 这个参数。

为什么记在**隐藏表**里，而不是文档属性：

  · Excel "另存为"、被脚本重写（我们自己的刷新候选/写编译结果表）之后，
    文档属性不一定还在，而表里的行一定在；
  · 与「文件级保真」「超长文本」同一套办法，测试与排查都看得见。

**读不到就当没有**（老表 / 手工新建的表 / 文件被搬走）：调用方退回
全量，行为与从前完全一致。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 隐藏表名。与 `beat_model.FILE_BLOCK_SHEET` 同一档：工具维护的搬运工。
META_SHEET = '表信息'

#: 表里的两列（人能读懂，万一展开也不至于一头雾水）。
META_HEADER = ('项', '值')

#: 键名（也是给人看的行标题）。
KEY_SOURCE = '来源文件'
KEY_CREATED = '生成于'
KEY_TOOL = '工具'

TOOL_NAME = "Renpy_Story_Graph_Editor"


def write_meta(wb, *, source_file='', created: str = '') -> None:
    """
    往工作簿里写一张隐藏的「表信息」。

    `source_file` 为空（例如手工新建的表、协作库导出）时**不写这张表**：
    写一行空路径只会让"读不到来源"看起来像"来源是空的"。
    """
    if wb is None:
        return
    src = str(source_file or '').strip()
    if not src:
        return
    # 同一份工作簿里已经有这张表（例如写完后又被调用一次）→ 先删，避免重名
    try:
        if META_SHEET in wb.sheetnames:
            del wb[META_SHEET]
    except Exception as e:                     # noqa: BLE001 - 重建比报错好
        swallow('story.table_meta.write_meta.drop', e)
    ws = wb.create_sheet(META_SHEET)
    ws.append(list(META_HEADER))
    ws.append([KEY_SOURCE, src])
    ws.append([KEY_CREATED, created or datetime.now().isoformat(
        timespec='seconds')])
    ws.append([KEY_TOOL, TOOL_NAME])
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 90
    # 整张隐藏：它是工具维护的搬运工，不是给人编辑的界面。
    ws.sheet_state = 'hidden'


def _cell_text(value) -> str:
    return '' if value is None else str(value).strip()


def read_meta(table_path) -> dict:
    """
    读「表信息」→ `{'来源文件': …, '生成于': …, '工具': …}`。

    读不到（没有这张表 / 文件坏了 / 不是 xlsx）一律返回 `{}` ——
    这个模块只提供**增益**，不许因为它让导出失败。
    """
    table_path = Path(table_path)
    if not table_path.is_file():
        return {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(table_path, read_only=True,
                                    data_only=True)
    except Exception as e:                     # noqa: BLE001 - 见 docstring
        swallow('story.table_meta.read_meta.open', e)
        return {}
    try:
        if META_SHEET not in wb.sheetnames:
            return {}
        ws = wb[META_SHEET]
        out = {}
        for row in ws.iter_rows(values_only=True):
            if not row:
                continue
            key = _cell_text(row[0])
            if not key or (key == META_HEADER[0]
                           and _cell_text(row[1] if len(row) > 1 else '')
                           == META_HEADER[1]):
                continue                      # 表头那一行不算数据
            out[key] = _cell_text(row[1] if len(row) > 1 else '')
        return out
    finally:
        try:
            wb.close()
        except Exception as e:                 # noqa: BLE001
            swallow('story.table_meta.read_meta.close', e)


def source_of(table_path) -> 'Path | None':
    """
    表里记的来源 `.rpy` → **存在才算数**（不存在返回 None）。

    解析顺序（都在"文件真的存在"这一关上验过）：

      ① 记的原始路径（生成时写的是绝对路径）；
      ② 相对**表所在目录**（表与脚本一起被搬到别处 —— 项目整体迁移）；
      ③ 相对**项目根**（`<表>/../` 这种：表常在 `game/` 或工程根下）。

    只有 ① 命中时才算"可信"；②③ 是补救，同样要求命中真实文件。
    """
    src = read_meta(table_path).get(KEY_SOURCE, '')
    if not src:
        return None
    table_path = Path(table_path)
    raw = Path(src)
    cands = [raw]
    if not raw.is_absolute():
        cands = [table_path.parent / raw]
    else:
        # 项目整体搬家：先试"表目录 + 原路径最后两段"，再试文件名同级
        cands.append(table_path.parent / raw.name)
    for cand in cands:
        try:
            if cand.is_file():
                return cand
        except OSError:                        # noqa: BLE001
            continue
    return None


__all__ = ['META_SHEET', 'META_HEADER', 'KEY_SOURCE', 'KEY_CREATED',
           'KEY_TOOL', 'read_meta', 'source_of', 'write_meta']
