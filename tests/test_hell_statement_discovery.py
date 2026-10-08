"""
地狱测试 · 自定义语句发现与声明（CST 区域，2026-10-08）
======================================================

「后面加的 CST 区域」在 `story/` 这一侧有三件套：

  · `cds_registry`：读项目自己的语法说明书（`renpy.register_statement`）；
  · `statement_registry`：清单（rules.json）> 宿主注册 的合并与查询；
  · `declare_statement` / `describe_statements`：「教一次」的写入口与摊开。

这一份专门为难它们：**发现**（该扫到的没扫到 = 静默失能）、**字面量**
（认不出就别猜）、**写清单**（坏 JSON 不许覆盖）。判据都落在"行为"上，
不落在实现细节上（实现换了仍然有意义）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story.cds_registry import (                          # noqa: E402
    find_scripts, parse_cds_calls, scan_project_cds, spec_from_call,
)
from storygraph.story.statement_registry import (                    # noqa: E402
    StatementRegistry, clear_cache, declare_statement, describe_statements,
)


def _reg(block='True'):
    return f'python early:\n    renpy.register_statement(name="shop", block={block})\n'


def _proj(tmp_path, name='p'):
    root = tmp_path / name
    (root / 'game').mkdir(parents=True, exist_ok=True)
    return root


# ===========================================================================
#  ① 发现：该扫到的必须扫到（静默失能 = 最坏的一种）
# ===========================================================================

def test_project_under_dot_named_ancestor_is_still_scanned(tmp_path):
    """
    **项目根上面有一层以 `.` 开头的目录时，脚本一条都不许被漏掉。**

    2026-10-08 修的真 bug：`find_scripts` 原来拿**绝对路径**逐段比 `startswith('.')`
    —— 只要祖先里有 `.pip-tmp` / `.venv` / 用户把项目放在 `~/.games/mygame`，
    **整个项目的脚本全被跳过**：CDS 认不出、自定义语句一条都发现不了，
    而且没有任何报错（表现成"这项目好像没声明过自定义语句"）。
    """
    root = tmp_path / '.hidden_root'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'a.rpy').write_text(_reg(), encoding='utf-8')
    found = find_scripts(root)
    assert found, '祖先目录以 . 开头 → 整个项目的脚本被静默跳过了'
    specs = scan_project_cds(root)
    assert [s.name for s in specs] == ['shop'], specs


def test_dot_dir_inside_project_is_skipped(tmp_path):
    """项目**内部**的点号目录（`.venv` / `.git` / 备份）仍然要跳过。"""
    root = _proj(tmp_path)
    (root / 'game' / 'ok.rpy').write_text(_reg(), encoding='utf-8')
    dot = root / 'game' / '.venv'
    dot.mkdir()
    (dot / 'junk.rpy').write_text(
        'renpy.register_statement(name="junk")\n', encoding='utf-8')
    names = sorted(s.name for s in scan_project_cds(root))
    assert names == ['shop'], names


def test_build_dirs_inside_project_are_skipped(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'ok.rpy').write_text(_reg(), encoding='utf-8')
    for d in ('build', 'tmp', '__pycache__'):
        sub = root / 'game' / d
        sub.mkdir()
        (sub / 'x.rpy').write_text(
            'renpy.register_statement(name="ghost")\n', encoding='utf-8')
    assert [s.name for s in scan_project_cds(root)] == ['shop']


def test_generated_and_nested_copy_skipped(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'ok.rpy').write_text(_reg(), encoding='utf-8')
    (root / 'game' / 'a.generated.rpy').write_text(
        'renpy.register_statement(name="gen")\n', encoding='utf-8')
    nested = root / 'game' / 'copy' / 'game'
    nested.mkdir(parents=True)
    (nested / 'x.rpy').write_text(
        'renpy.register_statement(name="nested")\n', encoding='utf-8')
    assert [s.name for s in scan_project_cds(root)] == ['shop']


# ===========================================================================
#  ② 字面量：认不出就别猜（猜错会静默改别人的脚本）
# ===========================================================================

def test_name_from_keyword_and_aliases():
    spec = spec_from_call('name="mycue battle", block=True')
    assert spec is not None and spec.name == 'mycue'
    assert spec.aliases == ['mycue', 'battle']
    assert spec.block == 'yes'


def test_name_from_first_positional():
    spec = spec_from_call('"battle", block=True')
    assert spec is not None and spec.name == 'battle'


def test_name_from_variable_is_not_guessed():
    """`name=SOME_CONST` 认不出 → **跳过**（绝不猜一个名字）。"""
    assert spec_from_call('name=SOME_CONST, block=True') is None


def test_block_from_variable_is_possible_with_note():
    spec = spec_from_call('name="shop", block=NEEDS_BLOCK')
    assert spec is not None and spec.block == 'possible'
    assert spec.note, '认不出的 block= 必须留一句 note，别装作知道'


def test_nested_call_args_do_not_split_name():
    """`parse=lambda lex: f(a, b)` 里的逗号是**嵌套**的，不许把 name= 切走。"""
    body = ('name="mycue", parse=lambda lex: helper(a, b), '
            'lint=lint_it')
    spec = spec_from_call(body)
    assert spec is not None and spec.name == 'mycue'
    assert 'parse' in spec.hooks and 'lint' in spec.hooks


def test_string_with_paren_comma_and_comment_inside_call():
    body = ('name="a(b,c)", parse=parse_it, lint=lint_it  # ) 假的收尾\n')
    spec = spec_from_call(body)
    assert spec is not None and spec.name == 'a(b,c)', spec


def test_escaped_quote_and_multi_line_call():
    text = (
        'python early:\n'
        '    renpy.register_statement(\n'
        '        name="battle scene",\n'
        '        block=True,\n'
        '        parse=parse_battle,\n'
        '    )\n'
    )
    specs = parse_cds_calls(text)
    assert len(specs) == 1 and specs[0].name == 'battle'


def test_two_register_calls_in_one_file():
    text = (_reg('True')
            + '    renpy.register_statement(name="shop", block=False)\n')
    names = sorted(s.name for s in parse_cds_calls(text))
    assert names == ['shop', 'shop']      # 同一份文件里两次 → 两条（去重在 scan 层）


def test_malformed_input_never_raises():
    for junk in ('', 'renpy.register_statement(', '"""', '((((', '##',
                 'renpy.register_statement(name='):
        assert isinstance(parse_cds_calls(junk), list)


# ===========================================================================
#  ③ 优先级：清单 > 宿主注册（用户能覆盖程序）
# ===========================================================================

def test_declared_overrides_host_registry(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'cds.rpy').write_text(_reg('False'), encoding='utf-8')
    (root / 'storygraph.rules.json').write_text(
        json.dumps({'statements': [{'name': 'shop', 'block': True}]},
                   ensure_ascii=False), encoding='utf-8')
    clear_cache()
    hit = StatementRegistry.for_project(root).match('shop')
    assert hit is not None
    assert hit.source == 'declared'
    assert hit.has_block is True, '用户说带块就带块，不许被程序 block=False 盖掉'


def test_host_registry_used_when_manifest_silent(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'cds.rpy').write_text(_reg('True'), encoding='utf-8')
    clear_cache()
    hit = StatementRegistry.for_project(root).match('shop')
    assert hit is not None and hit.source == 'host-registry', hit


def test_match_strips_trailing_colon(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'cds.rpy').write_text(_reg('True'), encoding='utf-8')
    clear_cache()
    reg = StatementRegistry.for_project(root)
    assert reg.match('shop:') is not None
    assert reg.match('  shop  ') is not None
    assert reg.match('nope') is None


# ===========================================================================
#  ④ 写清单：坏 JSON 绝不覆盖；同名更新；别的键原样保留
# ===========================================================================

def test_declare_refuses_to_clobber_broken_json(tmp_path):
    root = _proj(tmp_path)
    manifest = root / 'storygraph.rules.json'
    manifest.write_text('{ 这不是合法 JSON', encoding='utf-8')
    res = declare_statement(root, 'mycue', block=True)
    assert res['ok'] is False and res['action'] == 'refused', res
    assert '不是合法 JSON' in res['reason']
    assert manifest.read_text(encoding='utf-8') == '{ 这不是合法 JSON', \
        '用户手写的坏清单被覆盖了 —— 不可逆的破坏'


def test_declare_same_name_updates_in_place_and_keeps_other_keys(tmp_path):
    root = _proj(tmp_path)
    manifest = root / 'storygraph.rules.json'
    manifest.write_text(json.dumps({
        'statements': [{'name': 'mycue', 'docs': '旧说明'},
                       {'name': 'battle'}],
        'unknown_statements': {'allow': ['x']},
    }, ensure_ascii=False), encoding='utf-8')
    res = declare_statement(root, 'mycue', label='闪电', block=True,
                            columns=[{'name': 'intensity', 'required': True}])
    assert res['ok'] and res['action'] == 'updated', res
    data = json.loads(manifest.read_text(encoding='utf-8'))
    names = [s['name'] for s in data['statements']]
    assert names.count('mycue') == 1, '教两次变成了两条声明'
    assert names == ['mycue', 'battle'], names
    mycue = next(s for s in data['statements'] if s['name'] == 'mycue')
    assert mycue['docs'] == '旧说明', '更新时把用户原来的 docs 冲掉了'
    assert mycue['label'] == '闪电'
    assert mycue.get('unknown_statements') is None
    assert data['unknown_statements'] == {'allow': ['x']}, '顶层别的段被改动了'


def test_declare_empty_name_refused(tmp_path):
    root = _proj(tmp_path)
    assert declare_statement(root, '   ')['ok'] is False


def test_declare_then_describe_sees_declared(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'ch.rpy').write_text(
        'label a:\n    mycue "闪电":\n        target = ling\n    return\n',
        encoding='utf-8')
    clear_cache()
    before = describe_statements(project_root=root)
    row = next(r for r in before['statements'] if r['name'] == 'mycue')
    assert row['source'] == 'shape-guess', row

    assert declare_statement(root, 'mycue', label='闪电提示',
                             block=True)['ok']
    clear_cache()
    after = describe_statements(project_root=root)
    row = next(r for r in after['statements'] if r['name'] == 'mycue')
    assert row['declared'] is True and row['source'] == 'declared', row


# ===========================================================================
#  ⑤ 项目约定第三层：`unknown_statements` 的假阳性 + 接进构建报告
# ===========================================================================
# 缺口清单第 2 条：`project_conventions` 三层机制**零生产调用**。接进构建
# 报告之前先要它**不假阳性** —— 真实项目上它曾报出两条噪声：
#   · `jc "台词" (no_skip=True)`（台词带 say 参数，`=` 在括号里）；
#   · `show screen prompt(…, act=[` 的**续行** `Call("…")`。
# 接一条会乱叫的检查比不接更糟（会把报告噪声化）。

def _unknown(root):
    from storygraph.story.project_conventions import unknown_statements
    return unknown_statements(root)


def test_say_with_kwargs_is_not_an_unknown_statement(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'a.rpy').write_text(
        'label a:\n'
        '    jc "tai ci" (no_skip=True)\n'
        '    "plain line"\n'
        '    return\n', encoding='utf-8')
    assert _unknown(root) == {}, '台词带 say 参数被误报成语句头'


def test_multiline_statement_continuation_is_not_a_statement(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'a.rpy').write_text(
        'label a:\n'
        '    show screen prompt("x", act=[\n'
        '        Call("mini"),\n'
        '    ])\n'
        '    return\n', encoding='utf-8')
    assert _unknown(root) == {}, '多行语句的续行被误报成语句头'


def test_custom_statement_with_toplevel_kwarg_is_reported(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'a.rpy').write_text(
        'label a:\n    mycue "flash" intensity=3\n    return\n',
        encoding='utf-8')
    got = _unknown(root)
    assert 'mycue' in got and got['mycue']['count'] == 1, got


def test_known_and_container_statements_are_silent(tmp_path):
    root = _proj(tmp_path)
    (root / 'game' / 'a.rpy').write_text(
        'init python:\n'
        '    x = 1\n'
        'screen s():\n'
        '    text "hi"\n'
        'label a:\n'
        '    show ling\n'
        '    while game.f:\n'
        '        "x"\n'
        '    return\n', encoding='utf-8')
    assert _unknown(root) == {}, '已知/容器语句里的内容被误报'


def test_unknown_head_reaches_the_build_report(tmp_path):
    """
    **接进构建报告**（三层机制的第一个真实消费者）：有未认识的结构头时，
    报告里必须点名 —— 这是它设计要回答的那个问题。
    """
    from storygraph.pipeline.build import build_project
    root = _proj(tmp_path)
    (root / 'game' / 'a.rpy').write_text(
        'label a:\n    mycue "flash" intensity=3\n    return\n',
        encoding='utf-8')
    res = build_project(str(root), out_dir=str(tmp_path / 'out'))
    guard = (res.report.get('build', {}) or {}).get('guard') or {}
    text = guard.get('statements') or ''
    assert '工具不认识的语句头' in text, text
    assert 'mycue' in text, text


def test_clean_project_has_no_unknown_head_line(tmp_path):
    from storygraph.pipeline.build import build_project
    root = _proj(tmp_path)
    (root / 'game' / 'a.rpy').write_text(
        'label a:\n    "a line"\n    return\n', encoding='utf-8')
    res = build_project(str(root), out_dir=str(tmp_path / 'out'))
    guard = (res.report.get('build', {}) or {}).get('guard') or {}
    assert '工具不认识的语句头' not in (guard.get('statements') or '')
