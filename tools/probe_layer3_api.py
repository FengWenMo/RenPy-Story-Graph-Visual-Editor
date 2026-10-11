"""第三层 API 的真机验证：用**产品代码**生成探针 → 真跑 `test` → 读结论。

产品函数（不再手写探针字符串）：
  `behavior_probe_source(...)` / `read_test_status(...)` / `behavior_verdict(...)`

两种情形：
  ① 语句真的执行（证据文件会被写）→ 期望 `verified=True`
  ② 语句**不**执行（证据文件不会被写）→ 期望 `verified=False`

    python tools/probe_layer3_api.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真机项目不在本机，跳过')
    raise SystemExit(0)

from storygraph.pipeline import statement_probe as SP                       # noqa: E402
from storygraph.pipeline.engine_collect import _engine_python, find_sdk      # noqa: E402

sdk = find_sdk(None)
py = _engine_python(Path(sdk))
work_dir = Path(tempfile.mkdtemp(prefix='l3api_'))


def run_behavior(probe_body: str, case: str, evidence: Path, timeout=150):
    """把探针放进轻量副本，跑 `test`，边读边看 Status（它不自退）。"""
    evidence.unlink(missing_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='l3api_run_'))
    work = SP._copy_for_ask(REAL, tmp)
    for extra in SP.TEST_EXTRA_DIRS:                 # ← 产品常量（gui/fonts）
        src = REAL / 'game' / extra
        if src.is_dir():
            shutil.copytree(src, work / 'game' / extra, dirs_exist_ok=True)
    (work / 'game' / 'zz_storygraph_l3api.rpy').write_text(probe_body,
                                                           encoding='utf-8')
    p = subprocess.Popen([str(py), str(Path(sdk) / 'renpy.py'), str(work),
                          'test', case],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding='utf-8', errors='replace')
    buf, started = [], time.time()
    try:
        for line in p.stdout:
            buf.append(line)
            if re.search(r'\[rpytest\]\s*Status:', line):
                break
            if time.time() - started > timeout:
                break
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    shutil.rmtree(tmp, ignore_errors=True)
    out = ''.join(buf)
    # ⚠ 判定要**复核证据**（真机实测：`assert eval` 在条件不成立时也可能报
    #    `Status: PASSED` + `1 passed`，而证据文件根本没被写 → 假绿）
    verdict = SP.behavior_verdict(SP.read_test_status(out),
                                 evidence_path=evidence)
    return verdict, evidence.is_file(), out


# ① 真的执行：label 里写证据文件
ev_ok = work_dir / 'hit_ok.txt'
body_ok = SP.behavior_probe_source(
    'sg_api_ok',
    evidence=f'open(r"{ev_ok}", "w").write("hit")',
    testcase='sg_api_check')
print('=== ① 语句真的执行（期望 verified=True）')
print('    产品生成的探针：')
for ln in body_ok.strip().splitlines():
    print('      |', ln)
v1, wrote1, raw1 = run_behavior(body_ok, 'sg_api_check', ev_ok)
print(f'    ⇒ verified={v1["verified"]} ｜ 证据文件被写={wrote1}')
print(f'       {v1["summary"]}')
for ln in raw1.splitlines():
    if re.search(r'Assertions\s*:', ln):
        print('       |', ln.strip()[:120])

# ② 不执行：证据文件指向一个**永远不会被写**的路径（label 里不写它）
ev_bad = work_dir / 'never.txt'
body_bad = SP.behavior_probe_source(
    'sg_api_ok',
    evidence='sg_api_noop = True',      # 抽不出路径 → 条件恒不成立
    testcase='sg_api_check_bad')
print('\n=== ② 语句没执行（期望 verified=False）')
v2, wrote2, raw2 = run_behavior(body_bad, 'sg_api_check_bad', ev_bad)
print(f'    ⇒ verified={v2["verified"]} ｜ 证据文件被写={wrote2}')
print(f'       {v2["summary"]}')
for ln in raw2.splitlines():
    if re.search(r'Assertions\s*:', ln):
        print('       |', ln.strip()[:120])

print('\n=== 结论')
print('    ① 期望 True  →', '✅' if v1['verified'] is True else '❌')
print('    ② 期望 False →', '✅' if v2['verified'] is False else '❌')
shutil.rmtree(work_dir, ignore_errors=True)
