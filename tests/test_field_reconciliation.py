"""
字段对账不变量：`_parse_*` 算出来的字段，**链上每一环都要显式交代**
================================================================================

为什么要有它（2026-10-15 用户审核提的第三段）：
`SceneNode` 缺 `as_`/`zorder`/`behind` 那条洞，我是靠
`assert not hasattr(node, 'as_')` **逐个钉**的 —— 那种钉法**抓不住"下一个字段"**
（`zorder`/`behind` 就是证明：我只钉了 `as_`，漏了两个）。

所以这里换成**表驱动的不变量**：

    `_parse_show_scene` 返回的每个字段
        → 要么在 **Node** 上（能被携带）
        → 要么在 **Statement.payload** 上（能被下游拿到）
        → 要么**明写"刻意不传 + 理由"**（`_DELIBERATE`）
     缺一项即红 —— 一次覆盖全字段，将来任何"算出来没传"的新字段都会当场变红。

与既有物件的分工：
  · `core/statement._FACTORY_FIELDS` + `tests/test_kind_declaration_sync.py`
    守的是 **工厂 → payload**（"声明里写了什么"）；
  · 这一份守的是**更上游那一环**：`_parse_*` 的 dict → Node。
    这一环此前**没人守** —— `as_`/`zorder`/`behind` 正是从这里掉的。
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core.statement import _FACTORY_FIELDS, StatementKind  # noqa: E402
from storygraph.parsing.renpy_nodes import (HideNode,  # noqa: E402
                                           SceneNode, ShowNode)
from storygraph.parsing.renpy_parser import RenPyParser  # noqa: E402

#: 一行"每种子句都出现"的样例 —— 用它取 `_parse_show_scene` 的**字段全集**。
FULL = 'show bg at t1, t2 as alias with fade onlayer master zorder 3 behind q'

#: `ASTNode` 的基础字段（位置信息，不属于"子句字段"，不参与对账）。
_BASE = {'line_number', 'indent', 'filename'}

#: **刻意不传**的字段 + 理由（必须逐条写清；`None` 值表示"这一环现在没有它"）。
_DELIBERATE = {
    'scene': {
        'as_': 'SceneNode/Statement.payload/Beat 都没有载体 → `_stmt_scene` '
               '改成有它就 `_NO_MATCH`、整行走 `raw` 逐字保留（铁律 #3）',
        'zorder': '同上（与 as_ 同一批处理的三个字段之一）',
        'behind': '同上',
    },
    'hide': {
        # `_stmt_hide` 不是走 `_parse_show_scene`，而是自己的正则；它的
        # `onlayer` 同样没有载体 → 同一口径：尾部出现子句词就走 raw。
        'onlayer': 'HideNode 无载体 → `_stmt_hide` 改成尾部出现子句词即 '
                   '`_NO_MATCH`、整行走 `raw` 逐字保留',
        'at': 'hide 的官方子句里没有 at —— 出现即视为"认不出的形状"→ raw',
        'as_': '同上（hide 官方没有 as）',
        'behind': '同上（hide 官方没有 behind）',
        'zorder': '同上（hide 官方没有 zorder）',
    },
}


#: 每类的**形状字段**（官方语法上这一行可能出现的子句字段）。
#: `show` / `scene` 走 `_parse_show_scene`，字段集由**实测**取（下方断言它与
#: 样例一致）；`hide` 不走那个助手（它有自己的正则），所以按官方语法写明
#: ——"hide 的子句只有 onlayer（+with）"。
_CASES = (
    ('show', ShowNode, StatementKind.SHOW,
     {'image', 'at', 'as_', 'with_', 'zorder', 'onlayer', 'behind',
      'is_expression'}),
    ('scene', SceneNode, StatementKind.SCENE,
     {'image', 'at', 'as_', 'with_', 'zorder', 'onlayer', 'behind',
      'is_expression'}),
    ('hide', HideNode, StatementKind.HIDE,
     {'image', 'with_', 'onlayer'}),
)


def test_解析字段_在_Node_或_payload_或明写不传():
    """凡这一行**可能出现**的子句字段，每一环都得有交代。"""
    # 先保证"样例覆盖了全部子句"—— 否则下面那条不变量会抓不全
    parsed = RenPyParser._parse_show_scene(FULL, is_scene=False)
    assert set(parsed) - {'warnings'} == _CASES[0][3], (
        '样例 `FULL` 与形状字段集不一致 —— 先修样例/表，否则抓不全',
        sorted(set(parsed) - {'warnings'}))
    for tag, node_cls, kind, shape in _CASES:
        node_fields = {f.name for f in dataclasses.fields(node_cls)} - _BASE
        payload = set(_FACTORY_FIELDS[kind])
        dropped = _DELIBERATE.get(tag, {})
        unaccounted = shape - node_fields - payload - set(dropped)
        assert not unaccounted, (
            f'{tag}：这些字段**算出来了但没人交代**（漏一环就是静默丢字）—— '
            f'要么传到 Node/payload 上，要么在 `_DELIBERATE` 里写明理由：'
            f'{sorted(unaccounted)}')
        # 反过来：Node 上有的字段也必须进 payload，否则下游拿不到
        assert node_fields - payload - set(dropped) == set(), (
            f'{tag}：Node 上有、payload 里没有（下游拿不到）—— '
            f'{sorted(node_fields - payload - set(dropped))}')


def test_刻意不传的那几条_真的走逐字通道():
    """
    `_DELIBERATE` 里写了"走 raw 逐字保留"的，必须**真的**走 raw ——
    否则那张表就成了"嘴上交代、实际照丢"。
    """
    import tempfile

    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer

    cases = {
        'scene': ['scene bg as x', 'scene bg zorder 3', 'scene bg behind x'],
        'hide': ['hide bg onlayer master', 'hide bg zorder 3'],
    }
    assert set(cases) <= set(_DELIBERATE), '表里少了一类'
    analyzer = IncrementalRenPyAnalyzer()
    for tag, lines in cases.items():
        for ln in lines:
            with tempfile.TemporaryDirectory(prefix='fld_') as td:
                f = Path(td) / 'a.rpy'
                f.write_text(f'label a:\n    {ln}\n    return\n',
                             encoding='utf-8')
                pr, _ = analyzer.analyze(files=[f], graph=None)
            kinds = [str(getattr(st, 'kind', ''))
                     for st in (getattr(pr, 'statements', None) or ())
                     if str(getattr(st, 'kind', '')) in ('show', 'scene', 'hide')]
            assert kinds == [], (
                f'{tag}：{ln!r} 仍然被结构化解析成 {kinds} —— '
                f'`_DELIBERATE` 说的"走 raw 逐字保留"没落实')
