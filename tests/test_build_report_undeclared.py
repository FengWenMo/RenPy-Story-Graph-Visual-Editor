"""
构建报告要点名"工具不认识的东西"（CST 第二步：缺声明要**说出口**）
========================================================================

设计文档 `docs/设计_自定义结构发现与交互_2026-10-07.md` 的落地第 2 步：
**列/下拉/保真档全部从声明推导；缺声明就自动降级，并在报告里点名。**

"降级"这一半本来就成立（没声明 → 没有"每参数一列"、保真按形状判）；
缺的是**点名** —— 不然"表里少了几列"永远没人发现，直到有人手工对账。

名单的真源与 `cli describe` 同一份（`describe_statements`），这里钉的是
"它真的进了构建报告"这条线。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.pipeline.build import build_project            # noqa: E402
from storygraph.story.statement_registry import clear_cache   # noqa: E402

SCRIPT = (
    'label a:\n'
    '    "台词"\n'
    '    mycue "闪电" intensity=3:\n'
    '        target = ling\n'
    '    l oh "3"\n'
    '    return\n'
)

RULES = {'statements': [{'name': 'mycue', 'label': '闪电提示', 'block': True,
                         'columns': [{'name': 'intensity', 'label': '强度'}]}]}


def _project(tmp_path, with_rules: bool) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'ch.rpy').write_text(SCRIPT, encoding='utf-8')
    if with_rules:
        (root / 'storygraph.rules.json').write_text(
            json.dumps(RULES, ensure_ascii=False), encoding='utf-8')
    return root


def _statements_text(root: Path) -> str:
    """
    取构建报告里那段"语句"文本。

    ⚠ 路径是 `result.report['build']['guard']['statements']`
    （`guard_info` 挂在 `build` 节下，见 `pipeline/build.py`）——
    第一版猜成 `collect_report()['report']['guard']`（那是**体检**那条路，
    不含构建门禁那一段），取到空串、判据空转。
    """
    clear_cache()
    result = build_project(str(root), out_dir=root / 'build_out',
                           write_report=False)
    return str(((result.report.get('build') or {}).get('guard') or {})
               .get('statements') or '')


def test_report_names_the_undeclared_head(tmp_path):
    """`l`（真机里那条手写错句）没声明 → 报告里必须点名，并给出怎么修。"""
    text = _statements_text(_project(tmp_path, with_rules=True))
    assert '未声明' in text, text
    assert 'l×1' in text, text
    assert 'storygraph.rules.json' in text, text
    # 声明过的 `mycue` **不该**出现在"只靠形状猜"的名单里
    assert 'mycue' not in text.split('未声明')[-1], text


def test_report_stays_quiet_when_everything_is_declared(tmp_path):
    """都声明了就别报警（噪声会把真问题淹掉）。"""
    root = tmp_path / 'proj2'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'ch.rpy').write_text(
        'label a:\n    "台词"\n    mycue "闪电":\n        x = 1\n    return\n',
        encoding='utf-8')
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False), encoding='utf-8')
    text = _statements_text(root)
    assert '未声明' not in text, text
