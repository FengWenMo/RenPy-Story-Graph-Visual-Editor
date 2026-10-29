# -*- coding: utf-8 -*-
"""
「舞台（高级）」里**不像代码**的那几行（2026-10-29，用户）
========================================================================

## 这一列是什么（先说清，判据才有依据）

「舞台（高级）」/ Stage 是**代码**，不是提示：结构化列（背景 / 立绘 / 位置 /
转场）装不下的舞台指令写在这里，导出时**原样接进 `.rpy`**，占一行、会执行。

所以它跟「备注」是两回事：
  · 备注 → 脚本里 `# …` 一行注释；
  · Stage → 脚本里一条语句。

## 为什么需要这个模块

2026-10-28 曾经在这列"自动补 `#`"（`dsl._looks_like_stage_note`），
2026-10-29 撤掉 —— 因为判据只能看字面，而

    bg1.2 bgm13      ← 作者想让它是备忘
    weather6 rain    ← fork 引擎的自定义语句，必须执行

在字面上**完全同构**。自动补 `#` 的结果是把真语句注释掉，而卡片上
看着还是原文，只有导出后才变 —— 静默丢行为，最难查的一类。

用户 2026-10-29 拍板的新规矩：

> 可以！但也要尽量检测一下！实在看着不像是代码的才加弹窗确认，询问要不要
> 补为备注！！！如果很明显是代码，那就不需要，原样放进去就行……这个判断器
> 要写好，宁错报，不要漏报。

于是分成两件事，**都不动字节**：

  1. 本模块只**指认**（`scan`）—— 哪一拍的哪一条"看着不像语句"，为什么；
  2. 补不补成备注，由**人在弹窗里拍板**（`move_to_comment` 是那个动作的
     纯逻辑部分）。工具自己不写一个字。

## 判据：宽进（宁错报，不漏报）

"错报"的代价是**多问一次**，"漏报"的代价是**作者写完导出、启动游戏才
发现那句没生效**。两者不对等，所以一律往"报"这一边倒 —— 只要有一丝
不确定就报出来让人看。

放行（= 看着像代码，不打扰）的只有这几类**硬信号**：

  1. 以 `#` 开头 —— 本来就是注释，不必再问；
  2. 以 `[` 开头 —— DSL 动词（`[显示] 凌跃捷 开心`），走的是校验那条路；
  3. 以 `$` 开头 —— 一行 Python；
  4. 第一个词是**内置语句头**（`show`/`scene`/`pause`/`window`…）；
  5. 第一个词是**项目学过的自定义语句头**（`rules.json` / CDS，走
     `StatementRegistry`）—— 这一条是对 `weather6 rain` 的正解：
     工具知道它，就不问；不知道，就老老实实问一句；
  6. 含**代码标点**（引号 / 半角括号 / 等号 / `[]` / `{}` / `<>` / 反引号 /
     `@` / 反斜杠）—— 备忘里不会出现这些，出现了基本就是表达式或子句；
  7. 含 **Ren'Py 子句/参数关键字**（`at`/`with`/`as`/`behind`/`onlayer`/
     `zorder`/`fadeout`/`loop`/`volume`/`xpos`…）；
  8. 整条是"**动词 + 参数**"形状且每个词都是合法标识符/数字
     （`mycam shake` 这种）—— 看起来就是一条语句。

其余**全部报出来**。典型被报出来的：

    bg1.2 bgm13          资源简写，不是语句
    白色转场              纯中文备忘
    （淡出）              全角括号备忘
    bg1.2（淡出）          资源简写 + 全角备注
    后巷 夜               中文场景描述

## 注意：全角括号 `（）` 不算代码标点

舞台备忘里 `bg1.2（淡出）` 这种写法非常常见，而全角括号本来就不是
合法的 Ren'Py 语法成分。半角 `()` 才算（那是函数调用 / 参数的写法）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from storygraph.core.vocabulary import (
    RENPY_KNOWN_HEADS,
    SHOW_CLAUSE_KEYWORDS,
    statement_head_token,
)

# ---------------------------------------------------------------------------
#  判据用的词表
# ---------------------------------------------------------------------------

#: 出现即当代码的**硬标点**。
#: `:` `;` `,` 刻意不算 —— 舞台备忘里很常见（`bg1.2: 淡出`、`bg1.2, bgm13`），
#: 而"未知语句头 + 冒号/逗号"本来也不是合法 Ren'Py 语句。
#: **全角** `（）：、，` 一律不算：那是中文备忘的标点。
_CODE_PUNCT = frozenset('"\'()[]{}<>=`@\\')

#: 出现在**第二个词及以后**就说明"这是一条带子句的语句"。
#: `SHOW_CLAUSE_KEYWORDS` 是官方 show/scene 子句关键字（`at`/`with`/`as`/
#: `behind`/`onlayer`/`zorder`），这里再补一批通用参数名。
_PARAM_WORDS = frozenset(SHOW_CLAUSE_KEYWORDS) | frozenset({
    'fadein', 'fadeout', 'fade', 'loop', 'noloop', 'volume', 'channel',
    'queue', 'if', 'else', 'not', 'and', 'or',
    'xpos', 'ypos', 'xalign', 'yalign', 'xoffset', 'yoffset',
    'zoom', 'alpha', 'rotate', 'offset', 'anchor', 'pos', 'size',
    'delay', 'hard', 'relative', 'onlayer', 'transition',
})

#: 一个"词"是不是合法标识符或数字（`shake` / `ling` / `0.5` / `main.route`）。
_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*$|^[0-9]+(\.[0-9]+)?$')

#: 像"资源简写"的词：`bg1.2` / `bg9_2.mp3` / `cg.03` —— 备忘里最常见，
#: 语句里几乎不会出现（Ren'Py 图像名是空格分隔的，不是点分）。
_ASSET_SHORTHAND_RE = re.compile(
    r'^[A-Za-z_][A-Za-z0-9_]*\.\d+[A-Za-z0-9_]*$'
    r'|^[A-Za-z0-9_\-]+\.(mp3|ogg|opus|wav|flac|png|jpe?g|webp|gif|rpy|rpymc?)$',
    re.IGNORECASE)


# ---------------------------------------------------------------------------
#  结果
# ---------------------------------------------------------------------------

@dataclass
class Suspect:
    """一条"看着不像语句"的舞台内容。"""

    index: int                 # 第几拍（0 基，按传入的 beats 顺序）
    seq: object = ''           # `Beat.seq`（卡片上显示的拍号）
    label: str = ''
    text: str = ''             # 原文（一个字都不动）
    reason: str = ''           # 人话：为什么觉得它不像语句
    multi: bool = False        # 这一格里还有别的片段（移动时要拆）
    target: object = None      # 指向那个 Beat（内部用，不参与展示）

    def as_dict(self) -> dict:
        return {'index': self.index, 'seq': self.seq, 'label': self.label,
                'text': self.text, 'reason': self.reason, 'multi': self.multi}


@dataclass
class ScanResult:
    suspects: list = field(default_factory=list)   # [Suspect]
    checked: int = 0                                # 看了几格 Stage
    known_heads: frozenset = frozenset()

    def __bool__(self) -> bool:
        return bool(self.suspects)

    def __len__(self) -> int:
        return len(self.suspects)

    def report(self) -> str:
        """命令行用的一段人话（CLI 拿它打印，不阻塞）。"""
        if not self.suspects:
            return ''
        lines = [f'舞台（高级）里有 {len(self.suspects)} 处看着不像 Ren\'Py 语句：']
        for it in self.suspects:
            lines.append(f'  · 第 {it.seq} 拍：{it.text}　—— {it.reason}')
        lines.append('  如果它们其实是备注，请挪到「备注」列（那列会自动补 `#`）；'
                     '如果确实是语句（自定义语句请先在「教会工具」里登记），'
                     '原样导出即可。')
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
#  判据
# ---------------------------------------------------------------------------

def why_suspect(chunk: str, known_heads=()) -> str:
    """
    这一条**像不像** Ren'Py 语句？

    返回空串 = 看着像代码（放行）；返回非空 = 人话的原因（要问人）。

    判据顺序见模块 docstring；这里只强调一条：**放行的条件必须是硬信号**，
    "说不清"一律往"报"上倒（用户：宁错报，不漏报）。
    """
    text = str(chunk or '').strip()
    if not text:
        return ''

    # ① 已经是注释 / DSL / 一行 Python：各走各的路，不在这里问。
    if text[0] in '#[$':
        return ''

    head = statement_head_token(text)
    # ② 内置语句头（`show` / `scene` / `pause` / `window` / `play` …）。
    #    `head.rstrip('.')` 是为了 `renpy.` / `config.` 这种带点的写法。
    if head and (head in RENPY_KNOWN_HEADS
                 or head.rstrip('.') in RENPY_KNOWN_HEADS):
        return ''
    # ③ 项目学过的自定义语句头（`rules.json` / CDS）。
    if head and head in known_heads:
        return ''

    # ④ 代码标点（半角）。全角不在集合里 —— 那是中文备忘的标点。
    if any(ch in _CODE_PUNCT for ch in text):
        return ''

    words = text.split()
    # ⑤ 第二个词起出现 Ren'Py 子句/参数关键字：`xxx at right with dissolve`
    #    即便头不认识，也是"带子句的语句"的形状。
    for w in words[1:]:
        if w.lower().rstrip(':') in _PARAM_WORDS:
            return ''

    # ⑥ 「动词 + 参数」形状，且每个词都是合法标识符/数字：
    #    `mycam shake` / `weather6 rain`。资源简写（`bg1.2`）不算标识符。
    if len(words) >= 2 and all(_IDENT_RE.match(w) for w in words) \
            and not any(_ASSET_SHORTHAND_RE.match(w) for w in words):
        return ''

    # ⑦ 只剩一个词、且是合法标识符：**照样报**。
    #    曾经想放行（"一条无参语句"），但实测 `bg9_2.mp3` 就是这种形状 ——
    #    一个词的行更像备忘，而"多问一次"的代价远小于"漏一句"。真有单词的
    #    自定义语句，登记到 `rules.json` 后第 ③ 条会放行它。

    # ---------- 到这里就是"看着不像语句" ----------
    if any(_ASSET_SHORTHAND_RE.match(w) for w in words):
        return '像资源名或文件名（如 bg1.2、bg9_2.mp3），不像一条 Ren\'Py 语句'
    if head and any(ord(ch) > 0x2000 for ch in head):
        return '开头是中文/全角文字，不像语句'
    if not any(ch.isascii() for ch in text):
        return '整行都是中文，不像语句'
    if head:
        return f'第一个词「{head}」不是任何已知语句头（内置语句和项目规则里都没有）'
    return '认不出这是什么语句'


def known_heads_for(project_root=None) -> frozenset:
    """
    这个项目**认得**的语句头：内置 ∪ 项目声明（`rules.json` / CDS）。

    拿不到项目（没打开工程 / 命令行没给根）时只返回内置那批 ——
    此时自定义语句会被报出来问一次，这是**故意**的（宁可问，不要猜）。
    """
    heads = set(RENPY_KNOWN_HEADS)
    try:
        from storygraph.story.statement_registry import StatementRegistry
        reg = StatementRegistry.for_project(project_root)
        if reg:
            heads.update(reg.names())
    except Exception as e:                      # noqa: BLE001 - 拿不到就当没有
        from storygraph.core.safeguard import swallow
        swallow('story.stage_suspect.known_heads', e)
    return frozenset(heads)


def scan(beats, known_heads=None, project_root=None) -> ScanResult:
    """
    扫一遍这批拍的 Stage 列，挑出"看着不像语句"的片段。

    **只读**：不碰 `beat.stage`、不碰文件。要不要改，由人在弹窗里决定。
    """
    from storygraph.story.dsl import split_ops        # 延迟 import，避免环

    if known_heads is None:
        known_heads = known_heads_for(project_root)
    out = ScanResult(known_heads=frozenset(known_heads))
    for i, beat in enumerate(beats or ()):
        raw = getattr(beat, 'stage', '') or ''
        if not str(raw).strip():
            continue
        out.checked += 1
        chunks = split_ops(raw)
        bad = []
        for chunk in chunks:
            why = why_suspect(chunk, out.known_heads)
            if why:
                bad.append((chunk, why))
        if not bad:
            continue
        chunk, why = bad[0]
        out.suspects.append(Suspect(
            index=i, seq=getattr(beat, 'seq', ''), label=getattr(beat, 'label', ''),
            text=chunk, reason=why, multi=len(chunks) > 1 or len(bad) > 1,
            target=beat))
    return out


# ---------------------------------------------------------------------------
#  「补为备注」这个动作（人在弹窗里点了才走这里）
# ---------------------------------------------------------------------------

def move_to_comment(beats, suspects, join='\n') -> int:
    """
    把被指认的片段从 Stage **挪到备注**，返回挪了几条。

    为什么要挪到备注而不是在 Stage 里原地加 `#`：
      · 备注列本来就渲染成 `# …`，挪过去语义正确；
      · Stage 的定位是"代码"，让它出现注释行会把两列的分工搅浑；
      · 卡片上「备注」就在第一栏，作者改完还看得见。

    **只动这一格里的可疑片段**，同格的其它语句原样留下（`join` 用原格的
    分隔风格，默认换行）。这个函数是**用户点了确认之后**才该调的 ——
    工具自己不会走到这里（`scan` 是只读的）。
    """
    from storygraph.story.dsl import split_ops

    moved = 0
    by_beat = {}
    for it in suspects or ():
        beat = it.target
        if beat is None:
            continue
        by_beat.setdefault(id(beat), (beat, []))[1].append(it.text)

    for _key, (beat, texts) in by_beat.items():
        chunks = split_ops(getattr(beat, 'stage', '') or '')
        if not chunks:
            continue
        drop = {str(t).strip() for t in texts}
        keep = [c for c in chunks if c.strip() not in drop]
        taken = [c for c in chunks if c.strip() in drop]
        if not taken:
            continue
        beat.stage = join.join(keep)
        old = str(getattr(beat, 'comment', '') or '')
        add = '\n'.join(taken)
        beat.comment = (old + '\n' + add) if old.strip() else add
        moved += len(taken)
    return moved
