"""
storygraph.ui_qt.help_dialog
============================
**独立的「说明」窗口** —— 这是用户 2026-09-30 明确要求的那一条：

> 那堆操作提示，另找个说明窗口放，不要堆在那里……

以前操作提示**常年堆在主界面右列**，占据黄金位置。现在说明独立成窗：
  · 工具栏 ❓ 一键打开，**不占主界面**；
  · 非模态（开着也能回去改卡片）；
  · 内容 = 快捷键 + 每块区域是干什么的 + 落盘时机。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from storygraph.ui import tokens as T
from storygraph.ui_qt import theme

_GUIDE = """\
【这一章怎么编辑】

每**一拍**是一张卡片。卡片上真正常改的就四样：
  · 说话人 / 台词 —— 顶部那两栏；
  · 表情 / 位置 —— 在**立绘卡**上的两个 chip（点一下就换）；
  · 背景 / 音乐 / 语音 —— 中部的下拉框；
  · 备注 —— 给同事/程序看的说明（不进脚本）。

其它（代码块 / Label / 类型 / Notes / 舞台）都在**可折叠的高级区**。

【立绘卡】
  · 双击一张卡 = 换图；点 chip = 换表情 / 位置；
  · 「＋ 添加立绘」**先选图再建卡**（取消就什么都不加）；
  · 同一角色出现多次（换表情）会聚合成一张卡，数据不会丢。

【翻页】
  · Enter / ↓ / Alt+↓ = 下一拍；↑ / Alt+↑ = 上一拍；
  · **翻页是纯导航**：只提交当前拍，不改别的拍；
  · 在多行「台词」框里，↓/↑ 是移动光标（要翻页先把焦点点出来）。

【保存与落盘】
  · 💾 保存这一拍（Ctrl+S）：把这张卡片的改动提交到**当前这一拍**；
  · 改动**先在内存里**，点「⤓ 导出 .rpy」时才写进文件；
  · 导出优先走**增量补丁**（只换真正改过的行，保留注释）；
    改不了增量时退回全量，状态栏会写明为什么。

【跳错与体检】
  · 找不到的资源会写成 `# TODO: 缺少资源 …（是不是想写 …？）`；
  · 「项目体检」只做检查、**不写盘**。
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('使用说明')
        self.resize(620, 640)
        theme.apply_to(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                               T.SPACE['md'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])

        title = QLabel('剧本表编辑器 · 使用说明')
        f = QFont(theme.font_family(), theme.font_pt('panelTitle'))
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                              T.SPACE['sm'], T.SPACE['sm'])
        text = QLabel(_GUIDE)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bf = QFont(theme.font_family(), theme.font_pt('body'))
        text.setFont(bf)
        bl.addWidget(text)
        bl.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        close = QPushButton('关闭')
        close.setProperty('variant', 'primary')
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignRight)


_help_window = None


def show_help(parent=None):
    """打开（或聚焦）说明窗口（非模态，开着也能回去改）。"""
    global _help_window
    if _help_window is None or not _help_window.isVisible():
        _help_window = HelpDialog(parent)
        _help_window.show()
    else:
        _help_window.raise_()
        _help_window.activateWindow()
    return _help_window
