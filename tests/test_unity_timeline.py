"""
判据 · 跨引擎第一片：**结构上下文 + 演出时间轴**（2026-10-19）
=============================================================

用户排期把多引擎往后放，但目标里追加了"开始加入跨 Unity 引擎的功能"。
这一片只补两个**导出必需**的中立字段，不写播放器：

  ① `structure`：`{path, kind, known}` —— 别的引擎拿到 `menu:1>B`
     能知道它是"选项体里的拍"，而不是一列看不懂的字符串；
  ② `timeline`：按作者顺序摊平的舞台/音频/转场操作
     `[{op,target,at,clause,transition,action,params,order}]`。

Unity 适配层把它们摊成标量：
`structurePath` / `structureKind` / `structureKnown` + `timeline[]`，
**不引入嵌套字典**（JsonUtility 的既有约束）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.pipeline.engine_exporters import (            # noqa: E402
    build_neutral_ir, structure_of, timeline_of, to_godot, to_unity)
from storygraph.story.beat_model import (                     # noqa: E402
    Beat, assign_identity)


def test_structure_says_which_branch_and_whether_it_is_known():
    body = Beat(seq=1, label='a', kind='say', text='x',
                nest_path='if:1>menu:1>B')
    assert structure_of(body) == {'path': 'if:1>menu:1>B', 'kind': 'option',
                                  'known': True}
    top = Beat(seq=2, label='a', text='y', nest_path='')
    assert structure_of(top)['kind'] == ''
    assert structure_of(top)['known'] is True
    old = Beat(seq=3, label='a', text='z', nest_path=None)
    assert structure_of(old)['known'] is False, '老表不知道 ≠ 顶层'


def test_timeline_lists_scene_show_transition_in_order():
    b = Beat(seq=1, label='a', text='hi', scene='bg room',
             scene_at='t', show='eileen happy@left', with_='dissolve')
    ops = [(e['op'], e['target']) for e in timeline_of(b)]
    assert ops[:3] == [('scene', 'bg room'),
                       ('show', 'eileen happy'),
                       ('with', 'dissolve')], ops
    show = next(e for e in timeline_of(b) if e['op'] == 'show')
    assert show['at'] == 'left'
    assert show['transition'] == ''
    # `order` 是稳定的整数，播放器按它排序
    assert [e['order'] for e in timeline_of(b)] == list(
        range(len(timeline_of(b))))


def test_timeline_keeps_audio_and_voice():
    b = Beat(seq=1, label='a', text='', music='bgm_night',
             music_action='play', music_params='fadein 1.0',
             sound='door', sound_action='play', voice='v001')
    ops = [e['op'] for e in timeline_of(b)]
    assert 'music' in ops and 'sound' in ops and 'voice' in ops, ops
    music = next(e for e in timeline_of(b) if e['op'] == 'music')
    assert music['action'] == 'play'
    assert 'fadein' in music['params']


def test_timeline_is_in_the_neutral_ir_and_unity_is_flat():
    beats = [Beat(seq=1, label='a', text='hi', scene='bg room',
                  show='eileen happy', with_='dissolve', nest_path='')]
    assign_identity(beats)
    ir = build_neutral_ir(beats)
    line = ir['lines'][0]
    assert line['structure']['path'] == ''
    assert [e['op'] for e in line['timeline']] == ['scene', 'show', 'with']

    u = to_unity(ir)
    uline = u['lines'][0]
    assert uline['structurePath'] == ''
    assert uline['structureKnown'] is True
    assert uline['transition'] == 'dissolve'
    assert uline['timeline'][0]['op'] == 'scene'
    # 资产数组仍然只能是标量（既有契约）
    for a in u['assets']:
        assert not any(isinstance(v, dict) for v in a.values())

    g = to_godot(ir)
    assert g['lines'][0]['structure']['path'] == ''
    assert g['lines'][0]['timeline'][0]['op'] == 'scene'


def test_parsed_menu_body_gets_option_structure_and_timeline(tmp_path):
    """从真源解析：选项体里的拍带 `option` 结构，舞台操作进 timeline。"""
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result

    src = ('label a:\n'
           '    menu:\n'
           '        "选项A":\n'
           '            show eileen happy at left\n'
           '            e "选了"\n')
    f = tmp_path / 'ch.rpy'
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    body = next(b for b in beats if b.text == '选了')
    assert structure_of(body) == {'path': 'menu:1>A', 'kind': 'option',
                                  'known': True}, structure_of(body)
    show = next(e for e in timeline_of(body) if e['op'] == 'show')
    assert show['target'] == 'eileen happy'
    assert show['at'] == 'left'
