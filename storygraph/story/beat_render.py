"""
storygraph.story.beat_render
============================
**Beat → Ren'Py 代码**（导出方向）。

  · `render_beat_to_rpy`：一个 Beat → 若干语句行；`trace` 非空时逐行记录
    （来源 / 原始意图 / 代码 / 状态），「编译结果表」与生成的 .rpy 因此
    **同源**——它们是一次渲染的两个视图，不可能对不上。
  · `render_beats_to_rpy`：整表 → 完整 .rpy（含 label 分段与自定义 transform）
  · `render_transform_definitions`：为用到的自定义变换生成 ATL 定义

原则：找不到资源只写 `# TODO: 缺少资源 …` 且**不生成**那条非法语句；
图像名保留作者写法（`show eileen happy` 不改写成下划线形态）。
"""

from __future__ import annotations
import ast
import re
from typing import Optional
from storygraph.core.vocabulary import (GENERATOR_MARKER,
                                        RENPY_BUILTIN_TRANSFORMS,
                                        RENPY_STATEMENT_VERBS)
from storygraph.core.statement import is_tight_dollar, strip_marker
from storygraph.core.nest_path import (body_chain, join_nest,
                                       option_index, option_key, split_nest)

from storygraph.story.beat_model import (
    Beat,
    CODE_BLOCK_BODY,
    CODE_BLOCK_OPEN,
    KIND_EXTEND,
    KIND_MENU,
    KIND_MENU_OPTION,
    _fill_audio_from_cells,
    _looks_like_renpy_statement,
    _parse_native_stage_tail,
    _split_hide_items,
    _split_lines,
    _split_show_items,
    compose_code,
    hide_item_part,
    hide_item_parts,
    is_literal_speaker,
    show_item_part,
    show_item_parts,
    unmask_code_block_markers,
)

from storygraph.core.safeguard import swallow



# ===================== Beat -> rpy =====================

def _needs_quote(name: str) -> bool:
    return bool(name) and not re.match(r'^[A-Za-z_][\w.]*$', name)



def _audio_ref(name: str) -> str:
    """
    音频/语音引用：简单标识符原样输出，含路径分隔符或非 ASCII 的
    必须加引号——否则 `play music bgm/夜晚.mp3` 是非法 Ren'Py。
    """
    name = (name or '').strip()
    return _quote(name) if _needs_quote(name) else name



def _emit_image_name(name: str, resolver=None) -> str:
    """
    决定生成脚本时图像名用哪种写法。

    ⚠ **2026-10-05 改过一次（用户实测报到）**：这里原来写着

        if resolver is None:
            return underscored        # ← 没给资源表就"空格换下划线"

    后果是"**没资源表时反而改坏**"：真实项目 `definitions.rpy:111` 定义的是
    `image bg bg1_1`（Ren'Py 原生 **tag + attribute**），全工程 `grep bg_bg1_1`
    **0 命中** —— 于是 `scene bg bg1_1` 过一趟表就成 `scene bg_bg1_1`，
    运行时找不到这张图（真实项目 7 个文件 17 处）。
    更糟的是它会跟 `with` 抢词：`scene bg bg1_1 with dissolve` 再解析一轮
    只剩 `scene bg_bg1_1`（`with` 从句被名字吞掉）。

    **现在的规矩**（"没证据就别改"）：

      · **没给 resolver**（CLI 没 `--asset-dir`、单测）→ **原样返回**。
        作者写的就是工程里在用的形态，而我们**没有任何证据**说明下划线形态
        真的存在 —— 这时改写是**凭空的破坏**，不是"降级"；
      · 给了 resolver → 优先作者形态（资源表里解析得出就用它）；
        解析不了才试下划线形态；都不行仍返回作者形态（缺资源交给审计报）。

    为什么不再"至少提醒一声"：改成原样保留之后，这条路上**没有发生降级**，
    不需要提醒。真该提醒的是"资源表里找不到这个名字" —— 那件事由
    `audit.image()` 负责（卡片编辑器显示 TODO），与这里不重复。
    """
    name = (name or '').strip()
    if not name or '"' in name or "'" in name:
        return name
    underscored = name.replace(' ', '_')
    if resolver is None:
        # 原样保留：没有资源表就没有"该改成什么"的证据（见 docstring）
        return name
    try:
        if name == underscored or resolver(name):
            return name
        if resolver(underscored):
            return underscored
    except Exception as e:
        swallow('story.beat_render._emit_image_name', e)
        return underscored
    return name



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


#: **已知的 Ren'Py 语句动词**：以它们开头的行是原文真迹，不许补 `$`。
#:
#: 为什么必须有这一份（2026-10-05 实测）：`show screen my_hud` / 跨文件
#: `jump external_minigame` 这类语句由解析器归成 `raw`（设计内的"原样保存、
#: 原样回写"），如果被当成裸 python 补上 `$`，产物就成了
#: `$ show screen my_hud` —— 语法错误。
#:
#: 与 `_looks_like_dialogue` 是**并集**关系，两者不可互相替代：
#:   · 动词白名单认 `show` / `jump` / `pause` …，但**认不出**台词
#:     （`l oh "3"` 的第一步是**角色名**，不可能在白名单里）；
#:   · 台词形态认 `标识符链 + 引号串`，但认不出 `show screen my_hud`
#:     （它没有引号串）。
#:
#: 只在**动词那一档**放宽，不做"整体像 Ren'Py 就透传"的猜测 ——
#: 猜测那版（第一版）实测红了 13 个测试。
#: ⚠ **它是 `core.vocabulary.RENPY_STATEMENT_VERBS` 的别名**（M5 收敛）：
#: 从前这一份在三个模块里各写一遍，口径不同还得记得同步。现在只有一份，
#: 差异（"**刻意不含** `if/elif/else/while/for`"）写在 `core.vocabulary`
#: 的说明里 —— 别在这里加 `while`，那会把 `python:` 块的条件行当语句透传。
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



#: **Ren'Py 专有**的语句头（`for` / `if` / `while` / `with` / `pass` 这些
#: python 里也合法的词**故意不在里面** —— 详见 `_python_block_indent`）。
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


#: 旧名（模块内一直在用）。新代码请用 `render_code_block`。
_render_code_block = render_code_block



def _attach_inline_note(lines: list, beat) -> list:
    """
    把行尾注释贴到这一拍渲染出来的行上。

    **贴哪一行**有讲究：

      · 一拍一行 → 就是它；
      · 一拍多行 → **只有当前面几行都是"块头"（以 `:` 收尾）时才贴最后一行**
        （语句本体在那里）。典型形态是条件拍：

            if (fsflag) and not (_return == correct_answer):
                "你是笨蛋嘛喵？…"      ← 源码里注释写的是**这一行**

        别的多行形态（舞台指令 + 台词、多行代码）**不贴** —— 说不清注释原本
        挂在哪一行，**贴错比不贴更坏**。
    """
    note = _inline_note(beat)
    if not note or not lines:
        return lines
    target = 0
    if len(lines) > 1:
        heads = [ln for ln in lines[:-1] if ln.strip()]
        if not heads or not all(ln.rstrip().endswith(':') for ln in heads):
            return lines
        target = len(lines) - 1
    if '#' in lines[target]:
        return lines                      # 这一行已经有注释了，别叠
    return [ln + note if i == target else ln for i, ln in enumerate(lines)]


def render_beat_to_rpy(beat: Beat, indent: str = '    ',
                       name_resolver=None, audit=None,
                       trace: list = None) -> list:
    """
    一个 Beat 渲染成 Ren'Py 语句。返回不带换行符的字符串列表。

    两条输入形态、两条通道：

      · `stage` / `bgm` / `sfx` 有 DSL 文本 → 走**编译器**：
        方括号形式（`[显示] 凌跃捷 开心 左`）被翻译成 `show ling …`；
        原生形式（`show ling happy`）直通不解析；
        找不到资源只写 `# TODO: 缺少资源 …`，既不报错也不生成非法语句。
      · 没有 DSL 文本（Beat 由代码直接构造，如单测）→ 读结构化字段，
        保持历史行为。

    trace 非空时逐行记录 (来源, 原始意图, 代码, 状态)，供「编译结果表」
    （Export_RenPy）使用——Sheet 2 与生成的脚本因此**必然一致**，
    而不是"另写一套逻辑再对一遍"。
    """
    pre = []
    for c in _split_lines(beat.comment):
        pre.append(c if c.startswith('#') else '# ' + c)

    lines = []
    symbols = getattr(audit, 'symbols', None)

    def emit(line, source='', intent='', status='ok'):
        lines.append(line)
        if trace is not None:
            trace.append({'source': source, 'intent': intent,
                          'code': line, 'status': status})

    def todo(source, intent, message):
        emit(f"# TODO: {message}", source, intent, 'todo')

    # ---------------- 舞台 ----------------
    # 结构化列（背景/立绘/位置/转场/退场）先按 StageOrder 出，
    # 再把「舞台（高级）」列里那些结构化列表达不了的指令**追加**在后面。
    # 两个来源可能表达同一条指令（hydrate 会把 Stage 原文并回结构化字段），
    # 所以记下来做精确去重，避免同一句 show 被生成两次。
    emitted_stage = set()

    def emit_stage_verbs(verbs):
        """输出**指定的几个 stage verb**（供拍内交错用）。"""
        _render_stage_from_fields(beat, emit,
                                  _resolver_for(audit, name_resolver),
                                  audit, emitted_stage, verbs=verbs)

    def emit_stage_tail():
        """舞台的收尾部分：Stage 高级列 + 旧 Transform 列。

        无论单条 verb 怎么切分，这部分都在**所有舞台指令之后**输出一次。
        """
        if beat.stage:
            from storygraph.story.dsl import compile_stage, parse_stage
            # 结构化字段已经承载了哪些指令（见 `_stage_op_covered`）。
            # 每次渲染现算：`beat.show` / `beat.hide` 可能在这一拍里刚被改过。
            show_parts = show_item_parts(beat.show)
            hide_parts = hide_item_parts(beat.hide)
            ops = parse_stage(beat.stage).ops
            for op, (intent, code, status) in zip(
                    ops, compile_stage(beat.stage, symbols, audit)):
                if op.native:
                    # **作者手写的原生指令：逐字原样出去**（不做下划线改写，
                    # 也是"不把能跑的脚本改坏"的底线）。结构化列里那份是
                    # hydrate 留下的影子，`_stage_lines` 已经跳过它了。
                    # 只有"结构化通道自己又出了完全同一条"时才去重。
                    if code in emitted_stage:
                        continue
                    emit(code, '舞台', intent, status)
                    continue
                if code in emitted_stage:
                    # 结构化列已原样出过同一条指令（Stage 原文被 hydrate 并回）
                    continue
                if _stage_op_covered(code, op, beat, show_parts, hide_parts):
                    # 同一条指令换了写法重复出现在 Stage 里 → 不重复输出
                    continue
                emit(code, '舞台', intent, status)

        # 旧表带过来的 Transform 列（现已取消）：当成这一拍的转场
        if beat.transform and not beat.with_:
            emit(f"with {beat.transform}", '舞台', beat.transform)

    def emit_stage():
        """一次输出全部舞台指令（历史行为，给没有 op_order 的 Beat 用）。"""
        emit_stage_verbs(None)
        emit_stage_tail()

    # ---------------- 代码块（保真后门） ----------------
    def emit_code():
        for c in _render_code_block(compose_code(beat)):
            emit(c, '代码块', c)

    # ---------------- 工具不认识的块语句（M1） ----------------
    def emit_opaque():
        """
        `Beat.opaque` → 逐字写回，**一个字都不动**。

        为什么不做任何推断（2026-10-07，M1）：

          · 「代码」列那条路认不出语句就补 `$`（`render_code_block` 的
            兜底），还会把整块缩进包成 `python:` —— 对一段工具**看不懂**
            的自定义块，这两种推断都是在改剧情：
            `target = ling` 变成 `$ target = ling`、「块体」变成「python 块」；
          · 这里的契约只有一句：**原文什么样，产物什么样**。
            块体的相对缩进是原文的真迹，外层缩进由
            `render_beats_to_rpy` 统一加（`indent + ln`），所以这里只管吐。

        空行**保留**（`_split_lines` 会吃掉它们，所以这里自己切）——
        块体里的空行是作者排版的一部分。
        """
        text = str(getattr(beat, 'opaque', '') or '')
        if not text.strip():
            return
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        lines = text.split('\n')
        # **头行可编辑**（M4，T1a/T1b 的参数编辑面）：
        # `spec_head` 是头行的**原样拷贝**，所以"没改"时它与首行相同 ——
        # 只有**真的不同**才替换。于是
        #   没改 → 走原文（逐字节不变，与"精化/降级不改字节"一致）；
        #   改了 → 生效（策划在这里改参数）。
        # ⚠ 比对用 `strip()`：`spec_head` 是单行列，经过 `_one_line` 去过
        # 首尾空白；直接比全文会把"首行末尾多几个空格"误判成"人改过了"，
        # 于是第一次导出就悄悄改了别人的排版。
        # 三个可能的编辑面（同一时刻只会有一个非空）：
        # `spec_head`（项目声明的自定义语句）→ `loop_header`（`while`/`for`，D2）
        # → `camera_header`（3D 舞台 `camera`，2026-10-07）。
        head = (str(getattr(beat, 'spec_head', '') or '')
                or str(getattr(beat, 'loop_header', '') or '')
                or str(getattr(beat, 'camera_header', '') or ''))
        if head and lines and head.strip() != lines[0].strip():
            lines = [head] + lines[1:]
        for ln in lines:
            emit(ln.rstrip(), '原样块', ln.strip())

    # ---------------- 音频 ----------------
    # 音乐/音效列存的是**资产 ID / DSL 文本**（可下拉），参数单独一列；
    # 内部仍走同一套字段，停止/排队用「（停止音乐）」这类伪值表达。
    # 结构化字段（beat.music / beat.sound）为空时，从文本列（beat.bgm /
    # beat.sfx）解析一次——否则直接构造、没走过 from_row 的 Beat（如单测、
    # 内存里拼的）会丢掉 stop/play，生成不出音频语句。
    if not beat.music:
        _fill_audio_from_cells(beat, 'music', beat.bgm, beat.music_params)
    if not beat.sound:
        _fill_audio_from_cells(beat, 'sound', beat.sfx, beat.sound_params)

    def emit_audio():
        _render_audio_from_fields(beat, emit, audit)

    def emit_voice():
        if not beat.voice:
            return
        if audit is not None:
            from storygraph.story.dsl import TODO as _TODO
            if audit.audio('voice', beat.voice) == _TODO:
                todo('语音', beat.voice,
                     f"缺少资源 语音 '{beat.voice}'")
                return
            emit(f"voice {_quote(audit.audio_ref('voice', beat.voice))}",
                 '语音', beat.voice)
        else:
            emit(f"voice {_audio_ref(beat.voice)}", '语音', beat.voice)

    # ---------------- 按**源码里的顺序**输出 ----------------
    # 这一节修的是「跨类别顺序」+「同类内交错」：
    #   · `play music X` 写在 `show Y` 之前时，音频要真的在前（跨类别）；
    #   · `scene → play → show` 这种**同类内交错**也要保住——
    #     只按类别分组输出会得到一个"整块的舞台 + 整块的音频"，
    #     语义近似但顺序错了，回放/时间轴看到的顺序不对。
    #
    # `op_order` 记的是 **verb 级**顺序（`scene,audio,show`），
    # 所以这里逐条输出：stage verb 交给 `emit_stage_verbs` 单独渲染，
    # 这才能把音频插进 `scene` 与 `show` 之间。
    #
    # 代码块（CodeBlock 后门）不在 op_order 里，按历史契约紧跟舞台之后
    # 输出——它内部有更细的 `beat.ordered`（raw/code 交错）负责保真。
    # "紧跟舞台之后"包含**舞台尾段**（见下面 `emit_stage_tail()` 的位置）。
    # **逐条**输出（缺口 4，2026-10-07 修）：token 形如 `(verb, idx)`，
    # `idx=None` = verb 级（老表 / 补齐）→ 整组输出；有下标 → **只出那一条**。
    #
    # 从前这里只按 **verb 名**分组（`('show', ...)` 就把该拍所有 show 一次出完），
    # 于是"两条同名 verb 中间夹着别的"根本表达不出来：
    #
    #     show A / hide B / show C   →   show A / show C / hide B
    #
    # 顺序被改；`show A / hide A / show A` 更狠 —— 去重 + 分组之后最后那条
    # show 没了，**收尾画面与作者要的相反**。
    tokens = beat.op_tokens_full()
    plan = beat.unified_plan() if tokens else None
    #: 舞台行只算一次（`_stage_lines` 里有一堆字符串处理，别每个 token 重算）
    _stage_cache: list = []

    def _stage_lines_once():
        if not _stage_cache:
            _stage_cache.append(
                _stage_lines(beat, _resolver_for(audit, name_resolver)))
        return {k: list(v) for k, v in _stage_cache[0].items()}

    #: 已经出过的舞台行（**按 (verb, 下标) 记账**，见 `_emit_stage_token`）
    _emitted_idx: set = set()
    if plan is not None:
        # **一条真序列**（缺口 4 第八轮）：舞台动词 / raw 行 / code 行按源码顺序
        # 逐条执行。代码通道**按段输出**——`python:` 块的块体必须整块出，
        # 而块与块之间、以及块与非代码行之间可以插入舞台/raw 行。
        #
        # 为什么要分段：代码通道从前只有**一个槽位**（"紧跟第一条 scene"
        # 或"舞台尾段之后"），于是 `hide screen map` 写在 `scene` 前面也会
        # 跑到后面（真机 `pomodorro` / `chapter1_1` / `chapter1_4` /
        # `script_pomo` 四个文件实测）。
        code_run: list = []

        def flush_code_run():
            if not code_run:
                return
            for c in _render_code_block('\n'.join(code_run)):
                emit(c, '代码块', c)
            code_run.clear()

        for kind, payload in plan:
            if kind in ('scene', 'show', 'hide', 'with'):
                flush_code_run()
                _emit_stage_token(_stage_lines_once(), kind, payload, emit,
                                  emitted_stage, _emitted_idx)
            elif kind == 'raw':
                flush_code_run()
                # raw 行**逐字照出**（它是作者写的原生语句，不加 `$`、不缩进改写）
                emit(payload, '代码块', payload)
            elif kind == 'code':
                code_run.append(payload)      # 攒成一段，遇到别的再落地
            elif kind == 'blank':
                # 块内空行（带块标记）**要跟着这一段一起出**；块外的空行照旧
                # 不单独产出（`render_code_block` 本来就是按 `strip()` 判空的）。
                if code_run and payload:
                    code_run.append(payload)
            elif kind == 'audio':
                flush_code_run()
                emit_audio()
            elif kind == 'voice':
                flush_code_run()
                emit_voice()
        flush_code_run()
        _emit_leftover_stage(_stage_lines_once(), emit, emitted_stage,
                            _emitted_idx)
        # **舞台尾段 + 原样块照样要出**（第九轮修）：
        # 序列里只有"舞台动词 / raw / code"这几类，而「舞台（高级）」列的原生
        # 真迹与**未建模块**（`opaque`，例如 `camera …:` 的块体）不在序列里 ——
        # 少这两个调用，`camera` 整条语句会**静默消失**
        # （`test_session_apply_folds_parameter_keys` 当场红）。
        emit_stage_tail()
        emit_opaque()
    elif tokens:
        # **补齐 audio / voice**：老表/手工新增行可能只记了部分，
        # **绝不因为顺序信息不全就丢掉操作**。
        #
        # ⚠ 2026-10-05：stage verb 的那条补齐规矩收进 `Beat.op_tokens_full()`
        # （**唯一真源**，`stage_verbs` 同一个判据）—— 舞台快照/演出预览
        # 从前没照做，于是"卡片加了立绘、当前舞台却写着台上没人"（用户实测报的）。
        recorded = {v for v, _i in tokens}
        for k in ('audio', 'voice'):
            if k not in recorded:
                tokens.append((k, None))
        code_done = False
        for verb, idx in tokens:
            mark = len(lines)
            if verb in ('scene', 'show', 'hide', 'with'):
                _emit_stage_token(_stage_lines_once(), verb, idx, emit,
                                  emitted_stage, _emitted_idx)
            elif verb == 'audio':
                emit_audio()
            elif verb == 'voice':
                emit_voice()
            # **"真出了一行的 scene"才算数**（2026-10-05 修）。
            #
            # `tokens` 上面被**补齐**过：`missed_stage` 把 `scene/show/hide`
            # 里"op_order 没记的"一律追加到末尾，于是**没有背景的拍**
            # 也会走过 `t == 'scene'` 这一支 —— 原来的判据只看 token 名字，
            # 于是代码块被提前到那个**根本没出任何行**的 scene 位置，
            # 落在舞台尾段**之前**（`chapter1_2.rpy` 拍 136/152 实测：
            # `hide ye` / `show cg … behind ye` / `$ persistent…`，
            # 过表后 `$` 跑到了 `show` 前面）。
            #
            # 判据改成"这一支真的往 `lines` 里加过行"：有背景时行为与从前
            # **逐字节一致**（真 scene 一定出了行），没背景时不再误触发。
            if verb == 'scene' and not code_done and len(lines) > mark:
                # 代码块跟在**第一条舞台指令**之后（历史位置）
                emit_code()
                code_done = True
        # **兜底**：有内容却一条 token 都没点到的舞台行（"卡片加了立绘、
        # 顺序列没跟着长"那种）**也要出**，位置放在所有 token 之后 ——
        # 与从前的"补齐在末尾"同一个口径（`op_tokens_full` 已经补过 verb 级
        # 的，这里收的是"同一个 verb 里多出来的那几条"）。
        _emit_leftover_stage(_stage_lines_once(), emit, emitted_stage,
                            _emitted_idx)
        # **舞台尾段必须排在代码块之前**（2026-10-05 修，真实项目
        # `chapter1_2.rpy` 拍 136/152 实测）。
        #
        # 为什么：尾段（「舞台（高级）」列里的原生真迹）**属于舞台**，
        # 而"没有顺序信息"那条路本来就是 `emit_stage()` → `emit_code()`
        # （舞台在前）。这里原来把尾段放在 `emit_code()` **之后**，
        # 于是**同一条指令**在两条路上的位置不一样：
        #
        #   · 解析来的拍：`_stage_native_show` 是空的（`beat.stage` 是
        #     `compose_stage_extra` 从结构化列**合成**的），所以那行由
        #     结构化通道在 verb 位置出，尾段被 `emitted_stage` 去重 —— 代码在后；
        #   · 过表来的拍：`from_row` → `hydrate_from_dsl` 把它标成"原生影子"，
        #     结构化通道**跳过**它，于是只能由尾段出 —— 代码在前。
        #
        # 产物因此不一样，而且**源码顺序被改**（那两拍原文是
        # `hide ye` / `show cg … behind ye` / `$ persistent…`，过表后
        # `$` 跑到了 `show` 前面）。修法就是让尾段回到舞台这一侧：
        # 两条路都得到"动词 → 尾段 → 代码块"。
        #
        # 注意：`scene` 那条历史通道（代码紧跟第一条 scene）**不动** ——
        # 那两种排法在两条路上是一致的（被跳过的尾段行落在代码之后，
        # 与"结构化通道在 code 之后出它"同址）。
        emit_stage_tail()
        if not code_done:
            emit_code()
        # **原样块紧跟代码之后**（M1）：`beat_build` 让未建模块独占一张拍
        # 并在台词之前落卡，所以"代码 → 原样块 → 台词"就是源码顺序。
        emit_opaque()
    else:
        # 没有任何顺序信息（老表 / 直接构造的 Beat）：维持历史固定顺序
        emit_stage()
        emit_code()
        emit_opaque()
        emit_audio()
        emit_voice()

    # ---------------- 台词 ----------------
    speaker = beat.speaker
    if speaker and is_literal_speaker(speaker):
        # **字符串字面量说话人**（`"系统" "提示"`）：逐字原样出去。
        # 它不是角色变量，**不能**去角色表里查 —— 查不到就会写成
        # `# TODO: 缺少资源 角色 '"系统"'`，把真正的台词换成一句 TODO
        # （2026-10-05 实测：这一条比"丢后半句"更狠，因为它连前半句都换掉）。
        pass
    elif speaker and audit is not None:
        from storygraph.story.dsl import TODO as _TODO
        if audit.character(speaker) == _TODO:
            todo('说话人', speaker, f"缺少资源 角色 '{speaker}'")
            speaker = ''
    if beat.kind == KIND_MENU_OPTION:
        # 走 `_quote` 而不是手包一对引号：选项文字里可能有 `"` 或控制字符，
        # 裸包会写出语法错的菜单（与上面 `adv_backslash` 同一个坑）。
        #
        # **冒号也要带上**（2026-10-05 修 O6 追到的根）：整份渲染
        # （`render_menu_block`）写的是 `"选项":`，单拍这条路从前只写
        # `"选项"` —— 增量对位是**逐行配对**，两个形状配不上，于是
        #   · 选项行带行尾注释时：生成行被当"新增内容"插进去、旧行又留着
        #     → **重复行**（`"一"  # 备注` + `"一":  # 备注`）；
        #   · 小幅改选项文案时：既不落也不报 → **静默 no-op**。
        # 冒号本来就是 `menu:` 块的结构语法（`render_menu_block` 也是这么写的），
        # 单拍渲染没理由省掉它。
        emit(_quote(beat.text) + ':', '菜单', beat.text)
    elif beat.kind == KIND_EXTEND:
        emit(f"extend {_quote(beat.text)}", '台词', beat.text)
    elif beat.kind == KIND_MENU:
        # 菜单头。**必须与整份渲染（`render_menu_block`）给出同一个头** ——
        # 从前这一支接在 `elif beat.text:` **后面**，于是"有标题/有占位符"
        # 的菜单根本走不到这里（占位符 `MENU_NO_CAPTION` 是个真值字符串）：
        # 单拍这条路写出字面量 `"\x00menu"`，而整份产物写的是 `menu:`。
        # 两条路对不上 → 增量补丁的 `_verify_patch` 永远找不到那一行 →
        # 真实项目 6 个文件退回全量（O1）。
        from storygraph.story.beat_model import menu_caption_text
        cap = menu_caption_text(beat.text)
        if cap:
            emit(f"menu {_quote(cap)}:", '菜单', cap)
        elif beat.text:
            emit('menu:', '菜单', '')
    elif beat.text:
        if speaker:
            emit(f"{speaker} {_quote(beat.text)}", '台词', beat.text)
        else:
            emit(_quote(beat.text), '台词', beat.text)

    if not lines:
        # **落单的结构化循环头**（有头行、无整块原文、没被归组）：
        # 结构位丢了（隐藏列 `Nest` 被清空 / 老工具写的表），这一块没法还原成
        # 循环，但**内容不能因此消失** —— 块体那些拍还在流里，会照常渲染在
        # 下面。这里给一句**显式 TODO**（`UNRESOLVED_TODO` 门禁会抓到），
        # 让人看得见"结构降级了"。
        #
        # ⚠ 为什么不是 `while …:` + `pass`：那是**死循环**（`pass` 永远不改变
        # 条件）—— 比"结构没还原"严重得多。宁可少一个 `while` 行，
        # 也不要写出一份跑起来卡死的产物。
        if not pre and _is_orphan_loop_head(beat):
            emit(f'# TODO: 循环（'
                 f'{str(getattr(beat, "loop_header", "") or "").strip()}）'
                 f'的结构位缺失（隐藏列 Nest 被清空？）：这一块没能还原成 '
                 f'while/for —— 内容还在下面，但结构变了',
                 '循环', str(getattr(beat, 'loop_header', '') or ''), 'todo')
            return _attach_inline_note(pre + lines, beat)
        return pre

    if beat.condition:
        # 条件拍：`if <条件>:` 一行 + 块体。
        #
        # **块体必须比 `if` 深一层**（2026-10-05 实测，brutal test 的 F4）。
        # 以前写的是 `indent + ln`：`indent` 在"顶层直接渲染一拍"时是默认的
        # 4 空格，看着像是缩进了；但**嵌套渲染**（菜单选项体、`_render_unit_lines`）
        # 传的是 `indent=''`，于是 `if deepest:` 与它的块体贴成同一层 ——
        #
        #     if deepest:
        #     e "深层"          ← 与 `if` 齐平 = 语法错误
        #
        # 正确做法是**无条件再加一层**：调用方负责"这一拍整体在哪一层"，
        # 这里负责"拍内部块体比条件头深一层"。两条职责分开之后，
        # 顶层与嵌套两条路都能出对。
        # 条件行自己的注释贴到 `if …:` 那一行（收尾第 2 批）
        cond_head = f"if {beat.condition}:{_cond_note(beat)}"
        return _attach_inline_note(
            pre + [cond_head] + [
                '    ' + ln if ln.strip() else '' for ln in lines
            ], beat)
    if not lines and not pre and _is_orphan_loop_head(beat):
        # 兜底（正常情况下上面那个 `if not lines` 已经处理）：条件拍那条路
        # 会先返回，所以这里只是"两处都守一下"——判据见
        # `_is_orphan_loop_head`，别在两处写不同的判据。
        emit(f'# TODO: 循环（'
             f'{str(getattr(beat, "loop_header", "") or "").strip()}）'
             f'的结构位缺失：这一块没能还原成 while/for',
             '循环', str(getattr(beat, 'loop_header', '') or ''), 'todo')
        return _attach_inline_note(pre + lines, beat)
    # **行尾注释**（C）：贴在这一拍的输出上（只在一拍一行、源语句也一行时贴，
    # 见 `_inline_note`）。放在**这里**是因为它是"单拍渲染"的唯一出口 ——
    # 顶层（`render_beats_to_rpy`）、菜单头/选项行之外的所有拍都走它。
    return _attach_inline_note(pre + lines, beat)


def _is_orphan_loop_head(beat) -> bool:
    """这一拍是"落单的结构化循环头"吗（有头行、无整块原文、没被归组）？"""
    return bool(str(getattr(beat, 'loop_header', '') or '')) \
        and not str(getattr(beat, 'opaque', '') or '').strip()



# ===================== 菜单块：扁平拍 → 嵌套块 =====================
#
# 2026-10-05 修 P0-1。背景（评估报告第 3.2 节，真实数据实测）：
#
#   `menu` 在 Ren'Py 里是**嵌套块**，而 Beat 是**扁平**的拍。单拍渲染器
#   （`render_beat_to_rpy`）拿不到"菜单"这个结构，于是：
#
#       menu:                    label a:
#           "选项A":                 "选项A"        ← 变成裸旁白
#               jump b               jump b         ← 无条件执行
#           "选项B":                 "选项B"
#               jump c               jump c         ← 永远不可达
#
#   `menu:` 整块消失，第一个分支无条件跳转，第二个分支是死代码。
#   **游戏能启动、剧情全错、不报任何错。**
#
# 修复不去动 IR 的形状（`Statement` 加 children 会波及每一个消费者），
# 而是在渲染前**把扁平的拍重新归组**：`kind='menu'` 拍 + 紧随其后的
# `menu_option` 拍及其分支体，合起来正好是原来那棵块结构。分组所需的
# 信息本来就完整地活在这一串拍里（顺序 + kind + condition），
# 只是以前没有任何消费者去读它。

#: 菜单选项体分不出块内块外时的**兜底**条件（老表遗留的写法，见
#: `_expected_body_condition`）。B 阶段三之后**不再由我们写入**：新表靠
#: 隐藏列 `Nest`（块路径）说结构，只有老表里才可能见到 `True`。
#: Ren'Py 里 `if True:` 永远成立，所以补它**不改变任何演出**；
#: 它唯一的作用是让"这拍在菜单块里"变成一个可判定的结构信号。
TRUE_CONDITION = 'True'


def split_beat_units(beats: list, use_paths: bool = True) -> list:
    """
    拍序列 → **渲染单元**序列：`[('menu', 菜单组的拍), ('beat', [一拍])]`。

    菜单组从一张 `menu` 拍开始（见 `_take_menu_run` 的边界判据），
    其余每张拍自成一个单元——渲染行为与以前完全一致。

    `use_paths=False` 强制走**老推断**（`_menu_member` / 条件 / 行号）：
    对拍器要用它算"老路本来会怎么摆"，否则就成了拿路径比路径
    （见 `nest_placement_conflicts`）。
    """
    beats = list(beats or ())
    # **结构位齐不齐**逐 label 判（B 阶段二）：齐了就让块路径说了算，
    # 否则那一份整份退回老推断 —— 混着用会出现"半截菜单"，而多吞/少吞
    # 一拍都是静默的剧情错误（见 `_take_menu_run` 的 docstring）。
    plan = _nest_plan(beats) if use_paths else {'known': {}, 'menu_no': {}}
    units = []
    i = 0
    while i < len(beats):
        if beats[i].kind == KIND_MENU:
            by_path = _take_menu_run_by_path(beats, i, plan)
            if by_path is None:
                run, i = _take_menu_run(beats, i)
            else:
                run, i = by_path
            units.append(('menu', run))
            continue
        if _is_loop_head(beats[i]):
            # 结构化循环（2026-10-07）：头拍 + 结构位在 `loop:n` 里的那些拍
            # = 一个 `while …:` 块。取不到结构位就**退回单拍**（渲染层会给
            # 一句显式 TODO，见 `render_beat_to_rpy`）—— 宁可让人看见
            # "这一块没还原成循环"，也不要静默把它拍平成顺序语句。
            by_path = _take_block_run_by_path(beats, i, plan, 'loop')
            if by_path is None:
                units.append(('beat', [beats[i]]))
                i += 1
            else:
                units.append(('loop', by_path[0]))
                i = by_path[1]
            continue
        units.append(('beat', [beats[i]]))
        i += 1
    return units


def _is_loop_head(beat) -> bool:
    """
    这一拍是**结构化循环的组头**吗？

    判据两条，缺一不可（与 `menu` 头拍的判据同源）：

      · 有头行（`loop_header`）—— 老形态也有，所以单看它不够；
      · **没有** `opaque` 整块原文 —— 有它就是"整块逐字"那条老路
        （`emit_opaque` 会逐字吐回去，不能再按结构位归组，否则同一段内容
        会出去两遍）。
    """
    if not str(getattr(beat, 'loop_header', '') or ''):
        return False
    return not str(getattr(beat, 'opaque', '') or '').strip()


def _nest_plan(beats: list) -> dict:
    """
    预扫：`{'known': {label: 是不是每条拍都有结构位},
            'menu_no': {id(menu 拍): 它在 label 内第几个}}`。

    ⚠ **`menu_no` 必须从块路径推，不能"数第几张 KIND_MENU"**（2026-10-05 修，
    嵌套菜单）：`split_beat_units` 会被递归调用在**子列表**上（选项体那一段拍），
    子列表里的局部次序与 label 的文档序**对不上** —— 两层的例子里，嵌套菜单头
    在子列表里是第 1 张 `KIND_MENU`，可它的路径写的是 `menu:2`。数出来的编号
    与路径不符，切组就只切到"菜单头"一张拍（`render_menu_block(indent=8 拍数=1)`
    —— `tools/probe_nested_render.py` 的调用轨迹原样抓到），整层菜单随之塌掉。

    怎么从路径推：菜单头自己那条路径**不含**它自己的编号（那是"它所在的外层
    块"），编号出现在它的**子孙**路径里 —— 头后面第一条路径以
    `头路径>menu:` 开头的拍，取那一段的 `n`。一个菜单一个编号，label 内不重复，
    所以"往后找到第一条"就够。
    """
    known: dict = {}
    menu_no: dict = {}
    loop_no: dict = {}
    counters: dict = {}
    items = list(beats or ())
    for i, b in enumerate(items):
        label = str(getattr(b, 'label', '') or '')
        if getattr(b, 'nest_path', None) is None:
            known[label] = False
        else:
            known.setdefault(label, True)
        if b.kind == KIND_MENU:
            counters[label] = counters.get(label, 0) + 1
            n = _menu_number_from_path(items, i)
            menu_no[id(b)] = n if n else counters[label]
        elif _is_loop_head(b):
            counters[f'loop:{label}'] = counters.get(f'loop:{label}', 0) + 1
            n = _block_number_from_path(items, i, 'loop')
            loop_no[id(b)] = n if n else counters[f'loop:{label}']
    return {'known': known, 'menu_no': menu_no, 'loop_no': loop_no}


def _block_number_from_path(beats: list, head_i: int, seg: str):
    """
    组头拍的 `head_i` 的 `<seg>:n` 里的 `n`（从它子孙的块路径里读）。

    为什么不能"数第几张头拍"：`split_beat_units` 会被**递归**调用在子列表上
    （循环体 / 选项体那一段拍），子列表里的局部次序与 label 的文档序对不上
    —— 菜单那次就是这么切错的（见 `_nest_plan` 的 docstring）。
    读不到（空块体 / 结构位不全）返回 `0`，调用方退回"数第几张"。
    """
    head = beats[head_i]
    head_path = getattr(head, 'nest_path', None)
    if head_path is None:
        return 0
    head_path = str(head_path)
    want = f'{head_path}>{seg}:' if head_path else f'{seg}:'
    for b in beats[head_i + 1:]:
        path = getattr(b, 'nest_path', None)
        if path is None:
            return 0
        path = str(path)
        if not path.startswith(want):
            continue
        token = path[len(want):].split('>', 1)[0]
        return int(token) if token.isdigit() else 0
    return 0


def _take_block_run_by_path(beats: list, start: int, plan: dict, seg: str):
    """
    结构位齐全时：`seg` 组 = **块路径落在同一棵子树里**的那些拍。

    菜单（`seg='menu'`）与循环（`seg='loop'`）**共用这一份实现** —— 边界判据
    只许有一处："少吞一拍 / 多吞一拍"都是静默改剧情（循环更狠：少吞 = 那一句
    只执行一次，多吞 = 执行多次），复制一份判据就是给这类缺陷开第二条路。

    与 `_take_menu_run_by_path` 的两点差异（都是刻意的）：

      · 循环组**没有"选项行"这一层**（`while` 的头后面直接就是块体），所以
        只按"在不在子树里"收；
      · **掉队的拍照样收进来**（中间插了一张路径不在子树里的拍，但后面还有
        本组的拍）—— 与菜单同款：宁可多收一张（它本来就在这个块的位置上），
        也不要把块从中间劈成两半。

    返回 `None` = 这一份用不了路径（调用方退回单拍 / 老推断）。
    """
    head = beats[start]
    label = str(getattr(head, 'label', '') or '')
    if not plan['known'].get(label):
        return None
    k = (plan.get(f'{seg}_no') or {}).get(id(head))
    if not k:
        return None
    prefix = join_nest(getattr(head, 'nest_path', '') or '', f'{seg}:{k}')
    run = [head]
    i = start + 1
    while i < len(beats):
        if _under_nest(getattr(beats[i], 'nest_path', None), prefix):
            run.append(beats[i])
            i += 1
            continue
        # 不在子树里：后面还有本组的拍吗？（有 → 这是"掉队"的拍，一起收）
        nxt = _next_under_nest(beats, i + 1, prefix)
        if nxt is None:
            break
        run.extend(beats[i:nxt])
        i = nxt
    return run, i


def _menu_number_from_path(beats: list, head_i: int):
    """菜单头 `beats[head_i]` 的 `menu:n` 里的 `n`（见 `_block_number_from_path`）。"""
    return _block_number_from_path(beats, head_i, 'menu')


def _under_nest(path, prefix: str) -> bool:
    """`path` 在这棵子树里吗（等于前缀，或前缀 + `>` 再往下）。"""
    if path is None:
        return False
    path = str(path)
    return path == prefix or path.startswith(f'{prefix}>')


def _next_under_nest(beats: list, start: int, prefix: str):
    """`start` 之后（不含）还有没有落在 `prefix` 里的拍？返回它的下标。"""
    for j in range(start, len(beats)):
        if _under_nest(getattr(beats[j], 'nest_path', None), prefix):
            return j
    return None


def _take_menu_run_by_path(beats: list, start: int, plan: dict):
    """
    结构位齐全时：菜单组 = **块路径落在同一棵子树里**的那些拍（B 阶段二）。

    比老推断强在哪：一张"掉队的选项拍"（策划在 Excel 里插过一拍）不再靠
    `_menu_looks_unfinished` 去救 —— 它的路径直接说明它在这个菜单里。

    落单的拍（不在这棵子树里、但后面还有本菜单的拍）**照样收进来**，
    交给 `_menu_segments_by_path` 标成 `foreign`（渲染时带显式警告）——
    这与老路一致，且避免"半截菜单"。

    返回 `None` = 这一份用不了路径（调用方退回老路）。
    """
    head = beats[start]
    label = str(getattr(head, 'label', '') or '')
    k = plan['menu_no'].get(id(head))
    if not plan['known'].get(label) or not k:
        return None
    prefix = join_nest(getattr(head, 'nest_path', '') or '', f'menu:{k}')
    run = [head]
    i = start + 1
    while i < len(beats):
        if _under_nest(getattr(beats[i], 'nest_path', None), prefix):
            run.append(beats[i])
            i += 1
            continue
        # 不在这棵子树里：后面还有本菜单的拍吗？（有 → 这是"掉队"的拍）
        if beats[i].kind == KIND_MENU:
            break
        nxt = _next_under_nest(beats, i + 1, prefix)
        if nxt is None:
            break
        run.extend(beats[i:nxt])              # 掉队的那几张一起收
        i = nxt
    return run, i



def _take_menu_run(beats: list, start: int) -> tuple:
    """
    从 `start`（一张 `menu` 拍）起，取走整个菜单组的拍。

    **这里最容易出错，而且一错就是静默的剧情错误**（实测踩过两次）：
    多吞一拍 = 那句台词被塞进最后一个选项里，**只有选中那个选项才会播**；
    少吞一拍 = 分支体掉到菜单外，无条件执行。

    判据按优先级：

      · **结构位**（可靠）：从 `.rpy` 解析出来的拍带着
        `_menu_member`（解析器在 `_walk` 里标的"这条语句在菜单块内部"）。
        它直接回答"这拍算不算菜单组"，不用猜。
      · **下一张 `menu` 拍** → 新菜单开始（连续两个菜单不能被并成一块）。
      · **源行号**：选项体的拍必然排在它那张选项拍之后、
        `src_last` 不超过该选项体在源码里的最后一行。
      · **条件兜底**（手工构造 / 老表，两位信息都没有）：选项体的拍带着
        解析器 `_combine` 进去的选项条件。

    **为什么不能只靠条件**：解析器把菜单展开成扁平语句流时丢掉了缩进，而

        menu:
            "进去":          ← 选项自身无条件
                jump inner
        "剧情继续"            ← 块外，也**无条件**

    两者的 `condition` 都是空串 —— 任何"条件比对"都会把块外那句吞进菜单。

    **为什么还要接住"掉队的选项拍"**（2026-10-05，P1-2 实测）：
    分组是**重建**的（`_menu_member` 存不进 Excel），所以策划在 Excel 里
    往两个选项之间插一拍、或给某拍清掉条件时，`_in_menu_body` 会在那一拍上
    判 False、组就此断开，**后面的选项拍全部掉出菜单** —— 产物里
    `"选项B"` 变成一句普通旁白、它的块体成了无条件执行的 `if True:`。

    所以组**只能收在"下一个带选项/成员标记的拍之前"**：一张选项拍（或
    `_menu_member=True` 的拍）跟在后面时，中间那张认不出归属的拍照收不误，
    菜单结构不会被一次误判拆散。
    """
    run = [beats[start]]
    #: 当前块体**属于**的选项拍在 `run` 里的下标（None = 还没出现选项拍）
    cur_idx = None
    #: 连续收了几个块体拍（用来认"多余块体"这种畸形数据）
    cur_body = 0
    i = start + 1
    while i < len(beats):
        b = beats[i]
        if b.kind == KIND_MENU:
            break
        if b.kind == KIND_MENU_OPTION:
            run.append(b)
            cur_idx = len(run) - 1
            cur_body = 0
            i += 1
            continue
        if not _in_menu_body(b, run, cur_idx):
            # 认不出归属 —— 但后面**还有**选项/成员拍时不能就此收尾，
            # 否则那半截菜单会被散成一句句普通台词（静默改剧情）。
            if not _menu_looks_unfinished(beats, i + 1):
                break
        run.append(b)
        cur_body += 1
        i += 1
    return run, i



def _menu_looks_unfinished(beats: list, start: int) -> bool:
    """
    从 `start` 往后扫，**在遇到菜单块外的拍之前**还有没有选项/成员拍？

    用来救"中间插了一拍、后面接着还是这个菜单"的畸形序列。
    一遇到明确属于块外的拍（`_menu_member is False`）就停 —— 那才是菜单
    真的结束了（`menu: …` 之后那句块外台词正是这个形态）。
    """
    for b in beats[start:]:
        if b.kind == KIND_MENU:
            return False                 # 新菜单开始，本菜单到此为止
        if b.kind == KIND_MENU_OPTION:
            return True
        if getattr(b, '_menu_member', None) is True:
            return True
        if getattr(b, '_menu_member', None) is False:
            return False                 # 明确在菜单块外
    return False



def _in_menu_body(beat, run: list, cur_idx) -> bool:
    """
    这一拍属于**当前菜单组**吗？

    判据（按可靠性排序）：

      1. **结构位** `_menu_member`（`.rpy` 解析来的拍有）：
         `True` = 在某个选项体的内部；`False` = 不是（菜单块外的拍）。
         `None` = 没有结构信息（Excel 回导 / 手工构造的 Beat）→ 走第 2 档。
      2. **条件**（Excel 回导走这条）：块体拍带着"本选项 + 外层菜单"累积的
         条件；**无条件**的块体在写表时会被补成恒真的 `True`
         （见 `stamp_menu_bodies`），所以它也有可判定的条件。
         块外的拍条件是空串（或与选项无关的其它值），因此能分开。

    为什么这里可以只看"当前选项"、不必回头找归属：拍序列已经在
    `beat_build.order_menu_bodies` 里**归一化**成"选项 → 它的块体"了。
    """
    if cur_idx is None:
        # 还没出现任何选项拍 → 它不可能是任何选项体的内容
        return False

    flag = getattr(beat, '_menu_member', None)
    if flag is not None:
        return bool(flag)

    expected = _expected_body_condition(run[cur_idx], run[0].condition)
    return (beat.condition or '').strip() == expected




def _group_by_label(beats: list) -> list:
    """按 label 分组（保持文档序）。块路径里的菜单序号是**label 内**数的。"""
    order, buckets = [], {}
    for b in beats or ():
        name = str(getattr(b, 'label', '') or '')
        if name not in buckets:
            buckets[name] = []
            order.append(name)
        buckets[name].append(b)
    return [(n, buckets[n]) for n in order]


def _actual_chains(group: list, counter: list, parent_chain: tuple) -> dict:
    """
    老推断把这一组拍**实际**摆到了哪条选项体链上：`{id(拍): 链}`。

    与渲染**同源**：`split_beat_units` 切菜单组、`_menu_segments` 切选项体；
    选项体里再套菜单时**递归**（计数器接着数，所以嵌套菜单的序号与解析器
    的文档序一致）。认不出归属的拍（`_is_menu_foreign`）算**父层** ——
    它们渲染时本来就被摆在菜单外（带一句显式警告）。
    """
    chains: dict = {}
    # ⚠ **必须 `use_paths=False`**：这里要算的是"**老推断**会把它摆在哪"，
    # 拿路径再算一遍就成了自证（永远一致），对拍器也就没用了。
    for kind, unit in split_beat_units(group, use_paths=False):
        if kind != 'menu':
            for b in unit:
                chains[id(b)] = parent_chain
            continue
        counter[0] += 1
        n = counter[0]
        for oi, (opt, body, foreign) in enumerate(
                _menu_segments(unit, use_paths=False), start=1):
            if opt is None:
                continue
            chains[id(opt)] = parent_chain          # 选项行在菜单层
            chain = parent_chain + ((n, option_key(oi)),)
            body_beats = [b for _pos, b, _why in body]
            for b in body_beats:
                chains[id(b)] = chain
            for _pos, b, _why in foreign:
                # **认不出归属 = 老推断不知道**，不是"摆在菜单外"（2026-10-05）：
                # 记成 `parent_chain` 会让对拍器把它当成"结构位说谎"，
                # 而嵌套菜单正是它认不出的那一类 —— 那是**能力边界**，
                # 报出来只会制造假阳性。`None` = 不知道，对拍器跳过。
                chains[id(b)] = None
            # 选项体里可能还有嵌套菜单 → 递归
            for bid, c in _actual_chains(body_beats, counter, chain).items():
                chains[bid] = c
        for b in unit:                              # 菜单头等杂项拍
            chains.setdefault(id(b), parent_chain)
    return chains


def nest_placement_conflicts(beats: list) -> list:
    """
    **对拍器**（B 阶段一）：结构列（`Nest`）说的位置 vs 老推断**实际**摆的位置。

    返回 `[(label, 拍序号, 结构列说的链, 实际摆的链), …]`，只报不一致的。

    为什么要有它：`menu` 在 Ren'Py 里是**嵌套块**，而拍是扁平的 ——
    "这条拍属于哪个选项"以前只有两个信息源：
      · `_menu_member`（.rpy 侧，**进不了 Excel**）；
      · `Condition` 串 + 行号（表格侧 —— 只有 1 bit：在选项体里 / 不在，
        分不出**哪个**选项、也分不出多深；选项体一复杂信号就塌）。
    `Nest` 列是第三个、也是最完整的那个。这一版**不改任何行为**
    （渲染仍然走老推断），只把"两者不一致"说出来 —— 全绿之后，
    阶段二才让渲染改用结构列。

    ⚠ 只对**整份结构位都已知**的 label 判（`nest_path is None` = 老表 /
    手工加的行）。拿"不知道"当"在顶层"会天天假报，而假阳性比漏报更贵。
    """
    conflicts: list = []
    for name, group in _group_by_label(beats):
        if any(getattr(b, 'nest_path', None) is None for b in group):
            continue
        # ⚠ **这个 label 里有嵌套菜单 → 整份跳过**（2026-10-05）。
        #
        # 老推断（`_menu_member` / 条件 / 行号）的能力上限是 1 bit：
        # "这一拍在某个选项体里 / 不在"，分不出是哪一层的**哪个**选项。
        # 选项体里再套一层菜单时它**整份**都不可信 —— 不只是嵌套那几拍：
        # 它会把嵌套那一层当成外层菜单的选项，于是**嵌套菜单之后**的拍
        # （`e "后"`，路径 `menu:1>A`）也被算到那个假选项里（实测
        # `minimal_nested_menu`：4 处"不一致"，结构列全对）。
        #
        # 所以判据限定在"没有嵌套菜单"的 label 上（真实项目 40 个文件全是
        # 这一类，那里它被验证过、也确实抓到过东西）。嵌套那几层由渲染侧的
        # 判据管：`tools/hell_nest_inline.py` 的 N1/N2/N3 与
        # `tests/test_nested_menu_render.py`。
        if any(str(getattr(b, 'nest_path', '') or '').count('menu:') >= 2
               for b in group):
            continue
        actual = _actual_chains(group, [0], ())
        for i, b in enumerate(group):
            path = getattr(b, 'nest_path', '') or ''
            claim = body_chain(path)
            got = actual.get(id(b), ())
            if got is None:
                # 老推断**认不出**这一拍的归属（见 `_actual_chains` 的 foreign）；
                # "不知道"不等于"不一致"，跳过（嵌套菜单就是这一类）。
                continue
            # ⚠ **老推断做不到嵌套菜单，所以不对嵌套形态判"不一致"**
            # （2026-10-05，`minimal_nested_menu` 实测）。
            #
            # 它的能力上限是"这一拍在某个选项体里 / 不在"这 1 bit
            # （`_menu_member` 进不了 Excel、`Condition` 也只有 1 bit）：
            # 选项体里再套一层菜单时，它要么把那一层的选项行当成**外层菜单**
            # 的选项，要么把菜单头算成"顶层" —— 而结构列全是对的。
            # 那不是"结构位说谎"，是**它没有这个能力**；拿它当判据只制造假阳性
            # （假阳性比漏报更贵）。
            #
            # 判据：这一拍处在**嵌套菜单的上下文**里吗 —— 看它自己的块路径
            # 有没有**两个及以上** `menu:` 段，或者它本身就是一张**在块里的**
            # 菜单头（`KIND_MENU` 且路径非空 = 嵌在别人体内）。
            # 嵌套那几层由渲染侧的判据管：`tools/hell_nest_inline.py` 的
            # N1 结构守恒 / N2 不动点 / N3 两路一致，以及
            # `tests/test_nested_menu_render.py`。
            nested_ctx = (path.count('menu:') >= 2
                          or (b.kind == KIND_MENU and bool(path)))
            if nested_ctx or len(claim) >= 2 or len(got) >= 2:
                continue
            if claim != got:
                conflicts.append((name, i, claim, got))
    return conflicts


def _last_option(run: list):
    """
    这一组菜单里**当前生效**的选项的条件。

    取最后一张拍的条件（而不是最后一张 `menu_option` 的条件）：
    Excel 回导之后选项体的条件与选项条件相同、甚至被补成 `True`，
    此时"当前选项的上下文条件"就是那个值。取选项自己的条件会让
    "选项无条件"的体永远对不上（两边都是空串时又人人相等）。
    """
    return run[-1] if run else run



def _split_conjuncts(text: str) -> list:
    """
    `cond` 文本 → **顶层**合取项列表（只切括号深度为 0 的 `and`）。

    为什么不能 `text.split(' and ')`（2026-10-05，真实数据实测）：
    条件是被 `_combine` **层层嵌套**起来的 ——
    `((flag0) and (flag2)) and (flag4)`。按字面切会切进括号里面，
    得到 `['((flag0)', '(flag2))', '(flag4)']` 这种**碎片**：

      · 碎片既不等于任何合取项，于是"减条件"永远减不掉 → 条件每轮翻倍
        （`depth5_imimi` 的 N2 不动点失败）；
      · 更糟的是**产出语法坏的文本**：把碎片拼回去会得到
        `'(((flag0) and (flag4))'` —— 括号不配对，写进 `if` 就是产物语法错。

    同一栈里 `not (a and b)`、`(a or b)` 这些内部带 `and` 的项，
    也会被它切碎。

    切分对空白宽容（`a and b` / `a  and  b` 都认），但要求 `and` 两侧是词边界
    （`brand` 不算）—— 手写在「条件」列里的条件不一定带括号。
    """
    parts, depth, buf = [], 0, ''
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if depth == 0 and text.startswith('and', i) \
                and (i == 0 or not (text[i - 1].isalnum()
                                    or text[i - 1] == '_')) \
                and (i + 3 >= n or not (text[i + 3].isalnum()
                                        or text[i + 3] == '_')):
            parts.append(buf)
            buf = ''
            i += 3
            continue
        buf += ch
        i += 1
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def _bare_condition(text: str) -> str:
    """
    反复脱掉**整层**包裹括号：`((flag0))` → `flag0`。

    ⚠ 必须确认"首括号配对的右括号就在末尾"（2026-10-05 修）：
    只看"首字符是 `(`、末字符是 `)`"就脱，会把
    `(flag0) and (flag2)` 脱成 `flag0) and (flag2`（**碎掉**）——
    旧实现正是这样，于是 5 层嵌套里真的产出过 `'(((flag0) and (flag4))'`
    这种括号不配对的 `if` 条件（写进产物就是语法错）。
    """
    text = (text or '').strip()
    while len(text) > 2 and text[0] == '(' and text[-1] == ')':
        depth, closes_at_end = 0, False
        for i, ch in enumerate(text):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    closes_at_end = (i == len(text) - 1)
                    break
        if not closes_at_end:
            return text
        text = text[1:-1].strip()
    return text


def _has_top_level_or(text: str) -> bool:
    """顶层（括号深度 0）有没有 `or`？有就**不能**随便脱括号。"""
    depth, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if depth == 0 and text.startswith('or', i) \
                and (i == 0 or not (text[i - 1].isalnum()
                                    or text[i - 1] == '_')) \
                and (i + 2 >= n or not (text[i + 2].isalnum()
                                        or text[i + 2] == '_')):
            return True
        i += 1
    return False


def _rejoin_conjunct(text: str) -> str:
    """
    一个**残余项**要塞进合取式里时的写法（括号该留就留）。

    为什么必须管括号（2026-10-05 自查抓到）：`and` 比 `or` 紧，
    把 `(a or b)` 脱成 `a or b` 再和别的项 `and` 起来，
    `x and (a or b)` 就变成了 `(x and a) or b` —— **语义被改了**。
    所以只有"单个合取项且顶层没有 `or`"的残余才能不带括号。
    """
    text = (text or '').strip()
    if not text:
        return ''
    inner = _bare_condition(text)
    if len(_split_conjuncts(inner)) > 1 or _has_top_level_or(inner):
        return f'({inner})'
    return inner


def _condition_atoms(text: str) -> set:
    """
    条件 → **原子合取项**集合（括号只当分组，递归拆到底）。

    `((flag0) and (flag2)) and (flag4)` → `{'flag0', 'flag2', 'flag4'}`；
    `(flag0) and not ((flag2))` → `{'flag0', 'not ((flag2))'}`
    （`not (…)` 是**一个**原子项，不能再拆 —— 拆了就是改语义）。

    用途见 `strip_condition_context`（判"这一项外层 `if` 是否已经写过"）
    与 `_condition_is_refinement`（判"是不是同一分支的加强版"）——
    **两份共用一个实现**：从前这里有两份各自递归的实现，其中一份
    `_split` 只认 `' and '` 也递归，遇到 `not (a and b)` 会**无限递归**
    （`RecursionError`），另一份按字面切又会切碎括号。

    `_atoms('((outer) and (middle)) and (deepest)')`
      → `{'outer', 'middle', 'deepest'}`（括号只当分组，递归拆到底）
    `_atoms('not (a and b)')` → `{'not (a and b)'}`（`not (…)` 是**一个**原子项）
    """
    out = set()
    for part in _split_conjuncts(text):
        inner = _bare_condition(part)
        if len(_split_conjuncts(inner)) > 1:
            out |= _condition_atoms(inner)      # 括号里还是一个合取式
        else:
            out.add(inner)
    return out


def strip_condition_context(cond: str, context: str) -> str:
    """
    从 `cond` 里**去掉已经写在结构上的那部分条件** `context`。

    为什么需要（2026-10-05，N2 不动点实测）：解析器把外层条件 `_combine` 进了
    里层每一拍，而渲染时外层那个 `if:` 往往**已经由结构写出来了** ——
    菜单块自己的条件由 `_menu_block_condition` 写在 `menu:` 外面。里层再写一遍
    `(flag0) and (flag2)`，产物再解析就会被**再累加**一次外层条件，
    每轮更长（语义等价，但违反"渲染不动点"）：

        if flag0:
            menu:
                "选项1":
                    if (flag0) and (flag2):   ← 多写了 flag0
                        e "叶子"

    判据是**合取项级**的相等（两边都按 ` and ` 切开、去空白后比字面量），
    所以 `flag0` 能去掉 `(flag0)`：把两边都归一成"去掉包裹括号"再比。
    减不干净（对不上）时**原样返回** —— 宁可多写一层条件，也不要漏写一个
    合取项（漏了就是"该满足条件才播的，变成无条件播"）。

    **嵌套的合取式也要能减**（2026-10-05 修，`depth5_imimi`）：5 层混合嵌套
    里，条件长成 `((flag0) and (flag2)) and (flag4)` —— 外层结构已经写了
    `if flag0:` / `if flag2:`，所以这里该只剩 `flag4`。括号里的那一坨
    本身也是合取式，**递归下去**减（合取可结合，去掉被上下文蕴含的项
    永远安全）：只要它的每一项都能在 `context` 里找到，整坨就可以去掉。

    减掉过东西才返回新值；`dropped` 为假表示"一项都没对上"，
    原样返回（不猜）。
    """
    cond = str(cond or '').strip()
    context = str(context or '').strip()
    if not cond or not context:
        return cond
    if cond == context:
        return ''

    ctx = _condition_atoms(context)

    def _residual(text: str) -> tuple:
        """`text` 减去 `ctx` 里已有的项 → `(残余文本, 有没有减掉过)`。"""
        bare = _bare_condition(text)
        if bare != text:
            # 括号里可能还是一个合取式 → 进去看（括号只是分组，语义不变）
            rest, did = _residual(bare)
            return (_rejoin_conjunct(rest) if rest else ''), did
        pieces = _split_conjuncts(text)
        if len(pieces) > 1:
            kept, dropped = [], False
            for part in pieces:
                rest, did = _residual(part)
                dropped = dropped or did
                if rest:
                    # ⚠ 每个残余项都要**自带括号**（`or` 项 / 嵌套合取项），
                    # 否则 `x and (a or b)` 会被拉平成 `(x and a) or b`。
                    kept.append(_rejoin_conjunct(rest))
            return ' and '.join(kept), dropped
        if _bare_condition(text) in ctx:
            return '', True
        return text, False

    lean, dropped = _residual(cond)
    if not dropped:
        return cond                       # 一项都没减掉 → 原样（不猜）
    pieces = _split_conjuncts(lean)
    if len(pieces) == 1:
        # 只剩一项：**脱掉包裹括号**。`_combine` 每轮都会再包一层，留着
        # `((flag2))` 的话"渲染 → 解析 → 渲染"永远多一层（语义一样，
        # 但 N2 的**逐字节不动点**过不去）。脱到最里面之后，解析器下一轮
        # 又会包一层，正好回到 `(flag2)` —— 收敛。
        return _bare_condition(pieces[0])
    return lean


def _expected_body_condition(option, menu_condition) -> str:
    """
    选项体里的拍**应当**带的条件。

    解析器把选项条件 `_combine` 进了选项体的每一拍；而写表时
    （`stamp_menu_bodies`）会把**空条件**的选项体补成恒真的 `True`。
    所以三档：

      · 选项带条件 → 就是它；
      · 选项无条件、菜单挂在某个 `if` 下 → 是那个**组合后**的条件
        （`_combine` 把外层条件并了进来）；
      · 两者都没有 → `True`（写表时补的恒真条件，见 `TRUE_CONDITION`）。

    这一档不能漏：漏了就会把"无条件选项的选项体"判成块外内容。

    `option` 可以是选项拍，也可以是一个条件字符串（`_render_menu_segments`
    要从"选项自身的条件"出发算同一个值）。
    """
    raw = getattr(option, 'condition', None)
    own = (option if raw is None and isinstance(option, str)
           else raw or '').strip()
    menu = str(menu_condition or '').strip()
    if own and menu and own != menu:
        return f'({menu}) and ({own})'
    return own or menu or TRUE_CONDITION




def _menu_segments_by_path(run: list):
    """
    结构位齐全时：选项归属**由块路径直接决定**（B 阶段二）。

    与老路的差别（也是它存在的理由）：

      · **不再猜**。老路靠 `_menu_member`（进不了 Excel）与 `Condition` 串
        （只有 1 bit）判"这条拍属于哪个选项"；选项体里一旦有第二条语句
        或嵌套块，信号就塌 —— 实测会产出 `"选项": pass` + 内容提到菜单外
        （**选没选都会执行**，`tools/probe_nesting_min.py`）。
      · **"认不出归属"这一类基本消失**：路径齐的时候，拍要么在菜单层
        （选项行），要么在某个选项键下面。真正畸形（在菜单层却不是选项行）
        才进 `foreign`，渲染时带一句显式警告。

    选项键用的是**位置键**（第 1 个选项 `A`、第 2 个 `B`…）—— 与解析器
    写路径时同一套口径（`core/nest_path.option_key`）。

    返回 `None` = 这一组用不了路径（有拍没结构位 / 算不出本菜单前缀），
    调用方退回老路。
    """
    if not run:
        return None
    # **整组必须都有结构位**，一条"不知道"就整组退回老路 ——
    # 与 `_take_menu_run_by_path` 同一套粒度。
    # ⚠ 别只在前缀那一趟里查：手工加的行（`nest_path is None`）会在后面的
    # 落拍循环里被 `str(None)` 变成字面量 `'None'`、**静默跳过** ——
    # 那一拍就这么丢了（`test_inserting_a_beat_between_options_keeps_the_menu`
    # 当场抓到）。
    if any(getattr(b, 'nest_path', None) is None for b in run):
        return None
    head_path = str(getattr(run[0], 'nest_path', '') or '')
    depth = len(split_nest(head_path))
    prefix = ''
    for b in run[1:]:
        path = getattr(b, 'nest_path', None)
        if path is None:
            return None                       # 有一条不知道 → 整组退回老路
        segs = split_nest(path)
        if len(segs) > depth and segs[-1].startswith('menu:'):
            prefix = '>'.join(segs)
            break
    if not prefix:
        return None                           # 没有选项行 → 交给老路
    # 建段的第一趟：按**选项行的顺序**排好位置（选项行本身不带键）。
    option_rows: list = []                    # [(pos, 选项拍)]
    malformed: list = []                      # 菜单层上"不是选项行"的畸形拍
    keys_in_order: list = []                  # 块体按键**首次出现**的顺序
    for pos, b in enumerate(run[1:]):
        path = str(getattr(b, 'nest_path', ''))
        if path == prefix:
            if b.kind == KIND_MENU_OPTION:
                option_rows.append((pos, b))
            else:
                malformed.append((pos, b))
            continue
        rest = path[len(prefix):].lstrip('>') if path.startswith(prefix) else ''
        if not rest:
            return None                       # 在菜单里却算不出键 → 交给老路
        key = rest.split('>')[0]
        if key not in keys_in_order:
            keys_in_order.append(key)
    if not option_rows:
        return None                           # 一级选项都没有 → 老路
    # 键 → 段：**先按键本身**（`B` → 第 2 段，那是"结构位"的本义），
    # 解不出来才退回"按出现顺序配对"。
    #
    # 为什么不能只用顺序配对（2026-10-05 修，空选项体实测）：
    # **某个选项体是空的**时候键会缺一个 ——
    #
    #     menu:
    #         "空的":            ← A，没有块体
    #         "有内容":          ← B
    #             e "x"          ← 路径 `menu:1>B`
    #
    # 唯一的键 `B` 会被配给第 1 个选项（`空的`）→ **两个选项体对调**：
    # 选"空的"播了台词、选"有内容"什么都不做（静默改剧情）。
    #
    # 为什么还要保留顺序配对：**表可以被编辑** —— 删掉选项 A 之后，
    # 存下来的键还是 `B`/`C`（越界了），而它们现在排第 1/2 位。
    # 判据因此是"**所有**键都能解成互不重复、且落在选项数之内的序号"，
    # 才敢按键配对；只要有一个解不出/越界/撞车，就整体退回顺序配对
    # （`test_deleting_an_option_keeps_the_rest` 钉着这一档）。
    segments = [[opt, [], []] for _pos, opt in option_rows]
    key_of: dict = {}
    by_key: dict = {}
    used: set = set()
    for key in keys_in_order:
        i = option_index(key)
        if i and i <= len(segments) and (i - 1) not in used:
            by_key[key] = i - 1
            used.add(i - 1)
    if len(by_key) == len(keys_in_order):
        key_of = by_key
    else:
        for i, key in enumerate(keys_in_order):
            if i < len(segments):
                key_of[key] = i
    # 第二趟：块体拍按**它的键**归位；键对不上（编辑过）就按出现顺序找空位
    for pos, b in enumerate(run[1:]):
        path = str(getattr(b, 'nest_path', ''))
        rest = path[len(prefix):].lstrip('>') if path.startswith(prefix) else ''
        if not rest:
            continue                          # 菜单层上的拍：第一趟已经处理过
        if b.kind == KIND_MENU_OPTION and path == prefix:
            continue                          # **本层的**选项行（第一趟建过段）
        # ⚠ **嵌套菜单的选项行必须留下**（2026-10-05 修，`minimal_nested_menu`）：
        # 它的路径是 `menu:1>A>menu:2`（`rest='A>menu:2'`），属于**本层选项 A
        # 的块体**，要交给 `render_menu_block` 递归去渲染那一层菜单。
        # 第一版这里写的是"凡 `KIND_MENU_OPTION` 一律跳过" —— 那会把嵌套菜单的
        # 选项行**静默丢掉**：菜单头还在、选项行没了，整层菜单就塌进外层选项体
        # （`e "深深"` 直接漏到外面）。老推断那条路至少把它记成 `foreign` 保住结构。
        key = rest.split('>')[0]
        idx = key_of.get(key)
        if idx is None:
            # 没配上的键（例如它的选项行被删了）：找第一个还没认领的段
            idx = next((k for k in range(len(segments))
                        if not segments[k][1]), None)
            if idx is None:
                return None                   # 实在放不下 → 交给老路
            key_of[key] = idx
        segments[idx][1].append((pos, b, ''))
    for pos, b in malformed:                  # 菜单层上"不是选项行"的拍
        segments[-1][2].append((pos, b, '这一拍在菜单层、却不属于任何选项'))
    return segments


def _menu_segments(run: list, use_paths: bool = True) -> list:
    """
    菜单组 → `[(选项拍 or None, 选项体的拍列表, 认不出归属的拍), ...]`。

    按 `menu_option` 切段：一张选项拍之后的拍都归它（直到下一张选项拍）。

    **认不出归属的拍**（见 `_is_menu_foreign`）单独记在第三位：渲染时给它
    加一句显式警告，而不是悄悄塞进当前选项体（那会让它变成"只有选中这个
    选项才播"的静默剧情错误）。
    """
    if use_paths:
        by_path = _menu_segments_by_path(run)
        if by_path is not None:
            return by_path
    menu_cond = (run[0].condition or '').strip() if run else ''
    segments = []
    for pos, b in enumerate(run[1:]):
        if b.kind == KIND_MENU_OPTION:
            segments.append([b, [], []])
        elif segments:
            why = _is_menu_foreign(segments[-1][0], b, menu_cond)
            if why:
                segments[-1][2].append((pos, b, why))
            else:
                segments[-1][1].append((pos, b, ''))
        else:
            # 理论上到不了这里（`_take_menu_run` 从 menu 拍起走），
            # 但真出现时**不能丢**：当成菜单里的杂项拍放在块首。
            segments.append([None, [(pos, b, '')], []])
    return segments



def _is_menu_foreign(option, beat, menu_cond: str) -> str:
    """
    这一拍**能确定**它属于 `option` 的块体吗？不能就返回原因（供写警告）。

    能确定的三条（与 `_in_menu_body` 同一套判据）：

      · `_menu_member is True` —— 解析器标的真结构位（`.rpy → .rpy` 这条
        路，产出必须逐字不变，所以**不能**加警告）；
      · 条件正好等于"本选项 + 外层菜单"的累积条件 —— Excel 往返那条路，
        `stamp_menu_bodies` 把无条件块体补成了 `True`。
      · **条件是上面那个累积条件的"加强版"** —— 选项体里又嵌了一层 `if`：

            menu:
                "选项":
                    show bg_deep
                    if deepest:        ← 它的条件是
                        e "深层"          `((外层) and (选项)) and (deepest)`
                    else:                比块体基准**多一个合取项**

        这一条是必须的（2026-10-05 实测，brutal test 的 F4）：`_menu_member`
        **存不进 Excel**，所以表格往返之后"嵌套 if 的那几拍"只能靠条件
        认出来。不认的后果不是"少一句警告"，而是渲染层**不再写
        `if deepest:`** —— 两个分支都被无条件执行（静默改剧情），
        外加一句 TODO。

    都不成立时这拍很可能是策划**手工插进菜单组中间**的（P1-2）：
    它不属于任何选项，但 Ren'Py 的 `menu:` 里除选项外没有合法位置，
    只能收在某个选项体里放。此时**必须让它可见**。
    """
    if getattr(beat, '_menu_member', None) is True:
        return ''
    if option is None:
        return ''
    got = (beat.condition or '').strip()
    if not got:
        # **条件为空 = 认不出归属**（这个守卫是有用的，别删）：
        # 解析器给"菜单选项体里的拍"都会带上累积条件（连嵌套 if 也是
        # `((外层) and (选项)) and (内层)`），所以**条件为空**只可能是
        # "策划手工插进来的拍"（`Beat(kind=…, text=…)` 不带条件）。
        # 实测它插在两个选项中间时，条件为空正是它唯一的特征 ——
        # 靠这个守卫才认得出、才写得出那句 `# TODO`。
        return '这一拍不确定属于哪个选项'
    expected = _expected_body_condition(option, menu_cond)
    if got in (expected, (option.condition or '').strip()):
        return ''
    if _condition_is_refinement(got, expected):
        return ''
    return '这一拍不确定属于哪个选项'



def _condition_is_refinement(cond: str, base: str) -> bool:
    """
    `cond` 是不是 `base` 的**加强版**（含 `base` 的全部合取项）？

    判据刻意粗粒度 —— 按 `and` 拆成原子，看集合包含：

        base = '(outer) and (middle)'
        cond = '((outer) and (middle)) and (deepest)'
          → base 的原子 {(outer),(middle)} 全在 cond 里 → True（嵌套 if）

        base = '(outer) and (middle)'
        cond = 'not ((outer))'
          → (outer) 不在里面 → False（不是这个选项的块体）

    为什么不解析成 AST 去判蕴含：那要引入一门表达式语法，而这里只需要
    "明显是同一分支的加强"这一档判断。认不出时**保持原来的保守行为**
    （标 foreign、写警告）—— 不会比现在更差。

    原子集合由 `_condition_atoms` 算（与 `strip_condition_context` **同一份
    实现**，见那里的说明：从前两份各自递归，一份还会无限递归）。
    """
    b = _condition_atoms(base)
    if not b:
        return False
    return b <= _condition_atoms(cond)



def _render_menu_segments(run: list, segments: list, indent: str,
                          opt_indent: str, audit, trace: list) -> list:
    """
    递归渲染菜单：返回**已带缩进**的行，行尾不带换行符。

    `indent`     = `menu:` 那一行的缩进；
    `opt_indent` = 选项那一行的缩进（选项体再往里一级）。
    顶层调用传 `indent=''`、`opt_indent='    '`（`menu:` 顶格）。
    嵌套调用由 `_render_unit_lines` 按当前层级给足两者。
    """
    from storygraph.story.beat_model import menu_caption_text

    lines = []

    def emit(line, source='', intent='', status='ok'):
        lines.append(line)
        if trace is not None:
            trace.append({'source': source, 'intent': intent,
                          'code': line, 'status': status})

    caption = menu_caption_text(run[0].text)
    head = f"{indent}menu"
    if caption:
        head += ' ' + _quote(caption)
    head += ':'
    head += _inline_note(run[0])
    emit(head, '菜单', caption)

    for seg in segments:
        opt, body, foreign = (seg + [[], []])[:3] if len(seg) < 3 else seg
        if opt is None:
            # 菜单块里认不出归属的拍（认不出就当普通拍输出，不吞）
            lines.extend(_render_unit_lines(
                [b for _p, b, _w in body], opt_indent, audit, trace))
            continue
        own_cond = (opt.condition or '').strip()
        menu_cond = (run[0].condition or '').strip()
        label = opt_indent + _quote(opt.text or '')
        # 选项**自身**的条件（与菜单自身条件重复时不再写第二遍：
        # 解析器对选项体做过 `_combine`，会把菜单自身的条件并进选项条件里）
        if own_cond and own_cond != menu_cond:
            label += f' if {own_cond}'
        label += ':'
        label += _inline_note(opt)
        emit(label, '菜单', opt.text or '')

        # 选项体在拍上带的条件是**累积**的（`_combine(外层, 选项条件)`）。
        # 两种情况必须分开处理：
        #   · 正好等于"本选项 + 外层菜单"的累积条件 → 那只是解析器的记账，
        #     块体不该再多一层 `if`，把它**剥掉**再渲染；
        #   · 不等于（真的嵌了一层 `if`）→ 保留，让渲染器出嵌套 `if`。
        #
        # 认不出归属的拍（策划插进菜单组中间的）**按原位置**插回来，
        # 前面加一句显式警告：Ren'Py 的 `menu:` 里除选项外没有合法位置，
        # 只能收进某个选项体，静默收进去就成了"只有选中才播"的剧情错误。
        expected = _expected_body_condition(own_cond, menu_cond)
        ordered = list(sorted(list(body) + list(foreign)))
        inner = opt_indent + '    '
        # **选项体统一走这条递归路**（2026-10-05 修，`minimal_nested_menu`）。
        #
        # 从前这里分两支，两支都错在同一个地方：**非"记账条件"的拍一律扁平渲染**
        # （`_indent_beat_lines` 逐拍），于是嵌套菜单的头/选项行永远走不到
        # `render_menu_block` —— 那层菜单要么变成一句台词（`"菜单"`），要么整个
        # 塌进外层选项体。看 `tools/probe_nested_render.py` 的调用轨迹最清楚：
        # 两层的用例里 `render_menu_block` **只被调用一次**（外层那次）。
        #
        # 现在只有一条：
        #   1. 把"累积条件记账"那一层剥掉 —— `got == expected`，或者 `got` 为空
        #      （外层还是内层都没条件时，约定值就是恒真，见 `_expected_body_condition`）；
        #   2. **整段**交给 `_render_unit_lines` —— 它内部 `split_beat_units` →
        #      `render_menu_block`，嵌套菜单就在那里递归，**要几层有几层**；
        #   3. 认不出归属的拍（`why`）在**原位置**插一句显式 TODO（不许静默收进
        #      选项体：那会变成"只有选中这个选项才播"的剧情错误）。
        prepared: list = []
        for _pos, beat, why in ordered:
            got = (beat.condition or '').strip()
            if not why and (not got or got == expected):
                prepared.append((_with_condition(beat, ''), ''))
                continue
            # 要保留自己条件的拍：减去**已经写在结构上**的那部分
            # （选项条件 + 菜单块条件）—— 外层那个 `if:` 已经表达了它。
            # 不减就会翻倍（见 `strip_condition_context` 的 docstring）。
            if not why and got:
                lean = strip_condition_context(got, expected)
                if lean != got:
                    prepared.append((_with_condition(beat, lean), why))
                    continue
            prepared.append((beat, why))
        body_lines = []

        def _render_body(beats_: list) -> list:
            """
            一串拍 → 已带**本层前缀**（`inner`）的行。

            为什么不用 `_render_unit_lines`：它的口径是"相对调用方框架、
            不补前缀"，而这里的调用方（label 循环那边）会补一次 —— 两个框架
            混用，嵌套菜单的**菜单头**就会少一级（`probe_nested_render.py`
            的调用轨迹原样抓到）。这里统一自己补：菜单块用 `indent=''`
            渲染成纯相对形式，再整体加 `inner`：

                菜单头 = inner        选项 = inner+4        选项体 = inner+8

            与 `_indent_beat_lines` 的口径一致，所以**嵌套可以任意深**。
            """
            out = []
            for kind, group in split_beat_units(beats_):
                if kind == 'menu':
                    out.extend(
                        inner + ln if ln.strip() else ''
                        # 一级相对（`render_menu_block(indent=X)` 把**选项**放在 X
                        # 那一级、**菜单头**放在 0 那一级，量过见 probe），
                        # 再整体加 `inner` → 头 inner / 选项 inner+4 / 体 inner+8
                        for ln in render_menu_block(group, indent='    ',
                                                    audit=audit, trace=trace))
                elif kind == 'loop':
                    # 循环同一个口径（2026-10-07）：头在 0 级、块体在 4 级，
                    # 再整体加 `inner`。漏了这一支，**选项体里的循环**
                    # （`test_loop_inside_menu_is_nested`）会整块消失 ——
                    # 因为循环头拍自己没有 `opaque`，单拍渲染是空的。
                    out.extend(
                        inner + ln if ln.strip() else ''
                        for ln in render_loop_block(group, indent='    ',
                                                    audit=audit, trace=trace))
                else:
                    out.extend(_indent_beat_lines(group[0], inner,
                                                  audit, trace))
            return out

        body_run: list = []          # ⚠ 别叫 `run`：那是本函数的参数（整个菜单组）

        def _flush_body() -> None:
            if body_run:
                body_lines.extend(_render_body(list(body_run)))
                body_run.clear()

        for beat, why in prepared:
            if why:
                _flush_body()
                body_lines.append(inner + f'# TODO: {why}')
                body_run.append(_with_condition(beat, ''))
                continue
            body_run.append(beat)
        _flush_body()
        if not body_lines:
            # 空块在 Ren'Py 里是语法错误；"只写条件、不打正文"的选项确实存在
            body_lines = [opt_indent + '    pass']
        lines.extend(body_lines)
    return lines



def _with_condition(beat, condition: str):
    """
    复制一拍、换掉它的条件（只用于渲染：**不改动调用方的拍**）。
    """
    import copy
    clone = copy.copy(beat)
    clone.condition = condition
    return clone



def _render_unit_lines(beats: list, indent: str, audit, trace: list) -> list:
    """
    一串拍 → 已带缩进的行（**嵌套菜单走递归**，与顶层同一套规则）。

    `indent` 是这一串拍所在层级的缩进；菜单在其中占 `menu:` + 一级块体。

    **整个菜单还有自己的条件**（`if flag:` 里包 `menu:`，2026-10-05 实测）：
    那种写法解析出来的 menu 拍 / 选项拍 / 选项体**全都带 `flag`**，
    而 `_render_menu_segments` 只处理"选项自身的条件"，整块的 `if` 就没人写
    —— 产物变成无条件的 `menu:`，**菜单从"满足条件才出现"变成"总是出现"**。
    修法：跑一遍 `_expected_body_condition` 的同一套判据，认出"这一组菜单
    被包在 `if` 里"就把整块缩进一层、外面补 `if`。
    """
    lines = []
    for kind, group in split_beat_units(beats):
        if kind == 'menu':
            # 统一走 `render_menu_block`：菜单整块的条件（`if flag:` 包着
            # `menu:`）也要在那里补，两处各写一遍迟早会不一致。
            #
            # ⚠ 这里的 `indent` 是**相对调用方框架**的层级：本函数不补前缀
            # （与 label 循环那种"调用方补"的框架不同）。选项体那条路已经
            # 改成自己显式补前缀（见 `_render_menu_segments` 里的 `_body_lines`），
            # 所以这里保持原样，别再动 —— 两个框架各按各的口径来。
            lines.extend(render_menu_block(group, indent=indent,
                                           audit=audit, trace=trace))
        elif kind == 'loop':
            # 循环**同理**（2026-10-07）：`while` 整块也有自己的条件要写在
            # 外面，也得靠 `render_loop_block` 递归 —— 漏了这一支，
            # 循环在"认不出归属的拍"那条路上会退化成一句 TODO。
            lines.extend(render_loop_block(group, indent=indent,
                                           audit=audit, trace=trace))
        else:
            lines.extend(_indent_beat_lines(group[0], indent, audit, trace))
    return lines



def _indent_beat_lines(beat, indent: str, audit, trace: list) -> list:
    """
    一拍 → 已按 `indent` 缩进好的行，**保住拍内部的相对缩进**。

    为什么不能 `indent + ln` 一行行贴（2026-10-05 实测，brutal test 的 F4）：

        render_beat_to_rpy 已经有层级概念 —— 条件拍会吐
        `['if deepest:', 'e "深层"']`（第二行是**拍内**的块体，本该更深一层）。
        一律 `indent + ln` 会把两行贴成同一层：

            menu:
                "选项":
                    if deepest:
                    e "深层"        ← 与 `if` 齐平 = **语法错误**

    正解：以**第一条非空行的缩进**为拍自身的基准，其余行的**相对缩进**
    原样保留。这样 `if`/块体、多行 `python:` 块、嵌套菜单都跟着一起走。

    ⚠ **已知的排版漂移（未修，见 `_indent_block_body` 的说明）**：
    `python:` 块里**续行**（括号内换行）的缩进每往返一轮会大一档 ——
    续行自己那 12 个空格里已经含了"块体比 `python:` 深一层"的 4 个，
    而这里按 `base` 平移时又加了一次。试过三种改法（基准改成从非续行取、
    只给续行减一档、`_indent_block_body` 不叠基准），**每一种都让
    全项目丢失数变多或让解析异常从 0 涨到 5**，所以保持现状。
    这是**纯排版**：括号内换行的缩进在 Python 里自由，语义完全等价。
    """
    raw = render_beat_to_rpy(beat, indent='', audit=audit, trace=trace)
    base = None
    for ln in raw:
        if ln.strip():
            base = len(ln) - len(ln.lstrip())
            break
    if base is None:
        return []
    out = []
    for ln in raw:
        if not ln.strip():
            out.append('')
            continue
        out.append(indent + ln[base:] if len(ln) >= base else indent + ln)
    return out



def _menu_block_condition(run: list) -> str:
    """
    这一组菜单**整体**被包在 `if` 里吗？是就返回那个条件。

    判据只有一条：menu 拍自身带条件。

    解析器已经把两种形态分开了（见 `renpy_parser` 里 MENU 语句那一处）：

      · `if flag:` 包着 `menu:` → **menu 拍带 `flag`**（选项体那条累积条件
        也被 `_combine` 并进去，但"选项自己写的 `if`"是空）；
      · `menu:` 里选项写 `"A" if flag:` → **menu 拍不带条件**
        （`flag` 只落在选项/选项体上），`if` 由 `_render_menu_segments`
        写在**选项那一行**。

    所以这里**不需要**再跟选项条件比对 —— 比对反而会在第一种形态下
    误判（两者恰好相等），把整块的 `if` 吞掉。
    """
    if not run:
        return ''
    return (getattr(run[0], 'condition', '') or '').strip()



def render_menu_block(run: list, indent: str = '    ',
                      audit=None, trace: list = None) -> list:
    """
    一组菜单拍 → Ren'Py `menu:` 块的行（**不带缩进**，与
    `render_beat_to_rpy` 的返回约定一致：调用方自己加缩进）。

    产出形状与源脚本同构：

        menu:
            "选项A" if cond:
                <选项体的拍>
            "选项B":
                <选项体的拍>

    `indent` 是**块体那一层的缩进**（默认 4 空格），选项体是它再加一级。

    **整个菜单还有自己的条件**时（`if flag:` 里包 `menu:`），`if` 写在这一块
    **外面**、整块缩进一层 —— 见 `_menu_block_condition`。
    """
    lines = _render_menu_segments(run, _menu_segments(run), '', indent,
                                  audit, trace)
    if not lines:
        return []
    cond = _menu_block_condition(run)
    if cond:
        return [f'if {cond}:'] + ['    ' + ln if ln.strip() else ''
                                  for ln in lines]
    return [lines[0]] + [ln for ln in lines[1:]]



def render_loop_block(run: list, indent: str = '    ',
                      audit=None, trace: list = None) -> list:
    """
    一个循环组（`while` / `for` 头拍 + 它的块体拍）→ 行（**不带外层缩进**）。

    约定与 `render_menu_block` **一致**（同一套框架，不另立一套）：

      · 头行在第 0 级、块体在 `indent` 那一级；调用方负责"这一块整体在哪一层"；
      · 嵌套（循环里套菜单、菜单里套循环）由 `_render_block_body` 递归，
        要几层有几层。

    **整块自己的条件**（`if flag:` 里包 `while`）：解析时它是累积进头行拍的
    `condition` 的，所以这里写在**循环外面**、整块再缩一层；同时把这一层条件
    从块体拍上**剥掉**（`strip_condition_context`）—— 否则每一句块体台词都会
    各套一个 `if flag:`（语义等价，但产物会越滚越多，条件滚雪球就是这么来的）。

    空块体（`while x:` 后面什么都没有）补一行 `pass`：Ren'Py 要求缩进块非空，
    否则整份产物解析不了。
    """
    if not run:
        return []
    head = run[0]
    header = str(getattr(head, 'loop_header', '') or '').strip()
    if not header:
        return []
    head_cond = str(getattr(head, 'condition', '') or '').strip()
    body_beats = list(run[1:])
    if head_cond:
        body_beats = [
            _with_condition(b, strip_condition_context(
                str(getattr(b, 'condition', '') or '').strip(), head_cond))
            for b in body_beats]
    lines = [header + _inline_note(head)]
    body = _render_block_body(body_beats, indent, audit, trace)
    lines.extend(body or [indent + 'pass'])
    if head_cond:
        lines = [f'if {head_cond}:{_cond_note(head)}'] + [
            '    ' + ln if ln.strip() else '' for ln in lines]
    return lines


def _render_block_body(beats_: list, prefix: str, audit, trace: list = None) -> list:
    """
    一串拍 → 已带 `prefix` 的行（**循环体 / 选项体共用的那一步**）。

    三种单元各自递归：菜单走 `render_menu_block`、循环走 `render_loop_block`、
    其余单拍走 `_indent_beat_lines`（它保住**拍内部**的相对缩进，比如条件拍的
    块体要更深一层）。与 label 那一层是同一套单元划分（`split_beat_units`），
    所以"循环里套菜单、菜单里套循环"不需要任何额外代码。
    """
    out: list = []
    for kind, unit in split_beat_units(beats_):
        if kind == 'menu':
            out.extend(prefix + ln if ln.strip() else ''
                       for ln in render_menu_block(unit, indent='    ',
                                                   audit=audit, trace=trace))
        elif kind == 'loop':
            out.extend(prefix + ln if ln.strip() else ''
                       for ln in render_loop_block(unit, indent='    ',
                                                   audit=audit, trace=trace))
        else:
            out.extend(_indent_beat_lines(unit[0], prefix, audit, trace))
    return out


def _stage_op_covered(code, op, beat, show_parts, hide_parts) -> bool:
    """
    Stage 里的这条指令，`beat` 的**结构化字段是不是已经承载**了它？

    这是"互为投影"契约的落点。Stage 与结构化列是同一条流水线的两段输入：
    `hydrate_from_dsl` 会把 Stage 里的 `scene` / `show` / `hide` 吸进结构化
    字段，于是渲染时结构化通道先出一次、Stage 尾段再出一次 —— **双写**。

    原来只靠"输出字符串完全相等"去重（`emitted_stage`）：中文化写法
    （`[显示] 凌跃捷 开心 at f11`）与原生写法生成出来的串不完全一样时，
    这道防线就失效。这里改成**按语义比对**：把这条指令规范化成与结构化
    字段**同一套片段编码**（`show_item_parts` / `hide_item_parts`），
    在结构化那一侧找得到就算"已承载"。

      · 真重复 → 只由结构化通道输出一次（不再双写）
      · Stage 独有的东西（`pause`、认不出的指令）→ 仍由 Stage 输出（不丢）

    **认不出动词、或片段解析不出来的一律不跳过**：宁可多出一行，
    也不能吞掉作者手写的指令。
    """
    text = code if code and not str(code).startswith('#') else (op.raw or '')
    head = str(text).split(None, 1)
    verb = head[0] if head else ''
    rest = head[1].strip() if len(head) > 1 else ''
    if verb == 'show':
        parts = show_item_parts(rest)
        if parts and parts <= show_parts:
            return True
        # 原生 `show X at Y`：`_split_show_items` 会把 `at` 归成"复杂子句"、
        # 编码成整串，而结构化列里存的是 `X@Y`。两种编码都试一次。
        spec, at, with_ = _parse_native_stage_tail(rest)
        if spec and not with_:
            part = f'{spec}@{at}' if at else spec
            if part in show_parts:
                return True
        return False
    if verb == 'hide':
        parts = hide_item_parts(rest)
        return bool(parts) and parts <= hide_parts
    if verb == 'scene':
        spec, at, _with = _parse_native_stage_tail(rest)
        return bool(spec) and spec == (beat.scene or '').strip() \
            and at == (beat.scene_at or '').strip()
    return False



def _resolver_for(audit, name_resolver=None):
    """
    挑一个"图像名能不能解析"的判定函数。

    有符号表就用 `Symbols.image_spec_ok`——它比 `image_ok`（校验用）宽松：
    只要 `tag` 存在，`show ling base_arms_akimbo close_happy` 就算能跑，
    于是生成时**保留作者写法**，不会因为"资源表里没有这个属性组合"
    就被改写成下划线形态。判定结果决定写 `show eileen happy` 还是
    `show eileen_happy`——对 `tag attribute` 命名的项目，前者才跑得起来。
    """
    if name_resolver is not None:
        return name_resolver
    symbols = getattr(audit, 'symbols', None)
    if symbols is None:
        return None
    return lambda name: bool(symbols.image_spec_ok(name))



def _stage_lines(beat, name_resolver) -> dict:
    """
    结构化列 → 按 verb 分组的舞台指令行 `{'scene': [...], 'show': [...], 'hide': [...]}`。

    拆出来是为了让**单条 verb** 能被独立渲染（见 `_render_stage_from_fields`
    的 `verbs` 参数）——否则拍内 `scene → play → show` 的交错保不住。
    """
    preserve = bool(getattr(beat, '_from_script', False))

    # 作者在 Stage 列手写的**原生真迹**：结构化列里那份只是 hydrate 留下的
    # 影子（供舞台快照/校验用），**不能拿它自己的编码再渲染一遍** —— 出脚本时
    # 真迹由 `native_raw` 逐字输出（不做下划线改写，见 `test_roundtrip`）。
    #
    # ⚠ **为什么挂在条目上、而不是留给"舞台尾段"统一出**（2026-10-05 修 O2）：
    # 尾段是"所有 verb 之后"的一整块，而原生指令有**自己的 verb 位**。
    # `show A / show B with D（原生）/ hide A` 里，若 B 拖到尾巴上就会排到
    # `hide` 后面 —— 收尾画面不同（该淡出的人还站在台上）。
    # 上游 `beat_build._absorb_native` 因此把原文挂在条目上（`native_raw`），
    # 这里在**它自己的位置**逐字出；尾段再出一次会被 `emitted_stage` 去掉。
    native_show = getattr(beat, '_stage_native_show', None) or frozenset()
    native_hide = getattr(beat, '_stage_native_hide', None) or frozenset()
    native_scene = getattr(beat, '_stage_native_scene', None)
    native_scene_raw = str(getattr(beat, '_stage_native_scene_raw', '') or '')
    #: `(verb, 影子编码) → 原文`：原生指令的**原文**要让它排在自己的 verb 位上。
    native_raw = getattr(beat, '_stage_native_raw', None) or {}

    def art(name):
        return name if preserve else _emit_image_name(name, name_resolver)

    scene_is_native = bool(beat.scene) and native_scene == (
        (beat.scene or '').strip(), (beat.scene_at or '').strip())
    scene_line = ''
    if beat.scene and not scene_is_native:
        scene_line = f"scene {art(beat.scene)}"
        if beat.scene_at:
            scene_line += f" at {beat.scene_at}"
    elif native_scene_raw:
        scene_line = native_scene_raw

    #: 同一个 `native_raw` 可能被拆成多条影子（一格多指令那种），原文只许出一次。
    seen_native: set = set()

    if native_raw:
        def _native_line(verb, part):
            """这一条影子有没有对应的**原文**？有就记下来（只出一次）。"""
            raw = str(native_raw.get((verb, part)) or '').strip()
            if raw and raw not in seen_native:
                seen_native.add(raw)
                return raw
            return ''

    show_lines = []
    for item in _split_show_items(beat.show):
        part = show_item_part(item)
        if native_raw:
            raw = _native_line('show', part)
            if raw:
                show_lines.append(raw)
                continue
            if ('show', part) in native_raw:
                continue                     # 这条影子的原文已经出过了
        if item.get('clause'):
            if part in native_show:
                continue
            show_lines.append(f"show {item['clause']}")
            continue
        if part in native_show:
            continue
        if item.get('is_expression'):
            # `show expression holo("x") at i11 as q`：表达式本体**逐字**输出
            # （它是 Python 代码，任何"改名"都是改坏），位置与 `as` 照结构化
            # 字段出。`as` 不写的话原本挂在这个别名上的存档 / 演出会错位。
            line = f"show expression {item['image']}"
            if item.get('as_'):
                line += f" as {item['as_']}"
            at = (item.get('at') or '').strip() or (beat.at or '').strip()
            if at:
                line += f" at {at}"
            show_lines.append(line)
            continue
        line = f"show {art(item['image'])}"
        at = (item.get('at') or '').strip() or (beat.at or '').strip()
        if at:
            line += f" at {at}"
        show_lines.append(line)

    hide_lines = []
    for item in _split_hide_items(beat.hide):
        part = hide_item_part(item)
        if native_raw:
            raw = _native_line('hide', part)
            if raw:
                hide_lines.append(raw)
                continue
            if ('hide', part) in native_raw:
                continue                     # 这条影子的原文已经出过了
        if part in native_hide:
            continue
        line = f"hide {art(item['image'])}"
        if item.get('with'):
            line += f" with {item['with']}"
        hide_lines.append(line)

    # 转场挂在该拍最后一条舞台指令上（有背景就挂背景，否则挂最后一条立绘，
    # 都没有就单独成行）——Ren'Py 里 `scene X with D` 与
    # `show X with D` 是最常见的两种写法。
    #
    # 背景若是**原生真迹**（Stage 尾段会逐字输出 `scene … with D`），
    # 这里就不能再挂一次，否则同一拍出现两条转场。
    #
    # **还要防"这句已经带了同一个 with"**（2026-10-05 实测）：
    # 复杂子句形态的 show（`clause='bgcafe with dissolve'`）本身就把
    # 转场带在句子里，而 `beat.with_` 可能同时也是 `dissolve`
    # （`_show_clause_args` 把它报了出来）。两个来源各写一次，产物就成了
    # `show bgcafe with dissolve with dissolve` —— 语法错误。
    # 判据是"最后那条 show 行是否已经以 `with <同一个转场>` 收尾"。
    # **原生尾段里已经带了同一个转场吗**（2026-10-05 修，真实项目 17 处）：
    # 作者写在 `Stage` 列的那一行（如 `show bg bg2_1 with dissolve`）会**逐字**
    # 输出一次，而 `beat.with_` 同时也等于 `dissolve` —— 这里若再按"有背景就挂
    # 背景"的启发式挂一次，产物就成了
    #     scene bg bg1_1 with dissolve      ← 多出来的一条，语义还不一样
    #     show bg bg2_1 with dissolve
    # 判据用**原文**（`beat.stage` 是作者写的真迹），不看结构化列。
    native_with = bool(beat.with_) and bool(re.search(
        r'\bwith\s+' + re.escape(str(beat.with_)), str(beat.stage or '')))
    if beat.with_ and not scene_is_native and not native_with:
        already = (show_lines
                   and show_lines[-1].rstrip().endswith(
                       f' with {beat.with_}'.rstrip()))
        if already:
            pass
        elif scene_line:
            scene_line += f" with {beat.with_}"
        elif show_lines:
            show_lines[-1] += f" with {beat.with_}"
        elif not hide_lines:
            show_lines.append(f"with {beat.with_}")
    return {'scene': [scene_line] if scene_line else [],
            'show': show_lines, 'hide': hide_lines}


def _emit_stage_verbs(beat, stage_lines, verbs, emit, emitted=None) -> None:
    """按 `verbs` 顺序输出舞台行（每 verb 内部保持原顺序）。"""
    for v in verbs:
        for ln in stage_lines.get(v, ()):
            if emitted is not None:
                emitted.add(ln)
            emit(ln, '舞台', ln)


def _emit_stage_token(stage_lines: dict, verb: str, idx, emit,
                      emitted: set = None, emitted_idx: set = None) -> None:
    """
    一条顺序 token → 舞台行（缺口 4 的落点）。

    分两种 token：

      · **有下标**（`show#1`，新表）：只出**那一条**（下标从 0 数）；
      · **没下标**（`show`，老表 / 补齐）：把这个 verb **还没出过的**行
        整组出掉（老行为：`show,hide` 这种 verb 级顺序仍然成立）。

    ⚠ **去重必须按"下标"、不能按"内容"**（第一版就是按内容的，于是
    `show ling happy` 写两遍只出一条）：

      · `emitted_idx` = **这一条有没有出过**（`(verb, 下标)`）——
        逐条 token 与兜底那一趟都看它，所以同一条不会出两遍；
      · `emitted` = **这一行的文本有没有出过** —— 只给**舞台尾段**用
        （`_render_stage_tail` 按文本跳过"结构化通道已经出过的那一行"，
        否则原生影子会被写两遍）。

    两个集合各管一件事：一个防"同一条出两遍"，一个防"同一个文本出两遍"。
    合成一个就会出现"作者写两遍同样的 show，工具替他删一条"。
    """
    lines = list((stage_lines or {}).get(verb, ()) or ())
    if not lines:
        return
    if idx is not None and 0 <= int(idx) < len(lines):
        targets = [int(idx)]
    else:
        targets = [i for i in range(len(lines))
                   if emitted_idx is None or (verb, i) not in emitted_idx]
    for i in targets:
        if emitted_idx is not None:
            if (verb, i) in emitted_idx:
                continue
            emitted_idx.add((verb, i))
        ln = lines[i]
        if emitted is not None:
            emitted.add(ln)
        emit(ln, '舞台', ln)


def _emit_leftover_stage(stage_lines: dict, emit, emitted: set = None,
                         emitted_idx: set = None) -> None:
    """**兜底**：还有没出过的舞台行就补在末尾（见 `render_beat_to_rpy`）。"""
    for verb in ('scene', 'show', 'hide', 'with'):
        for i, ln in enumerate((stage_lines or {}).get(verb, ()) or ()):
            if emitted_idx is not None:
                if (verb, i) in emitted_idx:
                    continue
                emitted_idx.add((verb, i))
            if emitted is not None:
                emitted.add(ln)
            emit(ln, '舞台', ln)


def _render_stage_from_fields(beat, emit, name_resolver, audit,
                              emitted: set = None, verbs=None) -> None:
    """
    结构化列 → Ren'Py 舞台指令（顺序按 StageOrder 还原）。

    列与指令的对应关系（就是「使用说明」里写给策划的那张表）：

        背景列   `bg room` / `bg room@bgfit`     → scene bg room [at bgfit]
        立绘列   `ling happy` / `ling happy@f11` → show ling happy [at f11]
        位置列   `left`                          → 没写 @ 的立绘统一用它
        转场列   `dissolve`                      → 挂在该拍最后一条舞台指令上
        退场列   `ye` / `ye@dissolve`            → hide ye [with dissolve]

    从 .rpy 解析来的 Beat（`_from_script`）**逐字还原图像名**：作者的原文
    就是能跑的那份，不拿资源表的解析结果去改写它。

    `verbs` 非空时**只输出列出的 verb**（调用方按拍内源顺序逐个调用），
    这是保住 `scene → play → show` 这类**同类内交错**的关键；
    为空则维持历史行为（一次输出全部，按 stage_order）。
    """
    stage_lines = _stage_lines(beat, name_resolver)

    if verbs is not None:
        _emit_stage_verbs(beat, stage_lines, verbs, emit, emitted)
        return

    order = [v for v in (beat.stage_order.split(',')
                         if beat.stage_order else []) if v in stage_lines]
    for v in ('scene', 'show', 'hide'):
        if v not in order:
            order.append(v)
    _emit_stage_verbs(beat, stage_lines, order, emit, emitted)



def _render_audio_from_fields(beat, emit, audit=None) -> None:
    """
    音频字段 → play/queue/stop 语句。

    `audit` 非空时用它把**资产 ID 翻成真实文件路径**（`spring` →
    `"bgm/spring.mp3"`），并把查不到的引用写成 `# TODO: 缺少资源 …`
    而不是生成一条播不响的语句。
    """
    for channel, name, params, action, label in (
            ('music', beat.music, beat.music_params, beat.music_action, '音乐'),
            ('sound', beat.sound, beat.sound_params, beat.sound_action, '音效')):
        if not name and action not in ('stop', 'queue'):
            continue
        if action == 'stop':
            line = f"stop {channel}"
        elif action == 'queue':
            ref = _resolve_audio_ref(audit, channel, name)
            if ref is None:
                hint = _suggest_missing_ref(audit, channel, name)
                extra = f'（是不是想写 {hint!r}？）' if hint else ''
                emit(f"# TODO: 缺少资源 {label} '{name}'{extra}",
                     label, name, 'todo')
                continue
            line = f"queue {channel} {ref}"
        else:
            ref = _resolve_audio_ref(audit, channel, name)
            if ref is None:
                hint = _suggest_missing_ref(audit, channel, name)
                extra = f'（是不是想写 {hint!r}？）' if hint else ''
                emit(f"# TODO: 缺少资源 {label} '{name}'{extra}",
                     label, name, 'todo')
                continue
            line = f"play {channel} {ref}"
        if params:
            line += f" {params}"
        emit(line, label, name or action)



def _base_stem(text: str) -> str:
    """`bgm/夜晚，断电之后…….mp3` → `夜晚，断电之后`（去目录、去扩展名、去结尾省略号）。"""
    text = str(text or '').strip().replace('\\', '/')
    if '/' in text:
        text = text.rsplit('/', 1)[1]
    text = re.sub(r'\.[A-Za-z0-9]{1,5}$', '', text)
    return re.sub(r'[….\s]+$', '', text).strip()


def _suggest_missing_ref(audit, channel: str, name: str) -> str:
    """
    "缺少资源"时给一句**可操作**的猜测：是不是想写这一条？

    真实场景（`chapter1_4.rpy` 的 7 条音乐 TODO）：脚本里写的是
    `bgm/夜晚，断电之后…….mp3` —— 文件名被手写截断了（带省略号）。
    **资产其实就在盘里**，叫 `夜晚，断电之后，不存在的教室.mp3`。
    只报"缺少资源"会让人去找一张根本不缺的曲；给出唯一的前缀匹配，
    一眼就能把脚本里的名字改对。

    只报**唯一**候选，含糊就不猜（宁可不建议，也绝不指错一条）。
    """
    import difflib
    symbols = getattr(audit, 'symbols', None) if audit is not None else None
    reg = getattr(symbols, 'registry', None)
    if reg is None or not channel:
        return ''
    from storygraph.core.vocabulary import ASSET_TYPE_ATTR
    attr = ASSET_TYPE_ATTR.get(channel)
    rows = getattr(reg, attr, {}) if attr else None
    if not rows:
        return ''
    want = _base_stem(name)
    if not want:
        return ''
    best, best_score = '', 0.0
    for _aid, row in rows.items():
        row = row or {}
        cand = _base_stem(row.get('Path') or '') or _base_stem(row.get('ID') or '')
        if not cand:
            continue
        if cand == want:
            return ''                       # 一模一样：那不该判成缺
        score = difflib.SequenceMatcher(None, want, cand).ratio()
        if cand.startswith(want) or want.startswith(cand):
            score = max(score, 0.9)         # 前缀关系（省略号截断的典型形态）
        if score > best_score:
            best, best_score = cand, score
        elif score == best_score:
            best = ''                       # 并列 → 不猜
    return best if (best and best_score >= 0.72) else ''


def _resolve_audio_ref(audit, channel: str, name: str):
    """音频引用文本；audit 判为"确实找不到"时返回 None。"""
    if not name:
        return None
    if audit is None:
        return _audio_ref(name)
    from storygraph.story.dsl import TODO as _TODO
    if audit.audio(channel, name) == _TODO:
        return None
    return _audio_ref(audit.audio_ref(channel, name))




def _normalize_image_name(name: str) -> str:
    """
    渲染时把图像名里的空格换成下划线，与资产表的 ID 对齐。

    Ren'Py 的 `show bg 000` 与 `show bg_000` 等价（引擎内部就是这个规则），
    但资产表里存的是文件名 `bg_000`。统一成下划线后，脚本与表可以
    直接对得上，引用分析也不用再猜。
    """
    name = (name or '').strip()
    if not name or '"' in name or "'" in name:
        return name
    return name.replace(' ', '_')



def _inline_note(beat) -> str:
    """
    这一拍的行尾注释要不要贴、贴成什么样（C 阶段）。

    判据**故意严**：

      · 拍本身只能渲染出**一行**（贴到多行拍的第一行会变成"整个块"的注释）；
      · 注释本身得是单行、且以 `#` 开头（词法器给的原文）。

    "源语句是不是单独一行"这件事**不在这里判** —— 它由词法器在采集时就定死了
    （`_inline_comment` 对多行逻辑语句返回空），所以列里有值 = 那一行是单行。
    第一版在这里又查了 `src_first == src_last`，结果**表那条路全丢**：
    行号是运行期元数据、不进表（`from_row` 之后全是 0）。

    返回 `'  # …'`（可直接拼到行尾）或 `''`（不贴）。
    """
    note = str(getattr(beat, 'inline_comment', '') or '').strip()
    if not note.startswith('#'):
        return ''
    if '\n' in note or '\r' in note:
        return ''
    return '  ' + note


def _cond_note(beat) -> str:
    """这一拍的**条件行**（`if …:`）要贴的注释（来自 `if/elif/else` 头那一行）。"""
    note = str(getattr(beat, 'cond_comment', '') or '').strip()
    if not note.startswith('#') or '\n' in note or '\r' in note:
        return ''
    return '  ' + note


def _quote(text: str) -> str:
    """Ren'Py 字符串字面量。

    三类字符必须转义，缺一类就会写出**语法错**的 .rpy：

      · `\\` 和 `"` —— 字符串字面量的老规矩；
      · **`\\n` / `\\t` / `\\r`** —— 值里这些是**真的**换行/制表符
        （解析 .rpy 时已把 `\\n` 这类转义还原成了真字符），原样写出去
        就会变成**物理换行**，把一条 say 断成两行半。

    第三类是 2026-10-05 补的（地狱测试 `tools/hell_test.py` 的
    `adv_backslash` 抓到）：`e "路径 C:\\new\\test"` 往返后变成

        e "路径 C:
        ew\\test"

    ——产物直接不可解析。Ren'Py 里 `\\n` 是常用写法（强制换行），
    所以这不是边缘情况。
    """
    if text is None:
        return '""'
    escaped = (str(text)
               .replace('\\', '\\\\')
               .replace('\n', '\\n')
               .replace('\r', '\\r')
               .replace('\t', '\\t')
               .replace('"', '\\"'))
    return f'"{escaped}"'



#: 公共别名：**跨包使用就是契约**（`pipeline.renpy_exporter` 的"控制流导出"
#: 也要把字符串写成 Ren'Py 字面量）。私有名跨包 import 是被禁止的
#: （`tests/test_package_layering.py`），所以这里声明一份公开名 ——
#: 与 `parsing.renpy_parser.image_path_from_expr` 同一套做法。
#:
#: 为什么必须共用（2026-10-05，O4 取证）：控制流导出自己手写
#: `f'"{opt}"'`，于是选项文案里带 `"` 时写出 `"他说："你好"":`（语法错）、
#: 带换行时写出**跨物理行**的字符串（产物不可解析）。转义规则只有一份，
#: 两个生成器都得用这一份。
quote_string = _quote


def collect_used_transforms(beats: list) -> list:
    """
    收集演出表里引用的自定义变换（排除 Ren'Py 内置）。

    来源：At 列、Show 复杂子句里的 `at xxx`、Transform/With 列。
    这些 ID 若在 transforms.xlsx 里有 ATL 定义，导出时就会生成
    `transform xxx:` 代码块（改进文档第四节）。
    """
    used = set()

    def _add(name):
        name = (name or '').strip()
        if name and name not in RENPY_BUILTIN_TRANSFORMS:
            used.add(name)

    for b in beats:
        for t in (b.at or '').split():
            _add(t)
        for item in _split_show_items(b.show):
            if item['clause']:
                m = re.search(r'\bat\s+([\w.]+)', item['clause'])
                if m:
                    _add(m.group(1))
            else:
                for t in (item['at'] or '').split():
                    _add(t)
        _add(b.transform)
        _add(b.with_)
        for item in _split_hide_items(b.hide):
            _add(item['with'])
    return sorted(used)



def render_transform_definitions(registry, used_ids) -> list:
    """
    为用到的自定义变换生成 `transform id(params):` + ATL 体。

    transforms.xlsx 的约定列（可选）：
      · ATL       ATL 定义体（多行文本），如 "xalign 0.0\\nlinear 0.5 xalign 1.0"
      · Params    参数列表，如 "duration=0.5, distance=200"
      · Category  分类（入场/退场/强调…），仅用于分组，不参与生成

    没有 ATL 的 transform 不生成（可能由项目别处定义）。
    """
    if registry is None:
        return []
    transforms = getattr(registry, 'transforms', {}) or {}
    blocks = []
    for tid in used_ids:
        row = transforms.get(tid)
        if not row:
            continue
        atl_lines = [ln.rstrip() for ln in (row.get('ATL') or '').splitlines()]
        while atl_lines and not atl_lines[0].strip():
            atl_lines.pop(0)
        while atl_lines and not atl_lines[-1].strip():
            atl_lines.pop()
        if not atl_lines:
            continue

        # 整体去掉公共缩进再统一缩进 4：ATL 可能是"从脚本里解析出来的
        # （自带 4 空格）"，也可能是"人手填进 Excel 的（没有缩进）"。
        # 直接 strip 会把第一行的缩进吃掉，导致块体第一行顶格、后面缩进——
        # 生成出来的 transform 直接是语法错误。
        indents = [len(ln) - len(ln.lstrip())
                   for ln in atl_lines if ln.strip()]
        base = min(indents) if indents else 0

        # Doc 与 ATL 首个字符串重复时不再重复输出注释
        first_str = ''
        for ln in atl_lines:
            s = ln.strip()
            if not s or s.startswith('#'):
                continue
            if len(s) >= 2 and s[0] == s[-1] and s[0] in '"\'':
                first_str = s[1:-1]
            break

        doc = (row.get('Doc') or '').strip()
        if doc and doc != first_str:
            for ln in doc.splitlines():
                if ln.strip():
                    blocks.append(f"# {ln.strip()}")
        params = (row.get('Params') or '').strip()
        blocks.append(f"transform {tid}({params}):" if params
                      else f"transform {tid}:")
        for ln in atl_lines:
            if ln.strip():
                blocks.append('    ' + ln[base:])
        blocks.append('')
    return blocks



def make_symbols(registry=None, keywords=None):
    """从资源表造符号表（编译器查转译用的字典）。"""
    from storygraph.story.dsl import Symbols
    return Symbols(registry, keywords=keywords)



def _is_file_block_carrier(beat) -> bool:
    """
    这张拍**只**用来携带文件级保真块吗（自己没有演出内容）？

    见 `render_beats_to_rpy` 里"只用来携带的拍不产生 label"那段：
    纯 `init python:` 的文件没有 label，产物里也不该凭空多一个
    空壳 `label start: pass return`。
    """
    if not getattr(beat, 'file_blocks', ''):
        return False
    checker = getattr(beat, 'has_content', None)
    return (not checker()) if callable(checker) else False


def _strip_generator_marker(text: str) -> str:
    """把**生成标记行**从一段文件级原文里摘掉（其余行逐字不动）。"""
    from storygraph.core.vocabulary import GENERATOR_MARKER
    return '\n'.join(ln for ln in str(text or '').split('\n')
                     if ln.strip() != GENERATOR_MARKER)


def _collect_file_blocks(beats: list) -> list:
    """
    从拍列表里收集**文件级保真块**（去重、保序）。

    为什么要去重：块挂在第一张拍上（见 `beat_build.attach_file_blocks`），
    而同一个 Beat 列表可能被渲染多次、也可能有人手工复制那一行 ——
    重复写出 `init python:` 会**重复定义**（后者覆盖前者，行为可能变）。
    内容相同就只写一次；内容不同则如实按顺序都写（那是两份定义）。
    """
    from storygraph.story.beat_model import split_file_blocks

    out, seen = [], set()
    for b in beats or ():
        for part in split_file_blocks(getattr(b, 'file_blocks', '')):
            key = part.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(part)
    return out


def render_beats_to_rpy(beats: list, label_name: Optional[str] = None,
                        indent: str = '    ', registry=None,
                        symbols=None, trace: list = None,
                        include_transforms: bool = True,
                        pose_hides: dict = None) -> str:
    """
    Beat 列表 → 完整 .rpy 文本。

    label_name 为空时按 Beat.label 分段生成多个 label；
    指定了则全部塞进同一个 label（便于替换单个章节）。

    registry / symbols 非空时会：
      · 把演出表用到的自定义变换（transforms.xlsx 里带 ATL 的）生成
        `transform` 定义前置到文件头，让产物自包含；
      · 建立符号表，把 `[显示] 凌跃捷 开心 左` 翻成 `show ling close_happy at left`；
      · 找不到资源就写 `# TODO: 缺少资源 …`，**不报错、不中断**。

    `include_transforms=False`：**不写 transform 定义段**（只留文件头 + label）。
    给"项目里已经有 definitions.rpy 定义过这些 transform"的场合用 ——
    否则退回全量重生成会把同一批定义**重复写进章节文件**（用户 2026-10-05 实测：
    `chapter1_1` 151 行、`chapter3_1` 473 行差异主要就是这一段）。

    `pose_hides`（P4）：`{拍序号: ['hide ye', …]}` —— 由
    `story.pose_patch.plan_pose_hides()` 算出，**只在项目声明了
    `hide_before_pose_switch` 时**才传进来。补的 `hide` 写在该拍**自己的
    内容之前**，因此它仍然是"生成内容"，照旧走增量补丁的校验与备份。
    """
    from storygraph.story.dsl import Audit

    pose_hides = dict(pose_hides or {})

    if symbols is None and registry is not None:
        symbols = make_symbols(registry)
    audit = Audit(symbols) if symbols is not None else None

    out = [GENERATOR_MARKER]

    if registry is not None and include_transforms:
        used = collect_used_transforms(beats)
        defs = render_transform_definitions(registry, used)
        if defs:
            out.append('')
            out.append('# —— 自定义 transform（来自 transforms.xlsx）——')
            out.extend(defs)

    out.append('')

    # —— 文件级保真块（顶层 `init python:` 之类）——
    #
    # 2026-10-05，拓展边界③：这些块不属于任何"拍"，全量重生成本来会把它们
    # **整块丢掉**（真实项目 `whackamole.rpy` 实测丢 56 行游戏逻辑，而产物
    # 语法完全合法、门禁拦不住）。它们现在随拍带到这儿，在**所有 label
    # 之前**原样写出 —— 与源文件里"定义在前、label 在后"的顺序一致。
    file_texts = _collect_file_blocks(beats)
    for text in file_texts:
        # **保险**：把生成标记从文件级块里摘掉（开头已经加了一条）。
        # 为什么两道都要有：源头那道管"新解析的文件"，这道管**老表**里的
        # `FileBlocks`（那是上一版工具存的、里面可能已经带着标记）。
        # 只做源头的话，一张旧表导出的产物还是两条。
        text = _strip_generator_marker(text)
        if not text.strip():
            continue
        out.append(text)
        out.append('')

    groups = []
    if label_name:
        groups.append((label_name, list(beats)))
    else:
        current = None
        bucket = []
        for b in beats:
            name = b.label or 'start'
            if current is None:
                current = name
            if name != current:
                groups.append((current, bucket))
                current, bucket = name, []
            bucket.append(b)
        if bucket:
            groups.append((current, bucket))
        if not groups and label_name:
            groups.append((label_name, []))
        #
        # **一张拍都没有、也没指定 label 名 → 不产生任何 label**
        # （2026-10-05 修，`tools/probe_label_fate_xlsx.py` 抓到）。
        #
        # 旧写法是 `groups.append(('start', []))` —— 凭空造一个 label。
        # 真实项目里 6 个**纯定义文件**中招（`transform.rpy` /
        # `whackamole_images.rpy` / `generated_ui.rpy` / `plottree.rpy` /
        # `script0.rpy` / `script1.rpy`）：它们只有 `transform` / `image` /
        # `init python`，**源文件里根本没有 label**，产物里却各多出一个
        #
        #     label start:
        #         pass
        #         return
        #
        # 而 `start` 是 Ren'Py 的游戏入口 —— 这些产物一旦落进 `game/`，
        # 就是**重复 label，游戏起不来**。
        # 一个只有定义的文件**没有 label** 才是对的（Ren'Py 允许）。
        #
        # 注意这条与上面"空拍保住 label 名"是同一枚硬币的两面：
        # 源文件里**有**的 label 必须留下来，**没有**的绝不能凭空造。

    emitted_labels = 0
    #: 拍对象 → 原始下标（P4 的 `pose_hides` 按**拍序号**给）
    idx_of = {id(b): i for i, b in enumerate(beats or ())}
    for name, group in groups:
        # **只用来携带文件级块的拍不产生 label**（2026-10-05，拓展边界③）。
        #
        # 纯 `init python:` 的文件会造一张空拍来携带代码（见
        # `beat_build.attach_file_blocks`）。照常分组的话，产物里会多出一个
        # **空壳** `label start: pass return` —— 而源文件里根本没有 label。
        #
        # 为什么这个空壳不好：把一个纯定义文件导出到 `game/`（`--out` 指到那）
        # 时，它会与项目里真正的 `label start` 重名，Ren'Py 直接起不来
        # （`LABEL_COLLISION` 门禁能拦住这一种，因为目标在 game/ 里）。
        # 一个只做定义的文件**没有 label** 才是对的。
        #
        # ⚠ 实测口径（别把这条当成"防住了合并构建重名"）：全项目合并时
        # 这些块通常挂在**第一张真拍**上（`attach_file_blocks`），
        # 合成拍根本不出现 —— 真实项目产物 46 个 label / 0 处重名
        # （`tools/check_duplicate_labels.py`）。
        if group and all(_is_file_block_carrier(b) for b in group):
            continue
        out.append(f"label {name}:")
        emitted_labels += 1
        body = []
        # 菜单要按**块**渲染（`split_beat_units` 见模块里"菜单块"那一节）：
        # 逐拍渲染会把 `menu:` 整块压成顺序语句，分支结构静默损坏。
        for kind, unit in split_beat_units(group):
            # P4：这一拍缺的 `hide`（改回空姿势要先隐藏）——
            # 放在**这一拍自己的内容之前**，所以它是"生成内容"，
            # 增量补丁照旧会校验它、备份它。
            _hides = []
            for _b in (unit or ()):
                for _line in (pose_hides.get(idx_of.get(id(_b), -1), ())
                              or ()):
                    if _line and _line not in _hides:
                        _hides.append(_line)
            if _hides:
                body.extend(_hides)
            if kind == 'menu':
                body.extend(render_menu_block(unit, indent=indent,
                                              audit=audit, trace=trace))
            elif kind == 'loop':
                body.extend(render_loop_block(unit, indent=indent,
                                              audit=audit, trace=trace))
            else:
                body.extend(render_beat_to_rpy(unit[0], indent=indent,
                                               audit=audit, trace=trace))
        # **只去掉首尾的空行，中间的空行留着**（2026-10-05 修）。
        #
        # 旧写法是 `body = [ln for ln in body if ln.strip() != '']` ——
        # 把**所有**空行都扔了。那对拍之间的空行无所谓（纯排版），
        # 但对 `python:` 块内的空行是**实打实的内容丢失**：
        # 实测 `mini_game_puzzle.rpy` 的多行调用里 `cols=3,` 与
        # `grid_width=800,` 之间有一个空行，它被扔掉之后往返的逐行文本
        # 就对不上（审计算一条丢失），而且作者手写的排版被改掉了。
        #
        # 为什么不能干脆全留：`render_beat_to_rpy` 的返回值里，
        # 拍与拍之交可能有空行，全留会在 label 里堆出很多空行
        # （每个拍之间一个），产物会很散。所以只trim首尾。
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        if body:
            out.extend(indent + ln if ln.strip() else '' for ln in body)
        else:
            out.append(indent + 'pass')
        out.append(indent + 'return')
        out.append('')

    # 兜底 label 的判据（2026-10-05 收紧，`tools/probe_label_fate_xlsx.py`
    # 抓到）：**只有"有真拍却一个 label 都没输出"时才补**。
    #
    # 旧判据是 `not emitted_labels and not file_texts` —— 于是
    # **一张拍都没有**的纯定义文件也会得到 `label start: pass return`。
    # 真实项目 6 个文件中招（`transform.rpy` / `whackamole_images.rpy` /
    # `generated_ui.rpy` / `plottree.rpy` / `script0.rpy` / `script1.rpy`）：
    # 它们解析出 **0 条语句、0 个文件级块**（`transform` / `image` 定义
    # 还没进任何保真通道），产物里却各多出一个 `label start` ——
    # 而 `start` 是 Ren'Py 的游戏入口，落进 `game/` 就是**重复 label**。
    #
    # 源文件里**没有** label 的文件，产物就该**没有** label。
    # 与"空拍保住 label 名"（`beat_build._fill_*`）是同一枚硬币的两面。
    if (not emitted_labels and beats
            and any(not _is_file_block_carrier(b) for b in beats)):
        # 有拍、但它们的 Label 列是空的（Excel 里新建的表常这样）
        # → 照旧给一个能跑的兜底 label（没有这一条，产物没有入口）。
        out.append('label start:')
        out.append(indent + 'pass')
        out.append(indent + 'return')
        out.append('')

    return '\n'.join(out).rstrip() + '\n'
