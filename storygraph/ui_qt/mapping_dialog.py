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
  · 映射初稿怎么生成        → `assets.asset_mapping.draft_mapping`

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
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.asset_picker import (KIND_LABELS, SOURCE_LABELS,
                                           identity_map, pick_context)

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

#: 资源对照表的列（顺序就是人看图的顺序：先看是哪个文件，再看凭什么这么判）。
COLUMNS = ('图片路径', '推断', 'tag', '属性', '依据', '能否 show')

#: 状态栏那一行里"没有映射时"的说法（用户 2026-10-30 要求原话）。
INFER_TEXT = '按文件夹推断'


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
                 mapping_path: str = ''):
        super().__init__(parent)
        self.setWindowTitle('资源映射')
        self.resize(1040, 720)
        theme.apply_to(self)

        self._registry = registry
        self._project_root = str(project_root or '').strip()
        #: 资源对照表的数据（`list[ContrastRow]`；映射变了要重算）
        self._rows = []
        #: 等确认的写入 `(target, data)`；None = 没有待写内容
        self._pending = None
        #: 当前映射（不可变快照，刷新换新对象）
        if mapping_path:
            self._state = state_from_file(mapping_path)
        else:
            self._state = state_from_project(self._project_root)
        self._build()
        self._refresh_all()

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
        self._btn_save = QPushButton('保存到项目根')
        self._btn_save.setObjectName('btn_save')
        self._btn_save.setToolTip(
            '把当前这份映射写进项目（先给 diff，确认后才落盘；旧文件备份成 .bak）')
        self._btn_save.clicked.connect(self._on_save)
        for btn in (self._btn_load, self._btn_reload, self._btn_export,
                    self._btn_save):
            bar.addWidget(btn, 0)
        bar.addStretch(1)
        root.addLayout(bar)

        # —— 两个页签：映射文件 / 资源对照表 ——
        self._tabs = QTabWidget()
        self._tabs.setObjectName('mapping_tabs')
        self._tabs.addTab(self._build_file_tab(), '映射文件')
        self._tabs.addTab(self._build_table_tab(), '资源对照表')
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
            f'项目根的 {MAPPING_NAME}（Excel 版 {MAPPING_XLSX} 也认；'
            f'两者都可以放在 game/ 下）。它是**唯一"项目知识"载体**：贴图在哪、'
            f'谁是谁、哪张是预览图，都写在这里；工具只按它覆盖/纠正自己的推断。'
            f'没写到的图 = 按文件夹/文件名推断（对照表里能看到依据）。')
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
            '只留在对照表里"没认出来"的图 —— 这些要么补映射，要么资源表就该修')
        self._only_unknown.toggled.connect(lambda *_: self._fill_table())
        top.addWidget(self._only_unknown, 0)
        self._table_count = QLabel('')
        self._table_count.setProperty('role', 'caption')
        top.addWidget(self._table_count, 0)
        top.addStretch(1)
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
        self._refresh_status()
        self._refresh_json()
        self.refresh_contrast(force=True)
        self._refresh_buttons()

    def _refresh_status(self):
        if self._state.path:
            where = f'{self._state.label()}：{self._state.path}'
        else:
            where = f'没有映射文件 —— 工具{INFER_TEXT}。'
        self._status.setText(f'{self.status_line()}    {where}')
        self._status.setProperty('role',
                                 'warn' if self._state.error else 'note')
        self._repolish(self._status)

    def _refresh_json(self):
        fmt = format_label(self._state.path) if self._state.path else ''
        if self._state.error:
            self._json.setPlainText('')
            self._json.setPlaceholderText('（这份映射读不出来，见下面的原因）')
        elif not self._state.data:
            self._json.setPlainText('')
            self._json.setPlaceholderText(
                '（还没有映射。点「导出映射初稿…」按项目结构生成一份 —— '
                '存 .json 或 .xlsx 都行；或「载入映射…」读别人发来的。）')
        else:
            self._json.setPlainText(json_text(self._state.data))
        warn = []
        if fmt == 'Excel':
            warn.append('ℹ 这份映射来自 Excel：下面是工具读出来的**等价 JSON**。'
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
            self._rows = contrast_rows(
                self._registry, self._project_root,
                mapping=self._state.data if self._state.has_data() else None)
        self._fill_table()
        self._refresh_status()
        self._refresh_buttons()

    def _fill_table(self):
        from PySide6.QtGui import QBrush, QColor

        rows = self._rows
        if self._only_unknown.isChecked():
            rows = [r for r in rows if r.is_unknown()]
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
        has_state = self._state.has_data()
        can_write = bool(self._project_root) and has_state
        self._btn_save.setEnabled(can_write)
        if not self._project_root:
            self._btn_save.setToolTip('还没有打开项目 —— 没有"项目根"可写。')
        elif not has_state:
            self._btn_save.setToolTip('当前没有可保存的映射（先载入或导出初稿）。')
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
        self._refresh_status()
        self._refresh_json()
        self.refresh_contrast(force=True)
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
                     f'见「映射文件」页底部）')
        self._tabs.setCurrentIndex(0)
        return True

    def reload_mapping(self) -> bool:
        """把当前这份从磁盘重读一遍（外部改完不用重启工具）。"""
        if self._state.path:
            state = state_from_file(self._state.path, kind=self._state.kind)
        else:
            state = state_from_project(self._project_root, force=True)
        if state.error:
            self.say(f'⚠ 重读失败：{state.error}', warn=True)
            return False
        if not state.path:
            self._set_state(state, f'项目里没有映射文件 —— 工具{INFER_TEXT}。')
            return True
        self._set_state(state, f'已重读：{state.path}')
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
            return AM.draft_mapping(self._project_root, images, known_chars,
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
            self._tabs.setCurrentIndex(0)
            return True
        finally:
            QApplication.restoreOverrideCursor()

    def prepare_save(self) -> bool:
        """
        「保存到项目根」第一步：算出写哪儿 + 先给人看 diff，**一个字节都不写**。

        两步走的原因：这是全量覆盖一份项目文件（铁律第 5 条）。不弹第二个
        模态框（本仓库踩过模态套模态挂死测试的坑），diff 就摆在对话框底部。
        """
        if not self._state.has_data():
            self.say('⚠ 当前没有可保存的映射（先载入或导出初稿）。', warn=True)
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
        after = json_text(self._state.data)
        self._pending = (target, self._state.data)
        note = ''
        active = str(getattr(self._state, 'path', '') or '')
        if active and AM.is_workbook(active):
            note = (f'　⚠ 当前生效的是 {Path(active).name}：这里写的是同目录的 '
                    f'{Path(target).name} 快照，**不会**动那份 Excel，'
                    f'也不会改变"以 Excel 为准"。')
        self._diff_label.setText(
            f'将要写入：{target} —— {diff_summary(before, after)}{note}')
        self._diff.setPlainText('\n'.join(diff_lines(before, after))
                               or '（内容没有变化，写入也不会改变文件）')
        self._diff_label.setVisible(True)
        self._diff.setVisible(True)
        self._confirm_bar.setVisible(True)
        self._tabs.setCurrentIndex(0)
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
