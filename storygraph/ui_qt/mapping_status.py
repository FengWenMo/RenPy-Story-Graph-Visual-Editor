"""
storygraph.ui_qt.mapping_status
===============================
「🗂 资源映射」菜单 + 状态栏那一格：**装一次、刷很多次**。

2026-11-01 从 `ui_qt.main_window` 搬出来（解导入环）。为什么非搬不可：

`BeatCardEditor` 只依赖 main_window 的**一个**模块级名字 —— 就是这里的
`refresh_mapping_status`（立绘换成真图之后要立刻把"未识别张数"重算一遍）。
卡片编辑器随后也搬出 main_window 成了 `ui_qt.card_editor`，名字留在原处就是

    main_window → card_editor → main_window

那个模块级环。这一块本身只认「窗口对象上有哪些属性」，零反向依赖，
所以它是那个环上最便宜的断点。

**为什么全是模块级函数、而不是主类方法**：`test_main_window_mixins.py`
用 `ast.walk` 数 `MainWindowQt` 的方法（预算 ≤35，嵌套 def 也算）。
这些函数只操作窗口状态，不需要成为主类 API（与 `toggle_card_column`
同一条规矩）。main_window 里 re-export 老名字，`MW.refresh_mapping_status`
之类的老调用点（含测试）不变。
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QLabel

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import theme


# ==================================================================
#  「🗂 资源映射」入口 + 状态栏那一行（④⑥ 第二阶段，2026-10-30）
# ==================================================================
#
# **为什么全是模块级函数、而不是主类方法**：`test_main_window_mixins.py`
# 用 `ast.walk` 数 `MainWindowQt` 的方法（预算 ≤35，嵌套 def 也算）——
# 本轮实测 34/35，一个都不能再往主类里塞。这些函数只操作窗口状态，
# 不需要成为主类 API（与 `toggle_card_column` / `CardActionRow` 同一条规矩）。

#: 菜单里五个动作的**文本与顺序**（用户点名的五件事；判据直接读这一份）。
MAPPING_MENU_TEXTS = ('载入映射…', '重读', '导出映射初稿…', '保存到项目根',
                      '工具怎么认的')

#: 窗口上那个"未识别张数算过没有"的缓存（换项目 / force 才重算）。
MAPPING_CACHE_ATTR = '_mapping_cache'


def _exec_mapping_dialog(dlg):
    """打开资源映射对话框（**唯一出口**：测试把它换成记录器，避免真的弹窗）。"""
    return dlg.exec()


def _exec_asset_picker(dlg):
    """打开资产选择器（同上：唯一的 exec 出口，便于判据不弹真窗）。"""
    return dlg.exec()


def _choose_mapping_file(win) -> str:
    """「载入映射…」的第一步：挑一份映射（JSON 或 Excel，用户取消返回 ''）。"""
    start = str(getattr(win, '_project_root', '') or Path.cwd())
    path, _filter = QFileDialog.getOpenFileName(
        win, '载入资源映射（JSON / Excel）', start,
        '映射文件 (*.json *.xlsx *.xlsm);;JSON (*.json);;'
        'Excel (*.xlsx *.xlsm);;所有文件 (*)')
    return str(path or '')


def _choose_mapping_export_path(win) -> str:
    """「导出映射初稿…」的第一步：挑写到哪儿（.json 可提交 / .xlsx 可手改）。"""
    root = getattr(win, '_project_root', '') or Path.cwd()
    try:
        from storygraph.assets.asset_mapping import MAPPING_FILENAME
    except Exception as e:                       # noqa: BLE001 - 拿不到就用手写名
        swallow('ui_qt.main_window.mapping_export_path', e)
        MAPPING_FILENAME = 'storygraph.mapping.json'
    path, _filter = QFileDialog.getSaveFileName(
        win, '导出资源映射初稿（.json 可提交 / .xlsx 可手工维护）',
        str(Path(str(root)) / MAPPING_FILENAME),
        '映射 JSON (*.json);;映射 Excel (*.xlsx);;所有文件 (*)')
    return str(path or '')


def _ignore_checked(fn, win, *_args):
    """
    菜单/按钮回调垫片：`QAction.triggered(bool)` 会把 `checked` 当**第一个
    位置参数**喂给槽函数（见 `_build_toolbars.act()` 的说明）。这里吞掉它，
    否则 `fn(win, False)` 会 TypeError —— 用户看到的是"点了没反应"。
    """
    return fn(win)


def open_mapping_dialog(win, *, mapping_path: str = '',
                        force_reload: bool = False, tab: str = '',
                        export_path: str = '', prepare_save: bool = False,
                        focus_image: str = ''):
    """
    打开「🗂 资源映射」对话框（五个菜单动作共用这一条路）。

    各动作只是"开在哪个状态"，判定 / 写盘 / diff 全在对话框里（一次只维护一份）：

      · `mapping_path`   —— 指定要读哪份映射（「载入映射…」先问路径）；
      · `force_reload`   —— 绕过 mtime 缓存重读（「重读」：外部编辑器刚改完）；
      · `export_path`    —— 先按当前项目生成初稿写到这儿，再开窗显示结果
                            （写盘是用户刚才在「另存为」里**明确点过**的）；
      · `prepare_save`   —— 先把"保存到项目根"的 diff 算好摆出来，
                            用户只需在窗里点「确认写入」（**确认门禁不跳过**）；
      · `tab`            —— `'table'` 时直接翻到「工具怎么认的」页；
      · `focus_image`    —— 卡片上那条警告带过来的 image spec：开窗就翻到
                            「逐条编辑 → 单张图片」并选中那一行（没有就按推断
                            先补一条），人只需补空着的格子。

    没有项目根时**说一句人话**再返回（不静默什么都不做）：映射要写进项目里。
    """
    from storygraph.ui_qt.mapping_dialog import MappingDialog
    root = str(getattr(win, '_project_root', '') or '')
    if not root and not mapping_path:
        msg = ('还没有打开项目 —— 先点「📁 打开项目」选项目根，'
               '资源映射要存在项目里（项目根或 game/ 下）。')
        try:
            win._log('[资源映射] ' + msg)
            win._status(msg)
        except Exception as e:                    # noqa: BLE001 - 提示失败也返回
            swallow('ui_qt.main_window.mapping_no_project', e)
        return None
    registry = None
    try:
        registry = win._registry_provider()
    except Exception as e:                        # noqa: BLE001 - 没有资源表也能读映射
        swallow('ui_qt.main_window.mapping_registry', e)
    dlg = MappingDialog(win, registry=registry, project_root=root,
                        mapping_path=mapping_path, focus_image=focus_image)
    # 各动作的"预置状态"——失败只记台账，不许拦住开窗（用户还得能看能改）
    for step, fn in (('reload', lambda: dlg.reload_mapping() if force_reload else None),
                     ('export', lambda: dlg.export_draft(export_path) if export_path else None),
                     ('prepare_save', lambda: dlg.prepare_save() if prepare_save else None)):
        try:
            fn()
        except Exception as e:                    # noqa: BLE001
            swallow(f'ui_qt.main_window.mapping_{step}', e)
    if tab == 'table':
        tabs = getattr(dlg, '_tabs', None)        # 稳定内部结构（见 mapping_dialog._build）
        if tabs is not None:
            try:
                for i in range(tabs.count()):
                    if '认' in tabs.tabText(i):   # 「工具怎么认的」
                        tabs.setCurrentIndex(i)
                        break
            except Exception as e:                # noqa: BLE001
                swallow('ui_qt.main_window.mapping_tab', e)
    try:
        _exec_mapping_dialog(dlg)
    finally:
        # 对话框里可能载入了 / 写了新映射 —— 状态栏那一行与"未识别张数"必须重算
        refresh_mapping_status(win, force=True)
    return dlg


def mapping_load_file(win):
    """菜单「载入映射…」：先挑文件，再开在这个映射上。"""
    path = _choose_mapping_file(win)
    if not path:
        return None
    return open_mapping_dialog(win, mapping_path=path)


def mapping_reload(win):
    """菜单「重读」：绕过缓存重读项目里的映射文件。"""
    return open_mapping_dialog(win, force_reload=True)


def mapping_export_draft(win):
    """菜单「导出映射初稿…」：先挑保存位置，再开窗看到结果。"""
    path = _choose_mapping_export_path(win)
    if not path:
        return None
    return open_mapping_dialog(win, export_path=path)


def mapping_save_to_project(win):
    """菜单「保存到项目根」：开窗时 diff 已经算好，用户点「确认写入」才落盘。"""
    return open_mapping_dialog(win, prepare_save=True)


def mapping_show_table(win):
    """菜单「资源对照表」：开窗并翻到对照表页（1720 行一张图 + 只看未识别）。"""
    return open_mapping_dialog(win, tab='table')


def refresh_mapping_status(win, *, force: bool = False):
    """
    （重新）算「映射」那条状态 + 未识别张数，写进状态栏那一格。

    **未识别张数只算一次**（用户点名：别每次刷新都算一遍）：1720 张图算一轮
    实测 ~0.16s，挂在"每次状态栏刷新"上就是肉眼可见的卡。缓存键是**项目根**：
    换项目 / `force=True`（对话框关掉、点「重读」）才重算。

    返回 `(MappingState | None, unknown)`；判据（`tests/test_mapping_menu.py`）
    直接读它，不必去截状态栏文字。
    """
    root = str(getattr(win, '_project_root', '') or '')
    cache = getattr(win, MAPPING_CACHE_ATTR, None)
    if cache is not None and not force and cache.get('root') == root:
        state, unknown = cache.get('state'), int(cache.get('unknown') or 0)
    else:
        state, unknown = None, 0
        if root:
            try:
                from storygraph.ui_qt.mapping_dialog import (contrast_rows,
                                                            state_from_project,
                                                            unknown_count)
                state = state_from_project(root, force=force)
                registry = None
                try:
                    registry = win._registry_provider()
                except Exception as e:            # noqa: BLE001 - 没资源表也算
                    swallow('ui_qt.main_window.mapping_status.registry', e)
                if registry is not None:
                    unknown = int(unknown_count(contrast_rows(registry, root)))
            except Exception as e:                # noqa: BLE001 - 状态算不出不该拦人
                swallow('ui_qt.main_window.mapping_status', e)
        setattr(win, MAPPING_CACHE_ATTR,
                {'root': root, 'state': state, 'unknown': unknown})
    label = getattr(win, '_mapping_label', None)
    if label is None:                             # 界面还没搭到这一步
        return state, unknown
    try:
        from storygraph.ui_qt.mapping_dialog import status_text
        text = status_text(state, unknown)
        label.setText(text)
        color = theme.t('text.secondary')
        if state is not None and getattr(state, 'error', ''):
            color = theme.t('state.warning')
        elif state is not None and state.has_data():
            color = theme.t('state.success')
        label.setStyleSheet(f'color: {color};')
        tips = [text]
        if root:
            tips.append(f'项目根：{root}')
        path = str(getattr(state, 'path', '') or '') if state is not None else ''
        if path:
            tips.append(f'这次用的是：{path}')
        if state is not None and getattr(state, 'error', ''):
            tips.append('读不出来：' + str(state.error))
        if unknown:
            tips.append(f'{unknown} 张图没认出是什么 —— 点「🗂 资源映射 → 工具怎么认的」'
                        f'看明细（未识别的排最上面）。')
        label.setToolTip('\n'.join(str(t) for t in tips))
    except Exception as e:                        # noqa: BLE001 - 只影响一个提示
        swallow('ui_qt.main_window.mapping_status.label', e)
    return state, unknown


def _install_mapping_ui(win):
    """
    装「🗂 资源映射」菜单 + 状态栏那一格（在 `_build_toolbars` 里调一次）。

    为什么是**菜单栏**而不是工具栏按钮：工具栏那条在 900px 窗口下已经
    贴着预算（见 `test_ui_width.py`），再加一个 100px+ 的文字按钮会把它顶破；
    菜单栏标题本来就窄，而且两页都看得见（映射与"在哪一页"无关）。
    """
    try:
        menu = win.menuBar().addMenu('🗂 资源映射')
    except Exception as e:                        # noqa: BLE001 - 没有菜单栏也不该拦开窗
        swallow('ui_qt.main_window.mapping_menu', e)
        return
    menu.setObjectName('mappingMenu')
    menu.setToolTip('旧版资产识别口径：映射文件 / 重读 / 导出初稿 / 保存 / 对照表')
    actions = []
    for text, fn in zip(MAPPING_MENU_TEXTS,
                        (mapping_load_file, mapping_reload,
                         mapping_export_draft, mapping_save_to_project,
                         mapping_show_table)):
        act = menu.addAction(text)
        act.setObjectName('mappingAction' + str(len(actions)))
        act.triggered.connect(partial(_ignore_checked, fn, win))
        actions.append(act)
    win._mapping_menu = menu
    win._mapping_actions = actions
    label = QLabel('')
    label.setObjectName('mappingStatus')
    label.setProperty('role', 'caption')
    win._mapping_label = label
    try:
        win._status_bar.addPermanentWidget(label)
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.mapping_status.install', e)
    refresh_mapping_status(win, force=True)
