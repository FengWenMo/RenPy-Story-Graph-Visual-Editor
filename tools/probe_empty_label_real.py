#!/usr/bin/env python
"""真实项目里，`label X:` 之后没有任何可渲染内容的 label 有多少？

（决定"空 label 被改名成 start"这个 bug 值不值得改结构去修：
 真实数据里一例都没有 → 记成已知边界即可；有几百例 → 必须修。）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')

LABEL_RE = re.compile(r'^(\s*)label\s+([A-Za-z_0-9]+)\s*:')


def scan(path: Path):
    """返回 [(label, 行号, 块内"有意义"的行数)]"""
    out = []
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    for i, ln in enumerate(lines):
        m = LABEL_RE.match(ln)
        if not m:
            continue
        name = m.group(2)
        base = len(m.group(1))
        body = 0
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                continue
            ind = len(nxt) - len(nxt.lstrip())
            if ind <= base and not nxt.lstrip().startswith('#'):
                break        # 回到顶层 → label 结束
            if nxt.strip().startswith('#'):
                continue
            body += 1
        out.append((name, i + 1, body))
    return out


def main() -> int:
    if not PROJECT.exists():
        print(f'（真实项目不在：{PROJECT} → 跳过）')
        return 0
    files = sorted((PROJECT / 'game').rglob('*.rpy'))
    total = empty = 0
    samples = []
    for f in files:
        try:
            got = scan(f)
        except Exception as e:                         # noqa: BLE001
            print(f'  ! {f.name}: {e}')
            continue
        for name, lineno, body in got:
            total += 1
            if body == 0:
                empty += 1
                if len(samples) < 12:
                    samples.append(f'{f.name}:{lineno} label {name}')
    print(f'真实项目 {PROJECT}')
    print(f'  .rpy 文件      {len(files)}')
    print(f'  label 总数     {total}')
    print(f'  空 label       {empty}')
    for s in samples:
        print('    ·', s)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
