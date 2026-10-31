# -*- coding: utf-8 -*-
"""临时探针（只读）：跨章继承那一行在**真机**上怎么补、"本章第一拍"接不接得住。

四件事：

  1. 每个章节的第一拍**能不能**当落点（条件 / 菜单块 / ATL 块 / 同 tag）；
  2. 真链条：上一章出口状态 → 下一章入口 → 帧里出现 `source = -1` 的行；
  3. 那 6 处"背景漏章 / 第 1 句台词还是上一章"的接缝，各自会补出哪一行；
  4. 卡片这条路径（不传 initial_state）里这种行是 **0 个** —— 入口现在按不到，
     等的是"把入口状态喂给卡片"那一步。

用法：python tools/_probe_chapter_first_real.py
"""
from pathlib import Path

from storygraph.app.stage import build_frame_at, plan_size_edit
from storygraph.app.stage_render import size_rows
from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
from storygraph.story.atl import scan_transforms
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.stage_snapshot import stage_exit_state

ROOTS = [Path(r'd:\千连万理\sr_general'), Path(r'd:\千连万理\renpytest\sr_general')]
SEAMS = [('chapter1_1', 'chapter1_2'), ('chapter1_4', 'chapter2_1'),
         ('chapter2_1', 'chapter2_1_1'), ('chapter2_1', 'chapter2_1_2'),
         ('chapter2_1', 'chapter2_1_3'), ('chapter2_1', 'chapter2_1_4')]


def _chapter(root: Path, name: str):
    """按章节名找到那个 .rpy 并解析成拍（与 `session.load` 同一条路）。"""
    for f in discover_scripts(root):
        if Path(f).stem == name:
            pr = ParseResultBuilder().build(RenPyParser().parse(str(f)))
            return Path(f), beats_from_parse_result(pr)
    return None, []


def landing_stats(root: Path) -> None:
    from storygraph.app.stage import _chapter_first_reason

    ok = bad = 0
    reasons = {}
    for f in discover_scripts(root):
        pr = ParseResultBuilder().build(RenPyParser().parse(str(f)))
        beats = beats_from_parse_result(pr)
        if not beats:
            continue
        why = _chapter_first_reason(beats[0])
        if why:
            bad += 1
            head = why.split('：')[0]
            reasons[head] = reasons.get(head, 0) + 1
        else:
            ok += 1
    print(f'第一拍可当落点：{ok} 个章节 · 接不住：{bad} 个'
          + ('' if not reasons else f'（{reasons}）'))


def seam(root: Path, prev: str, nxt: str, index, transforms) -> None:
    _, prev_beats = _chapter(root, prev)
    nxt_path, beats = _chapter(root, nxt)
    if not prev_beats or not beats:
        print(f'  {prev} → {nxt}: 没找到文件')
        return
    entry = stage_exit_state(prev_beats)
    if not str(entry.get('scene') or '').strip():
        print(f'  {prev} → {nxt}: 上一章**整章没有 scene**（{len(prev_beats)} 拍）'
              f' → 光是"上一文件的出口"是空的：跨章继承要沿着链条推'
              f'（continuity 干的就是这件事）。这一条**本工具现在还看不见**。')
        return
    frame = build_frame_at(beats, index, initial_state=entry,
                           project_root=root, transform_index=transforms,
                           screen=(1920, 1080))
    rows = size_rows(frame, project_root=root, screen=(1920, 1080),
                     transform_index=transforms)
    if not rows:
        print(f'  {prev} → {nxt}: 入口有背景但这一拍没有行')
        return
    row = rows[0]
    cover = next((it for it in row['items'] if it.get('key') == 'cover'
                  and it.get('ok')), None)
    at = str((cover or {}).get('at') or '')
    print(f'  {prev} → {nxt}（第 {index + 1} 拍）：'
          f'背景 {row["spec"]}（at {row["at"] or "原样"}）· source {row["source"]}'
          f' · 落点第 1 拍 · 选中 {at or "（无可用预设）"}')
    if not at:
        return
    edit = plan_size_edit(beats, index, row, key='cover', at=at,
                          project_root=root, transform_index=transforms,
                          screen=(1920, 1080))
    if edit.ok:
        print('        + ' + (edit.after or '').replace('; ', '  |  '))
        print(f'        {nxt_path.name} 第 1 拍：影响 '
              f'{edit.affects[0] + 1}–{edit.affects[1] + 1} 拍 · '
              f'插入={edit.inserted}')
    else:
        print('        拒绝：' + edit.reason)


def card_path_count(root: Path) -> None:
    """卡片那条路（不传 initial_state）：这种行应该是 0 个。"""
    n = 0
    for f in discover_scripts(root):
        pr = ParseResultBuilder().build(RenPyParser().parse(str(f)))
        beats = beats_from_parse_result(pr)
        if not beats:
            continue
        for i in range(len(beats)):
            frame = build_frame_at(beats, i)
            if frame is None:
                continue
            if str(frame.scene or '').strip() and int(frame.scene_src) < 0:
                n += 1
    print(f'卡片路径（无入口状态）里"背景在、出处未知"的拍：{n}')


for root in ROOTS:
    if not root.exists():
        continue
    print('=' * 72)
    print(root)
    transforms = scan_transforms(root)
    print(f'transforms {len(transforms)}')
    landing_stats(root)
    card_path_count(root)
    print('— 真实接缝 —')
    for prev, nxt in SEAMS:
        # 第 1 句台词那一拍（接缝报告里的"第 1 句台词时"）
        _, beats = _chapter(root, nxt)
        first = next((i for i, b in enumerate(beats)
                      if str(getattr(b, 'text', '') or '').strip()), 0)
        seam(root, prev, nxt, first, transforms)
