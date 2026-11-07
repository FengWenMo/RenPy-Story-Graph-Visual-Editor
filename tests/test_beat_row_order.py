"""
`Beat.to_row()` 的**列序契约**（2026-11-03）
==========================================

为什么单开一份：今天把「台词后缀（内部）」升成可见的「台词后缀」时，
我顺手把它挪到了「台词」旁边 —— 结果**值整体错位**：`say_tail` 的值落进了
`CameraHeader` 列，而写表时**一点异常都没有**，是
`dict(zip(BEAT_FIELDS, beat.to_row()))` 才露出来。

根因：`to_row()` 返回的是**硬编码的位置列表**（`self.line_id, self.seq, …,
self.camera_header, self.say_tail, …`），而 `beat_fields` 的注释写着
"列顺序 = `BEAT_FIELDS`，一条都不许在这里另写"。**注释与实现不符**，
于是任何"移动 FieldSpec"的改动都会静默错位。

这个文件钉三件事：

  1. **每个字段的值必须落在自己那一列**（灌独特标记，错位立刻现形）；
  2. **列数一致**（`COLUMN_SPECS` 与 `to_row()` 长度相等）；
  3. **表头不重复**（改名/加列最容易撞的两个坑）。

外加一条历史兼容：**老表的「台词后缀（内部）」照样读得回来**
（`LEGACY_BEAT_HEADERS`，缺它 = 列改名那天全项目的 `with` 静默消失）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.story.beat_fields import (BEAT_FIELDS, BEAT_FIELD_SPECS,  # noqa: E402
                                          BEAT_HEADERS, COLUMN_SPECS,
                                          LEGACY_BEAT_HEADERS)
from storygraph.story.beat_model import Beat                              # noqa: E402


class TestEveryFieldStaysInItsOwnColumn:
    def test_marks_land_in_the_right_column(self):
        """给每个字段灌 `Z<字段名>Z`，它必须出现在 `BEAT_FIELDS` 里的同一格。

        比"完全相等"宽（容忍归一：`text` 会过 `menu_caption_text`、
        `comment` 会拼 `notes`），但**错位一定抓得住** —— 那才是今晚出的事。
        """
        beat = Beat(kind='say', speaker='jc', text='正文')
        marks: dict = {}
        for spec in BEAT_FIELD_SPECS:
            attr = spec.attr
            if not attr or not hasattr(beat, attr):
                continue
            cur = getattr(beat, attr)
            if isinstance(cur, bool) or isinstance(cur, (list, tuple, dict, set)):
                continue
            mark = f'Z{spec.name}Z'
            setattr(beat, attr, mark)
            marks[spec.name] = mark

        row = beat.to_row()
        pairs = list(zip(BEAT_FIELDS, row))
        misplaced = []
        for name, mark in marks.items():
            home = next((i for i, (n, _v) in enumerate(pairs) if n == name), None)
            if home is None:
                continue
            landed = [i for i, (_n, v) in enumerate(pairs) if mark in str(v or '')]
            if landed and home not in landed:
                misplaced.append(
                    f'{name}: 该在「{BEAT_HEADERS.get(name, name)}」'
                    f'（第 {home + 1} 列），却出现在第 '
                    f'{[i + 1 for i in landed]} 列')
        assert not misplaced, '列顺序错位（`to_row` 硬编码位置 vs BEAT_FIELDS）：\n  ' \
                              + '\n  '.join(misplaced)

    def test_row_length_matches_column_count(self):
        """`to_row` 少一列或多一列都会让整张表错位。"""
        assert len(Beat(kind='say', text='x').to_row()) == len(COLUMN_SPECS) \
            == len(BEAT_FIELDS)

    def test_every_column_spec_has_a_field(self):
        assert [s.name for s in COLUMN_SPECS] == list(BEAT_FIELDS)


class TestHeadersAreUnambiguous:
    def test_no_duplicate_headers(self):
        """两个列同名 → `from_row` 按名匹配时**后写的赢**，另一列永远读不到。"""
        seen: dict = {}
        dupes = []
        for name, header in BEAT_HEADERS.items():
            if not header:
                continue
            if header in seen:
                dupes.append(f'{header}（{seen[header]} / {name}）')
            seen[header] = name
        assert not dupes, f'表头重复：{dupes}'

    def test_legacy_headers_do_not_collide(self):
        """别名不许和现用表头撞（撞了就是"新表被当老表读"）。"""
        for old, field in LEGACY_BEAT_HEADERS.items():
            assert old not in BEAT_HEADERS.values(), \
                f'别名 {old!r} 与现用表头重名（映射到 {field}）'


class TestOldTablesStillReadable:
    """列改名那天，老表必须照样读得回来 —— 否则静默丢掉所有 `with`。"""

    def _row(self, header: str, value: str) -> dict:
        row = {h: '' for h in BEAT_HEADERS.values() if h}
        row['类型'] = 'say'
        row['说话人'] = 'jc'
        row['台词'] = '带转场'
        row[header] = value
        return row

    def test_legacy_say_tail_header(self):
        beat = Beat.from_row(self._row('台词后缀（内部）', 'with dissolve'))
        assert beat.say_tail == 'with dissolve'

    def test_new_say_tail_header(self):
        beat = Beat.from_row(self._row('台词后缀', 'with vpunch'))
        assert beat.say_tail == 'with vpunch'

    def test_english_field_name_still_works(self):
        beat = Beat.from_row(self._row('SayTail', 'with dissolve'))
        assert beat.say_tail == 'with dissolve'
