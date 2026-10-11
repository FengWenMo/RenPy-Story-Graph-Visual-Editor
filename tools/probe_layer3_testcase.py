"""第三层可行性实验：`testcase` + `renpy.py test` 在本机到底跑不跑得起来？

文档里 D7 的结论（exit 143 / SIGTERM）是在 **headless(dummy)** 下测的。
本机是 Windows + 有桌面 —— 所以先花一次实验把这件事定死，再决定第三层怎么写。

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
print('SDK =', sdk)

td = Path(tempfile.mkdtemp(prefix='l3test_'))
game = td / 'game'
(game / 'images').mkdir(parents=True)
(game / 'test.rpy').write_text('''init python:
    renpy.register_statement("weather", parse=lambda l: l.eol())


label start:
    weather
    $ sg_marker = "ran"
    return


testcase sg_check:
    # 行为级断言：确认脚本真的跑到了那一句
    run start
    assert sg_marker == "ran"
''', encoding='utf-8')

py = _engine_python(Path(sdk))
print('引擎 python =', py)


def run(args, env_extra=None, timeout=180):
    env = dict(os.environ)
    env.update(env_extra or {})
    cmd = [str(py), str(Path(sdk) / 'renpy.py'), str(td)] + [str(a) for a in args]
    print('\n$', ' '.join(cmd[1:]))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           timeout=timeout, encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        print('    ⏱ 超时（%ds）—— 很可能卡在等显示后端' % timeout)
        return None, ''
    out = (p.stdout or '') + (p.stderr or '')
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-8:]
    print(f'    退出码 = {p.returncode}')
    for ln in tail:
        print('    |', ln[:130])
    return p.returncode, out


print('\n=== ① 无头（dummy）下跑 test')
run(['test', 'sg_check'], {'SDL_VIDEODRIVER': 'dummy',
                           'SDL_AUDIODRIVER': 'dummy'}, timeout=120)

print('\n=== ② 有显示（不设 SDL_*）下跑 test')
run(['test', 'sg_check'], timeout=180)

print('\n=== ③ 对照：lint 在本机能不能跑（已知能）')
run(['lint'], {'SDL_VIDEODRIVER': 'dummy',
               'SDL_AUDIODRIVER': 'dummy'}, timeout=180)

print('\n结论要按上面的退出码写进设计 —— 别猜。')
shutil.rmtree(td, ignore_errors=True)
