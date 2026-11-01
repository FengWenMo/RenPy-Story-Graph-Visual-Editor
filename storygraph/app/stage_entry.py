# -*- coding: utf-8 -*-
"""
storygraph/app/stage_entry.py
=============================
「本章第一拍之前台上是什么」—— 跨章继承状态的**查询 + 缓存**（不碰 Qt）。

为什么需要它（2026-10-31）
--------------------------
卡片 / 尺寸面板 / 舞台小图算的都是**这一章自己**的拍（`build_frame_at(beats)`），
而 Ren'Py 的舞台是**跨章延续**的：上一章演完背景还在，本章第一句台词之前
没写 `scene` 就是接着上一章。于是卡片给的是"这一拍没有背景"，游戏里却有 ——
「本章第一拍补一条 show」那个入口因此**一次都按不到**（真机实测：两本项目
1013 个有背景的拍里，没有一个是从"入口状态"来的）。

`build_frame_at(..., initial_state=)` 早就收这份状态了，缺的是"上一章是谁"。
**不能按文件名排序**：`chapter2_1.rpy` 整章 130 拍没有一句 `scene`，
光看"上一个文件的出口"是空的；那 4 处 `chapter2_1 → 2_1_1~4` 的入口
得沿 `pipeline.continuity` 的链条（chapter1_4 → chapter2_1 → 2_1_1）推。

为什么不在这里重写一套"上一章是谁"
-----------------------------------
链条、单入边/汇聚、环、旁支、链根 —— 全在 `pipeline.continuity`
（`Chapter.entry` 就是它算出来的）。这里只做三件**它不做**的事：

  ① **整本分析太贵，不能摆在 GUI 线程里**：真机 `sr_general` 92 个文件
     整本约 2.2 秒（解析 1.6 + 分析 0.5）。所以分析在后台线程跑
     （`ui_qt.entry_warm`），这里只负责缓存与查询；
  ② **缓存键 = 项目根 + 源文件指纹**（mtime+size，真机 46ms）：文件一改，
     下次查询自然失效 —— 不靠"手动刷新"这种会忘的约定；
  ③ 把 `Chapter.entry` 换成界面能直接用的 `ChapterEntry`（带 `why` / `note`，
     见 `pipeline.continuity`）。

⚠ 查询（`entry_for`）**只在缓存命中时返回**；没命中返回 `None` 并说清原因
  （`why_not`）—— 界面这时照旧显示"本章自己的拍"，绝**不**在这里同步跑
  2 秒去凑一个答案（那会把翻页卡住，比没有这个功能更糟）。
"""
from __future__ import annotations

import os
from collections import OrderedDict

from storygraph.core.safeguard import swallow

#: 最多留几份报告（一份真机报告的对象很小，主要是 beats 引用）。
CACHE_LIMIT = 3

#: `(项目根, 源文件指纹) → ContinuityReport`（有序 = 先丢最早的）。
_CACHE: 'OrderedDict[tuple, object]' = OrderedDict()


def _root_key(project_root) -> str:
    """项目根的缓存键（Windows 大小写不敏感：同一份项目不能存两份）。"""
    try:
        return os.path.normcase(str(project_root or '')).rstrip('\\/')
    except (TypeError, ValueError) as e:
        # `normcase` 只对"路径协议怪异的类型"抛（`__fspath__` 返回非 str/bytes
        # → TypeError；内嵌空字节 → ValueError）。退化成原样字符串：
        # 那会让 Windows 上大小写不同的同一项目算成两个缓存键 —— 只浪费内存，
        # 不会给出错结果；但要留痕，不然永远查不到为什么缓存没命中。
        swallow('app.stage_entry.root_key', e)
        return str(project_root or '')


def signature(project_root) -> str:
    """整个项目的源文件指纹（mtime+size；读不到 → 空串 = 不做缓存）。"""
    root = str(project_root or '').strip()
    if not root:
        return ''
    try:
        from storygraph.pipeline.continuity import project_source_signature
        return project_source_signature(root)
    except Exception as e:                       # noqa: BLE001 - 指纹失败只影响缓存
        swallow('app.stage_entry.signature', e)
        return ''


def cached_report(project_root, sig: str = None):
    """缓存里有没有这份项目的报告（`sig=None` 时现算指纹）。"""
    key = (str(_root_key(project_root)),
           str(signature(project_root) if sig is None else sig))
    if not key[1]:
        return None
    return _CACHE.get(key)


def remember(project_root, sig: str, report) -> None:
    """记住一份报告（超过上限就丢最早的那份）。"""
    if report is None or not str(sig or ''):
        return
    key = (str(_root_key(project_root)), str(sig))
    _CACHE[key] = report
    _CACHE.move_to_end(key)
    while len(_CACHE) > CACHE_LIMIT:
        _CACHE.popitem(last=False)


def clear_cache() -> None:
    """清空（测试 / 换项目用）。"""
    _CACHE.clear()


def analyze(project_root):
    """
    **整本分析**（真机约 2 秒）→ `(指纹, 报告)`，并记进缓存。

    ⚠ 慢：只许在后台线程调用（`ui_qt.entry_warm`）。指纹在**分析之前**取，
    这样"分析期间文件又被改了"会自然落到下一个指纹上，不会把新结果
    记成旧文件的。
    """
    sig = signature(project_root)
    from storygraph.pipeline.continuity import analyze_project
    report = analyze_project(project_root)
    remember(project_root, sig, report)
    return sig, report


def entry_for(project_root, script_path):
    """
    这个 `.rpy` **开头那一章**的入口状态（`ChapterEntry`）或 `None`。

    **只读缓存**：没有缓存（还没分析过 / 文件刚改过）→ `None`。
    调用方（卡片）据此退回"本章自己的拍"，并在后台请求一次分析
    （`ui_qt.entry_warm`）——拿不到就如实显示"不知道"，不猜。
    """
    if not project_root or not script_path:
        return None
    report = cached_report(project_root)
    if report is None:
        return None
    try:
        from storygraph.pipeline.continuity import entry_for_file
        return entry_for_file(report, script_path)
    except Exception as e:                       # noqa: BLE001 - 查不到就是没有
        swallow('app.stage_entry.entry_for', e)
        return None


def matches_project(project_root, report) -> bool:
    """
    这份报告是不是**这个项目**的（后台算完回来时项目可能已经被换掉了）。

    只比根目录：两份不同项目的报告在界面上互换，比"没有入口状态"更糟。
    """
    if report is None:
        return False
    root = getattr(report, 'root', '')
    if not str(root or '').strip():
        return False
    return _root_key(root) == _root_key(project_root)


def why_not(project_root, script_path) -> str:
    """
    为什么现在拿不到入口状态（给卡片 tooltip / 日志的一句话）。

    三种原因**分开说**，因为处理方式不一样：
      · 没项目目录 → 单文件模式，跨章本来就不存在；
      · 没缓存（还没分析） → 后台正在算，等一下就有；
      · 缓存里有、但没有这一章 → 这个文件不是"被别的章接进来的"章节。
    """
    if not project_root:
        return '没有项目目录：判不了上一章（单文件模式）'
    report = cached_report(project_root)
    if report is None:
        return '还没分析过整本（后台算一次就绪；只在内存里，不写盘）'
    if not script_path:
        return '没有当前文件'
    return '这个文件不是章节（或它不是从别的章接进来的）'


def stale(project_root, sig: str = None) -> bool:
    """缓存里的那份报告是不是**过期**了（文件改过 → True）。"""
    if not project_root:
        return False
    now = str(signature(project_root) if sig is None else sig)
    return not any(k[0] == _root_key(project_root) and k[1] == now
                   for k in _CACHE)


__all__ = ['CACHE_LIMIT', 'analyze', 'cached_report', 'clear_cache',
           'entry_for', 'matches_project', 'remember', 'signature', 'stale',
           'why_not']
