"""
tests/test_project_conventions.py
=================================
**适配项目自己的结构**（用户 2026-10-05 的核心要求）：

> 若是程序自定义了一个结构，而编剧用这个编辑器编辑时发现：你没有适应到它的
> 自定义结构怎么办？他会立即退出，因为工具没有用！……很多时候结构是有人
> 自定义的，工具必须能够适应！

三层机制各测一组：
  ① 认形状（自动识别，不看名字）
  ② 让它声明（`storygraph.rules.json`，声明覆盖识别、坏文件不炸）
  ③ 不认识就明说（覆盖度报告：只统计**剧情层**，块里的界面语句不算）
再加 **P4**：空姿势要 hide（`plan_pose_hides` + 渲染器真的吐出来）。
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def project(tmp_path):
    """自造项目：一个自定义姿势表的项目（名字全自造）。"""
    game = tmp_path / 'game'
    game.mkdir(parents=True)
    (game / 'defs.rpy').write_text(
        "layeredimage alpha:\n"
        "    image_format 'images/alpha/{image}.png'\n"
        "    group mood when not tall:\n"
        "        attribute glad default: 'glad'\n"
        "    group outfit when tall:\n"
        "        attribute tall: 'tall'\n",
        encoding='utf-8')
    (game / 'poses.rpy').write_text(
        "init python:\n"
        "    CYCLE = {'alpha': ['', 'tall']}\n"
        "    CYCLE_LABEL = {'': '普通', 'tall': '高个'}\n",
        encoding='utf-8')
    return tmp_path


def _beats():
    from storygraph.story.beat_model import Beat, assign_identity
    beats = [
        Beat(seq=1, label='t', text='一', show='alpha tall glad'),
        Beat(seq=2, label='t', text='二', show='alpha glad'),
        Beat(seq=3, label='t', text='三', show='alpha tall glad'),
        Beat(seq=4, label='t', text='四', show='alpha glad', hide='alpha'),
    ]
    assign_identity(beats)
    return beats


# ----------------------------------------------------------------------
#  ① 认形状
# ----------------------------------------------------------------------

class TestDetection:
    def test_pose_table_is_detected_from_shape(self, project):
        from storygraph.story.project_conventions import detect_conventions
        conv = detect_conventions(project)
        assert conv.poses.get('alpha') == ['', 'tall'], conv.poses
        assert conv.source.get('alpha') in ('detected', 'derived')

    def test_pose_labels_are_collected(self, project):
        from storygraph.story.project_conventions import detect_conventions
        conv = detect_conventions(project)
        assert conv.pose_labels.get('') == '普通'
        assert conv.pose_labels.get('tall') == '高个'

    def test_auto_fixes_are_off_by_default(self, project):
        """**默认什么都不自动改** —— 与工具既有 P0 一致。"""
        from storygraph.story.project_conventions import detect_conventions
        conv = detect_conventions(project)
        assert conv.hide_before_pose_switch() is False
        assert conv.auto_fixes == set()


# ----------------------------------------------------------------------
#  ② 让它自己声明
# ----------------------------------------------------------------------

class TestManifest:
    def _write(self, project, data):
        (project / 'storygraph.rules.json').write_text(
            json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def test_declaration_overrides_detection(self, project):
        from storygraph.story.project_conventions import detect_conventions
        self._write(project, {'poses': {'alpha': ['', 'sitting']},
                              'hide_before_pose_switch': True})
        conv = detect_conventions(project)
        assert conv.poses['alpha'] == ['', 'sitting'], conv.poses
        assert conv.source['alpha'] == 'declared'
        assert conv.hide_before_pose_switch() is True

    def test_it_can_teach_a_tag_the_tool_could_not_guess(self, project):
        """项目凭空多一个姿势维度 —— 工具不认识，但清单一说就会了。"""
        from storygraph.story.project_conventions import detect_conventions
        self._write(project, {'poses': {'beta': ['', 'dance']}})
        conv = detect_conventions(project)
        assert conv.poses['beta'] == ['', 'dance']

    def test_a_broken_manifest_does_not_break_the_project(self, project):
        (project / 'storygraph.rules.json').write_text('{ 这不是 json',
                                                       encoding='utf-8')
        from storygraph.story.project_conventions import detect_conventions
        conv = detect_conventions(project)
        assert conv.warnings, '坏清单要有一条可读的警告'
        assert conv.poses.get('alpha') == ['', 'tall'], '识别结果还应当在'

    def test_detect_poses_can_be_turned_off(self, project):
        from storygraph.story.project_conventions import detect_conventions
        self._write(project, {'detect_poses': False,
                              'poses': {'alpha': ['', 'only']}})
        conv = detect_conventions(project)
        assert conv.poses == {'alpha': ['', 'only']}, conv.poses

    def test_notes_are_kept_for_the_report(self, project):
        from storygraph.story.project_conventions import detect_conventions
        self._write(project, {'notes': '我们的立绘是程序生成的'})
        assert detect_conventions(project).notes == '我们的立绘是程序生成的'


# ----------------------------------------------------------------------
#  ③ 不认识就明说
# ----------------------------------------------------------------------

class TestCoverage:
    def test_a_custom_story_statement_is_reported(self, project):
        (project / 'game' / 'custom.rpy').write_text(
            "label a:\n"
            "    mycustomcue 'Thunder' intensity=3\n"
            "    show alpha glad\n",
            encoding='utf-8')
        from storygraph.story.project_conventions import unknown_statements
        unk = unknown_statements(project)
        assert 'mycustomcue' in unk, unk
        assert unk['mycustomcue']['count'] == 1
        assert unk['mycustomcue']['examples'], '要带例子（好让人找到它）'

    def test_screen_language_is_not_reported(self, project):
        """`screen` / `layeredimage` / `init python` 里的东西属于
        "原样保留"的第三类内容，**不算**不认识。"""
        (project / 'game' / 'ui.rpy').write_text(
            "screen phone():\n"
            "    vbox:\n"
            "        text 'hello'\n"
            "        imagebutton idle 'a.png' action Call('x')\n"
            "\n"
            "init python:\n"
            "    def helper(a, b):\n"
            "        return a + b\n",
            encoding='utf-8')
        from storygraph.story.project_conventions import unknown_statements
        unk = unknown_statements(project)
        assert unk == {}, unk

    def test_known_statements_are_not_reported(self, project):
        (project / 'game' / 'ch.rpy').write_text(
            "label a:\n"
            "    scene bg room\n"
            "    show alpha glad at left\n"
            "    play music 'x.mp3' fadein 1.0\n"
            "    menu:\n"
            "        '选一个':\n"
            "            jump b\n"
            "    return\n",
            encoding='utf-8')
        from storygraph.story.project_conventions import unknown_statements
        assert unknown_statements(project) == {}


# ----------------------------------------------------------------------
#  P4：空姿势要 hide
# ----------------------------------------------------------------------

class TestPlanPoseHides:
    def test_b_to_empty_without_hide_is_planned(self, project):
        from storygraph.story.layered_model import load_layer_model
        from storygraph.story.pose_patch import plan_pose_hides
        m = load_layer_model(project)
        plan = plan_pose_hides(_beats(), m)
        assert plan == {1: ['hide alpha']}, plan

    def test_a_beat_that_hides_itself_is_not_planned(self, project):
        from storygraph.story.beat_model import Beat, assign_identity
        from storygraph.story.layered_model import load_layer_model
        from storygraph.story.pose_patch import plan_pose_hides
        beats = [Beat(seq=1, label='t', show='alpha tall glad'),
                 Beat(seq=2, label='t', show='alpha glad', hide='alpha')]
        assign_identity(beats)
        m = load_layer_model(project)
        assert plan_pose_hides(beats, m) == {}

    def test_switching_between_two_poses_is_not_planned(self, tmp_path):
        """两个姿势之间切换**不需要** hide（只有改回空姿势才要）。"""
        from storygraph.story.beat_model import Beat, assign_identity
        from storygraph.story.layered_model import load_layer_model
        from storygraph.story.pose_patch import plan_pose_hides
        game = tmp_path / 'p2' / 'game'
        game.mkdir(parents=True)
        (game / 'defs.rpy').write_text(
            "layeredimage alpha:\n"
            "    group mood when not tall and not gown:\n"
            "        attribute glad default: 'glad'\n"
            "    group outfit when tall:\n"
            "        attribute tall: 'tall'\n"
            "    group outfit when gown:\n"
            "        attribute gown: 'gown'\n",
            encoding='utf-8')
        beats = [Beat(seq=1, label='t', show='alpha tall glad'),
                 Beat(seq=2, label='t', show='alpha gown glad')]
        assign_identity(beats)
        m = load_layer_model(tmp_path / 'p2')
        assert plan_pose_hides(beats, m) == {}

    def test_report_is_human_readable(self, project):
        from storygraph.story.layered_model import load_layer_model
        from storygraph.story.pose_patch import pose_hide_report
        m = load_layer_model(project)
        lines = pose_hide_report(_beats(), m)
        assert len(lines) == 1
        assert 'hide alpha' in lines[0] and '第 2 拍' in lines[0], lines

    def test_the_renderer_emits_the_hide_line(self, project):
        from storygraph.story.beat_render import render_beats_to_rpy
        from storygraph.story.layered_model import load_layer_model
        from storygraph.story.pose_patch import plan_pose_hides
        beats = _beats()
        m = load_layer_model(project)
        text = render_beats_to_rpy(beats, pose_hides=plan_pose_hides(beats, m))
        assert 'hide alpha' in text, text
        # 一共两处：**补的那一处** + 第 4 拍自己写的那一处
        body = [ln.strip() for ln in text.splitlines()]
        assert body.count('hide alpha') == 2, body
        # 补的那一处必须在"改回普通"的那一拍之前（也就是第二条 show 之前）
        first_show2 = body.index('show alpha glad')
        assert body[:first_show2].count('hide alpha') == 1, body

    def test_nothing_is_added_when_there_is_no_plan(self, project):
        """不给 `pose_hides` → 一行都不多加（老行为逐字节不变）。"""
        from storygraph.story.beat_model import Beat, assign_identity
        from storygraph.story.beat_render import render_beats_to_rpy
        beats = [Beat(seq=1, label='t', text='一', show='alpha tall glad'),
                 Beat(seq=2, label='t', text='二', show='alpha glad')]
        assign_identity(beats)
        assert 'hide alpha' not in render_beats_to_rpy(beats)


# ---------------------------------------------------------------------------
#  M5：三张手维护清单收敛成唯一真源（2026-10-07）
# ---------------------------------------------------------------------------

class TestHeadWordlistsAreConverged:
    """
    语句头词表从前在**三处**各写一份（口径不同、都得记得同步）。
    M5 把它收进 `core.vocabulary`，三处变成**别名**。

    收敛的判据不是"看起来一样"，而是这三条：
      · 三处**是同一个对象**（别名，不是抄一份）；
      · 差异是**刻意的且被保住**（`if/while/for` 不许进动词表）；
      · **只许加，不许减** —— 少了任何一个词都会让覆盖度报告凭空多出噪声。
    """

    def test_three_sites_are_aliases_of_one_source(self):
        from storygraph.core import vocabulary as v
        from storygraph.story import beat_render
        from storygraph.story import project_conventions as pc

        assert beat_render._RENPY_STATEMENT_HEADS is v.RENPY_STATEMENT_VERBS
        assert pc.KNOWN_STATEMENT_HEADS is v.RENPY_KNOWN_HEADS
        assert pc._CONTAINER_HEADS is v.RENPY_CONTAINER_HEADS

    def test_flow_heads_stay_out_of_the_dollar_table(self):
        """
        `if/elif/else/while/for` **必须**留在动词表外。

        它们在 `raw` 里出现时是**代码块的条件行**，当成 Ren'Py 语句透传会把
        `python:` 块的结构弄坏（第一版把 `while` 放进去，实测红了两个测试）。
        这条钉住 M5 的"收敛"没有顺手把它们合进去。
        """
        from storygraph.core import vocabulary as v

        assert not (v.RENPY_FLOW_HEADS & v.RENPY_STATEMENT_VERBS), (
            '流程块头不该进动词表')

    def test_no_head_disappears_in_the_convergence(self):
        """**收敛只许加，不许减**（减了 = 以前认识的变成不认识）。"""
        from storygraph.core import vocabulary as v

        # 收敛前 `KNOWN_STATEMENT_HEADS` 的字面值（历史快照，不许改）
        old = frozenset({
            'label', 'menu', 'return', 'jump', 'call', 'pause', 'window',
            'nvl', 'scene', 'show', 'hide', 'with', 'play', 'stop', 'queue',
            'voice', 'if', 'elif', 'else', 'while', 'for', 'pass', 'python',
            'init', 'define', 'default', 'transform', 'image',
            'layeredimage', 'screen', 'style', 'translate', 'camera', 'at',
            'expression', 'extend', '$', 'character', 'audio', 'movie',
            'side', 'onlayer', 'behind', 'zorder', 'as', 'block', 'on',
            'parallel', 'function', 'key', 'timer', 'input', 'renpy',
            'persistent', 'store', 'testcase', 'rpy', 'early',
        })
        lost = sorted(old - v.RENPY_KNOWN_HEADS)
        assert not lost, f'收敛把以前认识的头弄丢了：{lost}'

    def test_container_heads_are_still_skipped_by_coverage_report(self):
        """容器块（`screen` / `init python` …）仍要整块跳过，不被当成"不认识"。"""
        from storygraph.core import vocabulary as v

        for head in ('screen', 'init', 'python', 'transform', 'layeredimage'):
            assert head in v.RENPY_CONTAINER_HEADS, head
