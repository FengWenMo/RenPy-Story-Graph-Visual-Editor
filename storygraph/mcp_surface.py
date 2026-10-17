"""
**MCP 铺路的第一步**：能力自描述 + 开局一次拿全上下文
=========================================================================

> 计划见 `docs/archive/设计_MCP铺路_能力清单_2026-10-11.md`；
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
             'status': 'done', 'summary': '这份清单：能做什么、边界在哪',
             'source': 'static', 'tri_state': False, 'known_limits': []},
            {'name': 'storygraph_project_context', 'kind': 'read',
             'status': 'done', 'summary': '一次拿全：引擎事实/扫描发现/程序声明/体检/行为级',
             'source': 'engine+scanner+table', 'tri_state': True,
             'known_limits': [
                 '项目有 parse error 时，引擎那几项**没有结论**（不是没问题）；'
                 '这时 `engine.collectable=false` 并给出第一处位置']},
            {'name': 'storygraph_scan_unknown', 'kind': 'read',
             'status': 'done', 'summary': '扫描"工具不认识的写法"',
             'source': 'scanner', 'tri_state': False,
             'known_limits': [
                 '规则 N1：只说"我认出了这些"，**不代表项目里只有这些**',
                 '与台词**字面同形**的写法（`mycue "闪电"`）分不出来 —— '
                 '那要问引擎或程序的验证函数']},
            {'name': 'storygraph_ask_engine', 'kind': 'judge',
             'status': 'done', 'summary': '问引擎认不认某个写法（跑 lint）',
             'source': 'engine', 'tri_state': True,
             'known_limits': [
                 '引擎在第一处 parse error 就停 → 此时一律 `null` + `blocked_by`',
                 '隔离副本上问（绝不改真项目）']},
            {'name': 'storygraph_verify_behavior', 'kind': 'judge',
             'status': 'done', 'summary': '真跑一次引擎，验"这一句真的执行了"',
             'source': 'engine', 'tri_state': True,
             'known_limits': [
                 '**两把锁**：引擎报 PASSED **且**证据文件真的存在',
                 '`Status: PASSED` 单独**不算**通过（`assert eval` 假命题也 PASSED）',
                 '需要显示后端 + 副本里要有 `gui/`、`fonts/`']},
            {'name': 'storygraph_host_validate', 'kind': 'execute',
             'status': 'done',
             'summary': '调程序自己写的验证函数（storygraph.host.py）'
                        '—— **会执行项目代码**，默认不执行',
             'source': 'host', 'tri_state': True,
             'known_limits': [
                 '程序**不管**的东西返回 `null`（≠ 程序说通过）',
                 '这是**任意代码执行**入口（exec，不是沙箱）：默认拒绝，'
                 '需项目 `storygraph.rules.json` 写 '
                 '`{"host_hooks": {"trust": true}}` 或调用时传 `trust=true`',
                 '未授权时 `available=false` + `denied=true`，'
                 '`reason` 会写清怎么授权']},
            {'name': 'storygraph_health', 'kind': 'read',
             'status': 'done', 'summary': '体检（结构化 + 文本两种渲染，同源）',
             'source': 'table+engine', 'tri_state': True,
             'known_limits': ['`severity=unknown` 表示"测不出来"，**不是**没问题']},
            {'name': 'storygraph_declare', 'kind': 'write',
             'summary': '把一条声明写进 storygraph.rules.json（唯一写入口）',
             'source': 'table', 'tri_state': False, 'status': 'done',
             'known_limits': ['只写清单文件；**不许**改资源表（硬红线）',
                              '`dry_run=True` 只算 diff 不写盘 —— '
                              '**先试算给人看，再决定真写**',
                              '回执必带 `written` / `diff` / `backup`']},
            {'name': 'storygraph_table_export', 'kind': 'write',
             'summary': '导出剧本表（**本步故意没做**，见 reason）',
             'source': 'table+engine', 'tri_state': False, 'status': 'planned',
             'known_limits': ['⚠ **还没做**：调它一律 `ok=False` + "还没实现"，'
                              '别把它当成导出成功']},
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
        return {'ok': False, 'source': 'none', 'root': str(project_root or ''),
                'reason': f'不是工程根（没有 game/）：{project_root}',
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


def _result(source, *, ok=True, reason='', **kw) -> dict:
    """所有工具的统一回执：**永远带 `source`**（谁说的）+ `ok` + `reason`。"""
    out = {'ok': bool(ok), 'source': source, 'reason': str(reason or '')}
    out.update(kw)
    return out


def scan_unknown(project_root) -> dict:
    """工具**不认识的写法**（扫描）。⚠ 带 N1 免责：不代表只有这些。"""
    try:
        from storygraph.story.project_conventions import unknown_statements
        rows = unknown_statements(project_root)
        return _result('scanner',
                       heads=[{'head': h, 'count': v.get('count'),
                               'why': v.get('why', '')} for h, v in rows.items()],
                       disclaimer='以上是「我认出的」，**不代表项目里只有这些**（N1）')
    except Exception as e:                          # noqa: BLE001
        swallow('mcp_surface.scan_unknown', e)
        return _result('scanner', ok=False, heads=[],
                       reason=f'扫描出错：{type(e).__name__}: {e}')


def discover(project_root) -> dict:
    """界面左边那一栏：**发现**还没声明的写法（含疑似写错的分开）。"""
    try:
        from storygraph.story.statement_registry import (clear_cache,
                                                         discover_undeclared)
        clear_cache()
        rows = discover_undeclared(project_root)
        return _result('scanner',
                       heads=[{'head': r.get('head'), 'count': r.get('count'),
                               'block': r.get('block'),
                               'suspect': r.get('suspect') or '',
                               'files': r.get('files') or []}
                              for r in rows])
    except Exception as e:                          # noqa: BLE001
        swallow('mcp_surface.discover', e)
        return _result('scanner', ok=False, heads=[],
                       reason=f'发现出错：{type(e).__name__}: {e}')


def engine_facts(project_root) -> dict:
    """已采集的引擎事实**可不可用**（采不到 ≠ 没问题）。"""
    try:
        from storygraph.core import engine_facts as EF
        path = EF.facts_path(project_root)
        if not path.is_file():
            return _result('engine', ok=False, collectable=False,
                           reason='还没采过引擎事实（先跑 engine-facts）')
        facts = EF.loads(path.read_text(encoding='utf-8'))
        ok, why = EF.is_collectable(facts)
        return _result('engine', ok=True, collectable=bool(ok), reason=why,
                       registry=sorted(EF.registered_statements(facts))
                       if ok else [],
                       lint=(facts.get('lint') or {}) if ok else {})
    except Exception as e:                          # noqa: BLE001
        swallow('mcp_surface.engine_facts', e)
        return _result('engine', ok=False, collectable=False,
                       reason=f'读引擎事实出错：{type(e).__name__}: {e}')


def registry_check(project_root, heads=()) -> dict:
    """
    "引擎注册表里有没有这些写法" —— 三态：`known` / `unknown` / **`unasked`**。

    ⚠ 采不到事实时**一律 `unasked`**（不是 `unknown`）—— "没问出来"与
    "引擎说没有"是两件事（本项目的假绿全出在这个混淆上）。
    """
    facts = engine_facts(project_root)
    if not facts.get('collectable'):
        return _result('engine', ok=False, known=[], unknown=[], unasked=list(heads),
                       reason=facts.get('reason') or '引擎事实不可用')
    reg = set(facts.get('registry') or [])
    want = [str(h) for h in (heads or ())]
    return _result('engine', ok=True,
                   known=[h for h in want if h in reg],
                   unknown=[h for h in want if h not in reg],
                   unasked=[] if want else [],
                   registry_size=len(reg))


def ask_engine(project_root, heads, *, sdk=None, timeout=300) -> dict:
    """问引擎认不认这些写法（**跑 lint、在副本上**）。三态，被挡住时全 null。"""
    try:
        from storygraph.pipeline import statement_probe as SP
        ans = SP.ask_many(project_root, list(heads or ()), sdk=sdk,
                          timeout=timeout)
        rows = []
        for head in ans.get('asked') or []:
            v = (ans.get('results') or {}).get(head) or {}
            rows.append({'head': head, 'recognized': v.get('recognized'),
                         'summary': v.get('summary') or '',
                         'evidence': (v.get('evidence') or [])[:1]})
        return _result('engine', ok=bool(ans.get('ok')),
                       reason=ans.get('reason') or '',
                       blocked_by=ans.get('blocked_by') or '', heads=rows)
    except Exception as e:                          # noqa: BLE001
        swallow('mcp_surface.ask_engine', e)
        return _result('engine', ok=False, heads=[],
                       reason=f'问引擎出错：{type(e).__name__}: {e}')


def verify_behavior(project_root, head, *, sdk=None,
                    timeout=180) -> dict:
    """验"这个写法真的能跑"（**两把锁**：引擎 PASSED **且**证据文件存在）。"""
    try:
        from storygraph.pipeline import statement_probe as SP
        v = SP.verify_head(project_root, head, sdk=sdk, timeout=timeout)
        return _result('engine', ok=bool(v.get('ok')),
                       verified=v.get('verified'), reason=v.get('reason') or '',
                       summary=v.get('summary') or '',
                       label=v.get('label') or '',
                       evidence=v.get('evidence') or '',
                       where=SP.first_parse_error(
                           ' '.join(v.get('raw_tail') or [])))
    except Exception as e:                          # noqa: BLE001
        swallow('mcp_surface.verify_behavior', e)
        return _result('engine', ok=False, verified=None,
                       reason=f'行为验证出错：{type(e).__name__}: {e}')


def host_validate(project_root, head, text, trust=None) -> dict:
    """
    调**程序自己写的**验证函数（`storygraph.host.py`）。`None` = 程序不管。

    ⚠ 这会**执行项目代码**（任意代码执行入口），所以默认不执行：
    `trust=True` 或项目 `storygraph.rules.json` 授权后才跑（见 host_hooks）。
    """
    from storygraph.story.host_hooks import load_host_hooks
    hooks = load_host_hooks(project_root, trust=trust)
    if not hooks.available:
        if getattr(hooks, 'denied', False):
            return _result('none', ok=True, verified=None, available=False,
                           denied=True,
                           reason='发现了 storygraph.host.py，但**未授权执行**'
                                  '（这是可执行入口，默认不跑）',
                           notes=hooks.notes)
        return _result('none', ok=True, verified=None, available=False,
                       reason='项目没有提供 storygraph.host.py（程序不参与判断）')
    ok, why = hooks.validate(head, text)
    return _result('host', ok=True, verified=ok, available=True, why=why,
                   notes=hooks.notes)


def health(project_root) -> dict:
    """体检（**结构化**）。`severity=unknown` 表示测不出来，**不是**没问题。"""
    try:
        from storygraph.app.asset_report import health_dict
        from storygraph.app.editor import EditorSession
        # ⚠⚠ 真机踩到两处（我的第一版就错在这儿）：
        #   ① 只给 `project_root=` 不够 —— `asset_health_report()` 走的是
        #      `assets_registry()`，它要**先 `load_assets()`** 才建得出来；
        #   ② 而 `registry_provider=` 喂的是**另一个**方法（`registry()`）。
        #   不调 `load_assets()` 的结果是**一律**报"还没有打开项目 / 读不到资源表"
        #   —— 看起来像"项目没问题"，实际是**没测**（正是本项目最忌的假绿）。
        session = EditorSession(project_root=project_root)
        session.load_assets()
        hd = health_dict(session.asset_health_report())
        return _result('table+engine', ok=bool(hd.get('ok')),
                       reason=hd.get('error') or '',
                       problems=hd.get('problems'),
                       findings=hd.get('findings') or [],
                       capability=hd.get('capability') or {},
                       notes=hd.get('notes') or [])
    except Exception as e:                          # noqa: BLE001
        swallow('mcp_surface.health', e)
        return _result('table+engine', ok=False, findings=[],
                       reason=f'体检没做成：{type(e).__name__}: {e}')


def _receipt(source, *, ok=True, reason='', written=None, diff='', backup='',
             dry_run=False, **kw) -> dict:
    """
    ⭐ **统一的"改了什么"回执**（第 4 步的第一等公民）。

    所有**写盘**的工具都返回同一种形状 —— 模型最需要知道的就是"你动了什么"：

        {'ok', 'source', 'reason',
         'written': [路径…],      # 真的被写的文件（空 = 什么都没改）
         'diff': 文本,            # 改了什么（人能读、模型能引）
         'backup': 路径,          # 备份在哪（能回滚）
         'dry_run': bool}         # 这次只是试算，没写盘

    ⚠ 绝不"写了但不说" —— 那是不可逆的破坏里最糟的一种。
    """
    out = {'ok': bool(ok), 'source': source, 'reason': str(reason or ''),
           'written': list(written or ()), 'diff': str(diff or ''),
           'backup': str(backup or ''), 'dry_run': bool(dry_run)}
    out.update(kw)
    return out


def _manifest_path(project_root):
    from storygraph.core import engine_facts as EF                  # noqa: F401
    from storygraph.story.project_conventions import MANIFEST_NAMES
    root = Path(project_root)
    return root / MANIFEST_NAMES[0]


def declare(project_root, name, *, label='', docs='', block=None, params=(),
            dry_run=False) -> dict:
    """
    **把一个写法写进项目清单**（`storygraph.rules.json`，唯一写入口）。

    这是 MCP 里**第一个会改盘的**工具，所以护栏最厚：

      · `dry_run=True` → **只算 diff、不写盘**（回执里 `written=[]`）——
        模型该先试算、给人看，再决定要不要真写；
      · 真写时**先记 diff 与备份路径**，回执里必须有；
      · 走的是**唯一写入口** `statement_registry.declare_statement`
        （它自己负责"同名=更新""不覆盖手写清单""拒绝空名字"）；
      · 只碰清单文件 —— **资源表一个字都不许动**（硬红线）。

    返回 `_receipt(...)`（含 `action`：`added` / `updated` / `refused`）。
    """
    try:
        from storygraph.authoring.learn_statement import build_declaration
        from storygraph.story.statement_registry import (clear_cache,
                                                         declare_statement)
        built = build_declaration(name, label=label, docs=docs, block=block,
                                  params=list(params or ()))
        if not built.get('ok'):
            return _receipt('table', ok=False,
                            reason=str(built.get('reason') or '声明不合法'),
                            action='refused', warnings=built.get('warnings'))
        # ⚠ `declare_statement(project_root, name, label=…, columns=…, block=…,
        #   docs=…)` 收的是**字段**，不是 entry 字典（我第一版当成 entry 传，
        #   于是它报"语句名不能为空"—— 判据当场抓到）。
        entry = {'name': built.get('name'), 'label': built.get('label'),
                 'docs': built.get('docs'), 'block': built.get('block'),
                 'columns': built.get('columns') or []}
        path = _manifest_path(project_root)
        before = path.read_text(encoding='utf-8') if path.is_file() else ''
        if dry_run:
            after = _preview_manifest(before, entry)
            return _receipt('table', ok=True, written=[], dry_run=True,
                            diff=_manifest_diff(before, after),
                            reason='（试算：没有写盘）', action='would-write',
                            warnings=built.get('warnings'))
        backup = ''
        if before:
            bak = path.with_suffix(path.suffix + '.bak')
            bak.write_text(before, encoding='utf-8')
            backup = str(bak)
        got = declare_statement(project_root, entry['name'], label=entry['label'],
                                columns=entry['columns'],
                                block=entry['block'], docs=entry['docs'])
        clear_cache()
        after = path.read_text(encoding='utf-8') if path.is_file() else ''
        written = [str(path)] if got.get('ok') and after != before else []
        return _receipt('table', ok=bool(got.get('ok')),
                        reason=str(got.get('reason') or ''),
                        written=written, backup=backup,
                        diff=_manifest_diff(before, after),
                        action=str(got.get('action') or ''),
                        warnings=built.get('warnings'))
    except Exception as e:                          # noqa: BLE001
        swallow('mcp_surface.declare', e)
        return _receipt('table', ok=False, reason=f'写清单出错：'
                        f'{type(e).__name__}: {e}', action='refused')


def _preview_manifest(before: str, entry: dict) -> str:
    """试算：把这条声明并进清单文本（**不写盘**）。纯函数。"""
    import json
    try:
        data = json.loads(before) if before.strip() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    stmts = data.get('statements')
    if not isinstance(stmts, list):
        stmts = []
    name = str(entry.get('name') or '')
    stmts = [s for s in stmts if not (isinstance(s, dict)
                                      and str(s.get('name')) == name)]
    stmts.append(entry)
    data['statements'] = stmts
    return json.dumps(data, indent=2, ensure_ascii=False) + '\n'


def _manifest_diff(before: str, after: str) -> str:
    """两段清单文本的**行级 diff**（够用就好：只报增删的行）。纯函数。"""
    if before == after:
        return ''
    old = set(before.splitlines())
    new = set(after.splitlines())
    lines = ['- ' + ln for ln in before.splitlines() if ln not in new][:20]
    lines += ['+ ' + ln for ln in after.splitlines() if ln not in old][:20]
    return '\n'.join(lines)


def table_export(project_root, *, dry_run=False) -> dict:
    """
    导出剧本表 —— ⚠ **本步（第 4 步）故意没做**，只留这个诚实占位。

    为什么宁可"没做"也不给个半成品：我第一版这里只"读了注册表、比了 mtime"
    就返回 `ok=True, written=[]` —— 那是**假绿**（模型会以为导出成功了）。
    本项目最忌这个（三处假绿都是同一形状），所以：

      · 真要做，得把"beats → excel"这条链（`export_beats_to_excel`
        + 候选表刷新 + 缩略图缓存）整条接进来，并给出**写了哪些文件**；
      · 在那之前，这里一律 `ok=False` + `reason='还没实现'`，
        能力清单里也标成 `planned`。
    """
    return _receipt('table+engine', ok=False, written=[],
                    reason='还没实现（第 4 步只做了 declare）—— '
                           '别把它当成"导出成功"')


__all__ = ['FALSE', 'TRUE', 'UNKNOWN', 'ask_engine', 'capabilities', 'declare',
           'discover', 'engine_facts', 'health', 'host_validate',
           'project_context', 'registry_check', 'scan_unknown', 'table_export',
           'verify_behavior']
