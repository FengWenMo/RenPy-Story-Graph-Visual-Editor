# -*- coding: utf-8 -*-
"""
tests/test_ui_width.py
======================
**UI 宽窄可变 / 右栏不许出现横向滚动条**（用户反馈第 5 条，2026-10-30）。

用户原话：

> UI 能不能把这个部分改短点，目前看根本用不到滚动条才能滚出来的部分

规划与探针：`.pip-tmp/plan/05_ui_width.md`（只读轮）、`.pip-tmp/plan/probe_after.py`
（修复后数字）。根因有四层，这个文件一层一条判据：

  ① 卡片页顶部动作条 5 个结构按钮 ≈570px → 窗口最小宽被顶到 1142px
     （`test_window_can_be_narrow` / `test_structural_menu_*`）；
  ② 「高级区」按钮 51 字长文本 /「选择…」按钮 / 288px 舞台小图 → 卡片地板 380px
     （`test_card_min_width_budget`）；
  ③ 立绘网格写死 4 列 → 2 张立绘卡就把卡片最小宽顶到 519px
     （`test_sprite_grid_columns_adapt` / `test_sprite_cards_never_widen_the_column`）；
  ④ 分栏能拖到远小于卡片宽度 → 拖窄必出横向滚动条
     （`test_every_beat_has_no_horizontal_scrollbar`：逐拍把右栏压到 340 断言）。

判据都量**可观察几何**（minimumSizeHint / 滚动条可见性 / 分栏尺寸），不测截图。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtCore import Qt                              # noqa: E402
from PySide6.QtWidgets import (QApplication, QMenu, QScrollArea,  # noqa: E402
                               QToolButton)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storygraph.ui_qt import main_window as MW                 # noqa: E402
from storygraph.ui_qt.sprite_grid import SpriteGridWidget      # noqa: E402


# ==================================================================
#  夹具
# ==================================================================

@pytest.fixture(scope='session', autouse=True)
def app():
    """没有 QApplication 时建 QWidget 是直接 abort（不是抛异常）。"""
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture(autouse=True)
def _hide_before_close(ui_window):
    """
    用例结束**先 hide 再让 conftest 关窗**。

    为什么（本轮实测踩过）：`closeEvent._confirm_close` 对"窗口可见 + 有未
    导出改动"会弹**模态确认框**；offscreen 下没人去点它 —— 整轮测试挂死
    （第一次跑 7 分钟没结束）。窗口不可见时它走"测试/嵌入"那条分支，不弹框。
    """
    yield
    ui_window.hide()
    QApplication.processEvents()


def _card_page(win, size=(1360, 820)):
    """
    切到卡片页、摆成指定窗口大小并**真的 show 出来**。

    为什么必须 show：`QWidget.resize()` 对**隐藏**窗口只是记下尺寸，子控件的
    resizeEvent / 布局激活要等 show 之后才发 —— 不 show 时量到的分栏尺寸
    永远是"最小宽兜底"（340），立绘网格也还按默认 640px 排 3 列。
    """
    win._set_page(win.PAGE_CARD)
    win.resize(*size)
    win.show()
    for _ in range(8):
        QApplication.processEvents()
    win.resize(*size)
    for _ in range(8):
        QApplication.processEvents()
    return win


def _card_scroll(win) -> QScrollArea:
    """卡片页右栏那个 `QScrollArea`（widget = `BeatCardEditor`）。"""
    page = win._stack.widget(win.PAGE_CARD)
    for sc in page.findChildren(QScrollArea):
        if sc.widget() is win._panel:
            return sc
    raise AssertionError('找不到卡片页的滚动区')


def _squeeze_right(win):
    """把分栏拖到最窄 —— 由右栏最小宽兜底（不是硬塞）。"""
    total = max(1, sum(int(n) for n in win._split_card.sizes()))
    win._split_card.setSizes([total, 0])
    for _ in range(5):
        QApplication.processEvents()


def _cards(n: int) -> list:
    return [{'image': f'ling happy{i}', 'expression': '', 'position': '',
             'at': '', 'speaking': False, 'order_index': i,
             'extra_rows': []} for i in range(n)]


# ==================================================================
#  ① 卡片地板（用户那句"这部分改短点"）
# ==================================================================

class TestCardMinWidthBudget:

    def test_card_min_width_budget(self, ui_window):
        """
        卡片的最小宽必须**小于**右栏的硬下限（340）——两个数一起看才成立：
        卡片 340 而右栏 340 时还有滚动区边框，等于没余量。
        """
        win = _card_page(ui_window)
        budget = int(win._panel.minimumSizeHint().width())
        assert budget <= MW.CARD_COLUMN_MIN_W, (
            f'卡片最小宽 {budget}px > 右栏下限 {MW.CARD_COLUMN_MIN_W}px —— '
            f'右栏压到下限时必出横向滚动条')
        assert budget <= 320, (
            f'卡片最小宽 {budget}px 超出本次预算（探针实测 296；'
            f'目标 ≤320，最差 ≤340）')

    def test_the_stage_thumb_is_narrowed_inside_the_card(self, ui_window):
        """「当前舞台」小图在**卡片里**是 240×135（仍是 16:9）。"""
        win = _card_page(ui_window)
        thumb = win._panel.stage_thumb()
        assert MW.CARD_STAGE_THUMB == (240, 135)
        assert thumb.maximumWidth() == 240, (
            f'卡片里的小图最大宽 {thumb.maximumWidth()}px（应 240）')
        assert thumb.maximumHeight() == 135
        assert (thumb.width(), thumb.height()) == (240, 135), (
            f'实际尺寸 {(thumb.width(), thumb.height())}（应 240×135）')

    def test_the_advanced_toggle_is_short(self, ui_window):
        """「高级区」按钮不许再挂 51 字长文本（它是卡片地板的最大单项）。"""
        win = _card_page(ui_window)
        btn = win._panel._adv_btn
        assert len(btn.text()) <= 12, f'高级区按钮文本又变长了：{btn.text()!r}'
        assert '高级区' in btn.text()
        # "里面有什么"不能丢：tooltip 里还写着（信息没少，只是不占宽度）
        for word in ('代码', 'Label', 'Notes', 'Stage'):
            assert word in btn.toolTip(), f'tooltip 里丢了 {word}：{btn.toolTip()!r}'

    def test_the_asset_pickers_are_icon_sized(self, ui_window):
        """背景 / 音乐 / 语音的「选择…」→ 24px「⋯」图标按钮（tooltip 说明去哪挑）。"""
        win = _card_page(ui_window)
        btns = win._panel._asset_btns
        assert set(btns) == {'Scene', 'BGM', 'Voice'}, btns
        for name, btn in btns.items():
            assert btn.text() == '⋯', f'{name} 的按钮又变成文字了：{btn.text()!r}'
            assert btn.maximumWidth() <= 28, (
                f'{name} 的按钮宽 {btn.maximumWidth()}px（应 ≤28）')
            # ⚠ 24px 宽的按钮**必须**去掉默认内边距，否则「⋯」会被裁成一片
            # 空白（offscreen 截图实测过）—— 用户看到的是"点了没反应的方块"。
            assert 'padding:0' in btn.styleSheet().replace(' ', ''), (
                f'{name} 的按钮没设 padding:0，24px 里「⋯」会被裁掉')
            assert '资源表' in btn.toolTip(), f'{name} 按钮没说明去哪挑：{btn.toolTip()!r}'


# ==================================================================
#  ② 右栏永不出横向滚动条（用户那句话的**直接判据**）
# ==================================================================

class TestNoHorizontalScrollbar:

    def test_the_right_column_has_a_min_width_floor(self, ui_window):
        """结构性保证：右栏最小宽 = 卡片地板；分栏拖到这儿就停。"""
        win = _card_page(ui_window)
        assert win._right_col.minimumWidth() == MW.CARD_COLUMN_MIN_W
        assert win._split_card.childrenCollapsible() is False, (
            '分栏还能把右栏拖成 0 宽 —— 那会立刻出横向滚动条')

    def test_every_beat_has_no_horizontal_scrollbar(self, ui_window):
        """
        **逐拍**把右栏压到最窄（340），断言横条不可见。

        这是用户那句话的直接判据："浏览背景不用拖滚动条到最右边"。
        覆盖：空卡 / 有立绘卡 / 控制流卡 / 带语句参数的拍（工程里都有）。
        """
        win = _card_page(ui_window, (900, 820))
        scroll = _card_scroll(win)
        bad = []
        for i in range(len(win._session.beats)):
            _squeeze_right(win)
            win._show_index(i)
            for _ in range(3):
                QApplication.processEvents()
            _squeeze_right(win)
            hbar = scroll.horizontalScrollBar()
            viewport = int(scroll.viewport().width())
            budget = int(win._panel.minimumSizeHint().width())
            if hbar.isVisible() or budget > viewport:
                bad.append((i, viewport, budget, int(hbar.maximum())))
        assert not bad, (
            '这些拍在右栏最窄时放不下（拍号, 视口宽, 卡片最小宽, 横条 range）：'
            f'{bad}')

    def test_sprite_cards_never_widen_the_column(self, ui_window):
        """
        2 / 4 张立绘卡在右栏最窄时**不许**把卡片顶宽（老毛病：2 张就 +139px）。

        网格列数随宽度自适应后，窄栏里是 1 列竖排。
        """
        win = _card_page(ui_window, (900, 820))
        scroll = _card_scroll(win)
        grid = win._panel._grid
        for n in (2, 4):
            grid.load(_cards(n))
            _squeeze_right(win)
            for _ in range(4):
                QApplication.processEvents()
            _squeeze_right(win)
            assert grid._cols <= 2, f'{n} 张立绘卡在窄栏里排了 {grid._cols} 列'
            assert not scroll.horizontalScrollBar().isVisible(), (
                f'{n} 张立绘卡 + 右栏最窄 → 又出横向滚动条了')
            assert int(win._panel.minimumSizeHint().width()) <= 340, (
                f'{n} 张立绘卡时卡片最小宽 '
                f'{win._panel.minimumSizeHint().width()}px')
        grid.load(_cards(0))

    def test_default_extra_wide_has_sprites_in_a_row(self, ui_window):
        """反方向：右栏够宽时列数会涨回去（自适应不是"永远 1 列"）。"""
        win = _card_page(ui_window, (1600, 900))
        grid = win._panel._grid
        grid.load(_cards(4))
        for _ in range(6):
            QApplication.processEvents()
        assert grid._cols >= 2, f'宽右栏（{grid.width()}px）还只排 1 列：{grid._cols}'
        grid.load(_cards(0))


class TestSpriteGridColumnsAdapt:
    """网格列数 = f(可用宽度)（独立控件判据，不经过整窗）。"""

    def test_columns_follow_the_available_width(self, app):
        grid = SpriteGridWidget()
        grid.load(_cards(4))
        grid.show()
        try:
            for _ in range(3):
                app.processEvents()
            grid.resize(300, 240)
            for _ in range(3):
                app.processEvents()
            assert grid._cols == 1, f'300px 宽排了 {grid._cols} 列（应 1 列）'
            grid.resize(900, 240)
            for _ in range(3):
                app.processEvents()
            assert grid._cols == 4, f'900px 宽排了 {grid._cols} 列（应 4 列）'
        finally:
            grid.close()

    def test_the_minimum_width_does_not_follow_the_column_count(self, app):
        """
        最小宽只报**一列卡片**（202 + 边距），不是"当前列数 × 卡片宽"。

        这条是防止老坑复发：4 列布局的最小宽（808px）要是被当成控件最小宽，
        窗口变宽时右栏反而被顶宽 —— 横向滚动条在"宽屏"上出现。
        """
        grid = SpriteGridWidget()
        grid.load(_cards(4))
        grid.show()
        try:
            grid.resize(900, 240)
            for _ in range(3):
                app.processEvents()
            assert grid._cols == 4
            assert grid.minimumSizeHint().width() <= 240, (
                f'4 列时最小宽 {grid.minimumSizeHint().width()}px —— '
                f'它会被右栏当成下限，宽屏又出横条')
        finally:
            grid.close()


# ==================================================================
#  ③ 窗口能变窄（"UI 宽窄可变"的前提）
# ==================================================================

class TestWindowCanBeNarrow:

    def test_the_default_window_size_constant(self):
        """默认窗口 1360×900（用户 2026-10-30 拍板），且没超过小屏预算。"""
        assert MW.DEFAULT_WINDOW_SIZE == (1360, 900)
        assert 1200 <= MW.DEFAULT_WINDOW_W <= 1400, (
            f'默认窗口宽 {MW.DEFAULT_WINDOW_W} 不在"窄一档"的范围')

    def test_the_window_minimum_width_is_under_900(self, ui_window):
        win = _card_page(ui_window)
        min_w = int(win.minimumSizeHint().width())
        assert min_w <= 900, (
            f'窗口最小宽 {min_w}px > 900 —— 还是"只能开到很宽"')
        # 2026-10-30 探针 738（目标 726±30）；放宽到 ≤820 防字体差异
        assert min_w <= 820, f'窗口最小宽 {min_w}px 超过本轮预算（≤820，探针 738）'

    def test_the_window_really_resizes_down(self, ui_window):
        """真的 `resize(880)` 时能缩到 880 附近（以前会停在 1142）。"""
        win = _card_page(ui_window)
        win.show()
        try:
            win.resize(880, 820)
            for _ in range(6):
                QApplication.processEvents()
            assert win.width() <= 900, (
                f'窗口缩到 880 却停在 {win.width()}px —— 最小宽还太大')
            assert win.width() >= 600, f'窗口被压到 {win.width()}px，太小了吧'
        finally:
            win.hide()

    def test_the_card_page_button_row_fits_at_900(self, ui_window):
        """卡片页那条动作条在 900px 窗口下**不许裁剪**（结构按钮已收进菜单）。"""
        win = _card_page(ui_window, (900, 820))
        row = win._back_btn.parentWidget()
        assert row.width() >= 600, f'动作条没拿到宽度（{row.width()}），量不到'
        assert row.minimumSizeHint().width() <= row.width(), (
            f'动作条最小宽 {row.minimumSizeHint().width()}px > 可用 {row.width()}px '
            f'—— 又有按钮被裁在窗口外')


class TestStructuralMenu:
    """5 个结构按钮收进「⋯ 结构」菜单（动作 / 快捷键 / 文案不变）。"""

    EXPECTED = ('＋ 插入卡片', '＋ 插到前面', '↳ 插入控制流卡',
                '◇ 插入选择点', '🗑 删除这张卡片')

    def test_the_five_actions_are_in_a_menu(self, ui_window):
        win = _card_page(ui_window)
        btn: QToolButton = win._structural_btn
        menu: QMenu = win._structural_menu
        assert btn.menu() is menu, '「⋯ 结构」没挂菜单'
        texts = tuple(a.text() for a in menu.actions())
        assert texts == self.EXPECTED, texts
        # 按钮本身**不再占**动作条的宽度（它们只是接线与可用性的真源）
        for name in ('_insert_btn', '_insert_before_btn', '_flow_btn',
                     '_choice_btn', '_delete_btn'):
            assert not getattr(win, name).isVisibleTo(win), (
                f'{name} 还显示在动作条上（窗口又被顶宽了）')

    def test_a_menu_action_does_the_same_thing_as_the_button(self, ui_window):
        """菜单项 = 那个按钮的第二个入口：真的能建框（不是"点了没反应"）。"""
        win = _card_page(ui_window)
        win._show_index(2)
        before = len(win._session.beats)
        action = win._structural_actions[0][0]      # ＋ 插入卡片
        action.trigger()
        for _ in range(3):
            QApplication.processEvents()
        assert len(win._session.beats) == before + 1, (
            '菜单里的「＋ 插入卡片」没建出新卡（点了没反应）')

    def test_menu_action_state_follows_the_button(self, ui_window):
        """可用性从按钮同步（`_sync_structural_buttons` 只认按钮）。"""
        win = _card_page(ui_window)
        action, button = next((a, b) for a, b in win._structural_actions
                              if a.text() == '🗑 删除这张卡片')
        button.setEnabled(False)
        win._structural_menu.aboutToShow.emit()     # 用户点开菜单时发生的事
        assert action.isEnabled() is False, '删卡片按钮置灰了，菜单项还亮着'
        button.setEnabled(True)
        win._structural_menu.aboutToShow.emit()
        assert action.isEnabled() is True

    def test_the_undo_structural_button_stays_visible(self, ui_window):
        """「↩ 撤销结构操作」留在外面（它是即时反馈，不该多一层菜单）。"""
        win = _card_page(ui_window)
        assert win._restore_btn.isVisibleTo(win), '撤销结构操作被收进菜单了'


# ==================================================================
#  ④ 分栏：默认窄 / 可展开 / 可记忆
# ==================================================================

class TestCardColumnSplitter:

    def test_the_default_right_column_is_narrow(self, ui_window):
        win = _card_page(ui_window, (1360, 820))
        sizes = [int(n) for n in win._split_card.sizes()]
        assert len(sizes) == 2, sizes
        assert 380 <= sizes[1] <= 480, (
            f'默认右栏 {sizes[1]}px —— 用户要的是"默认为窄"（≈420）')

    def test_it_can_be_expanded_and_collapsed(self, ui_window):
        win = _card_page(ui_window, (1360, 820))
        before = int(win._split_card.sizes()[1])
        assert MW.toggle_card_column(win) is True
        for _ in range(4):
            QApplication.processEvents()
        expanded = int(win._split_card.sizes()[1])
        assert expanded > before + 100, (
            f'展开后右栏 {expanded}px（展开前 {before}px）—— 没变宽')
        assert MW.toggle_card_column(win) is False
        for _ in range(4):
            QApplication.processEvents()
        back = int(win._split_card.sizes()[1])
        assert abs(back - before) <= 12, (
            f'收起后右栏 {back}px（原 {before}px）—— 没回到原样')

    def test_double_clicking_the_splitter_handle_expands(self, ui_window):
        """双击分栏把手 = 展开（与「⤢」同一个动作）。"""
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest

        win = _card_page(ui_window, (1360, 820))
        handle = win._split_card.handle(1)
        before = int(win._split_card.sizes()[1])
        QTest.mouseDClick(handle, Qt.LeftButton, pos=QPoint(6, 6))
        for _ in range(4):
            QApplication.processEvents()
        assert int(win._split_card.sizes()[1]) > before + 100, (
            '双击分栏把手没有展开卡片栏')

    def test_the_expand_button_exists_next_to_the_caption(self, ui_window):
        win = _card_page(ui_window)
        btn = win._card_expand_btn
        assert btn.isVisibleTo(win), '「⤢」展开按钮不见了'
        assert '双击' in btn.toolTip(), (
            f'按钮 tooltip 没提"双击分栏把手"这条捷径：{btn.toolTip()!r}')
