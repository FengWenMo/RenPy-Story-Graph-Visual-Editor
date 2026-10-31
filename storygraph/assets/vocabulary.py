# -*- coding: utf-8 -*-
"""
storygraph.assets.vocabulary
============================
**词汇表**：引擎里那串写法 ⇄ 人嘴里那个说法。**一处登记，处处认**。

用户 2026-10-31（原文）::

    为了此项目的通用性！架构必须做得更为扎实！脱离真实项目再考虑以下几种情况：
    1. 我要将 transform 的 at i11 一类的映射为中文（比如：1人，近处）。
    2. 我要将带有中文名的 BGM 映射为纯数字编号。
    3. 我要将背景的文件映射为 bg + 数字。
    4. 我要将一个角色的名称，比如 DY，映射为大爷。
    5. 我要自定义一个过场动画的调用代码，并把它用中文成功映射，然后在卡片编辑器
       中能看到可视化 UI 直接选中，在搜索中也要能够搜到！
    6. 我要一个角色自定义服装，然后将这个服装映射为 A/B/C 等形式，同时不影响
       角色定义和其它任何东西，可在卡片编辑器内显示并单独调整。

## 为什么这是一层、不是六个功能

上面六条**是同一件事**：屏幕上要出现的那串字（`i11` / `bgm/嗯？.mp3` / `bg9_2` /
`DY` / `my_wipe` / `base_swimsuit`）和一个**人话**（`1人，近处` / `18` / `bg9.2` /
`大爷` / `闪白` / `B 泳装`）之间的对照。差别只在"这串字出现在哪"：

| `kind` | 引擎里的写法 | 出现在哪 | 带角色作用域 |
|---|---|---|---|
| `transform` | `at i11` 里的 `i11` | 立绘卡的「位置」chip、候选列表 | 否 |
| `music` | `bgm/嗯？.mp3` | 音乐那格、选择器 | 否 |
| `sound` | `se/hit.ogg` | 音效那格 | 否 |
| `voice` | `voice/a.ogg` | 语音那格 | 否 |
| `bg` | `bg9_2` | 背景那格 | 否 |
| `character` | `DY` | 说话人、卡片标题、对白 | 否 |
| `cue` | `my_wipe` / `$ renpy.transition(...)` | 「技术调用」、卡片参数格 | 否 |
| `costume` | `base_swimsuit` | 立绘卡的服装 chip | **是** |
| `expression` | `indifferent` | 立绘卡的表情 chip | **是** |

所以这里只有一张表，一条 = `{kind, id, label, code, char, note}`：

    {"kind": "transform", "id": "i11", "label": "1人，近处"}
    {"kind": "music", "id": "bgm/嗯？.mp3", "label": "嗯？", "code": "18"}
    {"kind": "bg", "id": "bg9_2", "label": "储物间", "code": "bg9.2"}
    {"kind": "character", "id": "DY", "label": "大爷"}
    {"kind": "cue", "id": "my_wipe", "label": "闪白转场"}
    {"kind": "costume", "id": "base_swimsuit", "label": "泳装", "code": "B", "char": "lan"}

## 三条硬规矩

1. **`id` 永远是真值**：写进剧本、写进 JSON 的都是它；`label` / `code` 只影响
   屏幕（与 `mapping_editor` 的"显示值 / 真值"同一条规矩）。
2. **查不到就原样显示 `id`**：词汇表是**补充**，不是白名单。项目里 151 个变换
   只登记 3 个中文名，另外 148 个不能因此变成空白。
3. **`code` 与 `label` 各管一边**：`code` 是美术/编号那套（`18`、`B`、`bg9.2`），
   `label` 是称呼（`嗯？`、`泳装`、`储物间`）；两个都有时屏幕上是 `18 嗯？`。

## 与 `sprite_codes` 的关系

服装/表情那两段（`outfit_codes` / `expression_codes`）**本来就是服装/表情的
词汇**，而且多了"换算代号"的语义。所以 `Vocabulary.from_mapping()` 会把它们
**并进来**当 `costume` / `expression` 条目 —— 界面上只认这一张词汇表，
数据上仍是那两段（不搬家、不重复登记）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from storygraph.core.authority import Override, rank

# ---------------------------------------------------------------------------
#  种类
# ---------------------------------------------------------------------------

KIND_TRANSFORM = 'transform'
KIND_MUSIC = 'music'
KIND_SOUND = 'sound'
KIND_VOICE = 'voice'
KIND_BG = 'bg'
KIND_CHARACTER = 'character'
KIND_CUE = 'cue'
KIND_COSTUME = 'costume'
KIND_EXPRESSION = 'expression'

#: 一张表里的**全部种类**（顺序 = 下拉里的顺序，按"最常用"排）。
KINDS = (KIND_TRANSFORM, KIND_MUSIC, KIND_SOUND, KIND_VOICE, KIND_BG,
         KIND_CHARACTER, KIND_CUE, KIND_COSTUME, KIND_EXPRESSION)


@dataclass(frozen=True)
class Kind:
    """一种"要起中文名"的东西。"""

    key: str
    label: str          # 屏幕上的名字
    hint: str           # 「这一列填什么」
    #: 是否**按角色分**（服装/表情同一个词在不同角色身上不是一回事）。
    scoped: bool = False
    #: `code` 那一列在屏幕上叫什么（背景/音乐叫"编号"，服装叫"代号"）。
    code_label: str = '编号'
    tech: str = ''      # 给开发者的"技术写法"


KIND_SPECS = {
    KIND_TRANSFORM: Kind(
        KIND_TRANSFORM, '位置 / 镜头', scoped=False, code_label='代号',
        hint='剧本里 `at` 后面那个名字（例如 i11）→ 你想看到的说法'
             '（例如「1人，近处」）；代号可以留空',
        tech='transform（`at <name>` 的 name）'),
    KIND_MUSIC: Kind(
        KIND_MUSIC, '音乐', code_label='编号',
        hint='一首 BGM → 编号或中文名。编号就是美术/音乐表里那一列'
             '（例如 18）；屏幕上会显示「18 嗯？」',
        tech='music（`play music "<路径>"` 的路径，或资源表的 ID）'),
    KIND_SOUND: Kind(
        KIND_SOUND, '音效', code_label='编号',
        hint='一个音效 → 编号（例如 se9）',
        tech='sound（`play sound "<路径>"`）'),
    KIND_VOICE: Kind(
        KIND_VOICE, '语音', code_label='编号',
        hint='一句语音 → 编号',
        tech='voice（`voice "<路径>"`）'),
    KIND_BG: Kind(
        KIND_BG, '背景', code_label='编号',
        hint='一张背景在剧本里的名字（例如 bg9_2）→ 编号或说法'
             '（例如 bg9.2 / 储物间）',
        tech='bg（`scene bg <attr>` 的 attr）'),
    KIND_CHARACTER: Kind(
        KIND_CHARACTER, '角色', code_label='简称',
        hint='剧本里的角色写法（例如 DY）→ 人话（例如 大爷）。'
             '和「角色」那段是同一件事：那边填了「名字」就以那边为准',
        tech='character（`define <tag> = Character(...)` 的 tag）'),
    KIND_CUE: Kind(
        KIND_CUE, '过场 / 调用', code_label='代号',
        hint='项目自己写的过场函数名（例如 my_wipe）→ 中文名'
             '（例如 闪白转场）。填了之后卡片上就能按中文挑',
        tech='cue（`storygraph.rules.json` 里 calls 的 head，或 `$ f(...)`）'),
    KIND_COSTUME: Kind(
        KIND_COSTUME, '服装', scoped=True, code_label='代号',
        hint='一个角色的服装（例如 base_swimsuit）→ 代号/说法'
             '（例如 B / 泳装）；要给「角色」那一列写上是谁的',
        tech='costume（`layeredimage` 里 outfit 组的 attribute）'),
    KIND_EXPRESSION: Kind(
        KIND_EXPRESSION, '表情', scoped=True, code_label='编号',
        hint='一个角色的表情（例如 indifferent）→ 编号/说法（例如 7 / 不在意）',
        tech='expression（`layeredimage` 里 expressions 组的 attribute）'),
}

#: 一条词汇里能有的键。
KEYS = ('kind', 'id', 'label', 'code', 'char', 'note')


def kind_label(kind) -> str:
    """种类的屏幕名（不认识就把它自己显示出来，不吞）。"""
    spec = KIND_SPECS.get(str(kind or ''))
    return spec.label if spec else str(kind or '')


def is_kind(kind) -> bool:
    return str(kind or '') in KIND_SPECS


def kind_from_text(text) -> str:
    """
    `transform` / `位置` / `位置 / 镜头` / `bgm` → 规范的 `kind`；认不出 `''`。

    为什么要有：Excel 是人手写的，那一列一定有人写「音乐」「位置」——
    只认英文键名等于让人白填（`播放音乐` 这种说法也认）。
    """
    want = norm(text)
    if not want:
        return ''
    if want in KIND_SPECS:
        return want
    for key, spec in KIND_SPECS.items():
        if norm(spec.label) == want or want in norm(spec.label):
            return key
    aliases = {'bgm': KIND_MUSIC, 'music': KIND_MUSIC, 'se': KIND_SOUND,
               'sfx': KIND_SOUND, 'sound': KIND_SOUND,
               'at': KIND_TRANSFORM, 'transform': KIND_TRANSFORM,
               'position': KIND_TRANSFORM, 'background': KIND_BG,
               'bg': KIND_BG, 'char': KIND_CHARACTER,
               'character': KIND_CHARACTER, 'name': KIND_CHARACTER,
               'call': KIND_CUE, 'cue': KIND_CUE, 'transition': KIND_CUE,
               'outfit': KIND_COSTUME, 'costume': KIND_COSTUME,
               'clothes': KIND_COSTUME, 'expression': KIND_EXPRESSION,
               'face': KIND_EXPRESSION, 'voice': KIND_VOICE}
    return aliases.get(want, '')


# ---------------------------------------------------------------------------
#  归一化 / 比对
# ---------------------------------------------------------------------------

def norm(text) -> str:
    """
    比对用的归一写法：小写、去掉一切非字母数字（中文原样），`base_` 前缀去掉。

    `base_swimsuit` / `swim_suit` / `Swimsuit` → `swimsuit`；
    `bgm/嗯？.mp3` → `bgm嗯mp3`。**只用于比对，绝不写回文件**。
    """
    got = str(text if text is not None else '').strip().lower()
    got = got.replace('\\', '/')
    # ⚠ `位置 / 镜头` 这种**名字里就有斜杠**的不许当路径切（切了就只剩「镜头」，
    #   于是「位置」永远认不出是哪一类）。只有"两边没空格的斜杠"才算路径。
    if '/' in got and not re.search(r'\s/\s', got):
        got = got.rsplit('/', 1)[-1]          # 路径只比文件名（`bgm/嗯？.mp3`）
    got = re.sub(r'\.[a-z0-9]{2,5}$', '', got)   # 扩展名不比
    got = got.removeprefix('base_').removeprefix('base')
    return re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', got)


# ---------------------------------------------------------------------------
#  层：谁盖谁**只查这张表**（P0-2，2026-10-31）
# ---------------------------------------------------------------------------
#
# 从前这里是 `plus()` 的**调用顺序**："后来者优先"。写反了（先 mapping 再
# calls）"映射优先"就静默失效，没有任何地方会红 —— 这是契约里记的最高频
# 实操坑之一。现在层名 → `core.authority.AUTHORITY` 的层号，合并不看顺序。

#: 词汇表的层名 → `AUTHORITY` 的层（数字小的赢）。
VOCAB_LAYERS = {
    # storygraph.mapping.json / xlsx：人写的改名，**命名这一域的正本**
    'mapping': 'mapping-exact',
    # 同一份映射里的服装/表情代号段（`outfit_codes` / `expression_codes`）
    'codes': 'mapping-exact',
    # 同一份映射里的 `characters` 段（角色显示名）
    'characters': 'mapping-exact',
    # rules.json 的 `calls[].label`：项目自己声明的技术调用名
    'calls': 'declared',
    # 工具内置词条（引擎/平台词汇）
    'builtin': 'engine',
    # 从注释里学出来的初稿（`learn()`）：纯推断，只补空缺
    'learned': 'inferred',
}

#: 同一层的**段内次序**（小的先、赢）：越"专"的段越权威。
#:
#: 只在调用方把一份映射拆成几段传进来时才有意义；正常路径是
#: `from_mapping()` 一次拼好（它自己按"后登记的赢"处理，见那里的说明）。
_SEGMENT_ORDER = {'characters': 0, 'codes': 1, 'mapping': 2}


def _with_source(term, layer: str):
    """给词条标上"我是哪一层给的"（frozen dataclass 用 `replace`）。"""
    return replace(term, source=str(layer or ''))


def _display(term) -> str:
    """这一条在屏幕上显示成什么（比较"值变没变"用它）。"""
    try:
        return term.display()
    except AttributeError:
        return str(getattr(term, 'id', '') or '')


def _term_key_text(term) -> str:
    """人话的键：`transform i11` / `costume base_swimsuit（lan）`。"""
    kind = str(getattr(term, 'kind', '') or '')
    ident = str(getattr(term, 'id', '') or '')
    char = str(getattr(term, 'char', '') or '')
    return f'{kind} {ident}' + (f'（{char}）' if char else '')


# ---------------------------------------------------------------------------
#  一条 / 一张表
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Term:
    """一条词汇：`id` 是引擎写法（真值），其余只影响屏幕。"""

    kind: str
    id: str
    label: str = ''
    code: str = ''
    char: str = ''
    note: str = ''
    #: 这条是**哪一层**给的（`VOCAB_LAYERS` 的键：`mapping` / `calls` / …）。
    #: 只用于"凭什么这么判"和覆盖留痕，**不参与相等判断之外的任何判定**。
    source: str = ''

    @property
    def key(self) -> tuple:
        """同一条的判据（种类 + 引擎写法 + 角色）。"""
        return (self.kind, self.id, self.char)

    def as_dict(self) -> dict:
        out = {'kind': self.kind, 'id': self.id}
        for name in ('label', 'code', 'char', 'note'):
            value = str(getattr(self, name) or '').strip()
            if value:
                out[name] = value
        return out

    def display(self) -> str:
        """
        屏幕上该显示什么：`18 嗯？` / `B 泳装` / `1人，近处` / `i11`。

        两个都有就 `code label` —— 编号是给人对表的，称呼是给人认的，
        缺哪个就显示哪个；一个都没有就原样 `id`（规矩 2）。
        """
        parts = [p for p in (self.code.strip(), self.label.strip()) if p]
        return ' '.join(parts) if parts else self.id


EMPTY = Term('', '')


def parse_term(entry) -> Term:
    """一条原始数据 → `Term`（认不出种类/写法就返回 `EMPTY`）。"""
    if not isinstance(entry, dict):
        return EMPTY
    kind = str(entry.get('kind') or '').strip()
    ident = str(entry.get('id') or entry.get('ident') or '').strip()
    if kind not in KIND_SPECS or not ident:
        return EMPTY
    return Term(kind=kind, id=ident,
                label=str(entry.get('label') or '').strip(),
                code=str(entry.get('code') or '').strip(),
                char=str(entry.get('char') or entry.get('tag') or '').strip(),
                note=str(entry.get('note') or '').strip())


def parse_terms(entries) -> list:
    """一列原始数据 → `[Term, …]`（坏条目跳过，不猜）。"""
    out = []
    seen = set()
    for entry in entries or ():
        term = parse_term(entry)
        if not term.id or term.key in seen:
            continue
        seen.add(term.key)
        out.append(term)
    return out


@dataclass
class Vocabulary:
    """
    一张**只读**的词汇表（`from_mapping()` 从项目那份映射里拼出来）。

    它不是"另一个数据源"：`vocabulary` 段 + `outfit_codes` / `expression_codes`
    两段的**投影**。所以这里只有查询，没有写回 —— 写入永远走映射那几张表。
    """

    terms: tuple = ()
    #: 覆盖记录（append-only；`core.authority.Override`）。
    #: 只在 `merged()` 里跨层合并时产生 —— "谁盖掉了谁的原话"。
    overrides: tuple = ()

    # -- 读 ------------------------------------------------------------

    def of_kind(self, kind: str) -> list:
        return [t for t in self.terms if t.kind == kind]

    def scoped(self, kind: str, char: str = '') -> list:
        """
        这一种类在屏幕上的候选。

        带作用域的（服装/表情）：给了角色就**角色先算**、没写角色的算通配跟后面；
        没给角色（不知道是谁）就全给 —— 宁可多一条，也别让"查不到"变成空白。
        不带作用域的（位置/音乐…）：`char` 无所谓，全给。
        """
        items = self.of_kind(kind)
        spec = KIND_SPECS.get(kind)
        if spec is None or not spec.scoped:
            return items
        want = str(char or '').strip()
        if not want:
            return items
        mine = [t for t in items if t.char and t.char == want]
        loose = [t for t in items if not t.char]
        return mine + loose

    def find(self, kind: str, text, char: str = '') -> Term:
        """
        `text`（引擎写法 / 代号 / 中文名 / 备注）→ 这一条；找不到 `EMPTY`。

        比对顺序：**原样 id** → 归一 id → 归一 code → 归一 label → 归一 note。
        为什么"原样"排第一：路径里大小写/标点都是有意义的，先给精确匹配的
        机会，再做宽容匹配（宽容匹配只在**唯一命中**时才算，免得把两个相似
        的东西认成一个 —— 见 `_unique`）。
        """
        want = str(text if text is not None else '').strip()
        if not want:
            return EMPTY
        pool = self.scoped(kind, char)
        for term in pool:
            if term.id == want:
                return term
        # **原样**比一遍（`嗯？` 与 `嗯？嗯？` 归一后都是「嗯」，先给精确的机会）
        for field_name in ('code', 'label', 'note'):
            exact = [t for t in pool
                     if str(getattr(t, field_name, '') or '').strip() == want]
            if len(exact) == 1:
                return exact[0]
        key = norm(want)
        if not key:
            return EMPTY
        for field_name in ('id', 'code', 'label', 'note'):
            hit = _unique(pool, field_name, key)
            if hit is not None:
                return hit
        return EMPTY

    def ident(self, kind: str, text, char: str = '') -> str:
        """人话 → 引擎写法（查不到就原样返回 —— 允许手写自由值）。"""
        want = str(text if text is not None else '').strip()
        if not want:
            return ''
        term = self.find(kind, want, char)
        return term.id if term.id else want

    def label(self, kind: str, ident, char: str = '') -> str:
        """
        引擎写法 → 屏幕上那句（查不到就原样 `ident`，规矩 2）。

        ⚠ **整串查不到时再试最后一个词**（2026-10-31）：背景那一格的值是
        *图像 spec*（`bg bg9_5`），而人登记的是 `bg9_5` —— 不试这一下，
        背景永远显示不出 `bg9.2`（用户第 3 条要的就是它）。只在这一侧
        兜底、**不在 `find()` 里做**：反查（`ident()`）必须严格，否则
        `ident('bg', 'bg bg9_5')` 会把开头的 tag 吃掉，写出 `bg9_5` 这种
        少一个词的语句。
        """
        want = str(ident if ident is not None else '').strip()
        if not want:
            return ''
        term = self.find(kind, want, char)
        if not term.id and len(want.split()) > 1:
            term = self.find(kind, want.split()[-1], char)
        return term.display() if term.id else want

    def label_in_text(self, kind: str, text, char: str = '') -> str:
        """
        **带壳的写法**也能翻：壳原样留着，只翻中间那个名字。

            `[音乐] bgm/嗯？.mp3 fadein 1.0`  → `[音乐] 18 嗯？ fadein 1.0`
            `bg9_5@bgfit`                      → `bg9.2 储物间@bgfit`

        为什么需要（2026-10-31 实测）：音乐那一格的值**常常是复合的** ——
        有 `fadein` / `fadeout` / `loop` 参数时，`compose_audio_text()` 写的是
        `[音乐] <名字> <参数>`。整串拿去查词汇表当然查不到，于是"有参数的
        那几首永远是英文"，看起来就像多接了一半。

        ⚠ **只用于显示**：写回永远用 `id`/原串（`ident()` 那条路不经过这里）。
        一轮里最多拆一层壳（`[x]` 或 `@`），**不猜**更复杂的结构。
        """
        want = str(text if text is not None else '').strip()
        if not want:
            return ''
        whole = self.label(kind, want, char)
        if whole != want:
            return whole
        m = re.match(r'^(\[[^\]]*\]\s*)(\S+)(.*)$', want, re.S)
        if m:
            inner = self.label(kind, m.group(2), char)
            return f'{m.group(1)}{inner}{m.group(3)}' if inner != m.group(2) \
                else want
        head, sep, tail = want.partition('@')
        if sep:
            inner = self.label(kind, head.strip(), char)
            return f'{inner}@{tail}' if inner != head.strip() else want
        return want


    def options(self, kind: str, char: str = '') -> list:
        """候选（每个都是 `Term`）：屏幕上按这个顺序摆。"""
        return list(self.scoped(kind, char))

    def search(self, text, kinds=None, char: str = '') -> list:
        """
        按人话搜（中文名 / 代号 / 引擎写法 / 备注都算）。

        `kinds` 给了就只在这些种类里搜。返回 `[Term, …]`，**引擎写法也算命中**
        —— 想搜 `i11` 的人不该被"只搜中文"挡住。
        """
        key = norm(text)
        if not key:
            return []
        pool = []
        for kind in (kinds or KINDS):
            pool.extend(self.scoped(kind, char))
        out = []
        for term in pool:
            hay = norm(' '.join((term.id, term.code, term.label, term.note)))
            if key in hay:
                out.append(term)
        # label / code 命中的排前面：搜「泳装」时"泳装 B"该比"xxx泳装yyy"先出现
        out.sort(key=lambda t: (0 if key in norm(t.label + t.code) else 1,
                                t.kind, t.id))
        return out

    # -- 拼 ------------------------------------------------------------

    @classmethod
    def merged(cls, layers) -> 'Vocabulary':
        """
        按 **`AUTHORITY` 的层号**合并多张词表（P0-2，2026-10-31）。

        参数 `layers` 是 `[(层名, 词条), …]`，层名必须是 `VOCAB_LAYERS` 的键：

            Vocabulary.merged([('calls', 调用词条), ('mapping', 映射词条)])

        **为什么不按传入顺序**：从前 `plus()` 是"后来者优先"，于是"映射优先"
        这件事只存在于调用点的书写顺序里 —— 谁把它写反了，结果就静默反过来，
        而且没有任何地方会红（契约里已记为"最高频的实操坑"之一）。现在层号
        写在 `core/authority.py` 的同一张表里，**顺序无关**是它的判据：
        打乱 `layers` 的先后，结果必须逐字段相同。

        同一层（同一个文件）里还分几段时，段的次序由 `_SEGMENT_ORDER` 固定
        （越"专"的段越权威）；**别把一份映射拆成几个同层名传进来** ——
        那是同一层的内部次序，不是优先级。

        合并时每盖掉一个**值不同**的旧词条，就追加一条
        `core.authority.Override`（P0-3 的"覆盖留痕"）：赢家赢，但报告里
        要能说出"原来是什么、被谁盖的"。
        """
        prepared = []
        for name, terms in layers or ():
            layer = str(name or '').strip()
            if layer not in VOCAB_LAYERS:
                raise ValueError(
                    f'未知的词汇表层：{layer!r}（只认 {sorted(VOCAB_LAYERS)}）')
            prepared.append((rank(VOCAB_LAYERS[layer]),
                             _SEGMENT_ORDER.get(layer, 0), layer,
                             list(terms or ())))
        prepared.sort(key=lambda item: (item[0], item[1], item[2]))

        table: dict = {}
        overrides: list = []
        for _rank, _seg, layer, items in prepared:
            for term in items:
                if not getattr(term, 'id', ''):
                    continue
                current = table.get(term.key)
                if current is None:
                    table[term.key] = _with_source(term, layer)
                    continue
                if current.source == layer:
                    # 同层：后来者赢（与 `from_mapping` 里"后登记的盖前面"一致）
                    table[term.key] = _with_source(term, layer)
                    continue
                # 跨层：当前这条来自**更权威**的层（已按层号排过序），它赢。
                if _display(current) != _display(term):
                    overrides.append(Override(
                        domain='naming',
                        key=_term_key_text(term),
                        # 记**权威层名**（不是词汇表的层名）：一句话里要能
                        # 读成"映射的精确覆盖覆盖了项目声明"，而不是
                        # "mapping 覆盖了 calls"。
                        winner_layer=VOCAB_LAYERS.get(current.source,
                                                       current.source),
                        loser_layer=VOCAB_LAYERS.get(layer, layer),
                        winner_value=_display(current),
                        loser_value=_display(term),
                        match=term.key))
        return cls(tuple(table.values()), tuple(overrides))

    def plus(self, terms) -> 'Vocabulary':
        """
        并上一批（同一条以**后来的**为准：人改的盖过自动生成的）。

        ⚠ **兼容保留**：它按调用顺序决定胜负，写反了就静默反过来。
        新代码请用 `merged()`（层号写在 `core/authority.py`，顺序无关）。
        """
        merged = {}
        for term in list(self.terms) + [t for t in terms or () if t.id]:
            merged[term.key] = term
        return Vocabulary(tuple(merged.values()), self.overrides)

    def as_terms(self) -> list:
        return list(self.terms)

    # -- 覆盖留痕（P0-3） ------------------------------------------------

    def overrides_of(self, kind: str, ident, char: str = '') -> list:
        """这一条词（`kind` + 写法）被谁覆盖过 → `[Override, …]`（可能空）。"""
        term = self.find(kind, ident, char)
        if not term.id:
            return []
        return [o for o in self.overrides if o.match == term.key]

    def explain(self, kind: str, ident, char: str = '') -> str:
        """
        "凭什么这么判"的**一句话**（P0-3）。

        形如：`transform i11：映射的精确覆盖「1人，近处」覆盖了资源表「i11」`。
        没有覆盖记录 → 空串（那时"依据"由 `Identity.source` 那侧回答，
        两边都不许编）。
        """
        return '；'.join(o.sentence() for o in self.overrides_of(
            kind, ident, char))


def _unique(pool, field_name: str, key: str):
    """在某个字段上按归一值找**唯一**一条；0 条或 ≥2 条都返回 None。"""
    hits = {}
    for term in pool:
        value = getattr(term, field_name, '') or ''
        if value and norm(value) == key:
            hits[term.key] = term
    return next(iter(hits.values())) if len(hits) == 1 else None


# ---------------------------------------------------------------------------
#  从映射拼一张表（唯一入口）
# ---------------------------------------------------------------------------

def from_mapping(data) -> Vocabulary:
    """
    一份映射 → 词汇表（`vocabulary` 段 + 角色/服装/表情几段的投影）。

    ⚠ 那几段是**投影、不是副本**：`attr`/`code`/`label` 与这里的
    `id`/`code`/`label` 一一对应，改了那几段，这张表立刻跟着变
    （**不搬家、不重复登记**）。

    同一条（种类 + 写法 + 角色）出现两次时**后登记的赢**（与 `plus()` 同一条
    规矩）：`vocabulary` 段 ↔ 角色/服装/表情段的说法冲突时，以"那段"为准
    —— 因为那段本来就是这件事的家（`Kind.hint` 里写着）。

    四段的来历：

      · `vocabulary` —— 想给**任何**东西起的名字（位置/音乐/背景/过场…）；
      · `characters` —— `{tag: {display: '大爷'}}`：角色那段的"名字"；
      · `outfit_codes` / `expression_codes` —— 服装/表情的代号（带角色作用域）。
    """
    data = data if isinstance(data, dict) else {}

    def put(terms: dict, term: Term) -> None:
        if term.id:
            # 同一份文件里的几段：后登记的盖前面的，位置留在第一次。
            # 它们同属 `mapping` 这一层（见 `VOCAB_LAYERS`），互相之间的
            # 次序是**文件内部**的事，不是优先级。
            terms[term.key] = _with_source(term, 'mapping')

    terms: dict = {}
    for term in parse_terms(data.get('vocabulary')):
        put(terms, term)
    for row in data.get('outfit_codes') or ():
        if not isinstance(row, dict):
            continue
        put(terms, Term(KIND_COSTUME, str(row.get('attr') or '').strip(),
                        label=str(row.get('label') or '').strip(),
                        code=str(row.get('code') or '').strip(),
                        char=str(row.get('char') or '').strip()))
    for row in data.get('expression_codes') or ():
        if not isinstance(row, dict):
            continue
        put(terms, Term(KIND_EXPRESSION, str(row.get('attr') or '').strip(),
                        label=str(row.get('label') or '').strip(),
                        code=str(row.get('no') or '').strip(),
                        char=str(row.get('char') or '').strip()))
    chars = data.get('characters')
    if isinstance(chars, dict):
        for tag, entry in chars.items():
            label = entry.get('display') if isinstance(entry, dict) else entry
            put(terms, Term(KIND_CHARACTER, str(tag or '').strip(),
                            label=str(label or '').strip()))
    return Vocabulary(tuple(terms.values()))


EMPTY_VOCABULARY = Vocabulary(())


# ---------------------------------------------------------------------------
#  技术调用（`rules.json` 的 `calls`）→ cue 词条
# ---------------------------------------------------------------------------

def cue_terms(calls) -> list:
    """
    项目声明的**技术调用** → `cue` 词条（只认填了中文名的那几条）。

    为什么 cue 有第二个登记处：`$ my_wipe(...)` 这种包装函数**本来就在**
    `rules.json` 的 `calls` 里登记过（参数形状），那里天然有一个 `label`
    字段（"擦除"）。让人为了一个中文名再抄一遍到映射里，就是两份真源
    等着打架 —— 所以两边都读：**映射里写了以映射为准**（它是"改名"的正本），
    只有 `calls.label` 的也用得上，都没有就显示原名。
    """
    out = []
    for call in calls or ():
        if not isinstance(call, dict):
            continue
        head = str(call.get('head') or '').strip()
        label = str(call.get('label') or '').strip()
        if head and label:
            out.append(Term(KIND_CUE, head, label=label))
    return out


# ---------------------------------------------------------------------------
#  从项目里**学**一份初稿（够用就好，认不出就空着）
# ---------------------------------------------------------------------------

#: 注释里的编号：`# bgm18` / `# bg 9.2` / `# se9` / `# 表情B7`。
_LEARN = (
    (KIND_MUSIC, re.compile(r'(?<![A-Za-z])(bgm|BGM)\s?(\d{1,3})(?!\d)'),
     re.compile(r'^\s*play\s+music\s+[\'"]([^\'"]+)[\'"]')),
    (KIND_SOUND, re.compile(r'(?<![A-Za-z])(se|SE)\s?(\d{1,3})(?!\d)'),
     re.compile(r'^\s*play\s+sound\s+[\'"]([^\'"]+)[\'"]')),
    (KIND_BG, re.compile(r'(?<![A-Za-z])(bg|BG)\s?(\d{1,2}(?:[._]\d{1,2})?)'
                         r'(?!\d)'),
     re.compile(r'^\s*scene\s+bg\s+(\w+)')),
)


def learn(project_root, *, texts=None) -> list:
    """
    扫剧本注释，学出一批 `{kind, id, code}`（中文名留空，等人补）。

    规矩与 `sprite_codes` 同一条：**注释在前、语句在后**才算证据；一条注释
    只认它后面第一条对应语句（`play music` / `play sound` / `scene bg`），
    中间隔了别的语句就不算（宁缺毋滥）。
    """
    items = _project_texts(project_root, texts)
    found = {}
    for text in items:
        lines = str(text or '').splitlines()
        pending = None
        for raw in lines:
            line = raw.strip()
            if line.startswith('#'):
                for kind, code_re, _stmt in _LEARN:
                    m = code_re.search(line)
                    if m:
                        pending = (kind, m.group(0).replace(' ', ''))
                continue
            if not line:
                continue
            matched = False
            if pending is not None:
                for kind, _code_re, stmt_re in _LEARN:
                    if kind != pending[0]:
                        continue
                    m = stmt_re.match(raw)
                    if m:
                        code = pending[1]
                        ident = m.group(1).strip()
                        # 一个编号只认**第一条**证据：同一个 `# bgm18` 后面跟着
                        # 两条不同的 play music，多半是复制粘贴来的 —— 后一条
                        # 要是也登记，反查「18 是哪首」就有了两个答案。
                        found.setdefault((kind, code), ident)
                        matched = True
            if not matched or pending is not None:
                pending = None
    return [{'kind': kind, 'id': ident, 'code': code}
            for (kind, code), ident in sorted(found.items())]


def _project_texts(project_root, texts=None) -> list:
    """项目里所有剧本 `*.rpy` 的文字（跳过 cache/saves/libs/build）。"""
    if texts is not None:
        return list(texts)
    from pathlib import Path

    root = Path(str(project_root or '').strip())
    game = root / 'game'
    if not str(project_root or '').strip() or not game.is_dir():
        return []
    skip = {'cache', 'saves', 'libs', 'build', '__pycache__'}
    out = []
    for path in sorted(game.rglob('*.rpy')):
        if skip & set(path.parts):
            continue
        try:
            out.append(path.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            continue
    return out

__all__ = ['KIND_TRANSFORM', 'KIND_MUSIC', 'KIND_SOUND', 'KIND_VOICE',
           'KIND_BG', 'KIND_CHARACTER', 'KIND_CUE', 'KIND_COSTUME',
           'KIND_EXPRESSION', 'KINDS', 'KIND_SPECS', 'Kind', 'KEYS',
           'kind_label', 'is_kind', 'kind_from_text', 'norm', 'Term',
           'EMPTY', 'parse_term', 'parse_terms', 'Vocabulary',
           'EMPTY_VOCABULARY', 'from_mapping', 'learn', 'cue_terms']
