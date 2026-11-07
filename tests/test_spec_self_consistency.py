"""
地狱测试第七轮转正：**生成器 × 校验器自相矛盾**（2026-11-03）
==========================================================

第四轮之前那些测试问的是"产物对不对"。这一份问的是另一件事：

> **工具自己的两句话打不打架？**

真实事故（用户连报两轮）：卡片里那句 `qing base` 是**选择器自己写出来的**
（`resolve_pick('qing_normal')` → `spec='qing base'`，因为 `definitions.rpy`
里 `layeredimage qing:` + `attribute base default: "normal"`），而同一张卡片
旁边的提示说「`qing` 不是已知的角色立绘标签」。工具先算出一句合法的 `show`，
再说它不合法。

判据（脚本 `tools/hell_declared_spec.py` 里同源，这里是能进 CI 的部分）
------------------------------------------------------------------------

| 代号 | 不变量 |
|---|---|
| **I7** | 自产自销：选择器写出的 spec，校验器必须认（`spec_tag_problem` 为空） |
| **I8** | 声明优先：项目 `layeredimage` / `image` 声明过的，永远不许被判成"不是立绘" |
| **I9** | 判不了就闭嘴：声明认不全时只许不吭声，不许报"不是立绘" |
| **I10** | 口径一致：大小写 / 下划线 / `@` 后缀不许把合法写法判死 |
| **I11** | 名字不许拼两遍：`bg_1.jpg` 的自动 image name 是 `bg_1`，不是 `bg bg_1` |

⚠ 这一轮抓到的**都是真缺陷**（不是判据错），逐条：
1. `charactercenter/` 下 24 张 UI 图 `showable=True` → 选择器写出 `ui background`
2. 映射里 `char` 填显示名 → 选择器写出 `青 base`（引擎不认）
3. `game/images/bg_1.jpg` → spec `bg bg_1`（引擎不认）
4. 提示对 `qing base` 误报（只查角色表，没读项目声明）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.assets.asset_identity import (Identity,        # noqa: E402
                                              spec_for_identity,
                                              spec_tag_problem)


# ===========================================================================
#  I7：自产自销 —— 生成器不许产出自己不认识的名字
# ===========================================================================

class TestGeneratorRunsTheValidator:
    def test_display_name_as_tag_is_refused(self):
        """真机事故：映射写 `{"char": "青"}`，产出 `青 base`。"""
        why = spec_tag_problem('青 base', {'qing', 'lan', 'jc'})
        assert why
        assert '青' in why
        assert '显示名' in why or 'image tag' in why
        assert '资源映射' in why                  # 给出口

    def test_known_tag_is_fine(self):
        assert spec_tag_problem('qing base', {'qing', 'lan'}) == ''

    def test_engine_verbs_are_not_flagged(self):
        """`bg` / `cg` / `ui` 是引擎内置语义，不在"角色 tag"表里也不许否掉。"""
        for spec in ('bg rooftop', 'cg event01', 'ui background'):
            assert spec_tag_problem(spec, {'qing'}) == '', spec

    def test_no_tags_means_cannot_judge(self):
        """一张表都没有 → 判不了 → 放行（假装"认识"比误伤好）。"""
        assert spec_tag_problem('青 base', set()) == ''
        assert spec_tag_problem('青 base', frozenset()) == ''

    def test_empty_spec(self):
        assert spec_tag_problem('', {'qing'}) == ''


# ===========================================================================
#  I11：名字不许拼两遍
# ===========================================================================

class TestNameIsNotDoubled:
    def test_stem_already_carries_the_tag(self):
        """`images/bg_1.jpg` → 自动 name 是 `bg_1`；旧代码给 `bg bg_1`。"""
        ident = Identity(kind='bg', tag='bg', attrs=('bg_1',),
                         source='tag-column', showable=True)
        assert spec_for_identity(ident, {'Path': 'game/images/bg_1.jpg'}) \
            == 'bg_1'

    def test_subdirectory_still_needs_the_tag(self):
        """对照：`images/bg/rooftop.png` 要的是 `bg rooftop`（别改坏）。"""
        ident = Identity(kind='bg', tag='bg', attrs=('rooftop',),
                         source='tag-column', showable=True)
        assert spec_for_identity(
            ident, {'Path': 'game/images/bg/rooftop.png'}) == 'bg rooftop'

    def test_sprite_with_two_attrs_is_untouched(self):
        ident = Identity(kind='sprite', tag='qing', attrs=('base', 'angry'),
                         source='declared', showable=True)
        assert spec_for_identity(
            ident, {'Path': 'game/images/characters/qing/base.png'}) \
            == 'qing base angry'


# ===========================================================================
#  I8/I9/I10：提示侧的判定顺序（用最小假 symbols，不碰真项目）
# ===========================================================================

class _Sym:
    """只回答提示需要的四个问题（真 `Symbols` 的替身）。"""

    def __init__(self, declared=None, attrs=None, sprites=None,
                 actors=None):
        self._declared = declared or {}
        self._attrs = attrs or {}
        self._sprites = sprites or set()
        self._actors = actors or set()

    def has(self, kind):
        return kind in ('image', 'character')       # 表非空

    def declared_attrs_of(self, tag):
        return list(self._declared.get(tag, []))

    def attrs_of_tag(self, tag):
        return list(self._attrs.get(tag, []))

    def has_sprites(self, tag):
        return tag in self._sprites

    def is_actor_tag(self, tag):
        return tag in self._actors


class TestWarningOrder:
    def _warn(self, spec, sym):
        from storygraph.ui_qt.sprite_grid import known_tag_warning
        return known_tag_warning(spec, sym)

    def test_declaration_wins_and_shuts_up(self):
        """I8：项目声明过的属性 → 闭嘴（这是用户最直接的痛点）。"""
        sym = _Sym(declared={'qing': ['angry', 'base', 'curious', 'en']})
        assert self._warn('qing base', sym) == ''
        assert self._warn('qing angry', sym) == ''

    def test_wrong_attr_lists_the_declared_ones(self):
        sym = _Sym(declared={'qing': ['angry', 'base']})
        msg = self._warn('qing nosuch', sym)
        assert '没有「nosuch」这个属性' in msg
        assert 'angry' in msg and 'base' in msg

    def test_undeclared_but_has_sprites_points_at_character_table(self):
        sym = _Sym(attrs={'qing': ['normal']}, sprites={'qing'},
                   actors={'lan'})
        msg = self._warn('qing normal', sym)
        assert '角色表' in msg and '没有角色认领' in msg

    def test_nothing_known_falls_back_to_mapping_hint(self):
        sym = _Sym(actors={'lan'})
        msg = self._warn('nosuch base', sym)
        assert '不是已知的角色立绘标签' in msg

    def test_uppercase_gets_the_right_spelling(self):
        """I10：`Qing` 引擎确实不认，但要说"应为 qing"，不是"不是立绘"。"""
        sym = _Sym(declared={'qing': ['base']})
        msg = self._warn('Qing base', sym)
        assert 'qing' in msg
        assert '大小写' in msg
        assert '不是已知的角色立绘标签' not in msg

    def test_underscore_gets_the_right_spelling(self):
        sym = _Sym(declared={'qing': ['base']})
        msg = self._warn('qing_base', sym)
        assert 'qing base' in msg
        assert '空格' in msg

    def test_unknown_tag_is_not_renamed(self):
        """认不出的 tag **不许**被"猜一个相近的"（铁律：猜比不知道坏）。"""
        sym = _Sym(declared={'qing': ['base']})
        msg = self._warn('QINGX base', sym)
        assert '不是已知的角色立绘标签' in msg

    def test_at_suffix_is_not_an_attribute(self):
        """`qing base@left` 的 `@left` 是 AT 变换，不是属性名。"""
        sym = _Sym(declared={'qing': ['base']})
        assert self._warn('qing base@left', sym) == ''


class TestPickerRefusesItEndToEnd:
    """**走完整链路**（`resolve_pick`），不只测那个纯函数。

    ⚠ 变异验证抓到的教训：第一版只测 `spec_tag_problem`，把 picker 里的调用
    删掉它照样全绿 —— 判据没走缺陷真正发生的那条路径。
    """

    ROW = {'ID': 'qing', 'Tag': 'ui', 'Attributes': 'qing', 'Value': 'qing',
           'Path': 'game/phone_msg/qing.png', 'Category': 'ui'}

    def _pick(self, mapping):
        from storygraph.app.picker import resolve_pick
        return resolve_pick('qing', dict(self.ROW),
                            known_tags=frozenset({'qing', 'lan'}),
                            known_chars={}, mapping=mapping,
                            all_rows={'qing': dict(self.ROW)})

    def test_display_name_mapping_is_refused_with_a_reason(self):
        """真机映射：`{"char": "青"}` → 旧代码产出 `青 base`。"""
        res = self._pick({'images': {'qing': {'kind': 'sprite',
                                              'char': '青', 'attr': 'base'}}})
        assert res.ok is False, f'居然放行了：spec={res.spec!r}'
        assert not res.spec, '拒绝时不许留下 spec（否则上游会写进去）'
        assert '青' in res.message
        assert '显示名' in res.message or 'image tag' in res.message

    def test_correct_mapping_still_passes(self):
        """对照：映射写对了（`char: qing`）必须照常放行。"""
        res = self._pick({'images': {'qing': {'kind': 'sprite',
                                              'char': 'qing',
                                              'attr': 'base'}}})
        assert res.ok is True, res.message
        assert res.spec == 'qing base'
