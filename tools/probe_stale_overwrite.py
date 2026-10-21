"""
探针：**原地导出**（`--out` 就是源文件）在"表过期"时会不会吃掉程序员的代码。

场景（最真实的协作节奏）：
  1. 策划把章节导成表；
  2. 程序员在同一个 .rpy 里加了几行（pause / 新台词 / 赋值）；
  3. 策划在表里改一句台词，**导出回原文件**。

判据（按十条铁律 2「要么全对、要么完全不动」）：
  · 要么增量只改那 1 行，程序员的 3 行原样在；
  · 要么**拒绝写**并说明原因（源文件一个字节不动）；
  · 绝不能"退出码 0 + 程序员的 3 行消失"。

用法：
    D:/Python/python.exe tools/probe_stale_overwrite.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

BODY = '''\
label ch1:
    jc "第一句。"
    jc "第二句。"
    jc "第三句。"
    jc "第四句。"
    return
'''

#: 程序员的改动（表里没有这三行）
PROG_EXTRA = ('    pause 0.5\n'
              '    jc "程序员新加的一句。"\n'
              '    $ flag = True\n')


def run(*args):
    p = subprocess.run([PY, str(ROOT / 'cli.py'), *args], cwd=str(ROOT),
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def set_cell(xlsx: Path, old: str, new: str) -> int:
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx))
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.strip() == old:
                    c.value = new
                    wb.save(str(xlsx))
                    return 1
    wb.save(str(xlsx))
    return 0


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='staleov_') as td:
        work = Path(td)
        src = work / 'ch1.rpy'
        src.write_text(BODY, encoding='utf-8')
        xl = work / 'ch1.xlsx'
        rc, _ = run('table', 'rpy2xlsx', str(src), '--out', str(xl))
        print(f'[1] 导表：rc={rc}')

        # 程序员的改动：插在「第二句」后面
        cur = src.read_text(encoding='utf-8')
        src.write_text(cur.replace('    jc "第二句。"\n',
                                   '    jc "第二句。"\n' + PROG_EXTRA),
                       encoding='utf-8')
        before = src.read_bytes()
        print('[2] 程序员在源码里加了 3 行（表里没有）')
        for ln in PROG_EXTRA.rstrip('\n').splitlines():
            print(f'      + {ln}')

        print('[3] 策划改表：「第四句。」→「改过。」')
        print(f'      改到 {set_cell(xl, "第四句。", "改过。")} 个格子')

        rc, log = run('table', 'xlsx2rpy', str(xl), '--out', str(src),
                      '--source', str(src), '--no-engine-lint')
        after = src.read_bytes()
        print(f'\n[4] 原地导出：退出码 = {rc}')
        for ln in [x for x in log.strip().splitlines() if x.strip()]:
            print(f'    {ln}')

        t = after.decode('utf-8', 'replace')
        print('\n[5] 源文件现在是什么样')
        for ln in t.splitlines():
            print(f'    | {ln}')

        print('\n[6] 判定')
        print(f'    源文件被改了      : {before != after}')
        print(f'    pause 0.5 还在    : {"pause 0.5" in t}')
        print(f'    程序员新台词还在  : {"程序员新加的一句。" in t}')
        print(f'    $ flag 还在       : {"$ flag = True" in t}')
        print(f'    「第四句。」还在  : {"第四句。" in t}')
        print(f'    「改过。」进来了  : {"改过。" in t}')
        lost = [x for x in ('pause 0.5', '程序员新加的一句。', '$ flag = True')
                if x in before.decode('utf-8') and x not in t]
        p0 = rc == 0 and lost
        print(f'\n    >>> {"P0：退出码 0 但源码里的行消失：" + str(lost) if p0 else "不算 P0"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
