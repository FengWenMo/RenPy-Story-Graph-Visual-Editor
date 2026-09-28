r"""
renpy_parser.py
================
Ren'Py 脚本解析器（三层架构 + 双输出 + 增量解析）

修复历史：
  · v2 修复缓存丢失 errors/warnings、行尾注释、python 块缩进、
        Graph 残留、三引号括号计数、单引号、点号角色名、
        重复 label 警告、幽灵节点警告、O(N²) label 收集
  · v3 修复：
    [FIX-1]  字符串正则在另一种引号内部误判
    [FIX-2]  if/elif 条件中的冒号被截断
    [FIX-3]  翻译单元哈希冲突
    [FIX-4]  翻译文件语言代码不支持连字符
    [FIX-5]  python: 块多行内容缩进丢失
    [FIX-6]  条件块内的 Python 操作被丢弃
    [FIX-7]  _has_unclosed_triple 未跳过转义
    [FIX-8]  三引号查找未跳过转义
    [FIX-9]  菜单多行 caption 被覆盖
  · v4 修复：
    [FIX-10] label / jump / call / define 函数名支持点号
    [FIX-11] show / scene 支持多词目标
    [FIX-12] 反斜杠续行仅在奇数个尾部反斜杠时触发
    [FIX-13] Character kwargs 支持非字符串值
    [FIX-14] python: 块基准缩进取所有直接子节点的最小缩进
    [FIX-15] 菜单多行 caption 使用 \n 拼接
    [FIX-16] 翻译文件支持单引号
    [FIX-17] python: 支持 python early / python in scope 等变体
    [FIX-18] menu / label 参数支持一层嵌套括号
  · v5 修复（正则→扫描器）：
    [FIX-19] 新增 _scan_toplevel_colon
    [FIX-20] 新增 _scan_balanced_close，替换 _PAREN_NESTED
    [FIX-21] show / scene 目标解析改用尾部分隔符扫描
    [FIX-22] _RE_KWARG 支持函数调用值
  · v6 修复：
    [FIX-23] _RE_DEFINE 缺少 re.DOTALL，多行 define 值解析失败
    [FIX-24] _skip_string 未区分"未闭合"与"末尾恰好闭合"
    [FIX-25] _is_menu_header 内部重复 re.match
    [FIX-26] show expression 中 expression 只在最前是关键字
  · v7 修复：
    [FIX-27] voice "xxx" 被误匹配为 SayNode
    [FIX-28] extend "xxx" 新增专用 ExtendNode
    [FIX-29] analyze 入口对 files 去重，防止重复 AST
    [FIX-30] GraphBuilder._add_edge 去重
    [FIX-31] _parse_character_args 用扫描器重写
    [FIX-32] _is_menu_header 用 m.end() 定位
    [FIX-33] _skip_string 加入边界保护
    [FIX-34] 清空 operations 从 _collect_labels 移到 build 开头
  · v8 修复：
    [FIX-35] _RE_LABEL_HEAD 由 ^label\\b 改为 ^label(?=\\s)
    [FIX-36] _RE_PYTHON 的 [\\w ] 加入 .，支持 python in store.x:
    [FIX-37] define 右侧改用 _scan_balanced_close 提取第一个函数调用的
             参数，不再用贪婪 .*
    [FIX-38] parse_translation_files 在遇到 translate 但未匹配完整模式
             时重置 current_id
    [FIX-39] _parse_menu_option 空条件记一条 error
    [FIX-40] parse_translation_files 使用命名组
  · v9 修复：
    [FIX-41] parse_translation_files: translate <lang> strings: 块内
             每个 old/new 对生成独立 unit id（基于 old 文本哈希），
             修复多组 old/new 互相覆盖
    [FIX-42] define 右侧函数调用后若有尾部表达式，记 warning 而非
             静默丢弃 Character 参数（FIX-37 的兜底）
    [FIX-43] jump expression / call expression 不再误识别为
             target="expression"；call screen 不再生成幽灵边
  · v10 修复：
    [FIX-44] parse_translation_files: translate <lang> python: 与
             translate <lang> strings: 一样不产生 unit id，
             避免后续 old/new 挂到不存在的 'python' unit 上
    [FIX-45] GraphBuilder._add_edge 用 `is None` 判断 node id，
             不依赖 id 从 1 开始这一隐含约定
    [FIX-46] GraphBuilder.build 只清空即将重建的 label 节点的
             operations，避免部分文件重解时误清其他文件的
             operations（parse_renpy_file 兼容接口的场景）；
             同时将 _extract_edges 中 PythonNode 分支的
             `if nid:` 改为 `if nid is not None:`，与 FIX-45 风格统一。
  · v11 修复（本轮）：
    [FIX-47] show / scene 目标解析改用 _tokenize_show + _parse_show_scene，
             支持多关键字（at/as/with/behind/onlayer/zorder）、
             expression 形式、逗号分隔的多目标 at 值；
             ShowNode / SceneNode 从单一 target 字段扩展为结构化字段
             （image / at / as_ / with_ / zorder / onlayer / behind /
             is_expression）。zorder 解析失败记 warning，不再静默忽略。
  · v12 修复（本轮）：
    [FIX-48] TranslationUnitGenerator._unique_id 改用 core.uid.
             make_translation_uid 统一生成标识符前缀，替换内联的
             re.sub(r'[^\w]', '_', label) 逻辑。

注意事项（已核验的非 bug）：
  · translate <lang> style <name>: 不会被 re_translate 匹配
    （正则要求第二 token 后紧跟冒号），走 re_translate_any 分支
    已被正确处理，无需在排除列表中加入 'style'。
  · _IGNORED_SAY_HEADS 只包含 'voice'。Ren'Py 中合法
    `keyword "string"` 形式只有 voice；pause/play/stop/queue/window
    等关键字后面不直接跟字符串，_RE_SAY 匹配不上。
  · ShowNode / SceneNode 目前没有被任何下游消费者使用，
    show screen / scene 等语句的解析结果属于信息收集，无实际影响。
  · python: 必须跟缩进块，内联形式是 $ <code>；
    `python: pass` 不是合法语法。
  · PythonNode 无条件入 operations 是 v3 [FIX-6] 的既定行为：
      "条件块内的 Python 操作被丢弃"是 bug，v3 已修。operations
      目前不带条件前缀，如需按条件分类，应在下游处理或另立
      数据结构，不应回退到"条件块内丢弃"。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from storygraph.graph.models import Graph
except ImportError:
    Graph = None

from storygraph.core.uid import make_translation_uid
from storygraph.core.statement import Statement


# ====================================================================
#  模块级辅助：字符串 / 括号 / 顶层冒号扫描
# ====================================================================

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


# ====================================================================
#  第一层：Lexer
# ====================================================================

@dataclass
class LogicalLine:
    text: str
    line_number: int
    indent: int
    filename: str = ''
    subblock: list = field(default_factory=list)
    # 整行注释（`# ...`）。原先在 lexer 就被丢弃，导致"给美术的表情标注"
    # 这类跨职能信息在 rpy↔表格 往返中消失。现在保留下来。
    is_comment: bool = False


class RenPyLexer:
    """将 .rpy 源文件转换为逻辑行嵌套树。"""

    # 一条逻辑语句最多允许合并多少物理行（括号续行的安全阀）
    _MAX_MERGE_LINES = 120

    def __init__(self):
        self.errors: list = []
        self._current_file: str = ''

    def tokenize(self, filepath) -> list:
        filepath = Path(filepath)
        self._current_file = str(filepath)
        try:
            raw_lines = filepath.read_text(encoding='utf-8').splitlines()
        except Exception as e:
            self.errors.append(f"读取失败 {filepath}: {e}")
            return []

        flat = self._flatten(raw_lines)
        return self._build_tree(flat, str(filepath))

    # ---------- 扁平化 ----------

    def _flatten(self, raw_lines: list) -> list:
        flat = []
        i = 0
        n = len(raw_lines)

        while i < n:
            raw = raw_lines[i]
            line_no = i + 1
            stripped = raw.strip()

            if not stripped:
                i += 1
                continue

            indent = len(raw) - len(raw.lstrip())

            # 整行注释：保留（含前导 '#'），供"标注/待办"类信息往返。
            if stripped.startswith('#'):
                flat.append((stripped, line_no, indent, True))
                i += 1
                continue

            accumulated = raw

            if self._has_unclosed_triple(accumulated):
                while i + 1 < n and self._has_unclosed_triple(accumulated):
                    i += 1
                    accumulated += '\n' + raw_lines[i]

            while i + 1 < n:
                s = accumulated.rstrip()
                if not s.endswith('\\'):
                    break
                cnt = 0
                for c in reversed(s):
                    if c != '\\':
                        break
                    cnt += 1
                if cnt % 2 == 0:
                    break
                accumulated = s[:-1]
                i += 1
                accumulated += ' ' + raw_lines[i].strip()

            while i + 1 < n:
                clean_for_count = self._strip_inline_comment(accumulated)
                oc, cc = self._count_brackets(clean_for_count)
                if oc <= cc:
                    break
                # 安全阀：括号计数万一被奇葩代码骗了，也不能一路并到文件末尾
                # （那会把后面所有 label 吃掉）。宁可留一条超长语句 + 警告。
                if i - line_no > self._MAX_MERGE_LINES:
                    self.errors.append(
                        f"{self._current_file}:{line_no}: 括号始终未闭合，"
                        f"已停止合并（可能解析不完整）"
                    )
                    break
                i += 1
                # 用 '\n' 而不是 ' ' 拼接：这些是**真正的多行语句**
                # （参数表、列表字面量）。用空格拼会把换行信息抹掉，
                # 之后按行剥离行内注释时第一个 '#' 就会把后面全删掉。
                accumulated += '\n' + raw_lines[i].rstrip()

            accumulated = self._strip_inline_comment(accumulated)

            flat.append((accumulated, line_no, indent, False))
            i += 1

        return flat

    @staticmethod
    def _has_unclosed_triple(text: str) -> bool:
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]

            if ch == '\\':
                i += 2
                continue

            if i + 2 < n and text[i:i + 3] in ('"""', "'''"):
                delim = text[i:i + 3]
                j = _find_triple_end(text, i + 3, delim)
                if j == -1:
                    return True
                i = j + 3
                continue

            if ch in ('"', "'"):
                i, _ = _skip_string(text, i)
                continue

            i += 1

        return False

    @staticmethod
    def _strip_line_comment(line: str) -> str:
        """去掉**单行**的行内注释（字符串里的 # 不算）。"""
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]

            if ch == '\\':
                i += 2
                continue

            if ch in ('"', "'"):
                end, closed = _skip_string(line, i)
                if not closed:
                    return line          # 引号未闭合，别乱动
                i = end
                continue

            if ch == '#':
                return line[:i].rstrip()

            i += 1

        return line

    @staticmethod
    def _strip_inline_comment(text: str) -> str:
        """
        去掉行内注释，**按行处理**。

        这里曾经是"遇到第一个 # 就 return text[:i]"。对单行没问题，
        但对**已合并的多行逻辑语句**就是从第一个 # 起把后面所有行全删掉：
          括号计数因此永远配不平 → 合并循环一路吃到底 →
          整个文件被并成一条逻辑行、里面所有 label 全丢。
        真实项目（init python 里带多行参数表和行内中文注释）必然触发。
        """
        if '\n' not in text:
            return RenPyLexer._strip_line_comment(text)
        return '\n'.join(RenPyLexer._strip_line_comment(ln)
                         for ln in text.split('\n'))

    @staticmethod
    def _count_brackets(text: str) -> tuple:
        open_count = 0
        close_count = 0
        i = 0
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
                open_count += 1
            elif ch in ')]}':
                close_count += 1

            i += 1

        return open_count, close_count

    def _build_tree(self, flat: list, filename: str) -> list:
        root = LogicalLine(text='', line_number=0, indent=-1,
                           filename=filename)
        stack = [root]

        for item in flat:
            text, line_no, indent, is_comment = item
            node = LogicalLine(
                text=text,
                line_number=line_no,
                indent=indent,
                filename=filename,
                is_comment=is_comment,
            )
            while len(stack) > 1 and stack[-1].indent >= indent:
                stack.pop()
            stack[-1].subblock.append(node)
            stack.append(node)

        return root.subblock


# ====================================================================
#  第二层：Parser —— AST
# ====================================================================

@dataclass
class ASTNode:
    line_number: int = 0
    indent: int = 0
    filename: str = ''


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


@dataclass
class ReturnNode(ASTNode):
    pass


@dataclass
class PythonNode(ASTNode):
    code: str = ''


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


# 字符串正则：双引号分支与单引号分支分离
_STR_DQ = r'"((?:[^"\\]|\\.)*)"'
_STR_SQ = r"'((?:[^'\\]|\\.)*)'"
_STR = f'(?:{_STR_DQ}|{_STR_SQ})'

# 音频语句里的"参数词"：出现它们说明后面不是资产名
_AUDIO_PARAM_WORDS = frozenset({
    'fadein', 'fadeout', 'loop', 'noloop', 'volume', 'relative_volume',
    'if_changed', 'tight', 'fadeinout',
})

# 翻译文件用的命名组版本
_TR_DQ = r'"(?P<dq>(?:[^"\\]|\\.)*)"'
_TR_SQ = r"'(?P<sq>(?:[^'\\]|\\.)*)'"
_TR_STR = f'(?:{_TR_DQ}|{_TR_SQ})'


def _pick_str(m, *groups) -> str:
    for g in groups:
        v = m.group(g)
        if v is not None:
            return v
    return ''


def _string_literal(text: str):
    """`"abc"` / `'abc'` → 'abc'；不是纯字符串字面量则返回 None。"""
    text = (text or '').strip()
    if len(text) < 2:
        return None
    if text[0] not in '"\'' or text[-1] != text[0]:
        return None
    inner = text[1:-1]
    if text[0] in inner:                 # 里面还有同类引号 → 不是简单字面量
        return None
    return inner


def _image_path_from_expr(expr: str):
    """`image x = "images/a.png"` → 'images/a.png'；不是路径则 None。"""
    value = _string_literal(expr)
    if not value:
        return None
    low = value.lower()
    if any(low.endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.webp',
                                        '.bmp', '.gif', '.avif')):
        return value
    return None


def _image_solid_from_expr(expr: str):
    """`image x = "#000000"` / `Solid("#000")` → '#000000'。"""
    expr = (expr or '').strip()
    value = _string_literal(expr)
    if value and value.startswith('#'):
        return value
    m = re.search(r'Solid\(\s*["\'](#[0-9a-fA-F]{3,8})["\']', expr)
    if m:
        return m.group(1)
    return None


class RenPyParser:
    """递归下降解析器：LogicalLine 树 → ASTNode 列表。"""

    # ---------- 简单前缀正则 ----------
    _RE_LABEL_HEAD = re.compile(r'^label(?=\s)')
    _RE_IF_HEAD = re.compile(r'^if\s+')
    _RE_ELIF_HEAD = re.compile(r'^elif\s+')
    _RE_ELSE_HEAD = re.compile(r'^else\s*:')
    _RE_MENU_HEAD = re.compile(r'^menu\b')
    # 角色定义两种写法都收：define 是常量，default 是可存档变量
    _RE_DEFINE = re.compile(
        r'^(?:define|default)\s+([\w.]+)\s*=\s*(.+)$', re.DOTALL)
    _RE_SAY = re.compile(r'^([A-Za-z_][\w.]*)?\s*' + _STR)
    _RE_JUMP_EXPR = re.compile(r'^jump\s+expression\b')
    _RE_CALL_EXPR = re.compile(r'^call\s+expression\b')
    _RE_CALL_SCREEN = re.compile(r'^call\s+screen\b')
    _RE_JUMP = re.compile(r'^jump\s+([\w.]+)')
    _RE_CALL = re.compile(r'^call\s+([\w.]+)')
    _RE_RETURN = re.compile(r'^return\s*$')
    _RE_DOLLAR = re.compile(r'^\$\s*(.+)')
    _RE_PYTHON = re.compile(r'^python(?:\s+[\w. ]+)?\s*:')
    _RE_SHOW_HEAD = re.compile(r'^show\s+')
    _RE_SCENE_HEAD = re.compile(r'^scene\s+')
    _RE_HIDE_HEAD = re.compile(r'^hide\s+')
    # `show screen X` / `hide screen X` 是 UI 操作，不是立绘
    _RE_SCREEN_SHOW = re.compile(r'^(?:show|hide)\s+screen\b')
    # `image <名字> = <表达式>`：名字允许带空格（`bg bg1_1`）
    _RE_IMAGE_DEF = re.compile(
        r'^image\s+(?P<name>[^\s=][^=]*?)\s*=\s*(?P<rhs>.+)$')
    # `layeredimage <tag>:`（合成立绘）
    _RE_LAYEREDIMAGE = re.compile(r'^layeredimage\s+([\w.]+)')
    # layeredimage 块里声明属性的写法
    _RE_ATTR_DECL = re.compile(r'^attribute\s+([\w.]+)')
    _RE_ATTR_CLAUSE = re.compile(
        r'\b(?:when|if_all|if_any|if_not)\s+(.+)$')
    _RE_IMAGE_FORMAT = re.compile(r'image_format\s+["\']([^"\']+)["\']')
    # transform 定义两种形态都要认：
    #   transform name:            （无参）
    #   transform name(a, b=1):    （带参）
    _RE_TRANSFORM = re.compile(r'^transform\s+([\w.]+)')
    # 只认"整行就是一个字符串字面量"的首行（Ren'Py 用作文档字符串）
    _RE_ONLY_STR = re.compile(r'^(?:' + _STR + r')\s*(?:#.*)?$')
    _RE_AUDIO = re.compile(
        r'^(?P<action>play|queue|stop)\s+'
        r'(?P<channel>music|sound|audio)'
        r'(?:\s+(?P<rest>.*?))?\s*$')
    # voice 接受两种写法：字符串字面量（voice "a.ogg"）或裸表达式
    # （voice v_001，v_001 是变量）。后者也是合法 Ren'Py 语法，
    # 之前只收字符串导致裸表达式的语音被整句丢掉了。
    _RE_VOICE = re.compile(r'^voice\s+(?:' + _STR + r'|(\S+))')
    # extend 允许紧跟字符串（`extend"补充"` 也是合法写法，真实项目在用）
    _RE_EXTEND_HEAD = re.compile(r'^extend(?=\s|["\'])')
    _RE_EXTEND_STR = re.compile(r'^extend\s*' + _STR)

    # show / scene 解析用的关键字集合
    _SHOW_KEYWORDS = frozenset(
        {'at', 'as', 'with', 'behind', 'onlayer', 'zorder'}
    )
    _SCENE_KEYWORDS = frozenset({'at', 'with', 'onlayer'})

    _RE_POS_ARG = re.compile(r'^\s*' + _STR)

    _IGNORED_SAY_HEADS = frozenset({'voice'})

    _RE_DEFINE_FUNC_HEAD = re.compile(r'^([\w.]+)\s*\(')

    def __init__(self):
        self.errors: list = []
        self.warnings: list = []

    def parse(self, filepath) -> list:
        self.errors = []
        self.warnings = []
        lexer = RenPyLexer()
        lines = lexer.tokenize(filepath)
        self.errors.extend(lexer.errors)
        return self._parse_block(lines)

    # ---------- 块解析 ----------

    def _parse_block(self, lines: list) -> list:
        nodes = []
        i = 0
        pending_comments = []       # 紧邻的整行注释（可能成为下个定义的 doc）
        while i < len(lines):
            line = lines[i]
            text = line.text.strip()
            node = self._parse_statement(line, text)

            if node is None:
                i += 1
                continue

            if line.is_comment:
                pending_comments.append(line)

            if isinstance(node, IfNode):
                i = self._consume_if_chain(lines, i, node)
                nodes.append(node)
                pending_comments = []
                continue

            # transform 的"说明"常常写成紧邻的注释行。把它接到定义上，
            # 表格里选中这个变换时就能看到人话解释，而不是一串 xpos。
            if isinstance(node, TransformNode) and not node.doc:
                doc_lines = [p.text.lstrip('#').strip()
                             for p in pending_comments
                             if p.indent == line.indent]
                doc_lines = [d for d in doc_lines if d]
                if doc_lines:
                    node.doc = '\n'.join(doc_lines)

            nodes.append(node)
            if not line.is_comment:
                pending_comments = []
            i += 1
        return nodes

    # ---------- transform 解析 ----------

    @staticmethod
    def _render_block_source(lines: list, base_indent: int) -> list:
        """
        把逻辑行树按相对缩进还原成源码行（用于保留 ATL 原文）。

        注意 LogicalLine.text 是**带行首缩进的原始文本**，所以要先按
        `indent` 切掉原缩进，再补上相对缩进，否则缩进会翻倍。
        """
        out = []
        for ln in lines:
            rel = max(0, ln.indent - base_indent)
            body = ln.text[ln.indent:] if ln.indent > 0 else ln.text
            out.append(' ' * rel + body)
            if ln.subblock:
                out.extend(
                    RenPyParser._render_block_source(ln.subblock, base_indent))
        return out

    def _parse_transform(self, line: LogicalLine,
                         text: str) -> Optional[TransformNode]:
        m = self._RE_TRANSFORM.match(text)
        if not m:
            return None
        name = m.group(1)

        # 形如 `transform foo(x=0.5, y=0.5):` —— 名字后紧跟 '(' 的是参数表
        rest = text[m.end():]
        params = ''
        if rest.startswith('('):
            close = _scan_balanced_close(rest, 0)
            if close == -1:
                self.warnings.append(
                    f"{line.filename}:{line.line_number}: "
                    f"transform {name} 的参数括号不匹配，已按无参处理"
                )
            else:
                params = rest[1:close].strip()

        # 块体：按相对缩进还原，comment 行也写进去（注释是给美术的意图）
        body_lines = self._render_block_source(line.subblock, line.indent)
        body = '\n'.join(body_lines)

        doc = ''
        for raw in body_lines:
            s = raw.strip()
            if not s or s.startswith('#'):
                continue
            sm = self._RE_ONLY_STR.match(s)
            if sm:
                doc = _pick_str(sm, 1, 2)
            break

        return TransformNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            name=name,
            params=params,
            body=body,
            doc=doc,
        )

    def _parse_layeredimage(self, line: LogicalLine, text: str,
                            m) -> LayeredImageNode:
        """
        解析 `layeredimage <tag>:` 块，收集它声明的全部 attribute。

        attribute 有两种写法：
          · `attribute happy default:` —— 直接声明
          · `group expressions when not base_handdown:` —— 条件里出现的
            base_handdown 也是属性名（在别的 group 里声明）
        两种都要收，否则 `show ye base_handdown happy` 里的
        base_handdown 会被判成"未声明"。
        """
        name = m.group(1)
        attrs = []
        image_format = ''

        def _add(word):
            word = str(word or '').strip()
            if word and word not in attrs and word.isidentifier():
                attrs.append(word)

        def walk(block, fmt):
            for ln in block or ():
                body = ln.text.strip()
                fm = self._RE_IMAGE_FORMAT.search(body)
                if fm:
                    fmt = fm.group(1)
                am = self._RE_ATTR_DECL.match(body)
                if am:
                    _add(am.group(1))
                cm = self._RE_ATTR_CLAUSE.search(
                    RenPyLexer._strip_line_comment(body))
                if cm:
                    for tok in re.split(r'[\s()]+', cm.group(1)):
                        if tok and tok not in ('not', 'and', 'or', 'True',
                                               'False', 'None'):
                            _add(tok)
                if ln.subblock:
                    fmt = walk(ln.subblock, fmt)
            return fmt

        image_format = walk(line.subblock, image_format)
        return LayeredImageNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            name=name,
            attributes=attrs,
            image_format=image_format,
        )

    def _parse_statement(self, line: LogicalLine, text: str):
        # ---- 整行注释 ----
        # 注释不是"可忽略的噪声"：`# 表情A6，距离近` 是文案给美术的意图，
        # 往返丢了它，美术就不知道这张立绘该是什么情绪。
        if line.is_comment:
            return CommentNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=line.text.strip(),
            )

        # ---- label ----
        if self._RE_LABEL_HEAD.match(text):
            return self._parse_label(line, text)

        # ---- define ----
        m = self._RE_DEFINE.match(text)
        if m:
            target = m.group(1)
            rhs = m.group(2).strip()
            value_type = ''
            args_str = ''
            fm = self._RE_DEFINE_FUNC_HEAD.match(rhs)
            if fm:
                open_idx = fm.end() - 1
                close_idx = _scan_balanced_close(rhs, open_idx)
                if close_idx != -1:
                    tail = rhs[close_idx + 1:].strip()
                    if not tail:
                        value_type = fm.group(1)
                        args_str = rhs[open_idx + 1:close_idx]
                    else:
                        tail_display = tail[:60] + (
                            '...' if len(tail) > 60 else ''
                        )
                        self.warnings.append(
                            f"{line.filename}:{line.line_number}: "
                            f"define 右侧存在额外表达式，"
                            f"未解析其参数: {tail_display!r}"
                        )

            node = DefineNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                target=target,
                value_type=value_type,
                args_str=args_str,
                rhs=rhs,
            )
            if self._is_character_type(value_type):
                self._parse_character_args(node, args_str)
            return node

        # ---- menu ----
        m_menu = self._RE_MENU_HEAD.match(text)
        if m_menu:
            header_caption = self._scan_menu_header(text, m_menu)
            if header_caption is not None:
                node = MenuNode(
                    line_number=line.line_number,
                    indent=line.indent,
                    filename=line.filename,
                )
                sub_caption, node.options = self._parse_menu(line.subblock)
                if header_caption and sub_caption:
                    node.caption = header_caption + '\n' + sub_caption
                else:
                    node.caption = header_caption or sub_caption
                return node

        # ---- if ----
        m = self._RE_IF_HEAD.match(text)
        if m:
            colon = _scan_toplevel_colon(text, m.end())
            if colon == -1:
                self.errors.append(
                    f"{line.filename}:{line.line_number}: "
                    f"if 条件缺少顶层冒号"
                )
                return None
            cond = text[m.end():colon].strip()
            node = IfNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
            )
            node.branches.append((cond, self._parse_block(line.subblock)))
            return node

        # ---- jump expression ----
        if self._RE_JUMP_EXPR.match(text):
            # 动态跳转无法静态断言目标，但语句本身必须保真——
            # 原样存成 Raw，导出时原样回写，否则控制流被悄悄改掉。
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='jump_expression',
            )

        # ---- call expression / call screen ----
        if self._RE_CALL_EXPR.match(text):
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='call_expression',
            )

        if self._RE_CALL_SCREEN.match(text):
            # call screen 是 UI 调用，不产生 label 跳转边，但仍是演出一步
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='call_screen',
            )

        # ---- jump ----
        m = self._RE_JUMP.match(text)
        if m:
            return JumpNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                target=m.group(1),
            )

        # ---- call ----
        m = self._RE_CALL.match(text)
        if m:
            return CallNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                target=m.group(1),
            )

        # ---- return ----
        if self._RE_RETURN.match(text):
            return ReturnNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
            )

        # ---- $ python ----
        m = self._RE_DOLLAR.match(text)
        if m:
            return PythonNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                code=m.group(1).strip(),
            )

        # ---- python: block ----
        if self._RE_PYTHON.match(text):
            code = self._collect_python_block(line)
            return PythonNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                code=code,
            )

        # ---- layeredimage（合成立绘）----
        m = self._RE_LAYEREDIMAGE.match(text)
        if m:
            return self._parse_layeredimage(line, text, m)

        # ---- image 声明 ----
        m = self._RE_IMAGE_DEF.match(text)
        if m:
            return ImageDefNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                name=m.group('name').strip(),
                expr=m.group('rhs').strip(),
            )

        # ---- show / hide screen（UI 操作，不是图像引用）----
        if self._RE_SCREEN_SHOW.match(text):
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='screen',
            )

        # ---- show ----
        if self._RE_SHOW_HEAD.match(text):
            parsed = self._parse_show_scene(text, is_scene=False)
            if parsed and parsed['image']:
                for w in parsed['warnings']:
                    self.warnings.append(
                        f"{line.filename}:{line.line_number}: {w}"
                    )
                return ShowNode(
                    line_number=line.line_number,
                    indent=line.indent,
                    filename=line.filename,
                    image=parsed['image'],
                    at=parsed['at'],
                    as_=parsed['as_'],
                    with_=parsed['with_'],
                    zorder=parsed['zorder'],
                    onlayer=parsed['onlayer'],
                    behind=parsed['behind'],
                    is_expression=parsed['is_expression'],
                )

        # ---- scene ----
        if self._RE_SCENE_HEAD.match(text):
            parsed = self._parse_show_scene(text, is_scene=True)
            if parsed and parsed['image']:
                for w in parsed['warnings']:
                    self.warnings.append(
                        f"{line.filename}:{line.line_number}: {w}"
                    )
                return SceneNode(
                    line_number=line.line_number,
                    indent=line.indent,
                    filename=line.filename,
                    image=parsed['image'],
                    at=parsed['at'],
                    with_=parsed['with_'],
                    onlayer=parsed['onlayer'],
                    is_expression=parsed['is_expression'],
                )

        # ---- hide ----
        m = self._RE_HIDE_HEAD.match(text)
        if m:
            rest = text[m.end():].strip()
            with_ = ''
            mw = re.search(r'\bwith\s+([\w.]+)', rest)
            if mw:
                with_ = mw.group(1)
                rest = rest[:mw.start()].strip()
            return HideNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                image=rest,
                with_=with_,
            )

        # ---- play / queue / stop ----
        m = self._RE_AUDIO.match(text)
        if m:
            action = m.group('action')
            rest = (m.group('rest') or '').strip()
            name = ''
            # `stop music fadeout 1.0`：stop 后面没有"要播的资产"，
            # 全是参数。以前会把 fadeout 当成音频名，于是校验报
            # "音频 'fadeout' 未登记"（真实项目一次 11 条）。
            if action == 'stop':
                parts = rest.split()
                if parts and parts[0] in _AUDIO_PARAM_WORDS:
                    name, rest = '', rest
                elif parts:
                    name = parts[0]
                    rest = ' '.join(parts[1:])
            else:
                qm = re.match(r'^["\']([^"\']+)["\']', rest)
                if qm:
                    name = qm.group(1)
                    rest = rest[qm.end():]
                else:
                    parts = rest.split()
                    # `play music fadein 1.0` 这种没有名字的写法
                    if parts and parts[0] in _AUDIO_PARAM_WORDS:
                        name = ''
                    else:
                        name = parts[0] if parts else ''
                        rest = ' '.join(parts[1:])

            def _num(pat):
                mm = re.search(pat, rest)
                try:
                    return float(mm.group(1)) if mm else 0.0
                except ValueError:
                    return 0.0

            return AudioNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                action=action,
                channel=m.group('channel'),
                name=name,
                fadein=_num(r'\bfadein\s+([\d.]+)'),
                fadeout=_num(r'\bfadeout\s+([\d.]+)'),
                loop=bool(re.search(r'\bloop\b', rest)),
            )

        # ---- voice ----
        m = self._RE_VOICE.match(text)
        if m:
            return VoiceNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                name=_pick_str(m, 1, 2, 3),
            )

        # ---- extend ----
        if self._RE_EXTEND_HEAD.match(text):
            m = self._RE_EXTEND_STR.match(text)
            if m:
                return ExtendNode(
                    line_number=line.line_number,
                    indent=line.indent,
                    filename=line.filename,
                    text=_pick_str(m, 1, 2),
                )

        # ---- say（兜底） ----
        m = self._RE_SAY.match(text)
        if m:
            who = m.group(1) or ''
            if who in self._IGNORED_SAY_HEADS:
                return None
            what = _pick_str(m, 2, 3)
            return SayNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                who=who,
                what=what,
            )

        # ---- transform 定义 ----
        # 这是"支持自定义变换"的关键：不解析它，表格里的 At / Transform
        # 列就永远选不到项目自己写的变换。
        if self._RE_TRANSFORM.match(text):
            node = self._parse_transform(line, text)
            if node is not None:
                return node

        # ---- 未建模语句的兜底 ----
        # 到这里还没被任何分支接住的叶语句，说明模型没覆盖它。与其
        # 静默丢弃（这正是"解析不够强大"的根源），不如原样保存：
        # 渲染时逐字回写，保证 rpy↔表格 往返不丢程序员的代码。
        # 只收叶语句——带子块的未知语句（screen/init/transform …）交给
        # 上层忽略，避免只留头部、丢掉块体造成更严重的破坏。
        if not line.subblock:
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='unmodeled',
            )

        self.warnings.append(
            f"{line.filename}:{line.line_number}: "
            f"未建模的块语句已跳过（不在演出流内）: {text[:40]!r}"
        )
        return None

    # ---------- label 解析 ----------

    def _parse_label(self, line: LogicalLine, text: str) -> Optional[LabelNode]:
        m = self._RE_LABEL_HEAD.match(text)
        if not m:
            return None
        i = m.end()

        m2 = re.match(r'\s*([\w.]+)', text[i:])
        if not m2:
            self.errors.append(
                f"{line.filename}:{line.line_number}: label 缺少名称"
            )
            return None
        name = m2.group(1)
        i += m2.end()

        while i < len(text) and text[i] in ' \t':
            i += 1

        if i < len(text) and text[i] == '(':
            close = _scan_balanced_close(text, i)
            if close == -1:
                self.errors.append(
                    f"{line.filename}:{line.line_number}: "
                    f"label 参数括号未闭合"
                )
                return None
            i = close + 1
            while i < len(text) and text[i] in ' \t':
                i += 1

        if i >= len(text) or text[i] != ':':
            self.errors.append(
                f"{line.filename}:{line.line_number}: label 缺少冒号"
            )
            return None

        node = LabelNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            name=name,
        )
        node.block = self._parse_block(line.subblock)
        return node

    # ---------- menu 头扫描 ----------

    @classmethod
    def _scan_menu_header(cls, text: str, m) -> Optional[str]:
        """
        扫描 menu 头，返回 caption 字符串（无 caption 时返回空串）；
        不是合法 menu 头则返回 None。

        支持的合法形式：
            menu:
            menu (args):
            menu "caption":
            menu "caption" (args):
        """
        i = m.end()
        while i < len(text) and text[i] in ' \t':
            i += 1

        caption = ''
        if i < len(text) and text[i] in ('"', "'"):
            end, closed = _skip_string(text, i)
            if not closed:
                return None
            caption = text[i + 1:end - 1]
            i = end
            while i < len(text) and text[i] in ' \t':
                i += 1

        if i < len(text) and text[i] == ':':
            return caption

        if i < len(text) and text[i] == '(':
            close = _scan_balanced_close(text, i)
            if close == -1:
                return None
            j = close + 1
            while j < len(text) and text[j] in ' \t':
                j += 1
            if j < len(text) and text[j] == ':':
                return caption

        return None

    # ---------- show / scene 目标解析 ----------

    @staticmethod
    def _tokenize_show(rest: str) -> list:
        """
        把 show/scene 语句的目标部分分词。
        · 空格分隔
        · 逗号作为独立 token
        · 引号字符串不切分（'show expression "a b.png"' 中
          '"a b.png"' 是一个 token）
        """
        tokens = []
        i = 0
        n = len(rest)
        while i < n:
            c = rest[i]
            if c in ' \t':
                i += 1
                continue
            if c == ',':
                tokens.append(',')
                i += 1
                continue
            if c in ('"', "'"):
                quote = c
                start = i
                i += 1
                while i < n:
                    if rest[i] == '\\':
                        i += 2
                        continue
                    if rest[i] == quote:
                        i += 1
                        break
                    i += 1
                tokens.append(rest[start:i])
                continue
            start = i
            while i < n and rest[i] not in ' \t,':
                i += 1
            tokens.append(rest[start:i])
        return tokens

    @classmethod
    def _parse_show_scene(cls, text: str, is_scene: bool):
        """
        解析 show / scene 目标。

        返回 dict，或 None（语法不匹配）：
          image, at, as_, with_, zorder, onlayer, behind,
          is_expression, warnings (list)

        warnings 为字符串列表，元素不含 filename:line 前缀，
        由调用层负责拼接位置信息。
        """
        prefix = 'scene' if is_scene else 'show'
        m = re.match(r'^' + prefix + r'\s+', text)
        if not m:
            return None

        rest = text[m.end():].strip()
        result = {
            'image': '',
            'at': [],
            'as_': '',
            'with_': '',
            'zorder': None,
            'onlayer': '',
            'behind': [],
            'is_expression': False,
            'warnings': [],
        }

        if rest.startswith('expression '):
            result['is_expression'] = True
            rest = rest[len('expression '):].lstrip()
        elif rest == 'expression':
            result['is_expression'] = True
            rest = ''

        tokens = cls._tokenize_show(rest)
        keywords = cls._SCENE_KEYWORDS if is_scene else cls._SHOW_KEYWORDS

        image_parts = []
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if tok == ',':
                i += 1
                continue
            if tok in keywords:
                kw = tok
                i += 1
                # 跳过紧跟的逗号
                while i < n and tokens[i] == ',':
                    i += 1

                if kw == 'at':
                    # 一个或多个标识符，逗号分隔；遇下一个关键字停止
                    while i < n and tokens[i] not in keywords:
                        if tokens[i] != ',':
                            result['at'].append(tokens[i])
                        i += 1
                elif kw == 'as':
                    if i < n and tokens[i] not in keywords:
                        result['as_'] = tokens[i]
                        i += 1
                elif kw == 'with':
                    if i < n and tokens[i] not in keywords:
                        result['with_'] = tokens[i]
                        i += 1
                elif kw == 'onlayer':
                    if i < n and tokens[i] not in keywords:
                        result['onlayer'] = tokens[i]
                        i += 1
                elif kw == 'behind':
                    while (i < n and tokens[i] != ','
                           and tokens[i] not in keywords):
                        result['behind'].append(tokens[i])
                        i += 1
                        if i < n and tokens[i] == ',':
                            i += 1
                elif kw == 'zorder':
                    if i < n and tokens[i] not in keywords:
                        val = tokens[i]
                        try:
                            result['zorder'] = int(val)
                        except ValueError:
                            result['warnings'].append(
                                f'zorder 值无效: {val!r}'
                            )
                        i += 1
            else:
                image_parts.append(tok)
                i += 1

        result['image'] = ' '.join(image_parts)
        return result

    # ---------- python: 块收集 ----------

    def _collect_python_block(self, line: LogicalLine) -> str:
        result = []

        def walk(nodes, base_indent):
            for node in nodes:
                rel = max(0, node.indent - base_indent)
                text = node.text.lstrip()
                if text.strip():
                    text = text.rstrip()
                    result.append(' ' * rel + text)
                walk(node.subblock, base_indent)

        if line.subblock:
            base_indent = min(child.indent for child in line.subblock)
            walk(line.subblock, base_indent)

        return '\n'.join(result)

    # ---------- if 链消费 ----------

    def _consume_if_chain(self, lines: list, start_i: int,
                          if_node: IfNode) -> int:
        j = start_i + 1
        base_indent = lines[start_i].indent

        while j < len(lines):
            nxt = lines[j]
            if nxt.indent != base_indent:
                break
            nxt_text = nxt.text.strip()

            m = self._RE_ELIF_HEAD.match(nxt_text)
            if m:
                colon = _scan_toplevel_colon(nxt_text, m.end())
                if colon == -1:
                    self.errors.append(
                        f"{nxt.filename}:{nxt.line_number}: "
                        f"elif 条件缺少顶层冒号"
                    )
                    break
                cond = nxt_text[m.end():colon].strip()
                if_node.branches.append(
                    (cond, self._parse_block(nxt.subblock))
                )
                j += 1
                continue

            if self._RE_ELSE_HEAD.match(nxt_text):
                if_node.branches.append(
                    ('', self._parse_block(nxt.subblock))
                )
                j += 1

            break

        return j

    # ---------- menu 解析 ----------

    def _parse_menu(self, lines: list) -> tuple:
        caption = ''
        options = []
        i = 0
        while i < len(lines):
            line = lines[i]
            text = line.text.strip()

            opt = self._parse_menu_option(line, text)
            if opt is not None:
                opt.block = self._parse_block(line.subblock)
                options.append(opt)
                i += 1
                continue

            m = self._RE_SAY.match(text)
            if m:
                who = m.group(1) or ''
                if who not in self._IGNORED_SAY_HEADS:
                    cap = _pick_str(m, 2, 3)
                    caption = (caption + '\n' + cap) if caption else cap
                i += 1
                continue

            i += 1

        return caption, options

    def _parse_menu_option(self, line: LogicalLine,
                           text: str) -> Optional[MenuOptionNode]:
        m = re.match(r'^' + _STR, text)
        if not m:
            return None
        opt_text = _pick_str(m, 1, 2)
        i = m.end()

        while i < len(text) and text[i] in ' \t':
            i += 1

        condition = ''
        if text.startswith('if', i) and (
            i + 2 == len(text) or not (text[i + 2].isalnum()
                                       or text[i + 2] == '_')
        ):
            colon = _scan_toplevel_colon(text, i + 2)
            if colon == -1:
                return None
            condition = text[i + 2:colon].strip()
            if not condition:
                self.errors.append(
                    f"{line.filename}:{line.line_number}: "
                    f"菜单选项的 if 条件为空"
                )
        else:
            colon = _scan_toplevel_colon(text, i)
            if colon == -1:
                return None

        return MenuOptionNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            text=opt_text,
            condition=condition,
        )

    # ---------- Character 参数（扫描器） ----------

    @staticmethod
    def _is_character_type(value_type: str) -> bool:
        """
        是不是"角色定义"的构造函数。

        Ren'Py 的角色类型不止 `Character`：`DynamicCharacter`（可变名字）、
        `ADVCharacter` / `NVLCharacter` / `FlagCharacter` / `RandomCharacter`，
        以及 `renpy.Character` 这种带前缀的写法。
        旧实现只认 `Character` 和 `*.Character`，导致真实项目里
        **一整批 DynamicCharacter 角色被静默丢掉**，接着引用它们的每一句
        台词都被报成"说话人不在角色表里"（实测 703 条误报）。
        统一按"以 Character 结尾"识别。
        """
        if not value_type:
            return False
        return value_type == 'Character' or value_type.endswith('Character')

    @staticmethod
    def _is_dynamic_character(value_type: str) -> bool:
        """DynamicCharacter 的第一个位置参数是**名字变量**，不是显示名。"""
        return 'Dynamic' in (value_type or '')

    def _parse_character_args(self, node: DefineNode, args_str: str):
        pos = self._RE_POS_ARG.match(args_str)
        if pos:
            first = _pick_str(pos, 1, 2)
            node.positional.append(first)
            if self._is_dynamic_character(node.value_type):
                # `DynamicCharacter('jc_name', ...)`：存变量名，显示名
                # 之后从 `default jc_name = "「蒋忱」"` 里解析。
                node.name_var = first
            elif 'dynamic' in node.kwargs and node.kwargs['dynamic'] not in (
                    'False', 'false', '0', ''):
                node.name_var = first
            else:
                node.display_name = first

        i = 0
        n = len(args_str)
        while i < n:
            ch = args_str[i]
            if ch in ' \t,':
                i += 1
                continue

            m = re.match(r'(\w+)\s*=\s*', args_str[i:])
            if not m:
                i += 1
                continue

            key = m.group(1)
            i += m.end()

            if i >= n:
                break

            val = self._scan_arg_value(args_str, i)
            if val is None:
                break
            value_text, new_i = val
            if new_i <= i:
                i += 1
                continue
            i = new_i

            # 全部关键字参数原样留存，UI/资源表需要哪个就取哪个，
            # 不必为了一个新字段回来改解析器。
            node.kwargs[key] = value_text

            if key in ('color', 'who_color'):
                node.color = value_text
            elif key == 'what_color':
                node.what_color = value_text
            elif key == 'image':
                node.image_tag = value_text

        # `Character("x", dynamic=True)`：位置参数同样是名字变量
        dyn = node.kwargs.get('dynamic', '')
        if dyn and dyn not in ('False', 'false', '0', 'None', '') \
                and not node.name_var and node.display_name:
            node.name_var = node.display_name
            node.display_name = ''

    def _scan_arg_value(self, text: str, start: int):
        n = len(text)
        if start >= n:
            return None
        ch = text[start]

        if ch in ('"', "'"):
            end, closed = _skip_string(text, start)
            if not closed:
                return None
            return text[start + 1:end - 1], end

        if ch in '([{':
            close = _scan_balanced_close(text, start)
            if close == -1:
                return None
            return text[start:close + 1], close + 1

        j = start
        depth = 0
        while j < n:
            c = text[j]
            if c == '\\':
                j += 2
                continue
            if c in ('"', "'"):
                j, _ = _skip_string(text, j)
                continue
            if c in '([{':
                depth += 1
            elif c in ')]}':
                if depth == 0:
                    break
                depth -= 1
            elif c == ',' and depth == 0:
                break
            j += 1
        return text[start:j].strip(), j


# ====================================================================
#  第三层：数据结构
# ====================================================================

@dataclass
class TransformDefinition:
    """脚本里 `transform` 块解析出来的自定义变换。"""
    name: str
    params: str = ''
    body: str = ''
    doc: str = ''
    source_file: str = ''
    source_line: int = 0
    used_count: int = 0


@dataclass
class CharacterDefinition:
    variable_name: str
    display_name: str = ''
    color: str = ''
    what_color: str = ''
    image_tag: str = ''
    # DynamicCharacter 的名字变量（显示名从同名 default 里取）
    name_var: str = ''
    source_file: str = ''
    source_line: int = 0
    dialogue_count: int = 0
    # 其余定义细节（kind / voice_tag / side_image / *_prefix / *_suffix …）
    kind: str = ''
    voice_tag: str = ''
    side_image: str = ''
    what_prefix: str = ''
    what_suffix: str = ''
    who_prefix: str = ''
    who_suffix: str = ''
    extra: dict = field(default_factory=dict)


@dataclass
class DialogueLine:
    character_var: str = ''
    text: str = ''
    label: str = ''
    source_file: str = ''
    source_line: int = 0
    is_menu_option: bool = False
    menu_condition: str = ''
    translation_unit: str = ''


@dataclass
class TranslationUnit:
    identifier: str = ''
    label: str = ''
    source_file: str = ''
    source_line: int = 0
    lines: list = field(default_factory=list)
    is_menu_option: bool = False
    menu_condition: str = ''


@dataclass
class ParseResult:
    characters: dict = field(default_factory=dict)
    dialogues: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)
    # 脚本里自定义的 transform：{name: TransformDefinition}
    transforms: dict = field(default_factory=dict)
    # 脚本里 `image name = expr` 声明的图像：{image_name: {...}}
    # 这是"哪些图真的存在"的权威来源（比按文件名猜可靠）。
    images: dict = field(default_factory=dict)
    # 字符串常量：{变量名: 值}，用于解析 DynamicCharacter 的显示名
    string_defaults: dict = field(default_factory=dict)
    # 含 `menu` 的 label 集合（`menu:` 没有标题也算）——校验用：
    # 判断"有菜单选项却没有 menu 行"必须按这个来，不能按 caption 是否为空。
    menu_labels: set = field(default_factory=set)
    translation_units: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # 按源码顺序保留的全部演出语句（core.Statement）。
    # dialogues 只留对话，做不了"句子级演出配置"——
    # 说这句台词时舞台上站着谁、放着什么音乐，只有顺序能回答。
    # statement_labels 与 statements 一一对应，记录每条语句所属 label。
    statements: list = field(default_factory=list)
    statement_labels: list = field(default_factory=list)
    # 与 statements 一一对应的"所属菜单选项文本"，'' 表示不在菜单选项内。
    # 图与 Beat 表都要知道一条 jump 是从哪个选项里出去的（边上的
    # option_text / 表里的 Condition 语义），所以和 statement_labels
    # 一样用平行数组承载，避免污染 Statement 的相等与哈希契约。
    statement_menu_options: list = field(default_factory=list)


# ====================================================================
#  第三层：ParseResultBuilder
# ====================================================================

class ParseResultBuilder:
    def build(self, ast_nodes: list) -> ParseResult:
        result = ParseResult()
        # 先扫一遍字符串常量：DynamicCharacter 的显示名定义在
        # `default x_name = "..."`，而它可能出现在 define 之后。
        self._collect_string_defaults(ast_nodes, result)
        self._walk(ast_nodes, '', result)
        self._compute_counts(result)
        return result

    @staticmethod
    def _is_char_define(node) -> bool:
        return RenPyParser._is_character_type(
            getattr(node, 'value_type', '') or '')

    def _collect_string_defaults(self, nodes: list, result: ParseResult):
        """收集 `default/define x = "字面量"`（含嵌套块）。"""
        for node in nodes or ():
            if isinstance(node, DefineNode) and node.target:
                value = _string_literal(node.rhs)
                if value is not None:
                    result.string_defaults.setdefault(node.target, value)
            if isinstance(node, LabelNode):
                self._collect_string_defaults(node.block, result)
            elif isinstance(node, (IfNode,)):
                for _cond, block in node.branches:
                    self._collect_string_defaults(block, result)
            elif isinstance(node, MenuNode):
                for opt in node.options:
                    self._collect_string_defaults(opt.block, result)

    def _walk(self, nodes: list, current_label: str, result: ParseResult,
              accum_cond: str = '', menu_option: str = ''):
        def add_stmt(stmt):
            """
            收集语句，同时记下它属于哪个 label / 哪个菜单选项。

            Statement 本身不带 label（那是"内容指纹"契约的一部分），
            所以归属信息放在平行的 statement_labels /
            statement_menu_options 里。
            """
            result.statements.append(stmt)
            result.statement_labels.append(current_label)
            result.statement_menu_options.append(menu_option)

        for node in nodes:
            if isinstance(node, LabelNode):
                result.labels[node.name] = (node.filename, node.line_number)
                self._walk(node.block, node.name, result)

            elif isinstance(node, DefineNode):
                if ParseResultBuilder._is_char_define(node):
                    display = node.display_name
                    if not display and node.name_var:
                        display = result.string_defaults.get(node.name_var, '')
                    result.characters[node.target] = CharacterDefinition(
                        variable_name=node.target,
                        display_name=display or node.target,
                        color=node.color,
                        what_color=node.what_color,
                        image_tag=node.image_tag,
                        name_var=node.name_var,
                        source_file=node.filename,
                        source_line=node.line_number,
                        kind=node.kwargs.get('kind', ''),
                        voice_tag=node.kwargs.get('voice_tag', ''),
                        side_image=(node.kwargs.get('side_image')
                                    or node.kwargs.get('side_image_prefix')
                                    or ''),
                        what_prefix=node.kwargs.get('what_prefix', ''),
                        what_suffix=node.kwargs.get('what_suffix', ''),
                        who_prefix=node.kwargs.get('who_prefix', ''),
                        who_suffix=node.kwargs.get('who_suffix', ''),
                        extra=dict(node.kwargs),
                    )

            elif isinstance(node, LayeredImageNode):
                if node.name and node.name not in result.images:
                    result.images[node.name] = {
                        'name': node.name,
                        'expr': node.image_format or '',
                        'path': None,
                        'solid': None,
                        'layered': True,
                        'attrs': list(node.attributes),
                        'source_file': node.filename,
                        'source_line': node.line_number,
                    }

            elif isinstance(node, ImageDefNode):
                # 后声明的不覆盖先声明的（Ren'Py 里也是先定义先生效）
                if node.name and node.name not in result.images:
                    result.images[node.name] = {
                        'name': node.name,
                        'expr': node.expr,
                        'path': _image_path_from_expr(node.expr),
                        'solid': _image_solid_from_expr(node.expr),
                        'source_file': node.filename,
                        'source_line': node.line_number,
                    }

            elif isinstance(node, TransformNode):
                # 后写的不覆盖先写的：同名 transform 以首次定义为准，
                # 与 Ren'Py "先定义先生效" 的直觉一致，也避免重定义把
                # 有注释的那份挤掉。
                if node.name not in result.transforms:
                    result.transforms[node.name] = TransformDefinition(
                        name=node.name,
                        params=node.params,
                        body=node.body,
                        doc=node.doc,
                        source_file=node.filename,
                        source_line=node.line_number,
                    )

            elif isinstance(node, SayNode):
                result.dialogues.append(DialogueLine(
                    character_var=node.who,
                    text=node.what,
                    label=current_label,
                    source_file=node.filename,
                    source_line=node.line_number,
                ))
                add_stmt(Statement.make_say(
                    node.who, node.what, accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, ExtendNode):
                result.dialogues.append(DialogueLine(
                    character_var='',
                    text=node.text,
                    label=current_label,
                    source_file=node.filename,
                    source_line=node.line_number,
                ))
                add_stmt(Statement.make_extend(
                    node.text, accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, MenuNode):
                if current_label:
                    result.menu_labels.add(current_label)
                if node.caption:
                    result.dialogues.append(DialogueLine(
                        character_var='',
                        text=node.caption,
                        label=current_label,
                        source_file=node.filename,
                        source_line=node.line_number,
                    ))
                    add_stmt(Statement.make_menu(
                        node.caption, accum_cond,
                        source=(node.filename, node.line_number)))
                for opt in node.options:
                    result.dialogues.append(DialogueLine(
                        character_var='',
                        text=opt.text,
                        label=current_label,
                        source_file=opt.filename,
                        source_line=opt.line_number,
                        is_menu_option=True,
                        menu_condition=opt.condition,
                    ))
                    add_stmt(Statement.make_menu_option(
                        opt.text, self._combine(accum_cond, opt.condition),
                        source=(opt.filename, opt.line_number)))
                    self._walk(opt.block, current_label, result,
                               self._combine(accum_cond, opt.condition),
                               opt.text)

            elif isinstance(node, ShowNode):
                add_stmt(Statement.make_show(
                    node.image, at=node.at, as_=node.as_, with_=node.with_,
                    zorder=node.zorder, onlayer=node.onlayer,
                    behind=node.behind, is_expression=node.is_expression,
                    condition=accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, SceneNode):
                add_stmt(Statement.make_scene(
                    node.image, at=node.at, with_=node.with_,
                    onlayer=node.onlayer,
                    is_expression=node.is_expression,
                    condition=accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, HideNode):
                add_stmt(Statement.make_hide(
                    node.image, with_=node.with_, condition=accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, AudioNode):
                add_stmt(Statement.make_play(
                    node.action, node.channel, node.name,
                    loop=node.loop, fadein=node.fadein,
                    fadeout=node.fadeout, condition=accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, VoiceNode):
                add_stmt(Statement.make_voice(
                    node.name, accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, PythonNode):
                add_stmt(Statement.make_python(
                    node.code, accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, CommentNode):
                # 只保留 label 内的注释——顶层注释属于文件头/定义区，
                # 不属于任何演出流，混进表格反而会误导文案。
                if current_label:
                    add_stmt(Statement.make_comment(
                        node.text, accum_cond,
                        source=(node.filename, node.line_number)))

            elif isinstance(node, RawNode):
                if current_label:
                    add_stmt(Statement.make_raw(
                        node.text, raw_kind=node.raw_kind,
                        condition=accum_cond,
                        source=(node.filename, node.line_number)))

            elif isinstance(node, JumpNode):
                add_stmt(Statement.make_jump(
                    node.target, accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, CallNode):
                add_stmt(Statement.make_call(
                    node.target, accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, ReturnNode):
                add_stmt(Statement.make_return(
                    accum_cond,
                    source=(node.filename, node.line_number)))

            elif isinstance(node, IfNode):
                # else 分支要写成 `not (前面的条件)`：以前直接给空条件，
                # 导致"else 里的 jump/台词"在图上是无条件边、在表里
                # Condition 列空白——和 GraphBuilder 的语义不一致。
                # 现在两条路径（AST 图 / Statement 图）用同一套组合规则。
                prev_conds = []
                for cond, block in node.branches:
                    if cond:
                        child_cond = self._combine(accum_cond, cond)
                        prev_conds.append(cond)
                    else:
                        neg = ' or '.join(f'({c})' for c in prev_conds)
                        child_cond = (
                            f"({accum_cond}) and not ({neg})"
                            if accum_cond else f"not ({neg})"
                        )
                    self._walk(block, current_label, result, child_cond,
                               menu_option)

    @staticmethod
    def _combine(c1: str, c2: str) -> str:
        """条件累积，与 GraphBuilder._combine 同形（都形如 (a) and (b)）。"""
        if not c1:
            return c2
        if not c2:
            return c1
        return f"({c1}) and ({c2})"

    @staticmethod
    def _compute_counts(result: ParseResult):
        counts = {}
        for d in result.dialogues:
            if d.character_var:
                counts[d.character_var] = counts.get(d.character_var, 0) + 1
        for var, ch in result.characters.items():
            ch.dialogue_count = counts.get(var, 0)


# ====================================================================
#  第三层：GraphBuilder
# ====================================================================

class GraphBuilder:
    def __init__(self, graph):
        self.graph = graph
        self._label_to_nid: dict = {}
        self.warnings: list = []
        self._existing_edges: set = set()

    def build(self, ast_nodes: list, clear_graph: bool = True):
        """AST 入口（保留给直接吃 AST 的调用方与既有测试）。"""
        self.warnings = []

        # 本次 AST 里的 label 名：既用于 clear_graph 的节点清理，
        # 也用于限定 operations 的清空范围（[FIX-46]）。
        new_label_names = set()
        self._collect_label_names(ast_nodes, new_label_names)

        self._prepare(new_label_names, clear_graph)

        seen_labels: dict = {}
        self._collect_labels(ast_nodes, seen_labels)
        self._extract_edges(ast_nodes)

    # ---------- Statement 入口（图与 Beat 同源） ----------

    def build_from_parse_result(self, parse_result, clear_graph: bool = True):
        """
        从 ParseResult 建图 —— **与剧本表(Beat) 吃同一份 IR**。

        以前图直接吃 AST、表吃 Statement，两套 walk 各自累积条件、各自
        判断菜单归属，时间一长必然漂移（同一个 if 在图上是 A、在表里是 B）。
        现在统一为"ParseResult 是唯一中间层"：图与表都只从它派生。

        AST 入口 build() 保留，供显式传 AST 的调用方与既有测试使用。
        """
        labels = getattr(parse_result, 'labels', None) or {}
        return self.build_from_statements(
            parse_result.statements,
            labels,
            statement_labels=getattr(parse_result, 'statement_labels', None),
            statement_menu_options=getattr(
                parse_result, 'statement_menu_options', None),
            clear_graph=clear_graph,
        )

    def build_from_statements(self, statements, label_positions,
                              statement_labels=None,
                              statement_menu_options=None,
                              clear_graph: bool = True):
        """
        Statement 流 → 图。

        label_positions: {label_name: (file, line)}，来自 ParseResult.labels。
        statement_labels / statement_menu_options: 与 statements 平行的归属数组。
        """
        from storygraph.core.statement import StatementKind

        self.warnings = []
        label_positions = label_positions or {}
        statements = list(statements or [])
        n = len(statements)
        if statement_labels is None:
            statement_labels = [''] * n
        if statement_menu_options is None:
            statement_menu_options = [''] * n

        new_label_names = set(label_positions)
        self._prepare(new_label_names, clear_graph)

        # 建/更新 label 节点
        for name, pos in label_positions.items():
            nid = self._label_to_nid.get(name)
            if nid is None or nid not in self.graph.nodes:
                nid = self.graph.add_node(name, 200, 200)
                self._label_to_nid[name] = nid
            node = self.graph.nodes[nid]
            try:
                node.file_path, node.line_number = pos
            except (TypeError, ValueError):
                pass

        for i, st in enumerate(statements):
            label = statement_labels[i] if i < len(statement_labels) else ''
            option = (statement_menu_options[i]
                      if i < len(statement_menu_options) else '')
            kind = getattr(st, 'kind', '')
            payload = getattr(st, 'payload', None) or {}
            cond = getattr(st, 'condition', '') or ''

            if kind == StatementKind.JUMP:
                self._add_edge(label, payload.get('target', ''), 'jump',
                               cond, option)
            elif kind == StatementKind.CALL:
                self._add_edge(label, payload.get('target', ''), 'call',
                               cond, option)
            elif kind == StatementKind.PYTHON:
                nid = self._label_to_nid.get(label)
                if nid is not None:
                    node = self.graph.nodes.get(nid)
                    if node is not None:
                        node.operations.append(payload.get('code', ''))

    def _prepare(self, new_label_names: set, clear_graph: bool):
        """建图前的公共准备：清节点、建索引、清 operations、重置边去重表。"""
        if clear_graph:
            to_remove = [
                nid for nid, n in self.graph.nodes.items()
                if n.label_name not in new_label_names
            ]
            for nid in to_remove:
                self.graph.remove_node(nid)

        self._label_to_nid = {
            n.label_name: nid for nid, n in self.graph.nodes.items()
        }

        # [FIX-46] 只清空即将被重建的节点的 operations。
        for n in self.graph.nodes.values():
            if n.label_name in new_label_names:
                n.operations = []

        self._existing_edges = set()
        for e in self.graph.edges.values():
            self._existing_edges.add((
                e.from_id, e.to_id, e.type, e.condition, e.option_text
            ))

    def _collect_label_names(self, nodes: list, target: set):
        for node in nodes:
            if isinstance(node, LabelNode):
                target.add(node.name)
                self._collect_label_names(node.block, target)

    def _collect_labels(self, nodes: list, seen_labels: dict):
        for node in nodes:
            if not isinstance(node, LabelNode):
                continue

            if node.name in seen_labels:
                prev = seen_labels[node.name]
                self.warnings.append(
                    f"重复 label '{node.name}'："
                    f"{node.filename}:{node.line_number} 与 "
                    f"{prev[0]}:{prev[1]}"
                )
            else:
                seen_labels[node.name] = (node.filename, node.line_number)

            existing_nid = self._label_to_nid.get(node.name)
            if existing_nid is None or existing_nid not in self.graph.nodes:
                nid = self.graph.add_node(node.name, 200, 200)
                self._label_to_nid[node.name] = nid
                existing_nid = nid

            existing = self.graph.nodes[existing_nid]
            existing.file_path = node.filename
            existing.line_number = node.line_number

            self._collect_labels(node.block, seen_labels)

    def _extract_edges(self, nodes: list, accum_cond: str = '',
                       current_label: str = '', option_text: str = ''):
        for node in nodes:
            if isinstance(node, LabelNode):
                self._extract_edges(node.block, '', node.name, '')

            elif isinstance(node, JumpNode):
                self._add_edge(current_label, node.target, 'jump',
                               accum_cond, option_text)

            elif isinstance(node, CallNode):
                self._add_edge(current_label, node.target, 'call',
                               accum_cond, option_text)

            elif isinstance(node, IfNode):
                prev_conds = []
                for cond, block in node.branches:
                    if cond:
                        new_cond = self._combine(accum_cond, cond)
                        prev_conds.append(cond)
                    else:
                        neg = " or ".join(f"({c})" for c in prev_conds)
                        new_cond = (
                            f"({accum_cond}) and not ({neg})"
                            if accum_cond else f"not ({neg})"
                        )
                    self._extract_edges(block, new_cond, current_label,
                                        option_text)

            elif isinstance(node, MenuNode):
                for opt in node.options:
                    opt_cond = self._combine(accum_cond, opt.condition)
                    self._extract_edges(opt.block, opt_cond, current_label,
                                        opt.text)

            elif isinstance(node, PythonNode):
                # 所有 PythonNode 的 code 都会被收集（含条件块内），
                # 这是 v3 [FIX-6] 有意为之；operations 目前不带条件
                # 前缀，供下游按需处理。
                # 风格统一：以 is not None 判断，与 FIX-45 一致。
                nid = self._label_to_nid.get(current_label)
                if nid is not None:
                    n = self.graph.nodes.get(nid)
                    if n is not None:
                        n.operations.append(node.code)

    def _add_edge(self, from_label: str, to_label: str,
                  etype: str, condition: str, option_text: str = ''):
        if not from_label or not to_label:
            return

        from_id = self._label_to_nid.get(from_label)
        to_id = self._label_to_nid.get(to_label)

        if to_id is None:
            self.warnings.append(
                f"未定义的跳转目标 '{to_label}'（来自 '{from_label}'）"
            )
            nid = self.graph.add_node(to_label, 200, 200)
            self._label_to_nid[to_label] = nid
            to_id = nid

        # [FIX-45] 用 is None 判断，不依赖 node id 从 1 开始这一隐含约定
        if from_id is None or to_id is None:
            return

        key = (from_id, to_id, etype, condition, option_text)
        if key in self._existing_edges:
            return
        self._existing_edges.add(key)

        self.graph.add_edge(from_id, to_id, etype=etype,
                            condition=condition,
                            option_text=option_text)

    @staticmethod
    def _combine(c1: str, c2: str) -> str:
        if not c1:
            return c2
        if not c2:
            return c1
        return f"({c1}) and ({c2})"

# ====================================================================
#  第三层：TranslationUnitGenerator
# ====================================================================

class TranslationUnitGenerator:
    """
    翻译单元生成器。

    标识符格式：{make_translation_uid(label, md5(content)[:8])}
    冲突时追加 _1, _2, ...

    [FIX-48] 标识符前缀统一由 core.uid.make_translation_uid 生成，
    避免与项目内其他模块的翻译单元命名规则漂移。
    """

    def __init__(self):
        self._used: set = set()

    def generate(self, parse_result: ParseResult) -> list:
        self._used = set()
        units = []

        for d in parse_result.dialogues:
            if not d.label:
                continue

            if d.is_menu_option:
                content = f"menu\0{d.text}"
                is_menu = True
            else:
                content = f"say\0{d.character_var}\0{d.text}"
                is_menu = False

            ident = self._unique_id(d.label, content)
            unit = TranslationUnit(
                identifier=ident,
                label=d.label,
                source_file=d.source_file,
                source_line=d.source_line,
                lines=[(d.character_var, d.text)],
                is_menu_option=is_menu,
                menu_condition=d.menu_condition,
            )
            units.append(unit)
            d.translation_unit = ident

        parse_result.translation_units = units
        return units

    def _unique_id(self, label: str, content: str) -> str:
        h = hashlib.md5(content.encode('utf-8')).hexdigest()[:8]
        base = make_translation_uid(label, h)
        ident = base
        counter = 1
        while ident in self._used:
            ident = f"{base}_{counter}"
            counter += 1
        self._used.add(ident)
        return ident


# ====================================================================
#  统一入口：增量分析器
# ====================================================================

class IncrementalRenPyAnalyzer:
    """
    增量式双输出分析器。

    缓存策略：
      · 缓存元组： (mtime, size, ast_nodes, errors, warnings)
      · mtime + size 未变 → 复用 AST 与错误/警告
      · 已删除文件的缓存自动清理
      · 输出层（Graph / ParseResult / TU）每次全量重建

    增量粒度：**文件级**。同一文件任一字节变化即整文件重解，
    不做行级脏检查。若将来需要更细粒度，需要重新设计缓存 key
    （例如以"块"为单位），当前结构未预留该能力。

    Graph 清理范围：新 AST 中不存在的 label 节点会被移除；
    同一 (from, to, type, condition, option_text) 的边只加一次。
    """

    def __init__(self):
        self._file_cache: dict = {}

    def analyze(self, files, graph=None, clear_graph: bool = True) -> tuple:
        seen_keys = set()
        unique_files = []
        for fp in files:
            fp_path = Path(fp)
            if not fp_path.exists():
                continue
            try:
                fp_key = str(fp_path.resolve())
            except OSError:
                continue
            if fp_key in seen_keys:
                continue
            seen_keys.add(fp_key)
            unique_files.append((fp_path, fp_key))

        current_files = set()
        all_ast = []
        all_errors = []
        all_warnings = []

        for fp_path, fp_key in unique_files:
            current_files.add(fp_key)

            try:
                st = fp_path.stat()
            except OSError:
                continue

            cached = self._file_cache.get(fp_key)
            if (cached and cached[0] == st.st_mtime
                    and cached[1] == st.st_size):
                all_ast.extend(cached[2])
                all_errors.extend(cached[3])
                all_warnings.extend(cached[4])
                continue

            parser = RenPyParser()
            nodes = parser.parse(fp_path)
            file_errors = list(parser.errors)
            file_warnings = list(parser.warnings)
            all_errors.extend(file_errors)
            all_warnings.extend(file_warnings)
            self._file_cache[fp_key] = (
                st.st_mtime, st.st_size, nodes, file_errors, file_warnings
            )
            all_ast.extend(nodes)

        for k in list(self._file_cache.keys()):
            if k not in current_files:
                del self._file_cache[k]

        parse_result = ParseResultBuilder().build(all_ast)
        parse_result.errors.extend(all_errors)
        parse_result.warnings.extend(all_warnings)

        TranslationUnitGenerator().generate(parse_result)

        # 图从 ParseResult（Statement 流）建，而不是再扫一遍 AST：
        # 图与剧本表从此同源，条件累积 / 菜单归属 / 变量操作三处语义
        # 只有一份实现（一.1）。AST 入口 build() 仍为兼容保留。
        if graph is not None:
            gb = GraphBuilder(graph)
            gb.build_from_parse_result(parse_result, clear_graph=clear_graph)
            parse_result.warnings.extend(gb.warnings)
        else:
            # 调用方没给 graph：新建一个。
            # 注意这里是 `graph is None`，不是 `Graph is not None`
            # ——后者检查的是类能否导入，与本分支语义无关。
            graph = Graph()
            gb = GraphBuilder(graph)
            gb.build_from_parse_result(parse_result, clear_graph=True)
            parse_result.warnings.extend(gb.warnings)

        return parse_result, graph

    def invalidate(self, filepath):
        key = str(Path(filepath).resolve())
        self._file_cache.pop(key, None)

    def clear(self):
        self._file_cache.clear()

    def stats(self) -> dict:
        return {
            'cached_files': len(self._file_cache),
            'files': sorted(self._file_cache.keys()),
        }


# ====================================================================
#  兼容旧接口
# ====================================================================

_default_analyzer = None


def parse_renpy_file(filepath, graph):
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = IncrementalRenPyAnalyzer()
    _result, graph = _default_analyzer.analyze(
        [filepath], graph=graph, clear_graph=False
    )
    return graph


def batch_import_chapters(filepaths, graph):
    """批量把多个 .rpy 解析进同一张图（`clear_graph=False` 累加节点）。

    比循环调用 `parse_renpy_file` 更省：一次 analyzer 调用把所有文件的
    AST 合起来建图，且 `（mtime, size）` 缓存命中时直接复用已解析结果。
    `clear_graph=False` 保证多次导入 / 跨文件 label 互不覆盖、不重复建节点。
    """
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = IncrementalRenPyAnalyzer()
    _default_analyzer.analyze(list(filepaths), graph=graph, clear_graph=False)
    return graph


# ====================================================================
#  翻译文件解析
# ====================================================================

def parse_translation_files(tl_dir) -> dict:
    """
    解析 tl/ 目录，返回 {unit_id: {'original', 'translated', 'language'}}。

    [FIX-41] `translate <lang> strings:` 块里的 old/new 对**每一对**
    生成独立 unit id，形式为 `{lang}.strings_{md5(old)[:8]}`。
    同一 strings 块内多组 old/new 不再互相覆盖。

    [FIX-44] `translate <lang> python:` 不产生 unit id，
    块内 old/new 被忽略（python 翻译不是对话单元）。

    注意：`translate <lang> style <name>:` 不会命中 re_translate
    （第二 token 后不是冒号），走 re_translate_any 分支被正确处理，
    无需在此显式排除 'style'。
    """
    result = {}
    tl_dir = Path(tl_dir)
    if not tl_dir.exists():
        return result

    re_translate = re.compile(r'^\s*translate\s+([\w-]+)\s+([\w-]+)\s*:')
    re_translate_any = re.compile(r'^\s*translate\b')
    re_old = re.compile(r'^\s*#\s*old\s+' + _TR_STR)
    re_new = re.compile(r'^\s*new\s+' + _TR_STR)

    def _pick_named(m):
        v = m.group('dq')
        if v is not None:
            return v
        v = m.group('sq')
        if v is not None:
            return v
        return ''

    for lang_dir in tl_dir.iterdir():
        if not lang_dir.is_dir():
            continue
        language = lang_dir.name
        for fp in lang_dir.glob('*.rpy'):
            try:
                lines = fp.read_text(encoding='utf-8').splitlines()
            except Exception:
                continue

            current_id = None
            current_lang = None
            in_strings_block = False

            for line in lines:
                m = re_translate.match(line)
                if m:
                    lang, ident = m.group(1), m.group(2)
                    if ident == 'strings':
                        # strings 块：unit id 延迟到 old 行生成
                        current_id = None
                        current_lang = lang
                        in_strings_block = True
                    elif ident == 'python':
                        # [FIX-44] python 翻译块不是对话单元
                        current_id = None
                        current_lang = None
                        in_strings_block = False
                    else:
                        current_id = ident
                        current_lang = None
                        in_strings_block = False
                    continue

                if re_translate_any.match(line):
                    # translate python: / translate style foo: 等
                    current_id = None
                    current_lang = None
                    in_strings_block = False
                    continue

                m = re_old.match(line)
                if m:
                    orig = _pick_named(m)
                    if in_strings_block and current_lang:
                        h = hashlib.md5(
                            orig.encode('utf-8')
                        ).hexdigest()[:8]
                        key = f"{current_lang}.strings_{h}"
                        current_id = key
                        result.setdefault(key, {
                            'original': orig,
                            'language': language,
                        })
                    elif current_id:
                        result.setdefault(current_id, {
                            'original': orig,
                            'language': language,
                        })
                    continue

                m = re_new.match(line)
                if m and current_id:
                    result.setdefault(current_id, {})['translated'] = \
                        _pick_named(m)

    return result


# ====================================================================
#  命令行自测
# ====================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python renpy_parser.py <game_dir> [script.rpy ...]")
        sys.exit(0)

    game_dir = Path(sys.argv[1])
    scripts = [Path(p) for p in sys.argv[2:]] or list(game_dir.glob('*.rpy'))

    analyzer = IncrementalRenPyAnalyzer()
    result, graph = analyzer.analyze(scripts)

    print(f"=== 首次解析 ===")
    print(f"文件数:    {len(scripts)}")
    print(f"角色定义:  {len(result.characters)}")
    print(f"对话行数:  {len(result.dialogues)}")
    print(f"标签数:    {len(result.labels)}")
    print(f"翻译单元:  {len(result.translation_units)}")
    print(f"错误:      {len(result.errors)}")
    print(f"警告:      {len(result.warnings)}")

    print(f"\n=== 角色 ===")
    for var, ch in sorted(result.characters.items(),
                          key=lambda x: -x[1].dialogue_count):
        print(f"  {var}: {ch.display_name}  ({ch.dialogue_count} 行)")

    print(f"\n=== 翻译单元（前5）===")
    for tu in result.translation_units[:5]:
        print(f"  {tu.identifier}  @ {tu.label}")

    if graph is not None:
        print(f"\n=== 控制流 ===")
        print(f"节点: {len(graph.nodes)}, 边: {len(graph.edges)}")

    result2, graph2 = analyzer.analyze(scripts, graph=graph)
    print(f"\n=== 二次解析（缓存命中）===")
    print(f"错误:      {len(result2.errors)}  (应与首次一致)")
    print(f"警告:      {len(result2.warnings)}  (应与首次一致)")
    print(f"缓存:      {analyzer.stats()['cached_files']} 个文件")