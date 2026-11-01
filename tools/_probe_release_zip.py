# -*- coding: utf-8 -*-
"""
开箱核对：把**打出来的那个 zip** 当成"用户拿到手的东西"检查一遍。

用法：
    python tools/_probe_release_zip.py dist/StoryGraph-1.0.0-win64-2026-11-01b-rules.zip

判据都是"用户能看见的那一层"：
  · 解压撒不撒一地（顶层必须只有 `StoryGraph/`）；
  · 两个 exe、说明、预设、示例项目在不在；
  · `demo/` 是不是真的没有（程序版）；
  · 有没有把开发垃圾带进去（`__pycache__` / `.pip-tmp` / `.pyc` / `.bak`）；
  · 最大的 8 个文件（看体积是不是 Qt 该占的那些）。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

#: 程序版必须有（解压后相对 `StoryGraph/` 的路径）
#:
#: ⚠ 名字从**实际打出来的包**里抄的，不是猜的（第一版这里写了 `说明书.md`，
#: 于是判据自己在报"缺文件"—— 判据不许猜格式）。
REQUIRED = (
    'StoryGraph.exe',
    'StoryGraph-CLI.exe',
    'LICENSE',
    '快速上手.md',
    '新手快速操作手册.md',
    '剧本空表格.xlsx',
    'storygraph.rules.json',
    'storygraph.mapping.json',
)

#: 不该出现在交付包里的东西
FORBIDDEN_PARTS = ('__pycache__', '.pip-tmp', '.pytest_cache', '.git')


def main(argv) -> int:
    if len(argv) < 2:
        print('用法：python tools/_probe_release_zip.py <zip>', file=sys.stderr)
        return 2
    p = Path(argv[1])
    if not p.is_file():
        print(f'找不到 zip：{p}', file=sys.stderr)
        return 2

    z = zipfile.ZipFile(p)
    names = [n for n in z.namelist() if not n.endswith('/')]
    bad = []
    roots = sorted({n.split('/')[0] for n in z.namelist()})
    print(f'zip        {p.name}（{p.stat().st_size / 1024 / 1024:.1f} MB）')
    print(f'条目       {len(names)} 个文件')
    print(f'顶层       {roots}')
    if roots != ['StoryGraph']:
        bad.append(f'顶层不是单一 StoryGraph/：{roots}')

    root = f'{roots[0]}/' if roots else ''
    have = {n[len(root):] for n in names if n.startswith(root)}
    for rel in REQUIRED:
        print(f'  {"[有]" if rel in have else "[缺]"} {rel}')
        if rel not in have:
            bad.append(f'缺 {rel}')

    demo = [n for n in names if '/demo/' in n or n.startswith(root + 'demo/')]
    print(f'demo       {len(demo)} 个（程序版应为 0）')
    if demo:
        bad.append(f'程序版里混进了 demo：{demo[:3]}')

    junk = [n for n in names
            if any(part in n for part in FORBIDDEN_PARTS)
            or n.endswith(('.pyc', '.pyo'))
            or '.bak' in Path(n).name]
    print(f'垃圾        {len(junk)} 个')
    if junk:
        bad.append(f'带进了开发垃圾：{junk[:5]}')

    sizes = sorted(((z.getinfo(n).file_size, n) for n in names), reverse=True)[:8]
    print('最大的 8 个：')
    for size, n in sizes:
        print(f'  {size / 1024 / 1024:7.1f} MB  {n[len(root):]}')

    if bad:
        print('\n✗ 没通过：')
        for b in bad:
            print(f'  · {b}')
        return 1
    print('\n✓ 开箱核对通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
