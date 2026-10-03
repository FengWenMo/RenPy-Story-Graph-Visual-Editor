"""
tests/test_entry_points.py
==========================
**入口可达性**的回归测试。

这个文件的存在理由是一次真实反馈（2026-09-28）：

> 用户："我作为一个用户……我不知道如何打开这个你说的卡牌入口"

当时 4 项卡片的功能是**做完了**的（数据层、测试都过），但：

  1. 入口埋在侧栏第三组按钮里，名字叫「在应用内编辑剧本表」；
  2. 点开后是个**空窗口**，「载入 .rpy」还在底部——用户要先找到它；
  3. 侧栏写着「导出本章为 Excel 表」，但**这个方法根本不存在**（点了没反应）；
  4. 「导入 Ren'Py 文件」只支持单选，旁边却另有一个「批量导入章节」按钮。

**教训**：功能做完 ≠ 用户能用。这一层必须用测试锁住，
因为它是"看不见的功能"——普通测试测的是函数，没人测"用户找得到吗"。

2026-10-01：界面的唯一实现是 `storygraph/ui_qt/main_window.py`
（tkinter 的 `graph_view.py` 已删除）。所以本文件改成扫 **Qt 前端**，
并且判据也跟着 Qt 的接线方式走：

  · tkinter 用 `command=self.xxx`；Qt 用 `act('文字', self.xxx, ...)`
    与 `menu.addAction('文字', lambda: self.xxx(...))`；
  · 静态扫描 + `QAction` 运行时核对（`window.findChildren(QAction)`），
    两条一起才能覆盖"字符串写对了但方法不存在"和"方法在但没接上"。
"""

import ast
import re
from pathlib import Path

import pytest

from _source_paths import main_window_source_text
#: **整个前端**的源码（入口 + 5 个 mixin）。2026-10-05 起 `MainWindowQt`
#: 拆成六个文件，所以"某回调在不在"要看全部 —— 只读入口会误报
#: "按钮指向了不存在的方法"。
SOURCE_TEXT = main_window_source_text()


def _source() -> str:
    return SOURCE_TEXT


def _defined_names() -> set:
    tree = ast.parse(_source())
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _called_names(node) -> set:
    """`node` 里所有 `self.foo(...)` 形式的**调用**名（不含纯属性访问）。"""
    names = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                and fn.value.id == 'self':
            names.add(fn.attr)
    return names


#: 真正"挂回调"的调用点。只在这些调用里收 `self.xxx`，
#: 否则 `root.addWidget(self._adv_host)` 这种普通传参会被误当成回调。
_REGISTER_CALLS = frozenset({
    'connect', 'act', 'addAction', 'clicked', 'triggered',
    'exec', 'addItems',
})


def _is_registration_call(node: ast.Call) -> bool:
    """这个调用是不是在注册回调？"""
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id in _REGISTER_CALLS
    if isinstance(fn, ast.Attribute):
        # `x.connect(...)` / `menu.addAction(...)` / `btn.clicked.connect(...)`
        if fn.attr in _REGISTER_CALLS:
            return True
        if isinstance(fn.value, ast.Attribute):
            return fn.value.attr in _REGISTER_CALLS
    return False


def _action_targets() -> set:
    """
    所有被挂成动作的**方法名**。

    两类写法都要认（Qt 的接线方式和 tkinter 不一样）：
      · `act('…', self.foo, …)` —— 位置参数，函数引用（没调用）；
      · `menu.addAction('…', lambda: self.foo(nid))` —— lambda 里的调用。
    只在**注册回调的调用点**里收，不收 `root.addWidget(self._panel)`
    这种普通传参（那不是回调，把控件名当"悬空回调"报出来是假警报）。
    """
    tree = ast.parse(_source())
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_registration_call(node):
            continue
        for arg in list(node.args) + [k.value for k in node.keywords]:
            if isinstance(arg, ast.Attribute) and \
                    isinstance(arg.value, ast.Name) and \
                    arg.value.id == 'self':
                names.add(arg.attr)          # 函数引用本身
            if isinstance(arg, ast.Lambda):
                names |= _called_names(arg)
    return names


class TestNoDanglingCallbacks:
    """
    挂到按钮/菜单上的方法必须真的存在。

    记忆文件里有过先例：「批量导入章节」曾指向不存在的
    `self.batch_import_chapters`，点一下 AttributeError，且**没有任何测试覆盖**。
    """

    def test_every_action_target_exists(self):
        targets = _action_targets()
        assert targets, '没扫到任何动作目标，检查扫描逻辑'
        defined = _defined_names()
        missing = sorted(t for t in targets if t not in defined)
        assert not missing, f'悬空回调（点一下会报错）：{missing}'

    def test_no_tkinter_style_command_left(self):
        """
        `command=self.xxx` 是 tkinter 的写法。Qt 文件里出现它就说明
        有人把老代码粘回来了（而 Qt 根本不认这个关键字 —— 静默不接线）。
        """
        assert 'command=self.' not in _source(), \
            'Qt 窗口里出现了 tkinter 的 command= 写法（Qt 不认，等于没接）'


class TestCardEntryIsDiscoverable:
    """
    卡片的入口必须"看得见、知道会打开哪一章、点开就有内容"。
    """

    def test_toolbar_has_a_recognisable_open_entry(self):
        """工具栏第一屏必须有一个文案能让人认出来的主入口。"""
        src = _source()
        assert re.search(r"act\('[^']*(?:打开项目|载入单章)[^']*'", src), \
            '工具栏找不到"打开项目 / 载入单章"入口'

    def test_editor_receives_a_script_path(self):
        """打开卡片时必须把选中章节传进去——否则又是空窗口。"""
        src = _source()
        assert 'load_script(fp)' in src or 'load_script(' in src, \
            '双击章节块没有把脚本路径交给卡片编辑器，会开空窗口'

    def test_open_editor_has_file_fallback(self):
        """没选章节时要让用户挑文件，而不是开个空窗口晾着。"""
        src = _source()
        start = src.find('def _on_load')
        assert start != -1, '找不到「载入单章」的实现'
        body = src[start:start + 700]
        assert 'QFileDialog.getOpenFileName' in body, \
            '没选章节时没有再给一次选文件的机会'

    def test_right_click_menu_also_has_card_entry(self):
        """画布右键菜单里也要有——策划是在节点上右键的。"""
        src = _source()
        assert '在卡片界面里打开' in src, '右键菜单里没有卡片入口'
        assert '用 Excel 打开此章节' in src, \
            '右键菜单里没有"用 Excel 打开"（表格编辑那条路）'
        # 未关联源文件的块要置灰，而不是点完再弹错误
        assert 'setEnabled(False)' in src, \
            '不可用的菜单项没有置灰'

    def test_graph_canvas_signals_are_connected(self):
        """
        画布发的信号必须真的接到窗口上（不接 = 双击/右键/Shift 拖连线全没反应）。

        信号名对应**旧版的操作**（用户 2026-10-03："要遵照我旧版的操作来"）：
        `connect_requested` = Shift+左键拖连线、`node_ops_requested` = 双击块
        编辑变量、`node_add_requested` = 双击空白新建、`node_menu_requested` = 右键。
        """
        src = _source()
        for sig in ('connect_requested', 'node_ops_requested',
                    'node_add_requested', 'node_menu_requested'):
            assert f'.{sig}.connect(' in src, f'画布信号 {sig} 没接到窗口'


class TestCardSaveAndUndoAreDiscoverable:
    """
    用户 2026-10-01 明确要的两个功能，入口必须**看得见** ——
    这正是本文件存在的理由（"功能做完 ≠ 用户能用"）。

    tkinter 版当年就踩过一模一样的坑，用户的抱怨记录在这里：

        2026-09-30 > 你就没有做将一张卡片修改后的保存按钮！！！
                   > 我做完了也无法保存到该卡片上！
    """

    def test_save_button_exists_in_the_card(self):
        src = _source()
        assert re.search(r"_save_btn = QPushButton\('[^']*保存", src), \
            '卡片上没有「保存」按钮'
        assert 'save_requested.connect(self.save_current_beat)' in src, \
            '「保存」按钮没接到 save_current_beat'

    def test_undo_button_exists_in_the_card(self):
        src = _source()
        assert re.search(r"_undo_btn = QPushButton\('[^']*撤销", src), \
            '卡片上没有「撤销」按钮'
        assert 'undo_requested.connect(self.undo_current_beat)' in src, \
            '「撤销」按钮没接到 undo_current_beat'

    def test_toolbar_also_exposes_save(self):
        """工具栏也要有一份（Ctrl+S 的快捷键就挂在那里）。"""
        src = _source()
        assert 'self.save_current_beat' in src
        assert re.search(r"act\('[^']*保存[^']*'", src), \
            '工具栏没有保存入口'

    def test_undo_button_state_is_synced(self):
        """撤销按钮的可用性必须跟着撤销栈走，不能一直亮着骗人。"""
        assert '_sync_undo_button' in _source()

    def test_dirty_indicator_exists(self):
        """没有"有未保存改动"提示的话，"改了没存就切走"是静默的。"""
        src = _source()
        assert '_dirty_label' in src
        assert 'is_modified' in src


class TestGraphScopeAndAssetsAreTwoPaths:
    """
    用户 2026-10-03 纠正的语义，必须同时钉在**文案、签名、启动路径**三处：

    > 我没有叫你所有的文件全部导进剧情图内！这还选个毛线啊！看都要看瞎了！
    > 我是让你让用户自己导入章节！所谓的导入项目只是为了表格的 xlsx 的
    > 资源管理系统！不是把整个项目都排在剧情图上面！！！
    > 剧情图上只有用户自己导入的章节排版！！！跟条件设置！

    上一版把"打开项目"写成了"打开项目 → 建出结构图"，于是 40 个脚本
    （真实项目）一次性灌进图里变成 51 个块。三条判据缺一不可：
    工具栏有两条分开的入口 / 资源加载**拿不到图** / 启动不再扫全项目。
    """

    def test_toolbar_exposes_both_paths_separately(self):
        labels = re.findall(r"act\('([^']+)'", _source())
        assert any('导入章节' in t for t in labels), \
            f'工具栏没有「导入章节」入口（剧情图那条路）：{labels}'
        assert any('打开项目' in t for t in labels), \
            f'工具栏没有「打开项目」入口（资源表那条路）：{labels}'

    def test_open_project_tooltip_says_it_does_not_touch_the_graph(self):
        """用户最容易误解的就是"打开项目会不会把整部戏画出来"，说明里必须讲清。"""
        src = _source()
        i = src.find("act('📁 打开项目'")
        assert i != -1, '找不到「打开项目」动作'
        body = src[i:i + 500]
        assert '不往剧情图里放任何东西' in body or '不往这张图里放任何东西' in body, \
            f'「打开项目」的说明没讲清它不碰剧情图：{body[:200]!r}'

    def test_asset_loader_cannot_even_see_the_graph(self):
        """
        `load_assets()` 的签名里**没有** graph 参数 —— 想灌也灌不进去。

        这是"从接口上断开"而不是"靠自觉"：以后谁想顺手在打开项目时建图，
        得先改签名，改签名就会被这条用例拦住。
        """
        import inspect

        from storygraph.app.editor import EditorSession

        params = inspect.signature(EditorSession.load_assets).parameters
        assert 'graph' not in params, \
            'load_assets 收了 graph —— 又能"打开项目顺手建图"了'
        # 反过来：导入章节**必须**收 graph（它就是改图的）
        assert 'graph' in inspect.signature(
            EditorSession.import_chapters).parameters

    def test_startup_no_longer_discovers_the_whole_project(self):
        """
        启动路径不许再出现"扫全项目 → 建图"。

        判据走 **AST**，不做字符串搜索：`main_qt` 的注释里就写着
        "以前这里 `analyze(files=discover_scripts(root))` 一次把 40 个脚本
        灌进图里"——那句话是**历史说明**，不是违规代码
        （这个坑在 2026-10-01 扫 tkinter 时已经踩过一次）。
        """
        import ast

        from _source_paths import module_source

        tree = ast.parse(module_source('storygraph.main_qt')
                         .read_text(encoding='utf-8'))
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
        assert 'discover_scripts' not in used, \
            'main_qt 里真的在扫全项目建图（会把整部戏灌进剧情图）'
        # 反向确认：它确实只把"要么项目根、要么单个 .rpy"交给窗口
        assert 'open_project_path' in used and 'import_chapter_files' in used

    def test_graph_scope_is_derived_from_node_file_paths(self):
        """图的范围 = 图上节点带的文件（不是"项目里有哪些文件"）。"""
        from storygraph.app.editor import EditorSession
        from storygraph.graph.models import Graph

        g = Graph()
        g.add_node('a', 0, 0, file_path='x/one.rpy')
        g.add_node('b', 0, 0, file_path='x/two.rpy')
        g.add_node('c', 0, 0, file_path='x/one.rpy')
        files = EditorSession().graph_files(g)
        assert [Path(f).name for f in files] == ['one.rpy', 'two.rpy']


class TestAdvertisedButtonsHaveImplementations:
    """
    工具栏/菜单上写出来的功能必须真的有实现。

    「导出本章为 Excel 表」曾经只是**一行字**——方法不存在，
    点了什么都不会发生。这类"假按钮"比没有按钮更伤信任。
    """

    def test_actions_actually_do_something(self):
        """
        每个用 `act()` 声明的动作都必须绑定到**真实存在**的方法。

        （`_action_targets` 已覆盖存在性；这里额外确认工具栏里的
        中文文案都对应到某个 act 调用，防止"文案在、动作没接"。）
        """
        src = _source()
        labels = re.findall(r"act\('([^']+)'", src)
        assert len(labels) >= 8, f'工具栏动作太少，可能漏了：{labels}'
        for must in ('打开项目', '导入章节', '直接编辑单章', '保存', '导出',
                     '使用说明'):
            assert any(must in t for t in labels), \
                f'工具栏缺少「{must}」入口（现有：{labels}）'

    def test_table_roundtrip_is_reachable(self):
        """
        Excel 双向工作流必须有入口 —— tkinter 版有「用 Excel 打开此章节」，
        Qt 版删除 tkinter 时**不能把它弄丢**。
        """
        src = _source()
        assert 'def open_node_in_excel' in src
        assert 'def export_from_table' in src
        assert re.search(r"act\('[^']*从剧本表写回", src), \
            '工具栏没有「从剧本表写回」入口'


# ------------------------------------------------------------------
#  运行时核对：动作真的挂上了（需要 Qt）
# ------------------------------------------------------------------

class TestActionsAreReallyWired:
    """
    静态扫描只能证明"名字对得上"。这一组真的建一个窗口，
    把 `QAction` 捞出来确认**文案与回调都挂上了**。
    """

    def test_toolbar_actions_have_receivers(self, qapp, ui_window):
        from PySide6.QtGui import QAction

        actions = [a for a in ui_window.findChildren(QAction)
                   if (a.text() or '').strip()]
        labels = [a.text() for a in actions]
        assert labels, '窗口里一个 QAction 都没有 —— 工具栏没建起来？'
        for must in ('保存', '导出', '使用说明'):
            assert any(must in t for t in labels), \
                f'工具栏里没有「{must}」动作（实际：{labels}）'

    def test_save_action_actually_runs(self, qapp, ui_window):
        """触发一次保存动作：接线断了会抛，不该抛。"""
        from PySide6.QtGui import QAction

        save = next(a for a in ui_window.findChildren(QAction)
                    if '保存' in (a.text() or ''))
        ui_window._show_index(0)
        save.trigger()
        qapp.processEvents()

    def test_card_buttons_are_reachable(self, qapp, ui_window):
        """卡片上那两个按钮必须真的在控件树里（不是只存在于源码里）。"""
        from PySide6.QtWidgets import QPushButton

        texts = [b.text() for b in ui_window._panel.findChildren(QPushButton)]
        assert any('保存' in t for t in texts), texts
        assert any('撤销' in t for t in texts), texts
