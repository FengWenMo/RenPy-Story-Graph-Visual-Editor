"""
tests/test_layered_sprite_and_jump.py
=====================================
用户 2026-10-05（附"舞台演出预览"截图）：

> 演出界面需要改进！手动输入那个拍无法直接跳转到对应的拍场面，
> 另外存在一个"无头骑士"的 bug（不是程序问题）……查真实项目是资源组件
> 分离导致的……你尝试修复它

两件事分开钉：

① **无头骑士**：真实项目把立绘拆成 layeredimage 组件
   （`show ye base_handdown happy` = 身体件 + 表情件）。
   `ye/base_handdown.png` **只有身体没有头**（实测头部区不透明像素
   10935，而完整立绘 `ye/happy.png` 是 37152）。旧解析按**第一个属性**
   查表 → 画出来是个无头身体 ✗。

② **跳到第 N 拍**：手输数字后不回车也要跳（`QSpinBox` 的 `valueChanged`
   在敲键盘时只在上下箭头/滚轮触发；光输数字什么都不发生）。
"""

from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
#  ① 身体件 / 表情件
# ----------------------------------------------------------------------

def _images():
    """一张最小的表：同一 tag 下"身体件"和"完整立绘"都有。"""
    return {
        'base_handdown': {'ID': 'base_handdown', 'Tag': 'ye',
                          'Attributes': 'base_handdown',
                          'Path': 'game/images/characters/ye/base_handdown.png'},
        'ye_happy': {'ID': 'ye_happy', 'Tag': 'ye', 'Attributes': 'happy',
                     'Path': 'game/images/characters/ye/happy.png'},
        'ye_angry': {'ID': 'ye_angry', 'Tag': 'ye', 'Attributes': 'angry',
                     'Path': 'game/images/characters/ye/angry.png'},
        # 别的角色也有 happy/angry —— 不许串图
        'lan_happy': {'ID': 'lan_happy', 'Tag': 'lan', 'Attributes': 'happy',
                      'Path': 'game/images/characters/lan/happy.png'},
    }


class TestTheBodyPartIsNotUsedAsTheWholeSprite:
    def test_two_attributes_prefer_the_expression(self):
        """查询顺序：**表情件优先（从后往前）**，身体件垫底。"""
        from storygraph.app.beats import preferred_image_attrs
        assert preferred_image_attrs(['base_handdown', 'happy']) == \
            ['happy', 'base_handdown']

    def test_expression_first_order_also_works(self):
        """`show ye happy base_handdown`（表情写在前面）也要先试表情。"""
        from storygraph.app.beats import preferred_image_attrs
        assert preferred_image_attrs(['happy', 'base_handdown']) == \
            ['happy', 'base_handdown']

    def test_a_body_part_alone_still_resolves(self):
        """只写身体件时（`show ye base_handdown`）照旧给身体件。"""
        from storygraph.app.beats import lookup_image_id
        assert lookup_image_id(_images(), 'ye', ['base_handdown']) == \
            'base_handdown'

    def test_it_never_crosses_characters(self):
        """
        跨角色**永远不许**：给不到就退到"这个 tag 的第一张"（预览的老约定：
        宁可姿势不对也别空着），但绝不能是别人的立绘。
        """
        from storygraph.app.beats import lookup_image_id
        images = _images()
        for tag in ('lan', 'ling'):
            for attrs in (['angry'], ['happy'], ['base_handdown', 'happy']):
                got = lookup_image_id(images, tag, attrs)
                if got:
                    assert images[got]['Tag'] == tag, (tag, attrs, got)

    def test_body_part_detection(self):
        from storygraph.app.beats import _is_body_part
        for name in ('base_handdown', 'base_swimsuit', 'body', 'body_sit',
                     'base_hand_up', 'x_mask'):
            assert _is_body_part(name), name
        for name in ('happy', 'angry', 'close_happy', 'sideeye'):
            assert not _is_body_part(name), name


class TestResolveStageAssetUsesTheCompleteSprite:
    def test_composed_show_resolves_to_a_complete_file(self, tmp_path):
        """端到端：`ye base_handdown happy` 要给 `happy.png`（有头有身）。"""
        from storygraph.app.stage import resolve_stage_asset
        d = tmp_path / 'game' / 'images' / 'characters' / 'ye'
        d.mkdir(parents=True)
        for name in ('base_handdown.png', 'happy.png'):
            (d / name).write_bytes(b'x')
        images = {
            'base_handdown': {'ID': 'base_handdown', 'Tag': 'ye',
                              'Attributes': 'base_handdown',
                              'Path': 'game/images/characters/ye/'
                                      'base_handdown.png'},
            'ye_happy': {'ID': 'ye_happy', 'Tag': 'ye', 'Attributes': 'happy',
                         'Path': 'game/images/characters/ye/happy.png'},
        }
        got = resolve_stage_asset('ye base_handdown happy', images, tmp_path)
        assert got is not None and got.name == 'happy.png', got


# ----------------------------------------------------------------------
#  ② 手输"跳到第 N 拍"
# ----------------------------------------------------------------------

@pytest.fixture()
def preview(qapp):
    """一个只有 3 拍的预览窗口（用**真**的帧构建器，不读磁盘、不发声）。"""
    from storygraph.app.stage import build_frames
    from storygraph.story.beat_model import Beat, assign_identity
    from storygraph.ui_qt.stage_preview import StagePreviewWindow

    beats = [
        Beat(seq=1, label='t', speaker='e', text='一', scene='bg room'),
        Beat(seq=2, label='t', speaker='m', text='二'),
        Beat(seq=3, label='t', speaker='e', text='三'),
    ]
    assign_identity(beats)
    win = StagePreviewWindow(parent=None, frames=build_frames(beats))
    win._start_sound_loop = lambda: None
    return win


class TestTypingABeatNumberJumps:
    def test_typing_a_number_jumps_without_pressing_enter(self, preview):
        """
        手输数字 → **立刻**跳（`editingFinished` / `valueChanged` 都接上）。

        实测教训：`QSpinBox` 默认 `keyboardTracking(True)` 时 `valueChanged`
        只在上下箭头/滚轮发；光敲数字不回车什么都发生不了 —— 用户看到的
        就是"手动输入那个拍无法直接跳转"。
        """
        preview.show_frame(0)
        preview._jump.setValue(3)          # = 用户手输 "3"
        assert preview.idx == 2, f'没跳过去，还在第 {preview.idx + 1} 拍'

    def test_it_does_not_recurse(self, preview):
        """`show_frame` 里会 `setValue` 同步显示 —— 不能因此无限递归。"""
        preview.show_frame(1)
        preview._jump.setValue(2)
        preview.show_frame(0)
        assert preview.idx == 0

    def test_editing_finished_also_jumps(self, preview):
        preview.show_frame(0)
        preview._jump.setValue(1)
        preview._jump.editingFinished.emit()
        assert preview.idx == 0
        preview._jump.setValue(3)
        preview._jump.editingFinished.emit()
        assert preview.idx == 2
