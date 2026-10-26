"""
tests/test_import_stamps.py
===========================
**别再为"没改过的文件"付钱**（用户 2026-10-27："剩下的一些流程也需要优化"）。

真机（`sr_general`，91 个 `.rpy` / 1.2 MB）实测：

    🔁 刷新已导入章节   3.0 秒 → 4 毫秒（一个字节都没动时）
    🔁 刷新已导入章节   3.0 秒 → 只解改动的那几个文件
    ↻ 反复导入同一批     2.7 秒 → 1.7 秒（AST 复用，不再重解析）

两条互不替代的改动：

1. **会话持有一个 `IncrementalRenPyAnalyzer`**：它本来就有按 `(mtime, size)`
   复用 AST 的缓存，但每次导入都 new 一个 = 缓存只活一次调用。现在跨调用复用。
2. **刷新只重读真的改过的文件**：用导入时记下的 `(mtime_ns, size)` 比一遍；
   一个都没变就**连预览框都不弹**，直接说"没有改动"。

判据分三层：分析器、会话、界面动作。任何一层把"没变"当成"变了"都只是慢，
但把"变了"当成"没变"就是**改了看不到** —— 所以第 2 条的用例必须两边都钉。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import (  # noqa: E402
    IncrementalRenPyAnalyzer, RenPyParser)

pytest.importorskip('PySide6')


def _chapter(n: int) -> str:
    return (f'label ch{n}:\n'
            f'    "第 {n} 章的台词"\n'
            f'    return\n')


def _project(tmp_path: Path) -> Path:
    root = tmp_path / 'proj'
    game = root / 'game' / 'script'
    game.mkdir(parents=True)
    for i in (1, 2, 3):
        (game / f'ch{i:02d}.rpy').write_text(_chapter(i), encoding='utf-8')
    return root


def _files(root: Path) -> list:
    return sorted(str(p) for p in (root / 'game').rglob('*.rpy'))


class TestAnalyzerCache:
    """分析器这一层：同一份文件只解析一次，改过的必须重解。"""

    @staticmethod
    def _count_parses(monkeypatch):
        calls = []
        real = RenPyParser.parse

        def _spy(self, *a, **k):
            calls.append(a[0] if a else k.get('path'))
            return real(self, *a, **k)

        monkeypatch.setattr(RenPyParser, 'parse', _spy)
        return calls

    def test_second_analyze_does_not_reparse(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        files = _files(root)
        an = IncrementalRenPyAnalyzer()
        an.analyze(files, graph=None)

        calls = self._count_parses(monkeypatch)
        an.analyze(files, graph=None)
        assert calls == [], f'第二次还在解析：{calls}'

    def test_only_the_changed_file_is_reparsed(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        files = _files(root)
        an = IncrementalRenPyAnalyzer()
        an.analyze(files, graph=None)

        changed = Path(files[1])
        changed.write_text(_chapter(2) + '\nlabel extra:\n    return\n',
                           encoding='utf-8')
        calls = self._count_parses(monkeypatch)
        pr, _graph = an.analyze(files, graph=None)
        assert [Path(str(c)).name for c in calls] == [changed.name]
        assert 'extra' in (getattr(pr, 'labels', {}) or {})

    def test_deleted_file_leaves_the_cache(self, tmp_path):
        root = _project(tmp_path)
        files = _files(root)
        an = IncrementalRenPyAnalyzer()
        an.analyze(files, graph=None)
        before = an.stats()['cached_files']
        doomed = files[0]
        Path(doomed).unlink()
        an.analyze([f for f in files if f != doomed], graph=None)
        assert an.stats()['cached_files'] == before - 1
        assert not any(str(Path(doomed).resolve()) == f
                       for f in an.stats()['files'])

    def test_cache_is_bounded_but_keeps_the_current_batch(self, tmp_path):
        root = tmp_path / 'proj'
        game = root / 'game'
        game.mkdir(parents=True)
        for i in range(6):
            (game / f'c{i}.rpy').write_text(
                'label c{i}:\n    "台词 {i}"\n    return\n'.format(i=i),
                encoding='utf-8')
        files = _files(root)
        size = Path(files[0]).stat().st_size
        cap = size * 2
        an = IncrementalRenPyAnalyzer(max_source_bytes=cap)

        # 第一批比上限还大：**本批一个都不许丢**（丢一次导入要解析两遍），
        # 所以这一批允许暂时超上限。
        an.analyze(files[:4], graph=None)
        assert len(an.stats()['files']) == 4

        # 换一批：旧批次该被淘汰，新批次留下
        an.analyze(files[4:], graph=None)
        st = an.stats()
        assert st['cached_files'] == 2, st['files']
        assert st['cached_source_bytes'] <= cap

    def test_rebuilding_from_the_cache_is_identical(self, tmp_path):
        """同一个 AST 建两次，产物必须一样（跨批次复用 AST 的前提）。"""
        from storygraph.story.beat_build import beats_from_parse_result

        root = _project(tmp_path)
        files = _files(root)
        an = IncrementalRenPyAnalyzer()
        pr1, g1 = an.analyze(files, graph=None)
        pr2, g2 = an.analyze(files, graph=None)

        def _stmts(pr):
            return [str(getattr(s, 'raw', '')) for s in pr.statements]

        def _beats(pr):
            return [(b.label, str(getattr(b, 'raw', '')))
                    for b in beats_from_parse_result(pr)]

        assert _stmts(pr1) == _stmts(pr2)
        assert _beats(pr1) == _beats(pr2)
        assert ({n.label_name for n in g1.nodes.values()}
                == {n.label_name for n in g2.nodes.values()})


@pytest.fixture
def project(tmp_path):
    return _project(tmp_path)


@pytest.fixture
def window(qapp, project):
    from storygraph.ui_qt.main_window import MainWindowQt

    class _Engine:
        project_root = project

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), None)
    yield win
    win.close()


class TestSessionStamps:
    """会话这一层：导入记时间戳，刷新只挑"真的改过"的那些。"""

    def test_second_import_uses_the_same_analyzer(self, project, monkeypatch):
        from storygraph.app.editor import EditorSession

        files = _files(project)
        session = EditorSession(registry_provider=lambda: None,
                                project_root=project)
        first = session.import_chapters(files)
        assert first.get('ok'), first.get('error')
        calls = []
        real = RenPyParser.parse

        def _spy(self, *a, **k):
            calls.append(a[0] if a else k.get('path'))
            return real(self, *a, **k)

        monkeypatch.setattr(RenPyParser, 'parse', _spy)
        session.import_chapters(files, first['graph'])
        assert calls == [], f'第二次导入还在解析：{calls}'

    def test_stamps_say_nothing_changed(self, project):
        from storygraph.app.editor import EditorSession

        files = _files(project)
        session = EditorSession(registry_provider=lambda: None,
                                project_root=project)
        session.import_chapters(files)
        assert session.stale_import_files(files) == []

    def test_stamps_catch_a_touched_file(self, project):
        from storygraph.app.editor import EditorSession

        files = _files(project)
        session = EditorSession(registry_provider=lambda: None,
                                project_root=project)
        session.import_chapters(files)
        target = Path(files[0])
        # **同样长度**的改写：只靠 size 判不出来，必须靠 mtime
        target.write_text(_chapter(1).replace('第 1', '第 9'), encoding='utf-8')
        assert [Path(p).name for p in session.stale_import_files(files)] == \
            [target.name]

    def test_unknown_file_counts_as_changed(self, project):
        """没有导入记录的（图上塞进来的）一律算变 —— 宁可多读，不许糊弄。"""
        from storygraph.app.editor import EditorSession

        files = _files(project)
        session = EditorSession(registry_provider=lambda: None,
                                project_root=project)
        session.import_chapters(files)
        session._import_stamps().clear()
        assert len(session.stale_import_files(files)) == len(files)


class TestRefreshAction:
    """界面这一层：没改动 → 不弹预览、不重解；有改动 → 只重解那几个。"""

    def test_no_changes_means_no_dialog_and_no_reparse(self, window, project,
                                                       monkeypatch):
        from storygraph.ui_qt import dialogs as D

        files = _files(project)
        window.import_chapter_files(files)

        def _boom(*_a, **_k):
            raise AssertionError('一个文件都没改，不该弹预览框')

        monkeypatch.setattr(D, 'SyncPreviewDialog', _boom)
        monkeypatch.setattr(window, 'import_chapter_files',
                            lambda paths: pytest.fail('不该重新导入'))
        window.reload_imported_chapters()
        assert '没有改动' in window._status_bar.currentMessage()

    def test_only_changed_files_are_reimported(self, window, project,
                                               monkeypatch):
        from storygraph.ui_qt import dialogs as D

        files = _files(project)
        window.import_chapter_files(files)
        target = Path(files[0])
        target.write_text(_chapter(1) + '\nlabel later:\n    return\n',
                          encoding='utf-8')

        seen = {}

        class _FakeDialog:
            def __init__(self, parent=None, title='', intro='', detail='',
                         ok_text='', warn=''):
                seen.update(title=title, intro=intro, detail=detail,
                            ok_text=ok_text)

            def exec(self):
                return D.QDialog.Accepted

        called = []
        monkeypatch.setattr(D, 'SyncPreviewDialog', _FakeDialog)
        monkeypatch.setattr(window, 'import_chapter_files',
                            lambda paths: called.append(list(paths)))
        window.reload_imported_chapters()

        assert seen['title'] == '刷新已导入章节'
        assert 'later' in seen['detail']
        assert called == [[str(target)]], called
        assert str(len(files) - 1) in seen['intro']   # 其余几个明确说"跳过"
