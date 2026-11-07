"""
`show` spec 的地狱测试：**切一维只许改一个词**（2026-11-05）
=========================================================

用户：

> 「总之我需要你解决这个问题！你自己看着办吧！查资料，看项目，问引擎，只要保证
>   通用性都可以！顺序不能够错！错了 rpy 就完全不能跑啊！做完后，写地狱测试！」

## 为什么"顺序不能错"是真话（不是洁癖）

Ren'Py 的 `show` 语法（官方文档）：

    show <image name> [at <transform>…] [as <tag>] [behind <tag>…]
                     [onlayer <layer>] [zorder <int>] [with <transition>]

**属性只能出现在 `at` 之前**。真机 `beat.show` 里就有这些形态：

    'lan scared at i11 with vpunch'
    'cg cg02_2 at cg38402160 behind ye'
    'ye aa happy@l11'

旧实现把 `at` / `i11` / `with` / `vpunch` **全当属性词**，切一维时"替换 + 追加"
重拼 —— 追加出来的词落到 `with vpunch` **后面**：

    show lan scared at i11 with vpunch clothes2      ← 语法错，整份 .rpy 跑不了

## 三条不变量

| # | 不变量 | 违反的后果 |
|---|---|---|
| **I** | 切一维**只改一个词**：词数不变（除非新增/删除那一维）、那一个位置被换掉、其余**逐字节相同** | 顺序被改、`.rpy` 字节被改 |
| **II** | **子句区逐字节不动**（`at …` / `as …` / `behind …` / `onlayer …` / `zorder …` / `with …`） | 语法错（属性跑到 `with` 后面） |
| **III** | 新增一维 → 插在**属性区末尾、子句之前** | 同上 |

## 这一份怎么测

1. **纯函数**：`split_show_spec` 的 12 种真机形态；
2. **不变量 I/II/III**：20+ 种 spec × 切服装/切表情，逐字符比；
3. **真项目形态回归**：从 `D:\\千连万理\\sr_general` 的 8000 拍里抽真实
   `show` 行，每一行切一次，**除目标词外必须完全相等**（没有真项目就跳过）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.app import beats as B                     # noqa: E402

#: 真项目（没有就跳过真机那一组）
REAL_PROJECT = Path(r'D:\千连万理\sr_general')


def _rows(value: str, group: str = 'outfit'):
    """假的分组结果：只回答"这一维现在是哪个词"。"""
    def rows(image_spec, symbols, groups=None, facts=None, layer_model=None):
        return [{'group': group, 'label': '属性', 'value': value,
                 'display': value, 'options': ['aa', 'clothes2',
                                               'base_hand_up', 'happy',
                                               'angry', 'care'],
                 'other': ''}]
    return rows


def _apply(monkeypatch, spec, value, group='outfit', old=None):
    monkeypatch.setattr(B, 'attr_slot_rows', _rows(old or '', group))
    return B.apply_attr_slot(spec, group, value)


# ---------------------------------------------------------------------------
#  一、拆分：真机那 12 种形态
# ---------------------------------------------------------------------------

class TestSplitRealForms:
    @pytest.mark.parametrize('spec,tag,attrs,clauses', [
        # 全裸
        ('lan', 'lan', [], ''),
        ('lan happy', 'lan', ['happy'], ''),
        ('lan aa close_happy', 'lan', ['aa', 'close_happy'], ''),
        # 内联位置（`@` 粘在属性上）
        ('ye aa happy@l11', 'ye', ['aa', 'happy@l11'], ''),
        ('lan aa close_happy@i22', 'lan', ['aa', 'close_happy@i22'], ''),
        # 子句
        ('lan scared at i11', 'lan', ['scared'], 'at i11'),
        ('lan scared at i11 with vpunch', 'lan', ['scared'],
         'at i11 with vpunch'),
        ('lan aa happy at l11 with dissolve', 'lan', ['aa', 'happy'],
         'at l11 with dissolve'),
        ('cg cg02_2 at cg38402160 behind ye', 'cg', ['cg02_2'],
         'at cg38402160 behind ye'),
        ('lan happy as protagonist', 'lan', ['happy'], 'as protagonist'),
        ('bg room onlayer master', 'bg', ['room'], 'onlayer master'),
        ('lan happy zorder 5', 'lan', ['happy'], 'zorder 5'),
        ('lan aa happy at i11 as p behind ye with vpunch', 'lan',
         ['aa', 'happy'], 'at i11 as p behind ye with vpunch'),
        # 空
        ('', '', [], ''),
        ('   ', '', [], ''),
    ])
    def test_split(self, spec, tag, attrs, clauses):
        assert B.split_show_spec(spec) == (tag, attrs, clauses)

    def test_clause_keyword_as_an_attribute_name_is_not_a_clause(self):
        """`at` 作为**属性名**（`show x at`）—— 只有它出现在有子句的位置才算。

        实际不会这么写（`at` 是保留字），所以这里只钉"第一个 `at` 就是切点"。
        """
        tag, attrs, clauses = B.split_show_spec('lan happy at i11')
        assert attrs == ['happy'] and clauses == 'at i11'


# ---------------------------------------------------------------------------
#  二、不变量 I：**切一维只改一个词**
# ---------------------------------------------------------------------------

SPECS = [
    'lan happy',
    'lan aa happy',
    'lan happy aa',
    'lan clothes2 happy',
    'lan happy clothes2',
    'lan aa close_happy',
    'lan aa happy at i11',
    'lan aa happy at i11 with dissolve',
    'lan scared at i11 with vpunch',
    'lan aa happy@l11',
    'lan aa happy at l11 with dissolve',
    'lan aa happy as p',
    'lan aa happy behind ye',
    'lan aa happy onlayer master',
    'lan aa happy zorder 5',
    'lan _hd_cut aa happy',
    'lan aa happy at i11 as p behind ye zorder 5 with vpunch',
]


#: **替换**（那一维原来有值）：词数必须不变、只改一个位置
REPLACE_CASES = [
    ('lan clothes2 happy', 'clothes2'),
    ('lan aa happy', 'aa'),
    ('lan aa close_happy', 'aa'),
    ('lan aa happy at i11', 'aa'),
    ('lan aa happy at i11 with dissolve', 'aa'),
    ('lan aa happy@l11', 'aa'),
    ('lan aa happy as p', 'aa'),
    ('lan aa happy behind ye', 'aa'),
    ('lan aa happy onlayer master', 'aa'),
    ('lan aa happy zorder 5', 'aa'),
    ('lan _hd_cut aa happy', 'aa'),
    ('lan aa happy at i11 as p behind ye zorder 5 with vpunch', 'aa'),
]


class TestOnlyOneWordChanges:
    """**替换**一个维度：词数不变、只改一个位置、其余逐词相同。"""

    @pytest.mark.parametrize('spec,old', REPLACE_CASES)
    def test_word_count_is_stable(self, monkeypatch, spec, old):
        before = spec.split()
        got = _apply(monkeypatch, spec, 'base_hand_up', 'outfit', old)
        assert len(got.split()) == len(before), f'{spec} → {got}'

    @pytest.mark.parametrize('spec,old', REPLACE_CASES)
    def test_all_other_words_are_byte_identical(self, monkeypatch, spec, old):
        """⭐⭐ 除目标词外**逐词相同**（顺序、子句、`@` 全不许动）。"""
        before = spec.split()
        got = _apply(monkeypatch, spec, 'base_hand_up', 'outfit', old)
        after = got.split()
        diff = [(i, a, b) for i, (a, b) in enumerate(zip(before, after))
                if a != b]
        assert len(diff) == 1, f'{spec} → {got}（改了 {len(diff)} 处）'
        assert diff[0][2].startswith('base_hand_up'), diff
        assert diff[0][0] == before.index(old), '换掉的位置不是原来那个' 

    @pytest.mark.parametrize('spec', SPECS)
    def test_clause_region_is_untouched(self, monkeypatch, spec):
        """⭐ 子句区**逐字节**原样（`at …` / `with …` / `behind …`）。"""
        _tag, _attrs, clauses = B.split_show_spec(spec)
        got = _apply(monkeypatch, spec, 'base_hand_up', 'outfit', 'aa')
        _g_tag, _g_attrs, g_clauses = B.split_show_spec(got)
        assert g_clauses == clauses

    def test_at_position_never_moves_before_attributes(self, monkeypatch):
        """属性**不许**跑到 `at` 后面（那才是真的跑不了）。"""
        got = _apply(monkeypatch, 'lan scared at i11 with vpunch', 'clothes2',
                     'outfit', '')
        assert got == 'lan scared clothes2 at i11 with vpunch'
        assert got.index('at ') > got.index('clothes2')


class TestAtSuffixFollowsItsWord:
    @pytest.mark.parametrize('spec,expected', [
        ('ye aa happy@l11', 'ye aa care@l11'),          # 换的正是带 @ 的那个
        ('lan happy@i22', 'lan angry@i22'),
    ])
    def test_at_stays_on_the_word_it_attached_to(self, monkeypatch, spec,
                                                 expected):
        """`happy@l11` 换成 `care` → `care@l11`（位置跟着走，不能丢）。"""
        monkeypatch.setattr(B, 'attr_slot_rows', _rows('happy', 'expressions'))
        got = B.apply_attr_slot(spec, 'expressions',
                                expected.split()[-1].split('@')[0])
        assert got == expected, got

    def test_at_on_another_word_is_untouched(self, monkeypatch):
        """`aa@l11 happy` 换表情 → `aa@l11` 原样。"""
        monkeypatch.setattr(B, 'attr_slot_rows',
                            _rows('happy', 'expressions'))
        got = B.apply_attr_slot('lan aa@l11 happy', 'expressions', 'angry')
        assert got == 'lan aa@l11 angry', got


# ---------------------------------------------------------------------------
#  三、不变量 III：新增一维插在**属性区末尾、子句之前**
# ---------------------------------------------------------------------------

class TestAddDimension:
    def test_appends_before_the_clauses(self, monkeypatch):
        got = _apply(monkeypatch, 'lan happy at i11 with dissolve',
                     'clothes2', 'outfit', '')
        assert got == 'lan happy clothes2 at i11 with dissolve', got

    def test_appends_at_the_end_when_there_are_no_clauses(self, monkeypatch):
        got = _apply(monkeypatch, 'lan happy', 'clothes2', 'outfit', '')
        assert got == 'lan happy clothes2'

    def test_clearing_a_dimension_keeps_clauses(self, monkeypatch):
        got = _apply(monkeypatch, 'lan aa happy at i11', '', 'outfit', 'aa')
        assert got == 'lan happy at i11', got


# ---------------------------------------------------------------------------
#  四、真项目形态（8000 拍里抽真实 show 行）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_PROJECT.is_dir(), reason='没有真项目')
class TestRealProjectShapes:
    """从真项目抽 200 个**带子句或 `@`** 的 show 行，每行切一次，逐词比。"""

    @staticmethod
    def _shapes():
        from storygraph.parsing.discover import discover_scripts
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        files = [str(p) for p in sorted(discover_scripts(str(REAL_PROJECT)))]
        pr, _inc = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
        out = []
        for b in beats_from_parse_result(pr):
            spec = str(getattr(b, 'show', '') or '').strip()
            if not spec:
                continue
            _tag, _attrs, clauses = B.split_show_spec(spec)
            if len(spec.split()) >= 3 and (clauses or '@' in spec):
                out.append(spec)
            if len(out) >= 200:
                break
        return out

    def test_every_real_shape_survives_a_slot_change(self, monkeypatch):
        shapes = self._shapes()
        assert shapes, '一条带子句的真机 show 都没抽到（项目结构变了？）'
        bad = []
        for spec in shapes:
            tag, attrs, clauses = B.split_show_spec(spec)
            if len(attrs) < 2:
                continue
            # 拿第一个属性当"要被换掉的那一维"
            old = attrs[0].split('@')[0]
            monkeypatch.setattr(B, 'attr_slot_rows', _rows(old, 'outfit'))
            got = B.apply_attr_slot(spec, 'outfit', 'zzz_new')
            _g_tag, g_attrs, g_clauses = B.split_show_spec(got)
            expect = [a.split('@')[0] for a in attrs]
            expect[0] = 'zzz_new'
            if [a.split('@')[0] for a in g_attrs] != expect:
                bad.append((spec, got, '属性区被改动'))
            elif g_clauses != clauses:
                bad.append((spec, got, f'子句变了：{clauses!r} → {g_clauses!r}'))
        assert not bad, '真机形态被改坏：\n' + '\n'.join(
            f'  {s}\n    → {g}（{why}）' for s, g, why in bad[:8])


# ---------------------------------------------------------------------------
#  五、**关键字后面跟自定义名字**（`with aa` / `at form`）
# ---------------------------------------------------------------------------

class TestClauseKeywordsWithCustomNames:
    """用户：「把 with at 等关键字也写测试……比如定义一个 with aa form 等……」

    `with aa` 里那个 `aa` 是**用户自己定义的 transition**（`define aa = Dissolve(…)`），
    `at form` 里 `form` 是他定义的 transform。它们**长得跟属性词一模一样**，
    但位置不同 → 语义不同：

    | 位置 | `aa` 是什么 |
    |---|---|
    | `lan aa happy`（`at` 之前） | **服装代号**（layeredimage 的 attribute） |
    | `lan happy with aa` | **transition 名**（`define aa`） |
    | `lan happy at aa` | **transform 名**（`transform aa`） |

    所以判据是**位置决定语义**：`split_show_spec` 一旦过了 `at` / `with`，
    后面的词**一个都不许**当属性。
    """

    @pytest.mark.parametrize('spec,tag,attrs,clauses', [
        # 关键字后面跟"看着像属性"的自定义名
        ('lan happy with aa', 'lan', ['happy'], 'with aa'),
        ('lan happy at form', 'lan', ['happy'], 'at form'),
        ('lan happy at aa form', 'lan', ['happy'], 'at aa form'),
        ('lan happy at form with aa', 'lan', ['happy'], 'at form with aa'),
        ('lan happy with aa form', 'lan', ['happy'], 'with aa form'),
        # 同一个词当属性 vs 当子句
        ('lan aa happy', 'lan', ['aa', 'happy'], ''),
        ('lan happy aa', 'lan', ['happy', 'aa'], ''),
        # 子句里出现属性名，也不许被当属性
        ('lan happy at happy', 'lan', ['happy'], 'at happy'),
        ('lan happy behind aa', 'lan', ['happy'], 'behind aa'),
        ('lan happy as aa', 'lan', ['happy'], 'as aa'),
        ('lan happy onlayer aa', 'lan', ['happy'], 'onlayer aa'),
        ('lan happy zorder aa', 'lan', ['happy'], 'zorder aa'),
        # 全组合：`aa` 同时当属性、transform、transition、tag
        ('lan aa happy at aa with aa', 'lan', ['aa', 'happy'],
         'at aa with aa'),
        ('lan aa happy at form behind aa zorder 3 with aa form', 'lan',
         ['aa', 'happy'], 'at form behind aa zorder 3 with aa form'),
    ])
    def test_custom_names_after_keywords_are_clauses(self, spec, tag, attrs,
                                                     clauses):
        assert B.split_show_spec(spec) == (tag, attrs, clauses)

    def test_switching_a_dimension_leaves_custom_clause_names_alone(
            self, monkeypatch):
        """⭐ 切服装时 `with aa form` 一个字节都不许动。"""
        got = _apply(monkeypatch, 'lan aa happy with aa form', 'clothes2',
                     'outfit', 'aa')
        assert got == 'lan clothes2 happy with aa form', got

    def test_switching_expression_does_not_touch_at_names(self, monkeypatch):
        monkeypatch.setattr(B, 'attr_slot_rows',
                            _rows('happy', 'expressions'))
        got = B.apply_attr_slot('lan aa happy at aa form with aa',
                                'expressions', 'angry')
        assert got == 'lan aa angry at aa form with aa', got

    def test_a_word_only_in_the_clause_is_never_reported_as_missing(
            self, monkeypatch):
        """`form` 只出现在 `at` 后面 → **不是属性**，不许出现在属性区。"""
        got = _apply(monkeypatch, 'lan happy at form', 'clothes2', 'outfit',
                     '')
        _t, attrs, clauses = B.split_show_spec(got)
        assert 'form' not in attrs
        assert clauses == 'at form'

    def test_no_duplicate_when_the_same_word_serves_two_roles(
            self, monkeypatch):
        """`aa` 同时是服装和 transition：切服装时**只改属性区那个**。"""
        got = _apply(monkeypatch, 'lan aa happy with aa', 'clothes2',
                     'outfit', 'aa')
        assert got == 'lan clothes2 happy with aa', got
        # 按**词**数（`'aa '` 那种写法在末尾会漏 —— 自己踩过一次）
        assert got.split().count('aa') == 1, got
