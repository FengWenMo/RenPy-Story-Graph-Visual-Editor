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
from typing import Iterable, Optional
from storygraph.core.statement import Statement, StatementKind

from storygraph.story.beat_model import (
    Beat,
    KIND_EXTEND,
    KIND_MENU,
    KIND_MENU_OPTION,
    KIND_NARRATION,
    KIND_SAY,
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
        """把只含舞台指令、没有台词的指令段单独落成一行。"""
        nonlocal pending, pending_has_stage, seq
        if pending_has_stage and not pending.text:
            seq += 1
            pending.seq = seq
            beats.append(pending)
        pending = Beat(label=current_label)
        pending_has_stage = False

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
        if _ln:
            if not pending.src_first:
                pending.src_first = _ln
            pending.src_last = _ln


        if kind == StatementKind.SAY:
            speaker = payload.get('speaker', '') or ''
            pending.speaker = speaker
            pending.text = payload.get('text', '') or ''
            pending.kind = KIND_SAY if speaker else KIND_NARRATION
            pending.condition = stmt.condition or pending.condition
            seq += 1
            pending.seq = seq
            beats.append(pending)
            pending = Beat(label=current_label)
            pending_has_stage = False
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
            # 只是在 Beat 上「显示用」地填 speaker，渲染仍然走 `extend`
            # 分支（见 render_beat_to_rpy），**不会**生成 `jc "…"`。
            beats.append(with_src(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_EXTEND,
                speaker=last_speaker,
                text=payload.get('text', '') or '',
            ), _ln))

        elif kind == StatementKind.MENU:
            caption = payload.get('caption', '') or ''
            if caption:
                seq += 1
                beats.append(with_src(Beat(
                    seq=seq, label=current_label,
                    condition=stmt.condition or '',
                    kind=KIND_MENU, text=caption,
                ), _ln))
                # 菜单把说话链打断了：选项之后的第一句该谁说是新语境
                last_speaker = ''

        elif kind == StatementKind.MENU_OPTION:
            seq += 1
            beats.append(with_src(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_MENU_OPTION,
                text=payload.get('text', '') or '',
            ), _ln))
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
            target = payload.get('target', '')
            seq += 1
            beats.append(with_src(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_NARRATION,
                raw=f"{kind} {target}",
            ), _ln))
            # 控制流走了，后面的 extend 不再属于任何已说话的 Beat
            last_speaker = ''

        elif kind == StatementKind.RETURN:
            flush_stage_only()
            # 一章结束：`extend` 不可能跨 label 续写，归属清零
            last_speaker = ''

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
    return beats



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
            except Exception:
                pass
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
            pass



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
