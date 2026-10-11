"""第三层：**为什么 `assert eval "False"` 也会 PASSED**？—— 用铁证查清楚。

做法：让探针那句"真的执行"时**往磁盘写一个词**（不可能被误判的证据），
断言就断言那个词。三种情形各跑一次：

  ① 语句执行 + 断言它执行了          → 期望 PASSED
  ② 语句**不**执行 + 断言它执行了      → 期望 **FAILED**（这才证明断言有牙）
  ③ 语句执行 + 断言一个假命题         → 期望 **FAILED**

    python tools/probe_layer3_evidence.py
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
sdk_dir = None
from storygraph.pipeline.engine_collect import _engine_python, find_sdk    # noqa: E402
from storygraph.pipeline.statement_probe import _copy_for_ask               # noqa: E402

sdk = find_sdk(None)
py = _engine_python(Path(sdk))

#: 证据文件放在**项目之外**（临时目录），由探针在语句执行时写它
EVIDENCE = Path(tempfile.mkdtemp(prefix='l3ev_')) / 'evidence.txt'

CASES = {
    '① 执行+断言成立': '''init python:
    import os

label sg_probe_ran:
    $ open(r"{ev}", "w").write("executed")
    return


testcase sg_check_a:
    run Jump("sg_probe_ran")
    assert eval "os.path.exists(r'{ev}')"
''',
    '② 不执行+断言要求执行': '''init python:
    import os


label sg_probe_ran:
    return


testcase sg_check_b:
    run Jump("sg_probe_ran")
    assert eval "os.path.exists(r'{ev}')"
''',
    '③ 执行+断言假命题（带 timeout）': '''init python:
    import os

label sg_probe_ran:
    $ open(r"{ev}", "w").write("executed")
    return


testcase sg_check_c:
    run Jump("sg_probe_ran")
    assert eval "False" timeout 3
''',
    '④ 不执行+断言要求执行（带 timeout）': '''init python:
    import os


label sg_probe_ran:
    return


testcase sg_check_d:
    run Jump("sg_probe_ran")
    assert eval "os.path.exists(r'{ev}')" timeout 3
''',
}


def run_case(case_name, body, timeout=90):
    tmp = Path(tempfile.mkdtemp(prefix='l3ev_run_'))
    work = _copy_for_ask(REAL, tmp)
    for extra in ('gui', 'fonts'):
        s = REAL / 'game' / extra
        if s.is_dir():
            shutil.copytree(s, work / 'game' / extra, dirs_exist_ok=True)
    (work / 'game' / 'zz_storygraph_l3.rpy').write_text(
        body.format(ev=str(EVIDENCE).replace('\\', '\\\\')), encoding='utf-8')
    case = {'① 执行+断言成立': 'sg_check_a',
            '② 不执行+断言要求执行': 'sg_check_b',
            '③ 执行+断言假命题（带 timeout）': 'sg_check_c',
            '④ 不执行+断言要求执行（带 timeout）': 'sg_check_d'}[case_name]
    p = subprocess.Popen([str(py), str(Path(sdk) / 'renpy.py'), str(work),
                          'test', case],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding='utf-8', errors='replace')
    lines, status = [], ''
    started = time.time()
    try:
        for line in p.stdout:
            lines.append(line.rstrip('\n'))
            m = re.search(r'\[rpytest\]\s*Status:\s*(\w+)', line)
            if m:
                status = m.group(1)
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
    return status, lines


for tag, body in CASES.items():
    EVIDENCE.unlink(missing_ok=True)
    print(f'\n=== {tag}')
    status, lines = run_case(tag, body)
    wrote = EVIDENCE.is_file()
    print(f'    [rpytest] Status = {status or "⚠ 没读到"}'
          f' ｜ 证据文件被写了 = {wrote}')
    for ln in lines:
        if re.search(r'Assertions\s*:|renpy\.test\.types|Timed out', ln):
            print('    |', ln[:130])
            break

print('\n判别法：')
print('  · "证据文件被写了" = **语句真的执行了**（这是铁证，与断言无关）')
print('  · Status 与 (断言是否成立) 必须一致 —— 不一致就是假绿')
shutil.rmtree(EVIDENCE.parent, ignore_errors=True)
