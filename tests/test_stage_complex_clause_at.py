"""
tests/test_stage_complex_clause_at.py
=====================================
`show … at … behind …` 这类**复杂子句**里的 `at`（2026-10-30，P1 用户报）：
舞台快照里立绘 / 图层的变换丢失。

为什么必须单独钉：`beat_stage_parse._split_show_items` 对含子句关键字的
show 片段走 clause 分支，从前只填 `clause` / `image`，`at` **恒为空串**。
而 `stage_snapshot._apply_show_verb` 只认 item 的 `at` —— 于是

    show cg cg02 at cg38402160 behind ye

的变换整条丢掉，CG 只能按原图贴左上（真机 `sr_general` 实测 23 帧受影响，
都是 `show cg … at … behind …` 这种形状）。

修法口径（**不另写切分**）：clause 分支直接用
`core.vocabulary.classify_show_words` 已经切好的 `clauses`：
  · `at` / `behind` 分类器给 **list**（多目标：`at a, b` / `behind x, y`）；
  · `with` / `as` / `onlayer` / `zorder` 给 str；
  · 这里统一折成 item 的字符串字段：多目标用 **`', '` 连接**（Ren'Py 的 `at`
    是逗号分隔的变换列表，空格连接会少掉语义，与 `beat_model._join_at_targets`
    同一条规矩），单目标就是它本身。
  · `clause` 仍保留原文 —— 「复杂子句归舞台（高级）列」的渲染/去重契约
    一个字都不改（见 `beat_model.show_item_part` / `show_cell_parts`）。

判据：
  1. `cg cg02 at cg38402160 behind ye`     → at == 'cg38402160'
  2. `ling happy at left behind ye`        → at == 'left'
  3. `at cg38402160, cg_fit`（多目标）      → at == 'cg38402160, cg_fit'
     （口径写死：逗号 + 空格连接）
  4. 无子句的 `eileen happy`               → at == ''（行为不变）
  5. `stage_snapshots` 的 `state['layer_at']['cg']` == 'cg38402160'
     —— 这就是用户报的那 23 帧的机制。
"""

from __future__ import annotations

from storygraph.story.beat_model import Beat
from storygraph.story.beat_stage_parse import _split_show_items
from storygraph.story.stage_snapshot import stage_snapshots


def _one(text: str) -> dict:
    items = _split_show_items(text)
    assert len(items) == 1, items
    return items[0]


class TestComplexClauseAt:
    def test_cg_with_at_and_behind_keeps_at(self):
        """用户报的那条形状：`show cg cg02 at cg38402160 behind ye`。"""
        item = _one('cg cg02 at cg38402160 behind ye')
        assert item['image'] == 'cg cg02'
        assert item['at'] == 'cg38402160'
        # 「复杂子句归 Stage 列」的既有契约：clause 仍是原文，一个字不改
        assert item['clause'] == 'cg cg02 at cg38402160 behind ye'

    def test_actor_with_at_and_behind_keeps_at(self):
        item = _one('ling happy at left behind ye')
        assert item['image'] == 'ling happy'
        assert item['at'] == 'left'
        assert item['clause'] == 'ling happy at left behind ye'

    def test_multi_target_at_keeps_every_target(self):
        """`at a, b`：分类器给 list → 用 `', '` 连接（与渲染器同一口径）。"""
        item = _one('cg cg01 at cg38402160, cg_fit behind ye')
        assert item['at'] == 'cg38402160, cg_fit', item['at']
        # "至少两个目标都在"这件事也按词拆开核一遍（防连接符漂移时假绿）
        targets = [t.strip() for t in item['at'].split(',') if t.strip()]
        assert targets == ['cg38402160', 'cg_fit'], item['at']

    def test_plain_show_has_no_at(self):
        """无子句的普通 show 行为不变（不许凭空造一个 at）。"""
        item = _one('eileen happy')
        assert item['clause'] == ''
        assert item['image'] == 'eileen happy'
        assert item['at'] == ''

    def test_with_and_as_are_filled_too(self):
        """副产物：`with` / `as` 用同一份 clauses 填上，不用再切一次。"""
        item = _one('ling happy at left as ling2 with dissolve behind ye')
        assert item['at'] == 'left'
        assert item['as_'] == 'ling2'
        assert item['with_'] == 'dissolve'
        assert item['behind'] == 'ye'
        assert item['clause'] == (
            'ling happy at left as ling2 with dissolve behind ye')


class TestStageSnapshotUsesClauseAt:
    def test_cg_layer_at_lands_in_snapshot_state(self):
        """用户报的 23 帧机制：快照必须记下 CG 图层的变换（且持续继承）。"""
        beats = [
            Beat(seq=1, label='t', scene='bg room'),
            Beat(seq=2, label='t',
                 show='cg cg01 at cg38402160 behind ye'),
            Beat(seq=3, label='t', text='接着演'),
        ]
        rows = stage_snapshots(beats)
        assert rows[1]['state']['layer_at'] == {'cg': 'cg38402160'}
        # 下一拍没再写 show → 变换是持续状态，仍然在
        assert rows[2]['state']['layer_at'] == {'cg': 'cg38402160'}
        assert rows[1]['layers'] == 'cg cg01'

    def test_actor_clause_at_lands_in_actors(self):
        """复杂子句里的立绘 at 也要进 actors（从前同样丢）。"""
        beats = [Beat(seq=1, label='t', show='ling happy at left behind ye')]
        row = stage_snapshots(beats)[0]
        assert row['state']['actors'] == [['ling happy', 'left']]
        assert row['actors'] == 'ling happy@left'
