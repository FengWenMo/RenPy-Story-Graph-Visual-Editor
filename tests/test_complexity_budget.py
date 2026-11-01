# -*- coding: utf-8 -*-
"""
复杂度**棘轮**：只许降，不许升（2026-11-01，第二轮审查 P2-13 的开工第一刀）
=========================================================================

审查的原话是"核心对象太重（113 个方法 / 约 4200 行）""两个巨型函数，
一个 314 行、一个 331 行"。可"重"这种形容词没法当判据 —— 所以先把现状**量**
出来（`tools/complexity_report.py`），再把**上限**钉进一份快照
（`tests/complexity_budget.json`）。

这个文件只守一条规矩：

    **任何一个已经被记进预算的函数，不许变得比快照更长 / 更绕。**

为什么是"棘轮"而不是"一次拆完"：拆 113 个方法的核心对象是月级工作，
一次改完必然出事；但"不许再长"从今天就能生效 —— 不然每次加功能都往
最长的那个函数里塞，半年后又长 200 行。

函数被**拆小**或被改名 → 不入判据（那是改进，不是回归）。
新写的函数仍然有硬线（`limit_new`），别指望"反正是新函数"就能写 500 行。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUDGET = ROOT / 'tests' / 'complexity_budget.json'


def _report_module():
    path = ROOT / 'tools' / 'complexity_report.py'
    spec = importlib.util.spec_from_file_location('_complexity_report', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault('_complexity_report', mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def budget():
    return json.loads(BUDGET.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def rows(budget):
    """
    只扫**预算里出现过的文件**（约 25 个），不扫整个包。

    为什么：整包 5343 个函数要 11 秒，而棘轮只需要盯住"已经被记下来的那些
    文件里，那些函数有没有变长"。新函数另有硬线判据（见下），也只看这批文件
    —— 真正的探照灯是 `tools/complexity_report.py`（人跑，不占 CI 时间）。
    """
    mod = _report_module()
    files = sorted({key.split('::')[0] for key in budget['functions']})
    out = []
    for rel in files:
        path = ROOT / rel
        if path.is_file():
            out.extend(mod.scan_file(path))
    return out


def _key(row) -> str:
    return f"{row['file']}::{row['name']}"


def test_budget_file_exists_and_is_not_empty(budget):
    assert budget['functions'], '预算快照是空的 —— 棘轮等于没装'
    assert len(budget['functions']) >= 30, len(budget['functions'])


def test_no_budgeted_function_grew_longer_or_bushier(budget, rows):
    """
    已被记进预算的函数：行数与分支数都**不许超过**快照。

    失败时的输出直接给"谁长了多少"，不用再翻报告。
    """
    grew = []
    for row in rows:
        cap = budget['functions'].get(_key(row))
        if not cap:
            continue
        if row['lines'] > cap['lines']:
            grew.append(f'{_key(row)}：{cap["lines"]} → {row["lines"]} 行')
        if row['branches'] > cap['branches']:
            grew.append(f'{_key(row)}：{cap["branches"]} → {row["branches"]} 分支')
    assert not grew, ('复杂度涨了（拆小可以，变大不行）：\n  ' + '\n  '.join(grew))


def test_new_functions_stay_under_the_hard_line(budget, rows):
    """没进快照的新函数：行数 / 分支不许踩硬线（500 行 / 150 分支）。"""
    cap_lines = budget['limit_new']['lines']
    cap_branch = budget['limit_new']['branches']
    bad = [f'{_key(r)}：{r["lines"]} 行 / {r["branches"]} 分支'
           for r in rows
           if _key(r) not in budget['functions']
           and (r['lines'] > cap_lines or r['branches'] > cap_branch)]
    assert not bad, ('新函数一上来就超过硬线（拆成几个小函数再提交）：\n  '
                     + '\n  '.join(bad))


def test_report_tool_can_scan_a_real_file(rows):
    """报告器本身要能扫（语法错 / 读不了都要当场发现），且字段齐全。"""
    assert len(rows) > 100, len(rows)
    assert all({'file', 'name', 'line', 'lines', 'branches'} <= set(r)
               for r in rows[:5])
    assert any(r['file'].startswith('storygraph/story/') for r in rows)
