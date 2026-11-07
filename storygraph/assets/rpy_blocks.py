"""
`.rpy` 里的 Python 块切分（2026-11-04 从 `asset_identity` 拆出）
================================================================

只做一件事：把 `init python:` / `python:` / `init -1 python:` /
`init python early:` 的**块体**切出来，交给 AST 层去读字面量。

为什么单独一个模块：`asset_identity.py` 已到 1500 行技术债红线，而"切
rpy 的 python 块"同时被"项目自定义代号"（`asset_identity.project_literal_words`）
和"图像声明扫描"（`assets.image_declarations`）使用 —— 放谁家都会让另一家
反向依赖。这里是叶子，没有依赖。
"""
from __future__ import annotations

import re

#: `init python:` / `python:` / `init -1 python:` 的块头
#: `python:` / `init python:` / `init -1 python:` / `init python early:`
#: （数字是**可选**的 —— 第一版写成 `init[ \t]+-?\d+[ \t]+` 之后，
#: 最普通的 `init python:` 反而不匹配，一个块都切不出来）
#:
#: 2026-11-04 地狱测试补的三条：
#:   · 行尾注释（`init python:  # 换装表`）以前整块被跳过；
#:   · `init +1 python:` 的 `+`；
#:   · 文件开头的 BOM（在 `python_blocks` 里剥）。
_PY_BLOCK_RE = re.compile(
    r'^(?P<indent>[ \t]*)(?:init(?:[ \t]+[-+]?\d+)?[ \t]+)?'
    r'python(?:[ \t]+early)?[ \t]*:[ \t]*(?:#[^\n]*)?$')


def python_blocks(text: str):
    """
    把 `.rpy` 里的 `init python:` / `python:` 块体切出来（块基础缩进已剥）。

    ⚠ **不能写死剥 4 格**（2026-11-04 地狱测试）：2 空格缩进是合法 Python，
    固定 `[base+4:]` 会把 `  _9QZ = {` 切成 `9QZ = {`（SyntaxError）→ 整块
    词表空转 → 卡片对项目自己定义的词误报。这里按**块体第一行的缩进**剥，
    4 格 / 2 格 / tab 都按作者写的来。
    """
    if text.startswith('\ufeff'):
        text = text[1:]
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        m = _PY_BLOCK_RE.match(lines[i])
        if not m:
            i += 1
            continue
        base = len(m.group('indent'))
        body = []
        prefix = ''
        j = i + 1
        while j < len(lines):
            ln = lines[j]
            if not ln.strip():
                body.append('')
                j += 1
                continue
            cur = len(ln) - len(ln.lstrip(' \t'))
            if cur <= base:
                break
            if prefix == '':
                prefix = ln[:cur]
            if ln.startswith(prefix):
                body.append(ln[len(prefix):])
            else:
                # 混用 tab/空格（Python 本身也不允许）：保守地只去行首空白
                body.append(ln.lstrip(' \t'))
            j += 1
        yield '\n'.join(body)
        i = j


#: 老名字（`asset_identity` 里原来的私有名）；判据/探针还在用。
_python_blocks = python_blocks

__all__ = ['python_blocks']
