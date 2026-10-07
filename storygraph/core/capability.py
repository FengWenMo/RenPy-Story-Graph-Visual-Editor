"""
`storygraph.core.capability`
============================
**工具能力元模型**（Capability Meta-Model，2026-10-07）。

为什么要单独一层
----------------
在此之前，"工具能对一句话做什么"这件事**散在分支里**：

  · 它能不能被结构化编辑 —— 看 `_parse_statement` 有没有对应分支；
  · 它会不会丢 —— 看 `_stmt_unmodeled` 走哪条路；
  · 它会不会被报警 —— 看 `generation_guard` 排进了哪个桶；
  · 项目整体的"工具跟得上吗" —— 只有 `project_conventions.unknown_statements`
    一个函数，而它**没有任何生产调用**。

四个视角（保真 / 可编辑 / 可教 / 覆盖率）看起来是四件事，其实是**同一个东西的
四个投影**：模型的"能力等级"。这一层把它显式化，于是：

  · 等级可以在语句之间**移动**（精化 / 退化），路径和条件是可定义、可测的；
  · 报告能回答"我的项目里有多少句子现在是**只读**的、怎么把它们变成可编辑"；
  · 新加一种语句时，改动落在**一处**（这张表），而不是散在分支与死代码里。

五个组成部分
------------
1. **State**（`T0` / `T1a` / `T1b` / `T2` / `T3`）—— 一句话当前的能力等级；
2. **Provenance**（`builtin` / `declared` / `host-registry` / `shape` /
   `unknown`）—— 这个等级**凭什么**成立（谁告诉工具它是什么）；
3. **Transition** —— 等级之间的精化 / 退化规则，以及两条**硬不变量**；
4. **Coverage** —— 项目级的"可编辑覆盖率"（CAP 视角）；
5. **Escape** —— 每个等级"怎么教工具升级"的入口（逃逸舱口视角）。

⚠ 这一层**不改变**四层分级本身，只是给它加上生命周期。行为由调用方决定
（`story.statement_registry` 负责装配、`generation_guard` 负责报警、
`beat_model` 负责列），这里只有**模型与规则**，不碰文件、不碰渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
#  Capability State —— 当前能力等级
# ---------------------------------------------------------------------------

#: **T0 已建模**：工具完全理解它，结构化列全功能编辑、由渲染器生成。
T0 = 'T0'

#: **T0b 已建模·块体逐字**：工具**有内置 IR**（头行结构化、可编辑、可校验），
#: 但**块体还是逐字**的（循环体 `while` / `for`）。
#:
#: ⚠ 为什么不让它直接算 `T0`（评审意见驱动）：T0 的定义是"结构化列**全功能**
#: 编辑"。循环体做不到 —— `Beat` 是扁平的一维语句流，表达不了"这一段会
#: 重复执行"。把它记成 T0 就是**拿"接住了"冒充"完全理解了"**，
#: 而那正是这一层要防的事（`KNOWN_STATEMENT_HEADS` 的注释写着
#: "宁少不宁多：多写 → 假装懂（危险）"）。
T0B = 'T0b'

#: **T1a 已声明 + 有投影**：项目声明过它，而且给了参数形状/说明
#: → 参数成为**可编辑的格子**（M4 的通用行）。
T1A = 'T1a'

#: **T1b 已声明、只逐字透传**：项目声明过它（工具知道它叫什么、属于谁），
#: 但没给参数形状 → 整块原文可编辑、逐字写回。
T1B = 'T1b'

#: **T2 已识别形状**：工具认识"这是什么"（标准流程块 / 内置动词），
#: 但还没有结构化建模。内容逐字透传。
T2 = 'T2'

#: **T3 未识别**：工具完全不知道它是什么。内容逐字透传 + 报告要催。
T3 = 'T3'

#: 等级从"强"到"弱"（`Transition.kind` 靠这个次序判精化/退化）。
LEVELS = (T0, T0B, T1A, T1B, T2, T3)

#: 可编辑性：工具**能替你改**的等级（T0 全功能；T0b 头行可改；
#: T1a 参数成列；T1b 整块原文可改）。
EDITABLE_LEVELS = frozenset({T0, T0B, T1A, T1B})

#: 逐字透传：产物里的字节**由原文决定**，不由渲染器决定。
VERBATIM_LEVELS = frozenset({T0B, T1B, T2, T3})

#: **只读**：工具既不替你编辑、也不能重排 —— 只展示 + 逐字写回。
#:
#: ⚠ 这三份**不是划分关系**，`T1b` **同时**在"可编辑"与"逐字透传"里 ——
#: 那不是笔误，正是它的定义：**项目声明过（所以工具知道它的身份、能替你
#: 保存与定位），但没给参数形状（所以只能整块原文改、逐字写回）**。
#: 想划分就用这个：`READONLY = VERBATIM - EDITABLE`。
READONLY_LEVELS = VERBATIM_LEVELS - EDITABLE_LEVELS

#: 等级的中文名（报告用；别在别处再写一份）
LEVEL_NAMES = {
    T0: '已建模',
    T0B: '已建模·块体逐字',
    T1A: '已声明·参数可编辑',
    T1B: '已声明·逐字透传',
    T2: '已识别形状',
    T3: '未识别',
}

# ---------------------------------------------------------------------------
#  Capability Provenance —— 这个等级凭什么成立
# ---------------------------------------------------------------------------

#: 工具自己的语法（内置分支）—— 不是"被告知"，而是"本来就会"。
PROV_BUILTIN = 'builtin'
#: 项目**声明**（`storygraph.rules.json` 的 `statements` 段）。最强，可覆盖。
PROV_DECLARED = 'declared'
#: **宿主注册表**（`renpy.register_statement`，即 Ren'Py CDS）。
PROV_REGISTRY = 'host-registry'
#: **形状识别**：工具从结构上认出"这是个循环块 / 这是个块语句"。
PROV_SHAPE = 'shape'
#: **不知道**。
PROV_UNKNOWN = 'unknown'

PROVENANCES = (PROV_BUILTIN, PROV_DECLARED, PROV_REGISTRY, PROV_SHAPE,
               PROV_UNKNOWN)

PROVENANCE_NAMES = {
    PROV_BUILTIN: '工具内置语法',
    PROV_DECLARED: '项目清单声明',
    PROV_REGISTRY: '宿主注册（CDS）',
    PROV_SHAPE: '形状识别',
    PROV_UNKNOWN: '未知',
}


# ---------------------------------------------------------------------------
#  Capability —— 一条语句/一类语句的能力快照
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Capability:
    """**一句话（或一类句子）的能力快照**：它是几级、凭什么是这一级。"""

    head: str = ''
    level: str = T3
    provenance: str = PROV_UNKNOWN
    #: 人话：为什么判成这一级（报告里要能说清，别让人猜）
    evidence: str = ''
    #: 这一级**怎么升级**（逃逸舱口，人话）
    escape: str = ''
    #: 出现次数（按"类"聚合时才有意义）
    count: int = 1

    @property
    def level_name(self) -> str:
        return LEVEL_NAMES.get(self.level, self.level)

    @property
    def provenance_name(self) -> str:
        return PROVENANCE_NAMES.get(self.provenance, self.provenance)

    @property
    def editable(self) -> bool:
        return self.level in EDITABLE_LEVELS

    @property
    def verbatim(self) -> bool:
        return self.level in VERBATIM_LEVELS

    def to_dict(self) -> dict:
        return {'head': self.head, 'level': self.level,
                'level_name': self.level_name,
                'provenance': self.provenance,
                'provenance_name': self.provenance_name,
                'editable': self.editable, 'verbatim': self.verbatim,
                'evidence': self.evidence, 'escape': self.escape,
                'count': self.count}


# ---------------------------------------------------------------------------
#  Capability Escape —— 每个等级"怎么教工具升级"
# ---------------------------------------------------------------------------

#: 等级 → 升级入口（**唯一的说法**：报告、体检、文档都从这里取）。
ESCAPES = {
    T3: '在项目根或 `game/` 下写 `storygraph.rules.json`，'
        '在 `statements` 里写上它的名字；'
        '或者让程序用 `renpy.register_statement(name="…")` 声明它。',
    T2: '这是**标准流程块**（`while` / `for` 等）：内容已经安全，'
        '但工具还没有把它建模 —— 这一条是**给工具作者**的待办，'
        '写进 issue 即可，不需要你改脚本。',
    T1B: '给它补上参数形状：在 `storygraph.rules.json` 的对应条目里加 '
         '`columns`（每个参数一列）或 `label`（人话说明），'
         '它就从"整块透传"升级为"参数可编辑"。',
    T1A: '已经是可编辑的通用行了。想让它**更好用**（专用卡片、下拉候选）：'
         '给 `columns` 里的字段加 `choices`。',
    T0: '已经是工具的原生语句，不需要升级。',
    T0B: '工具已经接住了它的**头行**（可编辑、可校验），但**块体还是逐字**的。'
         '要让它也能被结构化编辑，得先给拍模型加作用域 —— '
         '这一条是**给工具作者**的待办，不需要你改脚本。',
}


def escape_for(level: str) -> str:
    """这个等级"怎么教工具升级"（人话）。未知等级返回空串。"""
    return ESCAPES.get(level, '')


# ---------------------------------------------------------------------------
#  Capability Transition —— 精化 / 退化规则
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Transition:
    """一次等级移动：从哪到哪、什么条件、会发生什么。"""

    frm: str
    to: str
    when: str
    effect: str

    @property
    def kind(self) -> str:
        """`refine`（升级）/ `degrade`（降级）。"""
        order = list(LEVELS)
        try:
            return ('refine' if order.index(self.to) < order.index(self.frm)
                    else 'degrade')
        except ValueError:
            return 'unknown'

    def to_dict(self) -> dict:
        return {'from': self.frm, 'to': self.to, 'kind': self.kind,
                'when': self.when, 'effect': self.effect}


#: 允许的移动。**没写在这里的移动就是不支持的** —— 与其让它在分支里
#: 悄悄发生，不如让它无处可去。
TRANSITIONS = (
    Transition(T3, T1B,
               when='在 `storygraph.rules.json` 的 `statements` 里写上它的名字，'
                    '或让程序 `renpy.register_statement` 声明它',
               effect='**立即不再报警**；内容照旧逐字透传（字节不变）'),
    Transition(T3, T1A,
               when='声明它**并**给出 `columns` / `label`',
               effect='参数成为可编辑的格子（字节不变）'),
    Transition(T1B, T1A,
               when='给这条声明补 `columns` / `label`',
               effect='从"整块原文可编辑"升级为"每个参数一格"（字节不变）'),
    Transition(T2, T0B,
               when='工具为这种块接住**头行**（**改工具代码**，不是改项目）'
                    '—— 例：`while` / `for` 的循环块（D2）',
               effect='头行可编辑可校验，块体逐字保真；不再报"给作者的待办"'),
    Transition(T2, T0,
               when='工具新增一条**完全**结构化的解析分支'
                    '（**改工具代码**，不是改项目）',
               effect='成为结构化拍，进入编辑器的主流程'),
    Transition(T0B, T0,
               when='给拍模型加上**作用域**（循环体也能被结构化编辑）',
               effect='从"头行结构化 + 块体逐字"升到完全建模'),
    Transition(T1B, T3,
               when='声明被删掉 / 清单写坏了 / 宿主不再注册它',
               effect='退回未识别 —— **并重新开始报警**（内容照旧不丢）'),
    Transition(T1A, T1B,
               when='`columns` / `label` 被删掉',
               effect='退回整块原文可编辑（内容照旧不丢）'),
)

#: **硬不变量**（这个元模型存在的全部意义）。任何改动都必须同时满足。
INVARIANTS = (
    ('降级不改字节',
     'T1a / T1b / T2 / T3 之间任意移动，产物里的**原文逐字不变** —— '
     '等级只决定"工具能不能替你编辑"，不决定"内容长什么样"。'),
    ('精化不改字节',
     '升级同样不改字节：从 T3 教到 T1a，第一次导出应该**与教之前逐字相同**'
     '（除了少一条告警）。变了就说明"升级"顺手改了别人的脚本。'),
    ('T0 不降级',
     '内置判定优先：声明只在**内置全部未命中之后**参与。'
     '项目不能通过声明把一条 `show` 变成自己的语句。'),
)


def transitions_from(level: str) -> list:
    """从这一级出发允许的移动。"""
    return [t for t in TRANSITIONS if t.frm == level]


def transitions_to(level: str) -> list:
    """能走到这一级的移动。"""
    return [t for t in TRANSITIONS if t.to == level]


# ---------------------------------------------------------------------------
#  Capability State 判定（**纯函数**：四个事实 → 一个等级）
# ---------------------------------------------------------------------------

def state_of(*, modeled: bool = False, modeled_verbatim: bool = False,
             declared: bool = False, projected: bool = False,
             known_head: bool = False) -> str:
    """
    事实 → 能力等级。判定顺序**就是**优先级，别改：

      1. `modeled`          —— 内置且完全结构化 → T0
                               （**项目不能通过声明抢走它**）；
      2. `modeled_verbatim` —— 内置但有块体逐字（循环块）→ T0b；
      3. `declared`         —— 项目声明过 → T1a（有投影）/ T1b（只声明）；
      4. `known_head`       —— 工具只认识形状 → T2；
      5. 都不满足           —— T3。

    `known_head` 在**声明之后**才判：标准流程块也能被项目声明
    （例如想给它一个专用投影），那时它应当升到 T1 而不是停在 T2。
    """
    if modeled:
        return T0
    if modeled_verbatim:
        return T0B
    if declared:
        return T1A if projected else T1B
    if known_head:
        return T2
    return T3


def provenance_of(*, modeled: bool = False, modeled_verbatim: bool = False,
                  declared_source: str = '', known_head: bool = False) -> str:
    """
    `state_of` 的伴生：这个等级**凭什么**。

    `declared_source` 由 `story.statement_registry` 给
    （`'declared'` = 清单 / `'host-registry'` = CDS）。
    """
    if modeled or modeled_verbatim:
        return PROV_BUILTIN
    if declared_source:
        return declared_source
    if known_head:
        return PROV_SHAPE
    return PROV_UNKNOWN


# ---------------------------------------------------------------------------
#  Capability Coverage —— 项目级的可编辑覆盖率（CAP 视角）
# ---------------------------------------------------------------------------

@dataclass
class Coverage:
    """
    "我的项目里，有多少句子工具能替我编辑？"

    这是**给人看的一个数**，也是衡量"工具跟不跟得上项目"的唯一指标：
    覆盖率下降 = 项目里出现了工具跟不上的新结构（不是坏事，是**信号**）。
    """

    counts: dict = field(default_factory=dict)      # level -> 条数
    #: 每类的代表（`[Capability]`，报告列样例用）
    samples: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def editable(self) -> int:
        return sum(n for lv, n in self.counts.items()
                   if lv in EDITABLE_LEVELS)

    @property
    def verbatim(self) -> int:
        return sum(n for lv, n in self.counts.items()
                   if lv in VERBATIM_LEVELS)

    @property
    def readonly(self) -> int:
        """工具**只展示、不改**的条数（= `T2` + `T3`）。

        ⚠ 别用 `verbatim` 当它：`T1b` 也逐字透传，但它是**可编辑**的
        （整块原文人能改）。这两个数回答的是不同的问题。
        """
        return sum(n for lv, n in self.counts.items()
                   if lv in READONLY_LEVELS)

    @property
    def ratio(self) -> float:
        """可编辑覆盖率（0.0 ~ 1.0；没有任何语句时算 1.0）。"""
        return (self.editable / self.total) if self.total else 1.0

    def count_of(self, level: str) -> int:
        return int(self.counts.get(level, 0))

    def add(self, level: str, head: str = '', n: int = 1) -> None:
        self.counts[level] = self.count_of(level) + n

    def to_dict(self) -> dict:
        return {'counts': dict(self.counts), 'total': self.total,
                'editable': self.editable, 'verbatim': self.verbatim,
                'readonly': self.readonly, 'ratio': self.ratio}


def format_capability_report(coverage: Coverage, limit: int = 6) -> str:
    """
    `Coverage` → 人话。**结论在第一条**（本仓库报告的一贯口径）。

    它要回答的是编剧/程序真正会问的那两个问题：
      · "工具有多少是替得了我的？"
      · "剩下那部分我该做什么？"
    """
    if coverage is None or not coverage.total:
        return ('这份脚本里没有需要"判断能力等级"的语句 —— '
                '全部由内置语法覆盖。')

    pct = f'{coverage.ratio * 100:.1f}%'
    head = (f'工具可编辑覆盖率 {pct}'
            f'（{coverage.editable}/{coverage.total} 条）')
    # ⚠ 区分「只读」与「逐字」：`T0b` / `T1b` **也**逐字保真，但它们是
    # **可编辑**的（头行 / 整块原文人能改）。把它们说成"只读"会让人以为
    # 那部分不归自己管，正好相反。
    if coverage.readonly:
        head += (f'；其余 {coverage.readonly} 条**只读**'
                 f'（内容逐字保真、不会丢，见下）')
    else:
        head += '；没有只读语句'
    lines = [head]

    order = [T0, T0B, T1A, T1B, T2, T3]
    for lv in order:
        n = coverage.count_of(lv)
        if not n:
            continue
        lines.append(f'  · {lv} {LEVEL_NAMES[lv]}：{n} 条')

    # **只列"需要动作"的那几级**，并且每条给出**怎么升级**。
    # 把 T0/T1a 也列出来只会淹没重点（它们不需要动作）。
    for lv in (T3, T2, T1B):
        hits = [c for c in coverage.samples if c.level == lv]
        if not hits:
            continue
        lines.append(f'  {lv} 里的代表：'
                     + '、'.join(c.head for c in hits[:limit]))
        lines.append(f'    → {escape_for(lv)}')
    return '\n'.join(lines)


__all__ = [
    'T0', 'T0B', 'T1A', 'T1B', 'T2', 'T3', 'LEVELS', 'LEVEL_NAMES',
    'EDITABLE_LEVELS', 'VERBATIM_LEVELS', 'READONLY_LEVELS',
    'PROV_BUILTIN', 'PROV_DECLARED', 'PROV_REGISTRY', 'PROV_SHAPE',
    'PROV_UNKNOWN', 'PROVENANCES', 'PROVENANCE_NAMES',
    'Capability', 'Transition', 'TRANSITIONS', 'INVARIANTS', 'ESCAPES',
    'Coverage', 'escape_for', 'transitions_from', 'transitions_to',
    'state_of', 'provenance_of', 'format_capability_report',
]
