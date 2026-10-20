"""
storygraph.assets.asset_dimensions
==================================
立绘属性的**维度结构化**：`Group`（维度）+ `Value`（属性值）。

为什么要这个模块（用户 2026-10-19 排期第 1 项）
------------------------------------------------
资源表（`images.xlsx`）从前只有一格 `Attributes`，而且扫描器往里塞的是
**整个文件名**（`ling_happy` / `base_handdown`）。于是下游只能"拆词"：

  · `Symbols._attrs_by_tag` 按空白拆 → `base_arms_akimbo_sigh` 被当成一个词；
  · 立绘卡要按引擎组（`outfit` / `expressions`）分格，只能现场读引擎事实或
    `layeredimage`，读不到就退回"一个格子塞全部"；
  · 校验只能判"这个词在不在 tag 的候选里"，判不出"同一维度写了两个值"
    （`show lan happy sad` —— 引擎 lint 一声不吭）；
  · 多引擎导出只能给一串属性，给不出 `{outfit: base_swimsuit}`。

现在把**维度**写进表：一行 = 一张图层文件（或一条脚本声明），
`Group` 是引擎维度名（`outfit` / `expressions` / …），`Value` 是该维度下
的一个属性（`base_swimsuit` / `happy`）。`Attributes` 保留为**兼容列**：
老表、老代码、以及"一行登记多个属性"的脚本声明仍然能读。

三条刻意的设计
--------------
1. **不写死组名**。`outfit` / `expressions` 来自引擎事实或项目的
   `layeredimage`；认不出就留空，**不猜**（猜错一个维度就是演出事故）。
2. **`Group`/`Value` 只描述"单维度的一行"**。脚本声明的
   `image ling happy sad` 一行有多个属性，硬塞一个 `Value` 会丢词 ——
   这种行保持 `Attributes` 原样，由 `dimensions_of_row` 逐词展开。
3. **只填空白，不覆盖**。回填表格时只写空着的 `Group`/`Value` 格；
   人改过的值、`Attributes`、其它列一个字节都不动。

本模块只依赖 `core`；`layered_model` 是**延迟 import**（assets 在上、
story 在下会反向依赖，见 `tests/test_package_layering.py` 的
`LAZY_ALLOWED`）。
"""

from __future__ import annotations

import re
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 表里的规范键（Excel 表头见 `ASSET_FIELD_LABELS`）。
GROUP_FIELD = 'Group'
VALUE_FIELD = 'Value'

#: 值之间允许的分隔符。**刻意不拆 `_` / `-`**：`base_handdown` /
#: `base-arms-akimbo` 是一个属性名，拆开就不再是引擎里的那个词。
_VALUE_SEP = re.compile(r'[\s,，、;；|]+')


def split_values(raw) -> list:
    """
    `Attributes` 单元格 → 属性列表（保序去重）。

    ⚠ **不按 `_` 拆**。真实项目里 `base_arms_akimbo` / `close_happy` 是
    一个完整属性；拆成 `base` + `arms` + `akimbo` 会造出三个不存在的词。
    分词只在**显式分隔符**（空白 / 逗号 / 顿号 / 分号 / 竖线）上做。
    """
    text = str(raw or '').strip()
    if not text:
        return []
    out = []
    for word in _VALUE_SEP.split(text):
        word = word.strip()
        if word and word not in out:
            out.append(word)
    return out


def row_values(row: dict) -> list:
    """
    一行图片登记的全部属性值（保序去重）。

    `Value` 是结构化列，**优先**；但它是"一格一个值"，不能把一行里其它
    属性词吞掉（脚本声明行 `image ling happy sad` 一行有多个属性）。
    所以规则是：

      · 有 `Value` → 先收 `Value`；`Attributes` 若还有**多个**词，再并进来
        （单个词的 `Attributes` 视为老兼容镜像，不重复收）；
      · 没有 `Value` → 按 `Attributes` 拆；
      · 最后并入脚本声明的 `_attrs`。
    """
    row = row or {}
    value_tokens = split_values(row.get(VALUE_FIELD))
    attr_tokens = split_values(row.get('Attributes'))
    if value_tokens:
        values = list(value_tokens)
        if len(attr_tokens) > 1:
            for word in attr_tokens:
                if word not in values:
                    values.append(word)
    else:
        values = list(attr_tokens)
    for attr in (row.get('_attrs') or ()):
        word = str(attr or '').strip()
        if word and word not in values:
            values.append(word)
    return values


def row_group(row: dict) -> str:
    """这一行显式写的维度名（没有 → 空串）。"""
    return str((row or {}).get(GROUP_FIELD) or '').strip()


def _fact_group_map(facts: dict, tag: str) -> dict:
    """`{属性: 维度}` —— 引擎事实里的分组（同一属性多组时取第一组）。"""
    out: dict = {}
    if not facts or not tag:
        return out
    try:
        from storygraph.core import engine_facts as EF
        for entry in EF.attributes_of(facts, str(tag)):
            attr = str(entry.get('attribute') or '')
            group = str(entry.get('group') or '')
            if attr and group:
                out.setdefault(attr, group)
    except Exception as e:                        # noqa: BLE001 - 坏事实不该炸表
        swallow('assets.asset_dimensions.fact_group_map', e)
    return out


def _model_group_map(layer_model, tag: str) -> dict:
    """`{属性: 维度}` —— 项目 `layeredimage` 的 group（事实没有时的第二档）。"""
    out: dict = {}
    if layer_model is None or not tag:
        return out
    try:
        img = (getattr(layer_model, 'images', {}) or {}).get(str(tag))
        if img is None:
            return out
        for group in (getattr(img, 'groups', None) or ()):
            name = str(getattr(group, 'name', '') or '')
            for attr in (getattr(group, 'attrs', {}) or {}):
                if attr and name:
                    out.setdefault(str(attr), name)
    except Exception as e:                        # noqa: BLE001
        swallow('assets.asset_dimensions.model_group_map', e)
    return out


def group_map_for_tag(tag: str, facts=None, layer_model=None) -> dict:
    """
    某个 tag 的 `{属性: 维度}`：**引擎事实优先，项目 `layeredimage` 兜底**。

    两档都没有 → 空 dict（调用方据此保留"认不出"的空维度）。
    """
    mapping = _fact_group_map(facts or {}, str(tag))
    if mapping:
        return mapping
    return _model_group_map(layer_model, str(tag))


def _strip_tag_prefix(value: str, tag: str, known: dict) -> str:
    """
    `ling_happy` + tag `ling` → `happy`，**但只在能确认 `happy` 是一个属性时**。

    为什么需要：平铺布局 `images/eileen_happy.png` 的 `Attributes` 从前是
    整个文件名；不剥前缀，`show eileen happy` 永远对不上那一行的属性。
    为什么不能无脑剥：`dynamic_images/lan_angry.png` 的 tag 是 `lan`，
    属性其实叫 `lan_angry`（内部件）；剥成 `angry` 会把它当成表情，
    改变卡的候选与校验结果。所以判据是"剥完那一段在引擎/模型里真的存在"。
    """
    value = str(value or '').strip()
    tag = str(tag or '').strip()
    if not value or not tag:
        return value
    # **整体已知就原样保留**：`lan_angry` 在引擎里就是一个属性名，
    # 不能因为 tag 叫 `lan` 就把它拆成 `angry`（那是另一个表情）。
    if value in known:
        return value
    prefix = tag + '_'
    if not value.lower().startswith(prefix.lower()):
        return value
    rest = value[len(prefix):]
    if not rest:
        return value
    if rest in known:
        return rest
    return value


def _is_nested_under_tag(path, tag: str) -> bool:
    """
    文件是不是**放在以 tag 命名的目录里**（`images/characters/lan/x.png`）？

    这决定"要不要去 tag 前缀"：嵌套布局里文件名本身就是属性
    （`lan/angry.png` → `lan angry`），**不能**再剥；平铺布局
    （`images/eileen_happy.png`）才需要剥。`dynamic_images/lan_angry.png`
    正是嵌套那一类 —— 剥成 `angry` 会把它当成表情（另一个文件）。
    """
    tag = str(tag or '').strip().lower().replace('_', ' ')
    if not tag or not path:
        return False
    try:
        parts = [str(p).strip().lower().replace('_', ' ')
                 for p in Path(str(path)).parts[:-1]]
    except (TypeError, ValueError):
        return False
    return any(p == tag for p in parts)


def dimensions_of_row(row: dict, facts=None, layer_model=None,
                      strip_prefix: bool = True) -> list:
    """
    一行 → `[{'group': …, 'value': …}, …]`（保序去重）。

    · 显式 `Group` + 单值 → 用显式维度；
    · 否则按引擎事实 / `layeredimage` 查每个值的维度；
    · 查不到 → `group=''`（**认不出**，不是"顶层"）。
    · `strip_prefix=False` / 文件在 `<tag>/` 目录下 → **不去 tag 前缀**
      （嵌套布局的文件名就是属性名）。
    """
    row = row or {}
    tag = str(row.get('Tag') or '').strip()
    known = group_map_for_tag(tag, facts=facts, layer_model=layer_model)
    raw_values = row_values(row)
    explicit = row_group(row)
    # `Value` 列是**人/表写下的最终值**：不再做去 tag 前缀的推断
    # （推断只用于老 `Attributes` 里的整文件名 / 脚本声明的词）。
    protected = set(split_values(row.get(VALUE_FIELD)))
    allow_strip = bool(strip_prefix) and not _is_nested_under_tag(
        row.get('Path'), tag)
    out = []
    for value in raw_values:
        if allow_strip and value not in protected:
            value = _strip_tag_prefix(value, tag, known)
        group = explicit if (explicit and len(raw_values) == 1) else ''
        if not group:
            group = known.get(value, '')
        item = {'group': group, 'value': value}
        if item not in out:
            out.append(item)
    return out


def enrich_image_rows(rows, project_root=None, facts=None, layer_model=None,
                      load_project_context: bool = True) -> int:
    """
    给一批图片行补上 **`_dims` / `_attrs` / `_dim_groups`**，并在能确定时
    填空白的 `Group` / `Value`。返回补出维度的行数。

    `load_project_context=True` 时自动读项目根下的引擎事实与
    `layeredimage`；调用方（测试/已加载过）也可以直接传 `facts` /
    `layer_model`，避免重复读盘。

    ⚠ 只填**空白**：`Group` / `Value` 已经有人写过就不碰。
    ⚠ 多值行（脚本声明的 `image ling happy sad`）**不填** `Value`
    —— 一格只能放一个值，硬填就是丢词。
    """
    items = (list(rows.values()) if isinstance(rows, dict)
             else list(rows or ()))
    if not items:
        return 0
    if load_project_context and project_root:
        if facts is None:
            facts = load_project_facts(project_root)
        if layer_model is None:
            layer_model = load_layer_model(project_root)
    filled = 0
    for row in items:
        if not isinstance(row, dict):
            continue
        tag = str(row.get('Tag') or '').strip()
        dims = dimensions_of_row(row, facts=facts, layer_model=layer_model)
        if not dims:
            continue
        row['_dims'] = dims
        attrs = [d['value'] for d in dims if d.get('value')]
        if attrs:
            merged = list(row.get('_attrs') or ())
            for attr in attrs:
                if attr not in merged:
                    merged.append(attr)
            row['_attrs'] = merged
        groups = []
        for d in dims:
            g = str(d.get('group') or '')
            if g and g not in groups:
                groups.append(g)
        if groups:
            row['_dim_groups'] = groups
        # 只有"单值"才写结构化列：多值行一格放不下，留 Attributes 原样。
        if len(dims) == 1:
            value = str(dims[0].get('value') or '')
            group = str(dims[0].get('group') or '')
            if value and not str(row.get(VALUE_FIELD) or '').strip():
                row[VALUE_FIELD] = value
            if group and not str(row.get(GROUP_FIELD) or '').strip():
                row[GROUP_FIELD] = group
        if any(str(d.get('group') or '') for d in dims):
            filled += 1
    return filled


def load_layer_model(project_root):
    """延迟 import：assets 不能顶层依赖 story（分层守卫）。"""
    try:
        from storygraph.story.layered_model import load_layer_model_cached
        return load_layer_model_cached(project_root)
    except Exception as e:                        # noqa: BLE001 - 读不到就退回无维度
        swallow('assets.asset_dimensions.load_layer_model', e)
        return None


def load_project_facts(project_root) -> dict:
    """
    读项目根下的引擎事实（没有 / 不可用 → 空 dict）。

    ⚠ **不可用就当没有**：`is_collectable` 为假（parse error / 探针没跑完）
    时用它的分组会得到一半的真相，比不用更糟。
    """
    if not project_root:
        return {}
    try:
        from storygraph.core import engine_facts as EF
        path = EF.facts_path(project_root)
        if not Path(path).is_file():
            return {}
        data = EF.loads(Path(path).read_text(encoding='utf-8'))
        ok, _why = EF.is_collectable(data)
        return data if ok else {}
    except Exception as e:                        # noqa: BLE001 - 坏文件不该炸表
        swallow('assets.asset_dimensions.load_project_facts', e)
        return {}


def dimensions_by_tag(rows) -> dict:
    """
    图片行 → `{tag: [{'group','value'}, …]}`（保序去重）。

    行里已有 `_dims`（`parse_asset_tables` 补过）就直接用；否则按
    `Attributes` 现算（不读盘，认不出维度就留 `''`）。
    """
    out: dict = {}
    for row in (rows or ()):
        if not isinstance(row, dict):
            continue
        tag = str(row.get('Tag') or '').strip()
        if not tag:
            continue
        dims = row.get('_dims')
        if not dims:
            dims = dimensions_of_row(row)
        bucket = out.setdefault(tag, [])
        for d in (dims or ()):
            item = {'group': str(d.get('group') or ''),
                    'value': str(d.get('value') or '')}
            if item['value'] and item not in bucket:
                bucket.append(item)
    return {k: v for k, v in out.items() if v}


def grouped_dimensions(rows) -> dict:
    """
    图片行 → `{tag: {group: [value, …]}}`（只收**认得出**的维度）。

    这是界面"一维一格"与校验"同组不能选两个"共用的那一份结构。
    """
    out: dict = {}
    for tag, dims in dimensions_by_tag(rows).items():
        buckets: dict = {}
        for d in dims:
            group = str(d.get('group') or '')
            value = str(d.get('value') or '')
            if not group or not value:
                continue
            bucket = buckets.setdefault(group, [])
            if value not in bucket:
                bucket.append(value)
        if buckets:
            out[tag] = buckets
    return out


def same_group_conflicts(dims, used) -> list:
    """
    一组维度 + 实际写出来的属性 → `[{'group','attrs'}, …]`。

    与 `engine_facts.group_conflicts` **同一套语义**（同组同时选两个 = 冲突），
    只是数据源换成资源表里的结构化维度 —— 没采过引擎事实的项目也能查。
    """
    used_set = {str(a or '') for a in (used or ()) if str(a or '')}
    buckets: dict = {}
    for d in (dims or ()):
        group = str((d or {}).get('group') or '')
        value = str((d or {}).get('value') or '')
        if not group or not value:
            continue
        bucket = buckets.setdefault(group, [])
        if value not in bucket:
            bucket.append(value)
    out = []
    for group, members in buckets.items():
        hit = [a for a in members if a in used_set]
        if len(hit) >= 2:
            out.append({'group': group, 'attrs': hit})
    return out


def format_dimensions(dims) -> str:
    """`[{'group':'outfit','value':'base_swimsuit'}]` → `outfit=base_swimsuit`。"""
    parts = []
    for d in (dims or ()):
        group = str((d or {}).get('group') or '')
        value = str((d or {}).get('value') or '')
        if not value:
            continue
        parts.append(f'{group}={value}' if group else value)
    return '；'.join(parts)


__all__ = [
    'GROUP_FIELD', 'VALUE_FIELD',
    'split_values', 'row_values', 'row_group',
    'group_map_for_tag', 'dimensions_of_row', 'enrich_image_rows',
    'load_project_facts', 'load_layer_model', 'dimensions_by_tag',
    'grouped_dimensions', 'same_group_conflicts', 'format_dimensions',
]
