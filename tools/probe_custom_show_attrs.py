"""
探针：**没教过的时候**，`show lan base_swimsuit happy` 这种自定义写法会不会被改坏。

用户的怀疑（2026-10-21）：
    `show lan base_swimsuit happy` 实际上是自定义写法……
    当我没有规定 rules.json 时，它能不能识别出"这是我不认识的东西"？

真正要回答的是三件事，一件比一件狠：

  A. **建表**：表里的「立绘」列写的是什么？看得到 `base_swimsuit` 吗？
  B. **零改动往返**：表里一个字不改，导出回原文件，是否逐字节相同？
  C. **改一句台词后**：那一行 `show` 是否**原样**（属性一个不少、顺序不变）？
     —— 这一条最狠：如果它被重写成 `show lan happy`，`base_swimsuit`
     就没了，立绘换一套而**没有任何报错**。

对照组是"单属性"和"无属性"，用来区分"三属性写坏了"还是"所有 show 都写坏了"。

用法：
    D:/Python/python.exe tools/probe_custom_show_attrs.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app import tables                      # noqa: E402

BODIES = {
    '三属性（用户说的那种）': '''\
label ch01:
    show lan base_swimsuit happy
    lan "第一句"
    lan "第二句"
    return
''',
    '单属性': '''\
label ch01:
    show lan happy
    lan "第一句"
    lan "第二句"
    return
''',
    '无属性': '''\
label ch01:
    show lan
    lan "第一句"
    lan "第二句"
    return
''',
    '带 position': '''\
label ch01:
    show lan base_swimsuit happy at left
    lan "第一句"
    return
''',
}


def dump_sheet(table: Path):
    import openpyxl
    wb = openpyxl.load_workbook(str(table))
    ws = wb['Beats']
    heads = {str(c.value).strip(): i + 1 for i, c in enumerate(ws[1]) if c.value}
    out = []
    for r in range(2, ws.max_row + 1):
        kind = str(ws.cell(r, heads['类型']).value or '').strip()
        if not kind and not ws.cell(r, heads['立绘']).value:
            continue
        out.append({
            'row': r, 'kind': kind,
            '背景': ws.cell(r, heads['背景']).value,
            '立绘': ws.cell(r, heads['立绘']).value,
            '位置': ws.cell(r, heads['位置']).value,
            '舞台(高级)': ws.cell(r, heads['舞台（高级）']).value,
            '代码': ws.cell(r, heads['代码']).value,
        })
    return out


def run_case(name: str, body: str, work: Path) -> dict:
    root = work / name.replace('（', '_').replace('）', '')
    (root / 'game').mkdir(parents=True, exist_ok=True)
    src = root / 'game' / 'ch.rpy'
    src.write_text(body, encoding='utf-8')
    before = src.read_bytes()

    gen = tables.generate_table(src, project_root=root)
    table = gen['table_path']
    rows = dump_sheet(table)
    show_rows = [r for r in rows if r['kind'] != 'say']
    d = {'name': name, 'rows': show_rows}

    # B. 零改动往返（源 = 目标）
    res0 = tables.export_table(table, project_root=root, source_rpy=src)
    done0 = tables.apply_export(res0, project_root=root)
    d['roundtrip_written'] = bool(done0.get('written'))
    d['roundtrip_note'] = str(res0.get('note'))[:80]
    d['identical'] = src.read_bytes() == before
    d['after_roundtrip'] = src.read_text(encoding='utf-8')

    # C. 改一句台词后再导
    src.write_text(body, encoding='utf-8')      # 复位
    import openpyxl
    wb = openpyxl.load_workbook(str(table))
    ws = wb['Beats']
    heads = {str(c.value).strip(): i + 1 for i, c in enumerate(ws[1]) if c.value}
    for r in range(2, ws.max_row + 1):
        if str(ws.cell(r, heads['台词']).value or '').strip() == '第二句':
            ws.cell(r, heads['台词']).value = '第二句（改）'
            break
    wb.save(str(table))
    res1 = tables.export_table(table, project_root=root, source_rpy=src)
    done1 = tables.apply_export(res1, project_root=root)
    d['edit_written'] = bool(done1.get('written'))
    d['edit_note'] = str(res1.get('note'))[:80]
    d['after_edit'] = src.read_text(encoding='utf-8')
    d['show_line_after'] = [ln.strip() for ln in d['after_edit'].splitlines()
                            if ln.strip().startswith('show')]

    # D. 改**立绘列**：把表情换掉，看服装属性还在不在
    src.write_text(body, encoding='utf-8')
    wb = openpyxl.load_workbook(str(table))
    ws = wb['Beats']
    heads = {str(c.value).strip(): i + 1 for i, c in enumerate(ws[1]) if c.value}
    touched = None
    for r in range(2, ws.max_row + 1):
        cur = str(ws.cell(r, heads['立绘']).value or '').strip()
        if cur and 'happy' in cur:
            ws.cell(r, heads['立绘']).value = cur.replace('happy', 'sad')
            touched = cur
            break
    wb.save(str(table))
    res2 = tables.export_table(table, project_root=root, source_rpy=src)
    done2 = tables.apply_export(res2, project_root=root)
    d['sprite_before'] = touched
    d['sprite_written'] = bool(done2.get('written'))
    d['sprite_note'] = str(res2.get('note'))[:90]
    d['show_line_sprite'] = [ln.strip() for ln in
                             src.read_text(encoding='utf-8').splitlines()
                             if ln.strip().startswith('show')]
    return d


def main() -> int:
    results = []
    with tempfile.TemporaryDirectory(prefix='custom_') as td:
        work = Path(td)
        for name, body in BODIES.items():
            try:
                r = run_case(name, body, work)
            except Exception as e:              # noqa: BLE001
                import traceback
                traceback.print_exc()
                r = {'name': name, 'error': f'{type(e).__name__}: {e}'}
            results.append(r)
            print('=' * 78)
            print(f'[{name}]')
            if 'error' in r:
                print('  !! 抛异常：', r['error'])
                continue
            for x in r['rows']:
                print(f"  表第{x['row']}行 kind={x['kind']}")
                print(f"     背景={x['背景']!r}  立绘={x['立绘']!r}")
                print(f"     位置={x['位置']!r}")
                print(f"     舞台(高级)={str(x['舞台(高级)'])[:70]!r}")
                print(f"     代码={str(x['代码'])[:70]!r}")
            print(f"  B. 零改动往返：written={r['roundtrip_written']} "
                  f"逐字节={r['identical']}")
            print(f"     {r['roundtrip_note']}")
            print(f"  C. 改一句后：written={r['edit_written']}")
            print(f"     {r['edit_note']}")
            print(f"     show 行 = {r['show_line_after']}")
            print(f"  D. 改立绘列（happy→sad）：written={r.get('sprite_written')} "
                  f"原值={r.get('sprite_before')!r}")
            print(f"     {r.get('sprite_note')}")
            print(f"     show 行 = {r.get('show_line_sprite')}")

        print('\n' + '=' * 78)
        print(f'{"场景":22} {"零改动逐字节":12} {"改台词后 show"}  {"改立绘后 show"}')
        for r in results:
            if 'error' in r:
                print(f'{r["name"]:22} 抛异常')
                continue
            print(f'{r["name"]:22} {str(r["identical"]):12} '
                  f'{r["show_line_after"]}  {r.get("show_line_sprite")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
