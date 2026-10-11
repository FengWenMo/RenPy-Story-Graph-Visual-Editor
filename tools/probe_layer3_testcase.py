"""第三层真机验收：**行为级断言**（`testcase` + `assert`）。

引擎的 `assert` **不认裸 Python 表达式** —— `testparser.parse_condition` 只认
（真机读源码确认，2026-10-11）：

    ( 子条件 ) | True | False | eval <表达式> | label <名> | 选择器 | and/or/not

所以断言要写成 `assert eval "标记 == 'ran'"`。

本脚本验证三件事：
  ① `test` 命令在本机**跑得起来**（不是文档 D7 的 headless 143）；
  ② **语句真的执行了**（用一个标记变量 + `eval` 断言证明）；
  ③ 断言**失败**时退出码确实非 0（否则第三层就是"假绿门禁"）。

    python tools/probe_layer3_testcase.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.pipeline.engine_collect import _engine_python, find_sdk  # noqa: E402

sdk = find_sdk(None)
py = _engine_python(Path(sdk))
print('SDK =', sdk)

CASES = {
    # ① 语句真的执行了：跑完后标记为 "ran" → 断言通过
    'pass_case': '''init python:
    renpy.register_statement("weather", parse=lambda l: l.eol())

label start:
    weather
    $ sg_marker = "ran"
    return

testcase sg_check:
    run start
    assert eval "sg_marker == 'ran'"
''',
    # ② 断言失败：标记故意不是 "ran" → 退出码应当非 0
    'fail_case': '''label start:
    $ sg_marker = "other"
    return

testcase sg_check:
    run start
    assert eval "sg_marker == 'ran'"
''',
    # ③ 语句**没被执行**（我们猜它执行了）→ 断言应当失败
    'not_run_case': '''init python:
    renpy.register_statement("weather", parse=lambda l: l.eol())

label start:
    $ sg_marker = "never"
    return

testcase sg_check:
    run start
    assert eval "sg_marker == 'ran'"
''',
}


def run_case(tag: str, body: str, env_headless=True):
    td = Path(tempfile.mkdtemp(prefix=f'l3_{tag}_'))
    game = td / 'game'
    (game / 'images').mkdir(parents=True)
    (game / 't.rpy').write_text(body, encoding='utf-8')
    env = dict(os.environ)
    if env_headless:
        env.update({'SDL_VIDEODRIVER': 'dummy', 'SDL_AUDIODRIVER': 'dummy'})
    cmd = [str(py), str(Path(sdk) / 'renpy.py'), str(td), 'test', 'sg_check']
    print(f'\n=== {tag}（{"无头" if env_headless else "有显示"}）')
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           timeout=180, encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        print('    ⏱ 超时（很可能卡在等显示后端）')
        shutil.rmtree(td, ignore_errors=True)
        return None
    out = (p.stdout or '') + (p.stderr or '')
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-6:]
    print(f'    退出码 = {p.returncode}')
    for ln in tail:
        print('    |', ln[:130])
    shutil.rmtree(td, ignore_errors=True)
    return p.returncode


r1 = run_case('pass_case', CASES['pass_case'], env_headless=True)
r2 = run_case('fail_case', CASES['fail_case'], env_headless=True)
r3 = run_case('not_run_case', CASES['not_run_case'], env_headless=True)

print('\n=== 结论（按退出码说，不猜）')
print(f'    ① 语句执行 + 断言成立 → 退出码 {r1}（期望 0）')
print(f'    ② 断言不成立         → 退出码 {r2}（期望非 0）')
print(f'    ③ 语句没执行         → 退出码 {r3}（期望非 0）')
ok = (r1 == 0 and r2 not in (0, None) and r3 not in (0, None))
print('    ⇒', '✅ 第三层可用：能证明"语句真的执行了"，且失败会红'
      if ok else '❌ 还不成立 —— 见上面的退出码')
