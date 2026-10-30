"""
storygraph.assets.mapping_excel
===============================
**资源映射的 Excel 定义途径**（`storygraph.mapping.xlsx`）。

用户 2026-10-30：

> 映射表除了 json 文件外，添加 excel 表格的定义途径（至于如何做，先想想……）

## 先想清楚的三件事

**① 谁说了算：同一目录里 `xlsx` 优先，而且每次都说出来**

`find_mapping_path` 在**每个目录里**按 `xlsx → json` 找，先后是「项目根」再
「`game/`」。为什么要有固定的先后，而不是"两份合并"或"谁新用谁"：

  · **合并**：两份文件都改过之后，没人说得清某张图到底是哪条规则给的
    （排错时只能靠猜，而这份文件就是"项目知识"的唯一载体）；
  · **按修改时间**：换台机器、`git checkout`、拷一份文件就会翻盘，
    "昨天看到的和今天不一样"永远查不出来；
  · **固定先后 + 界面每次都报当前用的是哪份**（状态栏 tooltip / 对话框状态行
    / 「重读」后的提示）才是可解释的；两份同时存在时还会多一条警告。

为什么 `xlsx` 排前面：它是**人手工维护**的那一面（真机 1720 张图，直接改
JSON 不现实）；JSON 是**分发 / 提交**那一面（可 diff、可 review、能单独发人）。
两份都在时以 xlsx 为准，是因为"我刚在 Excel 里改了怎么没生效"比
"我忘了删那份 Excel"更容易发生、也更难自查。

**② 表长什么样：一张表 = 一个顶层键**

| 表 | 顶层键 | 列 |
|---|---|---|
| 「映射」 | `schema` / `name` | 键 / 值 |
| 「角色」 | `characters` | tag / display / aliases / avatar / 备注 |
| 「规则」 | `rules` | glob / kind / char / attr / showable / display / 备注 |
| 「图片」 | `images` | key / kind / char / attr / showable / display / 备注 |
| 「属性」 | `attributes` | 维度 / label / 备注 |

  · **行序 = 数组顺序**：`rules` 是"先中先得"，Excel 里没有"数组下标"这种列，
    所以只能靠**行的上下位置**表达 —— 想调顺序就整行挪（这是 Excel 里最自然的
    操作，正好和 JSON 数组的顺序语义对上）；
  · 列名**中英文都认**（`tag` / `角色`、`kind` / `类型`……），少写 `schema` 也读；
  · 认不出的列**警告不失败**（和 JSON 的"未知键只警告"同一条纪律）；
  · `备注` / `说明` / `note` / `comment` 列是给人写字的，工具**刻意不看**
    （否则每写一句备注都多一条"未知键"警告，逼人不敢注释）；
  · 表头**不必在第 1 行**：前 10 行里找第一个认得出的表头，上面可以写标题。

**③ 写不写回：不写回（xlsx 只读）**

工具**只读**这份 Excel，从不改它。理由很硬：改一份人做的表 = `load_workbook`
→ `save` 一轮往返，而 openpyxl **会丢掉**它不认识的东西（图、图表、条件格式、
部分批注）。映射文件里如果夹着项目截图，静默丢图是不可接受的。
所以「保存到项目根」仍然写 JSON（原子写 + 旧文件 `.bak`），xlsx 由人在 Excel 里改。

`write_workbook` 只用于**新建**一份表：导出初稿 / 导出空白模板
（`save_mapping('…xlsx', data)` 会分派到这里，目标文件不存在或由我们整份重建）。
换句话说：**我们生成的表可以随便覆盖，人做的表一个字节都不碰。**

## 单元格约定（读的时候）

  · 空单元格 = **不写这个键**（不是空字符串）—— 这条最重要：`{"kind": ""}`
    和「没写 kind」在判定里是两件事；
  · 首尾空白一律去掉；
  · `别名`：`、` `,` `;` 换行 分隔（**不**按空格拆，"Wang Xiao" 是一个别名）；
  · `能否 show`：`是/能/可以/1/y/true` ↔ `否/不能/0/n/false`，认不出 → 警告 + 当没写；
  · 纯数字 id：Excel 会把 `13.2` 存成**数字**，这里格式化回文本 `'13.2'`
    （`13.0` → `'13'`；`007` 若被 Excel 存成数字，前导零已丢，读回来就是 `'7'`
    —— 想保住前导零就把那列设成"文本"格式再填）；
  · 公式单元格：用 `data_only=True` 读**缓存值**；从没在 Excel 里保存过的公式
    读出来是空 → 这一格当"没写"，并给一条人话警告（"用 Excel 打开保存一次"）。

## 判据

`tests/test_mapping_excel.py`（读 / 写 / 往返 / 优先级 / 定位警告）。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Mapping

from storygraph.assets.asset_identity import KINDS
from storygraph.assets.asset_mapping import MAPPING_SCHEMA, MappingError
from storygraph.core.safeguard import swallow

#: 项目里这份工作簿的名字（与 `MAPPING_FILENAME` 同一个模式：项目根或 `game/` 下）。
MAPPING_XLSX_FILENAME = 'storygraph.mapping.xlsx'

#: 认得的 Excel 后缀（`.xlsm` 只是带宏，读法一样；老式 `.xls` 读不了，见下）。
WORKBOOK_SUFFIXES = ('.xlsx', '.xlsm')

#: 「映射」表里认的键（其余行忽略）。
META_KEYS = {'schema': ('schema', '结构版本', '版本'),
             'name': ('name', '名称', '映射名', '标题')}

#: 一张表最多读这么多行（防"格式刷过整列"造成的百万行假维度）。
MAX_ROWS = 50000

#: 表头最多往下找这么多行（上面留给标题 / 说明）。
HEADER_SCAN = 10

#: 给人写字的列（工具刻意不看）。归一化之后比较。
NOTES_HEADERS = ('备注', '说明', '注释', 'note', 'notes', 'comment', 'comments',
                 'remark', 'remarks')

#: 工作表：键 → (规范表名, 认得的写法)。
SHEET_SPECS = (
    ('meta', '映射', ('映射', '映射信息', '信息', 'mapping', 'meta', 'info')),
    ('characters', '角色', ('角色', '角色表', '人物', '人物表',
                            'characters', 'character')),
    ('rules', '规则', ('规则', '规则表', '路径规则', 'rules', 'rule')),
    ('images', '图片', ('图片', '图片表', '单图', 'images', 'image')),
    ('attributes', '属性', ('属性', '属性表', 'attributes', 'attribute')),
)

#: 规范表名（写表 / 警告里用）。
SHEET_TITLES = {key: title for key, title, _aliases in SHEET_SPECS}

#: 每张表的列（字段 → 认得的表头写法）。**顺序即写表时的列顺序。**
FIELDS = {
    'characters': {
        'tag': ('tag', '角色', '角色名', '角色 tag', '角色编号', '编号', 'char'),
        'display': ('display', '显示名', '名字', '名称', '中文名'),
        'aliases': ('aliases', 'alias', '别名'),
        'avatar': ('avatar', '头像', '头像路径'),
    },
    'rules': {
        'glob': ('glob', 'pattern', '路径', '匹配路径', '路径规则', '路径模板'),
        'kind': ('kind', '类型', '类别', '资源类型'),
        'char': ('char', '角色', '角色 tag', 'tag'),
        'attr': ('attr', 'attrs', '属性', '表情'),
        'showable': ('showable', '能否 show', '能 show', '可否 show', 'show'),
        'display': ('display', '显示名', '名字'),
    },
    'images': {
        'key': ('key', 'asset id', '资产 id', '资产id', 'id', '资产', '图片',
                '路径', '文件', '图片路径'),
        'kind': ('kind', '类型', '类别', '资源类型'),
        'char': ('char', '角色', '角色 tag', 'tag'),
        'attr': ('attr', 'attrs', '属性', '表情'),
        'showable': ('showable', '能否 show', '能 show', '可否 show', 'show'),
        'display': ('display', '显示名', '名字'),
    },
    'attributes': {
        'key': ('key', '维度', 'dimension', 'name', '名字', '属性'),
        'label': ('label', '标签', '中文', '显示名', '中文名'),
    },
}

#: 每张表**必须有**的列（没有就跳过这张表并警告）。
KEY_FIELDS = {'characters': 'tag', 'rules': 'glob', 'images': 'key',
              'attributes': 'key'}

_TRUE = frozenset({'1', 'true', 'yes', 'y', 'on', '是', '能', '可以', '可',
                   '√', '✓', '对'})
_FALSE = frozenset({'0', 'false', 'no', 'n', 'off', '否', '不能', '不可以',
                    '不可', '×', '✗', 'x', '错', '不'})


# ---------------------------------------------------------------------------
#  小工具（纯函数）
# ---------------------------------------------------------------------------

def is_workbook(path) -> bool:
    """这个路径看起来是 Excel 工作簿（按后缀判，不看文件在不在）。"""
    return str(path or '').strip().lower().endswith(WORKBOOK_SUFFIXES)


def _norm(text) -> str:
    """
    表头 / 表名归一：去空格与常见分隔符、转小写。

    `资产 ID` / `asset id` / `Asset_ID` → `资产id` / `assetid` / `assetid`；
    这样中英文、有没有空格、大小写都能对上（人不会按我们想的写法敲）。
    """
    got = str(text if text is not None else '').strip().lower()
    got = re.sub(r'[\s_\-·.／/（）()\[\]「」<>]+', '', got)
    return got


def _text(value) -> str:
    """单元格 → 文本（数字按人能看懂的写法，`13.2` 不许变成 `13.199999`）。"""
    if value is None:
        return ''
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return format(value, '.15g')
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _blank(value) -> bool:
    """这一格算不算"空"（空单元格 / 空串 / 纯空白）。"""
    return _text(value) == ''


#: 归一化之后的"给人写字的列"（每格都不用再算一遍）。
_NOTES_NORM = frozenset(_norm(x) for x in NOTES_HEADERS)


def _as_bool(value):
    """`是否能 show` → `True / False / None`（认不出 = None，调用方给警告）。"""
    got = _norm(value)
    if not got:
        return None
    if got in _TRUE:
        return True
    if got in _FALSE:
        return False
    return None


def _split_list(value) -> list:
    """`别名` 一类的列表：顿号/逗号/分号/换行分隔，**不按空格拆**。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        words = [str(x) for x in value]
    else:
        words = re.split(r'[、,，;；\n\r]+', str(value))
    out = []
    for word in words:
        word = str(word or '').strip()
        if word and word not in out:
            out.append(word)
    return out


# ---------------------------------------------------------------------------
#  读
# ---------------------------------------------------------------------------

def _check_path(path) -> Path:
    """路径能不能当工作簿读（不存在 / 后缀不对 → `MappingError` 人话）。"""
    raw = str(path or '').strip()
    if not raw:
        raise MappingError('没有给出映射文件路径')
    p = Path(raw)
    if not p.is_file():
        raise MappingError(f'映射文件不存在：{p}')
    suffix = p.suffix.lower()
    if suffix == '.xls':
        from storygraph.assets.asset_mapping import xls_unsupported

        raise MappingError(xls_unsupported(p))
    if suffix not in WORKBOOK_SUFFIXES:
        raise MappingError(f'{p.name} 不是 Excel 工作簿（只认 .xlsx / .xlsm）')
    return p


def _open_workbook(p: Path):
    """打开工作簿（**只读流式**）；打不开 → `MappingError` 人话。"""
    try:
        import openpyxl
    except ImportError as e:                  # pragma: no cover - 依赖缺失环境
        raise MappingError(
            '读 Excel 映射需要 openpyxl（python -m pip install openpyxl）') from e
    try:
        return openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    except PermissionError as e:
        raise MappingError(
            f'打不开 {p.name}：这个文件可能正被 Excel / WPS 独占打开着。'
            f'关掉那个窗口再点「重读」。') from e
    except Exception as e:                    # noqa: BLE001 - 坏文件一律变人话
        raise MappingError(f'这个 Excel 文件读不出来：{p.name}（{e}）') from e


def _index_sheets(wb) -> dict:
    """工作簿 → `{键: 工作表 | None}`（表名中英文、带不带"表"字都认）。"""
    by_norm = {}
    for name in list(getattr(wb, 'sheetnames', ()) or ()):
        by_norm.setdefault(_norm(name), name)
    out = {}
    for key, _title, aliases in SHEET_SPECS:
        found = None
        for alias in aliases:
            real = by_norm.get(_norm(alias))
            if real:
                found = real
                break
        out[key] = wb[found] if found else None
    return out


def _rows(ws, warnings=None, label: str = '') -> list:
    """
    整张表读成 `[(单元格, …), …]`（只读流式；行数封顶 `MAX_ROWS`）。

    封顶**必须报出来**：静默只读前 5 万行 = "我的映射怎么少了一半"，
    正是本项目最不能接受的那类缺陷。
    """
    out = []
    try:
        for row in ws.iter_rows(values_only=True):
            out.append(tuple(row or ()))
            if len(out) >= MAX_ROWS:
                if warnings is not None:
                    warnings.append(
                        f'「{label}」表超过 {MAX_ROWS} 行，只读了前 {MAX_ROWS} 行 ——'
                        f'后面的没生效（映射表不该有这么多行，检查是不是'
                        f'格式刷拉出了空白行）')
                break
    except Exception as e:                    # noqa: BLE001 - 读到一半坏掉
        swallow('assets.mapping_excel._rows', e)
    return out


def _header_map(row, spec: dict) -> tuple:
    """表头一行 → `({字段: 列号}, [认不出的列文本])`。"""
    cols, unknown = {}, []
    for i, cell in enumerate(row or ()):
        name = _norm(cell)
        if not name:
            continue
        field = spec.get(name)
        if field is None:
            if name not in _NOTES_NORM:
                unknown.append(_text(cell))
            continue
        cols.setdefault(field, i)
    return cols, unknown


def _find_header(rows, key: str) -> tuple:
    """
    在前 `HEADER_SCAN` 行里找表头 → `(行号, {字段: 列}, 认不出的列)`。

    找不到返回 `(-1, {}, [])`：**不猜**（宁可说"这张表认不出"也不按列位硬套，
    列位一错就是整张表的 kind 全错，而且是静默的）。
    """
    spec = {_norm(alias): field
            for field, aliases in FIELDS[key].items() for alias in aliases}
    must = KEY_FIELDS[key]
    for idx, row in enumerate(rows[:HEADER_SCAN]):
        cols, unknown = _header_map(row, spec)
        if must in cols:
            return idx, cols, unknown
    return -1, {}, []


def _entries(rows, header_at, cols, key: str, warnings) -> list:
    """
    `[(行号, {字段: 原始值}), …]`（空行跳过；缺 key 的行给警告后跳过）。

    行号是**表里真实的行号**（1 起）—— 警告要指到人在 Excel 里看得到的那一行。
    """
    label = SHEET_TITLES[key]
    must = KEY_FIELDS[key]
    out = []
    for idx in range(header_at + 1, len(rows)):
        row = rows[idx]
        values = {}
        for field, col in cols.items():
            values[field] = row[col] if col < len(row) else None
        if all(_blank(v) for v in values.values()):
            continue                              # 整行空白：跳过，不吵
        line = idx + 1
        if _blank(values.get(must)):
            others = [f for f, v in values.items() if f != must and not _blank(v)]
            if others:
                warnings.append(
                    f'「{label}」表第 {line} 行：{must} 那格是空的，'
                    f'这一行用不了（已跳过）')
            continue
        out.append((line, values))
    return out


def _read_meta(ws, warnings) -> dict:
    """
    「映射」表 → `{'schema': …, 'name': …}`。

    两种写法都认：**第一列是键、第二列是值**（有没有表头行都行）——
    人写配置表就是这样，不必逼他先学会"表头必须是第 1 行"。
    """
    rows = _rows(ws, warnings, SHEET_TITLES['meta'])
    spec = {_norm(alias): field
            for field, aliases in META_KEYS.items() for alias in aliases}
    out = {}
    for row in rows:
        if not row or _blank(row[0]):
            continue
        name = spec.get(_norm(row[0]))
        if not name:                          # 表头「键」或人写的说明行：跳过
            continue
        out[name] = _text(row[1] if len(row) > 1 else '')
    schema = out.get('schema')
    if schema and schema != MAPPING_SCHEMA:
        warnings.append(f'「映射」表：schema 写的是 {schema!r}，本工具按 '
                        f'{MAPPING_SCHEMA} 读（结构不一样的地方可能读错）')
    return out


def _entry_of(values: dict, key: str, warnings, line: int, label: str) -> dict:
    """一行 `{字段: 原始值}` → 映射条目（**空的字段不写进去**）。"""
    entry = {}
    for field, raw in values.items():
        if field in ('key', 'tag', 'notes') or field not in _ENTRY_FIELDS[key]:
            continue
        if field == 'showable':
            flag = _as_bool(raw)
            if flag is None:
                if not _blank(raw):
                    warnings.append(
                        f'「{label}」表第 {line} 行：能否 show 写的是 {_text(raw)!r}，'
                        f'认不出（填 是 / 否）')
                continue
            entry['showable'] = flag
            continue
        text = _text(raw)
        if not text:                              # 空单元格 = 不写这个键
            continue
        if field == 'aliases':
            words = _split_list(text)
            if words:
                entry['aliases'] = words
            continue
        entry[field] = text
    return entry


#: 每张表里能进 JSON 条目的字段（`key`/`tag` 是标识，`notes` 给人写字）。
_ENTRY_FIELDS = {
    'characters': ('display', 'aliases', 'avatar'),
    'rules': ('kind', 'char', 'attr', 'showable', 'display'),
    'images': ('kind', 'char', 'attr', 'showable', 'display'),
    'attributes': ('label',),
}


def _read_characters(ws, warnings, located) -> dict:
    label = SHEET_TITLES['characters']
    rows = _rows(ws, warnings, label)
    header_at, cols, unknown = _find_header(rows, 'characters')
    if header_at < 0:
        _warn_no_header(rows, 'characters', warnings)
        return {}
    _warn_unknown_cols(unknown, label, header_at + 1, warnings)
    out = {}
    for line, values in _entries(rows, header_at, cols, 'characters', warnings):
        tag = _text(values.get('tag'))
        entry = _entry_of(values, 'characters', warnings, line, label)
        entry.pop('tag', None)
        if tag in out:
            warnings.append(f'「{label}」表第 {line} 行：tag {tag!r} 重复，'
                            f'后面的覆盖前面的')
        out[tag] = entry
        located[('characters', tag)] = f'「{label}」表第 {line} 行'
    return out


def _read_rules(ws, warnings, located) -> list:
    label = SHEET_TITLES['rules']
    rows = _rows(ws, warnings, label)
    header_at, cols, unknown = _find_header(rows, 'rules')
    if header_at < 0:
        _warn_no_header(rows, 'rules', warnings)
        return []
    _warn_unknown_cols(unknown, label, header_at + 1, warnings)
    out = []
    for line, values in _entries(rows, header_at, cols, 'rules', warnings):
        entry = _entry_of(values, 'rules', warnings, line, label)
        entry['glob'] = _text(values.get('glob'))
        # 行序 = 数组顺序（"先中先得"），JSON 的键顺序无所谓，这里保持列表。
        out.append(entry)
        located[('rules', len(out) - 1)] = f'「{label}」表第 {line} 行'
    return out


def _read_images(ws, warnings, located) -> dict:
    label = SHEET_TITLES['images']
    rows = _rows(ws, warnings, label)
    header_at, cols, unknown = _find_header(rows, 'images')
    if header_at < 0:
        _warn_no_header(rows, 'images', warnings)
        return {}
    _warn_unknown_cols(unknown, label, header_at + 1, warnings)
    out = {}
    seen = {}
    for line, values in _entries(rows, header_at, cols, 'images', warnings):
        asset_key = _text(values.get('key'))
        entry = _entry_of(values, 'images', warnings, line, label)
        entry.pop('key', None)
        if asset_key in out:
            warnings.append(
                f'「{label}」表第 {line} 行：key {asset_key!r} 重复'
                f'（第 {seen[asset_key]} 行已经有一条，后面的覆盖前面的）')
        out[asset_key] = entry
        seen[asset_key] = line
        located[('images', asset_key)] = f'「{label}」表第 {line} 行'
    return out


def _read_attributes(ws, warnings, located) -> dict:
    label = SHEET_TITLES['attributes']
    rows = _rows(ws, warnings, label)
    header_at, cols, unknown = _find_header(rows, 'attributes')
    if header_at < 0:
        _warn_no_header(rows, 'attributes', warnings)
        return {}
    _warn_unknown_cols(unknown, label, header_at + 1, warnings)
    out = {}
    for line, values in _entries(rows, header_at, cols, 'attributes', warnings):
        dim = _text(values.get('key'))
        entry = _entry_of(values, 'attributes', warnings, line, label)
        entry.pop('key', None)
        out[dim] = entry
        located[('attributes', dim)] = f'「{label}」表第 {line} 行'
    return out


def _warn_no_header(rows, key: str, warnings) -> None:
    """表头认不出 → 一句人话（**别静默**：人只会觉得"我的表怎么没用"）。"""
    if not rows or all(all(_blank(c) for c in row) for row in rows):
        return                                # 空表：不算错（模板就是这样）
    label = SHEET_TITLES[key]
    want = ' / '.join(FIELDS[key])
    warnings.append(f'「{label}」表：前 {HEADER_SCAN} 行里找不到表头'
                    f'（缺了必需的 {KEY_FIELDS[key]!r} 列？'
                    f'认得的列名：{want}）—— 这张表整个跳过')


def _warn_unknown_cols(unknown, label: str, header_line: int, warnings) -> None:
    if not unknown:
        return
    names = '、'.join(str(u) for u in unknown if str(u).strip())
    if names:
        warnings.append(f'「{label}」表第 {header_line} 行：列 {names} 不认识'
                        f'（已忽略；写错了字就会落到这里）')


def read_workbook(path) -> tuple:
    """
    读一份 Excel 映射 → `(data, warnings)`（与 `load_mapping` 同口径）。

    · 读不出 / 不是工作簿 / 四张表一张都认不出 → 抛 `MappingError`；
    · 未知列、重复 key、认不出的布尔值 → `warnings`（**带表名 + 行号**）；
    · 其余结构问题由 `asset_mapping.validate_mapping(data, where=…)` 统一判，
      同一句话在 JSON 里是 `images['x'] kind 不认识`，在这里就是
      `「图片」表第 12 行：kind 不认识`。
    """
    from storygraph.assets.asset_mapping import validate_mapping

    p = _check_path(path)
    wb = _open_workbook(p)
    warnings: list = []
    located: dict = {}
    try:
        sheets = _index_sheets(wb)
        if not any(sheets.get(k) for k in
                   ('characters', 'rules', 'images', 'attributes')):
            raise MappingError(
                f'{p.name} 里没有「角色 / 规则 / 图片 / 属性」任何一张表 —— '
                f'它看起来不是资源映射（认得的表名见 '
                f'「资源映射_给项目开发者.md」）。')
        meta = _read_meta(sheets['meta'], warnings) if sheets.get('meta') else {}
        data = {'schema': meta.get('schema') or MAPPING_SCHEMA}
        if meta.get('name'):
            data['name'] = meta['name']
        for key, reader in (('characters', _read_characters),
                            ('rules', _read_rules),
                            ('images', _read_images),
                            ('attributes', _read_attributes)):
            sheet = sheets.get(key)
            if sheet is not None:
                # **表在就出键**（哪怕是空的）：往返判据要能做到 `back == data`，
                # 而 `draft_mapping` / 空白模板里这四个键一直都在。
                data[key] = reader(sheet, warnings, located)
    finally:
        try:
            wb.close()
        except Exception as e:                # noqa: BLE001 - 关不掉不算错
            swallow('assets.mapping_excel.read_workbook.close', e)

    def where(*path_):
        return located.get(tuple(path_), '') or ''

    warnings.extend(validate_mapping(data, where=where))
    return data, warnings


# ---------------------------------------------------------------------------
#  写（只用于**新建**：导出初稿 / 空白模板）
# ---------------------------------------------------------------------------

def _safe_set(ws, row, col, value):
    """写一格（走 `excel.cells.set_text`：`=` 开头也不会被 Excel 当公式）。"""
    from storygraph.excel.cells import set_text

    try:
        return set_text(ws, row, col, value)
    except Exception as e:                    # noqa: BLE001 - 超长/怪字符退化成直写
        swallow('assets.mapping_excel._safe_set', e)
        cell = ws.cell(row=row, column=col)
        cell.value = value
        return cell


def _write_header(ws, fields: tuple) -> None:
    """表头：加粗 + 浅灰底 + 冻结（人一眼知道这行是标题，不是数据）。"""
    from openpyxl.styles import Font, PatternFill

    fill = PatternFill('solid', fgColor='FFEDEDED')
    for i, name in enumerate(fields, start=1):
        cell = _safe_set(ws, 1, i, name)
        try:
            cell.font = Font(bold=True)
            cell.fill = fill
        except Exception as e:                # noqa: BLE001 - 样式失败不影响内容
            swallow('assets.mapping_excel._write_header.style', e)
    try:
        ws.freeze_panes = 'A2'
    except Exception as e:                    # noqa: BLE001
        swallow('assets.mapping_excel._write_header.freeze', e)


def _write_rows(ws, fields: tuple, rows) -> int:
    """逐行写（`rows` = 已按列顺序排好的值列表）；返回写了多少行。"""
    n = 0
    for offset, values in enumerate(rows, start=2):
        for col, value in enumerate(values, start=1):
            if value is None or value == '':
                continue                      # 空格子就是"不写这个键"
            _safe_set(ws, offset, col, value)
        n += 1
    return n


def _set_widths(ws, fields: tuple) -> None:
    """列宽（窄列不挤、宽的路径给足；估的是"最长内容"，不是拍脑袋）。"""
    wide = {'glob': 46, 'key': 34, 'avatar': 34, 'display': 16, 'aliases': 22,
            'tag': 14, 'char': 12, 'kind': 11, 'attr': 16, 'showable': 10,
            'label': 16}
    for i, name in enumerate(fields, start=1):
        try:
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = \
                wide.get(name, 14)
        except Exception as e:                # noqa: BLE001
            swallow('assets.mapping_excel._set_widths', e)


def _add_kind_dropdown(ws, fields: tuple, last_row: int) -> None:
    """
    `kind` 列加下拉（`sprite / preview / …`）。

    为什么不 `showDropDown=True`：OOXML 里这个属性是**反的** ——
    写 1 等于"不显示下拉箭头"。所以这里**不设**它（默认就是显示）。
    """
    if 'kind' not in fields:
        return
    try:
        from openpyxl.worksheet.datavalidation import DataValidation

        col = fields.index('kind') + 1
        letter = ws.cell(row=1, column=col).column_letter
        dv = DataValidation(
            type='list', formula1='"' + ','.join(KINDS) + '"', allow_blank=True,
            showErrorMessage=True, errorTitle='kind 不认识',
            error='只能是：' + ' / '.join(KINDS))
        ws.add_data_validation(dv)
        dv.add(f'{letter}2:{letter}{max(last_row + 200, 1000)}')
    except Exception as e:                    # noqa: BLE001 - 没有下拉也能用
        swallow('assets.mapping_excel._add_kind_dropdown', e)


def _write_help(wb, counts: dict) -> None:
    """「说明」表：这张工作簿怎么用（随表分发时人第一眼看的就是它）。"""
    title = '说明'
    try:
        ws = wb.create_sheet(title)
    except Exception as e:                    # noqa: BLE001
        swallow('assets.mapping_excel._write_help', e)
        return
    lines = [
        ('这是什么', '资源映射的 Excel 版。工具打开项目时会自动读它'
                     '（项目根或 game/ 下的 storygraph.mapping.xlsx）。'),
        ('谁说了算', '同一个目录里 Excel 优先于 storygraph.mapping.json；'
                     '两份都在时工具用 Excel，并在「🗂 资源映射」里给一条提醒。'),
        ('改完怎么生效', '存盘 → 工具里点「🗂 资源映射 → 重读」（不用重启）。'),
        ('只有这几张表算了', '工具只读「映射」「角色」「规则」「图片」「属性」'
                            '五张表；别的表（封面 / 草稿 / 你的笔记）随便加，'
                            '工具不看也不改。'),
        ('规则顺序', '「规则」表从上到下就是匹配顺序（先中先得）；'
                     '想调顺序就整行挪。'),
        ('空单元格', '空 = 不写这个键（不是空字符串）。'
                     '不想限制某张图，就别填 kind，让工具自己推断。'),
        ('备注列', '「备注」是给人写字的，工具不看它，随便写。'),
        ('kind 取值', ' / '.join(KINDS)),
        ('写回', '工具只读这张表，从不改它；要提交 / 发人请用 '
                 '「保存到项目根」写 JSON（可 diff、可 review）。'),
        ('本次导出', '角色 {characters} 条 · 规则 {rules} 条 · 图片 {images} 条 · '
                     '属性 {attributes} 条'.format(**counts)),
    ]
    for i, (key, value) in enumerate(lines, start=1):
        cell = _safe_set(ws, i, 1, key)
        try:
            from openpyxl.styles import Font
            cell.font = Font(bold=True)
        except Exception as e:                # noqa: BLE001
            swallow('assets.mapping_excel._write_help.font', e)
        _safe_set(ws, i, 2, value)
    try:
        ws.column_dimensions['A'].width = 16
        ws.column_dimensions['B'].width = 96
    except Exception as e:                    # noqa: BLE001
        swallow('assets.mapping_excel._write_help.width', e)


def _cell_of(value):
    """JSON 值 → 单元格文本（`None` 不写；布尔/列表按人话写）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, (list, tuple)):
        return '、'.join(str(v) for v in value if str(v).strip())
    if isinstance(value, Mapping):
        import json

        return json.dumps(dict(value), ensure_ascii=False)
    return _text(value)


def _entry_cell(entry: Mapping, field: str):
    """
    条目里的一列 → 单元格。

    `char` / `tag` 与 `attr` / `attrs` 在判定里**是同一个东西**
    （`_identity_from_entry` 先看前者、没有再退回后者）。Excel 只有一列，
    所以这里也认别名 —— 否则一份用 `tag` 写的 JSON 转成 Excel 会**静默丢掉**
    角色（那是"导出成功、结果变错"的典型）。
    """
    if field == 'char':
        value = entry.get('char')
        return _cell_of(value if value is not None else entry.get('tag'))
    if field == 'attr':
        value = entry.get('attr')
        return _cell_of(value if value is not None else entry.get('attrs'))
    return _cell_of(entry.get(field))


def write_workbook(path, data) -> dict:
    """
    把一份映射写成工作簿（**新建**；已存在的文件先备份成 `.bak` 再整份替换）。

    返回写了多少行 `{'characters': n, 'rules': n, 'images': n, 'attributes': n}`
    —— 导出初稿后要说清楚"到底导出了什么"，别只给一句"成功"。

    ⚠ 这是"整份重建"，**不保留**原工作簿里的任何东西：只用于我们的产物
    （初稿 / 模板）。人做的表永远不经这条路覆盖 —— 见模块 docstring ③。
    """
    target = Path(str(path or '').strip())
    if not str(path or '').strip() or not target.name:
        raise MappingError('没有给出映射文件路径')
    if not isinstance(data, Mapping):
        raise MappingError('映射数据必须是 JSON 对象')
    if target.suffix.lower() not in WORKBOOK_SUFFIXES:
        raise MappingError(f'写 Excel 映射要用 .xlsx 后缀：{target.name}')
    try:
        import openpyxl
    except ImportError as e:                  # pragma: no cover - 依赖缺失环境
        raise MappingError(
            '写 Excel 映射需要 openpyxl（python -m pip install openpyxl）') from e
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise MappingError(f'建不出映射文件目录：{e}') from e

    payload = dict(data or {})
    counts = {key: len(payload.get(key) or ()) if isinstance(payload.get(key),
                                                            (list, dict)) else 0
              for key in ('characters', 'rules', 'images', 'attributes')}

    wb = openpyxl.Workbook()
    # ① 「映射」表（schema / name）
    meta_ws = wb.active
    meta_ws.title = SHEET_TITLES['meta']
    _write_header(meta_ws, ('键', '值'))
    meta_rows = [('schema', payload.get('schema') or MAPPING_SCHEMA),
                 ('name', payload.get('name') or '资源映射')]
    _write_rows(meta_ws, ('键', '值'), meta_rows)
    _set_widths(meta_ws, ('键', '值'))
    meta_ws.column_dimensions['A'].width = 12
    meta_ws.column_dimensions['B'].width = 48

    # ② 角色
    char_fields = ('tag', 'display', 'aliases', 'avatar')
    ws = wb.create_sheet(SHEET_TITLES['characters'])
    _write_header(ws, char_fields)
    chars = payload.get('characters') if isinstance(payload.get('characters'),
                                                    Mapping) else {}
    rows = []
    for tag, entry in chars.items():
        entry = entry if isinstance(entry, Mapping) else {}
        rows.append([_text(tag)] + [_cell_of(entry.get(f))
                                    for f in char_fields[1:]])
    counts['characters'] = _write_rows(ws, char_fields, rows)
    _set_widths(ws, char_fields)

    # ③ 规则（**行序 = 数组顺序**，不许排序）
    rule_fields = ('glob', 'kind', 'char', 'attr', 'showable', 'display')
    ws = wb.create_sheet(SHEET_TITLES['rules'])
    _write_header(ws, rule_fields)
    rules = [r for r in (payload.get('rules') or ()) if isinstance(r, Mapping)]
    rows = [[_entry_cell(r, f) for f in rule_fields] for r in rules]
    counts['rules'] = _write_rows(ws, rule_fields, rows)
    _set_widths(ws, rule_fields)
    _add_kind_dropdown(ws, rule_fields, counts['rules'] + 1)
    if rows:
        try:
            ws.auto_filter.ref = f'A1:F{len(rows) + 1}'
        except Exception as e:                # noqa: BLE001
            swallow('assets.mapping_excel.write_workbook.filter.rules', e)

    # ④ 图片（精确覆盖）
    img_fields = ('key', 'kind', 'char', 'attr', 'showable', 'display')
    ws = wb.create_sheet(SHEET_TITLES['images'])
    _write_header(ws, img_fields)
    images = payload.get('images') if isinstance(payload.get('images'),
                                                 Mapping) else {}
    rows = []
    for key, entry in images.items():
        entry = entry if isinstance(entry, Mapping) else {}
        rows.append([_text(key)] + [_entry_cell(entry, f)
                                    for f in img_fields[1:]])
    counts['images'] = _write_rows(ws, img_fields, rows)
    _set_widths(ws, img_fields)
    _add_kind_dropdown(ws, img_fields, counts['images'] + 1)
    if rows:
        try:
            ws.auto_filter.ref = f'A1:F{len(rows) + 1}'
        except Exception as e:                # noqa: BLE001
            swallow('assets.mapping_excel.write_workbook.filter.images', e)

    # ⑤ 属性
    attr_fields = ('维度', 'label')
    ws = wb.create_sheet(SHEET_TITLES['attributes'])
    _write_header(ws, attr_fields)
    attrs = payload.get('attributes') if isinstance(payload.get('attributes'),
                                                    Mapping) else {}
    rows = []
    for dim, entry in attrs.items():
        entry = entry if isinstance(entry, Mapping) else {}
        rows.append([_text(dim), _cell_of(entry.get('label'))])
    counts['attributes'] = _write_rows(ws, attr_fields, rows)
    _set_widths(ws, attr_fields)

    # ⑥ 说明（放最后：人打开先看到的是「映射」，但说明一直在）
    _write_help(wb, counts)

    # 写盘：旧文件先备份 → 临时文件 → os.replace（与 save_mapping 同一条纪律）
    if target.exists():
        backup = target.with_name(target.name + '.bak')
        try:
            shutil.copy2(target, backup)
        except OSError as e:
            raise MappingError(f'旧映射备份失败（未写，宁可没改）：{e}') from e
    tmp = target.with_name(target.name + '.tmp.xlsx')
    try:
        wb.save(str(tmp))
        os.replace(tmp, target)
    except OSError as e:
        _cleanup(tmp)
        if isinstance(e, PermissionError):
            raise MappingError(
                f'写不进 {target.name}：这个文件可能正被 Excel / WPS 打开着。'
                f'关掉那个窗口再试。') from e
        raise MappingError(f'写映射文件失败：{e}') from e
    except Exception as e:                    # noqa: BLE001 - openpyxl 自己的怪错
        _cleanup(tmp)
        raise MappingError(f'写映射文件失败：{e}') from e
    return counts


def _cleanup(tmp: Path) -> None:
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError as e:                      # noqa: BLE001 - 清理失败不掩盖真错误
        swallow('assets.mapping_excel._cleanup', e)


def blank_template() -> dict:
    """
    空白模板（只有表头、没有数据行）—— 「导出映射初稿」在没有资源表时也能给。

    `characters` / `rules` / `images` / `attributes` 全空，但四张表都在：
    人打开就能照着列名填。
    """
    return {'schema': MAPPING_SCHEMA, 'name': '资源映射',
            'characters': {}, 'rules': [], 'images': {}, 'attributes': {}}


__all__ = ['MAPPING_XLSX_FILENAME', 'WORKBOOK_SUFFIXES', 'SHEET_SPECS',
           'SHEET_TITLES', 'FIELDS', 'MAX_ROWS', 'HEADER_SCAN',
           'is_workbook', 'read_workbook', 'write_workbook', 'blank_template']
