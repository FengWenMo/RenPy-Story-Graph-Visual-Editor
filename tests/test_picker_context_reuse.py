"""
选择器逐张判定：**上下文只许建一次**（2026-11-03）
================================================

用户报的是"资源管理器不够健壮"，量下来先卡在这里：

    真机 1979 张图。`resolve_pick` 只要给了 `registry` 就**无条件**重建上下文
    （`build_copy_index` 1979 行两两比较 + 扫全项目 `.rpy` 建 layeredimage 模型），
    实测 97 ms/张 → 全部走一遍 3.2 分钟。而选择器就是**逐张**调它的。

    改完之后：调用方把上下文算好传进来 → **2.4 ms/张**（40×），1979 张 4.8 秒。

这个文件钉三件事（都用**调用次数**，不用墙钟 —— 时间判据在 CI 上不稳）：

  1. 传了 `context` → 一次都不许再建；
  2. 没传 `context`、但参数**齐了**（known_tags / known_chars / mapping /
     all_rows / sprite_words / row 都有）→ 也不许建（`need` 判据别被删掉）；
  3. 给了 `copy_index` → 不许再 `build_copy_index`（那是另一处平方级）。

对照组：真缺东西时**必须**建（不能为了快就不给上下文，那会静默丢口径）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import storygraph.app.picker as P                                  # noqa: E402

ROW = {'ID': 'qing_normal', 'Tag': 'qing', 'Attributes': 'normal',
       'Value': 'normal', 'Path': 'game/images/characters/qing/normal.png',
       'Category': 'character'}

FULL_CTX = {
    'known_tags': frozenset({'qing', 'lan'}),
    'known_chars': {'qing': {'display': '青'}},
    'mapping': {},
    'all_rows': {'qing_normal': ROW},
    'sprite_words': {'qing': {'normal': 'base'}},
    'copy_index': {},
}


class _Counter:
    """把 `pick_context` / `build_copy_index` 换成计数器。"""

    def __init__(self, monkeypatch):
        self.ctx_calls = 0
        self.copy_calls = 0
        self._monkeypatch = monkeypatch

    def install(self):
        def fake_ctx(*_a, **_k):
            self.ctx_calls += 1
            return dict(FULL_CTX)
        self._monkeypatch.setattr(P, 'pick_context', fake_ctx)

        import storygraph.app.picker as mod
        original = mod.build_copy_index

        def fake_copy(rows):
            self.copy_calls += 1
            return original(rows)
        self._monkeypatch.setattr(mod, 'build_copy_index', fake_copy)
        return self


def _pick(**kw):
    kw.setdefault('registry', object())        # 只用来触发"要不要建上下文"那段
    return P.resolve_pick('qing_normal', dict(ROW), **kw)


class TestContextIsNotRebuilt:
    def test_explicit_context_is_used_verbatim(self, monkeypatch):
        c = _Counter(monkeypatch).install()
        _pick(context=dict(FULL_CTX))
        assert c.ctx_calls == 0, '给了 context 还在重建'
        assert c.copy_calls == 0, '给了 copy_index 还在重建'

    def test_complete_arguments_skip_the_context(self, monkeypatch):
        """参数齐了就不建 —— 这条是防"need 判据被删掉"的。"""
        c = _Counter(monkeypatch).install()
        _pick(known_tags=frozenset({'qing'}),
              known_chars={}, mapping={},
              all_rows={'qing_normal': ROW}, sprite_words={},
              copy_index={})
        assert c.ctx_calls == 0, '参数齐了还在重建上下文（平方级回来了）'

    def test_copy_index_is_built_at_most_once(self, monkeypatch):
        """没给 copy_index 时建**一次**是应该的（这一份被这次调用复用）。"""
        c = _Counter(monkeypatch).install()
        _pick(known_tags=frozenset({'qing'}), known_chars={}, mapping={},
              all_rows={'qing_normal': ROW}, sprite_words={})
        assert c.copy_calls == 1, f'建了 {c.copy_calls} 次（平方级信号）'


class TestWhatMustStillBuild:
    """对照组：真缺东西时必须建，不许为了快就瞎猜。"""

    def test_missing_everything_builds_once(self, monkeypatch):
        c = _Counter(monkeypatch).install()
        _pick()
        assert c.ctx_calls == 1

    def test_missing_one_piece_still_builds(self, monkeypatch):
        c = _Counter(monkeypatch).install()
        _pick(known_tags=frozenset({'qing'}), known_chars={}, mapping={},
              all_rows={'qing_normal': ROW})       # 少了 sprite_words
        assert c.ctx_calls == 1

    def test_no_registry_never_builds(self, monkeypatch):
        """没有 registry（没打开项目）→ 无从建，也不许抛。"""
        c = _Counter(monkeypatch).install()
        P.resolve_pick('qing_normal', dict(ROW))
        assert c.ctx_calls == 0


class TestResultIsTheSameEitherWay:
    """加速不许改结论（这是性能改动的唯一红线）。"""

    def test_context_vs_no_context_agree(self):
        import storygraph.assets.asset_identity as AI
        rows = {'qing_normal': dict(ROW)}
        copies = AI.build_copy_index(rows)
        args = dict(known_tags=frozenset({'qing'}),
                    known_chars={'qing': {'display': '青'}},
                    mapping={}, all_rows=rows, sprite_words={})
        a = P.resolve_pick('qing_normal', dict(ROW), **args)
        b = P.resolve_pick('qing_normal', dict(ROW),
                           context=dict(args, copy_index=copies))
        assert (a.ok, a.spec) == (b.ok, b.spec), (a, b)
