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

#: 图像名之后**哪些词算子句** —— 既是"图像名到哪里为止"，也是"这些词后面不是属性"。
SHOW_CLAUSE_KEYWORDS = frozenset({
    'at', 'as', 'with', 'behind', 'onlayer', 'zorder',
})

#: `show` 的**引入型**关键字：它们**替换**图像名，后面跟的不是图名 ——
#: `show expression <表达式>` / `show screen <屏幕名>` / `show layer <层名>`。
#: 使用者：`authoring/learn_statement._NOT_A_VALUE`（反推参数名时它们不是"值"）、
#: `assets` 那侧（`show expression …` 本来就没有图像名 → 切出来是空）。
SHOW_INTRO_KEYWORDS = frozenset({'expression', 'screen', 'layer'})


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
    'RESERVED_STATEMENT_KEYWORDS',
]
