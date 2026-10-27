"""
`storygraph.ui_qt.window_nav` —— 顶部「模式条」+ 左侧「章节导航」+ 右栏属性表。
================================================================================

**为什么新开一块（2026-10-23）**
------------------------------
用户拿参考界面来对照，那一版的骨架是三件事：

    ┌ 模式标签（剧本 / 资产 / 叙事地图 / …）──────────────────────────┐
    │ 章节树 + 搜索 │ 正文（行号 + 类型牌）│ 检查器（属性 / 预览）        │
    └──────────────────────────────────────────────────────────────┘

我们原来的窗口只有"页"没有"模式条"，左栏随页消失，右栏一上来就是表单，
"我在哪一章、第几拍、源在第几行"没有任何地方写。这一块补的正是这三处
**骨架**，而编辑语义仍然一行都不在这里（载入 / 导出 / 增删卡片都在
`EditorSession` 与各自的 mixin 里）。

三个入口的分工
--------------
· `_build_mode_bar()`  顶部两格「剧情结构 / 卡片编辑」—— 与 `_set_page` 双向同步；
  同一条模式条上还挂着那个可整块删掉的 ⭐ 徽标（`ui_qt/github_star.py`）；
· `_build_nav_dock()`  左侧 `ChapterNavigator`（QDockWidget，不属于任何一页）；
· `_sync_inspector()`  右栏 `PropertyStrip`：文件 / label / 第几拍 / 类型 / 源行；
· `_toggle_location()` 右栏「定位」的折叠（**默认收起**，把高度留给卡片）。

**点一下就换章是错的**：这一栏紧贴拍列表，手一滑就会重读磁盘。所以导航里
打开只走双击 / 回车；而换章之前会**先把当前表单落进内存、脏就立刻抄一份
快照** —— 切走之后还能从「🕘 上次没导出的改动」里捡回来（不是"问一句要不要
保存"：那会在每次翻章时弹窗，用户三天就会点"否"）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDockWidget, QHBoxLayout, QLabel, QSizePolicy,
                               QTabBar, QToolBar, QWidget)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt.elide import ElidedLabel
from storygraph.ui_qt.navigator import ChapterNavigator

#: 拍的 `kind` → 属性表里那一行"类型"的字。
KIND_TEXT = {
    'say': '对白',
    'narration': '旁白',
    'flow': '控制流',
    'menu': '分支',
    'menu_option': '选项',
    'extend': '接续',
}


class WindowNavMixin:
    """
    顶部模式条 + 左侧章节导航 + 右栏属性表。

    **成员不许与其它 mixin 同名**（同名会让子类那份静默赢，
    `tests/test_main_window_mixins.py` 守着）。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = (
        'PAGE_CARD', 'PAGE_GRAPH', '_loaded_idx', '_log', '_project_root',
        '_session', '_set_page', '_status', 'apply_current_beat',
        'autosave_now', 'graph', 'import_chapter_files', 'load_script',
        '_show_index', '_swallow_mark', '_report_swallowed',
        # 右栏「定位」的折叠（2026-10-27）：三件都造在 `_build_card_page` 里
        '_inspector', '_loc_btn', '_loc_open',
    )

    # ------------------------------------------------------------------
    #  顶部：模式条
    # ------------------------------------------------------------------

    def _build_mode_bar(self):
        """
        最上面一行：「剧情结构 / 卡片编辑」两个模式标签。

        为什么要有它：切页原来只有工具栏里的「✎ 编辑这一章的卡片」与
        「← 返回剧情结构」两个按钮，夹在二十几个功能里；用户给的参考界面
        把"现在在哪个模式"单独摆在最上面一行 —— 一眼知道自己在哪。
        """
        tb = QToolBar('模式')
        tb.setObjectName('modeBar')
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)
        # **换行**：模式条自己占一行（参考界面第一行只有模式）。
        # 不换的话它和这一页的十几个动作挤在同一行里，模式标签会被挤到
        # 最左边、后面一长串按钮 —— 2026-10-23 第一版截图里就是这样。
        self.addToolBarBreak()
        self._tb_modes = tb

        tabs = QTabBar()
        tabs.setExpanding(False)
        tabs.setDrawBase(False)
        tabs.setToolTip('两页之间切换：结构页看全貌，卡片页改台词')
        tabs.addTab('🗺 剧情结构')
        tabs.addTab('✎ 卡片编辑')
        tabs.currentChanged.connect(self._on_mode_tab)
        self._mode_tabs = tabs
        tb.addWidget(tabs)

        # —— ⭐ GitHub 快速 star（**独立件：这一块可以整段删掉**）——
        #
        # 用户 2026-10-27 第一次要的是"顺着 GitHub 一键标星"，而且明说要一个
        # **能整块取下来**的独立件；同一天又纠正了位置：
        #
        #   > 还有，github的star我不是叫你放卡片编辑的旁边吗？你应当放第四张图
        #   > 这里，卡片编辑右边！放错位置啦！！！
        #
        # 所以它挂在**模式条**上、紧挨「✎ 卡片编辑」标签右边。为什么不放在
        # 卡片页右栏（第一版的位置）：那是编辑区，用户要求"右边这一栏几乎
        # 全留给卡片"；而这个徽标属于"这个工具本身"，挂在两页都在的那一行
        # 才合理。删掉下面这个 try 块 + `ui_qt/github_star.py` = 界面完全复原。
        try:
            from storygraph.ui_qt.github_star import attach_star_badge
            attach_star_badge(self, tb)
        except Exception as e:                 # noqa: BLE001 - 挂不上也只是少个徽标
            swallow('ui_qt.window_nav.star_badge', e)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        # 右边那句「文件 · label · 第 i 拍」：**必须能被压窄**。
        # 普通 QLabel 的最小宽度 = 整句宽度，1400px 窗口上它会把模式条
        # 顶到窗口外面（2026-10-23 用户截图：右边那句被裁在屏幕边缘）。
        # 这里包一个 Expanding 的壳给它"剩下的宽度"，文字超了画 `…`，
        # 全文在 tooltip（`_sync_nav_current` 会把它换成完整路径）。
        holder = QWidget()
        holder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        h = QHBoxLayout(holder)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        info = ElidedLabel('', mode=Qt.ElideRight, role='caption')
        info.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(info, 1)
        tb.addWidget(holder)
        self._mode_info = info

        # 「▥ 章节导航」开关**不在这里**（2026-10-26 用户："章节导航应摆到
        # 最左边"）：它改到两页工具栏的**第一个**动作（见
        # `main_window._build_toolbars`），这样从左到右就是
        # 章节导航 → 打开项目 → 自定义语句 → AI 助手 → 导入 → 导出 → 刷新 → 清理。
        # 这个类只保留「模式条」本身（两页标签 + 右边那句位置信息）。

    def _on_mode_tab(self, index: int):
        """点模式标签 → 切页（`_set_page` 会再同步回来，用 blockSignals 防环）。"""
        self._set_page(index)

    def _toggle_nav_dock(self, visible: bool):
        dock = getattr(self, '_nav_dock', None)
        if dock is not None:
            dock.setVisible(bool(visible))

    # ------------------------------------------------------------------
    #  左侧：章节导航
    # ------------------------------------------------------------------

    def _build_nav_dock(self):
        """
        左侧「章节导航」。**做成 dock 而不是某一页的一栏**：

        参考界面里那一栏在任何模式下都在；我们的"页"是替换中央控件，
        如果把导航放进页里，切页就看不见了 —— 而它最大的用处恰恰是
        "在卡片页时也想跳去别的章"。
        """
        nav = ChapterNavigator()
        nav.setMinimumWidth(200)
        self._nav = nav

        dock = QDockWidget('章节', self)
        dock.setObjectName('chapterNavDock')
        dock.setWidget(nav)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetMovable
                         | QDockWidget.DockWidgetFloatable
                         | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self._nav_dock = dock

        nav.open_requested.connect(self.open_chapter_at)
        nav.refresh_requested.connect(self.refresh_chapter_nav)
        nav.import_requested.connect(self.import_chapter_paths)
        # 面板自己关掉（右上角 ×）时，工具栏那个勾选框要跟着取消勾选；
        # 否则"勾着但面板没了"，再点一次反而没反应。
        dock.visibilityChanged.connect(self._on_nav_dock_visible)
        # 关窗 / 浮动都不该让"当前在哪一章"丢失：面板自己存着状态
        if self._project_root:
            nav.set_project_root(self._project_root)
        self._sync_nav_title()

    def _on_nav_dock_visible(self, _visible: bool):
        act = getattr(self, '_nav_action', None)
        dock = getattr(self, '_nav_dock', None)
        if act is None or dock is None:
            return
        # ⚠ 用 `isHidden()` 而不是信号的 `visible` 参数：主窗口还没 show 时
        #    dock 也报 False，那时把勾去掉等于"启动就莫名其妙没勾"。
        try:
            want = not dock.isHidden()
            if act.isChecked() != want:
                act.blockSignals(True)
                act.setChecked(want)
                act.blockSignals(False)
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.window_nav.nav_visible', e)

    # ------------------------------------------------------------------
    #  导航的三个动作
    # ------------------------------------------------------------------

    def refresh_chapter_nav(self):
        """重扫项目并说清结果（**不抛**：扫不出来也在状态栏说出来）。"""
        nav = getattr(self, '_nav', None)
        if nav is None:
            return None
        idx = nav.refresh()
        self._sync_nav_marks()
        self._sync_nav_current()
        self._sync_nav_title()
        if idx is None or not idx:
            why = ''
            try:
                errors = getattr(idx, 'errors', ()) or ()
                if errors:
                    why = f'（{errors[0][0] or "项目"}：{errors[0][1]}）'
            except Exception as e:                # noqa: BLE001 - 只是措辞
                swallow('ui_qt.window_nav.refresh.errors', e)
            if not getattr(self, '_project_root', None):
                # 没打开项目时"没找到 .rpy"是**假话**：还没开始找。
                msg = ('章节导航：还没打开项目 —— 用「📁 打开项目」选项目根，'
                       '左边才会列出 game/ 下的章节')
            else:
                msg = (f'章节导航：没找到带 label 的 .rpy{why} —— '
                       f'章要写成 `label 名字:` 才列得出来')
        else:
            msg = (f'章节导航：{idx.file_count()} 个文件 · '
                   f'{idx.label_count()} 章')
            if idx.skipped_without_label():
                msg += f'（另有 {idx.skipped_without_label()} 个文件没有 label，未列出）'
        self._status(msg)
        self._log(f'[导航] {msg}')
        return idx

    def open_chapter_at(self, path, label=''):
        """
        在卡片界面打开某一章（导航里双击 / 图上的块也走这条路）。

        三种情况：
          · **就是手上这一章** → 只跳拍（不重读磁盘 —— 重读会把没导出的改动
            冲掉）；
          · 换章且当前有未导出改动 → 先 `autosave_now()` 抄一份再换
            （"丢掉也捡得回来"，不弹窗打断）；
          · 其余 → 正常 `load_script`。
        """
        target = str(path or '').strip()
        if not target:
            return
        session = self._session
        current = str(getattr(session, 'script_path', '') or '')
        same = bool(current) and _same_file(current, target)
        mark = self._swallow_mark()
        try:
            self.apply_current_beat()         # 先把卡片上的字落进内存
        except Exception as e:                # noqa: BLE001 - 提交失败不该拦跳转
            swallow('ui_qt.window_nav.open.apply', e)
        try:
            if same:
                idx = session.index_of_label(label) if label else 0
                if idx is None or idx < 0:
                    self._status(f'这一章里没有 label「{label}」')
                    return
                self._set_page(self.PAGE_CARD)
                self._show_index(int(idx))
                self._status(f'已跳到 {Path(target).name}'
                             + (f' 的「{label}」' if label else ''))
                return
            # —— 换章：先保住上一章 ——
            try:
                dirty = bool(session.has_unsaved_edits())
            except Exception as e:            # noqa: BLE001
                swallow('ui_qt.window_nav.open.dirty', e)
                dirty = False
            if dirty:
                res = self.autosave_now() or {}
                if res.get('ok'):
                    self._log('[导航] 上一章有未导出的改动 → 已抄一份快照；'
                              '要找回它：「🧰 更多工具 → 🕘 上次没导出的改动…」')
                else:
                    self._log(f'[导航] ⚠ 上一章有未导出的改动，但快照没抄成：'
                              f'{res.get("reason") or "未知原因"}')
            self.load_script(Path(target), goto_card=True, label=label or None)
        finally:
            self._report_swallowed(mark)

    def import_chapter_paths(self, paths):
        """导航右键「导入到剧情图」→ 复用「➕ 导入章节」那条链路。"""
        files = [str(p) for p in (paths or ()) if str(p)]
        if not files:
            return None
        res = self.import_chapter_files(files)
        self._sync_nav_marks()
        return res

    # ------------------------------------------------------------------
    #  同步（图 → 导航；会话 → 导航 / 属性表）
    # ------------------------------------------------------------------

    def _sync_nav_title(self):
        """dock 标题 = `章节 · 1 个文件 · 4 章`（数量不另占一行）。"""
        dock = getattr(self, '_nav_dock', None)
        nav = getattr(self, '_nav', None)
        if dock is None or nav is None:
            return
        summary = ''
        try:
            summary = nav.summary_text()
        except Exception as e:                    # noqa: BLE001 - 标题而已
            swallow('ui_qt.window_nav.title', e)
        dock.setWindowTitle(f'章节 · {summary}' if summary else '章节')

    def _sync_nav_marks(self):
        """图上已有的 label 在导航里画 `●` —— 一眼看出"图和项目的差"。"""
        nav = getattr(self, '_nav', None)
        if nav is None:
            return
        labels = set()
        try:
            nodes = getattr(getattr(self, 'graph', None), 'nodes', {}) or {}
            for node in nodes.values():
                name = str(getattr(node, 'label_name', '') or '')
                if name:
                    labels.add(name)
        except Exception as e:                    # noqa: BLE001 - 标点而已
            swallow('ui_qt.window_nav.marks', e)
        nav.set_imported(labels)

    def _sync_nav_current(self):
        """现在打开的是哪一章 → 导航高亮 + 顶部那条信息。"""
        nav = getattr(self, '_nav', None)
        session = self._session
        path = str(getattr(session, 'script_path', '') or '')
        label = str(getattr(session, 'focus_label', '') or '')
        # ⚠ 别写 `int(getattr(...) or -1)`：**第 0 拍是 0**，`0 or -1` 会把它
        #   当成"没载入"（第十阶段在协作锁那儿踩过同一个坑）。
        try:
            idx = int(getattr(self, '_loaded_idx', -1))
        except (TypeError, ValueError) as e:
            swallow('ui_qt.window_nav.current.index', e)
            idx = -1
        if nav is not None:
            nav.set_current(path, label)
        info = getattr(self, '_mode_info', None)
        if info is None:
            return
        if not path:
            info.setText('还没有载入章节 —— 左边双击一章，或「➕ 导入章节」')
            return
        bits = [Path(path).name]
        if label:
            bits.append(label)
        if idx >= 0:
            bits.append(f'第 {idx + 1} 拍')
        info.setText(' · '.join(bits))
        info.setToolTip(path)

    # ------------------------------------------------------------------
    #  右栏：属性表
    # ------------------------------------------------------------------

    def _toggle_location(self):
        """
        展开 / 收起右栏那块「定位」（默认收起 —— 2026-10-27 用户点名）。

        > 还有就是这个定位能不能折叠一下（卡片界面才是最主要的！！！定位默认
        > 折叠起来，卡片界面必须留几乎右边全屏）……留给我的可编辑窗口太小了！

        收起时**不删内容**：`PropertyStrip` 一直在，`_sync_inspector` 照旧在
        翻拍 / 换章时往里填，展开就是对的 —— 这样"折叠"不会变成"看不到真话"。
        """
        try:
            self._loc_open = not bool(getattr(self, '_loc_open', False))
            self._inspector.setVisible(self._loc_open)
            self._loc_btn.setText(('▾' if self._loc_open else '▸')
                                  + ' 定位（这一拍在文件里的位置）')
        except Exception as e:                    # noqa: BLE001 - 折叠失败不该拦界面
            swallow('ui_qt.window_nav.toggle_location', e)

    def _sync_inspector(self, idx=None):
        """
        右栏顶上那块属性表（参考界面的"检查器"）。

        只显示**这一拍的定位信息**：文件 / label / 第几拍 / 类型 / 源行。
        这些恰好是以前一个都没显示过的 —— 想回源文件对照只能去数拍号。
        """
        strip = getattr(self, '_inspector', None)
        if strip is None:
            return
        session = self._session
        if idx is None:
            # 同上：**第 0 拍是 0**，不能用 `or -1` 兜底。
            try:
                idx = int(getattr(self, '_loaded_idx', -1))
            except (TypeError, ValueError) as e:
                swallow('ui_qt.window_nav.inspector.index', e)
                idx = -1
        if not session.has(idx):
            strip.set_empty('还没载入章节')
            return
        beat = session.beats[idx]
        path = str(getattr(session, 'script_path', '') or '')
        kind = str(getattr(beat, 'kind', '') or '')
        rows = [
            ('文件', Path(path).name if path else ''),
            ('label', str(getattr(beat, 'label', '') or '')),
            ('这一拍', f'{int(idx) + 1} / {len(session)}'),
            ('类型', self._kind_text(beat)),
        ]
        first = int(getattr(beat, 'src_first', 0) or 0)
        last = int(getattr(beat, 'src_last', 0) or 0)
        if first > 0:
            span = f'.rpy 第 {first} 行'
            if last > first:
                span += f'–{last}'
            rows.append(('源位置', span))
        elif kind:
            rows.append(('源位置', '还没对到源文件（新加的拍 / 已重写）'))
        who = str(getattr(beat, 'speaker', '') or '')
        if who:
            rows.append(('说话人', who))
        try:
            names = session.label_names()
            if len(names) > 1:
                rows.append(('这一章', f'{len(names)} 个 label'))
        except Exception as e:                    # noqa: BLE001 - 少一行而已
            swallow('ui_qt.window_nav.inspector.labels', e)
        strip.set_rows(rows)

    @staticmethod
    def _kind_text(beat) -> str:
        """拍的 kind → 人话（语句拍说清是哪个语句）。"""
        kind = str(getattr(beat, 'kind', '') or '')
        statement = str(getattr(beat, 'spec_name', '') or '').strip()
        if kind == 'say' and not statement:
            return '对白'
        if statement:
            return f'语句 · {statement}'
        return KIND_TEXT.get(kind, kind or '—')


def _same_file(a, b) -> bool:
    """两个路径是不是同一份文件（大小写 / 相对路径 / `..` 都不许骗过去）。"""
    try:
        same = Path(str(a)).resolve() == Path(str(b)).resolve()
    except (OSError, RuntimeError) as e:
        # 路径解析失败（坏盘符 / 符号链接成环）→ 退回纯字符串比较，
        # 但**留痕**：这类"比较失败"会让人以为"不是同一个文件"。
        swallow('ui_qt.window_nav.same_file', e)
        same = False
    if same:
        return True
    return str(a).replace('\\', '/').lower() == str(b).replace('\\', '/').lower()


__all__ = ['WindowNavMixin', 'KIND_TEXT']
