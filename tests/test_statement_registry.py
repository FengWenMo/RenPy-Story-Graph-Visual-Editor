"""
M3：注册表接入 —— "被声明过的自定义语句"从 T3 升到 T1
=====================================================

设计文档 §3.2 的三来源优先级：

    storygraph.rules.json（declared，**覆盖**）
    > renpy.register_statement（host-registry）
    > 形状识别（不做）

这一层要证明的**不是**"能解析自定义语法"（那是 M3 之后的事），而是
一条更基础的保证：

    项目**声明过**的自定义语句，工具要认领它 ——
    不再报"我不认识"，且它的原文一个字节都不改。

判据分三类：

  ① 装配与优先级（rules.json / CDS / 覆盖关系 / 别名）；
  ② 端到端：声明过的语句**进通用行**（`Beat.spec_name` + `Beat.opaque`），
    过表往返后原文逐字不变，且门禁**不再**对它报 `UNMANAGED_BLOCK`；
  ③ 反向：**没声明**的同一条语句照旧报警 —— 否则"声明"就没有意义了。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.story.statement_registry import (RegisteredStatement,
                                                 StatementRegistry,
                                                 annotate_spec_statements,
                                                 classify_head, clear_cache,
                                                 format_registry_report,
                                                 registry_for_parse_result,
                                                 root_of)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


SCRIPT = (
    'label a:\n'
    '    "第一句"\n'
    '    mycue "闪电" intensity=3:\n'
    '        target = ling\n'
    '        hold 0.5\n'
    '    "第二句"\n'
    '    return\n'
)

#: 无块的叶形态 —— 它以前会掉进「代码」列被补 `$`（M3 一并修掉）
LEAF = 'label a:\n    "一"\n    mycue "闪电"\n    "二"\n    return\n'


def _project(tmp_path, manifest=None, script=SCRIPT, name='ch.rpy'):
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True, exist_ok=True)
    (game / name).write_text(script, encoding='utf-8')
    if manifest is not None:
        (root / 'storygraph.rules.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    return root, game / name


def _beats(path):
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return pr, beats_from_parse_result(pr)


def _roundtrip(tmp_path, path):
    _pr, beats = _beats(path)
    x = tmp_path / 'out.xlsx'
    export_beats_to_excel(beats, x)
    return compile_beats(import_beats_from_excel(x)).rpy_text


# ---------------------------------------------------------------------------
#  ① 装配与优先级
# ---------------------------------------------------------------------------

def test_manifest_declares_statements(tmp_path):
    root, _ = _project(tmp_path, {'statements': [
        'battle',
        {'name': 'mycue cue', 'block': True, 'docs': '闪电动画'},
    ]})
    reg = StatementRegistry.for_project(root)
    assert reg.match('battle') is not None
    assert reg.match('battle').has_block
    assert reg.match('cue') is not None, '`name` 里的多个词都是名字'
    assert reg.match('cue').name == 'mycue'
    assert reg.match('cue').docs == '闪电动画'
    assert reg.match('nope') is None


def test_manifest_overrides_host_registry(tmp_path):
    """
    `rules.json` **覆盖** CDS —— 这是"让用户能覆盖工具"的最后一根杠杆。

    冲突时听用户的（与 `project_conventions` 的"声明覆盖识别"同一条规矩）。
    """
    root, path = _project(tmp_path, {'statements': [
        {'name': 'mycue', 'block': True}]})
    (path.parent / 'cds.rpy').write_text(
        'python early:\n'
        '    renpy.register_statement(name="mycue", block=False)\n',
        encoding='utf-8')
    reg = StatementRegistry.for_project(root)
    hit = reg.match('mycue')
    assert hit.source == 'declared', hit.source
    assert hit.has_block is True, '用户说带块就带块，不被程序的 block=False 盖掉'


def test_host_registry_is_used_when_manifest_silent(tmp_path):
    root, path = _project(tmp_path, {})
    (path.parent / 'cds.rpy').write_text(
        'python early:\n'
        '    renpy.register_statement(name="shop", block=True)\n',
        encoding='utf-8')
    reg = StatementRegistry.for_project(root)
    hit = reg.match('shop')
    assert hit is not None and hit.source == 'host-registry'


def test_classify_head_without_registry_is_empty():
    assert classify_head('mycue "x":', None) == ''
    reg = StatementRegistry(declared=[RegisteredStatement(name='mycue')])
    assert classify_head('mycue "x":', reg) == 'declared'
    assert classify_head('other "x":', reg) == ''


def test_root_of_finds_project_root():
    # **比较归一化后的字符串**（2026-10-22，CI 的 Linux 上抓到）：
    # `root_of` 现在先做**字符串层面**的 `/game/` 判定 —— POSIX 上
    # `Path(r'D:\p\game\a.rpy')` 是单段路径（反斜杠不是分隔符），用 Path
    # 推不出来；字符串判定返回原样分隔符的 `D:/p`。
    # 而 `Path(r'D:\p')` 在 Linux 上是**一个字面反斜杠的目录名** ——
    # 拿 Path 比必然不等，这跟 `root_of` 判得对不对无关。
    def _slash(x):
        return str(x).replace('\\', '/').rstrip('/')

    assert _slash(root_of(r'D:\p\game\chapter\a.rpy')) == 'D:/p'
    assert _slash(root_of(str(Path('/p/game/a.rpy')))) == '/p'


def test_root_of_refuses_non_project_dir(tmp_path):
    """
    **不是项目就返回空** —— 不许"退回父目录"。

    退回去的代价实测过：`rglob` 会去扫 `tmp_path` 的**上层**（系统临时目录，
    里面成百上千个目录），一个用例从 0.6 秒涨到 227 秒。
    """
    loose = tmp_path / 'loose'
    loose.mkdir()
    f = loose / 'a.rpy'
    f.write_text('label a:\n    return\n', encoding='utf-8')
    assert root_of(f) == '', '没有 game/ 也没有 rules.json 就不是项目根'


def test_root_of_accepts_manifest_without_game_dir(tmp_path):
    """有清单也算项目（有的项目根下只有 rules.json + 松散脚本）。"""
    f = tmp_path / 'a.rpy'
    f.write_text('label a:\n    return\n', encoding='utf-8')
    (tmp_path / 'storygraph.rules.json').write_text('{}', encoding='utf-8')
    assert Path(root_of(f)) == Path(tmp_path)


def test_annotate_only_touches_unmodeled():
    """内置建模过的语句（say 等）不该被 spec 抢走。"""
    reg = StatementRegistry(declared=[RegisteredStatement(name='mycue')])
    stmts = [
        type('S', (), {'payload': {'raw_kind': 'unmodeled',
                                   'text': 'mycue "x"'}})(),
        type('S', (), {'payload': {'raw_kind': '', 'text': 'mycue "y"'}})(),
        type('S', (), {'payload': {'raw_kind': 'unknown_block',
                                   'text': 'mycue "z":'}})(),
    ]
    assert annotate_spec_statements(stmts, reg) == 2
    assert stmts[0].payload['spec_name'] == 'mycue'
    assert 'spec_name' not in stmts[1].payload


def test_registry_report_mentions_both_sources(tmp_path):
    root, path = _project(tmp_path, {'statements': ['battle']})
    (path.parent / 'cds.rpy').write_text(
        'python early:\n'
        '    renpy.register_statement(name="shop", block=True)\n',
        encoding='utf-8')
    text = format_registry_report(StatementRegistry.for_project(root))
    assert text.splitlines()[0].startswith('项目声明了 2 个自定义语句')
    assert 'battle' in text and 'shop' in text
    assert '清单声明' in text and '宿主注册' in text


def test_report_tells_you_how_to_declare_when_empty():
    text = format_registry_report(StatementRegistry())
    assert '没有声明' in text
    assert 'register_statement' in text and 'rules.json' in text


# ---------------------------------------------------------------------------
#  ② 端到端：声明过 → 通用行，原文逐字不变，门禁不再报
# ---------------------------------------------------------------------------

def test_declared_block_goes_to_generic_row(tmp_path):
    root, path = _project(tmp_path, {'statements': [{'name': 'mycue',
                                                     'block': True}]})
    _pr, beats = _beats(path)
    hits = [b for b in beats if getattr(b, 'opaque', '')]
    assert len(hits) == 1, [(b.seq, b.text, b.opaque) for b in beats]
    assert hits[0].spec_name == 'mycue', '被声明的要认领（T1）'
    assert 'target = ling' in hits[0].opaque


def test_declared_block_survives_roundtrip_verbatim(tmp_path):
    root, path = _project(tmp_path, {'statements': [{'name': 'mycue',
                                                     'block': True}]})
    out = _roundtrip(tmp_path, path)
    for line in ['mycue "闪电" intensity=3:', 'target = ling', 'hold 0.5']:
        assert line in out, out
    assert '\npython:' not in out, f'声明过的语句被推断成了 python 块：\n{out}'


def test_declared_leaf_statement_is_not_dollar_prefixed(tmp_path):
    """
    **叶形态**（不带块）也必须走通用行。

    它以前掉进「代码」列：`render_code_block` 认不出它（不像台词、也不在
    动词白名单里）就补 `$` → `$ mycue "闪电"` = 语法错误。
    与 M1 修的块形态是同一个坑，只是形状不同。
    """
    root, path = _project(tmp_path, {'statements': ['mycue']}, script=LEAF)
    out = _roundtrip(tmp_path, path)
    assert 'mycue "闪电"' in out, out
    for line in out.splitlines():
        s = line.strip()
        if s.startswith('$') and 'mycue' in s:
            pytest.fail(f'叶形态被补了 `$`：{line!r}\n{out}')


def test_declared_block_no_longer_reported_as_unmanaged(tmp_path):
    """声明过就不该再报 `UNMANAGED_BLOCK` —— 否则"声明"毫无意义。"""
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.generation_guard import evaluate_generation

    root, path = _project(tmp_path, {'statements': [{'name': 'mycue',
                                                     'block': True}]})
    _pr, beats = _beats(path)
    text = render_beats_to_rpy(beats, label_name='a')
    codes = [i.code for i in evaluate_generation(
        text, tmp_path / 'o.rpy', beats=beats)]
    assert 'UNMANAGED_BLOCK' not in codes, codes


# ---------------------------------------------------------------------------
#  ③ 反向：没声明就照旧报警（否则"声明"没有意义）
# ---------------------------------------------------------------------------

def test_undeclared_block_still_reported(tmp_path):
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.generation_guard import evaluate_generation

    root, path = _project(tmp_path, {})
    _pr, beats = _beats(path)
    assert not [b for b in beats if getattr(b, 'spec_name', '')], (
        '没声明不该被认领')
    text = render_beats_to_rpy(beats, label_name='a')
    codes = [i.code for i in evaluate_generation(
        text, tmp_path / 'o.rpy', beats=beats)]
    assert 'UNMANAGED_BLOCK' in codes, codes


def test_registry_is_derived_from_parse_result(tmp_path):
    """反推项目根 → 装配注册表（8 个调用点不必各自传参）。"""
    root, path = _project(tmp_path,
                          {'statements': ['mycue']})
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    reg = registry_for_parse_result(pr)
    assert reg is not None and reg.match('mycue') is not None
