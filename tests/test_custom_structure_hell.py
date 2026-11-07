"""
**自定义结构**：5 维 `show` + `with tt form`（2026-11-05）
======================================================

用户：

> 「这可不一定！我的意思是……自定义一个新结构……这个结构是 with 后面跟两个变量，
>   比如 with tt form ，transform 也要这样，还有，show 增添多几个自定义结构，
>   变成 show 角色 动作 服装 表情 特效，一个都不许乱！」

所以"通用性"的意思是：**工具不能写死 Ren'Py 原生那六个关键字**（
`at` / `as` / `behind` / `onlayer` / `zorder` / `with`），也不能写死"三个维度"。
项目的 `storygraph.rules.json` 里写什么，工具就按什么：

```json
{"statements": [{"name": "show", "pattern": [
  {"kind": "keyword",  "text": "show"},
  {"kind": "choice",   "column": "角色",  "options": ["lan", "ye"]},
  {"kind": "choice",   "column": "动作",  "options": ["walk", "stand"]},
  {"kind": "choice",   "column": "服装",  "options": ["blue", "white"]},
  {"kind": "choice",   "column": "表情",  "options": ["smile", "cry"]},
  {"kind": "choice",   "column": "特效",  "options": ["fire", "bolt"]},
  {"kind": "optional", "text": "with"},
  {"kind": "variable", "column": "转场1"},
  {"kind": "variable", "column": "转场2"}]}]}
```

⇒ 一句 `lan walk blue smile fire with tt form` 里：

| 段 | 是什么 |
|---|---|
| `lan` | 角色（tag） |
| `walk` `blue` `smile` `fire` | **四个属性**（动作 / 服装 / 表情 / 特效） |
| `with tt form` | **子句**：`with` + **两个**变量（`tt` / `form`） |

**一个都不许乱**的意思：切"服装"这一维时，`walk` 仍在第 1 位、`fire` 仍在
第 4 位、`with tt form` 一个字节都不动。

## ⚠ 这一份的边界

`with tt form` 在**原生 Ren'Py 里跑不了**（`with` 只接一个 transition，见
`test_show_spec_engine_verified.py` 的实测）。它是**项目自定义结构** —— 工具要
**解析得对、改得准、原样写回**；"引擎肯不肯认"是另一层的事
（`verify_show_specs` 会如实说）。

所以这一份测的是**解析与改写**，引擎那一层测的是**能跑的形态**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.app import beats as B                     # noqa: E402

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64

RULES = {
    'statements': [{
        'name': 'show',
        'pattern': [
            {'kind': 'keyword', 'text': 'show'},
            {'kind': 'choice', 'column': '角色', 'options': ['lan', 'ye']},
            {'kind': 'choice', 'column': '动作', 'options': ['walk', 'stand']},
            {'kind': 'choice', 'column': '服装', 'options': ['blue', 'white']},
            {'kind': 'choice', 'column': '表情', 'options': ['smile', 'cry']},
            {'kind': 'choice', 'column': '特效', 'options': ['fire', 'bolt']},
            {'kind': 'optional', 'text': 'with'},
            {'kind': 'variable', 'column': '转场1'},
            {'kind': 'variable', 'column': '转场2'},
        ],
    }],
}

DECL = """\
layeredimage lan:
    image_format 'images/characters/lan/{image}.png'
    group act:
        attribute walk:
            'walk'
        attribute stand:
            'stand'
    group outfit:
        attribute blue:
            'blue'
        attribute white:
            'white'
    group expressions:
        attribute smile default:
            'smile'
        attribute cry:
            'cry'
    group fx:
        attribute fire:
            'fire'
        attribute bolt:
            'bolt'
"""


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / 'five'
    g = root / 'game'
    d = g / 'images' / 'characters' / 'lan'
    d.mkdir(parents=True)
    for n in ('walk', 'stand', 'blue', 'white', 'smile', 'cry', 'fire', 'bolt'):
        (d / f'{n}.png').write_bytes(PNG)
    (g / 'definitions.rpy').write_text(DECL, encoding='utf-8')
    (g / 'script.rpy').write_text('label start:\n    return\n', encoding='utf-8')
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False), encoding='utf-8')
    return root


def _sym(root):
    """造一个只有 `project_root` 的符号表（这个项目没有资源表，靠声明）。

    ⚠ `load_asset_registry()` 对**没有表**的项目返回 `None` —— 别拿它硬存
    `project_root`（第一版就是这么 `'NoneType' has no attribute` 的）。
    """
    from storygraph.assets.asset_registry import AssetRegistry
    from storygraph.story.dsl import Symbols
    reg = AssetRegistry()
    reg.project_root = str(root)
    return Symbols(reg)


class TestDeclaredStructure:
    def test_five_columns_in_the_declared_order(self, proj):
        sym = _sym(proj)
        cols = [c for c, _opts in B._declared_column_order(sym)]
        assert cols == ['角色', '动作', '服装', '表情', '特效'], cols

    def test_clause_keywords_are_native_plus_declared(self, proj):
        """⭐ 子句关键字 = **原生六个 ∪ 项目声明**（并存，不是替换）。

        用户：「原生+自定义需要并存！at 和 with 实际上是关键字优先，所以不会
        设置为变量本身，放心」
        """
        sym = _sym(proj)
        kws = B._declared_clause_keywords(sym)
        assert 'with' in kws
        for native in B.SHOW_CLAUSE_KEYWORDS:
            assert native in kws, f'原生关键字 {native} 被项目声明挤掉了'
        assert 'show' not in kws, '语句头不该被当子句' 

    def test_two_variables_after_with_both_go_to_clauses(self, proj):
        sym = _sym(proj)
        kws = B._declared_clause_keywords(sym)
        got = B.split_show_spec('lan walk blue smile fire with tt form', kws)
        assert got == ('lan', ['walk', 'blue', 'smile', 'fire'],
                       'with tt form')


class TestFiveDimensions:
    SPEC = 'lan walk blue smile fire'
    SPEC_CLAUSE = 'lan walk blue smile fire with tt form'

    @pytest.mark.parametrize('group,value', [
        ('outfit', 'white'),
        ('expressions', 'cry'),
        ('act', 'stand'),
        ('fx', 'bolt'),
    ])
    def test_only_the_target_word_changes(self, proj, group, value):
        sym = _sym(proj)
        got = B.apply_attr_slot(self.SPEC, group, value, symbols=sym)
        before, after = self.SPEC.split(), got.split()
        assert len(after) == len(before), got
        diff = [(i, a, b) for i, (a, b) in enumerate(zip(before, after))
                if a != b]
        assert len(diff) == 1, f'{self.SPEC} → {got}（改了 {len(diff)} 处）'
        assert diff[0][2] == value

    @pytest.mark.parametrize('group,value', [
        ('outfit', 'white'), ('fx', 'bolt'), ('act', 'stand'),
    ])
    def test_clause_with_two_variables_is_untouched(self, proj, group, value):
        """⭐⭐ `with tt form` **一个字节都不动**（两个变量都保住）。"""
        sym = _sym(proj)
        got = B.apply_attr_slot(self.SPEC_CLAUSE, group, value, symbols=sym)
        assert got.endswith(' with tt form'), got
        _t, attrs, clauses = B.split_show_spec(
            got, B._declared_clause_keywords(sym))
        assert clauses == 'with tt form'
        assert len(attrs) == 4

    def test_adding_a_dimension_goes_before_the_clauses(self, proj):
        sym = _sym(proj)
        got = B.apply_attr_slot('lan walk blue smile with tt form',
                                'fx', 'fire', symbols=sym)
        assert got == 'lan walk blue smile fire with tt form', got

    def test_clearing_a_dimension_keeps_five_way_order(self, proj):
        sym = _sym(proj)
        got = B.apply_attr_slot(self.SPEC, 'fx', '', symbols=sym)
        assert got == 'lan walk blue smile', got

    def test_slot_rows_follow_the_five_columns(self, proj):
        """卡片上的「属性 1..4」必须按**你的列**：动作 → 服装 → 表情 → 特效。"""
        sym = _sym(proj)
        rows = B.attr_slot_rows(self.SPEC, sym)
        assert [r['group'] for r in rows] == ['act', 'outfit', 'expressions',
                                              'fx'], rows
        assert [r['value'] for r in rows] == ['walk', 'blue', 'smile', 'fire']


class TestNoHardcodedKeywords:
    def test_native_keywords_still_work_without_a_rules_file(self, tmp_path):
        """没有 `rules.json` → 用 Ren'Py 原生那六个（一个字节都不变）。"""
        root = tmp_path / 'native'
        g = root / 'game'
        (g / 'images' / 'characters' / 'lan').mkdir(parents=True)
        (g / 'images' / 'characters' / 'lan' / 'happy.png').write_bytes(PNG)
        (g / 'definitions.rpy').write_text(
            "layeredimage lan:\n"
            "    image_format 'images/characters/lan/{image}.png'\n"
            "    attribute happy default: 'happy'\n", encoding='utf-8')
        sym = _sym(root)
        # 没有 rules.json → **原生六个**（不是空集）
        assert set(B._declared_clause_keywords(sym)) == \
            set(B.SHOW_CLAUSE_KEYWORDS)
        assert B.split_show_spec('lan happy at i11 with dissolve') == \
            ('lan', ['happy'], 'at i11 with dissolve')


    def test_native_and_custom_coexist(self, proj):
        """⭐⭐ **并存**：`at`（原生）与 `with`（项目声明）在同一个 spec 里都算子句。

        用户：「原生+自定义需要并存！at 和 with 实际上是关键字优先，所以不会
        设置为变量本身，放心」
        """
        sym = _sym(proj)
        kws = B._declared_clause_keywords(sym)
        got = B.split_show_spec('lan walk blue at form with tt form', kws)
        assert got == ('lan', ['walk', 'blue'], 'at form with tt form'), got

    def test_native_keywords_work_even_if_the_project_declares_its_own(
            self, proj):
        """项目只声明了 `with`，`at` / `behind` 照样是子句（并集，不是替换）。"""
        sym = _sym(proj)
        kws = B._declared_clause_keywords(sym)
        assert B.split_show_spec('lan walk at turn', kws) == \
            ('lan', ['walk'], 'at turn')
        assert B.split_show_spec('lan walk behind ye', kws)[2] == 'behind ye'
