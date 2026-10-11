"""第三层：`assert label` 到底怎么才成立（真机三选一）。

已知（实测）：`assert label X timeout T` **有牙**（等不到就
`RenpyTestAssertionError`、断言计数 1 failed）。但 `run Jump(X)` 到 X 秒回，
断言轮询时已经不在 X 了 → 判 FAILED。

试三种让"X 那一刻"能被观察到的写法：

  ① X 里**暂停**（`$ renpy.pause(...)`）—— 停在 X 上，等断言看到
  ② X 里**循环**（`while True` + pause）—— 同上，更"硬"
  ③ 用 `assert eval` 轮询**外部证据**（文件）—— 不依赖"停在 X"

    python tools/probe_layer3_label.py
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
EVID = Path(tempfile.mkdtemp(prefix='l3lab_')) / 'hit.txt'
EV = str(EVID).replace('\\', '\\\\')

CASES = {
    '① label 里暂停': ('''label sg_lab:
    $ open(r"{ev}", "w").write("hit")
    $ renpy.pause(20.0)
    return


testcase sg_lab_check:
    assert label sg_lab timeout 15
    run Jump("sg_lab")
''', 'sg_lab_check'),
    '② label 里死循环': ('''label sg_lab:
    $ open(r"{ev}", "w").write("hit")
    while True:
        $ renpy.pause(1.0)
    return


testcase sg_lab_check:
    assert label sg_lab timeout 15
    run Jump("sg_lab")
''', 'sg_lab_check'),
    '③ assert eval 轮询外部证据': ('''init python:
    import os

label sg_lab:
    $ open(r"{ev}", "w").write("hit")
    return


testcase sg_lab_check:
    assert eval "os.path.exists(r'{ev}')" timeout 15
    run Jump("sg_lab")
''', 'sg_lab_check'),
}


def run_case(body, case, timeout=120):
    tmp = Path(tempfile.mkdtemp(prefix='l3lab_run_'))
    work = SP._copy_for_ask(REAL, tmp)
    for extra in SP.TEST_EXTRA_DIRS:
        src = REAL / 'game' / extra
        if src.is_dir():
            shutil.copytree(src, work / 'game' / extra, dirs_exist_ok=True)
    (work / 'game' / 'zz_l3lab.rpy').write_text(body.format(ev=EV),
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
    return SP.read_test_status(''.join(buf))


for tag, (body, case) in CASES.items():
    EVID.unlink(missing_ok=True)
    st = run_case(body, case)
    print(f'\n=== {tag}')
    print(f"    Status = {st['status'] or '⚠ 没读到'}"
          f" ｜ 证据文件 = {EVID.is_file()}"
          f" ｜ 断言 {st['assertions']} passed / {st['failed']} failed")
    if st['reason']:
        print(f'    {st["reason"][:120]}')

shutil.rmtree(EVID.parent, ignore_errors=True)
print('\n判读：')
print('  · 证据文件被写 = 那句话真的执行了（与断言无关的铁证）')
print('  · Status=PASSED **且** 证据文件在 = 第三层可用于"证明执行"')
