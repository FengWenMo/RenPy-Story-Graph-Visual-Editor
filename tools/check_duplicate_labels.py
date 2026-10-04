"""
检查合并产物里有没有**重复 label**（同一个文件内部重名）。

为什么单独一个入口：门禁 `LABEL_COLLISION` 只查"产物 vs 项目其他 .rpy"
（`generation_guard.occupied_labels`），**同一个产物内部**的重名它看不出来 ——
而那同样会让 Ren'Py 直接起不来。这是已知的**门禁缺口**，
所以先用一个可跑的检查把它量住（真实项目实测：46 个 label / 0 处重名）。

用法：

    D:/Python/python.exe tools/check_duplicate_labels.py          # 真实项目
    D:/Python/python.exe tools/check_duplicate_labels.py --tiny   # 自造小工程
"""
from __future__ import annotations

import collections
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
sys.path.insert(0, str(ROOT))

from storygraph.pipeline.build import build_project  # noqa: E402


def _count(text: str) -> collections.Counter:
    return collections.Counter(re.findall(r'^label\s+([^\s:#]+)', text, re.M))


def _tiny(td: Path) -> tuple:
    """
    自造一个**容易暴露问题**的三文件工程：

        a_defs.rpy    只有 `init python:`（0 张拍）
        m_ch1.rpy     `label ch1`      ← 把定义文件与 start 组**隔开**
        z_start.rpy   `label start`

    "隔开"是关键：携带拍（label 为空 → 分组时按 `start` 走）与真正的
    `label start` **相邻**时会并进同一组、看不出问题；中间夹着别的 label
    才会分成两组。这也是"纯定义文件不该产生空壳 label"那条的判据来源。
    """
    proj = td / 'tiny'
    (proj / 'game').mkdir(parents=True, exist_ok=True)
    (proj / 'game' / 'a_defs.rpy').write_text(
        'init python:\n    LONE = 1\n', encoding='utf-8')
    (proj / 'game' / 'm_ch1.rpy').write_text(
        'label ch1:\n    e "one"\n', encoding='utf-8')
    (proj / 'game' / 'z_start.rpy').write_text(
        'label start:\n    e "go"\n', encoding='utf-8')
    out_dir = td / 'tiny_out'
    build_project(proj, out_dir=out_dir)
    return (out_dir / 'story.generated.rpy').read_text(encoding='utf-8')


def _report(text: str, title: str) -> int:
    counter = _count(text)
    dupes = {k: v for k, v in counter.items() if v > 1}
    print(f'{title}：产物 {len(text.splitlines())} 行，'
          f'label {sum(counter.values())} 个')
    # 注意计数口径：这里走 `build_project`，而它用
    # `discover_scripts`（排除 `*.generated.rpy` / `tl/`）。
    # 所以真实项目这里是 50，而"逐文件加总"是 51 —— 差的那个在
    # `chapter1_4.generated.rpy` 里，不是丢了。
    print('  重复 label：', dupes if dupes else '无 ✓')
    if dupes:
        for ln in text.splitlines():
            if ln.startswith('label '):
                print('   ', ln)
    return 1 if dupes else 0


def main() -> int:
    if '--tiny' in sys.argv:
        with tempfile.TemporaryDirectory(prefix='duptiny_') as td:
            return _report(_tiny(Path(td)), '自造小工程')

    if not REAL.exists():
        print('真实项目不在，跳过')
        return 0
    with tempfile.TemporaryDirectory(prefix='dupcheck_') as td:
        out_dir = Path(td) / 'out'
        res = build_project(REAL, out_dir=out_dir)
        rpy = out_dir / 'story.generated.rpy'
        if not rpy.exists():
            print('没有产物（门禁拦下了？）')
            print(res.summary())
            return 1
        text = rpy.read_text(encoding='utf-8')
    return _report(text, '真实项目')


if __name__ == '__main__':
    raise SystemExit(main())
