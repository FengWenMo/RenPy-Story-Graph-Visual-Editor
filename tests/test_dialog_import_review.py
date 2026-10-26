"""
冲突预检弹窗判据（离线 Qt）
============================

`ui_qt/dialog_import_review.py` 是用户 2026-10-26 要的那个"撞 label 时先问一声"
的弹窗。这里**不起真窗口**（offscreen 跑真 Qt），只钉住它的命门：

  ① **默认值 = 不改变现状**（工具生成的可覆盖 → 预选覆盖；手写的 → 预选跳过）；
  ② **看得见处境**：撞上了哪个文件、旧内容 vs 新内容差在哪行、覆盖要丢什么；
  ③ **点得动**：逐份选三种动作 + 一键全部覆盖 / 全部改名 / 全部跳过；
  ④ **不静默出错**：新名字不合法 / 已被占用 / 两行撞同一名字 → 就地红字，
     窗口**不关**，人改完还能继续；
  ⑤ **只在弹窗里做决定**：`choices()` 返回 payload，弹窗自己不写盘。

真正"谁撞了谁 / 三种动作各写到哪 / 名字合不合法"的逻辑不在这里测 ——
它在 `test_import_review.py`（`storygraph/authoring/import_review.py`）。
本文件的场景**用真流水线搭**（`txt_pipeline.build_plan` + `preflight`），
与真机同一条路。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('PySide6')

from PySide6.QtGui import QColor                                        # noqa: E402
from PySide6.QtWidgets import QDialog                                   # noqa: E402

from storygraph.assets.asset_registry import (_TABLE_FIELDS,            # noqa: E402
                                              write_dict_to_excel)
from storygraph.authoring import import_review as IR                    # noqa: E402
from storygraph.authoring import txt_pipeline as TP                    # noqa: E402
from storygraph.core.vocabulary import GENERATOR_MARKER                # noqa: E402
from storygraph.ui_qt import tokens as T                                # noqa: E402

#: 角色表（只要够转换跑通；这个弹窗不处理角色，角色弹窗另有用例）
ROWS = {'mm': {'ID': 'mm', 'Name': '妈妈'}}


# ======================================================================
#  搭一个真项目（与 `test_import_review.py` 同一套最小布局）
# ======================================================================

def _project(tmp_path: Path) -> Path:
    """`proj/game/` + `game/asset_tables/characters.xlsx`。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    table_dir = root / 'game' / 'asset_tables'
    table_dir.mkdir(parents=True, exist_ok=True)
    write_dict_to_excel(table_dir / 'characters.xlsx', ROWS,
                        _TABLE_FIELDS['character'])
    return root


def _txt(tmp_path: Path, name: str, text: str = '旁白一句\n') -> Path:
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return path


def _generated(path: Path, label: str, line: str = '旧的一句') -> Path:
    """工具以前生成的章节（首行有生成标记 → 重导覆盖它没有额外代价）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{GENERATOR_MARKER}\n\nlabel {label}:\n    "{line}"\n',
                    encoding='utf-8')
    return path


def _handwritten(path: Path, label: str) -> Path:
    """手写章节（没有生成标记 → 覆盖会丢 define，默认必须"不动"）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('define hand = Character("手写")\n'
                    f'label {label}:\n    "原文"\n', encoding='utf-8')
    return path


@pytest.fixture
def review(tmp_path):
    """
    真机 2026-10-26 同款的一批：3 份文件要拿主意。

      行 0：撞上 `game/chapter/chapter2_1.rpy`（工具以前生成的）→ 默认「覆盖」
      行 1：目标是**手写**的 `game/chapter0_0.rpy` → 默认「跳过」，且有
            "旧文件的 define 会丢"的风险；
      行 2：项目里**本来就有**两个 `chapter1_1` → 覆盖救不了，只能改名/跳过。
    """
    root = _project(tmp_path)
    _generated(root / 'game' / 'chapter' / 'chapter2_1.rpy', 'chapter2_1')
    _handwritten(root / 'game' / 'chapter0_0.rpy', 'chapter0_0')
    _generated(root / 'game' / 'chapter1_1.rpy', 'chapter1_1')
    _generated(root / 'game' / 'chapter' / 'chapter1_1.rpy', 'chapter1_1')
    files = [
        _txt(tmp_path, '2-1重复实验-定稿.txt'),
        _txt(tmp_path, '1-0初入大学-定稿.txt',
             '1.0 chapter0-0 初入大学\n旁白一句\n'),
        _txt(tmp_path, '1-1到达青蓝实验室-定稿.txt'),
    ]
    built = TP.build_plan(root, files, ROWS)
    found = IR.preflight(built, root)
    assert len(found.items) == 3, [i.conflict_text() for i in found.items]
    return found


@pytest.fixture
def dialog(qapp):
    """`dialog(review)` → 一个没 show 的弹窗（offline 跑真控件）。"""
    from storygraph.ui_qt.dialog_import_review import CollisionReviewDialog

    def _make(found):
        return CollisionReviewDialog(None, found)

    return _make


def _tree(root: Path) -> list:
    """项目里所有文件的相对路径（用来证明弹窗**没碰磁盘**）。"""
    return sorted(str(p.relative_to(root)) for p in root.rglob('*'))


# ======================================================================
#  ① 顶部说明：没决定之前一个字节都不写
# ======================================================================

def test_the_top_line_says_nothing_is_written_before_a_decision(dialog, review):
    """
    用户最怕的就是"擅自落盘 / 擅自全量替换"：弹窗第一行必须先说清
    **现在一个字节都没写**，而且"取消"就是什么都不写。
    """
    dlg = dialog(review)
    text = dlg.summary.text()
    assert review.summary() in text
    assert '没决定之前一个字节都不写' in text
    assert '取消' in text


# ======================================================================
#  ② 默认值 = 不改变现状；不能覆盖的行不许出现"覆盖"
# ======================================================================

def test_defaults_follow_the_item_default_action(dialog, review):
    """默认值 = `item.default_action`：能安全重导的预选覆盖，其余预选跳过。"""
    dlg = dialog(review)
    for row, item in enumerate(review.items):
        assert dlg._combos[row].currentData() == item.default_action, \
            (row, item.conflict_text())
    # 工具以前生成的 → 覆盖；手写的 / 项目本来就重名 → 跳过
    assert dlg._combos[0].currentData() == IR.ACT_REPLACE
    assert dlg._combos[1].currentData() == IR.ACT_SKIP
    assert dlg._combos[2].currentData() == IR.ACT_SKIP


def test_a_row_that_cannot_replace_does_not_offer_replace(dialog, review):
    """
    「覆盖旧文件」不可用的行（项目里本来就有两个同名 label，覆盖救不了），
    下拉里**不能出现这一项** —— 看得见却选不了比看不到更恼火。
    """
    dlg = dialog(review)
    broken = review.items[2]
    assert broken.allows_replace is False
    combo = dlg._combos[2]
    actions = [combo.itemData(i) for i in range(combo.count())]
    assert IR.ACT_REPLACE not in actions
    assert actions == [IR.ACT_RENAME, IR.ACT_SKIP]
    assert [combo.itemText(i) for i in range(combo.count())] == [
        '改个名字写入', '这次跳过']


# ======================================================================
#  ③ 右侧对比区：换行切内容、行对齐、摘要与风险看得见
# ======================================================================

def test_selecting_a_row_switches_the_comparison_and_keeps_lines_aligned(
        dialog, review):
    """选中行 → 右侧换成这一份；两栏按 diff 行对齐，**行数必然一样多**。"""
    dlg = dialog(review)
    assert dlg.table.currentRow() == 0, '默认应选第一行'
    assert str(review.items[0].old_path) in dlg.old_path_label.text()

    dlg.table.setCurrentCell(1, 0)
    item = review.items[1]
    assert dlg.table.currentRow() == 1
    assert str(item.old_path) in dlg.old_path_label.text()
    assert item.source.name in dlg.new_path_label.text()

    old_text = dlg.old_view.toPlainText()
    new_text = dlg.new_view.toPlainText()
    assert 'define hand' in old_text and '原文' in old_text
    assert '旁白一句' in new_text
    assert '│' in old_text and '│' in new_text      # 行首带行号
    rows, _stats = item.diff()
    assert dlg.old_view.document().blockCount() == len(rows)
    assert dlg.new_view.document().blockCount() == len(rows)


def test_stats_and_replace_risk_are_visible(dialog, review):
    """
    `stats.summary()`（"旧 N 行 → 新 M 行：…"）与 `risk_text()`（覆盖手写
    文件会丢 define / init python）必须**看得见** —— 那是人拍"覆盖"前的
    代价清单，藏起来等于骗他点。
    """
    dlg = dialog(review)
    assert dlg.stats_label.text() == review.items[0].diff()[1].summary()
    assert '行' in dlg.stats_label.text()
    assert dlg.risk_label.isHidden(), '工具生成的旧文件覆盖没代价，不该报警'

    dlg.table.setCurrentCell(1, 0)                    # 手写文件那一行
    item = review.items[1]
    assert not dlg.risk_label.isHidden(), '覆盖手写文件的风险没显示'
    assert dlg.risk_label.text() == '⚠ ' + item.risk_text()
    assert '顶层语句' in dlg.risk_label.text()
    assert 'define' in dlg.risk_label.text()
    assert T.t('state.warning') in dlg.risk_label.styleSheet()   # 橙色警示


def test_switching_to_rename_fills_the_suggested_name(dialog, review):
    """切到「改个名字」→ 自动填上没被占用的建议名，而且**人可以改**。"""
    dlg = dialog(review)
    item = review.items[0]
    combo = dlg._combos[0]
    edit = dlg._label_edits[0]
    assert not edit.isEnabled(), '没选"改名"时不该能填'

    combo.setCurrentIndex(combo.findData(IR.ACT_RENAME))
    suggestion = item.suggest_label(review.taken_labels)
    assert IR.valid_label(suggestion)
    assert suggestion not in review.taken_labels
    assert edit.isEnabled()
    assert edit.text() == suggestion
    assert edit.placeholderText() == suggestion
    assert not edit.isReadOnly()

    edit.setText('chapter2_1_review')             # 人可以改成自己想要的
    assert edit.text() == 'chapter2_1_review'
    combo.setCurrentIndex(combo.findData(IR.ACT_SKIP))
    assert not edit.isEnabled()


# ======================================================================
#  ④ 三个批量按钮（"可以全部强制更换"）
# ======================================================================

def test_replace_all_only_touches_rows_that_allow_it(dialog, review):
    """「全部覆盖旧文件」只对 `allows_replace` 的行生效，其余保持原选择。"""
    dlg = dialog(review)
    dlg._all_skip()
    assert all(c.currentData() == IR.ACT_SKIP for c in dlg._combos)

    dlg._all_replace()
    assert dlg._combos[0].currentData() == IR.ACT_REPLACE     # 撞生成文件
    assert dlg._combos[1].currentData() == IR.ACT_REPLACE     # 手写也能覆盖（人点的）
    assert dlg._combos[2].currentData() == IR.ACT_SKIP        # 不允许 → 不动
    assert review.items[2].allows_replace is False


def test_rename_all_fills_a_name_for_every_row(dialog, review):
    """「全部改个名字」→ 每行都变 rename，且都有非空、当场可用的新名字。"""
    dlg = dialog(review)
    dlg._all_rename()
    for row, item in enumerate(review.items):
        assert dlg._combos[row].currentData() == IR.ACT_RENAME
        name = dlg._label_edits[row].text().strip()
        assert name, f'第 {row + 1} 行没填名字'
        assert dlg._label_edits[row].isEnabled()
        assert dlg._review.rename_problem(item, name) == '', name


def test_skip_all_sets_every_row_to_skip(dialog, review):
    dlg = dialog(review)
    dlg.table.setCurrentCell(2, 0)              # 当前行先停在最后一份
    dlg._all_rename()
    dlg._all_skip()
    assert all(c.currentData() == IR.ACT_SKIP for c in dlg._combos)
    assert all(not e.isEnabled() for e in dlg._label_edits)
    # 批量按钮不抢走选中行：右侧对比区还停在人刚才看的那一份
    assert dlg.table.currentRow() == 2


# ======================================================================
#  ⑤ 结论 payload（与 apply_decisions 的入参同格式）
# ======================================================================

def test_choices_payload_matches_apply_decisions_input(dialog, review):
    """`choices()`：每个 item 一条、顺序与表格一致，**弹窗自己不写盘**。"""
    dlg = dialog(review)
    first = dlg._combos[0]
    first.setCurrentIndex(first.findData(IR.ACT_RENAME))
    dlg._label_edits[0].setText('chapter2_1_review')
    second = dlg._combos[1]
    second.setCurrentIndex(second.findData(IR.ACT_REPLACE))
    # 第 2 行保持默认「跳过」

    assert dlg.choices() == [
        {'source': str(review.items[0].source),
         'action': IR.ACT_RENAME, 'label': 'chapter2_1_review'},
        {'source': str(review.items[1].source),
         'action': IR.ACT_REPLACE, 'label': ''},
        {'source': str(review.items[2].source),
         'action': IR.ACT_SKIP, 'label': ''},
    ]


# ======================================================================
#  ⑥ 就地报错：不合法 / 已被占用 / 两行撞名 → 不关窗、红字说清
# ======================================================================

def test_an_illegal_new_name_keeps_the_dialog_open_with_a_reason(dialog, review):
    """名字不合法（中文/空格）→ 点确定**不关窗**，红字说清哪一行、为什么。"""
    dlg = dialog(review)
    combo = dlg._combos[0]
    combo.setCurrentIndex(combo.findData(IR.ACT_RENAME))
    dlg._label_edits[0].setText('中文 名字')

    dlg.btn_accept.click()
    assert dlg.result() != QDialog.Accepted, '名字不合法却关窗了'
    assert not dlg.error.isHidden()
    assert '第 1 行' in dlg.error.text()
    assert review.items[0].source.name in dlg.error.text()
    assert '合法' in dlg.error.text()


def test_a_name_already_taken_is_rejected_in_place(dialog, review):
    """新名字已被项目里的文件定义 → 当场拒绝（别等落盘才说"撞了"）。"""
    dlg = dialog(review)
    combo = dlg._combos[0]
    combo.setCurrentIndex(combo.findData(IR.ACT_RENAME))
    dlg._label_edits[0].setText('chapter1_1')       # 项目里已有文件定义它
    dlg._on_accept()
    assert dlg.result() != QDialog.Accepted
    assert '占用' in dlg.error.text()


def test_two_rows_renaming_to_the_same_name_is_rejected(dialog, review):
    """两行各自合法但撞彼此 → 落盘时第二份必失败，弹窗这边先拦住。"""
    dlg = dialog(review)
    for row in (0, 1):
        combo = dlg._combos[row]
        combo.setCurrentIndex(combo.findData(IR.ACT_RENAME))
        dlg._label_edits[row].setText('chapter2_1_dup')
    dlg._on_accept()
    assert dlg.result() != QDialog.Accepted
    assert '第 2 行' in dlg.error.text()
    assert '一样' in dlg.error.text()


# ======================================================================
#  ⑦ 确定 / 取消
# ======================================================================

def test_accepting_valid_choices_returns_accepted(dialog, review):
    """全部合法 → accepted；而且弹窗**只出决定、不碰磁盘**。"""
    root = review.root
    before = _tree(Path(root))
    dlg = dialog(review)
    combo = dlg._combos[0]
    combo.setCurrentIndex(combo.findData(IR.ACT_RENAME))
    dlg._label_edits[0].setText('chapter2_1_next')

    dlg._on_accept()
    assert dlg.result() == QDialog.Accepted
    assert dlg.error.isHidden()
    assert _tree(Path(root)) == before, '弹窗居然自己写了文件'


def test_cancel_is_not_accepted_and_writes_nothing(dialog, review):
    """「取消（什么都不写）」= reject：返回码不是 Accepted，磁盘一个字节没动。"""
    root = Path(review.root)
    before = _tree(root)
    dlg = dialog(review)
    dlg.btn_cancel.click()
    assert dlg.result() != QDialog.Accepted
    assert dlg.result() == QDialog.Rejected
    assert _tree(root) == before, '取消居然写了文件'


# ======================================================================
#  ⑧ 对比栏：差异着色 + 两边滚动联动
# ======================================================================

def test_the_views_mark_changes_and_scroll_together(qapp, dialog, tmp_path):
    """
    差异行要**看得见**（改=黄、增=绿、删=红，色相来自 tokens；底色压了透明
    度，否则浅底色在白色正文里几乎看不出边界），两边滚动条要**联动**
    （左滚到哪右跟到哪，否则行号看着看着就错位）。
    """
    from storygraph.ui_qt.dialog_import_review import DIFF_ALPHA

    old_lines = [f'旧行 {i}' for i in range(60)]
    new_lines = list(old_lines)
    new_lines[5] = '改过的一行'                  # 改
    del new_lines[30]                            # 删
    new_lines.insert(40, '新插入的一行')          # 增
    old_text = '\n'.join(old_lines)
    new_text = '\n'.join(new_lines)
    target = tmp_path / 'game' / 'big.rpy'
    item = IR.ReviewItem(
        source=tmp_path / 'big.txt', target=target, label='big',
        new_text=new_text, old_text=old_text, old_path=target,
        conflicts=[IR.Conflict(code=IR.CODE_LABEL_COLLISION,
                               message='`label big` 已经被别处定义')],
        replace_path=target, replace_is_generated=True,
        writable_dir=target.parent)
    synthetic = IR.Review(root=tmp_path, items=[item], taken_labels={'big'})
    dlg = dialog(synthetic)
    dlg.resize(1360, 820)
    dlg.show()
    qapp.processEvents()

    # ① 两栏行数仍然对齐（对齐是"改在哪一行"能看出来的前提）
    assert (dlg.old_view.document().blockCount()
            == dlg.new_view.document().blockCount())

    # ② 每一边只在"这一边有内容"的差异行上着色，且颜色分类正确
    rows, _stats = item.diff()
    state_of = {'chg': 'state.warning', 'add': 'state.success',
                'del': 'state.error'}
    for side, expected_rows in (
            (dlg.old_view, [r for r in rows if r.tag in ('chg', 'del')]),
            (dlg.new_view, [r for r in rows if r.tag in ('chg', 'add')])):
        marks = side.extraSelections()
        assert len(marks) == len(expected_rows)
        for mark, row in zip(marks, expected_rows):
            colour = mark.format.background().color()
            assert colour.name() == QColor(T.t(state_of[row.tag])).name()
            assert colour.alpha() == DIFF_ALPHA

    # ③ 滚动联动：把左边滚到第 7 行，右边跟着到同一个值
    old_bar = dlg.old_view.verticalScrollBar()
    new_bar = dlg.new_view.verticalScrollBar()
    assert old_bar.maximum() > 7, '合成数据不够长，没滚动起来'
    old_bar.setValue(7)
    qapp.processEvents()
    assert new_bar.value() == 7
    dlg.close()
