"""
自定义语句：表单 → 声明（**纯函数**，UI/CLI/测试共用）
=======================================================

用户在 2026-10-10 报的那个缺口：

> 你说支持自适应的解析……但是我并没有找到一个入口可以教这个工具学会
> 我自定义的项目模块……至于什么 `swim_suit` 一类的貌似无法做到呢……
> 至少我想要个能够自定义的模块入口放在最上方

后端其实一直是齐的（`declare_statement()` 是"教一次"的唯一写入口，
CLI 的 `learn` 在用它），缺的是**发现 + 入口**：

    `discover_undeclared()`（扫项目找出还不认识的写法）
        → 弹窗（勾选 / 填参数）→ `build_declaration()`（本模块，纯）
        → `declare_statement()`（写 `storygraph.rules.json`）
        → `clear_cache()` → 表里立刻多出参数列与候选值

## 本模块只做"表单 → 后端参数"这一步

为什么值得单独一个纯函数：**表单数据最脏**（用户手输的参数名可能是
`intensity=3`、`强度 intensity`、带空格、带中文……），而 `declare_statement`
的契约是"坏条目跳过"—— 若在这里不校验，用户会看到"成功了"但表里没有那一列。
所以这里把**人怎么写都行**的输入规整成后端要的形状，并**报出**丢弃了什么。
"""

from __future__ import annotations

import re
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import (SHOW_CLAUSE_KEYWORDS,
                                        SHOW_INTRO_KEYWORDS)

#: 参数名必须是合法标识符（Ren'Py 关键字参数的名字就是标识符）
_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

#: 语句名：**允许中文，但每段必须以"字母"开头**（2026-10-14 收紧一次）。
#:
#: 真机里 `renpy.register_statement("泳装切换")` 是**合法**的，而这个工具的
#: 用户就是中文项目（界面全中文、实例是 `show 小明 泳装 开心`）。
#: 旧版是 ASCII-only，于是中文动作名**当场被拒收**、连"待声明"列表都进不去 ——
#: 用户看到的是"这个模块根本用不了"。
#:
#: ⚠ 但**不能一路放宽成 `^\w+…$`**：那样 `9bad` / `_x` 也会被收，
#: 而它们是真的语法错（旧判据 `test_build_declaration_refuses_a_bad_statement_name`
#: 与 `test_mcp_surface.py::test_declare_refuses_a_bad_name_and_says_why` 钉着）。
#: `[^\W\d_]` = "**任意 Unicode 字母**"（吃中文、不吃数字/下划线）——
#: 与之前 `[A-Za-z_]` 的口径一致，只是把"字母"的含义还给了 Unicode。
#: ⚠ 仍然不许空格以外的东西（`(` / `=` / `"` 这些是**真**语法错，
#: 由 `_STMT_BAD_CHARS` 兜）。
_STMT_RE = re.compile(r'^[^\W\d_]\w*(\s+[^\W\d_]\w*)*$', re.UNICODE)
#: 语句名里的**分隔符**（`_STMT_RE` 只管形状，这里管"哪些字不能用"）
_STMT_BAD_CHARS = ('"', "'", '(', ')', '=', ':', ',', '#', '\\')
#: 候选值分隔符：`,，、;；|` 与空白
_CHOICE_SPLIT_RE = re.compile(r'[,，、;；|\s]+')


def parse_choices(text) -> list:
    """
    候选值那一格 → 列表（`1,2,3` / `1，2 / 低 中 高` 都认）。

    用户口径（`spec_arg_choices` 那条契约）：**没声明就返回空** ——
    所以这里也不猜：认不出分隔符就当**一个**候选值，绝不擅自切。
    """
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        parts = [str(x) for x in text]
    else:
        parts = _CHOICE_SPLIT_RE.split(str(text))
    out = []
    for raw in parts:
        item = str(raw).strip()
        if item and item not in out:
            out.append(item)
    return out


def build_declaration(name: str, label: str = '', docs: str = '',
                      block=None, params=None, pattern=None) -> dict:
    """
    表单 → `declare_statement` 的参数（**纯函数**）。

    `params` 是 `[{'name','label','choices'}]`（UI 表格的行），
    `choices` 可以是字符串（`'1,2,3'`）或列表。

    返回 `{'ok', 'name', 'label', 'docs', 'block', 'columns', 'warnings'}`：

      · `ok=False` 只在**语句名**不合法时（那是硬错误，写下去会污染清单）；
      · 参数行里的坏名字（`intensity=3`、中文名…）**丢掉并在 `warnings` 里报**
        —— 后端"坏条目跳过"是静默的，这里必须出声，否则用户以为加上了；
      · `name` 允许 `a b` 这种多词写法（后端会拆成别名）。
    """
    name = str(name or '').strip()
    warnings = []
    if not name:
        return {'ok': False, 'name': '', 'label': '', 'docs': '',
                'block': block, 'columns': [],
                'warnings': ['语句名不能为空']}
    if not _STMT_RE.match(name) or any(c in name for c in _STMT_BAD_CHARS):
        return {'ok': False, 'name': name, 'label': '', 'docs': '',
                'block': block, 'columns': [],
                'warnings': [f'"{name}" 不是合法的语句名（用词 + 空格；'
                             f'不能用引号/括号/等号/冒号/逗号/井号）']}
    columns = []
    seen = set()
    for row in (params or ()):
        if not isinstance(row, dict):
            continue
        cname = str(row.get('name') or '').strip()
        # ⚠⚠ **`name` 就是"编剧看到的列名"**（2026-10-13 改）。
        #
        # 用户："我就不填那个可选的中文名，也不填剧本里怎么写……它反馈出来的
        # 只有一个参数！"—— 根因是这里只认 `name`：界面把"列名"放在 `label`
        # 里（那是主字段），`name` 留空 → 这一列被**静默丢掉**（连警告都没有），
        # 三个格子只剩一个。现在两者同源，`name` 空就用 `label`。
        if not cname:
            cname = str(row.get('label') or row.get('column') or '').strip()
        # 容忍"顺手把等号也写进去"：`intensity=3` / `intensity = 3`
        if '=' in cname:
            cname = cname.split('=', 1)[0].strip()
            warnings.append(f'列名里带了 `=`：已按 "{cname}" 处理')
        if not cname:
            # ⚠ **不许静默丢**（旧版就是静默的，用户看到的是"只剩一个参数"）：
            #   说不出"哪一格"就只能报个数，但一定要说。
            warnings.append(f'第 {len(columns) + 1} 格没有列名 —— '
                            f'这一格**没写进**声明（卡片上不会出现它）。'
                            f'给它起个名字（编剧看到的那个，例如「角色」）再写一次')
            continue
        # ⚠⚠ 这里**不许**再套"合法标识符"校验（2026-10-13 实测修）：
        #   这一格是**表格列名**，不是脚本里的具名参数 —— 中文（「人物」「服装」
        #   「神态」）天经地义。旧校验把中文列名整条丢掉，于是：
        #     界面三个格子全填好 → 声明里**一列都没有** → 试切永远"切不上"，
        #     而界面还在旁边写着"参数名 人物 不是合法标识符"（用户当场看出来了）。
        #   ⚠ 与本文件下面 `pattern` 那段的注释本来是**同一条规矩**，
        #     实现却漏改了这一处 —— 这就是"注释对了、代码没跟上"的典型。
        #   仍然只做两件事：去重、剥掉顺手写的 `=`。
        if cname in seen:
            warnings.append(f'列 "{cname}" 写了两次 —— 只保留第一次')
            continue
        seen.add(cname)
        col = {'name': cname}
        clabel = str(row.get('label') or '').strip()
        if clabel:
            col['label'] = clabel
        choices = parse_choices(row.get('choices'))
        if choices:
            col['choices'] = choices
        columns.append(col)
    if block is None:
        block = None                    # 后端默认 `possible`：不确定就别写死
    # ⭐ **位置序列**（2026-10-11）：界面上的格子有顺序、有类型 —— 那是
    #   "代码里长什么样"的唯一依据，所以必须与列**一起**交给后端存下来。
    #   ⚠ 只增不改：没给 `pattern` 时，产物与以前**逐字节一样**
    #   （既有的 golden / 60+ 条判据都压在这上面）。
    slots = []
    if pattern:
        from storygraph.authoring.pattern import parse_pattern
        raw_list = list(pattern)
        for i, slot in enumerate(parse_pattern(pattern)):
            raw_slot = raw_list[i] if i < len(raw_list) else None
            item = {'kind': slot['kind']}
            if slot.get('text'):
                item['text'] = slot['text']
            if slot.get('column'):
                item['column'] = slot['column']
            if slot.get('group'):
                item['group'] = slot['group']
            if slot.get('options'):
                item['options'] = slot['options']
            # ⚠⚠ **`limit` / `multi` 必须一起写下来**（2026-10-13 修）。
            #
            # 这两个键是修"切不上"（候选≠限死）和"A 段可以有多个"时加的，
            # **读**的那一侧全都认（`parse_pattern` / `WALK_KINDS` / 卡片），
            # 只有这里漏了 —— 于是"只在半边生效"：
            #   · 编剧勾了「编剧只能从下面这些里选」→ 试切是对的 →
            #     点"写进清单" → **勾选蒸发** → 她以为自己限死过；
            #   · 勾了「这一格可以有多个」→ 存下来变成一格一个词。
            #
            # ⚠ 写**输入里真的给过的**（含显式的 `False`）——
            #   "说过 false"和"没说"在语义上是两件事，往返要保真；
            #   而**整个键都没给**时不写，旧的产物因此逐字节不变。
            for key in ('limit', 'multi'):
                if isinstance(raw_slot, dict) and key in raw_slot:
                    item[key] = bool(slot.get(key))
            slots.append(item)
        # ⚠ 列名**不套脚本参数那套标识符校验**：这两件事不是一回事 ——
        #   脚本里的具名参数（`intensity=3`）必须是合法标识符，
        #   而**表格列**是我们自己的列，中文（「角色」「服装」）天经地义。
        #   （用户撞墙记录里他要的正是中文列名；旧校验会把它们全丢掉。）
        # ⚠⚠ 但**两套数据同时给时只认 `params` 的列**（否则同一列会出现两次：
        #   `params` 给标识符+中文名，`pattern` 又给一个中文名 —— 判据抓到过）。
        #   这时 `pattern` 只负责**顺序与类型**；它自己生列只在**没有 params** 时
        #   （也就是纯位置序列那条界面路径）。
        known = {c['name'] for c in columns}
        if not known:
            for slot in slots:
                col = str(slot.get('column') or '').strip()
                if not col or col in known:
                    continue
                known.add(col)
                row = {'name': col, 'label': col}
                if slot.get('options'):
                    row['choices'] = list(slot['options'])
                columns.append(row)
        # 顺序也决定列的顺序 —— 用户卡点之一："凭什么对应那个顺序"
        #   ⚠ 两套数据都给时，`params` 的列名可能与格子的列名不同（标识符 vs 中文），
        #     那就按"格子顺序能对上的排前面，剩下的保持原序"处理，不必强求改名。
        ordered = [str(s.get('column')) for s in slots if s.get('column')]
        if ordered:
            by_name = {c['name']: c for c in columns}
            head = [by_name[n] for n in ordered if n in by_name]
            if head:
                columns = head + [c for c in columns if c not in head]
    out = {'ok': True, 'name': name, 'label': str(label or '').strip(),
           'docs': str(docs or '').strip(), 'block': block,
           'columns': columns, 'warnings': warnings}
    if slots:
        out['pattern'] = slots
    return out


def build_group(name: str, label: str = '', options=None) -> dict:
    """
    表单的一行 → 一条**词组声明**（`rules.json` 的 `attr_groups` 里的一项）。

    与 `build_declaration` 同一个理由：表单数据最脏。这里：
      · 组名必须合法（字母/数字/下划线，字母开头）—— 它是内部键；
      · 候选词按通用分隔符切（`swim_suit, uniform 便装`）；
      · 可以只写组名（`options` 空 = **兜底组**：没归到别的组的词都进这里）。
    """
    gname = str(name or '').strip()
    if not gname:
        return {'ok': False, 'name': '', 'label': '', 'options': [],
                'warnings': ['词组名不能为空']}
    if not _NAME_RE.match(gname):
        return {'ok': False, 'name': gname, 'label': '', 'options': [],
                'warnings': [f'词组名 "{gname}" 不是合法标识符'
                             f'（只能字母/数字/下划线，且字母开头）']}
    opts = parse_choices(options) if not isinstance(options, (list, tuple)) \
        else parse_choices(list(options or ()))
    return {'ok': True, 'name': gname, 'label': str(label or '').strip(),
            'options': opts, 'warnings': []}


def suggest_shape(project_root, head: str, *, limit_files: int = 400,
                  samples_limit: int = 6, min_choice_hits: int = 2) -> dict:
    """
    **从一个动作在项目里真实出现过的行，反推"它要填什么"**（纯函数）。

    为什么这是"编剧也能懂"的技术支撑（用户 2026-10-10）：

    > 而且你说的这个候选值其实非常繁琐！有多少动作我也不知道，都在表格内部的……

    编剧不知道参数名、也说不清候选值。但他**已经写过**这个动作若干次 ——
    那就别让他填，工具自己去数：

      `swim_suit lyj "泳装" mood=happy` 出现 12 次
        → 第 1 项取到 `lyj`/`林月见`…（若是角色 ID → 名字默认「角色」，候选=角色表）
        → 第 2 项取到 `泳装`/`校服`…（**只出现一次的值不算候选**）
        → 第 3 项：作者自己写了名字 `mood` → 直接用

    返回::

        {'head': 'swim_suit', 'found': True, 'occurrences': 12,
         'items': [{'index': 0, 'kind': 'word'|'value'|'key'|'number',
                    'name': '角色'|'mood'|'第1项', 'default_name': '角色',
                    'renamed': bool, 'examples': ['林月见', …],
                    'choices': ['林月见', …], 'choices_from': '角色表'|'项目里写过的值',
                    'suggest_allowed': bool, 'hits': {值: 次数}}],
         'samples': [{'file': 'ch01.rpy', 'line': 7, 'text': '…'}],
         'files': ['ch01.rpy', …]}

    ⚠ **猜不出就不猜**：名字是由 `kind` 决定的**保守默认**
    （引号里的值 → 「内容」、裸词 → 「对象」、数字 → 「时长」、作者写了 `x=` → `x`），
    绝不生成 `intensity` 这种**看着像真的、其实是我编的**英文名。

    ⚠ 一行的切片由 `_split_call_line` 负责，其中 `(…)` / `[…]` / `{…}` 里的
    一整段算**一个词** —— `show expression Text("…", color=X)` 里的 `color=`
    是 **Python 调用的关键字参数**，不是这条语句的参数（见 `_scan_bare_end`）。
    """
    import re as _re
    from collections import Counter

    head = str(head or '').strip()
    out = {'head': head, 'found': False, 'occurrences': 0, 'items': [],
           'samples': [], 'files': [], 'role_names': []}
    root = Path(project_root) if project_root else None
    if not head or root is None or not root.is_dir():
        return out
    try:
        from storygraph.parsing.discover import discover_scripts
        files = list(discover_scripts(root))[:limit_files]
    except Exception as e:                       # noqa: BLE001
        swallow('learn_statement.suggest_shape.discover', e)
        return out

    roles = _role_table(root)
    out['role_names'] = list(roles['display_names'])

    slots: dict = {}
    occurrences = 0
    for fp in files:
        try:
            lines = Path(fp).read_text(encoding='utf-8',
                                       errors='replace').splitlines()
        except OSError:
            continue
        for lineno, raw in enumerate(lines, start=1):
            text = raw.strip()
            if not text or text.startswith('#'):
                continue
            tokens = _split_call_line(text)
            if not tokens or tokens[0] != head:
                continue
            occurrences += 1
            if len(out['samples']) < samples_limit:
                out['samples'].append({'file': Path(fp).name, 'line': lineno,
                                       'text': text[:80]})
            if Path(fp).name not in out['files']:
                out['files'].append(Path(fp).name)
            _collect_slots(slots, tokens[1:], roles)
    if not occurrences:
        return out
    out['found'] = True
    out['occurrences'] = occurrences
    out['items'] = _finish_slots(slots, min_choice_hits,
                                 cast_names=roles['display_names'])
    return out


def _role_table(root) -> dict:
    """
    角色表 → `{'ids': set, 'names': set, 'id_to_name': {}, 'display_names': []}`
    （读不出就四个空）。

    用途：把脚本里的**裸词**认成角色（`lyj` → 「凌跃捷」），
    这样"第 1 项"的默认名字就可能是「角色」而不是「对象」，
    候选里给的也是**编剧看得懂的中文名**。

    ⚠ `display_names`（2026-10-15 加）是**有序**的显示名列表 ——
    `names` 是 `set`，拿它当候选来源会得到**每次运行都不一样**的顺序。
    `suggest_shape` 的「角色格候选并上整张角色表」用它，保证顺序稳定。
    """
    ids, names, id_to_name = set(), [], {}
    try:
        from storygraph.pipeline.build import load_asset_registry
        reg = load_asset_registry(root)
        for cid, row in (getattr(reg, 'characters', {}) or {}).items():
            cid = str(cid)
            ids.add(cid)
            shown = str((row or {}).get('Name')
                        or (row or {}).get('DisplayName')
                        or (row or {}).get('名字') or cid).strip()
            shown = shown.strip('「」『』') or cid
            id_to_name[cid] = shown
            if shown and shown not in names:
                names.append(shown)
    except Exception as e:                       # noqa: BLE001 - 没角色表也能用
        swallow('learn_statement._role_table', e)
    return {'ids': ids, 'names': set(names), 'id_to_name': id_to_name,
            'display_names': list(names)}


def _slot(slots: dict, i: int, kind: str, name: str) -> dict:
    """
    第 `i` 项的收集器。**键是位置下标** —— 同一个位置必须是同一项：
    `outfit lyj …` / `outfit lxj …` 是**同一个第 1 项**（角色）。
    我第一版按 `(位置, 名字)` 做键，于是 `lxj` 没被认成角色时又冒出一条"对象"，
    参数表里出现两个第 1 项（出图前当场看出来）。
    """
    from collections import Counter
    return slots.setdefault(i, {'index': i, 'kind': kind, 'name': name,
                                'default_name': name, 'examples': [],
                                'hits': Counter(), 'suggest_allowed': False,
                                'note': ''})


#: 形态优先级：作者自己写了名字 > 角色表里的角色 > 引号内容 > 裸词 > 数字。
_KIND_RANK = {'key': 4, 'word-role': 3, 'value': 2, 'word': 1, 'number': 0}


def _note_kind(rec: dict, kind: str, name: str, note: str = '') -> None:
    """把这一位见过的"更像样的"形态记下来（同优先级不覆盖）。"""
    cur = f'{rec["kind"]}-role' if rec.get('note') == '角色表' else rec['kind']
    new = f'{kind}-role' if note == '角色表' else kind
    if _KIND_RANK.get(new, 0) > _KIND_RANK.get(cur, 0):
        rec['kind'] = kind
        rec['name'] = name
        rec['default_name'] = name
        rec['note'] = note


def _collect_slots(slots: dict, tokens, roles) -> None:
    """一行脚本的各个项 → 累加进对应位置的收集器（**按位置**，与顺序无关）。"""
    for i, tok in enumerate(tokens):
        kind, shown, key = tok
        is_role = False
        if kind == 'key':
            rec = _slot(slots, i, 'key', key)
            _note_kind(rec, 'key', key)
            rec['suggest_allowed'] = True             # 作者自己写了名字
        elif kind == 'number':
            rec = _slot(slots, i, 'number', f'第{i + 1}项')
            _note_kind(rec, 'number', f'第{i + 1}项')
        elif kind == 'value':
            rec = _slot(slots, i, 'value', '内容')
            _note_kind(rec, 'value', '内容')
        else:                                          # 裸词：可能是角色
            is_role = shown in roles['ids'] or shown in roles['names']
            default = '角色' if is_role else '对象'
            rec = _slot(slots, i, 'word', default)
            _note_kind(rec, 'word', default, '角色表' if is_role else '')
            if is_role:
                rec['suggest_allowed'] = True
        # 角色那一项显示**中文名**（项目里写的是 ID，编剧看的是名字）
        if is_role or rec['note'] == '角色表':
            shown = roles['id_to_name'].get(shown, shown)
        rec['hits'][shown] += 1


def _finish_slots(slots: dict, min_choice_hits: int, cast_names=None) -> list:
    """
    收集器 → 给界面看的项（例子、候选、来源、能不能限定）。

    `cast_names` = 角色表的**显示名**（有序，2026-10-15 加）。

    ⚠⚠ 识别成「角色」的那一格（`note == '角色表'`），候选要**并上整张角色表**
    —— 这正是 `suggest_shape` docstring 写死的口径（"若是角色 ID → 候选=角色表"），
    而旧实现只给"在这条语句的行里出现过 ≥2 次的值"。差别在真机上很难看：

      · 候选中混着 `screen` / `image` 这类**语句关键字**（`show screen …`）
        和 `cg` / `bg_1` 这类图标签 —— 一个叫「角色」的格子却给人这些；
      · 真机 28 个角色里只冒出 10 个（都是碰巧在 `show` 行里出现够多次的）。

    用户 2026-10-13 的原话是"**必须以真实的有的资源为准**"，所以这里按角色表补。
    """
    cast: list = []
    seen_cast: set = set()
    for nm in (cast_names or ()):
        nm = str(nm or '').strip()
        if nm and nm not in seen_cast and not _is_not_a_value(nm):
            seen_cast.add(nm)
            cast.append(nm)
    out = []
    for idx in sorted(slots):
        rec = slots[idx]
        rec['examples'] = [v for v, _n in rec['hits'].most_common(6)]
        # ⚠ **只出现一次的值不算候选**：那更像笔误/一次性的写法，
        #    拿它当"只能填这些"会把编剧的手脚捆住（用户原话：候选非常繁琐）
        choices = [v for v, n in rec['hits'].most_common()
                   if n >= max(2, int(min_choice_hits))]
        # ⚠ 只滤 **choices**（下拉里给人挑的那些）；**examples 不滤** ——
        #   那是"这一位长什么样"的证据，而角色的 ID 完全可能就叫 `at`/`with`
        #   （第一版连 examples 一起滤，于是"给 show 推角色名"那条当场变红）。
        picked = [v for v in choices if not _is_not_a_value(v)]
        is_role = rec['note'] == '角色表'
        if is_role:
            # 角色格：**并上整张角色表**。"项目里写过的值"排在前面 ——
            # 它们是这条语句真的用过的，先给人看见（也保证 `lan`/`ling`/`ye`
            # 这些立绘 tag 稳定出现在前面：它们不在角色表里，只在这份 hits 里）。
            for nm in cast:
                if nm not in picked:
                    picked.append(nm)
        # 上限：角色表可以很长（真机 28），给它更宽的天花板，别把角色截掉。
        rec['choices'] = picked[:(64 if is_role else 24)]
        rec['choices_from'] = (rec['note'] if is_role
                               else ('项目里写过的值' if choices else ''))
        rec['suggest_allowed'] = bool(rec['suggest_allowed'] and rec['choices'])
        rec['hits'] = dict(rec['hits'].most_common(12))
        out.append(rec)
    return out


def _split_call_line(text: str) -> list:
    """
    一行脚本 → `[头, 项…]`；项是 `(kind, 显示值, 键名)`。

    `kind`：`'key'`（`mood=happy` 的 `happy`）/ `'value'`（引号里的值）/
    `'number'`（裸数字）/ `'word'`（裸词，可能是角色）。

    刻意**不用** `shlex`：Ren'Py 台词里的引号/中文书名号/转义情况很多，
    这里只需要"够用的分词"，宁可少切也不要切错（切错就是把编剧的台词拆坏）。

    ⚠⚠ **括号里的东西算同一个词**（2026-10-15 加，见 `_scan_bare_end`）：
    `Text("…", color=X)` / `prompt("…", act=[…])` 里那些 `key=` 是 **Python
    调用的关键字参数**，不是这条语句的参数 —— 混进来的后果见那条判据。
    """
    s = str(text or '').strip()
    if not s or s.startswith('#'):
        return []
    out: list = []
    i, n = 0, len(s)
    while i < n and not s[i].isspace() and s[i] not in '"\'':
        i += 1
    out.append(s[:i])
    while i < n:
        while i < n and (s[i].isspace() or s[i] == ','):
            i += 1
        if i >= n:
            break
        key = ''
        m = _KEY_RE.match(s, i)
        if m:
            key = m.group(1)
            i = m.end()
        if i < n and s[i] in '"\'':
            quote = s[i]
            i += 1
            buf = []
            while i < n and s[i] != quote:
                buf.append(s[i])
                i += 1
            i += 1                                    # 吃掉收尾引号
            kind = 'key' if key else 'value'
            out.append((kind, ''.join(buf), key))
            continue
        j = _scan_bare_end(s, i)
        token = s[i:j]
        i = j
        if not token:
            continue
        if key:
            out.append(('key', token.rstrip(':'), key))
        elif _NUM_RE.match(token):
            out.append(('number', token.rstrip(':'), ''))
        else:
            out.append(('word', token.rstrip(':'), ''))
    return out


_KEY_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*')
_NUM_RE = re.compile(r'^-?\d+(\.\d+)?$')

#: 括号配对表（`_scan_bare_end` / `_skip_balanced` 用）。
_OPENERS = '([{'
_CLOSERS = {')': '(', ']': '[', '}': '{'}


def _scan_bare_end(s: str, i: int) -> int:
    """
    从 `s[i]` 起扫一个**裸词**，返回它的结束下标（不含）。

    ⚠⚠ 2026-10-15 加：**括号里的一整段算同一个词**（`(…)` / `[…]` / `{…}`，
    连里面的引号一起吃）。为什么必须这样 —— 实测真机 `game/screens.rpy`：

        show expression Text("SEIREN PROJECT", color="#89c7fe", size=60, bold=True) as title:

    `color=` / `size=` / `bold=` 是 **Python 函数调用的关键字参数**，与 `show`
    语句毫无关系。旧写法按空白/逗号切词，于是它们被当成 `show` 的具名参数 →
    反推出 `color` / `size` / `bold` 三个**看着像真的**参数名；编剧点
    「从项目里拿」照着写，得到的是一堆语法正确的**噪声**。
    同理 `show screen prompt("…", act=[…])` 里的 `act`。

    括号**不配对**（多行语句的前半截，`… act=[`）就一路吃到行尾 ——
    宁可少切，也不要切错（与这个模块一贯的"切错就是把编剧的台词拆坏"同一条）。
    """
    n = len(s)
    j = i
    while j < n:
        c = s[j]
        if c in _OPENERS:
            j = _skip_balanced(s, j)
            continue
        if c.isspace() or c == ',':
            break
        if c in '"\'':
            j += 1
            while j < n:
                if s[j] == '\\':
                    j += 2
                    continue
                if s[j] == c:
                    break
                j += 1
            j += 1
            continue
        j += 1
    return min(j, n)


def _skip_balanced(s: str, i: int) -> int:
    """从 `s[i]`（一个开括号）跳到**配对的闭括号之后**；不配对 → 行尾。"""
    stack = [s[i]]
    j = i + 1
    n = len(s)
    while j < n and stack:
        c = s[j]
        if c in '"\'':
            j += 1
            while j < n:
                if s[j] == '\\':
                    j += 2
                    continue
                if s[j] == c:
                    break
                j += 1
            j += 1
            continue
        if c in _OPENERS:
            stack.append(c)
        elif c in _CLOSERS and _CLOSERS[c] == stack[-1]:
            stack.pop()
        j += 1
    return min(j, n)


def group_preview(groups) -> str:
    """给人看的"这会写进清单的分组"。"""
    rows = [g for g in (groups or ()) if g.get('ok')]
    if not rows:
        return '（还没填词组）'
    lines = [f'写进 `attr_groups` 的 {len(rows)} 组：']
    for g in rows:
        if g['options']:
            lines.append(f'  · {g["name"]}（{g["label"] or g["name"]}）：'
                         f'{len(g["options"])} 个词 —— '
                         + '、'.join(g['options'][:8])
                         + ('…' if len(g['options']) > 8 else ''))
        else:
            lines.append(f'  · {g["name"]}（{g["label"] or g["name"]}）：'
                         f'兜底（没归到别的组的词都进这里）')
    return '\n'.join(lines)


def declaration_preview(built: dict, path: str = '') -> str:
    """给人看的"这会写什么"（写盘前/后的确认文本）。"""
    if not built.get('ok'):
        return '（没写：' + '；'.join(built.get('warnings') or ['?']) + '）'
    lines = [f'语句：{built["name"]}']
    if built.get('label'):
        lines.append(f'  中文名：{built["label"]}')
    if built.get('docs'):
        lines.append(f'  说明：{built["docs"]}')
    if built.get('block'):
        lines.append('  带块：是（块体逐字保真）')
    elif built.get('block') is False:
        lines.append('  带块：否')
    else:
        lines.append('  带块：未指定（后端按 possible 处理）')
    cols = built.get('columns') or []
    if cols:
        lines.append(f'  参数 {len(cols)} 个：')
        for c in cols:
            ch = '，'.join(c.get('choices') or [])
            lines.append(f'    · {c["name"]}'
                         + (f'（{c["label"]}）' if c.get('label') else '')
                         + (f' 候选：{ch}' if ch else ''))
    else:
        lines.append('  参数：无（只逐字透传）')
    if built.get('warnings'):
        lines.append('⚠ ' + '；'.join(built['warnings']))
    if path:
        lines.append(f'  写进：{path}')
    return '\n'.join(lines)


def layeredimage_groups(project_root, limit_files: int = 400) -> dict:
    """
    项目里**每个立绘（tag）自己**有哪些组、每组有哪些属性 —— `{tag: {组: [值]}}`。

    为什么要它（2026-10-13 用户实测）：

    > 参数与参数之间实际上是有关联的……比如 lan 实际上没有 hanfu_base 这种选项……
    > 而 hanfu_base 内又不会有那么多种表情……但是我可选是从项目内批量导入的，
    > 不会排除这些情况……必须以真实的有的资源为准，你自己去看看真实项目内的定义！

    实测真机 `definitions.rpy`：

        layeredimage lan:  group expressions (18) / group outfit → clothes2, base_hand_up
        layeredimage ling: group expressions (28) / group outfit → base_swimsuit, base_arms_akimbo
        layeredimage ye:   group expressions (18) / group outfit → base_handdown, base_swimsuit

    `hanfu_base` **确实不属于 lan** —— 而"跨 tag 合并"那份清单会把它们混在一起，
    于是填进去的候选里有根本不能用的值。所以这里**按 tag 分开**给。

    ⚠ 这是**尽力而为的粗读**（只认 `layeredimage <名>:` 与它下面缩进的
    `group <名>` / `attribute <名>`）—— 它比"合并清单"准得多，但**不解析条件**
    （`group outfit when clothes2` 那种 `when` 只记下来不用）。真正的条件求值
    是引擎的事（见 `engine_facts` 那条路），这里只回答"这个 tag 定义了哪些值"。

    ⚠ 真机里 `<tag>_cc`（角色卡预览用的复制品）与本体**组和值完全一样**
    （实测 `lan_cc` == `lan`）—— 那种重复项**不返回**（否则来源清单直接翻倍，
    22 条里有一半是重复的）。判据：同名去掉 `_cc` 之后还有本体，就跳过它。
    """
    root = Path(project_root) if project_root else None
    out: dict = {}
    if root is None or not root.is_dir():
        return out
    try:
        files = sorted(root.rglob('*.rpy'))[:int(limit_files)]
    except Exception as e:                       # noqa: BLE001 - 扫不动就当没有
        swallow('learn_statement.layeredimage_groups.glob', e)
        return out
    import re
    # ⚠⚠ **块头允许缩进**（2026-10-13 修）。第一版写的是
    #   `line.startswith('layeredimage')` + `head_re = ^layeredimage…`，
    #   于是 `init 10:` / `if persistent.use_new_art:` **里面**定义的立绘
    #   一个都读不到 —— 真机的 5 个恰好都在顶层，所以真机看不出来；
    #   换个项目（"按条件换一套立绘"是常态）候选就**全空**。
    # ⚠ tag **允许中文**（`layeredimage 幽灵:`）—— 这个工具的客户是中文项目，
    #   只认 ASCII 等于把 `幽灵` 的候选整块丢掉（不报错、恒空）。
    #
    # ⚠⚠ **行尾不许用 `$` 锚**（这是我自己当场踩的坑）：真机的 `.rpy` 是
    #   **CRLF**，`'layeredimage lan:\r'` 里那个 `\r` 会让 `…:\s*$` 匹配失败 ——
    #   5 个立绘里只有文件末尾那个（没有尾随 CR）活着。
    #   所以：**逐行先 `rstrip()`**，并且匹配式里**不写行尾锚**。
    head_re = re.compile(r'^(\s*)layeredimage\s+(\S+?)\s*:')
    for fp in files:
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except Exception as e:                   # noqa: BLE001 - 单个文件读不动就跳过
            swallow('learn_statement.layeredimage_groups.read', e)
            continue
        #: 当前块头的**缩进宽度**（用它判"块结束了没"）——
        #  缩进比它浅/相等 = 出了这个块。0 表示顶层。
        head_indent = None
        tag = None
        group = None
        for raw_line in text.splitlines():
            line = raw_line.rstrip()             # ⚠ 去掉 `\r` / 尾随空白（见上）
            m = head_re.match(line)
            if m:
                tag = m.group(2)
                group = None
                head_indent = len(m.group(1).replace('\t', '    '))
                out.setdefault(tag, {})
                continue
            if tag is None:
                continue
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(' \t'))
            indent = len(line[:indent].replace('\t', '    '))
            if indent <= head_indent:
                tag, group = None, None        # 出了这个块
                continue
            # ⚠⚠ **组名与属性名后面可以跟别的字**（2026-10-13 第二次修）。
            #
            # 真机的写法是：
            #     group expressions when not clothes2 and not base_hand_up:
            #     group outfit when clothes2 or base_hand_up:
            #
            # 即"组名 + `when` 条件 + 冒号"。第一版我写成
            # `group\s+(\S+?)\s*:`（要求名字**紧跟**冒号）—— 于是真机 5 个立绘
            # 的组**一个都没读到**（`qing` 之所以有值，只是因为它那个组恰好没写
            # `when`）。所以这里取"`group` 后面的**第一个词**"就够，
            # 后面跟 `when …` 还是直接跟 `:` 都不管（条件求值是引擎的事）。
            g = re.match(r'\s*group\s+([^\s:]+)', line)
            if g:
                group = g.group(1)
                out[tag].setdefault(group, [])
                continue
            a = re.match(r'\s*attribute\s+([^\s:]+)', line)
            if a and group:
                val = a.group(1)
                if val not in out[tag][group]:
                    out[tag][group].append(val)
    # 丢掉 `<tag>_cc` 这种"本体的复制品"（判据见 docstring）
    cleaned = {}
    for tag, groups in out.items():
        base = tag[:-3] if tag.endswith('_cc') else ''
        if base and base in out and out[base] == groups:
            continue
        cleaned[tag] = groups
    return {k: v for k, v in cleaned.items() if v}


def choice_sources(project_root, head: str = '') -> list:
    """
    这一格的候选值**可以从项目里白拿** —— 返回"来源清单"，让人挑一个。

    用户 2026-10-12 的原话：

    > 另外就是过于繁琐了……我的真实项目内有无数个表情！我不可能将它们
    > 一个个输入进去！我希望它能够做到识别并自己录入（当我说这是表情的时候，
    > 能够自动导入相关类别……）不然要填到猴年马月？

    来源清单 —— ⚠ **工具仍然不猜"这一格是不是表情"**，
    它只把"项目里到底有哪些值能拿"摆出来，挑哪个由人点：

      · **某个立绘自己的组**（`lan 的 outfit` / `lan 的 expressions`…）：
        直接读项目里的 `layeredimage`（见 `layeredimage_groups`）——
        ⚠ 这是**2026-10-13 新增的、也是该优先点的那一份**：用户实测指出
        "lan 根本没有 hanfu_base 这个选项"，而下面那份合并清单会把
        **所有角色**的属性混在一起（于是候选里有根本不能用的值）；
      · **立绘属性 · 引擎的组（全部角色合并）**：真机事实里引擎自己分好的组，
        跨 tag 合并去重 —— **仍然保留**（有些项目只有一个 tag，或人就是想要全集），
        但它不再排在第一位（先给"按角色"的，再给这个）；
      · **角色表**：资产表里的角色（ID 与显示名都给，编剧认得的是后者）；
      · **项目里真实用过的值**：这个动作在脚本里出现过的值（≥2 次才算，
        单次出现更像笔误 —— 与 `suggest_shape` 同一条规矩）。

    返回 `[{'key','label','values','source','count'}]`；**认不出就空**
    （没采过引擎事实、没资产表都只是少一项，不是报错）。
    """
    out: list = []
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return out

    # ⓪ **按立绘分开**（最准的一份：不会把别的角色的属性混进来）
    try:
        table = layeredimage_groups(root)
        for tag in sorted(table):
            # ⚠⚠ `<tag>_cc` 这种"角色卡预览用的复制品"**必须标出来**
            #   （2026-10-13 修）：它与本体不同的那一份会照样进清单，于是并排出现
            #
            #       lan  的 expressions（18 个）
            #       lan_cc 的 expressions（16 个）
            #
            #   而 `lan_cc` **不是剧本里能写的图片 tag** —— 编剧按"名字像"点它，
            #   候选里就混进这套预览专用的值。只把 label 写清"它是副本"，
            #   不改任何判定（本体照样在、少了两个表情的副本也必须留着）。
            base = tag[:-3] if tag.endswith('_cc') else ''
            shown = f'{tag}（{base} 的副本）' if base else tag
            for gname in sorted(table[tag]):
                vals = table[tag][gname]
                if vals:
                    out.append({'key': f'layer:{tag}:{gname}',
                                'label': f'{shown} 的 {gname}（{len(vals)} 个）',
                                'values': vals, 'source': '项目里的立绘定义',
                                'count': len(vals)})
    except Exception as e:                       # noqa: BLE001 - 读不到不算错
        swallow('learn_statement.choice_sources.layeredimage', e)

    # ① 立绘属性：真机事实里引擎自己分的组（跨 tag 合并去重 —— 保留，但排后面）
    try:
        from storygraph.core import engine_facts as EF
        path = EF.facts_path(root)
        if path is not None and path.is_file():
            facts = EF.loads(path.read_text(encoding='utf-8'))
            table = EF.value_of(facts, 'attributes', {}) or {}
            merged: dict = {}
            for tag in (table.keys() if isinstance(table, dict) else ()):
                for gname, vals in (EF.groups_of(facts, str(tag)) or {}).items():
                    bucket = merged.setdefault(str(gname or ''), [])
                    for v in vals or ():
                        if str(v) and str(v) not in bucket:
                            bucket.append(str(v))
            for gname, vals in sorted(merged.items()):
                if vals:
                    out.append({'key': f'attr:{gname}',
                                'label': f'立绘属性 · {gname or "未分组"}'
                                         f'（全部角色 · {len(vals)} 个）',
                                'values': vals, 'source': '引擎事实',
                                'count': len(vals)})
    except Exception as e:                       # noqa: BLE001 - 没事实不算错
        swallow('learn_statement.choice_sources.facts', e)

    # ② 角色表（编剧认得的是显示名，所以给显示名）
    try:
        names = sorted(_role_table(root)['names'])
        if names:
            out.append({'key': 'roles', 'label': '角色表',
                        'values': names, 'source': '资产表',
                        'count': len(names)})
    except Exception as e:                       # noqa: BLE001
        swallow('learn_statement.choice_sources.roles', e)

    # ③ 这个动作在真实脚本里用过的值
    try:
        if str(head or '').strip():
            shape = suggest_shape(root, head)
            vals: list = []
            for item in (shape.get('items') or ()):
                for v in (item.get('choices') or ()):
                    if v not in vals:
                        vals.append(v)
            # ⚠⚠ **先滤掉"不是值"的东西**（2026-10-14 修）：
            #   Ren'Py 语句里有一批**修饰词**（`at` / `with` / `behind` /
            #   `onlayer` / `as`）与**注释残渣**（`#…`）—— 它们会被当成
            #   "这一位置用过的值"收进来。用户点「从项目里拿」灌进某一格时，
            #   下拉里就出现根本不能填的值（真机实测 `show` 的 used 里有 `at`）。
            #   ⚠ 只在**跨位置混合**的这一份里滤：按立绘分开的那些是真值。
            vals = [v for v in vals if not _is_not_a_value(v)]
            if vals:
                out.append({'key': 'used',
                            # ⚠ 标签必须**说明它是混的**：这一份把所有位置的值
                            #   合在一起，工具**不可能**知道哪一格该拿哪一份
                            #   （用户 2026-10-13 的现场就是"灌进来一半不能用"）。
                            'label': f'项目里用过的值（{head} · 各位置混在一起）',
                            'values': vals, 'source': '真实脚本',
                            'count': len(vals)})
    except Exception as e:                       # noqa: BLE001
        swallow('learn_statement.choice_sources.used', e)
    return out


#: **不是候选值**的东西（Ren'Py 的**语法词** + 注释残渣）。
#:
#: 依据：这些词在语句里是**结构词**，不是"某一格的值" ——
#: `show lan happy at left with dissolve` 里 `at`/`with` 是语法，
#: `left`/`dissolve` 才是值。
#:
#: 两类：
#:   · **修饰型关键字**（跟在图名后面）：`at` / `with` / `behind` /
#:     `onlayer` / `as`（官方语法 `show_statement -> "show" name
#:     ( "as" name )? ( "onlayer" name )? ( "at" … )? ( "behind" … )?
#:     ( "with" … )?`）；
#:   · **引入型关键字**（替换图名，2026-10-15 加）：`expression` /
#:     `screen` / `layer` —— `show expression <expr>`（表达式）/ `show screen
#:     <屏幕名>` / `show layer <层名>` 三个都是**独立语句形式**，
#:     它们后面的东西不是图名，本身也不可能是图名。
#:
#: ⚠⚠ **修正一条错诊断**（2026-10-15）。旧注释写"加 `expression`/`screen`
#: 会误伤真机里一个**真叫这个名字的角色 ID**" —— 实测那是**错的**：
#: `_role_table(真机)` 的 28 个 id/name 里既没有 `screen` 也没有 `expression`
#: （对照实验见 `.workbuddy/memory/2026-10-15.md`）。
#: 当年"当场变红"的真因是：滤掉它们之后角色格候选从 10 掉到 8，
#: 撞上了 `test_suggests_character_names_for_show` 那条 `len(choices) >= 10`
#: —— 那条断言是**被噪声撑起来的**（10 个候选里有 4 个根本不是角色）。
#: 所以这一版两件事一起修：词表补上引入型关键字，角色格的候选**并上整张角色表**
#: （让 `>=10` 由真实资源满足，而不是由噪声满足）。
#: ⚠⚠ **2026-10-16（用户排期 C 项）**：这一份只回答"这个词根本不可能是一个值"
#: （反推参数名时的取词边界）。**切分/判定目标**由
#: `core.vocabulary.classify_show_words` 一处负责 —— 别再在这里写 split() 口径。
_NOT_A_VALUE = frozenset(
    SHOW_CLAUSE_KEYWORDS | SHOW_INTRO_KEYWORDS | {'True', 'False', 'None'})


def _is_not_a_value(word) -> bool:
    """这个词是不是"根本不是值"（修饰词 / 注释残渣 / 空）？"""
    w = str(word or '').strip()
    if not w:
        return True
    if w in _NOT_A_VALUE:
        return True
    if w.startswith('#'):                        # 行内注释
        return True
    if w.endswith(')') or w.startswith('('):     # 括号残渣（`True)`）
        return True
    return False


__all__ = ['build_declaration', 'build_group', 'choice_sources',
           'declaration_preview', 'group_preview', 'parse_choices',
           'suggest_shape']
