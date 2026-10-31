"""
tests/test_merge_dialog.py
==========================
**合并回源的确认框**：一处一个勾（2026-10-31）。

为什么它必须是"勾选"，不能是一个「是 / 否」的 `QMessageBox`：
`docs/超清CG与BG_零侵入导入_2026-10-31.md` 把合并归在 **L4**（改用户的
`show` 行），报警下限是 **confirm、永远、逐处**。而那几处里完全可能有作者
**故意**写的一个推近 —— 合并只能按人勾的来。

钉五条：

  ① 默认**全勾**，`chosen()` = 全部拍号；
  ② 勾掉一处 → `chosen()` **只**少那一处（别的仍勾着）；
  ③ `全不选` → 空集，且按钮文字变成「全选」（能勾回来）；
  ④ **改不了**的那几处列在框里（不是只在 plan 里躺着）、带原因；
  ⑤ 框里必须写明 L4 的边界：**不删行、不加行**、只改 `@at`。
"""

from __future__ import annotations

import pytest

from storygraph.story.at_merge import MergePlan, MergeSkip, MergeStep

from storygraph.ui_qt import merge_dialog as MD                  # noqa: E402
from storygraph.ui_qt.merge_dialog import MergeDialog           # noqa: E402


def _plan(*rows, skipped=()):
    steps = tuple(MergeStep(index=i, image=image, before=before, after=after,
                            before_at=before_at, after_at=after_at,
                            clause_before=before, clause_after=after)
                  for i, image, before, after, before_at, after_at in rows)
    return MergePlan(kind='scene', tag='', label='背景', source=0, at='fit',
                     steps=steps,
                     skipped=tuple(MergeSkip(index=i, reason=r)
                                   for i, r in skipped))


PLAN = _plan(
    (4, 'bg a', 'bg a@half', 'bg a@fit', 'half', 'fit'),
    (6, 'bg a', 'bg a@half', 'bg a@fit', 'half', 'fit'),
)


def _texts(dialog) -> str:
    """框里所有可见文字拼起来（找一句话用）。"""
    from PySide6.QtWidgets import QCheckBox, QLabel

    parts = []
    for w in dialog.findChildren(QLabel):
        parts.append(w.text() or '')
    for w in dialog.findChildren(QCheckBox):
        parts.append(w.text() or '')
    return '\n'.join(parts)


class TestMergeDialog:
    def test_every_step_is_ticked_by_default(self, qapp):
        dialog = MergeDialog(PLAN)
        assert dialog.chosen() == {4, 6}
        assert len(dialog.boxes()) == 2

    def test_unticking_one_leaves_the_others(self, qapp):
        dialog = MergeDialog(PLAN)
        dialog.boxes()[0].setChecked(False)
        assert dialog.chosen() == {6}

    def test_unticking_everything_is_an_empty_set_not_a_cancel(self, qapp):
        dialog = MergeDialog(PLAN)
        for box in dialog.boxes():
            box.setChecked(False)
        assert dialog.chosen() == set()
        # 按钮文字跟着变：还能一键勾回来
        assert dialog._toggle.text() == '全选'

    def test_toggle_all_can_clear_and_restore(self, qapp):
        dialog = MergeDialog(PLAN)
        dialog._toggle_all()                     # 全勾着 → 全不选
        assert dialog.chosen() == set()
        dialog._toggle_all()                     # 全空 → 全勾
        assert dialog.chosen() == {4, 6}

    def test_the_box_names_the_count_the_value_and_the_source(self, qapp):
        text = _texts(MergeDialog(PLAN))
        assert '把本景 2 处单独改写的 at 改成' in text
        assert '「fit」' in text
        assert '第 1 拍' in text

    def test_each_step_is_listed_with_before_and_after(self, qapp):
        dialog = MergeDialog(PLAN)
        texts = [b.text() for b in dialog.boxes()]
        assert '第 5 拍' in texts[0] and 'half → fit' in texts[0]
        assert '第 7 拍' in texts[1]

    def test_unmergeable_sites_are_listed_with_reasons(self, qapp):
        plan = _plan((4, 'bg a', 'bg a@half', 'bg a@fit', 'half', 'fit'),
                     skipped=((9, '这条 show 是复杂子句，请手改'),))
        text = _texts(MergeDialog(plan))
        assert '改不了 1 处' in text
        assert '第 10 拍' in text
        assert '复杂子句' in text

    def test_the_footer_states_the_l4_boundary(self, qapp):
        text = _texts(MergeDialog(PLAN))
        assert '不删行' in text and '不加行' in text
        assert '导出时还会逐行给你看 diff' in text


class TestAskMerge:
    def test_a_cancel_returns_none(self, qapp, monkeypatch):
        monkeypatch.setattr(
            MD.MergeDialog, 'exec',
            lambda self: MD.QDialog.DialogCode.Rejected)
        assert MD.ask_merge(PLAN) is None

    def test_accept_returns_the_ticked_beats(self, qapp, monkeypatch):
        def _exec(self):
            self.boxes()[0].setChecked(False)
            return MD.QDialog.DialogCode.Accepted

        monkeypatch.setattr(MD.MergeDialog, 'exec', _exec)
        assert MD.ask_merge(PLAN) == {6}

    def test_a_broken_dialog_never_changes_anything(self, qapp, monkeypatch):
        def _boom(self):
            raise RuntimeError('打不开')

        monkeypatch.setattr(MD.MergeDialog, 'exec', _boom)
        assert MD.ask_merge(PLAN) is None
