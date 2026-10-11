"""第三层入口的真机验收：用**产品函数** `verify_behavior` 一次跑通。

两种情形（都在真项目副本上，绝不碰真项目）：

  ① label 里写证据 → 期望 `verified=True`
  ② label 里**不写**证据 → 期望 `verified=False`（引擎可能仍报 PASSED，被复核拦下）

    python tools/probe_layer3_verify.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真机项目不在本机，跳过')
    raise SystemExit(0)

from storygraph.pipeline import statement_probe as SP                       # noqa: E402

wd = Path(tempfile.mkdtemp(prefix='l3verify_'))
ev = wd / 'hit.txt'

# ① 真执行：把探针放进副本，label 里写证据文件
tmp = Path(tempfile.mkdtemp(prefix='l3verify_work_'))
work = SP._copy_for_ask(REAL, tmp)
for extra in SP.TEST_EXTRA_DIRS:
    s = REAL / 'game' / extra
    if s.is_dir():
        import shutil
        shutil.copytree(s, work / 'game' / extra, dirs_exist_ok=True)
# ① 真执行：产品生成的探针**自己带 label**（`behavior_probe_source` 生成），
#    所以这里**不要**再手写一个同名 label —— 我第一版手写了一份，
#    结果 `The label … is defined twice` → 引擎崩、`status=''`
#    （这也顺带证明：**读不到 Status 时产品如实报"测不出来"，没当通过**）。
print('=== ① 语句真的执行（期望 verified=True）')
v1 = SP.verify_behavior(work, 'sg_verify_ok',
                        evidence=f'open(r"{str(ev).replace(chr(92), "/")}", "w").write("hit")',
                        timeout=150)
print(f"    status={v1['status']!r} verified={v1['verified']} "
      f"证据文件={ev.is_file()}")
print(f"    {v1['summary']}")
print('    原始输出尾部：')
for ln in (v1.get('raw_tail') or [])[-14:]:
    print('      |', ln[:130])

# ② 不执行：证据语句抽不出路径 → 产品**不给"通过"**（verified=None）
print('\n=== ② 抽不出证据路径（期望 verified=None："没证明成"）')
ev.unlink(missing_ok=True)
v2 = SP.verify_behavior(work, 'sg_verify_ok', evidence='sg_noop = True',
                        timeout=150)
print(f"    status={v2['status']!r} verified={v2['verified']}")
print(f"    {v2['summary']}")

print('\n=== ③ 把验证结果记进事实 → 报告那一行长什么样')
from storygraph.core import engine_facts as EF                        # noqa: E402
facts = EF.empty_facts()
facts.setdefault('facts', {})
SP.record_behavior(facts, 'sg_verify_ok', v1)
SP.record_behavior(facts, 'sg_verify_meh', v2)
facts['collection'] = {'parse_clean': True, 'probe_complete': True}
print(EF.format_behavior_line(facts))
s = EF.behavior_summary(facts)
print(f"    汇总：通过 {s['verified']} / 失败 {s['failed']} / "
      f"没证明成 {s['unverified']}")

print('\n=== 结论')
print('    ① 期望 True  →', '✅' if v1['verified'] is True else '❌')
print('    ② 期望 None（没证据 ⇒ 不给通过）→',
      '✅' if v2['verified'] is None else '❌')
print('    ③ 报告能报出"验过 2 个"→',
      '✅' if '验过 2 个' in EF.format_behavior_line(facts) else '❌')

import shutil                                                     # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)
shutil.rmtree(wd, ignore_errors=True)
