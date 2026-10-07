"""
tests/test_prewarm.py
=====================
**预热缩略图**（用户 2026-10-05："另外，可以做预热缩略图"）。

为什么值得一条显式的路（实测数字）：

  · 立绘/CG 是 3–11 MB、3856×2176 … 4320×7680 的 PNG，**冷解码 384–434 ms**；
  · 命中磁盘缓存只要 **2 ms** —— 差两个数量级；
  · 但"第一次看到某张图"这笔账总要有人付：不预热就是用户在滚动时一张张等。

两条硬要求：**尺寸口径必须与界面一致**（缓存键含目标尺寸，记错一个数字就
白跑 —— 第一版就把卡片尺寸写成 160×220，实测是 156×204），
以及**可中断、可看进度**（真实项目几分钟，不能是黑盒）。
"""

from __future__ import annotations

import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402


def _png(path: Path, size=(40, 60), colour=(200, 40, 40, 255)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', size, colour).save(path, 'PNG')
    return path


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """把缩略图缓存换到临时目录（两个命名空间都要换，见 `test_thumbs.py`）。"""
    from storygraph.story import excel_export as EE
    from storygraph.app import thumbs as TH
    d = tmp_path / 'thumbs'
    d.mkdir()
    monkeypatch.setattr(EE, 'thumb_cache_dir', lambda: d)
    monkeypatch.setattr(TH, 'thumb_cache_dir', lambda: d)
    return d


class TestSizesMatchTheUi:
    """
    尺寸口径 —— **这一条是这次差点白跑的地方**。

    第一版 `UI_THUMB_SIZES` 里的卡片尺寸写的是 (160, 220)（凭印象），
    而 `sprite_grid.THUMB_W/H` 实际是 (156, 204)：预热出来的缓存
    卡片那边**一次都命中不了**（缓存键 = 路径|mtime|size|WxH）。
    """

    def test_the_picker_size_matches(self, qapp):
        from storygraph.app.prewarm import UI_THUMB_SIZES
        from storygraph.ui_qt.asset_picker import THUMB
        assert tuple(THUMB) in [tuple(s) for s in UI_THUMB_SIZES], (
            f'选择器要的是 {THUMB}，预热清单里没有：{UI_THUMB_SIZES}')

    def test_the_card_size_matches(self, qapp):
        from storygraph.app.prewarm import UI_THUMB_SIZES
        from storygraph.ui_qt.sprite_grid import THUMB_H, THUMB_W
        assert (THUMB_W, THUMB_H) in [tuple(s) for s in UI_THUMB_SIZES], (
            f'立绘卡要的是 {(THUMB_W, THUMB_H)}，预热清单里没有：'
            f'{UI_THUMB_SIZES}')


class TestImagePaths:
    def test_it_lists_existing_images_only(self, tmp_path):
        from storygraph.app.prewarm import image_paths
        from storygraph.assets.asset_registry import AssetRegistry
        root = tmp_path / 'proj'
        a = _png(root / 'game' / 'images' / 'ling' / 'happy.png')
        b = _png(root / 'game' / 'images' / 'bg' / 'classroom.png')
        reg = AssetRegistry()
        reg.images = {
            'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling',
                           'Path': 'game/images/ling/happy.png'},
            'bg_classroom': {'ID': 'bg_classroom', 'Tag': 'bg',
                             'Path': 'game/images/bg/classroom.png'},
            'ghost': {'ID': 'ghost', 'Tag': 'ui',
                      'Path': 'game/images/nope/ghost.png'},
        }
        got = image_paths(reg, root)
        assert sorted(Path(p).name for p in got) == ['classroom.png', 'happy.png']
        assert Path(a) in [Path(p) for p in got]
        assert Path(b) in [Path(p) for p in got]

    def test_it_dedupes_the_same_file(self, tmp_path):
        """两行指向同一个文件（别名）时只预热一次。"""
        from storygraph.app.prewarm import image_paths
        from storygraph.assets.asset_registry import AssetRegistry
        root = tmp_path / 'proj'
        _png(root / 'game' / 'images' / 'x.png')
        reg = AssetRegistry()
        reg.images = {
            'a': {'ID': 'a', 'Path': 'game/images/x.png'},
            'b': {'ID': 'b', 'Path': 'game/images/x.png'},
        }
        assert len(image_paths(reg, root)) == 1


class TestPrewarm:
    def test_it_fills_the_disk_cache_for_every_size(self, cache_dir, tmp_path):
        from storygraph.app.prewarm import prewarm
        paths = [_png(tmp_path / 'src' / f'{i}.png') for i in range(3)]
        res = prewarm(paths, sizes=((16, 24), (24, 36)))
        assert res['total'] == 6 and res['done'] == 6, res
        assert res['failed'] == 0, res
        assert len(list(cache_dir.rglob('*.png'))) == 6, '缓存条数不对'

    def test_progress_goes_up_and_ends_at_total(self, cache_dir, tmp_path):
        from storygraph.app.prewarm import prewarm
        paths = [_png(tmp_path / 'src' / f'{i}.png') for i in range(4)]
        seen = []
        res = prewarm(paths, sizes=((16, 24),),
                      on_progress=lambda d, t: seen.append((d, t)))
        assert seen, '一次进度都没回调'
        assert seen == sorted(seen), '进度必须单调不减'
        assert seen[-1] == (res['done'] + res['failed'], res['total'])
        assert all(t == 4 for _d, t in seen), '总数报错了'

    def test_a_broken_file_is_counted_not_raised(self, cache_dir, tmp_path):
        from storygraph.app.prewarm import prewarm
        good = _png(tmp_path / 'src' / 'good.png')
        bad = tmp_path / 'src' / 'bad.png'
        bad.write_bytes(b'not an image at all')
        res = prewarm([good, bad], sizes=((16, 24),))
        assert res['failed'] == 1, res
        assert res['done'] == 1, res

    def test_should_stop_makes_it_quit_early(self, cache_dir, tmp_path):
        from storygraph.app.prewarm import prewarm
        paths = [_png(tmp_path / 'src' / f'{i}.png') for i in range(6)]
        res = prewarm(paths, sizes=((16, 24),), should_stop=lambda: True)
        assert res['stopped'] is True
        assert res['done'] + res['failed'] < 6, (
            '说了要停就必须真的少做（否则用户只能干等）')

    def test_an_empty_list_is_not_an_error(self, cache_dir):
        from storygraph.app.prewarm import prewarm
        res = prewarm([], sizes=((16, 24),))
        assert res['total'] == 0 and res['done'] == 0

    def test_the_second_run_is_served_from_the_disk_cache(self, cache_dir,
                                                          tmp_path):
        """
        预热的意义就在这儿：第二次是**缓存命中**。

        ⚠ 判据用"缓存文件真的在"，不用墙钟时间 —— 小图（900×1200 只有
        十几毫秒）计时噪声比差别还大，实测第一次 0.015s / 第二次 0.016s
        就红了。要证明的是"第二次不解码了"，不是"第二次更快多少"。
        """
        from storygraph.app.prewarm import prewarm
        from PIL import Image
        big = tmp_path / 'src' / 'big.png'
        big.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGBA', (900, 1200), (10, 20, 30, 255)).save(big)
        assert not list(cache_dir.rglob('*.png')), '前置条件：缓存是空的'
        prewarm([big], sizes=((64, 96),))
        cached = list(cache_dir.rglob('*.png'))
        assert len(cached) == 1, f'第一次没写进缓存：{cached}'
        first_mtime = cached[0].stat().st_mtime_ns
        res = prewarm([big], sizes=((64, 96),))
        assert res['done'] == 1 and res['failed'] == 0, res
        again = list(cache_dir.rglob('*.png'))
        assert len(again) == 1 and again[0].stat().st_mtime_ns == first_mtime, \
            '第二次又写了一遍缓存（说明没命中）'
