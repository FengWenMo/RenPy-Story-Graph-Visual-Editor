"""
`camera`（3D 舞台）—— 把"工具认识的流程块"接住（T2 → T0b）
=========================================================

官方语法（`3dstage.html`）**带块**：`camera:` / `camera background:` 后面跟
一段 ATL 属性。所以它是流程块，`RENPY_FLOW_BLOCK_HEADS` 里一直有它 ——
但工具没建模：它落进"未建模块"通道（`raw_kind='known_block'`），报告里被
算成 **T2（只读）**，并进"**催我们建模**"那一桶。

这一层与循环块（D2）**同一档、同一套约定**（不发明第二套）：

  · 头行（层名）结构化、可编辑、可校验（隐藏列 `CameraHeader`）；
  · 块体（ATL 属性）逐字（`Opaque`）；
  · 等级 `T0b` —— **不记 T0**：ATL 属性没有被结构化编辑。

判据与 `tests/test_loop_model.py` 一一对应，外加两条这台机器特有的：

  · **不许误伤 `camera "台词"`**（说话人恰好叫 `camera`）—— Ren'Py 的
    `camera` 一定要带块，判据是"以冒号收尾"，没有尾冒号就不是它；
  · **层名要解出来**（`camera background:` → `background`），因为那是唯一
    的结构化语义位；解不出来就等于没建模。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import capability as cap
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.story.beat_render import render_beats_to_rpy

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')

SCRIPT = (
    'label a:\n'
    '    "第一句"\n'
    '    camera:\n'
    '        perspective True\n'
    '    camera background:\n'
    '        perspective True\n'
    '        xpos 0\n'
    '        linear 3.0 xpos 500\n'
    '    "第二句"\n'
    '    return\n'
)

#: 说话人**恰好叫** `camera` 的台词 —— 正则写宽了就会吃掉它
SAY_TRAP = (
    'label a:\n'
    '    camera "我不是相机"\n'
    '    return\n'
)


def _parse(tmp_path, src=SCRIPT, name='a.rpy'):
    f = tmp_path / name
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return f, pr


def _roundtrip(tmp_path, path, mutate=None):
    pr = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)[0]
    beats = beats_from_parse_result(pr)
    if mutate:
        mutate(beats)
    x = tmp_path / 'o.xlsx'
    export_beats_to_excel(beats, x)
    return compile_beats(import_beats_from_excel(x)).rpy_text


def _cameras(beats):
    return [b for b in beats if str(getattr(b, 'camera_header', '') or '')]


# ---------------------------------------------------------------------------
#  ① 认出来，而且**不误伤台词**
# ---------------------------------------------------------------------------

def test_camera_blocks_become_camera_statements(tmp_path):
    _f, pr = _parse(tmp_path)
    cams = [s for s in pr.statements if s.kind == 'camera']
    assert len(cams) == 2, [(s.kind, s.payload) for s in pr.statements]
    assert cams[0].payload['header'] == 'camera:'
    assert cams[0].payload['layer'] == ''            # 不给层名 = master
    assert cams[1].payload['header'] == 'camera background:'
    assert cams[1].payload['layer'] == 'background'
    # 块体逐字（含相对缩进），不是结构化字段
    assert any('perspective True' in ln for ln in cams[1].payload['body'])
    assert any('linear 3.0 xpos 500' in ln for ln in cams[1].payload['body'])


def test_say_with_speaker_named_camera_is_not_a_camera(tmp_path):
    """`camera "台词"` 必须**仍然**是台词（没有尾冒号就不是相机块）。"""
    _f, pr = _parse(tmp_path, SAY_TRAP)
    assert not [s for s in pr.statements if s.kind == 'camera'], (
        f'把台词误判成相机块了：{[(s.kind, s.payload) for s in pr.statements]}')
    says = [s for s in pr.statements if s.kind == 'say']
    assert [s.payload['speaker'] for s in says] == ['camera'], says
    assert [s.payload['text'] for s in says] == ['我不是相机'], says


def test_camera_with_extra_args_is_left_to_the_fidelity_channel(tmp_path):
    """
    `camera xpos 100:` 不是合法语法（层名后面直接冒号）→ **不许猜**。

    它落到未建模块通道（逐字保真）才对：猜成"层名 = xpos"会把别人的脚本
    改成一个跑不起来的相机块。
    """
    src = ('label a:\n'
           '    camera xpos 100:\n'
           '        perspective True\n')
    _f, pr = _parse(tmp_path, src)
    assert not [s for s in pr.statements if s.kind == 'camera'], (
        [(s.kind, s.payload) for s in pr.statements])


# ---------------------------------------------------------------------------
#  ② 字节不变
# ---------------------------------------------------------------------------

def test_camera_survives_roundtrip_verbatim(tmp_path):
    path, _pr = _parse(tmp_path)
    out = _roundtrip(tmp_path, path)
    for line in ('camera:', 'camera background:', 'perspective True',
                 'xpos 0', 'linear 3.0 xpos 500'):
        assert line in out, f'{line!r} 丢了：\n{out}'


def test_untouched_camera_output_equals_source(tmp_path):
    """没改 → 那几行与原文**逐字相同**（含缩进）。"""
    path, _pr = _parse(tmp_path)
    src_lines = path.read_text(encoding='utf-8').split('\n')
    out = _roundtrip(tmp_path, path).split('\n')
    for ln in src_lines:
        s = ln.strip()
        if s and not s.startswith('#') and s != 'return':
            assert ln in out, f'原文这一行在产物里对不上：{ln!r}'


def test_camera_render_is_idempotent(tmp_path):
    """逐字节不动点：`render(parse(t))` 再跑一轮必须一模一样。"""
    path, _pr = _parse(tmp_path)
    r1 = _roundtrip(tmp_path, path)
    f2 = tmp_path / 'r1.rpy'
    f2.write_text(r1, encoding='utf-8')
    r2 = _roundtrip(tmp_path, f2)
    assert r2 == r1, f'相机块每导出一次就变一次：\n--- R1 ---\n{r1}\n--- R2 ---\n{r2}'


def test_editing_camera_header_changes_only_that_line(tmp_path):
    """头行改了 → 生效；块体一个字不动（逐行比对，只允许一行不同）。"""
    path, _pr = _parse(tmp_path)
    baseline = _roundtrip(tmp_path, path)

    def mutate(beats):
        _cameras(beats)[1].camera_header = 'camera screens:'

    changed = _roundtrip(tmp_path, path, mutate=mutate)
    assert 'camera screens:' in changed, changed
    assert 'linear 3.0 xpos 500' in changed
    before, after = baseline.split('\n'), changed.split('\n')
    assert len(before) == len(after), (baseline, changed)
    diff = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(diff) == 1, [before[i] for i in diff]


def test_camera_header_survives_excel(tmp_path):
    path, pr = _parse(tmp_path)
    beats = beats_from_parse_result(pr)
    x = tmp_path / 'o.xlsx'
    export_beats_to_excel(beats, x)
    back = import_beats_from_excel(x)
    assert _cameras(back)[1].camera_header == 'camera background:'


# ---------------------------------------------------------------------------
#  ③ 能力等级：T0b，而且**不再报警**
# ---------------------------------------------------------------------------

def test_camera_is_t0b_not_t0(tmp_path):
    """
    **不许假装懂**：块体（ATL 属性）没结构化，就不能记成 T0。

    这条判据是这一层的良心 —— 记成 T0 会让覆盖率好看，也会让
    "工具完全理解它"变成一句假话（与循环块 D2 同一条线）。
    """
    from storygraph.story.statement_registry import coverage_of_beats
    _path, pr = _parse(tmp_path)
    beats = beats_from_parse_result(pr)
    cov = coverage_of_beats(beats)
    assert cov.count_of(cap.T0B) == 2, cov.counts
    assert cov.count_of(cap.T2) == 0, cov.counts
    assert cap.T0B in cap.EDITABLE_LEVELS
    assert cov.editable + cov.readonly == cov.total


def test_camera_no_longer_reported_as_unmanaged(tmp_path):
    """建模之后就不该再报"给工具作者的待办"（欠账还了）。"""
    from storygraph.story.generation_guard import evaluate_generation
    path, pr = _parse(tmp_path)
    beats = beats_from_parse_result(pr)
    text = render_beats_to_rpy(beats, label_name='a')
    codes = [i.code for i in evaluate_generation(
        text, tmp_path / 'o.rpy', beats=beats)]
    assert 'KNOWN_UNMODELED_BLOCK' not in codes, codes
    assert 'UNMANAGED_BLOCK' not in codes, codes


def test_camera_is_not_in_the_unmodeled_bucket(tmp_path):
    """解析结果里也不该再出现"未建模"的 camera（否则报告还是那套口径）。"""
    _f, pr = _parse(tmp_path)
    heads = [b.get('head', '') for b in (pr.unknown_blocks or ())]
    assert not [h for h in heads if h.startswith('camera')], heads
