#!/usr/bin/env python
"""P1 十条：当前到底哪几条还站着（一次性体检，不改任何文件）。

跑法：
    python tools/_probe_p1_state.py
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SDK = Path(r'D:\software\renpy-8.4.1-sdk')


def _render(sample: str, name: str, *, source=False):
    """sample 文本 → xlsx → rpy；返回 (beats, 产物文本, note)。"""
    from storygraph.story.script_table import open_rpy_as_table
    from storygraph.pipeline.compile import compile_table
    tmp = Path(tempfile.mkdtemp(prefix=f'p1_{name}_'))
    src = tmp / f'{name}.rpy'
    src.write_text(sample, encoding='utf-8')
    xlsx = tmp / f'{name}.xlsx'
    out = tmp / f'{name}_out.rpy'
    open_rpy_as_table(str(src), str(xlsx))
    _p, _s, res = compile_table(
        str(xlsx), rpy_path=out, write_sheet=False, force=True,
        source_path=str(src) if source else None, skip_empty=True,
        engine_lint=False)
    text = out.read_text(encoding='utf-8') if out.exists() else ''
    return res, text, src


def probe_menu_speaker():
    """P1-A：`menu:` 里带说话人的标题，全量路径还在不在。"""
    from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy

    sample = (
        'label start:\n'
        '    menu:\n'
        '        s "Sure, but what\'s a \\"visual novel?\\""\n'
        '        "Yes":\n'
        '            return\n'
        '        "No":\n'
        '            return\n'
    )
    tmp = Path(tempfile.mkdtemp(prefix='p1_menu_'))
    src = tmp / 'm.rpy'
    src.write_text(sample, encoding='utf-8')
    nodes = RenPyParser().parse(src)
    result = ParseResultBuilder().build(nodes)
    beats = beats_from_parse_result(result, label='start')
    full = render_beats_to_rpy(beats, label_name=None)
    ok_parsed = 'visual novel' in full
    print(f'[P1-A 菜单带说话人标题] 拍数={len(beats)} '
          f'台词在产物里={"是" if ok_parsed else "**否**"}')
    print('  拍清单：' + ' | '.join(f'{b.line_id}:{b.kind}' for b in beats))
    if not ok_parsed:
        print('  产物：\n' + '\n'.join('    ' + ln for ln in full.splitlines()))
    return ok_parsed


def probe_init_minus1():
    """P1-3c：`init -1 python:` 认不认（认了才是可编辑的 python 块）。"""
    from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder

    sample = (
        'init -1 python:\n'
        '    def part(expr):\n'
        '        return expr\n'
        '\n'
        'label start:\n'
        '    "hi"\n'
        '    return\n'
    )
    tmp = Path(tempfile.mkdtemp(prefix='p1_init_'))
    src = tmp / 'd.rpy'
    src.write_text(sample, encoding='utf-8')
    nodes = RenPyParser().parse(src)
    result = ParseResultBuilder().build(nodes)
    heads = [(b.header, b.source_line) for b in result.file_blocks]
    pynodes = [n for n in nodes if type(n).__name__ == 'PythonNode']
    print(f'[P1-3c init -1 python] PythonNode={len(pynodes)} '
          f'file_blocks={heads}')

    from storygraph.story.script_table import open_rpy_as_table
    from storygraph.pipeline.compile import compile_table
    xlsx = tmp / 'd.xlsx'
    out = tmp / 'd_out.rpy'
    open_rpy_as_table(str(src), str(xlsx))
    compile_table(str(xlsx), rpy_path=out, write_sheet=False, force=True,
                  skip_empty=True, engine_lint=False)
    text = out.read_text(encoding='utf-8') if out.exists() else ''
    ok = 'init -1 python:' in text and 'def part' in text
    print(f'  全量产物里整块还在={"是" if ok else "**否**"}')
    return ok


def probe_full_path_reformat():
    """P1-1：不给 --source 的全量路径会不会重排整份文件。"""
    from storygraph.story.script_table import open_rpy_as_table
    from storygraph.pipeline.compile import compile_table

    src = SDK / 'the_question' / 'game' / 'script.rpy'
    if not src.exists():
        print(f'[P1-1 全量重排] 跳过：没有 {src}')
        return None
    tmp = Path(tempfile.mkdtemp(prefix='p1_full_'))
    xlsx = tmp / 'q.xlsx'
    out = tmp / 'q.rpy'
    open_rpy_as_table(str(src), str(xlsx))
    compile_table(str(xlsx), rpy_path=out, write_sheet=False, force=True,
                  skip_empty=True, engine_lint=False)
    a = src.read_bytes()
    b = out.read_bytes()
    from tools.audit_byte_exact import strip_banner
    body, _ = strip_banner(b)
    src_body, _ = strip_banner(a)
    la = a.decode('utf-8-sig').splitlines()
    lb = b.decode('utf-8-sig').splitlines()
    crlf_a, crlf_b = a.count(b'\r\n'), b.count(b'\r\n')
    blank_a = sum(1 for ln in la if not ln.strip())
    blank_b = sum(1 for ln in lb if not ln.strip())
    set_a = [ln for ln in la if ln.strip()]
    set_b = [ln for ln in lb if ln.strip()]
    only_b = [ln for ln in set_b if ln.strip() not in {x.strip() for x in set_a}]
    only_a = [ln for ln in set_a if ln.strip() not in {x.strip() for x in set_b}]
    same = body == src_body
    print(f'[P1-1 全量重排] {src.name}: CRLF {crlf_a}->{crlf_b} · '
          f'空行 {blank_a}->{blank_b} · 行数 {len(la)}->{len(lb)} · '
          f'逐字节相同（剥生成标记后）={"是" if same else "**否**"}')
    print(f'  产物多出的行（{len(only_b)}）：{only_b[:6]}')
    print(f'  产物丢掉的行（{len(only_a)}）：{only_a[:6]}')
    return bool(same)


def probe_build_report_ran():
    """P2-B：lint 跑过且通过时，报告里的 ran 是不是 false。"""
    from storygraph.pipeline.build import build

    src = SDK / 'the_question'
    tmp = Path(tempfile.mkdtemp(prefix='p1_build_'))
    res = build(src, out_dir=tmp, write_report=False, engine_lint=True)
    info = (res.report.get('build', {}) or {}).get('guard', {}).get(
        'engine_lint', {})
    print(f'[P2-B 报告 ran] {info}')
    return bool(info.get('ran'))


def probe_has_change():
    """P1-8：'只看有变化的拍' 的判据。"""
    from storygraph.app.stage import build_frames
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder

    sample = (
        'label start:\n'
        '    scene bg room\n'
        '    "第一句"\n'
        '    "第二句"\n'
        '    show ling happy\n'
        '    "第三句"\n'
        '    return\n'
    )
    tmp = Path(tempfile.mkdtemp(prefix='p1_chg_'))
    src = tmp / 'c.rpy'
    src.write_text(sample, encoding='utf-8')
    result = ParseResultBuilder().build(RenPyParser().parse(src))
    beats = beats_from_parse_result(result, label='start')
    frames = build_frames(beats)
    print('[P1-8 只看有变化的拍]')
    for f in frames:
        print(f'  #{f.index + 1} {f.line_id} changes={f.changes!r} '
              f'text={f.text!r}')
    # 直接看预览的判据现在会留几拍
    keep = [f for f in frames if (f.changes or '').strip()]
    print(f'  有真实变化的拍：{len(keep)}/{len(frames)}')
    return bool(keep) and len(keep) < len(frames)


def main():
    results = {}
    results['P1-A 菜单带说话人'] = probe_menu_speaker()
    print()
    results['P1-3c init -1 python'] = probe_init_minus1()
    print()
    results['P1-1 全量重排'] = probe_full_path_reformat()
    print()
    results['P1-8 只看有变化的拍'] = probe_has_change()
    print()
    print('=' * 60)
    for k, v in results.items():
        print(f'  {k}: {"OK" if v else "**仍有问题**"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
