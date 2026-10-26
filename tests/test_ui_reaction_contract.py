"""
「点了不许没反应」判据（2026-10-26 用户实机）
=============================================

用户原话：

> 还发现一个问题：当块状编辑器没有源文件的时候……我点击这个块，
> 右键的所有栏都没反应……点击导入源文件也没有反应……

真实原因（复现到底）：`window_nodes._repair_choice` 里写的是

    role = QMessageBox.Cancel if key == 'cancel' else QMessageBox.AcceptRole
    box.addButton(label, role)

`QMessageBox.Cancel` 是 **StandardButton（4194304）**，不是 `ButtonRole`；
PySide6 当场抛 `TypeError: addButton(str, StandardButton)`。而 **Qt 槽里抛异常
只打到 stderr**，界面上就是"点了完全没反应"—— 无源节点的**四个修复入口**
（右键「新建 / 绑定源文件…」/「导入并绑定源文件…」、右栏主按钮、源文件丢失的
两档）一起静默失效。

这个文件钉三件事：

  ① **静态守卫**：`addButton(文本, QMessageBox.Ok/Cancel/Yes/No…)` 这种把
     标准按钮当角色用的写法，一旦出现就红（不只抓这一处）；
  ② **行为判据**：无源节点的修复入口**真的建出了对话框**（不是 catch 住异常
     假装没事），取消路径返回 'cancel'；
  ③ **安全壳**：任何菜单项 / 右栏按钮的槽抛异常，都必须写进状态栏 + 控制台，
     不许再变成"点了没反应"。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('PySide6')

from PySide6.QtWidgets import (QDialogButtonBox,  # noqa: E402
                               QMessageBox)

from storygraph.graph.models import Graph        # noqa: E402

UI = ROOT / 'storygraph' / 'ui_qt'


# ======================================================================
#  一、静态守卫：标准按钮不许当「角色」用
# ======================================================================

def _standard_button_names(cls) -> set:
    """`QMessageBox` / `QDialogButtonBox` 上的标准按钮名字（`Ok`/`Cancel`/…）。"""
    out = set()
    std = getattr(cls, 'StandardButton', None)
    if std is None:
        return out
    for name in dir(cls):
        if name.startswith('_'):
            continue
        try:
            value = getattr(cls, name)
        except Exception:                        # noqa: BLE001 - 有些属性会抛
            continue
        if isinstance(value, std):
            out.add(name)
    return out


def _bad_add_button_calls() -> list:
    """
    扫 `storygraph/ui_qt/**/*.py`：`addButton(文本, QMessageBox.<标准按钮>)`。

    为什么用 AST 而不是跑一遍：这类错误只在**用户真的点开那个对话框**时才炸，
    而对话框又大多是模态的（测试要点进去很麻烦）。静态扫一遍成本几乎为零，
    而且能把"下次有人又这么写"一起挡住 —— 与 `dlg.Accepted` 那条 AST 守卫
    同一个思路（见契约速查 §20）。
    """
    std = {
        'QMessageBox': _standard_button_names(QMessageBox),
        'QDialogButtonBox': _standard_button_names(QDialogButtonBox),
    }
    bad = []
    for path in sorted(UI.rglob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError):           # noqa: BLE001 - 读不了/解不了不归它管
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'addButton'):
                continue
            if len(node.args) < 2:
                continue
            second = node.args[1]
            if not (isinstance(second, ast.Attribute)
                    and isinstance(second.value, ast.Name)):
                continue
            names = std.get(second.value.id) or set()
            if second.attr in names:
                bad.append((path.relative_to(ROOT / 'storygraph'), node.lineno,
                            f'{second.value.id}.{second.attr}'))
    return bad


def test_no_standard_button_is_passed_as_a_button_role():
    """`addButton` 第二个参数是 `ButtonRole` —— 传标准按钮会 TypeError（静默）。"""
    bad = _bad_add_button_calls()
    assert not bad, (
        '这些地方把「标准按钮」当成「按钮角色」传给 addButton 了 —— '
        'PySide6 会抛 TypeError，而异常在 Qt 槽里只会打到 stderr，'
        '用户看到的就是"点了没反应"。要标准按钮请用'
        '`box.setStandardButtons(...)`，要自定义按钮请用 '
        'QMessageBox.AcceptRole / RejectRole / DestructiveRole：\n'
        + '\n'.join(f'  {p}:{ln}  {what}' for p, ln, what in bad))


# ======================================================================
#  二、无源节点的修复入口：真的建出对话框
# ======================================================================

def _patch_exec(monkeypatch) -> dict:
    """把 `QMessageBox.exec` 换成"记录按钮后立刻返回"，不真的模态。"""
    seen = {'calls': []}

    def _fake_exec(box):                        # noqa: ANN001 - Qt 方法替身
        seen['calls'].append({
            'title': box.windowTitle(),
            'text': box.text(),
            # ⚠ 按钮文字用 `btn.text()`：`box.buttonText()` 只吃 StandardButton，
            #   传自定义 QPushButton 会 TypeError（第一版替身就踩了这个）。
            'buttons': [btn.text() for btn in box.buttons()],
            'roles': [box.buttonRole(btn) for btn in box.buttons()],
        })
        return 0

    monkeypatch.setattr(QMessageBox, 'exec', _fake_exec)
    return seen


def _graph_with_manual_node(window, label: str = 'orphan') -> str:
    graph = Graph()
    nid = graph.add_node(label, 0, 0)
    window._set_graph(graph)
    window._canvas.select_node(nid, center=False)
    return nid


def test_repair_choice_really_builds_a_dialog(ui_window, monkeypatch):
    """`_repair_choice` 建框时不许抛（2026-10-26 的 TypeError 现场）。"""
    seen = _patch_exec(monkeypatch)
    got = ui_window._repair_choice(
        '给节点配源文件', '「orphan」还没有 .rpy。',
        [('new', '新建 .rpy…'), ('locate', '绑定已有文件…'),
         ('cancel', '取消')])
    assert got == 'cancel'                      # 没点按钮 = 取消
    assert len(seen['calls']) == 1, '对话框根本没建出来 / 没 exec'
    call = seen['calls'][0]
    assert call['buttons'] == ['新建 .rpy…', '绑定已有文件…', '取消'], call
    # `取消` 必须是 RejectRole（Esc / 关窗都该等于取消）
    assert QMessageBox.RejectRole in call['roles'], call['roles']


def test_the_main_button_on_a_manual_node_really_opens_the_dialog(
        ui_window, monkeypatch):
    """右栏「🩹 给这个节点建/绑源文件」→ 真的弹出询问框（不是没反应）。"""
    seen = _patch_exec(monkeypatch)
    nid = _graph_with_manual_node(ui_window)
    ui_window._on_node_open(nid)
    assert seen['calls'], '点了主按钮什么都没发生'
    assert '给节点配源文件' in seen['calls'][0]['title']


def test_the_right_click_repair_action_really_opens_the_dialog(
        ui_window, monkeypatch):
    """右键「新建 / 绑定源文件…」→ 同样真的弹出询问框。"""
    seen = _patch_exec(monkeypatch)
    nid = _graph_with_manual_node(ui_window)
    actions = {a.text(): a for a in ui_window.build_node_menu(nid).actions()}
    assert '新建 / 绑定源文件…' in actions, list(actions)
    assert actions['新建 / 绑定源文件…'].isEnabled()
    actions['新建 / 绑定源文件…'].trigger()
    assert seen['calls'], '右键修复入口点了没反应'


# ======================================================================
#  三、安全壳：槽里抛异常必须说话
# ======================================================================

def _boom(*_a, **_k):
    raise RuntimeError('假装这里炸了')


def test_a_menu_action_that_raises_speaks_instead_of_silence(ui_window,
                                                             monkeypatch):
    """
    菜单项处理函数抛异常 → 状态栏 + 控制台必须看得见（不许静默）。

    没有这层壳时，`_repair_choice` 那个 TypeError 就是这样消失的：
    Qt 把异常打到 stderr，用户只看到"点了没反应"。
    """
    ui_window.import_chapter_files([str(ui_window._file_path)])
    nid = next(iter(ui_window.graph.nodes))
    menu = ui_window.build_node_menu(nid)
    act = next(a for a in menu.actions()
               if a.text().startswith('改本章台词（卡片）'))
    monkeypatch.setattr(ui_window, '_on_node_open', _boom)
    act.trigger()
    message = ui_window._status_bar.currentMessage()
    assert '出错' in message and 'RuntimeError' in message, message
    assert 'RuntimeError' in ui_window._console.toPlainText()


def test_the_right_panel_button_speaks_when_its_slot_raises(ui_window,
                                                            monkeypatch):
    """右栏主按钮同理：槽炸了要写状态栏，不许"点了没反应"。"""
    ui_window.import_chapter_files([str(ui_window._file_path)])
    nid = next(iter(ui_window.graph.nodes))
    ui_window._canvas.select_node(nid, center=False)
    monkeypatch.setattr(ui_window, '_on_node_open', _boom)
    ui_window._open_script_btn.click()
    message = ui_window._status_bar.currentMessage()
    assert '出错' in message and 'RuntimeError' in message, message
