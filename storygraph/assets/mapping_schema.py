# -*- coding: utf-8 -*-
"""
映射文件的**结构契约**：版本号、认识哪些键、以及"这份映射有什么问题"的检查。

为什么单独一个模块（2026-11-01）：这些是 `asset_mapping`（算法）与
`mapping_excel`（Excel 读写）**两边都要**的东西，而 Excel 那一侧从前
顶层 import `asset_mapping` —— 它俩之间还夹着 `asset_identity` 的环。
把契约抽成叶子之后，`mapping_excel → mapping_schema`、`mapping_excel →
identity_core`，谁也不指回算法层；`asset_mapping` 反过来再导出这些名字，
外面的 `from ...asset_mapping import MappingError` 一行都不用改。

**禁止**在这里 import `asset_mapping` / `mapping_excel` / 任何 UI 框架
（它是叶子：assets 层里最底下的一格）。
"""

from __future__ import annotations

import re
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.assets.identity_core import KINDS

#: 映射文件的结构版本。**改结构就 +1**（读取方据此判断能不能认）。
MAPPING_SCHEMA = 'storygraph.mapping/1'

#: 认识的顶层键（多出来的**警告不失败**：别的工具/未来版本加的键不该拦读）。
KNOWN_TOP_KEYS = ('schema', 'name', 'characters', 'rules', 'images',
                  'attributes', 'outfit_codes', 'expression_codes',
                  'vocabulary')

#: 代号两段（`sprite_codes`）+ 词汇表（`vocabulary`）里一条能有的键。
CODE_KEYS = {'outfit_codes': ('char', 'code', 'attr', 'label'),
             'expression_codes': ('char', 'no', 'attr', 'label'),
             'vocabulary': ('kind', 'id', 'label', 'code', 'char', 'note')}

#: `images` / `rules` 条目里认识的键（多出来同样只警告）。
KNOWN_ENTRY_KEYS = ('glob', 'kind', 'char', 'tag', 'attr', 'attrs',
                    'showable', 'display')


class MappingError(ValueError):
    """映射文件读不出来 / 不是合法 JSON / 顶层不是对象。"""


def xls_unsupported(path) -> str:
    """
    老式 `.xls` 的统一说法（两条读取路径共用同一句，别一处一个写法）。

    `.xls` 是 OLE 复合文档，openpyxl 读不了；而它**恰好**也是"二进制当文本读"
    会炸的东西 —— 不拦下来的话用户看到的是 `'utf-8' codec can't decode ...`，
    等于没说。
    """
    return (f'{Path(str(path or "")).name} 是老式 .xls 格式，读不了 —— '
            f'在 Excel / WPS 里「另存为」`.xlsx` 再从「载入映射…」选它。')


def validate_mapping(data, *, where=None) -> list:
    """
    映射结构检查 → **警告列表**（空 = 没问题）。纯函数、不改数据。

    为什么警告不失败：这份文件是**人写的、会随项目长大**；多一个 `notes`
    之类的键就拒绝加载，等于逼人删信息。但也不能静默 —— 认不出的键
    要报出来，否则"我写进去了怎么没生效"永远查不出。

    `where` 是**定位器**（可选）：`where(*path) -> '图片 表第 12 行'`。
    Excel 来源的映射传它，同一句警告就从 `images['x'] kind 不认识`
    变成 `图片 表第 12 行：kind 不认识` —— 人要能按这句话直接在表里找到那一行。
    """
    warnings = []

    def at(*path):
        """一句警告的前缀（`rules[0] ` / `规则 表第 9 行：`）。"""
        default = path[0] if len(path) == 1 else (
            f'{path[0]}[{path[1]!r}]' if len(path) > 1 else str(path[0]))
        if where is not None:
            try:
                got = str(where(*path) or '').strip()
            except Exception as e:            # noqa: BLE001 - 定位器坏了不该拦校验
                swallow('assets.mapping_schema.validate_mapping.where', e)
                got = ''
            if got:
                return got + '：'
        return default + ' '

    if not isinstance(data, dict):
        return ['映射顶层不是对象']
    for key in data:
        if key not in KNOWN_TOP_KEYS:
            warnings.append(f'未知顶层键 {key!r}（已忽略；认识的键：'
                            + '、'.join(KNOWN_TOP_KEYS) + '）')
    schema = data.get('schema')
    if schema is None:
        warnings.append(f'缺 schema 字段（本工具按 {MAPPING_SCHEMA} 读）')
    elif schema != MAPPING_SCHEMA:
        warnings.append(f'schema 不认识：{schema!r}（本工具只认 '
                        f'{MAPPING_SCHEMA}，仍尽量按同结构读）')
    chars = data.get('characters')
    if chars is not None and not isinstance(chars, dict):
        warnings.append('characters 不是对象（应为 {tag: {...}}）')
    elif isinstance(chars, dict):
        for tag, entry in chars.items():
            if not str(tag or '').strip():
                warnings.append('characters 里有空 tag')
            elif not isinstance(entry, dict):
                warnings.append(f'{at("characters", tag)}不是对象')
    rules = data.get('rules')
    if rules is not None and not isinstance(rules, list):
        warnings.append('rules 不是数组')
    elif isinstance(rules, list):
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                warnings.append(f'{at("rules", i)}不是对象')
                continue
            for key in rule:
                if key not in KNOWN_ENTRY_KEYS:
                    warnings.append(f'{at("rules", i)}未知键 {key!r}（已忽略）')
            glob = str(rule.get('glob') or '').strip()
            if not glob:
                warnings.append(f'{at("rules", i)}缺 glob（这条规则用不了）')
            elif _looks_outside_project(glob):
                warnings.append(
                    f'{at("rules", i)}glob 像是绝对路径：{glob!r}'
                    '（映射里的路径应写**项目相对**，否则换台机器就失效）')
            kind = str(rule.get('kind') or '').strip()
            if kind and kind not in KINDS:
                warnings.append(f'{at("rules", i)}kind 不认识：{kind!r}')
    images = data.get('images')
    if images is not None and not isinstance(images, dict):
        warnings.append('images 不是对象（应为 {asset_id 或相对路径: {...}}）')
    elif isinstance(images, dict):
        for key, entry in images.items():
            if not str(key or '').strip():
                warnings.append('images 里有空 key')
                continue
            if not isinstance(entry, dict):
                warnings.append(f'{at("images", key)}不是对象（简短写法不支持，'
                                '请写 {"kind": ...}）')
                continue
            for field in entry:
                if field not in KNOWN_ENTRY_KEYS:
                    warnings.append(f'{at("images", key)}未知键 {field!r}（已忽略）')
            kind = str(entry.get('kind') or '').strip()
            if kind and kind not in KINDS:
                warnings.append(f'{at("images", key)}kind 不认识：{kind!r}')
    for section, keys in CODE_KEYS.items():
        entries = data.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            warnings.append(f'{section} 不是数组（应为 [{{...}}, ...]）')
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                warnings.append(f'{at(section, i)}不是对象')
                continue
            for field in entry:
                if field not in keys:
                    warnings.append(f'{at(section, i)}未知键 {field!r}（已忽略）')
            if section == 'vocabulary':
                from storygraph.assets.vocabulary import is_kind, kind_label
                kind = str(entry.get('kind') or '').strip()
                if kind and not is_kind(kind):
                    warnings.append(f'{at(section, i)}种类不认识：{kind!r}'
                                    f'（认得的：' + '、'.join(
                                        kind_label(k) for k in
                                        ('transform', 'music', 'bg',
                                         'character', 'cue', 'costume'))
                                    + ' …）')
                if not str(entry.get('id') or '').strip():
                    warnings.append(f'{at(section, i)}缺 id（这一条对不上任何东西）')
    attributes = data.get('attributes')
    if attributes is not None and not isinstance(attributes, dict):
        warnings.append('attributes 不是对象（应为 {维度: {"label": ...}}）')
    elif isinstance(attributes, dict):
        for key, entry in attributes.items():
            if not isinstance(entry, dict):
                warnings.append(f'{at("attributes", key)}不是对象'
                                '（应为 {"label": ...}）')
                continue
            for field in entry:
                if field not in ('label', 'values', 'options'):
                    warnings.append(f'{at("attributes", key)}未知键 {field!r}（已忽略）')
    return warnings


def _looks_outside_project(text: str) -> bool:
    """这个写法是不是绝对路径（映射里应写项目相对路径）。"""
    value = str(text or '').strip().replace('\\', '/')
    return (value.startswith('/') or bool(re.match(r'^[A-Za-z]:', value))
            or ':/' in value)
