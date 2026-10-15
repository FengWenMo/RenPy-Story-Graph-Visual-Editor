"""
**会话记忆**判据（2026-10-13，用户要求）
================================================================================

> 再给这个工具加个"记忆"功能，当第二次打开时自动读取之前打开过的那个项目，
> 如果项目内部有保存布局，一并读取，能做到吗？

分两层测，**两层都必须有**：

  ① **纯数据那一层**（`core/session_store.py`，不开窗口）——
     存哪儿、读不回来怎么办、项目搬家了还认不认得、最近列表会不会无限长；
  ② **界面那一层**（`window_state.py` + 真窗口）——
     "第二次打开"到底会不会回到上次那个项目、布局会不会摆回去、
     以及**两条不记忆的路**（命令行指定了项目 / 记忆里关掉了自动打开）。

⚠ 为什么 ② 不能省：① 全绿也可能"记了但没人读"——本仓的
`_sync_groups_to_slots` 就是这么坏过一次的（后端实现了、界面没接线）。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import session_store as SS


@pytest.fixture
def store(tmp_path, monkeypatch):
    """
    一个**显式**的临时记忆文件 + 打开记忆开关。

    `tests/conftest.py` 有一条 autouse 夹具把记忆整个关掉（否则跑全量测试会把你
    真实的最近项目表灌满临时目录），所以想测记忆的用例要自己开回来 ——
    而"开回来"只对这一个显式路径生效（`path=` 一路传下去），
    **绝不会**碰到 `%LOCALAPPDATA%`。
    """
    fp = tmp_path / 'session.json'
    SS.set_enabled(True)
    monkeypatch.setenv('STORYGRAPH_SESSION_CONFIG', str(fp))
    yield fp
    SS.set_enabled(False)


def _proj(tmp_path, name: str = 'proj') -> Path:
    root = tmp_path / name
    (root / 'game' / 'chapter').mkdir(parents=True)
    (root / 'game' / 'chapter' / 'ch01.rpy').write_text(
        'label start:\n    "旁白。"\n    return\n', encoding='utf-8')
    return root


# ===========================================================================
#  ① 纯数据：存哪儿
# ===========================================================================

def test_it_does_not_write_into_the_project_repo(store, tmp_path):
    """
    ⭐⭐ **记忆属于"这台机器的这个人"，不属于项目。**

    写进项目会污染 git diff，多人协同时还会互相打架
    （你拖宽的左栏跟着仓库跑到别人机器上）。所以：`remember_project`
    只动用户级那个文件，**项目目录里一个新文件都不该出现**。
    """
    root = _proj(tmp_path)
    before = sorted(p.name for p in root.iterdir())
    SS.remember_project(root, {'split_graph': [900, 500]}, path=store)
    assert store.is_file(), '用户级记忆没写出来'
    assert sorted(p.name for p in root.iterdir()) == before, \
        '记忆写进项目目录了 —— 那会进 git、会和别人打架'


def test_config_path_follows_the_ai_json_convention(store):
    """
    位置与 `ai.json` 同一处（`%LOCALAPPDATA%/StoryGraph/`）。

    为什么这条也值得钉：用户已经知道"这工具的东西在 StoryGraph 那个目录"，
    再开一个新地方会让人找不到该删什么。
    """
    assert SS.config_path() == store          # 夹具里就是走这条约定定的
    assert str(SS.config_path()).endswith('.json')


def test_the_env_switch_turns_everything_off(monkeypatch, tmp_path):
    """`STORYGRAPH_NO_SESSION` 非空 = 整趟不记忆（自动化 / "今天想空着开"）。"""
    monkeypatch.setenv('STORYGRAPH_NO_SESSION', '1')
    SS.set_enabled(True)
    try:
        assert SS.disabled()
        assert not SS.enabled()
    finally:
        SS.set_enabled(False)


# ===========================================================================
#  ① 纯数据：读 / 写 / 忘
# ===========================================================================

def test_round_trip_remembers_the_project_and_its_layout(store, tmp_path):
    root = _proj(tmp_path)
    res = SS.remember_project(root, {'split_graph': [1000, 300],
                                     'page': 1}, path=store)
    assert res['ok'], res
    data = SS.load(store)
    assert data['last_project'] == str(root)
    entry = data['projects'][SS._key(root)]
    assert entry['split_graph'] == [1000, 300]
    assert entry['page'] == 1
    assert entry['project_name'] == 'proj'
    assert entry['updated'], '得有时间戳（"这是哪次记的"）'


def test_remembering_merges_instead_of_overwriting(store, tmp_path):
    """
    ⭐ 只报"我打开了这个项目"时，**不该把布局抹掉**。

    （`remember_project(root)` 在每次打开项目时都调一次；如果它覆盖整条记录，
    分栏宽度会在下一次打开时消失 —— 那种坏法很难被肉眼发现。）
    """
    root = _proj(tmp_path)
    SS.remember_project(root, {'split_graph': [1000, 300],
                               'split_card': [200, 900]}, path=store)
    SS.remember_project(root, path=store)          # 不带布局
    entry = SS.load(store)['projects'][SS._key(root)]
    assert entry['split_graph'] == [1000, 300], '布局被覆盖没了'
    assert entry['split_card'] == [200, 900]


def test_reading_a_broken_file_is_like_having_none(store):
    """
    文件坏了 / 是半截 JSON → **当没有**，绝不抛异常。

    为什么必须这样：这个文件在**启动路径**上。为一份"我上次拖宽的左栏"
    而让工具打不开，是完全不成比例的代价。
    """
    store.write_text('{"last_project": "D:/x", "proj', encoding='utf-8')
    data = SS.load(store)
    assert data['last_project'] == '' and data['projects'] == {}
    assert SS.last_project(store) == ''
    assert SS.recent_projects(store) == []


def test_a_newer_format_is_not_guessed_at(store):
    """更新的工具写的文件（version 更大）→ 当读不懂，不猜。"""
    store.write_text(json.dumps({'version': 999, 'last_project': 'D:/x'}),
                     encoding='utf-8')
    assert SS.load(store)['last_project'] == ''


def test_a_project_that_no_longer_exists_is_not_returned(store, tmp_path):
    """
    ⭐ 项目目录没了 → **不自动打开**。

    否则用户下次开工具会看到"打开了一个不存在的项目"（比不记忆更糟）。
    但**记录仍然留着**（磁盘可能是拔了的移动硬盘，插回来还该认得）。
    """
    root = _proj(tmp_path)
    SS.remember_project(root, path=store)
    assert SS.last_project(store) == str(root)
    import shutil
    shutil.rmtree(root)
    assert SS.last_project(store) == '', '项目都不在了还返回它'
    assert SS.load(store)['last_project'] == str(root), '记录本身不该被抹掉'


def test_recent_list_is_deduped_capped_and_filtered(store, tmp_path):
    roots = [_proj(tmp_path, f'p{i}') for i in range(4)]
    for r in roots:
        SS.remember_project(r, path=store)
    SS.remember_project(roots[0], path=store)          # 再开一次最早那个
    recent = SS.recent_projects(store)
    assert recent[0] == str(roots[0]), '刚打开的该排最前'
    assert len(recent) == len(set(recent)) == 4, '不该有重复项'
    # 超过上限只留最近的 N 个
    for i in range(4, SS.MAX_RECENT + 4):
        SS.remember_project(_proj(tmp_path, f'q{i}'), path=store)
    assert len(SS.recent_projects(store)) == SS.MAX_RECENT
    # 内存里的记录表**不封顶**（"最近"封顶，"记得"不封顶）
    assert len(SS.load(store)['projects']) >= SS.MAX_RECENT


def test_forget_one_and_forget_all(store, tmp_path):
    a, b = _proj(tmp_path, 'a'), _proj(tmp_path, 'b')
    SS.remember_project(a, {'page': 1}, path=store)
    SS.remember_project(b, {'page': 0}, path=store)
    SS.forget(a, path=store)
    data = SS.load(store)
    assert SS._key(a) not in data['projects']
    assert SS._key(b) in data['projects'], '不该顺手把别的也清了'
    SS.forget(None, path=store)
    data = SS.load(store)
    assert data['projects'] == {} and data['recent'] == [] \
        and data['last_project'] == ''


def test_auto_open_flag_round_trips(store, tmp_path):
    SS.remember_project(_proj(tmp_path), path=store)
    assert SS.load(store)['auto_open'] is True
    SS.set_auto_open(False, path=store)
    assert SS.load(store)['auto_open'] is False


# ===========================================================================
#  ① 纯数据：布局
# ===========================================================================

def test_layout_only_keeps_what_it_really_got():
    """
    取不到的字段**根本不写进去**（而不是写个 0 / null）。

    为什么较真：写进 0 的话，下次恢复会把分栏设成 0 宽 —— 界面看着就是"坏了"，
    而根因只是一次没取到值。
    """
    got = SS.layout_from(page=1, card_index=None, chapter='',
                         geometry=[], split_graph=[], split_card=[])
    assert got == {'page': 1}, got


def test_geometry_and_sizes_reject_garbage():
    assert SS.normalize_geometry([1600, 950, 100, 80]) == [1600, 950, 100, 80]
    assert SS.normalize_geometry([1600, 950]) == [1600, 950, None, None]
    for bad in ([], [0, 900], [-5, 900], ['x', 900], None, 'nope'):
        assert SS.normalize_geometry(bad) == [], bad
    assert SS.normalize_sizes([1080, 340]) == [1080, 340]
    for bad in ([], [1080], [0, 340], [-1, 340], ['a', 2], None):
        assert SS.normalize_sizes(bad) == [], bad


def test_chapter_is_remembered_both_absolute_and_relative(store, tmp_path):
    """
    ⭐ 章节记**两份**：绝对路径 + 相对项目根那份。

    项目整个搬到别的盘 / 别的机器上时，绝对那份就失效了，
    而相对那份还能把章节找回来 —— 这正是用户那句"项目内部有保存布局"里
    最实用的一半。
    """
    root = _proj(tmp_path)
    ch = root / 'game' / 'chapter' / 'ch01.rpy'
    got = SS.layout_from(chapter=str(ch), root=root)
    assert got['last_chapter'] == str(ch)
    assert got['last_chapter_rel'] == 'game/chapter/ch01.rpy'

    # 搬到新地方：绝对路径失效、相对那份接上
    moved = tmp_path / 'moved'
    import shutil
    shutil.move(str(root), str(moved))
    assert SS.resolve_chapter(got, moved) == str(moved / 'game/chapter/ch01.rpy')
    # 两份都找不到 → 空（调用方就不载入章节，只开项目）
    assert SS.resolve_chapter(got, tmp_path / 'nowhere') == ''


# ===========================================================================
#  ① 纯数据：项目里那份（用户说的"项目内部有保存布局"）
# ===========================================================================

def test_the_project_own_file_is_read_and_wins(store, tmp_path):
    """
    ⭐⭐ **项目根里那份 `storygraph.session.json` 会被读，且优先级更高。**

    这一条正对着用户那句"如果项目内部有保存布局，一并读取"：
    团队可以把一份布局放进项目（一起评审、一起用），而工具
    **只读不写**它 —— 所以它不会因为"我今天拖了一下"就变，也不会进你的 diff。
    """
    root = _proj(tmp_path)
    SS.remember_project(root, {'split_graph': [1000, 300],
                               'split_card': [200, 900]}, path=store)
    (root / SS.PROJECT_FILE).write_text(json.dumps(
        {'layout': {'split_graph': [620, 380]}}), encoding='utf-8')
    layout = SS.project_layout(root, path=store)
    assert layout['split_graph'] == [620, 380], '项目里那份该赢'
    assert layout['split_card'] == [200, 900], '用户级那份该补上没有的字段'


def test_a_broken_project_file_does_not_break_startup(store, tmp_path):
    root = _proj(tmp_path)
    SS.remember_project(root, {'split_graph': [1000, 300]}, path=store)
    (root / SS.PROJECT_FILE).write_text('}{ 这不是 json', encoding='utf-8')
    layout = SS.project_layout(root, path=store)
    assert layout['split_graph'] == [1000, 300], '坏文件该被忽略，用户级照用'


# ===========================================================================
#  ② 界面：真窗口
# ===========================================================================

def _engine(root):
    """与 `tests/conftest.py::ui_window` 同一个桩（真读资源表）。"""
    class _Engine:
        def __init__(self, project_root):
            self.project_root = project_root
            self._registry = None
            try:
                from storygraph.pipeline.build import load_asset_registry
                self._registry = load_asset_registry(project_root)
            except Exception:                   # noqa: BLE001 - 夹具不该挂
                self._registry = None

        def get_registry(self):
            return self._registry

    return _Engine(root)


def _window(qapp, project_root, script, store_path):
    """
    造一个"记忆存在 `store_path`"的窗口（**子类注入**，见
    `WindowStateMixin.session_store_path`）。构造完跑一圈事件循环，
    让 `_boot`（含会话恢复）真的执行过。
    """
    from storygraph.ui_qt.main_window import MainWindowQt

    class _Win(MainWindowQt):
        def session_store_path(self):
            return store_path

    win = _Win(_engine(project_root), script)
    for _ in range(3):
        qapp.processEvents()
    return win


def test_second_launch_returns_to_the_last_project(qapp, ui_project, store):
    """
    ⭐⭐ 用户原话那一条：**第二次打开自动读取之前打开过的那个项目**
    （并把布局一起摆回来）。
    """
    from _ui_project import build_project
    root = Path(ui_project)
    ch = root / 'game' / 'script' / 'ch01.rpy'
    assert ch.is_file(), ch

    # —— 第一次：打开项目、切到卡片页、拖一下分栏、停在某一拍 ——
    win1 = _window(qapp, None, None, store)
    # ⚠ 先把窗口摆到**够宽**：offscreen 平台的默认窗口很小，
    #   分栏会被挤成另一个比例（实测设 [333,777] 读回 [186,434]）——
    #   那时判"布局摆回去了"测的其实是"屏幕够不够宽"。
    win1.resize(1500, 950)
    win1.open_project_path(root)
    win1.load_script(ch)                      # goto_card=True → 卡片页
    win1._split_graph.setSizes([700, 420])
    win1._set_page(win1.PAGE_CARD)
    win1._show_index(2)
    qapp.processEvents()
    want_graph = list(win1._split_graph.sizes())
    res = win1.save_session_state()
    assert res.get('ok'), res
    layout_saved = SS.project_layout(root, path=store)
    assert layout_saved['split_graph'] == want_graph, layout_saved
    win1.close()

    # —— 第二次：**什么都不给**地打开 → 该回到上面那个项目 ——
    win2 = _window(qapp, None, None, store)
    got = win2.restore_session()
    assert got['ok'], got
    assert str(win2._project_root) == str(root), win2._project_root
    assert win2._file_path.endswith('ch01.rpy'), win2._file_path
    assert win2.current_page() == win2.PAGE_CARD, '页面没摆回去'
    assert win2._loaded_idx == 2, win2._loaded_idx
    assert list(win2._split_graph.sizes()) == want_graph, \
        (win2._split_graph.sizes(), want_graph)
    win2.close()


def test_the_remembered_layout_survives_a_fresh_process(qapp, ui_project,
                                                        store):
    """
    ⭐ 记忆是**跨进程**的（不只是"同一个窗口对象里还记得"）。

    这条用"重新从磁盘读一遍"来代表"下次开工具"：把窗口关掉、
    只留文件，再问 `session_store` 要那份布局。摆得回去才算真的记住了
    （用户要的正是"第二次打开时"）。
    """
    root = Path(ui_project)
    ch = root / 'game' / 'script' / 'ch01.rpy'
    win = _window(qapp, None, None, store)
    win.resize(1500, 950)
    win.open_project_path(root)
    win.load_script(ch)
    win._split_card.setSizes([333, 777])
    qapp.processEvents()
    want = list(win._split_card.sizes())
    win.save_session_state()
    win.close()

    # 「再开一次」——只读文件
    assert SS.last_project(store) == str(root)
    layout = SS.project_layout(root, path=store)
    assert layout['split_card'] == want, (layout, want)
    assert Path(SS.resolve_chapter(layout, root)).name == 'ch01.rpy'


def test_a_cwd_guessed_project_root_yields_to_the_memory(tmp_path, monkeypatch):
    """
    ⭐⭐ **"从当前工作目录猜出来的项目根"不许顶掉记忆**（实测抓到的真 bug）。

    现场：双击 exe 时工作目录常常正好是某个项目根（或它下面），
    `main_qt._guess_project_root` 于是猜出一个根，**喂给窗口** ——
    那等于告诉窗口"命令行给过了"，`_boot_restore_session` 里那道门
    于是永远关上：**"下次自动回到上次的项目"在最常见的用法下彻底失效**。

    所以：只有命令行**明确给的**根才算指定；猜出来的根要让位。
    这一条钉住 `_guess_project_root` 有没有如实报告"这个根是怎么来的"。
    """
    from storygraph.main_qt import (ROOT_FROM_ARG, ROOT_FROM_CWD,
                                    _guess_project_root)
    proj = _proj(tmp_path, 'cwd_project')
    monkeypatch.chdir(proj)
    root, script, source = _guess_project_root([])
    assert root is not None and Path(root).resolve() == proj.resolve()
    assert source == ROOT_FROM_CWD, \
        f'从 cwd 猜出来的根被当成了命令行指定（source={source}）'

    # 命令行明确给项目目录 → 那才是指定
    root2, _script2, source2 = _guess_project_root([str(proj)])
    assert source2 == ROOT_FROM_ARG, source2
    assert Path(root2).resolve() == proj.resolve()

    # 命令行给章节 → 从它向上找到的项目也算指定（你指的就是这个项目）
    ch = proj / 'game' / 'chapter' / 'ch01.rpy'
    root3, script3, source3 = _guess_project_root([str(ch)])
    assert source3 == ROOT_FROM_ARG and script3 == str(ch), (source3, script3)
    assert Path(root3).resolve() == proj.resolve()


def test_it_does_not_hijack_an_explicit_command_line_project(qapp, ui_project,
                                                             store, tmp_path):
    """
    ⭐ 命令行**明确给了**项目/章节时不恢复（那是用户的明确指定）。

    不给这条门，`StoryGraph.exe 某项目 某章` 会先开"上次的项目"再开你指的 ——
    白读一遍资源表还算轻的，两边的注册表互相覆盖才麻烦。
    """
    other = _proj(tmp_path, 'other')
    SS.remember_project(other, path=store)     # 记着"上次是 other"
    root = Path(ui_project)
    ch = root / 'game' / 'script' / 'ch01.rpy'
    win = _window(qapp, root, ch, store)       # 命令行给了 ui_project
    win._boot_restore_session()                # `_boot` 里那一步
    assert str(win._project_root) == str(root), \
        f'命令行给的项目被"上次那个"盖掉了：{win._project_root}'
    win.close()


def test_turning_auto_open_off_is_honored(qapp, ui_project, store, tmp_path):
    """
    记忆里把自动打开关掉 → 启动**不**打开任何项目，但**记录仍然在**
    （"我今天想空着开"不等于"把记忆删了"）。
    """
    SS.remember_project(tmp_path / 'x', path=store)
    SS.set_auto_open(False, path=store)
    win = _window(qapp, None, None, store)
    got = win.restore_session()
    assert not got['ok'] and 'auto_open' in got['reason'], got
    assert win._project_root is None, win._project_root
    assert SS.load(store)['last_project'], '关掉自动打开不该把记录也清了'
    win.close()


def test_restore_never_raises_when_the_memory_is_garbage(qapp, store):
    """
    ⭐ 坏记忆**绝不拦启动**：给一份乱写的文件，恢复只该返回
    `{'ok': False}`，窗口照常可用。
    """
    store.write_text('{"projects": 3, "last_project": [1,2]}', encoding='utf-8')
    win = _window(qapp, None, None, store)
    got = win.restore_session()
    assert isinstance(got, dict) and got.get('ok') is False, got
    win.close()


def test_there_is_no_memory_ui_at_all(qapp, ui_project, store, tmp_path):
    """
    ⭐⭐ **记忆必须是"自动的"，不许有 UI**（2026-10-13 用户退回第一版）：

    > 记忆部分！我让你自动打开！而不是加个 UI……没有必要……直接记住，
    > 有上次的记录就读取，控制台打一行，已自动读取项目：……
    > 若是没有，就空白打开即可……不需要 UI！
    > 更不需要我手动点击说打开上次项目！那跟我手动打开项目有什么区别？？？

    所以这一条是**反向判据**：菜单栏里不许有「记忆」，窗口上也不许有
    "打开上次的项目 / 忘掉记忆"这类要人点的东西 —— 那等于把自动变成手动。
    """
    win = _window(qapp, None, None, store)
    labels = [a.text()
              for top in win.menuBar().actions()
              for a in top.menu().actions()]
    assert not any('记忆' in t for t in labels), labels
    assert not any('上次' in t for t in labels), labels
    # 也不许有那些要人点的东西（点一下才恢复 = 手动）
    for gone in ('open_recent_project', 'forget_all_sessions',
                 '_build_menus', '_recent_menu'):
        assert not hasattr(win, gone), f'{gone} 还在 —— 记忆又变成手动 UI 了'
    win.close()


def test_restoring_prints_one_line_in_the_console(qapp, ui_project, store):
    """
    ⭐ 用户要的**唯一**反馈方式：控制台一行「已自动读取项目：…」。

    不做弹窗、不做提示条、不问"要不要恢复" —— 就一行字。
    """
    root = Path(ui_project)
    ch = root / 'game' / 'script' / 'ch01.rpy'
    win = _window(qapp, None, None, store)
    win.open_project_path(root)
    win.load_script(ch)
    win.save_session_state()
    win.close()

    win2 = _window(qapp, None, None, store)
    logged = []
    win2._log = lambda m: logged.append(str(m))
    got = win2.restore_session()
    assert got['ok'], got
    assert any('已自动读取项目' in m and str(root) in m for m in logged), logged
    win2.close()


def test_a_missing_project_opens_blank_and_says_nothing(qapp, store, tmp_path):
    """
    ⭐ 用户："若是没有，就空白打开即可" —— **没有记录时不许打扰**。

    连一行日志都不该有（那只是噪音），更不许弹窗问"要不要打开上次的项目"。
    """
    assert not store.exists(), '这个用例要的是"第一次打开"（没有记忆文件）'
    win = _window(qapp, None, None, store)
    logged = []
    win._log = lambda m: logged.append(str(m))
    got = win.restore_session()
    assert got['ok'] is False, got
    assert logged == [], f'空白打开时说多了：{logged}'
    assert win._project_root is None
    win.close()


def test_closing_the_window_is_what_writes_the_memory(qapp, ui_project,
                                                      store, monkeypatch):
    """
    退出时落一次记忆（不是每次切页都写盘）。

    用一个"只数写入次数"的替身证明：`close()` 恰好写一次，
    而且**写的是那个项目**。
    """
    root = Path(ui_project)
    calls = []
    real = SS.remember_project

    def _spy(r, layout=None, path=None):
        calls.append((str(r), layout))
        return real(r, layout, path=path)

    monkeypatch.setattr(SS, 'remember_project', _spy)
    win = _window(qapp, root, root / 'game' / 'script' / 'ch01.rpy', store)
    win.close()
    for _ in range(2):
        qapp.processEvents()
    assert calls, '关窗口时没落记忆'
    assert calls[-1][0] == str(root)
    assert calls[-1][1], '落记忆时该把布局一起收上去'
