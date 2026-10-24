"""
探针：真机项目里 `python:` 块到底占多少？里面装的又是什么？
==========================================================================

回答一个问题：「工具不介入 `python:` 块内部，局限性到底有多大？」

不猜，直接量真机（`D:/千连万理/RenpyTest/sr_general`）：

  · 有多少个**叙事层**的 `python:` 块（label 里那种；`init python:` /
    `python hide:` / screen 里的不算）；
  · 每个块里有多少条语句；
  · 这些语句**分几类** —— 关键是：有多少条其实**应该在脚本层写出来**
    （`narrator(...)` / `renpy.say(...)` 这类叙事语句），
    有多少条是脚本层能直接表达的赋值，
    有多少条是只有 Python 才写得出来的（for / while / 函数调用）。

用法：
    D:/Python/python.exe tools/_probe_pyblocks_real.py [项目根]
"""
import ast
import re
import sys
import textwrap
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = r'D:/千连万理/RenpyTest/sr_general'

#: 叙事语句：这些出现在 python 块里 = 演出被关在了代码里
NARRATIVE_CALLS = {
    'narrator', 'say', 'renpy.say', 'renpy.show', 'renpy.hide',
    'renpy.scene', 'renpy.with_statement', 'renpy.pause',
    'renpy.play', 'renpy.music.play', 'renpy.sound.play',
    'renpy.voice',
}
#: 这些出现在 python 块里是有道理的（存档/IO/容器操作）
KEEPABLE_CALLS = {'renpy.checkpoint', 'open', 'print', 'len', 'range',
                  'sorted', 'sum', 'min', 'max', 'int', 'str', 'float',
                  'dict', 'list', 'set', 'tuple', 'enumerate', 'zip',
                  'renpy.random.randint', 'renpy.random.choice'}


def _container_before(lines, upto):
    """
    这一行之前最近的"容器"（`label:` / `screen ...:` / `init python:`）。
    返回 'label' / 'screen' / 'init' / ''。
    """
    for up in range(upto - 1, -1, -1):
        s = lines[up].strip()
        if not s or s.startswith('#'):
            continue
        if re.match(r'^label\s+\w+\s*:', s):
            return 'label'
        if re.match(r'^screen\s+\w+', s):
            return 'screen'
        if s.startswith('init ') or s.startswith('init:'):
            return 'init'
    return ''


def _collect_blocks(lines):
    """
    找出**叙事层的** `python:` 块：`[(起, 止)]`（行下标，半开）。
    `init python:` / `python hide:` / screen 里的、以及 `init python in ...:`
    一律不算 —— 那些本来就该是 Python。
    """
    out = []
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not (s == 'python:' or s.startswith('python ')):
            continue
        # `python hide:` / `python in xxx:` 不算叙事块
        if s != 'python:':
            continue
        # `init python:`（同一行上带 init）不算；上面已排除（s != 'python:'）
        if _container_before(lines, i) != 'label':
            continue
        indent = len(raw) - len(raw.lstrip())
        end = len(lines)
        for j in range(i + 1, len(lines)):
            t = lines[j]
            if not t.strip():
                continue
            if len(t) - len(t.lstrip()) <= indent:
                end = j
                break
        out.append((i + 1, end))
    return out


def _classify(body):
    """
    一个块体的语句分类。返回 `{类别: 条数}`。

    分类只数**顶层语句**（块里的第一层），嵌套在 for/if 里的算一条
    `控制流`（整块搬不走）。
    """
    counts = Counter()
    try:
        tree = ast.parse(body)
    except SyntaxError:
        counts['（解析不了）'] += 1
        return counts
    for node in tree.body:
        if isinstance(node, (ast.For, ast.While, ast.If, ast.Try,
                             ast.With, ast.FunctionDef)):
            counts['控制流/复合（整块搬不走）'] += 1
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            counts['赋值（脚本层 $ 能表达）'] += 1
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            name = _call_name(node.value.func)
            if name in NARRATIVE_CALLS:
                counts['叙事语句（本该写在脚本层）'] += 1
            elif name in KEEPABLE_CALLS:
                counts['工具/IO 调用（留在 Python 合理）'] += 1
            else:
                counts['其它函数调用'] += 1
        else:
            counts['其它'] += 1
    return counts


def _call_name(func):
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return '.'.join(reversed(parts)) or '?'


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT)
    game = root / 'game'
    base = game if game.is_dir() else root
    files = sorted(base.rglob('*.rpy'))
    if not files:
        print(f'没找到 .rpy：{base}')
        return 1

    total_lines = 0
    total_blocks = 0
    total_stmts = 0
    kinds = Counter()
    per_file = []
    samples = []

    for fp in files:
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except Exception as e:                       # noqa: BLE001
            print(f'  读不了 {fp.name}：{e}')
            continue
        lines = text.splitlines()
        total_lines += len(lines)
        blocks = _collect_blocks(lines)
        if not blocks:
            continue
        n_stmt = 0
        for a, b in blocks:
            body = textwrap.dedent('\n'.join(lines[a:b]))
            c = _classify(body)
            kinds.update(c)
            n_stmt += sum(c.values())
            if len(samples) < 6 and c.get('叙事语句（本该写在脚本层）'):
                samples.append((fp, a + 1, body.splitlines(), c))
        total_blocks += len(blocks)
        total_stmts += n_stmt
        per_file.append((fp.relative_to(base), len(blocks), n_stmt))

    print('=' * 72)
    print(f'真机：{root}')
    print('=' * 72)
    print(f'  .rpy 文件        {len(files)}')
    print(f'  总行数           {total_lines}')
    print(f'  叙事层 python: 块 {total_blocks}')
    print(f'  块内语句         {total_stmts}')
    if total_lines:
        print(f'  块占行数比       —— （下面按行数再算一次）')

    print('\n--- 块内语句分类（顶层语句，一条复合语句算一条）---')
    for k, v in kinds.most_common():
        print(f'  {v:>5}  {k}')

    print('\n--- 有 python 块的文件（前 15，按语句数排）---')
    per_file.sort(key=lambda x: -x[2])
    for name, nb, ns in per_file[:15]:
        print(f'  {ns:>4} 条 / {nb:>2} 块   {name}')

    if samples:
        print('\n--- 抽样：块里真的有叙事语句的（前 6 个）---')
        for fp, ln, body, c in samples:
            print(f'\n  {fp.name}:{ln}')
            for b in body[:6]:
                print(f'      {b}')
            print(f'      分类：{dict(c)}')

    # 行数占比
    block_lines = 0
    for fp in files:
        try:
            lines = fp.read_text(encoding='utf-8',
                                 errors='replace').splitlines()
        except Exception:                            # noqa: BLE001
            continue
        for a, b in _collect_blocks(lines):
            block_lines += (b - a)
    if total_lines:
        print(f'\n--- 占比 ---')
        print(f'  python 块行数       {block_lines}')
        print(f'  总行数              {total_lines}')
        print(f'  占比                {block_lines / total_lines:.2%}')
        print(f'  每千行有            '
              f'{block_lines / total_lines * 1000:.1f} 行在 python 块里')
    return 0


if __name__ == '__main__':
    sys.exit(main())
