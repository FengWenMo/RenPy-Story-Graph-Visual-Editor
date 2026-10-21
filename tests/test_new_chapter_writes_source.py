"""
tests/test_new_chapter_writes_source.py
=======================================
「新建章节」必须让节点**有源文件**，否则双击后剧本根本编辑不了。

用户 2026-10-21 的原话（两张截图 + 一句话）：

    新建了个节点显示无源文件，这个新建章节我也没搞明白，
    怎么才能把无源搞成有源，不然打开章节剧本不能用！

    我想在一个 rpy 文件里写出第二个 label 该怎么操作？

根因：`add_node` 只在内存里加一个 `Node`，`file_path` 是空的 ——
卡片页那边就显示"（无源文件）"，编辑器没有章节可载入。

改法：对话框多一格「写入文件」，默认为 `<项目根>/game/<章节名>.rpy`；
创建时把最小骨架写出来，节点带上 `file_path`。那一格**填已有文件**时
往末尾追加一个新 label（一个文件放多章是合法的，用户问的就是这个）。

判据（都看**磁盘上的字节**，不看回执）：
  1. 文件不存在 → 建出 `label X:` + `return`，节点拿得到路径；
  2. 文件已存在 → **追加**，原有内容一个字节不动，载体风格（CRLF/BOM）保住；
  3. 里面已有同名 label → 拒绝，文件不变（重名 label 会让 Ren'Py 起不来）；
  4. 那一格留空 → 不建文件（旧行为，纯结构图）；
  5. 对话框：默认路径跟着章节名走，但用户手改过之后就不再跟；
  6. `_has_label` 的边界：注释里的不算、前缀不误伤、带参数的认。
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from storygraph.ui_qt.window_nodes import _has_label, _write_new_chapter  # noqa: E402


class _View:
    """只记状态栏说了什么 —— `_write_new_chapter` 只用到这一个成员。"""

    def __init__(self):
        self.msgs = []

    def _status(self, msg):
        self.msgs.append(str(msg))


class TestWritesOrAppends:
    def test_new_file_gets_the_skeleton(self, tmp_path):
        target = tmp_path / 'game' / 'sss.rpy'
        wrote = _write_new_chapter(_View(), str(target), 'sss')
        assert wrote == str(target)
        assert target.read_text(encoding='utf-8') == 'label sss:\n    return\n'

    def test_existing_file_gets_a_second_label_appended(self, tmp_path):
        """用户问的那条：一个 .rpy 里写第二个 label。"""
        target = tmp_path / 'game' / 'chapter1_3.rpy'
        target.parent.mkdir(parents=True)
        # 故意用 CRLF —— 追加时**不许**把整个文件改成 LF
        target.write_bytes(
            ('# 第一章\r\nlabel chapter1_1:\r\n    e "你好"\r\n'
             '    return\r\n').encode('utf-8'))
        before = target.read_bytes()

        wrote = _write_new_chapter(_View(), str(target), 'chapter1_2')
        assert wrote == str(target)

        after = target.read_bytes()
        # 原有内容逐字节不动（只允许末尾多了东西）
        assert after.startswith(before.rstrip(b'\r\n'))
        # 新 label 进来了
        assert b'label chapter1_2:' in after
        # 载体风格没变：整份仍是 CRLF，且没有混进裸 LF
        lf = after.count(b'\n') - after.count(b'\r\n')
        assert lf == 0, f'追加之后混进了 {lf} 个裸 LF'

    def test_bom_is_preserved(self, tmp_path):
        target = tmp_path / 'game' / 'bom.rpy'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'\xef\xbb\xbflabel a:\r\n    return\r\n')
        _write_new_chapter(_View(), str(target), 'b')
        raw = target.read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf'), 'BOM 被吃掉了'
        assert raw.count(b'\xef\xbb\xbf') == 1, 'BOM 被重复插入'
        assert b'label b:' in raw

    def test_duplicate_label_is_refused_and_file_untouched(self, tmp_path):
        target = tmp_path / 'game' / 'dup.rpy'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'label sss:\r\n    return\r\n')
        before = target.read_bytes()

        view = _View()
        assert _write_new_chapter(view, str(target), 'sss') == ''
        assert target.read_bytes() == before, '重名被拒却还是改了文件'
        assert any('已经有' in m for m in view.msgs), view.msgs

    def test_empty_path_writes_nothing(self, tmp_path):
        assert _write_new_chapter(_View(), '', 'sss') == ''
        assert list(tmp_path.rglob('*.rpy')) == []


class TestDialogDefaults:
    def test_default_file_follows_the_name(self, tmp_path, qapp):
        from storygraph.ui_qt.dialogs import AddNodeDialog
        dlg = AddNodeDialog(None, existing=(), default_dir=str(tmp_path / 'game'))
        dlg._name.setText('chapter_02')
        assert dlg._file.text().endswith('chapter_02.rpy')
        assert str(tmp_path / 'game') in dlg._file.text()
        dlg._name.setText('chapter_03')
        assert dlg._file.text().endswith('chapter_03.rpy')

    def test_hand_edited_path_stops_following(self, tmp_path, qapp):
        from storygraph.ui_qt.dialogs import AddNodeDialog
        dlg = AddNodeDialog(None, existing=(), default_dir=str(tmp_path / 'game'))
        dlg._name.setText('a')
        dlg._file.setText(str(tmp_path / 'somewhere' / 'custom.rpy'))
        dlg._name.setText('b')
        assert dlg._file.text().endswith('custom.rpy'), \
            '用户手改过之后，章节名不该再改它'

    def test_duplicate_label_in_existing_file_is_rejected(self, tmp_path, qapp):
        from storygraph.ui_qt.dialogs import AddNodeDialog
        target = tmp_path / 'game' / 'ch.rpy'
        target.parent.mkdir(parents=True)
        target.write_text('label sss:\n    return\n', encoding='utf-8')

        dlg = AddNodeDialog(None, existing=(), default_dir=str(tmp_path / 'game'))
        dlg._name.setText('sss')
        dlg._file.setText(str(target))
        dlg._on_accept()
        assert dlg.result() != 1, '同名 label 竟然被接受'
        assert dlg.name == ''


class TestHasLabelBoundaries:
    @pytest.mark.parametrize('text,label,expected', [
        ('label a:\n', 'a', True),
        ('label a(x=1):\n', 'a', True),          # 带参数的 label
        ('# label a:\n', 'a', False),            # 注释里的不算
        ('label ab:\n', 'a', False),             # 前缀不误伤
        ('label  a:\n', 'a', True),              # 多个空格
        ('    label a:\n', 'a', False),          # 缩进的不算（块内）
    ])
    def test_boundaries(self, text, label, expected):
        assert _has_label(text, label) is expected
