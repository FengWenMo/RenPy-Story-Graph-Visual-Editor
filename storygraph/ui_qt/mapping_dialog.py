# -*- coding: utf-8 -*-
"""
storygraph.ui_qt.mapping_dialog
===============================
**资源映射管理对话框 + 资源对照表**（用户 2026-10-30，⑥）

用户原话：

> 映射表：查询 / 对照 / 修改 / 随包分发

这里给的是"人对一份映射文件能做的最少动作"，**每一步都不偷偷写**：

  · 「载入映射…」任意路径的 JSON（剧本群发来的文件也能吃）；
  · 「重读」把当前这份**从磁盘重新读一遍**（外部编辑器改完不用重启工具）；
  · 「导出映射初稿…」按**当前项目的数据**生成一份可改、可提交、可分发的初稿；
  · 「保存到项目根」把当前这份写进项目 —— **先给 diff，人点「确认写入」才落盘**，
    旧文件自动备份成 `.bak`（十条铁律第 5 条：绝不静默全量覆盖）；
  · 「资源对照表」一张图一行：路径 / 推断 kind / tag / 属性 / 依据 / 能不能 show，
    可以只看"未识别"的 —— 查出来是映射没写对，还是资源表本身有问题。

## 这些动作为什么不直接放主窗口菜单里做完

菜单项一点就该有结果；而"载入 / 导出 / 保存"都需要先看清内容再决定。所以
第二阶段主窗口的「🗂 资源映射」菜单只负责**打开这个对话框**，动作本身在这里。

## 判定/读写都不在这里

  · 一条图是什么            → `assets.asset_identity.resolve_image`
  · 映射怎么读 / 怎么校验   → `assets.asset_mapping`
  · 映射初稿怎么生成        → `assets.mapping_draft.draft_mapping`

本文件只负责：把结果摆出来、收人的决定、把错误变成**一句人话**（坏 JSON
不许闪退、不许把 traceback 甩给用户）。映射文件是**唯一"项目知识"载体**，
所以这里不写死任何项目名 / 目录名。
"""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                               QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPlainTextEdit, QPushButton, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from storygraph.assets import asset_mapping as AM
from storygraph.assets import mapping_draft as MD
from storygraph.assets.asset_identity import known_characters
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.asset_picker import (KIND_LABELS, SOURCE_LABELS,
                                           identity_map, pick_context)
from storygraph.ui_qt.mapping_editor import MappingEditor

#: 映射文件的名字（项目根或 `game/` 下；与 `storygraph.rules.json` 同一个模式）。
#: 定义在 `asset_mapping` 里（读/写/查找共用同一份），这里只是给界面用的别名。
MAPPING_NAME = AM.MAPPING_FILENAME

#: Excel 那份的名字（同位置**优先于** JSON，规则在 `assets/mapping_excel.py`）。
MAPPING_XLSX = AM.MAPPING_XLSX_FILENAME

#: 「载入 / 导出」的文件过滤器（JSON 是产出面、Excel 是手工维护面，都给）。
MAPPING_FILTERS = ('映射文件 (*.json *.xlsx *.xlsm);;JSON (*.json);;'
                   'Excel (*.xlsx *.xlsm);;所有文件 (*)')

#: 这份映射是哪来的。
SOURCE_PROJECT = 'project'      # 项目里找到的（自动加载 / 保存后就是它）
SOURCE_LOADED = 'loaded'        # 手动「载入映射…」进来的
SOURCE_NONE = 'none'            # 没有映射：工具按文件夹推断

SOURCE_TEXT = {
    SOURCE_PROJECT: '项目文件',
    SOURCE_LOADED: '手动载入的文件',
    SOURCE_NONE: '无',
}

#: 「工具怎么认的」那一页的列（顺序就是人看图的顺序：先看是哪个文件，
#: 再看工具认为它是什么、凭什么这么判）。
#:
#: ⚠ 这里同样是**给编剧看**的字（用户 2026-10-30："我需要一个编剧跟文案
#: 都能看明白的表"）：`推断` / `tag` / `属性` / `能否 show` 这些词只留在
#: 代码与契约里，屏幕上换成问句。
COLUMNS = ('文件', '工具认为它是', '角色', '表情', '凭什么这么判', '能直接写进剧本吗')

#: 状态栏那一行里"没有映射时"的说法（用户 2026-10-30 要求原话）。
INFER_TEXT = '按文件夹推断'

#: 三个页签的下标（**别写魔法数字**：加页签时这几处必须一起改）。
TAB_EDIT = 0        # 逐条编辑（不会写 JSON 的人也能定义）
TAB_FILE = 1        # 原始文件（JSON 全文 + 来源 / 警告）
TAB_TABLE = 2       # 工具怎么认的（一张图一行，查"凭什么这么判"）

#: 三个页签的标题（**唯一真源**：`_build` 与测试都读它，别各写一份）。
TAB_TITLES = ('逐条编辑', '原始文件', '工具怎么认的')


def _same_file(first, second) -> bool:
    """两个路径是不是同一个文件（相对/绝对、大小写都归一；Windows 不区分）。"""
    try:
        a = os.path.normcase(os.path.abspath(str(first or '')))
        b = os.path.normcase(os.path.abspath(str(second or '')))
    except (OSError, ValueError) as e:            # noqa: BLE001 - 路径怪就当不是
        swallow('ui_qt.mapping_dialog._same_file', e)
        return False
    return bool(a) and a == b


@dataclass(frozen=True)
class MappingState:
    """
    "当前这份映射"的快照（**不可变**，刷新就换一份新的）。

    `error` 只表示"读不出来"（坏 JSON / 文件没了）；`warnings` 是
    "读出来了但有问题"（未知键之类）—— 两者的处理不同：前者不能说
    "已加载"，后者可以照常用但要提示。
    """

    kind: str = SOURCE_NONE
    path: str = ''
    data: dict = field(default_factory=dict)
    warnings: tuple = ()
    error: str = ''

    def has_data(self) -> bool:
        """有没有一份**能用**的映射（读失败 / 空文件都算没有）。"""
        return bool(self.data) and not self.error

    def label(self) -> str:
        return SOURCE_TEXT.get(self.kind, self.kind)


@dataclass(frozen=True)
class ContrastRow:
    """资源对照表的一行：一张图 + 判定层给出的结论。"""

    asset_id: str
    relpath: str
    kind: str
    kind_label: str
    tag: str
    attrs: str
    source: str
    source_label: str
    showable: bool

    def is_unknown(self) -> bool:
        return self.kind == 'unknown'

    def path_text(self) -> str:
        """表里那格"图片路径"（ATL / 没登记 Path 的行说清是哪一条）。"""
        return self.relpath or f'（资源表没登记路径）{self.asset_id}'

    def cells(self) -> list:
        return [self.path_text(),
                self.kind_label,
                self.tag or '—',
                self.attrs or '—',
                self.source_label,
                '能' if self.showable else '不能']


# ==================================================================
#  读 / 写 / 比较 —— 纯函数（不碰 Qt）
# ==================================================================

def state_from_file(path, *, kind: str = SOURCE_LOADED) -> MappingState:
    """
    从一个映射文件读（**JSON 或 Excel**，按后缀分派）。

    坏文件 **不抛**给界面：转成 `MappingState.error` 里的一句人话 ——
    用户点的动作里不许出现 traceback（闪退更是最坏反馈）。
    """
    p = str(path or '').strip()
    if not p:
        return MappingState(kind=kind, error='没有给文件路径。')
    try:
        data, warnings = AM.load_mapping(p)
    except AM.MappingError as e:
        return MappingState(kind=kind, path=p, error=str(e))
    except OSError as e:
        return MappingState(kind=kind, path=p, error=f'读不到这个文件：{e}')
    return MappingState(kind=kind, path=p, data=dict(data or {}),
                        warnings=tuple(warnings or ()))


def _add_shadow(state: MappingState, root: str) -> MappingState:
    """
    把"项目里还有另一份映射、当前以哪份为准"补进 warnings。

    为什么不在 `state_from_file` 里做：那句话取决于**项目里还有哪些文件**，
    而 `state_from_file` 只认一个路径。「重读」走的是"直接按路径重读"
    （绕过 mtime 缓存），所以要在这里补一次，否则点一下「重读」这句提醒就没了。
    """
    active = str(getattr(state, 'path', '') or '')
    if not active or getattr(state, 'error', ''):
        return state
    try:
        note = AM.shadow_note(root, active)
    except Exception as e:                        # noqa: BLE001 - 提醒算不出来不算错
        swallow('ui_qt.mapping_dialog._add_shadow', e)
        note = ''
    if not note or note in state.warnings:
        return state
    return replace(state, warnings=tuple(state.warnings) + (note,))


def state_from_project(project_root, *, force: bool = False) -> MappingState:
    """
    项目里的映射（没有 = `SOURCE_NONE`）。

    `force=True` 时绕过 `mapping_cached` 的 mtime 缓存，直接按路径重读 ——
    「重读」按钮要的就是"用户刚在外部编辑器改完"的那个新文件。
    """
    root = str(project_root or '').strip()
    if not root:
        return MappingState()
    if force:
        try:
            path = AM.find_mapping_path(root)
        except Exception as e:                    # noqa: BLE001 - 找不到当没有
            swallow('ui_qt.mapping_dialog.find_mapping_path', e)
            path = None
        if not path:
            return MappingState()
        return _add_shadow(state_from_file(path, kind=SOURCE_PROJECT), root)
    try:
        data, warnings, path = AM.mapping_cached(root)
    except Exception as e:                        # noqa: BLE001 - 坏文件别闪退
        swallow('ui_qt.mapping_dialog.mapping_cached', e)
        return MappingState(kind=SOURCE_PROJECT, error=f'读映射失败：{e}')
    if not path:
        return MappingState()
    if not data and warnings:
        # `mapping_cached` 对**坏文件**不抛，只把原因放进 warnings（data 空）。
        # 这时状态必须是"读失败"，不能装成"没有映射、按文件夹推断"。
        return MappingState(kind=SOURCE_PROJECT, path=str(path),
                            warnings=tuple(warnings),
                            error='；'.join(str(w) for w in warnings))
    return MappingState(kind=SOURCE_PROJECT, path=str(path),
                        data=dict(data or {}),
                        warnings=tuple(warnings or ()))


def write_target(project_root) -> str:
    """
    「保存到项目根」写哪儿：项目里已有映射就写回**同目录**，否则写项目根。

    ⚠ 当前生效的是 Excel 时，写的是**同一目录的 JSON**（
    `storygraph.mapping.xlsx` → `storygraph.mapping.json`）——
    工具**从不回写人做的 Excel**（理由见 `assets/mapping_excel.py` ③），
    而放在同目录的 JSON 不会顶掉 Excel 的优先级（同一位置 Excel 优先）。
    """
    root = str(project_root or '').strip()
    if not root:
        return ''
    try:
        found = AM.find_mapping_path(root)
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.mapping_dialog.write_target', e)
        found = None
    if found:
        if AM.is_workbook(found):
            return str(Path(found).with_suffix('.json'))
        return str(found)
    return str(Path(root) / MAPPING_NAME)


def format_label(path) -> str:
    """这份映射是 Excel 还是 JSON（`''` = 不是映射文件）。"""
    return {'excel': 'Excel', 'json': 'JSON'}.get(AM.file_format(path), '')


def json_text(data) -> str:
    """一份映射 → 给人看/给人改的 JSON 文本（**保证中文原样**）。"""
    try:
        return json.dumps(data or {}, ensure_ascii=False, indent=2) + '\n'
    except (TypeError, ValueError) as e:          # noqa: BLE001 - 坏了也要显示
        return f'（这份映射里有程序写不出来的值：{e}）\n'


def _raw_diff(old: str, new: str) -> list:
    """unified diff 原始行（内容一样 = 空列表；**按行比**，看不出末尾换行差异）。"""
    return list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                     fromfile='现有文件', tofile='将要写入',
                                     lineterm='', n=2))


def diff_lines(before: str, after: str) -> list:
    """写盘前的差异（unified diff 行；内容一样 = 空列表）。"""
    old = str(before or '')
    new = str(after or '')
    lines = _raw_diff(old, new)
    if old != new and not lines:
        # 文字一字不差、只有末尾换行不同 —— unified diff 按行比看不出来，
        # 但不能显示成"没有变化"（那是在骗人）。
        return ['（文件文字一样，只有末尾换行不同：写入后以换行结尾）']
    return lines


def diff_summary(before: str, after: str) -> str:
    """写盘前的差异 → 一句话（"新增几行 / 删除几行"）。"""
    old = str(before or '')
    new = str(after or '')
    if old == new:
        return '和现有文件一字不差（写入不会改变它）'
    if not old.strip():
        return f'新建文件（{len(new.splitlines())} 行）'
    raw = _raw_diff(old, new)
    if not raw:
        return ('只有末尾换行不同（写完后文件以换行结尾；旧文件会先备份成 .bak）')
    added = removed = 0
    for line in raw:
        if line.startswith('+++') or line.startswith('---'):
            continue
        if line.startswith('+'):
            added += 1
        elif line.startswith('-'):
            removed += 1
    return f'新增 {added} 行 / 删除 {removed} 行（旧文件会先备份成 .bak）'


def status_text(state, unknown: int = 0) -> str:
    """
    主窗口状态栏那一行的**唯一实现**（第二阶段接；纯函数便于判据）。

    用户 2026-10-30 要的形态：`映射：项目文件` / `映射：按文件夹推断` /
    `映射：无（N 张未识别）`。

    来自 Excel 时后面多一句 `（Excel）`：**同一目录 Excel 优先于 JSON**，
    所以必须让人一眼看出"我改 JSON 没生效"是因为生效的是那份 Excel。
    """
    if state is None:
        base = f'映射：{INFER_TEXT}'
    elif state.error:
        base = '映射：读取失败（打开「资源映射」看原因）'
    elif state.has_data():
        base = f'映射：{state.label()}'
        if format_label(getattr(state, 'path', '')) == 'Excel':
            base += '（Excel）'
    else:
        base = f'映射：无（{INFER_TEXT}）'
    if unknown:
        base += f'（{unknown} 张未识别）'
    return base


def contrast_rows(registry, project_root, *, mapping=None) -> list:
    """
    资源对照表的数据（纯函数：注册表 + 项目根 → 一行一张图）。

    **不在这里按"只看未识别"过滤**：未识别张数是状态栏要用的数字，
    过滤是界面的显示选择。排序把未识别顶到最上面（要修的排前面），
    其余按 kind + 路径（稳定顺序，便于习惯位置）。

    `mapping` 给了就按**这一份**判（对话框里载入的映射），否则按项目里那份
    （`identity_map` 内部走 `mapping_cached`）。
    """
    images = getattr(registry, 'images', None) or {}
    idents = identity_map(registry, project_root, mapping=mapping)
    rows = []
    for aid in sorted(images):
        row = images.get(aid) or {}
        ident = idents.get(str(aid))
        kind = str(getattr(ident, 'kind', '') or '') if ident is not None else ''
        kind = kind or 'unknown'
        src = str(getattr(ident, 'source', '') or '') if ident is not None else ''
        tag = str(getattr(ident, 'tag', '') or '') if ident is not None else ''
        attrs = (' '.join(str(a) for a in (getattr(ident, 'attrs', ()) or ())
                          if str(a).strip())
                 if ident is not None else '')
        showable = (bool(getattr(ident, 'showable', False))
                    if ident is not None else False)
        rows.append(ContrastRow(
            asset_id=str(aid),
            relpath=str(row.get('Path') or '').strip(),
            kind=kind,
            kind_label=KIND_LABELS.get(kind, kind),
            tag=tag,
            attrs=attrs,
            source=src,
            source_label=SOURCE_LABELS.get(src, src or '—'),
            showable=showable,
        ))
    rows.sort(key=lambda r: (0 if r.is_unknown() else 1, r.kind,
                             r.relpath or r.asset_id))
    return rows


def unknown_count(rows) -> int:
    """资源对照表里"未识别"的张数（状态栏 / 提示条共用）。"""
    return sum(1 for r in (rows or []) if r.is_unknown())


# ==================================================================
#  对话框
# ==================================================================

class MappingDialog(QDialog):
    """
    资源映射：载入 / 重读 / 导出初稿 / 保存到项目根 / 资源对照表。

    用法（第二阶段主窗口「🗂 资源映射」菜单）::

        dlg = MappingDialog(self, registry=registry, project_root=root)
        dlg.exec()

    本类**不碰会话 / Beat / 主窗口**：只拿 registry + 项目根，所以可以脱离
    整个应用单测（`tests/test_mapping_dialog.py`）。
    """

    def __init__(self, parent=None, registry=None, project_root=None,
                 mapping_path: str = '', focus_image: str = ''):
        super().__init__(parent)
        self.setWindowTitle('资源映射')
        self.resize(1040, 720)
        theme.apply_to(self)

        self._registry = registry
        self._project_root = str(project_root or '').strip()
        #: 资源对照表的数据（`list[ContrastRow]`；映射变了要重算）
        self._rows = []
        #: 表里**当前显示**的行（"只看未识别"过滤之后；选中项按它换算）
        self._shown = []
        #: 表里改过、对照表还没按新映射重算（切到那一页时补算）
        self._contrast_stale = False
        #: 等确认的写入 `(target, data)`；None = 没有待写内容
        self._pending = None
        #: 当前映射（不可变快照，刷新换新对象）
        if mapping_path:
            self._state = state_from_file(mapping_path)
        else:
            self._state = state_from_project(self._project_root)
        self._build()
        self._refresh_all()
        #: 卡片上那条"去『资源映射』修"带过来的那条图 —— 开窗就落到它那一行。
        if focus_image:
            self.focus_image(focus_image)

    # ------------------------------------------------------------------
    #  建界面
    # ------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        title = QLabel('🗂 资源映射')
        title.setProperty('role', 'panelTitle')
        root.addWidget(title)

        # 当前这份映射是哪来的（人第一眼要看到的东西）。
        self._status = QLabel('')
        self._status.setObjectName('mapping_status')
        self._status.setWordWrap(True)
        self._status.setProperty('role', 'note')
        root.addWidget(self._status)

        # —— 动作条 ——
        bar = QHBoxLayout()
        bar.setSpacing(T.SPACE['sm'])
        self._btn_load = QPushButton('载入映射…')
        self._btn_load.setObjectName('btn_load')
        self._btn_load.setToolTip('读一份任意路径的映射 JSON（别人发来的也能吃）')
        self._btn_load.clicked.connect(self._on_load)
        self._btn_reload = QPushButton('重读')
        self._btn_reload.setObjectName('btn_reload')
        self._btn_reload.setToolTip(
            '从磁盘重新读一遍当前映射（外部编辑器改完不用重启工具）')
        self._btn_reload.clicked.connect(self._on_reload)
        self._btn_export = QPushButton('导出映射初稿…')
        self._btn_export.setObjectName('btn_export')
        self._btn_export.setToolTip(
            '按当前项目的目录结构 + 角色表生成一份映射初稿（导出后可改、可提交）')
        self._btn_export.clicked.connect(self._on_export)
        # 「给我一个模板」——用户 2026-10-31：空手填表太难，先要一张只有
        # 表头（和"这列填什么"）的样板。JSON / Excel 两种都给，看人习惯。
        self._btn_template = QPushButton('存一份空模板…')
        self._btn_template.setObjectName('btn_template')
        self._btn_template.setToolTip(
            '存一份只有表头的映射（.xlsx 六张表 / .json 六个键），'
            '照着列名填完再用「载入映射…」读回来')
        self._btn_template.clicked.connect(self._on_template)
        self._btn_save = QPushButton('保存到项目根')
        self._btn_save.setObjectName('btn_save')
        self._btn_save.setToolTip(
            '把当前这份映射写进项目（先给 diff，确认后才落盘；旧文件备份成 .bak）')
        self._btn_save.clicked.connect(self._on_save)
        for btn in (self._btn_load, self._btn_reload, self._btn_export,
                    self._btn_template, self._btn_save):
            bar.addWidget(btn, 0)
        bar.addStretch(1)
        root.addLayout(bar)

        # —— 三个页签：逐条编辑 / 原始文件 / 工具怎么认的 ——
        #
        # **逐条编辑排第一**（用户 2026-10-30 ⑧）：JSON 是产出格式，不是人的
        # 输入方式。人要"加一条 / 改一格"，第一眼就该看到表。
        #
        # 页签标题读 `TAB_TITLES`（唯一真源）：用户 2026-10-30 又说了一遍
        # "tag 一类的……谁知道你是怎么叫的呢"，于是「映射文件」→「原始文件」、
        # 「资源对照表」→「工具怎么认的」—— 屏幕上不留工具自己的行话。
        self._editor = MappingEditor(
            self, known_chars=self._known_char_entries(),
            project_root=self._project_root)
        self._editor.changed.connect(self._on_editor_changed)
        self._editor.note.connect(
            lambda text, warn=False: self.say(text, warn=warn))
        self._editor.save_requested.connect(self._on_save)
        self._tabs = QTabWidget()
        self._tabs.setObjectName('mapping_tabs')
        self._tabs.addTab(self._editor, TAB_TITLES[TAB_EDIT])
        self._tabs.addTab(self._build_file_tab(), TAB_TITLES[TAB_FILE])
        self._tabs.addTab(self._build_table_tab(), TAB_TITLES[TAB_TABLE])
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._tabs, 1)

        # —— 写盘前的 diff（点了「保存到项目根」才出现） ——
        self._diff_label = QLabel('')
        self._diff_label.setObjectName('diff_label')
        self._diff_label.setWordWrap(True)
        self._diff_label.setProperty('role', 'warn')
        self._diff_label.setVisible(False)
        root.addWidget(self._diff_label)
        self._diff = QPlainTextEdit()
        self._diff.setObjectName('diff')
        self._diff.setReadOnly(True)
        self._diff.setMaximumHeight(150)
        self._diff.setVisible(False)
        self._set_mono(self._diff)
        root.addWidget(self._diff)
        self._confirm_bar = QWidget()
        cb = QHBoxLayout(self._confirm_bar)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.addStretch(1)
        self._btn_confirm = QPushButton('确认写入')
        self._btn_confirm.setObjectName('btn_confirm')
        self._btn_confirm.setProperty('variant', 'primary')
        self._btn_confirm.clicked.connect(self.confirm_save)
        self._btn_cancel_write = QPushButton('不写了')
        self._btn_cancel_write.setObjectName('btn_cancel_write')
        self._btn_cancel_write.clicked.connect(self.cancel_save)
        cb.addWidget(self._btn_cancel_write, 0)
        cb.addWidget(self._btn_confirm, 0)
        self._confirm_bar.setVisible(False)
        root.addWidget(self._confirm_bar)

        self._hint = QLabel('')
        self._hint.setObjectName('mapping_hint')
        self._hint.setWordWrap(True)
        self._hint.setProperty('role', 'note')
        root.addWidget(self._hint)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self._btn_close = QPushButton('关闭')
        self._btn_close.setObjectName('btn_close')
        self._btn_close.clicked.connect(self.reject)
        bottom.addWidget(self._btn_close, 0)
        root.addLayout(bottom)

    def _build_file_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, T.SPACE['sm'], 0, 0)
        lay.setSpacing(T.SPACE['xs'])
        desc = QLabel(
            f'这里是这份说明文件的原文（{MAPPING_NAME}；同名的 Excel '
            f'{MAPPING_XLSX} 也认，两者都可以放在 game/ 下）。工具读的就是它：'
            f'哪张图是什么、谁是谁、哪张是预览图。没写到的图 —— 工具按文件夹和'
            f'文件名自己猜（在「工具怎么认的」页能看到它是怎么猜的）。')
        desc.setWordWrap(True)
        desc.setProperty('role', 'note')
        lay.addWidget(desc)
        self._json = QPlainTextEdit()
        self._json.setObjectName('mapping_json')
        self._json.setReadOnly(True)
        self._set_mono(self._json)
        lay.addWidget(self._json, 1)
        self._warn = QLabel('')
        self._warn.setObjectName('mapping_warn')
        self._warn.setWordWrap(True)
        self._warn.setProperty('role', 'warn')
        lay.addWidget(self._warn)
        return w

    def _build_table_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, T.SPACE['sm'], 0, 0)
        lay.setSpacing(T.SPACE['xs'])
        top = QHBoxLayout()
        self._only_unknown = QCheckBox('只看未识别')
        self._only_unknown.setObjectName('only_unknown')
        self._only_unknown.setToolTip(
            '只留工具"没认出来"的图 —— 要么在这里「加进表里」告诉它，'
            '要么去修资源表')
        self._only_unknown.toggled.connect(lambda *_: self._fill_table())
        top.addWidget(self._only_unknown, 0)
        self._table_count = QLabel('')
        self._table_count.setProperty('role', 'caption')
        top.addWidget(self._table_count, 0)
        top.addStretch(1)
        self._btn_add_mapping = QPushButton('加进表里…')
        self._btn_add_mapping.setObjectName('btn_add_mapping')
        self._btn_add_mapping.setToolTip(
            '把选中的图写进「逐条编辑」的图片表（只是填表，还要点'
            '「保存到项目根」才落盘）—— 未识别的图就是从这里补的')
        self._btn_add_mapping.clicked.connect(
            lambda *_: self.add_selected_to_mapping())
        top.addWidget(self._btn_add_mapping, 0)
        self._btn_rescan = QPushButton('重新扫描')
        self._btn_rescan.setObjectName('btn_rescan')
        self._btn_rescan.setToolTip('按当前映射把整份资源表重新判一遍')
        self._btn_rescan.clicked.connect(lambda *_: self.refresh_contrast(force=True))
        top.addWidget(self._btn_rescan, 0)
        lay.addLayout(top)
        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setObjectName('contrast_table')
        self._table.setHorizontalHeaderLabels(list(COLUMNS))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        head = self._table.horizontalHeader()
        # ⚠ **不许用 `ResizeToContents`**：真实项目 1720 行 × 6 列，每次
        # `setItem` 都会触发一次"扫描全表算列宽"，合计 O(行²) —— 实测在
        # 表格可见时点「导出初稿」（会重建整张表）直接卡死几分钟。
        # 改成固定初值 + 填完**一次性** `resizeColumnsToContents()`。
        for i in range(len(COLUMNS)):
            head.setSectionResizeMode(i, QHeaderView.Interactive)
        for i, width in enumerate((400, 190, 90, 150, 150, 80)):
            self._table.setColumnWidth(i, width)
        lay.addWidget(self._table, 1)
        return w

    @staticmethod
    def _set_mono(widget):
        """等宽字体（JSON / diff 对齐才看得出改了哪一行）。"""
        try:
            family = T.FONTS['mono'][0]
            pt = int(T.FONTS['mono'][1])
            widget.setStyleSheet(f'font-family:"{family}";font-size:{pt}pt;')
        except Exception as e:                    # noqa: BLE001 - 字体没有就退回默认
            swallow('ui_qt.mapping_dialog._set_mono', e)

    # ------------------------------------------------------------------
    #  逐条编辑（表格式）—— 与别的入口共用"保存到项目根"这一条写盘路
    # ------------------------------------------------------------------

    def _known_char_entries(self) -> dict:
        """
        资源表里认得出的角色 `{tag: {'display','aliases','avatar'}}`。

        来源是资源表自己的「角色」那张表（`known_characters`），不是映射 ——
        映射里的 `characters` 是**覆盖层**，这段空着时名字照样从表里来。
        所以这里只回答"表里有谁"，要不要写进映射由人决定（见编辑页的
        「把资源表里的角色写进来」）。
        """
        cached = getattr(self, '_known_entries', None)
        if cached is not None:
            return dict(cached)
        entries: dict = {}
        if self._registry is not None:
            try:
                entries = dict(known_characters(self._registry) or {})
            except Exception as e:                # noqa: BLE001 - 建议而已
                swallow('ui_qt.mapping_dialog.known_char_entries', e)
        self._known_entries = entries
        return dict(entries)

    def _known_chars(self) -> dict:
        """`{tag: 显示名}`（资源表里的角色；界面上说人话用）。"""
        out = {}
        for tag, value in self._known_char_entries().items():
            entry = value if isinstance(value, dict) else {'display': value}
            out[str(tag)] = str((entry or {}).get('display') or '')
        return out

    def _load_editor(self):
        """
        把当前映射灌进表里，并把"这份不能改"的理由摆在表头上。

        读失败时整页禁用：那种情况下表是空的，人一改一存就把原文件盖成空白
        （铁律第 5 条：不许静默全量覆盖）。
        """
        state = self._state
        if state.error:
            self._editor.load({})
            self._editor.setEnabled(False)
            self._editor.set_banner(
                f'⚠ 当前这份映射读不出来（{state.error}）—— 先修好文件再回这里'
                f'改；现在保存会把文件盖成空白。')
            return
        self._editor.setEnabled(True)
        self._editor.load(state.data if state.has_data() else {})
        self._autofill_codes_once()
        active = str(state.path or '')
        if active and AM.is_workbook(active):
            self._editor.set_banner(
                f'现在生效的是 {Path(active).name}（同一目录 Excel 优先于 JSON）。'
                f'你在这里改的是 JSON：保存只会写同目录的一份 JSON 快照，'
                f'游戏里仍然用那份 Excel。真要改请直接改 .xlsx；'
                f'想改用 JSON 维护，先把那份 .xlsx 改名。')
        else:
            self._editor.set_banner('')

    def _autofill_codes_once(self):
        """
        **导入时自动建表**：映射里还没有代号两段时，照项目现状数一份填进去。

        用户 2026-10-31："导入时自动建一个表，提供一个模板xlsx或者json文件"。
        所以这里不是"给个空表让他在 3889 行里自己填"，而是打开就有内容 ——
        表情编号读角色文件夹里的 `对应.txt`，服装字母从剧本注释里数。

        2026-11-08 补：**音乐这类编号表也算**。真实项目 `sr_general` 的
        `game/bgm/对应.txt`（`1——Empt1y` … 剧本写 `# bgm13`）是同一件事，
        从前只认角色文件夹，于是音乐永远进不了映射。现在「中文叫法」那段
        也按 `code_tables` 填；**两件事分开判**：表情两段已经有人填过，
        不代表音乐编号也该跳过。

        只填空表：某一段里只要已经有内容就整段不动（那是人写的，或者上次
        自动填完又改过的），免得每次开窗都把人改的东西冲回去。
        """
        data = self._editor.current_data()
        want_codes = not any(data.get(name) for name in
                             ('outfit_codes', 'expression_codes'))
        want_vocabulary = not data.get('vocabulary')
        if not (want_codes or want_vocabulary):
            return
        if not str(getattr(self, '_project_root', '') or '').strip():
            return
        counts = self._editor.autofill_codes(codes=want_codes,
                                             vocabulary=want_vocabulary)
        if not counts:
            return
        parts = []
        if counts.get('outfits') or counts.get('expressions'):
            parts.append(f'服装 {counts.get("outfits", 0)} / 表情 '
                         f'{counts.get("expressions", 0)} 条代号')
        if counts.get('vocabulary'):
            parts.append(f'中文叫法 {counts["vocabulary"]} 条'
                         '（音乐/音效的编号表）')
        if parts:
            self.say(f'已经照项目自动填了 {"，".join(parts)} —— 还没保存，'
                     f'核一下「服装代号」「表情编号」「中文叫法」几段，再点'
                     f'「保存到项目根」。')

    def current_data(self) -> dict:
        """当前这份映射（**表优先** —— 它是人正在改的那份；表空就是空）。"""
        return self._editor.current_data()

    def _on_editor_changed(self):
        self._refresh_json()
        self._refresh_status()
        self._refresh_buttons()
        # 资源对照表（真机 1720 张，半秒级）不在每次敲键盘时重算：
        # 切到那一页 / 点「重新扫描」再算，见 `_on_tab_changed`。
        self._contrast_stale = True

    def _on_tab_changed(self, index: int):
        if index == TAB_TABLE and self._contrast_stale:
            self._contrast_stale = False
            self.refresh_contrast(force=True)

    def _selected_contrast_rows(self) -> list:
        """对照表里选中的那些图（按**显示行**映射回数据行，过滤也认）。"""
        shown = getattr(self, '_shown', None) or self._rows
        picked = {item.row() for item in self._table.selectedItems()
                  if item is not None}
        return [shown[i] for i in sorted(picked) if 0 <= i < len(shown)]

    def add_selected_to_mapping(self) -> int:
        """「加进表里…」：把这一页选中的图填进「逐条编辑」的单张图那段（不落盘）。"""
        rows = self._selected_contrast_rows()
        if not rows:
            self.say('先在对照表里选中要补的图（按住 Ctrl 可以多选）。', warn=True)
            return 0
        added = self._editor.add_images([self._mapping_row_for(row)
                                         for row in rows])
        # 表里已经说了一句人话（「已补进 N 条…」走 `note` → 底部提示条），
        # 这里只管把人送到"能看见那几行"的地方。
        self._tabs.setCurrentIndex(TAB_EDIT)
        return added

    def _mapping_row_for(self, row) -> dict:
        """一张图 → 映射条目的初稿（**只填推断得出的，不猜**）。"""
        fields = {}
        if row.kind and row.kind != 'unknown':
            fields['kind'] = row.kind
        if row.tag and row.kind in ('sprite', 'preview'):
            fields['char'] = row.tag
        if row.attrs and row.kind == 'sprite':
            fields['attr'] = row.attrs
        return {'key': self._key_for(row), **fields}

    def _key_for(self, row) -> str:
        """这张图在映射里用什么键：资产 ID 唯一就用它，否则用相对路径。"""
        aid = str(getattr(row, 'asset_id', '') or '').strip()
        same = [r for r in self._rows if str(r.asset_id) == aid]
        if aid and len(same) == 1:
            return aid
        return str(getattr(row, 'relpath', '') or '').strip() or aid

    # ------------------------------------------------------------------
    #  「卡片上那张图 → 直接落到那一行」（用户 2026-10-30 起的闭环）
    # ------------------------------------------------------------------

    def _row_for_spec(self, spec):
        """
        一条 image spec（`ling happy` / `bg bg9_5`）→ 对照表里那一条。

        匹配顺序：资产 ID 完全相同 → 相对路径完全相同 → 「tag + 属性」。
        找不到就返回 `None`（**不猜**：猜错等于把人送到错的那一行填）。
        """
        text = str(spec or '').strip()
        if not text:
            return None
        words = text.split()
        head, rest = words[0], ' '.join(words[1:])
        for row in self._rows:
            if str(row.asset_id) == text or str(row.asset_id) == head:
                return row
        for row in self._rows:
            if str(row.relpath) == text:
                return row
        for row in self._rows:
            tag = str(row.tag or '')
            if not tag or tag != head:
                continue
            attrs = str(row.attrs or '')
            if not rest or rest in attrs:
                return row
        return None

    def focus_image(self, spec) -> bool:
        """
        从卡片的「去『资源映射』修」进来时，**直接落到那张图上**：

          · 表里已经有了 ⇒ 选中那一行、翻到「逐条编辑」；
          · 表里还没有、但资源表里认得出 ⇒ 按推断结果先补一条并选中
            （人只需补空着的那几格，再点「保存到项目根」）；
          · 资源表里也找不到 ⇒ 老实说一句，并把人送到「单张图片」那段。
        """
        text = str(spec or '').strip()
        if not text:
            return False
        row = self._row_for_spec(text)
        if row is None:
            self._tabs.setCurrentIndex(TAB_EDIT)
            self._editor.set_section('images')
            self.say(f'没在资源表里找到「{text}」这张图 —— 可以在「单张图片」里点'
                     f'「选一张图…」直接挑，或去「工具怎么认的」页里找它。',
                     warn=True)
            return False
        self._editor.add_images([self._mapping_row_for(row)])
        self._tabs.setCurrentIndex(TAB_EDIT)
        return True

    # ------------------------------------------------------------------
    #  刷新
    # ------------------------------------------------------------------

    def state(self) -> MappingState:
        """当前映射的只读快照（测试 / 第二阶段状态栏都用它）。"""
        return self._state

    def unknown_count(self) -> int:
        """资源表里"未识别"的张数（状态栏那一行要用）。"""
        return unknown_count(self._rows)

    def status_line(self) -> str:
        """状态栏那一行（第二阶段主窗口直接调 `status_text`）。"""
        return status_text(self._state, self.unknown_count())

    def _refresh_all(self):
        self._load_editor()
        self._refresh_status()
        self._refresh_json()
        self.refresh_contrast(force=True)
        self._refresh_buttons()

    def _refresh_status(self):
        # 一行说清两件事：**这次按哪份认** + **那份文件在哪**。
        # 从前是 `映射：手动载入的文件   手动载入的文件：D:\…` —— 同一句话
        # 在同一个屏幕上出现两遍（截图里像界面出了错），下半句改成"这次用的是"。
        if self._state.path:
            where = f'这次用的是 {self._state.path}'
        else:
            where = ('项目里还没有这份说明文件 —— 工具先按文件夹和文件名猜；'
                     '填好这张表、点「保存到项目根」，就会新建一份。')
        text = f'{self.status_line()}　　{where}'
        if self._editor.is_dirty():
            text += '　　· 表里有未保存的改动'
            self._btn_close.setToolTip('关闭窗口（表里未保存的改动会丢）')
        else:
            self._btn_close.setToolTip('关闭窗口')
        self._status.setText(text)
        self._status.setProperty('role',
                                 'warn' if self._state.error else 'note')
        self._repolish(self._status)

    def _refresh_json(self):
        fmt = format_label(self._state.path) if self._state.path else ''
        data = self.current_data()
        if self._state.error:
            self._json.setPlainText('')
            self._json.setPlaceholderText('（这份映射读不出来，见下面的原因）')
        elif not data:
            self._json.setPlainText('')
            self._json.setPlaceholderText(
                '（还没有映射。两种起手都行：① 点「导出映射初稿…」按项目结构'
                '生成一份；② 直接在「逐条编辑」页点「新增一行」手工定义。'
                '存 .json 或 .xlsx 都行；「载入映射…」能读别人发来的。）')
        else:
            self._json.setPlainText(json_text(data))
        warn = []
        if self._editor.is_dirty():
            warn.append('ℹ 下面是表里当前的内容（含还没保存的改动）—— '
                        '点「保存到项目根」才写进文件。')
        if fmt == 'Excel':
            warn.append('ℹ 这份映射来自 Excel：下面是工具读出来的等价 JSON。'
                        '要改请改那份 .xlsx（工具不会回写它），改完点「重读」。')
        if self._state.error:
            warn.append(f'⚠ 读不出来：{self._state.error}')
        if self._state.warnings:
            warn.append('⚠ ' + '；'.join(str(w) for w in self._state.warnings))
        self._warn.setText('\n'.join(warn))
        self._warn.setVisible(bool(warn))
        self._warn.setProperty(
            'role', 'warn' if (self._state.error or self._state.warnings) else 'note')
        self._repolish(self._warn)

    def refresh_contrast(self, *, force: bool = False):
        """
        重算资源对照表（映射变了 / 点了「重新扫描」才重算）。

        判定走 `asset_identity.resolve_image`，用的是**当前加载的这份映射**：
        所以"改映射 → 重读 → 表里那一行变了"是能当场看见的反馈。
        """
        if force or not self._rows:
            # 按**表里当前这份**判（不是磁盘上那份）：改一格再切回这一页，
            # 就能看见"这张图现在被认成什么了"。表空 = 明确"没有映射"，
            # 传 {} 而不是 None —— None 的语义是"去读项目里那份"。
            self._rows = contrast_rows(
                self._registry, self._project_root,
                mapping=self.current_data())
        self._fill_table()
        self._refresh_status()
        self._refresh_buttons()

    def _fill_table(self):
        from PySide6.QtGui import QBrush, QColor

        rows = self._rows
        if self._only_unknown.isChecked():
            rows = [r for r in rows if r.is_unknown()]
        self._shown = rows
        # 大表整批重建时先关更新：否则每次 `setItem` 都会触发一次布局/重绘
        # （1720 行实测能把界面卡几十秒）。填完再一次性开更新 + 量列宽。
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(len(rows))
            # 未识别 = 要人去修的：用警示色点出来（仍可选中 / 复制）。
            warn_brush = QBrush(QColor(theme.t('state.warning')))
            for i, row in enumerate(rows):
                for j, text in enumerate(row.cells()):
                    item = QTableWidgetItem(str(text))
                    if row.is_unknown():
                        item.setForeground(warn_brush)
                    self._table.setItem(i, j, item)
                first = self._table.item(i, 0)
                if first is not None:
                    first.setToolTip(
                        f'资产 ID：{row.asset_id}\n'
                        f'文件：{row.relpath or "（没登记路径）"}\n'
                        f'判定：{row.kind_label}（依据：{row.source_label}）\n'
                        f'tag：{row.tag or "（没有）"}   '
                        f'属性：{row.attrs or "（没推断出）"}')
            self._table.resizeColumnsToContents()
        finally:
            self._table.setUpdatesEnabled(True)
        n = len(self._rows)
        k = unknown_count(self._rows)
        suffix = '（只看未识别）' if self._only_unknown.isChecked() else ''
        self._table_count.setText(f'共 {n} 张图 · 未识别 {k} 张{suffix}')

    def _refresh_buttons(self):
        has_data = bool(self.current_data())
        can_write = bool(self._project_root) and has_data
        self._btn_save.setEnabled(can_write)
        self._editor.set_can_save(can_write)
        if not self._project_root:
            self._btn_save.setToolTip('还没有打开项目 —— 没有"项目根"可写。')
        elif not has_data:
            self._btn_save.setToolTip(
                '表是空的 —— 先在「逐条编辑」页加一条，或「导出映射初稿…」。')
        else:
            self._btn_save.setToolTip(
                '把当前这份映射写进项目（先给 diff，确认后才落盘；旧文件备份成 .bak）')
        self._btn_export.setEnabled(self._registry is not None)
        if self._registry is None:
            self._btn_export.setToolTip('没有资源表 —— 生成不了初稿。')

    # ------------------------------------------------------------------
    #  动作：载入 / 重读 / 导出 / 保存
    # ------------------------------------------------------------------

    def _set_state(self, state: MappingState, hint: str = ''):
        self._state = state
        self._pending = None
        self._hide_diff()
        self._load_editor()
        self._refresh_status()
        self._refresh_json()
        self.refresh_contrast(force=True)
        self._contrast_stale = False
        if hint:
            self.say(hint)

    def say(self, text: str, *, warn: bool = False):
        """底部提示条（**一句话**：做了什么 / 为什么不行）。"""
        self._hint.setText(str(text or ''))
        self._hint.setProperty('role', 'warn' if warn else 'note')
        self._repolish(self._hint)

    def load_mapping_file(self, path) -> bool:
        """
        载入任意路径的映射。坏文件只提示、不换状态、不闪退。

        返回 True = 读成功并已生效。
        """
        # 载入的正好是**项目里那份**时，状态说"项目文件"而不是"手动载入"
        # （它确实是项目文件 —— 状态栏要能一眼看出"工具会自动用它"）。
        kind = SOURCE_LOADED
        try:
            project_path = AM.find_mapping_path(self._project_root)
        except Exception as e:                    # noqa: BLE001 - 找不到当没有
            swallow('ui_qt.mapping_dialog.load.find_project', e)
            project_path = None
        if project_path and _same_file(path, project_path):
            kind = SOURCE_PROJECT
        state = state_from_file(path, kind=kind)
        if state.error:
            self.say(f'⚠ 这份映射读不出来：{state.error}', warn=True)
            return False
        self._set_state(state, f'已载入映射：{state.path}')
        if state.warnings:
            self.say(f'已载入映射：{state.path}（有 {len(state.warnings)} 条提醒，'
                     f'见「{TAB_TITLES[TAB_FILE]}」页底部）')
        # 载入后直接给人看**表**：那是"这份映射里有什么"最像人话的样子。
        self._tabs.setCurrentIndex(TAB_EDIT)
        return True

    def reload_mapping(self) -> bool:
        """把当前这份从磁盘重读一遍（外部改完不用重启工具）。"""
        dropped = self._editor.is_dirty()
        if self._state.path:
            state = state_from_file(self._state.path, kind=self._state.kind)
        else:
            state = state_from_project(self._project_root, force=True)
        if state.error:
            self.say(f'⚠ 重读失败：{state.error}', warn=True)
            return False
        tail = '（表里未保存的改动已丢弃）' if dropped else ''
        if not state.path:
            self._set_state(state,
                            f'项目里没有映射文件 —— 工具{INFER_TEXT}。{tail}')
            return True
        self._set_state(state, f'已重读：{state.path}{tail}')
        return True

    def draft_data(self):
        """按当前项目生成映射初稿（没有资源表返回 None）。"""
        if self._registry is None:
            return None
        ctx = pick_context(self._registry, self._project_root)
        known_chars = dict(ctx.get('known_chars') or {})
        images = getattr(self._registry, 'images', None) or {}
        name = Path(self._project_root).name if self._project_root else ''
        try:
            return MD.draft_mapping(self._project_root, images, known_chars,
                                    project_name=name)
        except Exception as e:                    # noqa: BLE001 - 坏注册表别闪退
            swallow('ui_qt.mapping_dialog.draft_data', e)
            self.say(f'⚠ 生成初稿失败：{e}', warn=True)
            return None

    def export_draft(self, path) -> bool:
        """
        把初稿写到 `path`，并把它当作"当前映射"（可以接着改、接着保存）。

        真实项目 1720 行实测 `draft_mapping` 约 **10 秒**（S1 的纯函数，按目录
        汇总规则），所以这里先给一句"正在生成" + 忙碌光标，别让人以为卡死。

        写成 `.xlsx` 时多一句实话：**同一位置 Excel 优先于 JSON**，所以把表
        导出到项目根就等于把它设成生效源（这件事必须当场说，不能事后猜）。

        返回 True = 写出成功。
        """
        from PySide6.QtWidgets import QApplication

        self.say('正在按项目结构生成映射初稿…（大项目约 10 秒，先别关窗）')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            draft = self.draft_data()
            if draft is None:
                self.say('⚠ 没有资源表 / 生成失败，导不出初稿。', warn=True)
                return False
            try:
                counts = AM.save_mapping(str(path), draft)
            except AM.MappingError as e:
                self.say(f'⚠ 写不进这个文件：{e}', warn=True)
                return False
            state = state_from_file(path)
            if state.error:
                self._set_state(
                    state, f'初稿已写到 {path}，但读回来失败：{state.error}')
                return True
            detail = ''
            if isinstance(counts, dict):
                detail = (f'（角色 {counts.get("characters", 0)} 条 · '
                          f'规则 {counts.get("rules", 0)} 条 · '
                          f'图片 {counts.get("images", 0)} 条 · '
                          f'属性 {counts.get("attributes", 0)} 条）')
            hint = (f'已导出映射初稿：{path}'
                    f'（按项目目录 + 角色表生成；改完可「保存到项目根」）')
            if AM.is_workbook(path):
                hint = (f'已导出映射初稿：{path}{detail}'
                        f'　⚠ 同一位置 Excel 优先于 JSON：以后打开这个项目就以这份'
                        f' Excel 为准了（在 Excel 里改完点「重读」即可）')
            elif detail:
                hint = f'已导出映射初稿：{path}{detail}'
            self._set_state(state, hint)
            self._tabs.setCurrentIndex(TAB_EDIT)
            return True
        finally:
            QApplication.restoreOverrideCursor()

    def prepare_save(self) -> bool:
        """
        「保存到项目根」第一步：算出写哪儿 + 先给人看 diff，**一个字节都不写**。

        两步走的原因：这是全量覆盖一份项目文件（铁律第 5 条）。不弹第二个
        模态框（本仓库踩过模态套模态挂死测试的坑），diff 就摆在对话框底部。
        """
        data = self.current_data()
        if not data:
            self.say('⚠ 当前没有可保存的映射（先在「逐条编辑」页加一条，'
                     '或「导出映射初稿…」）。', warn=True)
            return False
        target = write_target(self._project_root)
        if not target:
            self.say('⚠ 还没有打开项目 —— 没有"项目根"可写。', warn=True)
            return False
        before = ''
        try:
            p = Path(target)
            if p.is_file():
                # 与 `load_mapping` 同一口径（UTF-8 **含 BOM** 也认），否则
                # 现有文件带 BOM 时 diff 会多出一条"删除了空字符"的假改动。
                before = p.read_text(encoding='utf-8-sig')
        except OSError as e:
            self.say(f'⚠ 读不了现有文件：{e}', warn=True)
            return False
        after = json_text(data)
        self._pending = (target, data)
        note = ''
        active = str(getattr(self._state, 'path', '') or '')
        if active and AM.is_workbook(active):
            note = (f'　⚠ 当前生效的是 {Path(active).name}：这里写的是同目录的 '
                    f'{Path(target).name} 快照，不会动那份 Excel，'
                    f'也不会改变"以 Excel 为准"。')
        bad = [p for p in self._editor.problems() if p.level == 'bad']
        if bad:
            note += (f'　⚠ 表里有 {len(bad)} 条不会生效（红字那几行）—— '
                     f'保存不拦，但建议先改。')
        self._diff_label.setText(
            f'将要写入：{target} —— {diff_summary(before, after)}{note}')
        self._diff.setPlainText('\n'.join(diff_lines(before, after))
                               or '（内容没有变化，写入也不会改变文件）')
        self._diff_label.setVisible(True)
        self._diff.setVisible(True)
        self._confirm_bar.setVisible(True)
        self._tabs.setCurrentIndex(TAB_FILE)
        self.say('确认要写这份文件吗？点「确认写入」才落盘（旧文件会备份成 .bak）。')
        return True

    def confirm_save(self) -> bool:
        """「保存到项目根」第二步：真写（原子写 + 旧文件 .bak，见 asset_mapping）。"""
        if not self._pending:
            return self.prepare_save()
        target, data = self._pending
        try:
            AM.save_mapping(str(target), data)
        except AM.MappingError as e:
            self.say(f'⚠ 写不进去：{e}', warn=True)
            return False
        self._pending = None
        self._hide_diff()
        state = state_from_project(self._project_root, force=True)
        self._set_state(
            state,
            f'已写入 {target}（旧文件备份成 {Path(target).name}.bak）')
        return True

    def cancel_save(self):
        """不写了：diff 收起来，项目里一个字节都没动。"""
        self._pending = None
        self._hide_diff()
        self.say('没有写入任何文件。')

    def _hide_diff(self):
        self._diff_label.setVisible(False)
        self._diff.setVisible(False)
        self._confirm_bar.setVisible(False)
        self._diff.setPlainText('')

    # ------------------------------------------------------------------
    #  按钮回调（只做"选路径 / 调上面那些方法"，不藏业务）
    # ------------------------------------------------------------------

    def _on_load(self):
        start = self._project_root or ''
        path, _ = QFileDialog.getOpenFileName(
            self, '载入资源映射（JSON / Excel）', start, MAPPING_FILTERS)
        if path:
            self.load_mapping_file(path)

    def _on_reload(self):
        self.reload_mapping()

    def _on_export(self):
        if self._registry is None:
            self.say('⚠ 没有资源表 —— 生成不了初稿。', warn=True)
            return
        default = str(Path(self._project_root or '.') / MAPPING_NAME)
        path, _ = QFileDialog.getSaveFileName(
            self, '导出映射初稿（.json 可提交 / .xlsx 可手工维护）',
            default, MAPPING_FILTERS)
        if path:
            self.export_draft(path)

    def _on_save(self):
        if self._pending:
            self.confirm_save()
            return
        self.prepare_save()

    def _on_template(self):
        """「存一份空模板…」：只有表头的 .xlsx 或 .json（不碰项目里的任何文件）。"""
        default = str(Path(self._project_root or '.') / '资源映射模板.xlsx')
        path, _ = QFileDialog.getSaveFileName(
            self, '存一份空模板（.xlsx 六张表 / .json 六个键）',
            default, MAPPING_FILTERS)
        if path:
            self.export_template(path)

    def export_template(self, path) -> str:
        """
        把空模板写到 `path`，返回一句给人看的话（失败也返回，不抛）。

        模板就是 `mapping_excel.blank_template()`：六段全空、但**表头齐**。
        写到哪都行（桌面上也行），**绝不写项目根** —— 那是「保存」按钮的事。
        """
        target = str(path or '').strip()
        if not target:
            return ''
        try:
            from storygraph.assets import mapping_excel as MX

            data = MX.blank_template()
            if AM.is_workbook(target):
                counts = MX.write_workbook(target, data)
                detail = '、'.join(f'{k} {v}' for k, v in counts.items() if v)
                detail = detail or '六张表（都是空的）'
                text = f'空模板已存到 {target}（{detail}）'
            else:
                AM.save_mapping(target, data)
                text = f'空模板已存到 {target}（六个键，都是空的）'
        except Exception as e:                    # noqa: BLE001 - 说人话，不吞
            self.say(f'⚠ 模板没存成：{e}', warn=True)
            return ''
        self.say(text + '。照着列名填，再用「载入映射…」读回来。')
        return text

    def closeEvent(self, event):
        # 有待写内容不算"未保存的改动"（diff 只是预览，没落盘就是没写）。
        self._pending = None
        super().closeEvent(event)

    # ------------------------------------------------------------------

    @staticmethod
    def _repolish(widget):
        """`role` 变了要重新 polish，QSS 才会换色。"""
        try:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.mapping_dialog._repolish', e)


__all__ = ['MAPPING_NAME', 'MAPPING_XLSX', 'MAPPING_FILTERS', 'COLUMNS',
           'SOURCE_PROJECT', 'SOURCE_LOADED',
           'SOURCE_NONE', 'MappingState', 'ContrastRow',
           'state_from_file', 'state_from_project', 'write_target',
           'json_text', 'diff_lines', 'diff_summary', 'status_text',
           'contrast_rows', 'unknown_count', 'format_label', 'MappingDialog']
