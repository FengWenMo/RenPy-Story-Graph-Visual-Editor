"""
纯文字导入 · 单元判据（S1）
===========================

钉住的都是**与那版"粗糙原版"的差别**（原版 `convert_txt_to_renpy.py` 的毛病）：

  ① 括号 → 注释，放在**该行上方**（原版也做对了，保留）；
  ② ⚠ **中文旁白里带引号不许被当成角色名**（原版用"引号前的内容"判定 →
     真实剧本里 `世界上再也没有什么比"…"` 会被当成角色 `世界上再也没有什么比`）；
  ③ 角色名走**角色表**（`JC` → `jc`，大小写不敏感）；表里没有但形如
     全大写缩写 → 认，但**报未映射**（不静默）；
  ④ 未映射**不写 define 进产物**（原版会写 `define a = Character("Alice")` ——
     演示值，多章还会重复定义）；
  ⑤ 产物**确定性**：无时间戳、无绝对路径；首行是生成标记；与我们自己的渲染器
     **逐字节一致**。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.authoring.txt_import import (          # noqa: E402
    CharacterMap, classify_line, convert_text, extract_brackets, label_from,
    read_text_file)
from storygraph.core.vocabulary import GENERATOR_MARKER  # noqa: E402

#: 真机角色表的一小片（`jc`=「蒋忱」 等）—— 单元判据不依赖真机项目
CHARS = {'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
         'yhj': {'ID': 'yhj', 'Name': '「叶怀瑾」'},
         'lyj': {'ID': 'lyj', 'Name': '「凌跃捷」'},
         'l': {'ID': 'l', 'Name': '「蓝」'}}


def _cm():
    return CharacterMap(CHARS)


# ---------------- 看着像说话人的行（2026-10-11 修静默缺陷）----------------

def test_a_speaker_shaped_line_with_an_unknown_name_is_reported():
    """
    ⭐⭐ **修掉的静默缺陷**：`小明：谁啊？` 原来被**安静地**当成旁白 ——
    `unmapped / warnings / dropped / ambiguous` **全空**，用户一个字都看不到；
    而编剧写的是"小明说" ⇒ 工具悄悄改掉了作者意图。

    现在的规矩是"**只报不猜**"：这一行**照旧**是旁白（它真可能是），
    但必须出现在 `suspect_speakers` 里，并进 `warnings` 的人话摘要。
    """
    chars = dict(CHARS)
    chars['lyj'] = {'Name': '叶怀瑾'}            # 表里**没有**「小明」
    res = convert_text('lyj：好。\n小明：谁啊？\n', characters=chars)
    assert [s['token'] for s in res.suspect_speakers] == ['小明'], \
        res.suspect_speakers
    assert res.suspect_speakers[0]['no'] == 2
    assert any('看着像谁在说话' in w for w in res.warnings), res.warnings
    # ⚠ 判读**不许被改**（猜错了比不报更糟）
    kinds = {ln.raw: ln.kind for ln in res.lines}
    assert kinds['小明：谁啊？'] == 'narration'


def test_known_speakers_and_plain_colon_sentences_are_not_flagged():
    """别乱报：认识的说话人、以及**不像名字**的冒号句都不进 suspect。"""
    chars = dict(CHARS)
    chars['lyj'] = {'Name': '叶怀瑾'}
    res = convert_text('lyj：好。\n今天要说的是：明天放假。\n', characters=chars)
    assert res.suspect_speakers == [], res.suspect_speakers


# ---------------- 括号 ----------------

def test_brackets_become_comments_above_the_line():
    r = convert_text('JC"你好"（笑）', characters=_cm())
    body = [ln for ln in r.rpy_text.splitlines() if ln.strip()]
    assert body[0] == GENERATOR_MARKER
    assert body[1] == 'label chapter:'          # label 兜底（无文件名/无章节头）
    assert body[2] == '    # 笑'                # 注释在该行**上方**
    assert body[3] == '    jc "你好"'


def test_a_line_of_only_brackets_produces_no_empty_say():
    """整行只有括号 → 只出注释（空台词会触发校验 W113，而且那不是作者要的）。"""
    r = convert_text('（bg1.1）\nJC"继续"', characters=_cm())
    body = r.rpy_text.splitlines()
    assert '    # bg1.1' in body
    assert not [ln for ln in body if ln.strip() == '    ""']


def test_chinese_and_english_brackets_both_work():
    cleaned, comments = extract_brackets('大家都很高兴(切屏)（切bgm）')
    assert cleaned.strip() == '大家都很高兴'
    assert comments == ['切屏', '切bgm']


def test_nested_and_interleaved_brackets_do_not_corrupt_the_text():
    """重叠括号（中文里套英文）只当一段注释，正文不许被删错。"""
    cleaned, comments = extract_brackets('（外层(内层)还是外层）后面还有字')
    assert cleaned.strip() == '后面还有字'
    assert comments == ['外层(内层)还是外层']


# ---------------- 角色识别（对抗原版的头号缺陷） ----------------

def test_narration_with_quotes_is_not_mistaken_for_a_speaker():
    """
    ⚠ 原版在这里会错：`世界上再也没有什么比"…"更重要` → 角色名
    `世界上再也没有什么比`，产物写成 `世界上再也没有什么比 "…"`（**静默改剧情**）。
    """
    r = convert_text('世界上再也没有什么比"自由"更重要', characters=_cm())
    body = [ln for ln in r.rpy_text.splitlines() if ln.strip()]
    assert body[2] == '    "世界上再也没有什么比\\"自由\\"更重要"'
    assert r.unmapped == {}


def test_lowercase_and_uppercase_tokens_map_to_the_same_variable():
    """`JC`/`jc`/`Jc` 都映射到表里的 `jc`（用户原话："不能太死板！比如大小写问题"）。"""
    for token in ('JC', 'jc', 'Jc'):
        r = convert_text(f'{token}"你好"', characters=_cm())
        assert '    jc "你好"' in r.rpy_text, token


def test_token_is_matched_by_display_name_too():
    r = convert_text('「叶怀瑾」"你好"', characters=_cm())
    assert '    yhj "你好"' in r.rpy_text


def test_unknown_uppercase_token_is_reported_but_kept_readable():
    r = convert_text('MOM"吃饭了"', characters=_cm())
    assert '    MOM "吃饭了"' in r.rpy_text          # 产物里保留原 token（可读/可人工改）
    assert r.unmapped == {'MOM': 1}                  # 但必须**报出来**
    assert r.suggested_defines == ['define MOM = Character("MOM")']
    assert not r.ok                                  # 有未映射 → 不算干净通过
    assert any('MOM' in w for w in r.warnings)


def test_no_define_is_written_into_the_chapter_product():
    """未映射**不**写 define 进产物（多章会重复定义；显示名还是编的）。"""
    r = convert_text('MOM"吃饭了"', characters=_cm())
    assert 'define ' not in r.rpy_text


def test_colon_form_is_recognized():
    r = convert_text('jc：你好呀', characters=_cm())
    assert '    jc "你好呀"' in r.rpy_text


def test_narration_with_a_colon_is_not_a_speaker():
    """`他说：今天…` 的前半截不是角色名（原版会切错）。"""
    r = convert_text('他说：今天天气不错', characters=_cm())
    assert '    "他说：今天天气不错"' in r.rpy_text
    assert r.unmapped == {}


# ---------------- label ----------------

def test_label_prefers_the_chapter_header():
    assert label_from('1.0 chapter0-0 初入大学\n…') == 'chapter0_0'


def test_label_falls_back_to_the_filename_numbering():
    """`1-2蓝之初见-定稿.txt` → `chapter1_2`（与真机 label 命名一致）。"""
    assert label_from('没有章节头', '1-2蓝之初见-定稿.txt') == 'chapter1_2'


def test_label_last_resort_is_a_sanitized_filename():
    got = label_from('', '9 奇怪 名字!.txt')
    assert got and got[0].isalpha() and ' ' not in got and '!' not in got


# ---------------- 产物形状 ----------------

def test_the_product_is_deterministic_and_has_no_timestamp():
    text = '1.0 chapter0-0 初入大学\nJC"你好"（笑）\n旁白一句'
    a = convert_text(text, characters=_cm()).rpy_text
    b = convert_text(text, characters=_cm()).rpy_text
    assert a == b
    for bad in ('生成时间', 'timestamp', '2026-', 'D:\\', '/home/'):
        assert bad not in a


def test_first_line_is_the_generator_marker_and_last_is_return():
    r = convert_text('JC"你好"', characters=_cm())
    lines = r.rpy_text.splitlines()
    assert lines[0] == GENERATOR_MARKER
    assert lines[-1].strip() == 'return'


def test_quotes_and_backslashes_are_escaped():
    r = convert_text('JC"他说\\"行\\"了"', characters=_cm())
    assert '    jc "他说\\\\\\"行\\\\\\"了"' in r.rpy_text


def test_blank_lines_are_dropped_and_counted():
    r = convert_text('JC"一"\n\n\nJC"二"', characters=_cm())
    assert r.counts['blank'] == 2
    assert r.counts['say'] == 2


def test_every_nonblank_input_line_either_lands_or_is_reported():
    """行数守恒的单元版：不该出现"没产出也没报"的行。"""
    r = convert_text('JC"一"\n（只有括号）\n旁白', characters=_cm())
    assert r.dropped == []


# ---------------- 读文件 ----------------

def test_reads_utf8_with_bom(tmp_path):
    p = tmp_path / 'a.txt'
    p.write_bytes('\ufeffJC"你好"'.encode('utf-8'))
    assert read_text_file(p).lstrip('\ufeff') == 'JC"你好"'


def test_reads_gbk(tmp_path):
    p = tmp_path / 'a.txt'
    p.write_bytes('JC"你好"'.encode('gbk'))
    assert '你好' in read_text_file(p)


def test_crlf_and_lf_are_the_same(tmp_path):
    a = convert_text('JC"一"\r\nJC"二"', characters=_cm()).rpy_text
    b = convert_text('JC"一"\nJC"二"', characters=_cm()).rpy_text
    assert a == b


def test_classify_line_reports_header_only_for_the_first_nonblank_line():
    """章节头 = **第一个非空行**（真实剧本第一行就是它）。"""
    head = classify_line('1.0 chapter0-0 初入大学', 1, _cm(),
                         first_nonblank=True)
    assert head.kind == 'header'
    # 同样的句子出现在正文里 → 不是章节头，也不许被丢掉
    late = classify_line('1.0 chapter0-0 初入大学', 99, _cm())
    assert late.kind != 'header' and late.text
