"""
把整个项目的缩略图**预热**到磁盘缓存（之后任何界面都是 2ms 命中）。

用法：

    python tools/prewarm_thumbs.py [项目根]

    # 真实项目：2816 张图 × 2 个尺寸（选择器 104×74 / 立绘卡 160×220）
    # 冷解码 ~400ms/张，核数并行 → 几分钟；跑完再打开界面就是秒开。
    # Ctrl+C 随时中断：解好的都进缓存了，下次接着来就是命中。

为什么是"显式跑一次"而不是偷偷做：这是**真的要烧几分钟 CPU** 的批处理，
必须让用户看得见进度、能中止（见 `app/prewarm.py`）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CWD = Path.cwd()
for cand in (str(_CWD), str(_HERE.parent)):
    if cand in sys.path:
        sys.path.remove(cand)
sys.path.insert(0, str(_CWD))

DEFAULT_PROJECT = Path(r'D:\千连万理\RenpyTest\sr_general')


def main(argv) -> int:
    project = Path(argv[1]) if len(argv) > 1 else DEFAULT_PROJECT
    from storygraph.app.prewarm import (UI_THUMB_SIZES, image_paths, prewarm,
                                        prewarm_project)
    from storygraph.pipeline.build import load_asset_registry

    reg = load_asset_registry(project)
    if reg is None:
        print(f'读不到资源表：{project}')
        print('（先用编辑器打开一次项目，它会自动建表；'
              '或 python -m storygraph.cli scan --help）')
        return 2
    paths = image_paths(reg, project)
    print(f'项目：{project}')
    print(f'图片 {len(paths)} 张 × {len(UI_THUMB_SIZES)} 个尺寸 = '
          f'{len(paths) * len(UI_THUMB_SIZES)} 个缩略图要解')

    last = [0.0, time.monotonic()]

    def on_progress(done, total):
        now = time.monotonic()
        if now - last[1] < 0.5 and done != total:
            return
        last[1] = now
        pct = (done / total * 100) if total else 100.0
        print(f'\r  进度 {done}/{total}（{pct:.1f}%）', end='', flush=True)

    try:
        res = prewarm(paths, on_progress=on_progress)
    except KeyboardInterrupt:
        print('\n中断：已解好的都在缓存里，下次接着来就是命中。')
        return 130
    print()
    print(f'完成：成功 {res["done"]} · 失败 {res["failed"]} · '
          f'用时 {res["seconds"]}s')
    if res['failed']:
        print('（失败的是坏图 / 格式不支持 —— 不影响其它图）')
    print('现在再打开编辑器：缩略图应当是秒出（缓存命中 ~2ms）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
