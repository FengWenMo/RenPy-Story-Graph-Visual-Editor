"""
storygraph.ui_qt.diagnostics
============================
把"静默失败"从一大片红字变成**能看懂的摘要**。

用户 2026-10-22（截图里 212 条 `[静默失败]`）：

> 一大片红字，我不知道该处理哪条。

问题不在"日志详细"，在**把诊断成本转嫁给了用户**：真 bug、缺 SDK、
173 张缩略图没加载，混成同一种格式。这里做三件事：

  · 按严重程度分组：代码问题 / 环境未配置 / 资源未加载；
  · 同类聚合：`thumb.loader.request ×173` 一行，不是 173 行；
  · 详情可展开、可复制，但**默认只展开"需要处理"的代码问题**。

分类规则只按 context 的**命名**判断（`thumb` / `sdk` / `asset` …），
不猜异常内容 —— 猜错分类比不分类更误导。
"""

from __future__ import annotations

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme

#: 分组顺序 = 用户该处理的先后（代码问题最先，资源最后）。
GROUPS = (
    ('code', '代码问题', '这些是工具内部的错误，会影响功能；可以复制给开发者。',
     'state.error'),
    ('environment', '环境未配置', '缺 Ren\'Py SDK 之类；只影响引擎校验 / GUID 恢复，'
     '不影响预览与导出。', 'state.warning'),
    ('resource', '资源未加载', '缩略图 / 音频等缺失；不影响剧情数据。',
     'text.tertiary'),
)

#: context 关键词 → 分组。**先匹配到的赢**；都不匹配 = code。
_RESOURCE_WORDS = ('thumb', 'asset', 'image', 'sprite', 'prewarm', 'thumb_loader',
                   'audio', 'sound', 'voice', 'font', 'icon', 'thumbnail',
                   'texture', 'portrait', 'music')
_ENV_WORDS = ('sdk', 'engine_lint', 'engine_', 'recover_guid', 'renpy_sdk',
              'lint', 'missing_sdk', 'environment')


def category_of(context: str) -> str:
    """按 context 名字给一个分组 key（`code` / `environment` / `resource`）。"""
    name = str(context or '').lower()
    if any(w in name for w in _ENV_WORDS):
        return 'environment'
    if any(w in name for w in _RESOURCE_WORDS):
        return 'resource'
    return 'code'


def summary_line(items) -> str:
    """
    一行摘要：`1 个代码问题 · 173 条资源未加载`。

    `items` 是 `[(context, count, last), ...]`（`safeguard.recent()` 的形状）。
    """
    buckets = {'code': 0, 'environment': 0, 'resource': 0}
    for ctx, count, _last in (items or ()):
        buckets[category_of(ctx)] += int(count or 0)
    parts = []
    if buckets['code']:
        parts.append(f'{buckets["code"]} 个代码问题')
    if buckets['environment']:
        parts.append(f'{buckets["environment"]} 条环境问题')
    if buckets['resource']:
        parts.append(f'{buckets["resource"]} 条资源未加载')
    return ' · '.join(parts) or '没有跳过任何东西'


def format_report(items, total: int = None) -> str:
    """可复制的诊断全文：先摘要，再按组列出 context ×次数 + 最后一条详情。"""
    items = list(items or ())
    if total is None:
        total = sum(int(n or 0) for _c, n, _l in items)
    lines = [f'诊断：{summary_line(items)}（共 {total} 次跳过）']
    for key, title, _hint, _role in GROUPS:
        group = [(c, n, last) for c, n, last in items
                 if category_of(c) == key]
        if not group:
            continue
        lines.append('')
        lines.append(f'[{title}]')
        for ctx, count, last in group:
            tail = f' —— {last}' if last else ''
            lines.append(f'  · {ctx} ×{count}{tail}')
    return '\n'.join(lines)


class DiagnosticsDialog(QDialog):
    """诊断详情：分组树 + 复制。默认只展开"代码问题"。"""

    def __init__(self, parent=None, items=None, total: int = None):
        super().__init__(parent)
        self.setWindowTitle('诊断详情')
        self.resize(820, 560)
        theme.apply_to(self)
        self._items = list(items or ())
        self._total = (sum(int(n or 0) for _c, n, _l in self._items)
                       if total is None else int(total))

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        head = QLabel(f'{summary_line(self._items)}（共 {self._total} 次跳过）')
        head.setWordWrap(True)
        head.setProperty('role', 'section')
        root.addWidget(head)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(['位置 / 说明', '次数'])
        self._tree.setColumnWidth(0, 640)
        self._tree.setRootIsDecorated(True)
        root.addWidget(self._tree, 1)

        for key, title, hint, role in GROUPS:
            group = [(c, n, last) for c, n, last in self._items
                     if category_of(c) == key]
            if not group:
                continue
            total_n = sum(int(n or 0) for _c, n, _l in group)
            top = QTreeWidgetItem([f'{title}（{total_n}）', str(total_n)])
            top.setToolTip(0, hint)
            color = T.t(role)
            top.setForeground(0, QBrush(QColor(color)))
            self._tree.addTopLevelItem(top)
            for ctx, count, last in group:
                child = QTreeWidgetItem(
                    [ctx + (f' —— {last}' if last else ''), str(count)])
                child.setToolTip(0, last or ctx)
                top.addChild(child)
            top.setExpanded(key == 'code')

        bar = QHBoxLayout()
        copy = QPushButton('复制诊断')
        copy.setToolTip('把摘要和明细一起复制到剪贴板（贴给开发者时用这个）')
        copy.clicked.connect(self._copy)
        bar.addWidget(copy)
        clear = QPushButton('清空记录')
        clear.setToolTip('只清这份诊断记录，不动图、不动文件')
        clear.clicked.connect(self._clear)
        bar.addWidget(clear)
        bar.addStretch(1)
        close = QPushButton('关闭')
        close.setProperty('variant', 'primary')
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        root.addLayout(bar)

    def text(self) -> str:
        return format_report(self._items, self._total)

    def _copy(self):
        from PySide6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.text())

    def _clear(self):
        from storygraph.core import safeguard
        safeguard.clear()
        self._items = []
        self._total = 0
        self._tree.clear()
        self.accept()


__all__ = ['GROUPS', 'DiagnosticsDialog', 'category_of', 'format_report',
           'summary_line']
