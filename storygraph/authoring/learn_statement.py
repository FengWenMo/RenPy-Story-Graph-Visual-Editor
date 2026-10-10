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

#: 参数名必须是合法标识符（Ren'Py 关键字参数的名字就是标识符）
_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
#: 语句名：允许**空格分隔的多个词**（与 CDS 的 `name="a b"` 同形，后端会拆成别名）
_STMT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\s+[A-Za-z_][A-Za-z0-9_]*)*$')
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
                      block=None, params=None) -> dict:
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
    if not _STMT_RE.match(name):
        return {'ok': False, 'name': name, 'label': '', 'docs': '',
                'block': block, 'columns': [],
                'warnings': [f'"{name}" 不是合法的语句名（只能是字母/数字/下划线，'
                             f'多个词用空格分隔）']}
    columns = []
    seen = set()
    for row in (params or ()):
        if not isinstance(row, dict):
            continue
        cname = str(row.get('name') or '').strip()
        # 容忍"顺手把等号也写进去"：`intensity=3` / `intensity = 3`
        if '=' in cname:
            cname = cname.split('=', 1)[0].strip()
            warnings.append(f'参数名里带了 `=`：已按 "{cname}" 处理')
        if not cname:
            continue
        if not _NAME_RE.match(cname):
            warnings.append(f'参数名 "{cname}" 不是合法标识符 —— 这一行**没写进**'
                            f'声明（表里不会出现这一列）')
            continue
        if cname in seen:
            warnings.append(f'参数 "{cname}" 写了两次 —— 只保留第一次')
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
    return {'ok': True, 'name': name, 'label': str(label or '').strip(),
            'docs': str(docs or '').strip(), 'block': block,
            'columns': columns, 'warnings': warnings}


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

    # 角色表：用来把"裸词"认成角色（候选直接给中文显示名）
    role_names, role_ids, id_to_name = [], set(), {}
    try:
        from storygraph.pipeline.build import load_asset_registry
        reg = load_asset_registry(root)
        if reg is not None:
            for cid, row in (getattr(reg, 'characters', {}) or {}).items():
                cid = str(cid)
                role_ids.add(cid)
                shown = str((row or {}).get('Name')
                            or (row or {}).get('DisplayName')
                            or (row or {}).get('名字') or cid).strip()
                shown = shown.strip('「」『』') or cid
                id_to_name[cid] = shown
                if shown and shown not in role_names:
                    role_names.append(shown)
            out['role_names'] = role_names
    except Exception as e:                       # noqa: BLE001 - 没角色表也能用
        swallow('learn_statement.suggest_shape.roles', e)

    #: 一项的收集器。**键是位置下标** —— 同一个位置必须是同一项：
    #: `outfit lyj …` / `outfit lxj …` 是**同一个第 1 项**（角色），
    #: 我第一版按 `(位置, 名字)` 做键，于是 `lxj` 没被认成角色时
    #: 又冒出一条"对象"，参数表里出现两个第 1 项（出图前当场看出来）。
    slots: dict = {}

    def slot(i, kind, name):
        rec = slots.setdefault(i, {'index': i, 'kind': kind, 'name': name,
                                   'default_name': name, 'examples': [],
                                   'hits': Counter(), 'seen': set(),
                                   'suggest_allowed': False, 'note': ''})
        return rec

    def note_kind(rec, kind, name, note=''):
        """把这一位见过的"更像样的"形态记下来（key > 角色 > 内容 > 对象 > 数字）。"""
        rank = {'key': 4, 'word-role': 3, 'value': 2, 'word': 1, 'number': 0}
        cur = f'{rec["kind"]}-role' if rec.get('note') == '角色表' else rec['kind']
        new = f'{kind}-role' if note == '角色表' else kind
        if rank.get(new, 0) > rank.get(cur, 0):
            rec['kind'] = kind
            rec['name'] = name
            rec['default_name'] = name
            rec['note'] = note

    occurrences = 0
    for fp in files:
        try:
            lines = Path(fp).read_text(encoding='utf-8', errors='replace').splitlines()
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
            for i, tok in enumerate(tokens[1:]):
                kind, shown, key = tok
                is_role = False
                if kind == 'key':
                    rec = slot(i, 'key', key)
                    note_kind(rec, 'key', key)
                    rec['suggest_allowed'] = True          # 作者自己写了名字
                elif kind == 'number':
                    rec = slot(i, 'number', f'第{i + 1}项')
                    note_kind(rec, 'number', f'第{i + 1}项')
                elif kind == 'value':
                    rec = slot(i, 'value', '内容')
                    note_kind(rec, 'value', '内容')
                else:                                       # 裸词：可能是角色
                    is_role = shown in role_ids or shown in role_names
                    default = '角色' if is_role else '对象'
                    rec = slot(i, 'word', default)
                    note_kind(rec, 'word', default,
                              '角色表' if is_role else '')
                    if is_role:
                        rec['suggest_allowed'] = True
                # 角色那一项显示**中文名**（项目里写的是 ID，编剧看的是名字）
                if is_role or (rec['note'] == '角色表'):
                    shown = id_to_name.get(shown, shown)
                rec['hits'][shown] += 1
                rec['seen'].add(shown)
    if not occurrences:
        return out
    out['found'] = True
    out['occurrences'] = occurrences
    for idx in sorted(slots):
        rec = slots[idx]
        examples = [v for v, _n in rec['hits'].most_common(6)]
        rec['examples'] = examples
        # ⚠ **只出现一次的值不算候选**：那更像笔误/一次性的写法，
        #    拿它当"只能填这些"会把编剧的手脚捆住（用户原话：候选非常繁琐）
        choices = [v for v, n in rec['hits'].most_common()
                   if n >= max(2, int(min_choice_hits))]
        rec['choices'] = choices[:24]
        rec['choices_from'] = (rec['note'] if rec['note'] == '角色表'
                               else ('项目里写过的值' if choices else ''))
        rec['suggest_allowed'] = bool(rec['suggest_allowed'] and choices)
        rec['hits'] = dict(rec['hits'].most_common(12))
        rec.pop('seen', None)
        out['items'].append(rec)
    return out


def _split_call_line(text: str) -> list:
    """
    一行脚本 → `[头, 项…]`；项是 `(kind, 显示值, 键名)`。

    `kind`：`'key'`（`mood=happy` 的 `happy`）/ `'value'`（引号里的值）/
    `'number'`（裸数字）/ `'word'`（裸词，可能是角色）。

    刻意**不用** `shlex`：Ren'Py 台词里的引号/中文书名号/转义情况很多，
    这里只需要"够用的分词"，宁可少切也不要切错（切错就是把编剧的台词拆坏）。
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
        j = i
        while j < n and not s[j].isspace() and s[j] not in ',':
            j += 1
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


__all__ = ['build_declaration', 'build_group', 'declaration_preview',
           'group_preview', 'parse_choices', 'suggest_shape']
