"""
探针：**原地导出**（导出回原 .rpy）能不能保住"第三类"？

上面那条测试证明：`init python:` 之类不进 Beats（契约 A1 的"第三类"），
**导出到新路径（= 全量重建）必然丢**。

但日常用法很可能是**原地导出**（改完这一章、写回原文件）——
那条路走的是 `patcher` 增量合并（契约 B2），理论上**逐字保留原文里
不归自己的行**，第三类就该保住。

这两条路结论相反，直接决定"能不能交付"，必须实测。

只读真实项目；**复制到临时目录后再原地导出**，绝不碰源文件。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
PY = sys.executable

#: 样本：**同时**含第三类（不进 Beats）与演出（进 Beats）
SAMPLE_TEXT = (
    'default score = 0\n'
    '\n'
    'init python:\n'
    '    def helper():\n'
    '        return 1\n'
    '\n'
    'transform my_tf:\n'
    '    xalign 0.5\n'
    '\n'
    'label a:\n'
    '    e "原文台词"\n'
)
#: 第三类的特征串（不进 Beats，靠"不动它"才保得住）
MARKERS = ('init python:', 'default score', 'transform my_tf')


def main() -> int:
    if not REAL.exists():
        print('真实项目不在')
        return 0
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / 'proj'
        work.mkdir(parents=True)
        target = work / 'chapter.rpy'
        # 自己造样本：**同时**有第三类（不进 Beats）和演出（进 Beats）。
        # 真实项目里很难找到两者都有的单文件，而判据恰恰需要两者同在。
        target.write_text(SAMPLE_TEXT, encoding='utf-8')
        before = target.read_text(encoding='utf-8')

        print(f'样本：{target.name}（{len(before.splitlines())} 行）')
        have = [m for m in MARKERS if m in before]
        print(f'  含第三类特征：{have or "无"}')

        def run(*args):
            p = subprocess.run([PY, '-m', 'storygraph.cli', *args],
                               cwd=str(ROOT), capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            return p.returncode, (p.stdout + p.stderr)

        xl = work / 'd.xlsx'
        c, o = run('table', 'rpy2xlsx', str(target), '--out', str(xl))
        print(f'\n[1] rpy2xlsx  exit={c}')
        if c:
            print(o[:400])
            return 1

        # --force 是**逃生舱**：它越过手写文件护栏，代价就是第三类会被抹掉
        # （CLI --help 里写明了）。先测**默认**（不加 force）—— 那是日常用法，
        # 护栏必须拦住、文件必须逐字不动。
        c, o = run('table', 'xlsx2rpy', str(xl), '--out', str(target))
        print(f'[2] 原地 xlsx2rpy（**默认，无 --force**）  exit={c}')
        for l in o.strip().splitlines()[:3]:
            print(f'      {l[:110]}')
        after_default = target.read_text(encoding='utf-8')
        untouched = after_default == before
        print(f'  {"✓ 没写盘，原文件逐字未动" if untouched else "✗ 默认就把文件改了"}')
        if not untouched:
            print(f'      （前 3 行：{after_default.splitlines()[:3]}）')

        c, o = run('table', 'xlsx2rpy', str(xl), '--out', str(target),
                   '--force')
        print(f'\n[3] 原地 xlsx2rpy --force（逃生舱）  exit={c}')
        for l in o.strip().splitlines()[:2]:
            print(f'      {l[:110]}')
        if c:
            print(o[:600])
            return 1

        after = target.read_text(encoding='utf-8')
        print(f'\n  导出后 {len(after.splitlines())} 行')
        lost = []
        for m in have:
            n_before = sum(1 for l in before.splitlines() if l.strip().startswith(m))
            n_after = sum(1 for l in after.splitlines() if l.strip().startswith(m))
            ok = n_after >= n_before
            print(f'  {"✓" if ok else "✗"} {m!r}: {n_before} → {n_after}')
            if not ok:
                lost.append((m, n_before, n_after))

    print('\n结论：', '原地导出保住了第三类' if not lost
          else f'原地导出丢了：{lost}')
    return 1 if lost else 0


if __name__ == '__main__':
    raise SystemExit(main())
