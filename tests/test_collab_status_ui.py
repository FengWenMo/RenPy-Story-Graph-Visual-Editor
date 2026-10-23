"""
协作**状态**接线（体验报告 P1-5，2026-10-23）
==============================================

第十阶段的收尾里写着：

> P1-5 审阅 / 状态流：`collab_store.set_status` 有（draft/待审/定稿），
> 界面仍然没有入口。

这一份测的就是"入口"这件事，以及它刻意选的最小形状：**按章**，
不是按句。逐句标状态没人会做 —— 策划不会为 284 句一句句点"定稿"。

四层一起验：

  · `collab_store.set_status_range`：只动这一章、已同状态不算 changed、
    权限与 `set_status` **同一条**、写一条审计；
  · `app.session_collab`：两个薄壳（总览 / 按章改），**读总览不建库**；
  · `ui_qt.dialog_locks`：状态表 + 三个按钮，没选章时作用于当前这一章；
  · 窗口入口：「🔒 协作锁 / 状态」仍然挂在「更多工具」上。

锁本身的语义（租约 / 过期接手 / 强制解锁）不在这里重测 —— 那是
`test_collab_lease_roles.py` / `test_collab_locks_ui.py` 的活。
"""
from __future__ import annotations

import pytest

from storygraph.collab import collab_store as cs
from storygraph.story.beat_model import Beat, assign_identity

pytest.importorskip('PySide6')


def _beats():
    """2 章 5 拍（ch01 三拍 / ch02 两拍），状态默认 draft。"""
    rows = [('ch01', '第一句'), ('ch01', '第二句'), ('ch01', '第三句'),
            ('ch02', '别章一句'), ('ch02', '别章二句')]
    beats = [Beat(seq=i + 1, label=label, speaker='e', text=text)
             for i, (label, text) in enumerate(rows)]
    assign_identity(beats)
    return beats


@pytest.fixture
def seeded(tmp_path):
    db = tmp_path / 'collab.db'
    cs.init_db(db, users=('alice', 'bob', 'eve'), owner='alice')
    cs.set_role(db, 'eve', 'viewer', actor='alice')
    seed = tmp_path / 'seed.xlsx'
    from storygraph.story.excel_export import export_beats_to_excel
    export_beats_to_excel(_beats(), seed)
    res = cs.push(db, seed, actor='alice', force=True)
    assert res.get('added'), res
    return db


@pytest.fixture
def session(tmp_path):
    from storygraph.app.editor import EditorSession

    project = tmp_path / 'proj'
    project.mkdir()
    return EditorSession(project_root=project)


# ===========================================================================
#  第一层：存储（规则）
# ===========================================================================

class TestSetStatusRange:
    def test_marks_only_that_chapter(self, seeded):
        res = cs.set_status_range(seeded, 'ch01', cs.STATUS_REVIEW, 'bob')
        assert res['ok'], res
        assert res['changed'] == 3
        assert res['total'] == 3
        summary = cs.status_summary(seeded)
        assert summary['by_status'][cs.STATUS_REVIEW] == 3
        assert summary['by_status'][cs.STATUS_DRAFT] == 2
        by_label = {r['label']: r for r in summary['labels']}
        assert by_label['ch02']['in_review'] == 0

    def test_same_status_is_not_counted_again(self, seeded):
        first = cs.set_status_range(seeded, 'ch01', cs.STATUS_REVIEW, 'bob')
        assert first['changed'] == 3
        again = cs.set_status_range(seeded, 'ch01', cs.STATUS_REVIEW, 'bob')
        assert again['ok'] and again['changed'] == 0, again

    def test_bad_status_is_rejected(self, seeded):
        res = cs.set_status_range(seeded, 'ch01', '随便写的', 'bob')
        assert not res['ok']
        assert '状态只能是' in res['reason']

    def test_missing_chapter_is_rejected(self, seeded):
        res = cs.set_status_range(seeded, '没有这一章', cs.STATUS_REVIEW, 'bob')
        assert not res['ok']
        assert '没有这一章' in res['reason']

    def test_viewer_cannot_change_status(self, seeded):
        res = cs.set_status_range(seeded, 'ch01', cs.STATUS_APPROVED, 'eve')
        assert not res['ok']
        assert '只读成员' in res['reason']
        assert cs.status_summary(seeded)['by_status'][cs.STATUS_APPROVED] == 0

    def test_range_is_audited(self, seeded):
        cs.set_status_range(seeded, 'ch01', cs.STATUS_APPROVED, 'alice')
        recent = cs.status_summary(seeded)['recent']
        hit = [r for r in recent if r['action'] == 'status_range']
        assert hit, recent
        assert hit[0]['actor'] == 'alice'
        assert 'ch01' in str(hit[0]['line_id'])

    def test_status_labels_are_chinese(self):
        assert cs.STATUS_LABELS == {'draft': '草稿', 'in_review': '待审',
                                    'approved': '定稿'}


# ===========================================================================
#  第二层：会话薄壳
# ===========================================================================

class TestSessionWrappers:
    def test_overview_returns_counts(self, session, seeded):
        cs.set_status_range(seeded, 'ch01', cs.STATUS_REVIEW, 'alice')
        res = session.collab_status_overview(seeded)
        assert res['ok'], res
        summary = res['summary']
        assert summary['by_status'][cs.STATUS_REVIEW] == 3
        assert summary['lines'] == 5
        labels = {r['label'] for r in summary['labels']}
        assert labels == {'ch01', 'ch02'}

    def test_overview_does_not_create_a_database(self, session, tmp_path):
        """看一眼总览**不许**悄悄建库（与 `collab_locks` 同一条）。"""
        missing = tmp_path / '没有这个.db'
        res = session.collab_status_overview(missing)
        assert not res['ok']
        assert '还没有协作库' in res['error']
        assert not missing.exists(), '读总览把库建出来了'

    def test_set_status_range_forwards(self, session, seeded):
        res = session.collab_set_status_range(seeded, 'ch02',
                                              cs.STATUS_APPROVED, 'bob')
        assert res['ok'], res
        assert res['result']['changed'] == 2
        assert cs.status_summary(seeded)['by_status'][cs.STATUS_APPROVED] == 2


# ===========================================================================
#  第三层：对话框
# ===========================================================================

@pytest.fixture
def dialog(qapp, session, seeded):
    from storygraph.ui_qt.dialog_locks import CollabLockDialog

    dlg = CollabLockDialog(None, session=session, project_root=seeded.parent,
                           db_path=str(seeded), actor='bob', label='ch01')
    yield dlg, seeded
    dlg.close()


class TestStatusDialog:
    def test_lists_each_chapter_with_counts(self, dialog):
        dlg, db = dialog
        assert dlg.status_table.rowCount() == 2
        row = [dlg.status_table.item(0, c).text() for c in range(6)]
        assert row[0] == 'ch01'
        assert row[1] == '3'
        assert row[2] == '3'          # 草稿
        assert row[4] == '0'          # 定稿

    def test_summary_line_counts_all_rows(self, dialog):
        dlg, db = dialog
        text = dlg.status_summary.text()
        assert '共 5 行' in text
        assert '草稿 5' in text

    def test_button_acts_on_the_current_chapter_by_default(self, dialog):
        dlg, db = dialog
        dlg._set_status(cs.STATUS_REVIEW)
        assert cs.status_summary(db)['by_status'][cs.STATUS_REVIEW] == 3
        assert '改了 3 行' in dlg.out.toPlainText()

    def test_selected_chapter_wins_over_current(self, dialog):
        dlg, db = dialog
        dlg.status_table.setCurrentCell(1, 0)      # ch02
        dlg._set_status(cs.STATUS_APPROVED)
        summary = cs.status_summary(db)
        assert summary['by_status'][cs.STATUS_APPROVED] == 2
        assert summary['by_status'][cs.STATUS_DRAFT] == 3

    def test_same_status_says_nothing_to_do(self, dialog):
        dlg, db = dialog
        dlg._set_status(cs.STATUS_DRAFT)
        assert '本来就已经是草稿' in dlg.out.toPlainText()

    def test_viewer_is_refused_and_says_why(self, qapp, session, seeded):
        from storygraph.ui_qt.dialog_locks import CollabLockDialog

        dlg = CollabLockDialog(None, session=session, project_root=seeded.parent,
                               db_path=str(seeded), actor='eve', label='ch01')
        dlg._set_status(cs.STATUS_APPROVED)
        text = dlg.out.toPlainText()
        assert '没改成' in text and '只读' in text
        dlg.close()

    def test_missing_db_explains_instead_of_showing_an_empty_table(
            self, qapp, session, tmp_path):
        from storygraph.ui_qt.dialog_locks import CollabLockDialog

        dlg = CollabLockDialog(None, session=session, project_root=tmp_path,
                               db_path=str(tmp_path / 'nope.db'), actor='bob')
        assert dlg.status_table.rowCount() == 0
        assert '还没有协作库' in dlg.status_summary.text()
        dlg.close()

    def test_refresh_picks_up_a_status_set_elsewhere(self, dialog):
        dlg, db = dialog
        cs.set_status_range(db, 'ch02', cs.STATUS_APPROVED, 'alice')
        dlg.refresh()
        assert dlg.status_table.item(1, 4).text() == '2'


# ===========================================================================
#  第四层：窗口入口
# ===========================================================================

class TestWindowEntry:
    def test_menu_still_has_the_entry(self, ui_window):
        menu = ui_window._more_tools_menu()
        try:
            text = ' '.join(x.text() for x in menu.actions())
        finally:
            menu.deleteLater()
        assert '协作锁' in text
        assert '状态' in text, text

    def test_dialog_opens_on_the_current_chapter(self, monkeypatch, ui_window):
        from storygraph.ui_qt.dialog_locks import CollabLockDialog

        seen = {}

        def fake_exec(self):
            seen['label'] = self._label
            return 0

        monkeypatch.setattr(CollabLockDialog, 'exec', fake_exec)
        window = ui_window
        assert window._session.beats[window._loaded_idx].label == 'ch01'
        window.show_collab_locks()
        assert seen['label'] == 'ch01'
