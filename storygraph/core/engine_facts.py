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
import re
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
#:
#: ⭐ `statements`（2026-10-11 加，**第一层**）：引擎 `renpy.statements.registry`
#: 的原样快照 —— "**你注册了什么**"由引擎直接回答，这是判"认不认识某个写法"
#: 的**权威名单**（不再靠"看报错文本猜"）。
FACT_KEYS = (
    'images', 'tags', 'attributes', 'candidates',
    'transforms', 'screens', 'defines', 'labels', 'builtins', 'statements',
    #: ⭐ **行为级**的验证结果（第三层）：`{查询名: {'verified', 'why'}}`。
    #: `verified` 三态：`True` 证明了 / `False` 证明不成立 / **`None` 没证明成**。
    'behavior',
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


def registered_statements(facts: dict) -> dict:
    """
    **第一层**：引擎注册过的自定义语句 → `{名字: {'block', 'has_parse', …}}`。

    数据来自引擎自己的 `renpy.statements.registry`（探针原样快照）——
    所以这是"**你注册了什么**"的**权威答案**，不是我们的推测。

    ⚠ 三条不宣称：
      · 没采过 / `is_collectable` 为假 → `{}`（**不猜**）；
      · 探针取不到注册表（老引擎 / 魔改）→ `{}`（`facts` 里能看到
        探针记下的 `statements_error`）；
      · 这是**快照**：项目改过 CDS 之后要重采（指纹会变）。
    """
    if not facts:
        return {}
    ok, _reason = is_collectable(facts)
    if not ok:
        return {}
    table = value_of(facts, 'statements', {}) or {}
    if not isinstance(table, dict):
        return {}
    return {str(k): v for k, v in table.items() if isinstance(v, dict)}


def statement_names(facts: dict) -> set:
    """注册表里的名字集合（判"认不认识"用这一个就够）。"""
    return set(registered_statements(facts))


def statement_shape(facts: dict, head: str) -> dict:
    """
    某个写法在**引擎注册表**里的形状（没注册 → `{}`）。

    这是"**要填什么**"最权威的来源：`block` 直接说明它带不带块
    （`True` / `False` / `'possible'` / `'script'`）；`has_parse` 说明项目
    给它写了自定义 parse —— 那就**别猜参数**，该去看项目那段代码。
    """
    return registered_statements(facts).get(str(head or '').strip(), {})


def undetermined_statements(facts: dict) -> dict:
    """
    **第四层**：把"扫描发现了、但我们**测不出来**"的写法摊开（纯函数）。

    为什么要单独一栏（用户 2026-10-11 的四层方案第四层："诚实标记测不出来，
    不当假绿"）：扫描发现 N 个"工具不认识的写法"之后，**能不能问引擎**？
    实测结论是**不能** —— 未知写法本身就是 parse error，而 `init python`
    （注册 CDS 的地方）属于**后面的解析组**（`renpy/script.py:447-452`：
    一组出错、后面全不解析），所以引擎连注册代码都没机会跑。

    于是这些写法一律是"测不出来"，必须在报告里**显式说出来**；
    只显示"✅ 采集完成"就是假绿。

    返回::

        {'total_scan': int,
         'engine_known':   [{'head','count','why'}]   # 引擎答过：认识
         'engine_unknown': [{'head','count','why'}]   # 引擎答过：不认识
         'undetermined':   [{'head','count','why'}]}  # 没被回答 / 被跳过

    ⚠ **两种"没数据"要分开**（这是真机跑出来的，我第一版搞混了）：

      · **没有扫描段**（没采过 / 没跑扫描）→ 三栏全空、报告是空串（不宣称）；
      · **有扫描段、但事实不可用**（项目有 parse error → `is_collectable` 为假）
        → **必须说话**：那些写法就是 `undetermined`，而且理由是"这轮采集不可用
        （引擎在第一处 parse error 停下）"。
        ⚠ 我第一版把这两种一起返回空 —— 结果**恰好在最该报警的时候沉默了**
        （真机 `ask=True` 跑出来 `total_scan=0`，当场发现）。
    """
    out = {'total_scan': 0, 'engine_known': [], 'engine_unknown': [],
           'undetermined': []}
    if not facts:
        return out
    rows = (facts.get('derived') or {}).get('undeclared_heads')
    if not isinstance(rows, list):
        return out
    collectable, why_not = is_collectable(facts)
    for row in rows:
        if not isinstance(row, dict):
            continue
        head = str(row.get('head') or '')
        if not head:
            continue
        out['total_scan'] += 1
        item = {'head': head, 'count': int(row.get('count') or 0), 'why': ''}
        known = row.get('engine_recognized') if collectable else None
        if not collectable:
            item['why'] = (f'**这轮采集不可用**（{why_not or "事实不可用"}）'
                           f'—— 引擎在第一处 parse error 就停下了，问不到它')
            out['undetermined'].append(item)
        elif row.get('engine_skipped'):
            item['why'] = ('这一轮**跳过了**（超过一次能问的条数）—— 没测过，'
                           '不能当成"没问题"')
            out['undetermined'].append(item)
        elif known is True:
            item['why'] = str(row.get('engine_summary') or '引擎认识它')
            out['engine_known'].append(item)
        elif known is False:
            item['why'] = str(row.get('engine_error')
                              or row.get('engine_summary') or '引擎不认识它')
            out['engine_unknown'].append(item)
        else:
            why = str(row.get('engine_error') or '').strip()
            item['why'] = why or (
                '**没问成**：项目里有引擎不认识的写法时，引擎会在第一处 parse '
                'error 停下（后面的解析组全不跑），所以问不到关于它的结论')
            out['undetermined'].append(item)
    for key in ('engine_known', 'engine_unknown', 'undetermined'):
        out[key].sort(key=lambda r: (-r['count'], r['head']))
    return out


def format_behavior_line(facts: dict) -> str:
    """
    行为级那一行（**第四层的口径**）→ 人话。

    · **一个都没验过** → 明说"这一层没覆盖"，并写清**没覆盖 ≠ 通过**
      （原来的写法，保留）；
    · **验过** → 报"验过 N —— 通过/失败/没证明成"，并把失败与
      "没证明成"的**名字**列出来（`verified=None` 那档既不叫通过也不叫失败）。
    """
    """
    行为级那一行（第四层口径）→ 人话。

    · **一个都没验过** → 明说"这一层没覆盖"，并写清**没覆盖 ≠ 通过**
      （这是原来的写法，保留）；
    · **验过** → 报"已验 N / 没验 M"，并把失败与"没证明成"的**名字**列出来。
    """
    s = behavior_summary(facts)
    if not s['total']:
        return ('  ⚠ 行为级验证（`testcase` + `test`）**这一层没覆盖**：'
                '它在"能被 test 跑起来的完整工程"上才做得了（本机实测：'
                '裸最小工程跑 `test` 会卡住）。**没覆盖 ≠ 通过。**')
    parts = [f'  · 行为级验证：验过 {s["total"]} 个 —— '
             f'通过 {len(s["verified"])}'
             f' / 失败 {len(s["failed"])}'
             f' / **没证明成** {len(s["unverified"])}']
    if s['verified']:
        parts.append('      通过：' + '、'.join(s['verified'][:6]))
    if s['failed']:
        parts.append('      ⚠ 失败：' + '、'.join(s['failed'][:6]))
    if s['unverified']:
        parts.append('      ⚠ 没证明成（**既不是通过也不是失败**）：'
                     + '、'.join(s['unverified'][:6]))
    return '\n'.join(parts)


def format_undetermined(summary: dict) -> str:
    """
    第四层的汇总 → 人话（**没有内容就返回空串**：不宣称）。

    规则 **N1** 要求扫描结果必须带上那句免责："以上是「我认出的」，
    不代表项目里只有这些" —— 这里照抄，不另编说法。
    """
    summary = summary or {}
    total = int(summary.get('total_scan') or 0)
    if not total:
        return ''
    out = [f'【扫描 vs 引擎：能不能问出结论】扫描发现 {total} 个'
           f'"工具还不认识的写法"']
    und = summary.get('undetermined') or []
    known = summary.get('engine_known') or []
    unknown = summary.get('engine_unknown') or []
    if known:
        out.append(f'  · 引擎确认**认识**：{len(known)} 个'
                   + '（' + '、'.join(f'{r["head"]}×{r["count"]}'
                                     for r in known[:6]) + '）')
    if unknown:
        out.append(f'  · 引擎确认**不认识**：{len(unknown)} 个'
                   + '（' + '、'.join(f'{r["head"]}×{r["count"]}'
                                     for r in unknown[:6]) + '）')
    if und:
        out.append(f'  ⚠ **测不出来**：{len(und)} 个 —— 它们既没被引擎确认，'
                   f'也没被否认（**不能当成"没问题"**）')
        for r in und[:6]:
            out.append(f'      {r["head"]}×{r["count"]}：{r["why"][:100]}')
        if len(und) > 6:
            out.append(f'      …另有 {len(und) - 6} 个')
    out.append('  · 以上是「我认出的」，不代表项目里只有这些（N1）')
    return '\n'.join(out)


def behavior_facts(facts: dict) -> dict:
    """
    **第三层的事实**：查询语句的行为验证结果 → `{查询名: {'verified', 'why'}}`。

    形状（采集/验证方写进 `facts['behavior']`）::

        {'sg_check_weather': {'verified': True,  'why': '引擎报 PASSED，且证据存在'},
         'sg_check_outfit':  {'verified': False, 'why': '证据文件不存在'},
         'sg_check_demo':    {'verified': None,  'why': '没读到 Status'}}

    ⚠ 三条不宣称（与注册表那一层同源）：
      · 没采过 → `{}`；
      · `verified=None`（没证明成）**保留为 None**，绝不折成 False
        （"没证明"与"证明是错的"是两件事）；
      · 坏条目跳过，不炸。
    """
    if not facts:
        return {}
    table = value_of(facts, 'behavior', {}) or {}
    if not isinstance(table, dict):
        return {}
    out = {}
    for name, rec in table.items():
        if not isinstance(rec, dict):
            continue
        v = rec.get('verified')
        out[str(name)] = {'verified': None if v is None else bool(v),
                          'why': str(rec.get('why') or '')}
    return out


def behavior_summary(facts: dict) -> dict:
    """
    **第四层的口径**：行为级**验了几个 / 没验几个**（纯函数）。

    返回 `{'verified': [名字…], 'failed': [名字…], 'unverified': [名字…],
           'total': int}`。

    ⚠ 三档分开：`verified=True`（证明了）/ `False`（证明不成立）/
    `None`（**没证明成** —— 既不是"通过"也不是"失败"）。
    """
    table = behavior_facts(facts)
    out = {'verified': [], 'failed': [], 'unverified': [], 'total': len(table)}
    for name in sorted(table):
        v = table[name]['verified']
        if v is True:
            out['verified'].append(name)
        elif v is False:
            out['failed'].append(name)
        else:
            out['unverified'].append(name)
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


def unrecognized_attrs(facts: dict, tag: str, attrs) -> list:
    """
    这些属性里，**引擎没认**的有哪些（`[属性, …]`，保序去重）。

    用途（E2 · 2026-10-11）：**只做标注，绝不过滤**。
    真机实测：资源表里有一批"文件真实存在、但不是可选立绘属性"的东西
    （`mask` / `_ss_cut` / `lan_angry` …）——它们**照样可选、照样能导入**，
    只是选之前该知道"引擎会用 `'lan lan_angry' is not an image` 报错"。
    拿引擎的枚举去**删候选**，就会连"合法的、只是引擎还没认的"一起删掉 ——
    那是"无法导入"的来源（用户 2026-10-11 明确否掉的方向）。

    ⚠ 三条不宣称的规矩（**宁可不说，也不说错**）：
      1. 没有事实（没采过 / `is_collectable` 为假）→ 返回 `[]`；
      2. 引擎**对这个 tag 什么都没说**（属性和候选都空）→ 返回 `[]`
         （否则每个非 layeredimage 的 tag 都会被误标成"未认"）；
      3. 只比对，不推断。
    """
    if not facts:
        return []
    ok, _reason = is_collectable(facts)
    if not ok:
        return []
    known = {str(a.get('attribute') or '')
             for a in attributes_of(facts, str(tag))}
    known |= {str(v) for v in candidates_of(facts, str(tag))}
    known.discard('')
    if not known:
        return []
    out = []
    for attr in (attrs or ()):
        word = str(attr or '')
        if word and word not in known and word not in out:
            out.append(word)
    return out


#: 归一时抹掉的**记号**：`bg1.1` / `bg1_1` / `bg-1-1` 是同一个名字的三种写法。
_SPELLING_SEP = re.compile(r'[\s._\-]+')


def unrecognized_by_tag(items) -> dict:
    """
    `[(tag, [属性…]), …]` → `{tag: [引擎未认的属性…]}`（**纯函数**）。

    为什么要有它：`unrecognized_attrs` 只回答"**这一格**里哪些没被认"，
    而"报出来"（路线图 §8.9 待做的那一项）要的是**项目级汇总**：
    一个项目到底有多少处会 lint 报错、集中在哪几个角色身上。

    两条自我约束：
      · 空的 tag / 空的属性一律不收（报告里不出现空行）；
      · **保序去重**（同一个属性在同一个角色下只报一次）。

    ⚠ **只是汇总，不做过滤** —— 这些属性**仍然可选、仍然能导入**
    （用户 2026-10-11："一切以实际有的资源为准"）。
    """
    out: dict = {}
    for item in (items or ()):
        try:
            tag = str((item or [None, None])[0] or '').strip()
            attrs = (item or [None, None])[1] or ()
        except (TypeError, IndexError):
            continue
        if not tag:
            continue
        bucket = out.setdefault(tag, [])
        for attr in attrs:
            word = str(attr or '').strip()
            if word and word not in bucket:
                bucket.append(word)
    return {k: v for k, v in out.items() if v}


def _norm_word(word: str) -> str:
    """名字 → 归一形式（去大小写与 `.`/`_`/`-`/空格）。**只用来消除假警报。**"""
    return _SPELLING_SEP.sub('', str(word or '')).lower()


def table_vs_engine(facts: dict, table_attrs) -> dict:
    """
    资源表登记的 `{tag: [属性, …]}` 与**引擎事实**对账 → 差异。纯函数。

    这是路线图 §八 四步时序里**第 ③ 步"对账"**的实现。**只报告，不删任何东西**：

      · `table_only`   —— 表里有、引擎不认（`mask` / `_ss_cut` 这类**内部件**）；
        ⚠ 它们**仍然可选、仍然能导入**（用户 2026-10-11 定的口径：
        候选以**实际存在的资源**为准）—— 这里只是把它们**报出来**；
      · `engine_only`  —— 引擎有、表里没登记（提示补登）；
      · `spelling_only` —— **只是写法不同**（`bg1.1` vs `bg1_1`）→ **只计数、不算差异**，
        但**照样留在数据里**（不丢信息，报告里明说"这不算差异"）。

    ⚠ `spelling_only` 这一桶是**真机逼出来的**：不加它，`sr_general` 会报
    **175 项**，其中 **76 项**只是写法差异 —— 一份 175 条的清单没人会看，
    等于没有报告。归一**只用来消除假警报**（把"要紧的"从噪声里择出来），
    **绝不拿来下"它们其实是同一个"的结论**（契约 T1：归一只能找候选）。

    ⚠ 三条不宣称（与 `unrecognized_attrs` 同一族）：
      没有事实 / `is_collectable` 为假 / **引擎对这个 tag 什么都没说** → 跳过这个 tag。
    """
    if not facts or not table_attrs:
        return {}
    ok, _reason = is_collectable(facts)
    if not ok:
        return {}
    table_only: dict = {}
    engine_only: dict = {}
    spelling: dict = {}
    for tag, words in (table_attrs or {}).items():
        known = {str(a.get('attribute') or '')
                 for a in attributes_of(facts, str(tag))}
        known |= {str(v) for v in candidates_of(facts, str(tag))}
        known.discard('')
        if not known:
            continue                      # 引擎没说过这个 tag → 不下结论
        listed = [str(w) for w in (words or ()) if str(w)]
        listed_norm = {_norm_word(w) for w in listed}
        known_norm = {_norm_word(w) for w in known}
        tbl, eng, spl = [], [], []
        for word in listed:
            if word in known:
                continue
            if _norm_word(word) in known_norm:
                spl.append(word)          # 只是写法不同
            else:
                tbl.append(word)
        for word in sorted(known):
            if word in set(listed):
                continue
            if _norm_word(word) in listed_norm:
                if word not in spl:
                    spl.append(word)
            else:
                eng.append(word)
        if tbl:
            table_only[str(tag)] = tbl
        if eng:
            engine_only[str(tag)] = eng
        if spl:
            spelling[str(tag)] = spl
    return {'table_only': table_only, 'engine_only': engine_only,
            'spelling_only': spelling}


def diff_facts(old, new) -> dict:
    """
    两份引擎事实 → **漂移报告**（E3）。纯函数，无需引擎、无需项目 —— CI 里能直接跑。

    返回：
        {
          'same': bool,
          'engine': {'version': (旧, 新)} or {},
          'added':   {键: [新增的项…]},
          'removed': {键: [消失的项…]},
          'changed': {键: 'list' | 'scalar'},
        }

    为什么按"项"比而不是比整份 JSON：整份比只能告诉你"变了"，
    而"**引擎多了哪些 transform / 哪些 label 不见了**"才是人能直接处理的东西。
    映射型的事实（`attributes` / `candidates`）按 tag 展开成 `键=tag`，
    所以漂移报告能精确到**哪个角色多了哪个属性**。
    """
    out = {'same': True, 'engine': {}, 'added': {}, 'removed': {}, 'changed': {}}
    old_f = (old or {}).get('facts') or {}
    new_f = (new or {}).get('facts') or {}

    for key in FACT_KEYS:
        a, b = _flat_value(old_f.get(key)), _flat_value(new_f.get(key))
        if a is None and b is None:
            continue
        if a is None or b is None:
            out['changed'][key] = 'list' if isinstance(b if b is not None else a,
                                                       (list, dict)) else 'scalar'
            out['same'] = False
            continue
        if isinstance(a, dict) or isinstance(b, dict):
            for sub in sorted(set(a) | set(b)):
                x, y = set(_as_set(a.get(sub))), set(_as_set(b.get(sub)))
                if x != y:
                    out['same'] = False
                    if y - x:
                        out['added'][f'{key}.{sub}'] = sorted(y - x)
                    if x - y:
                        out['removed'][f'{key}.{sub}'] = sorted(x - y)
        else:
            x, y = set(_as_set(a)), set(_as_set(b))
            if x != y:
                out['same'] = False
                if y - x:
                    out['added'][key] = sorted(y - x)
                if x - y:
                    out['removed'][key] = sorted(x - y)

    old_v = ((old or {}).get('engine') or {}).get('version')
    new_v = ((new or {}).get('engine') or {}).get('version')
    if old_v and new_v and old_v != new_v:
        out['engine']['version'] = (old_v, new_v)
        out['same'] = False
    return out


def _flat_value(entry):
    """事实条目 → 裸值（没有这条 → `None`；条目为空列表/空字典也算"有"）。"""
    if entry is None:
        return None
    return _entry_value(entry)


def _as_set(value):
    """值 → 可比较的序列（列表/元组/集合原样；标量包成单元素）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value]
    if isinstance(value, dict):
        return [str(k) for k in value]
    return [str(value)]


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
    'grouped_choices', 'group_conflicts', 'rename_overlay', 'unrecognized_attrs',
    'unrecognized_by_tag',
    'table_vs_engine', 'diff_facts',
    'narrow_problem', 'merge_fact', 'build_facts', 'dumps', 'loads',
    'registered_statements', 'statement_names', 'statement_shape',
    'undetermined_statements', 'format_undetermined',
    'behavior_facts', 'behavior_summary',
]
