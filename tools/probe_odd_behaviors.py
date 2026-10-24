# -*- coding: utf-8 -*-
"""
古怪行为清单的**修复后探针**（`docs/审查_古怪行为清单_2026-10-23.md`）。

2026-10-23 晚：清单里的条目已经修掉，这些探针从"把怪行为摆出来给人看"
改成**正向断言** —— 它们现在钉的是"修好之后应该长什么样"，与 `tests/`
里的回归用例互补（这里打印证据，测试负责拦截）。

用法：
    D:/Python/python.exe tools/probe_odd_behaviors.py          # 全跑
    D:/Python/python.exe tools/probe_odd_behaviors.py label    # 只跑某一段

需要 `openpyxl`（import 链会拉到 `assets.asset_registry`）。
"""
from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _hr(title: str) -> None:
    print()
    print('=' * 66)
    print(title)
    print('=' * 66)


def _ok(label: str, condition: bool) -> None:
    print(f'  {"✔" if condition else "✘"} {label}')
    if not condition:
        raise AssertionError(label)


def probe_label() -> None:
    """清单 §二：多 label 文件里插一拍，空 label 继承**前文最近**的 label。"""
    _hr('§二 多 label 文件的空 label 归属（已修）')
    from storygraph.app.editor import EditorSession
    from storygraph.story.beat_model import Beat

    session = EditorSession()
    session.beats = [
        Beat(label='ch01', text='第一章第一句'),
        Beat(label='', text='（用户在 ch01 里新插的一拍）'),
        Beat(label='ch02', text='第二章开场'),
        Beat(label='', text='（用户在 ch02 里新插的一拍）'),
    ]
    session._renumber()
    got = [(b.label, b.line_id) for b in session.beats]
    for i, (label, lid) in enumerate(got, 1):
        print(f'  {i}. label={label!r:8} line_id={lid!r}')
    _ok('第 4 拍归属于 ch02（不是文件开头的 ch01）',
        got[3][0] == 'ch02' and got[3][1].startswith('ch02.'))
    _ok('第 2 拍继承 ch01', got[1] == ('ch01', 'ch01.0002'))
    _ok('第 3 拍是 ch02.0001（label 内发号）', got[2] == ('ch02', 'ch02.0001'))


def probe_ids() -> None:
    """清单 §三 / §七 / §十六：ID 语法、跨章重号、发号器分工。"""
    _hr('§三 / §七 / §十六 行身份（已修）')
    from storygraph.story.line_identity import allocate, parse, unique_ids

    print('  parse(ch01.0002~2) ->', parse('ch01.0002~2'))
    _ok('`~N` 仍然不在语法内（但它不会再被生成）',
        parse('ch01.0002~2') is None)
    print('  allocate({ch01.0002, ch01.0002~2}) ->',
          allocate({'ch01.0002', 'ch01.0002~2'}, 'ch01'))

    class _B:
        def __init__(self, lid, label):
            self.line_id, self.label, self.seq = lid, label, 0

    beats = [_B('ch01.0003', 'ch02'), _B('ch01.0003', 'ch02')]
    unique_ids(beats)
    print('  跨章重号后:', [(b.label, b.line_id) for b in beats])
    _ok('重发号用 beat.label（ch02），不从 ID 前缀反推 ch01',
        beats[1].line_id.startswith('ch02.'))

    from storygraph.story.beat_model import Beat, ensure_ids, refresh_fingerprints
    only = [Beat(label='ch01', seq=1, text='a'),
            Beat(label='ch01', seq=9, text='b', line_id='ch01.0042')]
    ensure_ids(only)
    _ok('ensure_ids 只补空，不动已有身份',
        only[0].line_id == 'ch01.0001' and only[1].line_id == 'ch01.0042')
    refresh_fingerprints(only)
    _ok('refresh_fingerprints 不换身份', only[1].line_id == 'ch01.0042')


def probe_facts() -> None:
    """清单 §六："没取到"与"真的没有"在事实文件里必须长得不一样。"""
    _hr('§六 引擎事实的缺口（已修）')
    from storygraph.core import engine_facts as EF

    facts = EF.build_facts(
        {
            'images': [('probe', None)],          # 探针没取到（API 改名）
            'tags': [('probe', {})],              # 真的是空
            'transforms': [('json_dump', ['left'])],
        },
        collection={'parse_clean': True, 'probe_complete': True, 'lint_exit': 0},
    )
    print('  facts 段里的键:', sorted(facts['facts']))
    print('  gaps:', facts['gaps'])
    print('  is_collectable ->', EF.is_collectable(facts))
    _ok('images 没取到 → 明确缺口', [g['key'] for g in EF.gaps_of(facts)] == ['images'])
    _ok('tags 空值被保留成"采到了、真的是空"',
        EF.value_of(facts, 'tags') == {} and EF.source_of(facts, 'tags') == 'probe')
    ok, why = EF.is_collectable(facts)
    _ok('整份事实仍然可用，但标成"部分可用"', ok and '部分可用' in why)
    _ok('validate_facts 通过', EF.validate_facts(facts) == [])


def probe_blocked() -> None:
    """清单 §一：`.blocked.rpy` 落在 game/ 之外，且不再占 label。"""
    _hr('§一 .blocked.rpy 的落点与反噬（已修）')
    import tempfile

    from storygraph.parsing.discover import discover_scripts
    from storygraph.story.generation_guard import (labels_in_danger,
                                                   occupied_labels,
                                                   write_generation)
    from storygraph.story.paths import inside_game_dir

    root = Path(tempfile.mkdtemp(prefix='sg-probe-blocked-'))
    game = root / 'game'
    game.mkdir(parents=True)
    (game / 'script.rpy').write_text('label start:\n    return\n',
                                     encoding='utf-8')
    target = game / 'ch01.rpy'
    before = sorted(p.name for p in game.glob('*.rpy'))
    art = write_generation('label ch01:\n    show 叶怀瑾\n', target,
                           project_root=root)
    print('  拦下时的副本落点：', art.review_path)
    _ok('坏产物没有写进目标路径', not target.exists() and art.blocked)
    _ok('副本不在 game/ 里（Ren\'Py 不会扫描）',
        art.review_path is not None
        and not inside_game_dir(art.review_path, root))
    _ok('game/ 里的 .rpy 清单没变',
        sorted(p.name for p in game.glob('*.rpy')) == before)

    # 历史项目里已经躺着的副本必须免疫
    (game / 'ch01.blocked.rpy').write_text(
        "label ch01:\n    '坏产物'\n", encoding='utf-8')
    occupied = occupied_labels(root)
    danger = labels_in_danger(target, root, exclude={target})
    print('  occupied_labels ->', sorted(occupied))
    print('  labels_in_danger(ch01.rpy) ->', sorted(danger))
    _ok('历史 .blocked.rpy 的 label ch01 不算"用户占用"',
        'ch01' not in occupied and 'ch01' not in danger)
    _ok('用户自己的 script.rpy 仍然被算进来', 'start' in occupied)
    _ok('discover_scripts 排除派生文件',
        discover_scripts(root) == [game / 'script.rpy'])


def _roundtrip(src: str):
    """源文本 → 拍 → 渲染回去。返回 (正文, 拍列表)。"""
    import tempfile

    from storygraph.core.vocabulary import GENERATOR_MARKER
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy

    tmp = Path(tempfile.mkdtemp(prefix='sg-probe-rt-')) / 'ch01.rpy'
    tmp.write_text(src, encoding='utf-8')
    res = ParseResultBuilder().build(RenPyParser().parse(str(tmp)))
    beats = beats_from_parse_result(res, label='ch01')
    out = render_beats_to_rpy(beats)
    body = out[len(GENERATOR_MARKER):] if out.startswith(GENERATOR_MARKER) else out
    return body.lstrip('\n'), beats


def probe_return() -> None:
    """清单 §九：return 从"两个错误抵消"变成"逐字保真"。"""
    _hr('§九 return 的往返（已修）')
    cases = {
        '裸 return': ('label ch01:\n    "一句台词"\n    return\n',
                      '    "一句台词"\n    return\n'),
        'return 带值': ('label ch01:\n    "一句台词"\n    return 42\n',
                        '    "一句台词"\n    return 42\n'),
        'return 后面还有台词': ('label ch01:\n    "第一句"\n    return\n    "第二句"\n',
                               '    "第一句"\n    return\n    "第二句"\n'),
        '没有 return 的 label': ('label ch01:\n    "一句台词"\n',
                                 '    "一句台词"\n    return\n'),
    }
    for name, (src, expected) in cases.items():
        out, beats = _roundtrip(src)
        body = out.split('label ch01:', 1)[1].lstrip('\n')
        print(f'--- {name} ---')
        print('  产物:', repr(body))
        print('  拍:', [(b.kind, b.raw) for b in beats])
        _ok(f'{name} 往返正确', body == expected)
    _ok('return 带值只出现一次', _roundtrip(cases['return 带值'][0])[0]
        .count('return') == 1)


def probe_indent() -> None:
    """清单 §十：假 `indent` 参数已删。"""
    _hr('§十 render_beat_to_rpy(indent=...)（已修）')
    from storygraph.story.beat_model import Beat
    from storygraph.story.beat_render import render_beat_to_rpy

    params = list(inspect.signature(render_beat_to_rpy).parameters)
    print('  参数:', params)
    _ok('签名里不再有 indent（假旋钮被删掉）', 'indent' not in params)
    beat = Beat(label='ch01', text='一句台词')
    print('  输出:', render_beat_to_rpy(beat))
    _ok('输出仍然正确', render_beat_to_rpy(beat) == ['"一句台词"'])


def probe_indent_of() -> None:
    """清单 §十四：四份 `_indent_of` 收口到 `core.indent`。"""
    _hr('§十四 缩进测量（已修）')
    from storygraph.core.indent import indent_prefix, indent_width
    from storygraph.parsing.renpy_parser import indent_width as parser_width
    from storygraph.story.patcher import indent_prefix as patch_prefix
    from storygraph.story.python_lift import indent_width as lift_width
    from storygraph.assets.atl_block import indent_width as atl_width

    print(f'{"样本":20} {"atl":>6} {"parser":>8} {"lift":>6} {"patcher 前缀":>16}')
    for s in ('    show ling', '\tshow ling', '        x', ''):
        print(f'{s!r:20} {atl_width(s):>6} {parser_width(s):>8} '
              f'{lift_width(s):>6} {patch_prefix(s)!r:>16}')
    _ok('四份实现对 tab 的结论一致（tab=4）',
        atl_width('\tx') == parser_width('\tx') == lift_width('\tx') == 4)
    _ok('patcher 的返回类型明确是"前缀字符串"',
        patch_prefix('\tx') == indent_prefix('\tx') == '\t')
    _ok('宽度与前缀长度是两件事',
        indent_width('\tx') == 4 and len(indent_prefix('\tx')) == 1)


def probe_cols() -> None:
    """清单 §十三：「列说明」表由 COLUMN_SPECS 生成。"""
    _hr('§十三「列说明」表 vs 列定义真源（已修）')
    import openpyxl

    from storygraph.story.beat_fields import COLUMN_SPECS
    from storygraph.story.excel_export import export_beats_to_excel

    out = Path(tempfile.mkdtemp(prefix='sg-probe-cols-')) / 'x.xlsx'
    export_beats_to_excel([], out)
    ws = openpyxl.load_workbook(out)['列说明']
    labels = [str(row[0].value or '') for row in ws.iter_rows(min_row=2)]
    print('  列定义真源 COLUMN_SPECS:', len(COLUMN_SPECS), '列')
    print('  「列说明」表:', len(labels), '行')
    missing = [s.name for s in COLUMN_SPECS
               if not any(s.name in label and s.header in label
                          for label in labels)]
    print('  缺行:', missing or '（无）')
    _ok('行数 == 列数', len(labels) == len(COLUMN_SPECS))
    _ok('每一列都有说明行', not missing)
    _ok('结构真源列在表里', any('Nest' in x for x in labels))


def probe_crosslabel() -> None:
    """清单 §十六：跨 label 搬之后，重发号看 `beat.label`。"""
    _hr('§十六 跨 label 搬之后的身份（已修）')
    from storygraph.story.line_identity import parse, unique_ids

    class _B:
        def __init__(self, lid, label):
            self.line_id, self.label, self.seq = lid, label, 0

    beats = [_B('ch01.0003', 'ch02'), _B('ch01.0003', 'ch02')]
    print('  搬过去之后:', [(b.label, b.line_id) for b in beats])
    unique_ids(beats)
    print('  重发号之后:', [(b.label, b.line_id) for b in beats])
    _ok('ID 前缀是历史来源，不反推当前章节',
        beats[1].line_id.startswith('ch02.'))
    _ok('所有 ID 都能被 parse 认出',
        all(parse(b.line_id) for b in beats))


_PROBES = {'blocked': probe_blocked, 'label': probe_label,
           'ids': probe_ids, 'facts': probe_facts,
           'return': probe_return, 'indent': probe_indent,
           'indentof': probe_indent_of, 'cols': probe_cols,
           'crosslabel': probe_crosslabel}


def main(argv: list) -> int:
    names = argv or list(_PROBES)
    for name in names:
        fn = _PROBES.get(name)
        if fn is None:
            print(f'不认识的探针：{name}（可选：{", ".join(_PROBES)}）')
            return 2
        fn()
    print('\n全部探针通过（这些都是"已修"的正向断言）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
