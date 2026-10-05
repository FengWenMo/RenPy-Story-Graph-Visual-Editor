"""
统计真实项目里"say 语句带尾从句（`with` / `id`）"的行 —— 一条**已知边界**。

为什么要数：解析器只取 `who` + 第一个字符串，尾从句（`e "x" with dissolve` /
`"系统" "x" id line_001`）**既不入模型、也不进 raw**，于是全量路径上直接丢。
这条不在本轮修的 P0 范围内（P0 是"字符串说话人把正文吃掉"），但同属"say 尾部"，
先量出真实暴露面，免得下次当新 bug 猜。

    python tools/probe_say_tail.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general\game')

#: `who what with|id …`：who 可以是标识符或字符串字面量
_SAY_TAIL = re.compile(
    r'^\s*(?:[A-Za-z_][\w.]*|"[^"]*"|\'[^\']*\')'
    r'\s+(?:"[^"]*"|\'[^\']*\')'
    r'\s+(with|id)\b')


def main() -> int:
    hits = []
    for p in sorted(REAL.rglob('*.rpy')):
        if p.name.endswith('.generated.rpy'):
            continue
        for i, ln in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            if _SAY_TAIL.match(ln):
                hits.append((p.name, i, ln.strip()[:80]))
    print(f'真实项目里 "say + with/id 从句" 的行：{len(hits)} 处')
    for h in hits[:10]:
        print('   ', h)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
