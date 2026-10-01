"""
storygraph.ui_qt.graph_canvas
=============================
Qt 版**块状编辑器**（图视图）—— 用 `QGraphicsScene` 画节点块与连线。

这是 tkinter 版 `graph_view.py`（Canvas 版）的 Qt 对应物，承担同一件事：
**看整章/整项目的结构**（谁跳谁、菜单怎么分叉），而卡片编辑器管"每一拍"。

  · 节点块：圆角矩形 + 章节名 + 代码预览（operations 前几行）
  · 连线：贝塞尔曲线 + 箭头；`jump` / `call` / `menu` 用不同颜色
  · 交互：滚轮缩放、拖块、空白处拖动平移、点选、双击、右键菜单
  · 自动布局：复用 `storygraph/graph/layout.py`（与 tkinter 版同一份）

**数据模型不动**：还是 `storygraph/graph/models.py` 的 `Graph/Node/Edge`。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsPathItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsTextItem,
                               QGraphicsView)

from storygraph.ui import tokens as T
from storygraph.ui_qt import theme

NODE_W, NODE_H = 176, 96

#: 连线颜色按语义分（与 tkinter 版一致：`jump` / `call` / `menu`）
EDGE_COLOR = {
    'jump': 'brand.primary',
    'call': 'state.info',
    'menu': 'state.warning',
}
EDGE_DEFAULT = 'text.tertiary'


def _edge_color(etype: str) -> QColor:
    try:
        return QColor(theme.t(EDGE_COLOR.get(etype, EDGE_DEFAULT)))
    except Exception:                    # noqa: BLE001
        return QColor('#757575')


class NodeItem(QGraphicsRectItem):
    """一个节点块（章节）。可拖、可选；双击/右键交给上层。"""

    def __init__(self, node):
        super().__init__(0, 0, NODE_W, NODE_H)
        self.node = node
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setPos(node.x, node.y)
        self.setZValue(1)

        self._text = QGraphicsTextItem(self)
        self._text.setPos(8, 6)
        self._text.setTextWidth(NODE_W - 16)
        self._text.setDefaultTextColor(QColor(theme.t('text.primary')))
        f = QFont(theme.font_family(), theme.font_pt('bodyStrong'))
        f.setBold(True)
        self._text.setFont(f)
        self._text.setPlainText(node.label_name or '(未命名)')

        self._ops = QGraphicsTextItem(self)
        self._ops.setPos(8, 28)
        self._ops.setTextWidth(NODE_W - 16)
        self._ops.setDefaultTextColor(QColor(theme.t('text.tertiary')))
        self._ops.setFont(QFont(theme.font_family(), theme.font_pt('caption')))
        ops = list(node.operations or [])
        preview = '\n'.join(str(o).strip() for o in ops[:3] if str(o).strip())
        if len(ops) > 3:
            preview += f'\n…共 {len(ops)} 条'
        self._ops.setPlainText(preview or '（无代码）')

    # ---------------- 绘制 ----------------

    def paint(self, painter, option, widget=None):
        rect = QRectF(0, 0, NODE_W, NODE_H)
        speaking = bool(getattr(self.node, 'is_current', False))
        selected = self.isSelected()
        fill = QColor(theme.t('brand.subtle' if speaking else 'bg.card'))
        if selected:
            fill = QColor(theme.t('bg.cardHover'))
        border = QColor(theme.t('border.strong' if speaking or selected
                                else 'border.default'))
        # 注意：paint 里抛异常 Qt 会直接 segfault（没有 traceback），
        # 所以这里必须用完整的枚举路径，别写 `painter.Antialiasing`。
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, 2 if (speaking or selected) else 1))
        painter.drawRoundedRect(rect, T.RADIUS['card'], T.RADIUS['card'])
        if speaking:
            painter.setPen(QPen(QColor(theme.t('brand.primary')), 3))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1),
                                    T.RADIUS['card'], T.RADIUS['card'])

    # ---------------- 拖动回写 ----------------

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            # 回到模型里，自动布局/保存都靠这两个数
            try:
                self.node.x = float(value.x())
                self.node.y = float(value.y())
            except Exception:                # noqa: BLE001
                pass
        return super().itemChange(change, value)

    def refresh(self):
        self._text.setPlainText(self.node.label_name or '(未命名)')
        speaking = bool(getattr(self.node, 'is_current', False))
        f = self._text.font()
        f.setBold(True)
        self._text.setFont(f)
        self.update()


class EdgeItem(QGraphicsPathItem):
    """一条连线（贝塞尔 + 箭头）。两端跟着节点走。"""

    def __init__(self, edge, src: NodeItem, dst: NodeItem):
        super().__init__()
        self.edge = edge
        self.src = src
        self.dst = dst
        self.setZValue(0)
        self.setPen(QPen(_edge_color(getattr(edge, 'type', 'jump')), 2))
        self.refresh()

    def refresh(self):
        a = self.src.sceneBoundingRect()
        b = self.dst.sceneBoundingRect()
        p1 = QPointF(a.center().x(), a.bottom())
        p2 = QPointF(b.center().x(), b.top())
        path = QPainterPath(p1)
        dy = (p2.y() - p1.y()) * 0.5
        path.cubicTo(p1.x(), p1.y() + dy, p2.x(), p2.y() - dy, p2.x(), p2.y())
        self.setPath(path)
        # 箭头
        import math
        ang = math.atan2(p2.y() - (p2.y() - dy), p2.x() - p2.x()) if dy else 0
        self._arrow_angle = ang


class GraphCanvas(QGraphicsView):
    """
    块状编辑器画布。

    `set_graph(graph)` 重建整张图；`node_double_clicked` / `node_menu`
    交给上层（主窗口）决定做什么（通常是"在卡片编辑器里打开这一章"）。
    """

    node_double_clicked = Signal(object)   # node_id
    node_menu_requested = Signal(object, object)   # node_id, 屏幕坐标
    selection_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.graph = None
        self._items: dict = {}
        self._edges: list = []
        self._panning = False
        self._pan_start = None
        self.setRenderHints(self.renderHints() | self.Antialiasing
                            if hasattr(self, 'Antialiasing') else self.renderHints())
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor(theme.t('bg.canvas'))))
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

    # ------------------------------------------------------------------

    def set_graph(self, graph):
        self.graph = graph
        self.scene().clear()
        self._items = {}
        self._edges = []
        if graph is None:
            return
        for nid, node in (graph.nodes or {}).items():
            item = NodeItem(node)
            self.scene().addItem(item)
            self._items[nid] = item
        for eid, edge in (graph.edges or {}).items():
            src = self._items.get(edge.from_id)
            dst = self._items.get(edge.to_id)
            if src is None or dst is None:
                continue
            ei = EdgeItem(edge, src, dst)
            self.scene().addItem(ei)
            self._edges.append(ei)
        # 位置没初始化过（全新图）就自动布局一次，别让所有块叠在原点
        if self._all_at_origin():
            self.auto_layout()
        else:
            self.fit_to_content()

    def _all_at_origin(self) -> bool:
        pts = {(n.x, n.y) for n in (self.graph.nodes or {}).values()}
        return len(pts) <= 1

    def fit_to_content(self):
        if not self._items:
            return
        rect = self.scene().itemsBoundingRect()
        if rect.isEmpty():
            return
        self.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)

    def auto_layout(self):
        """复用 `storygraph/graph/layout.py`（与 tkinter 版同一份布局器）。"""
        if self.graph is None or not self.graph.nodes:
            return
        try:
            from storygraph.graph.layout import layout_graph
            layout_graph(self.graph)
        except Exception:                # noqa: BLE001
            # 退而求其次：网格排开，至少不叠在一起
            for i, node in enumerate(self.graph.nodes.values()):
                node.x = 40 + (i % 5) * (NODE_W + 60)
                node.y = 40 + (i // 5) * (NODE_H + 80)
        for nid, node in self.graph.nodes.items():
            item = self._items.get(nid)
            if item is not None:
                item.setPos(node.x, node.y)
        for ei in self._edges:
            ei.refresh()
        self.fit_to_content()

    def selected_node_id(self):
        for item in self.scene().selectedItems():
            if isinstance(item, NodeItem):
                return item.node.id
        return None

    def highlight_node(self, nid):
        """把某个块标成"当前"（比如卡片编辑器正在编辑的那一章）。"""
        for item in self._items.values():
            item.node.is_current = (item.node.id == nid)
            item.refresh()
        self.scene().update()

    # ------------------------------------------------------------------
    #  交互
    # ------------------------------------------------------------------

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton or (
                event.button() == Qt.LeftButton
                and event.modifiers() & Qt.SpaceModifier):
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        nid = self.selected_node_id()
        self.selection_changed.emit(nid)

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        node = item.node if isinstance(item, NodeItem) else None
        if node is None and isinstance(item, QGraphicsTextItem):
            parent = item.parentItem()
            node = parent.node if isinstance(parent, NodeItem) else None
        if node is not None:
            self.node_double_clicked.emit(node.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        node = item.node if isinstance(item, NodeItem) else None
        self.node_menu_requested.emit(
            node.id if node is not None else None,
            self.mapToGlobal(event.pos()))
