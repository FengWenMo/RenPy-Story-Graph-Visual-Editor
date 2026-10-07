"""
storygraph.story.beat_model
===========================
**Beat（演出单元）数据结构**，以及它与表格单元格之间的编解码原语。

一个 Beat = 一行演出（"一句台词 + 这一拍要做的事"）。这里放三样东西：

  · 列契约：`BEAT_FIELDS`（列顺序唯一真源）、中文表头、下拉列、老表迁移列名
  · `Beat`：数据类本体（`to_row` / `from_row` 逐列对应）
  · 单元格原语：`compose_*` 把结构化字段写成格内文本，`_split_*` 反向拆开

刻意**不含**渲染与 Excel IO —— 那些在 beat_render / excel_export 里。
这一层只依赖 core 与 excel.cells，是全 story 层的地基。
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

from storygraph.core.statement import MARKER
#: 「每参数一列」的列名判定（`语句名·参数名` / `语句名·参数名@位置号`）。
#: 唯一真源在 `story/spec_args.py`（零项目内依赖），这里只 import 一个纯函数。
from storygraph.story.spec_args import is_dynamic_column





# ===================== 数据结构 =====================

BEAT_FIELDS = [
    'LineId', 'Seq', 'Label', 'Kind', 'Speaker', 'Comment', 'Text',
    # Condition 紧跟在 Text 后面（不是塞在最后一列）：它是**台词的修饰符**
    # ——"这句话在什么条件下才出现"，策划读表时和 Text 是同一眼的。
    # 2026-10-05 补：这一列**此前根本不存在**，于是 `Beat.condition`
    # 在 `to_row` / `from_row` 里两头都没有出口 —— `.rpy → .rpy` 看起来
    # 正常（渲染层 `beat_render` 一直是对的），可**一经过 Excel 往返，
    # 所有 if / 菜单条件全被抹平**（真实项目 `mini_game1` 实测 7 条 → 0 条，
    # if 行 5 → 1，分支变成顺序执行）。加列会改变 `BEAT_FIELDS` 的下标，
    # 所以必须同时迁 `HIDDEN_BEAT_COLUMNS` 的隐藏列定位（见下）。
    'Condition',
    'Scene', 'Show', 'At', 'With', 'Hide',
    'BGM', 'BGMParams', 'SFX', 'SFXParams', 'Voice',
    'Stage', 'CodeBlock', 'ContentHash',
    # StageOrder 放在最后并**隐藏整列**：它记录"这一拍先 hide 再 show"
    # 这类**源顺序**，属于运行时语义（`hide X` + `show X` = 重置立绘，
    # 反序会把立绘藏掉），不该由策划填。但也不能丢——丢了就退回
    # `scene→show→hide` 兜底，顺序变了演出就变了。
    #
    # 为什么是隐藏列而不是另开元数据表：它是**逐行**属性，藏在同一行上
    # 才能跟着复制/拖动/排序走；另开一张表一被手改就错位。
    # 用户 2026-09-28 拍板：「由工具维护，不暴露给策划」。
    'StageOrder',
    # OpOrder = 本拍**全部操作**（舞台/音乐/音效/语音）按源码出现的顺序。
    # 为什么还需要它（StageOrder 不够）：舞台指令之间能靠 StageOrder 保序，
    # 但**跨类别**的相对顺序没人记——`play music X` 写在 `show Y` 之前时，
    # 渲染固定按"舞台→音频→语音"输出，音频就跑到立绘后面去了。
    # 这在同一次交互里不改变最终画面，但回放/时间轴看起来是错的顺序。
    'OpOrder',
    # CodeBlocks = 「代码」列里**哪些行构成 `python:` 块**（行号区间）。
    #
    # 放在最末尾、**整列隐藏**（用户 2026-10-05：「新增的列放远点，甚至可默认
    # 隐藏，因为事实上策划和文案不需要」）。它纯属工具维护的内部结构：
    #
    #   · 「代码」列一个格子装的是**多行文本**，`python:` 块的边界在纯文本里
    #     **表达不出来** —— 一格里混着多条语句时（`$ a` / `$ b` / `python: c`）
    #     读回来无从知道哪几行属于同一个块；
    #   · 猜错的代价是**静默改语义**：整个块被拆成一条条 `$` 语句之后，
    #     `for` / `if` 的嵌套体全被压到同一层（真实项目 `mini_game1.rpy`
    #     一个块拆出 53 条，`for` 里的 `if` 直接变成顺序执行）。
    'CodeBlocks',
    # `Nest`：**结构位**（块路径，见 `Beat.nest_path`，B 阶段一）。
    # 它与 `_menu_member` 是**同一个信息的两份载体**：后者只活在 .rpy 侧、
    # 一进 Excel 就没了；前者随表走，于是"这条拍属于哪个选项"在表格往返
    # 之后仍然说得清（现在只用来**对拍**，阶段二才让渲染改用）。
    'Nest',
    # `Inline`：**行尾注释**（C）。它与 `Nest` 不同 —— 那是结构位，这是
    # 词法细节；两者都"工具维护 + 整列隐藏"。
    'Inline',
    # `Opaque` = **工具不认识的块语句，原样透传**（M1，2026-10-07）。
    #
    # 为什么单独一列、而不是塞进「代码」列：`CodeBlock` 的渲染会**推断**
    # （认不出就补 `$`，整块还可能被包成 `python:`）。对一段工具看不懂的
    # 自定义块，任何推断都是**改剧情**：
    #
    #     mycue "闪电" intensity=3:
    #         target = ling            ← 补 `$` 就变成 `$ target = ling`
    #
    # `Opaque` 这一列的契约只有一句：**逐字写回，一个字都不动**。
    # 它整列隐藏 —— 它是"我们管不了"的记录，不是给策划填的字段。
    'Opaque',
    # `SpecName` = 这一拍那条自定义语句**被谁声明过**（M3，2026-10-07）。
    #
    # 它把 `Opaque` 从"工具不认识"升级成"工具知道你是谁"：
    #   · 空   → 真·未声明（报告里要催）；
    #   · 有值 → 项目用 `storygraph.rules.json` 或 `renpy.register_statement`
    #            声明过 → **T1**，工具按通用行处理，不再报"不认识"。
    # 随表走是为了让"声明过没有"这件事**在往返之后依然成立**。
    'SpecName',
    # `SpecHead` = 那条自定义语句的**头行原文**（M4，2026-10-07）。
    #
    # 为什么单独一列（而不是让策划去改 `Opaque` 里的一大段）：这是 T1a/T1b
    # 的**参数编辑面** —— 头行装着"这条语句在干什么"（`mycue "闪电"
    # intensity=3:`），块体才是"细节"。分开之后：
    #   · 头行一格可改（编剧真能用）；
    #   · 块体格仍是逐字原文（撞不到）；
    #   · **没改时逐字节不变**（渲染层比对，见 `beat_render.emit_opaque`）。
    'SpecHead',
    # `LoopHeader` = 循环块（`while` / `for`）的**头行**（D2，2026-10-07）。
    #
    # 与 `SpecHead` 同款：头行可编辑可校验，块体逐字躺在 `Opaque` 里。
    # 有了它，`while` / `for` 就从"工具认识形状但没建模"（T2）升到
    # **T0b**（内置 IR + 块体逐字）—— 见 `core.capability`。
    'LoopHeader',
    # `CameraHeader` = 3D 舞台 `camera` 块的**头行**（2026-10-07）。
    #
    # 与 `LoopHeader` 同款：头行（`camera background:`）可编辑可校验，
    # 块体（ATL 属性）逐字躺在 `Opaque` 里。有了它，`camera` 就从
    # "工具认识形状但没建模"（T2，报告里进"催我们建模"那一桶）升到
    # **T0b** —— 与 `while` / `for` 同一档、同一套约定。
    'CameraHeader',
]


#: 这些列整列隐藏（工具维护的内部元数据，策划不该看见也不该填）。
#: 隐藏（hidden=True）在 Excel/WPS 里都是"列被收起"而不是"没有这一列"，
#: 数据照旧读写，所以往返不会丢——这是选隐藏列而非"不写这一列"的原因。
HIDDEN_BEAT_COLUMNS = ('StageOrder', 'OpOrder', 'CodeBlocks', 'Nest',
                       'Inline', 'Opaque', 'SpecName', 'SpecHead',
                       'LoopHeader', 'CameraHeader')


# 写进 Excel 的表头（中文）。策划只看中文；程序侧键名仍是英文，
# 读取时通过 _HEADER_ALIASES 把中文表头映射回键名，两边都不别扭。
BEAT_HEADERS = {
    'LineId': '行号', 'Seq': '序号', 'Label': '章节', 'Kind': '类型',
    'Speaker': '说话人', 'Comment': '注释', 'Text': '台词',
    'Condition': '条件',
    'Scene': '背景', 'Show': '立绘', 'At': '位置', 'With': '转场',
    'Hide': '退场',
    'BGM': '音乐', 'BGMParams': '音乐参数', 'SFX': '音效',
    'SFXParams': '音效参数', 'Voice': '语音',
    'Stage': '舞台（高级）', 'CodeBlock': '代码',
    'ContentHash': '内容指纹',
    'StageOrder': '舞台顺序（内部）',
    'OpOrder': '操作顺序（内部）',
    'CodeBlocks': '代码块结构（内部）',
    'Nest': '结构位（内部）',
    'Inline': '行尾注释（内部）',
    'Opaque': '原样块（内部）',
    'SpecName': '声明来源（内部）',
    'SpecHead': '自定义语句·头行（高级）',
    'LoopHeader': '循环头行（内部）',
    'CameraHeader': '相机头行（内部）',
}

_HEADER_ALIASES = {v: k for k, v in BEAT_HEADERS.items()}
# 兼容英文表头（旧表 / 程序自己生成的表）
_HEADER_ALIASES.update({f: f for f in BEAT_FIELDS})


#: **单行列**：这些列的值会被**逐字拼进一行代码**（`{说话人} "台词"` /
#: `show {立绘} at {位置}`），所以它们的单元格里**不许有换行/制表符**。
#:
#: 为什么必须有这份清单（2026-10-05，P0-1 的同一类，另一个出口）：
#: `_quote` 转义的是**字符串字面量**（台词）那条出口；而这些列是**裸 token**
#: 出口，不经过任何转义。单元格里的真换行（Excel 的 Alt+Enter、从别处粘
#: 一段多行文本、脚本写表）就直接变成产物里的**物理换行**：
#:
#:     Speaker 列 = 'ling\nX'      →      ling
#:                                        X "第一句"        ← 产物不可解析
#:     At      列 = 'left\nright'  →      show ling at left
#:                                        right              ← 同上
#:     Show    列 = 'ling\nhappy'  →      被拆成两条 show（`show ling` +
#:                                        `show happy`）—— 能解析，但语义变了
#:
#: 后两种最阴：产物**能解析**，所以门禁拦不住（门禁只看"产物的 Python
#: 语法/图像名/label 重名"，中途断行产生的是一个"看起来正常"的短行）。
#:
#: 处置口径：**读表时把控制字符压成空格**（列契约本来就是"一行一个值"），
#: 于是"粘进来的多行值"退化成"用手打了空格"，产物始终是一行 ——
#: 不静默丢内容，也不会写出跑不起来的脚本。
#:
#: 多行列（`Text` 台词 / `Comment` 注释 / `Stage` 舞台 / `CodeBlock` 代码 /
#: `FileBlocks` 文件级块）**刻意不在**这份清单里：它们的换行是**内容**
#: （台词里 `\n` 是强制换行；Stage 一格多指令；代码块多行），由 `_quote`
#: 转义或按行渲染，不走这条处置。
SINGLE_LINE_BEAT_FIELDS = (
    'LineId', 'Seq', 'Label', 'Kind', 'Speaker', 'Condition',
    'Scene', 'Show', 'At', 'With', 'Hide',
    'BGM', 'BGMParams', 'SFX', 'SFXParams', 'Voice',
    'ContentHash', 'StageOrder', 'OpOrder', 'CodeBlocks', 'Nest', 'Inline',
    'SpecName', 'SpecHead', 'LoopHeader', 'CameraHeader',
)

#: C0 控制字符 + DEL（`\n` / `\r` / `\t` 是这里最主要的三位）。
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]+')

#: 标签名里**根本不允许**的字符：空白与冒号（`label <名字>:` 以它们收尾）。
#: 只压成空格还不够 —— `label a b:` 是**语法错的标签头**，比断行更难查
#: （它"看起来像"一个正常的 label 行，门禁也不管 label 名形态）。
_LABEL_STRIP_RE = re.compile(r'[\s:]+')


def _one_line(text: str) -> str:
    """把值压成**一行**：控制字符 → 一个空格（见 `SINGLE_LINE_BEAT_FIELDS`）。"""
    return _CONTROL_CHARS_RE.sub(' ', str(text or '')).strip()


def sanitize_single_line_cell(field: str, value) -> str:
    """
    单行列的单元格值 → **能安全拼进一行代码**的形态。

    两步，顺序固定：

      1. 控制字符 → 空格（所有单行列）：断行、缩进错乱都是它引起的；
      2. `Label` 再去掉空白与冒号：Ren'Py 的标签名里不允许它们，
         留一个空格就写出 `label a b:` —— 一个语法错的标签头。

    读写两侧（`from_row` / `to_row`）**必须都过这里**，否则往返不幂等
    （`Notes` 那次不对称的坑）。
    """
    text = _one_line(value)
    if field == 'Label':
        return _LABEL_STRIP_RE.sub('', text)
    return text


def _row_fields(spec_fields=None) -> list:
    """
    `to_row` 的列契约 = 基础列 + 「每参数一列」的动态列（**唯一一处拼法**）。

    写表的一侧（`excel_export` 的表头）必须用同一个函数取列，
    否则表头行与数据行的列数会对不上 —— 那是"最后几列悄悄全空"
    那一类静默缺陷（`_flatten_single_line_cells` 的长度断言会先吼）。
    """
    return list(BEAT_FIELDS) + [str(f) for f in (spec_fields or ())]


def _flatten_single_line_cells(values: list, fields=None) -> list:
    """
    `to_row` 的收尾：单行列按 `sanitize_single_line_cell` 清洗，
    **与 `from_row` 对称**。

    为什么写这一侧也要做（不只是读那一侧）：

      · **幂等**：`from_row` 会压平，`to_row` 不压的话，
        `Beat(speaker='a\\nb')` 往返一次就变样（`Notes` 那个不对称的坑
        就是这么来的 —— 同一件事不许只有一头有出口）；
      · `openpyxl` **保存时**会对 `\\x0b` 这种控制字符直接抛
        `IllegalCharacterError` —— 不压平的话，一个来自畸形来源的值
        会让"导出 Excel"整个崩掉，而错误信息完全指不到是哪一格。

    行必须与 `fields`（默认 `BEAT_FIELDS`）一一对应：长度对不上说明有人
    加了列没同步 `to_row` —— 那时候**报错**比静默截断好（截断会让最后
    几列悄悄全空）。

    `fields` 是「**每参数一列**」（M4 第二阶段）加进来的：动态列名形如
    `mycue·intensity`，不属于 `BEAT_FIELDS`，但**同样是单行列**（它们会被
    折进语句头行那一行里）—— 所以判据是"在 `SINGLE_LINE_BEAT_FIELDS` 里
    **或**根本不是基础列"。
    """
    fields = list(BEAT_FIELDS if fields is None else fields)
    if len(values) != len(fields):
        raise ValueError(
            f'to_row 写出的格子数（{len(values)}）与列契约'
            f'（{len(fields)}）对不上 —— 加列/删列时两处要一起改')
    return [sanitize_single_line_cell(f, v)
            if ((f in SINGLE_LINE_BEAT_FIELDS or f not in BEAT_FIELDS)
                and isinstance(v, str)) else v
            for f, v in zip(fields, values)]


# 需要下拉框的列 -> 资产类型。
# **下拉是"方便"，不是"牢笼"**：Excel 的数据验证只做提示、不拦截
# （showErrorMessage=False），所以 `bg room@bgfit` 这种扩展写法照样能手打。
DROPDOWN_COLUMNS = {
    'Speaker': 'character',
    'Scene': 'image',
    'Show': 'image',
    'Hide': 'image',
    'At': 'transform',
    'With': 'transform',
    'BGM': 'music',
    'SFX': 'sound',
    'Voice': 'voice',
}


# 音频列里的"伪值"：动作写在同一个下拉里，策划不用再记一列动作。
# 用书名号包起来，永远不会和真实资产 ID 撞。
AUDIO_PSEUDO = {
    'BGM': {'（停止音乐）': 'stop', '（排队播放）': 'queue'},
    'SFX': {'（停止音效）': 'stop', '（排队播放）': 'queue'},
}

_AUDIO_PSEUDO_REVERSE = {k: v for k, v in
                         (('stop', '（停止音乐）'), ('queue', '（排队播放）'))}


# 旧版表格的列名：读旧表时自动迁移
LEGACY_STAGE_COLUMNS = ('Show', 'Hide', 'At', 'With', 'Transform',
                        'StageOrder', 'Raw')

LEGACY_AUDIO_COLUMNS = ('Music', 'MusicParams', 'MusicAction',
                        'Sound', 'SoundParams', 'SoundAction')


# 内联下拉（不依赖资源表）：列 -> 允许值
ACTION_COLUMNS = {
    'Kind': ['say', 'narration', 'menu', 'menu_option', 'extend'],
}

# 内联下拉值的含义提示
ACTION_HINTS = {
    'Kind': 'say=角色说话 / narration=旁白 / menu=菜单提示 / '
            'menu_option=菜单选项 / extend=接着上一句',
}


KIND_SAY = 'say'

KIND_NARRATION = 'narration'

KIND_MENU = 'menu'

KIND_MENU_OPTION = 'menu_option'

KIND_EXTEND = 'extend'

#: 「这个菜单没有标题」的占位文本（只活在内存里的 Beat 上）。
#:
#: 为什么需要它：Ren'Py 的 `menu:` **可以没有 caption**（真实项目 7 处
#: 全是这种），而 `Beat.text` 为空时"菜单拍"和"这一拍什么都没有"无法区分
#: —— 渲染层只能二选一，于是无标题菜单被整块丢掉、选项变成裸旁白。
#:
#: 用**不会与任何真实台词相撞**的字符串当占位（`\x00` 是控制字符，
#: 真人打不出来），并且：
#:   · **不写进 Excel**（`to_row` 写空串）——`openpyxl` 拒绝控制字符，
#:     写进去会直接抛 `IllegalCharacterError`；
#:   · `from_row` 读回空 cell 时按空 caption 处理，所以往返之后
#:     "有菜单、无标题"这个事实由 `kind='menu'` 自己承载，不靠文本。
MENU_NO_CAPTION = '\x00menu'


def is_literal_speaker(speaker) -> bool:
    """
    「说话人」这一格是**字符串字面量**（`"系统"`）还是角色变量名（`e`）？

    Ren'Py 的 say 语句允许 `who` 是字符串字面量（一次性角色名）：
    `"系统" "你已解锁角色【万理】的人物介绍！"`。解析侧把这种写法存成
    **带引号的原文**（见 `renpy_parser.split_string_speaker`），于是：

      · 渲染时**逐字写回去**（它已经是合法的源码片段，不能再套一层 `_quote`）；
      · 它**不是**角色变量，凡是要去角色表里查的地方都得跳过 ——
        否则会写出 `# TODO: 缺少资源 角色 '"系统"'`（把台词换掉）、
        报"说话人不在资源表角色里"、或把 `"系统"` 当成可登记的角色。
    """
    text = str(speaker or '').strip()
    return len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'")


def menu_caption_text(text) -> str:
    """
    Beat.text → 菜单标题。

    · 占位符（`MENU_NO_CAPTION`）→ 空串；
    · **只有空白也算"没有标题"** —— 源脚本里 `menu " ":` 这种写法
      （真实项目里 2 处）语义上就是一个无标题菜单，而空白标题会在
      Excel / 往返归一化里被 `.strip()` 掉，留着它只会制造假差异。
    """
    text = str(text or '')
    if text == MENU_NO_CAPTION:
        return ''
    return text if text.strip() else ''

@dataclass
class Beat:
    """一个演出单元：说这句话时，舞台上是什么样。

    **Excel 那一面是 21 列**（见 BEAT_FIELDS）：20 列结构化编辑面 + 1 列
    **隐藏**的 `StageOrder`。演出相关的是：

        场景 / 立绘 / 位置 / 转场 / 退场      每列一个值，各挂词表下拉
        音乐 / 音效                          各自的「参数」列放 fadein 之类
        舞台（高级）Stage                     一格多指令的 DSL 逃生通道
        语音 / 代码 / 注释 / 台词 / 说话人 …

    `StageOrder` 记录"先 hide 再 show"这类**源顺序**（运行时语义，反序会把
    立绘藏掉），由工具写、工具读，整列隐藏，不暴露给策划。

    已经不再有独立的 Show/Hide/At/With/Transform/Music/Sound 六列
    （那是 26 列时代的历史包袱），但它们仍是 Beat 上的**内部字段**：
    舞台快照、图、校验、多引擎导出都需要"能计算"的形态。

    Stage 与结构化列**互为投影**，方向固定：

        结构化列 ──compose_stage()──▶ Stage 文本
        Stage 文本 ──hydrate_from_dsl()──▶ 结构化字段

    `to_row` 写结构化列 + Stage 两份，`from_row` 反过来 hydrate 回来，
    所以两条通道不会各输出一次（会重复执行转场的那个坑）。

    保真后门只有一个：CodeBlock。里面有东西就**原封不动**抄进脚本，
    不解析、不校验——程序的逃生舱口。
    """
    seq: int = 0
    label: str = ''
    # 稳定身份 + 内容指纹：协作（DB 主键/锁）与翻译单元要用，
    # 详细理由见 assign_identity。
    line_id: str = ''
    content_hash: str = ''
    condition: str = ''
    comment: str = ''
    kind: str = KIND_SAY
    speaker: str = ''
    text: str = ''
    scene: str = ''
    # —— DSL 文本列（策划写这里）——
    stage: str = ''
    bgm: str = ''
    sfx: str = ''
    # —— 由 DSL 解析出来的内部字段（不直接暴露成列）——
    show: str = ''
    hide: str = ''
    at: str = ''                   # 「位置」列：作用于立绘
    scene_at: str = ''             # 背景专属变换（背景列里 `图@变换`）
    with_: str = ''                # 「转场」列
    transform: str = ''
    stage_order: str = ''          # 舞台指令出现顺序，如 'hide,show'（保真用）
    #: 本拍**全部操作**按源码出现的顺序，如 `'stage,music,sound,voice'`。
    #: 见 `BeatsFromStatements` 里的记录点与 `render_beat_to_rpy` 的消费点。
    op_order: str = ''
    #: 本拍对应的**源文件行区间**（1-based，闭区间）。
    #: 只用于**增量导出**（把原文件对应区间替换成新生成的行），
    #: 是运行时元数据：不入表、不进 ContentHash，也不影响渲染。
    src_first: int = 0
    src_last: int = 0
    raw: str = ''
    code: str = ''
    # CodeBlock 里"原生语句"与"python 代码"的**交错顺序**：
    # `[('raw', 'pause'), ('code', 'msg_add(…)'), ('raw', 'show screen …')]`。
    # raw / code 两个字段各自保序，但**两者之间**的顺序只有这份记录能还原。
    # 不记的话导出会把 `$ f()` / `show screen` / `pause` 的交替演出排成
    # "先全部 show/pause，再全部 $ f()"——同一张表导回去代码却变了样。
    ordered: list = field(default_factory=list)
    #: 「代码」列里**哪些行构成 `python:` 块**：`[(起始行, 结束行), ...]`
    #: （**1-based、闭区间**，行号对应 `compose_code(beat).splitlines()`）。
    #:
    #: 为什么需要它（2026-10-05 实测）：一个格子里可能混着多条语句 ——
    #: `$ a` / `$ b` / `python: c` / `python: d`，而**块的边界在纯文本里
    #: 表达不出来**（`python:` 关键字在单元格里已经被剥掉）。猜错的代价是
    #: 静默改语义：整个块被拆成一条条 `$` 语句之后，`for` / `if` 的嵌套体
    #: 全被压到同一层（`mini_game1.rpy` 实测一个块拆出 53 条）。
    #: 兜底：这一列为空（老表 / 手工新建行）时，渲染层退回"按缩进推断"。
    code_blocks: list = field(default_factory=list)
    #: **工具不认识的块语句**的整块原文（头行 + 块体，相对缩进保留）。
    #:
    #: 与 `raw` / `code` 分开存是刻意的：那两条通道的渲染会**推断**
    #: （补 `$` / 包 `python:`），而对一段工具看不懂的自定义块，任何
    #: 推断都是改剧情。这一列的渲染契约只有一句：**逐字写回**。
    #:
    #: 见 `beat_build._emit_raw` 的 `unknown_block` 分支与
    #: `beat_render.render_beat_to_rpy` 的 `emit_opaque`。
    opaque: str = ''
    #: 这一拍的 `opaque` 是**被谁声明过**的自定义语句（M3）：
    #: `''` = 真·未声明（T3，报告要催）；`'mycue'` = 声明过（T1，按通用行处理）。
    #: 见 `story.statement_registry`。
    spec_name: str = ''
    #: 那条自定义语句的**头行原文**（M4）。它是 T1 的**参数编辑面**：
    #: 策划改这一格就是改语句的参数；块体在 `opaque` 里逐字不动。
    #: 渲染时**只有它真的变了**才替换首行（见 `beat_render.emit_opaque`），
    #: 所以"没改 → 逐字节不变"是结构上成立的，不靠自觉。
    spec_head: str = ''
    #: 循环块（`while` / `for`）的**头行原文**（D2）。
    #: 与 `spec_head` 同一套渲染约定：改了才替换、没改就逐字回原文。
    loop_header: str = ''
    #: 3D 舞台 `camera` 块的**头行原文**（2026-10-07）。
    #: 与 `loop_header` 同款（并列，不混用一个字段）—— 见 `CameraHeader` 列。
    camera_header: str = ''
    #: 读表时**被清洗过**的列名（`sanitize_single_line_cell` 真的改了值）。
    #:
    #: 为什么要记下来（2026-10-05，评审意见）：清洗本身是**静默改数据** ——
    #: 值里的换行被压成空格，产物能跑了，但**内容变了**。本项目的立场是
    #: "静默改数据比报错坏得多"（P0 就是这么定义的），所以"救了用户"
    #: 之后必须**告诉他**：`pipeline/validate` 据此报 W114，导出时
    #: `CompileResult` 也带一句。
    #:
    #: 它是**运行时元数据**：不入表、不进 ContentHash（与 `src_first` 同类），
    #: 所以 `to_row` 不收它 —— 收进去反而会让表里多出一列无人维护的仓库。
    sanitized_cells: list = field(default_factory=list)
    #: **文件级保真块**：顶层（label 之外）的一切 —— `define` / `default` /
    #: `image` / `transform` / `screen` / `init python` / 文件头注释 …，
    #: 渲染时原样回到**文件级**（在所有 label 之前），不参与"拍"的组装。
    #:
    #: 它挂在**第一张拍**上（见 `beat_build.attach_file_blocks`）—— 因为它必须
    #: 跟着表走，而"表"的单位是拍。进出 Excel 走的是**隐藏表**
    #: `FILE_BLOCK_SHEET`（不是隐藏列）：真实文件的顶层区间轻松超过 Excel
    #: 单元格 32767 字符的上限，塞进单元格会被**静默截断**
    #: （实测 38492 → 32767）。表是一行一行存的，没有这个限制。
    file_blocks: str = ''
    #: **结构位（块路径）**：这一拍在哪个块里（B 阶段一，2026-10-05）。
    #:
    #: DSL 与 `Stage` 同风格、人可读：
    #:   · `''`   = label 顶层
    #:   · `if:1>menu:2>A>if:3` = 第 1 个 if → 第 2 个菜单 → 第 A 个选项体 → 第 3 个 if
    #:   · segment 形如 `<块类型>:<同类型的文档序>`，选项体用**位置键**（A/B/C…）
    #:     —— 用位置而不是选项文案：改文案不该算"结构变了"。
    #:   · `None` = **不知道**（老表 / 手工构造的拍）→ 对拍时整份跳过，
    #:     绝不拿"不知道"当"在顶层"（那会天天假报）。
    nest_path: Optional[str] = None
    #: **行尾注释原文**（含 `#`，C 阶段）。
    #:
    #: 从前它被词法器丢掉了（`_strip_inline_comment`），于是"表 → 新文件"
    #: 这条路必然丢、改过的行走增量也丢。现在随隐藏列 `Inline` 走。
    #: 空串 = 这一行没有行尾注释（**不是"不知道"**：注释本来就是可选的）。
    inline_comment: str = ''
    #: **条件行**（`if / elif / else` 头那一行）的行尾注释。
    #:
    #: 为什么单独一个槽：`if` 不是拍，它是拍上的条件 —— 这条注释要贴到这一拍
    #: 渲染出来的 `if …:` 那一行，而不是它自己那行。与"自己那行的注释"
    #: 共用一个单元格（`Inline`），用分隔符 + `@if ` 前缀消歧。
    cond_comment: str = ''
    music: str = ''
    music_params: str = ''
    music_action: str = 'play'     # 'play' / 'queue' / 'stop'
    sound: str = ''
    sound_params: str = ''
    sound_action: str = 'play'     # 'play' / 'queue' / 'stop'
    voice: str = ''
    notes: str = ''
    # 这一拍是**从 .rpy 解析出来的**（而不是策划在 Excel 里新写的）。
    # 解析来的图像名是作者的原文（`show ling base_arms_akimbo close_happy`），
    # 导出时必须逐字还回去；不能因为"资源表里查不到这个组合"就改写成
    # 下划线形态 `ling_base_arms_akimbo_close_happy`——那是把能跑的脚本改坏。
    _from_script: bool = False

    def _head_field(self) -> str:
        """
        这一拍的**语句头原文放在哪个字段**（渲染层那套优先级，唯一一处）。

        与 `beat_render.emit_opaque` 的取值顺序必须**完全一致**：
        `spec_head`（项目声明的自定义语句）→ `loop_header`（`while`/`for`）
        → `camera_header`（3D 舞台 `camera`）。不一致就会出现"参数列读的是
        这个头、渲染用的是那个头"—— 改了没效果，而且看不出来。
        """
        for attr in ('spec_head', 'loop_header', 'camera_header'):
            if str(getattr(self, attr, '') or '').strip():
                return attr
        return 'spec_head'

    def statement_head(self) -> str:
        """这一拍的语句头原文（没有头行 → 整块原文的首行，再没有就是空串）。"""
        attr = self._head_field()
        text = str(getattr(self, attr, '') or '')
        if text.strip():
            return text
        return next((ln for ln in str(self.opaque or '').split('\n')
                     if ln.strip()), '')

    def spec_arg_cells(self, columns) -> list:
        """
        「每参数一列」的格子值（`columns` = 动态列名，或
        `statement_registry.dynamic_spec_columns` 给的列描述）。

        **值从头行解出来**（`spec_head` / `loop_header` / `camera_header` 优先，
        其次 `opaque` 第一行）—— 单真源始终是头行，这里只是把它**摊成列**
        给人改、给下拉用。认不出的列名 / 头行里没有的参数 → 空格子（**不猜**）。

        位置参数（`camera·layer@0`）按**列名里的下标**取（`@0` = 第 0 个位置
        参数），与 `key=value` 分开 —— 两套混在一起就会把 `camera background`
        读成"有个名叫 background 的参数"。
        """
        from storygraph.story.spec_args import (parse_dynamic_full,
                                                positional_values,
                                                spec_arg_values)
        base = self.statement_head()
        vals = spec_arg_values(base)
        pos = positional_values(base)
        out = []
        for col in columns or ():
            header = col if isinstance(col, str) else str(col.get('header') or '')
            _head, param, positional = parse_dynamic_full(header)
            if not param:
                out.append('')
                continue
            if positional is None and not isinstance(col, str):
                # 列描述里带了位置号（`dynamic_spec_columns` 的产物）→ 以它为准
                try:
                    positional = (int(col['positional'])
                                   if col.get('positional') is not None
                                   else None)
                except (TypeError, ValueError):
                    positional = None
            if positional is not None:
                out.append(pos[positional]
                           if 0 <= positional < len(pos) else '')
            else:
                out.append(vals.get(param, ''))
        return out

    def to_row(self, symbols=None, spec_fields=None) -> list:
        """
        写进 Excel 的一行。

        stage/bgm/sfx 为空时（例如 Beat 来自 .rpy 解析），现场从结构化
        字段**合成** DSL 文本；给了符号表就尽量合成人话（`[显示] 凌跃捷
        开心 左`），合不成就保留原生写法（`show ling close_happy`）。
        两者都能被编译器原样吃回去，不会丢信息。

        `spec_fields` 非空时把「每参数一列」的格子**追加在末尾**（M4 第二
        阶段）。默认 `None` = 只有基础列 —— 老调用方（单测里满地的
        `dict(zip(BEAT_FIELDS, beat.to_row()))`、协作库、UI）一个都不用改。
        """
        stage = self.stage or compose_stage_extra(self, symbols)
        # 音频列：文本列（beat.bgm / beat.sfx）是真相源，有就优先写它；
        # 没有再从结构化字段合成（.rpy 解析来的 Beat 才有结构化字段）。
        # 否则策划手写在「音乐」列的人话/原生写法会在"导出→导入"往返里丢。
        #
        # **参数列始终从结构化字段走 `compose_audio_cell`**（2026-09-28 修）：
        # 原来文本列优先时把参数硬编码成 `''`，于是
        # `stop music fadeout 1.0`（文本列 = `[停止音乐]`）的参数**静默丢失**，
        # 读回来变成裸 `stop music`。真实章节里 6 条全中招。
        _bgm_cell, bgm_params = compose_audio_cell(self, 'music')
        _sfx_cell, sfx_params = compose_audio_cell(self, 'sound')
        bgm = self.bgm or _bgm_cell
        sfx = self.sfx or _sfx_cell
        # `notes` 已经取消（UI 不再暴露，见 `ADVANCED_FIELDS` 的说明），
        # 但**不能因此静默丢掉别人设过的值**（2026-10-05 修，
        # `test_editor_session_mixin_contract.TestFieldContract` 抓到）：
        # `Beat(notes='…')` 直接构造出来的拍（脚本、单测、老的协作数据）
        # 走 `to_row` 时那个值会**蒸发且不报错**。
        #
        # 修法与 `from_row` 对称：读的时候把 `Notes` 并进 `comment`，
        # 写的时候也把 `self.notes` 并进注释列。这样"设过 notes 的拍"
        # 往返一次之后内容还在（落在「注释」列）。
        comment = self.comment
        if self.notes:
            comment = (f'{comment}\n{self.notes}'.strip()
                       if comment else self.notes)
        return _flatten_single_line_cells([
            self.line_id, self.seq, self.label, self.kind,
            self.speaker, comment, menu_caption_text(self.text),
            self.condition,
            compose_scene_cell(self),
            compose_show_cell(self),
            self.at, self.with_,
            compose_hide_cell(self),
            bgm, bgm_params, sfx, sfx_params, self.voice,
            stage, compose_code_cell(self),
            self.content_hash,
            # 隐藏列：源顺序 + 代码块结构 + 文件级代码块。
            # 工具写、工具读，策划界面里看不到。
            self.stage_order,
            self.op_order,
            compose_code_blocks_cell(self),
            # 结构位：顶层写 `-`（**有信息**），不知道写空（老表兼容）——
            # 两者必须能分开，否则对拍会把"不知道"当成"在顶层"。
            ('-' if self.nest_path == '' else (self.nest_path or '')),
            compose_inline_cell(self),
            # **整块原文**（工具不认识的块语句）。逐字进出，不做任何
            # 归一化：它是"我们管不了、但绝不弄丢"的那一类。
            self.opaque,
            # 声明来源（M3）：空 = 未声明（T3），有值 = 已声明（T1）。
            self.spec_name,
            # 头行原文（M4）：T1 的参数编辑面。
            self.spec_head,
            # 循环头行（D2）：`while` / `for` 的参数编辑面。
            self.loop_header,
            # 相机头行（3D 舞台）：`camera [层名]:` 的参数编辑面。
            self.camera_header,
        ] + self.spec_arg_cells(spec_fields or ()),
            fields=_row_fields(spec_fields))

    @classmethod
    def from_row(cls, row: dict) -> 'Beat':
        # 中文表头 → 英文键名（策划看中文，程序用英文，两边都不别扭）
        row = {_HEADER_ALIASES.get(str(k).strip(), k):
               v for k, v in (row or {}).items()}

        # 读表时被清洗过的列名（见 `Beat.sanitized_cells` 的 docstring）
        sanitized: list = []

        def g(key, default=''):
            v = row.get(key, default)
            if v is None:
                return default
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            text = str(v).strip()
            # 单行列不许有换行（见 `SINGLE_LINE_BEAT_FIELDS`）：这些值会被
            # 逐字拼进一行代码，控制字符漏出去就是产物里的物理换行。
            if key not in SINGLE_LINE_BEAT_FIELDS:
                return text
            clean = sanitize_single_line_cell(key, text)
            if clean != text:
                sanitized.append(key)
            return clean

        # 台词**逐字节原样**，首尾空白都不动。
        #
        # 为什么不能 `.strip()` / `.lstrip()`（2026-10-05 修正）：
        # 以前这里写的是"首部空白仍去掉：Ren'Py 里台词字符串前面的空格
        # 没有语义，而 Excel 单元格里它多半是手误"。**这个判断是错的** ——
        # `beat.text` 是**字符串字面量的内容**，里面的空格就是内容：
        #
        #     jc " 听起来很有意思，来吧！"
        #        ^ 这个空格是台词的一部分
        #
        # `lstrip()` 之后往返回来变成 `"听起来很有意思，来吧！"`，
        # 而本项目的硬契约是"打开 → 导出**逐字节不变**"。
        # 真实项目 `script_pomo.rpy` 一处就是这么丢的（审计里 6 条之一）。
        #
        # 尾部空白同理（老注释里记的那 116 条）：`.strip()` 会静默吃掉。
        def g_text(key, default=''):
            v = row.get(key, default)
            if v is None:
                return default
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            return str(v)

        seq = row.get('Seq', 0)
        try:
            seq = int(seq) if seq not in (None, '') else 0
        except (TypeError, ValueError):
            seq = 0

        kind = g('Kind') or KIND_SAY
        if kind not in (KIND_SAY, KIND_NARRATION, KIND_MENU,
                        KIND_MENU_OPTION, KIND_EXTEND):
            kind = KIND_SAY

        # Notes 已经取消：老表里有内容就并进注释，绝不丢字
        comment = g('Comment')
        notes = g('Notes')
        if notes:
            comment = f'{comment}\n{notes}'.strip() if comment else notes

        beat = cls(
            seq=seq,
            label=g('Label'),
            line_id=g('LineId'),
            content_hash=g('ContentHash'),
            # 条件（`if …:` 走哪一支）：与 Text 同样保真，是**语义**不是排版
            condition=g('Condition'),
            comment=comment,
            kind=kind,
            speaker=g('Speaker'),
            text=g_text('Text'),
            code=g('CodeBlock') or g('Raw'),
            voice=g('Voice'),
        )
        # CodeBlock 单元格里 raw 与 code 是混着写的（一个格子装两样）。
        # 读回来时按"看起来像不像一条 Ren'Py 语句"重新分类，并把交错顺序
        # 记进 ordered——否则 `$ f()` 与 `show screen`/`pause` 交替的演出
        # 会被排成"先全部 show/pause 再全部 $ f()"，导出的代码和源脚本不一样。
        beat.code_blocks = parse_code_blocks_cell(g('CodeBlocks'))
        # 工具不认识的块语句：**整块原文**，走 `g_text`（不 strip）——
        # 块体的相对缩进是它的**结构性内容**，压掉就重建不出块。
        # 它刻意不在 `SINGLE_LINE_BEAT_FIELDS` 里（换行是内容）。
        beat.opaque = g_text('Opaque')
        # 声明来源（M3）：单行 token，走会清洗留痕的 `g`。
        beat.spec_name = g('SpecName')
        # 头行原文（M4）：单行列（一格一行可改）→ 也走会留痕的 `g`。
        beat.spec_head = g('SpecHead')
        # 循环头行（D2）：同上（单行列）。
        beat.loop_header = g('LoopHeader')
        # 相机头行（3D 舞台）：同上（单行列）。
        beat.camera_header = g('CameraHeader')
        # 结构位（块路径）：`-` = 顶层（**有信息**）；空 = **不知道**
        # （老表 / 手工加的行）。两者必须分得开 —— 否则对拍会把"不知道"
        # 当成"在顶层"，凡是老表都天天假报。
        #
        # 用 `g`（会清洗并留痕）而不是 `g_text`：这一列在
        # `SINGLE_LINE_BEAT_FIELDS` 里，脏值必须被压成一行**并且记进
        # `sanitized_cells`**（W114），否则又是"静默改数据"
        # —— `test_cleaning_is_recorded[Nest]` 当场抓过这一条。
        _nest = g('Nest')
        if _nest and 'Nest' in sanitized:
            # 被清洗过 = 这一格本来就不是一条合法路径（有换行/控制字符）。
            # 那就不许拿它当依据：按"**不知道**"处理（整份跳过对拍），
            # 而不是拿一条残缺路径去指认"结构对不上"——假阳性比漏报贵。
            _nest = ''
        beat.nest_path = (None if not _nest
                          else ('' if _nest == '-' else _nest))
        # 行尾注释（C）：原文（含 `#`），空 = 没有。
        # ⚠ 与 `Nest` 同一个道理：用 `g`（会清洗并记进 `sanitized_cells`），
        # 不能用 `g_text` —— 后者不留痕，正是"静默改数据"
        # （`test_cleaning_is_recorded[Inline]` 当场抓到）。
        # 被清洗过 = 这一格本来不是一条干净的注释 → **丢掉**（不清洗过的才可信）。
        _inline = g('Inline')
        if 'Inline' in sanitized:
            _inline = ''          # 清洗过 = 不可信（同 `Nest` 的道理）
        beat.inline_comment, beat.cond_comment = parse_inline_cell(_inline)
        (beat.raw, beat.code, beat.ordered,
         marked_blocks) = _split_code_cell(
             beat.code, expand_code_blocks(beat.code_blocks))
        # 单元格里的块标记是**权威**（写表时打的）；隐藏列只在标记缺失
        # （老表 / 手工新建行）时兜底。
        beat.code_blocks = marked_blocks or beat.code_blocks

        # —— 结构化列 → 内部字段 ——
        # 新版表：背景 / 立绘 / 位置 / 转场 / 退场 / 音乐 / 音效 各自一列，
        # 每列都能下拉。旧版表（Show/Hide/At/…）的列名在 LEGACY_* 里，
        # 这里一并兼容，用户手上的老表升级后照样读得出来。
        beat.scene = _spec_image(g('Scene'))
        beat.scene_at = _spec_at(g('Scene'))
        beat.at = g('At')
        beat.with_ = g('With')
        beat.show = _join_show_items([
            {'clause': '', 'image': img, 'at': at}
            for img, at in _split_spec_list(g('Show'))
        ])
        beat.hide = _join_hide_items([
            {'image': img, 'with': w}
            for img, w in _split_spec_list(g('Hide'), sep='@')
        ])
        # 「舞台（高级）」列：结构化列表达不了的指令（复杂 show 子句、
        # 自带位置的立绘、带转场的退场）都在这里；hydrate 会把它并回
        # 结构化字段，渲染时再靠去重避免重复生成。读表时必须读它，否则
        # 位置变换这类信息在"打开 rpy → 导出"往返里会丢。
        beat.stage = g('Stage')
        if g('StageOrder'):
            beat.stage_order = g('StageOrder')
        if g('OpOrder'):
            beat.op_order = g('OpOrder')
        beat.code_blocks = parse_code_blocks_cell(g('CodeBlocks'))

        # —— 音频列 → 内部字段（含「停止/排队」伪值）——
        _fill_audio_from_cells(beat, 'music', g('BGM'), g('BGMParams'))
        _fill_audio_from_cells(beat, 'sound', g('SFX'), g('SFXParams'))

        # 旧表（26 列）自动迁移。
        #
        # 注意判据：`Show/Hide/At/With` **当前表也有**（它们是结构化下拉列），
        # 所以不能拿 `LEGACY_STAGE_COLUMNS` 里任意一个键当"这是老表"的信号
        # ——那样每一行新表都会走进来，然后 `beat.raw = g('Raw')` 把从
        # CodeBlock 拆出来的 raw 行（`show screen` / `pause` / `with`）
        # **整批清空**，导出的代码就少了这些语句。只认真正的老表专属列。
        legacy_keys = set(LEGACY_STAGE_COLUMNS) - set(BEAT_FIELDS)
        if any(k in row for k in legacy_keys):
            if not beat.show:
                beat.show = g('Show')
            if not beat.hide:
                beat.hide = g('Hide')
            # 老表的 Raw 列是**额外**内容，追加而不是覆盖（老表可能同时
            # 有 CodeBlock 与 Raw 两列）。
            legacy_raw = g('Raw')
            if legacy_raw:
                beat.raw = _append_line(beat.raw, legacy_raw)
                beat.ordered.extend(
                    ('raw', ln) for ln in _split_lines(legacy_raw))
        if any(k in row for k in LEGACY_AUDIO_COLUMNS):
            if not beat.music and not beat.sound:
                _fill_audio_legacy(beat, g)

        # 「音乐」/「音效」文本列（beat.bgm / beat.sfx）是真相源：从已填好的
        # 结构化字段**合成回** DSL 文本，保证"老表迁移"与"打开 rpy → 导出"
        # 两种来源下文本列都有一致的人话/原生写法，便于策划复核与二次编辑。
        beat.bgm = compose_audio_text(beat, 'music')
        beat.sfx = compose_audio_text(beat, 'sound')

        # —— 「每参数一列」：把**改了的那一格**折回头行（M4 第二阶段）——
        #
        # 动态列名形如 `mycue·intensity`（`key=value` 参数）或
        # `camera·layer@0`（**位置**参数，下标进列名）。它们是
        # **编辑面不是第二份数据**：折进头行之后，渲染 / 校验 / 协作 / 卡片
        # 四条路都不需要知道"参数列"存在过 —— 它们看到的只是"头行变了"。
        #
        # 折到**哪一个字段**由 `_head_field()` 决定（与渲染层的优先级同源）：
        # 循环头归 `loop_header`、相机头归 `camera_header`、声明的自定义语句归
        # `spec_head` —— 折错字段就是"改了没效果"，而且看不出来。
        #
        # ⚠ 折进时用的是 `fold_dynamic_columns` 的**等式守卫**：值没变 → 头行
        # 逐字节原样（"打开表再导出一个字节都不动"照旧成立）；`target="ling"`
        # 这种带引号的原文也不会被吃掉引号；位置参数只替换那一个 token
        # （绝不写成语义错的 `layer=background`）。
        #
        # ⚠ 动态格子也过 `sanitize_single_line_cell` 并**留痕**：它们最终会
        # 被拼进一行语句，真换行漏出去就是产物里的物理换行。
        from storygraph.story.spec_args import fold_dynamic_columns
        dyn: dict = {}
        for key, val in (row or {}).items():
            if not is_dynamic_column(key):
                continue
            raw = '' if val is None else str(val)
            clean = sanitize_single_line_cell(str(key), raw).strip()
            if clean != raw.strip():
                sanitized.append(str(key))
            dyn[str(key)] = clean
        if dyn:
            target = beat._head_field()
            base_head = str(getattr(beat, target, '') or '')
            if not base_head:
                base_head = next(
                    (ln for ln in str(beat.opaque or '').split('\n')
                     if ln.strip()), '')
            folded = fold_dynamic_columns(base_head, dyn)
            if folded != base_head:
                setattr(beat, target, folded)

        # 记下"哪些格被清洗过"（见 `Beat.sanitized_cells`）：**清洗必须留痕**，
        # 否则就是静默改数据 —— 值里的换行被压掉，人却不知道。
        #
        # ⚠ 必须放在**所有 `g()` 调用之后**：结构化列（Scene / Show /
        # At / With / Hide / BGM / SFX / 两个内部顺序列 / CodeBlocks）
        # 是在 `cls(...)` **之后**才读的。放在构造函数后面那一行，这 12 列
        # 的留痕会全部丢掉 —— 而 W114 只在"有留痕"时才报，于是**恰好是
        # 最常被粘脏的那几列**（立绘 / 位置 / 音乐）静默无声。
        # （这条是参数化用例当场抓到的：`test_cleaning_is_recorded[Show]` 等 12 条红。）
        beat.sanitized_cells = sorted(set(sanitized))
        return beat

    @property
    def order_ops(self) -> list:
        """
        本拍操作的**类别**顺序（`['audio', 'stage']`）——`order_tokens` 的粗粒度视图。

        保留它是因为已有调用方（快照/校验/测试）只关心"音频在舞台前还是后"。
        要逐条还原请用 `order_tokens`。
        """
        cats = []
        for t in self.order_tokens:
            c = 'stage' if t in ('scene', 'show', 'hide') else t
            if c not in cats:
                cats.append(c)
        return cats

    @property
    def order_tokens(self) -> list:
        """
        本拍全部操作的**verb 级**顺序，用于逐条还原（保住同类内交错）。

        形如 `['scene', 'audio', 'show']`——这正是
        `scene bg X / play music Y / show ling happy` 的源码顺序。

        为什么不能只记类别（`stage,audio`）：那样渲染时会得到一个
        "整块舞台 + 整块音频"，把 `play` 挤到 `show` 后面
        （demo ch01 实测：`play music night` 原本在 `show eileen happy` 之前）。
        只记类别 + 成组输出 = 顺序错了但看起来"差不多"，
        而顺序本身是演出语义。

        兼容两种存法：
          · **verb 级**（新）：`scene,audio,show` —— 直接用；
          · **类别级**（老表）：`stage,audio` —— 用 `stage_order` 把
            `stage` 展开成具体 verb，尽量还原。
        完全没有顺序信息时返回空，让调用方走历史固定顺序。
        """
        raw = [t.strip() for t in (self.op_order or '').split(',') if t.strip()]
        if not raw and not (self.stage_order or '').strip():
            return []

        stage_seq = [t.strip() for t in (self.stage_order or '').split(',')
                     if t.strip()]
        out = []
        used = 0
        seen_audio = seen_voice = False
        for t in raw:
            if t in ('scene', 'show', 'hide'):
                out.append(t)
                used += 1
            elif t == 'stage':
                # 老格式：展开 StageOrder 里还没用掉的下一个 verb
                if used < len(stage_seq):
                    out.append(stage_seq[used])
                    used += 1
                else:
                    for v in ('scene', 'show', 'hide'):
                        if v not in out:
                            out.append(v)
            elif t == 'audio' and not seen_audio:
                out.append('audio')
                seen_audio = True
            elif t == 'voice' and not seen_voice:
                out.append('voice')
                seen_voice = True
        # stage_order 里还剩的 verb 补在最后
        for v in stage_seq[used:]:
            out.append(v)
        return out

    @property
    def stage_verbs(self) -> list:
        """
        这一拍的舞台 verb 顺序（`scene` / `show` / `hide`）——**缺的补齐**。

        规矩与渲染器**同一条**（`beat_render.render_beat_to_rpy` 里那句
        "绝不因为顺序信息不全就丢掉操作"），但从前只有渲染器照做，舞台快照
        与演出预览只回放"列出来的" verb —— 于是同一次保存出现两种真相：

            卡片上：立绘 ling angry（用户刚加上的）
            当前舞台：立绘（台上没人）      ← 快照没看到 show
            演出预览：台上没人              ← 同上
            产物：`show ling angry at center` ← 渲染器补齐了，其实是对的

        用户 2026-10-05 实测报的"我已经保存该卡片了，可是当前舞台变化没有生效，
        并且舞台演出预览没有变化"就是它。触发条件很常见：卡片加/删立绘只改
        `beat.show`，而 `stage_order` / `op_order` 还停在 `'scene'`
        （解析时那一拍只有 `scene black`）。

        "看到的 ≠ 生成的"是这一层最贵的一类缺陷（见 `compose_stage` 的契约），
        所以判据只写一处，两个消费者共用。
        """
        recorded = [t for t in self.order_tokens
                    if t in ('scene', 'show', 'hide')]
        for verb in ('scene', 'show', 'hide'):
            if verb not in recorded and str(getattr(self, verb, '') or '').strip():
                recorded.append(verb)
        return recorded

    def merged_stage_order(self) -> str:
        """
        `stage_order` 的**归一化**形式：把 `stage_verbs` 里缺的 verb 补上
        （**只算、不写回**）。

        与 `sync_stage_order` 分开是为了让"归一化"能出现在**不改数据**的地方
        （例如快照/指纹：对齐顺序列不算用户改动，见
        `session_table.snapshot`）。
        """
        merged = [v.strip() for v in (self.stage_order or '').split(',')
                  if v.strip()]
        for verb in self.stage_verbs:
            if verb not in merged:
                merged.append(verb)
        return ','.join(merged)

    def sync_stage_order(self) -> str:
        """
        把 `stage_verbs` 写回 `StageOrder` 列（**只追加，绝不覆盖已有顺序**）。

        为什么要写回（而不是只靠读的时候补齐）：`StageOrder` 是**随表走**的
        列 —— 表里那个值如果永远少一个 `show`，下一次从表回来的人（或别的
        消费者）看到的还是"这一拍没有立绘"。已有顺序是解析时的真迹
        （`scene` 在 `Scene` 列，`Stage` 列推导不出来），所以只补不覆盖
        ——与 `beat_build._hydrate_stage` 同一条规矩。
        """
        merged = [v.strip() for v in (self.stage_order or '').split(',')
                  if v.strip()]
        for verb in self.stage_verbs:
            if verb not in merged:
                merged.append(verb)
        if merged:
            self.stage_order = ','.join(merged)
        return self.stage_order

    def is_empty(self) -> bool:
        """这一行没有任何内容（用户留空的行不该生成代码）。

        纯 `stop music` / `stop sound` 没有"要播的资产"，但它是明确的
        演出指令（转场 / 进战斗的关键节点），必须算有内容，否则会被丢掉。
        注释 / 代码块同理——它们都是要回写的演出内容。

        `menu`（菜单拍）**永远算有内容**：它可能一个字都没有
        （`menu:` 无标题、选项在后面的行里），但那一行本身是整块菜单的
        结构锚点——按"空行"丢掉它，选项就变成了裸旁白。
        """
        if self.kind in (KIND_MENU, KIND_MENU_OPTION):
            return False
        # **label 锚点**（2026-10-05）：源文件里 `label X:` 后面没有任何
        # 可建模的语句时（`return`、或 label 是空的），构建层会造一张
        # **只有 label 的空拍**来保住名字（见 `beat_build._fill_empty_labels`
        # / `_fill_labels_without_beats`）。
        #
        # 这张拍一行内容都没有，按"空行"丢掉它的后果比留着严重得多：
        # 渲染层按拍分组出 label，一张拍都没有时会退回 `label start:`
        # （见 `render_beats_to_rpy`）—— 于是
        #
        #   · 所有 `jump X` **静默失效**；
        #   · 产物里凭空多出一个 `label start`，而 `start` 是 Ren'Py 的
        #     游戏入口，项目里几乎必然已有一个 → **重复 label，起不来**。
        #
        # 与 menu 锚点同理：这一行是**结构**，不是内容。
        if self.label and not self.has_content():
            return False
        # 文件级保真块也是"内容"（2026-10-05，拓展边界③）：
        # 一个只有 `init python:` 的文件解析出**一张空拍**来携带它，
        # 这一位不参与判空的话，`_read_beats_from_ws`（刷新候选表）
        # 会把这行当空行丢掉 —— 于是"刷新一下下拉框"就静默删掉代码。
        return not self.has_content() and not self.file_blocks

    def has_content(self) -> bool:
        """
        这张拍**自己**有没有演出内容？（不看它携带的文件级保真块）

        为什么要和 `is_empty()` 分开（2026-10-05，拓展边界③）：
        纯 `init python:` 的文件会造一张空拍来携带代码，而这张拍在
        **渲染分组**时不该产生一个空壳 label（源文件里根本没有 label）。
        `render_beats_to_rpy` 用这一位把"只用来携带的拍"认出来。
        """
        return any((
            self.text, self.speaker, self.scene, self.show, self.hide,
            self.stage, self.bgm, self.sfx,
            self.music, self.sound, self.voice, self.notes,
            self.comment, self.raw, self.code,
            self.music_action == 'stop',
            self.sound_action == 'stop',
        ))



# ===================== 结构化字段 ⇄ DSL 文本 =====================

def compose_stage(beat: 'Beat', symbols=None) -> str:
    """
    结构化舞台字段 → Stage 单元格文本。

    给了符号表就优先合成人话（`[显示] 凌跃捷 开心 左`），但**只在往返
    无损时才这么做**（见 dsl.stage_word_if_lossless）——猜错一个表情，
    比显示程序写法糟糕得多。
    """
    from storygraph.story.dsl import Symbols, stage_word_if_lossless
    symbols = symbols if isinstance(symbols, Symbols) else None

    ops = []
    if beat.scene:
        line = f"scene {beat.scene}"
        # 背景自己的变换是 `scene_at`（`scene bg bg9_5 at bgfit`）。
        # 这里曾错用 `beat.at`——那是**立绘**的位置，于是同一张表里
        # "Stage 列显示的"和"生成的 .rpy"对不上（背景变换丢失、
        # 立绘位置串到背景上）。渲染走 `beat_render._stage_lines`，
        # 两边必须同源。
        if beat.scene_at:
            line += f" at {beat.scene_at}"
        if beat.with_:
            line += f" with {beat.with_}"
        ops.append(('scene', line))
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            ops.append(('show', f"show {item['clause']}"))
            continue
        line = f"show {item['image']}"
        # 立绘的位置：自带 `@` 优先，其次才是整拍的「位置」列——
        # 与 `_stage_lines` 一致（少这一档就会出现"表里没位置、生成的有"）。
        at = (item.get('at') or '').strip() or (beat.at or '').strip()
        if at:
            line += f" at {at}"
        ops.append(('show', line))
    for item in _split_hide_items(beat.hide):
        line = f"hide {item['image']}"
        if item.get('with'):
            line += f" with {item['with']}"
        ops.append(('hide', line))
    # 转场没背景可挂时挂哪儿：最后一条立绘；连立绘都没有就单独成行。
    # 与 `beat_render._stage_lines` 一致 —— 那边是生成 .rpy 的真源，
    # 少这一档就会出现"Stage 列看不到转场、生成的脚本里有"。
    if beat.with_ and not beat.scene:
        show_at = [i for i, (v, _l) in enumerate(ops) if v == 'show']
        hide_at = [i for i, (v, _l) in enumerate(ops) if v == 'hide']
        if show_at:
            i = show_at[-1]
            ops[i] = ('show', ops[i][1] + f" with {beat.with_}")
        elif not hide_at:
            ops.append(('show', f"with {beat.with_}"))
    if beat.transform and not beat.at:
        ops.append(('with', f"with {beat.transform}"))
    # 注意：raw（`with dissolve` / `pause` / `jump x` 这类原生语句）**不进
    # Stage**——它们属于代码块后门（compose_code），两条通道各出一份，
    # 否则同一行语句会被生成两次。

    # 顺序按 StageOrder 还原（hide+show 重置立绘与反序语义相反）
    if beat.stage_order:
        order = [v.strip() for v in beat.stage_order.split(',') if v.strip()]
    else:
        order = []
    if order:
        buckets = {}
        for verb, line in ops:
            key = verb if verb in ('scene', 'show', 'hide') else 'raw'
            buckets.setdefault(key, []).append((verb, line))
        ordered = []
        for key in order:
            ordered.extend(buckets.pop(key, []))
        if 'raw' in buckets:
            ordered.extend(buckets.pop('raw'))
        for key in ('scene', 'show', 'hide'):
            ordered.extend(buckets.pop(key, []))
        ops = ordered

    if symbols is not None:
        out = []
        for verb, line in ops:
            if verb in ('show', 'hide'):
                head = line.split(None, 1)[1] if ' ' in line else ''
                spec, at, with_ = _parse_native_stage_tail(head)
                human = stage_word_if_lossless(symbols, verb, spec, at, with_)
                if human:
                    out.append(human)
                    continue
            out.append(line)
        return '; '.join(out)
    return '; '.join(line for _v, line in ops)



_NATIVE_TAIL_RE = re.compile(
    r'^(?P<spec>.+?)(?:\s+at\s+(?P<at>[\w.]+))?'
    r'(?:\s+with\s+(?P<with>[\w.]+))?$')



def _parse_native_stage_tail(head: str) -> tuple:
    """从 `ling close_happy at left with dissolve` 里拆出 (spec, at, with)。"""
    head = (head or '').strip()
    if not head or any(k in head.split()
                       for k in ('behind', 'as', 'onlayer', 'zorder',
                                 'expression')):
        return '', '', ''
    m = _NATIVE_TAIL_RE.match(head)
    if not m:
        return '', '', ''
    return (m.group('spec') or '').strip(), (m.group('at') or '').strip(), \
        (m.group('with') or '').strip()



def compose_audio_text(beat: 'Beat', channel: str) -> str:
    """结构化音频字段 → 一列 DSL 文本（旧接口，供兼容与测试使用）。"""
    name, params = compose_audio_cell(beat, channel)
    label = '音乐' if channel == 'music' else '音效'
    # 纯动作（停止 / 排队）：结构化字段里 name 为空、action 标记了动作。
    # 必须产出能被 parse_audio 原样认回去的写法，否则停在「音乐」列的
    # 文本会在"导出→导入"里变回普通播放。
    if not name:
        action = beat.music_action if channel == 'music' else beat.sound_action
        if action == 'stop':
            return f'[停止{label}]'
        if action == 'queue':
            return f'[{label}(queue)]'
        return ''
    if name in AUDIO_PSEUDO['BGM'] or name in AUDIO_PSEUDO['SFX']:
        # name 形如 `（停止音乐）` / `（排队播放）`：翻成干净动词
        # （`[停止音乐]` / `[音乐(queue)]`），能被 parse_audio 认回去。
        if name.startswith('（停止'):
            return f'[停止{label}]'
        return f'[{label}(queue)]'
    verb = label
    action = beat.music_action if channel == 'music' else beat.sound_action
    if action == 'queue':
        verb = f'{label}(queue)'
    text = f'[{verb}] {name}'
    if params:
        text += f' {params}'
    return text



# ---------------------------------------------------------------------------
#  结构化列 ⇄ 内部字段（每列都能下拉，所以拆开写；语义都写在这里）
# ---------------------------------------------------------------------------

def _split_spec_list(text: str, sep: str = '@') -> list:
    """
    `ling happy@f11; lan sideeye` → [('ling happy', 'f11'), ('lan sideeye', '')]

    `@` 后面在立绘/背景列里是**位置变换**，在退场列里是**转场**——
    同一个符号，两列各自的直觉含义。
    """
    out = []
    for part in (text or '').replace('\n', ';').split(';'):
        part = part.strip()
        if not part:
            continue
        if sep in part:
            img, _, extra = part.partition(sep)
            out.append((img.strip(), extra.strip()))
        else:
            out.append((part, ''))
    return out



def _spec_image(spec: str) -> str:
    """`bg room@bgfit` → `bg room`"""
    return _split_spec_list(spec)[0][0] if (spec or '').strip() else ''



def _join_at_targets(targets) -> str:
    """
    `at` 目标列表 → 文本。

    多个目标之间**保留逗号**（`at left, right`）：空格连接会得到
    `at left right`，而 Ren'Py 的 `at` 是"逗号分隔的变换列表" ——
    少了逗号语义就变了。

    单个目标原样返回（这是绝大多数情况：`at h11` / `at leftish`）。
    """
    parts = [str(t).strip() for t in (targets or []) if str(t).strip()]
    return ', '.join(parts)



def _spec_at(spec: str) -> str:
    """`bg room@bgfit` → `bgfit`"""
    items = _split_spec_list(spec)
    return items[0][1] if items else ''



def compose_scene_cell(beat: 'Beat') -> str:
    """
    背景列：`bg room`；背景有自己的位置变换时写 `bg room@bgfit`。

    背景的变换刻意**不跟「位置」列共用**：策划写「位置=左」时想的一定是
    立绘站左边，不是把背景挪走。所以背景要变换就在这一列里用 `@`。
    """
    if not beat.scene:
        return ''
    return f"{beat.scene}@{beat.scene_at}" if beat.scene_at else beat.scene



def compose_show_cell(beat: 'Beat') -> str:
    """
    立绘列：`ling happy`；自带位置写 `ling happy@left`。

    **这一列承载"自带位置的立绘"**（`图@位置`）。`compose_stage_extra`
    不再重复写它——两者必须严格互补，见那里的说明。

    （历史坑：这里曾经 `if item.get('at'): continue`，把带位置的立绘
    整条跳过，指望「舞台（高级）」列兜底。结果是**位置信息在 Excel
    往返里直接丢掉**：真实项目 `chapter1_4` 往返掉了 32 条带 `at` 的
    show，而且和 `compose_stage_extra` 一起构成"两列各写一遍"的双写。
    一列漏写 + 另一列重复写，是同一个根因的两面。）
    """
    parts = []
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            # 复杂子句（behind / onlayer / zorder / 多目标 at…）由
            # 「舞台（高级）」列承载 —— 两列必须严格互补，各写一份就会双写
            # （见 `compose_stage_extra` 的 docstring）。
            # 注意 `expression` 项**不是** clause（`_parse_show_expression`
            # 返回 clause=''），它走下面那条路。
            continue
        img = (item.get('image') or '').strip()
        if not img:
            continue
        at = (item.get('at') or '').strip()
        if item.get('is_expression'):
            line = _expression_clause(item)
            if not item.get('at'):
                # 位置可能落在"整拍"那格（`位置` 列）上
                fallback = (beat.at or '').strip()
                if fallback:
                    line += f' at {fallback}'
            parts.append(line)
            continue
        parts.append(f'{img}@{at}' if at else img)
    return '; '.join(parts)



def compose_hide_cell(beat: 'Beat') -> str:
    """
    退场列：`ye`，带转场写 `ye@dissolve`。

    **这一列承载"带转场的退场"**（`图@转场`），`compose_stage_extra` 不再
    重复写它。理由与立绘列的 `图@位置` 完全一样：一个格子装不下两个语义，
    而两列都写就会各输出一次，读回来变成
    `hide='e@dissolve; e@dissolve'`（实测到的重复退场）。
    """
    parts = []
    for item in _split_hide_items(beat.hide):
        img = (item.get('image') or '').strip()
        if not img:
            continue
        parts.append(f"{img}@{item['with']}" if item.get('with') else img)
    return '; '.join(parts)



def compose_audio_cell(beat: 'Beat', channel: str) -> tuple:
    """
    音频字段 → (音乐/音效列, 参数列)。

    停止 / 排队 这两种"动作"用**伪值**塞进同一个下拉列
    （`（停止音乐）` / `（排队播放）`）。策划少记一列，也不用手打动作词。

    **参数列必须照写，哪怕动作是停止**（2026-09-28 实测的数据丢失）：

        `stop music fadeout 1.0`  ——(导出 xlsx)→ 音乐=`（停止音乐）`、参数=空
                                  ——(读回)→ `stop music`
                                  ——(导出 .rpy)→ **`stop music`（fadeout 丢了）**

    原来这里在伪值分支直接 `return back.get(action, ''), ''`，
    把 `params` 吃掉了；读回时参数列是空的，`_fill_audio_from_cells`
    又用空参数覆盖掉已经解析出来的 `fadeout 1.0`。
    真实章节 `chapter1_3` 里 **6 条 `stop music fadeout 1.0` 全中招**。
    """
    if channel == 'music':
        name, params, action = beat.music, beat.music_params, \
            beat.music_action
        pseudo = AUDIO_PSEUDO['BGM']
    else:
        name, params, action = beat.sound, beat.sound_params, \
            beat.sound_action
        pseudo = AUDIO_PSEUDO['SFX']
    if action in ('stop', 'queue') and not name:
        back = {v: k for k, v in pseudo.items()}
        return back.get(action, ''), params or ''
    return name, params



def _fill_audio_from_cells(beat, channel: str, value: str, params: str):
    """音乐/音效列 + 参数列 → 内部字段（含伪值识别）。"""
    pseudo = AUDIO_PSEUDO['BGM' if channel == 'music' else 'SFX']
    raw = (value or '').strip()

    # 单元格是空的：**不**改写任何结构化字段——保持调用方已有的状态。
    # 否则会把下面的"无值"误当"清空"，把直接构造的 Beat 上已经标好的
    # music_action='stop' 之类冲掉，导致 stop 指令凭空消失。
    if not raw:
        return

    # 兼容上一版把整句 DSL 写在音频列里的表（`[音乐] spring fadein 1.0`）
    if raw.startswith('['):
        from storygraph.story.dsl import parse_audio
        ops = parse_audio(raw, channel)
        if ops:
            name = ops[0].name
            action = ops[0].action if ops[0].action != 'unknown' else 'play'
            params = ops[0].params or params
            if channel == 'music':
                beat.music, beat.music_params, beat.music_action = \
                    name, (params or '').strip(), action
            else:
                beat.sound, beat.sound_params, beat.sound_action = \
                    name, (params or '').strip(), action
            return

    if raw in pseudo:
        action, name = pseudo[raw], ''
    elif raw.lower() in ('stop', 'queue'):
        action, name = raw.lower(), ''
    else:
        action, name = 'play', raw
    # 参数列为空时**不要**覆盖已经解析出来的参数（老表没这列）。
    # 2026-09-28：`stop music fadeout 1.0` 因为这一句把 fadeout 丢了。
    keep = (params or '').strip()
    if not keep:
        keep = (beat.music_params if channel == 'music'
                else beat.sound_params) or ''
    if channel == 'music':
        beat.music, beat.music_params, beat.music_action = \
            name, keep, action
    else:
        beat.sound, beat.sound_params, beat.sound_action = \
            name, keep, action



def _fill_audio_legacy(beat, g):
    """旧表的 Music/Sound 三列组 → 内部字段。"""
    beat.music = g('Music')
    beat.music_params = g('MusicParams')
    beat.music_action = g('MusicAction') or 'play'
    beat.sound = g('Sound')
    beat.sound_params = g('SoundParams')
    beat.sound_action = g('SoundAction') or 'play'



def compose_stage_extra(beat: 'Beat', symbols=None) -> str:
    """
    结构化列**表达不了**的舞台指令 → 「舞台（高级）」列。

    规则与 `compose_show_cell` / `compose_hide_cell` **严格互补**，
    合起来必须恰好覆盖每个舞台指令一次（这是一条契约，不是约定俗成）：

        复杂 show 子句（clause）  → 只进 Stage
        带 `at` 的立绘            → 只进「立绘」列的 `图@位置`
        带转场的退场              → 只进「退场」列的 `图@转场`
        其余（pause / 原生语句）  → 只进 Stage

    **为什么这条互补关系必须成立**（2026-09-28 实测事故）：
    同一句舞台指令被两列各写一次时，读回来会往 `beat.show` / `beat.hide`
    里**追加两份**（一份来自结构化列、一份来自 Stage 的 hydrate），
    渲染时两条都出——真实项目 `chapter1_4.xlsx` 里这种情况有 10 行，
    生成的脚本里就出现了 `show ye hi at i11` + `show 叶怀瑾 hi at i11`
    这种**一条重复 + 一条非法中文 tag** 的组合。

    所以本函数**只**输出"结构化列表达不了"的东西。历史上它多输出了
    `show X at Y` 与 `hide X with Y` 两种**其实表达得了**的指令，
    那正是双写的来源。
    """
    from storygraph.story.dsl import Symbols, stage_word_if_lossless
    symbols = symbols if isinstance(symbols, Symbols) else None

    # 只收"结构化列表达不了"的：目前只有复杂 show 子句。
    # 带 `at` 的立绘走「立绘」列，带转场的退场走「退场」列，
    # 两者都由结构化列承载，这里一律不碰（见函数 docstring）。
    ops = []
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            ops.append(('show', f"show {item['clause']}"))
    if not ops:
        return ''

    if symbols is not None:
        out = []
        for verb, line in ops:
            head = line.split(None, 1)[1] if ' ' in line else ''
            spec, at, with_ = _parse_native_stage_tail(head)
            human = stage_word_if_lossless(symbols, verb, spec, at, with_)
            out.append(human or line)
        return '; '.join(out)
    return '; '.join(line for _v, line in ops)




def compose_code(beat: 'Beat') -> str:
    """
    CodeBlock = 原生语句（raw）+ python 代码（code），按源码交错顺序拼。

    这是唯一的"保真后门"：里面有东西，编译器就原封不动抄进脚本。

    有 `ordered` 时按**源码里的交错顺序**拼，并恢复每行的缩进
    （`ordered` 的第三位；见 `_relative_indents`）。

    **每一行都带块标记**（见 `CODE_BLOCK_OPEN` / `CODE_BLOCK_BODY`）：
    「代码」列一个格子装多行文本，`python:` 块的边界在纯文本里表达不出来
    （`python:` 关键字在单元格里已经被剥掉），而块结构决定 `for` / `if`
    的嵌套层级 —— 猜错就是静默改语义。标记由隐藏列 `CodeBlocks`
    （或 `.rpy` 解析时的行数）确定，渲染层 `render_code_block` 按标记出
    `python:` 块；写进 Excel 前由 `compose_code_cell` 把标记剥掉。

    返回文本**不带整体缩进**（缩进由渲染层按上下文加）。
    """
    lines = _ordered_code_lines(beat)
    if lines is None:
        lines = [ln for ln in _split_lines(beat.raw)]
        lines += [ln for ln in _split_lines(beat.code)]
    return '\n'.join(mark_code_blocks(lines,
                                      getattr(beat, 'code_blocks', None)))


#: 代码块标记：**行首**这两个私用区字符表示"这行属于一个 `python:` 块"。
#: `CODE_BLOCK_OPEN` = 块的第一行（渲染时出 `python:` 头），
#: `CODE_BLOCK_BODY` = 块体的后续行。私用区字符策划打不出来，
#: 也不是控制字符（`openpyxl` 拒收控制字符，见 `core.statement.MARKER`）。
CODE_BLOCK_OPEN = '\ue001'
CODE_BLOCK_BODY = '\ue002'


def mark_code_blocks(lines: list, blocks) -> list:
    """
    按 `blocks`（1-based 闭区间）给行打上块标记；`blocks` 为空则原样返回。

    兜底（老表 / 手工新建行）：`blocks` 为空时**不打标记**，
    渲染层会退回"按缩进推断"那条路（见 `render_code_block`）——
    最坏是块结构没保住，而不是产物改坏。
    """
    ranges = [(int(a), int(b)) for a, b in (blocks or []) if int(b) >= int(a)]
    if not ranges:
        return list(lines)
    out = []
    for idx, line in enumerate(lines, start=1):
        flag = ''
        for start, end in ranges:
            if start <= idx <= end:
                flag = CODE_BLOCK_OPEN if idx == start else CODE_BLOCK_BODY
                break
        out.append(flag + line)
    return out



def unmask_code_block_markers(text: str) -> str:
    """
    去掉**每一行**行首的块标记（写 Excel、比对、显示都用它）。

    要逐行做：`compose_code` 返回的是多行文本，只 `lstrip` 整个字符串
    只能去掉第一行那个标记，其余行的标记会原样写进单元格（实测）。
    """
    lines = [str(line or '').lstrip(CODE_BLOCK_OPEN + CODE_BLOCK_BODY)
             for line in str(text or '').split('\n')]
    return '\n'.join(lines)



#: 多个文件级保真块之间的分隔：一整行只放这个私用区字符。
#: 私用区字符策划打不出来、也不是控制字符（`openpyxl` 拒收控制字符），
#: 与 `CODE_BLOCK_OPEN` / `CODE_BLOCK_BODY` 同一套约定。
FILE_BLOCK_SEP = '\ue003'

#: `Inline` 单元格里"条件行注释"的**前缀与分隔**（见 `Beat.cond_comment`）。
#: 自己的注释一定以 `#` 开头，所以 `@if ` 前缀不会撞。
INLINE_COND_SEP = '\ue004'
INLINE_COND_PREFIX = '@if '


#: 文件级保真块在 Excel 里的**载体表名**（隐藏表，一行一行地存）。
#:
#: 为什么是表而不是隐藏列（2026-10-05 实测）：Excel 单元格上限 **32767 字符**，
#: 而真实文件的顶层区间轻松超过它 —— `ball_moving_game.rpy` 38492、
#: `my_perferences.rpy` 50426，写进单元格**静默截断**（读回只剩 32767），
#: 于是"导出到新路径"丢东西、而且不报错。表没有这个限制，
#: 一行只放一行文本，连"单个块本身就超长"也一起解决。
FILE_BLOCK_SHEET = '文件级保真'
FILE_BLOCK_HEADERS = ['块号', '行文本']

#: 老版本的隐藏列名（那一版把整段塞进单元格）。**只读不写**：今天的表里
#: 已经没有它，但那一版生成的表还得读得回来（见 `excel_import`）。
LEGACY_FILE_BLOCK_COLUMN = 'FileBlocks'


def compose_inline_cell(beat) -> str:
    """
    `Inline` 单元格：`自己那行的注释` [ SEP `@if 条件行的注释` ]。

    为什么要塞进一格：两处注释都属于**同一拍**，而单元格是逐行属性 ——
    再开一列只为装 4 行数据不值得（真实项目实测条件行注释共 4 处）。
    自己的注释一定以 `#` 开头，`@if ` 前缀因此不会歧义。
    """
    own = str(getattr(beat, 'inline_comment', '') or '')
    cond = str(getattr(beat, 'cond_comment', '') or '')
    if not cond:
        return own
    return f'{own}{INLINE_COND_SEP}{INLINE_COND_PREFIX}{cond}' if own \
        else f'{INLINE_COND_PREFIX}{cond}'


def parse_inline_cell(value) -> tuple:
    """
    `Inline` 单元格 → `(自己那行的注释, 条件行的注释)`（`compose_inline_cell` 的逆）。

    **只在 `SEP + '@if '` 这个组合处切**（2026-10-05 修，
    `tools/hell_edit_rounds.py` 的 A 轴实测）：`compose_inline_cell` 写出来的
    分隔符后面**永远紧跟** `@if `，所以"组合"是自反的判据；正文里孤立的
    `\\ue004`（粘贴带进来的）因此变成**惰性字符**，注释不会被劈成两半。

    从前只要看到分隔符就切 ——

        '# 正文里有分隔符\\ue004后面还有字'
          → ('# 正文里有分隔符', '后面还有字')      ← 后半被当成条件注释、
                                                     还会污染下一格

    ⚠ 仍然无解的一种：正文里带着**完整组合** `\\ue004@if ` 且后面还有内容 ——
    那与工具自己写出来的形状逐字相同，无从区分（U+E004 打不出来，
    只有粘贴**本工具自己的单元格内容**才可能造出来）。
    """
    text = str(value or '')
    if not text:
        return '', ''
    combo = INLINE_COND_SEP + INLINE_COND_PREFIX
    if combo in text:
        own, cond = text.split(combo, 1)
        cond = INLINE_COND_PREFIX + cond
    elif text.startswith(INLINE_COND_PREFIX):
        # 只有条件注释（没有"自己那行"的注释）—— 前缀是唯一的判据
        own, cond = '', text
    else:
        own, cond = text, ''
    if cond.startswith(INLINE_COND_PREFIX):
        cond = cond[len(INLINE_COND_PREFIX):]
    elif cond and not own.startswith('#'):
        # 只有一段、又不以 `#` 开头 → 那就是条件注释（前缀可能被手改掉）
        own, cond = '', cond
    return own, cond


def file_blocks_to_rows(text) -> list:
    """
    文件级保真块的合并文本 → 隐藏表的行 `[(块号, 行文本), …]`（1-based 块号）。

    块与块之间用 `FILE_BLOCK_SEP` 那一行分隔（`join_file_blocks` 写的），
    这里按行拆开；**块内空行保留**（缩进块里的空行是内容）。
    """
    if not str(text or '').strip():
        return []
    rows = []
    for bi, block in enumerate(split_file_blocks(text), start=1):
        for ln in block.split('\n'):
            rows.append((bi, ln))
    return rows


def file_blocks_from_rows(rows) -> str:
    """隐藏表的行 → 合并文本（与 `file_blocks_to_rows` 严格互逆）。"""
    groups: dict = {}
    for bi, line in rows or ():
        try:
            key = int(bi)
        except (TypeError, ValueError):
            continue
        groups.setdefault(key, []).append('' if line is None else str(line))
    parts = []
    for key in sorted(groups):
        # **去掉块首尾的空行**（写表时 `split_file_blocks` 也是这么切的），
        # 否则每往返一次就多一层空行。
        body = groups[key]
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        if body:
            parts.append('\n'.join(body))
    return f'\n\n{FILE_BLOCK_SEP}\n\n'.join(parts)


def render_file_block(header: str, code: str) -> str:
    """
    一个顶层保真块 → **文件级**的文本（块头 + 缩进 4 的块体）。

    为什么缩进固定 4：`_collect_python_block` 已经把块体归一到"基准缩进 0"
    （相对层级保住、绝对排版丢掉），文件级的块体就是比块头深一层 ——
    Ren'Py 的块体一律按缩进认，4 是它的惯用宽度。

    为什么不留行尾空白：块体里那些是作者的排版，不是语义；
    留着的唯一后果是每次往返都产出一个 diff 块。
    """
    head = str(header or '').strip() or 'python:'
    body = [('    ' + ln) if ln.strip() else ''
            for ln in str(code or '').split('\n')]
    return '\n'.join([head] + body).rstrip('\n')


def join_file_blocks(blocks) -> str:
    """
    若干 `(header, code)` → 隐藏列文本（块之间空一行，人能读）。

    **块体为空的"块头"要丢掉**：`init python:` / `screen foo():` 后面什么都不写
    是 Ren'Py 的语法错误（它要求一个缩进块）。宁可不写，也不写出一个跑不起来的
    半截块 —— 这条是测试抓到的（`test_empty_body_is_not_invented`）。

    但**无块体的整句**要留下（2026-10-05，消掉"第三类内容会丢"那条缺口时
    扩到这里）：顶层区间里绝大多数是 `define e = Character(…)` /
    `default score = 0` / `# 注释` 这种**一行就是一条完整语句**的东西。
    它们没有块体，却正是以前被整批丢掉的那一类。
    判据因此从"块体非空"改成"**不以 `:` 收尾**，或者有块体"。
    """
    parts = []
    for head, code in blocks or ():
        if not str(head or '').strip():
            continue
        if not str(code or '').strip() and str(head).rstrip().endswith(':'):
            continue                      # 半截块：宁可不写
        parts.append(render_file_block(head, code))
    return f'\n\n{FILE_BLOCK_SEP}\n\n'.join(parts)


def join_file_block_parts(parts) -> str:
    """若干**已渲染**的块文本 → 合并文本（`split_file_blocks` 的逆操作）。"""
    keep = [str(p).strip('\n') for p in parts or () if str(p or '').strip()]
    return f'\n\n{FILE_BLOCK_SEP}\n\n'.join(keep)


def split_file_blocks(text) -> list:
    """隐藏列文本 → 一段段文件级文本（渲染时逐段原样写出）。"""
    raw = str(text or '')
    if not raw.strip():
        return []
    return [p.strip('\n') for p in raw.split(FILE_BLOCK_SEP) if p.strip()]


def compose_code_blocks_cell(beat: 'Beat') -> str:
    """
    `Beat.code_blocks` → 隐藏列文本。

    编码刻意选**人能读懂**的形式（万一有人展开隐藏列）：

        '1-11 13-17'   ← 第 1~11 行、第 13~17 行各是一个 python 块
        '3'            ← 只有第 3 行（单行块）
        ''             ← 没有块，或老表（渲染层退回按缩进推断）
    """
    parts = []
    for item in getattr(beat, 'code_blocks', None) or []:
        try:
            start, end = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if start <= 0 or end < start:
            continue
        parts.append(str(start) if start == end else f'{start}-{end}')
    return ' '.join(parts)



def expand_code_blocks(blocks) -> set:
    """
    `[(2, 3), (4, 5)]` → `{2, 3, 4, 5}`（把区间摊成行号集合）。

    `_split_code_cell` 需要"**每一行**是不是 python 代码"，而隐藏列记的是
    区间；不摊开的话组里只有首行算代码，`for i in range(2):` 的体
    （`a += i`）会被当成 raw，块又被拆散。
    """
    out = set()
    for a, b in blocks or []:
        try:
            start, end = int(a), int(b)
        except (TypeError, ValueError):
            continue
        out.update(range(start, end + 1))
    return out



def parse_code_blocks_cell(text) -> list:
    """
    隐藏列文本 → `[(start, end), ...]`（1-based 闭区间）。

    解析不出来就返回 `[]`（**不抛异常**）：这一列是工具维护的内部元数据，
    手改坏了不该让整张表读不进来 —— 返回空列表后渲染层会退回按缩进推断，
    最坏情况是"块结构没保住"，而不是"表打不开"。
    """
    out = []
    for token in str(text or '').replace(',', ' ').split():
        left, _, right = token.partition('-')
        try:
            start = int(left)
            end = int(right) if right else start
        except ValueError:
            continue
        if start > 0 and end >= start:
            out.append((start, end))
    return out



#: 内部标记：`$code`（`$` 后无空格）这一位用它承载（见 Statement.make_python）。
#: 约定只有一份，从 `core.statement` 取；它现在是一个**私用区字符**
#: （不是控制字符），所以能安全地进出 Excel 单元格。
_MARKER = MARKER


def compose_code_cell(beat: 'Beat') -> str:
    """
    写进「代码」列**单元格**的文本 = `compose_code` 剥掉块标记。

    2026-10-05：标记以前是控制字符 `\\x01`，`openpyxl` 拒收，所以这里
    必须把它 `.replace()` 掉 —— 代价是**读回来认不出"原文紧凑写 `$code`"**，
    真实项目 22 条 `$persistent…` 因此在往返里被改写成 `$ persistent…`。
    标记改成 Unicode 私用区字符（见 `core.statement.MARKER`）之后它就能
    安全地待在单元格里，写表与渲染**内容完全一致**。

    块标记（`CODE_BLOCK_OPEN` / `CODE_BLOCK_BODY`）**不写进这里**：
    它们承载的结构信息落在隐藏列 `CodeBlocks` 上，单元格里只留纯文本
    —— 策划/文案万一展开那一列，看到的还是能读的代码。
    """
    return unmask_code_block_markers(compose_code(beat) or '')



def _ordered_code_lines(beat: 'Beat') -> Optional[list]:
    """
    raw / code 按 `beat.ordered` 还原成交错行；无法可信还原时返回 None。

    为什么不能只靠"raw 拼 code"：真实项目里手机消息那种演出是
    `$ msg_add(…)` → `show screen` → `pause` → `hide screen` → `$ msg_add(…)`
    交替出现的。raw 与 code 各自保序，但**两者之间**的顺序只有 ordered 有；
    丢掉它导出的代码就变成"先全部 show/pause、再全部 $ msg_add"——同一张表
    导回去演出全乱。

    实现是把 `ordered` 当**消费序列**：逐条按记录的类型去对应的行队列里取。
    某类型的行不够了就说明 ordered 与字段不同步（表被手改过），
    此时返回 None 走历史行为，绝不猜。

    条目可以是二元组 `(kind, line)`（`.rpy` 解析那条路，行自带原始缩进）
    或三元组 `(kind, line, indent)`（**从 Excel 读回来**那条路：单元格里
    的行缩进是不可信的排版残留，按记录的真实层级重新缩进）。
    """
    ordered = getattr(beat, 'ordered', None) or []
    if not ordered:
        return None
    # `raw` / `code` 字段里存的是**不带块标记**的纯文本（写表之前会剥掉），
    # 而 `ordered` 可能带着标记（同一个 Beat 先写过表再渲染时）——
    # 比对前统一剥掉，两边口径才一致。
    #
    # 两个池子都按**逻辑语句**成组（`for …:\n    body` 是一个元素）：
    # `_split_code_cell` 写进 `code` 的就是逻辑语句，逐行比会对不上。
    pools = {'raw': [unmask_code_block_markers(ln)
                     for ln in _split_lines(beat.raw)],
             'code': [unmask_code_block_markers(ln)
                      for ln in _split_lines(beat.code)]}
    pos = {'raw': 0, 'code': 0}
    out = []
    for item in ordered:
        try:
            kind, line = item[0], item[1]
            indent = int(item[2]) if len(item) > 2 else None
        except (TypeError, ValueError, IndexError):
            return None
        pool = pools.get(kind)
        if pool is None:
            return None
        line = unmask_code_block_markers(line)
        if not line.strip():
            # **空行**：它的位置由 `ordered` 说了算，但**字段里没有它**
            # （`_split_code_cell` 不把空行写进 `raw` / `code`）。
            # 这里若把游标指向的池行也吐出来，就会**多输出一行**，
            # 而块结构是按行号区间记的（隐藏列 `CodeBlocks`）——
            # 行数一错，块边界就整体串位：实测
            # `mini_game_puzzle.rpy` 的块尾被算到下一行的 `call screen` 上，
            # 产物变成 `python:` 里包含 `call screen …` → **语法错误**。
            #
            # 游标**不动**、只补一个空行：空行不参与"逐行取用"的配对。
            out.append('')
            continue
        i = pos[kind]
        if i >= len(pool) or pool[i] != line:
            return None
        out.append(_reindent(line, indent))
        pos[kind] = i + 1
    # ordered 必须是"两个队列都恰好取完"，否则说明它不完整
    if pos['raw'] != len(pools['raw']) or pos['code'] != len(pools['code']):
        return None
    return out



def _reindent(line: str, indent) -> str:
    """
    按记录的真实层级给一行补缩进（`indent=None` = 原样）。

    为什么需要（2026-10-05 真实项目实测）：`python:` 块的块体在 Excel
    单元格里**没有层级信息** —— 一格里就是几行文本。写表时把头一行的
    缩进剥掉之后，读回来再也分不出"哪些行是块体的一部分"，
    渲染只能逐行补 `$`，一个 `python:` 块被拆成十几条 `$` 语句
    （实测 `mini_game1.rpy` 一个块拆出 53 条）。把层级记在 `ordered`
    里，块结构就能原样回来。
    """
    if indent is None:
        return line
    if not line.strip():
        return ''
    return ' ' * max(0, int(indent)) + line.lstrip()



def _group_logical_lines(tokens: list) -> list:
    """
    物理行 → **逻辑语句组**（把多行语句的续行粘回同一个元素）。

        ['for i in range(2):', '    a += i', 'for j in range(2):']
          → ['for i in range(2):\\n    a += i', 'for j in range(2):']

    为什么必须成组：`_split_code_cell` 是按行判断"这行是语句还是代码"的，
    而 `for i in range(2):` 这一行**两种解释都成立**（Ren'Py 也有 `for`）。
    成组之后判据变成"**下一行更深 → 这是个块头**"（以及括号/引号状态），
    单行无法回答的问题就有答案了。

    输入是 `(文本, 是否带块标记)`；返回 `[(组文本, 是否带标记), ...]`。
    """
    from storygraph.story.beat_render import _bracket_delta, _quote_state

    groups = []
    buf, marked, quote, depth = [], False, '', 0
    for text, flag in tokens:
        buf.append(text)
        marked = marked or flag
        depth += _bracket_delta(text, '')
        quote = _quote_state(text, quote)
        if depth <= 0 and not quote:
            groups.append(('\n'.join(buf), marked))
            buf, marked, depth = [], False, 0
    if buf:
        groups.append(('\n'.join(buf), marked))
    return groups



def _split_code_cell(cell: str, block_lines=None) -> tuple:
    """
    CodeBlock 单元格 → `(raw, code, ordered, blocks)`。

    表里只有**一个**格子装"原生语句 + python 代码"，所以读回来时必须重新
    分类。分类规则刻意保守（只认确定是语句的头），因为猜错的代价是
    生成语法错误的脚本：

      · `with dissolve` / `show screen x` / `pause` / `jump x` … → raw
      · `$ f()` / `f()` 裸表达式 / `python:` 块 → code

    `ordered` 一并给出，让渲染能按**原来的交错顺序**出代码（见
    `_ordered_code_lines`）；`blocks` 是"哪几行是一个 `python:` 块"的
    行区间（1-based 闭区间），落进隐藏列 `CodeBlocks`。

    **块标记（`CODE_BLOCK_OPEN` / `CODE_BLOCK_BODY`）优先于一切推断**：
    它由写表前的 `compose_code` 打上，是"这行属于一个 `python:` 块"的
    权威说法。有标记时不再猜缩进 —— 一个格子里混着 `$ a` / `$ b` /
    `python: c` 时，只有标记能把块边界说清楚。

    单元格里的**缩进不可信**（写表/用户手改都会动它），所以：

      · 存进 `raw` / `code` 字段的行一律去掉行首空白；
      · 真实缩进记进 `ordered` 的第三位（`(kind, line, indent)`），
        渲染时按它还原；
      · `$` 前缀一并剥掉（渲染时会按 `$` / `$ ` 两种写法还原）——
        留着会变成 `$ $ x`（实测的双美元 bug）。
    """
    lines = _split_code_lines(cell)
    if not lines:
        return '', '', [], []
    #: 隐藏列 `CodeBlocks` 说的"这些物理行是 python 代码"（1-based）。
    #: 单元格标记丢了（老表 / 手工复制行）时由它兜底 —— 没有它就只能按
    #: 行的形态猜，而 `for i in range(2):` 这种**一行两解**的写法猜不准。
    forced = {int(n) for n in (block_lines or [])}
    plain, in_block = [], []
    for no, ln in enumerate(lines, start=1):
        marker = ln[:1]
        opened = marker == CODE_BLOCK_OPEN
        inside = marker in (CODE_BLOCK_OPEN, CODE_BLOCK_BODY) or no in forced
        plain.append(ln[1:] if marker in (CODE_BLOCK_OPEN, CODE_BLOCK_BODY)
                     else ln)
        in_block.append((inside, opened))
    indents = _relative_indents(plain, in_block)
    # **分类要成组做，输出要逐行**（2026-10-05）：
    # `for i in range(2):` 单独一行时"是 Ren'Py 还是 Python"无从判断，
    # 但它的**下一行更深**就说明它是个块头 —— 判据只在组上成立。
    # 而 `ordered` 必须逐物理行记：`_ordered_code_lines` 是按行去
    # `raw` / `code` 两个池子里取用的（池子被 `_split_lines` 拆成物理行），
    # 记成逻辑行就取不上、整条 ordered 作废（实测：退回"raw 在前 code 在后"
    # 的兜底顺序，`$ game = 1` 跑到 `show screen` 后面去了）。
    kinds = []
    for group, _marked in _group_logical_lines(
            list(zip(plain, [f[0] for f in in_block]))):
        first = group.split('\n', 1)[0].strip()
        if not first:
            kinds.append('blank')
        elif first.startswith('$') or (
                not _looks_like_renpy_statement(first)
                and not first.endswith(':') and not _is_bare_renpy_verb(first)):
            kinds.append('code')
        else:
            kinds.append('raw')
        kinds.extend(['code'] * (group.count('\n')))

    raw, code, ordered = [], [], []
    blocks = []
    for idx, ln in enumerate(plain):
        s = ln.strip()
        inside = in_block[idx][0]
        if not s:
            if inside and ordered:
                kind, line, rel = ordered[-1]
                ordered.append((kind, '', rel))
            continue
        rel = indents[idx] if idx < len(indents) else 0
        if in_block[idx][1]:
            blocks.append([idx + 1, idx + 1])
        elif inside and blocks:
            blocks[-1][1] = idx + 1
        if kinds[idx] == 'code':
            # `$x`（`$` 后无空格）在**原文件里**很常见，而生成器默认写 `$ x`。
            # 逐字节保留这一位，否则每个 `$` 行都成一个 diff 块。
            # 标记只给**紧凑写法**：`$ x` 存裸文本，渲染时补 `$ ` 就是原样
            # （标记标记号，标记再补 `$` 就会变成 `$ $ x`）。
            if s.startswith('$'):
                body = s[1:]
                line = MARKER + body if (body and not body[:1].isspace()) \
                    else body
            else:
                line = s
            code.append(line)
            ordered.append(('code', line, rel))
        else:
            raw.append(s)
            ordered.append(('raw', s, rel))
    return ('\n'.join(raw), '\n'.join(code), ordered,
            [(a, b) for a, b in blocks])



def _looks_like_code(line: str) -> bool:
    """
    这一行**确定**是 Python 代码（`$ xxx` 或赋值），而不是"认不出来的
    Ren'Py 语句"？

    只认两类无歧义的写法：`$` 前缀、`名字 = 值`（含 `+=` / `//=` 这类）。

    保留它是给"证据不足、只能保守判断"的场合用：认不出来时宁可当 raw
    原样透传，也不要把作者的 Ren'Py 错误翻译成我们生成的 Python 错误。
    """
    s = str(line or '').strip()
    if not s:
        return False
    if s.startswith('$'):
        return True
    return bool(re.match(
        r'^[A-Za-z_][\w.\[\]\'"]*\s*(?:[+\-*/%|&^@]|//|>>|<<)?=(?!=)', s))


def _relative_indents(lines: list, in_block=None) -> list:
    """
    每个非空行的**相对块层级**（0 = 与上一句同层，1 = 进了下一层，…）。

    为什么需要（2026-10-05 真实项目实测）：Excel 单元格里没有块结构，
    `python:` 块的嵌套关系只体现为**缩进**。写表时 `compose_code_cell`
    会把缩进一起写进去（那是重建块体唯一的依据），可读回来时如果直接
    `strip` 掉，块结构就永久丢了 —— 渲染只能逐行补 `$`，一个
    `python:` 块被拆成十几条 `$` 语句（`mini_game1.rpy` 实测一个块拆出
    53 条，`for` / `if` 的嵌套体全被压到同一层）。

    **只认真块的层级，不认续行对齐**：

        positions = [                     ← 语句没写完（括号未收平）
                (200, 200), (600, 200),   ← 这是**续行对齐**，不是块层级
            ]                             ← 收平

    续行那几行比首行深，可它们在 Python 里没有语义 —— 归成块层级只会
    把 `positions = [` 后面的内容整体拖深一级。所以带括号的行按
    "括号平衡"算出续行，续行一律记 0；只有**上一句已经收平**之后
    缩进变深，才算真的进了一层。

    （`_render_code_block` 的 `_group_code_statements` 用同一套括号口径
    把续行重新粘成一条语句，两边必须一致。）
    """
    from storygraph.story.beat_render import _bracket_delta, _quote_state

    flags = list(in_block or [])
    rel = []
    stack = [0]
    depth = 0
    quote = ''
    prev_open = False
    for idx, ln in enumerate(lines):
        if not ln.strip():
            rel.append(0)
            continue
        inside = bool(flags[idx][0]) if idx < len(flags) else False
        indent = len(ln) - len(ln.lstrip())
        if inside or prev_open:
            # **续行**：保留它**原本的物理缩进**。续行的缩进在 Python 里没有
            # 语义（只是对齐括号），但它决定这条语句重新渲染出来好不好看；
            # 归成块层级会把 `[` 后面的内容拖到括号位置之外。
            rel.append(indent)
        else:
            while len(stack) > 1 and indent < stack[-1]:
                stack.pop()
            if indent > stack[-1]:
                stack.append(indent)
            # **块层级**：归一成 4 的倍数。真块体在单元格里可能被写成 2 空格
            # 一级（有人手改过），归一到 4 才能和 `render_code_block` 的
            # `python:` 块缩进对上（那边按 4 空格一级出）。
            rel.append(4 * (len(stack) - 1) if indent > stack[0] else 0)
        depth += _bracket_delta(ln, '')
        quote = _quote_state(ln, quote)
        prev_open = depth > 0 or bool(quote)
    return rel



def _is_bare_renpy_verb(line: str) -> bool:
    """`pause` / `with dissolve` 这类没有参数也成立的裸语句（别被当 python）。"""
    head = line.split(None, 1)[0] if line.split() else ''
    return head.rstrip(':') in _RENPY_STATEMENT_HEADS



# ===================== rpy -> Beat =====================

# show 子句里用来分隔"图像名"与修饰关键字的分界词
_SHOW_ARG_KEYWORDS = frozenset(
    {'at', 'as', 'with', 'behind', 'onlayer', 'zorder'})



def _clause_image(clause: str) -> str:
    """从一个 show 子句（已去掉前导 'show '）里取出图像名。"""
    parts = []
    for w in clause.split():
        if w in _SHOW_ARG_KEYWORDS:
            break
        parts.append(w.rstrip(','))
    return ' '.join(parts)



def _fmt_seconds(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:.1f}" if f == int(f) else f"{f:g}"



def _audio_params(payload: dict) -> str:
    """把音频参数（loop/fadein/fadeout）还原成可回写的后缀文本。"""
    parts = []
    if payload.get('loop'):
        parts.append('loop')
    fi = payload.get('fadein') or 0
    fo = payload.get('fadeout') or 0
    if fi:
        parts.append(f"fadein {_fmt_seconds(fi)}")
    if fo:
        parts.append(f"fadeout {_fmt_seconds(fo)}")
    return ' '.join(parts)



def _show_clause_args(payload: dict) -> str:
    """
    把 show 语句 payload 还原成 `show ` 之后的完整参数。

    只由 image+at 组成的简单 show 不走这里（用紧凑的 `图像@变换`），
    只有带 as/behind/onlayer/zorder/with/expression 的复杂 show 才
    序列化成完整子句——既保住 Excel 的可读性，又不丢修饰符。
    """
    if payload.get('is_expression'):
        return f"expression {payload.get('image', '')}"
    parts = [payload.get('image', '')]
    if payload.get('as_'):
        parts += ['as', payload['as_']]
    at = payload.get('at') or []
    if at:
        parts += ['at'] + list(at)
    behind = payload.get('behind') or []
    if behind:
        parts += ['behind', ', '.join(behind)]
    if payload.get('onlayer'):
        parts += ['onlayer', payload['onlayer']]
    if payload.get('zorder') is not None:
        parts += ['zorder', str(payload['zorder'])]
    if payload.get('with_'):
        parts += ['with', payload['with_']]
    return ' '.join(p for p in parts if p != '')



def _show_item_text(payload: dict) -> str:
    """
    单个 show 语句 -> Show 单元格里的一项。

    三种编码：
      · 简单形态        → `图像@位置`（或纯图像名）
      · `expression`    → `expression <表达式>@位置`（**必须带前缀**：
        表达式本体的形态与普通图像名无从区分，不带前缀读回来就只能当普通图）
      · 带 as/behind/…  → 完整子句（那些修饰符没有结构化列承载）
    """
    image = payload.get('image', '') or ''
    # 多个 `at` 目标之间**必须保留逗号**（`at left, right`）：空格连接得到
    # `at left right`，Ren'Py 会把 `right` 当成 `left` 后面的另一个词法单元
    # 而不是第二个变换 —— 语义直接变了。
    at = _join_at_targets(payload.get('at'))
    simple = not any((
        payload.get('as_'), payload.get('with_'), payload.get('onlayer'),
        payload.get('behind'), payload.get('zorder') is not None,
    ))
    if payload.get('is_expression'):
        # 表达式形态统一用自描述子句（见 `_expression_clause`）：
        # 位置、别名都不丢，读回来还是同一个项。
        return _expression_clause({
            'image': image, 'at': at, 'as_': payload.get('as_') or '',
            'with_': payload.get('with_') or '',
        })
    if simple:
        return f'{image}@{at}' if at else image
    return _show_clause_args(payload)



def show_item_part(item: dict) -> str:
    """
    一个 show 项 → **规范片段**（`expression X as q at i` / 复杂子句原文 /
    `图像@变换` / 纯图像名）。

    这是 show 项在单元格里的唯一编码形式，`_join_show_items` 与
    `show_item_parts` 都用它 —— 编码只有一份，比较才有意义。

    `expression` 项**不能**用 `图像@位置` 那种紧凑编码：表达式本体里
    可能有空格、括号、引号（`holo("lan base")`），`@` 分不出边界。
    所以它编码成自描述的完整子句 `expression X as q at i`。
    """
    if item.get('clause'):
        return ' '.join(str(item['clause']).split())
    image = ' '.join(str(item.get('image') or '').split())
    if not image:
        return ''
    if item.get('is_expression'):
        return _expression_clause(item)
    at = str(item.get('at') or '').strip()
    return f'{image}@{at}' if at else image



def _expression_clause(item: dict) -> str:
    """
    `show expression` 项 → 规范子句 `expression X [as q] [at i] [with d]`。

    `beat_model` 内部（`compose_show_cell` / `show_item_part`）共用这一份，
    保证"写进单元格的"与"用来比较去重的"是**同一个字符串**。
    """
    line = f"expression {item.get('image', '')}"
    as_ = str(item.get('as_') or '').strip()
    if as_:
        line += f' as {as_}'
    at = str(item.get('at') or '').strip()
    if at:
        line += f' at {at}'
    with_ = str(item.get('with_') or '').strip()
    if with_:
        line += f' with {with_}'
    return line


def show_item_parts(text: str) -> set:
    """
    show 文本 → 它包含的**规范片段集合**（给"这条 Stage 指令是否已被
    结构化列承载"用，见 `beat_render` 的舞台尾段）。
    """
    return {p for p in (show_item_part(it) for it in _split_show_items(text)) if p}


def _join_show_items(items: list) -> str:
    """
    把多个 show 项拼成 `图像@变换; 图像2; cg x at y behind z`。

    **同名同参数的去重**：`hydrate_from_dsl` 是"追加"语义（结构化列 +
    高级列是同一条流水线的两段输入），而 Stage 列往往已经把结构化列的内容
    原样写了一遍，于是 hydrate 会把同一条 show 又追加一次。不去重的话
    导出会生成两遍 `show ling close_happy at i11`（重复执行转场）。
    """
    parts, seen = [], set()
    for it in items:
        part = show_item_part(it)
        if not part or part in seen:
            continue
        seen.add(part)
        parts.append(part)
    return '; '.join(parts)



def _split_show_items(text: str) -> list:
    """
    `bg@left; eileen happy; cg cg02 at t behind ye`
      -> [{'clause': '', 'image': 'bg', 'at': 'left'},
          {'clause': '', 'image': 'eileen happy', 'at': ''},
          {'clause': 'cg cg02 at t behind ye', 'image': 'cg cg02', 'at': ''}]

    只认 `图像@变换`（紧凑式）与"含修饰关键字的完整子句"两种；其余当纯图像名。

    **`expression` 形态单独走一条路**（2026-10-05 修）：`show expression`
    的图像名本身是个 Python 表达式（`holo("lan base")`），里面可能带空格、
    括号、引号，所以 `@位置` 那种紧凑编码**表达不了**它 —— 必须像别的
    修饰符一样解析成 `{expression, image, at, as, with}`，否则位置信息
    （`at i11`）会在写表时丢掉（真实项目 `script.rpy` 实测 5 条）。
    """
    items = []
    for part in (text or '').split(';'):
        part = part.strip()
        if not part:
            continue
        if part.startswith('expression') and (
                part == 'expression' or part[len('expression')].isspace()):
            items.append(_parse_show_expression(part))
        elif '@' in part:
            image, _, at = part.partition('@')
            image = image.strip()
            # `expression X@at` 进来时（`show_item_part` 的编码）走表达式的路
            if image.startswith('expression') and (
                    image == 'expression'
                    or image[len('expression')].isspace()):
                item = _parse_show_expression(image)
                item['at'] = at.strip()
                items.append(item)
                continue
            items.append({'clause': '', 'image': image, 'at': at.strip()})
        elif any(w in _SHOW_ARG_KEYWORDS for w in part.split()):
            items.append({'clause': part, 'image': _clause_image(part),
                          'at': ''})
        else:
            items.append({'clause': '', 'image': part, 'at': ''})
    return items



def _parse_show_expression(part: str) -> dict:
    """
    `expression holo("lan base") at i11 as qing with dissolve`
      → {clause: '', is_expression: True, image: 'holo("lan base")',
         at: 'i11', as_: 'qing', with_: 'dissolve'}

    关键词的**识别要跳过引号**：表达式里完全可以出现 `at` / `as` 这样的词
    （`Text("at as")`），按空格硬切会切错。找不到关键词时整个剩余部分
    就是表达式本体。
    """
    rest = part[len('expression'):].strip()
    at = as_ = with_ = ''
    spec_chars = []
    i = 0
    n = len(rest)
    while i < n:
        ch = rest[i]
        if ch in '"\'':
            # 跳过整个字符串字面量（含转义）
            j = i + 1
            while j < n:
                if rest[j] == '\\':
                    j += 2
                    continue
                if rest[j] == ch:
                    break
                j += 1
            spec_chars.append(rest[i:j + 1])
            i = j + 1
            continue
        if ch == ' ':
            m = re.match(r'\s+(at|as|with)\s+', rest[i:])
            if m:
                word = m.group(1)
                i += m.end()
                j = i
                while j < n and not rest[j].isspace():
                    j += 1
                value = rest[i:j]
                i = j
                if word == 'at' and not at:
                    at = value
                elif word == 'as' and not as_:
                    as_ = value
                elif word == 'with' and not with_:
                    with_ = value
                continue
            spec_chars.append(ch)
            i += 1
            continue
        spec_chars.append(ch)
        i += 1
    return {
        'clause': '',
        'is_expression': True,
        'image': ''.join(spec_chars).strip(),
        'at': at,
        'as_': as_,
        'with_': with_,
    }



def _split_hide_items(text: str) -> list:
    """`ye; lan@dissolve` -> [{'image': 'ye', 'with': ''},
                             {'image': 'lan', 'with': 'dissolve'}]"""
    items = []
    for part in (text or '').split(';'):
        part = part.strip()
        if not part:
            continue
        if '@' in part:
            image, _, w = part.partition('@')
            items.append({'image': image.strip(), 'with': w.strip()})
        else:
            items.append({'image': part, 'with': ''})
    return items



def hide_item_part(item: dict) -> str:
    """一个退场项 → 规范片段（`图像@转场` / 纯图像名）。"""
    image = ' '.join(str(item.get('image') or '').split())
    w = str(item.get('with') or '').strip()
    if not image:
        return ''
    return f'{image}@{w}' if w else image


def hide_item_parts(text: str) -> set:
    """退场文本 → 规范片段集合（同 `show_item_parts`）。"""
    return {p for p in (hide_item_part(it) for it in _split_hide_items(text)) if p}


def _join_hide_items(items: list) -> str:
    """
    `hide` 项 → `ye; lan@dissolve`。**同样要做"同名同参数去重"**。

    为什么必须去重（2026-09-30 真实数据实测）：`hydrate_from_dsl` 是追加
    语义，而 Stage 列常常把结构化列的内容原样又写了一遍。show 那条路早就
    有去重（见 `_join_show_items`），hide 这条**漏了**，于是同一条退场指令
    被吸两次就翻倍：`hide cg03` → `cg03; cg03`。**逐句校对必然踩中**
    （chapter1_3 实测 11 拍被改写）。
    """
    parts, seen = [], set()
    for it in items:
        part = hide_item_part(it)
        if not part or part in seen:
            continue
        seen.add(part)
        parts.append(part)
    return '; '.join(parts)



def _append_line(existing: str, line: str) -> str:
    return (existing + '\n' + line) if existing else line


# ===================== 立绘列的公共契约（跨包使用） =====================
#
# `split_show_items` / `join_show_items` / `split_hide_items` 是
# **立绘列（`beat.show` / `beat.hide`）的唯一编解码器**：
# `app`（卡片编辑器）、`pipeline`（校验）、`excel_import`、
# `stage_snapshot` **都在用**它们。
#
# 既然跨包在用，它们**已经是公共 API 了** —— 只是名字还带下划线。
# `test_package_layering.test_no_private_names_cross_package` 会把这种
# "事实上是契约、名义上是私有"的名字标出来（P2-7 的教训：那次实测
# `script_table` 门面 re-export 了 92 个下划线名，而外部只用 2 个）。
#
# 这里做成**别名**而不是重命名：包内还有几十处调用 `_split_show_items`，
# 一次性改名会把 diff 铺得很大、掩盖真正要审的改动。函数体只有一份，
# 两个名字指向同一个对象，**不会漂移**。
split_show_items = _split_show_items
join_show_items = _join_show_items
split_hide_items = _split_hide_items
join_hide_items = _join_hide_items




def assign_identity(beats: list):
    """
    给每条 Beat 补上稳定身份：LineId + ContentHash。

    为什么要身份：
      · 团队协作（数据库后端）需要主键——按"第几行"当主键，别人插一行
        你的锁就串了；按内容哈希当主键，改一个错别字就换身份，锁和
        状态全丢。所以用 `label.序号` 这种**位置稳定**的 ID，
        再配一个 ContentHash 记录"当时内容是什么"，用来判冲突。
      · 多语言/翻译单元、存档埋点、QA 反馈都要能指向具体某一句话。

    LineId 已有则不覆盖（保留 Excel/DB 传来的身份）；
    ContentHash **一律按当前内容重算**——它的语义是"这条现在长什么样"，
    用表里带过来的旧值会导致"改了内容却判成没变"，push 静默丢改动。
    """
    for b in beats:
        if not b.line_id:
            label = b.label or 'start'
            b.line_id = f"{label}.{b.seq:04d}"
        b.content_hash = _beat_content_hash(b)



def _beat_content_hash(beat: 'Beat') -> str:
    """Beat 语义内容的短哈希（只参与冲突检测，不参与相等判断）。

    刻意把 stage/bgm/sfx 的**原文**也算进去：策划在 Stage 里改了一个表情词，
    哪怕解析器暂时认不出来，也必须让 ContentHash 变——否则 push 会把
    "改了内容"判成"未变"，静默丢改动。
    """
    import hashlib
    parts = [
        beat.kind, beat.speaker, beat.text, beat.scene, beat.show, beat.hide,
        beat.at, beat.scene_at, beat.with_, beat.transform, beat.stage_order,
        beat.raw, beat.code,
        # raw/code 的交错顺序也算内容：顺序变了演出就变了
        repr(getattr(beat, 'ordered', None) or []),
        beat.music, beat.music_params, beat.music_action,
        beat.sound, beat.sound_params, beat.sound_action, beat.voice,
        beat.condition, beat.comment,
        beat.stage, beat.bgm, beat.sfx,
    ]
    blob = '\x1f'.join('' if p is None else str(p) for p in parts)
    return hashlib.md5(blob.encode('utf-8')).hexdigest()[:10]



def _split_lines(text: str) -> list:
    """
    多行单元格（Comment/Raw/CodeBlock）按行拆开，去空行。

    **行尾一律归一成 `\\n`**：Windows 上写出去的 `.rpy` / 单元格可能是 CRLF，
    而 `split('\\n')` 会把 `\\r` 留在行尾 —— 于是行尾多一个不可见字符，
    缩进计算、`$` 判定、按行比对全部悄悄错位（实测踩过：续行缩进被算成 0，
    一整个 `python:` 块被拆成几十条 `$` 语句）。
    """
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    return [ln.rstrip() for ln in text.split('\n') if ln.strip()]



def _split_code_lines(text) -> list:
    """
    代码格按行拆开，**保留空行**（其余同 `_split_lines`）。

    为什么代码格要留空行：Python 里空行没有语义，但它出现在多行语句的
    参数列表中间时（`drag_puzzle(` 里就有），丢了会让"同一段代码"的指纹
    在往返前后对不上 —— 审计把它算成一条丢失 + 一条新增。空行**不写进
    `raw` / `code` 字段**（由 `_ordered_code_lines` 在还原时插回去，
    见 `_split_code_cell`）。
    """
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    return [ln.rstrip() for ln in text.split('\n')]



def _looks_like_renpy_statement(line: str) -> bool:
    """这行代码像"一条 Ren'Py 语句"（而不是裸的 python 表达式）。"""
    head = line.split(None, 1)[0] if line.split() else ''
    head = head.rstrip(':')
    return head in _RENPY_STATEMENT_HEADS



_RENPY_STATEMENT_HEADS = frozenset({
    '$', 'python', 'with', 'pause', 'jump', 'call', 'play', 'queue', 'stop',
    'scene', 'show', 'hide', 'voice', 'window', 'menu', 'return', 'pass',
    'if', 'elif', 'else', 'while', 'for', 'init', 'label', 'define',
    'default', 'transform', 'image', 'extend', 'screen', 'nvl', 'camera',
    'style', 'layeredimage', 'translate', 'on', 'at', 'renpy.', 'persistent.',
    'config.', 'gui.', 'store.', 'narrator', 'voice_sustain',
})
