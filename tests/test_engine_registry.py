"""第一层判据：**直接问引擎"你注册了什么"**（用户 2026-10-11 的四层方案第一层）。

真机实验拿到的三个决定性事实（都写进判据，免得以后重踩）：

  ① `renpy.statements.registry` **不是普通 dict** ——
     `isinstance(reg, dict)` 为**假**！我第一版就是被这一条把整个注册表滤成了
     空字典（探针里一句 `if not isinstance(reg, dict): return {}`）。
     它是"像 dict 的映射"（有 `.items()`），所以只能按**鸭子类型**取。
  ② 它的键是**元组**：`('play', 'music')` / `('pause',)` ——
     多词语句按词拆开，渲染成"写法"要用空格接起来。
  ③ 里面有 **23 条**（最小工程），连**引擎内置**语句都在
     （`pause` / `play music` …）—— 所以这一层比我们自己的词表更权威。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core import engine_facts as EF                        # noqa: E402


def _facts_with_registry(statements, *, clean=True):
    return EF.build_facts(
        {'statements': [(EF.SRC_PROBE, statements)]},
        collection={'parse_clean': clean, 'probe_complete': clean,
                    'lint_exit': 0})


#: 真机（最小工程，注册了 weather）实测的形状
REG = {
    'pause': {'name': 'pause', 'has_parse': True, 'has_execute': True,
              'block': False},
    'play music': {'name': 'play music', 'has_parse': True, 'block': False},
    'weather': {'name': 'weather', 'has_parse': True, 'block': False},
    'outfit': {'name': 'outfit', 'has_parse': True, 'block': True},
}


def test_registry_is_read_when_collectable():
    got = EF.registered_statements(_facts_with_registry(REG))
    assert set(got) == {'pause', 'play music', 'weather', 'outfit'}
    assert got['outfit']['block'] is True


def test_registry_says_nothing_when_facts_are_unusable():
    """
    ⚠ 采不到就是采不到（方案坑 4）：`parse_clean=False` 时探针根本没跑，
    这时**一个名字都不许给**（给了就等于宣称"引擎说没有"）。
    """
    for kw in ({'clean': False},):
        assert EF.registered_statements(_facts_with_registry(REG, **kw)) == {}
    assert EF.registered_statements(None) == {}
    assert EF.registered_statements({}) == {}


def test_builtin_statements_are_in_the_registry():
    """
    ⭐ 这一层比我们自己的词表**更权威**：引擎内置语句也在名单里
    （真机 23 条里有 `pause` / `play music`）。
    """
    names = EF.statement_names(_facts_with_registry(REG))
    assert 'pause' in names
    assert 'play music' in names, '多词语句要用空格接起来（键是元组）'


def test_shape_of_a_registered_statement():
    facts = _facts_with_registry(REG)
    assert EF.statement_shape(facts, 'outfit')['block'] is True
    assert EF.statement_shape(facts, 'weather')['has_parse'] is True
    assert EF.statement_shape(facts, 'sg_definitely_unknown') == {}


def test_statements_is_a_known_fact_key():
    """`statements` 必须在已知事实键里（否则校验会把它当"多出来的键"报出来）。"""
    assert 'statements' in EF.FACT_KEYS
    facts = _facts_with_registry(REG)
    assert EF.validate_facts(facts) == [], EF.validate_facts(facts)
