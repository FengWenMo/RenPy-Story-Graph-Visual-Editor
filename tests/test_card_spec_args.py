"""
卡片的「语句参数」那一组：**动态参数列接进了 Qt 前端**
=====================================================

背景（用户 2026-10-07 的意见）：动态参数列只在 Excel 做了，卡片上还是
"候选注释"（`app.beats.spec_arg_choices_for` 一直挂在"health_check 说它
只有测试在用"那一类）。可编剧是**在卡片上写**的 —— 表里有下拉、卡片上
没有，这条能力就只对着会开 Excel 的人。

这个文件钉三件事：

  ① **同源**：卡片读的值 / 写回的路，与 Excel 的动态列**同一份实现**
     （`app.beats.spec_arg_fields_for` / `apply_spec_arg_fields` →
     `beat_model.fold_spec_arg_columns`）；
  ② **前端只递数据**：`BeatCardEditor.set_spec_args(cols, values, choices)`
     建控件、`collect_fields()` 把那一组的值带出去（键与 Excel 列名同名）；
  ③ **会话真的折叠**：`EditorSession.apply(fields)` 收到动态键就折进语句
     （头行 / 「代码」列那一行），与读表那条路结果一致。

offscreen 跑真 Qt（与 `tests/test_ui_qt.py` 同一套）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app import beats as B  # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402
from storygraph.story.statement_registry import StatementRegistry  # noqa: E402

RULES = {'statements': [{'name': 'mycue', 'label': '闪电提示', 'columns': [
    {'name': 'intensity', 'label': '强度', 'choices': ['1', '2', '3']},
    {'name': 'target', 'label': '目标', 'choices': ['ling', 'lan']},
]}]}

SCRIPT = (
    'label a:\n'
    '    "前"\n'
    '    mycue "闪电" intensity=3 target=ling:\n'
    '        target = ling\n'
    '    camera background:\n'
    '        perspective True\n'
    '    pause 1.0\n'
    '    "后"\n'
)


@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False), encoding='utf-8')
    src = root / 'game' / 'ch.rpy'
    src.write_text(SCRIPT, encoding='utf-8')
    return root, src


def _beats(src):
    pr = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)[0]
    return beats_from_parse_result(pr)


def _beat_with(beats, attr, text):
    for b in beats:
        if text in str(getattr(b, attr, '') or ''):
            return b
    raise AssertionError(f'没有 {attr} 含 {text!r} 的拍')


# ---------------------------------------------------------------------------
#  ① 同源：卡片要的值 / 写回的路
# ---------------------------------------------------------------------------

def test_spec_arg_fields_match_the_excel_cells(tmp_path):
    root, src = _project(tmp_path)
    beats = _beats(src)
    reg = StatementRegistry.for_project(root)

    decl = _beat_with(beats, 'spec_head', 'mycue')
    cols, values = B.spec_arg_fields_for(decl, reg)
    headers = [c['header'] for c in cols]
    assert 'mycue·intensity' in headers and 'mycue·target' in headers, headers
    assert values['mycue·intensity'] == '3', values
    assert values['mycue·target'] == 'ling', values

    cam = _beat_with(beats, 'camera_header', 'camera background')
    cols, values = B.spec_arg_fields_for(cam, reg)
    headers = [c['header'] for c in cols]
    # ⚠ 相机头那一拍**还会吸收紧跟它的语句**（`pause` 走「代码」列，与头拍
    # 同属一拍）—— 所以这一行的列里既有 `camera·layer@0` 也有
    # `pause·seconds@0`。这不是缺陷：一格装的是"这一拍里那条语句的参数"，
    # 与 Excel 的动态列口径完全一致。
    assert 'camera·layer@0' in headers, headers
    assert values['camera·layer@0'] == 'background', values

    leaf = next(b for b in beats if 'pause' in (b.raw or ''))
    cols, values = B.spec_arg_fields_for(leaf, reg)
    assert 'pause·seconds@0' in [c['header'] for c in cols], cols
    assert values['pause·seconds@0'] == '1.0', values


def test_apply_spec_arg_fields_folds_like_the_table(tmp_path):
    root, src = _project(tmp_path)
    beats = _beats(src)
    cam = _beat_with(beats, 'camera_header', 'camera background')
    B.apply_spec_arg_fields(cam, {'camera·layer@0': 'screens'})
    assert cam.camera_header == 'camera screens:', cam.camera_header

    decl = _beat_with(beats, 'spec_head', 'mycue')
    B.apply_spec_arg_fields(decl, {'mycue·target': 'lan'})
    assert 'target=lan' in decl.spec_head, decl.spec_head

    leaf = next(b for b in beats if 'pause' in (b.raw or ''))
    B.apply_spec_arg_fields(leaf, {'pause·seconds@0': '1.5'})
    assert 'pause 1.5' in (leaf.raw or ''), leaf.raw


def test_choices_come_from_the_project_declaration(tmp_path):
    root, src = _project(tmp_path)
    reg = StatementRegistry.for_project(root)
    decl = _beat_with(_beats(src), 'spec_head', 'mycue')
    got = B.spec_arg_choices_for(decl, reg)
    assert got == {'intensity': ['1', '2', '3'],
                   'target': ['ling', 'lan']}, got


def test_window_choices_are_built_in(tmp_path):
    """标准语句的候选**不需要项目声明**（内置形状表）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    f = root / 'game' / 'ch.rpy'
    f.write_text('label a:\n    window hide\n    "x"\n', encoding='utf-8')
    beat = next(b for b in _beats(f) if 'window' in (b.raw or ''))
    assert B.spec_arg_choices_for(beat, None) == {'action': ['show', 'hide',
                                                             'auto']}


# ---------------------------------------------------------------------------
#  ② 前端：控件建起来、值带得出去、脏标记会亮
# ---------------------------------------------------------------------------

@pytest.fixture
def panel(app):
    from storygraph.ui_qt.main_window import BeatCardEditor
    p = BeatCardEditor()
    yield p
    p.deleteLater()


def test_card_builds_one_widget_per_parameter(panel, tmp_path):
    root, src = _project(tmp_path)
    reg = StatementRegistry.for_project(root)
    decl = _beat_with(_beats(src), 'spec_head', 'mycue')
    cols, values = B.spec_arg_fields_for(decl, reg)
    panel.set_spec_args(cols, values, B.spec_arg_choices_for(decl, reg))
    assert sorted(panel._spec_widgets) == ['mycue·intensity', 'mycue·target']
    # 有候选 → **可编辑下拉**（与 Excel 的"方便不是牢笼"同一条口径）
    assert isinstance(panel._spec_widgets['mycue·intensity'], QComboBox)
    assert panel._spec_widgets['mycue·intensity'].currentText() == '3'


def test_card_collect_carries_the_parameter_keys(panel, tmp_path):
    root, src = _project(tmp_path)
    reg = StatementRegistry.for_project(root)
    cam = _beat_with(_beats(src), 'camera_header', 'camera background')
    cols, values = B.spec_arg_fields_for(cam, reg)
    panel.set_spec_args(cols, values, {})
    panel._spec_widgets['camera·layer@0'].setText('screens')
    got = panel.collect_fields()
    assert got.get('camera·layer@0') == 'screens', got
    assert panel.collect_all_fields().get('camera·layer@0') == 'screens'


def test_card_dirty_tracking_notices_a_parameter_edit(panel, tmp_path):
    """改一个参数 = 表单变脏（否则那个 💾 不会亮，用户以为改没生效）。"""
    root, src = _project(tmp_path)
    reg = StatementRegistry.for_project(root)
    cam = _beat_with(_beats(src), 'camera_header', 'camera background')
    cols, values = B.spec_arg_fields_for(cam, reg)
    panel.set_spec_args(cols, values, {})
    panel.load_fields({'Text': 'x'}, [])
    panel._spec_widgets['camera·layer@0'].setText('screens')
    assert panel.is_modified()


def test_card_hides_the_group_when_there_are_no_parameters(panel):
    panel.set_spec_args([], {}, {})
    assert not panel._spec_host.isVisible()
    assert panel._spec_values() == {}


def test_card_camera_column_has_no_dropdown(panel, tmp_path):
    """层名没有候选 → 普通输入框（编一组候选就是诱导人写错）。"""
    root, src = _project(tmp_path)
    reg = StatementRegistry.for_project(root)
    cam = _beat_with(_beats(src), 'camera_header', 'camera background')
    cols, values = B.spec_arg_fields_for(cam, reg)
    panel.set_spec_args(cols, values, B.spec_arg_choices_for(cam, reg))
    assert not isinstance(panel._spec_widgets['camera·layer@0'], QComboBox)


# ---------------------------------------------------------------------------
#  ③ 会话：收到动态键就折进语句（与读表同一条路）
# ---------------------------------------------------------------------------

def test_session_apply_folds_parameter_keys(tmp_path):
    from storygraph.app.editor import EditorSession
    root, src = _project(tmp_path)
    s = EditorSession(project_root=root)
    s.load(src)
    idx = next(i for i, b in enumerate(s.beats)
               if 'camera background' in (b.camera_header or ''))
    beat = s.apply(idx, {'camera·layer@0': 'screens'})
    assert beat.camera_header == 'camera screens:', beat.camera_header
    out = render_beats_to_rpy(s.beats, label_name='a')
    assert 'camera screens:' in out, out
