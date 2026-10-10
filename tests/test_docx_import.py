"""
`.docx` 导入判据（S4）
=======================

用户拍板：**标准库** `zipfile` + `xml.etree`（最大限度降低依赖）。

这里用**标准库自己造 .docx**（`zipfile` 写一个最小 OOXML），所以既不依赖
python-docx、也不需要二进制测试夹具 —— 而且能把"哪些 XML 形状必须认"写清楚。

钉住的性质：

  ① 段落 → 一行；`<w:br/>` 行内换行**也算一行**（作者按 Shift+Enter 就是想换行）；
  ② **不许静默丢文字**：run 包在 `<w:hyperlink>` / 文本框（`mc:AlternateContent`）
     里的文字也要抽出来；表格单元格各自成行；
  ③ 读不出来要说**能照做的**原因（.doc / 非 zip / zip 坏了 / 不像 docx）；
  ④ 与 `.txt` **同一条链路**：`.docx` → 文本 → `convert_text`，
     所以"往返逐字节一致"这个性质自动继承。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.docx_import import (                        # noqa: E402
    docx_text, docx_text_from_bytes, is_legacy_doc)
from storygraph.authoring.txt_import import CharacterMap, convert_text  # noqa: E402

_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'


def _p(*runs: str) -> str:
    """一组 run 文本 → `<w:p>`（`\\n` 表示 `<w:br/>`）。"""
    body = []
    for run in runs:
        for i, piece in enumerate(run.split('\n')):
            if i:
                body.append('<w:r><w:br/></w:r>')
            if piece:
                body.append(f'<w:r><w:t xml:space="preserve">{piece}</w:t></w:r>')
    return '<w:p>' + ''.join(body) + '</w:p>'


def make_docx(path: Path, *paragraphs: str, extra: str = '') -> Path:
    """造一个最小 .docx（真 zip + 真 OOXML，Word 也能打开）。"""
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document xmlns:w="{_W}" xmlns:mc="{_MC}">'
           f'<w:body>{"".join(paragraphs)}{extra}</w:body></w:document>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml',
                    '<Types xmlns="http://schemas.openxmlformats.org/'
                    'package/2006/content-types"/>')
        zf.writestr('word/document.xml', doc)
        zf.writestr('word/styles.xml', '<w:styles xmlns:w="%s"/>' % _W)
    return path


# ==================== ① 段落与换行 ====================

def test_paragraphs_become_lines(tmp_path):
    fp = make_docx(tmp_path / 'a.docx',
                   _p('1.0 chapter0-0 初入大学'),
                   _p('MOM"跟妈客气啥"'),
                   _p('旁白一句'))
    assert docx_text(fp) == '1.0 chapter0-0 初入大学\nMOM"跟妈客气啥"\n旁白一句'


def test_a_run_break_is_also_a_line(tmp_path):
    """`<w:br/>`（Shift+Enter 的行内换行）= 作者就是想换行 → 也切成两行。"""
    fp = make_docx(tmp_path / 'a.docx', _p('第一句\n第二句'))
    assert docx_text(fp) == '第一句\n第二句'


def test_several_runs_in_one_paragraph_are_joined(tmp_path):
    """Word 会把一句话切成多个 run（改个词就切）—— 拼接时不许加空格/换行。"""
    fp = make_docx(tmp_path / 'a.docx', _p('他说', '：今天', '天气不错'))
    assert docx_text(fp) == '他说：今天天气不错'


def test_tabs_are_kept(tmp_path):
    fp = make_docx(tmp_path / 'a.docx',
                   '<w:p><w:r><w:t>前</w:t><w:tab/><w:t>后</w:t></w:r></w:p>')
    assert docx_text(fp) == '前\t后'


def test_leading_and_trailing_blank_paragraphs_are_trimmed(tmp_path):
    fp = make_docx(tmp_path / 'a.docx', _p(''), _p('正文'), _p(''), _p(''))
    assert docx_text(fp) == '正文'


def test_chinese_and_smart_quotes_survive(tmp_path):
    """中文引号/书名号是作者写的台词 —— 一个字符都不许改。"""
    fp = make_docx(tmp_path / 'a.docx', _p('妈妈“跟妈客气啥”【系统】你获得了物品'))
    assert docx_text(fp) == '妈妈“跟妈客气啥”【系统】你获得了物品'


# ==================== ② 不许静默丢文字 ====================

def test_text_inside_a_hyperlink_is_kept(tmp_path):
    """run 包在 `<w:hyperlink>` 里（只认直接子节点会静默丢掉这句话）。"""
    fp = make_docx(
        tmp_path / 'a.docx',
        f'<w:p><w:hyperlink r:id="r1" xmlns:r="http://schemas.openxmlformats'
        f'.org/officeDocument/2006/relationships">'
        f'<w:r><w:t>链接里的台词</w:t></w:r></w:hyperlink></w:p>')
    assert docx_text(fp) == '链接里的台词'


def test_text_inside_a_textbox_is_kept(tmp_path):
    """文本框（`mc:AlternateContent`）里的文字也是作者写的，不能丢。"""
    fp = make_docx(
        tmp_path / 'a.docx',
        f'<w:p><mc:AlternateContent xmlns:mc="{_MC}"><mc:Fallback>'
        f'<w:r><w:t>文本框里的话</w:t></w:r></mc:Fallback>'
        f'</mc:AlternateContent></w:p>')
    assert docx_text(fp) == '文本框里的话'


def test_table_cells_each_become_a_line(tmp_path):
    """表格单元格里的 `<w:p>` 本来就在 body 里 → 自然各成一行，不许被吞。"""
    table = (f'<w:tbl><w:tr>'
             f'<w:tc>{_p("甲")}</w:tc><w:tc>{_p("乙")}</w:tc>'
             f'</w:tr><w:tr><w:tc>{_p("丙")}</w:tc><w:tc>{_p("丁")}</w:tc>'
             f'</w:tr></w:tbl>')
    fp = make_docx(tmp_path / 'a.docx', _p('表格前'), extra=table)
    assert docx_text(fp).splitlines() == ['表格前', '甲', '乙', '丙', '丁']


# ==================== ③ 读不出来要说清原因 ====================

def test_a_legacy_doc_says_what_to_do(tmp_path):
    fp = tmp_path / 'old.doc'
    fp.write_bytes(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 64)
    assert is_legacy_doc(fp)
    with pytest.raises(ValueError) as ei:
        docx_text(fp)
    assert '另存为' in str(ei.value), ei.value


def test_a_non_zip_file_says_it_is_not_docx(tmp_path):
    fp = tmp_path / 'a.docx'
    fp.write_bytes('{\\rtf1 这是 rtf}'.encode('utf-8'))
    with pytest.raises(ValueError) as ei:
        docx_text(fp)
    assert 'PK' in str(ei.value)


def test_a_broken_zip_says_the_zip_is_broken(tmp_path):
    fp = tmp_path / 'a.docx'
    fp.write_bytes(b'PK\x03\x04' + b'\x00' * 32)
    with pytest.raises(ValueError) as ei:
        docx_text(fp)
    assert 'zip' in str(ei.value).lower()


def test_a_zip_without_word_dir_says_it_is_not_docx(tmp_path):
    fp = tmp_path / 'a.docx'
    with zipfile.ZipFile(fp, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')       # 像 epub/odt
    with pytest.raises(ValueError) as ei:
        docx_text(fp)
    assert 'word/' in str(ei.value)


def test_a_docx_without_document_xml_says_which_member_is_missing(tmp_path):
    fp = tmp_path / 'a.docx'
    with zipfile.ZipFile(fp, 'w') as zf:
        zf.writestr('word/styles.xml', '<w:styles xmlns:w="%s"/>' % _W)
    with pytest.raises(ValueError) as ei:
        docx_text(fp)
    assert 'document.xml' in str(ei.value)


def test_broken_xml_says_it_is_not_valid_xml(tmp_path):
    """`word/document.xml` 本身坏掉 → 明说"不是合法 XML"。"""
    fp = tmp_path / 'a.docx'
    with zipfile.ZipFile(fp, 'w') as zf:                       # 一开始就是坏的
        zf.writestr('word/document.xml', '<w:document><未闭合>')
    with pytest.raises(ValueError) as ei:
        docx_text(fp)
    assert 'XML' in str(ei.value)


def test_empty_file_is_reported(tmp_path):
    fp = tmp_path / 'a.docx'
    fp.write_bytes(b'')
    with pytest.raises(ValueError) as ei:
        docx_text(fp)
    assert '空' in str(ei.value)


# ==================== ④ 与 .txt 同一条链路 ====================

ROWS = {'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
        'mm': {'ID': 'mm', 'Name': '妈妈'}}


def test_docx_text_feeds_the_same_conversion_pipeline(tmp_path):
    """`.docx` 只产文本，后面完全复用 `convert_text` —— 判定规则一套。"""
    fp = make_docx(tmp_path / 'a.docx',
                   _p('1.0 chapter0-0 初入大学'),
                   _p('妈妈“跟妈客气啥”'),
                   _p('（bg1.1）'),
                   _p('「蒋忱」：你终于来了。'))
    r = convert_text(docx_text(fp), characters=CharacterMap(ROWS),
                     filename=fp.name)
    assert r.dropped == []
    assert r.label == 'chapter0_0'
    body = [ln.strip() for ln in r.rpy_text.splitlines()
            if ln.strip() and not ln.startswith(('#', 'label'))]
    # ⚠ 注释在它所属那一行的**上方**（`（bg1.1）` 是独立一行 → 只有注释）
    assert body[:4] == ['mm "跟妈客气啥"', '# bg1.1', 'jc "你终于来了。"',
                        'return'], body


def test_docx_product_roundtrips_byte_for_byte(tmp_path):
    """继承 S2 的性质：产物 → 我们自己的渲染器 → 逐字节一致。"""
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy

    fp = make_docx(tmp_path / 'a.docx',
                   _p('1.0 chapter0-0 初入大学'),
                   _p('妈妈“跟妈客气啥”'),
                   _p('他说：今天天气不错'))
    rpy = convert_text(docx_text(fp), characters=CharacterMap(ROWS),
                       filename=fp.name).rpy_text
    out = tmp_path / 'a.rpy'
    out.write_text(rpy, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[out], graph=None)
    assert render_beats_to_rpy(beats_from_parse_result(pr)) == rpy


def test_the_same_text_gives_the_same_product_from_txt_and_docx(tmp_path):
    """同一份文字，`.txt` 与 `.docx` 两条路必须给**逐字节相同**的产物。"""
    text = '1.0 chapter0-0 初入大学\n妈妈“跟妈客气啥”\n（bg1.1）\n'
    fp = make_docx(tmp_path / 'a.docx', *[_p(ln) for ln in text.splitlines()])
    a = convert_text(text, characters=CharacterMap(ROWS),
                     filename='a.txt').rpy_text
    b = convert_text(docx_text(fp), characters=CharacterMap(ROWS),
                     filename='a.txt').rpy_text
    assert a == b
