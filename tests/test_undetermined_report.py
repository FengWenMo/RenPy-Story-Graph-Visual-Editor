"""
第四层判据：**如实报告 undetermined**（新增/汇总到报告 + CI 退出码）
=======================================================================

用户 2026-10-11 的四层方案第四层："诚实标记「测不出来」，不当假绿"。

这一层的价值不在"多问出什么"，而在**把没问出来的东西摆到台面上**：

  · 扫描发现 N 个"工具不认识的写法" → 这些写法**能不能问引擎**？
    实测结论（引擎级根因，见路线图 §8.13）：**不能** ——
    未知写法本身就是 parse error，引擎会在第一处停下，注册代码永远没机会跑。
  · 所以它们一律是 `undetermined`，必须**显式说出来**，而不是让报告只显示
    "✅ 采集完成"（那就是假绿）。

判据钉三件事：① 汇总的**分类**对不对；② **没数据时什么都不说**（不宣称）；
③ `--strict` 时 **undetermined 非空要退 1**。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core import engine_facts as EF                        # noqa: E402


def _facts(undeclared, *, statements=None, parse_clean=True, probe=True):
    return EF.build_facts(
        {'statements': [(EF.SRC_PROBE, statements or {})],
         'images': [(EF.SRC_PROBE, [])]},
        collection={'parse_clean': parse_clean, 'probe_complete': probe,
                    'lint_exit': 0},
        derived={'undeclared_heads': undeclared,
                 'scan_disclaimer': '以上是「我认出的」，不代表项目里只有这些（N1）'},
    )


# ==================== ① 汇总分类 ====================

def test_undetermined_counts_scan_only_heads():
    """
    **没被引擎回答过**的写法（`engine_recognized` 缺省）→ `undetermined`。
    这是最常见的一类：项目编译不过，我们连问都没问成。
    """
    facts = _facts([
        {'head': 'weather', 'count': 3, 'files': ['a.rpy']},
        {'head': 'outfit', 'count': 1, 'files': ['b.rpy']},
    ])
    got = EF.undetermined_statements(facts)
    # 排序 = 用得多在前（与扫描/报告其它地方一致），同数按名字
    assert [r['head'] for r in got['undetermined']] == ['weather', 'outfit'], got
    assert got['engine_known'] == [] and got['engine_unknown'] == []
    assert got['total_scan'] == 2


def test_engine_answered_heads_are_classified():
    """引擎答过的分两栏：认识的（known）/ 不认识的（unknown）—— 都不算 undetermined。"""
    facts = _facts([
        {'head': 'ok_one', 'count': 2, 'engine_recognized': True},
        {'head': 'bad_one', 'count': 1, 'engine_recognized': False},
        {'head': 'never_asked', 'count': 1},
    ])
    got = EF.undetermined_statements(facts)
    assert [r['head'] for r in got['engine_known']] == ['ok_one']
    assert [r['head'] for r in got['engine_unknown']] == ['bad_one']
    assert [r['head'] for r in got['undetermined']] == ['never_asked']


def test_blocked_collection_is_reported_as_the_reason():
    """
    `engine_error`（被挡住 / 问不到）要**带进结论** —— 读者得知道
    "为什么测不出来"，而不是只看到一个空结论。
    """
    facts = _facts([
        {'head': 'weather', 'count': 2, 'engine_error': 'File "game/a.rpy", '
         'line 2: expected statement.'},
    ])
    got = EF.undetermined_statements(facts)
    row = got['undetermined'][0]
    assert 'expected statement' in row['why'], row
    text = EF.format_undetermined(got)
    assert '测不出来' in text or 'undetermined' in text.lower()
    assert 'expected statement' in text


def test_skipped_heads_are_undetermined_too():
    """超出限额被跳过的（`engine_skipped`）同样是"没测" —— 不能当"测过了没问题"。"""
    facts = _facts([{'head': 'too_many', 'count': 9, 'engine_skipped': True}])
    got = EF.undetermined_statements(facts)
    assert [r['head'] for r in got['undetermined']] == ['too_many']
    assert '跳过' in got['undetermined'][0]['why']


# ==================== ② 没数据就不说话 ====================

def test_nothing_is_claimed_without_scan_data():
    """**没有扫描段** → 三栏全空、报告是空串（不宣称）。"""
    for facts in (None, {}, _facts([])):
        got = EF.undetermined_statements(facts)
        assert got['undetermined'] == [] and got['total_scan'] == 0, got
        assert EF.format_undetermined(got) == ''


def test_unusable_facts_still_speak_about_the_scan():
    """
    ⚠⚠ 真机跑出来的缺陷（我第一版把它藏了）：**有扫描段、但事实不可用**
    （项目有 parse error）时，那些写法恰恰是"最该报出来的" ——
    它们的理由就是"这轮采集不可用，引擎在第一处 parse error 停下了"。

    第一版 `is_collectable` 为假就整段返回空 → `total_scan=0` →
    报告沉默 —— 那正是**最该说话的时候**（真机 `ask=True` 当场发现）。
    """
    for kw in ({'parse_clean': False}, {'probe': False}):
        facts = _facts([{'head': 'weather', 'count': 3}], **kw)
        got = EF.undetermined_statements(facts)
        assert got['total_scan'] == 1, got
        assert [r['head'] for r in got['undetermined']] == ['weather'], got
        assert '采集不可用' in got['undetermined'][0]['why'], got
        text = EF.format_undetermined(got)
        assert '测不出来' in text, text


def test_report_has_a_disclaimer_about_the_scan():
    """规则 N1：扫描器**只说我认出了 N 个** —— 报告里必须带上这句。"""
    facts = _facts([{'head': 'weather', 'count': 1}])
    text = EF.format_undetermined(EF.undetermined_statements(facts))
    assert '不代表' in text, '要带上"不代表项目里只有这些"（N1）'


# ==================== ③ 退出码（CI 门禁） ====================

def test_ci_verdict_is_zero_when_everything_is_answered():
    from storygraph.cli import _undetermined_exit
    facts = _facts([{'head': 'ok_one', 'count': 1, 'engine_recognized': True}])
    code, note = _undetermined_exit(facts, strict=True)
    assert code == 0 and note == '', (code, note)


def test_ci_verdict_is_one_under_strict_when_undetermined_exists():
    """
    ⚠ 这一条是第四层的**全部意义**：`--strict` 下"有测不出来的"必须退 1，
    否则 CI 会显示"✅ 采集完成"，而那正是**假绿**。
    """
    from storygraph.cli import _undetermined_exit
    facts = _facts([{'head': 'weather', 'count': 3}])
    code, note = _undetermined_exit(facts, strict=True)
    assert code == 1, (code, note)
    assert '测不出来' in note or 'undetermined' in note.lower(), note


def test_ci_verdict_is_zero_without_strict():
    """不加 `--strict` 时**不改变**既有退出码语义（不偷偷把 CI 变红）。"""
    from storygraph.cli import _undetermined_exit
    facts = _facts([{'head': 'weather', 'count': 3}])
    code, note = _undetermined_exit(facts, strict=False)
    assert code == 0, (code, note)
    assert '测不出来' in note, '但提示还要打出来（让人看得见）'


# ==================== ④ 体检报告里也要有这一段 ====================

def _minimal_report(**kw):
    base = {'ok': True, 'problems': 0,
            'counts': {'images': 1, 'music': 0, 'sounds': 0, 'voices': 0},
            'missing': {}, 'unregistered': {}, 'outside_game': {},
            'duplicate_ids': [], 'duplicate_paths': [], 'no_path': [],
            'placeholders': [], 'total_rows': 1}
    base.update(kw)
    return base


def test_health_report_shows_the_undetermined_section():
    from storygraph.app.asset_report import format_asset_health
    summary = EF.undetermined_statements(_facts([
        {'head': 'weather', 'count': 3},
        {'head': 'ok_one', 'count': 1, 'engine_recognized': True},
    ]))
    text = format_asset_health(_minimal_report(engine_undetermined=summary))
    assert '能不能问出结论' in text, text
    assert 'weather' in text and '测不出来' in text
    assert 'ok_one' in text, '引擎答过"认识"的也要列'
    assert '没覆盖 ≠ 通过' in text, '必须写明行为级没覆盖'


def test_health_report_has_no_section_without_data():
    from storygraph.app.asset_report import format_asset_health
    text = format_asset_health(_minimal_report())
    assert '能不能问出结论' not in text


# ==================== ⑤ 行为级那一行（第四层口径跟上第三层） ====================

def test_behavior_line_says_not_covered_when_nothing_was_verified():
    """
    一个都没验过 → **明说"这一层没覆盖"**，并且必须写清 **没覆盖 ≠ 通过**
    （这是原来的口径，不能因为有了第三层就把它悄悄抹掉）。
    """
    text = EF.format_behavior_line({})
    assert '没覆盖' in text and '没覆盖 ≠ 通过' in text, text


def test_behavior_line_reports_counts_when_something_was_verified():
    """验过 → 报"验过 N / 通过 / 失败 / 没证明成"，并列出名字。"""
    facts = EF.build_facts(
        {'behavior': [(EF.SRC_PROBE, {
            'sg_ok': {'verified': True, 'why': '证据在'},
            'sg_bad': {'verified': False, 'why': '证据文件不存在'},
            'sg_meh': {'verified': None, 'why': '没读到 Status'},
        })]},
        collection={'parse_clean': True, 'probe_complete': True})
    text = EF.format_behavior_line(facts)
    assert '验过 3 个' in text, text
    assert '通过 1 / 失败 1' in text, text          # 计数（`/` 分隔）
    assert '没证明成** 1' in text, text
    assert 'sg_ok' in text and 'sg_bad' in text and 'sg_meh' in text, text
    assert '没覆盖' not in text, '验过了就不该再说"没覆盖"'


def test_behavior_none_is_kept_distinct_from_false():
    """
    ⚠ `verified=None`（没证明成）**绝不折成 False**：
    "没证明"与"证明是错的"是两件事（第四层的核心口径）。
    """
    facts = EF.build_facts(
        {'behavior': [(EF.SRC_PROBE, {
            'a': {'verified': None}, 'b': {'verified': False}})]},
        collection={'parse_clean': True, 'probe_complete': True})
    s = EF.behavior_summary(facts)
    assert s['unverified'] == ['a'] and s['failed'] == ['b'], s
    line = EF.format_behavior_line(facts)
    assert '既不是通过也不是失败' in line, line


def test_health_report_carries_the_behavior_line():
    """体检报告里也要有这一行（否则报告口径还是旧的）。"""
    from storygraph.app.asset_report import format_asset_health
    text = format_asset_health(_minimal_report(engine_facts={}))
    assert '行为级验证' in text and '没覆盖' in text, text
    facts = EF.build_facts(
        {'behavior': [(EF.SRC_PROBE, {'x': {'verified': True}})]},
        collection={'parse_clean': True, 'probe_complete': True})
    text2 = format_asset_health(_minimal_report(engine_facts=facts))
    assert '验过 1 个' in text2, text2
