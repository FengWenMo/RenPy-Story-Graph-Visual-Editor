"""
storygraph.assets.rename_scope
==============================
改名传播的**改写范围**：一行里哪些字是"资产引用位"，哪些不是（P1-4）。

为什么需要（2026-10-25《不安全因素》第二轮审计）：从前
`plan_rename_patch` 拿 `(?<!\\w)旧名(?!\\w)` 在**整份原文**上 `sub`。名字
恰好是常见英文词时（`bg` / `show` / `play` / `at`），它会把

    $ bg = 1                  →  $ bg_new = 1              （Python 变量）
    # bg 这是注释             →  # bg_new 这是注释          （注释）
    play music "bg 音乐.mp3"  →  play music "bg_new 音乐.mp3"（字符串里的文件名）

一起改掉 —— 而预览按"行"展示，编剧看不出哪一行其实是被当代码用的。

所以这里给"能不能改"定一个**逐行判据**（纯函数，绝不写盘）：

  ① 注释行（剥掉缩进后以 `#` 开头）整行不改；
  ② 语句首词是 Python / 流程关键字的行整行不改（`$`、`python`、`define`、
     `default`、`init`、`label`、`jump`、`call`…）—— 那里的 `bg` 是变量名或
     label 名；`init python:` / `python:` 的**块体**按缩进整段算作代码；
  ③ 引号里的名字要**同时**满足"音频/语音语句"+"是一整段路径"才改
     （`play music "bgm_x.ogg"` 是资产引用；而 `play music "bg 音乐.mp3"` 里
     那个 `bg` 只是文件名的一部分，不改）；台词、字符串比较、屏幕参数里的
     名字一律不改；
  ④ 行尾注释（引号外的 `#` 之后）不改；
  ⑤ 语句首词本身不是参数位（资产 id 恰好叫 `show` 时不改关键字）。

改不了的命中**不静默丢掉**：`rename_in_text` 把每一处原样报出来
（行号 / 那一行 / 原因），预览照实列给用户看（"这几处看着像代码，没改"）。

判据：`tests/test_p0_round5.py`（攻击二 4 条 + 保真 2 条）。
"""

from __future__ import annotations

import re

__all__ = ['QUOTED_VERBS', 'SKIP_VERBS', 'analyse_line', 'rename_in_text',
           'word_pattern']

#: 已知的 Ren'Py 语句首词 —— 它们是**语法**，不是参数位（资产 id 恰好叫
#: `show` / `scene` 时不该把关键字改掉）。
#:
#: ⚠ 不在这里的首词（自定义语句、`meiko "hello"` 的说话人）**第一个词就是
#: 参数/角色名**，必须可改：角色表改名要落到 `define meiko = Character(…)`
#: 与 `meiko "hello"` 两处（`asset_engine` 的角色改名就靠这条）。
STATEMENT_VERBS = frozenset({
    'show', 'scene', 'hide', 'play', 'queue', 'stop', 'voice', 'with',
    'image', 'transform', 'layeredimage', 'camera', 'window', 'pause',
    'init', 'define', 'default', 'label', 'jump', 'call', 'menu', 'screen',
    'style', 'translate', 'if', 'elif', 'else', 'while', 'return', 'pass',
    'python', '$',
})

#: 首词落在这些里面 → 这一行的"词"不是资产名（是 Python 变量 / 流程 label）。
#:
#: `label` / `jump` / `call` 也在里面：资产 id 叫 `bg` 而项目里正好有
#: `label bg:` 时，把 label 改写掉就是**换了一个跳转目标**（比改坏一行
#: 代码更糟）。`label` 从来不是资产引用位，整行不改不会有任何损失。
#:
#: ⚠ `define` / `default` **不在**这里：它们正是 Ren'Py 声明资产/角色的地方
#: （`define meiko = Character("Meiko")`、`define audio.bgm = …`），角色改名
#: 必须落到那一行（真机测试 `test_end_to_end_rename_reaches_script` 就是它）。
#: 审计报的坏例是 `$ bg = 1`（Python 赋值），不是 `define`。
SKIP_VERBS = frozenset({
    '$', 'python', 'init', 'def', 'class', 'import',
    'from', 'label', 'jump', 'call', 'return', 'pass', 'while', 'for',
})

#: 这些语句的资产引用**本身就写在引号里**（音频/语音的文件名或 id），
#: 所以允许改引号内的文本。其余语句的引号内容是台词/字符串，不动。
QUOTED_VERBS = frozenset({'play', 'queue', 'stop', 'voice'})

_RE_EOL = re.compile(r'(?:\r\n|\r|\n)$')
#: 行内字符串字面量（Ren'Py 的台词/参数都是单行；未闭合的不算）。
_RE_STR = re.compile(r'"(?:[^"\\\n]|\\.)*"'
                     r"|'(?:[^'\\\n]|\\.)*'")
#: 块头：`python:` / `python early:` / `init python:` / `init -1 python:`
_RE_PY_BLOCK = re.compile(r'^(?:python\b'
                          r'|init(?:\s+[-+]?\d+(?:\.\d+)?)?\s+python\b)')
_RE_VERB = re.compile(r'[ \t]*(\S+)')


def word_pattern(old_id) -> re.Pattern:
    """词边界匹配：`hero` 不匹配 `hero_happy`，也不匹配 `bgm_hero`。"""
    return re.compile(r'(?<![\w])' + re.escape(str(old_id)) + r'(?![\w])')


def _comment_start(line: str, strings) -> int:
    """引号外的第一个 `#`（行尾注释起点）；没有就返回行长。"""
    start = 0
    for s, _e in strings:
        idx = line.find('#', start, s)
        if idx >= 0:
            return idx
        start = _e
    idx = line.find('#', start)
    return idx if idx >= 0 else len(line)


def _subtract(spans, cuts):
    """区间差集：`spans` 里去掉 `cuts` 覆盖的部分。"""
    out = []
    for s, e in spans:
        pieces = [(s, e)]
        for cs, ce in cuts:
            nxt = []
            for ps, pe in pieces:
                if ce <= ps or cs >= pe:
                    nxt.append((ps, pe))
                    continue
                if ps < cs:
                    nxt.append((ps, cs))
                if ce < pe:
                    nxt.append((ce, pe))
            pieces = nxt
        out.extend(pieces)
    return [(s, e) for s, e in out if e > s]


def analyse_line(line: str, block_python: bool = False) -> dict:
    """
    一行 → 改写范围（纯函数）。

    返回 `{'verb','verb_end','code_end','strings','spans','skip'}`：

      · `spans`  —— **允许改**的区间（左闭右开）；
      · `skip`   —— 整行都不改的原因（`''` = 这一行可以改）；
      · `code_end` —— 行尾注释起点（`spans` 不会越过它）。
    """
    line = str(line or '')
    stripped = line.strip()
    strings = [(m.start(), m.end()) for m in _RE_STR.finditer(line)]
    code_end = _comment_start(line, strings)
    code = line[:code_end]
    m = _RE_VERB.match(code)
    verb = (m.group(1) if m else '').lower()
    verb_end = m.end(1) if m else 0
    skip = ''
    if not stripped:
        skip = '空行'
    elif stripped.startswith('#'):
        skip = '注释行'
    elif block_python:
        skip = 'Python 块里（那段是代码）'
    elif verb in SKIP_VERBS:
        skip = f'`{verb}` 语句（那里的词是代码/流程名）'
    spans = []
    # 首词是语法（`show` / `define` …）→ 参数位从它后面开始；否则（自定义语句、
    # 说话人）**第一个词就是参数**，从 0 开始算。
    start = verb_end if verb in STATEMENT_VERBS else 0
    if not skip:
        # 引号里一律不算"代码区"：音频/语音那几句由 `_is_path_segment` 单独裁决
        # （`play music "bgm_main.ogg"` 是资产引用，`play music "bg 音乐.mp3"`
        # 里那个 `bg` 只是文件名的一部分）。
        spans = _subtract([(start, code_end)], strings)
    return {'verb': verb, 'verb_end': verb_end, 'start': start,
            'code_end': code_end, 'strings': strings, 'spans': spans,
            'skip': skip}


#: 引号里的名字要"是一整段路径"才认（两边必须是引号 / 斜杠 / 点）。
_LEFT_OK = '"\'/\\'
_RIGHT_OK = '"\'/.\\'


def _is_path_segment(line: str, m) -> bool:
    """
    `m` 是不是引号里**一整段**路径/文件名？

    `"bgm_main.ogg"`（前引号 + 后 `.`）→ 是资产引用，改；
    `"bg 音乐.mp3"`（后面是空格）→ 只是文件名的一部分，**不改**。
    """
    before = line[m.start() - 1] if m.start() > 0 else '"'
    after = line[m.end()] if m.end() < len(line) else '"'
    return before in _LEFT_OK and after in _RIGHT_OK


def _in_spans(pos: int, spans) -> bool:
    return any(s <= pos < e for s, e in spans)


def _miss_reason(line: str, pos: int, info: dict) -> str:
    """命中但没改 —— 说清是哪种"不是引用位"。"""
    if pos < info['start']:
        return '语句首词（不是参数位）'
    for s, e in info['strings']:
        if s <= pos < e:
            return '引号里（不是一整段路径 → 像台词/文件名的一部分）'
    if pos >= info['code_end']:
        return '行尾注释'
    return '不在资产引用位'


def _splice(line: str, hits, new_id: str) -> str:
    """把 `hits` 那几处换掉，其余一字不动。"""
    parts, last = [], 0
    for m in hits:
        parts.append(line[last:m.start()])
        parts.append(new_id)
        last = m.end()
    parts.append(line[last:])
    return ''.join(parts)


def rename_in_text(text: str, old_id, new_id) -> tuple:
    """
    在**整份原文**上按行改写，返回 `(新全文, 改动, 被拒)`。

      · `改动` = `[(行号, 旧行, 新行)]`（行号 1 起）；
      · `被拒` = `[(行号, 那一行, 原因)]` —— 命中了旧名但不是资产引用位
        （代码 / 注释 / 字符串），**不静默丢掉**，预览要照实列出。

    换行符逐行原样带过去（`splitlines(keepends=True)` + 只摘行尾那一个
    EOL）：改一行不让别的行变样。
    """
    pattern = word_pattern(old_id)
    new_id = str(new_id)
    out, changed, rejected = [], [], []
    stack = []                                  # [(缩进, 是否 python 块)]
    for no, chunk in enumerate(str(text).splitlines(keepends=True), 1):
        tail = _RE_EOL.search(chunk)
        eol = tail.group(0) if tail else ''
        line = chunk[:-len(eol)] if eol else chunk
        indent = len(line) - len(line.lstrip())
        # ⚠ **空行与纯注释行不弹栈**（2026-10-25 真机踩到）：`init python:` 里
        # 的 `def ...: """…"""` 之间有空行，按缩进弹栈会把 block 上下文弹掉，
        # 于是文档字符串里那句"实测 = cc_preview/ling/happy.png"被当成代码
        # 改掉（`about_collection.rpy:19`）。空行不属于任何层级、注释也不结束
        # Python 缩进块 —— 让它们保持当前上下文。
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            while stack and indent <= stack[-1][0]:
                stack.pop()
        info = analyse_line(line, block_python=any(p for _i, p in stack))
        hits, misses = [], []
        for m in pattern.finditer(line):
            if info['skip']:
                misses.append(m)
                continue
            pos = m.start()
            in_str = any(s <= pos < e for s, e in info['strings'])
            if in_str:
                # 引号里：只有音频/语音语句 + 整段路径才算资产引用
                if info['verb'] in QUOTED_VERBS and _is_path_segment(line, m):
                    hits.append(m)
                else:
                    misses.append(m)
            elif _in_spans(pos, info['spans']):
                hits.append(m)
            else:
                misses.append(m)
        for m in misses:
            rejected.append((no, line,
                             info['skip'] or _miss_reason(line, m.start(),
                                                          info)))
        if not hits:
            out.append(chunk)
        else:
            replaced = _splice(line, hits, new_id)
            out.append(replaced + eol)
            changed.append((no, line, replaced))
        # 块头压栈：`init python:` / `python:` 的块体整段按代码处理。
        code = line[:info['code_end']].rstrip()
        if code.endswith(':'):
            stack.append((indent, bool(_RE_PY_BLOCK.match(code.lstrip()))))
    return ''.join(out), changed, rejected
