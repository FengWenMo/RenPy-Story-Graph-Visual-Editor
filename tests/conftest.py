"""
tests/conftest.py
=================
pytest 全局夹具。

职责：
  · 把项目根目录加入 sys.path（源码是 `storygraph` 包，放在项目根下）
  · 提供 `notify_calls`：后端通知端口的捕获夹具（断言后端"报了什么"）
  · 提供写 .rpy 临时文件的工厂 fixture

历史（2026-10-01）
------------------
这里曾经有 80 行 **tkinter 桩**。它存在的原因值得记下来：后端
（`graph.commands` / `pipeline.renpy_exporter` / `graph.editor_utils`）
为了弹一个"没有可撤销的操作"而 `import tkinter.messagebox`，
于是无头环境下**连 import 都过不去**，只能在测试里造假模块兜着。

现在后端只走 `storygraph.app.notify`（只汇报事实、不弹窗），
`storygraph/ui/` 整个包也已删除 —— 桩没有存在意义了。**故意不留**
"以防万一"的桩：真有人再往核心里塞 UI 依赖，这里就该当场炸。
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# 测试自带的辅助模块（`_ui_project` / `_source_paths`）也按模块名 import，
# 所以 tests/ 自身也要在 sys.path 上。
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


# ------------------------------------------------------------------
#  会话记忆的隔离（**autouse**：没有它，跑测试会改你真实的最近项目表）
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_real_session_memory():
    """
    跑测试时**不许**读写真实的 `%LOCALAPPDATA%/StoryGraph/session.json`。

    为什么必须 autouse（不是"想要就加"）：窗口在 `closeEvent` 里落一次记忆，
    而测试会造**几百个**临时项目当窗口（`tmp_path` 每次都不一样）——
    没有这道闸，跑一次全量测试就会把你真实的"最近打开的项目"灌满
    临时目录，还会把你自己的项目挤出列表（实测：`ui_window` 夹具一 close
    就写一次）。这类污染**当时不报错**，只在你下次开工具时突然发现"记忆坏了"。

    ⚠ 关的是"自动写"这件事本身（`session_store.set_enabled(False)`），
    **不是**换个路径假装隔离 —— 要测记忆的用例自己用
    `tests/test_session_memory.py` 里的 `store` 夹具（它给一个显式的临时文件），
    并在需要时显式 `set_enabled(True)`。这样"测记忆的用例"和
    "别的用例别碰记忆"两件事互不干扰。
    """
    from storygraph.core import session_store as SS

    before = SS.enabled()
    SS.set_enabled(False)
    try:
        yield
    finally:
        SS.set_enabled(before)


# ------------------------------------------------------------------
#  夹具
# ------------------------------------------------------------------

@pytest.fixture
def write_rpy(tmp_path):
    """写 .rpy 文件，返回 Path。"""
    counter = {'n': 0}

    def _write(content: str, name: str = None):
        if name is None:
            counter['n'] += 1
            name = f'script{counter["n"]}.rpy'
        fp = tmp_path / name
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding='utf-8')
        return fp

    return _write


class NotifyCalls:
    """
    后端通知端口的捕获器（见 `storygraph.app.notify`）。

    后端现在**只汇报事实**，不弹窗；这个夹具就是"假装自己是前端"，
    把通知按级别收集起来供断言。这样测的是**语义**（后端报了什么），
    而不是**显示方式**（tkinter 还是 Qt 弹的窗）。
    """

    def __init__(self):
        self.calls = []                  # [(level, message, title)]

    def __call__(self, level, message, title=''):
        self.calls.append((level, message, title))

    # ---- 便捷查询 ----
    def of(self, level):
        return [(m, t) for lv, m, t in self.calls if lv == level]

    def messages(self, level=None):
        return [m for lv, m, _ in self.calls if level is None or lv == level]

    def clear(self):
        self.calls.clear()

    def __len__(self):
        return len(self.calls)

    def __bool__(self):
        return bool(self.calls)


@pytest.fixture
def notify_calls():
    """
    订阅后端通知端口，返回捕获器；用例结束自动退订。

        def test_x(notify_calls):
            do_something()
            assert notify_calls.of('error')
    """
    from storygraph.app import notify

    catcher = NotifyCalls()
    unsubscribe = notify.subscribe(catcher)
    try:
        yield catcher
    finally:
        unsubscribe()


@pytest.fixture
def asset_env(tmp_path):
    """
    构造一个最小资产项目布局：

    project/
      game/
        images/bg/*.png
        audio/*.ogg
        script.rpy
      asset_tables/

    返回 dict(project_root, asset_dir, game_dir)。
    """
    from storygraph.assets.asset_registry import write_asset_excel

    project = tmp_path / 'project'
    game = project / 'game'
    images = game / 'images' / 'bg'
    audio = game / 'audio'
    asset_dir = tmp_path / 'asset_tables'

    images.mkdir(parents=True)
    audio.mkdir(parents=True)
    asset_dir.mkdir(parents=True)

    for name in ('bg_classroom.png', 'bg_street.png'):
        (images / name).write_bytes(b'\x89PNG\r\n\x1a\n')
    (audio / 'bgm_theme.ogg').write_bytes(b'OggS')
    (audio / 'sfx_click.ogg').write_bytes(b'OggS')

    (game / 'script.rpy').write_text(
        'define e = Character("Eileen", color="#c8ffc8")\n'
        'label start:\n'
        '    scene bg classroom\n'
        '    play music bgm_theme\n'
        '    show bg street at leftish\n'
        '    e "Hello."\n'
        '    jump ending\n'
        'label ending:\n'
        '    return\n',
        encoding='utf-8',
    )

    # 预置一张图片表，避免 AssetEngine 自动全盘扫描
    write_asset_excel(
        {
            'images': [
                {'ID': 'bg_classroom', 'Path': 'game/images/bg/bg_classroom.png',
                 'Tag': 'bg', 'Attributes': 'bg_classroom',
                 'Category': 'background', 'Notes': ''},
                {'ID': 'bg_street', 'Path': 'game/images/bg/bg_street.png',
                 'Tag': 'bg', 'Attributes': 'bg_street',
                 'Category': 'background', 'Notes': ''},
            ],
            'audio': {'music': [
                {'ID': 'bgm_theme', 'Path': 'game/audio/bgm_theme.ogg',
                 'Loop': True, 'Volume': 1.0, 'Notes': ''},
            ], 'sound': [
                {'ID': 'sfx_click', 'Path': 'game/audio/sfx_click.ogg',
                 'Notes': ''},
            ]},
        },
        asset_dir,
    )

    return {
        'project_root': project,
        'asset_dir': asset_dir,
        'game_dir': game,
    }


# ------------------------------------------------------------------
#  Qt 前端夹具（offscreen 跑真 Qt —— tkinter 已删除，这是唯一前端）
# ------------------------------------------------------------------

@pytest.fixture(scope='session')
def qapp():
    """
    一个 `QApplication`（session 级：Qt 只允许有一个）。

    没有 PySide6 就 skip，而不是让整个测试会话崩掉。
    """
    pytest.importorskip('PySide6')
    import os

    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    # 不定字体目录的话，中文/emoji 会渲染成方框（截图看起来像坏了）
    os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def ui_project(tmp_path):
    """
    现场生成的 Ren'Py 工程（4 个 label / 11 拍 / 真 PNG）。

    **可移植**：不再依赖作者机器上的绝对路径 —— 换机器 / 上 CI 会真的跑
    （历史上 Qt 测试因为写死路径而在别的机器上静默 skip）。
    """
    from _ui_project import build_project

    return build_project(tmp_path / 'proj')


@pytest.fixture
def ui_window(qapp, ui_project):
    """
    载入上面那一章的主窗口（`storygraph.ui_qt.MainWindowQt`）。

    engine 桩**真的加载资源表**（并合并脚本里的 `define ... = Character`）——
    否则 `registry` 恒为 None，"参与角色 / 资产选择器 / 缩略图路径"这些
    功能全部空转，测试就变成了"测一个空壳"（本夹具改造前正是如此）。
    """
    from storygraph.ui_qt.main_window import MainWindowQt

    script = ui_project / 'game' / 'script' / 'ch01.rpy'

    class _Engine:
        def __init__(self, root):
            self.project_root = root
            self._registry = None
            self._load()

        def _load(self):
            try:
                from storygraph.pipeline.build import load_asset_registry
                self._registry = load_asset_registry(self.project_root)
            except Exception:                    # noqa: BLE001 - 夹具不该挂
                self._registry = None
            if self._registry is None:
                return
            try:
                from storygraph.parsing.renpy_parser import (
                    IncrementalRenPyAnalyzer)
                from storygraph.parsing.discover import discover_scripts
                from storygraph.story.beat_build import apply_script_definitions
                analyzer = IncrementalRenPyAnalyzer()
                pr, _graph = analyzer.analyze(
                    files=discover_scripts(self.project_root), graph=None)
                apply_script_definitions(self._registry, pr)
            except Exception:                    # noqa: BLE001
                pass

        def get_registry(self):
            return self._registry

    win = MainWindowQt(_Engine(ui_project), script)
    yield win
    win.close()


# ======================================================================
#  没有 PySide6 时的收集豁免（2026-10-21，CI 三个版本全红）
# ======================================================================

#: 会拉起 Qt 的 import 行（只看行首，不看注释/字符串里提到）。
_QT_IMPORT_RE = __import__('re').compile(
    r'^\s*(?:from\s+PySide6\b|import\s+PySide6\b'
    r'|from\s+storygraph\.ui_qt\b|import\s+storygraph\.ui_qt\b)',
    __import__('re').M)


def pytest_ignore_collect(collection_path, config):
    """
    没装 PySide6 时，**整份跳过**"import 了 Qt"的测试文件。

    为什么需要（2026-10-21，GitHub Actions 三个 Python 版本全红）：
    CI 只装 `openpyxl` + `Pillow`（不装 PySide6 —— Linux 上还要补
    libegl / libxkbcommon 等系统库，多一个失败点）。可二十多个测试文件
    在 **模块顶层**就 `from storygraph.ui_qt… import …`，于是**收集阶段**
    直接 ModuleNotFoundError：一条测试都跑不了。

    各文件里写的 `pytest.importorskip('PySide6')` 救不了这种情况 ——
    它在模块体里，收集还没走到那一步。这条 hook 在**收集之前**按文件内容
    判一次，谁忘写 importorskip 都不影响。

    本机（装了 PySide6）走第一个分支直接返回 None，行为完全不变。
    """
    try:
        import PySide6  # noqa: F401
        return None
    except ImportError:
        pass
    p = Path(str(collection_path))
    if p.suffix != '.py' or not p.is_file():
        return None
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    return True if _QT_IMPORT_RE.search(text) else None
