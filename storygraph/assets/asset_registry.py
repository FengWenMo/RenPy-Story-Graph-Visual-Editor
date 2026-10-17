"""
asset_registry.py
==================
资产扫描、Excel 读写、解析与校验。

对外提供的接口（被 asset_engine.py 使用）：
  · AssetRegistry           资产注册表数据结构
  · scan_assets             从目录扫描资产（图片/音频）
  · write_asset_excel       将扫描结果写入 Excel
  · parse_asset_tables      从 Excel 目录构建 AssetRegistry
  · load_hooks              加载自定义钩子模块
  · analyze_references      扫描 .rpy 脚本填充 registry.references
  · validate_registry       校验文件存在性、依赖引用、颜色格式
"""

import re
from pathlib import Path
from collections import defaultdict
from typing import Optional
import openpyxl
import importlib.util

from storygraph.core.vocabulary import (
    ASSET_TYPE_ATTR as _TYPE_ATTR,
    NON_ACTOR_SHOW_TARGETS,
    RENPY_BUILTIN_TRANSFORMS,
    classify_show_words,
)
from storygraph.excel.cells import append_text_row, set_text

from storygraph.core.safeguard import swallow
from storygraph.assets.asset_registry_constants import (  # 2026-10-11 拆分
    SUPPORTED_IMAGE_EXT,
    SUPPORTED_AUDIO_EXT,
    CATEGORY_KEYWORDS,
    ASSET_FIELD_LABELS,
    _asset_set_text,
    _asset_append_row,
    _UI_WORDS,
    _UI_SUFFIXES,
    _CHARACTER_CONTAINER_WORDS,
    _GENERIC_IMAGE_ROOTS,
    _BG_WORDS,
    _CG_WORDS,
    _BG_NAME_RE,
    _CG_NAME_RE,
    _UI_NAME_RE,
    _BG_NAME_HINTS,
    _CG_NAME_HINTS,
    _UI_NAME_HINTS,
    _NON_CHARACTER_FIRST_WORDS,
    _SKIP_SCAN_DIRS,
    _GENERIC_IMAGE_DIRS,
    _MUSIC_DIR_NAMES,
    _SOUND_DIR_NAMES,
    _SOUND_NAME_HINTS,
    _MUSIC_NAME_HINTS,
    _ASSET_KINDS,
    _STANDARD_TABLES,
    _TABLE_FIELDS,
    _PLACEHOLDER_MARKS,
    _COLOR_PATTERN,
    _RE_SHOW_LINE,
    _RE_AUDIO_LINE,
    _RE_AT_TRANSFORM,
    _RE_WITH_TRANSITION,
    _RE_VOICE_LINE,
    _AUDIO_ID_PREFIXES,
)
from storygraph.assets.asset_registry_scan import (  # 2026-10-11 拆分
    FileWalkIndex,
    _classify_audio,
    _detect_audio_dirs,
    _detect_category,
    _detect_image_tag,
    _detect_voice_dirs,
    _is_scan_noise,
    _is_under_any,
    _make_unique_id,
    _music_row,
    _normalize_tag,
    _path_under_skip_dir,
    _resolve_audio_source,
    _scan_audio_from_dirs,
    _scan_audio_from_index,
    _scan_file_items,
    _scan_images,
    _scan_result,
    _scan_voices_from_dirs,
    _segment_is_ui,
    _segment_tokens,
    _sound_row,
    _table_paths_from_registry,
    _under_skip_dir,
    _unregistered_from_disk,
    compare_registry_to_disk,
    scan_assets,
)


# ===================== 常量 =====================



# ===================== 数据结构 =====================

class AssetRegistry:
    """
    资产注册表。

    · characters / images / transforms / music / sounds: {id: row_dict}
    · custom: {table_name: {id: row_dict}}
    · references: {asset_id: ['file.rpy:line', ...]}
    · errors / warnings: 校验过程中收集的诊断信息
    """

    def __init__(self):
        self.characters = {}
        self.images = {}
        self.transforms = {}
        self.music = {}
        self.sounds = {}
        self.voices = {}
        self.custom = {}
        self.references = defaultdict(list)
        self.unresolved = defaultdict(list)
        #: **不可用的资产**：`{表属性名: {资产 ID: 原因}}`。
        #:
        #: 目前只有一种：表里登记了、但 `Path` 指向的文件**不在了**，
        #: 而且这一行**没有**人工占位标记（见 `is_placeholder_row`）。
        #: 它们仍然留在表/注册表里（对账、体检要看得见），但
        #: `AssetIndex` 建索引时**跳过** → 候选下拉里没有、引用解析不到、
        #: 编译按缺资源处理（用户 2026-10-05："表里有但实际没有的资源也不能用"）。
        self.unusable = defaultdict(dict)
        self.errors = []
        self.warnings = []
        #: 资源表是从哪个项目/目录读来的（缩略图、路径定位要用它找到真实文件）。
        #: 由 parse_asset_tables 填；纯内存构造时是 None。
        self.asset_dir = None
        self.project_root = None

    def get_asset_ids(self, asset_type):
        mapping = {
            'character': self.characters,
            'image': self.images,
            'transform': self.transforms,
            'music': self.music,
            'sound': self.sounds,
            'voice': self.voices,
        }
        return list(mapping.get(asset_type, {}).keys())


# ===================== Excel 读写 =====================

#: 资源表列名：**内部规范键（英文）→ 中文表头**（用户 2026-10-07 要"改中文"）。
#:
#: 为什么内部键不改：`parse_asset_tables` / 改名 / 删行 / 搜索 / 合并
#: 全按 `row['ID']`、`row['Path']` 取值，改内部键等于全仓库换契约。
#: 做法是**只在读写边界翻译**：
#:   · **写**：表头写中文（`write_dict_to_excel`）；
#:   · **读**：中文表头映射回规范键（`_canon_field` / `_sheet_headers`）；
#:   · 于是**老表（英文表头）继续能读**、新表是中文 —— 用户手上的表不用改名。

#: 中文表头 → 规范键（读表时用）。**只认我们自己写出去的那些名字**，
#: 用户手加的列（`Custom_xxx` / `内容编号`…）原样保留，绝不改写。
ASSET_FIELD_ALIASES = {label: field
                       for field, label in ASSET_FIELD_LABELS.items()}
#: 旧中文表头**继续能读**（用户手上的表不用改名）——
#: `Tag` / `Attributes` 在 2026-10-18 改了更准确的中文名。
ASSET_FIELD_ALIASES.update({
    '角色标签': 'Tag',
    '表情': 'Attributes',          # 旧表头（其实装的是所有立绘属性）
})


def _canon_field(name):
    """表头 → 规范键（中文映射回英文；认不出就**原样返回**）。"""
    if name is None:
        return None
    return ASSET_FIELD_ALIASES.get(str(name).strip(), name)


def _sheet_headers(ws) -> list:
    """
    表头行 → **规范列名**（顺序 = 表里的列顺序）。

    直接读 `ws[1]` 的地方都要用它：表头改成中文之后，`'ID' not in headers`
    这类判据会全部落空（改名/删行**静默不生效**）。
    """
    return [_canon_field(c.value) for c in ws[1]]


#: ⚠ **列顺序目前不动**（用户 2026-10-07 说"与内容编号换位置往前挪"，
#: 但"内容编号"是哪一列还没确认）。
#:
#: 试过一版"顺序表 + 重排表头"（`Path` 提到 `ID` 前），**当场 4 条判据红**：
#: 表头一重排，**按行追加的那些写入点**（固定按 `['ID','Path',…]` 写值）
#: 就和表头错位了 —— 实测 ID 被读成 `game/images/bg/planned.png`
#: （占位行的 ID 变成了路径）。结论：
#:
#:   **换位不是"改表头那一行"就能完成的**：要么把列顺序做成"头与行共用
#:   同一份顺序表"（所有写入点都得跟着走），要么在写表时**按表头名逐列取值**
#:   而不是按位置。两件事都比"改一行"大，得单独一轮做。
#:
#: 所以这一版**只交付中文表头**（已验证：老表照旧能读能改、6 处直接读
#: `ws[1]` 的地方都改走 `_sheet_headers`）。换位等确认列名后再动。


def read_excel_rows(file_path) -> list:
    """
    读取 Excel 首个 sheet，返回 [{header: value, ...}, ...]。

    **`data_only=False`**（2026-10-05，与 `story.excel_import` 同一处修复）：
    资源表的 `Doc` / `Notes` / `ATL` 都是**自由文本**，里面完全可能出现
    以 `=` 开头的内容（transform 说明里的 `=====` 分隔线、用户写的
    `=TODO`）。openpyxl 会给这样的字符串打上 `data_type='f'`，而
    `data_only=True` 读到的是**缓存的计算结果** —— 这张表从没被 Excel
    打开算过，缓存是空的，于是那些格读回来是 `None`，**用户的文本静默消失**。

    要的是"格子里的原始字符串"，不是"公式的值"：资源表里没有需要计算的
    真实公式（下拉框走 defined name，不属于单元格值）。
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return []
    wb = openpyxl.load_workbook(file_path, data_only=False)
    ws = wb.active
    # 表头中文 → 规范键（老表英文表头原样通过）—— 见 `_canon_field`。
    headers = [_canon_field(cell.value) for cell in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None for v in row):
            continue
        row_dict = {}
        for i, header in enumerate(headers):
            if header is not None:
                row_dict[header] = row[i] if i < len(row) else None
        rows.append(row_dict)
    wb.close()
    return rows


# 安全写单元格的唯一实现在 `storygraph.excel.cells` —— 这段逻辑曾在这里、
# script_table、compile 三处各写一份（行为已开始漂移）。保留别名，调用点写法不变。



def write_dict_to_excel(file_path, data_dict: dict, field_order: list):
    """
    将 {id: row_dict} 写入 Excel。

    表头写**中文**（`ASSET_FIELD_LABELS`），取值仍按**规范键**
    （`field_order` 是内部英文键；`row_data` 也是）——
    所以"改成中文"只动了表头那一行，数据语义一个字节都没变（用户 2026-10-07）。

    列顺序**不动**（用户 2026-10-07 后来确认：`编号`/`ID` 就是"本该藏起来的
    那个工具内部用的东西，已经藏起来就不需要换位"）→ 这里改为**把 `ID` 列隐藏**：
    策划看到的第一列就是 `真实文件`，效果与"换位"一样，但**零风险**
    （隐藏列 openpyxl 照旧读得到，改名/删行/搜索都不受影响）。
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([ASSET_FIELD_LABELS.get(f, f) for f in field_order])
    for _asset_id, row_data in data_dict.items():
        excel_row = [row_data.get(field, '') for field in field_order]
        _asset_append_row(ws, excel_row)
    # **`ID`（编号）整列隐藏**（用户 2026-10-07："它就是那个本该藏起来、
    # 没什么用的内部编号，已经藏起来就不需要换位了"）。
    # 隐藏只是观感：openpyxl 读得到、`rename`/`delete`/搜索全不受影响。
    try:
        from openpyxl.utils import get_column_letter
        col = get_column_letter(list(field_order).index('ID') + 1) \
            if 'ID' in list(field_order) else ''
        if col:
            ws.column_dimensions[col].hidden = True
    except Exception as e:                      # noqa: BLE001 - 隐藏失败不影响数据
        swallow('assets.asset_registry.write_dict_to_excel.hide_id', e)
    wb.save(file_path)


# ===================== 智能扫描 =====================





#: UI 目录的**精确词元**（含 `dutui` 拆不开时靠后缀兜）
#: UI 目录的**词尾**（复合词：`dutui` / `charactercenter` / `mainmenu`）

#: 立绘目录的**精确词元**（`characters/ling/` 这种）

#: "图片根"目录名：它们的**直接子目录**按角色目录看
#: （`images/eileen/happy.png` → 角色 eileen）
#: 背景 / CG 目录的精确词元

#: 文件名里的**弱线索**（只在没有任何目录语义时才用）
#:
#: 背景那条允许**连写**（`bgbed.jpg` / `bgforest.jpg` —— 实测迷你游戏里
#: 全是这种写法），但排掉 `bgm`（那是音乐目录/文件）。
#: 界面/流程类文件名（`start_menu_glamour.jpg` 这种菜单底图）

#: 平铺文件名首段**不是**角色名的那些词（`images/bg_1.jpg` / `icon_x.png`）
#:
#: 第二组是**界面/流程用的词**（`start_menu_glamour.jpg` 是迷你游戏的菜单底图，
#: 不是谁的立绘）：实测把它们当角色名会造出 `start` 这种不存在的 tag。


#: 扫描时**整目录跳过**的名字：这些不是游戏素材。
#:
#: 实测（2026-10-05，用户克隆）：`tmp/` 里有 11 张 `about_collection_smoke.png`
#: 之类的临时截图被当成资源收进表，于是"指向 `game/` 之外的行"多出 12 条，
#: 每次对账都在报噪声。Ren'Py 的素材在 `game/` 下，项目根上只放工程文件。








# 图片目录里"只是容器、不含语义"的名字：它们不能当 image tag 用
# （`images/eileen/happy.png` 的 tag 是 eileen 而不是 images）。






# 音频用途判定的目录白名单。目录名比文件名可靠得多：
# `audio/night.ogg` 是首曲子，但文件名里既没有 bgm 也没有 music，
# 旧实现一律判成"音效"——于是 Music 下拉里空空如也，文案根本选不到音乐。

# 文件名里的"用途线索"。音效线索优先判定：`sfx_xxx.ogg` 就算放在
# music/ 目录下也是音效，名字比目录更具体。
































# ===================== Excel 生成 =====================

def asset_table_dirs(project_root, asset_dir=None) -> list:
    """
    资源表的候选目录 —— **顺序必须与 `pipeline.build.load_asset_registry` 一致**。

    （两处各写一套的话，"能找到但建在别处"就会变成"每次打开都要重建"。）
    """
    root = Path(project_root)
    out = []
    if asset_dir:
        out.append(Path(asset_dir))
    out += [root / 'asset_tables', root / 'game' / 'asset_tables']
    return out


def is_real_table_file(p) -> bool:
    """
    这是一个**真资源表**吗？

    排除三类"看着像表、其实不该当表用"的文件（2026-10-05 实测踩到）：

      · `images.bak.xlsx` —— 我们自己的备份（把目录里只剩备份的情况误判成
        "已经有表了"，于是永远不建表、打开项目得到一张**空表**）；
      · `~$images.xlsx` —— Excel 打开文件时放的锁文件；
      · `*.broken-<时间戳>.xlsx` —— 读不出来时我们挪到一边的坏文件。
    """
    name = str(getattr(p, 'name', p))
    low = name.lower()
    if name.startswith('~$'):
        return False
    if low.endswith('.bak.xlsx') or '.broken-' in low:
        return False
    return low.endswith('.xlsx')


#: 兼容内部旧写法。
_is_real_table_file = is_real_table_file


def find_asset_tables(project_root, asset_dir=None):
    """已经有资源表（任一候选目录里有**真表**）→ 返回那个目录；没有返回 None。"""
    for d in asset_table_dirs(project_root, asset_dir):
        try:
            if d.is_dir() and any(is_real_table_file(p)
                                  for p in d.glob('*.xlsx')):
                return d
        except OSError:
            continue
    return None


def _quarantine_broken_tables(table_dir, reason: str = '') -> list:
    """
    把**读不出来**的表文件挪到一边（改名成 `*.broken-<时间戳>.xlsx`），返回清单。

    为什么不删：那是用户的数据 —— 也许是 Excel 正开着（锁住）、也许写坏了，
    但**都要留着**让人自己去救（或者从我们的 `.bak.xlsx` 恢复）。
    能读的文件一个都不动（例如只有 `images.xlsx` 坏了，`characters.xlsx`
    照旧保留，重建时不会被覆盖）。
    """
    moved = []
    import time
    stamp = time.strftime('%Y%m%d-%H%M%S')
    try:
        for p in sorted(Path(table_dir).glob('*.xlsx')):
            if not is_real_table_file(p):
                continue
            try:
                read_excel_rows(p)               # 读得动就留着
                continue
            except Exception as e:               # noqa: BLE001 - 读不动 → 挪走
                swallow('assets.asset_registry._quarantine_broken_tables', e)
            target = p.with_name(f'{p.stem}.broken-{stamp}.xlsx')
            try:
                p.rename(target)
                moved.append(target.name)
            except OSError as e:                 # noqa: BLE001 - 挪不动就算了
                swallow('assets.asset_registry._quarantine_broken_tables.move', e)
    except Exception as e:                       # noqa: BLE001
        swallow('assets.asset_registry._quarantine_broken_tables.scan', e)
    if moved and reason:
        pass                                     # 原因由调用方写进日志
    return moved








def _reload_tables_and_diff(table_dir, root, walk, assets, tag: str):
    """
    删 / 改指 / 合并之后**重新读表并重算对账**（内存里的注册表已经过期）。

    三处一模一样 → 收敛成一处（M4 重构：只为拆复杂度，行为一字不改）。
    失败只留痕，**返回 `(None, None, None)`**，调用方按"保持原值"处理
    —— 与从前"except 里什么都不赋"等价。
    """
    try:
        registry = parse_asset_tables(
            table_dir, hooks=load_hooks(project_root=root),
            project_root=root, walk=walk)
        diff = compare_registry_to_disk(root, registry, walk=walk,
                                        assets=assets)
        return registry, diff, (diff or {}).get('unregistered') or {}
    except Exception as e:                      # noqa: BLE001
        swallow(f'assets.asset_registry.sync_asset_tables.{tag}', e)
        return None, None, None


#: 资源表里"按文件一一对应"的四类（舞台/音频之外的都归这里）。


def _junk_nested_paths(rows, mine_paths, nested_pref) -> list:
    """
    工具自己加过、**却指向嵌套副本**的那些 `Path`（纯函数）。

    "嵌套副本"= 项目根下另有一份带 `game/` 的树（`walk.nested_prefixes()`）。
    那些文件不属于本项目资源；行却是工具自己补登的（侧车记着）→ 该删。
    用户**自己写的**行不在此列（不在侧车里就不动）。
    """
    mine = {norm_asset_path(p) for p in (mine_paths or [])}
    if not mine or not nested_pref:
        return []
    out = []
    for _aid, row in (rows or {}).items():
        rel = str((row or {}).get('Path') or '')
        low = norm_asset_path(rel)
        if low in mine and any(low.startswith(p) for p in nested_pref):
            out.append(rel)
    return out


def _missing_paths(rows, skip, kind_key, walk, root) -> list:
    """
    `Path` 解析不到真实文件的行（纯查询；人工占位行豁免）。

    `skip` 是"上面已经删过的"（不重复报）。
    """
    from storygraph.story.excel_export import resolve_thumb_source

    out = []
    for _aid, row in (rows or {}).items():
        rel = str((row or {}).get('Path') or '').strip()
        if not rel or is_placeholder_row(row):
            continue                            # 占位：允许没有文件
        if rel in skip:
            continue                            # 上面已经删了
        if walk is not None:
            real = walk.match(rel)
        elif kind_key == 'images':
            real = resolve_thumb_source(rel, root)
        else:
            real = _resolve_audio_source(rel, root)
        if real is None:
            out.append(rel)
    return out


def _sync_prune_stage(root, table_dir, registry, walk):
    """
    ❶ **自动删**：`Path` 解析不到真实文件的行跟着走。

    两个例外（与用户 2026-10-05 的定案一致）：
      · **人工占位**（`Notes`/`Doc` 写了占位…）留着；
      · **工具自己加过、却指向"嵌套副本"** 的行一并删（只有侧车记着我加过的才删）。

    返回 `(removed_by_kind, index, added_paths)`；顺手把删掉的从侧车摘掉。
    """
    index = _read_autoscan_index(table_dir)
    added = index.get('added') or {}
    removed_by_kind = {}
    nested_pref = walk.nested_prefixes() if walk is not None else set()
    for kind_key in _ASSET_KINDS:
        rows = ((getattr(registry, kind_key, {}) or {})
                if registry is not None else {})
        if not rows:
            continue
        # ② 工具加过、却指向嵌套副本的行（判据见 `_junk_nested_paths`）
        junk_nested = _junk_nested_paths(rows, added.get(kind_key),
                                         nested_pref)
        if junk_nested:
            n = delete_rows_from_table(table_dir, kind_key, junk_nested,
                                       backup=True)
            if n:
                removed_by_kind[kind_key] = removed_by_kind.get(kind_key, 0) + n
                dead = set(junk_nested)
                added[kind_key] = [p for p in (added.get(kind_key) or [])
                                   if p not in dead]
        gone = _missing_paths(rows, set(junk_nested), kind_key, walk, root)
        if not gone:
            continue
        n = delete_rows_from_table(table_dir, kind_key, gone, backup=True)
        if n:
            removed_by_kind[kind_key] = removed_by_kind.get(kind_key, 0) + n
        if added.get(kind_key):
            dead = set(gone)
            added[kind_key] = [p for p in added[kind_key] if p not in dead]
    index['added'] = added
    return removed_by_kind, index, added


def _sync_relink_stage(root, table_dir, registry, walk):
    """
    ❷ **改指**：表里的行指向**嵌套副本**，而同一个资源在本项目 `game/` 里也有
    一份 → 把 `Path` 改指到本项目里的那一份。

    只有当 `walk` 认得嵌套前缀时才做（否则无从判断"副本"）。
    ⚠ 判据用**路径尾巴**、不能用 ID（表里老行的 ID 是 `_base` 这种，扫出来是
    `base`，对不上 —— 实测）。
    """
    relinked_by_kind = {}
    game_dir = root / 'game'
    if walk is None or not walk.nested_prefixes():
        return relinked_by_kind
    for kind_key in _ASSET_KINDS:
        table_rows = ((getattr(registry, kind_key, {}) or {})
                      if registry is not None else {})
        if not table_rows:
            continue
        updates = {}
        for aid, row in table_rows.items():
            rel = str((row or {}).get('Path') or '').strip().replace('\\', '/')
            if not rel or not walk.is_nested(rel):
                continue
            parts = [p for p in rel.split('/') if p not in ('', '.')]
            cand = None
            for i in range(1, len(parts)):       # 逐段剥前缀，找 `game/…`
                probe = '/'.join(parts[i:])
                if probe.lower().startswith('game/'):
                    cand = probe
                    break
            if not cand:
                continue
            new_real = walk.match(cand)
            if new_real is None:
                continue                         # 本项目里没有这一份：不动
            try:
                Path(new_real).relative_to(game_dir)
            except ValueError:
                continue
            updates[str(aid)] = cand
        if updates:
            n = update_table_paths(table_dir, kind_key, updates, backup=True)
            if n:
                relinked_by_kind[kind_key] = n
    return relinked_by_kind


def _sync_dedupe_stage(table_dir, registry):
    """
    ❸ **同一文件只留一行**：改指之后"原本指副本的那行"与"原本指本项目的那行"
    会指向同一个文件 → 必须合并，否则"一一对应"反过来被破坏（实测改指 1386 行
    会产生 ~1390 行重复）。占位行不动（它没有文件）。
    """
    deduped_by_kind = {}
    for kind_key in _ASSET_KINDS:
        rows = ((getattr(registry, kind_key, {}) or {})
                if registry is not None else {})
        if not rows:
            continue
        seen = {}
        drop_ids = []
        for aid, row in rows.items():
            if is_placeholder_row(row):
                continue                        # 占位行不动（它没有文件）
            key = norm_asset_path((row or {}).get('Path'))
            if not key:
                continue
            prev = seen.get(key)
            if prev is None:
                seen[key] = (aid, row)
                continue
            keep, drop = _prefer_row(prev, (aid, row))
            seen[key] = keep
            drop_ids.append(str(drop[0]))
        if not drop_ids:
            continue
        n = delete_rows_by_id(table_dir, kind_key, drop_ids, backup=True)
        if n:
            deduped_by_kind[kind_key] = n
    return deduped_by_kind


def _sync_append_stage(root, table_dir, assets, unreg, diff, walk, added):
    """
    ❹ **补登**：磁盘上有、表里没有的 → 追加（`append_rows_to_table`），并记进
    侧车（以后归工具管）。返回 `(added_by_kind, backup_names)`。

    ⚠ **按解析后的真实文件判重**（不是按字符串）：表里可能写
    `music/night.ogg`，扫出来是 `game/music/night.ogg` —— 字符串不等但是
    同一个文件；只比字符串会给它写第二行（实测）。
    """
    from storygraph.story.excel_export import resolve_thumb_source

    rows_by_kind = {
        'images': assets.get('images') or [],
        'music': (assets.get('audio') or {}).get('music') or [],
        'sounds': (assets.get('audio') or {}).get('sound') or [],
        'voices': assets.get('voices') or [],
    }
    have_resolved = (diff or {}).get('table_resolved') or {}
    added_by_kind = {}
    backups = []
    for kind_key, missing_paths in unreg.items():
        want = {norm_asset_path(p) for p in (missing_paths or []) if p}
        rows = [r for r in rows_by_kind.get(kind_key) or []
                if norm_asset_path(r.get('Path')) in want]
        if not rows:
            continue
        already = set(have_resolved.get(kind_key) or ())
        keep = []
        for r in rows:
            if walk is not None:
                real = walk.match(r.get('Path'))
            elif kind_key != 'images':
                real = _resolve_audio_source(r.get('Path'), root)
            else:
                real = resolve_thumb_source(r.get('Path'), root)
            if real is not None and str(Path(real).resolve()) in already:
                continue                         # 这个文件已经在表里了
            keep.append(r)
        if not keep:
            continue
        n = append_rows_to_table(table_dir, kind_key, keep,
                                 skip_paths=set(), backup=True)
        if n:
            added_by_kind[kind_key] = n
            added.setdefault(kind_key, [])
            for r in keep:
                rp = str(r.get('Path') or '')
                if rp and rp not in added[kind_key]:
                    added[kind_key].append(rp)   # 记进侧车：以后归我管
            bak = table_dir / (Path(_STANDARD_TABLES[kind_key]).stem
                               + '.bak.xlsx')
            if bak.is_file():
                backups.append(bak.name)
    return added_by_kind, backups


def _sync_report(*, table_dir, registry, diff, repaired, repair_error,
                 removed_by_kind=None, relinked_by_kind=None,
                 deduped_by_kind=None, added_by_kind=None, backup: str = ''):
    """带"删/改指/合并/补登"四项统计的返回体（原先两处各写一遍）。"""
    removed_by_kind = removed_by_kind or {}
    relinked_by_kind = relinked_by_kind or {}
    deduped_by_kind = deduped_by_kind or {}
    added_by_kind = added_by_kind or {}
    return {'ok': True, 'created': '', 'table_dir': str(table_dir),
            'added': sum(added_by_kind.values()), 'by_kind': added_by_kind,
            'removed': sum(removed_by_kind.values()),
            'removed_by_kind': removed_by_kind,
            'relinked': sum(relinked_by_kind.values()),
            'relinked_by_kind': relinked_by_kind,
            'deduped': sum(deduped_by_kind.values()),
            'deduped_by_kind': deduped_by_kind,
            'backup': backup,
            'unusable': _unusable_summary(registry),
            'scanned': (diff or {}).get('disk') or {},
            'reconcile': diff or {}, 'repaired': repaired,
            'repair_error': repair_error, 'registry': registry}


def sync_asset_tables(project_root, registry=None, asset_dir=None,
                      auto_append: bool = True, walk=None) -> dict:
    """
    **打开项目时的表 ↔ 磁盘对齐**（用户 2026-10-05 的目标）：

    > 每次打开项目文件，若没有表，建立表，若表已经存在，自动更新表中的资源，
    > 确保表与资源自动一一对应。

    行为（**只增不改**，写前备份）：

      ① 没有表 → 按项目内容**新建**（`ensure_asset_tables`）；
      ② 已有表 → 把"磁盘上有、表里没有"的图片/音乐/音效/语音**自动追加**
         （`append_rows_to_table(skip_paths=…, backup=True)`）—— 按 **Path**
         判重，所以是"一个文件一行"；**已存在的行一个字段都不动**
         （`Category` / `Notes` / `Tag` 里人改过的值必须活着）。

    返回：

        {'ok', 'created', 'table_dir', 'added', 'by_kind', 'backup',
         'scanned', 'reconcile'}

    ⚠ **不做的事**（都要用户明确动作）：删行（表里有、磁盘上没了）、
    改已有行的字段、把指向别的目录（另一份副本）的行搬过来 ——
    那些会动到用户整理过的数据。
    """
    root = Path(project_root)
    # ⚠ 本地 import 必须放在**函数最前面**：后面几处（删行判存、判重）
    # 都要用它，而 Python 会把"函数里任何位置 import 的名字"当成本地变量
    # —— 放在下面就会 `UnboundLocalError`（实测踩到）。
    from storygraph.story.excel_export import resolve_thumb_source
    made = ensure_asset_tables(root, asset_dir, walk=walk,
                               parse_check=registry is None)
    repaired = made.get('repaired') or []
    repair_error = made.get('repair_error') or ''
    if not made.get('ok'):
        return {'ok': False, 'created': '', 'table_dir': '', 'added': 0,
                'by_kind': {}, 'backup': '', 'error': made.get('error'),
                'repaired': repaired, 'repair_error': repair_error,
                'scanned': {}, 'reconcile': {}}
    table_dir = Path(made['path']) if made.get('path') else None
    if table_dir is None:
        return {'ok': False, 'created': '', 'table_dir': '', 'added': 0,
                'by_kind': {}, 'backup': '',
                'error': '找不到资源表目录', 'repaired': repaired,
                'repair_error': repair_error, 'scanned': {}, 'reconcile': {}}
    if made.get('created'):
        # 刚按项目内容建好（含"坏表挪走之后重建"）→ 表与磁盘本来就一致
        return {'ok': True, 'created': made['created'],
                'table_dir': str(table_dir), 'added': 0, 'by_kind': {},
                'backup': '', 'scanned': made.get('counts') or {},
                'reconcile': {}, 'repaired': repaired,
                'repair_error': repair_error,
                'registry': made.get('registry')}

    # 已经解析过一遍（`ensure_asset_tables` 读得动时会顺手带回来）——
    # 别再解析一次：真实项目 2890 行，一次解析不便宜。
    if registry is None and made.get('registry') is not None:
        registry = made.get('registry')

    if registry is None:
        try:
            registry = parse_asset_tables(
                table_dir, hooks=load_hooks(project_root=root),
                project_root=root)
        except Exception as e:                  # noqa: BLE001 - 读不动就当没漂移
            swallow('assets.asset_registry.sync_asset_tables.parse', e)
            registry = None
    # ⚠ **磁盘只扫一次**，对账和补登共用（原先各扫一次：真实项目 2 × 0.2 秒
    # 是小事，但它们各自还会再走一遍索引里的 5854 个文件）。
    assets = scan_assets(root, include_ui=True, walk=walk)
    diff = (compare_registry_to_disk(root, registry, walk=walk, assets=assets)
            if registry is not None else {})
    unreg = (diff or {}).get('unregistered') or {}
    # —— ❶ **自动删**：文件没了的行，跟着走 ——
    #
    # 用户 2026-10-05（看到"表里有 11 行指向的文件已经不在 → 已标为不可用"之后）：
    #
    # > 已经不在那这个东西实际上不需要啦！你自己自动删改表即可！
    # > 之前就说表跟资源一一对应！如果不可用直接删了就行！
    #
    # 所以现在**不再区分"谁加的行"**：只要 `Path` 解析不到真实文件就删。
    # 两个例外：
    #   · **人工占位**（`Notes`/`Doc` 写「占位」/placeholder/…）留着 —— 那是
    #     用户明说"这个还没有出图"，见上一轮那条"这个允许"；
    #   · 删之前一定备份 `*.bak.xlsx`；删不掉（例如表在 Excel 里锁着）就
    #     退回"标为不可用"，并在结果里报出来（`unusable`）。
    # —— ❶ **自动删**：文件没了的行，跟着走 ——
    #
    # 用户 2026-10-05（看到"表里有 11 行指向的文件已经不在 → 已标为不可用"之后）：
    #
    # > 已经不在那这个东西实际上不需要啦！你自己自动删改表即可！
    # > 之前就说表跟资源一一对应！如果不可用直接删了就行！
    #
    # 两个例外（人工占位留着；工具加过又指向嵌套副本的删掉）与备份规矩
    # 都在 `_sync_prune_stage` 里 —— 这里只负责编排（M4 重构）。
    removed_by_kind, index, added_paths = _sync_prune_stage(
        root, table_dir, registry, walk)
    if removed_by_kind:
        _write_autoscan_index(table_dir, index)
        # 删完要重新读表（内存里的注册表也过期了）
        _r, _d, _u = _reload_tables_and_diff(table_dir, root, walk, assets,
                                             'reload')
        if _r is not None:
            registry, diff, unreg = _r, _d, _u

    # —— ❷ **改指** + ❸ **合并重复** ——
    #
    # （为什么现在才敢自动改指：上一版试过，结果跟"自动补登"**打架** ——
    #  ping-pong：改指之后副本文件变成"未登记"→ 补登又加回来，未登记
    #  239 → 1644。现在 `scan_assets` 已经把嵌套副本**排除在项目资源之外**，
    #  两条规则终于共用同一个"什么算项目资源"的定义。）
    #
    # 两步都只在 `walk` 认得嵌套前缀时才做；判据与实现见各自的阶段函数
    # （M4 重构：只为拆复杂度，行为一字不改）。
    relinked_by_kind = _sync_relink_stage(root, table_dir, registry, walk)
    if relinked_by_kind:
        _r, _d, _u = _reload_tables_and_diff(table_dir, root, walk, assets,
                                            'relink')
        if _r is not None:
            registry, diff, unreg = _r, _d, _u
    if walk is not None and walk.nested_prefixes():
        deduped_by_kind = _sync_dedupe_stage(table_dir, registry)
        if deduped_by_kind:
            _r, _d, _u = _reload_tables_and_diff(table_dir, root, walk, assets,
                                                 'dedupe')
            if _r is not None:
                registry, diff, unreg = _r, _d, _u
    else:
        deduped_by_kind = {}

    if not auto_append or not unreg:
        baks = sorted({p.name for p in table_dir.glob('*.bak.xlsx')}
                      & {Path(_STANDARD_TABLES[k]).stem + '.bak.xlsx'
                         for k in removed_by_kind})
        return _sync_report(table_dir=table_dir, registry=registry,
                            diff=diff, repaired=repaired,
                            repair_error=repair_error,
                            removed_by_kind=removed_by_kind,
                            relinked_by_kind=relinked_by_kind,
                            deduped_by_kind=deduped_by_kind,
                            backup='、'.join(baks))

    # （`assets` 已经在上面扫过一次了，这里直接用 —— 别再扫第二遍。）
    # ❹ **补登**：磁盘上有、表里没有的 → 追加 + 记侧车。判重按**解析后的
    # 真实文件**（不是按字符串）—— 细节都在 `_sync_append_stage`（M4 重构）。
    added_by_kind, backups = _sync_append_stage(
        root, table_dir, assets, unreg, diff, walk, added_paths)
    if added_by_kind:
        index['added'] = added_paths
        _write_autoscan_index(table_dir, index)
    return _sync_report(table_dir=table_dir, registry=registry, diff=diff,
                        repaired=repaired, repair_error=repair_error,
                        removed_by_kind=removed_by_kind,
                        relinked_by_kind=relinked_by_kind,
                        deduped_by_kind=deduped_by_kind,
                        added_by_kind=added_by_kind,
                        backup='、'.join(sorted(set(backups))))


def _unusable_summary(registry) -> dict:
    """"表里有、文件没了"的条数（按类型）—— 给日志/界面看。"""
    out = {}
    for kind, attr in (('images', 'images'), ('music', 'music'),
                       ('sounds', 'sounds'), ('voices', 'voices')):
        n = len((getattr(registry, 'unusable', {}) or {}).get(attr) or {})
        if n:
            out[kind] = n
    return out


def _table_file_name(table_key: str) -> str:
    """
    资源表键 → 文件名（**唯一解析点**）。

    ⚠ 键是**复数**（`'characters'`，见 `_STANDARD_TABLES`），而
    `_TABLE_FIELDS` 用的是**单数**（`'character'`）—— 两套名字并存，实测踩到过：
    调用方写 `'character'` → 老实现**静默返回 0**（以为写成功、其实什么都没发生）。
    所以这里对"未知键"**出声**，而不是悄悄当 0。
    """
    key = str(table_key or '')
    fname = _STANDARD_TABLES.get(key)
    if not fname and key:
        raise KeyError(f'未知资源表键 {key!r}；可用：{sorted(_STANDARD_TABLES)}'
                       f'（注意是**复数**）')
    return fname or ''


def update_table_paths(asset_dir, table_key: str, updates: dict,
                       backup: bool = True) -> int:
    """
    按 **ID → 新 Path** 改一行里的 `Path`（只改这一格），返回改了几行。

    为什么需要它（2026-10-05 实测）：用户的表里有 1386 行指向**嵌套副本**
    `sr_general/**`，而同一个资源在 `game/` 里**也有一份**。那种行会让
    ①"磁盘上这份文件没人登记"（补登记时 ID 撞车、被静默跳过），
    ②导出的引用写到 `sr_general/game/…`（Ren'Py 找不到）。
    把 `Path` **改指**到本项目 `game/` 里的同名文件是最小、最安全的修法：
    不删行、不动其它字段、不产生重复候选，写前照旧 `*.bak.xlsx`。
    """
    updates = {str(k): str(v) for k, v in (updates or {}).items() if k and v}
    if not updates:
        return 0
    fname = _table_file_name(table_key)
    if not fname:
        return 0
    path = Path(asset_dir) / fname
    if not path.is_file():
        return 0
    changed = 0
    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        headers = _sheet_headers(ws)
        if 'ID' not in headers or 'Path' not in headers:
            return 0
        id_col = headers.index('ID') + 1
        p_col = headers.index('Path') + 1
        from storygraph.excel.cells import set_text
        for r in range(2, ws.max_row + 1):
            aid = ws.cell(row=r, column=id_col).value
            if aid is None:
                continue
            new = updates.get(str(aid))
            if not new:
                continue
            if str(ws.cell(row=r, column=p_col).value or '') == new:
                continue
            set_text(ws, r, p_col, new)
            changed += 1
        if changed:
            if backup:
                _backup_table(path)
            wb.save(path)
    except Exception as e:                      # noqa: BLE001 - 改不动就当没改
        swallow('assets.asset_registry.update_table_paths', e)
        return 0
    return changed


def delete_rows_from_table(asset_dir, table_key: str, paths,
                           backup: bool = True) -> int:
    """
    从资源表里**删掉 Path 命中的行**，返回删掉的行数（写前默认备份）。

    只给"自动跟随现实"用：工具自己加进去的行，文件被删掉之后就跟着走
    （用户 2026-10-05："我认为表应当实时反应实际资源的情况，自动更新和增删"）。
    **人工录入的行绝不走这条**（由侧车 `_autoscan.json` 判归属，见
    `sync_asset_tables`）—— 占位行更是要留着。
    """
    keys = {norm_asset_path(p) for p in (paths or ()) if p}
    if not keys:
        return 0
    fname = _table_file_name(table_key)
    if not fname:
        return 0
    path = Path(asset_dir) / fname
    if not path.is_file():
        return 0
    removed = 0
    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        headers = _sheet_headers(ws)
        if 'Path' not in headers:
            return 0
        pcol = headers.index('Path') + 1
        for r in range(ws.max_row, 1, -1):       # 倒着删，行号不飘
            v = ws.cell(row=r, column=pcol).value
            if v and norm_asset_path(v) in keys:
                ws.delete_rows(r, 1)
                removed += 1
        if removed:
            if backup:
                _backup_table(path)
            wb.save(path)
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_registry.delete_rows_from_table', e)
        return 0
    return removed


def delete_rows_by_id(asset_dir, table_key: str, ids, backup: bool = True) -> int:
    """
    按 **ID** 删行（`delete_rows_from_table` 是按 `Path` 删，删不掉"同一文件两行"）。

    为什么需要（2026-10-05 实测）：表里原本一行指 `game/images/x.png`、
    另一行指 `sr_general/game/images/x.png`（同一份资源的两个副本）。
    把它们**改指**到同一个文件之后，两行的 `Path` 就一样了 —— 这时按
    `Path` 删会把两行一起删掉 ✗，必须按 ID 精确删一行。
    """
    keys = {str(i) for i in (ids or ()) if i}
    if not keys:
        return 0
    fname = _table_file_name(table_key)
    if not fname:
        return 0
    path = Path(asset_dir) / fname
    if not path.is_file():
        return 0
    removed = 0
    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        headers = _sheet_headers(ws)
        if 'ID' not in headers:
            return 0
        id_col = headers.index('ID') + 1
        for r in range(ws.max_row, 1, -1):
            v = ws.cell(row=r, column=id_col).value
            if v is not None and str(v) in keys:
                ws.delete_rows(r, 1)
                removed += 1
        if removed:
            if backup:
                _backup_table(path)
            wb.save(path)
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_registry.delete_rows_by_id', e)
        return 0
    return removed


def _prefer_row(a: tuple, b: tuple) -> tuple:
    """
    "同一文件两行"留哪一行：`(keep, drop)`。

    规则（确定性，先看谁更有信息）：
      1. **带 Tag 的**优先（立绘/背景那类行能直接参与 `show` 解析）；
      2. 再看 `Attributes` 非空；
      3. 最后按 ID 字典序 —— 稳定，不随表里行序变化。
    """
    (aid_a, row_a), (aid_b, row_b) = a, b

    def score(item):
        _aid, row = item
        row = row or {}
        return (1 if str(row.get('Tag') or '').strip() else 0,
                1 if str(row.get('Attributes') or '').strip() else 0)

    sa, sb = score(a), score(b)
    if sa != sb:
        return (a, b) if sa > sb else (b, a)
    return (a, b) if str(aid_a) <= str(aid_b) else (b, a)


def _autoscan_index_path(table_dir) -> Path:
    """侧车文件：记录**工具自己加过哪些行**（用于安全地自动删）。"""
    return Path(table_dir) / '_autoscan.json'


def _read_autoscan_index(table_dir) -> dict:
    """读侧车（坏了/没有 → 空索引 = 一切按人工处理，保守）。"""
    p = _autoscan_index_path(table_dir)
    try:
        if p.is_file():
            import json
            data = json.loads(p.read_text(encoding='utf-8'))
            if isinstance(data, dict) and isinstance(data.get('added'), dict):
                return data
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_registry._read_autoscan_index', e)
    return {'version': 1, 'added': {}}


def _write_autoscan_index(table_dir, index: dict) -> None:
    p = _autoscan_index_path(table_dir)
    try:
        import json
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(index, ensure_ascii=False, indent=1),
                     encoding='utf-8')
    except Exception as e:                      # noqa: BLE001 - 侧车写不了就退回保守
        swallow('assets.asset_registry._write_autoscan_index', e)


def _write_script_tables(project_root, target) -> dict:
    """
    把**脚本里声明的**角色 / 变换也写进表（返回条数）。

    为什么必须补这一步：`scan_assets` 只能扫**文件**（图片/音频），
    而 `define e = Character("爱莲")`、`transform i11:` 这些**只存在于 .rpy 里**
    —— 真实项目里"变换 146"就是全靠它。只扫文件建出来的表，
    位置候选（`i11`）和说话人候选都会是空的。

    ⚠ 这里**函数内 import `story.*`**：`story.script_table` 在模块级 import 了
    `assets`，模块级反向 import 会成环（`assets → story → assets`）。
    """
    out = {'characters': 0, 'transforms': 0}
    try:
        from storygraph.parsing.discover import discover_scripts
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.script_table import registry_from_parse_result

        scripts = discover_scripts(project_root)
        if not scripts:
            return out
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=scripts, graph=None)
        reg = registry_from_parse_result(pr)
        chars = getattr(reg, 'characters', {}) or {}
        trans = getattr(reg, 'transforms', {}) or {}
        # ⚠ **已存在的表只追加，绝不整份重写**（2026-10-05 实测踩到）：
        # 重建 images 时如果顺手 `write_dict_to_excel(characters.xlsx, …)`，
        # 会把用户手工维护的角色表**整张覆盖**（实测：手写的 `hand_made`
        # 那行没了）。所以文件已在 → append-only（按 ID 跳过已有行）；
        # 文件不在 → 才新建。
        target_dir = Path(target)
        if chars:
            if (target_dir / 'characters.xlsx').is_file():
                append_rows_to_table(target_dir, 'characters',
                                     list(chars.values()))
            else:
                write_dict_to_excel(target_dir / 'characters.xlsx', chars,
                                    _TABLE_FIELDS['character'])
            out['characters'] = len(chars)
        if trans:
            if (target_dir / 'transforms.xlsx').is_file():
                append_rows_to_table(target_dir, 'transforms',
                                     list(trans.values()))
            else:
                write_dict_to_excel(target_dir / 'transforms.xlsx', trans,
                                    _TABLE_FIELDS['transform'])
            out['transforms'] = len(trans)
    except Exception as e:                      # noqa: BLE001 - 脚本解析失败也别挡住建表
        swallow('assets.asset_registry._write_script_tables', e)
    return out


def ensure_asset_tables(project_root, asset_dir=None, include_ui=True,
                        with_scripts: bool = True, walk=None,
                        parse_check: bool = True) -> dict:
    """
    **没有资源表就现场建一张**（扫描项目内容 → 写出 `*.xlsx`）。

    返回 `{ok, created, path, counts, error}`；`created` 非空 = 这次真的建了。

    用户 2026-10-05 实测报：

    > 我拿另一个复制看了一下测出 bug 了，首次加载导入时项目无法读到资源表，
    > 应当立即创建对应的资源表

    实测现场：复制出来的项目里 `game/asset_tables/` 是个**空目录**
    （xlsx 没跟过去）→ 卡片编辑器「打开项目」只报一句
    `读不到资源表：这个目录下找不到资源表（xlsx）（下拉候选会是空的）`
    → 立绘/音乐候选全空、缩略图全空，用户只能自己去建表（还得先知道表长什么样）。

    ⚠ 而"自动扫一份表"这件事**代码里早就有** —— 只是长在 `AssetEngine`
    的 `_ensure_asset_tables_exist()` 上，而那个引擎在当前 Qt 应用里
    **根本没有被构造**（`AssetEngine(` 在 `storygraph/ui_qt` 里一次都没出现）。
    于是"有需求、有实现、但没人调用"。这次把它提到 assets 层做**唯一真源**，
    编辑器这条路直接用它，引擎那边也改调它。

    `with_scripts`：是否把**脚本里声明的**角色 / 变换也写进表（`define x =
    Character(...)` / `transform i11:` —— 扫文件扫不出来）。
    默认 `True`（一张完整的表才有用）；**`AssetEngine` 会传 False** ——
    它自己有一套 `_sync_character_definitions`（还会打 `_from_script` 标记），
    表里先有 `characters.xlsx` 会让它走"表派"那条路、标记就没了
    （实测 `test_transforms_and_preview.py::…test_engine_writes_rich_character_row`）。
    """
    root = Path(project_root)
    try:
        found = find_asset_tables(root, asset_dir)
        if found is not None:
            # **有表 ≠ 读得出来**。2026-10-05 用户实测报：
            #
            #   [项目] 读不到资源表：这个目录下找不到资源表（xlsx），
            #          自动新建也失败了：未知原因
            #
            # 真相是表**在**、但解析抛异常（Excel 开着锁住 / 文件写坏），
            # 而这条路只说"找不到 + 未知原因" —— 既误导又没有出路。
            # 现在：先试解析；读得动就直接把注册表带回去（顺便省一次解析）；
            # 读不动就把坏文件挪到一边（`*.broken-<时间戳>.xlsx`，**不删**）
            # 再按项目内容重建 —— 能读的其它表（比如 `characters.xlsx`）不动。
            parse_error = ''
            registry_hint = None
            try:
                if parse_check:
                    reg = parse_asset_tables(
                        found, hooks=load_hooks(project_root=root),
                        project_root=root, walk=walk)
                else:
                    reg = registry_hint
                return {'ok': True, 'created': '', 'path': str(found),
                        'counts': {}, 'error': '', 'registry': reg,
                        'repaired': [], 'repair_error': ''}
            except Exception as e:              # noqa: BLE001 - 读不动 → 重建
                parse_error = f'{type(e).__name__}: {e}'
                swallow('assets.asset_registry.ensure_asset_tables.parse', e)
            repaired = _quarantine_broken_tables(found, parse_error)
            target = Path(found)
            assets = scan_assets(root, include_ui=include_ui, walk=walk)
            write_asset_excel(assets, target, split_by_category=False)
            extra = (_write_script_tables(root, target) if with_scripts
                     else {'characters': 0, 'transforms': 0})
            counts = {
                'images': len(assets.get('images') or []),
                'music': len((assets.get('audio') or {}).get('music') or []),
                'sounds': len((assets.get('audio') or {}).get('sound') or []),
                'voices': len(assets.get('voices') or []),
                'characters': extra.get('characters', 0),
                'transforms': extra.get('transforms', 0),
            }
            return {'ok': True, 'created': str(target), 'path': str(target),
                    'counts': counts, 'error': '', 'registry': None,
                    'repaired': repaired, 'repair_error': parse_error}
        if asset_dir:
            target = Path(asset_dir)
        elif (root / 'game').is_dir():
            # 项目自己的约定（真实项目就是 `game/asset_tables/`；UI 写表也写这儿）
            target = root / 'game' / 'asset_tables'
        else:
            target = root / 'asset_tables'
        assets = scan_assets(root, include_ui=include_ui)
        write_asset_excel(assets, target, split_by_category=False)
        # 角色 / 变换只能从**脚本声明**里来（`scan_assets` 扫的是文件）
        extra = (_write_script_tables(root, target) if with_scripts
                 else {'characters': 0, 'transforms': 0})
        counts = {
            'images': len(assets.get('images') or []),
            'music': len((assets.get('audio') or {}).get('music') or []),
            'sounds': len((assets.get('audio') or {}).get('sound') or []),
            'voices': len(assets.get('voices') or []),
            'characters': extra.get('characters', 0),
            'transforms': extra.get('transforms', 0),
        }
        return {'ok': True, 'created': str(target), 'path': str(target),
                'counts': counts, 'error': '', 'registry': None,
                'repaired': [], 'repair_error': ''}
    except Exception as e:                      # noqa: BLE001 - 建表失败不该让界面崩
        swallow('assets.asset_registry.ensure_asset_tables', e)
        return {'ok': False, 'created': '', 'path': '', 'counts': {},
                'error': f'{type(e).__name__}: {e}', 'registry': None,
                'repaired': [], 'repair_error': ''}


def _merge_existing(existing: dict, asset: dict, auto_fields: list) -> dict:
    """若资产已在 Excel 中存在，保留用户编辑字段，仅覆盖自动字段。"""
    aid = asset['ID']
    if aid in existing:
        merged = existing[aid].copy()
        for key in auto_fields:
            merged[key] = asset.get(key, '')
        return merged
    return asset


def write_asset_excel(assets_dict: dict, output_dir, split_by_category=False):
    """将 scan_assets 的结果写入 Excel（支持增量更新）。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 图片 ----------
    image_assets = assets_dict['images']
    if split_by_category:
        groups = defaultdict(list)
        for a in image_assets:
            groups[a['Category']].append(a)
        for cat, assets in groups.items():
            file_path = output_dir / f'images_{cat}.xlsx'
            existing = {r['ID']: r for r in read_excel_rows(file_path) if r.get('ID')}
            new_data = {}
            for a in assets:
                new_data[a['ID']] = _merge_existing(
                    existing, a, ['Path', 'Tag', 'Attributes', 'Category'])
            write_dict_to_excel(
                file_path, new_data,
                ['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'])
    else:
        file_path = output_dir / 'images.xlsx'
        existing = {r['ID']: r for r in read_excel_rows(file_path) if r.get('ID')}
        new_data = {}
        for a in image_assets:
            new_data[a['ID']] = _merge_existing(
                existing, a, ['Path', 'Tag', 'Attributes', 'Category'])
        write_dict_to_excel(
            file_path, new_data,
            ['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'])

    # ---------- 音乐 ----------
    music_file = output_dir / 'music.xlsx'
    existing = {r['ID']: r for r in read_excel_rows(music_file) if r.get('ID')}
    new_data = {}
    for a in assets_dict['audio']['music']:
        new_data[a['ID']] = _merge_existing(existing, a, ['Path', 'Loop', 'Volume'])
    write_dict_to_excel(
        music_file, new_data,
        ['ID', 'Path', 'Loop', 'Volume', 'Notes'])

    # ---------- 音效 ----------
    sound_file = output_dir / 'sounds.xlsx'
    existing = {r['ID']: r for r in read_excel_rows(sound_file) if r.get('ID')}
    new_data = {}
    for a in assets_dict['audio']['sound']:
        new_data[a['ID']] = _merge_existing(existing, a, ['Path'])
    write_dict_to_excel(
        sound_file, new_data,
        ['ID', 'Path', 'Notes'])

    # ---------- 语音 ----------
    voice_file = output_dir / 'voices.xlsx'
    existing = {r['ID']: r for r in read_excel_rows(voice_file) if r.get('ID')}
    new_data = {}
    for a in assets_dict.get('voices', []):
        new_data[a['ID']] = _merge_existing(existing, a, ['Path'])
    write_dict_to_excel(
        voice_file, new_data,
        ['ID', 'Path', 'Notes'])


# ===================== 钩子加载 =====================

def load_hooks(config_path=None, project_root=None):
    """
    加载自定义钩子模块。

    config_path=None 时按顺序搜索：
      1. project_root / 'asset_import_hooks.py'（推荐）
      2. CWD / 'asset_import_hooks.py'（兼容旧行为）
    均不存在则返回空字典。

    支持两个钩子：
      - pre_process_row(table_name, row) -> row
      - post_process_registry(registry) -> None
    """
    candidates = []
    if config_path is not None:
        candidates.append(Path(config_path))
    else:
        if project_root is not None:
            candidates.append(Path(project_root) / 'asset_import_hooks.py')
        candidates.append(Path('asset_import_hooks.py'))

    target = None
    for c in candidates:
        if c.exists():
            target = c
            break

    if target is None:
        return {}

    try:
        spec = importlib.util.spec_from_file_location(
            'asset_import_hooks', str(target)
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {
            'pre_process_row': getattr(module, 'pre_process_row', None),
            'post_process_registry': getattr(module, 'post_process_registry', None),
        }
    except Exception as e:
        print(f"[asset_registry] 加载钩子失败: {e}")
        return {}


# ===================== Excel 解析 =====================

# 标准表定义：(registry 属性名, 文件名)

# 资源表写入时的字段顺序（用于"新建资产"追加一行）
# ⚠ `Aliases`（别名，中文表头「别名」）是 2026-10-10 为**纯文字导入**加的：
# 剧本里作者可能用"表里没有的第三种写法"（`MOM` 之于变量 `mm`）—— 那是**人写的映射**，
# 只能显式配。它是**多值**列（`MOM|母亲|老妈`，`|` 或 `,` 分隔）。
# 为什么放 `Notes` 之前：人写列挨着看；为什么**不**进 `_CHAR_IDENTITY_KEYS`：
# 那个元组是**重命名识别**用的（"两边非空且不同就当成换了角色"），
# 人一编辑别名就会被误判成"换角色" —— 人写字段不该参与身份比较。

# 资产类型 → 集合属性名：已收敛到 `storygraph.core.vocabulary.ASSET_TYPE_ATTR`
# （顶部以 `_TYPE_ATTR` 别名导入）。别再就地写第二份——2026-09-28 之前
# 这里、`validate._TYPE_ATTR`、`dsl.Symbols.has()` 内联 dict 各有一份。

def add_asset_to_registry(registry, asset_type, asset_id, path='',
                         asset_dir=None):
    """
    在注册表（与可选的资源表 .xlsx）里登记一个新资产。

    M6：从资产选择器"新建资产..."直接写回资源表，打通回写链路。
    返回是否成功；asset_type 非法或 asset_id 为空返回 False。
    """
    attr = _TYPE_ATTR.get(asset_type)
    if not attr or not asset_id:
        return False
    target = getattr(registry, attr)
    row = {'ID': asset_id, 'Path': path or ''}
    if asset_type == 'music':
        row['Loop'] = True
        row['Volume'] = 1.0
    if asset_type == 'transform':
        row['ATL'] = ''
        row['Params'] = ''
        row['Doc'] = ''
        row['Category'] = ''
    row['Notes'] = ''
    target[asset_id] = row

    if asset_dir is not None:
        # _STANDARD_TABLES 以 registry 属性名为键（如 'voices' 复数），
        # 而 asset_type 是单数（'voice'），必须用 attr 反查文件名，
        # 否则 voice 类型查不到文件名、静默跳过写回。
        fname = _STANDARD_TABLES.get(attr)
        if fname:
            p = Path(asset_dir) / fname
            try:
                if p.exists():
                    wb = openpyxl.load_workbook(p)
                    ws = wb.active
                    # 按文件真实表头对齐追加，避免列错位（老文件可能
                    # 只有 ID/Path/Notes，新文件才有 ATL/Params…）
                    headers = _sheet_headers(ws)
                    _asset_append_row(ws, [row.get(h, '') if h else '' for h in headers])
                else:
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    fields = _TABLE_FIELDS[asset_type]
                    # ⚠ 新建表也要写**中文表头**（与 `write_dict_to_excel` 一致）：
                    # 从前这里写英文 `fields`，于是"哪条路径建的表"决定了表头
                    # 是中是英 —— 同一批表里两种表头，用户看着像坏了。
                    ws.append([ASSET_FIELD_LABELS.get(f, f) for f in fields])
                    _asset_append_row(ws, [row.get(h, '') for h in fields])
                wb.save(p)
            except Exception as e:
                print(f"[asset_registry] 写入资源表失败: {e}")
    return True


def _update_row_cells(ws, row_no: int, headers: list, row: dict,
                      keys) -> int:
    """
    更新指定行的若干列（只写**值确实不同**的单元格），返回改写的单元格数。

    单独抽出来是因为它只做"受控更新"：调用方给**列白名单**，
    白名单之外的列（人写的分类、备注…）一个都不碰。
    """
    changed = 0
    for key in keys:
        if key not in headers:
            continue
        col = headers.index(key) + 1
        new = row.get(key, '')
        cell = ws.cell(row=row_no, column=col)
        if (cell.value or '') != (new or ''):
            _asset_set_text(ws, row_no, col, new)
            changed += 1
    return changed


def append_rows_to_table(asset_dir, table_key: str, rows: list,
                         update_keys: tuple = (), skip_paths=None,
                         backup: bool = False) -> int:
    """
    把若干行写进资源表：**已存在的 ID 默认跳过**，返回写入的行数。

    这是"程序敲代码 → 表格跟着变"的落地动作：脚本里新写的 transform /
    角色 / image 声明，要真的出现在 transforms.xlsx / characters.xlsx 里，
    文案才看得见、选得到。真实项目里曾经一个 transform 都存不下来
    （只进了内存注册表，从不落盘），就是这个环节缺失。

    策略默认"只追加、不覆盖"：人填的说明/分类是权威，工具不该改它。

    `update_keys` 是唯一的例外：给定时，对**已存在的 ID** 也更新这几列。
    适用场景是"这几列以脚本为权威"——典型是 transform 的
    `ATL` / `Params` / `Doc`：脚本改了定义，表里必须跟着变，否则
    "程序改代码 → 表格自动反映"就是空话，而且被写坏的旧值会永久固化
    （2026-09-28 实测）。其它列仍然不动。

    `skip_paths`（2026-10-05 新增）：**再按 `Path` 判一次重**。资源表要的是
    "**一个文件一行**"（一一对应），而 ID 是文件名派生的 —— 同一个文件完全
    可能已经以另一个 ID 登记过（`RETURE.png` 与 `Frame 26_RETURE.png` 实测
    就有）。只按 ID 判重会把它再写一遍。

    `backup=True`：写之前把原表复制成 `*.bak.xlsx`（**默认关** —— 脚本同步
    那条路调用频繁，每次都备份太浪费；"打开项目自动对齐"那条路会打开它）。

    返回 = 新增行数 + 被改写的单元格数。
    """
    table_key = str(table_key or '')
    fname = _table_file_name(table_key)
    if not fname or not rows:
        return 0
    asset_dir = Path(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    path = asset_dir / fname
    added = updated = 0
    try:
        if path.exists():
            wb = openpyxl.load_workbook(path)
            ws = wb.active
            headers = _sheet_headers(ws)
            if not headers or all(h is None for h in headers):
                headers = list(rows[0].keys())
                ws.append(headers)
            # ⚠ **老表没有这一列时，得先补列**（2026-10-10 实测踩到）：
            # `_update_row_cells` 遇到"表头里没有的键"是**静默跳过**的，
            # 于是"给真人项目加别名"整个动作**什么都没发生**（真机 `characters.xlsx`
            # 是 2026-10-10 之前建的，只有 5 列：ID/Name/Color/ImageTag/Notes，
            # 没有「别名」）。
            #
            # ⚠ 补列的规矩是**只追加在末尾、绝不挪动已有列**：
            #   ① 挪列会连带数据一起挪（我第一版只改表头 → 整行错位，`Notes`
            #      的备注跑到别列去）；
            #   ② 就算连数据一起挪也是坏主意：表里的列位置是**别人写好的**，
            #      按"模板顺序重排"等于替用户改表 —— 判据当场红
            #      （`test_it_never_overwrites_a_hand_edited_row` 按列号找回那一行）。
            #   ③ 也用不着 `insert_cols` 那种重家伙：追加到末尾最安全。
            for key in rows[0].keys():
                if key and key not in headers:
                    headers.append(key)
                    ws.cell(row=1, column=len(headers), value=key)
            row_of = {}
            id_col = headers.index('ID') + 1 if 'ID' in headers else 1
            for r in range(2, ws.max_row + 1):
                v = ws.cell(row=r, column=id_col).value
                if v:
                    row_of.setdefault(str(v), r)
            # 表里已经登记过的**路径**（`skip_paths` 之外再从表里现读一遍，
            # 免得调用方漏传）
            have_paths = set(skip_paths or ())
            if 'Path' in headers:
                pcol = headers.index('Path') + 1
                for r in range(2, ws.max_row + 1):
                    v = ws.cell(row=r, column=pcol).value
                    if v:
                        have_paths.add(norm_asset_path(v))
            for row in rows:
                aid = str(row.get('ID') or '')
                if not aid:
                    continue
                rp = norm_asset_path(row.get('Path'))
                if rp and rp in have_paths:
                    continue                    # 这个文件已经在表里了（一一对应）
                if aid in row_of:
                    # 已存在的行：默认跳过；给了 update_keys 才更新白名单列
                    if update_keys:
                        updated += _update_row_cells(
                            ws, row_of[aid], headers, row, update_keys)
                        continue
                    if not rp:
                        continue            # 没写 Path 的行：没有"另一个文件"
                    # ⚠ **ID 撞车 ≠ 同一个文件**（2026-10-05 实测）：
                    # `game/cc_preview/lan/angry.png` 生成的 ID 可能是
                    # `lan_angry`，而表里已有一行 `lan_angry` 指
                    # `game/images/characters/lan/angry.png`。老代码在这里
                    # **静默丢掉**这个文件 —— 于是它永远登记不上，体检里
                    # 永远显示"未登记 N 条"（用户看到的就是这个）。
                    # 同一个文件在前面已经按 Path 判重跳过了，走到这里
                    # 一定是**另一个文件** → 另起一个 ID 收下它。
                    base = aid
                    k = 2
                    while f'{base}_{k}' in row_of:
                        k += 1
                    aid = f'{base}_{k}'
                    row = dict(row, ID=aid)
                row_of[aid] = _asset_append_row(
                    ws, [row.get(h, '') if h else '' for h in headers])
                if rp:
                    have_paths.add(rp)
                added += 1
            if added or updated:
                if backup:
                    _backup_table(path)
                wb.save(path)
        else:
            wb = openpyxl.Workbook()
            ws = wb.active
            headers = list(rows[0].keys())
            ws.append(headers)
            seen = set(skip_paths or ())
            for row in rows:
                rp = norm_asset_path(row.get('Path'))
                if rp and rp in seen:
                    continue
                _asset_append_row(ws, [row.get(h, '') for h in headers])
                if rp:
                    seen.add(rp)
                added += 1
            wb.save(path)
    except Exception as e:                      # noqa: BLE001
        print(f"[asset_registry] 写入 {fname} 失败: {e}")
        return 0
    return added + updated


def norm_asset_path(value) -> str:
    """资源表里的 Path → 判重用的规范形式（斜杠统一、去首尾空白、小写）。"""
    s = str(value or '').strip().replace('\\', '/')
    while s.startswith('./'):
        s = s[2:]
    return s.lower()


#: 「这一行是**人工占位**」的标记词（写在 `Notes` 或 `Doc` 里，大小写不限）。
#:
#: 用户 2026-10-05：
#:
#> 表里有但实际没有的资源也不能用啊？？？（除非人工把它录入占位，这个允许）
#:
#: 所以缺失文件的行分两种：
#:   · **没有**占位标记 → 认定"资源被删了" → 该行**不可用**（不出现在候选、
#:     不给解析、编译时按缺资源处理）；工具自己加的行还会被自动移除；
#:   · **有**占位标记 → 用户明知文件还没有/不要了，留着占位 →
#:     仍然**可用**（能选、能写进卡片），只是编译时照旧按"缺资源"提示。


#: 兼容内部旧写法（本模块里以前叫 _norm_asset_path）。
_norm_asset_path = norm_asset_path


def is_placeholder_row(row) -> bool:
    """这一行是用户**手工录入的占位**吗（`Notes` / `Doc` 里有标记词）？"""
    row = row or {}
    text = ' '.join(str(row.get(k) or '') for k in ('Notes', 'Doc', '说明'))
    low = text.lower()
    return any(mark in low for mark in _PLACEHOLDER_MARKS)


def _row_file_exists(kind: str, row, project_root=None, walk=None) -> bool:
    """这一行的 `Path` 在磁盘上真的存在吗（`kind` 是 `image` / `music` …）？"""
    rel = str((row or {}).get('Path') or '').strip()
    if not rel:
        return False
    try:
        if walk is not None:
            return walk.match(rel) is not None
        if kind == 'image':
            from storygraph.story.excel_export import resolve_thumb_source
            return resolve_thumb_source(rel, project_root) is not None
        return _resolve_audio_source(rel, project_root) is not None
    except Exception as e:                      # noqa: BLE001 - 判不了就当存在
        swallow('assets.asset_registry._row_file_exists', e)
        return True


def _backup_table(path: Path) -> str:
    """把资源表备份成 `*.bak.xlsx`（返回备份路径；失败返回 ''）。"""
    try:
        import shutil
        bak = Path(path).with_suffix('.bak.xlsx')
        shutil.copyfile(path, bak)
        return str(bak)
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_registry._backup_table', e)
        return ''


def parse_asset_tables(asset_dir, hooks=None, project_root=None,
                       walk=None) -> AssetRegistry:
    """
    解析资产表目录，构建 AssetRegistry。

    asset_dir:    存放 xlsx 的目录
    hooks:        load_hooks 的返回值
    project_root: 项目根目录；None 则默认 asset_dir.parent
    """
    asset_dir = Path(asset_dir)
    registry = AssetRegistry()
    if hooks is None:
        hooks = {}
    if project_root is None:
        project_root = asset_dir.parent

    pre_hook = hooks.get('pre_process_row')

    # ---------- 标准表 ----------
    for key, filename in _STANDARD_TABLES.items():
        file = asset_dir / filename
        if not file.exists():
            continue
        rows = read_excel_rows(file)
        for row in rows:
            if pre_hook is not None:
                try:
                    row = pre_hook(key, row)
                except Exception as e:
                    registry.warnings.append(
                        f"{filename}: pre_process_row 钩子异常 {e}")
            asset_id = row.get('ID')
            if not asset_id:
                registry.errors.append(f"{filename}: 缺少ID字段")
                continue
            target_dict = getattr(registry, key)
            if asset_id in target_dict:
                registry.errors.append(f"{filename}: ID '{asset_id}' 重复")
            target_dict[asset_id] = row
            custom_fields = {k: v for k, v in row.items()
                             if k.startswith('Custom_')}
            if custom_fields:
                row['_custom'] = custom_fields

    # ---------- 自定义表 ----------
    for file in asset_dir.glob('custom_*.xlsx'):
        table_name = file.stem.replace('custom_', '')
        rows = read_excel_rows(file)
        custom_dict = {}
        for row in rows:
            if 'ID' not in row:
                registry.warnings.append(f"{file.name}: 无ID列，跳过该行")
                continue
            custom_dict[row['ID']] = row
        registry.custom[table_name] = custom_dict

    # ---------- 后处理钩子 ----------
    post_hook = hooks.get('post_process_registry')
    if post_hook is not None:
        try:
            post_hook(registry)
        except Exception as e:
            registry.warnings.append(f"post_process_registry 钩子异常 {e}")

    validate_registry(registry, project_root, walk=walk)
    # 记住来路：资源图库要按 Path 去磁盘上取缩略图，得知道基准目录。
    registry.asset_dir = asset_dir
    registry.project_root = project_root
    return registry


# ===================== 校验 =====================





def _mark_unusable(registry, project_root, walk=None):
    """
    标出"表里有、磁盘上却没有"的行 → `registry.unusable`。

    用户 2026-10-05：

    > 我认为表应当实时反应实际资源的情况，自动更新和增删！
    > 表里有但实际没有的资源也不能用啊？？？
    > （除非人工把它录入占位，这个允许）

    判据：`Path` 解析不到真实文件，**且**这一行没有人工占位标记
    （`is_placeholder_row`）。被标的行**留在表里**（对账、体检要看得见），
    但 `AssetIndex` 建索引时会跳过 → 候选下拉没有、引用解析不到、
    编译按缺资源处理（"不能用"）。
    """
    for kind, attr in (('image', 'images'), ('music', 'music'),
                       ('sound', 'sounds'), ('voice', 'voices')):
        for aid, row in (getattr(registry, attr, {}) or {}).items():
            if is_placeholder_row(row):
                continue                        # 人工占位：允许没有文件
            if _row_file_exists(kind, row, project_root, walk=walk):
                continue
            registry.unusable[attr][aid] = \
                f"文件不存在：{(row or {}).get('Path') or '（没写 Path）'}"


def validate_registry(registry: AssetRegistry, project_root, walk=None):
    """检查文件存在性、依赖引用、颜色格式等。"""
    project_root = Path(project_root)
    _mark_unusable(registry, project_root, walk=walk)

    for img_id, data in registry.images.items():
        p = data.get('Path')
        if p and not _row_file_exists('image', data, project_root, walk=walk):
            registry.warnings.append(f"图片 '{img_id}' 文件不存在: {p}")

    for type_key in ('music', 'sounds', 'voices'):
        for aid, data in getattr(registry, type_key).items():
            p = data.get('Path')
            if p and not _row_file_exists(type_key.rstrip('s'), data,
                                          project_root, walk=walk):
                registry.warnings.append(f"{type_key} '{aid}' 文件不存在: {p}")

    image_tags = {d.get('Tag') for d in registry.images.values() if d.get('Tag')}
    for char_id, data in registry.characters.items():
        if data.get('ImageTag') and data['ImageTag'] not in image_tags:
            registry.warnings.append(
                f"角色 '{char_id}' 的 ImageTag '{data['ImageTag']}' "
                f"未找到对应图片标签")
        if data.get('DefaultTransform') and \
                data['DefaultTransform'] not in registry.transforms:
            registry.warnings.append(
                f"角色 '{char_id}' 的 DefaultTransform "
                f"'{data['DefaultTransform']}' 未定义")

    for char_id, data in registry.characters.items():
        color = data.get('Color')
        if color and not _COLOR_PATTERN.match(str(color)):
            registry.warnings.append(
                f"角色 '{char_id}' 的颜色 '{color}' 格式无效")


# ===================== 脚本引用分析 =====================

# Ren'Py 自带的变换 / 转场 / 位置白名单：已收敛到
# `storygraph.core.vocabulary.RENPY_BUILTIN_TRANSFORMS`，顶部导入后
# 在本模块继续对外可见（validate / script_table / 测试都从这里取，
# 不破坏既有 import 面）。

# show / scene / hide 之后用来切分"图像名"与关键字的分界符

# voice 两种写法：字符串路径（voice "voice/a.ogg"）或裸变量/ID（voice v_001）。
# 之前这条正则路径完全没收 voice，导致语音资产永远显示"未使用"。


def is_dynamic_image_spec(spec: str) -> bool:
    """
    这个"图像名"是运行时才确定的表达式吗？

    Ren'Py 允许 `show expression Text("...") as x`、`scene expression
    holo("lan base")`，以及带 ATL 的 `image icon:` 块头。
    这些都不是静态资产引用，拿去查资源表必然查不到——以前会把它们
    报成"未登记"（真实项目里就是这几条）。
    """
    s = str(spec or '').strip()
    if not s:
        return True
    if s.startswith('expression') or s.startswith('at '):
        return True
    if s.endswith(':'):
        return True
    if '(' in s or ')' in s:
        return True
    return False


def is_dynamic_transform_spec(name: str) -> bool:
    """`with Dissolve(1.5)` 这种函数式转场不是 transform ID。"""
    s = str(name or '').strip()
    return bool(s) and ('(' in s or ')' in s or s.startswith('expression'))


def _image_spec_candidates(spec: str) -> list:
    """
    把一个脚本里的图像名展开成"可能在资产表里叫什么的候选列表"，
    按确定性从高到低排列。

    Ren'Py 的 `show bg room` 与文件 `bg_room.png` 是同一个东西，
    所以空格形式和下划线形式都要试；只有 tag（`show bg`）时退化为
    最宽泛的匹配，交给 resolve_reference 兜底。
    """
    if not spec:
        return []
    parts = spec.split()
    candidates = []
    # 完整名（下划线形式优先级最高：bg room -> bg_room）
    if len(parts) > 1:
        candidates.append('_'.join(parts))
    candidates.append(spec)
    # 逐段递减的后缀：bg room night -> bg_room -> bg
    for i in range(len(parts) - 1, 0, -1):
        candidates.append('_'.join(parts[:i]))
    # 去重保序
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _extract_image_spec(rest: str) -> str:
    """
    从 show/scene/hide 的剩余部分里切出图像名，遇到关键字即停。

    ⚠⚠ 2026-10-16（用户排期 C 项）：**切分本体交给
    `core.vocabulary.classify_show_words`** —— 从前这里自己写了一遍
    "遇到 `_SHOW_STOP_WORDS` 就 break"的循环（还要自己处理大小写与结尾逗号），
    与 `beat_stage_parse` / `excel_export` / `renpy_parser` / `learn_statement`
    各写一遍是同一个口径的六份拷贝。

    返回值语义与从前逐字一致：**普通图像名才返回**；
    `show screen X` / `show layer Y` / `show expression …` 这类**没有图像名**
    （引入型关键字自己就是目标）→ 返回 `''`，调用方据此跳过。

    ⚠ 行尾注释先剥掉：`scene white with Dissolve(1.5) #白色转场`（真机
    `screens.rpy:1448`）在**旧循环**里是"注释恰好排在 `with` 后面"才没进
    图像名的；换成 tokenizer 之后注释会黏在最后一个词后面 —— 不剥就会
    把 `#白色转场` 当成名字的一部分。这是"取代之后仍然逐字一致"必须补的一步。
    """
    text = str(rest or '')
    hash_at = text.find('#')
    if hash_at >= 0:
        text = text[:hash_at]
    roles = classify_show_words(text)
    return '' if roles.intro_kind != 'image' else roles.target


def _normalize_spec(s) -> str:
    """
    归一化一个"图像名"用于比较。

    Ren'Py 里 `show eileen happy`、资产 ID `eileen_happy`、文件名
    `eileen-happy.png` 是同一个东西，所以下划线、连字符统一当空格，大小写无关。
    """
    s = str(s or '')
    for ch in ('_', '-'):
        s = s.replace(ch, ' ')
    return ' '.join(s.split()).lower()


def image_alias_index(registry: AssetRegistry) -> dict:
    """
    构建「图片别名 → 资产 ID 列表」映射，这是 P0 的核心。

    旧实现的错在于：资产 ID 取的是"文件名"，而脚本里写的是
    `tag + attribute`（`show eileen happy`），两者形状根本不同，
    于是 `eileen/` 目录下几十张表情图全部被误报成"未使用资产"。

    这里给每张图显式算出它可能被怎么称呼：
      ID、Tag、Attributes、`Tag Attributes`、以及去掉 tag 前缀的 Attributes。
    """
    index = defaultdict(list)
    for aid, row in (registry.images or {}).items():
        for alias in image_aliases(aid, row):
            if alias and aid not in index[alias]:
                index[alias].append(aid)
    return index


# 扫描资产时会给音频自动加的前缀。脚本里通常不带这些前缀
# （`play music night`），所以解析时要能剥掉。


def audio_aliases(aid: str, row: dict) -> set:
    """
    一条音频资产的称呼集合（已归一化）。

    `bgm_night` 的别名 = {bgm night, night, rain…}；文件路径
    `music/rain.ogg` 的 stem 也算——扫描器加前缀是为了 ID 规范，
    不该让"脚本写的名字"和"表里的 ID"因此对不上。
    """
    out = set()
    if aid:
        out.add(_normalize_spec(aid))

    def _add_variants(name):
        if not name:
            return
        out.add(_normalize_spec(name))
        low = name.lower()
        for p in _AUDIO_ID_PREFIXES:
            if low.startswith(p) and len(name) > len(p):
                out.add(_normalize_spec(name[len(p):]))

    _add_variants(aid)
    path = row.get('Path')
    if path:
        _add_variants(Path(str(path)).stem)
    _add_variants(row.get('Name'))
    return {a for a in out if a}


def image_aliases(aid: str, row: dict) -> set:
    """单张图片的全部称呼（已归一化）。"""
    out = set()
    if aid:
        out.add(_normalize_spec(aid))

    tag = _normalize_spec(row.get('Tag'))
    attrs = _normalize_spec(row.get('Attributes'))
    name = _normalize_spec(row.get('Name'))

    if tag:
        out.add(tag)
    if attrs:
        out.add(attrs)
    if name:
        out.add(name)
    if tag and attrs:
        out.add(f'{tag} {attrs}')
        # Attributes 自带 tag 前缀时（eileen_happy），再去掉一层前缀
        if attrs.startswith(tag + ' '):
            out.add(attrs[len(tag) + 1:])
    return {a for a in out if a}


class AssetIndex:
    """
    一次构建、重复使用的**解析索引**。

    为什么必须有它：解析一个名字原本要遍历所有资产并现场算别名，
    于是"校验 N 条引用 × M 个资产"变成 O(N×M)。真实项目 2828 张图 ×
    4058 次解析 = 1800 万次别名计算，一次体检要 268 秒，UI 直接假死。
    索引建一次（O(M)），之后每次解析是哈希查找 + 二分前缀，O(log M)。

    索引是**快照**：资源表变了要重新 build（引擎/校验都在每轮开始时重建）。
    """

    def __init__(self, registry: AssetRegistry):
        self.ids = {}          # 原始 ID（全类型）
        self.id_tag = {}       # 图片 ID -> 归一化 Tag（'' = 这行没 Tag）
        self.audio = {}        # 音频别名（归一化）-> [id]
        self.image = {}        # 图片别名（归一化）-> [(id, is_loose)]
        self.prefix = []       # 排序后的 (别名, id)，用于前缀匹配（排除纯 tag）

        mapping = (('images', 'image'), ('music', 'music'),
                   ('sounds', 'sound'), ('voices', 'voice'),
                   ('transforms', 'transform'),
                   ('characters', 'character'))
        #: **不可用的行不建索引** —— 表里有、文件不在了的那些
        #: （用户 2026-10-05："表里有但实际没有的资源也不能用"）。
        #: 这是唯一的收口点：候选、校验、编译的引用解析全走这里。
        unusable = getattr(registry, 'unusable', None) or {}
        for attr, _kind in mapping:
            bad = set((unusable.get(attr) or {}).keys())
            for aid in (getattr(registry, attr, {}) or {}):
                if aid in bad:
                    continue
                self.ids[aid] = True

        for attr in ('music', 'sounds', 'voices'):
            bad = set((unusable.get(attr) or {}).keys())
            for aid, row in (getattr(registry, attr, {}) or {}).items():
                if aid in bad:
                    continue
                for alias in audio_aliases(aid, row or {}):
                    self.audio.setdefault(alias, [])
                    if aid not in self.audio[alias]:
                        self.audio[alias].append(aid)

        prefix_buf = set()
        bad_images = set((unusable.get('images') or {}).keys())
        for aid, row in (registry.images or {}).items():
            if aid in bad_images:
                continue                            # 文件没了 → 当它不存在
            row = row or {}
            tag = _normalize_spec(row.get('Tag'))
            attrs = _normalize_spec(row.get('Attributes'))
            self.id_tag[aid] = tag
            for alias in image_aliases(aid, row):
                loose = bool(alias) and alias == tag and attrs != alias
                self.image.setdefault(alias, [])
                entry = (aid, loose)
                if entry not in self.image[alias]:
                    self.image[alias].append(entry)
                if alias and not loose:
                    prefix_buf.add((alias, aid))
        self.prefix = sorted(prefix_buf)

    # ---------- 查询 ----------

    def _prefer_tag_attribute(self, cand: str):
        """
        `ling_angry` 这种**下划线写法**：如果它更像是 `tag attribute`，
        而精确命中的那一行又没有 Tag → 返回**带 Tag 的那一行**的 ID。

        只在这两个条件同时成立时才改判（保守）：
          · 精确 ID 那行的 Tag 为空（不是立绘/背景那一类有 tag 的资源）；
          · `tag + attribute` 能命中**带 Tag** 的行。
        其它情况一律返回 None（保持"精确 ID 优先"的老行为）。
        """
        if '_' not in cand or ' ' in cand:
            return None
        if self.id_tag.get(cand):
            return None                      # 精确那行自己有 Tag → 别抢
        low = cand.lower()
        for i in range(len(low) - 1, 0, -1):
            if low[i] != '_':
                continue
            tag, attr = low[:i], low[i + 1:]
            if not tag or not attr:
                continue
            for aid, is_loose in self.image.get(f'{tag} {attr}', ()):
                if is_loose:
                    continue
                if self.id_tag.get(aid):
                    return aid               # 带 Tag → 这才是真立绘
        return None

    def resolve(self, candidate: str) -> list:
        if not candidate:
            return []
        cand = str(candidate).strip()
        if not cand:
            return []
        if cand in self.ids:
            # ⚠ **别让"没有 Tag 的行"劫持 `tag_attribute` 写法**（2026-10-05
            # 用户实测："改一个表……立绘不显示，而且表情也调不了了"）。
            #
            # 卡片里存的是 `ling_angry`（工具自己的"空格→下划线"写法），
            # 而表里可能正好有一行 ID 就叫 `ling_angry` —— 它来自**另一份
            # 副本**（`sr_general/game/cc_preview/ling/angry.png`），Tag 是空的。
            # 精确 ID 命中排在最前面，于是这个"同名但不是立绘"的行赢了：
            #   · 预览按它的 Path 取图（`cc_preview` 里的立绘特写，或取不到）
            #   · 表情/位置候选按它的 Tag（空）算 → **一个都没有**
            # 修法：当下划线写法能解释成 `tag attribute` 且能命中**带 Tag 的
            # 行**、而精确 ID 那行**没有 Tag** 时，用前者。
            better = self._prefer_tag_attribute(cand)
            if better is not None:
                return [better]
            return [cand]

        # 脚本里音频经常写成"路径"（`play music "bgm/bgm02.mp3"`），
        # 表里存的却是 ID（`bgm_bgm02`）。所以路径要逐级退化成
        # 去目录名 → 去扩展名 再试，否则整章音乐全被报"未登记"。
        for norm in _spec_variants(cand):
            if not norm:
                continue
            audio = self.audio.get(norm)
            if audio:
                return list(audio)

            hits, loose = [], []
            for aid, is_loose in self.image.get(norm, ()):
                (loose if is_loose else hits).append(aid)
            if hits:
                return hits
            if loose:
                # 只写 tag（`scene bg`）时认一张代表，否则"未使用资产"恒为 0
                return loose[:1]

            found = self._prefix_ids(norm)
            if found:
                return found
        return []

    def _prefix_ids(self, norm: str) -> list:
        """前缀匹配（Ren'Py 的 `show tag attr` 语义），二分定位起止。"""
        import bisect
        start = bisect.bisect_left(self.prefix, (norm + ' ', ''))
        out = []
        for i in range(start, len(self.prefix)):
            alias, aid = self.prefix[i]
            if not alias.startswith(norm + ' '):
                break
            if aid not in out:
                out.append(aid)
        return out


def _spec_variants(name: str) -> list:
    """
    一个引用的可能写法（按精确度递减）：
      原始归一 → 去掉目录部分 → 去掉扩展名
    """
    s = str(name or '').replace('\\', '/').strip()
    out = []
    for v in (s, s.rsplit('/', 1)[-1]):
        n = _normalize_spec(v)
        if n and n not in out:
            out.append(n)
        if '.' in v and not v.startswith('.'):
            n2 = _normalize_spec(v.rsplit('.', 1)[0])
            if n2 and n2 not in out:
                out.append(n2)
    return out


def resolve_references(candidate: str, registry: AssetRegistry = None,
                       index: 'AssetIndex' = None) -> list:
    """
    把一个脚本里出现的名字解析成**一组**资产 ID。

    匹配顺序：全类型精确 ID → 音频别名（剥 bgm_/sfx_ 前缀）→ 图片别名
    → 图片前缀（`show eileen happy` 覆盖 `eileen happy *` 变体）。

    循环里调用请传 index（否则每次都会重建索引，真实项目上是几百倍差距）。
    """
    if not candidate:
        return []
    if index is None:
        if registry is None:
            return []
        index = AssetIndex(registry)
    return index.resolve(candidate)


def resolve_reference(candidate: str, registry: AssetRegistry = None,
                      index: 'AssetIndex' = None) -> Optional[str]:
    """单值版本：解析不了返回 None。需要覆盖整组变体时用 resolve_references。"""
    ids = resolve_references(candidate, registry, index)
    return ids[0] if ids else None


def extract_script_references(text: str) -> list:
    """
    抽取一段脚本里的资产引用。

    返回 [(kind, raw_name, line_no, candidates), ...]：
      kind ∈ {'image', 'music', 'sound', 'transform'}
      raw_name   用于报告的名字（图像就是完整 spec）
      candidates 这个名字"可能在资产表里叫什么"的候选，按确定性排序

    抽取阶段只做语法识别，不做资产表解析——后者交给
    resolve_reference，避免这里硬编码表的形状。

    注意候选是**整组**的：`show bg room` 会展开成
    ('bg_room', 'bg room', 'bg')，只要有一个能对上就算引用成立；
    若逐个独立判定，命中之外的候选都会被误报成断裂引用。
    """
    refs = []

    def line_of(pos):
        return text[:pos].count('\n') + 1

    for m in _RE_SHOW_LINE.finditer(text):
        spec = _extract_image_spec(m.group('rest'))
        if not spec or spec.lower() == 'expression':
            continue
        # ⚠⚠ **非图像目标**（`screen` / `layer` / `movie` / `text`…）不是本项目的
        #   图像资产（2026-10-15 接线：`NON_ACTOR_SHOW_TARGETS` 在 `core.vocabulary`
        #   里**定义了、导出了、却从没有任何地方用过** —— "概念有了、线没接"）。
        #   不挡的话 `show screen msg_dialogue` 会变成一条**假断链**；
        #   真机实测这条路产出 110 条，而它是**无条件跟跑**的
        #   （Statement 优先 + 正则补覆盖不到的地方），所以那是真缺陷。
        if spec.split()[0].lower() in NON_ACTOR_SHOW_TARGETS:
            continue
        clean = spec.replace('"', '')
        # 运行时表达式 / 带 ATL 的块头 / 函数调用 → 不是静态资产引用
        if is_dynamic_image_spec(clean):
            continue
        cands = _image_spec_candidates(clean)
        refs.append(('image', clean, line_of(m.start()), tuple(cands)))

    for m in _RE_AUDIO_LINE.finditer(text):
        name = m.group('name')
        refs.append((m.group('channel'), name, line_of(m.start()), (name,)))

    for m in _RE_VOICE_LINE.finditer(text):
        name = m.group('q') or m.group('w')
        if name:
            refs.append(('voice', name, line_of(m.start()), (name,)))

    for m in _RE_AT_TRANSFORM.finditer(text):
        name = m.group('name')
        if name in RENPY_BUILTIN_TRANSFORMS:
            continue
        refs.append(('transform', name, line_of(m.start()), (name,)))

    for m in _RE_WITH_TRANSITION.finditer(text):
        name = m.group('name')
        if name in RENPY_BUILTIN_TRANSFORMS:
            continue
        refs.append(('transform', name, line_of(m.start()), (name,)))

    return refs


def references_from_statements(statements) -> list:
    """
    从 core.Statement 流抽取资产引用 —— **统一真源**（一.3）。

    返回 [(kind, raw_name, candidates, loc), ...]，与 extract_script_references
    同构，只是数据来源从"正则扫文本"升级为"读已解析的语句"。

    为什么要收敛到这里：以前有三套规则在跑
      · asset_registry.extract_script_references（正则扫原文）
      · asset_extensions.DependencyAnalyzer（读 registry.references）
      · script_table.find_unregistered_assets（自己归一 + 白名单）
    同一句 `show eileen happy` 在三处可能得出三种结论。现在抽取统一
    走 `_image_spec_candidates`，判定统一走 `resolve_references`，
    只保留"文本正则"这一条快速兜底路径（拿不到 Statement 时才用）。
    """
    from storygraph.core.statement import StatementKind

    refs = []

    def loc_of(st):
        src = getattr(st, 'source', None) or ('', 0)
        fname = Path(str(src[0])).name if src[0] else ''
        return f"{fname}:{src[1]}" if fname else ''

    for st in statements or ():
        kind = getattr(st, 'kind', None)
        payload = getattr(st, 'payload', None) or {}

        if kind in (StatementKind.SHOW, StatementKind.SCENE,
                    StatementKind.HIDE):
            spec = (payload.get('image') or '').strip()
            loc = loc_of(st)
            is_expr = bool(payload.get('is_expression'))
            # ⚠⚠ **非图像目标不是本项目的图像资产**（2026-10-15 接线）：
            #   `show layer master` / `show text "…"` 会走到这一支
            #   （`show screen` 更早被 `_stmt_screen_op` 拦成 `raw`）。
            #   实测它们会变成 `unresolved['image']` 里的假断链。
            #   ⚠ 只挡**图像引用**那一条 —— 后面的 `at` / `with` 变换引用照收。
            if (spec and not is_expr and not is_dynamic_image_spec(spec)
                    and spec.split()[0].lower() not in NON_ACTOR_SHOW_TARGETS):
                clean = spec.replace('"', '')
                refs.append(('image', clean,
                             tuple(_image_spec_candidates(clean)), loc))
            # show 子句里的 `at xxx` / `with xxx` 也是变换引用
            for t in (payload.get('at') or []):
                t = str(t).strip()
                if t and t not in RENPY_BUILTIN_TRANSFORMS \
                        and not is_dynamic_transform_spec(t):
                    refs.append(('transform', t, (t,), loc))
            with_ = (payload.get('with_') or '').strip()
            if with_ and with_ not in RENPY_BUILTIN_TRANSFORMS \
                    and not is_dynamic_transform_spec(with_):
                refs.append(('transform', with_, (with_,), loc))

        elif kind == StatementKind.PLAY:
            channel = (payload.get('channel') or '').strip()
            name = (payload.get('name') or '').strip()
            if name:
                refs.append(('music' if channel == 'music' else 'sound',
                             name, (name,), loc_of(st)))

        elif kind == StatementKind.VOICE:
            name = (payload.get('name') or '').strip()
            if name:
                refs.append(('voice', name, (name,), loc_of(st)))

    return refs


def analyze_statement_references(statements, registry: AssetRegistry):
    """
    用 Statement 流填充 registry.references / unresolved。

    与 analyze_references（正则版）等价，但更准：它读的是已解析的结构，
    不会被字符串里碰巧出现的 `show` 骗到。拿不到 ParseResult 时才退回
    analyze_references。
    """
    if not isinstance(registry.references, defaultdict):
        registry.references = defaultdict(list, registry.references or {})
    if not isinstance(registry.unresolved, defaultdict):
        registry.unresolved = defaultdict(list, registry.unresolved or {})

    index = AssetIndex(registry)          # 建一次，循环里复用
    for kind, raw, cands, loc in references_from_statements(statements):
        asset_ids = []
        for cand in cands:
            asset_ids = resolve_references(cand, registry, index)
            if asset_ids:
                break
        if asset_ids:
            for aid in asset_ids:
                if loc and loc not in registry.references[aid]:
                    registry.references[aid].append(loc)
        else:
            bucket = registry.unresolved[kind]
            if (raw, loc) not in bucket:
                bucket.append((raw, loc))
    return registry


def analyze_references(script_files, registry: AssetRegistry):
    """
    扫描 .rpy 脚本中的资产引用，填充 registry.references。

    registry.references: {asset_id: ['file.rpy:line', ...]}
    只有解析到具体资产的才记入；解析不到的留在
    registry.unresolved（供"断裂引用"诊断，可按需忽略某些类型）。

    之前的实现只取 `show` 后的第一个词（tag），而资产 ID 是完整文件名，
    两边对不上，导致整库图片被判成"未使用"。
    """
    if registry.references is None:
        registry.references = defaultdict(list)
    elif not isinstance(registry.references, defaultdict):
        converted = defaultdict(list)
        for k, v in registry.references.items():
            converted[k] = list(v)
        registry.references = converted

    if not hasattr(registry, 'unresolved') or registry.unresolved is None:
        registry.unresolved = defaultdict(list)

    index = AssetIndex(registry)          # 建一次，循环里复用
    for fp in script_files:
        fp = Path(fp)
        if not fp.exists():
            continue
        try:
            text = fp.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            swallow('assets.asset_registry.analyze_references', e)
            continue

        for kind, raw, line_no, cands in extract_script_references(text):
            loc = f"{fp.name}:{line_no}"

            # 候选整组判定：用一个候选解析（可能命中多张图，如立绘的表情集）
            asset_ids = []
            for cand in cands:
                asset_ids = resolve_references(cand, registry, index)
                if asset_ids:
                    break

            if asset_ids:
                for aid in asset_ids:
                    if loc not in registry.references[aid]:
                        registry.references[aid].append(loc)
            else:
                bucket = registry.unresolved[kind]
                if (raw, loc) not in bucket:
                    bucket.append((raw, loc))


def describe_asset(asset_type: str, row: dict) -> str:
    """
    给人看的一句话说明（用于候选表的"说明"列、应用内选择器的详情面板）。

    目标：文案在 Excel 里点开下拉、或在应用里选中一条资产时，能立刻
    知道"这是什么"，而不是只看到一个可能是缩写的 ID。
    """
    row = row or {}

    def g(key):
        v = row.get(key)
        return '' if v is None else str(v).strip()

    if asset_type == 'character':
        parts = [g('Name') or g('ID')]
        if g('Color'):
            parts.append(g('Color'))
        if g('ImageTag'):
            parts.append(f"立绘 {g('ImageTag')}")
        if g('VoiceTag'):
            parts.append(f"语音 {g('VoiceTag')}")
        if g('Kind'):
            parts.append(f"kind={g('Kind')}")
        cnt = row.get('_dialogue_count')
        if isinstance(cnt, int) and cnt:
            parts.append(f'台词 {cnt}')
        note = g('Notes')
        if note and ':' in note and '\\' in note:
            parts.append('定义于 ' + note.split('\\')[-1])
        return ' · '.join(p for p in parts if p)

    if asset_type == 'image':
        # 面向文案：类目给中文，别让"other"这种内部枚举出现在表格里
        category_labels = {
            'background': '背景', 'cg': 'CG', 'ui': '界面',
            'character': '立绘', 'other': '其他',
        }
        parts = []
        if g('Category'):
            parts.append(category_labels.get(g('Category'), g('Category')))
        if g('Tag'):
            parts.append(f"tag={g('Tag')}")
        if g('Attributes'):
            parts.append(f"属性={g('Attributes')}")
        if g('Path'):
            parts.append(g('Path'))
        return ' · '.join(parts)

    if asset_type == 'transform':
        # 说明 > ATL 首行 > 兜底。文案最需要的是那句话。
        parts = []
        doc = g('Doc') or g('Notes')
        if doc and ':' in doc and ('\\' in doc or '/' in doc) and len(doc) < 80:
            doc = ''
        if doc:
            parts.append(doc.replace('\n', ' '))
        if g('Params'):
            parts.append(f"参数 {g('Params')}")
        if g('Category'):
            parts.append(g('Category'))
        if not parts:
            for ln in (g('ATL') or '').splitlines():
                s = ln.strip()
                if s and not s.startswith('#'):
                    parts.append(s)
                    break
        if g('ATL'):
            parts.append(f"ATL {len([l for l in g('ATL').splitlines() if l.strip()])} 行")
        return ' · '.join(parts)

    # music / sound / voice
    parts = []
    if g('Path'):
        parts.append(g('Path'))
    if g('Loop'):
        parts.append('循环')
    if g('Volume'):
        parts.append(f"音量 {g('Volume')}")
    return ' · '.join(parts)


# ===================== 引用体检（纯函数：只看 registry） =====================
#
# 这两个查询原先只长在 `AssetEngine` 上（`find_unused_assets` /
# `find_broken_references`），于是**只有"有一个真引擎"的地方能用** ——
# Qt 版的会话手上只有 registry，而 `app.asset_panel` 又要求传入的对象带这
# 两个方法，结果"未使用资产 / 断裂引用"这类体检在编辑器里一直没有入口。
# 实现只依赖 `registry.references` / `registry.unresolved`，所以下移到资源表
# 层：引擎与适配器都转发到这里（一份实现，两处调用）。

def unused_assets(registry) -> list:
    """没人引用的资产：`[{'category': 'images', 'asset_id': …}, …]`。"""
    if registry is None:
        return []
    referenced = set(getattr(registry, 'references', {}) or {})
    out = []
    for name in ('images', 'music', 'sounds', 'transforms'):
        for aid in (getattr(registry, name, None) or {}):
            if aid not in referenced:
                out.append({'category': name, 'asset_id': aid})
    return out


def broken_references(registry, include_transforms: bool = False) -> list:
    """
    脚本引用了、但资产表里查不到的名字。

    transform 默认**不算**断裂：脚本里 `at my_slide` 用的自定义变换，用户
    完全可以不登记到 transforms.xlsx（那里只放要复用和管理的），把它报成
    "断裂引用"只会制造噪音。
    """
    if registry is None:
        return []
    unresolved = getattr(registry, 'unresolved', {}) or {}
    grouped = defaultdict(list)
    for kind, entries in unresolved.items():
        if kind == 'transform' and not include_transforms:
            continue
        for raw, loc in (entries or ()):
            grouped[(kind, raw)].append(loc)
    out = [{'asset_id': raw, 'kind': kind, 'locations': list(locs)}
           for (kind, raw), locs in grouped.items()]
    out.sort(key=lambda b: (b['kind'], b['asset_id']))
    return out


# ===================== 补登缺失资源（合并写，绝不重写整表） =====================

def _norm_path_for_compare(path) -> str:
    """比对用路径：统一分隔符、去空格、小写（表里实测有 `bg_2 .png` 这种手误）。"""
    s = str(path or '').replace('\\', '/').strip().lower()
    return re.sub(r'\s+', '', s)


def register_missing_assets(project_root, asset_dir=None,
                            include_ui: bool = False) -> dict:
    """
    把**磁盘上有、资产表里没登记**的图片（以及音乐/音效/语音）**追加**进表。

    用户 2026-10-05：

    > 先查背景图为何没有被登记进去，然后，让背景图被登记进去。

    实测 `sr_general`：`game/images/bg` 下有 39 张，表里只认到 30 张；
    表里用下划线名（`bg_1`）而磁盘是点号名（`bg1.2.png`）—— 于是脚本里的
    `bg bg1_2` 在表里查不到，演出预览的背景只能靠磁盘回退兜着。

    为什么不能直接用 `write_asset_excel`：它是"**只写扫描结果**"的全量重写
    （2828 行会掉到 349 行）。这里改成**只追加缺失行**：
      · 已登记（路径或 ID 命中，路径比对会去掉空格/大小写差异）→ 跳过；
      · 其余 → 追加到对应表的末尾，并在写之前把原表备份成 `*.bak.xlsx`。

    图片的 `Attributes` 会做一次 `.` → `_` 归一（Ren'Py 里
    `image bg bg6_2 = "images/bg/bg6.2.png"` 就是这种对应关系），
    这样脚本里写的 `bg bg6_2` 才能在表里查到。
    返回 `{ok, added, skipped, backup, by_kind, rows}`。
    """
    project_root = Path(project_root) if project_root else None
    if project_root is None:
        return {'ok': False, 'error': '没有项目根目录', 'added': 0,
                'skipped': 0, 'backup': '', 'by_kind': {}, 'rows': []}
    tables_dir = Path(asset_dir) if asset_dir else \
        (project_root / 'game' / 'asset_tables')
    game = project_root / 'game'
    scan_root = game if game.is_dir() else project_root
    try:
        assets = scan_assets(scan_root, include_ui=include_ui)
    except Exception as e:                    # noqa: BLE001
        return {'ok': False, 'error': f'扫描失败：{e}', 'added': 0,
                'skipped': 0, 'backup': '', 'by_kind': {}, 'rows': []}

    plan = [
        ('images', 'images.xlsx',
         ['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'],
         list(assets.get('images') or []), True),
        ('music', 'music.xlsx', ['ID', 'Path', 'Loop', 'Volume', 'Notes'],
         list((assets.get('audio') or {}).get('music') or []), False),
        ('sounds', 'sounds.xlsx', ['ID', 'Path', 'Notes'],
         list((assets.get('audio') or {}).get('sounds') or []), False),
        ('voices', 'voices.xlsx', ['ID', 'Path', 'Notes'],
         list(assets.get('voices') or []), False),
    ]

    by_kind: dict = {}
    all_rows: list = []
    backups: list = []
    added_total = skipped_total = 0
    import shutil
    for kind, filename, headers, scanned, is_image in plan:
        target = tables_dir / filename
        if not scanned and not target.exists():
            continue
        try:
            existing = read_excel_rows(target) if target.exists() else []
        except Exception as e:                     # noqa: BLE001
            swallow('assets.asset_registry.register_missing_assets', e)
            existing = []
        have_paths = {_norm_path_for_compare(r.get('Path')) for r in existing}
        have_ids = {str(r.get('ID') or '').strip() for r in existing}
        fresh = []
        for row in scanned:
            if _norm_path_for_compare(row.get('Path')) in have_paths:
                skipped_total += 1
                continue
            if str(row.get('ID') or '').strip() in have_ids:
                skipped_total += 1
                continue
            row = dict(row)
            if is_image:
                row['Attributes'] = str(row.get('Attributes') or '').replace('.', '_')
                row['ID'] = str(row.get('ID') or '').replace('.', '_')
            fresh.append(row)
        by_kind[kind] = len(fresh)
        if not fresh:
            continue
        backup = ''
        try:
            tables_dir.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup_path = target.with_suffix('.bak.xlsx')
                shutil.copyfile(target, backup_path)
                backup = str(backup_path)
                backups.append(backup)
            wb = openpyxl.load_workbook(target) if target.exists() \
                else openpyxl.Workbook()
            ws = wb.active
            if ws.max_row < 1 or not any(c.value for c in ws[1]):
                for col, name in enumerate(headers, start=1):
                    set_text(ws, 1, col, name)
            for row in fresh:
                values = [row.get(h, '') for h in headers]
                append_text_row(ws, values)
                all_rows.append(row)
            wb.save(target)
            added_total += len(fresh)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': f'写 {filename} 失败：{e}',
                    'added': added_total, 'skipped': skipped_total,
                    'backup': backup, 'by_kind': by_kind, 'rows': all_rows}
    return {'ok': True, 'error': '', 'added': added_total,
            'skipped': skipped_total, 'backup': backups[0] if backups else '',
            'backups': backups, 'by_kind': by_kind, 'rows': all_rows,
            'tables_dir': str(tables_dir)}


# ===================== 命令行自测 =====================

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python asset_registry.py <扫描目录> [输出目录]")
        sys.exit(0)

    scan_root = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd() / 'asset_tables'

    print(f"扫描目录: {scan_root}")
    assets = scan_assets(scan_root, include_ui=False)
    write_asset_excel(assets, output_dir, split_by_category=False)
    print(f"已生成资产表到 {output_dir}")

    reg = parse_asset_tables(output_dir)
    print(f"图片: {len(reg.images)}, 音乐: {len(reg.music)}, 音效: {len(reg.sounds)}")
    if reg.errors:
        print("错误:", reg.errors)
    if reg.warnings:
        print("警告:", reg.warnings[:10],
              "..." if len(reg.warnings) > 10 else "")