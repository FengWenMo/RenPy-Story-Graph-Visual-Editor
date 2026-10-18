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

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import (SHOW_INTRO_KEYWORDS,
                                        classify_show_words,
                                        tokenize_show_words)

# ⚠ 2026-10-16：这里原来还有一份 `_NO_IMAGE_INTRO = SHOW_INTRO_KEYWORDS -
#   {'expression'}` —— 它是"哪几个引入型关键字意味着**没有图像名**"的
#   第二份口径（`_parse_show_scene` 自己判断用）。现在这件事由
#   `classify_show_words` 的 `intro_kind` 统一回答（`screen` / `layer`
#   → 没有图像名；`expression` → 走 `is_expression`，语义不同），
#   `scene` 的差异由 `intros=` 参数表达，所以那一份**已删**。
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
from storygraph.core.nest_path import join_nest, option_key
from storygraph.core.statement import (CONTENT_HASH_HEX, Statement,
                                       StatementKind)
from storygraph.parsing.renpy_parser_stmts import StatementScreens
from storygraph.parsing.renpy_nodes import (  # 2026-10-11 拆分：见该模块文档
    ASTNode,
    AudioNode,
    CallNode,
    CameraNode,
    CommentNode,
    DefineNode,
    ExtendNode,
    HideNode,
    IfNode,
    ImageDefNode,
    JumpNode,
    LabelNode,
    LayeredImageNode,
    LoopNode,
    MenuNode,
    MenuOptionNode,
    PythonNode,
    RawNode,
    ReturnNode,
    SayNode,
    SceneNode,
    ShowNode,
    TopLevelRegionNode,
    TransformNode,
    UnknownBlockNode,
    VoiceNode,
    _AUDIO_PARAM_WORDS,
    _find_triple_end,
    _pick_str,
    _scan_balanced_close,
    _scan_toplevel_colon,
    _skip_string,
    split_string_speaker,
    unescape_renpy_string,
)


def read_source_text(path) -> str:
    r"""
    读一份源文件，并**吃掉开头的 UTF-8 BOM**（2026-10-08）。

    为什么必须吃（实测）：带 BOM 的 `.rpy` 第一行是 `\ufefflabel a:`，
    而标签判据是 `^label\b` —— 认不出来，于是整个文件的语句**一条都解析
    不到**（静默全丢；用户只看到"导入后什么都没有"，没有任何报错）。
    BOM 是编码标记、不是内容：剥掉它不改变任何一行的语义。

    ⚠ 只在**最开头**剥一个 —— 正文中间出现 `\ufeff` 是作者的内容，不许动。
    """
    text = Path(path).read_text(encoding='utf-8')
    if text.startswith('\ufeff'):
        text = text[1:]
    return text


# ====================================================================
#  模块级辅助：字符串 / 括号 / 顶层冒号扫描
# ====================================================================









# ====================================================================
#  第一层：Lexer
# ====================================================================

def _top_level_region(filepath, lines=None) -> list:
    """
    源文件里 **label 之外**的全部内容 → `[(块头, 去基准缩进的块体, 起始行号)]`。

    规则（刻意简单，因为要的是**逐字保真**，不是理解）：

      · 顶层 = 行首没有缩进；
      · 遇到 `label …:` → 跳过它**和它的块体**（那部分由"拍"负责）；
      · 其余顶层行各自起一段，并把后面**缩进的行 + 空行**收进这一段
        （即"这条语句/这个块"的正文）。

    为什么用原文而不是 AST：见 `TopLevelRegionNode` 的 docstring ——
    `screen` 这类带子块的语句**没进 AST**，`define` 这类进了 AST 但原文丢了。
    要"一个字都不丢"，就只能按原文取。

    缩进处理：块体按**最小缩进**整体归零（相对层级保留），渲染时再统一加 4
    —— 与 `_collect_python_block` 同一套口径，两处都这么做才不会有哪一类
    块在往返里越缩越深。

    `lines` 给了就**用它**（不再读盘）：`RenPyParser.parse()` 把"解析时读到的
    那一份"传进来，于是 AST 行号与这段原文来自**同一个快照**。
    三处各读一遍时，只要中间有人改了文件，行号与原文就会错位 —— 那是
    "把 A 的内容写到 B 的位置"，比少保真一点危险得多（见 `core.cst`）。
    """
    if lines is None:
        try:
            lines = read_source_text(filepath).splitlines()
        except (OSError, UnicodeDecodeError):
            return []
    raw = list(lines)

    chunks: list = []
    cur: list = []
    cur_row = 0

    def flush():
        if cur:
            chunks.append(_region_chunk(list(cur), cur_row))

    i, n = 0, len(raw)
    while i < n:
        line = raw[i]
        stripped = line.strip()
        top = line[:1] not in (' ', '\t')
        if not stripped:
            if cur:
                cur.append(line)              # 空行：留在当前段里（块内空行是内容）
            i += 1
            continue
        if top and re.match(r'^label\s', stripped):
            flush()                           # label 及其块体由"拍"负责
            cur = []
            i += 1
            # ⚠ **顶格注释不结束 label**（2026-10-18，审计 P0-3）。
            #
            # 从前这里的判据只有"空行 / 缩进行"—— 于是 label 体中间那条
            # **顶格注释**（Ren'Py 完全合法、真机 `whackamole.rpy` 就这么写）
            # 把 label 的"跳过区间"截断了：注释与它后面的缩进代码被当成
            # 顶层区域收走，渲染时被搬到文件最前面 → 产物里
            # `Unexpected indentation at start of file`。
            #
            # 语义上注释不参与缩进判断：只要**后面还有本块的缩进行**，
            # 它就仍属于块内（与 `_followed_by_indented` 同一口径）。
            # 反之（后面是顶格语句 / 到文件末尾）它是"下一段的标题"，
            # 留给下面那个 comment 分支自己起一段。
            while i < n:
                s = raw[i].strip()
                if not s or raw[i][:1] in (' ', '\t'):
                    i += 1
                    continue
                if s.startswith('#') and _followed_by_indented(raw, i + 1):
                    i += 1
                    continue
                break
            continue
        if top and stripped.startswith('#'):
            # 顶格**注释**：Ren'Py 里注释不参与缩进判断 —— 它可以出现在一个
            # 缩进块的中间。真实项目就是这么写的：
            #
            #     init python:
            #     # 类/对象设置          ← 顶格注释，仍在 init python 块里
            #         class GridCell:
            #
            # 所以"块头以 `:` 结尾"的当前段遇到顶格注释要**收进来**；
            # 否则块体会被劈成两段，渲染时丢掉 `init python:` 这个块头 →
            # 产物里出现一段悬空的缩进（Ren'Py 直接报缩进错）。
            #
            # ⚠ **但"块头以 `:` 收尾"不足以判定"**（2026-10-07 实测，
            # `tools/audit_idempotence.py` 抓到 5 个真实文件中招）：
            #
            #     init python:
            #         ...
            #     # 全局人物页面 screen      ← 作者写的是**下一段的标题**
            #     screen character_gallery_screen():
            #
            # 旧判据把它收进 `init python:` 的块体 → `render_file_block` 给
            # 块体每行加 4 → **每导出一次就多缩进一层**（幂等破掉）。
            #
            # 语义上它**两边都合法**（注释不参与缩进判断），所以这不是"猜
            # 语义"，而是按**排版意图**挑一种，并保证挑法是逐字稳定的。
            # 判据用**向后看**：块体的定义就是"后面还有属于本块的缩进行"——
            # 后面第一条**结构性**行（跳过空行与注释）缩进 → 收进块体；
            # 顶格 / 到底 → 它属于下一段，自己起一段。
            if (cur and _opens_a_block(cur[0].strip())
                    and _followed_by_indented(raw, i + 1)):
                cur.append(line)
            else:
                flush()
                cur, cur_row = [line], i + 1
            i += 1
            continue
        if top:
            flush()
            cur, cur_row = [line], i + 1        # 新的顶层语句起一段
            i += 1
            continue
        if cur:
            cur.append(line)                    # 缩进行属于当前段
        i += 1
    flush()
    # ⚠ **生成标记是工具自己的产物，不是作者的内容**（2026-10-07 实测）。
    #
    # 不在这里摘掉它，它就跟着 `file_blocks` 走到渲染层，而渲染层**开头
    # 又会加一条** → 产物里出现两条；用户再导一次，看到的是"工具在改我的
    # 文件"。第一轮就 2 条，之后稳定在 2 条（不是无界增长，但**幂等已经破了**：
    # "导出一次"与"再导出一次"结果不同）。
    #
    # 为什么敢直接丢：顶格行**各自起一段**，所以标记永远独占它那一段
    # （注释不可能拥有缩进块）；丢掉它只少一段注释，渲染层会补回来。
    from storygraph.core.vocabulary import GENERATOR_MARKER
    return [c for c in chunks if c[0].strip() != GENERATOR_MARKER]


def _opens_a_block(header: str) -> bool:
    """
    这一行是不是"带缩进块的块头"（以 `:` 收尾，且不是 `$` 语句、**不是注释**）。

    ⚠ **注释永远不开块**（2026-10-07 实测，`tools/audit_idempotence.py` 抓到）：

    真实项目里有人用"注释掉整个块"的方式留档：

        #label startpomodoro:
        #    $ renpy.block_rollback()
        #    menu:

    第一句以 `:` 收尾 → 被判成块头 → **后面那一串顶格注释被当成它的块体
    收进同一个 chunk**。而 `render_file_block` 会给块体的**每一行加 4 格
    （因为块体按定义比块头深一层）** → 那串注释被推进去一层。
    第二轮它们已经在 4 格上，判据再判一次……**每导出一轮就动一次**
    （实测 6 个真实文件中招：`pomodorro.rpy` / `script_pomo.rpy` /
    `character_gallery.rpy` / `credits_screen.rpy` / `eyes_tracker.rpy`）。
    """
    h = str(header or '').lstrip()
    if not h or h.startswith('#'):
        return False
    return h.endswith(':') and not h.startswith('$')


def _register_atl_image(result, header, filename, line_no) -> None:
    """
    `image 名:` + 块体（**ATL / 块形式**的图像声明）→ 资源清单里也登记一条。

    为什么（2026-10-19，审计 P1-3 最后一小块）：这种写法一直只是"逐字保真"
    （整块进「文件级保真」），于是资源表里**没有这个图**：策划在「立绘」下拉
    里选不到它，`show 名` 还会被当成"缺资源"。真机对照：`sr_general` 3 处、
    官方 `tutorial` 10 处（`image logo blink:` / `image concert:` …）。

    只登记**名字**（`path` 留空、`atl=True`）：块里的贴图可能一个都没有
    （纯 ATL 动画），也可能是好几个 layer —— 猜一个"主贴图"只会写错，
    所以由"资源表"那一层显示成"ATL 定义（贴图看源文件）"。
    `_RE_IMAGE_DEF`（`image 名 = 表达式`）那条路不受影响（先登记的先赢）。
    """
    m = _RE_IMAGE_ATL_HEAD.match(str(header or '').strip())
    if not m:
        return
    name = ' '.join(m.group('name').split())
    if not name or name in result.images:
        return
    result.images[name] = {
        'name': name,
        'expr': '',
        'path': '',
        'solid': None,
        'atl': True,
        'source_file': filename,
        'source_line': line_no,
    }


def _followed_by_indented(raw: list, start: int) -> bool:
    """
    `raw[start:]` 里第一条**结构性**行是不是缩进行？（`_top_level_region` 用）

    "结构性"与 `_region_chunk` 同一口径：**空行与整行注释都不算结构**
    （Ren'Py / Python 都忽略它们的缩进）。

    这条判据回答的是："那个顶格注释**后面还有没有本块的块体**"——
    有 → 它是块中间的注释；没有 → 它属于下一段（作者写的段落标题）。
    返回 `False` 覆盖"后面只有注释/空行"与"到文件末尾"两种情况。
    """
    for j in range(start, len(raw)):
        s = raw[j].strip()
        if not s or s.startswith('#'):
            continue
        return raw[j][:1] in (' ', '\t')
    return False


def _region_chunk(body: list, line_no: int) -> tuple:
    """
    一段顶层原文 → `(块头, 去基准缩进的块体, 起始行号)`。

    **基准缩进只看"结构性"的行 —— 整行注释不算**（2026-10-05，被门禁抓到）：
    Ren'Py / Python 都忽略整行注释的缩进，真实项目里就有人写

        init python:
            import random
        # 默认变量（会进入 store……）      ← 顶格注释，还在 init python 块里
            def mole_cell(r, c):

    第一版用"所有非空行的最小缩进"当基准 → 被那个顶格注释拉到 0 →
    整块**不再去缩进**，渲染再加 4 之后 `import random` 变成 8 格、
    注释变成 4 格 —— 产物里出现 **unexpected indent**，`init python:`
    整块语法坏掉。是 `PYTHON_SYNTAX_ERROR` 门禁把它拦下来的
    （`tools/journey_check.py` 第 2b/5 步），没有静默写出去。

    缩进比基准还小的行（就是那种顶格注释）**原样保留、不切片** ——
    否则 `ln[base:]` 会把注释文字本身切掉几个字。
    """
    head = body[0].strip()
    rest = [ln.rstrip() for ln in body[1:]]
    structural = [ln for ln in rest
                  if ln.strip() and not ln.lstrip().startswith('#')]
    ref = structural or [ln for ln in rest if ln.strip()]
    if not ref:
        return head, '', line_no
    base = min(len(ln) - len(ln.lstrip()) for ln in ref)
    out = []
    for ln in rest:
        if not ln.strip():
            out.append('')
        elif len(ln) - len(ln.lstrip()) >= base:
            out.append(ln[base:])
        else:
            out.append(ln)                    # 顶格注释：原样带着
    return head, '\n'.join(out).rstrip('\n'), line_no


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
    #: **行尾注释原文**（含 `#`）。原先在词法器里被 `_strip_inline_comment`
    #: 直接丢掉 —— IR 里根本没有这个字段，于是"表 → 新文件"这条路必然丢它
    #: （改过的行走增量也丢）。现在留下来，随隐藏列 `Inline` 走。
    #:
    #: ⚠ **只对单行逻辑语句有值**：多行逻辑语句（括号续行、`\` 续行）里
    #: "这条注释属于哪一行"说不清，硬记下来只会贴错地方 —— 那种情况留空。
    inline_comment: str = ''
    #: **逐物理行的行尾注释**（与 `text.split('\n')` 一一对齐；没有的行是 `''`）。
    #:
    #: 括号续行（`positions = [` … `]`）会被 `_flatten` 并成**一条逻辑行**，
    #: 而注释是**逐物理行**的 —— 单个 `inline_comment` 表达不了。
    #: 只有"拼装方式保持逐行"时才填（反斜杠续行是用空格拼的 → 对齐丢了 → 留空：
    #: 宁可少，也不要贴错行）。
    inline_comments: list = field(default_factory=list)


class RenPyLexer:
    """将 .rpy 源文件转换为逻辑行嵌套树。"""

    # 一条逻辑语句最多允许合并多少物理行（括号续行的安全阀）
    _MAX_MERGE_LINES = 120

    def __init__(self):
        self.errors: list = []
        self._current_file: str = ''

    def tokenize(self, filepath, text: Optional[str] = None) -> list:
        """
        源文件 → 逻辑行嵌套树。

        `text` 给了就**不再读盘**（用调用方读到的那一份）：`RenPyParser.parse()`
        依赖这一点让"词法看到的文本"与"顶层区间 / CST 真迹看到的文本"
        是**同一份快照**。
        """
        filepath = Path(filepath)
        self._current_file = str(filepath)
        if text is None:
            try:
                text = read_source_text(filepath)
            except Exception as e:
                self.errors.append(f"读取失败 {filepath}: {e}")
                return []

        raw_lines = text.splitlines()
        flat = self._flatten(raw_lines)
        return self._build_tree(flat, str(filepath))

    def tokenize_text(self, text: str, filename: str = '<memory>') -> list:
        """
        同 `tokenize`，但源是**内存里的字符串**（不落盘）。

        给"拿两段文本比结构"用（`generation_guard.menu_structure`：
        源 .rpy vs 产物，见不变量 74）——那种地方不该为了解析去写临时文件。
        """
        self._current_file = str(filename)
        flat = self._flatten(str(text or '').splitlines())
        return self._build_tree(flat, str(filename))

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
            #
            # 存**含缩进的原文**（`raw`），不能存 `stripped`：
            # `_render_block_source` 按 `ln.text[ln.indent:]` 还原块体，
            # 若这里已经去掉缩进，缩进就会被扣两次、吃掉注释开头的字符——
            # 真实事故（2026-09-28，sr_general 的 transform.rpy）：
            # `    #anchor(0.5, 0.5)` 变成 `    hor(0.5, 0.5)`，
            # **注释的 `#` 被吃掉、变成了非法 ATL 代码**，产物跑不起来。
            # 消费方（CommentNode / pending_comments）本来就用 `.strip()` 取文本。
            if stripped.startswith('#'):
                flat.append((raw, line_no, indent, True, '', []))
                i += 1
                continue

            accumulated = raw
            #: 逐行注释（与 `accumulated` 的行对齐）；拼装一旦不是逐行就清空
            merge_notes = [self._inline_comment(raw)]

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
                merge_notes = []          # 反斜杠续行用空格拼 → 对齐没了

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
                # 这一物理行的行尾注释（括号续行是**逐行**拼的，所以对得上）
                merge_notes.append(self._inline_comment(raw_lines[i]))

            # 先摘出**行尾注释原文**，再剥掉它（顺序不能反）
            inline = self._inline_comment(accumulated)
            accumulated = self._strip_inline_comment(accumulated)
            if len(merge_notes) != len(accumulated.split('\n')):
                merge_notes = []          # 对齐不上就不填（宁可少也不要贴错）
            flat.append((accumulated, line_no, indent, False, inline,
                         merge_notes))
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
    def _inline_comment(text: str) -> str:
        r"""
        行尾注释**原文**（含 `#`）；没有就返回 `''`。

        只认**单行**逻辑语句：多行语句（括号续行 / 反斜杠续行）里注释可能挂在
        任何一行上，记成"这条语句的注释"会在渲染时贴到别的行去 ——
        宁可不留（那种情况本来就是少数，且贴错比不贴更坏）。
        """
        text = str(text or '')
        if not text or '\n' in text:
            return ''
        code = RenPyLexer._strip_line_comment(text)
        rest = text[len(code):].strip()
        return rest if rest.startswith('#') else ''

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
        #: **注释不参与建树深度**（2026-10-18，P0-3）。
        #:
        #: 从前每读一行都先 `while stack[-1].indent >= indent: pop` ——
        #: 顶格注释会把当前块**整段关掉**，于是
        #:
        #:     label start:
        #:         python:
        #:             import random
        #:     # 顶格注释              ← indent 0，弹空到根
        #:             def f():       ← 成了注释的子节点（顶格！）
        #:                 return 1
        #:         e "after"
        #:
        #: 变成"注释 + `def f()` 在文件顶层、`label start:` 被挪到末尾"。
        #: 这不是排版漂移，是**脚本结构被重排**（产物里出现悬空缩进，
        #: 引擎直接报 `Unexpected indentation at start of file`）。
        #:
        #: 正确语义：Ren'Py / Python 都**忽略注释的缩进** —— 一条注释
        #: 不可能关闭一个块。所以注释先**攒着**，等下一个真语句来了再定归属
        #: （挂上去但不压栈：注释永远不会有子节点）。
        #:
        #: 归属判据（两条，缺一不可）：
        #:   ① 注释**比下一句浅**（`c.indent < indent`，且下一句在块里）
        #:      → 它属于"下一句所在的那个块"（P0-3 的顶格注释就靠这条）；
        #:   ② 否则按**注释自己的缩进**挂到最深的、比它浅的已打开块上
        #:      —— `python:` 块里缩进 12 的注释仍留在 python 块里，
        #:      不会因为下一句 `else:` 缩进 4 就跳到 label 层（真机
        #:      `mini_game1.rpy` 实测：那样会把 `else:` 判成"孤立块"，
        #:      `if/elif/else` 链当场断掉）。
        pending_comments: list = []

        def _deepest_open(limit: int):
            """当前栈里最深的、缩进**严格小于** `limit` 的节点。"""
            for cand in reversed(stack):
                if cand.indent < limit:
                    return cand
            return stack[0]

        for item in flat:
            text, line_no, indent, is_comment = item[:4]
            inline = item[4] if len(item) > 4 else ''
            node = LogicalLine(
                text=text,
                line_number=line_no,
                indent=indent,
                filename=filename,
                is_comment=is_comment,
                inline_comment=inline,
                inline_comments=list(item[5] if len(item) > 5 else []),
            )
            if is_comment:
                pending_comments.append(node)
                continue
            if pending_comments:
                # ① 的"下一句所在块"：**弹栈之前**先算出来（弹完就没了）
                post_parent = _deepest_open(indent)
                for c in pending_comments:
                    if indent > 0 and c.indent < indent:
                        post_parent.subblock.append(c)
                    else:
                        _deepest_open(c.indent).subblock.append(c)
                pending_comments = []
            while len(stack) > 1 and stack[-1].indent >= indent:
                stack.pop()
            stack[-1].subblock.append(node)
            stack.append(node)

        # 文件末尾的注释：没有"下一条真语句"可依，按它们自己的缩进找父块
        # （与从前的行为一致）——但同样**不压栈**。
        if pending_comments:
            base = min(c.indent for c in pending_comments)
            while len(stack) > 1 and stack[-1].indent >= base:
                stack.pop()
            stack[-1].subblock.extend(pending_comments)

        return root.subblock


def _indent_of(text: str) -> int:
    """这一行的缩进（字符数）。空行算 0。"""
    return len(text) - len(text.lstrip(' \t'))


def _shift_continuations(text: str, base: int) -> str:
    """
    多行文本 → **首行去缩进、续行相对 `base` 左移**（空行留空）。

    ⚠ 分工写清楚，否则会双重计算（2026-10-07 实测踩到）：
      · **首行**的缩进由**调用方的 `rel`** 负责（`rel = node.indent - base`），
        所以这里只 `lstrip()` 首行 —— 不在这里减 base，减了就加两次；
      · **续行**才减 `base`。`_flatten` 会把括号未闭合的多行语句并成**一条
        逻辑行**（`text` 含 `\n`），而 `lstrip()` 对多行字符串**只作用于
        第一行** → 续行的**绝对**缩进原样留下，被当成"更深一层"；
        渲染时整块再加一次基准，于是**每导出一次缩进就长一截**：

            原文 12 → 第一轮 20 → 第二轮 28 → 第三轮 36 …（实测 mini_game1.rpy）

        那不是"排版漂移"那么轻：文件**永远在变** → 增量对位每轮都判
        "文本变了" → 整份退回全量。
        判据：`tests/test_roundtrip_idempotent.py`、`tools/audit_idempotence.py`。
    """
    parts = str(text or '').split('\n')
    out = [parts[0].lstrip()]
    for ln in parts[1:]:
        if not ln.strip():
            out.append('')
            continue
        ind = len(ln) - len(ln.lstrip())
        out.append(' ' * max(0, ind - base) + ln.lstrip())
    return '\n'.join(out)


def _dedent_block(lines: list) -> list:
    """
    整块**按最小缩进归零**（相对层级保留，空行留空）。

    与 `_top_level_region` 同一套口径 —— 两处都这么做，
    才不会有哪一类块在往返里越缩越深。
    """
    indents = [_indent_of(s) for s in lines if s.strip()]
    base = min(indents) if indents else 0
    return [s[base:] if s.strip() else '' for s in lines]


# ====================================================================
#  第二层：Parser —— AST
# ====================================================================





















































# 字符串正则：双引号分支与单引号分支分离
_STR_DQ = r'"((?:[^"\\]|\\.)*)"'
_STR_SQ = r"'((?:[^'\\]|\\.)*)'"
_STR = f'(?:{_STR_DQ}|{_STR_SQ})'

#: `image <名字>:`（**ATL / 块形式**：贴图写在块里）的块头。
#:
#: 为什么放在模块级：顶层区间（`_register_atl_image`）与解析器类
#: （`RenPyParser._RE_IMAGE_ATL`）都要用它，两处各写一份迟早漂移。
#: 名字允许带空格（`logo blink` = tag + 属性）；**不许有 `=`**
#: （那是另一种写法 `image 名 = 表达式`，由 `_RE_IMAGE_DEF` 管）。
_RE_IMAGE_ATL_HEAD = re.compile(r'^image\s+(?P<name>[^\s=:][^=:]*?)\s*:\s*$')

# 音频语句里的"参数词"：出现它们说明后面不是资产名

# 翻译文件用的命名组版本
_TR_DQ = r'"(?P<dq>(?:[^"\\]|\\.)*)"'
_TR_SQ = r"'(?P<sq>(?:[^'\\]|\\.)*)'"
_TR_STR = f'(?:{_TR_DQ}|{_TR_SQ})'







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


#: 公共别名（跨包使用 —— `app/stage.py` 靠它们把 `image x = …` 认成资源）。
#: 名字带下划线但跨包在用，就说明**它已经是契约**，只是没声明
#: （见 `tests/test_package_layering.py::test_no_private_names_cross_package`）。
image_path_from_expr = _image_path_from_expr
image_solid_from_expr = _image_solid_from_expr


def _has_open_bracket(text: str) -> bool:
    """
    这行结束时**还有没闭合的括号**吗？（= 它的下一行是续行）

    ⚠ **当前没有调用方**（2026-10-05）：它是给"续行参与/不参与块体基准"
    那两个修法用的，两个都实测失败（见 `beat_render._indent_block_body`）。
    留在这里是因为它是判断"续行 vs 下一层块体"的**唯一正确判据** ——
    块体里"更深"的行有两种、含义完全相反，想再动手的人一定会需要它。
    """
    depth = 0
    i = 0
    s = str(text or '')
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == '\\':
            i += 2
            continue
        if ch in ('"', "'"):
            i, _ = _skip_string(s, i)
            continue
        if ch == '#':
            break                     # 行内注释之后的括号不算
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        i += 1
    return depth > 0


def _shift_embedded_indent(text: str, delta: int) -> str:
    """
    多行文本（`_flatten` 用 `'\\n'` 拼出来的续行）整体左移 `delta` 个空格。

    ⚠ **当前没有调用方**（2026-10-05）：它本来是给"把续行也按块体基准平移"
    那个修法用的，而那个修法实测让全项目解析异常从 0 涨到 5，已放弃。
    留在这是因为下一个人一定会再想"把续行平移一下就好了" ——
    先读 `beat_render._indent_block_body` 的说明再动手。

    只动**第二行起**的行首空白：首行由调用方 `lstrip` 之后自己管。
    空行原样保留（它没有缩进可言）。
    """
    if delta <= 0 or '\n' not in text:
        return text
    parts = text.split('\n')
    out = [parts[0]]
    for ln in parts[1:]:
        if not ln.strip():
            out.append('')
            continue
        room = len(ln) - len(ln.lstrip())
        out.append(ln[min(delta, room):])
    return '\n'.join(out)


class RenPyParser(StatementScreens):  # 2026-10-11 拆分：`_stmt_*` 在 renpy_parser_stmts 里
    """递归下降解析器：LogicalLine 树 → ASTNode 列表。"""
    # ---------- 简单前缀正则 ----------
    _RE_LABEL_HEAD = re.compile(r'^label(?=\s)')
    #: 循环块头（`while <条件>:` / `for <目标> in <可迭代>:`，D2）。
    #: 必须**以冒号收尾**：`for "台词"`（说话人恰好叫 `for`）与
    #: `while "x"` 都不会误命中 —— 它们没有尾冒号。
    _RE_LOOP_HEAD = re.compile(r'^(while|for)\b.*:\s*$')
    #: 3D 舞台的 `camera` 块头：`camera:` / `camera background:`。
    #:
    #: 三条约束，每条都是被坑过才加的：
    #   · **必须以冒号收尾** —— Ren'Py 的 `camera` 一定要带 ATL 块，
    #     而 `camera "台词"`（说话人恰好叫 camera）没有尾冒号，不许误伤；
    #   · 层名只认 `[\w.]+`（`master` / `background` / `screens`…）——
    #     不认表达式，认不出就当没匹配上；
    #   · 冒号前**不许有别的参数** —— `camera xpos 100:` 不是合法语法，
    #     那种行宁可留给"未建模"通道（逐字保真）也不要猜。
    _RE_CAMERA_HEAD = re.compile(r'^camera(?:\s+([\w.]+))?\s*:\s*$')
    _RE_IF_HEAD = re.compile(r'^if\s+')
    _RE_ELIF_HEAD = re.compile(r'^elif\s+')
    _RE_ELSE_HEAD = re.compile(r'^else\s*:')
    _RE_MENU_HEAD = re.compile(r'^menu\b')
    #: `menu` 头上的**菜单名**（`menu choice_menu:` / `menu .local:`）。
    #:
    #: 形状对齐 Ren'Py 的 `Lexer.label_name()`：一个标识符（可带 `.` 前缀
    #: 表示"本 label 内的局部菜单"）。**不认关键字**（`menu hide:` 不是
    #: 合法写法）—— 但这里刻意不查关键字表：真出现那种行，把它当名字
    #: 原样写回，与源文件逐字节相同，错的是源文件、不是我们。
    _RE_MENU_NAME = re.compile(r'\.?[^\W\d]\w*')
    # 角色定义两种写法都收：define 是常量，default 是可存档变量
    _RE_DEFINE = re.compile(
        r'^(?:define|default)\s+([\w.]+)\s*=\s*(.+)$', re.DOTALL)
    _RE_SAY = re.compile(r'^([^\W\d]\w*(?:\.[^\W\d]\w*)*)?\s*' + _STR)
    #: Python 字符串前缀（`u"…"` / `r"…"` / `b"…"` / `br"…"`）紧贴引号。
    #:
    #: ⚠ 这不是台词（2026-10-18，审计 P0-6）：
    #:   · 引擎**不认**它 —— SDK 8.5 lint 对 `u"统一"` 报
    #:     `expected statement.`（Ren'Py 的 say 只接受 who? + 裸字符串字面量）；
    #:   · 而工具从前把 `u` 当成**说话人**，产物成了 `u "统一"` —— 一句
    #:     凭空出现的"角色 u 说『统一』"，语义被静默改掉。
    #: 它必须**逐字原样**留住（这行本来就是作者的原文，引擎报错是引擎的事），
    #: 所以识别它之后 `_stmt_say` 主动放弃，交给 `_stmt_unmodeled` 的保真通道。
    _RE_SAY_PREFIX = re.compile(r'^[rRuUbBfF]{1,2}(?=["\'])')
    _RE_JUMP_EXPR = re.compile(r'^jump\s+expression\b')
    _RE_CALL_EXPR = re.compile(r'^call\s+expression\b')
    _RE_CALL_SCREEN = re.compile(r'^call\s+screen\b')
    _RE_JUMP = re.compile(r'^jump\s+([\w.]+)')
    _RE_CALL = re.compile(r'^call\s+([\w.]+)')
    #: `call X from Y` 里的 Y（存档点标识）
    _RE_CALL_FROM = re.compile(r'\bfrom\s+([A-Za-z_]\w*)')
    _RE_RETURN = re.compile(r'^return\s*$')
    _RE_DOLLAR = re.compile(r'^\$\s*(.+)')
    #: `python:` 块的**全部**块头形态：
    #:   · label 内：`python:` / `python in store:`
    #:   · 顶层：`init python:` / `init 1 python:` / `python early:`
    #:     （`init [N]` 前缀 2026-10-05 加，见 `_stmt_python_block`：少了它
    #:     顶层 `init python:` 会被当"未建模块"整块跳过）
    _RE_PYTHON = re.compile(r'^(?:init(?:\s+\d+)?\s+)?python(?:\s+early)?'
                            r'(?:\s+in\s+[\w.]+)?\s*:')
    _RE_SHOW_HEAD = re.compile(r'^show\s+')
    _RE_SCENE_HEAD = re.compile(r'^scene\s+')
    _RE_HIDE_HEAD = re.compile(r'^hide\s+')
    # `show screen X` / `hide screen X` 是 UI 操作，不是立绘
    _RE_SCREEN_SHOW = re.compile(r'^(?:show|hide)\s+screen\b')
    # `image <名字> = <表达式>`：名字允许带空格（`bg bg1_1`）
    _RE_IMAGE_DEF = re.compile(
        r'^image\s+(?P<name>[^\s=][^=]*?)\s*=\s*(?P<rhs>.+)$')
    # `image <名字>:`（**ATL / 块形式**：贴图写在块里）
    #   image logo blink:
    #       "logo.png"
    #       xpos 100
    #   · 名字允许带空格（`logo blink` = tag + 属性），与 `_RE_IMAGE_DEF` 同口径；
    #   · 必须**没有 `=`**（那是上面那一条），且冒号在行尾（带块体）。
    # 与 `_register_atl_image` 共用**同一个**正则（模块级 `_RE_IMAGE_ATL_HEAD`）：
    # 那一层在顶层区间里扫块头，这一层给 `_stmt_image_def` 之外的地方用 ——
    # 两处各写一份，迟早有一处认不出带空格的图名。
    _RE_IMAGE_ATL = _RE_IMAGE_ATL_HEAD
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

    # ⚠ 2026-10-16：这里的 `_SHOW_KEYWORDS` / `_SCENE_KEYWORDS` **两张别名表已删**。
    #   它们存在的唯一理由是"`_parse_show_scene` 自己要拿一个集合去 split()"；
    #   现在切词与角色判定全在 `core.vocabulary.classify_show_words` 一处，
    #   留着这两份就又是"看起来像真源、其实没人用"的第二口径 ——
    #   正是这次（用户排期 C 项）要根治的病。
    #   `scene` 与 `show` 同口径这件事改由分类器的 `intros=` 参数表达
    #   （官方原文："the scene statement takes the same properties as
    #   the show statement"；见 `_parse_show_scene`）。

    _RE_POS_ARG = re.compile(r'^\s*' + _STR)

    _IGNORED_SAY_HEADS = frozenset({'voice'})

    _RE_DEFINE_FUNC_HEAD = re.compile(r'^([\w.]+)\s*\(')

    def __init__(self):
        self.errors: list = []
        self.warnings: list = []

    def parse(self, filepath) -> list:
        self.errors = []
        self.warnings = []
        # 源文件文本缓存**按一次 parse 的生命周期**清掉：解析器实例是复用的，
        # 留着上一次的文本会让"同一路径被改写后再解析"读到旧内容
        # （见 `_source_text` 的用途：未建模块按原文物理行取）。
        self.__dict__.pop('_src_cache', None)
        # **整份文件只读一次**（2026-10-07，CST 稳固性）：这一次读到的文本同时
        # 喂给词法器、顶层区间、以及 CST 真迹的挂载（`IncrementalRenPyAnalyzer`
        # 从 `source_texts()` 取走它）。三处各读一遍时，只要中间有人改了文件，
        # **AST 的行号**与**原文切片**就不是同一份快照 → 真迹贴错行。
        text = self._source_text(str(filepath))
        lexer = RenPyLexer()
        lines = lexer.tokenize(filepath, text=text)
        self.errors.extend(lexer.errors)
        nodes = self._parse_block(lines)
        # **顶层（label 之外）那一整段**：逐字带出来（见 `TopLevelRegionNode`）。
        # 放在最后追加成一个节点是因为：AST 里根本没有"未建模的块语句"
        # （`_stmt_unmodeled` 遇到带子块的直接跳过），而 `define` / `image`
        # 这些虽然进了 AST，却只留下结构化字段、原文已经不在了 ——
        # 只有按**原文**取那一段，才能保证"全量重生成"不丢东西。
        region = _top_level_region(
            filepath, lines=text.splitlines() if text is not None else None)
        if region:
            nodes = list(nodes) + [TopLevelRegionNode(
                filename=str(filepath), blocks=region)]
        return nodes

    def source_texts(self) -> dict:
        """
        本次 `parse()` 读到的源文本 → `{路径: 文本}`（可能为空）。

        **给 CST 挂载用**：真迹必须切自"解析时那一份"，不许再读一遍盘
        （`core.cst.attach_spans` 的 `file_texts`）。拿不到某条路径就用
        它自己的兜底读盘 —— 那正是我们要尽量避免的窗口，所以这里尽量给全。
        """
        return dict(self.__dict__.get('_src_cache', {}) or {})

    # ---------- 块解析 ----------

    def _parse_block(self, lines: list) -> list:
        nodes = []
        i = 0
        pending_comments = []       # 紧邻的整行注释（可能成为下个定义的 doc）
        while i < len(lines):
            line = lines[i]
            text = line.text.strip()
            node = self._parse_statement(line, text)
            if node is not None:
                # 行尾注释跟着**这一行**走（C：见 `LogicalLine.inline_comment`）。
                # 动态属性，与 `Statement.nest_path` 同款 —— 不让它进 dataclass
                # 的相等/哈希（那是"内容指纹"契约的一部分）。
                node.inline_comment = getattr(line, 'inline_comment', '')

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

    #: 分派哨兵：`_stmt_*` 返回它 = "这条语句我认不出，试下一个分支"。
    #:
    #: 与 `None` **必须分开** —— `None` 是"认出来了但决定放弃解析"
    #: （例如 `if` 条件缺顶层冒号，那时要**就此结束**、不能继续试别的分支，
    #: 否则一句坏掉的 `if` 会被后面的 `_stmt_say` 当成台词吃掉）。
    _NO_MATCH = object()

    # ------------------------------------------------------------------
    #  语句分派（每个分支一个方法；原来是 375 行的扁平 if 链）
    #
    #  P3-12 拆的。**分派顺序就是匹配优先级**，不能重排。
    #  上一轮试过"按 ast 取行区间"的生成器自动搬：跑通了但结果是错的
    #  （`m = ...match()` 与 `if m:` 之间夹注释时会漏掉赋值 → `NameError`，
    #   而 `ast.parse` 完全通过）。所以这一版是**手写**的 ——
    #  结论记在 docs/archive/问题清单与优化路线_2026-10-03.md §20.5。
    # ------------------------------------------------------------------




























    def _unknown_block_lines(self, line: LogicalLine) -> list:
        """
        一条带子块的未知语句 → **原文行**（整块最小缩进归零）。

        优先**按原文物理行**取（`_raw_block_lines`），因为 AST 那条路
        天然缺两样东西：

          · **块内空行**：lexer 根本不产生对应的 `LogicalLine`，
            走 AST 必然丢（实测 8 行块丢 1 行空行）；
          · 括号续行的**绝对缩进**：lexer 把它们并成一条逻辑行，
            只按首行缩进切，续行会原地不动 —— 往返一次多缩一层
            （实测 12 → 16 空格）。

        两条都是"吃内容/改排版"，而这一层的契约是**一个字都不许吃**。
        原文取不到（读文件失败、行号对不上）才退回 AST 重建。
        """
        raw = self._raw_block_lines(line)
        if raw is not None:
            return raw
        nodes: list = []

        def walk(node) -> None:
            nodes.extend(str(getattr(node, 'text', '') or '').split('\n'))
            for sub in (getattr(node, 'subblock', None) or ()):
                walk(sub)

        walk(line)
        return _dedent_block(nodes)

    def _raw_block_lines(self, line: LogicalLine) -> Optional[list]:
        """
        按**原文物理行**取一整块（含空行、含续行），整块最小缩进归零。

        基准缩进 = 头行的缩进；块体 = 之后所有更深的行，
        中间的**空行**只有在"后面还有更深的行"时才算块体
        （否则它是块与下一段之间的分隔）。这正是 Ren'Py 的块规则。

        行号对不上（文件被改过 / 被别的进程写过）就返回 `None`，
        由调用方退回 AST 重建 —— 宁可少保真一点，也不要**贴错原文**。
        """
        path = getattr(line, 'filename', '') or ''
        if not path:
            return None
        try:
            text = self._source_text(path)
        except (OSError, UnicodeDecodeError):
            return None
        if text is None:
            return None
        lines = text.split('\n')
        start = int(getattr(line, 'line_number', 0) or 0) - 1
        if start < 0 or start >= len(lines):
            return None
        head = lines[start]
        # 身份核对：行号上那一段必须**就是**这条语句的头一行。
        if head.strip() != str(line.text or '').split('\n')[0].strip():
            return None
        base = len(head) - len(head.lstrip(' \t'))
        out = [head]
        i = start + 1
        while i < len(lines):
            cur = lines[i]
            if not cur.strip():
                j = i
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and _indent_of(lines[j]) > base:
                    out.extend(lines[i:j])
                    i = j
                    continue
                break
            if _indent_of(cur) <= base:
                break
            out.append(cur)
            i += 1
        return _dedent_block(out)

    def _source_text(self, path: str) -> Optional[str]:
        """读源文件（**每份只读一次**；`parse()` 开头会清缓存）。"""
        cache = self.__dict__.setdefault('_src_cache', {})
        if path in cache:
            return cache[path]
        try:
            text = read_source_text(path)
        except (OSError, UnicodeDecodeError):
            text = None
        cache[path] = text
        return text

    def _parse_statement(self, line: LogicalLine, text: str):
        """一条逻辑行 → AST 节点（按语句种类分派）。

        每个分支各自住在 `_stmt_*` 里（P3-12 拆的：原本是 375 行 /
        圈复杂度 60 的扁平 if 链）。**分派顺序就是匹配优先级**，
        不能重排 —— 它必须与 `_stmt_*` 的定义顺序一致：

             1. `comment`
             2. `label`
             3. `define`
             4. `menu`
             5. `if`
             6. `jump_expression`
             7. `call_expression`
             8. `call_screen`
             9. `jump`
            10. `call`
            11. `return`
            12. `dollar`
            13. `python_block`
            14. `layeredimage`
            15. `image_def`
            16. `screen_op`
            17. `show`
            18. `scene`
            19. `hide`
            20. `audio`
            21. `voice`
            22. `extend`
            23. `loop`（`while` / `for`）
            24. `camera`（3D 舞台）
            25. `say`
            26. `transform`
            27. `unmodeled`

        `_stmt_*` 返回 `self._NO_MATCH` 表示"这条语句我认不出，
        试下一个分支"；返回别的（含 `None`）表示"有结论了，
        就此结束" —— 两者**必须分开**，否则一句坏掉的 `if` 会被
        后面的 `_stmt_say` 当作台词吃掉。
        """
        node = self._stmt_comment(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_label(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_define(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_menu(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_if(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_jump_expression(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_call_expression(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_call_screen(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_jump(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_call(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_return(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_dollar(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_python_block(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_layeredimage(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_image_def(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_screen_op(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_show(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_scene(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_hide(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_audio(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_voice(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_extend(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_loop(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_camera(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_say(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_transform(line, text)
        if node is not self._NO_MATCH:
            return node
        node = self._stmt_unmodeled(line, text)
        if node is not self._NO_MATCH:
            return node
        return None

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
        # `label 名(参数…):` —— **参数表必须留住**（2026-10-18）。
        # 从前这里只是"扫过去"（`i = close + 1`），参数被丢掉，产物成了
        # `label 名:`。而 `call 名(...)` 是原样保真的 —— 于是调用方照旧传参，
        # 被调方却不收：Ren'Py 直接报"参数过多"，游戏起不来。
        # 单独存 `params`（不带括号），渲染时接在名字后面。
        label_head = text[m.end():].strip()
        if '(' in label_head:
            node.params = label_head[label_head.index('('):].strip()
            if node.params.endswith(':'):
                node.params = node.params[:-1].rstrip()
        node.block = self._parse_block(line.subblock)
        return node

    # ---------- menu 头扫描 ----------

    @classmethod
    def _scan_menu_header(cls, text: str, m):
        """
        扫描 menu 头，返回 `(caption, name, args)`；不是合法 menu 头则返回 None。

        支持的合法形式（= `renpy/parser.py: menu_statement` 的语法）：
            menu:
            menu 名:
            menu (args):
            menu 名(args):                 ← 2026-10-19 起**认了**（见下）
            menu "caption":                ← 引擎其实不收，见下
            menu "caption" (args):

        返回三元组的语义：
          · `caption` —— 头行上的字符串（空 = 没有）；
          · `name`    —— 菜单名（`ast.Label` 的源，**是 jump 目标的真 label**）；
          · `args`    —— 参数表原文（含括号，保持原样）。
        """
        i = m.end()
        while i < len(text) and text[i] in ' \t':
            i += 1

        caption = ''
        if i < len(text) and text[i] in ('"', "'"):
            end, closed = _skip_string(text, i)
            if not closed:
                return None
            # **必须反转义**（2026-10-05 修，`tools/hell_edit_rounds.py` 的
            # A2 轴实测）：渲染侧 `_quote` 把真换行写成 `\n`、把 `\` 写成 `\\`，
            # 这里若原样切片，`第一行\n第二行` 就会变成**字面量反斜杠 + n**；
            # 再渲染一次又转义一层 → `\\n` → `\\\\n` …… 每导出一轮标题就变一次，
            # 而"文本每轮都变"正是增量对位失败的上游。
            #
            # 台词那条路一直是对的（`_pick_str` 内部就反转义），所以只有
            # **标题**这一处不对称。选项文案走 `_parse_menu_option` 的
            # `_pick_str`，本来就没问题。
            caption = unescape_renpy_string(text[i + 1:end - 1])
            i = end
            while i < len(text) and text[i] in ' \t':
                i += 1

        if i < len(text) and text[i] == ':':
            return (caption, '', '')

        # **菜单名**（`menu choice_menu:`）：Ren'Py 的 `l.label_name()`
        # 只认标识符（可带点），不认关键字以外的任何东西。这里用同一个
        # 形状 —— 认不出就当"不是 menu 头"，交回通用语句通道（不猜）。
        name = ''
        m_name = cls._RE_MENU_NAME.match(text, i)
        if m_name:
            name = m_name.group(0)
            i = m_name.end()
            while i < len(text) and text[i] in ' \t':
                i += 1
            if i < len(text) and text[i] == ':':
                return (caption, name, '')

        if i < len(text) and text[i] == '(':
            close = _scan_balanced_close(text, i)
            if close == -1:
                return None
            args = text[i:close + 1]
            j = close + 1
            while j < len(text) and text[j] in ' \t':
                j += 1
            if j < len(text) and text[j] == ':':
                # **带参数 / 带名的 menu 头**现在**建模**了（2026-10-19，
                # 审计 P1-3 收尾）。从前这里 `return None`：参数被静默丢掉
                # （渲染固定写 `menu:`），整块只能走未建模通道 ——
                # 逐字保真，但选项在卡片里**看不见也改不了**。
                #
                # 现在 `name` / `args` 各有一格（`MenuName` / `MenuArgs`），
                # 选项重新成为可编辑的拍，头行照原样写回。
                return (caption, name, args)

        return None

    # ---------- show / scene 目标解析 ----------

    @staticmethod
    def _tokenize_show(rest: str) -> list:
        """
        把 show/scene 语句的目标部分分词。保留这个方法是**为了既有调用点与测试**。

        ⚠⚠ **实现已搬到 `core.vocabulary.tokenize_show_words`**（2026-10-16，用户排期
        C 项）：同一把刀必须同时给 `classify_show_words` 用 —— 否则
        "括号里的逗号不算分隔符"这类细节会在两者之间漂移。实测过的后果：
        用朴素的 `str.split()` 切 `show image puzzle_path at
        Transform(xysize=(1920, 1080))` 会切出 `Transform(xysize=(1920,` ——
        再拼回去就是**一个 Python 语法错误**、产物直接跑不起来。

        规则（真源里的 docstring 有完整说明）：
          · 空格分隔；
          · 逗号是独立 token（**只在括号外/引号外**）；
          · 引号字符串不切分；
          · `(...)` / `[...]` / `{...}` 里的逗号留住。
        """
        return tokenize_show_words(rest)

    @classmethod
    def _parse_show_scene(cls, text: str, is_scene: bool):
        """
        解析 show / scene 目标。

        返回 dict，或 None（语法不匹配）：
          image, at, as_, with_, zorder, onlayer, behind,
          is_expression, warnings (list)

        warnings 为字符串列表，元素不含 filename:line 前缀，
        由调用层负责拼接位置信息。

        ⚠⚠ **切词与"哪个词是什么角色"全部交给
        `core.vocabulary.classify_show_words`**（2026-10-16，用户排期 C 项）。
        从前这里自己写了一遍"拿关键字集合去 split()"的循环 —— 与
        `beat_stage_parse` / `excel_export` / `asset_registry` /
        `learn_statement` 各写一遍是**同一个口径的六份拷贝**。
        本函数现在只做**翻译**：把分类器给的角色表拼成下面这个 dict 形状
        （调用方 `_stmt_show` / `_stmt_scene` / `_stmt_hide` 与测试都按它取字段）。

        `show screen X` / `show layer Y` 这一支**没有图像名** →
        `_stmt_show` 走 `_NO_MATCH` → 整行落 `raw`（逐字保真）。
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

        roles = classify_show_words(
            rest,
            # `scene expression x` 在引擎里是合法写法（scene 没有引入型关键字），
            # 所以 scene 那条路把 `expression` 当普通目标 —— 与从前逐字一致。
            intros=(SHOW_INTRO_KEYWORDS - {'expression'} if is_scene
                    else SHOW_INTRO_KEYWORDS))

        if roles.intro_kind in ('screen', 'layer', 'unknown-intro'):
            # 没有图像名（`show layer master` / `show screen X` / `show zorder 3`）：
            # 原样落 raw，绝不猜。
            return result

        if roles.intro_kind == 'expression':
            result['is_expression'] = True
        result['image'] = roles.target
        result['at'] = list(roles.clause('at', []))
        result['as_'] = roles.clause('as') or ''
        result['with_'] = roles.clause('with') or ''
        result['onlayer'] = roles.clause('onlayer') or ''
        result['behind'] = list(roles.clause('behind', []))
        zorder = roles.clauses.get('zorder')
        if zorder:
            try:
                result['zorder'] = int(zorder)
            except ValueError:
                result['warnings'].append(f'zorder 值无效: {zorder!r}')
        return result

    # ---------- python: 块收集 ----------

    def _collect_python_block(self, line: LogicalLine) -> str:
        """
        `python:` 块 → 归一化到"块体基准缩进 0"的代码文本。

        **合并进同一个逻辑行的续行也要跟着平移**（2026-10-05 修，真实项目实测）：

        `_flatten` 会把括号未闭合的多行语句**并成一条逻辑行**
        （`node.text` 里因此含 `\\n`），而 `node.indent` 只是**第一行**的缩进。
        下面原来只给第一行算 `rel`，于是续行**原封不动带着它在原文件里的
        绝对缩进**进了 payload：

            原文件（块体缩进 8）
                renpy.store.drag_puzzle_game = drag_puzzle(     ← indent 8
                            puzzle_path,                        ← indent 12
                )

            payload 里就是 rel=0 的首行 + 12 空格的续行 ——
            可**规约是"相对块体基准"**，那个 12 会被当成"更深一层"。

        后果不是立刻错，而是**再渲染一次就翻倍**：`_indent_block_body` 给
        整块加 4，续行本身那 12 还在 → 16；第三轮 20…（实测产物里
        `puzzle_path,` 是 20 空格，原文 12 —— 审计按逐行文本比，算一条丢失）。

        修法：把**每一行**都减去 `base_indent`（`_flatten` 用 `'\\n'` 拼接时
        每行都保留了原文缩进），与第一行同一套口径。
        块体基准取 `line.subblock` 里最小的缩进（与原来一致）。
        """
        result = []

        def walk(nodes, base_indent):
            for node in nodes:
                rel = max(0, node.indent - base_indent)
                # **逐行**左移（不是 `lstrip()` 第一行）——见
                # `_shift_to_base` 的说明：续行带着绝对缩进会被当成
                # "更深一层"，每渲染一次就长一截。
                text = _shift_continuations(node.text, base_indent)
                if text.strip():
                    text = text.rstrip()
                    # **块体里这一行自己的行尾注释也要带上**（C 阶段的收尾）：
                    # 块体是拿 `.text`（注释已被剥掉）拼的，于是
                    # `    x = 1  # 备注` 里的备注在产物里消失（真实项目
                    # `mini_game1.rpy` 实测 7 行）。块体里每一行都是**独立逻辑行**，
                    # 自己那份 `inline_comment` 本来就在。
                    # 注释在 Python 里是合法行尾，进代码文本安全（语法检查也过）。
                    note = getattr(node, 'inline_comment', '') or ''
                    if note and '\n' not in text:
                        text = f'{text}  {note}'
                    else:
                        # 多行（括号续行）：逐行贴（`inline_comments` 与行对齐）
                        parts = text.split('\n')
                        notes = getattr(node, 'inline_comments', None) or []
                        if len(notes) == len(parts):
                            parts = [f'{x}  {nn}' if nn and x.strip() else x
                                     for x, nn in zip(parts, notes)]
                            text = '\n'.join(parts)
                    result.append(' ' * rel + text)
                walk(node.subblock, base_indent)

        if line.subblock:
            # 块体基准缩进**不看注释**（2026-10-18，P0-3）：注释的缩进在
            # Ren'Py / Python 里没有语义，顶格注释（indent 0）若参与取最小值，
            # 整块代码会被当成"相对 0 缩进"，渲染时再叠一层就是悬空缩进。
            base_indent = min(
                (c.indent for c in line.subblock if not c.is_comment),
                default=0)
            walk(line.subblock, base_indent)

        return '\n'.join(result)

    # ---------- if 链消费 ----------

    def _consume_if_chain(self, lines: list, start_i: int,                          if_node: IfNode) -> int:
        # `if / elif / else` **每一行自己的**行尾注释（收尾第 2 批）：
        # 条件不是拍，注释只能按"分支"记 —— 与 `branches` 一一对齐。
        # 动态属性：不让它进 AST 的 dataclass 契约。
        if_node.branch_comments = [
            getattr(lines[start_i], 'inline_comment', '') or '']
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
                if_node.branch_comments.append(
                    getattr(nxt, 'inline_comment', '') or '')
                j += 1
                continue

            if self._RE_ELSE_HEAD.match(nxt_text):
                if_node.branches.append(
                    ('', self._parse_block(nxt.subblock))
                )
                if_node.branch_comments.append(
                    getattr(nxt, 'inline_comment', '') or '')
                j += 1

            break

        return j

    # ---------- menu 解析 ----------

    def _parse_menu(self, lines: list) -> tuple:
        caption = ''
        options = []
        #: 块里认不出的行（`set seen` / `$ x = 1` …）——**绝不丢**。
        extras = []
        i = 0
        while i < len(lines):
            line = lines[i]
            text = line.text.strip()

            opt = self._parse_menu_option(line, text)
            if opt is not None:
                opt.block = self._parse_block(line.subblock)
                # 选项行自己的行尾注释（C）：选项行不是独立 AST 节点，
                # 注释只能挂在这张选项节点上（动态属性，同 `nest_path` 的做法）
                opt.inline_comment = getattr(line, 'inline_comment', '')
                options.append(opt)
                i += 1
                continue

            # 菜单块里的"裸字符串行"是**标题行**（Ren'Py 会把它显示成 caption）。
            # ⚠ 但**带说话人的那种不是裸字符串**（2026-10-18 审计 P1-3 的连带）：
            #
            #       menu:
            #           e "Would you like to hear about rollback?"
            #
            # 官方 `tutorial_playing.rpy:71` 就是这么写的（角色说标题）。
            # 从前这里取**后一个字符串**当 caption，说话人 `e` 被丢掉 ——
            # 产物成了裸 `"Would you like…"`，**每次导出都把角色从标题里抹掉**
            # （而且还是增量补丁失败的原因：生成行在原文里找不到）。
            # 模型里菜单标题没有"谁说的"这一位，所以整行进 `extras`
            # （逐字保真，渲染时原样吐在 `menu:` 之后、选项之前）。
            pair = split_string_speaker(text)
            if pair is not None:
                extras.append(text)
                i += 1
                continue

            m = self._RE_SAY.match(text)
            if m:
                who = m.group(1) or ''
                if who not in self._IGNORED_SAY_HEADS:
                    if who:
                        # `e "标题"`：带说话人 → 整行逐字保留（同上）。
                        extras.append(text)
                    else:
                        cap = _pick_str(m, 2, 3)
                        caption = (caption + '\n' + cap) if caption else cap
                i += 1
                continue

            # ⚠ 2026-10-18：这里从前是 `i += 1` —— **静默丢弃**。
            # `menu:` 块里除了选项、还有合法语句（`set seen` 最典型，
            # 它决定这个菜单要不要记"已读"）。丢掉它 = 玩法记账变了，
            # 而产物语法完全正确。整块（含子块）逐字收进 `extras`，
            # 渲染时在 `menu:` 之后原样吐回。
            if line.subblock:
                extras.extend(self._unknown_block_lines(line))
            elif text:
                extras.append(text)
            i += 1

        return caption, options, extras

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
class FileBlock:
    """
    **文件级保真块**：顶层（label 之外）的 `init python:` 之类整块代码。

    为什么需要这个类型（2026-10-05，拓展边界③）：这些块**不属于任何"拍"**，
    所以它们既不在 `statements` 里、也不在 Beats 里；以前连 AST 节点都不产生
    （`_stmt_unmodeled` 直接跳过 + 一条 warning）。于是"表 → 新文件"这条
    纯全量路径上，它们**整块消失而无人知晓**：

        真实项目 `whackamole.rpy`：2 个顶层 `init python:` / 56 行游戏逻辑
        （`def mole_click` / `mole_tick` / `mole_start` …）→ 产物 0 行，
        而产物语法完全合法、门禁拦不住。

    它只在**顶层**产生；label 里的 `python:` 走 `CodeBlock`（那是"拍"的一部分）。
    """

    header: str = ''            # 块头原文（`init python:` / `python early:`）
    code: str = ''              # 块体：基准缩进已归一到 0
    source_file: str = ''
    source_line: int = 0


@dataclass
class ParseResult:
    characters: dict = field(default_factory=dict)
    dialogues: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)
    #: `label 名(参数…):` 的参数表原文：`{名字: '(flag=True)'}`。
    #: 丢了它，`call 名(...)` 的实参无处可去（Ren'Py 报"参数过多"，
    #: 而 `call 名(...)` 本身是**原样保真**的 —— 两头对不上）。
    label_params: dict = field(default_factory=dict)
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
    # 顶层（label 之外）的 `python:` 整块代码，`FileBlock` 列表。
    # 见 `FileBlock` 的 docstring：不进 statements/Beats，但要能原样带回去。
    file_blocks: list = field(default_factory=list)
    # **工具不认识的带子块语句**（label 之内）：`[{head, file, line, lines}]`。
    #
    # 为什么要单独记一份（2026-10-07，M1）：这些块的**内容**走保真通道，
    # 但"工具不管辖它们"这件事必须**能数出来、能说出口** —— 体检报告要
    # 回答编剧真正关心的问题："我剧本里用的哪一种结构，工具管不了？"
    # 只统计 label 之内的：顶层（label 之外）由 `_top_level_region()`
    # 整段按原文带出，不存在丢的风险。
    unknown_blocks: list = field(default_factory=list)


# ====================================================================
#  第三层：ParseResultBuilder
# ====================================================================

class _WalkCtx:
    """
    `ParseResultBuilder._walk` **一层递归**里的共享可变状态（M4 拆分时打包）。

    为什么打包：这九样东西在 23 个分支里到处都是，而且 `counters` /
    `cur_inline` / `cond_inline` 是**可变容器**（分支会就地把它们改掉）——
    拆成一个个参数传，早晚漏传一个（而且漏传往往不报错，只是少收内容）。
    """

    __slots__ = ('builder', 'current_label', 'result', 'add_stmt', 'cur_inline',
                 'cond_inline', 'accum_cond', 'menu_option', 'nest_path',
                 'counters')

    def __init__(self, builder, current_label, result, add_stmt, cur_inline,
                 cond_inline, accum_cond, menu_option, nest_path, counters):
        self.builder = builder
        self.current_label = current_label
        self.result = result
        self.add_stmt = add_stmt
        self.cur_inline = cur_inline
        self.cond_inline = cond_inline
        self.accum_cond = accum_cond
        self.menu_option = menu_option
        self.nest_path = nest_path
        self.counters = counters


class ParseResultBuilder:
    def build(self, ast_nodes: list) -> ParseResult:
        result = ParseResult()
        # 先扫一遍字符串常量：DynamicCharacter 的显示名定义在
        # `default x_name = "..."`，而它可能出现在 define 之后。
        self._collect_string_defaults(ast_nodes, result)
        self._walk(ast_nodes, '', result)
        self._drop_file_block_comments(result)
        self._compute_counts(result)
        return result

    @staticmethod
    def _drop_file_block_comments(result: ParseResult) -> None:
        """
        已经被**文件级保真块**覆盖的注释，不许再算一条语句（否则渲染两遍）。

        为什么会有重叠（2026-10-18）：`_top_level_region` 按"顶层行 + 后面的
        缩进行"切块，于是

            #init python:              ← 顶层注释起一段
                # 添加打地鼠游戏入口      ← 缩进注释被收进这一段

        而**缩进注释在语义上仍然是它上面那个 label 的块体**（注释不参与缩进
        判断，也没有"注释块"这回事），`_build_tree` 会把它挂进 `label …:`
        的体里。两边都收 ⇒ 产物里同一行注释出现**两次**（真机
        `script.rpy` 实测），而逐字节保真审计看不见它（工具自己前后一致）。

        判据：注释语句的 (文件, 行号) 落在任一 `FileBlock` 的覆盖区间里
        → 丢掉那条语句。文件块仍然逐字带着它（渲染一次，位置在原处）。
        """
        spans: dict = {}
        for fb in result.file_blocks or ():
            start = int(getattr(fb, 'source_line', 0) or 0)
            code = str(getattr(fb, 'code', '') or '')
            if start <= 0:
                continue
            spans.setdefault(str(getattr(fb, 'source_file', '') or ''),
                             []).append((start, start + len(code.splitlines())))
        if not spans:
            return

        def covered(stmt) -> bool:
            file, line = (stmt.source or ('', 0))[:2]
            return any(a <= int(line) <= b
                       for a, b in spans.get(str(file or ''), ()))

        keep = [i for i, st in enumerate(result.statements)
                if not (st.kind == StatementKind.COMMENT and covered(st))]
        if len(keep) == len(result.statements):
            return
        total = len(result.statements)
        result.statements = [result.statements[i] for i in keep]
        # 两个平行数组**必须同进同退**（漏一个 = 语句与 label 错位，
        # 那是"把 A 的内容写到 B 的位置"级别的错）。
        for name in ('statement_labels', 'statement_menu_options'):
            seq = getattr(result, name, None)
            if isinstance(seq, list) and len(seq) == total:
                setattr(result, name, [seq[i] for i in keep])

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
              accum_cond: str = '', menu_option: str = '',
              nest_path: str = '', counters: dict = None,
              cond_note: str = ''):
        #: 同 kind 兄弟块的**文档序**计数器（整个 label 共用一份）——
        #: `menu:2` 就是"这个 label 里第 2 个菜单块"，与按行扫出来的顺序一致。
        counters = counters if counters is not None else {}
        #: 当前 AST 节点的行尾注释（`add_stmt` 读它 —— 逐节点更新）
        cur_inline = ['']
        #: 待消费的"条件行注释"（`if/elif/else` 头那一行；只给分支第一条语句）。
        #: ⚠ 由**参数**seeded —— `_walk` 是递归的，每层都有自己的局部变量，
        #: 在上层设一个局部量、指望下层的 `add_stmt` 读到，是读不到的
        #: （第一版就是这么错的：AST 上 `branch_comments` 明明有值，产物里却没有）。
        cond_inline = [cond_note or '']

        def add_stmt(stmt, path: str = None, inline: str = None):
            # `cond_comment`：这一拍渲染出来的 `if …:` 那一行要贴的注释
            # （来自 `if/elif/else` 头那一行）。**一次性**：只给本分支第一条。
            """
            收集语句，同时记下它属于哪个 label / 哪个菜单选项。

            Statement 本身不带 label（那是"内容指纹"契约的一部分），
            所以归属信息放在平行的 statement_labels /
            statement_menu_options 里。

            `_menu_member`（2026-10-05 加）是**结构位**：这条语句是不是
            "某个菜单块内部的东西"（菜单头 / 选项 / 选项体）。

            为什么必须记在语句上：`menu` 在 Ren'Py 里是**嵌套块**，而语句流
            是扁平的 —— 一旦走出 `_walk`，"选项体到哪儿结束"就再也算不出来，
            于是渲染层无法把扁平拍还原成 `menu:` 块（P0-1：菜单整块丢失、
            选项变裸旁白、分支变死代码）。这里记一位，比让渲染层去猜
            （比行号、比条件）都可靠。

            它**不进 Excel、不参与相等/哈希**：`.rpy` 侧的信息。
            Excel 往返之后 Beat 上没有这一位，`beat_render` 会退回按
            行号 / 条件推断（见 `_take_menu_run`）。
            """
            # **空台词不产出语句**（2026-10-05 修）：`who` 与 `what` 都为空
            # 的 `say` 是解析噪声，不是演出内容 —— 真实项目实测它来自
            # **空行**与**三引号文档字符串**（`eyes_tracker.rpy` 的 3 条
            # 全在 `"""…"""` 里）。留着它的代价不是"多一行"：
            # `Beat.is_empty()` 会把这条拍丢掉，可它不是干净地消失 ——
            # 整个文件因此变成"有拍但空标签"的 `label start: pass`
            # （实测 `eyes_tracker.rpy` 的产物就是这样），而这张拍在
            # Excel 往返里必然对不上（原文有、产物没有）。
            if stmt.kind == StatementKind.SAY and not (
                    (stmt.payload or {}).get('speaker')
                    or (stmt.payload or {}).get('text')):
                return
            stmt._menu_member = bool(menu_option)
            # **结构位（块路径）**：这条语句在哪个块里（B 阶段一）。
            # 与 `_menu_member` 一样是**运行期动态属性**（不是 dataclass 字段）
            # —— Statement 的相等/哈希是"内容指纹"契约的一部分，结构位不许进去。
            #   · `''`   = label 顶层
            #   · `if:1>menu:2>A>if:3` = 第 1 个 if → 第 2 个菜单 → 第 A 个选项 → 第 3 个 if
            stmt.nest_path = nest_path if path is None else path
            # 行尾注释（C）：与 `nest_path` 一样是**动态属性**，不进内容指纹。
            # `inline` 用于"这一行的注释不在 AST 节点上"的情况（菜单选项行）。
            stmt.inline_comment = (cur_inline[0] if inline is None else inline)
            if cond_inline[0]:
                stmt.cond_comment = cond_inline[0]
                cond_inline[0] = ''
            result.statements.append(stmt)
            result.statement_labels.append(current_label)
            result.statement_menu_options.append(menu_option)

        box = _WalkCtx(self, current_label, result, add_stmt,
                       cur_inline, cond_inline, accum_cond,
                       menu_option, nest_path, counters)
        for node in nodes:
            self._dispatch_node(node, box)

    def _dispatch_node(self, node, box):
        """
        `_walk` 的那条大分发（23 个 `isinstance`）—— **原样搬出来**，
        只为拆复杂度；判据/注释/顺序一个字都没动。

        上下文（`add_stmt` / 行尾注释 / 条件注释 / 结构位…）全在 `box` 上：
        它们是**逐层递归共享的可变状态**，必须显式传递，不能各留一份。
        """
        # 行尾注释（C）：本节点这一行的注释，`add_stmt` 会读到它
        box.cur_inline[0] = getattr(node, 'inline_comment', '')
        if isinstance(node, LabelNode):
            self._w_label(node, box)

        elif isinstance(node, DefineNode):
            if ParseResultBuilder._is_char_define(node):
                display = node.display_name
                if not display and node.name_var:
                    display = box.result.string_defaults.get(node.name_var, '')
                box.result.characters[node.target] = CharacterDefinition(
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
            if node.name and node.name not in box.result.images:
                box.result.images[node.name] = {
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
            if node.name and node.name not in box.result.images:
                box.result.images[node.name] = {
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
            if node.name not in box.result.transforms:
                box.result.transforms[node.name] = TransformDefinition(
                    name=node.name,
                    params=node.params,
                    body=node.body,
                    doc=node.doc,
                    source_file=node.filename,
                    source_line=node.line_number,
                )

        elif isinstance(node, SayNode):
            self._w_say(node, box)

        elif isinstance(node, ExtendNode):
            self._w_extend(node, box)

        elif isinstance(node, MenuNode):
            self._w_menu(node, box)

        elif isinstance(node, ShowNode):
            box.add_stmt(Statement.make_show(
                node.image, at=node.at, as_=node.as_, with_=node.with_,
                zorder=node.zorder, onlayer=node.onlayer,
                behind=node.behind, is_expression=node.is_expression,
                condition=box.accum_cond,
                source=(node.filename, node.line_number)))

        elif isinstance(node, SceneNode):
            box.add_stmt(Statement.make_scene(
                node.image, at=node.at, with_=node.with_,
                onlayer=node.onlayer,
                is_expression=node.is_expression,
                condition=box.accum_cond,
                source=(node.filename, node.line_number)))

        elif isinstance(node, HideNode):
            box.add_stmt(Statement.make_hide(
                node.image, with_=node.with_, condition=box.accum_cond,
                source=(node.filename, node.line_number)))

        elif isinstance(node, AudioNode):
            box.add_stmt(Statement.make_play(
                node.action, node.channel, node.name,
                loop=node.loop, fadein=node.fadein,
                fadeout=node.fadeout, extra=getattr(node, 'extra', ''),
                condition=box.accum_cond,
                source=(node.filename, node.line_number)))

        elif isinstance(node, VoiceNode):
            box.add_stmt(Statement.make_voice(
                node.name, box.accum_cond,
                source=(node.filename, node.line_number)))

        elif isinstance(node, TopLevelRegionNode):
            # **整个顶层区间**（label 之外的一切）→ 文件级保真块，
            # 按原文顺序追加。见 `TopLevelRegionNode` 的 docstring。
            for head, code, line_no in node.blocks or ():
                box.result.file_blocks.append(FileBlock(
                    header=head, code=code,
                    source_file=node.filename, source_line=line_no))
                # ATL 形式的图像声明（`image 名:` + 块体）**也进资源清单**
                # （2026-10-19，审计 P1-3 的最后一小块）：它从前只是"逐字
                # 保真"，于是 资源表 里没有这个图 —— 策划在「立绘」下拉里
                # 选不到它，`show 名` 还会被报成"缺资源"。
                # 真机对照：`sr_general` 3 处、官方 `tutorial` 10 处。
                _register_atl_image(box.result, head, node.filename, line_no)

        elif isinstance(node, PythonNode):
            # **顶层**的 `python:` / `init python:` 块**不进"拍"**。
            #
            # 2026-10-05：以前这里无条件 `add_stmt`，而顶层块根本没走到这儿
            # （被 `_stmt_unmodeled` 跳过了）。进了 statements 就会变成一张
            # "标签为空"的拍，被渲染进 `label start:` 里 —— 而它本该在
            # **文件级**。
            #
            # 现在顶层内容统一由 `TopLevelRegionNode` 按**原文**带出
            # （它连 `$ x = 1` 这种顶层单行也一并保真，不再多此一举），
            # 所以这里只要不在 label 里就**直接跳过**，不重复带一份。
            if box.current_label:
                box.add_stmt(Statement.make_python(
                    node.code, box.accum_cond,
                    source=(node.filename, node.line_number),
                    dollar_space=getattr(node, 'dollar_space', True)))

        elif isinstance(node, CommentNode):
            # 只保留 label 内的注释——顶层注释属于文件头/定义区，
            # 不属于任何演出流，混进表格反而会误导文案。
            if box.current_label:
                box.add_stmt(Statement.make_comment(
                    node.text, box.accum_cond,
                    source=(node.filename, node.line_number)))

        elif isinstance(node, LoopNode):
            # 循环块（D2/T0）：**头行一条语句 + 块体各一条语句**。
            #
            # 这里是"给拍模型加作用域"的落点：
            #   · 头行自己一条 `LOOP` 语句，它的 `nest_path` 是**外层**
            #     路径（循环头在父块里，不在自己里面 —— 与菜单头同款）；
            #   · 块体用 `join_nest(…, f'loop:{n}')` 递归 walk，
            #     于是"这一拍在哪个循环里"变成**随表走的结构位**。
            # `counters` 与 `if`/`menu` 共用一份（label 内的文档序），
            # 所以 `loop:2` 与按行扫出来的顺序一致。
            #
            # ⚠ 块体**不累积头行的条件**吗？累积的（`accum_cond` 原样传下去）。
            # 这是与 `if` 分支**刻意不同**的一处：循环体不是"条件成立才执行
            # 一次"，而是"条件成立才进入循环"，渲染层负责把这一层条件
            # **写回循环头外面**并从块体拍上剥掉（见 `render_loop_block`），
            # 否则每一句块体台词都会各自套一个 `if`。
            if box.current_label:
                box.counters['loop'] = box.counters.get('loop', 0) + 1
                box.add_stmt(Statement.make_loop(
                    node.header, text=node.text, walked=True,
                    condition=box.accum_cond,
                    source=(node.filename, node.line_number)))
                self._walk(
                    node.body_nodes, box.current_label, box.result, box.accum_cond,
                    box.menu_option,
                    nest_path=join_nest(
                        box.nest_path, f"loop:{box.counters['loop']}"),
                    counters=box.counters)

        elif isinstance(node, CameraNode):
            # 3D 舞台的 `camera` 块（2026-10-07）：与循环块同一档 ——
            # 头行（层名）结构化、块体（ATL 属性）逐字。
            if box.current_label:
                box.add_stmt(Statement.make_camera(
                    node.text, header=node.header, layer=node.layer,
                    body=node.body, condition=box.accum_cond,
                    source=(node.filename, node.line_number)))

        elif isinstance(node, RawNode):
            if box.current_label:
                # **未建模块**先记一笔账（体检报告 / 门禁要用），
                # 内容照旧走下面同一条保真通道。见
                # `ParseResult.unknown_blocks` 的说明。
                if isinstance(node, UnknownBlockNode):
                    box.result.unknown_blocks.append({
                        'head': node.head,
                        'file': node.filename,
                        'line': node.line_number,
                        'lines': len(node.block_raw) + 1,
                    })
                box.add_stmt(Statement.make_raw(
                    node.text, raw_kind=node.raw_kind,
                    condition=box.accum_cond,
                    source=(node.filename, node.line_number)))

        elif isinstance(node, JumpNode):
            box.add_stmt(Statement.make_jump(
                node.target, box.accum_cond,
                source=(node.filename, node.line_number)))

        elif isinstance(node, CallNode):
            box.add_stmt(Statement.make_call(
                node.target, box.accum_cond,
                source=(node.filename, node.line_number),
                from_clause=getattr(node, 'from_clause', ''),
                args=getattr(node, 'args', '')))

        elif isinstance(node, ReturnNode):
            box.add_stmt(Statement.make_return(
                box.accum_cond,
                source=(node.filename, node.line_number)))

        elif isinstance(node, IfNode):
            # else 分支要写成 `not (前面的条件)`：以前直接给空条件，
            # 导致"else 里的 jump/台词"在图上是无条件边、在表里
            # Condition 列空白——和 GraphBuilder 的语义不一致。
            # 现在两条路径（AST 图 / Statement 图）用同一套组合规则。
            #
            # **elif 也一样**：`if a / elif b / else` 的第 2 个分支隐含
            # "a 没走"。以前只 AND 了 b 自己，于是 `if score>3: jump X /
            # elif score>1: jump Y` 的第 2 条边条件写成 `score > 1`
            # （比实际宽），图上标签会误导人、按图导出也会写错分支。
            self._w_if(node, box)

    def _w_label(self, node, box):
        """LabelNode 的处理（从 `_dispatch_node` 原样搬出）。"""
        box.result.labels[node.name] = (node.filename, node.line_number)
        if getattr(node, 'params', ''):
            box.result.label_params[node.name] = node.params
        self._walk(node.block, node.name, box.result)

    def _w_say(self, node, box):
        """SayNode 的处理（从 `_dispatch_node` 原样搬出）。"""
        box.result.dialogues.append(DialogueLine(
            character_var=node.who,
            text=node.what,
            label=box.current_label,
            source_file=node.filename,
            source_line=node.line_number,
        ))
        box.add_stmt(Statement.make_say(
            node.who, node.what, box.accum_cond,
            source=(node.filename, node.line_number),
            tail=getattr(node, 'tail', '')))

    def _w_extend(self, node, box):
        """ExtendNode 的处理（从 `_dispatch_node` 原样搬出）。"""
        box.result.dialogues.append(DialogueLine(
            character_var='',
            text=node.text,
            label=box.current_label,
            source_file=node.filename,
            source_line=node.line_number,
        ))
        box.add_stmt(Statement.make_extend(
            node.text, box.accum_cond,
            source=(node.filename, node.line_number)))

    def _w_menu(self, node, box):
        """MenuNode 的处理（从 `_dispatch_node` 原样搬出）。"""
        if box.current_label:
            box.result.menu_labels.add(box.current_label)
        if node.caption:
            box.result.dialogues.append(DialogueLine(
                character_var='',
                text=node.caption,
                label=box.current_label,
                source_file=node.filename,
                source_line=node.line_number,
            ))
        # **MENU 语句一律产出**（2026-10-05 修 P0-1 的根因）。
        #
        # 以前只在 `node.caption` 非空时才 `add_stmt(make_menu(...))`，
        # 于是**裸 `menu:`（没有标题）在语句流里完全不存在** ——
        # 真实项目 7 处菜单全是这种写法，全部中招。后果一路往下传：
        #
        #   · 拍这一层看不到"这里有个菜单"（`beats_from_statements`
        #     只能听 MENU 语句的），`menu_option` 拍于是成了孤立的
        #     拍，渲染层把选项当裸旁白导出去；
        #   · `validate_level2` 按 `menu_labels` 判"有选项却没 menu 行"，
        #     计数与真实脚本对不上。
        #
        # 空 caption 是**合法**的菜单（Ren'Py 允许 `menu:` 无标题），
        # 所以这里照常产出，由下游决定"空标题怎么显示"。
        # MENU 语句的 condition = **包在 `menu:` 外面那一层的条件**。
        #
        # 为什么要单独算（2026-10-05 实测）：`accum_cond` 同时包含
        # "外层 `if`"与"选项体累积条件"两种来源，而渲染层需要分清
        #
        #     if flag:            ← `if` 要写在 **menu 外面**
        #         menu:
        #             "A":
        #                 jump a
        #
        #     menu:               ← `if` 要写在 **选项那一行**
        #         "A" if flag:
        #             jump a
        #
        # 判据：菜单**自身**的 `if` 会出现在"选项体最浅一层"的条件里
        # （`_combine` 把它并进了选项体），因此
        # `self._combine(accum_cond, opt.condition)` 就是承载它的值；
        # 只有"外层 if"（与选项无关）才需要整块包住。
        #
        # 具体做法：选项自己的 condition 为空 → 那个累积值来自菜单
        # 外面那一层 → 整块 `if`；否则菜单没有自己的 `if`。
        menu_own_cond = ''
        if node.options and all(
                not (getattr(o, 'condition', '') or '').strip()
                for o in node.options):
            menu_own_cond = box.accum_cond
        box.add_stmt(Statement.make_menu(
            node.caption or '', menu_own_cond,
            extras=list(getattr(node, 'extras', None) or ()),
            # 菜单名 / 参数表（2026-10-19，审计 P1-3 收尾）：从前这两样
            # 让整块掉进"未建模"通道 —— 一个字节不丢，但选项在卡片里
            # 彻底看不见。现在它们各占一格，选项重新成为可编辑的拍。
            name=getattr(node, 'name', '') or '',
            args=getattr(node, 'args', '') or '',
            source=(node.filename, node.line_number)))
        box.counters['menu'] = box.counters.get('menu', 0) + 1
        menu_path = join_nest(box.nest_path, f"menu:{box.counters['menu']}")
        for _oi, opt in enumerate(node.options, start=1):
            box.result.dialogues.append(DialogueLine(
                character_var='',
                text=opt.text,
                label=box.current_label,
                source_file=opt.filename,
                source_line=opt.line_number,
                is_menu_option=True,
                menu_condition=opt.condition,
            ))
            # 选项行本身**在菜单里、不在任何选项体里** → 路径到菜单为止
            box.add_stmt(Statement.make_menu_option(
                opt.text, self._combine(box.accum_cond, opt.condition),
                source=(opt.filename, opt.line_number)), menu_path,
                inline=getattr(opt, 'inline_comment', ''))
            self._walk(opt.block, box.current_label, box.result,
                       self._combine(box.accum_cond, opt.condition),
                       opt.text,
                       nest_path=join_nest(menu_path, option_key(_oi)),
                       counters=box.counters)

    def _w_if(self, node, box):
        """IfNode 的处理（从 `_dispatch_node` 原样搬出）。"""
        prev_conds = []
        # `_bi` = 分支下标：取"这一分支自己那一行"的注释要用它，
        # **不能**用 `len(prev_conds)`（那个列表在分支体里已 append，差一位）
        for _bi, (cond, block) in enumerate(node.branches):
            if cond:
                child_cond = self._combine(box.accum_cond, cond)
                if prev_conds:
                    neg = ' or '.join(f'({c})' for c in prev_conds)
                    child_cond = (f"({child_cond}) and not ({neg})"
                                  if child_cond else f"not ({neg})")
                prev_conds.append(cond)
            else:
                neg = ' or '.join(f'({c})' for c in prev_conds)
                child_cond = (
                    f"({box.accum_cond}) and not ({neg})"
                    if box.accum_cond else f"not ({neg})"
                )
            box.counters['if'] = box.counters.get('if', 0) + 1
            # 这一分支的注释（`if/elif/else` 头那一行）交给它第一条语句。
            # ⚠ 用 `_bi`（分支下标），**不能**用 `len(prev_conds)` ——
            # 那个列表在分支体里已经 append 过，会差一位（实测：第一分支
            # 拿到的是 `elif` 那行的注释）。
            _bcs = getattr(node, 'branch_comments', None) or []
            self._walk(block, box.current_label, box.result, child_cond,
                       box.menu_option,
                       nest_path=join_nest(
                           box.nest_path, f"if:{box.counters['if']}"),
                       counters=box.counters,
                       cond_note=(_bcs[_bi] if _bi < len(_bcs)
                                  else ''))



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
        self._existing_edges: dict = {}

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
            except (TypeError, ValueError) as e:
                swallow('renpy_parser.build_from_statements', e)

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
                               cond, option,
                               payload.get('from_clause', ''))
            elif kind == StatementKind.PYTHON:
                nid = self._label_to_nid.get(label)
                if nid is not None:
                    node = self.graph.nodes.get(nid)
                    if node is not None:
                        # **整条语句**进节点（含 `condition` 与 `source`
                        # 文件/行号）—— 以前只塞 `payload['code']` 字符串，
                        # 条件和出处到这里就丢了。
                        node.add_statement(st)

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
                n.clear_operations()

        self._existing_edges = {}
        for e in self.graph.edges.values():
            # 值放边 id：重入时若新解析带来 rom 子句，就补到这条边上
            # （键不含 from —— 带上它会把同一条边重复加一遍）
            self._existing_edges[
                (e.from_id, e.to_id, e.type, e.condition, e.option_text)] = e.id

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
                               accum_cond, option_text,
                               getattr(node, 'from_clause', ''))

            elif isinstance(node, IfNode):
                prev_conds = []
                for cond, block in node.branches:
                    if cond:
                        new_cond = self._combine(accum_cond, cond)
                        # **if/elif/else 的语义**：第 2 个及以后的分支还隐含
                        # "前面所有分支都没走"。以前这里只 AND 了自己的条件，
                        # 于是 `if a: jump X / elif b: jump Y` 的第 2 条边写成
                        # `b`（正确是 `b 且 not a`）—— 图上的条件标签会误导人，
                        # 按图导出时也会把 `elif` 写成永远更宽的条件。
                        # `else` 分支本来就做了 De Morgan，这里把 elif 补齐，
                        # 两者口径一致。
                        if prev_conds:
                            neg = " or ".join(f"({c})" for c in prev_conds)
                            new_cond = (f"({new_cond}) and not ({neg})"
                                        if new_cond else f"not ({neg})")
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
                # 这是 v3 [FIX-6] 有意为之。
                # 迁移（2026-10-05）：这里存**结构化语句**，把 `accum_cond`
                # （条件前缀）与 `source`（文件/行号）一起带上 —— 以前只留
                # 字符串，下游再也看不出这条代码在什么条件里。
                # `operations` 兼容视图只取代码文本，行为一字不变。
                nid = self._label_to_nid.get(current_label)
                if nid is not None:
                    n = self.graph.nodes.get(nid)
                    if n is not None:
                        n.add_statement(Statement.make_python(
                            node.code, accum_cond,
                            source=(getattr(node, 'filename', ''),
                                    getattr(node, 'line_number', 0)),
                            dollar_space=getattr(node, 'dollar_space', True)))

    def _add_edge(self, from_label: str, to_label: str,
                  etype: str, condition: str, option_text: str = '',
                  from_clause: str = ''):
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

        # 去重键**不含 from_clause**：同一个 (from,to,type,cond,option)
        # 就是同一条边，带上 from 反而会造出重复边（AST 与 Statement 两条
        # 解析路径里总有一条抓不到 from，实测直接把边翻倍）。
        key = (from_id, to_id, etype, condition, option_text)
        if key in self._existing_edges:
            # 已经有这条边 → 不重复建；但如果这次带来了 `from` 子句而旧的
            # 没有，就把它补上（谁抓到了就以谁为准，不会丢）。
            edge = self.graph.edges.get(self._existing_edges[key])
            if (edge is not None and from_clause
                    and not getattr(edge, 'from_clause', '')):
                edge.from_clause = from_clause
            return
        eid = self.graph.add_edge(from_id, to_id, etype=etype,
                                  condition=condition,
                                  option_text=option_text,
                                  from_clause=from_clause)
        self._existing_edges[key] = eid

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
        """
        翻译单元 id：`{安全 label}_{内容指纹}`，冲突再加 `_1`/`_2`。

        ⚠ 指纹位数**必须**与 `core.statement.CONTENT_HASH_HEX` 走同一个常量
        （2026-10-05 修）。这里原来是**写死的 `[:8]`**，而 `Statement`
        那边也写死 `[:8]` —— 两处手抄同一个数字，正是文档里明令禁止的
        "各自拼接"（`core/uid.py` 的 docstring 第 4-5 行）。
        后果不是立刻错，而是**改位数时只改一边**：
        `Statement.content_hash` 变成 16 位、TU 的 hash 还是 8 位，
        两者不再"逐字节一致"，而没有任何测试会发现
        （各自内部都自洽）。所以这里改成引用常量。
        """
        h = hashlib.md5(content.encode('utf-8')).hexdigest()[
            :CONTENT_HASH_HEX]
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
      · 缓存元组： (mtime, size, ast_nodes, errors, warnings, source_text)
      · mtime + size 未变 → 复用 AST 与错误/警告
        （`source_text` 也一起复用：CST 真迹必须切自**解析时那一份**）
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
        #: 路径 → **解析时读到的那一份**源文本（CST 真迹只许切自它）
        texts: dict = {}

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
                cached_text = cached[5] if len(cached) > 5 else ''
                if cached_text:
                    texts[str(fp_path)] = cached_text
                continue

            parser = RenPyParser()
            nodes = parser.parse(fp_path)
            file_errors = list(parser.errors)
            file_warnings = list(parser.warnings)
            # **同一次读**：解析器读到的文本原样留下来喂 CST（别再读一遍盘）。
            text = parser.source_texts().get(str(fp_path), '') or ''
            if text:
                texts[str(fp_path)] = text
            all_errors.extend(file_errors)
            all_warnings.extend(file_warnings)
            self._file_cache[fp_key] = (
                st.st_mtime, st.st_size, nodes, file_errors, file_warnings,
                text
            )
            all_ast.extend(nodes)

        for k in list(self._file_cache.keys()):
            if k not in current_files:
                del self._file_cache[k]

        parse_result = ParseResultBuilder().build(all_ast)
        # **CST（D3，2026-10-07）**：给每条语句挂上"它在原文里的行区间 +
        # 逐字原文 + 前导 trivia"。挂的是**动态属性**（不进 dataclass 的
        # 相等/哈希 —— 那是一条全局契约，不能为一次保真改动去动它）。
        #
        # 失败/取不到就静默跳过：拿不到 CST 时一切退回 D3 之前，
        # **没有降级风险**（这正是选动态属性的原因）。
        try:
            from storygraph.core.cst import attach_spans
            # `texts` 是**解析时读到的同一份**（见上面那个循环）。
            # 兜底：老式缓存（没有 text 那一位）/ 盘上新增的文件 → 读一次盘。
            # 这条兜底是**已知窗口**：它读到的可能已经不是解析时那一份，
            # 所以主路必须把 text 给全，别让它常态生效。
            for fp_path, _fp_key in unique_files:
                if str(fp_path) in texts:
                    continue
                try:
                    texts[str(fp_path)] = read_source_text(fp_path)
                except (OSError, UnicodeDecodeError) as e:
                    swallow('parsing.analyze.cst.read', e)
            parse_result.cst_attached = attach_spans(
                parse_result.statements, texts)
        except Exception as e:                  # noqa: BLE001
            swallow('parsing.analyze.attach_cst', e)
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

# 反转义实现在文件上方（`unescape_renpy_string`）—— 解析侧与翻译侧共用
# 同一份，别再抄一遍。这里保留一个私有别名，兼容既有调用。
_unescape_renpy_string = unescape_renpy_string



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
    # `# old "…"` = 对话块的"原文"注释（**必须带 `#`**）。
    # `old "…"`（无 `#`）= `strings:` 块的原文条目。
    # 两者分开：合成一个正则会把 `strings:` 块的条目认成注释。
    re_old_comment = re.compile(r'^\s+#\s*old\s+' + _TR_STR)
    re_old = re.compile(r'^\s+old\s+(?!#)' + _TR_STR)
    # `new "…"` 只认**缩进**的行（`^\s+`）。
    # 用 `^\s*` 会把**零缩进**的 `new "…"` 也吃进去 —— 而 `strings:` 块
    # 的 `new` 就在零缩进上，被这里先截走之后字符串翻译一条都读不到。
    re_new = re.compile(r'^\s+new\s+(?!#)' + _TR_STR)
    # `strings:` 块的 `new` 在**零缩进**上（`old` / `new` 与 `translate`
    # 顶格对齐），所以单独一条。
    re_new_strings = re.compile(r'^\s*new\s+(?!#)' + _TR_STR)
    # 现代 tl 格式：`translate <lang> <id>:` 下面**直接跟一句裸字符串**。
    # Ren'Py 自己生成的 tl 文件长这样，而旧实现只认 `# old` / `new`，
    # 于是新式文件的译文一条都读不到（翻译面板空的，且不报错）。
    re_bare_dialogue = re.compile(
        r'^\s+(?:[A-Za-z_]\w*\s+)?' + _TR_STR + r'\s*$')

    def _pick_named(m):
        """
        取正则捕获的字符串字面量，并**反转义**。

        `"他说：\\"走吧\\""` 取出来是 `他说：\\"走吧\\"`（正则里的 `\\.` 保留了
        反斜杠），而它的**实际文本**是 `他说："走吧"`。返回未反转义的原文会让
        翻译面板显示 `\\"`（用户看到的是源码而不是文字），拿去写回也会多出
        一层转义。Ren'Py 的字符串里 `\\"` / `\\'` / `\\\\` / `\\n` 都是转义。
        """
        v = m.group('dq')
        if v is None:
            v = m.group('sq')
        if v is None:
            return ''
        return _unescape_renpy_string(v)

    for lang_dir in tl_dir.iterdir():
        if not lang_dir.is_dir():
            continue
        language = lang_dir.name
        for fp in lang_dir.glob('*.rpy'):
            try:
                lines = fp.read_text(encoding='utf-8').splitlines()
            except Exception as e:        # noqa: BLE001
                # 读不了这一个文件（编码坏 / 权限 / 中途删了）就跳过它 ——
                # 但**记一笔**：整份翻译文件静默消失，比报错更难查。
                swallow('parsing.renpy_parser.parse_translation_files', e)
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
                        # **先登记**：现代 tl 格式里对话 id 下面只有一句译文
                        # （没有 `# old`），译文行才补 `translated`。
                        # 不先登记的话那条线的 `language` 永远缺（实测 KeyError）。
                        result.setdefault(current_id, {'language': language})
                    continue

                if re_translate_any.match(line):
                    # translate python: / translate style foo: 等
                    current_id = None
                    current_lang = None
                    in_strings_block = False
                    continue

                # 原文：对话块是 `# old "…"`，`strings:` 块是 `old "…"`。
                m = re_old_comment.match(line) or re_old.match(line)
                if m:
                    orig = _pick_named(m)
                    if in_strings_block and current_lang:
                        h = hashlib.md5(
                            orig.encode('utf-8')
                        ).hexdigest()[:8]
                        key = f"{current_lang}.strings_{h}"
                        current_id = key
                        entry = result.setdefault(key, {})
                    elif current_id:
                        entry = result.setdefault(current_id, {})
                    else:
                        entry = None
                    if entry is not None:
                        # **逐字段写**，不用 `setdefault(key, {...})`：
                        # 对话 id 在 `translate` 那一行就登记过了，
                        # `setdefault` 遇到已存在的键会**整块跳过**，
                        # `original` 于是永远补不上（实测 KeyError）。
                        entry['original'] = orig
                        entry.setdefault('language', language)
                    continue

                if in_strings_block:
                    m = re_new_strings.match(line)
                    if m and current_id:
                        result.setdefault(current_id, {})['translated'] = \
                            _pick_named(m)
                        continue

                m = re_new.match(line)
                if m and current_id and not in_strings_block:
                    result.setdefault(
                        current_id, {'language': language})['translated'] = \
                        _pick_named(m)
                    continue

                # **现代 tl 格式**：对话 id 下面直接一句裸字符串
                # （`"你好啊"` 或 `e "你好啊"`）。
                if current_id and not in_strings_block:
                    m = re_bare_dialogue.match(line)
                    if m:
                        result.setdefault(current_id, {})['translated'] = \
                            _pick_named(m)
                        continue

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

    print("=== 首次解析 ===")
    print(f"文件数:    {len(scripts)}")
    print(f"角色定义:  {len(result.characters)}")
    print(f"对话行数:  {len(result.dialogues)}")
    print(f"标签数:    {len(result.labels)}")
    print(f"翻译单元:  {len(result.translation_units)}")
    print(f"错误:      {len(result.errors)}")
    print(f"警告:      {len(result.warnings)}")

    print("\n=== 角色 ===")
    for var, ch in sorted(result.characters.items(),
                          key=lambda x: -x[1].dialogue_count):
        print(f"  {var}: {ch.display_name}  ({ch.dialogue_count} 行)")

    print("\n=== 翻译单元（前5）===")
    for tu in result.translation_units[:5]:
        print(f"  {tu.identifier}  @ {tu.label}")

    if graph is not None:
        print("\n=== 控制流 ===")
        print(f"节点: {len(graph.nodes)}, 边: {len(graph.edges)}")

    result2, graph2 = analyzer.analyze(scripts, graph=graph)
    print("\n=== 二次解析（缓存命中）===")
    print(f"错误:      {len(result2.errors)}  (应与首次一致)")
    print(f"警告:      {len(result2.warnings)}  (应与首次一致)")
    print(f"缓存:      {analyzer.stats()['cached_files']} 个文件")