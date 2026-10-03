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

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
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

    def __init__(self, node, canvas=None):
        super().__init__(0, 0, NODE_W, NODE_H)
        self.node = node
        self.canvas = canvas
        self.edges: list = []
        #: 静态分析结论：'stranded' / 'independent' / None（由画布设置）
        self.mark = None
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
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.graph_canvas.mousePressEvent', e)
            self._drag_from = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """
        拖完发 `node_moved`（仅当位置真的变了）。

        位置回写模型是在 `itemChange` 里做的（拖动过程中实时），所以这里
        只负责"报告区间"：撤销栈拿它造一条 `MoveNodeCommand`，撤销时把
        坐标改回去、重做时再改回来。
        """
        super().mouseReleaseEvent(event)
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
        self._apply_pen()
        self.refresh()

    # ---------------- 语义 ----------------

    def _label_text(self) -> str:
        opt = str(getattr(self.edge, 'option_text', '') or '').strip()
        cond = str(getattr(self.edge, 'condition', '') or '').strip()
        if opt:
            return f'「{opt}」'
        if cond:
            return f'if {cond}'
        return ''

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
        if etype == 'call':
            pen.setStyle(Qt.DashLine)
        if self.mark == 'stranded':
            pen = QPen(_col('state.error'), width)
        self.setPen(pen)

    def set_mark(self, mark):
        self.mark = mark
        self._apply_pen()

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
            # 标签放在中点上方（压在线上的话会把线挡断）
            text = self._label_text()
            self._label.setText(_elide(text, self._label.font(), 200))
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
        self._problems: dict = {}
        self._empty_hint = ''
        self._panning = False
        self._pan_start = None
        self._did_auto_layout = False
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
        """
        state = self._view_state()
        self.graph = graph
        self.scene().clear()
        self._items = {}
        self._edges = []
        self._connect_from = None
        self._temp_line = None
        if graph is None:
            self.setSceneRect(QRectF(-400, -300, 800, 600))
            self.viewport().update()
            return
        for nid, node in (graph.nodes or {}).items():
            item = NodeItem(node, canvas=self)
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

        # 场景矩形要留余量（否则小图铺满视口 → "拖画布没反应"）
        self._update_scene_rect()

        # 有没有"还没摆过"的块？有就重排，没有就保留（含用户手工拖过的位置）。
        if self._unplaced_ids():
            self._did_auto_layout = True
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

    def _reset_view(self):
        """
        视口复位：**装得下才适应窗口，装不下就 100% 停在入口块**。

        为什么不是无脑 `fitInView`：50 多个块 fit 进一屏要缩到 0.4 以下，
        字全成蚂蚁 —— 用户看到的"一堵小方块墙"就是这么来的。
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
            self.resetTransform()
            target = self._entry_id or self._top_left_id()
            self.center_on_node(target)
        self._emit_zoom()

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
        if self._temp_line is not None:
            try:
                self.scene().removeItem(self._temp_line)
            except Exception:                # noqa: BLE001
                swallow('graph_canvas.cancel_interaction')
            self._temp_line = None
        try:
            self.setCursor(Qt.OpenHandCursor if self._space_down
                           else Qt.ArrowCursor)
        except Exception:                    # noqa: BLE001
            swallow('graph_canvas.cancel_interaction')

    def auto_layout(self):
        """
        自动布局：复用 `storygraph/graph/layout.py`，**间距按节点尺寸传进去**。

        历史事故：节点放大到 208×104 而间距还是旧值 → 同层块垂直重叠。
        """
        if self.graph is None or not self.graph.nodes:
            return
        layer_gap, node_gap = _layout_gaps()
        try:
            from storygraph.graph.layout import layout_graph
            layout_graph(self.graph, layer_gap=layer_gap, node_gap=node_gap)
        except Exception:                # noqa: BLE001
            # 退而求其次：网格排开，至少不叠在一起
            for i, node in enumerate(self.graph.nodes.values()):
                node.x = 40 + (i % 4) * layer_gap
                node.y = 40 + (i // 4) * node_gap
        for nid, node in self.graph.nodes.items():
            item = self._items.get(nid)
            if item is not None:
                item.setPos(node.x, node.y)
        for ei in self._edges:
            ei.refresh()
        self._reset_view()

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
        for nid, item in self._items.items():
            item.mark = self._problems.get(nid)
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
            self.setCursor(Qt.CrossCursor)
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
            self.setCursor(Qt.ArrowCursor)
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
            self.setCursor(Qt.ArrowCursor)
        super().keyReleaseEvent(event)

    def set_space_down(self, down: bool):
        """
        由**窗口级**按键跟踪统一设置（见 `MainWindowQt.eventFilter`）。

        为什么不能只靠画布自己的 `keyPressEvent`：那要画布**有键盘焦点**才
        收得到 —— 而用户按空格时焦点通常在工具栏/右栏控件上，于是
        "空格 + 左键拖 = 平移"完全没反应（用户 2026-10-05 实测）。
        旧版 tkinter 就是把空格绑在 `root`（整窗）上的，这里恢复那个语义。
        """
        down = bool(down)
        if down == getattr(self, '_space_down', False):
            return
        self._space_down = down
        if not getattr(self, '_panning', False):
            self.setCursor(Qt.OpenHandCursor if down else Qt.ArrowCursor)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        panning = (event.button() == Qt.MiddleButton
                   or (event.button() == Qt.LeftButton and self._space_down))
        if panning:
            self._panning = True
            self._pan_start = pos
            self.setCursor(Qt.ClosedHandCursor)
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
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        if self._connect_from is not None:
            self._finish_connect(event.position().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)
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
        wx = self.mapToScene(pos)
        self.node_add_requested.emit(wx.x(), wx.y())
        event.accept()

    def _node_at(self, view_pos):
        """视口坐标 → 命中的块 id（穿透到块上的文字子项）。"""
        item = self.itemAt(view_pos)
        while item is not None and not isinstance(item, NodeItem):
            item = item.parentItem()
        return item.node.id if isinstance(item, NodeItem) else None

    def contextMenuEvent(self, event):
        nid = self._node_at(event.pos())
        if nid is not None:
            self.select_node(nid, center=False)
        self.node_menu_requested.emit(nid, self.mapToGlobal(event.pos()))
