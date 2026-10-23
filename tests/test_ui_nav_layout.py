"""
参考界面的三栏骨架（体验优化第十一阶段，2026-10-23）
====================================================

用户 2026-10-23 给了一张参考截图：

> 我看类似于这样的 UI 布局就非常不错！参考学习调整 UI 方面的内容！

那一版骨架是三件事：**顶部模式标签 / 左侧章节树 + 搜索 / 右栏检查器**，
中间是"带行号和类型牌的正文"。这一份测的就是这三处**真的接上了**，
以及它们各自不许违反的那几条：

  · `app.chapter_index`：把项目扫成目录（**没 label 的文件不列**，
    读不出来的记进 `errors`，不假装它不存在）；
  · `ui_qt.shot_list.ShotDelegate`：**只换画法不换控件** ——
    `item.text()` / `setCurrentRow` / `Qt.UserRole` 里的拍号一个都没动；
  · `ui_qt.navigator.ChapterNavigator`：双击才打开（单击只选中）、
    搜索过滤、`●` = 已在图里、当前章节加粗；
  · `ui_qt.inspector.PropertyStrip`：空值那行不显示，长值省略但能复制全文；
  · `window_nav`：模式条与 `_set_page` **双向**同步、导航换章不重读
    同一份文件、换章前把上一章的改动抄进快照。

编辑语义（载入 / 导出 / 增删拍）不在这里重测 —— 那是 `test_ui_qt` /
`test_autosave` / `test_card_*` 的活。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip('PySide6')

from PySide6.QtCore import QRect, Qt                    # noqa: E402
from PySide6.QtGui import QFont, QPainter, QPixmap     # noqa: E402
from PySide6.QtWidgets import (QApplication, QListWidget,        # noqa: E402
                               QListWidgetItem, QStyle,
                               QStyleOptionViewItem)

from storygraph.app import chapter_index as CI          # noqa: E402
from storygraph.parsing import discover                 # noqa: E402


# ===========================================================================
#  一、项目目录（纯数据层，不碰 Qt）
# ===========================================================================

def _project(tmp_path, files: dict):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    for rel, text in files.items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding='utf-8')
    return root


class TestChapterIndex:
    def test_lists_labels_with_line_numbers(self, tmp_path):
        root = _project(tmp_path, {
            'game/script/ch01.rpy':
                'label start:\n    "甲"\n\nlabel second:\n    "乙"\n',
        })
        idx = CI.build_index(root)
        assert idx.file_count() == 1
        assert idx.label_count() == 2
        f = idx.files[0]
        assert f.rel == 'game/script/ch01.rpy'
        assert f.label_names() == ('start', 'second')
        assert f.labels[1].line == 4, f.labels

    def test_files_without_label_are_not_listed(self, tmp_path):
        """`options.rpy` / `gui.rpy` 这类没有章的文件列出来只是噪音。"""
        root = _project(tmp_path, {
            'game/options.rpy': 'init python:\n    pass\n',
            'game/gui.rpy': 'init -2:\n    define gui.accent = "#fff"\n',
            'game/script/ch01.rpy': 'label start:\n    "甲"\n',
        })
        idx = CI.build_index(root)
        assert [f.name for f in idx.files] == ['ch01.rpy']
        assert idx.scanned == 3
        assert idx.skipped_without_label() == 2

    def test_generated_and_translation_files_are_ignored(self, tmp_path):
        root = _project(tmp_path, {
            'game/script/ch01.generated.rpy': 'label gen:\n    "甲"\n',
            'game/tl/english/ch01.rpy': 'label start:\n    "甲"\n',
            'game/script/ch01.rpy': 'label start:\n    "甲"\n',
        })
        idx = CI.build_index(root)
        assert [f.name for f in idx.files] == ['ch01.rpy']

    def test_search_matches_label_and_keeps_only_hits(self, tmp_path):
        root = _project(tmp_path, {
            'game/script/ch01.rpy':
                'label ch01:\n    "甲"\n\nlabel ch01_roof:\n    "乙"\n',
            'game/script/ch02.rpy': 'label ch02:\n    "丙"\n',
        })
        idx = CI.build_index(root)
        hits = idx.search('roof')
        assert len(hits) == 1
        assert hits[0].file.name == 'ch01.rpy'
        assert [l.name for l in hits[0].labels] == ['ch01_roof']

    def test_search_by_file_name_keeps_all_its_labels(self, tmp_path):
        """搜文件名要看到"这个文件里有什么"，不是一条空结果。"""
        root = _project(tmp_path, {
            'game/script/ch02.rpy': 'label a:\n    "甲"\n\nlabel b:\n    "乙"\n',
        })
        idx = CI.build_index(root)
        hits = idx.search('CH02')                      # 大小写不敏感
        assert len(hits) == 1
        assert [l.name for l in hits[0].labels] == ['a', 'b']

    def test_no_match_is_empty_not_error(self, tmp_path):
        root = _project(tmp_path, {'game/a.rpy': 'label a:\n    "甲"\n'})
        idx = CI.build_index(root)
        assert idx.search('找不到的词') == []

    def test_empty_root_is_falsy(self):
        idx = CI.build_index('')
        assert not idx
        assert idx.file_count() == 0

    def test_missing_root_is_reported(self, tmp_path):
        idx = CI.build_index(tmp_path / '不存在')
        assert not idx
        assert idx.errors and '目录不存在' in idx.errors[0][1]

    def test_find_label_by_relative_path(self, tmp_path):
        root = _project(tmp_path, {'game/script/ch01.rpy':
                                   'label start:\n    "甲"\n'})
        idx = CI.build_index(root)
        got = idx.find_label('game/script/ch01.rpy', 'start')
        assert got is not None and got.line == 1
        assert idx.find_label('game/script/ch01.rpy', '没有这个') is None

    def test_signature_changes_when_file_changes(self, tmp_path):
        root = _project(tmp_path, {'game/a.rpy': 'label a:\n    "甲"\n'})
        idx = CI.build_index(root)
        before = idx.signature()
        fp = root / 'game/a.rpy'
        fp.write_text('label a:\n    "甲"\n\nlabel b:\n    "乙"\n',
                      encoding='utf-8')
        import os
        os.utime(fp, (before[0][1] + 5, before[0][1] + 5))
        assert CI.build_index(root).signature() != before

    def test_label_lines_in_file_matches_labels_in_file(self, tmp_path):
        """两个函数必须认同一批 label（不然"列得出却打不开"）。"""
        fp = _project(tmp_path, {'game/a.rpy':
                                 'label start:\n    "甲"\nlabel start:\n'
                                 '    "重名只留第一处"\nlabel b:\n    "乙"\n'}
                      ) / 'game/a.rpy'
        assert discover.labels_in_file(fp) == ['start', 'b']
        assert [n for n, _l in discover.label_lines_in_file(fp)] == ['start', 'b']


# ===========================================================================
#  二、剧本版画法（delegate）
# ===========================================================================

class TestShotDelegatePainting:
    @pytest.fixture(autouse=True)
    def _needs_app(self, qapp):
        """**必须先有 QApplication**：没有它建 QListWidget 是直接 abort
        （不是抛异常 —— 整个 pytest 进程会消失，看不到任何 traceback）。"""
        return qapp

    def _list(self, titles=('001',), datas=(0,)):
        widget = QListWidget()
        for text, data in zip(titles, datas):
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, data)
            widget.addItem(item)
        widget.resize(320, 120)
        return widget

    def _paint(self, widget, delegate, row=0, selected=False):
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 320, 44)
        opt.font = QFont('Arial', 9)
        opt.state = QStyle.State_Enabled
        if selected:
            opt.state |= QStyle.State_Selected
        pix = QPixmap(320, 44)
        painter = QPainter(pix)
        try:
            delegate.paint(painter, opt, widget.model().index(row, 0))
        finally:
            painter.end()
        return pix

    def test_badge_for_each_kind(self):
        from storygraph.ui_qt.shot_list import badge_of
        assert badge_of({'text': '甲'})[0] == '对白'
        assert badge_of({'prefix': '☁'})[0] == '旁白'
        assert badge_of({'prefix': '⚑'})[0] == '分支'
        assert badge_of({'prefix': '➜'})[0] == '选项'
        assert badge_of({'statement': 'play_music'})[0] == '语句'
        assert badge_of({'flow_type': 'call'})[0] == '调用'
        assert badge_of({'flow_type': 'jump'})[0] == '跳转'

    def test_row_text_puts_speaker_first(self):
        from storygraph.ui_qt.shot_list import row_text_of
        head, tail = row_text_of({'who': 'e', 'text': '早上好'})
        assert head == 'e：'
        assert '早上好' in tail
        head, tail = row_text_of({'statement': 'play_music',
                                  'statement_head': 'play_music bgm',
                                  'statement_label': '播放音乐'})
        assert head.startswith('play_music')
        assert 'bgm' in tail

    def test_entry_provider_is_used_and_missing_entry_falls_back(self):
        from storygraph.ui_qt.shot_list import ShotDelegate

        seen = []

        def entry_of(idx):
            seen.append(idx)
            return {'who': 'e', 'text': f'第{idx}句'}

        widget = self._list(('原始文字',), (2,))
        delegate = ShotDelegate(widget, entry_of=entry_of)
        widget.setItemDelegate(delegate)
        self._paint(widget, delegate)
        assert seen == [2], seen

        empty = ShotDelegate(widget, entry_of=lambda idx: {})
        self._paint(widget, empty)             # 不抛 = 退回原来的字
        assert widget.item(0).text() == '原始文字'

    def test_row_heights_differ_for_group_and_beat(self):
        from storygraph.ui_qt.shot_list import GROUP_H, ROW_H, ShotDelegate

        widget = self._list(('▾ ch01　（3 拍）', '222'), (-1, 0))
        delegate = ShotDelegate(widget, entry_of=lambda i: {'text': 'x'})
        widget.setItemDelegate(delegate)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 320, 44)
        group = delegate.sizeHint(opt, widget.model().index(0, 0))
        beat = delegate.sizeHint(opt, widget.model().index(1, 0))
        assert group.height() == GROUP_H
        assert beat.height() == ROW_H

    def test_item_text_and_role_survive_painting(self):
        """**只换画法**：原来那套"行号 == 拍号 / 行文本"的判据必须还在。"""
        from storygraph.ui_qt.shot_list import ShotDelegate

        widget = self._list(('3 e：早上好',), (2,))
        delegate = ShotDelegate(widget, entry_of=lambda i: {'who': 'e',
                                                            'text': '早上好'})
        widget.setItemDelegate(delegate)
        self._paint(widget, delegate, selected=True)
        assert widget.item(0).text() == '3 e：早上好'
        assert widget.item(0).data(Qt.UserRole) == 2

    def test_blank_width_does_not_crash(self):
        """窗口还没布局（宽度 0）时也要画得出来 —— 启动瞬间就是这个状态。"""
        from storygraph.ui_qt.shot_list import ShotDelegate

        widget = self._list(('1 e：甲',), (0,))
        delegate = ShotDelegate(widget, entry_of=lambda i: {'who': 'e',
                                                            'text': '甲'})
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 0, 0)
        pix = QPixmap(1, 1)
        painter = QPainter(pix)
        try:
            delegate.paint(painter, opt, widget.model().index(0, 0))
        finally:
            painter.end()


# ===========================================================================
#  三、章节导航
# ===========================================================================

@pytest.fixture
def nav(qapp):
    from storygraph.ui_qt.navigator import ChapterNavigator
    widget = ChapterNavigator()
    yield widget
    widget.deleteLater()


@pytest.fixture
def nav_root(tmp_path):
    return _project(tmp_path, {
        'game/script/ch01.rpy':
            'label ch01:\n    "甲"\n\nlabel ch01_roof:\n    "乙"\n',
        'game/script/ch02.rpy': 'label ch02:\n    "丙"\n',
        'game/options.rpy': 'init python:\n    pass\n',
    })


class TestChapterNavigator:
    def test_tree_lists_files_then_labels(self, nav, nav_root):
        nav.set_project_root(nav_root)
        nav.refresh()
        rows = nav.visible_rows()
        assert rows[0][1].endswith('ch01.rpy')       # 行首有折叠箭头 `▾ `
        assert [r[1] for r in rows[1:3]] == ['ch01', 'ch01_roof']
        assert any(r[1].endswith('ch02.rpy') for r in rows)

    def test_search_filters_and_shows_hits_expanded(self, nav, nav_root):
        nav.set_project_root(nav_root)
        nav.refresh()
        nav._search.setText('roof')
        nav._rebuild()                       # 跳过 120ms 防抖（直接重建）
        rows = nav.visible_rows()
        assert len(rows) == 2
        assert rows[0][1].endswith('ch01.rpy')
        assert rows[1][1] == 'ch01_roof'

    def test_search_with_no_hit_shows_how_to_get_back(self, nav, nav_root):
        nav.set_project_root(nav_root)
        nav.refresh()
        nav._search.setText('不存在的')
        nav._rebuild()
        assert nav.visible_rows() == []
        assert '清空搜索框' in nav._hint.text()

    def test_no_project_hint_says_what_to_do(self, nav):
        assert '打开项目' in nav._hint.text()

    def test_double_click_opens_label(self, nav, nav_root):
        got = []
        nav.open_requested.connect(lambda p, l: got.append((Path(p).name, l)))
        nav.set_project_root(nav_root)
        nav.refresh()
        label_item = nav._tree.topLevelItem(0).child(1)
        nav._on_activated(label_item)
        assert got == [('ch01.rpy', 'ch01_roof')]

    def test_clicking_file_row_toggles_instead_of_opening(self, nav, nav_root):
        """文件行单击 = 折叠/展开；**不许**顺手把当前章节换掉。"""
        got = []
        nav.open_requested.connect(lambda p, l: got.append(l))
        nav.set_project_root(nav_root)
        nav.refresh()
        file_item = nav._tree.topLevelItem(0)
        assert file_item.isExpanded()
        nav._on_item_clicked(file_item)
        assert file_item.isExpanded() is False
        assert file_item.text(0).startswith('▸')
        nav._on_item_clicked(file_item)
        assert file_item.isExpanded() is True
        assert got == []

    def test_file_row_shows_a_fold_arrow(self, nav, nav_root):
        """QSS 把 QTreeView 的分支箭头吞掉了 —— 箭头必须自己在文字里。"""
        nav.set_project_root(nav_root)
        nav.refresh()
        text = nav._tree.topLevelItem(0).text(0)
        assert text.startswith(('▾', '▸')), text

    def test_imported_labels_get_a_dot(self, nav, nav_root):
        nav.set_project_root(nav_root)
        nav.refresh()
        nav.set_imported({'ch01'})
        file_item = nav._tree.topLevelItem(0)
        assert '●' in file_item.text(0)
        assert file_item.child(0).text(0).startswith('●')
        assert not file_item.child(1).text(0).startswith('●')

    def test_current_label_is_bold_and_reported(self, nav, nav_root):
        nav.set_project_root(nav_root)
        nav.refresh()
        nav.set_current(str(nav_root / 'game/script/ch01.rpy'), 'ch01_roof')
        item = nav._tree.topLevelItem(0).child(1)
        assert item.font(0).bold()
        assert 'ch01.rpy' in nav._now.text() and 'ch01_roof' in nav._now.text()

    def test_collapsed_file_stays_collapsed_across_refresh(self, nav, nav_root):
        nav.set_project_root(nav_root)
        nav.refresh()
        item = nav._tree.topLevelItem(0)
        item.setExpanded(False)                # 触发 itemCollapsed
        nav.refresh()
        assert nav._tree.topLevelItem(0).isExpanded() is False

    def test_clear_forgets_project(self, nav, nav_root):
        nav.set_project_root(nav_root)
        nav.refresh()
        assert nav.visible_rows()
        nav.clear()
        assert nav.visible_rows() == []
        assert '打开项目' in nav._hint.text()


# ===========================================================================
#  四、右栏属性表
# ===========================================================================

class TestPropertyStrip:
    @pytest.fixture
    def strip(self, qapp):
        from storygraph.ui_qt.inspector import PropertyStrip
        widget = PropertyStrip()
        yield widget
        widget.deleteLater()

    def test_rows_are_readable(self, strip):
        strip.set_rows([('文件', 'ch01.rpy'), ('label', '序章'),
                        ('源位置', '.rpy 第 12 行')])
        assert strip.row_count() == 3
        assert strip.texts()['label'] == '序章'

    def test_empty_values_are_not_shown(self, strip):
        strip.set_rows([('文件', 'ch01.rpy'), ('说话人', ''),
                        ('源位置', '—')])
        assert strip.row_count() == 1
        assert '说话人' not in strip.texts()

    def test_set_empty_clears_rows(self, strip):
        strip.set_rows([('文件', 'ch01.rpy')])
        strip.set_empty('还没载入章节')
        assert strip.row_count() == 0
        assert strip._empty.isVisible() is False    # 没显示过 → 还没排上屏

    def test_long_value_is_elided_but_full_text_is_kept(self, qapp, strip):
        long = 'game/script/chapter/very/deep/path/ch01.rpy'
        strip.set_rows([('文件', long)])
        strip.resize(140, 60)
        strip.show()
        qapp.processEvents()
        label = strip._rows[0][1]
        assert label.full_text() == long
        assert label.toolTip() == long
        strip.hide()


# ===========================================================================
#  五、窗口接线
# ===========================================================================

class TestWindowWiring:
    def _with_nav(self, win):
        win._nav.set_project_root(win._project_root)
        win.refresh_chapter_nav()
        return win

    def test_nav_is_a_left_dock_on_both_pages(self, ui_window):
        assert ui_window._nav_dock is not None
        assert ui_window.dockWidgetArea(ui_window._nav_dock) \
            == Qt.LeftDockWidgetArea
        assert ui_window._nav.parent() is ui_window._nav_dock

    def test_nav_lists_the_loaded_project(self, ui_window):
        self._with_nav(ui_window)
        rows = ui_window._nav.visible_rows()
        assert any(r[1].endswith('ch01.rpy') for r in rows)
        assert any(r[1] == 'ch01_roof' for r in rows)

    def test_mode_tabs_and_pages_stay_in_sync(self, ui_window):
        assert ui_window._mode_tabs.count() == 2
        ui_window._mode_tabs.setCurrentIndex(1)
        assert ui_window._page == ui_window.PAGE_CARD
        ui_window._set_page(ui_window.PAGE_GRAPH)
        assert ui_window._mode_tabs.currentIndex() == 0
        assert ui_window._stack.currentIndex() == 0

    def test_open_another_label_in_same_file_does_not_reload(self, ui_window):
        """同一个文件里换 label = **跳拍**（重读会把没导出的改动冲掉）。"""
        before = ui_window._session.beats
        ui_window.open_chapter_at(ui_window._session.script_path, 'ch01_roof')
        assert ui_window._session.beats is before
        assert ui_window._loaded_idx >= 0
        idx = ui_window._loaded_idx
        assert ui_window._session.beats[idx].label == 'ch01_roof'
        assert ui_window._page == ui_window.PAGE_CARD

    def test_open_another_file_loads_it(self, ui_window, ui_project):
        other = ui_project / 'game' / 'script' / 'ch02.rpy'
        other.write_text('label ch02:\n    "另一章"\n', encoding='utf-8')
        self._with_nav(ui_window)
        ui_window.open_chapter_at(other, 'ch02')
        assert Path(ui_window._session.script_path).name == 'ch02.rpy'
        assert ui_window._page == ui_window.PAGE_CARD
        assert ui_window._session.beats[0].label == 'ch02'

    def test_switching_with_unsaved_edits_snapshots_first(self, ui_window,
                                                          ui_project,
                                                          _no_real_autosave):
        """切走 = 不弹窗，但**必须**先抄一份快照（丢掉也捡得回来）。"""
        from storygraph.core import autosave as AS

        AS.set_enabled(True)
        ui_window._show_index(0)
        ui_window._panel._text.setPlainText('改了一句没导出的话')
        ui_window.apply_current_beat()
        assert ui_window._session.has_unsaved_edits(), '前置：确实脏了'

        other = ui_project / 'game' / 'script' / 'ch02.rpy'
        other.write_text('label ch02:\n    "另一章"\n', encoding='utf-8')
        ui_window.open_chapter_at(other, 'ch02')
        # 快照按**章**存：换章之后 `autosave_entries()` 只看当前这一章，
        # 所以这里直接问项目里有哪些快照（上一章那份必须还在）。
        entries = AS.candidates(ui_window._project_root)
        assert entries, '换章前没有抄快照 —— 上一章的改动会真的丢'
        assert any('ch01' in str(e.get('chapter') or '') for e in entries), entries

    def test_inspector_shows_where_this_shot_lives(self, ui_window):
        ui_window._show_index(1)
        texts = ui_window._inspector.texts()
        assert texts['文件'] == 'ch01.rpy'
        assert texts['label']
        assert texts['这一拍'].startswith('2 / ')
        assert texts['源位置'].startswith('.rpy 第')
        ui_window._show_index(0)
        assert ui_window._inspector.texts()['这一拍'].startswith('1 / ')

    def test_first_shot_is_not_treated_as_no_chapter(self, ui_window):
        """**第 0 拍是 0** —— `0 or -1` 会把它当成"没载入"（第十阶段踩过）。"""
        ui_window._show_index(0)
        assert '第 1 拍' in ui_window._mode_info.text()
        ui_window._sync_inspector()          # 不带参数 = 读 `_loaded_idx`
        assert ui_window._inspector.texts()['这一拍'].startswith('1 / ')

    def test_inspector_is_empty_before_loading(self, qapp):
        from storygraph.ui_qt.main_window import MainWindowQt

        class _E:
            project_root = None

            def get_registry(self):
                return None

        win = MainWindowQt(_E())
        assert win._inspector.row_count() == 0
        win.close()

    def test_nav_marks_follow_the_graph(self, ui_window):
        from storygraph.graph.models import Graph

        graph = Graph()
        graph.add_node('ch01', 10, 10)
        ui_window._set_graph(graph)
        ui_window._nav.set_project_root(ui_window._project_root)
        ui_window.refresh_chapter_nav()
        assert ui_window._nav._tree.topLevelItem(0).child(0).text(0).startswith('●')

    def test_dock_state_is_saved_in_the_layout(self, ui_window):
        blob = ui_window._dock_state_blob()
        assert blob, '没有 dock 状态 —— 左侧那一栏的宽度/开关不会被记住'
        layout = ui_window.current_layout()
        assert layout.get('dock_state') == blob

    def test_dock_state_can_be_restored(self, ui_window):
        blob = ui_window._dock_state_blob()
        ui_window._nav_dock.hide()
        ok = ui_window._restore_docks({'dock_state': blob})
        assert ok
        # 窗口本身没 show（offscreen 测试），所以判 `isHidden()` 而不是 `isVisible()`
        assert ui_window._nav_dock.isHidden() is False
        assert ui_window._nav_action.isChecked() is True
        ui_window._nav_dock.hide()
        assert ui_window._nav_action.isChecked() is False

    def test_broken_dock_state_is_ignored(self, ui_window):
        assert ui_window._restore_docks({'dock_state': '这不是 base64'}) is False
        assert ui_window._restore_docks({}) is False
