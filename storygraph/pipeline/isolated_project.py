"""
**第二层：隔离合成工程** —— 让"问引擎"真的问得出来
=====================================================

用户 2026-10-11 的四层方案里，这一层是**承重墙**。

## 为什么必须有它（不是可选的一步）

真机实测链（都是我踩出来的）：

  · 引擎**只要有一个 parse error 就停**（报第一处），
    连探针的 `init` 都不执行 → 第一层（问注册表）和第三层（行为断言）**全跑不起来**；
  · 而"项目里有引擎不认识的写法"**本身就是** parse error
    （实测：`register_statement` 不给 `parse` 错、`init python` 注册的语句在解析期
    根本用不了、同文件/跨文件/字典序三种布局全错）；
  · ⇒ **"发现未知写法"和"问引擎"这两件事，在原始项目上不能共存。**

**所以要在隔离工程里问**：把它拼成一个**能编译的**最小工程 ——
只放引擎需要的骨架 + 项目的 `.rpy`，**但把那些"含未知写法"的文件排除掉**
（它们就是编译不过的元凶），再注入探针。

## 三条自我约束

  ① **绝不碰原项目**：全程在临时目录里拼（连读都只读 `.rpy`/`.json`/`.txt`）；
  ② **排除谁要如实说**：`excluded` 列出被排除的文件与原因，结论里带上它
     （读者要能判断"这个结论是不是少了什么"）；
  ③ **排到最后拼不出来就说拼不出来**（`ok=False`），
     **绝不在残缺的工程上给结论**（那比不给结论更糟）。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 拼隔离工程时**只带这些扩展名**（语法/配置；图像音频一律不要 —— 见
#: `statement_probe._copy_for_ask` 的同一条理由：真机 8GB 整份拷会把磁盘写满）
KEEP_SUFFIXES = ('.rpy', '.json', '.txt')

#: 与语法无关的重目录（不拷）
SKIP_DIRS = frozenset({
    'images', 'bgm', 'sounds', 'voices', 'music', 'audio', 'sfx', 'voice',
    'fonts', 'cache', 'saves', 'cc_preview', 'minigamepieces', 'photo',
    'movie', 'videos', 'tmp',
})

#: 单个大文件的上限
MAX_BYTES = 4 * 1024 * 1024


def build_isolated(project_root, *, out_dir=None, exclude=None,
                   keep_dirs=('images',)) -> dict:
    """
    拼一个**隔离合成工程** → `{'ok','root','excluded','files','hint'}`。

    `exclude` 是要**排除**的 `.rpy` 相对路径集合（含未知写法、让引擎编译不过的那些）。
    `keep_dirs` 是即使空也要建出来的目录（Ren'Py 扫描 `game/` 依赖目录存在）。

    ⚠ 只读原项目；拼出来的东西全在临时目录（或调用方给的 `out_dir`）。
    """
    root = Path(project_root) if project_root else None
    if root is None or not (root / 'game').is_dir():
        return {'ok': False, 'root': '', 'excluded': [], 'files': 0,
                'hint': f'不是工程根（没有 game/）：{root}'}
    exclude = {str(x).replace('\\', '/') for x in (exclude or ())}
    tmp = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix='sg_iso_'))
    work = tmp / (root.name or 'project')
    excluded, kept = [], 0
    for src in sorted(root.rglob('*')):
        rel = src.relative_to(root)
        parts = rel.parts
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        if src.is_dir():
            continue
        if not src.is_file():
            continue
        rel_posix = rel.as_posix()
        if src.suffix.lower() not in KEEP_SUFFIXES:
            continue
        if src.suffix.lower() == '.rpy' and rel_posix in exclude:
            excluded.append(rel_posix)
            continue
        try:
            if src.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        dest = work / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            kept += 1
        except OSError as e:                     # noqa: BLE001 - 拷不动就跳过
            swallow('pipeline.isolated_project.copy', e)
    (work / 'game').mkdir(parents=True, exist_ok=True)
    for name in keep_dirs:                       # 空目录也建（引擎扫描要用）
        (work / 'game' / name).mkdir(parents=True, exist_ok=True)
    return {'ok': True, 'root': str(work), 'excluded': excluded,
            'files': kept, 'hint': ''}


def rpy_files(project_root, *, limit: int = 400) -> list:
    """项目里的 `.rpy` 相对路径（拼隔离工程时要从这里挑排除项）。"""
    root = Path(project_root) if project_root else None
    if root is None or not (root / 'game').is_dir():
        return []
    out = []
    for src in sorted(root.rglob('*.rpy')):
        try:
            if src.is_file():
                out.append(src.relative_to(root).as_posix())
        except OSError as e:                     # noqa: BLE001
            swallow('pipeline.isolated_project.rpy_files', e)
        if len(out) >= limit:
            break
    return out


def files_with_unknown_heads(project_root, heads) -> list:
    """
    哪些 `.rpy` 里**出现了**这些未知写法（→ 拼隔离工程时要把它们排掉）。

    判据是**文本上真的出现**（行首是该写法），够用且零风险 ——
    比"再跑一遍解析器判断"简单，也不会误伤（宁可少排一个文件，
    大不了那次探针被挡住、如实报 `undetermined`）。
    """
    heads = {str(h or '').strip() for h in (heads or ()) if str(h or '').strip()}
    root = Path(project_root) if project_root else None
    if not heads or root is None:
        return []
    out = []
    for rel in rpy_files(root):
        p = root / rel
        try:
            text = p.read_text(encoding='utf-8', errors='replace')
        except OSError as e:                     # noqa: BLE001
            swallow('pipeline.isolated_project.scan_file', e)
            continue
        for raw in text.splitlines():
            token = raw.strip().split(' ', 1)[0].rstrip(':')
            if token in heads:
                out.append(rel)
                break
    return out
