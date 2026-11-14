# -*- coding: utf-8 -*-
"""
**这一维原来没写**时，新词插在哪（2026-11-14 用户报 `oh aa`）
================================================================

⚠ 与 `tests/test_attr_slot_order.py` 的分工：
  · 那一份锁"**换**一维不许改动其余维度的顺序"（2026-11-05 真机事故，原位替换）；
  · 这一份锁"这一维**原来没有词**"时插在哪 —— 追加到末尾会写出反顺序。

用户："为什么还会出现 oh aa 一类的这样顺序乱套的情况呢？……有入口，我就在
工具内操作，绝大部分时候确实是对的，但我抓到了个不太好的方式"

## 现场

真机 `chapter2_2_3.rpy` 第 17/25/33 行是 `show lan shy aa`，而同一文件
第 252/271/395 行、以及全项目另外 1269 条都是 `show lan aa shy` ——
**同一个项目里写法不一致**（Ren'Py 里属性顺序语义等价，演出没错；
但作者读脚本时会觉得"工具又乱改了"）。

## 根因

"两个词都在"的时候走原位替换，顺序永远不动 —— 这就是"绝大部分时候是对的"。
漏掉的是**这一维原来没写**：`_replace_attr_word` 在找不到 `old` 时**追加到末尾**。

    show lan shy           ← 只写了表情
    点「服装」选 aa
    → show lan shy aa      ← 追加在末尾

而卡片上「属性 1」正好是表情、「属性 2」是服装（没有 rules.json 时按引擎分组
顺序显示），于是"从左往右点"这种最自然的操作就会把顺序点反。

## 修法（三档，从强到弱）

1. **有旧词**：原位替换（既有行为，`test_attr_slot_order.py` 锁着）；
2. **这一维刚被清空**：记住它原来的下标，落值时插回去（`_slot_holes`）；
3. **这一维从来没写过**：按**这个项目自己写过的顺序**插 —— `story.attr_order`
   只数作者真写过的 `show` 行，证据 < 2 条就追加，**绝不瞎插**。
"""
from __future__ import annotations

import pytest

CHAPTER = ('label ch01:\n'
           '    show lan aa shy\n'
           '    "一。"\n'
           '    show lan aa shy at i11\n'
           '    "二。"\n'
           '    show lan clothes2 oh\n'
           '    "三。"\n'
           '    return\n')

DEFINITIONS = '''\
layeredimage lan:
    image_format 'images/characters/lan/{image}.png'
    group outfit:
        attribute aa
        attribute clothes2
    group expressions:
        attribute shy
        attribute oh
'''


def _project(tmp_path, chapter=CHAPTER, definitions=DEFINITIONS):
    from storygraph.assets.asset_registry import (_TABLE_FIELDS,
                                                  write_dict_to_excel)

    root = tmp_path / 'proj'
    tables = root / 'game' / 'asset_tables'
    tables.mkdir(parents=True, exist_ok=True)
    write_dict_to_excel(
        tables / 'characters.xlsx',
        {'lan': {'ID': 'lan', 'Name': '蓝', 'ImageTag': 'lan'}},
        _TABLE_FIELDS['character'])
    (root / 'game' / 'definitions.rpy').write_text(definitions,
                                                   encoding='utf-8')
    (root / 'game' / 'ch01.rpy').write_text(chapter, encoding='utf-8')
    return root


@pytest.fixture
def env(tmp_path):
    """(root, symbols, layer_model) —— 真 registry（带 project_root）+ 真模型。"""
    from storygraph.assets.registry_load import load_asset_registry
    from storygraph.story.beat_render import make_symbols
    from storygraph.story.layered_model import (clear_layer_model_cache,
                                                load_layer_model_cached)

    root = _project(tmp_path)
    clear_layer_model_cache()
    symbols = make_symbols(load_asset_registry(root))
    model = load_layer_model_cached(str(root))
    return root, symbols, model


# ---------------------------------------------------------------------------
#  ① 学"这个项目怎么写"：只认作者真写过的行
# ---------------------------------------------------------------------------

class TestLearnedOrder:

    def _learn(self, root, group_of):
        from storygraph.story.attr_order import learned_order

        return learned_order(root, 'lan', group_of)

    def test_服装在前就从行里学出来(self, env):
        root, _s, _m = env
        order = self._learn(root, {'aa': 'outfit', 'clothes2': 'outfit',
                                   'shy': 'expressions', 'oh': 'expressions'})
        assert order == ['outfit', 'expressions'], order

    def test_只有一条证据不够_不许猜(self, tmp_path):
        root = _project(tmp_path, chapter='label ch01:\n'
                                         '    show lan aa shy\n'
                                         '    return\n')
        assert self._learn(root, {'aa': 'outfit', 'shy': 'expressions'}) == []

    def test_没有项目根就空(self):
        from storygraph.story.attr_order import learned_order

        assert learned_order('', 'lan', {'aa': 'outfit'}) == []

    def test_单维度的行说明不了顺序(self, tmp_path):
        """只有 `show lan aa` / `show lan shy` 这种单维度的行 → 学不出顺序。"""
        root = _project(tmp_path, chapter='label ch01:\n'
                                         '    show lan aa\n'
                                         '    show lan shy\n'
                                         '    show lan clothes2\n'
                                         '    show lan oh\n'
                                         '    return\n')
        assert self._learn(root, {'aa': 'outfit', 'clothes2': 'outfit',
                                  'shy': 'expressions',
                                  'oh': 'expressions'}) == []


# ---------------------------------------------------------------------------
#  ② 插哪儿
# ---------------------------------------------------------------------------

class TestInsertIndex:

    def test_服装要插在表情前面(self):
        from storygraph.story.attr_order import insert_index

        got = insert_index(['shy'], 'outfit', ['outfit', 'expressions'],
                           {'shy': 'expressions', 'aa': 'outfit'})
        assert got == 0

    def test_表情后面没有更晚的组_就追加(self):
        from storygraph.story.attr_order import insert_index

        assert insert_index(['aa'], 'expressions',
                            ['outfit', 'expressions'],
                            {'aa': 'outfit', 'shy': 'expressions'}) is None

    def test_组不在顺序里_不乱插(self):
        from storygraph.story.attr_order import insert_index

        assert insert_index(['shy'], 'weird', ['outfit', 'expressions'],
                            {'shy': 'expressions'}) is None


# ---------------------------------------------------------------------------
#  ③ 端到端：apply_attr_slot
# ---------------------------------------------------------------------------

class TestApplyAttrSlot:

    def _apply(self, env, spec, group, value, insert_at=None):
        from storygraph.app.beats import apply_attr_slot

        _root, symbols, model = env
        return apply_attr_slot(spec, group, value, symbols=symbols,
                               layer_model=model, insert_at=insert_at)

    def test_原来没写服装_按项目惯例插到前面(self, env):
        """用户报的那条：`lan shy` 选服装 → 必须是 `lan aa shy`。"""
        assert self._apply(env, 'lan shy', 'outfit', 'aa') == 'lan aa shy'

    def test_记住的下标优先于惯例(self, env):
        assert self._apply(env, 'lan shy', 'outfit', 'aa',
                           insert_at=1) == 'lan shy aa'

    def test_原位替换不动顺序(self, env):
        assert self._apply(env, 'lan aa shy', 'outfit',
                           'clothes2') == 'lan clothes2 shy'

    def test_表情补在后面(self, env):
        assert self._apply(env, 'lan aa', 'expressions', 'oh') == 'lan aa oh'

    def test_清空这一维_只删那个词(self, env):
        assert self._apply(env, 'lan clothes2 uh', 'outfit', '') == 'lan uh'

    def test_子句不许被当成属性(self, env):
        out = self._apply(env, 'lan shy at i11', 'outfit', 'aa')
        assert out == 'lan aa shy at i11', out

    def test_认不出这个维度_原样返回(self, env):
        assert self._apply(env, 'lan shy', '没这个组', 'aa') == 'lan shy'

    def test_没有项目惯例时行为与从前一致_追加(self, tmp_path):
        """学不到顺序（证据不足）→ 追加到末尾 —— 不许凭空插。"""
        from storygraph.app.beats import apply_attr_slot
        from storygraph.assets.registry_load import load_asset_registry
        from storygraph.story.beat_render import make_symbols
        from storygraph.story.layered_model import (clear_layer_model_cache,
                                                    load_layer_model_cached)

        root = _project(tmp_path, chapter='label ch01:\n'
                                        '    show lan aa shy\n'
                                        '    return\n')
        clear_layer_model_cache()
        symbols = make_symbols(load_asset_registry(root))
        model = load_layer_model_cached(str(root))
        out = apply_attr_slot('lan shy', 'outfit', 'aa', symbols=symbols,
                              layer_model=model)
        assert out == 'lan shy aa', out
