"""
"教一次"入口：人答的结构**当场落成声明**（设计文档 §5 第 3 步）
========================================================================

用户 2026-10-07 问的是"工具如何得知自定义结构、怎么和程序员**直接交互调节**"。
设计文档的答案是：得知必须**落成一份可评审的文件**（`rules.json` 的
`statements`）—— 只存在内存里的"工具知道"不算知道。

这个文件钉住那个写入口（`declare_statement` + `cli learn`）的四条契约：

  ① **教了就算数**：写完 `describe` 立刻把它算作 `declared`（同一份真源）；
  ② **教两次不会变两份**：同名 = 更新；
  ③ **不碰别人的东西**：已有的其它键与别的段原样保留；
  ④ ⚠ **坏清单拒绝覆盖**（返回 `ok=False`）—— 拿我们这份盖掉用户手写的
     清单是不可逆的破坏，宁可什么都不做。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story.statement_registry import (      # noqa: E402
    clear_cache, declare_statement, describe_statements,
)

SCRIPT = (
    'label a:\n'
    '    mycue "闪电" intensity=3:\n'
    '        target = ling\n'
    '    return\n'
)


def _project(tmp_path) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'ch.rpy').write_text(SCRIPT, encoding='utf-8')
    return root


def _manifest(root: Path) -> dict:
    p = root / 'storygraph.rules.json'
    return json.loads(p.read_text(encoding='utf-8'))


def test_learn_lands_in_the_manifest_and_describe_sees_it(tmp_path):
    """① 教了就算数：`describe` 立刻报 `declared`（同一份真源）。"""
    root = _project(tmp_path)
    res = declare_statement(root, 'mycue', label='闪电提示', block=True,
                            columns=[{'name': 'intensity', 'label': '强度',
                                      'choices': ['1', '2', '3']}])
    assert res['ok'] and res['action'] == 'added', res
    data = _manifest(root)
    assert data['statements'][0]['name'] == 'mycue'
    assert data['statements'][0]['label'] == '闪电提示'
    assert data['statements'][0]['columns'][0]['choices'] == ['1', '2', '3']

    clear_cache()
    desc = describe_statements(project_root=root)
    row = next(r for r in desc['statements'] if r['name'] == 'mycue')
    assert row['declared'] is True, row
    assert row['source'] == 'declared', row
    assert [c['name'] for c in row['columns']] == ['intensity'], row


def test_learning_twice_updates_instead_of_appending(tmp_path):
    """② 同名 = 更新（教两次不会变成两份声明）。"""
    root = _project(tmp_path)
    declare_statement(root, 'mycue', label='旧标签')
    res = declare_statement(root, 'mycue', label='新标签')
    assert res['action'] == 'updated', res
    items = _manifest(root)['statements']
    assert len(items) == 1, items
    assert items[0]['label'] == '新标签', items


def test_other_keys_are_preserved(tmp_path):
    """③ 不碰别人的东西：别的段与同一条里的其它键都留着。"""
    root = _project(tmp_path)
    (root / 'storygraph.rules.json').write_text(json.dumps(
        {'statements': [{'name': 'other', 'aliases': ['o'], 'docs': '别的'}],
         'asset_dirs': ['assets'], 'custom': {'keep': True}},
        ensure_ascii=False), encoding='utf-8')
    declare_statement(root, 'mycue', label='新')
    data = _manifest(root)
    assert data['asset_dirs'] == ['assets'], data
    assert data['custom'] == {'keep': True}, data
    names = [it['name'] for it in data['statements']]
    assert names == ['other', 'mycue'], names
    assert data['statements'][0]['aliases'] == ['o'], data


def test_broken_manifest_is_refused_not_overwritten(tmp_path):
    """④ 坏清单拒绝覆盖 —— 这是唯一不可逆的破坏方式，必须挡住。"""
    root = _project(tmp_path)
    bad = root / 'storygraph.rules.json'
    bad.write_text('{ 这不是 JSON', encoding='utf-8')
    res = declare_statement(root, 'mycue', label='x')
    assert res['ok'] is False and res['action'] == 'refused', res
    assert bad.read_text(encoding='utf-8') == '{ 这不是 JSON'   # 一个字节没动


def test_empty_name_is_refused(tmp_path):
    root = _project(tmp_path)
    res = declare_statement(root, '   ')
    assert res['ok'] is False, res
    assert not (root / 'storygraph.rules.json').exists()


def test_cli_learn_writes_and_reports(tmp_path):
    """CLI 那条路：`learn` 能跑通，参数形状从命令行拼出来。"""
    from storygraph.cli import main

    root = _project(tmp_path)
    rc = main(['learn', 'mycue', str(root), '--label', '闪电提示',
               '--block', '--param', 'intensity:强度:1,2,3'])
    assert rc == 0
    col = _manifest(root)['statements'][0]['columns'][0]
    assert col == {'name': 'intensity', 'label': '强度',
                   'choices': ['1', '2', '3']}, col
