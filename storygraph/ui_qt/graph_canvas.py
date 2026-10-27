"""
storygraph.ui_qt.graph_canvas
=============================
Qt 版**剧情结构图**（块状编辑器）—— 用 `QGraphicsScene` 画模块块与连线。

它是这个工具的**第一屏**：先看整章/整项目的结构（谁跳谁、菜单怎么分叉、
哪些块有问题），双击一个模块才进卡片编辑器改这一章的台词。

   · 模块块：圆角矩形 + label 名 + 来源文件:行号 + 变量操作摘要
   · 连线：贝塞尔曲线 + **真箭头** + 条件/选项标签；颜色按语义分
   · 静态分析结论**画回图上**：分析入口（绿）、走不到的块（红）、
     未接入主线的独立入口（灰虚线）
   · 交互：Ctrl+滚轮缩放、拖块（连线跟着走）、空白处中键平移、
     点选、双击进卡片、右键菜单

排版约定（**这里是唯一的真源**）
--------------------------------
`NODE_W/NODE_H` 与 `LAYER_GAP/NODE_GAP` 必须一起改 —— 历史事故：
节点从旧版的 120×60 放大到 176×96，间距却还是为旧尺寸调好的 `180/60`，
于是同一层相邻的块**垂直重叠 36 像素**，图看起来"糊成一坨"。
所以间距一律由节点尺寸算出（见 `_layout_gaps`），不再写死数字。
`storygraph/graph/layout.py` 仍然只负责分层与减交叉，**尺寸由这里传进去**。

默认缩放也不"适应全部"：50 多个块 fit 进窗口必然缩到 0.4 以下，
文字全成蚂蚁。这里改成"装得下就适应、装不下就 100% 停在入口块"。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetricsF,
                           QPainter, QPainterPath, QPen, QPolygonF, QTransform)
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsPathItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsTextItem, QGraphicsView)

from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme

#: 模块块尺寸
NODE_W, NODE_H = 208, 104
#: 同层相邻块的垂直净距
NODE_GAP_EXTRA = 30
#: 相邻层之间的水平净距
LAYER_GAP_EXTRA = 74
#: 缩放下限/上限（再小就看不清，再大就没意义）
ZOOM_MIN, ZOOM_MAX = 0.25, 3.0
#: 自动布局后，"整图装得下"的判定阈值：小于它就不 fit，改用 100%
FIT_MIN_SCALE = 0.70
#: 解析器新建节点时用的默认坐标（`GraphBuilder` 建 label / 占位节点都用它）。
#: 坐标正好等于它 = "这个块还没被摆过"（见 `GraphCanvas._unplaced_ids`）。
DEFAULT_POS = (200.0, 200.0)

#: 拖块时**离视口边缘多近开始自动滚**（像素）—— 用户 2026-10-27：
#: "当鼠标处在拖拽节点状态的时候，碰到画布边界画布舞台自动移动"。
#: 44 px 大约是一张卡片高度的 40%：注意力落在块上时也能感到"到头了"。
DRAG_SCROLL_MARGIN = 44.0
#: 自动滚的最快速度（每 16 ms 的像素数）与最小速度（贴边但也得一格一格走）
DRAG_SCROLL_MAX = 22.0
DRAG_SCROLL_MIN = 2.0
#: 自动滚的节拍（约 60 fps）
DRAG_SCROLL_INTERVAL = 16

#: 连线颜色按**语义**分（与 tkinter 版一致：选项 / 条件 / 类型）
EDGE_COLOR = {
    'jump': 'brand.primary',
    'call': 'state.info',
    'menu': 'state.warning',
}
EDGE_DEFAULT = 'text.tertiary'


def _layout_gaps():
    """把节点尺寸翻译成布局间距（**改尺寸必须走这里**）。"""
    return NODE_W + LAYER_GAP_EXTRA, NODE_H + NODE_GAP_EXTRA


def _col(name: str, fallback: str = '#8a8a8a') -> QColor:
    try:
        return QColor(theme.t(name))
    except Exception:                    # noqa: BLE001
        return QColor(fallback)


def _elide(text: str, font: QFont, width: float) -> str:
    """按字体度量截断加省略号（块里放不下就截，不让字溢出框外）。"""
    try:
        fm = QFontMetricsF(font)
        return fm.elidedText(str(text or ''), Qt.ElideRight, width)
    except Exception:                    # noqa: BLE001
        return str(text or '')


class NodeItem(QGraphicsRectItem):
    """
    一个**模块块**（一个 Ren'Py label / 一章）。

    块上只放"人要看的"：名字、来源、变量操作摘要。
    技术代码（`renpy.transition(...)` / `persistent.x = ...`）**不上块** ——
    真实项目里一个块能挂 55 条，全铺上去就把剧情变量淹了
    （过滤规则在 `storygraph.story.story_ops`，两边共用一份）。
    """

    def __init__(self, node, canvas=None, mark=None):
        super().__init__(0, 0, NODE_W, NODE_H)
        self.node = node
        self.canvas = canvas
        self.edges: list = []
        #: 静态分析结论：'stranded' / 'independent' / None（由画布设置）
        self.mark = mark
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        # 子项不许画出块外（文字长短不一，靠 clip 兜底）
        self.setFlag(QGraphicsItem.ItemClipsChildrenToShape, True)
        self.setPos(node.x, node.y)
        self.setZValue(1)
        self.setAcceptHoverEvents(True)
        self._build_text()
        self.refresh()

    # ---------------- 内容 ----------------

    def _build_text(self):
        self._title = QGraphicsSimpleTextItem(self)
        f = QFont(theme.font_family(), theme.font_pt('bodyStrong'))
        f.setBold(True)
        self._title.setFont(f)
        self._title.setBrush(QBrush(_col('text.primary')))
        self._title.setPos(10, 8)

        self._meta = QGraphicsSimpleTextItem(self)
        self._meta.setFont(QFont(theme.font_family(), theme.font_pt('caption')))
        self._meta.setBrush(QBrush(_col('text.tertiary')))
        self._meta.setPos(10, 32)

        self._summary = QGraphicsSimpleTextItem(self)
        self._summary.setFont(QFont(theme.font_family(),
                                    theme.font_pt('caption')))
        self._summary.setBrush(QBrush(_col('state.info')))
        self._summary.setPos(10, 58)

        self._note = QGraphicsSimpleTextItem(self)
        self._note.setFont(QFont(theme.font_family(), theme.font_pt('caption')))
        self._note.setBrush(QBrush(_col('state.error')))
        self._note.setPos(10, 78)

    def refresh(self):
        """把模型里的内容重新渲染到块上（内容变了就调它）。"""
        try:
            self._refresh_inner()
        except Exception:                # noqa: BLE001
            # 画布更新不能因为一个块的数据脏了就把整个界面带崩
            swallow('graph_canvas.refresh')
        self.update()

    def _refresh_inner(self):
        from storygraph.story import story_ops

        title_font = self._title.font()
        meta_font = self._meta.font()
        inner_w = NODE_W - 20
        name = self.node.label_name or '(未命名)'
        self._title.setText(_elide(name, title_font, inner_w))
        self._title.setToolTip(name)

        fp = getattr(self.node, 'file_path', None)
        if fp:
            from pathlib import Path
            stem = Path(str(fp)).name
            ln = getattr(self.node, 'line_number', None)
            self._meta.setText(_elide(f'{stem}:{ln}' if ln else stem,
                                      meta_font, inner_w))
        else:
            self._meta.setText('（无源文件）')

        summary = ''
        try:
            # 只讲**剧情变量**（技术代码连数量都不上块）：用户 2026-10-03
            # "技术代码不是变量，是具体的实现细节"。
            summary = story_ops.narrative_summary(self.node)
        except Exception as e:                # noqa: BLE001
            swallow('ui_qt.graph_canvas._refresh_inner', e)
            summary = ''
        self._summary.setText(_elide(summary, self._summary.font(), inner_w))
        self._summary.setVisible(bool(summary))

        mark_text = {'stranded': '⚠ 走不到（分析结论）',
                     'independent': '🔌 未接入主线'}.get(self.mark, '')
        self._note.setText(_elide(mark_text, self._note.font(), inner_w))
        self._note.setVisible(bool(mark_text))
        self.setToolTip(self._tooltip())

    def _tooltip(self) -> str:
        from storygraph.story import story_ops

        lines = [f'模块：{self.node.label_name or "(未命名)"}']
        fp = getattr(self.node, 'file_path', None)
        if fp:
            ln = getattr(self.node, 'line_number', None)
            lines.append(f'来源：{fp}' + (f':{ln}' if ln else ''))
        if self.mark == 'stranded':
            lines.append('⚠ 静态分析：从入口走不到这个块')
        elif self.mark == 'independent':
            lines.append('🔌 静态分析：没有入边（多为独立章节/被外部调用）')
        # 这里**只列剧情变量**：技术代码是引擎实现细节，不是变量，
        # 名字一律不出现在变量相关的地方（用户 2026-10-03 明确要求）。
        nar = story_ops.narrative_operations(self.node)
        if nar:
            lines.append('')
            lines.append('剧情变量（双击这个块可以编辑）：')
            lines.append(story_ops.render_list(nar, limit=12))
        else:
            lines.append('')
            lines.append('（这个模块没有剧情变量）')
        lines.append('')
        lines.append('双击 → 编辑剧情变量')
        lines.append('右键 → 打开本章剧本（改台词） / 用 Excel 打开此章节 / …')
        return '\n'.join(lines)

    # ---------------- 绘制 ----------------

    def paint(self, painter, option, widget=None):
        # paint 里抛异常 Qt 会直接 segfault（连 traceback 都没有），
        # 所以整个函数体必须包住，并且枚举路径要写全
        # （`QPainter.RenderHint.Antialiasing`，不是 `painter.Antialiasing`）。
        try:
            rect = QRectF(0, 0, NODE_W, NODE_H)
            speaking = bool(getattr(self.node, 'is_current', False))
            selected = self.isSelected()
            stranded = self.mark == 'stranded'
            if stranded:
                fill = _col('state.errorBg')
            elif speaking:
                fill = _col('brand.subtle')
            elif selected:
                fill = _col('bg.cardHover')
            else:
                fill = _col('bg.card')
            # 普通块也用 border.strong（浅蓝）：画布是白的，用 border.default
            # 的 #E0E0E0 会让块"浮不起来"，一屏块看着像一片空白格子。
            border = _col('border.strong')
            width = 1
            style = Qt.SolidLine
            if stranded:
                border, width = _col('state.error'), 2
            elif speaking or selected:
                border, width = _col('brand.primary'), 2
            elif self.mark == 'independent':
                border, style = _col('border.dashed'), Qt.DashLine

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QBrush(fill))
            pen = QPen(border, width)
            pen.setStyle(style)
            painter.setPen(pen)
            painter.drawRoundedRect(rect, T.RADIUS['card'], T.RADIUS['card'])
            if speaking:
                painter.setPen(QPen(_col('brand.primary'), 3))
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1),
                                        T.RADIUS['card'], T.RADIUS['card'])
            # 标题左侧的一条语义色（不占文字位置：靠 padding 让开）
            accent = _col('brand.primary' if speaking else 'border.strong')
            if stranded:
                accent = _col('state.error')
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(accent))
            painter.drawRoundedRect(QRectF(0, 0, 4, NODE_H), 2, 2)
        except Exception:                # noqa: BLE001
            swallow('graph_canvas.paint')

    # ---------------- 拖动回写 ----------------

    def mousePressEvent(self, event):
        """记下拖动**之前**的位置（拖完要发 `node_moved` 给撤销栈用）。"""
        try:
            if event.button() == Qt.LeftButton and not getattr(
                    self.canvas, '_space_down', False):
                self._drag_from = (float(self.pos().x()), float(self.pos().y()))
                # 拖块时贴到视口边缘 → 画布自己滚（用户 2026-10-27 的体验优化）
                if self.canvas is not None:
                    self.canvas.begin_node_drag(self)
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.graph_canvas.mousePressEvent', e)
            self._drag_from = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """拖动过程中把鼠标的**视口坐标**告诉画布（决定要不要自动滚）。"""
        super().mouseMoveEvent(event)
        try:
            if self.canvas is not None and self.canvas._drag_item is self:
                self.canvas.note_drag_cursor(
                    self.canvas.mapFromScene(event.scenePos()))
        except Exception as e:                    # noqa: BLE001 - 滚不动也要能拖
            swallow('ui_qt.graph_canvas.node_drag_move', e)

    def mouseReleaseEvent(self, event):
        """
        拖完发 `node_moved`（仅当位置真的变了）。

        位置回写模型是在 `itemChange` 里做的（拖动过程中实时），所以这里
        只负责"报告区间"：撤销栈拿它造一条 `MoveNodeCommand`，撤销时把
        坐标改回去、重做时再改回来。
        """
        super().mouseReleaseEvent(event)
        try:
            if self.canvas is not None:
                self.canvas.end_node_drag()
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.graph_canvas.node_drag_end', e)
        origin = getattr(self, '_drag_from', None)
        self._drag_from = None
        try:
            if origin is None or event.button() != Qt.LeftButton:
                return
            now = (float(self.pos().x()), float(self.pos().y()))
            if abs(now[0] - origin[0]) < 0.5 and abs(now[1] - origin[1]) < 0.5:
                return                       # 只是点了一下，没挪
            if self.canvas is not None:
                self.canvas.node_moved.emit(self.node.id, origin[0], origin[1],
                                            now[0], now[1])
        except Exception:                    # noqa: BLE001
            swallow('graph_canvas.mouseReleaseEvent')

    def itemChange(self, change, value):
        if self.scene():
            if change == QGraphicsItem.ItemPositionChange:
                # 回到模型里，自动布局/保存都靠这两个数
                try:
                    self.node.x = float(value.x())
                    self.node.y = float(value.y())
                except Exception:            # noqa: BLE001
                    swallow('graph_canvas.itemChange')
            elif change == QGraphicsItem.ItemPositionHasChanged:
                # 连线必须跟着块走 —— 而且必须挂在 **HasChanged**（位置已经
                # 生效）上：挂在 ItemPositionChange 里读到的还是**旧坐标**，
                # 于是 `ei.refresh()` 算出来的是原地不动的线（拖完线全留在
                # 原处，图立刻没法看）。第一版两处都写错了。
                for ei in list(self.edges):
                    try:
                        ei.refresh()
                    except Exception:        # noqa: BLE001
                        swallow('graph_canvas.itemChange')
        return super().itemChange(change, value)


def _edge_is_manual(edge) -> bool:
    return str(getattr(edge, 'origin', 'source') or 'source') == 'manual'


def _edge_is_control(edge) -> bool:
    """编排层路由（P2）：写在 `X_control` 里，不在章节源文件。"""
    return str(getattr(edge, 'origin', 'source') or 'source') == 'control'


def _edge_where_text(edge, info: dict | None = None) -> str:
    """
    这条线**在哪一拍**（短标签用）。

    草图没有真的拍：它记的是"打算写在哪"（P1 之后可以是第 N 拍，
    仍然只是**打算**，不是已写入）。源边有拍上下文时给"第 N 拍"，
    最后一拍额外标「章末」；上下文没载入时如实说「位置未载入」，
    别留空（留空会被读成"没有位置这回事"）。
    """
    if _edge_is_control(edge):
        return '章末（编排层）'
    if _edge_is_manual(edge):
        if str(getattr(edge, 'position', 'chapter_end')) == 'beat':
            if info:
                try:
                    return f'第 {int(info.get("index"))} 拍'
                except (TypeError, ValueError) as e:
                    swallow('ui_qt.graph_canvas.edge_where', e)
            return '指定拍（未载入或已删除）'
        return '章末'
    if info:
        try:
            idx = int(info.get('index'))
        except (TypeError, ValueError):
            return '位置未载入'
        text = f'第 {idx} 拍'
        total = info.get('total')
        if (isinstance(total, int)
                or (isinstance(total, str) and total.isdigit())):
            if int(total) == idx:
                text += '（章末）'
        return text
    return '位置未载入'


def edge_label_text(edge, info: dict | None = None) -> str:
    """
    连线上的那行字：**只放条件 / 菜单项**；位置归窗口（2026-10-24）。

    用户明确要求：线上不要把"第几拍"挂在外面，线型负责说 jump/call，
    条件/菜单项负责说分支；只有草图必须把「草图」写在脸上。

    ```
    「去屋顶」
    if trust > 2
    草图 ·「去屋顶」   ← 手工草图（位置在窗口/提示里）
    ```
    """
    opt = str(getattr(edge, 'option_text', '') or '').strip()
    cond = str(getattr(edge, 'condition', '') or '').strip()
    body = f'「{opt}」' if opt else (f'if {cond}' if cond else '')
    if _edge_is_manual(edge):
        return ' · '.join(x for x in ('草图', body) if x)
    return body


def edge_tooltip(edge, info: dict | None = None) -> str:
    """
    悬停时这条边的"肚子"：它在第几步、前后各是什么。

    先给位置，再给"这条线是什么"。草图那句警告保留在最前 ——
    它是"这条线还没写进脚本"的唯一提醒，不能因为加了位置就丢掉。
    """
    manual = _edge_is_manual(edge)
    control = _edge_is_control(edge)
    where = _edge_where_text(edge, info)
    if manual:
        tail = ('草图（未写入）：只画在图上，不写进 .rpy\n'
                '右键这条线 →「写入剧本…」/「删除这条草图」'
                '（条件/菜单项分支请用卡片）')
    elif control:
        tail = ('编排层（`X_control`）里的控制流：写在控制流文件里，'
                '章节源文件一个字没改')
    else:
        tail = '源文件里的控制流'

    if not info:
        return (f'拍位置：{where}\n\n{tail}' if manual
                else f'{tail}\n拍位置：{where}')

    kind = str(getattr(edge, 'type', 'jump') or 'jump')
    semantic = '调用（走完会回来）' if kind == 'call' else '跳转（去了不回）'
    label = str(info.get('label') or '')
    lines = [
        f'{label + " · " if label else ""}第 {info.get("index")} 拍 / '
        f'共 {info.get("total")} 拍',
        f'拍位置：{where}',
        f'{semantic}：{info.get("text") or "（空拍）"}',
        f'上一拍：{info.get("prev") or "（章首）"}',
        f'下一拍：{info.get("next") or "（章末）"}',
        '',
        tail,
    ]
    frm = str(getattr(edge, 'from_clause', '') or '')
    if frm:
        # `from` 是存档返回点，比"上一拍/下一拍"更影响老存档，放在语义后面。
        lines.insert(3, f'存档点：from {frm}')
    return '\n'.join(lines)


class EdgeItem(QGraphicsPathItem):
    """
    一条连线：贝塞尔曲线 + **真的箭头** + 条件/选项标签。

    第一版画了曲线却没画箭头（`_arrow_angle` 算完就扔了），于是"谁跳到谁"
    只能靠猜方向。箭头现在画在 `paint` 里。
    """

    def __init__(self, edge, src: NodeItem, dst: NodeItem, canvas=None):
        super().__init__()
        self.edge = edge
        self.src = src
        self.dst = dst
        self.canvas = canvas
        self.mark = None
        self._label = QGraphicsSimpleTextItem(self)
        self._label.setFont(QFont(theme.font_family(), theme.font_pt('caption')))
        self._label.setBrush(QBrush(_col('text.secondary')))
        self._label.setZValue(2)
        self.setZValue(0)
        self.setAcceptHoverEvents(True)
        # 边可选中：右键菜单 / 后续 Delete 键都靠它（P0）
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        # 手型光标 = "这条线可以点"（双击打开「编辑连线」，见 2026-10-24）
        self.setCursor(Qt.PointingHandCursor)
        self._apply_pen()
        self.refresh()

    # ---------------- 语义 ----------------

    def _beat_info(self) -> dict | None:
        """
        这条边对应哪一拍（画布持有的"拍上下文"里查）。

        源边查 `line_id`；草图没有已写入的拍，但 P1 记了**打算写到哪一拍**
        （`position_line_id`），同样能在上下文里翻译成"第 N 拍"。
        """
        canvas = self.canvas
        ctx = getattr(canvas, '_beat_ctx', None) if canvas is not None else None
        lid = str(getattr(self.edge, 'line_id', '') or '')
        if not lid and str(getattr(self.edge, 'origin', 'source')) == 'manual':
            lid = str(getattr(self.edge, 'position_line_id', '') or '')
        if not lid or not ctx:
            return None
        try:
            return ctx.get(lid)
        except Exception as e:                    # noqa: BLE001 - 上下文脏了不该画不出线
            swallow('ui_qt.graph_canvas.edge.beat_info', e)
            return None

    def _label_text(self) -> str:
        return edge_label_text(self.edge, self._beat_info())

    def _apply_pen(self):
        """颜色按语义分：选项 > 条件 > 连线类型（旧版就是这个规则）。"""
        opt = str(getattr(self.edge, 'option_text', '') or '').strip()
        cond = str(getattr(self.edge, 'condition', '') or '').strip()
        etype = getattr(self.edge, 'type', 'jump')
        if opt:
            color = _col('state.warning')
        elif cond:
            color = _col('state.success')
        else:
            color = _col(EDGE_COLOR.get(etype, EDGE_DEFAULT))
        width = 2 if (opt or cond) else 1.5
        pen = QPen(color, width)
        # 虚线 = 不立刻"走死"的/未落盘的：call 和草图都用虚线；
        # 草图额外靠脸上那句「草图」区分（用户不看图例，只看线）。
        if (getattr(self.edge, 'origin', 'source') == 'manual'
                or etype == 'call'):
            pen.setStyle(Qt.DashLine)
        if self.mark == 'stranded':
            pen = QPen(_col('state.error'), width)
        if self.isSelected():
            pen.setWidthF(max(pen.widthF(), 3.0))
            pen.setColor(_col('state.info'))
        self.setPen(pen)

    def set_mark(self, mark):
        self.mark = mark
        self._apply_pen()

    def itemChange(self, change, value):
        # 选中/取消选中时笔宽和颜色要跟着变（Qt 不会替我们重画笔）
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self._apply_pen()
        return super().itemChange(change, value)

    # ---------------- 几何 ----------------

    def _points(self):
        """
        (起点, 终点) —— **两端都取块的中心**，连线是直线。

        用户 2026-10-03：

        > 绕在一起没有自动布局非常难看，请把箭头放在中间，
        > 把连线点放在块的里面而不是边缘

        所以：

          · 端点取**块的中心**（不是边缘的某个锚点）—— 块的填充是不透明的，
            而边的 z 值低于块，于是线的两端**藏在块里面**，视觉上就是
            "从块里面连出来"；
          · 线走**直线**（以前的贝塞尔控制点在层与层之间绕大圈，
            几条线一起看就是一团乱麻）；
          · 箭头画在**中点**（见 `refresh` / `paint`），方向沿连线方向 ——
            不用跑到终点去找箭头，"谁指向谁"一眼能看出来。

        自环还是画在块右侧一圈（没有"两个中心"可言）。
        """
        a = self.src.sceneBoundingRect()
        b = self.dst.sceneBoundingRect()
        if self.src is self.dst:
            p1 = QPointF(a.right(), a.top() + a.height() * 0.30)
            p2 = QPointF(a.right(), a.top() + a.height() * 0.72)
            c1 = QPointF(a.right() + NODE_W * 0.42, a.top() + a.height() * 0.10)
            c2 = QPointF(a.right() + NODE_W * 0.42, a.bottom() - a.height() * 0.10)
            return p1, p2, c1, c2
        return a.center(), b.center(), None, None

    def refresh(self):
        try:
            p1, p2, c1, c2 = self._points()
            if c1 is None or c2 is None:
                # 直线（两端取块中心，端点藏在块里）
                path = QPainterPath(p1)
                path.lineTo(p2)
                mid = QPointF((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0)
                ang = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
            else:
                # 自环：右侧绕一圈，中点取贝塞尔 t=0.5
                path = QPainterPath(p1)
                path.cubicTo(c1, c2, p2)
                mid = QPointF((p1.x() + 3 * c1.x() + 3 * c2.x() + p2.x()) / 8.0,
                              (p1.y() + 3 * c1.y() + 3 * c2.y() + p2.y()) / 8.0)
                ang = math.atan2(p2.y() - c2.y(), p2.x() - c2.x())
            self.setPath(path)
            self._p1, self._p2, self._mid = p1, p2, mid
            # **箭头画在中点**（用户明确要求）——方向沿连线，一眼看出指向谁
            self._arrow = self._make_arrow(mid, ang)
            # 悬停时说清"这条线在第几步、前后是什么、是哪一种"
            self.setToolTip(edge_tooltip(self.edge, self._beat_info()))
            # 标签放在中点上方（压在线上的话会把线挡断）
            text = self._label_text()
            self._label.setText(_elide(text, self._label.font(), 320))
            self._label.setToolTip(text)
            self._label.setVisible(bool(text))
            if text:
                br = self._label.boundingRect()
                self._label.setPos(mid.x() - br.width() / 2,
                                   mid.y() - br.height() - 8)
        except Exception:                # noqa: BLE001
            swallow('graph_canvas.refresh')

    @staticmethod
    def _make_arrow(tip: QPointF, ang: float, size: float = 9.0) -> QPolygonF:
        poly = QPolygonF()
        for delta in (0.0, math.radians(150), math.radians(210)):
            poly.append(QPointF(tip.x() + size * math.cos(ang + delta),
                                tip.y() + size * math.sin(ang + delta)))
        return poly

    def mouseDoubleClickEvent(self, event):
        """
        双击一条边 = **打开这条线的属性**（旧版「编辑连线」，2026-10-24 找回）。

        以前这里发的是 `edge_open_requested`（跳进对应那一拍）—— 用户反馈
        "点一条线，弹的是章节/新建章节，线自己的信息一个没有"。定位那一拍
        改走右键菜单「定位到这一拍」，双击只回答"这条线是什么"。

        草图也发信号：窗口层会打开同一个窗口（只读落点 + 可改条件/菜单），
        比"双击没反应"好。
        """
        try:
            canvas = self.canvas
            if canvas is not None:
                canvas.edge_edit_requested.emit(self.edge)
        except Exception:                    # noqa: BLE001 - 点一下不该崩
            swallow('graph_canvas.edge.double_click')
        event.accept()

    def paint(self, painter, option, widget=None):
        try:
            super().paint(painter, option, widget)
            poly = getattr(self, '_arrow', None)
            if poly is not None and self.isVisible():
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(self.pen().color()))
                painter.drawPolygon(poly)
        except Exception:                # noqa: BLE001
            swallow('graph_canvas.paint')


class GraphCanvas(QGraphicsView):
    """
    剧情结构图画布（**第一屏**）。

    操作**照 tkinter 版**（用户 2026-10-03 明确要求"不要乱改我的操作"）：

      · `connect_requested(from, to)` —— **Shift + 左键拖动**连线，松手时发出；
      · `node_ops_requested(nid)`     —— **双击块** = 编辑变量操作；
      · `node_add_requested(x, y)`    —— 双击空白 = 在那个位置新建章节；
      · `edge_edit_requested(edge)`   —— **双击线** = 打开「编辑连线」；
      · `node_menu_requested(nid, 屏幕坐标)` —— 右键菜单；
      · `selection_changed(nid)`      —— 左键选中（右侧"当前模块"跟着变）。

    「进卡片编辑器」不在双击上（旧版双击是变量操作），走右侧主入口按钮
    或右键菜单。
    """

    connect_requested = Signal(object, object)       # from_id, to_id
    node_ops_requested = Signal(object)              # node_id
    node_add_requested = Signal(float, float)        # 场景坐标
    node_menu_requested = Signal(object, object)     # node_id, 屏幕坐标
    selection_changed = Signal(object)
    zoom_changed = Signal(int)                       # 百分比
    #: 拖完一个块：`nid, 原 x, 原 y, 新 x, 新 y` —— 图级撤销要用它
    #: （用户 2026-10-05："将撤销做好"：拖歪了也要能 Ctrl+Z 回去）。
    node_moved = Signal(object, float, float, float, float)
    #: 旧的双击边信号：保留给右键「定位到这一拍」那条路（画布不直接发）。
    edge_open_requested = Signal(object)
    #: 双击一条边（2026-10-24）：打开**这条线自己的属性**（旧版「编辑连线」）。
    edge_edit_requested = Signal(object)
    #: 右键一条边（P0，2026-10-24）：窗口层弹「写入剧本 / 删除」。
    edge_menu_requested = Signal(object, object)     # edge, 屏幕坐标
    #: 自动布局排完了（2026-10-27）：一句人话，说"折成了几列几页"。
    #: 为什么不直接在这边弹：画布没有状态栏，窗口把它接到 `_status` 就行
    #: （工具栏 / 右键菜单两条入口因此都能报，不用各写一遍）。
    layout_arranged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.graph = None
        self._items: dict = {}
        self._edges: list = []
        self._entry_id = None
        self._connect_from = None
        self._temp_line = None
        self._panning = False
        self._pan_start = None
        #: 空格按住 = 平移（旧版自己跟踪按键状态；Qt 没有"空格修饰键"）
        self._space_down = False
        self._did_auto_layout = False
        #: 上一次排版走的是哪条路：'logic'（按跳转分层）/ 'file'（按文件顺序）/
        #: ''（这次没有重排）。调用方（导入）用它决定要不要在控制台说一句。
        self.last_arrange = ''
        self._problems: dict = {}
        #: `line_id` → `{index,total,label,prev,text,next}`（S0，2026-10-23）。
        #:
        #: 画布**不持有 session**：它只认这份"拍上下文"。由窗口层在
        #: `_set_graph` 时灌进来（见 `set_beat_context`）——这样画布能单测，
        #: 也不用为了画一行字去翻整个会话。
        self._beat_ctx: dict = {}
        #: 只看某条线（D3）：`None` = 全显示；否则只显示与这个节点相连的边。
        #: **纯视图**，不删任何数据（删线会动剧本，那是另一件事）。
        self._edge_focus = None
        self._empty_hint = ''
        self._panning = False
        self._pan_start = None
        self._did_auto_layout = False
        #: 正在被拖的块（拖到视口边缘要自动滚，见 `begin_node_drag`）
        self._drag_item = None
        #: 拖动中最近一次的**视口坐标**（自动滚按它算方向与速度）
        self._drag_cursor = None
        #: 自动滚的节拍器：只在拖动 + 贴边时走
        self._drag_scroll_timer = QTimer(self)
        self._drag_scroll_timer.setInterval(DRAG_SCROLL_INTERVAL)
        self._drag_scroll_timer.timeout.connect(self._drag_scroll_tick)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 不用橡皮筋框选：左键在块上是"选中 + 拖动"，在空白是"取消选中"
        # （旧版 `on_left_down` 就是这么分的）。块本身仍然可拖。
        self.setDragMode(QGraphicsView.NoDrag)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(_col('bg.canvas')))
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    # ------------------------------------------------------------------
    #  建图
    # ------------------------------------------------------------------

    def set_beat_context(self, mapping):
        """
        灌"这条边在第几拍"的上下文（`line_id → {...}`，见
        `story.flow.beat_context`），并让已有的线重画标签。

        只影响**显示**：不改图、不写盘。换章 / 编辑拍之后由窗口层再灌一次 ——
        所以"第 N 拍"永远跟着当前这批拍走，不会停在上一次的旧值。
        """
        self._beat_ctx = dict(mapping or {})
        for ei in list(getattr(self, '_edges', None) or ()):
            try:
                ei.refresh()
            except Exception:                # noqa: BLE001 - 一条线画坏了不该拖垮整张图
                swallow('graph_canvas.set_beat_context')

    def _apply_edge_focus(self):
        """按 `_edge_focus` 设置每条边的可见性（重建图之后也要调一次）。"""
        target = getattr(self, '_edge_focus', None)
        for ei in list(getattr(self, '_edges', None) or ()):
            if target is None:
                ei.setVisible(True)
                continue
            ei.setVisible(target in (getattr(ei.edge, 'from_id', None),
                                     getattr(ei.edge, 'to_id', None)))

    def set_edge_focus(self, nid):
        """
        只看**跟某个模块相连**的连线（D3，2026-10-24）。`nid=None` 或再点一次 = 恢复。

        ## 为什么是"视图"不是"删除"

        用户说"图太乱想清一清"，第一反应是删线 —— 但删线在 S1 之后**会删剧本那一行**。
        所以"看起来干净"和"删掉数据"必须是两件事：这里只改 `visible`，
        一个字节都不动，随时恢复。

        返回 `{'focus': nid, 'shown': n, 'total': m}`（好让状态栏说清楚）。
        """
        # 再点同一个 = 取消（开关语义，不然用户找不到"怎么恢复"）
        target = None if nid == getattr(self, '_edge_focus', None) else nid
        self._edge_focus = target
        self._apply_edge_focus()

        total = len(getattr(self, '_edges', None) or ())
        shown = 0 if target is None else sum(
            1 for ei in (self._edges or ()) if ei.isVisible())
        return {'focus': target, 'shown': shown if target else total,
                'total': total}

    def set_graph(self, graph, keep_view=None):
        """
        换一张图（**同一个入口**：导入 / 连线 / 撤销 / 布局都走它）。

        用户 2026-10-05：

        > 连线之后，请保持原来的缩放……现在每连一个线，缩放就变……这不好

        图重建以前一律 `_reset_view()`，于是**每连一条线、每撤销一步，缩放和
        视口位置都被重新"适应窗口"**：刚放大到看得清某个块，一连线就被弹回
        全局视图。

        现在默认**保住原来的缩放与中心**（只要之前就已经在显示图，而且这次
        不需要自动排版）；以下情况仍然复位，因为它们确实换了"看什么"：

          · 有**没摆过**的新块（刚导入的章节落在默认坐标）→ 要重新排版，
            自然要重新适应窗口；
          · 调用方显式 `keep_view=False`（加载布局 JSON：用户要看新布局）。

        取的是**变换矩阵 + 视口中心对应的场景点**，还原时逐字塞回去，
        不重算 fitInView（那会带上"这次内容多大"的差异）。

        **排版用哪一种**（2026-10-22）：这一批新块如果**全来自同一个
        `.rpy`**，按**文件顺序**排（一个文件一列、从上到下就是文件里的先后）——
        一个 `.rpy` 里写了多个 label 时，这才是用户心里的"这一章的样子"；
        跨文件（或没有来源）才走按跳转分层的 `auto_layout`。
        """
        state = self._view_state()
        self.graph = graph
        self.last_arrange = ''
        self.scene().clear()
        self._items = {}
        self._edges = []
        self._connect_from = None
        self._temp_line = None
        self.end_node_drag()
        if graph is None:
            self.setSceneRect(QRectF(-400, -300, 800, 600))
            self.viewport().update()
            return
        for nid, node in (graph.nodes or {}).items():
            item = NodeItem(node, canvas=self, mark=self._problems.get(nid))
            self.scene().addItem(item)
            self._items[nid] = item
        for eid, edge in (graph.edges or {}).items():
            src = self._items.get(edge.from_id)
            dst = self._items.get(edge.to_id)
            if src is None or dst is None:
                continue
            ei = EdgeItem(edge, src, dst, canvas=self)
            self.scene().addItem(ei)
            self._edges.append(ei)
            if src is not dst:
                src.edges.append(ei)
            dst.edges.append(ei)
        self._apply_marks()
        # 重建之后要重新应用"只看选中"（否则一连线，刚才的过滤就失效了）
        try:
            self._apply_edge_focus()
        except Exception:                    # noqa: BLE001
            swallow('graph_canvas.set_graph.edge_focus')

        # 场景矩形要留余量（否则小图铺满视口 → "拖画布没反应"）
        self._update_scene_rect()

        # 有没有"还没摆过"的块？有就重排，没有就保留（含用户手工拖过的位置）。
        if self._unplaced_ids():
            self._did_auto_layout = True
            if self._sole_source_file():
                self.arrange_by_file()
            else:
                self.auto_layout()
            return
        self._did_auto_layout = False
        keep = (state is not None) if keep_view is None else bool(keep_view)
        if not (keep and self._restore_view(state)):
            self._reset_view()

    # ------------------------------------------------------------------
    #  视口状态（缩放 / 中心）—— 重建前后原样搬回去
    # ------------------------------------------------------------------

    def _view_state(self):
        """`(变换矩阵, 视口中心对应的场景点)`；还没显示过图时返回 None。"""
        if not self._items:
            return None
        try:
            return (QTransform(self.transform()),
                    self.mapToScene(self.viewport().rect().center()))
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.graph_canvas._view_state', e)
            return None

    def _restore_view(self, state) -> bool:
        """把 `_view_state()` 记下的缩放/中心塞回去（成功返回 True）。"""
        if not state:
            return False
        try:
            transform, center = state
            self.setTransform(transform)
            self._clamp_zoom()
            self.centerOn(center)
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.graph_canvas._restore_view', e)
            return False
        self._emit_zoom()
        return True

    def _unplaced_ids(self) -> set:
        """
        哪些块**还没被摆过**。

        判据（旧版只看"是不是所有块都在同一点"，漏了一半情况）：

          · 坐标与别的块**完全重合** → 至少有一个没摆过（叠在一起没法看）；
          · 坐标正好是解析器的默认值 `(200, 200)` → 这是"刚新建、还没排过"
            的记号（`GraphBuilder` 建 label / 占位节点都用它）。

        为什么必须补上第二种：**导入是累加的**。先导入第一章（整体排好），
        再导入第二章时，新块落在 (200, 200)，而旧规则看到"位置并不都相同"
        就认为布局已存在 → 不排版 → 新块直接压在别人身上（这也是"图糊了"
        的一个来源）。反过来，只要每个块都有各自的坐标，就一律保留
        （用户拖过的位置不该被重新导入冲掉）。
        """
        nodes = getattr(self.graph, 'nodes', None) or {}
        unplaced = set()
        by_pos: dict = {}
        for nid, node in nodes.items():
            key = (round(float(node.x), 3), round(float(node.y), 3))
            if key in by_pos:
                unplaced.add(nid)
                unplaced.add(by_pos[key])
            else:
                by_pos[key] = nid
        if len(nodes) > 1:
            for nid, node in nodes.items():
                if (round(float(node.x), 3), round(float(node.y), 3)) == DEFAULT_POS:
                    unplaced.add(nid)
        return unplaced

    #: 装不下时，把内容左上角留在离视口边缘这么远的地方（像素）
    FLUSH_MARGIN = 32.0

    def _reset_view(self):
        """
        视口复位：**装得下才适应窗口，装不下就 100% 从左上角开始看**。

        为什么不是无脑 `fitInView`：50 多个块 fit 进一屏要缩到 0.4 以下，
        字全成蚂蚁 —— 用户看到的"一堵小方块墙"就是这么来的。

        为什么"从左上角开始"而不是"停在入口块正中"（2026-10-27 改）：布局
        现在会把块**折进舞台**（自动布局排到右边就拐到下一排，文件顺序排满
        一列换右边、排满一页换下面一页 —— 见 `layout._flow_layers_into_bands`
        与 `_flow_into_pages`），入口块就在左上角；把它摆在屏幕正中等于
        **上面空掉半屏**（真实项目 990×847 的舞台里空了 340 px，一屏只能
        看到 4 排块）。对齐左上角，一屏能看到的块最多。

        算过入口（`set_analysis` 给过 `_entry_id`）时仍然停在入口块上：
        那时"从哪开始"比"看最多"更重要，入口不一定在左上角。
        """
        if not self._items:
            return
        rect = self.scene().itemsBoundingRect().adjusted(-40, -40, 40, 40)
        if rect.isEmpty():
            return
        vp = self.viewport().rect()
        scale = min(vp.width() / max(rect.width(), 1),
                    vp.height() / max(rect.height(), 1))
        if scale >= FIT_MIN_SCALE:
            self.fitInView(rect, Qt.KeepAspectRatio)
        else:
            self.resetTransform()           # 100%
            entry = self._entry_id if self._entry_id in self._items else None
            if entry is not None:
                self.center_on_node(entry)
            else:
                self._align_top_left(rect)
        self._emit_zoom()

    def _align_top_left(self, content_rect):
        """把内容的左上角摆到视口左上角（留 `FLUSH_MARGIN`）。100% 缩放时用。"""
        try:
            vp = self.viewport().rect()
            self.centerOn(content_rect.topLeft() + QPointF(
                vp.width() / 2.0 - self.FLUSH_MARGIN,
                vp.height() / 2.0 - self.FLUSH_MARGIN))
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.graph_canvas._align_top_left', e)

    def _top_left_id(self):
        if not self._items:
            return None
        return min(self._items,
                   key=lambda nid: (self._items[nid].node.y,
                                    self._items[nid].node.x))

    #: 场景矩形在内容四周至少留这么多元量（"拖得动"的最低保证，见下）
    SCENE_PAD_MIN = 600.0

    def _update_scene_rect(self):
        """
        给内容四周留出**可拖拽的余量**。

        用户 2026-10-05：

        > 有个拖拽画布没反应的bug你忘记修啦！

        根因不在手势（空格+左键 / 中键两条路都对），而在**场景矩形恰好等于
        内容+40px**：小图 `fitInView` 后内容刚好铺满视口 → 滚动条范围≈0 →
        拖了也没有东西可动。现在按内容尺寸留 60%（至少 600px）的余量，
        `fit_to_content` / `_reset_view` 仍然是按**内容**矩形来适应的
        （它们自己算 `itemsBoundingRect`），所以不会因此缩得更小。
        """
        try:
            rect = self.scene().itemsBoundingRect()
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.graph_canvas._update_scene_rect', e)
            return
        if rect.isEmpty():
            self.setSceneRect(QRectF(-400, -300, 800, 600))
            return
        pad_x = max(self.SCENE_PAD_MIN, rect.width() * 0.6)
        pad_y = max(self.SCENE_PAD_MIN, rect.height() * 0.6)
        self.setSceneRect(rect.adjusted(-pad_x, -pad_y, pad_x, pad_y))

    def fit_to_content(self):
        """适应窗口（工具栏/右键菜单）。"""
        if not self._items:
            return
        rect = self.scene().itemsBoundingRect()
        if rect.isEmpty():
            return
        self.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
        self._clamp_zoom()
        self._emit_zoom()

    def note_content_changed(self):
        """
        块被拖到更远的地方了 → 场景矩形跟着长大。

        为什么必须：可平移的范围**由场景矩形决定**。块被拖到矩形之外，
        它就"看得见但到不了"（拖画布到头也追不上），这正是"拖拽画布时灵
        时不灵"的一个来源 —— 灵不灵取决于你把块拖到哪儿了。
        """
        self._update_scene_rect()

    # ------------------------------------------------------------------
    #  无限舞台（用户 2026-10-05）
    # ------------------------------------------------------------------
    #
    #  > 这个拖不动是因为你的这个舞台有限……可是我的tkinter做的是无限的舞台
    #  > ……（目前我不能整个平移）所以说需要继续修改……
    #
    # 旧版 tkinter 是"世界坐标 + offset"自己画的：`offset_x/offset_y` 想挪多远
    # 挪多远（见旧版 `on_left_drag`），**没有边界**。Qt 版用的是
    # `QGraphicsView`，可平移范围由 `sceneRect` 决定 —— 于是"舞台"被内容
    # 框死了：内容 + 60% 余量就是尽头，拖到那儿再怎么拖都不动。
    #
    # 这里把那个语义补回来：**可见区离边界近了就往外长一截**，长的时候把
    # 视口中心摁住不动（`centerOn`），所以用户只感觉"外面还有地方"，
    # 不会看到画面跳。真正的无限（Math.inf）不行 —— QGraphicsView 的滚动条
    # 需要有限的矩形才能算范围；"按需生长"在体感上与无限等价。

    #: 可见区离边界不足这么多场景像素 → 判定"要碰墙了"
    SCENE_EDGE_MIN = 160.0
    #: 每次往外长的一步（场景像素，按视口大小自适应）
    SCENE_GROW_STEP = 700.0

    def _extend_scene_rect(self) -> bool:
        """
        朝需要的那几边把场景矩形撑大（**视口内容不动**）。撑过返回 True。

        为什么要"先撑再滚"：平移是改滚动条的值，而滚动条范围由场景矩形决定 ——
        到了边界再 `setValue` 会被**夹住**，那一下的位移就丢了（手感上就是
        "拖到头就拖不动了"）。所以在应用位移**之前**先看一眼要不要长。
        """
        rect = self.sceneRect()
        if rect.isEmpty():
            return False
        try:
            vis = self.mapToScene(self.viewport().rect()).boundingRect()
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.graph_canvas._extend_scene_rect', e)
            return False
        step = max(self.SCENE_GROW_STEP,
                   max(vis.width(), vis.height()) * 0.6)
        left = step if vis.left() - rect.left() < self.SCENE_EDGE_MIN else 0.0
        right = step if rect.right() - vis.right() < self.SCENE_EDGE_MIN else 0.0
        top = step if vis.top() - rect.top() < self.SCENE_EDGE_MIN else 0.0
        bottom = (step if rect.bottom() - vis.bottom() < self.SCENE_EDGE_MIN
                  else 0.0)
        if not (left or right or top or bottom):
            return False
        center = self.mapToScene(self.viewport().rect().center())
        grown = QRectF(rect)
        if left:
            grown.setLeft(rect.left() - left)
        if right:
            grown.setRight(rect.right() + right)
        if top:
            # 注意：y 轴向下，"上边"是 top
            grown.setTop(rect.top() - top)
        if bottom:
            grown.setBottom(rect.bottom() + bottom)
        self.setSceneRect(grown)
        # 撑大之后滚动范围变了，`centerOn` 把视口摁回原来那个场景点
        # （不这么做，画面会因为滚动条值被重算而突然跳一下）
        self.centerOn(center)
        return True

    def cancel_interaction(self):
        """
        打断进行中的手势（窗口失活时由主窗口调用）。

        不这么做，手势状态会**卡住**：`_panning` 卡在 True，之后每次左键
        拖动都变成平移（块拖不动、点选像是没反应）；`_connect_from` 卡住，
        那条临时红线会一直挂在那儿。失活时"鼠标释放"事件本来就会丢，
        所以必须由窗口显式打断。
        """
        self._panning = False
        self._pan_start = None
        self._connect_from = None
        self.end_node_drag()
        if self._temp_line is not None:
            try:
                self.scene().removeItem(self._temp_line)
            except Exception:                # noqa: BLE001
                swallow('graph_canvas.cancel_interaction')
            self._temp_line = None
        self._restore_cursor()

    def _sole_source_file(self) -> str:
        """
        图上的块**全来自同一个文件**时返回那个文件的路径，否则返回空串。

        用它决定"新块怎么排"（见 `set_graph`）：一个 `.rpy` 里写了多个 label
        时，按文件顺序排才像"这一章的样子"；跨文件时那个顺序没有意义
        （章与章之间靠 jump / call 相接），得按跳转分层。
        手画的占位块（没有 `file_path`）会让它返回空串 —— 那种图不该猜。
        """
        files = set()
        nodes = getattr(getattr(self, 'graph', None), 'nodes', None) or {}
        for node in nodes.values():
            fp = str(getattr(node, 'file_path', '') or '').strip()
            if not fp:
                return ''
            files.add(fp)
        return next(iter(files)) if len(files) == 1 else ''

    def _apply_node_positions(self):
        """把**模型里的坐标**搬到画面上（布局函数只改模型，不碰 QGraphics）。"""
        for nid, node in self.graph.nodes.items():
            item = self._items.get(nid)
            if item is not None:
                item.setPos(node.x, node.y)
        for ei in self._edges:
            ei.refresh()
        self._reset_view()

    def auto_layout(self):
        """
        自动布局：复用 `storygraph/graph/layout.py`，**间距按节点尺寸传进去**。

        历史事故：节点放大到 208×104 而间距还是旧值 → 同层块垂直重叠。

        这是**按跳转/调用分层**的排法（回答"谁跳到谁"）。一个 `.rpy` 里
        写了多个 label、想把图排成文件里的先后，用 `arrange_by_file()`
        （工具栏「▤ 按文件顺序排列」）。

        **折进舞台**（用户 2026-10-27 两轮话）：

        > 尽可能多的标签整齐排列在舞台内部，而不是一路长排下去……
        > 保持尽量横向的流程，自动实现感知到头就拐弯（分叉一类的还是并排放）

        所以这里是"**层还是左右排开**，排到舞台右边就拐到下面一排、下一排
        反着走"（见 `layout._flow_layers_into_bands`）—— 不是按格子平铺：
        平铺会把"谁跟谁是同一层"摊平，结构图就看不出结构了。
        """
        if self.graph is None or not self.graph.nodes:
            return
        layer_gap, node_gap = _layout_gaps()
        info = {}
        try:
            from storygraph.graph.layout import layout_graph
            info = layout_graph(self.graph, layer_gap=layer_gap,
                                node_gap=node_gap,
                                stage=self._stage_box()) or {}
        except Exception:                # noqa: BLE001
            # 退而求其次：网格排开，至少不叠在一起
            for i, node in enumerate(self.graph.nodes.values()):
                node.x = 40 + (i % 4) * layer_gap
                node.y = 40 + (i // 4) * node_gap
        self.last_arrange = 'logic'
        self._apply_node_positions()
        self._announce_arrange(info)

    def _announce_arrange(self, info):
        """说一句"15 层排成了几排"（没人连这个信号就算了，不能因此炸掉）。"""
        try:
            from storygraph.graph.layout import describe_bands
            self.layout_arranged.emit(
                '⤢ 已按跳转重排：' + (describe_bands(info) or '按跳转分层'))
        except Exception as e:           # noqa: BLE001 - 只是提示
            swallow('ui_qt.graph_canvas._announce_arrange', e)

    #: 可视区比这还小 = 画布还没显示出来（或被压扁）→ 用布局器的默认舞台。
    STAGE_MIN_W, STAGE_MIN_H = 320.0, 240.0

    def _stage_box(self):
        """
        舞台多大 = **画布可视区**多大（场景像素；100% 缩放下 1:1）。

        为什么是可视区而不是整个场景：布局要回答的是"一屏能摆下多少块"，
        场景是无边界的（见 `_extend_scene_rect`），拿它当舞台等于没边。

        画布还没 `show()` 过时 `viewport()` 是默认的 100×30，那时折出来的
        页小得荒唐（一页 0 列），所以低于 `STAGE_MIN_*` 就退回
        `layout.DEFAULT_STAGE`（16:9）—— 这也让无界面测试有确定的舞台。
        """
        try:
            from storygraph.graph.layout import DEFAULT_STAGE
        except Exception as e:           # noqa: BLE001 - 布局模块导不进来也不许崩
            swallow('ui_qt.graph_canvas._stage_box.import', e)
            DEFAULT_STAGE = (1280.0, 720.0)
        try:
            rect = self.viewport().rect()
            w, h = float(rect.width()), float(rect.height())
        except Exception as e:           # noqa: BLE001
            swallow('ui_qt.graph_canvas._stage_box', e)
            return DEFAULT_STAGE
        if w < self.STAGE_MIN_W or h < self.STAGE_MIN_H:
            return DEFAULT_STAGE
        return (w, h)

    def arrange_by_file(self) -> dict:
        """
        按**文件顺序**排列：一个 `.rpy` 一列，同一文件里从上到下就是文件里的先后。

        用户 2026-10-22：

        > 在一个 rpy 文件内定义多个 label……让它识别到并自动排好块逻辑
        > （现在这个流程图是按 label 之间的逻辑来排的，不是按 rpy 文件的逻辑）

        与 `auto_layout` 的关系是**互补**而不是替代：那条路看得见跨章节的
        跳转，这条路看得见"这一章在文件里是怎么写的"。返回 `layout_by_file`
        的摘要（谁在第几列、哪些块没有源文件），调用方拿去写控制台。

        同样**折进舞台**：真实项目 45 个文件并成一排是 12334 宽、
        一屏只看得到 4 个块（用户 2026-10-27："一路长排下去"）。
        """
        if self.graph is None or not self.graph.nodes:
            return {}
        layer_gap, node_gap = _layout_gaps()
        try:
            from storygraph.graph.layout import layout_by_file
            info = layout_by_file(self.graph, col_gap=layer_gap,
                                  row_gap=node_gap, stage=self._stage_box())
        except Exception as e:           # noqa: BLE001 - 排不出来就保持原样
            swallow('ui_qt.graph_canvas.arrange_by_file', e)
            return {}
        self.last_arrange = 'file'
        self._apply_node_positions()
        return info

    # ------------------------------------------------------------------
    #  缩放
    # ------------------------------------------------------------------

    def zoom_percent(self) -> int:
        return int(round(self.transform().m11() * 100))

    def _emit_zoom(self):
        try:
            self.zoom_changed.emit(self.zoom_percent())
        except Exception:                # noqa: BLE001
            swallow('graph_canvas._emit_zoom')

    def _clamp_zoom(self):
        s = self.transform().m11()
        if s <= 0:
            return
        if s < ZOOM_MIN:
            self.scale(ZOOM_MIN / s, ZOOM_MIN / s)
        elif s > ZOOM_MAX:
            self.scale(ZOOM_MAX / s, ZOOM_MAX / s)

    def zoom_by(self, factor: float, at=None):
        """
        以 `at`（视口坐标）为锚点缩放；不给就锚在视口中心。

        旧版 `update_view_transform(zoom, cx, cy)` 就是"以鼠标位置为中心缩放"
        ——滚轮放在哪就往哪放大，所以这里必须支持锚点。
        """
        s = self.transform().m11()
        if s <= 0:
            return
        target = max(ZOOM_MIN, min(ZOOM_MAX, s * factor))
        if target == s:
            return
        if at is None:
            self.scale(target / s, target / s)
            self._extend_scene_rect()      # 缩小后可视区变大 → 舞台跟着长
            self._emit_zoom()
            return
        # 手工实现"锚点不动"：先记住锚点下的场景坐标，缩放后再把它移回原处
        before = self.mapToScene(at)
        self.scale(target / s, target / s)
        after = self.mapToScene(at)
        delta = after - before
        self.translate(delta.x(), delta.y())
        self._extend_scene_rect()          # 缩小后可视区变大 → 舞台跟着长
        self._emit_zoom()

    def zoom_in(self):
        self.zoom_by(1.2)

    def zoom_out(self):
        self.zoom_by(1 / 1.2)

    def zoom_reset(self):
        """回到 100%（停在当前选中块上）。"""
        self.resetTransform()
        self.center_on_node(self.selected_node_id() or self._entry_id
                            or self._top_left_id())
        self._emit_zoom()

    def center_on_node(self, nid):
        item = self._items.get(nid) if nid is not None else None
        if item is None:
            return
        self.centerOn(item)

    # ------------------------------------------------------------------
    #  静态分析结论（画回图上）
    # ------------------------------------------------------------------

    def set_analysis(self, entry_id=None, problem_ids=(), independent_ids=()):
        """
        把分析结论标到图上：入口（绿）、走不到（红）、未接入主线（虚线）。

        静态分析只给一段文字的话，用户还得自己在几十个块里找 —— 标上去
        才是"结构的图把一切连起来"。
        """
        self._entry_id = entry_id
        self._problems = {}
        for nid in problem_ids or ():
            self._problems[nid] = 'stranded'
        for nid in independent_ids or ():
            self._problems.setdefault(nid, 'independent')
        self._apply_marks()

    def clear_analysis(self):
        self._entry_id = None
        self._problems = {}
        self._apply_marks()

    def _apply_marks(self):
        """
        把静态分析结论贴到块 / 线上。

        块只在 `mark` **真的变了**时重绘：`set_graph` 建块时已经把当时的
        `mark` 传进去了（`NodeItem(node, canvas, mark=…)`），这里再刷新一遍
        等于把每个块的文字重新排两遍 —— 120 个块多花 13 ms（2026-10-26 实测），
        而它换来的字一个像素都没变。
        """
        for nid, item in self._items.items():
            mark = self._problems.get(nid)
            if mark == item.mark:
                continue
            item.mark = mark
            item.refresh()
        for ei in self._edges:
            bad = (self._problems.get(ei.edge.from_id) == 'stranded'
                   or self._problems.get(ei.edge.to_id) == 'stranded')
            ei.set_mark('stranded' if bad else None)
        if self.graph is not None:
            self.scene().update()

    @property
    def entry_id(self):
        return self._entry_id

    # ------------------------------------------------------------------
    #  空状态提示
    # ------------------------------------------------------------------

    def set_empty_hint(self, text: str):
        self._empty_hint = str(text or '')
        self.viewport().update()

    def drawForeground(self, painter, rect):
        try:
            if not self._empty_hint or self._items:
                return
            vp = self.viewport().rect()
            top_left = self.mapToScene(vp.topLeft())
            area = QRectF(top_left, self.mapToScene(vp.bottomRight()))
            painter.save()
            f = QFont(theme.font_family(), 11)
            painter.setFont(f)
            painter.setPen(QPen(_col('text.tertiary')))
            painter.drawText(area, Qt.AlignCenter | Qt.TextWordWrap,
                             self._empty_hint)
            painter.restore()
        except Exception:                # noqa: BLE001
            swallow('graph_canvas.drawForeground')

    # ------------------------------------------------------------------
    #  选中 / 高亮 / 查找
    # ------------------------------------------------------------------

    def selected_node_id(self):
        for item in self.scene().selectedItems():
            if isinstance(item, NodeItem):
                return item.node.id
        return None

    def selected_edge_obj(self):
        """当前选中的边（没有就 None）—— Delete 键 / 右栏详情用。"""
        for item in self.scene().selectedItems():
            if isinstance(item, EdgeItem):
                return item.edge
        return None

    def highlight_node(self, nid):
        """把某个块标成"当前"（= 卡片编辑器正开着的那一章）。"""
        for item in self._items.values():
            item.node.is_current = (item.node.id == nid)
            item.refresh()
        self.scene().update()

    def select_node(self, nid, center=True):
        """选中一个块（左栏树点一下 = 图上选中并居中）。"""
        item = self._items.get(nid)
        if item is None:
            return
        self.scene().clearSelection()
        item.setSelected(True)
        if center:
            self.centerOn(item)
        self.selection_changed.emit(nid)

    def find_nodes(self, text: str) -> list:
        """按名字/文件名模糊找块（提示/定位用）。"""
        q = str(text or '').strip().lower()
        if not q:
            return list(self._items)
        out = []
        for nid, item in self._items.items():
            name = str(getattr(item.node, 'label_name', '') or '').lower()
            fp = str(getattr(item.node, 'file_path', '') or '').lower()
            if q in name or q in fp:
                out.append(nid)
        return out

    # ------------------------------------------------------------------
    #  交互 —— **严格照 tkinter 版的操作来**（用户 2026-10-03 明确要求）
    # ------------------------------------------------------------------
    #
    #  > 明明 shift+鼠标左键是连线图，左键也可以直接选中模块进行变量赋值
    #  > 和操作……为什么你非要改到右键呢？你要遵照我旧版的操作来！
    #
    # 所以这张表就是契约（与 `legacy` / tkinter `graph_view.py` 一致）：
    #
    #   Shift + 左键从一个块拖到另一个块   → 连线（松手弹类型对话框）
    #   左键点块                          → 选中（右侧"当前模块"跟着变）
    #   左键拖动块                        → 移动位置（连线跟着走）
    #   双击块                            → **编辑变量操作**
    #   双击空白                          → 新建章节
    #   滚轮                              → 缩放（**不需要 Ctrl**）
    #   空格 + 左键拖 / 中键拖             → 平移画布
    #   右键                              → 菜单
    #
    # 新加的只有"框选之外的橡皮筋"这类**没有**取代旧操作的东西。

    def _scene_pos(self, view_pos) -> QPointF:
        return self.mapToScene(view_pos)

    # ------------------------------------------------------------------
    #  光标：只从这个口子改（会自动复原）
    # ------------------------------------------------------------------

    #: 手势里会改掉的几种形状。**离开手势就必须复原**，否则会卡住。
    _GESTURE_CURSORS = (Qt.ClosedHandCursor, Qt.CrossCursor)

    def _idle_cursor(self):
        """没有手势时该是什么形状：空格按住 = 张开的手（可抓），否则箭头。"""
        return Qt.OpenHandCursor if self._space_down else Qt.ArrowCursor

    def _set_cursor(self, shape):
        """
        改画布光标（**同时改 viewport**）。

        为什么两处都写：`QAbstractScrollArea` 的实际显示面是 `viewport()`，
        只改外层在部分平台/Qt 版本上不生效（或只生效一半）；反正代价是零。
        """
        try:
            self.viewport().setCursor(shape)
        except Exception as e:               # noqa: BLE001
            swallow('ui_qt.graph_canvas.set_cursor.viewport', e)
        try:
            self.setCursor(shape)
        except Exception as e:               # noqa: BLE001
            swallow('ui_qt.graph_canvas.set_cursor', e)

    def _restore_cursor(self):
        """手势结束：回到"没有手势时"的形状（空格还按着就是张手）。"""
        self._set_cursor(self._idle_cursor())

    def _heal_cursor(self):
        """
        自愈：没有手势，却还顶着"抓/十字"的光标 → 复原。

        用户 2026-10-27：「拖动画布的时候鼠标变了，拖动结束后并没有变回来」。
        正常路径（`mouseReleaseEvent`）本来就会复原，但**释放事件可能根本
        没到画布**（拖到窗口外松手、系统吞掉一次 release、切走再回来……）。
        与其逐条堵，不如每次鼠标经过画布时看一眼：状态和光标不一致就改回去。
        只认 `_GESTURE_CURSORS` 这两种 —— 悬停连线时的"手型"是 QGraphicsScene
        自己设的，不能碰。
        """
        try:
            if self._panning or self._connect_from is not None:
                return
            shape = self.cursor().shape()
            want = self._idle_cursor()
            if shape in self._GESTURE_CURSORS and shape != want:
                self._set_cursor(want)
            elif (shape == Qt.OpenHandCursor and want == Qt.ArrowCursor
                  and not self._space_down):
                self._set_cursor(want)
        except Exception as e:               # noqa: BLE001 - 光标不该影响拖拽
            swallow('ui_qt.graph_canvas.heal_cursor', e)

    # ------------------------------------------------------------------
    #  拖块到边界：画布自己走（用户 2026-10-27 的体验优化）
    # ------------------------------------------------------------------

    def begin_node_drag(self, item):
        """
        开始拖一个块（由 `NodeItem.mousePressEvent` 调）。

        记下是哪一个：贴边自动滚的时候，**块要跟着视图一起走**，
        否则画布滚了、块还停在原地，手一松就"掉"到别处去了。
        """
        self._drag_item = item
        self._drag_cursor = None

    def note_drag_cursor(self, view_pos):
        """拖动中鼠标到了哪儿（视口坐标）—— 决定要不要自动滚、滚多快。"""
        if self._drag_item is None:
            return
        self._drag_cursor = view_pos
        dx, dy = self._drag_scroll_delta(view_pos)
        if dx or dy:
            if not self._drag_scroll_timer.isActive():
                self._drag_scroll_timer.start()
        else:
            self._drag_scroll_timer.stop()

    def end_node_drag(self):
        """拖完了：停表、清状态（**不改块的位置**）。"""
        self._drag_item = None
        self._drag_cursor = None
        try:
            self._drag_scroll_timer.stop()
        except Exception as e:               # noqa: BLE001
            swallow('ui_qt.graph_canvas.end_node_drag', e)

    def _drag_scroll_delta(self, view_pos):
        """
        光标离四边多近 → 每个节拍滚多少像素（x, y）。

        速度按**压进边缘的深度**平方增长（贴边慢、压到底快），这样既能
        一格一格微调，也能一口气拖到很远的地方。
        """
        if view_pos is None:
            return 0.0, 0.0
        vp = self.viewport().rect()
        margin = DRAG_SCROLL_MARGIN

        def speed(depth):
            depth = max(0.0, min(float(depth), margin))
            if depth <= 0.0:
                return 0.0
            ratio = depth / margin
            return max(DRAG_SCROLL_MIN,
                       DRAG_SCROLL_MAX * ratio * ratio)

        dx = dy = 0.0
        x, y = view_pos.x(), view_pos.y()
        if x < vp.left() + margin:
            dx = -speed(vp.left() + margin - x)
        elif x > vp.right() - margin:
            dx = speed(x - (vp.right() - margin))
        if y < vp.top() + margin:
            dy = -speed(vp.top() + margin - y)
        elif y > vp.bottom() - margin:
            dy = speed(y - (vp.bottom() - margin))
        return dx, dy

    def _drag_scroll_tick(self):
        """自动滚一格：滚视图 + 把块按同样的场景距离挪过去（块贴在鼠标下）。"""
        if self._drag_item is None or self._drag_cursor is None:
            self._drag_scroll_timer.stop()
            return
        dx, dy = self._drag_scroll_delta(self._drag_cursor)
        if not dx and not dy:
            self._drag_scroll_timer.stop()
            return
        try:
            hb = self.horizontalScrollBar()
            vb = self.verticalScrollBar()
            old_h, old_v = hb.value(), vb.value()
            hb.setValue(old_h + int(round(dx)))
            vb.setValue(old_v + int(round(dy)))
            moved_x = hb.value() - old_h
            moved_y = vb.value() - old_v
            if not moved_x and not moved_y:
                # 已经到头了（场景矩形就这么大）→ 这一轮不滚。
                # **故意不在这里 `_extend_scene_rect`**：撑舞台会连带
                # `centerOn` 重排视口，缩得很小时实测能把画面顶跑 376 px
                # （2026-10-27 自测抓到）。舞台在拖完时由窗口层按内容撑大
                # （`_on_node_moved` → `note_content_changed`），够用且不跳。
                self._drag_scroll_timer.stop()
                return
            scale = abs(self.transform().m11()) or 1.0
            item = self._drag_item
            # 滚动条 +1 = 视口往右走一格 = 同一个屏幕点对应的场景 x 变大 →
            # 块要往**同一个方向**挪同样的场景距离，才一直贴在鼠标下面。
            # （第一版把符号写反了：滚一下、块再反向跑一下，屏幕上直接飞走 2 倍。）
            item.setPos(item.pos() + QPointF(moved_x / scale,
                                             moved_y / scale))
        except Exception as e:               # noqa: BLE001 - 滚不动不该打断拖动
            swallow('ui_qt.graph_canvas.drag_scroll_tick', e)
            self._drag_scroll_timer.stop()

    def _start_connect(self, nid):
        self._connect_from = nid
        if self._temp_line is None:
            pen = QPen(QColor('#d32f2f'), 3, Qt.DashLine)
            self._temp_line = QGraphicsPathItem()
            self._temp_line.setPen(pen)
            self._temp_line.setZValue(50)
            self.scene().addItem(self._temp_line)
        self._update_temp_line(self.mapFromGlobal(QCursor.pos()))
        try:
            self._set_cursor(Qt.CrossCursor)
        except Exception:                # noqa: BLE001
            swallow('graph_canvas._start_connect')

    def _update_temp_line(self, view_pos):
        if self._connect_from is None or self._temp_line is None:
            return
        item = self._items.get(self._connect_from)
        if item is None:
            return
        a = item.sceneBoundingRect()
        p1 = QPointF(a.center().x(), a.center().y())
        p2 = self.mapToScene(view_pos)
        path = QPainterPath(p1)
        path.lineTo(p2)
        self._temp_line.setPath(path)

    def _finish_connect(self, view_pos):
        src = self._connect_from
        self._connect_from = None
        if self._temp_line is not None:
            self.scene().removeItem(self._temp_line)
            self._temp_line = None
        try:
            self._restore_cursor()
        except Exception:                # noqa: BLE001
            swallow('graph_canvas._finish_connect')
        if src is None:
            return
        dst = self._node_at(view_pos)
        if dst is not None and dst != src:
            self.connect_requested.emit(src, dst)

    def wheelEvent(self, event):
        # **旧版就是滚轮直接缩放**（`on_mousewheel` 里没有修饰键判断）。
        # 平移交给"空格 + 左键拖动"，所以滚轮不需要留给滚动条。
        self.zoom_by(1.1 if event.angleDelta().y() > 0 else 1 / 1.1,
                     at=event.position().toPoint())
        event.accept()

    def keyPressEvent(self, event):
        """
        空格按住 = 平移模式（旧版用 `<KeyPress-space>` 自己跟踪状态）。

        ⚠ 曾经这里写的是 `event.modifiers() & Qt.SpaceModifier` ——
        **PySide6 里没有 `SpaceModifier`**（空格是按键不是修饰键），
        于是左键按下时这一行直接抛 `AttributeError`：
        **左键点块、拖块全都失效**（只有右键菜单还能用）。
        用户 2026-10-03 的抱怨"左键也可以直接选中模块"指的就是这个现象。
        """
        if event.key() == Qt.Key_Space:
            self._space_down = True
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space:
            self._space_down = False
            self._restore_cursor()
        super().keyReleaseEvent(event)

    def set_space_down(self, down: bool):
        """
        由**窗口级**按键跟踪统一设置（见 `MainWindowQt.eventFilter`）。

        为什么不能只靠画布自己的 `keyPressEvent`：那要画布**有键盘焦点**才
        收得到 —— 而用户按空格时焦点通常在工具栏/右栏控件上，于是
        "空格 + 左键拖 = 平移"完全没反应（用户 2026-10-05 实测）。
        旧版 tkinter 就是把空格绑在 `root`（整窗）上的，这里恢复那个语义。

        ⚠ 光标**每次都刷新**，不因为 `down` 没变就早退：状态与光标有可能
        因为别的原因对不上（用户 2026-10-27：拖完画布光标没变回来），
        而这里是唯一知道"空格到底按着没有"的地方。
        """
        down = bool(down)
        self._space_down = down
        if not getattr(self, '_panning', False):
            self._set_cursor(self._idle_cursor())

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        panning = (event.button() == Qt.MiddleButton
                   or (event.button() == Qt.LeftButton and self._space_down))
        if panning:
            self._panning = True
            self._pan_start = pos
            self._set_cursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        nid = self._node_at(pos)
        if nid is not None and (event.modifiers() & Qt.ShiftModifier):
            # Shift + 左键拖 = 连线（旧版 `on_left_down` 里的 connect_from）
            self.select_node(nid, center=False)
            self._start_connect(nid)
            event.accept()
            return
        if nid is None:
            # 空白处：清掉选中（旧版行为；不画橡皮筋框选）
            self.scene().clearSelection()
            self.selection_changed.emit(None)
        super().mousePressEvent(event)
        if nid is not None:
            self.selection_changed.emit(nid)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._panning and self._pan_start is not None:
            delta = pos - self._pan_start
            self._pan_start = pos
            # **先撑舞台再滚**：否则到了场景矩形边界，`setValue` 会被夹住，
            # 这一下位移就丢了（"拖到头就拖不动"）。见 `_extend_scene_rect`。
            self._extend_scene_rect()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._connect_from is not None:
            self._update_temp_line(pos)
        self._heal_cursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self._pan_start = None
            # 空格还按着就回到"张手"，不是无脑箭头（2026-10-27 用户反馈的
            # 那条"拖动结束后鼠标没变回来"，正常路径就在这一行复原）
            self._restore_cursor()
            event.accept()
            return
        if self._connect_from is not None:
            self._finish_connect(event.position().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._heal_cursor()
        self.selection_changed.emit(self.selected_node_id())

    def mouseDoubleClickEvent(self, event):
        pos = event.position().toPoint()
        node = self._node_at(pos)
        if node is not None:
            # **双击块 = 编辑变量操作**（旧版 `on_double_click` →
            # `edit_operations_dialog`）。进卡片是「✎ 打开本章剧本」按钮 /
            # 右键菜单那条路 —— 两个动作分开，别互相顶掉。
            self.node_ops_requested.emit(node)
            event.accept()
            return
        edge = self._edge_at(pos)
        if edge is not None:
            # 线的标签是 EdgeItem 的子项：双击标签时事件冒到 view，
            # 以前会落进"双击空白 = 新建章节"（用户实测弹了「添加新章节」）。
            self.edge_edit_requested.emit(edge.edge)
            event.accept()
            return
        wx = self.mapToScene(pos)
        self.node_add_requested.emit(wx.x(), wx.y())
        event.accept()

    def _node_at(self, view_pos):
        """视口坐标 → 命中的块 id（穿透到块上的文字子项）。"""
        item = self.itemAt(view_pos)
        while item is not None and not isinstance(item, NodeItem):
            item = item.parentItem()
        return item.node.id if isinstance(item, NodeItem) else None

    def _edge_at(self, view_pos):
        """视口坐标 → 命中的边（穿透到边上的标签子项）。"""
        item = self.itemAt(view_pos)
        while item is not None and not isinstance(item, EdgeItem):
            item = item.parentItem()
        return item if isinstance(item, EdgeItem) else None

    def contextMenuEvent(self, event):
        edge = self._edge_at(event.pos())
        if edge is not None:
            # 先把这条线选中（视觉上告诉用户"菜单开的是这一条"）
            self.scene().clearSelection()
            edge.setSelected(True)
            self.edge_menu_requested.emit(edge.edge,
                                          self.mapToGlobal(event.pos()))
            event.accept()
            return
        nid = self._node_at(event.pos())
        if nid is not None:
            self.select_node(nid, center=False)
        self.node_menu_requested.emit(nid, self.mapToGlobal(event.pos()))
