"""
storygraph.core.authority
=========================
**真源与优先级的唯一层序表**（契约：`docs/真源与优先级_设计决策_2026-10-31.md`
§4.2 四域 / §4.3 层序常量 / §4.5 准入四问）。

为什么要有这个文件（2026-10-31，用户问"两个近乎克隆的项目为什么给出
不一样的 outfit 代号"）：

    在那之前，优先级散在**四种互不相认的写法**里 —— 目录远近（
    `mapping_sources`）、文件格式（同目录 xlsx > json）、调用顺序
    （`Vocabulary.plus()` 后来者优先）、字典覆盖（`_by_name`）。
    每一处单看都有理由，合起来就是"同一个冲突会有不同结论，而且没人报"。
    用户的实测把这笔账摊开了：同一章加了一个 `.generated.rpy` 副本，
    服装代号就从"留空"变成 "B" —— 结论取决于**扫到了几个文件**，
    而不是项目声明了什么。

CSS 的教训在这里最贵（`!important` 军备竞赛）：

  · 不要给每条数据算权重（specificity），要**显式声明层**（`@layer`）；
  · *"Specificity bugs overwhelmingly arise from mixing strategies within a
    single codebase without a clear priority model."*

所以本模块只做三件事（**不许在这里写业务判断**）：

  1. `AUTHORITY`：一张短的、固定的层序表（数字小的赢）；
  2. `DOMAINS`：四个域，各有一个所有者 —— 真源按**域**分，不按层分；
  3. `IDENTITY_ORDER` / `SOURCE_LAYER`：把"这张图是谁"的判定顺序和
     `Identity.source` 的取值钉在同一张表上。

硬规矩
------
  · 任何"谁覆盖谁"**必须**查这张表，不许按调用顺序、不许按文件远近
    临时排一个（那是"混合策略"）；
  · 覆盖前先问能不能**合并**（同一事实只有一个所有者），再问能不能
    **单写者**，最后才允许覆盖 —— 顺序见 `docs/真源与优先级_设计决策_2026-10-31.md`
    §4.1（① 所有者 ② 投影 ③ 单写者 ④ 才允许覆盖）；
  · 新资产先回答 §4.5 的四问，再决定它是不是**这个域的新一层**；
  · 层序常量改动 = 行为改动，必须同时改契约与 `tests/test_authority_layers.py`。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
#  一、层序：权威从高到低（数字小的赢）
# ---------------------------------------------------------------------------

#: 层 → 权威等级（0 最高）。**任何"谁覆盖谁"都查这里。**
#:
#: 每一层带一句"它凭什么"——没有这句话的层不许加进来（§4.5 第 4 问）。
AUTHORITY = {
    # 引擎说这行非法，任何映射都不能把它变合法：这是**天花板**，不是地板。
    'engine': 0,
    # 映射里**点名某个资产 ID / 相对路径**的那一条（人指着某一张图说"它叫这个"）。
    # 为什么它在 `declared` **之上**：这是最高级别的人为意图（"这一张，
    # 我说了算"），而声明是项目在自己那边写的名字；纠正可以改声明，但
    # **只对点名的这一张生效**，且必须留痕（§六 6.1 drop-in 模型 / §七.1）。
    'mapping-exact': 1,
    # `.rpy` 里的字面量声明（`layeredimage` / `image` / `define`）：
    # 项目自己说"这张图叫这个" —— 除人明确纠正之外，它就是事实。
    'declared': 2,
    # 映射的 glob 规则（"这一类大概都这样"）。它**猜**的成分最大，
    # 所以只补空缺：不许改项目声明过的事实，也不许改精确覆盖
    # （§六 6.2：个体显式 > 批量规则；`.gitignore` 那条也要逐个 `-f`）。
    'mapping-rule': 3,
    # 资源表的 Tag / Attributes / Value 列：人维护过的登记，但可能滞后于脚本。
    'registry': 4,
    # 路径目录段 / 文件名：纯推断，只在前四层都没有结论时说话。
    'inferred': 5,
    # 认不出（不猜）。它不是"最低权威"，它是"**没有结论**"。
    'unknown': 6,
}

#: "这张图是谁"的判定顺序（= `asset_identity.resolve_image` 的分支顺序）。
#:
#: 2026-10-31 改过一次：`declared` 提到 `mapping-rule` **之前**。
#: 原因：规则是"这一类"，声明是"这一个"；批量规则不许改已经声明的事实
#: （用户实测：一条 `game/**` 规则把项目自定义的图像名整片改掉）。
IDENTITY_ORDER = ('mapping-exact', 'declared', 'mapping-rule',
                  'registry', 'inferred', 'unknown')

#: `Identity.source` 的取值 → 它属于哪一层。
#:
#: 界面上"这条结论是谁给的"必须能翻回层号（§P0-3），否则"覆盖了谁"
#: 只能靠读代码猜。
SOURCE_LAYER = {
    'declared': 'declared',
    'mapping': 'mapping-exact',
    'mapping-rule': 'mapping-rule',
    'tag-column': 'registry',
    'folder': 'inferred',
    'stem': 'inferred',
    'unknown': 'unknown',
}

# ---------------------------------------------------------------------------
#  二、四个域：真源按"域"分，不按"层"分
# ---------------------------------------------------------------------------

#: 域 → {拥有的事实, 所有者, 其余角色的定位}。
#:
#: 这一段是"**表是编辑面，不是真源**"这句话的落点：`.rpy` 拥有内容域，
#: 表格只是打字的地方（像 git 工作区不是真源，但你确实在那里改代码）。
DOMAINS = {
    'A': {
        'name': '内容',
        'owns': '说什么话、谁出场、怎么演',
        'owner': '.rpy',
        'others': '剧本表 = 编辑面（不是真源）',
    },
    'B': {
        'name': '身份',
        'owns': '这张图是谁、这首歌叫什么',
        'owner': '引擎事实 / 项目声明',
        'others': '映射 = 纠正层',
    },
    'C': {
        'name': '形状',
        'owns': '这一行怎么写才合法',
        'owner': '引擎 lint / CDS',
        'others': 'rules.json = 声明层',
    },
    'D': {
        'name': '命名',
        'owns': '屏幕上显示成什么',
        'owner': '映射',
        'others': '词汇表 = 投影',
    },
}

# ---------------------------------------------------------------------------
#  三、小工具（只读，不许在这里加"某项目特例"）
# ---------------------------------------------------------------------------


def rank(layer: str) -> int:
    """
    层号（越小越权威）。不认识的层 → `unknown` 的层号（最弱）。

    为什么不是抛异常：这是**报告用**的读数，一条写错的 source 不该让
    整个界面炸掉；但它也不许静默赢过别人 —— 所以落到最弱的 `unknown`。
    """
    return AUTHORITY.get(str(layer or '').strip(), AUTHORITY['unknown'])


def wins(a: str, b: str) -> bool:
    """`a` 是否压过 `b`（同层不算压过：同层冲突要报出来，不是二选一）。"""
    return rank(a) < rank(b)


def layer_of(source: str) -> str:
    """`Identity.source` → 层名（认不出落 `unknown`）。"""
    return SOURCE_LAYER.get(str(source or '').strip(), 'unknown')


def covers_others(layer: str) -> bool:
    """
    这一层赢的时候**允不允许**压过别的层（§六 6.2 的判据）。

    只有 `engine` / `declared` / `mapping-exact` 是"说了就算"；
    `mapping-rule` / `registry` / `inferred` 只补空缺 —— 它们赢，
    是因为**别处没说话**，不是因为它更权威。
    """
    return str(layer or '').strip() in ('engine', 'declared', 'mapping-exact')


# ---------------------------------------------------------------------------
#  四、覆盖留痕：precedence 的另一半是 provenance（§六 6.4 / §七.1 / P0-3）
# ---------------------------------------------------------------------------
#
# 行业那句话（§六 6.4 引用）：
#
#   "Precedence answers which value wins. Governance also needs to answer
#    where that value came from."
#
# `Identity.source` / `Term.source` 只回答**赢家是谁**；回答不了
# "被谁盖掉的、原来是什么"。用户 2026-10-31 指的 P0-3 就是这一半：
#
#     `lang hai` 的显示名「嗨」来自映射，覆盖了资源表 Tag 列的同名条目。
#
# 规矩（§七.1）：
#   · **append-only**：只追加，不回改、不删除（运行期可重建，所以不落盘）；
#   · **不写进 `.rpy`**：`.rpy` 是用户的文件，往里面写工具注释就是往唯一真源掺沙子；
#   · **原值变了要提醒复核**：这是 systemd drop-in 那个"钉死旧行为"的老坑 ——
#     `origin` 就是给这条留的位置（能说出文件/行就写，说不出就空着，别编）。


@dataclass(frozen=True)
class Override:
    """
    一条"谁覆盖了谁"的记录（append-only）。

    字段都刻意是**人话能读的值**（不是层号/枚举）：这条记录最终要变成界面上
    的一句话，而不是让读者再去查一次表。

      · `domain`：哪个域（`naming` 命名 / `identity` 身份 / `content` 内容）；
      · `key`：说的是哪一条（人话，如 `transform i11`、`bg bg9_5`）；
      · `winner_layer` / `loser_layer`：层名（`mapping-exact` / `registry` …）；
      · `winner_value` / `loser_value`：两边的**显示值**（不是内部 id）；
      · `origin`：能说出"哪份文件哪一行"就写，说不出留空（**不编**）；
      · `match`：机器用来对齐的键（如 `Term.key`），只做过滤，不显示。
    """

    domain: str = ''
    key: str = ''
    winner_layer: str = ''
    loser_layer: str = ''
    winner_value: str = ''
    loser_value: str = ''
    origin: str = ''
    match: tuple = ()

    def sentence(self) -> str:
        """统一成一句话（界面 tooltip / 报告共用；不许各写一遍）。"""
        text = (f'{self.key}：{self._layer_cn(self.winner_layer)} '
                f'「{self.winner_value}」覆盖了 '
                f'{self._layer_cn(self.loser_layer)}「{self.loser_value}」')
        if self.origin:
            text += f'（{self.origin}）'
        return text

    @staticmethod
    def _layer_cn(layer: str) -> str:
        """层名 → 人话（查不到就原样 —— 宁可露出内部名，也不猜一个假名字）。"""
        return LAYER_CN.get(str(layer or '').strip(), str(layer or ''))


#: 层名 → 人话（只用于显示；`AUTHORITY` 才是判定用的真值）。
LAYER_CN = {
    'engine': '引擎',
    'mapping-exact': '映射的精确覆盖',
    'declared': '项目声明',
    'mapping-rule': '映射的通配规则',
    'registry': '资源表',
    'inferred': '路径推断',
    'unknown': '没认出来',
}


def describe(overrides) -> list:
    """一列 `Override` → 一列人话（保持顺序；空/坏条目跳过）。"""
    out = []
    for item in overrides or ():
        try:
            text = item.sentence()
        except AttributeError:                 # 不是 Override 的东西直接跳过
            continue
        if text:
            out.append(text)
    return out


__all__ = ['AUTHORITY', 'DOMAINS', 'IDENTITY_ORDER', 'LAYER_CN', 'Override',
           'SOURCE_LAYER', 'covers_others', 'describe', 'layer_of', 'rank',
           'wins']
