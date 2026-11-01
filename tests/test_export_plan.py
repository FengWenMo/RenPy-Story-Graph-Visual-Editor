"""
tests/test_export_plan.py
=========================
`app.export_plan` 的纯判定单测 —— **不造会话、不建 Qt、不写磁盘**。

为什么这几条值得单测：它们决定"被门禁拦下的导出，还要不要给用户一个
「仍然覆盖」的按钮"。判错的方向有两条，都是事故：

  · 放太宽（把语法错也当成可覆盖）→ 用户一点就把跑不起来的脚本写进
    `game/`，游戏起不来；
  · 放太窄（手写目标也不给）→ 用户只剩"先删掉再导出"这一条路
    （2026-10-28 实测报的正是这一条）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from storygraph.app import export_plan


ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
#  默认路径：有源文件就原地写回（审计 P1-7）
# ---------------------------------------------------------------------------

def test_with_a_source_file_the_default_is_that_file(tmp_path):
    src = tmp_path / 'game' / 'chapter1.rpy'
    assert export_plan.default_output_path(src, tmp_path) == src


def test_without_a_source_file_it_falls_back_to_build_renpy(tmp_path):
    out = export_plan.default_output_path(None, tmp_path)
    assert out == tmp_path / 'build' / 'renpy' / 'out.generated.rpy'


def test_without_a_project_root_it_is_relative_to_cwd():
    out = export_plan.default_output_path(None, None)
    assert out == Path('.') / 'build' / 'renpy' / 'out.generated.rpy'
    assert not out.is_absolute()


def test_string_arguments_are_accepted():
    """会话那两个属性可能是 `str`，也可能是 `Path`（没加载项目时还是 `None`）。"""
    assert export_plan.default_output_path('a/b.rpy', None) == Path('a/b.rpy')
    assert (export_plan.default_output_path(None, 'proj')
            == Path('proj') / 'build' / 'renpy' / 'out.generated.rpy')


# ---------------------------------------------------------------------------
#  门禁问题 → 给人看的话 / 给前端判的代码
# ---------------------------------------------------------------------------

class _Issue:
    def __init__(self, code='', message=None):
        self.code = code
        if message is not None:
            self.message = message

    def __str__(self):
        return f'{self.code}: boom'


def test_issue_message_prefers_the_message_attribute():
    assert export_plan.issue_message(_Issue('X', '人看的一句话')) == '人看的一句话'


def test_issue_message_falls_back_to_str():
    """单测与第三方实现里塞过裸字符串 / 没有 `.message` 的对象。"""
    assert export_plan.issue_message(_Issue('X')) == 'X: boom'
    assert export_plan.issue_message('裸字符串') == '裸字符串'


def test_block_codes_reads_code_only():
    codes = export_plan.block_codes([_Issue('HANDWRITTEN_TARGET'),
                                     _Issue('OPAQUE_LOST'),
                                     _Issue()])
    assert codes == ['HANDWRITTEN_TARGET', 'OPAQUE_LOST', '']


def test_block_codes_accepts_none():
    assert export_plan.block_codes(None) == []


# ---------------------------------------------------------------------------
#  能不能给"仍然覆盖"的逃生舱
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('result, expected', [
    ({'ok': False, 'codes': ['HANDWRITTEN_TARGET']}, True),
    ({'ok': False, 'codes': ['HANDWRITTEN_TARGET', 'OPAQUE_LOST']}, True),
    ({'ok': False, 'codes': ['PYTHON_SYNTAX_ERROR']}, False),
    ({'ok': False, 'codes': ['LABEL_COLLISION']}, False),
    ({'ok': False, 'codes': []}, False),
    ({'ok': False}, False),
    ({}, False),                                  # 老调用方会传没有 ok 的字典
    ({'codes': ['HANDWRITTEN_TARGET']}, True),    # 同上：没有 ok 但报了这条
    ({'ok': True, 'codes': ['HANDWRITTEN_TARGET']}, False),   # 成功了就没有"覆盖"
    (None, False),
])
def test_overridable(result, expected):
    assert export_plan.overridable(result) is expected


def test_only_the_handwritten_target_code_is_overridable():
    """
    新增门禁代码时**必须显式决定**它算不算"可覆盖"。
    这条用例的作用是让那个决定发生在这里，而不是被默认放行。
    """
    assert export_plan.OVERRIDABLE_CODES == frozenset({'HANDWRITTEN_TARGET'})


# ---------------------------------------------------------------------------
#  别再把这条判据抄第二份（这才是这次服务化的动机）
# ---------------------------------------------------------------------------

def test_no_module_hand_writes_the_codes_membership_check():
    """
    从前 `ui_qt.window_export` 与 `ui_qt.window_graph_ops` **各写了一份**
    `'HANDWRITTEN_TARGET' in codes`。两处漂移的后果是同一个被拦下的导出，
    一个入口给逃生舱、另一个不给 —— 而两边都"看起来正常"。
    现在判据只有一个出处（本模块），谁再手写一份就红。
    """
    pattern = re.compile(r"""HANDWRITTEN_TARGET['"]\s+in\b""")
    offenders = []
    for path in ROOT.joinpath('storygraph').rglob('*.py'):
        text = path.read_text(encoding='utf-8', errors='replace')
        if pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], offenders


def test_both_ui_entrances_go_through_the_service():
    """两处门禁的**行为**必须一致（判据来源同一份，调用点也要真的用上）。"""
    from storygraph.ui_qt.window_export import overwritable

    blocked = {'ok': False, 'codes': ['HANDWRITTEN_TARGET']}
    assert overwritable(blocked) is export_plan.overridable(blocked) is True
    bad = {'ok': False, 'codes': ['PYTHON_SYNTAX_ERROR']}
    assert overwritable(bad) is export_plan.overridable(bad) is False

    src = (ROOT / 'storygraph' / 'ui_qt' / 'window_graph_ops.py').read_text(
        encoding='utf-8')
    assert 'export_plan.overridable(' in src
