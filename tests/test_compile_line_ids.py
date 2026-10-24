"""
tests/test_compile_line_ids.py
==============================
清单 §四（P1）：同一行不许有两个身份。

编译结果表从前用 `line_id or f"{label}.{seq:04d}"` **临时捏**一个号：
`seq` 是全文连续序号、`line_id` 的号是 label 内连续序号，多 label 文件里
两者必然不同。于是同一拍在编译表里是 `ch02.0003`、导出 Excel 后变成
`ch02.0001` —— 同一条台词两个 key，翻译/语音跟着分叉。

现在编译前先 `ensure_ids`（唯一发号实现），报表直接用 `beat.line_id`。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.pipeline.compile import compile_beats          # noqa: E402
from storygraph.story.beat_model import Beat, assign_identity  # noqa: E402
from storygraph.story.line_identity import parse               # noqa: E402


def _beats():
    return [
        Beat(label='ch01', seq=1, text='第一章第一句'),
        Beat(label='ch02', seq=2, text='第二章开场'),
        Beat(label='ch02', seq=3, text='第二章第二句'),
        Beat(label='', seq=4, text='没有归属的一拍'),
    ]


def test_compile_fills_ids_before_reporting():
    beats = _beats()
    res = compile_beats(beats, include_transforms=False)

    assert all(b.line_id for b in beats), '编译没有回填身份'
    row_ids = {row['line_id'] for row in res.rows}
    assert row_ids == {b.line_id for b in beats}
    assert all(parse(lid) is not None for lid in row_ids), row_ids
    assert not any(lid.endswith('.0000') for lid in row_ids), row_ids


def test_compile_ids_match_the_identity_allocator():
    """同一批拍：编译报表的 ID == `assign_identity`（表格/导出）的 ID。"""
    expected = _beats()
    assign_identity(expected)
    expected_ids = [b.line_id for b in expected]

    got = _beats()
    res = compile_beats(got, include_transforms=False)
    assert [b.line_id for b in got] == expected_ids
    assert [row['line_id'] for row in res.rows] == expected_ids


def test_compile_never_rewrites_existing_ids():
    """已有身份是主键：编译只是"读报表"，不许顺手改掉它。"""
    beats = [Beat(label='ch01', seq=1, text='a', line_id='ch01.0042'),
             Beat(label='ch01', seq=2, text='b', line_id='ch01.0007')]
    res = compile_beats(beats, include_transforms=False)
    assert [b.line_id for b in beats] == ['ch01.0042', 'ch01.0007']
    assert [row['line_id'] for row in res.rows] == ['ch01.0042', 'ch01.0007']
