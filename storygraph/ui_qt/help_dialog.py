"""
storygraph.ui_qt.help_dialog
============================
**独立的「说明」窗口** —— 这是用户 2026-09-30 明确要求的那一条：

> 那堆操作提示，另找个说明窗口放，不要堆在那里……

以前操作提示**常年堆在主界面右列**，占据黄金位置（实测右列请求高度
975px > 可视 600px，全是靠滚）。现在说明独立成窗：

  · 工具栏 ❓ 一键打开，**不占主界面**；
  · 非模态（开着也能回去改卡片）；
  · **分页**：上手（怎么编辑）/ 快捷键 / 保存与落盘 / 图与预览。
    一大段文字没人读，分页以后"找那一条"变成"点那一页"。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from storygraph.ui import tokens as T
from storygraph.ui_qt import theme

#: 快捷键表：(按键, 做什么)。**与 main_window.eventFilter 保持一致** ——
#: 这份表是给用户看的，说错了比不说更糟。
SHORTCUTS = [
    ('Enter', '提交当前拍，并翻到下一拍'),
    ('↓', '下一拍（在「台词」多行框里、光标不在末尾时是移动光标）'),
    ('↑', '上一拍（同上：光标不在开头时才翻页）'),
    ('Alt + ↓', '下一拍（无论焦点在哪都翻页）'),
    ('Alt + ↑', '上一拍（无论焦点在哪都翻页）'),
    ('Ctrl + S', '保存这一拍（提交到内存，还没写文件）'),
    ('空格（预览里）', '播放 / 暂停'),
    ('← / →（预览里）', '上一句 / 下一句'),
    ('Home / End（预览里）', '跳到第一拍 / 最后一拍'),
    ('Ctrl + 滚轮（图上）', '缩放'),
    ('中键 / 空格 + 左键拖动（图上）', '平移画布'),
]

_EDIT = """每**一拍**是一张卡片。卡片上真正常改的就四样：

  · 说话人 / 台词 —— 顶部那两栏（台词是这张卡的主角，给了最大的框）；
  · 表情 / 位置 —— 在**立绘卡**上的两个 chip，点一下就换；
  · 背景 / 音乐 / 语音 —— 中部的三个下拉框；
  · 备注 —— 给同事 / 程序看的说明（**不进脚本**）。

其它字段（代码块 / Label / 类型 / Notes / Stage）都在**可折叠的高级区**里。

【立绘卡】
  · 双击一张卡 = 换图；点 chip = 换表情 / 位置；
  · 「＋ 添加立绘」**先选图再建卡**（取消就什么都不加，不会留空卡）；
  · 同一角色出现多次（换表情）会聚合成一张卡，**数据不会丢**。

【左栏】
  · 上半是**章节（拍）**列表，点一行就切过去；
  · 下半是**参与角色**，双击一个名字 = 填进这一拍的说话人。
    它只是"参考"，不会在翻页时自动帮你填（那会把上一句的说话人
    悄悄写进下一拍 —— 用户报过这个 bug）。
"""

_SAVE = """【改动的三步生命周期】

    1. 你在卡片上改 —— 还在控件里，什么都没发生；
    2. 💾 保存这一拍（Ctrl+S）—— 提交到**内存里的这一拍**；
       状态栏右下角变成「● 未导出」；
    3. ⤓ 导出 .rpy —— 才真的写文件。

改完直接导出也行（导出前会自动提交当前拍）。

【导出走哪条路】
  · 优先 **增量补丁**：只替换真正改过的行，保留你手写的注释与排版；
  · 对不上位（改动太大 / 结构变了）就退回**全量重生成**，
    状态栏会写明为什么退的 —— 不会悄悄换一条路。
  · 导出**必须过生成门禁**：命中 block 级问题（中文图像名、
    产物落进 game/ 时 label 重名）就**不写目标路径**，改写
    `<名字>.blocked.rpy`，以免把能跑的工程写坏。

【找不到资源怎么办】
  会写成 `# TODO: 缺少资源 …（是不是想写 …？）` 并且**不生成**那条非法语句。
  这是刻意的：宁可缺一句让人看见，也不要写出一条跑不起来的脚本。
"""

_GRAPH = """【块状编辑器（中央）】

  一张块 = 一个章节（label），一条线 = 一次跳转/调用。
  用来一眼看清"谁跳谁、菜单在哪分叉"。

  · 双击块 → 右侧卡片界面打开**那一章**；
  · 右键块 → 在卡片里打开 / 设为当前章节 / **编辑变量操作** / 连一条线 / 删除；
  · 右键空白 → 添加新章节 / 添加连线 / 自动布局 / 适应窗口 / 重新扫描；
  · 卡片正在编辑的那一章，图上对应的块会**高亮**。

  连线的三种形态（对话框里按你填的东西自动判断）：
    · 只填「菜单项」→ 分支（menu）；
    · 只填「条件」→ 条件跳转；
    · 都不填 → 直接 jump / call。
  连到自己会被挡住（那是死循环）。

【演出预览（🎬）】

  把这一章按卡片效果"跑"出来：背景 / 立绘 / 站位 / 说话人高亮 / 名条 / 对话框。
  · 「真实贴图」关掉就用占位方块（大项目里翻页更快）；
  · 「只看有变化的拍」—— 几百拍里快速过一遍真正切了景、换了人的地方；
  · 缺图自动降级成方块并写明名字，**绝不留一片黑**。

【体检与分析（只读）】
  · 🔍 项目体检：三级校验，**不写盘**；
  · 📊 变量分析：变量区间可达性 / 死代码 / 独立入口，可过滤可复制。
"""


def _page(title: str, body: str, parent=None) -> QWidget:
    """一页说明：标题 + 可滚动的正文。"""
    w = QWidget(parent)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                           T.SPACE['sm'], T.SPACE['sm'])
    lay.setSpacing(T.SPACE['xs'])

    cap = QLabel(title)
    cap.setProperty('role', 'section')
    lay.addWidget(cap)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    inner = QWidget()
    il = QVBoxLayout(inner)
    il.setContentsMargins(0, 0, 0, 0)
    text = QLabel(body)
    text.setWordWrap(True)
    text.setTextInteractionFlags(Qt.TextSelectableByMouse)
    text.setFont(QFont(theme.font_family(), theme.font_pt('body')))
    text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    il.addWidget(text)
    il.addStretch(1)
    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)
    return w


class HelpDialog(QDialog):
    """使用说明（非模态，开着也能继续改卡片）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('使用说明')
        self.resize(680, 660)
        theme.apply_to(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                               T.SPACE['md'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])

        title = QLabel('剧本编辑器 · 使用说明')
        f = QFont(theme.font_family(), theme.font_pt('panelTitle'))
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        sub = QLabel('主界面只留"这一拍要填的东西"；说明、快捷键都在这儿。')
        sub.setProperty('role', 'caption')
        lay.addWidget(sub)

        tabs = QTabWidget()
        tabs.addTab(_page('怎么编辑这一拍', _EDIT, self), '上手')
        tabs.addTab(self._shortcut_page(), '快捷键')
        tabs.addTab(_page('保存、导出与落盘时机', _SAVE, self), '保存与导出')
        tabs.addTab(_page('块状编辑器与演出预览', _GRAPH, self), '图与预览')
        lay.addWidget(tabs, 1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        close = QPushButton('知道了')
        close.setProperty('variant', 'primary')
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        lay.addLayout(bar)

    def _shortcut_page(self) -> QWidget:
        w = QWidget(self)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                               T.SPACE['sm'], T.SPACE['sm'])
        lay.setSpacing(2)
        cap = QLabel('键盘与鼠标')
        cap.setProperty('role', 'section')
        lay.addWidget(cap)
        for keys, what in SHORTCUTS:
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(0)
            k = QLabel(keys)
            kf = QFont(theme.font_family(), theme.font_pt('bodyStrong'))
            kf.setBold(True)
            k.setFont(kf)
            d = QLabel(what)
            d.setProperty('role', 'note')
            d.setWordWrap(True)
            rl.addWidget(k)
            rl.addWidget(d)
            lay.addWidget(row)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(w)
        holder = QWidget(self)
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(scroll)
        return holder


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


__all__ = ['HelpDialog', 'show_help', 'SHORTCUTS']
