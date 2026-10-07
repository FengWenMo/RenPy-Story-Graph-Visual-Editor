"""
M1：label 内的**未建模带子块语句**不许整块消失
================================================

背景（2026-10-07 架构评审，`docs/设计_自定义语法适应架构_2026-10-07.md` §1.5）：

`renpy_parser._stmt_unmodeled` 只处理**叶语句**；带子块的未知语句
`return None` —— 只留一条没人看的 warning。而顶层兜底
`_top_level_region()` **只覆盖 label 之外**。

于是这条路径成立：

    label 内的自定义块语句 → 连头带块体从模型里消失
    → 若走全量重生成（导出到新文件 / 补丁失败兜底），这段剧情就没了
    → 而门禁（`OPAQUE_LOST`）**无从得知**它存在过（内容没进任何通道）

性质与"不好用"不同：**这一条是"会吃内容"**。

本文件钉住三件事：

  1. 自定义块的**每一行**都逐字出现在产物里（不许只留头部丢块体）；
  2. 它走的是**保真通道**（`raw_kind='unknown_block'`，可诊断、可报告）；
  3. 顺序不变 —— 它在哪两句台词之间，产物理应也在那儿。

⚠ 夹具刻意"不干净"：块体里有注释、有空行、有缩进深浅不一的语句 ——
整齐夹具会被形状相似度歪打正着救回来（本仓库的老教训）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel


def _parse(tmp_path, src_text: str, name: str = 's'):
    f = tmp_path / f'{name}.rpy'
    f.write_text(src_text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return pr, beats_from_parse_result(pr)


def _roundtrip(tmp_path, src_text: str) -> str:
    """`.rpy` → 拍 → 表 → 表 → `.rpy`（**纯全量**那条路，没有源可增量）。"""
    _pr, beats = _parse(tmp_path, src_text)
    xl = tmp_path / 's.xlsx'
    export_beats_to_excel(beats, xl)
    return compile_beats(import_beats_from_excel(xl)).rpy_text


def _body_lines(text: str) -> list:
    """产物的"内容行"（去掉文件头注释、label 头、`return` 这些结构行）。"""
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith('#')
            and not ln.strip().startswith('label')
            and ln.strip() != 'return']


# ---------------------------------------------------------------------------
#  ① 最小现场：块头 + 两行块体
# ---------------------------------------------------------------------------

MINIMAL = (
    'label a:\n'
    '    "第一句"\n'
    '    mycue "闪电" intensity=3:\n'
    '        target = ling\n'
    '        hold 0.5\n'
    '    "第二句"\n'
    '    return\n'
)


def test_unknown_block_verbatim_not_inferred(tmp_path):
    """
    产物里**一个字都不许被推断**：不补 `$`、不包 `python:`。

    这条判据是变异验证逼出来的（2026-10-07）：只断言"这些行在产物里"
    太松 —— 缺了 `Beat.opaque` 那条保真通道时，整块会**侥幸**落进
    「代码」列，而它的形状（首行不缩进 + 其余行都缩进）恰好被
    `_python_block_indent` 认成 python 块，于是被包进 `python:`：
    文本还在，**语义已经全错**（一段 Ren'Py 自定义块变成 python 代码）。
    """
    out = _roundtrip(tmp_path, MINIMAL)
    assert '\npython:' not in out, f'自定义块被包进了 python 块：\n{out}'
    for line in out.splitlines():
        s = line.strip()
        if s and not s.startswith('#') and not s.startswith('label '):
            assert not s.startswith('$ '), (
                f'自定义块的行被补了 `$`（那是裸 python 的处理）：{line!r}\n{out}')


def test_unknown_block_survives_full_regeneration(tmp_path):
    """
    自定义块的**头与块体**都必须逐字出现在全量产物的 .rpy 里。

    这是"会吃内容"的最小复现：修复前这里只会剩一句 warning。
    断言取**整段相等**（不是"包含"）：包含式断言对"被包进 python 块"
    这类损坏是瞎的，见 `test_unknown_block_verbatim_not_inferred`。
    """
    out = _roundtrip(tmp_path, MINIMAL)
    body = _body_lines(out)
    assert body == ['"第一句"', 'mycue "闪电" intensity=3:',
                    'target = ling', 'hold 0.5',
                    '"第二句"'], out


def test_unknown_block_keeps_position(tmp_path):
    """块体不许被挪到别处：它夹在两句台词之间，产物里也得在那儿。"""
    out = _roundtrip(tmp_path, MINIMAL)
    lines = _body_lines(out)
    assert lines.index('mycue "闪电" intensity=3:') == 1, out
    assert lines.index('target = ling') == 2, out
    assert lines.index('"第二句"') == 4, out


# ---------------------------------------------------------------------------
#  ② 走保真通道：可诊断、可报告
# ---------------------------------------------------------------------------

def test_unknown_block_enters_fidelity_channel(tmp_path):
    """
    它必须进**保真通道**，而不是变成一个"解析失败"。

    `raw_kind='unknown_block'` 是这条通道的标签 —— 有了它，
    体检报告才能说清"这几行工具只保真、不管辖"，而不是一片沉默。
    """
    pr, _beats = _parse(tmp_path, MINIMAL)
    raws = [s for s in pr.statements if s.kind == 'raw']
    kinds = {(s.payload or {}).get('raw_kind', '') for s in raws}
    assert 'unknown_block' in kinds, (
        f'自定义块没有进保真通道，raw_kind 集合是 {kinds}\n'
        f'所有 raw：{[(s.payload or {}).get("text", "")[:40] for s in raws]}')


def test_unknown_block_is_not_silently_dropped_from_statements(tmp_path):
    """IR 里必须真的存在这些行 —— 不许"只在 warning 里提过"。"""
    pr, _beats = _parse(tmp_path, MINIMAL)
    all_text = '\n'.join(
        (s.payload or {}).get('text', '') or ''
        for s in pr.statements if s.kind == 'raw')
    assert 'hold 0.5' in all_text, (
        f'IR 里找不到块体内容：\n{all_text!r}')


# ---------------------------------------------------------------------------
#  ③ 脏夹具：注释 / 空行 / 两级缩进 / 条件里的块
# ---------------------------------------------------------------------------

MESSY = (
    'label messy:\n'
    '    "台词一"\n'
    '    shop_panel "杂货铺":\n'
    '        # 美术还没出图，先用占位\n'
    '        slots = [\n'
    '            "potion",\n'
    '            "elixir",\n'
    '        ]\n'
    '\n'
    '        if 好感 > 50:\n'
    '            discount = 0.8\n'
    '    "台词二"\n'
    '    return\n'
)


def test_messy_unknown_block_survives(tmp_path):
    """块体里有注释、空行、续行括号、内层 `if` —— 一行都不许少。"""
    out = _roundtrip(tmp_path, MESSY)
    for line in ['shop_panel "杂货铺":', '# 美术还没出图，先用占位',
                 '"potion",', '"elixir",', 'discount = 0.8']:
        assert line in out, f'{line!r} 丢了：\n{out}'


def _block_lines(out: str, head: str) -> list:
    """产物里以 `head` 开头那一整块（相对缩进，去空行）——判据用。"""
    lines = out.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.strip().startswith(head))
    base = len(lines[i]) - len(lines[i].lstrip())
    body = []
    for ln in lines[i + 1:]:
        if not ln.strip():
            continue
        if len(ln) - len(ln.lstrip()) <= base:
            break
        body.append(ln[base:])
    return body


def test_messy_unknown_block_keeps_relative_indentation(tmp_path):
    """
    块体的**相对缩进**必须逐字保留（括号续行也是）。

    为什么单独钉一条：`slots = [\\n    "potion",\\n]` 在 lexer 里是**一条**
    逻辑行（续行被并进来了），续行的缩进是**绝对**的 —— 只按首行缩进切，
    续行会原地不动，往返一次多缩一层（实测 12 → 16 空格）。
    判据取整段相等，包含式断言对这种"多缩一层"是瞎的。
    """
    out = _roundtrip(tmp_path, MESSY)
    assert _block_lines(out, 'shop_panel') == [
        '    # 美术还没出图，先用占位',
        '    slots = [',
        '        "potion",',
        '        "elixir",',
        '    ]',
        '    if 好感 > 50:',
        '        discount = 0.8',
    ], out


def test_blank_line_inside_unknown_block_survives(tmp_path):
    """
    块内的**空行**也是内容（作者的排版），不许被吃掉。

    lexer 不产生空行的 `LogicalLine`，所以只走 AST 那条路必然丢
    （实测 8 行块丢 1 行）。`_unknown_block_lines` 因此改成按**原文
    物理行**取 —— 判据落在"空行还在不在它该在的位置"。
    """
    out = _roundtrip(tmp_path, MESSY)
    text = out
    head = text.index('shop_panel "杂货铺":')
    tail = text.index('"台词二"', head)
    block = text[head:tail]
    assert ']\n\n' in block, f'块内空行丢了：\n{block!r}'


def test_incremental_patch_keeps_unknown_block(tmp_path):
    """
    **增量**那条路也要保住自定义块（改同章别的一行时）。

    全量那条路已经钉住了；但日常导出走的是受控补丁（"改一句话就原位替换
    一行"），而补丁的身份判据是 `Beat.text` —— 自定义块的拍 `text` 是空的
    （内容在 `opaque` 里），正好是契约 B2 说的"身份判据的坑"那一类。
    这里钉住：**改别处时它逐字节不动**。
    """
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.patcher import patch_export

    src = (
        '# 文件头：必须保住\n'
        'label ch:\n'
        '    "第一句"\n'
        '    mycue "闪电" intensity=3:\n'
        '        target = ling\n'
        '        hold 0.5\n'
        '    "第二句"\n'
        '    return\n'
    )
    fp = tmp_path / 'ch.rpy'
    fp.write_text(src, encoding='utf-8')
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(fp)))
    for b in beats:
        if b.text == '第二句':
            b.text = '第二句（改过）'
    out = (patch_export(beats, fp, render_beats_to_rpy(beats)).text or '')
    for line in ['mycue "闪电" intensity=3:', 'target = ling', 'hold 0.5']:
        assert line in out, f'{line!r} 在增量产物里丢了：\n{out}'
    assert '第二句（改过）' in out, out
    assert '# 文件头：必须保住' in out, f'没动的行不许变：\n{out}'


def test_unknown_block_inside_if_survives(tmp_path):
    """条件分支里的自定义块同样不许丢（而且要保住它的条件）。"""
    script = (
        'label b:\n'
        '    if flag:\n'
        '        mycue "雷鸣":\n'
        '            volume = 0.7\n'
        '    "一句"\n'
        '    return\n'
    )
    out = _roundtrip(tmp_path, script)
    assert 'volume = 0.7' in out, out


# ---------------------------------------------------------------------------
#  ④ 门禁必须**说出口**（M1-c，D1 选的是"透传 + 报警 + 报告"）
# ---------------------------------------------------------------------------

def test_gate_reports_unmanaged_block(tmp_path):
    """
    产物里有工具不认识的块 → 门禁必须报 `UNMANAGED_BLOCK`，并**指名道姓**。

    这条是"不许静默"的落地：内容已经保住了，但"这几行不归我管"这件事
    必须能出现在用户看得到的地方（导出结论 / build 报告），
    而不是只躺在解析器的 warning 列表里。
    """
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.generation_guard import evaluate_generation

    _pr, beats = _parse(tmp_path, MINIMAL)
    text = render_beats_to_rpy(beats, label_name='a')
    issues = evaluate_generation(text, tmp_path / 'out.rpy', beats=beats)
    hits = [i for i in issues if i.code == 'UNMANAGED_BLOCK']
    assert hits, [i.format() for i in issues]
    assert 'mycue' in hits[0].message, hits[0].message


def test_gate_does_not_block_unmanaged_block(tmp_path):
    """
    它是 **warn，不是 block** —— 工具不认识 != 脚本坏了。

    拦下导出只会把用户赶回"工具没用"那一步（D1 的选择）。
    """
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.generation_guard import evaluate_generation

    _pr, beats = _parse(tmp_path, MINIMAL)
    text = render_beats_to_rpy(beats, label_name='a')
    issues = evaluate_generation(text, tmp_path / 'out.rpy', beats=beats)
    hits = [i for i in issues if i.code == 'UNMANAGED_BLOCK']
    assert hits and not hits[0].is_blocking, hits


def test_standard_flow_block_is_reported_separately(tmp_path):
    """
    `while` / `for` 这类**标准流程块**要与"自定义块"分开报。

    真机实测（`mini_game1.rpy:50`）：那个 `while current_round < game_rounds:`
    是游戏的**主循环**，以前连头带块体被整块丢掉。它现在也要保真，
    但它的语义是"工具认识、只是没建模" —— 混成一句"工具不认识"会把
    人引向错误的修法（去写 rules.json，而真正该动的是我们）。
    """
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.generation_guard import evaluate_generation

    script = (
        'label a:\n'
        '    while n < 3:\n'
        '        call round_x from _call_round_x\n'
        '        $ n += 1\n'
        '    "完成"\n'
        '    return\n'
    )
    _pr, beats = _parse(tmp_path, script)
    text = render_beats_to_rpy(beats, label_name='a')
    # 内容必须保住（这才是主循环不丢的关键）
    assert 'call round_x from _call_round_x' in text, text
    issues = evaluate_generation(text, tmp_path / 'out.rpy', beats=beats)
    codes = [i.code for i in issues]
    # ⚠ **2026-10-07 更新**：D2 把 `while` / `for` 建模之后，
    # "标准流程块"这条待办就**还掉了** —— 这条路不再报警，是正确的。
    # （这条判据从前断言"必须报 KNOWN_UNMODELED_BLOCK"，是当时的事实。）
    assert 'KNOWN_UNMODELED_BLOCK' not in codes, (
        f'循环块已经建模（T0b），不该再报"给工具作者的待办"：'
        f'{[i.format() for i in issues]}')
    assert 'UNMANAGED_BLOCK' not in codes, (
        f'标准流程块被误报成"自定义块"了（口径混了）：'
        f'{[i.format() for i in issues]}')


def test_known_unmodeled_bucket_still_works():
    """
    机制仍在：**"认识形状但没建模"这条桶不许因为现在没货就烂掉。**

    D2 之后真实项目里可能已经没有这种语句了（好事），但只要我们往
    `RENPY_FLOW_BLOCK_HEADS` 里加一个**没建模**的头，它就要照常报警 ——
    否则下一个人加头的时候，会以为"加了就自动有人报"。
    """
    from storygraph.core.vocabulary import RENPY_FLOW_BLOCK_HEADS
    from storygraph.story.generation_guard import block_buckets

    # 从**词表本身**取一个"工具认识形状"的头（不写死名字：
    # 词表会变，写死会让这条判据变成"维护负担"而不是"守卫"）。
    known = sorted(RENPY_FLOW_BLOCK_HEADS - {'if', 'for', 'while'})
    assert known, '流程头词表不该为空'
    known_head, unknown_head = known[0], 'zzz_never_a_renpy_head'

    def _beat(head):
        class _B:
            opaque = f'{head} x:\n    pass'
            spec_name = ''
            loop_header = ''
        return _B()

    _d, custom, standard = block_buckets([_beat(known_head),
                                          _beat(unknown_head)])
    assert standard == [f'{known_head} x:'], (custom, standard)
    assert custom == [f'{unknown_head} x:'], (custom, standard)


def test_build_summary_prints_guard_hints(tmp_path):
    """
    **报告必须出现**（D1）：build 报告要把"哪几行工具不管辖"印出来。

    只报"有 N 条警告"等于没说 —— 用户需要知道的正是那几行是哪几行，
    因为那才是他要自己盯的地方。这条钉住 `BuildResult.summary()`
    真的会把门禁的非致命结论逐条带出来。
    """
    from storygraph.pipeline.build import BuildResult

    res = BuildResult(
        ok=True, engine='renpy', out_dir=str(tmp_path),
        report={'build': {'stats': {}, 'guard': {
            'written': True, 'blocking': 0,
            'warnings': ['[warn] UNMANAGED_BLOCK（整体）：有 1 处自定义块语句'],
        }}})
    text = res.summary()
    assert 'UNMANAGED_BLOCK' in text, text


def test_gate_silent_when_nothing_unmanaged(tmp_path):
    """干干净净的章节不许凭空多出一条假警报。"""
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.generation_guard import evaluate_generation

    _pr, beats = _parse(
        tmp_path, 'label a:\n    "只有一句台词"\n    return\n')
    text = render_beats_to_rpy(beats, label_name='a')
    issues = evaluate_generation(text, tmp_path / 'out.rpy', beats=beats)
    assert not [i for i in issues if i.code == 'UNMANAGED_BLOCK'], (
        [i.format() for i in issues])


def test_unknown_block_lines_are_reported(tmp_path):
    """
    解析结果要能**数得出来**有几处未建模块 —— 报告要用它说人话。

    判据落在 `ParseResult` 上（体检报告从它取数），不是落在日志上。
    """
    pr, _beats = _parse(tmp_path, MESSY)
    records = getattr(pr, 'unknown_blocks', None)
    assert records, (
        'ParseResult 上没有记录未建模块的清单，报告将无法说明"哪几处工具不管辖"')
    assert any('shop_panel' in (r.get('head') or '') for r in records), records
    assert all(r.get('file') and r.get('line') for r in records), (
        f'报告要能指到"哪一行"，缺了定位就没法修：{records}')
