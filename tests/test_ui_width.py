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
     （`TestWindowCanBeNarrow` / `TestStructuralButtonsRestored`：按钮**不藏菜单**，
     装进会折行的 `CardActionRow`，一行放不下就折行）；
  ② 「高级区」按钮 51 字长文本 /「选择…」按钮 / 288px 舞台小图 → 卡片地板 380px
     （`test_card_min_width_budget`）；
  ③ 立绘网格写死 4 列 → 2 张立绘卡就把卡片最小宽顶到 519px
     （`test_sprite_grid_columns_adapt` / `test_sprite_cards_never_widen_the_column`）；
  ④ 分栏能拖到远小于卡片宽度 → 拖窄必出横向滚动条
     （`test_every_beat_has_no_horizontal_scrollbar`：逐拍把右栏压到 340 断言）。

用户 2026-10-30 第 6 条（**"不要那个结构！恢复回原来的 UI"**）之后，① 的
"收进菜单"写法被整个否掉：按钮回到动作条上、窄了折行（`CardActionRow`）。
另加一条迁移判据：`TestLegacyLayoutMigration` —— 老记忆（窗口 1440 / 卡片栏
813）启动时压成窄默认，否则"默认改窄"对老用户完全不可见。

判据都量**可观察几何**（minimumSizeHint / 滚动条可见性 / 分栏尺寸），不测截图。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtCore import QPoint, Qt                      # noqa: E402
from PySide6.QtWidgets import (QApplication, QScrollArea,  # noqa: E402
                               QToolButton, QWidget)

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


class TestNothingSticksOut:
    """
    用户 2026-10-30 第 7 条（看着截图说的两句原话）：

    > 右窗口不得有横向滚动条！所有图案不得超出右边框！

    上一轮只做到"右栏有最小宽 + 压窗口"，可**卡片自己**仍会长到 1077px：
    `QComboBox` 的最小宽默认按**最长条目**算，真机背景候选里有 40 多字的长名，
    三个资产下拉（音乐 + 语音并排）加起来把卡片地板顶到 1077 —— 右栏 357，
    横向滚动条必然出现，所有控件被摆到 1053px 宽（全越界）。
    """

    def test_long_asset_names_do_not_widen_the_card(self, ui_window):
        """40 多字的长图名**不许**把卡片顶宽（这是真机 1077px 的来源）。"""
        win = _card_page(ui_window, (1200, 800))
        combo = win._panel._widgets['Scene']
        long_names = ['bg ' + 'x' * 40 + f'_{i}' for i in range(8)]
        combo.clear()
        combo.addItems(long_names)
        for _ in range(6):
            QApplication.processEvents()
        assert combo.minimumSizeHint().width() <= 220, (
            f'长条目又把组合框最小宽顶到 {combo.minimumSizeHint().width()}px'
            '（`sizeAdjustPolicy` 被改回去了？）')
        budget = int(win._panel.minimumSizeHint().width())
        widest = sorted(((w.minimumSizeHint().width(),
                          f'{type(w).__name__}#{w.objectName()}'
                          f'{w.isVisible() and "*" or ""}')
                         for w in win._panel.findChildren(QWidget)),
                        reverse=True)[:5]
        rows = []
        lay = win._panel.layout()
        for i in range(lay.count() if lay is not None else 0):
            item = lay.itemAt(i)
            w = item.widget() if item is not None else None
            rows.append((int(item.minimumSize().width()) if item else -1,
                         f'{type(w).__name__}#{w.objectName() if w else "layout"}'))
        assert budget <= 340, (
            f'卡片最小宽 {budget}px（长图名把卡片顶宽了）；最宽的几项：{widest}；'
            f'顶层同行：{sorted(rows, reverse=True)[:5]}')
        assert not _card_scroll(win).horizontalScrollBar().isVisible(), (
            '长图名把卡片顶宽 → 又出横向滚动条了')

    def test_no_widget_is_placed_beyond_the_right_edge(self, ui_window):
        """**逐拍**量：任何可见控件的右边界都不许越过卡片的右边。"""
        win = _card_page(ui_window, (1200, 800))
        panel = win._panel
        bad = []
        for i in range(len(win._session.beats)):
            win._show_index(i)
            for _ in range(3):
                QApplication.processEvents()
            right_of_panel = int(panel.width())
            for w in panel.findChildren(QWidget):
                if not w.isVisible():
                    continue
                try:
                    pos = w.mapTo(panel, QPoint(0, 0))
                except Exception:                    # noqa: BLE001 - 已销毁
                    continue
                edge = int(pos.x()) + int(w.width())
                if edge > right_of_panel + 1:
                    bad.append((i, type(w).__name__, w.objectName(), edge,
                                right_of_panel))
        assert not bad, (
            '这些控件被摆到了卡片右边界之外（拍号, 类, objectName, 右边界, 卡片宽）：'
            f'{bad[:8]}')

    def test_the_card_scroll_area_never_shows_a_horizontal_bar(self, ui_window):
        """结构性兜底：策略是 `AlwaysOff`（将来哪个控件又变宽也不出横条）。"""
        win = _card_page(ui_window)
        scroll = _card_scroll(win)
        assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        assert not scroll.horizontalScrollBar().isVisible()


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
        """默认窗口 1200×800（用户 2026-10-30 晚拍板），且没超过小屏预算。

        为什么从 1360 再降到 1200：用户那块屏可用区实测 **1440×852**，
        1360 宽 = 屏宽 94% —— 他看到的还是"铺满屏幕"（"还是那么宽看不全"）。
        """
        assert MW.DEFAULT_WINDOW_SIZE == (1200, 800)
        assert 1100 <= MW.DEFAULT_WINDOW_W <= 1400, (
            f'默认窗口宽 {MW.DEFAULT_WINDOW_W} 不在"窄一档"的范围')
        assert MW.DEFAULT_WINDOW_H <= 860, (
            f'默认窗口高 {MW.DEFAULT_WINDOW_H} 在 852 的可用高度上放不下')

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
        """卡片页那条动作条在 900px 窗口下**不许裁剪**（按钮折行而不是藏起来）。"""
        win = _card_page(ui_window, (900, 820))
        row = win._back_btn.parentWidget()
        assert row.width() >= 600, f'动作条没拿到宽度（{row.width()}），量不到'
        assert row.minimumSizeHint().width() <= row.width(), (
            f'动作条最小宽 {row.minimumSizeHint().width()}px > 可用 {row.width()}px '
            f'—— 又有按钮被裁在窗口外')


class TestStructuralButtonsRestored:
    """
    5 个结构按钮**回到动作条上**，窄了折行、不藏菜单也不裁出窗口。

    用户 2026-10-30 第 6 条：**"不要那个结构！恢复回原来的 UI 先"** ——
    第一版把它们收进「⋯ 结构」下拉菜单的写法整个否掉。
    """

    #: 动作条上的顺序（与恢复前完全一致：插入 / 插到前面 / 控制流 / 选择点 /
    #: 撤销 / 删除）
    EXPECTED = ('＋ 插入卡片', '＋ 插到前面', '↳ 插入控制流卡',
                '◇ 插入选择点', '↩ 撤销结构操作', '🗑 删除这张卡片')

    def test_the_six_buttons_are_back_on_the_bar(self, ui_window):
        win = _card_page(ui_window)
        shown = []
        for name in ('_insert_btn', '_insert_before_btn', '_flow_btn',
                     '_choice_btn', '_restore_btn', '_delete_btn'):
            btn = getattr(win, name)
            assert btn.isVisibleTo(win), f'{name} 不在动作条上（又被藏起来了）'
            assert btn.parentWidget() is win._action_row, (
                f'{name} 的爹不是折行按钮带 —— 它是被塞进菜单了吗')
            shown.append(btn.text())
        assert tuple(shown) == self.EXPECTED, shown

    def test_there_is_no_structure_menu_any_more(self, ui_window):
        """「⋯ 结构」这颗按钮和它的菜单**都不许再存在**（用户明确不要）。"""
        win = _card_page(ui_window)
        for dead in ('_structural_btn', '_structural_menu', '_structural_actions',
                     '_structural_buttons_host'):
            assert not hasattr(win, dead), f'{dead} 还在 —— 菜单没删干净'
        texts = [w.text() for w in win.findChildren(QToolButton)]
        assert '⋯ 结构' not in texts, texts

    def test_a_click_still_builds_a_card(self, ui_window):
        """按钮回到动作条后**仍然真的干活**（不是只画了个样子）。"""
        win = _card_page(ui_window)
        win._show_index(2)
        before = len(win._session.beats)
        win._insert_btn.click()
        for _ in range(3):
            QApplication.processEvents()
        assert len(win._session.beats) == before + 1, '「＋ 插入卡片」没建出新卡'

    def test_the_bands_wraps_instead_of_clipping(self, ui_window):
        """
        窗口窄到一行放不下 → **折行**（按钮全在），不是裁在窗口外。

        判据只看"每颗按钮的矩形是否都落在动作条里面"，
        所以字体/间距差异不会让这条变成假失败。
        """
        win = _card_page(ui_window, (900, 820))
        bar = win._back_btn.parentWidget()
        band = win._action_row
        assert band.height() >= band.sizeHint().height(), '按钮带高度不够'
        for name in ('_insert_btn', '_insert_before_btn', '_flow_btn',
                     '_choice_btn', '_restore_btn', '_delete_btn'):
            btn = getattr(win, name)
            top_left = btn.mapTo(bar, QPoint(0, 0))
            assert top_left.x() >= 0, f'{name} 跑到动作条左边外面了'
            assert top_left.x() + btn.width() <= bar.width() + 1, (
                f'{name} 的右边缘 {top_left.x() + btn.width()}px 超出动作条 '
                f'{bar.width()}px —— 被裁在窗口外')
            assert top_left.y() + btn.height() <= bar.height() + 1, (
                f'{name} 的底边超出动作条高度（折行时高度没跟上）')

    def test_the_band_wraps_to_two_lines_when_narrow(self, ui_window):
        """900px 下确实是**两行以上**（一行放不下 6 颗），且按钮带跟着变高。"""
        win = _card_page(ui_window, (900, 820))
        band = win._action_row
        rows = {getattr(win, name).mapTo(band, QPoint(0, 0)).y()
                for name in ('_insert_btn', '_insert_before_btn', '_flow_btn',
                             '_choice_btn', '_restore_btn', '_delete_btn')}
        assert len(rows) >= 2, f'900px 下 6 颗按钮还挤在一行（y={sorted(rows)}）'
        assert band.height() > band.sizeHint().height() + 8, (
            f'折行了但按钮带没变高（高 {band.height()}、单行 {band.sizeHint().height()}）')

    def test_the_band_stays_one_line_when_wide(self, ui_window):
        """1360px 下**就是原来那一行**（这条守住"恢复回原来的 UI"）。"""
        win = _card_page(ui_window, (1360, 820))
        band = win._action_row
        rows = {getattr(win, name).mapTo(band, QPoint(0, 0)).y()
                for name in ('_insert_btn', '_insert_before_btn', '_flow_btn',
                             '_choice_btn', '_restore_btn', '_delete_btn')}
        assert len(rows) == 1, f'1360px 下按钮折行了（y={sorted(rows)}）'


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


# ==================================================================
#  ⑤ 老记忆迁移：默认改窄必须**真的生效**（用户第 6 条）
# ==================================================================
#
# 真机（`%LOCALAPPDATA%/StoryGraph/session.json`，2026-10-30 19:39）存的是
#   geometry = [1440, 830, 0, 23]、split_card = [402, 813]
# —— 窗口 1440 宽、卡片栏 813（67%）。记忆**优先于默认值**，所以"默认 1600 → 1360"
# 对老用户完全不可见：还是 1440 宽 + 卡片铺满。
#
# 20:28 又变成 `[1392, 804]`（version 已经是 2）：迁移**跑过一次**了，但用户随手
# 把窗口拖大/最大化，退出时又把大尺寸存了回去 —— 下次启动"还是这么大"。
# 所以现在的规则是：**窗口大小每次恢复都压**（不看版本号），
# 卡片栏迁移才是一次性的。

@pytest.fixture
def big_screen(monkeypatch):
    """
    把"屏幕"换成用户那块 **1440×852**（可用区 -48 边距）。

    为什么需要：offscreen 平台的屏幕只有 800×800，`fit_to_available` 会把
    1200 的窗口压成 752 —— 于是 e2e 判据分不清"上限生效"和"被屏幕压小"，
    假绿。
    """
    from storygraph.ui_qt import window_state as WS

    def fake_fit(w, h, available=None, margin=48):
        return (min(int(w), 1440 - 48), min(int(h), 852 - 48))

    monkeypatch.setattr(WS, 'fit_to_available', fake_fit)
    return fake_fit


class TestLegacyLayoutMigration:
    """布局记忆 → 窄默认（`window_state.narrow_layout`）。"""

    def _ws(self):
        from storygraph.ui_qt import window_state as WS
        return WS

    def test_an_old_wide_layout_is_pressed_into_the_narrow_defaults(self):
        WS = self._ws()
        layout = {'geometry': [1440, 830, 0, 23], 'split_card': [402, 813]}
        got = WS.narrow_layout(layout)
        assert got['geometry'][:2] == list(MW.DEFAULT_WINDOW_SIZE), (
            f"老记忆的窗口没被压到默认窄：{got['geometry']}")
        assert list(got['split_card']) == list(MW.CARD_SPLIT_DEFAULT), (
            f"卡片栏 813/1215=67% 的旧摆法没被换回默认窄：{got['split_card']}")
        assert got[WS.LAYOUT_VERSION_KEY] == WS.LAYOUT_VERSION
        # **不改调用方那份 dict**（只返回新的一份）
        assert layout['geometry'] == [1440, 830, 0, 23]
        assert layout['split_card'] == [402, 813]

    def test_a_versioned_big_window_is_still_capped(self):
        """
        打过版本号**也**要压窗口 —— 用户第 6 条反馈的直接判据。

        "打开已经打开过的项目还是这么大"：第一次迁移之后用户把窗口拖大/最大化
        过一次，退出时 `1392×804` 就带着 `layout_version=2` 存回去了。窗口大小
        不能再给版本号留口子：记忆只用来把窗口**记小**，不用来记大。

        ⚠ 第 7 条之后（`LAYOUT_VERSION = 3`）：**对半的卡片栏也算旧摆法**。
        用户记忆里正是 `[583, 584]` = 50%，卡在旧的 60% 门槛下面，上一轮
        **一次都没迁移过** —— 所以他才说"还是一模一样，都说了要变窄一些"。
        """
        WS = self._ws()
        layout = {'geometry': [1392, 804, 0, 23], 'split_card': [583, 584],
                  WS.LAYOUT_VERSION_KEY: 2}
        got = WS.narrow_layout(layout)
        assert got['geometry'][:2] == list(MW.DEFAULT_WINDOW_SIZE), (
            f'版本号已是 2 的大窗口没被压：{got["geometry"]}')
        assert got['geometry'][2:] == [0, 23], got['geometry']
        assert list(got['split_card']) == list(MW.CARD_SPLIT_DEFAULT), (
            f'对半（50%）的卡片栏没被压成默认窄：{got["split_card"]}')
        assert layout['geometry'] == [1392, 804, 0, 23]

    def test_a_smaller_window_is_not_blown_up(self):
        """记忆比默认更小 → 尊重它（"压上限"不是"每次开都放大"）。"""
        WS = self._ws()
        got = WS.narrow_layout({'geometry': [980, 700, 10, 10]})
        assert got['geometry'] == [980, 700, 10, 10], got['geometry']

    def test_a_versioned_split_is_left_alone(self):
        """卡片栏的迁移只做一次：用户自己拖的比例之后一直尊重。"""
        WS = self._ws()
        layout = {'split_card': [700, 900],
                  WS.LAYOUT_VERSION_KEY: WS.LAYOUT_VERSION}
        got = WS.narrow_layout(layout)
        assert got['split_card'] == [700, 900], got['split_card']

    def test_a_moderate_saved_split_is_kept(self):
        """已经拖窄的比例（30%、40%）不算旧摆法，不要乱动。"""
        WS = self._ws()
        for narrow in ([700, 300], [600, 400]):
            got = WS.narrow_layout({'split_card': list(narrow)})
            assert got['split_card'] == list(narrow), (narrow, got['split_card'])

    def test_junk_layout_does_not_explode(self):
        WS = self._ws()
        for junk in (None, {}, '不是字典', {'geometry': ['x', 'y']},
                     {'split_card': []}):
            got = WS.narrow_layout(junk)
            assert isinstance(got, dict)

    def test_the_version_is_written_back_on_save(self, ui_window):
        """存记忆时打上版本号 —— 否则每次启动都会被压一次（用户拖宽也不记住）。"""
        WS = self._ws()
        win = _card_page(ui_window, (1360, 820))
        layout = win.current_layout()
        assert layout.get(WS.LAYOUT_VERSION_KEY) == WS.LAYOUT_VERSION, (
            f'`current_layout()` 没写 layout_version：{sorted(layout)}')

    def test_reopening_an_old_project_really_narrows_the_window(self, ui_window,
                                                               tmp_path,
                                                               big_screen):
        """
        **端到端**：写一份"老摆法"的记忆 → `reopen_project()` → 窗口真的变窄。

        这条是用户那句话的直接判据（"并没有做到缩小"）：只测 `narrow_layout`
        的纯函数不够 —— 它必须真的接在启动恢复的那条路上。
        """
        from storygraph.core import session_store as SS

        win = _card_page(ui_window, (1360, 820))
        store = tmp_path / 'session.json'
        root = str(win._project_root)
        # ⚠ 直接写文件（不走 `remember_project`）：测试环境 `STORYGRAPH_NO_SESSION=1`
        #   时写盘是被关掉的，而这里要的正是"盘上有一份老记忆"。
        key = __import__('os').path.abspath(root).replace('\\', '/')
        store.write_text(json.dumps({
            'version': SS.VERSION, 'last_project': root, 'auto_open': True,
            'recent': [root],
            'projects': {key: {'project_name': '老项目',
                               'geometry': [1440, 830, 0, 23],
                               'split_card': [402, 813]}},
        }, ensure_ascii=False), encoding='utf-8')
        win.session_store_path = lambda: store
        win.resize(1440, 830)
        for _ in range(4):
            QApplication.processEvents()

        win.reopen_project(root)
        for _ in range(8):
            QApplication.processEvents()

        assert win.width() <= MW.DEFAULT_WINDOW_W + 40, (
            f'老记忆 1440 宽 → 恢复后还是 {win.width()}px（用户："没有缩小"）')
        right = int(win._split_card.sizes()[1])
        assert right <= 480, (
            f'老记忆里卡片栏 813px（67%）→ 恢复后 {right}px，还是"卡片铺满"')

    def test_reopening_with_a_versioned_big_window_still_opens_small(
            self, ui_window, tmp_path, big_screen):
        """
        **端到端（用户真机现状）**：记忆里是 `1392×804` 且 `layout_version=2`
        —— 也就是"迁移过一次、又被用户拖大存回去"的那种 —— 再打开仍然窄。

        这条钉的是"先把窗口拖大再重启"这个循环：只要还能存回大尺寸，
        用户就会一直看到"打开还是这么大"。
        """
        from storygraph.core import session_store as SS

        win = _card_page(ui_window, (1360, 820))
        store = tmp_path / 'session.json'
        root = str(win._project_root)
        key = __import__('os').path.abspath(root).replace('\\', '/')
        store.write_text(json.dumps({
            'version': SS.VERSION, 'last_project': root, 'auto_open': True,
            'recent': [root],
            'projects': {key: {'project_name': '拖大过一次的项目',
                               'geometry': [1392, 804, 0, 23],
                               'split_card': [583, 584],
                               'layout_version': 2}},
        }, ensure_ascii=False), encoding='utf-8')
        win.session_store_path = lambda: store
        win.resize(1392, 804)
        for _ in range(4):
            QApplication.processEvents()

        win.reopen_project(root)
        for _ in range(8):
            QApplication.processEvents()

        assert win.width() <= MW.DEFAULT_WINDOW_W + 40, (
            f'记忆里 1392×804（version=2）→ 打开还是 {win.width()}px'
            '（用户："打开已经打开过的项目还是这么大"）')
        assert win.height() <= MW.DEFAULT_WINDOW_H + 40, win.height()
        right = int(win._split_card.sizes()[1])
        assert right <= 640, (
            f'窗口压窄了但卡片栏还有 {right}px（50:50 在 1200 窗口下约 600）')

    def test_the_remembered_split_is_scaled_not_clamped(
            self, ui_window, tmp_path, big_screen):
        """
        **端到端**：记忆里是个**窄**比例（`[700,400]`，右栏 36%），窗口从 1360
        被压到 1200 后，两栏必须**还是那个比例**（不能被 Qt 从右边硬砍）。

        老实现直接 `setSizes([700,400])`：和 1100 > 可用宽时 Qt 从右边砍，
        实测摆成 `[545,357]` —— 用户看到的是"我摆的 36%，怎么卡片只剩三分之一"。

        ⚠ 用 36% 而不是对半：`LAYOUT_VERSION = 3` 起，**≥45% 的旧摆法会被迁移**
        成默认窄栏（用户第 7 条），对半不再走"按比例还原"这条路。
        """
        from storygraph.core import session_store as SS

        win = _card_page(ui_window, (1360, 820))
        store = tmp_path / 'session.json'
        root = str(win._project_root)
        key = __import__('os').path.abspath(root).replace('\\', '/')
        store.write_text(json.dumps({
            'version': SS.VERSION, 'last_project': root, 'auto_open': True,
            'recent': [root],
            'projects': {key: {'project_name': '拖窄过的项目',
                               'geometry': [1360, 820, 0, 23],
                               'split_card': [700, 400],
                               'layout_version': 3}},
        }, ensure_ascii=False), encoding='utf-8')
        win.session_store_path = lambda: store
        for _ in range(4):
            QApplication.processEvents()

        win.reopen_project(root)
        for _ in range(8):
            QApplication.processEvents()

        left, right = (int(n) for n in win._split_card.sizes())
        share = right / max(1, left + right)
        assert abs(share - 400 / 1100) <= 0.04, (
            f'记忆里右栏占 36%，摆出来 {left}:{right}（{share:.0%}）—— 比例没保住')

    def test_absolute_pixels_are_scaled_to_the_current_width(self):
        from storygraph.ui_qt.window_state import WindowStateMixin as WSM
        got = WSM._scale_sizes([583, 584], 975)
        assert got == [487, 488], got
        assert sum(got) == 975
        # 已经放得下（和 ≤ 当前宽）也要等比缩，不能原样塞回去
        assert WSM._scale_sizes([200, 200], 400) == [200, 200]
        # 垃圾输入不炸
        for junk, avail in (([], 100), (['x', 1], 100), ([0, 0], 100),
                            ([1, 1], 0)):
            WSM._scale_sizes(junk, avail)

