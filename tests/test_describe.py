"""
`describe`：把"工具以为的自定义结构"摊开（设计文档 §2.1 第一档接口）
==================================================================

用户 2026-10-07 的问题："工具如何得知自定义结构、怎么和程序员直接交互调节？"
—— 前提是**工具能把自己以为的东西摊开**，否则人只能猜
"它到底认不认我的 `mycue`、认成什么档、凭什么"。

这个文件钉住四件事（真机跑出来的三条坑都在里面）：

  ① **范围**：只有**自定义结构**那几类进报告（`raw`/`loop`/`camera`）——
     台词/注释/代码行**不是**语句头（第一版真机跑出 1634 个"头"，
     里面全是 `兄弟，再别说了。` 这种台词）；
  ② **来源四档**：`declared`（rules/CDS）/ `builtin`（官方语句 + 工具自己
     建模的 `loop`/`camera` + 内置形状表 `with`/`pause`/`window`）/ `shape-guess`；
     ⚠ 只对 `shape-guess` 提示"去声明" —— 对 `show` 提示"请写进 rules.json"
     是噪声；
  ③ **声明过的**要带参数表与标签（它就是表里"每参数一列"的来源）；
  ④ **只读**：不写任何文件（CI 里要能安全地 diff 它的输出）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story.statement_registry import (      # noqa: E402
    clear_cache, describe_statements,
)

RULES = {
    'statements': [{
        'name': 'mycue', 'label': '闪电提示', 'block': True,
        'columns': [
            {'name': 'intensity', 'label': '强度', 'choices': ['1', '2', '3']},
            {'name': 'target', 'label': '目标'},
        ],
    }],
}

SCRIPT = (
    'label a:\n'
    '    "一句台词"\n'
    '    # 一条注释\n'
    '    mycue "闪电" intensity=3 target=ling:\n'
    '        target = ling\n'
    '    with dissolve\n'
    '    l oh "3"\n'
    '    while x < 3:\n'
    '        "循环里"\n'
    '    return\n'
)


def _project(tmp_path) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'ch.rpy').write_text(SCRIPT, encoding='utf-8')
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False), encoding='utf-8')
    return root


def _describe(tmp_path) -> dict:
    clear_cache()
    return describe_statements(project_root=_project(tmp_path))


def _row(data: dict, name: str) -> dict:
    for r in data['statements']:
        if r['name'] == name:
            return r
    raise AssertionError(f'报告里没有 {name!r}：'
                         f'{[r["name"] for r in data["statements"]]}')


def test_scope_excludes_dialogue_and_comments(tmp_path):
    """① 台词 / 注释 / 代码行**不是**语句头（真机第一版就错在这里）。"""
    data = _describe(tmp_path)
    names = [r['name'] for r in data['statements']]
    assert '一句台词' not in names, names
    assert not [n for n in names if n.startswith('#')], names
    assert '循环里' not in names, names
    # 这一份脚本里"自定义结构"就是这四条
    assert set(names) == {'mycue', 'with', 'l', 'loop'}, names


def test_declared_statement_carries_columns_and_label(tmp_path):
    """③ 声明过的：来源 + 标签 + 参数表（表里"每参数一列"的来源）。"""
    row = _row(_describe(tmp_path), 'mycue')
    assert row['source'] == 'declared'
    assert row['declared'] is True
    assert row['label'] == '闪电提示'
    assert [c['name'] for c in row['columns']] == ['intensity', 'target']
    assert row['warnings'] == []
    assert row['occurrences'] == 1
    assert row['file'].endswith('ch.rpy:4'), row['file']


def test_builtin_sources_do_not_ask_the_user_to_declare(tmp_path):
    """② 官方语句 / 内置形状 / 工具建模的循环：**不提示去声明**（那是噪声）。"""
    data = _describe(tmp_path)
    for name in ('with', 'loop'):
        row = _row(data, name)
        assert row['source'] == 'builtin', row
        assert row['warnings'] == [], row


def test_unknown_head_is_flagged_as_guess(tmp_path):
    """② 真正"只靠形状猜"的（真机里就是那一条手写错句 `l oh "3"`）要提示。"""
    row = _row(_describe(tmp_path), 'l')
    assert row['source'] == 'shape-guess'
    assert row['fidelity'] == 'T3', row
    assert row['warnings'], row
    assert data_has_guess(_describe(tmp_path)) is True


def data_has_guess(data: dict) -> bool:
    return data['summary']['guessed_heads'] >= 1


def test_summary_counts_match_rows(tmp_path):
    data = _describe(tmp_path)
    s = data['summary']
    assert s['heads'] == len(data['statements'])
    assert s['declared_heads'] == sum(
        1 for r in data['statements'] if r['declared'])
    assert s['guessed_heads'] == sum(
        1 for r in data['statements'] if r['source'] == 'shape-guess')
    assert s['needs_action'] == s['guessed_heads']


def test_cli_describe_json_is_machine_readable(tmp_path):
    """④ 只读 + 可 diff：`--json` 能直接进版本库比。"""
    from storygraph.cli import main

    root = _project(tmp_path)
    clear_cache()
    rc = main(['describe', str(root), '--json'])
    assert rc == 0
    # 只读：跑完项目里不该多出文件（除了我们建的那两个）
    files = sorted(p.name for p in root.rglob('*') if p.is_file())
    assert files == ['ch.rpy', 'storygraph.rules.json'], files
