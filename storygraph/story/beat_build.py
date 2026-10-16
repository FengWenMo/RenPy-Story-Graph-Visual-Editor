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
    assign_identity,
    _audio_params,
    _join_at_targets,
    _join_hide_items,
    _join_show_items,
    _parse_native_stage_tail,
    _show_item_text,
    _split_hide_items,
    _split_lines,
    _split_show_items,
    _split_code_lines,
    compose_audio_text,
    compose_stage_extra,
)



def _relative_continuation_indents(lines: list) -> list:
    """
    多行语句每一行的**相对缩进**：首行 0，续行 = 原缩进 − 续行的最小缩进。

    多行语句（括号没收平）的续行里总有一行落在语句自身那一层（收尾的
    `])` / `)`），拿它当基准减掉，就得到"相对于语句"的缩进 —— 渲染时
    `_reindent` 铺回这几格，外层再补这一拍的层级（见 `_emit_raw`）。

    单行返回 `[0]`；续行全在更深一层时基准取最小值（最坏是丢一点对齐，
    但**每一轮得到同一个结果** = 幂等，对齐是次要的）。
    """
    if len(lines) < 2:
        return [0] * len(lines)
    indents = [len(ln) - len(ln.lstrip()) for ln in lines[1:] if ln.strip()]
    base = min(indents) if indents else 0
    out = [0]
    for ln in lines[1:]:
        if not ln.strip():
            out.append(0)
        else:
            out.append(max(0, (len(ln) - len(ln.lstrip())) - base))
    return out



def _dedent_continuations(text: str) -> str:
    """
    ⚠ **已废弃、没有调用方**（留着是为了记住"这条死路"）：

    曾经在**存储**时把续行的缩进去掉（存相对缩进），纯渲染幂等确实修好了，
    但**过表那张路串了结构** —— 格子里的原文一改，`_split_code_cell` 靠
    缩进/块标记分辨 raw 与 code 的判据就变了，`mini_game_dfinder.rpy`
    的 `label` 与 `if` 块整体串位（Excel 幂等审计当场红）。

    正解是**存原文不动、在渲染侧记相对缩进**（见
    `_relative_continuation_indents` 与 `_emit_raw`）。这个函数不再被调用，
    保留为一个"不要再犯"的路标。
    """
    lines = str(text or '').split('\n')
    if len(lines) < 2:
        return text
    indents = [len(ln) - len(ln.lstrip()) for ln in lines[1:] if ln.strip()]
    if not indents:
        return text
    base = min(indents)
    if not base:
        return text
    out = [lines[0]] + [(ln[base:] if ln.strip() else ln)
                        for ln in lines[1:]]
    return '\n'.join(out)



class _BeatBuilder:
    """
    把顺序语句流折叠成 Beat 序列的**状态机**（P3-12，2026-10-05）。

    为什么要有这个类
    ----------------
    `beats_from_statements` 原本是**一个 538 行的函数**，圈复杂度 101
    （全项目前二）。它里面 6 个闭包 + 主循环**共享 7 个可变状态**：

        seq / current_label / pending / pending_has_stage /
        last_speaker / absorbed / struct_done

    闭包版本最难读的地方不是长，而是**状态改在哪儿看不出来** ——
    `last_speaker = ''` 出现在 4 个分支里，`absorbed = False` 出现在 6 个，
    而它们是 `nonlocal`/外层局部，读代码时得来回跳。

    这一版**只做一件事**：把那 7 个状态从"函数局部变量 + 闭包"变成
    **实例属性**，把 6 个闭包变成**方法**，把主循环按语句种类分成
    `_emit_*` 方法。**逻辑一行没改** —— 每条注释、每个分支顺序、
    每个 `flush_stage_only()` 调用的位置都保持原样。

    验证方式（这一步是"改结构"不是"搬家"，所以网要更硬）：
    `tools/_verify_beats.py` 与 HEAD 版本**逐拍逐字段**比对
    24 个会影响产物的字段，427 例 / 8533 拍全一致。

    状态字段的用途
    --------------
      · `seq`               拍的递增序号（1 起，落盘时写进 `Beat.seq`）
      · `current_label`     当前语句所属 label（`pending.label` 跟着它）
      · `pending`           正在攒的那张拍（舞台/代码/注释都攒在这里）
      · `pending_has_stage` `pending` 里有没有"要落盘的内容"（空拍不落）
      · `last_speaker`      最后一个说出台词的人（给 `extend` 用）
      · `absorbed`          这一轮的语句是否**真被这张拍吸收**（行号归属）
      · `struct_done`       结构位是否已在本轮标过（防被下一条语句覆盖）
    """

    def __init__(self, label: str = ''):
        self.seq = 0
        self.current_label = label
        self.pending = Beat(label=label)
        self.pending_has_stage = False
        self.last_speaker = ''
        #: **分支边界**（2026-10-05 修 P1-1）：相邻两条语句条件不同 = 这里换分支。
        #:
        #: 用途见 `_branch_guard`：舞台指令要按条件各自成拍，而"分支最后一条
        #: 指令"挂在它下面的 `with` 过渡**不能提到拍级** —— 一提，同一个拍里
        #: 别的（块外的）指令就都会被套上这个过渡，而拍级 `with` 是按原文
        #: `with dissolve` 独立成行输出的，于是产物里凭空多出若干条
        #: `with dissolve`（实测 3 条凭空新增）。
        self.branch_edges: set = set()
        #: 落好的拍（`build()` 返回它）
        self.beats: list = []
        #: **源文件里出现过的 label（按出现顺序）** —— 用来给"一张拍都没
        #: 落到它名下"的 label 补空拍（见 `_fill_empty_labels`）。
        self.label_order: list = []

    # ------------------------------------------------------------------
    #  状态操作（原来是闭包）
    # ------------------------------------------------------------------

    def note_branch_edges(self, statements: list) -> None:
        """记下"相邻两条语句条件不同"的前一条 —— 见 `branch_edges` 的说明。"""
        for i in range(1, len(statements)):
            prev = (statements[i - 1].condition or '').strip()
            cur = (statements[i].condition or '').strip()
            if prev != cur:
                self.branch_edges.add(id(statements[i - 1]))

    def flush_stage_only(self) -> None:
        """
        把只含舞台指令、没有台词的指令段单独落成一行。

        **不在这里标结构位**：这一拍的 `_menu_member` 由"把它攒起来的那些
        语句"决定，而那些语句在处理时就已标过（见 `add()` 末尾）。若在这里按
        **当前**语句再标一次，就会拿"下一条语句"的值覆盖它 —— 实测
        `menu_option` 分支正是先 `flush_stage_only()` 再 append，
        于是选项体的拍被标成 `False`（选项拍的值），菜单组整块散架。
        """
        if self.pending_has_stage and not self.pending.text:
            self.seq += 1
            self.pending.seq = self.seq
            self.beats.append(self.pending)
        self.pending = Beat(label=self.current_label)
        self.pending_has_stage = False

    def struct_guard(self, stmt) -> None:
        """
        **结构守卫**：这条语句的**块路径**与当前拍不一致时，先把当前拍落下去。

        为什么必须这样（2026-10-05，`tools/probe_body_leak.py` 实测）：
        `branch_guard` 只比**条件**，而"条件相同、位置不同"的语句照样会被并进
        同一拍 —— 一张拍只有一个结构位，而 `mark_struct` 写的是**最后那条**
        语句的路径：

            label a:
                menu:
                    "选项":
                        show bg room      ← 路径 `menu:1>A`（选中才播）
                e "体"                    ← 路径 `''`
                return

        两者条件都是空串 → 并成一拍 → 结构位被覆盖成 `''` → 渲染时
        `show bg room` **漏出菜单、变成无条件执行**（选没选都会换背景），
        选项体里只剩一句 `pass`。这是**静默改剧情**，而且对拍器看不见：
        `_menu_member` 与 `Nest` 两份载体都被最后那条语句覆盖成同一个谎，
        于是"两边自洽"。

        空选项体补的 `pass` 是同一根因最显眼的形态：那条 `pass` 在体内，
        却会被并进菜单外那张拍 → 产物里**多出一个顶层 `pass`**
        （"第二轮 ≠ 第一轮"的来路之一）。

        `None`（不知道 —— 手工构造的语句 / 老表）**不比**：与对拍器同一口径，
        绝不拿"不知道"当"顶层"。
        """
        incoming = getattr(stmt, 'nest_path', None)
        current = getattr(self.pending, 'nest_path', None)
        if incoming is None or current is None:
            return
        if incoming != current:
            self.flush_stage_only()

    def branch_guard(self, stmt) -> None:
        """
        **分支守卫**：舞台指令的条件与当前拍不一致时，先把当前拍落下去。

        为什么必须这样（2026-10-05 修 P1-1，实测 `script_pomo.rpy`）：

            if pomoplace == "beach":
                show bgocean with dissolve
            elif pomoplace == "cafe":
                show bgcafe with dissolve
            elif pomoplace == "library":
                show bglibrary with dissolve
            show qing base at h11

        解析器**明明给每条 `show` 都带了条件**，但下面那些分支里写的是
        `pending.condition = stmt.condition or pending.condition` ——
        三条舞台指令被吸进**同一张拍**，而一张拍只有一个 `condition`，
        于是：

          · 第一个条件被后两个覆盖（`or` 链只留最后一个真值）；
          · 块外那条 `show qing base`（条件是空串）反倒继承了
            残留的分支条件。

        产物变成"三个背景依次无条件 show + `qing base` 也被 `if` 包住"——
        最后一个背景永远生效（玩家永远看到 library），且**不报任何错**。

        修法与 menu 同源：**扁平拍 + 按条件归组**。条件不同的舞台指令
        各自成拍，渲染层就能给每条套上自己的 `if`。
        条件相同（同一分支里的连续几条指令）继续合并在同一拍，
        不改变原有产物形状。
        """
        incoming = (stmt.condition or '').strip()
        current = (self.pending.condition or '').strip()
        if incoming != current:
            self.flush_stage_only()

    @staticmethod
    def mark_struct(beat, stmt):
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
        # **结构位（块路径）**也一起转发（B 阶段一）：`_menu_member` 只答
        # "在不在选项体里"，它答"在**哪个**选项体里、套了几层" ——
        # 而后者才是能随表走、能对拍的那一份。`''` 是合法值（label 顶层），
        # 所以判据是"语句上有没有这一位"（`None` = 手工构造的语句 = 不知道）。
        if getattr(stmt, 'nest_path', None) is not None:
            beat.nest_path = stmt.nest_path
        # 行尾注释（C）同样转发；空串是合法值（这一行本来就没注释）
        beat.inline_comment = getattr(stmt, 'inline_comment', '') or ''
        beat.cond_comment = getattr(stmt, 'cond_comment', '') or ''
        return beat

    def mark_stage(self, verb: str, index: int = 0) -> None:
        """
        记录舞台指令在本拍中的出现顺序，供导出时保序。

        ⚠ **token 是"逐条"的：`show#0,hide#0,show#1`**（2026-10-07 修缺口 4）。

        从前这里存的是**verb 名**且**同名去重**（`if verb not in order`），
        于是顺序信息只有"哪些 verb、谁先谁后"，**同名 verb 无法表达两条**：

            show A / hide B / show C
            旧 token：show,hide          ← 第三条 show 根本没记
            旧产物  ：show A / show C / hide B   ← 渲染器只能按 verb 分组

        换成 `verb#<这一拍里第几条>` 之后，渲染器可以**逐条**还原，顺序与
        条数都能保住（`show A / hide A / show A` 那种"收尾画面反了"的
        形状就是被旧写法吃掉的）。
        """
        order = (self.pending.stage_order.split(',')
                 if self.pending.stage_order else [])
        token = f'{verb}#{int(index)}'
        if token not in order:
            order.append(token)
        self.pending.stage_order = ','.join(order)

    def stage_tokens(self, verb: str) -> list:
        """这一拍 `stage_order` 里某个 verb 已经记了几条（解析 token，含老格式）。"""
        out = []
        for tok in (self.pending.stage_order or '').split(','):
            tok = tok.strip()
            if not tok:
                continue
            head = tok.split('#', 1)[0]
            if head == verb:
                out.append(tok)
        return out

    @staticmethod
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

    def mark_op(self, token: str, index: int = None) -> None:
        """
        记录操作在本拍中的**逐条**出现顺序，供导出时逐条还原。

        存的是 `verb#<第几条>`（`show#0` / `hide#1` / `audio` …）——
        舞台 verb 走 `mark_stage` 同一套编号（同名 verb 的第几条）。

        **为什么必须逐条**：只记 verb 名时，同一拍里两条 `show` 之间夹着一条
        `hide` 就没法表达，渲染只能按 verb 分组输出，于是

            show A / hide B / show C   →   show A / show C / hide B

        演出顺序被改；而 `show A / hide A / show A`（作者要"最后显示"）会被
        去重成 `show A / hide A` —— **收尾画面反了**（玩家看到的画面不同）。

        audio / voice 同类只记一次：同一拍里两条 `play sound` 已经被 flush
        拆成了两拍（见 PLAY 分支），所以不需要计数。
        """
        token = str(token or '')
        if not token:
            return
        if index is not None:
            token = f'{token}#{int(index)}'
        order = (self.pending.op_order.split(',')
                 if self.pending.op_order else [])
        if token not in order:
            order.append(token)
        self.pending.op_order = ','.join(order)

    # ------------------------------------------------------------------
    #  主入口：一条语句
    # ------------------------------------------------------------------

    @staticmethod
    def source_line_of(stmt) -> int:
        """语句在源文件里的行号（1-based）；拿不到就是 0。"""
        try:
            src = stmt.source
            return int(src[1]) if (src and len(src) > 1 and src[1]) else 0
        except (TypeError, ValueError, IndexError):
            return 0

    def add(self, stmt, stmt_label) -> None:
        """处理一条语句（原来是那个 538 行 for 循环的循环体）。"""
        # **label 是硬边界**（2026-10-05 修，真实项目实测）。
        #
        # 攒着的"孤儿指令段"（`$ x` / `show screen` / `pause` / `play music`
        # 这些**没有台词**的语句）在换 label 时必须先落拍，否则它们会被吸进
        # **下一个 label 的第一张拍**里 —— 本 label 名下一张拍都没有，
        # 渲染时按拍分组 → 产物里**整个 label 消失**，且不报任何错。
        #
        # 真实项目实测（`tools/probe_label_fate_real.py`）：
        # 51 个 label 里丢 4 个 —— beginstory / location / pomo_start /
        # sr_about_render_test。其中 `location` 带 `call screen map`，
        # 是主循环节点；产物里没有它，所有 `jump location` 全部失效。
        #
        # `flush_stage_only()` 在这里是安全的：pending 空的时候它只重置、
        # 不落拍（见它的 `pending_has_stage` 判据），所以不会凭空多出空拍。
        if stmt_label and stmt_label != self.current_label:
            self.flush_stage_only()
        if stmt_label:
            self.current_label = stmt_label
            self.pending.label = self.current_label
            if stmt_label not in self.label_order:
                self.label_order.append(stmt_label)
        kind = stmt.kind
        payload = stmt.payload or {}
        _ln = self.source_line_of(stmt)

        # **结构守卫**（2026-10-05，见 `struct_guard` 的说明）：
        # 位置不同的语句不许并进同一拍 —— 否则整拍的结构位会被最后那条语句
        # 覆盖，"在选项体里"的内容会漏到菜单外**无条件执行**。
        self.struct_guard(stmt)

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
        #: （菜单拍 / 选项拍 / 跳转拍是直接构造的）
        struct_done = False

        handler = {
            StatementKind.SAY: self._emit_say,
            StatementKind.EXTEND: self._emit_extend,
            StatementKind.MENU: self._emit_menu,
            StatementKind.MENU_OPTION: self._emit_menu_option,
            StatementKind.SCENE: self._emit_scene,
            StatementKind.SHOW: self._emit_show,
            StatementKind.HIDE: self._emit_hide,
            StatementKind.PLAY: self._emit_play,
            StatementKind.VOICE: self._emit_voice,
            StatementKind.PYTHON: self._emit_python,
            StatementKind.COMMENT: self._emit_comment,
            StatementKind.RAW: self._emit_raw,
            StatementKind.LOOP: self._emit_loop,
            # `camera`（3D 舞台）与循环块**同一档、同一套约定**：头行进
            # 可编辑列、整块原文进 `opaque`（见 `CameraNode`）。
            StatementKind.CAMERA: self._emit_camera,
            StatementKind.JUMP: self._emit_jump_call,
            StatementKind.CALL: self._emit_jump_call,
            StatementKind.RETURN: self._emit_return,
        }.get(kind)
        _n_before = len(self.beats)
        if handler is not None:
            absorbed, struct_done = handler(stmt, payload, _ln)

        # **行号归属：只记"真被这一拍吸收"的那条**
        if _ln and absorbed:
            if not self.pending.src_first:
                self.pending.src_first = _ln
            self.pending.src_last = _ln
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
        # `mark_struct` 只在语句真的有 `_menu_member` 时才写 —— 手工构造的
        # Statement 没有这一位，Beat 上也不会凭空多出一个假的 `False`，
        # 渲染层因此能正确退回"按条件推断"。
        if not struct_done:
            self.mark_struct(self.pending, stmt)

    # ------------------------------------------------------------------
    #  各 kind 的分支（原来在 for 循环里）
    # ------------------------------------------------------------------

    def _emit_say(self, stmt, payload, _ln):
        # 这一拍要落卡了，先把"条件对不上的舞台指令"单独落成自己的拍。
        #
        # 为什么必须（2026-10-05 修 P1-1 的第二半，实测 `script_pomo.rpy`
        # 第 194/197 行）：
        #
        #     194:    show qing base at h11      ← 块外，无条件
        #     196:    if breakline == 1:
        #     197:        q "时间到了……"         ← 条件 breakline == 1
        #
        # `pending` 里攒着无条件的 `show qing base`，台词带条件 ——
        # 下面那句 `pending.condition = stmt.condition or pending.condition`
        # 会把**台词的条件**盖到整张拍上，于是块外的立绘跟着台词一起
        # 被 `if breakline == 1:` 包住：只有 `breakline == 1`（三分之一
        # 概率）时立绘才出现，而原文里它无条件执行。
        #
        # 条件**相同**（同一分支内的舞台+台词）照旧合并在同一拍，
        # 不改变产物形状。
        pending = self.pending
        if (stmt.condition or '').strip() != (pending.condition or '').strip():
            self.flush_stage_only()
            pending = self.pending
        speaker = payload.get('speaker', '') or ''
        pending.speaker = speaker
        pending.text = payload.get('text', '') or ''
        pending.kind = KIND_SAY if speaker else KIND_NARRATION
        pending.condition = stmt.condition or pending.condition
        self.seq += 1
        pending.seq = self.seq
        # 台词那一行属于**这张卡**（在落卡之前记，见 `add()` 的行号归属）
        if _ln:
            if not pending.src_first:
                pending.src_first = _ln
            pending.src_last = _ln
        self.mark_struct(pending, stmt)
        self.beats.append(pending)
        self.pending = Beat(label=self.current_label)
        self.pending_has_stage = False
        # 记住最后一个说话人，给紧随其后的 `extend` 用（见 EXTEND 分支）。
        # **旁白也算"最后一句"**：`"旁白。"` 之后跟 `extend` 续的是旁白，
        # 所以这里要把 last_speaker 清空，不能让它继承更早那个角色
        # （否则旁白续写会被安上说话人，把旁白变成台词）。
        self.last_speaker = speaker
        return False, False             # 已经落在上面那张卡上了

    def _emit_extend(self, stmt, payload, _ln):
        self.seq += 1
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
        pending = self.pending
        pending.seq = self.seq
        pending.kind = KIND_EXTEND
        pending.speaker = self.last_speaker
        pending.text = payload.get('text', '') or ''
        pending.condition = stmt.condition or pending.condition
        if _ln:
            if not pending.src_first:
                pending.src_first = _ln
            pending.src_last = _ln
        self.mark_struct(pending, stmt)
        self.beats.append(pending)
        self.pending = Beat(label=self.current_label)
        self.pending_has_stage = False
        return False, False             # 已经落在上面那张卡上了

    def _emit_menu(self, stmt, payload, _ln):
        caption = payload.get('caption', '') or ''
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
        # **菜单拍/选项拍直接落盘，所以落盘前必须先把 `pending` 里攒的
        # 舞台指令放出来**（2026-10-05，brutal test 的 F1 抓到）。
        #
        # 以前这里直接 `beats.append(_menu_beat)`，`pending` 原封不动 ——
        # 于是那一拍会一直攒到章尾才落，后果有两个：
        #
        #   1. 拍的 label 顺序变成 `g0, g1, g0`，而渲染层按"相邻同 label
        #      分组"，产物里出现**两个 `label g0:`**（重复定义，语法错误）；
        #   2. 它的 `src_last` 被 menu / menu_option 的行号**顶到后面**
        #      （行号是"被这一拍吸收的语句"记的），而
        #      `order_menu_bodies` 正是按行号把块体归给选项 ——
        #      于是它被判成"认不出属于哪个选项"，搬到菜单组末尾。
        #
        # 真实形态：`hide bg cafe` 紧跟着 `menu:`（源码里它在菜单**之前**、
        # 无条件执行）。修好之后它回到菜单前面，也不再需要那句警告。
        self.flush_stage_only()
        self.seq += 1
        menu_beat = self.with_src(Beat(
            seq=self.seq, label=self.current_label,
            condition=stmt.condition or '',
            kind=KIND_MENU,
            text=caption or MENU_NO_CAPTION,
        ), _ln)
        # 菜单拍**不算**选项体（`_menu_member=False`）：它是组头。
        # 结构位必须在这里显式写死，不能让循环末尾那趟"按当前语句"的
        # 转发覆盖 —— 当前语句恰好是"上一条选项体里的语句"时，
        # 组头会被标成 True，菜单组就从头断开了。
        menu_beat._menu_member = False
        # 菜单头在**父块**里（组头不属于任何选项体）
        menu_beat.nest_path = getattr(stmt, 'nest_path', None)
        menu_beat.inline_comment = getattr(stmt, 'inline_comment', '') or ''
        self.beats.append(menu_beat)
        # 菜单把说话链打断了：选项之后的第一句该谁说是新语境
        self.last_speaker = ''
        return False, True

    def _emit_menu_option(self, stmt, payload, _ln):
        # 同上：选项拍也是直接落盘，落盘前先放掉 `pending`。
        self.flush_stage_only()
        self.seq += 1
        opt = self.with_src(Beat(
            seq=self.seq, label=self.current_label,
            condition=stmt.condition or '',
            kind=KIND_MENU_OPTION,
            text=payload.get('text', '') or '',
        ), _ln)
        # 同上：选项拍自己**不算**选项体，但它是菜单组的一员
        # （渲染层边界判据里 `menu_option` 那条分支优先于结构位）。
        opt._menu_member = False
        # 选项行在**菜单里、不在任何选项体里**（解析器给的路径到菜单为止）
        opt.nest_path = getattr(stmt, 'nest_path', None)
        opt.inline_comment = getattr(stmt, 'inline_comment', '') or ''
        self.beats.append(opt)
        self.flush_stage_only()
        self.last_speaker = ''
        return False, True

    def _emit_scene(self, stmt, payload, _ln):
        self.branch_guard(stmt)
        pending = self.pending
        if pending.scene:
            # 同拍已有背景，新背景会覆盖它——先落拍，保留两条指令
            self.flush_stage_only()
            pending = self.pending
        pending.scene = payload.get('image', '') or ''
        if payload.get('with_'):
            pending.with_ = payload['with_']
        if payload.get('at'):
            # 背景自己的变换（`scene bg bg9_5 at bgfit`）要落到 scene_at。
            # 落到 `at`（立绘位置）会同时错两处：背景的 at 丢失，
            # 而这一拍后面的 show 会莫名其妙被套上 bgfit。
            pending.scene_at = _join_at_targets(payload['at'])
        pending.condition = stmt.condition or pending.condition
        self.pending_has_stage = True
        self.mark_stage('scene', 0)
        self.mark_op('scene', 0)
        # 换景 = 换了说话语境，`extend` 的显示归属到此为止（见下）
        self.last_speaker = ''
        return True, False

    def _emit_show(self, stmt, payload, _ln):
        self.branch_guard(stmt)
        pending = self.pending
        items = _split_show_items(pending.show)
        items.append({'clause': _show_item_text(payload),
                      'image': payload.get('image', '') or '',
                      'at': ''})
        # **作者写两遍就得留两遍**（`dedup=False`，2026-10-07 修缺口 4）：
        # 按内容去重会在 `show A / hide A / show A` 里吃掉最后那条 show
        # —— **收尾画面与作者要的相反**。影子重复由 hydrate 那条路自己挡
        # （见 `hydrate_from_dsl` 的 `dedup=True`）。
        pending.show = _join_show_items(items, dedup=False)
        if payload.get('with_') and not payload.get('at') \
                and id(stmt) not in self.branch_edges:
            # 简单 show 的 with 提到拍级；复杂 show 的 with 已在子句里。
            # **分支最后一条**不提（见 `branch_edges`）：那会让块外的
            # 指令也被套上这个过渡，并让产物多出独立的 `with` 行。
            pending.with_ = payload['with_']
        pending.condition = stmt.condition or pending.condition
        self.pending_has_stage = True
        self.mark_stage('show', len(items) - 1)
        self.mark_op('show', len(items) - 1)
        return True, False

    def _emit_hide(self, stmt, payload, _ln):
        self.branch_guard(stmt)
        pending = self.pending
        items = _split_hide_items(pending.hide)
        items.append({'image': payload.get('image', '') or '',
                      'with': payload.get('with_', '') or ''})
        pending.hide = _join_hide_items(items, dedup=False)
        pending.condition = stmt.condition or pending.condition
        self.pending_has_stage = True
        self.mark_stage('hide', len(items) - 1)
        self.mark_op('hide', len(items) - 1)
        # 退场也算"说话人离开"：`A say → hide A → extend` 时，
        # 那句 extend 归属谁是不确定的（A 已经不在台上了）。
        # 宁可标成空（策划会去补），也不要安错人。
        self.last_speaker = ''
        return True, False

    def _emit_play(self, stmt, payload, _ln):
        self.branch_guard(stmt)
        channel = payload.get('channel', '')
        name = payload.get('name', '') or ''
        action = payload.get('action', 'play')
        params = _audio_params(payload)
        pending = self.pending
        if channel == 'music':
            if pending.music or pending.music_action == 'stop':
                # 同通道已有 play/stop，新指令会覆盖它——先落拍，
                # 否则 play sound 紧跟 stop sound 时中间的 play 会丢
                self.flush_stage_only()
                pending = self.pending
            pending.music = name               # stop 时 name 为空
            pending.music_action = action      # 'stop' 如实存
            pending.music_params = params
        else:
            if pending.sound or pending.sound_action == 'stop':
                self.flush_stage_only()
                pending = self.pending
            pending.sound = name
            pending.sound_action = action
            pending.sound_params = params
        self.pending_has_stage = True
        self.mark_op('audio')
        pending.condition = stmt.condition or pending.condition
        return True, False

    def _emit_voice(self, stmt, payload, _ln):
        self.branch_guard(stmt)
        if self.pending.voice:
            self.flush_stage_only()
        pending = self.pending
        pending.voice = payload.get('name', '') or ''
        pending.condition = stmt.condition or pending.condition
        self.pending_has_stage = True
        self.mark_op('voice')
        return True, False

    def _emit_python(self, stmt, payload, _ln):
        # `$` / `python:` 逻辑进 CodeBlock，导出时原样回写，不再丢。
        code = (payload.get('code', '') or '').strip()
        if not code:
            return True, False
        # 条件不同就不能合并：一拍只有一个条件，合并会让
        # `if A: $ x` / `elif B: $ y` 里 `y` 的代码在 `A` 不成立时
        # 也执行（与舞台指令同一个根因，见 `branch_guard`）。
        self.branch_guard(stmt)
        pending = self.pending
        pending.condition = stmt.condition or pending.condition
        # **保留空行**：Python 里空行没有语义，但它出现在多行语句的
        # 参数列表中间时（`drag_puzzle(` 里就有），丢了会让往返
        # 前后的代码指纹对不上（审计算一条丢失 + 一条新增）。
        lines = _split_code_lines(code)
        start = len(pending.ordered) + 1        # 1-based 行号
        # ⚠ 令牌里的下标是**代码池里的第几条**（`_split_lines(pending.code)`，
        # **不含空行**）—— 与渲染侧 `_ordered_code_lines` 用的池子同一个口径。
        # 用 `start`（ordered 里的位置，含 raw 行）当基数会整体错位（第一版
        # 就是这么错的：`show screen` 被吞进 python 块、`window hide` 消失）。
        before = len(_split_lines(pending.code))
        pending.code = _append_line(pending.code, code)
        pending.ordered.extend(('code', ln) for ln in lines)
        for i in range(len(_split_lines(code))):
            self.mark_op('code', before + i)
        if len(lines) > 1:
            # **多行** = 它来自 `python:` 块（单行 `$ x` 只占一行）。
            # 记下它在格子里的行区间，写进隐藏列 `CodeBlocks` ——
            # 一个格子里混着多条语句时，块的边界在纯文本里表达不出来
            # （见 `BEAT_FIELDS` 里 `CodeBlocks` 的说明）。
            pending.code_blocks.append((start, start + len(lines) - 1))
        self.pending_has_stage = True
        return True, False

    def _emit_comment(self, stmt, payload, _ln):
        """
        注释进「注释」列（`Beat.comment`），导出时原样回写。

        ⚠ **注释也要过 `branch_guard`**（2026-10-05 修，真实项目
        `mini_game1.rpy` 第 116-119 行实测）。从前这里既不比条件、也不落拍，
        于是一条**顶层**注释会被吸进**上一个条件拍**的 pending 里，后果两条：

          · 那一拍的 `nest_path` 被注释的路径**覆盖**（条件还是分支的、
            结构位却成了顶层）—— 隐藏列 `Nest` 说的位置就不真了；
          · 渲染时注释写在该拍 `if …:` 的**前面**（注释永远排第一），
            位置从"分支之后"漂到"分支之前"；产物再解析时，
            `branch_guard` 会因为"注释无条件、后面的分支语句有条件"把这一拍
            **拆成两拍** —— N1 因此报"多一条结构位"。

        真实形态：

             116:        python:            ← 分支体（cond = fail_round>=3）
             117:            fsflag = True
             118:    # 生成选项（1个正确，3个错误）   ← 顶层注释
             119:    python:            ← 下一条顶层语句

        不落拍的后果：`$ fsflag = True` 与那条顶层注释并成**同一拍**，
        注释被搬到分支之前。落拍之后两者各归各位，产物与原文同序。
        """
        text = (payload.get('text', '') or '').strip()
        if not text:
            return True, False
        self.branch_guard(stmt)
        # ⚠⚠ **条件也要转发到拍上**（2026-10-15 修，真机 `mini_game_flipcard.rpy`
        #    第 395-396 行实测）。`branch_guard` 只负责"条件不同就落拍、开新拍"，
        #    它**不写条件** —— 而别的发射器（舞台/透传）紧接着都会写
        #    `pending.condition = stmt.condition or pending.condition`，只有这里漏了。
        #
        # 漏掉的后果（**注释漂出分支**）：分支内的注释会成一张
        # `condition=''` 而 `nest_path='if:2'` 的孤立拍 ——
        # 结构位说"它在 if 里"，条件却是空的。渲染时
        # `render_beat_to_rpy` 把注释无条件放在**该拍最前面**（`pre`），
        # 而这一拍排在 if 那拍**之前** → 注释被**提到 `if` 之前、
        # 缩进也从 8 掉到 4**（分支归属当场丢失）。
        #
        # 更糟的是它**一轮之内不稳定**：产物里注释已经在 `if` 前面了，
        # 再解析时它归属到**上一拍**，于是第 2 轮又换一个位置 ——
        # 真机 `audit_idempotence` 因此报 `mini_game_flipcard.rpy` 不幂等
        # （第 410/411 行两行互换）。
        #
        # 补上条件之后，这条注释与**分支体条件相同** → 紧接着的分支体语句
        # 不会再触发 `branch_guard` 落拍 → 二者并进**同一拍** →
        # 渲染出来正是 `if …:` + 体内的注释 + 体内的代码（与原文同序同缩进）。
        self.pending.condition = stmt.condition or self.pending.condition
        self.pending.comment = _append_line(self.pending.comment, text)
        self.pending_has_stage = True
        return True, False

    def _emit_raw(self, stmt, payload, _ln):
        text = (payload.get('text', '') or '').strip()
        if not text:
            return True, False
        # **分支守卫**（与舞台指令同源，2026-10-05 实测补上）：
        # 透传语句（`pause 0.1` / `show screen` / `window hide` …）
        # 也有条件 —— 以前这里既不看条件、也不落条件，于是
        #
        #     if flag:
        #         pause 0.1
        #     "一句"
        #
        # 里那句 `pause 0.1` 被吸进**后面那张无条件拍**，
        # 产物变成 `pause 0.1` 无条件执行、`if flag:` 整个消失。
        # 真实项目 81 条 unmodeled 里相当一部分是这种形状。
        self.branch_guard(stmt)
        # **工具不认识的块语句 → 单独的保真列**（M1，2026-10-07）。
        #
        # 为什么不走上面那条 raw/CodeBlock 通道：「代码」列的渲染会**推断**
        # （认不出的行补 `$`、整块还可能被包成 `python:`），而对一段工具
        # 看不懂的自定义块，任何推断都是改剧情：
        #
        #     mycue "闪电" intensity=3:
        #         target = ling          ← 补 `$` 就成了 `$ target = ling`
        #
        # `Beat.opaque` 的契约只有一句：**逐字写回**。
        # 先 `flush_stage_only()` 让它**独占一张拍**：否则它会和同一张拍里
        # 攒着的舞台/代码指令混在一起，渲染时的相对顺序就没有依据了
        # （它自己在源码里的位置恰恰是"两句台词之间"，这个位置有意义）。
        # **被声明过的自定义语句也走这一列**（M3，2026-10-07）：
        # 叶形态（`mycue "x" intensity=3`）如果留在 raw 通道，会被
        # `render_code_block` 的兜底补上 `$`（它不像台词、也不在动词白名单里）
        # —— 与 M1 修的块形态是同一个坑，只是形状不同。
        # 声明过就说明"工具有话说"，那就统一走"逐字写回"。
        declared = payload.get('spec_name') or ''
        if (payload.get('raw_kind') or '') in ('unknown_block',
                                              'known_block') or declared:
            self.flush_stage_only()
            self.pending.condition = stmt.condition or ''
            self.pending.opaque = _append_line(self.pending.opaque, text)
            self.pending.spec_name = declared
            if declared:
                # 头行原文（M4）：T1 的**参数编辑面**。存的是**原样拷贝**，
                # 渲染层靠"改没改"来决定要不要替换首行 —— 于是"没改就不动
                # 一个字节"是结构上成立的，不靠调用方自觉。
                self.pending.spec_head = next(
                    (ln for ln in str(text).split('\n') if ln.strip()), '')
            self.pending_has_stage = True
            return True, False
        pending = self.pending
        pending.condition = stmt.condition or pending.condition
        # **raw 行逐条记令牌**（缺口 4 第八轮）：一条语句可能占多行，
        # 令牌按**行**记（`raw#0`/`raw#1`…），这样"这条 raw 行夹在哪两条
        # 舞台指令之间"就能逐条还原（从前 raw 与舞台是两条通道，
        # 彼此之间只有槽位：真机 4 个文件命中）。
        before = len(_split_lines(pending.raw))
        pending.raw = _append_line(pending.raw, text)
        lines = _split_lines(text)
        # **续行的缩进按"相对这条语句"记**（`ordered` 的第三位），
        # 而**格子里的原文照旧逐字存**（`pending.raw` 不动）。
        #
        # 为什么这么分（2026-10-07 第九轮，真机 `mini_game_dfinder.rpy`）：
        # 续行在文件里是绝对缩进（label 的 4 + 相对的 4 = 8），渲染时外面还会
        # 按这一拍层级再补一次 → **每往返一轮多 4 个空格**（纯渲染幂等红）。
        # 但**不能**改写格子里的原文：`_split_code_cell` 靠缩进/标记分辨
        # raw 与 code，改了它过表就串结构（Excel 幂等红）。
        # 所以在**渲染侧**做：记相对缩进，`_reindent` 按它铺回去，
        # 外层再补这一拍的层级 —— 两边各管一半，闭环稳定。
        rel = _relative_continuation_indents(lines)
        pending.ordered.extend(
            ('raw', ln, r) for ln, r in zip(lines, rel))
        for i in range(len(lines)):
            self.mark_op('raw', before + i)
        self.pending_has_stage = True
        return True, False

    def _emit_loop(self, stmt, payload, _ln):
        """
        循环块（`while` / `for`）—— **头行成组头，块体成普通拍**
        （D2 起；2026-10-07 第三轮把块体也结构化）。

        `payload['walked']` 决定走哪条路：

          · `True`（**现在**的解析路径）：块体已经被解析成普通语句、带着
            `nest_path='…>loop:n'` 排在后面 —— 这里只造一张**头拍**
            （只有 `loop_header`，**没有 `opaque`**，与 `menu` 头拍同款）。
            渲染层按结构位把它们重新归组成 `while …:` 块
            （`beat_render.render_loop_block`），于是"循环体里的台词"
            与普通台词一样可以逐行编辑 = **T0**。
          · `False`（老表 / 兼容路）：块体只有逐字原文 —— 整块进 `opaque`，
            头行进 `loop_header`，渲染时**逐字吐回**（= `T0b`，
            与 M1/M4 那套"没改就不动一个字节"完全同款）。

        ⚠ **`opaque` 在结构化那条路上必须是空的**：它是渲染层的分支判据
        （`_is_loop_head` = 有头行、无整块原文）。带着 `opaque` 又带结构位
        会把同一段内容写两遍（一遍逐字、一遍来自块体拍）—— 那是"静默改剧情"
        里最难查的一种。
        """
        header = str(payload.get('header') or '')
        walked = bool(payload.get('walked'))
        if not walked:
            text = (payload.get('text', '') or '').strip()
            if not text:
                return True, False
            self.branch_guard(stmt)
            self.flush_stage_only()
            self.pending.condition = stmt.condition or ''
            self.pending.opaque = _append_line(self.pending.opaque, text)
            self.pending.loop_header = header
            self.pending_has_stage = True
            return True, False
        if not header:
            return True, False
        self.branch_guard(stmt)
        # 头拍直接落盘（与菜单头同一个理由：`pending` 里攒着的舞台指令
        # 属于**循环之前**，必须在循环前面落下来）。
        self.flush_stage_only()
        self.seq += 1
        head = self.with_src(Beat(
            seq=self.seq, label=self.current_label,
            condition=stmt.condition or '',
            kind=KIND_NARRATION,
            loop_header=header,
        ), _ln)
        # 循环头在**父块**里（组头不属于循环体本身）—— 与菜单头同款，
        # 结构位显式写死，不让"按当前语句转发"那趟覆盖。
        head.nest_path = getattr(stmt, 'nest_path', None)
        head.inline_comment = getattr(stmt, 'inline_comment', '') or ''
        self.beats.append(head)
        # 循环是控制流边界：后面的 `extend` 不再属于循环前那句台词
        self.last_speaker = ''
        return False, True

    def _emit_camera(self, stmt, payload, _ln):
        """
        `camera` 块（3D 舞台）：**头行进 `camera_header` + 整块原文进 `opaque`**。

        与 `_emit_loop` **逐字同款**（同一套保真策略、同一个 `flush_stage_only`
        让它独占一张拍）—— 不发明第二套规矩：渲染层靠"头行改没改"决定要不要
        替换首行，所以"没改就不动一个字节"是结构上成立的。

        为什么单独一个字段而不是复用 `loop_header`：两者的**校验**与**提示**
        不同（循环头是条件表达式，camera 头是层名），混用一个字段以后没法
        分开说；而渲染层的优先级链把它们并列即可（见 `beat_render.emit_opaque`）。
        """
        text = (payload.get('text', '') or '').strip()
        if not text:
            return True, False
        self.branch_guard(stmt)
        self.flush_stage_only()
        self.pending.condition = stmt.condition or ''
        self.pending.opaque = _append_line(self.pending.opaque, text)
        self.pending.camera_header = str(payload.get('header') or '')
        self.pending_has_stage = True
        return True, False

    def _emit_jump_call(self, stmt, payload, _ln):
        # 跳转/调用是控制流，进 Raw 原样保留，绝不丢。
        self.flush_stage_only()
        target = payload.get('target', '')
        self.seq += 1
        self.beats.append(self.mark_struct(self.with_src(Beat(
            seq=self.seq, label=self.current_label,
            condition=stmt.condition or '',
            kind=KIND_NARRATION,
            raw=f"{stmt.kind} {target}"
                f"{_call_from_suffix(stmt.kind, payload)}",
        ), _ln), stmt))
        # 控制流走了，后面的 extend 不再属于任何已说话的 Beat
        self.last_speaker = ''
        return False, True

    def _emit_return(self, stmt, payload, _ln):
        self.flush_stage_only()
        # `return` 不落成卡（只是章尾）；一章结束，`extend` 不可能跨 label
        # 续写，归属清零
        self.last_speaker = ''
        return False, False

    # ------------------------------------------------------------------
    #  收尾
    # ------------------------------------------------------------------

    def _fill_empty_labels(self) -> None:
        """
        源文件里写了 `label X:` 却**一张拍都没落到它名下**的，补一张空拍。

        不补会怎样：`render_beats_to_rpy` 是按拍分组出 label 的，一张拍都
        没有时它退回 `label start:`（见那里的 `groups.append(('start', []))`）
        —— **名字被静默改掉**，所有 `jump X` 失效，且不报任何错。

        什么时候会一张拍都没有：

          · `label X:` 后面全是"不落拍"的语句（`return` 就是）；
          · `label X:` 是空 label（真实项目 51 个 label 里 0 例，但
            `tools/hell_test.py` 的 `struct_empty_label` 就是照着它造的）。

        插在哪：这个 label **后面那个 label 的第一张拍之前**（保住原文的
        label 顺序）；后面没有了就放末尾。空拍没有内容，渲染出来只是一条
        `label X:` —— 与源文件一致，不会凭空多出语句。
        """
        have = {b.label for b in self.beats}
        for i, name in enumerate(self.label_order):
            if name in have:
                continue
            idx = len(self.beats)
            for nxt in self.label_order[i + 1:]:
                if nxt in have:
                    idx = next((j for j, b in enumerate(self.beats)
                                if b.label == nxt), idx)
                    break
            self.beats.insert(idx, Beat(label=name))
            have.add(name)

    def build(self) -> list:
        """落掉最后一拍 + 收尾（合成 DSL 文本 / 归位菜单体）。"""
        self.flush_stage_only()
        self._fill_empty_labels()
        assign_identity(self.beats)
        # 标记"解析来的 Beat"：导出时图像名要逐字还原，不做下划线改写。
        for b in self.beats:
            b._from_script = True
        # 合成 DSL 文本：表格里看到的舞台/音频是"人话"，而不是拆散的六七列。
        # 这里没有符号表，所以合成的是原生写法（`show ling close_happy at left`）
        # ——它是无损的，编译时原样直通；打开项目后会用符号表重合成中文写法。
        for b in self.beats:
            # 结构化列（背景/立绘/位置/转场/退场）已经承载了主要舞台指令，
            # 「舞台（高级）」列只放结构化列表达不了的部分（复杂 show 子句、
            # 自带位置的立绘、带转场的退场）。否则同一句 show 会被结构化列
            # 和 Stage 列各输出一次，生成重复代码。
            b.stage = compose_stage_extra(b, None)
            b.bgm = compose_audio_text(b, 'music')
            b.sfx = compose_audio_text(b, 'sound')
        order_menu_bodies(self.beats)
        return self.beats


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

    **实现搬到了 `_BeatBuilder`**（P3-12，2026-10-05）：它原本是一个
    538 行 / 复杂度 101 的函数，7 个可变状态藏在闭包里。
    这个函数保留原签名，只负责"取 labels → 喂给 builder → 收结果" ——
    调用方一行都不用改。
    """
    statements = list(statements)
    if labels is None:
        labels = [label] * len(statements)

    builder = _BeatBuilder(label)
    builder.note_branch_edges(statements)
    for stmt, stmt_label in zip(statements, labels):
        builder.add(stmt, stmt_label)
    return builder.build()


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



class _HydrateAcc:
    """
    `_collect_stage_acc` 的累加器（M4 拆 `_hydrate_stage` 时打包）。

    十二个名字里五个是**集合/字典**（原生真迹），其余是这一拍的结构化结果 ——
    打包成一个对象，循环里不会漏传一个。
    """

    __slots__ = ('at', 'hide_items', 'native_hide', 'native_raw', 'native_scene', 'native_scene_raw', 'native_show', 'order', 'scene', 'scene_at', 'show_items', 'with_')

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _collect_stage_acc(parsed, beat, symbols, audit, acc) -> None:
    """
    把「舞台（高级）」列的每个 op **并进累加器**（M4 从 `_hydrate_stage` 搬出）。

    两条分支：
      · **原生写法**（`op.native`）：逐字真迹，影子记进 `native_*`，渲染时
        结构化列要跳过它（真迹由 Stage 自己出）；
      · **方括号写法**：编译出来的 `show/hide/scene/…` 拆成影子。
    判定细节与那些"为什么"见循环里的注释 —— 一个字都没改。
    """
    # ⚠ 这两行原先是 `_hydrate_stage` 的**局部 import**，循环搬到这儿
    # 之后必须跟着过来（否则 `NameError: compile_stage is not defined`
    # —— 判据当场抓到，见 `test_stage_projection`）。
    from storygraph.story.beat_model import hide_item_part, show_item_part
    from storygraph.story.dsl import compile_stage

    for op, (_intent, code, status) in zip(
            parsed.ops, compile_stage(beat.stage, symbols, audit)):
        if op.native:
            n_show, n_hide = len(acc.show_items), len(acc.hide_items)
            _absorb_native(op, acc.show_items, acc.hide_items, acc.order)
            for it in acc.show_items[n_show:]:
                part = show_item_part(it)
                acc.native_show.add(part)
                # 影子 → 原文：渲染时按**这一条影子的编码**查回来
                acc.native_raw[('show', part)] = op.raw
            for it in acc.hide_items[n_hide:]:
                part = hide_item_part(it)
                acc.native_hide.add(part)
                acc.native_raw[('hide', part)] = op.raw
            head = op.raw.split()[0] if op.raw.split() else ''
            if head == 'scene':
                spec, _at, _w = _parse_native_stage_tail(
                    op.raw[6:].strip())
                acc.scene = spec or acc.scene
                acc.scene_at = _at or acc.scene_at
                acc.with_ = _w or acc.with_
                if spec:
                    acc.native_scene = (spec, _at or '')
                    # 原生 `scene …` 的原文（同 `native_raw`：它得排在
                    # **自己的 verb 位**上，不能整块拖到舞台尾段）
                    acc.native_scene_raw = op.raw
            elif head == 'with' and not acc.with_:
                parts = op.raw.split()
                acc.with_ = parts[1] if len(parts) > 1 else ''
            continue

        if op.verb == 'show':
            if code.startswith('# TODO'):
                continue
            head = code.split(None, 1)[1] if ' ' in code else ''
            spec, _at, _w = _parse_native_stage_tail(head)
            if spec:
                acc.show_items.append({'clause': '', 'image': spec,
                                   'at': _at or ''})
            acc.order.append('show')
        elif op.verb == 'hide':
            if code.startswith('# TODO'):
                continue
            head = code.split(None, 1)[1] if ' ' in code else ''
            spec, _at, _w = _parse_native_stage_tail(head)
            if spec:
                acc.hide_items.append({'image': spec, 'with': _w or ''})
            acc.order.append('hide')
        elif op.verb == 'scene':
            if code.startswith('# TODO'):
                continue
            head = code.split(None, 1)[1] if ' ' in code else ''
            spec, _at, _w = _parse_native_stage_tail(head)
            acc.scene = spec or acc.scene
            acc.scene_at = _at or acc.scene_at
            acc.with_ = _w or acc.with_
            acc.order.append('scene')
        elif op.verb == 'with':
            acc.with_ = acc.with_ or op.with_
        elif op.verb == 'pause':
            acc.order.append('raw')


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
    acc = _HydrateAcc(show_items=[], hide_items=[], order=[], scene=None,
                       scene_at='', with_='', at='', native_show=set(),
                       native_hide=set(), native_scene=None,
                       native_scene_raw='', native_raw={})

    _collect_stage_acc(parsed, beat, symbols, audit, acc)

    # 追加到结构化列已经填好的内容后面（高级列是"补充通道"）。
    #
    # ⚠ **只追加"还没有的"影子，不动作者原有的条目**（2026-10-07 缺口 4）：
    # 从前是"两边合起来按内容去重"，于是作者写两遍同样的 show/hide
    # （`show A / hide A / show A` 那种交错）会被一起收掉 ——
    # **收尾画面与作者要的相反**。现在的口径是：
    #   · 作者原有的顺序与条数**逐条保留**（`beat.show` 不动）；
    #   · 影子（Stage 列那一份，属于"同一条指令的另一种写法"）只在
    #     结构化列里**没有同款**时才追加。
    if acc.show_items:
        existing = _split_show_items(beat.show)
        have = {show_item_part(it) for it in existing}
        add = [it for it in acc.show_items
               if show_item_part(it) and show_item_part(it) not in have]
        if add:
            beat.show = _join_show_items(existing + add, dedup=False)
    if acc.hide_items:
        existing = _split_hide_items(beat.hide)
        have = {hide_item_part(it) for it in existing}
        add = [it for it in acc.hide_items
               if hide_item_part(it) and hide_item_part(it) not in have]
        if add:
            beat.hide = _join_hide_items(existing + add, dedup=False)
    if acc.scene is not None:
        beat.scene = acc.scene
    if acc.at:
        beat.at = acc.at
    if acc.scene_at:
        beat.scene_at = acc.scene_at
    if acc.with_:
        beat.with_ = acc.with_
    dedup = []
    for v in acc.order:
        if v in ('scene', 'show', 'hide') and v not in dedup:
            dedup.append(v)
    # ⚠ **不许覆盖**已有的 `stage_order`（2026-10-05 修，真实项目 17 处）：
    # 这个值是从表里 `StageOrder` 列带回来的、**解析时的真迹**；而 `Stage`
    # 这一列里**没有** `scene` 那条（`scene` 存在 `Scene` 列），拿它推导出来的
    # 顺序必然少一个 `scene` → 渲染时 `with` 从句就贴到别的一行上：
    #
    #     scene bg bg1_1 with dissolve      ← `with` 本来是给下一行 show 的
    #     show lan agreeable at right
    #     show bg bg2_1 with dissolve
    #
    # 规矩：**已有的顺序保留**，只把 DSL 里出现、原顺序里没有的 verb 补在后面
    # （老表没有这一列时 `existing` 为空，行为与从前一致）。
    existing = [v.strip() for v in (beat.stage_order or '').split(',')
                if v.strip()]
    merged = list(existing)
    for v in dedup:
        if v not in merged:
            merged.append(v)
    if merged:
        beat.stage_order = ','.join(merged)

    # 记下"原生真迹"的影子：渲染时结构化列要跳过它们（真迹由 Stage 自己出）。
    # 放在最后而不是逐条赋值，是为了让"没原生指令"时也能把上一轮残留清掉。
    for attr, val in (('_stage_native_show', frozenset(acc.native_show)),
                      ('_stage_native_hide', frozenset(acc.native_hide)),
                      ('_stage_native_scene', acc.native_scene),
                      ('_stage_native_scene_raw', acc.native_scene_raw),
                      ('_stage_native_raw', acc.native_raw)):
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

    ⚠ **影子条目带一份 `native_raw`**（作者那一行的原文，2026-10-05 修 O2）：
    影子只是给快照/校验看的，出脚本时**必须用原文**（不做下划线改写）；
    而原文究竟排在**哪个位置**，只有这张条目自己的位置知道 —— 从前渲染时
    把它整块拖到"舞台尾段"（所有 verb 之后），于是

        show ling happy at left
        show ling angry at right with dissolve   ← 影子在结构化列里
        hide ling with fade                      ← 逐字出，排在它的 verb 位

    过表之后 `hide` 会插到第二条 `show` 前面（收尾画面不同）。
    原文由 `_hydrate_stage` 记进 `beat._stage_native_raw`（**按条目编码查**，
    见 `beat_render._stage_lines`）—— 不能挂在条目 dict 上：`beat.show` 最后
    会被 `_join_show_items` 拼成**字符串**，dict 上的额外键活不过那一步。
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
    except Exception as e:                        # noqa: BLE001 - 解析不了就整条留下
        swallow('story.beat_build.stage_only_ops', e)
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



def attach_file_blocks(beats: list, blocks) -> list:
    """
    把**文件级保真块**（顶层 `init python:` 之类）挂到拍列表上。

    2026-10-05，拓展边界③。为什么挂在**第一张拍**上：

      · 这些块不属于任何"拍"，但"表"的单位是拍 —— 信息要跟着表走，
        就只能搭在某一拍上；
      · 搭第一张拍最稳：`to_row` / `from_row` 是逐行的，挂在中间任意一张
        也一样，但第一张在"表被排序/筛选"时最不容易被理解错
        （这一列整列隐藏，策划看不见，工具读回来只看"这些拍里有没有"）。

    没有拍的时候（纯 `init python:` 文件）**造一张空拍来携带它** ——
    否则信息无处安放，就会退回"整块消失"。这张空拍渲染时什么都不产出
    （`Beat.is_empty()` 认 `file_blocks` 算有内容，所以不会被当空行丢掉），
    `render_beats_to_rpy` 给的仍然是 `label start: pass return` + 文件级块，
    与"没有拍"时的产物只多不少。

    返回**新列表**（不改调用方传进来的那个）：调用方常常还要用原列表做别的。
    """
    from storygraph.story.beat_model import Beat, join_file_blocks

    blocks = [(getattr(b, 'header', '') or '', getattr(b, 'code', '') or '')
              for b in (blocks or ())]
    return attach_file_block_text(beats, join_file_blocks(blocks))


def attach_file_block_text(beats: list, text: str) -> list:
    """
    把**已经渲染好的**文件级保真块文本挂到拍列表上（表 → 拍那条路）。

    与 `attach_file_blocks` 是同一件事的两半：那个接 `(块头, 块体)` 的列表，
    这个接"从隐藏表读回来的整段文本"。挂载规则只有一处
    （挂在第一张拍；一张拍都没有就造一张携带拍），见上面那段说明。
    """
    from storygraph.story.beat_model import Beat

    text = str(text or '').strip('\n')
    if not text:
        return list(beats or ())
    out = list(beats or ())
    if not out:
        return [Beat(seq=0, label='', file_blocks=text)]
    first = out[0]
    first.file_blocks = (f'{first.file_blocks}\n\n{text}'
                         if first.file_blocks else text)
    return out


#: 解析器把"没被任何分支接住"的语句记成这几个 `raw_kind`。
#: 只有它们**有可能**被项目声明认领 —— 见 `_has_unmodeled`。
_UNMODELED_KINDS = frozenset({'unmodeled', 'unknown_block', 'known_block'})


def _has_unmodeled(parse_result) -> bool:
    """
    这份 `ParseResult` 里**有没有**可能被认领的语句？

    用来**短路注册表装配**（那要扫全项目找 CDS）。绝大多数章节一条都没有，
    而装配是纯开销 —— 不加这个判断，全量测试从 10 分钟涨到 19 分钟。
    """
    for st in (getattr(parse_result, 'statements', None) or ()):
        payload = getattr(st, 'payload', None)
        if isinstance(payload, dict) and (
                payload.get('raw_kind') or '') in _UNMODELED_KINDS:
            return True
    return False


def beats_from_parse_result(parse_result, label: str = '',
                            spec_registry=None) -> list:
    """
    入口：ParseResult → Beat 列表（并带上**文件级保真块**）。

    顶层 `init python:` 不属于任何拍，但必须跟着表走 ——
    见 `attach_file_blocks`（拓展边界③）。

    `spec_registry`（M3）：项目声明过的自定义语句表（见
    `story.statement_registry.StatementRegistry`）。给了就把"未建模"里
    被声明过的那几条**认领**下来（打上 `payload['spec_name']`）——
    它们从"工具不认识"（T3）升成"工具知道你是谁"（T1）。
    不给就退回 M1 的口径（全部算未建模块）。
    """
    # ⚠ **先短路**：没有任何"未建模"语句时不要去装配注册表 ——
    # 装配要扫一遍全项目找 CDS，而那只有"真出现了自定义语句"才有意义。
    # 实测（2026-10-07）：不短路时全量测试 10 分钟 → 19 分钟；
    # 绝大多数项目/用例里一条未建模语句都没有，这一次扫描纯属白跑。
    if spec_registry is None and _has_unmodeled(parse_result):
        # 没显式给就从 `ParseResult` 反推项目根再装配（见
        # `registry_for_parse_result` 的说明：8 个调用点，漏一处就是
        # "那条路上的自定义语句又变成不认识"）。
        try:
            from storygraph.story.statement_registry import (
                registry_for_parse_result)
            spec_registry = registry_for_parse_result(parse_result)
        except Exception as e:                # noqa: BLE001 - 装配失败不该影响出拍
            swallow('story.beat_build.spec_registry', e)
            spec_registry = None
    if spec_registry:
        from storygraph.story.statement_registry import (
            annotate_spec_statements)
        annotate_spec_statements(getattr(parse_result, 'statements', None),
                                 spec_registry)
    beats = beats_from_statements(
        parse_result.statements,
        label=label,
        labels=getattr(parse_result, 'statement_labels', None),
    )
    _fill_labels_without_beats(
        beats, getattr(parse_result, 'labels', None) or {})
    return attach_file_blocks(beats, getattr(parse_result, 'file_blocks', None))


def _fill_labels_without_beats(beats: list, labels_info: dict) -> None:
    """
    源文件里**出现过**、但一张拍都没落到它名下的 label：补一张空拍。

    与 `_BeatBuilder._fill_empty_labels` 是同一件事的两个入口：
      · builder 那只管"有语句、但语句都不落拍"的 label（如 `return`）；
      · 这只管**一条语句都没有**的 label（`label a:` 后面直接是 EOF
        或下一个 label）—— 那种 label 连 statement 都没有，builder
        根本看不见它（`pr.labels` 里有，`pr.statements` 里没有）。

    为什么非补不可：不补的话渲染层在没有拍时会退回 `label start:`
    （见 `render_beats_to_rpy`），于是产物里**凭空多出一个 `label start`**
    —— 而 `start` 是 Ren'Py 的游戏入口，项目里几乎必然已有一个，
    合并进 `game/` 就是**重复 label，游戏起不来**。
    名字被静默改掉比内容丢失更坏：所有 `jump a` 失效，且不报错。

    插在哪：源文件里它**后面第一条**拍之前（按源行号）；后面没有了就末尾。
    """
    if not labels_info:
        return
    have = {b.label for b in beats}
    try:
        order = sorted(labels_info.items(),
                       key=lambda kv: (str(kv[1][0]), int(kv[1][1])))
    except (TypeError, ValueError, IndexError):      # noqa: BLE001
        order = list(labels_info.items())
    for name, info in order:
        if name in have:
            continue
        try:
            lineno = int(info[1])
        except (TypeError, ValueError, IndexError):   # noqa: BLE001
            lineno = 0
        idx = len(beats)
        if lineno:
            for j, b in enumerate(beats):
                if getattr(b, 'src_first', 0) and b.src_first > lineno:
                    idx = j
                    break
        beats.insert(idx, Beat(label=name))
        have.add(name)
