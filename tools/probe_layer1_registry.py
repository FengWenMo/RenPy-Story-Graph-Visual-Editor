"""第一层真机验证：**直接问引擎"你注册了什么"**（用户 2026-10-11 的四层方案）。

    python tools/probe_layer1_registry.py [项目根]
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

from storygraph.core import engine_facts as EF                           # noqa: E402
from storygraph.pipeline.engine_collect import collect                    # noqa: E402
from storygraph.story import statement_registry as SR                     # noqa: E402

print('项目：', REAL)
print('=== 采集（只取注册表那一项；用轻量副本跑，别碰真项目）')

# 用轻量副本采集：只拷语法相关的文件（真机 8GB 整份拷会把磁盘写满）
from storygraph.pipeline.statement_probe import _copy_for_ask             # noqa: E402
tmp = Path(tempfile.mkdtemp(prefix='l1_'))
work = _copy_for_ask(REAL, tmp)
print('    轻量副本：', sum(1 for _ in work.rglob("*")), '个条目')

res = collect(work, timeout=900)
print('    采集 ok =', res['ok'], '|', res.get('reason') or '')
facts = res.get('facts') or {}
ok, why = EF.is_collectable(facts)
print('    事实可用 =', ok, why)

reg = EF.registered_statements(facts)
print(f'\n=== 第一层：引擎注册表里有多少条 = {len(reg)}')
for name in sorted(reg)[:20]:
    rec = reg[name]
    bits = [f'{k}={v}' for k, v in sorted(rec.items()) if k != 'name']
    print(f'    {name:20s} {bits}')
if len(reg) > 20:
    print(f'    …另有 {len(reg) - 20} 条')

# 对照：本项目扫描器"认出的"未声明写法 vs 引擎注册表
SR.clear_cache()
found = SR.discover_undeclared(work)
print(f'\n=== 扫描（本项目扫描器）发现 {len(found)} 个未声明写法')
print('    ', [(r['head'], r['count']) for r in found])
names = EF.statement_names(facts)
print(f'\n=== 拿注册表对账（第一层就能回答"认不认识"）')
for r in found:
    hit = r['head'] in names
    print(f"    {r['head']:16s} 在引擎注册表里 = {hit}"
          + (f"  形状：{EF.statement_shape(facts, r['head'])}" if hit else ''))

shutil.rmtree(tmp, ignore_errors=True)
print('\n（副本已删，真项目未被触碰）')
