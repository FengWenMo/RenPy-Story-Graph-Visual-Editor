"""第二层真机验收：**隔离合成工程**让"问引擎"真的问得出来。

对比三件事：
  ① 原始工程（含未知写法）→ 引擎编译不过 → 问不到（undetermined）；
  ② 隔离工程（排掉含未知写法的文件）→ **能编译** → 注册表查得到、探针跑得起来。

    python tools/probe_layer2_isolated.py [项目根]
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

from storygraph.core import engine_facts as EF                          # noqa: E402
from storygraph.pipeline import statement_probe as SP                    # noqa: E402
from storygraph.pipeline.engine_collect import collect                   # noqa: E402
from storygraph.pipeline.isolated_project import (                       # noqa: E402
    build_isolated, files_with_unknown_heads)
from storygraph.story import statement_registry as SR                    # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix='l2_'))

# ── 造素材：往副本里加一个"项目真会用、但工具不认识"的写法 ──────────────
work = SP._copy_for_ask(REAL, tmp / 'src')
demo = work / 'game' / 'zz_demo_unknown.rpy'
demo.write_text('label zz_demo:\n    weather rain 强度=3\n    return\n',
                encoding='utf-8')
SR.clear_cache()
found = SR.discover_undeclared(work)
heads = [r['head'] for r in found]
print('=== 素材：注入未知写法之后，扫描发现 =', [(r['head'], r['count']) for r in found])

# ── ① 原始工程：问引擎（预期：被挡住，如实报 undetermined）────────────
print('\n=== ① 原始工程上问引擎（含未知写法 → 引擎编译不过）')
ans = SP.ask_many(work, heads, timeout=900)
print(f"    ok={ans['ok']}")
print(f"    问不到的理由：{ans['reason']}")
if ans.get('blocked_by'):
    print(f"    被谁挡住：{ans['blocked_by']}")
print(f"    结论：{ {h: (ans['results'].get(h) or {}).get('recognized') for h in ans['asked']} }")

# ── ② 隔离合成工程：排掉那个文件，再问 ────────────────────────────────
print('\n=== ② 拼隔离合成工程（排掉含未知写法的文件）')
bad = files_with_unknown_heads(work, heads)
print('    含未知写法的文件：', bad)
iso = build_isolated(work, exclude=bad)
print(f"    ok={iso['ok']} 拷了 {iso['files']} 个文件，排掉 {iso['excluded']}")
print(f"    隔离工程：{iso['root']}")

res = collect(iso['root'], timeout=900, mode='copy')
facts = res.get('facts') or {}
print(f"\n=== ③ 隔离工程能不能编译 + 第一层（注册表）")
print(f"    采集 ok={res['ok']} | {res.get('reason') or '（干净）'}")
print(f"    事实可用 = {EF.is_collectable(facts)}")
reg = EF.registered_statements(facts)
print(f"    引擎注册表 {len(reg)} 条：{sorted(reg)[:10]}"
      + (' …' if len(reg) > 10 else ''))
for head in heads:
    print(f"    {head:12s} 在注册表里 = {head in EF.statement_names(facts)}")

# ── ④ 隔离工程上再问那几个头（这回探针能被解析到）────────────────────
print('\n=== ④ 在隔离工程上问那几个头（引擎能编译 → 探针才是有效信号）')
ans2 = SP.ask_many(iso['root'], heads, timeout=900)
print(f"    ok={ans2['ok']} | {ans2.get('reason') or ''}")
for head in ans2['asked']:
    v = ans2['results'].get(head) or {}
    print(f"    {head:12s} recognized={v.get('recognized')} "
          f"blocks={v.get('blocks')} params={v.get('params')}")
    for e in (v.get('evidence') or [])[:1]:
        print(f"                 引擎原话：{e[:110]}")

# 对照组：一定不认识的写法
ctrl = SP.ask_many(iso['root'], ['sg_definitely_unknown_xyz'], timeout=900)
cv = (ctrl.get('results') or {}).get('sg_definitely_unknown_xyz') or {}
print(f"\n=== ⑤ 对照组（隔离工程上问一个一定不认识的头）")
print(f"    ok={ctrl['ok']} recognized={cv.get('recognized')}（应为 False）")

shutil.rmtree(tmp, ignore_errors=True)
print('\n（临时目录已删，真项目未被触碰）')
