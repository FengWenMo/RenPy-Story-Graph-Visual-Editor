"""
纯文字导入 · 对抗性判据（S2+，2026-10-10 探针驱动）
====================================================

这个文件的每一条都来自**实测过的**对抗性输入（不是想出来的边界）：

  **A. 大块代码**：把一整段写好的 `.rpy` 当纯文字粘进来 —— 我们逐行转成旁白，
     零告警（`menu:`/`jump`/`show` 全变台词）→ 现在必须**提示**这是脚本。
  **B. `Ljk` 与 `ljk` 是两个不同的人**：归一化会静默把两个人合并成一个人，
     产物里**看不出**哪行错（说话人换人 = 改剧情）→ 现在精确名优先、
     归一撞车必须报、**报不出确定答案就不许映射**。
  **C. 未映射角色 = 产物里一个未定义变量**：`MOM"吃饭了"` → `MOM "吃饭了"`
     → Ren'Py 在这一句 `NameError`（**真机 5 章里就有 23 处**）→ 必须显著报。
  **D. 生僻资料**：全角拉丁 / 西里尔 / 零宽空格 / 组合符 / RTL 覆盖符 ——
     不许抛异常、不许静默丢行、往返必须逐字节。
  **E. 编码**：UTF-16（带/不带 BOM）**不许**被"成功"读成 GBK 乱码。
  **F. 候选污染**：真机里有单字母角色 `l`（「蓝」）→ `LINGDAO` 的"相近角色"
     曾是 `l`（误导）→ 单字符键不许参与候选。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.txt_import import (                    # noqa: E402
    Ambiguity, CharacterMap, convert_text, read_text_file)
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result       # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy          # noqa: E402

MATERIAL = Path(__file__).resolve().parents[1] / 'tests' / 'data' / \
    'txt_hell_material.txt'

CHARS = {
    'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
    'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
    'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'},
}


def _roundtrip(rpy_text: str) -> str:
    with tempfile.TemporaryDirectory(prefix='txt_adv_') as td:
        p = Path(td) / 'a.rpy'
        p.write_text(rpy_text, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[p], graph=None)
        return render_beats_to_rpy(beats_from_parse_result(pr))


def _body(r) -> list:
    return [ln for ln in r.rpy_text.splitlines()
            if ln.strip() and not ln.startswith('label')]


# ==================== A. 大块代码 ====================

CODE_BLOCK = '''label start:
    scene bg room
    show lyj happy at left
    $ flag = True
    if flag:
        lyj "我用代码说话"
    menu:
        "选项一":
            jump end
        "选项二":
            return
    python:
        for i in range(3):
            print(i)
    return
'''


def test_a_whole_rpy_pasted_as_prose_is_flagged():
    """
    实测：整段脚本会被逐行转成**旁白字符串**（结构全丢）却零告警。
    现在必须提示"这看起来是写好的脚本，请走导入 .rpy"。
    """
    r = convert_text(CODE_BLOCK, characters={'lyj': {'ID': 'lyj', 'Name': '「凌跃捷」'}})
    assert r.looks_like_script, '整段 .rpy 粘进来必须被识别出来'
    assert any('Ren\'Py 代码' in w for w in r.warnings)
    # 但**不改产物**：作者可能就是要在台词里演示代码
    assert '    "label start:"' in r.rpy_text
    assert '    lyj "我用代码说话"' in r.rpy_text


def test_the_code_block_product_still_roundtrips():
    """判据不能只是"报了警" —— 产物本身仍必须与我们的渲染器逐字节一致。"""
    r = convert_text(CODE_BLOCK)
    assert _roundtrip(r.rpy_text) == r.rpy_text


def test_ordinary_prose_is_not_flagged_as_code():
    text = ('1.0 chapter0-0 初入大学\n'
            'JC"你终于来了。"\n'
            '他说：今天天气不错\n'
            '（bg1.1）\n')
    r = convert_text(text, characters={'jc': {'ID': 'jc', 'Name': '「蒋忱」'}})
    assert r.looks_like_script == []
    assert not any('Ren\'Py 代码' in w for w in r.warnings)


def test_a_single_code_like_line_is_not_enough():
    """纯文字里偶尔出现 `show`/`if` 这种词不算 —— 要多行命中才提示。"""
    r = convert_text('我记得他说 if 一下就好\n而且 show 给我看看\n')
    assert r.looks_like_script == []


# ==================== B. Ljk 与 ljk 是两个不同的人 ====================

def test_case_only_collision_is_detected_at_load():
    cm = CharacterMap({'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
                       'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}})
    assert [c.kind for c in cm.conflicts] == ['case'], cm.conflicts
    assert '大小写' in cm.conflict_warnings()[0]


def test_exact_spelling_wins_over_folded_lookup():
    """作者写出来的**精确变量名**是最强信号（`Ljk` 就是 `Ljk`）。"""
    cm = CharacterMap({'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
                       'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}})
    assert cm.lookup('ljk') == ('ljk', 'variable')
    assert cm.lookup('Ljk')[0] == 'Ljk'


def test_all_caps_token_that_folds_to_a_collision_is_ambiguous():
    """`LJK` 归一后撞到**两个**变量 → 返回歧义，**不许**替人挑一个。"""
    cm = CharacterMap({'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
                       'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}})
    var, _how = cm.lookup('LJK')
    assert isinstance(var, Ambiguity), var
    assert var.candidates == ['Ljk', 'ljk']


def test_two_people_who_differ_only_in_case_are_not_merged():
    """
    ⚠ 这是本轮最贵的缺陷：旧实现把两行都写成 `ljk`（第二个人被顶掉），
    产物里看不出任何异常。现在：精确名各自落到自己身上。
    """
    cm = CharacterMap({'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
                       'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}})
    r = convert_text('ljk"我是李佳凯"\nLjk"我是陆嘉珂"', characters=cm)
    assert '    ljk "我是李佳凯"' in r.rpy_text
    assert '    Ljk "我是陆嘉珂"' in r.rpy_text
    assert any('大小写' in w for w in r.warnings)


def test_ambiguous_token_is_kept_raw_and_reported():
    cm = CharacterMap({'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
                       'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}})
    r = convert_text('LJK"我是谁"', characters=cm)
    assert '    LJK "我是谁"' in r.rpy_text       # 保留原文，不猜
    assert r.ambiguous == {'LJK': ['Ljk', 'ljk']}
    assert any('无法确定是谁' in w for w in r.warnings)
    assert not r.ok


def test_case_insensitive_matching_still_works_for_one_person():
    """`JC`/`jc`/`Jc` 是**同一个人**时照旧自动归一（用户原话："不能太死板"）。"""
    cm = CharacterMap({'jc': {'ID': 'jc', 'Name': '「蒋忱」'}})
    for token in ('JC', 'jc', 'Jc', 'JC'.lower()):
        assert cm.lookup(token)[0] == 'jc', token
        assert convert_text(f'{token}"你好"', characters=cm).rpy_text \
            .count('    jc "你好"') == 1


# ==================== C. 未映射角色 = 未定义变量 ====================

def test_unmapped_speaker_warns_about_the_runtime_crash():
    """
    `MOM"吃饭了"` → 产物 `MOM "吃饭了"`，而产物里没有 `define MOM` →
    Ren'Py 到这一句就是 `NameError`。真机 5 章里这种写法有 23 处。
    """
    r = convert_text('MOM"吃饭了"', characters=CHARS)
    assert '    MOM "吃饭了"' in r.rpy_text
    assert r.unmapped == {'MOM': 1}
    assert not r.ok
    assert any('未定义变量' in w for w in r.warnings)
    assert any('崩' in w for w in r.warnings)


def test_underscored_token_is_not_silently_mapped():
    """
    `L_J_K"…"`:旧实现走**归一化**（下划线被丢掉）→ 认成 `ljk` —— 一个
    作者根本没写过的角色（静默改剧情）。现在：表里有撞车时它是**歧义**
    （保留 `L_J_K` 原文）；表里没撞车时它是普通缩写路径（见下一条）。
    """
    cm = CharacterMap({'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
                       'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}})
    r = convert_text('L_J_K"带下划线"', characters=cm)
    assert '    L_J_K "带下划线"' in r.rpy_text
    assert r.ambiguous.get('L_J_K') == ['Ljk', 'ljk']
    assert '    ljk ' not in r.rpy_text


def test_underscored_token_without_collision_is_prose():
    """
    没有撞车时下划线 token 也不进产物当说话人（旧实现会写未定义变量）。
    ⚠ 下划线是**变量名**形状，而归一会把它丢掉 —— 探针实测它会命中别的角色。
    """
    r = convert_text('L_J_K"带下划线"', characters={'jc': CHARS['jc']})
    assert '    L_J_K ' not in r.rpy_text
    assert r.dropped == []
    assert r.unmapped == {}


def test_shape_boundary_is_explicit_and_two_sided():
    """
    ⚠ 形状判据**必然**有误判空间（`USA` 是角色还是英文词？看形状判不出来），
    所以把边界**明确写成判据**，而不是假装它不存在：

      · 认（保留作者写法 + 报未映射）：`MOM`/`TJF`/`A`/`B`/`OK` —— ≤8 且无下划线；
      · 不认（当旁白）：`L_J_K`（下划线）、`LINGDAO2X`（>8）。

    选择"认"的理由：认了会出现在未映射清单里（可修），当旁白则**丢掉说话人
    且不留痕迹**。反过来，`OK`/`USA` 这种英文词出现在中文剧本里本身就该被看见。
    """
    kept = ('MOM', 'TJF', 'A', 'B', 'OK', 'USA')
    dropped = ('L_J_K', 'LINGDAO2X')
    for token in kept:
        r = convert_text(f'{token}"内容"', characters={'jc': CHARS['jc']})
        assert '    %s ' % token in r.rpy_text, token
        assert token in r.unmapped, token
    for token in dropped:
        r = convert_text(f'{token}"内容"', characters={'jc': CHARS['jc']})
        assert '    %s ' % token not in r.rpy_text, token
        assert r.dropped == [], token


def test_single_letter_speakers_from_the_real_chapters_still_work():
    """⚠ 收窄形状判据不许把真机里真实存在的单字母角色（`A`/`B`）也误伤。"""
    r = convert_text('A"卷王——"\nB"大学生楷模——"', characters=CHARS)
    assert '    A "卷王——"' in r.rpy_text
    assert '    B "大学生楷模——"' in r.rpy_text
    assert set(r.unmapped) == {'A', 'B'}


def test_all_caps_token_the_project_actually_uses_is_still_kept():
    """真剧本里的 `MOM`/`LINGDAO` 仍按"作者写法优先"保留（并报未映射）。"""
    r = convert_text('LINGDAO"开会了"', characters=CHARS)
    assert '    LINGDAO "开会了"' in r.rpy_text
    assert r.unmapped == {'LINGDAO': 1}


def test_without_a_character_table_we_do_not_guess_speakers():
    """没有角色表时：连大写字都要当旁白（否则整章都建在未定义变量上）。"""
    r = convert_text('MOM"吃饭了"')
    assert r.unmapped == {}
    assert '    "MOM\\"吃饭了\\""' in r.rpy_text


# ==================== D. 生僻资料 ====================

WEIRD = [
    ('全角拉丁', 'ＬＪＫ"全角字母"'),
    ('西里尔', 'МОМ"西里尔字母"'),
    ('零宽空格', 'L\u200bJK"零宽空格"'),
    ('软连字符', 'L\u00adJK"软连字符"'),
    ('不换行空格', 'L\u00a0JK"不换行空格"'),
    ('组合附加符', 'L̂JK"带组合符"'),
    ('变体选择符', 'L\ufe0fJK"变体选择符"'),
    ('半角片假名', 'ﾊﾞﾌﾞ"半角片假名"'),
    ('阿拉伯-印度数字', '١٢٣"数字"'),
    ('RTL 覆盖符', 'L\u202eJK"RTL覆盖"'),
]


@pytest.mark.parametrize('name,text', WEIRD, ids=[c[0] for c in WEIRD])
def test_weird_material_converts_and_roundtrips(name, text):
    r = convert_text(text, characters=CHARS)
    assert r.dropped == [], f'{name} 丢了行'
    assert _roundtrip(r.rpy_text) == r.rpy_text, f'{name} 往返不一致'


def test_the_hell_material_file_converts_cleanly():
    """
    一份"生僻资料 + 错行"的整章（`tests/data/txt_hell_material.txt`）：
    不丢行、未映射都报出来、往返逐字节。
    """
    assert MATERIAL.exists(), MATERIAL
    r = convert_text(read_text_file(MATERIAL), characters=CHARS,
                     filename=MATERIAL.name)
    assert r.dropped == []
    assert r.label == 'chapter9_9'
    assert _roundtrip(r.rpy_text) == r.rpy_text
    # 表里没有的真剧本缩写照旧保留并报出来（`MOM`/`LINGDAO`）
    assert r.unmapped.get('MOM') == 2
    assert r.unmapped.get('LINGDAO') == 1
    # 归一化撞车的 token 进 `ambiguous`，**不**进 `unmapped`（两者的处置不同）
    assert r.ambiguous.get('LJK') == ['Ljk', 'ljk']
    assert 'LJK' not in r.unmapped


def test_a_ten_thousand_line_chapter_is_fast_and_lossless():
    """规模：1 万行不许丢行、不许变慢（旧版 0.06s）。"""
    big = '\n'.join(f'JC"第 {i} 行"（注释{i}）' for i in range(10000))
    r = convert_text(big, characters=CHARS)
    assert r.dropped == []
    assert r.counts['say'] == 10000
    assert r.counts['comment'] == 10000


# ==================== E. 编码 ====================

def test_utf16_with_bom_reads_correctly(tmp_path):
    p = tmp_path / 'a.txt'
    p.write_bytes('JC"你好"\n旁白\n'.encode('utf-16'))
    assert read_text_file(p) == 'JC"你好"\n旁白\n'


def test_utf16_without_bom_does_not_silently_become_mojibake(tmp_path):
    """
    ⚠ 旧实现：`utf-8` 失败 → `gbk` "成功" → 整章乱码 + **零告警**。
    现在宁可抛"读不出来"，也不给一份乱码。
    """
    for enc in ('utf-16-le', 'utf-16-be'):
        p = tmp_path / f'{enc}.txt'
        p.write_bytes('JC"你好"\n旁白\n'.encode(enc))
        with pytest.raises(UnicodeDecodeError) as ei:
            read_text_file(p)
        assert 'NUL' in str(ei.value) or 'UTF-16' in str(ei.value)


def test_gbk_without_bom_still_reads(tmp_path):
    p = tmp_path / 'gbk.txt'
    p.write_bytes('JC"你好"'.encode('gbk'))
    assert '你好' in read_text_file(p)


def test_binary_junk_is_reported_not_swallowed(tmp_path):
    p = tmp_path / 'x.txt'
    p.write_bytes(bytes(range(256)))
    with pytest.raises(UnicodeDecodeError):
        read_text_file(p)


# ==================== F. 候选污染 ====================

def test_single_letter_names_do_not_pollute_candidates():
    """
    真机里有单字母角色 `l`（「蓝」）：旧实现给 `LINGDAO` 的"相近角色"是 `l`
    —— 那不是提示，是**误导**（两者毫无关系）。
    """
    cm = CharacterMap({'l': {'ID': 'l', 'Name': '「蓝」'},
                       'lyj': {'ID': 'lyj', 'Name': '「凌跃捷」'}})
    assert 'l' not in cm.candidates('LINGDAO')
    assert 'l' not in cm.candidates('ld')


def test_real_prefix_candidates_are_still_offered():
    """该给的候选还得给（`MOM` 已能靠 `mm` 的别名解决，这里用长名字验证）。"""
    cm = CharacterMap({'jiaoguan': {'ID': 'jiaoguan', 'Name': '教官'},
                       'jiaxiao': {'ID': 'jiaxiao', 'Name': '驾校'}})
    assert 'jiaoguan' in cm.candidates('JIAOGUAN2')


def test_candidates_stay_deterministic():
    """候选顺序不许随字典遍历顺序漂（金标/快照全靠它）。"""
    rows = {'a1': {'ID': 'a1', 'Name': '甲'}, 'a2': {'ID': 'a2', 'Name': '乙'},
            'a3': {'ID': 'a3', 'Name': '丙'}}
    cm = CharacterMap(rows)
    assert cm.candidates('A') == cm.candidates('A')
    assert cm.candidates('A') == sorted(cm.candidates('A'))


# ==================== G. 复用与确定性 ====================

def test_reusing_a_character_map_has_no_side_effects():
    cm = CharacterMap(CHARS)
    before = (dict(cm.by_key), list(cm.conflicts))
    a = convert_text('JC"一"\nMOM"二"', characters=cm).rpy_text
    b = convert_text('JC"一"\nMOM"二"', characters=cm).rpy_text
    assert a == b
    assert (dict(cm.by_key), list(cm.conflicts)) == before
