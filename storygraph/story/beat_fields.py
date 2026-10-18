"""
storygraph.story.beat_fields
============================
**拍字段的唯一一份契约**（2026-10-17，架构收敛）。

为什么要有这个模块
------------------
调研结论（用户 2026-10-16）说得很准：保真字段的"登记、快照、哈希、协作、
序列化"没有收敛到同一份 schema，而是散在五六张手写清单里：

    BEAT_FIELDS / HIDDEN_BEAT_COLUMNS / SINGLE_LINE_BEAT_FIELDS
    app.session_table._SNAPSHOT_FIELDS
    beat_model._beat_content_hash() 里的 parts 列表
    collab_store._payload_from_beat() / _FIELD_ATTR
    schema.py 的列定义

每加一个字段要人肉同步五六处，**漏一处不会报错，只会某条路径静默丢数据**。
真实的伤口：`collab_store` 用 `field.lower()` 猜属性名，于是
`SpecHead → spechead`、`With → with`、`CodeBlocks → codeblocks` 全部取空 ——
过一次协作库，自定义语句头、循环头、相机头、代码块结构、行尾注释全没了，
而 `test_push_then_pull_roundtrip` 只断言了 LineId 和 Text，所以一直绿。

修法不是再补一张清单，而是**只留一张**：下面这份 `FieldSpec` 表。列顺序、
表头、隐藏列、单行列、撤销快照、内容指纹、协作序列化**全部由它派生**
（见 `beat_model` 模块级的别名导入与 `app.session_table._SNAPSHOT_FIELDS`）。

字段分类
--------
  · `excel=True`   —— 有 Excel 列（`header` 非空），顺序 = `BEAT_FIELDS`；
  · `collab=True`  —— 进协作库 payload（`file_blocks` 是无列但要持久化的
                      章节级字段，所以它 `excel=False / collab=True`）；
  · `snapshot=True`—— 进撤销快照（凡是会进渲染的都要在，否则撤销留半新半旧）；
  · `fingerprint=True` —— 进 `Beat.beat_fingerprint`（凡是会改产物或表内容的都要在，
                      否则"改了内容却判成没变"，协作 push 静默丢改动）。

**刻意不在表里的**（运行时元数据，既不入表、也不进快照/指纹）：
`src_first` / `src_last`（增量导出用的源文件行号）、`_from_script`、
`sanitized_cells`（清洗留痕）、`_hydrated_stage` 等派生缓存。
理由与从前一致：撤销一次编辑不该改掉"这一拍在源文件的哪几行"。

本模块**零项目内依赖**（只 import dataclasses），这样 `beat_model` 可以在
定义 `Beat` 之前就把它导进来，不会形成循环。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    """一个拍字段的**全部身份**：列名、属性名、旗标、默认值。"""

    #: 列名 / payload 键名（`SpecHead` / `FileBlocks`）—— Excel 与协作库都认它
    name: str
    #: `Beat` 上的属性名（`spec_head` / `with_`）
    attr: str
    #: Excel 中文表头；空串 = **不是 Excel 列**（内部/章节级字段）
    header: str = ''
    #: 整列隐藏（工具维护，策划看不见，但数据照旧读写）
    hidden: bool = False
    #: 单行列：值会被逐字拼进一行代码，读表时控制字符压成空格
    single_line: bool = False
    #: 进协作库 payload（`file_blocks` 没有列，但必须随库走）
    collab: bool = True
    #: 进撤销快照
    snapshot: bool = True
    #: 进 `Beat.beat_fingerprint`（拍内容指纹）
    fingerprint: bool = True
    #: 人话说明（为什么这个字段是保真的、或者为什么它不参与指纹）
    note: str = ''

    @property
    def excel(self) -> bool:
        """是不是 Excel 列（由 `header` 派生，不允许两处各写一份）。"""
        return bool(self.header)


# ======================================================================
#  拍表的列（顺序 = `BEAT_FIELDS` = Excel 列顺序 = 契约）
# ======================================================================
#
# ⚠ **加列只能加在末尾**（`StageOrder` 开始的内部列都在尾部）：
# 老表按列下标定位隐藏列，插在中间会让旧表读错列。
# 真正的新增列请追加在 `CameraHeader` 之后，并同步 `docs/schema.json`
# （`python -m storygraph.schema --write docs/schema.json`）。
COLUMN_SPECS = (
    FieldSpec('LineId', 'line_id', header='行号', single_line=True,
              fingerprint=False,
              note='稳定身份。不进指纹：它是坐标，不是内容。'),
    FieldSpec('Seq', 'seq', header='序号', single_line=True,
              fingerprint=False,
              note='排序权威。不进指纹：插一行就会改掉后面所有行的身份，'
                   '拿它算命内容会制造无意义的冲突。'),
    FieldSpec('Label', 'label', header='章节', single_line=True,
              note='章节名进产物（label 块），改了必须重写。'),
    FieldSpec('Kind', 'kind', header='类型', single_line=True),
    FieldSpec('Speaker', 'speaker', header='说话人', single_line=True),
    FieldSpec('Comment', 'comment', header='注释'),
    FieldSpec('Text', 'text', header='台词'),
    FieldSpec('Condition', 'condition', header='条件', single_line=True),
    FieldSpec('Scene', 'scene', header='背景', single_line=True),
    FieldSpec('Show', 'show', header='立绘', single_line=True),
    FieldSpec('At', 'at', header='位置', single_line=True),
    FieldSpec('With', 'with_', header='转场', single_line=True),
    FieldSpec('Hide', 'hide', header='退场', single_line=True),
    FieldSpec('BGM', 'bgm', header='音乐', single_line=True),
    FieldSpec('BGMParams', 'music_params', header='音乐参数', single_line=True),
    FieldSpec('SFX', 'sfx', header='音效', single_line=True),
    FieldSpec('SFXParams', 'sound_params', header='音效参数', single_line=True),
    FieldSpec('Voice', 'voice', header='语音', single_line=True),
    FieldSpec('Stage', 'stage', header='舞台（高级）'),
    FieldSpec('CodeBlock', 'code', header='代码'),
    FieldSpec('ContentHash', 'beat_fingerprint', header='内容指纹',
              single_line=True, fingerprint=False,
              note='拍内容指纹（改名：从前与 Statement.content_hash 同名不同义）。'
                   '指纹自己不进指纹。'),
    FieldSpec('StageOrder', 'stage_order', header='舞台顺序（内部）',
              hidden=True, single_line=True,
              note='源顺序：先 hide 再 show 是重置立绘，反序会把立绘藏掉。'),
    FieldSpec('OpOrder', 'op_order', header='操作顺序（内部）',
              hidden=True, single_line=True,
              note='跨类别操作顺序（音乐在立绘前还是后）。'),
    FieldSpec('CodeBlocks', 'code_blocks', header='代码块结构（内部）',
              hidden=True, single_line=True,
              note='「代码」格里哪些行构成 python: 块；丢了会把块拆成 $ 语句。'),
    FieldSpec('Nest', 'nest_path', header='结构位（内部）',
              hidden=True, single_line=True,
              note='块路径（if/menu 选项体）。'),
    FieldSpec('Inline', 'inline_comment', header='行尾注释（内部）',
              hidden=True, single_line=True,
              note='行尾注释原文（含 #）；条件行的注释也折在这一格。'),
    FieldSpec('Opaque', 'opaque', header='原样块（内部）', hidden=True,
              note='工具不认识的块语句，逐字写回。'),
    FieldSpec('SpecName', 'spec_name', header='声明来源（内部）',
              hidden=True, single_line=True,
              note='自定义语句被谁声明过（T1 / T3 的分界线）。'),
    FieldSpec('SpecHead', 'spec_head', header='自定义语句·头行（高级）',
              hidden=True, single_line=True,
              note='T1 的参数编辑面：改了才替换首行，没改逐字节不变。'),
    FieldSpec('LoopHeader', 'loop_header', header='循环头行（内部）',
              hidden=True, single_line=True,
              note='while / for 的头行。'),
    FieldSpec('CameraHeader', 'camera_header', header='相机头行（内部）',
              hidden=True, single_line=True,
              note='3D 舞台 camera 块的头行。'),
    #: 台词**引号串之后**的合法子句（`(what_color="#f00")` / `with` / `id` /
    #: `nointeract`）。2026-10-18 追加：从前它被 `_RE_SAY` 静默吃掉，
    #: 真机 `script_pomo.rpy` 的 `jc "…" (no_skip=True)` 导出后括号没了。
    FieldSpec('SayTail', 'say_tail', header='台词后缀（内部）',
              hidden=True, single_line=True,
              note='台词的样式/控制子句，原样写回；空 = 普通台词。'),
    #: `label 名(参数…):` 的参数表。2026-10-18 追加：从前解析器把参数
    #: "扫过去"就扔了，产物成 `label 名:` —— 而 `call 名(...)` 是原样保真的，
    #: 于是调用方传参、被调方不收，Ren'Py 报"参数过多"，游戏起不来。
    FieldSpec('LabelHeader', 'label_header', header='label 参数（内部）',
              hidden=True, single_line=True,
              note='label 的参数表原文（含括号），只记在该 label 的第一张拍上。'),
    #: `menu` 头行上的**菜单名**与**参数表**（2026-10-19，审计 P1-3 收尾）。
    #:
    #: 从前 `menu (nvl=True):` / `menu choice_menu:` 让 `_scan_menu_header`
    #: 直接放弃 → 整块掉进「原样块」：字节一个不丢，但**选项不在拍里**
    #: （卡片上整段菜单消失，策划改不了其中任何一句），而且那是个「可跳转
    #: 的 label」这个结构事实也丢了。现在两样各占一格，选项重新是可编辑的拍。
    #:
    #: 两列都隐藏：它们是**程序面**的结构（`screen=` / `nvl=` / 跳转用的名），
    #: 与 `SpecHead` / `LoopHeader` / `CameraHeader` 同一档；但在卡片的高级区
    #: 里能改（`ADVANCED_FIELDS`），也照样读得回来、写得进 Excel。
    FieldSpec('MenuName', 'menu_name', header='菜单名（内部）',
              hidden=True, single_line=True,
              note='`menu 名(args):` 的 `名`。它是真 label（jump/call 能跳进来）。'),
    FieldSpec('MenuArgs', 'menu_args', header='菜单参数（内部）',
              hidden=True, single_line=True,
              note='`menu 名(args):` 的 `(args)` 原文（含括号），喂给菜单 screen。'),
)


# ======================================================================
#  没有 Excel 列、但**必须持久化/参与渲染**的字段
# ======================================================================
#
# 这些字段要么是列的**结构化投影**（`music` / `sound` / `scene_at`），
# 要么是**章节级**的（`file_blocks`），要么是 `CodeBlock` 格里拆出来的
# 交错顺序（`raw` / `ordered`）。它们不占列，但少一个快照/指纹就会漏。
INTERNAL_SPECS = (
    #: **文件级保真块**：顶层 define/default/init python/screen…
    #:
    #: 调研结论 P0-2：它以前只挂在"第一张拍"上，于是删除第一拍 =
    #: `init python` 整块消失。现在它由 `story.chapter.ChapterDocument`
    #: 持有（会话层），拍上的这一个只作为**渲染/Excel 传输**用 ——
    #: 但它仍必须进快照与指纹：它改的是产物。
    FieldSpec('FileBlocks', 'file_blocks', collab=True,
              note='章节级顶层块。传输用；真源在 ChapterDocument。'),
    FieldSpec('Raw', 'raw',
              note='「代码」格里原生语句那部分（与 code 交错）。'),
    FieldSpec('Ordered', 'ordered',
              note='raw/code 的**交错顺序**；不记就会把交替演出排成两段。'),
    FieldSpec('SceneAt', 'scene_at',
              note='背景专属变换（`图@变换` 里的变换）。'),
    FieldSpec('Transform', 'transform',
              note='旧版 transform 字段；渲染仍认它（`with` 兜底）。'),
    FieldSpec('Music', 'music'),
    FieldSpec('MusicAction', 'music_action'),
    FieldSpec('Sound', 'sound'),
    FieldSpec('SoundAction', 'sound_action'),
    FieldSpec('Notes', 'notes',
              collab=False,
              note='已取消的独立列；写表时并进 Comment，所以它改内容。'),
    FieldSpec('CondComment', 'cond_comment',
              note='if/elif/else 头那一行的行尾注释（折进 Inline 格）。'),
    #: **导入期的运行时提示**（例如"Excel 行序与 Seq 不一致，已按 Seq 重排"）。
    #: 不入表、不进快照/指纹：它不是内容，是"工具替你做了什么"的交代。
    FieldSpec('RuntimeNotes', 'runtime_notes', collab=False,
              snapshot=False, fingerprint=False,
              note='导入时的自动处置留痕，由 validate 报 W203。'),
)


#: 全表（列在前、内部在后）。**唯一真源**。
BEAT_FIELD_SPECS = tuple(COLUMN_SPECS) + tuple(INTERNAL_SPECS)

#: 列名（Excel 列顺序 = `BEAT_FIELDS`）。
BEAT_FIELDS = [s.name for s in COLUMN_SPECS]

#: 列名 → 中文表头。
BEAT_HEADERS = {s.name: s.header for s in COLUMN_SPECS}

#: 整列隐藏的列（顺序同列顺序）。
HIDDEN_BEAT_COLUMNS = tuple(s.name for s in COLUMN_SPECS if s.hidden)

#: 单行列：读表时把控制字符压成空格并留痕（W114）。
SINGLE_LINE_BEAT_FIELDS = tuple(s.name for s in COLUMN_SPECS if s.single_line)

#: 撤销快照要装的**属性名**（`Beat.attr`）。
SNAPSHOT_FIELDS = tuple(s.attr for s in BEAT_FIELD_SPECS if s.snapshot)

#: 内容指纹要装的**属性名**。
FINGERPRINT_FIELDS = tuple(s.attr for s in BEAT_FIELD_SPECS if s.fingerprint)

#: 列名 → `Beat` 属性名（显式映射，**不再用 `field.lower()` 猜**）。
ATTR_OF = {s.name: s.attr for s in BEAT_FIELD_SPECS}

#: 属性名 → spec（快照/指纹/协作三边对账用）。
SPEC_OF_ATTR = {s.attr: s for s in BEAT_FIELD_SPECS}


def spec_for(name: str):
    """按列名 / 属性名取 spec（都没有 → None）。"""
    return SPEC_OF_ATTR.get(name) or next(
        (s for s in BEAT_FIELD_SPECS if s.name == name), None)


def stable_value(value) -> str:
    """字段值 → 可拼进指纹的稳定字符串（list 用 repr，None 当空串）。"""
    if value is None:
        return ''
    if isinstance(value, (list, tuple, dict)):
        return repr(value)
    return str(value)


__all__ = [
    'ATTR_OF', 'BEAT_FIELDS', 'BEAT_FIELD_SPECS', 'BEAT_HEADERS',
    'COLUMN_SPECS', 'FINGERPRINT_FIELDS', 'FieldSpec', 'HIDDEN_BEAT_COLUMNS',
    'INTERNAL_SPECS', 'SINGLE_LINE_BEAT_FIELDS', 'SNAPSHOT_FIELDS',
    'SPEC_OF_ATTR', 'spec_for', 'stable_value',
]
