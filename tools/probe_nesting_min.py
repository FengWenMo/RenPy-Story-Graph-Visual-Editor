"""最小复现：`if` 里套一个 `menu` —— 全量路径会怎么排结构。"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'cli.py'

CASES = {
    'if>menu': '''label a:
    e "开始"
    if flag:
        menu:
            "选项":
                e "选了"
        e "if 内收尾"
    e "结束"
''',
    'menu>if': '''label a:
    e "开始"
    menu:
        "选项":
            if flag:
                e "选了且 flag"
            e "选项内收尾"
    e "结束"
''',
    'if>if>menu': '''label a:
    if a1:
        if a2:
            menu:
                "选项":
                    e "深处"
''',
}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='nestmin_') as td:
        for i, (name, src) in enumerate(CASES.items()):
            tag = f'case{i}'
            proj = Path(td) / tag / 'game'
            proj.mkdir(parents=True)
            ch = proj / 'ch.rpy'
            ch.write_text(src, encoding='utf-8')
            xl = Path(td) / tag / 'ch.xlsx'
            out = Path(td) / tag / 'out.rpy'
            subprocess.run([sys.executable, str(CLI), 'table', 'rpy2xlsx',
                            str(ch), '--out', str(xl)], cwd=ROOT,
                           capture_output=True, text=True)
            subprocess.run([sys.executable, str(CLI), 'table', 'xlsx2rpy',
                            str(xl), '--out', str(out)], cwd=ROOT,
                           capture_output=True, text=True)
            print(f'===== {name}：原文 =====')
            for ln in src.splitlines():
                print(f'  {ln}')
            print(f'----- {name}：全量产物 -----')
            if out.exists():
                for i, ln in enumerate(
                        out.read_text(encoding='utf-8').splitlines(), 1):
                    if ln.strip() and not ln.startswith('#'):
                        print(f'{i:>3}| {ln}')
            else:
                print('  （没有产物 —— 被门禁拦下了）')
            print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
