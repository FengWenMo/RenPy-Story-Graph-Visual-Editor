"""
tests/test_ir_loss.py
=====================
**多引擎导出的"损失报告"**（2026-10-20，§18）。

要回答的问题是：一份 `story_ir.json` 里有 30 个行级字段，Unity/Godot 的
适配层到底带走了多少？没带走的（DSL 源码、原始行、策划备注、立绘站位、
BGM 参数……）**必须在报告里点名**，否则用户会以为两边看到的一样。

两条不变量（本文件守着）：

  ① 适配器带走的字段 → 报告里不许出现；
  ② 适配器没带走的字段 → 报告里必须出现，而且例子/数量对得上。

第 ① 条是"漂移守卫"：将来给 `to_unity()` 加字段却忘了改映射表，
`test_每_个非空字段要么被搬走要么被报告` 会红 —— 报告和适配器是同一份事实。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.pipeline.engine_exporters import (            # noqa: E402
    build_neutral_ir, expected_files, export_ir, to_godot, to_unity)
from storygraph.pipeline.ir_loss import (                     # noqa: E402
    LINE_FIELD_MAP, field_losses, report_markdown, summarize, write_report)
from storygraph.story.beat_model import Beat, assign_identity  # noqa: E402


def _beat(**kw):
    """一条"什么字段都有"的拍（缺省值可按需覆盖）。"""
    base = dict(seq=1, label='a', kind='say', speaker='e',
                text='hi {w} there', scene='bg room',
                show='eileen happy@left', with_='dissolve',
                music='bgm_night', music_action='play',
                music_params='fadein 1.0', sound='door',
                sound_action='play', sound_params='volume 0.5',
                voice='v001', comment='策划备注', raw='e "hi {w} there"',
                code='e "hi"', at='left', transform='t_slide',
                stage='show eileen happy at left')
    base.update(kw)
    return Beat(**base)


def _ir(*beats):
    beats = list(beats) or [_beat()]
    assign_identity(beats)
    return build_neutral_ir(beats, project='demo')


def _lost(ir, engine, scope='line'):
    return {l.field: l for l in field_losses(ir, engine) if l.scope == scope}


class TestUnityLosses:
    def test_reports_fields_the_adapter_does_not_carry(self):
        lost = _lost(_ir(), 'unity')
        for field in ('code', 'raw', 'comment', 'at', 'transform',
                      'music_params', 'sound_params', 'stage'):
            assert field in lost, f'{field} 没搬也没报'
        assert lost['code'].count == 1
        assert lost['code'].examples == ['a.0001']

    def test_does_not_report_what_the_adapter_carries(self):
        """`structure` 被摊成三个标量，仍然是带过去了 —— 不该报。"""
        lost = _lost(_ir(), 'unity')
        for field in ('line_id', 'text', 'speaker', 'scene', 'actors',
                      'music', 'sound', 'voice', 'with', 'timeline',
                      'targets', 'structure'):
            assert field not in lost, f'{field} 明明带过去了却报成损失'

    def test_asset_dimensions_are_reported_for_unity(self):
        """
        资源维度（§16.1 的结构化成果）Unity 适配层没搬 → 必须点名。

        `_unity_assets` 只留 id/type/path/tag/category/doc；`attributes`
        （`{outfit: base_swimsuit}`）和 `attribute_values` 都在中立 IR 里。
        """
        class Reg:
            images = {
                'e happy': {'Path': 'images/e_happy.png', 'Tag': 'e',
                            'Group': 'expressions', 'Value': 'happy',
                            'Doc': '笑脸'},
            }
            music = {}
            sounds = {}
            voices = {}
            transforms = {}

        ir = _ir()
        ir['assets'] = {
            'images': [
                {'id': 'e happy', 'type': 'image',
                 'path': 'images/e_happy.png', 'tag': 'e',
                 'attributes': {'expressions': 'happy'},
                 'attribute_values': ['happy'],
                 'group': 'expressions', 'value': 'happy', 'doc': '笑脸'},
            ],
        }
        lost = _lost(ir, 'unity', scope='asset')
        assert 'attributes' in lost and 'attribute_values' in lost
        assert lost['attributes'].examples == ['e happy']
        # Godot 原样带 assets，不该报
        assert 'attributes' not in _lost(ir, 'godot', scope='asset')

    def test_passthrough_engines_have_no_losses(self):
        ir = _ir()
        for engine in ('renpy', 'json', 'csv'):
            assert field_losses(ir, engine) == []


class TestGodotLosses:
    def test_godot_keeps_structure_but_loses_the_same_code_fields(self):
        lost = _lost(_ir(), 'godot')
        for field in ('code', 'raw', 'comment', 'at', 'transform',
                      'music_params', 'sound_params'):
            assert field in lost, f'{field} 没搬也没报'
        # Godot 保留完整 structure / stage 嵌套
        for field in ('structure', 'scene', 'actors', 'music', 'sound',
                      'sound_action', 'voice'):
            assert field not in lost, f'{field} 明明在 target 里有'

    def test_godot_stage_carries_sfx_action(self):
        ir = _ir()
        line = to_godot(ir)['lines'][0]
        assert line['stage']['sfx_action'] == 'play', line['stage']


class TestDriftGuard:
    """
    映射表 ↔ 适配器的对账（这一条才是"报告可信"的根据）。
    """

    def test_every_nonblank_field_is_either_carried_or_reported(self):
        ir = _ir()
        source = ir['lines'][0]
        target = to_unity(ir)['lines'][0]
        lost = {l.field for l in field_losses(ir, 'unity')
                if l.scope == 'line'}
        mapping = LINE_FIELD_MAP['unity']
        for field_name, value in source.items():
            if not value:
                continue
            if field_name in lost:
                continue                      # 报告的，合理
            want = mapping.get(field_name)
            assert want, f'{field_name} 非空、没报损失、映射表也没写'
            keys = (want,) if isinstance(want, str) else tuple(want)
            for key in keys:
                assert key in target, (
                    f'{field_name} 映射到 {key}，但 Unity 产物里没有 —— '
                    '适配器改了却忘了同步 LINE_FIELD_MAP')

    def test_the_map_covers_every_field_the_ir_can_produce(self):
        """IR 里能出现的行级字段，映射表都要有明确态度（搬 / 不搬）。"""
        ir = _ir(_beat(kind='python', text=''))
        seen = set()
        for line in ir['lines']:
            seen.update(k for k, v in line.items() if v)
        missing = sorted(seen - set(LINE_FIELD_MAP['unity']))
        assert not missing, f'这些字段映射表没表态：{missing}'


class TestReport:
    def test_markdown_names_fields_counts_and_examples(self):
        text = report_markdown(_ir(), 'unity')
        assert 'IR 损失报告 · unity' in text
        assert '`code`' in text and 'DSL 源码' in text
        assert '| 字段 | 含义 | 数量 | 例子 |' in text
        assert 'a.0001' in text

    def test_notes_mention_tags_and_unknown_structure(self):
        beats = [_beat(nest_path=None)]      # 老表：不知道结构位
        ir = _ir(*beats)
        notes = ' '.join(summarize(ir, 'unity')['notes'])
        assert 'Ren\'Py 标签' in notes
        assert '嵌套位置未知' in notes

    def test_passthrough_report_says_no_adapter(self):
        text = report_markdown(_ir(), 'json')
        assert '没有适配层' in text

    def test_write_report_creates_the_file(self, tmp_path):
        path = write_report(_ir(), 'unity', tmp_path)
        assert path and Path(path).is_file()
        assert Path(path).name == 'IR_LOSS-unity.md'


class TestExportWritesTheReport:
    def test_unity_and_godot_exports_include_it(self, tmp_path):
        beats = [_beat()]
        assign_identity(beats)
        for engine in ('unity', 'godot'):
            out = tmp_path / engine
            files = export_ir(engine, beats, out, project='demo')
            names = {Path(f).name for f in files}
            assert names == set(expected_files(engine)), names
            report = out / f'IR_LOSS-{engine}.md'
            assert report.is_file(), f'{engine} 没写损失报告'
            body = report.read_text(encoding='utf-8')
            assert 'DSL 源码' in body
