# -*- coding: utf-8 -*-
"""临时探针（只读）：跨章继承背景在**真机**上到底有多少、卡片看不看得见。

三个问题：
  1. 卡片这条路（单文件、无 initial_state）生成的帧里，`scene` 非空却
     `state['scene_src'] < 0` 的拍有多少 —— 也就是"跨章那一行"从界面上
     够不够得着（按文件逐个解析，和 `session.load` 一模一样）；
  2. 有多少**章节**（文件）开场在"第一句台词"之前没写 scene/换景 —— 这些
     章节第 1 句台词时玩家看到的是上一章的背景，而卡片上是一片空舞台；
  3. `analyze_project` 的耗时（给"载入一章时算入口状态"要不要缓存）。

用法：python tools/_probe_entry_real.py
"""
import time
from pathlib import Path

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.stage_snapshot import snapshot_rows

ROOTS = [Path(r'd:\千连万理\sr_general'), Path(r'd:\千连万理\renpytest\sr_general')]


def one_root(root: Path) -> None:
    print('=' * 70)
    print(root)
    files = list(discover_scripts(root))
    print(f'files {len(files)}')
    t0 = time.time()
    total = cross = 0
    no_scene = []
    for f in files:
        pr = ParseResultBuilder().build(RenPyParser().parse(str(f)))
        beats = beats_from_parse_result(pr)
        if not beats:
            continue
        total += len(beats)
        rows = snapshot_rows(beats)
        for row in rows:
            st = row.get('state') or {}
            if str(st.get('scene') or '').strip() and int(st.get('scene_src', -1)) < 0:
                cross += 1
        first_say = -1
        for i, b in enumerate(beats):
            if str(getattr(b, 'text', '') or '').strip():
                first_say = i
                break
        head = beats[:first_say + 1] if first_say >= 0 else beats
        has_scene = any(
            (str(getattr(b, 'scene', '') or '').strip()
             or 'bg ' in str(getattr(b, 'show', '') or ''))
            for b in head)
        if head and not has_scene:
            no_scene.append((f.name, len(beats), first_say))
    t1 = time.time()
    print(f'beats {total} · parse-all {t1 - t0:.2f}s')
    print(f'卡片帧里"背景在、但 scene_src < 0"的拍：{cross}')
    print(f'开场（第一句台词前）没写 scene 的章节：{len(no_scene)} / {len(files)}')
    for name, n, fs in no_scene[:15]:
        print(f'   · {name:<30} {n:>4} 拍 · 第一句台词在第 {fs + 1} 拍')


def cost_of_report(root: Path) -> None:
    from storygraph.pipeline.continuity import analyze_project
    t0 = time.time()
    rep = analyze_project(root)
    t1 = time.time()
    scenes = [fd for fd in getattr(rep, 'findings', []) if fd.code in ('S0', 'S1')]
    print(f'analyze_project {t1 - t0:.2f}s · findings {len(rep.findings)} '
          f'· S0/S1 {len(scenes)}')
    for fd in scenes[:6]:
        print(f'   · {fd.code} {fd.title}')


for r in ROOTS:
    if r.exists():
        one_root(r)
        cost_of_report(r)
