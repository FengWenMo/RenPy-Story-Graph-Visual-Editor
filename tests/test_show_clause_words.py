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

def test_词表只有一份_剩下的都是显式语义():
    """
    ⚠ 2026-10-16（用户排期 C 项）把这件事**收口**了：
    从前各站各自定义 `_SHOW_KEYWORDS` / `_SHOW_ARG_KEYWORDS` / `_SCENE_KEYWORDS`
    —— 即使只是"同一份集合的别名"，也给了下一次漂移一个落脚点
    （历史上正是这么漂的：`excel_export` 漏过 `onlayer`、多过 `on`）。
    现在**切分本体只有一处**（`classify_show_words`），三个别名表已删。

    剩下的消费点只许声明**语义**，不许再拿集合去 split 目标：
      · `asset_registry_constants._SHOW_STOP_WORDS` = 规范集 + 引入型（取词边界）
      · `learn_statement._NOT_A_VALUE`             = 上面 + 布尔字面量（取词边界）
      · `generation_guard` 的 stopwords            = 规范集 − `at`（`at` 之后）
    """
    canon = set(V.SHOW_CLAUSE_KEYWORDS)
    intro = set(V.SHOW_INTRO_KEYWORDS)
    assert canon == {'at', 'as', 'with', 'behind', 'onlayer', 'zorder'}, sorted(canon)
    assert intro == {'expression', 'screen', 'layer'}, sorted(intro)

    # ① 三个"别名表"必须真的不在了（它们没有任何理由存在）
    assert not hasattr(RenPyParser, '_SHOW_KEYWORDS'), (
        '`_SHOW_KEYWORDS` 又回来了 —— 它是同一份集合的第二个名字')
    assert not hasattr(RenPyParser, '_SCENE_KEYWORDS'), (
        '`_SCENE_KEYWORDS` 又回来了 —— scene 与 show 同口径由分类器的 '
        '`intros=` 参数表达')
    assert not hasattr(BSP, '_SHOW_ARG_KEYWORDS'), (
        '`_SHOW_ARG_KEYWORDS` 又回来了 —— 它只是同一份集合的第二个名字')
    assert not hasattr(EE, '_SHOW_KEYWORDS'), (
        '`_SHOW_KEYWORDS` 又回来了 —— `on` 没有官方依据、真机 0 次，'
        '当年就是这张表漂的')

    # ② 剩下的两处"取词边界"仍是内核 ± 显式增减
    assert set(ARC._SHOW_STOP_WORDS) == canon | intro, sorted(ARC._SHOW_STOP_WORDS)
    assert set(LS._NOT_A_VALUE) == (canon | intro
                                    | {'True', 'False', 'None'}), (
        sorted(LS._NOT_A_VALUE))
    # 藏在函数里的那一处（`at` 之后的变换列表边界）写进了源码，按源码核
    src = (ROOT / 'storygraph' / 'story' / 'generation_guard.py').read_text(
        encoding='utf-8')
    assert 'SHOW_CLAUSE_KEYWORDS - {\'at\'}' in src, (
        'generation_guard 的 stopwords 又自己写了一份')


# ---------------------------------------------------------------------
#  一处实现：**所有**消费 show 词的站点都走 `classify_show_words`
# ---------------------------------------------------------------------

#: 这些文件的"切分动作"必须由真源提供。
#: 判据是**行为的**：拿同一批形状去问每一站，答案必须与分类器一致。
_SPLIT_SITES = [
    'storygraph/parsing/renpy_parser.py',
    'storygraph/story/beat_stage_parse.py',
    'storygraph/story/excel_export.py',
    'storygraph/assets/asset_registry.py',
]


def test_切分口径只有一份_四站都调分类器():
    """
    ⚠⚠ **这才是 C 项要防的病**：光把词表收成一份不够 —— 以前每个站点还各写
    一遍"拿集合去 split()"的循环（`_clause_image` / `_extract_image_spec` /
    `_parse_show_scene` 的 while / `_unknown_show_words` 的 for），
    于是"改一处忘一处"照样复发。
    判据（源码层，简单但有效）：这四处都必须出现 `classify_show_words` 或
    `tokenize_show_words` 的调用；并且**不许**再出现"`in ...KEYWORDS` 然后 break"
    的本地切分标志。
    """
    for rel in _SPLIT_SITES:
        text = (ROOT / rel).read_text(encoding='utf-8')
        assert 'classify_show_words' in text or 'tokenize_show_words' in text, (
            f'{rel} 没有走分类器真源')

    # 反向：本轮删掉的那些"本地切分标志"不许回来
    for rel, needle in (
            ('storygraph/story/beat_stage_parse.py', 'for w in clause.split():'),
            ('storygraph/assets/asset_registry.py', 'if low in _SHOW_STOP_WORDS:'),
            ('storygraph/story/excel_export.py', 'if word in _SHOW_KEYWORDS:'),
    ):
        text = (ROOT / rel).read_text(encoding='utf-8')
        assert needle not in text, f'{rel} 又出现本地切分：{needle}'


def test_四站对同一批形状给出同一个目标():
    """
    行为层对照：同一批真机形状，四站算出的"**图像名**"必须与真源一致。

    形状取自真机（`D:/千连万理/RenPyTest/sr_general`）：
      · 多段图像名 + 子句：`lan happy at left with dissolve`
      · 逗号分隔的 `at` 列表：`lan at i11, i12`
      · 括号里的逗号（**Python 元组**）：`image puzzle_path at Transform(xysize=(1920, 1080))`
      · 引入型（**没有图像名**）：`screen msg_dialogue` / `layer master` /
        `expression holo("x") at y`
    """
    from storygraph.assets.asset_registry import _extract_image_spec
    from storygraph.story.excel_export import _unknown_show_words

    image_cases = {
        'lan happy at left with dissolve': 'lan happy',
        'lan at i11, i12': 'lan',
        'image puzzle_path at Transform(xysize=(1920, 1080))': 'image puzzle_path',
    }
    for text, target in image_cases.items():
        assert V.classify_show_words(text).target == target, text
        assert _extract_image_spec(text) == target, ('asset_registry', text)
        r = RenPyParser._parse_show_scene('show ' + text, False)
        assert (r or {}).get('image', '') == target, ('renpy_parser', text, r)
        assert _unknown_show_words('show ' + text, {}) == [], ('excel_export', text)

    # 引入型：**没有图像名** —— 资产那条路必须给空串（否则真机 110 条假断链）
    for text in ('screen msg_dialogue', 'layer master'):
        roles = V.classify_show_words(text)
        assert roles.intro_kind != 'image', (text, roles)
        assert _extract_image_spec(text) == '', ('asset_registry', text)
        r = RenPyParser._parse_show_scene('show ' + text, False)
        assert (r or {}).get('image', '') == '', ('renpy_parser', text, r)
        assert _unknown_show_words('show ' + text, {}) == [], ('excel_export', text)

    # `expression <表达式>` 是**另一件事**：它的"图像名"就是那个 Python 表达式
    # （`is_expression=True`），只是**查不到资产**，所以不算"没有目标"。
    # 资产那一侧照样要挡住（`is_dynamic_image_spec` + `intro_kind != 'image'`）。
    text = 'expression holo("x") at y'
    roles = V.classify_show_words(text)
    assert roles.intro_kind == 'expression', roles
    assert roles.target == 'holo("x")' and roles.clause('at') == ['y'], roles
    assert _extract_image_spec(text) == '', ('asset_registry', text)
    r = RenPyParser._parse_show_scene('show ' + text, False)
    assert (r or {}).get('image') == 'holo("x")' and r.get('is_expression'), r
    assert r.get('at') == ['y'], r
    assert _unknown_show_words('show ' + text, {}) == [], ('excel_export', text)


def test_表达式里的逗号一个都不许丢():
    """
    ⚠⚠ **这条是"产物能不能跑"级别**（真机 `screens.rpy:1439` 实测）：

        show expression Text("SEIREN PROJECT", color="#89c7fe", size=60, bold=True) as title:

    表达式里的逗号是 **Python 参数分隔符**。分词真源会把深度>0 的逗号留在
    同一个 token 里，但"去掉词末尾的逗号"这一步如果贪心（`rstrip(',')` 之类），
    就会把 `bold=True)` 之前的逗号也啃掉 → `Text("SEIREN PROJECT" color=…)`
    —— **一个 Python 语法错误**，产物直接跑不起来。

    所以判据是**逐字**：目标必须与原文里那段表达式**一个字不差**。
    """
    expr = 'Text("SEIREN PROJECT", color="#89c7fe", size=60, bold=True)'
    text = f'expression {expr} as title:'
    roles = V.classify_show_words(text)
    assert roles.intro_kind == 'expression', roles
    assert roles.target == expr, ('表达式被啃了', roles.target)
    assert roles.clause('as') == 'title:', roles

    # 带参调用 + `at` 子句：逗号在参数里，子句照样要切出来
    text2 = 'expression holo(path, "lan base", (1920, 1080)) at i11'
    roles2 = V.classify_show_words(text2)
    assert roles2.target == 'holo(path, "lan base", (1920, 1080))', roles2
    assert roles2.clause('at') == ['i11'], roles2

    # 端到端：原样进 → 原样出（`renpy_parser` 这一站的 dict）
    r = RenPyParser._parse_show_scene('show ' + text, False)
    assert r['image'] == expr and r['is_expression'] and r['as_'] == 'title:', r


def test_舞台片段那一站刻意不看引入型():
    """
    ⚠ `beat_stage_parse._clause_image` 切的是"**舞台片段**"（`beat.show` 那一格），
    不是"资产引用"—— 所以它**刻意**把 `screen msg` 整段当一个名字
    （`intros=()`）。这是"与从前逐字相同"的口径，不许顺手改；
    资产那一侧要"空串"由 `asset_registry` 自己看 `intro_kind` 决定。
    """
    from storygraph.story.beat_stage_parse import _clause_image

    assert _clause_image('screen msg_dialogue') == 'screen msg_dialogue'
    assert _clause_image('layer master') == 'layer master'
    assert _clause_image('cg cg02 at t behind ye') == 'cg cg02'
    assert _clause_image('expression holo("x") at y') == 'expression holo("x")'


# =====================================================================
#  ② 三个 verb 的子句（scene ≡ show）
# =====================================================================

def test_show_的子句照旧():
    r = RenPyParser._parse_show_scene('show lan happy at left with dissolve',
                                      is_scene=False)
    assert r['image'] == 'lan happy', r
    assert r['at'] == ['left'] and r['with_'] == 'dissolve', r


def test_scene_取与_show_同一批子句():
    """官方原文：`the scene statement takes the same properties as the show statement`。

    ⚠ 2026-10-16：断言从"两张表相等"改成**行为**（`_SCENE_KEYWORDS` 已删）——
    scene 与 show 同口径这件事现在由分类器的 `intros=` 参数表达，
    真正要守的是"`scene bg behind x` 不许把 `behind x` 吃进图像名"。
    """
    assert V.classify_show_words('bg behind x at left').target == 'bg'
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


def test_scene_的_as_behind_zorder_走逐字通道不丢字(write_rpy):
    """
    ⚠⚠ 铁律 #3（**认不出就原样抄**）：`scene` 的 `as` / `behind` / `zorder`
    三个子句**整行原样吐回**，一个字节不动。

    为什么（2026-10-15 用户审核指出）：`_SCENE_KEYWORDS` 与 show 同口径之后，
    这三个字段会被 `_parse_show_scene` **算出来**，而 `SceneNode`（以及
    `Statement.payload` / `Beat`）**没有它们的载体** → 算出来再丢掉 ——
    那就把原来的"切错但错得可见"变成了"**丢得无声**"。

    所以 `_stmt_scene` 改成：有这三个字段就 `_NO_MATCH` → 整行落 `raw`。
    判据是**往返逐字节相同**（渲染一轮、再渲染一轮都不许变）。
    """
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy

    lines = ['scene bg as x', 'scene bg behind x at left', 'scene bg zorder 3']
    body = ('label a:\n' + ''.join(f'    {ln}\n' for ln in lines)
            + '    return\n')
    fp = write_rpy(body, 'ch.rpy')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    out = render_beats_to_rpy(beats_from_parse_result(pr))
    for ln in lines:
        assert ln in out, ('这一行没有被逐字带出来', ln, out)
    # 再渲染一轮也不许变（真机审计同一把尺：导出一次与再导出一次逐字节相同）
    fp2 = write_rpy(out, 'ch2.rpy')
    pr2, _ = IncrementalRenPyAnalyzer().analyze(files=[fp2], graph=None)
    out2 = render_beats_to_rpy(beats_from_parse_result(pr2))
    assert out2 == out, ('第二轮变了', out, out2)


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
