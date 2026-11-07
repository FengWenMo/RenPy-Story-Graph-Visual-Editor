"""
项目 `.rpy` 里**直接声明过的图像名** → `{tag: [属性词, …]}`
================================================================

为什么单开（2026-11-04 地狱测试）：立绘提示的"声明层"以前只读
`layeredimage` 的 `attribute`，于是下面三种**引擎都认**的写法会被误报
"没有这个属性"：

    image lan based = "images/characters/lan/base.png"
    image lan based:
        "…/base.png"
    init python:
        renpy.image("lan based", "…/base.png")

真跑引擎 lint 验证过：这三种 `show lan based` 都合法（见
`docs/立绘提示检测器_地狱测试_2026-11-04.md`）。所以这一路只做一件事：
把**字面量图像名**里的 tag / 属性词收进来。

只认字面量：`renpy.image("%s" % name, …)`、`image lan [x] = …` 一律跳过
（猜一个名字比说"不知道"坏得多）。
"""
from __future__ import annotations

import re
from pathlib import Path

from storygraph.assets.rpy_blocks import python_blocks
from storygraph.core.safeguard import swallow

#: `image <名字> = <displayable>` / `image <名字>:` 的声明行。
#: 名字允许 Unicode（引擎实测 `show lan 符_77` 合法）与点号（`bg.room`）。
_IMAGE_DECL_RE = re.compile(
    r'^[ \t]*image[ \t]+(?P<name>[^=:#\n"\']+?)[ \t]*(?:=|:)')
#: 图像名的每一段：引擎的 image name 词（`[\w.]+`，Unicode 也算）。
_IMAGE_WORD_RE = re.compile(r'[\w.]+')


def _image_words_of_name(name: str):
    """`"lan based"` → `('lan', ['based'])`；不是合法图像名 → `None`。"""
    name = str(name or '').strip()
    if not name:
        return None
    parts = name.split()
    if len(parts) < 2:
        return None
    if not all(_IMAGE_WORD_RE.fullmatch(p) for p in parts):
        return None
    return parts[0], parts[1:]


def image_statement_words(project_root) -> dict:
    """项目 `.rpy` 里直接声明过的图像名 → `{tag: [属性词, …]}`（见模块说明）。"""
    root = str(project_root or '')
    if not root:
        return {}
    try:
        from storygraph.parsing.discover import discover_scripts
        files = list(discover_scripts(root))
    except Exception as e:                      # noqa: BLE001 - 读不出来就没这一路
        swallow('assets.image_declarations.discover', e)
        return {}
    out: dict = {}
    for fp in files:
        try:
            text = Path(fp).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if text.startswith('\ufeff'):
            text = text[1:]
        for line in text.split('\n'):
            m = _IMAGE_DECL_RE.match(line)
            if m:
                parsed = _image_words_of_name(m.group('name'))
                if parsed:
                    _add_image_words(out, *parsed)
        # 运行期注册：`renpy.image("lan based", …)`（引擎 lint 认）
        for block in python_blocks(text):
            for name in _renpy_image_names(block):
                parsed = _image_words_of_name(name)
                if parsed:
                    _add_image_words(out, *parsed)
    return out


def _add_image_words(out: dict, tag: str, attrs):
    bucket = out.setdefault(str(tag), [])
    for attr in attrs:
        if attr and attr not in bucket:
            bucket.append(attr)


def _renpy_image_names(code: str) -> list:
    """一段 Python 代码里 `renpy.image("字面量名", …)` 的字面量名。"""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == 'image'
                and isinstance(func.value, ast.Name)
                and func.value.id == 'renpy'):
            continue
        cand = node.args[0] if node.args else None
        if cand is None:
            for kw in node.keywords:
                if kw.arg == 'name':
                    cand = kw.value
                    break
        if isinstance(cand, ast.Constant) and isinstance(cand.value, str):
            names.append(cand.value)
    return names


__all__ = ['image_statement_words']
