# -*- coding: utf-8 -*-
"""
技术债**棘轮**：只许降，不许升（2026-11-01，审查 P2 的"清债"阶段开工）
======================================================================

`tools/debt_report.py` 把几笔**能数**的债量出来（超大函数、导入环、print、
宽泛 except、TODO、超长文件），`tools/debt_budget.py` 把当前值写成
`tests/tech_debt_budget.json`。这个文件只守一条规矩：

    **任何一项债，今天的数字就是上限 —— 只许降，不许升。**

为什么要棘轮而不是"一次清完"：这些债每一项都是月级工作（拆 113 个方法的
核心对象、解 29 个模块的导入环、把 249 处 `print` 换成日志）。
一次改完必然出事；但"不许再涨"从今天就能生效 —— 不然每次加功能都往最烂的
地方塞，半年后数字翻倍。

`swallow_calls` / `broad_excepts` 的口径是"全项目总数"：项目允许
`swallow()`（它现在会进日志），允许 `except Exception`（带 `# noqa: BLE001`
说明理由），但**总量不能涨** —— 涨了说明又有人图省事。

`print_calls` 是**总量**（含 CLI 与 `python xxx.py` 演示，那部分必须留
stdout），`print_library` 才是**真的债**：库里不许 print —— 打包成无窗口
程序后没有输出通道。两个数都钉（2026-11-01 第六段把 12 处库内 print
换成 `swallow` 后：12 → 3，剩下的 3 处是 `preflight` 的 stderr 兜底与
`safeguard` 的 `--debug` 通道，本身就是要给人看的）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUDGET = ROOT / 'tests' / 'tech_debt_budget.json'


def _report_module():
    path = ROOT / 'tools' / 'debt_report.py'
    spec = importlib.util.spec_from_file_location('_debt_report', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault('_debt_report', mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def budget():
    return json.loads(BUDGET.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def data():
    return _report_module().collect()


def test_budget_file_is_complete(budget):
    assert budget['limits'], '上限快照是空的 —— 棘轮等于没装'
    assert set(budget['limits']) >= {
        'long_functions', 'complex_functions', 'print_calls', 'print_library',
        'swallow_calls', 'broad_excepts', 'todos', 'long_files',
        'import_cycles_all'}


def test_every_counted_debt_does_not_grow(budget, data):
    """
    八项计数逐个比。哪一项涨了就点名 —— 不许"总量差不多"糊过去。
    """
    now = {
        'long_functions': data['long_functions'],
        'complex_functions': data['complex_functions'],
        'print_calls': data['print_calls'],
        'print_library': data['print_library'],
        'swallow_calls': data['swallow_calls'],
        'broad_excepts': data['broad_excepts'],
        'todos': data['todos'],
        'long_files': len(data['long_files']),
        'import_cycles_all': len(data['import_cycles_all']),
        'import_cycles_top': len(data['import_cycles_top']),
        'modules_in_cycles_all': sum(len(c) for c in data['import_cycles_all']),
    }
    grew = []
    for key, cap in budget['limits'].items():
        cur = now.get(key)
        if cur is None:
            continue
        if cur > cap:
            grew.append(f'{key}：{cap} → {cur}')
    assert not grew, ('技术债涨了（清债阶段只许降）：\n  ' + '\n  '.join(grew))


def test_no_new_module_joins_an_import_cycle(budget, data):
    """
    导入环的"成员"不许扩张。

    只看个数不够：把两个小环合成一个大环，个数还少了，债却更重。
    所以按**模块集合**比 —— 现在环里出现的模块，必须都在快照那份集合里。
    环被解开（模块退出环）是好事，不入判据。
    """
    allowed = set(budget['cycle_modules'])
    current = {m for c in data['import_cycles_all'] for m in c}
    new = current - allowed
    assert not new, ('这些模块**新卷进了导入环**（拆开或改成单向依赖）：\n  '
                     + '\n  '.join(sorted(new)))


def test_no_brand_new_cycle(budget, data):
    """也不许凭空冒出一个新环（形状不在快照里）。"""
    known = set(budget['cycle_shapes'])
    now = {'|'.join(c) for c in data['import_cycles_all']}
    fresh = now - known
    assert not fresh, ('出现了新的导入环：\n  ' + '\n  '.join(sorted(fresh)))


def test_report_is_usable(budget, data):
    """报告本身要能跑出这些数（扫不动就等于没装棘轮）。"""
    assert data['modules'] > 100, data['modules']
    assert data['functions'] > 1000, data['functions']
    assert data['thresholds']['long_func'] == budget['thresholds']['long_func']
