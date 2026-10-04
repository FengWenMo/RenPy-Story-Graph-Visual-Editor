"""
探针：Notes 到底"丢了"还是"降级并进 Comment"？

昨天我据 `a.notes == ''` 判成"备注丢失"。但 `beat_model.to_row`
里明明有"把 self.notes 并进注释列"的逻辑 —— 所以很可能**字没丢**，
只是从独立字段降级成了 Comment 的一部分。

这两种定性导向完全不同的修法，必须先查清。
只读，不写盘。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.beat_model import Beat
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.pipeline.compile import compile_beats

NOTE = '这句要重录，情绪再强烈一点'


def main() -> int:
    beats = [Beat(seq=1, label='a', kind='say', speaker='e',
                  text='第一句', notes=NOTE)]

    print('— 导出前 —')
    print(f'  notes   = {beats[0].notes!r}')
    print(f'  comment = {beats[0].comment!r}')

    with tempfile.TemporaryDirectory() as td:
        xl = Path(td) / 'n.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)

    b = back[0]
    print('\n— Excel 往返后 —')
    print(f'  notes   = {b.notes!r}')
    print(f'  comment = {b.comment!r}')

    survived = NOTE in (b.comment or '')
    print(f'\n备注的字还在吗（找 comment）：{"✓ 在" if survived else "✗ 真的没了"}')

    # 关键：备注会不会混进 .rpy 产物？
    rpy = compile_beats(back).rpy_text
    in_product = NOTE in rpy
    print(f'备注会不会进 .rpy 产物：{"✗ 会（混进注释）" if in_product else "✓ 不会"}')
    if in_product:
        for ln in rpy.splitlines():
            if NOTE in ln:
                print(f'    产物里这一行：{ln.strip()!r}')

    # 幂等：再来一轮会不会翻倍？
    with tempfile.TemporaryDirectory() as td:
        xl2 = Path(td) / 'n2.xlsx'
        export_beats_to_excel(back, xl2)
        back2 = import_beats_from_excel(xl2)
    n = (back2[0].comment or '').count(NOTE)
    print(f'\n再往返一次，备注出现 {n} 次：'
          f'{"✗ 翻倍了" if n > 1 else "✓ 幂等"}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
