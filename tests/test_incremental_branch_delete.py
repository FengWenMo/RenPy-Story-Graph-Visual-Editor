"""
tests/test_incremental_branch_delete.py
======================================
删掉"分支唯一语句"的那一拍：**连它所在的分支一起删**，而不是整章退回全量。

为什么单开一个文件（2026-10-21，真机 `pomodorro.rpy` 45 拍逐拍扫描）：

    删除走增量      33/45
    退回全量        12/45   ← 全是同一种形状："这一拍是 if/elif 分支或菜单
                              选项体的**唯一**语句"

patcher 从前判"删完上一级块会空"就放弃（空块语法错，判得对），代价是整章
重生成、手写 `default` / `transform` 全靠门禁拦着。现在的判据是"按分支处理"：

  · `elif` / `else` 分支 → 删分支头 + 体；
  · `if` 分支 → 同样删，并把**后面每一条** `elif` / `else` 换成渲染器给
    那一拍写的条件行（拍上的条件是展平后的累积形式）；
  · 菜单选项体（选项头自己是一拍）→ 体换成 `pass`（与全量重生成一致）。

每一条都断言**产物能重新解析**：删错一个词，`elif` 就成了孤儿。
"""

from pathlib import Path

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.patcher import patch_export

CHAIN = '''label ch01:
    $ t = 1
    if t == 1:
        "第一支"
    elif t == 2:
        "第二支"
    elif t == 3:
        "第三支"
    return
'''

MENU = '''label ch01:
    menu:
        "留下来":
            "那就留下。"
        "回家":
            jump ch02
    return
'''


def _beats(fp):
    return beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(fp)))


def _drop_and_patch(write_rpy, src, needle):
    fp = Path(write_rpy(src, 'proj/game/ch01.rpy'))
    beats = _beats(fp)
    idx = next(i for i, b in enumerate(beats) if b.text == needle)
    del beats[idx]
    res = patch_export(beats, fp, render_beats_to_rpy(beats))
    return res, fp


def _reparse(text, tmp_path):
    p = tmp_path / 'out.rpy'
    p.write_text(text, encoding='utf-8')
    return _beats(p)


class TestBranchDelete:
    def test_first_branch_of_a_chain_goes_away_with_its_head(self, write_rpy):
        """删掉 `if` 分支的唯一语句 → 分支头一起删，链不塌。"""
        res, _fp = _drop_and_patch(write_rpy, CHAIN, '第一支')
        assert res.ok, res.reasons
        assert '第一支' not in res.text
        assert 'if t == 1:' not in res.text
        assert 'elif' not in res.text, '还剩孤儿 elif'
        assert '第二支' in res.text and '第三支' in res.text

    def test_the_chain_still_parses_to_one_fewer_beat(self, write_rpy,
                                                     tmp_path):
        res, _fp = _drop_and_patch(write_rpy, CHAIN, '第一支')
        beats = _reparse(res.text, tmp_path)
        assert [b.text for b in beats if b.text] == ['第二支', '第三支']

    def test_middle_branch_goes_away_and_keeps_the_rest(self, write_rpy,
                                                        tmp_path):
        res, _fp = _drop_and_patch(write_rpy, CHAIN, '第二支')
        assert res.ok, res.reasons
        assert '第二支' not in res.text
        assert [b.text for b in _reparse(res.text, tmp_path) if b.text] == \
               ['第一支', '第三支']

    def test_a_menu_option_body_becomes_pass(self, write_rpy, tmp_path):
        """
        选项头自己是**一拍**，不能删 —— 体换成 `pass`。

        与全量重生成的产物一致（实测 `pomodorro.rpy` 的 `"该休息了!":` 就是
        这么写的）。选项还在，只是不再做任何事。
        """
        res, _fp = _drop_and_patch(write_rpy, MENU, '那就留下。')
        assert res.ok, res.reasons
        assert '那就留下。' not in res.text
        assert 'pass' in res.text, '空选项体必须补 pass'
        assert '"留下来":' in res.text, '选项头被误删了'
        beats = _reparse(res.text, tmp_path)
        assert any(str(b.kind) == 'menu_option' for b in beats)

    def test_changed_lines_counts_the_head_rewrites(self, write_rpy):
        """回执里的"改动 N 行"必须把被改写的 `elif` 头算进去。"""
        res, _fp = _drop_and_patch(write_rpy, CHAIN, '第一支')
        assert res.ok
        # 删 2 行（分支头 + 体）+ 改写 1 行（第二条 elif）
        assert res.changed_lines >= 3, res.changed_lines
