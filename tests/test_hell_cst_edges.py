"""
地狱测试 · CST 边界（2026-10-08）
================================

`core/cst.py` 是"逐字节保真成为语法树属性"的支点（D3）。已有 `test_cst.py`
钉住了主路（有区间 / 自足 / 不重不漏 / 可降级）。这一份专门**为难它**：
把"真实世界里文件长什么样"的各种脏形状摆进去 —— 编码头、行尾、缩进字符、
嵌套块、注释归属、以及一条更强的"**每行恰好一个主人**"不变量。

判据设计原则（对齐 J1/J3）：能是**不变量**就不要写成"等于某个实现细节"，
因为不变量被破坏时是真的坏了，而实现细节变了会让测试空转。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import cst                                  # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402


def _write(tmp_path: Path, name: str, text: str, encoding='utf-8') -> Path:
    f = tmp_path / name
    with open(f, 'w', encoding=encoding, newline='') as fh:
        fh.write(text)
    return f


def _parse(path: Path):
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return list(getattr(pr, 'statements', ()) or ())


# ---------------------------------------------------------------------------
#  ① 编码头：带 UTF-8 BOM 的文件**必须照常解析**
# ---------------------------------------------------------------------------
# 为什么这是 P1 而不是"洁癖"：Windows 编辑器（VS Code / 记事本"另存为 UTF-8"）
# 很容易写出带 BOM 的 .rpy。解析器按 `^label` 认标签，BOM 让第一行变成
# `\ufefflabel a:` —— 标签认不出来，整个文件**一条语句都解析不到**（静默全丢）。
# 工具随后会把这份文件当"没有内容的文件"处理，用户看到的是"导入后啥也没有"，
# 而这个"啥也没有"没有任何报错。

BOM_SRC = '\ufefflabel a:\n    "一"\n    "二"\n    return\n'


def test_bom_file_is_not_silently_invisible(tmp_path):
    f = _write(tmp_path, 'bom.rpy', BOM_SRC)
    st = _parse(f)
    assert st, '带 BOM 的 .rpy 解析出 0 条语句 —— 文件对工具不可见了（静默全丢）'
    texts = [cst.raw_text_of(s) for s in st]
    assert any('一' in t for t in texts), texts
    assert all('二' in t or '一' in t or 'return' in t for t in texts), texts


def test_bom_file_labels_are_recognized(tmp_path):
    """BOM 不能把 `label` 吃掉（判据钉在"标签语义"上，不钉实现）。"""
    f = _write(tmp_path, 'bom.rpy', BOM_SRC)
    st = _parse(f)
    # label 行本身进不进语句流随实现而定；进不进的判断标准是"有没有认错"：
    # 至少不能一条语句都没有，且不能把 `label` 当成台词。
    assert st
    for s in st:
        raw = cst.raw_text_of(s)
        assert 'label' not in raw or s.kind in ('raw', 'label'), (s.kind, raw)


# ---------------------------------------------------------------------------
#  ② 行尾：CRLF 文件内部的"真迹自洽"
# ---------------------------------------------------------------------------
# 产品侧读源文件走文本模式（通用换行 → `\n`），所以 `raw_text` 里是 `\n`。
# 判据要求的是**同一读法下的自洽**：用产品读法读出来的原文去核对，不许 mismatch。
# （"写回会不会把 CRLF 变 LF" 是另一条独立的保真缺口，单独记，不在这里混测。）

CRLF_SRC = 'label a:\r\n    "一"\r\n    "二"\r\n    return\r\n'


def test_crlf_source_is_self_consistent(tmp_path):
    f = _write(tmp_path, 'crlf.rpy', CRLF_SRC)
    st = _parse(f)
    assert st
    # 用产品读法（text mode，通用换行）读原文
    src = f.read_text(encoding='utf-8')
    stats = cst.cst_coverage(st, src)
    assert stats['mismatch'] == 0, (
        f'CRLF 文件的真迹在"同一读法的原文"里找不到：{stats}')
    assert cst.overlaps(st) == [], cst.overlaps(st)


def test_crlf_line_content_has_no_stray_carriage_return(tmp_path):
    """`raw_text` 里不许夹着 `\r`（夹了的话写回时会写进产物）。"""
    f = _write(tmp_path, 'crlf.rpy', CRLF_SRC)
    st = _parse(f)
    for s in st:
        assert '\r' not in cst.raw_text_of(s), repr(cst.raw_text_of(s))


# ---------------------------------------------------------------------------
#  ③ 缩进字符：Tab 缩进的文件要逐字保真
# ---------------------------------------------------------------------------

TAB_SRC = 'label a:\n\t"一"\n\tif x:\n\t\t"二"\n\treturn\n'


def test_tab_indented_file_is_verbatim(tmp_path):
    f = _write(tmp_path, 'tabs.rpy', TAB_SRC)
    st = _parse(f)
    assert st
    src = f.read_text(encoding='utf-8')
    stats = cst.cst_coverage(st, src)
    assert stats['mismatch'] == 0, stats
    assert cst.overlaps(st) == []
    lang = [cst.raw_text_of(s) for s in st if '一' in cst.raw_text_of(s)]
    assert lang and lang[0].startswith('\t'), repr(lang)


def test_tab_indent_is_recorded_in_trivia(tmp_path):
    f = _write(tmp_path, 'tabs.rpy', TAB_SRC)
    st = _parse(f)
    say = next(s for s in st if '一' in cst.raw_text_of(s))
    tr = cst.trivia_of(say)
    assert tr is not None
    assert tr.indent == '\t', repr(tr.indent)


# ---------------------------------------------------------------------------
#  ④ 末行无换行 / 大量空行：span 不许越界
# ---------------------------------------------------------------------------

def test_file_without_trailing_newline(tmp_path):
    f = _write(tmp_path, 'notrail.rpy', 'label a:\n    "一"\n    "二"')
    st = _parse(f)
    assert st
    src = f.read_text(encoding='utf-8')
    assert cst.cst_coverage(st, src)['mismatch'] == 0
    assert cst.overlaps(st) == []


def test_many_blank_lines_do_not_break_spans(tmp_path):
    src = ('label a:\n    "一"\n\n\n\n    "二"\n\n    return\n\n\n')
    f = _write(tmp_path, 'blank.rpy', src)
    st = _parse(f)
    assert st
    assert cst.cst_coverage(st, f.read_text(encoding='utf-8'))['mismatch'] == 0
    assert cst.overlaps(st) == []


def test_blank_before_counts_the_gap(tmp_path):
    src = 'label a:\n    "一"\n\n\n\n    "二"\n    return\n'
    f = _write(tmp_path, 'blank.rpy', src)
    st = _parse(f)
    two = next(s for s in st if '二' in cst.raw_text_of(s))
    tr = cst.trivia_of(two)
    assert tr is not None and tr.blank_before == 3, tr


# ---------------------------------------------------------------------------
#  ⑤ 不重不漏的**强判据**：label 内每个非空物理行恰好被一条 span 覆盖
# ---------------------------------------------------------------------------
# `overlaps()` 只抓"重"，抓不到"漏"。漏 = 某行没有任何语句认领 = 写回时
# 那一行会**消失**。这里把 label 内（缩进 > 0）的非空行逐行数主人。

NASTY = (
    '# 顶层注释\n'
    'label a:\n'
    '    "开场"\n'
    '    # 块内注释\n'
    '    if x:\n'
    '        "分支一"\n'
    '    menu:\n'
    '        "选项甲":\n'
    '            "甲的内容"\n'
    '        "选项乙":\n'
    '            python:\n'
    '                y = 1  # 行尾注释\n'
    '                # 独立注释\n'
    '    $ z = (1 +\n'
    '          2)\n'
    '    return\n'
    '# 文件尾注释\n'
)


def _owner_counts(statements, nlines):
    counts = [0] * (nlines + 1)
    for s in statements:
        sp = cst.span_of(s)
        if sp is None or not sp.ok:
            continue
        for L in range(sp.start, min(sp.end, nlines) + 1):
            counts[L] += 1
    return counts


def test_every_label_inner_line_has_exactly_one_owner(tmp_path):
    f = _write(tmp_path, 'nasty.rpy', NASTY)
    st = _parse(f)
    assert st
    lines = NASTY.split('\n')
    counts = _owner_counts(st, len(lines))
    naked = []
    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if line[:len(line) - len(line.lstrip(' \t'))] == '':   # 顶格行不归 label 内语句
            continue
        if counts[i] != 1:
            naked.append((i, counts[i], line))
    assert not naked, f'这些 label 内的行没有被恰好一条 span 覆盖：{naked}'


def test_nasty_file_verbatim_and_no_overlap(tmp_path):
    f = _write(tmp_path, 'nasty.rpy', NASTY)
    st = _parse(f)
    assert cst.overlaps(st) == [], cst.overlaps(st)
    assert cst.cst_coverage(st, NASTY)['mismatch'] == 0
    # 连续行的续行（括号）必须整条带缩进保真
    joined = '\n'.join(cst.raw_text_of(s) for s in st)
    assert '$ z = (1 +' in joined, joined
    assert '          2)' in joined, joined


# ---------------------------------------------------------------------------
#  ⑥ 自足：把源文件删掉，CST 真迹仍在（含自定义块 / 嵌套块）
# ---------------------------------------------------------------------------

FULL = (
    'label a:\n'
    '    mycue "闪电" intensity=3:\n'
    '        python:\n'
    '            for i in range(3):\n'
    '                game.flag = i\n'
    '        "块里的台词"\n'
    '    l oh "3"\n'
    '    return\n'
)


def test_cst_is_self_sufficient_for_nested_custom_block(tmp_path):
    f = _write(tmp_path, 'full.rpy', FULL)
    st = _parse(f)
    assert st
    expect = {cst.span_of(s).start: cst.raw_text_of(s) for s in st}
    f.unlink()
    got = {cst.span_of(s).start: cst.statement_text(s) for s in st}
    assert got == expect
    assert any('game.flag = i' in v for v in got.values()), got


def test_cst_does_not_change_fingerprint_on_real_parse(tmp_path):
    """挂上 CST 的语句与"同内容的干净孪生体"必须相等同哈希（协作主键契约）。"""
    f = _write(tmp_path, 'full.rpy', FULL)
    st = _parse(f)
    assert st
    for s in st:
        twin = type(s)(kind=s.kind, payload=dict(s.payload),
                       condition=s.condition, source=s.source)
        assert not cst.has_cst(twin)
        assert s == twin and hash(s) == hash(twin), s.kind


# ---------------------------------------------------------------------------
#  ⑦ 判据本身不许空转：mismatch 必须能被造出来
# ---------------------------------------------------------------------------

def test_mismatch_detector_is_not_vacuous(tmp_path):
    f = _write(tmp_path, 'a.rpy', 'label a:\n    "这是一句真实的台词"\n    return\n')
    st = _parse(f)
    assert st
    stats = cst.cst_coverage(st, 'label q:\n    "完全不相干的另一份"\n')
    assert stats['mismatch'] > 0, '拿别的原文核对居然全对 —— 判据空转'
    assert stats['mismatch'] <= stats['with_raw']


# ---------------------------------------------------------------------------
#  ⑧ 真机（换机 skip）：脏形状全过 + 不重不漏
# ---------------------------------------------------------------------------

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


# ---------------------------------------------------------------------------
#  ⑨ CST 收口：`statement_text` / `overlaps` 要有**真实消费者**（2026-10-08）
# ---------------------------------------------------------------------------
# 缺口清单第 1 条：CST 是"验证层"，但 `statement_text()` 生产零调用、
# `overlaps()` 只在单测里跑。现在 `describe_statements` 的行里带**原文样例**
# （调 `statement_text`），构建报告里加了一条 **span 重叠** 门禁（调
# `overlaps`）。这一节把"有消费者"钉成判据。

def test_describe_exposes_the_verbatim_sample(tmp_path):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'a.rpy').write_text(
        'label a:\n'
        '    mycue "flash" intensity=3:\n'
        '        target = ling\n'
        '    return\n', encoding='utf-8')
    from storygraph.story.statement_registry import (clear_cache,
                                                     describe_statements)
    clear_cache()
    rows = describe_statements(project_root=root)['statements']
    row = next(r for r in rows if r['name'] == 'mycue')
    assert row.get('sample'), 'describe 没带原文样例 —— statement_text 又悬空了'
    assert 'mycue' in row['sample'], row['sample']


def test_overlaps_accessor_really_detects_overlap():
    """`overlaps()` 必须真能报重叠 —— 构建报告那条门禁靠它。"""
    class N:
        pass

    a, b = N(), N()
    cst.attach(a, cst.Span(file='x.rpy', start=1, end=5), 'aaa')
    cst.attach(b, cst.Span(file='x.rpy', start=4, end=8), 'bbb')
    assert cst.overlaps([a, b]), '重叠 span 没被 overlaps 认出来'
    cst.attach(b, cst.Span(file='x.rpy', start=6, end=8), 'bbb')
    assert cst.overlaps([a, b]) == []
    assert cst.overlaps([a]) == []


def test_real_parse_has_no_overlap_and_gets_samples(tmp_path):
    f = _write(tmp_path, 'a.rpy', FULL)
    st = _parse(f)
    assert st
    assert cst.overlaps(st) == []
    # 每条真迹都能在原文里逐字找到（收口不改变真迹质量）
    assert cst.cst_coverage(st, FULL)['mismatch'] == 0


@pytest.mark.skipif(not REAL.is_dir(), reason='换机 skip')
def test_real_project_every_line_has_a_owner():
    """真机：逐条真迹对得上、且没有重叠 span。"""
    from storygraph.parsing.discover import discover_scripts
    az = IncrementalRenPyAnalyzer()
    total = mismatch = 0
    for f in discover_scripts(REAL):
        text = Path(f).read_text(encoding='utf-8', errors='ignore')
        pr, _ = az.analyze(files=[f], graph=None)
        st = list(getattr(pr, 'statements', ()) or ())
        if not st:
            continue
        stats = cst.cst_coverage(st, text)
        total += stats['total']
        mismatch += stats['mismatch']
        assert cst.overlaps(st) == [], (
            f'{Path(f).name} 有重叠 span：{cst.overlaps(st)[:2]}')
    assert total > 500, total
    assert mismatch == 0, f'{mismatch} 条真迹在源文件里找不到'
