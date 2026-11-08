# -*- coding: utf-8 -*-
"""
立绘卡的**「退场」语义**：一张「沿用」卡怎么变成 `hide`、怎么撤销。

为什么单独一个小模块（2026-11-08）：

用户拿截图来问"我的立绘为什么加了就无法删除了"——角色一旦变成「沿用」，
`SpriteCardWidget` 就把 ✕ 藏了（2026-11-02 留的口子），于是它永远下不了台。
语义本身不依赖 Qt（一个 dict 进、一个 dict 出），放在这里既能被
`sprite_grid` 直接用，也能脱开界面单测。

三条规矩：

  · **删一张卡 = "这一拍不要让 ta 在台上"**；
  · 这一拍自己 `show` 的卡：删掉就是删掉（`show` 里没它了）；
  · 「沿用」卡：角色是上一拍 `show` 上来的，光删卡什么都不写 ——
    所以标成 `hide=True`，保存时写 `hide <tag>`；再点 ↺ 撤销。
"""
from __future__ import annotations


def tag_of(item) -> str:
    """这张卡是哪个角色（`tag` 或 `image` 的第一个词）。"""
    item = item or {}
    return (str(item.get('tag') or '').strip()
            or (str(item.get('image') or '').split() or [''])[0])


def is_hide(item) -> bool:
    return bool((item or {}).get('hide'))


def is_inherited(item) -> bool:
    """「沿用」卡（退场卡不算 —— 它已经归这一拍了，只是写的是 hide）。"""
    return bool((item or {}).get('inherited')) and not is_hide(item)


def toggle(item) -> str:
    """
    点右上角那颗按钮：`''` = 不归这里管（调用方按"删卡"处理）。

    · 退场卡 → 回到「沿用」（这一拍又什么都不写了）；
    · 「沿用」卡 → 变成退场卡（`hide=True`，`inherited` 去掉）；
    · 普通卡 → 返回 `''`，由调用方拿掉这张卡。
    """
    item = item if isinstance(item, dict) else {}
    if is_hide(item):
        item.pop('hide', None)
        item['inherited'] = True
        return 'undo'
    if item.get('inherited'):
        item['hide'] = True
        item.pop('inherited', None)     # 这一拍要写它了（写的是 hide）
        return 'hide'
    return ''


def close_text(item) -> str:
    """按钮上的字：退场卡是 ↺（撤销），其余是 ✕。"""
    return '↺' if is_hide(item) else '✕'


def close_tip(item) -> str:
    """按钮的提示：**说清这一下会写出什么**（不写就是"点了没反应"）。"""
    item = item or {}
    tag = tag_of(item)
    if is_hide(item):
        return f'撤销：「{tag}」这一拍不退场了，恢复成「沿用」'
    if item.get('inherited'):
        return (f'删除「{tag}」→ 这一拍写 `hide {tag}`（保存后生效）。\n'
                f'角色是上一拍 show 上来的，光删这张卡不会下台。')
    return '删除这张卡（保存后生效）'


def title(item, base_text: str) -> str:
    """卡片标题：退场卡写「（这一拍退场）」，沿用卡加「（沿用）」。"""
    item = item or {}
    if is_hide(item):
        return f'{tag_of(item)}（这一拍退场）'
    return str(base_text or '') + ('（沿用）' if item.get('inherited') else '')


def title_tip(item) -> str:
    """标题下面挂的说明（拼在"引擎里写的是什么"后面）。"""
    item = item or {}
    tag = tag_of(item)
    if is_hide(item):
        return (f'\n\n**这一拍会写 `hide {tag}`**（保存后生效）。\n'
                f'点右上角 ↺ 撤销；保存后要改回来，去表格的「退场」列删掉。')
    if item.get('inherited'):
        return ('\n\n**这一拍没写它**：人是上一拍 show 上来的（沿用）。\n'
                '改一下表情或位置，它才会写进这一拍；不改则一字不写。\n'
                f'点 ✕ = 让 ta 这一拍**退场**（写 `hide {tag}`）。')
    return ''


__all__ = ['tag_of', 'is_hide', 'is_inherited', 'toggle', 'close_text',
           'close_tip', 'title', 'title_tip']
