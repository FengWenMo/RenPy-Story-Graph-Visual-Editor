"""
变异验证：把 `_python_syntax_issues` **打回逐行编译**，看守卫测试会不会红。

为什么要留一个可复现的变异入口（本仓库的惯例是"变异验证过的断言才算数"）：
`tests/test_python_syntax_guard_granularity.py` 的整条价值就是"挡住那个
假阳性"，而假阳性恰恰是**改回去很容易、看起来还很合理**的那种改动
（逐行编译读起来更"直接"）。所以证明"打回去会红"比证明"现在是绿的"更重要。

用法（从**仓库根**执行）：

    set PYTHONPATH=tools
    python -m pytest tests/test_python_syntax_guard_granularity.py -q -p mut_python_syntax_guard

（POSIX：`PYTHONPATH=tools python -m pytest ...`）

`PYTHONPATH` 只要指向 `tools/` —— 那是为了让 pytest 的 `-p` 找得到本模块。
`import storygraph` 由本文件自己把仓库根插进 `sys.path`（见下），
**不依赖 conftest 的注入时机**：只给仓库根（`PYTHONPATH=.`）会报
`No module named 'mut_python_syntax_guard'`，而只靠 conftest 把根塞进
`sys.path` 在不同 pytest 版本上并不保证。

期望：`TestNoFalsePositives` 里 **4 条红**（多行列表 / for / if-else /
多行函数调用），真错那几条仍绿。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 `import storygraph` 不依赖 conftest 的 sys.path 注入（见 docstring）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from storygraph.story import generation_guard as gg  # noqa: E402


def _line_by_line(lines):
    """旧行为：块体一行算一个块。"""
    i, n = 0, len(lines)
    while i < n:
        head = lines[i].strip()
        if not gg._PY_BLOCK_HEAD_RE.match(head):
            i += 1
            continue
        yield i + 1, lines[i], [lines[i + 1]] if i + 1 < n else []
        i += 1


gg._iter_python_blocks = _line_by_line


def pytest_configure(config):
    print('\n[mutation] 守卫已打回逐行编译（旧行为）\n')
