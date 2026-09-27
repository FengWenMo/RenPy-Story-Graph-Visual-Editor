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

# 引擎/框架前缀：这些调用属于"技术代码"，不是剧情变量
_TECH_PREFIXES = (
    'renpy.', 'renpy(', 'persistent.', 'store.', 'config.', 'gui.',
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
_TECH_TARGET_PARTS = ('renpy', 'persistent', 'config', 'store', 'gui',
                      'preferences', 'style', 'audio')


def is_narrative_code(code: str) -> bool:
    """
    这段代码是"剧情变量操作"吗？

    判定顺序：
      1. 以引擎前缀开头（renpy./persistent./config.…）→ 否
      2. 纯函数调用（没有赋值）→ 否（`renpy.notify("x")` 之类）
      3. 赋值目标里含引擎模块（`persistent.x = 1`）→ 否
      4. 其余赋值（`love += 1`、`flag = True`）→ 是
    """
    text = (code or '').strip()
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
