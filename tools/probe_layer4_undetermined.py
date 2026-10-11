"""第四层真机验收：**体检报告里的「测不出来」那一段**长什么样。

用真项目副本 + 注入一个未知写法，采一次事实，然后：
  ① 打印 `engine_facts.undetermined_statements` 的分类结果；
  ② 打印 `format_undetermined`（CLI 用的那段）；
  ③ 打印**体检报告**里那一段（`format_asset_health`）；
  ④ 打印 `_undetermined_exit` 的退出码（默认 vs `--strict`）。

    python tools/probe_layer4_undetermined.py
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

from storygraph.app.asset_report import format_asset_health              # noqa: E402
from storygraph.cli import _undetermined_exit                            # noqa: E402
from storygraph.core import engine_facts as EF                           # noqa: E402
from storygraph.pipeline import statement_probe as SP                     # noqa: E402
from storygraph.pipeline.engine_collect import collect                    # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix='l4_'))
work = SP._copy_for_ask(REAL, tmp / 'src')
# 注入两个"工具不认识、引擎也不认识"的写法（真机会用到的样子）
(work / 'game' / 'zz_demo_unknown.rpy').write_text(
    'label zz_demo:\n    weather rain 强度=3\n    return\n', encoding='utf-8')

print('=== 采集（扫描会记进 derived.undeclared_heads）')
res = collect(work, timeout=900, mode='copy', ask=True, ask_limit=5)
facts = res.get('facts') or {}
print('    采集 ok =', res['ok'], '|', res.get('reason') or '（干净）')
rows = (facts.get('derived') or {}).get('undeclared_heads') or []
print('    扫描到：', [(r['head'], r['count'],
                       'asked' if 'engine_recognized' in r else 'not-asked')
                      for r in rows])

print('\n=== ① 分类（undetermined_statements）')
summary = EF.undetermined_statements(facts)
for key in ('total_scan',):
    print(f'    {key} = {summary.get(key)}')
for key in ('engine_known', 'engine_unknown', 'undetermined'):
    print(f'    {key} = {[r["head"] for r in summary.get(key) or []]}')

print('\n=== ② CLI 那段（format_undetermined）')
print(EF.format_undetermined(summary) or '（空 —— 没有要说的）')

print('\n=== ③ 体检报告里那一段（format_asset_health）')
report = {'ok': True, 'problems': 0,
          'counts': {'images': 1, 'music': 0, 'sounds': 0, 'voices': 0},
          'missing': {}, 'unregistered': {}, 'outside_game': {},
          'duplicate_ids': [], 'duplicate_paths': [], 'no_path': [],
          'placeholders': [], 'total_rows': 1,
          'engine_undetermined': summary}
text = format_asset_health(report)
start = text.find('【扫描 vs 引擎')
print(text[start:start + 700] if start >= 0 else '（报告里没有这一段！）')

print('\n=== ④ 退出码（CI 门禁）')
code1, _ = _undetermined_exit(facts, strict=False)
code2, _ = _undetermined_exit(facts, strict=True)
print(f'    默认 = {code1}（不偷偷把 CI 变红）｜ --strict = {code2}（可修 → 1）')

shutil.rmtree(tmp, ignore_errors=True)
print('\n（临时目录已删，真项目未被触碰）')
