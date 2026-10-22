"""
tests/test_feedback_p5_fold_project.py
======================================
2026-10-22 用户："继续完成折叠状态跟项目走"

判据（全部离线，不靠截图 —— 断言磁盘上的 JSON、列表项状态、偏好 ini）：

  ① 折叠状态写进**项目**（`storygraph.session.json` 的 `cards.folded`），
     键是**项目内相对路径**；用户偏好里不留第二份；
  ② **把项目目录整个搬走**之后，折叠状态还认得 —— 这是"跟项目走"的核心
     含义（以前按绝对路径存在注册表里，搬一下目录就全展开）；
  ③ 写项目文件是**读-改-写**：`layout` 等别的段一字不动；
  ④ 换版本之前的旧偏好（绝对路径）**迁移一次**进项目，旧条目删掉；
  ⑤ 项目里明确记着"都展开了"（`[]`）时，**不**回头去翻旧偏好；
  ⑥ 没打开项目（或文件在项目外）时仍写偏好 —— 单文件模式不回归；
  ⑦ 项目文件**读不懂**时不覆盖它（退回偏好）、**写不进去**时也不拦人
     （窗口照开，折叠照用）。
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtCore import QSettings                          # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project                         # noqa: E402

MULTI_LABEL = '''label main_a:
    e "one"
    e "two"
    e "three"

label side_b:
    e "four"
    e "five"
'''

REL = 'game/script/multi.rpy'


@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture(autouse=True)
def fold_store(tmp_path, monkeypatch):
    """偏好写临时 ini（与 p3 同一条理由：别拿用户注册表当夹具）。"""
    from storygraph.ui_qt import prefs
    ini = tmp_path / 'prefs.ini'
    monkeypatch.setattr(
        prefs, 'settings_factory',
        staticmethod(lambda: QSettings(str(ini), QSettings.IniFormat)))
    return ini


class _Engine:
    def __init__(self, root=None):
        self.project_root = root

    def get_registry(self):
        return None


def _window(root, script):
    from storygraph.ui_qt.main_window import MainWindowQt
    return MainWindowQt(_Engine(root), script)


@pytest.fixture
def proj(app, tmp_path):
    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'multi.rpy'
    script.write_text(MULTI_LABEL, encoding='utf-8')
    return root, script


def _fold_side_b(win):
    win._on_list_item_clicked(win._group_items['side_b'])


def _folded_in_project(root) -> dict:
    data = json.loads((root / 'storygraph.session.json').read_text('utf-8'))
    return (data.get('cards') or {}).get('folded') or {}


def _pref_text(fold_store) -> str:
    return fold_store.read_text(encoding='utf-8') if fold_store.exists() else ''


# ======================================================================
#  ① 写进项目（相对路径），偏好不留第二份
# ======================================================================

class TestFoldedStateLivesInProject:
    def test_fold_is_written_into_the_project_file(self, proj, fold_store):
        root, script = proj
        win = _window(root, script)
        try:
            _fold_side_b(win)
        finally:
            win.close()

        assert (root / 'storygraph.session.json').is_file(), \
            '折叠状态没有写进项目 —— 换台机器 / 搬目录就没了'
        assert _folded_in_project(root).get(REL) == ['side_b']
        assert 'side_b' not in _pref_text(fold_store), \
            '项目里有了之后不该再往用户偏好里写一份'

    def test_key_is_relative_to_the_project_root(self, proj):
        """键必须是 `game/script/multi.rpy`，不是 `D:\\…`（绝对路径搬走就失效）。"""
        root, script = proj
        win = _window(root, script)
        try:
            _fold_side_b(win)
        finally:
            win.close()
        keys = list(_folded_in_project(root))
        assert keys == [REL], keys
        assert ':' not in keys[0] and not keys[0].startswith('/'), keys

    def test_reopening_reads_it_back_without_any_preference(self, proj):
        root, script = proj
        win = _window(root, script)
        try:
            _fold_side_b(win)
        finally:
            win.close()

        again = _window(root, script)
        try:
            assert again._collapsed_labels == frozenset({'side_b'})
            assert again._list.item(again._row_of_beat(4)).isHidden()
        finally:
            again.close()


# ======================================================================
#  ② 整个项目目录搬走 —— "跟项目走"的核心
# ======================================================================

class TestFollowsTheProjectFolder:
    def test_fold_survives_moving_the_whole_project(self, proj, tmp_path):
        root, script = proj
        win = _window(root, script)
        try:
            _fold_side_b(win)
        finally:
            win.close()

        moved = tmp_path / 'moved' / 'proj_copy'
        moved.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(moved))
        script2 = moved / 'game' / 'script' / 'multi.rpy'
        assert script2.is_file()

        again = _window(moved, script2)
        try:
            assert again._collapsed_labels == frozenset({'side_b'}), \
                '项目换了目录，折叠状态就没了 —— 说明存的还是绝对路径'
            assert again._list.item(again._row_of_beat(4)).isHidden()
        finally:
            again.close()

    def test_copying_the_project_carries_the_fold_too(self, proj, tmp_path):
        root, script = proj
        win = _window(root, script)
        try:
            _fold_side_b(win)
        finally:
            win.close()

        copy = tmp_path / 'another_machine'
        shutil.copytree(root, copy)
        again = _window(copy, copy / REL)
        try:
            assert again._collapsed_labels == frozenset({'side_b'}), \
                '拷一份项目到"另一台机器"（另一个目录）就读不到折叠状态了'
        finally:
            again.close()


# ======================================================================
#  ③ 读-改-写：别的段一字不动
# ======================================================================

class TestProjectFileIsPatchedNotReplaced:
    def test_layout_section_survives_a_fold_write(self, proj):
        root, script = proj
        layout = {'page': 1, 'card_index': 2, 'geometry': [1600, 950, 10, 10]}
        (root / 'storygraph.session.json').write_text(
            json.dumps({'layout': layout, 'note': '团队共享'}), encoding='utf-8')

        win = _window(root, script)
        try:
            _fold_side_b(win)
        finally:
            win.close()

        data = json.loads((root / 'storygraph.session.json').read_text('utf-8'))
        assert data['layout'] == layout, '写折叠状态把项目里的 layout 冲掉了'
        assert data.get('note') == '团队共享', '读-改-写把不认识的字段丢了'
        assert data['cards']['folded'][REL] == ['side_b']

    def test_no_write_when_nothing_changed(self, proj):
        """翻页 / 展开不该反复改项目文件（mtime 是我们唯一的观测点）。"""
        root, script = proj
        win = _window(root, script)
        try:
            _fold_side_b(win)
            stamp = (root / 'storygraph.session.json').stat().st_mtime_ns
            for idx in (0, 1, 2):             # 只在 main_a 里翻（那一组是开着的）
                win._show_index(idx)          # 翻页会 apply_current_beat
            assert (root / 'storygraph.session.json').stat().st_mtime_ns == stamp, \
                '只是翻页就把项目文件重写了一遍'
        finally:
            win.close()


# ======================================================================
#  ④⑤ 旧偏好迁移一次 / 明确的空表优先
# ======================================================================

class TestMigrationFromOldPreference:
    def test_old_absolute_path_preference_is_migrated_once(self, proj, fold_store):
        root, script = proj
        from storygraph.ui_qt import prefs
        old_key = str(script.resolve())
        prefs.save_json('cards/folded_labels', {old_key: ['side_b']})

        win = _window(root, script)
        try:
            assert win._collapsed_labels == frozenset({'side_b'}), \
                '换版本之后，原来记在偏好里的折叠状态没被读回来'
        finally:
            win.close()

        assert _folded_in_project(root).get(REL) == ['side_b'], \
            '旧偏好没有迁进项目 —— 下次换机器还是会丢'
        assert 'side_b' not in _pref_text(fold_store), \
            '迁移之后旧偏好那条还在（两份会各自老化）'

    def test_explicit_empty_list_wins_over_the_old_preference(self, proj, fold_store):
        """项目里记着 `[]`（折过、又都展开了）→ 不该回头去翻旧偏好。"""
        root, script = proj
        from storygraph.ui_qt import prefs
        prefs.save_json('cards/folded_labels',
                        {str(script.resolve()): ['side_b']})
        (root / 'storygraph.session.json').write_text(
            json.dumps({'cards': {'folded': {REL: []}}}), encoding='utf-8')

        win = _window(root, script)
        try:
            assert win._collapsed_labels == frozenset(), \
                '项目里明确写了"都展开"，却又去翻旧偏好，折回去了'
            assert not win._list.item(win._row_of_beat(4)).isHidden()
        finally:
            win.close()

    def test_expanding_again_records_an_explicit_empty_list(self, proj):
        root, script = proj
        win = _window(root, script)
        try:
            _fold_side_b(win)
            _fold_side_b(win)                 # 再点一次 = 展开
        finally:
            win.close()
        assert _folded_in_project(root).get(REL) == [], \
            '展开之后应该留下空表（"折过、又展开了"），不是删掉这个键'


# ======================================================================
#  ⑥ 没项目 / 文件在项目外 —— 仍写偏好
# ======================================================================

class TestWithoutProject:
    def test_single_file_mode_still_uses_the_preference(self, app, tmp_path,
                                                        fold_store):
        from storygraph.ui_qt import prefs
        script = tmp_path / 'standalone.rpy'
        script.write_text(MULTI_LABEL, encoding='utf-8')
        win = _window(None, script)
        try:
            assert win._fold_project_key() == ''
            _fold_side_b(win)
        finally:
            win.close()
        store = prefs.load_json('cards/folded_labels', {}) or {}
        assert list(store) == [str(script.resolve())], store
        assert store[str(script.resolve())] == ['side_b']

    def test_file_outside_the_project_root_is_not_put_into_the_project(
            self, proj, tmp_path, fold_store):
        root, _script = proj
        outside = tmp_path / 'elsewhere' / 'multi.rpy'
        outside.parent.mkdir(parents=True)
        outside.write_text(MULTI_LABEL, encoding='utf-8')

        win = _window(root, outside)
        try:
            assert win._fold_project_key() == '', \
                '项目外的文件被算成了项目里的相对路径'
            _fold_side_b(win)
        finally:
            win.close()
        assert not (root / 'storygraph.session.json').exists(), \
            '项目外的文件也往项目里写了折叠状态'
        from storygraph.ui_qt import prefs
        assert (prefs.load_json('cards/folded_labels', {}) or {}).get(
            str(outside.resolve())) == ['side_b']


# ======================================================================
#  ⑦ 坏了 / 写不进去，都不拦人
# ======================================================================

class TestBrokenProjectFileIsHarmless:
    def test_broken_json_is_never_overwritten(self, proj, fold_store):
        """读不懂就**别写**：这份文件人也会手改，盖回去等于删掉别人的内容。"""
        root, script = proj
        broken = root / 'storygraph.session.json'
        broken.write_text('{ 这不是 JSON', encoding='utf-8')
        win = _window(root, script)          # 不许抛
        try:
            _fold_side_b(win)
            assert win._collapsed_labels == frozenset({'side_b'})
        finally:
            win.close()
        assert broken.read_text(encoding='utf-8') == '{ 这不是 JSON', \
            '把读不懂的项目文件覆盖掉了'
        from storygraph.ui_qt import prefs
        assert (prefs.load_json('cards/folded_labels', {}) or {}).get(
            str(script.resolve())) == ['side_b'], '坏文件时连偏好也没记'

    def test_unwritable_project_falls_back_to_the_preference(
            self, proj, fold_store, monkeypatch):
        """项目目录只读 / 文件被锁：退回偏好，界面照用，控制台说一句。"""
        root, script = proj
        from storygraph.core import session_store as SS

        def boom(*a, **kw):
            return {'ok': False, 'path': '', 'reason': 'Permission denied',
                    'changed': False}
        monkeypatch.setattr(SS, 'remember_cards', boom)

        win = _window(root, script)
        try:
            _fold_side_b(win)
            assert win._collapsed_labels == frozenset({'side_b'})
        finally:
            win.close()
        assert not (root / 'storygraph.session.json').exists()
        from storygraph.ui_qt import prefs
        assert (prefs.load_json('cards/folded_labels', {}) or {}).get(
            str(script.resolve())) == ['side_b'], '写项目失败之后连偏好也没记'
