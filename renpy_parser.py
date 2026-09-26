"""
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
    from models import Graph
except ImportError:
    Graph = None

from core.uid import make_translation_uid


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


class RenPyLexer:
    """将 .rpy 源文件转换为逻辑行嵌套树。"""

    def __init__(self):
        self.errors: list = []

    def tokenize(self, filepath) -> list:
        filepath = Path(filepath)
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

            if not stripped or stripped.startswith('#'):
                i += 1
                continue

            indent = len(raw) - len(raw.lstrip())
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
                i += 1
                accumulated += ' ' + raw_lines[i].strip()

            accumulated = self._strip_inline_comment(accumulated)

            flat.append((accumulated, line_no, indent))
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
    def _strip_inline_comment(text: str) -> str:
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]

            if ch == '\\':
                i += 2
                continue

            if ch in ('"', "'"):
                end, closed = _skip_string(text, i)
                if not closed:
                    return text
                i = end
                continue

            if ch == '#':
                return text[:i].rstrip()

            i += 1

        return text

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

        for text, line_no, indent in flat:
            node = LogicalLine(
                text=text,
                line_number=line_no,
                indent=indent,
                filename=filename,
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
    display_name: str = ''
    color: str = ''
    what_color: str = ''
    image_tag: str = ''


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


# 字符串正则：双引号分支与单引号分支分离
_STR_DQ = r'"((?:[^"\\]|\\.)*)"'
_STR_SQ = r"'((?:[^'\\]|\\.)*)'"
_STR = f'(?:{_STR_DQ}|{_STR_SQ})'

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


class RenPyParser:
    """递归下降解析器：LogicalLine 树 → ASTNode 列表。"""

    # ---------- 简单前缀正则 ----------
    _RE_LABEL_HEAD = re.compile(r'^label(?=\s)')
    _RE_IF_HEAD = re.compile(r'^if\s+')
    _RE_ELIF_HEAD = re.compile(r'^elif\s+')
    _RE_ELSE_HEAD = re.compile(r'^else\s*:')
    _RE_MENU_HEAD = re.compile(r'^menu\b')
    _RE_DEFINE = re.compile(r'^define\s+([\w.]+)\s*=\s*(.+)$', re.DOTALL)
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
    _RE_EXTEND_HEAD = re.compile(r'^extend\s+')
    _RE_EXTEND_STR = re.compile(r'^extend\s+' + _STR)

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
        while i < len(lines):
            line = lines[i]
            text = line.text.strip()
            node = self._parse_statement(line, text)

            if node is None:
                i += 1
                continue

            if isinstance(node, IfNode):
                i = self._consume_if_chain(lines, i, node)
                nodes.append(node)
                continue

            nodes.append(node)
            i += 1
        return nodes

    def _parse_statement(self, line: LogicalLine, text: str):
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
            )
            if value_type == 'Character' or value_type.endswith('.Character'):
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
            self.warnings.append(
                f"{line.filename}:{line.line_number}: "
                f"jump expression 为动态跳转，控制流图不断言目标"
            )
            return None

        # ---- call expression / call screen ----
        if self._RE_CALL_EXPR.match(text):
            self.warnings.append(
                f"{line.filename}:{line.line_number}: "
                f"call expression 为动态调用，控制流图不断言目标"
            )
            return None

        if self._RE_CALL_SCREEN.match(text):
            # call screen 是 UI 调用，不产生 label 跳转边，静默忽略
            return None

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

    def _parse_character_args(self, node: DefineNode, args_str: str):
        pos = self._RE_POS_ARG.match(args_str)
        if pos:
            node.display_name = _pick_str(pos, 1, 2)

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

            if key in ('color', 'who_color'):
                node.color = value_text
            elif key == 'what_color':
                node.what_color = value_text
            elif key == 'image':
                node.image_tag = value_text

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
class CharacterDefinition:
    variable_name: str
    display_name: str = ''
    color: str = ''
    what_color: str = ''
    image_tag: str = ''
    source_file: str = ''
    source_line: int = 0
    dialogue_count: int = 0


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
    translation_units: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ====================================================================
#  第三层：ParseResultBuilder
# ====================================================================

class ParseResultBuilder:
    def build(self, ast_nodes: list) -> ParseResult:
        result = ParseResult()
        self._walk(ast_nodes, '', result)
        self._compute_counts(result)
        return result

    def _walk(self, nodes: list, current_label: str, result: ParseResult):
        for node in nodes:
            if isinstance(node, LabelNode):
                result.labels[node.name] = (node.filename, node.line_number)
                self._walk(node.block, node.name, result)

            elif isinstance(node, DefineNode):
                if node.value_type == 'Character' or \
                        node.value_type.endswith('.Character'):
                    result.characters[node.target] = CharacterDefinition(
                        variable_name=node.target,
                        display_name=node.display_name or node.target,
                        color=node.color,
                        what_color=node.what_color,
                        image_tag=node.image_tag,
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

            elif isinstance(node, ExtendNode):
                result.dialogues.append(DialogueLine(
                    character_var='',
                    text=node.text,
                    label=current_label,
                    source_file=node.filename,
                    source_line=node.line_number,
                ))

            elif isinstance(node, MenuNode):
                if node.caption:
                    result.dialogues.append(DialogueLine(
                        character_var='',
                        text=node.caption,
                        label=current_label,
                        source_file=node.filename,
                        source_line=node.line_number,
                    ))
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
                    self._walk(opt.block, current_label, result)

            elif isinstance(node, IfNode):
                for _cond, block in node.branches:
                    self._walk(block, current_label, result)

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
        self.warnings = []

        # 本次 AST 里的 label 名：既用于 clear_graph 的节点清理，
        # 也用于限定 operations 的清空范围（[FIX-46]）。
        new_label_names = set()
        self._collect_label_names(ast_nodes, new_label_names)

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
        # 若调用方只传部分文件（例如 parse_renpy_file 兼容接口，
        # 单文件 + clear_graph=False），其他文件节点的 operations
        # 不再被误清空。
        for n in self.graph.nodes.values():
            if n.label_name in new_label_names:
                n.operations = []

        self._existing_edges = set()
        for e in self.graph.edges.values():
            self._existing_edges.add((
                e.from_id, e.to_id, e.type, e.condition, e.option_text
            ))

        seen_labels: dict = {}
        self._collect_labels(ast_nodes, seen_labels)
        self._extract_edges(ast_nodes)

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

        if graph is not None:
            gb = GraphBuilder(graph)
            gb.build(all_ast, clear_graph=clear_graph)
            parse_result.warnings.extend(gb.warnings)
        elif Graph is not None:
            graph = Graph()
            gb = GraphBuilder(graph)
            gb.build(all_ast, clear_graph=True)
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