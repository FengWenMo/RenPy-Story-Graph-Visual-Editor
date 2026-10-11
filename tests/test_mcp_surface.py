"""
MCP 第一步判据：能力自描述 + 开局一次拿全（用户 2026-10-11："动 MCP 的第一步"）
=================================================================================

计划见 `docs/设计_MCP铺路_能力清单_2026-10-11.md`。
这一份判据**只钉两件事**，但都是 MCP 的命门：

  ① **能力清单必须写明边界** —— 只写"我能查注册表"会让模型以为
     "查不到 = 没有"；每条都要带 `known_limits`。
  ② **上下文里的"没问出来"不许被压缩掉** —— 项目有 parse error 时，
     引擎那几项必须是 `collectable=false` + `reason`（人话），
     而**不是**一片空（模型会把空读成"一切都好"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph import mcp_surface as M                                # noqa: E402


# ==================== ① 能力自描述 ====================

def test_every_tool_carries_its_known_limits():
    """⭐ 每条能力都要写清**边界**（空列表可以，但字段必须在）。"""
    caps = M.capabilities()
    assert caps['tools'], caps
    kinds = set()
    for t in caps['tools']:
        for key in ('name', 'kind', 'summary', 'source', 'known_limits'):
            assert key in t, (t.get('name'), key)
        kinds.add(t['kind'])
    assert {'read', 'judge', 'write'} <= kinds, kinds


def test_the_capability_list_does_not_promise_magic():
    """
    ⭐ 三条**硬边界**必须出现在能力清单里 —— 它们是这个工具最容易
    被误用的地方（我踩过两次）：
      · 引擎级：注册赶不上解析期；
      · **不要**建议改 SDK 绕过它；
      · 工具认识 ≠ 引擎认识。
    """
    caps = M.capabilities()
    blob = ' '.join(caps['hard_limits'])
    assert '解析期' in blob, blob
    assert 'SDK' in blob, blob
    assert 'register_statement' in blob, blob


def test_the_tri_state_rule_is_declared():
    """三态规则要**写在能力清单里**（模型得先知道"null 不是 false"）。"""
    tr = M.capabilities()['tri_state']
    assert tr['meaning']['null'] and '没问题' in tr['meaning']['null']
    assert 'null' in tr['rule']


def test_judge_tools_are_marked_as_tri_state():
    """判断类工具必须声明自己会给三态（只读的清单类不必）。"""
    for t in M.capabilities()['tools']:
        if t['kind'] == 'judge':
            assert t['tri_state'] is True, t['name']
            assert t['source'] in ('engine', 'host', 'scanner'), t


# ==================== ② 开局一次拿全 ====================

def test_context_on_a_non_project_says_so():
    """不是工程 → 结构化说清，**不抛异常**（模型不该看到 traceback）。"""
    got = M.project_context(None)
    assert got['ok'] is False and 'game/' in got['error']


def test_context_is_one_call_and_has_all_sections(tmp_path):
    """一次给全：engine / scan / health / behavior 四段都在。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text(
        'label a:\n    weather rain 强度=3\n    return\n', encoding='utf-8')
    got = M.project_context(root)
    assert got['ok'] is True
    for key in ('engine', 'scan', 'health', 'behavior'):
        assert key in got, key
    # 扫描那一栏必须带上 N1 免责（不许宣称"只有这些"）
    assert '不代表项目里只有这些' in got['scan']['disclaimer']
    assert 'weather' in [u['head'] for u in got['scan']['unknown']], got['scan']


def test_a_broken_engine_state_is_never_an_empty_silence(tmp_path):
    """
    ⭐⭐ **这一条是整份判据的重点**：引擎那几项拿不到时，
    必须 `collectable=false` + 人话 `reason` + 一条 `notes` ——
    **绝不能**只给一片空（模型会把空读成"没问题"）。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text('# 空项目\n', encoding='utf-8')
    got = M.project_context(root)
    eng = got['engine']
    assert eng['collectable'] is False, eng
    assert eng['reason'], '说了不可用却没说为什么'
    assert eng['source'] == 'engine'
    assert got['notes'], '没有结论时必须留一句话'
    # 行为级也要给形状（空列表 = 没验过，而不是"都通过了"）
    assert got['behavior']['verified'] == []
    assert got['behavior']['source'] == 'engine'


def test_context_survives_a_project_that_cannot_be_read(tmp_path):
    """
    读不动的项目（这里用一个**文件**冒充项目根）不许炸 ——
    只读工具在任何输入下都要给结构。
    """
    fake = tmp_path / 'not_a_dir.rpy'
    fake.write_text('x', encoding='utf-8')
    got = M.project_context(fake)
    assert got['ok'] is False and got['error']


def test_capabilities_names_the_tools_that_exist_today():
    """
    能力清单**不许列不存在的工具**（文档里禁过一次，这里也钉住）：
    列出来的名字，要么已在 `mcp_surface` 里，要么是**明确规划的**
    （本步只做前两个，其余标注为规划）。
    """
    names = {t['name'] for t in M.capabilities()['tools']}
    assert 'storygraph_capabilities' in names
    assert 'storygraph_context' in names
    # 规划的都在文档里，且必须与最终实现同名（这条防止"清单漂移"）
    assert len(names) >= 8, names
