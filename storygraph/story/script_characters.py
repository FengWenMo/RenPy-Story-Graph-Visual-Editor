"""
storygraph.story.script_characters
==================================

项目 `.rpy` 里 **`define x = Character(…)` / `DynamicCharacter(…)` 声明过的角色名**。

为什么需要它（2026-11-14，用户报"导出明明啥都没事，怎么还报缺资源见注释"）
------------------------------------------------------------------------------
`Symbols.actor()` 只查资源表（`characters.xlsx`），而表是**人工维护的**：
真机 `sr_general` 里 `nvl_narrator` / `cat` / `duck` / `ls` / `ds` / `dy` /
`grandma` / `all_npc` / `daoyou` 九类说话人全都写在 `definitions.rpy` 里
（`define cat = DynamicCharacter('cat_name', …)`），表里却没有 —— 于是
编译期把它们判成"缺少资源 角色 'cat'"，渲染器按 D7 把说话人**丢掉**、
降级成旁白，而增量补丁又把那条说明注释也滤掉了。用户看到的就是：
回执说"缺资源 9 处（见注释）"，产物里一条都没有，说话人却没了。

真源层序（`core/authority.py`）本来就写着 **declared(2) > registry(4)**：
脚本里 `define` 出来的东西，比表里有没有那一行**更权威**。这个模块就是
那层"声明"的读入口，只认字面量（铁律 4：不模糊匹配、不猜）。

读盘走 `story.scan_cache`（按文件 `mtime+size` 失效，无 TTL）：真机 96 个
`.rpy` 读一遍约 2ms 指纹 + 一次正则扫；命中缓存时只剩指纹。
"""
from __future__ import annotations

import re

#: `define cat = DynamicCharacter('cat_name', …)` /
#: `define nvl_narrator = Character(…)`。
#
# 与 `story.renpy_gui._CHAR_RE` 同一个形状（那边还要 `cls` 去分 DynamicCharacter
# 的"第一个参数是变量名"）；这里只关心**名字**，所以允许行首缩进、`=` 两侧任意
# 空格 —— 缩进里的 `define`（写在 `init python` 旁边的少数项目）也算。
CHARACTER_DEFINE_RE = re.compile(
    r'^[ \t]*define[ \t]+(?P<id>[A-Za-z_]\w*)[ \t]*=[ \t]*'
    r'(?:Dynamic)?Character[ \t]*\(',
    re.MULTILINE)


def declared_characters(project_root) -> frozenset:
    """
    项目里 `define x = Character(…)` 声明过的角色**变量名**（frozenset）。

    返回空集 = 没读到 / 没有项目根 / 项目里一个都没声明 —— 三种都是
    "**不知道**"，调用方不该据此下"不存在"的结论（`Symbols.actor` 只在
    表里找不到时才来问一次，问不到就照旧报缺资源）。

    读盘失败不打日志：这一层是"判不出来就交回旧行为"的旁路，而
    `scan_cache` 自己已经吞掉了读不到的文件（见 `read_text` / `discover`）。
    """
    root = str(project_root or '').strip()
    if not root:
        return frozenset()
    from storygraph.story import scan_cache as SC
    files = SC.discover(root)
    if not files:
        return frozenset()
    fingerprint = SC.fingerprint(root, files)
    return SC.memo('declared_characters', root, '',
                   fingerprint, lambda: _scan(files))


def _scan(files) -> frozenset:
    """把这一批 `.rpy` 里声明过的角色名收出来（缓存未命中时才跑）。"""
    from storygraph.story import scan_cache as SC
    names = set()
    for path in files:
        try:
            text = SC.read_text(path)[0]
        except (OSError, UnicodeDecodeError):
            continue                    # 读不动的那个文件不当证据（窄捕获）
        names.update(CHARACTER_DEFINE_RE.findall(text))
    return frozenset(names)


def script_actor(project_root, word) -> dict:
    """
    项目脚本声明过 `word` 这个角色吗？是 → 返回一条最小角色行，否则 `None`。

    为什么放在**这里**而不是 `Symbols` 里（2026-11-14）：`dsl.py` 已经顶到
    1500 行硬线（技术债棘轮 `long_files`），而读声明本来就不该是编译器的家事。
    缓存由 `scan_cache.memo` 按文件指纹管（`Symbols` 在编译/导出/金标里会被
    构造很多次，每次读盘是不可接受的）。
    """
    root = str(project_root or '').strip()
    word = str(word or '').strip()
    if not root or not word:
        return None
    return ({'ID': word, '_from_script': True}
            if word in declared_characters(root) else None)


__all__ = ['CHARACTER_DEFINE_RE', 'declared_characters', 'script_actor']
