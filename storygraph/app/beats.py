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

import re

from storygraph.core.safeguard import swallow
from pathlib import Path
from typing import Optional

from storygraph.assets.asset_completion import AssetCompletion
from storygraph.assets.asset_registry import describe_asset
from storygraph.story.beat_model import (
    Beat,
    KIND_MENU,
    KIND_MENU_OPTION,
    KIND_NARRATION,
    KIND_SAY,
    MENU_NO_CAPTION,
    join_show_items,
    split_show_items,
    compose_audio_text,
    compose_code,
    compose_stage,
    menu_caption_text,
)

# ===================== 纯逻辑（可单测） =====================

def kind_from_speaker(kind: str, speaker: str) -> str:
    """
    填了说话人 → 类型就是**台词**（`say`）。

    为什么必须有这一条（用户 2026-10-05 实测报）：

    > 明明卡牌编辑器内我已经编写了说话人，kind 仍然显示 narrator

    解析器**本来就是**这么定的（`beat_build`：`kind = say if speaker else
    narration`），但卡片上只改「说话人」那一格时没人重算它 —— 于是高级区
    的「类型」还写着 `narration`（左栏那一行也还挂着 ☁），用户合理地以为
    "我填的说话人没生效"。导出的台词其实是对的（渲染看 `speaker`），
    但**看得见的字段在说谎**，而"看得见的和实际的不一致"比报错更贵。

    ⚠ **刻意是单向的：不自动往下降级**（`say` + 空说话人 ≠ 自动变 narration）：

      · 那张卡片此时会显示 **[有台词但没有说话人 —— 导出后会变成旁白]**
        的提示条（`speaker_missing_warning`），而那条提示的判据正是
        `kind not in ('narration', 'extend')` —— 一降级，提示就被自己吞掉，
        用户再也看不到"这句会变旁白"；
      · 表里没有 `Kind` 列时默认 `say` 是既有的迁移契约
        （`tests/test_beat_editor.py::test_missing_kind_defaults_to_say`）。

    单一真源放在这里：`fields_to_beat`（表单那条路）与
    `TableSessionMixin.apply`（会话那条路）都调它，不各写一份。
    """
    k = str(kind or '').strip() or KIND_SAY
    if k == KIND_NARRATION and str(speaker or '').strip():
        return KIND_SAY
    return k


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
    except Exception as e:
        swallow('app.beats.asset_row', e)
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

    资产表的 Path 基准**五花八门**（`sr_general/game/images/x.png` /
    `game/images/x.png` / `images/x.png`），所以路径解析必须走
    `story.excel_export.resolve_thumb_source` —— 那是唯一真源
    （Excel 导出与界面预览用同一套规则）。

    ⚠ 2026-10-05 用户实测报"图片加载不完全/缺口大"，查出两条真因，这是第二条：

      这条从前是 `Path(project_root) / rel` —— **只试一个基准**。真实项目里
      有 74 条（`gui/dutui/**/RETURE.png`）的 Path 写作
      `sr_general/game/gui/...`（多了一层项目目录名）→ 拼出来必然不存在 →
      `None` → 选择器里那 74 格**永远只有文字、永远不出图**，
      而且**没有任何提示**（看起来就像"还在加载"）。
      换成 `resolve_thumb_source` 之后它们全都找得到。
    """
    if asset_type != 'image':
        return None
    row = asset_row(engine, asset_type, asset_id)
    if not row:
        return None
    rel = (row.get('Path') or '').strip()
    if not rel:
        return None
    root = getattr(engine, 'project_root', None)
    p = Path(rel)
    if p.is_absolute():
        try:
            return p if p.exists() else None
        except OSError:
            return None
    try:
        from storygraph.story.excel_export import resolve_thumb_source
        real = resolve_thumb_source(rel, root)
        if real is not None:
            return real
    except Exception as e:                # noqa: BLE001 - 退回老的单基准
        swallow('app.beats.asset_preview_path', e)
    if root:
        p = Path(root) / rel
    try:
        return p if p.exists() else None
    except OSError:
        return None


#: "这是**身体件**、不是整张立绘"的属性名特征。
#:
#: 用户 2026-10-05 实测报（附截图）：
#:
#> 存在一个"无头骑士"的 bug（不是程序问题）……查真实项目是资源组件分离导致的
#> ……你尝试修复它
#:
#: 真实项目把立绘拆成 **layeredimage 组件**（`show ye base_handdown happy`）：
#: `base_handdown.png` 只有身体、没有头（实测头部区不透明像素 10935 vs
#: 完整立绘 `happy.png` 的 37152）。旧解析按**第一个属性**查表，于是
#: `base_handdown` 赢了、画出来就是个无头身体。
_BODY_PART_RE = re.compile(
    r'^(?:base|body|pose)\b|_mask$|_mask_|^(?:base|body|pose)_', re.I)


def _is_body_part(name: str) -> bool:
    """这个属性看起来是"身体/姿态件"（要和表情件叠起来才是完整立绘）吗？"""
    return bool(_BODY_PART_RE.search(str(name or '').strip()))


def preferred_image_attrs(rest) -> list:
    """
    立绘属性 → **查询优先顺序**（修"无头骑士"，2026-10-05）。

    `show ye base_handdown happy` 里 `base_handdown` 是**身体件**
    （实测 `ye/base_handdown.png` 头部区不透明像素 10935，而完整立绘
    `ye/happy.png` 是 37152 —— 画出来就是用户报的"无头骑士"），
    `happy` 才是**表情/整图**。

    所以顺序：**先"不是身体件"的，从后往前**（表情通常写在最后），
    再轮到身体件。调用方按这个顺序逐个查，第一个查得到的就是完整立绘。
    """
    rest = [str(r).strip() for r in (rest or []) if str(r).strip()]
    return ([a for a in reversed(rest) if not _is_body_part(a)]
            + [a for a in reversed(rest) if _is_body_part(a)])


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
        # 台词后缀（2026-10-18）：`(what_color="#f00")` / `with` / `id` /
        # `nointeract` —— 台词那一行的样式子句，空 = 普通台词。
        'SayTail': getattr(beat, 'say_tail', '') or '',
        # 菜单头（2026-10-19）：`menu 名(args):` 的两段。空 = 裸 `menu:`。
        # 只在菜单拍上有意义，其它拍恒为空串（表单照样收，写回是 no-op）。
        #
        # 为什么必须进表单：这两样从前让整块菜单掉进「原样块」—— 卡片上
        # 整段消失，策划连选项都看不到（审计 P1-3 收尾）。
        'MenuName': getattr(beat, 'menu_name', '') or '',
        'MenuArgs': getattr(beat, 'menu_args', '') or '',
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
    # **类型跟着说话人变**（见 `kind_from_speaker`）：只填说话人不改类型，
    # 那一格就会一直写着 `narration`（用户实测报过）。
    b.kind = kind_from_speaker(b.kind, b.speaker)
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
        # ⚠ 与 `EditorSession.apply` 同一条守卫（2026-10-18）：这一格是
        #   `raw` + `code` 拼出来的显示值，**没改就别写回** —— 写回会把
        #   "哪些行是原生语句"的分类（`ordered`）冲掉，渲染顺序随之改写。
        _new = (fields.get('CodeBlock') or '').strip()
        if _new != str(compose_code(b) or '').strip():
            b.code = _new
            # raw 已经并入 CodeBlock（唯一的"保真后门"），这里不再单独维护
            b.raw = ''
    if 'Comment' in fields:
        b.comment = (fields.get('Comment') or '').strip()
    # 「备注」列已经取消（并入注释，见 Beat.from_row 的老表迁移）。
    # 这里再兜一次：旧 Beat / 旧表带来的 notes 绝不能静默丢掉。
    if 'Notes' in fields:
        b.notes = (fields.get('Notes') or '').strip()
    # 台词后缀（2026-10-18）：`(what_color="#f00")` / `with` / `id` ——
    # 与 `EditorSession.apply` 同一条键名（高级区里那一格）。
    if 'SayTail' in fields:
        b.say_tail = (fields.get('SayTail') or '').strip()
    # 菜单头（2026-10-19）：`menu 名(args):` 的两段。
    # 键存在才改（与其它列同一判据）—— 空串 = 主动清成裸 `menu:`。
    if 'MenuName' in fields:
        b.menu_name = (fields.get('MenuName') or '').strip()
    if 'MenuArgs' in fields:
        b.menu_args = (fields.get('MenuArgs') or '').strip()
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
    except Exception as e:                        # noqa: BLE001
        swallow('app.beats.stage_sprite_rows', e)
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


def beat_group_conflicts(beat: Beat, facts) -> list:
    """
    **规则 N3**：这一拍的立绘里，**同一组被同时选中了两个属性** → 冲突清单。

    `[{'tag','group','attrs'}, …]`，没有冲突就是 `[]`。

    为什么必须由工具自己查（而不是等引擎报）：实测 `show lan happy sad`
    （两个属性同属一组）时 **lint 退出码 0、引擎一声不吭** ——
    这类冲突**不在 lint 的覆盖范围内**，只能靠引擎给的 group 自己算。
    而 group 就在引擎事实里，所以这条检查的实现成本几乎为零。
    """
    from storygraph.core import engine_facts as EF
    if not facts:
        return []
    out = []
    try:
        rows = beat_sprite_rows(beat)
    except Exception as e:                          # noqa: BLE001
        swallow('app.beats.beat_group_conflicts.rows', e)
        return []
    for row in rows:
        spec = str(row.get('image') or '').strip()
        toks = spec.split()
        if len(toks) < 2:
            continue                                # 只有 tag、没属性 → 不可能同组
        tag, attrs = toks[0], toks[1:]
        for hit in EF.group_conflicts(facts, tag, attrs):
            out.append({'tag': tag, 'group': hit['group'], 'attrs': hit['attrs']})
    return out


def _show_item_word(token: str) -> str:
    """`close_happy@i11` → `close_happy`（`@` 后面是位置变换，**不算属性**）。"""
    return str(token or '').split('@', 1)[0]


def _show_item_attr(token: str) -> str:
    """
    show 里的一个词 → **纯属性名**（给"引擎认不认"用）。

    比 `_show_item_word` 多剥一层：**末尾的 `:`**。
    真机实测（2026-10-11）：`show ling heihei:` **跟一个 ATL 块**
    （`zoom 0.15` / `yoffset 200` 那些缩进行），冒号是**块开始**，
    不是属性的一部分 —— 不剥就会报出 `heihei:` 这种不存在的属性名，
    还会把同一个属性拆成 `heihei` / `heihei:` 两条（汇总里当场看出来）。
    """
    return _show_item_word(token).rstrip(':').strip()


def beat_unknown_attrs(beat: Beat, facts) -> list:
    """
    这一拍的立绘里，**引擎没认**的属性 → `[{'tag','attr'}, …]`（保序去重）。

    ⚠ **只报告，绝不阻止**（用户 2026-10-11 定的口径）：
    候选与导入**一律以实际存在的资源为准** —— 资源表里有的、引擎不认的
    （掩码 / 切图这类内部件）**照样能选、照样能导入**；
    这条只用来提示"选了它，引擎会报 `'lan lan_angry' is not an image'`"。
    拿引擎的枚举去**删候选**就会把"合法的、只是引擎还没认的"一起删掉 ——
    那正是"无法导入"的来源。
    """
    from storygraph.core import engine_facts as EF
    if not facts:
        return []
    try:
        rows = beat_sprite_rows(beat)
    except Exception as e:                          # noqa: BLE001
        swallow('app.beats.beat_unknown_attrs.rows', e)
        return []
    out, seen = [], set()
    for row in rows:
        toks = [w for w in (_show_item_attr(t)
                            for t in str(row.get('image') or '').split()) if w]
        if len(toks) < 2:
            continue                            # 只有 tag、没属性 → 无从谈起
        tag, attrs = toks[0], toks[1:]
        for attr in EF.unrecognized_attrs(facts, tag, attrs):
            if (tag, attr) not in seen:
                seen.add((tag, attr))
                out.append({'tag': tag, 'attr': attr})
    return out


def unknown_attrs_by_tag(beats, facts,
                         extra_pairs=()) -> dict:
    """
    **项目级汇总**：`{tag: [引擎未认的属性…]}`（纯函数）。

    为什么要有它：`beat_unknown_attrs` 只回答"**这一拍**哪里不对"（卡片那条逐拍提示），
    而"报出来"（路线图 §8.9 待做项）要的是**一个项目到底有多少处会 lint 报错、
    集中在哪几个角色身上** —— 那才是能交给程序去看的清单。

    `extra_pairs` 是"不在这一轮 beats 里、但也要算进去"的 `(tag, [属性…])`
    （例如调用方手上现成的一批），由调用方给；这里不猜、不去读盘。

    ⚠ 口径与 `beat_unknown_attrs` **完全一致**（同一条 `EF.unrecognized_attrs`）：
    **只汇总、不过滤** —— 这些属性仍然可选、仍然能导入。
    """
    from storygraph.core import engine_facts as EF
    if not facts:
        return {}
    pairs = []
    for beat in (beats or ()):
        try:
            rows = beat_sprite_rows(beat)
        except Exception as e:                          # noqa: BLE001
            swallow('app.beats.unknown_attrs_by_tag.rows', e)
            continue
        per_tag: dict = {}
        for row in rows:
            toks = [w for w in (_show_item_attr(t)
                                for t in str(row.get('image') or '').split()) if w]
            if len(toks) < 2:
                continue                            # 只有 tag、没属性 → 无从谈起
            per_tag.setdefault(toks[0], [])
            for attr in toks[1:]:
                if attr not in per_tag[toks[0]]:
                    per_tag[toks[0]].append(attr)
        for tag, attrs in per_tag.items():
            unknown = EF.unrecognized_attrs(facts, tag, attrs)
            if unknown:
                pairs.append((tag, unknown))
    pairs.extend(list(extra_pairs or ()))
    return EF.unrecognized_by_tag(pairs)


def silent_default_attrs(beat: Beat, layer_model=None, project_root=None,
                         only_groups=None) -> list:
    """
    这一拍的立绘里，**写了却被引擎忽略**的属性 → `[{'tag','group','attr',
    'used'}, …]`（保序去重）。

    为什么必须有这条提示（2026-10-12 实测，正是用户问的"为什么不报"的那一半）：

        `show ye base_handdown gratified angry`
          → `gratified` / `angry` **同属 `expressions` 组**，叠层语义取
            **声明靠前的那个**，另一个**静默丢掉** ——
          → 而实测 lint 对这类冲突**一声不吭**（`exit 0`），所以只能工具自己算。

    与 `beat_group_conflicts`（N3）的分工：那条说"你同组选了两个"，
    这条说清"**具体哪个被顶掉了**"；两者都在卡片那条提示里出现。

    ⚠ **只报真的被丢的**：一个词**单独**出现时不可能被顶掉（那正是它被采用）；
    真正要看的是"同组写了 ≥2 个，其中哪些没进 `attrs_chosen`"。与
    `beat_unknown_attrs` 同一条口径：**只报告，绝不阻止**，也绝不改用户写的东西。

    ⚠⚠ **`only_groups`**（2026-10-12 实测的省法）：没给就直接返回 `[]` ——
    "同组写了两个"这件事 `beat_group_conflicts` 几微秒就能算出来（纯属事实查表），
    而"谁被顶掉"要读项目里的 `layeredimage`（**首次 0.28 s**，见
    `layered_model.load_layer_model_cached`）。卡片每翻一页都调我这里，
    所以**先问 N3、有冲突才算** —— 没有同组冲突的拍，一次磁盘都不摸。

    ⚠ **认不出就返回 `[]`**：没给 `layer_model`、也没给 `project_root`（拿不到
    项目里的 `layeredimage`）时**不猜** —— 宁可不说，也不说错。
    """
    if not only_groups:
        return []
    try:
        from storygraph.story.layered_model import (load_layer_model_cached,
                                                    resolve_layers)
    except Exception as e:                          # noqa: BLE001
        swallow('app.beats.silent_default_attrs.import', e)
        return []
    model = layer_model
    if model is None:
        if not project_root:
            return []
        try:
            model = load_layer_model_cached(project_root)
        except Exception as e:                      # noqa: BLE001
            swallow('app.beats.silent_default_attrs.model', e)
            return []
    if model is None or not (model.images or {}):
        return []
    out, seen = [], set()
    try:
        rows = beat_sprite_rows(beat)
    except Exception as e:                          # noqa: BLE001
        swallow('app.beats.silent_default_attrs.rows', e)
        return []
    for row in rows:
        toks = [w for w in (_show_item_attr(t)
                            for t in str(row.get('image') or '').split()) if w]
        if len(toks) < 2:
            continue
        tag, attrs = toks[0], toks[1:]
        img = (model.images or {}).get(tag)
        if img is None:
            continue
        try:
            plan = resolve_layers(model, tag, attrs)
        except Exception as e:                      # noqa: BLE001
            swallow('app.beats.silent_default_attrs.resolve', e)
            continue
        # ⚠ 用 `attrs_chosen`（**真正采纳**的那些），不是 `attrs_used`（输入回显）
        # —— 同一组写了两个词时两者才分得开，见 `LayerPlan.attrs_chosen` 的注释。
        used = set(plan.attrs_chosen or ())
        for group in img.groups:
            if str(group.name or '') not in only_groups:
                continue                    # 这一组没有"同组写了两个" → 免查
            declared = [a for a in attrs if a in group.attrs]
            if len(declared) < 2:
                continue        # 0 个：本来就没写；1 个：不可能被顶掉
            for attr in declared:
                if attr in used:
                    continue
                key = (tag, attr)
                if key in seen:
                    continue
                seen.add(key)
                out.append({'tag': tag, 'group': group.name or '', 'attr': attr,
                            'used': [a for a in attrs if a in used]})
    return out


def card_note(beat: Beat, symbols=None, facts=None, project_root=None) -> str:
    """
    卡片底部那条提示的**完整文案**（一句话，`；` 分隔）。纯函数，可单测。

    为什么把"拼文案"放在这里而不是前端：这是本仓库的既有分工 ——
    **语义层出文案，前端只负责显示**（`speaker_missing_warning` 就是这么做的）。
    前端多拼一句，就多一处"两边说法不一致"的机会。

    目前两句：
      ① 有台词却没有说话人（导出会变成旁白）—— 原有行为；
      ② **N3 同组冲突**：同一组里被同时选中两个属性。
         ⚠ 为什么必须在这里说：实测 `show lan happy sad`（两者同组）
         **lint 退出码 0、引擎一声不吭** —— 这类冲突 lint 不管，
         只能靠引擎给的 group 自己算（`group_conflicts`）。
    """
    parts = []
    conflicts: list = []
    try:
        first = speaker_missing_warning(beat, symbols)
        if first:
            parts.append(first)
    except Exception as e:                          # noqa: BLE001
        swallow('app.beats.card_note.speaker', e)
    try:
        conflicts = list(beat_group_conflicts(beat, facts))
        for hit in conflicts:
            parts.append(f"「{hit['tag']}」的 {hit['group']} 组里同时选了 "
                         f"{'、'.join(hit['attrs'])}，引擎只认其中一个")
    except Exception as e:                          # noqa: BLE001
        swallow('app.beats.card_note.conflicts', e)
    try:
        # 引擎没认的属性：**只是提示**（候选与导入仍以实际资源为准，不拦）
        unknown = beat_unknown_attrs(beat, facts)
        for hit in unknown[:3]:
            parts.append(f"「{hit['tag']}」里的 {hit['attr']} 引擎没认"
                         "（可能是掩码/切图这类内部件，导出时它会报错）")
        if len(unknown) > 3:
            parts.append(f"另有 {len(unknown) - 3} 项引擎也没认")
    except Exception as e:                          # noqa: BLE001
        swallow('app.beats.card_note.unknown', e)
    try:
        # **角色变量 ≠ 立绘 tag**（2026-10-18）：`show lan …` 里 `lan` 是
        # 角色变量，真正能跑的 image tag 可能是 `lan_tag`。候选已经改成给 tag，
        # 但手写/旧数据仍可能踩到 —— 这里只提示，**绝不自动改**。
        show_words = str(getattr(beat, 'show', '') or '').split()
        if show_words and symbols is not None:
            tag = show_words[0]
            has_image = getattr(symbols, 'has_image_tag', None)
            map_tag = getattr(symbols, 'actor_image_tag', None)
            if callable(has_image) and not has_image(tag):
                mapped = (str(map_tag(tag) or '').strip()
                          if callable(map_tag) else '')
                if mapped and mapped != tag:
                    parts.append(f'「{tag}」是角色变量；这张立绘的 show tag 是 '
                                 f'{mapped}（不改你的写法，只提醒）')
    except Exception as e:                          # noqa: BLE001
        swallow('app.beats.card_note.image_tag', e)
    try:
        # **写了却被引擎静默丢掉**的属性（同组里的第二个词）——
        # ⚠ 只在**真有同组冲突**时才去读项目里的 `layeredimage`（首次 0.28 s）：
        # 没有冲突的拍（绝大多数）一次磁盘都不摸。见 `silent_default_attrs`。
        groups = {str(h.get('group') or '') for h in conflicts}
        for hit in silent_default_attrs(beat, project_root=project_root,
                                        only_groups=groups)[:2]:
            rest = '、'.join(hit['used']) or '默认的那一个'
            parts.append(f"「{hit['tag']}」里的 {hit['attr']} 没生效"
                         f"（{hit['group']} 组里引擎用的是 {rest}）")
    except Exception as e:                          # noqa: BLE001
        swallow('app.beats.card_note.silent', e)
    return '；'.join(parts)


def speaker_missing_warning(beat: Beat, symbols=None) -> str:
    """
    这一拍的说话人**有问题**吗？有问题就把话说清楚（返回 '' 表示没问题）。

    三种情形：

      ① 有台词、却没有任何说话人 → 导出后就是旁白（用户 2026-09-28 报的
         "幽灵旁白"：卡片上什么都没提示，空着也能点「下一句」）；
      ② **说话人没登记**（2026-10-05 用户实测：填了 `lj`，而角色表里没有它）
         → 渲染器按 D7 的纪律**丢掉这个说话人**，那一行按旁白写、另加一条
         `# TODO: 缺少资源 角色 'lj'`。用户看到的是"我填的说话人凭空没了"
         —— 必须当场说清楚，否则他只会觉得工具坏了；
      ③ 其余 → 不吵。

    `extend` 例外：它本来就不带说话人（续上一句），渲染成 `extend "…"`。
    旁白（`narration`）也例外 —— 它**就是**旁白。

    返回 '' 表示不需要提示（调用方据此清掉提示条）。
    这里**只算文案**，怎么显示由前端决定（`app` 层不认识控件）。
    """
    if not (beat.text or '').strip():
        # ④ **填了说话人、却没有台词** → 说话人哪也去不了（2026-10-18）。
        #
        # 语句拍（`play_music …` / `styled …`）在卡片上也有说话人那一格。
        # 人填了 `lan`、按保存，产物里**一个字都没多** —— 因为渲染只对
        # "有台词"的拍写 `lan "…"`。用户看到的是"我填的说话人不见了"，
        # 与 ② 是同一类"静默丢弃"，只是来路不同。
        if str(beat.speaker or '').strip():
            return (f'⚠ 这一拍填了说话人 `{beat.speaker}`，但**没有台词** —— '
                    f'说话人不会写进脚本（它只属于台词那一行）；'
                    f'要让这句话有人物就写一句台词，否则把说话人清掉')
        return ''
    if beat.speaker:
        # ② 登记过吗？（与渲染器同一条判据：`Audit.character`）
        if symbols is not None:
            try:
                from storygraph.story.dsl import TODO as _TODO, Audit
                if Audit(symbols).character(str(beat.speaker).strip()) == _TODO:
                    return (f'⚠ 角色 `{beat.speaker}` **没有登记** —— '
                            f'导出会**丢掉这个说话人**（那一句按旁白写，'
                            f'再补一条 `# TODO: 缺少资源 角色 \'{beat.speaker}\'`）。\n'
                            f'   把它补进角色表，或双击右栏「参与角色」里的名字换一个。')
            except Exception as e:            # noqa: BLE001 - 判不了就不吵
                swallow('app.beats.speaker_missing_warning', e)
        return ''
    if beat.kind in ('narration', 'extend', KIND_MENU, KIND_MENU_OPTION):
        # `menu` / `menu_option` 例外（2026-10-19，P1-3 收尾时顺手抓到）：
        # 这两类拍的「台词」格装的是**菜单标题 / 选项文案**，本来就不该有
        # 说话人（引擎的菜单选项行只有 `"文案" [if cond]:`）。从前这里照样
        # 弹"没有说话人 → 会变成旁白"，等于教人去点一个不存在的开关。
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
    except Exception as e:
        swallow('app.beats.inherited_state_hint', e)
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
    except Exception as e:
        swallow('app.beats.categorize_assets', e)
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


def _declared_words(symbols, kind: str) -> set:
    """项目在词表里**自己声明过**的词（`'expression'` / `'position'`）。"""
    return set(getattr(symbols, 'project_words', {}).get(kind, ()) or ())


def _expression_display(attr: str, tag: str, beat: Beat, symbols) -> str:
    """
    一个图像属性 → 卡片上显示的值。

    **项目词表里的词优先，否则就是属性名本身**（2026-10-05 用户实测报）：

    > 如果你标中文人话词，那么你就要遇到一个问题：万一……这个项目不用中文做
    > 怎么办？工具应是通用的！

    内置词表（工具自带的那批中文）是**输入别名**，不是项目的说法 ——
    它不参与显示，也不参与候选。这条规矩同时保证**显示值与候选值一致**：
    不一致的话 `choices.index(cur)` 会找不到当前值 → 点 chip 崩掉/没反应
    （"切不出生气"就是这么来的）。
    """
    if not attr or symbols is None:
        return attr
    declared = _declared_words(symbols, 'expression')
    if not declared:
        return attr
    try:
        word = symbols.expression_word_for(tag, attr)
    except Exception as e:                    # noqa: BLE001
        swallow('app.beats._expression_display', e)
        return attr
    if not word or word not in declared:
        return attr
    # 自证无损：翻回来必须是同一个属性（见 `beat_expression_word`）
    try:
        if normalize_expression(word, beat, symbols) == attr:
            return word
    except Exception as e:                    # noqa: BLE001
        swallow('app.beats._expression_display', e)
    return attr


def _position_display(transform: str, symbols) -> str:
    """一个 transform → 卡片上显示的值（同 `_expression_display` 的规矩）。"""
    if not transform or symbols is None:
        return transform
    declared = _declared_words(symbols, 'position')
    if not declared:
        return transform
    try:
        word = symbols.position_word_for(transform)
    except Exception as e:                    # noqa: BLE001
        swallow('app.beats._position_display', e)
        return transform
    return word if word in declared else transform


def image_tag_of(spec: str, symbols) -> str:
    """
    图像名 → **image tag**（`ling_angry` 与 `ling angry` 都要给 `ling`）。

    为什么需要（用户 2026-10-05："表情也调不了了，修复！"）：

    卡片里存的是工具自己的**下划线写法**（`ling_angry`），而从前这里只做
    `spec.split()[0]` —— 拿到的"tag"就是 `ling_angry`，`attrs_of_tag` 自然
    一个都查不到 → **表情候选全空**、chip 点不动。

    现在：有空格 → 第一段就是 tag；没空格 → 走**唯一真源**的引用解析，
    从命中的那一行读出真正的 `Tag`（顺带避开"某行 ID 恰好叫 ling_angry
    但没有 Tag"的劫持）。
    """
    spec = (spec or '').strip()
    if not spec:
        return ''
    parts = spec.split()
    if len(parts) > 1:
        return parts[0]
    if symbols is None:
        return parts[0]
    try:
        # `Symbols` 里已经有索引（`_get_index`），解析不用重建
        for aid, row in symbols.image_rows_for(spec):
            tag = str((row or {}).get('Tag') or '').strip()
            if tag:
                return tag
    except Exception as e:                        # noqa: BLE001 - 解析不了就退回原写法
        swallow('app.beats.image_tag_of', e)
    if '_' in spec:
        # 退路：`ling_angry` → 猜前一段是 tag（先看这个 tag 表里认不认识）
        try:
            head = spec.split('_')[0]
            if head and symbols.has_image_tag(head):
                return head
        except Exception as e:                    # noqa: BLE001
            swallow('app.beats.image_tag_of.head', e)
    return parts[0]


def position_choices(symbols, limit: int = 500) -> list:
    """
    位置候选：**项目自己的 transform 排最前**，然后才是内置位置。

    用户 2026-10-05 实测报：

    > 那些中央等位置同理，我没有看见我的 i11……我怕函数名对不上！

    两处叠加（都在这条候选表上）：

      · 顺序 + 截断：从前是"内置词表的**中文词**（19 个）→ 项目 transform"，
        而 `limit=40` 一到就砍 —— 真实项目 146 个 transform 里 `i11` 排在
        第 40 名之后，**永远看不见**；
      · 通用性：`左` / `中央` 是**工具内置**的说法，一个不用中文做的项目
        不该在候选里看到它们（见 `Symbols.project_words`）。

    现在候选是**项目真实的名字**：项目的 transform（全部，按名字排序）+
    内置位置那几个**真取值**（`left` / `center` / `right`…），
    每一项再经 `_position_display`（项目词表声明过就显示它的词，否则显示
    transform 名）。中文词仍然可用的**输入别名**（`normalize_position` 那条
    通道），只是不再冒充候选 —— 写进脚本的永远是 transform 名，所以
    "对不上"这件事在候选这一层就消失了。
    """
    names = []
    reg = getattr(symbols, 'registry', None)
    for name in sorted((getattr(reg, 'transforms', {}) or {})):
        if name and name not in names:
            names.append(name)
    for _word, mapped in (getattr(symbols, 'positions', {}) or {}).items():
        name = str(mapped or '').strip()
        if name and name not in names:
            names.append(name)
    out = []
    for name in names:
        value = _position_display(name, symbols)
        if value and value not in out:
            out.append(value)
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

    ⚠ **还要是"项目自己声明过的词"**（2026-10-05 用户实测报）：

    > 如果你标中文人话词，那么你就要遇到一个问题：万一……这个项目不用中文做
    > 怎么办？工具应是通用的！

    内置词表（`DEFAULT_EXPRESSIONS` 那批中文）是**输入别名**，不是项目的说法：
    一个不用中文做的项目不该在卡片上看到「生气」。所以显示优先级是
    **项目词表里的词 → 属性名本身**（内置词只用来解析输入）。
    这同时修掉"切不出"那一半：候选现在就是属性名，显示与候选对得上，
    点 chip 不会因为"当前值不在候选里"而卡住。
    """
    item = _first_show_item(beat)
    if not item:
        return ''
    parts = (item.get('image') or '').split()
    if len(parts) < 2:
        return ''
    tag, attrs = parts[0], parts[1:]
    return ' '.join(_expression_display(a, tag, beat, symbols) for a in attrs)


def beat_position_word(beat: Beat, symbols=None) -> str:
    """
    Beat → 位置（给卡片显示）。立绘没写 `@位置` 时退回 Beat 的 `at`。

    ⚠ **默认显示 transform 名本身**（`d11` / `i11` / `center`），只有
    **项目自己在词表里声明过**的说法才优先显示（2026-10-05 用户实测报）：

    > 那些中央等位置同理，我没有看见我的 i11……我怕函数名对不上！

    内置的中文位置词（`中央`）是**输入别名**，不是项目的说法。显示 `中央`
    而脚本里写 `center` 正是"对不上"的来源 —— 现在是项目词表 → transform 名。
    """
    item = _first_show_item(beat)
    if not item:
        return ''
    at = (item.get('at') or '').strip() or (getattr(beat, 'at', '') or '').strip()
    if not at or symbols is None:
        return at
    return _position_display(at, symbols)


def spec_arg_choices_for(beat: Beat, statements, registry=None) -> dict:
    """
    这一拍的**语句参数候选**：`{参数名: [候选值]}`（D1 / M4 第二阶段）。

    给卡片编辑器用 —— 有参数形状的语句，值那一栏应当是**下拉**，
    而不是让人凭记忆敲。**没有候选就返回 `{}`**：不猜候选，猜出来的下拉
    会诱导人写出脚本里根本没有的值。

    两档来源（与 Excel 那侧**同源**，都走 `statement_registry.arg_columns`）：

      · 项目声明的自定义语句（`rules.json` 的 `columns[].choices`）；
      · **标准语句的内置形状**（`camera` 层名 / `with` 转场 / `window` 动作 …）
        —— `with` 的候选来自**资源表里真实存在的变换**（`choices_from`），
        没有资源表就不给候选（宁可没有下拉，也不要编一串项目里没有的名字）。
    """
    out: dict = {}
    # ⚠ **不早退**：`statements` 与 `registry` 都为 None 时，内置形状表
    # （`window` 的 show/hide/auto）照样要有候选 —— 那三条是工具自己声明的，
    # 不需要项目声明，也不需要资源表。
    for c in spec_arg_columns_of(beat, statements):
        # ⚠ 两种形状别混：`spec_columns` 的 `name` **就是参数名**，而
        # `dynamic_spec_columns` 的 `name` 是**语句名**、参数名在 `param`。
        # 取参数名一律用 `param`-优先（第一版写成 `c['name']`，
        # 于是返回的键成了语句名 `mycue` —— `test_card_choices_provider` 当场红）。
        param = str(c.get('param') or c.get('name') or '')
        if not param:
            continue
        choices = list(c.get('choices') or [])
        source = str(c.get('choices_from') or '')
        if source == 'transform' and registry is not None:
            rows = getattr(registry, 'transforms', None) or {}
            choices = sorted(str(k) for k in rows) or choices
        if choices:
            out[param] = choices
    return out


def spec_arg_columns_of(beat: Beat, statements) -> list:
    """这一拍**自己的**参数列描述（`dynamic_spec_columns([beat], …)`）。"""
    try:
        from storygraph.story.statement_registry import dynamic_spec_columns
        return dynamic_spec_columns([beat], statements)
    except Exception as e:                      # noqa: BLE001 - 卡片不该因此打不开
        from storygraph.core.safeguard import swallow
        swallow('app.beats.spec_arg_columns_of', e)
        return []


def spec_arg_fields_for(beat: Beat, statements) -> tuple:
    """
    卡片参数那一组要的 `(列描述, 当前值)`（`{列名: 值}`）。

    值走 `Beat.spec_arg_cells` —— 与 Excel 的格子**同一个算法**，所以卡片上
    看到的值与表里那格必然一致（两处各算一遍就会出现"表里是 A、卡片是 B"）。
    """
    cols = spec_arg_columns_of(beat, statements)
    if not cols:
        return [], {}
    try:
        cells = beat.spec_arg_cells(cols)
    except Exception as e:                      # noqa: BLE001
        from storygraph.core.safeguard import swallow
        swallow('app.beats.spec_arg_fields_for', e)
        return cols, {}
    return cols, {c['header']: v for c, v in zip(cols, cells)}


def apply_spec_arg_fields(beat: Beat, values: dict) -> list:
    """
    卡片上改过的参数 → 折进这一拍（**与 Excel 读表同一条路径**）。

    返回被清洗过的列名（W114 留痕用，与 `from_row` 同一套语义）。
    """
    from storygraph.story.beat_model import fold_spec_arg_columns
    return fold_spec_arg_columns(beat, values or {})


def expression_choices(beat: Beat, symbols, limit: int = 500) -> list:
    """
    当前立绘的**可选表情**清单 = 资源表里这个 tag 真正声明过的**属性名**。

    用户 2026-10-05 实测报：

    > ling 一开始有很多个表情，但是不知道为何切着切着就少了！最后不剩几个，
    > 切不出生气！

    候选从前是**词表里的人话词**（`生气`）而不是属性名（`angry`），于是：

      · **覆盖不全**：没有词表条目的属性（`base_arms_akimbo` / `blankface` /
        `avoiding`…）压根不在候选里（"一开始有很多，切着切着就少了"）；
      · **卡死**：一旦当前值是属性名（`angry` —— 例如从 `show ling angry` 读回来的），
        它**不在候选里** → 点 chip 走"固定当前值"那一支 → **点了没反应**
        （"切不出生气"）。实测连点 20 次，值一个字都没变。

    现在候选就是项目真实的属性名（**写进脚本的那一串**），任何项目都通用；
    人话词仍然能当**输入别名**用（`normalize_expression` 那条通道，项目自己在
    词表里声明过的词还能优先显示，见 `Symbols.project_words`）。
    """
    item = _first_show_item(beat)
    if not item or symbols is None:
        return []
    tag = image_tag_of(str(item.get('image') or ''), symbols)
    if not tag:
        return []
    try:
        attrs = symbols.attrs_of_tag(tag)
    except Exception as e:
        swallow('app.beats.expression_choices', e)
        return []
    out = []
    for attr in attrs[:limit]:
        # 显示值与候选值**同源**（`_expression_display`）—— 不一致的话
        # `choices.index(cur)` 找不到当前值，chip 会崩/卡（见上）。
        value = _expression_display(str(attr or '').strip(), tag, beat, symbols)
        if value and value not in out:
            out.append(value)
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
    except Exception as e:                        # noqa: BLE001
        swallow('app.beats.participants', e)
        return []
    if registry is None:
        return []
    chars = getattr(registry, 'characters', {}) or {}
    ranked = sorted(
        chars.items(),
        key=lambda kv: (-(kv[1] or {}).get('_dialogue_count', 0), str(kv[0])))
    return [cid for cid, _row in ranked[:limit]]


def expression_choice_groups(beat: Beat, symbols, groups=None,
                            limit: int = 500) -> list:
    """
    当前立绘的可选**属性候选，按项目声明的词组分栏**。

    用户 2026-10-10：

    > 实际上需要分组显示……但因为我这个要求通用！所以一般人其实是没有服装这个
    > 选项的！所以事实上应当通过 B 这个自定义模块，做到把服装跟表情这种东西分开！

    所以"分不分栏、分哪几栏"**由项目在 `rules.json` 的 `attr_groups` 里声明**，
    这里只是把声明套到真正存在的候选上（`expression_choices` 的同一份清单，
    绝不另算一份）。返回 `[{'group','label','values'}]`。

    ⚠ **没有声明（或声明了但一个词都没归到）→ 返回 `[]`** —— 调用方据此走原来
    的平铺候选，于是不声明词组的项目行为**一个字节不变**。这条是通用性的底线：
    一般人没有"服装"这个概念，他就不该看到任何分组。
    """
    from storygraph.authoring.spec_groups import group_choices
    if not groups:
        return []
    values = expression_choices(beat, symbols, limit=limit)
    return group_choices(values, groups)


def expression_groups_for(tag: str, symbols, groups, facts=None) -> list:
    """
    `(tag, 符号表, 项目声明的词组)` → `[{'group','label','values'}]`，
    **值是界面显示值**（与 `choices_for_image` 的 `expression` 同一套
    `_expression_display`）—— 否则下拉里会出现 `happy` 而候选里是「开心」，
    两条路给出不一样的字面量（`expression_choices` 的 docstring 记着这个坑）。

    `groups` 为空 → 返回 `[]`（调用方走平铺），这条是通用性底线。

    **E2（2026-10-10）：`facts` 给了就以引擎为准。**
    分组与候选都来自引擎（`layeredimage` 的 group + 引擎枚举的属性），
    `groups`（项目声明）**降级为"改名"覆盖层** —— 人只把 `outfit` 显示成「服装」。
    实测依据：真机 `lan` 的组是 `outfit` / `expressions`，这本来就写在引擎里，
    让人再声明一遍是重复劳动、还会两边打架。
    ⚠ `facts` 为空（没采过）或引擎没给这个 tag 的分组 → **原路不变**（一字不改）。
    """
    if not tag:
        return []
    probe = Beat()
    probe.show = str(tag)

    if facts:
        from storygraph.core import engine_facts as EF
        from storygraph.core.safeguard import swallow as _sw
        try:
            if EF.attributes_of(facts, str(tag)):
                overlay = EF.rename_overlay(groups)
                rows = EF.grouped_choices(facts, str(tag), overlay)
            else:
                rows = []
        except Exception as e:                     # noqa: BLE001
            _sw('app.beats.expression_groups_for.facts', e)
            rows = []
        if rows:
            out = []
            for idx, entry in enumerate(rows, 1):
                group = str(entry.get('group') or '')
                label = str(entry.get('label') or '')
                if not label or label == group:
                    # 没声明改名时不把内部组名当标题（`outfit` → 「属性 1」）
                    label = f'属性 {idx}'
                vals = []
                for attr in entry['values']:
                    shown = (_expression_display(attr, str(tag), probe, symbols)
                             if symbols is not None else '') or attr
                    if shown and shown not in vals:
                        vals.append(shown)
                if vals:
                    out.append({'group': group, 'label': label,
                                'values': vals})
            if out:
                return out

    from storygraph.authoring.spec_groups import group_values
    if not groups or symbols is None:
        return []
    try:
        attrs = [str(a or '').strip() for a in symbols.attrs_of_tag(tag)]
    except Exception as e:                        # noqa: BLE001
        swallow('app.beats.expression_groups_for', e)
        return []
    out = []
    for entry in group_values([a for a in attrs if a], groups):
        vals = []
        for attr in entry['values']:
            shown = _expression_display(attr, tag, probe, symbols) or attr
            if shown and shown not in vals:
                vals.append(shown)
        if vals:
            out.append({'group': entry['name'], 'label': entry['label'],
                        'values': vals})
    return out


def _symbols_project_root(symbols):
    """
    符号表 → 项目根（认不出返回 `None`）。

    `Symbols` 自己不带 `project_root`，但它拿着 `registry`（`AssetRegistry`
    在 `parse_asset_tables` 里被赋了 `project_root`）—— 所以这里两级都试。
    **认不出就说认不出**：调用方据此不猜（`attr_slot_rows` 退回老路）。
    """
    root = getattr(symbols, 'project_root', None)
    if root:
        return root
    reg = getattr(symbols, 'registry', None)
    return getattr(reg, 'project_root', None) if reg is not None else None


def attr_slot_rows(image_spec: str, symbols, groups=None, facts=None,
                   layer_model=None) -> list:
    """
    一个立绘 spec → **按引擎分组的一维一格**行（`[{'group','label','value',
    'display','options','other'}, …]`）。

    用户 2026-10-12 实测报的缺口（`show ye base_handdown gratified`）：

    > 它明明就有两个动作……可是为什么编辑器不报呢？如果它不报的话……那么我就
    > 永远无法调节 show base_handdown+表情了，我就永远只能调默认的了

    病根不在"认不认识"（引擎两个属性都认、也不同组、不冲突），而在**卡片只有一个
    表情格**：那一个格子里塞了"服装 + 表情"两个词，于是
      · 当前值（`'base_handdown gratified'`）**不在**候选表里（候选是单个词）
        → 点 chip 走"固定当前值"那一支，**看不出任何反应**；
      · 从下拉里挑一个词 → `tag + 那个词` 重拼整张图，**服装被吃掉**
        （`show ye base_handdown gratified` → `show ye gratified`）。

    修法：**维度由引擎给**（`layeredimage` 的 group），工具一个组名都不写死 ——
    `outfit` / `expressions` 各自一个格子，点哪格只**替换那一格的那个词**。

    分组的来源有两档，先引擎后项目：
      1. **引擎事实**（`engine_facts.json` 的 group + 引擎枚举的属性）——
         真源，实测 `ye` 给的正是 `outfit` / `expressions`；
      2. 引擎事实没有时，退回 `layered_model`（直接读项目里的 `layeredimage`）。

    ⚠ **三处刻意的"不宣称 / 不乱动"**：
      · `groups`（项目在 `rules.json` 里声明的词组）只当**改名层**用，不参与分组
        ——结构已经写在引擎里，人再来指定成员就会两边打架（与 `rename_overlay`
        同一条口径）；
      · ⚠⚠ **建不建格看"引擎给了几个组"，不看"这一拍写了几个词"**
        （2026-10-16 改，用户实测报的"有的卡有服装格、有的没有"）：
        从前是"有值的维度不到 2 条 → 返回 `[]`"（`show ling happy` 走单格老路），
        于是**同一套立绘配置，卡片之间不一致**：
          · `ling close_happy base_swimsuit`（写了两个词）→ 两格；
          · `ling happy` / `lan happy`（只写一个词）→ 一个格都没有，服装改不了。
        用户原话："我明明配置了全局的 show 表情……为何一张卡片有服装，
        另一张卡片却没有啊？？？"
        现在：**引擎给了 ≥2 个组就每个组都建一格**，这一拍没写的维度显示
        `—`（点一下才把它固定到这一拍）—— 与"位置：i11（继承）"那条规矩统一。
        ⚠ 通用性底线**仍在**：没有引擎事实、也读不到项目 `layeredimage`
        （`group_rows` 为空）的项目照旧返回 `[]` 走老路，一个字节不变；
      · `extras`：**不属于任何引擎组的词原样留着**（`base_handdown_mask` /
        `_hd_cut` 这类内部件 —— 实测资源表里真有它们）。我们不认识它们，但
        **别人的东西一个字节都不许丢**。
    """
    spec = str(image_spec or '').strip()
    toks = [t for t in (_show_item_attr(t) for t in spec.split()) if t]
    # ⚠ 只要**认得出 tag**就往下走：`show lan`（这一拍没写属性）也应该能长出
    #   "服装 / 表情"格 —— 否则用户想补一个属性时根本没有入口
    #   （2026-10-18 实测："卡片编辑器内服装不显示"）。
    if not toks or symbols is None:
        return []
    tag, attrs = toks[0], toks[1:]
    # 项目声明只当**改名层**（`outfit` → 「服装」）：结构仍由引擎给。
    overlay: dict = {}
    try:
        from storygraph.core import engine_facts as EF
        overlay = EF.rename_overlay(groups)
    except Exception as e:                          # noqa: BLE001 - 坏声明不该炸卡片
        swallow('app.beats.attr_slot_rows.overlay', e)
    group_rows = []
    try:
        if facts:
            from storygraph.core import engine_facts as EF
            group_rows = list(EF.grouped_choices(facts, tag, overlay) or [])
    except Exception as e:                        # noqa: BLE001 - 引擎事实坏掉不该炸卡片
        swallow('app.beats.attr_slot_rows.facts', e)
        group_rows = []
    if not group_rows:
        # 没采过引擎事实 / 引擎没给这个 tag 的分组 → 退回项目里的 `layeredimage`
        # 声明（同一份结构，只是从 `.rpy` 现读）。
        try:
            model = layer_model
            if model is None:
                from storygraph.story.layered_model import load_layer_model_cached
                root = _symbols_project_root(symbols)
                model = load_layer_model_cached(root) if root else None
            img = (model.images or {}).get(tag) if model is not None else None
            if img is not None:
                buckets: dict = {}
                for g in img.groups:
                    names = [n for n in g.attrs if n and n not in buckets]
                    buckets.setdefault(g.name or '', [])
                    for n in names:
                        if n not in buckets[g.name or '']:
                            buckets[g.name or ''].append(n)
                group_rows = [{'group': k, 'label': overlay.get(k) or k,
                               'values': v}
                              for k, v in buckets.items() if v]
        except Exception as e:                    # noqa: BLE001
            swallow('app.beats.attr_slot_rows.layers', e)
            group_rows = []
    if not group_rows and groups:
        # 第三档：**项目自己声明的分栏**（`rules.json` 的 `attr_groups`）。
        # 没有引擎事实、也读不到 `layeredimage` 的项目，声明就是唯一结构来源。
        # ⚠ 仍然通用：按声明里的 `options` 归组，**不写死任何组名**。
        try:
            from storygraph.authoring.spec_groups import (group_choices,
                                                          parse_attr_groups)
            declared = groups
            if declared and isinstance(declared[0], dict):
                declared = parse_attr_groups(declared)
            known_attrs = [str(a or '').strip()
                           for a in (symbols.attrs_of_tag(tag) or ())
                           if str(a or '').strip()]
            if known_attrs:
                group_rows = [{'group': e['group'],
                               'label': e['label'] or e['group'],
                               'values': list(e['values'])}
                              for e in group_choices(known_attrs, declared)]
            else:
                for g in declared:
                    vals = [str(v) for v in (getattr(g, 'options', ()) or ())
                            if str(v).strip()]
                    if vals:
                        group_rows.append({
                            'group': str(getattr(g, 'name', '') or ''),
                            'label': str(getattr(g, 'title', lambda: '')()
                                         or getattr(g, 'name', '') or ''),
                            'values': vals})
        except Exception as e:                    # noqa: BLE001 - 坏声明不该炸卡片
            swallow('app.beats.attr_slot_rows.groups', e)
            group_rows = []
    # ⚠ **组数才是判据**（每组一格；≥1 个组就建格）。从前判的是"有值的维度数"，
    #   于是"只写了一个词的拍"一个格都没有、只写 tag 的拍更没有入口。
    #   三档结构来源：引擎事实 → 项目 `layeredimage` → 项目声明的 `attr_groups`；
    #   三档都认不出（`group_rows` 为空）才返回 `[]` 走老路。
    if not group_rows:
        return []
    # —— 显式拆分（2026-10-18）：引擎把动作/姿势并进 `outfit` 时，
    #    项目可以声明 `{"name":"pose","label":"动作","options":[...],"split":true}`
    #    把那些词从引擎组里**取走**，单独成一格。只影响卡片/候选，不改引擎语义。
    declared_groups = groups
    if declared_groups and isinstance(declared_groups[0], dict):
        try:
            from storygraph.authoring.spec_groups import parse_attr_groups
            declared_groups = parse_attr_groups(declared_groups)
        except Exception as e:                  # noqa: BLE001
            swallow('app.beats.attr_slot_rows.split.parse', e)
            declared_groups = []
    split_groups = [g for g in (declared_groups or ())
                    if bool(getattr(g, 'split', False))]
    if split_groups:
        engine_values = {str(v) for e in group_rows
                         for v in (e.get('values') or ())}
        split_slots, taken_words = [], set()
        for g in split_groups:
            wanted = [str(v) for v in (getattr(g, 'options', ()) or ())
                      if str(v).strip()]
            vals = [v for v in wanted if v in engine_values] or wanted
            if not vals:
                continue
            taken_words.update(vals)
            split_slots.append({'group': str(getattr(g, 'name', '') or ''),
                                'label': str(getattr(g, 'title', lambda: '')()
                                             or getattr(g, 'name', '') or ''),
                                'values': vals})
        if split_slots:
            kept = []
            for entry in group_rows:
                rest = [v for v in (entry.get('values') or ())
                        if v not in taken_words]
                if rest:
                    kept.append({**entry, 'values': rest})
            group_rows = kept + split_slots
    # 没有项目改名时，**不要把引擎内部组名**（`outfit` / `expressions`）当标题 ——
    # 用户 2026-10-18："我明明定义的编剧名称是服装，卡片上却显示 outfit"。
    # 标题用通用的「属性 1 / 属性 2」，内部组名只进 tooltip（`sprite_grid` 里）。
    labeled_rows = []
    for i, entry in enumerate(group_rows, 1):
        group_name = str(entry.get('group') or '')
        label = (overlay.get(group_name) or str(entry.get('label') or '')).strip()
        if not label or label == group_name:
            label = f'属性 {i}'
        labeled_rows.append({**entry, 'label': label})
    group_rows = labeled_rows
    # 当前值 → 每一维当前选中的那个词（一个词只归一组，先命中的组拿走 —— 与
    # `grouped_choices` 的"值 = 该组成员"同口径）。
    taken = set()
    slots = []
    for entry in group_rows:
        vals = [str(v) for v in (entry.get('values') or ()) if str(v)]
        cur = next((a for a in attrs if a in vals and a not in taken), '')
        if cur:
            taken.add(cur)
        slots.append({'group': str(entry.get('group') or ''),
                      'label': str(entry.get('label') or '')
                               or str(entry.get('group') or ''),
                      'value': cur, 'options': vals})
    # 不属于任何引擎组的词（掩码/切图这类内部件，实测项目里真有）原样留着。
    known = {v for s in slots for v in s['options']}
    extras = [a for a in attrs if a and a not in known]
    for slot in slots:
        probe = Beat(seq=1, label='', text='',
                     show=' '.join([tag] + ([slot['value']]
                                            if slot['value'] else [])))
        slot['display'] = (_expression_display(slot['value'], tag, probe, symbols)
                           if slot['value'] else '')
        # **其它维度的词原样保留**（点一格不许吃掉另一格）——点它时用这一份去合成。
        slot['other'] = [a for a in attrs if a and a != slot['value']]
        slot['extras'] = list(extras)
    return slots


def _replace_attr_word(attrs, old: str, new: str) -> list:
    """
    把 `attrs` 里的 `old` **原位**换成 `new`（没有 `old` 就追加到末尾）。

    为什么原位：Ren'Py 的 `show ye base_handdown happy` 是作者写的顺序，
    换表情不该让服装突然跳到后面去（往返 diff 越小越好）。
    """
    out = [a for a in (attrs or ()) if a and a != new]
    if old and old in out:
        out[out.index(old)] = new
        return out
    return out + [new]


def apply_attr_slot(image_spec: str, group: str, value: str, *, symbols=None,
                    groups=None, facts=None, layer_model=None) -> str:
    """
    **只换一个维度的词**，其余维度原样保留 → 新的 `show` spec。

    这是"点一下服装 chip，表情不丢"的唯一实现点（用户 2026-10-12 的原始诉求）。
    认不出这个维度 / 这张图没有多条维度 → 原样返回 `image_spec`（宁可不改，
    也不猜着改 —— 猜错就是演出事故）。
    """
    spec = str(image_spec or '').strip()
    rows = attr_slot_rows(spec, symbols, groups=groups, facts=facts,
                          layer_model=layer_model)
    hit = next((r for r in rows if r['group'] == str(group or '')), None)
    if hit is None:
        return spec
    toks = spec.split()
    tag = toks[0] if toks else ''
    if not tag:
        return spec
    value = str(value or '').strip()
    attrs = _replace_attr_word(toks[1:], hit['value'], value)
    # **不认识的词留在原位**（`base_handdown_mask` / `_hd_cut` 这类内部件）——
    # 一维一格只负责换它认识的那一维，其余一个字节都不动。
    attrs = [a for a in attrs if a and a != value]
    if value:
        attrs = attrs + [value]
    out = [tag] + attrs
    return ' '.join(out)


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
    except Exception as e:
        swallow('app.beats.normalize_expression', e)
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

    ⚠ **两个词被归一成同一个属性时，一个都不改**（2026-10-05 用户实测报：
    "明明已经添加了立绘，导出后对应的 show 却没有出现"）。实测（`ch01`）：

        show ling base_arms_akimbo sigh at i11      ← 作者写的
        → cards_to_show（**没改任何东西**，只是读了再写）
        show ling base_arms_akimbo_sigh base_arms_akimbo_sigh at i11

    病根是 `Symbols.attr_for` 的最后一档（子串兜底）：项目里那张图登记成
    `base_arms_akimbo_sigh`（一个属性），于是 `base_arms_akimbo` → 兜底命中
    整条属性、`sigh` → 也命中同一条 —— 两个词合并成一条，图像名被改写，
    **游戏里就找不到这张立绘了**（这正是用户说的"对应的 show 没出现"）。

    判据（与 `beat_expression_word` 的"自证无损"同一条思路，但**逐词**判）：
    归一化里那些"升级档"（下划线分词 / 子串兜底）**不许把两个词指向同一条
    属性** —— 一旦撞车，就退回**词表的原话**（权威档），而不是让 `attr_for`
    去猜：

        ['base_arms_akimbo', '无语']   词表写的是 无语 → sigh
          attr_for 第 3 档把 `base_arms_akimbo` 兜底成 `base_arms_akimbo_sigh`，
          又把 `sigh`（词表原话）分词命中成同一条 → 两个词合并成一条，
          图像名被改写（`ling base_arms_akimbo_sigh base_arms_akimbo_sigh`）。
          退回词表原话 → `['base_arms_akimbo', 'sigh']` = **作者写的那一份**。

    第一版写成"整组里有一个词被吃就整组原样"，结果把**中文词**留在了
    `show` 里（`ling base_arms_akimbo 无语@i11`）—— 那是"中文漏进脚本"，
    比原缺陷还糟（golden 快照当场抓到）。所以撞车时的退路是**词表原话**，
    而不是"原来的词"。
    """
    words = [w for w in str(expr or '').split() if w]
    if not words:
        return []
    probe = Beat(seq=1, label='', text='', show=image or '')
    mapped = [normalize_expression(w, probe, symbols) for w in words]
    if _any_word_swallows_another(mapped):
        plain = [_vocabulary_word(w, symbols) for w in words]
        if all(plain) and not _any_word_swallows_another(plain):
            return plain
        # 词表也救不了（例如作者本来就写了两个一样的词）：
        # 英文词（作者写的属性名）保持原样，中文词用词表原话。
        return [w if w.isascii() else (p or m)
                for w, p, m in zip(words, plain, mapped)]
    return mapped


def _any_word_swallows_another(values: list) -> bool:
    """这组属性里有没有"一个吃掉另一个"（相等、或互相包含）？"""
    vals = [v for v in values if v]
    for i, a in enumerate(vals):
        for j, b in enumerate(vals):
            if i != j and (a == b or b in a or a in b):
                return True
    return False


def _vocabulary_word(word: str, symbols) -> str:
    """
    词表里这条词的**原话**映射（`无语 → sigh`），不做 `attr_for` 的分词/子串升级。

    为什么要单独取它：升级档在**单词**场景下有用（项目把属性命名成
    `close_happy`，而词表写的是 `happy`），但在**多词**场景下会把两个词
    指向同一条属性 —— 那时应当相信词表，而不是相信长度启发式。
    """
    table = getattr(symbols, 'expressions', None) or {}
    try:
        mapped = table.get(str(word or '').strip())
    except Exception as e:                     # noqa: BLE001 - 词表形态不可控
        swallow('app.beats._vocabulary_word', e)
        mapped = None
    return str(mapped or word or '')


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
    except Exception as e:
        swallow('app.beats.normalize_position', e)
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
