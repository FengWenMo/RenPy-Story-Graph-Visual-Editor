"""实验（第二轮）：lint 对探针文件里"未知语句头"到底怎么报 —— 用来定判据。

    python tools/probe_statement_ask2.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真机项目不在本机，跳过')
    raise SystemExit(0)

import os                                                                # noqa: E402

from storygraph.pipeline.engine_collect import find_sdk, run_engine      # noqa: E402

sdk = find_sdk(None)
work = Path(tempfile.mkdtemp(prefix='ask2_')) / 'p'
shutil.copytree(REAL, work,
                ignore=shutil.ignore_patterns('.git', 'saves', 'build',
                                              '*.rpyc', 'log.txt', '*.backup',
                                              'cache'))
for stale in (work / 'errors.txt', work / 'errors.txt.sg-parked'):
    stale.unlink(missing_ok=True)
print('副本：', work)

#: 三种写法分别单独放一个探针文件（**每个文件一个候选**，避免"只报第一处"）
CASES = {
    'bare': 'label _sg_bare:\n    sg_unknown_xyz sg_arg1\n    return\n',
    'block': 'label _sg_block:\n    sg_unknown_xyz sg_arg1:\n        pass\n    return\n',
    'known': 'label _sg_known:\n    pause 1.0\n    return\n',
}

for name, body in CASES.items():
    f = work / 'game' / f'zz_storygraph_ask2_{name}.rpy'
    f.write_text('# storygraph 实验探针（用完即删）\n' + body, encoding='utf-8')
    rc, text = run_engine(sdk, work, ['lint', '--error-code'], timeout=900)
    hits = [ln for ln in text.splitlines() if f'ask2_{name}' in ln]
    print(f'\n=== {name}（退出码 {rc}）')
    print(f'    探针状态：存在={f.exists()}')
    if hits:
        for h in hits[:4]:
            print('    lint 提到：', h.strip()[:130])
        # 找"报错行 + 紧随的源码行"
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if f'ask2_{name}' in ln and i + 1 < len(lines):
                print('    → 紧随源码行：', lines[i + 1].strip()[:90])
    else:
        print('    lint 完全没提到这个文件')
    f.unlink()

# 最后：新探针文件会不会让引擎重新编译？看看有没有 .rpyc
print('\n=== 探针文件删掉后遗留：')
for p in sorted((work / 'game').glob('zz_storygraph_ask2*')):
    print('   ', p.name)
