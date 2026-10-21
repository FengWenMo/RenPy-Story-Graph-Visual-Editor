"""
探针：**重复导出 / 只读文件 / 怪路径 / 规模** —— 四个"日常但没人量"的面。

判据（任何一条不成立都算 P0，因为用户会按这个节奏操作）：

  A. **幂等**：同一张表连导 3 次，第 2、3 次的产物必须与第 1 次**逐字节相同**。
     不然每导一次文件就变一次 —— 用户会以为工具在乱改，最后不敢用。
  B. **只读**：源文件只读时，导出必须**报错**，不许报"已写入"。
  C. **怪路径**：目录名带空格 + 中文 + 括号，往返要正常。
  D. **规模**：1000 拍的章节，建表 + 导出能不能在可接受时间内完成，
     且拍数不丢。

用法：
    D:/Python/python.exe tools/probe_idempotence_and_io.py
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app import tables                      # noqa: E402

BASE = '''\
label ch01:
    e "第一句"
    e "第二句"
    e "第三句"
    return
'''


def _mk(root: Path, name='ch.rpy', body=BASE) -> Path:
    (root / 'game').mkdir(parents=True, exist_ok=True)
    fp = root / 'game' / name
    fp.write_text(body, encoding='utf-8')
    return fp


def _roundtrip(root: Path, src: Path) -> dict:
    gen = tables.generate_table(src, project_root=root)
    if not gen.get('ok'):
        return {'ok': False, 'why': gen.get('error')}
    res = tables.export_table(gen['table_path'], project_root=root,
                              source_rpy=src)
    done = tables.apply_export(res, project_root=root)
    return {'ok': bool(done.get('written')), 'note': res.get('note', ''),
            'patch': bool(res.get('used_patch')), 'error': done.get('error')}


def case_idempotent(td: Path) -> list:
    print('\n' + '=' * 74)
    print('A. 幂等：同一张表连导 3 次（中间不再改表）')
    root = td / 'idem'
    src = _mk(root)
    gen = tables.generate_table(src, project_root=root)
    table = gen['table_path']
    snaps = []
    for i in range(1, 4):
        res = tables.export_table(table, project_root=root, source_rpy=src)
        done = tables.apply_export(res, project_root=root)
        b = src.read_bytes()
        snaps.append(b)
        print(f'  第 {i} 次：written={done.get("written")} '
              f'patch={res.get("used_patch")} {b.count(b"\\n")} 行 '
              f'{len(b)} 字节  sha={hash(b) % 10**8}')
        print(f'         {str(res.get("note"))[:90]}')
    same = snaps[0] == snaps[1] == snaps[2]
    print(f'  三次逐字节相同 : {same}')
    print(f'  第2次与第1次    : {snaps[1] == snaps[0]}')
    print(f'  第3次与第2次    : {snaps[2] == snaps[1]}')
    print(f'  最终内容：{snaps[-1].decode("utf-8")!r}')
    return [('幂等', same, '' if same else '三次导出结果不一致')]


def case_readonly(td: Path) -> list:
    print('\n' + '=' * 74)
    print('B. 只读源文件：必须报错，不许谎报"已写入"')
    root = td / 'ro'
    src = _mk(root)
    before = src.read_bytes()
    try:
        os.chmod(src, stat.S_IREAD)
    except OSError as e:
        print(f'  设不了只读（{e}），跳过')
        return []
    try:
        r = _roundtrip(root, src)
    finally:
        try:
            os.chmod(src, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
    after = src.read_bytes()
    print(f'  written = {r["ok"]}  error = {str(r.get("error"))[:110]}')
    print(f'  源文件被改了吗 = {after != before}')
    ok = (not r['ok']) and after == before
    if r['ok'] and after != before:
        print('  !! 报了成功、文件也真被改了（只读是怎么写进去的？）')
        ok = False
    return [('只读文件', ok, '' if ok else f'written={r["ok"]} 改动={after != before}')]


def case_weird_path(td: Path) -> list:
    print('\n' + '=' * 74)
    print('C. 怪路径：目录名带空格 + 中文 + 括号')
    root = td / '我的 项目 (2026) v2'
    src = _mk(root)
    r = _roundtrip(root, src)
    text = src.read_text(encoding='utf-8')
    ok = r['ok'] and '第一句' in text and '第三句' in text
    print(f'  written={r["ok"]}  path={src}')
    print(f'  {str(r.get("note"))[:90]}')
    print(f'  内容还在 : {"第一句" in text}')
    return [('怪路径', ok, '' if ok else str(r.get('error'))[:60])]


def case_scale(td: Path) -> list:
    print('\n' + '=' * 74)
    print('D. 规模：1000 拍的章节')
    root = td / 'big'
    lines = ['label ch01:']
    for i in range(1000):
        lines.append(f'    e "第 {i} 句台词，用来量规模。"')
    lines.append('    return')
    src = _mk(root, body='\n'.join(lines) + '\n')
    t0 = time.time()
    r = _roundtrip(root, src)
    dt = time.time() - t0
    text = src.read_text(encoding='utf-8')
    n = text.count('句台词')
    print(f'  往返耗时 = {dt:.1f}s   written={r["ok"]}')
    print(f'  {str(r.get("note"))[:100]}')
    print(f'  产物里还剩 {n} 句（应为 1000）')
    ok = r['ok'] and n == 1000 and dt < 120
    return [('规模 1000 拍', ok, f'{dt:.0f}s / {n} 句')]


def main() -> int:
    rows = []
    with tempfile.TemporaryDirectory(prefix='idem_') as td:
        work = Path(td)
        for fn in (case_idempotent, case_readonly, case_weird_path, case_scale):
            try:
                rows.extend(fn(work))
            except Exception as e:              # noqa: BLE001
                import traceback
                traceback.print_exc()
                rows.append((fn.__name__, False, f'{type(e).__name__}: {e}'))
    print('\n' + '=' * 74)
    print(f'{"判据":16} {"结果":6} 说明')
    for name, ok, why in rows:
        print(f'{name:16} {"PASS" if ok else "FAIL":6} {why}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
