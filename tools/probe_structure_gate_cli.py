"""
证据：结构门禁在**真 CLI** 上是什么样（warn 出来、但照写）。

用 Python 写输入文件（PowerShell 的 `Set-Content` 会带 BOM，把
`label a:` 变成 `\\ufefflabel a:`，解析不到 label —— 第一版就栽在这儿）。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'cli.py'

#: 同一个 label，但内容**对不上**（造表用扁平版 → 补丁配不上 → 退回全量）
#: 这才是踩坑现场：**全量重生成 + 有源可比** = 结构被压平又被看见
SRC_RICH_UNALIGNED = '''label a:
    e "完全不同的开场白"
    menu:
        "选项":
            if 某个条件:
                e "第一句不一样的台词"
                e "第二句"
            e "第三句"
            if 另一个条件:
                e "第四句"
    e "完全不同的收尾"
'''

#: 同一个 label，但选项体只有一条语句 —— 用它造表，再把**丰富的那版**当 `--source`
#: （这就是踩坑现场：表比源"旧"／源被手改过，导出会按**表**的结构压平源）
SRC_FLAT = '''label a:
    e "开始"
    menu:
        "选项":
            e "选了且 flag"
    e "结束"
'''


def run(*args) -> tuple:
    p = subprocess.run([sys.executable, str(CLI), *map(str, args)],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='gate_') as td:
        proj = Path(td)
        (proj / 'game').mkdir()
        flat = proj / 'game' / 'flat.rpy'
        rich = proj / "game" / "rich.rpy"
        flat.write_text(SRC_FLAT, encoding='utf-8')
        rich.write_text(SRC_RICH_UNALIGNED, encoding="utf-8")
        xl = proj / 'ch.xlsx'

        print('① 用"扁平版"造表 :',
              run('table', 'rpy2xlsx', flat, '--out', xl)[0])

        # 全量（不给 --source）：门禁**没有参照**，不判
        rc, out = run('table', 'xlsx2rpy', xl, '--out', proj / 'full.rpy')
        print(f'② 全量无源       : exit={rc}；'
              f'提到结构了吗：{"是" if "结构" in out else "否（没有参照，无从判）"}')

        # 给了源（丰富版）：产物按表的结构出来 → 与源对不上 → 必须报出来
        rc, out = run('table', 'xlsx2rpy', xl, '--source', rich,
                      '--out', proj / 'gated.rpy')
        print(f'③ 给源导出       : exit={rc}')
        for ln in out.strip().splitlines():
            if '结构' in ln or '已生成' in ln or '增量' in ln:
                print('   ', ln[:170])
        print(f'   产物写出来了吗：{(proj / "gated.rpy").exists()}'
              f'（warn 不拦写盘）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
