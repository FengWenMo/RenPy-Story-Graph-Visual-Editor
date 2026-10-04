"""
storygraph.app.editor
=====================
**编辑器会话门面** —— 前端唯一需要认识的对象。

它是什么（深模块）
------------------
一个"打开了一章、正在编辑哪一拍、改完怎么落盘"的会话。接口很小：

    s = EditorSession(registry_provider=..., project_root=...)
    s.load('game/chapter/ch1.rpy')
    while s.has(i):
        fields = s.fields(i)          # → 直接塞进表单
        cards  = s.cards(i)           # → 直接塞进立绘网格
        ...用户改...
        s.apply(i, fields, cards)     # 表单 → Beat

    名字      一次导出          # 编译 + 增量补丁 + 安全门禁，**一个调用**
    体检      项目体检

实现里装着的全部业务不变量（前端再也碰不到，也就不可能各写一套）：

  · **`show` 是投影，不是输入**：表情/位置住在卡片 chip 上，
    写回时按出场顺序拼成 `show`（多属性不压平、extras 不丢、
    位置只有统一才整拍）；
  · **Stage 每次重算**（它是派生视图，不是用户输入）；
  · **空网格不清空立绘**（防误删）；
  · **高级区只在展开时才写回**（收起 ≠ 清空）；
  · 翻页是**纯导航**，不碰别的拍；
  · 落盘一律过生成门禁（`generation_guard`）。

为什么值得存在
--------------
拆分前，上面每一条都在 tkinter 的 `BeatEditorPanel` 里，而 Qt 版
`main_window.py` 只能**照抄一遍**并伸手去摸 `self._panel._speaker`
这种私有属性 —— 两套前端、两套语义，改一处必漏一处。

**禁止**在本模块里 import 任何 UI 框架。
"""


from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from storygraph.app.stage import build_frames
# 「表格 / 拍编辑」那一半 —— **项目的核心**，也是 P2-6 拆分的最后一块。
# 见 `session_table.py` 的模块 docstring：**这是一次搬家，契约没变**。
# 下面这几个名字在这里 re-export，是为了
# `from storygraph.app.editor import ADVANCED_FIELDS` / `BeatSnapshot` /
# `uniform_position_of` 这些老写法继续可用
# （`ui_qt/main_window.py:73`、`tests/test_app_layer.py:556` 各有一处）。
from storygraph.app.session_table import (BeatSnapshot,
                                          TableSessionMixin,
                                          _SNAPSHOT_FIELDS,
                                          uniform_position_of)
# 「剧情图」那一半（布局 / 章节导入 / 结构分析 / 剧情变量）。
# 这两个模块级函数同样在这里 re-export —— `ui_qt/main_window.py:2102`
# 就是 `from storygraph.app.editor import graph_from_layout_dict`。
from storygraph.app.session_graph import (GraphSessionMixin,
                                          _BODY_LINE_RE,
                                          files_look_like_control_flow,
                                          graph_from_layout_dict)
# 「资产」那一半（资源表读取 / 候选 / 概览 / 缩略图路径）。
from storygraph.app.session_assets import AssetRegistryMixin
# 「导出」那一半（落盘与门禁 / 增量补丁 / 多引擎导出 / 预览帧 / 体检）。
# ⚠ 这里住着**项目里最危险的一段代码**（`export` + `_patch_note`，
# 管着"patch 失败不许静默退回全量覆盖"那个 P0）—— 搬的时候一个字没改。
from storygraph.app.session_export import ExportMixin
# 「协作」那一半（SQLite ↔ Excel 的四条命令）—— 纯转发，风险最低。
from storygraph.app.session_collab import CollabMixin
from storygraph.story.beat_model import Beat
from storygraph.story.beat_render import make_symbols

from storygraph.core.safeguard import swallow


#: 主区字段（每一拍真正常改的）—— 与两套前端共用同一份划分。
#:
#: **刻意留在核心**（没跟表格块一起搬走）：它是"表单长什么样"的**前端契约**，
#: 前端 `from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS`
#: 直接用它（`ui_qt/main_window.py:73`）。搬进 mixin 会让前端依赖一个
#: 它其实不关心的类内实现。
#: （`session_table.py` 里 re-export 了同一对名字，两边指向同一个对象。）

#: 主区字段（每一拍真正常改的）—— 与两套前端共用同一份划分
PRIMARY_FIELDS = ['Speaker', 'Text', 'Scene', 'BGM', 'Voice', 'Comment']
#: 高级区字段（默认折叠）
#:
#: **不含 `Notes`**（2026-10-05 定：这个字段取消）。它以前在这里，
#: 于是前端照样建"备注"控件、照样把值回读进 `Beat.notes` ——
#: 而 `BEAT_FIELDS` 里**没有这一列**，`to_row` 不写它，
#: 所以编剧认真写的备注**导出一次就蒸发，且不报错**。
#:
#: 二选一里选了"取消"而不是"补一列"，理由是它与 `Comment` 完全重叠：
#: `Comment`（表头「注释」）在**主区**、是 `BEAT_FIELDS` 里真有的列、
#: 导出会写。留两个语义相同的输入框，只会让人猜该填哪个。
#:
#: 老表里的 `Notes` 列**仍然读得回来**（`Beat.from_row` 把它并进
#: `Comment`，绝不丢字）—— 这条兼容链路没动，见
#: `tests/test_editor_session_mixin_contract.py` 的 `TestFieldContract`。
ADVANCED_FIELDS = ['SFX', 'CodeBlock', 'Label', 'Kind', 'Stage']


class EditorSession(TableSessionMixin, GraphSessionMixin,
                    AssetRegistryMixin, ExportMixin, CollabMixin):
    """
    一章的编辑会话。**前端只碰这个类**（不碰 Beat 内部字段名）。

    registry_provider: 返回资产 registry 的可调用对象（可为 None）。
        故意收一个函数而不是 engine 对象 —— 会话不需要知道"引擎"是什么。

    **职责拆分**（P2-6，2026-10-05）：原本 74 个方法混装四块职责。
    已搬走四块里的三块（域不同就分文件），全部做成 mixin 而不是独立对象 ——
    这样调用方一行都不用改（这是**搬家**不是改契约，而前端此刻
    没有行为测试兜着）：

      · `GraphSessionMixin`（`session_graph.py`，13 方法）——
        布局 / 章节导入 / 结构分析 / 剧情变量；
      · `AssetRegistryMixin`（`session_assets.py`，11 方法）——
        资源表读取 / 候选 / 概览 / 缩略图路径；
      · `ExportMixin`（`session_export.py`，10 方法）——
        落盘与门禁 / 增量补丁 / 多引擎导出 / 预览帧 / 体检；
      · `CollabMixin`（`session_collab.py`，4 方法）—— SQLite ↔ Excel。

    **只剩下"表格/拍编辑"那一块没拆**（`load` / `fields` / `cards` /
    `apply` / `insert` / `delete` / 撤销 …，约 25 个方法）—— 那是核心、
    网最密，留到最后。加上保留在类里的基建（`registry` / `source` /
    `refresh_symbols` / `compile` / `__init__` / `_renumber`），
    本类现在自己带 28 个方法。

    **基类顺序**：`EditorSession` 自己不该有任何与 mixin 同名的方法
    （有了就会**静默**覆盖 mixin 的那份）。顺序本身不紧要（四个 mixin
    没有同名成员），但这条契约由 `tests/test_editor_session_split.py` 守着 ——
    它还会断言每个 mixin 的 `REQUIRES` 在合成后的类上都存在。
    """

    def __init__(self, registry_provider: Optional[Callable] = None,
                 project_root=None):
        self._registry_provider = registry_provider
        self.project_root = (Path(project_root)
                             if project_root is not None else None)
        self.script_path: Optional[Path] = None
        self.beats: List[Beat] = []
        self.symbols = None
        #: 「打开项目」读进来的资源表（xlsx）。**只服务资源侧**
        #: （下拉候选 / 缩略图 / 参与角色 / 资产选择器），与剧情图无关。
        self._loaded_registry = None
        #: 逐拍的"上一次保存前"快照（撤销用）。**单级**：每个 Beat 一份。
        #: 只装拍号，不装 Beat 对象 —— 拍的增删由载入负责，
        #: 这里只记内容，避免"撤销把别人新插入的行顶掉"。
        self._last_saved: dict = {}
        #: 前端最近一次载入的拍号（撤销不带参数时的默认目标）。
        self._last_idx: Optional[int] = None
        #: 上一次删掉的卡片 `(原拍号, Beat)` —— 单级结构撤销用（见 restore_last_removed）
        self._last_removed = None
        #: **结构撤销栈**（插卡 / 删卡，多级；见 `undo_structural`）。
        #: 记的是"做了什么"而不是快照：`('insert', 落点)` / `('delete', 原拍号, Beat)`。
        #: 有界（只留最近 50 次）—— 它是交互退路，不是版本控制。
        self._struct_undo: list = []
        #: 角色↔剧本 linker（按需构建，见 `asset_linker()`）
        self._linker = None
        #: "生成物要不要自带 transform 定义段"的**缓存判定**
        #: （见 `should_write_transforms`）。`None` = 还没判过。
        #: **在这里初始化**（而不是等第一次判定时才建）：`AssetRegistryMixin`
        #: 的 `register_missing_assets` 会把它清掉，而那种"属性是否存在取决于
        #: 之前发生过什么"的状态，正是拆分时最容易漏的东西 ——
        #: `test_editor_session_split` 的 `REQUIRES` 守卫就要求它一开始就在。
        self._transforms_decision = None

    #  资产
    # ------------------------------------------------------------------

    def registry(self):
        """当前资产 registry（拿不到就 None —— 界面降级但不崩）。"""
        if self._loaded_registry is not None:
            return self._loaded_registry
        if self._registry_provider is None:
            return None
        try:
            return self._registry_provider()
        except Exception as e:                    # noqa: BLE001
            swallow('app.editor.registry', e)
            return None

    def source(self):
        """
        把 registry 适配成"看起来像 engine"的对象。

        `app.beats` 里那批检索函数（`participants` / `search_assets` /
        `asset_row` …）历史上收的都是 **engine**（用 `engine.get_registry()`
        取表）。会话手上只有 registry，于是统一过一次
        `RegistrySource` 适配 —— **不在会话里重写一遍检索逻辑**。
        """
        from storygraph.app.picker import RegistrySource
        return RegistrySource(self.registry(), self.project_root)

    def refresh_symbols(self):
        """重算符号表（脚本声明变了 / 换了项目时调用）。"""
        self.symbols = make_symbols(self.registry())
        return self.symbols

    #  载入
    # ------------------------------------------------------------------

    def has(self, idx: int) -> bool:
        return 0 <= idx < len(self.beats)

    #  剧情图：保存 / 加载布局 + 导出控制流（旧版右栏「画布」那一组）

    #: 布局 JSON 格式版本（**与旧版 tkinter 逐字段一致**，见 save_layout）
    LAYOUT_KEYS = ('nodes', 'edges', 'next_ids')

    #  多引擎导出 / 资产统计 / 协作（原先"后端有、界面没入口"的三样）

    #: `pipeline.engine_exporters` 支持的引擎（顺序即界面下拉顺序）

    #  增 / 删卡片（结构编辑）
    # ------------------------------------------------------------------

    def _renumber(self):
        """重排 `seq` 与 `line_id`（拍号变了以后必须一致，否则协作/定位错位）。"""
        label = ''
        for b in self.beats:
            if str(getattr(b, 'label', '') or '').strip():
                label = str(b.label)
                break
        for i, b in enumerate(self.beats, 1):
            b.seq = i
            if not str(getattr(b, 'label', '') or '').strip():
                b.label = label
            b.line_id = f'{b.label}.{i:04d}'

    def __len__(self) -> int:
        return len(self.beats)

    #  读：Beat → 表单（前端直接塞控件）

    #  写：表单 → Beat

    #  保存 / 撤销（"这张卡片的修改"）
    # ------------------------------------------------------------------

    def forget_undo(self) -> None:
        """丢弃全部撤销记录（换章/重新载入时调用，免得撤回到别的文件）。"""
        self._last_saved.clear()
        self._last_idx = None

    #  演出预览

    #  导出（编译 + 增量补丁 + 安全门禁）
    # ------------------------------------------------------------------

    def compile(self):
        """编译当前 beats（返回编译结果对象，不落盘）。"""
        from storygraph.pipeline.compile import compile_beats
        return compile_beats(self.beats, registry=self.registry(),
                             include_transforms=self.should_write_transforms())

    #  模块的**剧情变量**（技术代码不进这个视图）

    #  剧情图的**范围**：只放用户导入过的章节

    #  结构分析（变量区间 / 可达性）
    # ------------------------------------------------------------------

__all__ = ['EditorSession', 'BeatSnapshot', 'PRIMARY_FIELDS',
           'ADVANCED_FIELDS', 'uniform_position_of']
