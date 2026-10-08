"""
拍表**单一定义**的对账（设计文档 §3 index 层第一件）
========================================================================

`storygraph/story/schema.py` 把散在各处的列契约（`BEAT_FIELDS` /
`BEAT_HEADERS` / `HIDDEN_BEAT_COLUMNS` / `_HEADER_ALIASES` /
`PRIMARY_FIELDS` / `ADVANCED_FIELDS`）汇总成**一份可 diff 的定义**
（`docs/schema.json`），并给出 `--check` 对账。

这个文件钉四件事：

  ① **已发布定义与代码一致**（改了列没更新定义 → 这条红）；
  ② 定义**覆盖全部列且表头正确**（列不重不漏、隐藏列都在列里）；
  ③ 别名能**双向**对上（`header → name → header`），且覆盖所有表头；
  ④ 对账本身**不是空转**：把定义改一个字，`check()` 必须报漂移。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.schema import (                            # noqa: E402
    DEFAULT_PATH, beat_schema, check, dumps,
)


def test_published_schema_matches_code():
    """① 已发布定义 == 代码（漂移即红：这就是"改一处忘一处"的守门人）。"""
    ok, msg = check(DEFAULT_PATH)
    assert ok, msg


def test_schema_covers_every_column_exactly_once():
    """② 列不重不漏、表头与 `BEAT_HEADERS` 一致、隐藏列都在列里。"""
    from storygraph.story.beat_model import (
        BEAT_FIELDS, BEAT_HEADERS, HIDDEN_BEAT_COLUMNS)

    s = beat_schema()
    names = [c['name'] for c in s['columns']]
    assert names == list(BEAT_FIELDS), '定义与 BEAT_FIELDS 不同（顺序也算契约）'
    assert len(names) == len(set(names)), names
    for c in s['columns']:
        assert c['header'] == BEAT_HEADERS.get(c['name'], c['name']), c
    hidden = {c['name'] for c in s['columns'] if c['hidden']}
    assert hidden == set(HIDDEN_BEAT_COLUMNS), hidden


def test_aliases_are_bidirectional_and_complete():
    """③ 别名双向对上，且覆盖所有表头（表头改名不会漏映射）。"""
    s = beat_schema()
    names = {c['name'] for c in s['columns']}
    for name, header in [(c['name'], c['header']) for c in s['columns']]:
        assert s['aliases'].get(header) == name, (header, name)
    # ⚠ 别断言"别名条数 == 表头数"：`BEAT_HEADERS` 里**没改中文的列是自带
    # 恒等映射**（`'At': 'At'`），所以别名表里同一个目标会有多条（英文名 + 中文名）
    # —— 第一版就是这么误报的。真正要钉的是**别名不许指向不存在的列**（拼错表头名）。
    for alias, target in s['aliases'].items():
        assert target in names, f'别名 {alias!r} 指向了不存在的列 {target!r}'


def test_groups_are_subset_of_columns():
    """卡片主区/高级区的字段必须是真列（写错过 `Notes` 这种不存在的列）。"""
    s = beat_schema()
    names = {c['name'] for c in s['columns']}
    for key in ('primary', 'advanced'):
        for f in s['groups'][key]:
            assert f in names, f'{key} 里的 {f} 不是真列'


def test_drift_detection_is_not_vacuous(tmp_path):
    """④ 变异：定义改一个字，`check()` 必须报漂移（否则 ① 是空转）。"""
    bad = tmp_path / 'schema.json'
    bad.write_text(dumps().replace('"行号"', '"行号X"'), encoding='utf-8')
    ok, msg = check(bad)
    assert ok is False, msg
    assert '漂移' in msg, msg
    # 顺带：少的文件也要说清怎么生成
    ok2, msg2 = check(tmp_path / 'nope.json')
    assert ok2 is False and '--write' in msg2, msg2


def test_codegen_reproduces_the_runtime_definitions():
    """
    **⑤ 的反转**（设计文档 §5 第 5 步）：由定义**生成** `beat_model` 那三个常量，
    生成物 `exec` 之后必须与运行时**逐项一致**。

    ⚠ 本轮只做到"可对账"：生成物不参与运行（切过去要动 `beat_model` 的
    导入期行为，与"资源表换位"是同一类坑）——但"由 schema 生成"从此不是空话。
    """
    from storygraph.schema import codegen_beat_model

    src = codegen_beat_model()
    assert 'storygraph/schema.py 生成' in src
    ns: dict = {}
    exec(compile(src, '<generated>', 'exec'), ns)      # noqa: S102 - 自己生成自己跑

    from storygraph.story.beat_model import (
        BEAT_FIELDS, BEAT_HEADERS, HIDDEN_BEAT_COLUMNS)

    assert ns['BEAT_FIELDS'] == list(BEAT_FIELDS)
    assert ns['HIDDEN_BEAT_COLUMNS'] == tuple(HIDDEN_BEAT_COLUMNS)
    #: 运行时的 `BEAT_HEADERS` 含历史别名（不只 name→header），
    #: 所以对账的是**表头那一半**：生成的 `HEADER_OF` 必须与它一致。
    header_of = ns['HEADER_OF']
    assert header_of == {name: BEAT_HEADERS.get(name, name)
                         for name in BEAT_FIELDS}, header_of
    # 生成两次必须一字不差（否则 diff 全是噪声）
    assert codegen_beat_model() == src


def test_schema_is_stable_json(tmp_path):
    """定义本身是**稳定序列化**（排序 + 固定缩进）—— 否则 diff 全是噪声。"""
    text = dumps()
    assert json.loads(text) == beat_schema()
    assert text == dumps(), '两次生成必须一字不差'
