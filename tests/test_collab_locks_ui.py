"""
协作锁接线（体验报告 P0-3，2026-10-23）
========================================

用户 2026-10-22 的原话：

> locks（collab_store）实测在**"只有测试在用"**清单里 → 能力悬空。

这一份测的就是"悬空"这件事被接上了没有 —— 而且是**三层一起接**：

  · `app.session_collab`：四条薄壳（锁 / 解锁 / 整章锁 / 列表）真的转发到
    `collab_store`（不是又写一遍规则）；
  · `ui_qt.dialog_locks`：列表 + 锁当前这一拍 + 续租 + 解锁 / 强制解锁；
  · `ui_qt.window_graph_ops.show_collab_locks`：知道**当前这一拍是谁**
    （`line_id` 取自会话的拍，不是文件里现算 —— 算错了就是锁错行）。

锁的语义（租约 / 过期可接手 / 只读不能锁 / 强制解锁只有所有者能做）
不在这里重测：那是 `tests/test_collab_lease_roles.py` 的活。这里只钉
"界面有没有把它用起来"。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from storygraph.collab import collab_store as cs
from storygraph.story.beat_model import Beat, assign_identity

pytest.importorskip('PySide6')


def _beats(n=3):
    beats = [Beat(seq=i + 1, label='ch01', speaker='e', text=f'第{i + 1}句')
             for i in range(n)]
    assign_identity(beats)
    return beats


@pytest.fixture
def seeded(tmp_path):
    """建库 + 推 3 拍进去（成员：alice 所有者 / bob 编辑 / eve 只读）。"""
    db = tmp_path / 'collab.db'
    cs.init_db(db, users=('alice', 'bob', 'eve'), owner='alice')
    cs.set_role(db, 'eve', 'viewer', actor='alice')
    seed = tmp_path / 'seed.xlsx'
    from storygraph.story.excel_export import export_beats_to_excel
    export_beats_to_excel(_beats(), seed)
    res = cs.push(db, seed, actor='alice', force=True)
    assert res.get('added'), res
    ids = [b.line_id for b in _beats()]
    return db, ids


@pytest.fixture
def session(tmp_path, seeded):
    from storygraph.app.editor import EditorSession

    project = tmp_path / 'proj'
    project.mkdir()
    return EditorSession(project_root=project)


# ===========================================================================
#  第一层：会话转发（四条薄壳）
# ===========================================================================

class TestSessionForwarding:
    def test_lock_then_list_then_unlock(self, session, seeded):
        db, ids = seeded
        res = session.collab_lock(db, ids[0], 'bob', note='测试')
        assert res['ok'], res
        got = session.collab_locks(db)
        assert got['ok']
        assert [r['line_id'] for r in got['locks']] == [ids[0]]
        assert got['locks'][0]['locked_by'] == 'bob'
        # 摘要是给人看的（只有 line_id 等于让她猜）
        assert got['locks'][0]['text'] == '第1句'
        assert got['locks'][0]['expired'] is False
        assert got['locks'][0]['expires_in'] > 0

        out = session.collab_unlock(db, ids[0], 'bob')
        assert out['ok']
        assert session.collab_locks(db)['locks'] == []

    def test_renew_extends_lease(self, session, seeded):
        db, ids = seeded
        assert session.collab_lock(db, ids[0], 'bob')['ok']
        first = cs.locks(db)[0]['lease_until']
        res = session.collab_renew_lock(db, ids[0], 'bob')
        assert res['ok'], res
        assert cs.locks(db)[0]['lease_until'] >= first

    def test_lock_range_locks_the_chapter(self, session, seeded):
        db, ids = seeded
        res = session.collab_lock_range(db, 'ch01', 'bob')
        assert res['ok'], res
        assert len(res['result']['locked']) == len(ids)
        assert len(session.collab_locks(db)['locks']) == len(ids)

    def test_lock_range_skips_other_peoples_locks(self, session, seeded):
        db, ids = seeded
        assert session.collab_lock(db, ids[0], 'alice')['ok']
        res = session.collab_lock_range(db, 'ch01', 'bob')
        assert ids[0] in res['result']['skipped']
        assert len(res['result']['locked']) == len(ids) - 1

    def test_viewer_cannot_lock(self, session, seeded):
        """只读成员上锁 → 拒绝（规则在 `collab_store`，界面只是把原因带出来）。"""
        db, ids = seeded
        res = session.collab_lock(db, ids[0], 'eve')
        assert not res['ok']
        assert '只读' in res['error']

    def test_missing_db_reports_error_not_crash(self, session, tmp_path):
        res = session.collab_locks(tmp_path / 'nope.db')
        assert not res['ok']
        assert res['locks'] == []


# ===========================================================================
#  第二层：对话框
# ===========================================================================

@pytest.fixture
def dialog(qapp, session, seeded):
    from storygraph.ui_qt.dialog_locks import CollabLockDialog

    db, ids = seeded
    dlg = CollabLockDialog(None, session=session, project_root=db.parent,
                           db_path=str(db), actor='bob',
                           label='ch01', line_id=ids[1])
    yield dlg, db, ids
    dlg.close()


class TestDialog:
    def test_lists_existing_locks(self, dialog, session):
        dlg, db, ids = dialog
        assert session.collab_lock(db, ids[0], 'alice')['ok']
        dlg.refresh()
        assert dlg.table.rowCount() == 1
        assert dlg.table.item(0, 1).text() == 'alice'
        assert '还有' in dlg.table.item(0, 2).text()

    def test_lock_current_beat(self, dialog, session):
        dlg, db, ids = dialog
        dlg._lock_current()
        rows = session.collab_locks(db)['locks']
        assert [r['line_id'] for r in rows] == [ids[1]]
        assert dlg.table.rowCount() == 1
        assert '已锁住' in dlg.out.toPlainText()

    def test_my_lock_is_marked(self, dialog, session):
        dlg, db, ids = dialog
        dlg._lock_current()
        assert dlg.table.item(0, 1).text() == '你'

    def test_unlock_selected(self, dialog, session):
        dlg, db, ids = dialog
        dlg._lock_current()
        dlg.table.setCurrentCell(0, 0)
        dlg._unlock_selected()
        assert session.collab_locks(db)['locks'] == []
        assert dlg.table.rowCount() == 0

    def test_unlock_selected_without_selection_says_so(self, dialog):
        dlg, db, ids = dialog
        dlg._unlock_selected()
        assert '先' in dlg.out.toPlainText()

    def test_renew_mine_without_locks_says_so(self, dialog):
        dlg, db, ids = dialog
        dlg._renew_mine()
        assert '没有锁着' in dlg.out.toPlainText()

    def test_lock_chapter(self, dialog, session):
        dlg, db, ids = dialog
        dlg._lock_chapter()
        assert len(session.collab_locks(db)['locks']) == len(ids)

    def test_missing_db_is_explained(self, qapp, session, tmp_path):
        from storygraph.ui_qt.dialog_locks import CollabLockDialog

        dlg = CollabLockDialog(None, session=session, project_root=tmp_path,
                               db_path=str(tmp_path / 'nope.db'), actor='bob')
        dlg._lock_current()
        assert '还没有协作库' in dlg.out.toPlainText()
        dlg.close()

    def test_needs_actor_name(self, qapp, session, seeded):
        from storygraph.ui_qt.dialog_locks import CollabLockDialog

        db, ids = seeded
        dlg = CollabLockDialog(None, session=session, project_root=db.parent,
                               db_path=str(db), actor='', line_id=ids[0])
        # 偏好里可能记着上次的用户名（`_build` 会带出来）—— 这里测的是
        # **空名字时说清楚**，所以先清空
        dlg.actor.setText('')
        dlg._lock_current()
        assert '当前用户' in dlg.out.toPlainText()
        assert cs.locks(db) == []
        dlg.close()


class TestHelpers:
    def test_lease_text(self):
        from storygraph.ui_qt.dialog_locks import lease_text

        assert '已过期' in lease_text({'expired': True})
        assert lease_text({'expires_in': 30}) == '还有 30 秒'
        assert lease_text({'expires_in': 600}) == '还有 10 分钟'

    def test_lock_row_text_prefers_summary(self):
        from storygraph.ui_qt.dialog_locks import lock_row_text

        assert lock_row_text({'text': '这句台词', 'line_id': 'ch01.0001'}) == \
            '这句台词'
        assert lock_row_text({'text': '', 'line_id': 'ch01.0001'}) == 'ch01.0001'


# ===========================================================================
#  第三层：窗口入口（知道"当前这一拍是谁"）
# ===========================================================================

class TestWindowEntry:
    def test_dialog_gets_current_beat_line_id(self, monkeypatch, ui_window):
        from storygraph.ui_qt.dialog_locks import CollabLockDialog

        seen = {}

        def fake_exec(self):
            seen['line_id'] = self._line_id
            seen['label'] = self._label
            return 0
        monkeypatch.setattr(CollabLockDialog, 'exec', fake_exec)

        window = ui_window
        idx = int(window._loaded_idx)
        expected = window._session.beats[idx].line_id
        label = window._session.beats[idx].label
        window.show_collab_locks()
        assert seen['line_id'] == expected
        assert seen['label'] == label

    def test_menu_has_both_entries(self, ui_window):
        """两个入口都真的挂在「更多工具」上（能力悬空的典型症状是没入口）。"""
        menu = ui_window._more_tools_menu()
        try:
            text = ' '.join(x.text() for x in menu.actions())
        finally:
            menu.deleteLater()
        assert '协作锁' in text
        assert '上次没导出的改动' in text
        assert '协作库' in text
