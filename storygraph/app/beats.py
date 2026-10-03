"""
storygraph.app.beats
====================
**剧本卡片的编辑语义层**（框架无关 / 无 UI 依赖）。

为什么单独一个模块（原 `ui/beat_editor.py` 里混着 3000 行 tkinter）：

  · 这些函数是**纯逻辑**（输入 Beat/registry → 输出 dict/list/str），
    一行 tkinter 都不需要，却因为住在 `ui/` 里，导致任何别的前端想用
    它们都被迫 import tkinter —— 依赖方向反了（后端 → 前端）；
  · 它们是编辑器的**真正难点**（表情/位置是 `show` 字符串的投影、
    多属性立绘不能被压平、位置只有统一才整拍…），属于"深模块"：
    接口是一行函数，实现里是全部业务不变量；
  · Qt 前端与 tkinter 前端从此共用同一份，**不可能各写一套**。

契约（勿破）：
  · 本模块**禁止** import 任何 UI 框架，也禁止 import `storygraph.ui_qt*`；
  · 编辑语义**只有这一份**：2026-10-01 tkinter 前端（`storygraph/ui/`）
    已整个删除，前端只剩 `ui_qt`，它也只做"摆控件 / 读表单 / 写表单"。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from pathlib import Path
from typing import Optional

from storygraph.assets.asset_completion import AssetCompletion
from storygraph.assets.asset_registry import describe_asset
from storygraph.story.beat_model import (
    Beat,
    KIND_MENU,
    MENU_NO_CAPTION,
    join_show_items,
    split_show_items,
    compose_audio_text,
    compose_code,
    compose_stage,
    menu_caption_text,
)

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

    **菜单拍的标题**（`Text` 字段）走 `menu_caption_text`：无标题的 `menu:`
    在 Beat 上用占位符承载"这里有个菜单"（见 `MENU_NO_CAPTION`），
    那一位是**内部标记**，不该出现在表单里让策划看到 `\x00menu`。
    写回去由 `fields_to_beat` 重新补上占位符。
    """
    return {
        'Label': beat.label,
        'Kind': beat.kind,
        'Speaker': beat.speaker,
        'Text': menu_caption_text(beat.text),
        # 条件显示（`if <条件>:`）—— 数据层一直有 `Beat.condition`，
        # 但当年**从没暴露给策划**（既不在表里也不在卡片里）。
        # 卡片上做成「＋ 条件」按钮，取字段就是这里。
        'condition': beat.condition,
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

    **键不存在 = 不动这一格**（2026-10-05 修）：以前一律
    `(fields.get(k) or '').strip()`，于是**局部表单会静默清空其它列** ——
    实测 `apply(0, {'Speaker': 'e', 'Text': '只改台词'})` 之后
    `beat.scene` 从 `'bg classroom'` 变成了 `''`（背景被抹掉）。
    `Expression` / `Position` 早就用"键存在才改"防住了这一类，
    这里把同一判据推广到全部列：

      · 前端交回**完整表单**（`fields()` 的产物）→ 行为完全不变；
      · 前端/调用方只交回改过的几格 → 其余列保持原值，不再被误清空；
      · 想**主动清空**某一格 → 显式传空串（`{'Scene': ''}`），照旧生效。

    区分"没给我"与"给我空串"是关键 —— 这是"收起高级区 ≠ 清空"
    那条已知规则的一般化。
    """
    b = base or Beat()
    if 'Label' in fields:
        b.label = (fields.get('Label') or '').strip()
    elif base is None:
        b.label = ''
    if 'Kind' in fields:
        b.kind = (fields.get('Kind') or 'say').strip() or 'say'
    if 'Speaker' in fields:
        b.speaker = (fields.get('Speaker') or '').strip()
    if 'Text' in fields:
        b.text = (fields.get('Text') or '').strip()
    # 菜单拍的标题留空 = "无标题菜单"，不是"没有菜单"：
    # 占位符必须补回来，否则 `menu:` 那一段会在渲染时整块消失
    # （见 `MENU_NO_CAPTION`）。表单里显示的是空串（见 `beat_to_fields`）。
    if b.kind == KIND_MENU and not b.text:
        b.text = MENU_NO_CAPTION
    if 'Scene' in fields:
        b.scene = (fields.get('Scene') or '').strip()
    if 'Stage' in fields:
        b.stage = (fields.get('Stage') or '').strip()
    if 'BGM' in fields:
        b.bgm = (fields.get('BGM') or '').strip()
    if 'SFX' in fields:
        b.sfx = (fields.get('SFX') or '').strip()
    if 'Voice' in fields:
        b.voice = (fields.get('Voice') or '').strip()
    if 'CodeBlock' in fields:
        b.code = (fields.get('CodeBlock') or '').strip()
        # raw 已经并入 CodeBlock（唯一的"保真后门"），这里不再单独维护
        b.raw = ''
    if 'Comment' in fields:
        b.comment = (fields.get('Comment') or '').strip()
    # 「备注」列已经取消（并入注释，见 Beat.from_row 的老表迁移）。
    # 这里再兜一次：旧 Beat / 旧表带来的 notes 绝不能静默丢掉。
    if 'Notes' in fields:
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
    for item in split_show_items(getattr(beat, 'show', '') or ''):
        if item.get('clause') or not (item.get('image') or '').strip():
            continue
        return item
    return {}


def _show_items(beat: Beat) -> list:
    return [i for i in split_show_items(getattr(beat, 'show', '') or '')
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
    for item in split_show_items(getattr(beat, 'show', '') or ''):
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
                swallow('beats.beat_sprite_rows')
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


def cards_to_show(cards: list, symbols=None) -> str:
    """
    **立绘卡数组 → `show` 文本**（`ling happy@left; mary smile@right`）。

    这是"卡片 → 数据"的唯一出口，两套前端共用。语义（曾经被改坏过，
    每一条都有真实事故背景）：

      · 按 `order_index`（**原始出场顺序**）写回，不按屏幕显示顺序 ——
        显示顺序会随说话人高亮变，用它会把立绘顺序改乱；
      · `extra_rows`（同一角色的其余条目）**原样带回，一个不丢** ——
        换表情时保留的多行立绘不能凭空消失；
      · 表情词**逐词**归一（`normalize_expression_words`）—— 多属性立绘
        （`ling base_arms_akimbo sigh`）不许被压成一个属性；
      · 位置写成 `图@位置`（`at`），位置是**逐条**的。

    整拍位置另有讲究，见 `uniform_sprite_position`。
    """
    ordered = sorted(cards or [], key=lambda it: it.get('order_index', 0))
    parts = []
    for it in ordered:
        image = str(it.get('image') or '').strip()
        if not image:
            continue
        for extra in it.get('extra_rows') or []:
            # **逐条位置必须跟着一起带回**（用户 2026-10-05 实测的"学姐第一次
            # 出场位置丢了"）：一拍里同一角色先 `show ye happy at i11` 再换表情
            # `show ye care` 时，前一条落在 `extra_rows` 里 —— 原先只带回
            # `image`，于是 `at i11` 被吃掉，产物变成 `show ye happy`。
            #
            # 触发点很隐蔽：**翻到下一句 / 保存 / 导出 / 增删卡**都会先把当前拍
            # 提交一遍（`apply_current_beat`），而提交就是"卡片 → show"这条路，
            # 所以只要在那一拍上待过，位置就悄悄没了。
            ex_image = str(extra.get('image') or '').strip()
            if not ex_image:
                continue
            ex_at = normalize_position(
                str(extra.get('position') or '').strip()
                or str(extra.get('at') or '').strip(), symbols)
            parts.append(f'{ex_image}@{ex_at}' if ex_at else ex_image)
        expr = normalize_expression_words(str(it.get('expression') or ''),
                                          image, symbols)
        spec = image
        if expr:
            tag = image.split()[0]
            spec = ' '.join([tag] + expr)
        # 位置也要**归一**（中文词 → transform 名）。
        #
        # 2026-10-01 修：位置 chip 的候选是中文人话（`左` / `右边`…），
        # 而这里原先直接把它拼进 `show` —— 于是中文漏进脚本：
        # `show eileen sad@左`。tkinter 版的 `_collect_sprites` 当年
        # 做过这一步归一，搬 Qt 时丢了（`normalize_position` 一直在，
        # 只是**没人调**）。
        # 认不出来的自定义 transform（`i11` / `靠左站`）原样保留 ——
        # 宁可不猜，也不能把作者写的变换名改坏。
        at = normalize_position(
            str(it.get('position') or '').strip()
            or str(it.get('at') or '').strip(), symbols)
        if at:
            spec += f'@{at}'
        parts.append(spec)
    return '; '.join(p for p in parts if p)


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
                swallow('beats.stage_sprite_rows')
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
    kept = [i for i in split_show_items(show or '') if i.get('clause')]
    out = []
    for row in rows or ():
        image = (row.get('image') or '').strip()
        if not image:
            continue
        at = (row.get('at') or '').strip()
        out.append({'clause': '', 'image': image, 'at': at})
    return join_show_items(out + kept)


def speaker_missing_warning(beat: Beat) -> str:
    """
    有台词、却没有任何说话人 → 导出后就是旁白，这里说清楚。

    `extend` 例外：它本来就不带说话人（续上一句），渲染成 `extend "…"`。
    旁白（`narration`）也例外——它**就是**旁白。

    为什么要有这条（用户 2026-09-28 报"幽灵旁白"）：当时卡片上什么都没提示，
    只看得出「说话人」是空的——而空着也能点「下一句」，错误就被带过去了。
    宁可吵一点，也不要让"会变成旁白"的拍静默通过。

    返回 '' 表示不需要提示（调用方据此清掉提示条）。
    这里**只算文案**，怎么显示由前端决定（`app` 层不认识控件）。
    """
    if not (beat.text or '').strip():
        return ''
    if beat.speaker:
        return ''
    if beat.kind in ('narration', 'extend'):
        return ''
    return ('⚠ 这一拍有台词但没有说话人 —— 导出后会变成**旁白**。\n'
            f'   kind={beat.kind!r}，请确认这句是谁说的；'
            '若确实是旁白，把「类型」改成 narration。')


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
        except Exception as e:
            swallow('beats.speaking_sprite_index', e)
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


def participants(engine, limit: int = 8) -> list:
    """
    取"本项目的常驻角色"（左栏「参与角色」列表）。

    用途（2026-09-30 定的）：**只作为参考** —— 提示"这一章里有哪些人"。
    谁在说话由双击某个名字 = 填进当前拍的说话人。

    为什么不做成"回车自动填说话人"：那样翻页时会把**上一拍的说话人**
    默默写进这一拍（用户报过这个 bug）。所以这里是"显式双击"，
    且只是填进当前拍，**不翻页、不改别的拍**。

    排序：按对话条数从多到少（`_dialogue_count`），让主角排前面 ——
    策划最常点的就是主角，不该每次都去列表里找。条数相同按 ID 排（稳定）。
    """
    if engine is None:
        return []
    try:
        registry = engine.get_registry()
    except Exception:                        # noqa: BLE001
        return []
    if registry is None:
        return []
    chars = getattr(registry, 'characters', {}) or {}
    ranked = sorted(
        chars.items(),
        key=lambda kv: (-(kv[1] or {}).get('_dialogue_count', 0), str(kv[0])))
    return [cid for cid, _row in ranked[:limit]]


def choices_for_image(image_spec: str, symbols) -> dict:
    """
    一张立绘的**可选表情/位置**（供卡片 chip 点一下换一个）。

    为什么需要它：`expression_choices(beat, symbols)` 要的是一个 Beat
    （它从 `beat.show` 里取出这张图的 tag）。而卡片上只有 `image`
    （`ling base_arms_akimbo sigh`）这一个字符串 —— 早期 Qt 版直接
    把 `symbols` 当 beat 传了进去，异常被 `except` 吞掉，
    于是**点 chip 永远没反应**（静默失效，最难查的那种）。
    这里统一成"给一个 image spec，还你两个候选列表"。

    返回 {'expression': [...], 'position': [...]}；拿不到就是空列表。
    """
    image_spec = str(image_spec or '').strip()
    if not image_spec or symbols is None:
        return {'expression': [], 'position': []}
    probe = Beat()
    probe.show = image_spec
    return {
        'expression': expression_choices(probe, symbols),
        'position': position_choices(symbols),
    }


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
    items = split_show_items(show or '')
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
    return join_show_items(out)


def apply_position(show: str, word: str) -> str:
    """
    把位置写进 `show` 的**每一个普通立绘**项（`图@位置`）。

    为什么是"每一个"而不是第一个：位置是舞台级的观感，同一拍里两张立绘
    站不同位置时，策划改的是"这句台词时谁在左谁在右"——这里给的是
    统一位置，单张要精细控制在「高级」区改 `图@位置`。
    """
    items = split_show_items(show or '')
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
    return join_show_items(out)
