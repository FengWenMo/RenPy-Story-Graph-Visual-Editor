# -*- coding: utf-8 -*-
"""
探针：**资源表健壮性压测**（2026-11-02）

问的不是"正常输入对不对"，而是：用户 / Excel / 同步盘把表弄脏之后，
工具是**崩溃**、**静默改用户数据**，还是**说人话并保住能保住的部分**。

场景（每个都在独立临时项目里跑，绝不碰真机目录）：
  A 重复 Path（两行指向同一个文件）
  B 重复 ID（两行不同文件、同一个 ID）
  C ID 为空 / Path 为空
  D Path 写成绝对路径
  E Path 大小写与磁盘不一致（Windows 上应视为同一个文件）
  F Path 指向目录
  G 表头缺 Path 列（人删了一列）
  H 用户自加一列（不应被抹掉）
  I ID 是数字（Excel 自动把 `001` 变成 `1`）
  J 表文件被占用（PermissionError）→ 同步时不许崩、不许半写
  K 表是 0 字节坏文件

跑法：
    D:\\Python\\python.exe tools\\_probe_asset_robustness_20261102.py
输出：每个场景一行结论 + 一行证据。
"""
from __future__ import annotations

import base64
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl  # noqa: E402

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA'
    'hKmMIQAAAABJRU5ErkJggg==')


def make_project(root: Path) -> Path:
    """最小 renpy 项目：game/images/bg/bg01.png + game/audio/bgm/theme.ogg。"""
    (root / 'game' / 'images' / 'bg').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'audio' / 'bgm').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'images' / 'bg' / 'bg01.png').write_bytes(PNG)
    (root / 'game' / 'images' / 'bg' / 'BG02.png').write_bytes(PNG)
    (root / 'game' / 'audio' / 'bgm' / 'theme.ogg').write_bytes(b'OggS')
    (root / 'game' / 'script.rpy').write_text(
        'label start:\n    "hi"\n    return\n', encoding='utf-8')
    return root


def tables_dir(root: Path) -> Path:
    from storygraph.assets.asset_registry import asset_table_dirs
    dirs = asset_table_dirs(root)
    return Path(dirs[0]) if dirs else root / 'game' / 'assets'


def image_table(root: Path) -> Path:
    from storygraph.assets.asset_registry import find_asset_tables, is_real_table_file
    d = find_asset_tables(root)
    if d is None:
        raise RuntimeError('没建出资源表')
    files = [p for p in Path(d).glob('*.xlsx') if is_real_table_file(p)]
    for p in files:
        if 'image' in p.name.lower() or '图' in p.name:
            return p
    return files[0]


def read_rows(path: Path) -> tuple:
    wb = openpyxl.load_workbook(path, data_only=False)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    rows = [list(r) for r in ws.iter_rows(min_row=2, values_only=True)]
    wb.close()
    return headers, rows


def rewrite(path: Path, headers: list, rows: list) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Images'
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)
    wb.close()


def build(root: Path) -> Path:
    from storygraph.assets.asset_registry import ensure_asset_tables
    make_project(root)
    ensure_asset_tables(root)
    return image_table(root)


#: 表头既可能是英文键，也可能是中文标签（同一个项目里两种都有）。
_ALIAS = {
    'ID': ['ID', '编号'],
    'Path': ['Path', '真实文件'],
    'Tag': ['Tag', '标签'],
    'Category': ['Category', '类别'],
    'Notes': ['Notes', '备注'],
    'Group': ['Group', '维度'],
    'Value': ['Value', '属性值'],
    'Attributes': ['Attributes', '立绘属性'],
}


def col(headers, name, default=None):
    """列号（1 基）。⚠ **找不到就抛** —— 从前 `default=1` 会把改动悄悄
    落到「编号」列上，于是"Path 指目录"这个场景测的其实是"ID 是路径"。"""
    for cand in _ALIAS.get(name, [name]):
        try:
            return headers.index(cand) + 1
        except ValueError:
            continue
    if default is not None:
        return default
    raise KeyError(f'表里没有 {name} 列（表头={headers}）')


def run_case(name: str, mutate) -> None:
    """mutate(table_path, headers, rows) → 可返回说明串。"""
    from storygraph.assets.asset_registry import sync_asset_tables
    from storygraph.assets.registry_load import load_asset_registry
    root = Path(tempfile.mkdtemp(prefix='sgrb_'))
    try:
        table = build(root)
        headers, rows = read_rows(table)
        note = mutate(table, headers, rows) or ''
        print(f'\n### {name}')
        if note:
            print(f'    改动：{note}')

        # 1) 同步（打开项目时走的那条）
        try:
            rep = sync_asset_tables(root)
            ok = rep.get('ok')

            def _brief(v):
                """`removed`/`added` 有时是 dict、有时是计数 —— 两种都要能打。"""
                if isinstance(v, dict):
                    return {k: (len(x) if not isinstance(x, int) else x)
                            for k, x in v.items()}
                return v
            print(f'    sync  ok={ok} 删={_brief(rep.get("removed"))} '
                  f'增={_brief(rep.get("added"))} '
                  f'err={str(rep.get("error") or rep.get("repair_error") or "")[:70]!r}')
        except Exception as e:                        # noqa: BLE001
            print(f'    sync  ✗ 抛异常 {type(e).__name__}: {e}')
            traceback.print_exc(limit=4)

        # 2) 读表（面板/选择器走的那条）
        try:
            reg = load_asset_registry(root)
            imgs = reg.get_asset_ids('image') if hasattr(reg, 'get_asset_ids') \
                else []
            unusable = {k: dict(v) for k, v in
                        (getattr(reg, 'unusable', {}) or {}).items() if v}
            short = {k: list(v.items())[:2] for k, v in unusable.items()}
            print(f'    load  image={len(imgs)} 条 unusable={short}')
            errs = [str(e) for e in (getattr(reg, 'errors', []) or [])][:3]
            warns = [str(w) for w in (getattr(reg, 'warnings', []) or [])][:3]
            print(f'          errors={errs} warnings={warns}')
            print(f'          前 5 个 ID={imgs[:5]}')
        except Exception as e:                         # noqa: BLE001
            print(f'    load  ✗ 抛异常 {type(e).__name__}: {e}')
            traceback.print_exc(limit=2)

        # 3) 文件有没有被改（"只读"场景要能自证没动）
        try:
            h2, r2 = read_rows(table)
            if (h2, r2) != (headers, rows):
                print(f'    表被改写：{len(rows)} 行 → {len(r2)} 行；'
                      f'表头 {headers} → {h2}')
        except Exception as e:                         # noqa: BLE001
            print(f'    表读不回来了：{type(e).__name__}: {e}')
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    def dup_path(table, headers, rows):
        c_id, c_path = col(headers, 'ID'), col(headers, 'Path')
        if len(rows) >= 1:
            rows.append(list(rows[0]))
            rows[-1][c_id - 1] = f'{rows[0][c_id - 1]}_copy'
            rewrite(table, headers, rows)
        return f'把第 1 行的 Path 复制成第 {len(rows)} 行'

    def dup_id(table, headers, rows):
        c_id, c_path = col(headers, 'ID'), col(headers, 'Path')
        rows.append(list(rows[-1]))
        rows[-1][c_id - 1] = rows[0][c_id - 1]
        rows[-1][c_path - 1] = 'game/images/bg/BG02.png'
        rewrite(table, headers, rows)
        return f'ID 重复成 {rows[0][c_id - 1]!r}，Path 指另一个真实文件'

    def empty_id(table, headers, rows):
        c_id = col(headers, 'ID')
        rows.append(['', 'game/images/bg/BG02.png'] + [None] * (len(headers) - 2))
        rewrite(table, headers, rows)
        return '新行 ID 为空、Path 真实'

    def empty_path(table, headers, rows):
        c_id, c_path = col(headers, 'ID'), col(headers, 'Path')
        rows.append(['ghost', ''] + [None] * (len(headers) - 2))
        rewrite(table, headers, rows)
        return '新行 Path 为空'

    def abs_path(table, headers, rows):
        c_path = col(headers, 'Path')
        rows[0][c_path - 1] = str(table.parents[2] / 'game/images/bg/bg01.png')
        rewrite(table, headers, rows)
        return f'Path 写成绝对路径 {rows[0][c_path - 1]!r}'

    def case_mismatch(table, headers, rows):
        c_path = col(headers, 'Path')
        rows[0][c_path - 1] = 'GAME/IMAGES/BG/BG01.PNG'
        rewrite(table, headers, rows)
        return 'Path 大小写与磁盘不一致'

    def path_is_dir(table, headers, rows):
        c_path = col(headers, 'Path')
        rows[0][c_path - 1] = 'game/images/bg'
        rewrite(table, headers, rows)
        return 'Path 指向目录'

    def drop_path_col(table, headers, rows):
        i = col(headers, 'Path') - 1
        heads = [h for k, h in enumerate(headers) if k != i]
        new_rows = [[v for k, v in enumerate(r) if k != i] for r in rows]
        rewrite(table, heads, new_rows)
        return '把 Path 列整列删掉'

    def extra_column(table, headers, rows):
        rewrite(table, headers + ['美术负责人'],
                [list(r) + ['小王'] for r in rows])
        return '追加一列「美术负责人」并填了值'

    def numeric_id(table, headers, rows):
        c_id = col(headers, 'ID')
        rows[0][c_id - 1] = 123
        rewrite(table, headers, rows)
        return '把某一行的 ID 改成数字 123'

    def locked(table, headers, rows):
        # 用独占句柄模拟"Excel 开着这张表"
        fh = open(table, 'rb')
        try:
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except Exception:                              # noqa: BLE001
            pass
        globals()['_LOCK_FH'] = fh
        return '表被独占打开（模拟 Excel 占用）'

    def corrupt(table, headers, rows):
        table.write_bytes(b'')
        return '把表写成 0 字节'

    def col_lower(table, headers, rows):
        # 表头被写成小写英文（老表 / 手改）
        rewrite(table, [str(h).lower() if h else h for h in headers], rows)
        return '表头改成全小写'

    for name, fn in [
        ('A 重复 Path', dup_path),
        ('B 重复 ID', dup_id),
        ('C1 ID 为空', empty_id),
        ('C2 Path 为空', empty_path),
        ('D 绝对路径', abs_path),
        ('E 大小写不一致', case_mismatch),
        ('F Path 是目录', path_is_dir),
        ('G 缺 Path 列', drop_path_col),
        ('H 用户自加列', extra_column),
        ('I ID 是数字', numeric_id),
        ('J 表被占用', locked),
        ('K 坏文件', corrupt),
        ('L 表头全小写', col_lower),
    ]:
        try:
            run_case(name, fn)
        except Exception as e:                          # noqa: BLE001
            print(f'\n### {name}\n    ✗ 探针自己炸了：{type(e).__name__}: {e}')
            traceback.print_exc(limit=3)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
