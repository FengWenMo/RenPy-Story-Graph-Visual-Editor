# -*- coding: utf-8 -*-
"""
S3：导出控制流前的守门。

核心是一条**实测到的 P0**（本文件 `test_exporting_two_of_three_chapters_is_refused`
直接复现）：

    第 1 天：导入 ch01/ch02/ch03 → 导出 → 文件里三块 `_control` ✅
    第 2 天：只导入 ch01/ch02 → 再点导出 → **ch03_control 消失**，无任何提示

`export_to_renpy` 按图整份重生成，图里没有的章就被抹掉了。
用户完全可能只导两章就点一下导出 —— 所以要在写盘前拦住，并说清缺哪几章。

另有一条同源的：文件里如果有**不叫 `*_control`** 的 label，说明有人手写过，
整份重生成会把它们删掉（与 S2 那个实验同一类）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

from storygraph.app.editor import EditorSession  # noqa: E402
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.story.control_write import plan_control_write  # noqa: E402


def _chapter(root, name):
    p = root / 'game' / 'script' / f'{name}.rpy'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'label {name}:\n    e "{name} 的台词"\n    return\n',
                 encoding='utf-8')
    return p


def _graph_of(root, names):
    session = EditorSession(project_root=root)
    graph = Graph()
    for name in names:
        res = session.import_chapters([root / 'game' / 'script' / f'{name}.rpy'],
                                      graph)
        assert res['ok'], res.get('error')
        graph = res['graph']
    return graph


@pytest.fixture
def project(tmp_path):
    root = build_project(tmp_path / 'proj')
    for name in ('ch01', 'ch02', 'ch03'):
        _chapter(root, name)
    return root


# ===========================================================================
#  一、计划：只算不写
# ===========================================================================

class TestPlan:
    def test_missing_target_is_a_fresh_create(self, project, tmp_path):
        target = tmp_path / 'new_controller.rpy'
        plan = plan_control_write(project, _graph_of(project, ['ch01']), target)
        assert plan.ok is True
        assert plan.creates_file is True
        assert not target.exists(), '计划阶段不许建文件'

    def test_full_coverage_is_allowed(self, project, tmp_path):
        target = tmp_path / 'all.rpy'
        target.write_text(
            'label ch01_control:\n    return\n'
            'label ch02_control:\n    return\n', encoding='utf-8')
        plan = plan_control_write(project, _graph_of(project, ['ch01', 'ch02']),
                                  target)
        assert plan.ok is True, plan.blocking

    def test_missing_chapter_is_refused_and_named(self, project, tmp_path):
        target = tmp_path / 'all.rpy'
        target.write_text(
            'label ch01_control:\n    return\n'
            'label ch02_control:\n    return\n'
            'label ch03_control:\n    return\n', encoding='utf-8')
        plan = plan_control_write(project, _graph_of(project, ['ch01', 'ch02']),
                                  target)
        assert plan.ok is False
        assert plan.missing_chapters == ['ch03']
        assert any('ch03' in b for b in plan.blocking)

    def test_handwritten_label_blocks_the_export(self, project, tmp_path):
        """文件里有非 `_control` 的 label = 有人手写过 → 整份重生成会删掉它。"""
        target = tmp_path / 'all.rpy'
        target.write_text(
            'label ch01_control:\n    return\n'
            'label 我手写的入口:\n    return\n', encoding='utf-8')
        plan = plan_control_write(project, _graph_of(project, ['ch01']), target)
        assert plan.ok is False
        assert plan.foreign_labels == ['我手写的入口']

    def test_control_style_graph_also_counts_as_coverage(self, project, tmp_path):
        """图里是 `X_control` 节点（控制流图模式）时，不该误报"缺章"。"""
        target = tmp_path / 'all.rpy'
        target.write_text('label ch01_control:\n    return\n', encoding='utf-8')

        g = Graph()
        g.add_node('ch01_control', 0, 0)
        plan = plan_control_write(project, g, target)
        assert plan.ok is True, plan.blocking

    def test_empty_graph_is_refused(self, project, tmp_path):
        plan = plan_control_write(project, Graph(), tmp_path / 'x.rpy')
        assert plan.ok is False
        assert plan.blocking


# ===========================================================================
#  二、P0 回归：不许冲掉没加载的章
# ===========================================================================

def test_exporting_two_of_three_chapters_is_refused(project):
    """
    这就是实测到的那个 P0：
    三章导出过一次之后，只加载两章再导出 —— 第三章的 `_control` 会消失。
    守门必须拒写，而且**文件一个字节都不能变**。
    """
    target = project / 'game' / 'script' / 'all_controller.rpy'
    g_all = _graph_of(project, ['ch01', 'ch02', 'ch03'])
    first = EditorSession(project_root=project).export_control_flow(
        g_all, target)
    assert first['ok'], first.get('error')
    before = target.read_bytes()
    assert 'ch03_control' in before.decode('utf-8')

    g_two = _graph_of(project, ['ch01', 'ch02'])
    again = EditorSession(project_root=project).export_control_flow(
        g_two, target)

    assert again['ok'] is False, '缺章时必须拒写'
    assert 'ch03' in again['error']
    assert target.read_bytes() == before, '被拒时文件不许有任何改动'


def test_after_importing_everything_the_export_goes_through(project):
    target = project / 'game' / 'script' / 'all_controller.rpy'
    g_all = _graph_of(project, ['ch01', 'ch02', 'ch03'])
    session = EditorSession(project_root=project)

    assert session.export_control_flow(g_all, target)['ok']
    text = target.read_text(encoding='utf-8')
    for name in ('ch01', 'ch02', 'ch03'):
        assert f'{name}_control' in text

    # 再来一次（同样的图）→ 仍然放行，内容不丢
    assert session.export_control_flow(g_all, target)['ok']
    again = target.read_text(encoding='utf-8')
    for name in ('ch01', 'ch02', 'ch03'):
        assert f'{name}_control' in again


def test_refusal_happens_at_the_session_layer_not_only_in_the_plan(project):
    """
    守门必须在 `EditorSession.export_control_flow` 里，
    而不是只在某个 UI 调用点 —— 否则 CLI / 别的入口照样能把文件冲掉。
    """
    target = project / 'game' / 'script' / 'all_controller.rpy'
    session = EditorSession(project_root=project)
    session.export_control_flow(_graph_of(project, ['ch01', 'ch02', 'ch03']),
                                target)

    res = session.export_control_flow(_graph_of(project, ['ch01']), target)
    assert res['ok'] is False
    assert 'plan' in res, '拒的时候要把"为什么"带出来，UI 才能显示'


# ===========================================================================
#  三、P1-1：同名 `_control` 块里**手写的内容**不许被静默抹掉
# ===========================================================================

class TestManualContentInsideControl:
    """
    三条老判据只认 label 名：用户在 `ch01_control` 里加一句
    `$ persistent.seen_ch1 = True`（很常见），名字对得上就放行，
    整份重生成把那一行静默删掉。现在必须**先列出来、默认拒写**；
    用户看过并明确确认后才允许覆盖。
    """

    def _target(self, project, body: str):
        target = project / 'game' / 'script' / 'all_controller.rpy'
        target.write_text(body, encoding='utf-8')
        return target

    def test_manual_line_is_detected_and_refused(self, project):
        target = self._target(
            project,
            'label ch01_control:\n'
            '    $ persistent.seen_ch01 = True\n'
            '    return\n')
        plan = plan_control_write(project, _graph_of(project, ['ch01']),
                                  target)
        assert plan.ok is False
        assert [m['text'] for m in plan.manual_lines] == \
            ['$ persistent.seen_ch01 = True']
        assert plan.manual_lines[0]['label'] == 'ch01_control'
        assert plan.forceable is True, '手写内容这一条应允许确认后覆盖'

    def test_generated_ops_are_not_mistaken_for_manual(self, project):
        """控制流图模式下，图里的 `$` / show 操作也是生成的，不许误报。"""
        target = self._target(
            project, 'label ch01_control:\n    $ x = 1\n    return\n')
        g = Graph()
        g.control_flow = True
        g.add_node('ch01_control', 0, 0, operations=['$ x = 1'])
        plan = plan_control_write(project, g, target)
        assert plan.ok is True, plan.blocking
        assert plan.manual_lines == []

    def test_missing_chapter_is_still_not_forceable(self, project):
        """缺章是另一条拦截：就算用户点了覆盖，也不许越过它。"""
        target = self._target(
            project,
            'label ch01_control:\n    $ seen = True\n    return\n'
            'label ch02_control:\n    return\n')
        plan = plan_control_write(project, _graph_of(project, ['ch01']),
                                  target)
        assert plan.ok is False
        assert 'ch02' in plan.missing_chapters
        assert plan.forceable is False

    def test_session_refuses_then_force_overwrites(self, project):
        target = project / 'game' / 'script' / 'all_controller.rpy'
        session = EditorSession(project_root=project)
        g1 = _graph_of(project, ['ch01'])
        assert session.export_control_flow(g1, target)['ok']

        # 用户在生成物里手加一行（模拟"我就在这个文件里补一句"）
        text = target.read_text(encoding='utf-8')
        target.write_text(
            text.replace('label ch01_control:\n',
                         'label ch01_control:\n    $ seen = True\n', 1),
            encoding='utf-8')
        before = target.read_bytes()

        refused = session.export_control_flow(g1, target)
        assert refused['ok'] is False
        assert refused['plan']['manual_lines']
        assert target.read_bytes() == before, '被拒时一个字节都不许动'

        forced = session.export_control_flow(g1, target,
                                             allow_overwrite=True)
        assert forced['ok'] is True, forced.get('error')
        assert '$ seen = True' not in target.read_text(encoding='utf-8')
