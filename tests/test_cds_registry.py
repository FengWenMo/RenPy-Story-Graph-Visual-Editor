"""
M2：把宿主注册表（Ren'Py CDS）读成 `StatementSpec`
==================================================

Ren'Py 的 Creator-Defined Statements 要求写在 `python early:` 块里：

    python early:
        renpy.register_statement(name="mycue", parse=parse_mycue, block=True)

**项目已经把自定义语句的形状公布在仓库里了**，而工具从前把 `early` 整块当
"不编辑的容器"跳过（设计文档 §1.4：「说明书在手边，工具选择不看」）。

这一层是**纯读**：只做字面量提取，不 eval 项目代码、不改任何行为。
所以判据的重点有两类：

  1. **读得出来**：名字 / 别名 / 带不带块 / 回调，多行与嵌套括号都不能切错；
  2. **认不出时不许猜**：`name=SOME_VAR` 要留下 `note`，而不是编一个名字
     （"猜错"比"说不知道"坏得多 —— 猜出来的 spec 会进 M3 的解析分派）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story.cds_registry import (StatementSpec, format_cds_report,
                                           parse_cds_calls, scan_project_cds,
                                           spec_from_call)

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


# ---------------------------------------------------------------------------
#  ① 读得出来
# ---------------------------------------------------------------------------

def test_reads_keyword_name_and_block():
    src = (
        'python early:\n'
        '    renpy.register_statement(\n'
        '        name="mycue",\n'
        '        parse=parse_mycue,\n'
        '        block=True,\n'
        '    )\n'
    )
    specs = parse_cds_calls(src, filename='a.rpy')
    assert len(specs) == 1
    s = specs[0]
    assert s.name == 'mycue'
    assert s.block == 'yes' and s.has_block
    assert 'parse' in s.hooks
    assert s.source == 'host-registry'
    assert s.source_line == 2, '行号要指到注册那一行，报告才点得准'


def test_reads_positional_name_and_aliases():
    """`name="a b"` 是**名字列表**（Ren'Py 允许一条语句多个开头词）。"""
    src = ('python early:\n'
           '    renpy.register_statement("line a b", block=False)\n')
    s = parse_cds_calls(src)[0]
    assert s.name == 'line'
    assert s.aliases == ['line', 'a', 'b']
    assert s.block == 'no' and not s.has_block


def test_block_possible():
    src = ('python early:\n'
           '    renpy.register_statement("x", block="possible")\n')
    s = parse_cds_calls(src)[0]
    assert s.block == 'possible' and s.has_block


def test_multiline_call_with_nested_parens_and_strings():
    """
    多行 + 嵌套括号 + 字符串里的逗号和括号 —— 一刀切错就会**丢掉 name=**。

    夹具刻意"不干净"：`parse=` 里放一个带逗号的 lambda，字符串里塞括号。
    """
    src = (
        'python early:\n'
        '    renpy.register_statement(\n'
        '        name="cue",\n'
        '        parse=lambda lex: build(lex, "a, b", (1, 2)),\n'
        '        lint=lint_cue,   # 这里还有注释，] ) , 都是干扰\n'
        '        block=False,\n'
        '    )\n'
    )
    specs = parse_cds_calls(src)
    assert len(specs) == 1, specs
    s = specs[0]
    assert s.name == 'cue'
    assert s.block == 'no'
    assert set(s.hooks) == {'parse', 'lint'}


def test_brace_inside_string_does_not_break_balancing():
    src = ('python early:\n'
           '    renpy.register_statement(name="weird(", block=True)\n')
    s = parse_cds_calls(src)[0]
    assert s.name == 'weird('
    assert s.block == 'yes'


# ---------------------------------------------------------------------------
#  ② 认不出时不许猜
# ---------------------------------------------------------------------------

def test_non_literal_name_is_skipped_not_guessed():
    """
    名字不是字面量时**跳过**，绝不猜一个出来。

    ⚠ 这条判据原本写反了（2026-10-07）：第一版期望"把 `MY_NAME` 当成名字
    记下来"。那是**猜**。猜错的名字会一路进 M3 的解析分派 —— 工具会把
    `MY_NAME ...:` 这类根本不是自定义语句的行当成自定义语句，那是静默改
    语义。说"不知道"只是少一层能力；猜错是写坏别人的脚本。
    """
    src = ('python early:\n'
           '    renpy.register_statement(name=MY_NAME, block=True)\n')
    assert parse_cds_calls(src) == [], (
        '认不出名字就该跳过，不能凭空编一个 spec')


def test_literal_positional_still_works_when_name_kwarg_is_expr():
    """
    但 `name=` 不是字面量、**第一个位置参数是字面量**时仍要读出来。

    Ren'Py 两种写法都支持，手写注册里常常混着用。
    """
    src = ('python early:\n'
           '    renpy.register_statement("realname", name=ALIAS_VAR)\n')
    specs = parse_cds_calls(src)
    assert len(specs) == 1 and specs[0].name == 'realname', specs


def test_empty_call_is_skipped_not_crashed():
    assert parse_cds_calls('python early:\n    renpy.register_statement()\n') == []


def test_unbalanced_call_does_not_crash_and_is_skipped():
    src = 'python early:\n    renpy.register_statement(name="a", block=True\n'
    assert parse_cds_calls(src) == []


def test_broken_text_never_raises():
    """一份手写坏的片段不该让项目打不开 —— 什么输入都要能返回。"""
    for junk in ('', 'renpy.register_statement(', '"""', '((((', '##',
                 'renpy.register_statement(name='):
        assert isinstance(parse_cds_calls(junk), list)


# ---------------------------------------------------------------------------
#  ③ 项目扫描
# ---------------------------------------------------------------------------

def test_scan_project_dedups_and_reports(tmp_path):
    g = tmp_path / 'game'
    g.mkdir()
    (g / 'a.rpy').write_text(
        'python early:\n'
        '    renpy.register_statement(name="mycue", block=True)\n',
        encoding='utf-8')
    # 同一个名字在另一个文件里又注册一次 → 只留第一条
    (g / 'b.rpy').write_text(
        'python early:\n'
        '    renpy.register_statement(name="mycue", block=False)\n'
        '    renpy.register_statement(name="battle", block=True)\n',
        encoding='utf-8')
    specs = scan_project_cds(tmp_path)
    names = sorted(s.name for s in specs)
    assert names == ['battle', 'mycue'], specs
    assert next(s for s in specs if s.name == 'mycue').block == 'yes'

    text = format_cds_report(specs)
    assert text.splitlines()[0].startswith('项目用 CDS 声明了 2 个自定义语句')
    assert 'mycue' in text and 'battle' in text


def test_scan_skips_nested_copy(tmp_path):
    """嵌套副本（子目录自带 `game/`）不该被当成项目脚本扫。"""
    g = tmp_path / 'game'
    g.mkdir()
    nested = g / 'sr_general' / 'game'
    nested.mkdir(parents=True)
    (nested / 'x.rpy').write_text(
        'python early:\n    renpy.register_statement(name="ghost")\n',
        encoding='utf-8')
    assert scan_project_cds(tmp_path) == []


def test_report_accepts_dicts_too():
    """
    报告要能吃 `to_dict()` 的形态 —— 它跨层传递（session → 纯函数报告），
    沿途不该要求 dataclass 实例。
    """
    text = format_cds_report([
        {'name': 'mycue', 'aliases': ['mycue', 'cue'], 'block': 'yes',
         'hooks': ['parse', 'lint'], 'file': 'a.rpy', 'line': 3},
        {'name': ''},          # 空名字：跳过，不参与
        'junk',                # 别的东西：跳过，不抛
    ])
    assert text.splitlines()[0].startswith('项目用 CDS 声明了 1 个')
    assert 'a.rpy:3' in text and 'parse,lint' in text


def test_report_says_so_when_nothing_declared():
    text = format_cds_report([])
    assert '没有用' in text
    assert 'storygraph.rules.json' in text, '要给出**出路**，不能只说没有'


@pytest.mark.skipif(not REAL.is_dir(), reason='换机 skip')
def test_real_project_scan_does_not_crash():
    """真机：这个项目没有 CDS —— 要么空、要么有问题清单，**但绝不许抛**。"""
    specs = scan_project_cds(REAL)
    assert isinstance(specs, list)
    for s in specs:
        assert isinstance(s, StatementSpec)
        assert s.name and s.source == 'host-registry'
