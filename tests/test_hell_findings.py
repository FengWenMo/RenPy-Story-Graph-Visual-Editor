#!/usr/bin/env python
"""
地狱测试抓到的两个 P0 的回归钉子
================================

`tools/hell_test.py`（组合面轰炸）在 2026-10-05 抓到两处**真缺陷**，
都具备"产物看起来能跑、实际上已经坏了"的特征 —— 也就是最贵的那类：

**P0-1 反斜杠写出物理换行**（`story/beat_render.py::_quote`）

    e "路径 C:\new\test"        →      e "路径 C:
                                      ew\test"

    Beat.text 里存的已经是**真的**换行符（解析时把 `\\n` 还原了），
    `_quote` 只转义了 `\\` 和 `"`，没管控制字符 → 一条 say 被断成两行，
    产物**不可解析**。Ren'Py 里 `\\n` 是写台词的常用手法（强制换行），
    所以这不是边缘情况。

**P0-2 没有台词的 label 整段消失**（`story/beat_build.py`）

    label a:                    →      label start:
        $ focusmode = False             pass
        show screen home                return
    label b:
        e "后一句"

    "孤儿指令段"（没有台词、不落拍的那些语句）在换 label 时没有落拍，
    被吸进**下一个 label** 的第一张拍里 → 本 label 名下一张拍都没有 →
    渲染按拍分组时它**根本不出现**，渲染层退回 `label start:`。
    真实项目 51 个 label 里丢 4 个（含带 `call screen map` 的 `location`），
    **所有 `jump X` 静默失效**；更糟的是凭空多出一个 `label start`，
    与游戏入口重名 → 重复 label、游戏起不来。

这些用例全部**变异验证过**：把修复注释掉，它们必须变红
（见本文件末尾 `TestMutationNotes` 的说明）。
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy


# ---------------------------------------------------------------------------
#  工具
# ---------------------------------------------------------------------------

def _beats(src: str):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'h.rpy'
        f.write_text(src, encoding='utf-8')
        pr, _g = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        return beats_from_parse_result(pr)


def _rpy(src: str) -> str:
    return render_beats_to_rpy(_beats(src))


#: 一行里未闭合的双引号 → 字符串跨了物理行 → 产物语法坏
def broken_string_lines(rpy_text: str) -> list:
    """
    一行里未闭合的双引号 → 字符串跨了物理行 → 产物语法坏。

    ⚠ **先剥掉行尾注释再数**（2026-10-05 修）：注释里的引号不算数，而
    Ren'Py 的注释就是从 `#` 到行尾。第一版没剥，于是真实项目里这一行

        hover_sound "a.ogg"#"bgm/…mp3" #"fall.ogg"

    被报成"字符串断了" —— **假阳性**（`#` 后面全是注释，产物完全正常）。
    剥注释复用词法器那套实现（`RenPyLexer._strip_line_comment`）：
    引号里的 `#` 不算注释起点，这活儿只有一份实现。
    """
    from storygraph.parsing.renpy_parser import RenPyLexer

    out = []
    for ln in rpy_text.splitlines():
        if '"""' in ln:
            # Python 三引号 docstring：**本来**就可以跨行，单行配平判不了。
            # 不跳开它，真实项目里 14 条 docstring 会被报成"语法坏"——
            # 假阳性比漏报更贵（它会让一条检查永远无法用来验收）。
            continue
        code = RenPyLexer._strip_line_comment(ln)
        s = re.sub(r'\\.', '', code)
        if s.count('"') % 2:
            out.append(ln)
    return out


# ---------------------------------------------------------------------------
#  P0-1：反斜杠 / 控制字符
# ---------------------------------------------------------------------------

class TestQuoteEscapesControlChars:
    """`\\n` / `\\t` / `\\r` 在 Beat 里是真字符，写出来必须转回转义写法。"""

    def test_backslash_path_stays_on_one_line(self):
        """`C:\\new\\test` 不能把一条 say 断成两行。"""
        out = _rpy('label a:\n    e "路径 C:\\new\\test"\n')
        assert broken_string_lines(out) == [], out
        assert '路径 C:\\new\\test' in out

    def test_newline_escape_is_not_a_real_newline(self):
        """Ren'Py 里最常见的强制换行写法，往返后必须还是 `\\n` 两个字符。"""
        out = _rpy('label a:\n    e "第一行\\n第二行"\n')
        assert broken_string_lines(out) == [], out
        # 产物里那一行包含字面 `\n`（反斜杠 + n），而不是物理换行
        line = next(ln for ln in out.splitlines() if '第一行' in ln)
        assert '\\n' in line
        assert '第二行' in line        # 还在同一行

    def test_tab_escape_is_not_a_real_tab(self):
        out = _rpy('label a:\n    e "列1\\t列2"\n')
        line = next(ln for ln in out.splitlines() if '列1' in ln)
        assert '\\t' in line
        assert '\t' not in line

    def test_real_tab_inside_text_is_escaped(self):
        """原文里直接写了个真 TAB，产物里必须是 `\\t`。"""
        out = _rpy('label a:\n    e "中间\t有制表符"\n')
        assert broken_string_lines(out) == [], out
        line = next(ln for ln in out.splitlines() if '中间' in ln)
        assert '\\t' in line

    def test_menu_option_text_is_quoted_too(self):
        """选项文字同样要走 `_quote`（以前是手包一对引号）。"""
        out = _rpy('label a:\n    menu:\n        "他说：\\"退出\\"":\n'
                   '            return\n')
        assert broken_string_lines(out) == [], out


# ---------------------------------------------------------------------------
#  P0-2：label 是硬边界
# ---------------------------------------------------------------------------

class TestLabelBoundary:
    """换 label 时攒着的孤儿指令段必须先落拍，label 名不许消失。"""

    def test_orphan_segment_keeps_own_label(self):
        src = ('label a:\n'
               '    $ focusmode = False\n'
               '    show screen home\n'
               'label b:\n'
               '    e "后一句"\n')
        beats = _beats(src)
        assert {b.label for b in beats} == {'a', 'b'}
        out = render_beats_to_rpy(beats)
        assert 'label a:' in out
        assert 'label b:' in out

    def test_orphan_segment_at_end_keeps_label(self):
        """孤儿段在**文件末尾**时最容易漏：后面没有拍可以吸附。"""
        src = ('label a:\n'
               '    e "前一句"\n'
               'label b:\n'
               '    play music "m.ogg" fadein 1.0\n'
               '    show screen home\n'
               '    $ renpy.pause(hard=True)\n')
        beats = _beats(src)
        assert {b.label for b in beats} == {'a', 'b'}
        assert 'label b:' in render_beats_to_rpy(beats)

    def test_return_only_label_keeps_name(self):
        out = _rpy('label a:\n    return\n')
        assert 'label a:' in out
        assert 'label start:' not in out

    def test_empty_label_keeps_name(self):
        """空 label：产物里不许冒出 `label start:`（会和游戏入口重名）。"""
        out = _rpy('label a:\n')
        assert 'label a:' in out
        assert 'label start:' not in out

    def test_empty_label_survives_excel_roundtrip(self):
        """空拍必须能**走过 Excel** 回来。

        这一条专钉 `Beat.is_empty` 的 label 锚点判据：只有走 xlsx 往返，
        `_read_beats_from_ws` 的"空行跳过"才会被触发（不走往返时那条
        分支根本不执行，测试会假绿 —— 变异验证就是这么发现的）。
        """
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel
        src = 'label a:\n    return\n'
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            f = tmp / 'h.rpy'
            f.write_text(src, encoding='utf-8')
            pr, _g = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            xl = tmp / 'h.xlsx'
            export_beats_to_excel(beats, xl)
            back = import_beats_from_excel(xl)
            out = render_beats_to_rpy(back)
        assert back, '空拍在 xlsx 往返里被当空行丢掉了'
        assert 'label a:' in out
        assert 'label start:' not in out

    def test_orphan_content_not_absorbed_by_next_label(self):
        """孤儿语句的内容不许跑到下一个 label 的拍里。"""
        src = ('label a:\n'
               '    $ focusmode = False\n'
               'label b:\n'
               '    e "后一句"\n')
        beats = _beats(src)
        a = [b for b in beats if b.label == 'a']
        b = [b for b in beats if b.label == 'b']
        assert a, f'label a 名下没有拍：{[x.label for x in beats]}'
        assert 'focusmode' in a[0].code
        assert 'focusmode' not in (b[0].code if b else '')


# ---------------------------------------------------------------------------
#  真实项目（换机即 skip）
# ---------------------------------------------------------------------------

REAL_PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')


@pytest.mark.skipif(not REAL_PROJECT.exists(),
                    reason='真实项目不在本机')
class TestRealProjectLabels:
    """真实项目是最终判据：51 个 label，一个都不许少。"""

    def test_no_label_is_lost(self):
        files = sorted((REAL_PROJECT / 'game').rglob('*.rpy'))
        pr, _g = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
        beats = beats_from_parse_result(pr)
        out = render_beats_to_rpy(beats)
        src_labels = set(pr.labels)
        out_labels = set(re.findall(r'^label\s+([^\s:#]+)', out, re.M))
        assert src_labels - out_labels == set(), \
            f'产物里少了这些 label：{sorted(src_labels - out_labels)}'
        assert out_labels - src_labels == set(), \
            f'产物里凭空多出：{sorted(out_labels - src_labels)}'

    def test_no_label_is_invented_per_file(self):
        """**逐文件**渲染：源文件里没有的 label，产物里不许出现。

        上一档是全项目合并渲染（有拍就不走兜底分支），看不出这个问题。
        真实项目里 6 个**纯定义文件**（`transform.rpy` /
        `whackamole_images.rpy` / `generated_ui.rpy` / `plottree.rpy` /
        `script0.rpy` / `script1.rpy`）解析出 0 条语句、0 个文件级块，
        却各得到一个 `label start:` —— 而 `start` 是 Ren'Py 的游戏入口。
        """
        files = sorted((REAL_PROJECT / 'game').rglob('*.rpy'))
        invented = []
        for f in files:
            pr, _g = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            out = render_beats_to_rpy(beats_from_parse_result(pr))
            got = set(re.findall(r'^label\s+([^\s:#]+)', out, re.M))
            for name in got - set(pr.labels):
                invented.append(f'{f.name}::{name}')
        assert not invented, f'凭空多出的 label：{sorted(invented)}'

    def test_no_broken_strings_in_product(self):
        files = sorted((REAL_PROJECT / 'game').rglob('*.rpy'))
        pr, _g = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
        out = render_beats_to_rpy(beats_from_parse_result(pr))
        assert broken_string_lines(out) == []


class TestMutationNotes:
    """
    变异验证记录（2026-10-05 实测）：

      · 把 `_quote` 里的控制字符转义去掉 →
        `TestQuoteEscapesControlChars` **4 条红**（menu option 那条不红：
        它的字面量里没有控制字符，只验证了引号转义）；
      · 把 `beat_build.add()` 里 label 切换时的 `flush_stage_only()` 去掉 →
        `test_orphan_content_not_absorbed_by_next_label` +
        `TestRealProjectLabels::test_no_label_is_lost` 变红
        （后者会报出那 4 个真实 label 名）；
      · 把 `Beat.is_empty` 里的 label 锚点判据去掉 →
        `test_empty_label_survives_excel_roundtrip` 变红。

    ⚠ 第三条是**变异验证逼出来**的一课：最初写的
    `test_return_only_label_keeps_name` / `test_empty_label_keeps_name`
    在变异下**全绿** —— 它们直接调渲染器，**不走 Excel**，
    而 `is_empty` 只在 `_read_beats_from_ws` 里生效，那条分支根本没被执行。
    "测试绿了"不等于"缺陷被钉住"，必须走**缺陷真正发生的那条路径**。

    没有这一步，"测试全绿"说明不了任何事 —— 同一天我们已经见过两次
    "1731 测试全绿却漏掉 100% 丢失"和"断言方向写反"的教训。
    """
