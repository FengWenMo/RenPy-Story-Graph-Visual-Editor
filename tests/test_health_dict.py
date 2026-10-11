"""
结构化体检判据（用户 2026-10-11："补结构化体检"）
=====================================================

给 MCP / 大模型用的是 `asset_report.health_dict()`。
它与**文本版**（`format_asset_health`）**同源** —— 这里就把"同源"钉住：
不靠人盯，靠判据逐项对一遍。

## 为什么必须同源

两份各自算的体检报告，**迟早会说不同的话** —— 然后人看文本、模型看结构，
一边说"没问题"、一边说"有 3 处"（本项目的假绿全是这个形状）。
所以 `health_dict()` **只整理字段、不重算**。

## 为什么 `severity` 里要有 `unknown`

"测不出来"**不是"没问题"**：引擎编译不过时，与引擎有关的那几项根本没有结论。
结构化版本必须把这个**显式**表达出来，否则模型会把"空列表"读成"一切都好"。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _report(**kw) -> dict:
    base = {'ok': True, 'problems': 2,
            'counts': {'images': 5, 'music': 1, 'sounds': 0, 'voices': 0},
            'missing': {'images': ['a.png', 'b.png']},
            'unregistered': {}, 'outside_game': {'count': 0, 'by_top': {}},
            'duplicate_ids': [{'id': 'x', 'rows': [1, 2]}],
            'duplicate_paths': [], 'no_path': [], 'placeholders': ['p.png'],
            'total_rows': 6}
    base.update(kw)
    return base


def test_dict_and_text_agree_on_the_numbers(tmp_path):
    """⭐ **同源**判据：结构里的计数与文本里写的数字必须一致。"""
    from storygraph.app.asset_report import format_asset_health, health_dict
    rep = _report()
    text = format_asset_health(rep)
    got = health_dict(rep)
    assert got['ok'] is True
    assert got['problems'] == 2
    # 文本里说"发现 2 处"；结构里 problems 也是 2
    assert '发现 2 处' in text, text[:120]
    by_id = {f['id']: f for f in got['findings']}
    assert by_id['missing']['count'] == 2
    assert '【① 表里有、磁盘上没有】2 条' in text
    assert by_id['duplicate_ids']['count'] == 1
    assert '【④ 同一个 ID 出现多次】1 个' in text
    assert by_id['placeholders']['count'] == 1
    assert '【⑦ 人工占位' in text


def test_every_finding_carries_severity_and_source():
    """每一项都要能回答"要不要拦"与"谁说的" —— 缺了模型就只能猜。"""
    from storygraph.app.asset_report import health_dict
    got = health_dict(_report())
    assert got['findings'], got
    for f in got['findings']:
        assert f['severity'] in ('fix', 'info', 'unknown'), f
        assert f['source'], f
        assert 'samples' in f and 'count' in f and 'title' in f


def test_placeholders_are_info_not_a_problem():
    """占位是**合法**的（人在 Notes 里写了「占位」）—— 不许算成待修。"""
    from storygraph.app.asset_report import health_dict
    by_id = {f['id']: f for f in health_dict(_report())['findings']}
    assert by_id['placeholders']['severity'] == 'info'


def test_engine_sections_mark_unknown_when_something_was_not_proven():
    """
    ⭐⭐ **这一条是整份判据的重点**：有"测不出来"的东西时，
    严重度必须是 `unknown` —— 模型**绝不能**把空当"没问题"。
    """
    from storygraph.core import engine_facts as EF
    from storygraph.app.asset_report import health_dict
    facts = EF.build_facts(
        {'behavior': [(EF.SRC_PROBE, {'sg_x': {'verified': None}})]},
        collection={'parse_clean': True, 'probe_complete': True},
        derived={'undeclared_heads': [{'head': 'weather', 'count': 2}]})
    cap = health_dict(_report(engine_facts=facts))['capability']
    assert cap['behavior']['unverified'] == ['sg_x']
    assert cap['behavior']['severity'] == 'unknown', cap['behavior']
    assert cap['undetermined']['total'] == 1
    assert cap['undetermined']['severity'] == 'unknown', cap['undetermined']


def test_no_engine_facts_is_a_note_not_a_silent_pass():
    """没采事实时**要留一句话**说明"引擎那几项没有结论"。"""
    from storygraph.app.asset_report import health_dict
    got = health_dict(_report())
    assert any('没有结论' in n for n in got['notes']), got['notes']


def test_a_failed_report_is_structured_too():
    """体检没做成也要给结构（`ok=False` + 原因），不许返回 None 让调用方猜。"""
    from storygraph.app.asset_report import health_dict
    got = health_dict({'ok': False, 'error': '读不到资源表'})
    assert got['ok'] is False and got['error'] == '读不到资源表'
    assert got['findings'] == []
