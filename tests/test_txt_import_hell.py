"""
纯文字导入 · 地狱级判据（S2）
==============================

用**真机 5 章定稿**（1372 行）+ 一批对抗性合成边界来钉住四件事：

  ① **静默丢行 = 0**：原文每个非空行，要么落成台词/旁白，要么落成注释，
     要么被认成章节头 —— **不许有"没产出也没报"的行**；
  ② **往返逐字节一致**：产物 → 喂给我们**自己的解析器** → `render_beats_to_rpy`
     → 与产物**完全相同**。这条比"人眼看"强得多（转换器没有真值参照，
     但"产物与我们自己的渲染器一致"是可判定的），而且它一过，
     增量补丁 / 幂等审计 / 各处金标就都能直接作用在产物上；
  ③ **确定性**：同输入两次逐字节相同（无时间戳/路径/随机）；
  ④ **认不出必须报**：未映射角色、没产出的行 —— 都要在 `warnings` 里出现。

真机料在 `D:\\千连万理\\txt--renpy脚本转换工具`（不在则 skip）。
"""

from __future__ import annotations

import glob
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.txt_import import (          # noqa: E402
    CharacterMap, convert_file, convert_text, read_text_file)
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.pipeline.build import load_asset_registry  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402

TXT_DIR = Path(r'D:\千连万理\txt--renpy脚本转换工具')
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
HAS_TXT = TXT_DIR.is_dir()
HAS_REAL = REAL.is_dir()


def _chapters():
    return sorted(glob.glob(str(TXT_DIR / '*-定稿.txt')))


def _chars():
    return CharacterMap((load_asset_registry(REAL) or {}).characters)


def _roundtrip(rpy_text: str) -> str:
    """产物 → 我们自己的解析器 → 渲染（喂给临时文件，避免污染真机）。"""
    with tempfile.TemporaryDirectory(prefix='txt_hell_') as td:
        p = Path(td) / 'a.rpy'
        p.write_text(rpy_text, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[p], graph=None)
        return render_beats_to_rpy(beats_from_parse_result(pr))


# ==================== 真机 5 章 ====================

@pytest.mark.skipif(not HAS_TXT, reason='定稿 txt 不在本机')
def test_the_five_chapters_do_not_silently_drop_any_line():
    chars = _chars() if HAS_REAL else None
    for f in _chapters():
        r = convert_file(f, characters=chars)
        assert r.dropped == [], f'{Path(f).name} 静默丢了行：{r.dropped[:3]}'


@pytest.mark.skipif(not HAS_TXT, reason='定稿 txt 不在本机')
def test_the_five_chapters_roundtrip_byte_for_byte():
    """产物 == 我们自己的渲染器渲染回来的东西（逐字节）。"""
    chars = _chars() if HAS_REAL else None
    for f in _chapters():
        r = convert_file(f, characters=chars)
        back = _roundtrip(r.rpy_text)
        assert back == r.rpy_text, f'{Path(f).name} 往返不一致'


@pytest.mark.skipif(not HAS_TXT, reason='定稿 txt 不在本机')
def test_the_five_chapters_are_deterministic():
    chars = _chars() if HAS_REAL else None
    for f in _chapters():
        a = convert_file(f, characters=chars).rpy_text
        b = convert_file(f, characters=chars).rpy_text
        assert a == b, f'{Path(f).name} 两次产物不同（有随机/时间/路径混进来了）'


@pytest.mark.skipif(not HAS_TXT, reason='定稿 txt 不在本机')
def test_every_nonblank_line_lands_somewhere():
    """
    行数守恒（真机版）：非空 = 台词 + 旁白 + 章节头 + 注释行数（含一行多注释）。
    注释是"落在产物里"的另一种形式，所以按**注释条数**对账。
    """
    chars = _chars() if HAS_REAL else None
    for f in _chapters():
        text = read_text_file(f)
        r = convert_file(f, characters=chars)
        nonblank = len([x for x in text.replace('\r\n', '\n').split('\n')
                        if x.strip()])
        landed = (r.counts['say'] + r.counts['narration'] + r.counts['header']
                  + r.counts['comment'])
        assert nonblank <= landed, f'{Path(f).name}：非空 {nonblank} > 落地 {landed}'


@pytest.mark.skipif(not HAS_TXT, reason='定稿 txt 不在本机')
def test_unmapped_speakers_are_reported_not_swallowed():
    """真实章节里确实有表里没有的缩写（MOM/LINGDAO/…）——**必须报出来**。"""
    if not HAS_REAL:
        pytest.skip('角色表来自真机项目')
    chars = _chars()
    reported = set()
    for f in _chapters():
        r = convert_file(f, characters=chars)
        for token in r.unmapped:
            assert any(token in w for w in r.warnings), token
            reported.add(token)
    assert reported, '真机章节里应当有未映射的角色（否则判据没测到东西）'


# ==================== 合成边界 ====================

EDGE_CASES = [
    ('空文件', ''),
    ('只有空白', '   \n\t\n'),
    ('只有括号', '（bg1.1）\n(bg2.1)'),
    ('交错括号', '（外层(内层)还是外层）后面还有字'),
    ('引号套引号', 'JC"他说\\"行\\"了"'),
    ('书名号不该被当注释', '我学了【工科数学分析基础】、【线性代数】。'),
    ('冒号在旁白里', '他说：今天天气不错'),
    ('无末尾换行', 'JC"最后一句"'),
    ('超长行', 'JC"' + '啊' * 3000 + '"'),
    ('全角半角混排', 'JC"混合:半角,(半角括),(全角括)结束"'),
    ('章节头出现在中间', 'JC"一"\n1.0 chapter0-0 名字\nJC"二"'),
    ('只有章节头', '1.0 chapter0-0 初入大学'),
    ('反斜杠与美元', 'JC"路径 C:\\a\\b 与 $var"'),
    ('空格与制表符', '  JC"前面有空格"\t'),
]


@pytest.mark.parametrize('name,text', EDGE_CASES,
                         ids=[c[0] for c in EDGE_CASES])
def test_edge_cases_convert_and_roundtrip(name, text):
    r = convert_text(text, characters={'jc': {'ID': 'jc', 'Name': '「蒋忱」'}})
    assert r.dropped == [], f'{name}：丢了行 {r.dropped}'
    assert _roundtrip(r.rpy_text) == r.rpy_text, f'{name}：往返不一致'


@pytest.mark.parametrize('name,text', EDGE_CASES,
                         ids=[c[0] for c in EDGE_CASES])
def test_edge_cases_are_deterministic(name, text):
    a = convert_text(text).rpy_text
    b = convert_text(text).rpy_text
    assert a == b, name


def test_nested_brackets_keep_the_outer_text_only():
    """交错括号：正文不许被删错（原版按偏移量递增删，重叠就错位）。"""
    r = convert_text('（外层(内层)还是外层）后面还有字')
    assert '    # 外层(内层)还是外层' in r.rpy_text
    assert '    "后面还有字"' in r.rpy_text


def test_book_title_brackets_are_not_treated_as_comments():
    """`【…】` 是书名，不是演出注释 —— 不许被吃掉。"""
    r = convert_text('我学了【线性代数】。')
    assert '    "我学了【线性代数】。"' in r.rpy_text
    assert '#' not in r.rpy_text.split('label')[1]


def test_chapter_header_in_the_middle_is_not_a_header():
    r = convert_text('JC"一"\n1.0 chapter0-0 名字\nJC"二"')
    assert r.counts['header'] == 0            # 正文里的不算章节头
    assert '1.0 chapter0-0 名字' in r.rpy_text  # 也不会被丢掉


def test_label_is_not_a_python_keyword_or_digit_leading(tmp_path):
    r = convert_text('JC"一"', filename='123 abc!.txt')
    assert not r.label[0].isdigit()
    assert '!' not in r.label
