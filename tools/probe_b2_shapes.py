"""
B 阶段二的两条证据（都能独立重跑）：

  ① 真实项目上菜单**确实是按路径切的**（不是悄悄退回老路）；
  ② 老推断会压平的那几个形状，现在**修好了**（走完整 CLI 全量路径）。

只写临时目录，不碰真实项目。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
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
    'menu>deep': '''label a:
    menu:
        "选项":
            e "第一句"
            if c1:
                e "第二句"
            e "第三句"
''',
}


def cut_stats() -> None:
    """① 真实项目：菜单组是按路径切的，还是退回老路的？"""
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story import beat_render as br
    from storygraph.story.beat_build import beats_from_parse_result

    files = sorted(f for f in (REAL / 'game').rglob('*.rpy')
                   if not f.name.endswith('.generated.rpy'))
    by_path = fallback = 0
    for f in files:
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        if not beats:
            continue
        plan = br._nest_plan(beats)
        for i, b in enumerate(beats):
            if b.kind != br.KIND_MENU:
                continue
            if br._take_menu_run_by_path(beats, i, plan) is None:
                fallback += 1
            else:
                by_path += 1
    print(f'① 真实项目里菜单组的切法：**按路径 {by_path} 个** / '
          f'退回老路 {fallback} 个')


def cli_case(name: str, src: str, td: Path) -> None:
    """② 走完整 CLI（rpy2xlsx → xlsx2rpy 全量），打印产物结构。"""
    proj = td / name.replace('>', '_')
    (proj / 'game').mkdir(parents=True)
    ch = proj / 'game' / 'ch.rpy'
    ch.write_text(src, encoding='utf-8')
    xl = proj / 'ch.xlsx'
    out = proj / 'out.rpy'
    for args in (['table', 'rpy2xlsx', str(ch), '--out', str(xl)],
                 ['table', 'xlsx2rpy', str(xl), '--out', str(out)]):
        subprocess.run([sys.executable, str(CLI), *args], cwd=ROOT,
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    print(f'\n--- {name}：全量产物 ---')
    if not out.exists():
        print('  （没有产物 —— 被门禁拦下了）')
        return
    for i, ln in enumerate(out.read_text(encoding='utf-8').splitlines(), 1):
        if ln.strip() and not ln.startswith('#'):
            print(f'{i:>3}| {ln}')


def main() -> int:
    if REAL.exists():
        cut_stats()
    with tempfile.TemporaryDirectory(prefix='b2_') as td:
        for name, src in CASES.items():
            cli_case(name, src, Path(td))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
