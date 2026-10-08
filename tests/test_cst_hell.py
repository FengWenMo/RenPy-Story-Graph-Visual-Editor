"""
CST **地狱级**验收（用户 2026-10-07 点名："专门检验 CST，可以用真实项目"）
================================================================================

分三层，每层都要能**被变异打红**（不然就是空转判据）：

  ① **真机对账**（`sr_general`，换机 skip）：53 个脚本逐条核对
     "`raw_text` 真的逐字出现在**这一份**源文件里" —— 覆盖率与 `mismatch`；
     并且**注入变异**：把 A 文件的语句拿去在 B 文件的原文里核对，
     `mismatch` 必须 > 0（证明这条判据不是恒真）。
  ② **刁钻形状**：自定义块里嵌 `python:` / 循环 / 行尾注释 / 参数带引号与 `=`，
     逐条要求"真迹 == 原文切片"。
  ③ **声明 → 工具的闭环**：`learn` 教一次 → `describe` 认成 `declared`
     → `W120`（缺必需参数）能红能绿 → 撤掉声明后**一条不报**。

只读：真机那层不写任何文件（`sr_general` 是验收对象，不是工作区）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core.cst import cst_coverage, raw_text_of          # noqa: E402
from storygraph.parsing.discover import discover_scripts           # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.statement_registry import (                  # noqa: E402
    clear_cache, declare_statement, describe_statements,
)

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
real_only = pytest.mark.skipif(not REAL.is_dir(), reason='换机 skip')


def _analyze(path: Path):
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return list(getattr(pr, 'statements', ()) or ())


# ---------------------------------------------------------------------------
#  ① 真机：逐条"真迹必须在这份原文里"
# ---------------------------------------------------------------------------

@real_only
def test_real_project_cst_is_clean():
    """真机 53 章：**每一条真迹都能在它自己那份原文里逐字找到**。"""
    files = discover_scripts(REAL)
    assert len(files) >= 40, len(files)
    total = mismatched = 0
    for f in files:
        text = Path(f).read_text(encoding='utf-8', errors='ignore')
        st = _analyze(Path(f))
        stats = cst_coverage(st, text)
        total += stats['total']
        mismatched += stats['mismatch']
        assert stats['mismatch'] == 0, (
            f'{Path(f).name}: {stats["mismatch"]} 条真迹在原文里找不到'
            f'（那是"贴错行"，比没有更危险）')
        assert stats['ratio'] == 1.0 or stats['with_raw'] == stats['verified'], \
            f'{Path(f).name}: {stats}'
    assert total > 500, total          # 真机量级；太小说明扫描路径错了
    assert mismatched == 0


@real_only
def test_real_project_cst_judgement_is_not_vacuous():
    """
    **变异**：拿 A 文件的语句去 B 文件的原文里核对 —— `mismatch` 必须 > 0。

    没有这一条，上面那条判据可能只是"函数永远返回 0"（空转）。
    """
    files = sorted(discover_scripts(REAL))
    # ⚠ 别拿 `files[0]` 硬当样本：真机里有**只有 define/transform 的文件**
    # （语句流是空的，`total == 0` → 判据自己先崩，看起来像"变异没生效"）。
    a = next((Path(f) for f in files if _analyze(Path(f))), None)
    assert a is not None, '真机居然没有一条语句'
    b = next((Path(f) for f in files
              if Path(f) != a and Path(f).read_text(encoding='utf-8',
                                                    errors='ignore')
              != a.read_text(encoding='utf-8', errors='ignore')), None)
    assert b is not None
    text_b = b.read_text(encoding='utf-8', errors='ignore')
    stats = cst_coverage(_analyze(a), text_b)
    assert stats['total'] > 0
    assert stats['mismatch'] > 0, (
        '把 A 的语句拿去 B 的原文里核对居然全对 —— 那这条判据是空转的')


# ---------------------------------------------------------------------------
#  ② 刁钻形状：真迹 == 原文切片
# ---------------------------------------------------------------------------

HARD = (
    'label a:\n'
    '    mycue "闪电" intensity=3 target="a=b" mode=\'x\':   # 行尾注释\n'
    '        "块里的台词"\n'
    '        python:\n'
    '            for i in range(3):\n'
    '                game.flag = i\n'
    '        while game.flag < 2:\n'
    '            "循环体台词"\n'
    '    l oh "3"\n'                        # 手写错句：工具不认识
    '    return\n'
)


def _hard_file(tmp_path) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    f = root / 'game' / 'hard.rpy'
    f.write_text(HARD, encoding='utf-8')
    return f


def test_hard_shapes_raw_text_is_verbatim(tmp_path):
    """块体里嵌 `python:` / 循环 / 行尾注释 / 参数带引号与 `=` —— 全部逐字。"""
    f = _hard_file(tmp_path)
    text = f.read_text(encoding='utf-8')
    st = _analyze(f)
    assert st, '语句流是空的'
    stats = cst_coverage(st, text)
    assert stats['mismatch'] == 0, stats
    raws = [raw_text_of(n) for n in st]
    joined = '\n'.join(r for r in raws if r)
    # 块体整段（含缩进）必须在真迹里；参数引号/`=` 不许被改写
    assert 'target="a=b"' in joined, joined
    assert "mode='x'" in joined, joined
    assert 'game.flag = i' in joined, joined
    assert '"循环体台词"' in joined, joined
    assert '# 行尾注释' in joined or '行尾注释' in joined, joined
    assert 'l oh "3"' in joined, joined


def test_hard_shapes_mismatch_is_detectable(tmp_path):
    """变异：故意拿别的原文去核对 → 必须报 mismatch（判据活着）。"""
    f = _hard_file(tmp_path)
    st = _analyze(f)
    other = 'label z:\n    "完全不相干的一句"\n'
    stats = cst_coverage(st, other)
    assert stats['mismatch'] == stats['with_raw'] > 0, stats


# ---------------------------------------------------------------------------
#  ③ 声明 → 工具闭环（教一次 → 认它 → 校验它 → 撤掉就不报）
# ---------------------------------------------------------------------------

LOOP_SCRIPT = (
    'label a:\n'
    '    mycue "闪电":\n'                  # 缺声明为 required 的 intensity
    '        target = ling\n'
    '    return\n'
)


def _loop_project(tmp_path) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'ch.rpy').write_text(LOOP_SCRIPT, encoding='utf-8')
    return root


def test_declaration_loop_end_to_end(tmp_path):
    from storygraph.pipeline.validate import validate_spec_args
    from storygraph.story.beat_build import beats_from_parse_result

    root = _loop_project(tmp_path)
    clear_cache()
    # ① 教之前：`describe` 说它是"猜的"
    desc = describe_statements(project_root=root)
    row = next(r for r in desc['statements'] if r['name'] == 'mycue')
    assert row['source'] == 'shape-guess', row

    # ② 教一次（落成声明）
    res = declare_statement(root, 'mycue', label='闪电提示', block=True,
                            columns=[{'name': 'intensity', 'required': True}])
    assert res['ok'], res
    desc = describe_statements(project_root=root)
    row = next(r for r in desc['statements'] if r['name'] == 'mycue')
    assert row['declared'] is True and row['source'] == 'declared', row

    # ③ 校验开始生效：缺声明为必需的 `intensity` → W120
    pr, _ = IncrementalRenPyAnalyzer().analyze(
        files=[root / 'game' / 'ch.rpy'], graph=None)
    beats = beats_from_parse_result(pr)
    codes = [i.code for i in validate_spec_args(beats, project_root=root)]
    assert 'W120' in codes, codes

    # ④ 参数补上 → 不报
    f = root / 'game' / 'ch.rpy'
    f.write_text(LOOP_SCRIPT.replace('mycue "闪电":',
                                     'mycue "闪电" intensity=3:'),
                 encoding='utf-8')
    clear_cache()
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    codes = [i.code for i in validate_spec_args(beats, project_root=root)]
    assert 'W120' not in codes, codes

    # ⑤ 撤掉声明 → 一条不报（少报胜过乱报）
    (root / 'storygraph.rules.json').write_text(
        json.dumps({'statements': []}, ensure_ascii=False), encoding='utf-8')
    clear_cache()
    beats = beats_from_parse_result(pr)
    codes = [i.code for i in validate_spec_args(beats, project_root=root)]
    assert 'W120' not in codes and 'W122' not in codes, codes
