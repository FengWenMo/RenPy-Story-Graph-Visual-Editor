"""最简 `testcase` 实验：**无头 vs 有显示**，各是什么结果（一次问清）。

    python tools/probe_layer3_min.py

## 本实验的结论（2026-10-11，照退出码写，不猜）

| 跑什么 | 结果 |
|---|---|
| 裸最小工程 + `testcase` + `test`（**无头**） | ⏱ **卡住**（120s 超时） |
| 同上（**有显示**） | ⏱ **卡住** |
| 同一个工程 + `lint`（对照） | ✅ 退出码 0 |

⇒ **`test` 在一个"裸最小工程"上跑不起来**（与显示后端无关 —— 有显示也卡），
而 `lint` 在同一个工程上正常。原因指向"test 需要一个**完整工程骨架**"
（主菜单/`start` 入口能被 test 机器人点动）；我这一轮没能把那个骨架拼到最小，
所以**第三层目前停在"机制已确认、工程前提未解决"**，如实记着。

⚠ 另外两条实测（对第三层很重要）：

  ① 夹具**必须能往下走**：最简版（`label start` 直接 `return`）会停在主菜单，
     test 机器人一直点 → 报 **Possible infinite loop**；
  ② 夹具里的语句**必须引擎本来就认识** —— 用 CDS 注册的自定义语句会撞上
     引擎级根因（`init python` 属于后面的解析组，解析期不可用）→ `expected statement.`。

也就是说：第三层**能证"某句执行了"这件事是真的**（断言语法已确认：
`assert eval "表达式"` / `assert label 名` / `assert (子条件)`），
但要跑它需要一个能正常启动的工程，而不是裸最小工程。
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

#: ⚠ 夹具里的语句**必须引擎本来就认识**！用自己的 `weather`（CDS 注册的）会撞上
#: 同一个引擎级根因：`init python` 属于后面的解析组，解析期它还不可用 →
#: `expected statement.`（这一轮实测确认过三次）。
#: 第三层要证的是"**那句真的执行了**"，机制与语句是不是自定义的无关。
BODY = '''label start:
    $ sg_log = []
    pause 0.01
    $ sg_log.append("executed")
    return


testcase sg_check:
    run start
    assert eval "'executed' in sg_log"
'''


def trial(label, env_extra, args=('test', 'sg_check'), timeout=120):
    td = Path(tempfile.mkdtemp(prefix='l3min_'))
    game = td / 'game'
    (game / 'images').mkdir(parents=True)
    (game / 't.rpy').write_text(BODY, encoding='utf-8')
    env = dict(os.environ)
    env.update(env_extra)
    cmd = [str(py), str(Path(sdk) / 'renpy.py'), str(td)] + list(args)
    print(f'=== {label}：{" ".join(args)}')
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           timeout=timeout, encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        print(f'    ⏱ 超时（{timeout}s）—— 卡住了')
        shutil.rmtree(td, ignore_errors=True)
        return None
    out = (p.stdout or '') + (p.stderr or '')
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-8:]
    print(f'    退出码 = {p.returncode}')
    for ln in tail:
        print('    |', ln[:130])
    shutil.rmtree(td, ignore_errors=True)
    return p.returncode


HEADLESS = {'SDL_VIDEODRIVER': 'dummy', 'SDL_AUDIODRIVER': 'dummy'}

a = trial('① 无头跑 test', HEADLESS)
b = trial('② 有显示跑 test', {})
c = trial('③ 无头跑 lint（对照，已知能跑）', HEADLESS, args=('lint',))

print('\n=== 结论（照退出码写，不猜）')
print(f'    无头 test = {a} ｜ 有显示 test = {b} ｜ 无头 lint = {c}')
