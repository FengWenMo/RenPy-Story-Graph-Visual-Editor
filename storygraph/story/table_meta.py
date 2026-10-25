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

⚠ **这里只记"来源文件"，不记"覆盖了哪些 label"**（2026-10-21 记一笔，
用户提的结构性隐患）：

  · 现在的做法是**读源文件现算** —— 哪些 label 属于这张表，以文件里
    真实存在的为准（实测撑得住：表删一行、源文件多一段新 label，
    两边各自都对）；
  · 别把它优化成"表里记着 label 集合，按集合拼产物"：源文件的 label
    **会被手工改名/搬走**，那时记录就成了假前提，而症状是"静默少了一章"。
  · 真要按集合走，前提是先做一次"记录的集合 vs 文件里真实集合"的对账，
    对不上就**说出来**（不许静默按旧的走）。
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
#: 摊表那一刻源 `.rpy` 的**内容指纹**（sha1，见 `fingerprint_of`）。
#:
#: 为什么必须记（2026-10-25 第六轮，P1「表过期只丢非台词行」）：
#:   "表比源码旧"从前只能靠**间接证据**猜 —— 补丁发现"源里有台词被删"才
#:   怀疑过期（2026-10-21 那条守卫）。而"只丢了 `$ flag = True` 这种行"
#:   时没有台词被删，守卫不响，于是无从判断"是表旧了"还是"用户清空了那格"。
#:   记下指纹之后，这件事有了**直接证据**：指纹对不上 = 源码在这张表之后
#:   被改过 → 那次删除很可能是丢别人的行，而不是用户有意清空。
KEY_SHA1 = '来源指纹'

TOOL_NAME = "Renpy_Story_Graph_Editor"


def fingerprint_of(path) -> str:
    """
    源文件的**内容指纹**：`sha1(原始字节)` 的前 16 位十六进制。

    为什么是字节而不是"规范化后的文本"：这张表将来可能被搬到别的机器、
    被 git 换行归一（`core.autocrlf`），而"源文件到底变没变"问的是
    盘上那份字节。指纹只用来判"变没变"，不参与任何还原 —— 所以
    CRLF/LF 差异导致的"变了"是**保守方向**（宁可多报一次"表旧了"）。
    """
    try:
        import hashlib
        data = Path(path).read_bytes()
    except OSError:
        return ''
    return hashlib.sha1(data).hexdigest()[:16]


def write_meta(wb, *, source_file='', created: str = '',
               source_sha1: str = '') -> None:
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
    if source_sha1:
        ws.append([KEY_SHA1, str(source_sha1)])
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
           'KEY_SHA1', 'KEY_TOOL', 'fingerprint_of', 'read_meta', 'source_of',
           'staleness_note', 'write_meta']


def staleness_note(table_path, source_path, removed_count: int = 0) -> str:
    """
    这张表**比源码旧**吗？旧就返回一句人话（否则空串）。

    只在"这次导出要删掉源里的行"时才说话（`removed_count > 0`）：
    没有删除就无所谓新旧 —— 说了也只是噪音。

    判据是**直接证据**（摊表时记下的内容指纹 vs 现在的字节），
    不是"补丁发现台词对不上"那种间接推理：
    所以它能把"表旧了、丢掉的是别人刚写的行"与"用户自己清空了这一格"
    分开说 —— 前者该重新载入，后者本来就是他的意思。
    """
    if int(removed_count or 0) <= 0:
        return ''
    if not source_path:
        return ''
    meta = read_meta(table_path)
    stamp = meta.get(KEY_SHA1, '')
    if not stamp:
        return ''                    # 老表（没有指纹）：不猜，交给回执的删行清单
    now = fingerprint_of(source_path)
    if not now or now == stamp:
        return ''
    return ('；**这张表比源码旧**（源码在摊表之后又被改过）：这次删掉的那几行'
            '很可能是别人刚写的，不是你清空的。先把这一章重新载入'
            '（表会拿到新行）再导出。')
