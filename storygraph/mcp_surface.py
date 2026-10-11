"""
**MCP 铺路的第一步**：能力自描述 + 开局一次拿全上下文
=========================================================================

> 计划见 `docs/设计_MCP铺路_能力清单_2026-10-11.md`；
> 总纲：**"模型的价值不在于不用工具，而在于用工具时成本最低"**。

这一步**故意只做两个纯只读函数**（零风险），先把 MCP 的两条硬约定立起来：

  ① **能力自描述**（`capabilities()`）—— 模型得先知道"现在能做什么、
     **各自的已知边界是什么**"。⚠ 边界要写进去：例如"同形的写法问不出来"，
     否则模型会拿一个必然失败的问法反复试。
  ② **开局一次拿全**（`project_context()`）—— 模型最怕"分五步问五次、
     每步都可能猜错"。一次给全：引擎事实可不可用、扫描发现了什么、
     程序声明了什么、体检结论、行为级验过没有。

## ⭐ MCP 特有纪律：把"不确定"做成返回值的一等公民

本项目**三处假绿**（`assert eval` 恒真 / `Status` 单独用 / `None` 折成 `False`）
在 MCP 场景下会**放大** —— 模型不会怀疑工具的返回值，它会**直接写进答案**。所以：

  · 每个结论都带 **`source`**（`engine` / `scanner` / `host` / `table` / `none`）；
  · 表达"有没有"用**三态**（`true` / `false` / `null`），`null` = **没问出来**；
  · `null` 必须带**人话 `reason`**（例如"引擎在第一处 parse error 停下了"）。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow

#: 三态（**不许**用布尔糊过去）
TRUE, FALSE, UNKNOWN = True, False, None


def capabilities() -> dict:
    """
    这个工具**能做什么、不能做什么**（纯函数，不碰项目）。

    ⚠ `known_limits` 是这一节的重点：MCP 的工具描述如果只写"我能查注册表"，
    模型会以为"查不到 = 没有"。所以每条都把**已知边界**写清楚。
    """
    return {
        'tool': 'storygraph（Ren\'Py 剧情结构编辑器）',
        'principle': ('工具的价值不在于"不依赖程序"，而在于'
                      '"依赖程序时成本最低"；'
                      '模型的价值不在于"不用工具"，而在于"用工具时成本最低"'),
        'tri_state': {'meaning': {'true': '确证', 'false': '确证不成立',
                                  'null': '没问出来（**不是没问题**）'},
                      'rule': 'null 必须带 reason；不许把 null 折成 false'},
        'tools': [
            {'name': 'storygraph_capabilities', 'kind': 'read',
             'summary': '这份清单：能做什么、边界在哪',
             'source': 'static', 'tri_state': False, 'known_limits': []},
            {'name': 'storygraph_context', 'kind': 'read',
             'summary': '一次拿全：引擎事实/扫描发现/程序声明/体检/行为级',
             'source': 'engine+scanner+table', 'tri_state': True,
             'known_limits': [
                 '项目有 parse error 时，引擎那几项**没有结论**（不是没问题）；'
                 '这时 `engine.collectable=false` 并给出第一处位置']},
            {'name': 'storygraph_scan_unknown', 'kind': 'read',
             'summary': '扫描"工具不认识的写法"',
             'source': 'scanner', 'tri_state': False,
             'known_limits': [
                 '规则 N1：只说"我认出了这些"，**不代表项目里只有这些**',
                 '与台词**字面同形**的写法（`mycue "闪电"`）分不出来 —— '
                 '那要问引擎或程序的验证函数']},
            {'name': 'storygraph_ask_engine', 'kind': 'judge',
             'summary': '问引擎认不认某个写法（跑 lint）',
             'source': 'engine', 'tri_state': True,
             'known_limits': [
                 '引擎在第一处 parse error 就停 → 此时一律 `null` + `blocked_by`',
                 '隔离副本上问（绝不改真项目）']},
            {'name': 'storygraph_verify_behavior', 'kind': 'judge',
             'summary': '真跑一次引擎，验"这一句真的执行了"',
             'source': 'engine', 'tri_state': True,
             'known_limits': [
                 '**两把锁**：引擎报 PASSED **且**证据文件真的存在',
                 '`Status: PASSED` 单独**不算**通过（`assert eval` 假命题也 PASSED）',
                 '需要显示后端 + 副本里要有 `gui/`、`fonts/`']},
            {'name': 'storygraph_host_validate', 'kind': 'judge',
             'summary': '调程序自己写的验证函数（storygraph.host.py）',
             'source': 'host', 'tri_state': True,
             'known_limits': ['程序**不管**的东西返回 `null`（≠ 程序说通过）']},
            {'name': 'storygraph_health', 'kind': 'read',
             'summary': '体检（结构化 + 文本两种渲染，同源）',
             'source': 'table+engine', 'tri_state': True,
             'known_limits': ['`severity=unknown` 表示"测不出来"，**不是**没问题']},
            {'name': 'storygraph_declare', 'kind': 'write',
             'summary': '把一条声明写进 storygraph.rules.json（唯一写入口）',
             'source': 'table', 'tri_state': False,
             'known_limits': ['只写清单文件；**不许**改资源表（硬红线）']},
        ],
        'hard_limits': [
            '引擎级：`init python` 里注册的写法**赶不上解析期**，'
            '所以"发现未知写法"与"问引擎"不能共存（见终极方案 §12.1/§12.7）',
            '**不要**建议使用者去改 SDK / 预注册来绕过它 —— 那条路已证伪',
            '工具认识 ≠ 引擎认识：`rules.json` 管工具，`register_statement` 管引擎',
        ],
    }


def project_context(project_root) -> dict:
    """
    **开局一次拿全**（纯只读；任何一项拿不到都如实记 `reason`，不抛异常）。

    返回（要点）::

        {'ok': bool, 'root': str,
         'engine': {'collectable': bool, 'reason': str, 'source': 'engine',
                    'registry': [...], 'lint': {...}},
         'scan': {'unknown': [...], 'declared': [...], 'discovered': [...],
                  'source': 'scanner', 'disclaimer': '不代表只有这些'},
         'health': {'problems': int, 'findings': [...], 'source': 'table+engine'},
         'behavior': {'verified': [...], 'failed': [...], 'unverified': [...]},
         'notes': [...]}
    """
    out = {'ok': True, 'root': str(project_root or ''), 'engine': {},
           'scan': {}, 'health': {}, 'behavior': {}, 'notes': []}
    root = Path(project_root) if project_root else None
    if root is None or not (root / 'game').is_dir():
        return {'ok': False, 'root': str(project_root or ''),
                'error': f'不是工程根（没有 game/）：{project_root}',
                'engine': {}, 'scan': {}, 'health': {}, 'behavior': {},
                'notes': []}

    # ---- 引擎事实（可不可用**必须**显式给）----
    try:
        from storygraph.core import engine_facts as EF
        facts = EF.loads(EF.facts_path(root).read_text(encoding='utf-8')) \
            if EF.facts_path(root).is_file() else {}
        collectable, why = EF.is_collectable(facts) if facts else (False, '')
        out['engine'] = {
            'collectable': bool(collectable),
            'reason': why or ('' if facts else
                              '还没采过引擎事实（先跑 engine-facts）'),
            'source': 'engine',
            'registry': sorted(EF.registered_statements(facts))
            if collectable else [],
            'lint': ((facts.get('lint') or {}) if collectable else {}),
        }
        if not collectable:
            out['notes'].append('引擎那几项**没有结论**（不是"没问题"）：'
                                + (why or '没采过'))
        behavior = EF.behavior_summary(facts) if facts else {}
        out['behavior'] = {k: behavior.get(k, []) for k in
                           ('verified', 'failed', 'unverified')}
        out['behavior']['source'] = 'engine'
        out['behavior']['total'] = int(behavior.get('total') or 0)
    except Exception as e:                          # noqa: BLE001 - 读不到就如实说
        swallow('mcp_surface.context.engine', e)
        out['engine'] = {'collectable': False, 'source': 'engine',
                         'reason': f'读引擎事实出错：{type(e).__name__}: {e}'}
        out['notes'].append(out['engine']['reason'])

    # ---- 扫描（工具不认识 / 已声明 / 界面那一栏）----
    try:
        from storygraph.story.project_conventions import unknown_statements
        from storygraph.story.statement_registry import (StatementRegistry,
                                                         clear_cache,
                                                         discover_undeclared)
        clear_cache()
        unknown = unknown_statements(root)
        out['scan'] = {
            'unknown': [{'head': h, 'count': v.get('count'),
                         'why': v.get('why', '')} for h, v in unknown.items()],
            'declared': sorted(StatementRegistry.for_project(root).names()),
            'discovered': [r.get('head') for r in discover_undeclared(root)],
            'source': 'scanner',
            'disclaimer': '以上是「我认出的」，**不代表项目里只有这些**（规则 N1）',
        }
    except Exception as e:                          # noqa: BLE001
        swallow('mcp_surface.context.scan', e)
        out['scan'] = {'unknown': [], 'declared': [], 'discovered': [],
                       'source': 'scanner',
                       'reason': f'扫描出错：{type(e).__name__}: {e}'}
        out['notes'].append(out['scan']['reason'])

    # ---- 体检（结构化版；拿不到也给形状）----
    try:
        from storygraph.app.asset_report import health_dict
        from storygraph.app.editor import EditorSession
        rep = EditorSession(project_root=root).asset_health_report()
        hd = health_dict(rep)
        out['health'] = {k: hd.get(k) for k in
                         ('ok', 'error', 'problems', 'findings', 'source')}
        if hd.get('notes'):
            out['notes'].extend(hd['notes'])
    except Exception as e:                          # noqa: BLE001
        swallow('mcp_surface.context.health', e)
        out['health'] = {'ok': False, 'problems': 0, 'findings': [],
                         'source': 'table+engine',
                         'error': f'体检没做成：{type(e).__name__}: {e}'}
        out['notes'].append(out['health']['error'])
    return out


__all__ = ['FALSE', 'TRUE', 'UNKNOWN', 'capabilities', 'project_context']
