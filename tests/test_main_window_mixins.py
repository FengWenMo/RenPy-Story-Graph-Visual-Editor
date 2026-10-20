"""
`MainWindowQt` 拆分守卫（P3-14，2026-10-05）
==========================================

`tests/test_main_window_snapshot.py` 守的是**行为**（63 步操作序列）。
这一批守的是**结构** —— 那三类"行为测试在多数时候抓不到、一旦坏掉却
很难往回追"的拆分事故：

  1. **基类顺序**（最阴的一类）。
     `MainWindowQt` 拆成"入口 + 5 个 mixin + `QMainWindow`"。
     如果 mixin 排在 `QMainWindow` **后面**，方法**还在**（`hasattr` 为真、
     `MRO` 也合法），但 `QObject` 自己定义的**虚函数**会抢先 ——
     `eventFilter` / `closeEvent` 就被静默盖掉：

         实测（2026-10-05，拆完第一次跑）：
         13 条键盘用例全红，而 `hasattr(MainWindowQt, 'eventFilter')` 是 True。
         报错信息是"空格键没被跟踪"，跟"基类顺序"隔着十万八千里。

  2. **同名成员**（静默覆盖）。
     mixin 里有个 `export_script`、主类里也有一个 → 子类那份赢，
     mixin 的变成**死代码**。方法测试可能照样绿（测的是赢的那个），
     所以只能靠结构检查。

  3. **`REQUIRES` 不许撒谎**（双向）。
     每个 mixin 声明"我要宿主提供哪些成员"。声明的必须真有；
     而**真正读到的**也必须声明 —— 后者是事实、前者是声明，
     只查一边等于没查（同 `test_editor_session_mixin_contract.py` 的思路）。

  4. **不许有成员漏在"三不管"地带**。
     每个成员要么在某个 mixin 里、要么在 `KEEP` 白名单里（附理由）。
     拆完剩一个方法在主类里没人认领，就是"下次没人敢动它"的开始。

为什么用 `ast` 而不是 `getattr`：这些判据要的是**源码归属**，
而 `MainWindowQt.foo` 无论 foo 住在哪个文件都取得到 ——
`getattr` 恰好分不出"住在哪"，那正是这里要守的东西。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / 'storygraph'
UI = PKG / 'ui_qt'

#: `文件 → mixin 类名`。**加一块就加一行**（`_source_paths` 里也有一份，
#: 见 `WINDOW_MIXIN_MODULES` —— 两处都改，这条守卫会提醒你）。
BLOCKS = {
    'window_graph_ops': 'GraphOpsMixin',
    'window_card': 'CardPageMixin',
    'window_nodes': 'NodeEditMixin',
    'window_export': 'WindowExportMixin',
    'window_assets': 'WindowAssetsMixin',
    'window_keyboard': 'WindowKeyboardMixin',
    'window_state': 'WindowStateMixin',
}

#: 主类**刻意留下**的成员（键是名字，值是"为什么留"）。
#: 这份清单要**两边对齐**：`tools/_extract_window.py` 里的 `KEEP` 是
#: 拆分时用的同一份理由，改了这里也要改那里。
KEEP = {
    '__init__': 'Qt super().__init__() 必须在这里链到 QMainWindow',
    '_registry_provider': '构造 EditorSession 时就要传',
    '_ensure_registry': '上面那个的配套',
    'beats': '属性：会话拍的快捷读法',
    '_loaded_idx': '属性',
    '_file_path': '属性',
    '_symbols': '属性',
    'current_page': '属性',
    '_boot': '启动时序（QTimer 调它）',
    '_set_graph': '图的所有权',
    '_build': '搭界面骨架',
    '_build_graph_page': '第 0 页构建',
    '_build_card_page': '第 1 页构建',
    '_build_toolbars': '两组工具栏',
    '_boot_restore_session': '启动时按记忆恢复（并在命令行给了参数时不恢复）',
    '_set_page': '页切换',
    '_focus_into_card_page': '页切换配套',
    '_sync_action_states': '全局动作可用性',
    '_update_crumb': '面包屑',
    '_beat_lines_hint': '面包屑的一部分：这一拍在产物里占几行',
    '_back_to_graph': '回结构页',
    '_open_selected_in_card': '页切换 + 载入',
    '_on_zoom_changed': '画布缩放 → 状态栏',
    '_log': '控制台',
    'clear_console': '控制台',
    '_swallow_mark': '静默失败标记',
    '_report_swallowed': '上面那个的配套',
    '_is_text_widget': '事件过滤器的界面探针',
    '_text_input_has_focus': '同上',
    '_record_applied': '一次编辑的落账',
    '_status': '状态栏',
    'closeEvent': 'Qt 重写点',
    'PAGE_GRAPH': '类常量',
    'PAGE_CARD': '类常量',
    'EMPTY_HINT': '类常量',
    'ASSET_FIELD_KINDS': '类常量：字段→资产种类',
    'PREFETCH_AHEAD': '类常量：往后预取几拍（卡片块读）',
    'PREFETCH_BEHIND': '类常量：往前预取几拍（卡片块读）',
}

#: Qt 自己就定义了这些**虚函数** —— mixin 想覆盖它们就必须排在 Qt 类**前面**。
QT_VIRTUALS = {'eventFilter', 'closeEvent', 'paintEvent', 'keyPressEvent',
               'resizeEvent', 'showEvent', 'hideEvent', 'changeEvent'}

#: **Qt（`QMainWindow`/`QWidget`/`QObject`）自己提供的**成员。
#:
#: 为什么两端都要这一份：生成器（`tools/_extract_window.py` 的 `QT_MEMBERS`）
#: 用它在造 `REQUIRES` 时排掉 `self.statusBar()` / `self.focusWidget()` 这类
#: 调用；守卫用它在**查** `REQUIRES` 时同样排掉 —— 否则一查就报
#: "宿主根本没有 `focusWidget`"（第一版实测 6 个 mixin 全红）。
#:
#: 这是**故意重复**（不是漏了 import 生成器）：守卫要能在
#: 没装 PySide6 的机器上跑，也不能依赖一个 `tools/` 下的私有脚本。
#: 下面 `test_qt_members_are_real_qt_members` 会**真的去问 Qt**
#: 这份清单里的名字存不存在 —— 重复由此变成"互相校验"，而不是"两份会漂移"。
QT_MEMBERS = {
    # 窗口 / 控件
    'statusBar', 'setWindowTitle', 'windowTitle', 'resize', 'close',
    'setCentralWidget', 'centralWidget', 'addToolBar', 'addDockWidget',
    'menuBar', 'setStatusBar', 'setWindowIcon', 'setMinimumSize',
    'setAcceptDrops', 'setMouseTracking', 'rect', 'size', 'width', 'height',
    'parent', 'parentWidget', 'window', 'geometry', 'pos',
    'mapToGlobal', 'mapFromGlobal', 'mapTo', 'mapFrom',
    # 事件 / 过滤器
    'installEventFilter', 'removeEventFilter', 'focusWidget',
    'isAncestorOf', 'setFocus', 'hasFocus', 'update', 'repaint', 'setEnabled',
    'isEnabled', 'setVisible', 'isVisible', 'show', 'hide', 'setParent',
    'findChild', 'findChildren', 'setShortcut', 'children', 'deleteLater',
    'setAttribute', 'setCursor', 'setToolTip', 'setWhatsThis',
    # 控件树操作
    'setLayout', 'layout',
}


def _class_def(path: Path, cls_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            return node
    raise AssertionError(f'{path.name} 里找不到 {cls_name}')


def _assign_names(node) -> list:
    """
    一条赋值的**所有**目标名，含元组解包。

    `PREFETCH_AHEAD, PREFETCH_BEHIND = 2, 1` 是类体里真实存在的一条，
    目标是 `ast.Tuple` 不是 `ast.Name`。第一版只认 `ast.Name` ——
    于是这两个类常量对**守卫**也是隐形的：`test_keep_list_has_no_stale_names`
    会报"KEEP 里的名字已经不在主类了"（其实在），
    而更早那版**生成器**犯的是同一个错，后果更重：它把这两个常量
    从主类里删掉了，搬到别处的 `self.PREFETCH_AHEAD` 会在运行时炸。
    同一个疏漏在两个地方各咬一次，所以这里和生成器都要认元组。
    """
    out = []
    for tg in node.targets:
        if isinstance(tg, ast.Name):
            out.append(tg.id)
        elif isinstance(tg, (ast.Tuple, ast.List)):
            out.extend(e.id for e in tg.elts if isinstance(e, ast.Name))
    return out


def _members(cls: ast.ClassDef) -> set:
    out = set()
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(item.name)
        elif isinstance(item, ast.Assign):
            out |= set(_assign_names(item))
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target,
                                                            ast.Name):
            out.add(item.target.id)
    return out


def _requires(cls: ast.ClassDef) -> set:
    for item in cls.body:
        if isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name) and t.id == 'REQUIRES':
                    try:
                        return set(ast.literal_eval(item.value))
                    except ValueError:
                        return set()
    return set()


def _self_attrs(cls: ast.ClassDef) -> tuple:
    """(`self.X` 读集合, 写集合)。读才是依赖，写是造状态。"""
    loads, stores = set(), set()
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == 'self'):
            (loads if isinstance(node.ctx, ast.Load) else stores).add(node.attr)
    return loads, stores


def _instance_attrs(*classes) -> set:
    """
    这些类里**被赋值过**的 `self.X`（= 实例属性）。

    必须算进"宿主有没有"：`REQUIRES` 里绝大多数其实**不是类成员**，
    而是 `__init__`（或别处）里 `self._canvas = …` 造出来的**实例状态**。
    只查类成员会得出"宿主根本没有 `_canvas`"的荒谬结论 ——
    这个守卫第一版就是这么错的（6 个 mixin 全红）。
    """
    out = set()
    for cls in classes:
        if cls is None:
            continue
        _loads, stores = _self_attrs(cls)
        out |= stores
    return out


def _read_deps(cls: ast.ClassDef) -> set:
    """
    本 mixin **真正读**的 `self.X`，排掉自己提供的和 Qt 自己提供的。

    排 Qt 那几个是必须的：`self.focusWidget()` / `self.rect()` 读的是
    Qt 的方法，不是宿主依赖 —— 不排掉就会报"宿主根本没有 `focusWidget`"。
    """
    loads, _stores = _self_attrs(cls)
    return loads - _members(cls) - QT_MEMBERS


def _host_members(exclude: str = '') -> set:
    """
    宿主能提供的**全部**成员：类成员 + 实例属性 + 前一个 mixin 的方法。

    `exclude` 是"要检查的那个 mixin 的 key" —— 它自己的成员不算宿主提供的
    （自己满足自己不算依赖，那样 `REQUIRES` 就失去意义了）。
    """
    mw = _main_cls()
    out = _members(mw) | _instance_attrs(mw)
    for k, c in BLOCKS.items():
        if k == exclude:
            continue
        cls = _class_def(UI / f'{k}.py', c)
        out |= _members(cls) | _instance_attrs(cls)
    return out


def _main_cls() -> ast.ClassDef:
    return _class_def(UI / 'main_window.py', 'MainWindowQt')


@pytest.fixture(scope='module')
def blocks():
    return {k: _class_def(UI / f'{k}.py', v) for k, v in BLOCKS.items()}


class TestBaseOrderAndWiring:

    def test_qt_class_is_last_base(self):
        """
        **五个 mixin 必须排在 `QMainWindow` 前面** —— 这条一破，
        `eventFilter` / `closeEvent` 会被 `QObject` 抢先（实测 13 条键盘用例红）。
        """
        bases = [ast.unparse(b) for b in _main_cls().bases]
        assert 'QMainWindow' in bases, bases
        idx = bases.index('QMainWindow')
        assert idx == len(bases) - 1, (
            f'`QMainWindow` 必须是**最后一个**基类（现在在第 {idx+1}/'
            f'{len(bases)} 位）：{bases}\n'
            f'它后面的 mixin 里那些 Qt 虚函数会被 `QObject` 抢先，'
            f'方法还在、但永远不会被调用。')

    def test_all_mixins_are_bases(self):
        bases = {ast.unparse(b) for b in _main_cls().bases}
        for fname, cls in BLOCKS.items():
            assert cls in bases, f'{cls}（{fname}.py）没写进基类列表：{bases}'

    def test_qt_virtuals_live_in_the_main_class_or_earlier_mixins(self):
        """
        Qt 虚函数**要么在主类、要么在基类列表里排在 `QMainWindow` 之前的
        mixin 里**。

        判据用"基类顺序"而不是运行时 MRO：源码层面就看得出来，
        不依赖真起 Qt（这个文件要能在没装 PySide6 的机器上跑）。
        """
        mw = _main_cls()
        own = _members(mw)
        bases = [ast.unparse(b) for b in mw.bases]
        qt_idx = bases.index('QMainWindow')
        earlier = set()
        for fname, cls in BLOCKS.items():
            if cls in bases and bases.index(cls) < qt_idx:
                earlier |= _members(_class_def(UI / f'{fname}.py', cls))
        overridden = {
            name for name in QT_VIRTUALS
            if any(name in _members(_class_def(UI / f'{f}.py', c))
                   for f, c in BLOCKS.items() if c in bases)
        }
        bad = sorted(overridden - own - earlier)
        assert not bad, (
            f'这些 Qt 虚函数被会排在 `QMainWindow` 后面的 mixin 覆盖，'
            f'永远不会被调用：{bad}')


class TestNoDuplicateMembers:

    def test_mixin_members_do_not_collide_with_each_other(self, blocks):
        """
        mixin 之间**方法/类属性**不许重名（`REQUIRES` 除外，那是约定字段）。

        为什么只查方法/类属性、不查实例属性（`self.X = …`）：
        **实例属性跨块共享是这个拆分刻意的设计** ——
        `self._session` 由主类造、好几块都在用；`self._canvas` 由
        主类造、节点块与键盘块都用。那正是 `REQUIRES` 要登记的东西，
        不是冲突。（真把它们算冲突，就有 6 个 mixin 全红。）
        """
        seen, dup = {}, []
        for fname, cls in BLOCKS.items():
            for name in _members(blocks[fname]) - {'REQUIRES'}:
                if name in seen:
                    dup.append((name, seen[name], cls))
                seen[name] = cls
        assert not dup, f'mixin 之间成员重名（会静默覆盖）：{dup}'

    def test_mixin_members_are_not_redefined_in_main_class(self, blocks):
        """
        主类里**不许**再出现 mixin 的同名成员。

        这是"静默覆盖"最危险的方向：主类排在 MRO 最前，
        它写一份同名方法就把 mixin 那份变成死代码 ——
        而且**测试照样绿**（跑的是主类那份），直到有人改了 mixin 却发现
        "改了没用"。
        """
        own = _members(_main_cls())
        dup = []
        for fname, cls in BLOCKS.items():
            for name in _members(blocks[fname]) & own:
                dup.append((name, cls))
        assert not dup, (
            f'主类里重复定义了 mixin 的成员（mixin 那份会变成死代码）：{dup}')


class TestRequiresIsHonest:

    @pytest.mark.parametrize('fname', sorted(BLOCKS))
    def test_declared_requires_exist_on_the_host(self, fname, blocks):
        """声明的必须真有（类成员**或**实例属性，Qt 自己的除外）。"""
        declared = _requires(blocks[fname]) - QT_MEMBERS
        missing = sorted(declared - _host_members(fname))
        assert not missing, (
            f'{BLOCKS[fname]}.REQUIRES 里有宿主根本没有的成员：{missing}\n'
            f'（类成员和 `self.X = …` 造出来的实例属性都算"有"）')

    @pytest.mark.parametrize('fname', sorted(BLOCKS))
    def test_read_dependencies_are_declared(self, fname, blocks):
        """
        代码里读到的 `self.X`（且不是本 mixin 自己的、也不是 Qt 的）
        **必须**在 `REQUIRES` 里。

        这是上一批（`EditorSession`）守卫**没有**的那个方向，
        也是当年 `ExportMixin` 漏掉 `_transforms_decision` 的那一类。
        """
        undeclared = sorted(_read_deps(blocks[fname]) - _requires(blocks[fname]))
        assert not undeclared, (
            f'{BLOCKS[fname]} 读到了这些宿主成员却没写进 REQUIRES：'
            f'{undeclared}\n'
            f'（现在能跑只是因为宿主恰好有它们 —— '
            f'宿主一改初始化顺序就会在运行时炸）')


class TestCoverageAndShape:

    def test_every_member_is_claimed(self, blocks):
        """
        每个成员要么在 mixin 里、要么在 `KEEP` 里（附理由）。

        拆完剩一个方法在主类里"没人认领"，就是下次没人敢动它的开始。
        """
        all_mixin = set()
        for fname in BLOCKS:
            all_mixin |= _members(blocks[fname])
        own = _members(_main_cls())
        unclaimed = sorted(own - all_mixin - set(KEEP))
        assert not unclaimed, (
            f'主类里这些成员既不在任何 mixin、也不在 KEEP 白名单里：'
            f'{unclaimed}\n'
            f'要么搬走，要么写进 KEEP 并说明为什么留。')

    def test_keep_list_has_no_stale_names(self, blocks):
        """`KEEP` 不许留已经搬走的名字（清单过期会让人误判）。"""
        own = _members(_main_cls())
        stale = sorted(set(KEEP) - own)
        assert not stale, f'KEEP 里这些名字已经不在主类了：{stale}'

    def test_main_class_shrank_a_lot(self):
        """主类里"自己定义的方法"要少（否则等于没拆）。"""
        own = _members(_main_cls())
        methods = {n.name for n in ast.walk(_main_cls())
                   if isinstance(n, ast.FunctionDef)}
        assert len(methods) <= 35, (
            f'主类还有 {len(methods)} 个方法（拆分后应 ≤ 35；'
            f'起点是 120）—— 是不是又往主类里塞东西了？')
        _ = own

    def test_each_mixin_has_a_module_docstring(self, blocks):
        """每块都要说清"我负责什么"（不然半年后没人敢动）。"""
        for fname in BLOCKS:
            tree = ast.parse((UI / f'{fname}.py').read_text(encoding='utf-8'))
            doc = ast.get_docstring(tree)
            assert doc and len(doc) > 60, (
                f'{fname}.py 的模块 docstring 太短或没有：{doc!r}')


class TestQtMembersListIsHonest:
    """
    `QT_MEMBERS` 是一份**手写**的"Qt 自己提供的成员"清单，两端都在用
    （生成器造 `REQUIRES` 时排掉、守卫查 `REQUIRES` 时排掉）。
    手写清单会漂 —— 所以这里**真的去问 Qt**：这些名字在
    `QMainWindow`/`QWidget`/`QObject` 上到底存不存在。

    这样"重复两份"就变成了互相校验：清单里混进一个不存在的名字，
    或者 Qt 哪天改了名字，这条会红。
    """

    def test_qt_members_are_real_qt_members(self):
        pytest.importorskip('PySide6')
        from PySide6.QtCore import QObject
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import (QComboBox, QDialog, QListWidget,
                                       QMainWindow, QPlainTextEdit, QSplitter,
                                       QStackedWidget, QToolBar, QWidget)

        real = set()
        # **要问真正的那些类**：`setWidget`/`widget` 是 `QStackedWidget` 的
        # （不是 `QWidget` 的）、`setShortcut` 是 `QAction` 的 ——
        # 只问 QObject/QWidget/QMainWindow 会误报"清单里有假名字"。
        for cls in (QObject, QWidget, QMainWindow, QAction, QComboBox, QDialog,
                    QListWidget, QPlainTextEdit, QSplitter, QStackedWidget,
                    QToolBar):
            real |= {n for n in dir(cls) if not n.startswith('__')}
        fake = sorted(QT_MEMBERS - real)
        assert not fake, (
            f'QT_MEMBERS 里这些名字在 Qt 类上**不存在**：{fake}\n'
            f'（这份清单是用来"排掉 Qt 自己的成员"的，写错了会把真正的'
            f'宿主依赖也一起排掉 —— 那正是这个守卫要防的）')

    def test_no_project_member_is_mistaken_for_qt(self, blocks):
        """
        反过来：**本项目的成员不许被误判成 Qt 的**。

        判错方向更毒：把一个真依赖排掉，`REQUIRES` 就少一项，
        而守卫也一起看不见 —— 两边同时瞎，等于没查。
        变量名与 Qt 同名的概率很低，但这条能把它钉死。
        """
        project = set(KEEP) | set()
        for fname in BLOCKS:
            project |= _members(blocks[fname])
            project |= _self_attrs(blocks[fname])[1]      # 实例属性
        clash = sorted(project & QT_MEMBERS)
        assert not clash, (
            f'这些名字既像本项目的成员、又在 QT_MEMBERS 里：{clash}\n'
            f'（会让两边的判据同时失效，必须改名或确认归属）')
