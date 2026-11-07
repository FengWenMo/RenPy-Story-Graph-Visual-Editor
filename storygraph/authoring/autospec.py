# -*- coding: utf-8 -*-
"""
从**一行真实剧本**自动拆出参数格子（纯函数，UI / CLI / 测试共用）
==================================================================

用户 2026-11-07（原话）：

> 想一想到底有没有更简便的操作方式，上网嗖一下，谁会填那个逆天的表啊！！！

「逆天的表」指的是自定义模块里那两个声明入口：语法的「格子」要人手加、
每格还要选类型 / 勾"可多值" / 勾"可不填"；技术调用的「参数列」更离谱 ——
要人手写 `转场@0; 层=layer` 这种 DSL。

## 为什么这件事**能**自动做

Ren'Py 的 CDS 语法**没有 schema**：`renpy.register_statement(parse=...)`
里的 parse 函数想怎么吃就怎么吃（`[wiki]`：parse 只是一个吃 lexer 的函数）。
所以"参数形状"只有两个来源：

  1. **parse 函数体**——理论上能读，实际是一段任意 Python，读不准；
  2. **剧本里真实写出来的那一行**——这才是**唯一可靠的证据**。

而工具**已经**扫到了这些行（`statement_registry.discover_undeclared` 会给出
`count` / `samples` / 文件）。也就是说：**答案早就在手里了，却还在问人。**

这一层就是把"手里那行"变成"现成的格子"：切词 → 猜类型 → 给个名字。

## 纪律

  · **只认字面量**：`at left` 里的 `at` 是固定字（`SHOW_CLAUSE_KEYWORDS`），
    `intensity=3` 的关键字名是 `intensity`，引号串里的空格不切 —— 都按
    `pattern.tokenize` 那一份词法（**不另写一套切词**）；
  · **给的是草稿，不是判决**：猜出来的类型/名字全部进界面，人改一个字就覆盖；
  · **空引号算一个位置**（`EMPTY_TOKEN` 的语义），不合并、不跳过；
  · 认不出 head 就不动第一词（`split_sample` 允许 `head=''`）。
"""

from __future__ import annotations

import re

from storygraph.core.vocabulary import SHOW_CLAUSE_KEYWORDS

#: `名字=值` 形态的关键字参数（`intensity=3` / `layer="master"`）。
_KWARG = re.compile(r'^([A-Za-z_]\w*)=(.*)$', re.S)

#: 剥引号后当名字用（太长就截断，界面上一格放不下）。
_NAME_LIMIT = 12

#: 这些词后面跟的是"子句"，词本身是**固定字**（剧本里必须原样出现）。
_CLAUSE_WORDS = frozenset(str(w) for w in SHOW_CLAUSE_KEYWORDS)

#: 已知子句关键字 → `(中文名, 值类型)`。
#:
#: 值类型决定"吃到哪为止"（与 `spec_clauses.CLAUSE_KINDS` 同一套）：
#:   · `word` / `number`：只吃一个词（`behind ye` / `zorder 5`）；
#:   · `words`：吃到下一个子句关键字（`at 左, 右`）；
#:   · `expr` / `transition`：同上，但值里可以有括号/空格（`with Dissolve( 0.3 )`）。
_CLAUSE_META = {
    'at': ('位置', 'words'),
    'with': ('转场', 'transition'),
    'behind': ('挡住', 'word'),
    'zorder': ('层级', 'number'),
    'as': ('别名', 'word'),
    'onlayer': ('层', 'word'),
}

#: 只吃一个词的子句类型。
_SINGLE_WORD_KINDS = ('word', 'number')


def _plain(token: str) -> str:
    text = str(token or '').strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        return text[1:-1]
    return text


def _guess_kind(token: str) -> str:
    """
    一个词 → 格子类型（只给 `keyword` / `variable` 两种，与界面下拉一致）。

    判据刻意保守：**只有"剧本里必须原样出现的词"才算 keyword** ——
    认错了会导致卡片上那一格变成只读，比猜成 variable 难查得多。
    """
    text = str(token or '').strip()
    plain = _plain(text)
    if plain.lower() in _CLAUSE_WORDS and not text.startswith(('"', "'")):
        return 'keyword'
    return 'variable'


def _guess_name(token: str, index: int) -> str:
    """
    一个词 → 这一格默认叫什么（人一眼能认出是哪一格）。

    `intensity=3` → `intensity`；`"闪电"` → `闪电`；`left` → `left`；
    空引号 → `第 n 格`（没内容可取名）。
    """
    text = str(token or '').strip()
    pair = _KWARG.match(text)
    if pair:
        return pair.group(1)
    plain = _plain(text)
    if not plain:
        return f'第{index + 1}格'
    plain = ' '.join(plain.split())
    return plain if len(plain) <= _NAME_LIMIT else plain[:_NAME_LIMIT] + '…'


def split_sample(line, head: str = '') -> tuple:
    """
    一行剧本 → **`(位置格子, 复杂子句)`** 两份草稿。

      · 位置格：`[{'name','label','kind','sample'}]`（`pattern` 吃这个）；
      · 子句：`[{'keyword','name','kind','values'}]`（`spec_clauses` 吃这个）。

    `head` 给了且正好是第一个词 → 摘掉它（`show lan happy` 的 `show`
    不是格子，是语句名）。**只摘一次，且必须整词相等**（不模糊匹配）。

    ## 为什么必须分开认（"准确"的关键）

    `mycue "闪电" at left` 里 `at left` **不是两个位置格**，是一个子句：
      · `at` 是关键字，`left` 是它的值（而且真项目里常是 `at 左, 右` 一串）；
      · 当两个位置格喂给 `pattern`，试切会报"多出 `at`、`left`"（实测：
        `leftover: ['at','left']`）—— **工具说人写错了，其实是工具认错了**；
      · 而 `with Dissolve( 0.3 )` 按空白切会碎成三块，怎么都对不上。

    子句只认**已知那几个**（`_CLAUSE_META`）：自定义语句的关键字是项目自己
    定的，猜不出来 —— 不认识的词老老实实当位置格，别把它藏进子句里。

    ⚠ 子句只在**位置格之后**出现时才认（Ren'Py 的写法就是这样：
    `show eileen happy at left`）。第一个词就是 `at` 的话，说明这个项目把
    `at` 当普通词用，不抢。
    """
    from storygraph.authoring.pattern import tokenize

    text = str(line or '').strip()
    if not text:
        return [], []
    tokens = [str(t) for t in (tokenize(text) or ())]
    want = str(head or '').strip()
    if want and tokens and _plain(tokens[0]) == want:
        tokens = tokens[1:]

    slots: list = []
    clauses: list = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        meta = _clause_meta(token)
        if meta is not None and slots:          # 子句只在位置格之后认
            keyword, (label, kind) = meta
            index += 1
            values = []
            limit = 1 if kind in _SINGLE_WORD_KINDS else len(tokens)
            while index < len(tokens) and len(values) < limit:
                if values and _clause_meta(tokens[index]) is not None:
                    break                        # 下一个子句开始
                values.append(_plain(tokens[index]))
                index += 1
            clauses.append({'keyword': keyword, 'name': label, 'kind': kind,
                            'values': values})
            continue
        empty = token == '\x00' or _plain(token) == ''
        name = f'第{len(slots) + 1}格' if empty else _guess_name(token, len(slots))
        slots.append({
            'name': name,
            'label': name,
            'kind': _guess_kind(token),
            'sample': '' if empty else _plain(token),
        })
        index += 1
    return slots, clauses


def _clause_meta(token):
    """一个词 → `(关键字, (中文名, 类型))`；不是已知子句关键字 → `None`。"""
    text = str(token or '').strip()
    if not text or text[0] in '"\'':
        return None
    plain = _plain(text).lower()
    if plain not in _CLAUSE_WORDS:
        return None
    meta = _CLAUSE_META.get(plain)
    return (plain, meta) if meta else None


def call_columns(line, head: str = '') -> list:
    """
    一行 `$ 函数调用(...)` → **技术调用的参数列草稿**
    （`[{'name','label','position'|'kw'}]`，与 `rules.json` 的 `calls` 同形）。

    位置参数从 0 开始编号；`layer="master"` 这种关键字参数记成 `kw`。
    代价说明写在这里而不是界面上：**位置号是"第几个逗号"，不是"第几个词"** ——
    所以这里用 `call_expr.find_calls` 的实参区间（与读/写参数同一份解析），
    绝不按空格切。
    """
    from storygraph.story.call_expr import find_calls

    hits = find_calls(str(line or ''), str(head or '').strip())
    if len(hits) != 1:
        return []
    columns = []
    for arg in hits[0].args:
        if arg.kind == 'kw':
            columns.append({'name': arg.name, 'label': arg.name, 'kw': arg.name,
                            'sample': arg.value})
        else:
            plain = _plain(arg.value)
            # `Dissolve(0.1)` 这种：名字取函数名（`Dissolve`），别把半截括号
            # 当一个"参数名"塞进表格（人看一眼就知道那一格是什么）。
            call = re.match(r'^([A-Za-z_]\w*)\s*\(', plain)
            if call:
                name = call.group(1)
            elif re.match(r'^\d+(?:\.\d+)?$', plain):
                # 裸数字当列名很难认（`my_wipe·0.6@1`），给个位置名让人改
                name = f'参数{arg.index + 1}'
            else:
                name = plain if len(plain) <= _NAME_LIMIT else plain[:_NAME_LIMIT] + '…'
            columns.append({'name': name or f'第{arg.index + 1}个参数',
                            'label': plain, 'position': int(arg.index),
                            'sample': plain})
    return columns


def teachable_head(line, registry=None) -> tuple:
    """
    这一行**值不值得**"教工具认识它" → `(语句名, 不能教的理由)`。

    可教时第二项是 `''`。判据与 `discover_undeclared` **同一份**
    （都走 `statement_registry.is_known_head`），否则会出现"列表里不显示、
    右键却说可以教"这种自相矛盾。

    界面上这条判据决定右键菜单**显不显示**那一项 —— 不能对一个 `show`
    说"教工具认识它"（那是骂人，也没东西可教）。
    """
    from storygraph.core.vocabulary import statement_head_token
    from storygraph.story.statement_registry import is_known_head

    text = str(line or '').strip()
    if not text:
        return '', '这一行是空的'
    if text.startswith('#'):
        return '', '这一行是注释'
    if text.startswith(('$', 'python:', 'init', 'label', 'screen', 'style',
                        'define', 'default', 'transform', 'image', 'menu')):
        return '', '这一行是 Python / 声明 / 结构行，不是语句'
    if text.endswith(':'):
        return '', '这一行是块头（以冒号结尾）'
    head = statement_head_token(text)
    if not head:
        return '', '看不出这一行以哪个词开头'
    if is_known_head(head, registry):
        return '', f'工具已经认识「{head}」了'
    return head, ''


def guess_call_heads(texts) -> list:
    """
    一堆代码行 → **看起来像"项目自己的调用"的 head**（`[(head, 样例)]`）。

    只回答"哪几个名字反复出现在 `$ 名字(` 里"，不判断它是转场还是别的 ——
    那是人来定的（`calls` 声明里还有"等价效果 / 时长取第几个参数"）。

    排除：`renpy.` 开头的（引擎 API，工具本来就认）、单字母名（多半是变量
    赋值误判）、`print/len/range` 这类内置。
    """
    from storygraph.story.call_expr import find_calls

    skip = {'print', 'len', 'range', 'str', 'int', 'float', 'list', 'dict',
            'set', 'tuple', 'open', 'eval', 'exec', 'getattr', 'setattr',
            'isinstance', 'super', 'enumerate', 'zip', 'sorted', 'min', 'max',
            'sum', 'abs', 'round', 'format', 'type'}
    pattern = re.compile(r'\$\s*([A-Za-z_]\w*)\s*\(')
    seen = {}
    for text in (texts or ()):
        for line in str(text or '').split('\n'):
            m = pattern.match(line.strip())
            if not m:
                continue
            head = m.group(1)
            if head in skip or len(head) < 3:
                continue
            if head in seen:
                seen[head]['count'] += 1
                continue
            if len(find_calls(line, head)) != 1:
                continue
            seen[head] = {'head': head, 'count': 1, 'sample': line.strip()}
    return sorted(seen.values(), key=lambda r: (-r['count'], r['head']))


__all__ = ['call_columns', 'guess_call_heads', 'split_sample',
           'teachable_head']
