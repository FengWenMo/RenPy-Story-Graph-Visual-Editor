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

  1. token **能在角色表里查到**（忽略大小写、角括号、引号）→ 认，并**映射成表里的变量**；
  2. token 形如**短的全大写缩写且不含下划线**（`^[A-Z][A-Z0-9]{0,7}$`，真实剧本里就是
     `JC`/`YHJ`/`MOM`/`A`/`B`）→ 认，但记进"未映射"（产物里保留原 token，可读、可人工改）；
  3. 其余（例如 `世界上再也没有什么比`）→ **一律当旁白**，绝不猜。

第 3 条是关键：宁可把一句带引号的旁白当旁白，也不要把旁白当角色 —— 后者是
**静默改剧情**（说话人错了，游戏里显示的名字就错了）。

## ⚠ 三个"看起来像角色"的陷阱（2026-10-10 对抗性探针实测，已修）

**① 大小写只能算"同一个人的另一种写法"，前提是两者都指向同一个人。**
表里同时有 `ljk` 与 `Ljk` 两个变量时，归一化会把**两个人**当成一个人：
实测旧版把 `Ljk"我是陆嘉珂"` 写成 `ljk "我是陆嘉珂"` —— 说话人换人了，
产物里**看不出**哪一行错。现在（见 `CharacterMap.lookup`）：
**精确写谁就是谁**（`ljk`→`ljk`、`Ljk`→`Ljk`），任何不规范写法
（`LJK`/`L_J_K`）返回**歧义**、保留原文、报出来让人定 —— 绝不替人挑一个。

**② 未映射的 token 写进产物 = Ren'Py 运行期必崩。**
产物里没有 `define`，于是 `OK "好的"` 在游戏里是**未定义变量**
（玩家到那一句就 `NameError` 崩）。真机 5 章 1372 行里这种写法有 **23 处**
（`MOM`×13 / `A`×4 / `B`×4 / `LINGDAO`×4 / `JIAOGUAN`×3 / `XIAOYI`×2 /
`BAOAN`×3 / `TJF`×1）。所以：
· 第 2 条**收紧**了（必须角色表非空 + 短缩写 + 不含下划线）；
· 每个未映射 token 现在都额外报一条"⚠ 直接落盘会因未定义变量崩掉"。

**③ 整段 Ren'Py 脚本被当纯文字粘进来**：逐行变旁白（`menu:`/`jump`/`show`
全成台词），旧版**零告警**。现在多行命中代码形状时提示走「导入章节 .rpy」。
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
#: 第 2 条判据真正用的形状：**短**（1–8）+ `A–Z0–9` + **不含下划线**。
#: 单字母也要放行 —— 真机 5 章里就是 `A"卷王——"` / `B"大学生楷模——"`（各 4 处）。
#: ⚠ 为什么排除下划线：下划线是"变量名"的形状，而归一会把下划线**丢掉** ——
#: 于是 `L_J_K` 会命中 `ljk`（作者根本没写过这个角色）→ 探针实测的静默改剧情。
_ABBREV_TOKEN_RE = re.compile(r'^[A-Z][A-Z0-9]{0,7}$')
#: 章节头（`1.0 chapter0-0 初入大学` / `chapter0_0 …`）
_HEADER_RE = re.compile(r'^\s*\d+(?:\.\d+)?\s*([A-Za-z][\w\-]*\d[\w\-]*)\b')
#: 章节头里那个"章节号"（`chapter0-0`）
_CHAPTER_ID_RE = re.compile(r'(chapter[\-_]?\d+[\-_]\d+)', re.I)
#: "这段输入像是已经写好的 Ren'Py 脚本"的形状（只在多行命中时才提示）
_CODE_LIKE_RES = (
    (re.compile(r'^label\s+[\w.]+\s*:'), 'label'),
    (re.compile(r'^\$\s*\S'), '美元语句'),
    (re.compile(r'^python\s*:'), 'python 块'),
    (re.compile(r'^(scene|show|hide)\s+\w'), '舞台指令'),
    (re.compile(r'^(menu|jump|call|return|if|elif|else|while|for)\b\s*:?'),
     '控制流/跳转'),
)


#: UTF-16/32 的 BOM（**长的先试**：UTF-32-LE 的前两字节正是 UTF-16-LE 的 BOM）
_BOMS = (
    (b'\xff\xfe\x00\x00', 'utf-32'),
    (b'\x00\x00\xfe\xff', 'utf-32'),
    (b'\xff\xfe', 'utf-16'),
    (b'\xfe\xff', 'utf-16'),
)


def read_text_file(path) -> str:
    """
    读纯文本：**BOM** → `utf-8-sig` → `utf-8` → `gbk` 依次试。

    ⚠ 为什么先看 BOM：探针实测，`utf-16-le` 保存的文稿被这条链**"成功"读成 GBK**
    （字节 `J\\x00C\\x00` 恰好是合法 GBK 双字节），于是在**零告警**的情况下把整章
    变成乱码 —— 这正是 MEMORY §B「要么全对要么完全不动」最怕的形态。

    ⚠ 为什么 utf-8 之后还要看有没有 NUL：无 BOM 的 UTF-16 也走同一条死路
    （`utf-8-sig` 只在有 BOM 时才报错），所以这里**宁可报"读不出来"**，
    也不给调用方一份乱码。

    三种都失败就抛 `UnicodeDecodeError`，让调用方报给用户
    （"读不出来"要能看见，而不是得到一份空文件）。
    """
    raw = Path(path).read_bytes()
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return raw.decode(enc)
    errors = []
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError as exc:
            errors.append(f'{enc}: {exc.reason}')
            continue
        if '\x00' in text:
            # 解出来带 NUL = 几乎一定不是这种编码（典型：无 BOM 的 UTF-16 被当 GBK）
            errors.append(f'{enc}: 解出了 NUL 字节（像是无 BOM 的 UTF-16/UTF-32）')
            continue
        return text
    raise UnicodeDecodeError('utf-8', raw, 0, min(1, len(raw)),
                             f'既不是 UTF-8 也不是 GBK（{"；".join(errors)}）：{path}')


def split_lines(text: str) -> list:
    """按行切（**去掉 CRLF 的 `\\r`**，`\\r\\n` 与 `\\n` 都算一行）。"""
    return text.replace('\r\n', '\n').replace('\r', '\n').split('\n')


# ===================== 角色表 =====================

def _norm_key(name: str) -> str:
    """
    归一化键：小写 + 去掉下划线/空格/**角括号与引号**。

    于是 `JC`/`jc`/`J_C` 是同一个；**显示名**带书名号/角括号也能配上
    （作者写 `「叶怀瑾」"…"` 时，token 里就带着角括号）。

    ⚠ 归一化**会丢大小写** —— 所以它只用来"找候选"，**不许单独用来定论**：
    `Ljk` 与 `ljk` 归一后是同一个键，但它们**可能是两个不同的人**。
    定论那一步在 `lookup()` 里（精确名优先 → 歧义必须报）。
    """
    s = re.sub(r'[\s_]+', '', str(name or ''))
    s = re.sub(r'[「」『』"\'“”]', '', s)
    return s.lower()


@dataclass
class Conflict:
    """撞车的一条记录（**不许静默取第一个**）。"""
    kind: str          # 'case' | 'cross'
    key: str           # 归一化后的键
    existing: str      # 已占用的角色变量
    new: str           # 后来的角色变量
    rank: str = ''     # 涉及的层（variable/display/alias）

    def message(self) -> str:
        if self.kind == 'case':
            return (f'角色大小写敏感：变量名 "{self.existing}" 与 "{self.new}" '
                    f'只差大小写（都归类到 "{self.key}"）。归一化会让两者指向同一个人 —— '
                    f'若他们**是两个人**，请改成不靠大小写区分的变量名'
                    f'（例如 ljk_li / ljk_lu）；若本就是一个人，请合并成一行。')
        if self.rank == 'alias':
            return (f'角色冲突："{self.key}" 同时指向 {self.existing} 与 {self.new}'
                    f'（请到 characters.xlsx 的「别名」列里改掉一个）')
        return (f'角色冲突："{self.key}" 同时指向 {self.existing} 与 {self.new}'
                f'（请到 characters.xlsx 的「显示名」列里改掉一个）')


@dataclass
class Ambiguity:
    """归一化撞车后的**不确定**答案（`lookup` 的第三种返回）。"""
    key: str
    candidates: list          # 候选角色变量（**确定性排序**）
    kind: str = 'case'        # 'case'（只差大小写）| 'cross'（不同写法撞在一起）

    def message(self) -> str:
        who = '、'.join(self.candidates)
        if self.kind == 'case':
            return (f'"{self.key}" 无法确定是谁：{who} 只差大小写，'
                    f'请写**完整且大小写正确**的变量名（或到角色表加别名）')
        return (f'"{self.key}" 无法确定是谁：{who} 撞在同一个写法上，'
                f'请到 characters.xlsx 的「别名」列改掉一个')


class CharacterMap:
    r"""
    角色标识 → 角色变量 的映射（**唯一真源来自角色表**）。

    `rows` 是 `AssetRegistry.characters` 那种形状：
    `{变量: {'ID','Name','Aliases','ImageTag',…}}`。

    ## 四层匹配（能自动的绝不让人配；**猜不准的一律不猜**）

    | 层 | 判据 | 例 |
    |---|---|---|
    | L0 精确 | token == 角色变量（**大小写也必须一样**） | `mm`/`Ljk` |
    | L1 归一 | 大小写/下划线/空格/角括号/引号归一 | `Mm`/`MM`/`m_m`/`「叶怀瑾」` |
    | L2 显示名 | token == `Name` 列（归一后比较；冲突时**显示名压过别人的别名**） | `妈妈`（变量是 `mm`） |
    | L3 别名 | token ∈ `Aliases` 列（**多值**：`MOM\|母亲\|老妈`） | `MOM` |
    | L4 | 都不中 → **报出来**（`lookup` 返回空） | `LINGDAO` |

    ⚠ 为什么不猜：`LINGDAO ↔ ld` 之间**没有算法关系**（`ld` 既不是拼音也不是可推的缩写），
    猜错就是**改剧情**（游戏里说话人换了人）。

    ## ⚠ 归一化的代价：`Ljk` 与 `ljk` 可能是两个人（2026-10-10 探针实测）

    归一化把小写当"同一个人"，这在**大部分时候**对（`JC`/`jc`/`Jc`），
    但表里同时有 `ljk` 和 `Ljk` 两个变量时，它会把两个人**静默合并**：
    实测旧版把 `Ljk"我是陆嘉珂"` 写成 `ljk "我是陆嘉珂"` —— 说话人换人了，
    产物里**看不出**哪一行错。现在的处置分三种，判据在
    `tests/test_txt_import_adversarial.py` 与 `tests/test_character_aliases.py`：

      1. **只差大小写的两个变量** → 该归一键进 `_shadowed`：
         **精确写法各归各家**（`ljk`→`ljk`、`Ljk`→`Ljk`），
         任何不规范写法（`LJK`/`L_J_K`）→ `Ambiguity`，产物保留原文 + 报出来；
      2. **同一层两个人抢同一写法**（共用一个别名）→ 记 `Conflict`；
      3. **跨层撞车**（别名撞别人的显示名）→ 记 `Conflict`（既有契约要报），
         但归一命中按**强层赢**（显示名 > 别名）—— 这条**刻意保留**：
         它保证 `lookup('乙')` 拿到的是"正名那个角色"，而不是"先登记的那个"。
    """

    #: 层优先级（越小越强）：变量 > 显示名 > 别名
    LAYER_RANK = {'variable': 0, 'display': 1, 'alias': 2}

    def __init__(self, rows=None):
        #: 归一化键 → 角色变量（**归一命中的结果**，按"强层赢"定）
        self.by_key = {}
        #: 归一化键 → ('variable'|'display'|'alias')，与 `by_key` 配对
        self.layer_of = {}
        self.display = {}
        #: 层 → 归一化键 → 角色变量：**同层内**抢同一个写法才算撞车
        #: （跨层撞车按"强层赢"定，那是既有契约；同层才是真歧义）
        self._layer_owner = {}
        #: 归一化键 → {角色变量: 它命中该键的**最强层**}
        self._by_key_layer = {}
        #: 归一化键 → {角色变量: 层}：**只差大小写**的多个变量（"可能是两个人"）
        #: 这些键**不指向任何一个**，但精确写法仍必须解析到它自己的那个角色
        self._shadowed = {}
        #: 撞车清单：`[Conflict]` —— **不许静默取第一个**
        self.conflicts = []
        for var, row in (rows or {}).items():
            row = row or {}
            var = str(row.get('ID') or var or '').strip()
            if not var:
                continue
            # L0/L1：变量名本身（`JC`/`jc` 归一到同一个键；`J_C` 同键见 _norm_key）
            self._add(_norm_key(var), var, 'variable')
            name = str(row.get('Name') or '').strip()
            if name:
                self.display[var] = name
                self._add(_norm_key(name), var, 'display')      # L2
            for alias in split_aliases(row.get('Aliases')):
                self._add(_norm_key(alias), var, 'alias')        # L3

    def _add(self, key: str, var: str, layer: str) -> None:
        """
        登记一次归一命中。**显式优先级**（变量 > 显示名 > 别名），
        不靠登记顺序 —— 靠顺序的话"同一个人先以别名出现"会把后来的显示名顶掉。

        三种撞车的处置**刻意不同**：

          · **同层、只差大小写**（`ljk` vs `Ljk`）→ 这是"可能是两个人"的
            最危险形态：两个都记进 `_shadowed`，norm 键**不指向任何一个**
            （`lookup` 对任何非精确写法返回 `Ambiguity` —— 绝不静默改判）；
          · **同层、其他**（两个人共用一个别名）→ 记 `Conflict`，
            归一命中保留先来的那个（真撞车，必须人来改）；
          · **跨层**（别名撞别人的显示名）→ 记 `Conflict`（既有契约要报），
            但归一命中按**强层赢**（显示名压过别人的别名）。
        """
        if not key:
            return
        cands = self._by_key_layer.setdefault(key, {})
        prev = cands.get(var)
        if prev is None or self.LAYER_RANK[layer] < self.LAYER_RANK[prev]:
            cands[var] = layer
        # ⚠ 撞车的两条判据**都要**：
        #   ① **跨层**（别名撞别人的显示名）—— 既有契约要求"报出来"，
        #      否则用户看到的是"正名输给了别人的别名"，比报错更费解；
        #      但归一命中仍按强层赢（`lookup('乙')` → 显示名那个 `b`）；
        #   ② **同层**（两个变量抢同一个写法）—— 真撞车，报出来；
        #      只差大小写的还要额外进 `_shadowed`（那是"可能是两个人"）。
        rivals = [v for v in cands if v != var]
        if rivals:
            other = min(rivals, key=lambda v: (self.LAYER_RANK[cands[v]], v))
            self._record(self._conflict(key, other, var, layer))
        owners = self._layer_owner.setdefault(layer, {})
        cur = owners.get(key)
        if cur is None:
            owners[key] = var
        elif cur != var and cur not in rivals:
            self._record(self._conflict(key, cur, var, layer))
        # 只差大小写且都在变量层 → 该键**不出唯一赢家**："精确写谁就是谁"，
        # 但任何非精确写法（`LJK`/`L_J_K`）一律返回歧义。
        if (layer == 'variable' and self.layer_of.get(key) == 'variable'
                and len(rivals) == 1 and rivals[0].lower() == var.lower()):
            self._shadowed.setdefault(key, {})[var] = 'variable'
            self._shadowed[key][rivals[0]] = 'variable'
        # 归一命中的结果：候选里选**最强**（并列按变量名排序，保证确定性）。
        # ⚠ 大小写撞车的键虽然没有唯一赢家，`by_key` 里仍留一个"最强者"
        # 给 `candidates()` 等展示用 —— `lookup` 会先看 `_shadowed`，
        # 所以它**绝不会**用这个值去映射。
        best = min(cands, key=lambda v: (self.LAYER_RANK[cands[v]], v))
        self.by_key[key] = best
        self.layer_of[key] = cands[best]

    @staticmethod
    def _conflict(key: str, existing: str, new: str, layer: str) -> Conflict:
        """
        造一条撞车记录。**只差大小写**（`ljk` vs `Ljk`）判成 `case`：

        它不是"配置写错了"，而是"**工具不敢替你决定**"——
        归一化会把两个人当成一个人，所以必须由人来说清是两个人还是一个人。
        """
        is_case = existing.lower() == new.lower()
        return Conflict(kind='case' if is_case else 'cross', key=key,
                        existing=existing, new=new, rank=layer)

    def _record(self, c: Conflict) -> None:
        """撞车去重（同一对角色只报一次）。"""
        for old in self.conflicts:
            if (old.key, old.existing, old.new, old.kind) == (
                    c.key, c.existing, c.new, c.kind):
                return
        self.conflicts.append(c)

    def lookup(self, token: str):
        """
        返回 `(变量, 命中层)`；没命中返回 `(None, '')`；
        **归一化撞车**返回 `(Ambiguity, '')` —— 调用方必须当"认不出"处理。

        判定顺序（顺序就是优先级）：

          1. **精确写法**：token 逐字等于某个角色变量（连大小写）→ 就是它。
             这一条专治 `ljk` / `Ljk` 是两个人：作者写谁就是谁；
          2. **归一化**：`LJK`/`JＣ`/`「叶怀瑾」` → 归一键；若该键被**多个变量**
             占用（只差大小写那种）→ `Ambiguity`（**不许挑一个**）；
          3. 都没有 → `(None, '')`，调用方报未映射。

        ⚠ 为什么第 1 条只认**变量名本身**（不认显示名/别名）：让"精确写法"压过
        归一化会把 `lookup('乙')` 变成"先登记的那个角色"，破坏契约里的
        "**强层赢**"（`tests/test_character_aliases.py` 当场抓住）。
        """
        tok = str(token or '').strip()
        if not tok:
            return None, ''
        key = _norm_key(tok)
        if not key:
            return None, ''
        shadow = self._shadowed.get(key)
        if shadow:
            if tok in shadow:                      # 精确写法 → 它自己的角色
                return tok, 'variable'
            return Ambiguity(key=key, candidates=sorted(shadow), kind='case'), ''
        var = self.by_key.get(key)
        return (var, self.layer_of.get(key, '')) if var else (None, '')

    def _exact(self, token: str):
        """
        精确命中（含大小写的**变量名本身**）—— 给判据/调试用。

        ⚠ 这不是"另一条判定路径"，而是 `lookup` 里那条精确规则的可读版本：
        只有**大小写撞车**的键才有"精确写法"可言（普通角色让归一化处理，
        这样才不会破坏"强层赢"）。
        """
        tok = str(token or '').strip()
        if not tok:
            return None
        return tok if tok in self._shadowed.get(_norm_key(tok), {}) else None

    def conflict_warnings(self) -> list:
        """撞车 → 给人看的告警（同一个写法指到了两个角色）。"""
        return [c.message() for c in self.conflicts]

    def case_collision_keys(self) -> set:
        """只差大小写、都在变量层的键（**这些键不许下结论**）。"""
        return set(self._shadowed)

    def candidates(self, token: str) -> list:
        """
        未映射时给**候选**（让人挑，而不是我们猜）。

        只按"前缀"粗略提示；列不出就返回空 —— 空也不算错，
        S3 的界面会把整张角色表摊给用户挑。

        ⚠ 单字符键（真机里就有 `l`：「蓝」）**不许参与**：`LINGDAO` 会命中它
        （`l` 是 `lingdao` 的前缀），于是提示"相近的角色：l" —— 这不是提示，
        而是**误导**（探针实测）。短 token 只认精确命中。
        """
        key = _norm_key(token)
        if not key:
            return []
        out = []
        for k, var in self.by_key.items():
            if k == key or len(k) < 2 or len(key) < 2:
                continue
            if k.startswith(key) or (len(key) >= 3 and key.startswith(k)):
                if var not in out:
                    out.append(var)
        # 撞车且没有赢家的键（`_shadowed`）也要能当候选给出（否则用户看不到可选值）
        for k, shadow in self._shadowed.items():
            if k == key or len(k) < 2 or len(key) < 2:
                continue
            if k.startswith(key) or (len(key) >= 3 and key.startswith(k)):
                for var in shadow:
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
    #: 非空 = 这个 token 归一后撞到多个变量（`Ljk` vs `ljk`）→ **不许映射**
    ambiguity: object = None


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
    判定"这一行是不是角色台词"，返回 `(token, var, 台词, 命中方式, 歧义)`。

    三条判据见模块头（表命中 / 全大写缩写 / 其余当旁白）。
    `歧义` 非空 = token 归一后撞到了**多个变量**（`Ljk` vs `ljk`）——
    这时 `var` 是空字符串，调用方**不许**映射（绝不当场挑一个）。
    """
    # ① 冒号形式：`JC：台词` / `JC: 台词`（**只认第一处冒号**）
    m = _COLON_RE.search(body)
    if m:
        token = body[:m.start()].strip()
        rest = body[m.end():].strip()
        if token and rest and _looks_like_speaker(token, chars):
            var, how, amb = _resolve(token, chars)
            return token, var, rest.strip('“”"'), how, amb

    # ② 引号形式：`JC"…"` / `A“…”`（引号紧跟在 token 之后）
    for rx in (re.compile(r'^\s*([^\s“”":：]{1,16})\s*[“"](.*?)[”"]\s*$'),
               re.compile(r'^\s*([^\s“”":：]{1,16})\s*[“"](.*)$')):
        m = rx.match(body)
        if m:
            token = m.group(1).strip()
            text = m.group(2).strip().strip('“”"')
            if token and text and _looks_like_speaker(token, chars):
                var, how, amb = _resolve(token, chars)
                return token, var, text, how, amb
    return '', '', '', '', None


def _resolve(token: str, chars: CharacterMap):
    """`lookup` 的包装：把 `Ambiguity` 拆出来（调用方据此**不映射**）。"""
    var, how = chars.lookup(token)
    if isinstance(var, Ambiguity):
        return '', '', var
    return var or '', how, None


def _looks_like_speaker(token: str, chars: CharacterMap) -> bool:
    """
    说话人形状判定（见模块头第 3 条：宁可当旁白，也不要把旁白当角色）。

    ⚠ 第 2 条（全大写缩写）**收紧了**（2026-10-10 探针）：它保留原 token 进产物，
    而产物里没有 `define` → Ren'Py 运行期是**未定义变量**，玩家到那句就崩
    （实测 `OK"好的"` → `OK "好的"`）。现在的条件：

      · 角色表非空（说明这确实是本项目，不是随便一段文字 —— 否则整章都建在
        未定义变量上）；
      · 形状是**短缩写**：1–8 个字符、`A–Z0–9`、**不含下划线**
        —— 真剧本里就是 `MOM`/`TJF`/`A`/`B`；`L_J_K`（下划线）当旁白；
      · （认了之后**必须报未映射**，见 `convert_text` 的告警。）

    ⚠ **已知误判边界**（不装作没有）：只看形状分不出 `USA`/`OK` 是"角色缩写"
    还是"英文单词 + 引号"。这里**选择认**（保留作者的写法 + 报未映射），
    代价是英文全大写词会被当成说话人 —— 但它会出现在未映射清单里，
    而三个字母以上的英文词本来就不该出现在中文剧本的台词里。
    反过来（当旁白）会**丢掉说话人**且不留痕迹，那更贵。
    """
    if not token or len(token) > 16:
        return False
    if any(ch in token for ch in '，。！？、；…—-—'):     # 带标点的一定不是
        return False
    if chars.lookup(token)[0]:                             # 表里有（含歧义）→ 认
        return True
    if chars.by_key and _ABBREV_TOKEN_RE.match(token):
        return True                                        # 真剧本缩写 → 认（会报未映射）
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
    token, var, text, _how, amb = _detect_speaker(body_stripped, chars)
    if token and text:
        return TextLine(no=no, raw=raw, kind=SAY, comments=comments,
                        speaker_token=token, speaker_var=var, text=text,
                        ambiguity=amb)
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
    unmapped: dict = field(default_factory=dict)       # token → 出现次数（表里没有）
    suggested_defines: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    dropped: list = field(default_factory=list)        # 被丢掉的原文行（应当为空）
    conflicts: list = field(default_factory=list)      # 撞车（不许静默取第一个）
    #: token → [候选变量…]：归一化撞车（`Ljk` vs `ljk`）—— **不许映射**
    ambiguous: dict = field(default_factory=dict)
    #: 这份输入看起来像**已经写好的 .rpy**（不是纯文字）—— 强提示
    looks_like_script: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """有"丢内容/认不出"就不算干净通过（调用方据此决定要不要拦）。"""
        return not self.warnings


def _looks_like_renpy_code(text: str) -> list:
    """
    这份文本看着像**已经写好的 `.rpy`** 吗？（探针实测：把一整段 Ren'Py 代码
    粘进来，产物会把每一行变成**旁白字符串**，零告警 —— `menu:`/`jump`/
    `show lyj happy` 全变成台词，作者可能到跑起来才发现"我的选项呢"。）

    只在"多行命中"时才报（纯文字里偶尔出现 `show` 这种词不算），
    并且**只提示、不改产物**（作者确实可能就是想演示代码）。
    """
    hits = []
    for no, raw in enumerate(split_lines(text), 1):
        s = raw.strip()
        if not s:
            continue
        for rx, name in _CODE_LIKE_RES:
            if rx.match(s):
                hits.append((no, name, s[:50]))
                break
    return hits


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
    ambiguous = {}
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
            if tl.ambiguity is not None:
                # 归一化撞车（`Ljk` vs `ljk`）：**不映射**，保留原文 token，
                # 让它显式地坏在明处（E102 校验 / 人眼都能看见），
                # 绝不"当场挑一个" —— 挑错就是静默改剧情。
                ambiguous.setdefault(tl.speaker_token, sorted(
                    tl.ambiguity.candidates))
                out.append(f'{indent}{tl.speaker_token} "{_escape(tl.text)}"')
                continue
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
    for token, cands in sorted(ambiguous.items()):
        warnings.append(
            f'角色 "{token}" 无法确定是谁：{"、".join(cands)} '
            f'归一到同一个写法 —— 产物里已保留原文 "{token}"（**没有**替你挑一个），'
            f'请把 txt 里写成完整且大小写正确的变量名，或到 characters.xlsx 加别名')
    for token, n in sorted(unmapped.items()):
        hint = chars.candidates(token)
        extra = f'；相近的角色：{"、".join(hint)}' if hint else ''
        warnings.append(
            f'角色 "{token}" 不在角色表里（{n} 处）—— 在 characters.xlsx 的'
            f'「别名」列给它补一个别名（可写多个，用 | 分隔），'
            f'或把 txt 里改成表里的变量名/显示名{extra}')
        warnings.append(
            f'⚠ "{token}" 若直接落盘，Ren\'Py 会在这一句因**未定义变量**崩掉：'
            f'先把它加进角色表（或补 {chars.suggest_define(token)}）')
    # 撞车：同一个写法指到两个角色 —— **报出来**，绝不静默取第一个
    warnings.extend(chars.conflict_warnings())

    code_like = _looks_like_renpy_code(text)
    if len(code_like) >= 3:
        sample = '；'.join(f'第 {no} 行像 {name}' for no, name, _s in code_like[:5])
        warnings.append(
            f'⚠ 这份输入看起来是**已经写好的 Ren\'Py 代码**（{len(code_like)} 行命中：'
            f'{sample}）—— 逐行转成旁白会把它压成台词；'
            f'若你要的是"导入现成脚本"，请直接用「导入章节 .rpy」')

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
        warnings=warnings, dropped=dropped, conflicts=list(chars.conflicts),
        ambiguous=ambiguous, looks_like_script=code_like)


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
