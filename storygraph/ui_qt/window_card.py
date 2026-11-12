"""
storygraph.ui_qt.window_card.py
===============================
`MainWindowQt` 的**卡片页那一半：载入单章 / 拍列表 / 导航 / 增删卡片 / 卡片级撤销。**

**为什么是 mixin、当初怎么拆的**：`docs/代码地图.md` §三·五 讲一次，
不再在 6 个 `window_*.py` 里各抄一遍（从前这 22 行在 6 个文件里逐字重复）。

与主类的隐式依赖
----------------
见每个类的 `REQUIRES`：那是**实测**（静态扫 `self.*`）出来的，
由 `tests/test_main_window_mixins.py` 守着。
"""
from __future__ import annotations

import os
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSplitter, QStackedWidget, QToolBar,
                               QVBoxLayout, QWidget)

from storygraph.ui_qt import prefs
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.flow_notes import edge_rebuild_note
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.labeled_choices import labeled, pairs, session_vocabulary
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.status import Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader


__all__ = ['CardPageMixin']

#: 列表行的 `Qt.UserRole` 存**拍号**（`-1` = 这一行是 label 分组标题，不是拍）。
#: 从前"行号 == 拍号"是硬约定；有了分组标题行之后，映射必须走 data ——
#: 否则点分组标题就会 `_show_index(-1)`，把"当前拍"弄丢。
_GROUP_LABEL_ROLE = Qt.UserRole + 1

#: 左栏折叠状态**不进**布局 JSON（那是"这张图长什么样"），但它 2026-10-22 起
#: **跟项目走**：写项目根的 `storygraph.session.json`（`cards.folded`，键是
#: 项目内相对路径）。这一条偏好键只是**兜底**：没打开项目 / 文件在项目外 /
#: 项目文件写不进去时用它（键是绝对路径，见 `_fold_store_key`）。
_FOLD_PREF_KEY = 'cards/folded_labels'
#: 偏好里最多记住多少个文件（偏好不是日志：每换一个项目就多一条会永不清理）。
_FOLD_PREF_LIMIT = 24


def _scene_cell_image(cell) -> str:
    """
    `Scene` 那一格 → 图像名（`bg big@bgfit` 的**图像部分**是 `bg big`）。

    卡片上「背景」一格存的是紧凑写法 `图@变换`（与 `compose_scene_cell` 同一
    形状），判"这一格本来是不是空的"必须只看图像那半边 —— 只看整串的话，
    `@bgfit` 也会被当成"有背景"。
    """
    return str(cell or '').split('@')[0].strip()


def note_scene_added(card, idx: int, image: str) -> None:
    """
    卡片「背景」那一格**新写出一条 `scene`** 之后，把"带走了谁"说出来。

    为什么是"事后点名"而不是弹框（2026-10-31）：

      · 填背景是这个工具**最常做**的编辑之一；每保存一次弹一个确认框，
        人就会条件反射点"是" —— 那比不弹更糟（本项目已经用这个理由把
        "一次点击弹两次确认"毙掉过）；
      · 但**静默**更糟：从"没有背景"变成"有背景"= 一条 `scene`，
        Ren'Py 会清掉该层**所有** displayable。卡片上看不见那些立绘
        （它们是上一拍 show 的），用户只会看到"人突然没了"。

    所以：数据照常保存（可撤销），控制台/状态栏**立刻**点名被带走的立绘与
    图层。要"事前确认"的那条路是尺寸面板背景行的「换景」
    （`app.stage.SceneEdit` / `ui_qt.card_actions.apply_scene_from_card`）——
    它会在写之前把 N 个立绘写进确认框。

    台上没人 → 一个字都不说（新建一景、空台换景是最常见的情形）。

    返回**一句短警告**（给调用方拼进"已保存"那条状态栏消息）；没话说返回空串。
    控制台那条详细版由这里自己写 —— 两处说的是同一件事。
    """
    try:
        from storygraph.app.stage import stage_before
        info = stage_before(list(getattr(card._session, 'beats', None) or ()),
                            idx)
    except Exception as e:                        # noqa: BLE001 - 提示失败不拦保存
        swallow('ui_qt.window_card.note_scene_added', e)
        return ''
    if not info:
        return ''
    sprites = list(info.get('sprites') or ())
    layers = list(info.get('layers') or ())
    if not sprites and not layers:
        return ''
    bits = []
    if sprites:
        bits.append(f'{len(sprites)} 个立绘（{"、".join(sprites[:4])}'
                    + ('…' if len(sprites) > 4 else '') + '）')
    if layers:
        bits.append(f'{len(layers)} 个图层（{"、".join(layers[:3])}'
                    + ('…' if len(layers) > 3 else '') + '）')
    what = '、'.join(bits)
    try:
        card._log(f'[换景] 这一拍新写了一条 `scene`（背景：{image}）—— '
                  f'Ren\'Py 的 scene 会清掉该层所有 displayable：{what}。'
                  '数据已经改了（可撤销）；要**事前**确认请用「尺寸」里'
                  '背景行的「换景」。')
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.window_card.note_scene_added.log', e)
    return f'⚠ 这一拍新写了一条 scene：{what} 被清掉（可撤销）。'


def arg_kind_of(col) -> tuple:
    """
    参数列 → 该去词汇表**哪个种类**里找它的候选名（空 = 不翻）。

    约定只有一条：**`choices_from` 就是词汇表的 `kind`**（`transform` / `music`
    / `bg` / `costume` …）—— 项目给列声明"候选从哪来"，与词汇表"这东西叫什么"
    是同一个词，不需要第二张对照表。

    唯一的例外是转场那一格：`with` 的候选既可能是资源表里的变换（`dissolve`），
    也可能是词汇表登记的**过场调用**（`Fade(0.2, 0, 0.2)` / `my_wipe`）——
    用户第 5 条要的就是后者在卡片上能按中文挑，所以两个种类轮流问。
    """
    source = str((col or {}).get('choices_from') or '').strip()
    if source == 'transform':
        return ('transform', 'cue')
    return (source,) if source else ()


def _statement_of(beat, statements) -> dict:
    """
    「这一拍是哪条语句」→ `{'name','label','head'}`（台词拍 → `{}`）。

    三种来源，**顺序即优先级**：

      · `spec_name` —— 项目声明过的自定义语句（T1，卡片能改参数的就是它）；
      · `camera_header` —— 3D 舞台的 `camera …:`（头行可编辑）；
      · `loop_header` —— `while` / `for`（块头行可编辑）。

    为什么卡片需要它：这几种拍**没有台词**，从前在卡片上就是一张空白的
    对话卡（说话人 + 空台词框 + 空立绘网格），人看不出这一拍到底在干什么。
    """
    name = str(getattr(beat, 'spec_name', '') or '').strip()
    head = str(getattr(beat, 'spec_head', '') or '').strip()
    if not name:
        head = str(getattr(beat, 'camera_header', '') or '').strip()
        name = 'camera' if head else ''
    if not name:
        head = str(getattr(beat, 'loop_header', '') or '').strip()
        name = (head.split()[0] if head.split() else '')
    if not name:
        return {}
    label = ''
    try:
        spec = statements.match(name) if statements is not None else None
        label = str(getattr(spec, 'label', '') or '')
    except Exception as e:                      # noqa: BLE001 - 没有标签也能显示
        swallow('ui_qt.window_card._statement_of.label', e)
    return {'name': name, 'label': label, 'head': head}


def _loose_lines(beat) -> list:
    """
    这一拍里**工具没建模、但会原样执行**的行（卡片顶部要说得出口）。

    为什么需要（2026-10-18）：一条 `play ambience "amb/wind.ogg"`（项目自定义
    声道）或 `show screen map` 既不是台词、也不进任何结构化列 —— 它躺在
    「代码」格里，而那一格在**折叠着的高级区**里。于是卡片上看起来
    "这一拍什么都没有"，人根本不知道该去哪里改。

    取的是 `raw`（代码格里原生的那些行）与 `opaque`（整块原样行），
    去掉已经由语句条显示的那条头行（否则同一行说两遍）。
    """
    head = str(getattr(beat, 'spec_head', '') or '').strip()
    # 控制流行已经由卡片顶部那条「调用 / 跳转」条显示，不要再在
    # "这一拍还会原样执行"里说一遍（2026-10-22，P0-4）。
    from storygraph.story.flow import parse_flow
    flow = parse_flow(beat)
    flow_line = str((flow or {}).get('line') or '')
    out: list = []
    for src in (getattr(beat, 'raw', ''), getattr(beat, 'opaque', '')):
        for line in str(src or '').splitlines():
            text = line.strip()
            if text and text != head and text != flow_line and text not in out:
                out.append(text)
    return out


class CardPageMixin:
    """
    卡片页那一半：载入单章 / 拍列表 / 导航 / 增删卡片 / 卡片级撤销。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('PAGE_CARD', 'PREFETCH_AHEAD', 'PREFETCH_BEHIND', '_base_title', '_canvas', '_file_path', '_graph_dirty', '_label_combo', '_list', '_loaded_idx', '_log', '_next_action', '_on_canvas_select', '_page', '_panel', '_pos', '_prev_action', '_project_root', '_refresh_asset_choices', '_report_swallowed', '_run_command', '_session', '_set_graph', '_set_page', '_status', '_status_bar', '_swallow_mark', '_sync_inspector', '_sync_nav_current', '_update_crumb', '_who', 'graph', 'maybe_offer_recovery')

    #: 被用户折起来的 label 名（跨 `_reload_list` 保留：翻页/插卡不该弹开）。
    _collapsed_labels: frozenset = frozenset()
    #: 拍号 → 列表行号；`_reload_list` 里新建（这两个只是"还没载入"时的空值）。
    _beat_rows: tuple = ()
    _group_items: dict = {}
    _group_counts: dict = {}

    def _on_load(self):
        start = str(Path(self._project_root or '.') / 'game')
        path, _ = QFileDialog.getOpenFileName(
            self, '直接编辑一章 .rpy（不进剧情图）', start,
            "Ren'Py 脚本 (*.rpy)")
        if path:
            self.load_script(path)
    def load_script(self, path=None, goto_card: bool = True, label=None):
        """
        载入一章（**语义全在 EditorSession.load**）。

        `goto_card`：载入是"要在卡片界面里改这一章"，所以默认**跳进卡片页**；
        构造窗口时的自动载入传 False —— 第一屏必须是剧情结构（用户明确要求）。

        `label`：同一个 `.rpy` 里有多个 label 时，落在**这个 label 的第一拍**，
        而不是文件第 1 拍。点图上的第二个 label 节点走的就是这条路。
        """
        target = path or self._session.script_path
        if not target:
            return
        mark = self._swallow_mark()
        try:
            # 只有真要定位 label 时才传第二个参数：老调用方 / 测试里的
            # `load(path)` 替身仍然能接住（少一个位置参数不破坏兼容）。
            n = (self._session.load(target, label=label) if label
                 else self._session.load(target))
            self._panel.set_symbols(self._session.symbols)
            self._sync_choice_groups()
            self._refresh_asset_choices()
            # **先读偏好、再建列表**：`_reload_list` 建完就按 `_collapsed_labels`
            # 藏行了，反了的话第一次打开会全展开、然后才被"恢复"成折叠。
            self._load_collapsed_pref()
            self._reload_list()
            idx = self._session.index_of_label(label) if label else 0
            if label and idx < 0:
                self._log(f'[载入] ⚠ {Path(target).name} 里没有 label '
                          f'「{label}」—— 已退到第 1 拍')
                self._status(f'{Path(target).name} 里找不到 label「{label}」，'
                             f'已从第 1 拍打开。')
                idx = 0
            self._show_index(idx)
            self._sync_label_combo()
            self._sync_graph_highlight()
            # 跨章继承（2026-10-31）：本章第一拍之前台上是什么。
            # 缓存命中就立刻生效；没命中就后台算一次（不阻塞这一章打开）。
            self.warm_stage_entry()
            if goto_card:
                self._set_page(self.PAGE_CARD)
            else:
                self._update_crumb()
            where = f' · {label}' if label else ''
            self._log(f'[载入] {Path(target).name}{where}：{n} 拍'
                      f'（{"进入卡片编辑器" if goto_card else "留在剧情结构页"}）')
            self._status(f'已载入 {Path(target).name}{where}：{n} 拍')
            # **自动保存的恢复问句挂在这里**（2026-10-23）：必须等"按磁盘
            # 载入过这一章"之后才问 —— 恢复是"在磁盘那一版之上换掉拍列表"，
            # 没有这一版就没有基线（见 `app/session_recover` 的说明）。
            # 放在 `finally` 之外：恢复失败的异常不该被载入失败的提示盖住。
            self.maybe_offer_recovery()
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '载入失败', str(e))
        finally:
            self._report_swallowed(mark)
    def adopt_loaded_session(self, label=None, index=None,
                             goto_card: bool = False):
        """
        会话里的拍**已经被别处改过**（变量工作台 / 变量对话框）→ 刷新卡片页。

        为什么不直接 `load_script()`：那会从磁盘重读，把刚写进拍的变量改动
        又盖回旧值。这里只重建列表 / 下拉 / 当前拍，数据仍然用会话里那份。
        """
        self._reload_list()
        self._sync_choice_groups()
        self._refresh_asset_choices()
        self._sync_label_combo()
        if index is None:
            index = self._session.index_of_label(label) if label else 0
        if index is None or index < 0:
            index = 0
        self._show_index(int(index))
        self._sync_graph_highlight()
        if goto_card:
            self._set_page(self.PAGE_CARD)
        else:
            self._update_crumb()

    def _sync_choice_groups(self):
        """
        把项目声明的**候选分栏**接成立绘网格的分组候选（分栏显示"服装 / 表情"）。

        用户 2026-10-10：

        > 实际上需要分组显示……因为我这个要求通用！所以一般人其实是没有服装这个
        > 选项的！所以事实上应当通过 B 这个自定义模块，做到把服装跟表情这种东西
        > 分开！

        所以：**分哪几栏由项目声明决定**（`rules.json` 的 `attr_groups`），
        工具不写死任何组名；没声明 → 推空 → 网格走原来的平铺候选（一字不变）。
        值是**界面显示值**（走 `expression_groups_for` 同一套翻译），
        与 chip 上看到的字面量一致。

        **E2（2026-10-10）：引擎事实优先。**
        采过 `engine_facts.json` 之后，分组与候选**都从引擎来**
        （`layeredimage` 的 group + 引擎枚举的属性），项目声明**降级为"改名"**
        （把 `outfit` 显示成「服装」）。真机实测：`lan` 的组本来就叫
        `outfit` / `expressions` —— 再让人声明一遍是重复劳动，还会两边打架。
        ⚠ 没采过引擎事实（`facts` 为空）时，**一维一格仍然要能工作** ——
        判据退回项目自己的 `layeredimage`（`app.beats.attr_slot_rows` 里有这一档，
        2026-10-16 实测报的"粘在一起了"就是因为从前这里直接 `return` 掉了，
        那一档永远走不到）。所以现在**总是**注入 `_for_tag`（可以是空的），
        让网格自己按"有没有引擎事实 / 有没有 layeredimage"去决定。
        """
        try:
            groups = self._session.declared_attr_groups()
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_card._sync_choice_groups.declare', e)
            groups = []
        try:
            facts = self._session.engine_facts()
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_card._sync_choice_groups.facts', e)
            facts = {}
        from storygraph.app.beats import expression_groups_for
        symbols = getattr(self._session, 'symbols', None)

        def _for_tag(tag):
            return expression_groups_for(str(tag or ''), symbols, groups, facts)

        # **把原料挂在函数上**（2026-10-12）：卡片要"一维一格"时得知道
        # 引擎的分组与候选（`app.beats.attr_slot_rows`），但网格那套注入通道
        # 只传得下一个可调用对象。挂属性 = **不新增一个"要记得转发"的方法** ——
        # 转发层漏一个方法的代价是模态弹窗（见 `main_window.set_choice_groups_provider`
        # 的注释，那个坑真踩过）。
        _for_tag.attr_groups = list(groups or ())
        _for_tag.engine_facts = facts if isinstance(facts, dict) else {}
        #: ⚠ 2026-10-16：把**项目根**也挂上 —— 没有引擎事实时，`attr_slot_rows`
        #:   要退回项目自己的 `layeredimage`（它自己按项目根建/读模型缓存）。
        _for_tag.project_root = getattr(self._session, 'project_root', None)
        #: 词汇表（2026-10-31）：引擎写法 → 人话。卡片上那些 chip 的显示名
        #: 全靠它；挂在这里的理由同上（不多开一条注入通道）。
        _for_tag.vocabulary = self._vocabulary()

        # 现取（不缓存）：同一个词在不同角色下的显示名可能不同
        self._panel.set_choice_groups_provider(_for_tag)
        self._warn_engine_facts_once()

    def _vocabulary(self):
        """
        项目那份映射里的**词汇表**（引擎写法 → 中文叫法），没有就空表。

        查不到的种类/写法一律原样显示（见 `assets/vocabulary.py` 规矩 2），
        所以这里返回空表是**安全**的：卡片与从前一模一样。

        真源在会话（`session.vocabulary()`：映射 + `rules.json` 的 calls 中文名）。
        """
        return session_vocabulary(self._session)

    def _warn_engine_facts_once(self):
        """
        引擎级告警**只在打开项目时说一次**（N2/N5：项目覆盖了内置语句）。

        为什么不塞进卡片那条 note：note 是"**这一拍**"的提示，跟着翻页刷新 ——
        把项目级的告警放进去，就会每翻一页重复喊一遍，人很快就不看了。
        """
        if getattr(self, '_facts_warned', False):
            return
        self._facts_warned = True
        try:
            from storygraph.app import notify
            for msg in self._session.engine_facts_warnings():
                notify.warn(msg)
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_card._warn_engine_facts_once', e)
    def _sync_graph_highlight(self):
        """卡片编辑器正开着的那一章 → 图上对应模块标成"当前"并居中。"""
        if self.graph is None or not self._session.script_path:
            return
        target = Path(self._session.script_path).resolve()
        focus = str(getattr(self._session, 'focus_label', '') or '')
        candidates = []
        for node in (self.graph.nodes or {}).values():
            fp = getattr(node, 'file_path', None)
            if fp and Path(str(fp)).resolve() == target:
                candidates.append(node)
        if not candidates:
            return
        # 同一个文件里多个 label：**优先高亮当前 label**，不再永远命中第一个。
        node = next((n for n in candidates
                     if str(getattr(n, 'label_name', '') or '') == focus),
                    candidates[0])
        self._canvas.highlight_node(node.id)
        self._canvas.select_node(node.id)
        self._on_canvas_select(node.id)
    def _sync_label_combo(self):
        """
        把"这个文件里有哪些 label"填进左栏下拉，并选中当前 label。

        `blockSignals` 很重要：载入时 `addItems` 会发 `currentIndexChanged`，
        不挡住就会在 `_show_index` 之前又跳一次。
        """
        combo = getattr(self, '_label_combo', None)
        if combo is None:
            return
        labels = self._session.label_names()
        current = str(getattr(self._session, 'focus_label', '') or '')
        combo.blockSignals(True)
        try:
            combo.clear()
            for name in labels:
                combo.addItem(name)
            idx = combo.findText(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.setEnabled(len(labels) > 1)
            combo.setToolTip(
                f'这个 .rpy 里有 {len(labels)} 个 label；选一个就跳到它的第一拍'
                if len(labels) > 1 else '这个 .rpy 里只有一个 label')
        finally:
            combo.blockSignals(False)
    def _on_label_changed(self, index: int):
        """左栏 label 下拉：切到那个 label 的第一拍（先提交当前卡片的改动）。"""
        combo = getattr(self, '_label_combo', None)
        if combo is None or index < 0:
            return
        label = combo.itemText(index)
        target = self._session.index_of_label(label)
        if target < 0 or target == self._loaded_idx:
            return
        self.apply_current_beat()
        self._show_index(target)
        self._sync_graph_highlight()
        self._status(f'已切到 label「{label}」的第一拍（第 {target + 1} 拍）。')
    def _reload_list(self):
        """
        重建左栏：同一个 `.rpy` 有多个 label 时，**按 label 分组**（可折叠）。

        用户在这一轮之前只看到一串连续拍号，边界靠行尾 `〔label〕` 认；
        分组标题行说清"从哪到哪"，点标题还能折起来（大章节翻别的 label 时
        不用一路滚）。单 label 的常见情况**不加标题行** —— 没有歧义时
        多一行只是噪音，也保住了"行号 == 拍号"的老判据。

        行与拍的映射：每行 `Qt.UserRole` 存拍号（标题行是 -1），
        `_beat_rows[拍号] = 行号`。**不能再用行号当拍号**（标题行会插进来）。
        """
        labels = self._session.label_names()
        beats = list(getattr(self._session, 'beats', ()) or ())
        counts: dict = {}
        for b in beats:
            name = str(getattr(b, 'label', '') or '')
            counts[name] = counts.get(name, 0) + 1

        grouped = len(labels) > 1
        self._group_items = {}
        self._group_counts = counts
        self._beat_rows = []

        self._list.blockSignals(True)
        try:
            self._list.clear()
            last = None
            for i in range(len(self._session)):
                label = str(getattr(beats[i], 'label', '') or '')
                if grouped and label and label != last:
                    self._list.addItem(self._make_group_item(label, counts))
                    self._group_items[label] = self._list.item(
                        self._list.count() - 1)
                    last = label
                self._list.addItem(self._make_item(i))
                self._beat_rows.append(self._list.count() - 1)
        finally:
            self._list.blockSignals(False)
        self._apply_group_folding()

        self._who.clear()
        labels = self._character_labels()
        for cid in self._session.participants():
            shown = labels.get(cid, '')
            item = QListWidgetItem(shown or cid)
            # ⚠ **tag 留在数据里**：双击填进剧本的必须是 `DY`，不是「大爷」
            item.setData(Qt.UserRole, cid)
            if shown and shown != cid:
                item.setToolTip(f'{shown}（剧本里写的是 `{cid}`）')
            self._who.addItem(item)

    def _character_labels(self) -> dict:
        """
        `{tag: 人话名}`（说话人下拉 / 参与角色列表用）。

        真源在会话（映射的 `characters` / `vocabulary` 段 + 资源表的 `name`），
        这里只兜住"会话是测试里的假对象"那一档 —— 拿不到就空表，
        界面原样显示 tag（与从前逐字一致）。
        """
        getter = getattr(self._session, 'character_labels', None)
        if not callable(getter):
            return {}
        try:
            return dict(getter() or {})
        except Exception as e:                  # noqa: BLE001 - 名字取不到不该拦列表
            swallow('ui_qt.window_card._character_labels', e)
            return {}

    @staticmethod
    def _group_text(label: str, count: int, folded: bool = False) -> str:
        """分组标题那一行：`▾ chapter1_1　（4 拍）`。"""
        arrow = '▸' if folded else '▾'
        return f'{arrow} {label}　（{count} 拍）'

    def _make_group_item(self, label: str, counts: dict) -> QListWidgetItem:
        """
        建一行"label 分组标题"。

        **不可选中**（`ItemIsEnabled` 但不带 `ItemIsSelectable`）：
        标题不是一拍，被选中就会让"当前拍"指向一个不存在的位置；
        点它是折叠/展开（`_on_list_item_clicked`）。
        """
        item = QListWidgetItem(self._group_text(label, counts.get(label, 0)))
        item.setData(Qt.UserRole, -1)
        item.setData(_GROUP_LABEL_ROLE, label)
        item.setFlags(Qt.ItemIsEnabled)
        font = QFont(item.font())
        font.setBold(True)
        item.setFont(font)
        try:
            item.setForeground(QBrush(QColor(theme.t('text.secondary'))))
        except Exception as e:                  # noqa: BLE001 - 取色失败不影响分组
            swallow('ui_qt.window_card.group_color', e)
        item.setToolTip(f'label「{label}」：点这一行折叠 / 展开它的拍')
        return item

    def _row_of_beat(self, idx: int):
        """拍号 → 列表行号（没有分组标题行时就是它自己）。"""
        rows = getattr(self, '_beat_rows', None) or []
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return None
        if 0 <= idx < len(rows):
            return int(rows[idx])
        return None

    def _fold_project_key(self) -> str:
        """
        项目文件里"这一个文件"的键：**相对项目根**（正斜杠）。

        为什么不是绝对路径（折叠状态要**跟项目走**，2026-10-22 用户要求）：
        项目整个目录搬到别处、或者换台机器打开，绝对路径全变了 —— 状态就
        "没了"。相对路径只要项目内部结构不变就一直对得上。

        文件不在项目根里面（直接打开项目外的 `.rpy`）→ 返回空串：
        它不属于这个项目，写进项目的视图文件只会让下一个人困惑。
        """
        root = str(getattr(self, '_project_root', '') or '')
        path = str(getattr(self, '_file_path', '') or '')
        if not root or not path:
            return ''
        try:
            rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
        except (OSError, ValueError) as e:      # noqa: BLE001 - 算不出就退回偏好
            swallow('ui_qt.window_card._fold_project_key', e)
            return ''
        if os.path.isabs(rel) or rel == os.pardir \
                or rel.startswith(os.pardir + os.sep):
            return ''
        return rel.replace(os.sep, '/')

    def _fold_store_key(self) -> str:
        """
        偏好里"这一个文件"的键：**绝对路径**（**没打开项目**时才用它）。

        为什么不用 `script_path` 原样：同一份文件可以从项目根、从 `game/`、
        从命令行相对路径打开，三个字符串指同一份文件 —— 用相对路径当键，
        换个入口就"折叠状态没了"。

        有项目的时候走 `_fold_project_key`（项目内相对路径），这条只是兜底：
        单文件模式 / 文件在项目外 / 项目文件写不进去。
        """
        path = str(getattr(self, '_file_path', '') or '')
        if not path:
            return ''
        try:
            return str(Path(path).resolve())
        except OSError as e:                    # noqa: BLE001 - 解析不了就退回原字符串
            swallow('ui_qt.window_card._fold_store_key', e)
            return path

    def _pref_names(self) -> list:
        """旧偏好里"这个文件"折了哪些 label（形状 `{绝对路径: [label, …]}`）。"""
        key = self._fold_store_key()
        if not key:
            return []
        store = prefs.load_json(_FOLD_PREF_KEY, {})
        if not isinstance(store, dict):
            return []
        names = store.get(key) or ()
        return [str(n) for n in names if str(n or '').strip()]

    def _pref_drop(self):
        """把"这个文件"从偏好里删掉（已经迁进项目了，别留两份各自老化）。"""
        key = self._fold_store_key()
        if not key:
            return
        store = prefs.load_json(_FOLD_PREF_KEY, {})
        if not isinstance(store, dict) or key not in store:
            return
        store.pop(key, None)
        prefs.save_json(_FOLD_PREF_KEY, store)

    def _save_pref(self, folded) -> bool:
        """折叠状态写进用户偏好（**没有项目时**的存储，也是写项目失败时的退路）。"""
        key = self._fold_store_key()
        if not key:
            return False
        store = prefs.load_json(_FOLD_PREF_KEY, {})
        if not isinstance(store, dict):
            store = {}
        # 先删再插：dict 保持插入顺序，最后那个才是"最近用过的文件"。
        store.pop(key, None)
        if folded:
            store[key] = list(folded)
        if len(store) > _FOLD_PREF_LIMIT:
            store = dict(list(store.items())[-_FOLD_PREF_LIMIT:])
        return prefs.save_json(_FOLD_PREF_KEY, store)

    def _save_project_fold(self, rel: str, folded) -> bool:
        """折叠状态写进项目根的 `storygraph.session.json`（失败返回 False）。"""
        try:
            from storygraph.core import session_store as SS
            res = SS.remember_cards(self._project_root, rel, list(folded or ()))
        except Exception as e:                  # noqa: BLE001 - 写不进去也不能拦人
            swallow('ui_qt.window_card._save_project_fold', e)
            res = {'ok': False, 'reason': f'{type(e).__name__}: {e}'}
        if res.get('ok'):
            return True
        # 项目目录只读 / 文件被编辑器锁着 / 盘满 —— 说一句，然后退回偏好。
        self._log(f'[折叠] ⚠ 写不进项目里的 storygraph.session.json'
                  f'（{res.get("reason") or "未知原因"}）—— 这次记在用户偏好里，'
                  f'换机器不会跟着走')
        return False

    def _load_collapsed_pref(self):
        """
        读回"这个文件上次折了哪些 label"（**新载入一章时**调一次）。

        读的顺序（2026-10-22 起）：

          1. **项目里**那份（`storygraph.session.json` 的 `cards.folded`，
             键是项目内相对路径）—— 有它就以它为准，**看都不看**旧偏好：
             否则"我明明展开了，下次又折回去"会来回跳；
          2. 项目里没有这个文件的记录 → 读**旧偏好**（换版本之前的记忆）：
             有就**迁进项目**（迁移只发生一次），并把偏好那条删掉；
          3. 没有项目（单文件 / 文件在项目外）→ 仍然只读偏好。

        为什么不在 `__init__`：构造时还不知道会载入哪一章。为什么不用
        `_reload_list` 挡：它会跟着插卡 / 删卡 / 变量改动反复重建，从存储
        重读会把**这次会话里刚折好的**状态冲掉（存的是上一次的）。
        """
        self._collapsed_labels = frozenset()
        rel = self._fold_project_key()
        if not rel:
            self._collapsed_labels = frozenset(self._pref_names())
            return
        try:
            from storygraph.core import session_store as SS
            store = SS.project_cards(self._project_root)
        except Exception as e:                  # noqa: BLE001 - 读不出来就用偏好
            swallow('ui_qt.window_card._load_project_fold', e)
            store = {}
        if rel in store:
            self._collapsed_labels = frozenset(store[rel])
            return
        names = self._pref_names()
        self._collapsed_labels = frozenset(names)
        if not names:
            return
        if self._save_project_fold(rel, names):
            self._pref_drop()
            self._log(f'[折叠] 这个文件的折叠状态已改记到项目里'
                      f'（{SS.PROJECT_FILE} 的 cards 段）—— 换台机器 / '
                      f'把项目挪个目录都还认得')

    def _save_collapsed_pref(self):
        """
        把当前折叠状态写进存储：**有项目就写项目**，没有（或写不进去）写偏好。

        两个调用点，语义都是"**用户最后看到的样子**"：

          · `_toggle_label_group` —— 点了标题行（明确的折 / 展）；
          · `_expand_label` —— 导航进了一个折着的组，它被自动展开了。

        第二个也要记：只记"点过的那一次"的话，用户折起 A、跳进 A 看一眼、
        关掉，下次打开 A 又是折的 —— 与关窗那一刻看到的画面相反。
        """
        folded = sorted(str(n) for n in (self._collapsed_labels or ()))
        rel = self._fold_project_key()
        if rel and self._save_project_fold(rel, folded):
            return
        self._save_pref(folded)

    def _apply_group_folding(self):
        """按 `_collapsed_labels` 把分组子行藏起来 / 放出来（不重建列表）。"""
        groups = getattr(self, '_group_items', None) or {}
        if not groups:
            return
        collapsed = set(getattr(self, '_collapsed_labels', None) or ())
        beats = list(getattr(self._session, 'beats', ()) or ())
        rows = list(getattr(self, '_beat_rows', ()) or ())
        self._list.blockSignals(True)
        try:
            for label, head in groups.items():
                folded = label in collapsed
                head.setText(self._group_text(
                    label, (getattr(self, '_group_counts', None) or {})
                    .get(label, 0), folded))
            for i, row in enumerate(rows):
                item = self._list.item(row)
                if item is None or i >= len(beats):
                    continue
                label = str(getattr(beats[i], 'label', '') or '')
                item.setHidden(label in collapsed)
            # 当前拍被折进去时，把"当前"挪到分组标题上：否则列表里没有任何
            # 高亮，看起来像"选中丢了"（`_loaded_idx` 不动，卡片还显示那一拍）。
            cur = self._loaded_idx
            if 0 <= cur < len(rows) and rows[cur] < self._list.count():
                item = self._list.item(rows[cur])
                label = str(getattr(beats[cur], 'label', '') or '')
                if item is not None and item.isHidden() and label in groups:
                    self._list.setCurrentItem(groups[label])
        finally:
            self._list.blockSignals(False)

    def _expand_label(self, label: str):
        """展开某个 label 的分组（导航到它时不让人对着折叠行猜）。"""
        label = str(label or '')
        if not label:
            return False
        collapsed = set(getattr(self, '_collapsed_labels', None) or ())
        if label not in collapsed:
            return False
        collapsed.discard(label)
        self._collapsed_labels = frozenset(collapsed)
        self._apply_group_folding()
        # 展开放进记忆（"最后看到的样子"就是这个）：只在这个组**真的**从折着
        # 变成开着时才写，翻页本身不产生写盘。
        self._save_collapsed_pref()
        return True

    def _toggle_label_group(self, label: str):
        label = str(label or '')
        if not label or label not in (getattr(self, '_group_items', None) or {}):
            return
        collapsed = set(getattr(self, '_collapsed_labels', None) or ())
        if label in collapsed:
            collapsed.discard(label)
        else:
            collapsed.add(label)
        self._collapsed_labels = frozenset(collapsed)
        self._apply_group_folding()
        self._save_collapsed_pref()
        self._status(f'{"已折叠" if label in collapsed else "已展开"}'
                     f' label「{label}」的分组（再点标题行切换；'
                     f'下次打开这个文件还是这样）。')

    def _on_list_item_clicked(self, item):
        """点列表：分组标题 → 折叠/展开；拍 → 交给 `currentRowChanged`。"""
        if item is None:
            return
        data = item.data(Qt.UserRole)
        try:
            is_group = data is not None and int(data) < 0
        except (TypeError, ValueError):
            is_group = False
        if is_group:
            self._toggle_label_group(str(item.data(_GROUP_LABEL_ROLE) or ''))

    def _make_item(self, i) -> QListWidgetItem:
        item = QListWidgetItem(self._item_text(i))
        item.setData(Qt.UserRole, i)
        beat = self._session.beats[i]
        label = str(getattr(beat, 'label', '') or '')
        try:
            from storygraph.app.beat_clear import empty_note
            note = empty_note(beat)
        except Exception as e:                  # noqa: BLE001 - 只是标记
            swallow('ui_qt.window_card.make_item.empty_note', e)
            note = ''
        tips = []
        if label:
            tips.append(f'label：{label}')
        if note:
            tips.append(f'{note}（你清空了这一拍的文字；'
                        f'导出后它会只剩这些行）')
            # 灰掉：一眼扫出来哪些是"清空过"的壳子
            item.setForeground(QColor(T.t('text.tertiary')))
        item.setToolTip('\n'.join(tips))
        return item

    def _entry_of_beat(self, idx: int) -> dict:
        """
        拍号 → 这一行的摘要（给 `ShotDelegate` 排版用）。

        与 `_item_text` 同一个数据源（`EditorSession.list_entry`）：列表的
        文字内容与画出来的内容**不许各取一套** —— 那会出现"看起来是 A、
        复制出来是 B"。
        """
        try:
            return self._session.list_entry(int(idx))
        except Exception as e:                  # noqa: BLE001 - 画不出来也要画这行
            swallow('ui_qt.window_card.entry_of_beat', e)
            return {}

    def _item_text(self, i) -> str:
        """
        列表一行 = 这一拍的正文（拍号 / 台词 / 语句名）。

        label 边界从**行尾标记**升级成了**分组标题行**（`_reload_list`）：
        同一文件里多个 label 时，标题行写清 `▾ label（N 拍）`，还能折叠。
        多 label 时行内不再重复 `〔label〕` —— 同一件事说两遍，只是噪音。
        """
        return self._row_text(self._session.list_entry(i))

    @staticmethod
    def _row_text(e: dict) -> str:
        """
        左边列表那一行的**字**（2026-10-18：语句拍不再显示成"（无台词）"）。

        一条语句拍显示成

             3 ◆ play_music · 播放音乐  track=bgm file="bgm/theme.ogg"

        而不是「3 ：（无台词）」—— 后者让所有非台词的拍在列表里长得一样，
        人得一张张点进去才知道哪条是哪条。

        ⚠ 记号用 `◆` 而不是 `⚙`（2026-10-18 截图当场看出来的）：`⚙`(U+2699)
          在微软雅黑里没有字形，界面上是个豆腐块 `□`。
        """
        head = str(e.get('statement_head') or '')
        flow_type = str(e.get('flow_type') or '')
        if flow_type:
            arrow = '↳' if flow_type == 'call' else '→'
            verb = '调用' if flow_type == 'call' else '跳转'
            return (f"{int(e.get('index', 0)) + 1:>3} "
                    f"{arrow} {verb} {e.get('flow_target') or '（未指定）'}")
        if head:
            name = str(e.get('statement') or '')
            rest = head[len(name):].strip() if head.startswith(name) else head
            label = str(e.get('statement_label') or '')
            tail = f' · {label}' if label else ''
            return f"{int(e.get('index', 0)) + 1:>3} ◆ {name}{tail}  {rest}"
        note = str(e.get('empty_note') or '')
        if note:
            # **分级删除留下的空拍**：说清"我还剩下什么"，
            # 而不是混进「（无台词）」里（用户 2026-10-25）
            return (f"{int(e.get('index', 0)) + 1:>3} ○ {note}")
        return (f"{int(e.get('index', 0)) + 1:>3} "
                f"{e['prefix']}{e['who']}：{e['text'][:24]}")
    def refresh_list_row(self, i: int):
        """
        单独重画列表里的第 i 行（保存一拍之后调用）。

        为什么需要它：改完台词只更新**卡片**的话，左边列表还挂着旧文本 ——
        用户看到的列表和卡片不一致，会以为没保存上（tkinter 版有这一步，
        搬过来时漏了）。整表重画会丢掉滚动位置与选中态，所以只画一行。
        """
        if not self._session.has(i):
            return
        row = self._row_of_beat(i)
        item = self._list.item(row) if row is not None else None
        if item is None:
            return
        self._list.blockSignals(True)
        try:
            item.setText(self._item_text(i))
            item.setData(Qt.UserRole, i)
        finally:
            self._list.blockSignals(False)
    def insert_card(self, after: bool = True):
        """在当前这一拍前/后插入一张空卡片（语义在 `EditorSession.insert_beat`）。"""
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没法插入卡片。')
            return
        self.apply_current_beat()             # 先把这一拍的表单提交，免得丢改动
        res = self._session.insert_beat(idx, after=after)
        if not res.get('ok'):
            self._status(res.get('error') or '插入失败')
            return
        # 插卡会让后面的拍全部换 line_id：图边跟着重认身份，否则下次
        # 改目标会按过期 ID 找不到那条边、又画一条新的。
        self._refresh_flow_edges()
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f"[卡片] 已在第 {idx + 1} 拍{'后' if after else '前'}插入一张空卡片"
                  f"（现在是第 {res['index'] + 1} / {res['count']} 拍）")
        self._status(f"✔ 已插入一张空卡片（第 {res['index'] + 1} 拍）——"
                     f"填完按「💾 保存这一拍」，导出时才落盘。")

    def _flow_target_labels(self) -> list:
        """
        插入控制流卡时「目标 label」的**全部**候选。

        ⚠ 2026-10-23 修（用户截图："下拉……缺失"）：第一版只给
        `session.label_names()` —— 那是**当前这个文件**的 label。真机
        `chapter0_0.rpy` 里只有一个 label，于是下拉里只有 `chapter0_0`：
        用户想 call 的小 label 一个都不在，下拉等于没有。
        现在补上**项目级目录**（与左侧导航同一个 `chapter_index`，40 个文件
        约 10ms），并且当前这一章排在最前面。
        """
        labels = [str(x) for x in self._session.label_names() if str(x).strip()]
        graph = getattr(self, 'graph', None)
        for node in ((graph.nodes or {}).values() if graph is not None else ()):
            name = str(getattr(node, 'label_name', '') or '').strip()
            if name and name not in labels:
                labels.append(name)
        try:
            from storygraph.app.chapter_index import build_index
            index = build_index(getattr(self, '_project_root', None))
            for entry in index.files:
                for item in entry.labels:
                    name = str(getattr(item, 'name', '') or '')
                    if name and name not in labels:
                        labels.append(name)
        except Exception as e:                    # noqa: BLE001 - 扫盘失败不该挡住插卡
            swallow('ui_qt.insert_flow_card.project_labels', e)
        return labels

    def insert_flow_card(self, after: bool = True):
        """
        在当前拍后插入一张 `call` / `jump` 控制流卡（2026-10-22，P0-4a）。

        这是"大 label 里 call 小 label 再回来"的**入口**：不用回源文件手写，
        选类型 + 目标即可。插入的是内存里的拍，导出时才落盘。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没法插入控制流卡。')
            return
        self.apply_current_beat()
        labels = self._flow_target_labels()
        from storygraph.ui_qt.dialogs import FlowCardDialog
        dlg = FlowCardDialog(self, labels=labels)
        if dlg.exec() != QDialog.Accepted:
            return
        res = self._session.insert_flow_beat(
            idx, after=after, flow_type=dlg.flow_type, target=dlg.target,
            args=getattr(dlg, 'args', ''), from_clause=getattr(dlg, 'from_clause', ''))
        if not res.get('ok'):
            self._status(res.get('error') or '插入控制流卡失败')
            return
        edge_note = self._flow_edge_note(self._refresh_flow_edges())
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        extra = ''.join(x for x in (getattr(dlg, 'args', ''),
                                    (f' from {dlg.from_clause}'
                                     if getattr(dlg, 'from_clause', '') else ''))
                        if x)
        self._log(f'[控制流] 插入 {dlg.flow_type} {dlg.target}{extra}'
                  f'（第 {res["index"] + 1} 拍）'
                  + (f'；{edge_note}' if edge_note else ''))
        self._status(f'✔ 已插入 {dlg.flow_type} {dlg.target}{extra}'
                     f'（第 {res["index"] + 1} 拍）'
                     + (f'。{edge_note}' if edge_note else ''))

    def insert_choice_point(self):
        """
        在当前这一拍后面放一个**选择点**（S2，2026-10-24）。

        落点（两件事，两个文件）：
          · `menu` 写进 `<章节>_choices.rpy` —— 工具独占，导出控制流和
            导出剧本都**不碰**它（控制流产物是整份重生成的，塞进去会被冲掉）；
          · 剧本里**只多一行** `call <label> from _call_<label>`，
            而且只进**内存**，导出才落盘（与 S1 同一条规矩）。

        为什么不是"先把 menu 插进剧本"：那样一次就 +5 行起，正文当场被搞乱，
        而且缩进归属一错就是"语法合法、语义错了"。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('先选一张卡，再插入选择点。')
            return
        chapter = Path(str(getattr(self._session, 'script_path', '') or ''))
        label = str(getattr(self._session.beats[idx], 'label', '') or '')
        root = getattr(self, '_project_root', None) or chapter.parent

        from storygraph.story.choice_point import (
            apply_choice_point, plan_choice_point,
        )
        plan = plan_choice_point(root, chapter, label)
        if not plan.ok:
            self._status('插入选择点被拦住了：' + '；'.join(plan.blocking))
            return

        what = '新建' if plan.creates_file else '追加到'
        if QMessageBox.question(
                self, '插入选择点',
                f'会在这一拍后面插入一行：\n\n    {plan.call_line}\n\n'
                f'menu 会{what}：{plan.path}\n\n'
                f'剧本正文只多这一行（导出才落盘）；'
                f'选项文字稍后在那个文件里改。继续？'
        ) != QMessageBox.Yes:
            return

        res = apply_choice_point(plan, root)
        if not res.get('ok'):
            self._status(f'选择点文件写不进去：{res.get("error")}')
            return

        beat_res = self._session.insert_flow_beat(
            idx, after=True, flow_type='call', target=plan.label,
            from_clause=plan.from_clause)
        if not beat_res.get('ok'):
            self._status('选择点文件写好了，但插那一行失败：'
                         f'{beat_res.get("error")}')
            return

        try:
            self._refresh_flow_edges()
            self._reload_list()
            self._show_index(int(beat_res.get('index', idx)))
        except Exception as e:               # noqa: BLE001 - 界面没刷新不该回滚文件
            swallow('ui_qt.window_card.insert_choice_point.refresh', e)

        self._log(f'[选择点] {plan.label} → {plan.path}；'
                  f'剧本里插了 {plan.call_line}')
        self._status(f'已插入选择点 {plan.label}（剧本只多一行，导出才落盘）')

    # ---------- 分级删除（用户 2026-10-25）----------

    def _beat_brief(self, beat) -> tuple:
        """`(谁, 台词/流程摘要)` —— 确认框里那一行。"""
        from storygraph.story.flow import flow_card_text, parse_flow
        flow = parse_flow(beat)
        who = '控制流' if flow else (
            str(getattr(beat, 'speaker', '') or '') or '（旁白）')
        text = (flow_card_text(flow) if flow
                else (str(getattr(beat, 'text', '') or '')[:60]
                      or '（没有台词）'))
        return who, text

    def delete_options(self, idx: int) -> list:
        """
        三档删除各自的"将去掉几行 / 会留下几行 / 能不能做"（**纯计算**）。

        抽成独立方法是为了可测：界面只负责把这些话摆出来，
        判据直接查这个列表，不用去点按钮。
        """
        from storygraph.app.beat_clear import (MODES, MODE_WHOLE,
                                               normalize_mode)
        out = []
        for mode, label, desc in MODES:
            mode = normalize_mode(mode)
            if mode == MODE_WHOLE:
                prev = self._delete_preview(idx)
                out.append({'mode': mode, 'label': label, 'desc': desc,
                            'enabled': bool(prev.get('ok', True)),
                            'lines': int(prev.get('lines') or 0),
                            'keep': 0,
                            'head': [str(x) for x in (prev.get('head') or ())],
                            'why': str(prev.get('error') or '')})
                continue
            plan = self._session.clear_preview(idx, mode) or {}
            ok = bool(plan.get('ok'))
            drops = [str(x) for x in (plan.get('drop_lines') or [])]
            out.append({'mode': mode, 'label': label, 'desc': desc,
                        'enabled': ok,
                        'lines': len(drops),
                        'keep': len(plan.get('keep_lines') or []),
                        'head': drops[:3],
                        'why': str(plan.get('blocking') or '')})
        return out

    def _delete_choice_text(self, idx: int, who: str, text: str,
                            options: list) -> str:
        """
        三选一弹框里那段正文（**纯计算**，判据直接查它，不用点按钮）。

        每一档都要说清"去掉几行、**去掉的是哪几行**、留几行" ——
        `8 行` 是数字，`$ renpy.block_rollback()` 才是人能认出来的东西
        （2026-10-21 那条教训，在分级删除里同样成立）。
        """
        rows = []
        for opt in options:
            if not opt['enabled']:
                rows.append(f"· {opt['label']}：这一档用不了 —— {opt['why']}")
                continue
            head = [str(x) for x in (opt.get('head') or ())]
            brief = ''
            if head:
                more = opt['lines'] - len(head)
                brief = '（' + ' ／ '.join(h.strip() for h in head)
                brief += ' …' if more > 0 else ''
                brief += '）'
            rows.append(f"· {opt['label']}：去掉 {opt['lines']} 行{brief}、"
                        f"留 {opt['keep']} 行 —— {opt['desc']}")
        return '\n'.join(rows)

    def _ask_delete_mode(self, idx: int, who: str, text: str,
                         options: list) -> str:
        """
        弹出三选一，返回 `'text'` / `'text_stage'` / `'whole'`；取消返回 `''`。

        用 `QMessageBox` 的自定义按钮（不是 `question()`：那只有是/否，
        而这里要人**挑一档**）。默认高亮第二档 —— 按用户描述的事故，
        "文字和舞台清掉、代码留下"才是他真正想要的结果。
        """
        box = QMessageBox(self)
        box.setWindowTitle('删这张卡，删到什么程度？')
        box.setText(f'第 {idx + 1} 拍　{who}：{text}')
        box.setInformativeText(
            self._delete_choice_text(idx, who, text, options))
        buttons = {}
        for opt in options:
            role = (QMessageBox.DestructiveRole if opt['mode'] == 'whole'
                    else QMessageBox.ActionRole)
            btn = box.addButton(opt['label'], role)
            btn.setEnabled(bool(opt['enabled']))
            if not opt['enabled']:
                btn.setToolTip(opt.get('why') or '')
            buttons[btn] = opt['mode']
        cancel = box.addButton('取消', QMessageBox.RejectRole)
        buttons[cancel] = ''
        stage_btn = next((b for b, m in buttons.items()
                          if m == 'text_stage' and b.isEnabled()), None)
        if stage_btn is not None:
            box.setDefaultButton(stage_btn)
        box.exec()
        return buttons.get(box.clickedButton(), '')

    def _do_clear(self, idx: int, mode: str, who: str, text: str) -> dict:
        """执行"清空"这一档（内存 + 结构撤销栈），并把结果说出来。"""
        res = self._session.clear_beat(idx, mode)
        if not res.get('ok'):
            self._status(res.get('error') or '清空失败')
            return res
        drop = int(len(res.get('drop_lines') or []))
        keep = int(len(res.get('kept_lines') or []))
        remains = str(res.get('remains') or '')
        name = '只清空文字' if mode == 'text' else '清空文字和舞台'
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f'[卡片] 第 {idx + 1} 拍已「{name}」（{who}：{text}）：'
                  f'产物里少了 {drop} 行、留下 {keep} 行'
                  + (f'（{remains}）' if remains else '')
                  + '，**代码块一个字没动**；'
                    '点「↩ 撤销结构操作」可以放回来')
        self._status(f'✂ 已{name}：少 {drop} 行、留 {keep} 行'
                     + (f'（{remains}）' if remains else ''))
        return res

    def delete_card(self):
        """
        删掉当前这一拍 —— **先问删到什么程度**。

        三档（`app/beat_clear.MODES`）：
          ① 只清空文字（代码块、舞台都留）
          ② 清空文字和舞台（**代码块留**）   ← 默认
          ③ 整个删掉（连代码块一起）        ← 从前唯一的选择

        为什么要有①②（用户 2026-10-25）：

          > 当我删掉卡片的时候……我会顺带把一些代码块什么的也删掉……
          > 能不能说：只删掉句子等……而不删掉代码块呢……？

        一张卡 = 一句话 + 它附带的代码行，整拍删等于把 CodeBlock 里那几行
        手写代码一起带走 —— 于是"删除需要慎用"。分级之后，默认那一档
        只掉一句台词的量，掉代码要人自己再点一次。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没有可删的卡片。')
            return
        beat = self._session.beats[idx]
        who, text = self._beat_brief(beat)
        options = self.delete_options(idx)
        mode = self._ask_delete_mode(idx, who, text, options)
        if not mode:
            return
        if mode != 'whole':
            self._do_clear(idx, mode, who, text)
            return

        # —— 第三档：整拍删除（与从前一致）——
        preview = self._delete_preview(idx)
        if preview and preview.get('ok') is False \
                and not preview.get('can_cascade'):
            self._status(preview.get('error') or '这一拍删不了。')
            return
        res = self._session.delete_beat(idx)
        if not res.get('ok') and res.get('can_cascade'):
            n = int(res.get('blocked_count') or 0)
            wide = self._delete_preview(idx, cascade=True)
            if QMessageBox.question(
                    self, '删除整块',
                    f'{res.get("error") or "这是块的开头"}'
                    f'\n\n要连着块体一起删掉吗？（共 {n + 1} 拍）'
                    + self._delete_line_note(wide)
            ) != QMessageBox.Yes:
                return
            preview = wide
            res = self._session.delete_beat(idx, cascade=True)
        if not res.get('ok'):
            self._status(res.get('error') or '删除失败')
            return
        edge_note = self._flow_edge_note(self._refresh_flow_edges())
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        lines = int(preview.get('lines') or 0)
        tail = f'，产物里少了 {lines} 行（{self._lines_brief(preview)}）' \
            if lines > 1 else ''
        self._log(f'[卡片] 已删除第 {idx + 1} 拍（{who}：{text}）{tail} ——'
                  f'还剩 {res["count"]} 拍；点「↩ 撤销结构操作」可以放回来'
                  + (f'；{edge_note}' if edge_note else ''))
        self._status(f'🗑 已删除第 {idx + 1} 拍{tail}（还剩 {res["count"]} 拍）。'
                     + (f'{edge_note}。' if edge_note else ''))

    # ---------------- 删除前"要丢几行" ----------------

    def _delete_preview(self, idx: int, cascade: bool = False) -> dict:
        """问会话"删这一拍会让产物少什么"；算不出来就返回空（不拦操作）。"""
        try:
            return self._session.delete_preview(idx, cascade=cascade) or {}
        except Exception as e:                    # noqa: BLE001 - 预览失败不拦删除
            swallow('ui_qt.window_card._delete_preview', e)
            return {}

    @staticmethod
    def _lines_brief(preview: dict) -> str:
        """`前两行…` 的短版本（状态栏／控制台用，一行说完）。"""
        head = [str(x) for x in (preview.get('head') or ())]
        if not head:
            return '多行'
        brief = ' ／ '.join(head[:2])
        if int(preview.get('lines') or 0) > len(head):
            brief += ' …'
        return brief

    def _delete_line_note(self, preview: dict) -> str:
        """
        确认框里那段"要丢几行"。

        单行拍的多数情况返回空串（不打扰）；多行拍把**前两行原文**摆出来 ——
        "8 行"是数字，`$ renpy.block_rollback()` 才是人能认出来的东西。
        """
        lines = int(preview.get('lines') or 0)
        if lines <= 1:
            return ''
        head = [str(x) for x in (preview.get('head') or ())]
        more = lines - len(head)
        body = '\n'.join('    ' + h for h in head)
        tail = f'\n    …（其余 {more} 行）' if more > 0 else ''
        # ⚠ 这里**不用 Markdown 的 `**`**：`QMessageBox` 只认纯文本/HTML，
        # 星号会原样显示（截图里就是 `**8 行**`），比不加粗更难看。
        return f'\n\n⚠ 这一拍在产物里是 {lines} 行（不是 1 行）：\n{body}{tail}'

    def restore_card(self):
        """
        撤销**最近一次结构编辑**（插卡或删卡，可连按多级）。

        优先走结构撤销栈（`undo_structural`）；栈空时退回旧的单级
        `restore_last_removed`（老表/老调用方仍能拿到那个行为）。
        """
        if self._session.can_undo_structural():
            res = self._session.undo_structural()
        else:
            res = self._session.restore_last_removed()
        if not res.get('ok'):
            self._status(res.get('error') or '没有可恢复的卡片。')
            return
        edge_note = self._flow_edge_note(self._refresh_flow_edges())
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f'[卡片] 已把删掉的卡片放回第 {res["index"] + 1} 拍'
                  + (f'；{edge_note}' if edge_note else ''))
        self._status(f'↩ 已恢复那一张卡片（第 {res["index"] + 1} 拍）。'
                     + (f'{edge_note}。' if edge_note else ''))
    def resync_after_export(self, res: dict) -> None:
        """
        导出成功之后刷新卡片列表（2026-10-05）。

        为什么需要：**原地导出**会把内存重新对齐到刚写出的那份文件
        （行号与**粒度**都由解析器定，见 `session_export._reload_after_inplace_write`）
        —— 刚插的那张"只有注释/代码"的卡可能和后面那张并成一张。
        列表不刷新的话，界面还显示旧的那些行，用户点哪一张都对不上，
        `_loaded_idx` 也可能越界。

        只在"拍数真的变了"时才动：没变就别打扰 —— 刷新会丢掉用户
        在列表里的选择状态，而"改一句台词再导出"是最常见的操作。
        """
        count = int(res.get('count') or 0) or len(self._session)
        if count == len(getattr(self, '_beat_rows', ()) or ()):
            return
        self._reload_list()
        self._show_index(max(0, min(int(self._loaded_idx), count - 1)))
        self._sync_structural_buttons()
        self._log(f'[导出] 写回之后重新对齐到磁盘：'
                  f'{count} 拍（结构由文件决定）')

    def _sync_structural_buttons(self):
        """「撤销结构操作」在**结构栈非空**（或还留着刚删的拍）时可用。"""
        for btn, on in ((getattr(self, '_restore_btn', None),
                         self._session.can_undo_structural()
                         or self._session.has_removed()),
                        (getattr(self, '_delete_btn', None),
                         len(self._session) > 1),
                        (getattr(self, '_insert_btn', None),
                         len(self._session) > 0),
                        (getattr(self, '_insert_before_btn', None),
                         len(self._session) > 0)):
            if btn is not None:
                btn.setEnabled(bool(on))
    def next_dialogue(self):
        """
        「下一句台词 ▶」—— 用户 2026-09-28 要的**只改台词**快捷路径（本轮补做）。

        用户要的是"快速过一遍台词"：一句一句跳，不必每次都在列表里找。
        ⚠ **不做按内容的过滤**（把没有台词的拍藏起来）：`_show_index` 靠的是
        "这一拍在会话里的下标"，按内容藏行会让"翻到下一句"跳过别的拍时
        对不上账。label 分组折叠是**用户自己按的**（`_on_list_item_clicked`），
        而且任何导航都会先展开那一组（`_show_index` → `_expand_label`），
        所以不会出现"跳进折叠区、列表没高亮"。
        """
        beats = getattr(self._session, 'beats', None) or []
        if not beats:
            self._status('这一章还没载入拍')
            return
        # ⚠ 别用 `... or -1`：`self._idx == 0` 是**合法的第一拍**，
        #   而 `0` 是 falsy —— 会被悄悄换成 -1（判据当场抓到，害我少跳一拍）。
        cur = getattr(self, '_idx', -1)
        start = max(int(-1 if cur is None else cur) + 1, 0)
        for i in range(start, len(beats)):
            if self._has_dialogue(beats[i]):
                self._show_index(i)
                self._focus_dialogue()
                self._status(f'第 {i + 1} 句台词（共 {len(beats)} 拍）')
                return
        # 后面没有了 → **说清楚**，不许默默不动（能点就必须有反应）
        self._status('后面没有台词了 —— 这是最后一句')

    @staticmethod
    def _has_dialogue(beat) -> bool:
        """这一拍有没有台词（谁说了句话）。"""
        if beat is None:
            return False
        kind = getattr(beat, 'kind', '')
        name = getattr(kind, 'name', '') or str(kind)
        text = str(getattr(beat, 'text', '') or '').strip()
        if not text:
            return False
        return name.upper() in ('SAY', 'EXTEND') or bool(
            str(getattr(beat, 'speaker', '') or '').strip())

    def _focus_dialogue(self):
        """把光标放进台词框（跳过去就能直接打字）。"""
        panel = getattr(self, '_panel', None)
        for name in ('text', '_text', 'text_edit'):
            widget = getattr(panel, name, None) if panel is not None else None
            if widget is not None and hasattr(widget, 'setFocus'):
                widget.setFocus()
                return

    def _sync_export_dirty(self):
        """
        状态栏右下角的「● 未导出 / ✔ 已同步」**跟着会话走**，不跟着翻页走。

        2026-10-22 修：从前 `_show_index` 每翻一页就把标签切成「✔ 已同步」，
        于是"改了三拍还没导出"这件事被界面自己抹掉了（而关窗时也不问，
        改动就这么没了）。现在唯一的判据是 `session.has_unsaved_edits()`：
        载入 / 导出后是 False，任何改拍的路径（保存这一拍 / 撤销 / 插卡 /
        删卡 / 变量工作台）之后是 True。
        """
        try:
            dirty = bool(self._session.has_unsaved_edits())
            path = str(getattr(self._session, 'script_path', '') or '')
            self._status_bar.set_dirty(dirty, path)
            # **标题栏也要刺眼**（2026-10-23，清单 §八）：一行状态栏小字
            # 救不了"我按了 Ctrl+S 以为存了"。`*` = 这一章有未导出的改动，
            # `●` = 剧情图布局有未保存改动；两个可以同时出现。
            self._script_dirty = dirty
            prefix = ('* ' if dirty else '')
            prefix += ('● ' if bool(getattr(self, '_graph_dirty', False))
                       else '')
            self.setWindowTitle(prefix + self._base_title)
        except Exception as e:                  # noqa: BLE001 - 只影响一个提示
            swallow('ui_qt.window_card._sync_export_dirty', e)

    def show_beat(self, idx: int) -> bool:
        """
        公开的「跳到第 idx 拍」（校验清单 / 诊断条目用）。

        为什么要这层包装：`_show_index` 是同一张窗口里几个 mixin 共用的内部
        约定，而 `window_graph_ops`（另一个 mixin）要调它。给它一个**公开
        名字**，比让两个模块互相摸对方的私有方法清楚；越界**返回 False**
        而不是静默摆到"没有这一拍"的状态 —— 调用方（校验清单）要据此告诉
        用户"这条跳不过去"，而不是假装跳了。
        """
        try:
            i = int(idx)
        except (TypeError, ValueError):
            return False
        if i < 0 or i >= len(list(getattr(self._session, 'beats', ()) or ())):
            return False
        self._show_index(i)
        return True

    def _show_index(self, idx: int):
        if not self._session.has(idx):
            self._idx = -1
            self._session.mark_loaded(-1)
            # 空会话 / 拍号越界：**图也要清掉**（否则卡片上顶着上一章的舞台图，
            # 而左边一行字都没有 —— 那是纯粹的误导）。
            self._panel.clear_stage_frame()
            # 尺寸行同理：不能显示上一章那一拍的背景/图层尺寸
            self._panel.set_stage_size([])
            self._update_nav()
            self._sync_undo_button()
            self._sync_inspector(-1)
            self._sync_nav_current()
            return
        self._idx = idx
        # 告诉会话"前端现在显示这一拍"（`undo()` 不带参数时的默认目标）
        self._session.mark_loaded(idx)
        # 当前 label 跟着**实际显示的那一拍**走：翻到别的 label 时，
        # 面包屑 / 下拉 / 图上的高亮都应当一起变。
        self._session.focus_label = str(
            getattr(self._session.beats[idx], 'label', '') or '')
        # `source_lines` = 这一拍在源 .rpy 里的行号范围 → 喂给 CodeBlock 的
        # 行号栏（用户 2026-10-25："精确到行……不然我都不知道它到底在哪儿"）。
        _beat = self._session.beats[idx]
        self._panel.load_fields(
            self._session.fields(idx), self._session.cards(idx),
            source_lines=(getattr(_beat, 'src_first', None),
                          getattr(_beat, 'src_last', None)),
            inherited=self._session.stage_state(idx))
        self._sync_spec_args(idx)
        # 说话人下拉：屏幕上是人话（「大爷」），写回 beat 的仍是 tag（`DY`）
        self._panel.set_speakers(
            pairs(self._session.characters(), self._character_labels()),
            self._session.beats[idx].speaker or '')
        self.refresh_derived_panels(idx)
        # 导航到哪一拍，那一组就自动展开（折叠状态不该挡住"跳过去"）
        self._expand_label(str(getattr(self._session.beats[idx], 'label', '') or ''))
        row = self._row_of_beat(idx)
        self._list.blockSignals(True)
        if row is not None:
            self._list.setCurrentRow(row)
        self._list.blockSignals(False)
        self._sync_label_combo()
        # 翻页**不改**"导出过没有"：这里从前写死 `set_dirty(False)`，
        # 于是改了没导出、翻一页就显示"已同步"（2026-10-22 修）。
        self._sync_export_dirty()
        self._update_nav()
        self._sync_undo_button()
        self._sync_structural_buttons()
        # 右栏属性表（文件 / label / 第几拍 / 源行）+ 左侧导航的"当前这一章"
        # —— 都只读会话，改一个字也不写回去。
        self._sync_inspector(idx)
        self._sync_nav_current()
        # 相邻几拍的立绘先解进缓存（翻卡片不空等；见 `_prefetch_neighbours`）
        self._prefetch_neighbours(idx)

    def _sync_spec_args(self, idx: int):
        """
        把这一拍的**语句参数**（列 + 当前值 + 候选）推给卡片那一组控件。

        语义在 `app.beats.spec_arg_*`（与 Excel 的列**同源**），这里只递数据 ——
        前端不认识"哪些语句有参数"这件事。

        取注册表失败（非项目目录 / 读不成）时**不炸**：退化成"只有内置形状"
        （`camera` / `with` / `pause` / `window` 由工具自己声明，不需要项目声明）。
        """
        from storygraph.app import beats as _beats
        try:
            beat = self._session.beats[idx]
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_card._sync_spec_args.beat', e)
            return
        statements = None
        try:
            root = getattr(self._session, 'project_root', None)
            if root:
                from storygraph.story.statement_registry import StatementRegistry
                statements = StatementRegistry.for_project(str(root))
        except Exception as e:                  # noqa: BLE001 - 没声明也要能显示内置形状
            swallow('ui_qt.window_card._sync_spec_args.registry', e)
            statements = None
        try:
            cols, values = _beats.spec_arg_fields_for(beat, statements)
            registry = getattr(getattr(self._session, 'symbols', None),
                               'registry', None)
            vocab = self._vocabulary()
            choices = _beats.spec_arg_choices_for(beat, statements,
                                                  registry=registry,
                                                  vocab=vocab)
            # 候选翻成屏幕上那句（写回去的仍是候选里的原值）：
            # `with` 那格的 `Fade(0.2,0,0.2)` → 「闪白」，`dissolve` → 原样。
            kinds = {str(c.get('param') or c.get('name') or ''): arg_kind_of(c)
                     for c in (cols or ())}
            shown = {param: labeled(vals, vocab, kinds.get(param, ''))
                     for param, vals in choices.items()}
            self._panel.set_spec_args(
                cols, values, shown or choices,
                statement=_statement_of(beat, statements),
                loose=_loose_lines(beat))
        except Exception as e:                  # noqa: BLE001 - 参数组刷不动不该拦住卡片
            swallow('ui_qt.window_card._sync_spec_args', e)
        self._sync_flow_card(beat, idx)

    def _refresh_flow_edges(self) -> dict:
        """
        用**当前这一章的拍**重新派生剧情图上的 `source` 边。

        这是 `_sync_flow_edge` 的替代品（2026-10-22 第三阶段）：从前保存时
        手动改一条边、导入时又派生一遍，是两份真源。现在只有
        `flow_edges(beats)` 一份 —— 保存 / 插卡 / 删卡 / 导航顺带提交之后
        都走这里，图边由拍算出来。

        ## 为什么不会拖慢编辑

        `rebuild_source_edges` 只扫这一章的拍 + 图上这些 label 的边，
        复杂度是 `O(本章拍数 + 相关边数)`，不扫整个项目；真没变化时
        连画布都不重建（返回的计数全 0）。实测见
        `docs/控制流卡_第三阶段_2026-10-22.md`。

        返回 `rebuild_source_edges()` 的结果；失败时返回 `{'error': ...}`
        让调用方把"图上的边没同步"说出来，**不拦保存**。
        """
        if self.graph is None:
            return {}
        beats = list(getattr(self._session, 'beats', None) or ())
        if not beats:
            return {}
        labels = {str(getattr(b, 'label', '') or '') for b in beats}
        labels.discard('')
        if not any(str(getattr(n, 'label_name', '') or '') in labels
                   for n in (self.graph.nodes or {}).values()):
            return {}
        try:
            from storygraph.app.session_graph import rebuild_source_edges
            res = rebuild_source_edges(self.graph, beats)
        except Exception as e:                    # noqa: BLE001 - 同步失败不该拦保存
            swallow('ui_qt.window_card._refresh_flow_edges', e)
            return {'error': str(e)}
        # 只改 line_id（identified）在画面上看不出来，不必重建画布；
        # 端点 / 条数 / 类型变了才需要重画。
        if (res.get('added') or res.get('retargeted') or res.get('removed')):
            self._set_graph(self.graph)
        return res

    def _flow_edge_note(self, res: dict) -> str:
        """把派生结果念成人话（统一文案在 `ui_qt.flow_notes`）。"""
        return edge_rebuild_note(res)

    def _flow_target_brief(self, target) -> dict:
        """
        目标那一章的摘要（给控制流条写"几拍 / 第几行 / 本章几处在调它"）。

        只读、不写盘：
          · 同一个文件（当前会话里）→ 用现有拍算：几拍 + 首个源行 + 本章调用数；
          · 别的文件 → 用左侧导航那份**已在内存里**的项目目录（`_nav.index()`）
            拿文件名与行号，**不解析别的文件**（为了显示一行字去扫盘不值得）。
        数据缺一格就不显示那一格（宁可不写，也不编）。

        目标是个**有名 menu**（`menu choice_a:`）时，另带上 `menu_brief_text`
        那几行（选项各自去哪）—— 用户 2026-10-25：

            > 它哪里写的是：menu……可是它没有显示 menu 的内容是什么……
            > 又跳去哪儿……（不然要我点进去才知道）

        menu 名在 Ren'Py 里是真 label（`jump choice_a` 合法），但拍的
        `label` 是**外层 label**，所以 `label_brief` 认不到它 —— 这时走
        `menu_brief` 那条。若目标既像 label 又像 menu，**两件都是事实**，
        就都摆出来（不猜语义、不挑一个、不编造）。
        """
        from storygraph.story.flow import (
            callers_of, label_brief, menu_brief, menu_brief_text)

        name = str(target or '').strip()
        if not name:
            return {}
        beats = list(getattr(self._session, 'beats', None) or ())
        brief = label_brief(beats, name)
        # 名字是个有名 menu 时的选项清单（不是 menu / 找不到 → `{}`）。
        menu = menu_brief(beats, name)
        menu_text = menu_brief_text(menu) if menu else ''
        if brief:
            brief['same_file'] = True
            brief['file'] = Path(self._file_path).name if self._file_path else ''
            brief['callers'] = len(callers_of(beats, name))
            if menu_text:
                brief['menu_text'] = menu_text
            return brief
        if menu_text:
            # 只是个有名 menu（不是普通 label）：控制流条上写清它有几条、
            # 各自去哪，就不用点进去看了。count 不塞进 brief —— 那是**选项数**，
            # 会被渲染成"N 拍"，驴唇不对马嘴。
            return {'same_file': True,
                    'file': Path(self._file_path).name if self._file_path else '',
                    'callers': len(callers_of(beats, name)),
                    'menu_text': menu_text}
        nav = getattr(self, '_nav', None)
        try:
            index = nav.index() if nav is not None else None
        except Exception as e:                    # noqa: BLE001 - 摘要失败不该拦卡片
            swallow('ui_qt.flow_target_brief.index', e)
            index = None
        for entry in getattr(index, 'files', ()) or ():
            for item in getattr(entry, 'labels', ()) or ():
                if str(getattr(item, 'name', '') or '') == name:
                    return {'file': entry.name, 'line': int(item.line or 0),
                            'same_file': False}
        return {}

    def _sync_flow_card(self, beat, idx: int):
        """
        把这一拍是不是 `call` / `jump`、目标与返回拍推给控制流条。

        候选 label 用 `_flow_target_labels()`：本文件 + 图上的节点 +
        **项目目录**（2026-10-23 修：从前只有本文件，真机下拉里只有一项）。
        """
        try:
            from storygraph.story.flow import flow_info
            info = flow_info(beat, idx, self._session.beats)
            names = self._flow_target_labels()
            brief = self._flow_target_brief((info or {}).get('target'))
            self._panel.set_flow(info, names, brief=brief)
        except Exception as e:                  # noqa: BLE001 - 控制流条失败不拦卡片
            swallow('ui_qt.window_card._sync_flow_card', e)

    # ---------------- 跨章继承：上一章是谁（2026-10-31） ----------------
    def stage_entry_warmer(self):
        """后台"整本分析一次"的调度器（懒建；一个窗口一份）。"""
        warmer = getattr(self, '_stage_entry_warmer', None)
        if warmer is None:
            from storygraph.ui_qt.entry_warm import StageEntryWarmer
            warmer = StageEntryWarmer(self)
            warmer.ready.connect(self._on_stage_entry_ready)
            self._stage_entry_warmer = warmer
        return warmer

    def warm_stage_entry(self) -> str:
        """
        按需在后台算一次"本章第一拍之前台上是什么"（整本连续性分析一次）。

        为什么不在 `_show_index` 里做：翻一拍就分析一次整本 = 卡死。这里是
        **按项目缓存**的（指纹 = 所有 .rpy 的 mtime+size），只在载入章节时问一次。

        返回 `'cached'` / `'queued'` / `'running'` / `'skipped'`（诊断与测试用）。
        """
        session = getattr(self, '_session', None)
        root = getattr(session, 'project_root', None) if session else None
        if not root or not getattr(session, 'script_path', None):
            return 'skipped'
        try:
            status = self.stage_entry_warmer().request(root)
        except Exception as e:                    # noqa: BLE001 - 排不上队不拦载入
            swallow('ui_qt.window_card.warm_stage_entry', e)
            return 'skipped'
        if status == 'cached':
            # 缓存命中：主线程直接查（O(1)），不排队、不等
            try:
                from storygraph.app import stage_entry as SE
                self._apply_stage_entry(SE.cached_report(root))
            except Exception as e:                # noqa: BLE001
                swallow('ui_qt.window_card.warm_stage_entry.cached', e)
        return status

    def _on_stage_entry_ready(self, report):
        """后台算完了（主线程）：把入口状态塞进会话，按需重画派生面板。"""
        try:
            self._apply_stage_entry(report)
        except Exception as e:                    # noqa: BLE001 - 失败不拦卡片
            swallow('ui_qt.window_card._on_stage_entry_ready', e)

    def _apply_stage_entry(self, report) -> bool:
        """
        报告 → 会话的入口状态。返回"变了没有"（变了才重画）。

        ⚠ 只重画**派生面板**（当前舞台 / 尺寸格 / 小图），**不重载表单**：
        后台算两秒的工夫，用户可能已经在打字了。
        """
        session = getattr(self, '_session', None)
        if session is None:
            return False
        from storygraph.app import stage_entry as SE
        root = getattr(session, 'project_root', None)
        if not SE.matches_project(root, report):
            return False
        try:
            entry = SE.entry_for(root, getattr(session, 'script_path', None))
        except Exception as e:                    # noqa: BLE001 - 查不到就是没有
            swallow('ui_qt.window_card._apply_stage_entry.query', e)
            entry = None
        changed = session.set_stage_entry(entry)
        try:
            if entry is not None:
                self._log(f'[跨章] 本章第一拍之前：{entry.describe()}')
            else:
                self._log('[跨章] 判不出上一章：' + session.stage_entry_why_not())
        except Exception as e:                    # noqa: BLE001 - 台账失败不拦
            swallow('ui_qt.window_card._apply_stage_entry.log', e)
        if changed:
            try:
                self.refresh_derived_panels(getattr(self, '_loaded_idx', -1))
            except Exception as e:                # noqa: BLE001
                swallow('ui_qt.window_card._apply_stage_entry.refresh', e)
        return changed

    def shutdown_stage_entry(self):
        """关窗：停掉后台分析（正在跑的会跑完，结果只进内存缓存）。"""
        try:
            warmer = getattr(self, '_stage_entry_warmer', None)
            if warmer is not None:
                warmer.shutdown()
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.window_card.shutdown_stage_entry', e)

    def refresh_derived_panels(self, idx: int = None):
        """
        重算这一拍的**只读派生面板**：警告条 / 当前舞台 / 继承提示。

        为什么不只靠 `_show_index`（2026-10-05 用户实测报）：

        > 我已经保存该卡片了，可是当前舞台变化没有生效，并且舞台演出预览没有变化

        "当前舞台"是**载入那一刻**算好写上去的（`set_stage_digest`）。用户
        加了一张立绘、按了 💾 —— 内存里的拍已经变了，可那一块还是载入时的
        旧文案：「立绘：（台上没人）」。于是他合理地认为"保存没生效"，
        而去点演出预览也一样（那个窗口读的是刚打开时的帧……见 `open_preview`）。

        这些标签都是**只读**的（不碰输入控件），所以保存/提交之后重算一次
        是安全的，不会把用户正在编辑的内容冲掉。
        """
        if idx is None:
            idx = self._loaded_idx
        if not self._session.has(idx):
            return
        from storygraph.app import beats as _beats
        beat = self._session.beats[idx]
        try:
            # 「有台词却没有说话人」→ 导出后会变成旁白，在这里就说清楚。
            # 说话人**没登记**时同理要说（否则导出会丢掉它，用户以为工具坏了）。
            # **N3 同组冲突**也在这里说（`show lan happy sad` 这种 —— lint 实测不报）。
            # 文案整体由 `app.beats.card_note` 拼（前端只负责显示，不自己凑句子）。
            facts = {}
            try:
                facts = self._session.engine_facts()
            except Exception as e:            # noqa: BLE001
                swallow('ui_qt.window_card.refresh_derived_panels.facts', e)
            self._panel.set_note(_beats.card_note(
                beat, self._session.symbols, facts,
                project_root=getattr(self._session, 'project_root', None)))
            # 当前舞台（这一拍生效的状态）+ 继承上一拍的提示。
            # 两份文案都来自语义层（stage_snapshot / app.beats），前端不自己拼。
            self._panel.set_stage_digest(self._session.stage_digest_text(idx),
                                         self._session.inherited_hint(idx))
            # 「尺寸」一格（P1，2026-10-31）：背景 + 各 CG/UI 图层的尺寸与
            # 预设匹配（"铺满 / 适应 / 50%" → 项目里效果一样的 transform）。
            # 与上面两处同一时刻刷新 —— 换了 scene/show，尺寸行必须跟着变。
            self._panel.set_stage_size(self._session.stage_size_rows(idx))
            # 这一拍的**舞台图**（2026-10-27，用户："每一拍的舞台信息那里……
            # 给一张这一拍的图片"）。与上面那行文字同时刷新 —— 它渲染的是
            # 同一个 `StageFrame`（`app.stage_render`，与预览窗口同源），
            # 所以"字说台上没人、图上却站着人"这种分叉不会发生。
            self._panel.show_stage_frame(idx, self._session)
        except Exception as e:                # noqa: BLE001 - 派生面板刷不动不该拦住保存
            swallow('ui_qt.window_card.refresh_derived_panels', e)
    def _prefetch_neighbours(self, idx: int):
        """
        预取**相邻几拍**的立绘缩略图（用户 2026-10-05："卡牌界面图片加载慢"）。

        只做"把图解进缓存"，不碰界面：真正翻过去时 `ThumbLoader.request`
        命中内存缓存 → 0 延迟。范围很小（默认往后 2 拍 / 往前 1 拍），
        且预取本身有张数上限（见 `SpriteGridWidget.prefetch`），
        不会把线程池占满、反把当前这一拍挤慢。
        """
        panel = getattr(self, '_panel', None)
        if panel is None or not self._session.has(idx):
            return
        cards = []
        lo = max(0, int(idx) - self.PREFETCH_BEHIND)
        hi = int(idx) + self.PREFETCH_AHEAD
        for j in range(lo, hi + 1):
            if j == idx or not self._session.has(j):
                continue
            try:
                cards.extend(self._session.cards(j) or ())
            except Exception as e:                # noqa: BLE001 - 预取失败无所谓
                swallow('ui_qt.window_card._prefetch_neighbours', e)
                continue
        if cards:
            panel.prefetch_sprites(cards)
    def apply_current_beat(self):
        """表单 → 当前拍（语义在 `EditorSession.apply`，这里只递数据）。

        **顺带提交也是真提交**（2026-11-12 用户实测报）：

            > 我先改完了一张卡片，切换到下一张卡片，再回来，就发现已经
            > 无法按撤销修改的按钮了……改错了无法改回去

        翻页 / 预览 / 导出前都会走这里，把卡片上的字悄悄写进拍。从前它只调
        `apply` —— 那是"无脑写回"，**不记撤销快照**（只有 `save()` 会用
        `_last_saved` 记下"写之前的样子"）。于是"改了不点 💾 直接翻页"的人
        内容进了内存、撤销栈却是空的：翻回来按钮是灰的，改错了退不回去。
        点过 💾 的人不会遇到 —— 这就是"有时候"的来源。

        所以**确实改过**时改走 `save()`：同一份写回，外加把"保存前"压进
        撤销栈。判"改过没有"用面板自己的基线（`is_modified` +
        `cards_modified`），**不**拿快照指纹判 —— `apply` 第一次会把
        `op_order` / `stage_order` 归一化，那是 hydrate 的副作用、不是用户
        改的字；拿它当判据，光翻一遍本章就会给每一拍点亮"可撤销"。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            return None
        old_flow = (dict(self._panel._flow)
                    if getattr(self._panel, '_flow', None) else None)
        fields = self._panel.collect_fields()
        cards = self._panel.sprite_cards()
        # 只有**用户真改过**才值得记撤销：没改的卡片翻页提交是"无改动"的，
        # 走 save() 也行，但会白拍两份快照（大户章节里翻页会变慢）。
        pending = bool(self._panel.is_modified()
                       or self._panel.cards_modified())
        if pending:
            beat = self._session.save(
                idx, fields, cards=cards,
                advanced_open=self._panel.is_advanced_open(),
                # 载入时那一格的原文：会话靠它判断"用户手改了 Stage 没有"
                stage_loaded=self._panel.stage_loaded()).get('beat')
        else:
            beat = self._session.apply(
                idx, fields, cards,
                advanced_open=self._panel.is_advanced_open(),
                # 载入时那一格的原文：会话靠它判断"用户手改了 Stage 没有"
                stage_loaded=self._panel.stage_loaded())
        self._sync_kind_field(beat)
        # 翻页 / 预览 / 导出前的"顺带提交"也重新派生一次图边：
        # 否则卡片上改了目标、一翻页，图还指着旧目标。
        # 只有类型 / 目标 / from 真的变了才跑（参数改动不影响边）。
        if old_flow:
            from storygraph.story.flow import flow_info
            new_flow = flow_info(beat, self._loaded_idx, self._session.beats)
            changed = (not new_flow or any(
                str(old_flow.get(k) or '') != str((new_flow or {}).get(k) or '')
                for k in ('type', 'target', 'from_clause')))
            if changed:
                note = self._flow_edge_note(self._refresh_flow_edges())
                if note:
                    self._log(f'[控制流] {note}')
        if pending:
            # 提交完了 → 表单/网格重新成为"已提交状态"（与 💾 保存一致）。
            # 不重置的话，下一次顺带提交会再进 save() 分支空跑一遍。
            self._panel.reset_baseline()
        return beat

    def _sync_kind_field(self, beat):
        """
        把归一化后的「类型」推回面板（说话人一改，那一格立刻跟着变）。

        用户 2026-10-05："明明卡牌编辑器内我已经编写了说话人，kind 仍然
        显示 narrator"。数据层已经会重算（`app.beats.kind_from_speaker`），
        但控件里还是载入时的旧值 —— 那一格在骗人，而它恰好是用户唯一能
        看到"这句算台词还是旁白"的地方。
        """
        if beat is None:
            return
        try:
            self._panel.set_kind(getattr(beat, 'kind', ''))
        except Exception as e:                  # noqa: BLE001 - 界面同步失败不该拦住导出
            swallow('ui_qt.window_card._sync_kind_field', e)
    def _too_long_fields(self) -> list:
        """
        卡片上有哪些格超过 Excel 单元格上限（32767）？`[(字段, 字数), …]`

        **在输入端就拦**（2026-10-18，用户拍板"改回 excel 的单元格上限"）：
        等导出时才报错，用户已经在卡片里写了一万字、还以为是编辑器卡了。
        这里给的是同一份判据（`core.cell_limits`），只是更早、更近地说。
        """
        from storygraph.core.cell_limits import CELL_TEXT_LIMIT

        out = []
        for name, value in self._panel.collect_all_fields().items():
            if isinstance(value, str) and len(value) > CELL_TEXT_LIMIT:
                out.append((name, len(value)))
        return out

    def save_current_beat(self):
        """
        **保存这张卡片的修改**（卡片上的 💾 按钮 / Ctrl+S / 工具栏同名按钮）。

        走 `EditorSession.save`：它会顺手判"到底改了没有"，
        只有真改了才压撤销栈 —— 所以"翻页顺带提交"不会把撤销按钮点亮。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没什么可保存。')
            return None
        # 「背景」那一格也能写出一条 `scene`（从"没有背景"变成"有背景"）——
        # 那是换景，会清台。基线要在 `reset_baseline()` **之前**读
        # （保存成功后基线会被改成新值，再看就永远是"没变"）。
        #
        # ⚠ 判据是**这一拍自己有没有写过 `scene`**，不是"表单载入时那格是不是空的"
        # （2026-11-02）：表单现在会把台上沿用的背景填出来（`inherited`），
        # 拿它当基线的话，"这一拍原本没有背景、用户新写了一条"会被误判成
        # "换图" → 那句"带走了 N 个立绘"就再也不说了。
        scene_loaded = _scene_cell_image(
            getattr(self._session.beats[idx], 'scene', ''))
        scene_now = _scene_cell_image(self._panel.collect_fields().get('Scene'))
        flow_change = self._panel.flow_change()
        if flow_change:
            from storygraph.story.flow import is_valid_label
            if not is_valid_label(flow_change['target']):
                self._status('目标 label 不合法：只能字母/数字/下划线，'
                             '可用点号分段；没有保存。')
                return None
            if flow_change['call_to_jump']:
                if QMessageBox.question(
                        self, '把「调用」改成「跳转」？',
                        f'目标：{flow_change["target"]}\n\n'
                        f'call 会执行目标 label，遇到 return 回到这里继续；\n'
                        f'jump 不会返回 —— 这一章会在跳转处结束。\n\n'
                        f'确定改成 jump 吗？'
                ) != QMessageBox.Yes:
                    self._status('已取消：控制流类型仍是 call。')
                    return None
        over = self._too_long_fields()
        if over:
            from storygraph.core.cell_limits import CELL_TEXT_LIMIT

            names = '、'.join(f'「{n}」{k} 字' for n, k in over[:4])
            QMessageBox.warning(
                self, '这一格装不进 Excel',
                f'{names}\n\n'
                f'Excel 一个单元格最多 {CELL_TEXT_LIMIT} 字，超了会被静默截断，'
                f'所以工具不会替你存。\n'
                f'把这一句拆成几拍（比如按段落切成两张卡），再保存。')
            self._status(f'没有保存：{len(over)} 格超过单元格上限 '
                         f'{CELL_TEXT_LIMIT} 字')
            return None
        res = self._session.save(
            idx,
            self._panel.collect_fields(),
            cards=self._panel.sprite_cards(),
            advanced_open=self._panel.is_advanced_open(),
            stage_loaded=self._panel.stage_loaded())

        # 保存成功 = 表单内容成为新基线；列表那一行也要跟着变，
        # 否则左边还挂着旧台词，看起来像"没保存上"。
        self._sync_kind_field(res['beat'])
        # 控制流改了 → 用这一章的拍重新派生图边（不再手动改单条边）。
        edge_note = (self._flow_edge_note(self._refresh_flow_edges())
                     if flow_change else '')
        self._sync_flow_card(res['beat'], idx)
        self._panel.reset_baseline()
        self.refresh_list_row(idx)
        # 只读派生面板也要跟着变（当前舞台 / 继承提示 / 警告条）——
        # 用户 2026-10-05："我已经保存该卡片了，可是当前舞台变化没有生效"。
        self.refresh_derived_panels(idx)
        self._sync_undo_button()
        # 保存成功 = 内存比磁盘新（写入走「⤓ 导出 .rpy」）
        self._sync_export_dirty()

        if res['changed']:
            # 这一保存**新写了一条 scene**（原来没有背景）→ 把代价点名。
            # 与"已保存"合成**一条**状态栏消息：两条会互相盖掉，
            # 而被盖掉的恰好是"人已经没了"那句。
            warn = ''
            if scene_now and not scene_loaded:
                warn = note_scene_added(self, idx, scene_now) or ''
            # **工具替你改的**（2026-11-08：两次点 ✕ 写了重复的 hide，
            # 后一次并进前一次、把多余那条摘掉）—— 明细进窗口日志，
            # 状态栏只留一句短的（Toast 只有一行，长了会被裁在句子中间）。
            for note in (res.get('notes') or ()):
                self._log(f'[退场] {note}')
            extra = (f'（顺手并掉了 {len(res["notes"])} 处重复退场，明细见日志）'
                     if res.get('notes') else '')
            self._status(f'✔ 已保存到这一拍（{res["display_name"]}）。'
                         + (f'{edge_note}；' if edge_note else '')
                         + f'改动在内存里，点「⤓ 导出 .rpy」时才落盘。'
                         + (f' {warn}' if warn else '')
                         + extra)
            if flow_change:
                self._log(
                    f'[控制流] {flow_change["old"]["type"]} '
                    f'{flow_change["old"]["target"]} → '
                    f'{flow_change["type"]} {flow_change["target"]}'
                    + (f'；{edge_note}' if edge_note else '；图边无需改动'))
        else:
            self._status(f'这一拍没有改动，不需要保存（{res["display_name"]}）。')
        return res['beat']
    def undo_current_beat(self):
        """
        **撤销这张卡片的修改** —— 恢复成上一次保存时的样子。

        只影响这一拍（不碰别的卡片）。撤销不可用时**明说**，
        不要静默什么都不做 —— 用户按了按钮却"没反应"最难排查。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没有可撤销的修改。')
            return
        if not self._session.has_undo(idx):
            # ⚠ **刻意不退到结构撤销**（2026-10-05 定稿）：结构撤销只走
            # 「撤销结构操作」那颗按钮 —— Ctrl+Z 按一下就把卡片删掉/放回来
            # 太意外了（golden 快照第 56 步当场抓到：连按撤销会凭空多一张卡）。
            self._status('这一拍没有可撤销的修改'
                         '（撤销只能回到"上一次保存"之前）。')
            return
        self._session.undo(idx)
        # 数据变了 → 必须重新载入表单与立绘卡（含 chip 与缩略图）
        self._show_index(idx)
        # 撤销可能把 `call A` 改回 `call B`：图边也必须跟着派生回去，
        # 否则卡片显示 B、图上还连着 A。
        self._refresh_flow_edges()
        self.refresh_list_row(idx)
        self._sync_undo_button()
        self._sync_export_dirty()
        self._status('↩ 已撤销这一拍的修改，恢复成上一次保存时的样子。')
    def _sync_undo_button(self):
        """撤销按钮的可用性跟着会话的撤销栈走。"""
        try:
            self._panel.set_undo_enabled(
                self._session.has_undo(self._loaded_idx))
        except Exception:                       # noqa: BLE001
            swallow('main_window._sync_undo_button')
    def _update_nav(self):
        n = len(self._session)
        i = self._loaded_idx
        # 翻拍按钮同样只在卡片页可用：它们在结构页上没有意义
        # （键盘那条路同样由 `eventFilter` 判页号）。
        on_card = self._page == self.PAGE_CARD
        if hasattr(self, '_prev_action'):
            self._prev_action.setEnabled(on_card and i > 0)
        if hasattr(self, '_next_action'):
            self._next_action.setEnabled(on_card and 0 <= i < n - 1)
        if hasattr(self, '_pos'):
            self._pos.setText(f'{i + 1 if i >= 0 else 0} / {n}')
        self._update_crumb()
    def _sync_nav(self):
        if hasattr(self, '_prev_action'):
            self._update_nav()
    def _on_select(self, row: int):
        """
        列表选中变了：按**行里记的拍号**切拍（不是行号）。

        行号只在"没有分组标题"时才等于拍号；多 label 的列表里标题行
        会插进来，用行号当拍号会切错拍（甚至切到 -1）。
        """
        item = self._list.item(row) if row is not None and row >= 0 else None
        if item is None:
            return
        data = item.data(Qt.UserRole)
        try:
            idx = int(data)
        except (TypeError, ValueError):
            return
        if idx < 0:
            return                      # 分组标题：点击由 `_on_list_item_clicked` 处理
        if idx != self._loaded_idx:
            self.apply_current_beat()
            self._show_index(idx)
    def next_beat(self):
        """
        前进一拍（先提交当前拍，再翻页）。

        **越界必须有守卫**：第一版直接 `_show_index(self._loaded_idx + 1)`，
        走到最后一拍会 `_show_index(n)` → 内部把 `_idx` 设成 `-1`。
        后果不是"没反应"，而是**静默丢改动**：卡片上还显示着最后一拍、
        用户改完按「💾 保存这一拍」，`apply_current_beat` 一看
        `has(-1)` 是假就什么都不做 —— 用户以为存了。
        （tkinter 版的 `_update_nav_state` 就是干这个的，搬过来时漏了。）
        """
        i = self._loaded_idx
        if not self._session.has(i):
            return
        self.apply_current_beat()
        if i < len(self._session) - 1:
            self._show_index(i + 1)
    def prev_beat(self):
        """后退一拍（同样先提交、同样不许越界）。"""
        i = self._loaded_idx
        if not self._session.has(i):
            return
        self.apply_current_beat()
        if i > 0:
            self._show_index(i - 1)
    def _fill_speaker(self, item):
        # 列表上显示的是人话（「大爷」），`UserRole` 里才是 tag（`DY`）
        cid = ''
        if item is not None:
            data = item.data(Qt.UserRole)
            cid = str(data) if isinstance(data, str) and data else item.text()
        if not cid or not self._session.has(self._loaded_idx):
            return
        self._session.set_speaker(self._loaded_idx, cid)
        self._panel.set_speaker(cid)
        # `set_speaker` 是**绕过 `apply()`** 直接改拍的（左栏双击填角色），
        # 会话那边已经补了脏标记，这里只要把状态栏叫醒。
        self._sync_export_dirty()
        shown = self._character_labels().get(cid, cid)
        self._status(f'已把「{shown}」填进这一拍的说话人。')
