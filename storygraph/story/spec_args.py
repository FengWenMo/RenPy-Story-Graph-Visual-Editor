"""
`storygraph.story.spec_args`
===========================
**语句头行的参数**（`key=value`）—— 唯一真源。

为什么要有这个模块（2026-10-07，M4 第二阶段：每参数一列）
-------------------------------------------------------
"参数"这件事此前只有**一个**消费者（`pipeline/validate` 的 `W120`/`W121`），
所以扫描函数就长在 `validate.py` 里。现在要"每参数一列"了，消费者变成三个：

  · `story.excel_export` —— 把参数摊成**列**、给每列挂原生下拉；
  · `story.beat_model`   —— 读表时把改了的那一列**折回头行**（`from_row`）；
  · `pipeline/validate`  —— `W120`/`W121` 要按**改后**的值判。

而契约 H1 不许 `story` 层 import `pipeline` —— 三份各写一遍正则，
迟早在某处漂移（"同一个值两处一个说合法、一个说非法"）。所以把它挪到
`story` 这一层，`validate` 反过来 import 它。

**单真源仍然是头行**（`Beat.spec_head`）
--------------------------------------
"每参数一列"是**编辑面**，不是第二份数据：读表时把**真的改了**的参数
折回头行（`fold_spec_args`），于是：

  · 没改 → 折出来与原来逐字节相同（`set_spec_arg` 里有等式守卫）→
    "打开表再导出，一个字节都不动"照旧成立；
  · 改了 → 只替换**那个值**，头行其余部分（缩进、参数顺序、别的参数的
    写法、行尾注释）一个字节不动。

这样 `SpecHead` 仍是唯一真源，`collab_store` / 渲染 / 校验 / 卡片
四条路**都不需要知道有"参数列"这回事** —— 它们看到的只是"头行变了"。
"""

from __future__ import annotations

import re

#: 动态列名的分隔符：`mycue·intensity`（语句名 · 参数名）。
#:
#: 为什么不用 `_` 或 `.`：语句名与参数名都是标识符（`[A-Za-z_]\w*`），
#: 用它们**分不出**"语句名里的下划线"与"分隔符"。`·` 是可见的非标识符，
#: 人一眼能读出层次，机器一眼能切开。
SPEC_COLUMN_SEP = '·'

#: 从语句头行里抽 `key=value`（值可以是引号串或裸 token）。
SPEC_KV_RE = re.compile(
    r'\b([A-Za-z_]\w*)\s*=\s*'
    r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^\s,)\]]+)')


def strip_block_colon(head) -> str:
    """
    去掉头行末尾的**块标记** `:`（`mycue "x" intensity=3:` → `…intensity=3`）。

    ⚠ 不剥的话 `target=ling:` 会被当成值 `ling:` —— 明明写对了却报
    "值不在候选里"（2026-10-07 实测踩到，判据当场抓到）。
    """
    text = str(head or '').rstrip()
    return text[:-1].rstrip() if text.endswith(':') else text


def _unquote(token: str) -> str:
    """值 token → 人话值（去掉配对的引号，还原转义）。"""
    v = str(token or '').strip()
    if len(v) >= 2 and v[:1] in ('"', "'") and v[-1:] == v[:1]:
        inner = v[1:-1]
        if v[:1] == '"':
            inner = inner.replace('\\"', '"').replace('\\\\', '\\')
        return inner
    return v


def iter_kv(head):
    """
    头行里的 `key=value` 匹配（按出现顺序），**跳过字符串字面量内部**。

    为什么要跳过引号里那一截：`mycue "a=b" intensity=3` 里那个 `a=b`
    是**台词/参数值的内容**，不是参数。原实现（`validate` 里那份）会把
    它一起收进来 —— 于是"每参数一列"会为一个不存在的参数生成一列，
    而改那一列会把引号里的文字改掉（改写台词）。
    引号状态必须自己扫（正则照顾不到嵌套引号）。
    """
    return iter_kv_text(strip_block_colon(head))


def iter_kv_text(text):
    """`iter_kv` 的**已剥冒号**版本（`set_spec_arg` 自己先剥过一次）。"""
    text = str(text or '')
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < n:
                if text[i] == '\\':
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        m = SPEC_KV_RE.match(text, i)
        if m is not None:
            yield m
            i = m.end()
            continue
        i += 1


def spec_arg_values(head) -> dict:
    """
    头行 → `{参数名: 值}`（值已去引号）。

    只认 `key=value`（含 `key="v"`）；位置参数**不算** —— 位置参数没有名字，
    拿"第几个词"去对声明里的参数名会是**猜**，而猜错会指着一处根本不存在的
    错误让人去改（本仓库对"猜"一贯不收）。
    """
    out: dict = {}
    for m in iter_kv(head):
        out.setdefault(m.group(1), _unquote(m.group(2)))
    return out


def spec_arg_value(head, name) -> str:
    """单个参数的值（没有这个参数 → 空串）。"""
    return spec_arg_values(head).get(str(name or ''), '')


def format_value(value) -> str:
    """值 → 能安全拼进一行语句的 token（有空白/引号就加引号）。"""
    v = str(value if value is not None else '')
    if v == '' or re.search(r'[\s,)\]"\']', v):
        return '"' + v.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return v


def set_spec_arg(head, name, value) -> str:
    """
    头行里把参数 `name` 的值改成 `value` —— **没变就一个字节都不动**。

    三种情况：

      · 值没变（含"原文带引号、解出来一样"）→ 原样返回。这条等式守卫是
        "每参数一列"能安全加进来的前提：表里每一格都会把值写回来，
        没有它，`target="ling"` 会被改写成 `target=ling`（引号被吃掉）
        —— 一次导出就改动了别人的排版；
      · 值变了 → **只替换那个值 token**，头行其余部分逐字保留；
      · 头行里没有这个参数 → 值非空就追加在末尾（块标记 `:` 之前）、
        值为空就什么都不做（"清空一个不存在的参数"不是编辑）。

    空值 = **删掉这个参数**（策划把那个格子清空，意思就是"别带这个参数"）。
    """
    text = str(head or '')
    name = str(name or '')
    if not name:
        return text
    body = strip_block_colon(text)
    colon = text[len(body):] if text.rstrip().endswith(':') else ''
    new_value = str(value if value is not None else '')

    for m in iter_kv_text(body):
        if m.group(1) != name:
            continue
        if _unquote(m.group(2)) == new_value:
            return text                       # ← 没改：逐字节原样
        if new_value == '':
            start = m.start(0)
            while start > 0 and body[start - 1] in ' \t':
                start -= 1                    # 连它前面多余的空格一起删
            return body[:start] + body[m.end(0):] + colon
        return body[:m.start(2)] + format_value(new_value) + body[m.end(2):] \
            + colon

    if new_value == '':
        return text
    return f'{body} {name}={format_value(new_value)}{colon}'


def fold_spec_args(head, args) -> str:
    """
    把一组"参数列的值"折进头行（`{参数名: 值}`，按给定顺序逐个应用）。

    顺序无关紧要：每个参数只动自己那一个值 token。
    """
    text = str(head or '')
    for name, value in (args or {}).items():
        text = set_spec_arg(text, name, value)
    return text


# ---------------------------------------------------------------------------
#  动态列名
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  位置参数（**只按声明认，不猜**）
# ---------------------------------------------------------------------------
#
# 为什么单独一节：`camera background:` 的参数是**位置**参数（层名），
# `mycue "闪电" intensity=3` 的标题也是位置参数 —— 它们没有 `key=`，
# 光看一行文本**分不出**"第几个词是哪个参数"。
#
# 本仓库对"猜"一贯不收（M8：位置参数不当成有名字的参数，硬对齐是猜）。
# 所以这里只做**机械的一步**：把"头之外的、既不是 `key=value` 也不是头一个词
# 的那些 token"按顺序列出来（含引号串，整串算一个）。**谁对应谁**由调用方
# 声明的 schema 决定 —— 声明了就编辑，没声明就一个都不碰。

def positional_tokens(head, skip_head: bool = True) -> list:
    """
    语句头之外的**位置参数** token → `[(起, 止, 原文), …]`（`止` 不含）。

    跳过四样东西：头一个词（语句名，`skip_head=True` 时）、`key=value`
    （有名字的参数）、行尾的块标记 `:`、以及空串以外的一切空白。
    引号串**整串算一个**（`mycue "闪电"` 里那是一个参数，不是两个词）。

    ⚠ `skip_head=False`（2026-10-13 加）：调用方**已经把动作名摘掉了**时用 ——
    这时第一个词就是**第一个参数**（`show` 摘下后的 `lan`），再跳一次就整体左移
    （实测：`人物` 那格读成了 `swim_suit`）。
    """
    text = strip_block_colon(head)
    out: list = []
    i, n = 0, len(text)
    named_spans = [(m.start(0), m.end(0)) for m in iter_kv_text(text)]
    in_named = lambda pos: any(a <= pos < b for a, b in named_spans)  # noqa: E731
    first = bool(skip_head)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        if in_named(i):
            # 跳到这个 `key=value` 的末尾（它整体是一个有名字的参数）
            i = max(b for a, b in named_spans if a <= i < b)
            continue
        start = i
        if text[i] in ('"', "'"):
            quote = text[i]
            i += 1
            while i < n:
                if text[i] == '\\':
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
        else:
            while i < n and not text[i].isspace():
                i += 1
        token = text[start:i]
        if first:
            first = False          # 第一个 token 是**语句头**，不是参数
            continue
        if token and token != ':':
            out.append((start, i, token))
    return out


def positional_values(head, skip_head: bool = True) -> list:
    """位置参数的值（已去引号），按出现顺序（`skip_head=False` 见 `positional_tokens`）。"""
    return [_unquote(tok) for _s, _e, tok in positional_tokens(head,
                                                               skip_head=skip_head)]


def head_is_a_pattern_slot(head, pattern) -> bool:
    """
    动作名**自己在格子里**吗？（`head` = 整条语句头原文，`pattern` = 声明的格子）

    这一条决定"位置参数从哪个数起"（2026-10-13 实测修，用户现场）：

        A. 格子只有「人物 / 服装 / 表情」，动作名 `show` **不在格子里**
           → 粘 `show lan swim_suit happy` 时，`show` 之后的**第一个参数**
             才是「人物」→ 数位置参数前要把动作名摘掉；
        B. 格子第一格就是"固定几个字 `show`"（用户可能的另一种填法）
           → 动作名**占一格**，位置参数从它开始数，**不能**摘。

    ⚠ 只认"头一个格子、而且是 keyword、文字正好等于动作名"——其余一律当 A
    （**不猜**别的形态）。
    """
    text = strip_block_colon(str(head or '')).strip()
    if not text:
        return False
    first = text.split()[0]
    for raw in (pattern or ()):
        if not isinstance(raw, dict):
            return False                    # 旧形状（字符串列名）→ 当 A
        kind = str(raw.get('kind') or '')
        slot_text = str(raw.get('text') or '').strip()
        return kind == 'keyword' and slot_text == first
    return False


#: 走格子认领词时，**会吃掉一个词**的格子类型（2026-10-13 抽出这一条真源）。
#:
#: ⚠ `optional` **不在里面**：它"可出现可不出现"，吃不吃词取决于下一格 ——
#:   读值那一侧（`pattern_slot_values`）单独判；数位置号时按"不吃"处理。
#: ⚠ 这张表与"谁敢占位置号"是**同一件事** —— 关键字格也吃词、也占号，
#:   否则读值就会把关键字本身当成旁边那格的值（实测：三格只对一格）。
WALK_KINDS = ('value', 'variable', 'choice', 'keyword')


def pattern_slot_values(head, pattern, options=None) -> dict:
    """
    **拿声明的格子去认领这一行的词** → `{第 i 格: 词}`（只认领得出的才在里面）。

    为什么要有它（2026-10-13 实测的第三次修正）：位置号（`@0/@1`）那套算法
    **数不准**——`keyword` 格（固定几个字）**不占位置号却确实吃掉一个词**：

        show lan swim_suit happy
        [人物=任意] [swim_suit=固定] [happy=从这些选]
        实测：位置号法把 `swim_suit` 给了「人物」，把 `happy` 给了「惊喜」…
              （用户现场就是"三格只对一格"）

    所以这里不再"算下标"，而是**按格子顺序认领词**：
      · `keyword` 格：词必须**逐字相等**才认领（认领后吃掉它）；
      · `variable` / `choice` 格：吃掉一个词；
      · `multi` 格（"这一格可以有多个"）：吃掉**连续的一串词**，但
        **不吃后面格子的固定字**（`keyword.text` / `optional.text`）——
        否则 `mypose happy sad at left` 会把 `at left` 一起吃进「表情」；
      · `optional` 格：下一个词等于它的文字就吃掉两个词，否则跳过
        （**读值也要认它** —— 2026-10-13 修：以前这里跳过，于是「位置」
        那一格永远空着，人以为剧本里没写位置）。
      · 认不出的格子 -> 不放进结果（调用方显示空格子，**不猜**）。

    ⚠ `head` 传的是"**动作名之后**"的部分（调用方已经摘掉动作名）。
    """
    # ⚠⚠ 这一段**整段重写**过（2026-10-13）：原来的写法在"多值格 + 可选段"
    #   同时出现时会串位（多值格只吃到一个词 / 可选段读成空）。重写的原则没变：
    #   **按格子顺序认领词**，只是把"能吃几个"写得更直白：
    #     1. `keyword`：必须逐字相等；
    #     2. `optional`：下一个词正好是它的字 → 吃两个字；
    #     3. `multi`：连续吃到"下一个词属于后面某格的固定字"或"吃不动"为止；
    #     4. `choice`：候选里的词**优先**（不要求它就在当前位置，中间隔着的
    #        词留给后面的格子）；候选外的词也认（候选 ≠ 限死，见 `limit`）；
    #     5. 其它：吃一个词。
    #   ⚠ 认领不出来时**不放进结果**（调用方显示空格子，不猜）。
    tokens = [t for t in str(strip_block_colon(head or '')).split() if t]
    slots = [s for s in (pattern or ()) if isinstance(s, dict)]
    # ⚠⚠ **与 `slot_value_map` 同一份实现**（2026-10-13 的决定）：
    #   这两处以前各写一遍"顺序认领"，两套口径 → 卡片显示的值与试切切出来的
    #   值会对不上（多值格尤其明显）。现在只保留 `slot_value_map` 一份。
    by_column = slot_value_map(head, slots, options=options)
    result: dict = {}
    for i, slot in enumerate(slots):
        keys = [str(slot.get('column') or '').strip()]
        if str(slot.get('kind') or '') == 'optional':
            keys.append(f"{keys[0]}+{str(slot.get('text') or '').strip()}")
        keys.append(str(slot.get('text') or '').strip())
        hit = next((by_column[k] for k in keys
                    if k and by_column.get(k)), None)
        if hit is not None:
            result[i] = hit
    return result


def _claim(head, pattern) -> dict:
    """
    **顺序认领器** —— 一行 + 一串格子 → `{第 i 格: 词}`。

    ⚠⚠ 2026-10-15 起它是 `authoring.pattern.assign_tokens` 的**薄转发**。
    在此之前，"把一行词分配到格子"这件事在仓库里有**三套并行实现**
    （`pattern.match_line` 的递归回溯 / 这里的单趟认领 / `slot_value_map`
    又抄了一遍），规则漂移过 —— 用户看到的"试切说切上了、卡片读出来不一样"
    就是它。现在分配规则**只有那一份**，这里只负责切词与返回形状。

    策略用 `'candidates'`：**读值那一侧的历史口径** —— 枚举格允许"跳过中间那些
    词、去认后面出现的候选"（把候选当词典用）。试切那一侧用 `'order-first'`
    （按书写顺序吃当前这个词）；两种策略的分岔点写在 `assign_tokens` 的
    `POLICIES` 上，并由判据钉住，不再靠"两套代码各写一遍"。
    """
    from storygraph.authoring.pattern import assign_tokens
    tokens = [t for t in str(strip_block_colon(head or '')).split() if t]
    slots = [s for s in (pattern or ()) if isinstance(s, dict)]
    got = assign_tokens(tokens, slots, policy='candidates')
    return {i: ' '.join(tokens[a:b]) for i, (a, b) in got['spans'].items()}


def slot_span(head, pattern, index) -> tuple:
    """
    第 `index` 格在 `head` 里的**字符区间** `(start, end, text)`（认不出 → `('', '', '')`）。

    为什么要"区间"而不是"位置号"（2026-10-13 实测）：
      · **多值格**（"这一格可以有多个"，`mypose happy sad`）占**两个词的宽度** ——
        按位置号只会换掉第一个词，`sad` 留在原地 → 一行里出现
        `mypose smile sad`（谁也没想要）；
      · **可选段**（`at left`）在位置号那套里**没有号**，于是那一格永远写不回去。

    找法是"认领出来的那段原文，在行里按**整词**出现的位置"：
    只在恰好处出现一次才给区间（出现多次 / 找不到 → 认不出，**不猜**）。
    """
    text = str(strip_block_colon(head or ''))
    slots = [s for s in (pattern or ()) if isinstance(s, dict)]
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return '', '', ''
    if not (0 <= idx < len(slots)):
        return '', '', ''
    # ⚠⚠ **先按格子直查**（2026-10-14 修，这是折回"整段换值"最后一块）。
    #
    # `slot_value_map` 的键是**格子上的列名**（`角色` / `服装`），
    # 而调用方手上的列可能叫**另一个名字**（声明里 `columns[].name` = `who`）
    # —— 于是"按列名查"会**查空或查错**：
    #   · 查空 → 区间认不出 → 兜底去**追加**（实测行尾多一个值
    #     `swim_suit lyj "泳装" mood=happy 校服`）；
    #   · 更糟的是**假命中**（拿 `角色` 查到了关键字的键），
    #     于是连兜底都走不到、区间还算错。
    # `_claim` 是**按格子顺序**认领原文的，对"列叫什么名字"完全不敏感 ——
    # 所以它才是这一侧的真源（与读值那一侧的 `slot_value_map` 同一套走法）。
    slot = slots[idx]
    value = _unquote(str(_claim(text, slots).get(idx) or '').strip())
    if not value:
        # 兜底：按列名查（`_claim` 认不出时才走这里，**不猜**）
        m = slot_value_map(text, slots)
        keys = [str(slot.get('column') or '').strip()]
        if str(slot.get('kind') or '') == 'optional':
            keys.append(f"{keys[0]}+{str(slot.get('text') or '').strip()}")
        keys.append(str(slot.get('text') or '').strip())
        value = next((str(m[k]).strip() for k in keys if k and m.get(k)), '')
    if not value:
        return '', '', ''
    # ⚠⚠ **按词扫一遍找这一段**（2026-10-14 定稿，不再用正则找边界）。
    #
    #   用正则找"整词边界"连着错了两版：行上是 `"泳装"`，而那一格的**值**
    #   （去了引号）是 `泳装` —— 值的左右两边是**引号**不是空白，
    #   `(?<!\S)` 匹配不到 → 区间认不出 → 兜底去**追加**，
    #   实测行尾多出一个值（`swim_suit lyj "泳装" mood=happy 校服`）。
    #   引号、书名号、破折号…… 靠枚举"哪些字符算边界"永远会漏。
    #
    #   所以直接**按空白切词**（与 `spec_arg_values` / `positional_tokens`
    #   同一套词法），把每个词的原文区间记下来，再找"**去引号之后等于这个值**
    #   的那些词"；多词的值（`at left`）按**连续几个词**匹配。
    #   一个词都不多、一个词都不少 —— 认不出就返回空（**不猜**）。
    words: list = []                              # [(start, end, raw)]
    pos = 0
    for tok in str(text or '').split():
        at = text.find(tok, pos)
        if at < 0:
            break
        words.append((at, at + len(tok), tok))
        pos = at + len(tok)
    needle = str(value or '').split()
    if not needle:
        return '', '', ''
    for i in range(len(words) - len(needle) + 1):
        span = words[i:i + len(needle)]
        joined_raw = ' '.join(w[2] for w in span)
        joined_val = ' '.join(_unquote(w[2]) for w in span)
        if joined_val == ' '.join(needle) or joined_raw == ' '.join(needle):
            return span[0][0], span[-1][1], value
    return '', '', ''


def set_slot_value(head, pattern, index, value) -> str:
    """
    第 `index` 格 → `value`（按**区间**换；认不出区间就退回位置号那套）。

    ⚠ 值没变 → **逐字节原样**（与 `set_spec_arg` / `set_positional_arg` 同一条约定：
    "打开表再导出一个字节都不动"）。值为空 → 把那一格删掉。
    """
    text = str(head or '')
    new_value = str(value if value is not None else '').strip()
    start, end, old = slot_span(text, pattern, index)
    if not start and not end:
        # 区间认不出（值本来就不在行里 / 出现多次）→ **追加/删除**都别硬来：
        # 非空值追加在尾冒号之前，空值什么都不做（不猜）。
        if not new_value:
            return text
        return set_positional_arg(
            text, _positional_index_of(pattern, index), new_value)
    if old == new_value:
        return text                             # ← 没改：逐字节原样
    if not new_value:
        cut = start
        while cut > 0 and text[cut - 1] in ' \t':
            cut -= 1
        return text[:cut] + text[end:].lstrip(' \t')
    return text[:start] + new_value + text[end:]


def _positional_index_of(pattern, index) -> int:
    """
    第 `index` 格 ↔ 它前面**占位置号**的格子有几个（`keyword`/多值也占）。

    ⚠ 这是"区间认不出"时的**兜底**（与 `statement_registry._declared_positionals`
    同一套走法），认得出区间时根本用不到它。
    """
    slots = [s for s in (pattern or ()) if isinstance(s, dict)]
    return sum(1 for s in slots[:max(0, int(index))]
               if str(s.get('kind') or '') in WALK_KINDS)


def slot_value_map(head, pattern, options=None) -> dict:
    """
    **每一格去认领最贴近它的那段词** → `{列名或显示名: 词}`。

    ⚠⚠ 规则（2026-10-13 把地狱测试的三处期望放在一起才看清）：

        [人物] [表情(multi)] [可选段 at 位置]
        happy sad          → 人物=happy、表情=sad           ← 表情只拿"它这一段"里的
        happy sad at left  → 人物=happy、表情=sad、位置=at left
        happy              → 人物=happy、表情=（空）

    也就是**贪心 + 让位**：每一格拿"最近的、属于它的"词；
    `multi` 格把**连续的一段**收进来，但**遇到后面格子的固定字就让位**
    （`at`）；前一层（`person`）拿不到的，才轮到它。

    ⚠ 与 `pattern.match_line`（试切那一路）**同一套口径**：那边是"带回溯的
    顺序走法"（能给出"切不上"和"哪一格没吃到"），这边是"给值"（更宽松，
    因为卡片只是把值显示出来给人改）。
    """
    tokens = [t for t in str(strip_block_colon(head or '')).split() if t]
    slots = [s for s in (pattern or ()) if isinstance(s, dict)]
    # ⚠⚠ **分配规则不在这里**（2026-10-15 抽，见 `pattern.assign_tokens`）。
    #   这一段以前是**第三份**拷贝 —— 与 `_claim` 同一套规则抄了一遍、只是
    #   返回形状不同；两处一起漂移的代价就是"试切说切上了、卡片读出来不一样"。
    #   现在只保留"按格子取词 → 去引号 → 挂到列名/原文键上"这一层。
    spans = _claim(head, slots)
    out: dict = {}
    for si, slot in enumerate(slots):
        got = str(spans.get(si) or '').strip()
        if not got:
            continue
        # ⚠⚠ **去掉引号**（2026-10-13 修，步骤 1 的②）：真机 `swim_suit lyj "泳装"`
        #   里那一格的值是 `泳装`，不是 `"泳装"` —— 卡片上带着引号显示、
        #   编剧一改就写出 `""泳装""`。`positional_values` 那侧一直走 `_unquote`，
        #   这一侧漏了（两套实现并行的又一个代价）。
        value = _unquote(got)
        if not value:
            continue
        kind = str(slot.get('kind') or 'variable')
        column = str(slot.get('column') or '').strip()
        text = str(slot.get('text') or '').strip()
        if column:
            out.setdefault(column, value)
        if text and kind == 'optional':
            # 可选段的**原文键**（`C+at`）也放一份：`slot_span` 要按原文找区间
            out.setdefault(f'{column}+{text}', value)
            out.setdefault(text, value)
        elif text and kind == 'keyword':
            out.setdefault(text, value)
    return out


def _replace_span(text: str, start: int, end: int, piece: str) -> str:
    return text[:start] + piece + text[end:]


def set_positional_arg(head, index, value) -> str:
    """
    把**第 `index` 个位置参数**（0-based）改成 `value` —— 没变就一个字节都不动。

    与 `set_spec_arg` 同一套三条约定：

      · 值没变（含"原文带引号、解出来一样"）→ 原样返回；
      · 值变了 → **只替换那一个 token**（其余空格、参数顺序、尾冒号不动）；
      · 值为空 → **删掉这个 token**（策划清空那一格 = 别带这个参数）；
      · 位置超出范围 → 值非空就追加在尾冒号之前。
    """
    text = str(head or '')
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return text
    if idx < 0:
        return text
    new_value = str(value if value is not None else '')
    toks = positional_tokens(text)
    if idx < len(toks):
        start, end, token = toks[idx]
        if _unquote(token) == new_value:
            return text                       # ← 没改：逐字节原样
        if new_value == '':
            cut = start
            while cut > 0 and text[cut - 1] in ' \t':
                cut -= 1
            return text[:cut] + text[end:]
        return _replace_span(text, start, end, format_value(new_value))
    if new_value == '':
        return text
    body = strip_block_colon(text)
    colon = text[len(body):] if text.rstrip().endswith(':') else ''
    return f'{body} {format_value(new_value)}{colon}'


def fold_arg_values(head, args, schema=None) -> str:
    """
    一组"参数列的值"折进语句头 —— `{参数名: 值}` + **schema**（声明形状）。

    `schema` 里的每一项可以带 `positional: <int>`（位置参数）或按名字
    （`key=value`）。**没有 schema 的不动任何东西**（不猜）。
    """
    text = str(head or '')
    pos_of = {}
    for col in (schema or ()):
        if not isinstance(col, dict):
            continue
        name = str(col.get('name') or '').strip()
        if not name:
            continue
        if isinstance(col.get('positional'), bool):
            continue
        if col.get('positional') is not None:
            try:
                pos_of[name] = int(col['positional'])
            except (TypeError, ValueError):
                continue
    for name, value in (args or {}).items():
        if name in pos_of:
            text = set_positional_arg(text, pos_of[name], value)
        else:
            text = set_spec_arg(text, name, value)
    return text


# ---------------------------------------------------------------------------
#  内置语句的参数形状（**我们自己声明，不猜**）
# ---------------------------------------------------------------------------
#
# 自定义语句的参数形状由项目声明（`rules.json` 的 `columns`）；而 `camera`
# 这类**已识别的标准语句**没人替它声明 —— 于是"每参数一列"只覆盖了自定义
# 语句那一半。补法两种：**猜**（拿"第几个词"去对参数名）或**声明一次**。
# 本仓库对猜一贯不收（M8：硬对齐是猜），所以选后者：按**官方语法**写死形状，
# `positional` = 第几个位置参数（0-based；头一个词是语句名、不算参数）。
#
# ⚠ 只写**语法上确定**的：`camera [层名]:`（3dstage.html：不给层名 = master）、
# `with <转场>`、`pause <秒>`、`window <show|hide|auto>`。
#
# `choices_from` = 候选来自**资源表的某一类**（`with` 的转场就是项目里真实存在的
# 变换）—— 由 Excel 那侧绑到候选表（自伸缩、无 255 字符上限），不是内联一串
# 会过期的值。**层名一律留空**：它是运行期概念（`config.layers` / `add_layer`），
# 项目里没有任何地方声明过"有哪些层"，编一组候选就是诱导人写错。
#
# ⚠ `with` / `pause` / `window` 走的是**「代码」列**那条路（`Beat.raw` +
# `beat.ordered`），不是 `opaque`：它们的参数列折回的是 raw 里**那一行**，
# 而且必须**同步 `ordered`**（`_ordered_code_lines` 是"池行 == 记录行"才认，
# 不同步就整份退回历史顺序 → 相邻指令翻序 = 记忆里的"缺口 4"）。
# 一格装不下两条同类语句时（同一拍里两个 `pause`）**不猜**：那一格留空。
BUILTIN_ARG_SCHEMAS = {
    'camera': [
        {'name': 'layer', 'label': '层名（空 = master）',
         'positional': 0, 'choices': [], 'required': False},
    ],
    'with': [
        {'name': 'transition', 'label': '转场（项目里的变换）',
         'positional': 0, 'choices': [], 'choices_from': 'transform',
         'required': False},
    ],
    'pause': [
        {'name': 'seconds', 'label': '秒数',
         'positional': 0, 'choices': [], 'required': False},
    ],
    'window': [
        {'name': 'action', 'label': '动作',
         'positional': 0, 'choices': ['show', 'hide', 'auto'],
         'required': False},
    ],
}


def builtin_columns(head) -> list:
    """这个语句头的**内置**参数形状（没有 → 空列表）。"""
    return [dict(c) for c in
            BUILTIN_ARG_SCHEMAS.get(str(head or '').strip(), ())]


def dynamic_column_name(head, param, positional=None) -> str:
    """
    `('mycue', 'intensity')` → `'mycue·intensity'`（列名/表头）。

    `positional` 非 None 时写成 `'camera·layer@0'` —— **位置位进列名**，
    因为读表那一侧（`Beat.from_row`）拿不到 schema：

      · 值变了要**替换第 N 个位置参数**（`camera background` → `camera screens`），
        而不是追加一个 `layer=…`（那对 `camera` 是**语法错**）；
      · 只认列名就能判，就不必把 schema 也搬进 `beat_model`（那是个很底层
        的模块，不该认识"项目声明"这套东西）。
    """
    h = str(head or '').strip()
    p = str(param or '').strip()
    if not h or not p:
        return ''
    name = f'{h}{SPEC_COLUMN_SEP}{p}'
    if positional is None or isinstance(positional, bool):
        return name
    try:
        idx = int(positional)
    except (TypeError, ValueError):
        return name
    return f'{name}@{idx}' if idx >= 0 else name


def parse_dynamic_full(header) -> tuple:
    """
    列名 → `(语句名, 参数名, 位置号 | None)`；不是动态列 → `('', '', None)`。

    ⚠ 只认**恰好一个**分隔符、且两段都非空、**两边都不含分隔符**：
    `a·b·c` 不是我们的列名（宁可认不出，也不要把别人的列当成自己的 ——
    认错一列会把值折进错误的参数里）。

    ⚠⚠ **参数名可以是中文**（2026-10-13 实测修，用户现场）：
    这里以前用 `[A-Za-z_]\\w*` 卡两段，于是 `show·人物@0`、`show·服装@1`
    这种列名**认不出来** → 卡片上那几格**永远是空的**（而界面还显示
    "列：人物/服装/神态"，看着一切正常）。
    病根与 `learn_statement` 里那个"中文列名被丢掉"是**同一个**：
    把"脚本具名参数"的标识符规则套到了**表格列名**上。
    列名是我们自己的列，中文天经地义；**只要不是空、并且不含分隔符**就认。
    """
    text = str(header or '').strip()
    positional = None
    if '@' in text:
        text, _sep, tail = text.rpartition('@')
        if not tail.isdigit():
            return '', '', None
        positional = int(tail)
    if not text or text.count(SPEC_COLUMN_SEP) != 1:
        return '', '', None
    h, p = (part.strip() for part in text.split(SPEC_COLUMN_SEP))
    if not h or not p:
        return '', '', None
    # ⚠ 两段**都不许以数字开头**（`1bad·x` / `a·1bad` 不是我们的列名 ——
    #   那是别人的表头），但**允许中文**（`show·人物@0` 是我们自己的列，见下）。
    if h[0].isdigit() or p[0].isdigit():
        return '', '', None
    # ⚠⚠ **左边那段必须是 ASCII 标识符**（2026-10-13 实测）：语句名是**代码里**的
    #   写法（`show` / `mycue` / `camera`），永远是 ASCII；而右边那段是**我们自己的
    #   表格列名**，中文天经地义（`show·人物@0`）。
    #   不加这一条，`SpecHead` 的中文表头「自定义语句·头行（高级）」会被当成
    #   动态列（`·` 正好在中间）—— 实测：它污染了"干净章节一列都不多"那条判据。
    if not re.fullmatch(r'[A-Za-z_]\w*', h):
        return '', '', None
    return h, p, positional


def parse_dynamic_column(header) -> tuple:
    """列名 → `(语句名, 参数名)`（**兼容视图**；位置号见 `parse_dynamic_full`）。"""
    head, param, _pos = parse_dynamic_full(header)
    return head, param


def is_dynamic_column(header) -> bool:
    """这个列名是不是"每参数一列"的列？"""
    return bool(parse_dynamic_column(header)[1])


def dynamic_headers(rows) -> list:
    """
    一批行（`{列名: 值}`，或表头序列）里出现的动态列名（**保序、去重**）。

    给"读表时把参数折回头行"用：**只折这次真的出现在表里的列**，
    这样老表（没有这些列）读回来一个字节都不动。
    """
    out: list = []
    for row in (rows or ()):
        keys = row.keys() if isinstance(row, dict) else row
        for key in keys or ():
            if is_dynamic_column(key) and key not in out:
                out.append(str(key))
    return out


def fold_dynamic_cells(row, values) -> dict:
    """
    `{参数名: 值}` ← 那些动态列（`values` 是 `{列名: 值}`）。

    认不出的列名直接忽略（见 `parse_dynamic_column`）。
    """
    out: dict = {}
    for header, value in (values or {}).items():
        _head, param = parse_dynamic_column(header)
        if not param:
            continue
        out[param] = '' if value is None else str(value).strip()
    return out


def fold_dynamic_columns(row, values) -> str:
    """
    **动态列（`{列名: 值}`）折进头行** —— 位置参数与 `key=value` 分开走。

    为什么按列名分而不是查 schema：读表那一侧（`Beat.from_row`）拿不到 schema，
    而"这一列是不是位置参数"必须判对 —— 判错的后果是往 `camera` 头上写
    `layer=background`（**语法错**，游戏起不来）。位置号因此**进列名**
    （`camera·layer@0`，见 `dynamic_column_name`）。

    值没变 → 逐字节原样（`set_spec_arg` / `set_positional_arg` 里各有一道
    等式守卫）；清空 → 删掉那个参数。
    """
    text = str(row or '')
    raw_pattern = list(values.get('__pattern__') or ()) if isinstance(values, dict) \
        else []
    for header, value in (values or {}).items():
        if header == '__pattern__':
            continue
        _head, param, positional = parse_dynamic_full(header)
        if not param:
            continue
        clean = '' if value is None else str(value).strip()
        # ⚠⚠ **有多值 / 可选段时按"格子区间"换**（2026-10-13 修）：
        #   位置号只会换掉第一个词，`mypose happy sad` 改成 smile 会留下
        #   `mypose smile sad`（谁也没想要）；而可选段（`at left`）在位置号
        #   里根本没有号，永远写不回去。认得出格子的区间就按区间换。
        if raw_pattern:
            # ⚠ 列名可能是 `outfit`（声明里的 `columns[].name`）而格子叫
            #   `服装` —— `_pattern_slot_index_for` 会回落到 `@N`，
            #   所以这里要把**整段列名**（含 `@N`）传进去，不能只传 `param`。
            idx = _pattern_slot_index_for(raw_pattern, header)
            if idx is None:
                idx = _pattern_slot_index_for(raw_pattern, param)
            if idx is not None:
                span = slot_span(text, raw_pattern, idx)
                if span[0] or span[1]:
                    text = set_slot_value(text, raw_pattern, idx, clean)
                    continue
        # ⚠⚠ **带位置号的列，也可能在行里是 `key=value`**（2026-10-13 实测修）：
        #   位置号是按"格子顺序"发给**每一格**的（关键字格也吃词，见
        #   `statement_registry._declared_positionals`），但关键字格在脚本里写成
        #   `mood=happy` —— 那种**不是位置参数**。按位置号去替换就成了
        #   `… mood=happy shy`（把新值**追加**到行尾，实测现场）。
        #   判据：这一行里**真的有这个 `param=`** 就按具名参数写回。
        if positional is not None and _has_named_param(text, param):
            text = set_spec_arg(text, param, clean)
            continue
        if positional is not None:
            text = set_positional_arg(text, positional, clean)
        else:
            text = set_spec_arg(text, param, clean)
    return text


def _pattern_slot_index_for(pattern, param: str):
    """
    列名（`param`）↔ 声明里第几格（认不出 → `None`）。

    ⚠⚠ **带 `@N` 的列名必须能用**（2026-10-14 修，这是折回"整段换值"最后一块）：
    声明里 `columns[].name`（`outfit`）与 `pattern[].column`（`服装`）**常常不同**
    —— 前者进表头、后者写在格子上。只按名字对，`outfit` 对不上 `服装`，
    于是 `idx` 是 `None` → **跳过区间那一支**、直接走位置号 →
    值被**追加**到行尾（实测 `swim_suit lyj "泳装" mood=happy 校服`）。

    所以名字对不上时**回落到 `@N`**：`dynamic_spec_columns` 发的 `@N` 就是
    格子的下标（见 `statement_registry._declared_positionals`），
    它与 `pattern` 的下标**同一套**。这条兜底只对"有位置号的列"生效。
    """
    want = str(param or '').strip()
    if not want:
        return None
    slots = [s for s in (pattern or ()) if isinstance(s, dict)]
    for i, slot in enumerate(slots):
        keys = {str(slot.get('column') or '').strip(),
                str(slot.get('text') or '').strip()} - {''}
        if want in keys:
            return i
    # 名字对不上 → 试"这一列是不是第 N 格"（`名字@N` 或纯数字都认）
    tail = want.rsplit('@', 1)[-1] if '@' in want else (
        want if want.isdigit() else '')
    if str(tail).isdigit():
        at = int(tail)
        if 0 <= at < len(slots):
            return at
    return None


def _has_named_param(text: str, param: str) -> bool:
    """这一行里有没有 `<param>=` 这个**具名参数**？（有就按 `key=value` 写回）"""
    want = str(param or '').strip()
    if not want:
        return False
    return any(str(m.group(1) or '') == want for m in iter_kv_text(str(text or '')))


__all__ = [
    'SPEC_COLUMN_SEP', 'SPEC_KV_RE', 'BUILTIN_ARG_SCHEMAS',
    'strip_block_colon', 'iter_kv', 'iter_kv_text',
    'spec_arg_values', 'spec_arg_value', 'format_value',
    'set_spec_arg', 'fold_spec_args', 'builtin_columns',
    'positional_tokens', 'positional_values', 'set_positional_arg',
    'fold_arg_values', 'fold_dynamic_columns',
    'slot_value_map', 'slot_span', 'set_slot_value', 'pattern_slot_values',
    'head_is_a_pattern_slot', 'WALK_KINDS',
    'dynamic_column_name', 'parse_dynamic_column', 'parse_dynamic_full',
    'is_dynamic_column',
    'dynamic_headers', 'fold_dynamic_cells',
]
