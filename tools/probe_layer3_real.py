"""第三层：在**真项目副本**上跑行为级断言（`testcase` + `test`）。

假设（要被验证或推翻）：`test` 在裸最小工程上卡住，是因为那里**没有骨架**
（主菜单点不动）；在真项目副本上应当能跑。

做法：轻量副本（只拷 `.rpy` 等语法文件，不拷图像音频）+
注入一个探针文件（含 `testcase`），跑
`renpy.py <副本> test sg_probe_check`，按退出码与输出定性。

    python tools/probe_layer3_real.py [项目根]
"""

import os
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
tmp = Path(tempfile.mkdtemp(prefix='l3real_'))
work = _copy_for_ask(REAL, tmp)
# ⚠ `test` 与 `lint` **需要的不是同一批文件**（真机实测 2026-10-11，逐步逼出来的）：
#   · `lint` 只要 `.rpy` 文本（语法）—— 轻量副本够；
#   · `test` 要**真的把游戏跑起来** → 需要界面资源与字体：
#     第一次撞 `FileNotFoundError: gui/window_icon.png`（补 `gui/`），
#     第二次撞 `Could not find font 'fonts/普力士Plix/Plix-Regular.otf'`（补 `fonts/`）。
#   ⚠ 图像/音频仍不拷：缺图只影响画面，而它们占了 8GB（磁盘红线）。
for extra in ('gui', 'fonts'):
    src = REAL / 'game' / extra
    if src.is_dir():
        shutil.copytree(src, work / 'game' / extra, dirs_exist_ok=True)
        print(f'  额外拷了 {extra}/：',
              sum(1 for _ in (work / 'game' / extra).rglob('*')), '个条目')
print('轻量副本：', work)
print('  .rpy 数：', len(list((work / 'game').rglob('*.rpy'))))

# 探针：一个 testcase，断言"**跑到了某个 label**"。
# ⚠ 断言形态（引擎只认这几种，读 `testparser.parse_condition` 确认）：
#    `( 子条件 )` / `True` / `False` / `eval <表达式>` / **`label <名>`** / 选择器。
#    `assert label X` 的语义是「**等到 X 被到达**」—— 这才是"证明脚本执行了"
#    的正解，而且**必须带 timeout**，否则条件永不成立时它会一直等（实测超时）。
PROBE = '''# storygraph 行为探针（工具生成，用完即删；请勿提交）

label sg_probe_target:
    $ sg_probe_hit = True
    return


testcase sg_probe_check:
    run sg_probe_target
    assert label sg_probe_target timeout 30
'''
target = work / 'game' / 'zz_storygraph_l3.rpy'
target.write_text(PROBE, encoding='utf-8')


def run(args, env_extra=None, timeout=240):
    env = dict(os.environ)
    env.update(env_extra or {})
    cmd = [str(py), str(Path(sdk) / 'renpy.py'), str(work)] + list(args)
    print(f'\n$ {" ".join(args)}'
          + (f'   （{"无头" if env_extra else "有显示"}）' if True else ''))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           timeout=timeout, encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        print(f'    ⏱ 超时（{timeout}s）')
        return None, ''
    out = (p.stdout or '') + (p.stderr or '')
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-10:]
    print(f'    退出码 = {p.returncode}')
    for ln in tail:
        print('    |', ln[:135])
    return p.returncode, out


print('\n=== ① 先 lint（确认副本 + 探针能编译；不然 test 的结论不可信）')
rc_lint, _ = run(['lint'], timeout=300)

print('\n=== ② 有显示跑 test（真项目骨架）')
rc_gui, _ = run(['test', 'sg_probe_check'], timeout=300)

print('\n=== ③ 无头跑 test（对照）')
rc_head, _ = run(['test', 'sg_probe_check'],
                 {'SDL_VIDEODRIVER': 'dummy', 'SDL_AUDIODRIVER': 'dummy'},
                 timeout=240)

print('\n=== 结论（照退出码写，不猜）')
print(f'    lint = {rc_lint} ｜ 有显示 test = {rc_gui} ｜ 无头 test = {rc_head}')
print('    ⇒ ' + ('✅ 行为级在真项目副本上跑通了' if rc_gui == 0
                  else '见上面的输出：还没跑通'))

shutil.rmtree(tmp, ignore_errors=True)
