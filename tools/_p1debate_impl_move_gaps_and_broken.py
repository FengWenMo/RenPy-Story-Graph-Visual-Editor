"""P1 讨论·实现派探针②b+④：移动拍"认不出的行"点名 + write_asset_excel 坏表行为。

§1 移动被拒里"重排段里有认不出的行"的那几对 —— 把空隙行原文点名
§2 write_asset_excel：坏表（zip 损坏）→ 隔离重建后用户内容去向
§3 损坏 xlsx 的 zip 级抢救可行性（sharedStrings + sheet XML 直读）
§4 被 Excel 锁住（PermissionError 模拟）时的现状

跑法： /d/Python/python tools/_p1debate_impl_move_gaps_and_broken.py
"""
from __future__ import annotations

import io
import re
import shutil
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = '[PASS]', '[FAIL]'
results = []


def check(name, ok, detail=''):
    results.append((bool(ok), name))
    print(f'{PASS if ok else FAIL} {name}')
    if detail:
        for line in str(detail).splitlines():
            print(f'       {line}')


POMO = Path(r'D:\千连万理\RenpyTest\sr_general\game\pomodorro\script_pomo.rpy')


def load_session(src: Path):
    from storygraph.app.editor import EditorSession
    tmp = Path(tempfile.mkdtemp(prefix='p1g_'))
    game = tmp / 'game'
    game.mkdir(parents=True)
    dst = game / src.name
    shutil.copyfile(src, dst)
    s = EditorSession(project_root=tmp)
    s.load(dst)
    return s, dst, tmp


def section1():
    print('=' * 68)
    print('§1 "认不出的行"逐对点名（真机）')
    print('=' * 68)
    if not POMO.exists():
        print('  [SKIP] 真机文件不在')
        return
    from storygraph.story.patcher import (
        _plan_reorders, _norm, _LABEL_LINE_RE)
    s, dst, tmp = load_session(POMO)
    try:
        n = len(s.beats)
        named = 0
        for a in range(min(n - 1, 80)):
            s2, dst2, tmp2 = load_session(POMO)
            try:
                s2.move_beat(a, a + 1)
                res = s2.compile()
                from storygraph.story.patcher import patch_export
                pr = patch_export(s2.beats, dst2, res.rpy_text,
                                  registry=s2.registry(),
                                  symbols=s2.symbols, trust_ranges=True)
                why = '；'.join(pr.reasons)
                if pr.ok or '认不出的行' not in why:
                    continue
                # 现场还原：重排段的空隙行
                orig_lines = dst2.read_text(encoding='utf-8').splitlines()
                # 找出这一对涉及的区间
                moved = [b for b in s2.beats
                         if (b.src_first or 0) and (b.src_last or 0)]
                firsts = sorted((b.src_first, b.src_last)
                                for b in s2.beats if (b.src_first or 0))
                inv = set()
                seq = [b for b in s2.beats if (b.src_first or 0)]
                fs = [b.src_first for b in seq]
                for j in range(len(fs)):
                    for k in range(j):
                        if fs[k] > fs[j]:
                            inv.add(j); inv.add(k)
                if not inv:
                    continue
                span_f = min(fs[j] for j in inv)
                span_l = max(seq[j].src_last for j in inv)
                gaps = []
                slots = sorted(((b.src_first, b.src_last) for b in seq
                                if span_f <= b.src_first
                                and b.src_last <= span_l))
                for (f1, l1), (f2, l2) in zip(slots, slots[1:]):
                    for ln in range(l1 + 1, f2):
                        t = _norm(orig_lines[ln - 1]).strip()
                        if t and not t.startswith('#') \
                                and not _LABEL_LINE_RE.match(orig_lines[ln - 1]):
                            gaps.append((ln, t))
                named += 1
                print(f'  对 ({a},{a+1}) 拍[{getattr(seq[a], "text", "")[:12]!r}]'
                      f' span={span_f}-{span_l} 空隙行：')
                for ln, t in gaps:
                    print(f'    L{ln}: {t[:70]!r}  '
                          f'冒号结尾={t.endswith(":")}')
                if named >= 6:
                    break
            finally:
                shutil.rmtree(tmp2, ignore_errors=True)
        check('"认不出的行"已被点名', named > 0, f'共 {named} 对')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def section2():
    print()
    print('=' * 68)
    print('§2 write_asset_excel：坏表（zip 截断）→ 现状')
    print('=' * 68)
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import PatternFill
    from storygraph.assets.asset_registry import (
        ensure_asset_tables, write_asset_excel)

    tmp = Path(tempfile.mkdtemp(prefix='p1broken_'))
    try:
        tables = tmp / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        imgs = tmp / 'game' / 'images' / 'chars'
        imgs.mkdir(parents=True)
        for name in ('a.png', 'b.png'):
            (imgs / name).write_bytes(b'\x89PNG\r\n\x1a\n')
        xlsx = tables / 'images.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['编号', '真实文件', '标签', '立绘属性', '类别', '备注',
                   '维度', '属性值', '配音演员'])
        ws.append(['a', 'game/images/chars/a.png', 'a', '', 'character',
                   '第一张', '', '', '小林'])
        ws.append(['b', 'game/images/chars/b.png', 'b', '', 'character',
                   '', '', '', ''])
        ws.append(['future', '', 'future', '', 'character', '还没画',
                   '', '', '待定'])
        ws.cell(row=2, column=1).fill = PatternFill('solid', fgColor='FFF2CC')
        ws.cell(row=2, column=1).comment = Comment('这张要重画', '策划')
        extra = wb.create_sheet('分类说明')
        extra['A1'] = 'character = 角色立绘'
        wb.save(xlsx)
        # 截断成坏表（保留前 60% 字节 → zip 损坏）
        data = xlsx.read_bytes()
        xlsx.write_bytes(data[:int(len(data) * 0.6)])
        broken_size = xlsx.stat().st_size

        res = ensure_asset_tables(tmp, with_scripts=False)
        print(f'  ensure: ok={res.get("ok")} repaired={res.get("repaired")} '
              f'repair_error={(res.get("repair_error") or "")[:60]}')
        after = {p.name: p.stat().st_size
                 for p in tables.glob('*.xlsx')}
        print(f'  表目录现在：{after}（坏表原本 {broken_size} 字节）')
        # 新表里还有没有用户内容？
        new = tables / 'images.xlsx'
        if new.exists():
            wb2 = openpyxl.load_workbook(new)
            ws2 = wb2.active
            rows = [[c.value for c in r] for r in ws2.iter_rows()]
            has_voice = any('配音演员' in (str(h) for h in r) for r in rows[:1])
            has_future = any('future' in (str(v) for v in r) for r in rows)
            has_sheets = wb2.sheetnames
            marks = ws2.cell(row=2, column=1).comment
            print(f'  新表：配音演员列={has_voice} future行={has_future} '
                  f'页={has_sheets} 批注={marks}')
            check('坏表重建后用户列/行/页/批注全没（=问题现场）',
                  not (has_voice and has_future and len(has_sheets) > 1
                       and marks),
                  f'voice={has_voice} future={has_future} '
                  f'sheets={has_sheets} marks={marks}')
        broken_copies = list(tables.glob('*.broken-*.xlsx'))
        check('隔离副本存在（现状：只隔离不保住）', bool(broken_copies),
              f'{[p.name for p in broken_copies]}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def section3():
    print()
    print('=' * 68)
    print('§3 zip 级抢救：坏 xlsx 还能不能读出用户行')
    print('=' * 68)
    import openpyxl
    tmp = Path(tempfile.mkdtemp(prefix='p1salv_'))
    try:
        xlsx = tmp / 'images.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['编号', '真实文件', '备注', '配音演员'])
        ws.append(['a', 'game/a.png', '第一张', '小林'])
        ws.append(['future', '', '还没画', '待定'])
        wb.save(xlsx)
        data = xlsx.read_bytes()
        # 场景 A：截断 zip 尾部（中心目录损坏）
        a_path = tmp / 'trunc.xlsx'
        a_path.write_bytes(data[:int(len(data) * 0.7)])
        # 场景 B：只把 workbook.xml 弄坏（sheet/sharedStrings 完好）
        b_path = tmp / 'badwb.xlsx'
        with zipfile.ZipFile(xlsx) as zin:
            with zipfile.ZipFile(b_path, 'w') as zout:
                for item in zin.namelist():
                    blob = zin.read(item)
                    if item == 'xl/workbook.xml':
                        blob = b'<broken'
                    zout.writestr(item, blob)
        # 场景 C：把 styles.xml 删掉（openpyxl 可能拒载）
        c_path = tmp / 'nostyle.xlsx'
        with zipfile.ZipFile(xlsx) as zin:
            with zipfile.ZipFile(c_path, 'w') as zout:
                for item in zin.namelist():
                    if item == 'xl/styles.xml':
                        continue
                    zout.writestr(item, zin.read(item))

        def zip_salvage(path):
            """直读 sharedStrings + 第一张 sheet 的 XML。"""
            try:
                z = zipfile.ZipFile(path)
            except Exception as e:               # noqa: BLE001
                return None, f'zip 打不开：{type(e).__name__}'
            names = z.namelist()
            shared = []
            if 'xl/sharedStrings.xml' in names:
                root = ElementTree.fromstring(z.read('xl/sharedStrings.xml'))
                ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
                for si in root.findall(f'{ns}si'):
                    shared.append(''.join(t.text or ''
                                          for t in si.iter(f'{ns}t')))
            sheet_name = next((n for n in names
                               if re.match(r'xl/worksheets/sheet\d+\.xml$', n)),
                              None)
            if not sheet_name:
                return None, '没有 sheet'
            try:
                root = ElementTree.fromstring(z.read(sheet_name))
            except Exception as e:               # noqa: BLE001
                return None, f'sheet 解析失败：{type(e).__name__}'
            ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
            rows = []
            for row in root.iter(f'{ns}row'):
                vals = []
                for c in row.findall(f'{ns}c'):
                    v = c.find(f'{ns}v')
                    t = c.get('t')
                    if v is None:
                        vals.append('')
                    elif t == 's':
                        vals.append(shared[int(v.text)])
                    else:
                        vals.append(v.text)
                rows.append(vals)
            return rows, ''

        for label, path in (('截断 zip', a_path), ('workbook 坏',
                                                   b_path),
                            ('缺 styles', c_path)):
            rows, err = zip_salvage(path)
            # 对照 openpyxl
            try:
                openpyxl.load_workbook(path)
                ox = '可载'
            except Exception as e:               # noqa: BLE001
                ox = f'{type(e).__name__}'
            n = len(rows) if rows else 0
            print(f'  {label}: openpyxl={ox} zip抢救={n}行 {err}')
            if rows:
                for r in rows[:4]:
                    print(f'      {r}')
        # 判据：openpyxl 打不开的两种，zip 级至少一种能救出数据
        rows_b, _ = zip_salvage(b_path)
        rows_c, _ = zip_salvage(c_path)
        try:
            openpyxl.load_workbook(b_path)
            ox_b = True
        except Exception:                       # noqa: BLE001
            ox_b = False
        check('zip 级抢救能在 openpyxl 拒载时读回用户行',
              (rows_b and len(rows_b) >= 3) or (rows_c and len(rows_c) >= 3),
              f'rows_b={len(rows_b or [])} rows_c={len(rows_c or [])} '
              f'openpyxl_b={ox_b}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def section4():
    print()
    print('=' * 68)
    print('§4 表被占用（模拟 PermissionError）时的现状')
    print('='*68)
    import openpyxl
    from storygraph.assets import asset_registry as AR
    tmp = Path(tempfile.mkdtemp(prefix='p1lock_'))
    try:
        tables = tmp / 'game' / 'asset_tables'
        tables.mkdir(parents=True)
        imgs = tmp / 'game' / 'images' / 'chars'
        imgs.mkdir(parents=True)
        (imgs / 'a.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        xlsx = tables / 'images.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['编号', '真实文件', '配音演员'])
        ws.append(['a', 'game/images/chars/a.png', '小林'])
        wb.save(xlsx)

        real_load = openpyxl.load_workbook

        def locked_load(*a, **kw):
            raise PermissionError(13, '被 Excel 占用')

        openpyxl.load_workbook = locked_load
        try:
            res = AR.ensure_asset_tables(tmp, with_scripts=False)
        finally:
            openpyxl.load_workbook = real_load
        print(f'  ensure: ok={res.get("ok")} error={(res.get("error") or "")[:80]}'
              f' repaired={res.get("repaired")}')
        after = {p.name: p.stat().st_size for p in tables.glob('*.xlsx')}
        print(f'  表目录现在：{after}')
        # 二次：只读失败发生在 write_dict_to_excel 的 snapshot
        # （即 parse 过了、写回时才锁）—— 略，主路径已覆盖
        check('被占用时报错而非静默重建（现状是否做到）',
              bool(res.get('error')) or bool(res.get('repair_error')),
              f'error={res.get("error")!r} repair={res.get("repair_error")!r}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    section1()
    section2()
    section3()
    section4()
    bad = [ok for ok, _ in results if not ok]
    print()
    print(f'汇总：{len(results) - len(bad)}/{len(results)} 通过')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
