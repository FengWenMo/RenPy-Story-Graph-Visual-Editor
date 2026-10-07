"""
D3：无损语法树（CST）—— 原文真迹成为 IR 的**属性**
===================================================

在此之前，"逐字节保真"是一条**后门**：`RawNode` / `FileBlocks` / `# TODO` /
`Beat.opaque` —— 每来一条新需求就再加一个。Roslyn 的经验是：全保真应当是
语法树的属性 —— 每个节点带着自己的**原文区间**与**前导 trivia**，
于是"把树写回原文"是**结构上可能**的，而不是靠各条路各自小心。

判据分四类，其中 ②③ 是这个文件存在的理由（只测 ① 是自证）：

  ① **有区间**：每条语句都拿得到 `Span` 与逐字 `raw_text`；
  ② **自足**：**把源文件删掉**，`statement_text(stmt)` 仍能给出原文 ——
     这证明真迹在 IR 里，不在文件里；
  ③ **不重不漏**：同一文件里 span **不许重叠**（重叠 = 写回必然打架），
     且顶格行不许被吞进 label 内的语句；
  ④ **可降级**：没有 CST 的语句退回规范化文本，行为退回 D3 之前
     （动态属性承载的代价与收益都要钉住）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import cst
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')

SRC = (
    '# 文件头注释\n'
    '\n'
    'label a:\n'
    '    scene bg room\n'
    '\n'
    '    "第一句"\n'
    '    mycue "闪电" intensity=3:\n'
    '        target = ling\n'
    '        hold 0.5\n'
    '    "第二句"\n'
    '    return\n'
    '\n'
    'define e = Character("Eileen")\n'
)


def _parse(tmp_path, src=SRC, name='a.rpy'):
    f = tmp_path / name
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return f, pr


# ---------------------------------------------------------------------------
#  ① 有区间
# ---------------------------------------------------------------------------

def test_every_statement_gets_a_span_and_raw_text(tmp_path):
    _f, pr = _parse(tmp_path)
    assert pr.statements, '夹具没解析出语句，后面的判据全是空转'
    for st in pr.statements:
        assert cst.has_cst(st), f'{st.kind} 没有 CST：{st.source}'
        assert cst.raw_text_of(st).strip(), st.source


def test_raw_text_is_the_exact_source_slice(tmp_path):
    """`raw_text` 必须**逐字等于**源文件里那几行 —— 这是"真迹"的定义。"""
    f, pr = _parse(tmp_path)
    lines = SRC.split('\n')
    for st in pr.statements:
        sp = cst.span_of(st)
        assert sp.ok, st.source
        assert cst.raw_text_of(st) == '\n'.join(lines[sp.start - 1:sp.end]), (
            f'{st.kind} 的 raw_text 与源文件对不上：{sp.to_dict()}')


def test_the_unknown_block_keeps_its_whole_text(tmp_path):
    """自定义块的那一刻：它的原文（含块体与缩进）必须整段在 IR 里。"""
    _f, pr = _parse(tmp_path)
    raws = [cst.raw_text_of(st) for st in pr.statements]
    hit = [r for r in raws if 'mycue' in r]
    assert hit, raws
    assert 'target = ling' in hit[0], hit[0]
    assert '        target = ling' in hit[0], '块体的缩进必须原样带着'


def test_trivia_records_blank_lines_above(tmp_path):
    _f, pr = _parse(tmp_path)
    say = next(st for st in pr.statements if '第一句' in (st.payload or {})
               .get('text', ''))
    tr = cst.trivia_of(say)
    assert tr is not None and tr.blank_before >= 1, tr
    assert tr.indent == '    ', repr(tr.indent)


# ---------------------------------------------------------------------------
#  ② 自足：把源文件删掉也要拿得到原文
# ---------------------------------------------------------------------------

def test_statement_text_survives_source_deletion(tmp_path):
    """
    **这是 D3 的核心判据**：真迹在 IR 里，不在文件里。

    把源文件删掉之后仍然能逐字给出每条语句的原文 —— 就说明
    "保真"已经是语法树的属性，而不是"再回去读一遍文件"。
    """
    f, pr = _parse(tmp_path)
    expected = {cst.span_of(st).start: cst.raw_text_of(st)
                for st in pr.statements}
    assert expected, '没有语句'
    f.unlink()
    assert not f.exists()
    got = {cst.span_of(st).start: cst.statement_text(st)
           for st in pr.statements}
    assert got == expected


def test_statement_without_cst_falls_back(tmp_path):
    """没有 CST 的语句退回规范化文本（**不许抛**）—— 降级路径也要钉。"""
    class Fake:
        payload = {'text': 'mycue "x"'}

    assert cst.span_of(Fake()) is None
    assert not cst.has_cst(Fake())
    assert cst.statement_text(Fake()) == 'mycue "x"'
    assert cst.raw_text_of(Fake()) == ''
    assert cst.trivia_of(Fake()) is None


# ---------------------------------------------------------------------------
#  ②b 真迹必须切自"**解析时那一份**"文本，不是事后又读一遍盘
# ---------------------------------------------------------------------------
#
# 为什么这条要紧：`Span` 的行号来自 AST（解析时算的），而原文切片一度是
# **挂载时再读一遍盘**得来的。两次读之间只要有人改了文件（本仓库里
# "程序改代码 → 表格自动反映"就是 1.5s 轮询 + 重解析，工具与外部编辑器
# 同时开着也是常态），行号与内容就不是同一份快照 —— **真迹贴错行**：
# 写回时把 A 的内容写到 B 的位置。
#
# ⚠ 判据怎么才**不是空转**（第一版写错过）：外部在 `analyze()` **返回之后**
# 改文件，两条实现都会给出旧原文（都是"在改之前"读的），于是变异验证全绿。
# 必须让**第二次读盘**本身就返回被改过的内容 —— 用"会变心的 `read_text`"
# 直接顶掉那一次读，缺陷才真正被钉住。

NEWER = 'label z:\n    "换了一份"\n'


def _lying_read_text(monkeypatch, target, state):
    """把 `Path.read_text` 换成"第二次读同一文件就返回**新版本**"。"""
    real = Path.read_text

    def lying(self, *args, **kwargs):
        if Path(self) == target:
            state['n'] += 1
            if state['n'] > 1:
                return NEWER
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', lying)


def _assert_cst_is_the_original(pr):
    raws = [cst.raw_text_of(st) for st in pr.statements]
    assert any('第一句' in r for r in raws), raws
    assert not any('换了一份' in r for r in raws), (
        f'真迹取自**改了之后**的那一份（贴错行）：{raws}')


def test_analyze_reads_each_source_exactly_once(tmp_path, monkeypatch):
    """
    **同一份源文件在一次 `analyze` 里只读一次**（词法 / 顶层区间 / CST 共用）。

    这条是上面那条不变量**结构上的**保证：只有一次读，就不存在"两次读之间
    文件被改写"的窗口。计数判据对"又读了第二遍"是敏感的 —— 而这正是
    旧实现（三处各读一遍）的样子。
    """
    f = tmp_path / 'a.rpy'
    f.write_text(SRC, encoding='utf-8')
    state = {'n': 0}
    _lying_read_text(monkeypatch, f, state)
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    assert state['n'] >= 1, '夹具没读盘，判据空转'
    assert state['n'] == 1, f'同一份源文件被读了 {state["n"]} 次'
    _assert_cst_is_the_original(pr)


def test_cached_analyze_uses_the_cached_snapshot(tmp_path, monkeypatch):
    """
    **走缓存那一遍一次盘都不读**，真迹取自缓存里的那一份。

    缓存元组如果没带源文本，第二次就会退回"再读一遍盘"—— 那正是上一条
    堵掉的窗口，只不过这次是缓存把它重新打开的。
    """
    f = tmp_path / 'a.rpy'
    f.write_text(SRC, encoding='utf-8')
    state = {'n': 0}
    _lying_read_text(monkeypatch, f, state)
    az = IncrementalRenPyAnalyzer()
    pr1, _ = az.analyze(files=[f], graph=None)
    pr2, _ = az.analyze(files=[f], graph=None)      # 命中缓存
    assert state['n'] == 1, f'缓存那一遍又读了盘（{state["n"]} 次）'
    _assert_cst_is_the_original(pr1)
    _assert_cst_is_the_original(pr2)
    assert cst.overlaps(pr2.statements) == []


# ---------------------------------------------------------------------------
#  ②c CST 是**动态属性**：不许影响内容指纹（相等 / 哈希）
# ---------------------------------------------------------------------------

def test_cst_does_not_touch_the_content_fingerprint(tmp_path):
    """
    挂了 CST 与没挂 CST 的**同一条语句**必须仍然相等、同哈希。

    为什么这是硬契约：`Statement` 的相等/哈希是**协作主键 + 增量对位**的
    依据（`content_hash` / `LineId`）。`span` / `raw_text` 一旦进了 dataclass
    字段，就等于让"一次保真改动"变成"一次全局契约变更"—— 所有依赖相等
    语义的地方（去重、配对、快照）会一起动，而它们各自内部都自洽，
    所以**不会有测试自己变红**。这条判据就是为了让它变红。
    """
    _f, pr = _parse(tmp_path)
    assert pr.statements
    for st in pr.statements:
        twin = type(st)(kind=st.kind, payload=dict(st.payload),
                        condition=st.condition, source=st.source)
        assert not cst.has_cst(twin)          # 对照物确实没挂
        assert cst.has_cst(st)
        assert st == twin, f'{st.kind} 的相等被 CST 影响了'
        assert hash(st) == hash(twin), f'{st.kind} 的哈希被 CST 影响了'


def test_attaching_to_a_slots_object_does_not_raise():
    """挂不上去（`__slots__` 之类）**不许抛** —— 记进 safeguard 就好。"""
    class Slotted:
        __slots__ = ('payload',)

        def __init__(self):
            self.payload = {'text': 'x'}

    obj = Slotted()
    cst.attach(obj, cst.Span(file='a.rpy', start=1, end=1), 'x',
               cst.Trivia(blank_before=0, indent=''))
    assert cst.span_of(obj) is None       # 没挂上
    assert cst.statement_text(obj) == 'x'  # 但降级路径仍然可用


# ---------------------------------------------------------------------------
#  ③ 不重不漏
# ---------------------------------------------------------------------------

def test_spans_do_not_overlap(tmp_path):
    """重叠 = 写回时必然打架 —— 它是 D3 的验收项，不是可选体检项。"""
    _f, pr = _parse(tmp_path)
    assert cst.overlaps(pr.statements) == [], cst.overlaps(pr.statements)


def test_top_level_lines_are_not_swallowed(tmp_path):
    """
    label **内**的语句不许把顶格行吞进来。

    `define e = Character(...)` 是顶层定义，它不归任何 label 内的语句 ——
    被吞进 span 就意味着"写回时会顺手把别人的定义也重写一遍"。
    """
    _f, pr = _parse(tmp_path)
    for st in pr.statements:
        raw = cst.raw_text_of(st)
        for line in raw.split('\n'):
            if line.strip() and (len(line) - len(line.lstrip())) == 0:
                assert line.strip().startswith('label'), (
                    f'{st.kind} 的 span 吞了顶格行：{line!r}\n{raw}')
    assert not any('Character' in cst.raw_text_of(st) for st in pr.statements)


def test_label_is_its_own_statement_not_a_swallow_all(tmp_path):
    _f, pr = _parse(tmp_path)
    labels = [st for st in pr.statements if st.kind == 'raw'
              and (st.payload or {}).get('text', '').startswith('label')]
    # `label` 行本身进不进语句流随实现而定；进了就必须只占自己一行
    for st in labels:
        sp = cst.span_of(st)
        assert sp.lines == 1, sp.to_dict()


# ---------------------------------------------------------------------------
#  ④ 真机
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL.is_dir(), reason='换机 skip')
def test_real_project_cst_coverage_is_total():
    """
    真机：**每个 .rpy 的每条语句都要带真迹**，且没有一条贴错行。

    "贴错行"（`raw_text` 在源文件里找不到）比"没有真迹"危险得多 ——
    写回时会把 A 的内容写到 B 的位置。
    """
    from storygraph.parsing.discover import discover_scripts
    scripts = discover_scripts(REAL)
    assert scripts, '没扫到脚本'
    az = IncrementalRenPyAnalyzer()
    total = verified = mismatch = 0
    for f in scripts:
        pr, _ = az.analyze(files=[f], graph=None)
        src = Path(f).read_text(encoding='utf-8', errors='ignore')
        stats = cst.cst_coverage(pr.statements, src)
        total += stats['total']
        verified += stats['verified']
        mismatch += stats['mismatch']
        assert not cst.overlaps(pr.statements), (
            f'{Path(f).name} 有重叠 span：{cst.overlaps(pr.statements)[:2]}')
    assert total > 1000, f'语句太少（{total}），判据不成立'
    assert mismatch == 0, f'{mismatch} 条真迹在源文件里找不到'
    assert verified == total, f'{total - verified} 条语句没有真迹'
