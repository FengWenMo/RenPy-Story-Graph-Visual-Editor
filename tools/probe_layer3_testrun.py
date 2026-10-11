"""第三层：**把 `test` 的结论读出来**（它跑得起来，只是不自退）。

真机摸清的事实（2026-10-11，全部实测）：

  ① `test` **不是"卡住"** —— 它跑完测试并打印报告，然后**进程不退出**
     （用 `subprocess.run(timeout=…)` 等它会误判成"卡住"）。
     正确做法：**边读输出边看 `[rpytest] Status:`**，见到就把进程收掉。
  ② 它需要**显示**（`register_command("test", …, uses_display=True)`）——
     无头会真卡住；本机有桌面，所以能用。
  ③ 它需要 `gui/` 与 `fonts/`（否则起界面时就崩：
     `Couldn't find file 'gui/window_icon.png'` / `Could not find font …`）。
  ④ `run` 收的是 **Python 表达式且要可调用**：
     裸名 → `NameError`；字符串 → `TypeError: 'str' object is not callable`；
     **给 Action（`Jump("label")`）才行**。
  ⑤ `assert` 只认 `( 子条件 )` / `True` / `False` / **`eval <表达式>`** /
     `label <名>` / 选择器 —— 裸 Python 表达式会被判 `Invalid condition.`。

⚠ **读不到 `[rpytest] Status:` 时绝不能当成通过**（我第一版就差点这样）：
夹具自身出错时进程会崩、什么都不打印。

    python tools/probe_layer3_testrun.py [项目根]
"""

import os
import re
import shutil
import subprocess
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

from storygraph.pipeline.engine_collect import _engine_python, find_sdk    # noqa: E402
from storygraph.pipeline.statement_probe import _copy_for_ask               # noqa: E402

sdk = find_sdk(None)
py = _engine_python(Path(sdk))

#: ① 断言成立：探针 label 里 append 一次，断言"真的执行过"
PASS_BODY = '''init python:
    sg_log = []


label sg_probe_ran:
    $ sg_log.append("executed")
    return


testcase sg_check_pass:
    run Jump("sg_probe_ran")
    assert eval "len(sg_log) == 1"
'''

#: ② 断言不成立：同样的执行路径，但断言写死 False
FAIL_BODY = '''label sg_probe_ran:
    return


testcase sg_check_fail:
    run Jump("sg_probe_ran")
    assert eval "False"
'''

#: ③ 断言**等一个永不到来的条件**（带 timeout）→ 应当 timeout 失败，
#:    而不是把上游挂死
TIMEOUT_BODY = '''testcase sg_check_timeout:
    assert label sg_never_defined timeout 3
'''


def build_copy():
    tmp = Path(tempfile.mkdtemp(prefix='l3run_'))
    work = _copy_for_ask(REAL, tmp)
    for extra in ('gui', 'fonts'):
        s = REAL / 'game' / extra
        if s.is_dir():
            shutil.copytree(s, work / 'game' / extra, dirs_exist_ok=True)
    return tmp, work


def run_test(work, probe_body, case, timeout=120):
    """跑一次 `test`：**边读边看 `[rpytest] Status:`**（它不会自己退出）。"""
    (work / 'game' / 'zz_storygraph_l3.rpy').write_text(probe_body,
                                                        encoding='utf-8')
    cmd = [str(py), str(Path(sdk) / 'renpy.py'), str(work), 'test', case]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding='utf-8', errors='replace')
    lines, status = [], ''
    import time
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
    return status, lines


for tag, body, case, expect in (
        ('① 断言成立', PASS_BODY, 'sg_check_pass', 'PASSED'),
        ('② 断言不成立', FAIL_BODY, 'sg_check_fail', 'FAILED'),
        ('③ 条件永远不成立（带 timeout）', TIMEOUT_BODY, 'sg_check_timeout',
         'FAILED')):
    tmp, work = build_copy()
    print(f'\n=== {tag}（期望 {expect}）')
    status, lines = run_test(work, body, case, timeout=90)
    print(f'    [rpytest] Status = {status or "⚠ 没读到（**不能当成通过**）"}')
    keep = [ln for ln in lines
            if re.search(r'\[rpytest\]\s*(Status|Test cases|Assertions|Time)|'
                         r'Timed out|Exception|Error', ln)]
    for ln in keep[-6:]:
        print('    |', ln[:135])
    print(f'    ⇒ {"✅ 符合预期" if status == expect else "❌ 与预期不符"}')
    shutil.rmtree(tmp, ignore_errors=True)

print('\n结论：`test` 可用，判据是**它自己的 `[rpytest] Status:`**；'
      '读不到就当"没测出来"（第四层），绝不当通过。')
