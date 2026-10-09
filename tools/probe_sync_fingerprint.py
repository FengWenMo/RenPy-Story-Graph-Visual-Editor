"""sync_asset_tables 行为指纹：**重构前后必须一字不差**（M4 的 oracle）。

做法：搭一个**确定性的合成项目**（`backup/game/` 是嵌套副本），把四种现场
一次性摆进去（文件没了 / 人工占位 / 工具加过指向嵌套副本 / 指向副本但本项目
也有 → 改指 / 未登记 → 补登），跑 `sync_asset_tables`，然后

  · 把所有资源表**逐格**读出来算一个稳定 hash；
  · 把返回的 dict（去掉 registry 这种大对象）规范化成一行。

两次运行的 `FINGERPRINT` 必须一字不差。

    python tools/probe_sync_fingerprint.py            # 打印指纹
    python tools/probe_sync_fingerprint.py --save f   # 存到文件
    python tools/probe_sync_fingerprint.py --check f  # 与文件比对
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl                                              # noqa: E402

from storygraph.assets.asset_registry import (               # noqa: E402
    FileWalkIndex, _autoscan_index_path, _STANDARD_TABLES,
    append_rows_to_table, ensure_asset_tables, sync_asset_tables)


def _img(path: Path, color=(30, 120, 200, 255)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (6, 6), color).save(path, 'PNG')
    return path


def build_project(base: Path) -> Path:
    """确定性合成项目：把 sync 的四个阶段全摆上。"""
    root = base / 'proj'
    game = root / 'game'
    (game / 'asset_tables').mkdir(parents=True)

    # —— 磁盘上的文件 ——
    _img(game / 'images' / 'ling' / 'happy.png')            # 已登记且存在
    _img(game / 'images' / 'bg' / 'classroom.png')          # 未登记 → 补登
    (game / 'bgm').mkdir(parents=True, exist_ok=True)
    (game / 'bgm' / 'night.ogg').write_bytes(b'OggS' + b'\0' * 64)
    (game / 'chapter').mkdir(parents=True, exist_ok=True)
    (game / 'chapter' / 'ch01.rpy').write_text(
        'define e = Character("Ai", image="ling")\n'
        'label ch01:\n    e "hi"\n', encoding='utf-8')

    # —— 嵌套副本（项目根下另有一份带 game/ 的树）——
    _img(root / 'backup' / 'game' / 'images' / 'ling' / 'happy.png',
         (200, 60, 60, 255))
    _img(root / 'backup' / 'game' / 'images' / 'junk1.png', (60, 200, 60, 255))

    ensure_asset_tables(root)
    table_dir = game / 'asset_tables'
    # 建表**之后**才出现的文件 → 才是"未登记 → 补登"（❹）的现场。
    _img(game / 'images' / 'new' / 'added.png', (120, 120, 30, 255))
    (game / 'bgm' / 'late.ogg').write_bytes(b'OggS' + b'\0' * 32)

    # —— 表里摆四种现场 ——
    append_rows_to_table(table_dir, 'images', [
        {'ID': 'ling_happy', 'Path': 'game/images/ling/happy.png',
         'Tag': 'ling', 'Category': 'characters'},
        {'ID': 'gone_row', 'Path': 'game/images/gone.png'},        # 文件没了 → 删
        {'ID': 'ph_row', 'Path': 'game/images/todo.png',           # 人工占位 → 留
         'Notes': '占位：等出图'},
        {'ID': 'nested_same',                                      # 改指 → 与上面合并
         'Path': 'backup/game/images/ling/happy.png'},
    ], backup=False)
    # 工具自己加过、且指向嵌套副本的行（侧车记着才算"我的"）
    append_rows_to_table(table_dir, 'images', [
        {'ID': 'junk1', 'Path': 'backup/game/images/junk1.png'},
    ], backup=False)
    _autoscan_index_path(table_dir).write_text(json.dumps({
        'added': {'images': ['backup/game/images/junk1.png']},
    }, ensure_ascii=False), encoding='utf-8')
    return root


def table_fingerprint(table_dir: Path, base: Path | None = None) -> str:
    h = hashlib.sha256()
    for name in sorted(_STANDARD_TABLES.values()):
        p = table_dir / name
        if not p.is_file():
            h.update(f'{name}:MISSING\n'.encode())
            continue
        ws = openpyxl.load_workbook(p).active
        for r, row in enumerate(ws.iter_rows(values_only=True), 1):
            for c, v in enumerate(row, 1):
                # ⚠ 单元格里也会有**绝对路径**（扫出来的行 Path 是绝对路径）
                # → 不归一化的话每次跑都不一致（实测踩到）。
                v = _norm(v, base) if isinstance(v, str) else v
                h.update(f'{name}|{r}|{c}|{type(v).__name__}|{v}\n'.encode())
    return h.hexdigest()


def _norm(obj, base: Path):
    """
    把临时目录的**绝对路径**归一化掉（含各种转义层级）。

    ⚠ 为什么要递归 + 多种变体：`reconcile.table_resolved` 的值是
    **set 的 repr 字符串**，里面 Windows 路径的反斜杠是**双写**的 ——
    只按单反斜杠替换会一条都匹配不上（第一版就是这么废掉的）。
    """
    variants = []
    if base is not None:
        s = str(base)
        variants = [s, s.replace('\\', '\\\\'), s.replace('\\', '/'),
                    s.replace('/', '\\'), s.replace('/', '\\\\')]
    if isinstance(obj, str):
        for v in variants:
            obj = obj.replace(v, '<BASE>')
        # ⚠ **set 的 repr 顺序是随机的**（`table_resolved` 里就是 set 的
        # repr 字符串）→ 不排序的话指纹每跑一次都不一样（实测踩到）。
        s = obj.strip()
        if s.startswith('{') and s.endswith('}'):
            inner = s[1:-1].strip()
            if inner:
                parts = sorted(p.strip() for p in inner.split(','))
                obj = '{' + ', '.join(parts) + '}'
        return obj
    if isinstance(obj, dict):
        return {k: _norm(v, base) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_norm(v, base) for v in obj]
    return obj


def result_fingerprint(res: dict, base: Path | None = None) -> str:
    slim = {k: v for k, v in (res or {}).items() if k != 'registry'}
    # 先过一遍 json（`default=str` 把 set/Path 之类折成字符串），再递归归一。
    slim = json.loads(json.dumps(slim, ensure_ascii=False, default=str))
    slim = _norm(slim, base)
    return json.dumps(slim, ensure_ascii=False, sort_keys=True)


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix='sync_fp_'))
    try:
        root = build_project(base)
        res = sync_asset_tables(root, walk=FileWalkIndex(root))
        tf = table_fingerprint(root / 'game' / 'asset_tables', base)
        rf = result_fingerprint(res, base)
        out = {
            'table_hash': tf,
            'result': rf,
            'fingerprint': hashlib.sha256(
                (tf + rf).encode()).hexdigest(),
        }
        line = json.dumps(out, ensure_ascii=False, sort_keys=True)
        print('FINGERPRINT=' + out['fingerprint'])
        print(line[:1500])

        argv = sys.argv[1:]
        if len(argv) == 2 and argv[0] == '--save':
            Path(argv[1]).write_text(out['fingerprint'] + '\n',
                                     encoding='utf-8')
            print(f'saved -> {argv[1]}')
        elif len(argv) == 2 and argv[0] == '--check':
            want = Path(argv[1]).read_text(encoding='utf-8').strip()
            ok = (want == out['fingerprint'])
            print('MATCH' if ok else f'MISMATCH want={want}')
            return 0 if ok else 1
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
