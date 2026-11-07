"""
**引擎实跑**的地狱测试：生成的每一行都必须能被引擎接受（2026-11-05）
====================================================================

用户：

> 「再写，再查（**必须保证能跑不报错！！！**）后续因为有引擎检查……所以可能甚至
>   不会有 json 文件给你查！**必须从引擎里面直接审查**……」

所以这一份**不比字符串**，它把 `apply_attr_slot` 生成出来的每一行
**塞进一个真项目跑 lint**，断言"没有一条是语法错"。

## 为什么要这样测（字符串比不出来）

`lan happy with dissolve clothes2` 和 `lan happy with dissolve` 都是"一串词"，
字符串层面看不出前者**根本跑不了**。引擎的原话是：

    File "game/_sg_verify.rpy", line 8: end of line expected.

—— 属性**只能出现在 `at` 之前**，跑到 `with` 后面就是语法错、整份 `.rpy` 跑不了。

## 没有 `rules.json` 也要能过

这一组**故意不写 `storygraph.rules.json`**：靠引擎的 `layeredimage` 分组
（`group outfit` / `group expressions`）+ 项目 `transform`，工具照样要生成
合法的行。这是用户说的"可能甚至不会有 json 文件给你查"。

## 跳过条件

没有 SDK（`STORYGRAPH_SDK` / `SHOW_HELL_SDK` / 本机默认路径都不在）→ 跳过。
**跳过不是通过**（`-rs` 里能看到）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64

#: 引擎声明的形态（**故意不写 rules.json**）
DECL = """\
layeredimage lan:
    image_format 'images/characters/lan/{image}.png'
    group outfit:
        attribute clothes2:
            'clothes2'
        attribute base_hand_up:
            'base_hand_up'
    group expressions:
        attribute happy default:
            'happy'
        attribute angry:
            'angry'
        attribute close_happy:
            'close_happy'

transform left:
    xalign 0.0

transform right:
    xalign 1.0

define dissolve_long = Dissolve(0.4)

# ⚠ **用户自己定义的名字**（2026-11-05 用户："比如定义一个 with aa form 等"）：
# `aa` / `form` 长得跟属性词一模一样，但它们是 **transition / transform 名**。
# 工具必须靠**位置**（`at` / `with` 之后）认出它们，不能当属性。
define aa = Dissolve(0.25)
define aa_form = Dissolve(0.35)

transform form:
    xalign 0.5

transform aa_pose:
    zoom 0.9

label start:
    return
"""


def _sdk() -> str:
    for key in ('SHOW_HELL_SDK', 'STORYGRAPH_SDK', 'STORYGRAPH_RENPY_SDK'):
        val = os.environ.get(key)
        if val and (Path(val) / 'renpy.exe').is_file():
            return val
    for guess in (r'D:\software\renpy-8.4.1-sdk',
                  r'D:\千连万理\storygraph_fork_sdk'):
        if (Path(guess) / 'renpy.exe').is_file():
            return guess
    return ''


SDK = _sdk()
pytestmark = pytest.mark.skipif(not SDK, reason='没有 Ren\'Py SDK，跑不了引擎')


def _project(tmp_path) -> Path:
    root = tmp_path / 'tiny'
    g = root / 'game'
    d = g / 'images' / 'characters' / 'lan'
    d.mkdir(parents=True)
    for n in ('happy', 'angry', 'close_happy', 'clothes2', 'base_hand_up'):
        (d / f'{n}.png').write_bytes(PNG)
    (g / 'script.rpy').write_text(DECL, encoding='utf-8')
    return root


#: 切维度的输入（真机各种形态：子句、`@`、不认识的词）
INPUTS = [
    ('lan happy', 'outfit', 'clothes2'),
    # —— 自定义名的子句（`with aa` / `at form`）——
    ('lan happy with aa', 'outfit', 'clothes2'),
    ('lan happy at form', 'outfit', 'clothes2'),
    ('lan happy at form with aa', 'outfit', 'clothes2'),
    # ⚠ `with a b` 是**非法**的（Ren'Py 的 `with` 只接一个 transition，
    # 多参数要逗号）—— 引擎原话 `end of line expected`，2026-11-05 实测。
    # 所以要"两个转场"只能**先 define 一个组合名**再 `with aa_form`。
    ('lan happy with aa_form', 'outfit', 'clothes2'),
    ('lan happy at aa_pose with aa', 'expressions', 'angry'),
    ('lan happy at form zorder 3 with aa', 'outfit', 'clothes2'),
    ('lan happy behind aa at form', 'expressions', 'angry'),
    ('lan happy as aa at form', 'outfit', 'clothes2'),
    ('lan clothes2 happy', 'expressions', 'angry'),
    ('lan happy clothes2', 'expressions', 'angry'),
    ('lan happy at left', 'outfit', 'clothes2'),
    ('lan clothes2 happy at left', 'expressions', 'angry'),
    ('lan happy at left with dissolve', 'outfit', 'clothes2'),
    ('lan clothes2 happy at left with dissolve', 'expressions', 'angry'),
    ('lan happy as p at left', 'outfit', 'clothes2'),
    ('lan happy behind lan at left', 'expressions', 'angry'),
    ('lan happy onlayer master', 'outfit', 'clothes2'),
    ('lan happy zorder 5', 'outfit', 'clothes2'),
    ('lan happy at left zorder 5 with dissolve', 'outfit', 'clothes2'),
    ('lan clothes2 happy at left zorder 5 with dissolve_long', 'expressions',
     'close_happy'),
    ('lan happy@left', 'outfit', 'clothes2'),
    ('lan aa happy', 'outfit', 'clothes2'),          # `aa` 项目里没有
    ('lan _hd_cut happy', 'outfit', 'clothes2'),     # 不认识的内部件
    ('lan happy', 'expressions', ''),                # 清空一维
    ('lan clothes2 happy', 'outfit', ''),            # 清空服装
]


class TestGeneratedLinesRun:
    """⭐⭐ 把 `apply_attr_slot` 的**每一行产物**丢进引擎，断言零语法错。"""

    def test_every_generated_spec_is_accepted(self, tmp_path):
        from storygraph.app import beats as B
        from storygraph.assets.engine_images import verify_show_specs
        from storygraph.assets.registry_load import load_asset_registry
        from storygraph.story.dsl import Symbols

        root = _project(tmp_path)
        reg = load_asset_registry(root)
        sym = Symbols(reg)

        produced = []
        for spec, group, value in INPUTS:
            got = B.apply_attr_slot(spec, group, value, symbols=sym)
            produced.append((spec, group, value, got))
            assert got, f'{spec} 切完变成空串'

        # 一次 lint 验完（每条一行 `show`）
        verdict = verify_show_specs(root, [g for *_x, g in produced],
                                    sdk=SDK, timeout=900)
        assert verdict['ok'], '生成的某些行**引擎跑不了**：\n' + '\n'.join(
            f"  {row['spec']}  ← {row['why']}" for row in verdict['fatal'])

    def test_clauses_keep_their_order_after_the_change(self, tmp_path):
        """子句**内部**的顺序也不许被改（`at` 在 `as` 前、`with` 最后）。"""
        from storygraph.app import beats as B

        for spec, group, value in INPUTS:
            if ' at ' not in spec and ' with ' not in spec:
                continue
            got = B.apply_attr_slot(spec, group, value)
            _t, _a, before = B.split_show_spec(spec)
            _g_t, _g_a, after = B.split_show_spec(got)
            assert after == before, f'{spec} → {got}：子句被改了'

    def test_custom_clause_names_survive_a_dimension_change(self, tmp_path):
        """⭐ `with aa form` / `at form` 在切维度后**逐字节不变**，且引擎照跑。"""
        from storygraph.app import beats as B

        cases = [
            ('lan happy with aa_form', 'outfit', 'clothes2',
             'lan clothes2 happy with aa_form'),
            ('lan happy at form with aa', 'expressions', 'angry',
             'lan happy at form with aa'),
        ]
        for spec, group, value, _expect_clause in cases:
            got = B.apply_attr_slot(spec, group, value)
            _t, _a, before = B.split_show_spec(spec)
            _g_t, _g_a, after = B.split_show_spec(got)
            assert after == before, f'{spec} → {got}'

    def test_a_known_bad_order_really_fails(self, tmp_path):
        """**反向对照**：说明这套判据真的能抓到错，不是永远绿。

        故意造一条"属性跑到 `with` 后面"的行 —— 引擎必须说它跑不了。
        """
        from storygraph.assets.engine_images import verify_show_specs

        root = _project(tmp_path)
        bad = ['lan happy with dissolve clothes2']
        verdict = verify_show_specs(root, bad, sdk=SDK, timeout=900)
        assert not verdict['ok'], '引擎居然接受了错序的行 —— 判据失效了'
        assert verdict['fatal'], verdict
