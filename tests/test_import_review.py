"""
纯文字导入 · **冲突预检 / 先问后写** 判据（2026-10-26）
========================================================

用户实机原话：

> 我一个个删 vscode 里面的 .bak 和 blocked 里面的文件老狼狈了……
> 当检测到有 label 重复的时候，能不能不要擅自不落盘，或者擅自全量替换呀……
> 能不能给出一个 UI 提醒，让我能够对着 txt 换 label？如果撞 label，
> 显示新内容与旧内容对比，再决定是否强制更换（可以全部强制更换，
> 也可以一个个文件分……）

`storygraph/authoring/import_review.py` 就是这条要求的纯逻辑部分。这里钉住：

  ① **写之前**就算得出"谁撞了谁"（并说清是哪个文件占着 label）；
  ② 三种结局各自的行为：覆盖旧文件（备份 .bak）/ 改个名字（旧文件不动）/
     跳过（**不产生 .blocked.rpy**）；
  ③ **取消 = 零写**（一个字节都不写、也不留任何对照副本）；
  ④ 不给 `resolve`（CLI / 单测）时行为与从前**完全一致**（这条别被改掉）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.assets.asset_registry import (                        # noqa: E402
    _TABLE_FIELDS, write_dict_to_excel)
from storygraph.authoring import import_review as IR                  # noqa: E402
from storygraph.authoring import txt_pipeline as TP                   # noqa: E402
from storygraph.core.vocabulary import GENERATOR_MARKER               # noqa: E402

ROWS = {
    'mm': {'ID': 'mm', 'Name': '妈妈'},
    'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
}


def _project(tmp_path: Path) -> Path:
    """最小项目：`game/` + 角色表（和 `test_txt_pipeline.py` 同一套）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    adir = root / 'game' / 'asset_tables'
    adir.mkdir(parents=True, exist_ok=True)
    write_dict_to_excel(adir / 'characters.xlsx', ROWS, _TABLE_FIELDS['character'])
    return root


def _txt(tmp_path: Path, name: str, text: str = '旁白一句\n') -> Path:
    fp = tmp_path / name
    fp.write_text(text, encoding='utf-8')
    return fp


def _generated(path: Path, label: str, line: str = '旧的一句') -> Path:
    """造一份**工具以前生成的**章节（首行生成标记 → 可以安全重导）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{GENERATOR_MARKER}\n\nlabel {label}:\n    "{line}"\n',
                    encoding='utf-8')
    return path


def _handwritten(path: Path, label: str, define: bool = True) -> Path:
    """造一份**手写**章节（没有生成标记）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    head = 'define hand = Character("手写")\n' if define else ''
    path.write_text(f'{head}label {label}:\n    "原文"\n', encoding='utf-8')
    return path


def _plan_review(root, files):
    plan = TP.build_plan(root, files, ROWS)
    return plan, IR.preflight(plan, root)


def _choices(*items):
    return [{'source': str(i.source), 'action': i.action,
             'label': i.new_label} for i in items]


# ==================== ① 预检：写之前就说得清"谁撞了谁" ====================

def test_a_label_owned_by_another_file_is_found_before_any_write(tmp_path):
    """
    真机 2026-10-26 的形态：章节住在 `game/chapter/`，导入器却往 `game/` 写。
    预检必须**在写之前**说出"`chapter2_1` 已经被 `game/chapter/chapter2_1.rpy`
    定义"，并给出"覆盖旧文件"的落点。
    """
    root = _project(tmp_path)
    owner = _generated(root / 'game' / 'chapter' / 'chapter2_1.rpy', 'chapter2_1')
    fp = _txt(tmp_path, '2-1重复实验-定稿.txt')
    plan, review = _plan_review(root, [fp])

    assert review.needs_review
    item = review.items[0]
    assert item.code == IR.CODE_LABEL_COLLISION
    assert item.label == 'chapter2_1'
    assert item.other_path == owner
    # 说清是哪个文件 —— 别再让人对着"项目其他 .rpy"猜
    assert 'chapter' in item.conflict_text() and 'chapter2_1.rpy' in \
        item.conflict_text()
    # 覆盖旧文件 = 写进**那个已经占位的文件**，而不是另造一份撞名产物
    assert item.replace_path == owner
    # 改名 / 新写落到**计划的落点目录**（界面会先问导出文件夹；不传时 = game/）。
    # ⚠ 2026-10-26 起**不再**跟着旧章节目录走（很多项目没有 game/chapter/）。
    assert item.writable_dir == root / 'game', item.writable_dir
    # 旧的是工具以前生成的 → 默认建议"覆盖"（可安全重导）
    assert item.default_action == IR.ACT_REPLACE
    assert not list((root / 'game').glob('*.rpy')), '预检居然写了文件'


def test_a_handwritten_target_is_reported_as_handwritten(tmp_path):
    root = _project(tmp_path)
    hand = _handwritten(root / 'game' / 'chapter0_0.rpy', 'chapter0_0')
    fp = _txt(tmp_path, '1-0初入大学-定稿.txt',
              '1.0 chapter0-0 初入大学\n旁白一句\n')
    _plan, review = _plan_review(root, [fp])
    item = review.items[0]
    assert item.code == IR.CODE_HANDWRITTEN
    assert item.replace_path == hand
    assert item.replace_is_generated is False
    # 手写文件默认**不动**（用户点错"继续"也不该毁掉它）
    assert item.default_action == IR.ACT_SKIP
    # 覆盖代价要算出来：旧文件里的顶层语句（define …）会丢
    assert item.lost_chunks, '没算"覆盖会丢 define"'
    assert '顶层语句' in item.risk_text()


def test_two_existing_owners_mean_the_project_was_already_broken(tmp_path):
    """项目里本来就有两个文件定义同一个 label → 覆盖救不了，只能改名/跳过。"""
    root = _project(tmp_path)
    _generated(root / 'game' / 'chapter1_1.rpy', 'chapter1_1')
    _generated(root / 'game' / 'chapter' / 'chapter1_1.rpy', 'chapter1_1')
    fp = _txt(tmp_path, '1-1到达青蓝实验室-定稿.txt')
    _plan, review = _plan_review(root, [fp])
    item = review.items[0]
    assert item.code == IR.CODE_ALREADY_DUPLICATED
    assert item.allows_replace is False
    assert '本来就有' in item.conflict_text()


def test_a_clean_import_needs_no_review(tmp_path):
    root = _project(tmp_path)
    fp = _txt(tmp_path, '1-0初入大学-定稿.txt',
              '1.0 chapter0-0 初入大学\n旁白一句\n')
    _plan, review = _plan_review(root, [fp])
    assert review.ok and review.items == []
    assert '没有' in review.summary()


# ==================== ② 新旧对比（弹窗右半边那份数据） ====================

def test_compare_texts_aligns_changed_lines():
    rows, stats = IR.compare_texts('甲\n乙\n丙', '甲\n乙改了\n丙\n丁')
    assert [r.tag for r in rows] == ['eq', 'chg', 'eq', 'add']
    assert stats.old_lines == 3 and stats.new_lines == 4
    assert (stats.same, stats.changed, stats.added, stats.removed) == (2, 1, 1, 0)
    assert rows[1].old == '乙' and rows[1].new == '乙改了'
    assert rows[1].old_no == 2 and rows[1].new_no == 2
    assert rows[3].old_no == 0 and rows[3].new_no == 4
    assert '改了 1 行' in stats.summary()


def test_compare_texts_says_identical_when_nothing_changed():
    _rows, stats = IR.compare_texts('甲\n乙', '甲\n乙')
    assert stats.identical
    assert '完全一样' in stats.summary()


def test_a_crlf_old_file_does_not_make_every_line_look_changed():
    """
    ⚠ 2026-10-26 队友实测：旧 .rpy 是 CRLF（vscode 手写最常见）时，
    `split_lines` 切出来的每行都带 `\\r` —— 内容明明一样，对比却把**每一行**
    都标成"改了"。展示层必须把行尾 `\\r` 去掉（只影响对比，不影响落盘口径）。
    """
    rows, stats = IR.compare_texts('甲\r\n乙\r\n丙', '甲\n乙\n丙')
    assert stats.identical, stats.summary()
    assert all(r.tag == 'eq' for r in rows), [r.tag for r in rows]


def test_the_review_item_carries_the_old_and_new_text(tmp_path):
    """对比栏要的是**最终产物**与**旧文件全文**（不是摘要）。"""
    root = _project(tmp_path)
    owner = _generated(root / 'game' / 'chapter' / 'chapter2_1.rpy',
                       'chapter2_1', line='旧的一句')
    fp = _txt(tmp_path, '2-1重复实验-定稿.txt', '新写的一句\n')
    _plan, review = _plan_review(root, [fp])
    item = review.items[0]
    assert '旧的一句' in item.old_text
    assert '新写的一句' in item.new_text
    rows, stats = item.diff()
    assert not stats.identical
    assert any(r.tag in ('chg', 'add') for r in rows)


# ==================== ③ 三种结局 ====================

def test_replace_writes_into_the_existing_file_and_keeps_a_bak(tmp_path):
    """
    「覆盖旧文件」= 写进 `game/chapter/chapter2_1.rpy`；旧内容备份成
    `*.rpy.bak`；**不再**另造一个 `game/chapter2_1.rpy`。
    """
    root = _project(tmp_path)
    owner = _generated(root / 'game' / 'chapter' / 'chapter2_1.rpy', 'chapter2_1')
    before = owner.read_text(encoding='utf-8')
    fp = _txt(tmp_path, '2-1重复实验-定稿.txt')
    seen = {}

    def _resolve(review):
        seen['item'] = review.items[0]
        return [{'source': str(review.items[0].source),
                 'action': IR.ACT_REPLACE}]

    res = TP.run(root, [fp], rows=ROWS, project_root=root, resolve=_resolve)
    assert res.ok, res.messages
    assert res.blocked == []
    assert res.written == [owner], res.written
    assert not (root / 'game' / 'chapter2_1.rpy').exists(), '又造了一份撞名产物'
    assert '旁白一句' in owner.read_text(encoding='utf-8')
    bak = Path(str(owner) + '.bak')
    assert bak.is_file() and bak.read_text(encoding='utf-8') == before, \
        '覆盖手写/旧文件前必须留 .bak'
    assert any('覆盖' in m for m in res.messages), res.messages


def test_replace_a_handwritten_file_is_explicit_and_backed_up(tmp_path):
    """手写文件也能覆盖 —— 但那必须是**人点的那一下**（force），且留 .bak。"""
    root = _project(tmp_path)
    hand = _handwritten(root / 'game' / 'chapter0_0.rpy', 'chapter0_0')
    before = hand.read_text(encoding='utf-8')
    fp = _txt(tmp_path, '1-0初入大学-定稿.txt',
              '1.0 chapter0-0 初入大学\n旁白一句\n')
    res = TP.run(root, [fp], rows=ROWS, project_root=root,
                 resolve=lambda rv: [{'source': str(rv.items[0].source),
                                      'action': IR.ACT_REPLACE}])
    assert res.ok, res.messages
    assert res.written == [hand]
    assert before in Path(str(hand) + '.bak').read_text(encoding='utf-8')
    assert 'define hand' not in hand.read_text(encoding='utf-8')


def test_rename_writes_into_the_export_dir_and_leaves_the_old_file(tmp_path):
    """
    「改个名字」：新 label 写进产物第一行，**写到导出文件夹**、旧文件一字不动。

    ⚠ 2026-10-26 用户改口径："不要写入 chapter 文件夹！很多项目根本没有这个
    文件夹！你可以给一个 rpy 的导出路径，问一问导出到哪个文件夹就行！"
    —— 所以不再跟着旧章节所在目录走；不传 `out_dir` 时退回计划落点的父目录
    （默认 `game/`）。
    """
    root = _project(tmp_path)
    owner = _generated(root / 'game' / 'chapter' / 'chapter2_1.rpy', 'chapter2_1')
    before = owner.read_text(encoding='utf-8')
    fp = _txt(tmp_path, '2-1重复实验-定稿.txt')
    res = TP.run(root, [fp], rows=ROWS, project_root=root,
                 resolve=lambda rv: [{'source': str(rv.items[0].source),
                                      'action': IR.ACT_RENAME,
                                      'label': 'chapter2_1_new'}])
    assert res.ok, res.messages
    out = res.written[0]
    assert out.parent == root / 'game', out       # 不再写进 game/chapter/
    assert out.name == 'chapter2_1_new.rpy', out.name
    assert 'label chapter2_1_new:' in out.read_text(encoding='utf-8')
    assert owner.read_text(encoding='utf-8') == before, '旧文件被动了！'
    assert any('改名' in m or '改名为' in m for m in res.messages), res.messages


def test_the_export_dir_is_where_new_files_go(tmp_path):
    """
    导出文件夹由人指定：`out_dir=` 之后，改名产物的落点就是它
    （`game/chapter/`、`build/`、甚至项目外，都是人说了算）。

    同时钉住冲突预检里给弹窗看的落点（`item.writable_dir`）也用这个目录 ——
    否则弹窗写"将写到 A"、实际写到 B。
    """
    root = _project(tmp_path)
    owner = _generated(root / 'game' / 'chapter' / 'chapter2_1.rpy', 'chapter2_1')
    before = owner.read_text(encoding='utf-8')
    out_dir = root / 'game' / '章节目录'
    out_dir.mkdir(parents=True)
    fp = _txt(tmp_path, '2-1重复实验-定稿.txt')
    plan = TP.build_plan(root, [fp], ROWS, out_dir=out_dir)
    assert plan.files[0].target == out_dir / 'chapter2_1.rpy'
    review = IR.preflight(plan, root, out_dir=out_dir)
    item = review.items[0]
    assert item.writable_dir == out_dir, item.writable_dir
    assert item.rename_target('chapter2_1_x') == out_dir / 'chapter2_1_x.rpy'

    res = TP.run(root, [fp], rows=ROWS, project_root=root, out_dir=out_dir,
                 resolve=lambda rv: [{'source': str(rv.items[0].source),
                                      'action': IR.ACT_RENAME,
                                      'label': 'chapter2_1_x'}])
    assert res.ok, res.messages
    assert res.written == [out_dir / 'chapter2_1_x.rpy'], res.written
    assert owner.read_text(encoding='utf-8') == before, '旧文件被动了！'


def test_rename_with_a_bad_name_is_refused_with_a_reason(tmp_path):
    root = _project(tmp_path)
    _generated(root / 'game' / 'chapter' / 'chapter2_1.rpy', 'chapter2_1')
    fp = _txt(tmp_path, '2-1重复实验-定稿.txt')
    res = TP.run(root, [fp], rows=ROWS, project_root=root,
                 resolve=lambda rv: [{'source': str(rv.items[0].source),
                                      'action': IR.ACT_RENAME,
                                      'label': '中文 名字'}])
    assert not res.written
    assert res.skipped and '合法' in res.skipped[0][1]
    assert any('没改成' in m for m in res.messages), res.messages


def test_skip_writes_nothing_and_leaves_no_review_copy(tmp_path):
    """
    「这次跳过」= 保留旧文件，这一份不写，**也不产生 .blocked.rpy**
    （用户要删的就是那些东西）。
    """
    root = _project(tmp_path)
    owner = _generated(root / 'game' / 'chapter' / 'chapter2_1.rpy', 'chapter2_1')
    before = owner.read_text(encoding='utf-8')
    fp = _txt(tmp_path, '2-1重复实验-定稿.txt')
    res = TP.run(root, [fp], rows=ROWS, project_root=root,
                 resolve=lambda rv: [{'source': str(rv.items[0].source),
                                      'action': IR.ACT_SKIP}])
    assert res.written == []
    assert owner.read_text(encoding='utf-8') == before
    assert not list((root / 'build' / 'review').glob('*.blocked.rpy')), \
        '跳过居然还留了对照副本'
    assert any('跳过' in m for m in res.messages), res.messages


def test_cancelling_the_review_writes_absolutely_nothing(tmp_path):
    """弹窗点「取消」→ 一个字节都不写、不留任何副本，并如实说"先不写"。"""
    root = _project(tmp_path)
    hand = _handwritten(root / 'game' / 'chapter0_0.rpy', 'chapter0_0')
    before = hand.read_text(encoding='utf-8')
    fp = _txt(tmp_path, '1-0初入大学-定稿.txt',
              '1.0 chapter0-0 初入大学\n旁白一句\n')
    res = TP.run(root, [fp], rows=ROWS, project_root=root,
                 resolve=lambda rv: None)
    assert res.needs_review
    assert res.written == [] and res.blocked == [] and res.skipped == []
    assert hand.read_text(encoding='utf-8') == before
    assert not list((root / 'game').glob('*.rpy')) or \
        [p.name for p in (root / 'game').glob('*.rpy')] == ['chapter0_0.rpy']
    assert not (root / 'build' / 'review').exists() or \
        not list((root / 'build' / 'review').glob('*.blocked.rpy'))
    assert '先不写' in res.summary
    assert any('一个字节都没动' in m for m in res.messages), res.messages


# ==================== ④ 整批（"全部强制更换"） ====================

def test_the_real_machine_batch_can_be_replaced_in_one_go(tmp_path):
    """
    真机那五份（`2-1重复实验` / `2-1-1..2-1-4`）全部撞上 `game/chapter/` 里
    工具以前生成的章节 → 全部「覆盖旧文件」→ 五份都写回 chapter/，
    `game/` 根目录下**一份新产物都不许出现**。
    """
    root = _project(tmp_path)
    names = {
        '2-1重复实验-定稿.txt': 'chapter2_1',
        '2-1-1叶怀瑾线-定稿.txt': 'chapter2_1_1',
        '2-1-2凌跃捷线-定稿.txt': 'chapter2_1_2',
        '2-1-3蓝线-定稿.txt': 'chapter2_1_3',
        '2-1-4星桥理子线-定稿.txt': 'chapter2_1_4',
    }
    owners = {}
    for name, label in names.items():
        owners[label] = _generated(root / 'game' / 'chapter' / f'{label}.rpy',
                                   label)
    files = [_txt(tmp_path, n) for n in names]
    res = TP.run(root, [files[0]],
                 rows=ROWS, project_root=root)      # 先确认单份也走这条
    assert not res.ok and res.blocked, '没有 resolve 时应保持老行为（被门禁拦）'

    res = TP.run(root, files, rows=ROWS, project_root=root,
                 resolve=lambda rv: [{'source': str(i.source),
                                      'action': IR.ACT_REPLACE}
                                     for i in rv.items])
    assert res.ok, res.messages
    assert res.blocked == []
    assert len(res.written) == 5
    assert {p.name for p in res.written} == {f'{v}.rpy' for v in names.values()}
    assert all(p.parent.name == 'chapter' for p in res.written)
    assert not [p for p in (root / 'game').glob('*.rpy')], 'game/ 根下多出了产物'


# ==================== ⑤ 向后兼容：不给 resolve 时一切照旧 ====================

def test_without_resolve_the_old_behaviour_is_kept(tmp_path):
    """
    CLI / 单测不传 `resolve` → 照旧写、照旧被门禁拦下、照旧留
    `build/review/*.blocked.rpy`。**别把这条改掉**：老调用方的判据还钉着它。
    """
    root = _project(tmp_path)
    hand = _handwritten(root / 'game' / 'chapter0_0.rpy', 'chapter0_0')
    fp = _txt(tmp_path, '1-0初入大学-定稿.txt',
              '1.0 chapter0-0 初入大学\n旁白一句\n')
    res = TP.run(root, [fp], rows=ROWS, project_root=root)
    assert not res.ok
    assert res.blocked and '手写' in res.blocked[0][1]
    assert hand.read_text(encoding='utf-8').startswith('define hand')
    copies = list((root / 'build' / 'review').glob('*.blocked.rpy'))
    assert copies, '老行为：拦下时要留对照副本'
    assert any('对照副本' in m for m in res.messages), res.messages


def test_dry_run_also_computes_the_review(tmp_path):
    """第一遍（dry_run）就要能说"有 N 个文件撞名"，否则点完角色弹窗才吓人。"""
    root = _project(tmp_path)
    _handwritten(root / 'game' / 'chapter0_0.rpy', 'chapter0_0')
    fp = _txt(tmp_path, '1-0初入大学-定稿.txt',
              '1.0 chapter0-0 初入大学\n旁白一句\n')
    res = TP.run(root, [fp], rows=ROWS, project_root=root, dry_run=True)
    assert res.review is not None and res.review.needs_review
    assert res.written == []


# ==================== ⑥ 小工具 ====================

def test_reimporting_identical_text_does_not_rewrite_nor_pile_up_baks(tmp_path):
    """
    同一批 txt 重导一遍、产物一个字节没变 → **不碰文件**（也就不会多一份
    `.bak`）。用户"一个个删 .bak 老狼狈"里有很大一部分就来自这种空转重写。
    """
    root = _project(tmp_path)
    fp = _txt(tmp_path, '1-2蓝之初见-定稿.txt',
              '1.2 chapter1-2 蓝之初见\n旁白一句\n')
    first = TP.run(root, [fp], rows=ROWS, project_root=root)
    assert first.ok, first.messages
    out = root / 'game' / 'chapter1_2.rpy'
    before = out.read_bytes()
    mtime = out.stat().st_mtime_ns
    assert not list((root / 'game').rglob('*.rpy.bak*'))

    again = TP.run(root, [fp], rows=ROWS, project_root=root)
    assert again.ok, again.messages
    assert out.read_bytes() == before
    assert out.stat().st_mtime_ns == mtime, '内容没变却又写了一遍'
    assert not list((root / 'game').rglob('*.rpy.bak*')), '空转重写多出了 .bak'
    assert any('一模一样' in m for m in again.messages), again.messages


def test_suggest_free_label_is_deterministic():
    assert IR.suggest_free_label('chapter3_1', set()) == 'chapter3_1'
    assert IR.suggest_free_label('chapter3_1', {'chapter3_1'}) == 'chapter3_1_2'
    assert IR.suggest_free_label('chapter3_1',
                                 {'chapter3_1', 'chapter3_1_2'}) == 'chapter3_1_3'


@pytest.mark.parametrize('name,ok', [
    ('chapter1_1', True), ('main.route', True), ('_x2', True),
    ('1abc', False), ('中文名', False), ('a b', False), ('', False)])
def test_valid_label(name, ok):
    assert IR.valid_label(name) is ok


def test_why_rename_failed_explains_every_case(tmp_path):
    """界面校验与落地校验**共用**这一套理由（不允许各写一套）。"""
    root = _project(tmp_path)
    owner = _generated(root / 'game' / 'chapter' / 'chapter2_1.rpy', 'chapter2_1')
    fp = _txt(tmp_path, '2-1重复实验-定稿.txt')
    _plan, review = _plan_review(root, [fp])
    item = review.items[0]
    occupied = {'chapter2_1'}
    planned = {'chapter2_1'}
    assert '没有填' in IR.why_rename_failed(item, '', review, occupied, planned)
    assert '合法' in IR.why_rename_failed(item, '中文名', review, occupied, planned)
    assert '就是撞上的那个' in IR.why_rename_failed(
        item, 'chapter2_1', review, occupied, planned)
    assert '已经有文件定义' in IR.why_rename_failed(
        item, 'chapter9_9', review, {'chapter9_9'}, planned)
    assert IR.why_rename_failed(item, 'chapter2_1_new', review, occupied,
                                planned) == ''


def test_the_review_does_not_touch_the_project(tmp_path):
    """预检是**纯读**：连 .bak 都不该产生。"""
    root = _project(tmp_path)
    _handwritten(root / 'game' / 'chapter0_0.rpy', 'chapter0_0')
    before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    fp = _txt(tmp_path, '1-0初入大学-定稿.txt',
              '1.0 chapter0-0 初入大学\n旁白一句\n')
    _plan, review = _plan_review(root, [fp])
    assert review.items
    after = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    assert before == after, set(after) ^ set(before)
