# -*- coding: utf-8 -*-
"""
tests/test_stage_entry.py
=========================
**本章第一拍之前台上是什么**（跨章继承，2026-10-31）。

这一份钉住的是"上一章是谁"这条路真的接上了 —— 不是"界面好不好看"：

  ① 判据只有一份：入口状态来自 `pipeline.continuity.Chapter.entry`
     （`stage_exit_state` 的产物），这里只加"为什么"与"哪些不知道"；
  ② **不知道 ≠ 空**：链根 / 分支汇聚 / 环里 → 每个通道都是"不知道"，
     卡片必须写"不知道"，不许退化成"（未设置）/（台上没人）"；
  ③ 会话侧：`stage_initial_state()` 是唯一入口，喂给
     `stage_digest_text` / `stage_size_rows` / 卡片小图 / 演出预览；
  ④ 缓存按**项目根 + 源文件指纹**失效（整本 2 秒，不许摆在 GUI 线程）；
  ⑤ 卡片把入口状态塞进会话之后**只重画派生面板**，不重载表单
     （后台算两秒的工夫，用户可能已经在打字）。

真机（`sr_general`）：43 章里 6 章的入口有确定背景 —— chapter1_2 ← bg9_2、
chapter2_1 / 2_1_1~4 ← black，正是跨章体检报的那 6 处接缝。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storygraph.app import stage_entry as SE
from storygraph.app.editor import EditorSession
from storygraph.app.stage import build_frame_at, build_frames
from storygraph.pipeline.continuity import (Chapter, ChapterEntry,
                                            ContinuityReport, analyze_project,
                                            entry_by_file, entry_for_file)
from storygraph.story.stage_snapshot import EXIT_STATE_KEYS, stage_digest


# ------------------------------------------------------------------
#  夹具：每章一个文件的最小项目（文件 → 章 的对应关系才唯一）
# ------------------------------------------------------------------

def _write(root, name: str, text: str) -> Path:
    path = Path(root) / 'game' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


@pytest.fixture(scope='module')
def chain(tmp_path_factory) -> dict:
    """c1（写在 bg room）→ c2（什么都不写）＋ 编排层 start。"""
    root = tmp_path_factory.mktemp('chain')
    c1 = _write(root, 'c1.rpy',
                'label c1:\n'
                '    scene bg room\n'
                '    show ling happy\n'
                '    play music song1\n'
                '    "c1 的台词"\n'
                '    return\n')
    c2 = _write(root, 'c2.rpy',
                'label c2:\n'
                '    "c2 的第一句（前面什么都没写）"\n'
                '    return\n')
    _write(root, 'controls.rpy',
           'label start:\n'
           '    call c1\n'
           '    call c2\n'
           '    return\n')
    report = analyze_project(root)
    return {'root': root, 'c1': c1, 'c2': c2, 'report': report}


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例都从空缓存开始（缓存是模块级的，跨用例会互相污染）。"""
    SE.clear_cache()
    yield
    SE.clear_cache()


# ==================================================================
#  一、ChapterEntry：确定 / 不知道 两种说法
# ==================================================================

def _entry(state=None, why='接上一章 c1 的收尾', label='c2',
           file='x.rpy') -> ChapterEntry:
    return ChapterEntry(label=label, file=file, state=dict(state or {}),
                        why=why)


class TestEntryWording:
    def test_a_known_state_says_what_is_on_stage(self):
        e = _entry({'scene': 'bg room', 'actors': [['ling happy', '']],
                    'layers': {'cg': 'cg cg01'},
                    'music': 'bgm/a.mp3', 'unknown': set()})
        assert e.known('scene') and e.scene == 'bg room'
        assert e.has_any is True
        note = e.note()
        assert '背景 room' in note and '立绘 ling happy' in note
        assert 'CG cg01' in note and 'BGM a.mp3' in note
        assert '不知道' not in note

    def test_an_unknown_channel_is_worded_not_empty(self):
        e = _entry({'scene': '', 'actors': [], 'layers': {},
                    'music': 'bgm/a.mp3',
                    'unknown': {'scene', 'actors', 'layers'}})
        note = e.note()
        assert '背景不知道' in note and '立绘不知道' in note
        assert '台上没人' not in note          # 空 ≠ 没人
        assert '没有背景' not in note
        assert e.known('scene') is False and e.scene == ''
        assert e.has_any is True               # BGM 是确定的

    def test_an_empty_unknown_state_has_nothing(self):
        e = _entry({'unknown': set(EXIT_STATE_KEYS)})
        assert e.has_any is False
        assert '背景不知道' in e.note()

    def test_describe_puts_the_reason_first(self):
        e = _entry({'unknown': set()}, why='接上一章 chapter1_1 的收尾')
        assert e.describe().startswith('接上一章 chapter1_1 的收尾；')

    def test_key_is_stable_against_dict_and_set_order(self):
        a = _entry({'scene': 'bg room', 'unknown': {'music', 'voice'}})
        b = _entry({'scene': 'bg room', 'unknown': {'voice', 'music'}})
        assert a.key() == b.key()
        c = _entry({'scene': 'bg room', 'unknown': {'music'}})
        assert c.key() != a.key()

    def test_to_dict_is_json_friendly(self):
        e = _entry({'scene': 'bg room', 'music': '',
                    'unknown': {'voice'}})
        data = e.to_dict()
        assert data['unknown'] == ['voice']
        assert data['state']['scene'] == 'bg room'
        assert data['note']


# ==================================================================
#  二、文件 → 入口状态（判据来自 continuity，不另写一套）
# ==================================================================

class TestEntryByFile:
    def test_the_second_chapter_inherits_the_first_ones_exit(self, chain):
        e = entry_for_file(chain['report'], chain['c2'])
        assert e is not None
        assert e.label == 'c2'
        assert e.known('scene') and e.scene == 'bg room'
        assert '接上一章 c1' in e.why
        # 立绘 / BGM 也是上一章留下的（这里只钉"知道就行"）
        assert e.known('actors') and [a[0] for a in e.state['actors']] \
            == ['ling happy']
        assert e.state['music'] == 'song1'

    def test_the_first_chapter_has_no_previous_one(self, chain):
        e = entry_for_file(chain['report'], chain['c1'])
        assert e is not None and e.label == 'c1'
        assert '链根' in e.why
        assert e.known('scene') is False
        assert e.has_any is False

    def test_a_file_that_is_not_a_chapter_has_no_entry(self, chain):
        other = _write(chain['root'], 'options.rpy', 'define x = 1\n')
        assert entry_for_file(chain['report'], other) is None

    def test_paths_are_matched_case_insensitively(self, chain):
        shouty = Path(str(chain['c2']).upper())
        assert entry_for_file(chain['report'], shouty) is not None

    def test_one_file_with_two_chapters_uses_the_first(self):
        """同一文件里两章时取**开头那一章**（文件是从它开始演的）。"""
        first = Chapter(label='a', file='x/game/one.rpy', order=0,
                        entry={'scene': 'bg a', 'unknown': set()})
        second = Chapter(label='b', file='X/GAME/ONE.RPY', order=5,
                         entry={'scene': 'bg b', 'unknown': set()})
        rep = ContinuityReport(chapters=[second, first])
        e = entry_by_file(rep)['x/game/one.rpy']
        assert e.label == 'a' and e.scene == 'bg a'

    def test_a_merge_point_is_unknown_and_says_why(self, tmp_path):
        """两个分支汇进 c2 → 不知道走的是哪条；**不猜**"反正一样"。"""
        _write(tmp_path, 'c1a.rpy',
               'label c1a:\n    scene bg a\n    "A"\n    return\n')
        _write(tmp_path, 'c1b.rpy',
               'label c1b:\n    scene bg b\n    "B"\n    return\n')
        c2 = _write(tmp_path, 'c2.rpy',
                    'label c2:\n    "x"\n    return\n')
        _write(tmp_path, 'controls.rpy',
               'label start:\n'
               '    call c1a_control\n'
               '    return\n'
               '\n'
               'label c1a_control:\n'
               '    call c1a\n'
               '    menu:\n'
               '        "选 A":\n'
               '            jump c2_control\n'
               '        "选 B":\n'
               '            jump c1b_control\n'
               '\n'
               'label c1b_control:\n'
               '    call c1b\n'
               '    jump c2_control\n'
               '\n'
               'label c2_control:\n'
               '    call c2\n'
               '    return\n')
        rep = analyze_project(tmp_path)
        e = entry_for_file(rep, c2)
        assert e is not None
        assert '汇到这里' in e.why, e.why
        assert e.known('scene') is False

    def test_the_entry_by_file_map_is_cached_on_the_report(self, chain):
        rep = chain['report']
        first = entry_by_file(rep)
        assert entry_by_file(rep) is first        # 不重算


# ==================================================================
#  三、会话：喂进去之后，卡片那几处都能看见
# ==================================================================

class TestSessionWiring:
    def _session(self, chain, path=None):
        s = EditorSession(project_root=chain['root'])
        s.load(path or chain['c2'])
        return s

    def test_without_an_entry_the_card_says_nothing_is_set(self, chain):
        s = self._session(chain)
        text = s.stage_digest_text(0)
        assert '背景：（未设置）' in text
        assert '跨章继承' not in text
        assert s.stage_initial_state() is None
        # 说清"为什么没有"：还没分析过整本 —— 空着不等于"上一章什么都没留"
        assert '上一章留了什么 —— 还不知道' in text
        assert '还没分析' in text

    def test_single_file_mode_says_nothing_about_other_chapters(self, chain):
        """没有项目目录时跨章本来不存在 —— 别每一拍都喊一句（老行为不变）。"""
        s = EditorSession()
        s.load(chain['c2'])
        assert s.stage_digest_text(0) == stage_digest(s.beats, 0)

    def test_the_digest_shows_the_inherited_background(self, chain):
        s = self._session(chain)
        assert s.set_stage_entry(entry_for_file(chain['report'], chain['c2']))
        text = s.stage_digest_text(0)
        assert '跨章继承：背景（本章没写过，来自上一章）' in text
        assert '接上一章 c1 的收尾' in text
        assert '背景：bg room' in text
        assert '立绘：ling happy' in text

    def test_unknown_channels_are_worded_as_unknown(self, chain):
        e = ChapterEntry(label='c2', file=str(chain['c2']), why='上游有分支',
                         state={'scene': '', 'actors': [],
                                'unknown': {'scene', 'actors'}})
        s = self._session(chain)
        s.set_stage_entry(e)
        text = s.stage_digest_text(0)
        assert '背景：（不知道' in text
        assert '立绘：（不知道' in text
        assert '（未设置）' not in text
        assert '（台上没人）' not in text

    def test_the_size_rows_get_a_cross_chapter_source(self, chain):
        """`source < 0` 正是「本章第一拍补一条 show」那个入口的前提。"""
        from storygraph.app.stage import is_unknown_source
        s = self._session(chain)
        assert s.stage_size_rows(0) == []          # 没有入口状态：这一拍没背景
        s.set_stage_entry(entry_for_file(chain['report'], chain['c2']))
        rows = s.stage_size_rows(0)
        assert [r['kind'] for r in rows] == ['scene']
        assert rows[0]['spec'] == 'bg room'
        assert is_unknown_source(rows[0]['source'])
        assert '上一章' in rows[0]['why']

    def test_a_cross_chapter_row_does_not_claim_it_is_already_as_is(self, chain):
        """
        跨章那一行的 at **看不到** —— 说"原样（已经是原样）"是假装知道。

        真机截图当场露出来过（chapter1_2 第 1 拍：下拉写着"已经是原样"，
        可上一章写的是什么这里根本读不到）。
        """
        s = self._session(chain)
        s.set_stage_entry(entry_for_file(chain['report'], chain['c2']))
        row = s.stage_size_rows(0)[0]
        as_is = next(i for i in row['items'] if i['key'] == 'as_is')
        assert '已经是原样' not in as_is['text']
        assert '跨章' in as_is['text']
        assert '上一章的 at 看不到' in as_is['why']

    def test_a_local_row_keeps_the_old_wording(self, chain):
        """本章自己写的那一行照旧（`entry_state` 缺省 = 不是跨章）。"""
        s = EditorSession(project_root=chain['root'])
        s.load(chain['c1'])
        row = s.stage_size_rows(0)[0]
        as_is = next(i for i in row['items'] if i['key'] == 'as_is')
        assert as_is['text'] == '原样（已经是原样）'
        assert '本来就没有 at' in as_is['why']

    def test_frames_are_built_on_top_of_the_entry(self, chain):
        s = self._session(chain)
        s.set_stage_entry(entry_for_file(chain['report'], chain['c2']))
        frame = build_frame_at(s.beats, 0,
                               initial_state=s.stage_initial_state())
        assert frame.scene == 'bg room'
        assert frame.scene_src < 0                 # 出处不在这本剧本里
        assert frame.entry_state is True
        # 整章每一拍都接着这一份入口状态演（这一章自己没写过 scene）
        assert {f.scene for f in s.frames()} == {'bg room'}

    def test_the_entry_only_affects_this_chapter(self, chain):
        """另一章（链根那章）拿到的是"不知道"，不是这一章的入口。"""
        s = EditorSession(project_root=chain['root'])
        s.load(chain['c1'])
        s.set_stage_entry(entry_for_file(chain['report'], chain['c1']))
        assert s.stage_initial_state() is not None
        assert '跨章继承' not in s.stage_digest_text(0)   # 第 0 拍自己写了 scene
        assert '背景：bg room' in s.stage_digest_text(0)

    def test_setting_the_same_entry_twice_reports_no_change(self, chain):
        s = self._session(chain)
        e = entry_for_file(chain['report'], chain['c2'])
        assert s.set_stage_entry(e) is True
        assert s.set_stage_entry(e) is False
        assert s.set_stage_entry(None) is True

    def test_loading_another_chapter_drops_the_old_entry(self, chain):
        s = self._session(chain)
        s.set_stage_entry(entry_for_file(chain['report'], chain['c2']))
        assert s.stage_initial_state() is not None
        s.load(chain['c1'])
        assert s.stage_initial_state() is None
        assert s.stage_entry() is None

    def test_the_note_and_the_reason_are_readable(self, chain):
        s = self._session(chain)
        assert '还没分析' in s.stage_entry_why_not()
        s.set_stage_entry(entry_for_file(chain['report'], chain['c2']))
        assert s.stage_entry_why_not() == ''
        assert '接上一章 c1' in s.stage_entry_note()


# ==================================================================
#  四、app.stage_entry：缓存 / 失效 / 只说人话
# ==================================================================

class TestEntryCache:
    def test_without_a_report_there_is_no_entry_and_a_reason(self, chain):
        assert SE.entry_for(chain['root'], chain['c2']) is None
        assert '还没分析' in SE.why_not(chain['root'], chain['c2'])

    def test_analyze_remembers_the_report_by_signature(self, chain):
        sig, report = SE.analyze(chain['root'])
        assert sig and SE.cached_report(chain['root']) is report
        assert SE.stale(chain['root']) is False
        assert SE.entry_for(chain['root'], chain['c2']) is not None

    def test_changing_a_file_makes_the_report_stale(self, chain):
        SE.analyze(chain['root'])
        target = Path(chain['c1'])
        target.write_text(target.read_text(encoding='utf-8') + '\n# 改一下\n',
                          encoding='utf-8')
        assert SE.stale(chain['root']) is True
        assert SE.entry_for(chain['root'], chain['c2']) is None
        # 改回去（同一份内容也会换 mtime —— 反正下一次分析自己会重记）
        target.write_text(target.read_text(encoding='utf-8')
                          .replace('\n# 改一下\n', ''), encoding='utf-8')

    def test_a_report_from_another_project_is_refused(self, chain):
        _, report = SE.analyze(chain['root'])
        assert SE.matches_project(chain['root'], report) is True
        assert SE.matches_project(chain['root'] / 'elsewhere', report) is False
        assert SE.matches_project(chain['root'], None) is False

    def test_no_project_means_no_question(self, chain):
        assert SE.signature('') == ''
        assert SE.entry_for('', chain['c2']) is None
        assert '没有项目目录' in SE.why_not('', chain['c2'])


# ==================================================================
#  五、卡片：把入口状态塞进会话（只重画派生面板）
# ==================================================================

class _FakeCard:
    """只带 `CardPageMixin._apply_stage_entry` 用到的那几样。"""

    def __init__(self, session):
        self._session = session
        self._loaded_idx = 0
        self.logs: list = []
        self.refreshed: list = []

    def _log(self, text):
        self.logs.append(str(text))

    def refresh_derived_panels(self, idx=None):
        self.refreshed.append(idx)


class TestTheCardAppliesIt:
    def _fake(self, chain):
        s = EditorSession(project_root=chain['root'])
        s.load(chain['c2'])
        return _FakeCard(s)

    def test_it_sets_the_entry_and_redraws_once(self, chain):
        from storygraph.ui_qt.window_card import CardPageMixin
        fake = self._fake(chain)
        _, report = SE.analyze(chain['root'])
        assert CardPageMixin._apply_stage_entry(fake, report) is True
        assert fake._session.stage_initial_state() is not None
        assert fake.refreshed == [0]
        assert any('接上一章 c1' in line for line in fake.logs)
        # 再来一次：状态没变 → 不重画
        assert CardPageMixin._apply_stage_entry(fake, report) is False
        assert fake.refreshed == [0]

    def test_a_report_of_another_project_is_ignored(self, chain, tmp_path):
        from storygraph.ui_qt.window_card import CardPageMixin
        fake = self._fake(chain)
        other = analyze_project(tmp_path)
        assert CardPageMixin._apply_stage_entry(fake, other) is False
        assert fake._session.stage_initial_state() is None
        assert fake.refreshed == []

    def test_no_project_or_no_script_means_no_analysis(self):
        from storygraph.ui_qt.window_card import CardPageMixin

        class _Bare:
            _session = None

        assert CardPageMixin.warm_stage_entry(_Bare()) == 'skipped'


# ==================================================================
#  六、后台分析：只排一次、算完进缓存（不碰 GUI 线程）
# ==================================================================

class TestTheWarmer:
    def test_no_project_means_no_analysis(self, qapp):
        from storygraph.ui_qt.entry_warm import StageEntryWarmer
        w = StageEntryWarmer()
        assert w.request('') == 'skipped'
        assert w.is_running() is False
        w.shutdown(100)

    def test_a_warm_cache_is_reused_instead_of_reanalyzing(self, qapp, chain):
        from storygraph.ui_qt.entry_warm import StageEntryWarmer
        SE.analyze(chain['root'])
        w = StageEntryWarmer()
        assert w.request(chain['root']) == 'cached'
        assert w.is_running() is False
        w.shutdown(100)

    def test_a_cold_cache_runs_once_and_fills_the_cache(self, qapp, chain):
        from PySide6.QtCore import QEventLoop, QTimer
        from storygraph.ui_qt.entry_warm import StageEntryWarmer
        w = StageEntryWarmer()
        seen = []
        w.ready.connect(seen.append)
        assert w.request(chain['root']) == 'queued'
        loop = QEventLoop()
        w.ready.connect(lambda _r: loop.quit())
        QTimer.singleShot(20000, loop.quit)
        loop.exec()
        assert len(seen) == 1                    # 只算一次
        assert SE.cached_report(chain['root']) is seen[0]
        assert w.is_running() is False
        w.shutdown(100)


# ==================================================================
#  七、演出预览：写回 at 之后重算帧不能把入口状态弄丢
# ==================================================================

class TestPreviewKeepsTheEntry:
    def test_rebuild_uses_the_entry(self, qapp, chain):
        from storygraph.ui_qt.stage_preview import StagePreviewWindow
        s = EditorSession(project_root=chain['root'])
        s.load(chain['c2'])
        s.set_stage_entry(entry_for_file(chain['report'], chain['c2']))
        win = StagePreviewWindow(None, s.frames(), project_root=chain['root'],
                                 real_images=False, sound=False,
                                 beats=list(s.beats),
                                 initial_state=s.stage_initial_state())
        try:
            assert win.frames and win.frames[0].scene == 'bg room'
            win._refresh_frames_after_beat_edit()
            assert win._all[0].scene == 'bg room'
        finally:
            win.close()
            win.deleteLater()
