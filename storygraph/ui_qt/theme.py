"""
storygraph.ui_qt.theme
======================
Qt 版的**主题唯一真源**：把 `ui_qt/tokens.py` 翻译成 Qt StyleSheet（QSS）。

为什么不是直接抄一份颜色：项目契约是"界面代码**不许写死**色值/字号"，
所以这里**只读取** `ui_qt.tokens`，一个真源、一处修改。
主题切换（light/dark）也因此天然跟着走（`tokens` 本来就是两套）。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T


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
    """
    整份应用的 QSS（主窗口 `setStyleSheet(qss())` 一次到位）。

    按"看得到的块"分六段（2026-11-01）：全局 / 输入 / 按钮 / 容器与列表 /
    面板与卡片 / 滚动条。**拼接顺序就是 QSS 的层叠顺序** —— 后写的规则
    覆盖前面的（按钮那三个 variant 的 disabled 就靠这个），别随便挪段。
    """
    c = lambda k: t(k)
    return ''.join((_qss_base(c), _qss_inputs(c), _qss_buttons(c),
                    _qss_containers(c), _qss_panels(c),
                    _qss_scrollbars(c)))


def _qss_base(c) -> str:
    """全局：字体、前景色、窗口底色（必须是第一段，后面每段都叠在它上面）。"""
    f = font_family()
    return f"""
* {{ font-family: "{f}"; color: {c('text.primary')}; }}

QMainWindow, QDialog, QWidget {{ background: {c('bg.canvas')}; }}
"""


def _qss_inputs(c) -> str:
    """输入控件：输入框 / 多行文本 / 下拉 / 数字（含 focus 与 disabled 两种态）。"""
    return f"""
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
"""


def _qss_buttons(c) -> str:
    """按钮：普通 / primary / danger 三个变体。
    
    变体规则必须排在 `:disabled` **之后**（段内那条注释说的就是这件事）。"""
    return f"""
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
/* ⚠ 变体规则排在 `:disabled` 后面会把它盖掉 —— 于是"按不动的主按钮"
   看起来和能按的一模一样（用户点了没反应）。这里给变体补一条灰化。 */
QPushButton[variant="primary"]:disabled {{
    background: {c('bg.disabled')}; color: {c('text.tertiary')};
    border: 1px solid {c('border.default')};
}}
QPushButton[variant="danger"] {{
    background: {c('state.error')}; color: {c('brand.onPrimary')};
    border: 1px solid {c('state.error')};
}}
QPushButton[variant="danger"]:disabled {{
    background: {c('bg.disabled')}; color: {c('text.tertiary')};
    border: 1px solid {c('border.default')};
}}
"""


def _qss_containers(c) -> str:
    """容器与列表：列表 / 停靠区 / 工具栏 / 状态栏 / 分隔与标签 / 立绘卡 / Toast。"""
    return f"""
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
/* 「有未保存的改动」/ 台词超长：用警示色，但**不抢眼到像报错** */
QLabel[role="warn"] {{ color: {c('state.warning')}; }}

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

/* ---------- Toast（浮层提示）---------- */
/* 不参与布局：出现/消失都不推动任何控件，不打断正在输入的人。 */
QFrame[role="toast"] {{
    background: {c('toast.bg')};
    border: 1px solid {c('border.default')};
    border-radius: {T.RADIUS['card']}px;
}}
"""


def _qss_panels(c) -> str:
    """面板与卡片：页签 / 分隔条 / 复选框 / 卡片与表单 / 对话框外壳 / 提示气泡。"""
    return f"""
/* ---------- 页签（说明窗口）---------- */
QTabWidget::pane {{
    border: 1px solid {c('border.default')};
    border-radius: {T.RADIUS['control']}px;
    background: {c('bg.canvas')};
    top: -1px;
}}
QTabBar::tab {{
    background: {c('bg.surface')};
    color: {c('text.secondary')};
    border: 1px solid {c('border.default')};
    border-bottom: none;
    border-top-left-radius: {T.RADIUS['control']}px;
    border-top-right-radius: {T.RADIUS['control']}px;
    padding: 6px 14px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {c('bg.canvas')};
    color: {c('text.primary')};
    font-weight: bold;
}}
QTabBar::tab:hover {{ background: {c('bg.cardHover')}; }}

/* ---------- 分隔条 / 停靠区分隔 ---------- */
QSplitter::handle {{ background: {c('bg.surface')}; }}
QSplitter::handle:hover {{ background: {c('brand.subtle')}; }}

/* ---------- 复选框 ---------- */
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {c('border.default')};
    border-radius: 3px;
    background: {c('bg.canvas')};
}}
QCheckBox::indicator:checked {{
    background: {c('brand.primary')};
    border: 1px solid {c('brand.primary')};
}}

/* ---------- 卡片 / 表单（对话框里的"一格一张卡"）---------- */
/* 以前 `role=card` / `cardTitle` / `fieldLabel` 三个 role 只在代码里写着，
   QSS 里**一个都没有** —— 于是"卡片"其实只是白底上的一段文字，分不出块。
   这里补上：卡片有底色和描边，标题加粗，字段标签用次级色。 */
QFrame[role="card"] {{
    background: {c('bg.surface')};
    border: 1px solid {c('border.default')};
    border-radius: {T.RADIUS['card']}px;
}}
QFrame[role="card"][active="true"] {{
    border: 1px solid {c('brand.primary')};
}}
QLabel[role="cardTitle"] {{
    color: {c('text.primary')};
    font-weight: bold;
}}
QLabel[role="fieldLabel"] {{
    color: {c('text.secondary')};
}}
QLabel[role="stepTitle"] {{
    color: {c('text.primary')};
    font-weight: bold;
}}
/* 步骤序号：小圆牌。固定 20×20 由布局保证，这里只管长相。 */
QLabel[role="step"] {{
    background: {c('brand.subtle')};
    color: {c('brand.primary')};
    border-radius: 10px;
    font-weight: bold;
}}

/* ---------- 对话框外壳：标题区 / 动作条 ---------- */
QFrame[role="dialogHeader"] {{
    background: {c('bg.surface')};
    border: none;
    border-bottom: 1px solid {c('border.default')};
}}
QFrame[role="actionBar"] {{
    background: {c('bg.surface')};
    border: none;
    border-top: 1px solid {c('border.default')};
}}
QLabel[role="panelTitle"] {{
    color: {c('text.primary')};
    font-weight: bold;
    font-size: {T.FONTS['panelTitle'][1]}pt;
}}

/* ---------- 数字输入 ---------- */
QSpinBox {{ padding: 3px 4px; }}

/* ---------- 提示气泡 ---------- */
QToolTip {{
    background: {c('toast.bg')};
    color: {c('toast.fg')};
    border: 1px solid {c('border.default')};
    padding: 4px 6px;
}}
"""


def _qss_scrollbars(c) -> str:
    """滚动条：横竖两套（细轨 + 圆角滑块 + 隐藏箭头）。"""
    return f"""
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
        swallow('theme.apply_to')
