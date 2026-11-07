"""
**引擎裁决**这一层（2026-11-04；2026-11-05 按用户纠正重写）
==========================================================

用户 2026-11-05 的原话：

> 「`aa` 是服装的定义！在 charactercenter.rpy，它根本不对应任何 image。
>   `show lan aa happy` 在真实项目里完全能跑 —— 你不能去问它是不是 png。」

## 为什么上一版判错了

`renpy lint` 对 `show lan aa happy` 的原话是
`'lan aa happy' is not an image.`，但它查的是 **image 注册表**：

    layeredimage lan:
        attribute happy default: 'happy'

`aa` 是 `CC_OUTFIT_CYCLE = {"lan": ["aa", …]}` 里的**服装代号**（"普通"），
既没有 `aa.png`，也不是 `layeredimage` 的 `attribute`。lint 说"不是图像"
**不等于**项目不认这个变量 —— 真机 949 条这种"错"，游戏照跑
（`LayeredImage._duplicate` 对未知属性只画一行调试字，不抛异常）。

## 现在的判据

| 情况 | 谁说了算 |
|---|---|
| 项目**声明过**这个 tag（`layeredimage` / `image`） | 声明 + 项目变量字面量 + 同名图片；**lint 不参与** |
| 项目**没声明过**这个 tag（`bg_1`） | 引擎的 `is not an image` = 真否定 |

## 这个文件钉什么

| 组 | 钉什么 |
|---|---|
| `TestDeclaredTagIgnoresEngine` | 声明过的 tag：变量词不被 lint 推翻；错词照报，且给"实际显示哪张图" |
| `TestUndeclaredTagUsesEngine` | 没声明过的 tag：引擎否定才引用 lint 原话 |
| `TestOrderNormalised` | 同一组属性词，顺序无关 |
| `TestItRunsAutomatically` | 打开项目就起后台线程（有新鲜缓存则不跑） |
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.assets.engine_images import spec_key        # noqa: E402
from storygraph.ui_qt.sprite_hints import known_tag_warning  # noqa: E402


class _Sym:
    def __init__(self, declared=None, attrs=None, bad=None, words=None,
                 images=None, resolved=None):
        self._declared = declared or {}
        self._attrs = attrs or {}
        self._bad = bad or set()
        self._words = words or set()
        self._images = images or {}
        self._resolved = resolved or {}

    def has(self, kind):
        return kind in ('image', 'character')

    def declared_attrs_of(self, tag):
        return list(self._declared.get(tag, []))

    def attrs_of_tag(self, tag):
        return list(self._attrs.get(tag, []))

    def has_sprites(self, tag):
        return False

    def is_actor_tag(self, tag):
        return tag in ('lan', 'ye')

    def defined_words(self):
        return sorted(self._words)

    def engine_bad_keys(self):
        return set(self._bad)

    def token_image_path(self, tag, token):
        return self._images.get((tag, token), '')

    def resolved_paths_of(self, tag, attrs):
        return list(self._resolved.get(tag, ()))


LAN = ['happy', 'close_happy', 'afraid']


def _sym(bad=(), words=(), images=None, resolved=None):
    return _Sym(declared={'lan': LAN}, attrs={'lan': LAN},
                bad={spec_key(x) for x in bad}, words=set(words),
                images=images, resolved=resolved)


class TestDeclaredTagIgnoresEngine:
    def test_variable_word_is_not_overridden_by_lint(self):
        """`aa` 是项目变量里的服装代号 —— lint 说"不是图像"不算数。"""
        sym = _sym(bad=['lan aa happy'], words=['aa'])
        assert known_tag_warning('lan aa happy', sym) == ''

    def test_typo_is_reported_without_engine_wording(self):
        sym = _sym(bad=['lan based'])
        msg = known_tag_warning('lan based', sym)
        assert 'based' in msg
        assert 'is not an image' not in msg, '已声明的 tag 不许引用 lint 那句话'

    def test_typo_reports_the_picture_that_will_actually_show(self):
        """用户要的"找到对应的图"：`based` 认不出，实际显示默认 `indifferent`。"""
        sym = _sym(bad=['lan based'],
                   resolved={'lan': ['game/images/characters/lan/indifferent.png']})
        msg = known_tag_warning('lan based', sym)
        assert 'indifferent.png' in msg

    def test_token_with_a_real_png_is_known(self):
        """声明漏写 `attribute`、但图在（真机 `lan lose`）—— 也算认识。"""
        sym = _sym(images={('lan', 'lose'): 'game/images/characters/lan/lose.png'})
        assert known_tag_warning('lan lose', sym) == ''

    def test_legal_name_is_silent(self):
        assert known_tag_warning('lan close_happy', _sym(bad=['lan aa happy'])) == ''


class TestUndeclaredTagUsesEngine:
    def test_engine_verdict_is_quoted_for_an_undeclared_tag(self):
        class _Unknown(_Sym):
            def __init__(self):
                super().__init__(declared={}, attrs={},
                                 bad={spec_key('bg definitely_not_here')})

            def is_actor_tag(self, tag):
                return False

        msg = known_tag_warning('bg definitely_not_here', _Unknown())
        assert 'is not an image' in msg

    def test_no_verdict_falls_through_to_the_old_wording(self):
        class _Unknown(_Sym):
            def __init__(self):
                super().__init__(declared={}, attrs={})

            def is_actor_tag(self, tag):
                return False

        msg = known_tag_warning('bg whatever', _Unknown())
        assert msg and 'is not an image' not in msg


class TestOrderNormalised:
    @pytest.mark.parametrize('spec', ['lan aa happy', 'lan happy aa'])
    def test_same_verdict_whatever_the_order(self, spec):
        sym = _sym(bad=['lan aa happy'], words=['aa'])
        assert known_tag_warning(spec, sym) == ''

    def test_spec_key_is_order_free(self):
        assert spec_key('lan aa happy') == spec_key('lan happy aa')
        assert spec_key('lan happy') != spec_key('lan aa happy')

    def test_spec_key_handles_empty(self):
        assert spec_key('') == ''
        assert spec_key('lan') == 'lan'


class TestItRunsAutomatically:
    """打开项目就该问（用户原话），而不是等他点按钮。"""

    def test_fresh_cache_does_not_spawn_a_thread(self, monkeypatch, tmp_path):
        from storygraph.app import session_assets as SA
        calls = []
        monkeypatch.setattr('storygraph.assets.engine_images.cached',
                            lambda root: {'ok': True, 'invalid': []})
        monkeypatch.setattr('threading.Thread',
                            lambda **kw: calls.append(kw) or type(
                                'T', (), {'start': lambda self: None})())
        sess = object.__new__(SA.AssetRegistryMixin)
        sess.project_root = str(tmp_path)
        got = sess._kick_engine_images()
        assert got.get('ok') is True
        assert not calls, '缓存新鲜还去跑引擎'

    def test_missing_cache_spawns_a_background_thread(self, monkeypatch,
                                                      tmp_path):
        from storygraph.app import session_assets as SA
        calls = []
        monkeypatch.setattr('storygraph.assets.engine_images.cached',
                            lambda root: {'ok': False, 'invalid': []})
        monkeypatch.setattr('threading.Thread',
                            lambda **kw: calls.append(kw) or type(
                                'T', (), {'start': lambda self: None})())
        sess = object.__new__(SA.AssetRegistryMixin)
        sess.project_root = str(tmp_path)
        got = sess._kick_engine_images()
        assert got.get('running') is True
        assert calls, '没有缓存却没去问引擎'
        assert calls[0].get('daemon') is True, '不能拖住进程退出'
