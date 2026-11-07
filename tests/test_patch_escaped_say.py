"""
台词里的**转义引号 + 真制表符**：往返必须逐字节不动（2026-11-02）
=================================================================

真机现场（`sr_general/chapter/chapter3_4_4.rpy` 第 90 行，作者原话）：

    xqlz "那是上一句的接话！下一句下一句！你不会忘了吧\\" <TAB><TAB>#这里…"

`\\"` 是**转义引号**，所以 `#` 仍在字符串内部，整行是一条台词。旧代码在
`atl._strip_comment` 里不认转义：它把 `\\"` 当成字符串结尾，于是 `#` 之后
被当行尾注释截掉。截断位置在两种形态上**还不一样**：

  · 原文形态（真 tab）截完恰好以 `"` 结尾 → `_SAY_RE` 贪婪匹配勉强认出是
    台词 → 键 `('say','xqlz')`；
  · 渲染形态（`_quote` 把 tab 写成 `\\t`）截完剩 `…\\" \\t\\t`（没有结尾引号）
    → 认不出 → 键掉成 `('other', 整行)`。

于是补丁认为"生成器产出了一行新内容"，把它**插进去**；原文那一行又因为
"台词不许删"（`_may_drop_line`）留着 → **同一句台词出现两遍**，多出来的
那条还是"tab 被写成 `\\t`"的版本。实测 236 行 → 238 行，第二轮才稳定
（不是无限膨胀，但第一次导出就已经脏了）。

这个文件守四件事（对应修的两处）：

  1. `_strip_comment` 认 `\\` 转义（`\\"` 不是字符串结尾）；
  2. `_line_key` 对"真 tab"与"`\\t`"给出**同一个**身份键；
  3. `_replace_matched` 遇到"只差转义写法"时**保留原文**，不写新行；
  4. 走生产链路（`.rpy → 表 → 写回`）往返一次**逐字节相同**，且重复导出
     不再变化。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.story import patcher as P             # noqa: E402
from storygraph.story.atl import _strip_comment      # noqa: E402

#: 真机那一行的两种形态。`OLD` 是作者写的（真 tab），`GEN` 是 `_quote` 渲染的。
SPEAKER = 'xqlz'
TEXT = '那是上一句的接话！下一句下一句！你不会忘了吧"'
TAIL = '#这里出现名字但是不要出现立绘'
OLD = f'    {SPEAKER} "{TEXT}\\" \t\t{TAIL}"'
GEN = f'    {SPEAKER} "{TEXT}\\" \\t\\t{TAIL}"'


class TestStripCommentKnowsEscapes:
    def test_escaped_quote_does_not_end_the_string(self):
        """`\\"` 不是字符串结尾 → 后面的 `#` 仍在引号里，不算注释。"""
        assert _strip_comment(OLD.strip()) == OLD.strip()

    def test_plain_trailing_comment_still_stripped(self):
        """别修过头：真正的行尾注释照样要去掉（`_line_key` 靠它认台词）。"""
        assert _strip_comment('e "台词"  # 备注') == 'e "台词"'

    def test_hash_inside_normal_string_is_not_a_comment(self):
        assert _strip_comment('e "第#1句"') == 'e "第#1句"'

    def test_doubled_backslash_before_hash(self):
        """`\\\\#` 是两个字符（反斜杠 + `#`），`#` 仍在引号内。"""
        line = 'e "路径 C:\\\\#tmp"'
        assert _strip_comment(line) == line


class TestLineKeyIgnoresEscapeSpelling:
    def test_real_tabs_and_escaped_tabs_are_the_same_say(self):
        assert P._line_key(P._body(OLD)) == ('say', SPEAKER)
        assert P._line_key(P._body(GEN)) == ('say', SPEAKER)

    def test_escaped_same_is_true_only_for_equal_content(self):
        assert P._escaped_same(OLD, GEN) is True
        # 内容真变了就不能算"只差写法"
        assert P._escaped_same(OLD, GEN.replace('下一句', '上一句')) is False

    def test_escaped_same_does_not_claim_identical_lines(self):
        """完全相同的两行不算"只差转义"（那条由 `body == pick_body` 先接住）。"""
        assert P._escaped_same(OLD, OLD) is False


class TestPatchKeepsTheAuthorsBytes:
    def test_replace_matched_keeps_one_line(self):
        """核心判据：**一行进、一行出**（旧行为是两行）。"""
        out, changed, unlanded, grown, shrunk = P._replace_matched(
            [OLD], [GEN], allow_edit=True, relax_speaker=True)
        assert len(out) == 1, out
        assert out[0] == OLD          # 逐字节不动（真 tab 保留）
        assert (changed, grown, shrunk) == (0, 0, 0)
        assert unlanded == []


class TestRoundTripThroughTheTable:
    """走生产那条路：`.rpy → 表（xlsx） → 带 --source 写回`。"""

    SOURCE = (
        'label ch01:\n'
        '    jc "前一句"\n'
        f'{OLD}\n'
        '    jc "后一句"\n'
        '    return\n'
    )

    def _roundtrip(self, tmp_path, text, index=1):
        from storygraph.pipeline.compile import compile_table
        from storygraph.story.script_table import open_rpy_as_table
        src = tmp_path / 'src.rpy'
        src.write_text(text, encoding='utf-8')
        xlsx = tmp_path / f't{index}.xlsx'
        out = tmp_path / f'o{index}.rpy'
        open_rpy_as_table(str(src), str(xlsx))
        compile_table(str(xlsx), rpy_path=str(out), write_sheet=False,
                      force=True, source_path=str(src), skip_empty=True,
                      engine_lint=False)
        return src.read_text(encoding='utf-8'), out.read_text(encoding='utf-8')

    def test_first_export_is_bytewise_identical(self, tmp_path):
        _src, out = self._roundtrip(tmp_path, self.SOURCE)
        # 生成标记（若补丁路径不加）不影响这一句；只查关键行没被复制/改写
        assert out.count(TEXT) == 1, out
        assert '\\t\\t' not in out          # 没有被"渲染成转义"的版本混进来
        assert '\t\t' in out                # 原文那两个真制表符还在

    def test_second_export_does_not_grow(self, tmp_path):
        """旧行为：第一次 236 → 238 行。现在两次都一样。"""
        src, out1 = self._roundtrip(tmp_path, self.SOURCE, index=1)
        _s2, out2 = self._roundtrip(tmp_path, out1, index=2)
        assert out1 == out2
        assert out2.count(TEXT) == 1

    def test_normal_say_is_untouched(self, tmp_path):
        """对照组：不碰的台词一个字节都不许动。"""
        _s, out = self._roundtrip(tmp_path, self.SOURCE)
        assert '    jc "前一句"' in out
        assert '    jc "后一句"' in out
