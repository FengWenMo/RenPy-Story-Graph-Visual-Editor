"""
`storygraph.story.spec_clauses`
==============================
**复杂子句**：项目声明「某条语句可以带哪些关键字子句」→ 读值 / 定点写回。

为什么需要这一层（用户 2026-10-30）
-----------------------------------
「自定义语句」原来只能声明**位置序列**（`pattern`：一格一个词 / 一格多个词 /
可选的固定段）。这对 `swim_suit lan 泳装` 够用，但对真项目里常见的复杂写法
不够：

    mycue "开场" at 左, 右 behind ye with Dissolve( 0.3 ) zorder 5
    camera pan from (0, 0) to (1, 1) with my_wipe("left", 0.4)

这几段不是"第 N 个词"能表达的：
  · `at` 后面是**逗号分隔的一串**；
  · `with` 后面是**表达式**，里面可以有空格、括号、引号；
  · 子句可以**不出现**，也可以**任意顺序**（Ren'Py 的 `show` 就是这样）；
  · `Dissolve( 0.3 )` 这种带空格的写法，按空白切词会切碎。

所以声明里多一段 `clauses`：

    "clauses": [
      {"keyword": "at",   "name": "位置", "kind": "words"},
      {"keyword": "with", "name": "转场", "kind": "transition"},
      {"keyword": "behind","name": "挡住", "kind": "word"}
    ]

本模块只做两件事（**纯函数、不碰 UI、不碰文件**）：

  1. `clause_values(head, clauses)`：从头行里读出每个子句的值；
  2. `set_clause_value(head, clauses, name, value)`：**只改那一段**。

写回的纪律（与 `spec_args` 的"改值才写"同源）
-----------------------------------------------
  · 值没变 → **原样返回入参字符串**（逐字节相同，铁律 #6）；
  · 有这一句 → 只替换 `关键字 … 值` 里**值**的那一段，别处的空格/引号/
    其他子句一个字节都不动；
  · 没有这一句、人填了值 → 按声明顺序插在"下一个出现过的子句"前面；
    都没有就插在行尾（块语句插在结尾的 `:` 之前）；
  · 清空 → 连同关键字一起删掉，并只吃掉一个分隔空格；
  · 认不出（关键字不是完整词 / 在引号或括号里 / 声明里没有它）→ **不动**。

⚠ **关键字只在"顶层"认**：`mycue "a at b"` 里的 `at` 在引号内，
`my_wipe( at , 0.4 )` 里的 `at` 在括号内 —— 都不是子句关键字。
"""

from __future__ import annotations

import re

#: 子句值的类型。决定"读到哪为止"和界面怎么提示。
#:
#:   · `word`      ：一个词（`behind ye` / `zorder 5`）；
#:   · `words`     ：一串词，直到下一个子句关键字（`at 左, 右`）；
#:   · `expr`      ：一段表达式，可含空格/括号/引号（`with Dissolve( 0.3 )`）；
#:   · `transition`：同 `expr`，但**过场预览会按它演**（见 `app.stage`）；
#:   · `number`    ：数字（`fade 0.5`；读值原样保留，不转 float）。
CLAUSE_KINDS = ('word', 'words', 'expr', 'transition', 'number')

#: 界面上给人看的类型名（`dialog_learn` 的下拉用它）。
CLAUSE_KIND_LABELS = {
    'word': '一个词',
    'words': '多个词（逗号分隔）',
    'expr': '一段表达式（可含括号/空格）',
    'transition': '转场表达式（预览按它演）',
    'number': '数字',
}

#: 关键字里不许出现的字符（出现就说明这不是一个"词"）
_BAD_KEYWORD_CHARS = ('"', "'", '(', ')', '[', ']', '{', '}', ':', ',', '#', '=')

#: 控制字符（与 `beat_model._CONTROL_CHARS_RE` 同一口径；这里不 import 它，
#: 避免 `story -> story` 的循环 —— 本模块只做纯文本处理）。
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]+')


def _one_line(text) -> str:
    """压成一行（换行/制表符会改坏头行）。"""
    return _CONTROL_RE.sub(' ', str(text or '')).strip()


# ---------------------------------------------------------------------------
#  声明：规范化
# ---------------------------------------------------------------------------

def normalize_clause(item, *, reserved_names=()) -> dict:
    """
    清单/表单里的一项 → 规范形状；不合法返回 `{}`（调用方跳过并报原因）。

    返回 `{keyword, name, label, kind, optional, choices}`。注意：
      · `keyword` 是**脚本里的字**（`at` / `with` / `behind`）；
      · `name` 是**表格列名**（中文天经地义，`位置` / `转场`）；
      · `label` 可留空（= 与 `name` 相同）。
    """
    if not isinstance(item, dict):
        return {}
    keyword = _one_line(item.get('keyword') or item.get('text') or '')
    if not keyword or any(ch.isspace() for ch in keyword):
        return {}
    if any(ch in keyword for ch in _BAD_KEYWORD_CHARS):
        return {}
    name = _one_line(item.get('name') or item.get('label') or keyword)
    if not name or any(ch in name for ch in _BAD_KEYWORD_CHARS):
        return {}
    if name in set(reserved_names or ()):
        return {}
    kind = str(item.get('kind') or 'word').strip().lower()
    if kind not in CLAUSE_KINDS:
        kind = 'word'
    choices = []
    for raw in (item.get('choices') or item.get('options') or ()):
        text = _one_line(raw)
        if text and text not in choices:
            choices.append(text)
    return {
        'keyword': keyword,
        'name': name,
        'label': _one_line(item.get('label') or ''),
        'kind': kind,
        'optional': bool(item.get('optional', True)),
        'choices': choices,
    }


def normalize_clauses(raw, *, reserved_names=(), warnings: list = None) -> list:
    """
    一段声明 → 规范子句列表（按出现顺序；同名/同关键字**只留第一条**）。

    `warnings` 非空时把丢弃原因追加进去 —— 后端"坏条目跳过"是静默的，
    表单这一层必须出声（与 `learn_statement.build_declaration` 同一条纪律）。
    """
    out = []
    seen_kw = set()
    seen_name = set()
    for i, item in enumerate(raw or ()):
        clause = normalize_clause(item, reserved_names=reserved_names)
        if not clause:
            if warnings is not None:
                warnings.append(f'第 {i + 1} 条子句写得不完整（关键字/列名'
                                f'不能带空格、引号、括号、冒号），已跳过')
            continue
        if clause['keyword'] in seen_kw:
            if warnings is not None:
                warnings.append(f'子句关键字 "{clause["keyword"]}" 写了两次 —— '
                                f'只保留第一次')
            continue
        if clause['name'] in seen_name:
            if warnings is not None:
                warnings.append(f'子句列名 "{clause["name"]}" 写了两次 —— '
                                f'只保留第一次')
            continue
        seen_kw.add(clause['keyword'])
        seen_name.add(clause['name'])
        out.append(clause)
    return out


def clause_by_name(clauses, name) -> dict:
    """按**列名**找子句（也认关键字本身，方便命令行/表格只写 `at`）。"""
    target = str(name or '').strip()
    if not target:
        return {}
    for clause in (clauses or ()):
        if not isinstance(clause, dict):
            continue
        if target in (str(clause.get('name') or '').strip(),
                      str(clause.get('keyword') or '').strip()):
            return clause
    return {}


# ---------------------------------------------------------------------------
#  文本扫描：顶层词、深度、行尾
# ---------------------------------------------------------------------------

def _mask_non_code(text: str) -> str:
    """
    字符串字面量与注释 → 空格（换行保留），长度不变。

    这样找"行尾的 `:` / 注释"只命中真代码，不会被 `mycue "x:"` 骗到。
    """
    n = len(text)
    out = list(text)
    i = 0
    quote = ''
    while i < n:
        ch = text[i]
        if quote:
            out[i] = '\n' if ch == '\n' else ' '
            if ch == '\\':
                if i + 1 < n:
                    out[i + 1] = '\n' if text[i + 1] == '\n' else ' '
                i += 2
                continue
            if ch == quote:
                quote = ''
            i += 1
            continue
        if text.startswith('"""', i) or text.startswith("'''", i):
            triple = text[i:i + 3]
            end = text.find(triple, i + 3)
            stop = n if end < 0 else end + 3
            for j in range(i, stop):
                if text[j] != '\n':
                    out[j] = ' '
            i = stop
            continue
        if ch in '"\'':
            quote = ch
            out[i] = ' '
            i += 1
            continue
        if ch == '#':
            while i < n and text[i] != '\n':
                out[i] = ' '
                i += 1
            continue
        i += 1
    return ''.join(out)


def _depth_map(text: str) -> list:
    """每个字符位置**开始前**的括号深度（引号里的括号不算）。"""
    n = len(text)
    depth = [0] * n
    d = 0
    quote = ''
    i = 0
    while i < n:
        ch = text[i]
        if quote:
            depth[i] = d
            if ch == '\\' and i + 1 < n:
                depth[i + 1] = d
                i += 2
                continue
            if ch == quote:
                quote = ''
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            depth[i] = d
            i += 1
            continue
        if ch in '([{':
            depth[i] = d
            d += 1
        elif ch in ')]}':
            d = max(0, d - 1)
            depth[i] = d
        else:
            depth[i] = d
        i += 1
    return depth


def _comment_start(text: str) -> int:
    """第一个**顶层** `#` 的下标（引号里的 `#` 不算）；没有 → `len(text)`。"""
    n = len(text)
    i = 0
    quote = ''
    while i < n:
        ch = text[i]
        if quote:
            if ch == '\\' and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = ''
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            i += 1
            continue
        if ch == '#':
            return i
        i += 1
    return n


def _line_limit(text: str) -> int:
    """
    头行的"有效结尾"：结尾的块冒号 / 行尾注释之前。

    `mycue "x" at left:` 的值扫描不能把 `:` 吃进值里；
    `mycue "x" # 备注` 也不能。
    """
    depth = _depth_map(text)
    limit = _comment_start(text)
    while limit > 0 and text[limit - 1] in ' \t':
        limit -= 1
    # 结尾是块冒号时再往前让一格（值扫描不能把 `:` 吃进去）。
    if limit > 0 and text[limit - 1] == ':' and depth[limit - 1] == 0:
        limit -= 1
    return limit


def _token_spans(text: str) -> list:
    """词区间 `[(起, 止)…]` —— 与卡片/试切**同一个词法层**（`pattern.token_spans`）。"""
    try:
        from storygraph.authoring.pattern import token_spans
        return [(int(a), int(b)) for a, b in token_spans(text) if b > a]
    except Exception:                       # noqa: BLE001 - 词法层不可用时退化为空白切
        spans = []
        for m in re.finditer(r'\S+', text):
            spans.append((m.start(), m.end()))
        return spans


def _trim(span_text: str, start: int) -> tuple:
    """`(原文, 起点)` → 去掉首尾空白后的 `(值, 新起点, 新终点)`。"""
    left = len(span_text) - len(span_text.lstrip())
    right = len(span_text.rstrip())
    return span_text.strip(), start + left, start + right


def clause_spans(head, clauses) -> list:
    """
    头行里**找到的子句**（按出现顺序）→::

        [{'keyword','name','kind',
          'start','end',              # 关键字..值 的整段（含中间空白）
          'value_start','value_end',  # 值本身的字符区间
          'value'}]

    只认**顶层、完整词**的关键字（引号/括号里的不算）。同名子句出现多次时
    全部列出来；`clause_values` 只取第一条（不猜哪条算数）。
    """
    text = str(head or '')
    items = [c for c in (clauses or ()) if isinstance(c, dict)
             and str(c.get('keyword') or '').strip()]
    if not text or not items:
        return []
    by_kw = {str(c.get('keyword')).strip(): c for c in items}
    limit = _line_limit(text)
    depth = _depth_map(text)
    # ⚠ 词法层喂**掩码后的文本**：注释里的 `at`、引号里的 `at`、
    #   括号里的 `at` 都不该被当成子句关键字（字面量已经被换成空格，
    #   长度不变，所以区间仍能切回原文）。
    spans = [s for s in _token_spans(_mask_non_code(text)) if s[0] < limit]
    found = []
    for idx, (start, end) in enumerate(spans):
        if start >= limit or end > limit:
            continue
        if depth[start] != 0:
            continue                         # 括号里：不是子句
        word = text[start:end]
        if word.endswith(':'):
            word = word[:-1]
        if word not in by_kw:
            continue
        next_start = limit
        for n_start, n_end in spans[idx + 1:]:
            if n_start >= limit:
                break
            if depth[n_start] != 0:
                continue
            n_word = text[n_start:n_end]
            if n_word.endswith(':'):
                n_word = n_word[:-1]
            if n_word in by_kw:
                next_start = n_start
                break
        raw = text[end:next_start]
        value, v_start, v_end = _trim(raw, end)
        clause = by_kw[word]
        found.append({
            'keyword': word,
            'name': str(clause.get('name') or word),
            'kind': str(clause.get('kind') or 'word'),
            'start': start,
            'end': end,
            'value_start': v_start,
            'value_end': v_end,
            'value': value,
        })
    return found


def clause_values(head, clauses) -> dict:
    """
    `{列名: 值}` —— 只放**真的出现过的**子句（没出现 = 不放进结果，不猜空）。

    值按类型收口：
      · `word` / `number`：去掉首尾空白与多余逗号（`at 左,` → `左`）；
      · `words` / `expr` / `transition`：原样（内部空格保留，读起来就是作者写的）。
    """
    out = {}
    for hit in clause_spans(head, clauses):
        value = hit['value']
        if hit['kind'] in ('word', 'number'):
            value = value.strip().strip(',').strip()
        if hit['name'] in out:
            continue                         # 同名出现两次：只取第一条
        out[hit['name']] = value
    return out


def clause_value(head, clause) -> str:
    """单个子句的值（没有 → 空串）。"""
    if not isinstance(clause, dict):
        return ''
    return clause_values(head, [clause]).get(
        str(clause.get('name') or clause.get('keyword') or ''), '')


# ---------------------------------------------------------------------------
#  写回：只动那一段
# ---------------------------------------------------------------------------

def _insert_at(head: str, clauses, clause, value: str) -> str:
    """把 `关键字 值` 插到**声明顺序里下一个出现过的子句**前面（都没有就插行尾）。"""
    text = str(head or '')
    limit = _line_limit(text)
    order = [str(c.get('name') or '') for c in (clauses or ()) if isinstance(c, dict)]
    try:
        pos_in_order = order.index(str(clause.get('name') or ''))
    except ValueError:
        pos_in_order = len(order)
    found = clause_spans(text, clauses)
    after = [f for f in found
             if str(f.get('name')) in order
             and order.index(str(f.get('name'))) > pos_in_order]
    piece = f"{clause['keyword']} {value}".strip()
    if after:
        at = min(int(f['start']) for f in after)
        return text[:at] + piece + ' ' + text[at:]
    body = text[:limit].rstrip()
    tail = text[limit:]
    sep = ' ' if body else ''
    return body + sep + piece + tail


def _remove_at(head: str, hit) -> str:
    """删掉一段子句（含关键字），只吃掉一个分隔空格。"""
    text = str(head or '')
    start, end = int(hit['start']), int(hit['value_end'] if hit['value']
                                        else hit['end'])
    if start > 0 and text[start - 1] in ' \t':
        start -= 1
    elif end < len(text) and text[end] in ' \t':
        end += 1
    return text[:start] + text[end:]


def set_clause_value(head, clauses, name, value) -> str:
    """
    把 `name` 这个子句的值改成 `value`；返回**新的头行**。

    纪律见模块 docstring。这里再强调三条边界：
      · 声明里没有这个子句 → **原样返回**（绝不按 `name=value` 拼一个
        项目根本不认识的参数 —— 那是"改坏脚本"）；
      · 值没变（去空白后相等）→ 原样返回（保证零改动逐字节相同）；
      · 值里带换行/控制字符 → 压成一行（头行必须是一行）。
    """
    text = str(head or '')
    clause = clause_by_name(clauses, name)
    if not clause:
        return text
    new_value = _one_line(value)
    hits = [h for h in clause_spans(text, clauses)
            if str(h.get('name')) == str(clause.get('name'))]
    if hits:
        hit = hits[0]
        old = str(hit.get('value') or '')
        if old.strip() == new_value.strip():
            return text
        if not new_value:
            return _remove_at(text, hit)
        return (text[:int(hit['value_start'])] + new_value
                + text[int(hit['value_end']):])
    if not new_value:
        return text
    return _insert_at(text, clauses, clause, new_value)


def declared_transition_clauses(clauses) -> list:
    """声明里**会被预览消费**的子句（`kind='transition'`）。"""
    return [c for c in (clauses or ())
            if isinstance(c, dict)
            and str(c.get('kind') or '') == 'transition']


__all__ = [
    'CLAUSE_KINDS', 'CLAUSE_KIND_LABELS',
    'normalize_clause', 'normalize_clauses', 'clause_by_name',
    'clause_spans', 'clause_values', 'clause_value', 'set_clause_value',
    'declared_transition_clauses',
]
