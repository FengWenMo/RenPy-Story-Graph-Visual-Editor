"""
`renpy_parser` 的**语句筛查**一族（`_stmt_*`）—— 2026-10-11 从 `renpy_parser.py` 提取成 mixin
========================================================================

为什么单独一个模块（见 `docs/archive/重构方案_拆分_renpy_parser_2026-10-11.md`）：
`RenPyParser` 曾经一个类 50 个方法；这一族（27 方法 / 544 行）聚类最干净、
又几乎只依赖节点类与几个纯 helper（第①步已把它们搬进 `renpy_nodes`）。

⚠ 搬家纪律：**逐字照搬**，一个字没改（连注释与空行）——
这样 `git diff` 一眼能看出"只是搬家"。

⚠ 这一族大量用到 `self._RE_*`（`RenPyParser` 的**类属性**）与
`self.errors` / `self.warnings`（它在 `__init__` 里设）：
实例属性查找会沿 MRO 落到 `RenPyParser` 上，所以 mixin 里照写即可。
"""

from __future__ import annotations

import re
from storygraph.core.vocabulary import SHOW_CLAUSE_KEYWORDS
from storygraph.parsing.renpy_nodes import (
    AudioNode,
    CallNode,
    CameraNode,
    CommentNode,
    DefineNode,
    ExtendNode,
    HideNode,
    IfNode,
    ImageDefNode,
    JumpNode,
    LoopNode,
    MenuNode,
    PythonNode,
    RawNode,
    ReturnNode,
    SayNode,
    SceneNode,
    ShowNode,
    UnknownBlockNode,
    VoiceNode,
    _AUDIO_PARAM_WORDS,
    _pick_str,
    _scan_balanced_close,
    _scan_toplevel_colon,
    _skip_string,
    split_string_speaker,
)


def _say_tail(text: str, pos: int) -> str:
    """
    台词那个引号串**之后**还剩什么（去掉行尾注释；只有空白 → 空串）。

    行尾注释不算内容：`e "嗨"  # 这里要给个特写` 是**一句台词 + 一行注释**，
    注释由注释通道自己收，不能因为它在就把这句降级成原样行。
    """
    rest = str(text or '')[max(0, int(pos)):].strip()
    if not rest or rest.startswith('#'):
        return ''
    # 去掉行尾注释（`(no_skip=True)  # 别忘了`）：只在**引号外**找 `#`，
    # 而这里剩下的部分可能含引号（`("#f00")`），所以先按字符串跳一遍。
    i, n = 0, len(rest)
    while i < n:
        ch = rest[i]
        if ch in ('"', "'"):
            i, _closed = _skip_string(rest, i)
            continue
        if ch == '#':
            return rest[:i].strip()
        i += 1
    return rest


def _say_clause(tail: str) -> bool:
    """
    这段尾巴是 Ren'Py 的**合法 say 子句**吗？

    只认四种（写死，不猜）：
      · `(kwargs)` —— 角色参数覆盖，`(what_color="#f00")` 就是"给这句台词上色"；
      · `with <转场>`；
      · `id <名字>`（翻译/语音标识）；
      · `nointeract`。
    其余一律不算 —— 那说明这一行根本不是台词（见 `_stmt_say` 的 ⓪）。
    """
    t = str(tail or '').strip()
    if not t:
        return True
    if t.startswith('('):
        close = _scan_balanced_close(t, 0)
        return close != -1 and not t[close + 1:].strip()
    if t == 'nointeract':
        return True
    m = re.match(r'^(?:with|id)\s+\S+$', t)
    return bool(m)


def _after_second_string(text: str) -> int:
    """`"系统" "提示"` 里**第二个**字符串闭合之后的索引（认不出 → len(text)）。"""
    text = str(text or '')
    end, closed = _skip_string(text, 0)
    if not closed:
        return len(text)
    i = end
    while i < len(text) and text[i] in ' \t':
        i += 1
    if i >= len(text):
        return len(text)
    end2, closed2 = _skip_string(text, i)
    return end2 if closed2 else len(text)


class StatementScreens:
    """`_stmt_*` 一族（搬自 `RenPyParser`，逐字未改）。"""

    def _stmt_comment(self, line, text):
        """整行注释。

        注释不是"可忽略的噪声"：`# 表情A6，距离近` 是文案给美术的意图，
        往返丢了它，美术就不知道这张立绘该是什么情绪。
        """
        if line.is_comment:
            return CommentNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=line.text.strip(),
            )
        return self._NO_MATCH

    def _stmt_label(self, line, text):
        """label 定义。"""
        if self._RE_LABEL_HEAD.match(text):
            return self._parse_label(line, text)
        return self._NO_MATCH

    def _stmt_define(self, line, text):
        """define 声明（含 Character 参数）。"""
        m = self._RE_DEFINE.match(text)
        if not m:
            return self._NO_MATCH
        target = m.group(1)
        rhs = m.group(2).strip()
        value_type = ''
        args_str = ''
        fm = self._RE_DEFINE_FUNC_HEAD.match(rhs)
        if fm:
            open_idx = fm.end() - 1
            close_idx = _scan_balanced_close(rhs, open_idx)
            if close_idx != -1:
                tail = rhs[close_idx + 1:].strip()
                if not tail:
                    value_type = fm.group(1)
                    args_str = rhs[open_idx + 1:close_idx]
                else:
                    tail_display = tail[:60] + (
                        '...' if len(tail) > 60 else ''
                    )
                    self.warnings.append(
                        f"{line.filename}:{line.line_number}: "
                        f"define 右侧存在额外表达式，"
                        f"未解析其参数: {tail_display!r}"
                    )

        node = DefineNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            target=target,
            value_type=value_type,
            args_str=args_str,
            rhs=rhs,
        )
        if self._is_character_type(value_type):
            self._parse_character_args(node, args_str)
        return node

    def _stmt_menu(self, line, text):
        """menu 块头（`menu [名] [(参数)]:` —— 三样都建模，见 `MenuNode`）。"""
        m_menu = self._RE_MENU_HEAD.match(text)
        if not m_menu:
            return self._NO_MATCH
        head = self._scan_menu_header(text, m_menu)
        if head is None:
            return self._NO_MATCH
        header_caption, header_name, header_args = head
        node = MenuNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            name=header_name,
            args=header_args,
        )
        sub_caption, node.options, node.extras = self._parse_menu(line.subblock)
        if header_caption and sub_caption:
            node.caption = header_caption + '\n' + sub_caption
        else:
            node.caption = header_caption or sub_caption
        return node

    def _stmt_if(self, line, text):
        """if 块头（条件缺顶层冒号时**放弃解析**，返回 `None`）。"""
        m = self._RE_IF_HEAD.match(text)
        if not m:
            return self._NO_MATCH
        colon = _scan_toplevel_colon(text, m.end())
        if colon == -1:
            self.errors.append(
                f"{line.filename}:{line.line_number}: "
                f"if 条件缺少顶层冒号"
            )
            return None
        cond = text[m.end():colon].strip()
        node = IfNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
        )
        node.branches.append((cond, self._parse_block(line.subblock)))
        return node

    def _stmt_jump_expression(self, line, text):
        """动态跳转 `jump expression`。

        动态跳转无法静态断言目标，但语句本身必须保真 ——
        原样存成 Raw，导出时原样回写，否则控制流被悄悄改掉。
        """
        if self._RE_JUMP_EXPR.match(text):
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='jump_expression',
            )
        return self._NO_MATCH

    def _stmt_call_expression(self, line, text):
        """动态调用 `call expression`（同动态跳转，保真存 Raw）。"""
        if self._RE_CALL_EXPR.match(text):
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='call_expression',
            )
        return self._NO_MATCH

    def _stmt_call_screen(self, line, text):
        """`call screen`（UI 调用，不产生 label 跳转边，但仍是演出一步）。"""
        if self._RE_CALL_SCREEN.match(text):
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='call_screen',
            )
        return self._NO_MATCH

    def _stmt_jump(self, line, text):
        """静态跳转。"""
        m = self._RE_JUMP.match(text)
        if not m:
            return self._NO_MATCH
        return JumpNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            target=m.group(1),
        )

    def _stmt_call(self, line, text):
        """静态调用（含 `call X from Y` 的存档点标识与 `call X(…)` 的参数）。"""
        m = self._RE_CALL.match(text)
        if not m:
            return self._NO_MATCH
        # `call foo(1, two=True) from _call_foo`：参数表**必须留住**
        # （2026-10-18，审计 P0-4）。认不出配平的括号就不吞（宁可原样落 raw）。
        args = ''
        i = m.end()
        while i < len(text) and text[i] in ' \t':
            i += 1
        if i < len(text) and text[i] == '(':
            close = _scan_balanced_close(text, i)
            if close == -1:
                return self._NO_MATCH
            args = text[i:close + 1]
        return CallNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            target=m.group(1),
            # `call X from Y`：Y 是**存档点标识**（同一个 label 被多处
            # call 时靠它区分存档属于哪一处）。丢了它对老存档不友好。
            from_clause=(self._RE_CALL_FROM.search(text).group(1)
                         if self._RE_CALL_FROM.search(text) else ''),
            args=args,
        )

    def _stmt_return(self, line, text):
        """return。"""
        if self._RE_RETURN.match(text):
            return ReturnNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
            )
        return self._NO_MATCH

    def _stmt_dollar(self, line, text):
        """`$ python` 单行（含**括号续行**）。"""
        m = self._RE_DOLLAR.match(text)
        if not m:
            return self._NO_MATCH
        # `$ x` 与 `$x` 都合法；记下原文写法，导出时原样还回去
        raw_after = text[1:]
        # ⚠ 2026-10-18 修：这里从前用 `m.group(1)`，而 `_RE_DOLLAR` 是
        # `^\$\s*(.+)` —— `.+` **不跨行**，于是 logical-line 层好不容易
        # 合并进来的括号续行被截掉：
        #
        #     $ y = (
        #         1 + 2
        #       )
        #
        # 解析出来只剩 `y = (`，产物是一句**语法错误**的 `$ y = (`。
        # 括号续行在真实工程里很常见（`renpy.pause(\n    1.0,\n    hard=True)`）。
        # 合并结果（`text`）里已经带着换行与续行缩进，直接整段取走；
        # 续行的**绝对缩进**要相对这一行的缩进左移，否则渲染时整块再加一次
        # 基准，每导出一次就多缩一截（与 `_shift_continuations` 同一条口径）。
        from storygraph.parsing.renpy_parser import _shift_continuations
        code = _shift_continuations(raw_after.strip(), line.indent)
        return PythonNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            code=code,
            dollar_space=bool(raw_after[:1].isspace()),
        )

    def _stmt_python_block(self, line, text):
        """`python:` 块 —— 含**顶层**的 `init python:` / `python early:`。

        2026-10-05（拓展边界③）：以前这里的正则只认 `python…:`，于是顶层
        `init python:` 一路落到 `_stmt_unmodeled`，被当成"不在演出流内的
        未建模块"**跳过**（只留一条 warning）。后果是 `.rpy → 表 → .rpy`
        之后整块代码消失，而产物语法完全合法、门禁拦不住
        （真实项目 `whackamole.rpy`：2 个块 / 56 行游戏逻辑没了）。

        现在它产出 `PythonNode`（带块头原文），由 `ParseResultBuilder`
        按"在不在 label 里"分流：label 内 → 拍；顶层 → `file_blocks`。
        """
        m = self._RE_PYTHON.match(text)
        if not m:
            return self._NO_MATCH
        code = self._collect_python_block(line)
        return PythonNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            code=code,
            header=text[:m.end()].strip(),
        )

    def _stmt_layeredimage(self, line, text):
        """`layeredimage`（合成立绘）。"""
        m = self._RE_LAYEREDIMAGE.match(text)
        if m:
            return self._parse_layeredimage(line, text, m)
        return self._NO_MATCH

    def _stmt_image_def(self, line, text):
        """`image` 声明。"""
        m = self._RE_IMAGE_DEF.match(text)
        if not m:
            return self._NO_MATCH
        return ImageDefNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            name=m.group('name').strip(),
            expr=m.group('rhs').strip(),
        )

    def _stmt_screen_op(self, line, text):
        """`show/hide screen`（UI 操作，不是图像引用）。"""
        if self._RE_SCREEN_SHOW.match(text):
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='screen',
            )
        return self._NO_MATCH

    def _stmt_show(self, line, text):
        """`show` 立绘。"""
        if not self._RE_SHOW_HEAD.match(text):
            return self._NO_MATCH
        parsed = self._parse_show_scene(text, is_scene=False)
        if parsed and parsed['image']:
            for w in parsed['warnings']:
                self.warnings.append(
                    f"{line.filename}:{line.line_number}: {w}"
                )
            return ShowNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                image=parsed['image'],
                at=parsed['at'],
                as_=parsed['as_'],
                with_=parsed['with_'],
                zorder=parsed['zorder'],
                onlayer=parsed['onlayer'],
                behind=parsed['behind'],
                is_expression=parsed['is_expression'],
            )
        return self._NO_MATCH

    def _stmt_scene(self, line, text):
        """`scene` 背景。"""
        if not self._RE_SCENE_HEAD.match(text):
            return self._NO_MATCH
        parsed = self._parse_show_scene(text, is_scene=True)
        # ⚠⚠ **认不出就原样抄**（2026-10-15，用户审核指出 —— 铁律 #3）：
        #   `SceneNode` 只携带 image / at / with_ / onlayer / is_expression，
        #   **没有 `as_` / `zorder` / `behind` 三个载体**（`Beat` 侧也没有，
        #   渲染器没有东西可发射）。自从 `_SCENE_KEYWORDS` 与 show 同口径之后
        #   （官方语法：scene 取同一批 properties），这三个字段会被**算出来再丢掉**：
        #
        #     改前：`scene bg behind x` → image='bg behind x'（切错，但**错得可见**）
        #     若不管：`scene bg behind x` → image='bg'、behind=['x'] 被丢（**丢得无声**）
        #
        #   "丢得无声"是保真性最忌讳的那一档，所以这里**宁可认不出**：
        #   只要有这三个字段就返回 `_NO_MATCH` → 整行落进 `raw` 通道**逐字保留**
        #   （渲染时原样吐回，一个字节不动）。
        #
        # ⚠ 真机命中数：`scene … as/behind/zorder` **0 次** —— 所以"通用性"的损失是 0。
        #   等哪天真要结构化这三个字段，再把这条口径翻回来（要动
        #   SceneNode + Statement payload + Beat + 渲染，五处起）。
        #   判据：`tests/test_show_clause_words.py::test_scene_的_as_behind_zorder_
        #   走逐字通道不丢字`（往返逐字节相同）。
        if parsed and (parsed['as_'] or parsed['behind']
                       or parsed['zorder'] is not None):
            return self._NO_MATCH
        if parsed and parsed['image']:
            for w in parsed['warnings']:
                self.warnings.append(
                    f"{line.filename}:{line.line_number}: {w}"
                )
            return SceneNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                image=parsed['image'],
                at=parsed['at'],
                with_=parsed['with_'],
                onlayer=parsed['onlayer'],
                is_expression=parsed['is_expression'],
            )
        return self._NO_MATCH

    def _stmt_hide(self, line, text):
        """`hide`。"""
        m = self._RE_HIDE_HEAD.match(text)
        if not m:
            return self._NO_MATCH
        rest = text[m.end():].strip()
        with_ = ''
        mw = re.search(r'\bwith\s+([\w.]+)', rest)
        if mw:
            with_ = mw.group(1)
            rest = rest[:mw.start()].strip()
        # ⚠⚠ **认不出就原样抄**（2026-10-15，同 `_stmt_scene` 那条）：
        #   官方 `hide` 的子句只有 `onlayer`（+ `with`），而 `HideNode`
        #   只携带 image / with_ —— **没有 `onlayer` 载体**。不管的话
        #   `hide bg onlayer master` 会把 `onlayer master` 当成图像名的一部分
        #   → 变成一条**假断链**（`bg onlayer master` 查不到）。
        #   所以：尾部只要出现**任何 show/scene/hide 子句词**（at/as/with/behind/
        #   onlayer/zorder），就 `_NO_MATCH` → 整行落 `raw` **逐字保留**。
        #   真机 `hide … onlayer/zorder/behind/at/as` 命中 0 次，通用性损失是 0。
        if any(tok.rstrip(',') in SHOW_CLAUSE_KEYWORDS
               for tok in rest.split()):
            return self._NO_MATCH
        return HideNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            image=rest,
            with_=with_,
        )

    def _stmt_audio(self, line, text):
        """`play / queue / stop music|sound`。"""
        m = self._RE_AUDIO.match(text)
        if not m:
            return self._NO_MATCH
        action = m.group('action')
        rest = (m.group('rest') or '').strip()
        name = ''
        # `stop music fadeout 1.0`：stop 后面没有"要播的资产"，
        # 全是参数。以前会把 fadeout 当成音频名，于是校验报
        # "音频 'fadeout' 未登记"（真实项目一次 11 条）。
        if action == 'stop':
            parts = rest.split()
            if parts and parts[0] in _AUDIO_PARAM_WORDS:
                name, rest = '', rest
            elif parts:
                name = parts[0]
                rest = ' '.join(parts[1:])
        else:
            qm = re.match(r'^["\']([^"\']+)["\']', rest)
            if qm:
                name = qm.group(1)
                rest = rest[qm.end():]
            else:
                parts = rest.split()
                # `play music fadein 1.0` 这种没有名字的写法
                if parts and parts[0] in _AUDIO_PARAM_WORDS:
                    name = ''
                else:
                    name = parts[0] if parts else ''
                    rest = ' '.join(parts[1:])

        def _num(pat):
            mm = re.search(pat, rest)
            try:
                return float(mm.group(1)) if mm else 0.0
            except ValueError:
                return 0.0

        # **没建模的其余参数**（2026-10-18）：从前只认 fadein/fadeout/loop，
        # `play sound "x" volume 0.5` 的 `volume 0.5` 在往返里静默消失 ——
        # 音量调整没了，产物照样"能跑"，没有任何提示。这里把已建模的词
        # 从 rest 里剜掉，剩下的**逐字**带走（`if_changed` / `pan` /
        # `relative_volume` / `noloop`… 全都不需要工具理解）。
        extra = rest
        extra = re.sub(r'\bfadein\s+[^\s,]+', ' ', extra)
        extra = re.sub(r'\bfadeout\s+[^\s,]+', ' ', extra)
        extra = re.sub(r'\bloop\b', ' ', extra)
        extra = ' '.join(extra.split())

        return AudioNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            action=action,
            channel=m.group('channel'),
            name=name,
            fadein=_num(r'\bfadein\s+([\d.]+)'),
            fadeout=_num(r'\bfadeout\s+([\d.]+)'),
            loop=bool(re.search(r'\bloop\b', rest)),
            extra=extra,
        )

    def _stmt_voice(self, line, text):
        """`voice`。"""
        m = self._RE_VOICE.match(text)
        if not m:
            return self._NO_MATCH
        return VoiceNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            name=_pick_str(m, 1, 2, 3),
        )

    def _stmt_extend(self, line, text):
        """`extend` 续写。"""
        if not self._RE_EXTEND_HEAD.match(text):
            return self._NO_MATCH
        m = self._RE_EXTEND_STR.match(text)
        if m:
            return ExtendNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=_pick_str(m, 1, 2),
            )
        return self._NO_MATCH

    def _stmt_loop(self, line, text):
        """
        `while <条件>:` / `for <目标> in <可迭代>:` —— **T2 → T0b → T0**
        （D2 起；2026-10-07 第三轮把块体也结构化）。

        为什么要专门接住它（真机实测）：`sr_general/game/minigame/mini_game1.rpy:50`
        的**游戏主循环**就是这个形状，而它以前连头带块体被整块丢掉。M1 之后它
        进了保真通道（内容安全了），但报告里只能反反复复说"这是**给工具作者**
        的待办" —— 一个**永远还不掉**的待办不是待办，是欠账。

        D2 只做到一半：头行可编辑、块体仍是一坨逐字文本 —— 编剧还是改不到
        循环体里那句台词。**这一轮把块体也解析成语句**（`body_nodes`）：那正是
        "给拍模型加作用域"的第一步。作用域由 `_walk` 标的
        `nest_path='…>loop:<n>'` 承载（随隐藏列 `Nest` 走），渲染层据此把
        扁平拍重新归组成 `while …:` 块。

        ⚠ 判据**必须以冒号收尾**：`for "台词"`（说话人恰好叫 `for`）与
        `while "x"` 都**必须**仍然是台词（`tests/test_loop_model.py` 钉着）。
        """
        if not self._RE_LOOP_HEAD.match(text):
            return self._NO_MATCH
        return LoopNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            header=text,
            # 块体 → AST 节点（**结构化那条路**）。`_parse_block` 与
            # `if` / `menu` 分支用的是同一个入口，所以块体里的东西
            # （台词 / `$` / 嵌套 if / 嵌套 menu / 未建模块）走的是**同一套**
            # 解析与同一条保真通道 —— 不在这里另写一遍分派，
            # 也不另外抄一份"逐字块体"（那是第二套真相）。
            body_nodes=self._parse_block(line.subblock),
            text=text,
        )

    def _stmt_camera(self, line, text):
        """
        3D 舞台的 `camera` 块 —— **将 T2 升到 T0b**（2026-10-07）。

        为什么它算"欠账"：`RENPY_FLOW_BLOCK_HEADS` 里一直有 `camera`
        （工具**认识**它是流程块），但没建模 —— 于是它落在
        `raw_kind='known_block'` 那一桶，报告里被算成 **T2（只读）**，
        并进了"**催我们建模**"的清单。而它和循环块一样，是**能接住的**：
        头行只有"层名"一个语义位，块体是 ATL 属性（Transform 的领域）。

        所以处理方式与 `while` / `for` 完全一致（**同一套约定，不发明第二套**）：
        头行结构化（`CameraHeader` 列，可编辑、可校验）、块体逐字（`Opaque`）、
        等级 `T0b` —— **不记 T0**：ATL 属性没有被结构化编辑。

        ⚠ 官方语法（3dstage.html）：`camera` 后**只能**跟一个层名，然后必须
        有块体（`perspective True` 之类）。所以判据是"以冒号收尾"，与
        `for "台词"` 那条防误伤规则同源。
        """
        m = self._RE_CAMERA_HEAD.match(text)
        if not m:
            return self._NO_MATCH
        block_lines = self._unknown_block_lines(line)
        head_lines = len((line.text or '').split('\n'))
        return CameraNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            header=text,
            layer=(m.group(1) or ''),
            body=block_lines[head_lines:],
            text='\n'.join(block_lines),
        )

    def _stmt_say(self, line, text):
        """台词（**兜底**：前面的分支都没接住，才算台词）。"""
        # ⓪ **台词不许带"尾巴"**（2026-10-18，P0：静默删代码）。
        #
        #     Ren'Py 的 say 语句到那个引号串就**结束**了。`_RE_SAY` 只
        #     `match()` 到引号为止，后面还剩什么都**没看** —— 于是
        #
        #         e "嗨" with dissolve        →  e "嗨"
        #         e "嗨" id foo               →  e "嗨"
        #         e "嗨" (what_color="#f00")  →  e "嗨"
        #         styled "红色" "#ff0000" 32  →  styled "红色"   ← 颜色/字号没了
        #
        #     最后一行正是"文字类自定义语句"的典型长相（花字：内容 + 颜色 +
        #     字号），而它在导出时**一声不响**丢掉两个参数 —— 剧本还是能跑，
        #     只是颜色和字号没了，谁也不会立刻发现。
        #
        #     判据：引号串之后**只允许空白、注释、或四种合法 say 子句**
        #     （`(kwargs)` / `with` / `id` / `nointeract`，见 `_say_clause`）。
        #     合法子句照旧算台词，但**尾巴要跟着走**（`SayNode.tail` →
        #     `Beat.say_tail` → 原样写回）；别的东西 → 这一行不是台词，
        #     交回 `_stmt_unmodeled`（`RawNode`）**逐字保真**；
        #     如果项目声明过这个语句，`annotate_spec_statements` 会当场把它
        #     认领成 `spec_name` → 卡片上就长出那几格参数。
        #
        #     ⚠ 代价（写在这里，免得下一个人以为是疏忽）：`e "嗨" with dissolve`
        #     这类合法尾巴现在会**保留**（不再丢），但它仍是台词；真正被降级
        #     成原样行的是"引号后面还跟着别的东西"（`styled "红色" "#f00" 32`）。
        # ⓿ **台词不许带块**（2026-10-07，M1 止血）。
        #
        #     `mycue "闪电" intensity=3:` 的长相与台词**一模一样**，
        #     `_RE_SAY` 会把它读成"mycue 说了一句 闪电" —— 冒号与
        #     `intensity=3:` 一起丢掉，紧随的块体整段消失，
        #     产物里长出一句**不存在的台词**（静默改剧情）。
        #
        #     Ren'Py 的 say 语句**不可能**开块，所以"带子块"就是
        #     "这不是台词"的硬证据：放弃匹配，交给 `_stmt_unmodeled`
        #     变成 `UnknownBlockNode`（整块保真）。
        #
        #     判据要求子块里有**非注释**内容：只有注释被多缩进两格的
        #     话（`e "台词"` 后面跟一行缩进歪掉的注释）不该改变语义。
        if line.subblock and any(
                (getattr(ln, 'text', '') or '').strip()
                and not getattr(ln, 'is_comment', False)
                for ln in line.subblock):
            return self._NO_MATCH
        # ⓪′ **带 Python 字符串前缀的不是台词**（2026-10-18，审计 P0-6）。
        #
        #     `u"统一"` / `r"…"` / `b"…"`：Ren'Py 的 say 只接受 who? + 裸字符串
        #     字面量，前缀式**引擎不认**（SDK lint：`expected statement.`）。
        #     而 `_RE_SAY` 的 who 组是个标识符正则，于是 `u` 被当成**说话人**、
        #     产物成了 `u "统一"` —— 一句凭空的"角色 u 说『统一』"，语义被静默
        #     改掉。原文既然是错的（GIGO），工具的义务只有一条：**不要改它**。
        #     放弃匹配 → `_stmt_unmodeled` → 逐字保真。
        if self._RE_SAY_PREFIX.match(text):
            return self._NO_MATCH
        # ① **字符串说话人**优先：`"系统" "提示"`（Ren'Py 合法写法）。
        #    不先接住它，第二个字符串就被整句丢掉（见 `split_string_speaker`）。
        pair = split_string_speaker(text)
        if pair is not None:
            who_raw, what = pair
            tail = _say_tail(text, _after_second_string(text))
            if not _say_clause(tail):
                return self._NO_MATCH
            return SayNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                who=who_raw,
                what=what,
                tail=tail,
            )
        m = self._RE_SAY.match(text)
        if not m:
            return self._NO_MATCH
        tail = _say_tail(text, m.end())
        if not _say_clause(tail):
            return self._NO_MATCH
        who = m.group(1) or ''
        if who in self._IGNORED_SAY_HEADS:
            return None
        what = _pick_str(m, 2, 3)
        return SayNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            who=who,
            what=what,
            tail=tail,
        )

    def _stmt_transform(self, line, text):
        """`transform` 定义。

        这是"支持自定义变换"的关键：不解析它，表格里的 At / Transform
        列就永远选不到项目自己写的变换。
        """
        if not self._RE_TRANSFORM.match(text):
            return self._NO_MATCH
        node = self._parse_transform(line, text)
        if node is not None:
            return node
        return self._NO_MATCH

    def _stmt_unmodeled(self, line, text):
        """未建模语句的兜底。

        到这里还没被任何分支接住的语句，说明模型没覆盖它。与其
        静默丢弃（这正是"解析不够强大"的根源），不如原样保存：
        渲染时逐字回写，保证 rpy↔表格 往返不丢程序员的代码。

        **带子块的也要收**（2026-10-07，M1 止血）。从前这里
        `return None` —— 只留一条没人看的 warning，于是 label 内的
        自定义块连头带块体从模型里消失，全量重生成就把这段剧情丢了，
        而门禁**无从得知**它存在过（内容没进任何通道）。现在整块
        进保真通道（`UnknownBlockNode`），并按 `raw_kind` 计数，
        好让门禁与体检报告说得出口。

        ⚠ 这条与顶层那段是**同一枚硬币的两面**：顶层（label 之外）
        由 `_top_level_region()` 按原文整段带出；label 之内由这里带出。
        """
        if not line.subblock:
            return RawNode(
                line_number=line.line_number,
                indent=line.indent,
                filename=line.filename,
                text=text,
                raw_kind='unmodeled',
            )
        # 整块原文：头一行 + 块体，**整块最小缩进归零**（相对层级保留）——
        # 与 `_top_level_region` 同一套口径。不能用 `line.indent` 当基准：
        # 括号续行（`slots = [\n    "potion",\n]`）被 lexer 并成了**一条**
        # 逻辑行，续行的缩进是**绝对**的，按首行缩进去切只会把续行留在
        # 原地，往返一次就多缩一层（实测 12 → 16 空格）。
        block_lines = self._unknown_block_lines(line)
        head_lines = len((line.text or '').split('\n'))
        # 保真通道**同一条**（`Beat.opaque`），但要不要"告状"要分开：
        # `while` / `for` 这类是 Ren'Py 标准流程块（T2，工具认识形状），
        # 项目自定义的是真的不懂（T3）。判据是纯函数，放 `core.vocabulary`
        # ——两端各自调用不会漂移（见 `RENPY_FLOW_BLOCK_HEADS`）。
        from storygraph.core.vocabulary import is_known_flow_block
        return UnknownBlockNode(
            line_number=line.line_number,
            indent=line.indent,
            filename=line.filename,
            text='\n'.join(block_lines),
            raw_kind=('known_block' if is_known_flow_block(text)
                      else 'unknown_block'),
            head=text,
            block_raw=block_lines[head_lines:],
        )
