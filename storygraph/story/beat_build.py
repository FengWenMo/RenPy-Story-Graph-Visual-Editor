"""
storygraph.story.beat_build
===========================
**从脚本 / 表格文本构造 Beat**：`.rpy` 的 Statement 流 →（折叠）→ Beat。

三件事：
  · `beats_from_statements`：把顺序语句流折叠成"一拍一句台词"（核心）
  · `hydrate_from_dsl`：把格内 DSL 原文（`[显示] …`）解释成结构化字段
  · `apply_script_definitions`：把脚本里的 `image` / `transform` /
    `define Character` 声明合并进资源表（"程序敲代码 → 表格跟着变"的关键一步）

`hydrate_from_dsl` **一律**要跑（含 registry=None）——不跑的话舞台快照、
校验、多引擎导出会看到"空舞台"。
"""

from __future__ import annotations
from storygraph.core.safeguard import swallow
from typing import Iterable, Optional
from storygraph.core.statement import Statement, StatementKind

from storygraph.story.beat_model import (
    Beat,
    KIND_EXTEND,
    KIND_MENU,
    KIND_MENU_OPTION,
    KIND_NARRATION,
    KIND_SAY,
    MENU_NO_CAPTION,
    _append_line,
    _assign_identity,
    _audio_params,
    _join_hide_items,
    _join_show_items,
    _parse_native_stage_tail,
    _show_item_text,
    _split_hide_items,
    _split_lines,
    _split_show_items,
    compose_audio_text,
    compose_stage_extra,
)



def beats_from_statements(statements: Iterable[Statement],
                          label: str = '',
                          labels: Optional[list] = None) -> list:
    """
    把顺序语句流折叠成 Beat 序列。

    规则：舞台指令（scene/show/hide/music/sound/voice）吸附在**紧随其后**
    的第一句台词上。若一段指令后面没有台词（比如结尾淡出），
    会自成一个 Beat，保证不丢。

    注释 / 原始语句（with、pause…）/ python 代码同样吸附到下一拍，
    分别进 Comment / Raw / CodeBlock 列，导出时原样回写。

    labels 与 statements 一一对应（每条语句所属 label）；不传时
    全部使用 label 参数。
    """
    statements = list(statements)
    if labels is None:
        labels = [label] * len(statements)

    beats = []
    pending = Beat(label=label)
    pending_has_stage = False
    current_label = label
    #: 最后一个说出台词的人，用来给 `extend` 标注"谁在续说"（显示用）。
    last_speaker = ''
    seq = 0

    def flush_stage_only():
        """
        把只含舞台指令、没有台词的指令段单独落成一行。

        **不在这里标结构位**：这一拍的 `_menu_member` 由"把它攒起来的那些
        语句"决定，而那些语句在处理时就已标过（见循环末尾）。若在这里按
        **当前**语句再标一次，就会拿"下一条语句"的值覆盖它 —— 实测
        `menu_option` 分支正是先 `flush_stage_only()` 再 append，
        于是选项体的拍被标成 `False`（选项拍的值），菜单组整块散架。
        """
        nonlocal pending, pending_has_stage, seq
        if pending_has_stage and not pending.text:
            seq += 1
            pending.seq = seq
            beats.append(pending)
        pending = Beat(label=current_label)
        pending_has_stage = False

    def _mark_struct(beat, stmt):
        """
        把**结构位**从语句转发到拍上：这一拍属于某个菜单块吗？

        `Statement._menu_member` 由解析器在 `_walk` 里标好（它当时还看得见
        `menu:` 的嵌套结构）。渲染层靠这一位把扁平的拍还原成 `menu:` 块
        （见 `beat_render.split_beat_units`）——没它就只能靠行号/条件去猜，
        而"猜"在"选项体后面紧跟一句块外台词"时必然猜错（两边条件都是空串）。

        **只在语句真的有这一位时才写**：`hasattr` 为假表示语句是手工构造的
        （单测、老代码），那就让 Beat 上根本没有这一位，渲染层自然会退回
        推断路径，而不是被一个假的 `False` 骗到。
        """
        if hasattr(stmt, '_menu_member'):
            beat._menu_member = bool(stmt._menu_member)
        return beat

    def mark_stage(verb):
        """记录舞台指令在本拍中的出现顺序，供导出时保序。"""
        order = pending.stage_order.split(',') if pending.stage_order else []
        if verb not in order:
            order.append(verb)
        pending.stage_order = ','.join(order)

    def with_src(beat, line_no):
        """
        给**直接构造的 Beat** 带上源行号（1-based）。

        为什么需要：`extend` / `menu` / `menu_option` / `jump` 这几个分支是
        直接 `beats.append(Beat(...))` 建的，不走 `pending` 那条路，
        所以 `pending.src_first/src_last` 的赋值到不了它们身上 ——
        实测 12 条 `extend` 全部 `src=[0,0]`，增量导出对不上位。
        """
        if line_no:
            beat.src_first = line_no
            beat.src_last = line_no
        return beat

    def mark_op(token):
        """
        记录操作在本拍中的**verb 级**出现顺序，供导出时逐条还原。

        存的是 verb（`scene` / `show` / `hide` / `audio` / `voice`），
        不是类别。**这一点很关键**：

        只记类别（`stage,audio`）时，渲染只能把整个舞台组一次输出，
        得到"整块舞台 + 整块音频"，于是 `scene → play → show` 变成
        `scene, show, play` —— `play` 被挤到 `show` 后面。
        demo ch01 实测就是这样（`play music night` 原本在
        `show eileen happy` 之前），而这属于**演出顺序被改**。

        audio / voice 同类只记一次：同一拍里两条 `play sound` 已经被
        flush 拆成了两拍（见 PLAY 分支），所以不需要计数。
        舞台 verb 按 `mark_stage` 的语义去重（同 verb 不重复记）。
        """
        if token in ('scene', 'show', 'hide'):
            order = pending.op_order.split(',') if pending.op_order else []
            # 舞台按"尚未记录的 verb"补位，与 stage_order 的顺序保持一致
            if token not in order:
                order.append(token)
            pending.op_order = ','.join(order)
            return
        order = pending.op_order.split(',') if pending.op_order else []
        if token not in order:
            order.append(token)
        pending.op_order = ','.join(order)

    for stmt, stmt_label in zip(statements, labels):
        if stmt_label:
            current_label = stmt_label
            pending.label = current_label
        kind = stmt.kind
        payload = stmt.payload or {}
        # 记下这条语句在**源文件里的行号**（1-based），供增量导出定位。
        # `Statement.source` 是 `(文件路径, 行号)`；没有就不记。
        try:
            _src = stmt.source
            _ln = int(_src[1]) if (_src and len(_src) > 1 and _src[1]) else 0
        except (TypeError, ValueError, IndexError):
            _ln = 0
        # **行号归属**（2026-10-05 修）：
        # 只有"真的被这一拍吸收"的语句才把行号记进 `pending`。
        # 以前是在这里**无条件**先记上，于是"处理到一半发现要落拍"的分支
        # （`scene` 撞 `scene`、`play` 撞 `play`、`voice` 撞 `voice`）
        # 会把**属于下一拍的那一行**算进上一拍：
        #
        #     31: pause 0.1
        #     32: scene bg bg3_1      ← 属于第 33 行那句台词那一拍
        #     33: "宿舍，食堂……"
        #
        # 实测（真实 chapter1_1）：第 18 拍的行区间变成 31-32、第 19 拍只剩
        # 33-33 —— 补丁按区间替换时把 32 行写成了上一拍的 `scene bg bg6_1`，
        # 而 `scene bg bg3_1` **无处可放**（产物里背景变成错的），
        # 整体校验发现后整章退回全量重生成 → **手写注释全丢**。
        absorbed = True
        #: 这一轮的语句是否已经把结构位标在了**自己的 Beat** 上
        #: （菜单拍 / 选项拍 / 跳转拍是直接构造的 —— 见循环末尾）
        struct_done = False

        if kind == StatementKind.SAY:
            speaker = payload.get('speaker', '') or ''
            pending.speaker = speaker
            pending.text = payload.get('text', '') or ''
            pending.kind = KIND_SAY if speaker else KIND_NARRATION
            pending.condition = stmt.condition or pending.condition
            seq += 1
            pending.seq = seq
            # 台词那一行属于**这张卡**（在落卡之前记，见循环末尾的行号归属）
            if _ln:
                if not pending.src_first:
                    pending.src_first = _ln
                pending.src_last = _ln
            _mark_struct(pending, stmt)
            beats.append(pending)
            pending = Beat(label=current_label)
            pending_has_stage = False
            absorbed = False             # 已经落在上面那张卡上了
            # 记住最后一个说话人，给紧随其后的 `extend` 用（见 EXTEND 分支）。
            # **旁白也算"最后一句"**：`"旁白。"` 之后跟 `extend` 续的是旁白，
            # 所以这里要把 last_speaker 清空，不能让它继承更早那个角色
            # （否则旁白续写会被安上说话人，把旁白变成台词）。
            last_speaker = speaker

        elif kind == StatementKind.EXTEND:
            seq += 1
            # `extend` 在 Ren'Py 里是**续上一句**（渲染成 `extend "…"`，
            # 不写说话人），但表里/卡片上必须让策划看出**这是谁在续说**——
            # 否则像 `#52 extend "铁汁我好像就是妹子啊…"` 这种看起来
            # 跟旁白一模一样，分不清是谁。用户 2026-09-28 报的"幽灵旁白"
            # 就是这个现象。
            #
            # 迁移（2026-10-05）：**它仍然是"这一拍"** —— 把 pending 里攒的
            # 舞台 / 代码 / 注释一起带走（与 SAY 同一条规矩）。否则那些行的
            # 行号会被**下一句台词**那一拍吞掉，于是两拍的行区间**交叠**
            # （实测真实 test.rpy：extend 拍 18-18 落在下一拍 16-23 里面），
            # 增量补丁遇到区间交叠只能跳过后拍 → 对位率掉到 71% → 整章退回
            # 全量重生成（注释全丢）。
            pending.seq = seq
            pending.kind = KIND_EXTEND
            pending.speaker = last_speaker
            pending.text = payload.get('text', '') or ''
            pending.condition = stmt.condition or pending.condition
            if _ln:
                if not pending.src_first:
                    pending.src_first = _ln
                pending.src_last = _ln
            _mark_struct(pending, stmt)
            beats.append(pending)
            pending = Beat(label=current_label)
            pending_has_stage = False
            absorbed = False             # 已经落在上面那张卡上了

        elif kind == StatementKind.MENU:
            caption = payload.get('caption', '') or ''
            absorbed = False             # 自成一张卡（`with_src` 已带上行号）
            # **无标题的 `menu:` 也要落一张卡**（2026-10-05 修 P0-1 的第一半）。
            # 以前只在 `if caption:` 时建拍，于是真实项目里 7 处裸 `menu:`
            # （策划最常见的写法）在拍这一层**没有任何痕迹**：后面那一串
            # `menu_option` 拍孤零零地排着，渲染层无从知道"这几条选项属于
            # 同一个菜单"，只能把选项当成裸旁白导出去：
            #
            #     "选项A"        ← 本该是 `menu:` 里的一行
            #     jump b         ← 本该缩进在"选项A"下面
            #     "选项B"
            #     jump c         ← 永远不可达
            #
            # 空 caption 用占位符承载（**不写进 Excel**，见 `MENU_NO_CAPTION`）
            # ——"有菜单"这个结构事实必须活到渲染那一刻。
            seq += 1
            _menu_beat = with_src(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_MENU,
                text=caption or MENU_NO_CAPTION,
            ), _ln)
            # 菜单拍**不算**选项体（`_menu_member=False`）：它是组头。
            # 结构位必须在这里显式写死，不能让循环末尾那趟"按当前语句"的
            # 转发覆盖 —— 当前语句恰好是"上一条选项体里的语句"时，
            # 组头会被标成 True，菜单组就从头断开了。
            _menu_beat._menu_member = False
            struct_done = True
            beats.append(_menu_beat)
            # 菜单把说话链打断了：选项之后的第一句该谁说是新语境
            last_speaker = ''

        elif kind == StatementKind.MENU_OPTION:
            seq += 1
            absorbed = False             # 自成一张卡（`with_src` 已带上行号）
            _opt = with_src(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_MENU_OPTION,
                text=payload.get('text', '') or '',
            ), _ln)
            # 同上：选项拍自己**不算**选项体，但它是菜单组的一员
            # （渲染层边界判据里 `menu_option` 那条分支优先于结构位）。
            _opt._menu_member = False
            struct_done = True
            beats.append(_opt)
            flush_stage_only()
            last_speaker = ''

        elif kind == StatementKind.SCENE:
            if pending.scene:
                # 同拍已有背景，新背景会覆盖它——先落拍，保留两条指令
                flush_stage_only()
            pending.scene = payload.get('image', '') or ''
            if payload.get('with_'):
                pending.with_ = payload['with_']
            if payload.get('at'):
                # 背景自己的变换（`scene bg bg9_5 at bgfit`）要落到 scene_at。
                # 落到 `at`（立绘位置）会同时错两处：背景的 at 丢失，
                # 而这一拍后面的 show 会莫名其妙被套上 bgfit。
                pending.scene_at = ' '.join(payload['at'])
            pending.condition = stmt.condition or pending.condition
            pending_has_stage = True
            mark_stage('scene')
            mark_op('scene')
            # 换景 = 换了说话语境，`extend` 的显示归属到此为止（见下）
            last_speaker = ''

        elif kind == StatementKind.SHOW:
            items = _split_show_items(pending.show)
            items.append({'clause': _show_item_text(payload),
                          'image': payload.get('image', '') or '',
                          'at': ''})
            pending.show = _join_show_items(items)
            if payload.get('with_') and not payload.get('at'):
                # 简单 show 的 with 提到拍级；复杂 show 的 with 已在子句里
                pending.with_ = payload['with_']
            pending.condition = stmt.condition or pending.condition
            pending_has_stage = True
            mark_stage('show')
            mark_op('show')

        elif kind == StatementKind.HIDE:
            items = _split_hide_items(pending.hide)
            items.append({'image': payload.get('image', '') or '',
                          'with': payload.get('with_', '') or ''})
            pending.hide = _join_hide_items(items)
            pending_has_stage = True
            mark_stage('hide')
            mark_op('hide')
            # 退场也算"说话人离开"：`A say → hide A → extend` 时，
            # 那句 extend 归属谁是不确定的（A 已经不在台上了）。
            # 宁可标成空（策划会去补），也不要安错人。
            last_speaker = ''

        elif kind == StatementKind.PLAY:
            channel = payload.get('channel', '')
            name = payload.get('name', '') or ''
            action = payload.get('action', 'play')
            params = _audio_params(payload)
            if channel == 'music':
                if pending.music or pending.music_action == 'stop':
                    # 同通道已有 play/stop，新指令会覆盖它——先落拍，
                    # 否则 play sound 紧跟 stop sound 时中间的 play 会丢
                    flush_stage_only()
                pending.music = name               # stop 时 name 为空
                pending.music_action = action      # 'stop' 如实存
                pending.music_params = params
                pending_has_stage = True
                mark_op('audio')
            else:
                if pending.sound or pending.sound_action == 'stop':
                    flush_stage_only()
                pending.sound = name
                pending.sound_action = action
                pending.sound_params = params
                pending_has_stage = True
                mark_op('audio')

        elif kind == StatementKind.VOICE:
            if pending.voice:
                flush_stage_only()
            pending.voice = payload.get('name', '') or ''
            pending_has_stage = True
            mark_op('voice')

        elif kind == StatementKind.PYTHON:
            # $ / python 逻辑进 CodeBlock，导出时原样回写，不再丢。
            code = (payload.get('code', '') or '').strip()
            if code:
                pending.code = _append_line(pending.code, code)
                pending.ordered.extend(('code', ln)
                                       for ln in _split_lines(code))
                pending_has_stage = True

        elif kind == StatementKind.COMMENT:
            text = (payload.get('text', '') or '').strip()
            if text:
                pending.comment = _append_line(pending.comment, text)
                pending_has_stage = True

        elif kind == StatementKind.RAW:
            text = (payload.get('text', '') or '').strip()
            if text:
                pending.raw = _append_line(pending.raw, text)
                pending.ordered.extend(('raw', ln)
                                       for ln in _split_lines(text))
                pending_has_stage = True

        elif kind in (StatementKind.JUMP, StatementKind.CALL):
            # 跳转/调用是控制流，进 Raw 原样保留，绝不丢。
            flush_stage_only()
            absorbed = False             # 自成一张卡（`with_src` 已带上行号）
            target = payload.get('target', '')
            seq += 1
            beats.append(_mark_struct(with_src(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_NARRATION,
                raw=f"{kind} {target}{_call_from_suffix(kind, payload)}",
            ), _ln), stmt))
            struct_done = True
            # 控制流走了，后面的 extend 不再属于任何已说话的 Beat
            last_speaker = ''

        elif kind == StatementKind.RETURN:
            flush_stage_only()
            absorbed = False             # `return` 不落成卡（只是章尾）
            # 一章结束：`extend` 不可能跨 label 续写，归属清零
            last_speaker = ''

        # **行号归属：只记"真被这一拍吸收"的那条**
        if _ln and absorbed:
            if not pending.src_first:
                pending.src_first = _ln
            pending.src_last = _ln
        # **结构位**：把"这一拍属于哪个菜单块"标在 `pending` 上。
        #
        # 这一位由**创建 pending 的那条语句**决定，而 `pending` 会在流程里
        # 被换掉（选项拍就是先 `flush_stage_only()` 再把 `pending` 换成新
        # Beat），所以：
        #   · 直接构造 Beat 的分支（菜单 / 选项 / 跳转）在 append 处就标好，
        #     并把 `struct_done` 置真 —— 这里**不能再标**，否则会被**下一条**
        #     语句的 `_menu_member=False`（选项拍的）覆盖成 False，
        #     整组块体都被判成"块外"（实测：credits 4 个选项丢 3 个）。
        #   · 其余分支（舞台 / 台词 / 代码 / 注释…）攒的是 `pending`，
        #     在这里标一次即可。
        #
        # `_mark_struct` 只在语句真的有 `_menu_member` 时才写 —— 手工构造的
        # Statement 没有这一位，Beat 上也不会凭空多出一个假的 `False`，
        # 渲染层因此能正确退回"按条件推断"。
        if not struct_done:
            _mark_struct(pending, stmt)

    flush_stage_only()
    _assign_identity(beats)
    # 标记"解析来的 Beat"：导出时图像名要逐字还原，不做下划线改写。
    for b in beats:
        b._from_script = True
    # 合成 DSL 文本：表格里看到的舞台/音频是"人话"，而不是拆散的六七列。
    # 这里没有符号表，所以合成的是原生写法（`show ling close_happy at left`）
    # ——它是无损的，编译时原样直通；打开项目后会用符号表重合成中文写法。
    for b in beats:
        # 结构化列（背景/立绘/位置/转场/退场）已经承载了主要舞台指令，
        # 「舞台（高级）」列只放结构化列表达不了的部分（复杂 show 子句、
        # 自带位置的立绘、带转场的退场）。否则同一句 show 会被结构化列
        # 和 Stage 列各输出一次，生成重复代码。
        b.stage = compose_stage_extra(b, None)
        b.bgm = compose_audio_text(b, 'music')
        b.sfx = compose_audio_text(b, 'sound')
    order_menu_bodies(beats)
    return beats


def _call_from_suffix(kind: str, payload: dict) -> str:
    """
    `call X from Y` 的 ` from Y` 尾巴（不是 call、或没有 from 就走空串）。

    `from` 是 Ren'Py 的**存档点标识**：同一个 label 被多处 call 时靠它区分
    存档属于哪一处。`pipeline/renpy_exporter` 对"控制流导出"那条路已经补过
    （见那里的 `_from_suffix`），这里是**章节导出**这条路的同一修。

    为什么必须修（2026-10-05 真实项目实测）：`chapter_controller.rpy` 里
    7 条 `call chapterN from _call_chapterN` 在 `rpy → xlsx → rpy` 往返后
    **全部退化成裸 `call`**。语义看着等价（照样跳得过去），代价是**老存档里的
    `_call_chapterN` 存档点全都找不到** —— 玩家读档直接落到别处。
    而这一位一直好好地存在 `Statement.payload['from_clause']` 里，
    只是拼 Raw 文本那行代码把它忘了。
    """
    if str(kind) != StatementKind.CALL:
        return ''
    clause = str(payload.get('from_clause', '') or '').strip()
    return f' from {clause}' if clause else ''


def order_menu_bodies(beats: list) -> list:
    """
    把菜单组里的拍**重排成"选项 → 它的块体"**（就地修改并返回同一个列表）。

    为什么需要（2026-10-05 实测真实项目 `credits_screen.rpy` 丢 2 个选项）：

    解析器吐语句的顺序与源码**物理顺序不一致**。`_parse_menu` 先把一个
    `menu:` 里的**全部选项头**收完，`_walk` 再逐个下钻它们的块体，于是

        选项"完整带图片版本"(605) → 选项"简化带图片版本"(608)
        → 块体 raw(606) → 选项(611) → 块体 raw(609) → …

    而"一拍一行"的落盘规则把块体挂在**当时攒着的那一拍**上，于是扁平序列
    变成 `option(605), option(608), body(606), option(611), …` ——
    选项头与块体交错。渲染时按"相邻"判块边界，第二个选项就被判成块外，
    `menu:` 块在那里截断，后面几个选项当成裸旁白导出去。

    归位规则（只用**渲染顺序**，不依赖源行号 —— Excel 回导之后行号是 0）：

        块体拍 → 挂到它前面最近的那张**选项拍**后面

    这正好同时满足两种形态：
      · 常见形态 `option, body, option, body` —— 块体前面最近的选项就是它
        自己的选项，重排后顺序不变；
      · credits 那种 `option, option, body, option, body` —— 块体被搬回
        它真正归属的那张选项后面。

    **只在菜单组内部搬**：菜单组之外的拍一个都不动（顺序是演出语义）。
    **就地**重排，调用方拿到的就是同一个列表对象。
    """
    n = len(beats)
    i = 0
    while i < n:
        if beats[i].kind != KIND_MENU:
            i += 1
            continue
        j = i + 1
        while j < n and beats[j].kind == KIND_MENU_OPTION:
            j += 1
        if j == i + 1:
            # 这个菜单一张选项拍都没有（`menu:` 后面直接跟块外内容）→ 不动
            i += 1
            continue
        # 组尾：从 j 起，直到出现下一张 menu 拍
        k = j
        while k < n and beats[k].kind != KIND_MENU:
            k += 1
        beats[i + 1:k] = _cluster_option_order(list(beats[i + 1:k]))
        i = k
    return beats


def _cluster_option_order(items: list) -> list:
    """
    菜单组内的拍 → "选项 + 它的块体"的顺序（`order_menu_bodies` 的实现）。

    输入形如 `[optA, optB, bodyA, optC, bodyB]`（选项头与块体交错），
    输出 `[optA, bodyA, optB, bodyB, optC]`。

    归位判据（按可靠性排序）：

      1. **源行号**（`.rpy` 解析来的拍有）：块体属于"行号在它之前、
         且离它最近"的那张选项拍。这就是源码里的归属，不用猜。
      2. **出场顺序兜底**（Excel 回导后行号是 0）：按"第 k 张块体归
         第 k 张选项"。

    为什么需要两种：`Beat.src_first/src_last` 是运行时元数据，不入表
    （增量导出要用，但 Excel 里没有这两列），所以回导之后只能靠顺序。
    """
    options = [it for it in items if it.kind == KIND_MENU_OPTION]
    if not options:
        return list(items)
    first_opt = next(i for i, it in enumerate(items)
                     if it.kind == KIND_MENU_OPTION)

    #: 第一张选项拍**之前**的拍：它们不属于任何选项，位置原样保留。
    #: （真实项目里这一段是"上一条 `queue sound` 攒下的那一拍"——
    #:   它被 `menu_option` 的 `flush_stage_only()` 落盘，出现在选项拍之前。
    #:   若把它当成块体归给第一个选项，它就会**跑到菜单后面**去，
    #:   块的顺序整段错位。）
    lead = list(items[:first_opt])
    body_items = list(items[first_opt:])
    options = [it for it in body_items if it.kind == KIND_MENU_OPTION]

    bodies = [it for it in body_items if it.kind != KIND_MENU_OPTION]
    lines = [int(getattr(o, 'src_last', 0) or 0) for o in options]
    use_lines = bool(bodies) and all(lines) and all(
        int(getattr(b, 'src_last', 0) or 0) for b in bodies)

    owners = {}                     # id(选项) -> [块体拍]
    leftover = []                   # 认不出归属的（绝不丢）
    if use_lines:
        for b in bodies:
            line = int(getattr(b, 'src_last', 0))
            best = None
            for idx, opt_line in enumerate(lines):
                if opt_line <= line:
                    best = idx
            if best is None:
                leftover.append(b)
            else:
                owners.setdefault(id(options[best]), []).append(b)
    else:
        # 顺序兜底：第 k 张块体归第 k 张选项；多余的留给最后一张
        for idx, b in enumerate(bodies):
            owner = options[idx] if idx < len(options) else options[-1]
            owners.setdefault(id(owner), []).append(b)

    out = list(lead)
    for opt in options:
        out.append(opt)
        out.extend(owners.get(id(opt), []))
    out.extend(leftover)
    return out



def hydrate_from_dsl(beats: list, symbols) -> list:
    """
    把「舞台（高级）」/ 「音乐」/「音效」列里的 DSL 文本解析成结构化字段。

    为什么分两步：单元格里是**人话或原生写法**，解析它需要符号表（角色
    显示名 → image tag、表情词 → 属性、资产 ID → 文件路径）。而
    `Beat.from_row` 读表时不该依赖资源表。所以：
      · 读表 → 结构化列直接落字段，DSL 原文先不动
      · 有资源表时 → hydrate 把 DSL 原文**追加**成字段

      "追加"而不是"替换"：结构化列与高级列是同一条流水线的两段输入，
      替换会把策划在下拉列里填的立绘吃掉。

    幂等：同一段原文只解释一次（`_hydrated_stage` 记着上次处理过的文本），
    否则 import → compile 两次 hydrate 会把立绘输出两遍。
    """
    from storygraph.story.dsl import (Audit, Symbols, parse_audio,
                                      parse_stage)

    symbols = symbols if isinstance(symbols, Symbols) else Symbols(symbols)
    audit = Audit(symbols)

    for beat in beats or ():
        if beat.stage and getattr(beat, '_hydrated_stage', None) != beat.stage:
            _hydrate_stage(beat, symbols, audit, parse_stage)
            try:
                beat._hydrated_stage = beat.stage
            except Exception as e:
                swallow('beat_build.hydrate_from_dsl', e)
        if beat.bgm:
            _hydrate_audio(beat, 'music', symbols, parse_audio)
        if beat.sfx:
            _hydrate_audio(beat, 'sound', symbols, parse_audio)
    return beats



def _hydrate_stage(beat, symbols, audit, parse_stage):
    """
    Stage DSL → beat.scene / show / hide / at / with_ / stage_order。

    原生写法的指令也要拆进结构化字段（舞台快照才看得见演员），但**不写回
    raw**——raw 属于代码块后门，同一行语句被输出两次就会生成重复代码。
    拆不动就忽略：`beat.stage` 原文还在，渲染时它会原样出去。

    同时记下**哪些结构化条目是"原生真迹的影子"**（见 `_absorb_native` 与
    `render_beat_to_rpy` 的舞台尾段）：作者在 Stage 列手写的原生指令，
    出脚本时要**逐字原样**出去（不做下划线改写），所以结构化列里那份
    不能再渲染一遍 —— 影子只给快照/校验用。
    """
    from storygraph.story.beat_model import hide_item_part, show_item_part
    from storygraph.story.dsl import compile_stage

    parsed = parse_stage(beat.stage)
    show_items, hide_items, order = [], [], []
    scene = None
    at = ''            # 立绘的位置
    scene_at = ''      # 背景自己的变换
    with_ = ''
    #: 原生真迹 → 它在结构化列里留下的影子（用于"只渲染真迹"）
    native_show, native_hide = set(), set()
    native_scene = None

    for op, (_intent, code, status) in zip(
            parsed.ops, compile_stage(beat.stage, symbols, audit)):
        if op.native:
            n_show, n_hide = len(show_items), len(hide_items)
            _absorb_native(op, show_items, hide_items, order)
            native_show.update(show_item_part(it)
                               for it in show_items[n_show:])
            native_hide.update(hide_item_part(it)
                               for it in hide_items[n_hide:])
            head = op.raw.split()[0] if op.raw.split() else ''
            if head == 'scene':
                spec, _at, _w = _parse_native_stage_tail(
                    op.raw[6:].strip())
                scene = spec or scene
                scene_at = _at or scene_at
                with_ = _w or with_
                if spec:
                    native_scene = (spec, _at or '')
            elif head == 'with' and not with_:
                parts = op.raw.split()
                with_ = parts[1] if len(parts) > 1 else ''
            continue

        if op.verb == 'show':
            if code.startswith('# TODO'):
                continue
            head = code.split(None, 1)[1] if ' ' in code else ''
            spec, _at, _w = _parse_native_stage_tail(head)
            if spec:
                show_items.append({'clause': '', 'image': spec,
                                   'at': _at or ''})
            order.append('show')
        elif op.verb == 'hide':
            if code.startswith('# TODO'):
                continue
            head = code.split(None, 1)[1] if ' ' in code else ''
            spec, _at, _w = _parse_native_stage_tail(head)
            if spec:
                hide_items.append({'image': spec, 'with': _w or ''})
            order.append('hide')
        elif op.verb == 'scene':
            if code.startswith('# TODO'):
                continue
            head = code.split(None, 1)[1] if ' ' in code else ''
            spec, _at, _w = _parse_native_stage_tail(head)
            scene = spec or scene
            scene_at = _at or scene_at
            with_ = _w or with_
            order.append('scene')
        elif op.verb == 'with':
            with_ = with_ or op.with_
        elif op.verb == 'pause':
            order.append('raw')

    # 追加到结构化列已经填好的内容后面（高级列是"补充通道"）
    if show_items:
        beat.show = _join_show_items(_split_show_items(beat.show)
                                     + show_items)
    if hide_items:
        beat.hide = _join_hide_items(_split_hide_items(beat.hide)
                                     + hide_items)
    if scene is not None:
        beat.scene = scene
    if at:
        beat.at = at
    if scene_at:
        beat.scene_at = scene_at
    if with_:
        beat.with_ = with_
    dedup = []
    for v in order:
        if v in ('scene', 'show', 'hide') and v not in dedup:
            dedup.append(v)
    if dedup:
        beat.stage_order = ','.join(dedup)

    # 记下"原生真迹"的影子：渲染时结构化列要跳过它们（真迹由 Stage 自己出）。
    # 放在最后而不是逐条赋值，是为了让"没原生指令"时也能把上一轮残留清掉。
    for attr, val in (('_stage_native_show', frozenset(native_show)),
                      ('_stage_native_hide', frozenset(native_hide)),
                      ('_stage_native_scene', native_scene)):
        try:
            setattr(beat, attr, val)
        except Exception:                    # noqa: BLE001
            swallow('beat_build._hydrate_stage')



def _absorb_native(op, show_items, hide_items, order):
    """
    原生舞台写法 → 结构化字段（供舞台快照/校验使用）。

    简单的 `show tag attr at 位置` 会折成紧凑形式（`tag attr@位置`），
    和"从语句流折叠"出来的写法保持同一种编码——否则同一张表里会出现
    两种 show 形态，后续比较、去重都要写两套。带 behind/as/onlayer/
    zorder/expression 的复杂子句原样保留。
    """
    head = op.raw.split()[0] if op.raw.split() else ''
    if head == 'show':
        rest = op.raw[len('show'):].strip()
        spec, at, with_ = _parse_native_stage_tail(rest)
        if spec and not with_:
            show_items.append({'clause': '', 'image': spec, 'at': at})
        else:
            for it in _split_show_items(rest):
                show_items.append(it)
        order.append('show')
    elif head == 'hide':
        rest = op.raw[len('hide'):].strip()
        spec, at, with_ = _parse_native_stage_tail(rest)
        if spec and not at:
            hide_items.append({'image': spec, 'with': with_})
        else:
            for it in _split_hide_items(rest):
                hide_items.append({'image': it['image'],
                                   'with': it.get('with', '')})
        order.append('hide')



def _hydrate_audio(beat, channel, symbols, parse_audio):
    from storygraph.story.dsl import compile_audio, TODO

    text = beat.bgm if channel == 'music' else beat.sfx
    ops = parse_audio(text, channel)
    compiled = compile_audio(text, channel, symbols)
    for op, (_intent, code, status) in zip(ops, compiled):
        if status == TODO or code.startswith('# TODO'):
            continue
        if code.startswith('stop '):
            action, name = 'stop', ''
        elif code.startswith('queue '):
            action, name = 'queue', op.name
        else:
            action, name = 'play', op.name
        # **参数只在 DSL 文本真的带了参数时才覆盖**
        # （2026-09-28 实测的数据丢失）：`[停止音乐]` 这类**伪值本身不含参数**，
        # 而参数住在旁边那一列（「音乐参数」= `fadeout 1.0`）。
        # 原来这里无条件 `op.params` 覆盖，于是把已经读出来的 fadeout 冲成空，
        # 真实章节 6 条 `stop music fadeout 1.0` 全变成裸 `stop music`。
        params = op.params
        if not params:
            params = (beat.music_params if channel == 'music'
                      else beat.sound_params) or ''
        if channel == 'music':
            beat.music, beat.music_params, beat.music_action = \
                name, params, action
        else:
            beat.sound, beat.sound_params, beat.sound_action = \
                name, params, action




def apply_script_definitions(registry, parse_result) -> dict:
    """
    把脚本里 `define` / `image` / `transform` 声明合并进资源表（内存）。

    这是"程序敲代码 → 表格跟着变"的核心一步：
      · `image bg bg1_1 = "..."` 声明 → 图像清单（`scene black`、
        `show bg bg1_1` 引用的名字就在这里，不合并就会被报"未登记"）
      · `transform bounce_quick:` → 变换清单（否则表格里选不到自定义变换）
      · `define x = DynamicCharacter(...)` → 角色清单

    合并策略分两种，因为"谁权威"本来就不同：

      · **新增**：表里没有的条目直接加进来；
      · **更新**：`transform` 的 `ATL` / `Params` / `Doc` 以**脚本为准**
        （脚本里定义了这个 transform，它的定义就是权威）。

    为什么要有第二种：原先对已登记条目一律"只补空缺"，于是**改了 .rpy 里的
    定义、表格永远不跟着变**；更糟的是，解析器历史上写坏的旧 ATL 会被永久
    固化在表里（2026-09-28 实测：`    #anchor(0.5, 0.5)` 被啃成
    `    hor(0.5, 0.5)` 落进 transforms.xlsx，解析器修好后产物依旧是错的）。

    其余字段不动：人写的 `Category` / `Notes` 保留；`image` / `character`
    仍"只补空缺"（显示名之类可能被人改过）。

    返回 `{'images': n, 'transforms': n, 'characters': n, 'updated': n}`：
    前三个是**新增**数量，`updated` 是**被脚本改写的字段数**（调用方据此判断
    "要不要落盘"——只更新时也得写）。
    """
    if registry is None or parse_result is None:
        return {'images': 0, 'transforms': 0, 'characters': 0, 'updated': 0}

    added = {'images': 0, 'transforms': 0, 'characters': 0}
    updated = 0

    # ---------- 图像声明 ----------
    for name, info in (getattr(parse_result, 'images', {}) or {}).items():
        info = info or {}
        if info.get('layered'):
            # 合成立绘：以 tag 本身为 ID，属性清单单独存，
            # 校验时按"tag 存在 + 属性都声明过"判定（见 validate）。
            if name in registry.images:
                row = registry.images[name]
                if info.get('attrs'):
                    row.setdefault('_attrs', list(info['attrs']))
            else:
                registry.images[name] = {
                    'ID': name,
                    'Path': info.get('expr') or '',
                    'Tag': name,
                    'Attributes': '',
                    'Category': 'character',
                    'Notes': _source_note(info),
                    '_layered': True,
                    '_attrs': list(info.get('attrs') or []),
                    '_from_script': True,
                }
                added['images'] += 1
            continue

        image_id = _image_id_from_name(name)
        if not image_id:
            continue
        tag, attrs = _split_image_name(name)
        row = {
            'ID': image_id,
            'Path': info.get('path') or '',
            'Tag': tag,
            'Attributes': attrs,
            'Category': _category_from_tag(tag),
            'Notes': _source_note(info),
            '_from_script': True,
        }
        if info.get('solid') and not row['Path']:
            row['Solid'] = info['solid']
        if image_id in registry.images:
            existing = registry.images[image_id]
            for key, val in row.items():
                if key.startswith('_'):
                    continue
                if not existing.get(key) and val:
                    existing[key] = val
        else:
            registry.images[image_id] = row
            added['images'] += 1

    # ---------- 变换 ----------
    for name, tf in (getattr(parse_result, 'transforms', {}) or {}).items():
        row = {
            'ID': name,
            'Path': '',
            'ATL': getattr(tf, 'body', ''),
            'Params': getattr(tf, 'params', ''),
            'Doc': getattr(tf, 'doc', ''),
            'Category': 'custom',
            'Notes': (f"{getattr(tf, 'source_file', '')}:"
                      f"{getattr(tf, 'source_line', 0)}"),
            '_from_script': True,
        }
        if name in registry.transforms:
            existing = registry.transforms[name]
            # 脚本是 ATL / Params / Doc 的权威来源：**值不同就更新**（见 docstring）
            for key in ('ATL', 'Params', 'Doc'):
                val = row.get(key)
                if val and existing.get(key) != val:
                    existing[key] = val
                    updated += 1
            existing.setdefault('_from_script', True)
        else:
            registry.transforms[name] = row
            added['transforms'] += 1

    # ---------- 角色 ----------
    for var, ch in (getattr(parse_result, 'characters', {}) or {}).items():
        row = {
            'ID': var,
            'Name': getattr(ch, 'display_name', '') or var,
            'Color': getattr(ch, 'color', ''),
            'WhatColor': getattr(ch, 'what_color', ''),
            'ImageTag': getattr(ch, 'image_tag', ''),
            'Kind': getattr(ch, 'kind', ''),
            'VoiceTag': getattr(ch, 'voice_tag', ''),
            'SideImage': getattr(ch, 'side_image', ''),
            'WhatPrefix': getattr(ch, 'what_prefix', ''),
            'WhatSuffix': getattr(ch, 'what_suffix', ''),
            'WhoPrefix': getattr(ch, 'who_prefix', ''),
            'WhoSuffix': getattr(ch, 'who_suffix', ''),
            'DefaultTransform': '',
            'Notes': (f"{getattr(ch, 'source_file', '')}:"
                      f"{getattr(ch, 'source_line', 0)}"),
            '_dialogue_count': getattr(ch, 'dialogue_count', 0),
            '_from_script': True,
        }
        if var in registry.characters:
            existing = registry.characters[var]
            for key, val in row.items():
                if key.startswith('_'):
                    if key == '_dialogue_count':
                        existing[key] = val
                    continue
                if not existing.get(key) and val:
                    existing[key] = val
        else:
            registry.characters[var] = row
            added['characters'] += 1

    added['updated'] = updated
    return added



def stage_only_ops(text: str, symbols=None, audit=None) -> str:
    """
    Stage 原文里**结构化列永远表达不了**的那些指令（`[停顿] 1.0` / 认不出的原生语句）。

    用途：编辑器重新投影 `beat.stage` 时，把这部分**原样留下**。
    能表达的（scene / show / hide / with）会由结构化列重新生成，
    表达不了的必须由 Stage 自己带着走 ——
    否则"打开卡片、点一下下一句"，作者手写的 `[停顿] 1.0` 就没了。

    判定按**编译后的动词**：`[停顿] 1.0` 编译成 `pause 1.0`，首词是
    `pause`，不在 `scene/show/hide/with` 里 → 留下。
    """
    text = str(text or '').strip()
    if not text:
        return ''
    try:
        from storygraph.story.dsl import compile_stage, parse_stage
        parsed = parse_stage(text)
        compiled = compile_stage(text, symbols, audit)
    except Exception:                        # noqa: BLE001 - 解析不了就整条留下
        return text
    keep = []
    for op, (_intent, code, _status) in zip(parsed.ops, compiled):
        head = str(code).split(None, 1)[0] if str(code).split() else ''
        if head in ('scene', 'show', 'hide', 'with'):
            continue
        line = str(code).strip()
        if line and line not in keep:
            keep.append(line)
    return '; '.join(keep)



def _source_note(info) -> str:
    info = info or {}
    src = f"{info.get('source_file', '')}:{info.get('source_line', 0)}"
    return src if info.get('source_file') else ''



def _image_id_from_name(name: str) -> str:
    """Ren'Py 图像名 → 资产 ID：空格转下划线。`bg bg1_1` → `bg_bg1_1`"""
    return '_'.join(str(name or '').split())



def _split_image_name(name: str) -> tuple:
    """`bg bg1_1` → ('bg', 'bg1_1')；单个词时 attrs 取同值。"""
    parts = str(name or '').split()
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], '_'.join(parts[1:])



def _category_from_tag(tag: str) -> str:
    tag = (tag or '').lower()
    if tag in ('bg', 'background'):
        return 'background'
    if tag in ('cg', 'event'):
        return 'cg'
    if tag in ('ui', 'gui'):
        return 'ui'
    return 'character'



def beats_from_parse_result(parse_result, label: str = '') -> list:
    """入口：ParseResult → Beat 列表。"""
    return beats_from_statements(
        parse_result.statements,
        label=label,
        labels=getattr(parse_result, 'statement_labels', None),
    )
