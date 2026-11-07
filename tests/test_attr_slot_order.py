"""
切一维**不许改动其余维度的顺序**（2026-11-05 真机事故）
=====================================================

用户（截图 `lan happy clothes2`）：

> 「不行……我还是不放心……你确定它导入的代码没有写反吗？？？我就切换了一下……
> 怎么两个又不对应了……？我的注册不是 show 人物 服装 表情嘛……？」

## 病因

`apply_attr_slot` 里这三行：

```python
attrs = _replace_attr_word(toks[1:], hit['value'], value)   # 原位替换 ✓
attrs = [a for a in attrs if a and a != value]              # 把刚换上的删掉
if value:
    attrs = attrs + [value]                                  # 再追加到末尾
```

`_replace_attr_word` **本来**就是原位替换（它的 docstring 写着"换表情不该让服装
突然跳到后面去"）。后面这两行把它白做了 —— 等于**把换的那一维挪到末尾**：

```
lan clothes2 happy   --outfit=base_hand_up-->  lan happy base_hand_up   ✗
                                                ↑ 服装跑到最后（用户注册的是 人物 服装 表情）

修后：
lan clothes2 happy   --outfit=base_hand_up-->  lan base_hand_up happy   ✓
```

## 为什么这不止是"看着别扭"

`show` 里属性顺序**语义等价**（Ren'Py 不看顺序），但：

1. 卡片显示的顺序**不是作者写的顺序**，人就会怀疑"它到底存对了没有"；
2. 一旦保存，`.rpy` 那一行的**字节被改了** —— 违反"不归己的行逐字节不动"
   的往返保真（`test_patch_golden` 盯的就是这个）；
3. 用户按自己注册的 `人物 服装 表情` 读剧本，顺序一乱就得每次重新对位。

⇒ 判据：**切一维之后，其余维度的相对顺序一个都不能变。**
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.app import beats as B                     # noqa: E402


def _fake_rows(group_value: str, group: str = 'outfit'):
    """只回答"这一维现在是什么词"，不参与分组（分组另有判据）。"""
    def rows(image_spec, symbols, groups=None, facts=None, layer_model=None):
        toks = str(image_spec or '').split()
        return [{'group': group, 'label': '属性', 'value': group_value,
                 'display': group_value, 'options': ['aa', 'clothes2',
                                                     'base_hand_up'],
                 'other': ' '.join(toks[2:])}]
    return rows


class TestSlotKeepsOrder:
    def test_outfit_stays_where_it_was(self, monkeypatch):
        """⭐ 用户截图那一对：服装必须留在第二位。"""
        monkeypatch.setattr(B, 'attr_slot_rows',
                            _fake_rows('clothes2'))
        got = B.apply_attr_slot('lan clothes2 happy', 'outfit', 'base_hand_up')
        assert got == 'lan base_hand_up happy', got

    def test_expression_stays_where_it_was(self, monkeypatch):
        monkeypatch.setattr(B, 'attr_slot_rows',
                            _fake_rows('happy', group='expressions'))
        got = B.apply_attr_slot('lan clothes2 happy', 'expressions', 'angry')
        assert got == 'lan clothes2 angry', got

    def test_normal_offfits_case_from_the_screenshot(self, monkeypatch):
        """`lan happy clothes2` 也能被纠正回 `lan clothes2 happy` 吗？

        **不能** —— 工具不知道作者原本想写哪个顺序。它只保证"切了之后不乱"。
        所以这条断言的是：切 `aa` 回去时，服装仍在它**现在**的位置。
        """
        monkeypatch.setattr(B, 'attr_slot_rows', _fake_rows('clothes2'))
        got = B.apply_attr_slot('lan happy clothes2', 'outfit', 'aa')
        assert got == 'lan happy aa', got

    def test_unknown_word_keeps_its_place(self, monkeypatch):
        """`_hd_cut` 这类内部件：不认识，但**一个字节都不许丢、不许挪**。"""
        monkeypatch.setattr(B, 'attr_slot_rows', _fake_rows('clothes2'))
        got = B.apply_attr_slot('lan _hd_cut clothes2 happy', 'outfit', 'aa')
        assert got == 'lan _hd_cut aa happy', got

    def test_clearing_a_dimension_keeps_the_rest(self, monkeypatch):
        monkeypatch.setattr(B, 'attr_slot_rows', _fake_rows('clothes2'))
        got = B.apply_attr_slot('lan clothes2 happy', 'outfit', '')
        assert got == 'lan happy', got

    def test_adding_a_dimension_appends(self, monkeypatch):
        """这一维原来没有 → 只能追加（没有"原位"可言）。"""
        monkeypatch.setattr(B, 'attr_slot_rows', _fake_rows(''))
        got = B.apply_attr_slot('lan happy', 'outfit', 'clothes2')
        assert got == 'lan happy clothes2', got

    def test_no_duplicate_when_replacing_with_the_same_word(self, monkeypatch):
        monkeypatch.setattr(B, 'attr_slot_rows', _fake_rows('clothes2'))
        got = B.apply_attr_slot('lan clothes2 happy', 'outfit', 'clothes2')
        assert got == 'lan clothes2 happy', got

    def test_long_spec_keeps_every_other_position(self, monkeypatch):
        monkeypatch.setattr(B, 'attr_slot_rows', _fake_rows('clothes2'))
        got = B.apply_attr_slot('lan aaa clothes2 happy _mask', 'outfit', 'aa')
        assert got == 'lan aaa aa happy _mask', got

    def test_tag_is_never_moved(self, monkeypatch):
        monkeypatch.setattr(B, 'attr_slot_rows', _fake_rows('clothes2'))
        got = B.apply_attr_slot('lan clothes2 happy', 'outfit', 'aa')
        assert got.split()[0] == 'lan'


class TestNoSlotNoChange:
    def test_unknown_group_returns_spec_untouched(self, monkeypatch):
        """认不出这一维 → **原样返回**（宁可不改，也不猜着改）。"""
        monkeypatch.setattr(B, 'attr_slot_rows', lambda *a, **k: [])
        spec = 'lan clothes2 happy'
        assert B.apply_attr_slot(spec, 'outfit', 'aa') == spec

    def test_empty_spec_is_safe(self, monkeypatch):
        monkeypatch.setattr(B, 'attr_slot_rows', _fake_rows('clothes2'))
        assert B.apply_attr_slot('', 'outfit', 'aa') == ''


# ---------------------------------------------------------------------------
#  二、列顺序：**按 rules.json 里你声明的顺序**（2026-11-05）
# ---------------------------------------------------------------------------

class TestDeclaredColumnOrder:
    """用户拍桌子那一条：

    > 「我不是已经自定义了结构吗……？我的 json 文件里面难道没有顺序码？
    >   你列出来的时候不是按照我说的属性列的吗？」

    `storygraph.rules.json` 里 `show` 的 `pattern` 就是顺序码：

        [{kind: choice, column: 角色}, {kind: choice, column: 服装},
         {kind: choice, column: 表情}]

    ⇒ 卡片上「属性 1」必须是**服装**、「属性 2」才是表情。
    把"列"认到"引擎组"上靠**候选值交集**（作者自己写的数据），不靠列名猜。
    """

    def _rows(self):
        return [
            {'group': 'expressions', 'label': '属性 1',
             'values': ['happy', 'angry'], 'value': 'happy'},
            {'group': 'outfit', 'label': '属性 2',
             'values': ['clothes2', 'aa'], 'value': 'clothes2'},
        ]

    def test_declared_order_wins(self):
        order = [('角色', {'lan', 'ye'}),
                 ('服装', {'clothes2', 'aa'}),
                 ('表情', {'happy', 'angry'})]
        got = B._reorder_by_declared(self._rows(), order)
        assert [r['group'] for r in got] == ['outfit', 'expressions'], got

    def test_tag_column_is_skipped_not_fatal(self):
        """⭐ 第一版踩的：`角色` 那列（tag）与任何组都无交集，
        被当成"认不出"→ **整体放弃**，于是排序永远不生效。"""
        order = [('角色', {'lan'}), ('服装', {'clothes2'})]
        got = B._reorder_by_declared(self._rows(), order)
        assert got[0]['group'] == 'outfit', 'tag 那列把排序带崩了'

    def test_no_matching_column_leaves_rows_alone(self):
        """一列都对不上 → **保持原样**（排错比不排更糟）。"""
        rows = self._rows()
        got = B._reorder_by_declared(rows, [('不存在', {'zzz'})])
        assert [r['group'] for r in got] == ['expressions', 'outfit']

    def test_empty_order_is_noop(self):
        rows = self._rows()
        assert B._reorder_by_declared(rows, []) == rows

    def test_reads_values_field_not_options(self):
        """⚠ `group_rows` 阶段的键是 `values`（`options` 是后一步才有的）。"""
        rows = [{'group': 'a', 'values': ['x']}, {'group': 'b', 'values': ['y']}]
        got = B._reorder_by_declared(rows, [('第二', {'y'}), ('第一', {'x'})])
        assert [r['group'] for r in got] == ['b', 'a']

    def test_unknown_groups_keep_their_relative_order(self):
        rows = self._rows() + [{'group': 'extra', 'values': ['zzz']}]
        order = [('服装', {'clothes2'})]
        got = B._reorder_by_declared(rows, order)
        assert [r['group'] for r in got] == ['outfit', 'expressions', 'extra']
