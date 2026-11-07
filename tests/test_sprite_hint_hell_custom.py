"""
地狱测试：**自定义 rpy 定义一个新东西**，工具必须"不错不漏"（2026-11-04）
======================================================================

用户："万一剧本错了呢！万一我用了 ba 呢……？这你也当认识？太粗糙了，
我不接受！！！仿照之前我给你的剧本写地狱测试，就要自定义一个 rpy 文件，
定义一个原来没有的东西，我要它能认出来，且不错不漏！"

## 上一版错在哪

第一版为了解释真机的 `aa`（它是 `init python` 里 `CC_OUTFIT_CYCLE` 的界面
代号），加了一路"**剧本里 `show` 过就算认识**"。用户当场否掉 ——
那是**循环论证**：剧本里写错的词也"被用过"，于是永远不报。

## 这一版的判据：**定义位置 vs 使用位置**

| 位置 | 算不算"项目认识" | 为什么 |
|---|---|---|
| `layeredimage` / `image` 声明 | ✅ | 项目自己写的结构 |
| 资源表 `Attributes` 列 | ✅ | 项目自己登记的 |
| `init python` 里 **dict 的键 / list 的元素** | ✅ | 项目自己定义的数据 |
| **剧本里 `show` 过** | ❌ | **使用不是定义**（写错了也"用过"） |
| `init python` 里 dict 的**值** | ❌ | 那多是中文说明（`{"aa": "普通"}` 里"普通"不是属性） |
| 音频路径 / 长句（`bgm/x.mp3`） | ❌ | 它们在容器里，但不是代号 |

这个文件用**真项目结构**（`game/` + `definitions.rpy` + `config.rpy` +
`script.rpy`）钉住上面每一行，走完整链路：
`Symbols(registry)` → `defined_words()` → `known_tag_warning()`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 一个"新东西"要在三处之一定义才合法 —— 这里给出四种写法
DECL = """\
layeredimage lan:
    image_format 'images/characters/lan/{image}.png'
    attribute happy default:
        'happy'
    attribute close_happy:
        'close_happy'
"""

INIT = """\
init python:
    CC_OUTFIT_CYCLE = {
        "lan": ["aa", "clothes2"],
    }
    CC_OUTFIT_LABEL = {
        "aa": "普通",
    }
    SFX_BANK = ["bgm/ed.mp3", "sound/hit.ogg"]
"""


def _make(tmp_path, *, decl=DECL, init=INIT, script='') -> Path:
    game = tmp_path / 'game'
    (game / 'images' / 'characters' / 'lan').mkdir(parents=True, exist_ok=True)
    (game / 'images' / 'characters' / 'lan' / 'happy.png').write_bytes(b'\x89PNG')
    (game / 'definitions.rpy').write_text(decl, encoding='utf-8')
    (game / 'config.rpy').write_text(init, encoding='utf-8')
    (game / 'script.rpy').write_text(
        script or 'label start:\n    return\n', encoding='utf-8')
    return tmp_path


def _symbols(root):
    from storygraph.assets.asset_registry import AssetRegistry
    from storygraph.story.dsl import Symbols
    reg = AssetRegistry()
    reg.project_root = str(root)
    # 一行表数据：`known_tag_warning` 要 `has('image')` 才肯判
    reg.images['lan_happy'] = {'ID': 'lan_happy', 'Tag': 'lan',
                               'Attributes': 'happy',
                               'Path': 'game/images/characters/lan/happy.png',
                               'Category': 'character'}
    return Symbols(reg), reg


def _warn(spec, sym):
    from storygraph.ui_qt.sprite_hints import known_tag_warning
    return known_tag_warning(spec, sym)


class TestItRecognizesNewDefinitions:
    """**认出来**：新加的定义必须立刻生效（不许"只有写进表才认"）。"""

    def test_word_defined_in_init_python(self, tmp_path):
        sym, _ = _symbols(_make(tmp_path))
        assert _warn('lan aa happy', sym) == ''

    def test_word_defined_in_layeredimage(self, tmp_path):
        sym, _ = _symbols(_make(tmp_path))
        assert _warn('lan close_happy', sym) == ''

    def test_word_defined_in_the_table(self, tmp_path):
        """表里登记过的也算（`_attrs_by_tag` 在**构造时**建，所以要重建）。"""
        from storygraph.story.dsl import Symbols
        _sym, reg = _symbols(_make(tmp_path))
        reg.images['lan_extra'] = {'ID': 'lan_extra', 'Tag': 'lan',
                                   'Attributes': 'tableword',
                                   'Path': 'game/x.png',
                                   'Category': 'character'}
        sym2 = Symbols(reg)                    # 用同一个 registry 重建
        assert 'tableword' in sym2.attrs_of_tag('lan')
        assert _warn('lan tableword', sym2) == ''

    def test_new_definition_is_picked_up_after_editing_the_rpy(self, tmp_path):
        """改完 `.rpy` 再建 `Symbols` → 新词立刻认（不许吃旧缓存）。"""
        root = _make(tmp_path)
        sym, _ = _symbols(root)
        assert 'clothes2' in sym.defined_words()
        # 加一个新代号
        (root / 'game' / 'config.rpy').write_text(
            INIT.replace('"lavender", ', '')
            .replace('"lan": ["aa", "clothes2"],',
                     '"lan": ["aa", "clothes2", "lavender"],'),
            encoding='utf-8')
        sym2, _ = _symbols(root)
        assert 'lavender' in sym2.defined_words()


class TestItDoesNotMiss:
    """**不漏**：没定义的东西，不管是没用过、还是**写进了剧本**，都要报。"""

    def test_word_never_defined_and_never_used(self, tmp_path):
        sym, _ = _symbols(_make(tmp_path))
        msg = _warn('lan ba happy', sym)
        assert 'ba' in msg

    def test_word_used_in_the_script_but_never_defined(self, tmp_path):
        """⭐ 用户的原话："万一剧本错了呢！万一我用了 `ba` 呢？"

        `ba` 在剧本里 `show` 了三次，但**哪里都没定义** → 必须报。
        上一版（"用过就算认识"）在这里会放过 —— 那是循环论证。
        """
        script = ('label start:\n'
                  '    show lan ba happy\n'
                  '    lan happy\n'
                  '    show lan ba close_happy\n'
                  '    return\n')
        sym, _ = _symbols(_make(tmp_path, script=script))
        msg = _warn('lan ba happy', sym)
        assert 'ba' in msg, '剧本里写过的错词被放过了（循环论证）'

    def test_dict_values_are_not_words(self, tmp_path):
        """`{"aa": "普通"}` —— "普通"是说明，不该因此变成合法属性名。"""
        sym, _ = _symbols(_make(tmp_path))
        assert '普通' not in sym.defined_words()

    def test_audio_paths_are_not_words(self, tmp_path):
        sym, _ = _symbols(_make(tmp_path))
        words = sym.defined_words()
        assert 'bgm/ed.mp3' not in words
        assert not any('/' in w for w in words)


class TestOrderStillDoesNotMatter:
    """上一轮的不变量不许被这次改动弄坏（I12）。"""

    @pytest.mark.parametrize('spec', [
        'lan aa happy', 'lan happy aa', 'lan happy aa clothes2',
        'lan clothes2 aa happy',
    ])
    def test_defined_words_in_any_order_are_silent(self, tmp_path, spec):
        sym, _ = _symbols(_make(tmp_path))
        assert _warn(spec, sym) == '', spec

    @pytest.mark.parametrize('spec', [
        'lan ba happy', 'lan happy ba', 'lan ba aa happy', 'lan happy aa ba',
    ])
    def test_undefined_words_in_any_order_are_reported(self, tmp_path, spec):
        sym, _ = _symbols(_make(tmp_path))
        assert 'ba' in _warn(spec, sym), spec


class TestNoDefinitionAtAll:
    """一个 `init python` 都没有 → 只认声明，别把声明外的东西放过。"""

    def test_without_init_python_aa_is_reported(self, tmp_path):
        sym, _ = _symbols(_make(tmp_path, init='init python:\n    pass\n'))
        assert 'aa' in _warn('lan aa happy', sym)

    def test_broken_python_block_does_not_take_down_the_hint(self, tmp_path):
        """`init python` 写坏了（语法错）→ 这一路空转，但不许崩。"""
        sym, _ = _symbols(_make(tmp_path, init='init python:\n    def (: pass\n'))
        assert _warn('lan happy', sym) == ''
        assert 'aa' in _warn('lan aa happy', sym)
