"""
storygraph.ui_qt.size_panel
===========================
卡片上的**「尺寸」一格**（P1，2026-10-31）：背景 + 各 CG/UI 图层，每行一个
「预设」下拉 + 一个「改」按钮。

用户原话：

> 针对 bg 和 cg 不能改像素的问题……就是之前那个超清像素导入导致的错位

尺寸面板解决的是"人话 → 可写的东西"这一段：人只会说"铺满 / 适应 / 50%"，
`.rpy` 里必须写 transform 名。匹配在 `story.size_presets`（按**效果**匹配，
不按名字猜），这里只负责**把行画出来、把选择送出去**。

## 这个控件不认识会话

它只吃 `rows`（`app.stage_render.size_rows` 的结果），吐一个请求 dict：

```python
{'row': 0, 'key': 'cover', 'at': 'bgfit', 'kind': 'scene', 'tag': '',
 'spec': 'bg bg7_0', 'source': 0, 'generate': False, 'natural': (2304, 1296)}
```

`generate=True` 的条目（`at` 是个**还不存在**的 `sg_*` 名字）表示：确认之后
要先往 `game/sg_transforms.rpy` 里写一条定义，再改这一行。写盘与护栏都在
`story.sg_transforms`，面板只负责如实转达那条请求。

## 「换景」是另一个按钮（2026-10-31）

背景行还多一个「换景」——它发 `scene_requested`，形状同上，但 `kind`
恒为 `'scene'`、`spec` 是**现在这张**背景，`at` 是下拉里选中的预设（当作
**新背景**要套的变换）。为什么单独一个按钮：Ren'Py 的 `scene` 会清掉
master 层上**所有** displayable（立绘全没），所以它必须是**人明确按下**的
动作，确认框里还要写清"会清掉台上 N 个立绘"（`app.stage.SceneEdit`）。
面板自己不弹框、不数人 —— 它只把请求递出去。

真正的计划 / diff 确认 / 写回在窗口层（`main_window._on_card_size_requested`
与 `main_window.apply_scene_from_card`）：面板不 import 会话、不碰 `.rpy`。

## 行下面那一句"本景还有 N 处单独改写"（2026-10-31）

尺寸是**持续状态**，改的是最初写下这一行的那一拍；可本景后面只要有谁又
`show` 了一次同一个对象，Ren'Py 就重建 displayable —— 前面那行 `at` 从那一刻
起不再生效。面板原来对这件事**一个字都不说**，用户改完看的是当下那一拍，
于是"改好了"和"其实没改好"长得一模一样。

`row['advice']`（`story.override_advice.OverrideAdvice`）就是那句提示：

  · `advice is None` → 不给拍表（单测/探针），整行不显示；
  · `level='none'`（0 处）→ 不显示（不多话）;
  · `level='hint'`（1–2 处）→ 一行灰字；
  · `level='notice'`（≥3 处）→ 一行琥珀色（`role="warn"`）；
  · `unknown=True` → 说"统计不出"，**绝不冒充 0 处**。

完整句子（含"合并回源"那句建议）在 tooltip 里 —— 右栏窄，一屏四行放不下
三句话。这一句**不拦任何操作**：它报的是"改源到第几拍为止有效"，不是"不能改"。

## 为什么固定 4 个槽而不是动态建控件

卡片每翻一拍就要刷一次（真机一章 285 拍）。每次 `clear()` + 新建 QLabel /
QComboBox 在 offscreen 下实测能感到卡顿，而槽位最多 4 个（1 背景 + 3 图层，
`stage_render.SIZE_ROW_LAYERS`）—— 建一次、换文字最省。没用到的槽 `hide()`。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.wheel_guard import WheelSafeComboBox
from storygraph.ui_qt.wrap_label import NarrowWrapLabel

#: 与 `app.stage_render.SIZE_ROW_LAYERS` 对齐：1 个背景 + 3 个图层。
SIZE_SLOTS = 4


class SizePanel(QWidget):
    """「尺寸」一格：每行 = 一行尺寸说明 + 预设下拉 + 「改」（背景行多一个「换景」）。"""

    #: 用户按了某一行的「改」：参数是请求 dict（见模块 docstring）
    size_requested = Signal(object)
    #: 用户按了**背景行**的「换景」：参数是请求 dict（多了 `kind='scene'`）
    scene_requested = Signal(object)

    def __init__(self, parent=None, slots: int = SIZE_SLOTS):
        super().__init__(parent)
        self._rows: list = []
        self._slots: list = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.SPACE['xs'])
        self._empty = QLabel('')
        self._empty.setProperty('role', 'caption')
        self._empty.setWordWrap(True)
        self._empty.setVisible(False)
        lay.addWidget(self._empty)
        for i in range(max(1, int(slots))):
            slot = self._build_slot(i)
            lay.addWidget(slot['widget'])
            self._slots.append(slot)

    # ---------- 界面 ----------

    def _build_slot(self, index: int) -> dict:
        slot = {'index': index, 'row': None}
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        note = NarrowWrapLabel('')
        note.setProperty('role', 'note')
        note.setWordWrap(True)
        note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(note)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(T.SPACE['xs'])
        combo = WheelSafeComboBox()
        combo.setToolTip(
            '这一行要用哪个尺寸。\n'
            '「铺满 / 适应 / 50%」会先按**几何**在项目里找效果完全一样的 '
            'transform，找得到才列出来（名字后面就是它）。\n'
            '找不到时列的是「生成 sg_fit_cover」这类条目 —— **只有你按下'
            '「改」并确认之后**才会新建 game/sg_transforms.rpy（工具生成、'
            '带指纹；以后手改过就拒绝覆盖）。\n'
            '「原样」= 去掉 at；「自定义」= 直接挑项目里已有的 transform。')
        btn = QPushButton('改')
        btn.setToolTip(
            '改这一行的尺寸（先看 diff 再确认）。\n'
            '尺寸是持续状态：改的是**最初写下这一行的那一拍**（说明里有拍号），'
            '影响从那一拍起、直到下一次被覆盖。')
        btn.setMaximumWidth(40)
        # —— 「换景…」（2026-10-31，只给背景行）——
        #
        # 换景是**另一件事**：`scene` 清的是整个 master 层（立绘全没），
        # 所以必须是人**明确按下**的动作，而且确认框要写清会清掉几个立绘。
        # 它不放在「改」里，也不用同一个按钮 —— 用户原话：
        # "只有在人明确选'换景'时才写"。
        swap = QPushButton('换景')
        swap.setMaximumWidth(48)
        swap.setToolTip(
            '换成另一张背景（写 `scene`）。\n'
            '⚠ Ren\'Py 的 `scene` 会**清掉台上所有立绘**（master 层全部 '
            'displayable）——确认框里会写清这一拍会清掉谁。\n'
            '只想改这张背景的大小、不想清台：用右边那个「改」（它写 show）。')
        swap.setVisible(False)
        swap.clicked.connect(lambda _c=False, idx=index: self._emit_scene(idx))
        combo.currentIndexChanged.connect(
            lambda _i, idx=index: self._sync_apply_btn(idx))
        btn.clicked.connect(lambda _c=False, idx=index: self._emit(idx))
        row.addWidget(combo, 1)
        row.addWidget(btn, 0)
        row.addWidget(swap, 0)
        col.addLayout(row)
        # —— 「本景还有几处单独改写」那一行（2026-10-31）——
        #
        # 只读提示，**不拦**：它说的是"改源只到第 X 拍有效"，不是"不能改"。
        # 完整句子进 tooltip（框窄，一屏四行放不下三句）；
        # `notice`（≥3 处）用 `role="warn"` 上色 —— 强弱靠颜色与 tooltip 区分，
        # 不靠把句子写得更长。
        advice = NarrowWrapLabel('')
        advice.setProperty('role', 'warn')
        advice.setWordWrap(True)
        advice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        advice.setVisible(False)
        col.addWidget(advice)
        box.setVisible(False)
        slot.update({'widget': box, 'note': note, 'combo': combo, 'button': btn,
                     'swap': swap, 'advice': advice})
        return slot

    # ---------- 数据 ----------

    def set_rows(self, rows) -> None:
        """按 `size_rows(...)` 的结果刷新（`[]` = 这一拍没有背景/图层）。"""
        self._rows = [r for r in (rows or ()) if isinstance(r, dict)]
        for i, slot in enumerate(self._slots):
            row = self._rows[i] if i < len(self._rows) else None
            if row is None:
                slot['row'] = None
                slot['widget'].setVisible(False)
                continue
            slot['row'] = row
            slot['widget'].setVisible(True)
            slot['note'].setText(str(row.get('note') or '').strip())
            # 「换景」只属于背景行：CG/UI 图层换图不是换景（那只写 show）。
            slot['swap'].setVisible(str(row.get('kind') or 'scene') == 'scene')
            self._fill_advice(slot, row)
            self._fill_combo(slot, row)
        if not self._rows:
            self._empty.setText('这一拍没有背景 / CG / UI 图层，没有尺寸可调。')
        self._empty.setVisible(not self._rows)

    def _fill_advice(self, slot: dict, row: dict) -> None:
        """
        这一行的"本景还有几处单独改写"（`row['advice']`）→ 一句话 + tooltip。

        三种情况，**各有各的样子**（不合并成一种"没有说话"）：

          · `advice is None` —— 调用方没给拍表（单测/探针）：整行隐藏；
          · `unknown` —— 算不出来：说"统计不出"，**不冒充 0 处**；
          · 0 处 —— 隐藏（文档 §5.5：`none` 什么都不显示）。
        """
        label = slot['advice']
        advice = row.get('advice')
        text = ''
        full = ''
        level = 'none'
        if advice is not None:
            try:
                label_text = str(row.get('label') or '')
                text = str(advice.short(label=label_text) or '').strip()
                full = str(advice.text(label=label_text) or '').strip()
                level = str(getattr(advice, 'level', 'none') or 'none')
            except Exception as e:             # noqa: BLE001 - 提示坏了不拦面板
                swallow('ui_qt.size_panel.advice', e)
                text, full, level = '', '', 'none'
        label.setText(text)
        label.setToolTip(full or text)
        label.setProperty('role', 'warn' if level == 'notice' else 'note')
        label.setVisible(bool(text))
        # 换了 role 要重新算样式（Qt 不会自动为动态属性重刷）
        style = label.style()
        style.unpolish(label)
        style.polish(label)

    def _fill_combo(self, slot: dict, row: dict) -> None:
        combo: QComboBox = slot['combo']
        combo.blockSignals(True)
        try:
            combo.clear()
            current = -1
            for item in (row.get('items') or ()):
                combo.addItem(str(item.get('text') or ''))
                n = combo.count() - 1
                combo.setItemData(n, str(item.get('at') or ''),
                                  int(Qt.ItemDataRole.UserRole) + 1)
                combo.setItemData(n, str(item.get('key') or ''),
                                  int(Qt.ItemDataRole.UserRole) + 2)
                if item.get('why'):
                    # 为什么不能用 → 放在条目自己的 tooltip 上（点不了也说清原因）
                    model = combo.model()
                    try:
                        model.item(n).setToolTip(str(item['why']))
                        model.item(n).setEnabled(bool(item.get('ok')))
                    except Exception as e:    # noqa: BLE001 - 非标准模型就算了
                        swallow('ui_qt.size_panel.item_flags', e)
                if item.get('current') and current < 0:
                    current = n
            if current >= 0:
                combo.setCurrentIndex(current)
            elif combo.count():
                combo.setCurrentIndex(0)
            combo.setToolTip(self._combo_tip(row))
        finally:
            combo.blockSignals(False)
        self._sync_apply_btn(slot['index'])

    def _combo_tip(self, row: dict) -> str:
        note = str(row.get('why') or '').strip()
        base = (f'{row.get("label") or "图层"} {row.get("spec") or ""}\n'
                '「铺满 / 适应 / 50%」按几何匹配项目里已有的 transform；'
                '匹配不到会列「生成 sg_*」（确认后才写 game/sg_transforms.rpy）；'
                '「原样」去掉 at；「自定义」直接挑一个。')
        return (base + ('\n⚠ ' + note if note else ''))

    def _sync_apply_btn(self, slot_index: int) -> None:
        if not 0 <= int(slot_index) < len(self._slots):
            return
        slot = self._slots[int(slot_index)]
        row = slot['row']
        combo: QComboBox = slot['combo']
        item = None
        items = list((row or {}).get('items') or ())
        if 0 <= combo.currentIndex() < len(items):
            item = items[combo.currentIndex()]
        ok = bool(row and item and item.get('ok'))
        slot['button'].setEnabled(ok)
        if not ok and item is not None:
            slot['button'].setToolTip(str(item.get('why')
                                          or '这一项现在不能写。'))
        else:
            slot['button'].setToolTip(
                '改这一行的尺寸（先看 diff 再确认）。\n'
                '尺寸是持续状态：改的是**最初写下这一行的那一拍**'
                '（说明里有拍号），影响从那一拍起、直到下一次被覆盖。')

    # ---------- 出口 ----------

    def request(self, slot_index: int):
        """槽位当前选择 → 请求 dict；没选好 / 不可写 → None。"""
        if not 0 <= int(slot_index) < len(self._slots):
            return None
        slot = self._slots[int(slot_index)]
        row = slot['row']
        items = list((row or {}).get('items') or ())
        combo: QComboBox = slot['combo']
        if not row or not (0 <= combo.currentIndex() < len(items)):
            return None
        item = items[combo.currentIndex()]
        if not item.get('ok'):
            return None
        return {'row': int(row.get('index', slot_index)),
                'key': str(item.get('key') or ''),
                'at': str(item.get('at') or ''),
                'kind': str(row.get('kind') or 'scene'),
                'tag': str(row.get('tag') or ''),
                'spec': str(row.get('spec') or ''),
                'source': row.get('source', -1),
                # `generate=True` = 这个 transform 项目里**还没有**，确认后要写
                # `game/sg_transforms.rpy`；`natural` 是这张图的引擎显示尺寸，
                # 生成前要拿它再查一遍重（护栏 ②）。
                'generate': bool(item.get('generate')),
                'natural': row.get('natural'),
                'text': str(item.get('text') or '')}

    def _emit(self, slot_index: int) -> None:
        req = self.request(slot_index)
        if req is not None:
            self.size_requested.emit(req)

    def scene_request(self, slot_index: int):
        """
        背景行 → 「换景」请求；不是背景行 → None。

        与 `request()` 的差别：**不看这一项"能不能写"**。那些 `ok=False`
        是"对**现在这张图**不能写"（例如「原样（已经是原样）」）——
        换成**另一张图**之后这条限制根本不存在。选中的预设只当作
        **新背景**要套的 `at`（`generate=True` 时窗口层先去生成/查重）。
        """
        if not 0 <= int(slot_index) < len(self._slots):
            return None
        slot = self._slots[int(slot_index)]
        row = slot['row']
        if not row or str(row.get('kind') or 'scene') != 'scene':
            return None
        items = list(row.get('items') or ())
        combo: QComboBox = slot['combo']
        item = (items[combo.currentIndex()]
                if 0 <= combo.currentIndex() < len(items) else {})
        return {'row': int(row.get('index', slot_index)),
                'kind': 'scene', 'tag': '',
                'spec': str(row.get('spec') or ''),
                'source': row.get('source', -1),
                'key': str(item.get('key') or ''),
                'at': str(item.get('at') or ''),
                'generate': bool(item.get('generate')),
                'natural': row.get('natural'),
                'text': str(item.get('text') or '')}

    def _emit_scene(self, slot_index: int) -> None:
        req = self.scene_request(slot_index)
        if req is not None:
            self.scene_requested.emit(req)

    # ---------- 诊断/测试 ----------

    def rows(self) -> list:
        """当前显示的行（只读副本，测试与诊断用）。"""
        return list(self._rows)

    def slots(self) -> list:
        return list(self._slots)


__all__ = ['SIZE_SLOTS', 'SizePanel']
