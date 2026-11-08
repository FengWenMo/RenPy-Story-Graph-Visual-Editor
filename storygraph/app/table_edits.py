"""
storygraph.app.table_edits
==========================
"表单 → 一拍"的**写回规则**：`apply` 里那十二段各管一条语义的代码。

为什么单独一层（2026-11-01，服务化第二刀）
------------------------------------------
判据与资源侧那一刀同一个形状：**只拿 `(这一拍, 表单字典, 符号表, 兄弟拍)`
能不能回答？** 能回答的搬到这里，答不了（会话状态、指纹、脏标记、
磁盘、Qt）的留在 `SessionTableMixin`。

搬完之后的边界：

  · 这里**不知道**"第几拍"、不知道有没有加载过文件、不碰 `self.beats[idx]`。
    调用方把那一拍递进来，改的是那个对象本身。
  · 这里**不立"未导出"标记**（`mark_edits_dirty`）：那要比较保存前后
    的指纹，而指纹怎么算属于会话（`_beat_edits_print`）。
  · 这里**不落盘、不导出**：改完就返回。

于是这些规则可以**脱开会话单测**：造一拍、给一个表单字典、看那一拍变成
什么样。从前要覆盖"高级区收起 ≠ 清空"得先建 `EditorSession`、加载一个
真项目、再 `apply`，测的是三件事而不是一件。

⚠ 顺序是**契约**，不是实现细节（见 `write` 里那段长注释）：折动态列必须
最后做，否则会被立绘卡与 hydrate 盖回去。拆分时一律保持原顺序，注释跟着
代码走 —— 这些注释记的是"为什么不能换个写法"，比代码本身更容易被误删。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from storygraph.core.safeguard import swallow

from storygraph.app import beats as B
from storygraph.story.flow import FLOW_FIELD_KEYS
from storygraph.story.beat_build import hydrate_from_dsl, stage_only_ops
from storygraph.story.beat_model import (Beat, KIND_MENU, MENU_NO_CAPTION,
                                         compose_stage_extra, hide_item_part,
                                         join_hide_items, show_item_part,
                                         split_hide_items, split_show_items)
from storygraph.story.spec_args import is_dynamic_column


__all__ = ['FieldWriter', 'drop_hide_tag', 'hide_flow', 'hide_tags',
           'show_tags', 'uniform_position_of', 'write_statement_head']


#: `show` / `hide` 里**不是立绘 tag** 的那几种首词（舞台/屏幕/表达式）。
_NOT_A_TAG = frozenset({'expression', 'screen', 'layer', 'scene', 'behind'})


def _op_tags(text: str, split, encode) -> set:
    """
    一段 `show` / `hide` 文本 → 它涉及的**角色 tag** 集合。

    `lan@dissolve` 只认 `lan`；`show expression holo("x")` / `show screen hud`
    这类**不是立绘**，跳过 —— 拿它们去判"谁在台上"会把不相干的东西算进来。
    """
    out = set()
    for item in split(str(text or '')):
        head = encode(item).split('@')[0].strip()
        if not head:
            continue
        word = head.split()[0]
        if word in _NOT_A_TAG:
            continue
        out.add(word)
    return out


def show_tags(text: str) -> set:
    """这段 `show` 让谁上台（tag 集合）。"""
    return _op_tags(text, split_show_items, show_item_part)


def hide_tags(text: str) -> set:
    """这段 `hide` 把谁赶下台（tag 集合）。"""
    return _op_tags(text, split_hide_items, hide_item_part)


def hide_flow(peers, idx: int, tag: str) -> tuple:
    """
    同一个 tag 在**兄弟拍**里的上下台曲线 → `(before, after)`。

    · `before` —— 最近的、在 `idx` **之前**就写了 `hide tag`、而从那之后
      **没有** `show tag` 的那一拍下标（`None` = 到 `idx` 时人还在台上）；
    · `after`  —— `idx` **之后**、在下一次 `show tag` 之前还写着 `hide tag`
      的拍下标（这些是"重复退场"）。

    为什么要这层判断（2026-11-08 用户实测）：

    > 我一开始先做了一次，是在后面一拍操作的，然后又在前头再点了一次
    > hide……两个就重复了！！！

    两次操作各改各的拍，谁也不看对方 —— 于是同一章里躺着两条 `hide ling`。
    它的反方向（先在前面 hide、再到后面 hide）是同一件事。判据只有一条：
    **中间有没有再 `show` 上来**；没有，多的那条就是噪声。
    """
    peers = list(peers or ())
    if not (0 <= int(idx) < len(peers)):
        return None, []
    idx = int(idx)
    before = None
    for i in range(idx - 1, -1, -1):
        beat = peers[i]
        if tag in show_tags(getattr(beat, 'show', '')):
            break
        if tag in hide_tags(getattr(beat, 'hide', '')):
            before = i
            break
    after = []
    for i in range(idx + 1, len(peers)):
        beat = peers[i]
        if tag in show_tags(getattr(beat, 'show', '')):
            break
        if tag in hide_tags(getattr(beat, 'hide', '')):
            after.append(i)
    return before, after


def drop_hide_tag(beat, tag: str) -> list:
    """从这一拍的 `hide` 里摘掉 `tag`（同一个 tag 的重复退场）；返回被摘的条目。"""
    items = split_hide_items(str(getattr(beat, 'hide', '') or ''))
    keep, gone = [], []
    for item in items:
        if hide_item_part(item).split('@')[0].strip() == tag:
            gone.append(item)
        else:
            keep.append(item)
    if gone:
        beat.hide = join_hide_items(keep) if keep else ''
    return gone


def _note(notes, text: str) -> None:
    """把一句"我替你做了什么"收进清单（`None` = 调用方不要提示）。"""
    if notes is not None and text:
        notes.append(text)


def uniform_position_of(cards: list) -> str:
    """
    卡数组 → 整拍位置。**只有所有立绘位置一致才返回它**，否则留空。

    留空很关键：位置是"整拍"的属性，若不一致还硬写一个，
    会兜底套到别的立绘头上 → 立绘错位（实测踩过）。
    """
    rows = [{'at': (str(c.get('position') or '').strip()
                    or str(c.get('at') or '').strip())} for c in (cards or [])]
    return B.uniform_sprite_position(rows)


def write_statement_head(b: Beat, text: str) -> bool:
    """
    「代码」格里写的是**这一拍自己那条自定义语句** → 写回头行（P2，2026-10-25）。

    `mycue intensity=5` 是 Ren'Py 语句，不是 Python。走代码通道会被
    `render_code_block` 补上 `$`（`$ mycue intensity=5`）—— 产物连加载
    都过不了，而拒因只会说"Python 语法错误"，看不出"参数该填参数格"。

    判据只有一条：**首词就是这一拍自己那条语句的名字**。那就不可能是
    "改成一段 python 代码"，只能是"改这条语句本身"：

      · 项目声明过的语句（`spec_name`）→ 写 `spec_head`（渲染时替换头行，
        见 `beat_render._emit_opaque`）；
      · 工具不认识的块（`opaque`）→ 换掉它的首行；
      · 普通原生行（`raw`）→ 换掉 `raw`。

    三条都不匹配（首词不是自己那条语句 / 多行）→ 返回 False，
    调用方照旧走 python 通道（那才是"我就要写代码"）。
    """
    line = (text or '').strip()
    if not line or '\n' in line:
        return False
    verb = line.split()[0]
    name = str(getattr(b, 'spec_name', '') or '').strip()
    if name and verb == name:
        b.spec_head = line
        opaque = str(getattr(b, 'opaque', '') or '')
        if opaque.strip():
            rows = opaque.replace('\r\n', '\n').split('\n')
            b.opaque = '\n'.join([line] + rows[1:])
        return True
    opaque = str(getattr(b, 'opaque', '') or '')
    if opaque.strip():
        rows = opaque.replace('\r\n', '\n').split('\n')
        head = rows[0].strip()
        if head.split() and head.split()[0] == verb:
            b.opaque = '\n'.join([line] + rows[1:])
            if name:
                b.spec_head = line
            return True
    raw = str(getattr(b, 'raw', '') or '')
    if raw.strip() and '\n' not in raw and raw.split()[0] == verb:
        b.raw = line
        return True
    return False


@dataclass(frozen=True)
class FieldWriter:
    """
    把一份表单写回一拍。**两个依赖，都是只读引用**：

      · `symbols`：符号表（立绘 / 位置 / 舞台投影都要它，与导出同一份）；
      · `peers`：**同一份文档里的兄弟拍**。只有两处要看到它们 ——
        改 `Label` 是"给整章那个 label 改名"，改链头条件是"后面几支的守卫
        跟着重算"。传空元组时这两条退化成"只改这一拍"（不会崩）。
    """

    symbols: object = None
    peers: Sequence = field(default_factory=tuple)

    # ------------------------------------------------------------------
    #  编排
    # ------------------------------------------------------------------

    def write(self, b: Beat, fields: dict, cards: list = None,
              advanced_open: bool = False, stage_loaded: str = None,
              notes: list = None) -> Beat:
        """
        把表单内容写回 `b`。返回同一个对象（调用方递进来的那一拍）。

        语义（与 tkinter 版 `collect_beat` 一字不差，现在只有这一份实现）：
          · 主区**始终**写回；
          · 高级区**只在展开时**写回（收起 ≠ 清空）；
          · `show` 只有拼得出内容时才覆盖（**空网格不清空立绘**）；
          · `at` 只在"所有立绘位置一致"时才整拍（否则留空，防错位）；
          · `Stage` **每次重算**（它是投影，不是输入），**除非用户真的手改了
            那一格**（见 `_apply_stage`）；
          · 最后过一遍 `hydrate_from_dsl`，保证与 .rpy 同源。

        `notes`：调用方给的清单（`None` = 不要提示）。**工具替用户改的地方**
        往里说一句（现在只有一处：重复的退场被并成一次）——
        "我替你删了一条 `hide`"必须让人看得见，静默改别人的演出指令是本项目
        最不能接受的那一类事。

        `stage_loaded`：表单**载入时**那一格里的原文（Q 前端从
            `BeatCardEditor.load_fields` 里记下来回传）。
            用来判断"用户手改了 Stage 没有" —— 判据是
            `typed != loaded`，而不是"格子非空"：`beat_to_fields` 会把
            结构化列投影进这一格显示，所以非空**不代表**人改过。
            传 None 时退回用 `beat.stage`（老调用方的行为）。
        """
        fields = dict(fields or {})

        self._apply_flow_fields(b, fields)

        self._apply_main_columns(b, fields)

        # **「每参数一列」的键**（`mycue·intensity` / `camera·layer@0` /
        # `pause·seconds@0`）：卡片与 Excel 走**同一条折叠路径**
        # （`beat_model.fold_spec_arg_columns`）—— 一份实现两处用，
        # 卡片上改参数与在表里改参数的结果必然一致。
        #
        # ⚠⚠ **折的时机被挪到了这个方法的最末尾**（2026-10-13 实测修）：
        #   放在这里会被后面的 `cards_to_show` / `hydrate_from_dsl` 盖回去
        #   （实测：`show·人物@0` 改成 `ling`，保存后又变回 `lan`）。
        #   真正的调用点在 `return b` 之前，见那里的长注释。
        #
        # 为什么不受 `advanced_open` 影响：这一组控件是跟着这一拍的语句走的
        # （有参数才出现），不像高级区那样会被折叠 —— 折叠起来的东西不写回
        # 是契约（见上面那段），而它一直可见。

        self._apply_advanced_fields(b, fields, advanced_open)
        # **类型是说话人的投影**（`say` / `narration` 这一对，见
        # `app.beats.kind_from_speaker`）：用户只填说话人时，高级区那一格
        # 也必须跟着变成 `say` —— 否则它一直显示 `narration`，用户会以为
        # "我填的说话人没生效"（2026-10-05 实测报的）。
        b.kind = B.kind_from_speaker(b.kind, b.speaker)

        self._apply_sprite_cards(b, cards, notes)

        self._apply_word_cells(b, fields, cards)

        self._apply_condition(b, fields)

        self._apply_stage(b, fields, stage_loaded)

        try:
            hydrate_from_dsl([b], self.symbols)
        except Exception:                    # noqa: BLE001 - 与旧实现一致的兜底
            swallow('editor.apply')
        # **舞台 verb 顺序跟着结构化列走**（2026-10-05 用户实测报："我已经保存
        # 该卡片了，可是当前舞台变化没有生效，并且舞台演出预览没有变化"）：
        # 卡片上加一张立绘只写 `beat.show`，而 `stage_order` 还停在 `'scene'`
        # —— 舞台快照/预览从前只回放列出来的 verb → 看不到这个人（产物其实
        # 是对的，渲染器会补齐）。读的时候两边现在都补齐（`Beat.stage_verbs`），
        # 这里再把**随表走的列**写实，免得下一次从表回来的人又看到"这一拍
        # 没有立绘"。
        try:
            b.sync_stage_order()
        except Exception as e:                # noqa: BLE001 - 顺序列坏了不该拦住保存
            swallow('editor.apply.sync_stage_order', e)
        # ⚠⚠ **语句参数必须在这一切之后再折**（2026-10-13 实测修）。
        #
        # 现场（用户要求"连 show 这种内置语句也能在卡片上长出参数格"）：
        # 卡片上把 `show·人物@0` 从 `lan` 改成 `ling`，保存后 `Beat.show` 又变回
        # `lan swim_suit happy` —— **一个字都没生效**。
        #
        # 根因是**顺序**：动态列在上面（第 480 行那一支）就折好了，可紧接着
        #   · `cards_to_show(cards)` 会用**立绘卡**重建整个 `Beat.show`；
        #   · `hydrate_from_dsl` 会再从 `show` 反推一遍结构化字段；
        # 两件事都把刚折进去的参数**盖回原样**。而 `show` 的参数格恰恰就是
        # `Beat.show` 这一格 —— 于是"卡片上能改、但改了不算"。
        #
        # 动态列是**用户刚在这一格上亲手改的字**，它比"从立绘卡/DSL 推出来的"
        # 更权威，所以放在最后折。（放在这里也顺带让 `sanitized_cells` 的留痕
        # 与"最终内容"一致。）
        self._fold_dynamic_columns(b, fields)
        return b

    # ------------------------------------------------------------------
    #  各段
    # ------------------------------------------------------------------

    def _apply_flow_fields(self, b, fields) -> None:
        """控制流卡（call / jump）的目标与类型写回（拆自 `apply`，2026-11-01）。"""
        # **控制流卡的目标 / 类型**（2026-10-22，P0-4 第一阶段）。
        #
        # 它改的是 `Beat.raw` 里那一行（`call side`），不是任何一个 Excel
        # 列。所以必须在下面的 `CodeBlock` 处理**之前**做：先让 raw 变成
        # 新目标，后面的 `CodeBlock != compose_code(b)` 才会是 False（前端
        # 同时提交了同步后的代码格），不会把 raw 误降级成 code。
        flow_keys = any(k in fields for k in FLOW_FIELD_KEYS)
        if flow_keys:
            from storygraph.story.flow import (parse_flow, render_flow_line,
                                               update_beat_flow)
            _old_flow = parse_flow(b)
            update_beat_flow(
                b,
                fields.get('FlowType') if 'FlowType' in fields else None,
                fields.get('FlowTarget') if 'FlowTarget' in fields else None,
                fields.get('FlowArgs') if 'FlowArgs' in fields else None,
                fields.get('FlowFrom') if 'FlowFrom' in fields else None)
            # **代码格跟着控制流卡一起改**（2026-10-24，修审计 P0-6）。
            #
            # 从前这件事只在前端做（`main_window._flow_code_synced`）：UI 保存时
            # 会先把「代码」格改成新那行，于是 `compose_code(b)` 与它相等，
            # 下面那支不会把 raw 降级。可**任何别的调用方**（脚本 / 表格导入 /
            # AI agent / 测试）都只交回 `fields()`，那里面 `CodeBlock` 恒等于
            # **旧**行 —— 于是：raw 改成新目标 → `CodeBlock != compose_code(b)`
            # → raw 被清空、旧行写回 code → **改了等于没改，还 ok=True**。
            #
            # 下沉到这里的判据与前端那条一样窄：只有「代码格确实还是原来那行
            # 控制流」才同步；用户手写过别的代码，就交给下面那支去降级。
            if 'CodeBlock' in fields and _old_flow:
                if str(fields.get('CodeBlock') or '').strip() == _old_flow['line']:
                    _new_flow = parse_flow(b)
                    if _new_flow:
                        fields['CodeBlock'] = render_flow_line(
                            _new_flow['type'], _new_flow['target'],
                            _new_flow.get('args') or '',
                            _new_flow.get('from_clause') or '')

    def _apply_main_columns(self, b, fields) -> None:
        """主区那六列（Speaker / Text / Scene / BGM / Voice / Comment）（拆自 `apply`，2026-11-01）。"""
        # **键不存在 = 不动这一格**（2026-10-05 修，与 `fields_to_beat` 同源）。
        #
        # 以前一律 `b.scene = str(fields.get('Scene') or '').strip()`，于是
        # **局部表单会静默清空其它列** —— 实测
        #
        #     s.apply(0, {'Speaker': 'e', 'Text': '只改台词'})
        #
        # 之后 `beat.scene` 从 `'bg classroom'` 变成 `''`：只改了台词，
        # **背景被抹掉了**。前端（Qt / tkinter）交回的永远是完整表单，
        # 所以界面上的行为不变；但任何"只交回改过那几格"的调用方
        # （脚本、测试、将来的增量编辑器）都会踩到。
        #
        # `condition` 早就是这个判据（见下面那段注释），这里把同一套
        # 推广到全部列：**区分"没给我"与"给我空串"**。
        if 'Speaker' in fields:
            b.speaker = str(fields.get('Speaker') or '').strip()
        if 'Text' in fields:
            b.text = str(fields.get('Text') or '').strip()
        # 无标题菜单的占位符要补回来：表单里那一格是空的（`beat_to_fields`
        # 把占位符显示成空串），但"空"在这里的意思是"菜单没有标题"，
        # 不是"这一拍什么都没有"——补不回占位符，`menu:` 块就会消失。
        if b.kind == KIND_MENU and not b.text:
            b.text = MENU_NO_CAPTION
        if 'Scene' in fields:
            b.scene = str(fields.get('Scene') or '').strip()
        if 'BGM' in fields:
            b.bgm = str(fields.get('BGM') or '').strip()
        if 'Voice' in fields:
            b.voice = str(fields.get('Voice') or '').strip()
        if 'Comment' in fields:
            b.comment = str(fields.get('Comment') or '').strip()

    def _apply_advanced_fields(self, b, fields, advanced_open) -> None:
        """高级区：**收起 ≠ 清空**（拆自 `apply`，2026-11-01）。"""
        if advanced_open:
            if 'SFX' in fields:
                b.sfx = str(fields.get('SFX') or '').strip()
            self._apply_code_cell(b, fields)
            if 'Label' in fields:
                self._apply_label(b, str(fields.get('Label') or '').strip())
            if 'Kind' in fields:
                b.kind = str(fields.get('Kind') or '').strip() or b.kind
            if 'Notes' in fields:
                b.notes = str(fields.get('Notes') or '').strip()
            # 台词后缀（2026-10-18）：`(what_color="#f00")` / `with` / `id`。
            # 它在高级区里，所以**只有展开时才写回**（收起 ≠ 清空）。
            if 'SayTail' in fields:
                b.say_tail = str(fields.get('SayTail') or '').strip()
            # 菜单头（2026-10-19）：`menu 名(args):` 的名与参数表。
            # 同样在高级区里 —— 收起时不动（收起 ≠ 清空）。
            if 'MenuName' in fields:
                b.menu_name = str(fields.get('MenuName') or '').strip()
            if 'MenuArgs' in fields:
                b.menu_args = str(fields.get('MenuArgs') or '').strip()

    def _apply_code_cell(self, b, fields) -> None:
        """《代码》那一格**没改就别动**（拆自 `_apply_advanced_fields`，2026-11-01）。"""
        if 'CodeBlock' in fields:
            # ⚠⚠ **没改就别动**（2026-10-18 修）。
            #
            # 《代码》那一格是 `raw` + `code` 两份内容**拼出来给人看的**，
            # 而 `raw` 里那些行还带着"它是不是原生语句"的分类（`ordered`）。
            # 无条件写回（并把 `raw` 清空）等于**把分类丢掉**：
            # `code_lines_with_kinds()` 于是对不上 → `unified_plan()` 返回
            # `None` → 渲染退回"按 verb 分组"那条老路 →
            #
            #     play music / play ambience / play sound / stop ambience
            #     保存一次卡片之后变成
            #     play music / play sound / play ambience / stop ambience
            #
            # **顺序被改**，而用户只是打开了卡片、什么都没改。
            # 判据：只有这一格**真的和显示值不同**时才写回。
            new_code = str(fields.get('CodeBlock') or '').strip()
            if new_code != str(B.compose_code(b) or '').strip():
                # **写的是这一拍自己那条自定义语句 → 逐字写回头行**（P2）。
                #
                # 现场（审计第四轮 P2）：声明过 `mycue` 的拍，「代码」格里
                # 写 `mycue intensity=5` → 走下面那条 python 通道 →
                # 渲染成 `$ mycue intensity=5`（`render_code_block` 会给
                # 认不出的行补 `$`）→ 产物连 lint 都过不了，而拒因只说
                # "`$` 语句里的 Python 语法错误"，人猜不到"该填参数格"。
                if not write_statement_head(b, new_code):
                    b.code = new_code
                    b.raw = ''

    def _apply_sprite_cards(self, b, cards, notes: list = None) -> None:
        """立绘卡 → `show` / `at` / `hide`（**空网格不清空**）（拆自 `apply`，2026-11-01）。"""
        if cards is not None:
            # **"沿用"来的卡不写回**（2026-11-02）：`session.cards(idx)` 会把台上
            # 沿用的角色也摆出来（`inherited=True`，让人看得见现在是谁、能改表情）。
            # 那些是**看见**不是**写过** —— 放它们进来，"点一下保存"就会把上一拍
            # 的 `show` 物化进这一拍（用户没改任何东西却多了几行）。
            # 卡片改过一次，`SpriteCardWidget._mark_local` 就会清掉这个标记。
            cards = [c for c in (cards or []) if not (c or {}).get('inherited')]
            # **退场卡**（`hide=True`，2026-11-08）：用户把一张"沿用"卡点了 ✕。
            # 它要写的是 `hide <tag>`，**不是** show —— 混进 `write_back_show`
            # 会变成"这一拍又 show 一下"，人反而留在台上。
            hide_cards = [c for c in cards if (c or {}).get('hide')]
            show_cards = [c for c in cards if not (c or {}).get('hide')]
            # **立绘写回的唯一出口**（2026-10-24 修 P0，见 `write_back_show`）。
            #
            # 从前这里直接 `b.show = cards_to_show(cards)` —— **整体替换**。
            # 而卡片数组来自 `beat_sprite_rows`，它跳过"结构化列表达不了"的
            # 项（`behind` / `as` / `with` / `onlayer` / `screen` /
            # `show expression`）。两件事一夹：那些指令**整条消失**，
            # 而它们的 `with` 还会被拍级兜底套给别的立绘（真机 8 组中招）。
            #
            # `write_back_show` 把表达不了的项按原锚点插回去；没有这类项时
            # 它的输出与 `cards_to_show` **逐字节相同**（老语义不变）。
            old_show = str(getattr(b, 'show', '') or '').strip()
            new_show = B.write_back_show(old_show, show_cards, self.symbols)
            # 卡片空 → 保留原值（"空网格不清空立绘"）。这一条是防误删，
            # 不是保真：用户真要清空立绘，得用卡片上的删除按钮（会走
            # `remove_sprite`，那条路是逐条精确匹配的）。
            if new_show or not old_show:
                b.show = new_show
            # 整拍位置只在真有立绘卡时才重算：空网格连位置也不许动
            # （同一条规矩；以前这里无条件清空 `b.at`）。
            if show_cards:
                b.at = uniform_position_of(show_cards)
            self._apply_hide_cards(b, hide_cards, notes)

    def _apply_hide_cards(self, b, hide_cards, notes: list = None) -> None:
        """
        退场卡 → `beat.hide`（**只加不减**：原先那些 `hide` 条目一个字不动）。

        为什么要有这条路（2026-11-08 用户）：角色是**上一拍** `show` 上来的，
        这一拍没写 `show`，卡片网格里那张「沿用」卡就是它。对这种卡点 ✕，
        光把卡拿掉什么都不写（`show` 本来就没有它），只能写 `hide <tag>`。
        不加 `hide` 的话"角色只会越来越多"——正是用户报的那个现象。

        ⚠ 只加不减是**故意的**：`hide` 里可能还有 `cg@dissolve` 这类卡片
        表达不了、也看不懂的条目；这里没有的东西一律不动（宁可多留一条，
        也不静默删别人的演出指令）。

        **同一个角色只该退场一次**（2026-11-08 第十九轮补，用户原话）：

        > hide 了两遍！（为什么我能做到……？因为我一开始先做了一次，是在
        > 后面一拍操作的，然后又在前头再点了一次 hide……两个就重复了！！！）

        两条判据（都在 `scripts_hide_flow` 里，纯函数）：

          · **前面已经把他赶下去了**（中间没有再 `show` 上来）→ 这一拍不用再写
            `hide`，说了原因；
          · **后面还有一条多余的 `hide`**（中间没有再 `show`）→ 把那一条摘掉
            （摘的是"同一个 tag 的重复退场"，块里其它条目原样留着）。

        为什么摘后面的而不是只警告：Ren'Py 里对不在台上的人再 `hide` 一次是
        空操作，重复只是噪声；而"人已经下台了"这件事以**最早**那一拍为准。
        """
        if not hide_cards:
            return
        existing = split_hide_items(getattr(b, 'hide', '') or '')
        # 已经 hide 过这个 tag 的（哪怕带转场 `lan@dissolve`）不再重复加：
        # `hide lan` + `hide lan@dissolve` 是两行没意义的重复。
        have = {hide_item_part(it).split('@')[0] for it in existing}
        add = []
        for card in hide_cards:
            tag = (str((card or {}).get('tag') or '').strip()
                   or str((card or {}).get('image') or '').split()[0])
            if tag and tag not in have:
                have.add(tag)
                add.append({'image': tag})
        if not add:
            return
        keep = self._drop_redundant_hides(b, add, notes)
        if not keep:
            return
        b.hide = join_hide_items(existing + keep)

    def _drop_redundant_hides(self, b, add, notes: list = None) -> list:
        """挑出真该写的退场条目，并摘掉后面那些重复的（见 `_apply_hide_cards`）。"""
        idx = self._peer_index(b)
        if idx is None:
            return list(add)                 # 没有兄弟拍（单测/局部表单）：照写
        keep = []
        for item in add:
            tag = str(item.get('image') or '').strip()
            before, after = hide_flow(self.peers, idx, tag)
            if before is not None:
                _note(notes, f'「{tag}」在第 {before + 1} 拍就退场了'
                             f'（中间没有再出场），这一拍没有再写 `hide {tag}`')
                continue
            keep.append(item)
            for later in after:
                drop_hide_tag(self.peers[later], tag)
                _note(notes, f'第 {later + 1} 拍那条 `hide {tag}` 变多余了'
                             f'（这一拍已经让他退场），已一并去掉')
        return keep

    def _peer_index(self, b):
        """这一拍在 `peers` 里的下标（没有兄弟拍 / 找不到 → `None`）。"""
        for i, peer in enumerate(self.peers or ()):
            if peer is b:
                return i
        return None

    def _apply_word_cells(self, b, fields, cards) -> None:
        """「表情 / 位置」两格（**只在没有立绘卡时**写）（拆自 `apply`，2026-11-01）。"""
        # **「表情 / 位置」两格**（2026-10-24，第三轮扫描 `p0_scan_struct.py`
        # 的"改 show 的位置"用例暴露）：`fields()` 会算这两格
        # （`beat_to_fields` 的投影），而 **`apply` 从前根本不读它们** ——
        # 它们只活在旧的 `fields_to_beat`（tkinter 那条路）里。
        # 于是任何"拿到 fields()、改 Position/Expression、再 apply"的调用方
        # （脚本 / 表格导入 / AI agent）都会得到 **改了等于没改、还 ok=True**。
        #
        # 三条限制（一条都不放宽）：
        #   · **只在新值与当前投影不同、且非空时写** —— 空串在这里的意思是
        #     "多张立绘位置不一致、这一格没有统一值"（`beat_position_word` 的
        #     约定），拿它去写会把所有位置抹掉；
        #   · **只在没有立绘卡时**（`cards is None`）：卡片是真源，两处同时
        #     写会互相覆盖；
        #   · 只改 `show` 里的那一项，别的属性/参数不动（`apply_*` 本身是
        #     纯函数）。
        if cards is None:
            if 'Expression' in fields:
                _want = (fields.get('Expression') or '').strip()
                _cur = B.beat_expression_word(b, self.symbols)
                if _want and _want != _cur:
                    b.show = B.apply_expression(b.show, _want)
            if 'Position' in fields:
                _want = (fields.get('Position') or '').strip()
                _cur = B.beat_position_word(b, self.symbols)
                if _want and _want != _cur:
                    # 位置有两种存法，**这一拍用的是哪种就改哪种**：
                    #   · 拍级 `b.at`（渲染成 `show … at X`，作者最常见的写法）
                    #     —— `beat_position_word` 正是在 `item.at` 为空时退回它；
                    #   · 立绘项里的 `@X`（`e happy@left`）—— 由 `apply_position` 改。
                    # 只改已经在用的那一个，避免两种写法叠出来
                    # `e happy@right at right`。
                    if str(getattr(b, 'at', '') or '').strip():
                        b.at = _want
                    else:
                        b.show = B.apply_position(b.show, _want)

    def _apply_condition(self, b, fields) -> None:
        """条件写回 + 链头守卫重算（拆自 `apply`，2026-11-01）。"""
        # 条件显示（卡片上的「＋ 条件」）。**只在调用方真的给了这个键时才写**：
        # 用 `fields.get(..., b.condition)` 会让"没给"和"给了空串"分不开，
        # 而这两件事的语义完全相反（不改 vs 清空条件）。
        if 'condition' in fields:
            _old_cond = str(getattr(b, 'condition', '') or '').strip()
            _new_cond = str(fields.get('condition') or '').strip()
            b.condition = _new_cond
            # **链头一改，后面那几支的守卫要跟着重算**（P1，2026-10-25）。
            #
            # `elif` / `else` 在表里存的是**累积形式**（`(b) and not (a)`）。
            # 只改链头那一行（`if a == 1:` → `if a == 9:`）之后，文件里
            # 后面那几支的 `elif` 一个字节没动 —— 解析出来的否定项就变成了
            # `not (a == 9)`，与表里存的 `not (a == 1)` 对不上：
            #   · 增量补丁判"条件与要写的拍对不上"→ 整份拒绝；
            #   · 就算放过去，**全量重写**又按表里的旧条件写 —— 两条路的
            #     语义分家（同一章导两次得到两种剧情）。
            # 所以在模型侧就把它们改对：否定项里出现"旧的那支条件"就换成新的
            # （`rechain_condition`，与补丁同一个判据）。
            self._rechain_siblings(b, _old_cond, _new_cond)

    def _rechain_siblings(self, b: Beat, old_cond: str, new_cond: str) -> None:
        """
        改了**链头**那一支的条件之后，把同一链里后面几支的守卫跟着重算。

        判据（一条不满足就什么都不做 —— 让补丁那边去拒绝并说清楚）：

          · 这一拍是链头：`nest_path` 的最内层是 `if:1`（第 1 支就是 `if`）；
          · 它原来那条条件**就是自己那一支的**（没有 `and not (…)` 前缀）——
            只有链头是这样（`elif` 存的都是累积形式）；
          · 新条件非空（清空 = 拆掉这一层 `if`，那是结构改动，不在这里做）。

        后面每一支的否定项里找到"旧的那支条件"就换成新的（`rechain_condition`）。
        换不动（形状认不出）就整体放弃 —— 宁可让补丁拒绝，也不写一半。
        """
        if not old_cond or not new_cond or old_cond == new_cond:
            return
        path = str(getattr(b, 'nest_path', '') or '')
        if not path:
            return
        head, _, last = path.rpartition('>') if '>' in path else ('', '', path)
        if not last.startswith('if:') or last != 'if:1':
            return                             # 不是链头
        prefix_path = (head + '>') if head else ''
        try:
            from storygraph.story.patcher import (is_flattened_condition,
                                                  rechain_condition)
        except Exception as e:                 # noqa: BLE001
            swallow('app.table_edits._rechain_siblings', e)
            return
        if is_flattened_condition(old_cond):
            return                             # 自己那条也是累积形式 → 不是链头
        # 找出同一链、排在它后面的兄弟分支
        later = []
        started = False
        for o in self.peers:
            if o is b:
                started = True
                continue
            if not started:
                continue
            opath = str(getattr(o, 'nest_path', '') or '')
            if prefix_path:
                if not opath.startswith(prefix_path):
                    break
                tail = opath[len(prefix_path):]
            else:
                tail = opath
            if '>' in tail:
                continue                       # 更深一层，不是兄弟
            if not tail.startswith('if:'):
                break                          # 链结束了
            later.append((o, tail))
        if not later:
            return
        plans = []
        for o, tail in later:
            cur = str(getattr(o, 'condition', '') or '').strip()
            if not cur:
                continue
            nxt = rechain_condition(cur, old_cond, new_cond)
            if nxt is None:
                return                         # 认不出 → 整体放弃（不写一半）
            plans.append((o, nxt))
        for o, nxt in plans:
            o.condition = nxt

    def _apply_label(self, b: Beat, new_label: str) -> None:
        """
        `Label` 那一格：**改一格 = 给整章那个 label 改名**（2026-10-24，P0-8）。

        为什么不能只改这一拍：`label 名:` 是**块头**，它下面所有拍共用同一个
        名字。只把一拍的名字改掉，内存里就出现"同一个块里两拍说自己属于两个
        不同 label"的状态 —— 导出时块头只能写一个名字，另一半的拍在产物里
        找不到自己的 label，整体校验判"台词找不到"，**整章导出直接失败**。

        所以这里按"改名"处理：凡是旧名字的拍都跟着改。**新名字已经被别的
        label 占了**时不改（那更像是"把这一拍挪到另一个 label"，属于结构改动，
        交给导出的校验去拒绝，绝不悄悄合并两个 label）。
        """
        if not new_label or new_label == b.label:
            return
        old_label = b.label
        taken = any(o is not b and str(getattr(o, 'label', '') or '') == new_label
                    for o in self.peers)
        if taken:
            return
        for o in self.peers:
            if str(getattr(o, 'label', '') or '') == old_label:
                o.label = new_label

    @staticmethod
    def _fold_dynamic_columns(b: Beat, fields: dict):
        """把「每参数一列」的那些键折进这一拍（见上面那段顺序说明）。"""
        _dyn = {k: v for k, v in (fields or {}).items()
                if isinstance(k, str) and is_dynamic_column(k)}
        if not _dyn:
            return
        from storygraph.story.beat_model import fold_spec_arg_columns
        fold_spec_arg_columns(b, _dyn)

    def _apply_stage(self, b: Beat, fields: dict, stage_loaded):
        """
        `Stage` 那一格的写回规则（**这是"自己吃自己"事故的正解**）。

        背景：`Stage` 是结构化列的**投影**（`beat_to_fields` 把它算出来显示），
        所以把显示值原样写回再 hydrate 就是自己吃自己 —— 实测
        `hide cg03` 会静默变成 `cg03; cg03`（305 拍里 11 拍被改写）。

        两条分支：
          · **用户真的改了这一格**（`typed` 非空且 ≠ 载入时的原文）：
            以人写的为准，并且**先清掉结构化舞台字段再 hydrate**，
            否则"旧投影 + 新文本"会叠加成两份指令。
          · **没改**：重新投影，并把"结构化列永远表达不了"的那部分
            （`[停顿] 1.0` 之类）原样留下 —— 否则打开卡片点一下下一句，
            作者手写的东西就没了。

        为什么"空值"不算手改：把格子清空是很容易的误操作，而它的后果是
        抹掉这一拍的 scene/show/hide。语料里"我在 Stage 框里删光"不是
        一种需求，所以空值一律走重新投影（**永远不因为空 Stage 丢数据**）。

        ⚠ **还得知道"载入时那一格是什么"才敢判手改**（2026-10-05 实测修）：
        `stage_loaded=None` 时旧代码拿 `b.stage` 当基线，而界面显示的那一格
        往往是**投影**（`beat.stage` 为空 → 显示 `compose_stage(...)`）——
        两者不相等，于是"用户手改了 Stage"被误判，**这一拍刚加上的立绘
        （`b.show`）会被整段清掉**。一个"没告诉过我们基线"的调用方，
        没资格说这是人改的：那就老老实实走重新投影。
        """
        typed = None
        if 'Stage' in fields:                # 表单真的给了这一格（高级区展开）
            typed = str(fields.get('Stage') or '').strip()
        loaded = (b.stage if stage_loaded is None
                  else str(stage_loaded or '')).strip()

        if typed and stage_loaded is not None and typed != loaded:
            b.stage = typed
            for attr in ('scene', 'scene_at', 'show', 'hide', 'at',
                         'with_', 'transform', 'stage_order'):
                setattr(b, attr, '')
            # **立即可重建**（2026-10-24 审计 P1-2）：这一格现在是**完整**
            # 舞台投影（见 `app.beats.stage_cell`），能认出来的场景/立绘/
            # 退场/转场全部重建；认不出的（`[停顿]` / 原生语句）由
            # `stage_only_ops` 原样留在 `b.stage` 里带走。
            #
            # 从前这里只清空、等 `apply` 末尾那次 hydrate —— 那条路本身没坏，
            # 坏的是格子当时只显示"结构化列表达不了的那一条"，于是"清空 +
            # 从一条重建"必然把其余舞台指令丢掉。现在先重建再把 stage
            # 归一成"表达不了的部分"，与没手改那条分支同一个出口。
            try:
                hydrate_from_dsl([b], self.symbols)
            except Exception as e:          # noqa: BLE001 - 解析失败也别拦住保存
                swallow('editor._apply_stage.rehydrate', e)
            parts = [t for t in (compose_stage_extra(b, self.symbols),
                                 stage_only_ops(typed, self.symbols)) if t]
            b.stage = '; '.join(parts)
            try:
                b._hydrated_stage = b.stage
            except Exception:               # noqa: BLE001 - Beat 不该是槽位
                swallow('editor._apply_stage')
            return

        # 没手改：结构化列是唯一真源 → 重新投影；
        # 表达不了的部分（pause / 认不出的原生语句）从原文里捞回来。
        parts = [t for t in (compose_stage_extra(b, self.symbols),
                             stage_only_ops(loaded, self.symbols)) if t]
        b.stage = '; '.join(parts)
        # 投影已经在结构化字段里了 → 标记成"已吸过"，
        # 免得下面那次 hydrate 又把它吸第二遍（见 `hydrate_from_dsl`）。
        try:
            b._hydrated_stage = b.stage
        except Exception:                    # noqa: BLE001 - Beat 不该是槽位
            swallow('editor._apply_stage')
