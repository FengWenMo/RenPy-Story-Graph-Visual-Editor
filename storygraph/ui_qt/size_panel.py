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
 'spec': 'bg bg7_0', 'source': 0, 'generate': False, 'natural': (2304, 1296),
 'zoom': None}
```

`generate=True` 的条目（`at` 是个**还不存在**的 `sg_*` 名字）表示：确认之后
要先往 `game/sg_transforms.rpy` 里写一条定义，再改这一行。写盘与护栏都在
`story.sg_transforms`，面板只负责如实转达那条请求。

## 「自定义倍数」那一行（2026-10-31）

用户原话要的是「自定义 0.75×」：`50%` 只是 `0.5` 的特例，想填 0.75 / 1.25
就得给个输入框。它是下拉里**固定的一条**（`key='custom'`，排在四条预设后面、
「自定义：xxx」那一串前面），选中时才露出下面那一行输入框：

    倍数 [0.75]  会写 zoom 0.75；没有现成的就生成 sg_zoom_075（重名会换名）

- 认写法与说范围全在 `story.size_presets.parse_zoom` / `zoom_error`
  （**唯一判据**，与写盘前 `sg_transforms.plan_define` 用的是同一个）；
- 填错了条目**仍然可选**（不然人连自己错在哪儿都看不到），灰的是「改」按钮，
  原因同时在输入框右边、按钮 tooltip 与条目 tooltip 里；
- 请求里多一个 `'zoom'`（别的条目是 `None`），`at` 仍是 `''` ——
  名字（`sg_zoom_075`）由窗口层在确认那一步定（要读项目 transform 表），
  面板**不猜**；
- 填过的数按 `kind+tag` 记着，翻拍回来还是它（存**原样的字**，填错了也留着）。

## 「换景」是另一个按钮（2026-10-31）

背景行还多一个「换景」——它发 `scene_requested`，形状同上，但 `kind`
恒为 `'scene'`、`spec` 是**现在这张**背景，`at` 是下拉里选中的预设（当作
**新背景**要套的变换）。为什么单独一个按钮：Ren'Py 的 `scene` 会清掉
master 层上**所有** displayable（立绘全没），所以它必须是**人明确按下**的
动作，确认框里还要写清"会清掉台上 N 个立绘"（`app.stage.SceneEdit`）。
面板自己不弹框、不数人 —— 它只把请求递出去。

真正的计划 / diff 确认 / 写回在窗口层（`ui_qt.card_actions.apply_size_from_card`
与 `apply_scene_from_card`）：面板不 import 会话、不碰 `.rpy`。

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
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from storygraph.core.safeguard import swallow
from storygraph.story import sg_transforms as SG
from storygraph.story import size_presets as SP
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.wheel_guard import WheelSafeComboBox
from storygraph.ui_qt.wrap_label import NarrowWrapLabel

#: 与 `app.stage_render.SIZE_ROW_LAYERS` 对齐：1 个背景 + 3 个图层。
SIZE_SLOTS = 4

#: 「自定义倍数」输入框的初值（2026-10-31）。0.75 是用户自己举的那个例子；
#: 之后每一行记着自己上一次填的数（`_zoom_memory`），翻拍不重置。
CUSTOM_ZOOM = 0.75


class SizePanel(QWidget):
    """
    「尺寸」一格：每行 = 一行尺寸说明 + 预设下拉 + 「改」（背景行多一个「换景」），
    选中「自定义倍数」那一项时多一行「倍数 [输入框]」。
    """

    #: 用户按了某一行的「改」：参数是请求 dict（见模块 docstring）
    size_requested = Signal(object)
    #: 用户按了**背景行**的「换景」：参数是请求 dict（多了 `kind='scene'`）
    scene_requested = Signal(object)
    #: 用户按了某一行的「一并改成一样」：参数是请求 dict（`kind/tag/source`）
    merge_requested = Signal(object)

    def __init__(self, parent=None, slots: int = SIZE_SLOTS):
        super().__init__(parent)
        self._rows: list = []
        self._slots: list = []
        #: 每一行「自定义倍数」上一次填的字（键 = `_row_key(row)`）。
        #: 存**原样的字**不存 parse 后的数：填错了那串也得留着，人才看得见自己
        #: 写了什么（翻拍回来被悄悄换成 0.75 是最糟的"我没写这个"）。
        self._zoom_memory: dict = {}
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
        # —— 「自定义倍数」那一行（2026-10-31，只在选中它时出现）——
        #
        # 单独一行，不塞进上面那排：右栏 345px，combo 本来就靠拉伸吃饭，
        # 再挤一个输入框，所有条目文本都成省略号（10-30 那条"卡片被顶到
        # 1077px"的老账就是从这类并排开始的）。
        #
        # 用 `QLineEdit` 而不是 `QDoubleSpinBox`：数值框**吃滚轮**（改了值还
        # 把页面滚给吃掉），全靠 `wheel_guard` 那个全局过滤器兜；输入框根本不
        # 响应滚轮，单测/探针里不装过滤器也不会误改。认写法与范围都在
        # `story.size_presets.parse_zoom`（唯一判据，与写盘前同一个）。
        zbox = QWidget()
        zrow = QHBoxLayout(zbox)
        zrow.setContentsMargins(0, 0, 0, 0)
        zrow.setSpacing(T.SPACE['xs'])
        zlabel = QLabel('倍数')
        zlabel.setProperty('role', 'caption')
        zedit = QLineEdit()
        zedit.setPlaceholderText('0.75')
        zedit.setFixedWidth(58)
        zedit.setToolTip(
            '填一个倍数（0.05–5）。认这几种写法：0.75 / 0.75× / 75%。\n'
            '别写 75 —— 那是 75 倍；想写 75% 请写 0.75 或 75%。\n'
            '确认之后：项目里已经有一样效果的就直接用它，没有才在 '
            'game/sg_transforms.rpy 里生成一条（工具生成、带指纹；'
            '手改过就拒绝覆盖）。')
        zhint = NarrowWrapLabel('')
        zhint.setProperty('role', 'note')
        zhint.setWordWrap(True)
        zhint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        zrow.addWidget(zlabel, 0)
        zrow.addWidget(zedit, 0)
        zrow.addWidget(zhint, 1)
        zbox.setVisible(False)
        col.addWidget(zbox)
        zedit.textChanged.connect(
            lambda _t='', idx=index: self._on_zoom_edited(idx))
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
        # —— 「一并改成一样」（2026-10-31）——
        #
        # 只在"本景还有 N 处单独改写"时出现：把那几处的 at 改成和源一样。
        # 为什么是**人按按钮**而不是自动做：动的是用户的 `.rpy` 行
        # （`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5 的 L4），
        # 必须逐处确认、可撤销。按钮文字里带处数 —— 点之前就知道要改几行。
        merge = QPushButton('一并改成一样')
        merge.setVisible(False)
        merge.clicked.connect(lambda _c=False, idx=index: self._emit_merge(idx))
        col.addWidget(merge)
        box.setVisible(False)
        slot.update({'widget': box, 'note': note, 'combo': combo, 'button': btn,
                     'swap': swap, 'advice': advice, 'merge': merge,
                     'zoom_box': zbox, 'zoom_edit': zedit, 'zoom_hint': zhint,
                     'zoom_key': None, 'zoom_text': '', 'zoom': None,
                     'zoom_err': '', 'items': [], 'custom_index': -1})
        return slot

    # ---------- 数据 ----------

    def set_rows(self, rows) -> None:
        """
        按 `size_rows(...)` 的结果刷新（`[]` = 这一拍没有背景/图层）。

        「自定义倍数」那一条在这里被换成**算好的副本**（`_effective_items`）：
        面板记着每一行上一次填的数（`_zoom_memory`，按 `kind+tag` 认行），
        翻拍回来还是那个数 —— 不然每翻一页都得重填。
        """
        self._rows = [r for r in (rows or ()) if isinstance(r, dict)]
        for i, slot in enumerate(self._slots):
            row = self._rows[i] if i < len(self._rows) else None
            if row is None:
                slot['row'] = None
                slot['items'] = []
                slot['custom_index'] = -1
                slot['zoom_box'].setVisible(False)
                slot['widget'].setVisible(False)
                continue
            slot['row'] = row
            slot['zoom_key'] = self._row_key(row)
            slot['zoom_text'] = self._zoom_memory.get(
                slot['zoom_key'], f'{CUSTOM_ZOOM:g}')
            slot['zoom'], slot['zoom_err'] = SP.parse_zoom(slot['zoom_text'])
            slot['items'] = self._effective_items(row, slot['zoom_text'])
            slot['widget'].setVisible(True)
            slot['note'].setText(str(row.get('note') or '').strip())
            # 「换景」只属于背景行：CG/UI 图层换图不是换景（那只写 show）。
            slot['swap'].setVisible(str(row.get('kind') or 'scene') == 'scene')
            self._fill_advice(slot, row)
            self._fill_merge(slot, row)
            self._fill_zoom(slot)
            self._fill_combo(slot)
        if not self._rows:
            self._empty.setText('这一拍没有背景 / CG / UI 图层，没有尺寸可调。')
        self._empty.setVisible(not self._rows)

    # ---------- 「自定义倍数」（2026-10-31） ----------

    @staticmethod
    def _row_key(row: dict) -> tuple:
        """认这一行的键（背景 / 某个 tag 的图层）—— 记填过的倍数用。"""
        return (str((row or {}).get('kind') or ''), str((row or {}).get('tag') or ''))

    @staticmethod
    def _custom_item(base: dict, text) -> dict:
        """
        `size_rows` 给的「自定义倍数」占位条目 + 输入框里的字 → **算好的副本**。

        算不出来（没填 / 写法认不出 / 越界）时 `ok=False` + `why` 说原因，
        条目本身**仍然可选**（不然人连自己填错在哪儿都看不到）。
        """
        item = dict(base or {})
        value, err = SP.parse_zoom(text)
        if value is None:
            item.update({'zoom': None, 'at': '', 'ok': False, 'generate': True,
                         'why': err, 'text': '自定义倍数…（先填一个数）'})
            return item
        item.update({'zoom': value, 'at': '', 'ok': True, 'generate': True,
                     'why': '',
                     'text': f'自定义倍数 {SP.zoom_label(value)}'
                             f'（{SP.atl_body("custom", value)}）'})
        return item

    def _effective_items(self, row: dict, text) -> list:
        """`row['items']` 的副本：只把「自定义倍数」那一条换掉，别的原样。"""
        out = []
        for item in (row.get('items') or ()):
            if isinstance(item, dict) and item.get('key') == 'custom':
                out.append(self._custom_item(item, text))
            else:
                out.append(item)
        return out

    def _fill_zoom(self, slot: dict) -> None:
        """输入框文字按记着的值摆好（**挡信号**：这不是用户在打字）。"""
        edit: QLineEdit = slot['zoom_edit']
        text = str(slot.get('zoom_text') or '')
        edit.blockSignals(True)
        try:
            if edit.text() != text:
                edit.setText(text)
        finally:
            edit.blockSignals(False)
        self._fill_zoom_hint(slot)

    def _fill_zoom_hint(self, slot: dict) -> None:
        """输入框右边那句：填对了说"会写什么"，填错了说**错在哪**。"""
        hint = slot['zoom_hint']
        zoom = slot.get('zoom')
        err = str(slot.get('zoom_err') or '')
        if zoom is None:
            text = err
            role = 'warn'
            tip = err
        else:
            text = SG.custom_hint(zoom)
            role = 'note'
            tip = (f'{text}\n'
                   '• 项目里已有一样效果的 transform → 复用它，不写文件；\n'
                   '• 没有 → 生成一条 sg_zoom_0xx（工具生成、带指纹；'
                   '手改过就拒绝覆盖）；\n'
                   '• 名字被别的内容占了会退到 _2（不改别人的定义）。\n'
                   '按下「改」之后先看 diff 再写，只改内存里的拍。')
        hint.setText(text)
        hint.setToolTip(tip)
        hint.setProperty('role', role)
        style = hint.style()
        style.unpolish(hint)
        style.polish(hint)

    def _on_zoom_edited(self, slot_index: int) -> None:
        """用户在输入框里打字：记住原样文字 → 重算条目 → 同步下拉与按钮。"""
        if not 0 <= int(slot_index) < len(self._slots):
            return
        slot = self._slots[int(slot_index)]
        row = slot.get('row')
        if not row:
            return
        text = str(slot['zoom_edit'].text() or '')
        slot['zoom_text'] = text
        if slot.get('zoom_key') is not None:
            self._zoom_memory[slot['zoom_key']] = text
        slot['zoom'], slot['zoom_err'] = SP.parse_zoom(text)
        slot['items'] = self._effective_items(row, text)
        self._refresh_custom_item(slot)
        self._sync_apply_btn(int(slot_index))

    def _refresh_custom_item(self, slot: dict) -> None:
        """
        就地改下拉里那一条的文字/提示（不重建整个下拉：那会把弹开态与滚动位
        一起丢掉，真机上表现为"打字时下拉乱跳"）。
        """
        idx = int(slot.get('custom_index', -1))
        items = slot.get('items') or []
        if not (0 <= idx < len(items)):
            return
        item = items[idx]
        combo: QComboBox = slot['combo']
        combo.blockSignals(True)
        try:
            combo.setItemText(idx, str(item.get('text') or ''))
        finally:
            combo.blockSignals(False)
        model = combo.model()
        try:
            # 条目**永远可选**（填错了也要能看见错在哪）；能不能写由「改」
            # 按钮的灰/亮与 tooltip 说 —— `_sync_apply_btn` 干这件事。
            model.item(idx).setToolTip(str(item.get('why') or
                                           item.get('note') or ''))
        except Exception as e:              # noqa: BLE001 - 非标准模型就算了
            swallow('ui_qt.size_panel.custom_item', e)
        self._fill_zoom_hint(slot)

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

    def _fill_merge(self, slot: dict, row: dict) -> None:
        """
        「一并改成一样」按钮的显隐与文字（2026-10-31）。

        只在本景**真有**单独改写、而且**找得到源那一拍**时出现：

          · `advice is None` / 0 处 → 藏起来（没有可合并的）；
          · `unknown`（算不出来）→ 藏起来：连有几处都不知道，就别给一个
            要改不知道几行的按钮（提示行仍会说"统计不出"）；
          · `source < 0`（跨章继承）→ 藏起来：源不在这本剧本里，没有"回"；
          · **那几处已经写着源那个值**（`advice.agreed`）→ 也藏起来：点了
            也是"没有要改的"。这时提示行会改口气（"写着同一个 at"），
            按钮不出现 —— 按下去什么都不会发生的按钮比没有按钮更糟。

        文字里带**处数**：按下去之前就知道要改几行。
        """
        btn = slot['merge']
        advice = row.get('advice')
        count = 0
        if advice is not None and not getattr(advice, 'unknown', False):
            try:
                count = int(getattr(advice, 'count', 0) or 0)
            except (TypeError, ValueError):
                count = 0
        try:
            source = int(row.get('source', -1))
        except (TypeError, ValueError):
            source = -1
        agreed = bool(getattr(advice, 'agreed', False))
        if count <= 0 or source < 0 or agreed:
            btn.setVisible(False)
            btn.setText('一并改成一样')
            btn.setToolTip('')
            return
        src_at = row.get('source_at')
        value = (f'`{src_at}`' if str(src_at or '') else '原样（不带 at）') \
            if src_at is not None else '源那一行写的值'
        btn.setText(f'一并改成一样（{count} 处）')
        btn.setToolTip(
            f'把那 {count} 处的 at 都改成「源那一行」的值'
            f'（源：第 {source + 1} 拍 · {value}）。\n'
            f'⚠ 会改 {count} 行 .rpy：只动那几处 show 的 @at —— 不删行、'
            '不加行、图名与 behind/with/as 一个字都不动。\n'
            '逐处确认（确认框里一处一个勾）；现在只改内存，导出时还有一次 '
            'diff 确认。\n'
            '改不了的（复杂子句 / 拍级 at）会在确认框里单独列出来 —— '
            '不会悄悄跳过。')
        btn.setVisible(True)

    def _fill_combo(self, slot: dict) -> None:
        combo: QComboBox = slot['combo']
        row = slot.get('row') or {}
        items = list(slot.get('items') or ())
        slot['custom_index'] = -1
        combo.blockSignals(True)
        try:
            combo.clear()
            current = -1
            for n, item in enumerate(items):
                combo.addItem(str(item.get('text') or ''))
                combo.setItemData(n, str(item.get('at') or ''),
                                  int(Qt.ItemDataRole.UserRole) + 1)
                combo.setItemData(n, str(item.get('key') or ''),
                                  int(Qt.ItemDataRole.UserRole) + 2)
                if item.get('key') == 'custom':
                    # 「自定义倍数」那一条**永远可选**：填错了也要能停在它上面
                    # 看输入框里的错在哪（能不能写由「改」按钮说）。
                    slot['custom_index'] = n
                    model = combo.model()
                    try:
                        model.item(n).setToolTip(str(item.get('why')
                                                    or item.get('note') or ''))
                    except Exception as e:    # noqa: BLE001 - 非标准模型就算了
                        swallow('ui_qt.size_panel.custom_item', e)
                elif item.get('why'):
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
                '「自定义倍数」填一个数（0.75 / 1.25 …），「自定义」直接挑一个；'
                '「原样」去掉 at。')
        return (base + ('\n⚠ ' + note if note else ''))

    def _sync_apply_btn(self, slot_index: int) -> None:
        if not 0 <= int(slot_index) < len(self._slots):
            return
        slot = self._slots[int(slot_index)]
        row = slot['row']
        combo: QComboBox = slot['combo']
        item = None
        items = list(slot.get('items') or ())
        if 0 <= combo.currentIndex() < len(items):
            item = items[combo.currentIndex()]
        # 输入框只在选中「自定义倍数」时露出来 —— 别的条目用不着它。
        slot['zoom_box'].setVisible(bool(item and item.get('key') == 'custom'))
        ok = bool(row and item and item.get('ok'))
        slot['button'].setEnabled(ok)
        if item is not None and item.get('key') == 'custom':
            if not ok:
                slot['button'].setToolTip(str(item.get('why')
                                              or '先在下面填一个倍数。'))
            else:
                cross = self._cross_chapter(row)
                slot['button'].setToolTip(
                    f'把这一行改成 {SP.atl_body("custom", item.get("zoom"))}'
                    f'（{SP.zoom_label(item.get("zoom"))}）：'
                    '项目里已有一样效果的就直接用它，没有才生成一条\n'
                    '（确认框里会写清写哪个文件、叫什么名字；只改内存里的拍，'
                    '导出时还有一次 diff）。\n'
                    + ('⚠ 这一行是上一章留下的（出处不在这本剧本里）：会改在'
                       '**本章第一拍**补一条 show，不改上一章、不清立绘。'
                       if cross else
                       '尺寸是持续状态：改的是**最初写下这一行的那一拍**'
                       '（说明里有拍号），影响从那一拍起、直到下一次被覆盖。'))
        elif not ok and item is not None:
            slot['button'].setToolTip(str(item.get('why')
                                          or '这一项现在不能写。'))
        elif self._cross_chapter(row):
            # 出处不在这本剧本里（上一章留下的）：按「改」**补一条**，不是改
            # 那一行 —— 这一点必须在按下去之前就说清（2026-10-31）。
            slot['button'].setToolTip(
                '这一行是上一章留下的（出处不在这本剧本里）：按「改」会在'
                '**本章第一拍**补一条 show，把尺寸定下来。\n'
                '不改上一章、不清立绘（写 show 不写 scene）；确认框里先看 '
                'diff 再写，只动内存里的拍。「原样」补不了。')
        else:
            slot['button'].setToolTip(
                '改这一行的尺寸（先看 diff 再确认）。\n'
                '尺寸是持续状态：改的是**最初写下这一行的那一拍**'
                '（说明里有拍号），影响从那一拍起、直到下一次被覆盖。')

    @staticmethod
    def _cross_chapter(row) -> bool:
        """这一行的出处是不是"不在这本剧本里"（`source < 0`，跨章继承）。"""
        try:
            return int((row or {}).get('source', -1)) < 0
        except (TypeError, ValueError):
            return True

    # ---------- 出口 ----------

    def request(self, slot_index: int):
        """槽位当前选择 → 请求 dict；没选好 / 不可写 → None。"""
        if not 0 <= int(slot_index) < len(self._slots):
            return None
        slot = self._slots[int(slot_index)]
        row = slot['row']
        items = list(slot.get('items') or ())
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
                # `zoom` 只有「自定义倍数」才有（`key='custom'`）：窗口层拿它
                # 去算名字与体（`sg_zoom_075` / `zoom 0.75`），面板不认识表。
                'zoom': item.get('zoom'),
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
        items = list(slot.get('items') or ())
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
                'zoom': item.get('zoom'),
                'text': str(item.get('text') or '')}

    def _emit_scene(self, slot_index: int) -> None:
        req = self.scene_request(slot_index)
        if req is not None:
            self.scene_requested.emit(req)

    def merge_request(self, slot_index: int):
        """
        「一并改成一样」→ 请求 dict；没有可合并的（或源不在本剧本里）→ None。

        带 `source_at`（源那一行**自己**写着的 at）只是给界面显示用的；
        计划那边（`story.at_merge.plan_at_merge`）会自己从拍表里读同一个值 ——
        界面传什么都不会改写那个数字。
        """
        if not 0 <= int(slot_index) < len(self._slots):
            return None
        slot = self._slots[int(slot_index)]
        row = slot['row']
        if not row:
            return None
        advice = row.get('advice')
        try:
            count = int(getattr(advice, 'count', 0) or 0) \
                if advice is not None else 0
            source = int(row.get('source', -1))
        except (TypeError, ValueError):
            return None
        if count <= 0 or source < 0 or getattr(advice, 'unknown', False) \
                or getattr(advice, 'agreed', False):
            return None
        return {'row': int(row.get('index', slot_index)),
                'kind': str(row.get('kind') or 'scene'),
                'tag': str(row.get('tag') or ''),
                'spec': str(row.get('spec') or ''),
                'label': str(row.get('label') or ''),
                'source': source,
                'source_at': row.get('source_at'),
                'count': count}

    def _emit_merge(self, slot_index: int) -> None:
        req = self.merge_request(slot_index)
        if req is not None:
            self.merge_requested.emit(req)

    # ---------- 诊断/测试 ----------

    def rows(self) -> list:
        """当前显示的行（只读副本，测试与诊断用）。"""
        return list(self._rows)

    def slots(self) -> list:
        return list(self._slots)


__all__ = ['SIZE_SLOTS', 'SizePanel']
