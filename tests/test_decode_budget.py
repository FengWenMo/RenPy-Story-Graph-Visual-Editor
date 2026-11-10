# -*- coding: utf-8 -*-
"""
解码闸门：一张 8K 立绘 ~270MB，别再让 16 个线程一起解（2026-11-10）。

用户真机原话（第 39 轮）：

> 还有一点……内存占用太大了……有一次我用这个工具的时候……直接给我电脑
> 卡死机重启了……能否减少内存的占用？别什么都先存内存里……？

实测（`.pip-tmp/probe_r29_decode_peak.py`，12 张 4320×7680 立绘）：

    · 16 并发：峰值 2 751 MB
    · 4 并发：峰值 1 105 MB
    · 加闸门（预算 640 MB）：峰值 580 MB

判据钉四件事：
  1. 预算按**字节**算（大图排队、小图照常并发）；
  2. 单张超预算时，空闲就放行（绝不永远卡住）；
  3. 所有解码入口都过闸门（缩略图 / 预热 / 舞台 / 分层合成）；
  4. 预热同一张图**只解一遍**（两个尺寸从一次解码里缩出来）。
"""
from __future__ import annotations

import contextlib
import os
import threading
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture(autouse=True)
def _fresh_gate():
    from storygraph.core import decode_budget as DB
    DB.reset()
    yield
    DB.reset()


def _png(tmp_path, name='a.png', size=(90, 60)):
    from PIL import Image
    p = tmp_path / name
    Image.new('RGBA', size, (1, 2, 3, 255)).save(p)
    return p


# ----------------------------------------------------------------------
#  闸门本身
# ----------------------------------------------------------------------

class TestTheGateItself:
    def test_a_second_big_decode_waits_for_the_first(self):
        from storygraph.core.decode_budget import DecodeBudget
        gate = DecodeBudget()
        gate.configure(100)
        got = gate.acquire(60)
        assert got == 60

        done = []
        started = threading.Event()

        def worker():
            started.set()
            done.append(gate.acquire(60))
            gate.release(done[-1])

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert started.wait(1)
        time.sleep(0.08)
        assert not done, '预算不够时第二个必须等着'

        gate.release(got)
        t.join(2)
        assert done == [60], done

    def test_a_single_oversize_decode_is_allowed_when_idle(self):
        from storygraph.core.decode_budget import DecodeBudget
        gate = DecodeBudget()
        gate.configure(100)
        t0 = time.monotonic()
        got = gate.acquire(500)
        assert got == 500
        assert time.monotonic() - t0 < 0.5, '空闲时单张超预算也必须立刻放行'
        gate.release(got)
        assert gate.info()['used'] == 0

    def test_zero_budget_means_unlimited(self):
        from storygraph.core.decode_budget import DecodeBudget
        gate = DecodeBudget()
        gate.configure(0)
        for _ in range(5):
            gate.acquire(10 ** 9)
        assert gate.info()['used'] == 5 * 10 ** 9

    def test_cost_comes_from_the_file_header(self, tmp_path):
        from storygraph.core.decode_budget import PEAK_FACTOR, cost_of
        p = _png(tmp_path, size=(100, 50))
        assert cost_of(p) == int(100 * 50 * 4 * PEAK_FACTOR)
        assert cost_of(p, factor=1.0) == 100 * 50 * 4

    def test_an_unreadable_file_gets_the_fallback(self, tmp_path):
        from storygraph.core.decode_budget import FALLBACK_COST, cost_of
        bad = tmp_path / 'bad.png'
        bad.write_bytes(b'not a png')
        assert cost_of(bad) == FALLBACK_COST

    def test_env_sets_the_budget(self, monkeypatch):
        from storygraph.core import decode_budget as DB
        monkeypatch.setenv(DB.MB_ENV, '256')
        monkeypatch.delenv(DB.BYTES_ENV, raising=False)
        assert DB.budget_bytes() == 256 * 1024 * 1024
        assert DB.configure() == 256 * 1024 * 1024


# ----------------------------------------------------------------------
#  入口都过闸门
# ----------------------------------------------------------------------

def test_decode_sync_takes_a_permit(tmp_path, monkeypatch):
    from storygraph.app import thumbs
    png = _png(tmp_path)
    monkeypatch.setattr(thumbs, 'thumb_cache_dir', lambda: tmp_path / 'cache')
    seen = []

    @contextlib.contextmanager
    def fake(cost):
        seen.append(cost)
        yield

    monkeypatch.setattr(thumbs, 'decoding', fake)
    assert thumbs.decode_sync(png, (20, 20)) is not None
    assert seen and seen[0] >= 90 * 60 * 4, seen


def test_the_stage_load_takes_a_permit(tmp_path, monkeypatch):
    from storygraph.app import stage_render as S
    png = _png(tmp_path, size=(120, 80))
    seen = []
    monkeypatch.setattr(S, 'acquire',
                        lambda cost: (seen.append(cost), cost)[1])
    monkeypatch.setattr(S, 'release', lambda cost: seen.append(-cost))
    S.cache_clear()
    img = S._load_scaled(str(png), S._mtime(png), 60, 40)
    assert img is not None and img.size == (60, 40)
    assert seen and seen[0] > 0, seen
    assert -seen[0] in seen, '借的额度必须还'


def test_the_layer_fallback_takes_a_permit(tmp_path, monkeypatch):
    from storygraph.app import layer_compose as LC
    from storygraph.core import decode_budget as DB
    png = _png(tmp_path, size=(64, 64))
    seen = []
    monkeypatch.setattr(DB, 'acquire', lambda cost: seen.append(cost) or 0)
    monkeypatch.setattr(DB, 'release', lambda cost: seen.append(-cost))
    img = LC._open_full_layer(str(png))
    assert img is not None and img.size == (64, 64)
    assert seen and seen[0] > 0, seen


# ----------------------------------------------------------------------
#  并发上限 / 预热只解一遍
# ----------------------------------------------------------------------

def test_concurrency_defaults_are_capped():
    from storygraph.app.prewarm import _default_workers
    from storygraph.ui_qt.thumb_loader import default_max_in_flight
    assert default_max_in_flight() <= 4
    assert _default_workers() <= 4


def test_prewarm_decodes_each_image_only_once(tmp_path, monkeypatch):
    from storygraph.app import prewarm as P
    from storygraph.app import thumbs as T
    calls = []

    def fake(path, sizes, project_root=None):
        calls.append((str(path), tuple(tuple(s) for s in sizes)))
        return {tuple(s): True for s in sizes}

    monkeypatch.setattr(T, 'decode_sizes', fake)
    res = P.prewarm([tmp_path / 'a.png', tmp_path / 'b.png'],
                    sizes=((16, 24), (24, 36)))
    assert len(calls) == 2, calls          # 两张图、各解一次
    assert res['total'] == 4 and res['done'] == 4, res

    again = P.prewarm([tmp_path / 'a.png'], sizes=((16, 24),))
    assert len(calls) == 3 and again['total'] == 1
