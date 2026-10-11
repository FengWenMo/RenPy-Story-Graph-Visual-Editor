"""
**位置序列**：把"自定义写法"从键值对换成"一排有顺序、有类型的格子"
=========================================================================

> 用户（程序员）2026-10-11 的撞墙记录，诊断原话：
> "它的根本错误在于：你试图用一个**扁平的键值对表单**，
>  去描述一个**复杂的树状语法结构**。"
> "这就好比你想教人做菜，但你的表单只给了三个填空：'食材'、'调料'、'火候'，
>  却没有地方让你写'先放油还是先放盐'。"

所以这里定义**位置序列**（`pattern`）—— 顺序、类型、可选、候选值全都放得下：

    pattern = [
        {'kind': 'keyword', 'text': 'show'},
        {'kind': 'variable', 'column': '角色'},
        {'kind': 'choice',  'column': '服装', 'group': 'outfit'},
        {'kind': 'choice',  'column': '神态', 'group': 'expression'},
        {'kind': 'optional', 'text': 'at', 'column': '位置'},
    ]

## 为什么要有 `match_line`

用户卡死的第一步是"**语句名该填什么、这一行会被怎么切**" —— 界面答不出来。
`match_line(pattern, '小明 泳装 开心')` 就是那个答案：它把一行样例切成
**每格吃到的字**，于是界面可以**当场**把切错的地方显示出来
（"顺序不对"、"这一格没东西"、"多出来一段没人认领"）。

⚠ 匹配用**带回溯的贪心**（模式很短，够用）：`optional` 段可以"这次不出现"。
靠"从左往右一路吃"是**错的** —— 那会把 `show a at left` 里的 `at` 当成变量。
"""

from __future__ import annotations

import re

#: 格子的类型（**这是"工具怎么知道小明是变量、泳装是枚举"的答案**）
SLOT_KINDS = ('keyword', 'variable', 'choice', 'optional')

#: 给界面/文档看的中文名（**用词说人话**：不出现 schema/JST 这类实现者词汇）
KIND_LABELS = {
    'keyword': '固定关键字',
    'variable': '任意变量',
    'choice': '从这些里选',
    'optional': '可选的固定段',
}

#: 一个"词"怎么切：非空白连续段（中文没有空格时**整段算一个词**，
#: 所以中文写法的样例要靠人给空格或靠候选值来切 —— 见 `_tokens`）
_TOKEN_RE = re.compile(r'\S+')


def _tokens(line: str, *, options=()) -> list:
    """
    把一行切成"词"。优先用空格；**没有空格时**用候选值列表做二次切分。

    为什么需要二次切分（中文项目的现实）：`show小明泳装开心` 没有空格，
    一眼看不出边界；但如果我们知道候选值是 `泳装/校服/开心/害羞…`，
    就能按"最长匹配"把它们切出来。⚠ 切不出来就**不硬切**（返回整段），
    让界面显示"这一格吃掉了整段"，而不是猜错。
    """
    text = str(line or '').strip()
    if not text:
        return []
    if re.search(r'\s', text):
        return _TOKEN_RE.findall(text)
    # 无空格：按候选值最长匹配切（**只在真的切得出来时才用**）
    words = sorted({str(o) for o in (options or ()) if str(o).strip()},
                   key=len, reverse=True)
    if not words:
        return [text]
    out, i = [], 0
    while i < len(text):
        hit = next((w for w in words if text.startswith(w, i)), '')
        if hit:
            out.append(hit)
            i += len(hit)
        else:
            out.append(text[i])
            i += 1
    return out


def parse_pattern(raw) -> list:
    """
    规范化/校验一串格子（**纯函数**，坏格子直接丢掉并标出来）。

    返回 `[slot…]`，每个 slot 一定是 `{'kind', 'text', 'column', 'group',
    'options', 'problems'}` 齐全的形状 —— 界面可以直接照它渲染。
    """
    out = []
    for i, item in enumerate(raw or ()):
        slot = {'kind': '', 'text': '', 'column': '', 'group': '',
                'options': [], 'problems': []}
        if isinstance(item, dict):
            kind = str(item.get('kind') or '').strip()
            slot['text'] = str(item.get('text') or '').strip()
            slot['column'] = str(item.get('column') or item.get('label')
                                 or '').strip()
            slot['group'] = str(item.get('group') or '').strip()
            opts = item.get('options') or item.get('choices') or []
            if isinstance(opts, str):
                opts = [o.strip() for o in opts.replace('，', ',').split(',')]
            slot['options'] = [str(o).strip() for o in opts if str(o).strip()]
            # ⚠ **旧形状**（`{'name','label','choices'}`）要继续认（判据压着）：
            #   旧 `name` 是"剧本里怎么写的那串字" ⇒ 对应新模型的**固定关键字**；
            #   旧 `label` 是表格列名 ⇒ `column`；旧 `choices` ⇒ `options`。
            if not kind and item.get('name'):
                kind = 'keyword'
                slot['text'] = str(item.get('name')).strip()
        else:
            kind = str(item or '').strip()
        if kind not in SLOT_KINDS:
            # 兼容旧形状：`foo=`（带等号的）猜成 variable，纯词猜成 keyword
            if kind.endswith('='):
                kind, slot['column'] = 'variable', kind[:-1]
            elif kind:
                kind, slot['text'] = 'keyword', kind
            else:
                slot['problems'].append('这一格没写类型')
                kind = 'variable'
        slot['kind'] = kind
        if kind in ('keyword', 'optional') and not slot['text']:
            slot['problems'].append('固定段得写清是哪个字')
        if kind in ('variable', 'choice', 'optional') and not slot['column']:
            slot['problems'].append('这一格要有个列名（表格里显示什么）')
        if kind == 'choice' and not (slot['options'] or slot['group']):
            slot['problems'].append('"从这些里选"要么给候选值，要么挂一个词组')
        out.append(slot)
    return out


def _cell(slot: dict, text: str) -> dict:
    return {'kind': slot['kind'], 'text': text, 'column': slot.get('column', ''),
            'group': slot.get('group', ''), 'ok': True}


def match_line(pattern, line, *, groups=None) -> dict:
    """
    **把一行样例切成格子** —— 界面靠它当场告诉用户"你填的顺序对不对"。

    返回::

        {'ok': bool,
         'cells': [{'kind','text','column','group','ok'}…],   # 每格吃到的字
         'leftover': [没被认领的词…],                          # 多出来的
         'missing': [第几格没吃到东西…],
         'reason': 人话（不 ok 时说明卡在哪）}

    ⚠ 匹配是**带回溯**的：`optional` 段允许"这次没出现"。
    一路贪心是错的 —— 会把 `show a at left` 的 `at` 当变量吃掉。
    """
    slots = parse_pattern(pattern)
    groups = groups or {}
    options = []
    for s in slots:
        options.extend(s.get('options') or [])
        g = groups.get(s.get('group') or '')
        if isinstance(g, dict):
            options.extend(g.get('options') or [])
    #: 无空格时也**尽量**切得出来：候选值 + 固定关键字一起当"可切的整词"
    #: （中文项目的现实：`show小明泳装开心` 没有空格）
    options = list(options)
    for s in slots:
        if s.get('text'):
            options.append(s['text'])
    tokens = _tokens(line, options=options)

    def walk(si: int, ti: int):
        """从第 si 格、第 ti 个词开始尝试；成功返回 (cells, ti)，失败 None。"""
        if si == len(slots):
            return ([], ti)
        slot = slots[si]
        kind = slot['kind']
        # ---- 固定关键字：必须一模一样 ----
        if kind == 'keyword':
            if ti < len(tokens) and tokens[ti] == slot['text']:
                rest = walk(si + 1, ti + 1)
                if rest:
                    return ([_cell(slot, tokens[ti])] + rest[0], rest[1])
            return None
        # ---- 可选段：`at 位置` —— **出现就吃两个词（关键字+值），不出现就跳过** ----
        #   ⚠ 只有一条路（一路贪心）是错的：那样 `at` 会被变量格吃掉。
        #   这里**两条路都试**（回溯），先试"出现"。
        if kind == 'optional':
            if (ti + 1 < len(tokens) and tokens[ti] == slot['text']
                    and tokens[ti + 1] != ''):
                rest = walk(si + 1, ti + 2)
                if rest:
                    eaten = f'{tokens[ti]} {tokens[ti + 1]}'
                    return ([_cell(slot, eaten)] + rest[0], rest[1])
            return walk(si + 1, ti)                   # 这次不出现
        # ---- 变量 / 枚举：吃一个词 ----
        if ti >= len(tokens):
            return None
        word = tokens[ti]
        if kind == 'choice':
            allowed = list(slot.get('options') or [])
            g = groups.get(slot.get('group') or '')
            if isinstance(g, dict):
                allowed.extend(g.get('options') or [])
            if allowed and word not in allowed:
                return None                            # 不在候选里 → 不是这一格
        rest = walk(si + 1, ti + 1)
        if rest:
            return ([_cell(slot, word)] + rest[0], rest[1])
        return None

    got = walk(0, 0)
    if got is None:
        # 失败要说清"卡在第几格"，而不是只说"不匹配"
        return {'ok': False, 'cells': [], 'leftover': tokens, 'missing': [],
                'reason': '这一行跟你的格子对不上 —— 检查顺序、关键字写法，'
                          '或者是不是少了/多了哪一段'}
    cells, used = got
    return {'ok': used >= len(tokens), 'cells': cells,
            'leftover': tokens[used:],
            'missing': [i for i, c in enumerate(cells) if not str(c['text']).strip()],
            'reason': '' if used >= len(tokens) else
                      f'后面还有 {len(tokens) - used} 个词没人认领：'
                      + ' '.join(tokens[used:])}


def columns_of(pattern) -> list:
    """这一串格子在表格里会产生哪几列（**按顺序**，去重）。"""
    out = []
    for s in parse_pattern(pattern):
        col = s.get('column') or ''
        if col and col not in out:
            out.append(col)
    return out


def groups_used(pattern) -> list:
    """这一串格子用到了哪些词组（去重、按顺序）—— 界面据此提示"还得建词组"。"""
    out = []
    for s in parse_pattern(pattern):
        g = s.get('group') or ''
        if g and g not in out:
            out.append(g)
    return out


def problems_of(pattern) -> list:
    """把所有格子的毛病汇总成**人话**清单（界面直接显示，不用自己拼）。"""
    out = []
    for i, s in enumerate(parse_pattern(pattern), 1):
        for p in s.get('problems') or []:
            out.append(f'第 {i} 格：{p}')
    return out


__all__ = ['KIND_LABELS', 'SLOT_KINDS', 'columns_of', 'groups_used',
           'match_line', 'parse_pattern', 'problems_of']
