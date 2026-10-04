"""
三条"新一轮测出来"的缺陷取证（菜单标题/选项文案的转义、选项体变 `pass`、
`Inline` 分隔符被劈开）。逐轮 `repr` 打出来，别从屏幕上的样子倒推。

用法：
    python tools/probe_round_escapes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASES = {
    '①menu_caption_newline': ('menu', '第一行\n第二行'),
    '①menu_caption_backslash': ('menu', '路径 C:\\new'),
    '①option_newline': ('option', '选项\n第二行'),
    '①option_backslash': ('option', '路径 C:\\new'),
}


def _beats(text: str, path: Path) -> list:
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    path.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _dump(tag: str, beats: list) -> None:
    print(f'  --- {tag}')
    for i, b in enumerate(beats):
        print(f'    [{i}] kind={b.kind:<12} text={b.text!r:<28} '
              f'cond={(b.condition or "").strip()!r:<10} '
              f'nest={b.nest_path!r}')


def main() -> int:
    import tempfile
    from storygraph.story.beat_model import (Beat, compose_show_cell,
                                             menu_caption_text)
    from storygraph.story.beat_render import render_beats_to_rpy

    tmp = Path(tempfile.mkdtemp(prefix='round_esc_'))
    for name, (kind, text) in CASES.items():
        print('=' * 74)
        print(f'{name}: {kind} text={text!r}')
        if kind == 'menu':
            beats = [Beat(seq=1, label='a', kind='menu', text=text),
                     Beat(seq=2, label='a', kind='menu_option', text='选项'),
                     Beat(seq=3, label='a', kind='say', speaker='e', text='体')]
        else:
            beats = [Beat(seq=1, label='a', kind='menu', text='标题'),
                     Beat(seq=2, label='a', kind='menu_option', text=text),
                     Beat(seq=3, label='a', kind='say', speaker='e', text='体')]
        _dump('R0（构造的拍）', beats)
        r1 = render_beats_to_rpy(beats, label_name='a')
        print('  R1:')
        for ln in r1.splitlines():
            if ln.strip():
                print(f'    {ln!r}')
        b2 = _beats(r1, tmp / f'{name}_1.rpy')
        _dump('R1 再解析的拍', b2)
        r2 = render_beats_to_rpy(b2, label_name='a')
        print('  R2:')
        for ln in r2.splitlines():
            if ln.strip():
                print(f'    {ln!r}')
        print(f'  R1 == R2 ? {r1 == r2}')
        print(f'  标题/文案字段：R0 text={text!r} → R1 再解析 '
              f'{[getattr(b, "text", "") for b in b2]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
