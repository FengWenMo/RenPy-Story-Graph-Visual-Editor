"""
`W122`：块体里出现了**声明之外**的语句（结构级约束只能被声明）
================================================================

设计文档 `docs/archive/设计_自定义结构发现与交互_2026-10-07.md` §2.3 的第三条规则，
也是 CST 第四步"校验规则从声明生成"里**最后一条**（前两条 W120 必填 /
W121 取值早已在 `tests/test_spec_choices.py` 里钉着）。

为什么这条只能是"声明"（§0 的 ②层）：语法形状能看出"有块体"，
但看不出"这个块里允许写 `say`/`show` 还是只允许 Python" —— 那是项目自己的
约定，工具猜就是编。声明写成 `body: {"allows": ["say","show"]}`。

口径与 W121 一致：**警告不是错误**，而且刻意保守 ——
认不出头的行（裸 Python / `$` / 注释）一律放过，只在"有明确的头、而它不在
允许表里"时报警（少报胜过乱报，报多了人就去关校验）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.pipeline.validate import validate_spec_args           # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result       # noqa: E402
from storygraph.story.statement_registry import clear_cache           # noqa: E402

RULES = {'statements': [{
    'name': 'mycue', 'label': '闪电提示', 'block': True,
    'columns': [{'name': 'intensity', 'label': '强度'}],
    'body': {'allows': ['say', 'show']},
}]}

SCRIPT_OK = (
    'label a:\n'
    '    mycue "闪电" intensity=3:\n'
    '        "块里的台词"\n'
    '        show ling happy\n'
    '        target = ling\n'          # 裸 Python：认不出头 → **放过**
    '    return\n'
)

SCRIPT_BAD = (
    'label a:\n'
    '    mycue "闪电" intensity=3:\n'
    '        "块里的台词"\n'
    '        scene bg room\n'          # 不在允许表里 → W122
    '    return\n'
)


def _beats(tmp_path, script, with_body=True):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    f = root / 'game' / 'ch.rpy'
    f.write_text(script, encoding='utf-8')
    rules = dict(RULES)
    if not with_body:
        rules = {'statements': [{k: v for k, v in RULES['statements'][0].items()
                                 if k != 'body'}]}
    (root / 'storygraph.rules.json').write_text(
        json.dumps(rules, ensure_ascii=False), encoding='utf-8')
    clear_cache()
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr), root


def _codes(issues):
    return [i.code for i in issues]


def _text(i) -> str:
    """`Issue` 是 dataclass（`message` / `hint` / `where`），**没有 `format()`**。"""
    return ' '.join(str(getattr(i, a, '') or '')
                    for a in ('code', 'message', 'hint', 'where'))


def _dump(issues):
    return [_text(i) for i in issues]


def test_allowed_body_produces_no_warning(tmp_path):
    beats, root = _beats(tmp_path, SCRIPT_OK)
    issues = validate_spec_args(beats, project_root=root)
    assert 'W122' not in _codes(issues), _dump(issues)


def test_unlisted_statement_in_body_warns(tmp_path):
    """`scene` 不在允许表里 → 一条 W122，且要说清允许清单与那一行。"""
    beats, root = _beats(tmp_path, SCRIPT_BAD)
    issues = validate_spec_args(beats, project_root=root)
    hits = [i for i in issues if i.code == 'W122']
    assert len(hits) == 1, _dump(issues)
    text = _text(hits[0])
    assert 'scene' in text, text
    assert 'say' in text, text          # 允许清单要说出来
    assert 'body.allows' in text, text  # 怎么修也要说


def test_no_declaration_means_no_check(tmp_path):
    """没声明 `body.allows` → **一条不报**（少报胜过乱报）。"""
    beats, root = _beats(tmp_path, SCRIPT_BAD, with_body=False)
    issues = validate_spec_args(beats, project_root=root)
    assert 'W122' not in _codes(issues), _dump(issues)
