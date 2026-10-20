"""
tests/test_ir_validate.py
=========================
**中立 IR 自检**（2026-10-20，§20）。

§18 的损失报告回答"目标引擎少拿了什么"；这个文件守的是另一半：
**交出去的这一份本身有没有自相矛盾** —— 重复 `line_id`、`stats` 与内容
不符、`stage[]` 指向不存在的行、`timeline[].order` 错位、
`structure.known` 与 `path` 互相打脸、Unity 产物里混进嵌套字典。

两条不变量：

  ① 干净 IR 必须**零错误零警告**（否则检查在制造噪音，没人会看）；
  ② 每一条坏数据都有对应 `E-IR-*` / `E-AD-*`，且报告里写得出来。

另外守一条漂移：`KNOWN_SCHEMA_VERSIONS` 必须包含
`engine_exporters.SCHEMA_VERSION` —— 加了 IR 版本却忘了自检会红。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.pipeline.engine_exporters import (            # noqa: E402
    SCHEMA_VERSION, build_neutral_ir, expected_files, export_ir,
    to_unity)
from storygraph.pipeline.ir_validate import (                 # noqa: E402
    CHECK_REPORT_NAME, CRASH_CODE, KNOWN_SCHEMA_VERSIONS, LOW_CONFIDENCE,
    check_adapter, check_ir, crash_report, report_markdown, summarize,
    write_report)
from storygraph.story.beat_model import Beat, assign_identity  # noqa: E402


def _line(**kw):
    base = {
        'line_id': 'a.0001', 'label': 'a', 'seq': 1, 'kind': 'say',
        'speaker': '', 'speaker_name': '', 'text': 'hello',
        'condition': '', 'scene': '', 'actors': [],
        'music': '', 'music_action': '', 'sound': '', 'sound_action': '',
        'voice': '', 'with': '', 'nest': '', 'line_hash': '',
        'structure': {'path': '', 'kind': '', 'known': True},
        'timeline': [], 'targets': [],
    }
    base.update(kw)
    return base


def _stats(lines, characters):
    return {
        'lines': len(lines),
        'labels': len({l.get('label') for l in lines if l.get('label')}),
        'characters': len(characters),
        'spoken_lines': sum(1 for l in lines if l.get('text')),
        'chars': sum(len(l.get('text') or '') for l in lines),
    }


def _ir(lines=None, characters=None, assets=None, stage=None, stats=None,
        schema=f'renpy-story-ir/{SCHEMA_VERSION}'):
    lines = [_line()] if lines is None else lines
    characters = [] if characters is None else characters
    assets = {} if assets is None else assets
    if stage is None:
        stage = [{'line_id': l.get('line_id'), 'scene': '', 'actors': [],
                  'music': ''} for l in lines if isinstance(l, dict)]
    return {
        'schema': schema,
        'project': 'demo', 'title': 'demo',
        'characters': characters,
        'lines': lines,
        'assets': assets,
        'stage': stage,
        'stats': _stats(lines, characters) if stats is None else stats,
    }


def _codes(issues):
    return {i.code for i in issues}


def _by_code(issues, code):
    rows = [i for i in issues if i.code == code]
    return rows[0] if rows else None


class TestCleanIr:
    def test_clean_ir_has_no_issues(self):
        assert check_ir(_ir()) == []

    def test_clean_ir_with_everything_still_clean(self):
        ir = _ir(
            lines=[_line(label='a', speaker='e',
                         text='你好',
                         actors=['eileen happy'],
                         structure={'path': 'menu:1>B', 'kind': 'menu',
                                    'known': True},
                         timeline=[{'op': 'show', 'target': 'eileen',
                                    'order': 0}])],
            characters=[{'id': 'e', 'name': 'Eileen'}],
            assets={'images': [{'id': 'i1', 'tag': 'eileen',
                                'attributes': {'outfit': 'school'},
                                'attribute_meta': [
                                    {'group': 'outfit', 'value': 'school',
                                     'source': 'explicit',
                                     'confidence': 1.0}]}]})
        assert check_ir(ir) == []


class TestSchema:
    def test_missing_schema_is_an_error(self):
        issues = check_ir(_ir(schema=''))
        assert 'E-IR-SCHEMA' in _codes(issues)

    def test_unknown_schema_version_is_a_warning(self):
        issues = check_ir(_ir(schema='renpy-story-ir/9.9'))
        assert issues and all(i.level == 'warning' for i in issues)
        assert 'W-IR-SCHEMA-VERSION' in _codes(issues)

    def test_schema_version_table_stays_in_sync(self):
        assert SCHEMA_VERSION in KNOWN_SCHEMA_VERSIONS, (
            'IR 版本改了却没加进 KNOWN_SCHEMA_VERSIONS —— 自检会把新数据'
            '当成旧格式')


class TestLines:
    def test_empty_lines_is_an_error(self):
        issues = check_ir(_ir(lines=[], stage=[]))
        assert 'E-IR-EMPTY' in _codes(issues)

    def test_duplicate_line_id_is_an_error(self):
        ir = _ir(lines=[_line(), _line(text='again')])
        issues = check_ir(ir)
        row = _by_code(issues, 'E-IR-ID-DUP')
        assert row is not None and row.count == 2
        assert row.as_dict()['examples'] == ['a.0001']

    def test_blank_line_id_is_an_error(self):
        ir = _ir(lines=[_line(line_id='')], stage=[])
        assert 'E-IR-ID-EMPTY' in _codes(check_ir(ir))

    def test_missing_required_keys_is_an_error(self):
        broken = _line()
        del broken['timeline']
        issues = check_ir(_ir(lines=[broken]))
        row = _by_code(issues, 'E-IR-LINE-KEYS')
        assert row is not None and 'timeline' in row.examples

    def test_non_integer_seq_is_a_warning(self):
        ir = _ir(lines=[_line(seq='1')])
        assert 'W-IR-SEQ' in _codes(check_ir(ir))


class TestStats:
    def test_stats_mismatch_names_the_field(self):
        bad = _stats([_line()], [])
        bad['lines'] = 99
        issues = check_ir(_ir(stats=bad))
        row = _by_code(issues, 'E-IR-STATS')
        assert row is not None
        assert any('lines' in e and '99' in e for e in row.examples)

    def test_missing_stats_is_an_error(self):
        ir = _ir()
        del ir['stats']
        assert 'E-IR-STATS' in _codes(check_ir(ir))


class TestStage:
    def test_stage_pointing_at_a_missing_line_is_an_error(self):
        ir = _ir(stage=[{'line_id': 'a.9999', 'scene': '', 'actors': [],
                         'music': ''}])
        assert 'E-IR-STAGE-ORPHAN' in _codes(check_ir(ir))

    def test_duplicate_stage_snapshot_is_an_error(self):
        row = {'line_id': 'a.0001', 'scene': '', 'actors': [], 'music': ''}
        ir = _ir(stage=[dict(row), dict(row)])
        assert 'E-IR-STAGE-DUP' in _codes(check_ir(ir))

    def test_missing_snapshot_is_only_a_warning(self):
        ir = _ir(stage=[])
        issues = check_ir(ir)
        assert not [i for i in issues if i.level == 'error']
        assert 'W-IR-STAGE-MISSING' in _codes(issues)


class TestStructureAndTimeline:
    def test_known_false_with_path_is_an_error(self):
        ir = _ir(lines=[_line(structure={'path': 'menu:1>B',
                                        'kind': 'menu',
                                        'known': False})])
        assert 'E-IR-STRUCTURE' in _codes(check_ir(ir))

    def test_known_false_without_path_is_a_warning(self):
        ir = _ir(lines=[_line(structure={'path': '', 'kind': '',
                                        'known': False})])
        issues = check_ir(ir)
        assert not [i for i in issues if i.level == 'error']
        assert 'W-IR-STRUCTURE-UNKNOWN' in _codes(issues)

    def test_timeline_order_must_be_zero_based(self):
        ir = _ir(lines=[_line(timeline=[{'op': 'show', 'order': 1}])])
        assert 'E-IR-TIMELINE-ORDER' in _codes(check_ir(ir))

    def test_timeline_event_must_be_scalar(self):
        ir = _ir(lines=[_line(timeline=[{'op': 'show', 'order': 0,
                                        'target': {'tag': 'e'}}])])
        assert check_ir(ir), '嵌套值应该被自检抓到'
        assert 'E-IR-TIMELINE-VALUE' in _codes(check_ir(ir))


class TestActorsAndSpeaker:
    def test_joined_actors_is_an_error(self):
        ir = _ir(lines=[_line(actors=['eileen happy; bg room'])])
        assert 'E-IR-ACTOR-JOINED' in _codes(check_ir(ir))

    def test_actor_not_in_assets_is_a_warning(self):
        ir = _ir(lines=[_line(actors=['eileen happy'])],
                 assets={'images': [{'id': 'bg1', 'tag': 'bg'}]})
        issues = check_ir(ir)
        assert not [i for i in issues if i.level == 'error']
        assert 'W-IR-ACTOR-UNKNOWN' in _codes(issues)

    def test_unknown_speaker_warns_only_when_characters_are_known(self):
        ir = _ir(lines=[_line(speaker='ghost')])
        assert 'W-IR-SPEAKER-UNKNOWN' not in _codes(check_ir(ir))
        ir = _ir(lines=[_line(speaker='ghost')],
                 characters=[{'id': 'e', 'name': 'Eileen'}])
        assert 'W-IR-SPEAKER-UNKNOWN' in _codes(check_ir(ir))

    def test_renpy_markup_is_a_warning_not_an_error(self):
        ir = _ir(lines=[_line(text='hi {w} [name]')])
        issues = check_ir(ir)
        assert not [i for i in issues if i.level == 'error']
        assert 'W-IR-MARKUP' in _codes(issues)


class TestAssets:
    def test_duplicate_asset_id_is_an_error(self):
        ir = _ir(assets={'images': [{'id': 'x'}, {'id': 'x'}]})
        assert 'E-IR-ASSET-DUP' in _codes(check_ir(ir))

    def test_attributes_without_meta_is_a_warning(self):
        ir = _ir(assets={'images': [{'id': 'i1',
                                     'attributes': {'outfit': 'school'}}]})
        issues = check_ir(ir)
        assert not [i for i in issues if i.level == 'error']
        assert 'W-IR-DIM-NO-META' in _codes(issues)

    def test_guessed_dimensions_are_called_out(self):
        ir = _ir(assets={'images': [{
            'id': 'i1',
            'attributes': {'outfit': 'school'},
            'attribute_meta': [{'group': 'outfit', 'value': 'school',
                                'source': 'unknown',
                                'confidence': LOW_CONFIDENCE - 0.5}]}]})
        assert 'W-IR-DIM-GUESSED' in _codes(check_ir(ir))


class TestAdapterConstraints:
    def test_unity_nested_dictionary_is_caught(self):
        payload = {'lines': [{'lineId': 'a.0001', 'speakerId': '',
                              'text': '',
                              'stage': {'background': 'bg'}}]}
        issues = check_adapter('unity', payload)
        row = _by_code(issues, 'E-AD-UNITY-NESTED')
        assert row is not None
        assert row.examples == ['line[0].stage']

    def test_unity_missing_keys_is_caught(self):
        issues = check_adapter('unity', {'lines': [{'text': 'x'}]})
        assert 'E-AD-UNITY-KEYS' in _codes(issues)

    def test_real_unity_payload_is_flat(self):
        beats = [_beat()]
        assign_identity(beats)
        ir = build_neutral_ir(beats, project='demo')
        assert check_adapter('unity', to_unity(ir)) == [], (
            'to_unity() 写出了 JsonUtility 吃不下（或缺键）的数据')

    def test_other_engines_have_no_adapter_rules(self):
        assert check_adapter('godot', {'anything': {'nested': 1}}) == []


class TestReportAndExport:
    def test_report_markdown_lists_counts_and_codes(self):
        ir = _ir(lines=[_line(), _line(text='again')])
        text = report_markdown(ir, 'unity')
        assert text.startswith('# 中立 IR 自检 · unity')
        assert '- 行：2，资源：0' in text
        assert 'E-IR-ID-DUP' in text
        assert '## 错误' in text and '## 自查了什么' in text

    def test_report_survives_malformed_ir(self):
        text = report_markdown({'lines': 'nope', 'assets': ['x']}, 'json')
        assert '行：0，资源：0' in text
        assert 'E-IR-LINES-SHAPE' in text

    def test_write_report_creates_the_file(self, tmp_path):
        path = write_report(_ir(), tmp_path, 'json')
        assert Path(path).name == CHECK_REPORT_NAME
        assert 'IR 自检' in checkbox_text(Path(path))

    def test_summarize_counts_and_groups_by_code(self):
        ir = _ir(lines=[_line(), _line(text='again')])
        summary = summarize(ir, 'unity')
        assert summary['error_count'] >= 2
        assert summary['by_code']['E-IR-ID-DUP'] == 2
        assert summary['issues'][0]['level'] == 'error'

    def test_export_writes_the_check_report_for_every_engine(self, tmp_path):
        beats = [_beat()]
        assign_identity(beats)
        for engine in ('renpy', 'unity', 'godot', 'json', 'csv'):
            out = tmp_path / f'chk_{engine}'
            files = export_ir(engine, beats, out, project='demo')
            names = {Path(f).name for f in files}
            assert names == set(expected_files(engine)), names
            assert CHECK_REPORT_NAME in names
            assert (out / CHECK_REPORT_NAME).is_file()

    def test_exported_real_ir_has_no_self_check_errors(self, tmp_path):
        beats = [_beat(), _beat(seq=2, text='第二句')]
        assign_identity(beats)
        ir = build_neutral_ir(beats, project='demo')
        issues = check_ir(ir)
        errors = [i for i in issues if i.level == 'error']
        assert errors == [], [i.as_dict() for i in errors]
        summary = summarize(ir)
        assert summary['error_count'] == 0


class TestBadShapesDoNotCrashTheChecker:
    """
    坏 IR 是自检**要诊断的对象**，不许成为自检的崩溃点（2026-10-20 修，P1）。

    用户实测：`report_markdown({'lines': 'nope'}, 'unity')` 抛
    `TypeError: string indices must be integers`；`{'assets': ['x']}` 抛
    `AttributeError: 'list' object has no attribute 'values'`。它俩都在
    `export_ir` 的写盘流程里 —— 于是坏 IR 让整次导出失败，`IR_CHECK.md`
    根本落不下来，而这份报告正是"告诉用户哪里坏了"的唯一出口。
    """

    BAD = [
        ('lines 是字符串', {'lines': 'nope'}),
        ('assets 是数组', {'assets': ['x']}),
        ('lines 里混了字符串', {'lines': ['x', {'line_id': 'a.0001'}]}),
        ('characters 不是数组', {'characters': 'nope', 'lines': []}),
        ('stage 不是数组', {'lines': [], 'stage': 'nope'}),
        ('structure 不是对象', {'lines': [{'line_id': 'a', 'structure': 'x'}]}),
        ('整个 IR 是数组', [1, 2, 3]),
    ]

    def test_report_is_written_for_every_bad_shape(self):
        for name, ir in self.BAD:
            for engine in ('', 'unity', 'godot'):
                text = report_markdown(ir, engine)
                assert '自检' in text, f'{name}/{engine}：报告是空的'

    def test_summarize_never_says_clean_on_bad_shapes(self):
        for name, ir in self.BAD:
            for engine in ('unity', 'godot'):
                assert summarize(ir, engine)['error_count'] >= 1, (
                    f'{name}/{engine}：坏数据竟然"零错误"')

    def test_bad_shape_is_named_in_the_report(self):
        assert 'E-IR-LINES-SHAPE' in report_markdown({'lines': 'nope'}, 'unity')

    def test_export_survives_a_bad_ir_already_on_disk(self, tmp_path):
        """目录里已经躺着一份坏 IR → 导出照样跑完，报告照样写出来。"""
        beats = [_beat()]
        assign_identity(beats)
        out = tmp_path / 'bad_on_disk'
        out.mkdir()
        (out / 'story_ir.json').write_text('{"lines": "nope"}',
                                           encoding='utf-8')
        files = export_ir('unity', beats, out, project='demo')
        assert CHECK_REPORT_NAME in {Path(f).name for f in files}
        assert (out / CHECK_REPORT_NAME).is_file()

    def test_adapter_crash_becomes_a_reported_error(self, monkeypatch):
        """自检的适配分支崩了 → 记一条 `E-IR-CHECK-CRASH`，不是静默 0 错。"""
        import storygraph.pipeline.engine_exporters as EE

        def boom(_ir):
            raise RuntimeError('适配器没跑成')

        monkeypatch.setattr(EE, 'to_unity', boom)
        summary = summarize(_ir(), 'unity')
        assert 'E-IR-CHECK-CRASH' in summary['by_code'], summary['by_code']
        assert summary['error_count'] >= 1

    def test_crash_report_says_the_check_did_not_run(self):
        text = crash_report('unity', RuntimeError('内部错误'))
        assert CRASH_CODE in text
        assert '内部错误' in text
        assert '没跑成' in text, '报告必须先说清"这不是零问题"'


class TestStageCoverage:
    """
    覆盖率只数**真对上**的拍（2026-10-20 修，P3）。

    从前数的是 `len(seen)`，而孤儿 id 也被 `seen.add` 了：
    "2 拍 + 1 合法快照 + 1 孤儿"会算出 `len(seen) == len(line_ids)`，
    于是**漏报**"有拍没快照"。两条结论互相抵消，等于没有这条检查。
    """

    def _stage(self, *ids):
        return [{'line_id': i, 'scene': '', 'actors': [], 'music': ''}
                for i in ids]

    def test_orphan_does_not_count_as_coverage(self):
        lines = [_line(line_id='a.0001'), _line(line_id='a.0002', seq=2)]
        ir = _ir(lines=lines, stage=self._stage('a.0001', 'a.9999'))
        codes = {i.code for i in check_ir(ir)}
        assert 'E-IR-STAGE-ORPHAN' in codes
        assert 'W-IR-STAGE-MISSING' in codes, (
            '孤儿被算成"已覆盖"，覆盖率不足没报出来')

    def test_full_coverage_is_silent(self):
        lines = [_line(line_id='a.0001'), _line(line_id='a.0002', seq=2)]
        ir = _ir(lines=lines, stage=self._stage('a.0001', 'a.0002'))
        codes = {i.code for i in check_ir(ir)}
        assert 'W-IR-STAGE-MISSING' not in codes
        assert 'E-IR-STAGE-ORPHAN' not in codes

    def test_missing_ids_are_named(self):
        lines = [_line(line_id='a.0001'), _line(line_id='a.0002', seq=2)]
        ir = _ir(lines=lines, stage=self._stage('a.0001'))
        hits = [i for i in check_ir(ir) if i.code == 'W-IR-STAGE-MISSING']
        assert hits and 'a.0002' in hits[0].examples[0], hits[0].as_dict()


class TestDimensionConflicts:
    def test_conflicting_columns_are_not_called_guessed(self):
        """`Attributes` 与 `Value` 打架 → 单独一条代码，不混进"猜的"。"""
        assets = {'images': [{
            'id': 'ling_happy',
            'attributes': {'expressions': 'happy'},
            'attribute_values': ['angry'],
            'attribute_meta': [
                {'group': 'expressions', 'value': 'happy',
                 'source': 'value', 'confidence': 0.95},
                {'group': 'expressions', 'value': 'angry',
                 'source': 'conflict', 'confidence': 0.5}]}]}
        codes = {i.code for i in check_ir(_ir(assets=assets))}
        assert 'W-IR-DIM-CONFLICT' in codes
        assert 'W-IR-DIM-GUESSED' not in codes


def checkbox_text(path: Path) -> str:
    """报告文件里一定有的那行字（读文件时别把整篇贴进断言）。"""
    for line in path.read_text(encoding='utf-8').splitlines():
        if '自检' in line:
            return line.strip()
    return ''


def _beat(**kw):
    base = dict(seq=1, label='a', kind='say', speaker='e', text='hi',
                scene='bg room', show='eileen happy@left',
                with_='dissolve', music='bgm_night',
                music_action='play')
    base.update(kw)
    return Beat(**base)
