# -*- coding: utf-8 -*-
"""
发布包**开箱实测**：拿打出来的那个 exe 跑一遍主链路，不是只看文件在不在。

用法：
    python tools/_probe_release_smoke.py --exe dist/StoryGraph/StoryGraph-CLI.exe

它做的事（全部在 `.pip-tmp/release_smoke/` 里的临时项目上，不碰真项目）：

  1. `selfcheck` —— 退出码 0；
  2. `table rpy2xlsx` —— 摊表成功，且 `.rpy` **一个字节没动**；
  3. `table xlsx2rpy --source` —— 导回之后与原文件**逐字节相同**
     （这一条才是"只改你动过的行"的最终判据）；
  4. `validate` —— 退出码 0；
  5. 反例：传一个不存在的 `.rpy` → **退出码 2**（从前是 0 + 一张空表）。

为什么要有它：打包能把源码里跑得通的事悄悄弄坏（少打一个模块 / 少一个 data 文件），
而"文件都在"不等于"跑得起来"。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 一份"什么都有"的章节（中文 + 舞台 + 分支 + 第三类内容）
CHAPTER = '''# 头注释
define e = Character("Eileen")
default flag = False

init python:
    persistent.x = 1

transform left:
    xalign 0.0

label ch01:
    scene bg room with dissolve
    show eileen happy at left
    e "第一句"
    if flag:
        e "第二句"
    return
'''


def _run(exe: Path, *args, timeout=180):
    p = subprocess.run([str(exe), *map(str, args)], capture_output=True,
                       timeout=timeout)
    return (p.returncode,
            p.stdout.decode('utf-8', 'replace'),
            p.stderr.decode('utf-8', 'replace'))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', default=str(ROOT / 'dist' / 'StoryGraph'
                                         / 'StoryGraph-CLI.exe'))
    ap.add_argument('--keep', action='store_true', help='跑完不删临时项目')
    a = ap.parse_args(argv)
    exe = Path(a.exe)
    if not exe.is_file():
        print(f'找不到 exe：{exe}', file=sys.stderr)
        return 2

    work = ROOT / '.pip-tmp' / 'release_smoke'
    shutil.rmtree(work, ignore_errors=True)
    game = work / 'game'
    game.mkdir(parents=True)
    ch = game / 'ch01.rpy'
    ch.write_bytes(CHAPTER.encode('utf-8'))
    original = ch.read_bytes()
    xlsx = ch.with_suffix('.xlsx')

    ok = True

    def report(name, passed, detail=''):
        nonlocal ok
        ok = ok and passed
        print(f'{"[通过]" if passed else "[失败]"} {name}'
              + (f' —— {detail}' if detail else ''))

    code, out, err = _run(exe, 'selfcheck')
    report('selfcheck 退出码 0', code == 0, f'exit={code}')
    if code != 0:
        print((out + err)[-1200:])

    code, out, err = _run(exe, 'table', 'rpy2xlsx', ch, '--project', work,
                          '--no-engine-lint')
    report('rpy2xlsx 成功', code == 0 and xlsx.is_file(), f'exit={code}')
    report('摊表没动源文件', ch.read_bytes() == original)
    if code != 0:
        print((out + err)[-1200:])

    code, out, err = _run(exe, 'table', 'xlsx2rpy', xlsx, '--source', ch,
                          '--project', work, '--no-engine-lint')
    report('xlsx2rpy 成功', code == 0, f'exit={code}')
    report('导回之后逐字节相同', ch.read_bytes() == original,
           f'{len(ch.read_bytes())} vs {len(original)} 字节')
    if ch.read_bytes() != original:
        print((out + err)[-1500:])

    code, out, err = _run(exe, 'validate', work)
    report('validate 退出码 0', code == 0, f'exit={code}')
    if code != 0:
        print((out + err)[-1200:])

    # Excel 安全层（2026-11-01）：自检命令要在包里真的存在，而且对一张
    # 正常表给 0（否则用户第一次用就撞上一句"打不开"）
    code, out, err = _run(exe, 'excel-check', xlsx)
    report('excel-check 对正常表给 0', code == 0, f'exit={code}')
    if code != 0:
        print((out + err)[-1200:])

    code, out, err = _run(exe, 'table', 'rpy2xlsx', game / 'nope.rpy',
                          '--no-engine-lint')
    report('坏路径退出码 2', code == 2, f'exit={code}')

    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    print('✓ 开箱实测通过' if ok else '✗ 开箱实测没通过')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
