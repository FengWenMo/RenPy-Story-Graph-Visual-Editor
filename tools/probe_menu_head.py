#!/usr/bin/env python
"""
`menu` 头（名 / 参数）的建模探针 —— 审计 P1-3 收尾（2026-10-19）
================================================================

判据三条，缺一不可：

  1. **结构**：`menu 名(args):` 的选项必须是**真的拍**（`kind='menu_option'`），
     不再整块掉进「原样块」—— 否则卡片上整段菜单消失，策划改不了任何一句；
  2. **保真**：渲染回来的头行与原文**逐字节相同**（四种合法形态各验一遍）；
  3. **引擎**：产物丢给真 SDK `lint` 必须过（`menu` 头是语法敏感区）。

    python tools/_probe_menu_head.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import (  # noqa: E402
    IncrementalRenPyAnalyzer)
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402

SDK = Path(r'D:\software\renpy-8.4.1-sdk')

#: `(样本名, 原文里的头行, 期望的产物头行)`
CASES = [
    ('裸 menu', 'menu:', 'menu:'),
    ('只有参数', 'menu (nvl=True):', 'menu (nvl=True):'),
    ('只有名', 'menu choice_menu:', 'menu choice_menu:'),
    ('名 + 参数', 'menu named_menu (screen="choice"):',
     'menu named_menu (screen="choice"):'),
    # 紧贴写法：引擎收（`parse_arguments` 直接匹配 `(`），我们**规范化**成
    # 一个空格 —— 那是唯一一处不逐字节的写法，在这里显式钉住。
    ('紧贴写法', 'menu named_menu(screen="choice"):',
     'menu named_menu (screen="choice"):'),
]


def _src(head: str) -> str:
    return (
        'label start:\n'
        '    "before"\n'
        f'    {head}\n'
        '        "选项 A":\n'
        '            jump done\n'
        '        "选项 B" if flag:\n'
        '            "chose B"\n'
        '    return\n'
        '\n'
        'label done:\n'
        '    return\n'
    )


def _roundtrip(text: str, tmp: Path, tag: str) -> tuple[list, str]:
    f = tmp / f'{tag}.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    return beats, render_beats_to_rpy(beats)


def _lint(project: Path) -> tuple[int, str]:
    game = project / 'game'
    game.mkdir(parents=True, exist_ok=True)
    (game / 'options.rpy').write_text(
        'define e = Character("E")\n', encoding='utf-8')
    py = SDK / 'lib' / 'py3-windows-x86_64' / 'python.exe'
    rp = SDK / 'renpy.py'
    if not py.exists() or not rp.exists():
        return -1, '（没有 SDK，跳过 lint）'
    out = subprocess.run(
        [str(py), str(rp), str(project), 'lint', '--error-code'],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=180,
    )
    return out.returncode, (out.stdout or '') + (out.stderr or '')


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='sgmenu_'))
    bad = 0
    for i, (name, head, want) in enumerate(CASES):
        print('=' * 72)
        print(name, '→', head)
        print('-' * 72)
        src = _src(head)
        try:
            beats, out = _roundtrip(src, tmp, f'm{i}')
        except Exception as exc:                       # noqa: BLE001
            print(f'  ✗ 解析/渲染出错：{type(exc).__name__}: {exc}')
            bad += 1
            continue

        # 1. 结构：选项是真的拍
        kinds = [b.kind for b in beats]
        opts = [b for b in beats if b.kind == 'menu_option']
        heads = [b for b in beats if b.kind == 'menu']
        ok_struct = (len(heads) == 1 and len(opts) == 2
                     and not any(b.opaque for b in beats))
        print(f'  {"✓" if ok_struct else "✗"} 结构：菜单拍 {len(heads)} 张、'
              f'选项拍 {len(opts)} 张、原样块 '
              f'{sum(1 for b in beats if b.opaque)} 段'
              f'（kinds={kinds}）')
        if not ok_struct:
            bad += 1

        # 2. 保真：头行逐字节
        got = next((ln.strip() for ln in out.split('\n')
                    if ln.strip().startswith('menu')), '(没有 menu 行)')
        ok_head = got == want
        print(f'  {"✓" if ok_head else "✗"} 头行：{got!r}'
              f'（期望 {want!r}）')
        if not ok_head:
            bad += 1

        # 幂等 + 引擎
        try:
            _b2, again = _roundtrip(out, tmp, f'm{i}b')
            ok_idem = again == out
        except Exception as exc:                       # noqa: BLE001
            ok_idem = False
            print(f'  ✗ 产物不能再解析：{type(exc).__name__}: {exc}')
        print(f'  {"✓" if ok_idem else "✗"} 幂等（第二遍不再变）')
        if not ok_idem:
            bad += 1

        proj = tmp / f'proj_m{i}'
        (proj / 'game').mkdir(parents=True, exist_ok=True)
        (proj / 'game' / 'script.rpy').write_text(out, encoding='utf-8')
        rc, log = _lint(proj)
        if rc == -1:
            print(f'  · {log}')
        elif rc == 0:
            print('  ✓ 引擎 lint 通过')
        else:
            print(f'  ✗ 引擎 lint 失败（rc={rc}）')
            for line in log.strip().split('\n'):
                if ('File "' in line or 'rror' in line
                        or line.strip().startswith('^')):
                    print(f'    {line}')
            bad += 1
        print()
    print('=' * 72)
    print(f'失败：{bad}')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
