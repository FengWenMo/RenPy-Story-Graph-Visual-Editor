"""
tests/test_stage_render_memory.py
=================================
**高分辨率图的解码上限 + 缓存字节预算**（2026-10-30，⑦ P0）。

背景（用户二轮反馈里的"高分辨率图可能出问题"）：真实项目立绘 4320×7680
（RGBA 132.7MB），选择器缩略图冷缓存单张 781ms / 峰值 +134MB。舞台渲染
原来的 `@lru_cache(maxsize=48)` 按**张数**淘汰 —— 如果用户把 bg/CG 换成
8K，理论最坏 48×132.7MB = **6.4GB**。现在两层一起收：

  1. `_load_scaled` 只按**画布可见区域**解码：8K 背景无 at 时贴左上，
     缓存里留的是屏幕大小（8.3MB），不是整图（132.7MB）；
  2. 缓存按**字节预算**淘汰（默认 256MB，环境变量可调），单张超预算不缓存。

判据用合成大图（不依赖真机素材），断言"请求的解码尺寸"而不是耗时。
"""

from __future__ import annotations

import pytest

from PIL import Image, ImageDraw

from storygraph.app import stage_render as R
from storygraph.app.stage import StageFrame

pytestmark = pytest.mark.skipif(not R.available(), reason='没装 Pillow')

SCREEN = (800, 450)
BIG = (2000, 3000)          # 比 8K 小、但同样"原图远大于屏幕"


GUI = '''init python:
    gui.init(800, 450)
'''

PNG_DECL = 'image bg big = "images/big.png"\n'


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """每个用例都从空缓存、默认预算开始（预算/环境变量互不污染）。"""
    monkeypatch.delenv(R.CACHE_BYTES_ENV, raising=False)
    monkeypatch.delenv(R.CACHE_MB_ENV, raising=False)
    R.cache_clear()
    R.configure_cache(None)
    yield
    R.cache_clear()
    R.configure_cache(None)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / 'proj'
    game = root / 'game'
    (game / 'images').mkdir(parents=True)
    (game / 'gui.rpy').write_text(GUI, encoding='utf-8')
    (game / 'script.rpy').write_text(PNG_DECL +
                                     'label start:\n    return\n',
                                     encoding='utf-8')
    img = Image.new('RGBA', BIG, (10, 20, 30, 255))
    ImageDraw.Draw(img).rectangle((0, 0, 31, 31), fill=(255, 255, 255, 255))
    img.save(game / 'images' / 'big.png')
    return root


def _frame(**kw):
    return StageFrame(index=0, line_id='t.0001', label='t', speaker='',
                      text='', scene='bg big', **kw)


class TestDecodeIsCappedToTheScreen:
    def test_background_decode_request_is_capped(self, project, monkeypatch):
        calls = []
        real = R._load_scaled

        def spy(path, mtime, w, h, crop=None, resample=None, full=None):
            calls.append((int(w), int(h), crop))
            return real(path, mtime, w, h, crop=crop, resample=resample,
                        full=full)

        monkeypatch.setattr(R, '_load_scaled', spy)
        got = R.render_frame(_frame(), project_root=project)
        assert got is not None and got.image is not None
        assert calls, '背景没有走 _load_scaled？'
        for w, h, _crop in calls:
            assert w <= SCREEN[0] and h <= SCREEN[1], (
                f'解码目标没有封顶：请求 {w}×{h}（屏幕 {SCREEN}）')
            assert w * h <= SCREEN[0] * SCREEN[1]

    def test_semantics_are_still_crop_not_scale(self, project):
        """解码封顶不能把"裁切"变成"缩放"：标记点必须还在原位置。"""
        got = R.render_frame(_frame(), project_root=project)
        assert got.background_box == (0.0, 0.0) + BIG
        px = got.image.load()
        assert px[10, 10] == (255, 255, 255, 255)
        assert px[100, 100] == (10, 20, 30, 255)

    def test_cache_holds_the_visible_block_not_the_whole_image(self, project):
        R.cache_clear()
        R.render_frame(_frame(), project_root=project)
        info = R.cache_info()
        assert info['entries'] >= 1
        natural_bytes = BIG[0] * BIG[1] * 4
        screen_bytes = SCREEN[0] * SCREEN[1] * 4
        assert info['bytes'] <= screen_bytes, (
            f'缓存了整图：{info["bytes"] / 1e6:.1f}MB（整图 {natural_bytes / 1e6:.1f}MB）')


class TestByteBudgetCache:
    def _img(self, folder, name):
        """一张 100×100 的图 = 40,000 字节（RGBA）。"""
        path = folder / name
        Image.new('RGBA', (100, 100), (1, 2, 3, 255)).save(path)
        return str(path)

    def test_budget_evicts_the_lru_entry(self, tmp_path):
        budget = 100_000
        assert R.configure_cache(budget) == budget
        a = self._img(tmp_path, 'a.png')
        b = self._img(tmp_path, 'b.png')
        c = self._img(tmp_path, 'c.png')
        ia = R._load_scaled(a, R._mtime(a), 100, 100)
        ib = R._load_scaled(b, R._mtime(b), 100, 100)
        ic = R._load_scaled(c, R._mtime(c), 100, 100)
        assert ia is not None and ib is not None and ic is not None
        info = R.cache_info()
        assert info['budget'] == budget
        assert info['bytes'] <= budget, info
        # 40KB/张 × 3 张 = 120KB > 100KB → 最早那张（a）必须被淘汰
        again = R._load_scaled(a, R._mtime(a), 100, 100)
        assert again is not ia, '最早的那张没有被淘汰'

    def test_single_entry_larger_than_budget_is_not_cached(self, tmp_path):
        R.configure_cache(1_000)
        path = self._img(tmp_path, 'huge.png')
        img = R._load_scaled(path, R._mtime(path), 100, 100)
        assert img is not None and img.size == (100, 100)
        info = R.cache_info()
        assert info['entries'] == 0 and info['bytes'] == 0

    def test_budget_can_come_from_the_environment(self, tmp_path,
                                                  monkeypatch):
        monkeypatch.setenv(R.CACHE_BYTES_ENV, str(2 * 1024 * 1024))
        R.configure_cache(None)              # 回到环境变量
        assert R.cache_info()['budget'] == 2 * 1024 * 1024
        # MB 变量也认（给手调的人少按几个零）
        monkeypatch.delenv(R.CACHE_BYTES_ENV)
        monkeypatch.setenv(R.CACHE_MB_ENV, '3')
        R.configure_cache(None)
        assert R.cache_info()['budget'] == 3 * 1024 * 1024

    def test_default_budget_is_256_mb(self, monkeypatch):
        monkeypatch.delenv(R.CACHE_BYTES_ENV, raising=False)
        monkeypatch.delenv(R.CACHE_MB_ENV, raising=False)
        R.configure_cache(None)
        assert R.DEFAULT_CACHE_BYTES == 256 * 1024 * 1024
        assert R.cache_info()['budget'] == R.DEFAULT_CACHE_BYTES

    def test_clear_resets_entries_and_counters(self):
        R.configure_cache(1_000_000)
        info = R.cache_info()
        assert info['entries'] == 0 and info['bytes'] == 0
        assert info['hits'] == 0 and info['misses'] == 0


class TestClipBox:
    def test_box_is_clipped_to_the_screen(self):
        got = R._clip_to_screen(0, 0, 2000, 3000, SCREEN)
        assert got is not None
        px, py, pw, ph, crop, fw, fh = got
        assert (px, py, pw, ph) == (0, 0, SCREEN[0], SCREEN[1])
        assert crop == (0, 0, SCREEN[0], SCREEN[1])
        assert (fw, fh) == (2000, 3000)

    def test_negative_offset_positive_crop(self):
        got = R._clip_to_screen(-100, -50, 400, 300, SCREEN)
        assert got is not None
        px, py, pw, ph, crop, _fw, _fh = got
        assert (px, py, pw, ph) == (0, 0, 300, 250)
        assert crop == (100, 50, 400, 300)

    def test_fully_offscreen_is_none(self):
        assert R._clip_to_screen(-500, 0, 100, 100, SCREEN) is None
        assert R._clip_to_screen(0, -500, 100, 100, SCREEN) is None
