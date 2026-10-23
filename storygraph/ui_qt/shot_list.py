"""
`storygraph.ui_qt.shot_list` —— 拍列表的**剧本版**画法（`QStyledItemDelegate`）。
================================================================================

为什么不是重写一个列表控件（2026-10-23，照用户给的参考界面改）
--------------------------------------------------------------
参考界面中间那一栏是"剧本正文"：左边一列行号、每条前面一个类型小牌
（节奏 / 描述 / 选择…）、然后是正文。我们的拍列表原来只有一行纯文字
（`3 ◆ play_music · 播放音乐  track=bgm`），读起来像日志，不像剧本。

但那个列表**被 5845 条用例里的几十条按 `item.text()` / `setCurrentRow`
判过**，换控件等于把"存错拍"级别的语义重写一遍。所以这里只换**画法**：
`QListWidget` 的模型、行号、选中态、`Qt.UserRole` 里的拍号**一个都没动**，
`item.text()` 仍是原来的字 —— 只是不再照着它画。

它自己不认识剧本
----------------
"这张拍是什么类型 / 说话人是谁"全部由调用方通过 `entry_of(拍号)` 递进来
（`EditorSession.list_entry`）。这个文件里没有一行解析逻辑 —— 上一轮
`list_entry` 已经把"哪几个键能说明这拍是什么"定清楚了，这里只管排版。

配色 / 字号**只从 tokens 取**（界面代码不许写死色值）。
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T


#: 列表里的两种行（分组标题 / 一拍）。高度不同，`sizeHint` 按这个报。
GROUP_H = 30
ROW_H = 44
#: 行号那一条的宽度（三位数够 999 拍；超了也照样右对齐画得下）
GUTTER_W = 40
#: 类型小牌的高度 / 最小宽度 / 圆角
BADGE_H = 18
BADGE_MIN_W = 30
BADGE_RADIUS = 9


def badge_of(entry: dict) -> tuple:
    """
    一拍 → `(牌子上的字, 前景 token, 底色 token)`。

    判据与 `window_card._row_text` **同源**（`entry` 里那几个键就是它排字
    用的同一份）：先看控制流，再看语句，最后看前缀。两边漂移的话会出现
    "列表写着`跳转`、牌子写着`对白`"这种自相矛盾。
    """
    e = entry or {}
    if str(e.get('flow_type') or ''):
        return ('调用' if e.get('flow_type') == 'call' else '跳转',
                'state.info', 'state.infoBg')
    if str(e.get('statement') or ''):
        return ('语句', 'state.warning', 'state.warningBg')
    prefix = str(e.get('prefix') or '')
    if prefix == '☁':
        return ('旁白', 'text.secondary', 'bg.surface')
    if prefix == '⚑':
        return ('分支', 'brand.primary', 'brand.subtle')
    if prefix == '➜':
        return ('选项', 'brand.primary', 'brand.subtle')
    return ('对白', 'brand.primary', 'brand.subtle')


def row_text_of(entry: dict) -> tuple:
    """
    一拍 → `(加粗的那一小段, 后面的正文)`。

    对白：`('小明：', '今天的天气真不错')`；语句：`('play_music', '· 播放音乐
    track=bgm')`；控制流：`('↳ 调用', ' 序章')`。**说话人加粗**是参考界面
    里最省事的一处层级 —— 眼睛扫一列名字就能找到要找的那句。
    """
    e = entry or {}
    if str(e.get('flow_type') or ''):
        verb = '调用' if e.get('flow_type') == 'call' else '跳转'
        arrow = '↳' if e.get('flow_type') == 'call' else '→'
        return (f'{arrow} {verb}', f" {e.get('flow_target') or '（未指定）'}")
    if str(e.get('statement') or ''):
        name = str(e.get('statement') or '')
        head = str(e.get('statement_head') or '')
        rest = head[len(name):].strip() if head.startswith(name) else head
        label = str(e.get('statement_label') or '')
        tail = f' · {label}' if label else ''
        return (f'{name}{tail}', f'  {rest}')
    who = str(e.get('who') or '').strip()
    text = str(e.get('text') or '')
    if who:
        return (f'{who}：', f' {text}')
    # **不再把 `☁ ⚑ ➜` 抄进正文**：类型牌已经说了是旁白/分支/选项，
    # 而这三个符号在微软雅黑里没有字形（`⚑` / `➜` 会变成豆腐块，
    # 与 2026-10-18 那个 `⚙` 是同一类事故）。牌子上有字就够了。
    return ('', f' {text}')


class ShotDelegate(QStyledItemDelegate):
    """
    把 `QListWidget` 的一行画成"剧本一行"。

    `entry_of(拍号) -> dict` 由窗口递进来（拿不到就退回默认画法，
    绝不让列表变成空白 —— 那会让人以为这一拍没了）。
    """

    def __init__(self, parent=None, entry_of=None):
        super().__init__(parent)
        self._entry_of = entry_of or (lambda idx: {})

    # ---- 取数据 ----
    @staticmethod
    def beat_index(index):
        """行 → 拍号（`None` = 不是拍：分组标题 / 空行）。"""
        data = index.data(Qt.UserRole)
        try:
            idx = int(data)
        except (TypeError, ValueError):
            return None
        return idx if idx >= 0 else None

    def entry(self, index) -> dict:
        idx = self.beat_index(index)
        if idx is None:
            return {}
        try:
            return dict(self._entry_of(idx) or {})
        except Exception as e:                   # noqa: BLE001 - 画不出来也要画这行
            swallow('ui_qt.shot_list.entry', e)
            return {}

    def _is_group(self, index) -> bool:
        data = index.data(Qt.UserRole)
        try:
            return data is not None and int(data) < 0
        except (TypeError, ValueError):
            return False

    # ---- 尺寸 ----
    def sizeHint(self, option, index) -> QSize:
        h = GROUP_H if self._is_group(index) else ROW_H
        return QSize(int(option.rect.width()), h)

    # ---- 画 ----
    def paint(self, painter, option, index):
        painter.save()
        try:
            if self._is_group(index):
                self._paint_group(painter, option, index)
            else:
                self._paint_row(painter, option, index)
        finally:
            painter.restore()

    # · 选中 / 悬停的底色自己画：QSS 的 ::item 背景会被 delegate 整个盖掉，
    #   不自己画的话选中态就"消失"了（点哪一行都看不出来）。
    def _fill(self, painter, option, bg: str) -> QRect:
        rect = QRect(option.rect)
        painter.fillRect(rect, QColor(theme.t(bg)))
        return rect

    def _paint_group(self, painter, option, index):
        rect = self._fill(painter, option, 'bg.surface')
        painter.setPen(QColor(theme.t('border.default')))
        painter.drawLine(rect.left(), rect.bottom(),
                         rect.right(), rect.bottom())
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSize(theme.font_pt('captionStrong'))
        painter.setFont(font)
        painter.setPen(QColor(theme.t('text.secondary')))
        painter.drawText(rect.adjusted(10, 0, -10, 0),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         str(index.data(Qt.DisplayRole) or ''))

    def _paint_row(self, painter, option, index):
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected:
            rect = self._fill(painter, option, 'listbox.selectBg')
        elif hovered:
            rect = self._fill(painter, option, 'bg.cardHover')
        else:
            rect = self._fill(painter, option, 'bg.canvas')

        entry = self.entry(index)
        if not entry:
            # 取不到摘要（会话还没载入）→ 至少把原来的字画出来，不留空行
            font = QFont(option.font)
            font.setPointSize(theme.font_pt('body'))
            painter.setFont(font)
            painter.setPen(QColor(theme.t('text.primary')))
            painter.drawText(rect.adjusted(10, 0, -10, 0),
                             Qt.AlignVCenter | Qt.AlignLeft,
                             str(index.data(Qt.DisplayRole) or ''))
            return

        idx = self.beat_index(index)
        number = (int(idx) + 1) if idx is not None else 0

        # ① 选中时左边一道 3px 的亮条：竖着扫一列，当前行最先被看到
        if selected:
            painter.fillRect(QRect(rect.left(), rect.top(), 3, rect.height()),
                             QColor(theme.t('brand.onPrimary')))

        # ② 行号（等宽、右对齐）
        fg_main = 'listbox.selectFg' if selected else 'text.primary'
        fg_dim = 'listbox.selectFg' if selected else 'text.tertiary'
        gutter = QRect(rect.left() + 8, rect.top(), GUTTER_W, rect.height())
        num_font = QFont(option.font)
        num_font.setFamily(str(T.FONTS['mono'][0]))
        num_font.setPointSize(theme.font_pt('caption'))
        painter.setFont(num_font)
        num_color = QColor(theme.t(fg_dim))
        if selected:
            num_color.setAlpha(200)
        painter.setPen(num_color)
        painter.drawText(gutter, Qt.AlignVCenter | Qt.AlignRight,
                         f'{number:03d}' if number else '')

        # ③ 类型小牌
        text, tone, tone_bg = badge_of(entry)
        font = QFont(option.font)
        font.setPointSize(theme.font_pt('caption'))
        painter.setFont(font)
        fm = QFontMetrics(font)
        badge_w = max(BADGE_MIN_W, fm.horizontalAdvance(text) + 12)
        badge = QRect(rect.left() + 8 + GUTTER_W + 6,
                      rect.center().y() - BADGE_H // 2, badge_w, BADGE_H)
        if selected:
            chip_bg = QColor(theme.t('brand.onPrimary'))
            chip_bg.setAlpha(46)
            painter.setPen(Qt.NoPen)
            painter.setBrush(chip_bg)
            painter.drawRoundedRect(badge, BADGE_RADIUS, BADGE_RADIUS)
            painter.setPen(QColor(theme.t('listbox.selectFg')))
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.t(tone_bg)))
            painter.drawRoundedRect(badge, BADGE_RADIUS, BADGE_RADIUS)
            painter.setPen(QColor(theme.t(tone)))
        painter.setBrush(Qt.NoBrush)
        painter.drawText(badge, Qt.AlignCenter, text)

        # ④ 正文：说话人（或语句名）加粗 + 后面的字
        body = QRect(badge.right() + 8, rect.top(),
                     max(0, rect.right() - badge.right() - 14), rect.height())
        head, tail = row_text_of(entry)
        head_font = QFont(option.font)
        head_font.setBold(True)
        head_font.setPointSize(theme.font_pt('body'))
        body_font = QFont(option.font)
        body_font.setPointSize(theme.font_pt('body'))
        hfm = QFontMetrics(head_font)
        bfm = QFontMetrics(body_font)
        head_w = min(hfm.horizontalAdvance(head), max(0, body.width() - 40))
        painter.setFont(head_font)
        painter.setPen(QColor(theme.t(fg_main)))
        painter.drawText(QRect(body.left(), body.top(), head_w, body.height()),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         hfm.elidedText(head, Qt.ElideRight, head_w))
        rest_w = max(0, body.width() - head_w)
        painter.setFont(body_font)
        painter.setPen(QColor(theme.t(fg_dim)))
        painter.drawText(QRect(body.left() + head_w, body.top(),
                               rest_w, body.height()),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         bfm.elidedText(tail, Qt.ElideRight, rest_w))

        # ⑤ 底部一条极淡的分隔线：40 多行靠它"分行"而不靠底色
        if not selected:
            line = QColor(theme.t('border.default'))
            line.setAlpha(110)
            painter.setPen(line)
            painter.drawLine(rect.left() + 8, rect.bottom(),
                             rect.right() - 8, rect.bottom())


__all__ = ['ShotDelegate', 'badge_of', 'row_text_of',
           'GROUP_H', 'ROW_H', 'GUTTER_W']
