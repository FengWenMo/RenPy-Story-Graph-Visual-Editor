"""
裁决的新鲜度（L1-2 / L1-3）
==========================

> 2026-11-05。交接文档：「`.rpy` 改过之后旧裁决会**说反话** —— 那比
> "没有裁决"更糟。」

## 三个状态

| 状态 | 意思 | 提示层 |
|---|---|---|
| `fresh` | `.rpy` 没动，裁决可用 | **引用**引擎的话 |
| `stale` | `.rpy` 变了（或老缓存没指纹） | **闭嘴**，交给声明/表/定义位 |
| `unknown` | 没问过 / 缓存坏了 | **闭嘴** |

## 为什么 `stale` 要闭嘴而不是照用

引擎裁决是按**当时的** `.rpy` 跑出来的。用户刚加了一条 `attribute aa`，
旧裁决还说"没有" —— 提示条就会在他刚修好的地方报错。**说反话比不说更糟。**

## 为什么懒算

`rpy_stamp()` 要 stat 93 个 `.rpy`：真机实测 **39–68 ms**。塞进
`Symbols.__init__` 会让每张卡片都等一次；所以 `engine_verdict_state()`
第一次问的时候才算，结果缓存在实例上。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.assets import engine_images as EI          # noqa: E402
from storygraph.ui_qt.sprite_hints import known_tag_warning  # noqa: E402

DECL = """\
layeredimage lan:
    attribute happy default: 'happy'
    attribute close_happy: 'close_happy'
"""


def _project(tmp_path, extra=''):
    root = tmp_path / 'proj'
    g = root / 'game'
    (g / 'images' / 'characters' / 'lan').mkdir(parents=True)
    (g / 'images' / 'characters' / 'lan' / 'happy.png').write_bytes(b'\x89PNG')
    (g / 'definitions.rpy').write_text(DECL + extra, encoding='utf-8')
    (g / 'script.rpy').write_text('label start:\n    return\n', encoding='utf-8')
    return root


def _write_cache(root, *, stamp_rpy, invalid=(), version=EI.CACHE_VERSION):
    """直接造一份缓存（**不跑引擎** —— 这里测的是新鲜度，不是 lint）。"""
    data = {'ok': True, 'source': 'engine', 'cached': False,
            'invalid': list(invalid), 'version': version,
            'stamp_rpy': stamp_rpy, 'stamp': {'n_rpy': 2, 'newest_rpy': 0,
                                              'n_images': 1}}
    EI.cache_path(root).write_text(json.dumps(data), encoding='utf-8')


class TestThreeStates:
    def test_unknown_without_cache(self, tmp_path):
        root = _project(tmp_path)
        assert EI.verdict_state(root) == 'unknown'

    def test_fresh_when_rpy_untouched(self, tmp_path):
        root = _project(tmp_path)
        _write_cache(root, stamp_rpy=EI.rpy_stamp(root))
        assert EI.verdict_state(root) == 'fresh'

    def test_stale_after_editing_an_rpy(self, tmp_path):
        root = _project(tmp_path)
        _write_cache(root, stamp_rpy=EI.rpy_stamp(root))
        assert EI.verdict_state(root) == 'fresh'
        (root / 'game' / 'definitions.rpy').write_text(
            DECL + "\nlayeredimage qing:\n    attribute base: 'base'\n",
            encoding='utf-8')
        assert EI.verdict_state(root) == 'stale'

    def test_stale_when_cache_has_no_stamp(self, tmp_path):
        """老缓存（v1）没有 `stamp_rpy` → 不敢说 fresh。"""
        root = _project(tmp_path)
        _write_cache(root, stamp_rpy='')
        assert EI.verdict_state(root) == 'stale'

    def test_version_mismatch_is_unknown(self, tmp_path):
        root = _project(tmp_path)
        _write_cache(root, stamp_rpy=EI.rpy_stamp(root), version=1)
        assert EI.verdict_state(root) == 'unknown'

    def test_rpy_stamp_ignores_images(self, tmp_path):
        """⚠ **加图不该让裁决过期**：判名字看注册表，不看文件。"""
        root = _project(tmp_path)
        stamp = EI.rpy_stamp(root)
        (root / 'game' / 'images' / 'characters' / 'lan' / 'added.png') \
            .write_bytes(b'\x89PNG')
        assert EI.rpy_stamp(root) == stamp

    def test_rpy_stamp_changes_with_content(self, tmp_path):
        root = _project(tmp_path)
        stamp = EI.rpy_stamp(root)
        (root / 'game' / 'script.rpy').write_text(
            'label start:\n    "x"\n    return\n', encoding='utf-8')
        assert EI.rpy_stamp(root) != stamp


class _FakeSymbols:
    """只回答提示层要问的几件事；`state` 决定引擎层开不开。

    ⚠ `declared` 默认**空**：提示层的顺序是「引擎 → 声明 → …」，
    有声明时引擎裁决会被"有声明不采信 lint"那条**主动让路**（真机 `aa`
    能跑正是这个原因）。测引擎层就必须模拟"这个 tag 没声明过"。
    """

    def __init__(self, *, state='fresh', bad=('lan|aa|happy',), declared=()):
        self._state = state
        self._bad = set(bad)
        self._declared = list(declared)

    def has(self, kind):
        return True

    def declared_attrs_of(self, tag):
        return list(self._declared) if tag == 'lan' else []

    def attrs_of_tag(self, tag):
        return []

    def has_sprites(self, tag):
        return False

    def is_actor_tag(self, tag):
        return True

    def defined_words(self):
        return []

    def engine_bad_keys(self):
        return set(self._bad)

    def engine_verdict_state(self):
        return self._state


class TestHintRespectsFreshness:
    def test_fresh_quotes_the_engine(self):
        sym = _FakeSymbols(state='fresh')
        assert 'is not an image' in known_tag_warning('lan aa happy', sym)

    @pytest.mark.parametrize('state', ['stale', 'unknown'])
    def test_stale_or_unknown_falls_back(self, state):
        """⭐ `.rpy` 改过 → 引擎那句**不再出现**，交给声明层（诚实）。"""
        sym = _FakeSymbols(state=state)
        msg = known_tag_warning('lan aa happy', sym)
        assert 'is not an image' not in msg

    def test_stale_still_reports_real_unknown_attributes(self):
        """退回声明层后，`ba` 这种**真的没有**的仍要说（不是全放过）。"""
        sym = _FakeSymbols(state='stale', declared=['happy', 'close_happy'])
        assert 'ba' in known_tag_warning('lan ba happy', sym)

    def test_legal_name_is_silent_in_every_state(self):
        for state in ('fresh', 'stale', 'unknown'):
            sym = _FakeSymbols(state=state, bad=(),
                               declared=['happy', 'close_happy'])
            assert known_tag_warning('lan close_happy', sym) == '', state


class TestSymbolsLazyState:
    def test_state_is_computed_once(self, tmp_path, monkeypatch):
        """懒算：问两次只 stat 一遍 `.rpy`（真机 39–68 ms，不能每次重算）。"""
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.dsl import Symbols
        root = _project(tmp_path)
        _write_cache(root, stamp_rpy=EI.rpy_stamp(root))
        _hit = EI.cached_fast(root)
        assert _hit.get('ok'), f'缓存读不到：{_hit} path={EI.cache_path(root)}'
        reg = AssetRegistry()
        reg.project_root = str(root)
        reg.images['lan_happy'] = {'ID': 'lan_happy', 'Tag': 'lan',
                                   'Attributes': 'happy',
                                   'Path': 'game/images/characters/lan/happy.png',
                                   'Category': 'character'}
        calls = []
        real = EI.rpy_stamp
        monkeypatch.setattr(EI, 'rpy_stamp',
                            lambda r: calls.append(r) or real(r))
        sym = Symbols(reg)
        assert sym.engine_verdict_state() == 'fresh'
        assert sym.engine_verdict_state() == 'fresh'
        assert len(calls) == 1, f'rpy_stamp 被算了 {len(calls)} 次'
