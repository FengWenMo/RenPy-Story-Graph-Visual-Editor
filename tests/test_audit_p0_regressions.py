"""
审计六条 P0 的回归（docs/评估_代码完成度与真实项目体检_2026-10-18.md §3）
=========================================================================

这六条的共同点：**产物过不了引擎 lint，而工具自己不会发现** ——
因为工具从来不对着引擎验产物。每一条都先有最小复现，再有这条判据。

判据刻意**不写"等于某个实现"**，而写"引擎能不能吃 / 内容有没有变"：

  · P0-1 BOM：产物第一行不能带 `\\ufeff`（那不是注释，是语法错）；
    且写盘时要按源文件风格**还原** BOM（逐字节）。
  · P0-2 菜单标题：`menu` 头**不许**带字符串（引擎：`expected ':' not found.`），
    标题必须写在块里。
  · P0-3 块内顶格注释：注释不结束块（结构不能被重排）。
  · P0-4 `call X(…)` 的参数必须留住（丢了就是运行时参数错误）。
  · P0-5 `expression` 里的 `;` 不是分隔符（撕成两条 = 语法错）。
  · P0-6 `u"…"` 不是"角色 u 说…"（引擎不认前缀式；工具至少不许改写它）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result      # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy         # noqa: E402


def _roundtrip(tmp_path, src: str, name: str = 'a.rpy') -> str:
    f = tmp_path / name
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return render_beats_to_rpy(beats_from_parse_result(pr))


# ---------------------------------------------------------------------------
#  P0-1：BOM 不许跑到正文行首；写盘按源风格还原
# ---------------------------------------------------------------------------

BOM_SRC = ('\ufeff# This file demonstrates...\n'
           'label start:\n'
           '    e "hi"\n'
           '    return\n')


def test_bom_never_lands_on_a_content_line(tmp_path):
    """产物里 `\\ufeff` 只能出现在**文件开头**（或干脆没有）。"""
    out = _roundtrip(tmp_path, BOM_SRC)
    for i, line in enumerate(out.split('\n')):
        if '\ufeff' in line:
            assert i == 0 and line.startswith('\ufeff'), (
                f'BOM 跑到第 {i + 1} 行行首了（那一行不再是注释）：{line!r}')
    assert 'e "hi"' in out


def test_source_style_roundtrip_bom_and_crlf(tmp_path):
    """写盘那一层必须按源文件的 BOM / 换行**逐字节**还原。"""
    from storygraph.story.source_style import detect, read_text

    f = tmp_path / 'win.rpy'
    f.write_bytes(b'\xef\xbb\xbf# \xe4\xb8\xad\xe6\x96\x87\r\nlabel a:\r\n'
                  b'    return\r\n')
    text, style = read_text(f)
    assert not text.startswith('\ufeff'), '读的时候必须吃掉 BOM（否则标签认不出）'
    assert '\r' not in text, '读进来的文本要统一成 LF（解析器按行处理）'
    assert style.bom and style.newline == '\r\n'
    assert detect(f).describe() == 'CRLF+BOM'
    back = style.apply(text)
    assert back.encode('utf-8') == f.read_bytes(), '还原后必须逐字节相同'


# ---------------------------------------------------------------------------
#  P0-2：menu 头不许带字符串
# ---------------------------------------------------------------------------

MENU_SRC = ('label start:\n'
            '    menu:\n'
            '        "As soon as she catches my eye, I decide..."\n'
            '        "To ask her right away.":\n'
            '            jump rightaway\n'
            '    return\n')


def test_menu_caption_is_written_inside_the_block(tmp_path):
    out = _roundtrip(tmp_path, MENU_SRC)
    assert 'menu "As soon as' not in out, (
        'menu 头带字符串了 —— Ren\'Py 会报 expected \':\' not found.')
    lines = out.split('\n')
    i = next(i for i, ln in enumerate(lines) if ln.strip() == 'menu:')
    assert lines[i + 1].strip() == '"As soon as she catches my eye, I decide..."', (
        f'标题没写进块里：{lines[i:i + 3]}')


def test_menu_with_speaker_caption_keeps_the_speaker(tmp_path):
    """
    官方 `tutorial_playing.rpy` 的写法：菜单标题是**角色说的**。

    模型里菜单标题没有"谁说的"这一位 → 整行必须逐字保留，
    不许只留下后半句（那会把角色从标题里抹掉）。
    """
    src = ('label start:\n'
           '    menu:\n'
           '        e "Would you like to hear about rollback?"\n'
           '        "Yes.":\n'
           '            return\n'
           '    return\n')
    out = _roundtrip(tmp_path, src)
    assert 'e "Would you like to hear about rollback?"' in out, out


# ---------------------------------------------------------------------------
#  P0-3：块内顶格注释不许把结构拆散
# ---------------------------------------------------------------------------

COMMENT_IN_BLOCK = (
    'label start:\n'
    '    python:\n'
    '        import random\n'
    '# 顶格注释\n'
    '        def f():\n'
    '            return 1\n'
    '    e "after"\n'
)


def test_top_level_comment_inside_a_block_keeps_structure(tmp_path):
    out = _roundtrip(tmp_path, COMMENT_IN_BLOCK)
    lines = out.split('\n')
    # `label start:` 必须是**第一个**非注释/非空行（不许被挪到文件末尾）
    body = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith('#')]
    assert body[0].strip() == 'label start:', (
        f'label 被挪走了（结构被重排）：{lines[:8]}')
    # `def f():` 必须在 python 块里（缩进 > 该块头的缩进）
    label_i = next(i for i, ln in enumerate(lines) if ln.strip() == 'label start:')
    py_i = next(i for i, ln in enumerate(lines) if ln.strip() == 'python:')
    def_i = next(i for i, ln in enumerate(lines) if ln.strip() == 'def f():')
    assert py_i > label_i and def_i > py_i, 'def f() 跑到块外了'
    # 注释留在块内
    assert any('顶格注释' in ln for ln in lines[py_i:def_i]), (
        '那条注释没留在块里')


# ---------------------------------------------------------------------------
#  P0-4：call 的参数
# ---------------------------------------------------------------------------

CALL_SRC = ('label start:\n'
            '    call foo(1, two=True) from _call_foo\n'
            '    return\n'
            '\n'
            'label foo(a, two=False):\n'
            '    e "x"\n'
            '    return\n')


def test_call_arguments_survive_roundtrip(tmp_path):
    out = _roundtrip(tmp_path, CALL_SRC)
    assert 'call foo(1, two=True) from _call_foo' in out, out


# ---------------------------------------------------------------------------
#  P0-5：`;` 只在引号/括号外分隔
# ---------------------------------------------------------------------------

SHOW_SEMICOLON = ('label start:\n'
                  '    show expression holo("a;b")\n'
                  '    return\n')


def test_semicolon_inside_expression_is_not_a_separator(tmp_path):
    out = _roundtrip(tmp_path, SHOW_SEMICOLON)
    assert 'show expression holo("a;b")' in out, out
    assert out.count('show ') == 1, f'一条 show 被撕成了多条：\n{out}'


def test_split_stage_items_quotes_and_brackets():
    from storygraph.core.vocabulary import split_stage_items
    assert split_stage_items('a; b') == ['a', 'b']
    assert split_stage_items('holo("a;b"); c') == ['holo("a;b")', 'c']
    assert split_stage_items('f(x=(1;2))') == ['f(x=(1;2))']
    assert split_stage_items('a;;b;') == ['a', 'b']


# ---------------------------------------------------------------------------
#  P0-6：带 Python 字符串前缀的不是台词
# ---------------------------------------------------------------------------

PREFIX_SRC = ('label start:\n'
              '    u"统一"\n'
              '    return\n')


def test_prefixed_string_is_kept_verbatim_not_turned_into_a_speaker(tmp_path):
    out = _roundtrip(tmp_path, PREFIX_SRC)
    assert 'u"统一"' in out, out
    assert 'u "统一"' not in out, (
        '被改写成了"角色 u 说统一" —— 语义被静默改了')


# ---------------------------------------------------------------------------
#  中文说话人（审计 P1-2）
# ---------------------------------------------------------------------------

def test_chinese_speaker_is_a_real_dialogue(tmp_path):
    """
    `小明 "你好"` 是合法 Ren'Py（Python 3 标识符可以是中文）。

    审计 P1-2："原文与产物一致，但解析出的语句数 = 1（少了这一句）" ——
    台词流里没有它，编辑器里就看不到这句话。
    """
    src = ('define 小明 = Character("小明")\n'
           '\n'
           'label start:\n'
           '    小明 "你好"\n'
           '    return\n')
    f = tmp_path / 'cn.rpy'
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    says = [s for s in pr.statements if s.kind == 'say']
    assert says, '中文说话人那一句没进语句流'
    assert says[0].payload.get('speaker') == '小明', says[0].payload
    assert says[0].payload.get('text') == '你好'
    assert '小明 "你好"' in _roundtrip(tmp_path, src)
