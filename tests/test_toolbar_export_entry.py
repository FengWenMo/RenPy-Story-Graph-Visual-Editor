"""
「导出」在最上面那一栏（用户 2026-10-21）

> 导出应当放在最上面那一栏……因为太常用了

判据分三层，越往下越接近用户真正碰到的问题：

  ① 两页的顶部工具栏**都有**它（不用先回卡片页才能导出）；
  ② 它排在**前面**（工具栏窄了会溢出进「»」菜单 —— 排后面的等于没有）；
  ③ 窄窗口（1280px）下它**仍然可见**，而且点下去真的会走到导出那条路。
"""
from __future__ import annotations

import pytest

pytest.importorskip('PySide6')


def _texts(toolbar) -> list:
    return [a.text() for a in toolbar.actions() if a.text().strip()]


def _export_action(toolbar):
    for a in toolbar.actions():
        if '导出' in a.text() and '控制流' not in a.text():
            return a
    return None


def test_both_pages_have_an_export_entry_in_their_top_bar(ui_window):
    """① 两页顶部都有导出（导的是"已载入的那一章"，跟在哪一页无关）。"""
    graph = _texts(ui_window._tb_graph)
    card = _texts(ui_window._tb_card)
    assert any('导出' in t for t in graph), f'结构页顶部没有导出：{graph[:8]}'
    assert any('导出' in t for t in card), f'卡片页顶部没有导出：{card[:8]}'


def test_export_sits_near_the_front_of_the_bar(ui_window):
    """
    ② 排在前面。

    为什么"存在"不够：这条工具栏在 1500px 宽的真机窗口里就已经装不下
    （实测需要 2349px），排在后面的按钮会被折进右端的「»」菜单 ——
    用户看到的是"没有这个按钮"。
    """
    graph = _texts(ui_window._tb_graph)
    card = _texts(ui_window._tb_card)
    assert any('导出' in t for t in graph[:3]), f'结构页导出排太后面：{graph[:8]}'
    assert any('导出' in t for t in card[:4]), f'卡片页导出排太后面：{card[:8]}'
    # 「🧩 自定义语句」是用户点名要的第一个（2026-10-10），导出不许把它挤掉
    assert '自定义语句' in graph[0]


def test_export_is_visible_in_a_narrow_window(qapp, ui_window):
    """③ 1280px 宽的真机窗口里，它**仍然可见**（没有掉进「»」菜单）。"""
    ui_window.resize(1280, 860)
    ui_window.show()
    qapp.processEvents()
    try:
        for toolbar in (ui_window._tb_graph, ui_window._tb_card):
            ui_window._set_page(ui_window.PAGE_GRAPH
                                if toolbar is ui_window._tb_graph
                                else ui_window.PAGE_CARD)
            qapp.processEvents()
            act = _export_action(toolbar)
            assert act is not None
            btn = toolbar.widgetForAction(act)
            assert btn is not None, '这个动作没有按钮（根本没挂上去）'
            assert btn.isVisible(), '窄窗口里导出被折进了「»」溢出菜单'
    finally:
        ui_window._set_page(ui_window.PAGE_GRAPH)


def test_the_top_bar_export_reaches_the_export_path(ui_window, monkeypatch):
    """
    点了顶端的「导出」要真的走到导出那条路（弹保存框），不是个摆设。

    这里把文件对话框替换掉（不写盘、不弹窗），只验"动作 → 导出 → 选路径"
    这一段是通的。导出本身另有整套判据（门禁/增量/回执）。
    """
    from storygraph.ui_qt import window_export as WE

    seen = []

    def _fake_save(parent, caption, *args, **kwargs):
        seen.append(caption)
        return '', ''

    monkeypatch.setattr(WE.QFileDialog, 'getSaveFileName',
                        staticmethod(_fake_save))
    monkeypatch.setattr(WE.QMessageBox, 'critical',
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(WE.QMessageBox, 'warning',
                        staticmethod(lambda *a, **k: None))

    for page, toolbar in ((ui_window.PAGE_GRAPH, ui_window._tb_graph),
                          (ui_window.PAGE_CARD, ui_window._tb_card)):
        ui_window._set_page(page)
        seen.clear()
        _export_action(toolbar).trigger()
        assert seen, f'第 {page} 页顶部的「导出」点了没反应'
    ui_window._set_page(ui_window.PAGE_GRAPH)
