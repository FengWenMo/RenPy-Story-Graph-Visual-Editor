"""真机验收：**扫描出来的写法 → 引擎作证**（用户 2026-10-11 "做实现！"）。

跑副本，不碰真项目；用轻量副本（只拷语法相关的东西，8GB → 几 MB）。

    python tools/probe_ask_real.py [项目根]
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真机项目不在本机，跳过')
    raise SystemExit(0)

from storygraph.pipeline import statement_probe as SP                    # noqa: E402
from storygraph.story import statement_registry as SR                    # noqa: E402

# ① 扫描：这个项目里有哪些"工具还不认识的写法"
SR.clear_cache()
found = SR.discover_undeclared(REAL)
print('=== ① 扫描（`discover_undeclared`，已过 V2-1 闸门）')
if not found:
    print('    没有发现未声明的写法 —— 这个项目很干净')
print('    ', [(r['head'], r['count']) for r in found])

# 为了演示，往副本里注入一个"项目真会用、但工具不认识"的写法。
# ⚠ 用 `python` 块包起来：引擎**能解析**（不制造 parse error），但工具照样
#   能扫出"不认识的写法"。否则注入的行本身会让引擎提前停下、探针根本走不到 ——
#   这正是我第一版演示假绿的原因（真机实测踩到）。
work = Path(tempfile.mkdtemp(prefix='askreal_')) / 'sr_general'
shutil.copytree(REAL, work,
                ignore=shutil.ignore_patterns('.git', 'saves', 'build',
                                              '*.rpyc', 'log.txt', '*.backup',
                                              'cache'))
demo = work / 'game' / 'zz_demo_custom_actions.rpy'
demo.write_text('''init python:
    SG_DEMO_LINES = """
    weather rain 强度=3
    weather snow 强度=1
    outfit lyj "泳装" mood=happy
    outfit lyj "校服" mood=calm
    """

label zz_demo_actions:
    return
''', encoding='utf-8')
SR.clear_cache()
found = SR.discover_undeclared(work)
print(f'\n=== ② 注入四个自定义写法之后（{demo.name}）')
for r in found:
    print(f"    {r['head']:12s} x{r['count']:<3d} 来自 {r['files']}")

# ③ 问引擎：认不认
heads = [r['head'] for r in found]
print(f'\n=== ③ 把 {len(heads)} 个写法丢给引擎（每个头一次 lint）')
res = SP.ask_many(work, heads, timeout=900)
print(f"    ok={res['ok']} 引擎退出码={res['exit']} "
      f"问了={res['asked']} 跳过={res['skipped']}")
if not res['ok']:
    print(f"    问不到：{res['reason']}")
    if res.get('blocked_by'):
        print(f"    被谁挡住：{res['blocked_by']}")
for head in res['asked']:
    v = res['results'].get(head) or {}
    print(f"    {head:12s} 引擎认不认识 = {v.get('recognized')}")
    if v.get('summary'):
        print(f"                 {v['summary']}")
    for e in (v.get('evidence') or [])[:1]:
        print(f"                 引擎原话：{e[:110]}")

# ④ 对照组：一个一定不认识的写法
print('\n=== ④ 对照组（一定不认识的头）')
res2 = SP.ask_many(work, ['sg_definitely_unknown_xyz'], timeout=900)
v2 = (res2.get('results') or {}).get('sg_definitely_unknown_xyz') or {}
print(f"    ok={res2['ok']} recognized = {v2.get('recognized')}（应为 False）")
if not res2['ok']:
    print(f"    问不到：{res2['reason']}")

print('\n=== ⑤ 清理')
shutil.rmtree(work.parent, ignore_errors=True)
print('    副本已删；真项目未被触碰')
