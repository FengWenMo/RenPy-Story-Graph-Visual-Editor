"""
自动保存 / 崩溃恢复（体验报告 P0-2，2026-10-23）
=================================================

用户 2026-10-22 的原话：

> 拍的改动没有自动保存 —— 关窗会问，崩了就三拍没了。
> 50 人 × 每天几小时，这件事一定会发生。

三层各测各的（**判据不互相借**）：

  · `storygraph.core.autosave` —— 纯文件：写 / 读 / 列 / 清 / 拒（没有 Qt）；
  · `storygraph.app.session_recover` —— 拍 ↔ JSON、摊回会话；
  · `storygraph.ui_qt.window_recover` —— 定时器 / 状态栏 / 该不该问。

最要紧的两条判据（整条功能的对错都在这里）：

  1. **恢复之后不是"已保存"**：`has_unsaved_edits()` 必须为真，
     状态栏才敢喊「● 未导出」。说反了就是"用户以为写回去了，随手一关真丢了"。
  2. **恢复出来的拍与快照逐拍一致**：比的是**渲染出来的行**
     （与导出同一条路），不是字段字典 —— 字段对得上但渲染变了，一样是坏。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _ui_project import build_project


# ---------------------------------------------------------------------------
#  夹具：自动保存一律写进临时目录（conftest 的 autouse 已把默认位置挪开）
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    """打开自动保存，并把目录指到 `tmp_path`（用例显式开关，不靠全局状态）。"""
    from storygraph.core import autosave as AS

    root = tmp_path / 'autosave'
    monkeypatch.setenv('STORYGRAPH_AUTOSAVE_DIR', str(root))
    before = AS.enabled()
    AS.set_enabled(True)
    try:
        yield AS
    finally:
        AS.set_enabled(before)


@pytest.fixture
def project(tmp_path):
    """现场生成的 Ren'Py 工程（4 label / 13 拍，与 `ui_window` 同一份）。"""
    return build_project(tmp_path / 'proj')


@pytest.fixture
def chapter(project):
    return project / 'game' / 'script' / 'ch01.rpy'


@pytest.fixture
def session(project, chapter):
    """按磁盘载入过 ch01 的会话（**恢复必须建立在它之上**，见模块说明）。"""
    from storygraph.app.editor import EditorSession

    s = EditorSession(project_root=project)
    s.load(chapter)
    return s


def _rendered(session) -> list:
    from storygraph.story.beat_render import render_beat_to_rpy

    return [list(render_beat_to_rpy(b)) for b in session.beats]


# ===========================================================================
#  第一层：core.autosave（纯文件）
# ===========================================================================

class TestStorage:
    def test_write_then_read_round_trip(self, store, project, chapter):
        beats = [{'seq': 1, 'text': '第一拍'}, {'seq': 2, 'text': '第二拍'}]
        res = store.write(project, chapter, beats,
                          meta={'chapter_rel': 'game/script/ch01.rpy',
                                'dirty': {'changed': 2}, 'total': 2},
                          extra={'file_blocks': 'init python:\n    x = 1'})
        assert res['ok'], res
        got = store.read(res['path'])
        assert got['ok'], got
        data = got['data']
        assert data['version'] == store.VERSION
        assert data['chapter'] == str(chapter)
        assert data['chapter_rel'] == 'game/script/ch01.rpy'
        assert data['total'] == 2
        assert data['dirty'] == {'changed': 2}
        assert [b['text'] for b in data['beats']] == ['第一拍', '第二拍']
        # 章节级顶层块随快照一起走（`extra` 通道）
        assert 'init python' in data['file_blocks']
        # 源文件指纹也记了（用来判"文件在崩溃之后有没有被改过"）
        assert data['source']['hash'] and data['source']['size'] > 0

    def test_write_is_atomic_no_tmp_left(self, store, project, chapter):
        res = store.write(project, chapter, [{'text': 'a'}])
        assert res['ok']
        assert not list(Path(res['path']).parent.glob('*.tmp')), \
            '原子写留下了半截文件'

    def test_extra_cannot_override_fixed_keys(self, store, project, chapter):
        res = store.write(project, chapter, [{'text': 'a'}],
                          extra={'version': 99, 'beats': 'oops',
                                 'file_blocks': 'ok'})
        got = store.read(res['path'])
        assert got['ok'], got
        assert got['data']['version'] == store.VERSION
        assert got['data']['beats'] == [{'text': 'a'}]
        assert got['data']['file_blocks'] == 'ok'

    def test_disabled_refuses_to_write(self, store, project, chapter):
        store.set_enabled(False)
        res = store.write(project, chapter, [{'text': 'a'}])
        assert not res['ok']
        assert '关' in res['reason']

    def test_env_switch_off(self, store, project, chapter, monkeypatch):
        monkeypatch.setenv('STORYGRAPH_NO_AUTOSAVE', '1')
        assert not store.enabled()
        assert not store.write(project, chapter, [{'text': 'a'}])['ok']

    def test_candidates_newest_first(self, store, project, chapter, tmp_path):
        other = tmp_path / 'other.rpy'
        other.write_text('label x:\n    return\n', encoding='utf-8')
        store.write(project, chapter, [{'text': 'old'}])
        # 手改 `saved_at`：文件系统的 mtime 精度不够，排序判据看的是字段
        path = store.snapshot_path(project, chapter)
        data = json.loads(path.read_text(encoding='utf-8'))
        data['saved_at'] = '2020-01-01T00:00:00'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        store.write(project, other, [{'text': 'new'}])
        entries = store.candidates(project)
        assert len(entries) == 2
        assert entries[0]['saved_at'] > entries[1]['saved_at']
        assert entries[0]['chapter'] == str(other)

    def test_candidates_filter_by_chapter(self, store, project, chapter,
                                          tmp_path):
        other = tmp_path / 'other.rpy'
        other.write_text('label x:\n    return\n', encoding='utf-8')
        store.write(project, chapter, [{'text': 'a'}])
        store.write(project, other, [{'text': 'b'}])
        assert len(store.candidates(project)) == 2
        one = store.candidates(project, chapter)
        assert len(one) == 1 and one[0]['chapter'] == str(chapter)

    def test_broken_file_is_skipped_but_kept(self, store, project, chapter):
        store.write(project, chapter, [{'text': 'a'}])
        bad = store.project_dir(project) / 'broken.json'
        bad.write_text('{not json', encoding='utf-8')
        entries = store.candidates(project)
        assert len(entries) == 1, '坏文件应当被跳过'
        assert bad.is_file(), '坏文件不许删（用户可能想手工看一眼）'

    def test_newer_version_is_refused_and_kept(self, store, project, chapter):
        store.write(project, chapter, [{'text': 'a'}])
        path = store.snapshot_path(project, chapter)
        data = json.loads(path.read_text(encoding='utf-8'))
        data['version'] = store.VERSION + 1
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        got = store.read(path)
        assert not got['ok']
        assert '更新版本' in got['error']
        assert path.is_file()
        assert store.candidates(project) == []

    def test_discard_removes_only_that_chapter(self, store, project, chapter,
                                               tmp_path):
        other = tmp_path / 'other.rpy'
        other.write_text('label x:\n    return\n', encoding='utf-8')
        store.write(project, chapter, [{'text': 'a'}])
        store.write(project, other, [{'text': 'b'}])
        res = store.discard(project, chapter)
        assert res['ok'] and res['removed']
        assert [e['chapter'] for e in store.candidates(project)] == [str(other)]

    def test_discard_project_removes_all(self, store, project, chapter):
        store.write(project, chapter, [{'text': 'a'}])
        res = store.discard_project(project)
        assert res['ok'] and res['removed'] == 1
        assert store.candidates(project) == []
        assert not store.project_dir(project).is_dir(), '空目录应当顺手收掉'

    def test_prune_keeps_newest(self, store, project, tmp_path):
        # 造 KEEP+2 个不同章节的快照（key 用章节路径，所以必然是不同文件）
        for i in range(store.KEEP + 2):
            fp = tmp_path / f'ch{i}.rpy'
            fp.write_text('label x:\n    return\n', encoding='utf-8')
            store.write(project, fp, [{'text': str(i)}])
        entries = store.candidates(project)
        assert len(entries) == store.KEEP, '超过上限的应当被清掉'
        # 最新的那几份必须还在（按 mtime 排序，最后写的那两个在里面）
        kept = {e['chapter'] for e in entries}
        assert str(tmp_path / f'ch{store.KEEP + 1}.rpy') in kept


class TestStale:
    def test_unchanged_file_is_not_stale(self, store, project, chapter):
        store.write(project, chapter, [{'text': 'a'}])
        entry = store.candidates(project)[0]
        assert store.stale_reason(entry) == ''

    def test_changed_file_is_stale_and_says_when(self, store, project, chapter):
        store.write(project, chapter, [{'text': 'a'}])
        entry = store.candidates(project)[0]
        chapter.write_text('label ch01:\n    e "别处改的"\n    return\n',
                           encoding='utf-8')
        reason = store.stale_reason(entry)
        assert '被改过' in reason
        assert 'ch01.rpy' in reason

    def test_missing_file_is_stale(self, store, project, chapter):
        store.write(project, chapter, [{'text': 'a'}])
        entry = store.candidates(project)[0]
        chapter.unlink()
        assert '不在磁盘上' in store.stale_reason(entry)

    def test_file_stamp_tracks_content(self, store, chapter):
        before = store.file_stamp(chapter)
        chapter.write_text(chapter.read_text(encoding='utf-8') + '\n# x\n',
                           encoding='utf-8')
        after = store.file_stamp(chapter)
        assert before['exists'] and after['exists']
        assert before['hash'] != after['hash']

    def test_entry_line_has_chapter_and_counts(self, store, project, chapter):
        store.write(project, chapter, [{'text': 'a'}],
                    meta={'dirty': {'changed': 3, 'added': 1}, 'total': 13})
        line = store.entry_line(store.candidates(project)[0])
        assert 'ch01.rpy' in line
        assert '3 拍内容改了' in line
        assert '1 拍是新加的' in line
        assert '自动保存' in line


# ===========================================================================
#  第二层：session_recover（拍 ↔ JSON，摊回会话）
# ===========================================================================

class TestSessionCaptureRestore:
    def test_capture_needs_a_chapter(self, store, project):
        from storygraph.app.editor import EditorSession

        s = EditorSession(project_root=project)
        res = s.autosave_capture()
        assert not res['ok']
        assert '还没打开' in res['reason']

    def test_capture_writes_metadata(self, store, session, project, chapter):
        session.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        res = session.autosave_capture()
        assert res['ok'], res
        entry = session.autosave_entries(chapter)[0]
        assert entry['total'] == len(session.beats)
        assert entry['dirty'].get('changed') == 1
        assert entry['chapter_rel'] == 'game/script/ch01.rpy'

    def test_restore_returns_identical_beats(self, store, session, chapter):
        """恢复出来的拍与快照**逐拍渲染一致**（字段对不上都算坏）。"""
        session.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        session.insert_beat(2)
        session.apply(2, {'Speaker': 'e', 'Text': '新插的一拍'})
        before = _rendered(session)
        assert session.autosave_capture()['ok']

        # 重新按磁盘载入（模拟"重开软件"）→ 再恢复
        session.load(chapter)
        assert len(session.beats) != len(before), '前提：磁盘那一版拍数不同'
        res = session.autosave_restore(session.autosave_entries(chapter)[0])
        assert res['ok'], res
        assert _rendered(session) == before

    def test_restore_leaves_session_dirty(self, store, session, chapter):
        """**恢复 ≠ 已保存**：状态栏必须还能喊「● 未导出」。"""
        session.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        assert session.autosave_capture()['ok']
        session.load(chapter)
        assert not session.has_unsaved_edits(), '前提：刚载入是干净的'
        res = session.autosave_restore(session.autosave_entries(chapter)[0])
        assert res['ok']
        assert session.has_unsaved_edits()
        info = session.unsaved_edits()
        assert info['dirty'] and info['changed'] >= 1

    def test_restore_keeps_line_ids(self, store, session, chapter):
        """
        拍的身份编号**不许**被重排。

        `line_id` 是协作库那一行的主键（"每一拍有稳定身份"是拍级三路合并的
        地基）；恢复时顺手 `_renumber()` 会让**同一个内容**换一个新编号，
        协作那边就看不出"这是同一句"了。
        """
        session.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        before = [b.line_id for b in session.beats]
        assert session.autosave_capture()['ok']
        session.load(chapter)
        session.autosave_restore(session.autosave_entries(chapter)[0])
        assert [b.line_id for b in session.beats] == before

    def test_restore_keeps_src_lines_when_file_unchanged(self, store, session,
                                                        chapter):
        session.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        assert session.autosave_capture()['ok']
        session.load(chapter)
        res = session.autosave_restore(session.autosave_entries(chapter)[0])
        assert res['ok']
        assert res['realigned'] is False
        assert any(int(b.src_first or 0) > 0 for b in session.beats), (
            '源文件没变过时，行号应当留着（增量补丁才对得上）')

    def test_restore_zeroes_src_lines_when_file_changed(self, store, session,
                                                        chapter):
        """
        源文件在崩溃之后被改过 → **行号必须清零**。

        这是"宁可慢一点也不能写到别人的行上"那条底线的具体落点：
        旧行号配新文件 = 增量补丁把改动写到别处（且会报"成功"）。
        """
        session.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        assert session.autosave_capture()['ok']
        chapter.write_text(chapter.read_text(encoding='utf-8') + '\n# 别人的改动\n',
                           encoding='utf-8')
        session.load(chapter)
        res = session.autosave_restore(session.autosave_entries(chapter)[0])
        assert res['ok'], res
        assert res['realigned'] is True
        assert all(int(b.src_first or 0) == 0 for b in session.beats)
        assert all(int(b.src_last or 0) == 0 for b in session.beats)

    def test_restore_reports_first_change(self, store, session, chapter):
        session.apply(3, {'Speaker': 'e', 'Text': '第四拍改了'})
        assert session.autosave_capture()['ok']
        session.load(chapter)
        res = session.autosave_restore(session.autosave_entries(chapter)[0])
        assert res['ok']
        assert res['first_change'] == 3, res

    def test_restore_keeps_file_blocks(self, store, session, chapter):
        blocks = session.chapter_file_blocks()
        if not blocks.strip():
            pytest.skip('这一章没有顶层块（夹具变了？）')
        session.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        assert session.autosave_capture()['ok']
        session.load(chapter)
        session.chapter.file_blocks = ''
        session.autosave_restore(session.autosave_entries(chapter)[0])
        assert session.chapter_file_blocks() == blocks

    def test_restore_bad_entry_leaves_session_alone(self, store, session,
                                                    chapter, tmp_path):
        session.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        before = _rendered(session)
        bad = tmp_path / 'bad.json'
        bad.write_text('{', encoding='utf-8')
        res = session.autosave_restore({'path': str(bad), 'chapter': str(chapter)})
        assert not res['ok']
        assert _rendered(session) == before, '恢复失败不许动内存里的拍'

    def test_restore_refuses_empty_beats(self, store, session, chapter):
        bad = tmp_path_beats = store.snapshot_path(session.project_root, chapter)
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(json.dumps({'version': 1, 'beats': []}), encoding='utf-8')
        res = session.autosave_restore({'path': str(bad), 'chapter': str(chapter)})
        assert not res['ok']
        assert '没有可用的拍' in res['error']

    def test_discard_current_chapter(self, store, session, chapter):
        session.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        assert session.autosave_capture()['ok']
        assert session.autosave_discard()['ok']
        assert session.autosave_entries(chapter) == []


# ===========================================================================
#  第三层：窗口（定时器 / 状态栏 / 该不该问）
# ===========================================================================

pytest.importorskip('PySide6')


@pytest.fixture
def window(store, ui_window):
    """`ui_window` 已经载入 ch01（`conftest.ui_window`）。"""
    return ui_window


class TestWindowAutosave:
    def test_dirty_edit_is_saved_and_shown(self, window, chapter):
        window._session.apply(0, {'Speaker': 'e', 'Text': '窗口里改的'})
        res = window.autosave_now()
        assert res['ok'], res
        assert window._session.autosave_entries(chapter)
        # 状态栏那一格必须出现钟点 —— 看不见的自动保存在体验上等于没有
        assert '自动保存' in window._status_bar._autosave.text()
        assert ':' in window._status_bar._autosave.text()

    def test_hidden_window_does_not_write(self, window, chapter):
        window._session.apply(0, {'Speaker': 'e', 'Text': '藏着改的'})
        window.hide()
        window._autosave_tick()
        assert window._session.autosave_entries(chapter) == []

    def test_clear_removes_snapshot(self, window, chapter):
        window._session.apply(0, {'Speaker': 'e', 'Text': '窗口里改的'})
        assert window.autosave_now()['ok']
        window.autosave_clear()
        assert window._session.autosave_entries(chapter) == []

    def test_noop_detection_says_nothing_to_recover(self, window, chapter):
        """
        快照与磁盘**一样** → 不该问。

        真实的来路是"改了又撤销回去再崩"：那次改动被撤销之后，快照里那份
        已经不代表任何"没导出的东西"。假警报的代价不是烦一次 ——
        它会训练用户闭眼点"恢复"，于是真出事那天也闭眼点。
        """
        window._session.apply(0, {'Speaker': 'e', 'Text': '改了'})
        window._session.load(chapter)            # 撤销回到磁盘那一版
        assert window.autosave_now()['ok']       # 快照 == 磁盘
        entry = window._session.autosave_entries(chapter)[0]
        assert window._recovery_is_noop(entry) is True

    def test_noop_detection_false_when_different(self, window, chapter):
        window._session.apply(0, {'Speaker': 'e', 'Text': '改了'})
        assert window.autosave_now()['ok']
        window._session.load(chapter)            # 模拟重开软件 → 拍回到磁盘那一版
        entry = window._session.autosave_entries(chapter)[0]
        assert window._recovery_is_noop(entry) is False

    def test_failed_autosave_is_reported(self, window, chapter, monkeypatch):
        """存失败必须说出来（悄悄失败比没有自动保存更坏）。"""
        from storygraph.core import autosave as AS

        def boom(*_a, **_kw):
            return {'ok': False, 'path': '', 'reason': '磁盘满了'}
        monkeypatch.setattr(AS, 'write', boom)
        window._session.apply(0, {'Speaker': 'e', 'Text': '写不进去'})
        res = window.autosave_now()
        assert not res['ok']
        assert '失败' in window._status_bar._autosave.text()
        assert '磁盘满了' in window._status_bar._autosave.toolTip()

    def test_tick_clears_snapshot_once_clean_again(self, window, chapter):
        """
        脏 → 抄一份；又变干净（撤销回原样）→ **定时点把快照收掉**。

        没有这一步，上面那条 `_recovery_is_noop` 就永远是"事后诸葛亮"：
        快照是在撤销**之前**写下的，重启时它确实比磁盘多东西。
        真正让假警报消失的是"变干净就清掉"。
        """
        window._session.apply(0, {'Speaker': 'e', 'Text': '改了'})
        assert window.autosave_now()['ok']
        assert window._session.autosave_entries(chapter)
        window._session.load(chapter)            # 变干净（等价于撤销回原样）
        window.show()
        try:
            window._autosave_tick()
        finally:
            assert window._session.autosave_entries(chapter) == []
            window.hide()

    def test_menu_restore_does_not_ask_twice(self, window, chapter,
                                             monkeypatch):
        """
        从菜单「🕘 上次没导出的改动」恢复时，**不许再被载入末尾那个问句拦一次**。

        真实的坑：`_open_recovery_entry` 会先 `load_script()`（按磁盘读一遍），
        而 `load_script` 末尾就会问"要恢复吗" → 用户点了"恢复最新那份"，
        紧接着又被同一个快照问一遍（看起来像点了没用）。
        """
        window._session.apply(0, {'Speaker': 'e', 'Text': '改成快照里的版本'})
        assert window.autosave_now()['ok']
        entry = window._session.autosave_entries(chapter)[0]
        window._session.load(chapter)            # 回到磁盘那一版（模拟重开）
        asked = []
        monkeypatch.setattr(window, '_ask_recovery',
                            lambda e: asked.append(e) or False)
        monkeypatch.setattr(window, 'load_script',
                            lambda *a, **k: window._session.load(chapter))
        window._open_recovery_entry(entry)
        assert asked == [], '从菜单恢复时又被问了一遍（同一个快照）'
        # 真的恢复了（而不是只在标记里跳过）
        assert any('改成快照里的版本' in (b.text or '') for b in window._session.beats)
