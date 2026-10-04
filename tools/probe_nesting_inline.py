"""
实测两条"已知边界"：**嵌套 ≥3 层** 与 **行尾注释**。

不问文档、不问记忆，直接造一个输入跑现有 CLI，看产物和 validate 说什么。

    python tools/probe_nesting_inline.py

只写临时目录（`%TEMP%`），不碰真实项目。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'cli.py'

SRC = '''\
# 嵌套探针
label nest:
    e "顶层"                       # 行尾注释：顶层台词
    if outer:
        e "depth1"
        menu:
            "选项 A":              # 行尾注释：菜单选项
                e "选项体"
                if inner:
                    e "depth2"
                    menu:
                        "选项 B":
                            if inner2:
                                e "depth3"
                                menu:
                                    "选项 C":
                                        if inner3:
                                            e "depth4 —— 深度 4"
    e "收尾"
'''


def run(*args) -> tuple:
    p = subprocess.run([sys.executable, str(CLI), *map(str, args)],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='nestprobe_') as td:
        proj = Path(td) / 'proj'
        (proj / 'game').mkdir(parents=True)
        ch = proj / 'game' / 'ch.rpy'
        ch.write_text(SRC, encoding='utf-8')

        xl = proj / 'ch.xlsx'
        full = proj / 'out_full.rpy'
        inc = proj / 'out_inc.rpy'

        print('① rpy2xlsx      :', run('table', 'rpy2xlsx', ch, '--out', xl)[0])
        print('② xlsx2rpy 全量 :',
              run('table', 'xlsx2rpy', xl, '--out', full)[0])
        print('③ xlsx2rpy 增量 :',
              run('table', 'xlsx2rpy', xl, '--source', ch,
                  '--out', inc)[0])

        def show(path: Path, title: str):
            print(f'\n===== {title} =====')
            if not path.exists():
                print('（没有产物）')
                return
            for i, ln in enumerate(path.read_text(encoding='utf-8')
                                   .splitlines(), 1):
                if ln.strip():
                    print(f'{i:>3}| {ln}')

        show(full, '全量产物（不给 --source）')
        show(inc, '增量产物（--source）')

        print('\n===== 行尾注释活没活 =====')
        for title, p in (('源文件', ch), ('全量', full), ('增量', inc)):
            if not p.exists():
                print(f'  {title}: （无）')
                continue
            n = sum(1 for ln in p.read_text(encoding='utf-8').splitlines()
                    if '#' in ln and ln.strip().startswith(('#', 'label'))
                    and '#' not in ln.split('#')[0] or
                    ('#' in ln and not ln.strip().startswith('#')))
            print(f'  {title}: 含行尾注释的行 {n} 行')

        rc, out = run('validate', proj)
        print(f'\n===== validate（exit={rc}）=====')
        print('\n'.join(out.strip().splitlines()[-8:]))
        hit = [w for w in ('嵌套', '深度', '结构') if w in out]
        print(f'  提到"嵌套/深度/结构"了吗：{hit or "**一个字都没说**"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
