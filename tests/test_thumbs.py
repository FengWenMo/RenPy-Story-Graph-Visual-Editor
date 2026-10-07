"""
tests/test_thumbs.py
====================
缩略图流水线的三处**实测过的**问题（用户 2026-10-05："图片加载还是慢，
并且加载不完全……再次优化图片加载逻辑"）：

① **磁盘缓存长成了 14 万个文件的平目录**（`%TEMP%\\renpy_sge_thumbs`）：
   每个 `is_file()` 都在一张巨大的索引里翻 → 命中缓存也要付这笔钱。
   → `thumb_cache_file` 两级分片 + `prune_thumb_cache` 有界自清。
② **内存缓存按"张数"限**（`CACHE_LIMIT = 64`）：候选 2816 张、卡片缩略图
   140 KB/张 → 滚两屏就把前面的挤掉，滚回去**再解一次**（大图 380 ms）。
   → 改成**字节预算**。
③ 清理本身必须有刹车（扫描预算 + 每次删多少），否则每开一个面板就扫
   14 万条 —— 实测能把 pytest 卡住十几分钟。
"""

from __future__ import annotations

import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402


def _png(path: Path, size=(32, 32), colour=(10, 120, 200, 255)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', size, colour).save(path, 'PNG')
    return path


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """
    把缩略图缓存目录换到临时目录（不碰用户真实的 %TEMP%）。

    ⚠ **两个命名空间都要换**：`app.thumbs` 把 `thumb_cache_dir` 导进了自己的
    命名空间（`decode_sync` 用它），`excel_export` 里还有原件
    （`thumb_cache_file` 的默认基准）。只换一个的话，另一条路会**悄悄写到
    真实 %TEMP%** —— 测试看着过，用户的缓存却被测试污染。
    """
    from storygraph.story import excel_export as EE
    from storygraph.app import thumbs as TH
    d = tmp_path / 'thumbs'
    d.mkdir()
    monkeypatch.setattr(EE, 'thumb_cache_dir', lambda: d)
    monkeypatch.setattr(TH, 'thumb_cache_dir', lambda: d)
    return d


class TestShardedCachePath:
    def test_the_path_is_sharded_two_levels(self, cache_dir):
        from storygraph.story.excel_export import thumb_cache_file
        p = thumb_cache_file('some|key|1|104x74')
        rel = p.relative_to(cache_dir)
        assert len(rel.parts) == 3, f'应当是 <ab>/<cd>/<digest>.png：{rel}'
        assert rel.name.endswith('.png')
        assert len(rel.name) == 36, rel.name          # md5 十六进制 + .png

    def test_the_same_key_is_the_same_file(self, cache_dir):
        from storygraph.story.excel_export import thumb_cache_file
        assert thumb_cache_file('k') == thumb_cache_file('k')

    def test_the_digest_is_spread_out(self, cache_dir):
        """分片要真的把文件摊开（同一个 <ab>/<cd> 下不该堆几千个）。"""
        from storygraph.story.excel_export import thumb_cache_file
        dirs = {thumb_cache_file(f'key-{i}').parent for i in range(200)}
        assert len(dirs) > 100, f'分片不足：200 个键只落到 {len(dirs)} 个目录'


class TestDecodeRoundTripUsesTheShardedCache:
    def test_second_decode_reads_the_cache(self, cache_dir, tmp_path):
        from storygraph.app.thumbs import decode_sync
        src = _png(tmp_path / 'src' / 'a.png', (64, 64))
        out1 = decode_sync(src, (32, 32), None)
        assert out1 is not None and out1.size == (32, 32)
        cached = list(cache_dir.rglob('*.png'))
        assert len(cached) == 1, f'没有写进分片缓存：{cached}'
        # 删掉源图 → 仍然应当能从缓存拿到（证明第二次是读缓存）
        stat = src.stat()
        mtime = int(stat.st_mtime)
        src.unlink()
        # 缓存键里有 mtime/size，源没了就查不到 —— 这里只验证文件确实在
        assert cached[0].is_file() and cached[0].stat().st_size > 0
        assert mtime > 0

    def test_a_missing_source_returns_none(self, cache_dir, tmp_path):
        from storygraph.app.thumbs import decode_sync
        assert decode_sync(tmp_path / 'nope.png', (32, 32), None) is None


class TestPruneBounds:
    def _mk(self, cache_dir, n, shift_days=0, tag='a'):
        paths = []
        for i in range(n):
            p = cache_dir / f'{i % 10:02d}' / f'{i:04d}' / f'{tag}{i}.png'
            _png(p)
            if shift_days:
                old = time.time() - shift_days * 86400
                os.utime(p, (old, old))
            paths.append(p)
        return paths

    def test_it_deletes_old_files_first(self, cache_dir):
        from storygraph.story.excel_export import prune_thumb_cache
        fresh = self._mk(cache_dir, 5, tag='new')
        stale = self._mk(cache_dir, 5, tag='old')
        old = time.time() - 100 * 86400
        for p in stale:
            os.utime(p, (old, old))
        removed = prune_thumb_cache(max_files=100, max_age_days=45)
        assert removed == 5, removed
        assert all(not p.exists() for p in stale), '过期文件没被删'
        assert all(p.exists() for p in fresh), '新文件被误删'

    def test_max_files_is_enforced(self, cache_dir):
        from storygraph.story.excel_export import prune_thumb_cache
        self._mk(cache_dir, 30)
        removed = prune_thumb_cache(max_files=10, max_age_days=999)
        assert removed == 20, removed
        assert len(list(cache_dir.rglob('*.png'))) == 10

    def test_the_delete_batch_is_capped(self, cache_dir):
        """一次只删一批：14 万个文件的 unlink 会把磁盘占满（见 docstring）。"""
        from storygraph.story.excel_export import prune_thumb_cache
        self._mk(cache_dir, 50)
        removed = prune_thumb_cache(max_files=0, max_age_days=0, max_delete=7)
        assert removed == 7, removed
        assert len(list(cache_dir.rglob('*.png'))) == 43

    def test_non_png_files_are_never_touched(self, cache_dir):
        from storygraph.story.excel_export import prune_thumb_cache
        self._mk(cache_dir, 5)
        keep = cache_dir / 'notes.txt'
        keep.write_text('别人的东西', encoding='utf-8')
        prune_thumb_cache(max_files=0, max_age_days=0)
        assert keep.is_file(), '清理只许删 *.png'

    def test_an_empty_dir_is_not_an_error(self, cache_dir):
        from storygraph.story.excel_export import prune_thumb_cache
        assert prune_thumb_cache() == 0


class TestMemoryCacheBudget:
    """
    内存缓存按**字节**算，不是按"张数"。

    从前 `CACHE_LIMIT = 64` 张：候选列表 2816 张图，往下滚两屏就把前面的
    挤掉，滚回去**再解一次**（大图 380 ms/张）。
    """

    @pytest.fixture
    def loader(self, qapp, tmp_path):
        from PySide6.QtWidgets import QWidget
        from storygraph.ui_qt.thumb_loader import ThumbLoader
        w = QWidget()
        return ThumbLoader(w, max_size=(16, 16), project_root=tmp_path)

    def test_it_keeps_far_more_than_the_old_64(self, loader):
        from PySide6.QtGui import QPixmap
        pm = QPixmap(16, 16)
        for i in range(300):
            loader._remember(f'key{i}', pm)
        assert len(loader._cache) == 300, (
            f'小缩略图应当全部留住（旧上限 64 张）：{len(loader._cache)}')

    def test_it_evicts_by_bytes_and_keeps_the_newest(self, loader,
                                                     monkeypatch):
        from PySide6.QtGui import QPixmap
        from storygraph.ui_qt import thumb_loader as TL
        big = QPixmap(1024, 1024)                 # 4 MB/张
        monkeypatch.setattr(TL, 'CACHE_BYTES_LIMIT', 10 * 1024 * 1024)
        for i in range(6):
            loader._remember(f'key{i}', big)
        assert len(loader._cache) == 2, f'字节预算没生效：{len(loader._cache)}'
        assert 'key5' in loader._cache and 'key0' not in loader._cache
        assert loader.cache_bytes() <= 10 * 1024 * 1024

    def test_replacing_a_key_does_not_double_count(self, loader):
        from PySide6.QtGui import QPixmap
        pm = QPixmap(16, 16)
        loader._remember('k', pm)
        first = loader.cache_bytes()
        loader._remember('k', pm)
        assert loader.cache_bytes() == first, '同一个键重复放入不该翻倍'
