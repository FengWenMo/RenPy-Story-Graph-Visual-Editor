"""
tests/test_generation_guard.py
==============================
生成安全门禁的回归测试。

**这个文件的存在理由是一次真实事故**（2026-09-28 实测）：
`sr_general/game/chapter/chapter1_4.generated.rpy` 里有 18 行
`show <中文角色名>`——Ren'Py 规定图像 tag/属性只能是字母/数字/下划线，
那 18 行是语法非法的；同一份文件还和原文 `chapter1_4.rpy`
重复定义了 `label chapter1_4`。而 Ren'Py 会把 game/ 下所有 .rpy
当成**同一个脚本**，重名 label 会让游戏直接起不来。

事故的根因是"立绘列与舞台列双写"（另见
`test_projection_single_source.py`）；这个文件守的是**最后一道闸**：
坏脚本不许被静默写进项目。

分层：
  · `TestGuardRules`       —— 纯函数规则（任何机器都能跑）
  · `TestRealProjectArtifact` —— 用真实坏产物当固定用例（项目不在就 skip）
  · `TestWriteGate`        —— 落盘路径确实会被拦住
  · `TestExportPlan`       —— "先评、再问、最后写"这套顺序的纯逻辑
"""

from pathlib import Path

import pytest

from storygraph.story.script_table import (
    Beat, GenerationBlocked, GuardIssue, evaluate_generation,
    generation_guard, occupied_labels, planned_output_path,
    render_beats_to_rpy, write_beats_to_rpy, write_generation,
)


#: 真实项目里那份坏产物。没有这个目录时相关用例自动 skip；
#: 换机器用环境变量 STORYGRAPH_REAL_PROJECT 指到自己那份即可（见 _source_paths）。
from _source_paths import REAL_PROJECT               # noqa: E402


# ==================================================================
#  一、纯规则
# ==================================================================

def codes(issues):
    return sorted(i.code for i in issues)


def test_valid_image_names_are_clean():
    """正常脚本一条都不该报——误报比漏报更伤（会把好脚本拦下）。"""
    text = '\n'.join([
        'label a:',
        '    scene bg_classroom',
        '    show ling close_happy at left',
        '    show ling base_arms_akimbo close_happy at i11',
        '    show ye hi at left slide_in',      # at 后面跟多个变换
        '    hide ye with dissolve',
        '    show screen msg_dialogue',          # screen 不是图像，别管
        '    pause 1.0',
        '',
    ])
    assert generation_guard(text, Path('a.rpy')) == []


def test_chinese_image_tag_is_blocking():
    issues = generation_guard('label a:\n    show 叶怀瑾\n', Path('a.rpy'))
    assert codes(issues) == ['CJK_IN_IMAGE_TAG']
    assert issues[0].is_blocking
    assert issues[0].line_no == 2


def test_chinese_scene_and_hide_also_caught():
    text = 'label a:\n    scene 教室\n    hide 蓝\n'
    assert codes(generation_guard(text, Path('a.rpy'))) == [
        'CJK_IN_IMAGE_TAG', 'CJK_IN_IMAGE_TAG']


def test_chinese_transform_is_blocking():
    issues = generation_guard('label a:\n    show ling happy at 左边\n',
                              Path('a.rpy'))
    assert codes(issues) == ['CJK_IN_TRANSFORM']


def test_at_with_one_valid_token_is_not_blocked():
    """
    `at left 左边` 确实有问题，但"至少有一个合法标识符的候选列表"
    是我们判定安全的基准——宁可少报，也不要拦下正常脚本。
    """
    text = 'label a:\n    show ling at left 左边\n'
    assert generation_guard(text, Path('a.rpy')) == []


def test_commented_out_chinese_is_ignored():
    text = 'label a:\n    # show 叶怀瑾\n    pass\n'
    assert generation_guard(text, Path('a.rpy')) == []


def test_screen_language_and_expressions_are_not_flagged():
    """
    第一版规则在**程序手写的 UI 代码**上误报过一批（实测 sr_general 40 个脚本
    里 22 处）——所以这些写法必须固定成回归用例：

      `at transform:`（Screen Language）、`Transform(zoom=scale)`（表达式调用）、
      `at eye_tracking_transform(a, b)`、`show icon:`（属性写法）、UTF-8 BOM。

    把能跑的代码拦下来，比漏掉一行中文 tag 更贵。
    """
    text = '\n'.join([
        '\ufeff# BOM 开头的文件',
        'screen demo():',
        '    at transform:',
        '        zoom 0.5',
        '    show icon:',
        '        xpos 10',
        'label a:',
        '    show ling happy at Transform(zoom=scale)',
        '    show ling at eye_tracking_transform(cfg, 0.5, 0.5)',
        '    show ling happy at bounce_once:',
        '        linear 0.2 xoffset 4',
        '',
    ])
    assert generation_guard(text, Path('a.rpy')) == []


def test_label_collision_is_blocking():
    text = 'label chapter1_4:\n    pass\n'
    issues = generation_guard(text, Path('a.rpy'),
                              occupied_labels={'chapter1_4', 'start'})
    assert codes(issues) == ['LABEL_COLLISION']
    assert issues[0].is_blocking


def test_todo_is_warning_not_block():
    text = 'label a:\n    # TODO: 缺少资源 音乐 \'x.mp3\'\n    pass\n'
    issues = generation_guard(text, Path('a.rpy'))
    assert codes(issues) == ['UNRESOLVED_TODO']
    assert not issues[0].is_blocking


def test_outside_build_dir_warns_only_inside_project_game(tmp_path):
    """产物落进 <project>/game/ 才提醒；临时目录不该被说三道四。"""
    project = tmp_path / 'proj'
    (project / 'game').mkdir(parents=True)
    inside = project / 'game' / 'x.rpy'
    outside = tmp_path / 'elsewhere' / 'x.rpy'

    assert 'OUTSIDE_BUILD_DIR' in codes(
        generation_guard('label a:\n', inside, project_root=project))
    assert 'OUTSIDE_BUILD_DIR' not in codes(
        generation_guard('label a:\n', outside, project_root=project))
    # 没有 project_root 时干脆不判
    assert 'OUTSIDE_BUILD_DIR' not in codes(
        generation_guard('label a:\n', inside))


def test_guard_issue_format_mentions_line_and_level():
    issue = GuardIssue('X', 'block', '坏了', 7)
    assert '第 7 行' in issue.format() and 'block' in issue.format()


# ==================================================================
#  二、真实项目坏产物（固定用例）
# ==================================================================

needs_real = pytest.mark.skipif(
    not (REAL_PROJECT / 'game' / 'chapter' / 'chapter1_4.generated.rpy').exists(),
    reason='本机没有真实项目 sr_general')


@pytest.fixture
def real_chapter_paths():
    """(坏产物, 原文) 两个路径。写成模块级夹具而不是类方法——
    类作用域的实例方法夹具在 pytest 9 会告 deprecation。"""
    chapter = REAL_PROJECT / 'game' / 'chapter'
    return chapter / 'chapter1_4.generated.rpy', chapter / 'chapter1_4.rpy'


class TestRealProjectArtifact:
    """
    用**真实发生过的那份坏文件**当固定用例。

    数字是事故当时的实测值：18 行中文 tag + 1 处 label 重名。
    如果哪天数字变了，说明生成器或者这份文件被改过——两种情况都值得人看一眼。
    """

    @needs_real
    def test_broken_artifact_is_caught(self, real_chapter_paths):
        bad, _good = real_chapter_paths
        text = bad.read_text(encoding='utf-8')
        occ = occupied_labels(project_root=REAL_PROJECT, exclude={bad})
        issues = generation_guard(text, bad, project_root=REAL_PROJECT,
                                  occupied_labels=occ)

        by_code = {}
        for i in issues:
            by_code.setdefault(i.code, []).append(i)

        assert len(by_code.get('CJK_IN_IMAGE_TAG', [])) == 18, \
            '真实坏产物里的中文 tag 行数变了，请人工确认'
        assert len(by_code.get('LABEL_COLLISION', [])) == 1
        assert all(i.is_blocking for i in by_code['CJK_IN_IMAGE_TAG'])
        assert all(i.is_blocking for i in by_code['LABEL_COLLISION'])

    @needs_real
    def test_original_script_has_no_blocking_issue(self, real_chapter_paths):
        """原文必须是干净的——门禁不能把能跑的脚本也拦下。"""
        _bad, good = real_chapter_paths
        text = good.read_text(encoding='utf-8')
        occ = occupied_labels(project_root=REAL_PROJECT, exclude={good})
        issues = generation_guard(text, good, project_root=REAL_PROJECT,
                                  occupied_labels=occ)
        blocking = [i for i in issues if i.is_blocking]
        assert blocking == [], [i.format() for i in blocking]

    @needs_real
    def test_occupied_labels_are_discovered(self, real_chapter_paths):
        bad, _good = real_chapter_paths
        occ = occupied_labels(project_root=REAL_PROJECT, exclude={bad})
        # 原文的 label 必须在里面（否则重名判不出来）
        assert 'chapter1_4' in occ
        # 本工具的产物不该被算进"占用"（discover_scripts 已排除 .generated.rpy）
        assert (REAL_PROJECT / 'game' / 'chapter'
                / 'chapter1_4.generated.rpy') not in occ


# ==================================================================
#  三、落盘闸：坏脚本不许写进项目
# ==================================================================

class TestWriteGate:
    def test_writing_bad_script_raises_and_writes_nothing(self, tmp_path):
        """
        中文 tag 的 Beat 落盘时必须抛 GenerationBlocked，
        并且**不留半个文件**——否则"拒写"就成了半成品。
        """
        out = tmp_path / 'bad.rpy'
        # 直接写一个中文 image tag（模拟结构化列/舞台列双写的后果）
        beat = Beat(seq=1, label='a', text='喂', show='叶怀瑾 happy')
        with pytest.raises(GenerationBlocked) as ei:
            write_beats_to_rpy([beat], out)
        assert 'CJK_IN_IMAGE_TAG' in ei.value.format()
        assert not out.exists(), '门禁拦下后不该留下文件'

    def test_writing_good_script_succeeds(self, tmp_path):
        out = tmp_path / 'good.rpy'
        beat = Beat(seq=1, label='a', text='喂', speaker='e')
        write_beats_to_rpy([beat], out)
        assert out.exists()
        assert 'label a:' in out.read_text(encoding='utf-8')

    def test_blocked_script_is_still_renderable_for_comparison(self, tmp_path):
        """
        拦下不等于丢掉：调用方仍能用 render_beats_to_rpy 拿到文本给人看，
        这样"拒写"不会让人连问题都看不见。
        """
        beat = Beat(seq=1, label='a', text='喂', show='叶怀瑾 happy')
        text = render_beats_to_rpy([beat])
        assert '叶怀瑾' in text          # 文本拿得到
        out = tmp_path / 'x.rpy'
        with pytest.raises(GenerationBlocked):
            write_beats_to_rpy([beat], out)
        assert not out.exists()


# ==================================================================
#  四、导出计划：先评、再问、最后写
# ==================================================================

class TestExportPlan:
    """
    UI 的确认框顺序是"先评估 → 弹确认 → 才写盘"。
    这套顺序以前埋在 tkinter 里（测试只有桩，接错了也看不出来），
    现在抽成纯函数，在这里锁住。
    """

    def test_planned_output_goes_to_build_dir(self, tmp_path):
        """
        产物必须落在 `<项目根>/build/renpy/`，不再和原文并排——
        "并排"正是那次事故的形态（抢同一个 label）。
        """
        project = tmp_path / 'proj'
        table = project / 'game' / 'chapter' / 'ch01.xlsx'
        out = planned_output_path(table, project_root=project)
        assert out.parent == project / 'build' / 'renpy'
        assert out.name == 'ch01.rpy'
        # 产物不能在 game/ 里
        assert (project / 'game') not in out.parents

    def test_evaluate_does_not_touch_disk(self, tmp_path):
        """评估阶段绝不能写任何东西——用户还没点头。"""
        out = tmp_path / 'build' / 'renpy' / 'x.rpy'
        issues = evaluate_generation('label a:\n    show 叶怀瑾\n', out)
        assert [i.code for i in issues] == ['CJK_IN_IMAGE_TAG']
        assert not out.exists()
        assert not out.parent.exists(), '连目录都不该建'

    def test_evaluate_reports_warnings_separately(self, tmp_path):
        """确认框要能分别说"拒写"和"提醒"，所以评估结果必须能分开。"""
        text = 'label a:\n    # TODO: 缺少资源 音乐 \'x.mp3\'\n    pass\n'
        issues = evaluate_generation(text, tmp_path / 'x.rpy')
        blocking = [i for i in issues if i.is_blocking]
        warnings = [i for i in issues if not i.is_blocking]
        assert blocking == []
        assert [i.code for i in warnings] == ['UNRESOLVED_TODO']

    def test_write_generation_writes_when_clean(self, tmp_path):
        out = tmp_path / 'build' / 'renpy' / 'x.rpy'
        art = write_generation('label a:\n    pass\n', out)
        assert art.written and not art.blocked
        assert out.exists() and not art.review_path

    def test_write_generation_keeps_review_copy_but_not_target(self, tmp_path):
        """
        block 时：**目标文件不许出现**，但要另存一份 `.blocked.rpy` 供对比。
        只报错不给人看产物 = 让人猜哪里错了；写进目标路径 = 那次事故。
        """
        out = tmp_path / 'build' / 'renpy' / 'x.rpy'
        art = write_generation('label a:\n    show 叶怀瑾\n', out)
        assert art.blocked and not art.written
        assert not out.exists(), '坏了还写进目标路径'
        assert art.review_path is not None
        assert art.review_path.name == 'x.blocked.rpy'
        assert '叶怀瑾' in art.review_path.read_text(encoding='utf-8')

    def test_artifact_exposes_blocking_and_warnings(self, tmp_path):
        art = write_generation(
            'label a:\n    # TODO: 缺少资源 音乐 \'x.mp3\'\n    pass\n',
            tmp_path / 'y.rpy')
        assert art.written
        assert art.blocking == []
        assert [i.code for i in art.warnings] == ['UNRESOLVED_TODO']

    def test_label_collision_only_matters_inside_game_dir(self, tmp_path):
        """
        "和原文重名"只在产物**会被 Ren'Py 扫描**（落在 `<项目>/game/` 里）
        时才是致命问题。

        这条规则是两次实测逼出来的，缺一不可：
          · 修 `_guess_project_root` 之前：它恒返回 None，occupied 永远空集
            → LABEL_COLLISION **从来不判**（门禁形同虚设）；
          · 修好推断之后：`build` 写 `<项目>/out/story.generated.rpy` 时
            因为与 `game/script.rpy` 都有 `label start` 被拦下 —— 而那份
            产物根本不在 game/ 下、不会被扫描，**这是误报**。
        正确规则 = 两者取交集：会被扫描才判。
        """
        project = tmp_path / 'proj'
        (project / 'game').mkdir(parents=True)
        (project / 'game' / 'script.rpy').write_text(
            'label start:\n    return\n', encoding='utf-8')

        beat = Beat(seq=1, label='start', text='喂')

        target = project / 'build' / 'renpy' / 'ch1.rpy'
        write_beats_to_rpy([beat], target, project_root=project)
        assert target.exists(), '产物不在 game/ 下，撞名不该被拦'

        inside = project / 'game' / 'ch1.rpy'
        with pytest.raises(GenerationBlocked):
            write_beats_to_rpy([beat], inside, project_root=project)
        assert not inside.exists(), '真写进 game/ 时必须拦下且不留文件'
