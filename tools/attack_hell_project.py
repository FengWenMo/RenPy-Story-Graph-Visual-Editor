"""用**地狱项目**攻击本工具：逐条记录"期望 vs 实际"。

用户 2026-10-11："造一个地狱项目！结合真实项目去攻击此工具"

用法（先用 `tools/make_hell_project.py` 造出来）：

    python tools/attack_hell_project.py

## 判读方式

每一条攻击都写明：**攻击什么 → 期望工具怎么反应 → 实际怎么反应**。
· ✅ = 反应符合期望（工具扛住了）
· ❌ = **工具没扛住**（这就是我们要找的东西）
· ⚠ = 工具反应了，但说法可能误导人（半扛住 —— 也要修）

⚠ 这个脚本**不是判据**（判据在 `tests/`）：它是"发现新缺陷"的探照灯。
一旦某条攻击被修好，就应该给它补一条判据，并在下面标注。
"""

from __future__ import annotations

import json
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HELL = ROOT / '.pip-tmp' / 'hell'

if not (HELL / 'game').is_dir():
    print('地狱项目还没造：先跑 python tools/make_hell_project.py')
    raise SystemExit(0)

RESULTS = []


def attack(name: str, expect: str):
    """装饰器：跑一条攻击，把"期望/实际"记下来。"""
    def wrap(fn):
        try:
            detail, ok = fn()
        except Exception as e:                      # noqa: BLE001 - 崩了就是没扛住
            detail = f'**抛异常**：{type(e).__name__}: {e}'
            detail += '\n' + ''.join(traceback.format_exc().splitlines(True)[-4:])
            ok = False
        RESULTS.append((name, expect, detail, ok))
        mark = {True: '✅', False: '❌', None: '⚠'}[ok]
        print(f'\n{mark} 【{name}】')
        print(f'   期望：{expect}')
        for ln in str(detail).splitlines()[:14]:
            print(f'   实际：{ln}')
        return fn
    return wrap


# ==================== 攻击 1：扫描"工具不认识的写法" ====================

@attack('扫描：自定义写法要报出来，内置关键字一个都不许报',
        '报出 weather / swim_suit 这类；'
        '**不许**报 with / window / at / pause / play music')
def a1():
    from storygraph.story.project_conventions import unknown_statements
    from storygraph.story.statement_registry import clear_cache
    clear_cache()
    got = unknown_statements(HELL)
    heads = sorted(got)
    lines = [f'报出 {len(heads)} 种：{heads}']
    # 断言式检查（写在攻击里而不是判据里：先看现状）
    bad = [h for h in heads if h in ('with', 'window', 'at', 'pause', 'scene',
                                     'show', 'hide', 'play', 'stop', 'nvl',
                                     'return', 'jump', 'call', 'menu', 'if',
                                     'else', 'elif', 'while', 'for', 'pass')]
    if bad:
        lines.append(f'❌ **内置关键字被报成不认识了**：{bad}')
        return '\n'.join(lines), False
    if 'weather' not in heads:
        lines.append('❌ 自定义写法 weather 没报出来（漏报比误报更危险）')
        return '\n'.join(lines), False
    lines.append('✅ 自定义写法报了、内置关键字没误报')
    # 额外信息：有没有把 ATL 块冒号当属性
    if 'heihei:' in heads or 'heihei' in heads:
        lines.append('❌ 又把 ATL 块冒号 `heihei:` 当属性报了（31 假阳性回归）')
        return '\n'.join(lines), False
    lines.append('✅ ATL 块冒号没被误报')
    return '\n'.join(lines), True


# ==================== 攻击 2：引擎事实采集（lint + 探针）====================

@attack('采集：13 个地狱文件里必然有 parse error —— 工具要**说清是哪儿**，'
        '不许假绿',
        'ok=False + 指名道姓说哪个文件哪一行；事实标记为不可用')
def a2():
    from storygraph.pipeline.engine_collect import collect
    res = collect(HELL, timeout=600, mode='copy')
    lines = [f'ok={res["ok"]}', f'reason={res.get("reason") or "（空）"}']
    facts = res.get('facts') or {}
    coll = (facts.get('collection') or {})
    lines.append(f'collection={coll}')
    if res['ok'] and not coll.get('parse_clean'):
        lines.append('❌ ok=True 但 parse_clean=False —— **这是假绿**')
        return '\n'.join(lines), False
    if not res['ok']:
        if not res.get('reason'):
            lines.append('❌ 说了不可用，但没说为什么')
            return '\n'.join(lines), False
        if 'hell_' not in json.dumps(res, ensure_ascii=False):
            lines.append('⚠ 理由里没指出**具体是哪个地狱文件**'
                         '（人不知道去修哪儿）')
            return '\n'.join(lines), None
        lines.append('✅ 如实说不可用，并指出了具体文件')
        return '\n'.join(lines), True
    return '\n'.join(lines), True


# ==================== 攻击 3：lint 语义/语法分类 ====================

@attack('lint：把"语法错"与"语义/资源错"分开归类',
        'parse_errors 里能看到 hell_cds_unknown 的 `expected statement`；'
        '重名 label 单独能认出来')
def a3():
    from storygraph.pipeline.engine_collect import collect
    res = collect(HELL, timeout=600, mode='copy')
    lint = ((res.get('facts') or {}).get('lint') or {})
    out = []
    for bucket in ('parse_errors', 'semantic_errors', 'program_errors'):
        rows = lint.get(bucket) or []
        out.append(f'{bucket}: {len(rows)} 条')
        for r in rows[:2]:
            out.append(f'    {r[:110]}')
    blob = json.dumps(lint, ensure_ascii=False)
    out.append('能认出重名 label：' +
               ('是' if 'defined twice' in blob or 'twice' in blob else '**否**'))
    out.append('能认出未注册写法：' +
               ('是' if 'expected statement' in blob else '**否**'))
    # 采集本身可能因为 parse error 而不 ok，但 lint 桶仍然要对
    ok = bool(lint)
    return '\n'.join(out), (True if ok else False)


# ==================== 攻击 4：CLI 自检与体检报告不许崩 ====================

@attack('CLI/报告：面对地狱项目不许崩、不许静默',
        'selfcheck 与体检报告都能给出结论（哪怕是"不可用"）')
def a4():
    out = []
    p = subprocess.run([sys.executable, str(ROOT / 'storygraph' / 'cli.py'),
                        'selfcheck'], capture_output=True, text=True,
                       timeout=600, encoding='utf-8', errors='replace')
    out.append(f'selfcheck exit={p.returncode}')
    for ln in (p.stdout or '').splitlines()[-3:]:
        out.append('  ' + ln[:110])
    if p.returncode != 0:
        return '\n'.join(out), False
    from storygraph.pipeline.build import load_asset_registry
    from storygraph.app.asset_report import format_asset_health
    from storygraph.app.session_assets import EditorSession
    try:
        reg = load_asset_registry(HELL)
        sess = EditorSession(HELL)
        rep = sess.asset_health_report()
        text = format_asset_health(rep)
        out.append(f'体检报告 {len(text)} 字，前 3 行：')
        for ln in text.splitlines()[:3]:
            out.append('  ' + ln[:110])
    except Exception as e:                          # noqa: BLE001
        out.append(f'❌ 体检报告炸了：{type(e).__name__}: {e}')
        return '\n'.join(out), False
    return '\n'.join(out), True


# ==================== 攻击 5：UI 后端能干活（发现 + 声明）====================

@attack('UI 后端：地狱项目里"发现"与"声明"两条路要能走',
        'discover_undeclared 报出 weather；声明后它变成"认识的"')
def a5():
    from storygraph.story.statement_registry import (StatementRegistry,
                                                     clear_cache,
                                                     discover_undeclared)
    clear_cache()
    found = discover_undeclared(HELL)
    out = [f'发现 {len(found)} 个：{[r["head"] for r in found][:8]}']
    if not found:
        out.append('❌ 一个都没发现（真实项目 0 条 + 地狱文件有 weather，应至少 1）')
        return '\n'.join(out), False
    reg = StatementRegistry.for_project(HELL)
    out.append(f'注册表里 {len(reg.names())} 个名字')
    return '\n'.join(out), True


print('\n' + '=' * 72)
n_ok = sum(1 for r in RESULTS if r[3] is True)
n_bad = sum(1 for r in RESULTS if r[3] is False)
n_warn = sum(1 for r in RESULTS if r[3] is None)
print(f'总账：✅ {n_ok} ｜ ❌ {n_bad} ｜ ⚠ {n_warn}')
print('=' * 72)
if n_bad:
    print('\n❌ **工具没扛住的**（这些就是要修的）：')
    for name, expect, _d, ok in RESULTS:
        if ok is False:
            print(f'  · {name}')
if n_warn:
    print('\n⚠ 半扛住的（说法可能误导人）：')
    for name, expect, _d, ok in RESULTS:
        if ok is None:
            print(f'  · {name}')
