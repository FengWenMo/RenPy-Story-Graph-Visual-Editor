"""
storygraph.ui_qt.card_actions
=============================
卡片上那些**按钮点下去要干的事**：改尺寸 / 换景 / 写回 at / 并入同样的一批 /
立绘挑图 / 去资源映射里修。

全是 `fn(win, req)` 形态的模块级函数，不是主窗口的方法 —— 理由和
`mapping_status` 那边一样：`tests/test_main_window_mixins.py` 数主类里的
方法（预算 ≤35），而这些东西只需要"窗口对象上有哪些属性"，没有一个该成为
主类 API。

2026-11-01 从 `ui_qt.main_window` 搬出来，为的是**搬页面构建器**：
`_build_card_page` / `_build_toolbars` 要进 `GraphPageMixin`，而它们用到的
`apply_size_from_card` / `BeatCardEditor` 那时还住在 main_window ——
`window_graph_page → main_window` 就是反向边。这一块落到本模块后方向理顺：

    main_window → window_graph_page → {card_actions, card_editor, mapping_status}

⚠ 判据要打桩的对象变了：从前 `monkeypatch.setattr(MW, '_ask_scene_image', …)`
改的是 main_window 的模块级名字；这些函数现在读的是**本模块**的名字，
所以打桩要打在 `storygraph.ui_qt.card_actions` 上（`import … as CA`）。
main_window 里仍 re-export 这些函数，读得到、调得动。
"""

from __future__ import annotations

from functools import partial

from PySide6.QtWidgets import QDialog, QMessageBox

from storygraph.core.safeguard import swallow
from storygraph.ui_qt.mapping_status import (_exec_asset_picker,
                                             open_mapping_dialog)


# ==================================================================
#  立绘网格 ↔ 资产选择器 / 资源映射（④⑥ 第二阶段接线）
# ==================================================================

def pick_sprite_result(win, current: str = ''):
    """
    立绘网格的「＋ 添加立绘 / 双击换图」→ 资产选择器（**用途预选「立绘」**）。

    为什么要专门的入口（而不是复用 `window_assets._pick_image`）：那条路只还
    一个 spec 字符串，卡片就无从知道"用户选的预览图被换成了哪张真立绘"，
    提示条也就摆不出来（用户 2026-10-30：要看得见判定）。这里多返回
    `PickResult`（含 `replaced_from/replaced_to/message`），取消返回 None。
    """
    from storygraph.ui_qt.asset_picker import AssetPickerDialog
    if not win._ensure_registry():
        return None
    dlg = AssetPickerDialog(
        win, registry=win._registry_provider(),
        project_root=getattr(win, '_project_root', None), current=current,
        types=['image'], want_image_spec=True,
        title='选择立绘（预览图 / 头像会自动换成真立绘）',
        purpose='sprite')
    try:
        if _exec_asset_picker(dlg) != QDialog.Accepted:
            return None
        res = getattr(dlg, 'pick_result', None)
    finally:
        try:
            dlg.close()
        except Exception as e:                    # noqa: BLE001 - 关不掉也返回
            swallow('ui_qt.main_window.pick_sprite.close', e)
    return res if res is not None else str(dlg.image_spec or '')


def open_mapping_from_card(win, spec=''):
    """
    卡片提示条上「去『资源映射』修」→ 打开资源映射，并**落到那张图那一行**。

    `spec` 是那条警告里的 image spec（`ling happy`）：开窗直接翻到
    「逐条编辑 → 单张图片」并选中它（表里没有就先按推断补一条）。
    从前这里只 `open_mapping_dialog(win)` —— 人还得自己在 3889 行里找。
    """
    return open_mapping_dialog(win, focus_image=str(spec or ''))


def notify_card_notice(win, message: str):
    """
    立绘网格的"没写成"通知（选择器拒绝 / 给不出可写 spec）→ 控制台 + 状态栏。

    网格里没有状态栏可用，但**绝不能**因此把拒绝变成静默：用户会以为加上了。
    """
    text = str(message or '').strip()
    if not text:
        return
    try:
        win._log('[立绘] ' + text)
        win._status(text)
    except Exception as e:                        # noqa: BLE001 - 提示失败也不拦
        swallow('ui_qt.main_window.card_notice', e)


# ==================================================================
#  「🎬 演出预览」的写回 at 接线（P2，2026-10-30）
# ==================================================================
#
# ⚠ 真正的调用点在 `window_export.open_stage_preview`（S1 的文件，本轮不加线）。
# 所以这里给**工具栏那条**换成带 beats / on_apply_at 的版本；各处测试仍可调
# `win.open_stage_preview()` 走老路（它们只关心"从当前那一拍开始"）。

def _confirm_at_plan(parent, plan, extra: str = '',
                     title: str = '写回 at？') -> bool:
    """
    「写回 at」的确认框（**唯一出口**：判据把它换掉，不弹真窗）。

    `extra` 是**同一张框里**的第二件事（2026-10-31 起：生成 `sg_*` 时要顺带
    新建/追加 `game/sg_transforms.rpy`）。刻意不弹第二个框 —— 一次点击却弹
    两次确认，人会条件反射点"是"，那等于没有确认。

    `title` 只影响窗口标题：换景走的是同一个出口，但它是"会清台"的那件事，
    标题得说清（`换景（会清台）？`），不能还写着"写回 at"。
    """
    body = plan.diff_text()
    extra = str(extra or '').strip()
    if extra:
        body = f'{body}\n\n—— 另外要动项目文件 ——\n{extra}'
    return QMessageBox.question(
        parent, title, body,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


def _plan_preview_at(win, req):
    """演出预览发来的写回请求 → `AtEdit` 计划（只算，不动 Beat）。"""
    from storygraph.app.stage import MODE_CHAPTER_FIRST, is_unknown_source
    from storygraph.app.stage import plan_at_edit
    from storygraph.story.atl import DEFAULT_SCREEN, project_screen
    root = getattr(win, '_project_root', None)
    try:
        screen = project_screen(root) if root else DEFAULT_SCREEN
    except Exception as e:                        # noqa: BLE001 - 读不到就用默认分辨率
        swallow('ui_qt.main_window.preview_at.screen', e)
        screen = DEFAULT_SCREEN
    try:
        idx = int((req or {}).get('beat_index', -1))
    except (TypeError, ValueError):
        idx = -1
    mode = str((req or {}).get('mode') or 'source')
    src = (req or {}).get('source_index')
    # 跨章继承（出处不在这本剧本里，`source_index < 0`）且这一拍**看得见**背景/
    # 图层：没有"那一行"可改 → 与尺寸面板同一条路（本章第一拍补一条 show）。
    # 同一个请求两种出口两种待遇，正是本轮在治的病。
    if mode == 'source' and is_unknown_source(src) \
            and str((req or {}).get('scene_image') or '').strip():
        mode = MODE_CHAPTER_FIRST
    return plan_at_edit(list(getattr(win._session, 'beats', None) or ()), idx,
                        target=str((req or {}).get('target') or 'scene'),
                        tag=str((req or {}).get('tag') or ''),
                        transform=str((req or {}).get('transform') or ''),
                        project_root=root, screen=screen,
                        source_index=src, mode=mode,
                        scene_image=str((req or {}).get('scene_image') or ''))


def _after_preview_beat_edit(win, plan, *, kind: str = '已写回 at',
                             tag: str = '演出预览') -> None:
    """
    改完一拍之后的**既有门禁**：会话脏标记 + 状态栏「● 未导出」+ 一行台账。

    `kind` / `tag` 只影响那行台账与状态栏措辞：换景（`scene`）走的不再是
    "写回 at"，日志里不能还这么写（真实项目第一次跑就露出来了：
    `[演出预览] 已写回 at：第 134 拍 · 换景（scene，会清台） ·  ·`）——
    空 transform 还会多出一对孤零零的 `·`。

    ⚠ 刻意**不重载卡片**：卡片里可能有还没保存的手改，重载会把它们冲掉。
    `session.apply` 对"用户没手改 Stage"的拍会保留结构化列，所以不重载不会
    把这次写回弄丢（Stage 被手改过的拍以卡片为准 —— 那是用户自己的选择）。
    """
    try:
        win._session.mark_edits_dirty()
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.preview_at.dirty', e)
    try:
        win._sync_export_dirty()
    except Exception as e:                        # noqa: BLE001 - 状态标签失败不拦写回
        swallow('ui_qt.main_window.preview_at.dirty_ui', e)
    try:
        span = ''
        try:
            if getattr(plan, 'affects', ()):
                first, last = int(plan.affects[0]), int(plan.affects[1])
                span = (f' · 影响第 {first + 1}–{last + 1} 拍'
                        f'（共 {last - first + 1} 拍）')
        except (TypeError, ValueError):
            span = ''
        what = str(getattr(plan, 'transform', '') or '').strip()
        win._log(f'[{tag}] {kind}：第 {int(plan.beat_index) + 1} 拍 · '
                 f'{plan.target_label}' + (f' · {what}' if what else '')
                 + f'{span}（只改内存里的拍；导出时才写 .rpy，可撤销）')
        win._status(f'{kind}（只改内存里的拍；导出仍走既有 diff 确认门禁）')
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.preview_at.log', e)


def apply_at_from_preview(win, req):
    """
    预览窗口「写回 at…」的外部钩子（`on_apply_at(request) -> AtEdit | None`）。

    约定（见 `stage_preview._on_apply_at_clicked`）：返回的 `AtEdit` 已经
    **落进内存 Beat**，窗口负责把它记进自己的撤销栈并重画帧；返回 None =
    没写（用户取消 / 计划被拒）。

    流程与 S3 窗口内的默认路径一致：算计划 → 给 diff 确认 → 写内存 →
    会话脏标记（导出时的既有确认门禁因此照常生效）。
    """
    plan = _plan_preview_at(win, req)
    if plan is None or not getattr(plan, 'ok', False):
        reason = str(getattr(plan, 'reason', '') or '').strip() or '没有可写的改动。'
        notify_card_notice(win, '写回 at 没做：' + reason)
        return None
    if not _confirm_at_plan(win, plan):
        try:
            win._status('已取消：没有写回 at。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.preview_at.cancel', e)
        return None
    try:
        if not plan.apply():
            notify_card_notice(win, '写回 at 失败：计划没落进内存里的拍。')
            return None
    except Exception as e:                        # noqa: BLE001 - 写失败要说出来
        swallow('ui_qt.main_window.preview_at.apply', e)
        notify_card_notice(win, f'写回 at 失败：{e}')
        return None
    _after_preview_beat_edit(win, plan)
    return plan


def _screen_pair(win):
    """项目的游戏分辨率（`gui.init`）；读不到用 1920×1080。"""
    from storygraph.story.atl import DEFAULT_SCREEN, project_screen

    root = getattr(win, '_project_root', None)
    try:
        return project_screen(root) if root else DEFAULT_SCREEN
    except Exception as e:                        # noqa: BLE001 - 读不到就用默认分辨率
        swallow('ui_qt.main_window.size.screen', e)
        return DEFAULT_SCREEN


def _plan_size_from_card(win, req, index=None):
    """
    尺寸面板（卡片上的「尺寸」一格）的一次「改」→ `AtEdit` 计划（只算）。

    请求 dict 来自 `ui_qt.size_panel.SizePanel.request`：
    `{row, key, at, kind, tag, spec, source, generate, natural}` —— 里面的 `at`
    是 `story.size_presets` **按几何**在项目里匹配到的 transform（或 `''` =
    原样）。**`generate=True` 时 `at` 是个还不存在的 `sg_*` 名字**：窗口层先把
    它加进 `index`（`transform_def` 从生成的文本再解析一次），计划就能像
    "文件已经写好"那样算几何；真写盘在确认之后（见 `apply_size_from_card`）。

    这里只把它翻成"改哪一拍、写哪个字段"，不重新匹配（面板给的就是它看见的）。
    """
    from storygraph.app.stage import plan_size_edit

    root = getattr(win, '_project_root', None)
    screen = _screen_pair(win)
    try:
        idx = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        idx = -1
    req = req or {}
    row = {'kind': str(req.get('kind') or 'scene'),
           'tag': str(req.get('tag') or ''),
           'spec': str(req.get('spec') or ''),
           'source': req.get('source', -1)}
    return plan_size_edit(list(getattr(win._session, 'beats', None) or ()),
                          idx, row, key=str(req.get('key') or ''),
                          at=str(req.get('at') or ''),
                          project_root=root, transform_index=index,
                          screen=screen)


def _refresh_card_after_size(win, plan) -> None:
    """
    改完尺寸把卡片对齐（**只有这一条路会重载表单**）。

    为什么允许重载（`_after_preview_beat_edit` 刻意不重载）：这次改动是从
    **卡片自己**发起的，动笔之前已经把表单里的手改提交进这一拍了
    （`apply_current_beat`）。所以重载丢掉的东西 = 无。

    改的是**别的拍**（背景是第 41 拍写的、你在第 100 拍改）时只刷只读派生面板
    （当前舞台 / 尺寸行 / 小图），不重载表单 —— 表单本来就不该变。
    """
    try:
        loaded = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        loaded = -1
    try:
        if int(getattr(plan, 'beat_index', -2)) == loaded:
            win._show_index(loaded)
        else:
            win.refresh_derived_panels(loaded)
    except Exception as e:                        # noqa: BLE001 - 刷不动不拦写回
        swallow('ui_qt.main_window.size.refresh_card', e)


def _plan_sg_from_card(win, req, index):
    """
    `generate=True` 的那一条 → 写 `game/sg_transforms.rpy` 的计划 + 新 transform 表。

    返回 `(plan, index)`：

      · `plan.writes` → 确认之后要**先写文件**，`index` 里已经带上这条新定义的
        几何（`AtEdit` 计划因此能算得像"文件已写好"）；
      · `plan` 是 `reuse` / `noop` → 项目里已经有等价的东西（护栏 ②），
        **不写文件**，只是把名字换成它；
      · `plan` 是 `refuse` / `None` → 调用方说清原因、什么都不动。

    三条护栏都在 `story.sg_transforms` 里，这里只负责"读一次表、合一次表"。
    """
    from storygraph.story import sg_transforms as SG

    key = str((req or {}).get('key') or '')
    # `zoom` 只有「自定义倍数」有（`key='custom'`）：名字与体都从这个数来
    # （`sg_zoom_075` / `zoom 0.75`），所以一路带着它 —— 只在 `plan_for_project`
    # 里丢掉的话，写进文件的会是 `zoom 1`（2026-10-31）。
    zoom = (req or {}).get('zoom')
    root = getattr(win, '_project_root', None)
    plan = SG.plan_for_project(root, key, zoom, index=index,
                               natural=(req or {}).get('natural'),
                               screen=_screen_pair(win))
    if not plan.ok or not plan.writes:
        return plan, index
    defn = SG.transform_def(plan.name, key, plan.zoom)
    if defn is None:
        return SG.SgPlan(action=SG.REFUSE, key=key, zoom=zoom,
                         reason=f'生成的 {plan.name} 解析不出几何，拒绝写'), index
    merged = dict(index or {})
    merged[plan.name] = defn
    return plan, merged


def apply_size_from_card(win, req):
    """
    「尺寸」一格的「改」：算计划 → diff 确认 → 写内存 Beat → 刷卡片。

    与 `apply_at_from_preview` **同一条门禁**（同一个 `_confirm_at_plan`、
    同一个 `_after_preview_beat_edit`）：只改内存里的拍，导出时才落盘；
    会话脏标记照常点亮，所以既有 diff 确认不会绕过去。

    **唯一的例外**（2026-10-31，用户拍板的前提）：`generate=True` 时要先往
    `game/sg_transforms.rpy` 里写一条 `sg_*` 定义 —— 那是**项目文件**，不是
    内存。所以：

      ① 先把两件事一起摊在**同一张**确认框里（脚本 diff + 文件 diff）；
      ② 确认后**先写文件**（护栏过了才写），再 `.apply()` 内存里的拍；
      ③ 写完刷 session 的 transform 缓存 —— 否则卡片还显示"项目里没有"。

    返回落进内存的 `AtEdit`；取消 / 被拒 / 失败返回 None。
    """
    if not isinstance(req, dict):
        return None
    # 先把卡片上还没保存的手改落进这一拍 —— 否则它会在写回后被表单盖掉
    try:
        win.apply_current_beat()
    except Exception as e:                        # noqa: BLE001 - 提交失败也照样能改
        swallow('ui_qt.main_window.size.apply_current', e)
    index = None
    sg_plan = None
    if req.get('generate'):
        try:
            index = dict(win._session.transform_index() or {})
        except Exception as e:                    # noqa: BLE001 - 取不到表就别生成
            swallow('ui_qt.main_window.size.index', e)
            index = {}
        try:
            sg_plan, index = _plan_sg_from_card(win, req, index)
        except Exception as e:                    # noqa: BLE001 - 计划失败要说出来
            swallow('ui_qt.main_window.size.sg_plan', e)
            notify_card_notice(win, f'生成 transform 没做：{e}')
            return None
        if sg_plan is None or not sg_plan.ok:
            reason = str(getattr(sg_plan, 'reason', '') or '').strip() \
                or '生成不了这个 transform。'
            notify_card_notice(win, '生成 transform 没做：' + reason)
            return None
        # 查重命中（项目里已经有等价的东西）时，名字可能不是面板上那个
        req = dict(req, at=str(sg_plan.name or ''), generate=False)
    try:
        plan = _plan_size_from_card(win, req, index=index)
    except Exception as e:                        # noqa: BLE001 - 计划失败要说出来
        swallow('ui_qt.main_window.size.plan', e)
        notify_card_notice(win, f'改尺寸没做：{e}')
        return None
    if plan is None or not getattr(plan, 'ok', False):
        reason = str(getattr(plan, 'reason', '') or '').strip() or '没有可写的改动。'
        notify_card_notice(win, '改尺寸没做：' + reason)
        return None
    extra = sg_plan.diff_text() if (sg_plan is not None
                                   and getattr(sg_plan, 'writes', False)) else ''
    if not _confirm_at_plan(win, plan, extra=extra):
        try:
            win._status('已取消：没有改尺寸。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.size.cancel', e)
        return None
    if sg_plan is not None and getattr(sg_plan, 'writes', False):
        try:
            sg_plan.apply()
        except Exception as e:                    # noqa: BLE001 - 写文件失败要说出来
            swallow('ui_qt.main_window.size.sg_apply', e)
            notify_card_notice(win, f'写 {sg_plan.rel} 失败：{e}')
            return None
        try:
            win._session.refresh_transform_index()
        except Exception as e:                    # noqa: BLE001 - 刷不到只影响显示
            swallow('ui_qt.main_window.size.sg_refresh', e)
        notify_card_notice(win, f'已生成 {sg_plan.name} → {sg_plan.rel}'
                                '（工具生成、带指纹；手改过就不再覆盖）')
    try:
        if not plan.apply():
            notify_card_notice(win, '改尺寸失败：计划没落进内存里的拍。')
            return None
    except Exception as e:                        # noqa: BLE001 - 写失败要说出来
        swallow('ui_qt.main_window.size.apply', e)
        notify_card_notice(win, f'改尺寸失败：{e}')
        return None
    _after_preview_beat_edit(win, plan)
    _refresh_card_after_size(win, plan)
    return plan


def _ask_scene_image(win, req) -> str:
    """
    「换景」的**唯一入口**：让用户挑新背景（判据把它换掉，不开真窗）。

    走的是卡片「背景」那一格**同一个**资产选择器（`_pick_asset_for`）：
    背景的可选范围必须与那一格完全一致，否则"从这里能选、从那里选不到"
    就成了两个真相。取消 / 打不开 → 空串（调用方什么都不做）。
    """
    current = str((req or {}).get('spec') or '')
    try:
        return str(win._pick_asset_for('Scene', current) or '')
    except Exception as e:                        # noqa: BLE001 - 打不开要说出来
        swallow('ui_qt.main_window.scene.pick', e)
        notify_card_notice(win, f'打不开背景选择器：{e}')
        return ''


def _plan_scene_from_card(win, req, index=None):
    """「换景」请求 → `SceneEdit` 计划（只算，不动 Beat）。"""
    from storygraph.app.stage import plan_scene_change

    root = getattr(win, '_project_root', None)
    screen = _screen_pair(win)
    try:
        idx = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        idx = -1
    return plan_scene_change(
        list(getattr(win._session, 'beats', None) or ()), idx,
        str((req or {}).get('image') or ''),
        at=str((req or {}).get('at') or ''),
        project_root=root, transform_index=index, screen=screen)


def apply_scene_from_card(win, req):
    """
    背景行的「换景」：挑图 → 计划 → **一张**确认框（写清会清掉几个立绘）
    → 写内存 Beat → 刷卡片。

    为什么这条路要单独存在（用户 2026-10-31 拍板）：Ren'Py 的 `scene`
    移除 master 层上**所有** displayable 再显示新背景 —— 立绘会一起没。
    改背景"大小"用不着它（那写 `show`，见 `apply_size_from_card`）；
    但"这一拍换成另一张背景"只有它，所以入口必须**显式**，代价必须
    **写在确认框里**（`SceneEdit.diff_text` 那句"会清掉台上 N 个立绘"）。

    与尺寸那条路共用同一套门禁：只改内存里的拍（导出时才落盘）、同一个
    脏标记、同一张确认框出口；`generate=True`（新背景要套一个项目里还没有
    的 `sg_*`）时也先写 `game/sg_transforms.rpy`（三条护栏都在
    `story.sg_transforms` 里）。

    返回落进内存的 `SceneEdit`；取消 / 被拒 / 失败返回 None。
    """
    if not isinstance(req, dict):
        return None
    try:
        loaded = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        loaded = -1
    if loaded < 0:
        # 没有"当前拍"就没有换景的落点：别先弹一个背景选择器再告诉人没载入
        try:
            win._status('先载入一章，再换景。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.scene.no_chapter', e)
        return None
    # 卡片上还没保存的手改先落进这一拍 —— 换景会重建这一拍的脚本
    try:
        win.apply_current_beat()
    except Exception as e:                        # noqa: BLE001 - 提交失败也照样能换
        swallow('ui_qt.main_window.scene.apply_current', e)
    image = _ask_scene_image(win, req)
    if not image:
        try:
            win._status('已取消：没有换景。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.scene.cancel', e)
        return None
    index = None
    sg_plan = None
    if req.get('generate'):
        try:
            index = dict(win._session.transform_index() or {})
        except Exception as e:                    # noqa: BLE001 - 取不到表就别生成
            swallow('ui_qt.main_window.scene.index', e)
            index = {}
        try:
            # ⚠ 换景要**换图**：请求里那个 `natural` 是**旧背景**的尺寸（面板是
            # 按"现在这张图"算的）。拿它去查重会把"只对旧图等价"的 transform
            # 当成等价（例如 `xysize (1920,1080)` 恰好等于 4K 图的「铺满」），
            # 可新图还没量过。所以这里**不传尺寸**：只按逐字相同的 ATL 体
            # （尺度无关）与倍数类预设的"不用看图尺寸"那条查重（2026-10-31）。
            sg_plan, index = _plan_sg_from_card(
                win, dict(req, natural=None), index)
        except Exception as e:                    # noqa: BLE001 - 计划失败要说出来
            swallow('ui_qt.main_window.scene.sg_plan', e)
            notify_card_notice(win, f'生成 transform 没做：{e}')
            return None
        if sg_plan is None or not sg_plan.ok:
            reason = str(getattr(sg_plan, 'reason', '') or '').strip() \
                or '生成不了这个 transform。'
            notify_card_notice(win, '换景没做：' + reason)
            return None
        # 查重命中（项目里已有等价的东西）时名字可能不是面板上那个
        req = dict(req, at=str(sg_plan.name or ''), generate=False)
    try:
        plan = _plan_scene_from_card(win, dict(req, image=image), index=index)
    except Exception as e:                        # noqa: BLE001 - 计划失败要说出来
        swallow('ui_qt.main_window.scene.plan', e)
        notify_card_notice(win, f'换景没做：{e}')
        return None
    if plan is None or not getattr(plan, 'ok', False):
        reason = str(getattr(plan, 'reason', '') or '').strip() or '没有可写的改动。'
        notify_card_notice(win, '换景没做：' + reason)
        return None
    extra = sg_plan.diff_text() if (sg_plan is not None
                                   and getattr(sg_plan, 'writes', False)) else ''
    if not _confirm_at_plan(win, plan, extra=extra, title='换景（会清台）？'):
        try:
            win._status('已取消：没有换景。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.scene.cancel_confirm', e)
        return None
    if sg_plan is not None and getattr(sg_plan, 'writes', False):
        try:
            sg_plan.apply()
        except Exception as e:                    # noqa: BLE001 - 写文件失败要说出来
            swallow('ui_qt.main_window.scene.sg_apply', e)
            notify_card_notice(win, f'写 {sg_plan.rel} 失败：{e}')
            return None
        try:
            win._session.refresh_transform_index()
        except Exception as e:                    # noqa: BLE001 - 刷不到只影响显示
            swallow('ui_qt.main_window.scene.sg_refresh', e)
        notify_card_notice(win, f'已生成 {sg_plan.name} → {sg_plan.rel}'
                                '（工具生成、带指纹；手改过就不再覆盖）')
    try:
        if not plan.apply():
            notify_card_notice(win, '换景失败：计划没落进内存里的拍。')
            return None
    except Exception as e:                        # noqa: BLE001 - 写失败要说出来
        swallow('ui_qt.main_window.scene.apply', e)
        notify_card_notice(win, f'换景失败：{e}')
        return None
    _after_preview_beat_edit(win, plan, kind='已换景', tag='换景')
    _refresh_card_after_size(win, plan)
    try:
        # 台账那行由 `_after_preview_beat_edit` 统一写（措辞已经按"换景"走）；
        # 这里只补它不知道的那件事：**清掉了谁**（确认框里报过，事后也要能查）。
        if plan.cleared_sprites or plan.cleared_layers:
            win._log('换景清掉：'
                     + (f'立绘 {len(plan.cleared_sprites)} 个'
                        f'（{"、".join(plan.cleared_sprites[:4])}）'
                        if plan.cleared_sprites else '')
                     + ('；' if plan.cleared_sprites and plan.cleared_layers
                        else '')
                     + (f'图层 {len(plan.cleared_layers)} 个'
                        f'（{"、".join(plan.cleared_layers[:3])}）'
                        if plan.cleared_layers else ''))
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.scene.log', e)
    return plan


# ==================================================================
#  「一并改成一样」—— 合并回源（2026-10-31）
# ==================================================================
#
# 尺寸面板那句"本景还有 2 处单独改写"后面那个按钮。它动的是**用户的 show
# 行**（`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5 的 L4：confirm、永远、
# 逐处），所以：
#
#   ① 计划是 `story.at_merge.plan_at_merge`（只算，不动 Beat）；
#   ② 确认框**一处一个勾**（`ui_qt.merge_dialog`），勾掉的一个字都不动；
#   ③ 写进内存的拍，导出时仍有既有 diff 门禁 —— 那一步还能反悔。

def _confirm_merge_plan(parent, plan):
    """
    合并回源的确认框（**唯一出口**：判据把它换掉，不弹真窗）。

    返回勾选的拍号集合；**取消**返回 `None`（与"一个都没勾"区分：
    后者要说"一处都没勾"，前者只写一行"已取消"）。
    """
    from storygraph.ui_qt.merge_dialog import ask_merge

    return ask_merge(plan, parent)


def apply_merge_from_panel(win, req):
    """
    尺寸面板「一并改成一样」：算计划 → **逐处**确认 → 写内存 Beat → 刷卡片。

    为什么是"把源的值套给那几处"而不是"删掉那几行"：Ren'Py 的 `show` 会
    **重建** displayable（`at` 不继承），所以删掉 `at` 等于回到原样 —— 见
    `story.at_merge` 的模块注释。这里只负责窗口层：门禁、脏标记、台账、刷新。

    返回落进内存的 `MergePlan`；取消 / 被拒 / 一处都没勾 / 失败返回 None。
    """
    if not isinstance(req, dict):
        return None
    from storygraph.story.at_merge import plan_at_merge

    # 卡片上还没保存的手改先落进这一拍（与「改」「换景」同一条前提）
    try:
        win.apply_current_beat()
    except Exception as e:                        # noqa: BLE001 - 提交失败也照样能合
        swallow('ui_qt.main_window.merge.apply_current', e)
    try:
        plan = plan_at_merge(
            list(getattr(win._session, 'beats', None) or ()),
            int(req.get('source', -1)),
            kind=str(req.get('kind') or 'scene'),
            tag=str(req.get('tag') or ''),
            label=str(req.get('label') or ''))
    except Exception as e:                        # noqa: BLE001 - 计划失败要说出来
        swallow('ui_qt.main_window.merge.plan', e)
        notify_card_notice(win, f'合并回源没做：{e}')
        return None
    if plan is None or not getattr(plan, 'ok', False):
        reason = str(getattr(plan, 'reason', '') or '').strip() \
            or '没有要合并的。'
        notify_card_notice(win, '合并回源没做：' + reason)
        return None
    chosen = _confirm_merge_plan(win, plan)
    if chosen is None:
        try:
            win._status('已取消：没有合并回源。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.merge.cancel', e)
        return None
    if not chosen:
        notify_card_notice(win, '一处都没勾：没有改任何行。')
        return None
    try:
        if not plan.apply(selected=set(int(i) for i in chosen)):
            notify_card_notice(win, '合并回源失败：计划没落进内存里的拍。')
            return None
    except Exception as e:                        # noqa: BLE001 - 写失败要说出来
        swallow('ui_qt.main_window.merge.apply', e)
        notify_card_notice(win, f'合并回源失败：{e}')
        return None
    try:
        win._session.mark_edits_dirty()
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.merge.dirty', e)
    try:
        win._sync_export_dirty()
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.merge.dirty_ui', e)
    _refresh_card_after_merge(win, plan)
    try:
        names = '、'.join(str(i + 1) for i in plan.beats[:6])
        more = (f' 等 {plan.count} 处' if plan.count > 6 else '')
        skipped = (f'（另有 {plan.skipped_count} 处改不了，见台账）'
                   if plan.skipped_count else '')
        win._log(f'[尺寸] 合并回源：第 {names} 拍的{plan.label} at 改成'
                 f' {plan.value_text}（源第 {plan.source + 1} 拍；'
                 '只改内存里的拍；不删行、不加行）')
        win._status(f'已把 {plan.count} 处改成和源一样{more}{skipped}'
                    '（只改内存里的拍；导出仍走既有 diff 确认门禁）')
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.merge.log', e)
    return plan


def _refresh_card_after_merge(win, plan) -> None:
    """
    合并之后刷卡片：**动到的拍里有当前载入的那一拍**才重载表单。

    与 `_refresh_card_after_size` 同一条规矩（重载会冲掉手改，所以只在
    "反正已经提交过"时重载）：这里事先调过 `apply_current_beat`，而且只有
    当载入的那一拍**自己被改了**才需要重载（`show` 那一列的文字变了）。
    """
    try:
        loaded = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        loaded = -1
    try:
        if loaded >= 0 and loaded in set(int(i) for i in plan.beats):
            win._show_index(loaded)
        else:
            win.refresh_derived_panels(loaded)
    except Exception as e:                        # noqa: BLE001 - 刷不动不拦写回
        swallow('ui_qt.main_window.merge.refresh_card', e)


def open_stage_preview_with_at(win):
    """
    工具栏「🎬 演出预览」：打开预览窗口，并把**当前 beats + 写回 at 入口**交给它。

    与 `window_export.open_stage_preview` 的差别只有"多传两个参数"：
    `beats` 让 S3 的「尺寸变换 → 写回 at」能用（只改内存 Beat），
    `on_apply_at` 让写回走主窗口的脏标记 / 导出确认门禁（见
    `apply_at_from_preview`）。
    """
    session = getattr(win, '_session', None)
    if not len(session):
        try:
            win._status('先载入一章。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.preview.no_chapter', e)
        return None
    try:
        win.apply_current_beat()                  # 先把卡片上的改动提交（老行为）
    except Exception as e:                        # noqa: BLE001 - 提交失败也照样能预览
        swallow('ui_qt.main_window.preview.apply_current', e)
    try:
        from storygraph.ui_qt.stage_preview import open_preview
        beats = list(getattr(session, 'beats', None) or [])
        return open_preview(win, session.frames(),
                            registry=win._registry_provider(),
                            project_root=getattr(win, '_project_root', None),
                            start_index=getattr(win, '_loaded_idx', 0),
                            beats=beats or None,
                            on_apply_at=partial(apply_at_from_preview, win),
                            # 跨章继承（2026-10-31）：第 1 拍的背景可能来自上一章。
                            initial_state=session.stage_initial_state())
    except Exception as e:                        # noqa: BLE001 - 与老入口同一处理
        QMessageBox.critical(win, '无法打开预览', str(e))
        return None
