"""
tests/test_stage_preview.py
===========================
**舞台演出预览**的核心（用户 2026-09-28 的核心诉求：

    > 我希望你能够写一个舞台演出核心，让这一章能够实时根据卡片效果跑起来

困难不在画，在**语义同源**：预览必须和生成的 .rpy 说同一件事，
否则"预览在骗人"——那比没有预览更糟（用户评审第 6 条：
"一旦策划发现我表里看到的和生成的不一样，他还会用这个工具吗"）。

所以 `build_frames` 的舞台状态**全部来自 `stage_snapshot.snapshot_rows`**，
本模块只多做两件事：transform → 屏幕 x、把"谁在说话"标出来。
这个文件锁的就是这两件事 + 同源约束。
"""

import pytest

from storygraph.story.beat_model import Beat, _assign_identity
from storygraph.story.stage_snapshot import snapshot_rows
from storygraph.ui.stage_preview import (
    CENTER_X, StageFrame, build_frames, frame_summary, transform_x,
)


class TestTransformX:
    def test_known_position_words(self):
        assert transform_x('left') < CENTER_X
        assert transform_x('right') > CENTER_X
        assert transform_x('center') == CENTER_X

    def test_project_transform_atl_wins(self):
        """资源表里 transform 的 ATL 有 xpos —— 这是最准的来源。"""
        class R:
            transforms = {'mine': {'ID': 'mine', 'ATL': 'xpos 0.33\nlinear 0.2 xpos 0.9'}}
        assert transform_x('mine', R()) == pytest.approx(0.33)

    def test_letter_heuristic_for_project_conventions(self):
        """`i11` / `f11` / `l21` / `r22` 这类项目惯例命名给个合理默认位。"""
        assert transform_x('l21') < CENTER_X
        assert transform_x('r22') > CENTER_X
        assert transform_x('i11') < CENTER_X
        assert transform_x('f11') > CENTER_X

    def test_unknown_falls_back_to_center(self):
        """拿不准就居中 —— **不猜**。"""
        assert transform_x('zzz_whatever') == CENTER_X
        assert transform_x('') == CENTER_X

    def test_result_always_normalized(self):
        class R:
            transforms = {'wild': {'ID': 'wild', 'ATL': 'xpos 5.0'}}
        assert 0.0 <= transform_x('wild', R()) <= 1.0


class TestBuildFrames:
    def test_state_comes_from_snapshot(self):
        """
        **同源约束**：帧里的场景/立绘/音乐必须与 `snapshot_rows` 一致。
        这是"预览不骗人"的机器化保证。
        """
        beats = [
            Beat(seq=1, label='t', speaker='e', text='一',
                 scene='bg room', show='ling happy@i11', music='bgm_a'),
            Beat(seq=2, label='t', speaker='m', text='二'),
            Beat(seq=3, label='t', speaker='e', text='三', hide='ling'),
        ]
        # 真实管线上 LineId 由 beats_from_parse_result / 导入时统一分配
        _assign_identity(beats)
        rows = snapshot_rows(beats)
        frames = build_frames(beats)
        assert len(frames) == len(rows) == 3
        for f, r in zip(frames, rows):
            assert f.scene == r['scene']
            assert f.music == r['music']
            assert f.line_id == r['line_id']
            assert sorted(s.tag for s in f.sprites) == \
                sorted(img.split()[0]
                       for img, _at in r['state']['actors'])

    def test_inherited_state_shows_up_in_later_frames(self):
        """第 2 拍没写背景/立绘 → 帧里仍然是继承来的（Ren'Py 就是这语义）。"""
        beats = [
            Beat(seq=1, label='t', text='一', scene='bg room',
                 show='ling happy@i11', music='bgm_a'),
            Beat(seq=2, label='t', text='二'),
        ]
        frames = build_frames(beats)
        assert frames[1].scene == 'bg room'
        assert [s.tag for s in frames[1].sprites] == ['ling']
        assert frames[1].music == 'bgm_a'

    def test_empty_beats_safe(self):
        assert build_frames([]) == []

    def test_frame_index_matches_position(self):
        beats = [Beat(seq=i + 1, label='t', text=f'第{i}句')
                 for i in range(5)]
        frames = build_frames(beats)
        assert [f.index for f in frames] == [0, 1, 2, 3, 4]

    def test_dialogue_text(self):
        f = build_frames([Beat(seq=1, label='t', speaker='e', text='你好')])[0]
        assert 'e' in f.dialogue and '你好' in f.dialogue

    def test_narration_dialogue_has_no_speaker_prefix(self):
        f = build_frames([Beat(seq=1, label='t', speaker='', text='下雨了')])[0]
        assert f.dialogue == '下雨了'

    def test_speaking_marked_only_when_confident(self):
        """
        没有角色表时 `speaker='e'` 匹配不上立绘 tag `ling` → **谁都不高亮**。
        高亮打错人比不高亮更糟。
        """
        beats = [Beat(seq=1, label='t', speaker='e', text='一',
                      show='ling happy@i11')]
        frames = build_frames(beats)
        assert all(not s.speaking for s in frames[0].sprites)

    def test_speaking_marked_via_image_tag(self):
        """有角色表时 `lyj` → ImageTag `ling` → 高亮那一张。"""
        class Reg:
            characters = {'lyj': {'ID': 'lyj', 'ImageTag': 'ling'}}
            transforms = {}
        beats = [Beat(seq=1, label='t', speaker='lyj', text='一',
                      show='ye hi@left; ling shy@f11')]
        frames = build_frames(beats, registry=Reg())
        speaking = [s for s in frames[0].sprites if s.speaking]
        assert len(speaking) == 1 and speaking[0].tag == 'ling'

    def test_speaking_sprite_is_first(self):
        class Reg:
            characters = {'lyj': {'ID': 'lyj', 'ImageTag': 'ling'}}
            transforms = {}
        beats = [Beat(seq=1, label='t', speaker='lyj', text='一',
                      show='ye hi@left; ling shy@f11')]
        frames = build_frames(beats, registry=Reg())
        assert frames[0].sprites[0].tag == 'ling', \
            '正在说话的立绘应该排在最前（用户要求"提前"）'

    def test_transforms_applied_to_sprite_x(self):
        beats = [Beat(seq=1, label='t', text='一',
                      show='a x@left; b y@right')]
        frames = build_frames(beats)
        xs = {s.tag: s.x for s in frames[0].sprites}
        assert xs['a'] < CENTER_X < xs['b']

    def test_changes_text_carried_over(self):
        """帧里带着「本拍变化」，与舞台快照表同一份文字。"""
        beats = [Beat(seq=1, label='t', text='一', scene='bg room')]
        rows = snapshot_rows(beats)
        frames = build_frames(beats)
        assert frames[0].changes == rows[0]['display_changes']


class TestFrameSummary:
    def test_summary_mentions_key_bits(self):
        f = build_frames([Beat(seq=1, label='t', text='一', scene='bg room',
                               show='ling happy', music='bgm_a')])[0]
        s = frame_summary(f)
        assert 'bg room' in s and 'ling happy' in s and 'bgm_a' in s

    def test_summary_on_empty_frame(self):
        f = build_frames([Beat(seq=1, label='t', text='一')])[0]
        assert frame_summary(f)          # 不抛就够


@pytest.mark.skipif(
    not __import__('pathlib').Path(
        r'D:\千连万理\RenPyTest\sr_general\game\chapter\chapter1_3.rpy').exists(),
    reason='本机没有真实项目')
class TestRealChapterPreview:
    """真实章节能建出帧，且立绘位置分布合理（不是全挤在中间）。"""

    REAL = r'D:\千连万理\RenPyTest\sr_general\game\chapter\chapter1_3.rpy'

    def _frames(self):
        from pathlib import Path
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        from storygraph.story.beat_build import beats_from_parse_result
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(Path(self.REAL))))
        return build_frames(beats)

    def test_builds_all_frames(self):
        frames = self._frames()
        assert len(frames) == 305

    def test_positions_are_varied(self):
        """位置散布要合理：全居中说明 transform 推导没起作用。"""
        frames = self._frames()
        xs = [s.x for f in frames for s in f.sprites]
        assert xs, '真实章节应该有立绘'
        assert len(set(round(x, 2) for x in xs)) > 1, \
            '所有立绘都推到了同一个 x —— transform 推导失效'


# ==================================================================
#  真实素材解析（2026-09-30：预览开始画真图 + 用真实图片实测）
# ==================================================================

class TestStageAssetResolution:
    """
    预览画真图，就必须回答"这个图像名对应磁盘上哪张图"。
    这里锁的是三条**真实数据实测出来的**坑。
    """

    def _proj(self, tmp_path, rel, content=b'x'):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content)
        return f

    def test_both_path_bases_are_tried(self, tmp_path):
        """
        真实数据实测：表里两套路径基准并存 ——
        扫描出来的行是 `game/images/...`（相对项目根），
        脚本 `image` 声明合并进来的是 `images/...`（相对 `game/`）。
        只认前者的话，**脚本声明的背景图一张都解析不出来**
        （chapter1_3 的 305 帧背景全落空）。
        """
        from storygraph.ui.stage_preview import resolve_stage_asset
        self._proj(tmp_path, 'game/images/bg/room.png')
        images = {'bg_room': {'ID': 'bg_room', 'Tag': 'bg',
                              'Attributes': 'room',
                              'Path': 'images/bg/room.png'}}
        got = resolve_stage_asset('bg room', images, tmp_path)
        assert got is not None and got.name == 'room.png'

        images2 = {'bg_room': {'ID': 'bg_room', 'Tag': 'bg',
                               'Attributes': 'room',
                               'Path': 'game/images/bg/room.png'}}
        got2 = resolve_stage_asset('bg room', images2, tmp_path)
        assert got2 is not None and got2.name == 'room.png'

    def test_solid_color_from_solid_field(self):
        """`image black = "#000000"`：颜色在 `Solid` 字段里（Path 是空的）。"""
        from storygraph.ui.stage_preview import resolve_solid_color
        images = {'black': {'ID': 'black', 'Tag': 'black',
                            'Attributes': 'black', 'Path': '',
                            'Solid': '#000000'}}
        assert resolve_solid_color('black', images) == '#000000'

    def test_solid_color_from_path_field(self):
        """老表/手写表可能把颜色直接写在 Path 上。"""
        from storygraph.ui.stage_preview import resolve_solid_color
        images = {'x': {'ID': 'x', 'Tag': 'x', 'Attributes': 'x',
                        'Path': 'Solid("#112233")'}}
        assert resolve_solid_color('x', images) == '#112233'

    def test_missing_and_solid_are_distinguished(self):
        """
        **纯色不算"找不到图"**：`scene black` 在游戏里就是一块黑，
        报成缺图会让人白去找一张不存在的文件。
        """
        from storygraph.ui.stage_preview import (StageFrame, SpriteLayout,
                                                 resolve_frame_assets)
        images = {'black': {'ID': 'black', 'Tag': 'black',
                            'Attributes': 'black', 'Solid': '#000000'}}
        f = StageFrame(index=0, line_id='a', label='a', speaker='',
                       text='', scene='black', sprites=[])
        a = resolve_frame_assets(f, images, None)
        assert a['missing'] == []

    def test_unresolvable_is_reported(self):
        from storygraph.ui.stage_preview import StageFrame, resolve_frame_assets
        f = StageFrame(index=0, line_id='a', label='a', speaker='',
                       text='', scene='bg 根本没这张', sprites=[])
        a = resolve_frame_assets(f, {}, None)
        assert a['missing'] == ['bg 根本没这张']
        assert a['bg'] is None

    def test_disk_index_matches_underscore_and_dot(self, tmp_path):
        """
        真实数据：脚本写 `bg bg6_2`，文件叫 `bg6.2.png`
        （`image bg bg6_2 = "images/bg/bg6.2.png"`）。两种写法都要认。
        """
        from storygraph.ui.stage_preview import image_index, image_index_lookup
        self._proj(tmp_path, 'game/images/bg/bg6.2.png')
        idx = image_index(tmp_path)
        assert idx, '索引应当是空的才怪'
        assert image_index_lookup('bg bg6_2', idx) is not None
        assert image_index_lookup('bg6.2', idx) is not None

    def test_disk_index_skips_gui(self, tmp_path):
        """`gui/` 里几千张界面素材与演出无关，不该进索引。"""
        from storygraph.ui.stage_preview import image_index
        self._proj(tmp_path, 'game/images/bg/room.png')
        self._proj(tmp_path, 'game/gui/button/room.png')
        idx = image_index(tmp_path)
        assert 'room' in idx
        from pathlib import Path as _P
        assert _P(idx['room']).parts[-3:-1] == ('bg', 'room.png') or \
            _P(idx['room']).name == 'room.png'
        assert 'gui' not in _P(idx['room']).parts, '界面素材不该进索引'
        # 索引里一个 gui 路径都不该有
        assert not [v for v in idx.values() if 'gui' in _P(v).parts]

    def test_index_lookup_is_safe_on_empty(self):
        from storygraph.ui.stage_preview import image_index_lookup
        assert image_index_lookup('bg room', {}) is None
        assert image_index_lookup('', {'room': 'x'}) is None
