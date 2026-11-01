"""
切行只认 `\\n`：不可见分隔符不许被改成真换行（2026-11-01，外部审查 5.2 / P1-5）
=========================================================================

`str.splitlines()` 认的"行边界"远不止 `\\n`：`U+2028`（软回车）、`\\x0c`（换页）、
`\\x85`（NEL）、`\\x1c`~`\\x1e` 在它眼里全是换行，而 Ren'Py 的 lexer 只认 `\\n`。
补丁模块如果拿它切行、再用 `'\\n'.join()` 拼回去，这些字符就被**换成真换行**：

    原始：# 备注：软回车\\u2028在这里          ← 一行
    产物：# 备注：软回车                       ← 两行
          # 在这里

实测（改之前）：这种文件不只被改写，**连增量都做不成** —— 行号整体错位，
`_verify_patch` 报"1 句台词在补丁结果里找不到"，整章退回全量。

这个文件守三件事：

  1. 切行 → 拼回必须**逐字节等于原文**（含这些字符、含末尾换行）；
  2. 真有改动时，改的那一行落地，**别的字节一个不动**（那个 U+2028 还在原地）；
  3. 万一有人换回会吞分隔符的实现，`patch_export` **当场拒绝增量**，
     而不是安静地改坏文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.patcher import _join_like, patch_export  # noqa: E402

#: 三个"看起来是换行、其实不是"的字符：软回车 / 换页 / NEL
SPECIALS = {
    'u2028': '\u2028',
    'formfeed': '\x0c',
    'nel': '\x85',
}


def _script(mark: str) -> str:
    return ('label ch:\n'
            '    "第一句。"\n'
            f'    # 备注：这里有个看不见的分隔符{mark}就在这一行\n'
            '    "第二句。"\n'
            '    return\n')


def _beats_of(path: Path):
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


@pytest.fixture(params=sorted(SPECIALS))
def special_script(request, tmp_path):
    """`(rpy 路径, 原文)`：台词里混着一个特殊分隔符。"""
    mark = SPECIALS[request.param]
    text = _script(mark)
    path = tmp_path / f'{request.param}.rpy'
    path.write_text(text, encoding='utf-8', newline='')
    return path, text


class TestSplitLinesRoundTrip:

    @pytest.mark.parametrize('name', sorted(SPECIALS))
    def test_join_like_reproduces_the_file_byte_for_byte(self, name):
        text = _script(SPECIALS[name])
        from storygraph.core.textlines import split_lines
        assert _join_like(text, split_lines(text)) == text

    def test_missing_trailing_newline_is_not_added(self):
        from storygraph.core.textlines import split_lines
        text = 'label a:\n    return'          # 末尾没有换行
        assert _join_like(text, split_lines(text)) == text

    def test_trailing_newline_is_not_dropped(self):
        from storygraph.core.textlines import split_lines
        text = 'label a:\n    return\n'
        assert _join_like(text, split_lines(text)) == text


class TestPatchKeepsTheSpecialCharacters:

    def test_no_change_roundtrip_is_byte_identical(self, special_script):
        """
        "一个字没改 → 产物逐字节相同"。改之前这里 `ok=False`（被拒），
        而 `text` 还是被改坏的 —— 两条都错，只是错法不同。
        """
        path, text = special_script
        res = patch_export(_beats_of(path), path, generated_text='')
        assert res.ok, res.reasons
        assert res.text == text, '特殊分隔符被换成了真换行'

    def test_real_change_lands_and_the_special_character_survives(
            self, special_script):
        """改一句台词：只有那一行变，注释里的分隔符必须原样留在原行。"""
        path, text = special_script
        beats = _beats_of(path)
        changed = False
        for beat in beats:
            if str(getattr(beat, 'text', '')) == '第二句。':
                beat.text = '第二句（改过）。'
                changed = True
        assert changed, '测试脚本没解析出要改的那句台词'

        res = patch_export(beats, path, generated_text='')
        assert res.ok, res.reasons
        assert '第二句（改过）。' in res.text
        # 特殊分隔符还在（没被换成 '\n'）
        assert any(s in res.text for s in SPECIALS.values())
        # 行数没变：那一行没有变成两行
        assert len(res.text.split('\n')) == len(text.split('\n'))


class TestLossySplitIsRefused:
    """
    回归闸：谁把切行换回会吞分隔符的实现，`patch_export` 必须**拒绝**，
    而不是安静地写一份被改过的文件。
    """

    def test_patch_is_refused_when_split_eats_separators(self, tmp_path,
                                                         monkeypatch):
        path = tmp_path / 'ch.rpy'
        text = _script('\u2028')
        path.write_text(text, encoding='utf-8', newline='')

        import storygraph.story.patcher as patcher

        # 模拟"换回 str.splitlines()"：U+2028 被当成换行切开
        monkeypatch.setattr(patcher, 'split_lines',
                            lambda s: ('' if s is None else str(s)).splitlines())
        res = patcher.patch_export(_beats_of(path), path, generated_text='')

        assert not res.ok
        assert any('分隔符' in r for r in res.reasons), res.reasons
        assert res.text == '', '被拒绝时不该产出一份"看着能用"的文本'
