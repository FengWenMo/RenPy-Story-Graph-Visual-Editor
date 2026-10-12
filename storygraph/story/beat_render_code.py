"""
`beat_render` 的**代码块族**（2026-10-12 从 `beat_render.py` 搬出）
========================================================================

为什么单独一个模块：`beat_render.py` 3388 行、类只占 16 行、
**模块级函数 3040 行 / 80 个** —— 一张以 `render_beat_to_rpy` 为根的调用网，
叶子常量只有 9 行，所以只能**按调用子树切**。实测三族的闭包：

    菜单族      初选 11 → 闭包 72 个 / 2585 行   （切不动）
    条件/标签族  初选  8 → 闭包 74 个 / 2674 行   （切不动）
    代码块族    初选  3 → 闭包 13 个 /  480 行   ← 只有这一刀是干净的

⚠ 搬家纪律：**逐字照搬**（连注释与空行）—— `git diff` 一眼能看出"只是搬家"。
"""

from __future__ import annotations

from storygraph.core.statement import is_tight_dollar, strip_marker
from storygraph.core.vocabulary import GENERATOR_MARKER, RENPY_BUILTIN_TRANSFORMS, RENPY_STATEMENT_VERBS
from storygraph.story.beat_model import Beat, CODE_BLOCK_BODY, CODE_BLOCK_OPEN, KIND_EXTEND, KIND_MENU, KIND_MENU_OPTION, _fill_audio_from_cells, _looks_like_renpy_statement, _parse_native_stage_tail, _split_hide_items, _split_lines, _split_show_items, compose_code, hide_item_part, hide_item_parts, is_literal_speaker, show_item_part, show_item_parts, unmask_code_block_markers
import ast

def _looks_like_dialogue(line: str) -> bool:
    """
    这行"只可能"是**台词**（`角色 属性… "内容"`），而不是裸 python 表达式吗？

    为什么要它（P0，2026-10-05，真实项目 `script.rpy:93` 实测）：
    作者手写了一句 `l oh "3"`（`l` 后面多打了一个 `oh`）。Ren'Py 把
    **裸的 `词 词 "串"`** 当台词读（角色 `l oh` 说 `"3"`）—— Ren'Py 要到
    **运行时**才抱怨角色未定义，那属于"脚本能跑、演出不对"的范畴。

    而工具把它归成 `raw`，导出时按"裸 python 表达式"补了 `$`：

        $ l oh "3"            ← Python 语法错误 → **Ren'Py 加载脚本就报错**

    也就是说工具的产物比作者原文**更糟**（原文至少能跑）。这是 P0，
    `audit_roundtrip.py` 在真实项目里一直报着它。

    **判据刻意保守**：只认 `标识符链 + 一个引号串 + 行尾`。

        'l oh "3"'        → True     台词
        'ling "台词"'     → True     台词
        "e '单引号'"       → True     台词
        'love += 1'       → False    `+=` 不在允许字符内
        'msg_add(1)'      → False    括号
        'x = "s"'         → False    `=`
        'foo("a")'        → False    括号
        'persistent.n = 1'→ False    `=`（且后面还有内容）

    唯一会被它放过的 python 形态是 `f() "x"`（函数调用后面跟一个串），
    而那**本身就是语法错误** —— 透传反而更接近原文，不亏。

    为什么不用"动词白名单"（第一版就是这么做的，已回退）：
    白名单只认 `show` / `jump` / `pause` 这类**已知动词**，而
    `l oh "3"` 的第一步是个**角色名**，白名单必然漏掉它，
    同时又会把白名单内的真 python 内容误判。**按形态认，不按名字认。**
    """
    s = str(line or '').strip()
    if not s:
        return False
    i = 0
    # 标识符链：`l`、`ling`、`e happy`、`renpy.x` 都行（空格或点分隔）
    seen = False
    while i < len(s):
        ch = s[i]
        if ch.isalnum() or ch == '_':
            seen = True
            i += 1
            continue
        if ch in ' .' and seen:
            # 后面必须还接标识符字符，否则这个空格/点就是分隔符之前的东西
            j = i + 1
            if j < len(s) and (s[j].isalnum() or s[j] == '_'):
                i = j
                continue
            break
        break
    if not seen or i >= len(s):
        return False
    # 角色名与台词之间的空格（`l oh "3"` 里 `oh` 与 `"3"` 之间那个）
    while i < len(s) and s[i] in ' \t':
        i += 1
    # 剩下来的必须正好是**一个引号串**，直到行尾
    rest = s[i:]
    if rest[:1] not in ('"', "'"):
        return False
    quote = rest[0]
    try:
        tree = ast.parse(rest, mode='eval')
    except SyntaxError:
        return False
    return (isinstance(tree.body, ast.Constant)
            and isinstance(tree.body.value, str)
            and rest.endswith(quote))

_RENPY_STATEMENT_HEADS = RENPY_STATEMENT_VERBS

def _looks_like_renpy_statement(line: str) -> bool:
    """
    这行像一条 **Ren'Py 语句**（而不是裸 python 表达式）→ 原样透传、不补 `$`。

    两条并集（见 `_RENPY_STATEMENT_HEADS` 与 `_looks_like_dialogue` 的说明）：
    已知动词，或者"只可能是台词"的形态。

    **不包含 `if` / `elif` / `else` / `while` / `for`** —— 那几条在
    `raw` 里出现时是**代码块的条件行**（`python:` 块内），当成语句透传
    会把块结构弄坏（第一版把 `while` 放进来，实测红了两个测试）。
    """
    s = str(line or '').strip()
    if not s:
        return False
    head = s.split(None, 1)[0].rstrip(':')
    if head in _RENPY_STATEMENT_HEADS:
        return True
    return _looks_like_dialogue(s)

def render_code_block(text: str, multiline: str = 'auto') -> list:
    """
    CodeBlock / python op（保真后门）→ 代码行，**不解析、不校验**。

    处理四类内容：
      · `$ x += 1`             → 原样
      · `with dissolve`        → 原样（原生语句）
      · `x += 1` / `renpy.x()` → 补 `$`（裸 python 表达式在 Ren'Py 里必须带 $）
      · **整块都是 python**（每一行都有缩进，或显式 `multiline='python'`）
        → 包回 `python:` 块，保持块结构（`for` / `def` 的缩进不能丢）

    **判定"是不是 python 块"必须看每一行**（2026-10-05 修，真实项目实测）：
    原先只看"有没有任何一行带缩进"，于是这种**混合**格子被整块包进
    `python:` —— 而里面明明是 Ren'Py 语句：

        window hide                       ← Ren'Py 语句，缩进是"续行对齐"留下的
        hide screen prompt
        show screen prompt("再玩一遍？", act=[
            Call("mini_game_dfinder"),    ← 多行语句的续行
        ])

    产物变成 `python:` 块里的 `window hide` / `hide screen prompt` ——
    语法错误、整段演出消失（`mini_game_dfinder.rpy` 实测 7 条全丢）。
    现在只有在"每行都缩进"（真块体）时才包块；混合内容退回逐行输出，
    续行（带缩进的那些）按裸表达式补 `$`，绝不会被塞进 `python:`。

    `multiline`：
      · `'auto'`（默认，卡片那条路）：按上面两条判据自动选；
      · `'python'`（控制流导出那条路）：多行一律包成 `python:` 块 ——
        与它一直以来的产物形状一致（多行 op 在原文件里本来就是 `python:` 块）。

    **这份实现是"python op → 源文本"的唯一规则源**（2026-10-05 审计"两套
    .rpy 生成器"时合并）：`pipeline/renpy_exporter` 以前自己手写
    `f"    $ {op}"`，而它不知道行首标记（紧凑 `$code`，见
    `core.statement.MARKER`）这个约定，于是把标记原样写进了 .rpy
    （实测产物里 `$ ^Apersistent…`，Ren'Py 直接不认）。
    两个生成器现在都调这里，约定只有一份。
    """
    lines = _split_lines(text)
    if not lines:
        return []
    # **块标记优先**（隐藏列 / `.rpy` 解析给的确定结构）：标记在行首，是
    # "这行属于一个 python 块"的**权威**说法，不必再去猜缩进与括号。
    marked = [ln for ln in lines
              if ln[:1] in (CODE_BLOCK_OPEN, CODE_BLOCK_BODY)]
    if marked:
        return _render_marked_code_block(lines, multiline)
    block_indent = _python_block_indent(lines, multiline)
    if block_indent is not None:
        # 真块体：块体每一行统一缩进 `base + 4`，**行内原有的缩进保留** ——
        # 那正是嵌套层级（`for` 里的 `if` 里的语句）。把行 `strip` 掉再贴
        # 固定缩进会把所有嵌套压到同一层（实测踩过）。
        return ['python:'] + [('    ' * (block_indent + 1) + ln)
                              if ln.strip() else '' for ln in lines]
    out = []
    for chunk, first in _group_code_statements(lines):
        s = first.strip()
        if not s:
            continue
        # 行首标记 = 原文写的是 `$code`（`$` 后无空格），原样还回去。
        # 不还的话每个 `$` 行都会变成一个 diff 块。
        tight = is_tight_dollar(s)
        if tight:
            s = strip_marker(s).strip()
        if tight:
            out.append('$' + s)
        elif s.startswith('$') or _looks_like_renpy_statement(s):
            out.append(strip_marker(chunk))
        elif len(chunk.splitlines()) > 1:
            # 多行**裸 python 语句**：`$` 只能在第一行，续行必须保持原样贴在
            # 后面 —— 逐行加 `$` 会把它拆成好几条（`$ Call(...)` 直接语法错误）。
            out.append('$ ' + strip_marker(chunk))
        else:
            # 兜底：补 `$` 当成裸 python 表达式（`love += 1` / `msg_add(1)`）。
            # 不能一律原样透传 —— 那是**生成语法正确的脚本**所依赖的路径
            # （实测透传后 13 个测试变红）。真正"认不出来的 Ren'Py 语句"
            # （`l oh "3"`）由 `_looks_like_renpy_statement` 提前拦住。
            out.append('$ ' + s)
    return out

def _indent_block_body(body: list) -> list:
    """
    `python:` 块体 → 已缩进的行：**整体平移，相对缩进原样保留**。

    为什么不再"按括号重新对齐"（2026-10-05 修，真实项目实测）：

    旧实现把续行**对齐到未闭合括号所在列**，于是

        positions = [
                    (200, 200), (600, 200), …      ← 原始：12 空格
            ]                                      ← 原始：8 空格

    被改写成（括号在第 21 列）

        positions = [
                             (200, 200), …         ← 变成 21 空格
                             ]                     ← 变成 21 空格

    **语法一样能跑**（括号内换行的缩进在 Python 里自由），但它是
    "凭空改写了作者手写的排版" —— 三处 Python 块的往返"丢失"全部来自
    这里（两个 `mini_game*.rpy`）。审计按逐行文本比对，把"同一段代码
    缩进不同"算成一丢一增。

    为什么"整体平移"就够：单元格里的相对缩进是**原文的**，原文是合法
    Python，所以

      · 块层级（`for` 体比 `for` 深）→ 相对缩进保住 → 语义保住；
      · 续行对齐 → 相对缩进保住 → 排版逐字节回到原文。

    也就是说这一条同时满足"语义正确"和"不动作者的排版"，
    而"重新对齐"只在语义上够用。**能不动就不动。**

    平移量取**第一条非空行的相对缩进**（`anchor`）：代码格写表时会把
    块头那一行的缩进剥掉，所以 `anchor` 通常就是 0。
    减去 `anchor` 再统一加块体基准缩进（`python:` 的块体必须比它深一层，
    所以每一行都要加）。

    ⚠ **已知的排版漂移（未修，见文末）**：续行自己那 12 个空格是原文的
    绝对排版，再统一加 4 之后产物里是 16/20，与原文对不上 —— 两个
    `mini_game*.rpy` 的往返"丢失"就是它。这是**纯排版**（括号内换行的
    缩进在 Python 里自由），语义完全等价。试过三种归一化写法都让
    全项目丢失数**变多**（3 → 7），所以保持现状并记录清楚。
    """
    anchor = None
    for ln in body:
        if ln.strip():
            anchor = len(ln) - len(ln.lstrip())
            break
    if anchor is None:
        return [''] * len(body)

    out = []
    for ln in body:
        if not ln.strip():
            out.append('')
            continue
        indent = len(ln) - len(ln.lstrip())
        # 比 anchor 还浅的行（理论上不该有）夹到 0，避免负缩进
        out.append(' ' * (4 + max(0, indent - anchor)) + ln.lstrip())
    return out

def _render_marked_code_block(lines: list, multiline: str) -> list:
    """
    按**块标记**渲染：标记说哪些行属于同一个 `python:` 块。

    这是"结构由数据说话"的那条路（块边界来自隐藏列 `CodeBlocks` /
    `.rpy` 解析），不依赖缩进推断 —— 一个格子里混着 `$ a` / `$ b` /
    `python: c` 时，只有它能准确还原。

    块**外**的行**按原文原样透传**，不补 `$` —— 走到这里说明
    `compose_code` 已经给需要 `$` 的行留了 `$`（或行首标记）；
    再不问青红皂白补一个 `$`，一个手写错句（实测 `script.rpy` 的
    `l oh "3"`，`l` 后面多打了个 `oh`）就会变成 `$ l oh "3"` ——
    **产物从"Ren'Py 报未知语句"变成"Python 语法错误"**，更难查。
    """
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        marker = line[:1]
        if marker == CODE_BLOCK_OPEN:
            j = i + 1
            while j < n:
                nxt = lines[j]
                if nxt[:1] == CODE_BLOCK_BODY:
                    j += 1
                    continue
                # **空行也是块体的一部分**（2026-10-05 修）。
                #
                # 实测来源：`mini_game_puzzle.rpy` 的多行调用里有一个空行
                # （`cols=3,` 与 `grid_width=800,` 之间）。块标记是**行首字符**，
                # 而空行没有行首字符 —— 旧循环到它这里就停了，于是：
                #   · 空行之后那几行**掉出块体**；
                #   · `python:` 块被从中间截断，尾巴按"块外行"逐行处理；
                #   · 那一整段在往返后逐行文本对不上（审计算一条丢失）。
                # 判据要"往前看"：空行**后面还有块体行**才算块内空行，
                # 否则它是块与下一段之间的分隔（那种要留在块外）。
                if not nxt.strip():
                    k = j
                    while k < n and not lines[k].strip():
                        k += 1
                    if k < n and lines[k][:1] == CODE_BLOCK_BODY:
                        j = k
                        continue
                break
            body = [unmask_code_block_markers(ln) for ln in lines[i:j]]
            out.append('python:')
            out.extend(_indent_block_body(body))
            i = j
            continue
        if marker == CODE_BLOCK_BODY:
            # 只有块体行而没有块头：数据不一致，按普通行处理（不吞）
            i += 1
            continue
        s = unmask_code_block_markers(line).strip()
        if not s:
            i += 1
            continue
        tight = is_tight_dollar(s)
        if tight:
            out.append('$' + strip_marker(s).strip())
        elif s.startswith('$') or _looks_like_renpy_statement(s):
            out.append(strip_marker(line))
        else:
            # 兜底：补 `$` 当成裸 python 表达式 —— 走到这里说明
            # `_split_code_cell` 把它判成了 code（两处判据同源）。
            # 不补的话 `$ a = 1` 会退化成裸 `a = 1`（实测往返丢 3 条）。
            #
            # 真正"认不出来的 Ren'Py 语句"（`l oh "3"`）由
            # `_looks_like_renpy_statement` 的**台词形态**提前拦住，
            # 不会掉进这里。
            out.append('$ ' + s)
        i += 1
    return out

def _python_block_indent(lines: list, multiline: str):
    """
    这些行整体是一个 `python:` **块体**吗？是就返回它的基础缩进层级（0/1/…），
    不是返回 `None`。

    判据（2026-10-05 修，两处都踩过）：

      · 首行**不缩进**（块头所在层级）、其余非空行**都缩进** —— 这是
        `python:` 块的标准形状。`compose_code` 把块体各行保留了原始缩进，
        所以这个形状能直接看出来。
      · **没有任何一行是原生 Ren'Py 语句** —— 兜底：`window hide` 这种
        就算被误缩进也不能进 `python:` 块（塞进去是语法错误，实测
        `mini_game_dfinder.rpy` 7 条演出整段消失）。
      · `multiline='python'`（控制流导出那条路）：多行一律当块。

    首行缩进的情况也要认（嵌套在别的块里的 `python:`）—— 用首行的缩进
    当基准，其余行更深才算块体。
    """
    body = [ln for ln in lines if ln.strip()]
    if len(body) < 2:
        return None
    if multiline == 'python':
        return 0
    # **有 Ren'Py 专有语句就不是 python 块**。用例必须挑**专有**的那些
    # （`window` / `show` / `hide` / `scene` / `play`…），不能用
    # `_looks_like_renpy_statement` —— 它的表里还包含 `for` / `if` /
    # `while` / `with` / `pass`，而这些**正是合法的 python 语句**，
    # 拿它一票否决会把真块体（`for i in range(3):` 开头的那种）全拦下来。
    if any(_is_native_renpy_line(ln.strip()) for ln in body):
        return None
    if not any(ln != ln.lstrip() for ln in body):
        return None
    # **有人真的进了下一层**（而不是只有多行语句的续行对齐）才算块体：
    # 用括号平衡区分 —— `positions = [` 后面的行虽然也缩进，但那时括号还
    # 没收平，属于同一条语句的续行，不是块层级。
    base = len(body[0]) - len(body[0].lstrip())
    depth = 0
    for ln in body:
        indent = len(ln) - len(ln.lstrip())
        if depth <= 0 and indent > base:
            return base // 4
        depth += _bracket_delta(ln, '')
    return None

_RENPY_ONLY_HEADS = frozenset({
    'show', 'hide', 'scene', 'play', 'queue', 'stop', 'voice', 'window',
    'screen', 'nvl', 'camera', 'image', 'transform', 'layeredimage',
    'define', 'default', 'init', 'label', 'translate', 'on', 'style',
    'jump', 'call', 'return', 'menu', 'extend', 'voice_sustain',
    'renpy.', 'persistent.', 'config.', 'gui.', 'store.',
})

def _is_native_renpy_line(line: str) -> bool:
    """
    这一行是**只有 Ren'Py 才有**的语句吗（python 里不合法）？

    `renpy.` / `persistent.` / `config.` 这类点号前缀要认，但**不能**认
    `renpy.` 开头的**任何** python 调用 —— `renpy.hide_screen(…)` 也是
    合法 python（`renpy` 是引擎暴露的模块），真块体里很常见。
    这里只把"显然不是 python 语句"的形状判为原生语句：
    裸动词（`window hide` / `show screen x` / `hide screen y`）与
    `window`/`scene`/`play`… 这类 python 里根本不存在的关键字。

    判据用**第一个词**，并且要求它不在"python 里也合法"的那组里。
    """
    parts = line.split(None, 1)
    if not parts:
        return False
    head = parts[0].rstrip(':')
    if head not in _RENPY_ONLY_HEADS:
        return False
    # `renpy.hide_screen(…)`：带括号调用一律当 python（引擎模块的函数也是
    # python 表达式）；不带括号的 `renpy.restart_interaction` 也是表达式，
    # 所以点号前缀的**一律不算**原生语句。
    if head.startswith(('renpy.', 'persistent.', 'config.', 'gui.',
                        'store.')):
        return False
    return True

def _group_code_statements(lines: list) -> list:
    """
    代码行 → **逻辑语句**分组 `[(文本, 首行), ...]`。

    为什么需要（2026-10-05 真实项目实测）：Ren'Py 允许一条语句跨多行（括号
    续行）——

        show screen prompt("还要再玩一遍吗？", act=[
            Call("mini_game_dfinder"),
        ])

    解析器把这三行当成**一条**逻辑语句，于是「代码」列里是一格三行。
    逐行渲染会把续行当成独立语句补 `$`，产物变成
    `$ Call("mini_game_dfinder"),` / `$ ])` —— **语法错误**。

    判据是**括号/引号平衡**：一条语句在自己的括号收平之前，后面所有行都属于它。
    （不猜缩进：续行缩进是作者排版，而括号平衡是硬语法。）
    """
    groups = []
    buf = []
    depth = 0
    quote = ''
    for ln in lines:
        if not ln.strip() and not buf:
            continue
        buf.append(ln)
        depth += _bracket_delta(ln, quote)
        # 引号状态也要跟着走（三引号字符串里可能出现括号/跳行）
        quote = _quote_state(ln, quote)
        if depth <= 0 and not quote:
            groups.append(('\n'.join(buf), buf[0]))
            buf, depth, quote = [], 0, ''
    if buf:
        groups.append(('\n'.join(buf), buf[0]))
    return groups

def _bracket_delta(line: str, quote: str) -> int:
    """这一行让括号深度变化多少（引号内的括号不算；`#` 之后不算）。"""
    depth = 0
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == '\\':
                i += 2
                continue
            if line.startswith(quote, i):
                i += len(quote)
                quote = ''
                continue
            i += 1
            continue
        if ch == '#':
            break
        if ch in '"\'':
            if line.startswith(ch * 3, i):
                quote = ch * 3
                i += 3
                continue
            quote = ch
            i += 1
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        i += 1
    return depth

def _quote_state(line: str, quote: str) -> str:
    """扫完这一行之后，三引号字符串是否还开着（开着的引号标记，否则空串）。"""
    i = 0
    n = len(line)
    while i < n:
        if quote:
            if line.startswith(quote, i):
                quote = ''
                i += 3
                continue
            i += 1
            continue
        ch = line[i]
        if ch == '#':
            break
        if ch in '"\'':
            if line.startswith(ch * 3, i):
                quote = ch * 3
                i += 3
                continue
            # 单行字符串：跳到它的收尾
            i += 1
            while i < n:
                if line[i] == '\\':
                    i += 2
                    continue
                if line[i] == ch:
                    break
                i += 1
        i += 1
    return quote
