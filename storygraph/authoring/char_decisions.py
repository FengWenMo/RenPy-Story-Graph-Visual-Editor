"""
角色决策（纯函数）：未映射 token → **候选 + 证据** → 决定 → 写回角色表
================================================================================

用户 2026-10-10 的方案（原话）：

> A（保留+告警），但是先这样：在转换时搜索已定义的相似字符，可以搜索相关的 define，
> 比如 `Ljk` 和 `ljk` 最像 `L_J_K` 也有可能，`mom` 在游戏内显示为妈妈等……，
> 然后写一个弹窗，问是不是已定义的这个角色？如果是，自动标上别名，
> 如果不是，自动生成新角色 define 并输出。

**为什么这件事能做：因为中间那个"问一句"。** 字符串相似度**永远**分不清
"同一个人写错了"和"两个长得像的人"（用户担心的正是后者）—— 把判定交给
**人来点一下**，就把它从"猜"变成了"确认"。所以本模块只做两件事：

    「给候选 + **说清凭什么**」  →  人决定  →  「按决定改表」

## 分两层（对应 `suggestions()` / `plan_*`）

**① 建议层（纯，不写盘）** `suggestions(conversion, chars, rows)`

每个未映射 token 得到一条 `Suggestion`：出现次数、**原文样例**（人要看上下文
才知道 `MOM` 是不是妈妈）、候选角色 + 每个候选的相似度证据。

证据分档（**相似度是给人看的，不是用来替人决定的**）：

| 证据 | 判据 | 例 |
|---|---|---|
| `same`  | 压根不该走到这（归一后相等）→ 若出现即为异常，提示改名 | `LJK` ↔ 变量 `ljk` |
| `case`  | 忽略大小写后相等 | `MOM` ↔ 别名 `mom` |
| `sep`   | 再去掉下划线/空格后相等 | `L_J_K` ↔ `ljk` |
| `fuzzy` | 归一形态的编辑距离（`difflib`），≥60% 才展示 | `TJF` ↔ `txf` |

⚠ **没有"拼音/语义"这一档**：`MOM`↔`妈妈`、`LINGDAO`↔`ld`（校领导）这一类**算不出来**
（项目零拼音依赖，而手写的字表被实测打脸 —— 详见文件上方那段说明）。
它们靠弹窗里的**手工挑选**兜住：弹窗本来就该把整张角色表摊给用户选。

⚠ 有证据 ≠ 就该自动改。`Ljk`/`ljk` 那种"只差大小写"的候选**恰恰是最危险的**
（可能是两个人），所以它只作为候选出现，并且 `plan_alias` 会**主动拦下**
不能表达的别名。

**② 决定层（纯，算出表格该怎么变）**

    plan_alias(rows, token, target)     → AliasPlan（能不能加、加完长什么样）
    plan_new_character(rows, token, …)  → 新角色那一行（重名要报）
    apply_plans(asset_dir, …)           → 真的写 characters.xlsx（走 append_rows_to_table）

口径（用户 2026-10-10 拍板）：
- 新角色默认 **变量名 = token 原样、显示名 = token 原样**，弹窗里可改；
- 新角色**写进 `characters.xlsx`**（只追加不覆盖），`define` 交给既有同步链路
  —— 不写进章节产物（多章会重复 define，那正是被砍掉的那版脚本的毛病）。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.assets.asset_completion import score_candidates
from storygraph.assets.asset_registry import append_rows_to_table
from storygraph.authoring.txt_import import (Ambiguity, CharacterMap, _norm_key,
                                             split_aliases)

# ===================== 证据 =====================

#: 相似度证据的四档（**全部是可验证的字符串事实**，没有"感觉像"）
SAME = 'same'
CASE = 'case'
SEP = 'sep'
FUZZY = 'fuzzy'

#: `fuzzy` 证据的准入线（低于它的候选**不展示** —— 免得用户乱点）
_FUZZY_MIN = 0.6

#: ⚠ **刻意不做拼音首字母匹配**（2026-10-10 试过又撤掉，理由留档）：
#:
#: `LINGDAO` ↔ `ld`（「校领导」）看着像是"能算出来的"，但项目**零拼音依赖**
#: （requirements 只有 openpyxl/PySide6/Pillow），要么装 `pypinyin`、要么手写
#: 一张字表。我先按 GB2312 一级汉字"按拼音排序"的区间推了一版，**结果不可信**：
#: `吧` 被判成 `a`（实际 `b`）、`瑾` 被判成 `z`（实际 `j`）。原因是没有
#: 权威数据源时，锚点字本身就可能落在多音字/边界上 —— 这等于是**造一个假权威**。
#:
#: 为什么撤掉而不是将就：候选列表的质量决定这个弹窗的价值。给一串"排在最前
#: 却毫不相干"的候选，用户会**学会不看候选**（比没有候选更糟），而
#: `MOM↔妈妈`、`LINGDAO↔ld` 这类**本来就不是字符串问题**（要语义/上下文），
#: 正确处置是弹窗里那个**手工挑选**的下拉框。
#:
#: 以后若要加回来：装 `pypinyin`（或引入一张 commit 进仓库的权威字表），
#: 并且判据要写成"已知首字母的真机名字逐个对齐"（`叶怀瑾→yhj`、`校领导→ld`）。

#: 证据档位 → 给人看的说明（**必须说人话**：用户要凭这句话点"是/不是"）
EVIDENCE_LABEL = {
    SAME: '与已有变量只差大小写/分隔符 —— 归一化会把它当成**同一个人**，'
          '若确是同一个人，正确做法是把脚本里的写法改成表里的变量名',
    CASE: '忽略大小写后完全相同',
    SEP: '再去掉下划线/空格后完全相同',
    FUZZY: '写法相近',
}


@dataclass
class Candidate:
    """一个候选角色（**带证据**，不是光给个名字）。"""
    var: str
    evidence: str
    score: float = 0.0
    reason: str = ''

    @property
    def label(self) -> str:
        return f'{self.var}（{self.reason or EVIDENCE_LABEL[self.evidence]}）'


@dataclass
class Suggestion:
    """一个未映射 token 的完整处境（弹窗的一行）。"""
    token: str
    count: int = 0
    samples: list = field(default_factory=list)      # [(行号, 原文)]，看上下文用
    candidates: list = field(default_factory=list)   # [Candidate]
    note: str = ''

    @property
    def best(self):
        return self.candidates[0] if self.candidates else None

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


# ===================== 建议层 =====================

def _evidence_for(token: str, var: str, row: dict) -> Candidate:
    """
    算"这个 token 与这个角色"的证据。

    ⚠ 只用**可验证的字符串事实**（大小写 / 分隔符 / 编辑距离），
    **不涉及语义、不涉及拼音**（拼音那条为什么撤掉见文件上方 `SAME`/`CASE` 的说明）。
    比较对象是该角色的**三种写法**：变量名、显示名、别名列 ——
    作者写 `「叶怀瑾」` 时 token 就是显示名本身；别名列写了 `Mom` 时 `MOM` 就命中。
    """
    tok = str(token or '').strip()
    row = row or {}
    name = str(row.get('Name') or '')
    aliases = split_aliases(row.get('Aliases'))
    # 这个角色的所有写法（变量名 + 显示名 + 别名）
    spellings = [(str(var or ''), 'variable')] + \
        ([(name, 'display')] if name else []) + \
        [(a, 'alias') for a in aliases]
    for text, _which in spellings:
        if text and tok != text and tok.lower() == text.lower():
            return Candidate(var, CASE, 0.95, EVIDENCE_LABEL[CASE])
        if text and _norm_key(tok) == _norm_key(text):
            return Candidate(var, SEP, 0.9, EVIDENCE_LABEL[SEP])
    # 写法相近：只看字母数字形态（显示名里带汉字的，去掉汉字后比）
    a = re.sub(r'[^0-9a-z]', '', tok.lower())
    forms = [str(var or '')] + ([name] if name else []) + aliases
    ratios = [
        difflib.SequenceMatcher(None, a, re.sub(r'[^0-9a-z]', '', f.lower()) or ' ')
        .ratio() for f in forms if f
    ]
    ratio = max(ratios or [0.0])
    if ratio >= _FUZZY_MIN:
        return Candidate(var, FUZZY, ratio, f'写法相近（{ratio:.0%}）')
    return Candidate(var, FUZZY, ratio, '')


def _has_han(text: str) -> bool:
    return any('\u4e00' <= ch <= '\u9fff' for ch in str(text or ''))


#: "填充名"判据：只有 ≤2 个不同字符的短变量名（`aaa`/`aabb`/`bb`）——
#: 真机表里有这种**拼出来的组合行**（`舍友A`=`aaa`、`A&B`=`aabb`）。
#: 它们与单字母 token（真机里 `A"卷王——"` / `B"大学生楷模——"`）的编辑距离是
#: **100%**（`a` vs `aaa` 完全包含），但那不是"像"，只是"短"。
_FILLER_RE = re.compile(r'^[a-z0-9]{1,6}$')


def _is_filler_name(var: str) -> bool:
    s = str(var or '').lower()
    return bool(_FILLER_RE.match(s)) and len(set(s)) <= 2


def _rank_candidates(token: str, rows, chars: CharacterMap,
                     max_candidates: int) -> list:
    """
    打分并排序候选（**证据强的前面，噪音砍掉**）。

    ⚠ 为什么不能直接用 `score_candidates` 的排序：它的第 0 档是**前缀命中**，
    于是单字母 token `A` 会把 `aaa`/`aabb`/`ay` 全排到前面（前缀 = 全表命中，
    等于没有候选）。探针实测过。所以这里：**先按证据分档，前缀只作为召回手段**。

    砍噪音的两条（都来自实测）：
      · `fuzzy` 证据要 ≥ `_FUZZY_MIN`；单字母 token 更要 ≥ 0.8
        （`A` 与 `aaa` 的 ratio 是 1.0，纯粹因为短）；
      · 同分时**有汉字显示名的优先** —— 真机表里 `txf`（汤X凤）/`mm`（妈妈）
        是真角色，`aaa`/`aabb`/`dhyab` 是拼出来的组合行。
    """
    items = [(v, v) for v in rows] if rows else \
        [(v, v) for v in chars.by_key.values()]
    items = list(dict.fromkeys(items))
    recall = score_candidates(items, token, fuzzy=True, min_score=0.25, limit=100)
    threshold = 0.8 if len(str(token or '').strip()) <= 1 else _FUZZY_MIN
    found = {}
    for _t, var in recall:
        row = rows.get(var) or {}
        ev = _evidence_for(token, var, row)
        if not ev.reason:
            continue
        if ev.evidence == FUZZY and ev.score < threshold:
            continue
        # 短 token 与"填充名"的 100% 相似是假象（`A` vs `aaa`），不是候选
        if (ev.evidence == FUZZY and len(str(token or '').strip()) <= 2
                and _is_filler_name(var)):
            continue
        found[var] = ev
    # 兜底：`score_candidates` 召回不到的（例如大小写/分隔符差异但前缀不同），
    # 也要能凭 `CASE`/`SEP` 证据进候选 —— 那是**可验证的事实**，不该漏。
    for var, row in (rows or {}).items():
        if var in found:
            continue
        ev = _evidence_for(token, var, row)
        if ev.evidence in (CASE, SEP) and ev.reason:
            found[var] = ev
    order = {CASE: 0, SEP: 1, FUZZY: 2}
    cands = sorted(found.values(),
                   key=lambda c: (order.get(c.evidence, 9), -c.score,
                                  not _has_han((rows.get(c.var) or {}).get('Name')),
                                  c.var))
    return cands[:max_candidates]


def suggestions(conversion, chars: CharacterMap = None,
                rows=None, max_candidates: int = 5) -> list:
    """
    未映射 token 清单 → `[Suggestion]`（**纯函数**，弹窗的数据源）。

    `conversion` 是 `txt_import.ConversionResult`；`chars`/`rows` 给角色表
    （`rows` 用来看显示名；不给他就只用 `chars`）。

    ⚠ 候选**宁少勿滥**：一份 28 个角色的表里给 5 个"看着都差不多"的候选，
    等于没有候选 —— 用户只会乱点。
    """
    chars = chars if isinstance(chars, CharacterMap) else CharacterMap(rows)
    rows = rows or {}
    lines = getattr(conversion, 'lines', None) or []
    amb = dict(getattr(conversion, 'ambiguous', None) or {})

    # 每个 token 的原文样例（人要看上下文才知道是不是同一个人）
    samples = {}
    for tl in lines:
        tok = getattr(tl, 'speaker_token', '')
        if not tok:
            continue
        samples.setdefault(tok, [])
        if len(samples[tok]) < 3:
            samples[tok].append((getattr(tl, 'no', 0),
                                 (getattr(tl, 'raw', '') or '').strip()[:80]))

    out = []
    tokens = dict(getattr(conversion, 'unmapped', None) or {})
    for tok in amb:                                  # 歧义的也要人决定
        tokens.setdefault(tok, len(samples.get(tok, [])) or 1)
    for tok in sorted(tokens):
        sug = Suggestion(token=tok, count=int(tokens[tok]),
                         samples=list(samples.get(tok, [])))
        if tok in amb:
            # 归一化撞车：**不能加别名**（那个写法已经被占了），要改名/定人
            sug.note = ('这个写法归一后撞到多个变量（' + '、'.join(amb[tok])
                        + '）—— 不能靠别名解决，需改成不靠大小写区分的变量名')
            sug.candidates = [Candidate(v, SAME, 1.0, '归一化撞车的一员')
                              for v in amb[tok]]
            out.append(sug)
            continue
        sug.candidates = _rank_candidates(tok, rows, chars, max_candidates)
        if not sug.candidates:
            sug.note = '表里没有看着像的角色 —— 要么手工指一个，要么建成新角色'
        out.append(sug)
    return out


# ===================== 决定层（纯：算表格该怎么变） =====================

@dataclass
class AliasPlan:
    """"把 token 当作某角色的别名"这件事的**计划**（还没写盘）。"""
    token: str = ''
    var: str = ''
    before: str = ''                  # 该角色原来的「别名」列
    after: str = ''                   # 加完长什么样
    ok: bool = False
    reason: str = ''

    @property
    def added(self) -> str:
        return self.token if self.ok else ''


def plan_alias(rows, token: str, var: str) -> AliasPlan:
    """
    算出"把 `token` 加进角色 `var` 的「别名」列"的后果（**预检 + 合并文本**）。

    四道闸门（任一不过就 `ok=False` 并说清原因）：

      1. 角色必须存在；
      2. token 不能**已经**是某个角色的写法（重复决定）；
      3. ⚠ token 归一后不能**等于任何变量名** —— 那种情况下加别名的结果是
         `Aliases` 里出现一个"等于变量名本人"的写法，`CharacterMap` 会把这个键
         判成**歧义**（我们刚建的机制会主动拒绝映射）→ 别名白加、更糟。
         正确处置是**改脚本写法或改变量名**（`Ljk`/`ljk` 那类）。
      4. 加完**不能撞车**（`CharacterMap` 预检）：撞了就报出来，不写。
    """
    rows = rows or {}
    token, var = str(token or '').strip(), str(var or '').strip()
    plan = AliasPlan(token=token, var=var)
    if var not in rows:
        plan.reason = f'角色表里没有 {var}'
        return plan
    # ⚠ 这一条**必须排在"已经是某个写法"前面**（2026-10-10 实测踩到）：
    # `LJK` 与变量 `ljk` 只差大小写，`lookup('LJK')` 会命中变量 `ljk`，
    # 于是先命中"已经是 ljk 的写法 → 不用加"，用户按提示去加别名也没用。
    # 真正的问题是**写法本身有歧义风险**，要说清处置办法。
    for other, row in rows.items():
        vid = str((row or {}).get('ID') or other).strip()
        if vid and vid != token and vid.lower() == token.lower():
            plan.reason = (f'"{token}" 与变量名 "{vid}" 只差大小写 —— '
                           f'加别名解决不了（别名归一后等于变量名本人，该写法会变歧义）；'
                           f'请把 txt/脚本里改成 {vid}，或把变量名改成不靠大小写区分的写法')
            return plan
        if vid == token:
            plan.reason = (f'"{token}" 与变量名 "{vid}" 完全相同 —— '
                           f'它本来就是变量名，不需要加别名')
            return plan
    cm = CharacterMap(rows)
    hit, how = cm.lookup(token)
    if isinstance(hit, Ambiguity):
        plan.reason = (f'"{token}" 归一后撞到多个变量（'
                       + '、'.join(hit.candidates) + '），加别名解决不了')
        return plan
    if hit:
        plan.reason = (f'"{token}" 已经是 {hit} 的写法（{how}）—— 不需要再加别名')
        return plan
    before = str((rows.get(var) or {}).get('Aliases') or '')
    parts = split_aliases(before)
    if token in parts:
        plan.reason = f'"{token}" 已经写在 {var} 的别名列里'
        return plan
    after = '|'.join(parts + [token])
    # ⚠ 判据只针对**新加的这个写法**：表里原本就有的撞车不该把这次加别名判死
    # （那是另一件事，`conflicts` 会单独报）；而"变量名已存在"在这里是**必然**的
    # —— 我们就是往那个角色那一行写别名，所以只能问"这个写法会不会抢别人的名字"。
    owner = _spelling_taken_by_others(rows, token, var)
    if owner:
        plan.reason = f'加完会撞车：写法「{token}」已经被 {owner} 用了'
        return plan
    probe = dict(rows)
    probe[var] = dict(rows.get(var) or {}, Aliases=after)
    resolved, _how = CharacterMap(probe).lookup(token)
    if resolved != var:
        plan.reason = (f'加完 "{token}" 仍解析到 {resolved}（不是 {var}）'
                       f'—— 这个写法已经被别人占用')
        return plan
    plan.before, plan.after, plan.ok = before, after, True
    return plan


def _spelling_taken_by_others(rows, text: str, owner: str) -> str:
    """
    `text` 这个写法是不是已经被**别人**（不是 `owner` 自己）占用了？返回占用者。

    ⚠ 与"变量名是否已存在"是**两件事**，别混用（2026-10-10 实测踩到）：
    加别名时 `owner` 本来就必须存在（我们就是往它那一行写），所以不能拿
    "变量名已存在"当拒绝理由；要问的是"**这个写法会不会抢别人的名字**"。
    """
    text = str(text or '').strip()
    if not text:
        return ''
    owner = str(owner or '').strip()
    key = _norm_key(text)
    for other, row in (rows or {}).items():
        row = row or {}
        oid = str(row.get('ID') or other).strip()
        if oid == owner:
            continue                        # 自己名下的写法 = 本意，不算冲突
        oname = str(row.get('Name') or '').strip()
        spellings = [oid] + ([oname] if oname else []) + \
            split_aliases(row.get('Aliases'))
        if any(s and _norm_key(s) == key for s in spellings):
            return oid
    return ''


def _adding_breaks(rows, var: str, name: str = '', alias: str = '') -> str:
    """
    把"**新角色** `var`（显示名 `name`、别名 `alias`）"加进表里会打破什么吗？
    返回原因（空串 = 没问题）。⚠ 只给"建新角色"用（加别名走
    `_spelling_taken_by_others`：那边 `var` 是**必须已经存在**的）。

    ⚠ 判据必须只针对**即将新增的这位**。早先的写法是"建个 probe 表看有没有
    `conflicts`" —— 于是表里**本来就有的**撞车（`ljk` 与 `Ljk`）会把
    **任何**新建/加别名都判死：探针实测，连 `lujk` + 全新显示名都建不了。
    撞车只有"新符号与它指向的含义对不上"才算数。
    """
    var = str(var or '').strip()
    name = str(name or '').strip()
    alias = str(alias or '').strip()
    if not var:
        return '没有可用的变量名'
    for other, row in (rows or {}).items():
        row = row or {}
        oid = str(row.get('ID') or other).strip()
        if oid == var:
            return f'变量名 {var} 已经存在'
        if oid and _norm_key(oid) == _norm_key(var):
            return (f'变量名 {var} 与 {oid} 归一后是同一个写法'
                    f'（只差大小写/下划线/空格）—— 请换一个不靠大小写区分的名字')
    for text, which in ((name, '显示名'), (alias, '别名')):
        owner = _spelling_taken_by_others(rows, text, var)
        if owner:
            return f'{which}「{text}」已经被 {owner} 用了'
    return ''


@dataclass
class NewCharacterPlan:
    """"给 token 建一个新角色"的计划（还没写盘）。"""
    token: str = ''
    row: dict = field(default_factory=dict)
    ok: bool = False
    reason: str = ''


def plan_new_character(rows, token: str, var: str = '', name: str = '',
                       image_tag: str = '', notes: str = '') -> NewCharacterPlan:
    """
    算出新角色那一行（口径：**变量名/显示名默认就是 token 原样**，人可在弹窗里改）。

    重名/撞车要**报**（`ID` 已存在 / 与别人的写法归一后相同 / 显示名已被别人用）
    —— 那些情况正确动作是"当作已有角色加别名"或**换一个不靠大小写区分的名字**，
    不是硬建第二个人。
    """
    rows = rows or {}
    token = str(token or '').strip()
    var = (str(var).strip() or _safe_var(token))
    name = str(name).strip() or token
    plan = NewCharacterPlan(token=token)
    if not var:
        plan.reason = f'"{token}" 变不出合法的变量名，请手工填'
        return plan
    why = _adding_breaks(rows, var, name)
    if why:
        plan.reason = f'{why} —— 要么当作已有角色加别名，要么换个名字'
        return plan
    # ⚠ 空值列**不放进去**：`append_rows_to_table` 里判断"值变没变"用的是
    # `(cell.value or '') != (row.get(key) or '')`，而 openpyxl 读空单元格给 `None`
    # —— 空字符串那一格会被判成"没变化"从而不落盘（表里也不会出现空列，
    # 效果一样）。这里只带有内容的键，避免踩那个既有实现的小坑。
    row = {'ID': var, 'Name': name}
    if image_tag:
        row['ImageTag'] = image_tag
    note = notes or f'由纯文字导入创建（原文：{token}）'
    if note:
        row['Notes'] = note
    plan.row = row
    plan.ok = True
    return plan


def _safe_var(token: str) -> str:
    """token → 能当 Ren'Py 变量名的写法（**不做拼音猜测**，只做字符合法化）。"""
    var = re.sub(r'[^A-Za-z0-9_]', '', str(token or ''))
    if not var or var[0].isdigit():
        var = 'c' + var
    return var


# ===================== 提议层（把"建议 + 角色表"过一遍，给出可执行动作） =====================

#: 动作：指到已有角色（写别名）/ 建新角色 / 先跳过
#: ⚠ 名字是"existing"而不是"alias"：动作是**指到已有角色**，
#: 落地方式恰好是"写进别名列"（以后若改成别的落地方式，这个名字还成立）。
ACT_EXISTING = 'existing'
ACT_NEW = 'new'
ACT_SKIP = 'skip'


@dataclass
class Proposal:
    """
    一个 token 的**可执行提议**（纯计算结果，弹窗/CLI/测试共用同一份）。

    `alias` / `new` 两个计划**最多只有一个 ok**：别名走得通就走别名（不动角色表结构），
    走不通才建新角色 —— 这条顺序是刻意的：能不改结构就别改。
    """
    suggestion: Suggestion
    rows: dict = field(default_factory=dict)
    alias: AliasPlan = None
    new: NewCharacterPlan = None
    action: str = ACT_SKIP
    note: str = ''

    @property
    def token(self) -> str:
        return self.suggestion.token

    @property
    def alias_ok(self) -> bool:
        return bool(self.alias and self.alias.ok)

    @property
    def new_ok(self) -> bool:
        return bool(self.new and self.new.ok)

    @property
    def executable(self) -> bool:
        return self.action != ACT_SKIP and (self.alias_ok or self.new_ok)


def propose_one(rows, suggestion: Suggestion, target: str = '') -> Proposal:
    """
    "建议 + 角色表" → 可执行提议。

    `target` 是**人指定的角色变量**（弹窗里那个下拉选的）：
      · 给了 → 只试别名（人已经指名了，不该再自作主张建新角色）；
      · 没给 → 先试**证据最强**的候选，走不通再试建新角色。
    """
    rows = rows or {}
    alias = new = None
    if target:
        alias = plan_alias(rows, suggestion.token, target)
    elif suggestion.candidates:
        alias = plan_alias(rows, suggestion.token, suggestion.candidates[0].var)
    if alias is None or not alias.ok:
        new = plan_new_character(rows, suggestion.token)
        if new.ok:
            return Proposal(suggestion=suggestion, rows=rows, alias=alias,
                            new=new, action=ACT_NEW,
                            note='表里没有这个角色 → 建新角色（显示名可改）')
        reason = (alias.reason if alias and alias.reason else '') or \
            (new.reason if new else '') or '无法自动处理'
        return Proposal(suggestion=suggestion, rows=rows, alias=alias, new=new,
                        action=ACT_SKIP, note=reason)
    return Proposal(suggestion=suggestion, rows=rows, alias=alias, new=new,
                    action=ACT_EXISTING,
                    note=f'当作 {alias.var} 的别名（写进 characters.xlsx）')


def propose_alias(rows, token: str, var: str) -> Proposal:
    """弹窗那一行选了"指到某个角色"时的**复算**（判据与 `plan_alias` 同一套）。"""
    sug = Suggestion(token=str(token or ''))
    alias = plan_alias(rows or {}, token, var)
    prop = Proposal(suggestion=sug, rows=rows or {}, alias=alias,
                    action=ACT_EXISTING if alias.ok else ACT_SKIP,
                    note=alias.reason or f'当作 {var} 的别名')
    if not alias.ok:
        # 别名走不通时**给第二次机会**：这个 token 能不能建新角色
        # （否则弹窗里这一格会变成死路，用户只能跳过）
        prop.new = plan_new_character(rows or {}, token)
        if prop.new.ok:
            prop.note += '；也可以直接建新角色'
    return prop


def propose_new_character(rows, token: str, var: str = '',
                          name: str = '') -> Proposal:
    """弹窗那一行选了"建新角色"时的复算。"""
    sug = Suggestion(token=str(token or ''))
    new = plan_new_character(rows or {}, token, var=var, name=name)
    return Proposal(suggestion=sug, rows=rows or {}, new=new,
                    action=ACT_NEW if new.ok else ACT_SKIP,
                    note=new.reason or
                    f'将新建角色 {new.row.get("ID", "")}（显示名 {new.row.get("Name", "")}）')


def proposals(conversion, chars: CharacterMap = None, rows=None,
              max_candidates: int = 5) -> list:
    """`suggestions()` → `propose_one()`（弹窗/CLI 一行就够的入口）。"""
    rows = rows or {}
    return [propose_one(rows, s)
            for s in suggestions(conversion, chars, rows, max_candidates)]


# ===================== 落盘层（唯一入口） =====================

def asset_dir_of(project_root) -> Path:
    """
    角色表在哪（**唯一解析点**）：`<根>/game/asset_tables/`。

    `append_rows_to_table` 收的是**目录**（它自己拼 `characters.xlsx`），
    所以这个路径得有人负责算对 —— 算错就是"写到隔壁目录去了"。
    """
    root = Path(project_root or '.')
    game = root / 'game'
    return (game if game.is_dir() else root) / 'asset_tables'


def decisions_from(proposals, rows=None) -> list:
    """
    把 `[Proposal]`（弹窗结果）或 `[{token, action, target|var, name}]`
    （给人看的决定清单）统一成 `[Proposal]`。

    两种形态都要收：UI 那边自然产出"决定清单"（可日志、可回读），
    纯逻辑这边要的是带计划的 `Proposal`；转换只做一次，免得两处各写一套。
    """
    out = []
    for item in (proposals or []):
        if isinstance(item, Proposal):
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        sug = Suggestion(token=str(item.get('token') or ''))
        act = str(item.get('action') or ACT_SKIP)
        if act == ACT_NEW:
            out.append(Proposal(
                suggestion=sug, rows=rows or {}, action=ACT_NEW,
                new=plan_new_character(rows or {}, sug.token,
                                      var=item.get('var', ''),
                                      name=item.get('name', ''))))
        elif act == ACT_EXISTING:
            out.append(propose_alias(rows or {}, sug.token,
                                     item.get('target', '')))
        else:
            out.append(Proposal(suggestion=sug, rows=rows or {},
                                action=ACT_SKIP, note='跳过'))
    return out


def apply_decisions(asset_dir, proposals, rows=None, backup: bool = False) -> dict:
    """
    把弹窗里的决定**写进 `characters.xlsx`**（唯一落盘入口）。

    `rows` 是**当前**角色表（`{变量: 行}`）—— 决定清单里只有 `token`/`target`，
    判据（`plan_alias`）要拿表来算。⚠ 不传它就会拿着空表判"角色不存在"→
    **所有决定静默跳过**（实测踩到：`aliased=0` 却没人报错）。

    顺序是刻意的：**先建新角色，再加别名** —— 别名可能指到"刚刚才建出来"的
    角色（用户在弹窗里先点"建新角色"，又在另一行把同一个人指过去）。

    返回 `{'ok','added','aliased','skipped','messages'}`：
    每一项都**出声**（写了什么、为什么没写），因为这是"用户以为改好了"的
    高风险路径 —— 静默跳过比报错坏得多。
    """
    items = decisions_from(proposals, rows)
    new = [p.new for p in items if p.action == ACT_NEW and p.new_ok]
    alias = [p.alias for p in items if p.action == ACT_EXISTING and p.alias_ok]
    skipped = [p for p in items
               if not (p.new_ok or p.alias_ok) and p.action != ACT_SKIP]
    messages = []
    for p in skipped:
        why = (p.new.reason if p.new and p.new.reason else '') or \
            (p.alias.reason if p.alias and p.alias.reason else '') or p.note
        messages.append(f'"{p.token}" 没写进表：{why}')
    added = aliased = 0
    if new:
        res = apply_new_character_plans(asset_dir, new, backup=backup)
        added = int(res.get('written') or 0)
        if not res.get('ok'):
            messages.append(res.get('reason') or '新角色没写进表')
        else:
            for p in new:
                messages.append(f'新建角色 {p.row.get("ID")}'
                                f'（显示名 {p.row.get("Name")}）'
                                f'← 原文写法 "{p.token}"')
    if alias:
        res = apply_alias_plans(asset_dir, alias, backup=backup)
        aliased = int(res.get('written') or 0)
        if not res.get('ok'):
            messages.append(res.get('reason') or '别名没写进表')
        else:
            for p in alias:
                messages.append(f'给 {p.var} 加别名 "{p.token}"'
                                f'（「别名」列：{p.after}）')
    return {'ok': bool(added or aliased), 'added': added, 'aliased': aliased,
            'skipped': len(skipped), 'messages': messages}


def preview_decisions(asset_dir, proposals, rows=None) -> list:
    """
    不写盘，只说清"每个决定会怎么改那张表"（CLI / 确认框用）。

    与 `apply_decisions` **同一套判据**（都走 `plan_alias`/`plan_new_character`）
    —— 预览和实际动作不可能不一致。
    """
    items = decisions_from(proposals, rows)
    lines = []
    for p in items:
        if p.action == ACT_EXISTING and p.alias_ok:
            lines.append(f'· {p.token} → 角色 {p.alias.var} 的别名'
                         f'（「别名」列 {p.alias.before or "(空)"} → {p.alias.after}）')
        elif p.action == ACT_NEW and p.new_ok:
            lines.append(f'· {p.token} → 新建一行：ID={p.new.row.get("ID")} '
                         f'显示名={p.new.row.get("Name")}')
        elif p.action == ACT_SKIP:
            lines.append(f'· {p.token} → 跳过（产物里仍是原文写法）')
        else:
            why = (p.new.reason if p.new and p.new.reason else '') or \
                (p.alias.reason if p.alias and p.alias.reason else '') or p.note
            lines.append(f'· {p.token} → **不可行**：{why}')
    return lines


def apply_alias_plans(asset_dir, plans, backup: bool = False) -> dict:
    """
    把 `AliasPlan` 写进 `characters.xlsx`（**只改「别名」列**，别的列一个不碰）。

    落盘走 `asset_registry.append_rows_to_table(..., update_keys=('Aliases',))`
    —— 那是既有的"只追加 + 列白名单"实现，我不另写一套写表代码
    （**唯一写盘入口**契约）。
    """
    good = [p for p in (plans or []) if getattr(p, 'ok', False)]
    if not good:
        return {'ok': False, 'written': 0, 'skipped': len(list(plans or [])),
                'reason': '没有可执行的别名计划'}
    rows = [{'ID': p.var, 'Aliases': p.after} for p in good]
    n = append_rows_to_table(asset_dir, 'characters', rows,
                             update_keys=('Aliases',), backup=backup)
    return {'ok': True, 'written': n, 'applied': [(p.token, p.var) for p in good],
            'skipped': 0}


def apply_new_character_plans(asset_dir, plans, backup: bool = False) -> dict:
    """
    把 `NewCharacterPlan` 追加进 `characters.xlsx`（**只追加**，已有行不动）。

    `define` 不由这里写：角色表是唯一真源，项目既有的同步链路会把它带去脚本
    （写进章节产物会在多章导入时**重复定义** —— 那正是被砍掉的那版脚本的毛病）。
    """
    good = [p for p in (plans or []) if getattr(p, 'ok', False)]
    if not good:
        return {'ok': False, 'written': 0, 'skipped': len(list(plans or [])),
                'reason': '没有可执行的新角色计划'}
    n = append_rows_to_table(asset_dir, 'characters',
                             [dict(p.row) for p in good], backup=backup)
    return {'ok': True, 'written': n, 'applied': [p.row.get('ID') for p in good],
            'skipped': 0}


__all__ = [
    'ACT_EXISTING', 'ACT_NEW', 'ACT_SKIP', 'AliasPlan', 'Candidate',
    'NewCharacterPlan', 'Proposal', 'Suggestion', 'apply_alias_plans',
    'apply_decisions', 'apply_new_character_plans', 'asset_dir_of',
    'decisions_from', 'plan_alias', 'plan_new_character', 'preview_decisions',
    'propose_alias', 'propose_new_character', 'propose_one', 'proposals',
    'suggestions',
]
