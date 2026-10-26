"""
「🧹 清理导入残留」弹窗判据（离线 Qt）
=====================================

`ui_qt/dialog_cleanup.py` 是用户 2026-10-26 那句"我一个个删 vscode 里面的
.bak 和 blocked 里面的文件老狼狈了……"的 UI 出口。这里（offscreen 跑真 Qt）
钉住三条：

  ① **默认勾选是安全的**：`.rpy.bak`（可能是手写原件唯一旧版本）**默认不勾**，
     只有"确定可以丢"的三类才默认勾上；
  ② **勾了才删、删了才关窗**：一个都没勾 → 红字提示、不关窗；
  ③ **资源表备份绝不动**（`*.bak.xlsx` 既不列也不删）。

扫描/删除的判据本身在 `tests/test_import_cleanup.py`，这里只测界面接线。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('PySide6')


def _project(tmp_path: Path) -> Path:
    """一个带四类残留 + 一份"绝不能删"的资源表备份的最小项目。"""
    root = tmp_path / 'proj'
    game = root / 'game'
    chapter = game / 'chapter'
    chapter.mkdir(parents=True)
    (game / 'asset_tables').mkdir(parents=True)
    # 四类残留
    (chapter / 'chapter1_1.rpy.bak').write_text('旧的一版\n', encoding='utf-8')
    (chapter / 'chapter1_1.rpy.bak-20261026-210509-123456').write_text(
        '更旧的一版\n', encoding='utf-8')
    (game / 'chapter0_0.bak.rpy').write_text('历史遗留\n', encoding='utf-8')
    (root / 'build' / 'review').mkdir(parents=True)
    (root / 'build' / 'review' / 'chapter2_1.blocked.rpy').write_text(
        '被门禁拦下的产物\n', encoding='utf-8')
    # ⚠ 资源表备份：唯一不能删的东西
    (game / 'asset_tables' / 'characters.xlsx.bak').write_bytes(b'\x00xlsx')
    # 正常脚本
    (chapter / 'chapter1_1.rpy').write_text('label chapter1_1:\n', encoding='utf-8')
    return root


@pytest.fixture
def dialog(qapp, tmp_path):
    from storygraph.ui_qt.dialog_cleanup import ImportJunkDialog
    root = _project(tmp_path)

    def _make(**kw):
        return ImportJunkDialog(None, root, **kw)
    _make.root = root
    return _make


def test_counts_and_kind_labels_are_visible(dialog):
    dlg = dialog()
    assert dlg.summary['total'] == 4
    texts = [dlg._boxes[k].text() for k in dlg._boxes]
    assert any('时间戳备份' in t and '1 个' in t for t in texts), texts
    assert any('对照副本' in t and '1 个' in t for t in texts), texts
    assert any('自动备份' in t and '1 个' in t for t in texts), texts
    assert any('历史遗留' in t and '1 个' in t for t in texts), texts


def test_the_precious_bak_is_not_checked_by_default(dialog):
    """`.rpy.bak` 可能是覆盖手写文件前**唯一**的旧版本 → 默认不勾。"""
    from storygraph.authoring import import_cleanup as IC
    dlg = dialog()
    assert dlg._boxes[IC.KIND_BAK].isChecked() is False
    for kind in (IC.KIND_BAK_STAMPED, IC.KIND_BLOCKED, IC.KIND_BAK_RPY):
        assert dlg._boxes[kind].isChecked() is True
    kinds = {i.kind for i in dlg.selected_files()}
    assert IC.KIND_BAK not in kinds
    # 提示语要写清楚为什么（否则用户会以为这是漏勾）
    from PySide6.QtWidgets import QLabel
    warns = [w.text() for w in dlg.findChildren(QLabel)
             if w.objectName() == 'junkBakWarning']
    assert warns and '唯一' in warns[0], warns


def test_preview_lists_relative_paths_of_selected_kinds(dialog):
    dlg = dialog()
    text = dlg.preview.toPlainText()
    assert 'game\\chapter\\chapter1_1.rpy.bak-20261026-210509-123456' in text
    assert 'build\\review\\chapter2_1.blocked.rpy' in text
    # 没勾的那类不该出现在预览里
    assert 'chapter1_1.rpy.bak\n' not in text


def test_checking_the_bak_adds_it_back(dialog):
    from storygraph.authoring import import_cleanup as IC
    dlg = dialog()
    dlg._boxes[IC.KIND_BAK].setChecked(True)
    names = {i.path.name for i in dlg.selected_files()}
    assert 'chapter1_1.rpy.bak' in names
    assert '删除选中的 4 个' in dlg._ok.text()


def test_deleting_removes_only_checked_junk_and_keeps_the_xlsx_backup(dialog):
    from storygraph.authoring import import_cleanup as IC
    dlg = dialog()
    dlg._boxes[IC.KIND_BAK].setChecked(True)      # 这次连 .rpy.bak 一起删
    dlg._on_accept()
    root = dialog.root
    assert len(dlg.deleted) == 4, dlg.failed
    assert Path(root / 'game' / 'asset_tables' / 'characters.xlsx.bak').is_file()
    assert Path(root / 'game' / 'chapter' / 'chapter1_1.rpy').is_file()
    assert not list(root.rglob('*.rpy.bak*'))
    assert not list((root / 'build' / 'review').glob('*.blocked.rpy'))


def test_nothing_checked_is_refused_without_closing(dialog):
    from storygraph.authoring import import_cleanup as IC
    dlg = dialog()
    for box in dlg._boxes.values():
        box.setChecked(False)
    assert dlg.selected_files() == []
    dlg._on_accept()
    # ⚠ 判"显示没显示"用 `isHidden()`：对话框没 exec 时子控件 `isVisible` 恒 False
    assert not dlg._error.isHidden()
    assert '什么都没勾选' in dlg._error.text()
    # 一个文件都没动
    assert dialog.root.joinpath('game', 'chapter', 'chapter1_1.rpy.bak').is_file()


def test_an_empty_project_says_so_and_disables_delete(qapp, tmp_path):
    from storygraph.ui_qt.dialog_cleanup import ImportJunkDialog
    root = tmp_path / 'empty'
    (root / 'game').mkdir(parents=True)
    dlg = ImportJunkDialog(None, root)
    assert dlg.summary['total'] == 0
    assert dlg.selected_files() == []
    assert dlg._ok.isEnabled() is False
