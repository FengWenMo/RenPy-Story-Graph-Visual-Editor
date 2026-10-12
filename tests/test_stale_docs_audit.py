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
    """⭐ 核心判据：文档说"待做"、代码里**已有够具体的同名** → 可疑，要去核。"""
    docs, root = _make(tmp_path)
    got = A.audit(docs, root)
    rows = {r['text'][:12]: r for r in got['rows']}
    flagged = [r for r in got['rows'] if r['specific']]
    assert len(flagged) == 1, [r['text'] for r in flagged]
    assert 'already_done' in flagged[0]['specific'], flagged[0]
    assert 'storygraph' in flagged[0]['specific']['already_done'][0]


def test_a_generic_name_alone_is_not_suspicious(tmp_path):
    """
    ⭐⭐ **降权判据**（2026-10-12 加）：
    只命中 `registry`/`engine` 这种**到处都有的词**时**不算可疑** ——
    那是误报，而噪声比不报更糟（人会开始无视整份报告）。
    实测：本仓 11 行可疑里就有 3 行只是这种泛名命中。
    """
    code = tmp_path / 'storygraph'
    code.mkdir(parents=True)
    # ⚠ 索引只收**定义**（def/class/模块名），所以这里用 def ——
    #   真实命中也是这种（`registry` / `engine` 在本仓是某些文件里的函数名）。
    (code / 'thing.py').write_text(
        'def registry():\n    return {}\n\n\ndef engine():\n    return None\n',
        encoding='utf-8')
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / 'plan.md').write_text(
        '- `registry` 与 `engine` 还没接 —— 待做\n', encoding='utf-8')
    got = A.audit(docs, tmp_path)
    row = got['rows'][0]
    assert row['found'], '泛名也是命中，found 不该空'
    assert not row['specific'], '泛名不该进"可疑"那一栏'
    assert got['counts']['suspicious'] == 0, got['counts']
    assert got['counts']['generic_only'] == 1, got['counts']


def test_is_pending_line_tells_real_pending_from_mentions():
    """
    ⭐⭐ **误报判据**（2026-10-12 加，例子全是本仓实测的）：
    8 行"可疑"里 6 行只是**提到了** `TODO`/`未完成` 这些字 ——
    本仓文档经常在描述里写 `# TODO`（工具本来就会往生成代码里写它），
    还会把"未完成"当名词用。收紧后本仓：42 行标记 → 27 行，可疑 11 → **2**。
    """
    # 真的在声明"还没做"
    assert A.is_pending_line('- `foo_bar` 还没接 —— 待做')
    assert A.is_pending_line('| 3.3 | 只改台词 | ⏳ 待做 |')
    assert A.is_pending_line('- [ ] TODO: 把 X 接上')
    assert A.is_pending_line('| P2 | 事件总线半接通 | 未做 |')

    # ⚠ 只是**提到**了这些字（本仓实测的 6 个误报形状）
    assert not A.is_pending_line('那一句按旁白写 + `# TODO: 缺少资源 角色 lj`')
    assert not A.is_pending_line('（`RawNode` / `FileBlocks` / `# TODO` 攒出来的）')
    assert not A.is_pending_line('而不是"完成/未完成"的布尔')
    assert not A.is_pending_line('**已修（2026-10-05）**。复验发现订阅端是齐的')
    assert not A.is_pending_line('结构位丢 -> 显式 `# TODO`；`W113` 排除')


def test_a_resolved_row_is_never_reported():
    """"已修/已完成"的行就算提到了 TODO，也不该出现在待做清单里。"""
    docs = None
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / 'storygraph').mkdir()
        (root / 'storygraph' / 'done.py').write_text(
            'def widget_glue():\n    return 1\n', encoding='utf-8')
        (root / 'docs').mkdir()
        (root / 'docs' / 'log.md').write_text(
            '| P2-9 | 事件总线半接通 | **已修**（提到 `widget_glue` 与 `# TODO`）|\n',
            encoding='utf-8')
        got = A.audit(root / 'docs', root)
        assert got['counts']['rows'] == 0, got['rows']


def test_is_specific_tells_names_apart():
    """"够不够具体"的判据本身：长名/带下划线/驼峰 → 具体；泛名/短名 → 不算。"""
    for yes in ('stage_digest', 'filter_choices', 'AssetIndex', 'build_plan',
                'problems_of'):
        assert A.is_specific(yes), yes
    for no in ('registry', 'engine', 'path', 'text', 'label', 'rows'):
        assert not A.is_specific(no), no


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
