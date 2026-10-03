"""
探针：`Beat.notes` 到底会不会在 Excel 往返里丢。

怀疑来源：`ADVANCED_FIELDS` 里有 `'Notes'`（前端会建控件、会回读），
但 `BEAT_FIELDS`（列顺序唯一真源）里**没有** `Notes`。
那前端填进去的备注，导出到 Excel 时就该没了。

只读，不写盘。用法：
    D:/Python/python.exe tools/probe_notes_loss.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.beat_model import BEAT_FIELDS, Beat
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel


def main() -> int:
    print('BEAT_FIELDS 里有 Notes 吗：', 'Notes' in BEAT_FIELDS)
    print('Beat 有 notes 字段吗：', 'notes' in Beat.__dataclass_fields__)
    print()

    beats = [
        Beat(seq=1, label='a', kind='say', speaker='e', text='第一句',
             notes='这句要重录，情绪再强烈一点'),
        Beat(seq=2, label='a', kind='say', speaker='e', text='第二句',
             notes='备用台词'),
    ]

    with tempfile.TemporaryDirectory() as td:
        xl = Path(td) / 'notes.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)

    print('往返之后：')
    for before, after in zip(beats, back):
        got = getattr(after, 'notes', '<没有这个字段>')
        flag = '✓ 保住' if got == before.notes else '✗ 丢了'
        print(f'  {flag}  原文 notes={before.notes!r} → 回读 notes={got!r}')

    lost = sum(1 for b, a in zip(beats, back)
               if getattr(a, 'notes', None) != b.notes)
    print(f'\n结论：{lost}/{len(beats)} 条备注丢失')
    return 1 if lost else 0


if __name__ == '__main__':
    raise SystemExit(main())
