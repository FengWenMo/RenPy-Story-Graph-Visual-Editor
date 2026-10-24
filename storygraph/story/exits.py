# -*- coding: utf-8 -*-
"""
每个 label 的**出口**是什么样的 —— 纯只读分析，不写任何文件。

## 为什么要这一层（2026-10-23，G0）

图编辑器能不能接线，取决于"这一章演完之后会发生什么"：

| 章末最后一句 | 实际会发生什么 | 能不能包一层 `_control` |
|---|---|---|
| `return` | 回到调用者 | ✅ 能（call 它，返回后接着跳） |
| `jump X` | 直接穿出去 | ❌ **不能**：`call 这章` 之后 jump 走掉，永远回不到 `_control` |
| `call X`（后面没有 `return`） | **落空**（见下） | ❌ 不能 |
| `$ renpy.full_restart()` | 游戏重启/结束 | ❌ 不需要（没有"下一步"） |
| 台词 / `show` / `menu` 收尾 | **落空**（见下） | ❌ 不能 |
| label 里一条语句都没有 | 落空 | ❌ 不能 |

### "落空"是什么，为什么它是这里最危险的一档

Ren'Py 官方文档两处原话：

> "If there is no jump statement at the end of the block associated with the label,
>  Ren'Py will continue on to the next statement."（quickstart）

> "it simply continues on to the following label block...
>  **as if the label statements weren't there at all**."（language wiki）

也就是说：**label 演完不写 `return`，玩家会顺路撞进同文件的下一个 label。**
而引擎 lint **一个字都不说**（2026-10-23 实测：落空 / 章末 jump / menu 嵌在 if 里，
三个样本全部 `ok=True`、零警告）。

所以"这一章有没有出口"必须由工具自己认 —— 这就是本模块存在的全部理由。

### 一个容易写错的点（我第一版就写错了）

**`call` 后面不写 `return`，不等于隐式 `return`。**
`call sub` 执行完回到这一行之后，如果后面没有语句，label 就结束了 —— 一样落空。
所以"末尾是 call"归到 `call_without_return`（= 落空风险），**不**归到 `return`。
（2026-10-23 修：定案文档 §6.7 里那条"call 结尾可包"是错的，以本模块为准。）

## 不做什么

- 不判断"这段剧情好不好"——那是人的事；
- 不区分"条件分支里每条都 return 了"这种完美情况：只看**最后一条语句**并如实标注
  `last_condition`，让人自己判断（`confidence` 字段说明可靠度）；
- 不改任何文件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.core.statement import StatementKind

# ---------------------------------------------------------------------------
#  出口六档
# ---------------------------------------------------------------------------

#: 显式 `return`：唯一"安全"的出口，能包 `_control`。
EXIT_RETURN = 'return'
#: 章末 `jump X`：这章自己写了"去哪"，不能再包一层。
EXIT_JUMP = 'jump'
#: 章末 `call X` 但后面没有 `return`：**会落空**（不是隐式 return）。
EXIT_CALL_NO_RETURN = 'call_without_return'
#: 终局语句（`renpy.full_restart()` / `renpy.quit()`）：没有"下一步"。
EXIT_TERMINATE = 'terminate'
#: 以 `menu` 收尾：出口由**每个选项各自**决定，不能拿最后一条代表全体。
#: （2026-10-23 实测抓到的错：折平之后最后一条是最后一个选项里的 `jump`，
#:  按 `EXIT_JUMP` 报会漏掉别的选项 —— 那正是"猜一个名字比说不知道更坏"。）
EXIT_MENU = 'menu'
#: **运行时决定去哪**：`$ renpy.jump(x)` / `$ renpy.call(x)` / `jump expression x`。
#:
#: 为什么单独一档（2026-10-23，用户指出的第三层边界）：
#:   静态分析**确实判不出来**它去哪。判不出来就如实说，**不能塞进"落空"** ——
#:   那是把一个"可能很正常"的写法报成缺陷，用户会开始不信所有报警。
#:   Ink Graph 敢拒绝删除 function call，是因为它确知会破坏返回栈；
#:   我们对 `renpy.jump(变量)` 没有这种确知，所以正确的动作是承认不知道。
EXIT_DYNAMIC = 'dynamic'
#: 落空：台词 / 演出 / menu 收尾 —— 会落到同文件的下一个 label。
EXIT_FALLTHROUGH = 'fallthrough'
#: label 里一条语句都没有。
EXIT_EMPTY = 'empty'

ALL_EXITS = (EXIT_RETURN, EXIT_JUMP, EXIT_CALL_NO_RETURN, EXIT_TERMINATE,
             EXIT_MENU, EXIT_DYNAMIC, EXIT_FALLTHROUGH, EXIT_EMPTY)

#: 只有这一档能安全地包 `<label>_control`。
WRAPPABLE = (EXIT_RETURN,)

# ---------------------------------------------------------------------------
#  label 的角色（决定"要不要管它"）
# ---------------------------------------------------------------------------

#: 内容章（编剧写的）：要判断出口、要判断能不能包。
ROLE_CONTENT = 'content'
#: 编排层（`*_control`）：它自己就是包装层，不再被包。
ROLE_CONTROLLER = 'controller'
#: Ren'Py 标准入口（`start` / `splashscreen` …）：引擎自己会进，不需要包。
ROLE_ENTRY = 'entry'

#: Ren'Py 认的入口 label。与 `graph.analysis.ENTRY_LABELS` 同源 ——
#: 两处不一致会出现"图上是入口、出口分析说是普通章"这种自相矛盾。
ENTRY_LABELS = ('start', 'splashscreen', 'main_menu', 'before_main_menu',
                'after_load', 'beginstory', 'beginstory1', 'begin')

#: 视为"游戏到此为止"的 Ren'Py 调用。只认这几个**字面量前缀**，
#: 不做模糊匹配（猜错了会把正常章判成终局，比不判更坏）。
_TERMINATING_CALLS = (
    'renpy.full_restart(',
    'renpy.quit(',
    'renpy.jump_out_of_context(',
)


def _terminate_code(code: str) -> str:
    """这段 python 代码是不是"游戏到此为止"？是就返回那个调用名，否则空串。"""
    text = str(code or '').strip().replace(' ', '')
    for call in _TERMINATING_CALLS:
        if text.startswith(call) or f' {call}' in f' {text}':
            return call
    return ''


#: 这些调用**静态判不出去哪** —— 目标是运行时的变量/表达式。
#: `renpy.jump_out_of_context(` 不在此列（它已经在 `_TERMINATING_CALLS` 里，是终局）。
_DYNAMIC_CALLS = ('renpy.jump(', 'renpy.call(', 'renpy.call_in_new_context(')

#: `jump expression x` / `call expression x`：Ren'Py 自己的表达式跳转语法。
_DYNAMIC_PREFIXES = ('jumpexpression', 'callexpression')


def _dynamic_code(code: str) -> str:
    """
    这段代码是不是"运行时才知道去哪"？是就返回命中的写法，否则空串。

    ⚠ 判据是**有没有出现那个调用**，不是"参数能不能解析"：
    `renpy.jump("b")` 虽然参数是字面量，但那是 Python 表达式，工具不解析它
    （解析一半比不解析更危险 —— 会说"目标是 b"，而实际跑的时候可能是别的）。
    """
    text = str(code or '').strip()
    squeezed = text.replace(' ', '')
    for call in _DYNAMIC_CALLS:
        if call in squeezed:
            return call
    for prefix in _DYNAMIC_PREFIXES:
        if squeezed.startswith(prefix):
            return prefix
    return ''


@dataclass
class LabelExit:
    """一个 label 的出口结论（全部是"读出来的事实"，不含建议）。"""

    label: str
    file: str = ''
    line: int = 0
    role: str = ROLE_CONTENT
    #: 六档之一（见 `ALL_EXITS`）。
    exit: str = EXIT_EMPTY
    #: 最后一条有效语句的种类（`StatementKind`），排查用。
    last_kind: str = ''
    #: 最后一条语句的原文摘要（最多 80 字）。
    last_text: str = ''
    last_line: int = 0
    #: 最后一条语句的**前缀条件**（来自 `if` 累积）。非空 = 它在某个分支里，
    #: 条件不成立时同样会落空 —— 这一档的结论可靠度要打折。
    last_condition: str = ''
    #: `jump` / `call` 的目标 label。
    target: str = ''
    #: 落空时会撞进的 label（同文件的下一个）；文件里最后一个 → 空串。
    falls_into: str = ''
    #: 以 menu 收尾时，**每个选项各自的出口**：`[{text, exit, target}]`。
    #: 非空说明这一档不能用一个 target 代表全体。
    menu_options: list = field(default_factory=list)
    #: 章末 `jump` 的目标就是自己 = 原地循环（等事件/重试），不是"去哪"。
    is_self_loop: bool = False
    #: 结论可靠度：`high` = 只看最后一条就够；`low` = 有 `if`/循环前缀条件
    #: 或 menu 收尾，真实行为还取决于运行时/玩家选择。
    confidence: str = 'high'
    #: 一句话人话解释（给体检报告/界面直接显示）。
    reason: str = ''

    # ---------------- 派生判断 ----------------

    @property
    def can_wrap(self) -> bool:
        """能不能给它生成 `<label>_control` 包一层。"""
        return self.role == ROLE_CONTENT and self.exit in WRAPPABLE

    @property
    def is_risky(self) -> bool:
        """要不要报给用户（落空 / 末尾 call 无 return / 有选项落空）。

        ⚠ `EXIT_DYNAMIC` **不算风险**：那是"工具判不出来"，不是"用户写错了"。
        把判不出来的东西报成缺陷，用户很快就会对所有报警免疫。
        """
        if self.role != ROLE_CONTENT:
            return False
        if self.exit in (EXIT_FALLTHROUGH, EXIT_CALL_NO_RETURN, EXIT_EMPTY):
            return True
        if self.exit == EXIT_MENU:
            return any(o.get('exit') in (EXIT_FALLTHROUGH, EXIT_CALL_NO_RETURN)
                       for o in self.menu_options)
        return False

    def to_dict(self) -> dict:
        return {
            'label': self.label, 'file': self.file, 'line': self.line,
            'role': self.role, 'exit': self.exit,
            'last_kind': self.last_kind, 'last_text': self.last_text,
            'last_line': self.last_line, 'last_condition': self.last_condition,
            'target': self.target, 'falls_into': self.falls_into,
            'menu_options': list(self.menu_options),
            'is_self_loop': self.is_self_loop,
            'confidence': self.confidence, 'reason': self.reason,
            'can_wrap': self.can_wrap, 'is_risky': self.is_risky,
        }


# ---------------------------------------------------------------------------
#  单条语句 → 出口
# ---------------------------------------------------------------------------

def _statement_text(stmt) -> str:
    """取一句话的"人看得懂的那部分"，只用于显示。"""
    payload = getattr(stmt, 'payload', None) or {}
    if not isinstance(payload, dict):
        return str(payload)[:80]
    for key in ('text', 'caption', 'raw', 'header', 'code', 'target', 'who'):
        val = payload.get(key)
        if val:
            return str(val).replace('\n', ' ')[:80]
    return str(payload)[:80]


def classify_last(stmt) -> tuple:
    """
    最后一条语句 → `(exit 档, target, last_kind)`。

    纯函数，不碰文件。判据只有 `StatementKind` + 少数终局调用名。
    """
    if stmt is None:
        return EXIT_EMPTY, '', ''
    kind = str(getattr(stmt, 'kind', '') or '')
    payload = getattr(stmt, 'payload', None) or {}
    payload = payload if isinstance(payload, dict) else {}

    if kind == StatementKind.RETURN:
        return EXIT_RETURN, '', kind

    if kind == StatementKind.JUMP:
        target = str(payload.get('target', '') or '')
        if target.replace(' ', '').startswith('expression'):
            return EXIT_DYNAMIC, '', kind
        return EXIT_JUMP, target, kind

    if kind == StatementKind.CALL:
        # ⚠ 关键：末尾的 call **没有**隐式 return。见模块 docstring。
        target = str(payload.get('target', '') or '')
        if target.replace(' ', '').startswith('expression'):
            return EXIT_DYNAMIC, '', kind
        return EXIT_CALL_NO_RETURN, target, kind

    # `$ renpy.jump(x)` 是 PYTHON，`jump expression x` 可能落进 RAW —— 两者都要看原文
    text = (str(payload.get('code', '') or payload.get('raw', '')
                or payload.get('text', '') or ''))
    if kind in (StatementKind.PYTHON, StatementKind.RAW):
        if _terminate_code(text):
            return EXIT_TERMINATE, '', kind
        if _dynamic_code(text):
            # 静态判不出去哪：**承认不知道**，不归"落空"
            return EXIT_DYNAMIC, '', kind

    return EXIT_FALLTHROUGH, '', kind


def _menu_tail(idxs, statements, menu_opts) -> list:
    """
    从 label 末尾往回找**最后一个 menu 块**，给出每个选项各自的出口。

    找法：从后往前，只要语句还带着"所属选项文本"就继续；遇到第一条不在
    任何选项里的语句就停 —— 那一停，就是 menu 块的边界。

    为什么不猜"全体选项"：相邻两个 menu 首尾相接时，"最后一个 menu" 和
    "上一段 menu" 靠 `statement_menu_options` 分不开，所以只认末尾这一段，
    并让 `confidence` 打折（见 `label_exits_for_result`）。
    """
    if not idxs or not menu_opts:
        return []

    tail = []
    for i in reversed(idxs):
        opt = str(menu_opts[i] or '') if i < len(menu_opts) else ''
        if opt:
            tail.append(i)
            continue
        # 选项头（`"去屋顶":`）本身也是一条语句，但它不属于任何选项 ——
        # 跳过它继续往前，否则第一个选项就被它挡住了（2026-10-23 实测：
        # 只认出最后一个选项，正是"猜一个名字"的毛病）。
        kind = str(getattr(statements[i], 'kind', '') or '')
        if kind == StatementKind.MENU_OPTION:
            continue
        break
    tail.reverse()
    if not tail:
        return []

    last_of = {}                 # 选项文本 → 该选项最后一条语句下标
    order = []
    for i in tail:
        st = statements[i]
        kind = str(getattr(st, 'kind', '') or '')
        # 注释与选项头都不代表"这个选项的去向"
        if kind in (StatementKind.COMMENT, StatementKind.MENU_OPTION):
            continue
        opt = str(menu_opts[i] or '')
        if opt not in last_of:
            order.append(opt)
        last_of[opt] = i

    out = []
    for opt in order:
        exit_kind, target, _kind = classify_last(statements[last_of[opt]])
        out.append({'text': opt, 'exit': exit_kind, 'target': target})
    return out


# ---------------------------------------------------------------------------
#  单文件
# ---------------------------------------------------------------------------

def _role_of(label: str) -> str:
    name = str(label or '')
    if name in ENTRY_LABELS:
        return ROLE_ENTRY
    if name.endswith('_control'):
        return ROLE_CONTROLLER
    return ROLE_CONTENT


#: 出口档的中文说法（给界面/报告直接用，别各处自己拼）。
EXIT_ZH = {
    EXIT_RETURN: 'return（回到调用者）',
    EXIT_JUMP: 'jump（去不回）',
    EXIT_CALL_NO_RETURN: 'call 后没 return（会落空）',
    EXIT_TERMINATE: '重启/退出',
    EXIT_MENU: 'menu 收尾（按选项分岔）',
    EXIT_DYNAMIC: '运行时决定去哪（工具判不出来）',
    EXIT_FALLTHROUGH: '落空（撞进下一个 label）',
    EXIT_EMPTY: '空 label',
}


def _describe_option(opt: dict) -> str:
    """一个选项的去向，说人话。"""
    target = str(opt.get('target') or '')
    if target:
        return f'{opt.get("text")} → {target}'
    return f'{opt.get("text")} → {EXIT_ZH.get(opt.get("exit"), opt.get("exit"))}'


def _reason_for(exit_kind: str, role: str, falls_into: str, target: str,
                last_kind: str, menu_options=None) -> str:
    if role == ROLE_CONTROLLER:
        return '编排层（*_control），它本身就是包装层，不再被包'
    if role == ROLE_ENTRY:
        return 'Ren\'Py 标准入口，引擎自己会进来，不需要包'
    if exit_kind == EXIT_RETURN:
        return '显式 return：演完交回调用者，能安全包一层控制流'
    if exit_kind == EXIT_JUMP:
        return (f'章末 jump {target or "?"}：这章自己写着去哪；'
                '包一层 call 之后 jump 会直接穿出，永远回不到控制流')
    if exit_kind == EXIT_CALL_NO_RETURN:
        return (f'章末 call {target or "?"} 后面没有 return —— '
                '它会落空，不是隐式返回（Ren\'Py 没有隐式 return）')
    if exit_kind == EXIT_TERMINATE:
        return '终局语句（重启/退出）：这里没有"下一步"，不该有出边'
    if exit_kind == EXIT_DYNAMIC:
        return ('动态出口：最后一句是 `renpy.jump(...)` / `renpy.call(...)` / '
                '`jump expression ...`，目标要到运行时才知道 —— '
                '工具**判不出来**，不当作缺陷，也不猜它去哪')
    if exit_kind == EXIT_MENU:
        opts = list(menu_options or [])
        parts = '、'.join(_describe_option(o) for o in opts[:4])
        if len(opts) == 1:
            return f'以 menu 收尾：只有 1 个选项（{parts}）—— 出口不算明确'
        return (f'以 menu 收尾：{len(opts)} 个选项各自决定去向（{parts}）—— '
                '不能用一个"章末 jump"代表全体')
    if exit_kind == EXIT_EMPTY:
        return 'label 里没有任何语句'
    if falls_into:
        return (f'落空：最后一句是 {last_kind or "普通语句"}，没有 return，'
                f'玩家会顺路撞进同文件的「{falls_into}」')
    return (f'落空：最后一句是 {last_kind or "普通语句"}，没有 return，'
            '而这是文件里最后一个 label —— 会落到文件末尾')


def label_exits_for_result(parse_result, file_path: str = '') -> list:
    """
    从一个已解析的 `ParseResult` 里读出口。

    只读；不重新解析。`file_path` 仅用于记录来源。
    """
    statements = list(getattr(parse_result, 'statements', None) or [])
    owners = list(getattr(parse_result, 'statement_labels', None) or [])
    menu_opts = list(getattr(parse_result, 'statement_menu_options', None) or [])
    labels = getattr(parse_result, 'labels', None) or {}

    # label → 语句下标（保序）
    by_label = {}
    for idx, owner in enumerate(owners):
        if idx >= len(statements):
            break
        by_label.setdefault(str(owner or ''), []).append(idx)

    order = [str(name) for name in labels.keys()]
    # labels 之外的语句（理论上没有）也按出现顺序补在后面
    for name in by_label:
        if name and name not in order:
            order.append(name)

    pos = {name: i for i, name in enumerate(order)}
    out = []
    for name in order:
        idxs = by_label.get(name) or []
        last = None
        last_idx = -1
        # 注释不改变执行流，不能当"最后一条有效语句"。
        # （2026-10-23 真机：`fc_play` 末尾是一段 `## ----` 分节注释，
        #  第一版把它当成了出口，理由写着"最后一句是 comment"——结论对、
        #  依据错，这种"对了也是蒙对"的判据必须修。）
        for i in reversed(idxs):
            if str(getattr(statements[i], 'kind', '') or '') != StatementKind.COMMENT:
                last = statements[i]
                last_idx = i
                break
        exit_kind, target, last_kind = classify_last(last)

        # 以 menu 收尾：最后一个 menu 块的每个选项各自决定去向，
        # 不能拿折平后的"最后一条"代表全体。
        menu_options = []
        if last_idx >= 0 and menu_opts and last_idx < len(menu_opts) \
                and str(menu_opts[last_idx] or ''):
            menu_options = _menu_tail(idxs, statements, menu_opts)
            if menu_options:
                exit_kind, target, last_kind = EXIT_MENU, '', last_kind

        # 落空 → 同文件的下一个 label
        falls_into = ''
        if exit_kind in (EXIT_FALLTHROUGH, EXIT_CALL_NO_RETURN, EXIT_EMPTY):
            i = pos.get(name, -1)
            if 0 <= i < len(order) - 1:
                falls_into = order[i + 1]

        role = _role_of(name)
        last_condition = str(getattr(last, 'condition', '') or '') if last else ''
        self_loop = bool(exit_kind == EXIT_JUMP and target
                         and target == name)
        reason = _reason_for(exit_kind, role, falls_into, target, last_kind,
                             menu_options)
        if self_loop:
            reason = (f'章末原地循环（jump {name} 自己）：出口仍写在剧本里，'
                      '不能包控制流；图上这是一条回到自己的边')
        file_of, line_of = ('', 0)
        if isinstance(labels.get(name), (tuple, list)) and labels.get(name):
            meta = labels[name]
            file_of = str(meta[0]) if len(meta) > 0 else ''
            try:
                line_of = int(meta[1]) if len(meta) > 1 else 0
            except (TypeError, ValueError):
                line_of = 0

        item = LabelExit(
            label=name,
            file=file_path or file_of,
            line=line_of,
            role=role,
            exit=exit_kind,
            last_kind=last_kind,
            last_text=_statement_text(last) if last else '',
            last_line=int((getattr(last, 'source', ('', 0)) or ('', 0))[1] or 0)
            if last else 0,
            last_condition=last_condition,
            target=target,
            falls_into=falls_into,
            menu_options=menu_options,
            is_self_loop=self_loop,
            confidence='low' if (last_condition or menu_options) else 'high',
            reason=reason,
        )
        out.append(item)
    return out


def label_exits_for_file(path) -> list:
    """解析并读取一个 `.rpy` 的 label 出口。解析失败时返回空表（不抛）。"""
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser

    p = Path(path)
    try:
        nodes = RenPyParser().parse(str(p))
        result = ParseResultBuilder().build(nodes)
    except Exception as e:                           # noqa: BLE001 - 单个文件坏不该炸整次扫描
        swallow('story.exits.label_exits_for_file', e)
        return []
    return label_exits_for_result(result, file_path=str(p))


# ---------------------------------------------------------------------------
#  整项目
# ---------------------------------------------------------------------------

@dataclass
class ExitsReport:
    """整项目的出口体检结果 + 由此推出的"能力降级"清单。"""

    labels: list = field(default_factory=list)
    files: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    # ---------------- 查询 ----------------

    def by_label(self, name: str):
        for item in self.labels:
            if item.label == str(name):
                return item
        return None

    def of_role(self, role: str) -> list:
        return [x for x in self.labels if x.role == role]

    def risky(self) -> list:
        return [x for x in self.labels if x.is_risky]

    def unwrappable_content(self) -> list:
        """内容章里**不能**包控制流的（出口不是 return）。"""
        return [x for x in self.labels
                if x.role == ROLE_CONTENT and not x.can_wrap]

    def counts(self) -> dict:
        out = {k: 0 for k in ALL_EXITS}
        for item in self.labels:
            out[item.exit] = out.get(item.exit, 0) + 1
        return out

    # ---------------- 降级提示（"工具能力受限"要能说出口） ----------------

    def degradations(self) -> list:
        """
        把出口结论翻译成"图上这条路径只能用多深"。

        每条形如：
          `{kind, level, title, message, labels, hint}`
        `level`：`blocked`（完全不能接）/ `readonly`（只能看，不能加边）。
        界面/报告直接拿它显示，不要各自拼文案。
        """
        out = []
        fall = [x for x in self.labels if x.exit == EXIT_FALLTHROUGH
                and x.role == ROLE_CONTENT]
        call_noret = [x for x in self.labels if x.exit == EXIT_CALL_NO_RETURN]
        jumps = [x for x in self.labels if x.exit == EXIT_JUMP
                 and x.role == ROLE_CONTENT]
        terms = [x for x in self.labels if x.exit == EXIT_TERMINATE
                 and x.role == ROLE_CONTENT]

        if fall:
            names = '、'.join(x.label for x in fall[:6])
            out.append({
                'kind': 'label_falls_through',
                'level': 'blocked',
                'title': '这些章没有出口（会撞进下一个 label）',
                'message': f'{len(fall)} 个章以台词/演出收尾，没有 return：{names}',
                'labels': [x.label for x in fall],
                'hint': ('在每章末尾补一行 return；'
                         '补之前这些章不能在图上线（也没法包控制流）'),
            })
        if call_noret:
            names = '、'.join(x.label for x in call_noret[:6])
            out.append({
                'kind': 'call_without_return',
                'level': 'blocked',
                'title': '这些章末尾只有 call，没有 return',
                'message': (f'{len(call_noret)} 个章：{names} —— '
                            'call 回来之后没有语句了，一样会落空'),
                'labels': [x.label for x in call_noret],
                'hint': '在 call 后面补一行 return',
            })
        if jumps:
            names = '、'.join(x.label for x in jumps[:6])
            out.append({
                'kind': 'chapter_ends_with_jump',
                'level': 'readonly',
                'title': '这些章的出口写在剧本里',
                'message': (f'{len(jumps)} 个章以 jump 收尾：{names} —— '
                            '不能再包控制流层，否则跳转会穿出去'),
                'labels': [x.label for x in jumps],
                'hint': '图上能看到这条边、能改它的目标，但不能再加一条',
            })
        if terms:
            names = '、'.join(x.label for x in terms[:6])
            out.append({
                'kind': 'chapter_terminates',
                'level': 'readonly',
                'title': '这些章是游戏终点',
                'message': f'{len(terms)} 个章以重启/退出收尾：{names}',
                'labels': [x.label for x in terms],
                'hint': '这里没有"下一步"，图上不该有出边',
            })
        menus = [x for x in self.labels
                 if x.exit == EXIT_MENU and x.role == ROLE_CONTENT]
        if menus:
            names = '、'.join(x.label for x in menus[:6])
            out.append({
                'kind': 'chapter_ends_with_menu',
                'level': 'readonly',
                'title': '这些章以选项收尾（去向由玩家选）',
                'message': (f'{len(menus)} 个章以 menu 收尾：{names} —— '
                            '出口不是一个"章末跳转"，而是每个选项各一条'),
                'labels': [x.label for x in menus],
                'hint': '图上看得到每个选项的去向；要改就在卡片里改那段 menu',
            })

        # —— 第三层：能力边界声明（不是"你写错了"，是"我看不出来"）——
        # 单独一档、单独一条提示：把它和"缺陷"混在一起，用户会开始不信报警。
        dynamics = [x for x in self.labels
                    if x.exit == EXIT_DYNAMIC and x.role == ROLE_CONTENT]
        if dynamics:
            names = '、'.join(x.label for x in dynamics[:6])
            out.append({
                'kind': 'dynamic_exit',
                'level': 'readonly',
                'title': '这些章的出口要到运行时才知道（不是缺陷）',
                'message': (f'{len(dynamics)} 个章以 `renpy.jump(...)` / '
                            f'`renpy.call(...)` / `jump expression` 收尾：{names}'),
                'labels': [x.label for x in dynamics],
                'hint': ('工具**不会假装知道**它去哪：图上这段只读，'
                         '不报"落空"；要改就改那一行代码'),
            })
        return out

    def to_dict(self) -> dict:
        return {
            'labels': [x.to_dict() for x in self.labels],
            'files': list(self.files),
            'errors': list(self.errors),
            'counts': self.counts(),
            'degradations': self.degradations(),
        }


def project_exits(project_root, files=None) -> ExitsReport:
    """
    扫一个项目的全部 `.rpy`，给出每个 label 的出口 + 降级清单。

    只读。`files` 传了就用它（测试/局部扫描），否则发现项目里的脚本。
    `errors` 记"这个文件解析失败"，**不中断整次扫描** —— 坏一个文件不该
    让体检报告什么都给不出来。
    """
    from storygraph.parsing.discover import discover_scripts

    root = Path(project_root)
    paths = [Path(p) for p in files] if files else \
        [Path(p) for p in discover_scripts(root)]

    report = ExitsReport()
    for path in paths:
        try:
            items = label_exits_for_file(path)
        except Exception as exc:                     # noqa: BLE001
            report.errors.append({'file': str(path), 'error': str(exc)})
            continue
        if items:
            report.files.append(str(path))
            report.labels.extend(items)
    return report


__all__ = [
    'EXIT_RETURN', 'EXIT_JUMP', 'EXIT_CALL_NO_RETURN', 'EXIT_TERMINATE',
    'EXIT_MENU', 'EXIT_DYNAMIC', 'EXIT_FALLTHROUGH', 'EXIT_EMPTY',
    'ALL_EXITS', 'WRAPPABLE',
    'ROLE_CONTENT', 'ROLE_CONTROLLER', 'ROLE_ENTRY', 'ENTRY_LABELS',
    'LabelExit', 'ExitsReport', 'classify_last', 'label_exits_for_result',
    'label_exits_for_file', 'project_exits',
]
