"""
变异验证：把单行列的清洗**关掉**，看 `tests/test_cell_control_chars.py` 会不会红。

为什么要留一个可复现的变异入口：这条修复的本质是"**在正确的层**挡住控制字符"，
而"少一层清洗"看起来完全无害（谁会在单元格里打换行呢）——
所以证明"关掉它会红"比证明"现在是绿的"更重要。

用法（从**仓库根**执行）：

    set PYTHONPATH=tools
    python -m pytest tests/test_cell_control_chars.py -q -p mut_cell_sanitizer

（POSIX：`PYTHONPATH=tools python -m pytest ...`）

`PYTHONPATH` 只要指向 `tools/` —— 这是为了让 pytest 的 `-p` 找得到本模块。
`import storygraph` 由**本文件自己**把仓库根插进 `sys.path`（见下），
不依赖 conftest 的时机：实测 `PYTHONPATH=.`（只给仓库根）会报
`No module named 'mut_cell_sanitizer'`，而只靠 conftest 把根塞进 `sys.path`
在不同 pytest 版本上并不保证 —— 两件事各归各管最稳。

期望：`TestSingleLineCellsAreFlattened` / `TestCleaningIsReported` 里**多条红**
（会写出物理换行或被拆成两条语句，且清洗不再留痕 → W114 不报），
`TestMultiLineCellsKeepTheirNewlines` 仍绿（多行列本来就不该被压）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 `import storygraph` 不依赖 conftest 的 sys.path 注入（见 docstring）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from storygraph.story import beat_model as bm  # noqa: E402


def _identity(field, value):
    """旧行为：单行列原样收下（控制字符随它去）。"""
    return str(value or '')


bm.sanitize_single_line_cell = _identity


def pytest_configure(config):
    print('\n[mutation] 单行列清洗已关闭（旧行为）\n')
