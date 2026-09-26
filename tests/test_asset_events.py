import pytest

import pytest

"""
tests/test_asset_events.py
==========================
事件总线、文件监听、确认闸门测试。

文件监听用短 interval + 手动 _tick 驱动，避免测试真的睡眠等待。
"""

import time

from asset_events import (
    AssetEventType, AssetEvent, AssetEventBus,
    AssetFileWatcher, ConfirmationGate, ConfirmationRequest,
)


# ------------------------------------------------------------------
#  事件总线
# ------------------------------------------------------------------

class TestEventBus:
    def test_subscriber_receives_event(self):
        bus = AssetEventBus()
        got = []
        bus.subscribe(AssetEventType.TABLE_RELOADED, lambda e: got.append(e))
        bus.publish(AssetEvent(AssetEventType.TABLE_RELOADED))
        assert len(got) == 1

    def test_other_types_not_delivered(self):
        bus = AssetEventBus()
        got = []
        bus.subscribe(AssetEventType.TABLE_RELOADED, lambda e: got.append(e))
        bus.publish(AssetEvent(AssetEventType.ASSET_ADDED))
        assert got == []

    def test_wildcard_subscriber(self):
        bus = AssetEventBus()
        got = []
        bus.subscribe(None, lambda e: got.append(e))
        bus.publish(AssetEvent(AssetEventType.ASSET_ADDED))
        bus.publish(AssetEvent(AssetEventType.TABLE_RELOADED))
        assert len(got) == 2

    def test_unsubscribe(self):
        bus = AssetEventBus()
        got = []
        cb = lambda e: got.append(e)
        bus.subscribe(AssetEventType.ASSET_ADDED, cb)
        bus.unsubscribe(AssetEventType.ASSET_ADDED, cb)
        bus.publish(AssetEvent(AssetEventType.ASSET_ADDED))
        assert got == []

    def test_unsubscribe_unknown_is_safe(self):
        AssetEventBus().unsubscribe(AssetEventType.ASSET_ADDED, lambda e: None)

    def test_callback_exception_isolated(self):
        """一个订阅者抛异常不应影响其他订阅者。"""
        bus = AssetEventBus()
        got = []

        def boom(e):
            raise RuntimeError('boom')

        bus.subscribe(AssetEventType.ASSET_ADDED, boom)
        bus.subscribe(AssetEventType.ASSET_ADDED, lambda e: got.append(e))
        bus.publish(AssetEvent(AssetEventType.ASSET_ADDED))
        assert len(got) == 1

    def test_subscriber_can_subscribe_inside_callback(self):
        """回调中修改订阅关系不应死锁。"""
        bus = AssetEventBus()
        second_calls = []

        def first(e):
            bus.subscribe(AssetEventType.ASSET_REMOVED,
                          lambda ev: second_calls.append(ev))

        bus.subscribe(AssetEventType.ASSET_ADDED, first)
        bus.publish(AssetEvent(AssetEventType.ASSET_ADDED))
        bus.publish(AssetEvent(AssetEventType.ASSET_REMOVED))
        assert len(second_calls) == 1


# ------------------------------------------------------------------
#  文件监听
# ------------------------------------------------------------------

@pytest.fixture
def collect_changes():
    """on_change 的回调签名是 (changes: list)，此处摊平成单条记录。"""
    changes = []

    def extend(new_changes):
        changes.extend(new_changes)

    return changes, extend


class TestFileWatcher:
    def test_initial_scan_no_events(self, tmp_path, collect_changes):
        changes, on_change = collect_changes
        (tmp_path / 'a.png').write_bytes(b'1')
        w = AssetFileWatcher([tmp_path], on_change, interval=0.01)
        # 模拟 start() 的首次扫描：只建基线，不触发事件
        w._snapshot = w._scan_all()
        w._pending = dict(w._snapshot)
        w._pending_since = None
        (tmp_path / 'b.png').write_bytes(b'1')
        w._tick()          # 第一轮：发现变化，等待稳定
        assert changes == []
        w._tick()          # 第二轮：与上一轮一致 → 提交
        assert [str(c[0]) for c in changes] == [str(tmp_path / 'b.png')]
        assert changes[0][1] == 'added'

    def test_modification_detected(self, tmp_path, collect_changes):
        changes, on_change = collect_changes
        f = tmp_path / 'a.png'
        f.write_bytes(b'1')
        w = AssetFileWatcher([tmp_path], on_change, interval=0.01)
        w._snapshot = w._scan_all()
        w._pending = dict(w._snapshot)
        time.sleep(0.01)
        f.write_bytes(b'22222')
        w._tick()
        w._tick()
        assert changes and changes[0][1] == 'modified'

    def test_removal_detected(self, tmp_path, collect_changes):
        changes, on_change = collect_changes
        f = tmp_path / 'a.png'
        f.write_bytes(b'1')
        w = AssetFileWatcher([tmp_path], on_change, interval=0.01)
        w._snapshot = w._scan_all()
        w._pending = dict(w._snapshot)
        f.unlink()
        w._tick()
        w._tick()
        assert changes and changes[0][1] == 'removed'

    def test_ignored_prefix_files_not_scanned(self, tmp_path):
        (tmp_path / '~$tmp.xlsx').write_bytes(b'1')
        (tmp_path / 'keep.png').write_bytes(b'1')
        w = AssetFileWatcher([tmp_path], lambda c: None, interval=0.01)
        snapshot = w._scan_all()
        assert len(snapshot) == 1

    def test_extension_filter(self, tmp_path):
        (tmp_path / 'a.png').write_bytes(b'1')
        (tmp_path / 'b.txt').write_bytes(b'1')
        w = AssetFileWatcher([tmp_path], lambda c: None,
                             interval=0.01, extensions={'.png'})
        assert len(w._scan_all()) == 1

    def test_missing_root_tolerated(self, tmp_path):
        w = AssetFileWatcher([tmp_path / 'nope'], lambda c: None)
        assert w._scan_all() == {}

    def test_hidden_dirs_pruned(self, tmp_path):
        (tmp_path / '.git').mkdir()
        (tmp_path / '.git' / 'x.png').write_bytes(b'1')
        (tmp_path / 'ok.png').write_bytes(b'1')
        w = AssetFileWatcher([tmp_path], lambda c: None, interval=0.01)
        assert len(w._scan_all()) == 1

    def test_start_stop_lifecycle(self, tmp_path):
        (tmp_path / 'a.png').write_bytes(b'1')
        w = AssetFileWatcher([tmp_path], lambda c: None, interval=0.05)
        w.start()
        assert w.is_running()
        w.stop(timeout=2.0)
        assert not w.is_running()

    def test_double_start_is_idempotent(self, tmp_path):
        w = AssetFileWatcher([tmp_path], lambda c: None, interval=0.05)
        w.start()
        t1 = w._thread
        w.start()
        assert w._thread is t1
        w.stop(timeout=2.0)

    def test_stability_timeout_forces_commit(self, tmp_path, collect_changes):
        """
        文件持续变化导致永不"稳定"时，超过 MAX_PENDING_TICKS * interval
        必须强制提交，否则用户会看到修改永远不生效。
        """
        changes, on_change = collect_changes
        (tmp_path / 'a.png').write_bytes(b'1')
        w = AssetFileWatcher([tmp_path], on_change, interval=0.01)
        w.MAX_PENDING_TICKS = 0     # 立即超时，避免测试真的等待
        w._snapshot = w._scan_all()
        w._pending = dict(w._snapshot)
        for i in range(4):
            (tmp_path / f'x{i}.png').write_bytes(b'1')
            w._tick()
        assert changes


# ------------------------------------------------------------------
#  确认闸门
# ------------------------------------------------------------------

class TestConfirmationGate:
    def test_delete_needs_confirmation(self):
        gate = ConfirmationGate()
        req = gate.needs_confirmation('removed', ref_count=0)
        assert req is not None and req.reason == 'delete'

    def test_rename_under_threshold_is_silent(self):
        gate = ConfirmationGate(rename_threshold=5)
        assert gate.needs_confirmation('renamed', ref_count=3) is None

    def test_rename_over_threshold_asks(self):
        gate = ConfirmationGate(rename_threshold=5)
        req = gate.needs_confirmation('renamed', ref_count=6)
        assert req is not None and '6' in req.summary

    def test_script_patch_default_silent(self):
        gate = ConfirmationGate()
        assert gate.needs_confirmation('script_patch') is None

    def test_script_patch_opt_in(self):
        gate = ConfirmationGate(confirm_script_patch=True)
        assert gate.needs_confirmation('script_patch') is not None

    def test_ask_default_true_without_callback(self):
        gate = ConfirmationGate()
        assert gate.ask(ConfirmationRequest('delete', 'x', {})) is True

    def test_ask_uses_callback(self):
        gate = ConfirmationGate(ask_callback=lambda r: False)
        assert gate.ask(ConfirmationRequest('delete', 'x', {})) is False

    def test_ask_callback_exception_returns_false(self):
        def boom(r):
            raise ValueError('boom')
        gate = ConfirmationGate(ask_callback=boom)
        assert gate.ask(ConfirmationRequest('delete', 'x', {})) is False

    def test_unknown_change_type_silent(self):
        assert ConfirmationGate().needs_confirmation('whatever') is None
