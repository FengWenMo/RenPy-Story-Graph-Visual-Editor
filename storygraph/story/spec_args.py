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

def positional_tokens(head) -> list:
    """
    语句头之外的**位置参数** token → `[(起, 止, 原文), …]`（`止` 不含）。

    跳过四样东西：头一个词（语句名）、`key=value`（有名字的参数）、
    行尾的块标记 `:`、以及空串以外的一切空白。
    引号串**整串算一个**（`mycue "闪电"` 里那是一个参数，不是两个词）。
    """
    text = strip_block_colon(head)
    out: list = []
    i, n = 0, len(text)
    named_spans = [(m.start(0), m.end(0)) for m in iter_kv_text(text)]
    in_named = lambda pos: any(a <= pos < b for a, b in named_spans)  # noqa: E731
    first = True
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


def positional_values(head) -> list:
    """位置参数的值（已去引号），按出现顺序。"""
    return [_unquote(tok) for _s, _e, tok in positional_tokens(head)]


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

    ⚠ 只认**恰好一个**分隔符、且两段都是标识符：`a·b·c` 不是我们的列名
    （宁可认不出，也不要把别人的列当成自己的 —— 认错一列会把值折进错误的参数里）。
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
    if (not re.fullmatch(r'[A-Za-z_]\w*', h)
            or not re.fullmatch(r'[A-Za-z_]\w*', p)):
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
    for header, value in (values or {}).items():
        _head, param, positional = parse_dynamic_full(header)
        if not param:
            continue
        clean = '' if value is None else str(value).strip()
        if positional is not None:
            text = set_positional_arg(text, positional, clean)
        else:
            text = set_spec_arg(text, param, clean)
    return text


__all__ = [
    'SPEC_COLUMN_SEP', 'SPEC_KV_RE', 'BUILTIN_ARG_SCHEMAS',
    'strip_block_colon', 'iter_kv', 'iter_kv_text',
    'spec_arg_values', 'spec_arg_value', 'format_value',
    'set_spec_arg', 'fold_spec_args', 'builtin_columns',
    'positional_tokens', 'positional_values', 'set_positional_arg',
    'fold_arg_values', 'fold_dynamic_columns',
    'dynamic_column_name', 'parse_dynamic_column', 'parse_dynamic_full',
    'is_dynamic_column',
    'dynamic_headers', 'fold_dynamic_cells',
]
