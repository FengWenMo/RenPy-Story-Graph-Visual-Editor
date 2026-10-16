"""
storygraph.core.vocabulary
==========================
**共享词表的唯一真源**：引擎/表格里那些"约定俗成的名字集合"。

为什么要有这个模块（2026-09-28 收敛）：

"资产类型 → registry 集合属性"这件事曾经有 **5 处**各写一份：

    asset_registry._TYPE_ATTR          （6 项）
    validate._TYPE_ATTR                （6 项，与上面重复）
    dsl.Symbols.has() 里内联的 dict     （6 项，又一份）
    dsl.Symbols._AUDIO_ATTR            （3 项子集）
    collab_store._FIELD_ATTR           （另一件事：Beat 字段名映射，不在此列）

"非角色 tag"则有 **2 份且内容不同**：`dsl.NON_ACTOR_TAGS` 与
`stage_snapshot._NON_ACTOR_TAGS`。同一个画面，编译器与快照可能给出
不同结论——这不是重复，是**分歧**。

约定：这个模块**零项目内依赖**（只放纯数据），谁都可以 import，
不产生环。加新词表请加在这里，不要就地再写一份。
"""
from __future__ import annotations

# ==================================================================
#  资产类型
# ==================================================================

#: 资产类型（脚本与表格里的**单数**名）→ `AssetRegistry` 的集合属性名（复数）。
#: 注意 `voice → voices`、`sound → sounds` 是复数，`music` 是单数（历史命名）。
ASSET_TYPE_ATTR = {
    'character': 'characters',
    'image': 'images',
    'transform': 'transforms',
    'music': 'music',
    'sound': 'sounds',
    'voice': 'voices',
}

#: 音频类资产 → 集合属性名（`ASSET_TYPE_ATTR` 的子集，便于按通道取行）。
AUDIO_TYPE_ATTR = {
    'music': 'music',
    'sound': 'sounds',
    'voice': 'voices',
}


# ==================================================================
#  舞台词汇
# ==================================================================

#: **非角色图层** tag：会被 `show` 出来，但不是"台上站着谁"。
#: 快照靠它把 `show cg cg01` / `show bg rooftop` 挡在 actors 之外
#: （否则快照会显示"台上站着一个 bg"），编译器靠它判断"要不要查角色表"。
#:
#: 2026-09-28 合并了原先两份内容不同的集合（`dsl.NON_ACTOR_TAGS` 与
#: `stage_snapshot._NON_ACTOR_TAGS`），取并集——两边都有的名字保持原语义，
#: 只多出 `background / movie / video / overlay / title / splash` 与 `bg_`，
#: 这些在 Ren'Py 里同样不是立绘 tag。
NON_ACTOR_TAGS = frozenset({
    'bg', 'bg_', 'background', 'cg', 'ui', 'gui', 'black', 'white',
    'logo', 'text', 'screen', 'movie', 'video', 'overlay', 'title',
    'splash',
})

#: `show X ...` 里 X **不是本项目图像资产**的写法（Screen Language / 图层操作）：
#: `show screen msg` / `show layer master` / `show expression F(x)` /
#: `show text "居中"` / `show movie`。
#: 它们不参与"图像引用"判定，也永远不算立绘。
#:
#: ⚠ 与 `SHOW_INTRO_KEYWORDS` 是**两个概念、内容不同、别互相替换**：
#:   那个是"**语法**上替换图像名的关键字"（三个）；这一份更宽 ——
#:   `text` / `movie` 在官方语法里是**预定义 displayable 当图名**（不是引入型），
#:   但它们同样不是本项目的图像资产，去查资源表必然查不到。
#: 使用者：`assets/asset_registry` 的两条引用抽取（Statement 路径 + 正则兜底）。
#: ⚠⚠ **2026-10-15 之前它是"定义了、导出了、却没有任何地方用过"的** ——
#:   于是 `show screen msg_dialogue` 被当成图像名，变成一条假断链
#:   （真机实测 110 条，见 `.workbuddy/memory/2026-10-15.md`）。现在线接上了。
NON_ACTOR_SHOW_TARGETS = frozenset({
    'screen', 'layer', 'expression', 'movie', 'text',
})

#: 非角色图层 tag → 给人看的中文名（快照「本拍变化」列里用）。
#: 缺失的 key 由调用方兜底成"图层"。
LAYER_CN_TAG = {
    'cg': 'CG',
    'ui': '界面',
    'gui': '界面',
    'movie': '影片',
    'video': '影片',
    'logo': '标志',
    'overlay': '叠加层',
    'black': '纯色',
    'white': '纯色',
    'screen': '画面',
    'text': '文字',
}


# ==================================================================
#  内置变换 / 转场 / 位置
# ==================================================================

#: Ren'Py 自带的变换 / 转场 / 位置。脚本里出现这些名字是正常的，
#: 不该把它们当成"引用了不存在的资产"，否则一次扫描就报上百处断裂引用。
RENPY_BUILTIN_TRANSFORMS = frozenset({
    # 转场
    'dissolve', 'fade', 'pixellate', 'move', 'moveinright', 'moveinleft',
    'moveintop', 'moveinbottom', 'moveoutright', 'moveoutleft',
    'moveouttop', 'moveoutbottom', 'ease', 'zoomin', 'zoomout',
    'hpunch', 'vpunch', 'blinds', 'squares', 'wipeleft', 'wiperight',
    'wipeup', 'wipedown', 'slideleft', 'slideright', 'slideup',
    'slidedown', 'slideawayleft', 'slideawayright', 'slideawayup',
    'slideawaydown', 'pushright', 'pushleft', 'pushup', 'pushdown',
    'irisin', 'irisout', 'cropmove', 'pixellate', 'None', 'null',
    # 默认位置
    'left', 'right', 'center', 'truecenter', 'top', 'bottom',
    'topleft', 'topright', 'bottomleft', 'bottomright',
    'offscreenleft', 'offscreenright', 'reset', 'default',
})


# ==================================================================
#  保真等级：Ren'Py 的**流程块头**
# ==================================================================

#: Ren'Py **标准流程块**：`while` / `for` 这类"工具认识它是什么、
#: 但（截至 M1）不把它结构化建模"的语句头。
#:
#: 为什么要单独一份（2026-10-07，M1 真机实测）：
#:
#: 真实项目 `mini_game1.rpy:50` 有一个游戏主循环
#:
#:     while current_round < game_rounds:
#:         call mini_game_round from _call_mini_game_round
#:         $ current_round += 1
#:
#: 它以前**连头带块体被整块丢掉**（`_stmt_unmodeled` 对带子块的语句
#: `return None`），全量重生成之后这个循环就没了。现在它走保真通道
#: （`Beat.opaque`）逐字保留。
#:
#: 但它和"项目自定义的块"**不是一回事**，用户动作也不同：
#:   · 标准流程块（T2）：内容安全，只是工具不替你结构化编辑
#:     —— 该催的是**我们**把它建模；
#:   · 自定义块（T3）：工具完全不懂 —— 该催的是**用户/程序**
#:     用 `storygraph.rules.json` 或宿主 `register_statement` 告诉我们。
#:
#: 所以告警文案必须分得开，不能混成一句"工具不认识"。
#: 分类是**纯函数**（只看语句头），因此解析层与门禁层可以各自调用
#: 而不会漂移 —— 这也是它放 `core` 的原因：`parsing` 不许 import `story`
#: （契约 H1 的依赖方向）。
RENPY_FLOW_BLOCK_HEADS = frozenset({
    # 循环
    'while', 'for',
    # 分支 / 结构
    'if', 'elif', 'else', 'menu', 'label', 'return', 'pass',
    # 定义式块（顶层居多，但 label 里也可能出现）
    'init', 'python', 'screen', 'transform', 'image', 'layeredimage',
    'style', 'translate', 'testcase', 'on', 'parallel', 'function', 'rpy',
    'early', 'camera',
})


# ==================================================================
#  语句头词表的**唯一真源**（M5，2026-10-07）
# ==================================================================
#
# 为什么要有这一节：同一批"工具认识的语句头"从前在**三处**各写一份，
# 口径不同、还都得记得同步（加一种语句要在三处补，迟早在某处漏）：
#
#   · `beat_render._RENPY_STATEMENT_HEADS`  —— 判"这行是 Ren'Py 原文，
#     不许补 `$`"；
#   · `project_conventions.KNOWN_STATEMENT_HEADS` —— 判"工具认不认识它"；
#   · `project_conventions._CONTAINER_HEADS` —— 判"块体要不要原样跳过"。
#
# ⚠ **它们不能简单合并成一个集合** —— 三者的语义不同，差异是**刻意的**：
# `beat_render` 那一份**必须排除** `if/elif/else/while/for`，因为它们在
# `raw` 里出现时是**代码块的条件行**，当语句透传会把块结构弄坏
# （第一版把 `while` 放进来，实测红了两个测试）。
# 而 `: 认识吗` 那一份**必须包含**它们（那确实是 Ren'Py 语句头）。
#
# 所以这里的做法是：**公共内核只写一份**，各处的差异用"谁 = 内核 ± 显式增减"
# 表达出来 —— 差异本身也变成可读的、有注释的事实。

#: 「这行是 Ren'Py 语句原文」—— 判据是**动词白名单**。
#: 使用者：`beat_render._RENPY_STATEMENT_HEADS`（防补 `$`）。
RENPY_STATEMENT_VERBS = frozenset({
    '$', 'python', 'with', 'pause', 'jump', 'call', 'play', 'queue', 'stop',
    'scene', 'show', 'hide', 'voice', 'window', 'menu', 'return', 'pass',
    'init', 'label', 'define', 'default', 'transform', 'image', 'extend',
    'screen', 'nvl', 'camera', 'style', 'layeredimage', 'translate',
    'renpy.', 'persistent.', 'config.', 'gui.', 'store.',
    'narrator', 'voice_sustain',
})

#: 流程块头：**只**用来算"工具认不认识"，**不许**进上面那份动词表。
RENPY_FLOW_HEADS = frozenset({'if', 'elif', 'else', 'while', 'for'})

#: 声明式/子句式的语句头（脚本里以它们开头也算"工具认识"）。
RENPY_DECLARATIVE_HEADS = frozenset({
    'at', 'expression', 'character', 'audio', 'movie', 'side', 'onlayer',
    'behind', 'zorder', 'as', 'block', 'on', 'parallel', 'function', 'key',
    'timer', 'input', 'testcase', 'rpy', 'early',
    # ⚠ 这三个**无点写法**（`renpy` / `persistent` / `store`）从前在
    # `KNOWN_STATEMENT_HEADS` 里，收敛时必须留着 —— **不许出现"以前认识、
    # 现在不认识"**（那会让覆盖度报告凭空多出几条噪声，用户以为退化了）。
    # 收敛后新增的另外 7 个（`renpy.` / `persistent.` / `config.` / `gui.` /
    # `store.` / `narrator` / `voice_sustain`）来自动词表：它们只会出现在
    # python 块里，而那些块被 `RENPY_CONTAINER_HEADS` 整块跳过，
    # 所以报告口径**实际没有变化**。
    'renpy', 'persistent', 'store',
})

#: 工具**认识**的语句头全集（含流程块）。
#: 使用者：`project_conventions.KNOWN_STATEMENT_HEADS`（算覆盖度）。
RENPY_KNOWN_HEADS = (RENPY_STATEMENT_VERBS | RENPY_FLOW_HEADS
                     | RENPY_DECLARATIVE_HEADS)

#: 这些头**开一个"工具不编辑的块"**：块里的语句一律原样保留，不算"不认识"。
#: 使用者：`project_conventions._CONTAINER_HEADS`。
#: 不这么分的话，覆盖度报告会被 `add` / `text` / `vbox` 这类界面语句淹没
#: （实测 203 种里绝大多数是这种噪声）。
RENPY_CONTAINER_HEADS = frozenset({
    'screen', 'layeredimage', 'transform', 'style', 'translate', 'python',
    'init', 'image', 'define', 'default', 'on', 'parallel', 'function',
    'testcase', 'rpy', 'early',
})


# ==================================================================
#  `show` / `scene` / `hide` 子句关键字的**唯一真源**（2026-10-15）
# ==================================================================
#
# 依据**官方语法**（displaying_images.html）：
#
#     show_statement -> "show" "expression" simple_expression
#                       ( "as" name )? ( "onlayer" name )?
#                       ( "at" transform_list )? ( "behind" name_list )?
#                       ( "with" simple_expression )?
#
# 外加同一族的 `zorder`（docs: "zorder Takes an integer"）；docs 还明说
# **scene 取与 show 同一批 properties**、`hide` 只有 `onlayer`。
#
# 为什么要有这一节：这批词从前在**四处**各写一份，口径已经不同 ——
#
#   · `parsing/renpy_parser._SHOW_KEYWORDS`          = 规范集
#   · `story/beat_stage_parse._SHOW_ARG_KEYWORDS`    = 规范集（纯重复）
#   · `story/excel_export._SHOW_KEYWORDS`            = 有 `on`、**漏了 `onlayer`**（真词漏了）
#   · `assets/asset_registry_constants._SHOW_STOP_WORDS` = 规范集 + `expression`
#   · `story/generation_guard` 的 stopwords          = 规范集 − `at`（它本来就用在 `at` 之后）
#
# 现在**内核只写一份**，各处的差异用"**内核 ± 显式增减**"表达出来
# —— 与上面「语句头词表」那一节同一套做法：差异本身也变成可读、有注释的事实。
#
# ⚠ 这一节**只统一口径，不改口径**：各处改写后**生效的集合与从前逐字相同**
#   （除了 `excel_export` 补回漏掉的 `onlayer` —— 那是"真词漏了"，
#   让 `show cg onlayer overlay` 里的 `onlayer` 不再被当成属性报错）。
#   发现但**没动**的口径分歧，都在各自站点上写明理由（见下）。
#
# ⚠⚠ **2026-10-16 收口（用户排期 C 项）**：光收词表还不够 —— 上面那五处
#   各有一份"**拿集合去 split()**"的循环，于是"改一处忘一处"还会复发。
#   现在切分与"哪个词是什么角色"只有一处实现：`classify_show_words`（下面），
#   分词真源是 `tokenize_show_words`。上面列的 `_SHOW_KEYWORDS` /
#   `_SHOW_ARG_KEYWORDS` / `_SCENE_KEYWORDS` **三个别名表已删**
#   （它们只是同一份集合的第二名字，"看起来像真源、其实没人用"）。
#   剩下的消费点一律只声明**语义**：
#     · `asset_registry_constants._SHOW_STOP_WORDS` —— "这些词不可能是参数值"
#     · `learn_statement._NOT_A_VALUE`             —— 同上（多两个布尔字面量）
#     · `generation_guard` 的 stopwords            —— `at` 之后的变换名边界
#   判据见 `tests/test_show_clause_words.py` 的 §一处实现。

#: 图像名之后**哪些词算子句** —— 既是"图像名到哪里为止"，也是"这些词后面不是属性"。
SHOW_CLAUSE_KEYWORDS = frozenset({
    'at', 'as', 'with', 'behind', 'onlayer', 'zorder',
})

#: `show` 的**引入型**关键字：它们**替换**图像名，后面跟的不是图名 ——
#: `show expression <表达式>` / `show screen <屏幕名>` / `show layer <层名>`。
#:
#: ⚠ 与 `NON_ACTOR_SHOW_TARGETS` **不是一个概念**（那个更宽：`text` / `movie`
#:   在官方语法里是**预定义 displayable 当图名**，不是引入型）。
#:
#: 使用者：读目标的一侧是 `classify_show_words`（下面）；另有两个站点按
#: "这些词根本不可能是参数值"的**另一层**语义各用一份
#: `SHOW_CLAUSE_KEYWORDS | SHOW_INTRO_KEYWORDS`
#: （`learn_statement._NOT_A_VALUE`、`asset_registry_constants._SHOW_STOP_WORDS`）
#: —— 那是"取词边界"，不是本分类器。
SHOW_INTRO_KEYWORDS = frozenset({'expression', 'screen', 'layer'})


# ------------------------------------------------------------------
#  `classify_show_words`：show / scene / hide 那句话里的词**各是什么角色**
# ------------------------------------------------------------------
#
#  为什么必须有它（2026-10-16，用户排期 C 项）：同一件事从前**六个站点各写一遍**
#  "拿一个集合去 split()"。词表已经收敛成上面两份真源（2026-10-15），
#  但**切分口径**仍然各写一遍 —— 于是"改一处忘一处"的下一次复发只是时间问题。
#  与 `authoring/pattern.assign_tokens`（10-15 收"一行词 → 每格占哪几个词"）
#  同一套路：**消掉第二份口径，而不是去加固每一份**。
#
#  这一份是**纯函数**：不 import 任何 story / parsing 层的东西，只认这两份词表。
#  它回答四个问题：
#    · `target`     —— 图像名（或引入型关键字后面那个表达式）
#    · `intro_kind` —— 这个目标是什么形态（见下）
#    · `clauses`    —— 子句关键字 → 它吃掉的词（`at` / `behind` 可以是多个）
#    · `words`      —— 原句里的每一个词，按角色分好（给"按位置看"的调用方）
#
#  `intro_kind` 有五种取值：
#    · `'image'`         —— 普通图像名（`show lan happy at left`）
#    · `'expression'`    —— `expression <Python 表达式>`（**目标就是那个表达式**，
#                          遇到子句关键字才停；`as` / `at` 照样要切出来）
#    · `'screen'` / `'layer'` —— `show screen X` / `show layer Y`：**没有图像名**
#                          （`target` 就是 X / Y 本身，调用方按"非图像资产"处理）
#    · `'none'`          —— 后面什么都没有（`show expression` / `show layer`）
#    · `'unknown-intro'` —— 切不出普通目标、却也不是已知引入型（`show zorder 3`）。
#                          调用方按铁律 #3"认不出就原样抄"处理 —— 落 `raw`，绝不猜。
#
#  ⚠ 口径**逐字对齐**于它取代的那段 `renpy_parser._parse_show_scene` 循环：
#    逗号是分隔符、不算目标的一部分（`show bg room, night` → target `bg room`）；
#    `at` / `behind` 吃到下一个子句关键字为止（逗号只当分隔符、不进值）；
#    其余子句各吃一个词；`as` 重复时**后一个赢**（与从前一致）。
#    这不是"顺手改口径"，是取代的**前提**。

_SHOW_MULTI_CLAUSES = frozenset({'at', 'behind'})

_SHOW_KIND_BY_INTRO = {'expression': 'expression', 'screen': 'screen',
                       'layer': 'layer'}


def _strip_show_comma(tok: str) -> str:
    """去掉一个 show 词**末尾**的顶层逗号（`room,` → `room`）。

    ⚠⚠ 只许剥**末尾**那一个，**token 内部的逗号一个字都不许动**：
      · 括号里的逗号是 Python 参数分隔符 —— `Text("SEIREN PROJECT",
        color="#89c7fe")` 是**一个** token（分词真源里深度>0 的逗号不分词），
        把内部逗号剥掉就变成 `Text("SEIREN PROJECT" color="#89c7fe")`，
        **一个 Python 语法错误**、产物直接跑不起来（真机 `screens.rpy:1439` 实测）；
      · 引号里的逗号同理。
    所以这里**不看后面的内容**、只 `removesuffix(',')` 一次。
    """
    return tok[:-1] if tok.endswith(',') else tok


def tokenize_show_words(text: str, *, keep_commas: bool = True) -> list:
    """
    `show` / `scene` / `hide` 目标部分的**分词真源**（2026-10-16 收重复）。

    从前这份实现住在 `parsing/renpy_parser._tokenize_show`；现在 core 里放一份、
    那边只叫它 —— 因为 `classify_show_words`（下面）必须用**同一把刀**切词，
    否则"括号里的逗号不算分隔符"这类细节就会在两者之间漂移
    （实测：真机 `show image puzzle_path at Transform(xysize=(1920, 1080))`
    用朴素的 `str.split()` 会切出 `Transform(xysize=(1920,` —— 一个语法错误，
    再拼回去产物直接跑不起来）。

    规则：
      · 空格分隔；
      · 逗号是独立 token（**只在括号外/引号外**）；
      · 引号字符串不切分（`expression "a b.png"` 里整个引号串是一个 token）；
      · `(...)` / `[...]` / `{...}` 里的逗号留住（`Transform(xysize=(1920, 1080))`）。

    `keep_commas=False` 时连**独立**的逗号也不吐出来（那些调用方只想看词）。
    """
    text = str(text or '')
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in ' \t':
            i += 1
            continue
        if c == ',':
            if keep_commas:
                tokens.append(',')
            i += 1
            continue
        if c in ('"', "'"):
            quote = c
            start = i
            i += 1
            while i < n:
                if text[i] == '\\':
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            tokens.append(text[start:i])
            continue
        # 普通 token：一路吃到空格或**顶层**逗号；括号里的逗号留住
        start = i
        depth = 0
        while i < n:
            ch = text[i]
            if ch in '([{':
                depth += 1
            elif ch in ')]}':
                if depth:
                    depth -= 1
            elif depth == 0 and (ch in ' \t,'):
                break
            i += 1
        tokens.append(text[start:i])
    return tokens


class ShowWordRoles(tuple):
    """
    `classify_show_words` 的结果。故意做成 **NamedTuple 风格**：

      · 可以 `.target` / `.clauses['at']` 按名字取（可读）；
      · 也能像元组一样解包 `target, intro, clauses, words = …`（省事）；
      · `result.as_dict()` 还原成普通字典（习惯 dict 的调用方不用改）。

    ⚠ 它**不是** `dict` 的子类（是 `tuple` 的），所以没有 `result['target']`
      那种取法 —— 要字典请显式调 `as_dict()`：`dict(tuple)` 会把四个字段
      当成"键值对"解读并在解包时炸掉，那种隐式转换宁可没有。
    """

    __slots__ = ()

    def __new__(cls, target: str, intro_kind: str, clauses: dict, words: list):
        return super().__new__(
            cls, (target, intro_kind, dict(clauses), list(words)))

    @property
    def target(self) -> str:
        return self[0]

    @property
    def intro_kind(self) -> str:
        return self[1]

    @property
    def clauses(self) -> dict:
        return self[2]

    @property
    def words(self) -> list:
        return self[3]

    def clause(self, name: str, default=''):
        """取一个子句的值：多个取列表，单个取字符串，没有取 `default`。"""
        return self.clauses.get(name, default)

    def as_dict(self) -> dict:
        """还原成普通字典：`{'target':…, 'intro_kind':…, 'clauses':…, 'words':…}`。"""
        return {'target': self.target, 'intro_kind': self.intro_kind,
                'clauses': dict(self.clauses), 'words': list(self.words)}

    def __repr__(self) -> str:
        return (f'ShowWordRoles(target={self.target!r}, '
                f'intro_kind={self.intro_kind!r}, clauses={self.clauses!r}, '
                f'words={self.words!r})')


def classify_show_words(text: str, *, intros=None) -> ShowWordRoles:
    """
    把 `show` / `scene` / `hide` 的剩余部分（动词之后那一段）拆成词的角色。

    参数
    ----
    text
        动词之后的原文（`'lan happy at left with dissolve'`）；
        也可以整行传（`'show lan happy'`）—— 函数自己吃掉句首动词。
    intros
        把**首个词**认成"引入型关键字"的集合，默认 `SHOW_INTRO_KEYWORDS`。
        `scene` 传 `SHOW_INTRO_KEYWORDS - {'expression'}` 就是"expression 走
        普通目标那条路"。

    返回值见 `ShowWordRoles` 与上面那节注释。**纯函数、无副作用**。
    """
    intro_words = SHOW_INTRO_KEYWORDS if intros is None else frozenset(intros)
    raw = [w for w in tokenize_show_words(text, keep_commas=False) if w]
    if raw and raw[0] in ('show', 'scene', 'hide'):
        raw = raw[1:]
    if not raw:
        return ShowWordRoles('', 'none', {}, [])

    words: list = []
    clauses: dict = {}
    target_parts: list = []
    intro_kind = 'image'

    i = 0
    n = len(raw)
    while i < n:
        tok = raw[i]

        if tok in SHOW_CLAUSE_KEYWORDS:
            words.append((tok, 'clause'))
            i += 1
            values = []
            if tok in _SHOW_MULTI_CLAUSES:
                # `at a, b` / `behind x, y`：吃到下一个子句关键字为止。
                # 独立逗号已经在切词时被丢掉（`keep_commas=False`），
                # 值里的逗号与旧循环**逐字一致**。
                while i < n and raw[i] not in SHOW_CLAUSE_KEYWORDS:
                    values.append(raw[i])
                    words.append((raw[i], 'value'))
                    i += 1
            else:
                # 其余子句各吃**一个**词（`as qing` / `with dissolve` /
                # `onlayer master` / `zorder 3`）。
                if i < n and raw[i] not in SHOW_CLAUSE_KEYWORDS:
                    values.append(raw[i])
                    words.append((raw[i], 'value'))
                    i += 1
            clauses[tok] = (values if tok in _SHOW_MULTI_CLAUSES
                            else (values[0] if values else ''))
            continue

        if not target_parts and not clauses:
            # 目标位的**第一个词**：只有这里才看引入型关键字
            if tok in intro_words:
                intro_kind = _SHOW_KIND_BY_INTRO.get(tok, 'expression')
                words.append((tok, 'intro'))
                i += 1
                if intro_kind == 'expression':
                    # `expression <Python 表达式>`：表达式里可以有空格、括号、
                    # 引号，一直到**子句关键字**为止都算目标本体（与
                    # `beat_stage_parse._parse_show_expression` 同口径）。
                    while i < n and raw[i] not in SHOW_CLAUSE_KEYWORDS:
                        target_parts.append(raw[i])
                        words.append((raw[i], 'target'))
                        i += 1
                else:
                    # `screen` / `layer`：后面**一个词**是屏幕/层的名字
                    if i < n and raw[i] not in SHOW_CLAUSE_KEYWORDS:
                        target_parts.append(raw[i])
                        words.append((raw[i], 'target'))
                        i += 1
                continue

        # 普通目标：图像名可以多段（`bg room night`），遇到子句关键字为止
        target_parts.append(tok)
        words.append((tok, 'target'))
        i += 1

    target = ' '.join(_strip_show_comma(p) for p in target_parts)
    target = ' '.join(target.split())

    if intro_kind == 'image' and not target:
        # 普通目标为空：要么后面什么都没有，要么第一个词就是子句关键字
        # （`show zorder 3` 这种可疑形状）—— 绝不猜，交给调用方落 raw。
        intro_kind = 'none' if not clauses else 'unknown-intro'

    return ShowWordRoles(target, intro_kind, clauses, words)


#: 不可能出现在"**说话人**"位上的 Ren'Py 语句关键字（角色变量不许叫这些名字）。
#:
#: 使用者：`story/patcher._KEYWORD_SPEAKERS` —— 句位配对（`ignore_speaker`）
#: 不许把它们当成"说话人变了"（`extend "…"` / `menu "标题":` 的形状与
#: `lyj "台词"` 一样，退化成同一身份就会和真台词互相顶掉）。
#:
#: ⚠⚠ **`narrator` 必须排除在外**：官方文档（dialogue.html）说明它是
#:   "say statements without a character name 用的那个 `Character`"，且
#:   **特殊角色可以被重定义** → **它能当说话人**。并进来会让
#:   `narrator "A"` 与 `narrator "B"` 得到同一个身份 → **静默丢行**。
#:   同理 `RENPY_DECLARATIVE_HEADS` 里的 `character` / `audio` / `movie` /
#:   `key` / `timer` / `input` / `side` 都不是保留字（可以是角色变量），
#:   所以这一份**只从动词表 + 流程块头 + show 子句词**取。
#:
#: ⚠ 本轮**只统一口径、不改口径**：下面那两处减法是"这一份从前就没有它们"，
#:   加进来会改句位配对的行为（要单独定）。真源是那三个内核集合。
RESERVED_STATEMENT_KEYWORDS = (
    (RENPY_STATEMENT_VERBS | RENPY_FLOW_HEADS | SHOW_CLAUSE_KEYWORDS
     | {'break', 'continue'})
    - {'narrator', '$', 'voice_sustain', 'layeredimage', 'translate'}
    - {w for w in RENPY_STATEMENT_VERBS if w.endswith('.')}
)


def statement_head_token(text: str) -> str:
    """取一句话的**语句头**（第一个 token，去掉结尾的 `:`）。"""
    s = str(text or '').strip()
    if not s:
        return ''
    return s.split(None, 1)[0].rstrip(':').strip()


def is_known_flow_block(text: str) -> bool:
    """这个语句头是 Ren'Py 标准流程块吗（工具认识形状、但不建模）。"""
    return statement_head_token(text) in RENPY_FLOW_BLOCK_HEADS


#: 生成器写在产物**首行**的标记。
#:
#: 为什么它必须只有一份实现（2026-10-05）：两处要用它，而**方向相反** ——
#:   · `story.beat_render` 把它**写**进产物；
#:   · `story.generation_guard._looks_handwritten` 靠它判断
#:     "这个已存在的文件是不是工具自己写的"，从而决定要不要拒绝覆盖。
#: 两处字符串一旦漂移，护栏就会认不出自家产物，"再导出一次"被拦下 ——
#: 那比不拦更烦，而且症状（"我明明是用工具生成的"）极难自查。
#:
#: 放 `core` 而不是那两个模块：`generation_guard` 已经 import 了
#: `beat_render`，反向 import 会成环（`beat_render` 自己也不能为了这个
#: 常量去依赖 story 层）。
GENERATOR_MARKER = '# 由剧本表自动生成，请勿在此文件手工改动结构'


__all__ = [
    'ASSET_TYPE_ATTR', 'AUDIO_TYPE_ATTR',
    'NON_ACTOR_TAGS', 'NON_ACTOR_SHOW_TARGETS', 'LAYER_CN_TAG',
    'RENPY_BUILTIN_TRANSFORMS', 'GENERATOR_MARKER',
    'RENPY_FLOW_BLOCK_HEADS', 'statement_head_token', 'is_known_flow_block',
    'RENPY_STATEMENT_VERBS', 'RENPY_FLOW_HEADS', 'RENPY_DECLARATIVE_HEADS',
    'RENPY_KNOWN_HEADS', 'RENPY_CONTAINER_HEADS',
    'SHOW_CLAUSE_KEYWORDS', 'SHOW_INTRO_KEYWORDS',
    'tokenize_show_words', 'ShowWordRoles', 'classify_show_words',
    'RESERVED_STATEMENT_KEYWORDS',
]
