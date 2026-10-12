"""
`renpy_parser` 的**节点与纯 helper**（2026-10-11 从 `renpy_parser.py` 搬出）
=========================================================================

为什么单独一个模块（见 `docs/重构方案_拆分_renpy_parser_2026-10-11.md`）：

`renpy_parser.py` 曾经 4422 行、`RenPyParser` 一个类 50 个方法，
而其中 `_stmt_*` 那一族（27 方法 / 544 行，聚类最干净）引用的 **26 个模块级名字
全都定义在同一文件里** ⇒ 想把那一族提取成 mixin 就会**循环 import**。

所以分两步：**① 先把被引用的这一层（节点类 + 纯 helper）搬出来**（就是本模块），
② 再把方法族提取成 mixin。本模块是第①步的产物。

⚠ 搬家纪律：**逐字照搬**，一个字都没改（连注释与空行）——
这样 `git diff` 一眼能看出"只是搬家"，而不是"顺手改了逻辑"。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from storygraph.core.nest_path import join_nest, option_key
from storygraph.core.statement import (CONTENT_HASH_HEX, Statement,
                                       StatementKind)
from storygraph.core.uid import make_translation_uid


def _find_triple_end(text: str, start: int, delim: str) -> int:
    """
    从 start 开始在 text 中查找未转义的 delim（三引号）。
    返回起始索引，未找到返回 -1。
    """
    i = start
    n = len(text)
    while i < n - 2:
        if text[i] == '\\':
            i += 2
            continue
        if text[i:i + 3] == delim:
            return i
        i += 1
    return -1

def _skip_string(text: str, start: int) -> tuple:
    """
    从 text[start] 处的引号开始，跳过整个字符串。

    返回 (end_index, closed)：
      · closed=True  → end_index 是字符串闭合后的下一个索引
      · closed=False → end_index == len(text)，字符串未闭合
    """
    n = len(text)
    if start < 0 or start >= n:
        return n, True

    ch = text[start]
    if ch not in ('"', "'"):
        return start, True

    if start + 2 < n and text[start:start + 3] in ('"""', "'''"):
        delim = text[start:start + 3]
        j = _find_triple_end(text, start + 3, delim)
        if j == -1:
            return n, False
        return j + 3, True

    i = start + 1
    while i < n:
        if text[i] == '\\':
            i += 2
            continue
        if text[i] == ch:
            return i + 1, True
        i += 1
    return n, False

def _scan_toplevel_colon(text: str, start: int) -> int:
    """
    从 start 开始扫描，返回顶层冒号索引；未找到返回 -1。
    顶层 = 不在字符串内、不在任何括号内。
    """
    i = start
    n = len(text)
    depth = 0
    while i < n:
        ch = text[i]
        if ch == '\\':
            i += 2
            continue
        if ch in ('"', "'"):
            i, _ = _skip_string(text, i)
            continue
        if ch in '([{':
            depth += 1
            i += 1
            continue
        if ch in ')]}':
            depth = max(0, depth - 1)
            i += 1
            continue
        if ch == ':' and depth == 0:
            return i
        i += 1
    return -1

def _scan_balanced_close(text: str, open_idx: int) -> int:
    """
    返回与 text[open_idx] 匹配的右括号索引；未找到返回 -1。
    支持任意深度嵌套与字符串内的括号。
    """
    if open_idx >= len(text) or text[open_idx] not in '([{':
        return -1
    pairs = {'(': ')', '[': ']', '{': '}'}
    stack = [pairs[text[open_idx]]]
    i = open_idx + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\\':
            i += 2
            continue
        if ch in ('"', "'"):
            i, _ = _skip_string(text, i)
            continue
        if ch in '([{':
            stack.append(pairs[ch])
        elif ch in ')]}':
            if not stack or ch != stack[-1]:
                return -1
            stack.pop()
            if not stack:
                return i
        i += 1
    return -1

@dataclass
class ASTNode:
    line_number: int = 0
    indent: int = 0
    filename: str = ''

@dataclass
class TopLevelRegionNode(ASTNode):
    """
    文件里 **label 之外**的那一整段（逐字、按原顺序）。

    为什么要有这个节点（2026-10-05，消除交付审查的第 3 条缺口）：
    以前只有顶层 `python:` 块会被保真带回（`PythonNode.header` → 第 3 条边界），
    而 `screen` / `style` / `init:` / `translate` / 文件头注释**根本不在 AST 里**
    （带子块的未建模块被 `_stmt_unmodeled` 跳过，只留一条 warning），
    `define` / `default` / `image` / `transform` 虽然进了 AST，却只留下
    结构化字段、**原文已经不在**了（`define e = Character(...)` 只记下角色）。

    于是"全量重生成"这条路必然丢它们 —— 而它正是**导出到新路径**与
    **补丁失败**两条路的必经之处。用户的诉求是"软件自己解决"，
    所以这里**不做建模**：整段原文按顺序带出来，渲染时原样写回文件级。

    与 `CodeBlock`（一拍之内的保真后门）同一个思路，作用域是整个文件。
    `blocks` 是 `[(块头, 去基准缩进的块体, 起始行号)]`。
    """

    blocks: list = field(default_factory=list)

@dataclass
class LabelNode(ASTNode):
    name: str = ''
    block: list = field(default_factory=list)

@dataclass
class DefineNode(ASTNode):
    target: str = ''
    value_type: str = ''
    args_str: str = ''
    rhs: str = ''                 # 等号右侧原文（用于收集字符串常量）
    display_name: str = ''
    color: str = ''
    what_color: str = ''
    image_tag: str = ''
    # DynamicCharacter('jc_name', ...) 的第一个位置参数是**名字变量**，
    # 不是显示名——显示名要去 `default jc_name = "「蒋忱」"` 里取。
    name_var: str = ''
    # Character(...) 里的其余关键字参数原样留存（kind / voice_tag /
    # side_image / what_prefix …）。角色定义写得很花，只挑 4 个字段
    # 存下来，导入到资源表时就会"看着不对"，说不清到底漏了什么。
    kwargs: dict = field(default_factory=dict)
    positional: list = field(default_factory=list)

@dataclass
class SayNode(ASTNode):
    who: str = ''
    what: str = ''

@dataclass
class ExtendNode(ASTNode):
    text: str = ''

@dataclass
class MenuNode(ASTNode):
    caption: str = ''
    options: list = field(default_factory=list)

@dataclass
class MenuOptionNode(ASTNode):
    text: str = ''
    condition: str = ''
    block: list = field(default_factory=list)

@dataclass
class IfNode(ASTNode):
    branches: list = field(default_factory=list)

@dataclass
class JumpNode(ASTNode):
    target: str = ''

@dataclass
class CallNode(ASTNode):
    target: str = ''
    #: `call X from Y` 的 `Y`（空 = 没写）
    from_clause: str = ''

@dataclass
class ReturnNode(ASTNode):
    pass

@dataclass
class PythonNode(ASTNode):
    code: str = ''
    #: 原文是 `$ code`（`$` 后有空格）还是 `$code`。
    #: Ren'Py 两种都合法，但**往返要保真**：原文写 `$persistent.x= True`，
    #: 导回去变成 `$ persistent.x= True` 会让每个 `$` 行都成一个 diff 块
    #: （用户："全量修改出来的东西，根本就没办法看"）。
    dollar_space: bool = True
    #: `python:` 块的**块头原文**（`init python:` / `python early:` /
    #: `python in store:` …）；单行 `$` 语句为空串。
    #:
    #: 为什么需要（2026-10-05，拓展边界③）：顶层 `init python:` 以前
    #: 落到 `_stmt_unmodeled` 被**跳过**（只留一条 warning），于是
    #: "这一章有顶层 python 块"这个事实根本不进模型 —— 下游没有任何东西
    #: 记得它。记下块头之后，它能被原样带回去（见 `ParseResult.file_blocks`）。
    header: str = ''

@dataclass
class TransformNode(ASTNode):
    """
    自定义变换定义：`transform name(params):` + ATL 块体。

    以前这类块语句被当作"未建模的块"直接跳过，于是
      · 表格里 `At` / `Transform` 列没法选到项目自己定义的变换；
      · 文案不知道 `leftish` 到底是"靠左站"还是"侧身回头"。
    现在把名字、参数、ATL 原文、以及给美术看的说明（前导注释或首个
    字符串字面量）全部收下来。
    """
    name: str = ''
    params: str = ''
    body: str = ''        # ATL 原文，按相对缩进还原
    doc: str = ''         # 说明：紧邻的前导注释，或块体首个字符串字面量
    is_expression: bool = False   # transform expression / ATL inline 等非常规形态

@dataclass
class LayeredImageNode(ASTNode):
    """
    `layeredimage ye:` —— Ren'Py 的合成立绘。

    它定义了 tag（ye）和一整套 attribute（happy / base_handdown / …），
    脚本里 `show ye base_handdown happy` 引用的就是这些。
    不解析它，这类"多属性立绘"会被整批报成"未登记"（实测 130 条）。
    """
    name: str = ''
    attributes: list = field(default_factory=list)
    image_format: str = ''

@dataclass
class ImageDefNode(ASTNode):
    """
    `image bg room = "images/bg/room.png"` / `image black = "#000000"`。

    这是 Ren'Py 里**最权威的图像清单**（比按文件名猜可靠得多）：
    `scene black`、`show bg bg1_1` 引用的名字就写在这里。不解析它，
    校验会把项目里明明定义好的图全报成"未登记"。
    """
    name: str = ''
    expr: str = ''

@dataclass
class ShowNode(ASTNode):
    image: str = ''
    at: list = field(default_factory=list)
    as_: str = ''
    with_: str = ''
    zorder: Optional[int] = None
    onlayer: str = ''
    behind: list = field(default_factory=list)
    is_expression: bool = False

@dataclass
class SceneNode(ASTNode):
    image: str = ''
    at: list = field(default_factory=list)
    with_: str = ''
    onlayer: str = ''
    is_expression: bool = False

@dataclass
class HideNode(ASTNode):
    image: str = ''
    with_: str = ''

@dataclass
class AudioNode(ASTNode):
    """play / queue / stop music|sound|audio <name>"""
    action: str = ''       # 'play' / 'queue' / 'stop'
    channel: str = ''      # 'music' / 'sound' / 'audio'
    name: str = ''
    fadein: float = 0.0
    fadeout: float = 0.0
    loop: bool = False

@dataclass
class VoiceNode(ASTNode):
    name: str = ''

@dataclass
class CommentNode(ASTNode):
    """整行注释（text 含前导 '#'，便于原样回写）。"""
    text: str = ''

@dataclass
class RawNode(ASTNode):
    """
    未建模、但属于演出流的原始语句（`with dissolve` / `pause` /
    `window hide` / `call screen` / 动态 jump 等）。

    原样保存、原样回写：宁可表格里多一列原文，也不要在往返中
    悄悄删掉程序员的代码。raw_kind 仅作诊断标签。
    """
    text: str = ''
    raw_kind: str = ''

@dataclass
class UnknownBlockNode(RawNode):
    """
    **未建模的带子块语句**（项目自定义的块语句，如 `mycue "闪电":`）。

    为什么需要这个节点（2026-10-07，M1 止血）：

    从前 `_stmt_unmodeled` 只接叶语句，**带子块的直接 `return None`** ——
    只留一条没人看的 warning。而顶层兜底 `_top_level_region()` **只覆盖
    label 之外**。于是 label 内的自定义块会走一条最坏的路径：

        mycue "闪电" intensity=3:      ← 被 `_stmt_say` 抢先匹配成台词，
            target = ling                 `intensity=3:` 与冒号一起丢掉
            hold 0.5                  ← 块体整段消失

    产物里长出**一句由 `mycue` 说的台词 `"闪电"`**，剧情被静默改掉
    （`tests/test_unknown_block_preserved.py` 钉住了这个现场）。

    现在是**整块保真**：头 + 块体逐字进保真通道，并按
    `raw_kind='unknown_block'` 计数，好让门禁与体检报告说得出
    "这几行工具只保真、不管辖"。

    · `text`      = 整块的原文（行首基准缩进已归零，块体保留相对缩进）；
    · `head`      = 头一行的原文（诊断用）；
    · `block_raw` = 块体各行（诊断/报告用）。
    """

    head: str = ''
    block_raw: list = field(default_factory=list)

@dataclass
class LoopNode(ASTNode):
    """
    循环块（`while` / `for`）—— **头行结构化 + 块体成语句**（D2 → T0，2026-10-07）。

    真机来源：`sr_general/game/minigame/mini_game1.rpy:50` 的**游戏主循环**。

    D2 时只做到一半：头行结构化、块体**逐字**（`body`）—— 因为 `Beat` 是
    **扁平**的（一维语句流），表达不了"这一段会重复执行"。那时的等级是
    `T0b`（内置 IR + 块体逐字），并留了一句"要真正结构化循环体，得给拍模型
    加作用域"。

    **2026-10-07 第三轮把作用域加上了**：块体现在由 `_parse_block` 正常解析成
    语句（`body_nodes`），`ParseResultBuilder._walk` 给它们标上
    `nest_path='…>loop:<n>'` —— 于是"这一拍在哪个循环里"变成了**随表走的结构位**
    （隐藏列 `Nest`），渲染层据此把扁平拍重新归组成 `while …:` 块。

    · `header`     = 头行原文；
    · `body_nodes` = 块体的 **AST 节点**（结构化那条路）；
    · `text`       = 头行原文（`text`/`header` 同值是刻意的：它俩一个是
      "语句头 token 的投影"、一个是可编辑面，见 `Statement.make_loop`）。

    ⚠ **没有"逐字块体副本"**：内容只活在块体那些语句里。留一份当兜底
    看着保险，实际是第二套真相（渲染时选错 = 同一段剧情出去两遍或一遍都不出去）。
    """

    header: str = ''
    body_nodes: list = field(default_factory=list)
    text: str = ''

@dataclass
class CameraNode(ASTNode):
    """
    3D 舞台的 `camera` 语句 —— **头行结构化 + 块体逐字**（2026-10-07）。

    官方语法（`3dstage.html`）是**带块**的：

        camera:                 # 不给层名 = master
            perspective True

        camera background:      # 只对 background 这一层开 3D 舞台
            perspective True

        camera:
            perspective True
            xpos 0
            linear 3.0 xpos 500

    所以它是**流程块**，不是叶语句 —— `RENPY_FLOW_BLOCK_HEADS` 里一直有它，
    但工具没建模：它落进 `_stmt_unmodeled` → `UnknownBlockNode`
    （`raw_kind='known_block'`）→ 报告里进"**催我们建模**"那一桶（T2）。

    这一层把欠账还掉，**边界与循环块（D2）完全相同**：头行（`camera <层名>:`
    里的层名）结构化、可编辑、可校验；**块体逐字**（ATL 属性是 Transform 的
    领域，工具不解释它）。于是等级是 `T0b` 而不是 `T0`。

    · `header` = 头行原文；`layer` = 层名（空 = master，与 Ren'Py 一致）；
    · `body`   = 块体各行（逐字、相对缩进）；`text` = 整块原文。
    """

    header: str = ''
    layer: str = ''
    body: list = field(default_factory=list)
    text: str = ''

_AUDIO_PARAM_WORDS = frozenset({
    'fadein', 'fadeout', 'loop', 'noloop', 'volume', 'relative_volume',
    'if_changed', 'tight', 'fadeinout',
})

def unescape_renpy_string(text: str) -> str:
    """
    Ren'Py 字符串字面量的内容 → 实际文本（反转义）。

    只处理转义序列，**不动 `{tag}` 与 `[var]`**（那是 Ren'Py 的文本标记，
    属于内容本身，反转义不该碰它们）。

    两个调用方：`_pick_str`（解析侧，`.rpy` → 内容）与
    `parse_translation_files._pick_named`（`tl/` → 译文）。
    """
    if '\\' not in text:
        return text
    out, i, n = [], 0, len(text)
    table = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\',
             '"': '"', "'": "'", ' ': ' ', 'a': '\a', 'b': '\b',
             'f': '\f', 'v': '\v', '0': '\0'}
    while i < n:
        ch = text[i]
        if ch == '\\' and i + 1 < n:
            nxt = text[i + 1]
            if nxt in table:
                out.append(table[nxt])
                i += 2
                continue
            # `\uXXXX` 这类：认不出就原样留着（不做多余猜测）
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)

def _pick_str(m, *groups) -> str:
    """
    取正则捕获的字符串字面量，并**反转义**。

    `_STR` / `_TR_STR` 的捕获组是 `((?:[^"\\\\]|\\\\.)*)` —— 它把 `\\.` 的
    反斜杠**一起留在捕获内容里**。于是

        "file \\"c:\\\\admin_script.py\\", line 24, "

    取出来是 `file \\"c:\\\\admin_script.py\\", line 24, `（源码形态），
    而它的**实际文本**是 `file "c:\\admin_script.py", line 24, `。

    不反转义的代价是**每往返一次转义层数翻倍**（`"` → `\\"` → `\\\\\\"` …）：
    真实项目 `chapter1_2.rpy` 里那条调试输出旁白就是这么漂的。

    **所有调用点都该反转义**：这里拿到的都是"内容"（台词 / 选项文字 /
    角色名 / 路径），没有一处需要"源码形态"—— 要源码的场合调的是
    `line.text`，不是这里。
    """
    for g in groups:
        v = m.group(g)
        if v is not None:
            return unescape_renpy_string(v)
    return ''

def split_string_speaker(text: str):
    """
    `"系统" "你已解锁…"` → `('"系统"', '你已解锁…')`；不是这个形态则 `None`。

    为什么单开一条路（2026-10-05，P0 实测）：Ren'Py 的 say 语句允许 `who` 是
    **字符串字面量**（一次性角色名，`"系统" "提示"` 这种写法完全合法）。
    而 `_RE_SAY` 的 who 组只认标识符（`[A-Za-z_][\\w.]*`），于是这种写法被
    当成"旁白 `"系统"`" —— **真正的台词整句丢掉**：

        "系统" "你已解锁角色【万理】的人物介绍！"
          → speaker='' + text='系统'          ← 后半句没了

    触发面比"改那一句"大得多：模型里根本没有那句台词，所以**这一章只要动过
    任何一处**，增量补丁就会把那一行改写成残缺的 `"系统"`
    （真实项目 `character_gallery.rpy` 2 处，都在示例 label 里）。

    返回的 `who_raw` 是**带引号的原文切片**（转义原样保留），这样：
      · 渲染时逐字写回去，不需要"重新转义"；
      · 与角色变量名（`e` / `jc`）天然分得开 —— 见 `beat_model.is_literal_speaker`。
    """
    text = str(text or '')
    if not text or text[0] not in '"\'':
        return None
    end, closed = _skip_string(text, 0)
    if not closed:
        return None
    i = end
    while i < len(text) and text[i] in ' \t':
        i += 1
    if i >= len(text) or text[i] not in '"\'':
        return None                      # 只有一个字符串 = 普通旁白
    end2, closed2 = _skip_string(text, i)
    if not closed2:
        return None
    return text[:end], unescape_renpy_string(text[i + 1:end2 - 1])
