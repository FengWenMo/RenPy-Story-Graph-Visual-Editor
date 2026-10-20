"""
字段契约的唯一真源：`story.beat_fields.FieldSpec`（2026-10-17 架构收敛）
======================================================================

调研结论（用户 2026-10-16）：保真字段的"登记、快照、哈希、协作、序列化"
散在五六张手写清单里 —— `BEAT_FIELDS` / `_SNAPSHOT_FIELDS` /
`_beat_content_hash()` 的 parts / `collab_store._FIELD_ATTR` / `schema.py`。
每加一个字段要人肉同步，漏一处**不会报错**，只会"某条路径静默丢数据"
（实测：协作库用 `field.lower()` 猜属性名，`SpecHead → spechead` 取空，
过一次库自定义语句头、循环头、相机头、代码块结构全没了）。

现在只有一张表。这个文件守四件事：

  ① **没有字段能绕过登记**：`Beat` 上每个 dataclass 字段都必须出现在
     `FieldSpec` 表里（运行时元数据要明写豁免 + 理由）；
  ② **登记了就必须进快照**：撤销漏字段 = 留半新半旧；
  ③ **登记了就必须进指纹**：漏了 = 改了内容判成没变，协作 push 静默丢改动；
  ④ **指纹/快照真的覆盖**：逐个字段变异，指纹必须变、快照必须能还原。

⚠ 允许豁免的只有三类，且都在 `RUNTIME_ONLY` / `FINGERPRINT_EXEMPT` 里
**逐条写明为什么**（"身份坐标"不是内容；"源文件行号"不该被撤销改掉）。
"""

from __future__ import annotations

import copy
import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.beat_fields import (                 # noqa: E402
    BEAT_FIELDS, BEAT_FIELD_SPECS, BEAT_HEADERS, COLUMN_SPECS,
    FINGERPRINT_FIELDS, HIDDEN_BEAT_COLUMNS, SINGLE_LINE_BEAT_FIELDS,
    SNAPSHOT_FIELDS,
)
from storygraph.story.beat_model import (                  # noqa: E402
    Beat, _beat_content_hash,
)

#: **不进快照/指纹**的运行时元数据 —— 每条都要说清为什么。
RUNTIME_ONLY = {
    'src_first': '增量导出用的源文件行号：撤销一次编辑不该改动它在源文件的位置',
    'src_last': '同上',
    '_from_script': '解析来源标记：不是内容，撤销也不该改',
    'sanitized_cells': '清洗留痕：W114 报告的运行时证据，不是内容',
    'runtime_notes': '导入期自动处置留痕（W203），不是内容',
    'kind_raw': '`类型` 列的原值（工具不认时点名用）：读表时的输入证据，'
                '不参与渲染，撤销也不该改它',
}

#: **进快照但不进指纹**的字段：它们要么是身份坐标，要么就是指纹本身。
FINGERPRINT_EXEMPT = {
    'LineId': '身份坐标（主键），不是内容；进指纹会让"改一列身份"变成内容变更',
    'Seq': '排序坐标：插一行会改掉后面所有行，拿它算命内容会制造无意义冲突'
           '（顺序变化由 push 的 meta_changed 单独处理）',
    'ContentHash': '指纹自己（值就是它）',
}


def _sentinel(attr: str):
    """给某个属性造一个"与默认值必然不同"的值（按属性类型/默认值选）。"""
    if attr == 'kind':
        return 'narration'
    if attr == 'music_action':
        return 'stop'
    if attr == 'sound_action':
        return 'stop'
    if attr == 'ordered':
        return [('raw', 'pause 1.0', 0)]
    if attr == 'code_blocks':
        return [(1, 2)]
    if attr == 'nest_path':
        return 'if:1>A'
    return 'X'


class TestSingleSource:

    def test_columns_come_from_the_spec_table(self):
        """列顺序 / 表头 / 隐藏列 / 单行列都是 `FieldSpec` 的投影。"""
        assert BEAT_FIELDS == [s.name for s in COLUMN_SPECS]
        assert BEAT_HEADERS == {s.name: s.header for s in COLUMN_SPECS}
        assert HIDDEN_BEAT_COLUMNS == tuple(
            s.name for s in COLUMN_SPECS if s.hidden)
        assert SINGLE_LINE_BEAT_FIELDS == tuple(
            s.name for s in COLUMN_SPECS if s.single_line)
        assert len(BEAT_FIELD_SPECS) == len({s.attr for s in BEAT_FIELD_SPECS})

    def test_no_beat_field_can_bypass_registration(self):
        """
        **新字段守门人**：`Beat` 上多一个 dataclass 字段而没进 `FieldSpec`
        → 这条红。它抓的正是"加字段忘了登记"那类静默丢数据。
        """
        declared = {f.name for f in dataclasses.fields(Beat)}
        registered = {s.attr for s in BEAT_FIELD_SPECS}
        missing = sorted(declared - registered - set(RUNTIME_ONLY))
        assert not missing, (
            f'这些 Beat 字段没有登记进 story/beat_fields.py：{missing}\n'
            f'（登记它，或加进本文件的 RUNTIME_ONLY 并写明为什么它是运行时元数据）')
        # 反向：表里指向了不存在的属性 → 运行时 getattr 静默拿空
        bogus = sorted(registered - declared)
        assert not bogus, f'FieldSpec 指向了 Beat 上不存在的属性：{bogus}'

    def test_every_runtime_exemption_still_exists(self):
        """豁免表不许变成"历史免死金牌"：字段删了，豁免也要删。"""
        declared = {f.name for f in dataclasses.fields(Beat)}
        stale = sorted(set(RUNTIME_ONLY) - declared)
        assert not stale, f'RUNTIME_ONLY 里这些字段已经不存在了：{stale}'

    def test_every_persisted_column_is_in_snapshot(self):
        """进了 Excel / 协作 payload 的列，必须能撤销（否则留半新半旧）。"""
        missing = [s.name for s in COLUMN_SPECS
                   if s.attr not in SNAPSHOT_FIELDS
                   and s.attr not in RUNTIME_ONLY]
        assert not missing, f'这些列不在撤销快照里：{missing}'

    def test_every_content_column_is_in_fingerprint(self):
        """
        进产物 / 进表的列必须进 `content_hash`，否则"改了却判没变"：
        撤销按钮不亮、协作 push 判 `unchanged`、改动静默不落库。
        """
        missing = [s.name for s in COLUMN_SPECS
                   if s.attr not in FINGERPRINT_FIELDS
                   and s.name not in FINGERPRINT_EXEMPT]
        assert not missing, f'这些列不在内容指纹里：{missing}'
        # 豁免必须写了理由（空 note = 下一个维护者不知道能不能删）
        by_name = {s.name: s for s in COLUMN_SPECS}
        for name in FINGERPRINT_EXEMPT:
            assert by_name[name].note.strip(), f'{name} 豁免了却没写理由'

    def test_every_beat_field_is_snapshotted_or_exempt(self):
        """`Beat` 上除运行时元数据外的一切，撤销都要能还原。"""
        declared = {f.name for f in dataclasses.fields(Beat)}
        missing = sorted(declared - set(SNAPSHOT_FIELDS) - set(RUNTIME_ONLY))
        assert not missing, f'这些字段不在撤销快照里：{missing}'


class TestFingerprintActuallyCovers:

    @pytest.mark.parametrize('attr', FINGERPRINT_FIELDS)
    def test_changing_any_fingerprint_field_changes_the_hash(self, attr):
        """
        **变异验证**：逐个字段改成不同值，指纹必须变。

        这条是"漏字段"的直接判据 —— 从前它会对 `spec_head` /
        `loop_header` / `camera_header` / `opaque` / `nest_path` /
        `code_blocks` / `inline_comment` / `cond_comment` / `file_blocks`
        九条同时红（正是一次真实审阅抓到的）。
        """
        base = Beat()
        before = _beat_content_hash(base)
        changed = Beat()
        setattr(changed, attr, _sentinel(attr))
        assert _beat_content_hash(changed) != before, (
            f'{attr} 改了，Beat.content_hash 却没变 —— 协作 push 会判 unchanged')

    def test_runtime_metadata_does_not_change_the_hash(self):
        """运行时元数据不许影响内容身份（否则每次扫描都"内容变了"）。"""
        base = Beat()
        before = _beat_content_hash(base)
        base.src_first, base.src_last = 10, 20
        base._from_script = True
        base.sanitized_cells = ['Show']
        base.runtime_notes = ['导入时按 Seq 重排']
        assert _beat_content_hash(base) == before


class TestSnapshotActuallyRestores:

    def test_every_snapshot_field_roundtrips(self):
        """
        逐个字段改坏 → `restore` 必须把它们全部还原（含列表深拷贝）。

        `stage_order` 是**派生列**（`snapshot()` 存的是归一化形式），所以
        比对基准是快照里实际存下的值，不是还原前的原值。
        """
        from storygraph.app.editor import EditorSession

        session = EditorSession()
        session.beats = [Beat()]
        snap = session.snapshot(0)
        expected = copy.deepcopy(snap.data)
        beat = session.beats[0]
        for attr in SNAPSHOT_FIELDS:
            if attr in ('ordered', 'code_blocks'):
                setattr(beat, attr, [('dirty', 'x', 0)])
            elif attr == 'nest_path':
                beat.nest_path = 'if:9>Z'
            else:
                setattr(beat, attr, 'MUTATED')
        session.restore(snap)
        for attr in SNAPSHOT_FIELDS:
            assert getattr(beat, attr) == expected[attr], (
                f'{attr} 没被撤销还原：{getattr(beat, attr)!r}')

    def test_list_fields_are_not_aliased_between_snapshot_and_beat(self):
        """
        快照与拍不许共享同一个可变列表：拍上原地 append 一次，快照不能跟着变。
        （从前 `ordered` 单独 deepcopy，`code_blocks` 没有 —— 撤销撤不回去。）
        """
        from storygraph.app.editor import EditorSession

        session = EditorSession()
        beat = Beat()
        beat.code_blocks = [(1, 2)]
        session.beats = [beat]
        snap = session.snapshot(0)
        beat.code_blocks.append((3, 4))
        session.restore(snap)
        assert beat.code_blocks == [(1, 2)], beat.code_blocks
