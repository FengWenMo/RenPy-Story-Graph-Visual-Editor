#!/usr/bin/env python
"""
体检报告（docs/评估_代码完成度与真实项目体检_2026-10-18.md）§3 六条 P0 的复现探针
=====================================================================================

只走**产品那条路**：`.rpy` → 解析 → 拍 → 渲染 → `.rpy`。
每条都打印 `原文 / 产物 / 判据`，能用引擎 lint 的再丢给 SDK。

    python tools/_probe_audit_p0.py
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

CASES = {}

CASES['P0-1 BOM'] = (
    '\ufeff# This file demonstrates...\n'
    'label start:\n'
    '    e "hi"\n'
    '    return\n'
)

CASES['P0-2 menu caption'] = (
    'label start:\n'
    '    menu:\n'
    '        "As soon as she catches my eye, I decide..."\n'
    '        "To ask her right away.":\n'
    '            jump rightaway\n'
    '    return\n'
    '\n'
    'label rightaway:\n'
    '    return\n'
)

CASES['P0-3 块内顶格注释'] = (
    'label start:\n'
    '    python:\n'
    '        import random\n'
    '# 顶格注释\n'
    '        def f():\n'
    '            return 1\n'
    '    e "after"\n'
)

CASES['P0-4 call 参数'] = (
    'label start:\n'
    '    call foo(1, two=True) from _call_foo\n'
    '    return\n'
    '\n'
    'label foo(a, two=False):\n'
    '    e "x"\n'
    '    return\n'
)

CASES['P0-5 分号表达式'] = (
    'label start:\n'
    '    show expression holo("a;b")\n'
    '    return\n'
)

CASES['P0-6 u 前缀'] = (
    'label start:\n'
    '    u"统一"\n'
    '    return\n'
)

#: 这些样本**原文本身**就过不了引擎 lint（不是工具的问题）。
#:
#: P0-6 的原文 `u"统一"` 就是这种：Ren'Py 的 say 只接受 who? + 裸字符串，
#: 前缀式是 Python 语法、不是 Ren'Py 语法（SDK lint 原文：
#: `expected statement.`）。审计报告说"Ren'Py 支持"是**错的**。
#: 工具的义务只有一个：**不要把它改得更坏**（从前会变成 `u "统一"`，
#: 即凭空多出一个叫 u 的说话人）。判据因此改成"那一行逐字不变"。
GIGO = {'P0-6 u 前缀': 'u"统一"'}


def _roundtrip(text: str, tmp: Path, tag: str) -> str:
    f = tmp / f'{tag}.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    return render_beats_to_rpy(beats)


def _lint(project: Path) -> tuple[int, str]:
    """把产物丢进一个最小工程跑 SDK lint。"""
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
    tmp = Path(tempfile.mkdtemp(prefix='sgaudit_'))
    bad = 0
    for name, src in CASES.items():
        print('=' * 72)
        print(name)
        print('-' * 72)
        tag = name.split()[0].lower().replace('-', '')
        try:
            out = _roundtrip(src, tmp, tag)
        except Exception as exc:                       # noqa: BLE001
            print(f'  ✗ 审计本身出错：{type(exc).__name__}: {exc}')
            bad += 1
            continue
        print('  原文：')
        for line in src.split('\n'):
            print(f'    | {line}')
        print('  产物：')
        for line in out.split('\n'):
            print(f'    | {line}')
        # 结构判据：把产物再解析一遍，语句条数应当稳定
        try:
            again = _roundtrip(out, tmp, tag + 'b')
            if again != out:
                print('  ✗ 不幂等（第二遍又变了）')
                bad += 1
        except Exception as exc:                       # noqa: BLE001
            print(f'  ✗ 产物不能再解析：{type(exc).__name__}: {exc}')
            bad += 1
        # 引擎 lint
        proj = tmp / f'proj_{tag}'
        game = proj / 'game'
        game.mkdir(parents=True, exist_ok=True)
        (game / 'script.rpy').write_text(out, encoding='utf-8')
        rc, log = _lint(proj)
        if rc == -1:
            print(f'  · {log}')
        elif rc == 0:
            print('  ✓ 引擎 lint 通过')
        elif name in GIGO:
            kept = GIGO[name] in out
            print(f'  {"✓" if kept else "✗"} 原文本来就过不了 lint'
                  f'（判据：逐字保留 —— {"是" if kept else "****否****"}）')
            if not kept:
                bad += 1
        else:
            print(f'  ✗ 引擎 lint 失败（rc={rc}）')
            for line in log.strip().split('\n'):
                if ('File "' in line or 'rror' in line or 'arning' in line
                        or line.strip().startswith(('^', 'e ', 'u '))):
                    print(f'    {line}')
            bad += 1
        print()
    print('=' * 72)
    print(f'失败：{bad}')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
