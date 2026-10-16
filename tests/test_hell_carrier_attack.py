"""
tests/test_hell_carrier_attack.py
=================================
**第七轮地狱测试：载体攻击**（用户数据不是落在 `.rpy` 里，而是落在"**装它的东西**"上）

为什么换这条轴（方法论：换轴比加量更有效）
----------------------------------------
前六轮分别换过：顶层块 / 结构位×行尾注释 / 编辑序列 / 跨文件 / 白盒解析 / 多引擎。
这一轮换的是"**载体**" —— 同一句台词，装它的容器换了，丢字的机理**完全不一样**：

  ① **Excel 单元格**（xlsx = XML + 单元格有"类型"）：
       · `=` / `+` / `-` / `@` 开头的文本会被当**公式**；
       · `00123` / `1e5` / `TRUE` / `2026-10-16` 会被改成**数字/布尔/日期**型
         → 回来时**类型变了**（前导零没了、`TRUE` 变 `True`）；
       · 单元格 **32767 字符**硬上限；
       · `>` `<` `&` 在 XML 里要转义；
       · 用户在格子里按 Alt+Enter 塞进去的是**真的换行符**（`\\n`）；
       · 首尾空格会被 Excel 的输入习惯吃掉。
     ⚠ **Excel 是这个产品的主路**（用户画像："对着表格就能编排出很棒的演出"）
       —— 这条路静默改字，比 `.rpy` 那条路更贵。
  ② **文件编码 / 换行**：CRLF、BOM、无尾换行、Tab 缩进、混合缩进。

判据（沿用方法论里的 I1/I2/I3/I5）
--------------------------------
  · **I1 内容不灭**：payload 在**它原来那个字段**里还在 ——
    **按字段比、按 LineId 对齐**，绝不在整份产物里搜字符串
    （方法论 §判据校准 9 + "假阴性比假阳性更危险"）；
  · **I2 结构不坏**：产物可再解析、无跨物理行字符串；
  · **I3 不许撒谎**：保不住的必须报出来，不许静默；
  · **I5 编码不动点**：`render → parse → render` **逐字节**相等。

§有意为之（别当缺陷报）
-----------------------
  · 首次导出**会**补生成标记行、统一缩进 → 凡"两路/两轮"比较都先 `_norm()`；
  · `elif` 折成 `not(前一条件)`、空块补 `pass`（既有设计，本文件不碰）；
  · **注释列的尾随空格会被规范化掉**（实测 `'## 尾随空格 '` → `'## 尾随空格'`），
    而**文本列不会**（`'尾随空格 '` 原样回来）。判断：多半是有意为之
    （注释行尾的空格没有意义，留着只会制造无谓的 diff），但**代码里没写明** ——
    所以这一条记成 **P3**（见下方 `test_注释列尾随空格被规范化_已知`），
    本文件按"已规范化"比对，不把它当红。

§判据校准（我这一轮踩过的坑，写下来免得下次再犯）
------------------------------------------------
1. **`注释` 列存的是带 `## ` 前缀的整行**（`'## zzsentcmtzz'`），不是裸文本 ——
   第一版我按裸文本搜行号，于是**除最简单的负载外全红**（77 条里 51 红，
   其中 49 条是这个错造成的）。**现象上看着像"产品到处丢话"，其实是判据错**。
   校准：搜行号 / 注入 / 断言都按 `'## ' + payload`。
2. 方法论 §三.9 那条（别用 `count` / 别在整份产物里搜字符串）——本文件一律
   **按 LineId 对齐到那一拍、读它自己的字段**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_model import BEAT_FIELDS  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel  # noqa: E402
from storygraph.story.excel_import import import_beats_from_excel  # noqa: E402

GEN_MARK = '# 由剧本表自动生成'

SENT_SAY = 'zzsentinelzz'
SENT_CMT = 'zzsentcmtzz'

#: 载体 ①：**用户在 Excel 单元格里打**这些东西（我把它注射进格子里）。
CELL_PAYLOADS = [
    ('=1+1', '公式式样'),
    ('=SUM(A1:A2)', '函数调用式样'),
    ('+1', '加号开头（Excel 也当公式输入）'),
    ('-1', '减号开头'),
    ('@foo', 'at 开头'),
    (' 前导空格', '前导空格'),
    ('尾随空格 ', '尾随空格'),
    ('A\nB', '格内真换行（Alt+Enter）'),
    ('A\tB', '格内制表符'),
    ('<tag>', 'XML 左尖括号'),
    ('a & b', 'XML 与号'),
    ('a > b', 'XML 右尖括号'),
    ('全角\u3000空格', '全角空格'),
    ('零宽\u200b空格', '零宽空格'),
    ('🎯emoji', '代理对'),
    ('中文😀混排', '中英混排 + 代理对'),
    ('00123', '前导零的"数字"'),
    ('1e5', '科学计数式样'),
    ('TRUE', '布尔式样（大写）'),
    ('FALSE', '布尔式样（大写）'),
    ('#N/A', 'Excel 错误值式样'),
    ('2026-10-16', '日期式样'),
    ('2026/10/16 10:00', '日期时间式样'),
    ('3.14', '小数点式样'),
    ('-0.5', '负小数式样'),
]

#: 载体 ②：**.rpy 里本来就有**（测"工具的**写**表那一侧"）。
RPY_PAYLOADS = [p for p, _ in CELL_PAYLOADS if '\n' not in p and '\t' not in p]


def _norm(text: str) -> list:
    """去掉生成标记行 + 首尾空行 + 行尾空白（方法论 §三.4）。"""
    lines = [ln.rstrip() for ln in str(text or '').splitlines()
             if not ln.startswith(GEN_MARK)]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _beats(fp):
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    return beats_from_parse_result(pr)


def _project(write_rpy, name='ch.rpy'):
    body = (f'label a:\n'
            f'    e "{SENT_SAY}"\n'
            f'    ## {SENT_CMT}\n'
            f'    return\n')
    return write_rpy(body, name)


_CELL = {'Text': BEAT_FIELDS.index('Text') + 1,
         'Comment': BEAT_FIELDS.index('Comment') + 1,
         'LineId': BEAT_FIELDS.index('LineId') + 1}


def _row_where(ws, col, value):
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=col).value == value:
            return r
    raise AssertionError(f'表里找不到 {value!r}（第 {col} 列）')


def _inject(tmp_path, write_rpy, payload):
    """
    导出 → **用户在格子里打字** → 存 → 读回。返回 (读回的拍, 两个 LineId)。

    ⚠ 判据校准（我第一版在这里错了）：`注释` 列里存的是**带 `## ` 前缀的整行**
    （实测 `'## zzsentcmtzz'`），不是裸文本 —— 所以搜行号、注入、断言
    都必须按 `'## ' + payload` 走。文本列则是裸文本。
    """
    import openpyxl

    fp = _project(write_rpy)
    x = tmp_path / 't.xlsx'
    export_beats_to_excel(_beats(fp), x, include_dropdowns=False)
    wb = openpyxl.load_workbook(x)
    ws = wb['Beats']
    r_say = _row_where(ws, _CELL['Text'], SENT_SAY)
    r_cmt = _row_where(ws, _CELL['Comment'], '## ' + SENT_CMT)
    lid_say = ws.cell(row=r_say, column=_CELL['LineId']).value
    lid_cmt = ws.cell(row=r_cmt, column=_CELL['LineId']).value
    ws.cell(row=r_say, column=_CELL['Text']).value = payload
    ws.cell(row=r_cmt, column=_CELL['Comment']).value = '## ' + payload
    wb.save(x)
    got = {b.line_id: b for b in import_beats_from_excel(x)}
    return got, lid_say, lid_cmt


@pytest.mark.parametrize('payload,why', CELL_PAYLOADS,
                         ids=[f'{i}-{w}' for i, w in CELL_PAYLOADS])
def test_在单元格里打的字必须原样回来(tmp_path, write_rpy, payload, why):
    """
    I1（按字段、按 LineId 对齐）：用户在格子里打的字，读回来必须还是那串字。

    ⚠ 这里**不搜整份产物**，而是按 LineId 找到那一拍、读它自己的 `text` /
    `comment` —— 否则"payload 恰好等于别的字段的值"会让判据永远变绿（假阴性）。
    """
    got, lid_say, lid_cmt = _inject(tmp_path, write_rpy, payload)
    say = got.get(lid_say)
    cmt = got.get(lid_cmt)
    assert say is not None and cmt is not None, (why, lid_say, lid_cmt)
    bad = []
    if str(getattr(say, 'text', '')) != payload:
        bad.append(('Text', payload, str(getattr(say, 'text', ''))))
    # ⚠ 注释列**允许尾随空格的规范化**（§有意为之第 3 条，P3）——文本列不允。
    got_cmt = str(getattr(cmt, 'comment', ''))
    if got_cmt.rstrip() != ('## ' + payload).rstrip():
        bad.append(('Comment', '## ' + payload, got_cmt))
    assert not bad, (
        f'【{why}】单元格里的字**被改掉了**（左=我打的，右=读回来的）：{bad}')


@pytest.mark.parametrize('payload,why', CELL_PAYLOADS,
                         ids=[f'{i}-{w}' for i, w in CELL_PAYLOADS])
def test_单元格里的字不许把产物弄成非法语句(tmp_path, write_rpy, payload,
                                          why):
    """I2：格子里带真换行/引号之类，编译出来的 `.rpy` 必须**还能解析**。"""
    from storygraph.pipeline.compile import compile_beats

    got, lid_say, _ = _inject(tmp_path, write_rpy, payload)
    text = compile_beats(list(got.values())).rpy_text
    out = tmp_path / 'back.rpy'
    out.write_text(text, encoding='utf-8')
    from storygraph.parsing.renpy_parser import RenPyParser
    parser = RenPyParser()
    parser.parse(out)
    assert not parser.errors, (
        f'【{why}】格子里那串字把产物写成了非法语句：{parser.errors[:3]}\n'
        f'（payload={payload!r}）')


@pytest.mark.parametrize('payload', RPY_PAYLOADS,
                         ids=[p for p in RPY_PAYLOADS])
def test_rpy_里本来就有也走一遍表(tmp_path, write_rpy, payload):
    """
    载体 ②：**工具写表那一侧**（`.rpy` 里就有这些字）。
    往返一遍，`text` 字段里必须还是原样。
    """
    fp = write_rpy(f'label a:\n    e "{payload}"\n    return\n', 'w.rpy')
    before = [b for b in _beats(fp) if str(getattr(b, 'text', '')) == payload]
    assert before, f'原文里那串字没有被解析成台词：{payload!r}'
    x = tmp_path / 'w.xlsx'
    export_beats_to_excel(_beats(fp), x, include_dropdowns=False)
    got = {b.line_id: b for b in import_beats_from_excel(x)}
    after = got.get(before[0].line_id)
    assert after is not None, (payload, before[0].line_id)
    assert str(getattr(after, 'text', '')) == payload, (
        f'走一趟表就变了：{payload!r} → {getattr(after, "text", None)!r}')


def test_fixed_超长台词不许静默截断(tmp_path, write_rpy):
    """
    ❌ **真缺陷（P1）· 当场是红的** —— 台词超过 32767 字就被**静默截断**。

    现象（最小复现）
    ----------------
        label a:
            e "<40000 个 L>"          ← 一次独白长一点就到了
    导出成表后：那个台词格里只剩 **32767** 个字；读回来 32767；编译产物 32767。
    **3187 字原文消失，全程零警告**。更贵的是：用户下次"导出回 .rpy"，
    产物会带着截断后的文本**覆盖原文** —— 真源被静默改坏。

    根因（定位到具体处）
    -------------------
    xlsx 规范里**单元格字符串硬上限 32767**。`excel_export.export_beats_to_excel`
    直接把 `beat.text` 交给 openpyxl 写单元格（`ws.append` / `cell.value = …`），
    **既不检查长度，也不在超限时报出来** —— openpyxl 自己会静默截断。
    这不是"某个 carry 写错了"，是**载体本身的容量没被当成约束**。

    影响的真实用法
    --------------
    长独白 / 整段旁白 / 系统提示里贴的一大段文本。用户画像里"文案写手"最常写长句。
    Excel 是产品主路 —— 这条路静默吃字，比 `.rpy` 那条更贵。

    修法方向（两条，选一即可，但**必须把 I3 补上**）
    ----------------------------------------------
      · 超 32767 时**报出来**（导出报告里列一个"本条台词过长、未能进表"），
        并**拒绝**把该拍写进表（宁可让用户看到，也不许静默截）；
      · 或把超长文本切进"溢出列/附注表"，导入时拼回来 —— 但那是新功能，
        至少要先把"报出来"做上。
    ⚠ 修好之后本测试自己会变绿（下面 `test_现状_…` 那条钉住的是**当前**行为，
    修好后要一起改）。
    """
    import openpyxl

    from storygraph.story.excel_import import import_beats_from_excel

    # —— 变异对照（证明判据不是假阳性）：30000 字**没有**超过上限，必须原样通过 ——
    ok_text = 'K' * 30000
    fp_ok = write_rpy(f'label a:\n    e "{ok_text}"\n    return\n', 'ok.rpy')
    x_ok = tmp_path / 'ok.xlsx'
    export_beats_to_excel(_beats(fp_ok), x_ok, include_dropdowns=False)
    back_ok = [b for b in import_beats_from_excel(x_ok)
               if str(getattr(b, 'text', ''))]
    ok_ok = bool(back_ok) and back_ok[0].text == ok_text
    assert ok_ok, (
        '30000 字没过上限却也变了 —— 那说明这条判据红的不是"上限"，先查这个')

    long_text = 'L' * 40000
    fp = write_rpy(f'label a:\n    e "{long_text}"\n    return\n', 'l.rpy')
    x = tmp_path / 'l.xlsx'
    export_beats_to_excel(_beats(fp), x, include_dropdowns=False)
    ws = openpyxl.load_workbook(x)['Beats']
    cell = ws.cell(row=_row_where(ws, _CELL['Text'], long_text[:32767]),
                   column=_CELL['Text'])
    got = cell.value
    # ⚠ 先算成布尔再断言：不然 pytest 的断言重写会把两个 40000 字的值全 dump 出来
    same = (got == long_text)
    assert same, (
        '超长台词在表里被**静默截断**了：原文 %d 字，格子里 %s'
        '（既没保住、也没有任何地方报出来 —— 撞 I3「不许撒谎」）'
        % (len(long_text), 'None' if got is None else f'{len(str(got))} 字'))

def test_注释列尾随空格被规范化_已知(tmp_path, write_rpy):
    """
    P3（**已知规范化，不是缺陷**）：注释列的尾随空格会被去掉，文本列不会。
    这条把"当前行为"钉住 —— 哪天注释列也开始保留尾随空格，它会红，
    提醒回来确认这是有意的改动。
    """
    got, _, lid = _inject(tmp_path, write_rpy, '尾随空格 ')
    assert str(got[lid].comment) == '## 尾随空格', got[lid].comment
    got2, lid2, _ = _inject(tmp_path, write_rpy, '尾随空格 ')
    assert str(got2[lid2].text) == '尾随空格 ', '文本列的尾随空格不该被动'


def test_编码不动点_逐字节(tmp_path, write_rpy):
    """I5：`render → parse → render` 必须逐字节相等（用户数据的各种载体一起上）。"""
    from storygraph.story.beat_render import render_beats_to_rpy

    lines = ['label a:']
    for p in RPY_PAYLOADS[:12]:
        lines.append(f'    e "{p}"')
    lines.append('    return')
    fp = write_rpy('\n'.join(lines) + '\n', 'i.rpy')
    r1 = render_beats_to_rpy(_beats(fp))
    fp2 = write_rpy(r1, 'i2.rpy')
    r2 = render_beats_to_rpy(_beats(fp2))
    assert _norm(r1) == _norm(r2), (
        '第 1→2 轮变形',
        [(a, b) for a, b in zip(_norm(r1), _norm(r2)) if a != b][:5])


def test_文件载体_crlf_bom_无尾换行_tab缩进(tmp_path, write_rpy):
    """载体 ②：**文件本身**换了编码/换行 —— 不许因为 CRLF/BOM 就丢字或报错。"""
    from storygraph.parsing.renpy_parser import RenPyParser

    cases = {
        'crlf': 'label a:\r\n    e "台词"\r\n    return\r\n',
        'bom': '\ufefflabel a:\n    e "台词"\n    return\n',
        'no_tail_newline': 'label a:\n    e "台词"\n    return',
        'tab_indent': 'label a:\n\te "台词"\n\treturn\n',
        'blank_lines': '\n\nlabel a:\n\n    e "台词"\n\n    return\n\n',
    }
    bad = []
    for tag, body in cases.items():
        fp = write_rpy(body, f'{tag}.rpy')
        p = RenPyParser()
        p.parse(fp)
        if p.errors:
            bad.append((tag, '解析报错', p.errors[:2]))
            continue
        bs = _beats(fp)
        if not any('台词' in str(getattr(b, 'text', '')) for b in bs):
            bad.append((tag, '台词没被解析出来', [b.text for b in bs][:3]))
    assert not bad, bad


def test_最小结构面_不许无中生有(tmp_path, write_rpy):
    """I3：空文件 / 只有注释 / 只有 label 头 / 空块 —— 不许凭空造出台词或行。"""
    cases = {
        'empty': '',
        'only_comment': '## 只有一行注释\n',
        'label_only': 'label a:\n',
        'empty_if': 'label a:\n    if x:\n    return\n',
        'only_blank_lines': '\n\n\n',
    }
    bad = []
    for tag, body in cases.items():
        try:
            fp = write_rpy(body, f'min-{tag}.rpy')
            bs = _beats(fp)
            texts = [str(getattr(b, 'text', '') or '') for b in bs]
            bogus = [t for t in texts if t.strip()]
            if bogus:
                bad.append((tag, '凭空多了台词', bogus[:3]))
        except Exception as exc:                     # noqa: BLE001
            bad.append((tag, f'{type(exc).__name__}: {exc}'))
    assert not bad, bad
