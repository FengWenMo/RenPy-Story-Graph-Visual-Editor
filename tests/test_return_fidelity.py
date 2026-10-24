"""
tests/test_return_fidelity.py
=============================
清单 §九（P0）：`return` 的"逐字节保真"从前是两个错误互相抵消出来的。

  · `beat_build._emit_return` 收到 return 什么都不产出（三个参数一个没读）；
  · `beat_render._emit_label_groups` 在每个 label 结尾无条件补一行裸 return。

"章尾一个裸 return"这一种写法正好抵消，所以 52 条金标全绿；只要写法偏离
（`return 42` / return 后面还有台词 / call 的返回值），产物就多一行或改了
控制流。这一组用例钉的是**偏离写法**，它们以前一条都没有。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.core.vocabulary import GENERATOR_MARKER          # noqa: E402
from storygraph.parsing.renpy_parser import (                    # noqa: E402
    ParseResultBuilder, RenPyParser,
)
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy     # noqa: E402


def _roundtrip(src: str):
    """源文本 → 拍 → 渲染回去（去掉生成标记后的正文）。"""
    tmp = Path(tempfile.mkdtemp(prefix='sg-return-')) / 'ch01.rpy'
    tmp.write_text(src, encoding='utf-8')
    res = ParseResultBuilder().build(RenPyParser().parse(str(tmp)))
    beats = beats_from_parse_result(res, label='ch01')
    out = render_beats_to_rpy(beats)
    body = out[len(GENERATOR_MARKER):] if out.startswith(GENERATOR_MARKER) else out
    return body.lstrip('\n'), beats

def _body_after_label(text: str) -> str:
    """只取第一个 `label ch01:` 之后的部分（比对拍内容）。"""
    return text.split('label ch01:', 1)[1].lstrip('\n')


def test_bare_return_is_preserved_exactly():
    src = 'label ch01:\n    "一句台词"\n    return\n'
    out, beats = _roundtrip(src)
    assert _body_after_label(out) == '    "一句台词"\n    return\n'
    assert [b.kind for b in beats] == ['narration', 'flow']
    assert beats[-1].raw == 'return'


def test_return_with_value_is_not_duplicated():
    """`return 42` 以前会多出一行裸 return（返回值没被盖掉只是运气）。"""
    src = 'label ch01:\n    "一句台词"\n    return 42\n'
    out, beats = _roundtrip(src)
    assert _body_after_label(out) == '    "一句台词"\n    return 42\n'
    assert out.count('return') == 1, out
    assert beats[-1].raw == 'return 42'


def test_statements_after_return_stay_unreachable():
    """
    `return` 后面的台词是**死代码**。产物不许把 return 挪到章尾把它救活。

    这一档最严重：如果那句是 `$ gold += 100` 或 `play music "x"`，控制流
    就真的变了。
    """
    src = ('label ch01:\n'
           '    "第一句"\n'
           '    return\n'
           '    "第二句"\n')
    out, beats = _roundtrip(src)
    assert _body_after_label(out) == ('    "第一句"\n'
                                      '    return\n'
                                      '    "第二句"\n')
    assert [b.kind for b in beats] == ['narration', 'flow', 'narration']


def test_label_without_return_still_gets_a_safe_one():
    """没有 return 的 label 仍补一行（保证生成脚本有出口）——这一档不变。"""
    src = 'label ch01:\n    "一句台词"\n'
    out, _beats = _roundtrip(src)
    assert _body_after_label(out) == '    "一句台词"\n    return\n'


def test_return_value_is_not_overwritten_by_a_synthetic_return():
    """`label ch02` 结尾的 `return 7` 后不许再补裸 return（否则 call 拿到 None）。"""
    src = ('label ch01:\n'
           '    "一"\n'
           '    return\n'
           'label ch02:\n'
           '    "二"\n'
           '    return 7\n')
    out, _beats = _roundtrip(src)
    assert out.count('return') == 2, out
    assert 'return 7\n' in out
    assert out.rstrip().endswith('return 7'), out


def test_return_inside_a_menu_option_stays_inside_the_option():
    """菜单选项体里的 return 不许被挪到章尾（那会改变分支结构）。"""
    src = ('label ch01:\n'
           '    menu:\n'
           '        "A":\n'
           '            return\n'
           '        "B":\n'
           '            "x"\n')
    out, _beats = _roundtrip(src)
    body = _body_after_label(out)
    assert '        "A":\n            return\n' in body, body
    assert body.rstrip().endswith('return'), body


def test_label_ending_in_jump_does_not_get_a_dead_return():
    """`jump` 永不返回：它后面补 `return` 是死代码 + 凭空多一行。"""
    src = ('label ch01:\n'
           '    "一句"\n'
           '    jump ch02\n')
    out, _beats = _roundtrip(src)
    assert _body_after_label(out) == ('    "一句"\n'
                                      '    jump ch02\n')


def test_conditional_return_is_preserved_without_a_synthetic_one():
    """
    条件 return 也是"这一章已经有 return 拍" → 不再补章尾裸 return。

    源文件本来就没有章尾 return；补一行属于**凭空改字节**（而且条件为假时
    走到章尾本来就等价于 return）。判据与清单 §九一致：
    "已经渲染出 return 拍就不再补"。
    """
    src = ('label ch01:\n'
           '    if flag:\n'
           '        return\n'
           '    "after"\n')
    out, beats = _roundtrip(src)
    body = _body_after_label(out)
    assert body == ('    if flag:\n'
                    '        return\n'
                    '    "after"\n'), body
    assert body.count('return') == 1
    assert [b.kind for b in beats] == ['flow', 'narration']
