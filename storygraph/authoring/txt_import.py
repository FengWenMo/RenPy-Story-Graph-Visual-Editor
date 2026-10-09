"""
纯文字（`.txt`）→ Ren'Py 脚本 —— **作者层入口**（S1）
=====================================================

用户 2026-10-09 的需求：

> 我写了个非常粗糙的 txt 转 renpy 的 .py …… 我想把它接入此工具，成为一个
> 导入接口（后续还希望导入 .docx），这样就再也不用先转成 Ren'Py 脚本了，
> 纯文字即可直接转换，转换后顺带导入进去。

**本模块只做"文本 → `.rpy` 文本"这一件事**，而且是**纯函数 + 确定性**的：

  · 不读配置、不建目录、不写盘（那些副作用是原脚本的毛病，接入时不能带进来）；
  · 产物里**没有时间戳 / 绝对路径 / 随机** —— 否则全仓"逐字节比对"的判据全失效
    （幂等审计、各处金标都靠它）；
  · 落盘由调用方走 `generation_guard.write_generation`（唯一的写盘入口）。

## 口径（用户 2026-10-10 拍板）

| 问题 | 结论 |
|---|---|
| 括号 `（bg1.1）` / `（背景纯黑）` | **v1 一律当注释**，放在该行**上方**（后期再考虑原样带进 Stage） |
| 角色名 `JC` / `YHJ` / `LYJ` | **读角色表**（大小写不敏感，`JC`→`jc`）；**未映射要报出来**，并给出建议 define |
| `define` 写哪 | **不写进章节产物**（多章会重复定义）；能定位到项目集中处再补 —— 本模块只**产建议**，不落盘 |
| `.docx` | 标准库 `zipfile` + `xml.etree` 自己抽段落（见 `docx_import`），**不新增依赖** |

## 识别规则（这是与"粗糙原版"最大的差别）

原版是"引号前的内容就是角色名" —— 中文旁白里一有引号就误判（真实 5 章 1372 行里
就有一批：`世界上再也没有什么比"…"` 会被当成角色名）。本模块的判据是：

  1. token **能在角色表里查到**（忽略大小写、下划线、空格）→ 认，并**映射成表里的变量**；
  2. token 形如**全大写拉丁缩写**（`^[A-Z][A-Z0-9_]{0,11}$`，真实剧本里就是 `JC`/`YHJ`/`MOM`）
     → 认，但记进"未映射"（产物里保留原 token，可读、可人工改）；
  3. 其余（例如 `世界上再也没有什么比`）→ **一律当旁白**，绝不猜。

第 3 条是关键：宁可把一句带引号的旁白当旁白，也不要把旁白当角色 —— 后者是
**静默改剧情**（说话人错了，游戏里显示的名字就错了）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.vocabulary import GENERATOR_MARKER

#: 中文/英文括号（含全角圆括号、方括号的常见写法）
_BRACKET_RES = (
    re.compile(r'（[^（）]*）'),      # （…）
    re.compile(r'\([^()]*\)'),        # (…)
)
#: 说话人可能用的引号：中文引号 / 英文双引号
_QUOTE_RES = (re.compile(r'[“”]'), re.compile(r'"'))
#: 冒号（全角/半角）
_COLON_RE = re.compile(r'[：:]')
#: "全大写拉丁缩写"——真实剧本里的角色标识就是这个形状
_UPPER_TOKEN_RE = re.compile(r'^[A-Z][A-Z0-9_]{0,11}$')
#: 章节头（`1.0 chapter0-0 初入大学` / `chapter0_0 …`）
_HEADER_RE = re.compile(r'^\s*\d+(?:\.\d+)?\s*([A-Za-z][\w\-]*\d[\w\-]*)\b')
#: 章节头里那个"章节号"（`chapter0-0`）
_CHAPTER_ID_RE = re.compile(r'(chapter[\-_]?\d+[\-_]\d+)', re.I)


def read_text_file(path) -> str:
    """
    读纯文本：`utf-8-sig` → `utf-8` → `gbk` 依次试（原脚本就是这个顺序）。

    ⚠ 不静默吞异常：三种都失败就抛 `UnicodeDecodeError`，让调用方报给用户
    （"读不出来"要能看见，而不是得到一份空文件）。
    """
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError('utf-8', raw, 0, 1,
                             f'既不是 UTF-8 也不是 GBK：{path}')


def split_lines(text: str) -> list:
    """按行切（**去掉 CRLF 的 `\\r`**，`\\r\\n` 与 `\\n` 都算一行）。"""
    return text.replace('\r\n', '\n').replace('\r', '\n').split('\n')


# ===================== 角色表 =====================

def _norm_key(name: str) -> str:
    """
    归一化键：小写 + 去掉下划线/空格/**角括号与引号**。

    于是 `JC`/`jc`/`J_C` 是同一个；**显示名**带书名号/角括号也能配上
    （作者写 `「叶怀瑾」"…"` 时，token 里就带着角括号）。
    """
    s = re.sub(r'[\s_]+', '', str(name or ''))
    s = re.sub(r'[「」『』"\'“”]', '', s)
    return s.lower()


class CharacterMap:
    r"""
    角色标识 → 角色变量 的映射（**唯一真源来自角色表**）。

    `rows` 是 `AssetRegistry.characters` 那种形状：
    `{变量: {'ID','Name','Aliases','ImageTag',…}}`。

    ## 四层匹配（能自动的绝不让人配；**猜不准的一律不猜**）

    | 层 | 判据 | 例 |
    |---|---|---|
    | L0 精确 | token == 角色变量 | `mm` |
    | L1 归一 | 大小写/下划线/空格/角括号/引号归一 | `Mm`/`MM`/`m_m`/`「叶怀瑾」` |
    | L2 显示名 | token == `Name` 列 | `妈妈`（变量是 `mm`） |
    | L3 别名 | token ∈ `Aliases` 列（**多值**：`MOM\|母亲\|老妈`） | `MOM` |
    | L4 | 都不中 → **报出来**（`lookup` 返回空） | `LINGDAO` |

    ⚠ 为什么不猜：`LINGDAO ↔ ld` 之间**没有算法关系**（`ld` 既不是拼音也不是可推的缩写），
    猜错就是**改剧情**（游戏里说话人换了人）。
    """

    #: 层优先级（越小越强）：变量 > 显示名 > 别名
    LAYER_RANK = {'variable': 0, 'display': 1, 'alias': 2}

    def __init__(self, rows=None):
        self.by_key = {}
        self.layer_of = {}
        self.display = {}
        #: 撞车清单：`(归一后的键, 已占用的角色, 后来的角色)` —— **不许静默取第一个**
        self.conflicts = []
        for var, row in (rows or {}).items():
            row = row or {}
            var = str(row.get('ID') or var or '').strip()
            if not var:
                continue
            # L0/L1：变量名本身（`JC`/`jc`/`J_C` 都归一到同一个键）
            self._add(_norm_key(var), var, 'variable')
            name = str(row.get('Name') or '').strip()
            if name:
                self.display[var] = name
                self._add(_norm_key(name), var, 'display')      # L2
            for alias in split_aliases(row.get('Aliases')):
                self._add(_norm_key(alias), var, 'alias')        # L3

    def _add(self, key: str, var: str, layer: str) -> None:
        if not key:
            return
        cur = self.by_key.get(key)
        if cur is None:
            self.by_key[key] = var
            self.layer_of[key] = layer
            return
        if cur != var:
            # 撞车：这个写法指到了两个角色 —— **报出来**（绝不静默取第一个），
            # 但映射本身仍按"**强层赢**"定下来（显示名 > 别名），
            # 否则用户看到的是"正名输给了别人的别名"，比报错更费解。
            self.conflicts.append((key, cur, var))
            if self.LAYER_RANK[layer] < self.LAYER_RANK.get(
                    self.layer_of[key], 'alias'):
                self.by_key[key] = var
                self.layer_of[key] = layer
        elif self.LAYER_RANK[layer] < self.LAYER_RANK.get(self.layer_of[key],
                                                          'alias'):
            self.layer_of[key] = layer      # 同一个角色：保留更强的来源

    def lookup(self, token: str):
        """返回 `(变量, 命中层)`；没命中返回 `(None, '')`。"""
        key = _norm_key(token)
        if not key:
            return None, ''
        var = self.by_key.get(key)
        return (var, self.layer_of.get(key, '')) if var else (None, '')

    def conflict_warnings(self) -> list:
        """撞车 → 给人看的告警（同一个写法指到了两个角色）。"""
        return [f'角色别名冲突："{key}" 同时指向 {a} 与 {b}'
                f'（请到 characters.xlsx 的「别名」列里改掉一个）'
                for key, a, b in self.conflicts]

    def candidates(self, token: str) -> list:
        """
        未映射时给**候选**（让人挑，而不是我们猜）。

        只按"前缀/子串"粗略提示，命中就列出来；列不出就返回空 —— 空也不算错，
        S3 的界面会把整张角色表摊给用户挑。
        """
        key = _norm_key(token)
        if not key:
            return []
        out = []
        for k, var in self.by_key.items():
            if k != key and (k.startswith(key) or key.startswith(k)):
                if var not in out:
                    out.append(var)
        return sorted(out)[:8]

    def suggest_define(self, token: str) -> str:
        """
        未映射时的**建议 define**（本模块不落盘，只给调用方/用户看）。

        ⚠ 与"粗糙原版"的区别：原版会把它**直接写进产物**（`define a = ...`），
        于是多章合并会重复定义、而且显示名是编的（Alice/Bob）。这里只是建议，
        且**显示名就写 token 本身**，不编人名。
        """
        var = re.sub(r'[^A-Za-z0-9_]', '', str(token or '')) or 'unknown'
        if re.match(r'^\d', var):
            var = 'c' + var
        return f'define {var} = Character("{token}")'


#: 别名列的分隔符：`|` `,` `，` `、` `;` `；` 与空白都算（一个人可以有**多个**别名）
_ALIAS_SPLIT_RE = re.compile(r'[|,，、;；\s]+')


def split_aliases(value) -> list:
    """
    别名列 → 别名列表（**多值**）。

    用户 2026-10-10："一个人可以有多个别名" —— `妈妈` / `Mom` / `MM` 都是同一个人，
    所以这一列天然是列表，不是单值。空值/None 返回 `[]`。
    """
    if value is None:
        return []
    return [x.strip() for x in _ALIAS_SPLIT_RE.split(str(value)) if x.strip()]


# ===================== 行型判定 =====================

#: 一行被判成什么
NARRATION = 'narration'
SAY = 'say'
HEADER = 'header'


@dataclass
class TextLine:
    """原文一行 → 判定结果（保留行号，便于报"第 N 行怎么样"）。"""
    no: int
    raw: str
    kind: str
    comments: list = field(default_factory=list)   # 括号内容（按出现顺序）
    speaker_token: str = ''
    speaker_var: str = ''
    text: str = ''


def extract_brackets(line: str):
    """
    抽出括号内容当注释；返回 `(去掉括号后的正文, [注释…])`。

    ⚠ 与原版不同：**从后往前删**（原版按"偏移量"递增删，遇到交错/重叠括号会错位），
    并且**不把括号内容再拆词** —— 一个字不改地当注释。
    """
    found = []
    for rx in _BRACKET_RES:
        for m in rx.finditer(line):
            found.append((m.start(), m.end(), m.group(0)[1:-1].strip()))
    if not found:
        return line, []
    # 重叠只留外层第一个（避免同一段被删两次）
    found.sort()
    kept = []
    last_end = -1
    for s, e, content in found:
        if s < last_end:
            continue
        kept.append((s, e, content))
        last_end = e
    out = line
    for s, e, _c in reversed(kept):
        out = out[:s] + out[e:]
    comments = [c for _s, _e, c in kept if c]
    return out, comments


def _detect_speaker(body: str, chars: CharacterMap):
    """
    判定"这一行是不是角色台词"，返回 `(token, var, 台词, 命中方式)`。

    三条判据见模块头（表命中 / 全大写缩写 / 其余当旁白）。
    """
    # ① 冒号形式：`JC：台词` / `JC: 台词`（**只认第一处冒号**）
    m = _COLON_RE.search(body)
    if m:
        token = body[:m.start()].strip()
        rest = body[m.end():].strip()
        if token and rest and _looks_like_speaker(token, chars):
            var, how = chars.lookup(token)
            return token, var, rest.strip('“”"'), how

    # ② 引号形式：`JC"…"` / `A“…”`（引号紧跟在 token 之后）
    for rx in (re.compile(r'^\s*([^\s“”":：]{1,16})\s*[“"](.*?)[”"]\s*$'),
               re.compile(r'^\s*([^\s“”":：]{1,16})\s*[“"](.*)$')):
        m = rx.match(body)
        if m:
            token = m.group(1).strip()
            text = m.group(2).strip().strip('“”"')
            if token and text and _looks_like_speaker(token, chars):
                var, how = chars.lookup(token)
                return token, var, text, how
    return '', '', '', ''


def _looks_like_speaker(token: str, chars: CharacterMap) -> bool:
    """说话人形状判定（见模块头第 3 条：宁可当旁白，也不要把旁白当角色）。"""
    if not token or len(token) > 16:
        return False
    if any(ch in token for ch in '，。！？、；…—-—'):     # 带标点的一定不是
        return False
    if chars.lookup(token)[0]:
        return True                                        # 表里有 → 认
    if _UPPER_TOKEN_RE.match(token):
        return True                                        # 全大写缩写 → 认（未映射会报）
    return False


def classify_line(line: str, no: int, chars: CharacterMap,
                  first_nonblank: bool = False) -> TextLine:
    """
    一行 → `TextLine`（**纯函数**，不看上下文）。

    `first_nonblank`：这一行是不是**第一个非空行** —— 只有它才可能被认成章节头
    （真实剧本第一行就是 `1.0 chapter0-0 初入大学`）。⚠ 早先我用"行号 ≤ 3"，
    于是正文第 2 行里长得像章节头的句子也会被吃掉（地狱测试当场抓到）。
    """
    raw = line
    body, comments = extract_brackets(line)
    body_stripped = body.strip()
    if not body_stripped:
        # 整行只是括号（`（bg1.1）`）—— 只出注释，**不出空台词**
        # （空台词会触发校验 W113，而且那不是作者要的）
        return TextLine(no=no, raw=raw, kind=NARRATION, comments=comments,
                        text='')
    if first_nonblank and _HEADER_RE.match(body_stripped):
        return TextLine(no=no, raw=raw, kind=HEADER, comments=comments,
                        text=body_stripped)
    token, var, text, _how = _detect_speaker(body_stripped, chars)
    if token and text:
        return TextLine(no=no, raw=raw, kind=SAY, comments=comments,
                        speaker_token=token, speaker_var=var, text=text)
    return TextLine(no=no, raw=raw, kind=NARRATION, comments=comments,
                    text=body_stripped)


# ===================== 转换 =====================

def label_from(text: str, filename: str = '') -> str:
    """
    推 label 名（三级回退，全部**确定性**）：

      1. **章节头里的 `chapter0-0`**（真实剧本第一行就是 `1.0 chapter0-0 初入大学`）
         → `chapter0_0`；
      2. **文件名的 `1-2` 前缀**（`1-2蓝之初见-定稿.txt`）→ `chapter1_2`
         —— 与真机项目里 `chapter1_1` / `chapter1_2` 的命名习惯一致；
      3. 兜底：文件名规范化（非字母数字 → `_`，数字开头补 `ch_`）。
    """
    m = _CHAPTER_ID_RE.search(text or '')
    if m:
        return re.sub(r'[^A-Za-z0-9_]', '_', m.group(1)).lower()
    stem = Path(filename).stem if filename else ''
    m2 = re.match(r'^(\d+)[\-_](\d+)', stem or '')
    if m2:
        return f'chapter{m2.group(1)}_{m2.group(2)}'
    name = re.sub(r'[^A-Za-z0-9_\u4e00-\u9fff]', '_', stem or '').strip('_')
    name = name or 'chapter'
    if re.match(r'^\d', name):
        name = 'ch_' + name
    return name


@dataclass
class ConversionResult:
    """转换结果（**可直接落盘**；也能拿去给 UI 显示告警）。"""
    rpy_text: str
    label: str
    lines: list = field(default_factory=list)          # [TextLine]
    counts: dict = field(default_factory=dict)         # 行型统计
    unmapped: dict = field(default_factory=dict)       # token → 出现次数
    suggested_defines: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    dropped: list = field(default_factory=list)        # 被丢掉的原文行（应当为空）
    conflicts: list = field(default_factory=list)      # 别名撞车（不许静默取第一个）

    @property
    def ok(self) -> bool:
        """有"丢内容/认不出"就不算干净通过（调用方据此决定要不要拦）。"""
        return not self.warnings


def convert_text(text: str, *, label: str = '', filename: str = '',
                 characters=None, indent: str = '    ',
                 mark_generated: bool = True) -> ConversionResult:
    """
    纯文字 → `.rpy` 文本。**同输入必同输出**（没有时间戳/路径/随机）。

    `characters` 给 `CharacterMap` 或角色表 dict（`{变量: 行}`）；
    不给就只有"全大写缩写"那条判据（未映射会报出来）。
    """
    chars = (characters if isinstance(characters, CharacterMap)
             else CharacterMap(characters))
    lines = split_lines(text)
    label = label or label_from(text, filename)

    out = []
    counts = {NARRATION: 0, SAY: 0, HEADER: 0, 'blank': 0, 'comment': 0}
    unmapped = {}
    parsed = []
    dropped = []

    for no, raw in enumerate(lines, 1):
        if not raw.strip():
            counts['blank'] += 1
            continue
        tl = classify_line(raw, no, chars, first_nonblank=(not parsed))
        parsed.append(tl)
        counts[tl.kind] = counts.get(tl.kind, 0) + 1
        if tl.kind == HEADER:
            continue                      # 章节头不进产物（它只用来推 label）
        for c in tl.comments:
            out.append(f'{indent}# {c}')
            counts['comment'] += 1
        if not tl.text:
            if not tl.comments:
                dropped.append((no, raw))    # 既没正文也没注释 → 内容丢了，必须报
            continue
        if tl.kind == SAY:
            speaker = tl.speaker_var or tl.speaker_token
            if not tl.speaker_var:
                unmapped[tl.speaker_token] = unmapped.get(tl.speaker_token, 0) + 1
            out.append(f'{indent}{speaker} "{_escape(tl.text)}"')
        else:
            out.append(f'{indent}"{_escape(tl.text)}"')
    if not out:
        # **空体要补 `pass`** —— 与 `render_beats_to_rpy` 的空 label 形状一致
        # （空文件 / 只有章节头时；`label X:` 里直接跟 `return` 也能跑，但我们就
        # 不与自己的渲染器逐字节一致了，地狱测试当场抓到这条）。
        out.append(f'{indent}pass')
    out.append(f'{indent}return')

    warnings = []
    for no, raw in dropped:
        warnings.append(f'第 {no} 行没产出任何内容（原文：{raw.strip()[:40]}）')
    for token, n in sorted(unmapped.items()):
        hint = chars.candidates(token)
        extra = f'；相近的角色：{"、".join(hint)}' if hint else ''
        warnings.append(
            f'角色 "{token}" 不在角色表里（{n} 处）—— 在 characters.xlsx 的'
            f'「别名」列给它补一个别名（可写多个，用 | 分隔），'
            f'或把 txt 里改成表里的变量名/显示名{extra}')
    # 别名撞车：同一个写法指到两个角色 —— **报出来**，绝不静默取第一个
    warnings.extend(chars.conflict_warnings())

    body = '\n'.join(out)
    head = f'label {label}:\n' if label else ''
    text_out = f'{head}{body}\n'
    if mark_generated:
        # ⚠ 形状必须与 `render_beats_to_rpy` **逐字节一致**（实测对齐一次就够了）：
        #
        #     <生成标记>
        #     （空行）
        #     label X:
        #         <正文（label 与正文之间**没有**空行）>
        #         return
        #
        # 对齐之后，本模块的产物与我们自己的渲染器**完全相同**，于是增量补丁、
        # 幂等审计、各处金标全都能直接作用在它上面（这比"看起来差不多"值钱得多）。
        text_out = f'{GENERATOR_MARKER}\n\n{text_out}'

    return ConversionResult(
        rpy_text=text_out, label=label, lines=parsed, counts=counts,
        unmapped=unmapped,
        suggested_defines=[chars.suggest_define(t) for t in sorted(unmapped)],
        warnings=warnings, dropped=dropped, conflicts=list(chars.conflicts))


def _escape(text: str) -> str:
    """
    台词正文 → Ren'Py 双引号字符串里的安全写法。

    ⚠ 只转义必要的：`"` → `\\"`、反斜杠 → `\\\\`、换行 → `\\n`。
    **逗号/方括号/大括号不动**（那是 Ren'Py 的文本标签与插值语法，
    作者写了就是要它生效）。
    """
    return (str(text).replace('\\', '\\\\')
            .replace('"', '\\"')
            .replace('\n', '\\n'))


def convert_file(path, *, label: str = '', characters=None,
                 indent: str = '    ', mark_generated: bool = True):
    """读 `.txt` → 转换（`path` 只用来读与推 label，**不进产物**）。"""
    p = Path(path)
    text = read_text_file(p)
    return convert_text(text, label=label or '', filename=p.name,
                        characters=characters, indent=indent,
                        mark_generated=mark_generated)
