"""
文档过期审计判据（2026-10-11）
================================

一天之内**三次**照文档重做已经有的东西（S3/S4、候选搜索…），代价是白写代码。
工具在 `tools/audit_stale_docs.py`；这份判据保证它**不腐烂**
（工具类的东西最容易"写一次就没再用过"，所以拿合成样本钉住它的行为）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import audit_stale_docs as A                                            # noqa: E402


def _make(tmp_path):
    """合成一个"代码 + 文档"的小世界：`already_done` 在代码里，`not_yet` 不在。"""
    code = tmp_path / 'storygraph'
    code.mkdir(parents=True)
    (code / 'done.py').write_text(
        'def already_done():\n    return 1\n\n\nclass SomeClass:\n    pass\n',
        encoding='utf-8')
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / 'plan.md').write_text(
        '# 计划\n\n'
        '- `already_done` 还没接进界面 —— 待做\n'
        '- `never_written_thing` 也待做\n'
        '- 这件事没有反引号名字，未做\n',
        encoding='utf-8')
    return docs, tmp_path


def test_it_flags_documented_pending_that_exists_in_code(tmp_path):
    """⭐ 核心判据：文档说"待做"、代码里**已有同名** → 可疑，要去核。"""
    docs, root = _make(tmp_path)
    got = A.audit(docs, root)
    rows = {r['text'][:12]: r for r in got['rows']}
    flagged = [r for r in got['rows'] if r['found']]
    assert len(flagged) == 1, [r['text'] for r in flagged]
    assert 'already_done' in flagged[0]['found'], flagged[0]
    assert 'storygraph' in flagged[0]['found']['already_done'][0]


def test_it_does_not_flag_things_that_really_are_missing(tmp_path):
    """真的还没做的，不许报成可疑（否则人会开始无视整份报告）。"""
    docs, root = _make(tmp_path)
    got = A.audit(docs, root)
    for row in got['rows']:
        assert 'never_written_thing' not in row['found'], row


def test_lines_without_a_name_are_counted_as_unjudgeable(tmp_path):
    """抽不出标识符的行要**计入"无从判定"**，并建议把符号写进反引号。"""
    docs, root = _make(tmp_path)
    counts = A.audit(docs, root)['counts']
    assert counts['rows'] == 3, counts
    assert counts['suspicious'] == 1, counts
    assert counts['unjudgeable'] == 1, counts


def test_chinese_and_commands_are_not_treated_as_names():
    """`python tools/xxx.py` 这类命令行、以及中文，都不该被当成"代码里的名字"。"""
    assert A.names_in_line('跑 `python tools/probe.py` 看看') == []
    assert A.names_in_line('这一条 `待做的东西` 是中文') == []
    got = A.names_in_line('调 `filter_choices` 与 `stage_snapshot.stage_digest`')
    assert 'filter_choices' in got
    assert 'stage_digest' in got and 'stage_snapshot' in got


def test_the_index_only_collects_definitions(tmp_path):
    """
    ⚠ 索引只收**定义**：收引用的话，"文档提过的名字"会在任意 import 处命中，
    这条审计就彻底没用了（满屏可疑）。
    """
    root = tmp_path / 'storygraph'
    root.mkdir(parents=True)
    (root / 'user.py').write_text(
        'from storygraph.provider import borrowed_name\n\n\n'
        'def own_name():\n    return borrowed_name\n', encoding='utf-8')
    index = A.code_index(tmp_path)
    assert 'own_name' in index
    assert 'borrowed_name' not in index, '把"引用"也当成定义了'
