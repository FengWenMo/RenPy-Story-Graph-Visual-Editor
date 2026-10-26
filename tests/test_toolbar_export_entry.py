"""
「导出」入口的位置（用户 2026-10-21 提出、2026-10-26 调整顺序）

> 导出应当放在最上面那一栏……因为太常用了

2026-10-26 用户重新点了整条工具栏的顺序：

    章节导航 → 打开项目 → 自定义模块 → AI 助手 →
    导入（章节 / 纯文字）→ **导出** → 刷新 → 清理

所以"导出在最前"改成了"导出紧跟导入、在刷新/清理之前"。
判据仍然分三层，越往下越接近用户真正碰到的问题：

  ① 两页的顶部工具栏**都有**它（不用先回卡片页才能导出）；
  ② 它排在导入之后、刷新/清理之前（顺序是用户点名的，不许被再一次挤到后面）；
  ③ 窗口够宽时它**仍然可见**，而且点下去真的会走到导出那条路。
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
    assert any('导出 .rpy' in t for t in graph), f'结构页顶部没有导出：{graph[:8]}'
    assert any('导出 .rpy' in t for t in card), f'卡片页顶部没有导出：{card[:8]}'


def test_export_sits_between_imports_and_refresh(ui_window):
    """
    ② 顺序（用户 2026-10-26 点名）：导入 → **导出** → 刷新 → 清理。

    为什么"存在"不够：这条工具栏在窄窗口里会溢出进「»」菜单 ——
    位置本身就是可用性。用户这次明确要"先是导入，后是导出，
    最后才是刷新和清理"，所以导出**必须**落在导入之后、刷新之前。
    """
    graph = _texts(ui_window._tb_graph)

    def first_index(key):
        return next((i for i, t in enumerate(graph) if key in t), -1)

    import_at = first_index('导入纯文字')
    export_at = first_index('导出 .rpy')
    refresh_at = first_index('刷新已导入章节')
    cleanup_at = first_index('清理导入残留')
    assert export_at >= 0, f'结构页没有导出：{graph[:10]}'
    assert import_at < export_at < refresh_at < cleanup_at, (
        f'顺序不对：导入@{import_at} 导出@{export_at} '
        f'刷新@{refresh_at} 清理@{cleanup_at}；{graph[:10]}')
    # 「章节导航」依然是第一个（用户："章节导航应摆到最左边"）
    assert '章节导航' in graph[0], graph[:3]


def test_export_is_visible_in_a_wide_window(qapp, ui_window):
    """
    ③ 窗口够宽时它**可见**（没有掉进「»」溢出菜单）。

    ⚠ 1280px 下不再强求：用户把导入排到了导出前面，工具栏宽度有限，
    窄窗口里排在后面的动作本来就该由「»」接住 —— 强行塞进首屏反而
    会把后面的按钮挤出去。宽窗口（2400px，足够放整条）必须看得见。
    """
    ui_window.resize(2400, 900)
    ui_window.show()
    ui_window._set_page(ui_window.PAGE_GRAPH)
    qapp.processEvents()
    try:
        act = _export_action(ui_window._tb_graph)
        assert act is not None
        btn = ui_window._tb_graph.widgetForAction(act)
        assert btn is not None, '这个动作没有按钮（根本没挂上去）'
        assert btn.isVisible(), '宽窗口里导出仍然不可见'
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
