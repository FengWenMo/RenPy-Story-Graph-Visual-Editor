"""
storygraph.ui_qt.window_nodes.py
================================
`MainWindowQt` 的**模块 ↔ 卡片、画布上的增删连边与右键菜单。**

为什么拆（P3-14，2026-10-05）
-----------------------------
`MainWindowQt` 原本是 3452 行 / 120 方法的上帝对象 —— `EditorSession`
（已拆成 5 块）的另一半。它比那边更难拆，因为：

  · 它是 **UI 层**：行为测试薄得多，而且要真起 Qt（`test_ui_qt.py`）；
  · 方法之间靠 **Qt 信号**耦合，不像会话那样能按"主语是什么"干净切开。

所以顺序是**先补网、再拆**：`tests/test_main_window_snapshot.py`
（63 步操作序列 golden）先把外部可观察行为钉住，
拆分期间它必须一直绿。

**做成 mixin 而不是独立对象**：调用方（菜单、工具栏、信号、
`open_editor`）一行都不用改 —— 这是**搬家**不是改契约。

与主类的隐式依赖
----------------
见每个类的 `REQUIRES`：那是**实测**（静态扫 `self.*`）出来的，
由 `tests/test_main_window_mixins.py` 守着。
"""
from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.ui_qt.qt_args import drop_qt_bool
from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                               QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSplitter, QStackedWidget, QToolBar,
                               QVBoxLayout, QWidget)

from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.status import Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader


__all__ = ['NodeEditMixin']


def _skeleton_block(label: str, operations: str = '') -> str:
    """
    新 label 的最小骨架（`label 名:` + 可选变量操作 + `return`）。

    `operations` 是对话框里那一格（一行一条，`$` 可写可不写）。它从前只进
    内存节点、不进骨架文件 —— 用户填了变量，打开剧本却看不到（P2-2）。
    现在**同一份文本**既进节点也进文件，两边不会再对不上。
    """
    lines = [f'label {label}:']
    for raw in str(operations or '').splitlines():
        text = str(raw or '').strip()
        if not text:
            continue
        if text.startswith('$'):
            text = text[1:].strip()
        if not text:
            continue
        # `#` 注释照原样缩进；其余按剧情变量写成 `$ ...`。
        lines.append(f'    {text}' if text.startswith('#') else f'    $ {text}')
    lines.append('    return')
    return '\n'.join(lines) + '\n'


def _default_chapter_dir(project_root, known_files=()) -> str:
    """
    新章节默认写到哪：**跟随项目里已有章节所在的目录**（P2-3）。

    从前固定 `<项目>/game/<名字>.rpy`，而用户的实际章节都在
    `game/script/` 下 —— 新文件散落在 `game/` 根目录，Ren'Py 虽然也会读，
    但作者按目录找章节时就会"少一章"。取已知章节里**最常见**的那个目录；
    一个文件都没有时才退回 `game/`。
    """
    root = Path(project_root) if project_root else None
    dirs = []
    for fp in (known_files or ()):
        try:
            p = Path(str(fp))
            if p.exists():
                dirs.append(p.parent)
        except (OSError, ValueError):
            continue
    if not dirs and root is not None:
        try:
            from storygraph.parsing.discover import discover_scripts
            dirs = [p.parent for p in discover_scripts(root)]
        except Exception as e:                    # noqa: BLE001 - 只是默认值
            swallow('ui_qt.window_nodes.default_dir', e)
    if dirs:
        from collections import Counter
        # `most_common` 在并列时按首次出现；再按路径长度取深的那个，
        # `game/script` 比 `game` 更像"章节目录"。
        counts = Counter(str(d) for d in dirs)
        best = max(counts, key=lambda d: (counts[d], len(Path(d).parts)))
        return best
    if root is None:
        return ''
    return str(root / 'game')


def _write_new_chapter(view, path, label: str, operations: str = '') -> str:
    """
    给"新建章节"落一个最小骨架（`label 名:` + `return`），返回路径（失败返回空串）。

    三种情形（2026-10-21，用户实测报的"无源文件"和"一个文件里写第二个 label"）：

      · **文件不存在** → 新建它；
      · **文件已存在、里面没有同名 label** → **追加**一段
        `\n\nlabel 名:\n    return\n`（用户原话："我想在一个 rpy 文件里写出
        第二个 label 该怎么操作？" —— 一个文件放多章在 Ren'Py 里完全合法，
        工具以前只会建新文件，把这条路堵死了）；
      · **里面有同名 label** → 一个字都不写（重名 label 会让游戏起不来）。

    追加时按文件自己的**载体风格**（BOM / CRLF）铺回去，不改已有任何一个字节。
    """
    if not path or not str(label or '').strip():
        return ''
    p = Path(path)
    try:
        from storygraph.story.paths import write_text_atomic
        from storygraph.story.source_style import detect as _detect
        from storygraph.story.source_style import read_text as _read

        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(p, _skeleton_block(label, operations))
            return str(p)

        text, style = _read(p)
        if _has_label(text, label):
            try:
                view._status(f'{p.name} 里已经有 label {label} 了 —— 换个章节名。')
            except Exception as e:            # noqa: BLE001 - 状态栏坏了不影响判断
                swallow('ui_qt.window_nodes.dup_label_status', e)
            return ''
        body = (text.rstrip('\n') + f"\n\n"
                + _skeleton_block(label, operations))
        write_text_atomic(p, _detect(p).apply(body))
        return str(p)
    except OSError as e:                      # 权限 / 磁盘：说出来，不静默
        try:
            view._status(f'章节建好了，但文件没写出来：{e}')
        except Exception as e2:               # noqa: BLE001 - 状态栏坏了也得往下走
            swallow('ui_qt.window_nodes.write_status', e2)
        return ''


def _has_label(text: str, label: str) -> bool:
    """这段源码里有没有 `label <名字>:`（含带参数 / 带 `(` 的写法）。"""
    import re as _re
    return bool(_re.search(
        rf'^label\s+{_re.escape(label)}\s*(?:\(|:)', text or '', _re.M))


def _beat_short_text(beat, limit: int = 22) -> str:
    """
    下拉框里"这是哪一拍"的一行短文。

    不追求完整（完整文本在卡片里），只求两件事：**认得出**、
    **不把下拉框撑爆**。控制流拍直接给 `call side` 这种原文。
    """
    from storygraph.story.flow import parse_flow

    flow = parse_flow(beat)
    if flow:
        text = str(flow.get('line') or '')
    else:
        header = str(getattr(beat, 'loop_header', '')
                     or getattr(beat, 'camera_header', '') or '').strip()
        speaker = str(getattr(beat, 'speaker', '') or '').strip()
        body = str(getattr(beat, 'text', '') or '').strip()
        if header:
            text = f'{header}:'
        elif str(getattr(beat, 'kind', '') or '') == 'menu':
            text = 'menu:'
        elif speaker or body:
            text = f'{speaker}：{body}' if speaker else body
        else:
            raw = str(getattr(beat, 'raw', '') or '').strip()
            text = raw.splitlines()[0] if raw else \
                str(getattr(beat, 'kind', '') or '（空拍）')
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _session_block_heads(session) -> set:
    """会话里哪些拍是块头（查询失败 = 没有块头，不拦接线）。"""
    fn = getattr(session, 'block_head_line_ids', None)
    if not callable(fn):
        return set()
    try:
        return {str(x) for x in (fn() or ())}
    except Exception as e:                        # noqa: BLE001 - 查询失败按无块头
        swallow('ui_qt.window_nodes.session_block_heads', e)
        return set()


def _block_heads_of(beats) -> set:
    """
    一批**没载入会话**的拍里，哪些是块头（`menu` / 循环 / 镜头 / 选项头）。

    只读解析出来的拍也要能挡住"插在块头后面"——判据与
    `EditorSession.block_head_line_ids` 同一份（都从 `ChapterTree` 来）。
    """
    try:
        from storygraph.story.chapter_tree import ChapterTree
        tree = ChapterTree.build(list(beats or ()))
    except Exception as e:                        # noqa: BLE001 - 建树失败按没有块头
        swallow('ui_qt.window_nodes.block_heads_of', e)
        return set()
    out = set()
    try:
        for node in tree.root.walk():
            header = getattr(node, 'header', None)
            if header is None or not getattr(node, 'children', None):
                continue
            line_id = str(getattr(header, 'line_id', '') or '')
            if line_id:
                out.add(line_id)
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.window_nodes.block_heads_of.walk', e)
    return out


def _beat_position_plan(beats, idx: int, block_heads=()) -> dict:
    """
    把线接在**第 `idx` 拍**后面（或 `return` 前面）行不行 —— 纯判断。

    这是 P1 "拍位置"选择器的**唯一判据**：下拉框里哪些项能选、
    `plan_wire_landing(position='beat', …)` 的结论，都走这一份，
    免得"下拉框能选、点了却写不进"。

    `block_heads` 是**有真身拍的块头**（`menu`/`loop`/`camera`/选项头）：
    插在它们后面会掉进块体，必须挡。`if` 没有独立拍，所以"if 前面那拍"
    照常能接 —— 那是顶层，不是块头。

    返回 `{ok, kind, after, reason}`；`kind='beat'` / `'bad_position'`。
    """
    bad = {'ok': False, 'kind': 'bad_position', 'after': True, 'reason': ''}
    if not (0 <= int(idx) < len(beats)):
        return {**bad, 'reason': '这一拍不在这章里了'}
    beat = beats[int(idx)]
    n = int(idx) + 1
    raw = str(getattr(beat, 'raw', '') or '').strip()
    kind = str(getattr(beat, 'kind', '') or '')
    nest = str(getattr(beat, 'nest_path', '') or '')
    line_id = str(getattr(beat, 'line_id', '') or '')

    if nest:
        return {**bad, 'reason': f'第 {n} 拍在块体里（{nest}），不能把线接在那里'}
    if line_id and line_id in {str(x) for x in (block_heads or ())}:
        return {**bad, 'reason': f'第 {n} 拍是块开头，不能接在它后面'}
    if raw == 'return' or raw.startswith('return '):
        return {'ok': True, 'kind': 'beat', 'after': False,
                'reason': f'插在第 {n} 拍（return）之前'}
    if (raw.startswith('jump ') or raw.startswith('call ')
            or kind in ('jump', 'call')):
        return {**bad,
                'reason': f'第 {n} 拍已经有去向（{raw or kind}），后面接不上'}
    if kind in ('menu', 'menu_option', 'loop', 'camera'):
        return {**bad, 'reason': f'第 {n} 拍是块开头，不能接在它后面'}

    return {'ok': True, 'kind': 'beat', 'after': True,
            'reason': f'接在第 {n} 拍之后'}


class NodeEditMixin:
    """
    模块 ↔ 卡片、画布上的增删连边与右键菜单。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('_beat_context', '_canvas', '_chapter_label', '_delete_line_note', '_log', '_open_edge_in_card', '_project_root', '_refresh_flow_edges', '_run_command', '_session', '_set_graph', '_status', 'arrange_by_file', 'check_logic', 'clear_graph', 'complete_related_chapters', 'edit_node_ops', 'graph', 'import_chapter_files', 'import_chapters', 'lift_python_assignments', 'load_script', 'open_node_in_excel', 'reload_imported_chapters', 'remove_chapter_from_graph', 'show_variable_workbench')

    def _selected_node(self):
        nid = self._canvas.selected_node_id()
        return (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
    def _source_info(self, node) -> dict:
        """
        这个节点**现在**属于哪种来源状态（2026-10-22 用户反馈）。

        从前只用一个 `file_path` 布尔值，把三种完全不同的情况混成
        「（无源文件）」：

          · `bound`   有路径且文件在 → 能打开卡片；
          · `found`   没有路径，但项目里能找到声明它的 `.rpy`
                      （别的章节 jump/call 过来的占位块）→ 可一键导入绑定；
          · `manual`  没有路径，项目里也找不到 → 手工节点，要新建/绑定；
          · `missing` 有路径，但磁盘上文件没了 → 要重新定位或新建。

        状态不同，右栏主按钮就该不同 —— 不能再出现"可点但必然报错"。
        """
        label = str(getattr(node, 'label_name', '') or '')
        fp = getattr(node, 'file_path', None)
        if fp and Path(str(fp)).exists():
            return {'kind': 'bound', 'label': label, 'file': str(fp)}
        if fp:
            return {'kind': 'missing', 'label': label, 'file': str(fp)}
        root = getattr(self, '_project_root', None)
        if root and label:
            try:
                from storygraph.parsing.discover import find_label_files
                found = find_label_files(root, [label])
            except Exception as e:                # noqa: BLE001 - 扫描失败按手工处理
                swallow('ui_qt.window_nodes.source_info', e)
                found = {}
            path = found.get(label)
            if path:
                return {'kind': 'found', 'label': label, 'file': str(path)}
        return {'kind': 'manual', 'label': label, 'file': ''}

    def _on_canvas_select(self, nid):
        """左键选中模块 → 右栏"当前模块"和**主按钮**跟着状态变。"""
        # 命令对象要读 `selected_node`（旧版视图接口），这里跟着同步
        self.selected_node = nid
        self.selected_edge = None
        node = (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
        info = self._source_info(node) if node is not None else {}
        name = getattr(node, 'label_name', '') if node else ''
        if node is None:
            self._chapter_label.setText('（在图上点一个模块）')
        elif info.get('kind') == 'bound':
            self._chapter_label.setText(
                f'{name}　·　{Path(str(info["file"])).name}')
        elif info.get('kind') == 'found':
            self._chapter_label.setText(
                f'{name}　·　未绑定（在 {Path(str(info["file"])).name} 里找到）')
        elif info.get('kind') == 'missing':
            self._chapter_label.setText(
                f'{name}　·　源文件丢失：{Path(str(info["file"])).name}')
        else:
            self._chapter_label.setText(f'{name}　·　手工节点，还没有源文件')

        btn = getattr(self, '_open_script_btn', None)
        if btn is not None:
            kind = info.get('kind')
            if kind == 'bound':
                btn.setText('✎ 打开本章剧本（改台词）')
                btn.setToolTip('进入卡片编辑器改这一章的台词（也可右键模块 → 改本章台词）')
                btn.setEnabled(True)
            elif kind == 'found':
                btn.setText('🔗 导入并绑定源文件')
                btn.setToolTip(
                    f'项目里的 {Path(str(info["file"])).name} 声明了「{name}」；'
                    f'点一下导入该文件并把节点绑上去')
                btn.setEnabled(True)
            elif kind in ('manual', 'missing'):
                btn.setText('🩹 给这个节点建/绑源文件')
                btn.setToolTip('还没有可编辑的 .rpy：可以新建一个，或绑定已有文件')
                btn.setEnabled(True)
            else:
                btn.setText('✎ 打开本章剧本（改台词）')
                btn.setEnabled(False)

        edit_btn = getattr(self, '_edit_vars_btn', None)
        if edit_btn is not None:
            edit_btn.setEnabled(node is not None)
        workbench = getattr(self, '_vars_workbench_btn', None)
        if workbench is not None:
            workbench.setEnabled(node is not None)

    def _same_chapter_file(self, a, b) -> bool:
        """两个路径是不是同一个 `.rpy`（Windows 下大小写不敏感）。"""
        try:
            pa = str(Path(str(a or '')).resolve())
            pb = str(Path(str(b or '')).resolve())
        except OSError:
            pa, pb = str(a or ''), str(b or '')
        return bool(pa) and bool(pb) and pa.lower() == pb.lower()

    def _ensure_chapter_loaded(self, path, label='') -> bool:
        """
        让这一章成为**当前会话** —— 写剧本 / 改那一行都要求它在会话里。

        换章前先把未导出的改动抄一份快照（`autosave_now`），不静默丢；
        载入用 `goto_card=False`，不把正在看剧情结构的人顶到卡片页。
        已经在会话里时直接返回，不重读磁盘（那会冲掉内存里的改动）。
        """
        session = getattr(self, '_session', None)
        if session is None:
            return False
        target = str(path or '')
        if not target:
            return False
        cur = str(getattr(session, 'script_path', '') or '')
        if cur and self._same_chapter_file(cur, target):
            return True
        try:
            dirty = bool(session.has_unsaved_edits())
        except Exception as e:                    # noqa: BLE001 - 判不出就按没改
            swallow('ui_qt.window_nodes.ensure_chapter.dirty', e)
            dirty = False
        if dirty:
            save = getattr(self, 'autosave_now', None)
            res = {}
            if callable(save):
                try:
                    res = save() or {}
                except Exception as e:            # noqa: BLE001 - 快照失败也要能继续
                    swallow('ui_qt.window_nodes.ensure_chapter.autosave', e)
                    res = {}
            log = getattr(self, '_log', None)
            if callable(log):
                if res.get('ok'):
                    log('[接线] 上一章有未导出的改动 → 已抄一份快照，再换章')
                else:
                    log(f'[接线] ⚠ 上一章有未导出的改动，但快照没抄成：'
                        f'{res.get("reason") or "未知原因"}')
        load = getattr(self, 'load_script', None)
        if not callable(load):
            return False
        load(target, goto_card=False, label=label or None)
        cur = str(getattr(session, 'script_path', '') or '')
        return bool(cur) and self._same_chapter_file(cur, target)

    def _source_beats(self, src) -> dict:
        """
        起点章的拍 —— **只读**，不切章、不同步界面。

        用户 2026-10-24 抓到的 bug：接线窗口的「插入位置」只认**当前打开**
        的那一章 —— 从 `chapter0_0` 拖（它是打开的）能选第 N 拍，从
        `chapter1_2` 拖（没打开）只剩章末。同一张窗口两种表现，不是功能。

        现在：会话里就是这一章 → 用会话的拍（尊重没导出的改动）；
        否则只读解析那个 `.rpy`。真正写回时才 `_ensure_chapter_loaded`。
        返回 `{ok, beats, path, loaded, reason}`。
        """
        empty = {'ok': False, 'beats': [], 'path': '', 'loaded': False,
                 'reason': ''}
        if src is None:
            return {**empty, 'reason': '起点模块不在图上'}
        label = str(getattr(src, 'label_name', '') or '')
        info = self._source_info(src)
        if info.get('kind') != 'bound':
            return {**empty,
                    'reason': (f'「{label}」还没绑定可读的源文件 —— '
                               f'先修源文件，再选具体哪一拍')}
        path = str(info.get('file') or '')
        session = getattr(self, '_session', None)
        cur = str(getattr(session, 'script_path', '') or '')
        if session is not None and cur and self._same_chapter_file(cur, path):
            beats = list(getattr(session, 'beats', None) or ())
            if beats:
                return {'ok': True, 'beats': beats, 'path': path,
                        'loaded': True, 'reason': ''}
        try:
            st = Path(path).stat()
            key = (path, int(st.st_mtime_ns), int(st.st_size))
        except OSError as e:
            return {**empty, 'path': path,
                    'reason': f'读不了 {Path(path).name}：{e}'}
        cache = getattr(self, '_source_beats_cache', None)
        if not isinstance(cache, dict):
            cache = {}
            self._source_beats_cache = cache
        beats = cache.get(key)
        if beats is None:
            try:
                from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                             RenPyParser)
                from storygraph.story.beat_build import beats_from_parse_result
                pr = ParseResultBuilder().build(RenPyParser().parse(path))
                beats = list(beats_from_parse_result(pr))
            except Exception as e:                # noqa: BLE001 - 解析失败要说原因
                swallow('ui_qt.window_nodes.source_beats.parse', e)
                return {**empty, 'path': path,
                        'reason': f'解析 {Path(path).name} 失败：{e}'}
            if len(cache) >= 8:
                cache.clear()
            cache[key] = beats
        return {'ok': True, 'beats': beats, 'path': path,
                'loaded': False, 'reason': ''}

    def edge_position_choices(self, from_id) -> dict:
        """
        「拍位置」下拉框的内容：这一章**顶层**的每一拍，以及能不能接在它后面。

        返回 `{ok, items, reason}`，`items` 每项：
        `{line_id, index, number, total, text, selectable, note}`。

        为什么把"不能选"的也列出来（置灰）：用户看到第 7 拍是 `jump`，
        比只看到 1~6、不知道 7 去哪了更好懂。判据与
        `plan_wire_landing(position='beat')` 共用 `_beat_position_plan`，
        不会出现"能选但写不进"。
        """
        control_path = ''
        try:
            from storygraph.story.control_route import find_control_file
            ctrl = find_control_file(getattr(self, '_project_root', None))
            control_path = str(ctrl) if ctrl is not None else ''
        except Exception as e:                    # noqa: BLE001 - 只是标签，不拦接线
            swallow('ui_qt.window_nodes.edge_position_choices.control', e)
        empty = {'ok': False, 'items': [], 'reason': '',
                 'control': bool(control_path), 'control_path': control_path,
                 'loaded': False, 'source_path': ''}
        graph = self.graph
        if graph is None:
            return {**empty, 'reason': '还没有图'}
        src = (graph.nodes or {}).get(from_id)
        if src is None:
            return {**empty, 'reason': '起点模块不在图上'}
        label = str(getattr(src, 'label_name', '') or '')
        info = self._source_beats(src)
        if not info.get('ok'):
            return {**empty, 'reason': str(info.get('reason') or '')}
        beats = list(info.get('beats') or ())
        empty = {**empty, 'loaded': bool(info.get('loaded')),
                 'source_path': str(info.get('path') or '')}
        idxs = [i for i, b in enumerate(beats)
                if str(getattr(b, 'label', '') or '') == label]
        if not idxs:
            return {**empty,
                    'reason': f'{Path(str(info.get("path") or "")).name} 里'
                              f'没有「{label}」这个 label —— 源文件可能刚被改过'}

        total = len(beats)
        session = getattr(self, '_session', None)
        heads = (_session_block_heads(session) if info.get('loaded')
                 else _block_heads_of(beats))
        items = []
        for i in idxs:
            beat = beats[i]
            # 只列**顶层**拍：块体里的拍没有稳定的“上一拍/下一拍”语义，
            # 硬列出来只会让人选中一个写不进的位置。
            if str(getattr(beat, 'nest_path', '') or ''):
                continue
            lid = str(getattr(beat, 'line_id', '') or '')
            if not lid:
                continue
            sub = _beat_position_plan(beats, i, heads)
            items.append({
                'line_id': lid, 'index': i, 'number': i + 1, 'total': total,
                'text': _beat_short_text(beat),
                'selectable': bool(sub.get('ok')),
                'note': str(sub.get('reason') or ''),
            })
        if not items:
            return {**empty, 'reason': f'「{label}」里没有顶层拍可选'}
        return {**empty, 'ok': True, 'items': items, 'reason': ''}

    def suggest_call_from(self, target) -> str:
        """
        给新 `call` 生成一个**没被占用**的存档返回点：`_call_<目标>_<n>`。

        为什么要扫全部脚本：`from` 名字重了，Ren'Py 会报 label 重复，
        整个游戏起不来。扫描只在用户点「自动命名」时发生（不是每次开窗），
        失败就退回"只查本章"，并在对话框里让用户自己改 —— 不悄悄生成
        一个可能撞车的名字。
        """
        from storygraph.story.flow import (call_counts, collect_from_clauses,
                                           from_stem, next_from_clause)

        texts = [str(getattr(b, 'raw', '') or '')
                 for b in (getattr(self._session, 'beats', None) or ())]
        root = getattr(self, '_project_root', None)
        if root:
            try:
                from storygraph.parsing.discover import discover_scripts
                from storygraph.story.source_style import read_text
                for path in discover_scripts(root):
                    try:
                        texts.append(read_text(Path(path))[0])
                    except OSError as e:          # 单个文件读不了：跳过
                        swallow('ui_qt.window_nodes.suggest_call_from.read', e)
            except Exception as e:                # noqa: BLE001 - 扫描失败不该拦住接线
                swallow('ui_qt.window_nodes.suggest_call_from.scan', e)

        used = collect_from_clauses(texts)
        # Ren'Py 会给没写 from 的 call 自动生成 `_call_<目标>_<n>`；
        # 那些名字不在源码里，但同样会被占用，必须一起避开。
        stem = from_stem(target)
        for n in range(1, int(call_counts(texts).get(target, 0)) + 1):
            used.add(f'{stem}_{n}')
        return next_from_clause(target, used)

    def _control_route_plan(self, from_id, to_id, etype='jump'):
        """
        章末接线**写编排层**的计划（P2）；项目里没有独立控制流文件时才 `None`。

        注意：这不是"优先选一个更好看的地方"，而是安全判据 —— 有 `_control`
        包装还往章节源文件末尾插 `jump`，会把目标章跑两遍（见
        `story.control_route` 的模块说明）。
        """
        graph = self.graph
        root = getattr(self, '_project_root', None)
        if graph is None or not root or getattr(graph, 'control_flow', False):
            return None
        src = (graph.nodes or {}).get(from_id)
        dst = (graph.nodes or {}).get(to_id)
        if src is None or dst is None:
            return None
        src_label = str(getattr(src, 'label_name', '') or '')
        dst_label = str(getattr(dst, 'label_name', '') or '')
        if not src_label or not dst_label:
            return None
        from storygraph.story.control_route import plan_control_route
        try:
            plan = plan_control_route(root, src_label, dst_label, etype)
        except Exception as e:                    # noqa: BLE001 - 算不出就按没有编排层
            swallow('ui_qt.window_nodes.control_route_plan', e)
            return None
        # 没有控制流文件（path 空）→ 当作项目没有编排层，走章节源文件那条老路。
        return plan if plan.path else None

    def plan_wire_landing(self, from_id, to_id, position='chapter_end',
                          position_line_id='', etype='jump') -> dict:
        """
        从 `from_id` 拖一条线到 `to_id`，**它会落到哪里**（纯计算）。

        ## 判据（拍板单 E1/E2/E3 + P1 拍位置 + P2 编排层）

        | 选的位置 | 落点 | 要不要问 |
        |---|---|---|
        | 章末 · 项目有独立编排层 | 写 `X_control` 里的路由 | 不问（新增）/ 要确认（改接） |
        | 章末 · 最后一条是 `return` | 插在 `return` **之前** | 不问 |
        | 章末 · 最后一条是台词/舞台 | 接在末尾 | 不问 |
        | 章末 · 最后是 `jump`/`call`/`menu` | 已有去向 | 要问（或改选某一拍） |
        | 指定第 N 拍 | 接在它后面；`return` 则插在它前面 | 不问 |
        | 指定拍在块体里 / 是块头 / 已有去向 | 写不进 | 明说原因 |
        | 起点不是当前载入的那一章 | 写不进 | 明说原因 |

        P2：`position='chapter_end'` 且项目有独立控制流文件时，**先算编排层**。
        `jump` 写 `jump B_control`，`call` 写 `call B`（返回后继续原路）。
        编排层存在但这一段接不上时返回 `control_blocked`，**不许**悄悄退回
        源文件 —— 那会让 `X_control` 的 `call X` 之后再跳一次目标章。

        返回 `{ok, kind, index, after, etype, target, reason, last_raw,
        position, position_line_id, control_plan}`；
        `kind` = `control` / `control_init` / `control_blocked` /
        `chapter_end` / `beat` / `ambiguous` / `not_loaded` /
        `bad_position` / `stale_position` / `cannot`。
        """
        position = str(position or 'chapter_end').strip() or 'chapter_end'
        position_line_id = str(position_line_id or '')
        etype = str(etype or 'jump').strip() or 'jump'
        empty = {'ok': False, 'kind': 'cannot', 'index': -1, 'after': True,
                 'etype': etype, 'target': '', 'reason': '', 'last_raw': '',
                 'position': 'chapter_end', 'position_line_id': '',
                 'from_id': from_id, 'to_id': to_id, 'control_plan': None,
                 'structure': '', 'needs_load': '', 'source_path': ''}
        graph = self.graph
        session = getattr(self, '_session', None)
        if graph is None:
            return {**empty, 'reason': '还没有图或章节'}

        src = (graph.nodes or {}).get(from_id)
        dst = (graph.nodes or {}).get(to_id)
        if src is None or dst is None:
            return {**empty, 'reason': '这条线的两端必须都在图上'}
        target = str(getattr(dst, 'label_name', '') or '')
        if not target:
            return {**empty, 'reason': '目标模块还没有 label 名'}
        if from_id == to_id:
            return {**empty, 'target': target,
                    'reason': '起点和终点是同一个模块'}

        # P2：章末优先走编排层。没有控制流文件时 `_control_route_plan` 返回
        # None，下面照旧按章节源文件算。
        control_fn = getattr(self, '_control_route_plan', None)
        if position == 'chapter_end' and callable(control_fn) \
                and getattr(self, '_project_root', None):
            ctrl = control_fn(from_id, to_id, etype)
            if ctrl is not None:
                base = {**empty, 'etype': etype, 'target': ctrl.target_label,
                        'control_plan': ctrl, 'structure': 'control',
                        'position': 'control_end', 'position_line_id': ''}
                if ctrl.ok:
                    return {**base, 'ok': True, 'kind': 'control',
                            'reason': ctrl.reason,
                            'last_raw': ctrl.old_route}
                init = getattr(ctrl, 'init', None)
                if init is not None and getattr(init, 'ok', False):
                    return {**base, 'kind': 'control_init',
                            'reason': ctrl.reason,
                            'last_raw': ctrl.old_route}
                return {**base, 'kind': 'control_blocked',
                        'reason': ctrl.reason or '编排层这一段接不上'}

        if session is None:
            return {**empty, 'reason': '还没有载入章节'}
        label = str(getattr(src, 'label_name', '') or '')
        info = self._source_beats(src)
        base = {**empty, 'source_path': str(info.get('path') or ''),
                'needs_load': ('' if info.get('loaded')
                               else str(info.get('path') or ''))}
        if not info.get('ok'):
            return {**base, 'kind': 'not_loaded', 'target': target,
                    'reason': str(info.get('reason') or '起点章读不出来')}
        beats = list(info.get('beats') or ())
        idxs = [i for i, b in enumerate(beats)
                if str(getattr(b, 'label', '') or '') == label]
        if not idxs:
            # 起点那一章没在编辑器里 / 文件里也没有这个 label → 插不了拍。
            # **如实说**，别画一条"看起来写进去了"的线（那是最坑的一种假象）。
            return {**base, 'kind': 'not_loaded', 'target': target,
                    'reason': (f'{Path(str(info.get("path") or "")).name} 里'
                               f'找不到 label「{label}」'
                               '—— 现在写不进剧本；先修源文件再接线，'
                               '否则这条线只能画成草图')}

        if position == 'beat':
            if not position_line_id:
                return {**base, 'kind': 'bad_position', 'target': target,
                        'position': 'beat',
                        'reason': '选了「某一拍」，但没说是哪一拍 —— 重新选一个位置'}
            idx = next((i for i in idxs
                        if str(getattr(beats[i], 'line_id', '') or '')
                        == position_line_id), None)
            if idx is None:
                return {**base, 'kind': 'stale_position', 'target': target,
                        'position': 'beat',
                        'position_line_id': position_line_id,
                        'reason': ('这条线选的那一拍已经不在这一章了'
                                   '（可能刚删过 / 改过）—— 重新选一个位置')}
            sub = _beat_position_plan(beats, idx,
                                      (_session_block_heads(session)
                                       if info.get('loaded')
                                       else _block_heads_of(beats)))
            return {**base, **sub, 'target': target, 'position': 'beat',
                    'position_line_id': position_line_id,
                    'structure': 'source',
                    'index': int(idx) if sub.get('ok') else -1,
                    'last_raw': str(getattr(beats[idx], 'raw', '') or '')}

        last = beats[idxs[-1]]
        raw = str(getattr(last, 'raw', '') or '').strip()
        kind_text = str(getattr(last, 'kind', '') or '')
        nest = str(getattr(last, 'nest_path', '') or '')

        # ⚠ **在块体里**（选项体 / if 分支）就归卡片（E3）。
        # 实测：章末 menu 的最后一拍是 `menu:1>B` 里的那个 `return` ——
        # 只看 `raw == 'return'` 会判成"插在它前面"，那一插就插进选项体了。
        if nest:
            return {**base, 'kind': 'ambiguous', 'target': target,
                    'last_raw': raw or kind_text,
                    'reason': f'这一章以块体收尾（{nest}）—— 分支归卡片，'
                              '图上不插；也可以改选前面某一拍'}

        # 已有明确去向 → 位置有歧义（E2：要问）
        if raw.startswith('jump ') or raw.startswith('call '):
            return {**base, 'kind': 'ambiguous', 'target': target,
                    'last_raw': raw,
                    'reason': f'这一章末尾已经有「{raw}」—— 再插一条就有两个'
                              '"去哪"了；改选前面某一拍，或用卡片'}
        from storygraph.core.statement import StatementKind
        if kind_text in (StatementKind.MENU, StatementKind.MENU_OPTION):
            return {**base, 'kind': 'ambiguous', 'target': target,
                    'last_raw': raw or kind_text,
                    'reason': '这一章以选项收尾 —— 分支归卡片，图上不插；'
                              '也可以改选前面某一拍'}

        # `return` → 插在它**前面**（before=True）。⚠ 不能用 `idxs[-2]`
        # + after=True：倒数第二拍可能还在 `if`/`menu` 体里，那样插出来的
        # jump 只在条件成立时执行（2026-10-24 实测）。用 return 自己当参考拍，
        # 由树决定"与 return 同容器"，块体才会被整个跳过。
        if raw == 'return' or raw.startswith('return'):
            return {**base, 'ok': True, 'kind': 'chapter_end',
                    'index': idxs[-1], 'after': False, 'target': target,
                    'structure': 'source', 'last_raw': raw,
                    'reason': f'插在「{label}」的 return 之前'}

        # 台词 / 舞台收尾 → 接在末尾（但那是"落空"的章，照样能接）
        return {**base, 'ok': True, 'kind': 'chapter_end',
                'index': idxs[-1], 'after': True, 'target': target,
                'structure': 'source', 'last_raw': raw or kind_text,
                'reason': f'接在「{label}」末尾'}

    def _apply_control_route(self, plan) -> dict:
        """
        把章末路由**写进编排层**（P2）：只改 `X_control` 那一行，剧本不动。

        走 `ControlRouteCommand`：文件旧全文存在命令里，`Ctrl+Z` 逐字节写回；
        图上加一条 `origin='control'` 的边（不是草图，也不冒充源文件里的行）。
        """
        cplan = plan.get('control_plan') if isinstance(plan, dict) else None
        init = getattr(cplan, 'init', None)
        if cplan is None or not (getattr(cplan, 'ok', False)
                                 or getattr(init, 'ok', False)):
            msg = (getattr(cplan, 'reason', '') or '编排层这一段接不上')
            self._status(msg)
            return {'ok': False, 'error': msg}
        from storygraph.graph.commands import ControlRouteCommand
        cmd = ControlRouteCommand(
            self, cplan, mode='add', from_id=plan.get('from_id'),
            to_id=plan.get('to_id'), etype=plan.get('etype') or 'jump',
            init_plan=getattr(cplan, 'init', None),
            project_root=getattr(self, '_project_root', None))
        # 测试/排查用：这一次真正执行的命令（撤销要拿它）。
        self._last_control_cmd = cmd
        if not self._run_command(cmd):
            msg = getattr(cmd, 'error', '') or '写编排层失败'
            self._status(f'编排层没写成：{msg}')
            return {'ok': False, 'error': msg}
        path = getattr(cmd, 'path', '') or str(getattr(cplan, 'path', ''))
        self._log(f'[接线] 编排层：{getattr(cplan, "control_label", "")} '
                  f'→ {getattr(cplan, "new_lines", []) or [getattr(cplan, "target_label", "")]}'
                  f'（{path}；章节源文件未改）')
        self._status(
            f'✔ 已接到编排层（{Path(path).name if path else "控制流文件"}，'
            f'章节剧本一个字未改）；Ctrl+Z 可撤回')
        return {'ok': True, 'path': path, 'kind': 'control',
                'edge_id': getattr(cmd, 'edge_id', None),
                'control_plan': cplan}

    def _load_for_wire(self, plan) -> dict:
        """
        起点章不在会话里 → 先载入它，再重算一次落点。

        写回是**往那一章的拍里插一行**，会话必须就是那一章。用户 2026-10-24
        抓到的 bug 就是这里只认"当前打开的那一章"：从 `chapter1_2` 拖线，
        下拉框只剩章末，点创建也写不进。现在按需载入（换章前抄快照），
        再把 `line_id` 重新对一遍 —— 载入后那几条拍的身份不该变。
        """
        path = str(plan.get('needs_load') or '')
        graph = self.graph
        src = (graph.nodes or {}).get(plan.get('from_id')) if graph else None
        label = str(getattr(src, 'label_name', '') or '')
        if not path or src is None:
            return {**plan, 'ok': False, 'error': '起点章不在图上，写不进去'}
        if not self._ensure_chapter_loaded(path, label):
            return {**plan, 'ok': False,
                    'error': (f'打不开「{label}」的源文件'
                              f'（{Path(path).name}），没能写进去')}
        new = self.plan_wire_landing(
            plan.get('from_id'), plan.get('to_id'),
            position=str(plan.get('position') or 'chapter_end'),
            position_line_id=str(plan.get('position_line_id') or ''),
            etype=str(plan.get('etype') or 'jump'))
        if not new.get('ok'):
            return {**new,
                    'error': str(new.get('reason') or '换章之后位置算不出来了')}
        return new

    def _apply_wire(self, plan) -> dict:
        """
        把 `plan_wire_landing` 算出来的那一行**写进去**，并让图重派生。

        章末（有编排层）→ 写 `X_control`；章末（没有编排层）/ 指定拍 →
        插进章节源文件（内存）。起点章没载入时先按需载入（`_load_for_wire`），
        接线窗口已经让人确认过位置；写进去后仍要给**明确的撤销提示**
        ——"拖了一下就改了剧本"这种感觉很吓人。
        """
        if isinstance(plan, dict) and str(plan.get('kind') or '') in (
                'control', 'control_init'):
            return self._apply_control_route(plan)
        if isinstance(plan, dict) and plan.get('needs_load'):
            plan = self._load_for_wire(plan)
            if not plan.get('ok'):
                msg = (str(plan.get('error') or '')
                       or str(plan.get('reason') or '') or '换章失败')
                self._status(f'接线没写进去：{msg}')
                return {'ok': False, 'error': msg}
        session = getattr(self, '_session', None)
        if session is None:
            return {'ok': False, 'error': '还没有载入章节'}
        res = session.insert_flow_beat(
            int(plan.get('index', -1)), after=bool(plan.get('after', True)),
            flow_type=str(plan.get('etype') or 'jump'),
            target=str(plan.get('target') or ''),
            from_clause=str(plan.get('from_clause') or ''))
        if not res.get('ok'):
            self._status(f'接线没写进去：{res.get("error") or "未知原因"}')
            return res

        try:
            self._refresh_flow_edges()
        except Exception as e:                # noqa: BLE001 - 图没刷不该回滚剧本
            swallow('ui_qt.window_nodes._apply_wire.refresh', e)

        idx = int(res.get('index', 0))
        frm = str(plan.get('from_clause') or '')
        self._log(f'[接线] 插了一行 {plan.get("etype", "jump")} '
                  f'{plan.get("target", "")}'
                  f'{(" from " + frm) if frm else ""}（第 {idx + 1} 拍，'
                  f'{plan.get("reason", "")}）')
        fallback = (plan.get('structure') == 'source'
                    and plan.get('position') == 'chapter_end')
        self._status(
            f'✔ 已写进第 {idx + 1} 拍（剧本 +1 行；导出才落盘，'
            f'「↩ 撤销结构操作」或 Ctrl+Z 可撤回）'
            + ('；这个项目没有编排层，写的是章节文件' if fallback else ''))
        return res

    def _on_connect_requested(self, from_id, to_id):
        """
        Shift + 左键从一个块拖到另一个块 → **每次都弹「接线」窗口**。

        用户 2026-10-24 明确要求：新连线必须先看见这个窗口（jump/call、
        条件、菜单项、插入位置），由人按下确定才写。不要为了少一次弹窗
        省掉这一步——弹窗不必多，但接线这一步是必要的：线是画上了，可用户
        根本不知道它插在哪、是 jump 还是 call，更没机会改。
        """
        self.add_edge(from_id=from_id, to_id=to_id)

    def _on_node_open(self, nid):
        """进卡片编辑器打开这一章（右栏主按钮 / 右键菜单）。"""
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        info = self._source_info(node)
        if info.get('kind') != 'bound':
            # 没有源文件时**不要**先报一句错再让用户自己找入口：
            # 主按钮就是修复入口（2026-10-22 用户："怎么把无源搞成有源？"）。
            self._repair_node_source(nid, info)
            return
        self.load_script(info['file'], goto_card=True, label=node.label_name)
        self._canvas.highlight_node(nid)

    def _repair_node_source(self, nid, info=None):
        """
        无源节点的修复入口（可重复调用）：

          · 占位块（项目里找得到声明）→ 导入那个文件并绑定；
          · 手工节点 → 新建 `.rpy` / 绑定已有文件；
          · 源文件丢失 → 在原路径新建 / 重新定位。
        """
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        info = info or self._source_info(node)
        kind = info.get('kind')
        label = str(getattr(node, 'label_name', '') or '')

        if kind == 'found':
            path = info.get('file') or ''
            ans = QMessageBox.question(
                self, '导入并绑定源文件',
                f'「{label}」在项目里的这个文件里：\n{path}\n\n'
                f'现在导入它，并把这个节点绑上去？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans == QMessageBox.Yes:
                self._import_and_open(path, label)
            return

        if kind == 'missing':
            path = info.get('file') or ''
            choice = self._repair_choice(
                '源文件丢失',
                f'「{label}」记着这个文件，但磁盘上已经不在了：\n{path}\n\n'
                f'要在原路径重新建一个最小骨架，还是重新定位到别的文件？',
                [('new', '在原路径新建'), ('locate', '重新定位…'),
                 ('cancel', '取消')])
            if choice == 'new':
                wrote = _write_new_chapter(self, path, label)
                if wrote:
                    self._import_and_open(wrote, label)
            elif choice == 'locate':
                self._locate_and_bind(label, start=Path(path).parent)
            return

        # 手工节点：先看项目里有没有同名 label —— 有就直接给"导入并绑定"，
        # 没有才让用户新建/绑定。
        root = getattr(self, '_project_root', None)
        candidates = []
        if root:
            try:
                from storygraph.parsing.discover import find_label_files
                found = find_label_files(root, [label])
                if found.get(label):
                    candidates = [str(found[label])]
            except Exception as e:                # noqa: BLE001
                swallow('ui_qt.window_nodes.repair_find', e)
        if candidates:
            ans = QMessageBox.question(
                self, '导入并绑定源文件',
                f'项目里已经有「{label}」：\n{candidates[0]}\n\n'
                f'导入它并绑定到这个节点？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans == QMessageBox.Yes:
                self._import_and_open(candidates[0], label)
            return

        choice = self._repair_choice(
            '给节点配源文件',
            f'「{label}」现在只是图上的一个块，还没有 `.rpy`。\n\n'
            f'新建一个源文件，或者绑定项目里已有的文件？',
            [('new', '新建 .rpy…'), ('locate', '绑定已有文件…'),
             ('cancel', '取消')])
        if choice == 'new':
            self._create_source_for_node(label)
        elif choice == 'locate':
            self._locate_and_bind(label, start=None)

    def _repair_choice(self, title: str, text: str, buttons) -> str:
        """带自定义按钮文字的询问框 → 返回按钮 key（没有则 'cancel'）。"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Question)
        made = {}
        for key, label in buttons:
            role = (QMessageBox.Cancel if key == 'cancel'
                    else QMessageBox.AcceptRole)
            made[key] = box.addButton(label, role)
        box.exec()
        for key, button in made.items():
            if box.clickedButton() is button:
                return key
        return 'cancel'

    def _create_source_for_node(self, label: str):
        """给手工节点新建一个最小 `.rpy`，导入并打开。"""
        root = getattr(self, '_project_root', None)
        known = []
        try:
            known = [f for f in (self._session.graph_files(self.graph)
                                 if self.graph else []) if Path(f).exists()]
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.window_nodes.create_source.known', e)
        folder = _default_chapter_dir(root, known)
        default = (str(Path(folder) / f'{label}.rpy') if folder
                   else f'{label}.rpy')
        path, _ = QFileDialog.getSaveFileName(
            self, '给节点新建源文件', default, "Ren'Py 脚本 (*.rpy)")
        if not path:
            return
        if not path.lower().endswith('.rpy'):
            self._status('源文件必须以 .rpy 结尾。')
            return
        wrote = _write_new_chapter(self, path, label)
        if wrote:
            self._import_and_open(wrote, label)

    def _locate_and_bind(self, label: str, start=None):
        """选一个已有的 `.rpy`，确认里面真有这个 label，再导入绑定。"""
        start_dir = str(start or (Path(self._project_root or '.') / 'game'))
        path, _ = QFileDialog.getOpenFileName(
            self, f'找到声明「{label}」的 .rpy', start_dir,
            "Ren'Py 脚本 (*.rpy)")
        if not path:
            return
        try:
            from storygraph.story.source_style import read_text
            text = read_text(Path(path))[0]
        except OSError as e:
            self._status(f'读不了这个文件：{e}')
            return
        if not _has_label(text, label):
            self._status(f'{Path(path).name} 里没有 label「{label}」——'
                         f'没有绑定（避免把节点指到错的文件）。')
            return
        self._import_and_open(path, label)

    def _import_and_open(self, path: str, label: str):
        """导入一个文件（会更新同名节点的 `file_path`），再打开那个 label。"""
        res = self.import_chapter_files([path]) or {}
        if not res.get('ok', True):
            self._status(f'导入 {Path(path).name} 失败：'
                         f'{res.get("error") or "未知原因"}')
            return
        node = next((n for n in ((self.graph.nodes or {}).values()
                                 if self.graph else [])
                     if str(getattr(n, 'label_name', '') or '') == label),
                    None)
        if node is None:
            self._status(f'{Path(path).name} 导入完了，但图里没找到「{label}」——'
                         f'检查文件里 label 的写法。')
            return
        self.load_script(path, goto_card=True, label=label)
        self._canvas.highlight_node(node.id)
        self._status(f'✔ 已把「{label}」绑定到 {Path(path).name}，'
                     f'并打开它的第一拍。')
    def open_node_in_editor(self, nid):
        """旧版右键菜单里的「在编辑器中打开」：用系统默认程序打开那个 .rpy。"""
        from storygraph.graph.editor_utils import open_with_system_default
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        fp = getattr(node, 'file_path', None) if node else None
        if not fp or not Path(str(fp)).exists():
            self._status('这个块没有关联到磁盘上的 .rpy，打不开。')
            return
        if open_with_system_default(fp):
            self._log(f'[打开] 已用系统默认程序打开 {Path(str(fp)).name}')
        else:
            self._status(f'没能调用系统默认程序，请手动打开：{fp}')
    def build_node_menu(self, nid=None) -> QMenu:
        """
        拼出右键菜单（**不 exec**，所以能测"哪些项被置灰了"）。

        条目与旧版 `GraphView.menu` 对齐。旧版有而上一版被我漏掉的：
        「在编辑器中打开」。旧版叫「改本章台词（卡片）」的那条保持原名
        （用户认得这个名字）。
        """
        menu = QMenu(self)
        node = (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
        if node is not None:
            name = getattr(node, 'label_name', '')
            fp = getattr(node, 'file_path', None)
            has_src = bool(fp and Path(str(fp)).exists())

            targets = [
                menu.addAction(f'改本章台词（卡片）「{name}」',
                               (lambda: self._on_node_open(nid)) if has_src
                               else None),
                menu.addAction('在编辑器中打开',
                               (lambda: self.open_node_in_editor(nid))
                               if has_src else None),
                menu.addAction('用 Excel 打开此章节',
                               (lambda: self.open_node_in_excel(nid))
                               if has_src else None),
            ]
            if not has_src:
                # 没有源文件的块：**先给修复入口**，再把三条"打开"置灰。
                # 从前只有置灰，用户完全不知道该点哪里（2026-10-22 实测）。
                info = self._source_info(node)
                label = ('导入并绑定源文件…' if info.get('kind') == 'found'
                         else '新建 / 绑定源文件…')
                menu.addAction(label, lambda: self._repair_node_source(nid))
                # 没有源文件的块点了也没用 —— 置灰比"点完再弹一句"友好
                for a in targets:
                    a.setEnabled(False)
                    a.setToolTip('该块未关联源文件'
                                 '（手工新建的块，或先「➕ 导入章节」）')
            else:
                # 同一个 .rpy 里加第二个 label：不用离开工具、不用手打路径。
                menu.addAction('在本文件新建 label…',
                               lambda: self.add_label_to_file(nid))
            menu.addSeparator()
            menu.addAction('设为当前章节',
                           lambda: self._canvas.highlight_node(nid))
            menu.addAction('编辑剧情变量…', lambda: self.edit_node_ops(nid))
            menu.addAction('变量工作台…',
                           lambda: self.show_variable_workbench(nid))
            # 2026-10-22：「工具去适应 python 块」是错的方向；正确的一刀是把
            # 块里那些 `$` 就写得出来的赋值**搬回脚本层**（用户原话）。这一项
            # 只搬简单赋值，块里的调用 / 控制流原位不动。
            menu.addAction('⤴ 搬出 python 块里的赋值…',
                           lambda: self.lift_python_assignments(nid))
            menu.addAction('从这里连一条到…', lambda: self.add_edge(from_id=nid))
            menu.addAction('接管进入点（jump → _control）…',
                           lambda: self.takeover_node(nid))
            menu.addSeparator()
            # 「移除这一章」按**文件**整章移除（用户心智里一章 = 一个 .rpy）；
            # 「删除这个块」只删这一个 label。两者都在，别混为一谈。
            if fp:
                menu.addAction(f'从剧情图移除「{Path(str(fp)).name}」整章',
                               lambda: self.remove_chapter_from_graph(nid))
            menu.addAction('删除这个块…', lambda: self._delete_node(nid))
        else:
            menu.addAction('添加新章节…', self.add_node)
            menu.addAction('添加连线…', self.add_edge)
            menu.addSeparator()
            menu.addAction('自动布局', self._canvas.auto_layout)
            menu.addAction('按文件顺序排列', self.arrange_by_file)
            menu.addAction('适应窗口', self._canvas.fit_to_content)
            menu.addAction('检查逻辑问题', self.check_logic)
            menu.addSeparator()
            # 画布右键 = 剧情图的家务事，所以放"导入章节 / 清空图"，
            # **不放**资源表的动作（那是另一条路，混在一起用户会以为
            # "重扫项目"会把整部戏画出来 —— 正是上一版最大的误解）。
            menu.addAction('导入章节 .rpy…', self.import_chapters)
            menu.addAction('补全缺失章节…', self.complete_related_chapters)
            menu.addAction('刷新已导入章节…', self.reload_imported_chapters)
            menu.addAction('清空剧情图', self.clear_graph)
        return menu
    def _on_node_menu(self, nid, global_pos):
        self.build_node_menu(nid).exec(global_pos)
    def add_node_at(self, x, y):
        """双击空白 = 在那个场景坐标新建章节（旧版 `on_double_click` 的分支）。"""
        return self.add_node(at=(x, y))
    def add_node(self, at=None):
        """
        新建一个章节模块（**校验在对话框里**，不合法就地提示）。

        `at` 给场景坐标时放在那里（双击空白 = 在这里新建，旧版行为）；
        不给就放视野中央（工具栏按钮）。
        """
        from storygraph.ui_qt.dialogs import AddNodeDialog
        existing = [getattr(n, 'label_name', '')
                    for n in ((self.graph.nodes or {}).values()
                              if self.graph else [])]
        # **默认写到项目里已有章节的目录**（Ren'Py 只扫 game/，但作者常分
        # `game/script` 这类子目录 —— 固定写 `game/` 会让新章散落在外面）。
        # 没有项目根（没点「打开项目」）时填空 → 对话框里那一格留空，
        # 行为与从前一致（只在图里画结构）。
        known = []
        try:
            known = [f for f in (self._session.graph_files(self.graph)
                                 if self.graph else [])
                     if Path(f).exists()]
        except Exception as e:                  # noqa: BLE001 - 下拉没有也不影响手填
            swallow('ui_qt.window_nodes.known_files', e)
        default_dir = _default_chapter_dir(
            getattr(self, '_project_root', None), known)
        dlg = AddNodeDialog(self, existing=existing, default_dir=default_dir,
                            known_files=known)
        if dlg.exec() != QDialog.Accepted:
            return
        # **把"无源文件"变成"有源文件"**（2026-10-21 用户实测报的那条：
        # 新建章节之后双击进去"打开章节剧本不能用"）。
        # 先把最小骨架写出来（`label 名:` + `return`），节点就带上 file_path，
        # 双击直接进编辑器。对话框里那一格留空 → 不建文件（旧行为）。
        # `getattr`：测试里喂的是**假对话框**（只实现 name/operations），
        # 真对话框才有这一格。
        _want_file = getattr(dlg, 'file_path', '') or ''
        _ops = getattr(dlg, 'operations', '') or ''
        wrote = _write_new_chapter(self, _want_file, dlg.name, _ops)
        if self.graph is None:
            from storygraph.graph.models import Graph
            self._set_graph(Graph())
        at = drop_qt_bool(at, context='ui_qt.add_node')
        if at is None:
            center = self._canvas.mapToScene(
                self._canvas.viewport().rect().center())
            at = (center.x(), center.y())
        from storygraph.graph.commands import AddNodeCommand
        cmd = AddNodeCommand(self, dlg.name, float(at[0]), float(at[1]),
                             operations=([dlg.operations]
                                         if dlg.operations else None),
                             file_path=(str(_want_file) if wrote else None))
        self._run_command(cmd)
        # 命令 execute 之后才知道新块的 id（它自己 add_node 拿的）
        nid = getattr(cmd, 'node_id', None)
        if nid is not None:
            self._canvas.highlight_node(nid)
            self._canvas.select_node(nid, center=False)
        if wrote:
            self._status(f'已新建章节「{dlg.name}」并写出 {Path(wrote).name}'
                         f'—— 现在双击它就能编辑剧本。Ctrl+Z 可撤销。')
        else:
            self._status(f'已新建章节「{dlg.name}」（只在图里，还没有源文件；'
                         f'右键它 →「新建 / 绑定源文件…」，'
                         f'或点右栏主按钮就能配源文件）。Ctrl+Z 可撤销。')

    def add_label_to_file(self, nid=None):
        """
        在**这个节点所属的同一个 `.rpy`** 里追加一个新 label，并打开它。

        用户 2026-10-22：
        > 我想在一个 rpy 文件里写出第二个 label 该怎么操作？
        > 我就不打开编辑器！我就要在这个工具内改！

        与「新建章节 → 写入文件填已有路径」是同一个落盘动作，但这里
        文件已经确定，只问 label 名 —— 不需要用户手打完整路径。
        """
        node = ((self.graph.nodes or {}).get(nid) if (self.graph and nid)
                else self._selected_node())
        if node is None:
            self._status('先在图上点一个模块，再在它的文件里新建 label。')
            return
        fp = getattr(node, 'file_path', None)
        if not fp or not Path(str(fp)).exists():
            self._status('这个块还没有源文件 —— 先用「新建 / 绑定源文件…」。')
            self._repair_node_source(node.id)
            return
        from storygraph.ui_qt.dialogs import valid_label
        name, ok = QInputDialog.getText(
            self, '在本文件新建 label',
            f'文件：{Path(str(fp)).name}\n新 label 名（可以用点号，如 main.route）：')
        name = str(name or '').strip()
        if not ok or not name:
            return
        if not valid_label(name):
            self._status('label 名不合法：只能字母/数字/下划线，'
                         '可用点号分段，且不能以数字开头。')
            return
        if any(str(getattr(n, 'label_name', '') or '') == name
               for n in (self.graph.nodes or {}).values()):
            self._status(f'图上已经有「{name}」了 —— 换个名字。')
            return
        try:
            from storygraph.story.source_style import read_text
            text = read_text(Path(fp))[0]
        except OSError as e:
            self._status(f'读不了 {Path(fp).name}：{e}')
            return
        if _has_label(text, name):
            self._status(f'{Path(fp).name} 里已经有 label「{name}」了。')
            return
        wrote = _write_new_chapter(self, str(fp), name)
        if not wrote:
            return
        from storygraph.graph.commands import AddNodeCommand
        cmd = AddNodeCommand(self, name, float(node.x) + 60.0,
                             float(node.y) + 160.0, file_path=str(fp))
        self._run_command(cmd)
        if getattr(cmd, 'node_id', None) is not None:
            self._canvas.highlight_node(cmd.node_id)
            self._canvas.select_node(cmd.node_id, center=False)
        self.load_script(str(fp), goto_card=True, label=name)
        self._status(f'✔ 已在 {Path(fp).name} 里追加 label「{name}」，'
                     f'并打开它的第一拍。Ctrl+Z 只撤图上的块，文件改动需手动撤。')

    def add_edge(self, from_id=None, to_id=None):
        """
        接线：**默认写进去**（有编排层写 `X_control`，没有才写章节剧本）；
        填了条件 / 菜单项时写**编排层的分支段**（章节剧本不动）；只有勾了
        「草稿」或实在写不进去时，才落成草图（图上标「草图」）。

        两个入口共用这一份：**Shift + 左键从 A 拖到 B** 和
        右键菜单「从这里连一条到…」/「添加连线…」。

        2026-10-24（P0）改掉了"对话框只会创建草图"的老行为：对话框里
        选好「插入位置」，能写就写；写不进去时按草图保留，并在状态栏
        说清原因，不再出现"点完确定才发现只画了一条线"却不知道为什么。
        """
        from storygraph.ui_qt.dialogs import AddEdgeDialog
        if self.graph is None or len(self.graph.nodes or {}) < 2:
            self._status('至少要有两个模块才能连线'
                         '（先用「➕ 导入章节 .rpy」加几章，或新建章节）。')
            return
        from_id = drop_qt_bool(from_id, context='ui_qt.add_edge')
        if from_id is None:
            from_id = self._canvas.selected_node_id()
        # 只导入、还没打开起点章时，先把它的拍读进来（不跳卡片页）——
        # 否则「插入位置」只剩章末，写回也只会说"先打开这一章"。
        load = getattr(self, '_load_edge_source_for_positions', None)
        if from_id is not None and callable(load):
            load(from_id)
        dlg = AddEdgeDialog(self, nodes=self.graph.nodes, from_id=from_id,
                            to_id=to_id, plan_fn=self.plan_wire_landing,
                            positions_fn=getattr(self, 'edge_position_choices',
                                                None),
                            suggest_from_fn=getattr(self, 'suggest_call_from',
                                                    None),
                            open_fn=getattr(self, '_open_chapter_for_edge',
                                           None))
        if dlg.exec() != QDialog.Accepted:
            return
        if bool(getattr(dlg, 'draft', False)):
            # 人勾了「草稿」：只画线，不写剧本。位置已经记在 dlg 上，
            # 以后右键「写入剧本…」仍能转正。
            self._add_manual_edge(dlg)
            return
        branch = bool(getattr(dlg, 'branch', False))
        if branch:
            # 条件 / 菜单分支：**写进编排层**（`X_control`），源剧本不动。
            # 项目还没有编排层 / 这一章还没有包装时，先带人接管进入点；
            # 接管被取消才退回草图（条件与选项文字都保留）。
            if self._add_control_branch(dlg):
                return
            self._add_manual_edge(dlg)
            return
        if bool(getattr(dlg, 'write', False)):
            self._write_dialog_edge(dlg)
            return
        self._add_manual_edge(dlg)

    def _load_edge_source_for_positions(self, from_id) -> bool:
        """
        起点章还没载入时，把它的拍读进来（**不跳卡片页**）。

        为什么：`edge_position_choices` / `plan_wire_landing` 都依赖
        `self._session`；只导入、没打开时它们只看得到章末，用户会以为
        "没有拍位置这个功能"。只在**当前一章都没载入**时做；已经开着
        别的章时不静默切换（那会丢未保存编辑，必须由用户自己决定）。
        """
        session = getattr(self, '_session', None)
        if session is not None and list(getattr(session, 'beats', None) or ()):
            return False
        node = (self.graph.nodes or {}).get(from_id) if self.graph else None
        if node is None:
            return False
        info = self._source_info(node)
        if info.get('kind') != 'bound':
            return False
        open_fn = getattr(self, '_open_chapter_for_edge', None)
        if not callable(open_fn):
            return False
        try:
            return bool(open_fn(from_id))
        except Exception as e:                # noqa: BLE001 - 载入失败仍能画草图
            swallow('ui_qt.window_nodes.load_edge_source', e)
            return False

    def _open_chapter_for_edge(self, nid) -> bool:
        """把起点章载入当前会话（给外部/测试用；接线窗口里已不再放这个按钮）。"""
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            self._status('这个块不在当前图上。')
            return False
        info = self._source_info(node)
        if info.get('kind') != 'bound':
            self._status(f'「{getattr(node, "label_name", "")}」还没有绑定源文件，'
                         f'写不进剧本；先修源文件再接线。')
            return False
        try:
            self.load_script(info['file'], goto_card=False,
                             label=node.label_name)
        except Exception as e:                    # noqa: BLE001 - 打不开要说话
            swallow('ui_qt.window_nodes._open_chapter_for_edge', e)
            self._status(f'打开「{node.label_name}」失败：{e}')
            return False
        self._status(f'已打开「{node.label_name}」；现在可以把这条线写进剧本。')
        return True

    def _write_dialog_edge(self, dlg) -> bool:
        """把对话框里选好的那一行写进剧本；失败就明说，不偷偷改存草图。"""
        plan = self.plan_wire_landing(
            dlg.from_id, dlg.to_id,
            position=str(getattr(dlg, 'position', 'chapter_end')
                         or 'chapter_end'),
            position_line_id=str(getattr(dlg, 'position_line_id', '') or ''),
            etype=str(getattr(dlg, 'etype', 'jump') or 'jump'))
        if not plan.get('ok'):
            self._status(f'没写进去：{plan.get("reason") or "找不到落点"}')
            return False
        plan = {**plan, 'etype': str(dlg.etype or 'jump'),
                'from_clause': str(getattr(dlg, 'from_clause', '') or '')}
        try:
            res = self._apply_wire(plan)
        except Exception as e:                    # noqa: BLE001 - 写失败要说，不要吞
            swallow('ui_qt.window_nodes._write_dialog_edge', e)
            res = {'ok': False, 'error': str(e)}
        if res.get('ok'):
            return True
        QMessageBox.warning(
            self, '接线没写进去',
            f'{res.get("error") or "未知原因"}\n\n'
            f'剧本一个字节都没改。换一个「插入位置」再试，'
            f'或先打开起点那一章。')
        return False

    def _add_manual_edge(self, dlg) -> None:
        """写不进 / 勾了「草稿」/ 条件分支时走这里（图上会标「草图」）。"""
        from storygraph.graph.commands import AddEdgeCommand
        self.selected_node = dlg.from_id
        self.selected_edge = None
        cmd = AddEdgeCommand(
            self, dlg.from_id, dlg.to_id, dlg.etype, dlg.condition,
            dlg.option_text, origin='manual',
            from_clause=str(getattr(dlg, 'from_clause', '') or ''),
            position=str(getattr(dlg, 'position', 'chapter_end')
                         or 'chapter_end'),
            position_line_id=str(getattr(dlg, 'position_line_id', '') or ''))
        if not self._run_command(cmd):
            return
        self.selected_edge = getattr(cmd, 'edge_id', None)
        names = {nid: getattr(n, 'label_name', '')
                 for nid, n in (self.graph.nodes or {}).items()}
        frm = str(getattr(dlg, 'from_clause', '') or '')
        where = ('章末' if str(getattr(dlg, 'position', 'chapter_end'))
                 != 'beat' else '指定拍')
        explicit_draft = bool(getattr(dlg, 'draft', False))
        mark = '草稿' if explicit_draft else '草图'
        self._log(f'[连线] {names.get(dlg.from_id, dlg.from_id)} → '
                  f'{names.get(dlg.to_id, dlg.to_id)}（{dlg.etype}'
                  f'{(" from " + frm) if frm else ""}'
                  f'{("，" + dlg.condition) if dlg.condition else ""}'
                  f'{("「" + dlg.option_text + "」") if dlg.option_text else ""}）'
                  f' —— {mark} · {where}')
        if explicit_draft:
            self._status(f'已画草稿（勾了「草稿」，未写入；{where}）；'
                         f'右键这条线可以「写入剧本」转正，或删除。')
            return
        reason = str(getattr(dlg, 'reason', '') or '')
        if not reason and (dlg.condition or dlg.option_text):
            reason = '条件/菜单项是分支，不能写成一行'
        why = f'：{reason}' if reason else ''
        self._status(f'已画草图（未写入，图上标「草图」）{why}；'
                     f'右键这条线可以「写入剧本」或删除。')

    def _add_control_branch(self, dlg) -> bool:
        """
        条件 / 菜单分支：写进编排层 `X_control`，**章节剧本不动**。

        没有编排层 / 这一章还没有 `X_control` 时，先弹出接管预览（补包装 +
        把 `jump X` 改接 `jump X_control`）；取消接管就退回草图，条件与
        菜单项文字都保留。返回 `True` = 真写进了编排层。
        """
        from storygraph.graph.commands import AddControlBranchCommand
        from storygraph.story.control_branch import plan_control_branch
        root = getattr(self, '_project_root', None)
        graph = self.graph
        src = (graph.nodes or {}).get(dlg.from_id) if graph else None
        dst = (graph.nodes or {}).get(dlg.to_id) if graph else None
        src_label = str(getattr(src, 'label_name', '') or '')
        dst_label = str(getattr(dst, 'label_name', '') or '')
        if not root or src is None or dst is None:
            self._status('条件 / 菜单分支需要项目根目录与两端模块，没写成。')
            return False
        try:
            probe = plan_control_branch(
                root, src_label, dst_label, dlg.etype, dlg.condition,
                dlg.option_text,
                edge_id=int(getattr(graph, 'next_edge_id', 1) or 1))
        except Exception as e:                    # noqa: BLE001 - 算不出要说原因
            swallow('ui_qt.window_nodes.add_control_branch.plan', e)
            probe = None

        def _ready(p) -> bool:
            """能写吗？目标章缺包装时 `ok=False` 但带一个可执行的 init。"""
            init = getattr(p, 'init', None) if p is not None else None
            return bool(p is not None and (p.ok or
                                           (init is not None and init.ok)))

        if not _ready(probe):
            reason = (getattr(probe, 'reason', '') if probe is not None
                      else '算不出这条分支写在哪')
            # 章末是 jump 导致 `call X` 不返回：接管也救不了这一条
            # （接管只改"谁进来"，不改"这一章怎么出去"），先如实说。
            if '永远不会返回' in reason:
                QMessageBox.warning(self, '条件分支没写成', reason)
                dlg.reason = reason
                return False
            if not self._ensure_control_entry(src_label):
                dlg.reason = reason or '项目里还没有编排层'
                return False
            try:
                probe = plan_control_branch(
                    root, src_label, dst_label, dlg.etype, dlg.condition,
                    dlg.option_text,
                    edge_id=int(getattr(graph, 'next_edge_id', 1) or 1))
            except Exception as e:                # noqa: BLE001
                swallow('ui_qt.window_nodes.add_control_branch.replan', e)
                probe = None
            if not _ready(probe):
                reason = (getattr(probe, 'reason', '') if probe is not None
                          else '接管之后仍然算不出这条分支')
                QMessageBox.warning(
                    self, '条件分支没写成',
                    f'{reason}\n\n章节剧本没有改动。'
                    f'也可以先把条件 / 菜单项留空，接好线再双击线补上。')
                dlg.reason = reason
                return False
        cmd = AddControlBranchCommand(
            self, root, dlg.from_id, dlg.to_id, dlg.etype, dlg.condition,
            dlg.option_text,
            from_clause=str(getattr(dlg, 'from_clause', '') or ''))
        if not self._run_command(cmd):
            err = str(getattr(cmd, 'error', '') or '撤销栈没接住这次改动')
            self._status(f'分支没有写进编排层：{err}')
            return False
        path = Path(str(getattr(cmd, 'path', '') or ''))
        self._log(f'[分支] {src_label}_control：'
                  f'{("菜单 " + dlg.option_text) if dlg.option_text else ""}'
                  f'{(" 条件 " + dlg.condition) if dlg.condition else ""}'
                  f'（{dlg.etype} {dst_label}；{path.name}）')
        self._status(
            f'✔ 条件 / 菜单分支已写进编排层'
            f'（{path.name if path.name else "控制流文件"}）；'
            f'章节剧本一个字未动，Ctrl+Z 可撤回')
        return True

    def _ensure_control_entry(self, label: str) -> bool:
        """这一章还没有 `X_control` / 入口没接管时，先带人接管。"""
        label = str(label or '')
        if not label:
            return False
        root = getattr(self, '_project_root', None)
        if not root:
            self._status('没有项目根目录，接管不了进入点。')
            return False
        from storygraph.story.control_takeover import plan_takeover
        try:
            plan = plan_takeover(root, [label])
        except Exception as e:                    # noqa: BLE001 - 算不出要说
            swallow('ui_qt.window_nodes.ensure_control_entry.plan', e)
            self._status(f'算不出这一章怎么接管：{e}')
            return False
        if not plan.ok:
            QMessageBox.warning(self, '这一章接不进编排层',
                                f'{plan.reason}\n\n'
                                f'条件 / 菜单分支这次没有写。')
            return False
        if not plan.edits:
            return True                       # 已经是接管状态
        return self._takeover_labels([label], plan=plan)

    def _takeover_labels(self, labels, plan=None) -> bool:
        """接管若干章的进入点：先给 diff 预览，确认后多文件原子写。"""
        from storygraph.graph.commands import TakeoverCommand
        from storygraph.story.control_takeover import plan_takeover
        from storygraph.ui_qt.dialogs import TakeoverDialog
        root = getattr(self, '_project_root', None)
        labels = [str(x) for x in (labels or ()) if str(x or '').strip()]
        if not root or not labels:
            self._status('没有可接管的章节。')
            return False
        if plan is None:
            try:
                plan = plan_takeover(root, labels)
            except Exception as e:                # noqa: BLE001 - 算不出要说
                swallow('ui_qt.window_nodes.takeover.plan', e)
                self._status(f'算不出怎么接管：{e}')
                return False
        if not plan.ok:
            QMessageBox.warning(self, '接管不了进入点', str(plan.reason))
            return False
        if not plan.edits:
            self._status('这些章已经在编排层里了，没有要改的入口。')
            return True
        # 接管会**直接改盘**。当前打开的那一章如果还有没导出的改动，
        # 内存里的拍和磁盘马上就会分叉（下一次导出会把接管改回去）——
        # 先让人导出，别替人做这个决定。
        session = getattr(self, '_session', None)
        loaded = str(getattr(session, 'script_path', '') or '')
        edited = []
        for e in plan.edits:
            try:
                edited.append(Path(e.path).resolve())
            except OSError:
                continue
        touches_loaded = bool(loaded) and Path(loaded).resolve() in edited
        if touches_loaded and session is not None \
                and session.has_unsaved_edits():
            QMessageBox.warning(
                self, '先导出这一章',
                f'「{Path(loaded).name}」还有未导出的改动；接管会直接改这个'
                f'文件，内存里的拍会和磁盘分叉。\n\n'
                f'先点「导出」把这一章写回去，再接管。这次没有改任何文件。')
            return False
        dlg = TakeoverDialog(self, plan=plan)
        if dlg.exec() != QDialog.Accepted:
            self._status('取消接管：一个文件都没改。')
            return False
        cmd = TakeoverCommand(self, plan)
        if not self._run_command(cmd):
            self._status('接管没有写成：撤销栈没接住这次改动。')
            return False
        if touches_loaded:
            # 磁盘已经变了，内存必须跟着重读；否则下一次导出会拿旧拍
            # 把 `jump X_control` 写回 `jump X`（接管白做）。
            try:
                self.load_script(loaded, goto_card=False)
            except Exception as e:                # noqa: BLE001 - 重读失败要说话
                swallow('ui_qt.window_nodes.takeover.reload', e)
                self._status(f'接管写好了，但重读这一章失败：{e}；'
                             f'请手动重新导入这一章')
        files = '、'.join(Path(p).name for p in
                          (getattr(cmd, 'written', None) or []))
        self._log(f'[接管] {"、".join(labels)} → 编排层（{files}）')
        self._status(
            f'✔ 已接管进入点：补 {len(plan.wrappers)} 个包装，'
            f'改 {sum(len(e.changes) for e in plan.edits)} 处 jump；'
            f'Ctrl+Z 可整体撤回')
        return True

    def takeover_node(self, nid=None) -> bool:
        """右键「接管进入点…」：把 `jump X` 改接 `jump X_control`。"""
        graph = self.graph
        nid = nid if nid is not None else getattr(self, 'selected_node', None)
        node = (graph.nodes or {}).get(nid) if graph else None
        if node is None:
            self._status('先点一个模块，再接管它的进入点。')
            return False
        label = str(getattr(node, 'label_name', '') or '')
        if not label:
            self._status('这个模块还没有 label 名。')
            return False
        return self._takeover_labels([label])

    def _edit_control_branch(self, edge, dlg) -> bool:
        """改写编排层里一条条件 / 菜单分支（按标记段换，旧的自动消失）。"""
        from storygraph.graph.commands import WriteControlBranchCommand
        if not (str(dlg.condition or '').strip()
                or str(dlg.option_text or '').strip()):
            QMessageBox.information(
                self, '清空分支',
                '条件 / 菜单项都清空，这条线就不再是分支了。\n'
                '请删掉这条线，再用「从这里连一条到…」接一条普通路由 ——'
                '这样不会留下半条旧分支。')
            return False
        if str(getattr(edge, 'option_text', '') or '').strip() \
                and not str(dlg.option_text or '').strip():
            QMessageBox.information(
                self, '清空菜单项',
                '菜单项不能留空：菜单组里会多出一个没有文字的选项。\n'
                '要删掉这个选项请删除这条线（菜单组会自动收缩/消失）。')
            return False
        cmd = WriteControlBranchCommand(
            self, getattr(self, '_project_root', None), edge.id,
            dlg.etype, dlg.condition, dlg.option_text, dlg.from_clause)
        if not self._run_command(cmd):
            err = str(getattr(cmd, 'error', '') or '撤销栈没接住这次改动')
            QMessageBox.warning(self, '这条分支没改成', err)
            return False
        path = Path(str(getattr(cmd, 'path', '') or ''))
        self._log(f'[分支] edge {edge.id} → {dlg.etype} '
                  f'{("条件 " + dlg.condition) if dlg.condition else ""}'
                  f'{(" 菜单 " + dlg.option_text) if dlg.option_text else ""}'
                  f'（{path.name}）')
        self._status(
            f'✔ 已改写编排层里的分支段（{path.name if path.name else "控制流文件"}）；'
            f'章节剧本一个字未动，Ctrl+Z 可撤回')
        return True

    def change_edge_position(self, edge) -> bool:
        """
        改一条草图的落点（P2）：章末 ↔ 第 N 拍，进图级撤销栈。

        只改图上的"打算写在哪"，**不碰剧本**；要写进去仍走「写入剧本…」。
        当前那一拍已经不在本章时，窗口会明说，只允许重选一个能选的位置。
        """
        if edge is None or str(getattr(edge, 'origin', '')) != 'manual':
            return False
        from storygraph.ui_qt.dialogs import EdgePositionDialog
        try:
            data = self.edge_position_choices(edge.from_id)
        except Exception as e:                    # noqa: BLE001 - 算不出也要能改成章末
            swallow('ui_qt.window_nodes.change_edge_position.choices', e)
            data = {'ok': False, 'items': [], 'reason': '位置列表算不出来'}
        parent = self if isinstance(self, QWidget) else None
        dlg = EdgePositionDialog(
            parent, positions=data,
            current_position=str(getattr(edge, 'position', '') or ''),
            current_line_id=str(getattr(edge, 'position_line_id', '') or ''))
        if dlg.exec() != QDialog.Accepted:
            return False
        new_pos = str(getattr(dlg, 'position', 'chapter_end') or 'chapter_end')
        new_lid = str(getattr(dlg, 'position_line_id', '') or '')
        if (new_pos == str(getattr(edge, 'position', '') or '')
                and new_lid == str(getattr(edge, 'position_line_id', '') or '')):
            return False
        from storygraph.graph.commands import SetEdgePositionCommand
        cmd = SetEdgePositionCommand(self, edge.id, new_pos, new_lid)
        if not self._run_command(cmd):
            self._status('改落点没记进历史，图没有改动。')
            return False
        where = '章末' if new_pos != 'beat' else '指定拍'
        self._status(f'✔ 草图落点已改成「{where}」；剧本一个字没动，'
                     f'Ctrl+Z 可撤回。')
        self._log(f'[改落点] edge {edge.id} → {new_pos} {new_lid}')
        return True

    def open_control_layer(self, edge=None) -> bool:
        """右键编排线 → 打开控制流文件（不跳字符位置，先让人看见它住在哪）。"""
        from storygraph.story.control_route import find_control_file
        path = find_control_file(getattr(self, '_project_root', None))
        if path is None:
            self._status('这个项目现在没有独立控制流文件（编排层）。')
            return False
        try:
            self.load_script(str(path), goto_card=True)
        except Exception as e:                    # noqa: BLE001 - 打不开要说
            swallow('ui_qt.window_nodes.open_control_layer', e)
            self._status(f'打开编排层失败：{e}')
            return False
        self._status(f'已打开编排层：{Path(path).name}（章节剧本没动）')
        return True

    def edit_edge(self, edge):
        """
        双击一条线 → **这条线自己的属性**（旧版「编辑连线」）。

        不是"跳到这一拍"：那是右键菜单里的「定位到这一拍」。用户 2026-10-24
        实测：双击线弹出「添加新章节」，因为线的标签盖住了线、双击落到了空白。
        现在线/标签都归到这里，窗口里显示类型、条件、菜单项和**插入位置**
        （线上只留条件和菜单项，类型看实线/虚线）。
        """
        if edge is None:
            return False
        from storygraph.graph.commands import (EditControlRouteCommand,
                                               EditSourceFlowCommand,
                                               UpdateEdgeCommand)
        from storygraph.ui_qt.dialogs import EditEdgeDialog

        origin = str(getattr(edge, 'origin', 'source') or 'source')
        graph = self.graph
        src = (graph.nodes or {}).get(edge.from_id) if graph else None
        dst = (graph.nodes or {}).get(edge.to_id) if graph else None
        from_label = str(getattr(src, 'label_name', '') or edge.from_id or '')
        to_label = str(getattr(dst, 'label_name', '') or edge.to_id or '')

        positions = None
        if origin in ('manual', 'source'):
            try:
                positions = self.edge_position_choices(edge.from_id)
            except Exception as e:            # noqa: BLE001 - 列表算不出仍能改语义
                swallow('ui_qt.window_nodes.edit_edge.positions', e)
                positions = None

        targets = None
        if origin in ('source', 'control'):
            targets = []
            for nid, node in (graph.nodes or {}).items():
                if nid == edge.from_id:
                    continue
                targets.append((nid, str(getattr(node, 'label_name', '') or nid)))
            targets.sort(key=lambda pair: str(pair[1]))

        dlg = EditEdgeDialog(
            self, edge=edge, from_label=from_label, to_label=to_label,
            position_text=self._edge_position_text(edge),
            positions=positions, targets=targets, target_id=edge.to_id,
            open_fn=(lambda: self.open_control_layer(edge))
            if origin == 'control' else None)
        if not dlg.exec():
            return False
        if not dlg.changed:
            self._status('连线没有改动。')
            return False

        new_dst = (graph.nodes or {}).get(dlg.to_id) if graph else None
        new_to_label = str(getattr(new_dst, 'label_name', '') or '')

        if origin == 'control':
            is_branch = bool(str(getattr(edge, 'condition', '') or '')
                             or str(getattr(edge, 'option_text', '') or ''))
            branch_changed = (str(dlg.condition or '')
                              != str(getattr(edge, 'condition', '') or '')
                              or str(dlg.option_text or '')
                              != str(getattr(edge, 'option_text', '') or ''))
            target_changed = (str(dlg.to_id) != str(getattr(edge, 'to_id', ''))
                              or dlg.etype
                              != str(getattr(edge, 'type', 'jump') or 'jump'))
            # 分支边改目标 / 类型也要改写标记段；否则只改字段、文件不动，
            # 图上"看着改了"、跑起来还是旧目标。
            if branch_changed or (is_branch and target_changed):
                return self._edit_control_branch(edge, dlg)
            return self._edit_control_edge(edge, dlg, new_to_label,
                                           EditControlRouteCommand)
        if origin == 'source':
            changed_line = (dlg.etype
                            != str(getattr(edge, 'type', 'jump') or 'jump')
                            or str(dlg.to_id)
                            != str(getattr(edge, 'to_id', '')))
            old_line_id = str(getattr(edge, 'line_id', '') or '')
            moved = (str(dlg.position_line_id or '') != old_line_id
                     and str(dlg.position or '') == 'beat')
            if changed_line and not self._edit_source_edge(
                    edge, dlg, new_to_label, EditSourceFlowCommand, src):
                return False
            if moved:
                return self._move_source_edge(edge, dlg)
            return changed_line

        cmd = UpdateEdgeCommand(
            self, edge.id, etype=dlg.etype, condition=dlg.condition,
            option_text=dlg.option_text, from_clause=dlg.from_clause,
            position=dlg.position,
            position_line_id=dlg.position_line_id)
        if not self._run_command(cmd):
            self._status('连线没有保存：撤销栈没接住这次改动。')
            return False
        self._log(f'[连线] edge {edge.id} → {dlg.etype}'
                  f'{(" 条件 " + dlg.condition) if dlg.condition else ""}'
                  f'{(" 菜单 " + dlg.option_text) if dlg.option_text else ""}'
                  f'{(" from " + dlg.from_clause) if dlg.from_clause else ""}')
        self._status('✔ 已更新这条草图（图上）；'
                     '右键「写入剧本…」才落进 .rpy。Ctrl+Z 可撤回。')
        return True

    def _edit_control_edge(self, edge, dlg, new_to_label, command_cls) -> bool:
        """编排线：把 `X_control` 里那一行**原地改掉**（类型 / 目标）。"""
        src_node = ((self.graph.nodes or {}).get(edge.from_id)
                    if self.graph else None)
        old_to = str(getattr(
            (self.graph.nodes or {}).get(edge.to_id) if self.graph else None,
            'label_name', '') or '')
        if dlg.etype == str(getattr(edge, 'type', 'jump') or 'jump') \
                and str(dlg.to_id) == str(edge.to_id):
            self._status('连线的类型和目标都没变。')
            return False
        if not new_to_label:
            self._status('新目标没有 label 名，没有改动。')
            return False
        try:
            from storygraph.story.control_route import plan_control_edit
            plan = plan_control_edit(
                getattr(self, '_project_root', None),
                str(getattr(src_node, 'label_name', '') or ''),
                old_to, str(getattr(edge, 'type', 'jump') or 'jump'),
                new_to_label, dlg.etype)
        except Exception as e:                # noqa: BLE001 - 算不出要说原因
            swallow('ui_qt.window_nodes.edit_control_edge.plan', e)
            QMessageBox.warning(self, '这条编排线改不了', str(e))
            return False
        if not plan.ok:
            QMessageBox.warning(
                self, '这条编排线改不了',
                f'{plan.reason or "算不出要改哪一行"}\n\n'
                f'也可以右键这条线 →「在编辑器里打开编排层」手动改。')
            return False
        cmd = command_cls(
            self, plan, edge.id, dlg.to_id, dlg.etype,
            project_root=getattr(self, '_project_root', None))
        if not self._run_command(cmd):
            err = str(getattr(cmd, 'error', '') or '撤销栈没接住这次改动')
            self._status(f'编排层没改成：{err}')
            return False
        path = Path(str(getattr(cmd, 'path', '') or ''))
        self._log(f'[连线] 编排层：{getattr(plan, "control_label", "")} '
                  f'{old_to} → {new_to_label}（{dlg.etype}；{path.name}）')
        self._status(
            f'✔ 已改写编排层那一行（{path.name if path.name else "控制流文件"}：'
            f'jump/call 目标与类型）；章节剧本一个字未动，Ctrl+Z 可撤回')
        return True

    def _edit_source_edge(self, edge, dlg, new_to_label, command_cls,
                          src_node) -> bool:
        """源边：把剧本里那一行**原地改掉**（类型 / 目标；导出才落盘）。"""
        old_type = str(getattr(edge, 'type', 'jump') or 'jump')
        if dlg.etype == old_type and str(dlg.to_id) == str(edge.to_id):
            self._status('连线的类型和目标都没变。')
            return False
        if not new_to_label:
            self._status('新目标没有 label 名，没有改动。')
            return False
        info = self._source_info(src_node) if src_node is not None else {}
        path = str(info.get('file') or '')
        label = str(getattr(src_node, 'label_name', '') or '')
        if info.get('kind') != 'bound' or not path:
            self._status(f'「{label}」没有可写的源文件，改不了这一行。')
            return False
        if not self._ensure_chapter_loaded(path, label):
            self._status(f'打不开「{label}」的源文件，没改成。')
            return False
        session = getattr(self, '_session', None)
        beats = list(getattr(session, 'beats', None) or ())
        line_id = str(getattr(edge, 'line_id', '') or '')
        beat = next((b for b in beats
                     if str(getattr(b, 'line_id', '') or '') == line_id), None)
        if beat is None:
            self._status('那一拍已经不在这一章里了（可能刚改过），'
                         '先重新导入 / 刷新剧情图再试。')
            return False
        refresh = getattr(self, '_refresh_flow_edges', None)
        cmd = command_cls(self, session, beat, dlg.etype, new_to_label,
                          refresh=refresh)
        if not self._run_command(cmd):
            err = str(getattr(cmd, 'error', '') or '撤销栈没接住这次改动')
            self._status(f'剧本那一行没改成：{err}')
            return False
        self._log(f'[连线] 源边 {label}：{old_type} → {dlg.etype} '
                  f'{new_to_label}（第 {int(getattr(beat, "seq", 0) or 0)} 拍）')
        self._status(
            f'✔ 已改写「{label}」里那一行（{dlg.etype} {new_to_label}）；'
            f'改动在内存里，导出 .rpy 时才落盘。Ctrl+Z 可撤回')
        return True

    def _move_source_edge(self, edge, dlg) -> bool:
        """
        源边改插入位置：把剧本里那一行**移到本章另一个顶层拍之后**。

        不删了重插：块结构 / 条件挂在拍上，删重插会拆块；用
        `session.move_beat` 让树决定新位置，撤销走结构撤销栈。
        跨章不在这里做 —— 那会改 `from` label，不是"位置"而是另一条边。
        """
        from storygraph.graph.commands import MoveSourceFlowCommand
        graph = self.graph
        src = (graph.nodes or {}).get(edge.from_id) if graph else None
        if src is None:
            self._status('这条线的起点不在图上。')
            return False
        label = str(getattr(src, 'label_name', '') or '')
        info = self._source_info(src)
        path = str(info.get('file') or '')
        if info.get('kind') != 'bound' or not path:
            self._status(f'「{label}」没有可写的源文件，挪不了这一行。')
            return False
        if not self._ensure_chapter_loaded(path, label):
            self._status(f'打不开「{label}」的源文件，没挪成。')
            return False
        session = getattr(self, '_session', None)
        beats = list(getattr(session, 'beats', None) or ())
        old_lid = str(getattr(edge, 'line_id', '') or '')
        new_lid = str(getattr(dlg, 'position_line_id', '') or '')
        old_idx = next((i for i, b in enumerate(beats)
                        if str(getattr(b, 'line_id', '') or '') == old_lid),
                       None)
        new_idx = next((i for i, b in enumerate(beats)
                        if str(getattr(b, 'line_id', '') or '') == new_lid),
                       None)
        if old_idx is None:
            self._status('那一拍已经不在这一章里了（可能刚改过），'
                         '先重新导入 / 刷新剧情图再试。')
            return False
        if new_idx is None:
            self._status('选中的那一拍已经不在这一章里了，重新选一个位置。')
            return False
        target_beat = beats[new_idx]
        if str(getattr(target_beat, 'label', '') or '') != label:
            self._status('跨 label 挪行会改这条线的起点，不做；'
                         '要换起点请删掉这条线重接。')
            return False
        if str(getattr(target_beat, 'nest_path', '') or ''):
            self._status('那一拍在块体里，不能把线接在那里。')
            return False
        if new_idx == old_idx:
            self._status('位置没有变。')
            return False
        remaining = [b for i, b in enumerate(beats) if i != old_idx]
        target_pos = next((i for i, b in enumerate(remaining)
                           if b is target_beat), None)
        if target_pos is None:
            self._status('算不出这一拍的新位置，没挪成。')
            return False
        raw = str(getattr(target_beat, 'raw', '') or '').strip()
        dst = target_pos if raw.startswith('return') else target_pos + 1
        if dst == old_idx:
            self._status('位置没有变。')
            return False
        cmd = MoveSourceFlowCommand(
            self, session, old_idx, dst,
            refresh=getattr(self, '_refresh_flow_edges', None))
        if not self._run_command(cmd):
            err = str(getattr(cmd, 'error', '') or '撤销栈没接住这次移动')
            self._status(f'那一行没挪成：{err}')
            return False
        self._log(f'[连线] 源边 {label}：第 {old_idx + 1} 拍 → '
                  f'第 {int(getattr(cmd, "actual_index", dst)) + 1} 拍')
        self._status(
            f'✔ 已把「{label}」里那一行挪到新位置'
            f'（导出 .rpy 时才落盘）；Ctrl+Z 可撤回')
        return True

    def build_edge_menu(self, edge) -> QMenu:
        """一条线的右键菜单（**不 exec**，方便测哪些项被置灰）。"""
        menu = QMenu(self)
        if edge is None:
            return menu
        origin = str(getattr(edge, 'origin', 'source') or 'source')
        manual = origin == 'manual'
        control = origin == 'control'
        menu.addAction('编辑连线…', lambda: self.edit_edge(edge))
        menu.addSeparator()
        if control:
            menu.addAction('在编辑器里打开编排层',
                           lambda: self.open_control_layer(edge))
            menu.addAction('删除这条编排线…',
                           lambda: self.delete_edge(edge.id))
            return menu
        if manual:
            plan = self.plan_wire_landing(
                edge.from_id, edge.to_id,
                position=str(getattr(edge, 'position', 'chapter_end')
                             or 'chapter_end'),
                position_line_id=str(getattr(edge, 'position_line_id', '')
                                     or ''))
            can_write = (bool(plan.get('ok'))
                         and not str(getattr(edge, 'condition', '') or '')
                         and not str(getattr(edge, 'option_text', '') or ''))
            act = menu.addAction('写入剧本…',
                                 lambda: self.write_manual_edge(edge))
            act.setEnabled(can_write)
            if not can_write:
                act.setToolTip(str(plan.get('reason')
                                   or '条件/菜单项分支请用卡片'))
            menu.addAction('改落点…',
                           lambda: self.change_edge_position(edge))
            menu.addAction('删除这条草图',
                           lambda: self.delete_edge(edge.id))
        else:
            menu.addAction('定位到这一拍',
                           lambda: self._open_edge_in_card(edge))
            menu.addAction('删除这条连线…',
                           lambda: self.delete_edge(edge.id))
        return menu

    def _on_edge_menu(self, edge, global_pos):
        self.build_edge_menu(edge).exec(global_pos)

    def _edge_position_text(self, edge) -> str:
        """菜单/提示里那句"这条线在第几拍"（不另造位置）。"""
        try:
            ctx = self._beat_context() or {}
        except Exception as e:                    # noqa: BLE001 - 菜单不能因上下文炸
            swallow('ui_qt.window_nodes._edge_position_text', e)
            ctx = {}
        if str(getattr(edge, 'origin', 'source') or 'source') == 'control':
            return '章末（编排层）'
        if str(getattr(edge, 'origin', 'source') or 'source') == 'manual':
            if str(getattr(edge, 'position', 'chapter_end')) == 'beat':
                lid = str(getattr(edge, 'position_line_id', '') or '')
                info = ctx.get(lid) if lid else None
                where = (f'第 {info.get("index")} 拍' if info
                         else '指定拍（未载入或已删除）')
            else:
                where = '章末'
            return f'草图 · 未写入 · {where}'
        line_id = str(getattr(edge, 'line_id', '') or '')
        info = ctx.get(line_id) if line_id else None
        if info:
            return f'第 {info.get("index")} 拍 / 共 {info.get("total")} 拍'
        # 当前没打开那一章：只读解析源文件，别把"第几拍"说成未载入
        fn = getattr(self, '_source_beats', None)
        src = ((getattr(self, 'graph', None).nodes or {}).get(edge.from_id)
               if getattr(self, 'graph', None) else None)
        data = fn(src) if callable(fn) and src is not None else {}
        if isinstance(data, dict) and data.get('ok'):
            beats = list(data.get('beats') or ())
            for i, beat in enumerate(beats):
                if str(getattr(beat, 'line_id', '') or '') == line_id:
                    return f'第 {i + 1} 拍 / 共 {len(beats)} 拍'
        return '源边 · 当前位置未载入'

    def write_manual_edge(self, edge):
        """把一条草图**真的写进剧本**，然后移除草图（P0 的"转正"）。"""
        if edge is None or str(getattr(edge, 'origin', '')) != 'manual':
            return
        if str(getattr(edge, 'condition', '') or '') \
                or str(getattr(edge, 'option_text', '') or ''):
            self._status('带条件 / 菜单项的线是分支，不能直接写成一行；'
                         '请用卡片编辑，或删掉这条草图。')
            return
        plan = self.plan_wire_landing(
            edge.from_id, edge.to_id,
            position=str(getattr(edge, 'position', 'chapter_end')
                         or 'chapter_end'),
            position_line_id=str(getattr(edge, 'position_line_id', '') or ''))
        if not plan.get('ok'):
            QMessageBox.information(
                self, '这条草图写不进剧本',
                str(plan.get('reason') or '当前章节没有可插的落点。'))
            return
        plan = {**plan, 'etype': str(getattr(edge, 'type', 'jump') or 'jump'),
                'from_clause': str(getattr(edge, 'from_clause', '') or '')}
        res = self._apply_wire(plan)
        if not res.get('ok'):
            return
        from storygraph.graph.commands import RemoveEdgeCommand
        self._run_command(RemoveEdgeCommand(self, edge.id))
        self._status(f'✔ 已把草图写成剧本（{plan.get("etype", "jump")}）；'
                     f'草图已移除，Ctrl+Z 可撤回。')

    def edge_delete_plan(self, edge) -> dict:
        """
        删这条边**会发生什么** —— 纯查询，不改任何状态（S1，2026-10-23）。

        返回：
          `{ok, kind, index, total, who, text, preview, archive_note, error}`

        `kind` = `manual`（草图，只删图）/ `control`（编排层，只删一行）/
        `source`（剧本里的那一行）/ `blocked`。

        为什么要单独一个"计划"函数：确认框、撤销、测试都要看同一份结论。
        三处各算一遍的话，迟早出现"框里说删 1 行、实际删了 2 行"。
        """
        empty = {'ok': False, 'kind': 'blocked', 'index': -1, 'total': 0,
                 'who': '', 'text': '', 'preview': {}, 'archive_note': '',
                 'error': '', 'control_plan': None, 'path': ''}
        if edge is None:
            return {**empty, 'error': '这条线已经不在了'}
        origin = str(getattr(edge, 'origin', 'source') or 'source')

        if origin == 'control':
            graph = self.graph
            src = (graph.nodes or {}).get(edge.from_id) if graph else None
            dst = (graph.nodes or {}).get(edge.to_id) if graph else None
            src_label = str(getattr(src, 'label_name', '') or '')
            dst_label = str(getattr(dst, 'label_name', '') or '')
            if str(getattr(edge, 'condition', '') or '') \
                    or str(getattr(edge, 'option_text', '') or ''):
                # 条件 / 菜单分支：按标记段删（菜单组里只删那一项）。
                from storygraph.story.control_branch import (
                    plan_control_branch_remove)
                bplan = plan_control_branch_remove(
                    getattr(self, '_project_root', None), src_label, edge.id)
                if not bplan.ok:
                    return {**empty, 'kind': 'control_branch',
                            'path': bplan.path, 'branch_plan': bplan,
                            'error': bplan.reason or '编排层里找不到这条分支'}
                return {**empty, 'ok': True, 'kind': 'control_branch',
                        'path': bplan.path, 'branch_plan': bplan,
                        'who': '编排层', 'text': bplan.reason,
                        'archive_note': ''}
            from storygraph.story.control_route import plan_control_unroute
            cplan = plan_control_unroute(
                getattr(self, '_project_root', None), src_label, dst_label,
                str(getattr(edge, 'type', 'jump') or 'jump'))
            flow = None
            try:
                from storygraph.story.flow import parse_flow_line
                flow = parse_flow_line(str(getattr(cplan, 'old_line', '')).strip())
            except Exception as e:                # noqa: BLE001 - 只影响存档提示
                swallow('ui_qt.window_nodes.edge_delete_plan.control_flow', e)
            flow = flow or {'type': str(getattr(edge, 'type', 'jump')
                                       or 'jump'),
                            'target': dst_label, 'from_clause': ''}
            if not cplan.ok:
                return {**empty, 'kind': 'control', 'path': cplan.path,
                        'control_plan': cplan,
                        'error': cplan.reason or '编排层里找不到这条路由'}
            return {**empty, 'ok': True, 'kind': 'control',
                    'path': cplan.path, 'control_plan': cplan,
                    'who': '编排层',
                    'text': f'{cplan.control_label}：{cplan.old_route}',
                    'archive_note': self._edge_archive_note(flow)}

        session = getattr(self, '_session', None)
        if session is None:
            return {**empty, 'error': '还没有载入章节'}

        line_id = str(getattr(edge, 'line_id', '') or '')
        if origin != 'source' or not line_id:
            # 草图：剧本里本来就没有这一行 —— 只删图，不碰剧本
            return {**empty, 'ok': True, 'kind': 'manual'}

        beats = list(getattr(session, 'beats', None) or ())
        idx = next((i for i, b in enumerate(beats)
                    if str(getattr(b, 'line_id', '') or '') == line_id), None)
        if idx is None:
            # 不在当前章节 → 若它确实属于另一章，走"只删源文件那一行"的
            # 路径（不切当前章节，P2）。同一章里找不到则仍然拒绝 ——
            # 那是"刚改过 / 身份过期"，不能拿文件去猜。
            planner = getattr(self, '_plan_source_file_delete', None)
            other = planner(edge, session) if callable(planner) else None
            if other is not None:
                return other
            return {**empty,
                    'error': ('这条线对应的那一拍不在这章里了 '
                              '（可能刚改过 / 不是在当前这一章里）')}

        preview = session.delete_preview(idx)
        if preview.get('ok') is False and not preview.get('can_cascade'):
            return {**empty, 'index': idx, 'total': len(beats),
                    'preview': preview,
                    'error': preview.get('error') or '这一拍删不了'}

        beat = beats[idx]
        from storygraph.story.flow import flow_card_text, parse_flow
        flow = parse_flow(beat)
        who = ('控制流' if flow else
               (str(getattr(beat, 'speaker', '') or '') or '（旁白）'))
        text = (flow_card_text(flow) if flow else
                (str(getattr(beat, 'text', '') or '')[:60] or '（没有台词）'))

        return {**empty, 'ok': True, 'kind': 'source', 'index': idx,
                'total': len(beats), 'who': who, 'text': text,
                'preview': preview,
                'archive_note': self._edge_archive_note(flow)}

    def _plan_source_file_delete(self, edge, session):
        """
        源边不在当前章节时，算"只删源文件那一行"的计划。

        只在**确实属于另一个文件**时才走；同一文件里找不到行身份说明
        文件刚被改过，拿行号去删会删错 —— 那条路仍然拒绝。
        """
        origin = str(getattr(edge, 'origin', 'source') or 'source')
        if origin != 'source':
            return None
        graph = self.graph
        node = (graph.nodes or {}).get(edge.from_id) if graph else None
        path = str(getattr(node, 'file_path', '') or '') if node is not None else ''
        if not path:
            return None
        cur = str(getattr(session, 'script_path', '') or '')
        try:
            if cur and Path(cur).resolve() == Path(path).resolve():
                return None
        except OSError:
            return None
        from storygraph.app.source_line_delete import plan_source_line_delete
        plan = plan_source_line_delete(
            getattr(self, '_project_root', None), path,
            str(getattr(edge, 'line_id', '') or ''),
            str(getattr(node, 'label_name', '') or ''))
        if not plan.ok:
            return {'ok': False, 'kind': 'source_file', 'path': path,
                    'error': plan.reason or '这条源边删不了'}
        dst = (graph.nodes or {}).get(edge.to_id) if graph else None
        flow = {'type': str(getattr(edge, 'type', 'jump') or 'jump'),
                'target': str(getattr(dst, 'label_name', '') or ''),
                'from_clause': str(getattr(edge, 'from_clause', '') or '')}
        return {'ok': True, 'kind': 'source_file', 'path': path,
                'source_plan': plan,
                'index': int(plan.index), 'total': 0,
                'who': f'第 {plan.start} 行',
                'text': plan.text,
                'preview': {},
                'archive_note': self._edge_archive_note(flow),
                'error': ''}

    def _edge_archive_note(self, flow: dict | None) -> str:
        """
        删这条控制流**会不会伤到存档**（S1 + 三层边界第①层）。

        · `call X from _call_Y`：`from` 就是**存档返回点**。删了它，玩家在 X
          里存的档回不到原处 —— Ren'Py 官方文档原话（label.html）：
          "An explicit label helps to ensure that saved games with return stacks
          can return to the proper place when loaded on a changed script."
        · `jump`：**没有**返回点身份 → 不提示（拍了 `D2`：
          假警报一多，真警报就没人看）。
        """
        if not flow or str(flow.get('type') or '') != 'call':
            return ''
        clause = str(flow.get('from_clause') or '').strip()
        target = str(flow.get('target') or '')
        if clause:
            return (f'\n\n⚠ 这是一条 call，它带着存档返回点「{clause}」。'
                    f'删掉之后，玩家在「{target}」里存的档就回不到原处了。')
        return (f'\n\n⚠ 这是一条 call（没有 from 返回点名）。'
                f'删掉之后，玩家在「{target}」里存的档可能回不到原处。')

    def delete_edge(self, eid):
        """
        删一条连线。

        两种边，两种语义（S1，2026-10-23）：

          · **草图**（`origin='manual'`）：剧本里本来就没有这一行 → 只删图；
          · **源边**（`origin='source'`）：它**就是**剧本里的那一行 →
            删边 = 删那一拍（内存），再让图边由拍重新派生。

        为什么以前"删了又弹回来"：从前这里只跑 `RemoveEdgeCommand`，
        删掉的只是**投影**；下次 `flow_edges` 从拍重新派生，线自然长回来。
        现在删的是本体，投影跟着没了 —— 弹不回来。
        """
        if self.graph is None or eid not in (self.graph.edges or {}):
            return
        edge = self.graph.edges[eid]
        plan = self.edge_delete_plan(edge)

        if plan.get('kind') == 'manual':
            from storygraph.graph.commands import RemoveEdgeCommand
            self._run_command(RemoveEdgeCommand(self, eid))
            self._status('已删掉这条草图（Ctrl+Z 可撤销）。剧本里本来就没有它。')
            return

        if plan.get('kind') == 'control':
            self._confirm_and_delete_control_edge(edge, plan)
            return

        if plan.get('kind') == 'control_branch':
            self._confirm_and_delete_control_branch(edge, plan)
            return

        if plan.get('kind') == 'source_file':
            self._confirm_and_delete_source_file_edge(edge, plan)
            return

        if not plan.get('ok'):
            self._status(plan.get('error') or '这条线删不了。')
            return

        idx = int(plan['index'])
        total = int(plan['total'])
        self._confirm_and_delete_source_edge(
            idx=idx, total=total, who=plan['who'], text=plan['text'],
            preview=plan['preview'], archive_note=plan['archive_note'])

    def _confirm_and_delete_source_file_edge(self, edge, plan) -> bool:
        """删跨章源边：只改那个源文件的物理行，不切当前章节。"""
        if not plan.get('ok'):
            self._status(plan.get('error') or '这条源边删不了。')
            return False
        sp = plan.get('source_plan')
        if sp is None:
            self._status('这条源边没有可用的删除计划。')
            return False
        if QMessageBox.question(
                self, '删除这条连线（另一章）',
                f'{Path(str(sp.path)).name} 第 {sp.start} 行：\n'
                f'{sp.text.strip()}\n\n'
                f'当前编辑器里打开的不是这一章；这次只删源文件这一行，'
                f'不切换章节、不改别的行。'
                f'{plan.get("archive_note") or ""}'
                f'\n\nCtrl+Z 可以把文件逐字节放回去。') != QMessageBox.Yes:
            return False
        from storygraph.graph.commands import DeleteSourceLineCommand
        cmd = DeleteSourceLineCommand(self, sp, edge.id)
        if not self._run_command(cmd):
            self._status(f'源文件没删成：{getattr(cmd, "error", "") or "未知原因"}')
            return False
        self._log(f'[删跨章源边] {Path(str(sp.path)).name} 第 {sp.start} 行 '
                  f'({sp.text.strip()})')
        self._status(f'✔ 已删掉 {Path(str(sp.path)).name} 第 {sp.start} 行'
                     f'（当前章节未切换，Ctrl+Z 可撤回）')
        return True

    def _confirm_and_delete_control_edge(self, edge, plan) -> bool:
        """删一条编排线：只删 `X_control` 里那一行（必要时补 return）。"""
        if not plan.get('ok'):
            self._status(plan.get('error') or '这条编排线删不了。')
            return False
        cplan = plan.get('control_plan')
        if cplan is None:
            self._status('这条编排线没有可用的删除计划。')
            return False
        tail = ''
        if getattr(cplan, 'new_lines', None):
            tail = f'\n\n删掉之后会补一行 `{cplan.new_lines[0].strip()}` 收尾。'
        if QMessageBox.question(
                self, '删除这条编排线',
                f'{plan.get("text") or ""}\n\n'
                f'只改编排层文件（章节剧本一个字不动）。'
                f'{tail}{plan.get("archive_note") or ""}'
                f'\n\nCtrl+Z 可以把文件逐字节放回去。') != QMessageBox.Yes:
            return False
        from storygraph.graph.commands import ControlRouteCommand
        cmd = ControlRouteCommand(
            self, cplan, mode='remove', from_id=edge.from_id,
            to_id=edge.to_id, etype=str(getattr(edge, 'type', 'jump') or 'jump'))
        if not self._run_command(cmd):
            self._status(f'编排层没删成：{getattr(cmd, "error", "") or "未知原因"}')
            return False
        path = Path(str(getattr(cplan, 'path', '') or ''))
        self._log(f'[删编排线] {cplan.control_label}：{cplan.old_route}')
        self._status(f'✔ 已删掉编排层里的 {cplan.old_route}'
                     f'（{path.name}；章节剧本未动，Ctrl+Z 可撤回）')
        return True

    def _confirm_and_delete_control_branch(self, edge, plan) -> bool:
        """删一条编排层分支：按标记段删（菜单组里只收缩那一项）。"""
        if not plan.get('ok'):
            self._status(plan.get('error') or '这条分支删不了。')
            return False
        bplan = plan.get('branch_plan')
        if bplan is None:
            self._status('这条分支没有可用的删除计划。')
            return False
        if QMessageBox.question(
                self, '删除这条分支',
                f'{plan.get("text") or ""}\n\n'
                f'只改编排层这一小段（章节剧本一个字不动）；'
                f'菜单组里最后一项目删掉时整个菜单会一起走。'
                f'\n\nCtrl+Z 可以把文件逐字节放回去。') != QMessageBox.Yes:
            return False
        from storygraph.graph.commands import RemoveControlBranchCommand
        cmd = RemoveControlBranchCommand(self, bplan, edge.id)
        if not self._run_command(cmd):
            self._status(f'编排层分支没删成：'
                         f'{getattr(cmd, "error", "") or "未知原因"}')
            return False
        path = Path(str(getattr(bplan, 'path', '') or ''))
        self._log(f'[删分支] {getattr(bplan, "control_label", "")}：'
                  f'{getattr(bplan, "line_no", "")}')
        self._status(f'✔ 已删掉编排层里的分支段'
                     f'（{path.name}；章节剧本未动，Ctrl+Z 可撤回）')
        return True

    def _confirm_and_delete_source_edge(self, idx, total, who, text,
                                        preview, archive_note, cascade=False):
        """
        确认 → 删拍 → 图边重新派生。`cascade=True` 时直接删整块（第二次确认后）。

        抽成独立方法是为了让"删整块"能递归复用（块头带块体时先删单拍会被拒）。
        """
        session = self._session
        if cascade:
            res = session.delete_beat(idx, cascade=True)
        else:
            head = (f'这条线对应第 {idx + 1} 拍（共 {total} 拍）。\n\n'
                    f'{who}：{text}')
            if QMessageBox.question(
                    self, '删除这条连线',
                    head + self._delete_line_note(preview) + archive_note
                    + '\n\n（只改内存里的章节；导出前不落盘，Ctrl+Z 可撤销）'
            ) != QMessageBox.Yes:
                return
            res = session.delete_beat(idx)

        if not res.get('ok') and res.get('can_cascade'):
            n = int(res.get('blocked_count') or 0)
            wide = session.delete_preview(idx, cascade=True)
            if QMessageBox.question(
                    self, '删除整块',
                    f'{res.get("error") or "这是块的开头"}'
                    f'\n\n要连着块体一起删掉吗？（共 {n + 1} 拍）'
                    + self._delete_line_note(wide)
            ) == QMessageBox.Yes:
                self._confirm_and_delete_source_edge(
                    idx=idx, total=total, who=who, text=text,
                    preview=wide, archive_note=archive_note, cascade=True)
            return

        if not res.get('ok'):
            self._status(res.get('error') or '删不掉这一拍。')
            return

        # 图边由拍重新派生：删掉的那一拍没了，线自然消失（不再弹回来）
        note = ''
        try:
            rebuilt = self._refresh_flow_edges()
            if rebuilt.get('removed'):
                note = f'，图上少了 {rebuilt["removed"]} 条线'
        except Exception as e:                # noqa: BLE001 - 图没同步不该拦删除
            swallow('ui_qt.window_nodes.delete_edge.refresh', e)
        self._log(f'[删边] 第 {idx + 1} 拍已删（{who}：{text}）{note}')
        self._status(f'已删掉第 {idx + 1} 拍{note}（Ctrl+Z 可撤销）。')

    def _delete_node(self, nid):
        if self.graph is None:
            return
        node = self.graph.nodes.get(nid)
        if node is None:
            return
        if QMessageBox.question(
                self, '删除块',
                f'删除「{node.label_name}」及其连线？（只改内存里的图，不动文件；'
                f'Ctrl+Z 可撤销）'
        ) != QMessageBox.Yes:
            return
        from storygraph.graph.commands import RemoveNodeCommand
        self._run_command(RemoveNodeCommand(self, nid))
