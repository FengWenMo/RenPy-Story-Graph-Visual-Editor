"""
storygraph.ui_qt.help_dialog
============================
**独立的「说明」窗口** —— 这是用户 2026-09-30 明确要求的那一条：

> 那堆操作提示，另找个说明窗口放，不要堆在那里……

以前操作提示**常年堆在主界面右列**，占据黄金位置（实测右列请求高度
975px > 可视 600px，全是靠滚）。现在说明独立成窗：

  · 工具栏 ❓ 一键打开，**不占主界面**；
  · 非模态（开着也能回去改卡片）；
  · **分页**：上手（先看图再看卡）/ 怎么编辑这一拍 / 快捷键 / 保存与导出 /
    图与预览。一大段文字没人读，分页以后"找那一条"变成"点那一页"。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme

#: 快捷键表：(按键, 做什么)。**与 main_window.eventFilter 保持一致** ——
#: 这份表是给用户看的，说错了比不说更糟。
SHORTCUTS = [
    ('Shift + 左键拖动（图上）', '从一个块拖到另一个块 = 连线'),
    ('左键（图上）', '选中块；按住拖动 = 移动位置'),
    ('双击块（图上）', '编辑这个块的剧情变量'),
    ('双击空白（图上）', '在这里新建一个章节块'),
    ('滚轮（图上）', '缩放（不用按 Ctrl）'),
    ('空格 + 左键拖动 / 中键拖动（图上）', '平移画布'),
    ('Enter（剧情结构页）', '进选中模块的卡片（= 右栏「✎ 打开本章剧本」）'),
    ('Delete（剧情结构页）', '删掉选中的块（只改内存里的图，不动文件）'),
    ('Esc（卡片页）', '回剧情结构图'),
    ('Enter（卡片页）', '提交当前拍，并翻到下一拍'),
    ('↓', '下一拍（在「台词」多行框里、光标不在末尾时是移动光标）'),
    ('↑', '上一拍（同上：光标不在开头时才翻页）'),
    ('Alt + ↓', '下一拍（无论焦点在哪都翻页）'),
    ('Alt + ↑', '上一拍（无论焦点在哪都翻页）'),
    ('Ctrl + S', '保存这一拍（提交到内存，还没写文件）'),
    ('Ctrl + Z', '撤销这一拍的修改（回到上一次保存前）'),
    ('空格（预览里）', '播放 / 暂停'),
    ('← / →（预览里）', '上一句 / 下一句'),
    ('Home / End（预览里）', '跳到第一拍 / 最后一拍'),
]

_START = """界面是**两页**，顺序就是你要做事的顺序：

【第 0 页 · 剧情结构】（启动就是这一页）

  · 中间：**结构图** —— 一张块 = 一个 label（一章里可能有好几个），
    一条线 = 一次 jump / call / 菜单选项，线上写着条件或选项文字；
  · 右上：**控制台** —— 导入 / 打开项目 / 体检 / 分析的结果都写在这里
    （导入完会逐个文件告诉你"来了几个模块"，不用去猜）；
  · 右下：**当前模块** + 「✎ 打开本章剧本（改台词）」+「🧩 编辑剧情变量…」
    + 检查逻辑问题 / 变量分析。

  ⚠ 剧情图**只放你自己导入的章节**，不会自动把整个项目画出来。
    要加章节：工具栏「➕ 导入章节 .rpy」（**可多选，累加**）。
    想让"谁跳谁"看得全，就把相关的几章一起导入进来 ——
    连线两端都在图里才会画出来；只有一端时会先建一个
    标注「未导入的章节」的占位块，告诉你"这里还缺一个文件"。

【两条路不要混】

  · 「➕ 导入章节 .rpy」→ 改的是**剧情图**（放哪些章节、它们的跳转与条件）；
  · 「📁 打开项目」→ 只加载**资源表 xlsx**（立绘 / 音乐 / 语音 / 变换 /
    角色候选、缩略图路径），**它不往剧情图里放任何东西**。
    改了资源表之后用「🔄 重读资源表」；
    磁盘上的 .rpy 改过之后用「🔁 重载已导入章节」（范围不变）。

【第 1 页 · 卡片编辑】

  选中一个块 → 右栏「✎ 打开本章剧本（改台词）」（或右键 →「改本章台词（卡片）」）
  → 进入卡片页改**那一章**。
  左上「← 返回剧情结构」(Esc) 回第一屏；返回前会自动提交当前这一拍。
"""

_EDIT = """每**一拍**是一张卡片。卡片上真正常改的就四样：

  · 说话人 / 台词 —— 顶部那两栏（台词是这张卡的主角，给了最大的框）；
  · 表情 / 位置 —— 在**立绘卡**上的两个 chip，点一下就换；
  · 背景 / 音乐 / 语音 —— 中部的三个下拉框；
  · 备注 —— 给同事 / 程序看的说明（**不进脚本**）。

  · 台词框右下角会显示**字数**（不含空白）；超过 200 字转成警示色 ——
    那多半会在游戏里溢出对话框。

【当前舞台（这一拍生效）】

  台词框下面那一块会写明**这一拍舞台上是什么**：本拍变化 / 背景 / 立绘 /
  CG 图层 / 音乐 / 音效 / 语音 / 条件 / 备注，以及**继承上一拍的哪些**
  （Ren'Py 里背景、BGM、立绘是"状态"，不写就是沿用上一拍 —— 所以这里
  直接告诉你"现在实际是什么"，不必每拍重填）。

【增 / 删卡片】

  · 「＋ 插入卡片」= 在当前这一拍**后面**插一张空卡片（Ctrl+Insert）；
    「＋ 插到前面」= 插在前面。新卡是**空的**，不会把上一拍的背景 / 音乐
    复制过来（那会把"沿用上一拍"变成"这一拍重新设置"）；
  · 「↳ 插入控制流卡」= 在当前这一拍后面插一张「调用 / 跳转」卡：选目标
    label 即可。调用（call）执行完会回到这里继续；跳转（jump）不会返回。
    插入后卡片顶部会写明"返回后继续第几拍"；
  · 「🗑 删除这张卡片」= 删掉当前这一拍（Ctrl+Delete）。会先问一声，
    删完可以点「↩ 撤销结构操作」放回原位（插错了卡也能用它撤掉，可连按多级）；
  一章至少留一拍；
  · 删之前会写出**这一拍在产物里占几行、前两行是什么**：源码可能整段塞在
    「代码」那一格里，Excel 单元格只有一行高，滚动看不出来；选中一拍时
    面包屑上也有「（产物 N 行）」；
  · 这些改动**只在内存里**，"⤓ 导出"才落盘，所以要撤销还有退路。

【背景 / 音乐 / 语音怎么填】

  三格都可以直接打字，右边「选择…」会打开**资产选择器**（按分类 / 搜索 /
  带预览），候选来自你打开项目时载入的**资源表**。
  如果「选择…」提示"还没有资源表可用"，就是还没点「📁 打开项目」选项目根。
  「🔄 刷新下拉候选」= 资源表改过之后重读一遍候选。

【布局 JSON / 控制流导出 / 撤销（右栏「画布」那一组）】

  · 「💾 保存布局(JSON)」（Ctrl+S）：把图的**结构和每个块的位置**存成
    `.json` —— 下次「📂 加载布局」（Ctrl+O）就能原样回来，
    格式与旧版一致（旧文件也能直接读）；
  · 「⤓ 导出控制流 .rpy」：按图生成 Ren'Py 控制流脚本 —— 每个模块一个
    `<label>_control`，里面 `call` 原 label，再按连线写
    `jump` / `call` / `menu` / `if-elif-else`。（原 label 末尾要能正常
    `return`，否则控制流接不上。）
  · 「↩ 撤销 / ↪ 重做」（Ctrl+Z / Ctrl+Y）：结构页的图操作（新建 / 删除块、
    加 / 删连线、改变量、导入章节、清空图、加载布局）都能撤回来。
    注意**按页分工**：在卡片页按 Ctrl+Z 撤的是"这一拍的修改"。

其它字段（条件 / 代码块 / Label / 类型 / Notes / Stage）都在**可折叠的高级区**里。

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

【⤓ 导出在**两页**顶部都有】
  它导的是"已载入的那一章"，跟你在哪一页无关 —— 所以结构页顶部也放了
  一个（紧跟「🧩 自定义语句」）。不用先回卡片页，也不用去右栏翻。

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

_GRAPH = """【剧情结构图（第 0 页，中央）】

  一张块 = 一个 label（一个 .rpy 里可能有好几个），一条线 = 一次跳转/调用。
  用来一眼看清"谁跳谁、菜单在哪分叉、条件写在哪儿"。

  图上**只出现你导入过的章节**：
    · 工具栏「➕ 导入章节 .rpy」可多选，**累加**（再导入一批不会覆盖上一批）；
    · 「🔁 重载已导入章节」= 磁盘上的 .rpy 改过之后按**同样的范围**重解析；
    · 「🗑 清空剧情图」= 只清图，资源表与项目设置都不动；
    · 右键一个块 → 「从剧情图移除「xxx.rpy」整章」（只改内存里的图，不动文件）。
    · 连线两端都在图里才会画出来；只有一端时先建一个**占位块**，
      标题写着"未导入的章节"—— 按它提示把那个文件导入进来即可。

  块上写的是"给人看的"：label 名 + `文件:行号` + **剧情变量**摘要（`2 剧情变量`）。
  这一格**只讲剧情变量** —— 技术代码（`renpy.transition(...)` 之类）不是变量，
  是引擎实现细节，连条数都不显示在这里；想改哪些变量就双击那个块，
  或者在图上点它 → 右栏「🧩 编辑剧情变量…」。
  （实现细节不会被删掉：保存变量时它们**原位保留**；要看它们请用
  「用 Excel 打开此章节」的「代码」列，或直接看 .rpy。）

  交互（**与旧版一致**，不是新发明的）：
    · **Shift + 左键**从一个块拖到另一个块 → 连线（松手弹类型对话框）；
    · **左键点块** = 选中（右栏"当前模块"跟着变）；**左键拖动** = 移动位置；
    · **双击块** = 编辑那个块的**剧情变量**；**双击空白** = 在那里新建章节；
    · **滚轮** = 缩放（不用按 Ctrl）；**空格 + 左键拖动**（或中键拖动）= 平移；
    · 右键块 → 改本章台词（卡片）/ 在编辑器中打开 / 用 Excel 打开此章节 /
      设为当前章节 / 编辑剧情变量 / 连一条线 / 从剧情图移除整章 / 删除这个块；
    · 右键空白 → 添加新章节 / 添加连线 / 自动布局 / 适应窗口 / 检查逻辑问题 /
      导入章节 / **补全相关章节** / 重载已导入章节 / 清空剧情图；
    · **图上一条线都没有？** 多半是"连接这些章节的文件还没导入"（例如
      各章自己只有一个 label，串起它们的是 `chapter_controller.rpy`）——
      控制台会**点名说缺哪个文件**，点工具栏「🔗 补全相关章节」补齐即可；
    · 卡片正在编辑的那一章，图上对应的块会**高亮**。

  连线的三种形态（对话框里按你填的东西自动判断）：
    · 只填「菜单项」→ 分支（menu）· 只填「条件」→ 条件跳转；
    · 都不填 → 直接 jump / call。连到自己会被挡住（那是死循环）。

【演出预览（🎬）】

  把这一章按卡片效果"跑"出来：背景 / 立绘 / 站位 / 说话人高亮 / 名条 / 对话框。
  · 「真实贴图」关掉就用占位方块（大项目里翻页更快）；
  · 「只看有变化的拍」—— 几百拍里快速过一遍真正切了景、换了人的地方；
  · 缺图自动降级成方块并写明名字，**绝不留一片黑**。
  · **「引擎排版」**（勾上）= 按项目的 gui.rpy / 字体 / 分辨率把整帧离屏
    合成出来，和游戏里长得一样。每一拍都要合成一张 1920×1080，所以往后几拍
    会**提前在后台画好**，播放时轮到它只是贴上去；
  · 这一拍还没画好时，画面上先**顶着上一拍的图**（右下角小字"正在渲染"），
    不会黑屏；等很久还画不出来才是真出错（横条上写明原因）；
  · 来回翻看过的拍是**缓存命中**，不再重算。

【体检与分析（只读）】
  · 🩺 项目体检：三级校验，**不写盘**；
  · 🔎 检查逻辑问题：可达性 / 死分支 —— 结论写在右栏，**并标在图上**
    （🔴 走不到 / 死分支，灰虚线 = 未接入主线的独立入口）；
  · 📊 变量分析：逐节点的变量区间与操作明细，可过滤可复制。
  · 📦 资源体检：查资源表与项目资源对不对得上（缺失 / 未登记 / 重复 / 指向 game 之外），出报告；打开项目时也会自动对齐（写前备份）。
"""


def _emphasize(text: str) -> str:
    """
    把 `**x**` 变成 `<b>x</b>`，其余原样（换行 → `<br>`）。

    **故意不整篇走 Markdown**：帮助文本里到处是 `1. `、`  · ` 和缩进，
    Markdown 会把它们解释成有序列表 / 代码块，把排版弄得更乱
    （试过 `Qt.MarkdownText`：编号被改写、缩进行变等宽块）。
    这里只需要"加粗"这一种效果，做精确的那一件事。
    """
    import html
    import re

    out = html.escape(str(text))
    out = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', out, flags=re.S)
    return out.replace('\n', '<br>')


def _page(title: str, body: str, parent=None) -> QWidget:
    """
    一页说明：标题 + 可滚动的正文。

    正文按**富文本**渲染，只认 `**加粗**`：不然源码里为了强调写的
    `**加粗**` 会**原样连星号一起显示**给用户（以前就是这样）。
    """
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
    text = QLabel()
    try:
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setText(_emphasize(body))
    except Exception:                        # noqa: BLE001
        text.setText(body)
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

        sub = QLabel('主界面只留"这次要动的东西"；说明、快捷键都在这儿。')
        sub.setProperty('role', 'caption')
        lay.addWidget(sub)

        tabs = QTabWidget()
        # 第一页讲**界面顺序**（先剧情结构、点进模块才进卡片）——
        # 以前第一页直接讲"这一拍怎么改"，于是新用户不知道章节是怎么进图的。
        tabs.addTab(_page('先导入章节，再看结构，最后改卡片', _START, self),
                    '上手')
        tabs.addTab(_page('怎么编辑这一拍', _EDIT, self), '编辑卡片')
        tabs.addTab(self._shortcut_page(), '快捷键')
        tabs.addTab(_page('保存、导出与落盘时机', _SAVE, self), '保存与导出')
        tabs.addTab(_page('剧情图与演出预览', _GRAPH, self), '图与预览')
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
