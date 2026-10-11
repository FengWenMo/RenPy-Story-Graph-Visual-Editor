"""
「报出来」判据（路线图 §8.9 的两个待做项 + 扫描进事实）
=========================================================

方案的一句北极星：**能用引擎自己拿到的，就不让人填；引擎不提供的语义才由人补；
引擎和人都不说的，靠扫描发现并报警 —— 而扫描永不宣称"只有这些"。**

这个文件钉住"最后那一步"：**结论要真的出现在报告里**。

  ① `EF.unrecognized_by_tag()`（纯）：只汇总、不过滤、保序去重；
  ② `app.beats.unknown_attrs_by_tag()`：项目级汇总，与卡片那条提示**同源**；
  ③ 体检报告【⑩ 段】：有就出现、没有就整段不出现；
  ④ `derived.undeclared_heads`：扫描进引擎事实（**带闸门**，不再教错人）；
  ⑤ 构建报告：`guard['statements']` **真的被打印**（它一直只写在 json 里）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core import engine_facts as EF                        # noqa: E402

#: ⚠ `attributes` / `candidates` 的**真形状是按 tag 分组的**
#: （`{'lan': [{'group':…, 'attribute':…}]}`）—— 我第一版写成了平铺列表，
#: 于是 `attributes_of(facts,'lan')` 恒为空、`unrecognized_attrs` 一个字都不说，
#: 判据以"两边都是空集 → 相等"的假象通过（当场抓到）。
LAN_ATTRS = {'lan': [{'group': 'expressions', 'attribute': 'happy'},
                     {'group': 'expressions', 'attribute': 'sad'},
                     {'group': 'outfit', 'attribute': 'clothes2'}]}
LAN_CANDS = {'lan': ['happy', 'sad', 'clothes2']}


def _facts(attrs=None, cands=None, *, parse_clean=True, probe=True):
    return EF.build_facts(
        {'attributes': [(EF.SRC_PROBE, attrs if attrs is not None else LAN_ATTRS)],
         'candidates': [(EF.SRC_PROBE, cands if cands is not None else LAN_CANDS)]},
        collection={'parse_clean': parse_clean, 'probe_complete': probe,
                    'lint_exit': 0})


def test_the_fixture_really_carries_facts():
    """
    夹具自检：**别让"两边都是空集"冒充"一致"**（我第一版就是这么假过的）。
    这条钉住"夹具本身是有内容的"。
    """
    facts = _facts()
    assert EF.attributes_of(facts, 'lan'), '夹具的 attributes 没被读到'
    assert EF.candidates_of(facts, 'lan') == ['happy', 'sad', 'clothes2']
    assert EF.unrecognized_attrs(facts, 'lan', ['sideeye']) == ['sideeye']


# ==================== ① 纯函数 ====================

def test_unrecognized_by_tag_merges_and_dedupes():
    got = EF.unrecognized_by_tag([('lan', ['sideeye', 'mask']),
                                  ('lan', ['sideeye', 'base_hand_up']),
                                  ('ye', ['uh'])])
    assert got == {'lan': ['sideeye', 'mask', 'base_hand_up'], 'ye': ['uh']}


def test_unrecognized_by_tag_drops_empty():
    assert EF.unrecognized_by_tag([('', ['x']), ('lan', []), ('lan', ['']),
                                   None, ('ye', ['uh'])]) == {'ye': ['uh']}
    assert EF.unrecognized_by_tag(None) == {}


# ==================== ② 项目级汇总（与卡片同源） ====================

class _Beat:
    """只带 `show` 的最小拍（`beat_sprite_rows` 认这个字段）。"""

    def __init__(self, show):
        self.show = show


def test_unknown_attrs_by_tag_agrees_with_the_per_beat_helper():
    """
    ⚠ 两条路必须**同源**：卡片逐拍提示用 `beat_unknown_attrs`，
    项目级汇总用 `unknown_attrs_by_tag` —— 算法不一就会出现
    "卡片说没事、报告说有事"这种最难查的分歧。
    """
    from storygraph.app.beats import (beat_unknown_attrs,
                                      unknown_attrs_by_tag)
    facts = _facts()
    beats = [_Beat('lan sideeye happy'), _Beat('ye uh')]
    by_tag = unknown_attrs_by_tag(beats, facts)
    flat = {(h['tag'], h['attr']) for b in beats for h in beat_unknown_attrs(b, facts)}
    from_tag = {(t, a) for t, attrs in by_tag.items() for a in attrs}
    assert from_tag == flat, (from_tag, flat)
    assert by_tag['lan'] == ['sideeye'], by_tag
    # ⚠ `ye` **不在**汇总里，这是**对的**：引擎对这个 tag 什么都没说
    #   （`unrecognized_attrs` 的第 2 条"不宣称"）—— 不能因为脚本里写了 `ye uh`
    #   就报"引擎不认 uh"。判据把这条性质也钉住。
    assert 'ye' not in by_tag, by_tag
    assert EF.unrecognized_attrs(facts, 'ye', ['uh']) == []


def test_unknown_attrs_by_tag_says_nothing_without_facts():
    """没采过事实 / 采得不干净 → 一个字都不宣称。"""
    from storygraph.app.beats import unknown_attrs_by_tag
    beats = [_Beat('lan sideeye')]
    assert unknown_attrs_by_tag(beats, None) == {}
    assert unknown_attrs_by_tag(beats, {}) == {}
    assert unknown_attrs_by_tag(beats, _facts(probe=False)) == {}


def test_unknown_attrs_by_tag_ignores_bare_tags_and_keywords():
    """只有 tag 没有属性 → 无从谈起；`at`/`with` 是关键字不是属性（E2 踩过的坑）。"""
    from storygraph.app.beats import unknown_attrs_by_tag
    facts = _facts()
    assert unknown_attrs_by_tag([_Beat('lan'), _Beat('lan at center')], facts) == {}
    got = unknown_attrs_by_tag([_Beat('lan happy')], facts)
    assert got == {}, got


def test_atl_block_colon_is_not_part_of_the_attribute():
    """
    ⚠ 真机实测（2026-10-11）：脚本里有

        show ling heihei:
            zoom 0.15

    ——末尾的 `:` 是**块开始**，不是属性的一部分。不剥掉就会报出
    `heihei:` 这种不存在的属性名，还会把同一属性拆成 `heihei` / `heihei:` 两条。
    （这条是**用真项目跑汇总时看出来的**，不是在单测里想出来的。）
    """
    from storygraph.app.beats import beat_unknown_attrs, unknown_attrs_by_tag
    facts = _facts(attrs={'ling': [{'group': 'expressions', 'attribute': 'happy'}]},
                   cands={'ling': ['happy']})
    beat = _Beat('ling heihei:')
    got = beat_unknown_attrs(beat, facts)
    assert got == [{'tag': 'ling', 'attr': 'heihei'}], got
    assert unknown_attrs_by_tag([beat], facts) == {'ling': ['heihei']}
    # 同一个属性不许被拆成两条
    both = unknown_attrs_by_tag([_Beat('ling heihei'), _Beat('ling heihei:')], facts)
    assert both == {'ling': ['heihei']}, both


# ==================== ③ 体检报告【⑩ 段】 ====================

def _minimal_report(**kw):
    base = {'ok': True, 'problems': 0, 'counts': {'images': 1, 'music': 0,
                                                 'sounds': 0, 'voices': 0},
            'missing': {}, 'unregistered': {}, 'outside_game': {},
            'duplicate_ids': [], 'duplicate_paths': [], 'no_path': [],
            'placeholders': [], 'total_rows': 1}
    base.update(kw)
    return base


def test_health_report_has_no_unknown_section_without_data():
    from storygraph.app.asset_report import format_asset_health
    text = format_asset_health(_minimal_report())
    assert '⑩' not in text, '没有数据就不许出现这一段（不宣称）'


def test_health_report_lists_the_unknown_attrs():
    from storygraph.app.asset_report import format_asset_health
    text = format_asset_health(_minimal_report(
        engine_unknown={'lan': ['sideeye', 'mask'], 'ye': ['uh']}))
    assert '【⑩' in text
    assert '3 处' in text and '2 个角色' in text, text
    assert 'lan: sideeye、mask' in text and 'ye: uh' in text
    # 口径必须写在报告里：只提示，不是要删
    assert '不是"要删掉"' in text or '不是要删' in text, text
    assert '照样能选' in text or '不影响导入' in text, text


# ==================== ④ 扫描进引擎事实（带闸门） ====================

SRC = '''label ch01:
    scene bg room
    with dissolve
    pause 1.0
    lyj "台词一"
    weather rain 强度=3
    weather snow 强度=1
    return
'''


def test_scan_goes_into_engine_facts_with_the_gates(tmp_path):
    """
    `derived.undeclared_heads`：**采集时**把扫描结果算一次，随事实一起进版本库。

    ⚠ 它必须走 V2-1 之后的**闸门版**扫描器 —— `with`/`pause` 这类内置语句
    **不许**出现在这份清单里（否则又是"工具在输出错误知识"）。
    """
    from storygraph.pipeline.engine_collect import _derived_undeclared_heads
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch01.rpy').write_text(SRC, encoding='utf-8')
    got = _derived_undeclared_heads(root)
    heads = [r['head'] for r in got]
    assert heads == ['weather'], got
    assert got[0]['count'] == 2
    assert got[0]['files'] == ['ch01.rpy'], got[0]
    for builtin in ('with', 'pause', 'window', 'scene'):
        assert builtin not in heads, f'{builtin} 是内置语句，不该进这份清单'


def test_scan_in_facts_fails_soft(tmp_path):
    """扫不动（不是工程根 / 解析失败）→ 空列表，绝不编。"""
    from storygraph.pipeline.engine_collect import _derived_undeclared_heads
    assert _derived_undeclared_heads(tmp_path / '不存在') == []


# ==================== ⑤ 构建报告真的打印出来 ====================

def test_build_output_prints_the_statements_section():
    """
    `guard['statements']`（未声明 / 工具不认识的语句头 / CST 真迹覆盖率）
    一直只写进 `build_report.json`，**控制台上一个字都看不到** ——
    路线图 §8.9 的"报出来"就是这个。

    ⚠ 用的是**真** `format_build_result`（不是重写一份），否则判据测的是我的复述。
    """
    from storygraph.pipeline.build import BuildResult, format_build_result
    res = BuildResult(ok=True, engine='renpy', out_dir='out',
                      artifacts=[], report_path='', report={
                          'build': {'ok': True, 'stats': {},
                                    'guard': {'statements':
                                              '⚠ 未声明（只靠形状猜）：weather×12'
                                              ' —— 工具不知道它们的参数/约束'}},
                          'issues': []})
    text = format_build_result(res)
    assert '语句与能力' in text, text
    assert 'weather×12' in text, text


def test_build_output_omits_the_section_when_empty():
    from storygraph.pipeline.build import BuildResult, format_build_result
    res = BuildResult(ok=True, engine='renpy', out_dir='out',
                      artifacts=[], report_path='', report={
                          'build': {'ok': True, 'stats': {}, 'guard': {}},
                          'issues': []})
    assert '语句与能力' not in format_build_result(res)
