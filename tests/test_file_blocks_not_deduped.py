"""
文件级保真块**不许按内容去重**（2026-10-18，`cli.py build` 真机 P0）
=====================================================================

现场：真机项目 `screens.rpy` 的顶层区间里，内容相同、位置不同的块有
8 条单独的 `}`、33 条 `##`、12 条 `####…` 横线。

旧 `_collect_file_blocks` 拿 `part.strip()` 当去重键 → 8 个 `}` 只剩 1 个：

    define bubble.expand_area = {
        "thought" : (0, 0, 0, 0),
                                      ← 本该在这里的 `}` 没了
    ## 移动设备界面

字典没闭合，Ren'Py 加载直接 `SyntaxError`。更隐蔽的是那 33 条 `##` 与
12 条横线 —— 它们不影响语法，只是把文件结构悄悄抹平了。而保真体检
（`audit_byte_exact` / `audit_full_path`）走的是**增量补丁**，碰不到这些行，
所以四轮"343/343 逐字节相同"全是绿灯。

这里钉住两件事：

  1. **内容相同 ≠ 重复**：同一条 `}` / `##` / 注释可以出现任意多次；
  2. 真正的重复（同一份块被挂了两次）仍然只写一次 —— 判据换成
     "整段连续重复"，不是"这一条以前出现过"。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer   # noqa: E402
from storygraph.story.beat_build import (attach_file_block_text,      # noqa: E402
                                         beats_from_parse_result)
from storygraph.story.beat_model import Beat, FILE_BLOCK_SEP           # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy           # noqa: E402

#: 三个"内容不同、但都以顶格 `}` 收尾"的字典字面量 + 重复的分隔注释。
#: 这就是 `screens.rpy` 的缩小版：三个 `}` 长得一模一样。
THREE_BRACES = (
    '## 气泡\n'
    'define bubble.frame = {\n'
    '    "bottom_left" : 0,\n'
    '    "top_left" : 22,\n'
    '}\n'
    '\n'
    '## 气泡\n'
    'define bubble.thoughtframe = {\n'
    '    "bottom_left" : 0,\n'
    '    "top_left" : 22,\n'
    '}\n'
    '\n'
    '## 气泡\n'
    'define bubble.expand_area = {\n'
    '    "thought" : (0, 0, 0, 0),\n'
    '}\n'
    '\n'
    'label start:\n'
    '    e "hi"\n'
)


def _collect(src: str, tmp_path: Path):
    """源码 → 拍（顶层块挂在第一张拍上）→ 收集到的文件级块。"""
    f = tmp_path / 'screens.rpy'
    f.write_text(src, encoding='utf-8')
    pr, _graph = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def _render(src: str, tmp_path: Path) -> str:
    beats = _collect(src, tmp_path)
    return render_beats_to_rpy(beats, label_name=None)


def _top_level(text: str, token: str) -> int:
    """顶格（列 0）出现 `token` 的行数 —— 只数真正参与结构的那几条。"""
    return sum(1 for ln in text.splitlines() if ln.rstrip() == token)


class TestIdenticalBlocksAreNotDuplicates:
    def test_three_closing_braces_all_survive(self, tmp_path):
        out = _render(THREE_BRACES, tmp_path)
        assert _top_level(out, '}') == 3, (
            '顶格 `}` 被当成重复定义合并了 —— 产物里的字典会少闭合\n' + out)
        assert out.count('define bubble.') == 3, out

    def test_repeated_section_comments_all_survive(self, tmp_path):
        out = _render(THREE_BRACES, tmp_path)
        assert out.count('## 气泡') == 3, (
            '内容相同的分隔注释被去重吃掉了（语法合法，但文件结构被抹平）\n'
            + out)

    def test_the_dict_that_follows_a_brace_is_still_closed(self, tmp_path):
        """P0 的原样复现：`define x = {` 与它的 `}` 之间不许插进别的东西。"""
        out = _render(THREE_BRACES, tmp_path)
        lines = out.splitlines()
        start = next(i for i, ln in enumerate(lines)
                     if ln.startswith('define bubble.expand_area'))
        # 往下第一条顶格行必须是 `}`（块体是缩进的，不算）
        nxt = next(ln for ln in lines[start + 1:] if ln and not ln[0].isspace())
        assert nxt == '}', f'`define bubble.expand_area = {{` 没有闭合：{out}'


class TestRealDuplicatesAreStillWrittenOnce:
    """去重的初衷（同一份块挂两次）不能丢 —— 判据换成"整段连续重复"。"""

    def test_adjacent_identical_blocks_are_still_kept(self, tmp_path):
        """相邻的相同块**不是**重复：作者就是那么写的，一条都不许少。"""
        src = '##\n##\n##\n\nlabel a:\n    e "hi"\n'
        out = _render(src, tmp_path)
        assert out.count('##') == 3, out

    def test_two_beats_carrying_the_same_blocks_emit_them_once(self, tmp_path):
        """手工复制了带隐藏块的那一行：两张拍各带一份，也只写一次。"""
        text = 'init python:\n    x = 1'
        beats = [Beat(seq=1, label='a', text='hi', file_blocks=text),
                 Beat(seq=2, label='a', text='hi again', file_blocks=text)]
        out = render_beats_to_rpy(beats, label_name=None)
        assert out.count('init python:') == 1, out

    def test_different_blocks_are_both_written(self, tmp_path):
        beats = [Beat(seq=1, label='a', text='hi',
                      file_blocks=f'init python:\n    x = 1'
                      f'\n\n{FILE_BLOCK_SEP}\n\n'
                      f'define y = 2\n\n{FILE_BLOCK_SEP}\n\ndefine y = 3')]
        out = render_beats_to_rpy(beats, label_name=None)
        assert 'define y = 2' in out and 'define y = 3' in out, out
