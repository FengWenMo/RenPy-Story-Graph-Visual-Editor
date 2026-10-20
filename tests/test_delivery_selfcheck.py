"""
交付自检（`StoryGraph.exe --selfcheck`）必须是真的绿的
======================================================

为什么单独给它一份测试：**它以前没人跑**。`--selfcheck` 是打包后的
exe 自己验主链路的命令（README 的"打包后排查"、`tools/attack_hell_project.py`
都在用它），但 pytest 里没有任何一条用例调用它 —— 于是它红着也没人知道。

事故本身：P1-1（零改动往返不许插生成标记，见
`test_incremental_p0_fixes.py`）改了合同，自检里那条"产物首行必须有
`# 由剧本表自动生成…`"却留在原地。结果 `_check_export()` 永久返回
"产物缺少生成标记（首行：default score = 0）"：自检样例什么都没改，
走的就是零改动那条路，产物本来就不该有标记。打包出来的人第一次跑
`--selfcheck` 就看到失败 —— 而且是"导出坏了"这种最吓人的措辞。

这里钉三件事：

  ① 自检的"导出主链路"现在是绿的（真调，不 mock）；
  ② 豁免**只**给逐字节副本：表里真改了东西 → 产物必须带生成标记
     （否则护栏认不出自家产物，"再导出一次"退化成线上事故）；
  ③ 零改动那一份确实是逐字节副本、且确实没有标记 —— 自检认的是这种形态。
"""

from __future__ import annotations

from pathlib import Path

from storygraph import selfcheck
from storygraph.app.editor import EditorSession
from storygraph.core.vocabulary import GENERATOR_MARKER


def _sample(tmp_path: Path) -> Path:
    """把自检用的那一份样例章节落到临时目录（与 `_check_export` 同一份）。"""
    src = tmp_path / 'game' / 'selfcheck.rpy'
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(selfcheck.SAMPLE_RPY, encoding='utf-8')
    return src


def test_the_export_chain_in_the_delivery_selfcheck_is_green():
    """
    真跑自检里"导出主链路"那一项，必须返回空串（= 通过）。

    它走的正是打包后 `--selfcheck` 的路径：五个引擎各导一遍、落盘导出、
    再导出一次、第三类内容（`default` / `init python`）还在。
    """
    assert selfcheck._check_export() == ''


def test_a_changed_chapter_still_carries_the_generator_marker(tmp_path):
    """表里改过 → 产物是新生成的，**必须**带标记（零改动豁免不适用）。"""
    src = _sample(tmp_path)
    s = EditorSession(project_root=tmp_path)
    assert s.load(src) > 0
    s.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})

    tgt = tmp_path / 'build' / 'renpy' / 'out.rpy'
    res = s.export(tgt)
    assert res.get('ok'), res
    assert not res.get('untouched'), '表里改过，不该走零改动那条路'

    text = tgt.read_text(encoding='utf-8')
    assert text.splitlines()[0] == GENERATOR_MARKER
    assert '改过的台词' in text


def test_an_unchanged_chapter_exports_a_byte_identical_copy(tmp_path):
    """零改动 → 逐字节副本、且没有标记（自检要认的第二种形态就在这里）。"""
    src = _sample(tmp_path)
    s = EditorSession(project_root=tmp_path)
    assert s.load(src) > 0

    tgt = tmp_path / 'build' / 'renpy' / 'out.rpy'
    res = s.export(tgt)
    assert res.get('ok'), res
    assert res.get('untouched'), res
    assert tgt.read_bytes() == src.read_bytes(), '零改动必须逐字节等于源文'
    assert GENERATOR_MARKER not in tgt.read_text(encoding='utf-8')
