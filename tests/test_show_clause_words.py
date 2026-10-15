"""
`show` / `scene` / `hide` 子句词：**一处真源 + 三个 verb 各自的口径**
================================================================================

这一份钉三件事（2026-10-15，用户审核后定的优先级）：

  ① **词表只有一处真源** —— `core.vocabulary.SHOW_CLAUSE_KEYWORDS` /
     `SHOW_INTRO_KEYWORDS`。各站只许写"内核 ± 显式增减"，不许再抄一份
     （从前五处各一份、口径已经不同：`excel_export` 有 `on` 却漏了 `onlayer`；
     `renpy_parser` 的 scene 集更窄；`learn_statement` 手抄还缺 `zorder`）。
  ② **`scene` 与 `show` 同口径** —— 官方原文："the scene statement takes the
     same properties as the show statement"。从前 `scene bg behind x` 会把
     `behind x` 当成图像名的一部分。
  ③ **非图像目标不产生"图像引用"** —— `show screen X` / `show layer X` /
     `show text` / `show expression F(x)` 里的 X **不是本项目图像资产**，
     不许进 `registry.references`/`unresolved`。
     ⚠ 真机实测（2026-10-15）：**正则兜底那条路**会造出 **110 条**假断链
     （`unresolved['image']` 126 条里 110 条是 `screen …`），而它
     **是无条件跟跑的**（`build.collect_report` / `asset_engine._rebuild_references`
     都是"Statement 优先 + 正则补覆盖不到的地方"）—— 所以那不是理论问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.assets import asset_registry_constants as ARC        # noqa: E402
from storygraph.authoring import learn_statement as LS               # noqa: E402
from storygraph.core import vocabulary as V                          # noqa: E402
from storygraph.core.statement import StatementKind                  # noqa: E402
from storygraph.parsing.renpy_parser import (IncrementalRenPyAnalyzer,  # noqa: E402
                                            RenPyParser)
from storygraph.story import beat_stage_parse as BSP                 # noqa: E402
from storygraph.story import excel_export as EE                      # noqa: E402


# =====================================================================
#  ① 词表：一处真源
# =====================================================================

def test_各站只写内核加减_不再各抄一份():
    canon = set(V.SHOW_CLAUSE_KEYWORDS)
    intro = set(V.SHOW_INTRO_KEYWORDS)
    assert canon == {'at', 'as', 'with', 'behind', 'onlayer', 'zorder'}, sorted(canon)
    assert intro == {'expression', 'screen', 'layer'}, sorted(intro)
    # 规范集：三处都是原样
    assert set(RenPyParser._SHOW_KEYWORDS) == canon
    assert set(BSP._SHOW_ARG_KEYWORDS) == canon
    assert set(EE._SHOW_KEYWORDS) == canon, (
        '`on` 没有官方依据（官方 show 语法里没有它）、真机 0 次 —— '
        '不该留在表里', sorted(EE._SHOW_KEYWORDS))
    # 显式增减的两处
    assert set(ARC._SHOW_STOP_WORDS) == canon | intro, sorted(ARC._SHOW_STOP_WORDS)
    assert set(LS._NOT_A_VALUE) == (canon | intro
                                    | {'True', 'False', 'None'}), (
        sorted(LS._NOT_A_VALUE))
    # 藏在函数里的那一处（`at` 之后的变换列表边界）写进了源码，按源码核
    src = (ROOT / 'storygraph' / 'story' / 'generation_guard.py').read_text(
        encoding='utf-8')
    assert 'SHOW_CLAUSE_KEYWORDS - {\'at\'}' in src, (
        'generation_guard 的 stopwords 又自己写了一份')


# =====================================================================
#  ② 三个 verb 的子句（scene ≡ show）
# =====================================================================

def test_show_的子句照旧():
    r = RenPyParser._parse_show_scene('show lan happy at left with dissolve',
                                      is_scene=False)
    assert r['image'] == 'lan happy', r
    assert r['at'] == ['left'] and r['with_'] == 'dissolve', r


def test_scene_取与_show_同一批子句():
    """官方原文：`the scene statement takes the same properties as the show statement`。"""
    assert set(RenPyParser._SCENE_KEYWORDS) == set(V.SHOW_CLAUSE_KEYWORDS), (
        'scene 的子句集比 show 窄 —— `scene bg behind x` 会把 `behind x` '
        '当成图像名的一部分', sorted(RenPyParser._SCENE_KEYWORDS))
    r = RenPyParser._parse_show_scene('scene bg behind x at left', is_scene=True)
    assert r['image'] == 'bg', ('behind 被吃进图像名了', r)
    assert r['behind'] == ['x'] and r['at'] == ['left'], r


def test_show_的引入型关键字不产生图像名():
    """`show layer master` / `show screen X` 后面那个词不是**本项目图像资产**。"""
    for text in ('show layer master', 'show screen msg_dialogue'):
        r = RenPyParser._parse_show_scene(text, is_scene=False)
        assert not (r or {}).get('image'), (
            f'{text!r} 被解析出图像名了 —— 它会变成一个假断链', r)


# =====================================================================
#  ③ 非图像目标不产生"图像引用"（两条路径都要）
# =====================================================================

NON_ACTOR_LINES = [
    'show screen msg_dialogue',
    'hide screen map',
    'show layer master',
    'show text "Centered text"',
    'show expression holo("lan base")',
]


def test_正则兜底那条路不产生假断链():
    from storygraph.assets.asset_registry import extract_script_references

    for line in NON_ACTOR_LINES:
        refs = [r for r in extract_script_references(line) if r[0] == 'image']
        assert refs == [], (line, '正则兜底把它当图像引用了', refs)


def test_语句那条路也不产生假断链(write_rpy):
    from storygraph.assets.asset_registry import references_from_statements

    body = 'label a:\n' + ''.join(f'    {ln}\n' for ln in NON_ACTOR_LINES)
    fp = write_rpy(body, 'ch.rpy')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    stmts = list(getattr(pr, 'statements', None) or [])
    imgs = [r for r in references_from_statements(stmts) if r[0] == 'image']
    assert imgs == [], imgs
    # ⚠ **判据校准（2026-10-15）**：这里**不**断言"非图像目标绝不出现在 image 里"。
    #   `show text "…"` 按官方文档就是拿 `text` 这个**预定义 displayable** 当图名，
    #   解析出 `text "…"` 是**诚实的**（`movie` 同理）。要钉的是**引用那一层**：
    #   它不许变成 `registry.unresolved['image']` 里的一条断链（上面已断言）。
    #   真正"没有图像名"的只有**引入型**（screen / layer / expression）。
    from storygraph.core.vocabulary import SHOW_INTRO_KEYWORDS
    for st in stmts:
        if getattr(st, 'kind', None) in (StatementKind.SHOW, StatementKind.SCENE,
                                        StatementKind.HIDE):
            spec = str((getattr(st, 'payload', None) or {}).get('image') or '')
            head = spec.split()[0] if spec else ''
            assert head not in SHOW_INTRO_KEYWORDS, (
                '引入型关键字钻进了 SHOW/SCENE/HIDE 的 image', spec)


# =====================================================================
#  说话人关键字：`narrator` 必须当说话人（不能并进"保留字"）
# =====================================================================

def test_narrator_仍然是说话人而不是关键字():
    """
    ⚠ 为什么这条必须钉：`RENPY_STATEMENT_VERBS` 里含 `narrator`，而官方文档
    （dialogue.html）说明它是"没有角色名的 say 语句用的那个 Character"、
    且**特殊角色可以被重定义** —— 它能当说话人。
    一旦被并进"不可能当说话人"的集合，`narrator "A"` 与 `narrator "B"`
    会撞成同一个键 → **静默丢行**（违反铁律 #7）。
    """
    from storygraph.story import patcher as PT

    assert 'narrator' not in PT._KEYWORD_SPEAKERS, (
        'narrator 被当成关键字了 —— 两条不同旁白会撞键')
    assert PT._head_of('narrator "A"') != PT._head_of('narrator "B"'), (
        '两条不同旁白得到同一个身份 → 静默丢行')
    # 反面：真正不能当说话人的还得在里面
    for kw in ('extend', 'nvl', 'menu', 'show', 'at', 'onlayer'):
        assert kw in PT._KEYWORD_SPEAKERS, kw


def test_说话人关键字只统一口径_生效集合逐字未变():
    """收敛到 `core.vocabulary.RESERVED_STATEMENT_KEYWORDS` 时**不许顺手改口径**。"""
    from storygraph.story import patcher as PT

    BEFORE = {
        'extend', 'voice', 'play', 'stop', 'queue', 'show', 'hide', 'scene',
        'menu', 'nvl', 'window', 'pause', 'return', 'jump', 'call', 'label',
        'if', 'elif', 'else', 'while', 'for', 'python', 'init', 'define',
        'default', 'transform', 'screen', 'style', 'image', 'camera', 'with',
        'at', 'as', 'behind', 'onlayer', 'zorder', 'pass', 'break', 'continue',
    }
    got = set(PT._KEYWORD_SPEAKERS)
    assert got == BEFORE, ('生效集合变了', sorted(got ^ BEFORE))
    assert PT._KEYWORD_SPEAKERS is V.RESERVED_STATEMENT_KEYWORDS, (
        'patcher 又自己写了一份，没引用真源')
