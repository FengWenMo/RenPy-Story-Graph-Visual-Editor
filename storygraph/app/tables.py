"""
storygraph.app.tables —— **剧本表（Excel）工作流**，框架无关。

为什么需要它
------------
"用表格编演出"是这个工具的原始诉求：一个 .rpy ↔ 一张同名 .xlsx 双向走。
这条路上有两个真实事故，所以规则写得很细：

  · **产物不许写回 game/**。以前写 `.generated.rpy` 就在表旁边，
    而表在 game/ 里 —— Ren'Py 把 game/ 下所有 .rpy 当同一个脚本，
    产物和原文抢同一批 label（一次真实事故）。现在一律走
    `planned_output_path` → `<项目根>/build/renpy/`。
  · **优先增量补丁**。用户 2026-09-28 的原话："保留注释，全量修改出来的
    东西，根本就没办法看！！！"实测真实章节：全量与原文相似度 0.9246、
    丢 18 行 / 多 13 行；增量 0.9947、零丢失零多出。

这些规则原先长在 tkinter 的 `GraphView.export_rpy_from_table` 里
（141 行 UI 方法）。清理 tkinter 时把它们搬到这里 —— 语义一条没改，
但 Qt 前端现在也能用，且不必 import tkinter。

接口约定：**只返回事实，不弹框**
--------------------------------
本模块不 import 任何 UI。所有"要不要继续""要不要覆盖"的判断交给前端：
返回的 dict 带齐 `ok` / `blocking` / `warnings` / `review_path` / `note`，
前端照着展示即可。禁止在这里调用 `messagebox` 之类的东西。

**禁止**在本模块里 import 任何 UI 框架。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from pathlib import Path

# 剧本表读写（都在 story 层，这里只编排）
from storygraph.story.excel_import import (find_unregistered_assets,
                                           import_beats_from_excel)
from storygraph.story.generation_guard import (evaluate_generation,
                                              labels_in_danger,
                                              planned_output_path,
                                              unmanaged_block_heads,
                                              write_generation)
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.patcher import patch_export
from storygraph.story.script_table import open_rpy_as_table


# ----------------------------------------------------------------------
#  候选下拉的 mtime 记忆（"资源表变了，表里的下拉要跟着变"）
# ----------------------------------------------------------------------

def remember_table_mtime(store: dict, table_path) -> None:
    """
    记下这张表**我们已知的** mtime（生成/刷新之后调用）。

    `store` 由调用方持有（一个 dict），这样本模块保持无状态、可重入。
    """
    try:
        store[str(Path(table_path).resolve())] = Path(table_path).stat().st_mtime
    except OSError as e:
        swallow('tables.remember_table_mtime', e)


def table_changed_since(store: dict, table_path) -> bool:
    """
    这张表在我们记下 mtime 之后有没有被外部改过？

    没记录过 → 先记一次并返回 False（第一次看到不算"被改过"）。
    """
    table_path = Path(table_path)
    key = str(table_path.resolve())
    cached = store.get(key)
    if cached is None:
        remember_table_mtime(store, table_path)
        return False
    try:
        return table_path.stat().st_mtime != cached
    except OSError:
        return False


# ----------------------------------------------------------------------
#  .rpy → 剧本表（生成并交给系统打开）
# ----------------------------------------------------------------------

def generate_table(script_path, output_path=None, registry=None,
                   project_root=None, with_thumbnails=True) -> dict:
    """
    把一个 .rpy 解析成演出表（同名 .xlsx）写盘。

    返回 {ok, table_path, error}。**不打开 Excel** —— 那是前端的事
    （`open_with_system_default` 在这里只提供能力，不主动调用：
    无头/测试环境不该被弹窗或外部程序打断）。
    """
    script_path = Path(script_path)
    if not script_path.exists():
        return {'ok': False, 'table_path': None,
                'error': f'找不到脚本：{script_path}'}
    table_path = Path(output_path) if output_path else script_path.with_suffix('.xlsx')
    try:
        open_rpy_as_table(script_path, table_path, registry=registry,
                          with_thumbnails=with_thumbnails)
    except Exception as e:                    # noqa: BLE001 - 报给前端
        return {'ok': False, 'table_path': table_path, 'error': str(e)}
    return {'ok': True, 'table_path': table_path, 'error': ''}


# ----------------------------------------------------------------------
#  剧本表 → .rpy（导入 + 增量补丁 + 安全门禁）
# ----------------------------------------------------------------------

def export_table(table_path, registry=None, project_root=None,
                 source_rpy=None, out_path=None) -> dict:
    """
    把剧本表写回 .rpy。**只算与写，不问**（"先问后写"的确认框在前端）。

    返回一个"事实包"：

        ok            是否写出成功
        out_path      目标路径
        beats         读到的演出行数
        text          最终文本（拦下时前端可拿来对照）
        note          用了增量补丁还是全量重生成（一句人话）
        blocking      [str] 致命问题（非空 = 没写目标文件）
        warnings      [str] 非致命提醒
        review_path   被拦下时另存的 `.blocked.rpy` 副本（供对比）
        unregistered  [(category, id)] 表里引用但资源表未登记的资产
        error         读取阶段就失败时的原因（此时其余字段为空）

    步骤与旧实现逐条对应：读表 → 找未登记资产 → 决定产物路径 →
    渲染 → 增量补丁 → 门禁评估 → 落盘（拦下则只留副本）。
    """
    table_path = Path(table_path)
    try:
        beats = import_beats_from_excel(table_path, registry=registry)
    except Exception as e:                    # noqa: BLE001 - 报给前端
        return _empty(error=f'读取剧本表失败：{e}', out_path=out_path)
    if not beats:
        return _empty(error='剧本表里没有读到任何演出行。', out_path=out_path)

    # M4：表里引用了、但资源表未登记的资产 —— 提示文案去登记
    unregistered = []
    if registry is not None:
        try:
            unregistered = list(find_unregistered_assets(beats, registry))
        except Exception as e:                     # noqa: BLE001 - 纯提示，不该拦住
            swallow('app.tables.export_table', e)
            unregistered = []

    target = Path(out_path) if out_path else planned_output_path(
        table_path, script_path=source_rpy, project_root=project_root)
    text = render_beats_to_rpy(beats, label_name=None, registry=registry)

    # —— 增量补丁优先（保住原文的注释与逐字节未改行）——
    note = ''
    #: 这次合并的**底稿**（增量对上了才是路径，否则 None）。
    #: 为什么要带出去（2026-10-05 实测修）：落盘那一步
    #: （`apply_export` → `write_generation`）要靠它判断
    #: "手写护栏让不让路" —— 少了它，增量产物写回**手写**文件时会被自己的
    #: 护栏拦下，而报出来的错还是"检查文件是否被占用"（驴唇不对马嘴）。
    merged_from = None
    try:
        src = _source_rpy(source_rpy, table_path)
        # `registry` 必须传：补丁里的"单拍渲染"要与全量渲染用**同一套符号表**，
        # 否则生成写法会不一致（`ling happy` 被写成中文 tag / 下划线形态），
        # 那就是"把能跑的脚本改坏"。
        res = patch_export(beats, src, text, registry=registry)
        if res.ok:
            text = res.text
            merged_from = src
            note = (f'增量补丁：对位 {res.matched_beats}/{res.total_beats} 拍，'
                    f'实际改动 {res.changed_lines} 行')
            if getattr(res, 'unlanded_lines', 0):
                note += (f'（{res.unlanded_lines} 行注释/代码未参与替换，'
                         f'保持原文）')
        else:
            why = '；'.join(res.reasons) if res.reasons else \
                f'与源文件对位率不足（{res.coverage:.0%}）'
            note = f'全量重生成（增量补丁未采用：{why}）'
    except Exception as e:                    # noqa: BLE001
        note = f'全量重生成（增量补丁不可用：{e}）'

    # —— 生成安全门禁：判定在 story 层，这里只摆结果 ——
    #
    # **occupied 必须走 labels_in_danger，不能在这里自己算**：
    # 以前 UI 自己算了一遍，绕过了"产物在 build/ 就免查重名"的守卫，
    # 于是写到 build/ 也被拒写（用户 2026-09-28 实测）。
    occupied = labels_in_danger(target, project_root,
                                exclude={target, table_path})
    issues = evaluate_generation(text, target, project_root=project_root,
                                 occupied=occupied, beats=beats)
    blocking = [i for i in issues if i.is_blocking]
    warnings = [i for i in issues if not i.is_blocking]
    # 工具不认识的块语句（M1）：**结论必须随结果走到落盘那一步** ——
    # `apply_export` 只拿得到这份 dict，拿不到 beat 对象，而门禁那条
    # 判据（`UNMANAGED_BLOCK`）必须"说出口"，不许在这一跳上丢掉。
    unmanaged_blocks = unmanaged_block_heads(beats, None)

    if blocking:
        # 拒写，但**不把产物丢掉**：另存一份 .blocked.rpy 供对比，
        # 否则"拒写"会让人连问题长什么样都看不到（信任 > 方便）。
        # 注意用 `write_generation(review_on_block=True)` 走同一套逻辑，
        # 不再手写副本 —— 副本命名规则只能有一个定义处。
        art = write_generation(text, target, review_on_block=True,
                               project_root=project_root, occupied=occupied,
                               beats=beats)
        return {
            'ok': False,
            'out_path': target,
            'beats': len(beats),
            'text': text,
            'note': note,
            'used_patch': bool(merged_from),
            'merged_from': merged_from,
            'blocking': [i.format() for i in blocking[:12]],
            'blocking_total': len(blocking),
            'warnings': [i.format() for i in warnings[:6]],
            'review_path': art.review_path,
            'unregistered': unregistered,
            'unmanaged_blocks': unmanaged_blocks,
            'error': '',
        }

    return {
        'ok': True,
        'out_path': target,
        'beats': len(beats),
        'text': text,
        'note': note,
        'used_patch': bool(merged_from),
        'merged_from': merged_from,
        'blocking': [],
        'blocking_total': 0,
        'warnings': [i.format() for i in warnings[:6]],
        'review_path': None,
        'unregistered': unregistered,
        'unmanaged_blocks': unmanaged_blocks,
        'error': '',
    }


def apply_export(result: dict, project_root=None) -> dict:
    """
    把 `export_table` 算好的文本真正落盘。

    刻意分成两步：前端要能"先看结果、问过用户、再落盘"（旧实现就是
    "先评、再问、最后写"——不在用户点头前落任何盘）。

    **`merged_from` 必须往下传**（2026-10-05 实测修）：它是增量合并的底稿，
    也就是那条"手写护栏让不让路"的唯一判据（见
    `generation_guard.merging_onto`）。少了它，写回**手写**章节时产物会被
    `HANDWRITTEN_TARGET` 拦下 —— 而这里从前把结论只写成
    "检查文件是否被占用"，把"门禁拒绝覆盖"说成了"文件被占用"，
    排查方向被彻底带偏。
    """
    target = Path(result['out_path'])
    art = write_generation(result.get('text') or '', target,
                           review_on_block=True, project_root=project_root,
                           merged_from_source=result.get('merged_from'),
                           unmanaged_blocks=result.get('unmanaged_blocks'))
    out = dict(result)
    out['ok'] = bool(art.written)
    out['review_path'] = art.review_path
    out['written'] = bool(art.written)
    if not art.written and not result.get('blocking'):
        # 说**门禁给的那句**（手写目标 / 语法 / 重名…）；门禁没给结论
        # （磁盘满、文件被占用）时才是原本那句。
        why = '；'.join(_issue_text(i) for i in (art.issues or []))
        out['error'] = (f'写不出去：{art.out_path}（{why}）' if why else
                        f'写不出去：{art.out_path}（检查文件是否被占用）')
    return out


def _issue_text(issue) -> str:
    """门禁发现 → 一句给人看的话（`GuardIssue` 有 `.format()`，别的实现只有 `str`）。"""
    fmt = getattr(issue, 'format', None)
    return str(fmt() if callable(fmt) else issue)


# ----------------------------------------------------------------------
#  角色表
# ----------------------------------------------------------------------

#: 角色表的列顺序：脚本解析出来的字段全部落表，导入完能一眼看清
#: 这个角色是怎么定义的（显示名/颜色/立绘标签/语音/前后缀…）。
CHARACTER_TABLE_FIELDS = [
    'ID', 'Name', 'Color', 'WhatColor', 'ImageTag', 'Kind', 'VoiceTag',
    'SideImage', 'WhatPrefix', 'WhatSuffix', 'WhoPrefix', 'WhoSuffix',
    'DefaultTransform', 'Notes',
]


def export_character_table(registry, asset_dir) -> dict:
    """
    把 registry 里的角色写进 `characters.xlsx`。

    返回 {ok, path, count, error}。以前角色只"悄悄"合并进内存注册表，
    用户看不到导入了什么；这个动作把结果显式落表。
    """
    if registry is None:
        return {'ok': False, 'path': None, 'count': 0,
                'error': '没有资产注册表。'}
    if asset_dir is None:
        return {'ok': False, 'path': None, 'count': 0,
                'error': '该项目没有资产表目录。'}
    characters = getattr(registry, 'characters', {}) or {}
    if not characters:
        return {'ok': False, 'path': None, 'count': 0,
                'error': '脚本里没解析到 Character 定义。'}

    from storygraph.assets.asset_registry import write_dict_to_excel
    target = Path(asset_dir) / 'characters.xlsx'
    data = {cid: {k: row.get(k, '') for k in CHARACTER_TABLE_FIELDS}
            for cid, row in characters.items()}
    try:
        write_dict_to_excel(target, data, list(CHARACTER_TABLE_FIELDS))
    except Exception as e:                    # noqa: BLE001 - 报给前端
        return {'ok': False, 'path': target, 'count': 0, 'error': str(e)}
    return {'ok': True, 'path': target, 'count': len(data), 'error': ''}


# ----------------------------------------------------------------------
#  内部
# ----------------------------------------------------------------------

def _source_rpy(source_rpy, table_path):
    """
    增量补丁的底稿 .rpy。

    查法（从可信到兜底）：
      1. 调用方明确给的（画布上选中章节的 file_path，最准）；
      2. 表旁边同名 `.rpy`（`chapter1_3.xlsx` → `chapter1_3.rpy`）；
    都没有就返回 None → 调用方退回全量（安全）。
    """
    if source_rpy is not None and Path(source_rpy).exists():
        return Path(source_rpy)
    cand = Path(table_path).with_suffix('.rpy')
    if cand.exists() and not cand.name.endswith('.generated.rpy'):
        return cand
    return None


def _empty(error: str, out_path=None) -> dict:
    return {
        'ok': False, 'out_path': Path(out_path) if out_path else None,
        'beats': 0, 'text': '', 'note': '',
        'used_patch': False, 'merged_from': None,
        'blocking': [], 'blocking_total': 0, 'warnings': [],
        'review_path': None, 'unregistered': [], 'error': error,
    }


__all__ = ['generate_table', 'export_table', 'apply_export',
           'export_character_table', 'remember_table_mtime',
           'table_changed_since', 'CHARACTER_TABLE_FIELDS']
