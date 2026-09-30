"""
beat_editor.py
==============
应用内的剧本表编辑 UI（M5 / M6）。

与 Excel 双向路线并行，这里提供一个"不离开编辑器"的轻量编辑界面：
  · AssetPickerDialog   通用资产选择器（复用 AssetCompletion 的前缀/模糊搜索）
  · BeatEditorPanel     选中一拍后的快速编辑面板（角色/台词/语音/音乐/立绘…）
  · ScriptTableEditorWindow  载入一个 .rpy → 列出所有 Beat → 编辑 → 导出 .rpy

纯逻辑（搜索、Beat↔字段映射、新建资产回写）抽成函数，便于无头测试；
tkinter 只负责把数据摆到界面上。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import messagebox, simpledialog

from storygraph.assets.asset_completion import AssetCompletion
from storygraph.assets.asset_registry import describe_asset
from storygraph.ui import tokens as T
from storygraph.ui.scroll_frame import ScrollFrame
from storygraph.ui.sprite_card import AddCard, SpriteCard
from storygraph.ui.thumb_loader import ThumbLoader
from storygraph.ui.tooltip import Tooltip
from storygraph.story.beat_model import (
    Beat,
    KIND_EXTEND,
    KIND_NARRATION,
    _assign_identity,
    _join_show_items,
    _split_show_items,
    compose_stage,
    compose_stage_extra,
    compose_audio_text,
    compose_code,
)
from storygraph.story.beat_build import (
    beats_from_parse_result,
    hydrate_from_dsl,
    stage_only_ops,
)
from storygraph.story.generation_guard import generation_guard, occupied_labels
from storygraph.story.beat_render import render_beats_to_rpy, make_symbols
from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
from storygraph.story.stage_snapshot import stage_digest
from storygraph.pipeline.validate import validate_all


# ===================== 纯逻辑（可单测） =====================

def search_assets(engine, query: str = '', asset_types=None, limit: int = 50):
    """按前缀/模糊搜索资产，返回 [(asset_type, asset_id), ...]。"""
    comp = AssetCompletion(engine)
    return comp.complete(query, asset_types=asset_types, limit=limit)


def asset_row(engine, asset_type: str, asset_id: str):
    """取一条资产的原始行数据（找不到返回 None）。"""
    if engine is None or not asset_id:
        return None
    try:
        registry = engine.get_registry()
    except Exception:
        return None
    if registry is None:
        return None
    attr = AssetCompletion.TYPE_MAP.get(asset_type)
    if not attr:
        return None
    return (getattr(registry, attr, {}) or {}).get(asset_id)


def asset_detail(engine, asset_type: str, asset_id: str) -> str:
    """
    一条资产的人话说明。

    重点是 transform：选中 `leftish` 时要能看到"靠左站"那句注释，
    而不是只有一串 xpos——不然文案根本不知道该选哪个变换。
    """
    row = asset_row(engine, asset_type, asset_id)
    if row is None:
        return ''
    described = describe_asset(asset_type, row)
    if asset_type == 'transform':
        atl = (row.get('ATL') or '').strip()
        if atl:
            described += '\n\nATL:\n' + '\n'.join(
                '    ' + l.strip() for l in atl.splitlines() if l.strip())
    return described


def asset_preview_path(engine, asset_type: str, asset_id: str):
    """
    返回可用于预览的图片绝对路径（仅 image 类型且文件存在时）。

    资产表的 Path 是相对项目根目录的，所以这里要靠 engine.project_root
    拼回绝对路径；拼不出来就给 None，让调用方退化显示文字。
    """
    if asset_type != 'image':
        return None
    row = asset_row(engine, asset_type, asset_id)
    if not row:
        return None
    rel = (row.get('Path') or '').strip()
    if not rel:
        return None
    p = Path(rel)
    if not p.is_absolute():
        root = getattr(engine, 'project_root', None)
        if root:
            p = Path(root) / rel
    try:
        return p if p.exists() else None
    except OSError:
        return None


def lookup_image_id(images: dict, tag: str, rest: list) -> str:
    """
    在资源表里找出这张立绘的资产 ID（**先按角色，再按名字**）。

    为什么不能用属性名硬撞（2026-09-30 用户实测的串图事故）：
    真实项目里 `lan` 与 `ling` **都有 `oh` 这个表情**，而资源表里
    `ID='oh'` 那一条属于 **lan**。旧实现会退化成"拿最后一个词去查"，
    于是 `ling oh` 显示成 **lan 的立绘**（用户原话：
    "这其实是角色 lan 的立绘，不是 ling oh！"）。

    顺序：
      1. `Tag == tag` 且 `Attributes` 含 `rest[0]`   ← 最可靠
      2. `Tag == tag` 且属性为空（没写属性时）
      3. 完整名 / 下划线名 / `_属性` / `tag_属性`
    **故意没有"裸属性名"这一档** —— 那正是跨角色串图的来源。
    """
    if not images:
        return ''
    tag = str(tag or '').strip()
    if not tag:
        return ''
    rest = [str(r).strip() for r in (rest or []) if str(r).strip()]
    want = rest[0].lower() if rest else ''

    fallback = ''
    for aid, row in images.items():
        row = row or {}
        if str(row.get('Tag') or '').strip().lower() != tag.lower():
            continue
        attrs = [a.lower() for a in str(row.get('Attributes') or '').split()]
        if not want:
            if not attrs:
                return str(aid)             # 没写属性：最贴的是"无属性"那张
            if not fallback:
                fallback = str(aid)
            continue
        if attrs and (attrs[0] == want or want in attrs):
            return str(aid)
    if fallback:
        return fallback

    # 2) 名字直配：**候选里必须带 tag**，否则又可能跨角色串图
    #    （曾经试过 `_属性` 这一档，在 tag 对不上时会命中别人的图，去掉）
    text = ' '.join([tag] + rest).strip()
    for cand in (text, text.replace(' ', '_')):
        if cand in images:
            return cand
    if rest:
        cand = f'{tag}_{rest[0]}'
        if cand in images:
            return cand
    return ''


def resolve_sprite_thumb(engine, image_spec: str, max_size=(150, 210)):
    """
    立绘图像名（`ling ah`）→ 缩略图（失败返回 None）。

    用户 2026-09-28：**"卡片内的立绘预览还是没有解决！！！画面被压扁了"**。
    上一轮我把"看图"做成了外部预览窗，但**卡片里那一行根本没有画面**
    —— 用户在改立绘时看不到任何图。这个方法就是给它补上图。

    解析走 `asset_registry.resolve_references` 的**同一条链**
    （不另造一套"空格↔下划线"），再用 `asset_preview_path` 定位文件。
    """
    spec = (image_spec or '').strip()
    if not spec or engine is None:
        return None, ''
    reg = None
    try:
        reg = engine.get_registry()
    except Exception:
        reg = None
    if reg is None:
        return None, ''

    aid = ''
    try:
        from storygraph.assets.asset_registry import resolve_references
        from storygraph.assets.asset_registry import AssetIndex
        index = AssetIndex(reg)
        refs = resolve_references(spec, reg, index) or {}
        for cand in (refs.get('images') or refs.get('image') or []):
            if cand:
                aid = cand
                break
    except Exception:
        aid = ''

    # 退化：直接按名字找（`bg_room` / `room`）
    if not aid:
        images = getattr(reg, 'images', {}) or {}
        # **按角色解析**：真实项目里 `lan` 与 `ling` 都有 `oh` 表情，
        # 拿属性名硬撞会把 `ling oh` 解析成 **lan 的图**（用户实测的串图事故）
        parts = spec.split()
        tag = parts[0] if parts else ''
        aid = lookup_image_id(images, tag, parts[1:]) if tag else ''
    if not aid:
        return None, ''

    path = asset_preview_path(engine, 'image', aid)
    if not path:
        return None, aid
    return load_thumbnail(path, max_size), aid


def _set_thumb(label, photo, text):
    """把缩略图（或占位文字）塞进 Label；失败静默退化。"""
    if label is None or not hasattr(label, 'config'):
        return
    try:
        label.config(image=photo if photo is not None else '', text=text or '')
        if photo is not None:
            # 必须留引用，否则被 GC 掉图就没了
            label.image = photo
        else:
            label.image = None
    except Exception:
        pass


def load_thumbnail(path, max_size=(220, 140)):
    """
    读一张图并缩成缩略图，返回 tk PhotoImage（失败返回 None）。

    优先 Pillow（png/jpg/webp 通吃），没装则退到 tk.PhotoImage
    （只能读 png/gif，且只能整数倍缩小）。两条路都走不通就返回 None，
    调用方显示文字说明即可——预览是锦上添花，不能让它把功能挡住。
    """
    path = Path(path)
    if not path.exists():
        return None

    try:
        from PIL import Image, ImageTk      # type: ignore
        img = Image.open(path)
        img.thumbnail(max_size)
        return ImageTk.PhotoImage(img)
    except Exception:
        pass

    try:
        if not hasattr(tk, 'PhotoImage'):
            return None
        photo = tk.PhotoImage(file=str(path))
        w = max(photo.width(), 1)
        h = max(photo.height(), 1)
        factor = max(1, (w + max_size[0] - 1) // max_size[0],
                     (h + max_size[1] - 1) // max_size[1])
        if factor > 1:
            photo = photo.subsample(factor, factor)
        return photo
    except Exception:
        return None


def beat_to_fields(beat: Beat, symbols=None) -> dict:
    """
    Beat → 面板字段字典（界面与数据之间的中性中间层）。

    面板把**全部舞台指令投影进 Stage 一格**（给了符号表就合成中文写法
    `[显示] 凌跃捷 开心 左`，合不成就写原生写法 `show ling close_happy
    at left`——两种都能被编译器吃回去），另有 Scene / Voice 两个能点选的
    结构化列。Excel 那面则是 20 列结构化列 + Stage 高级通道；两边通过
    `compose_stage` / `hydrate_from_dsl` 互为投影，不会各输出一次。

    2026-09-28 新增 **Expression / Position 两个派生字段**：
    它们是"卡片上 4 个可编辑项"里的后两项（前两项是 Speaker / Text）。
    见 `beat_expression_word` / `beat_position_word` 的说明——
    这两个字段没有对应的 Beat 属性，是 `show` 的投影。
    """
    return {
        'Label': beat.label,
        'Kind': beat.kind,
        'Speaker': beat.speaker,
        'Text': beat.text,
        'Expression': beat_expression_word(beat, symbols),
        'Position': beat_position_word(beat, symbols),
        'Scene': beat.scene,
        'Stage': beat.stage or compose_stage(beat, symbols),
        'BGM': beat.bgm or compose_audio_text(beat, 'music'),
        'SFX': beat.sfx or compose_audio_text(beat, 'sound'),
        'Voice': beat.voice,
        'CodeBlock': compose_code(beat),
        'Comment': beat.comment,
        'Notes': beat.notes,
    }


def fields_to_beat(fields: dict, base: Optional[Beat] = None) -> Beat:
    """面板字段字典 → Beat。base 非空时在其上覆盖，保留 seq 等元信息。

    顺序很讲究：先把结构化字段写进 Beat，再用 Expression / Position
    改写 `beat.show`。**Stage 单元格不在这里重算**——面板显示 Stage 时
    会拿改好的 Beat 调 `compose_stage`（见 `BeatEditorPanel.collect`），
    否则 4 项卡片里改的表情/位置会在 Stage 里显示成旧值，
    正是"我表里看到的和生成的不一样"。
    """
    b = base or Beat()
    b.label = (fields.get('Label') or '').strip()
    b.kind = (fields.get('Kind') or 'say').strip() or 'say'
    b.speaker = (fields.get('Speaker') or '').strip()
    b.text = (fields.get('Text') or '').strip()
    b.scene = (fields.get('Scene') or '').strip()
    b.stage = (fields.get('Stage') or '').strip()
    b.bgm = (fields.get('BGM') or '').strip()
    b.sfx = (fields.get('SFX') or '').strip()
    b.voice = (fields.get('Voice') or '').strip()
    b.code = (fields.get('CodeBlock') or '').strip()
    # raw 已经并入 CodeBlock（唯一的"保真后门"），这里不再单独维护
    b.raw = ''
    b.comment = (fields.get('Comment') or '').strip()
    # 「备注」列已经取消（并入注释，见 Beat.from_row 的老表迁移）。
    # 这里再兜一次：旧 Beat / 旧表带来的 notes 绝不能静默丢掉。
    b.notes = (fields.get('Notes') or '').strip()
    if b.notes:
        b.comment = f'{b.comment}\n{b.notes}'.strip() if b.comment else b.notes
        b.notes = ''

    # —— 4 项卡片的派生两项：写回 show ——
    # 只有表单里真的带这两个键时才改写，避免"直接构造 Beat 的调用方"
    # （单测、CLI）在没碰表情/位置的情况下被清空立绘。
    if 'Expression' in fields:
        b.show = apply_expression(b.show, (fields.get('Expression') or '').strip())
    if 'Position' in fields:
        b.show = apply_position(b.show, (fields.get('Position') or '').strip())
    return b


# ===================== 4 项卡片：表情 / 位置的投影 =====================
#
# 为什么要有这一层：策划在卡片上真正会改的只有 **4 样**——
#   说话人 / 台词 / 表情 / 位置
# 而 Beat 上只有前两样有独立属性（speaker / text）；表情和位置是
# `beat.show`（`ling happy@left`）这一格的投影。直接让策划编辑 show 字符串
# 等于要求他记住 `图@位置` 这种语法——那正是要消灭的负担。
#
# 所以这里做一对纯函数（读出来 / 写回去），UI 只负责摆控件；
# 无头测试可以直接验它们，不必起 tkinter。


def _first_show_item(beat: Beat) -> dict:
    """取第一个「一般立绘」项（跳过 clause 形态）。没有就返回空 dict。"""
    for item in _split_show_items(getattr(beat, 'show', '') or ''):
        if item.get('clause') or not (item.get('image') or '').strip():
            continue
        return item
    return {}


def _show_items(beat: Beat) -> list:
    return [i for i in _split_show_items(getattr(beat, 'show', '') or '')
            if not i.get('clause') and (i.get('image') or '').strip()]


def beat_sprite_rows(beat: Beat, symbols=None) -> list:
    """
    Beat → **立绘数组**（每张立绘一行），供卡片显示/编辑。

    用户 2026-09-28 的设计意见：
    > 立绘是数组，不是单数 —— 一张卡片可以同时有多个立绘：
    > 主角在左、女主在右、路人居中。每个立绘独立控制位置。

    数据层**本来就支持**（`beat.show` 是 `;` 分隔的列表，渲染成多条 show），
    但卡片只显示第一张 —— 这里把它摊成数组。
    返回 `[{'image':..., 'tag':..., 'expression':..., 'position':...}, ...]`，
    顺序即出场顺序（不丢信息）。
    """
    rows = []
    for item in _split_show_items(getattr(beat, 'show', '') or ''):
        if item.get('clause') or not (item.get('image') or '').strip():
            # 复杂子句（behind/as/onlayer）不在这套结构里表达，跳过
            continue
        parts = (item.get('image') or '').split()
        tag = parts[0] if parts else ''
        attrs = parts[1:]
        at = item.get('at') or ''
        # **裸词位置**（`凌跃捷 开心 左`）必须认成位置，不能算作表情：
        # 否则 chip 上会显示"开心 左"，而切换表情时"左"会被吃掉（数据丢失）。
        # DSL 允许裸词位置，结构化列要求 `@位置`，这里统一成后者。
        if not at and attrs:
            try:
                from storygraph.story.dsl import DEFAULT_POSITIONS
                if attrs[-1] in DEFAULT_POSITIONS:
                    at = attrs[-1]
                    attrs = attrs[:-1]
            except Exception:                # noqa: BLE001
                pass
        probe = Beat(seq=1, label='', text='',
                     show=' '.join([tag] + attrs))
        probe.at = at
        rows.append({
            'tag': tag,
            'image': ' '.join([tag] + attrs),
            'expression': beat_expression_word(probe, symbols),
            'position': beat_position_word(probe, symbols),
            'at': at,
            'clause': '',
        })
    return rows


def uniform_sprite_position(rows: list) -> str:
    """
    立绘行 → 整拍统一的「位置」（`beat.at`，不统一就返回 ''）。

    为什么不能直接取第一张的位置（2026-09-30 真实数据实测的"立绘错位"）：
    `beat.at` 在渲染时是**整拍的兜底位置**，会套给**每一条没写 `@` 的
    show**（见 `beat_render._stage_lines`）。而一拍的立绘可以各有各的位置。

    于是 `show ye happy at i11` + `show ye care` 这样的一拍，一旦被
    "取 rows[0]['at']" 填上 `i11`，再渲染就变成：

        show ye happy at i11
        show ye care  at i11     ← 凭空给第二个人也套上了位置

    只有**所有立绘位置一致且都非空**时才写回整拍位置；
    否则留空 —— 位置已经逐条写在 `图@位置` 里了，兜底值只会帮倒忙。
    """
    positions = {str((r or {}).get('at') or '').strip() for r in (rows or [])}
    if len(positions) == 1:
        return positions.pop()
    return ''


def group_sprites_by_tag(rows: list) -> list:
    """
    立绘行 → **按角色（tag）聚合**的卡片模型（纯显示层聚合，不碰数据）。

    为什么需要（用户 2026-09-29）：
    > 表情变化被当作多个角色立绘处理，外观虽正确但不直观

    Ren'Py 里 `show ling happy` 之后再 `show ling sad`，是**换属性**
    （后者替换前者，场上还是一个人）。但 `beat_sprite_rows` 会把每个
    show 子项摊成一行，于是"同一角色换了表情"看起来像"站了两个人"。

    聚合规则：
      · 同一 tag 的多条 → **一张卡**；`active` 指向最后一条（Ren'Py 的生效语义）
      · `extras` 保存其余条目 —— **原样保留**，写回时不能丢
      · `multi_position`：同 tag 多条却位置不同 → 数据可疑
        （Ren'Py 不会让同 tag 有两个实例），卡上给警告而不是静默摊开
    """
    grouped, order = {}, []
    for idx, r in enumerate(rows or []):
        r = dict(r or {})
        tag = (r.get('tag') or (r.get('image') or '').split()[0] or '')
        if tag not in grouped:
            grouped[tag] = []
            order.append(tag)
        grouped[tag].append((idx, r))

    cards = []
    for tag in order:
        items = grouped[tag]
        positions = {str(it[1].get('position') or '') for it in items}
        cards.append({
            'tag': tag,
            'active': items[-1][1],              # 最后一条 = 生效的那条
            'extras': [it[1] for it in items[:-1]],
            'count': len(items),
            'first_index': items[0][0],
            'indices': [it[0] for it in items],
            'multi_position': len(positions) > 1,
        })
    return cards


def stage_sprite_rows(stage_text: str, symbols=None) -> list:
    """
    从 `Stage` 列的 DSL 里取出 `[显示]` 立绘（**老写法的兼容通道**）。

    为什么必须做（用户 2026-09-30）：
    > 已经导入了的立绘没有显示！！！（确认台上确实有人）

    原因很实在：立绘在新版走结构化的 `show` 列，但**历史数据里大量立绘是写在
    Stage 列的**（`[显示] 凌跃捷 开心 左`）。立绘区只读 `show` 列，于是
    "台上明明有人、界面却是空白"。这里把 Stage 里的立绘读出来，
    让界面**如实反映台上的人**，而不是让用户以为自己看错了。

    返回与 `beat_sprite_rows` **同构**的行（额外带 `from_stage=True`），
    这样卡片、按 tag 聚合、写回全都能复用同一套逻辑。
    """
    text = str(stage_text or '').strip()
    if not text:
        return []
    try:
        from storygraph.story.dsl import parse_stage
        parsed = parse_stage(text)
    except Exception:                        # noqa: BLE001
        return []

    # 把每条 `[显示]` 还原成"人话片段"（`凌跃捷 开心@左`），再交给
    # `beat_sprite_rows` 统一解析 —— **表情 / 位置的识别逻辑只有一份**。
    # （第一版在这里自己拆 attrs，把裸词位置 `左` 当成了表情，
    #   迁移后 `凌跃捷 开心 左` 变成 `凌跃捷 左`，等于把"开心"吃掉。
    #   这正是"同一件事有两套解析"的老毛病，所以收敛掉。）
    pieces = []
    for op in getattr(parsed, 'ops', []) or []:
        # 只要 `[显示]`（show）；隐藏与原生写法由 Stage 自己负责
        if getattr(op, 'verb', '') != 'show' or getattr(op, 'native', False):
            continue
        target = str(getattr(op, 'target', '') or '').strip()
        if not target:
            continue
        attrs = [str(a).strip() for a in (getattr(op, 'attrs', None) or [])
                 if str(a).strip()]
        at = str(getattr(op, 'at', '') or '').strip()
        # **裸词位置**（`[显示] 凌跃捷 开心 左`）要识别成位置，否则它会被
        # 当成表情：卡片上显示"开心 左"，迁移后 `@位置` 也丢了。
        # DSL 允许裸词位置，结构化列要求 `@位置`，这里做一次归一。
        if not at and attrs:
            try:
                from storygraph.story.dsl import DEFAULT_POSITIONS
                if attrs[-1] in DEFAULT_POSITIONS:
                    at = attrs[-1]
                    attrs = attrs[:-1]
            except Exception:                # noqa: BLE001
                pass
        piece = ' '.join([target] + attrs)
        if at:
            piece += '@' + at
        pieces.append(piece)
    if not pieces:
        return []

    probe = Beat(seq=1, label='', text='', show='; '.join(pieces))
    rows = beat_sprite_rows(probe, symbols)
    for r in rows:
        r['from_stage'] = True
    return rows


def strip_stage_show_lines(stage_text: str) -> str:
    """
    删掉 Stage 文本里所有 `[显示]` 行，保留其它指令（迁移立绘时用）。

    迁移后 Stage 只剩转场 / 停顿这类**非立绘**指令 —— 这就是"职责明晰"的落地：
    立绘只由立绘区（`show` 列）表达，两列不再同时说同一件事。
    """
    kept = []
    for line in str(stage_text or '').split('\n'):
        s = line.strip()
        if s.startswith('[显示]') or s.startswith('［显示］'):
            continue
        kept.append(line)
    return '\n'.join(kept).strip()


def apply_sprite_rows(show: str, rows: list) -> str:
    """
    立绘数组 → `beat.show` 字符串（`a x@left; b y@right`）。

    **复杂子句原样保留**：它们结构化列表达不了，重建时不能丢。
    """
    kept = [i for i in _split_show_items(show or '') if i.get('clause')]
    out = []
    for row in rows or ():
        image = (row.get('image') or '').strip()
        if not image:
            continue
        at = (row.get('at') or '').strip()
        out.append({'clause': '', 'image': image, 'at': at})
    return _join_show_items(out + kept)


def inherited_state_hint(beats: list, idx: int) -> str:
    """
    「继承自上一拍」的提示文本（背景 / 立绘 / 音乐这类**持续状态**）。

    用户 2026-09-28 的设计意见：
    > 背景、BGM、环境层这类持续状态，默认从上一张卡片继承。
    > 卡片上显示"继承：教室-黄昏"，点击才覆盖。

    重要前提：**Ren'Py 本身就是继承语义** —— 背景/立绘/BGM 是状态，
    不写就是沿用上一拍。所以这一条**不需要改数据模型**，
    只要把"当前继承到什么"**显示出来**，创作者就不必每拍重填。

    这里复用 `stage_snapshot` 的同一套推导（预览与生成必须同源），
    返回一行人类可读的说明；第 0 拍或没有任何继承时返回 ''。
    """
    if not beats or not (0 < idx < len(beats)):
        return ''
    try:
        from storygraph.story.stage_snapshot import snapshot_rows
        rows = snapshot_rows(beats)
    except Exception:
        return ''
    if idx >= len(rows):
        return ''
    before = rows[idx - 1]

    parts = []
    # 只有"这一拍自己没写"的才算继承
    if not (beats[idx].scene or '').strip() and before.get('scene'):
        parts.append(f'背景 {before["scene"]}')
    if not (beats[idx].show or '').strip() and before.get('actors'):
        parts.append(f'立绘 {before["actors"]}')
    if not (beats[idx].music or '').strip() and before.get('music'):
        parts.append(f'音乐 {before["music"]}')
    if not parts:
        return ''
    return '继承上一拍：' + '　·　'.join(parts)


def speaking_sprite_index(beat: Beat, rows: list, symbols=None) -> int:
    """
    哪一张立绘**正在说话**（返回 rows 里的下标，找不到返回 -1）。

    用户 2026-09-28：
    > 不仅要显示登场角色，左下侧正在说话的角色还要提前、高光显示

    匹配规则（从强到弱，命中即返回）：
      1. 说话人的 `ImageTag` 直接等于某张立绘的 tag；
      2. 说话人就是 tag 本身（有些项目角色变量与 tag 同名）；
      3. 说话人显示名 → `ImageTag`（`凌跃捷` → `ling`）。

    找不到就返回 -1：**不猜**。猜错会把高光打在错的角色上，
    比不高亮更让人困惑。
    """
    if not rows:
        return -1
    who = (getattr(beat, 'speaker', '') or '').strip()
    if not who:
        return -1

    tags = [(r.get('tag') or '').strip() for r in rows]

    def find(tag: str) -> int:
        if not tag:
            return -1
        for i, t in enumerate(tags):
            if t and t == tag:
                return i
        return -1

    if symbols is not None:
        try:
            hit = find(symbols.actor_image_tag(who))
            if hit >= 0:
                return hit
        except Exception:
            pass
    hit = find(who)
    if hit >= 0:
        return hit
    return -1


def order_sprite_rows(rows: list, speaking_idx: int) -> list:
    """
    把正在说话的那张立绘**提到最前面**（其余保持原顺序）。

    注意：这只影响**卡片上的显示顺序**，不改变 `beat.show` 里的出场顺序
    —— 出场顺序是演出语义（谁先出现），显示顺序只是"让人先看到该看的"。
    所以 `_collect_sprites` 必须按**原始顺序**写回，不能按显示顺序。
    """
    if not rows or not (0 <= speaking_idx < len(rows)):
        return list(rows)
    out = [rows[speaking_idx]]
    out += [r for i, r in enumerate(rows) if i != speaking_idx]
    return out


def image_spec_of_row(row: dict, fallback: str = '') -> str:
    """
    资产行 → **策划要写进「立绘」列的图像名**（Ren'Py 的 `tag + 属性`）。

    为什么需要它（用户 2026-09-28 实测的"立绘错位"）：
    真实项目把立绘存成 `Tag=ling` + `Attributes=ah`，而**资产 ID 是 `_ah`**
    （文件名 `ling/_ah.png`）。选择器原来直接显示 ID，于是策划看到
    `_ah` / `_disgusting`，完全认不出这是什么，也**复制不出该写的名字**。

    规则（与 `dsl.Symbols.build_image_spec` 同口径，不另造一套）：
      · 有 Tag  →  `tag 属性`（如 `ling ah`）——这就是 show 列该写的
      · 无 Tag  →  退回 Attributes，再退回 ID

    注意**不保证唯一**：Ren'Py 里 `tag + 属性` 才是图像名，重复 ID 属于
    资源表的既有状态（真实项目里同一个图常有两行：文件名一份、路径一份），
    这里只负责"显示成能用的名字"，不去重。
    """
    row = row or {}
    tag = str(row.get('Tag') or '').strip()
    attrs = str(row.get('Attributes') or '').strip()
    if tag:
        return f'{tag} {attrs}'.strip() if attrs else tag
    return attrs or str(fallback or '').strip()


def categorize_assets(engine, asset_types=None, limit: int = 3000) -> dict:
    """
    把候选资产**按类别分组**（用户 2026-09-28 要求，阶段 3 第 3 项）。

    > 资源选择器加分类/分组 —— 2904 张图不能靠打字。

    以前的 `AssetPickerDialog` 只有"搜索 + 预览"，没有分类：
    真实项目有 2904 张图，策划不记得 ID 就只能一个个打字试。
    这里给出分组结构，UI 用左侧分类列表 + 右侧条目。

    分组规则（**看图的人怎么找**，不是按数据表怎么存）：

        背景 / CG / 界面    —— 非角色图层，按 tag 分
        立绘：<角色 tag>     —— 角色立绘按 tag 分组（最常用的一组）
        音乐 / 音效 / 语音   —— 音频按通道分
        变换                —— transform

    返回 `{类别名: [(asset_type, asset_id), ...]}`，类别内按 ID 排序
    （稳定顺序，便于习惯位置）。
    """
    from storygraph.assets.asset_completion import AssetCompletion

    reg = None
    try:
        reg = engine.get_registry() if engine is not None else None
    except Exception:
        reg = None

    # 拿全量候选（空查询）+ 各类型的原始行，用于判断 tag
    comp = AssetCompletion(engine) if engine is not None else None
    rows = []
    types = list(asset_types) if asset_types else [
        'character', 'image', 'transform', 'music', 'sound', 'voice']
    for atype in types:
        attr = AssetCompletion.TYPE_MAP.get(atype)
        table = getattr(reg, attr, {}) if (reg is not None and attr) else {}
        for aid in sorted(table or {}):
            rows.append((atype, aid, table.get(aid) or {}))
        if not table and comp is not None:
            # 没有资源表时退回搜索接口（例如只解析了脚本的场景）
            for atype2, aid in comp.complete('', asset_types=[atype],
                                             limit=limit):
                rows.append((atype2, aid, {}))

    groups = {}
    for atype, aid, row in rows[:limit]:
        if atype == 'image':
            tag = (row.get('Tag') or '').strip()
            if not tag:
                # 从 ID 猜 tag：`bg_classroom` → bg；`ling_happy` → ling
                tag = _guess_image_tag(aid)
            low = tag.lower()
            if low in ('bg', 'background'):
                cat = '背景'
            elif low in ('cg',):
                cat = 'CG'
            elif low in NON_ACTOR_TAGS_FOR_PICK:
                cat = '界面/其他图层'
            else:
                cat = f'立绘：{tag}' if tag else '未分类图片'
        else:
            cat = {'character': '角色', 'transform': '变换',
                   'music': '音乐', 'sound': '音效',
                   'voice': '语音'}.get(atype, atype)
        groups.setdefault(cat, []).append((atype, aid))

    # 类别排序：立绘在前（最常用），背景次之，其余按名字
    def rank(name):
        if name.startswith('立绘：'):
            return (0, name)
        return (1, name)

    return {k: groups[k] for k in sorted(groups, key=rank)}


#: 判"这条图片不是角色立绘"的 tag 集合（与 stage_snapshot 同口径）
NON_ACTOR_TAGS_FOR_PICK = frozenset({
    'bg', 'background', 'cg', 'ui', 'gui', 'movie', 'video', 'logo',
    'black', 'white', 'overlay', 'screen', 'text', 'title', 'splash',
})


def _guess_image_tag(aid: str) -> str:
    """从资产 ID 猜 image tag：`bg_classroom` → `bg`，`ling_happy` → `ling`。"""
    aid = (aid or '').strip()
    if not aid:
        return ''
    if '_' in aid:
        head = aid.split('_')[0]
        # `bg_*` / `cg_*` 这类前缀就是 tag 本身
        if head.lower() in ('bg', 'cg', 'ui', 'gui'):
            return head
        return head
    return aid


def position_choices(symbols, limit: int = 40) -> list:
    """
    位置词候选：内置/词表位置 + 项目里真实存在的自定义 transform。

    自定义 transform 一定要带上——真实项目里 `i11`、`leftish` 这种
    比 `left` 用得多，策划找不到它们就只能手打（用户抱怨过"变换选不到"）。
    """
    out = []
    for word in (getattr(symbols, 'positions', {}) or {}):
        if word not in out:
            out.append(word)
    reg = getattr(symbols, 'registry', None)
    for name in sorted((getattr(reg, 'transforms', {}) or {})):
        if name not in out:
            out.append(name)
    return out[:limit]


def _ask_choice(parent, title, prompt, choices):
    """
    一句话选择框：候选少时用按钮列表，多时用列表 + 确定。

    tkinter 没有 combobox 的稳定跨平台实现（ttk 在无头测试里是桩），
    所以这里自己用 Toplevel + Listbox 做一个，行为可预期。
    """
    import tkinter as tk

    win = tk.Toplevel(parent)
    win.title(title)
    tk.Label(win, text=prompt).pack(anchor='w', padx=8, pady=(8, 2))
    box = tk.Listbox(win, height=min(12, max(3, len(choices))), width=34)
    for c in choices:
        box.insert('end', c)
    box.pack(fill='both', expand=True, padx=8)
    if hasattr(box, 'selection_set') and choices:
        box.selection_set(0)
    picked = {'v': None}

    def ok(_e=None):
        sel = box.curselection() if hasattr(box, 'curselection') else ()
        if sel:
            picked['v'] = choices[sel[0]]
        win.destroy()

    def cancel(_e=None):
        win.destroy()

    btns = tk.Frame(win)
    btns.pack(fill='x', padx=8, pady=8)
    tk.Button(btns, text='确定', command=ok).pack(side='left')
    tk.Button(btns, text='取消', command=cancel).pack(side='left')
    if hasattr(box, 'bind'):
        box.bind('<Double-Button-1>', ok)
    try:
        win.grab_set()
    except Exception:
        pass
    parent.wait_window(win) if hasattr(parent, 'wait_window') else None
    return picked['v']


def beat_expression_word(beat: Beat, symbols=None) -> str:
    """
    Beat → 表情词（给策划看的那个词）。

    **必须保证"翻成人话再翻回来"是逐字无损的**，做不到就显示原属性名。
    理由（2026-09-28 实测到的事故）：

        show ling close_happy@left
          → 显示成「开心」（`expression_word_for` 靠子串把 close_happy 匹配到 happy）
          → 写回时「开心」→ `happy`，**close_ 丢了**
          → `show ling happy at left`  ← 演出变了，而且是静默变的

    这跟 `dsl.stage_word_if_lossless` 是同一条原则：**猜错一个表情就是演出事故**，
    所以宁可给策划看 `close_happy`（丑但准），也不给人话（好看但会改演出）。

    无损标准：反推出的词经 `attr_for` 翻回来，必须**逐字等于**原属性。
    """
    item = _first_show_item(beat)
    if not item:
        return ''
    parts = (item.get('image') or '').split()
    if len(parts) < 2:
        return ''
    tag, attrs = parts[0], parts[1:]
    words = []
    for attr in attrs:
        word = ''
        if symbols is not None:
            try:
                candidate = symbols.expression_word_for(tag, attr)
            except Exception:
                candidate = ''
            # 自证无损：翻回来必须是同一个属性
            if candidate:
                try:
                    if normalize_expression(candidate, beat, symbols) == attr:
                        word = candidate
                except Exception:
                    word = ''
        words.append(word or attr)
    return ' '.join(words)


def beat_position_word(beat: Beat, symbols=None) -> str:
    """Beat → 位置词（如「左」）。立绘没写 `@位置` 时退回 Beat 的 `at`。"""
    item = _first_show_item(beat)
    if not item:
        return ''
    at = (item.get('at') or '').strip() or (getattr(beat, 'at', '') or '').strip()
    if not at or symbols is None:
        return at
    try:
        return symbols.position_word_for(at) or at
    except Exception:
        return at


def expression_choices(beat: Beat, symbols, limit: int = 200) -> list:
    """
    当前立绘的**可选表情词**清单（给下拉/选择器用）。

    来源是 `symbols.attrs_of_tag(tag)`——即资源表里这个 tag 真正声明过的属性。
    这比 `DEFAULT_EXPRESSIONS` 准：项目自己的表情命名才算数，
    而且不会给出"这个角色根本没有的表情"。
    """
    item = _first_show_item(beat)
    if not item or symbols is None:
        return []
    tag = (item.get('image') or '').split()[0] if item.get('image') else ''
    try:
        attrs = symbols.attrs_of_tag(tag)
    except Exception:
        return []
    out = []
    for attr in attrs[:limit]:
        try:
            word = symbols.expression_word_for(tag, attr)
        except Exception:
            word = ''
        out.append(word or attr)
    return out


def normalize_expression(word: str, beat: Beat, symbols) -> str:
    """
    表情词 → 图像属性。**这一步必须在写回 `show` 之前做。**

    策划在卡片上看到/填写的是中文（「开心」），但脚本里必须是属性
    （`happy` / `close_happy`）——否则生成的就是 `show eileen 开心`，
    一个 Ren'Py 语法上不存在的图像。这条曾经只是"编译期才报错"，
    现在在写回的那一刻就落地。

    解析不到就**原样返回**：策划可能就是在写英文属性名（`close_happy`），
    那种写法本来就合法，不该被改写。
    """
    word = (word or '').strip()
    if not word or symbols is None:
        return word
    item = _first_show_item(beat)
    tag = (item.get('image') or '').split()[0] if item.get('image') else ''
    if not tag:
        return word
    try:
        return symbols.attr_for(tag, word) or word
    except Exception:
        return word


def normalize_expression_words(expr: str, image: str, symbols) -> list:
    """
    表情词 → 规范属性**列表**（逐词归一）。

    为什么要逐词（2026-09-30 真实数据实测）：`show ling base_arms_akimbo sigh`
    是合法的**多属性**写法，而界面上的表情词本来就是逐属性拼起来的
    （`base_arms_akimbo 无语`）。把整串当成**一个**属性去归一，会把它压成
    `ling base_arms_akimbo 无语` —— `sigh` 被静默吃掉、演出被改坏。
    逐词归一则是无损往返：`base_arms_akimbo` 原样、`无语` → `sigh`。

    解析不出来的词原样保留（策划可能就是在写英文属性名）。
    """
    words = [w for w in str(expr or '').split() if w]
    if not words:
        return []
    probe = Beat(seq=1, label='', text='', show=image or '')
    return [a for a in (normalize_expression(w, probe, symbols)
                        for w in words) if a]


def normalize_position(word: str, symbols) -> str:
    """
    位置词 → transform（`左` → `left`）。

    解析不到就原样返回：项目自定义 transform（`i11`、`leftish`）
    本来就是英文/自定义名。
    """
    word = (word or '').strip()
    if not word or symbols is None:
        return word
    try:
        return symbols.position_for(word) or word
    except Exception:
        return word


def apply_expression(show: str, word: str) -> str:
    """
    把 `show` 里**第一个立绘**的表情换成 `word`，其余项原样保留。

    语义：
      · `word` 为空 → 去掉表情（只留 tag）；
      · 不改位置，不动其他立绘，**不碰 clause 形态的项**（它们结构化列
        表达不了，改了会丢修饰符）。

    `word` 会先经 `attribute_for` 归一：策划写中文就落成英文属性，
    写 `close_happy` 就原样——与 DSL 编译期同一套规则，避免"卡片上写中文、
    脚本里出中文"。
    """
    items = _split_show_items(show or '')
    if not items:
        # 没有立绘可改：不凭空造一个（那会改变演出）
        return show or ''
    out = []
    done = False
    for item in items:
        if item.get('clause') or done:
            out.append(item)
            continue
        parts = (item.get('image') or '').split()
        if not parts:
            out.append(item)
            continue
        tag = parts[0]
        new = dict(item)
        new['image'] = ' '.join([p for p in ([tag] + ([word] if word else []))
                                 if p])
        out.append(new)
        done = True
    return _join_show_items(out)


def apply_position(show: str, word: str) -> str:
    """
    把位置写进 `show` 的**每一个普通立绘**项（`图@位置`）。

    为什么是"每一个"而不是第一个：位置是舞台级的观感，同一拍里两张立绘
    站不同位置时，策划改的是"这句台词时谁在左谁在右"——这里给的是
    统一位置，单张要精细控制在「高级」区改 `图@位置`。
    """
    items = _split_show_items(show or '')
    if not items:
        return show or ''
    out = []
    for item in items:
        if item.get('clause'):
            out.append(item)
            continue
        new = dict(item)
        new['at'] = word or ''
        out.append(new)
    return _join_show_items(out)


# ===================== 资产选择器 =====================

class AssetPickerDialog(tk.Toplevel):
    """
    通用资产选择弹窗：搜索 → 列表 → 说明/预览 → 选中；支持"新建资产"回写。

    比旧版多了两块信息，专门解决"只看文字根本不清楚效果"：
      · 说明面板：显示这条资产的人话描述（transform 会带上它的注释与 ATL）
      · 预览图：图片资产直接显示缩略图，不用靠脑补
    """

    def __init__(self, parent, engine, asset_types=None, title='选择资产',
                 allow_new: bool = True, on_new_asset=None,
                 current: str = '', current_type: str = ''):
        """
        `current` / `current_type`：**当前已经选中的那一项**。

        用户 2026-09-28："这个点击预览，能不能默认直接跳转到当前图片啊！
        不要让我选！" —— 所以打开时自动选中当前值、滚到它、并显示预览。
        """
        super().__init__(parent)
        self.engine = engine
        self.asset_types = asset_types or list(AssetCompletion.TYPE_MAP.keys())
        self.title(title)
        self.result = None
        self._items = []
        self._photo = None          # 必须留引用，否则被 GC 掉图就没了
        self._allow_new = allow_new
        self._on_new_asset = on_new_asset
        self._current = (current or '').strip()
        self._current_type = current_type or ''
        self._build()

    def _build(self):
        top = tk.Frame(self)
        top.pack(fill='x', padx=8, pady=(8, 0))
        tk.Label(top, text="搜索：").pack(side='left')
        self._query = tk.StringVar()
        entry = tk.Entry(top, textvariable=self._query)
        entry.pack(side='left', fill='x', expand=True)

        body = tk.Frame(self)
        body.pack(fill='both', expand=True, padx=8, pady=8)

        # ---- 左：分类（2904 张图不能靠打字） ----
        # 用户 2026-09-28：资源选择器要有"分类/分组"。
        # 点分类 = 只看这一类；不点 = 看全部。
        cats = tk.Frame(body, width=150)
        cats.pack(side='left', fill='y')
        tk.Label(cats, text='分类', font=('微软雅黑', 9, 'bold')
                 ).pack(anchor='w')
        self._cat_list = tk.Listbox(cats, height=14, width=22,
                                    exportselection=False)
        self._cat_list.pack(fill='y', expand=True)
        if hasattr(self._cat_list, 'bind'):
            self._cat_list.bind('<<ListboxSelect>>',
                                lambda e: self._populate(self._query.get()
                                                         if self._query else ''))
        self._categories = []

        mid = tk.Frame(body)
        mid.pack(side='left', fill='both', expand=True, padx=(8, 0))
        self._list = tk.Listbox(mid, height=14, width=34, exportselection=False)
        self._list.pack(fill='both', expand=True)
        if hasattr(self._list, 'bind'):
            self._list.bind('<<ListboxSelect>>', self._on_select)

        right = tk.Frame(body, width=280)
        right.pack(side='right', fill='y', padx=(8, 0))
        self._preview = tk.Label(right, text='（无预览）', height=8,
                                 width=34, anchor='center',
                                 bg='#f0f0f0', fg='#666')
        self._preview.pack(fill='x')
        self._detail = tk.Label(right, text='', justify='left', anchor='nw',
                                wraplength=280, height=8, fg='#222')
        self._detail.pack(fill='both', expand=True, pady=(6, 0))

        # 输入即过滤
        if hasattr(self._query, 'trace_add'):
            self._query.trace_add('write', lambda *a: self._populate(
                self._query.get()))

        self._load_categories()
        self._populate('')

        btn = tk.Frame(self)
        btn.pack(fill='x', padx=8, pady=(0, 8))
        tk.Button(btn, text="确定", command=self._on_ok).pack(side='left')
        tk.Button(btn, text="取消", command=self._on_cancel).pack(side='left')
        if self._allow_new:
            tk.Button(btn, text="＋ 新建资产…",
                      command=self._on_new).pack(side='right')
        if hasattr(self._list, 'bind'):
            self._list.bind('<Double-Button-1>', lambda e: self._on_ok())

    # ---------- 分类 ----------

    def _load_categories(self):
        """
        算好分类，填进左侧列表（第一项"全部"）。

        若当前已有选中值，**自动定位到它所在的分类**（用户要求"默认
        直接跳转、不要让我选"）。
        """
        self._groups = categorize_assets(self.engine, self.asset_types)
        self._categories = ['全部'] + list(self._groups)
        if hasattr(self._cat_list, 'delete'):
            self._cat_list.delete(0, 'end')
        if hasattr(self._cat_list, 'insert'):
            for name in self._categories:
                n = len(self._groups.get(name, []))
                self._cat_list.insert(
                    'end', name if name == '全部'
                    else f'{name}（{n}）')
            idx = self._category_of_current()
            if hasattr(self._cat_list, 'selection_set'):
                self._cat_list.selection_set(idx)
            if hasattr(self._cat_list, 'see'):
                self._cat_list.see(idx)

    def _category_of_current(self) -> int:
        """当前值落在哪个分类里（找不到就 0 = 全部）。"""
        if not self._current:
            return 0
        for i, name in enumerate(self._categories):
            if name == '全部':
                continue
            for _t, aid in self._groups.get(name, ()):
                if self._matches_current(aid):
                    return i
        return 0

    def _index_of_current(self) -> int:
        """当前值在条目列表里的下标（找不到返回 -1）。"""
        if not self._current:
            return -1
        for i, (_t, aid) in enumerate(self._items):
            if self._matches_current(aid):
                return i
        return -1

    def _matches_current(self, aid: str) -> bool:
        """
        这一条资产就是"当前正在用的"吗？

        **用户实测的缺口**：立绘卡片里存的是**图像名** `ling oh`，
        而资源表 ID 是 `ling_oh`。所以直接比字符串永远匹配不上，
        打开选择器就不会跳到当前那张图（用户："没有自动跳转到
        当前正在使用的表情！！！"）。

        三种形态都算命中：
          · ID 相等（`ling_oh`）
          · 图像名相等（`ling oh`）
          · 归一后相等（空格 ↔ 下划线）
        """
        cur = (self._current or '').strip()
        if not cur or not aid:
            return False
        if aid == cur:
            return True
        row = self._asset_row('image', aid)
        spec = image_spec_of_row(row, fallback=aid)
        if spec == cur:
            return True
        norm = lambda s: s.replace('_', ' ').strip()  # noqa: E731
        return norm(aid) == norm(spec) == norm(cur) or \
            norm(aid) == norm(cur) or norm(spec) == norm(cur)

    def _selected_category(self):
        if not hasattr(self._cat_list, 'curselection'):
            return None
        sel = self._cat_list.curselection()
        if not sel:
            return None
        i = sel[0]
        return self._categories[i] if i < len(self._categories) else None

    def _populate(self, query: str = ''):
        """
        刷新条目列表。

        分类与搜索是**叠加**的：先按分类取子集，再按查询过滤。
        这样"在背景里搜 classroom"和"在所有里搜 classroom"都能用。

        条目显示的是**能直接用的图像名**（`ling ah`）而不是资产 ID（`_ah`）
        —— 用户实测："立绘错位了"，因为看到 `_ah` 根本不知道该写什么。
        """
        cat = self._selected_category()
        if cat and cat != '全部':
            pool = list(self._groups.get(cat, []))
            if query.strip():
                q = query.strip().lower()
                pool = [(t, a) for t, a in pool if q in a.lower()]
            self._items = pool
        else:
            self._items = search_assets(self.engine, query, self.asset_types)
        if hasattr(self._list, 'delete'):
            self._list.delete(0, 'end')
        if hasattr(self._list, 'insert'):
            for atype, aid in self._items:
                self._list.insert('end', self._label_for(atype, aid))

        # **默认跳到当前值**：选中它、滚到它、并显示预览与说明。
        # 用户："不要让我选！" —— 打开弹窗就是为了换一张图，
        # 起点应该是"我现在是什么"，而不是列表第一行。
        idx = self._index_of_current()
        if idx >= 0:
            if hasattr(self._list, 'selection_clear'):
                self._list.selection_clear(0, 'end')
            if hasattr(self._list, 'selection_set'):
                self._list.selection_set(idx)
            if hasattr(self._list, 'see'):
                self._list.see(idx)
            if hasattr(self._list, 'activate'):
                try:
                    self._list.activate(idx)
                except Exception:
                    pass
            self._show_detail(self._items[idx])
        else:
            self._show_detail(None)

    def _label_for(self, atype: str, aid: str) -> str:
        """
        列表里显示什么。

        图片显示成**图像名**（`ling ah`，即 show 列该写的）；能看出与 ID
        不同时把 ID 括在后面（`ling ah  (_ah)`），方便对照资源表。
        其他类型就用 ID。
        """
        if atype != 'image':
            return f'[{atype}] {aid}'
        row = self._asset_row(atype, aid)
        spec = image_spec_of_row(row, fallback=aid)
        if spec and spec != aid:
            return f'{spec}   ({aid})'
        return spec or aid

    def _asset_row(self, atype: str, aid: str) -> dict:
        reg = None
        try:
            reg = self.engine.get_registry() if self.engine else None
        except Exception:
            reg = None
        if reg is None:
            return {}
        from storygraph.assets.asset_completion import AssetCompletion
        attr = AssetCompletion.TYPE_MAP.get(atype)
        if not attr:
            return {}
        return (getattr(reg, attr, {}) or {}).get(aid) or {}

    def _selected(self):
        if not hasattr(self._list, 'curselection'):
            return None
        sel = self._list.curselection()
        if not sel:
            return None
        return self._items[sel[0]] if sel[0] < len(self._items) else None

    def _on_select(self, event=None):
        self._show_detail(self._selected())

    def _show_detail(self, item):
        """更新右侧说明 + 预览。item 为 None 时清空。"""
        if item is None:
            self._set_text(self._detail, '')
            self._set_photo(None)
            self._set_text(self._preview, '（无预览）')
            return
        atype, aid = item
        self._set_text(self._detail, asset_detail(self.engine, atype, aid))

        path = asset_preview_path(self.engine, atype, aid)
        if path is None:
            self._set_photo(None)
            self._set_text(
                self._preview,
                '（该类型无预览图）' if atype != 'image'
                else '（找不到图片文件）')
            return
        photo = load_thumbnail(path)
        if photo is None:
            self._set_photo(None)
            self._set_text(self._preview, f'无法预览：{Path(path).name}')
        else:
            self._photo = photo
            self._set_photo(photo)

    @staticmethod
    def _set_text(widget, text):
        if hasattr(widget, 'config'):
            try:
                widget.config(text=text)
            except Exception:
                pass

    def _set_photo(self, photo):
        if hasattr(self._preview, 'config'):
            try:
                self._preview.config(image=photo if photo is not None else '')
            except Exception:
                pass

    def _on_ok(self):
        sel = self._selected()
        if sel is not None:
            self.result = sel
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()

    def _on_new(self):
        asset_type = simpledialog.askstring("新建资产", "资产类型：")
        if not asset_type or asset_type not in AssetCompletion.TYPE_MAP:
            messagebox.showerror("类型无效",
                                 "请输入合法类型："
                                 + "/".join(AssetCompletion.TYPE_MAP))
            return
        asset_id = simpledialog.askstring("新建资产", "资产 ID：")
        if not asset_id:
            return
        path = simpledialog.askstring("新建资产", "资源路径（可选）：") or ''
        if self._on_new_asset is not None:
            self._on_new_asset(asset_type.strip(), asset_id.strip(), path.strip())
        # 选中刚新建的，方便直接填回
        self.result = (asset_type.strip(), asset_id.strip())
        self.destroy()

    def pick(self):
        """
        模态打开，返回 `(asset_type, asset_id)` 或 None。

        **注意**：调用方若要把它填进「立绘」列，应该用
        `picked_image_spec()` 拿**图像名**（`ling ah`），而不是 asset_id
        （`_ah`）—— 后者是资源表内部键，写进 show 列是错的。
        """
        self.grab_set()
        self.wait_window(self)
        return self.result

    def picked_image_spec(self, fallback: str = '') -> str:
        """
        选中的**图像名**（卡片的「立绘」列该写的东西）。

        用户实测的"立绘错位"就是这个：资源表 ID 是 `_ah`，
        而脚本里要写的是 `ling ah`。选择器必须给出**能用的那个**。
        """
        if not self.result:
            return fallback
        atype, aid = self.result
        if atype != 'image':
            return aid
        return image_spec_of_row(self._asset_row(atype, aid),
                                 fallback=aid) or aid


# ===================== Beat 快速编辑面板 =====================

class BeatEditorPanel(tk.Frame):
    """
    选中一拍后的快速编辑面板 —— **4 项卡片**。

    策划真正会改的只有四样（`PRIMARY_FIELDS`）：

        说话人 / 台词 / 表情 / 位置

    前两样是 Beat 的真实属性；后两样是 `show`（`ling happy@left`）的投影
    （见 `apply_expression` / `apply_position`）。**不能让策划直接编辑
    `图@位置` 这种语法**——那正是要消灭的负担。

    其余全部进「高级」区（`ADVANCED_FIELDS`）并**默认收起 + 灰显只读**：
    不是删掉（程序与老策划还要用），而是不让它们占第一眼的注意力。
    收起时**不会清空**里面的值——`collect_beat` 从当前 Beat 出发，
    只覆盖真正在编辑的字段（曾经整体覆盖会把 Stage/音频/注释清掉）。

    与 Excel 那面的关系：Excel 是 20 列结构化列 + Stage 高级通道，
    两边通过 `compose_stage` / `hydrate_from_dsl` 互为投影，不会各输出一次。
    """

    #: 主区**常驻**字段（每一拍都会看、会填的）。
    #:
    #: 2026-09-30 按用户要求调整：「背景 / 音乐 / 语音 / 备注」从折叠的高级区
    #: **提上来** —— 它们本来就属于一拍的基本信息，藏起来等于没有。
    #: 不在此列的一律进高级区（默认灰显只读，收起时不清空值）。
    #: 注：`Expression` / `Position` 不在任何一栏里，它们**只在立绘卡内**编辑。
    PRIMARY_FIELDS = ['Speaker', 'Text', 'Scene', 'BGM', 'Voice', 'Comment']

    #: 高级区（默认灰显、可展开）：只留**程序向**的东西。
    #: `Stage` 的职责已明确为"转场 / 停顿这类**非立绘**指令"（见 _HINTS 与
    #: _MIGRATE_HINT），立绘一律走上面的立绘区 —— 两条通道不再重叠。
    ADVANCED_FIELDS = ['Stage', 'SFX', 'CodeBlock', 'Label', 'Kind', 'Notes']

    # 走资产选择器的字段（一格一个值，能点选）
    _ASSET_FIELDS = {
        'Speaker': ('character',),
        'Scene': ('image',),
        'Voice': ('voice',),
    }

    # 单行字段（按界面上从上到下的顺序）；表情/位置已移到立绘卡内
    _SINGLE_FIELDS = ['Speaker', 'Scene', 'Voice', 'Comment',
                      'Label', 'Kind', 'Notes']
    # 多行字段 → 行高（DSL 与长文本要能换行写）
    _MULTI_FIELDS = {'Text': 4, 'Stage': 3, 'BGM': 2, 'SFX': 2,
                     'CodeBlock': 3}
    # 真正的展示顺序：先 4 项卡片，再高级区
    _ORDER = PRIMARY_FIELDS + ADVANCED_FIELDS

    _HINTS = {
        'Scene': '背景图像：ID 或图像名（如 bg_bg1_2）',
        'BGM': '[音乐] 春天花瓣 fadein 1.0　/　[停止音乐]',
        'Voice': '语音资产 ID（没有就留空）',
        'Comment': '给这一拍留的说明／待办，导出时原样保留成注释',
        'SFX': '[音效] 开门',
        # Stage 的职责必须**写死在界面上**（用户 2026-09-30：
        # "stage 到底用来干什么？？？显示立绘吗？可是立绘又有了……"）
        'Stage': '只放转场 / 停顿这类指令：[转场] dissolve、[停顿] 1.0。'
                 '立绘请写在上面「立绘」区（那里是结构化编辑，能选图/换表情）',
    }

    def __init__(self, parent, engine, on_new_asset=None, **kw):
        super().__init__(parent, **kw)
        self.engine = engine
        self.on_new_asset = on_new_asset
        self._beat = None
        self._widgets = {}
        self._multi = set()
        #: 单行字段的 StringVar（Entry 的读写只能通过它，见 _wset）
        self._vars = {}
        self._note_var = tk.StringVar(value='')
        self._stage_var = tk.StringVar(value='')
        self._adv_open = False
        #: 载入时 Stage 框里的原文（结构化列的投影）。写回时拿它判断
        #: "用户到底有没有手改过 Stage"（见 collect_beat）。
        self._stage_loaded = ''
        self._advanced_rows = []
        #: 立绘数组的每张卡（见 add_sprite_row / _collect_sprites）。每项：
        #: {'card', 'frame'(与 card 同，兼容旧调用), 'image', 'expression',
        #:  'position', 'speaking', 'order_index'}
        self._sprite_widgets = []
        #: 缩略图加载器：防抖 120ms + 后台解码 + 内存 LRU + 复用磁盘缓存
        self._thumb_loader = ThumbLoader(
            self, max_size=(T.CARD['thumbW'], T.CARD['thumbH']),
            project_root=self._project_root())
        self._add_card = None            # 网格末尾的「＋ 添加立绘」卡
        self._selected_sprite = None     # 当前选中的立绘卡
        self._toast_win = None
        self._toast_after = None
        self._build()

    def _project_root(self):
        """项目根：缩略图解析与磁盘缓存都要它（拿不到就 None，会退回 cwd 试）。"""
        for attr in ('project_root', 'root', 'project_dir'):
            val = getattr(self.engine, attr, None)
            if val:
                return val
        try:
            reg = self.engine.get_registry() if self.engine else None
            return getattr(reg, 'project_root', None) if reg else None
        except Exception:                    # noqa: BLE001
            return None

    # ---------- 控件读写（StringVar 与 Text 统一） ----------

    def _wget(self, name: str) -> str:
        if name in self._multi:
            w = self._widgets.get(name)
            if w is None:
                return ''
            return w.get('1.0', 'end-1c') if hasattr(w, 'get') else ''
        var = self._vars.get(name)
        if var is None:
            return ''
        return var.get() if hasattr(var, 'get') else ''

    def _wset(self, name: str, value: str):
        """
        把一个字段写进控件。

        **单行字段走 StringVar，多行字段走 Text 的 delete/insert。**
        不能对控件本身调 `set()`——`tk.Entry` 没有这个方法，
        以前那样写会让单行字段**静默写不进去**（用户 2026-09-28 实测：
        卡片上只有台词有值，说话人/表情/位置全空）。
        """
        if name in self._multi:
            w = self._widgets.get(name)
            if w is None or not hasattr(w, 'delete'):
                return
            # **只读的 Text 会静默吞掉写入**（Tk 不报错、内容仍是空的）——
            # 于是高级区的 Stage / 代码块永远显示空白，而"看似为空"又会让
            # 写回把它们清掉。所以这里临时放开 state 写完再还原。
            prev_state = None
            try:
                prev_state = w.cget('state')
                if prev_state == 'disabled' and hasattr(w, 'configure'):
                    w.configure(state='normal')
            except Exception:                # noqa: BLE001
                prev_state = None
            try:
                w.delete('1.0', 'end')
                if value:
                    w.insert('1.0', value)
            finally:
                if prev_state == 'disabled':
                    try:
                        w.configure(state='disabled')
                    except Exception:        # noqa: BLE001
                        pass
            if name == 'Text':
                self._update_char_count()     # 换拍时字数要跟着刷（不能留旧值）
            return
        var = self._vars.get(name)
        if var is not None and hasattr(var, 'set'):
            var.set(value or '')

    def _build(self):
        row = 0

        # ---- 4 项卡片（永远可见、可编辑） ----
        tk.Label(self, text='这一拍', font=('微软雅黑', 10, 'bold')
                 ).grid(row=row, column=0, columnspan=5, sticky='w')
        row += 1
        # 先说话人与台词；立绘用下面的「立绘数组」独立编辑
        row = self._build_field('Speaker', row, enabled=True)
        row = self._build_field('Text', row, enabled=True)

        # ---- 立绘（数组，不是单数） ----
        # 用户 2026-09-28：一张卡片可以同时有多个立绘，各自独立控制位置。
        # 数据层本来就支持（`show` 是 `;` 分隔列表），但以前卡片只显示第一张。
        tk.Label(self, text='立绘（可多个）',
                 font=T.FONTS['section']).grid(
                     row=row, column=0, columnspan=5, sticky='w',
                     pady=(T.SPACE['md'], T.SPACE['xs']))
        row += 1
        # 网格容器：卡片按窗口宽度自动排布（见 _relayout_sprites）
        self._sprites_frame = tk.Frame(self, background=T.t('bg.surface'))
        self._sprites_frame.grid(row=row, column=0, columnspan=5, sticky='we')
        row += 1
        # 空态：一张都没有时给一句解释（"不是坏了，是还没有"）
        self._sprite_empty = tk.Label(
            self, text='这一拍还没有立绘，背景与音乐会继承上一拍',
            font=T.FONTS['caption'], fg=T.t('text.tertiary'), anchor='w')
        self._sprite_empty.grid(row=row, column=0, columnspan=5, sticky='we',
                                pady=(T.SPACE['xs'], 0))
        row += 1
        # Stage 迁移提示：**只在"立绘写在 Stage 列里"时出现**
        # （用户 2026-09-30：已经导入了的立绘没有显示！！！确认台上确实有人）
        self._stage_hint = tk.Frame(self, background=T.t('state.warningBg'))
        self._stage_hint_label = tk.Label(
            self._stage_hint, text='', font=T.FONTS['caption'],
            fg=T.t('state.warning'), background=T.t('state.warningBg'),
            anchor='w', justify='left', wraplength=520)
        self._stage_hint_label.pack(side='left')
        self._stage_hint_btn = tk.Button(
            self._stage_hint, text='迁移到立绘区',
            command=self._migrate_stage_sprites)
        self._stage_hint_btn.pack(side='left', padx=(T.SPACE['sm'], 0))
        self._stage_hint.grid(row=row, column=0, columnspan=5, sticky='we',
                              pady=(T.SPACE['xs'], 0))
        self._stage_hint.grid_remove()          # 默认隐藏
        row += 1

        # ---- 舞台基本信息（背景 / 音乐 / 语音 / 备注） ----
        # 用户 2026-09-30："Scene、BGM、Comment、Voice 都不应当在高级内部，
        # 请把它提前" —— 这四项每拍都要看，藏在折叠区里等于没有。
        tk.Label(self, text='舞台（背景 / 音乐 / 语音 / 备注）',
                 font=T.FONTS['section']).grid(
                     row=row, column=0, columnspan=5, sticky='w',
                     pady=(T.SPACE['md'], T.SPACE['xs']))
        row += 1
        for name in ('Scene', 'BGM', 'Voice', 'Comment'):
            row = self._build_field(name, row, enabled=True)

        # ---- 继承提示（持续状态默认继承，这里只是把它显示出来） ----
        self._inherit_var = tk.StringVar(value='')
        tk.Label(self, textvariable=self._inherit_var, justify='left',
                 anchor='w', wraplength=520, fg='#0A5',
                 font=('微软雅黑', 8)).grid(
                     row=row, column=0, columnspan=5, sticky='we', pady=(6, 0))
        row += 1

        # ---- 条件显示（台词/立绘/背景都能挂，统一一个入口） ----
        cond = tk.Frame(self)
        cond.grid(row=row, column=0, columnspan=5, sticky='we', pady=(6, 0))
        tk.Button(cond, text='＋ 条件', command=self._focus_condition
                  ).pack(side='left')
        self._condition_var = tk.StringVar(value='')
        tk.Entry(cond, textvariable=self._condition_var,
                 font=('Consolas', 9)).pack(side='left', fill='x',
                                            expand=True, padx=(6, 0))
        tk.Label(cond, text='（留空＝无条件；如 love >= 5）',
                 font=('微软雅黑', 8), fg='#888').pack(side='left')
        row += 1

        # ---- 高级区（默认隐藏 + 灰显） ----
        self._adv_toggle = tk.Button(
            self, text='▸ 高级（Stage 指令 / 音效 / 代码 / 章节 / 类型 / 备注）',
            command=self.toggle_advanced, anchor='w',
            relief='flat', fg='#555')
        self._adv_toggle.grid(row=row, column=0, columnspan=5,
                              sticky='we', pady=(8, 0))
        row += 1

        self._adv_frame = tk.Frame(self)
        self._adv_frame.grid(row=row, column=0, columnspan=5, sticky='we')
        self._adv_frame.grid_remove()          # 默认收起
        self._adv_row = 0
        for name in self.ADVANCED_FIELDS:
            # `enabled=True`：展开后**可编辑**（收起时整块 grid_remove，
            # 不影响观感）。此前这里是 `enabled=False`，而只读的 Text 会
            # **静默吞掉** `_wset` 的写入 → 这些字段永远显示空白，
            # 而"看着是空的"又让写回把它们清掉（"展开高级区按下一句 → 
            # Stage / 代码块 / 注释被清空"，2026-09-30 实测）。
            self._adv_row = self._build_field(
                name, self._adv_row, enabled=True, parent=self._adv_frame)
        self._adv_frame.columnconfigure(1, weight=1)
        row += 1

        self.columnconfigure(1, weight=1)

        # 动作条：预览编译结果 / 看词表 / 解释舞台
        act = tk.Frame(self)
        act.grid(row=row, column=0, columnspan=5, sticky='we', pady=(6, 0))
        tk.Button(act, text="预览编译结果",
                  command=self.preview_compiled).pack(side='left')
        tk.Button(act, text="舞台词表",
                  command=self.show_keywords).pack(side='left')
        tk.Button(act, text="解释舞台列",
                  command=self.explain_stage).pack(side='left')
        row += 1

        # 说明区：选中/填好一个变换、立绘、音乐后，这里给出"它是什么"
        tk.Label(self, text="说明").grid(row=row, column=0, sticky='nw')
        tk.Label(self, textvariable=self._note_var, justify='left',
                 anchor='nw', wraplength=380, fg='#444'
                 ).grid(row=row, column=1, columnspan=4, sticky='we')
        row += 1

        # 舞台快照：这一拍播出来玩家看见什么（背景/立绘/音乐）
        tk.Label(self, text="舞台快照").grid(row=row, column=0, sticky='nw')
        tk.Label(self, textvariable=self._stage_var, justify='left',
                 anchor='nw', wraplength=380, fg='#0A5'
                 ).grid(row=row, column=1, columnspan=4, sticky='we')

    def _build_field(self, name, row, enabled=True, parent=None):
        """
        建一个字段行。

        `enabled=False` 时**灰显且只读**（保留给"纯展示"用途）。
        注意：**只读的 `Text` 会静默吞掉程序化写入**（Tk 不报错、内容仍空），
        所以高级区不能用它来"灰显"——那会让 Stage / 代码块永远显示空白，
        而"看着是空的"又会让写回把它们清空。见 `_wset` 与 `collect_beat`。
        """
        host = parent if parent is not None else self
        label = name
        hint = self._HINTS.get(name, '')
        col0 = tk.Frame(host)
        col0.grid(row=row, column=0, sticky='nw')
        tk.Label(col0, text=label,
                 fg='#222' if enabled else '#999').pack(anchor='w')
        if hint:
            tk.Label(col0, text=hint, font=('微软雅黑', 7),
                     fg='#888' if enabled else '#bbb',
                     wraplength=120, justify='left').pack(anchor='w')

        if name in self._MULTI_FIELDS:
            import tkinter.scrolledtext as st
            widget = st.ScrolledText(host, height=self._MULTI_FIELDS[name],
                                     wrap='word', font=('Consolas', 9),
                                     background='white' if enabled else '#f2f2f2')
            widget.grid(row=row, column=1, sticky='we')
            self._multi.add(name)
            if not enabled and hasattr(widget, 'configure'):
                widget.configure(state='disabled')
            if name == 'Text' and enabled:
                # 台词字数：超长台词是演出现场真会踩的坑（一屏读不完）
                self._attach_char_counter(widget, host, row)
        else:
            var = tk.StringVar()
            widget = tk.Entry(host, textvariable=var,
                              state='normal' if enabled else 'disabled',
                              disabledbackground='#f2f2f2',
                              disabledforeground='#999')
            widget.grid(row=row, column=1, sticky='we')
            # **必须自己记住 StringVar**：`tk.Entry` 没有 `set()` 方法，
            # 读写只能通过 textvariable。以前 `_wset` 判 `hasattr(w, 'set')`
            # 对 Entry 恒为 False，于是单行字段**静默**写不进去
            # （多行的 ScrolledText 有 insert()，所以只有 Entry 受影响）。
            self._vars[name] = var
        self._widgets[name] = widget

        types = self._ASSET_FIELDS.get(name)
        if types and enabled:
            tk.Button(host, text="🔍", width=2,
                      command=lambda n=name, t=types: self._pick(n, t)
                      ).grid(row=row, column=2)
            tk.Button(host, text="?说明", width=5,
                      command=lambda n=name, t=types: self._explain(n, t)
                      ).grid(row=row, column=3)
        return row + 1

    #: 台词超过这个字数就给提醒（一屏读不完、演出时长不可控）
    TEXT_WARN_CHARS = 200

    def _attach_char_counter(self, widget, host, row):
        """
        台词字数统计（设计规格 §3.2）。

        为什么值得做：Ren'Py 一句台词在对话框里显示有上限，太长的句子
        玩家一屏读不完、演出节奏也会拖垮。字数摆在眼前是最省事的提醒。
        """
        var = tk.StringVar(value='')
        label = tk.Label(host, textvariable=var, font=T.FONTS['caption'],
                         fg=T.t('text.tertiary'))
        label.grid(row=row, column=2, columnspan=2, sticky='w',
                   padx=(T.SPACE['sm'], 0))
        self._char_count_var = var
        self._char_count_label = label
        for seq in ('<KeyRelease>', '<<Modified>>'):
            try:
                widget.bind(seq, lambda e: self._update_char_count())
            except Exception:                # noqa: BLE001
                pass

    def _update_char_count(self, *_a):
        """刷新台词字数；超阈值时**同时**给颜色与文字（不靠颜色单编码）。"""
        var = getattr(self, '_char_count_var', None)
        if var is None:
            return
        text = self._wget('Text') or ''
        n = len(''.join(str(text).split()))       # 不计空白与换行
        if n <= 0:
            var.set('')
            return
        warn = n > self.TEXT_WARN_CHARS
        var.set(f'{n} 字' + (f'（超过 {self.TEXT_WARN_CHARS}，建议拆句）'
                             if warn else ''))
        label = getattr(self, '_char_count_label', None)
        if label is not None:
            try:
                label.config(fg=(T.t('state.warning') if warn
                                 else T.t('text.tertiary')))
            except Exception:                # noqa: BLE001
                pass

    def toggle_advanced(self):
        """展开/收起高级区。收起时字段**灰显且只读**，数据照旧保留。"""
        self._adv_open = not self._adv_open
        if self._adv_open:
            self._adv_frame.grid()
            self._adv_toggle.config(
                text='▾ 高级（Stage 指令 / 音效 / 代码 / 章节 / 类型 / 备注）')
        else:
            self._adv_frame.grid_remove()
            self._adv_toggle.config(
                text='▸ 高级（Stage 指令 / 音效 / 代码 / 章节 / 类型 / 备注）')

    # ---------- 立绘数组（可增删，每张独立表情/位置） ----------

    def _clear_sprite_rows(self):
        """清空所有立绘卡（切拍时调用；这里才真正销毁）。"""
        for item in list(getattr(self, '_sprite_widgets', []) or []):
            self._destroy_card(item)
        self._sprite_widgets = []
        self._selected_sprite = None
        self._relayout_sprites()

    def add_sprite_with_picker(self):
        """
        「＋ 添加立绘」：**先选图，再建卡**。

        用户 2026-09-30：
        > 你的那个点击添加立绘，只能添加空立绘啊？？？你倒是给我个选项呀

        以前点了就直接建一张**空卡**，还得再点一次卡上的「换图」才知道
        有哪些图可选 —— 等于让人点两次、第一步还看不到任何选项。
        现在先弹选择器：选好了才建卡（表情跟着选中的图走），
        **取消就什么都不加**（不留下空卡）。
        """
        probe = tk.StringVar(value='')
        try:
            picked = self._pick_sprite_image(probe)
        except Exception:                    # noqa: BLE001 - 桩环境/无选择器
            picked = False
        spec = ''
        try:
            spec = (probe.get() or '').strip()
        except Exception:                    # noqa: BLE001
            spec = ''
        if not picked or not spec:
            self._set_note('没有选图，就没有添加立绘（先选一张再加）。')
            return None
        item = self.add_sprite_row({'image': spec, 'expression': '',
                                    'position': '', 'at': ''})
        self._sync_expression_from_spec(item)   # 表情 chip 跟着选中的图走
        self._sync_card_spec(item)
        return item

    def add_sprite_row(self, row: dict = None, speaking: bool = False,
                       order_index: int = None, extra_rows: list = None):
        """
        加一张立绘卡（网格里的一个格子），返回 item（失败返回 None）。

        `row` 为 None 时是"用户点了添加"——给一张空卡，点击它去选图。

        `speaking=True`：正在说话的那张会加**左侧主色条 + `▶ 说话`**。
        用户 2026-09-28：
        > 不仅要显示登场角色，正在说话的角色还要提前、高光显示
        理由很实在：一屏好几张立绘时，"这句话是谁说的"要一眼看出来。

        `order_index`：这张卡在 `beat.show` 里的**原始出场位置**。
        显示顺序会把说话人提到最前，但写回必须按原始顺序（出场顺序是演出语义）。
        """
        host = getattr(self, '_sprites_frame', None)
        if host is None:
            return None
        row = dict(row or {})
        item = self._make_card(
            image_var=tk.StringVar(value=row.get('image') or ''),
            expr_var=tk.StringVar(value=row.get('expression') or ''),
            pos_var=tk.StringVar(value=row.get('position') or ''),
            speaking=bool(speaking),
            order_index=(len(self._sprite_widgets) if order_index is None
                         else int(order_index)))
        # 同一角色在本拍出现多次（换表情）时：一张卡代表这个角色，其余条目
        # 存在 `extra_rows` 里 —— **显示聚合、数据不丢**
        # （写回顺序见 _collect_sprites：extras 在前、生效条目在后）
        if item is None:                     # 卡片建失败：不要把 None 塞进列表
            return None
        item['extra_rows'] = list(extra_rows or [])
        item['repeat'] = len(item['extra_rows']) + 1
        self._sprite_widgets.append(item)
        self._relayout_sprites()
        return item

    def _remove_sprite_row(self, target):
        """
        删掉一张立绘卡（**可撤销**）。

        `target` 可以是 item 字典，也可以是卡片对象（兼容旧调用点传 frame）。

        为什么给撤销而不是弹确认框：删错一张立绘的代价高，但确认框会打断
        "连着删几张"的节奏。Toast + 8 秒撤销是更好的折中——直到撤销窗口
        结束，卡片只是从网格里摘掉，没有销毁。
        """
        item = target if isinstance(target, dict) else None
        if item is None:
            for w in list(self._sprite_widgets):
                if w.get('card') is target or w.get('frame') is target:
                    item = w
                    break
        if item is None:
            return
        try:
            idx = self._sprite_widgets.index(item)
        except ValueError:
            return

        self._sprite_widgets.pop(idx)
        if self._selected_sprite is item:
            self._selected_sprite = None
        card = item.get('card')
        if card is not None:
            try:
                card.grid_forget()          # 只摘掉，不销毁（撤销要放回去）
            except Exception:               # noqa: BLE001
                pass
        self._relayout_sprites()

        name = ''
        try:
            name = (item['image'].get() or '').strip()
        except Exception:                    # noqa: BLE001
            name = ''

        def _restore():
            self._restore_sprite(item, idx)

        self._toast(f'已删除立绘「{name or "空"}」', undo=_restore)

    # ---------- 立绘卡：建/毁/排布/选中 ----------

    def _make_card(self, image_var, expr_var, pos_var, speaking=False,
                   order_index=0):
        """
        建一张立绘卡，并把它与数据（三个 StringVar）绑在一起。

        卡片**只显示与上报点击**，改数据由面板做 —— 视图层不碰 `beat.show`，
        否则"改个表情"要同时改 3 个地方，迟早漏一个。
        """
        holder = {}                       # 让回调能拿到后建出来的 item
        try:
            card = SpriteCard(
                self._sprites_frame, self._thumb_loader,
                image_var, expr_var, pos_var,
                speaking=speaking,
                path_resolver=self._sprite_thumb_path,
                on_select=lambda: self._select_sprite(holder.get('item')),
                on_open_picker=lambda: self._open_sprite_picker(
                    holder.get('item')),
                on_delete=lambda: self._remove_sprite_row(holder.get('item')),
                on_cycle_expression=lambda: self._cycle_expression(
                    holder.get('item')),
                on_cycle_position=lambda: self._cycle_position(
                    holder.get('item')))
        except Exception:                    # noqa: BLE001
            # 一张卡建不出来，**不能让整个面板刷新崩掉** —— 宁可少一张卡，
            # 也不能让用户看到"立绘全没了"（2026-09-30 的真实投诉：
            # `asset_preview_path` 返回 Path，而加载器对它调了 `.strip()`，
            # 于是一个 AttributeError 把整次 load_beat 带崩）
            return None
        item = {'card': card, 'frame': card, 'image': image_var,
                'expression': expr_var, 'position': pos_var,
                'speaking': bool(speaking), 'order_index': int(order_index)}
        holder['item'] = item
        return item

    def _destroy_card(self, item):
        """真正销毁一张卡（取消挂起的加载任务 + 摘掉 + destroy）。"""
        card = item.get('card') if isinstance(item, dict) else None
        if card is None:
            return
        try:
            self._thumb_loader.forget(card)
        except Exception:                    # noqa: BLE001
            pass
        for op in ('grid_forget', 'destroy'):
            try:
                getattr(card, op)()
            except Exception:                # noqa: BLE001
                pass
        if isinstance(item, dict):
            item['card'] = None
            item['frame'] = None

    def _restore_sprite(self, item, idx):
        """撤销删除：把卡片放回原来的位置。"""
        card = item.get('card') if isinstance(item, dict) else None
        if card is None:
            return
        try:
            card.grid()
        except Exception:                    # noqa: BLE001
            pass
        pos = max(0, min(int(idx), len(self._sprite_widgets)))
        self._sprite_widgets.insert(pos, item)
        self._relayout_sprites()
        name = ''
        try:
            name = (item['image'].get() or '').strip()
        except Exception:                    # noqa: BLE001
            name = ''
        self._toast(f'已恢复立绘「{name or "空"}」')

    def _grid_columns(self) -> int:
        """每行放几张卡（按可用宽度算；还没布局时按 5 张）。"""
        g = T.CARD
        try:
            avail = int(self._sprites_frame.winfo_width())
        except Exception:                    # noqa: BLE001 - 测试桩
            avail = 0
        if avail <= 1:
            return 5
        cols = (avail + g['gap']) // (g['w'] + g['gap'])
        return int(max(2, min(6, cols)))

    def _relayout_sprites(self):
        """
        重排立绘网格：卡片按行铺开，末尾放「＋ 添加立绘」，无立绘时显示空态说明。

        高度与数量**脱钩**（这是"多个立绘太占地方"的解法）：
        3 张卡占 1 行 ≈176px，而不是旧版的 3×210=630px。
        """
        frame = getattr(self, '_sprites_frame', None)
        if frame is None:
            return
        try:
            for child in frame.winfo_children():
                child.grid_forget()
        except Exception:                    # noqa: BLE001
            pass
        cols = self._grid_columns()
        for i, item in enumerate(self._sprite_widgets):
            card = item.get('card')
            if card is None:
                continue
            try:
                card.grid(row=i // cols, column=i % cols,
                          padx=(0, T.CARD['gap']),
                          pady=(0, T.CARD['gap']), sticky='nw')
            except Exception:                # noqa: BLE001
                pass

        self._ensure_add_card()
        if self._add_card is not None:
            i = len(self._sprite_widgets)
            try:
                self._add_card.grid(row=i // cols, column=i % cols,
                                    padx=(0, T.CARD['gap']),
                                    pady=(0, T.CARD['gap']), sticky='nw')
            except Exception:                # noqa: BLE001
                pass

        empty = getattr(self, '_sprite_empty', None)
        if empty is not None:
            try:
                if self._sprite_widgets:
                    empty.grid_remove()
                else:
                    empty.grid()
            except Exception:                # noqa: BLE001
                pass

    def _ensure_add_card(self):
        """确保末尾那张虚线「＋ 添加立绘」卡存在（原地添加，不用滚到底部找按钮）。"""
        if getattr(self, '_add_card', None) is not None:
            return
        try:
            self._add_card = AddCard(self._sprites_frame,
                                     command=self.add_sprite_with_picker)
            Tooltip(self._add_card, '添加一张立绘（先选图；双击已有卡片＝换图）')
        except Exception:                    # noqa: BLE001
            self._add_card = None

    def _select_sprite(self, item):
        """选中一张立绘卡（单选；顺带把其它卡的高亮清掉）。"""
        if item is None:
            return
        self._selected_sprite = item
        for w in self._sprite_widgets:
            card = w.get('card')
            if card is None:
                continue
            try:
                card.set_selected(w is item)
            except Exception:                # noqa: BLE001
                pass

    def _open_sprite_picker(self, item):
        """双击卡片 / 点变体：打开资产选择器换图，然后刷新图与候选。"""
        if item is None:
            return
        if not self._pick_sprite_image(item['image']):
            return                           # 取消：什么都不动（连刷新都省了）
        # 换图后要**让新图的属性成为写回真源**，否则旧 expression 会把
        # 刚选的属性反向覆盖（"换了图没生效"）。
        self._sync_expression_from_spec(item)
        card = item.get('card')
        if card is not None:
            try:
                card.refresh_thumb()
            except Exception:                # noqa: BLE001
                pass
        self._refresh_sprite_candidates(item)

    # ---------- 立绘卡：表情 / 位置（**唯一入口**） ----------

    def _sprite_expression_choices(self, item) -> list:
        """
        这张立绘可选的表情词。**两个来源合并**：

          1. **资源表里同 tag 的其它属性组合**（`ling ah` / `ling happy`
             → 能切 to「开心」）—— 这条最关键：真实项目大量使用**中文 tag**，
             而 `expression_choices()` 依赖 symbols 的变量名匹配，中文 tag
             查不到，于是卡片上明明写着"开心"、候选却是空的（2026-09-30 实测）。
          2. `expression_choices()`：英文 tag / 声明式角色的场景。

        并且：**非角色 tag（bg / cg / ui…）不给候选** —— 否则点一下 chip
        就把立绘换成背景了（同一个 bug 的另一半）。
        """
        spec = ''
        try:
            spec = (item['image'].get() or '').strip()
        except Exception:                    # noqa: BLE001
            spec = ''
        parts = spec.split()
        tag = parts[0] if parts else ''
        if not tag:
            return []
        try:
            from storygraph.story.dsl import NON_ACTOR_TAGS
            if tag in NON_ACTOR_TAGS:
                return []
        except Exception:                    # noqa: BLE001
            pass

        symbols = self._symbols()
        # 中文名 → 英文 tag：资源表的 `Tag` 列存的是英文（`ye`），而界面里
        # 写的可能是中文名（`叶怀瑾`）—— 不归一就一个候选都找不到
        resolved = tag
        try:
            alt = symbols.actor_image_tag(tag) if symbols else ''
            if alt:
                resolved = alt
        except Exception:                    # noqa: BLE001
            pass

        words = []
        # 1) 资源表里同 tag 的属性组合（英文属性 → 中文词，复用同一套词表）
        try:
            reg = self.engine.get_registry() if self.engine else None
            for row in (getattr(reg, 'images', {}) or {}).values():
                if str(row.get('Tag') or '') != resolved:
                    continue
                for word in str(row.get('Attributes') or '').split():
                    probe = Beat(seq=1, label='', text='',
                                 show=f'{resolved} {word}')
                    zh = beat_expression_word(probe, symbols) or ''
                    # 只收**词表能翻成中文**的属性：`base_handdown_mask` 这类
                    # 拼装件不是表情，混进候选会让人一脸问号
                    if zh and zh != word and zh not in words:
                        words.append(zh)
        except Exception:                    # noqa: BLE001
            pass

        # 2) 词表/声明带来的候选
        try:
            probe = Beat(seq=1, label='', text='', show=spec)
            for cand in (expression_choices(probe, self._symbols()) or []):
                if cand and cand not in words:
                    words.append(cand)
        except Exception:                    # noqa: BLE001
            pass
        return words

    def _sprite_position_choices(self) -> list:
        from storygraph.story.dsl import DEFAULT_POSITIONS
        try:
            return [p for p in DEFAULT_POSITIONS if p]
        except Exception:                    # noqa: BLE001
            return []

    def _refresh_sprite_candidates(self, item):
        """把候选（表情/位置）交给卡片，chip 才知道能切哪些。"""
        card = item.get('card') if isinstance(item, dict) else None
        if card is None:
            return
        try:
            card.set_candidates(expressions=self._sprite_expression_choices(item),
                                positions=self._sprite_position_choices())
        except Exception:                    # noqa: BLE001
            pass

    def _warn_sprite_repeat(self, item, group):
        """
        同一角色在本拍出现多次：在卡上明说，别让用户以为"界面显示错了"。

        这不是视图 bug，而是**数据里真有两条 show**：Ren'Py 里后一条覆盖
        前一条，观众只看到最后一个表情。工具该做的是如实告知
        （并让用户能一键改/删），而不是偷偷合并。
        """
        card = item.get('card') if isinstance(item, dict) else None
        if card is None:
            return
        if group.get('multi_position'):
            text = f'同角色 {group["count"]} 条且位置不同'
        else:
            text = f'本拍出现 {group["count"]} 次（后者生效）'
        try:
            card.set_warn(text)
        except Exception:                    # noqa: BLE001
            pass

    def _sync_card_spec(self, item):
        """
        把卡片的**名字行 / 缩略图**与「真正会写回去的数据」对齐。

        为什么必须做（2026-09-30 实机复现）：

        写回走 `_collect_sprites`，它用的是 `tag + 归一后的 expression`；
        而卡片名字行与缩略图读的是 `item['image']` —— 那是**载入时冻结的
        整个图像名**。两者一旦脱钩，卡片就开始说谎：

            · 点 chip 换表情 → 数据已经是 `ling happy`，
              卡片却还写着 `ling oh`、缩略图还是 oh.png；
            · 用户看到"名字和脸对不上"，只会认为工具显示错了。

        所以表情一变就把 `image` 重算成**同一个串**，让
        「看到的名字 = 看到的图 = 写出去的数据」恒等。
        """
        if item is None:
            return
        try:
            spec = (item['image'].get() or '').strip()
            expr = (item['expression'].get() or '').strip()
        except Exception:                    # noqa: BLE001
            return
        parts = spec.split()
        tag = parts[0] if parts else ''
        if not tag or not expr:
            return                           # 没名字/没表情：保持原样，不猜
        # 逐词归一（多属性写法不能被压成一个属性，见
        # `normalize_expression_words`），并且**个数对不上就不动**：
        # 宁可这次不改，也不能猜掉一个属性。
        attrs = normalize_expression_words(expr, spec, self._symbols())
        if not attrs:
            return
        orig_attrs = parts[1:]
        if not (len(attrs) == len(orig_attrs) or len(orig_attrs) <= 1):
            return
        new = ' '.join([tag] + attrs)
        if new != spec:
            try:
                item['image'].set(new)
            except Exception:                # noqa: BLE001
                pass

    def _sync_expression_from_spec(self, item):
        """
        换图后，把**新图的属性**同步进 `expression` 变量。

        `_collect_sprites` 写回的是 `tag + 归一后的 expression`，也就是
        **expression 才是"写回哪个属性"的真源**。选择器只改了 `image`，
        如果 expression 还留着旧表情，新选的属性会被反向覆盖 ——
        表现就是"换了图没生效"。
        """
        if item is None:
            return
        try:
            spec = (item['image'].get() or '').strip()
        except Exception:                    # noqa: BLE001
            return
        if not spec:
            return
        probe = Beat(seq=1, label='', text='', show=spec)
        word = beat_expression_word(probe, self._symbols())
        try:
            item['expression'].set(word or '')
        except Exception:                    # noqa: BLE001
            pass

    def _cycle_expression(self, item):
        """
        点卡片上的表情 chip → 切到下一个候选表情（**1 次点击**）。

        这是"表情只在卡内改"的落点：面板级不再有表情输入框，
        改的是**这张卡自己**的 expression，与其它立绘互不影响。
        （旧实现要靠滚动到某一行、手打属性名，还可能填错行。）
        """
        if item is None:
            return
        choices = self._sprite_expression_choices(item)
        if not choices:
            self._toast('这个立绘在资源表里还没声明表情（补 Attributes 后再试）')
            return
        if len(choices) == 1:
            # 只有一个候选时点 chip "没反应"会让人以为坏了 → 直接说明原因
            self._toast(f'这个立绘目前只有「{choices[0]}」一个表情；'
                        f'在资源表里给它补上其它表情（Attributes）就能切换了')
            return
        cur = ''
        try:
            cur = (item['expression'].get() or '').strip()
        except Exception:                    # noqa: BLE001
            cur = ''
        nxt = choices[(choices.index(cur) + 1) % len(choices)] if cur in choices \
            else choices[0]
        try:
            item['expression'].set(nxt)
        except Exception:                    # noqa: BLE001
            pass
        # 名字行 / 缩略图必须跟着表情走，否则卡片显示的还是换之前那张脸
        self._sync_card_spec(item)
        card = item.get('card')
        if card is not None:
            try:
                card.set_candidates(expressions=choices)
                card.refresh_thumb()         # 表情换了，图也要跟着换
            except Exception:                # noqa: BLE001
                pass
        self._toast(f'已切换到「{nxt}」', undo=None)

    def _cycle_position(self, item):
        """点位置 chip → 在「左 / 中 / 右」之间循环（同样是 1 次点击）。"""
        if item is None:
            return
        choices = self._sprite_position_choices()
        if not choices:
            return
        cur = ''
        try:
            cur = (item['position'].get() or '').strip()
        except Exception:                    # noqa: BLE001
            cur = ''
        nxt = choices[(choices.index(cur) + 1) % len(choices)] if cur in choices \
            else choices[0]
        try:
            item['position'].set(nxt)
        except Exception:                    # noqa: BLE001
            pass
        card = item.get('card')
        if card is not None:
            try:
                card.set_candidates(positions=choices)
            except Exception:                # noqa: BLE001
                pass
        self._toast(f'位置：{nxt}')

    def _sprite_thumb_path(self, spec: str) -> str:
        """
        图像名（`ling happy`）→ 磁盘上的图片绝对路径。

        只做解析、**不解码**（解码交给 `ThumbLoader` 在后台线程做）。
        解析复用 `asset_preview_path`，与 Excel 导出那条路同一套基准，
        免得出现"表格里有图、界面里没图"。
        """
        text = str(spec or '').strip()
        if not text:
            return ''
        try:
            reg = self.engine.get_registry() if self.engine else None
        except Exception:                    # noqa: BLE001
            reg = None
        images = getattr(reg, 'images', {}) if reg is not None else {}
        parts = text.split()
        tag = parts[0] if parts else ''
        # 中文名 → 英文 tag（资源表的 Tag 列是英文，show 里可能写中文名）
        try:
            symbols = self._symbols()
            alt = symbols.actor_image_tag(tag) if symbols else ''
            if alt:
                tag = alt
        except Exception:                    # noqa: BLE001
            pass
        # **按角色解析**，不要拿属性名硬撞（真实项目 lan 与 ling 都有 `oh`）
        aid = lookup_image_id(images or {}, tag, parts[1:])
        if not aid:
            return ''
        try:
            # `asset_preview_path` 返回的是 **Path**（可能为 None）——
            # 统一成字符串再给加载器，免得下游对 Path 调字符串方法
            path = asset_preview_path(self.engine, 'image', aid)
            return str(path) if path else ''
        except Exception:                    # noqa: BLE001
            return ''

    # ---------- Stage 列里的立绘：明示 + 迁移 ----------

    def _update_stage_migration_hint(self, stage_rows, show_rows=None):
        """
        Stage 列里有立绘时，在立绘区下方给一条明示 + 一键收敛。

        两种情形分开说（用户 2026-09-30："功能必须职责明晰"）：

          · **只有 Stage 有** → 上面已如实显示，建议迁到立绘区（老写法升级）
          · **两列都有**     → 这是真的职责重叠：生成时 Stage 里的会盖住
            立绘区（同 tag 后者生效），界面上必须说明，并提供一键收敛

        **不自动改用户数据**：动 Stage 列必须由用户点头。但也不能什么都不做
        —— 否则用户会以为"工具把我的立绘弄丢了"或者"改了没反应"。
        """
        hint = getattr(self, '_stage_hint', None)
        if hint is None:
            return
        n = len(stage_rows or [])
        if n <= 0:
            try:
                hint.grid_remove()
            except Exception:                # noqa: BLE001
                pass
            return
        has_show = bool(show_rows)
        try:
            if has_show:
                self._stage_hint_label.config(
                    text=(f'⚠ 立绘写了两处：立绘区 {len(show_rows)} 条、'
                          f'Stage 列 {n} 条。生成时 **Stage 里的会盖住立绘区**'
                          f'（同名字后者生效）。建议只留一处：'))
                self._stage_hint_btn.config(text='保留立绘区，清掉 Stage 里的')
            else:
                self._stage_hint_label.config(
                    text=(f'⚠ 这一拍的 {n} 条立绘写在「Stage」列里（老写法）。'
                          f'上面已如实显示；迁到立绘区后选图/换表情更方便：'))
                self._stage_hint_btn.config(text='迁移到立绘区')
            hint.grid()
        except Exception:                    # noqa: BLE001
            pass

    def _migrate_stage_sprites(self):
        """
        把 Stage 列里的 `[显示]` 立绘迁进结构化立绘区（**用户点按钮才做**）。

        迁移后：立绘只存在于 `show` 列，Stage 只剩转场 / 停顿这类指令 ——
        两列不再同时表达立绘。这就是用户 2026-09-30 要的"功能职责明晰"。
        """
        beat = self._beat
        if beat is None:
            return
        new_show = self._collect_sprites()
        if not new_show:
            self._toast('没有可迁移的立绘')
            return
        beat.show = new_show
        beat.stage = strip_stage_show_lines(getattr(beat, 'stage', '') or '')
        try:
            self._wset('Stage', beat.stage)
        except Exception:                    # noqa: BLE001
            pass
        self.load_beat(beat, getattr(self, '_inherit_hint_text', ''))
        self._toast('已迁移：立绘进立绘区，Stage 只剩非立绘指令')

    # ---------- 提示（Toast） ----------

    def _toast(self, message: str, undo=None, timeout=None):
        """
        底部浮出一条提示，可选「撤销」。

        为什么不弹确认框：删除/切换是高频动作，弹框会打断节奏；
        而"可撤销 + 明确文案"既安全又不打断（文案带具体名字，
        不说"操作成功"——那种提示等于没说）。
        """
        self._hide_toast()
        # 浮层贴在**可视区**上（面板被包进滚动容器后，面板自身高度是
        # 全部内容的高度，贴它 = 贴到可视区外面，提示等于消失）
        host = getattr(self, '_toast_host', None) or self
        if not hasattr(host, 'place'):
            host = self
        try:
            win = tk.Frame(host, background=T.t('toast.bg'))
            tk.Label(win, text=message, bg=T.t('toast.bg'),
                     fg=T.t('toast.fg'), font=T.FONTS['body']
                     ).pack(side='left', padx=(T.SPACE['md'], T.SPACE['sm']),
                            pady=T.SPACE['xs'] + 2)
            if undo is not None:
                def _do_undo():
                    self._hide_toast()
                    try:
                        undo()
                    except Exception:        # noqa: BLE001
                        pass
                tk.Button(win, text='撤销', command=_do_undo, relief='flat',
                          bg=T.t('toast.bg'), fg=T.t('border.strong'),
                          activebackground=T.t('toast.bg'),
                          activeforeground=T.t('toast.fg'),
                          font=T.FONTS['caption'], cursor='hand2'
                          ).pack(side='right', padx=(0, T.SPACE['sm']))
            win.place(relx=0.5, rely=1.0, anchor='s', y=-T.SPACE['sm'])
        except Exception:                    # noqa: BLE001 - 桩环境无 place
            return
        self._toast_win = win
        try:
            self._toast_after = self.after(timeout or T.MOTION['toast'],
                                           self._hide_toast)
        except Exception:                    # noqa: BLE001
            self._toast_after = None

    def _hide_toast(self):
        if getattr(self, '_toast_after', None) is not None:
            try:
                self.after_cancel(self._toast_after)
            except Exception:                # noqa: BLE001
                pass
            self._toast_after = None
        win = getattr(self, '_toast_win', None)
        self._toast_win = None
        if win is not None:
            try:
                win.destroy()
            except Exception:                # noqa: BLE001
                pass

    def _pick_sprite_image(self, img_var):
        """
        给某一行立绘选图。

        两件事（都是用户实测提出来的）：
          · **默认跳到当前那张图**（`current=`）—— 打开就是为了换一张，
            起点该是"我现在是什么"；
          · 填回的是**图像名**（`ling ah`），不是资产 ID（`_ah`）——
            后者写进 show 列是错的（"立绘错位"）。
        """
        cur = ''
        try:
            cur = (img_var.get() or '').strip()
        except Exception:
            cur = ''
        dlg = AssetPickerDialog(self, self.engine, ['image'],
                                title='选择立绘', current=cur)
        if dlg.pick() is None:
            return False                     # 用户取消：不改一个字符
        spec = dlg.picked_image_spec(fallback=cur)
        if hasattr(img_var, 'set'):
            img_var.set(spec)
        return True

    def _focus_condition(self):
        """「＋ 条件」——高级区里的 Kind/Label 旁边给不了条件，这里只提示用法。"""
        self._set_note(
            '条件写在这一行的输入框里（如 `love >= 5`）。\n'
            '生成脚本时这一拍会被包在 `if <条件>:` 里；'
            '留空表示无条件。')

    def _collect_sprites(self) -> str:
        """
        立绘数组 → `show` 字符串（`ling happy@left; mary smile@right`）。

        **按 `order_index`（原始出场顺序）写回，不按显示顺序** ——
        卡片的显示顺序会把"正在说话的那张"提到最前，但出场顺序是
        演出语义（谁先出现），不能因为显示而改掉。

        **复杂子句（behind/as/onlayer）原样保留** —— 它们不出现在数组里，
        重建时要带回去，否则一编辑就丢。
        """
        symbols = self._symbols()
        widgets = list(getattr(self, '_sprite_widgets', []) or [])
        widgets.sort(key=lambda w: w.get('order_index', 0))
        rows = []
        for w in widgets:
            image = (w['image'].get() or '').strip()
            if not image:
                continue
            expr = (w['expression'].get() or '').strip()
            pos = (w['position'].get() or '').strip()
            parts = image.split()
            tag = parts[0]
            orig_attrs = parts[1:]
            # 表情：把中文词**逐词**归一成属性（不写就保留原属性）。
            #
            # 必须逐词归一：`show ling base_arms_akimbo sigh` 是合法的
            # **多属性**写法，而卡片上的表情词本来就是"逐属性拼起来的"
            # （`base_arms_akimbo 无语`）。把整串当成**一个**属性会把它压成
            # `ling base_arms_akimbo 无语` —— `sigh` 被静默吃掉
            # （2026-09-30 真实数据实测：chapter1_3 第 91 拍）。
            #
            # 属性个数对不上就**保持原值不动**：宁可这次不改，
            # 也不能靠猜去丢掉一个属性。
            if expr:
                attrs = normalize_expression_words(expr, image, symbols)
                if len(attrs) == len(orig_attrs) or len(orig_attrs) <= 1:
                    image = ' '.join([tag] + (attrs or orig_attrs))
            # 先放"这个角色的其它条目"，再放生效条目 —— Ren'Py 里后者覆盖
            # 前者，所以生效的必须落在最后，否则写回就等于把演出语义改了
            for extra in w.get('extra_rows') or []:
                rows.append({'image': extra.get('image') or '',
                             'at': extra.get('at') or '',
                             'clause': extra.get('clause') or ''})
            rows.append({'image': image, 'at': normalize_position(pos, symbols),
                         'clause': ''})
        kept = [i for i in _split_show_items(
            getattr(self._beat, 'show', '') or '') if i.get('clause')]
        return _join_show_items(rows + kept)

    # ---------- 符号表 / 审计 ----------

    def _symbols(self):
        reg = None
        try:
            reg = self.engine.get_registry() if self.engine else None
        except Exception:
            reg = None
        return make_symbols(reg)

    # ---------- 交互 ----------

    def set_stage_snapshot(self, text: str):
        """外部（选中某拍时）灌入这一拍的舞台状态文本。"""
        if hasattr(self._stage_var, 'set'):
            self._stage_var.set(text or '')

    def _set_note(self, text: str):
        if hasattr(self._note_var, 'set'):
            self._note_var.set(text or '')

    def _field_asset_type(self, field_name):
        types = self._ASSET_FIELDS.get(field_name) or ()
        return types[0] if types else None

    def _explain(self, field_name, asset_types):
        """点「?说明」：解释当前字段里填的值是什么。"""
        value = self._wget(field_name).strip()
        if not value:
            self._set_note(f'{field_name}：还没填。点 🔍 从资源表里选。')
            return
        texts = []
        for token in re.split(r'[;\s]+', value):
            if not token or token == '@':
                continue
            detail = ''
            for atype in list(asset_types) + ['image', 'transform', 'music',
                                              'sound', 'voice', 'character']:
                detail = asset_detail(self.engine, atype, token)
                if detail:
                    break
            texts.append(f'{token}：{detail}' if detail
                         else f'{token}：（资源表里没有这条，可能是内置或未登记）')
        self._set_note('\n'.join(texts))

    def _pick(self, field_name, asset_types):
        dlg = AssetPickerDialog(self, self.engine, list(asset_types),
                                title=f"选择 {field_name}")
        result = dlg.pick()
        if result is not None:
            _atype, aid = result
            self._wset(field_name, aid)
            detail = asset_detail(self.engine, _atype, aid)
            self._set_note(f'{field_name} = {aid}\n{detail}')
            if field_name == 'Speaker':
                # 换了说话人 → 表情候选跟着换（旧表情可能属于上一个角色）
                self._refresh_expression_hint()

    def pick_expression(self):
        # 2026-09-29：**此方法没有任何调用点**。表情现在在立绘卡内的 chip 上
        # 编辑（见 sprite_card.SpriteCard.on_cycle_expression）。保留它只是
        # 为了不动既有 import 面；**不要再把它接到任何入口上**——它作用于
        # "第一张立绘"，接上去就等于又造了第二个入口。
        """
        从**该立绘声明过的表情**里挑（不靠记词，也不给不存在的表情）。

        候选来自 `expression_choices`（资源表里这个 tag 的 Attributes）；
        一个都没有时退回"手填 + 词表提示"，绝不因为查不到就让人没法写。
        """
        choices = expression_choices(self.collect(), self._symbols())
        if not choices:
            self._set_note(
                '这个立绘在资源表里没有声明过表情属性。\n'
                '可以直接手填（如 开心 / close_happy），或先在资源表里补属性。')
            return
        picked = _ask_choice(self, '选表情', '这一拍用哪个表情？', choices)
        if picked is not None:
            self._wset('Expression', picked)
            self._set_note(f'表情 = {picked}')

    def pick_position(self):
        """从位置词里挑（内置位置 + 项目自定义 transform）。"""
        choices = position_choices(self._symbols())
        picked = _ask_choice(self, '选位置', '这一拍站哪里？', choices)
        if picked is not None:
            self._wset('Position', picked)
            self._set_note(f'位置 = {picked}')

    def _refresh_expression_hint(self):
        choices = expression_choices(self.collect(), self._symbols())
        if choices:
            self._set_note('这个立绘可选的表情：' + '、'.join(choices[:20]))

    def explain_stage(self):
        """把 Stage / BGM / SFX 逐条解释：会被翻译成什么，或缺什么。"""
        from storygraph.story.dsl import compile_stage, compile_audio, Audit, TODO

        symbols = self._symbols()
        audit = Audit(symbols)
        lines = []
        stage = self._wget('Stage').strip()
        if stage:
            lines.append('【舞台】')
            for intent, code, status in compile_stage(stage, symbols, audit):
                mark = '⚠ ' if status == TODO else '  '
                lines.append(f'{mark}{intent}  →  {code}')
        for name, channel, label in (('BGM', 'music', '音乐'),
                                     ('SFX', 'sound', '音效')):
            text = self._wget(name).strip()
            if not text:
                continue
            lines.append(f'【{label}】')
            for intent, code, status in compile_audio(text, channel, symbols,
                                                      audit):
                mark = '⚠ ' if status == TODO else '  '
                lines.append(f'{mark}{intent}  →  {code}')
        self._set_note('\n'.join(lines) or '舞台 / BGM / SFX 都还没填。')

    def preview_compiled(self):
        """把这一拍编译成 Ren'Py 代码看一眼——所见即所得。"""
        beat = self.collect_beat()
        text = render_beats_to_rpy([beat], label_name=beat.label or 'preview',
                                   registry=self._registry())
        win = tk.Toplevel(self)
        win.title('编译预览（这一拍会生成什么）')
        import tkinter.scrolledtext as st
        box = st.ScrolledText(win, width=84, height=22,
                              font=('Consolas', 10))
        box.pack(fill='both', expand=True, padx=8, pady=8)
        box.insert('1.0', text)
        box.configure(state='disabled')
        tk.Button(win, text='关闭', command=win.destroy).pack(pady=(0, 8))

    def show_keywords(self):
        """把 Stage 里能写的词摊出来（角色/位置/表情/转场）。"""
        from storygraph.story.dsl import DEFAULT_EXPRESSIONS, DEFAULT_POSITIONS

        symbols = self._symbols()
        lines = ['【角色】可写显示名或变量名，后面跟表情/位置']
        for var, row in (symbols._by_var or {}).items():
            name = (row.get('Name') or var)
            tag = row.get('ImageTag') or var
            lines.append(f'  {name}（变量 {var}，立绘 {tag}）')
        lines.append('')
        lines.append('【位置】' + '、'.join(DEFAULT_POSITIONS))
        lines.append('【表情】' + '、'.join(
            w for w, m in DEFAULT_EXPRESSIONS.items() if m))
        lines.append('')
        lines.append('写法：[显示] 角色 表情 位置　例：[显示] 凌跃捷 开心 左')
        self._set_note('\n'.join(lines))

    def _registry(self):
        try:
            return self.engine.get_registry() if self.engine else None
        except Exception:
            return None

    # ---------- 载入 / 回写 ----------

    def load_beat(self, beat: Beat, inherit_hint: str = ''):
        self._beat = beat
        self._inherit_hint_text = inherit_hint or ''
        symbols = self._symbols()
        fields = beat_to_fields(beat, symbols)
        for name in self._ORDER:
            self._wset(name, fields.get(name, ''))
        # 记下这一拍载入时 Stage 框的内容：它是结构化列的**投影**，
        # 后面写回时要靠"有没有变过"区分"投影"与"人写的高级指令"。
        self._stage_loaded = (fields.get('Stage') or '').strip()

        # —— 立绘数组 ——
        # 显示的**顺序**把"正在说话的那张"提到最前并高亮，但每行都记着
        # `order_index`（原始出场位置），写回时按它排 —— 出场顺序是演出语义。
        self._clear_sprite_rows()
        show_rows = beat_sprite_rows(beat, symbols)
        stage_rows = stage_sprite_rows(getattr(beat, 'stage', '') or '',
                                       symbols)
        # **立绘写在 Stage 列的老数据也要显示出来**
        # （用户 2026-09-30：已经导入了的立绘没有显示！！！确认台上确实有人）
        src_rows = show_rows or stage_rows
        spk = speaking_sprite_index(beat, src_rows, symbols)
        # **按角色聚合**：同一个 tag 换表情只显示一张卡（见 group_sprites_by_tag）
        groups = group_sprites_by_tag(src_rows)
        order = list(range(len(groups)))
        if 0 <= spk < len(src_rows):
            owner = next((gi for gi, g in enumerate(groups)
                          if spk in g['indices']), None)
            if owner is not None and owner > 0:
                order = [owner] + [i for i in order if i != owner]
        for gi in order:
            g = groups[gi]
            item = self.add_sprite_row(
                g['active'],
                speaking=(0 <= spk < len(src_rows) and spk in g['indices']),
                order_index=g['first_index'],
                extra_rows=g['extras'])
            if item is not None:
                # 告诉卡片这个立绘能切哪些表情/位置（chip 靠它循环）
                self._refresh_sprite_candidates(item)
                if g['count'] > 1 or g['multi_position']:
                    self._warn_sprite_repeat(item, g)
        # 立绘在 Stage 列（或两列都有）时，给一条明示 + 一键收敛（不自动改数据）
        self._update_stage_migration_hint(stage_rows, show_rows)

        # —— 条件 ——
        cond_var = getattr(self, '_condition_var', None)
        if cond_var is not None and hasattr(cond_var, 'set'):
            cond_var.set(getattr(beat, 'condition', '') or '')

        # —— 继承提示（持续状态默认继承，只是显示出来） ——
        inh = getattr(self, '_inherit_var', None)
        if inh is not None and hasattr(inh, 'set'):
            inh.set(inherit_hint or '')

        self._set_note('')
        # 载入时就把舞台列解释一遍——文案最常问的就是"这行会变成什么"
        if any((beat.stage, beat.bgm, beat.sfx)):
            self.explain_stage()
        # 防御性警告：**会变成旁白**的拍必须显形
        # （用户 2026-09-28 报"幽灵旁白"时，卡片上什么都没提示，
        #  只看得出 Speaker 是空的——而空着也能点"下一句"，错误就被带过去了）
        self._warn_if_speaker_missing(beat)

    def _warn_if_speaker_missing(self, beat: Beat):
        """
        有台词、却没有任何说话人 → 导出后就是旁白，在这里说清楚。

        `extend` 例外：它本来就不带说话人（续上一句），渲染成 `extend "…"`。
        旁白（KIND_NARRATION）也例外——它**就是**旁白。
        """
        if not (beat.text or '').strip():
            return
        if beat.speaker:
            return
        if beat.kind in (KIND_NARRATION, KIND_EXTEND):
            return
        self._set_note(
            '⚠ 这一拍有台词但没有说话人 —— 导出后会变成**旁白**。\n'
            f'   kind={beat.kind!r}，请确认这句是谁说的；'
            '若确实是旁白，把「类型」改成 narration。')

    def collect_beat(self) -> Beat:
        """
        表单 → Beat。

        原则：**从当前 Beat 出发，只覆盖表单里真正可编辑的字段**。

        可编辑范围 = 主区（Speaker / Text / Scene / BGM / Voice / Comment，
        永远可见）+ 展开时的高级区（SFX / CodeBlock / Label / Kind / Notes）。
        高级区**收起时一个字都不碰** —— 那正是"改一句台词把这一拍的
        转场/代码弄没了"的来源。

        唯一的例外是 `Stage`：它是结构化列的**投影**，不当作输入
        （除非用户真的手改了那一格，见下面的注释）。
        """
        b = self._beat or Beat()
        b.speaker = self._wget('Speaker').strip()
        b.text = self._wget('Text').strip()

        # —— 章节（label）——
        # 顶部那个"章节（label）"框是**始终可见**的真源（Label 字段本身在
        # 折叠的高级区里，用户反馈"没地方改这一章的 label"）。
        # 它填的就是这一拍所属的 label，所以写回它以它为准。
        cvar = getattr(self, '_chapter_var', None)
        if cvar is not None and hasattr(cvar, 'get'):
            new_label = (cvar.get() or '').strip()
            if new_label:
                b.label = new_label

        # —— 条件 ——
        cond_var = getattr(self, '_condition_var', None)
        if cond_var is not None and hasattr(cond_var, 'get'):
            b.condition = (cond_var.get() or '').strip()

        # —— 立绘数组 → show ——
        # 每张立绘的表情/位置在各自那一行里编辑（见 _collect_sprites），
        # 而且**行内已经做过归一**（中文词 → 属性），所以这里不再调用
        # apply_expression/apply_position —— 否则会二次改写第一张。
        #
        # **数组为空 ≠ 清空立绘**：只有"表单里真的没有立绘行"时才不动
        # `b.show`。否则 `show=...` 会被静默清成空
        # （实测：`_collect_sprites()` 在没有行时返回 ''，直接把立绘抹掉）。
        # 要删立绘请用「✕」删掉那一行，再配合「退场」列。
        if getattr(self, '_sprite_widgets', None):
            new_show = self._collect_sprites()
            if new_show:
                b.show = new_show
                rows = beat_sprite_rows(b, self._symbols())
                b.at = uniform_sprite_position(rows)

        # —— Stage：**投影，不是输入**（除用户手改） ——
        # `Stage` 承载的是"结构化列表达不了的部分"（见 compose_stage_extra），
        # 而 `beat_to_fields` 又把结构化列投影进这一格显示。把投影原样写回
        # 再 hydrate，就是**自己吃自己**：实测 `hide cg03` 会静默变成
        # `cg03; cg03`（逐句校对必然踩中，305 拍里 11 拍被改写）。
        #
        # 唯一例外：用户真的在 Stage 框里改了字（与载入时不同）——这时以
        # 人写的为准，并且**先清掉结构化舞台字段再 hydrate**，否则
        # "旧投影 + 新文本"会叠加成两份指令。
        typed_stage = self._wget('Stage').strip()
        loaded_stage = getattr(self, '_stage_loaded', '') or ''
        hand_written = bool(typed_stage) and typed_stage != loaded_stage
        if hand_written:
            b.stage = typed_stage
            for attr in ('scene', 'scene_at', 'show', 'hide', 'at',
                         'with_', 'transform', 'stage_order'):
                setattr(b, attr, '')
        else:
            # 没手改：**重新投影**（结构化列是唯一真源，绝不能把那份投影
            # 原样留着 —— 立绘一改，旧投影就会和新的结构化列各输出一次）。
            # 但要把"结构化列表达不了的"那部分原样留下（`[停顿] 1.0` 之类），
            # 否则打开卡片点一下下一句，作者手写的东西就没了。
            symbols = self._symbols()
            parts = [t for t in (compose_stage_extra(b, symbols),
                                 stage_only_ops(loaded_stage, symbols))
                     if t]
            b.stage = '; '.join(parts)

        if self._adv_open:
            # 展开时才让高级区接管（这时它的控件是可编辑的）
            b.sfx = self._wget('SFX').strip()
            b.code = self._wget('CodeBlock').strip()
            # raw 已并入 CodeBlock（唯一的保真后门），不单独维护
            b.raw = ''
            b.label = self._wget('Label').strip() or b.label
            b.kind = self._wget('Kind').strip() or b.kind
            b.notes = self._wget('Notes').strip()

        # —— 主区四项：**始终可编辑，必须始终写回** ——
        # 用户 2026-09-30 把 Scene / BGM / Voice / Comment 从折叠区提了上来，
        # 但它们一直跟高级区共用一个 `_adv_open` 闸门：收起时改了**静默丢失**
        # （实测：改完按「下一句」，Scene/BGM/Voice/Comment 一个都没写回）。
        b.scene = self._wget('Scene').strip()
        b.bgm = self._wget('BGM').strip()
        b.voice = self._wget('Voice').strip()
        b.comment = self._wget('Comment').strip()

        # 立刻把 DSL 解释成结构化字段：舞台快照/校验才看得见这一拍的内容。
        # 非手写分支的 `b.stage` 是**投影**（已经在结构化字段里了），
        # 标记成"已吸过"，免得 hydrate 再吸一遍。
        try:
            if not hand_written:
                b._hydrated_stage = b.stage
            hydrate_from_dsl([b], self._symbols())
        except Exception:
            pass
        if hand_written:
            # 手写 Stage 会重写结构化舞台字段：立绘卡等界面必须以**新数据**
            # 重新渲染一次，否则界面还停在被覆盖掉的那一份上。
            try:
                self.load_beat(b)
            except Exception:                # noqa: BLE001
                pass
        return b

    def collect(self) -> dict:
        """表单 → 字段字典（`beat_to_fields` 的逆，供选择器等只读用途）。"""
        return beat_to_fields(self.collect_beat(), self._symbols())



# ===================== 参与角色（说话人参考表） =====================

def participants(engine, limit: int = 8) -> list:
    """
    取"本项目的常驻角色"，按台词量排序。

    用途（2026-09-30 改）：**只作为参考表** —— 显示这一章有哪些人、
    谁正在说话，双击某人 = 把他填进当前这一拍的说话人。

    它曾经驱动一个"回车自动轮流说话"的轮转，但那条路会在翻页时
    **静默改写下一拍的说话人**（用户标记为最严重的问题），已删除。
    便利性用"显式双击"保留，自动化一律取消：**改哪一拍，由用户指哪一拍**。
    """
    if engine is None:
        return []
    try:
        registry = engine.get_registry()
    except Exception:
        return []
    if registry is None:
        return []
    chars = getattr(registry, 'characters', {}) or {}
    ranked = sorted(
        chars.items(),
        key=lambda kv: (-(kv[1] or {}).get('_dialogue_count', 0) or 0,
                        str(kv[0])))
    return [cid for cid, _row in ranked[:limit]]


# ===================== 编辑器主窗口 =====================

class ScriptTableEditorWindow(tk.Toplevel):
    """
    载入 .rpy → 列出 Beat → 编辑 → 导出 .rpy。

    三处针对实操痛点的设计（来自对同类工具的调研）：
      · 参与角色：一表看清这一章有谁、**谁正在说话**，双击即填进当前拍。
      · 舞台快照：选中一拍直接看到"这时台上是谁、在哪、放什么音乐"，
        不用在脑子里做叠加运算。
      · 体检入口：随时跑三级校验，问题清单里点一条就跳到那一拍。
    """

    def __init__(self, parent, engine, script_path=None, asset_dir=None,
                 on_new_asset=None):
        super().__init__(parent)
        self.engine = engine
        self.asset_dir = asset_dir
        self.on_new_asset = on_new_asset
        self.beats = []
        self.rotation = []
        #: 面板里**当前显示的是哪一拍**（唯一真源，见 _show_index）
        self._loaded_idx = -1
        self.title("剧本表编辑器")
        self._build()
        if script_path:
            self.load_script(script_path)

    def _build(self):
        # ============ 顶部：这一章是哪个文件 + 换一章 ============
        # 以前窗口打开是**空的**，「载入 .rpy」还在底部——用户根本不知道
        # 该干什么（反馈："我不知道如何打开你说的卡片入口"）。
        # 现在顶部第一行就说清楚"正在编哪个文件"，旁边就是换文件按钮。
        top = tk.Frame(self)
        top.pack(side='top', fill='x', padx=T.SPACE['sm'],
                 pady=(T.SPACE['xs'], 0))
        self._file_var = tk.StringVar(value='（还没打开章节）')
        tk.Label(top, textvariable=self._file_var,
                 font=T.FONTS['bodyStrong']).pack(side='left')
        tk.Button(top, text='换一章…', command=self._on_load).pack(side='right')

        # ---- 翻页：**放在顶部** ----
        # 逐句校对时它是最高频的动作，而底部那条常被面板内容顶出视野
        # （用户 2026-09-29："翻页处于最下沿，经常被挡住，一点也不方便"）。
        self._nav_label = tk.Label(top, text='第 0 / 0 句',
                                   font=T.FONTS['caption'],
                                   fg=T.t('text.secondary'))
        self._nav_label.pack(side='right', padx=(0, T.SPACE['md']))
        self._nav_next = tk.Button(top, text='下一句 ▶（Enter / ↓）',
                                   command=self.next_beat)
        self._nav_next.pack(side='right')
        self._nav_prev = tk.Button(top, text='◀ 上一句（Alt+↑ / ↑）',
                                   command=self.prev_beat)
        self._nav_prev.pack(side='right', padx=(0, T.SPACE['sm']))
        tk.Label(top,
                 text='说话人 / 台词在左侧；立绘的表情与位置点卡片上的标签切换',
                 font=T.FONTS['caption'],
                 fg=T.t('text.tertiary')).pack(side='right',
                                               padx=(0, T.SPACE['md']))

        # ============ 章节（label）栏 ============
        # 用户 2026-09-28："你也没给我地方改这一章的 label 啊？？？"
        # `Label` 字段其实一直在，但它属于"高级区"（默认折叠），
        # 而"整章切到另一个 label"更是个结构性操作 —— 两件事都该在这里。
        nav = tk.Frame(self)
        nav.pack(side='top', fill='x', padx=8, pady=(4, 0))
        tk.Label(nav, text='章节（label）：',
                 font=('微软雅黑', 9)).pack(side='left')
        self._chapter_var = tk.StringVar(value='')
        self._chapter_entry = tk.Entry(nav, textvariable=self._chapter_var,
                                       width=18, font=('Consolas', 9))
        self._chapter_entry.pack(side='left')
        tk.Button(nav, text='▸ 跳到这一章开头',
                  command=self.goto_chapter).pack(side='left', padx=(6, 0))
        tk.Button(nav, text='整章改名…',
                  command=self.rename_chapter).pack(side='left', padx=(6, 0))
        self._chapter_list = tk.Listbox(nav, height=1, width=24,
                                        exportselection=False)
        self._chapter_list.pack(side='left', padx=(6, 0))
        if hasattr(self._chapter_list, 'bind'):
            self._chapter_list.bind('<<ListboxSelect>>',
                                    lambda e: self._on_chapter_pick())
        tk.Label(nav, text='（改本拍所属章节直接改左框；整章改名用右边按钮）',
                 font=('微软雅黑', 8), fg='#888').pack(side='left', padx=(6, 0))

        left = tk.Frame(self)
        left.pack(side='left', fill='y')
        tk.Label(left, text="演出单元（Beat）").pack(anchor='w')
        self._list = tk.Listbox(left, width=40, height=25)
        self._list.pack(fill='y')
        self._list.bind('<<ListboxSelect>>', self._on_select)

        # 参与角色：**参考表**，不再参与"回车自动轮流说话"（那条路会
        # 静默改写下一拍的说话人）。双击某人 = 显式填进**当前这一拍**。
        self._rotation_title = tk.Label(left, text="参与角色")
        self._rotation_title.pack(anchor='w', pady=(6, 0))
        self._rotation_list = tk.Listbox(left, width=40, height=5,
                                         exportselection=False)
        self._rotation_list.pack(fill='x')
        if hasattr(self._rotation_list, 'bind'):
            self._rotation_list.bind(
                '<Double-Button-1>',
                lambda e: self.fill_speaker_from_participant())
        self._refresh_participants()

        right = tk.Frame(self)
        right.pack(side='right', fill='both', expand=True, padx=8)
        # 面板内容比一屏长（真实数据实测：请求高度 923px > 可视 813px），
        # 不做滚动的话底部被**直接裁掉且没有任何提示**——用户 2026-09-30：
        # "那个卡片模块右侧栏太长了！！！起码给我个滚动条，不然我根本就
        #  看不完全"。所以这里给整个右侧面板套一层滚动容器。
        self._panel_scroller = ScrollFrame(right, background=T.t('bg.surface'))
        self._panel_scroller.pack(fill='both', expand=True)
        self._panel = BeatEditorPanel(self._panel_scroller.body, self.engine,
                                      on_new_asset=self.on_new_asset)
        self._panel.pack(fill='both', expand=True)
        self._panel_scroller.attach_wheel(self)
        # Toast 必须贴**可视区**底部：面板身处滚动内容里，自身高度等于全部
        # 内容高度，用面板自己 place(rely=1.0) 会落到可视区下方——
        # 表现就是"操作了却看不到任何提示"。
        self._panel._toast_host = self._panel_scroller.viewport

        bar = tk.Frame(self)
        bar.pack(side='bottom', fill='x')
        # **「保存这一拍」**：用户 2026-09-30
        # > 你就没有做将一张卡片修改后的保存按钮！！！我做完了也无法保存到该卡片上！
        # 以前只能靠"翻页/导出"顺带提交当前拍，改完停在原地就没有落点。
        self._save_btn = tk.Button(
            bar, text='💾 保存这一拍（Ctrl+S）', command=self.save_current_beat,
            bg='#2E7D32', fg='white')
        self._save_btn.pack(side='left', padx=(0, 6))
        tk.Button(bar, text="载入 .rpy",
                  command=self._on_load).pack(side='left')
        tk.Button(bar, text="导出 .rpy",
                  command=self._on_export).pack(side='left')
        # 翻页已上移到顶部条（见 _build 开头）；这里只留低频动作
        tk.Button(bar, text="项目体检",
                  command=self.show_health).pack(side='left')
        # 舞台演出预览：让这一章按卡片效果"跑起来"（用户明确诉求）
        tk.Button(bar, text='🎬 演出预览（这一章跑起来）',
                  command=self.open_stage_preview,
                  bg='#1976D2', fg='white').pack(side='left', padx=(6, 0))

        if hasattr(self, 'bind'):
            # **回车 = 下一拍**（纯导航，不写别的拍）。
            # 用户 2026-09-30 明确："回车是下一章卡片的意思"。
            self.bind('<Control-s>', lambda e: self.save_current_beat())
            self.bind('<Return>', lambda e: self.next_beat())
            # 键盘优先：逐句校对时不必去够按钮
            self.bind('<Alt-Down>', lambda e: self.next_beat())
            self.bind('<Alt-Up>', lambda e: self.prev_beat())
            # 方向键也能翻页（用户要求"按住下箭头也是下一张卡片"）。
            # 但不能抢掉"在多行台词里移动光标"——见 `_on_arrow_key`。
            self.bind('<Down>', lambda e: self._on_arrow_key(True))
            self.bind('<Up>', lambda e: self._on_arrow_key(False))
        self._update_nav_state()

    def _on_arrow_key(self, down: bool):
        """
        方向键 → 翻页，**但只在不会跟"移动光标"打架的时候**。

        为什么不能无脑绑：`↓` 在多行「台词」框里是移动光标（一屏读不完的
        长句要靠它改错字），在左列表里是自己移动选择（那也是换一拍）。
        规则：
          · 焦点在 Listbox → **完全放行**（它自己动选择，已经是换拍，
            我们再翻一次就会一次跳两拍）
          · 焦点在多行 Text → 光标**已经在最后一行**（↓）/第一行（↑）
            才翻页，否则让光标正常移动
          · 其它控件（Entry / 按钮 / 卡片）→ 直接翻页
        """
        try:
            widget = self.focus_get()
        except Exception:                    # noqa: BLE001 - 桩环境
            widget = None
        cls = ''
        try:
            cls = widget.winfo_class() if widget is not None else ''
        except Exception:                    # noqa: BLE001
            cls = ''
        if cls == 'Listbox':
            return None                      # 放行：列表自己换拍
        if cls == 'Text' and not self._caret_at_edge(widget, down):
            return None                      # 放行：光标还没到底/到顶
        if down:
            self.next_beat()
        else:
            self.prev_beat()
        return 'break'

    @staticmethod
    def _caret_at_edge(widget, down: bool) -> bool:
        """
        多行 Text 的光标是否已在**最后一行**（down）/ **第一行**（up）。

        用**逻辑行**而不是显示行：`wrap='word'` 的长台词会被折成好几屏行，
        而用户要的是"按住下箭头翻卡片"—— 那种情况按下箭头应当翻页，
        不该在折行之间挪光标（挪光标用 `←`/`→` 或直接点更顺手）。
        只有用户自己敲过回车（多行台词）才需要在行间移动光标。

        （别用 `Text.count(..., 'displaylines')`：它在首行会返回 `None`，
        取下标就抛异常，于是"永远放行"—— 实测踩过。）
        """
        try:
            cur_line = int(str(widget.index('insert')).split('.')[0])
            last_line = int(str(widget.index('end-1c')).split('.')[0])
        except Exception:                    # noqa: BLE001 - 桩环境
            return True
        return cur_line >= last_line if down else cur_line <= 1

    # ---------- 章节（label）导航 ----------

    def chapters(self) -> list:
        """本文档里出现过的 label（按首次出现顺序，不排序——保持阅读顺序）。"""
        out = []
        for b in getattr(self, 'beats', []) or []:
            name = (getattr(b, 'label', '') or '').strip()
            if name and name not in out:
                out.append(name)
        return out

    def _chapter_anchor(self, beat) -> str:
        """
        这一拍**原本属于**哪一章。

        为什么要单独记：`Beat.label` 是**逐拍**可改的（策划可能只把某几拍
        挪到别处），而"整章改名"要按**原始归属**找齐所有拍。
        用 `line_id` 的 `label.序号` 前缀做锚（`LineId` 是位置稳定主键），
        这样逐拍改 label 之后仍然知道它原来是谁。
        """
        lid = (getattr(beat, 'line_id', '') or '').strip()
        if '.' in lid:
            return lid.rsplit('.', 1)[0]
        return (getattr(beat, 'label', '') or '').strip()

    def _refresh_chapters(self):
        """把 label 列表填进顶部选择器。"""
        names = self.chapters()
        lst = getattr(self, '_chapter_list', None)
        if lst is not None and hasattr(lst, 'delete'):
            lst.delete(0, 'end')
            if hasattr(lst, 'insert'):
                for n in names:
                    lst.insert('end', n)
            if hasattr(lst, 'config'):
                try:
                    lst.config(height=min(len(names), 6) or 1)
                except Exception:
                    pass

    def _on_chapter_pick(self):
        """从列表里点一个章节 → 把输入框填上（不跳转，跳转要点按钮）。"""
        lst = getattr(self, '_chapter_list', None)
        names = self.chapters()
        if lst is None or not hasattr(lst, 'curselection'):
            return
        sel = lst.curselection()
        if sel and sel[0] < len(names):
            var = getattr(self, '_chapter_var', None)
            if var is not None and hasattr(var, 'set'):
                var.set(names[sel[0]])

    def goto_chapter(self):
        """跳到输入框里那个 label 的**第一拍**。"""
        name = ''
        var = getattr(self, '_chapter_var', None)
        if var is not None and hasattr(var, 'get'):
            name = (var.get() or '').strip()
        if not name:
            self._set_note('先在「章节（label）」里填一个 label，再点跳转。')
            return
        for i, b in enumerate(getattr(self, 'beats', []) or []):
            if self._chapter_anchor(b) == name or \
                    (getattr(b, 'label', '') or '').strip() == name:
                self._show_index(i)
                return
        self._set_note(f'找不到 label「{name}」。')

    def rename_chapter(self):
        """
        **整章改名**：把记忆里属于该章的所有拍改成新 label。

        为什么单独做一个动作：`Label` 字段是**逐拍**的，而策划想的是
        "这一章改叫 chapter1_4b"——那是整段操作，逐拍改 200 行不现实。

        按 `LineId` 前缀锚定原始归属（见 `_chapter_anchor`），
        所以之前逐拍挪过的那些拍不会被误改。
        """
        names = self.chapters()
        if not names:
            self._set_note('还没载入章节。')
            return
        old = ''
        var = getattr(self, '_chapter_var', None)
        if var is not None and hasattr(var, 'get'):
            old = (var.get() or '').strip()
        if old not in names:
            old = names[0]
        new = simpledialog.askstring(
            '整章改名', f'把 label「{old}」改成：', initialvalue=old)
        if not new:
            return
        new = new.strip()
        if not new or new == old:
            return
        if new in names:
            messagebox.showerror('改名失败', f'已经有 label「{new}」了。')
            return
        self.apply_current_beat()          # 先保存当前编辑
        n = 0
        for b in getattr(self, 'beats', []) or []:
            if self._chapter_anchor(b) == old:
                b.label = new
                n += 1
        # LineId 的 label 前缀要跟着改，否则协作主键还指向旧章节
        _assign_identity(self.beats)
        for b in self.beats:
            lid = (getattr(b, 'line_id', '') or '')
            if lid.startswith(old + '.'):
                b.line_id = new + lid[len(old):]
        self._rebuild_list()
        self._refresh_chapters()
        if var is not None and hasattr(var, 'set'):
            var.set(new)
        self._set_note(f'已把 {n} 拍从「{old}」改名为「{new}」。')

    def _rebuild_list(self):
        """按 self.beats 重建整个列表（改名/重排后用）。"""
        lst = getattr(self, '_list', None)
        if lst is None or not (hasattr(lst, 'delete') and hasattr(lst, 'insert')):
            return
        lst.delete(0, 'end')
        for i, b in enumerate(self.beats):
            text = b.text or b.kind or '(空)'
            lst.insert('end', f"#{i + 1} [{b.label}] {text}")
        if getattr(self, '_loaded_idx', -1) >= 0:
            if hasattr(lst, 'selection_set'):
                lst.selection_set(self._loaded_idx)

    def _refresh_participants(self):
        self.rotation = participants(self.engine)
        if hasattr(self._rotation_list, 'delete'):
            self._rotation_list.delete(0, 'end')
        if hasattr(self._rotation_list, 'insert'):
            for cid in self.rotation:
                self._rotation_list.insert('end', cid)

    # ---------- 选择与轮转 ----------
    #
    # **唯一真源 = self._loaded_idx（面板里现在显示的是哪一拍）。**
    #
    # 为什么不直接用列表的 curselection：用户 2026-09-28 截图暴露
    # "列表高亮第 N 行、面板显示第 N-1 拍"。一旦两者错开，
    # `apply_current_beat` 会把**面板里那一拍的内容写进列表选中的另一拍**
    # ——这不是显示问题，是**改错数据**。
    # 所以：载入一律走 `_show_index`，回写一律用 `_loaded_idx`。

    def _highlight_speaker(self, speaker: str = ''):
        """
        在「参与角色」列表里标出**正在说话的人**。

        用户 2026-09-30（提了很多次）：
        > 参与角色这里，正在说话的人应当（高亮）

        注意：**高亮 ≠ 选中**。选中是"用户点了谁"（轮转用它），
        高亮是"这一拍是谁在说"。两者混在一起会让列表的选中随翻页乱跳，
        所以这里只改行的底色与字色，不碰 selection。
        """
        lst = getattr(self, '_rotation_list', None)
        target = str(speaker or '').strip()
        if lst is not None:
            try:
                items = list(lst.get(0, 'end'))
            except Exception:                # noqa: BLE001
                items = []
            for i, name in enumerate(items):
                hit = bool(target) and str(name).strip() == target
                try:
                    lst.itemconfig(
                        i,
                        background=T.t('brand.subtle') if hit else '',
                        foreground=T.t('brand.primary') if hit else '',
                        selectbackground=T.t('listbox.selectBg'),
                        selectforeground=T.t('listbox.selectFg'))
                except Exception:            # noqa: BLE001
                    pass
                if hit:
                    try:
                        lst.see(i)
                    except Exception:        # noqa: BLE001
                        pass
        # 标题上也写出来：**颜色之外再给一处文字**（不靠颜色单编码）
        title = getattr(self, '_rotation_title', None)
        if title is not None:
            try:
                title.config(text='参与角色（双击＝填进这一拍）　'
                                  f'正在说话：{target or "—"}')
            except Exception:                # noqa: BLE001
                pass

    def _selected_index(self):
        """列表当前高亮的行（只用于响应用户点击）。"""
        if not hasattr(self._list, 'curselection'):
            return -1
        sel = self._list.curselection()
        return sel[0] if sel else -1

    def _show_index(self, idx: int):
        """
        把第 idx 拍载入面板（**唯一的载入入口**），并让列表高亮跟着走。

        顺序很关键：先记 `_loaded_idx`，再 sync 列表，最后 load 面板——
        这样即使 `selection_set` 触发了 `<<ListboxSelect>>` 事件，
        重入时 idx 已经是同一个，不会加载到别的拍。
        """
        if not (0 <= idx < len(self.beats)):
            return
        self._loaded_idx = idx
        if hasattr(self._list, 'selection_clear'):
            self._list.selection_clear(0, 'end')
            self._list.selection_set(idx)
            if hasattr(self._list, 'see'):
                self._list.see(idx)
        # 章节框跟着当前拍走（用户要一眼看到"这一拍属于哪一章"）
        cvar = getattr(self, '_chapter_var', None)
        if cvar is not None and hasattr(cvar, 'set'):
            cvar.set((getattr(self.beats[idx], 'label', '') or '').strip())
        self._panel.load_beat(self.beats[idx],
                              inherited_state_hint(self.beats, idx))
        self._panel.set_stage_snapshot(stage_digest(self.beats, idx))
        self._update_nav_state()
        # 参与角色列表里高亮**正在说话的人**（用户反复提过的那条）
        self._highlight_speaker(getattr(self.beats[idx], 'speaker', ''))

    def _on_select(self, event=None):
        """用户点了列表某一行 → 载入那一拍。"""
        self._show_index(self._selected_index())

    def _update_nav_state(self):
        """
        刷新翻页按钮的可用态与计数。

        首句禁用「上一句」、末句禁用「下一句」——**禁用而不是隐藏**：
        位置不跳动，用户还看得见"到头了"，不会以为"点了没反应"。
        """
        total = len(getattr(self, 'beats', []) or [])
        idx = int(getattr(self, '_loaded_idx', 0) or 0)
        label = getattr(self, '_nav_label', None)
        if label is not None:
            try:
                label.config(text=f'第 {(idx + 1) if total else 0} / {total} 句')
            except Exception:                # noqa: BLE001
                pass
        for btn, enabled, why in (
                (getattr(self, '_nav_prev', None), idx > 0, '已经是第一句'),
                (getattr(self, '_nav_next', None),
                 total > 0 and idx < total - 1, '已经是最后一句')):
            if btn is None:
                continue
            try:
                btn.config(state='normal' if enabled else 'disabled')
            except Exception:                # noqa: BLE001
                pass
            # 禁用时**说明原因**：不然用户只看到灰按钮，以为"点了没反应"
            try:
                tip = getattr(btn, '_sg_tip', None)
                if tip is None:
                    tip = Tooltip(btn)
                    btn._sg_tip = tip
                tip.set_text('' if enabled else why)
            except Exception:                # noqa: BLE001
                pass

    def save_current_beat(self):
        """
        **把这一张卡片上的改动保存到这一拍**（用户明确要的按钮）。

        之前只有"翻页"和"导出"会顺带提交当前拍 —— 改完停在原地就没有
        落点，用户会以为"改了什么都保存不上"。

        说清楚边界（免得误以为已经写进文件）：
          · 这一步是**提交到内存里的这一拍**，可以随时再改；
          · **落盘**发生在「导出 .rpy」时（导出会再过一遍安全门禁）。
        """
        idx = getattr(self, '_loaded_idx', -1)
        if not (0 <= idx < len(getattr(self, 'beats', []))):
            self._set_note('还没有载入章节，没什么可保存。')
            return None
        beat = self.apply_current_beat()
        line_id = getattr(beat, 'line_id', '') or f'第 {idx + 1} 拍'
        panel = getattr(self, '_panel', None)
        if panel is not None:
            try:                             # 状态栏：说清"存到哪、什么时候落盘"
                panel._set_note(
                    f'✔ 已保存到这一拍（{line_id}）。'
                    '\n改动在内存里，点「导出 .rpy」时才写进文件。')
            except Exception:                # noqa: BLE001
                pass
        try:                                 # 屏幕上给一下反馈，不用去读状态栏
            panel._toast(f'已保存到这一拍（{line_id}）')
        except Exception:                    # noqa: BLE001
            pass
        return beat

    def next_beat(self):
        """
        保存当前拍 → 前进到下一拍。

        **翻页是纯导航**：只提交"当前这一拍"的编辑，**绝不碰别的拍**
        （用户 2026-09-30 拍板：
        > 回车不要自动填说话人！！！回车是下一章卡片的意思！！！

        在此之前这里还会调用一段"参与角色轮转"，把说话人**静默写进下一拍**——
        那是"说话人根本就不对"的根因。要把某个人填进某一拍，请用
        「参与角色」里**双击**（显式动作，只改当前拍）。

        回写用 `_loaded_idx` 而不是列表高亮：面板里显示哪一拍，
        改动就属于哪一拍。以前两者错开时会把内容写错行。
        """
        idx = self._loaded_idx
        if not (0 <= idx < len(self.beats)):
            return
        self.apply_current_beat()
        nxt = idx + 1
        if nxt >= len(self.beats):
            return
        self._show_index(nxt)

    def fill_speaker_from_participant(self, cid: str = ''):
        """
        把「参与角色」里选中（或双击）的人填进**当前这一拍**的说话人。

        为什么保留这个入口：轮转是"写双人对话时不用反复打角色名"的便利，
        价值真实存在；问题只出在它**自动化**。现在改成显式动作——
        用户点了谁就填谁，改的是**眼前这一拍**（看得见、可改回），
        绝不顺手去改别的拍。
        """
        cid = str(cid or '').strip()
        if not cid:
            lst = getattr(self, '_rotation_list', None)
            try:
                items = list(lst.get(0, 'end'))
                sel = list(lst.curselection())
                cid = items[sel[0]] if sel and sel[0] < len(items) else ''
            except Exception:                # noqa: BLE001
                cid = ''
        if not cid:
            self._set_note('先在「参与角色」里点一个名字，再双击（或点「填进说话人」）。')
            return
        idx = self._loaded_idx
        if not (0 <= idx < len(self.beats)):
            return
        self.beats[idx].speaker = cid
        # 面板上的说话人框要跟着变（不然数据变了、界面还是旧值）
        try:
            self._panel._wset('Speaker', cid)
        except Exception:                    # noqa: BLE001
            pass
        self._highlight_speaker(cid)
        self._set_note(f'已把「{cid}」填进这一拍的说话人（第 {idx + 1} 拍）。')

    def prev_beat(self):
        """保存当前拍 → 后退一拍（与「下一句」对称，回车流程也常用）。"""
        idx = self._loaded_idx
        if not (0 <= idx < len(self.beats)):
            return
        self.apply_current_beat()
        if idx - 1 >= 0:
            self._show_index(idx - 1)

    # ---------- 载入 / 导出 / 体检 ----------

    def load_script(self, script_path):
        script_path = Path(script_path)
        nodes = RenPyParser().parse(script_path)
        result = ParseResultBuilder().build(nodes)
        self.beats = beats_from_parse_result(result)
        # 顶栏显示"在编哪个文件"——用户要随时知道自己在改哪一章
        var = getattr(self, '_file_var', None)
        if var is not None and hasattr(var, 'set'):
            var.set(f'正在编辑：{script_path.name}（{len(self.beats)} 拍）')
        # 合成中文 DSL：能无损反推就显示 `[显示] 凌跃捷 开心 左`，
        # 反推不了就保留原生写法（绝不猜错一个表情）
        symbols = make_symbols(self._registry())
        for b in self.beats:
            # 用 `compose_stage_extra`（**只放结构化列表达不了的部分**）
            # 而不是 `compose_stage`（整拍投影）：
            # `Stage` 是**投影**，把它整份塞回 `beat.stage`，渲染时就会被
            # 结构化列和 Stage 各输出一次（实测 chapter1_3：
            # `scene bg bg5_0 at bgfit` 后面多出一行 `scene bg bg5_0`，
            # 立绘位置/背景变换被静默改掉）。界面想给策划看全文，
            # 由 `beat_to_fields` 的 `或 compose_stage(...)` 负责，不影响数据。
            b.stage = compose_stage_extra(b, symbols)
        if hasattr(self._list, 'delete'):
            self._list.delete(0, 'end')
        if hasattr(self._list, 'insert'):
            for i, b in enumerate(self.beats):
                label = b.text or b.kind or '(空)'
                self._list.insert('end', f"#{i + 1} [{b.label}] {label}")
        self._refresh_participants()
        self._refresh_chapters()
        self.health = None
        # 载入后**直接选中第一拍并显示卡片**，不要让人对着空面板发呆。
        # 走 `_show_index`（唯一载入入口），它会同时管列表高亮与面板。
        if self.beats:
            self._show_index(0)

    def _on_load(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title="选择 .rpy",
            filetypes=[("Ren'Py 脚本", "*.rpy"), ("所有文件", "*.*")])
        if p:
            self.load_script(p)

    def _on_export(self):
        """
        编译导出：先生成脚本，再写「编译结果表」。

        找不到资源**不会**中断导出——只在脚本里留一行
        `# TODO: 缺少资源 …`，然后把数量报给策划。这是刻意的：
        一个 2600 张图的项目，没人会去看 1073 条红字；
        但 12 处"待补资源"他会去改。
        """
        from tkinter import filedialog
        from storygraph.pipeline.compile import compile_beats, write_export_sheet

        self.apply_current_beat()
        out = filedialog.asksaveasfilename(
            title="导出 .rpy",
            defaultextension=".rpy",
            filetypes=[("Ren'Py 脚本", "*.rpy")])
        if not out:
            return
        registry = self._registry()
        result = compile_beats(self.beats, registry=registry)

        # 生成安全门禁：与「从剧本表生成 .rpy」同一条闸。
        # 这里也要过——否则应用内编辑这条路会绕过保护，
        # 把中文图像 tag / 重名 label 的脚本写进项目。
        occupied = occupied_labels(
            project_root=getattr(self.engine, 'project_root', None),
            exclude={Path(out)})
        issues = generation_guard(
            result.rpy_text, Path(out),
            project_root=getattr(self.engine, 'project_root', None),
            occupied_labels=occupied)
        blocking = [i for i in issues if i.is_blocking]
        if blocking:
            messagebox.showerror(
                '拒绝导出：生成的脚本会让游戏跑不起来',
                '发现 %d 条致命问题，已取消写入：\n\n%s'
                % (len(blocking),
                   '\n'.join('· ' + i.format() for i in blocking[:12])))
            return

        Path(out).write_text(result.rpy_text, encoding='utf-8')
        sheet = None
        try:
            sheet = write_export_sheet(out, result)
        except Exception:
            sheet = None

        msg = [f'已写入：{out}', '', result.summary()]
        if sheet:
            msg += ['', f'编译结果表：{sheet}']
        todos = [r for r in result.rows if r.get('status') == 'todo']
        if todos:
            msg += ['', '缺少资源（脚本里找不到的引用；新生成/改动的行会写成 # TODO 注释，源文件里原有的行按原样保留）：']
            for r in todos[:8]:
                msg.append(f"  · {r['code']}")
            if len(todos) > 8:
                msg.append(f'  …还有 {len(todos) - 8} 处')
        messagebox.showinfo("编译导出完成", '\n'.join(msg))

    def _registry(self):
        if self.engine is None:
            return None
        try:
            return self.engine.get_registry()
        except Exception:
            return None

    # ---------- 舞台演出预览 ----------

    def open_stage_preview(self):
        """
        打开「舞台演出预览」：把这一章按卡片效果逐帧播出来。

        用户 2026-09-28 的核心诉求。**先保存当前编辑**再建帧——
        否则预览的是"改之前"的状态，那比没有预览更误导人。
        """
        self.apply_current_beat()
        if not getattr(self, 'beats', None):
            messagebox.showinfo('没有内容', '先载入一章。')
            return
        try:
            from storygraph.ui.stage_preview import (build_frames,
                                                     StagePreviewWindow)
        except Exception as e:                       # noqa: BLE001
            messagebox.showerror('无法打开预览', str(e))
            return
        frames = build_frames(self.beats, registry=self._registry())
        if not frames:
            messagebox.showinfo('没有内容', '这一章没有可播的拍。')
            return
        win = StagePreviewWindow(
            self, frames,
            title=f'舞台演出预览 · {len(frames)} 拍',
            registry=self._registry(),
            project_root=getattr(self.engine, 'project_root', None))
        # 预览窗停在**当前这一拍**，接着看下去
        if getattr(self, '_loaded_idx', -1) >= 0:
            win.show_frame(self._loaded_idx)
        return win

    def show_health(self):
        """
        项目体检：三级校验 + 报告窗口。点一条问题跳到对应拍。

        调研里给这类视图的定位很准：把结构性问题集中一处，别让程序在
        跑游戏时一个个撞见。这里复用 build.collect_report 的同一套规则。
        """
        self.apply_current_beat()
        registry = self._registry()
        report = validate_all(self.beats, registry=registry)
        text = report.format(max_per_level=60)

        dlg = tk.Toplevel(self)
        dlg.title(f"项目体检 · {report.summary()}")
        box = tk.Listbox(dlg, width=110, height=24)
        box.pack(fill='both', expand=True, padx=8, pady=8)
        self._health_issues = list(report.issues)
        index_of = {}
        for i, issue in enumerate(report.issues):
            index_of[len(index_of)] = issue
            mark = '✗' if issue.severity == 'error' else '!'
            box.insert('end', f"{mark} {issue.level} {issue.code} "
                              f"[{issue.where}] {issue.message}")
        self._health_index = index_of

        def jump(_event=None):
            if not hasattr(box, 'curselection'):
                return
            sel = box.curselection()
            if not sel:
                return
            issue = index_of.get(sel[0])
            if issue is None:
                return
            for i, b in enumerate(self.beats):
                if getattr(b, 'line_id', '') == issue.where:
                    if hasattr(self._list, 'selection_clear'):
                        self._list.selection_clear(0, 'end')
                        self._list.selection_set(i)
                        self._list.see(i)
                    self._on_select()
                    break

        if hasattr(box, 'bind'):
            box.bind('<<ListboxSelect>>', jump)
        tk.Label(dlg, text=text.split('\n')[0], fg='#666').pack()
        return None

    def apply_current_beat(self):
        """
        把面板当前编辑写回 `self.beats`。

        **写回 `_loaded_idx`（面板显示的那一拍），不是列表高亮的那一行**——
        两者错开时按列表写会把内容写错行。
        """
        idx = self._loaded_idx
        if 0 <= idx < len(self.beats):
            self.beats[idx] = self._panel.collect_beat()
            _assign_identity([self.beats[idx]])
            # 列表里显示的是台词，改完要跟着更新，否则"列表和卡片不一致"
            self._refresh_list_row(idx)

    def _refresh_list_row(self, idx: int):
        """刷新列表某一行的文字（台词改了要立刻反映出来）。"""
        if not (0 <= idx < len(self.beats)):
            return
        if not (hasattr(self._list, 'delete') and hasattr(self._list, 'insert')):
            return
        b = self.beats[idx]
        text = b.text or b.kind or '(空)'
        self._list.delete(idx)
        self._list.insert(idx, f"#{idx + 1} [{b.label}] {text}")
