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
import re
from typing import Optional
from storygraph.assets.asset_registry import RENPY_BUILTIN_TRANSFORMS
from storygraph.core.statement import is_tight_dollar, strip_marker

from storygraph.story.beat_model import (
    Beat,
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
    show_item_part,
    show_item_parts,
)



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

    历史行为是"空格一律换成下划线"（`show bg room` → `show bg_room`），
    因为资产表 ID 是文件名形态。但这个改写有个致命前提：项目里必须有
    `bg_room` 这个名字。对用 Ren'Py 原生 `tag + attribute` 命名
    （`images/eileen/happy.png` → `show eileen happy`）的项目，
    改成 `eileen_happy` 反而把能跑的脚本改坏了。

    所以：给了 resolver（有资源表）时，**优先原样保留作者写的形态**——
    只要它能在资源表里解析出来；解析不了才退回下划线形态（那是文案
    从下拉里选的 ID 形态，本来就带下划线）。没给 resolver 时保持旧行为，
    兼容单测与"无资源表"场景。
    """
    name = (name or '').strip()
    if not name or '"' in name or "'" in name:
        return name
    underscored = name.replace(' ', '_')
    if resolver is None:
        return underscored
    try:
        if name == underscored or resolver(name):
            return name
        if resolver(underscored):
            return underscored
    except Exception:
        return underscored
    return name



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
            out.append('$ ' + s)
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
    _verb_ops = {'scene': lambda: emit_stage_verbs(['scene']),
                 'show': lambda: emit_stage_verbs(['show']),
                 'hide': lambda: emit_stage_verbs(['hide']),
                 'audio': emit_audio, 'voice': emit_voice}

    tokens = [t for t in beat.order_tokens if t in _verb_ops]
    if tokens:
        # 老表/手工新增行可能只记了部分——把漏掉的 stage verb 按 StageOrder
        # 补在后面，**绝不因为顺序信息不全就丢掉操作**。
        missed_stage = [v for v in ('scene', 'show', 'hide')
                        if v not in tokens]
        for v in missed_stage:
            tokens.append(v)
        for k in ('audio', 'voice'):
            if k not in tokens:
                tokens.append(k)
        code_done = False
        for t in tokens:
            _verb_ops[t]()
            if t == 'scene' and not code_done:
                # 代码块跟在**第一条舞台指令**之后（历史位置）
                emit_code()
                code_done = True
        if not code_done:
            emit_code()
        emit_stage_tail()
    else:
        # 没有任何顺序信息（老表 / 直接构造的 Beat）：维持历史固定顺序
        emit_stage()
        emit_code()
        emit_audio()
        emit_voice()

    # ---------------- 台词 ----------------
    speaker = beat.speaker
    if speaker and audit is not None:
        from storygraph.story.dsl import TODO as _TODO
        if audit.character(speaker) == _TODO:
            todo('说话人', speaker, f"缺少资源 角色 '{speaker}'")
            speaker = ''
    if beat.kind == KIND_MENU_OPTION:
        emit(f'"{beat.text}"', '台词', beat.text)
    elif beat.kind == KIND_EXTEND:
        emit(f"extend {_quote(beat.text)}", '台词', beat.text)
    elif beat.text:
        if speaker:
            emit(f"{speaker} {_quote(beat.text)}", '台词', beat.text)
        else:
            emit(_quote(beat.text), '台词', beat.text)
    elif beat.kind == KIND_MENU and beat.text:
        emit(_quote(beat.text), '台词', beat.text)

    if not lines:
        return pre

    if beat.condition:
        return pre + [f"if {beat.condition}:"] + [
            indent + ln for ln in lines
        ]
    return pre + lines



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

#: 菜单选项体"无显式条件"时补的恒真条件（见 `stamp_menu_bodies`）。
#: Ren'Py 里 `if True:` 永远成立，所以补它**不改变任何演出**；
#: 它唯一的作用是让"这拍在菜单块里"变成一个可判定的结构信号。
TRUE_CONDITION = 'True'


def split_beat_units(beats: list) -> list:
    """
    拍序列 → **渲染单元**序列：`[('menu', 菜单组的拍), ('beat', [一拍])]`。

    菜单组从一张 `menu` 拍开始（见 `_take_menu_run` 的边界判据），
    其余每张拍自成一个单元——渲染行为与以前完全一致。
    """
    beats = list(beats or ())
    units = []
    i = 0
    while i < len(beats):
        if beats[i].kind == KIND_MENU:
            run, i = _take_menu_run(beats, i)
            units.append(('menu', run))
            continue
        units.append(('beat', [beats[i]]))
        i += 1
    return units



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
            break
        run.append(b)
        cur_body += 1
        i += 1
    return run, i



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




def stamp_menu_bodies(beats: list) -> int:
    """
    给菜单选项体里**没有条件**的拍补一个恒真条件（`True`），返回补了几拍。

    为什么需要它（P0-1 的最后一环）：菜单组的边界靠 `condition` 兜底判定
    （`_menu_member` 是 `.rpy` 侧的结构位，Excel 里存不下），而"选项无条件
    + 选项体只写跳转"是最常见的写法 ——

        menu:
            "进去":              ← 选项 condition = ''
                jump inner       ← 选项体 condition 也是 ''
        "剧情继续"                ← 块外，condition 还是 ''

    三个空串分不出谁在块里。把选项体里**空条件**的拍写成 `True`
    （**语义等价**，Ren'Py 里 `if True:` 永远成立）之后，条件就成了可判定
    的结构信号：

        选项体（无条件）→ `True`      块外 → 空

    为什么只补空条件（不统一覆盖选项体）：
      · 选项自身有条件的（`"去屋顶" if love >= 0:`），选项体的拍本来就带着
        那个条件（解析器 `_combine` 进去的），边界已经可判定，不必再动；
      · 只补"本来没有信息"的那些，改动面最小。

    两个直接好处：
      · Excel 往返（以及任何丢掉 `_menu_member` 的路径）之后菜单结构还在；
      · 反复往返**收敛**（第二次拿到的 `True` 原样保留，不会再变）。

    为什么不在渲染时就写 `if True:`：渲染会把"与选项同条件的累积条件"
    剥掉（那是解析器的记账，不是真嵌套 `if`），所以 `.rpy → .rpy` 那条路
    完全不需要动内容，少写一层缩进更贴近原文。只在**写表前**补。
    """
    stamped = 0
    for kind, unit in split_beat_units(beats):
        if kind != 'menu':
            continue
        for opt, body in _menu_segments(unit):
            if opt is None:
                continue
            for beat in body:
                if not (beat.condition or '').strip():
                    beat.condition = TRUE_CONDITION
                    stamped += 1
    return stamped



def _last_option(run: list):
    """
    这一组菜单里**当前生效**的选项的条件。

    取最后一张拍的条件（而不是最后一张 `menu_option` 的条件）：
    Excel 回导之后选项体的条件与选项条件相同、甚至被补成 `True`，
    此时"当前选项的上下文条件"就是那个值。取选项自己的条件会让
    "选项无条件"的体永远对不上（两边都是空串时又人人相等）。
    """
    return run[-1] if run else run



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




def _menu_segments(run: list) -> list:
    """
    菜单组 → `[(选项拍 or None, 选项体的拍列表), ...]`。

    按 `menu_option` 切段：一张选项拍之后的拍都归它（直到下一张选项拍）。
    """
    segments = []
    for b in run[1:]:
        if b.kind == KIND_MENU_OPTION:
            segments.append((b, []))
        elif segments:
            segments[-1][1].append(b)
        else:
            # 理论上到不了这里（`_take_menu_run` 从 menu 拍起走），
            # 但真出现时**不能丢**：当成菜单里的杂项拍放在块首。
            segments.append((None, [b]))
    return segments



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
    emit(head, '菜单', caption)

    for opt, body in segments:
        if opt is None:
            # 菜单块里认不出归属的拍（认不出就当普通拍输出，不吞）
            lines.extend(_render_unit_lines(body, opt_indent, audit, trace))
            continue
        own_cond = (opt.condition or '').strip()
        menu_cond = (run[0].condition or '').strip()
        label = opt_indent + _quote(opt.text or '')
        # 选项**自身**的条件（与菜单自身条件重复时不再写第二遍：
        # 解析器对选项体做过 `_combine`，会把菜单自身的条件并进选项条件里）
        if own_cond and own_cond != menu_cond:
            label += f' if {own_cond}'
        label += ':'
        emit(label, '菜单', opt.text or '')

        # 选项体在拍上带的条件是**累积**的（`_combine(外层, 选项条件)`）。
        # 两种情况必须分开处理：
        #   · 正好等于"本选项 + 外层菜单"的累积条件 → 那只是解析器的记账，
        #     块体不该再多一层 `if`，把它**剥掉**再渲染；
        #   · 不等于（真的嵌了一层 `if`）→ 保留，让渲染器出嵌套 `if`。
        expected = _expected_body_condition(own_cond, menu_cond)
        stripped, nested = [], []
        for beat in body:
            got = (beat.condition or '').strip()
            if got and got == expected:
                stripped.append(_with_condition(beat, ''))
            else:
                nested.append(beat)
        body_lines = _render_unit_lines(stripped, opt_indent + '    ',
                                       audit, trace)
        for beat in nested:
            body_lines.extend(opt_indent + '    ' + ln
                              for ln in render_beat_to_rpy(
                                  beat, indent='', audit=audit, trace=trace)
                              if ln.strip())
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
    """
    lines = []
    for kind, group in split_beat_units(beats):
        if kind == 'menu':
            lines.extend(_render_menu_segments(
                group, _menu_segments(group), indent, indent + '    ',
                audit, trace))
        else:
            for ln in render_beat_to_rpy(group[0], indent='', audit=audit,
                                         trace=trace):
                lines.append(indent + ln if ln.strip() else '')
    return lines



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
    """
    lines = _render_menu_segments(run, _menu_segments(run), '', indent,
                                  audit, trace)
    if not lines:
        return []
    return [lines[0]] + [ln for ln in lines[1:]]



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
    # 影子（供舞台快照/校验用），**不能在这里再渲染一遍** —— 出脚本时
    # 真迹由 Stage 尾段逐字输出（不做下划线改写，见 `test_roundtrip`）。
    native_show = getattr(beat, '_stage_native_show', None) or frozenset()
    native_hide = getattr(beat, '_stage_native_hide', None) or frozenset()
    native_scene = getattr(beat, '_stage_native_scene', None)

    def art(name):
        return name if preserve else _emit_image_name(name, name_resolver)

    scene_is_native = bool(beat.scene) and native_scene == (
        (beat.scene or '').strip(), (beat.scene_at or '').strip())
    scene_line = ''
    if beat.scene and not scene_is_native:
        scene_line = f"scene {art(beat.scene)}"
        if beat.scene_at:
            scene_line += f" at {beat.scene_at}"

    show_lines = []
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            if show_item_part(item) in native_show:
                continue
            show_lines.append(f"show {item['clause']}")
            continue
        if show_item_part(item) in native_show:
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
        if hide_item_part(item) in native_hide:
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
    if beat.with_ and not scene_is_native:
        if scene_line:
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



def _quote(text: str) -> str:
    """Ren'Py 字符串字面量。含双引号时用单引号包。"""
    if text is None:
        return '""'
    escaped = str(text).replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'



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



def render_beats_to_rpy(beats: list, label_name: Optional[str] = None,
                        indent: str = '    ', registry=None,
                        symbols=None, trace: list = None,
                        include_transforms: bool = True) -> str:
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
    """
    from storygraph.story.dsl import Audit

    if symbols is None and registry is not None:
        symbols = make_symbols(registry)
    audit = Audit(symbols) if symbols is not None else None

    out = ['# 由剧本表自动生成，请勿在此文件手工改动结构']

    if registry is not None and include_transforms:
        used = collect_used_transforms(beats)
        defs = render_transform_definitions(registry, used)
        if defs:
            out.append('')
            out.append('# —— 自定义 transform（来自 transforms.xlsx）——')
            out.extend(defs)

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
        if not groups:
            groups.append(('start', []))

    for name, group in groups:
        out.append(f"label {name}:")
        body = []
        # 菜单要按**块**渲染（`split_beat_units` 见模块里"菜单块"那一节）：
        # 逐拍渲染会把 `menu:` 整块压成顺序语句，分支结构静默损坏。
        for kind, unit in split_beat_units(group):
            if kind == 'menu':
                body.extend(render_menu_block(unit, indent=indent,
                                              audit=audit, trace=trace))
            else:
                body.extend(render_beat_to_rpy(unit[0], indent=indent,
                                               audit=audit, trace=trace))
        if body:
            body = [ln for ln in body if ln.strip() != '']
        if body:
            out.extend(indent + ln if ln.strip() else '' for ln in body)
        else:
            out.append(indent + 'pass')
        out.append(indent + 'return')
        out.append('')

    return '\n'.join(out).rstrip() + '\n'
