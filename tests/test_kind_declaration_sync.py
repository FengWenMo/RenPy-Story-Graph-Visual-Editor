"""
契约守卫：**kind 声明与工厂实现必须同步**（风险清单 P2-3）
=========================================================

清单里那条"`_FACTORY_FIELDS['call']` 漏 `from_clause`，**自检没抓到**
（CALL 不参与哈希被跳过），属'声明与实现不同步'"——这个文件把它堵住。

原来的自检 `validate_hash_contract()` 只查"**参与哈希**的 kind"
（`_HASH_FIELDS[kind][0] is not None`）。设计上是成立的：不参与哈希就不会
因为漏字段而让 `content_hash` 错位。但代价是**不参与哈希的 kind 声不声明
都没人管** —— `CALL` 的 payload 里明明有 `from_clause`（`call X from Y`
的存档点 ID），声明里却只写了 `target`。

这份守卫换一个判据：**拿一个真的语句来对**，而不是靠人维护的清单自证。
"""

from __future__ import annotations

import pytest

from storygraph.core.statement import (
    Statement,
    StatementKind,
    _FACTORY_FIELDS,
    validate_hash_contract,
)


def _samples() -> dict:
    """
    每种 kind 造一个**带全部可选字段**的语句。

    `condition` / `source` 不是 payload 字段（它们是 `Statement` 自己的
    属性），所以不在这里。

    ⚠ **可选字段也要传**（2026-10-27 审计）：`make_say(tail=…)` /
    `make_menu(extras=…, name=…, args=…)` / `make_call(args=…)` /
    `make_return(value=…)` 都只在"真有"时才往 payload 里写键。老版本这里
    全用默认参数调用，于是**永远看不到**这些键 —— 判据对"可选字段"形同虚设
    （`_FACTORY_FIELDS` 里漏了 `tail`/`extras`/`name`/`args`/`value` 也没人发现）。
    """
    S = Statement
    return {
        StatementKind.SAY: S.make_say('e', '一句', tail=' (what_color="#f00")'),
        StatementKind.EXTEND: S.make_extend('一句'),
        StatementKind.SHOW: S.make_show(
            'ling happy', at=['left'], as_='q', with_='dissolve',
            zorder=1, onlayer='master', behind=['ye'], is_expression=False),
        StatementKind.SCENE: S.make_scene('bg room', at=['bgfit'],
                                          with_='fade', onlayer='master'),
        StatementKind.HIDE: S.make_hide('ling', with_='dissolve'),
        StatementKind.MENU: S.make_menu('选哪个', extras=['set seen'],
                                        name='m1', args='(x)'),
        StatementKind.MENU_OPTION: S.make_menu_option('左边'),
        StatementKind.JUMP: S.make_jump('target_label'),
        StatementKind.CALL: S.make_call('target_label', from_clause='_c_1',
                                        args='(1)'),
        StatementKind.RETURN: S.make_return(value='1'),
        StatementKind.PYTHON: S.make_python('x = 1'),
        StatementKind.COMMENT: S.make_comment('# 给美术的标注'),
        StatementKind.RAW: S.make_raw('pause 1.0', raw_kind='unmodeled'),
    }


def test_factory_fields_are_declared_for_every_kind():
    """有工厂方法的 kind，都必须在 `_FACTORY_FIELDS` 里声明。"""
    missing = sorted(set(_samples()) - set(_FACTORY_FIELDS))
    assert not missing, f'这些 kind 有工厂方法但没声明工厂字段：{missing}'


def test_declared_fields_actually_exist_in_payload():
    """声明的字段必须真的出现在工厂产出的 payload 里（防"声明了没实现"）。"""
    bad = []
    for kind, stmt in _samples().items():
        declared = set(_FACTORY_FIELDS.get(kind, ()))
        actual = set(stmt.payload)
        ghost = declared - actual
        if ghost:
            bad.append((kind, sorted(ghost), sorted(actual)))
    assert not bad, ('声明了但 payload 里没有的字段（声明与实现不同步）：\n'
                     + '\n'.join(f'  {k}: 声明有 {g}，实际 {a}'
                                 for k, g, a in bad))


def test_payload_fields_are_all_declared():
    """
    工厂产出的 payload 字段必须**全部**在声明里 —— 这就是会抓到
    `CALL` 漏 `from_clause` 的那一条。
    """
    bad = []
    for kind, stmt in _samples().items():
        declared = set(_FACTORY_FIELDS.get(kind, ()))
        actual = set(stmt.payload)
        undeclared = actual - declared
        if undeclared:
            bad.append((kind, sorted(undeclared)))
    assert not bad, (
        '工厂产出的字段没在 `_FACTORY_FIELDS` 里声明（漏声明）：\n'
        + '\n'.join(f'  {k}: {u}' for k, u in bad)
        + '\n\n修法：把缺的字段补进 `storygraph/core/statement.py` 的 '
          '`_FACTORY_FIELDS`。')


def test_call_declares_from_clause():
    """
    点名一条：`call X from Y` 的存档点 ID。

    风险清单 P2-3 记的就是它 —— 漏了之后 `validate_hash_contract()`
    抓不到（CALL 不参与哈希），于是"声明与实现不同步"能长期潜伏。
    真实项目有 7 条 `call … from …`，丢了存档点会让回滚/读档错位。
    """
    assert 'from_clause' in _FACTORY_FIELDS[StatementKind.CALL], \
        '_FACTORY_FIELDS[CALL] 必须声明 from_clause'


def test_optional_payload_fields_are_declared_too():
    """
    **只在"真有"时才进 payload 的字段也要声明**（2026-10-27 审计）。

    这些键默认调用时根本不出现，所以"漏声明"不会让任何判据变红 ——
    除非样例把可选参数都传上（见 `_samples()` 的说明）。
    """
    expected = {
        StatementKind.SAY: {'tail'},
        StatementKind.MENU: {'extras', 'name', 'args'},
        StatementKind.CALL: {'args'},
        StatementKind.RETURN: {'value'},
    }
    for kind, keys in expected.items():
        declared = set(_FACTORY_FIELDS.get(kind, ()))
        assert keys <= declared, f'{kind} 少了可选字段的声明：{sorted(keys - declared)}'


def test_hash_contract_still_passes():
    """原有的哈希契约自检继续通过（不能为了新增守卫把它放宽）。"""
    assert validate_hash_contract() == []


@pytest.mark.parametrize('kind', [k for k in StatementKind.ALL
                                  if k != StatementKind.UNKNOWN])
def test_every_kind_in_all_is_declared_or_explicitly_absent(kind):
    """
    `StatementKind.ALL` 里的每个 kind 都要有明确态度：
    要么在 `_FACTORY_FIELDS` 里声明，要么**明确**没有工厂方法。

    这条防止"新增第 16 个 kind 时漏改"（清单第 5 项的原始目标）。
    """
    if kind in _FACTORY_FIELDS:
        return
    # 没声明的必须也没有工厂方法 —— 否则就是漏声明
    name = 'make_' + kind
    assert not hasattr(Statement, name), (
        f'{kind} 有工厂方法 `Statement.{name}` 但没在 `_FACTORY_FIELDS` '
        f'里声明 —— 新增 kind 时漏改了。')
