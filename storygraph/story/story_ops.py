"""
story_ops.py
============
把节点上的 Python 代码分成两类：**剧情变量操作** 与 **技术代码**。

背景（来自真实项目反馈）：
  一个章节导入后节点上冒出 55 条"操作"，点开一看全是
  `renpy.transition(Dissolve(0.1), layer="master")`（重复 23 次）和
  `persistent.lock_lst["cg02.1"] = True`。
  但策划要看的"操作"是**影响剧情走向的变量**（`love += 1`、`flag = True`），
  不是引擎 API 调用。技术代码堆在同一个列表里，等于把这个功能淹掉。

所以：展示/分析走过滤视图，导出仍然原样回写全部代码
（`renpy_exporter` 不加过滤——保真是底线，过滤只是"给人看的视图"）。
"""

from __future__ import annotations

import re

from storygraph.core.statement import strip_marker

# 引擎/框架前缀：这些调用属于"技术代码"，不是剧情变量
#
# 注意**没有** `store.`：在 Ren'Py 里 `store` 就是默认命名空间，
# `store.love += 1` 与 `love += 1` 是同一件事。曾经把 `store.` 也算成
# 技术代码 → 这类写法在变量编辑器里**整条看不见**（用户改不到它）。
_TECH_PREFIXES = (
    'renpy.', 'renpy(', 'persistent.', 'config.', 'gui.',
    'preferences.', 'audio.', 'defaults.', 'nvl_', '_', 'main_menu',
    'style.', 'layeredimage', 'init ', 'import ', 'from ', 'del ',
    'print(', 'log(', 'renpy_', 'achievement.', 'iap.', 'build.',
)

# 赋值语句（含增强赋值）
_RE_ASSIGN = re.compile(
    r'^\s*(?P<target>[A-Za-z_][\w\.\[\]"\' ]*?)\s*'
    r'(?P<op>\+=|-=|\*=|/=|//=|%=|\|=|&=|\^=|>>=|<<=|:=|=)\s*(?P<value>.+)$')
# 自增/自减
_RE_INCDEC = re.compile(r'^\s*[A-Za-z_][\w\.\[\]]*\s*(\+\+|--)\s*$')

# 明显不是"剧情变量"的赋值目标
#
# 同样**没有** `store`：见 `_TECH_PREFIXES` 上面的说明。
_TECH_TARGET_PARTS = ('renpy', 'persistent', 'config', 'gui',
                      'preferences', 'style', 'audio')


def is_narrative_code(code: str) -> bool:
    """
    这段代码是"剧情变量操作"吗？

    判定顺序：
      1. 以引擎前缀开头（renpy./persistent./config.…）→ 否
      2. 纯函数调用（没有赋值）→ 否（`renpy.notify("x")` 之类）
      3. 赋值目标里含引擎模块（`persistent.x = 1`）→ 否
      4. 其余赋值（`love += 1`、`flag = True`、`store.love += 1`）→ 是

    `code` 可能是从图里拿到的 **python op 原文**，行首带 `\\x01`
    （= 原文写的是紧凑 `$code`）——分类前必须先脱掉它，否则
    `$love += 1` 这种写法会匹配不上赋值正则，被当成技术代码**藏起来**。
    """
    text = strip_marker(code).strip()
    if not text:
        return False
    # 多行 python 块：逐行看，只要有一行是剧情变量就算
    if '\n' in text:
        return any(is_narrative_code(ln) for ln in text.splitlines())

    low = text
    for prefix in _TECH_PREFIXES:
        if low.startswith(prefix):
            return False

    m = _RE_ASSIGN.match(text)
    if not m:
        return bool(_RE_INCDEC.match(text))

    target = m.group('target')
    head = re.split(r'[\.\[]', target, maxsplit=1)[0]
    if head in _TECH_TARGET_PARTS:
        return False
    if head.startswith('_'):
        return False
    return True


def narrative_operations(node_or_ops) -> list:
    """过滤出剧情变量操作（接受 Node 或 operations 列表）。"""
    ops = getattr(node_or_ops, 'operations', node_or_ops) or []
    return [op for op in ops if is_narrative_code(op)]


def tech_operations(node_or_ops) -> list:
    """过滤出技术代码（引擎 API / 非赋值调用）。"""
    ops = getattr(node_or_ops, 'operations', node_or_ops) or []
    return [op for op in ops if not is_narrative_code(op)]


def group_operations(ops) -> list:
    """
    去重聚合：`[(代码, 出现次数), ...]`，按次数降序。

    同一个 `renpy.transition(Dissolve(0.1))` 出现 23 次，列表里就该是
    一行 ×23，而不是 23 行把别的内容挤走。
    """
    counts = {}
    order = []
    for op in ops or ():
        key = str(op)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
    return sorted(((k, counts[k]) for k in order),
                  key=lambda kv: (-kv[1], kv[0]))


def summarize(node) -> str:
    """给节点标签用的一句话摘要，例如 `3 剧情变量 · 52 技术代码`。"""
    nar = narrative_operations(node)
    tech = tech_operations(node)
    if not nar and not tech:
        return ''
    parts = []
    if nar:
        parts.append(f'{len(nar)} 剧情变量')
    if tech:
        parts.append(f'{len(tech)} 技术代码')
    return ' · '.join(parts)


def narrative_summary(node) -> str:
    """
    只讲**剧情变量**的摘要，例如 `3 剧情变量`（没有就返回空串）。

    为什么要有它（用户 2026-10-03）：

    > 这个所谓的"技术代码"，它不是变量，是具体的实现细节！……
    > 我不要再在变量的窗口上看到任何内部技术代码的名字！编辑变量是为了
    > 让策划和编剧调整和编排剧情分支用的地方！不是拿来填细节的！！！

    所以**变量相关的显示**（块上的摘要、变量编辑器、变量分析）一律只讲
    剧情变量；技术代码只在需要看实现细节的地方出现（原始代码 / Excel 的
    「代码」列），连"有多少条"都不该混进变量视图里。
    """
    n = len(narrative_operations(node))
    return f'{n} 剧情变量' if n else ''


def merge_narrative_operations(original, edited) -> list:
    """
    把"编辑过的剧情变量"合并回原始操作列表：**只替换剧情变量，技术代码原位保留**。

    为什么必须这样（真实事故的形状）：变量编辑器里看不到技术代码，
    如果保存时直接把整份 `operations` 换成编辑器里的内容，那么
    `renpy.transition(...)` / `persistent.x = ...` 这一批会被**静默删掉** ——
    用户只是想改一个 `love += 1`，结果整章的转场没了。
    （tkinter 版是 `new_ops + tech_operations(node)`，即技术代码统一挪到末尾；
    这里改成**原位保留**：转场与 show 的相对顺序不能靠"挪到末尾"来赌。）

    参数：
      original 原始 `operations`（含技术代码）
      edited   编辑器里看得到的那些剧情变量（新顺序 / 新内容）
    """
    out = []
    edited = list(edited or [])
    used = 0
    for op in list(original or []):
        if is_narrative_code(op):
            if used < len(edited):
                out.append(edited[used])
                used += 1
            # 编辑器里删掉的条数 → 这里就少写几条（其余位置原样保留）
        else:
            out.append(op)
    out.extend(edited[used:])          # 新增的变量追加到末尾
    return out


def render_list(ops, limit: int = 40) -> str:
    """把操作列表渲染成可读文本（聚合 + 截断）。"""
    grouped = group_operations(ops)[:limit]
    lines = []
    for code, count in grouped:
        lines.append(f'{code}    ×{count}' if count > 1 else code)
    total = len(list(ops or ()))
    if total > limit:
        lines.append(f'…还有 {total - limit} 种')
    return '\n'.join(lines)
