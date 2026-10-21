"""
tests/test_py310_syntax.py
==========================
**源码必须是 Python 3.10 能解析的**（CI 的 3.10 job 就在这上面挂过一次）。

2026-10-22 实测：`beat_merge.py` 里写了

    f'… else f"⚠ {c["conflicts"]} 处冲突"'

—— f-string 里嵌**同种引号**，那是 Python **3.12** 才允许的（PEP 701）。
3.10 / 3.11 直接在**收集阶段** SyntaxError，整个 3.10 job 一条测试都跑不了，
而本地（3.12）全绿，看不出任何异常。

`ast.parse(feature_version=(3, 10))` **抓不到这个**：`feature_version`
只开关语法特性，不换 tokenizer —— 引号嵌套是 tokenizer 行为，3.12 的
tokenizer 一律接受。所以这里只能用文本层面的判据。

判据（两条正则，足够窄，不误伤）：
  · `f"` 开头的 f-string，`{}` 里出现 `名字["`  → 内层双引号
  · `f'` 开头的 f-string，`{}` 里出现 `名字['`  → 内层单引号

反例（**不许**被这条抓到）：
  · `f"QFrame[role=\\"toast\\"] {{"` —— 引号是**转义**的，3.10 合法；
  · `f'img{i}', {'Path': …}` —— 那对引号在 f-string **外面**（正则要求
    出现在同一个 `{}` 里，且 `{` 前面已有同种引号的 f 前缀）。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 扫这些目录（`legacy/` 是历史单文件，不参与构建，跳过）
SCAN_DIRS = ('storygraph', 'tests', 'tools')

#: `f"…{ x["key"] …}` —— 内层双引号与外层相同
DQ = re.compile(r'f"[^{}\n]*\{[^{}\n]*\w\["')
#: `f'…{ x['key'] …}` —— 内层单引号与外层相同
SQ = re.compile(r"f'[^{}\n]*\{[^{}\n]*\w\['")


#: 这个文件自己就存着反例字符串，扫它等于自己打自己
_SELF = Path(__file__).resolve()


def _scan(pattern):
    hits = []
    for d in SCAN_DIRS:
        for p in (ROOT / d).rglob('*.py'):
            if p.resolve() == _SELF:
                continue
            try:
                text = p.read_text(encoding='utf-8-sig', errors='replace')
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith('#'):
                    continue          # 注释里的示例代码不算
                if pattern.search(line):
                    hits.append(f'{p.relative_to(ROOT)}:{i}  {line.strip()[:90]}')
    return hits


def test_no_double_quote_nested_in_fstring():
    hits = _scan(DQ)
    assert not hits, (
        'f-string 里嵌了同种引号（Python 3.12 才允许，3.10/3.11 会 SyntaxError）：\n'
        + '\n'.join(hits))


def test_no_single_quote_nested_in_fstring():
    hits = _scan(SQ)
    assert not hits, (
        'f-string 里嵌了同种引号（Python 3.12 才允许，3.10/3.11 会 SyntaxError）：\n'
        + '\n'.join(hits))


def test_the_checker_itself_works():
    """守卫自己要能抓到反例 —— 否则"0 处"可能只是正则写错了。"""
    bad = 'x = f"a {d["k"]} b"'
    assert DQ.search(bad), '正则抓不到反例，这条守卫是假的'
    bad2 = "y = f'a {d['k']} b'"
    assert SQ.search(bad2)
    # 转义的引号是合法的，不许误报
    ok = r'CSS = f"QFrame[role=\"toast\"] {{"'
    assert not DQ.search(ok), '转义引号被误报了'
    # f-string 外面的字典字面量，不许误报
    ok2 = "_Change('removed', 'images', f'img{i}', {'Path': f'{i}.png'}, {})"
    assert not SQ.search(ok2), 'f-string 外面的引号被误报了'
