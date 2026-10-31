"""对比两个几乎相同的项目在「工具还不认识的写法」上的判定差异。只读。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storygraph.story.statement_registry import (StatementRegistry,
                                                 discover_undeclared)

PROJECTS = [
    r'D:\千连万理\RenPyTest\sr_general',
    r'D:\千连万理\sr_general',
]


def main() -> int:
    for root in PROJECTS:
        print('=' * 70)
        print('项目:', root)
        print('有 rules.json:', (Path(root) / 'storygraph.rules.json').exists())
        try:
            reg = StatementRegistry.for_project(root)
            names = [getattr(d, 'name', d) for d in (reg.declared or ())]
            print('已声明(已认识)条数:', len(names), names)
        except Exception as e:                       # noqa: BLE001
            print('读 registry 失败:', e)
        t0 = time.time()
        found = discover_undeclared(root, limit_files=400)
        dt = time.time() - t0
        print(f'未认识的写法: {len(found)} 条（耗时 {dt:.1f}s）')
        for rec in found[:15]:
            print(f"   {rec['head']!r} x{rec['count']}  block={rec['block']}  "
                  f"files={rec['files'][:3]}")
        if not found:
            print('   （空 —— 与界面「没发现未声明的写法」一致）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
