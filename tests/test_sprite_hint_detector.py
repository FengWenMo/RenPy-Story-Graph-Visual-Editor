"""
立绘提示**检测器**的测试（2026-11-04）
=====================================

用户：「明明有资源报没有！同样的东西还能报两种错！我建议你专门写一个检测器的测试！」

真机事故（截图）：

    lan aa happy   → 提示「lan 没有「aa」这个属性」   ← 报了
    lan happy aa   → 什么都不说                      ← 没报

两行是**同一个东西**，只是顺序不同。

## 根因（两层，都在这份判据里钉住）

1. **提示只看第二个词**：`want = spec.split()[1]` —— `aa` 排第二就撞上，
   排第三就漏掉；
2. **工具自己会重排**：`preferred_image_attrs(['aa','happy'])` →
   `['happy','aa']`（表情在前、其它在后）。所以用户"在编辑器里切一下就好了"
   —— 不是资源变了，是**顺序变了**，把 `aa` 挪出了第二位。

⇒ 核心不变量 **I12：结论只取决于属性词集合，与顺序无关**。

## 这个检测器查什么（从硬到软）

| 层 | 判据 | 例子 |
|---|---|---|
| ⓪ | 项目**脚本声明过**这个 tag | `layeredimage lan:` |
| ①′ | 只差写法（大小写 / 下划线） | `Qing base` / `qing_base` |
| ① | 角色表认领了 | `characters.xlsx` 的 ImageTag |
| ② | 有立绘素材、角色表没认领 | `qing`（`q`/「青」没填 ImageTag） |
| ③ | 都没有 | 去资源映射认领 |

**判不了就闭嘴**：`symbols` 为 None / 一张表都没有 → 空串。
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.ui_qt.sprite_hints import known_tag_warning   # noqa: E402


class _Sym:
    """只回答提示要的几个问题（真 `Symbols` 的替身）。"""

    def __init__(self, declared=None, attrs=None, sprites=None, actors=None,
                 used=None, words=None, engine=None, images=None,
                 resolved=None):
        self._declared = declared or {}
        self._attrs = attrs or {}
        self._sprites = sprites or set()
        self._actors = actors or set()
        self._used = used or {}
        self._words = words or set()
        self._engine = engine or set()
        self._images = images or {}
        self._resolved = resolved or {}

    def used_attrs_of(self, tag):
        return list(self._used.get(tag, ()))

    def has(self, kind):
        return kind in ('image', 'character')

    def declared_attrs_of(self, tag):
        return list(self._declared.get(tag, []))

    def attrs_of_tag(self, tag):
        return list(self._attrs.get(tag, []))

    def has_sprites(self, tag):
        return tag in self._sprites

    def is_actor_tag(self, tag):
        return tag in self._actors

    def defined_words(self):
        return sorted(self._words)

    def engine_bad_keys(self):
        return set(self._engine)

    def token_image_path(self, tag, token):
        return self._images.get((tag, token), '')

    def resolved_paths_of(self, tag, attrs):
        return list(self._resolved.get(tag, ()))


LAN = ['afraid', 'happy', 'close_happy', 'indifferent', 'stare']


def _lan(**kw):
    return _Sym(declared={'lan': LAN}, attrs={'lan': LAN}, actors={'lan'},
                **kw)


class TestOrderDoesNotMatter:
    """**I12**：同一组属性词，任意排列必须给同一个结论。

    这条不需要预先知道缺陷长什么样 —— 任何"只看第 N 个词"的写法都会现形。
    """

    @pytest.mark.parametrize('words', [
        ['aa'], ['aa', 'happy'], ['aa', 'happy', 'i11'],
        ['happy', 'aa'], ['i11', 'aa', 'happy'],
    ])
    def test_every_permutation_agrees(self, words):
        sym = _lan()
        verdicts = {known_tag_warning(' '.join(['lan'] + list(p)), sym)
                    for p in itertools.permutations(words)}
        assert len(verdicts) == 1, f'顺序不同结论不同：{verdicts}'

    def test_the_report_screenshot(self):
        """截图那一对：两边都必须报 `aa`。"""
        sym = _lan()
        a = known_tag_warning('lan aa happy', sym)
        b = known_tag_warning('lan happy aa', sym)
        assert a == b, (a, b)
        assert 'aa' in a

    def test_all_legal_words_permutations_are_silent(self):
        sym = _lan()
        for perm in itertools.permutations(['happy', 'close_happy']):
            assert known_tag_warning('lan ' + ' '.join(perm), sym) == ''


class TestReportsEveryBadWord:
    def test_two_bad_words_are_both_named(self):
        msg = known_tag_warning('lan aa zz happy', _lan())
        assert 'aa' in msg and 'zz' in msg
        assert 'happy' not in msg.split('没有')[1].split('这个属性')[0]

    def test_good_words_are_not_named(self):
        msg = known_tag_warning('lan aa happy', _lan())
        head = msg.split('这个属性')[0]
        assert 'aa' in head
        assert 'happy' not in head


class TestLayersInOrder:
    def test_declared_and_legal_is_silent(self):
        assert known_tag_warning('lan happy', _lan()) == ''

    def test_declared_but_unknown_is_reported(self):
        assert 'aa' in known_tag_warning('lan aa', _lan())

    def test_table_only_word_counts_as_known(self):
        """表里有、脚本没声明 → **不报**（取舍：表比脚本宽松，误报更烦人）。"""
        sym = _Sym(declared={'lan': ['happy']}, attrs={'lan': ['happy', 'lose']})
        assert known_tag_warning('lan lose', sym) == ''

    def test_bare_tag_is_silent(self):
        assert known_tag_warning('lan', _lan()) == ''

    def test_no_symbols_means_silent(self):
        assert known_tag_warning('lan aa', None) == ''

    def test_no_tables_means_silent(self):
        class _Empty:
            def has(self, kind):
                return False
        assert known_tag_warning('lan aa', _Empty()) == ''


class TestSpellingLayer:
    def test_underscore_is_corrected(self):
        msg = known_tag_warning('lan_happy', _lan())
        assert 'lan happy' in msg

    def test_uppercase_is_corrected(self):
        msg = known_tag_warning('Lan happy', _lan())
        assert 'lan' in msg and '大小写' in msg


class TestSpellingVariantOfAWord:
    """
    **词内部**的拼法差异（2026-11-05 用户截图）：

    > 「为什么检测不到！明明是有的！！！」

    真机 `chapter2_5.rpy` 写 `show ye base_hand_down hi`，而 `layeredimage ye`
    声明的是 `base_handdown`。旧文案只说"没有 base_hand_down 这个属性"，
    加上项目声明一长串，人只会觉得工具**没看见**那条声明 —— 而它看见了，
    只是没法说"该写成什么"。

    判据：忽略大小写与 `_` / `-` 之后相等 = 同一个词的另一种拼法；
    **不做**前缀 / 子串 / 编辑距离（`close_happy` 与 `happy` 是两个词）。
    """

    YE = ['ahah', 'angry', 'base_handdown', 'base_swimsuit', 'gratified',
          'happy']

    def _sym(self, **kw):
        return _Sym(declared={'ye': self.YE}, attrs={'ye': self.YE},
                    actors={'ye'}, **kw)

    def test_it_says_the_correct_spelling(self):
        msg = known_tag_warning('ye base_hand_down gratified', self._sym())
        assert '其实写作「base_handdown」' in msg, msg

    def test_it_still_names_the_word_that_is_wrong(self):
        msg = known_tag_warning('ye base_hand_down gratified', self._sym())
        assert 'base_hand_down' in msg, msg

    def test_a_real_unknown_word_gets_no_spelling_hint(self):
        msg = known_tag_warning('ye nosuchword', self._sym())
        assert '其实写作' not in msg, msg

    def test_prefix_is_not_a_spelling_variant(self):
        """`close_happy` 不是 `happy` 的拼法变体（不做子串匹配）。"""
        msg = known_tag_warning('ye close_happy', self._sym())
        assert '其实写作' not in msg, msg
        assert 'close_happy' in msg, msg

    def test_the_sentence_order_is_stable(self):
        """两个坏词、两种拼法 → 文案可重复（判据靠字符串比对）。"""
        a = known_tag_warning('ye base_hand_down basehanddown', self._sym())
        b = known_tag_warning('ye base_hand_down basehanddown', self._sym())
        assert a == b


class TestAtSuffix:
    def test_at_is_not_an_attribute(self):
        assert known_tag_warning('lan happy@left', _lan()) == ''

    def test_at_on_a_bad_word_still_reports_that_word(self):
        msg = known_tag_warning('lan aa@left happy', _lan())
        assert 'aa' in msg


class TestVariableAndImageEvidence:
    """2026-11-05：`aa` 这种"有定义、没有 png"的代号**不许报**。

    用户原话：「`aa` 是服装的定义！在 charactercenter.rpy，它根本不对应
    任何 image。`show lan aa happy` 在真实项目里完全能跑 —— 你不能去问
    它是不是 png。」
    """

    def test_defined_word_is_silent(self):
        sym = _lan(words={'aa'})
        assert known_tag_warning('lan aa happy', sym) == ''

    def test_lint_does_not_override_a_declared_tag(self):
        sym = _lan(words={'aa'}, engine={'lan|aa|happy'})
        assert known_tag_warning('lan aa happy', sym) == ''

    def test_token_with_a_real_png_is_silent(self):
        sym = _lan(images={
            ('lan', 'lose'): 'game/images/characters/lan/lose.png'})
        assert known_tag_warning('lan lose', sym) == ''

    def test_unknown_word_names_the_picture_that_will_show(self):
        sym = _lan(resolved={
            'lan': ['game/images/characters/lan/indifferent.png']})
        msg = known_tag_warning('lan based', sym)
        assert 'based' in msg and 'indifferent.png' in msg


class TestDoesNotResurrectKnownBugs:
    """回归：以前修过的三条不许被这次改动弄坏。"""

    def test_declared_tag_with_name_mismatch(self):
        """`qing base` 里 `base` 只存在于声明（文件名是 `normal.png`）。"""
        sym = _Sym(declared={'qing': ['base', 'angry']})
        assert known_tag_warning('qing base', sym) == ''

    def test_orphan_sprite_points_at_the_character_table(self):
        sym = _Sym(attrs={'qing': ['normal']}, sprites={'qing'}, actors={'lan'})
        msg = known_tag_warning('qing normal', sym)
        assert '角色表' in msg

    def test_orphan_sprite_names_every_unknown_word(self):
        sym = _Sym(attrs={'qing': ['normal']}, sprites={'qing'}, actors={'lan'})
        msg = known_tag_warning('qing aa normal', sym)
        assert 'aa' in msg


class TestUsedInTheScriptIsNotEvidence:
    """⚠⚠ **上一版这里断言"剧本用过就算认识"，用户当场否掉了。**

    原话：「万一剧本错了呢！万一我用了 `ba` 呢……？这你也当认识？太粗糙了，
    我不接受！」

    现在的判据：**只有"定义位置"算数**（`layeredimage` / 表 / `init python`
    里的字面量），**使用位置不算** —— 剧本里写错的词也"被用过"，
    拿它当证据是循环论证。

    end-to-end 版本（真造一个 rpy）在
    `tests/test_sprite_hint_hell_custom.py::TestItDoesNotMiss`。
    """

    def test_word_only_seen_in_a_script_is_reported(self):
        sym = _Sym(declared={'lan': ['happy']})
        msg = known_tag_warning('lan aa happy', sym)
        assert 'aa' in msg, '剧本里出现过就放过 —— 那是循环论证'

    def test_definition_位置_still_counts(self):
        """对照：真好定义过的（`init python` 字面量）必须放过。"""
        sym = _Sym(declared={'lan': ['happy']})
        sym._defined = {'aa'}

        def defined_words():
            return sorted(sym._defined)
        sym.defined_words = defined_words
        assert known_tag_warning('lan aa happy', sym) == ''
