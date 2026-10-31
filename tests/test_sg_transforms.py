"""
tests/test_sg_transforms.py
===========================
**生成 `sg_*` 尺寸变换的三条护栏**（2026-10-31，用户拍板的前提，一条都别省）。

用户原话：

> 选 1（允许生成 sg），但要加三个前提条件
> ①文件头"可检测的生成标记 + 内容 sha256"，手改了就 abort 不覆盖；
> ②生成前查重（同名 / 几何能匹配 → 复用，不写文件）；
> ③引用存在性检测：脚本里引用了 `sg_*` 但文件不在 → 打开项目时点名。

这个文件按这三条分开钉：

  · `TestNaming` / `TestCompose` —— 生成什么（名字可预测、体来自唯一出处）；
  · `TestFingerprint` —— ① 标记 + sha256：手改 / 别人的文件 / 陌生版本，一律不覆盖；
  · `TestDedup` —— ② 查重：同名同内容、几何等价、撞名 → 分别怎么处理；
  · `TestPlanApply` —— 写盘：只写该写的、写之前再看一眼磁盘（TOCTOU）；
  · `TestReferences` —— ③ 引用存在性 + 它怎么变成体检里的一条错误。

⚠ 端到端（面板点「改」→ 真的写文件 + 改拍）在 `tests/test_size_generate.py`。
"""

from __future__ import annotations

import pytest

from storygraph.story import sg_transforms as SG
from storygraph.story.atl import scan_transforms

SCREEN = (1920, 1080)


def _project(tmp_path, transforms='', scripts=''):
    """一个最小的 Ren'Py 工程（只建 `game/`，够护栏判事）。"""
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True)
    if transforms:
        (game / 'transform.rpy').write_text(transforms, encoding='utf-8')
    if scripts:
        (game / 'script.rpy').write_text(scripts, encoding='utf-8')
    return root


def _file(root):
    return root / 'game' / 'sg_transforms.rpy'


# ==================================================================
#  生成什么：名字 + 体
# ==================================================================

class TestNaming:
    def test_presets_map_to_predictable_names(self):
        assert SG.transform_name('cover') == 'sg_fit_cover'
        assert SG.transform_name('contain') == 'sg_fit_contain'
        assert SG.transform_name('half') == 'sg_zoom_050'
        assert SG.transform_name('custom', 0.75) == 'sg_zoom_075'
        assert SG.transform_name('custom', 1.25) == 'sg_zoom_125'

    def test_as_is_generates_nothing(self):
        assert SG.transform_name('as_is') == ''
        assert SG.definition_text('sg_x', 'as_is') == ''

    def test_odd_zoom_keeps_a_readable_name(self):
        """不是整百分比的缩放（0.333）也要有**稳定**名字，不许靠 `%g` 的写法。"""
        assert SG.transform_name('custom', 0.333) == 'sg_zoom_0p333'

    def test_definition_body_comes_from_the_preset(self):
        assert SG.definition_text('sg_fit_cover', 'cover') == \
            'transform sg_fit_cover:\n    fit "cover"\n'
        assert SG.definition_text('sg_zoom_075', 'custom', 0.75) == \
            'transform sg_zoom_075:\n    zoom 0.75\n'

    def test_the_generated_def_has_the_same_geometry_as_the_preset(self):
        """生成的定义算出来的框 = 预设要的框（否则"预览对了、游戏里还是错"）。"""
        from storygraph.story import size_presets as SP
        from storygraph.story.atl import placement_box, placement_of

        for natural in ((4000, 3000), (1, 1), (3840, 2160)):
            for key in ('cover', 'contain'):
                name = SG.transform_name(key)
                defn = SG.transform_def(name, key)
                place = placement_of(name, {name: defn}, screen=SCREEN)
                box = placement_box(place, natural, SCREEN)
                assert SP._same_box(box, SP.target_box(key, natural, SCREEN)), \
                    (key, natural, box)


# ==================================================================
#  ① 标记 + 指纹
# ==================================================================

class TestFingerprint:
    def test_a_missing_file_is_fine(self, tmp_path):
        sg = SG.inspect(_project(tmp_path))
        assert sg.state == SG.MISSING and sg.ok and not sg.exists

    def test_round_trip_is_clean(self, tmp_path):
        root = _project(tmp_path)
        plan = SG.plan_for_project(root, 'cover')
        plan.apply()
        sg = SG.inspect(root)
        assert sg.state == SG.OK and sg.names() == ['sg_fit_cover']
        assert sg.stmt_of('sg_fit_cover') == 'fit "cover"'
        assert sg.rel == 'game/sg_transforms.rpy'

    def test_the_marker_is_on_the_third_line_and_carries_the_body_hash(
            self, tmp_path):
        root = _project(tmp_path)
        SG.plan_for_project(root, 'cover').apply()
        lines = _file(root).read_text(encoding='utf-8').split('\n')
        assert SG.MARKER_RE.match(lines[2].strip()), lines[:4]

    def test_hand_edit_aborts_instead_of_overwriting(self, tmp_path):
        """核心判据：指纹对不上 → **拒绝**，且文件内容一个字不动。"""
        root = _project(tmp_path)
        SG.plan_for_project(root, 'cover').apply()
        f = _file(root)
        edited = f.read_text(encoding='utf-8').replace(
            'fit "cover"', 'fit "cover"\n    alpha 0.9')
        f.write_text(edited, encoding='utf-8')
        sg = SG.inspect(root)
        assert sg.state == SG.HAND_EDITED and not sg.ok
        assert '改过' in sg.reason
        plan = SG.plan_for_project(root, 'half')
        assert plan.action == SG.REFUSE and '改过' in plan.reason
        assert plan.writes is False
        assert plan.apply() is None                     # 拒绝的计划不写任何东西
        assert f.read_text(encoding='utf-8') == edited, '手改的内容被覆盖了'

    def test_a_foreign_file_is_left_alone(self, tmp_path):
        root = _project(tmp_path)
        f = _file(root)
        f.write_text('# 我自己写的\ntransform mine:\n    zoom 2\n',
                     encoding='utf-8')
        sg = SG.inspect(root)
        assert sg.state == SG.FOREIGN and '不是工具生成的' in sg.reason
        assert SG.plan_for_project(root, 'cover').action == SG.REFUSE
        assert 'mine' in f.read_text(encoding='utf-8')

    def test_an_unknown_version_is_not_touched(self, tmp_path):
        root = _project(tmp_path)
        _file(root).write_text(
            '# sg-gen v9 sha256 ' + '0' * 64 + '\n\ntransform x:\n    zoom 2\n',
            encoding='utf-8')
        sg = SG.inspect(root)
        assert sg.state == SG.BAD_VERSION and 'v9' in sg.reason

    def test_crlf_is_not_a_hand_edit(self, tmp_path):
        """Windows 上 git 把文件换成 CRLF 不是"人改过"——指纹按 LF 归一算。"""
        root = _project(tmp_path)
        SG.plan_for_project(root, 'cover').apply()
        f = _file(root)
        raw = f.read_bytes().replace(b'\n', b'\r\n')
        f.write_bytes(raw)
        assert SG.inspect(root).state == SG.OK

    def test_unreadable_is_not_guessed_at(self, tmp_path):
        """二进制垃圾（不是 UTF-8）→ 不猜、不写。"""
        root = _project(tmp_path)
        _file(root).write_bytes(b'\xff\xfe\x00\x00sg-gen')
        sg = SG.inspect(root)
        assert sg.state == SG.UNREADABLE and not sg.ok


# ==================================================================
#  ② 查重
# ==================================================================

class TestDedup:
    def test_same_name_and_body_is_reused_not_rewritten(self, tmp_path):
        root = _project(tmp_path, transforms='transform sg_fit_cover:\n'
                                             '    fit "cover"\n')
        plan = SG.plan_for_project(root, 'cover', index=scan_transforms(root))
        assert plan.action == SG.REUSE and plan.name == 'sg_fit_cover'
        assert not plan.writes

    def test_a_geometrically_equal_transform_is_reused(self, tmp_path):
        """项目里已有 `bgfit`（fit cover）→ 不生成，用它的名字。"""
        root = _project(tmp_path, transforms='transform bgfit:\n'
                                             '    fit "cover"\n')
        index = scan_transforms(root)
        plan = SG.plan_for_project(root, 'cover', index=index,
                                   natural=(4000, 3000), screen=SCREEN)
        assert plan.action == SG.REUSE and plan.name == 'bgfit'
        assert '等价' in plan.reason

    def test_a_taken_name_is_not_shadowed(self, tmp_path):
        """人自己写过 `transform sg_fit_cover: zoom 3` → 退到 `_2`，不改人家的。"""
        root = _project(tmp_path, transforms='transform sg_fit_cover:\n'
                                             '    zoom 3\n')
        plan = SG.plan_for_project(root, 'cover', index=scan_transforms(root))
        assert plan.action == SG.CREATE and plan.name == 'sg_fit_cover_2'
        assert '已经被别的内容占用' in plan.note

    def test_the_second_preset_appends_to_the_same_file(self, tmp_path):
        root = _project(tmp_path)
        SG.plan_for_project(root, 'cover').apply()
        plan = SG.plan_for_project(root, 'contain')
        assert plan.action == SG.APPEND
        plan.apply()
        sg = SG.inspect(root)
        assert sg.names() == ['sg_fit_cover', 'sg_fit_contain']
        text = _file(root).read_text(encoding='utf-8')
        assert text.count('transform ') == 2

    def test_asking_twice_does_not_duplicate(self, tmp_path):
        root = _project(tmp_path)
        SG.plan_for_project(root, 'cover').apply()
        again = SG.plan_for_project(root, 'cover')
        assert again.action == SG.NOOP
        assert SG.inspect(root).names() == ['sg_fit_cover']

    def test_as_is_is_refused_with_a_sentence(self, tmp_path):
        root = _project(tmp_path)
        plan = SG.plan_for_project(root, 'as_is')
        assert plan.action == SG.REFUSE and '去掉 at' in plan.reason


# ==================================================================
#  写盘
# ==================================================================

class TestPlanApply:
    def test_only_writing_actions_touch_the_disk(self, tmp_path):
        root = _project(tmp_path, transforms='transform bgfit:\n'
                                             '    fit "cover"\n')
        plan = SG.plan_for_project(root, 'cover', index=scan_transforms(root),
                                   natural=(4000, 3000), screen=SCREEN)
        assert plan.action == SG.REUSE
        assert plan.apply() is None
        assert not _file(root).exists()

    def test_the_file_changes_between_plan_and_write(self, tmp_path):
        """计划之后、确认之前文件被改 → 抛错，**不按旧计划写**。"""
        root = _project(tmp_path)
        plan = SG.plan_for_project(root, 'cover')
        _file(root).write_text('# 别人的文件\n', encoding='utf-8')
        with pytest.raises(RuntimeError) as info:
            plan.apply()
        assert '不是工具生成' in str(info.value) or '改过' in str(info.value)
        assert _file(root).read_text(encoding='utf-8') == '# 别人的文件\n'

    def test_the_diff_says_which_file_and_shows_the_lines(self, tmp_path):
        root = _project(tmp_path)
        text = SG.plan_for_project(root, 'cover').diff_text()
        assert 'game/sg_transforms.rpy' in text
        assert '+ transform sg_fit_cover:' in text
        assert '+     fit "cover"' in text

    def test_the_header_says_what_it_is(self, tmp_path):
        root = _project(tmp_path)
        SG.plan_for_project(root, 'cover').apply()
        head = _file(root).read_text(encoding='utf-8').split('\n')[:3]
        assert 'Renpy Story Graph Editor' in head[0]
        assert '请不要手工修改' in head[0]


# ==================================================================
#  ③ 引用存在性
# ==================================================================

class TestReferences:
    def test_a_reference_without_a_definition_is_named(self, tmp_path):
        root = _project(tmp_path, scripts=(
            'label a:\n'
            '    scene bg room at sg_fit_cover\n'
            '    show ling happy at bgfit, sg_zoom_075\n'))
        missing = SG.missing_references(root)
        assert [m['name'] for m in missing] == ['sg_fit_cover', 'sg_zoom_075']
        assert missing[0]['file'] == 'game/script.rpy'
        assert missing[0]['line'] == 1

    def test_a_defined_one_is_not_reported(self, tmp_path):
        root = _project(tmp_path, scripts=(
            'transform sg_fit_cover:\n    fit "cover"\n'
            'label a:\n    scene bg room at sg_fit_cover\n'))
        assert SG.missing_references(root) == []

    def test_the_generated_file_counts_as_a_definition(self, tmp_path):
        root = _project(tmp_path, scripts=(
            'label a:\n    scene bg room at sg_fit_cover\n'))
        assert len(SG.missing_references(root)) == 1
        SG.plan_for_project(root, 'cover').apply()
        assert SG.missing_references(root) == []

    def test_dialogue_that_mentions_at_is_not_a_reference(self, tmp_path):
        root = _project(tmp_path, scripts=(
            'label a:\n    e "meet me at sg_home tonight"\n'))
        assert SG.missing_references(root) == []

    def test_only_the_sg_namespace_is_reported(self, tmp_path):
        """人自己的 transform 不在这里报 —— 动态定义 / 别的目录都扫不全。"""
        root = _project(tmp_path, scripts=(
            'label a:\n    scene bg room at my_own_unknown\n'))
        assert SG.missing_references(root) == []

    def test_comments_are_ignored(self, tmp_path):
        root = _project(tmp_path, scripts=(
            'label a:\n    # scene bg room at sg_nope\n'))
        assert SG.missing_references(root) == []

    def test_it_becomes_an_error_in_the_health_report(self, tmp_path):
        """③ 的落点：项目体检里一条 **错误**，带文件名与行号。"""
        from storygraph.pipeline.validate import validate_sg_transforms

        root = _project(tmp_path, scripts=(
            'label a:\n'
            '    scene bg room at sg_fit_cover\n'))
        issues = validate_sg_transforms(root)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == 'E305' and issue.severity == 'error'
        assert 'sg_fit_cover' in issue.message
        assert issue.where == 'game/script.rpy:2'
        assert 'sg_transforms.rpy' in issue.hint

    def test_no_project_root_means_no_check(self):
        from storygraph.pipeline.validate import validate_sg_transforms

        assert validate_sg_transforms(None) == []
