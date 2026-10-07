"""
tests/test_asset_picker_ui.py
=============================
资产选择器（"选择立绘 / 背景图"）的三件事 —— 都来自用户 2026-10-05 实测：

> 另一个问题：图片加载还是慢，并且加载不完全，先加一个背景切换颜色按钮，
> 把白背景变成暗色背景，防止有的照片被挡住。再次优化图片加载逻辑，
> 可通过调试确认原因，或联网搜索更快速的算法

① **路径解析**：真实项目 2890 条里有 74 条（`gui/dutui/**/RETURE.png`）
   的 Path 多了一层项目目录名 → `asset_preview_path` 从前只试
   `project_root / Path` 一个基准 → 永远 `None` → 那 74 格**永远不出图、
   也没人告诉你为什么**（看起来就像"加载不完全"）。
② **底色切换**：白底/透明底的图在浅色面板上看不见。
③ **"没有图片文件"要说出来**：不能和"还在加载"长得一样。

（后台补齐与缓存口径的钉子在同目录 `test_thumbs.py`。）
"""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from PySide6.QtCore import QSettings  # noqa: E402


# ==================================================================
#  ① 路径解析：多一层项目目录名也要找得到
# ==================================================================

class _Engine:
    def __init__(self, reg, root):
        self._reg = reg
        self.project_root = root

    def get_registry(self):
        return self._reg


def _write_png(path: Path):
    """
    写一张**真能解码**的小 PNG。

    （用 8 字节假文件头会让 Qt/libpng 往 stderr 刷 `libpng error: Read Error`
    —— 测试输出会被噪声淹没，而且掩盖真正的解码报错。）
    """
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (4, 4), (200, 30, 30, 255)).save(path, 'PNG')


def _registry_with(rows):
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = rows
    return reg


class TestPreviewPathFindsEveryBase:
    def test_a_path_with_a_duplicated_project_dir_is_found(self, tmp_path):
        """
        真实项目那 74 条的形态：Path = `sr_general/game/gui/...`，而项目根
        就是 `.../sr_general` —— 文件其实在 `<root>/game/gui/...`。
        """
        from storygraph.app.beats import asset_preview_path
        proj = tmp_path / 'sr_general'
        img = proj / 'game' / 'gui' / 'dutui' / 'Frame 26' / 'RETURE.png'
        img.parent.mkdir(parents=True, exist_ok=True)
        _write_png(img)
        reg = _registry_with({'sr_general_RETURE': {
            'ID': 'sr_general_RETURE', 'Tag': 'ui',
            'Path': 'sr_general/game/gui/dutui/Frame 26/RETURE.png'}})
        got = asset_preview_path(_Engine(reg, proj), 'image',
                                 'sr_general_RETURE')
        assert got is not None, '多一层项目目录名的 Path 必须有兜底基准'
        assert Path(got).name == 'RETURE.png'
        assert Path(got).is_file()

    def test_a_plain_relative_path_still_works(self, tmp_path):
        from storygraph.app.beats import asset_preview_path
        proj = tmp_path / 'proj'
        img = proj / 'game' / 'images' / 'ling' / 'happy.png'
        img.parent.mkdir(parents=True, exist_ok=True)
        _write_png(img)
        reg = _registry_with({'ling_happy': {
            'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy',
            'Path': 'game/images/ling/happy.png'}})
        assert asset_preview_path(_Engine(reg, proj), 'image',
                                  'ling_happy') is not None

    def test_a_missing_file_is_still_none(self, tmp_path):
        from storygraph.app.beats import asset_preview_path
        reg = _registry_with({'ghost': {
            'ID': 'ghost', 'Tag': 'ui', 'Path': 'game/nope/ghost.png'}})
        assert asset_preview_path(_Engine(reg, tmp_path), 'image',
                                  'ghost') is None


# ==================================================================
#  ② + ③ 选择器本体
# ==================================================================

@pytest.fixture
def picker(qapp, tmp_path, monkeypatch):
    """
    一个"干净"的对话框：注册表里两条有图、一条没图。

    ⚠ 偏好存储换成**临时 ini 文件** —— Windows 上
    `QSettings.setPath/setDefaultFormat` 不一定生效（实测仍落注册表），
    不换的话测试会改写**用户真实的偏好**。
    """
    from storygraph.ui_qt import asset_picker as AP
    ini = tmp_path / 'prefs.ini'
    monkeypatch.setattr(
        AP, 'settings_factory',
        lambda: QSettings(str(ini), QSettings.IniFormat))

    from storygraph.ui_qt.asset_picker import AssetPickerDialog
    proj = tmp_path / 'proj'
    for name in ('ling_happy', 'ling_sad'):
        p = proj / 'game' / 'images' / 'ling' / f'{name}.png'
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_png(p)
    reg = _registry_with({
        'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling',
                       'Attributes': 'happy',
                       'Path': 'game/images/ling/ling_happy.png'},
        'ling_sad': {'ID': 'ling_sad', 'Tag': 'ling', 'Attributes': 'sad',
                     'Path': 'game/images/ling/ling_sad.png'},
        'ghost': {'ID': 'ghost', 'Tag': 'ui', 'Path': 'game/nope/ghost.png'},
    })
    dlg = AssetPickerDialog(None, registry=reg, project_root=proj,
                            types=['image'], want_image_spec=True)
    yield dlg
    dlg.close()


class TestNoImageIsSaidOutLoud:
    def test_the_tile_says_there_is_no_file(self, picker):
        texts = [picker._items.item(i).text()
                 for i in range(picker._items.count())]
        assert any('没有图片文件' in t for t in texts), texts
        assert picker._no_image == 1, picker._no_image

    def test_the_hint_counts_it(self, picker):
        assert '1 条没有图片文件' in picker._hint.text(), picker._hint.text()

    def test_the_detail_does_not_pretend_it_is_loading(self, picker):
        for i in range(picker._items.count()):
            it = picker._items.item(i)
            if '没有图片文件' in it.text():
                picker._items.setCurrentItem(it)
                break
        assert '不是"还在加载"' in picker._detail.text(), picker._detail.text()


class TestTileBackgroundToggle:
    """
    用户 2026-10-05："先加一个背景切换颜色按钮，把白背景变成暗色背景，
    防止有的照片被挡住"。
    """

    def test_toggling_switches_the_grid_background(self, picker):
        light = picker.tile_background()
        assert light not in ('', '#FF00FF'), (
            f'浅色底用了不存在的 token（theme.t 对未知键返回 #FF00FF）：{light}')
        picker._bg_btn.setChecked(True)
        dark = picker.tile_background()
        assert dark != light
        assert dark == picker.DARK_BG
        assert picker._items.styleSheet(), '底色要真的画到网格上'

    def test_the_text_colour_follows_the_background(self, picker):
        """深底 + 深字 = 换个方向看不见 —— 文字色必须跟着翻。"""
        assert picker.tile_foreground() != picker.DARK_FG
        picker._bg_btn.setChecked(True)
        assert picker.tile_foreground() == picker.DARK_FG

    def test_the_choice_is_remembered(self, picker, tmp_path):
        from storygraph.ui_qt import asset_picker as AP
        picker._bg_btn.setChecked(True)
        s = AP._settings()
        assert s.value(AP.SETTING_DARK, False, type=bool) is True, \
            '切了底色却没记住，下次打开又变回浅色'
        assert 'prefs.ini' in s.fileName(), (
            f'偏好写到了临时文件之外的地方：{s.fileName()}')


class TestBackgroundFill:
    """
    "加载不完全"的另一半：可见的优先，剩下的**后台自己补完**。
    """

    def test_the_fill_advances_and_stays_lower_priority(self, picker,
                                                        monkeypatch):
        from storygraph.ui_qt import thumb_loader as TL
        # 造一批候选，确保"视口外"的有很多
        picker._loader.clear_pending()
        picker._fill_cursor = 0
        picker._requested = set()
        seen_priorities = []
        real_request = picker._loader.request

        def spy(slot, path, apply, **kw):
            seen_priorities.append(kw.get('priority'))
            return real_request(slot, path, apply, **kw)

        monkeypatch.setattr(picker._loader, 'request', spy)
        picker._fill_step()
        assert picker._fill_cursor > 0, '补齐游标没往前走'
        assert seen_priorities, '补齐没有发出任何请求'
        assert all(p == TL.PRIORITY_BACKGROUND for p in seen_priorities), \
            f'补齐不许抢可见项的路：{seen_priorities}'

    def test_the_budget_stops_it_from_burning_the_cpu(self, picker):
        from storygraph.ui_qt.asset_picker import FILL_BUDGET
        picker._start_background_fill()
        assert picker._fill_deadline <= FILL_BUDGET, \
            '后台补齐必须有刹车（真实项目大图冷解码 ~380ms/张）'

    def test_a_missing_file_is_not_queued_for_decoding(self, picker,
                                                       monkeypatch):
        """没有文件的格子**不许**排队 —— 那是纯浪费（永远解不出图）。"""
        asked = []
        monkeypatch.setattr(picker, '_request_thumb',
                            lambda item, aid, priority=None: asked.append(aid))
        picker._requested = set()
        picker._fill_cursor = 0
        picker._fill_step()
        assert 'ghost' not in asked, asked
