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

## 怎么切：**词法 → 全局最优分配**（2026-10-17 第二次重构）

> 用户原话（第一次）："停止用正则表达式，改为使用分词器思路重构整个匹配代码。"
> "`base_swimsuit` 根本不在角色的候选里，它就绝对不会被角色格子吃掉！"
> 用户原话（第二次，把标准抬到工程级）："必须处理引号包裹 / 必须兼容脏空格 /
> 禁止暴力回溯 / 精确到词的报错路由。"

所以现在是**两层**，缺一层都会在真输入上塌：

1. **词法层（`_atoms` + `_split_atom`）**：一行 → 词。
   · 空白只是分隔符 —— **连续空格、Tab、全角空格、零宽空格都算一个**；
   · **引号里的空白不算分隔符**（`"base swimsuit"` 是**一个**词），
     而且**引号本身不进词**（值里带着引号会被原样写回 `.rpy`，比切不上更坏）；
   · **空引号**（`""`）是一个**占位**（`EMPTY_TOKEN`）：它不是值（Ren'Py 里
     那是空属性名、跑不起来），但**占住那个位置** —— 后面的值一格都不许前移
     （以前 `if text:` 把它丢掉，于是整行右移一格、还可能报绿灯）；
   · 中文没有空格时按**候选词典**切（`泳装开心` → `泳装` / `开心`），
     但**只在词典能把它盖满时**才切 —— 否则 `lan_2` 会被拆成 `lan` + `_2`，
     一个本来很正常的变量名就散了。
2. **分配层（`_spans`）**：词 → 每格占哪几个词，**整行总代价最小**的那一种。
   代价只看一件事：**这个词是不是这一格的候选**（自己的 0 / 谁都不认识 2 /
   别的格子的 20）。于是"`base_swimsuit` 不会被角色吃掉"不是靠规则特判，
   而是代价最小的解本来就长那样。

   两条被真输入逼出来的补充，都对**所有**取值格生效（不再分"两种策略"）：

   · **只吃自己人**（候选里的词）。`dirty base_swimsuit` 里那个垃圾词绝不许被
     焊进值里 —— 值和垃圾混在一起，写进库就是一条不存在的服装；
   · **可以跳过前面的垃圾词**（`SKIP_JUNK`）：`lan dirty base_swimsuit happy`
     的正确排版是服装=`base_swimsuit`、`dirty` 晾着被点名，不是把两个词焊起来。

   ⚠⚠ 2026-10-17 第二轮攻击时**取消了"两套策略"**（`order-first` / `candidates`）。
     两套策略 = 试切那一栏和卡片读出来可以**不是同一个值** ——
     用户盯着试切改顺序，改的是 `junk` 那个位置，库里存的却是另一套。
     "跳过垃圾词去认后面的候选"本来就该所有人受益，于是它进了代价表，
     两处**只剩一份实现、一个答案**（判据
     `test_cut_tokenizer_attack2::test_试切与卡片读值不许对同一行给出不同的格子值`）。

### 这一版的四条硬指标（都写在代码里，也都有判据钉着）

| 指标 | 落点 |
|---|---|
| 引号包裹 = 一个词、且值里不带引号 | `_atoms`（`_QUOTE_PAIRS`） |
| 空引号（`""`）= 输入错误 + **占住位置**，后面的值不许前移 | `EMPTY_TOKEN`、`assign_tokens` 的 dead 两趟 |
| 脏空格（Tab / 全角 / 连写）不产生空串 | `_atoms`（`_WS_RE`）、`_split_atom` |
| 不许暴力回溯（试切必须一瞬间） | `_spans` 的**备忘录**：每个 `(第几格, 第几个词)` 只算一次 |
| 报错**精确到词**（第几个词 × 第几格） | `_report` + `match_line` 的 `reason` |
| "第几个词"按**人敲的空格**数，不按词的下标 | `_tokens_meta` 的 `words` / `n_words` |
| 属性格**可填可不填**（`show lan happy` 合法） | `required` + `with_optional_defaults`（`show/scene/hide`） |

⚠ 备忘录不是"优化"，是**正确性**的一部分：`_spans` 的 `multi` 分支要问
"这一格吃 0、1、2……个词各会怎样"，朴素写法是指数级（`分支^格子数`）；
这里每个 `(格子, 词)` 只算一次，状态数 = `格子数 × 词数`，多值格那一段再扫一遍词
→ 最坏 `O(格子数 × 词数²)`，**多项式、没有回溯**。实测一行 300 个词（5 格、
4 格是"可有多个"且词全在候选里，最坏形状）约 0.15 秒；真实剧本一行 5–20 个词
是微秒级。判据 `test_cut_tokenizer_hell`（200 词卡 2 秒）与
`test_cut_tokenizer_edge`（300 词卡 1.5 秒）两处压着它。
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


# ===========================================================================
#  一、词法层：一行 → 词
# ===========================================================================

#: 引号对（开 → 闭）：**只认 Ren'Py 的字符串引号**（`"` 和 `'`）。
#:
#: 为什么词法层必须认引号（2026-10-17 用户点名，也是真剧本的形状）：
#:   `show lan "base swimsuit" happy` 里的 `base swimsuit` 是**一个**属性名。
#:   `line.split()` 会把它切成两个词 → 「服装」只拿到 `"base`、后面整体右移；
#:   更糟的是切出来的值**带着引号**，写回 `.rpy` 就成了一个带引号的属性名
#:   —— 那比"切不上"危险得多，因为它会**报成功**。
#:
#: ⚠ 全角引号（`“”`）、直角引号（`「」`）、书名号在剧本里是**普通字符**
#:   （`mycue 「闪电」` 里那两个括号是内容的一部分）—— 把它们当引号剥掉
#:   会**改掉人的字**，所以一个都不收。
_QUOTE_PAIRS = {'"': '"', "'": "'"}

#: 一切"看起来是空白"的字符：`\s` 已经覆盖半角、Tab、换行、全角空格（U+3000）、
#: 不换行空格（U+00A0）等；额外补上**零宽空格**（U+200B）和 BOM（U+FEFF）
#: —— 它们从网页/表格里粘过来时肉眼看不见，却能把一个词劈成两个。
#:
#: ⚠ 这里是 `+`：**连续多个脏空格只算一个分隔符**，绝不会切出空串
#:   （`'a  b'.split(' ')` 会给你一个 `''`，那个空串正是后面全崩的起点）。
_WS_RE = re.compile(r'[\s\u200b\ufeff]+')

#: **空引号**（`""` / `''` / `"   "`）在词流里的占位符。
#:
#: 为什么占位而不是丢掉（2026-10-18 第三轮攻击修，用户点名的第一条）：
#:
#:     show lan "" base_swimsuit happy
#:
#: 人写的是**四个位置**（第 2 个是空的）。以前 `_atoms` 里 `if text:` 把它连同
#: 毛病一起吞掉 → 3 个词对 3 个值格 → `服装=base_swimsuit`、`表情=happy`，
#: **还报绿灯**：人看到的是一条切得整整齐齐、但整行右移了一格的结果。
#:
#: 它同时是一个**输入错误**，不是"这一格留空"的写法：Ren'Py 里
#: `show lan "" happy` 的 `""` 是一个**空属性名**，跑不起来。想留空就别写那个词
#: （`show lan happy` → 服装空，那是绿测）。
#:
#: 取值用 `\x00`：真实的人名/属性名里不可能出现它，于是它既不会被任何人
#: 认成候选，也不会混进写回原文（`_span_text` 把它滤掉 → 那一格的值是 `''`）。
EMPTY_TOKEN = '\x00'


def _atoms(line):
    """
    一行 → (`词` 列表, `毛病` 列表)。**按字符走一遍，是唯一知道引号在哪的一层。**

    词 = `{'text', 'quoted', 'empty', 'word', 'start', 'end'}`：
      · `text` **已经去掉引号**、内部连续空白压成一个空格；
      · `quoted=True` 表示它是引号包起来的 —— 后面**不再按候选切**
        （引号的意思就是"这一个词，别再动它"）；
      · `empty=True` 是**空引号**：它**占一个词的位置**，但值是空的
        （见 `EMPTY_TOKEN`）；
      · `word` = **第几个词**（1 起，按**人敲的空格**数）—— 报错说"第几个词"
        就靠它（中文连写的一段算一个词，即使后面按候选又切细了）；
      · `start` / `end` = 这一词在**原文**里的字符区间（引号算在里面）——
        写回那一侧靠它把 `""` 换成真正的值。

    毛病 = `{'kind', 'at', 'quote', 'text', 'why'}`，`kind` 两种：
      · `'unclosed'` —— **引号没关上**（`show lan "base swimsuit`）：否则后半行
        会被悄悄吞成一个词，而人看到的是一句"切不上"，完全想不到是少了一个引号；
      · `'empty'` —— **引号里没有值**（`""`）：报出来，而且那个位置**留着**
        （`EMPTY_TOKEN`），后面的值才不会被挤到前一格去。
    """
    raw = str(line or '')
    out: list = []
    problems: list = []
    buf: list = []
    head = 0                       # 当前 buf 第一个字符在 raw 里的下标

    def flush(end):
        if not buf:
            return
        text = _WS_RE.sub(' ', ''.join(buf)).strip()
        buf.clear()
        if text:
            out.append({'text': text, 'quoted': False, 'empty': False,
                        'word': len(out) + 1, 'start': head, 'end': end})

    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch in _QUOTE_PAIRS:
            flush(i)
            close = _QUOTE_PAIRS[ch]
            j, inner, closed = i + 1, [], False
            while j < n:
                cj = raw[j]
                if cj == '\\' and j + 1 < n:
                    # 转义：**两个字符原样保留**（不做解码，绝不改写人的字）
                    inner.append(cj)
                    inner.append(raw[j + 1])
                    j += 2
                    continue
                if cj == close:
                    closed, j = True, j + 1
                    break
                inner.append(cj)
                j += 1
            text = _WS_RE.sub(' ', ''.join(inner)).strip()
            if not closed:
                # ⚠ 报错要带**词的序号**（`at` 是 0 起的），而且交给
                #   `match_line` 去拼句子 —— 界面会摘掉行首的动作名，
                #   序号得在那之后再补偏移，否则"第 2 个词"指的是人没粘的那一段。
                problems.append({
                    'kind': 'unclosed', 'at': len(out), 'quote': ch,
                    'text': text,
                    'why': (f'第 {len(out) + 1} 个词的位置上有个引号「{ch}」'
                            f'没关上 —— 后面整段都被当成一个词了')})
            if text:
                out.append({'text': text, 'quoted': True, 'empty': False,
                            'word': len(out) + 1, 'start': i, 'end': j})
            elif closed:
                # ⚠ **空引号要占位**（见 `EMPTY_TOKEN`）：不占位，后面的值整体右移，
                #   最坏的结果是"切得整整齐齐、还报绿灯"，比切不上危险得多。
                shown = ch + ''.join(inner) + close
                problems.append({
                    'kind': 'empty', 'at': len(out), 'quote': ch,
                    'text': '', 'raw': shown,
                    'why': (f'第 {len(out) + 1} 个词的位置上是一对空引号「{shown}」'
                            f'—— 引号里没有值（Ren\'Py 会把它当成一个空属性名，'
                            f'跑不起来）；删掉它，或者把它改成那一格真正的值')})
                out.append({'text': '', 'quoted': True, 'empty': True,
                            'word': len(out) + 1, 'start': i, 'end': j})
            i = j
            continue
        if ch.isspace() or _WS_RE.match(ch):
            flush(i)
            i += 1
            continue
        if not buf:
            head = i
        buf.append(ch)
        i += 1
    flush(n)
    return out, problems


def _dict_index(values):
    """
    候选值表 → `{首字: [词…]}`（同一首字的词**按长度从长到短**）。

    为什么要索引到首字：切词时每个位置只可能被"以这个字开头"的词命中，
    不建索引就得给每个位置扫全表（200 个候选 × 800 个字 = 16 万次 `startswith`，
    中文长行会明显卡）。这是"试切必须一瞬间"的第一道保障。
    """
    index: dict = {}
    for value in values or ():
        text = str(value or '').strip()
        if not text:
            continue
        # 候选本身可以含空格（`base hands down`）：**整串和每个词都进词典**
        for word in [text, *text.split()]:
            if not word:
                continue
            bucket = index.setdefault(word[0], [])
            if word not in bucket:
                bucket.append(word)
    for bucket in index.values():
        bucket.sort(key=lambda w: (-len(w), w))
    return index


#: 中日韩字符（含假名、谚文、中日韩标点）的起点 —— 用来决定"这个原子值得切吗"。
_CJK_FLOOR = 0x2E80


def _has_cjk(text: str) -> bool:
    return any(ord(ch) >= _CJK_FLOOR for ch in text)


def _split_atom(text, index):
    """
    一个**没有空格**的词 → 尽量细地切成若干个词（`泳装开心` → `泳装` / `开心`）。

    为什么可以"尽量细"：切细了还能拼回去（`_span_text` 按原样拼，或按候选认），
    切粗了就拼不回来 —— `abcd` 若被当成一个词，A=`ab` / B=`cd` 就再也分不到了。

    取舍是**两层**的，对"这个原子怎么切"做一次小 DP（从后往前，`O(n·词数)`）：

      ① 先让**更多字被词典覆盖**（别把认识的词闷在一坨不认识的里面）；
      ② 一样多时**切得更细**（给后面的路由留出选择）。

    ⚠ 两条护栏（都是真输入逼出来的）：

      · **纯 ASCII 的原子只有被词典盖满时才切**。否则 `lan_2`（`lan` 是候选）
        会被拆成 `lan` + `_2`、`base_swimsuit_old` 会被拆开头 —— 一个本来
        很正常的变量名就散了。中文没有空格，才必须靠词典切。
      · **引号包起来的原子根本不会进来**（`_tokens` 早早就跳过了）。
    """
    n = len(text)
    if n <= 1:
        return [text]
    buckets = [()] * n
    starts = [False] * n
    for i in range(n):
        bucket = index.get(text[i])
        if not bucket:
            continue
        hits = tuple(w for w in bucket if text.startswith(w, i))
        if hits:
            buckets[i] = hits
            starts[i] = True
    best = [None] * (n + 1)          # best[i] = (覆盖字数, 词数, 词元组)
    best[n] = (0, 0, ())
    for i in range(n - 1, -1, -1):
        choice = None
        for word in buckets[i]:
            rest = best[i + len(word)]
            if rest is None:
                continue
            cand = (rest[0] + len(word), rest[1] + 1, (word,) + rest[2])
            if choice is None or cand[:2] > choice[:2]:
                choice = cand
        if not starts[i]:
            # 不认识的连续一段：延长到"下一个有词开始的位置"之前。
            # ⚠ 这一段**不会含任何词典词**（含了就说明那里有词开始），
            #   所以它是"一坨不认识的"最合理的切法。
            j = i + 1
            while j < n and not starts[j]:
                j += 1
            rest = best[j]
            if rest is not None:
                cand = (rest[0], rest[1] + 1, (text[i:j],) + rest[2])
                if choice is None or cand[:2] > choice[:2]:
                    choice = cand
        best[i] = choice
    chosen = list(best[0][2]) if best[0] else [text]
    if len(chosen) > 1 and not _has_cjk(text) and best[0][0] < n:
        return [text]               # 纯 ASCII 又没被盖满 → **不切**（见上面的护栏）
    return chosen


def _tokens_meta(line: str, *, options=()) -> dict:
    """
    一行 → **词 + 每个词的来处**（`tokenize` / `match_line` 的同一份词法）。

    返回::

        {'tokens': [...],          # 词（含 `EMPTY_TOKEN` 那种占位）
         'words':  [...],          # 每个词是**人敲的第几个词**（1 起）
         'spans':  [(起, 止)…],    # 每个词在原文里的字符区间（写回用）
         'n_words': int,           # 人这一行一共敲了几个词（按空格数）
         'atoms':  [...],          # `_atoms` 的原样输出
         'problems': [...]}        # `_atoms` 的词法毛病

    `options` 是"这一行本来就该认得的词"（各格候选 + 固定字 + 分栏候选）：
    中文没有空格时，它是唯一的切分依据。

    ⚠⚠ `words` 存在的理由（2026-10-18 第三轮攻击修）：`_split_atom` 会把
      `小明泳装` 切成 `小明` + `泳装` 两个**词**，但人按空格只数到**一个**。
      以前报错直接拿词的下标当"第几个词"，于是 `show 小明泳装` 报
      "第 4 个词"（这一行只有 2 个）—— 人照着数字去改，改的是别处。
      现在每个词都带着它属于人敲的第几个词，报错那一侧只需查表。
    """
    atoms, problems = _atoms(line)
    index = _dict_index(options)
    tokens: list = []
    words: list = []
    spans: list = []
    for atom in atoms:
        text = atom['text']
        if atom['empty']:                       # 空引号：占位，不参与切分
            tokens.append(EMPTY_TOKEN)
            words.append(atom['word'])
            spans.append((atom['start'], atom['end']))
            continue
        if atom['quoted'] or not index:          # 引号串 / 没有词典：整段一个词
            tokens.append(text)
            words.append(atom['word'])
            spans.append((atom['start'], atom['end']))
            continue
        off = atom['start']
        for piece in _split_atom(text, index):
            # `_split_atom` 返回的是**依次相接的几段**（拼起来就是这一段原文），
            # 所以字符区间可以一路累加出来 —— 写回时要落到原文上。
            tokens.append(piece)
            words.append(atom['word'])
            spans.append((off, off + len(piece)))
            off += len(piece)
    return {'tokens': tokens, 'words': words, 'spans': spans,
            'n_words': len(atoms), 'atoms': atoms, 'problems': problems}


def _tokens(line: str, *, options=()) -> list:
    """一行 → 词（**试切、读值、卡片用的是同一份**；来处见 `_tokens_meta`）。"""
    return _tokens_meta(line, options=options)['tokens']


def tokenize(line, slots=(), *, groups=None) -> list:
    """
    一行 + 一串格子 → 词。**`spec_args._claim` 转发到这里**，于是"试切怎么切、
    卡片就读到什么"不再有两套词法（以前引号只在读值侧去掉，同一行两个答案）。

    ⚠ 空引号在结果里是 `EMPTY_TOKEN`（`'\x00'`）：它**占一个位置**，
      但谁吃它谁就是空值（见 `EMPTY_TOKEN` 那段）。
    """
    parsed = parse_pattern(slots)
    return _tokens(line, options=_all_options(parsed, groups or {}))


def token_spans(line, slots=(), *, groups=None) -> list:
    """
    一行 + 一串格子 → **每个词在原文里的字符区间** `[(起, 止)…]`（终点不含）。

    与 `tokenize` **同序同长**（同一份 `_tokens_meta`）。写回那一侧靠它把
    `""` 这种"占了位置却没有值"的词换成真正的值 —— 按值去找区间是找不到的
    （值就是空串），只能按位置找。
    """
    parsed = parse_pattern(slots)
    return _tokens_meta(line, options=_all_options(parsed, groups or {}))['spans']


def _all_options(slots, groups) -> list:
    """所有"能被认出来"的词：每格候选 + 词组候选 + 固定关键字。

    ⚠ 是**列表**不是集合：`_dict_index` 的桶内顺序由 `(-长度, 词)` 决定，
    与给进来的顺序无关 —— 所以试切和读值两侧即使拼装顺序不同，切出来也一样。
    """
    out: list = []
    for slot in slots:
        out.extend(slot.get('options') or ())
        group = groups.get(str(slot.get('group') or ''))
        if isinstance(group, dict):
            out.extend(group.get('options') or ())
        if slot.get('text'):
            out.append(slot['text'])
    return out


def parse_pattern(raw) -> list:
    """
    规范化/校验一串格子（**纯函数**，坏格子直接丢掉并标出来）。

    返回 `[slot…]`，每个 slot 一定是 `{'kind', 'text', 'column', 'group',
    'options', 'limit', 'multi', 'required', 'problems'}` 齐全的形状 ——
    界面可以直接照它渲染。

    ⚠ 另外带一个内部标记 `_required_set`（`required` 是**人说的**吗）：
      `required` 缺省 True 由这里补，而 `with_optional_defaults` 只在
      "没说过"的时候才补 `False` —— 光看 `'required' in slot` 分不出
      "人说的 True"与"补出来的 True"（判据 `test_cut_round5` ②）。
      它不参与分配、也不进 `storygraph.rules.json`（落盘时摘掉）。
    """
    out = []
    for i, item in enumerate(raw or ()):
        slot = {'kind': '', 'text': '', 'column': '', 'group': '',
                'options': [], 'limit': False, 'multi': False,
                'required': True, 'problems': []}
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
            # ⚠ `limit`（2026-10-13 加）：**候选到底限不限死**。
            #   缺省 `False` —— 老声明（没有这个键）也一律按"候选只是建议"处理，
            #   这正是修"切不上"的那一半：存量声明不会因为候选不全而判人写错。
            slot['limit'] = bool(item.get('limit'))
            # ⚠ `multi`（2026-10-13 加）：这一格**可以吃多个词**。
            #   真机依据：`show lan happy sad` 的 `happy sad` 是同一格
            #   （`layeredimage` 的 `expressions` 组可以有多个属性）。
            #   缺省 `False` —— 老声明一个字不变。
            slot['multi'] = bool(item.get('multi'))
            # ⚠⚠ `required`（2026-10-18 加）：这一格**可填可不填**（不写 = 用默认）。
            #
            #   用户现场（真实项目 `sr_general`）：
            #   > 默认写法会报告切不上……实际上服装那一栏可填可不填……
            #   > 不填就是默认写法，你可以去真实项目内查看一下定义。
            #
            #   事实核对（`layeredimage lan` + 真实剧本）：`show ye happy` / `show lan
            #   happy` 在项目里出现几十次，`show ling at bounce_once` 更是一个属性都
            #   没有 —— Ren'Py 的**属性本来就是可选的**。所以"服装没写"不是错误，
            #   把它判成"第 2 格该有一个词"是**误报**。
            #
            #   缺省 `True`（老声明一个字不变）：只有明确说了"可不填"的格子才免费
            #   空着；要不要默认成"可不填"由语句头决定，见 `with_optional_defaults`。
            slot['required'] = bool(item.get('required', True))
            # ⚠⚠ `required` 的**来源**（2026-10-18 第五轮攻击修，P0）：
            #
            #   上面那行会给每一格补 `required`（缺省 True），于是"人明确说了必填"
            #   与"没说过、只是被补了默认"在**解析之后长得一模一样** —— 而
            #   `with_optional_defaults` 的判据恰恰是"没说过才补"
            #   （`'required' not in slot`）。后果是默认值**跟着入口顺序变**：
            #
            #       传原始 dict   → 补出 `required=False` → `show ye happy` 切得上
            #       传 parse 过的 → 全 True             → 同一行"切不上"
            #
            #   而读盘后的声明、卡片表单、`match_line` 自己拿到的**都是** parse 过的
            #   形状 —— 也就是说这条默认在真实入口上从来没生效过。
            #   `_required_set` 只记"这句话是谁说的"，不参与分配；落盘那一刻由
            #   `statement_registry.declare_statement` 摘掉（`required` 本身是具体的，
            #   读回来自然就是"说过的"）。
            slot['_required_set'] = 'required' in item
            # ⚠ **旧形状**（`{'name','label','choices'}`）要继续认（判据压着）：
            #   旧 `name` 是"剧本里怎么写的那串字" ⇒ 对应新模型的**固定关键字**；
            #   旧 `label` 是表格列名 ⇒ `column`；旧 `choices` ⇒ `options`。
            if not kind and item.get('name'):
                kind = 'keyword'
                slot['text'] = str(item.get('name')).strip()
        else:
            kind = str(item or '').strip()
            slot['_required_set'] = False     # 旧形状（一个字符串）从没说过
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
            slot['problems'].append('固定段得写清是哪个字（不然这一格永远对不上）')
        if kind in ('variable', 'choice', 'optional') and not slot['column']:
            slot['problems'].append('这一格要有个列名（表格里显示什么）')
        if kind == 'choice' and slot['limit'] \
                and not (slot['options'] or slot['group']):
            # ⚠ "从这些里选"**没给候选不算毛病**（2026-10-13 改）：候选是下拉里的
            #   备选，不是必填项 —— 没候选就等于"随便写"。旧版在这里报一句红，
            #   逼人去贴一串候选；贴了之后旧代码又把那一格变成封闭枚举
            #   （→ "切不上"）。只有**显式勾了"只能选这些"却没给候选**才真矛盾。
            slot['problems'].append('勾了"只能选这些"，却一个候选都没给')
        out.append(slot)
    return out


#: 第一个词是**立绘 tag**（不是参数）的语句 —— 与
#: `learn_statement._IMAGE_TAG_HEADS` 是同一条事实（那边还用它挑角色表的来源）。
#: 这里的用途只有一件：**这些语句的属性格可填可不填**。
IMAGE_TAG_HEADS = ('show', 'scene', 'hide')


def with_optional_defaults(slots, head) -> list:
    """
    给"属性格"补上 `required=False` 的**默认** —— `show/scene/hide` 专用。

    为什么（用户 2026-10-18 现场，见 `tests/test_cut_optional_slots.py`）：

      · Ren'Py 的 `show` 是 `show <tag> [属性…]`，**属性全是可选的**：
        项目里 `show ye happy`（穿默认衣服）出现 11 次、`show ling at bounce_once`
        一个属性都没有 —— 它们都跑得起来；
      · 而编辑器的格子是把 `show ye base_handdown happy` 拆成
        「角色｜服装｜表情」三格，于是"没写服装"看起来像"少了一个词" ——
        试切于是报"第 2 格该有一个词"，人对着一条**完全合法**的行改半天。

    规矩（三条，都是刻意收窄的）：

      · 只对 `show/scene/hide` 生效，而且只对**第一个取值格之后**的取值格
        （第一个是 tag 本身，`show` 后面没有它跑不起来）；
      · **只补缺**：格子自己写明了 `required`（界面勾了/取消过）就一字不动；
      · 不动 `keyword` / `optional`：它们的"必不必须"由别处决定。

    ⚠⚠ "只补缺"怎么判（2026-10-18 第五轮攻击修，P0）：**不能**用
      `'required' not in slot` —— `parse_pattern` 会给每格补上 `required`
      （缺省 True），于是同一个格子：

        传原始 dict   → 补出 `required=False` → 同一行判绿；
        传 parse 过的 → 全 True             → 同一行判红。

      而读盘后的声明、表单、`match_line` 拿到的**都是** parse 过的形状，
      所以这条默认在真实入口上**从来没生效过**。现在问的是
      `_required_set`（"这句话是谁说的"），与入口顺序无关。
      补出去的值同时带上 `_required_set=True`：它就是"说过的"了，
      再调一次不会被翻掉（幂等）。

    ⚠ 是**纯函数**、也不改入参（返回新的 dict 列表）：界面每一帧都会调它。
    """
    show_like = str(head or '').strip() in IMAGE_TAG_HEADS
    out: list = []
    seen_value = False
    for item in (slots or ()):
        if not isinstance(item, dict):
            out.append(item)
            continue
        slot = dict(item)
        kind = str(slot.get('kind') or '')
        if kind in ('keyword', 'optional'):
            out.append(slot)
            continue
        #: 这一格的 `required` 是**人说的**吗？（原始 dict 看键在不在；
        #: `parse_pattern` 之后看 `_required_set` —— 它正是为这件事留的）
        explicit = (bool(slot['_required_set']) if '_required_set' in slot
                    else 'required' in slot)
        if show_like and seen_value and not explicit:
            slot['required'] = False
            slot['_required_set'] = True      # 从现在起它是"说过的"
        seen_value = True
        out.append(slot)
    return out


def _as_count(value) -> int:
    """
    脏输入 → 一个**非负整数**（`'x'` / `None` / `2.7` / `'3'` 都不许抛）。

    ⚠ 存在的理由（2026-10-17 第二轮攻击）：`word_offset` 来自界面状态与配置，
       而 `match_line` 是 Qt 槽里调用的 —— 那里抛异常等于"点了没反应"，
       比给个默认值难查得多。
    """
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return 0


def _cell(slot: dict, text: str, allowed=()) -> dict:
    """
    一格 → 界面要画的那个小方块；`ok` **按候选算**（2026-10-17 第二轮攻击修）。

    ⚠ `ok` 以前是写死的 `True`，于是"值不在候选里"在界面结构上**没有位置放**：
      `show lan junk base_swimsuit` 里 `服装 = junk`，格子却是绿的。
      这个字段存在的意义就是给界面标黄/标红，恒真等于没有。

    ⚠ `allowed` 为空 = 这一格**随便写**（任意变量），任何值都算 ok；
      多值格按**每个词**算（`base_swimsuit clothes2` 里混进一个垃圾词要能看出来）。
    """
    value = str(text or '')
    ok = True
    if allowed and value.strip():
        words = value.split()
        ok = all(w in allowed for w in words) or value.strip() in allowed
    return {'kind': slot['kind'], 'text': text, 'column': slot.get('column', ''),
            'group': slot.get('group', ''), 'ok': ok}


#: 一个词落到某一格时的**代价** —— 分词器思路的核心（2026-10-17 重构）。
#:
#: 用户现场：`show lan base_swimsuit happy` 被切成
#: 「`lan base_swimsuit` ｜ 空 ｜ `happy`」。「服装」勾了"可有多个"，于是它可以
#: **一个都不吃**；旧实现只问"后面每一格还能不能吃到词"，**从不问"这个词是谁的"**，
#: 于是前一格把后一格的候选词当成了自己的第二个值。
#:
#: 现在的规矩：每个词先查**归属**（`_owners_of`），落到
#:   · 自己的候选里 → 0（本该如此）；
#:   · 谁也不认识 → 2（自由文本，正常）；
#:   · **别的格子**的候选里 → 20（跨格抢词，几乎总是错的）。
#: 整行取**总代价最小**的切法（`_spans`）。
#:
#: ⚠⚠ 20 是刻意留的大数，不是"稍微贵一点"（2026-10-17 第二轮攻击修）：
#:   不抢词那一侧付的是 `EMPTY_SLOT(7) + SKIP_WORD(3) = 10`。旧值 8 比它便宜，
#:   于是"抢"**永远**赢 —— 实测 `show lan lan` 会给出一条不存在的服装
#:   （`服装 = lan`），界面还报绿灯。第一轮只比了 8 > 7，漏了被抢那个词本来
#:   要付的 3。
#:   兜底判据：`COST_STOLEN > EMPTY_SLOT + SKIP_WORD`（`test_cut_tokenizer_attack2`）。
#:   贵过这条线之后，"抢"在任何排版里都不会再被选中；把数留着而不是删掉分支，
#:   是为了代价表保持**全函数**（每个词×每格都有定义），将来要放开只改一个数。
COST_OWN = 0
#: 这一格**没有候选**（任意变量 / 没给候选的枚举格）→ 什么词都只是自由文本。
COST_UNKNOWN = 2
#: 这一格**随便写**（没有候选），而这个词是**别的格子写明的候选** —— "借"。
#:
#: ⚠ 8 被两条真需求夹出来的（2026-10-17）：
#:   · **必须比 `EMPTY_SLOT`（7）贵**：`[人物][表情(可多个)]` 遇上只有 `happy`，
#:     人物是"随便写"的格子，它抢走 `happy` 就会让表情整格空着 ——
#:     人写了 `happy`（表情的候选），卡片上"表情"却是空的，那是假成功。
#:     7 < 8 → 空着那一边赢（判据 `test_试切与读值在策略不冲突的形状上逐格相同`
#:     里 `happy` 那一例）。
#:   · **必须比 `EMPTY_SLOT + COST_EXTRA`（9）便宜**：`mypose happy sad at left`
#:     里人物抽走 `happy`、表情拿 `sad` 是 8 点；人物空着、表情吃两个是 7+2=9 点。
#:     8 < 9 → 人物拿到 `happy`，"谁在摆姿势"才不会莫名其妙变成空的
#:     （判据 `test_cut_tokenizer_attack2::test_任意变量格抢词这条需求不许被修坏`）。
#:   两个方向只留 1 点余量，所以**别随手改这两个数里的任何一个**。
COST_BORROW = 8
#: 这一格**有候选**，却吃了一个**谁也不认识**的词 = "值不在候选里"。
#:
#: ⚠ 比 `COST_UNKNOWN`（2）贵（2026-10-17 第二轮攻击修）：同样的词，
#:   落进"随便写"的格子是正常，落进**封闭枚举**是**异常**（界面把那一格标黄）。
#:   贵一点才使得"跳过它去认自己真正的候选"划算 —— 见 `SKIP_JUNK`。
#:   它必须**严格小于** `COST_STOLEN`（抢别人写明的候选永远更糟）。
COST_OFFLIST = 5
#: 封闭枚举格吃了一个**别的格子写明的候选** = 跨格抢词。
#:
#: ⚠⚠ 20 是刻意留的大数，不是"稍微贵一点"（2026-10-17 第二轮攻击修）：
#:   不抢词那一侧付的是 `EMPTY_SLOT(7) + SKIP_WORD(3) = 10`。旧值 8 比它便宜，
#:   于是"抢"**永远**赢 —— 实测 `show lan lan` 会给出一条不存在的服装
#:   （`服装 = lan`），界面还报绿灯。第一轮只比了 8 > 7，漏了被抢那个词本来
#:   要付的 3。
#:   兜底判据：`COST_STOLEN > EMPTY_SLOT + SKIP_WORD`（`test_cut_tokenizer_attack2`）。
#:   贵过这条线之后，"抢"在任何排版里都不会再被选中；把数留着而不是删掉分支，
#:   是为了代价表保持**全函数**（每个词×每格都有定义），将来要放开只改一个数。
COST_STOLEN = 20
#: 多值格从**第 2 个**词起每个加一点：能分开就别堆在一格
#: （两个相邻格子都勾了"可有多个"时，这一步就是"甲吃甲、乙吃乙"的仲裁）。
COST_EXTRA = 2

#: "没做成"那一档的代价（四类失败各记一点，数值只有相对大小有意义）。
#:
#: ⚠⚠ 这几个数**不是**"切不切得上"的判据 —— 判据是 `_report` 从同一份
#:   `spans` 倒推出来的四件事（关键字对不上 / 限死挡词 / 该吃词的格子空着 /
#:   有词没人认领）。代价只负责**选哪一种排版最说得通**（失败那一屏也要好看），
#:   以及"能塞进某一格就别晾着"这种偏好。
#:   为什么不能拿"总代价 == 0"当成功：吃一个谁都不认识的词（自由文本，真机常态）
#:   本身就要 `COST_UNKNOWN` 2 点，那不是失败。
#:
#: ⚠ `SKIP_WORD`（3）**故意比** `COST_UNKNOWN`（2）大一点：意思是"能塞进某一格（2）
#:   就塞，塞不进去才晾着（3）"。太便宜的话，什么词都会被判成"没人认领"。
SKIP_WORD = 3
#: 一个**必填格**空着（该有一个词，却没有）。
#:
#: ⚠ 这个数字与"抢别人的词"是**一对**：抢 = 20 > 空着 7 + 那个词晾着 3 = 10，
#:   所以"能不动别人的词就不动"是**硬的**，不是"略便宜"。
#:   （2026-10-17 第一轮它写成 7 对 8，只比了 7 < 8，漏了被抢那个词本来要付的
#:   3 —— 于是 `show lan lan` 会给出一条不存在的服装 `服装 = lan`，还报绿灯。
#:   判据：`test_cut_tokenizer_attack2::test_一个词不许被别的格子偷走`。）
#:
#:   `mypose happy sad at left`（人物格没有候选 → 自由文本 2）这条真需求**不是**
#:   靠这个数保住的，而是靠"闭枚举 vs 随便写"（见 `COST_UNKNOWN` / `COST_OFFLIST`）。
EMPTY_SLOT = 7
MISS_KW = 2
#: **跳过一个"谁也不认识的词"**，好让自己那一串候选从头开始。
#:
#: 为什么要跳（用户现场 2026-10-17 的第二次地狱测试）：
#:   `show lan dirty base_swimsuit happy` 里的 `dirty` 是垃圾词。「服装」如果
#:   "从当前位置开始吃"，就只能把它焊进值里（`dirty base_swimsuit`）—— 那正是
#:   用户点名的"垃圾被焊进某个合法值"。有了这一条，正确排版是
#:   `服装 = base_swimsuit` + "第 3 个词「dirty」没人认领"。
#:
#: ⚠ 跳过的**总**代价 = `SKIP_JUNK + SKIP_WORD`（2026-10-17 第二轮攻击修）：
#:   被跳过的词最后一定会被 `_report` 算进 `leftover`，而"晾着一个词"本来就要
#:   付 `SKIP_WORD`（3）—— 只记 `SKIP_JUNK` 等于让"跳过"变**免费**，
#:   于是 `小明 龙袍 泳装` 会跳过 `龙袍` 去吃 `泳装`（2 点 vs 4 点），
#:   而 `clothes2 junk happy` 里表情宁可跟垃圾焊在一起。
#:   合并之后（每个被跳过的词 1+3 = 4 点）：
#:   · 比"把这个词当值吃进闭枚举"（`COST_OFFLIST` 5）便宜 → 会去认自己的候选；
#:   · 比"跳过一个本来就该晾着的末位词"贵（4 > 2）→ 末位垃圾词仍然被吃掉。
SKIP_JUNK = 1
#: 固定关键字对不上时，**把这个错词也吃掉**的代价（比"晾着"贵一点）。
#:
#: 为什么需要它：`shw lan base_swimsuit happy` 里 `shw` 是**拼错的关键字**。
#: 如果它不被吃掉，第 2 格（角色）就会从 `shw` 开始，只能把拼错的关键字
#: 当成一个角色名（实测界面显示 `角色 = "shw lan"`，而 reason 说"关键字错了"
#: —— 两条互相打脸的线索）。吃掉它 = 承认"这个词本来该是关键字"。
MISS_KW_SKIP = 4


def _allowed_of(slot, groups) -> list:
    """这一格的候选：格子自己写的 + 它的词组给的。"""
    out = [str(o) for o in (slot.get('options') or ())]
    g = groups.get(str(slot.get('group') or ''))
    if isinstance(g, dict):
        out += [str(o) for o in (g.get('options') or ())]
    return out


def _owners_of(slots, groups) -> dict:
    """
    每个词**被哪些格子写成了候选** —— `{词: {格子下标…}}`。

    这是"分词器"与"贪婪捕获"的分水岭：切之前先知道"这个词是谁的"，
    后面就不需要靠"多吃少吃"来猜。候选本身含空格时（`base hands down`）
    连每个词也单独登记，于是 `base` / `hands` / `down` 都算那一格的东西。
    """
    owners: dict = {}
    for si, slot in enumerate(slots):
        known = [str(o).strip() for o in _allowed_of(slot, groups)
                 if str(o).strip()]
        literal = str(slot.get('text') or '').strip()
        if literal:
            known.append(literal)          # 固定字也是"这一格的东西"
        for opt in known:
            for word in {opt, *opt.split()}:
                if word:
                    owners.setdefault(word, set()).add(si)
    return owners


def _tok_cost(tok, si: int, allowed, owners, limited: bool, dead=()):
    """
    一个词单独交给第 `si` 格，代价多少；`None` = **这一格不能这么吃**。

    ⚠ `limit`（"只能从这些里选"）是一条**硬**规矩：候选外的词一概不收，
       返回 `None` 让分配器换别的路（换不了就由 `_report` 说清是谁挡的）。

    ⚠⚠ **闭枚举格 vs 随便写的格子**（2026-10-17 第二轮攻击修）：
       `allowed` 非空 = 这一格是**封闭枚举**（"这个词落在这一格"有明确归属），
       别人的候选落在里面就是**抢**（20）；
       `allowed` 为空 = 这一格**随便写**（任意变量 / 没给候选的枚举格），
       对它来说任何词都只是自由文本（2）—— 于是
       `mypose happy sad at left` 里人物格拿走 `happy` 仍然便宜，
       "谁在摆姿势"不会莫名其妙变成空。
       这一条是"不许偷词"与"变量格要能抢到第一个词"能同时成立的**唯一**依据。

    ⚠ `dead`（2026-10-18）：**被空引号废掉的那几格**。它们的候选词不再是
      "别人写明的候选" —— 那一格这一行没有值，它的候选词就是**没人要**的，
      后面那一格按位置接住才是对的（`show lan "" base_swimsuit happy`
      里 `base_swimsuit` 要落到「表情」）。但它们在**跳过**那一侧仍然是
      "有主的"（`_spans` 的跳过循环照样停），所以不会被当成垃圾晾掉。

    ⚠⚠ **只属于死格的候选词按"没人认识"（`COST_UNKNOWN`）计价**（2026-10-18
      第四轮攻击修）：按"候选外"（`COST_OFFLIST` 5）计价时，"接住"只在**必填格**
      成立 —— 多值格宁可空着（0 + 晾着 3 = 3 < 5），于是
      `show lan "" base_swimsuit happy` 在"服装 multi"下把两个词一起晾着。
      降成 2 之后：必填格 2 < 空着 7 + 3；多值格 2 < 空着 0 + 3 —— 两种都接住。
      只属于死格 = `owners[tok] - dead` 为空；仍然属于某个**活格**的词照旧是"抢"。
    """
    if tok == EMPTY_TOKEN:
        # 空引号：**任何格子都吃得下，代价 0**。它是"这一格这一行没有值"的占位，
        # 不是值本身 —— 认领它 = 那一格停在空引号那里，后面的值不许前移。
        # ⚠ 必须在 `limited`（"只能从这些里选"）之前：那是一条硬规矩，但空引号
        #   根本不参与"值在不在候选里"这件事，不拦它。
        return COST_OWN
    if tok in allowed:
        return COST_OWN
    if limited:
        return None
    foreign = bool(owners.get(tok)) and bool(owners[tok] - {si} - set(dead))
    if not allowed:
        #: 这一格**随便写**：谁的词都能进来，只是"占了别人写明的候选"贵一点
        return COST_BORROW if foreign else COST_UNKNOWN
    if foreign:
        return COST_STOLEN            # 封闭枚举去抢别人写明的候选：几乎总是错的
    if dead and owners.get(tok) and not (owners[tok] - set(dead)):
        #: 这个词**只**属于被空引号废掉的那几格 —— 那一格这一行没有值，它的候选
        #: 已经**没人要**了。按"候选外"（5）计价的话，多值格宁可空着
        #: （0 + 晾着 3 = 3 < 5）也不接它：`show lan "" base_swimsuit happy`
        #: 里 `base_swimsuit` 明明该按位置落到下一格，却被和 `happy` 一起晾着
        #: （2026-10-18 第四轮攻击修）。按"谁都不认识的词"（2）计价：
        #:   必填格 2 < 空着 7 + 晾着 3；多值格 2 < 空着 0 + 晾着 3 —— 两头都接住。
        #: ⚠ 只在**有死格**的行生效（`dead` 非空），普通行一个数都不动。
        return COST_UNKNOWN
    return COST_OFFLIST               # 封闭枚举吃了候选外的词 = "值不在候选里"


def _span_hit(tokens, a: int, b: int, allowed) -> bool:
    """
    `tokens[a:b]` 整段**能不能算某一格的候选值**（两种拼法都认）：

      · `' '.join` —— 候选本身含空格（真机立绘的 `base hands down`）；
      · `''.join` —— 候选是一个"连写"的词，而它被切成了几段
        （`abcd` 切成 `ab`+`cd`、`小明泳装` 切成 `小明`+`泳装`）。
        没有这一条，切得越细反而越认不出整块候选 —— 那是"分词"与"路由"
        互相打架的老病根。
    """
    if not allowed or b <= a:
        return False
    if b - a == 1:
        return tokens[a] in allowed
    if ' '.join(tokens[a:b]) in allowed:
        return True
    return ''.join(tokens[a:b]) in allowed


def _show_token(tok) -> str:
    """一个词给**人看**的样子：空引号的占位符显示成 `""`（不是 `\\x00`）。"""
    return '""' if tok == EMPTY_TOKEN else str(tok)


def _span_text(tokens, a: int, b: int, allowed) -> str:
    """
    `tokens[a:b]` 这一格**显示/写回**用的值。

    默认是"原样的几个词，用空格连起来"；但如果整段正好是某一格写着的候选
    （含把连写的词拼回去），给的就是**那个候选** —— 否则 `base`+`_swimsuit`
    会在界面上显示成 `base _swimsuit`，而人写的明明是 `base_swimsuit`。

    ⚠ 空引号的占位符**不算值**（2026-10-18）：它只占位置，滤掉之后这一格
      的值就是空串 —— 于是界面显示空格子、卡片读出来是空，谁都不会把它
      当成一个属性名写进 `.rpy`。
    """
    parts = [t for t in tokens[a:b] if t != EMPTY_TOKEN]
    if not parts:
        return ''
    if len(parts) > 1 and allowed:
        joined = ' '.join(parts)
        if joined in allowed:
            return joined
        tight = ''.join(parts)
        if tight in allowed:
            return tight
    return ' '.join(parts)


def _spans(tokens, slots, groups, dead=()):
    """
    整行 → 每格占哪几个词，**总代价最小**的那一种 —— 一句话的**唯一**一份实现。

    备忘录化的动态规划，状态只有 `(第几格, 第几个词)`：

        solve(si, ti) = min over 这一格的各种吃法 [ 这一格的代价 + solve(si+1, 吃完之后) ]

    ⚠ 为什么必须"全局"而不能"从前往后、能多吃就多吃"（用户现场的原病）：
      紧挨着的两个"可有多个"格子，任何一种"先到先得"的走法都要在某一格上做
      **没有依据**的决定（多吃一个还是少吃一个）。代价表把这件事变成
      "这个词是谁的候选"，于是决定有依据、可复现，也不需要人再去调顺序凑。

    ⚠ 为什么不会指数爆炸（用户点名的"禁止暴力回溯"）：`multi` 格要枚举
      "吃 0/1/2…个词"，朴素写法是 `O(分支^格子数)`；这里每个 `(si, ti)`
      只算一次，状态数 = `格子数 × 词数`，每格最多再扫一遍词 → `O(格子² × 词数)`。
      判据 `test_两百个词的试切不许指数爆炸` 卡 2 秒，实测毫秒级
      （300 个词的真实形状 4ms；300 个**全是垃圾词**的最坏跳过形状 0.3 秒）。

    ⚠ "跳过垃圾词"只在**遇到谁也不认识的词**时展开（遇到别人的候选就停），
      所以它不是在词数上再乘一个维度 —— 真实行通常一步都不跳。

    `dead`：**因为空引号而"这一行没有值"的格子下标**（2026-10-18）。
    它们不参与分配、也不占词；它们的候选词对别人来说不再是"抢词"
    （见 `_tok_cost` 的 `dead` 那段）。

    ⚠⚠ **空引号落到哪一格，是位置决定的，不是代价决定的**（2026-10-18
      第四轮攻击修）：空引号对谁都免费、多值格"不吃"也免费，于是 DP 会把它
      **推给下一格**（只「服装」multi 时 `show lan "" base_swimsuit happy`
      会排成"表情死、服装=base_swimsuit"——值整体左移一格）。现在轮到取值格
      而下一个词是空引号时，这一格**只有"吃掉它"一个分支**（见下面 `solve`
      里的注释），于是第一遍 `spans` 就能按位置点出 `dead`。
      `keyword` / `optional` 不强制：空引号不是关键字的匹配，`at 位置`
      也本来就能整段缺席。

    返回 `(总代价, {第几格: (词起点, 词终点)})`；终点不含。
    ⚠ `代价 == 0` **不是**"切上了"的判据（吃一个自由文本就要 2 点）——
      判据是 `_report` 从同一份 `spans` 倒推的四类失败。
    """
    m, n = len(tokens), len(slots)
    dead = set(dead or ())
    owners = _owners_of(slots, groups)
    allowed_sets: list = []
    max_lens: list = []
    own_sets: list = []
    stop_sets: list = []
    for si, slot in enumerate(slots):
        allowed = {str(o) for o in _allowed_of(slot, groups) if str(o)}
        allowed_sets.append(allowed)
        #: 一格最多可能吃几个词 —— 用**最长候选的字数**当上界（每段至少一个字），
        #: 于是"整段是一个候选"的枚举不会漏，也不会白扫几十次。
        max_lens.append(max([1] + [len(o) for o in allowed]))
        #: 这一格**认得的词**（候选整串 + 候选里的每个词，因为候选可以含空格）。
        #: 空集 = 它是"随便写"的（变量格 / 没给候选的枚举格），来者不拒。
        own = set()
        for opt in allowed:
            own.add(opt)
            own.update(opt.split())
        own_sets.append(own)
        stop_sets.append(
            {str(s.get('text') or '').strip() for s in slots[si + 1:]
             if str(s.get('kind') or '') in ('keyword', 'optional')} - {''})
    #: `(第几格, 第几个词)` → `(代价, 下一步吃到第几个词, 这一格占的区间)`
    #:
    #: ⚠ 这里**只存回溯指针，不存 spans 副本**：以前每个候选分支都 `dict(...)`
    #:   拷一份 spans，长句上是明显的常数开销（800 词要 1.3 秒）。现在每个状态
    #:   只留三元组，最后顺着指针走一遍，实测同样的长句快 3～5 倍。
    memo: dict = {}

    def solve(si: int, ti: int):
        key = (si, ti)
        hit = memo.get(key)
        if hit is not None:
            return hit
        if si >= n:
            res = ((SKIP_WORD * (m - ti) if ti < m else 0), None, None)
            memo[key] = res
            return res
        if si in dead:
            # 被空引号废掉的那一格：这一行它没有值 —— 不吃词、不占位置，
            # 直接交给后面（词数不变，所以后面那一格从**同一个词**继续）。
            nxt = solve(si + 1, ti)
            res = (nxt[0], ti, None)
            memo[key] = res
            return res
        slot = slots[si]
        kind = str(slot.get('kind') or 'variable')
        text = str(slot.get('text') or '').strip()
        allowed = allowed_sets[si]
        limited = bool(slot.get('limit')) and kind == 'choice'

        # ⚠⚠ **空引号落在哪一格 = 位置说了算，DP 没有选择权**（2026-10-18 第四轮攻击修）：
        #   空引号对**任何**格子都免费（`COST_OWN`），而多值格"一个都不吃"也免费 ——
        #   于是 DP 可以把空引号**推给下一格**。只「服装」multi 时，
        #   `show lan "" base_swimsuit happy` 会被排成 `broken=[3]`（表情死了）、
        #   `服装 = base_swimsuit`：人写的"服装空"变成"服装=base_swimsuit"，
        #   **值整体左移一格**，而报错还指着无辜的表情。
        #   以前这里靠注释假设"空引号不许跳过，所以一定落到轮到的那一格"，
        #   漏了"多值格可以不吃，把空引号让出去"这一条（总价更低，DP 当然选它）。
        #   现在把它变**硬**的：轮到取值格而下一个词是空引号 → 这一格唯一的选择
        #   就是吃掉它（于是 `assign_tokens` 第一遍就能按位置定死谁死）。
        #   · `keyword` **不**这样改：空引号不是关键字的匹配（吃掉它 = 让后面的值
        #     前移），它照旧"空着往下 + MISS_KW"，由 `_report` 点名；
        #   · `optional` 也不强制：`at 位置` 整段缺席本来就合法。
        if (ti < m and tokens[ti] == EMPTY_TOKEN
                and kind not in ('keyword', 'optional')):
            res = (COST_OWN + solve(si + 1, ti + 1)[0], ti + 1, (ti, ti + 1))
            memo[key] = res
            return res

        best = [None]

        def offer(next_ti: int, extra: int, span=None):
            """试一种吃法：这一格付 `extra`，剩下的交给后面的格子。"""
            total = extra + solve(si + 1, next_ti)[0]
            if best[0] is not None and total >= best[0][0]:
                return                   # 严格更好才换：同价取**先试**的那种
            best[0] = (total, next_ti, span)

        if kind == 'keyword':
            matched = bool(text) and ti < m and tokens[ti] == text
            if matched:
                offer(ti + 1, COST_OWN, (ti, ti + 1))
            else:
                if ti < m and tokens[ti] != EMPTY_TOKEN:
                    # 把这个"本来该是关键字"的词吃掉：拼错的关键字不该流到
                    # 第 2 格去当一个值（`角色 = "shw lan"` 那种打脸）
                    # ⚠ 空引号**不许被吃掉**（2026-10-18）：吃掉它 = 让后面的
                    #   值前移一格，正是这一轮要修的病。
                    offer(ti + 1, MISS_KW_SKIP)
                # 对不上（或压根没写是哪几个字）→ 这一格空着往下走，由 `_report` 点名
                offer(ti, MISS_KW)
        elif kind == 'optional':
            # `at 位置`：出现就吃两个词（固定字 + 值），不出现也合法。
            #
            # ⚠ 固定字前面夹了**谁也不认识的词**也要能认出来（2026-10-17 第二轮
            #   攻击修）：`show lan junk at left happy` 里 `at left` 是位置那一段，
            #   不该因为前面多了个 `junk` 就整段丢掉、把 `junk` 塞进表情。
            #   与取值格同一条规矩 —— 只跳"谁也不认识的词"。
            p, skipped = ti, 0
            while text and p < m:
                if (p + 1 < m and tokens[p] == text
                        and tokens[p + 1] not in ('', ':')):
                    offer(p + 2, skipped + COST_OWN, (p, p + 2))
                if (tokens[p] in stop_sets[si] or owners.get(tokens[p])
                        or tokens[p] == EMPTY_TOKEN):
                    break            # 空引号也跳不过去（与取值格同一条规矩）
                p += 1
                skipped += SKIP_JUNK + SKIP_WORD
            offer(ti, COST_OWN)
        elif limited and not allowed:
            # 勾了"只能从这些里选"却一个候选都没给 = 前后矛盾，谁也不能通过
            offer(ti, EMPTY_SLOT)
        else:
            # ── 取值格：任意变量 / 从这些里选 / 可选的固定段之外的吃词逻辑 ──
            #
            # 一段"吃法"= 从哪个词**开始吃** + 吃几个。开始位置只能是 `ti`，
            # 或者把中间那些**谁也不认识的词**晾给整行（每个记 `SKIP_JUNK`）
            # 之后的某个位置 —— 这就是"跳过垃圾词去认自己的候选"，
            # 以前它只存在于 `policy='candidates'` 那一份里，于是试切与卡片
            # 会给出**不同的格子值**（用户第二轮攻击点名的病）。
            stop = stop_sets[si]
            own = own_sets[si]
            multi = bool(slot.get('multi'))
            starts = [(ti, 0)]
            p, skipped = ti, 0
            while (p < m and tokens[p] not in stop
                   and not owners.get(tokens[p])
                   and tokens[p] != EMPTY_TOKEN):
                #: ⚠ `tokens[p] != EMPTY_TOKEN`：**空引号跳不过去**（2026-10-18）。
                #:   它占着一个位置；允许跳过就等于允许后面整体前移一格
                #:   （`show lan "" base_swimsuit happy` 会被"跳过去认候选"
                #:   排成 `服装=base_swimsuit`，还报绿灯）。所以这里只跳
                #:   "谁也不认识的词"，遇到空引号必须停下来、由某一格认领它。
                p += 1
                #: 被跳过的词最后一定会被算成 `leftover`，所以这里连
                #: `SKIP_WORD`（"晾着一个词"）一起记，跳过才不是免费的。
                skipped += SKIP_JUNK + SKIP_WORD
                if p <= m:
                    starts.append((p, skipped))

            for start, skipped in starts:
                if start >= m:
                    continue             # 话说到头了 → 交给"这一格空着"
                one = _tok_cost(tokens[start], si, allowed, owners, limited, dead)
                if one is None:
                    continue             # 限死的格子：这个位置吃不动
                # ① 只吃这一个词（自由文本 / 单个候选 / 引号串）
                offer(start + 1, skipped + one, (start, start + 1))
                # ② 整段是一个候选（`base hands down` / 被切细的连写候选）
                if allowed and max_lens[si] > 1:
                    for k in range(2, min(max_lens[si], m - start) + 1):
                        if _span_hit(tokens, start, start + k, allowed):
                            offer(start + k, skipped + COST_OWN,
                                  (start, start + k))
                # ③ "可有多个"：一串**连续的自己人**
                #    （不是自己人就到这儿为止 —— `dirty base_swimsuit` 那种
                #     "垃圾被焊进合法值"绝不许发生）。
                #    ⚠ 起点也必须是自己人：一个**不是自己人**的词只能"整格就它一个"
                #      （①）—— 否则 `clothes2 junk happy` 会把 `junk happy`
                #      焊成表情的值（第一轮地狱测试点名的同款病）。
                if multi and (not own or tokens[start] in own):
                    cost, k = one, 1
                    while start + k < m:
                        tok = tokens[start + k]
                        if tok == EMPTY_TOKEN:
                            break    # 空引号是一个"位置"，绝不焊进值里
                        if tok in stop:
                            break        # 后面那一格的固定字，绝不吃
                        if own and tok not in own:
                            break        # 不是自己人 → 到这儿为止（不焊接）
                        c = _tok_cost(tok, si, allowed, owners, limited, dead)
                        if c is None:
                            break        # 限死的格子吃到候选外 → 到此为止
                        cost += c
                        k += 1
                        # 分支限界：代价随 k 单调不减（每个词 ≥0，第 2 个起 +2），
                        # 一旦"这一格已经比当前最优整行还贵"，更长的 k 不必再看。
                        if best[0] is not None and skipped + cost >= best[0][0]:
                            break
                        extra = (COST_OWN if (k <= max_lens[si]
                                             and _span_hit(tokens, start,
                                                           start + k, allowed))
                                 else cost + COST_EXTRA * (k - 1))
                        offer(start + k, skipped + extra, (start, start + k))
            # 一个都不吃：多值格本来就可以空着（免费）；**可填可不填**的格子
            # 同理（`required=False`，`show` 的属性格就是这种，见
            # `with_optional_defaults`）；必填格要付 `EMPTY_SLOT` 并如实报
            # "这一格该有一个词"。
            offer(ti, COST_OWN if (slot.get('multi')
                                   or not slot.get('required', True))
                  else EMPTY_SLOT)
        memo[key] = best[0]
        return best[0]

    total = solve(0, 0)[0]
    spans: dict = {}
    si, ti = 0, 0
    while si < n:                        # 顺着回溯指针走一遍，把区间收回来
        _cost, next_ti, span = memo[(si, ti)]
        if span is not None:
            spans[si] = span
        si += 1
        ti = next_ti if next_ti is not None else ti
    return total, spans


def _report(tokens, slots, spans, groups) -> dict:
    """
    从**那一份分配**倒推"哪儿不对" —— 于是报告和画面永远对得上。

    为什么不在分配时顺手记失败信号（旧 `_assign_report` 的做法）：那样会有
    **两套口径** —— 分配是一套、报错是另一套，用户看到的就是
    "reason 说第 2 格该有一个词，而第 2 格明明写着 `base`"。
    现在只有一份：先有 `spans`，再照着它点名额。
    """
    m = len(tokens)
    covered: set = set()
    for a, b in spans.values():
        covered.update(range(a, b))
    leftover: list = [_show_token(tokens[i]) for i in range(m) if i not in covered]
    leftover_at: list = [i for i in range(m) if i not in covered]

    bad_kw = None
    rejected = None
    rejected_word = ''
    rejected_at = None
    unfilled: list = []
    empty_at: dict = {}              # 第几格 → 它在第几个词的位置上没词
    pos = 0
    for si, slot in enumerate(slots):
        span = spans.get(si)
        if span is not None:
            pos = max(pos, span[1])
            continue
        kind = str(slot.get('kind') or 'variable')
        allowed = _allowed_of(slot, groups)
        empty_at[si] = pos
        word = _show_token(tokens[pos]) if pos < m else ''
        if kind == 'keyword':
            if bad_kw is None:
                bad_kw = si
        elif kind == 'optional' or slot.get('multi') \
                or not slot.get('required', True):
            # 可选段缺席 / 多值格一个都不吃 / **可填可不填**的格子空着：都合法
            continue
        elif kind == 'choice' and slot.get('limit'):
            if rejected is None:
                rejected = si
                rejected_word = word
                rejected_at = pos if word else None
        else:
            unfilled.append(si)

    values: dict = {}
    for si, (a, b) in spans.items():
        if 0 <= si < len(slots):
            values[si] = _span_text(tokens, a, b,
                                    set(_allowed_of(slots[si], groups)))
    return {'used': len(covered), 'bad_kw': bad_kw, 'rejected': rejected,
            'rejected_word': rejected_word, 'rejected_at': rejected_at,
            'unfilled': unfilled, 'leftover': leftover,
            'leftover_at': leftover_at, 'empty_at': empty_at,
            'values': values}


def assign_tokens(tokens, slots, *, groups=None) -> dict:
    """
    **一行词 → 每格占哪几个词** —— 这件事的**唯一一份实现**。

    为什么要有它（2026-10-15 抽，对应路线图 D5 的"坏味道"）：
    在此之前，这件事有**三套并行实现**，规则已经漂移过：

      · `pattern.match_line`  —— 试切，递归回溯、会"切不上"；
      · `story/spec_args._claim`              —— 卡片读值那一侧的单趟认领；
      · `story/spec_args.slot_value_map`      —— 又把 `_claim` 抄了一遍。

    漂移的后果正是用户看到的那种自相矛盾："**试切说切上了、卡片读出来不一样**"。
    现在只有这一份；`match_line` 与 `_claim` 都只是它的**薄转发**。

    返回::

        {'spans': {第几格: (词起点, 词终点)},   # 终点不含；没认领到的不在里面
         'values': {第几格: 值},               # 显示/写回用（`_span_text`）
         'used': 被认领了几个词（**不是**"吃到第几个词"：允许跳词，所以它等于
                 `sum(spans 的长度)`，不再假装整行都被吃满了）,
         'bad_kw': 第几格的关键字对不上（或 None）,
         'rejected': 第几格勾了"只能选这些"却来了候选外的词（或 None）,
         'best_spans': 与 `spans` **同一份**（历史键，界面用它排版）,
         'broken': 哪几格"吃到了空引号"（= 那一格这一行是空的，见下）,
         'exact': 是不是"整行都有解"的那一次分配（= 四类失败一个都没有）}

    ⚠⚠ 2026-10-17 第二轮攻击起**没有 `policy` 参数了**：以前试切用
      `'order-first'`、卡片用 `'candidates'`，两种策略会给出**不同的格子值**
      （试切显示 `服装=junk`、卡片读出 `服装=base_swimsuit`）—— 同一个病只是
      换了个触发条件。现在"跳过垃圾词去认后面的候选"进了**代价表**，
      两处逐字同源，一个答案。

    ⚠⚠ **空引号要"废掉那一格再重排"**（2026-10-18 第三轮攻击修）：
      `show lan "" base_swimsuit happy` 里 `""` 占着「服装」的位置，但它是个
      **输入错误**（Ren'Py 里是空属性名），不是"服装 = base_swimsuit"。
      第一遍分配先问清"这个空引号落在哪一格"（空引号**不许跳过**、**也不许让给
      下一格**：轮到取值格而下一个词是空引号时，`_spans` 里这一格只有"吃掉它"
      一个分支，见那里的注释 —— 这是 2026-10-18 第四轮攻击修的，之前 DP 会把
      空引号推给下一格，于是"服装空"变成"服装=base_swimsuit"、值整体左移一格）；
      落在第 i 格，就把第 i 格标成 **dead**
      （这一行它没有值：不吃词、不占词），再把那些空引号从词流里去掉重排一次 ——
      于是 `base_swimsuit` 落到「表情」、`happy` 算多出来的，不会整体左移一格，
      也不会把后面的值吸进空着的那一格。
      `dead` 的候选词仍然"有主"（谁都不许跳过它们），但对别人不再是"抢"——
      它们是**没人要**的（见 `_tok_cost`）。
    """
    tokens = [str(t) for t in (tokens or ()) if str(t)]
    slots = [dict(s) for s in (slots or ()) if isinstance(s, dict)]
    groups = groups or {}
    cost, spans = _spans(tokens, slots, groups)
    #: 哪几格"吃到了空引号"：`{第几格: 占位符在词里的下标}`
    broken = {si: a for si, (a, b) in spans.items()
              if b == a + 1 and 0 <= a < len(tokens) and tokens[a] == EMPTY_TOKEN}
    if broken:
        #: 去掉占位符之后的词 → 原文下标（写回/报错仍然要用原文的下标）
        keep_toks = [i for i in range(len(tokens)) if i not in set(broken.values())]
        cost, sub_spans = _spans([tokens[i] for i in keep_toks], slots, groups,
                                 dead=broken.keys())
        spans = {si: (keep_toks[a], keep_toks[b - 1] + 1)
                 for si, (a, b) in sub_spans.items()}
        for si, a in broken.items():
            spans[si] = (a, a + 1)      # 那一格的区间就是那对空引号本身
    got = _report(tokens, slots, spans, groups)
    got['spans'] = spans
    got['best_spans'] = dict(spans)      # ⚠ 同一份：失败时界面不许另摆一套切法
    got['broken'] = sorted(broken)
    # ⚠ "切上了"**不能**用 `cost == 0` 判：吃一个谁都不认识的词（自由文本，真机
    #   常态）本身就要 2 点，于是 `lan base_swimsuit happy`（角色那格没给候选）
    #   会算成 4 点 —— 那是**正常的**，不是失败。失败只有四种，全部由 `_report`
    #   从同一份 `spans` 倒推：关键字对不上 / 限死挡词 / 该吃词的格子空着 / 有词没人认领。
    # ⚠ 空引号那一格**永远算没切上**（2026-10-18）：`exact` 一旦为真，落盘/导入
    #   那边就会把这一行当成"切好了"，而 `""` 在 Ren'Py 里跑不起来。
    got['exact'] = (got['bad_kw'] is None and got['rejected'] is None
                    and not got['unfilled'] and not got['leftover']
                    and not broken)
    got['cost'] = cost
    return got


def _lex_reason(problem, base: int = 0) -> str:
    """
    词法毛病 → **一句人话**（`match_line` 的 reason 用）。

    `kind='empty'`（引号里没有值）与 `kind='unclosed'`（引号没关上）说的
    完全不是一件事，所以**不能共用一句话**：

      · `show lan "" happy` → 人写的是空属性名，Ren'Py 跑不起来。
        想留空就别写那个词（`show lan happy`），所以这句要说"删掉它 / 改成值"；
      · `show lan "base swimsuit` → 少一个闭引号，后半行被吞成一个词，
        要说"没关上"，否则人看到"切不上"根本想不到是引号。

    ⚠ 两种情况都带**第几个词**（人敲的第几个），否则人不知道改哪一处。
    """
    info = dict(problem or {})
    at = _as_count(info.get('at')) + _as_count(base) + 1
    quote = str(info.get('quote') or chr(34))
    if str(info.get('kind') or '') == 'empty':
        shown = str(info.get('raw') or (quote + quote))
        return (f'第 {at} 个词的位置上是一对空引号「{shown}」—— 引号里没有值'
                f'（Ren\'Py 会把它当成一个空属性名，跑不起来）；'
                f'删掉它，或者把它改成那一格真正的值')
    if info.get('at') is not None:
        return (f'第 {at} 个词的位置上有个引号「{quote}」没关上'
                f' —— 后面整段都被当成一个词了')
    return str(info.get('why') or '引号没关上')       # 老形状（只有 why）


def match_line(pattern, line, *, groups=None, word_offset: int = 0) -> dict:
    """
    **把一行样例切成格子** —— 界面靠它当场告诉用户"你填的顺序对不对"。

    `word_offset`：这一行**已经被摘掉了开头几个词**时报错要补上的号
    （界面会摘掉行首的动作名 `show` —— 人粘的是整行，说的"第几个词"必须是
    **他粘的那一行**里的第几个，不是摘完之后的）。

    返回::

        {'ok': bool,
         'cells': [{'kind','text','column','group','ok'}…],   # 每格吃到的字
         'leftover': [没被认领的词…],                          # 多出来的
         'missing': [第几格没吃到东西…],
         'lex_problem': 词法毛病 or None,                      # 空引号 / 引号没关上
         'reason': 人话（不 ok 时说明**卡在第几个词、第几格**）}

    ⚠ 分配规则**不在这里** —— 它只有一份，见 `assign_tokens`。
    这里只做三件"试切"独有的事：把词交出去、把结果**对齐到格子下标**
    （缺席的格留一个空格子）、把"切不上"说成**人话**。

    ⚠ 报错必须**精确到词**（用户 2026-10-17 的第四条）：只说"检查顺序"
      等于让人瞎猜；所以每一条 `reason` 都同时给出**第几个词**和**第几格**。
    """
    slots = parse_pattern(pattern)
    groups = groups or {}
    #: ⚠ 词法与"人敲的第几个词"来自**同一份** `_tokens_meta`（2026-10-18）：
    #:   `小明泳装` 会被切成两个**词**，但人按空格只数到**一个** —— 报错说
    #:   "第几个词"必须按人数的来，否则他照着数字去改，改的是别处。
    meta = _tokens_meta(line, options=_all_options(slots, groups))
    tokens = meta['tokens']
    words = meta['words']
    n_words = meta['n_words']
    lex_problems = meta['problems']
    got = assign_tokens(tokens, slots, groups=groups)
    ok = bool(got['exact']) and not lex_problems
    #: 行首被摘掉的词数（只影响报错序号）。
    #:
    #: ⚠ 必须**容错**（2026-10-17 第二轮攻击修）：`int('x')` 会抛 `ValueError`，
    #:   而这是给界面按键刷新用的函数 —— Qt 槽吞掉异常后，用户看到的是
    #:   "点了没反应"，不是报错。脏一次就够，所以这里绝不抛。
    base = _as_count(word_offset)

    cells = [_cell(slot, got['values'].get(i, ''),
                   set(_allowed_of(slot, groups)))
             for i, slot in enumerate(slots)]
    leftover = got['leftover']
    missing = [i for i, c in enumerate(cells) if not str(c['text']).strip()]

    def _word(idx):
        """第 `idx` 个词 → `第 N 个词「词」`；越界 → 空串。

        ⚠ `N` 是**人敲的第几个词**（`meta['words']`），不是词的下标。
        ⚠ 这是"报错精确到词"的唯一出处（用户 2026-10-17 第四条）：
          每条失败原因都必须能对上**具体是哪一个词**，不能只说"检查顺序"。
        """
        if idx is None or not (0 <= idx < len(tokens)):
            return ''
        return f'第 {words[idx] + base} 个词「{_show_token(tokens[idx])}」'

    def _word_no(idx):
        """`第 N 个词` —— **允许 `idx == len(tokens)`**：那是"话说到头了"。

        ⚠ 到头时用"人敲的词数 + 1"（不是"词的下标 + 1"）：中文连写会让词比
          人敲的多，报"第 4 个词"而这一行只有 2 个词，人只会更糊涂。
        """
        if idx is None:
            return ''
        if 0 <= idx < len(tokens):
            return f'第 {words[idx] + base} 个词'
        if idx == len(tokens):
            return f'第 {n_words + 1 + base} 个词'
        return ''

    if ok:
        reason = ''
    elif lex_problems:
        reason = _lex_reason(lex_problems[0], base)
    elif got['bad_kw'] is not None:
        i = got['bad_kw']
        want = str(slots[i].get('text') or '').strip()
        if not want:
            # 空固定段：说"对不上"等于让人去猜一个**根本不存在的字**。
            reason = (f'第 {i + 1} 格是「固定几个字 / 可出现可不出现」，'
                      f'但没写"是哪几个字" —— 这一格永远对不上')
        else:
            at = got['empty_at'].get(i)
            reason = (f'第 {i + 1} 格要的关键字是「{want}」，这一行里对不上'
                      + (f'（这里是{_word(at)}）' if _word(at) else '')
                      + ' —— 检查顺序、关键字写法')
    elif got['rejected'] is not None:
        i = got['rejected']
        word = str(got.get('rejected_word') or '')
        at = got.get('rejected_at')
        reason = (f'第 {i + 1} 格勾了"只能从这些里选"，'
                  f'而这一行里的词不在候选里'
                  + (f'（挡下的是{_word(at)}）' if _word(at)
                     else (f'（挡下的是「{word}」）' if word else '')))
    elif leftover:
        first = got['leftover_at'][0] if got.get('leftover_at') else None
        more = len(leftover) - 1
        reason = (f'{_word(first)}没人认领' if _word(first)
                  else f'有 {len(leftover)} 个词没人认领：「{leftover[0]}」')
        if more:
            reason += f'（后面还有 {more} 个：{"、".join(leftover[1:6])}'
            reason += '…）' if more > 5 else '）'
        reason += ' —— 检查顺序、关键字写法，或者是不是多写了一段'
    else:
        i = got['unfilled'][0]
        name = str(slots[i].get('column') or slots[i].get('text') or '').strip()
        at = got['empty_at'].get(i)
        where = f'在{_word_no(at)}的位置上' if _word_no(at) else ''
        reason = (f'第 {i + 1} 格（{name or "这一格"}）{where}该有一个词，'
                  f'但这一行里没有（这一行共 {n_words} 个词）'
                  f' —— 检查顺序、关键字写法，或者是不是少了哪一段')
    return {'ok': ok, 'cells': cells, 'leftover': leftover,
            'missing': missing,
            'rejected': got['rejected'], 'rejected_word': got['rejected_word'],
            #: 词法层的病根（空引号 / 引号没关上）—— 界面据此把它**排在第一位**：
            #: 它是这一行真正的病因，"哪个词没人认领"只是后果。
            'lex_problem': dict(lex_problems[0]) if lex_problems else None,
            'reason': reason}


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
    """
    把所有格子的毛病汇总成**人话**清单（界面直接显示，不用自己拼）。

    ⚠ 除了每格自己的毛病，还查一条**跨格**的：**列名重名**
    （2026-10-13 加）。两个格子叫同一个列名时，切行照样切得出来，
    但它们在卡片/表里是**同一列** → 两个值互相覆盖，而人看不出来。
    这种"切得上、但结果不对"的毛病正是这个函数存在的理由。
    """
    parsed = parse_pattern(pattern)
    out = []
    for i, s in enumerate(parsed, 1):
        for p in s.get('problems') or []:
            out.append(f'第 {i} 格：{p}')
    seen: dict = {}
    for i, s in enumerate(parsed, 1):
        name = str(s.get('column') or '').strip()
        if not name:
            continue
        if name in seen:
            out.append(f'第 {seen[name]} 格与第 {i} 格**重名**（都叫「{name}」）——'
                       f'它们在卡片与表里是同一列，两个值会互相覆盖；'
                       f'改掉其中一个的名字')
        else:
            seen[name] = i
    return out


__all__ = ['EMPTY_TOKEN', 'KIND_LABELS', 'SLOT_KINDS', 'assign_tokens',
           'columns_of', 'groups_used',
           'match_line', 'parse_pattern', 'problems_of',
           'token_spans', 'tokenize']
