"""
storygraph.ui_qt.theme
======================
Qt 版的**主题唯一真源**：把 `ui/tokens.py` 翻译成 Qt StyleSheet（QSS）。

为什么不是直接抄一份颜色：项目契约是"界面代码**不许写死**色值/字号"，
tkinter 版遵守它（都走 `ui.tokens`），Qt 版也必须遵守 ——
所以这里**只读取** `ui.tokens`，一个真源、两套界面。
主题切换（light/dark）也因此天然跟着走（`tokens` 本来就是两套）。
"""

from __future__ import annotations

from storygraph.ui import tokens as T


def t(name: str) -> str:
    """取一个 token 色值（与 tkinter 版同一个函数，**不复制数值**）。"""
    return T.t(name)


def _font(name: str) -> tuple:
    """`('微软雅黑', 9, 'bold')` 的规范化（缺省按 body）。"""
    spec = T.FONTS.get(name) or T.FONTS['body']
    return (spec[0], int(spec[1]),
            'bold' if len(spec) > 2 and 'bold' in str(spec[2]) else 'normal')


def font_family() -> str:
    return T.FONTS['body'][0]


def font_pt(name: str) -> int:
    return _font(name)[1]


def font_weight(name: str) -> str:
    return _font(name)[2]


def qss() -> str:
    """整份应用的 QSS（主窗口 `setStyleSheet(qss())` 一次到位）。"""
    c = lambda k: t(k)
    f = font_family()
    return f"""
* {{ font-family: "{f}"; color: {c('text.primary')}; }}

QMainWindow, QDialog, QWidget {{ background: {c('bg.canvas')}; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
    background: {c('bg.canvas')};
    color: {c('text.primary')};
    border: 1px solid {c('border.default')};
    border-radius: {T.RADIUS['control']}px;
    padding: 4px 6px;
    selection-background-color: {c('listbox.selectBg')};
    selection-color: {c('listbox.selectFg')};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border: 1px solid {c('brand.primary')};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QComboBox:disabled {{
    background: {c('bg.disabled')};
    color: {c('text.tertiary')};
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {c('bg.surface')};
    color: {c('text.primary')};
    border: 1px solid {c('border.default')};
    border-radius: {T.RADIUS['control']}px;
    padding: 5px 12px;
}}
QPushButton:hover {{ background: {c('bg.cardHover')}; }}
QPushButton:pressed {{ background: {c('brand.subtle')}; }}
QPushButton:disabled {{ color: {c('text.tertiary')}; background: {c('bg.disabled')}; }}
QPushButton[variant="primary"] {{
    background: {c('brand.primary')}; color: {c('brand.onPrimary')};
    border: 1px solid {c('brand.primary')};
}}
QPushButton[variant="primary"]:hover {{ background: {c('brand.primaryHover')}; }}
QPushButton[variant="danger"] {{
    background: {c('state.error')}; color: {c('brand.onPrimary')};
    border: 1px solid {c('state.error')};
}}

/* ---------- 列表 ---------- */
QListWidget {{
    background: {c('bg.canvas')};
    border: 1px solid {c('border.default')};
    border-radius: {T.RADIUS['control']}px;
    outline: 0;
}}
QListWidget::item {{ padding: 3px 6px; }}
QListWidget::item:selected {{
    background: {c('listbox.selectBg')}; color: {c('listbox.selectFg')};
}}
QListWidget::item[role="speaking"] {{ font-weight: bold; }}

/* ---------- 停靠区 / 工具栏 / 状态栏 ---------- */
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{
    background: {c('bg.surface')}; color: {c('text.secondary')};
    padding: 4px 8px; border: 1px solid {c('border.default')};
}}
QToolBar {{
    background: {c('bg.surface')}; border: none; spacing: 4px; padding: 4px;
}}
QToolBar QToolButton {{
    background: transparent; border: 1px solid transparent;
    border-radius: {T.RADIUS['control']}px; padding: 4px 8px;
}}
QToolBar QToolButton:hover {{ background: {c('bg.cardHover')}; }}
QStatusBar {{ background: {c('bg.surface')}; color: {c('text.secondary')}; }}

/* ---------- 分隔 / 标签 ---------- */
QLabel[role="section"] {{
    color: {c('text.secondary')}; font-weight: bold;
    border-bottom: 1px solid {c('border.default')}; padding-bottom: 3px;
}}
QLabel[role="caption"] {{ color: {c('text.tertiary')}; }}
QLabel[role="note"] {{ color: {c('text.secondary')}; }}

/* ---------- 立绘卡 ---------- */
QFrame[role="spriteCard"] {{
    background: {c('bg.card')};
    border: 1px solid {c('border.default')};
    border-radius: {T.RADIUS['card']}px;
}}
QFrame[role="spriteCard"][speaking="true"] {{
    background: {c('brand.subtle')};
    border: 1px solid {c('border.strong')};
}}
QFrame[role="spriteCard"]:hover {{ background: {c('bg.cardHover')}; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: {c('scroll.trough')}; width: 10px; margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {c('scroll.thumb')}; min-height: 24px; border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: {c('scroll.thumbHover')}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none; border: none; height: 0;
}}
QScrollBar:horizontal {{
    background: {c('scroll.trough')}; height: 10px; margin: 0; border: none;
}}
QScrollBar::handle:horizontal {{
    background: {c('scroll.thumb')}; min-width: 24px; border-radius: 5px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none; border: none; width: 0;
}}
"""


def apply_to(widget):
    """给控件树应用整份 QSS。"""
    try:
        widget.setStyleSheet(qss())
    except Exception:                        # noqa: BLE001
        pass
