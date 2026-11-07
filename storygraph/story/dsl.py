"""
dsl.py
======
策划写的"人话"指令 ⇄ Ren'Py 代码。**表格与代码之间唯一的翻译层。**

设计立场（被真实项目教育过之后确定的）
--------------------------------------
数据层（策划表）越靠近人类语言，中间层（编译器）承担的复杂度就越多，
表现层（生成的 .rpy）才会越干净。

把 show / hide / at / with / transform / music / sound … 拆成十来个
结构化列，是把「人话 / 程序代码 / 资源索引」揉进一张表——同一个动作被
拆到三列、还允许冗余，改一个位置要动两列，这不是严谨，是给未来埋雷。

于是舞台与音频收敛成三列 DSL：

    Stage : [显示] 凌跃捷 开心 左 ; [转场] dissolve
    BGM   : [音乐] 春天花瓣 fadein 1.0
    SFX   : [音效] 开门

两条通道，对应两种人：

  · **方括号形式**（`[显示] …`）—— 被解析、被校验、被编译器翻译。
    策划用它。词表有限，写错就有明确反馈。
  · **原生形式**（`show ling happy at left`）—— 直通后门，不解析不校验，
    原封不动抄进脚本。程序用它写复杂的原生演出。

找不到资源 **不报错**，只在生成代码里写一行 `# TODO: 缺少资源 …`。
校验是可选的外部工具（validate.py），绝不写进编译路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from storygraph.core.vocabulary import (
    ASSET_TYPE_ATTR,
    AUDIO_PSEUDO_ACTION,
    AUDIO_TYPE_ATTR,
    NON_ACTOR_TAGS,
)

from storygraph.core.safeguard import swallow
from storygraph.assets.asset_dimensions import dimensions_by_tag
#: "这张图放在立绘目录里吗" —— 唯一实现在 assets（2026-11-03）。
#: story → assets 是下行（`assets` 是底座，不反向 import story），不会成环。
from storygraph.assets.asset_identity import is_under_sprite_container

# ---------------------------------------------------------------------------
# 词表：把"人话"映射成 Ren'Py 认识的东西。
# 项目可以用 `配置_词表` 工作表覆盖（列：类别 | 词 | 映射为 | 说明）。
# ---------------------------------------------------------------------------

# 位置词 -> transform（Ren'Py 内置 left/center/right 开箱可用）
DEFAULT_POSITIONS = {
    '左': 'left', '左边': 'left', '左侧': 'left', '左边点': 'left',
    '中': 'center', '中间': 'center', '中央': 'center', '居中': 'center',
    '右': 'right', '右边': 'right', '右侧': 'right',
    '近': 'close', '近处': 'close', '特写': 'closeup',
    '远': 'far', '远处': 'far',
    '原地': '', '不动': '',
}

# 表情词 -> 图像属性（英文）。项目属性名千奇百怪（close_happy / e_smile），
# 所以这里只给"语义核心词"，实际属性名靠 `attr_for` 在候选里模糊匹配。
DEFAULT_EXPRESSIONS = {
    '开心': 'happy', '高兴': 'happy', '快乐': 'happy',
    '笑': 'smile', '微笑': 'smile', '大笑': 'laugh',
    '生气': 'angry', '愤怒': 'angry', '怒': 'angry',
    '无语': 'sigh', '叹气': 'sigh', '无奈': 'sigh',
    '害羞': 'blush', '脸红': 'blush',
    '闭眼': 'closed', '睁眼': 'open',
    '惊讶': 'surprised', '吃惊': 'surprised',
    '难过': 'sad', '悲伤': 'sad', '伤心': 'sad', '哭': 'cry',
    '平静': 'normal', '普通': 'normal', '常态': 'normal',
    '认真': 'serious', '严肃': 'serious',
    '默认': '', '无': '',
}

# 方括号动词 -> 内部动词
VERB_ALIAS = {
    # 舞台
    '显示': 'show', '出场': 'show', '上场': 'show', 'show': 'show',
    '隐藏': 'hide', '退场': 'hide', '下场': 'hide', 'hide': 'hide',
    '场景': 'scene', '背景': 'scene', '换景': 'scene', 'scene': 'scene',
    '转场': 'with', '过渡': 'with', 'with': 'with',
    '等待': 'pause', '停顿': 'pause', 'pause': 'pause',
    # 音频
    '音乐': 'music', '背景音乐': 'music', 'bgm': 'music', 'music': 'music',
    '停止音乐': 'music_stop', '音乐停止': 'music_stop',
    '音效': 'sound', 'sfx': 'sound', 'sound': 'sound',
    '停止音效': 'sound_stop',
    '语音': 'voice', '配音': 'voice', 'voice': 'voice',
}

STAGE_VERBS = {'show', 'hide', 'scene', 'with', 'pause'}
AUDIO_VERBS = {'music', 'music_stop', 'sound', 'sound_stop', 'voice'}

# 音频里可识别的参数关键字
_AUDIO_PARAM_RE = re.compile(
    r'\b(loop|fadein|fadeout|fade|volume|channel|if_changed)\b')
_STOP_WORDS = {'停止', '停', '关掉', 'stop', 'off', '无', '空'}

# `（停止音乐）` / `（排队播放）` 这类**伪值**：动作写在曲名那一格里。
# 词表本体在 `core.vocabulary`（表格下拉、Excel 候选表、这里必须同一份，
# 否则“Excel 能选、卡片打出来却导成 play”）。
_PSEUDO_AUDIO_WORDS = {w.strip('（）()'): v
                       for w, v in AUDIO_PSEUDO_ACTION.items()}
_PSEUDO_AUDIO_RE = re.compile(
    r'^[（(](' + '|'.join(re.escape(w) for w in _PSEUDO_AUDIO_WORDS)
    + r')[)）]\s*(.*)$')

# 原生 stop 行直接写在音频格里（从 .rpy 复制过来）：
# 以前它被当成曲名，导出成 `play music "stop music fadeout 1.0"` + TODO。
_NATIVE_STOP_RE = re.compile(r'^stop\s+(music|sound)\b\s*(.*)$',
                             re.IGNORECASE)


def _params_only(tokens: list) -> str:
    """
    只挑参数（`fadeout 1.0` / `loop`），**忽略**其它词。

    停止动作后面只允许参数；非参数词如果被糊成参数，会生成语法错的
    `stop music bgm03`，比丢掉更糟。
    """
    params = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _AUDIO_PARAM_RE.match(tok):
            params.append(tok)
            if tok in ('fadein', 'fadeout', 'fade', 'volume', 'channel') \
                    and i + 1 < len(tokens):
                params.append(tokens[i + 1])
                i += 2
                continue
            i += 1
            continue
        i += 1
    return ' '.join(params)

# 原生写法的头部关键字（直通后门）
_NATIVE_HEADS = ('show', 'hide', 'scene', 'with', 'pause', 'window')

# `_NOT_ACTOR_TARGETS`（`show screen x` / `show layer x` 这类非图像目标）
# 与 `NON_ACTOR_TAGS`（背景 / CG / UI 等非角色 tag）都已收敛到
# `storygraph.core.vocabulary`，顶部导入后在本模块继续可见
# （`script_table` / `beat_editor` 也从这里取 NON_ACTOR_TAGS）。


def normalize_text(text: str) -> str:
    """
    统一**分隔符类**的全角标点 → 半角，去零宽字符。

    刻意只动分隔符，不动 `，`/`（`/`）`：它们在中文台词与素材文件名里
    是**内容**（`bgm/夜晚，断电之后….mp3` 里那个逗号必须原样保留），
    归一化会把资产路径改坏——这不是洁癖，是踩过的坑。
    """
    if not text:
        return ''
    table = {
        '；': ';', '【': '[', '】': ']', '　': ' ',
        '\u200b': '', '\ufeff': '',
    }
    for k, v in table.items():
        text = text.replace(k, v)
    return text


def split_ops(text: str) -> list:
    """把一格里的多条指令拆开（`;` 或换行分隔）。"""
    text = normalize_text(text)
    if not text:
        return []
    parts = []
    for chunk in re.split(r'[;\n]', text):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


# ---------------------------------------------------------------------------
# 解析结果
# ---------------------------------------------------------------------------

@dataclass
class StageOp:
    """一条舞台指令。"""
    verb: str = ''                 # show / hide / scene / with / pause / comment / unknown
    native: bool = False           # True = 原生写法，直通不校验
    raw: str = ''                  # 原始文本（native 或 pause 用）
    target: str = ''               # 角色词 / 图像词
    attrs: list = field(default_factory=list)   # 表情等附加属性
    at: str = ''                   # 位置 / 变换
    with_: str = ''                # 转场
    source: str = ''               # 原始片段，写进"原始意图"列


@dataclass
class AudioOp:
    """一条音频指令。"""
    channel: str = 'sound'         # music / sound / voice
    action: str = 'play'           # play / queue / stop
    name: str = ''
    params: str = ''               # 'fadein 1.0 loop' 之类
    source: str = ''


@dataclass
class ParsedStage:
    ops: list = field(default_factory=list)        # [StageOp]
    problems: list = field(default_factory=list)   # [(source, message)]


# ---------------------------------------------------------------------------
# 解析：人话 -> 中间表示
# ---------------------------------------------------------------------------

def _tokens(rest: str) -> list:
    return [t for t in re.split(r'\s+', rest.strip()) if t]


def _split_at_with(tokens: list) -> tuple:
    """
    从 token 流里挑出 `at X` / `with Y`，剩下的按顺序分成 (核心, 附加)。

    兼容这些写法：
        [显示] 凌跃捷 开心 左
        [显示] 凌跃捷 开心 at l11
        [显示] 凌跃捷 开心 左 with dissolve
        [显示] 凌跃捷@左 开心
    """
    core, at, with_, i = [], '', '', 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ('at', '@') and i + 1 < len(tokens):
            at = tokens[i + 1]
            i += 2
            continue
        if tok == 'with' and i + 1 < len(tokens):
            with_ = tokens[i + 1]
            i += 2
            continue
        core.append(tok)
        i += 1
    return core, at, with_


def _split_target(core: list) -> tuple:
    """
    第一个 token 是目标；`目标@位置` 也算。

    返回 (target, attrs)
    """
    if not core:
        return '', []
    head = core[0]
    extra_at = ''
    if '@' in head:
        head, _, extra_at = head.partition('@')
    attrs = list(core[1:])
    if extra_at:
        attrs.insert(0, '@' + extra_at)
    return head, attrs


def parse_stage(text: str) -> ParsedStage:
    """
    解析 Stage 单元格。

    每个片段要么是 `[动词] 参数…`（被校验），要么是原生 Ren'Py 写法（直通）。
    """
    out = ParsedStage()
    for chunk in split_ops(text):
        m = re.match(r'^\[([^\]]*)\]\s*(.*)$', chunk)
        if not m:
            # 裸文本一律**原样直通**（`_native_op`）。
            #
            # 这格是"逃生通道"：写什么就出什么，工具**不替作者判断**
            # "这行看起来像不像备忘"。曾经试过自动补 `#`
            # （`_looks_like_stage_note`，2026-10-28 → 10-29 撤掉）：
            # 判据只能看字面，而 `bg1.2 bgm13`（备忘）和 `weather6 rain`
            # （fork 引擎的自定义语句）字面上完全同构 —— 结果是把作者
            # 真正要执行的语句注释掉了，卡片上看着还是原文，导出后
            # 行为静默消失。用户 2026-10-29 拍板：**补 `#` 只发生在备注列**。
            out.ops.append(_native_op(chunk))
            continue
        word = (m.group(1) or '').strip()
        rest = (m.group(2) or '').strip()
        verb = VERB_ALIAS.get(word, '')
        if verb not in STAGE_VERBS:
            out.problems.append(
                (chunk, f"不认识的舞台指令 '{word}'"))
            out.ops.append(StageOp(verb='unknown', raw=chunk, source=chunk))
            continue
        out.ops.append(_build_stage_op(verb, rest, chunk))
    return out


def _native_op(chunk: str) -> StageOp:
    """原生 Ren'Py 写法：直通后门。"""
    head = chunk.split()[0].lower() if chunk.split() else ''
    if head in ('with', 'pause'):
        verb = head
    elif head in _NATIVE_HEADS:
        verb = head
    else:
        verb = 'raw'
    return StageOp(verb=verb, native=True, raw=chunk, source=chunk)


def _build_stage_op(verb: str, rest: str, chunk: str) -> StageOp:
    if verb == 'pause':
        return StageOp(verb='pause', raw=f"pause {rest}".strip(),
                       source=chunk)
    if verb == 'with':
        return StageOp(verb='with', with_=_tokens(rest)[0] if _tokens(rest)
                       else '', source=chunk)

    tokens = _tokens(rest)
    if not tokens:
        return StageOp(verb='unknown', raw=chunk, source=chunk)
    core, at, with_ = _split_at_with(tokens)
    target, attrs = _split_target(core)
    attrs = [a for a in attrs if a]
    for a in attrs:
        if a.startswith('@') and not at:
            at = a[1:]
    attrs = [a for a in attrs if not a.startswith('@')]
    return StageOp(verb=verb, target=target, attrs=attrs, at=at,
                   with_=with_, source=chunk)


def parse_audio(text: str, channel: str) -> list:
    """
    解析 BGM / SFX / 语音单元格，返回 [AudioOp]。

    支持：
        [音乐] 春天花瓣 fadein 1.0
        [音乐] 停止 fadeout 2.0
        [停止音乐]
        [音效] 开门
        [语音] v_001
        （方括号可省略：直接写 `春天花瓣 fadein 1.0`）
    """
    ops = []
    for chunk in split_ops(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        # 伪值（`（停止音乐）` / `（排队播放）`）：动作不在方括号里，
        # 是与 Excel 下拉候选表同一套词。后面可以直接跟参数。
        m_pseudo = _PSEUDO_AUDIO_RE.match(chunk)
        if m_pseudo:
            word, rest = m_pseudo.group(1), (m_pseudo.group(2) or '').strip()
            action, ch = _PSEUDO_AUDIO_WORDS[word]
            ops.append(_build_audio_op(ch or channel, rest, action, chunk))
            continue
        # 原生 stop 行（`stop music fadeout 1.0`）直接写在音频格里
        m_native = _NATIVE_STOP_RE.match(chunk)
        if m_native:
            ops.append(_build_audio_op(m_native.group(1).lower(),
                                       (m_native.group(2) or '').strip(),
                                       'stop', chunk))
            continue
        m = re.match(r'^\[([^\]]*)\]\s*(.*)$', chunk)
        verb = ''
        rest = chunk
        forced = ''
        if m:
            word = (m.group(1) or '').strip()
            # `[音效(queue)]` = "排队播放"：动作写在动词后缀里
            suffix = re.match(r'^(.+?)[（(](queue|play|stop)[)）]$', word)
            if suffix:
                word, forced = suffix.group(1).strip(), suffix.group(2)
            verb = VERB_ALIAS.get(word, '')
            rest = (m.group(2) or '').strip()
            if verb not in AUDIO_VERBS:
                # 方括号里不是音频动词（比如把 [显示] 写进音乐格）→ 忽略并留痕
                ops.append(AudioOp(channel=channel, action='unknown',
                                   name=chunk, source=chunk))
                continue
        ch = channel
        action = 'play'
        if verb in ('music', 'music_stop', 'sound_stop', 'voice'):
            ch = {'music': 'music', 'music_stop': 'music',
                  'sound_stop': 'sound', 'voice': 'voice'}[verb]
            if verb.endswith('_stop'):
                action = 'stop'
        if forced:
            action = forced
        ops.append(_build_audio_op(ch, rest, action, chunk))
    return ops


def _build_audio_op(channel: str, rest: str, action: str, chunk: str) -> AudioOp:
    tokens = _tokens(rest)
    if action == 'stop':
        # 停止动作后面**只允许参数**（`fadeout 1.0`）。
        # 从前第一个 token 被当曲名：`[停止音乐] fadeout 1.0` 的 fadeout
        # 死在 name 里，导出成裸 `stop music`（2026-10-28 实测的数据丢失）。
        return AudioOp(channel=channel, action='stop', name='',
                       params=_params_only(tokens), source=chunk)
    if not tokens:
        # `[停止音乐]` 这种把动作写在动词里的形式已在上面处理；
        # 到这里还没有名字 = 无法识别的指令（`[音乐]` 裸写）。
        return AudioOp(channel=channel, action='unknown', name=chunk,
                       source=chunk)

    # 名字：支持带引号的路径，或第一个非参数 token
    name = ''
    params = []
    idx = 0
    first = tokens[0]
    if first.startswith('"') or first.startswith("'"):
        quote = first[0]
        if len(first) > 1 and first.endswith(quote):
            name = first[1:-1]
            idx = 1
        else:
            buf = [first[1:]]
            idx = 1
            while idx < len(tokens) and not tokens[idx].endswith(quote):
                buf.append(tokens[idx])
                idx += 1
            if idx < len(tokens):
                buf.append(tokens[idx][:-1])
                idx += 1
            name = ' '.join(buf)
    else:
        name = first
        idx = 1

    if name in _STOP_WORDS:
        action = 'stop'
        name = ''

    # 参数：识别 fadein/fadeout/loop 等，其余当名字的一部分（含空格的文件名）
    while idx < len(tokens):
        tok = tokens[idx]
        if _AUDIO_PARAM_RE.match(tok):
            params.append(tok)
            if tok in ('fadein', 'fadeout', 'fade', 'volume', 'channel') \
                    and idx + 1 < len(tokens):
                params.append(tokens[idx + 1])
                idx += 2
                continue
            idx += 1
            continue
        if name:
            name = f"{name} {tok}"
        else:
            name = tok
        idx += 1

    if not name and action != 'stop':
        return AudioOp(channel=channel, action='unknown', name=chunk,
                       source=chunk)
    return AudioOp(channel=channel, action=action, name=name,
                   params=' '.join(params), source=chunk)


# ---------------------------------------------------------------------------
# 符号表：把"人话"落到资源上
# ---------------------------------------------------------------------------

_QUOTE_CHARS = '「」『』""''\u3000 '


def _clean_name(text: str) -> str:
    text = (text or '').strip()
    return text.strip(_QUOTE_CHARS)


#: 属性名的分词符：`close_happy` → {close, happy}，`base-arms` → {base, arms}
_ATTR_TOKEN_SPLIT = re.compile(r'[_\-\s]+')


def _attr_has_token(attr: str, mapped: str) -> bool:
    """
    核心词是否落在属性名的**词边界**上。

    `close_happy` vs `happy` → True
    `unhappy`      vs `happy` → False（这是关键：子串匹配会把它算命中，
                               猜错表情就是演出事故）
    """
    attr = (attr or '').strip().lower()
    mapped = (mapped or '').strip().lower()
    if not attr or not mapped:
        return False
    return mapped in _ATTR_TOKEN_SPLIT.split(attr)


#: 媒体扩展名：用来判断"这看起来是文件路径还是资产 ID"
_MEDIA_EXTS = ('.mp3', '.ogg', '.wav', '.opus', '.flac', '.m4a', '.mp2',
               '.webm', '.avi', '.mp4', '.png', '.jpg', '.jpeg', '.webp',
               '.gif', '.bmp')


def renpy_asset_path(path) -> str:
    """
    资源表里的 Path → **能写进 Ren'Py 脚本**的相对路径。

    Ren'Py 引用素材时，路径一律相对 `game/`（`"bgm/x.mp3"`）。但资产表里
    的 Path 是扫描时按"扫描根"拼出来的，基准不统一：
    `sr_general/game/bgm/x.mp3`、`game/images/x.png`、`images/x.png` 都有。
    带项目名的前缀直接抄进脚本，玩家那边就是找不到文件的死引用。

    规则（只动**确定要动**的）：
      · 路径里有 `game/` 段 → 取它之后的部分。这是 Ren'Py 的素材基准，
        专门治 `sr_general/game/bgm/x.mp3` 这类"扫的是项目上级目录"的情况；
      · 没有 `game/` 段 → **原样保留**。`bgm/spring.mp3` 本身就已经是
        合法写法，猜错前缀比留着一个多余目录更糟。
    """
    text = str(path or '').strip().replace('\\', '/')
    if not text:
        return ''
    parts = [p for p in text.split('/') if p not in ('', '.')]
    if not parts:
        return ''
    if 'game' in parts:
        tail = parts[parts.index('game') + 1:]
        if tail:
            return '/'.join(tail)
    return '/'.join(parts)


class Symbols:
    """
    资源表的"符号表"（Symbol Table）。

    Candidates_* 那几张表不是给策划看的，是给编译器查的字典：
    这里把它们索引成"人话 → 资产"的映射，并回答"这个引用到底存不存在"。

    关键克制（否则新项目一打开就满屏 TODO）：
      某一类资产**压根没建表**时，任何引用都放行——我们无从判断对错。
      只有"表里有东西，但你写的名字不在其中"才算真的找不到。
    """

    def __init__(self, registry=None, keywords=None, index=None):
        self.registry = registry
        self.positions = dict(DEFAULT_POSITIONS)
        self.expressions = dict(DEFAULT_EXPRESSIONS)
        #: **项目自己在词表里声明过的**人话词（`{'expression': {...}, 'position': {...}}`）。
        #:
        #: 为什么要单独记（2026-10-05 用户实测报）：
        #: > 如果你标中文人话词，那么你就要遇到一个问题：万一……这个项目不用中文
        #: > 做怎么办？工具应是通用的！不应该只能做具体的项目！
        #:
        #: `positions` / `expressions` 是**内置词表 + 项目词表**的合并结果，
        #: 界面从前直接拿它当候选 → 一个不用中文做的项目也会看到 `左` / `生气`。
        #: 现在分得清"这是项目自己的说法"还是"工具的内置猜测"：
        #:   · **候选**只给项目真实的名字（属性名 / transform 名）；
        #:   · **显示当前值**时，只有项目自己声明过的词才优先显示（那是它的习惯）；
        #:   · 内置词继续当**输入别名**（策划打 `生气` 照样能落到 `angry`）。
        self.project_words = {'expression': set(), 'position': set()}
        self._by_var = {}
        self._by_name = {}
        self._attrs_by_tag = {}
        #: 结构化的维度：`{tag: [{'group', 'value'}, …]}`（2026-10-19）。
        #: 来源是资源表的 `Group`/`Value` 列；老表没有时按 `Attributes`
        #: 逐词展开（`group=''`）—— 行为与从前一致，只是多了一个入口。
        self._dims_by_tag = {}
        #: **有成套立绘的 tag**（路径目录里写着 `characters` / `立绘` / `sprite`…）。
        #:
        #: 与 `_attrs_by_tag` 的区别：那个只说明"有这个 tag 的图"，
        #: 这个还要求"作者把它放在立绘目录里" —— 是**写出来的**证据，
        #: 不是位置猜测（`images/nbg/bg1.1.png` 那 42 张背景进不来）。
        #:
        #: 用途：卡片里写了 `qing base` 而 `qing` 没有角色认领时，
        #: 提示要说清"它确实有 4 张立绘，只是角色表没认领"，
        #: 而不是把用户打发去「资源映射」（2026-11-03 用户实测）。
        self._sprite_tags = set()
        #: **项目 `layeredimage` 声明过的 tag** → `{tag: [属性, …]}`（第四路）。
        #:
        #: 为什么必须有这一路（2026-11-03，用户第二次报同一件事）：
        #: `definitions.rpy` 里写着
        #:
        #:     layeredimage qing:
        #:         image_format 'images/characters/qing/{image}.png'
        #:         attribute base default: "normal"
        #:         group expressions:
        #:             attribute angry: 'speechless'
        #:
        #: 也就是说 **`show qing base` 是合法的**（`base` 映射到 `normal.png`），
        #: 而 `qing` 这个 tag 在 `characters.xlsx` 里根本没有对应行
        #: （出声的是 `q`，显示名「青」）。只查角色表的判据会把**引擎认识的东西**
        #: 判成"不是立绘" —— 用户看到的正是"预览有图、提示说没有"。
        #:
        #: 数据来自 `asset_identity.declared_sprite_attrs`（属性名本身；
        #: `layeredimage`，以及项目里直接的 `image` / `renpy.image()` 声明）。
        #:
        #: ⚠ **懒加载**（2026-11-04）：读声明要扫一遍 `.rpy`，而 `Symbols` 在
        #: 编译/导出/金标里会被构造很多次（真机 hydrate 金标每文件一次）。
        #: 只有真正问"这个属性声明过吗"的提示层才付这笔钱。
        self._declared_attrs = {}
        #: 项目在 `init python` / `python` 块 / `define` 里**定义过**的代号词。
        #: `None` = 还没读（不是空集）。
        #:
        #: 为什么需要（用户："`aa` 明明是服装代号，这你也查不到？"）：
        #: `aa` 写在 `CC_OUTFIT_CYCLE` / `CC_OUTFIT_LABEL` 这类**数据结构**里
        #: （界面换装循环的代号，"普通"），**不是** `layeredimage` 的
        #: `attribute`，资源表也没登记 —— 只看那两路会说"没有这个属性"。
        #:
        #: ⚠⚠ 判据落在**定义位置**，不落在使用位置：第一版用"剧本里 `show` 过
        #: 就算认识"，用户当场否掉（"万一剧本错了呢？万一我用了 `ba` 呢？"）。
        self._project_words = None
        self._declared_loaded = False
        self._project_root = str(getattr(registry, 'project_root', '') or '')
        #: 引擎说"这几个 image 名不存在"（归一化键 `lan|aa|happy`）。
        #: 空集 = 还没问过 / 问失败（**不是**"全都合法"）。
        self._engine_bad = set()
        #: 引擎裁决的新鲜度缓存（`None` = 还没算过）。
        #: ⚠ 别在这里再写 `self._project_root = ''` —— 上面已经赋过真值了，
        #: 覆盖成空串会让 `engine_verdict_state()` 永远问空路径 → `unknown`
        #: （2026-11-05 自己踩的：两次 replace 顺序反了）。
        self._verdict_state = None
        root = self._project_root
        if root:
            try:
                # **引擎的裁决**（打开项目时后台跑出来的缓存）——只读一个小
                # JSON，不扫盘，所以放在构造里。
                from storygraph.assets.engine_images import bad_keys
                self._engine_bad = bad_keys(root)
            except Exception as e:                  # noqa: BLE001 - 读不到就当没有
                swallow('story.dsl.Symbols.engine_bad', e)
        self._index = index

        for category, target in (keywords or {}).items():
            bucket = {'位置': self.positions, '表情': self.expressions,
                      'position': self.positions,
                      'expression': self.expressions}.get(category)
            if bucket is None:
                continue
            kind = 'position' if bucket is self.positions else 'expression'
            for word, mapped in (target or {}).items():
                bucket[word] = mapped
                self.project_words[kind].add(word)

        self._build()

    # ---------- 索引 ----------

    def _build(self):
        reg = self.registry
        if reg is None:
            return
        for var, row in (getattr(reg, 'characters', {}) or {}).items():
            row = row or {}
            self._by_var[var] = row
            display = _clean_name(row.get('Name') or '')
            if display:
                self._by_name.setdefault(display, row)
            tag = (row.get('ImageTag') or '').strip()
            if tag:
                self._by_name.setdefault(_clean_name(tag), row)

        for aid, row in (getattr(reg, 'images', {}) or {}).items():
            row = row or {}
            tag = (row.get('Tag') or '').strip()
            if not tag:
                continue
            attrs = self._attrs_by_tag.setdefault(tag, [])
            for a in (row.get('Attributes') or '').split():
                if a and a not in attrs:
                    attrs.append(a)
            for a in (row.get('_attrs') or []):
                if a and a not in attrs:
                    attrs.append(a)
            # **这个 tag 有"放在立绘目录里"的图吗**（2026-11-03）。
            #
            # 为什么不能只看"有这个 tag 的图"：`game/images/nbg/bg1.1.png`
            # 那 42 张背景被位置猜测判成了 `character`，只看 tag 会把背景
            # 也算成"一套立绘"。判据取**路径目录写着 characters/立绘/sprite** ——
            # 写出来的字才是证据（唯一实现在 `asset_identity`）。
            if is_under_sprite_container(str(row.get('Path') or '')):
                self._sprite_tags.add(tag)
        self._dims_by_tag = dimensions_by_tag(
            (getattr(reg, 'images', {}) or {}).values())

    def _get_index(self):
        if self._index is None and self.registry is not None:
            from storygraph.assets.asset_registry import AssetIndex
            self._index = AssetIndex(self.registry)
        return self._index

    # ---------- 类别是否存在 ----------

    def has(self, kind: str) -> bool:
        """这一类资源表里有没有东西？没有 = 无从校验，一律放行。"""
        reg = self.registry
        # 类型 → 集合属性名：唯一真源在 core.vocabulary
        #（2026-09-28 之前这里内联了一份，与 asset_registry / validate 各自为政）
        attr = ASSET_TYPE_ATTR.get(kind)
        if reg is None or not attr:
            return False
        return bool(getattr(reg, attr, {}) or {})

    # ---------- 角色 ----------

    def actor(self, word: str) -> Optional[dict]:
        word = (word or '').strip()
        if not word:
            return None
        if word in self._by_var:
            return self._by_var[word]
        return self._by_name.get(_clean_name(word))

    def actor_image_tag(self, word: str) -> str:
        row = self.actor(word)
        if not row:
            return ''
        return (row.get('ImageTag') or row.get('ID') or '').strip()

    def actor_display(self, var_or_name: str) -> str:
        row = self.actor(var_or_name)
        if not row:
            return var_or_name or ''
        return _clean_name(row.get('Name') or '') or row.get('ID') or ''

    def is_actor_tag(self, tag: str) -> bool:
        tag = (tag or '').strip()
        if not tag:
            return False
        if tag.lower() in NON_ACTOR_TAGS:
            return False
        for row in self._by_var.values():
            if (row.get('ImageTag') or row.get('ID')) == tag:
                return True
        # 角色表为空时，只能按"非背景/CG/UI"粗略判定
        return tag in self._attrs_by_tag and not self.has('character')

    # ---------- 表情 / 位置 ----------

    def attrs_of_tag(self, tag: str) -> list:
        return list(self._attrs_by_tag.get((tag or '').strip(), []))

    def has_sprites(self, tag: str) -> bool:
        """
        这个 tag 有**放在立绘目录里**的图吗（`images/characters/<tag>/…`）。

        注意它回答的不是"这个 tag 是角色"（那要角色表认领，见 `is_actor_tag`），
        而是"这套立绘素材真的存在"。两者分开，提示才能说准话：
        素材在、角色没认领 —— 那一步得人去角色表填「立绘 tag」。
        """
        return (tag or '').strip() in self._sprite_tags

    def engine_bad_keys(self) -> set:
        """引擎说"不是图像"的归一化键（`lan|aa|happy`）；空集 = 没问过。"""
        return set(self._engine_bad)

    def engine_verdict_state(self) -> str:
        """
        这份引擎裁决**还新不新** → `'fresh' | 'stale' | 'unknown'`。

        ⚠ **懒算**（2026-11-05 L1-2）：算它要 stat 93 个 `.rpy`，真机
        39–68 ms —— 塞进 `__init__` 会让**每张卡片**都等一次。所以第一次
        问的时候才算，结果缓存在 `self._verdict_state`。

        为什么要这一层：`.rpy` 改过之后（加了 `attribute` / 删了 `image`），
        旧裁决会**说反话** —— 那比"没有裁决"更糟。
        """
        if self._verdict_state is None:
            try:
                from storygraph.assets.engine_images import verdict_state
                self._verdict_state = verdict_state(self._project_root)
            except Exception as e:              # noqa: BLE001 - 算不出来就说不知道
                swallow('story.dsl.engine_verdict_state', e)
                self._verdict_state = 'unknown'
        return self._verdict_state

    def defined_words(self) -> list:
        """项目 `init python` / `define` 里**定义过**的代号词（与 tag 无关）。

        ⚠ 名字不能叫 `project_words` —— `Symbols` 早就有一个**属性**
        `project_words`（关键词表 `{kind: {词}}`），撞上去就是
        `'dict' object is not callable`。

        ⚠ 懒加载：第一次问才扫 `.rpy`（`None` 是"还没读"，空集是"读过了"）。
        """
        if self._project_words is None:
            self._project_words = self._load_project_words()
        return sorted(self._project_words)

    def _load_project_words(self) -> set:
        root = self._project_root
        if not root:
            return set()
        try:
            from storygraph.assets.asset_identity import (
                project_literal_words)
            return set(project_literal_words(root) or ())
        except Exception as e:                      # noqa: BLE001 - 读不出来就当没有
            swallow('story.dsl.Symbols.project_words', e)
            return set()

    def token_image_path(self, tag: str, token: str) -> str:
        """
        这个词按 `layeredimage` 的 `image_format` 展开后**真实存在**的图片
        （项目相对路径，`''` = 没有）。

        为什么"有图"也算证据：声明可能漏写 `attribute`，但图在 ——
        真机 `images/characters/lan/lose.png` 存在、剧本写 `show lan aa lose`。
        只查声明会说"没有这个属性"，而用户要的是"找到对应的图"。
        """
        tag = str(tag or '').strip()
        token = str(token or '').strip()
        if not tag or not token or not self._project_root:
            return ''
        try:
            from storygraph.story.layered_model import load_layer_model_cached
            model = load_layer_model_cached(self._project_root)
            img = (getattr(model, 'images', None) or {}).get(tag)
            fmt = str(getattr(img, 'image_format', '') or '')
        except Exception as e:                      # noqa: BLE001 - 结构怪就当没有
            swallow('story.dsl.Symbols.token_image', e)
            return ''
        if not fmt:
            return ''
        rel = fmt.replace('{image}', token)
        root = Path(self._project_root)
        for base in (root / 'game', root):
            path = base / rel
            if path.is_file():
                try:
                    return str(path.relative_to(root)).replace('\\', '/')
                except ValueError:
                    return str(path).replace('\\', '/')
        return ''

    def resolved_paths_of(self, tag: str, attrs) -> list:
        """
        `show <tag> <attrs>` **实际会叠出哪些图**（项目相对路径，只列存在的）。

        这是"找到对应的图"的唯一实现：走 `layeredimage` 的组条件 / 默认属性
        / 函数展开（`resolve_layers`），不是按文件名猜。
        """
        if not self._project_root:
            # 没有项目根时连扫都不扫：`Path('') == '.'`，会把仓库当项目
            # （2026-11-05 实测界面卡死，见 `layered_model.load_layer_model`）。
            return []
        try:
            from storygraph.story.layered_model import (
                load_layer_model_cached, resolve_layers)
            model = load_layer_model_cached(self._project_root)
            plan = resolve_layers(model, str(tag or ''), list(attrs or ()),
                                  self._project_root)
        except Exception as e:                      # noqa: BLE001 - 判不了就不给
            swallow('story.dsl.Symbols.resolved_paths', e)
            return []
        root = Path(self._project_root)
        out = []
        for path in plan.paths():
            try:
                out.append(str(Path(path).relative_to(root)).replace('\\', '/'))
            except ValueError:
                out.append(str(path).replace('\\', '/'))
        return out

    def _ensure_declared(self) -> None:
        """第一次问 `declared_attrs_of` 时读声明（见 `_declared_attrs` 的说明）。"""
        if self._declared_loaded:
            return
        self._declared_loaded = True               # 先置位：读失败也不反复扫
        root = self._project_root
        if not root:
            return
        try:
            from storygraph.assets.asset_identity import (
                declared_sprite_attrs, image_statement_words)
            # ① `layeredimage` 的属性名（**全部**，不是反查表的唯一值）
            # ② 直接的 `image <tag> <attr> …` / `renpy.image("tag attr")`
            #    —— 引擎认这些名字，但不是 `layeredimage` 的 attribute。
            groups = (declared_sprite_attrs(root) or {},
                      image_statement_words(root) or {})
        except Exception as e:                      # noqa: BLE001 - 声明读不出来就当没有
            swallow('story.dsl.Symbols.declared', e)
            return
        for mapping in groups:
            for _tag, _names in mapping.items():
                bucket = self._declared_attrs.setdefault(str(_tag), [])
                for _name in _names:
                    _name = str(_name).strip()
                    if _name and _name not in bucket:
                        bucket.append(_name)

    def declared_attrs_of(self, tag: str) -> list:
        """
        **项目脚本自己声明过**的属性名（`layeredimage qing:` 的 attribute）。

        `show qing base` 之所以合法，就是因为这里声明了 `attribute base`；
        属性名与**文件名**经常不是一回事（`attribute angry: 'speechless'`
        —— 文件叫 speechless、属性叫 angry）。所以候选与校验都该用这一份。

        也包含项目里直接的 `image <tag> <attr>` / `renpy.image("tag attr")`
        声明（引擎认，但不是 `layeredimage` 的属性）。

        没有声明 / 没有项目根 → `[]`（不猜）。
        """
        self._ensure_declared()
        return list(self._declared_attrs.get((tag or '').strip(), []))

    def sprite_tags_without_owner(self) -> dict:
        """
        **有立绘素材、但角色表没认领**的 tag → `{tag: [属性, …]}`（按 tag 排序）。

        给资源管理器的体检 / 诊断面板用：一次列出"这些立绘没人认领"，
        比用户在 1979 行表里猜哪条是立绘可靠得多（2026-11-03）。
        """
        out = {}
        for tag in sorted(self._sprite_tags):
            if self.is_actor_tag(tag):
                continue
            attrs = self._attrs_by_tag.get(tag) or []
            out[tag] = list(attrs)
        return out

    def dimensions_of_tag(self, tag: str) -> list:
        """
        这个 tag 的**结构化维度**：`[{'group', 'value'}, …]`。

        与 `attrs_of_tag` 的区别：后者只有词，前者说清"这个词属于哪一维"
        （`outfit=base_swimsuit` / `expressions=happy`）。老表没有结构化列时
        每个词仍然回来，只是 `group=''` —— 调用方据此走老路，不会更差。
        """
        tag = (tag or '').strip()
        return [dict(d) for d in self._dims_by_tag.get(tag, [])]

    def dimension_groups_of_tag(self, tag: str) -> dict:
        """
        `{维度: [值, …]}`（只收**认得出**的维度；按首次出现顺序）。

        这是"一维一格"与"同组不能选两个"共用的结构来源；认不出返回 `{}`，
        调用方**不许**把 `{}` 当成"没有维度"以外的任何结论。
        """
        out: dict = {}
        for item in self.dimensions_of_tag(tag):
            group = str(item.get('group') or '')
            value = str(item.get('value') or '')
            if not group or not value:
                continue
            bucket = out.setdefault(group, [])
            if value not in bucket:
                bucket.append(value)
        return out

    def same_group_conflicts(self, tag: str, used) -> list:
        """
        同组同时选了两个属性 → `[{'group','attrs'}, …]`（只报告，不改写）。

        与 `engine_facts.group_conflicts` 同一语义；差别是它不要求采过引擎
        事实 —— 资源表里的 `Group` 列就是那一份结构的副本。
        """
        from storygraph.assets.asset_dimensions import same_group_conflicts
        return same_group_conflicts(self.dimensions_of_tag(tag), used)

    def has_image_tag(self, tag: str) -> bool:
        """资源表里存在这个 image tag 吗（Ren'Py 的 `show <tag> <属性>`）。"""
        return bool((tag or '').strip()) and \
            (tag or '').strip() in self._attrs_by_tag

    def image_spec_ok(self, spec: str) -> bool:
        """
        这个图像名**能不能原样写进脚本**（生成代码时决定要不要改写成下划线）。

        比 `image_ok` 宽松，因为它回答的是不同的问题：
          · `image_ok`   —— "这条引用在资源表里查得到吗"（校验用，要严）
          · `image_spec_ok` —— "这个名字形态能不能跑"（生成用，别乱改）

        Ren'Py 的 `show ling base_arms_akimbo close_happy` 是合法的
        `tag + 多个属性`，只要 `ling` 这个 tag 存在就一定能跑——哪怕资源表里
        没有"这个属性组合"的那一行。曾经这里只认 `image_ok`，于是真实项目里
        大量立绘被判"查不到"，生成时被改写成 `ling_base_arms_akimbo_close_happy`
        ——**把能跑的脚本改坏了**，而且让导出/导入两个方向生成的代码不一样。
        """
        spec = (spec or '').strip()
        if not spec:
            return True
        if not self.has('image'):
            # 没有图片表 = 无从判断，保持作者写法
            return True
        if self.image_ok(spec):
            return True
        tag = spec.split()[0] if spec.split() else ''
        if tag and len(spec.split()) > 1 and self.has_image_tag(tag):
            return True
        # `bg room`: bg 是背景 tag，属性就是房间名
        return bool(tag) and self.has_image_tag(tag)

    def attr_for(self, tag: str, word: str) -> str:
        """
        表情词 → 图像属性。**分档匹配，且能解释为什么命中**。

        为什么不能再只做"子串模糊匹配"：策划写「开心」，属性里同时有
        `close_happy` / `unhappy` 时，`mapped in a` 会把 `unhappy` 也算命中
        `happy`——**猜错一个表情就是演出事故**（用户评审第 11 条）。

        档位（从强到弱，命中即返回）：
          1. 词表显式命中：项目在「词表」里写了 `开心 → close_happy`
             （或者直接写属性名），这是**权威**，不再做任何猜测；
          2. 属性名整体等于核心词：`happy` == `happy`；
          3. 下划线分词命中：`close_happy` 切成 {close, happy}，
             核心词 `happy` 落在词边界上——`unhappy` 不会被切出 `happy`；
          4. 仅当该 tag **没有任何分词能命中** 时，才退回子串匹配
             （兼容 `e_happy` 这种没按词表命名的情况）。

        候选属性为空（项目没声明属性）→ 原样输出，交给 Ren'Py。
        """
        word = (word or '').strip()
        if not word:
            return ''
        mapped = self.expressions.get(word, word)
        if not mapped:
            return ''
        attrs = self.attrs_of_tag(tag)
        if not attrs:
            return mapped
        # 1) 权威：词表里已经写成了该 tag 的真实属性名
        if mapped in attrs:
            return mapped
        # 2) 分词命中（词边界）
        token_hits = [a for a in attrs if _attr_has_token(a, mapped)]
        if token_hits:
            return sorted(token_hits, key=len)[0]
        # 3) 兜底子串（会命中 unhappy⊃happy 这类，所以放最后）
        sub_hits = [a for a in attrs if mapped in a]
        if sub_hits:
            return sorted(sub_hits, key=len)[0]
        return mapped

    def position_for(self, word: str) -> str:
        """
        位置词 → transform。

        优先：项目词表 → 项目里真实存在的 transform → 内置 left/center/right。
        """
        word = (word or '').strip()
        if not word:
            return ''
        if word in self.positions:
            return self.positions[word]
        reg = self.registry
        if reg is not None and word in (getattr(reg, 'transforms', {}) or {}):
            return word
        return word        # 已经是英文/自定义名，原样用

    def position_word_for(self, transform: str) -> str:
        """反向：transform → 位置词（把原生脚本汉化回表格时用）。"""
        t = (transform or '').strip()
        if not t:
            return ''
        for word, mapped in self.positions.items():
            if mapped == t:
                return word
        return ''

    def expression_word_for(self, tag: str, attr: str) -> str:
        """
        反向：图像属性 → 表情词（把原生脚本汉化回表格时用）。

        同长候选按**词表声明顺序**取第一个（稳定排序），所以
        `happy` 会优先还原成先声明的「开心」而不是「高兴」——
        策划看到的是自己习惯的那个词。
        """
        attr = (attr or '').strip()
        if not attr:
            return ''
        cands = []
        for word, mapped in self.expressions.items():
            if not mapped:
                continue
            if mapped == attr or mapped in attr:
                cands.append((len(mapped), word))
        if not cands:
            return ''
        cands.sort(key=lambda c: -c[0])
        return cands[0][1]

    # ---------- 图像 ----------

    def image_rows_for(self, spec: str) -> list:
        """
        图像名 → 资源表里命中的那些行 `[(ID, row), …]`（走唯一解析链）。

        给"从名字反查 tag"用（用户 2026-10-05："表情也调不了了，修复！"）：
        卡片存的是下划线写法 `ling_angry`，要能从它读回真正的 `Tag='ling'`。
        """
        spec = (spec or '').strip()
        if not spec or not self.has('image'):
            return []
        data = getattr(self.registry, 'images', {}) or {}
        from storygraph.assets.asset_registry import resolve_references
        out = []
        for aid in resolve_references(spec, self.registry, self._get_index()):
            row = data.get(aid)
            if row is not None:
                out.append((aid, row))
        return out

    def image_ok(self, spec: str) -> bool:
        if not spec:
            return True
        if not self.has('image'):
            return True
        from storygraph.assets.asset_registry import resolve_references
        return bool(resolve_references(spec, self.registry, self._get_index()))

    def build_image_spec(self, tag: str, attrs: list) -> str:
        parts = [tag] + [a for a in attrs if a]
        return ' '.join(p for p in parts if p)

    # ---------- 音频 ----------

    #: 音频资产类型 → 资源表上的属性名（唯一真源：core.vocabulary）
    _AUDIO_ATTR = AUDIO_TYPE_ATTR

    def audio_row(self, kind: str, name: str) -> Optional[dict]:
        """
        音频引用 → 资源表那一行（查不到返回 None）。

        走 `asset_registry` 的**唯一解析链**（`AssetIndex.resolve`），
        不再自己造一套"别名比对"：真实项目里脚本写的是整条路径
        （`play music "bgm/bgm02.mp3"`、`"pomodorro/music/cafe.ogg"`），
        而表里存的是 ID（`bgm_bgm02`）。少了"路径逐级退化"这一步，
        明明文件存在、也明明登记过的音乐会被判成 `# TODO: 缺少资源`
        （实测 sr_general 全项目 47 条音乐 TODO 里有 27 条是这么来的）。
        """
        reg = self.registry
        attr = self._AUDIO_ATTR.get(kind)
        if reg is None or not attr:
            return None
        data = getattr(reg, attr, {}) or {}
        name = (name or '').strip()
        if not name:
            return None
        if name in data:
            return data[name]
        # 唯一真源：路径/别名/前缀统一由 AssetIndex 解析成资产 ID，
        # 再回到**本类别**的表里取行（resolve 会跨类型找，这里把住类型，
        # 免得音乐名恰好撞上某个图片前缀而被判成"音乐存在"）。
        from storygraph.assets.asset_registry import resolve_references
        for aid in resolve_references(name, reg, self._get_index()):
            row = data.get(aid)
            if row is not None:
                return row
        return None

    def audio_ok(self, kind: str, name: str) -> bool:
        if not name:
            return True
        if not self.has(kind):
            return True
        return self.audio_row(kind, name) is not None

    def audio_ref(self, kind: str, name: str) -> str:
        """
        写进脚本的音频引用。

        优先用资源表里的**真实文件路径**：策划写的是资产 ID（`春天花瓣`），
        而 Ren'Py 需要的是 `"bgm/春天花瓣剥落.mp3"`——这中间的翻译就是
        编译器的活。路径找不到才退回 ID 原文。

        资源表里的 Path **不是**能直接写进脚本的字符串：扫描时按"扫的是哪个
        目录"留下了五花八门的基准（`sr_general/game/bgm/x.mp3`、
        `game/images/x.png`、`images/x.png`）。Ren'Py 的素材路径一律相对
        `game/`，所以这里要把项目前缀削掉，否则生成的脚本在游戏里全是
        找不到文件的死引用（实测真实项目会输出
        `play music "sr_general/game/bgm/bgm02.mp3"`）。
        """
        row = self.audio_row(kind, name) or {}
        path = renpy_asset_path(row.get('Path'))
        return path or (name or '').strip()


# ---------------------------------------------------------------------------
# 审计：判定引用，并决定写进脚本的内容
# ---------------------------------------------------------------------------

# 判定结果
OK = 'ok'
TODO = 'todo'
PASS = 'pass'


class Audit:
    """
    编译期的引用审计器。

    它只回答三件事，**从不抛异常、从不阻止编译**：
      · ok   —— 引用成立
      · todo —— 表里有东西但它不在其中 → 生成 `# TODO: 缺少资源`
      · pass —— 这类资产没建表，无从判断 → 原样放行
    """

    def __init__(self, symbols: Symbols = None):
        self.symbols = symbols or Symbols()

    # 图片
    def image(self, spec: str) -> str:
        if not spec:
            return OK
        if not self.symbols.has('image'):
            return PASS
        return OK if self.symbols.image_ok(spec) else TODO

    # 角色
    def character(self, name: str) -> str:
        if not name:
            return OK
        if not self.symbols.has('character'):
            return PASS
        return OK if self.symbols.actor(name) else TODO

    # 音频（音乐/音效/语音）
    def audio(self, kind: str, name: str) -> str:
        if not name:
            return OK
        return OK if self.symbols.audio_ok(kind, name) else TODO

    def audio_ref(self, kind: str, name: str) -> str:
        return self.symbols.audio_ref(kind, name)


# ---------------------------------------------------------------------------
# 编译：中间表示 -> Ren'Py 代码
# ---------------------------------------------------------------------------

def compile_stage(text: str, symbols: Symbols, audit: Audit = None) -> list:
    """
    Stage 单元格 → [(原始意图, 代码行, 状态)]。

    方括号形式严格翻译；原生形式直通。找不到资源写 TODO 注释，
    **不生成**那条非法语句（生成了只会让游戏在运行时报错）。

    ⚠ 2026-10-23（清单 §十五）：从前签名尾部有个 `native_ok=True`，
    函数体一次都没读、全仓也没有调用方传它 —— 纯装饰开关，已删除。
    """
    audit = audit or Audit(symbols)
    rows = []
    parsed = parse_stage(text)

    for op in parsed.ops:
        if op.verb == 'unknown':
            rows.append((op.source,
                         f"# TODO: 无法识别的舞台指令 {op.source}",
                         TODO))
            continue

        if op.native:
            rows.append((op.source, op.raw, OK))
            continue

        if op.verb == 'pause':
            rows.append((op.source, op.raw or 'pause', OK))
            continue

        if op.verb == 'with':
            if not op.with_:
                rows.append((op.source, '# TODO: [转场] 缺少变换名', TODO))
            else:
                rows.append((op.source, f"with {op.with_}", OK))
            continue

        if op.verb == 'scene':
            code, status = _compile_scene(op, audit)
            rows.append((op.source, code, status))
            continue

        if op.verb in ('show', 'hide'):
            code, status = _compile_actor(op, audit)
            rows.append((op.source, code, status))
            continue

    return rows


def _compile_scene(op: StageOp, audit: Audit) -> tuple:
    """
    `[场景] bg 教室 左` / `[场景] 实验室 with dissolve`。

    第一个词是 tag（或完整图像名的一段），其余词里能当位置词的解释成 at，
    剩下的当属性。
    """
    scene_tag = op.target
    attr_words = []
    at = op.at
    for word in op.attrs:
        if word in audit.symbols.positions:
            if not at:
                at = audit.symbols.position_for(word)
            continue
        attr_words.append(word)

    spec = ' '.join([scene_tag] + attr_words).strip()
    if not spec:
        return '# TODO: [场景] 缺少背景名', TODO

    status = audit.image(spec)
    if status == TODO:
        return f"# TODO: 缺少资源 图片 '{spec}'", TODO

    line = f"scene {spec}"
    if at:
        line += f" at {at}"
    if op.with_:
        line += f" with {op.with_}"
    return line, status


def _compile_actor(op: StageOp, audit: Audit) -> tuple:
    """
    `[显示] 凌跃捷 开心 左` → `show ling close_happy at left`
    """
    symbols = audit.symbols
    target = op.target
    actor_row = symbols.actor(target)

    tag = ''
    if actor_row:
        tag = (actor_row.get('ImageTag') or actor_row.get('ID') or '').strip()
    elif symbols.has('character'):
        # 角色表里有东西，但这个词不在其中 → 真的找不到
        return (f"# TODO: 缺少资源 角色 '{target}'", TODO)
    else:
        # 没建角色表：把词本身当图像名（可能是 `bg room` 这种写法）
        tag = target

    attrs = []
    at = op.at
    for word in op.attrs:
        if word in symbols.positions and not at:
            at = symbols.position_for(word)
            continue
        attr = symbols.attr_for(tag, word)
        if attr:
            attrs.append(attr)

    spec = symbols.build_image_spec(tag, attrs)
    if not spec:
        return f"# TODO: 缺少资源 图片 '{target}'", TODO

    status = audit.image(spec)
    if status == TODO:
        return f"# TODO: 缺少资源 图片 '{spec}'", TODO

    line = f"{op.verb} {spec}"
    if at:
        line += f" at {at}"
    if op.with_:
        line += f" with {op.with_}"
    return line, status


def compile_audio(text: str, channel: str, symbols: Symbols,
                  audit: Audit = None) -> list:
    """
    音频单元格 → [(原始意图, 代码行, 状态)]。

    `[音乐] 春天花瓣 fadein 1.0` → `play music "bgm/春天花瓣剥落.mp3" fadein 1.0`
    """
    audit = audit or Audit(symbols)
    rows = []
    for op in parse_audio(text, channel):
        if op.action == 'unknown':
            rows.append((op.source, f"# TODO: 无法识别的音频指令 {op.source}",
                         TODO))
            continue
        if op.action == 'stop':
            # `[停止音乐]` / `[音乐] 停止`
            hook = 'voice' if channel == 'voice' else op.channel
            if hook == 'voice':
                rows.append((op.source, '# TODO: 语音不支持停止', TODO))
                continue
            line = f"stop {hook}"
            if op.params:
                line += f" {op.params}"
            rows.append((op.source, line, OK))
            continue

        kind = op.channel
        status = audit.audio(kind, op.name)
        if status == TODO:
            label = {'music': '音乐', 'sound': '音效',
                     'voice': '语音'}.get(kind, kind)
            rows.append((op.source,
                         f"# TODO: 缺少资源 {label} '{op.name}'", TODO))
            continue
        ref = audit.audio_ref(kind, op.name)
        ref = _quote_audio(ref)
        params = f" {op.params}".rstrip() if op.params else ''
        verb = 'queue' if op.action == 'queue' else 'play'
        rows.append((op.source, f"{verb} {kind} {ref}{params}", OK))
    return rows


def _quote_audio(name: str) -> str:
    """音频引用：简单标识符原样，含路径/中文一律加引号。"""
    name = (name or '').strip()
    if not name:
        return '""'
    if re.match(r'^[A-Za-z_][\w.]*$', name) and '/' not in name:
        return name
    escaped = name.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# 反向：中间表示 -> 人话（.rpy 导回表格时用）
# ---------------------------------------------------------------------------

def stage_word_if_lossless(symbols: Symbols, verb: str, spec: str,
                           at: str = '', with_: str = '') -> str:
    """
    把原生图像写法翻成人话，**但只在往返无损时才返回**。

    做法很土但很可靠：翻完立刻用编译器再翻回去，和原始写法逐字对比，
    不一致就返回 ''（调用方退回原生写法）。

    为什么必须自检：表情词是靠"语义核心词 + 模糊匹配"落地的
    （开心 → happy → `close_happy`）。逆向时 `happy` 可能对应项目里
    好几个属性，猜错一个表情就是演出事故。宁可显示程序写法。
    """
    try:
        text = render_stage_word(symbols, spec, at, with_, verb)
        if not text:
            return ''
        back = compile_stage(text, symbols, Audit(symbols))
        if len(back) != 1:
            return ''
        _intent, code, status = back[0]
        if status == TODO:
            return ''
        expected = f"{verb} {spec}"
        if at:
            expected += f" at {at}"
        if with_:
            expected += f" with {with_}"
        return text if code == expected else ''
    except Exception as e:
        swallow('story.dsl.stage_word_if_lossless', e)
        return ''


def render_stage_word(symbols: Symbols, spec: str, at: str = '',
                      with_: str = '', verb: str = 'show') -> str:
    """
    把"原生图像写法"尽量翻回人话（`ling close_happy` → `凌跃捷 开心`）。

    只在**能唯一反推**时才翻译，否则返回 '' 让调用方退回原生写法——
    宁可显示程序写法，也不能猜错一个表情。
    """
    parts = (spec or '').split()
    if not parts:
        return ''
    label = {'show': '显示', 'hide': '隐藏'}.get(verb, '显示')
    tag = parts[0]
    attrs = parts[1:]
    actor = ''
    for row in symbols._by_var.values():
        if (row.get('ImageTag') or row.get('ID')) == tag:
            actor = _clean_name(row.get('Name') or '') or row.get('ID') or ''
            break
    if not actor:
        return ''
    words = [actor]
    for attr in attrs:
        # 反推不出中文的属性**原样带上**（顺序也不动）——宁可显示
        # `base_handdown` 这种程序写法，也不能把它丢掉或换个位置。
        words.append(symbols.expression_word_for(tag, attr) or attr)
    if at:
        pos = symbols.position_word_for(at)
        if pos:
            words.append(pos)
        else:
            # 项目自定义变换（`f11` / `bgfit`）没有中文词，用 `at 名字`
            # 的写法留在人话句法里，比整句退回原生写法更易读
            words.append(f'at {at}')
    text = f'[{label}] ' + ' '.join(words)
    if with_:
        text += f' with {with_}'
    return text


def render_audio_word(symbols: Symbols, kind: str, name: str,
                      params: str = '', action: str = 'play') -> str:
    """音频 → 人话（用资产 ID，而不是编译后要用的文件路径）。"""
    label = {'music': '音乐', 'sound': '音效', 'voice': '语音'}.get(kind, '音效')
    if action == 'stop':
        return f'[停止{label}]' if kind != 'voice' else '[/语音]'
    text = f'[{label}] {name}'.rstrip()
    if params:
        text += f' {params}'
    return text
