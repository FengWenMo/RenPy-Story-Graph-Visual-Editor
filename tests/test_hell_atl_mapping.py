"""
tests/test_hell_atl_mapping.py
==============================
**带 ATL 块的 show × 语句归属 × 映射模块**的地狱测试（2026-10-31）。

与 `tests/test_hell_custom_module.py` 的分工
-------------------------------------------
那份（58 条，10-17 写）攻击的是"**教工具认识自定义语法**"那条链路：
`pattern.match_line` / 参数反推 / 落盘 / 卡片读值。

这一份攻击的是**另外三块**，它们共享同一条铁律 ——
**`.rpy` 是唯一真源，映射只影响屏幕**：

  · **ATL 块**：`show x at y:` + 缩进块。字节对了但工具以为台上没人的
    那种失败（真机 33 处），只有"结构 + 字节"**两条一起判**才抓得住；
  · **语句归属**：每条语句都必须有落点，不许静默丢（P1 审计）；
  · **映射模块**：位置/音乐/背景/角色/过场/服装六条，**双向 + 搜索 + 作用域**。

判据只有两种
------------
  ① 渲染出来的字节与原文逐字节相同（或只差我们明确承认的那几处）；
  ② 屏幕上的显示变了，但**产物一个字节都没动**。

跑法
----
有 pytest：`python -m pytest tests/test_hell_atl_mapping.py -q`
没 pytest：`python tests/test_hell_atl_mapping.py`（末尾自带运行器，只用标准库）
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.vocabulary import (EMPTY, Vocabulary, cue_terms,
                                          from_mapping)
from storygraph.core.statement import Statement, StatementKind
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import (_HANDLER_OF, _BeatBuilder,
                                         beats_from_parse_result,
                                         beats_from_statements)
from storygraph.story.beat_render import render_beats_to_rpy


# ---------------------------------------------------------------------------
#  工具
# ---------------------------------------------------------------------------

def _parse(src: str, name: str = 'a.rpy'):
    """源码文本 → `ParseResult`（临时目录里落一份真文件再解析）。"""
    td = tempfile.TemporaryDirectory()
    f = Path(td.name) / name
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return pr, td


def _render(src: str) -> str:
    pr, td = _parse(src)
    try:
        return render_beats_to_rpy(beats_from_parse_result(pr))
    finally:
        td.cleanup()


def _beats(src: str):
    pr, td = _parse(src)
    try:
        return beats_from_parse_result(pr)
    finally:
        td.cleanup()


def _window(src: str, needle: str, before: int = 1, after: int = 4) -> list:
    """渲染结果里第一处命中 needle 的前后若干行（用于精确比对缩进）。"""
    out = _render(src).split('\n')
    for i, ln in enumerate(out):
        if needle in ln:
            return out[max(0, i - before):i + after]
    return []


# 真机 chapter3_1.rpy 里三种真实长相
ATL_PLAIN = '''label start:
    show lan happy at right:
        zoom 0.15
        yoffset 200
    with dissolve
    l "ok"
    return
'''

ATL_WEIRD_INDENT = '''label start:
    show lan sad:
            zoom 0.15
            yoffset 200
    l "ok"
    return
'''

ATL_EMPTY_LINE = '''label start:
    show lan oh:
        zoom 0.15

        yoffset 200
    l "ok"
    return
'''

HELL_SRC = '''label start:
    scene bg room
    show lan happy at right:
        zoom 0.15
        yoffset 200
    with dissolve
    l "一句台词"
    # 给美术的标注
    $ flag = 1
    show ling sad
    l "第二句"
    return
'''


# ---------------------------------------------------------------------------
#  A. ATL 块：结构进 beat，块体逐字跟走
# ---------------------------------------------------------------------------

def test_atl_block_roundtrip_is_byte_identical(tmp_path=None):
    """`show x at y:` + 块体 + `with` 三者的缩进一字节不差。

    P0 的核心判据。从前带块的 show 整条落 `known_block` ——
    产物没错，但工具以为台上没人。
    """
    got = _window(ATL_PLAIN, 'zoom 0.15')
    assert got == ['    show lan happy at right:',
                   '        zoom 0.15',
                   '        yoffset 200',
                   '    with dissolve',
                   '    l "ok"'], got


def test_atl_block_keeps_author_extra_indent(tmp_path=None):
    """作者多缩进两格（12 格）也要原样保留 —— 排版是他的，工具不"修正"。"""
    got = _window(ATL_WEIRD_INDENT, 'zoom 0.15', after=3)
    assert got == ['    show lan sad:',
                   '            zoom 0.15',
                   '            yoffset 200',
                   '    l "ok"'], got


def test_atl_block_keeps_blank_line_inside(tmp_path=None):
    """块体里的空行是作者的分段，不许被吃掉。"""
    got = _window(ATL_EMPTY_LINE, 'zoom 0.15')
    assert got == ['    show lan oh:',
                   '        zoom 0.15',
                   '',
                   '        yoffset 200',
                   '    l "ok"'], got


def test_atl_block_still_puts_actor_on_stage(tmp_path=None):
    """**语义判据**：带块的 show 必须让 card 层知道"台上有这个人"。

    与"字节相同"是两件事：字节相同只说明没弄坏文件；
    这条说明工具真的读懂了"谁在台上"（卡片 / 预览 / 跨章连续性都靠它）。
    """
    shown = [b.show for b in _beats(ATL_PLAIN) if getattr(b, 'show', '')]
    assert any('lan' in s for s in shown), shown
    assert any(s.rstrip().endswith(':') for s in shown), shown


def test_atl_block_order_show_then_block_then_with(tmp_path=None):
    """顺序不许乱：`show …:` → 块体 → `with`。

    第一版把 opaque 令牌插在 `mark_op('show')` 之前，产物变成
    `zoom 0.15` 在 `show` 上面 —— 引擎直接语法错（实测踩到）。
    """
    out = _render(ATL_PLAIN)
    i_show = out.index('show lan happy at right:')
    i_zoom = out.index('zoom 0.15')
    i_with = out.index('with dissolve')
    assert i_show < i_zoom < i_with, out


def test_atl_block_does_not_leak_into_the_next_beat(tmp_path=None):
    """块体只属于它那一拍：下一句台词前后不许夹着 zoom。"""
    out = _render(ATL_PLAIN)
    tail = out[out.index('with dissolve'):]
    assert 'zoom' not in tail and 'yoffset' not in tail, out


# ---------------------------------------------------------------------------
#  B. 归属审计：每条语句都必须有落点
# ---------------------------------------------------------------------------

def test_every_statement_kind_has_a_handler():
    """`StatementKind` 全集必须都有处理分支。

    P1 的静态判据：处理表从前是 `add()` 里的临时 dict，新增 kind 忘接线
    → 语句被静默丢掉、导出少一行、测试全绿。
    """
    all_kinds = {v for k, v in vars(StatementKind).items()
                 if not k.startswith('_') and isinstance(v, str)}
    missing = sorted(all_kinds - set(_HANDLER_OF))
    assert not missing, f'这些 kind 没有处理分支：{missing}'


def test_unknown_kind_is_preserved_not_dropped():
    """读到不认识的 kind（新版表 / 手改 JSON）→ 原样保真，不许丢。

    这是 P1 审计当场抓到的真缺口：`UNKNOWN` 从前没有任何分支。
    """
    st = Statement.from_dict({'kind': 'future_kind_xyz',
                              'payload': {'text': 'mycue "闪" intensity=3'}})
    assert st.kind == StatementKind.UNKNOWN
    report = []
    beats = beats_from_statements([st], label='x', report=report)
    assert not report, report
    assert any(b.opaque == 'mycue "闪" intensity=3' for b in beats), \
        [b.opaque for b in beats]
    assert 'mycue "闪" intensity=3' in render_beats_to_rpy(beats, label_name='x')


def test_a_brand_new_kind_is_reported_not_silently_lost():
    """连 handler 都没有的 kind → 必须进 `unassigned`（不许装作没发生）。"""
    class Fake:
        kind = 'weird_new_kind'
        payload = {'text': ''}
        condition = ''
        source = ('a.rpy', 7)

    builder = _BeatBuilder('x')
    builder.add(Fake(), 'x')
    assert builder.unassigned, builder.unassigned
    assert builder.unassigned[0]['reason'] == 'no-handler'


def test_no_statement_is_dropped_on_hell_source():
    """综合样本（带块 show + with + 注释 + `$` + 第二条 show）审计必须为空。"""
    pr, td = _parse(HELL_SRC)
    try:
        report = []
        beats_from_parse_result(pr, report=report)
    finally:
        td.cleanup()
    assert not report, report


# ---------------------------------------------------------------------------
#  C. 映射模块：六条能力 × 对抗
# ---------------------------------------------------------------------------

def _vocab():
    """覆盖六条需求的映射（`vocabulary` 段 + 两段代号）。"""
    return from_mapping({
        'characters': {'DY': {'display': '大爷'}},
        'vocabulary': [
            {'kind': 'cue', 'id': 'i11', 'label': '1人，近处'},
            {'kind': 'music', 'id': 'bgm_01', 'label': '日常', 'code': '001'},
            {'kind': 'bg', 'id': 'bg_forest', 'label': '森林', 'code': '03'},
        ],
        'outfit_codes': [
            {'char': 'ye', 'code': 'B', 'attr': 'base_swimsuit', 'label': '泳装'},
            {'char': 'lan', 'code': 'B', 'attr': 'base_hanfu', 'label': '汉服'},
        ],
    })


def test_position_transform_chinese_both_directions():
    """① `at i11` ⇄「1人，近处」：两个方向都通，且搜得到。"""
    v = _vocab()
    assert v.find('cue', 'i11').label == '1人，近处'
    assert v.find('cue', '1人，近处').id == 'i11'
    assert [t.id for t in v.search('近处')] == ['i11']
    assert [t.id for t in v.search('i11')] == ['i11']


def test_music_chinese_name_and_number():
    """② 中文名 BGM ⇄ 纯数字编号。"""
    v = _vocab()
    assert v.find('music', 'bgm_01').label == '日常'
    assert v.find('music', '001').id == 'bgm_01'
    assert v.find('music', '日常').id == 'bgm_01'


def test_background_code():
    """③ 背景文件 ⇄ `bg+数字` 代号。"""
    v = _vocab()
    assert v.find('bg', 'bg_forest').code == '03'
    assert v.find('bg', '03').id == 'bg_forest'


def test_character_display_name():
    """④ `DY` ⇄ 大爷。"""
    v = _vocab()
    assert v.find('character', 'DY').label == '大爷'
    assert v.find('character', '大爷').id == 'DY'


def test_custom_cue_is_mapped_and_searchable():
    """⑤ 自定义过场调用：中文映射 + **搜索命中**（最难的一条）。"""
    v = Vocabulary(tuple(cue_terms([{'head': 'my_wipe', 'label': '擦除'}])))
    assert v.find('cue', 'my_wipe').label == '擦除'
    assert v.find('cue', '擦除').id == 'my_wipe'
    assert [t.id for t in v.search('擦除')] == ['my_wipe']


def test_costume_code_is_scoped_per_character():
    """⑥ 同一个代号 `B` 在不同角色身上是不同衣服（作用域不许串）。"""
    v = _vocab()
    assert v.find('costume', 'B', char='ye').id == 'base_swimsuit'
    assert v.find('costume', 'B', char='lan').id == 'base_hanfu'


def test_mapping_wins_over_calls_label():
    """映射是"改名"的正本：两边都登记同一个 head 时**映射赢**。

    ⚠ `Vocabulary.plus()` 的语义是**后来的盖前面的**，顺序写反行为就反，
    而且两边都自洽、都不报错 —— 生产代码是
    `Vocabulary(cue_terms(...)).plus(映射)`，这个方向不许改。
    """
    calls = [{'head': 'my_wipe', 'label': '擦除'}]
    vocab = from_mapping({
        'vocabulary': [{'kind': 'cue', 'id': 'my_wipe', 'label': '雪花擦除'}],
    })
    merged = Vocabulary(tuple(cue_terms(calls))).plus(vocab.as_terms())
    assert merged.find('cue', 'my_wipe').label == '雪花擦除'
    # 反过来写（错的）会得到「擦除」—— 记在这里，免得有人"顺手调顺序"
    assert vocab.plus(cue_terms(calls)).find('cue', 'my_wipe').label == '擦除'


def test_ambiguous_label_is_not_guessed():
    """两条都像的，一条都不认（宁可空，也不猜）。"""
    v = from_mapping({'vocabulary': [
        {'kind': 'cue', 'id': 'wiggle_a', 'label': '抖动慢'},
        {'kind': 'cue', 'id': 'wiggle_b', 'label': '抖动快'},
    ]})
    assert v.find('cue', '抖动慢').id == 'wiggle_a'     # 精确仍然命中
    assert v.find('cue', '抖动X').id == ''              # 模糊 → 不认


def test_unknown_id_returns_empty_not_guessed():
    """查不到就是空 —— 屏幕显示原名，不空白也不猜。"""
    v = _vocab()
    assert v.find('cue', 'i99').id == ''
    assert v.find('music', '不存在的曲子').id == ''


def test_blank_label_does_not_break_lookup():
    """只登记 id、label 空着，按 id 查仍要通。"""
    v = from_mapping({'vocabulary': [{'kind': 'cue', 'id': 'i12'}]})
    term = v.find('cue', 'i12')
    assert term.id == 'i12' and term.label == ''


# ---------------------------------------------------------------------------
#  D. 联合：映射不许碰代码
# ---------------------------------------------------------------------------

def test_mapping_never_changes_the_rpy_bytes():
    """**最重要的一条**：查过映射之后，导出字节必须完全不变。

    映射只影响屏幕（label / code），`.rpy` 是唯一真源。这条一红，说明
    "显示层"偷偷改到了"产物层" —— 本工具最不可接受的失败。
    """
    without = _render(HELL_SRC)
    v = _vocab()
    v.find('cue', 'i11')
    v.search('近处')
    v.find('costume', 'B', char='ye')
    assert _render(HELL_SRC) == without


def test_hell_source_survives_two_full_roundtrips():
    """幂等：渲染一次与渲染两次的产物必须相同（不许"越存越歪"）。"""
    once = _render(HELL_SRC)
    twice = _render(once)
    assert once == twice, f'第一次与第二次不一致：\n{once}\n---\n{twice}'


def test_non_ascii_speaker_and_text_survive():
    """中文说话人 / 中文台词在带块 show 的同一拍里不许乱。"""
    out = _render(HELL_SRC)
    assert 'l "一句台词"' in out and 'l "第二句"' in out


# ---------------------------------------------------------------------------
#  无 pytest 时的运行器（只用标准库；有 pytest 时不会执行）
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import inspect
    import traceback

    funcs = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    failed = 0
    for name, fn in funcs:
        try:
            # 带 `tmp_path` 的用例在这里传 None（它们自己用 TemporaryDirectory）；
            # 不带的直接调用 —— 统一传参会 TypeError，别让运行器自己骗自己。
            if inspect.signature(fn).parameters:
                fn(None)
            else:
                fn()
            print(f'  OK   {name}')
        except Exception:                          # noqa: BLE001
            failed += 1
            print(f'  FAIL {name}')
            traceback.print_exc()
    print(f'\n{len(funcs) - failed}/{len(funcs)} 通过')
    raise SystemExit(1 if failed else 0)
