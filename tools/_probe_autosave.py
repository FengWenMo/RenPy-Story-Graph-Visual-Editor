"""临时探针：量一下"整章拍 → JSON → 拍"的体积 / 耗时 / 保真。

只读，不改任何用户文件。
"""
from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path

ROOT = Path(r'D:\千连万理\renpytest\sr_general')


def main() -> int:
    from storygraph.app.editor import EditorSession

    target = ROOT / 'game' / 'chapter' / 'chapter1_4.rpy'
    if not target.is_file():
        print('没有这个章节：', target)
        return 1
    s = EditorSession(registry_provider=None, project_root=ROOT)
    t0 = time.perf_counter()
    n = s.load(target)
    t1 = time.perf_counter()
    print(f'载入 {n} 拍  {t1 - t0:.3f}s')

    rows = [dataclasses.asdict(b) for b in s.beats]
    t0 = time.perf_counter()
    blob = json.dumps({'beats': rows}, ensure_ascii=False)
    t1 = time.perf_counter()
    print(f'asdict+json.dumps {len(blob) / 1024:.0f} KB  {t1 - t0:.3f}s')
    t0 = time.perf_counter()
    got = json.loads(blob)['beats']
    t1 = time.perf_counter()
    print(f'json.loads {t1 - t0:.3f}s')

    from storygraph.story.beat_model import Beat
    t0 = time.perf_counter()
    back = [Beat(**r) for r in got]
    t1 = time.perf_counter()
    print(f'重建 {len(back)} 拍  {t1 - t0:.3f}s')

    from storygraph.story.beat_render import render_beat_to_rpy
    bad = 0
    for i, (a, b) in enumerate(zip(s.beats, back)):
        la = list(render_beat_to_rpy(a))
        lb = list(render_beat_to_rpy(b))
        if la != lb:
            bad += 1
            if bad <= 3:
                print(f'  ✗ 第 {i + 1} 拍渲染不同')
                for x, y in zip(la, lb):
                    if x != y:
                        print(f'     原: {x!r}\n     回: {y!r}')
                        break
    print(f'渲染对比：{len(back)} 拍里 {bad} 拍不同')

    # 指纹口径（导入基线之后）
    prints_a = [s._beat_edits_print(i) for i in range(len(s.beats))]
    s.beats = back
    prints_b = [s._beat_edits_print(i) for i in range(len(back))]
    print('edits_print 一致：', prints_a == prints_b)
    return 0


if __name__ == '__main__':
    sys.exit(main())
