"""
storygraph.parsing.discover
===========================
**发现项目里该被构建的 .rpy**（章节脚本清单）。

为什么它住在这里（2026-09-28 下移）
------------------------------------
它原先写在 `pipeline/build.py` 里，但**生成门禁**（`story/generation_guard.py`
的 `occupied_labels`，用来判"生成的 label 是否与项目其他 .rpy 重名"）也需要
它——于是 story 层反过来 import pipeline 层，形成 `story ↔ pipeline` 的环，
只能靠函数内延迟 import 压着。

把"扫出 .rpy 清单"这件事下移到 parsing 层后，依赖方向恢复单向：
`parsing ← story ← pipeline`。`pipeline/build.py` 仍然 re-export 它，
既有调用方（`cli.py` / `story` / 测试）无需改动。
"""
from __future__ import annotations

import re
from pathlib import Path

#: 这些目录名出现在路径里就跳过：翻译产物、引擎自带目录、缓存。
SKIP_DIR_PARTS = frozenset({
    'tl', 'common', 'renpy', 'cache', 'saves', '__pycache__',
})

#: 本工具自己生成的产物后缀：不能被当成"项目原文"扫进来（否则自我循环）。
GENERATED_SUFFIX = '.generated.rpy'

#: `label 名字:`（行首、允许缩进）。只用来回答"这个 label 在哪个文件里"，
#: 不替代解析器 —— 跑一次正则比解析 40 个文件快得多，而且这个问题
#: （"还缺哪个文件"）只在用户导入时问一次。
#:
#: 名字允许点号分段（`main.route`）：解析器本来就是 `[\w.]+`，发现层
#: 从前只认 `\w+`，于是这类 label 导入后永远补全不到（2026-10-22 实测）。
_LABEL_RE = re.compile(
    r'^[ \t]*label[ \t]+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'
    r'[ \t]*(?:\([^)]*\))?[ \t]*:', re.MULTILINE)

#: `jump 名字` / `call 名字`（含 `call X from Y` 那种）。同样只用于
#: "谁引用了这一章"这种导航性问题，不做语法判断。
_REF_RE = re.compile(
    r'^[ \t]*(?:jump|call)[ \t]+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)',
    re.MULTILINE)


def discover_scripts(project_root, extra_dirs=None) -> list:
    """
    找出需要构建的章节脚本（返回 Path 列表，已排序、稳定）。

    扫描范围：有 `game/` 就只扫它，否则扫项目根；extra_dirs 追加。
    排除：`tl/`（翻译）、`common/` `renpy/` `cache/` `saves/` `__pycache__/`
    （引擎自带或缓存）、以及 `*.generated.rpy`（本工具的产物）。
    """
    project_root = Path(project_root)
    game = project_root / 'game'
    roots = [game] if game.exists() else [project_root]
    for d in (extra_dirs or []):
        roots.append(Path(d))

    found = []
    for root in roots:
        if not root.exists():
            continue
        for fp in sorted(root.rglob('*.rpy')):
            rel_parts = set(fp.relative_to(root).parts)
            if rel_parts & SKIP_DIR_PARTS:
                continue
            if fp.name.endswith(GENERATED_SUFFIX):
                continue
            found.append(fp)
    return found


def find_label_files(project_root, labels) -> dict:
    """
    `{label 名: 声明它的 .rpy}` —— 只回答"这个 label 在哪个文件里"。

    用途（用户 2026-10-03："我连线呢！？"）：剧情图上出现了**没有源文件的
    占位块** —— 别的章节 `jump/call` 到了它，但那个文件还没被导入，
    所以连线画不出来。这时要能一句话告诉用户"还缺哪个文件"，
    并且支持一键把它们一起导入。

    实现刻意用**正则扫一遍**而不是解析器：这个问题只在导入/提示时问一次，
    40 个小文件跑正则远快于跑一遍完整解析；而且它不需要 AST 级别的准确度
    （找不到就归到 `unknown`，由调用方如实告知）。

    返回里只包含**找得到**的 label；调用方自己算差集 = 找不到的那些。
    """
    wanted = {str(x) for x in (labels or ()) if str(x)}
    if not wanted:
        return {}
    found: dict = {}
    for fp in discover_scripts(project_root):
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for m in _LABEL_RE.finditer(text):
            name = m.group(1)
            if name in wanted and name not in found:
                found[name] = fp
        if len(found) == len(wanted):
            break
    return found


def find_files_referencing_labels(project_root, labels) -> dict:
    """
    `{label 名: [引用它的 .rpy, ...]}` —— 谁 `jump`/`call` 了这些 label。

    和 `find_label_files` 是**两个方向**，两个都要（用户 2026-10-03 实测）：

      · 声明方向 → 图上有**占位块**时用（别的章节跳到它，文件没导入）；
      · 引用方向 → 导入的章节**自己不含跳转**时用（串起它们的是另一个
        文件，比如 `chapter_controller.rpy`）。

    只做声明方向的话，`chapter1_1/1_2/1_3` 这种情况会**什么都提示不出来**
    —— 因为它们自己没跳转、也没人跳到它们之外的缺失文件，
    结果就是用户对着一张没有线的图继续猜"我连线呢？"。
    """
    wanted = {str(x) for x in (labels or ()) if str(x)}
    if not wanted:
        return {}
    found: dict = {}
    for fp in discover_scripts(project_root):
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for m in _REF_RE.finditer(text):
            name = m.group(1)
            if name in wanted:
                found.setdefault(name, [])
                if fp not in found[name]:
                    found[name].append(fp)
    return found


__all__ = ['discover_scripts', 'find_label_files',
           'find_files_referencing_labels',
           'SKIP_DIR_PARTS', 'GENERATED_SUFFIX']
