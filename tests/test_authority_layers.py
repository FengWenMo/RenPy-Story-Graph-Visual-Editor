"""
tests/test_authority_layers.py
==============================
**真源与优先级的层序表**：`core/authority.py` 与设计决策文档是**同一份决定**。

用户 2026-10-31 的原话：

> 现在查看真源与优先级的设计决策表，并考虑一下这个我挖出来的 outfit 在
> 两个近乎克隆项目内不统一的 bug 引申出的优先级问题……至于那个待定项：
> 我倾向于先做哈希，先不做 base！但这个 base 可以以后再做……必须保存好这个选项！

这份测试钉三件事：

  1. **层序常量本身**：数字小的赢、精确覆盖 > 项目声明 > glob 规则；
  2. **`Identity.source` 必须能翻回层号**（"这条结论来自哪一层"是 P0-3 的
     前提）—— 新增一个 source 不登记就红；
  3. **文档 §4.3 的层序表与代码逐条相同** —— 文档和代码各说各的，正是这次
     "两个项目给出不同 outfit"争议的根源；两源不一致必须**报出来**。

为什么不是"行为测试就够了"：层序是**数据**，行为测试只能覆盖到想得到的那几条；
常量对不上时，代码可能仍然"自洽地"错着（旧代码就是这样：注释说要让声明赢，
实际映射先跑，两边都不报错）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from storygraph.assets import asset_identity
from storygraph.core.authority import (AUTHORITY, DOMAINS, IDENTITY_ORDER,
                                       SOURCE_LAYER, covers_others, layer_of,
                                       rank, wins)

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / 'docs' / '真源与优先级_设计决策_2026-10-31.md'


class TestTheTableItself:
    def test_数字小的赢(self):
        assert rank('engine') < rank('declared')
        assert rank('declared') < rank('mapping-rule')

    def test_精确覆盖压过声明_声明压过规则(self):
        """
        2026-10-31 的第一条判断（用户实测的"outfit 两个项目不一样"）：

          · 人**点名某一张图**（`images` 表的精确覆盖）→ 可以改项目声明；
          · **glob 规则**（"这一类大概都这样"）→ 只补空缺，改不了声明。

        这条如果反了，症状是"一条 `game/**` 规则把项目自定义的图像名整片改掉"，
        而且没有任何地方会报。
        """
        assert wins('mapping-exact', 'declared')
        assert wins('declared', 'mapping-rule')
        assert wins('mapping-exact', 'mapping-rule')
        assert not wins('mapping-rule', 'declared')

    def test_引擎是天花板(self):
        """引擎说这行非法，映射不能让一行非法代码变合法。"""
        assert rank('engine') == 0
        assert all(rank('engine') < rank(x) for x in AUTHORITY if x != 'engine')

    def test_规则与登记只补空缺(self):
        """
        `covers_others` 是"赢的时候允不允许压过别人"的判据：
        规则/登记/推断赢，是因为**别处没说话**，不是因为它们更权威。
        """
        assert covers_others('engine')
        assert covers_others('declared')
        assert covers_others('mapping-exact')
        for layer in ('mapping-rule', 'registry', 'inferred', 'unknown'):
            assert not covers_others(layer), layer

    def test_认不出的层落到最弱(self):
        """写错的 source 不许静默赢过别人，也不许炸界面。"""
        assert rank('') == rank('unknown')
        assert rank('mapping-exactt') == rank('unknown')

    def test_判定顺序就是层序(self):
        """`IDENTITY_ORDER` 必须是 `AUTHORITY` 去掉 `engine` 后的完整顺序。"""
        expect = tuple(k for k, _ in sorted(AUTHORITY.items(),
                                           key=lambda kv: kv[1])
                       if k != 'engine')
        assert expect == IDENTITY_ORDER


class TestSourceCanBeTracedBack:
    def test_每个_source都登记了层号(self):
        """`Identity.source` 的每个取值都必须能翻回层（新增一个不登记就红）。"""
        assert set(asset_identity.SOURCES) == set(SOURCE_LAYER), (
            '新增了 source 却没在 core/authority.SOURCE_LAYER 登记 —— '
            '界面上"这条结论来自哪一层"就答不出来了')

    def test_source转层号(self):
        assert layer_of('declared') == 'declared'
        assert layer_of('mapping') == 'mapping-exact'
        assert layer_of('mapping-rule') == 'mapping-rule'
        assert layer_of('tag-column') == 'registry'
        assert layer_of('folder') == 'inferred'
        assert layer_of('stem') == 'inferred'
        assert layer_of('没这个') == 'unknown'

    def test_四域各有唯一所有者(self):
        """真源按**域**分：一个事实只有一个所有者，其余角色写清楚了才算数。"""
        assert set(DOMAINS) == {'A', 'B', 'C', 'D'}
        for key, spec in DOMAINS.items():
            assert spec['owner'], key
            assert spec['owns'] and spec['others'], key
        assert DOMAINS['A']['owner'] == '.rpy'
        assert '编辑面' in DOMAINS['A']['others']


def _doc_authority() -> dict:
    """从设计文档里读出 §4.3 的 `AUTHORITY` 代码块（读不到 = 文档失了这节）。"""
    assert DOC.is_file(), f'设计决策文档不见了：{DOC}'
    text = DOC.read_text(encoding='utf-8')
    block = re.search(r'```python\n(.*?AUTHORITY\s*=\s*\{.*?\n\})\s*```',
                      text, re.S)
    assert block, ('设计决策文档里找不到 §4.3 的 AUTHORITY 代码块 —— '
                   '层序表不许只活在代码里')
    pairs = re.findall(r"^\s*'([\w-]+)':\s*(\d+)\s*,", block.group(1),
                       re.MULTILINE)
    assert pairs, block.group(1)[:200]
    return {name: int(value) for name, value in pairs}


class TestDocAndCodeAgree:
    def test_文档的层序表与代码逐条相同(self):
        """
        两源不一致**必须报出来**（这条正是本次争议的教训）。

        以前文档写"精确覆盖排在项目声明之上"，表里却给它更大的数字
        （= 更弱的权威），而代码按第三种顺序跑 —— 三处各说各的，谁都不报。
        """
        doc = _doc_authority()
        assert doc == AUTHORITY, (
            '文档与代码的层序表不一致：\n'
            f'  文档：{doc}\n  代码：{AUTHORITY}\n'
            '改任何一边都要同步另一边（`docs/真源与优先级_设计决策_2026-10-31.md` §4.3）')

    def test_文档说明白谁压谁(self):
        text = DOC.read_text(encoding='utf-8')
        assert '规则只补空缺；覆盖必须精确' in text, (
            '§六 6.2 的结论不许删 —— 它是"glob 规则不改声明"的唯一出处')


if __name__ == '__main__':                      # 本机没 pytest 时的兜底
    import sys
    failed = 0
    for cls in (TestTheTableItself, TestSourceCanBeTracedBack,
                TestDocAndCodeAgree):
        for name in dir(cls):
            if not name.startswith('test_'):
                continue
            try:
                getattr(cls(), name)()
                print(f'ok   {cls.__name__}.{name}')
            except Exception as e:              # noqa: BLE001
                failed += 1
                print(f'FAIL {cls.__name__}.{name}: {e}')
    sys.exit(1 if failed else 0)
