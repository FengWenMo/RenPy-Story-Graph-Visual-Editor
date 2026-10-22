"""
tests/test_asset_rename_ui.py
=============================
E2（用户 2026-10-19）：rename patch **接进界面** —— 预览 / 应用 / 回滚。

用户原话：

> 真正缺的是"资源表改名之后，Beats 里的旧值怎么办"。你已经有
> `asset_extensions._apply_rename_patch`（plan → apply(confirm=) → rollback），
> 但它按 D3' 没接 Qt。E2：把 rename patch 接进 UI（2 天）。
> 这才是"表格互联"真正缺的一块：不是让 Beats 变公式，而是让改名能一键
> 传播到所有引用处，并且能回滚。

判据：

① **词边界**：`ling_happy` 改成 `ling_smile` 时，`ling_happy2` / `ling_happy_x`
   一个字都不许动（部分匹配会改坏别人的资源名）；
② **预览不写盘**：算完磁盘上一个字节都没变；
③ **应用留备份**、**回滚逐位还原**（含 CRLF —— `.rpy` 是全项目最贵的文件）；
④ 对话框把这三步串起来：预览之后才能点应用，应用之后才能点回滚。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402


class _Stub:
    """只有 `project_root` 的宿主（会话方法是纯的，能独立测）。"""

    def __init__(self, root):
        self.project_root = Path(root) if root is not None else None


@pytest.fixture
def proj(tmp_path):
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'script.rpy').write_text(
        'label start:\n'
        '    show ling_happy at left\n'
        '    show ling_happy2 at right\n'
        '    show ling_happy_x\n',
        encoding='utf-8')
    return tmp_path


class TestPlan:
    def test_it_finds_only_word_boundary_matches(self, proj):
        from storygraph.app.session_assets import AssetRegistryMixin

        plan = AssetRegistryMixin.asset_rename_plan(_Stub(proj),
                                                    'ling_happy', 'ling_smile')
        assert plan['ok'], plan
        assert plan['total_lines'] == 1, plan['files']
        text = plan['files'][0]['new_text']
        assert 'show ling_smile at left' in text
        assert 'ling_happy2' in text, '部分匹配改坏了别人的资源名'
        assert 'ling_happy_x' in text

    def test_the_plan_does_not_touch_the_disk(self, proj):
        from storygraph.app.session_assets import AssetRegistryMixin

        before = (proj / 'game' / 'script.rpy').read_bytes()
        AssetRegistryMixin.asset_rename_plan(_Stub(proj), 'ling_happy',
                                             'ling_smile')
        assert (proj / 'game' / 'script.rpy').read_bytes() == before

    def test_the_same_name_is_refused(self, proj):
        from storygraph.app.session_assets import AssetRegistryMixin

        res = AssetRegistryMixin.asset_rename_plan(_Stub(proj), 'a', 'a')
        assert not res['ok'] and '一样' in res['error']

    def test_no_project_is_refused(self):
        from storygraph.app.session_assets import AssetRegistryMixin

        res = AssetRegistryMixin.asset_rename_plan(_Stub(None), 'a', 'b')
        assert not res['ok'] and '项目' in res['error']


class TestApplyAndRollback:
    def test_apply_writes_and_keeps_a_backup(self, proj):
        from storygraph.app.session_assets import AssetRegistryMixin

        host = _Stub(proj)
        original = (proj / 'game' / 'script.rpy').read_bytes()
        plan = AssetRegistryMixin.asset_rename_plan(host, 'ling_happy',
                                                    'ling_smile')
        res = AssetRegistryMixin.asset_rename_apply(host, plan)
        assert res['ok'] and res['modified'], res
        assert 'ling_smile' in (proj / 'game' / 'script.rpy').read_text(
            encoding='utf-8')
        bak = Path(res['backup_files'][0])
        assert bak.read_bytes() == original, '备份必须是写前那一份'

    def test_rollback_restores_the_exact_bytes(self, proj):
        from storygraph.app.session_assets import AssetRegistryMixin

        host = _Stub(proj)
        path = proj / 'game' / 'script.rpy'
        original = path.read_bytes()
        plan = AssetRegistryMixin.asset_rename_plan(host, 'ling_happy',
                                                    'ling_smile')
        applied = AssetRegistryMixin.asset_rename_apply(host, plan)
        AssetRegistryMixin.asset_rename_rollback(host, applied)
        assert path.read_bytes() == original, '回滚不是逐位还原'

    def test_crlf_survives_the_round_trip(self, tmp_path):
        from storygraph.app.session_assets import AssetRegistryMixin

        game = tmp_path / 'game'
        game.mkdir()
        path = game / 'crlf.rpy'
        original = b'label start:\r\n    show ling_happy\r\n'
        path.write_bytes(original)
        host = _Stub(tmp_path)
        plan = AssetRegistryMixin.asset_rename_plan(host, 'ling_happy',
                                                    'ling_smile')
        applied = AssetRegistryMixin.asset_rename_apply(host, plan)
        assert path.read_bytes() == b'label start:\r\n    show ling_smile\r\n', \
            '改名把 CRLF 翻了（整份文件都会显示成改动）'
        AssetRegistryMixin.asset_rename_rollback(host, applied)
        assert path.read_bytes() == original


class _StubSession:
    """假会话：记录三步各自被调了几次。"""

    def __init__(self, plan, script_path='', dirty=False):
        self.plan = plan
        self.calls = []
        self.applied = None
        self.script_path = script_path
        self.dirty = dirty

    def has_unsaved_edits(self):
        return bool(self.dirty)

    def asset_rename_plan(self, old, new):
        self.calls.append(('plan', old, new))
        if not old or not new:
            return {'ok': False, 'error': '旧名和新名都要填', 'files': [],
                    'matched_files': [], 'total_lines': 0}
        return dict(self.plan)

    def asset_rename_apply(self, plan):
        self.calls.append(('apply',))
        self.applied = {'ok': True, 'modified': ['a.rpy'],
                        'backup_files': ['a.rpy.bak']}
        return self.applied

    def asset_rename_rollback(self, applied):
        self.calls.append(('rollback',))
        return {'restored': ['a.rpy'], 'failed': []}


@pytest.fixture
def dlg(qapp, monkeypatch, proj):
    from storygraph.assets.asset_registry import AssetRegistry
    from storygraph.ui_qt import dialog_asset_rename as D

    reg = AssetRegistry()
    reg.images = {'ling_happy': {'ID': 'ling_happy'}}
    plan = {'ok': True, 'old_id': 'ling_happy', 'new_id': 'ling_smile',
            'total_lines': 1,
            'files': [{'path': str(proj / 'game' / 'script.rpy'),
                       'changed': [(2, '    show ling_happy at left',
                                    '    show ling_smile at left')]}],
            'matched_files': [str(proj / 'game' / 'script.rpy')]}
    session = _StubSession(plan)
    monkeypatch.setattr(D.QMessageBox, 'question',
                        lambda *a, **k: D.QMessageBox.Yes)
    d = D.AssetRenameDialog(None, session=session, registry=reg)
    d._stub_session = session
    yield d
    d.close()


@pytest.fixture
def rename_dialog_factory(qapp, monkeypatch, proj):
    """
    按需造对话框（`dlg` 那个夹具把 session 固定成"干净、没 script_path"了）。

    这一组测的是**顺序**（先导出再改名）与**写盘之后的重读**：
    两者都要能改 session 的状态，所以这里给一个能配的工厂。
    """
    from storygraph.assets.asset_registry import AssetRegistry
    from storygraph.ui_qt import dialog_asset_rename as D

    monkeypatch.setattr(D.QMessageBox, 'question',
                        lambda *a, **k: D.QMessageBox.Yes)
    # ⚠ **warning 也要打桩**：`apply()` 里"这一章有未导出改动"那条是
    # `QMessageBox.warning(...)`（模态）—— 不打桩就挂在这里等用户点确定。
    warnings = []
    monkeypatch.setattr(D.QMessageBox, 'warning',
                        lambda *a, **k: warnings.append(a[2] if len(a) > 2 else ''))
    made = []

    def _make(script_path='', dirty=False, modified=None, on_applied=None):
        """造一个对话框；`_make.warnings` 里是这一路弹过的 warning 正文。"""
        reg = AssetRegistry()
        reg.images = {'ling_happy': {'ID': 'ling_happy'}}
        plan = {'ok': True, 'old_id': 'ling_happy', 'new_id': 'ling_smile',
                'total_lines': 1,
                'files': [{'path': str(proj / 'game' / 'script.rpy'),
                           'changed': [(2, '    show ling_happy at left',
                                        '    show ling_smile at left')]}],
                'matched_files': [str(proj / 'game' / 'script.rpy')]}
        session = _StubSession(plan, script_path=script_path, dirty=dirty)
        if modified is not None:
            session.applied = {'ok': True, 'modified': modified,
                               'backup_files': []}
            session.asset_rename_apply = lambda p: session.applied
        d = D.AssetRenameDialog(None, session=session, registry=reg,
                                on_applied=on_applied)
        d._stub_session = session
        made.append(d)
        return d

    _make.warnings = warnings
    yield _make
    for d in made:
        d.close()


class TestTheDialog:
    def test_it_lists_the_ids_of_the_current_kind(self, dlg):
        texts = [dlg._old.itemText(i) for i in range(dlg._old.count())]
        assert 'ling_happy' in texts

    def test_apply_is_disabled_until_a_preview_happened(self, dlg):
        assert not dlg._apply_btn.isEnabled()
        dlg._new.setText('ling_smile')
        dlg.preview()
        assert dlg._apply_btn.isEnabled()

    def test_the_preview_shows_the_line_and_the_new_text(self, dlg):
        dlg._old.setCurrentText('ling_happy')
        dlg._new.setText('ling_smile')
        dlg.preview()
        out = dlg._out.toPlainText()
        assert '旧名 ling_happy → 新名 ling_smile' in out, out
        assert 'show ling_happy at left' in out
        assert 'show ling_smile at left' in out

    def test_apply_then_rollback(self, dlg):
        dlg._old.setCurrentText('ling_happy')
        dlg._new.setText('ling_smile')
        dlg.preview()
        dlg.apply()
        assert dlg._stub_session.calls[-1] == ('apply',)
        assert dlg._undo_btn.isEnabled()
        dlg.rollback()
        assert dlg._stub_session.calls[-1] == ('rollback',)

    def test_a_missing_name_blocks_the_apply_button(self, dlg):
        dlg._old.setCurrentText('ling_happy')
        dlg._new.setText('')
        dlg.preview()
        assert not dlg._apply_btn.isEnabled()


class TestOrderingWithUnsavedEdits:
    """
    「先导出再改名」这条顺序（2026-10-23）。

    改的是**文件**，会话内存里那份还是旧的 —— 反过来做，下次导出就把
    旧名字写回去了，而导出还会报"成功"。所以这一组钉的是：
    **这一章有没导出的改动时，改名必须被拦住**。
    """

    def _preview(self, d):
        d._old.setCurrentText('ling_happy')
        d._new.setText('ling_smile')
        d.preview()

    def test_apply_is_blocked_when_this_chapter_is_dirty(
            self, rename_dialog_factory, proj):
        d = rename_dialog_factory(script_path=str(proj / 'game' / 'script.rpy'),
                                  dirty=True)
        self._preview(d)
        d.apply()
        assert ('apply',) not in d._stub_session.calls, '有未导出改动时改名写盘了'
        # 光"没写盘"不够：必须**说清为什么**（不然就是点了没反应）
        said = ' '.join(rename_dialog_factory.warnings)
        assert '导出 .rpy' in said, said

    def test_apply_goes_through_when_clean(self, rename_dialog_factory, proj):
        d = rename_dialog_factory(script_path=str(proj / 'game' / 'script.rpy'),
                                  dirty=False)
        self._preview(d)
        d.apply()
        assert ('apply',) in d._stub_session.calls

    def test_dirty_but_other_chapter_is_not_blocked(self, rename_dialog_factory,
                                                    proj, tmp_path):
        """手上这一章**不在**这次改名范围里 → 不该拦（否则改名被无关的改动卡住）。"""
        other = tmp_path / 'other.rpy'
        other.write_text('label x:\n    return\n', encoding='utf-8')
        d = rename_dialog_factory(script_path=str(other), dirty=True)
        self._preview(d)
        d.apply()
        assert ('apply',) in d._stub_session.calls

    def test_on_applied_receives_the_result(self, rename_dialog_factory, proj):
        seen = []
        target = str(proj / 'game' / 'script.rpy')
        d = rename_dialog_factory(script_path=target, dirty=False,
                                  modified=[target],
                                  on_applied=lambda res: seen.append(res))
        self._preview(d)
        d.apply()
        assert seen and seen[0].get('modified') == [target]


class TestWindowReloadsAfterRename:
    """窗口那一侧：改名写盘之后，把**手上这一章**从磁盘重读（否则导出写回旧名）。"""

    def test_reloads_when_the_current_chapter_changed(self, ui_window, monkeypatch):
        window = ui_window
        chapter = str(window._file_path)
        calls = []
        monkeypatch.setattr(window, 'load_script',
                            lambda *a, **k: calls.append((a, k)))
        window._after_asset_rename({'modified': [chapter]})
        assert calls, '这一章被改名改到了，却没重读 —— 下次导出会把旧名字写回去'
        assert calls[0][0][0] == chapter
        assert calls[0][1].get('goto_card') is False

    def test_does_not_reload_for_other_files(self, ui_window, monkeypatch):
        window = ui_window
        calls = []
        monkeypatch.setattr(window, 'load_script',
                            lambda *a, **k: calls.append((a, k)))
        window._after_asset_rename({'modified': ['D:/elsewhere/x.rpy']})
        assert calls == []
