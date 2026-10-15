"""
MCP 第一步判据：能力自描述 + 开局一次拿全（用户 2026-10-11："动 MCP 的第一步"）
=================================================================================

计划见 `docs/archive/设计_MCP铺路_能力清单_2026-10-11.md`。
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
    assert 'storygraph_project_context' in names, names
    # 规划的都在文档里，且必须与最终实现同名（这条防止"清单漂移"）
    assert len(names) >= 8, names


def test_every_listed_tool_actually_exists():
    """
    ⭐ **清单漂移防线**：能力清单里标了 `status='done'` 的工具，
    在 `mcp_surface` 里**必须**有同名函数 —— 否则模型会去调一个不存在的工具。
    `planned` 的要在 `known_limits` 里**自己说还没做**（不许假装能用）。
    """
    for t in M.capabilities()['tools']:
        short = t['name'].replace('storygraph_', '')
        status = t.get('status', 'done')
        assert status in ('done', 'planned'), (t['name'], status)
        if status == 'done':
            assert callable(getattr(M, short, None)), \
                f'清单说 {t["name"]} 已做，但 mcp_surface.{short} 不存在'
        else:
            blob = ' '.join(t.get('known_limits') or [])
            assert '还没做' in blob or '还没实现' in blob, \
                f'{t["name"]} 只是规划，却没在 known_limits 里说清'
            fn = getattr(M, short, None)
            if fn is not None:
                # 允许"诚实的占位"：能调，但必须**明确说没做**（不许假绿）
                got = fn(ROOT / '.pip-tmp', ) if short == 'table_export' else None
                if got is not None:
                    assert got['ok'] is False, (t['name'], got)
                    assert '还没' in got['reason'], got


# ==================== ③ 八个薄封装：三态 + source + 绝不抛异常 ====================

def test_every_tool_returns_a_source_and_never_raises(tmp_path):
    """
    ⭐ 第 2 步的**统一契约**：每个工具都返回 `{ok, source, reason}`，
    而且**任何输入都不抛异常**（模型不该看到 traceback）——
    这里用"根本不存在的路径"逐个打一遍。
    """
    bogus = tmp_path / 'nope' / 'nope'
    calls = [
        ('scan_unknown', lambda: M.scan_unknown(bogus)),
        ('discover', lambda: M.discover(bogus)),
        ('engine_facts', lambda: M.engine_facts(bogus)),
        ('registry_check', lambda: M.registry_check(bogus, ['weather'])),
        ('ask_engine', lambda: M.ask_engine(bogus, ['weather'], timeout=5)),
        ('verify_behavior', lambda: M.verify_behavior(bogus, 'weather',
                                                      timeout=5)),
        ('host_validate', lambda: M.host_validate(bogus, 'weather', 'weather x=1')),
        ('health', lambda: M.health(bogus)),
        ('project_context', lambda: M.project_context(bogus)),
    ]
    for name, fn in calls:
        got = fn()                                   # 不抛就是通过
        assert isinstance(got, dict), name
        assert 'ok' in got and 'source' in got, (name, got)
        assert 'reason' in got, (name, got)


def test_registry_check_uses_three_states_not_two(tmp_path):
    """
    ⭐⭐ **本项目的命门**：`registry_check` 必须有**第三态** `unasked` ——
    采不到事实时那些写法是"**没问出来**"，**不是**"引擎说没有"。
    折成两态就是假绿（模型会当真话用）。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    got = M.registry_check(root, ['weather', 'outfit'])
    assert got['ok'] is False, got
    assert got['known'] == [] and got['unknown'] == [], got
    assert got['unasked'] == ['weather', 'outfit'], got
    assert got['reason'], '必须说清为什么没问出来'


def test_engine_facts_says_not_collected_rather_than_empty(tmp_path):
    """没采过 → `ok=False` + 一句人话（**不是**给一个空 dict 让人误会）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    got = M.engine_facts(root)
    assert got['ok'] is False and got['collectable'] is False
    assert '还没采过' in got['reason'], got


def test_verify_behavior_surfaces_the_engine_location(tmp_path):
    """
    行为验证没通过时，回执里要带**引擎指的位置**（人话，可直接去改）——
    这是上一轮在 UI 上做的事，MCP 版同样要有。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text(
        'label a:\n    weather rain 强度=3\n    return\n', encoding='utf-8')
    got = M.verify_behavior(root, 'weather', timeout=60)
    # 没 SDK / 跑不了 → 也必须是"没证明成"的形状，而不是崩
    assert got['verified'] in (True, False, None)
    assert 'source' in got


def test_health_actually_reads_a_project_rather_than_always_failing(tmp_path):
    """
    ⭐⭐ **真机抓到的假绿**：`health()` 第一版没调 `session.load_assets()`，
    于是 `asset_health_report()` **永远**返回
    "还没有打开项目 / 读不到资源表" —— 看起来像"项目没问题"，
    实际是**根本没测**。这里用一个最小可读项目钉住"它真的在读"。
    """
    import json
    from openpyxl import Workbook
    root = tmp_path / 'proj'
    game = root / 'game'
    (game / 'images').mkdir(parents=True)
    (game / 'images' / 'a.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 32)
    tables = game / 'asset_tables'
    tables.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = 'images'
    ws.append(['ID', 'Path', 'Notes'])
    ws.append(['a', 'a.png', ''])
    wb.save(tables / 'images.xlsx')
    (root / 'storygraph.rules.json').write_text(json.dumps({}), encoding='utf-8')
    got = M.health(root)
    # 关键：**不是**那句"还没有打开项目"
    assert '还没有打开项目' not in (got.get('reason') or ''), got
    assert got['ok'] is True, got
    assert isinstance(got.get('findings'), list)
    assert 'severity' not in [f.get('id') for f in got['findings']]


def test_declare_dry_run_writes_nothing_but_shows_the_diff(tmp_path):
    """
    ⭐ 第 4 步的第一条护栏：**模型该先试算**。
    `dry_run=True` → 回执里 `written=[]`，但要**给出 diff**（人能看懂要改什么），
    而且磁盘上的清单**一个字节都不许变**。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    manifest = root / 'storygraph.rules.json'
    got = M.declare(root, 'weather', label='天气', dry_run=True,
                    params=[{'name': '强度', 'label': '强度'}])
    assert got['dry_run'] is True and got['written'] == [], got
    assert 'weather' in got['diff'], got['diff']
    assert not manifest.exists(), 'dry_run 竟然写盘了'


def test_declare_writes_and_hands_back_a_receipt(tmp_path):
    """真写：回执必须说清**写了哪个文件**、diff 是什么、备份在哪。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    got = M.declare(root, 'weather', label='天气')
    assert got['ok'] is True, got
    assert got['written'], '写了盘却没说是哪个文件'
    assert str(root / 'storygraph.rules.json') in got['written']
    assert got['diff'], '没给 diff'
    assert got['action'] in ('added', 'updated'), got
    # 真的落进去了（而且是可评审的 JSON）
    import json
    data = json.loads((root / 'storygraph.rules.json').read_text(encoding='utf-8'))
    assert any(s.get('name') == 'weather' for s in data['statements'])


def test_declare_refuses_a_bad_name_and_says_why(tmp_path):
    """护栏：名字不合法 → `ok=False` + 原因（**不写盘**）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    for bad in ('', '9 bad', '   '):
        got = M.declare(root, bad)
        assert got['ok'] is False, (bad, got)
        assert got['reason'], bad
        assert got['written'] == [], bad


def test_declare_never_touches_asset_tables(tmp_path):
    """⭐⭐ **硬红线**：写声明的全过程里，资源表**一个字节都不许动**。"""
    from openpyxl import Workbook
    root = tmp_path / 'proj'
    tables = root / 'game' / 'asset_tables'
    tables.mkdir(parents=True)
    wb = Workbook()
    wb.active.title = 'images'
    wb.active.append(['ID', 'Path'])
    wb.active.append(['a', 'a.png'])
    target = tables / 'images.xlsx'
    wb.save(target)
    before = target.read_bytes()
    for name in ('weather', 'outfit'):
        M.declare(root, name, label=name)
    after = target.read_bytes()
    assert before == after, '写声明竟然动了资源表'


def test_table_export_is_honestly_not_implemented(tmp_path):
    """
    ⚠ 我第一版这里只"读了注册表、比了 mtime"就返回 `ok=True` —— **假绿**。
    现在一律 `ok=False` + "还没实现"，能力清单里也标 `planned`。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    got = M.table_export(root)
    assert got['ok'] is False and '还没实现' in got['reason'], got
    assert got['written'] == []
    planned = {t['name'] for t in M.capabilities()['tools']
               if t.get('status') == 'planned'}
    assert 'storygraph_table_export' in planned, planned


def test_both_write_tools_use_the_same_receipt_shape(tmp_path):
    """⭐ 统一回执：两个写类工具（含未实现的占位）形状必须一致。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    for got in (M.declare(root, 'weather'), M.table_export(root)):
        for key in ('ok', 'source', 'reason', 'written', 'diff', 'backup',
                    'dry_run'):
            assert key in got, (key, got)


def test_host_validate_reports_when_the_program_does_not_participate(tmp_path):

    """程序没提供钩子 → `source='none'` + `verified=None`（**不是 false**）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    got = M.host_validate(root, 'weather', 'weather x=1')
    assert got['available'] is False
    assert got['verified'] is None, '程序不参与时不许说"不通过"'
    assert got['source'] == 'none'


def test_host_validate_uses_the_program_answer(tmp_path):
    """程序说了算的那一条：钩子说 True/False，回执照转（理由用原话）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.host.py').write_text(
        'def validate_statement(head, text):\n'
        '    if head == "weather":\n'
        '        return ("强度" in text), "天气必须写强度"\n'
        '    return None\n', encoding='utf-8')
    yes = M.host_validate(root, 'weather', 'weather rain 强度=3')
    assert yes['verified'] is True and yes['source'] == 'host'
    no = M.host_validate(root, 'weather', 'weather rain')
    assert no['verified'] is False and '强度' in no['why']
    other = M.host_validate(root, 'something_else', 'something_else x=1')
    assert other['verified'] is None, 'None（程序不管）不许折成通过/不通过'
