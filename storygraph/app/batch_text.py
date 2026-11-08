# -*- coding: utf-8 -*-
"""
`storygraph.app.batch_text`
===========================
批处理里**只对一行文字做判断**的那几件事（叶子模块：只 import `re`）。

为什么单独一层（2026-11-08 第三轮地狱测试 + 技术债棘轮）
--------------------------------------------------------
这一轮往 `app.batch_edit` 里加的是"追加要落在代码末尾（行尾注释留最后）"、
"python 块里的行不许补"、"块头只能在文件最外层"这几条判据。它们全是
**纯文本判断**：给一行 / 给整份行表，回答一个问题，不碰规则对象、不碰
拍、不碰磁盘。留在 `batch_edit` 里有两个代价：

  · 那个文件本来就顶在 1500 行的**超长文件**上限上（`tools/debt_report.py`
    的棘轮只许降），再加一百多行就翻过去了；
  · `format_plan` 的分支数被同一批改动推过 20 —— 而它其实只是"把计划
    渲染成人话"，与文本判据是两件事。

所以拆成三块（`batch_edit` 仍然导出全部公开名字，调用方一行都不用改）：

    batch_text    一行/整表的纯文本判据 + 落盘拼接（本模块）
    batch_report  计划 → 人话 / 统一 diff
    batch_edit    规则解析、算计划、写盘

判据为什么长这样，每一处都写在函数自己的 docstring 里（行尾注释、引号里的
`#`、python 块、只能写最外层的块头 —— 都是地狱测试抓出来的真实坏产物）。
"""
from __future__ import annotations

import re

#: **只能在文件最外层**写的语句头（2026-11-08，地狱测试逼出来的）。
#: 批处理是"往 label 里补代码"，这些头写进 label 就是语法错 ——
#: 与其写出跑不了的剧本，不如在规则校验这一层点名拒绝。
#: 注意：**裸 `python:` 不在此列**（label 里写 `python:` 是合法的）。
_TOP_LEVEL_ONLY = re.compile(
    r'^(?:init\b|python\s+early\b|define\b|default\b|label\b|screen\b|'
    r'transform\b|image\b|style\b|translate\b|testcase\b)')

#: `python:` / `init python:` / `init -1 python:` / `python early:` 的块头。
_PY_BLOCK_HEAD = re.compile(
    r'^[ \t]*(?:init(?:[ \t]+-?[\d.]+)?[ \t]+)?python(?:[ \t]+early)?'
    r'[ \t]*:[ \t]*(?:#.*)?$')

#: 块头判定：`if:` / `python:` / `show …:` / `menu:`，注释不算
#: （行尾注释允许：`init python:  # 换装表`）。
_BLOCK_TAIL = re.compile(r':[ \t]*(?:#.*)?$')

#: 跳过的**保护性**原因（`session_export` 要把它们说给用户听，不能静默）。
#: 别的跳过原因（没匹配到行 / 已经是目标内容）属于日常，不在此列。
SKIP_COMMENT = '目标是注释行（补进去等于没补）'
SKIP_PYTHON = '这一行在 python 块里；追加/替换会改坏 Python 代码'
SKIP_APPENDED = '已经是目标内容'
GUARD_SKIPS = (SKIP_COMMENT, SKIP_PYTHON)


def indent_of(line: str) -> int:
    """行首缩进**字符数**（tab 算 1；比较层级用，与 `core.indent` 的分工不同）。"""
    return len(line) - len(line.lstrip(' \t'))


def is_block_header(line: str) -> bool:
    """`if:` / `python:` / `show …:` / `menu:`；注释不算。"""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith('#') \
        and bool(_BLOCK_TAIL.search(line.rstrip()))


def render_lines(text: str, indent: str) -> list:
    """把规则正文渲染成源文件行：基准缩进 + 续行的相对缩进。"""
    return ['' if not line.strip() else indent + line
            for line in text.split('\n')]


def split_comment(line: str) -> tuple:
    """
    一行 → `(代码部分, 注释部分)`；**引号里的 `#` 不算注释**。

    为什么要自己扫引号（2026-11-08 地狱测试）：`play music "bgm/#1.mp3"`
    里那个 `#` 是文件名的一部分。只按 `str.find('#')` 切，会把这一行劈成
    `play music "bgm/` + `1.mp3"` —— 追加的东西插进引号中间，直接语法错。

    认单/双引号与三引号（`\"\"\"`）；反斜杠转义的字符跳过。没有注释时
    第二项是 `''`。
    """
    text = str(line or '')
    i, n = 0, len(text)
    quote = ''
    while i < n:
        ch = text[i]
        if quote:
            if ch == '\\':
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                quote = ''
                continue
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch * 3 if text.startswith(ch * 3, i) else ch
            i += len(quote)
            continue
        if ch == '#':
            return text[:i], text[i:]
        i += 1
    return text, ''


def append_line(line: str, append: str) -> str:
    """
    把 `append` 接到这一行的**代码末尾**；行尾注释留在最后（2026-11-08）。

    为什么不能直接 `line.rstrip() + append`（地狱测试抓到的真事）：

        play music "bgm/a.mp3"   # 战斗曲
      → play music "bgm/a.mp3"   # 战斗曲 fadein 1.0

    追加的内容落进注释里 —— 音乐**根本不会淡入**，而导出还报"改了 1 行"；
    第二次导出又说"已经是目标内容"（行尾确实是那段字），于是永远补不上。
    正确落点是注释前面：

        play music "bgm/a.mp3" fadein 1.0   # 战斗曲

    注释前的空格原样保留（不动人的排版）。追加内容自己以 `#` 开头时
    例外：那是"补一句注释"，接在整行最后（否则会插进别人的注释里）。
    """
    body, comment = split_comment(line)
    if not append.lstrip().startswith('#') and comment:
        # 注释前的空格原样保留；原本紧贴着（`"x"# 注释`）就补一个，
        # 免得成品是 `fadein 1.0# 战斗曲`（语法没错，但人看着像坏行）。
        pad = body[len(body.rstrip()):] or ' '
        return body.rstrip() + append + pad + comment
    return str(line).rstrip() + append


def append_already_there(line: str, append: str) -> bool:
    """
    这一行是不是**已经加过这条尾巴**（append 的幂等判据）。

    两个判据，任一成立就算"加过了"：

      · **代码部分**行尾已经是这段（逐字）；
      · **追加内容的第一个词**（`fadein` / `fadeout` / `with`）已经作为
        一个词出现在**代码部分**里。

    为什么要有第二条（真实语料踩点，2026-11-08）：项目里本来就有
    `play music "bgm/bgm02.mp3" fadein 1.0`（作者手写的），逐字比行尾当然
    不相等 —— 再加一次会变成 `… fadein 1.0 fadein 1.0`，**重复关键字 =
    语法错**，脚本直接起不来。宁可少补一次，也不能补出跑不了的脚本。

    为什么两个判据都只看**代码部分**（地狱测试，同日）：注释里写着
    `# 这里要 fadein` 的行，从前会被词判据当成"加过了"而永远跳过；
    而带注释的行尾又永远不等于追加内容。现在注释先切掉，两处都准。
    """
    line = str(line or '')
    append = str(append or '')
    if not append.strip():
        return False
    body, _comment = split_comment(line)
    body = body.rstrip()
    if body.endswith(append.rstrip()):
        return True
    word = append.strip().split()[0]
    if word.startswith('#'):
        # "补一句注释"：只有逐字判据可用（词判据会命中别人的注释）
        return False
    # 只在**引号外面**找那个词：`play music "volume.mp3"` 里的 volume
    # 是文件名，不是参数名 —— 拿它判"加过了"会永远不补。
    bare = re.sub(r'"[^"]*"|\'[^\']*\'', '""', body)
    return re.search(rf'(?<!\w){re.escape(word)}(?!\w)', bare) is not None


def python_block_head(lines: list, idx: int):
    """
    `lines[idx]` 在不在某个 **python 块体**里；在就返回块头下标，否则 `None`。

    为什么要有（2026-11-08 地狱测试）：`python:` 块体里的行也是普通文本，
    `replace` 的匹配（正则）照样打得中它。往 `MUSIC = "bgm/a.mp3"` 尾巴上
    补 ` fadein 1.0`，产物是**语法错的 Python** —— 剧本加载即崩。
    批处理补的是 Ren'Py 子句，不该有一处能补进 Python 代码里。

    往上走的口径：跳过缩进 >= 当前基准的行（同级/更深，都是块体），遇到
    缩进更浅的第一行就是"最近的外层块头"；它若不是 python，就把基准降它
    的缩进继续往上找（这样 `python:` 里的 `if`/`for` 也能穿过去）。空白行
    不弹栈（Python 块里空行是常态）。
    """
    if not (0 <= idx < len(lines)):
        return None
    base = indent_of(lines[idx])
    for j in range(idx - 1, -1, -1):
        if not lines[j].strip():
            continue
        if indent_of(lines[j]) >= base:
            continue
        if _PY_BLOCK_HEAD.match(lines[j]):
            return j
        base = indent_of(lines[j])
    return None


def check_top_level_only(lines: list, where: str) -> None:
    """
    第一行是**只能在文件最外层**写的语句头 → 拒绝（2026-11-08，地狱测试）。

    批处理补进去的位置全在 label 里（锚点 / 拍区间都在 label 内）。把
    `init python:` / `define` / `default` / `label` / `screen` 这些写进
    label，Ren'Py 解析期就报 unexpected statement —— 那是**写坏剧本**，
    不是"少补一次"。与其导出之后让人对着引擎报错，不如此刻点名：

        「init python:」只能在文件最外层写；批处理是往 label 里补代码。

    裸 `python:` 放行：它在 label 里是合法的（临时改状态常用）。
    """
    head = str(lines[0] if lines else '').strip()
    if head and _TOP_LEVEL_ONLY.match(head):
        raise ValueError(
            f'{where} 第一行「{head.split(":")[0]}」只能在文件最外层写；'
            f'批处理是往 label 里补代码，插进去就是语法错'
            f'（要改这类块请直接改剧本）')


def splice_edits(text: str, insertions: list, edits: list) -> str:
    """按**原始行号**从后往前改；同一位置的多条插入保持规则顺序。"""
    lines = text.split('\n')
    ops = []
    for ins in insertions:
        pos = ins.line if not ins.before else ins.line - 1
        pos = max(0, min(pos, len(lines)))
        ops.append((pos, int(ins.seq), 0, 'insert', ins))
    for edit in edits:
        start = edit.line - 1
        end = edit.last_line - 1
        if 0 <= start < len(lines):
            end = min(max(end, start), len(lines) - 1)
            ops.append((start, int(edit.seq), 1, edit.kind, edit))
    # 从后往前；同一位置先做线编辑、后做插入；插入按 seq 逆序放入，
    # 最终文件里的顺序才是 seq 正序。
    ops.sort(key=lambda op: (op[0], op[2], op[1]), reverse=True)
    for pos, _seq, _kind_order, kind, obj in ops:
        if kind == 'insert':
            lines[pos:pos] = render_lines(obj.text, obj.indent)
        elif kind == 'replace':
            end = min(max(obj.last_line - 1, pos), len(lines) - 1)
            if obj.append:
                # 成品行在算计划时已经拼好（行尾注释留在最后）；老对象/外部
                # 直接造的 `LineEdit` 没有 `new` 时退回"整行末尾追加"。
                lines[pos:end + 1] = [obj.new or (obj.old.rstrip() + obj.append)]
            else:
                lines[pos:end + 1] = render_lines(obj.text, obj.indent)
        elif kind == 'delete':
            end = min(max(obj.last_line - 1, pos), len(lines) - 1)
            del lines[pos:end + 1]
    return '\n'.join(lines)


__all__ = [
    'GUARD_SKIPS', 'SKIP_APPENDED', 'SKIP_COMMENT', 'SKIP_PYTHON',
    'append_already_there', 'append_line', 'check_top_level_only',
    'indent_of', 'is_block_header', 'python_block_head', 'render_lines',
    'splice_edits', 'split_comment',
]
