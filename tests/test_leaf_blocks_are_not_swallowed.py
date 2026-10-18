"""
叶子语句**不许吞掉块体**（2026-10-18，build P0 的第二个现场）
============================================================

修掉"`}` 被当成重复定义"之后，同一个 `cli.py build` 立刻露出 35 处新的
语法错（以前被第一个错误挡住，引擎 lint 又 fail-open，所以从没被人看见）：

    show statement expects a non-empty block.

现场（真机 `screens.rpy` / `chapter3_1.rpy`）：

    show lan agreeable at right:      →   show lan agreeable at right:
        zoom 0.15                              ← 块体（zoom / yoffset）没了
        yoffset 200                        with dissolve

`show` / `scene` / `image` 的**头行**被 `_stmt_show` 等分支照常建模，
`line.subblock` 里那几行 ATL 却没有载体 —— 解析时被静默丢掉。产物里剩下
一个空头的 `show …:`，Ren'Py 加载直接报错。

规则：**解析出来的节点没有块体载体，就不许吃这条语句的块。**
整块交给 `_stmt_unmodeled` 逐字保真（`UnknownBlockNode` → `Beat.opaque`）。

⚠ 反过来也要守住：**没有块的 `show` 仍然是结构化拍的**（能改图片名、
能查断链）。降级只发生在"真的带块"时。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer   # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result        # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy           # noqa: E402


def _beats(tmp_path, src: str, name: str = 's'):
    f = tmp_path / f'{name}.rpy'
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return pr, beats_from_parse_result(pr)


def _render(tmp_path, src: str) -> str:
    _pr, beats = _beats(tmp_path, src)
    return render_beats_to_rpy(beats, label_name=None)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _empty_block_heads(text: str) -> list:
    """
    **开了块却没有任何缩进内容**的 `show` / `scene` / `image` 行。

    这就是引擎那句 `show statement expects a non-empty block.` 的判据：
    行以 `:` 收尾，下一条非空行的缩进**不比它深**。
    """
    lines = [ln for ln in str(text or '').splitlines()]
    out = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s.endswith(':') or s.startswith('#'):
            continue
        if not re.match(r'^(show|scene|image)\b', s):
            continue
        nxt = next((x for x in lines[i + 1:] if x.strip()), '')
        if not nxt or _indent(nxt) <= _indent(ln):
            out.append(s)
    return out


#: 真机里出现的三种形状（`show` 立绘 / `show expression` / `scene`）。
CASES = {
    'show_atl': (
        'label a:\n'
        '    scene bg room\n'
        '    show lan agreeable at right:\n'
        '        zoom 0.15\n'
        '        yoffset 200\n'
        '    with dissolve\n'
        '    e "hi"\n'
    ),
    'show_expression': (
        'label a:\n'
        '    show expression Text("SEIREN", color="#89c7fe") as title:\n'
        '        xalign 0.5\n'
        '        alpha 0.0\n'
        '        linear 2.0 alpha 1.0\n'
        '    e "hi"\n'
    ),
    'scene_atl': (
        'label a:\n'
        '    scene bg room:\n'
        '        zoom 1.2\n'
        '    e "hi"\n'
    ),
}


class TestBlockBodySurvives:
    def test_every_body_line_is_in_the_output(self, tmp_path):
        for name, src in CASES.items():
            out = _render(tmp_path, src)
            for line in src.splitlines():
                s = line.strip()
                if not s or s.startswith(('label', 'show', 'scene', 'e ')):
                    continue
                assert s in out, f'{name}：块体 `{s}` 丢了\n{out}'

    def test_output_has_no_empty_block_head(self, tmp_path):
        """产物里**一个**空头的 `show/scene/image …:` 都不许有。"""
        for name, src in CASES.items():
            out = _render(tmp_path, src)
            assert not _empty_block_heads(out), (
                f'{name}：产物里有空块（引擎会报 '
                f'`show statement expects a non-empty block.`）\n{out}')

    def test_statement_goes_to_the_fidelity_channel(self, tmp_path):
        """整块进 `Beat.opaque`（逐字保真），不是 structured 的 stage。"""
        _pr, beats = _beats(tmp_path, CASES['show_atl'])
        carrier = [b for b in beats if 'show lan agreeable at right:' in
                   (b.opaque or '')]
        assert carrier, [b.opaque for b in beats]
        assert 'zoom 0.15' in carrier[0].opaque
        assert 'show lan agreeable at right:' in carrier[0].opaque


class TestTheGuardDoesNotOverTrigger:
    def test_plain_show_stays_structured(self, tmp_path):
        """没有块的 `show` 不是"未建模"，它得继续能查断链 / 能改图。"""
        _pr, beats = _beats(tmp_path,
                            'label a:\n    show lan happy at left\n    e "hi"\n')
        assert not any(b.opaque for b in beats), \
            [b.opaque for b in beats if b.opaque]
        assert any('lan happy' in (b.show or '') for b in beats), \
            [(b.show, b.text) for b in beats]

    def test_indented_comment_only_is_not_a_block(self, tmp_path):
        """缩进歪掉的**注释**不该让一句普通台词降级成保真块。"""
        src = ('label a:\n'
               '    e "hi"\n'
               '        # 只是注释，多缩进两格\n'
               '    e "bye"\n')
        _pr, beats = _beats(tmp_path, src)
        assert [b.text for b in beats if b.text] == ['hi', 'bye'], \
            [(b.kind, b.text, b.opaque) for b in beats]

    def test_menu_and_if_still_keep_their_blocks(self, tmp_path):
        """`menu:` / `if:` 本来就是有载体的节点 —— 守卫不许把它们打下去。"""
        src = ('label a:\n'
               '    menu:\n'
               '        "去":\n'
               '            e "好"\n'
               '    if True:\n'
               '        e "再见"\n')
        _pr, beats = _beats(tmp_path, src)
        assert not any(b.opaque for b in beats), \
            [b.opaque for b in beats if b.opaque]
