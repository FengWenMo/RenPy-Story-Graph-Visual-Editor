# -*- coding: utf-8 -*-
"""
S2：选择点（分支外置）。

三条命门，每条都来自实测：

  1. **选择点必须活在"没人碰"的文件里** —— 往控制流文件里塞会被整份重写冲掉
     （本文件 `test_control_flow_export_does_not_eat_the_choice_file` 直接复现）。
  2. **剧本只多一行**，而且是 `call X from _call_X`：带返回点，存档才安全。
  3. **追加不覆盖**：选择点文件里可能有手写的东西，读-改-写、原字节不动。

其余是边角：label 名冲突递增、非法 label 拒绝、选项引号转义、计划不碰盘。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

from storygraph.story.choice_point import (  # noqa: E402
    DEFAULT_OPTIONS, apply_choice_point, build_menu_block, choices_path_for,
    plan_choice_point, used_labels,
)

SCRIPT = '''# 测试章节
label ch01:
    e "今天有件事想问你。"
    e "算了，先上课吧。"
    return
'''


@pytest.fixture
def project(tmp_path):
    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'ch01.rpy'
    script.write_text(SCRIPT, encoding='utf-8')
    return root


def _plan(project, **kw):
    script = project / 'game' / 'script' / 'ch01.rpy'
    return plan_choice_point(project, script, 'ch01', **kw)


# ===========================================================================
#  一、计划：只算不写
# ===========================================================================

class TestPlan:
    def test_names_and_paths(self, project):
        plan = _plan(project)
        assert plan.ok, plan.blocking
        assert plan.label == 'ch01_choice_1'
        assert plan.path.endswith('ch01_choices.rpy')
        assert plan.call_line == 'call ch01_choice_1 from _call_ch01_choice_1'
        assert plan.from_clause == '_call_ch01_choice_1'
        assert plan.creates_file is True

    def test_plan_does_not_touch_disk(self, project):
        before = {p: p.read_bytes() for p in project.rglob('*.rpy')}
        _plan(project)
        after = {p: p.read_bytes() for p in project.rglob('*.rpy')}
        assert after == before, '计划阶段不许写任何文件'

    def test_label_increments_when_taken(self, project):
        target = choices_path_for(project / 'game' / 'script' / 'ch01.rpy')
        target.write_text(
            'label ch01_choice_1:\n    return\n', encoding='utf-8')
        assert 'ch01_choice_1' in used_labels(project)

        plan = _plan(project)
        assert plan.label == 'ch01_choice_2', plan.label
        assert plan.call_line == 'call ch01_choice_2 from _call_ch01_choice_2'

    def test_existing_file_means_append(self, project):
        target = choices_path_for(project / 'game' / 'script' / 'ch01.rpy')
        target.write_text('# 之前加的\nlabel old:\n    return\n', encoding='utf-8')
        plan = _plan(project)
        assert plan.creates_file is False

    def test_missing_chapter_is_refused(self, project, tmp_path):
        plan = plan_choice_point(project, tmp_path / 'nope.rpy', 'ch01')
        assert plan.ok is False
        assert any('不存在' in b for b in plan.blocking)

    def test_bad_label_is_refused(self, project):
        script = project / 'game' / 'script' / 'ch01.rpy'
        plan = plan_choice_point(project, script, '1 bad label')
        assert plan.ok is False
        assert plan.blocking

    def test_default_options_are_neutral(self, project):
        plan = _plan(project)
        for text in DEFAULT_OPTIONS:
            assert f'"{text}"' in plan.block

    def test_custom_options_escape_quotes(self, project):
        plan = _plan(project, options=['他说："走"', '留下'])
        assert '\\"' in plan.block, '选项里的双引号必须转义，否则产物语法错'
        assert '"他说：\\"走\\""' in plan.block


# ===========================================================================
#  二、落盘：新建与追加
# ===========================================================================

class TestApply:
    def test_creates_a_menu_label(self, project):
        plan = _plan(project)
        res = apply_choice_point(plan, project)
        assert res['ok'], res
        assert res['created'] is True

        text = Path(plan.path).read_text(encoding='utf-8')
        assert 'label ch01_choice_1:' in text
        assert '    menu:' in text
        assert '    return' in text
        for opt in DEFAULT_OPTIONS:
            assert f'"{opt}":' in text
        assert 'pass' in text

    def test_script_file_is_untouched(self, project):
        """S2 只写选择点文件；剧本那一行进"拍"，落盘走导出。"""
        script = project / 'game' / 'script' / 'ch01.rpy'
        before = script.read_bytes()
        apply_choice_point(_plan(project), project)
        assert script.read_bytes() == before

    def test_append_keeps_existing_bytes(self, project):
        target = choices_path_for(project / 'game' / 'script' / 'ch01.rpy')
        original = '# 手写的备注\nlabel ch01_choice_1:\n    return\n'
        target.write_text(original, encoding='utf-8')

        plan = _plan(project)          # 会自动变成 _2
        res = apply_choice_point(plan, project)
        assert res['ok'], res and res.get('error')
        assert res['created'] is False

        text = target.read_text(encoding='utf-8')
        assert text.startswith(original), '已有内容必须逐字节保留'
        assert 'label ch01_choice_2:' in text

    def test_refused_plan_writes_nothing(self, project, tmp_path):
        plan = plan_choice_point(project, tmp_path / 'nope.rpy', 'ch01')
        res = apply_choice_point(plan, project)
        assert res['ok'] is False
        assert not list(project.rglob('*_choices.rpy'))

    def test_menu_block_is_parseable_by_our_own_lexer(self, project):
        """生成的片段至少要被自家 lexer 认出来（不然连行号都对不上）。"""
        from storygraph.parsing.renpy_parser import RenPyLexer

        block = build_menu_block('x_choice_1', ['A', 'B'])
        lines = RenPyLexer().tokenize_text(block, '<mem>')
        label_node = next(ln for ln in lines
                          if ln.text.strip().startswith('label x_choice_1'))
        # lexer 建的是树：label → menu: → 选项 → 选项体
        sub = [s.text.strip() for s in label_node.subblock]
        assert 'menu:' in sub, sub
        menu_node = next(s for s in label_node.subblock
                         if s.text.strip() == 'menu:')
        opts = [s.text.strip() for s in menu_node.subblock]
        assert any('"A"' in s for s in opts), opts


# ===========================================================================
#  三、命门：控制流导出不许吃掉选择点
# ===========================================================================

def test_control_flow_export_does_not_eat_the_choice_file(project):
    """
    这条是 S2 的**架构判据**。

    定案原本说"menu 塞进控制流文件"。实测：控制流产物是整份重生成的，
    手加进去的 label 下一次导出就没了。所以选择点必须住在
    `*_choices.rpy` 这种**导出器不碰**的文件里。

    本测试直接复现那个坑，并证明我们的落点是安全的。
    """
    from storygraph.app.editor import EditorSession
    from storygraph.graph.models import Graph
    from storygraph.pipeline.renpy_exporter import export_to_renpy

    script = project / 'game' / 'script' / 'ch01.rpy'
    session = EditorSession(project_root=project)
    res = session.import_chapters([script], Graph())
    assert res['ok'], res.get('error')
    graph = res['graph']

    plan = _plan(project)
    assert apply_choice_point(plan, project)['ok']
    choice_before = Path(plan.path).read_bytes()

    # 导出控制流（用户随时会点这个按钮）
    target = project / 'game' / 'script' / 'ch01_controller.rpy'
    export_to_renpy(graph, str(target), project_root=project)

    assert Path(plan.path).is_file(), '选择点文件被导出删掉了'
    assert Path(plan.path).read_bytes() == choice_before, '选择点文件被改写了'


def test_choice_file_is_not_treated_as_a_chapter(project):
    """
    它进 `discover_scripts` 的扫描范围，但不能被当成"章节内容文件"乱算 ——
    至少不能因为它多出来就报错/崩。
    """
    from storygraph.story.placement import detect_placement

    apply_choice_point(_plan(project), project)
    p = detect_placement(project)
    assert p.content_files is not None      # 不崩就行
    assert any('ch01.rpy' in x for x in p.content_files)


# ===========================================================================
#  四、接线：按钮真的连上了方法（不然模块就是孤岛）
# ===========================================================================

def _src(name: str) -> str:
    return (Path(__file__).resolve().parents[1]
            / 'storygraph' / 'ui_qt' / name).read_text(encoding='utf-8')


def test_the_button_is_wired_to_the_method():
    assert 'self._choice_btn.clicked.connect(self.insert_choice_point)' in \
        _src('main_window.py')


def test_method_exists_and_goes_through_the_plan():
    body = _src('window_card.py')
    body = body[body.find('def insert_choice_point'):]
    assert 'plan_choice_point(' in body, '必须走计划函数（算清楚再写）'
    assert 'apply_choice_point(' in body
    assert 'insert_flow_beat(' in body, '剧本那一行要走既有的插拍入口'
    # 先写文件、再插拍：反过来的话插拍失败会留下半截状态
    assert body.find('apply_choice_point(') < body.find('insert_flow_beat(')
