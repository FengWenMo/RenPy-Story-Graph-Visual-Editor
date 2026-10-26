"""
storygraph.story.scan_cache
===========================

"这个项目长什么样"的读盘结果缓存（**只读**，按 `mtime+size` 失效）。

## 为什么要有这一层（2026-10-26，用户："每一次连线都很卡……优化一下算法"）

接线窗口一打开就要回答几个**项目级**的问题：编排层是哪个文件、这一章的
`_control` 包装在不在、谁 `jump`/`call` 了这一章。它们分别落在
`detect_placement` / `find_control_file` / `_label_decl` / `_incoming_refs`
里 —— 而每一个都是"读一遍全部 `.rpy`"，`detect_placement` 还要把每个文件
**整份解析**（不是正则扫，是完整 AST）。

真机账（`sr_general`，91 个 `.rpy` / 1.2 MB，2026-10-26 实测）：

| 调用 | 改之前 |
|---|---|
| `detect_placement(root)` | 1435 ms |
| `find_control_file(root)`（无 src） | 1498 ms |
| `plan_control_route`，起点章没有 `_control` 包装 | 3389 ms |
| 一次连线（窗口构造 + 点「创建」） | 10~20 s |

关键事实：**同一次接线里这些文件一个字节都没变**。这几个函数互相调用
（`plan_control_route` → `find_control_file` ×1 + `plan_control_init` →
`find_control_file` ×2 + `_label_decl` ×2 + `_incoming_refs` ×2），于是同一份
几百 KB 的脚本在一次操作里被解析/读了好几遍。缓存它们不是"猜得更快"，
是把**同一次操作里重复的读盘去掉**。

## 失效是**内容驱动**的，不是按时间猜

没有 TTL。指纹 = `((路径, mtime_ns, size), ...)` 全表：任何文件被改
（本工具自己写回、用户在外面用编辑器改），指纹就变，缓存自然失效。
`discover()` 那一层看得更粗（只看目录 mtime + 文件还在不在）—— 它只回答
"有哪些 `.rpy`"，目录里**新建/删除文件**才会改答案，而那正好会改目录 mtime。

## 契约（用的人要守）

- 这里给的都是**读到的快照**：`read_text` 拿到的 `text` 只适合"看"和
  "算计划"，**写盘必须重新读**（`SourceStyle.apply(source=路径)` 会严格
  解码并核对原文，那是唯一末班车）。
- 返回的容器是**拷贝**（`labels_of` 是 tuple，`file_facts` / `_label_decl` /
  `_incoming_refs` 的缓存版各自拷贝）——调用方随便改，不会污染缓存。
- 单线程使用（Qt 主线程 / CLI 各一份）；内部仍加锁，只防"哪天有人在
  线程里问一句"。
"""
from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict
from dataclasses import replace as _dc_replace
from pathlib import Path

#: 每个缓存最多留多少条。几百章的项目也够；换项目时旧条目自然被挤掉。
MAX_ENTRIES = 512

#: `label 名字:`（行首，允许缩进）—— 与 `story.control_route` 用的是**同一条**
#: （那边从这里取，避免"找得到 label"和"算得出落点"用两套正则）。
LABEL_RE = re.compile(r'^\s*label\s+([^\s:(#]+)', re.MULTILINE)

#: `jump 名字` / `call 名字`（含 `call X from Y`）—— 同样只做导航性判断。
REF_RE = re.compile(
    r'^[ \t]*(?:jump|call)[ \t]+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)',
    re.MULTILINE)

_lock = threading.RLock()
#: `路径 → ((mtime_ns, size), (text, style))`
_text_cache: 'OrderedDict[str, tuple]' = OrderedDict()
#: `路径 → ((mtime_ns, size), (label, ...))`
_labels_cache: 'OrderedDict[str, tuple]' = OrderedDict()
#: `路径 → ((mtime_ns, size), FileFacts)`
_facts_cache: 'OrderedDict[str, tuple]' = OrderedDict()
#: `(根, 额外目录) → (守卫, (Path, ...))`
_discover_cache: dict = {}
#: `(名字, 根, 细分键) → (指纹, 结果)`
_memo: 'OrderedDict[tuple, tuple]' = OrderedDict()

#: 统计（只有诊断/测试看）：缓存命中 / 未命中，读文件次数。
hits = 0
misses = 0


def _touch(cache, key, value):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > MAX_ENTRIES:
        cache.popitem(last=False)


def clear() -> None:
    """清空全部缓存（显式"刷新"或测试用；平时不需要调 —— 指纹会自己失效）。"""
    global hits, misses
    with _lock:
        _text_cache.clear()
        _labels_cache.clear()
        _facts_cache.clear()
        _discover_cache.clear()
        _memo.clear()
        hits = 0
        misses = 0


def stats() -> dict:
    """缓存现状（诊断用：看看命中了没有，别在正常流程里依赖它）。"""
    with _lock:
        return {'hits': hits, 'misses': misses,
                'text': len(_text_cache), 'labels': len(_labels_cache),
                'facts': len(_facts_cache), 'discover': len(_discover_cache),
                'memo': len(_memo)}


# ---------------------------------------------------------------------------
#  单个文件
# ---------------------------------------------------------------------------

def stamp(path):
    """
    `(mtime_ns, size)` —— 缓存的**唯一**失效判据；读不到返回 `None`。

    为什么是这两个数：内容变了 mtime 一定变（Windows NTFS 是 100ns 精度），
    长度变了 size 变。两个一起看，连"改了但刚好同一毫秒"这种边界也能扛。
    """
    try:
        st = os.stat(str(path))
    except OSError:
        return None
    return (int(st.st_mtime_ns), int(st.st_size))


def read_text(path):
    """
    `(text, style)` —— 与 `story.source_style.read_text` 完全相同，只读一次。

    文本是**看用**的（BOM 已剥、换行已折成 `\\n`）；要写盘请走
    `SourceStyle.apply(..., source=路径)`，它会重新读原文并严格解码。
    """
    global hits, misses
    p = str(path)
    st = stamp(p)
    if st is None:
        from storygraph.story.source_style import read_text as _read
        return _read(path)
    with _lock:
        hit = _text_cache.get(p)
        if hit is not None and hit[0] == st:
            _text_cache.move_to_end(p)
            hits += 1
            return hit[1]
        misses += 1
    from storygraph.story.source_style import read_text as _read
    value = _read(path)
    with _lock:
        _touch(_text_cache, p, (st, value))
    return value


def labels_of(path) -> tuple:
    """这个 `.rpy` 声明了哪些 label（按出现顺序、去重）—— 同 `control_route`。"""
    global hits, misses
    p = str(path)
    st = stamp(p)
    if st is None:
        return ()
    with _lock:
        hit = _labels_cache.get(p)
        if hit is not None and hit[0] == st:
            _labels_cache.move_to_end(p)
            hits += 1
            return hit[1]
        misses += 1
    try:
        text = read_text(path)[0]
    except OSError:
        return ()
    out, seen = [], set()
    for m in LABEL_RE.finditer(text):
        name = m.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    value = tuple(out)
    with _lock:
        _touch(_labels_cache, p, (st, value))
    return value


def file_facts(path, project_root):
    """
    `placement.FileFacts` 的缓存版：同一份文件只**整份解析**一次。

    这是最贵的那一步（完整 AST），也是 `detect_placement` 慢的全部原因。
    返回拷贝，调用方改自己的副本不影响缓存。
    """
    global hits, misses
    p = str(path)
    st = stamp(p)
    with _lock:
        hit = _facts_cache.get(p) if st is not None else None
        if hit is not None and hit[0] == st:
            _facts_cache.move_to_end(p)
            hits += 1
            return _copy_facts(hit[1])
        misses += 1
    from storygraph.story.placement import scan_file
    facts = scan_file(path, project_root)
    if st is not None:
        with _lock:
            _touch(_facts_cache, p, (st, facts))
    return _copy_facts(facts)


def _copy_facts(facts):
    """`FileFacts` 是 dataclass，里面三个 list —— 拷贝出来再给调用方。"""
    try:
        return _dc_replace(facts, decl_names=list(facts.decl_names or ()),
                           labels=list(facts.labels or ()))
    except Exception as exc:         # noqa: BLE001 - 拷不动就原样给（只读场景无碍）
        from storygraph.core.safeguard import swallow
        swallow('story.scan_cache._copy_facts', exc)
        return facts


# ---------------------------------------------------------------------------
#  整个项目
# ---------------------------------------------------------------------------

def _guard_for(paths, root) -> tuple:
    """
    发现结果的"守卫"：`((目录, mtime_ns), ...)`。

    只记目录 mtime，不记文件内容 —— 这一层回答的是"有哪些 `.rpy`"，
    文件**内容**变不改变答案（内容级的失效在 `fingerprint` 那层）。
    """
    dirs = set()
    for p in paths:
        try:
            dirs.add(str(Path(p).parent))
        except (OSError, ValueError):
            continue
    game = Path(str(root)) / 'game'
    if game.is_dir():
        dirs.add(str(game))
    out = []
    for d in sorted(dirs):
        try:
            out.append((d, int(os.stat(d).st_mtime_ns)))
        except OSError:
            continue
    return tuple(out)


def _guard_ok(guard: tuple) -> bool:
    """目录 mtime 都没变？"""
    for d, mtime in guard:
        try:
            if int(os.stat(d).st_mtime_ns) != mtime:
                return False
        except OSError:
            return False
    return True


def discover(root, extra_dirs=None) -> list:
    """
    `parsing.discover.discover_scripts` 的缓存版：目录没变就不重新 rglob。

    为什么值得缓存：`game/` 底下两三千个图片/音频文件，`rglob('*.rpy')`
    每次要走完整个树（真机 38 ms）。一次接线要问"有哪些脚本"七八遍。

    失效判据：**目录 mtime 变了**（新建/删除/改名文件都会改父目录 mtime），
    或者缓存里的某个文件已经不在了。新增一个 `.rpy` 一定会命中其中之一。

    ⚠ 项目根**不存在**时**不缓存**：否则先问一次（那时项目还没建）就会把
    空清单记住，用户建好项目后仍然什么都扫不到 —— 这种"缓存了却没有东西
    能让它失效"的情况必须挡在入口。
    """
    base = Path(str(root))
    if not base.is_dir():
        return []
    key = (str(base), tuple(str(d) for d in (extra_dirs or ())))
    with _lock:
        hit = _discover_cache.get(key)
    if hit is not None:
        guard, paths = hit
        if _guard_ok(guard) and all(stamp(p) is not None for p in paths):
            return [Path(p) for p in paths]
    from storygraph.parsing.discover import discover_scripts
    paths = tuple(Path(p) for p in discover_scripts(base, extra_dirs))
    guard = _guard_for(paths, base)
    with _lock:
        _discover_cache[key] = (guard, paths)
    return list(paths)


def fingerprint(root, files=None) -> tuple:
    """
    项目指纹：`((路径, mtime_ns, size), ...)` —— **任何**文件变了它就不一样。

    这是所有"项目级结论"的失效判据。算一遍要 stat 全部脚本（真机 91 个
    约 2 ms），相对"重新解析 1.4 秒"是免费的。
    """
    if files is None:
        paths = discover(root)
    else:
        paths = [Path(p) for p in files]
    out = []
    for p in paths:
        st = stamp(p)
        if st is None:
            continue
        out.append((str(p), st[0], st[1]))
    return tuple(out)


def memo(name: str, root, extra, fp, compute):
    """
    按 `(名字, 根, 细分键)` 存一份项目级结果；指纹不同就重算。

    `compute` 只在未命中的时候调 —— 所以放进去的必须是**纯读**操作。
    """
    global hits, misses
    key = (str(name), str(root or ''), extra)
    with _lock:
        hit = _memo.get(key)
        if hit is not None and hit[0] == fp:
            _memo.move_to_end(key)
            hits += 1
            return hit[1]
        misses += 1
    value = compute()
    with _lock:
        _touch(_memo, key, (fp, value))
    return value


__all__ = ['LABEL_RE', 'MAX_ENTRIES', 'REF_RE', 'clear', 'discover',
           'file_facts', 'fingerprint', 'labels_of', 'memo', 'read_text',
           'stamp', 'stats']
