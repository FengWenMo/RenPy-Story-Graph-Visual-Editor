"""P1 讨论·实现派探针①：给**无标题** menu 加标题（从无到有）。

疑点（读代码得出，待实测）：
  · 卡片路（`EditorSession.export`）有 `trust_ranges=True`（行号新鲜），
    表格路（`export_table`）**不传** trust_ranges —— 同一刀两种待遇（铁律 6）。
  · `tests/test_p0_round4.py::TestMenuCaptionFromScratch` 只走了卡片路，
    所以"测试全绿"证明不了表格路也能加标题。

本探针四件事：
  §1 两条出口 × 五种菜单形状，各测"从无到有加标题"
  §2 失败时把拒因原文打出来（审计说"有三条出路"，核实是什么）
  §3 只动该动的行：期望全文逐行断言（判据要"构造期望全文"）
  §4 空白标题（`" "`）不许被顺手删掉（既有护栏不许回归）

跑法： /d/Python/python tools/_p1debate_impl_menu_caption.py
退出码：0=结论与预期一致；1=出现意外（先怀疑探针，再怀疑产品）
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = '[PASS]', '[FAIL]'
results = []


def check(name, ok, detail=''):
    results.append((bool(ok), name))
    print(f'{PASS if ok else FAIL} {name}')
    if detail:
        for line in str(detail).splitlines():
            print(f'       {line}')


def make_project(tmp: Path, src: str):
    game = tmp / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    return path


def load(path: Path):
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=path.parents[1])
    s.load(path)
    return s


def body(text: str) -> list:
    out, skip = [], False
    for ln in text.splitlines():
        if ln.startswith('# 由剧本表自动生成'):
            skip = True
            continue
        if skip and not ln.strip():
            skip = False
            continue
        out.append(ln)
    return out


# ---------------------------------------------------------------- §1 形状矩阵
SHAPES = {
    '裸 menu:': (
        'label start:\n'
        '    menu:\n'
        '        "开始":\n'
        '            e "a"\n'
        '        "结束":\n'
        '            e "b"\n'
        '    return\n',
        'label start:\n'
        '    menu:\n'
        '        "新标题"\n'
        '        "开始":\n'
        '            e "a"\n'
        '        "结束":\n'
        '            e "b"\n'
        '    return\n'),
    '带名字 menu my_menu:': (
        'label start:\n'
        '    menu my_menu:\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n',
        'label start:\n'
        '    menu my_menu:\n'
        '        "新标题"\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n'),
    '带参数 menu (nvl=True):': (
        'label start:\n'
        '    menu (nvl=True):\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n',
        'label start:\n'
        '    menu (nvl=True):\n'
        '        "新标题"\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n'),
    '嵌在 if 里': (
        'label start:\n'
        '    if flag:\n'
        '        menu:\n'
        '            "开始":\n'
        '                e "a"\n'
        '    return\n',
        'label start:\n'
        '    if flag:\n'
        '        menu:\n'
        '            "新标题"\n'
        '            "开始":\n'
        '                e "a"\n'
        '    return\n'),
    'CRLF 载体': (
        'label start:\r\n'
        '    menu:\r\n'
        '        "开始":\r\n'
        '            e "a"\r\n'
        '    return\r\n',
        'label start:\r\n'
        '    menu:\r\n'
        '        "新标题"\r\n'
        '        "开始":\r\n'
        '            e "a"\r\n'
        '    return\r\n'),
    '带 extras（set seen）': (
        'label start:\n'
        '    menu:\n'
        '        set seen\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n',
        'label start:\n'
        '    menu:\n'
        '        "新标题"\n'
        '        set seen\n'
        '        "开始":\n'
            '            e "a"\n'
        '    return\n'),
}


def run_card_path(src: str, expect: str):
    """卡片路：EditorSession → export(原地)。返回 (ok, 实际全文, 回执)。"""
    tmp = Path(tempfile.mkdtemp(prefix='p1cap_card_'))
    try:
        path = make_project(tmp, src)
        s = load(path)
        # 菜单拍是第 0 拍；把 Text 从占位符改成「新标题」
        fields = s.fields(0)
        fields.update({'Text': '新标题'})
        s.apply(0, fields, cards=s.cards(0), advanced_open=True,
                stage_loaded=fields.get('Stage'))
        res = s.export(path)
        text = path.read_bytes().decode('utf-8')
        return bool(res.get('ok')), text, res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_table_path(src: str, expect: str):
    """表格路：generate_table → 改「台词」格 → export_table + apply_export。"""
    tmp = Path(tempfile.mkdtemp(prefix='p1cap_tab_'))
    try:
        path = make_project(tmp, src)
        import openpyxl
        from storygraph.app.tables import apply_export, export_table
        from storygraph.story.script_table import (
            export_beats_to_excel, import_beats_from_excel)

        xlsx = tmp / 'script.xlsx'
        beats = import_beats_from_excel  # 别名，仅为可读性
        del beats
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
        pr = ParseResultBuilder().build(RenPyParser().parse(path))
        beats = beats_from_parse_result(pr)
        export_beats_to_excel(beats, xlsx)
        wb = openpyxl.load_workbook(xlsx)
        ws = wb['Beats'] if 'Beats' in wb.sheetnames else wb.active
        headers = [str(c.value or '') for c in ws[1]]
        col = None
        for name in ('台词', 'Text', 'text'):
            if name in headers:
                col = headers.index(name) + 1
                break
        assert col, f'找不到台词列：{headers}'
        # 找菜单那一行（Text 为空或占位符）
        for r in range(2, ws.max_row + 1):
            v = ws.cell(row=r, column=col).value
            if not v or str(v) == '\x00menu':
                ws.cell(row=r, column=col).value = '新标题'
                break
        else:
            return False, '', {'error': '表里找不到菜单行'}
        wb.save(xlsx)
        result = export_table(xlsx, source_rpy=path)
        apply_result = apply_export(result, project_root=tmp) \
            if result.get('ok') and result.get('merged_from') else result
        text = path.read_bytes().decode('utf-8')
        ok = bool(result.get('ok')) and bool(
            (apply_result or {}).get('ok', True))
        return ok, text, result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print('=' * 68)
    print('§1 菜单标题从无到有：两条出口 × 六种形状')
    print('=' * 68)
    card_fail, tab_fail = [], []
    for name, (src, expect) in SHAPES.items():
        exp_body = body(expect)
        ok_c, text_c, res_c = run_card_path(src, expect)
        got_c = body(text_c)
        good_c = ok_c and got_c == exp_body
        if not good_c:
            card_fail.append(name)
        check(f'卡片路 · {name}', good_c,
              '' if good_c else
              f'ok={ok_c} 拒因={"；".join(map(str, (res_c or {}).get("blocking") or (res_c or {}).get("reasons") or []))}\n'
              f'期望={exp_body}\n实际={got_c}')

        ok_t, text_t, res_t = run_table_path(src, expect)
        got_t = body(text_t)
        good_t = ok_t and got_t == exp_body
        if not good_t:
            tab_fail.append(name)
        check(f'表格路 · {name}', good_t,
              '' if good_t else
              f'ok={ok_t} note={(res_t or {}).get("note")}\n'
              f'blocking={"；".join(map(str, (res_t or {}).get("blocking") or []))}\n'
              f'期望={exp_body}\n实际={got_t}')

    print()
    print('=' * 68)
    print('§2 结论')
    print('=' * 68)
    check('卡片路六形状全过（含带名字/嵌套/extras）', not card_fail,
          f'失败：{card_fail}' if card_fail else '')
    check('表格路六形状全过', not tab_fail,
          f'失败：{tab_fail}' if tab_fail else '')
    check('两条出口同一口径（铁律 6）', not card_fail and not tab_fail,
          f'卡片失败={card_fail} 表格失败={tab_fail}'
          if (card_fail or tab_fail) else '')

    # §3 空白标题不许被顺手删（既有护栏）
    print()
    print('=' * 68)
    print('§3 空白标题护栏不许回归')
    print('=' * 68)
    src_ws = ('label start:\n'
              '    menu:\n'
              '        " "\n'
              '        "开始":\n'
              '            e "a"\n'
              '    return\n')
    tmp = Path(tempfile.mkdtemp(prefix='p1cap_ws_'))
    try:
        path = make_project(tmp, src_ws)
        s = load(path)
        res = s.export(path)          # 零改动导出
        after = path.read_bytes().decode('utf-8')
        check('零改动导出后 `" "` 原样保留', after == src_ws,
              f'期望={src_ws!r}\n实际={after!r} res={res.get("ok")}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [ok for ok, _ in results if not ok]
    print()
    print(f'汇总：{len(results) - len(bad)}/{len(results)} 通过')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
