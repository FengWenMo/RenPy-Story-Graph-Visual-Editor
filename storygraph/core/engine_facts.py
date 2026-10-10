"""
`storygraph.core.engine_facts`
==============================
**引擎事实**的纯数据层：结构（schema）、校验、查询。

为什么要有这一层（2026-10-10）
-----------------------------
引擎（Ren'Py）自己知道很多东西：有哪些图、每个 tag 有哪些属性、属性分在哪几组、
哪些属性互相兼容、有多少 transform / screen / label。
**这些不该让人手填** —— 让人填既累又必然会写错（真机上编剧抱怨过"候选值要一个个填"）。

采集这些事实的**动作**在 `pipeline.engine_collect`（要跑子进程、要写盘）。
本模块只放**纯逻辑**：结构长什么样、怎么校验、怎么查。
这样 `story` / `excel` / `app` 各层都能用，而不必依赖 pipeline（契约 H1：依赖单向）。

三条不容违反的规矩
------------------
1. **每条事实都必须带 `source`** —— 谁给的（探针 / 官方 dump / 本项目扫描器）。
   没有来源的事实一律**校验不过**：查不出来源的数字，就没法判"引擎变了没有"。
2. **两个来源不一致时不许静默挑一个** —— 记进 `conflicts` 并降级标记
   （`source == 'conflict'`）。对魔改过的引擎，"来源打架"本身就是最重要的信号。
3. **采不到 ≠ 没有变化** —— `is_collectable()` 是硬闸门：
   `parse_clean` 或 `probe_complete` 为假时，**整份事实不可用于判定**。
   （实测：一个 parse error 会让探针也跑不到，`facts` 直接不产出。）
"""

from __future__ import annotations

import json
from pathlib import Path

#: 事实文件的结构版本。**改结构就 +1**（读取方据此判断能不能认）。
SCHEMA_VERSION = 1

#: 事实文件的名字（放在**项目根**，与 `storygraph.rules.json` 同级）。
FACTS_FILENAME = 'storygraph.engine_facts.json'

# ---- 来源标记（`facts.<key>.source` 的取值） --------------------------------
#: 注入探针：在引擎 `init` 期间运行，能拿到"运行期事实"（图像属性/分组/label）。
SRC_PROBE = 'probe'
#: 官方 `--json-dump`：引擎自己导出的**静态**清单（transform / screen / define）。
SRC_JSON_DUMP = 'json-dump'
#: 本项目自己的扫描器（**不是引擎给的**，所以单独标出来）。
SRC_SCANNER = 'scanner'
#: 两个以上来源打架 —— 值不可信，必须人看一眼。
SRC_CONFLICT = 'conflict'

#: 已知的事实键（校验只认这些；多出来的键**报出来**，不静默忽略）。
FACT_KEYS = (
    'images', 'tags', 'attributes', 'candidates',
    'transforms', 'screens', 'defines', 'labels', 'builtins',
)

#: `derived` 段（**工具自己算的**，与"引擎给的"分开，避免混为一谈）。
DERIVED_KEYS = (
    'overridden_builtins', 'group_conflicts', 'undeclared_heads', 'scan_disclaimer',
)


# ---------------------------------------------------------------------------
#  结构与校验
# ---------------------------------------------------------------------------

def empty_facts() -> dict:
    """一份空的事实骨架（采集失败时也要写出来 —— 让"没采到"可见）。"""
    return {
        'schema': SCHEMA_VERSION,
        'engine': {},
        'fingerprint': {},
        'collection': {
            'parse_clean': False,
            'probe_complete': False,
            'lint_exit': None,
        },
        'lint': {'parse_errors': [], 'semantic_errors': [], 'program_errors': []},
        'facts': {},
        'derived': {},
        'conflicts': [],
    }


def facts_path(project_root) -> Path:
    """事实文件的路径（项目根下）。"""
    return Path(project_root) / FACTS_FILENAME


def _entry_value(entry):
    """取一条事实的**值**（两条历史形状都认：`{'value','source'}` 或裸值）。"""
    if isinstance(entry, dict) and 'value' in entry:
        return entry['value']
    return entry


def value_of(facts: dict, key: str, default=None):
    """`facts['facts'][key]` 的值（认不出结构就返回 `default`）。"""
    entry = ((facts or {}).get('facts') or {}).get(key)
    if entry is None:
        return default
    return _entry_value(entry)


def source_of(facts: dict, key: str) -> str:
    """`facts['facts'][key]` 的来源标记（没有 → 空串）。"""
    entry = ((facts or {}).get('facts') or {}).get(key)
    if isinstance(entry, dict):
        return str(entry.get('source') or '')
    return ''


def validate_facts(facts) -> list:
    """
    校验一份事实 → **问题清单**（空列表 = 通过）。纯函数。

    刻意做**结构性**校验（不猜内容）：
      · 不是 dict / 缺 schema / schema 版本不认识；
      · `facts` 段里出现**未知键**（多出来的东西要报，不能悄悄留着）；
      · **任何一条事实没有 `source`** —— 这条最重要：没有来源就无法判断"引擎变了没有"；
      · `collection` 段缺 `parse_clean` / `probe_complete`（采不到就没法用它判定）。
    """
    problems = []
    if not isinstance(facts, dict):
        return [f'事实不是 dict：{type(facts).__name__}']

    got = facts.get('schema')
    if got is None:
        problems.append('缺 schema 字段')
    elif got != SCHEMA_VERSION:
        problems.append(f'schema 版本不认识：{got!r}（本工具只认 {SCHEMA_VERSION}）')

    facts_sec = facts.get('facts')
    if not isinstance(facts_sec, dict):
        problems.append('缺 facts 段（或不是 dict）')
    else:
        for key, entry in facts_sec.items():
            if key not in FACT_KEYS:
                problems.append(f'facts 里有未知键：{key!r}（不静默忽略，请登记）')
                continue
            if not isinstance(entry, dict):
                problems.append(f'facts.{key} 不是 {{value, source}} 结构')
                continue
            if 'value' not in entry:
                problems.append(f'facts.{key} 缺 value')
            src = entry.get('source')
            if not src:
                problems.append(f'facts.{key} 缺 source —— 没有来源就无法判断引擎变没变')

    coll = facts.get('collection')
    if not isinstance(coll, dict):
        problems.append('缺 collection 段（无法判断"采到没有"）')
    else:
        for k in ('parse_clean', 'probe_complete'):
            if k not in coll:
                problems.append(f'collection 缺 {k}')

    return problems


def is_collectable(facts) -> tuple:
    """
    `(能不能用, 原因)` —— **硬闸门**（坑 2 / 坑 4）。

    为什么是硬闸门：实测过两次 ——
      · 只要有一个 **parse error**，引擎后面**全部**语义检查都不跑；
      · **探针也拿不到事实**（`facts` 直接不产出）。
    所以"没采到"必须报成**失败**，绝不能被当成"没有漂移"。
    """
    coll = (facts or {}).get('collection') or {}
    if not coll.get('parse_clean'):
        return False, ('工程未通过语法检查（有 parse error）：'
                       '此时引擎的语义检查与探针**都不会跑**，事实不可用')
    if not coll.get('probe_complete'):
        return False, '探针未产出事实（init 没跑完）：事实不可用'
    problems = validate_facts(facts)
    if problems:
        return False, '事实结构不合格：' + '；'.join(problems[:3])
    return True, ''


# ---------------------------------------------------------------------------
#  查询（给界面/表格用；都做"认不出就返回空"）
# ---------------------------------------------------------------------------

def attributes_of(facts: dict, tag: str) -> list:
    """某个 tag 的属性记录 `[{'group':…, 'attribute':…}, …]`（没有 → 空列表）。"""
    table = value_of(facts, 'attributes', {}) or {}
    if not isinstance(table, dict):
        return []
    rows = table.get(str(tag)) or []
    return [r for r in rows if isinstance(r, dict)]


def groups_of(facts: dict, tag: str) -> dict:
    """
    `{组名: [属性, …]}` —— **引擎给的分组**（`layeredimage` 的 group）。

    ⚠ 这是"真分组"。项目在 `rules.json` 里声明的 `attr_groups` 只应当是
    **改名 / 合并**的覆盖层，不是分组的来源（这一点真机实测过）。
    """
    out: dict = {}
    for row in attributes_of(facts, tag):
        name = str(row.get('group') or '')
        attr = str(row.get('attribute') or '')
        if not attr:
            continue
        out.setdefault(name, [])
        if attr not in out[name]:
            out[name].append(attr)
    return out


def candidates_of(facts: dict, tag: str) -> list:
    """
    某个 tag 的**合法候选**（引擎按"适合展示"排过序的那一份）。

    这就是"候选值不该让人手填"的落点：编剧要选的东西，引擎已经列好了。
    """
    table = value_of(facts, 'candidates', {}) or {}
    if not isinstance(table, dict):
        return []
    vals = table.get(str(tag)) or []
    return [str(v) for v in vals if str(v)]


def grouped_choices(facts: dict, tag: str, rename=None) -> list:
    """
    引擎事实 → 界面用的**分组候选**：`[{'group', 'label', 'values'}, …]`。

    这是 E2 的核心：**"下拉里放什么"由引擎决定，人一个都不用填。**

    三条规则（都对着实测过的取舍）：
      · **组的来源是引擎**（`layeredimage` 的 group），不是我们编的组名；
      · **值 = 引擎枚举出的候选 ∩ 该组成员**，并**保持引擎给的顺序**（不重排 ——
        引擎那份是"适合展示给用户"的顺序，重排等于把它的判断丢掉）；
      · **`rename` 只做改名**（`{'outfit': '服装'}`）—— 这就是"定义表降级为覆盖层"的含义。
      · 引擎有、但没归到任何组的候选 → 兜底一栏（`label` 为空 = 不显示标题），**绝不丢词**。
    """
    groups = groups_of(facts, tag)
    cands = candidates_of(facts, tag)
    rename = rename or {}
    out = []
    if groups:
        for name, members in groups.items():
            allowed = set(members)
            values = [v for v in cands if v in allowed] if cands else list(members)
            if not values:
                continue
            out.append({'group': name,
                        'label': str(rename.get(name) or '') or name,
                        'values': values})
    covered = {v for entry in out for v in entry['values']}
    rest = [v for v in cands if v not in covered]
    if rest:
        out.append({'group': '', 'label': str(rename.get('') or ''), 'values': rest})
    return out


def group_conflicts(facts: dict, tag: str, attrs) -> list:
    """
    **规则 N3**：同一个组里被**同时选中**的属性 → 冲突（`[{'group', 'attrs'}, …]`）。

    为什么工具必须自己查：实测 `show lan happy sad`（两者同组）时
    **lint exit 0、引擎一声不吭** —— 这类冲突 lint 不管，只能靠引擎给的 group 自己算。
    """
    groups = groups_of(facts, tag)
    used = [str(a) for a in (attrs or []) if str(a)]
    out = []
    for name, members in groups.items():
        allowed = set(members)
        hit = [a for a in used if a in allowed]
        if len(hit) >= 2:
            out.append({'group': name, 'attrs': hit})
    return out


def rename_overlay(attr_groups) -> dict:
    """
    项目声明（`rules.json` 的 `attr_groups`）→ **只取改名**用的 `{组名: 中文名}`。

    这就是"定义表降级为覆盖层"的具体含义：**分组结构由引擎给，人只改说法**
    （把 `outfit` 显示成「服装」）。
    ⚠ 声明里的 `options`（哪些词属于哪组）在本函数里**不参与** ——
    那是旧机制；结构已经是引擎的事实，人再来"指定成员"就与引擎打架了。
    """
    out = {}
    for entry in (attr_groups or ()):
        if isinstance(entry, dict):
            name = str(entry.get('name') or '')
            label = str(entry.get('label') or '')
        else:
            name = str(getattr(entry, 'name', '') or '')
            label = str(getattr(entry, 'label', '') or '')
        if name and label:
            out[name] = label
    return out


def narrow_problem(before, after) -> str:
    """
    收窄断言：`get_ordered_image_attributes(tag, (已选,))` 的结果必须满足
    **性质**（不写死期望值）：

      ① 是原集合的**子集**；② 不比原集合**更大**；③ 只在 **空** 或 **没变化** 时报警。

    为什么写成性质而不是期望值：引擎升级或项目加属性后，写死的期望值都要人工更新，
    否则 CI 天天误报 —— 人就把这条门禁关了。**性质断言不会过期。**
    """
    a = set(str(x) for x in (before or []))
    b = set(str(x) for x in (after or []))
    if not b.issubset(a):
        extra = sorted(b - a)
        return f'收窄后出现了原集合里没有的属性：{extra[:5]}'
    if len(b) > len(a):
        return f'收窄后变大了：{len(a)} → {len(b)}'
    if len(a) >= 2 and len(b) == len(a):
        return f'收窄无效：给了已选属性后集合没有变小（{len(a)} 项）'
    if len(a) >= 1 and len(b) == 0:
        return '收窄成空集：引擎认为该属性与任何属性都不兼容'
    return ''


# ---------------------------------------------------------------------------
#  合并多来源（不许静默挑一个）
# ---------------------------------------------------------------------------

def merge_fact(contributions) -> tuple:
    """
    `[(来源, 值), …]` → `(事实条目, 冲突记录列表)`。

    规则（都不静默）：
      · 丢掉空贡献；
      · 只 1 个 → `{'value':…, 'source':…}`；
      · 多个且**相等** → 记 `sources` 列表，来源取第一个（都是同一个值，不算冲突）；
      · 多个且**不等** → 值取**列表的并集**（列表型）/ 第一个（标量型），
        `source` 标成 `conflict`，并产出一条冲突记录。

    为什么并集而不是"引擎优先"：本项目对"猜"一贯不收。
    两个来源各说各的，工具**没有资格**替人决定谁对 —— 报出来，让人看。
    """
    kept = [(str(s), v) for s, v in (contributions or []) if v not in (None, '', [], {})]
    if not kept:
        return None, []
    if len(kept) == 1:
        src, val = kept[0]
        return {'value': val, 'source': src}, []

    values = [v for _s, v in kept]
    same = all(v == values[0] for v in values[1:])
    sources = [s for s, _v in kept]
    if same:
        return {'value': values[0], 'source': sources[0], 'sources': sources}, []

    if all(isinstance(v, list) for v in values):
        union = []
        for v in values:
            for item in v:
                if item not in union:
                    union.append(item)
        merged = union
    else:
        merged = values[0]
    conflict = {
        'field': '',
        'sources': sources,
        'note': '来源不一致，值取并集且已标记为 conflict（请人工确认）',
        'values': [v if not isinstance(v, (list, dict)) else f'<{type(v).__name__} {len(v)}>'
                   for v in values],
    }
    return {'value': merged, 'source': SRC_CONFLICT, 'sources': sources}, [conflict]


def build_facts(payloads: dict, *, engine=None, fingerprint=None,
                collection=None, lint=None, derived=None) -> dict:
    """
    把 `{键: [(来源, 值), …]}` 装配成一份完整事实（含冲突记录）。纯函数。

    这是采集器的**最后一公里**（采集器只管跑引擎、收原始数据）。
    放进纯层是为了让"来源/冲突"这套规矩能被单测覆盖，不必真跑引擎。
    """
    out = empty_facts()
    out['engine'] = dict(engine or {})
    out['fingerprint'] = dict(fingerprint or {})
    out['collection'] = {'parse_clean': False, 'probe_complete': False,
                         'lint_exit': None}
    out['collection'].update(collection or {})
    out['lint'] = dict(lint or {})
    out['derived'] = dict(derived or {})

    conflicts = []
    for key in FACT_KEYS:
        entry, found = merge_fact((payloads or {}).get(key))
        if entry is not None:
            out['facts'][key] = entry
            for c in found:
                c['field'] = key
                conflicts.append(c)
    out['conflicts'] = conflicts
    return out


def dumps(facts: dict) -> str:
    """事实 → JSON 文本（稳定：键排序、缩进 1、保留中文）。"""
    return json.dumps(facts, ensure_ascii=False, indent=1, sort_keys=True)


def loads(text: str) -> dict:
    """JSON 文本 → 事实（解析失败**抛出**，由调用方决定怎么报）。"""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError('事实文件的顶层不是对象')
    return data


__all__ = [
    'SCHEMA_VERSION', 'FACTS_FILENAME',
    'SRC_PROBE', 'SRC_JSON_DUMP', 'SRC_SCANNER', 'SRC_CONFLICT',
    'FACT_KEYS', 'DERIVED_KEYS',
    'empty_facts', 'facts_path', 'validate_facts', 'is_collectable',
    'value_of', 'source_of', 'attributes_of', 'groups_of', 'candidates_of',
    'grouped_choices', 'group_conflicts', 'rename_overlay',
    'narrow_problem', 'merge_fact', 'build_facts', 'dumps', 'loads',
]
