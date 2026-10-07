"""
往返**幂等**：导出一次与导出 N 次必须逐字节相同
================================================

`tools/hell_edit_rounds.py` 的轴 A2 把文本**归一化**之后再比（它要过滤
"我故意插卡"的噪声），于是有一类缺陷它看不见：**文件级的东西每轮长一行**。

实测现场（2026-10-07，跑 `--only A2 -v` 时从 verbose 输出里看出来的）：

    R1 = 5 行        R2 = 7 行        R3 = 7 行
    ['# 由剧本表自动生成…', 'label a:', …]
    ['# 由剧本表自动生成…', '# 由剧本表自动生成…', 'label a:', …]

**生成标记被当成"作者的顶层内容"带回来了，而渲染时又加了一条。**
后果不是"多一行注释"那么轻：

  · 产物每过一轮全量就长一行 —— 用户看到的是"工具在改我的文件"；
  · 更糟的是**增量对位**：多出来的一行会让"没动过"的文件在第二轮
    被判成"文本变了"，于是**整份退回全量**（而全量又继续长）。

这个文件的判据是**逐字节**的（不用归一化）：
`render(parse(text)) == text` 对任意轮次都成立。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core.vocabulary import GENERATOR_MARKER
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

SRC = (
    'label a:\n'
    '    "第一句"\n'
    '    menu "标题":\n'
    '        "选项":\n'
    '            jump b\n'
    '    return\n'
)

#: 顶部带**我们自己的生成标记**的文件（工具产物就长这样）
GENERATED = GENERATOR_MARKER + '\n\n' + SRC


def _round(tmp_path, text: str, name: str) -> str:
    f = tmp_path / name
    f.write_text(text, encoding='utf-8')
    pr = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)[0]
    return render_beats_to_rpy(beats_from_parse_result(pr))


def test_generator_marker_does_not_accumulate(tmp_path):
    """
    **产物再导出一次，不许长出第二行生成标记。**

    判据用**精确注释行数**（不是"包含"）—— "包含"对"多了一行"是瞎的，
    而"多了一行"正是这个缺陷的全部内容。
    """
    r1 = _round(tmp_path, GENERATED, 'r1.rpy')
    n1 = sum(1 for ln in r1.splitlines() if ln.strip() == GENERATOR_MARKER)
    assert n1 == 1, f'第一轮就有 {n1} 条生成标记：\n{r1}'

    r2 = _round(tmp_path, r1, 'r2.rpy')
    n2 = sum(1 for ln in r2.splitlines() if ln.strip() == GENERATOR_MARKER)
    assert n2 == 1, f'第二轮长成了 {n2} 条生成标记：\n{r2}'


def test_render_parse_render_is_a_fixed_point(tmp_path):
    """
    **逐字节不动点**：`render(parse(p))` 再跑一轮必须一模一样。

    这条比"内容没丢"更强：它同时挡住"每轮多一行""每轮改一次排版"
    这些**累积型**缺陷 —— 那类缺陷在单轮检查里完全看不出来。
    """
    r1 = _round(tmp_path, GENERATED, 'f1.rpy')
    r2 = _round(tmp_path, r1, 'f2.rpy')
    r3 = _round(tmp_path, r2, 'f3.rpy')
    assert r2 == r1, (f'第 1→2 轮变了：\n--- R1 ---\n{r1}\n--- R2 ---\n{r2}')
    assert r3 == r2, (f'第 2→3 轮变了：\n--- R2 ---\n{r2}\n--- R3 ---\n{r3}')


def test_marker_written_by_hand_is_kept_once(tmp_path):
    """手写的同一行注释：只应当留下**一条**（它就是全局唯一的那个标记）。"""
    text = GENERATOR_MARKER + '\n\n' + SRC
    r1 = _round(tmp_path, text, 'h1.rpy')
    assert r1.splitlines()[0].strip() == GENERATOR_MARKER, r1
    assert r1.count(GENERATOR_MARKER) == 1, r1


def test_plain_source_without_marker_gains_exactly_one(tmp_path):
    """没有标记的源文件：渲染后**恰好**得到一条（不能没有、也不能两条）。"""
    r1 = _round(tmp_path, SRC, 'p1.rpy')
    assert r1.count(GENERATOR_MARKER) == 1, r1


# ---------------------------------------------------------------------------
#  括号续行：**缩进不许每轮长一截**（真机实测过的那一族）
# ---------------------------------------------------------------------------

BRACKET = (
    'label a:\n'
    '    python:\n'
    '        positions = [\n'
    '            (200, 200), (600, 200),  # 第一行\n'
    '            (200, 400), (600, 400),\n'
    '        ]\n'
    '    return\n'
)


def _indent_of(lines: list, needle: str):
    for ln in lines:
        if needle in ln:
            return len(ln) - len(ln.lstrip())
    return None


def test_bracket_continuation_indent_does_not_drift(tmp_path):
    """
    **括号续行的缩进必须逐轮稳定**（真机实测过：`12 → 20 → 28 → 36 → 44`）。

    病根：`_flatten` 把括号未闭合的多行语句**并成一条逻辑行**（`text` 里
    含 `\\n`），而取块体时用的是 `node.text.lstrip()` —— `lstrip()` 对
    多行字符串**只作用于第一行**，续行带着**绝对缩进**进了 payload，
    被当成"更深一层"；渲染时整块再加一次基准 → **每导出一次就长一截**。

    后果不是"排版难看"：文件**永远在变** → 增量对位每轮都判"文本变了"
    → 整份退回全量。判据：`tools/audit_idempotence.py`。
    """
    r1 = _round(tmp_path, BRACKET, 'b1.rpy')
    r2 = _round(tmp_path, r1, 'b2.rpy')
    r3 = _round(tmp_path, r2, 'b3.rpy')
    for needle in ('positions = [', '(200, 200), (600, 200)',
                   '(200, 400), (600, 400)', ']'):
        a = _indent_of(r1.splitlines(), needle)
        b = _indent_of(r2.splitlines(), needle)
        c = _indent_of(r3.splitlines(), needle)
        assert a == b == c, (f'{needle!r} 的缩进在漂：{a} → {b} → {c}\n'
                             f'--- R1 ---\n{r1}\n--- R2 ---\n{r2}')
    # 而且续行不该被压到与首行齐平（那会把括号内的排版也吃掉）
    assert _indent_of(r1.splitlines(), '(200, 200)') > \
        _indent_of(r1.splitlines(), 'positions = [')


def test_bracket_continuation_keeps_author_indent(tmp_path):
    """首轮就要**精确复现作者写的缩进**（原文 12 → 产物 12，不是 20）。"""
    r1 = _round(tmp_path, BRACKET, 'k1.rpy')
    src_indent = _indent_of(BRACKET.split('\n'), '(200, 200)')
    assert _indent_of(r1.splitlines(), '(200, 200)') == src_indent, (
        f'续行缩进与原文不一致（原文 {src_indent}）：\n{r1}')


# ---------------------------------------------------------------------------
#  顶格注释：块**后面**的那种不许每轮往里缩一层
# ---------------------------------------------------------------------------
#
# 真机实测（2026-10-07，`tools/audit_idempotence.py`）：**5 个文件**同族。
#
#     init python:
#         ...
#     # 全局人物页面 screen      ← 作者写的是**下一段的标题**
#     screen character_gallery_screen():
#
# `_top_level_region` 的判据曾经是"当前段的头以 `:` 收尾 → 顶格注释收进块体"，
# 而这个顶格注释**语义上两边都合法**（Ren'Py / Python 都忽略注释的缩进）。
# 收进块体之后 `render_file_block` 给块体每行加 4 → **每导出一次缩一层**。
#
# ⚠ 为什么真机上"第一轮看着没事、第二轮才动"：源文件里那个注释与块之间
# 隔着一个 `label`（`_top_level_region` 会把 label 及其块体整段跳过 → `cur`
# 被清空 → 注释自己起一段）。而**渲染时文件级块会被提到所有 label 之前**
# → 产物里那个注释就紧跟在块体后面了 → 第二轮才被吸进去。所以夹具必须
# **带一个 label 插在中间**，否则复现不出来（判据会空转）。

TOP_COMMENT_AFTER_BLOCK = (
    'init python:\n'
    '    def f():\n'
    '        return 1\n'
    '\n'
    'label a:\n'
    '    "hi"\n'
    '\n'
    '# 下一段的标题\n'
    'screen gallery():\n'
    '    text "y"\n'
)


def test_top_level_comment_after_block_does_not_sink(tmp_path):
    """
    块**之后**的顶格注释：逐轮都停在第 0 列（不许被吸进块体加 4）。

    判据用**缩进值**而不是"注释还在不在" —— 它在两种实现下都在，
    变的只是缩进，而缩进就是全部症状。
    """
    r1 = _round(tmp_path, TOP_COMMENT_AFTER_BLOCK, 't1.rpy')
    r2 = _round(tmp_path, r1, 't2.rpy')
    r3 = _round(tmp_path, r2, 't3.rpy')
    for tag, text in (('R1', r1), ('R2', r2), ('R3', r3)):
        ind = _indent_of(text.splitlines(), '# 下一段的标题')
        assert ind == 0, (f'{tag}：注释被推进去了 {ind} 格：\n{text}')
    assert r2 == r1, f'第 1→2 轮变了：\n--- R1 ---\n{r1}\n--- R2 ---\n{r2}'
    assert r3 == r2, f'第 2→3 轮变了：\n--- R2 ---\n{r2}\n--- R3 ---\n{r3}'


# 与上面相反的一面：块**中间**的顶格注释必须**留在块体里**。
# 真实写法（`whackamole.rpy`）：
#
#     init python:
#         import random
#     # 默认变量（会进入 store……）      ← 顶格注释，但后面还有本块的缩进行
#         def mole_cell(r, c):
#
# 不收进来 → 块被劈成两段、`init python:` 这个块头被丢掉 →
# 产物里出现一段**悬空的缩进**（Ren'Py 直接报缩进错）。
TOP_COMMENT_INSIDE_BLOCK = (
    'init python:\n'
    '    import random\n'
    '# 默认变量（会进入 store……）\n'
    '    def mole_cell(r, c):\n'
    '        return random.randint(1, 3)\n'
    '\n'
    'label a:\n'
    '    "hi"\n'
)


def test_top_level_comment_inside_block_stays_inside(tmp_path):
    """
    块**中间**的顶格注释：块头不许被丢掉，块体不许变成悬空缩进。

    判据是**整段结构**：`init python:` 之后到顶层语句之前，
    每一非空行的缩进都 > 0（悬空缩进 = 那段里冒出一个顶格行）。
    """
    r1 = _round(tmp_path, TOP_COMMENT_INSIDE_BLOCK, 'i1.rpy')
    r2 = _round(tmp_path, r1, 'i2.rpy')
    lines = r1.splitlines()
    assert 'init python:' in lines, r1
    start = lines.index('init python:') + 1
    # 块体到下一个顶层行（或结尾）为止
    body = []
    for ln in lines[start:]:
        if ln.strip() and not ln[:1].isspace():
            break
        body.append(ln)
    assert any(l.strip() == '# 默认变量（会进入 store……）' for l in body), (
        f'那条顶格注释没进块体：\n{r1}')
    assert all(not l.strip() or l[:1].isspace() for l in body), (
        f'块体里出现了顶格行（悬空缩进）：\n{r1}')
    assert r2 == r1, f'第 1→2 轮变了：\n--- R1 ---\n{r1}\n--- R2 ---\n{r2}'
