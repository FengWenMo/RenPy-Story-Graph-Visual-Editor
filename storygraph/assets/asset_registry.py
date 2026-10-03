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
    RENPY_BUILTIN_TRANSFORMS,
)
from storygraph.excel.cells import append_text_row, set_text


# ===================== 常量 =====================
SUPPORTED_IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
SUPPORTED_AUDIO_EXT = {'.ogg', '.mp3', '.wav', '.m4a', '.flac', '.aac', '.opus'}

CATEGORY_KEYWORDS = {
    'background': ['bg', 'background', 'backdrop', 'scene'],
    'cg': ['cg', 'event', 'cutscene', 'illust'],
    'character': ['character', 'char', 'sprite', 'avatar', 'portrait',
                  'figure', 'people'],
    'ui': ['gui', 'ui', 'dutui', 'interface', 'system', 'icon', 'button',
           'menu'],
    'music': ['bgm', 'music', 'theme', 'loop', 'ost'],
    'sound': ['sfx', 'sound', 'effect', 'se', 'voice', 'click', 'hit'],
}


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
    headers = [cell.value for cell in ws[1]]
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

_asset_set_text = set_text
_asset_append_row = append_text_row


def write_dict_to_excel(file_path, data_dict: dict, field_order: list):
    """将 {id: row_dict} 写入 Excel。"""
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(field_order)
    for _asset_id, row_data in data_dict.items():
        excel_row = [row_data.get(field, '') for field in field_order]
        _asset_append_row(ws, excel_row)
    wb.save(file_path)


# ===================== 智能扫描 =====================

def _detect_category(rel_path: Path) -> str:
    """根据路径中的关键词推断资产类别。"""
    path_str = rel_path.as_posix().lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in path_str for kw in kws):
            # bg 与 bgm 冲突时，优先 bgm（音乐）
            if cat == 'background' and 'bgm' in path_str:
                continue
            return cat
    parent = rel_path.parent.name.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if parent in kws:
            return cat
    return 'other'


# 图片目录里"只是容器、不含语义"的名字：它们不能当 image tag 用
# （`images/eileen/happy.png` 的 tag 是 eileen 而不是 images）。
_GENERIC_IMAGE_DIRS = frozenset({
    'game', 'images', 'image', 'img', 'imgs', 'assets', 'asset', 'graphics',
    'sprite', 'sprites', 'characters', 'character', 'char', 'chars',
    'pics', 'pic', 'pictures', 'picture', 'figure', 'figures', 'art',
    'portrait', 'portraits', 'avatar', 'avatars', 'story', 'data',
})


def _normalize_tag(name: str) -> str:
    """目录名/文件名 → image tag：去分隔符、转小写。"""
    name = str(name or '').strip()
    for ch in ('_', '-'):
        name = name.replace(ch, ' ')
    return ' '.join(name.split()).lower()


def _detect_image_tag(file: Path, rel_path: Path, category: str) -> str:
    """
    推断一张图在脚本里的 **image tag**。

    这是 P0 的关键：脚本写的是 `show eileen happy`（tag + attribute），
    而资产 ID 取的是文件名（`happy`）。算不出 tag，两边的映射就断了，
    `eileen/` 目录下的立绘会被整组误报成"未使用资产"。

    规则（Ren'Py 自动图像定义的习惯）：
      · 背景 / CG / UI → 固定 tag（bg / cg / ui）
      · 立绘 → 用"角色目录名"当 tag（`images/eileen/happy.png` → eileen）
      · 没有角色目录 → 退到文件名首段（`eileen_happy.png` → eileen）
    """
    if category == 'background':
        return 'bg'
    if category == 'cg':
        return 'cg'
    if category == 'ui':
        return 'ui'

    # 立绘：从最靠近文件的父目录往上找第一个"有语义"的目录名
    for part in reversed(rel_path.parts[:-1]):
        low = part.lower()
        if low in _GENERIC_IMAGE_DIRS:
            continue
        return _normalize_tag(part)

    stem = file.stem
    if '_' in stem:
        return _normalize_tag(stem.split('_')[0])
    return ''


# 音频用途判定的目录白名单。目录名比文件名可靠得多：
# `audio/night.ogg` 是首曲子，但文件名里既没有 bgm 也没有 music，
# 旧实现一律判成"音效"——于是 Music 下拉里空空如也，文案根本选不到音乐。
_MUSIC_DIR_NAMES = {'music', 'bgm', 'bgm_music', 'ost', 'soundtrack',
                    'bgm_music_box'}
_SOUND_DIR_NAMES = {'sound', 'sounds', 'sfx', 'se', 'effect', 'effects',
                    'ampl', 'noise'}

# 文件名里的"用途线索"。音效线索优先判定：`sfx_xxx.ogg` 就算放在
# music/ 目录下也是音效，名字比目录更具体。
_SOUND_NAME_HINTS = ('sfx', 'se_', '_se', 'sound', 'effect', 'click', 'hit',
                     'voice', 'button', 'ui_', 'impact', 'step', 'door',
                     'noise', 'beep', 'alarm', 'switch')
_MUSIC_NAME_HINTS = ('bgm', 'music', 'theme', 'ost', 'song', 'loop',
                     'soundtrack')


def _classify_audio(rel_path: Path) -> str:
    """
    判断一个音频文件是 music 还是 sound。

    优先级：目录语义 > 文件名线索 > 默认 music。

    为什么默认 music：文案最在意"音乐能不能选到"。`game/audio/night.ogg`
    这种既不在音效目录、名字也不像音效的文件，当音乐收进下拉，文案就能
    选；判成音效则它永远不出现在 Music 列，这正是用户抱怨的痛点。
    真正的音效几乎都带 sfx_/se_/click 这类名字或放在 sfx/ 目录。
    """
    parts = [p.lower() for p in rel_path.parts[:-1]]
    for p in reversed(parts):
        if p in _SOUND_DIR_NAMES:
            return 'sound'
        if p in _MUSIC_DIR_NAMES:
            return 'music'

    name = rel_path.name.lower()
    if any(h in name for h in _SOUND_NAME_HINTS):
        return 'sound'
    if any(h in name for h in _MUSIC_NAME_HINTS):
        return 'music'

    path_str = rel_path.as_posix().lower()
    if any(kw in path_str for kw in CATEGORY_KEYWORDS['music']):
        return 'music'
    return 'music'


def _make_unique_id(stem: str, parent_dirs: list, existing_ids: set) -> str:
    """生成唯一 ID：优先用 stem，冲突时逐步添加父目录名。"""
    if stem not in existing_ids:
        existing_ids.add(stem)
        return stem
    for i in range(len(parent_dirs) - 1, -1, -1):
        new_id = f"{parent_dirs[i]}_{stem}"
        if new_id not in existing_ids:
            existing_ids.add(new_id)
            return new_id
    counter = 1
    while True:
        new_id = f"{stem}_{counter}"
        if new_id not in existing_ids:
            existing_ids.add(new_id)
            return new_id
        counter += 1


def _is_scan_noise(filename: str) -> bool:
    """
    过滤掉"不是真资产"的噪声文件，避免误扫进资源表。

    典型来源：从聊天/浏览器粘贴截图时产生的
    ``Clipboard_Screenshot.png``、``屏幕截图 xxx.png``、``Snipaste_xxx.png``，
    以及 Excel 的临时锁文件 ``~$xxx.xlsx``。这些不该出现在候选下拉里。
    """
    name = (filename or '').lower()
    if not name:
        return True
    if name.startswith('~$'):
        return True
    for kw in ('clipboard', 'screenshot', '屏幕截图', '截屏',
               'snipaste', 'screen shot'):
        if kw in name:
            return True
    return False


def scan_assets(scan_root, include_ui=True, audio_dirs=None,
                voice_dirs=None) -> dict:
    """
    扫描目录下的资产，返回：
    {
        'images': [...],
        'audio': {'music': [...], 'sound': [...]},
        'voices': [...],          # 语音文件（game/voice/ 等）
    }

    scan_root:   扫描根目录（Path 或 str）
    include_ui:  是否包含 UI 相关图片
    audio_dirs:  自定义音频目录列表；None 则自动探测
    voice_dirs:  自定义语音目录列表；None 则自动探测（game/voice/、
                 game/audio/voice/）
    """
    scan_root = Path(scan_root)
    if not scan_root.exists():
        return {'images': [], 'audio': {'music': [], 'sound': []},
                'voices': []}

    image_assets = []
    audio_music = []
    audio_sound = []
    voice_assets = []

    # 音频目录自动探测。要同时看项目根和 Ren'Py 的 game/ 目录——
    # 真实项目里音频在 game/audio/、game/music/，只探根目录会全部漏掉，
    # 于是退化成"整棵树 rglob"，全靠文件名猜音乐还是音效。
    if audio_dirs is None:
        candidates = []
        for base in (scan_root, scan_root / 'game'):
            candidates += [base / 'audio', base / 'music', base / 'sounds',
                           base / 'bgm']
        existing = [d for d in candidates if d.exists()]
        audio_dirs = existing if existing else [scan_root]

    # 语音目录自动探测（语音常独立存放，不应混进音效候选）
    if voice_dirs is None:
        vcands = []
        for base in (scan_root, scan_root / 'game'):
            vcands += [base / 'voice', base / 'audio' / 'voice',
                       base / 'voices', base / 'sound' / 'voice']
        voice_dirs = [d for d in vcands if d.exists()]
    voice_dirs = list(voice_dirs)

    existing_image_ids = set()

    # ---------- 图片扫描 ----------
    for file in scan_root.rglob('*'):
        if not file.is_file() or file.suffix.lower() not in SUPPORTED_IMAGE_EXT:
            continue
        if _is_scan_noise(file.name):
            continue
        rel_path = file.relative_to(scan_root)
        rel_str = rel_path.as_posix().lower()

        if not include_ui:
            if any(kw in rel_str
                   for kw in ('gui', 'ui', 'dutui', 'interface', 'system')):
                continue

        category = _detect_category(rel_path)
        parent_dirs = [p.name for p in rel_path.parents if p != scan_root]

        tag = ''
        attributes = file.stem
        if category == 'character':
            char_idx = None
            for i, part in enumerate(rel_path.parts):
                if any(kw in part.lower()
                       for kw in CATEGORY_KEYWORDS['character']):
                    char_idx = i
                    break
            if char_idx is not None and char_idx + 1 < len(rel_path.parts):
                possible_name = rel_path.parts[char_idx + 1]
                if possible_name == file.name:
                    tag = _normalize_tag(file.stem.split('_')[0])
                else:
                    tag = _normalize_tag(possible_name)
            else:
                tag = _normalize_tag(file.stem.split('_')[0])
        else:
            # 背景/CG/UI 走固定 tag；其余（尤其没有 char 关键词的立绘目录，
            # 如 images/eileen/happy.png）靠"最近的语义目录名"当 tag。
            tag = _detect_image_tag(file, rel_path, category)

        unique_id = _make_unique_id(file.stem, parent_dirs, existing_image_ids)
        image_assets.append({
            'ID': unique_id,
            'Path': rel_path.as_posix(),
            'Tag': tag,
            'Attributes': attributes,
            'Category': category,
            'Notes': '',
        })

    # ---------- 音频扫描 ----------
    voice_dirs_resolved = [Path(d) for d in voice_dirs]

    def _under_voice(file: Path) -> bool:
        for vd in voice_dirs_resolved:
            try:
                file.relative_to(vd)
                return True
            except ValueError:
                continue
        return False

    for adir in audio_dirs:
        adir = Path(adir)
        if not adir.exists():
            continue
        for file in adir.rglob('*'):
            if not file.is_file() or file.suffix.lower() not in SUPPORTED_AUDIO_EXT:
                continue
            if _is_scan_noise(file.name):
                continue
            # 语音目录已经单独扫过，别再把同一个文件当作音效收一遍
            if _under_voice(file):
                continue
            try:
                rel_path = file.relative_to(scan_root)
            except ValueError:
                rel_path = file
            stem = file.stem
            if _classify_audio(rel_path) == 'music':
                audio_music.append({
                    'ID': f"bgm_{stem}" if not stem.startswith('bgm_')
                          else stem,
                    'Path': rel_path.as_posix(),
                    'Loop': True,
                    'Volume': 1.0,
                    'Notes': '',
                })
            else:
                audio_sound.append({
                    'ID': f"sfx_{stem}" if not stem.startswith('sfx_')
                          else stem,
                    'Path': rel_path.as_posix(),
                    'Notes': '',
                })

    # ---------- 语音扫描（独立目录，不与音效混） ----------
    for vdir in voice_dirs:
        vdir = Path(vdir)
        if not vdir.exists():
            continue
        for file in vdir.rglob('*'):
            if not file.is_file() or file.suffix.lower() not in SUPPORTED_AUDIO_EXT:
                continue
            if _is_scan_noise(file.name):
                continue
            try:
                rel_path = file.relative_to(scan_root)
            except ValueError:
                rel_path = file
            voice_assets.append({
                'ID': file.stem,
                'Path': rel_path.as_posix(),
                'Notes': '',
            })

    return {
        'images': image_assets,
        'audio': {'music': audio_music, 'sound': audio_sound},
        'voices': voice_assets,
    }


# ===================== Excel 生成 =====================

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
_STANDARD_TABLES = {
    'characters': 'characters.xlsx',
    'images': 'images.xlsx',
    'transforms': 'transforms.xlsx',
    'music': 'music.xlsx',
    'sounds': 'sounds.xlsx',
    'voices': 'voices.xlsx',
}

# 资源表写入时的字段顺序（用于"新建资产"追加一行）
_TABLE_FIELDS = {
    'character': ['ID', 'Name', 'Color', 'ImageTag', 'Notes'],
    'image': ['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'],
    # transform 增加 ATL/Params/Doc/Category：支持自定义变换（文档第四节），
    # 导出时按 ATL 生成 `transform id(params):` 代码块；Doc 是给文案看的
    # 一句话说明（表格下拉里选中它就显示这句）。
    'transform': ['ID', 'Path', 'ATL', 'Params', 'Doc', 'Category', 'Notes'],
    'music': ['ID', 'Path', 'Loop', 'Volume', 'Notes'],
    'sound': ['ID', 'Path', 'Notes'],
    'voice': ['ID', 'Path', 'Notes'],
}

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
                    headers = [c.value for c in ws[1]]
                    _asset_append_row(ws, [row.get(h, '') if h else '' for h in headers])
                else:
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    fields = _TABLE_FIELDS[asset_type]
                    ws.append(fields)
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
                         update_keys: tuple = ()) -> int:
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
    返回 = 新增行数 + 被改写的单元格数。
    """
    table_key = str(table_key or '')
    fname = _STANDARD_TABLES.get(table_key)
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
            headers = [c.value for c in ws[1]]
            if not headers or all(h is None for h in headers):
                headers = list(rows[0].keys())
                ws.append(headers)
            row_of = {}
            id_col = headers.index('ID') + 1 if 'ID' in headers else 1
            for r in range(2, ws.max_row + 1):
                v = ws.cell(row=r, column=id_col).value
                if v:
                    row_of.setdefault(str(v), r)
            for row in rows:
                aid = str(row.get('ID') or '')
                if not aid:
                    continue
                if aid in row_of:
                    # 已存在的行：默认跳过；给了 update_keys 才更新白名单列
                    if update_keys:
                        updated += _update_row_cells(
                            ws, row_of[aid], headers, row, update_keys)
                    continue
                row_of[aid] = _asset_append_row(
                    ws, [row.get(h, '') if h else '' for h in headers])
                added += 1
            if added or updated:
                wb.save(path)
        else:
            wb = openpyxl.Workbook()
            ws = wb.active
            headers = list(rows[0].keys())
            ws.append(headers)
            for row in rows:
                _asset_append_row(ws, [row.get(h, '') for h in headers])
                added += 1
            wb.save(path)
    except Exception as e:                      # noqa: BLE001
        print(f"[asset_registry] 写入 {fname} 失败: {e}")
        return 0
    return added + updated


def parse_asset_tables(asset_dir, hooks=None, project_root=None) -> AssetRegistry:
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

    validate_registry(registry, project_root)
    # 记住来路：资源图库要按 Path 去磁盘上取缩略图，得知道基准目录。
    registry.asset_dir = asset_dir
    registry.project_root = project_root
    return registry


# ===================== 校验 =====================

_COLOR_PATTERN = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')


def validate_registry(registry: AssetRegistry, project_root):
    """检查文件存在性、依赖引用、颜色格式等。"""
    project_root = Path(project_root)

    for img_id, data in registry.images.items():
        p = data.get('Path')
        if p and not (project_root / p).exists():
            registry.warnings.append(f"图片 '{img_id}' 文件不存在: {p}")

    for type_key in ('music', 'sounds', 'voices'):
        for aid, data in getattr(registry, type_key).items():
            p = data.get('Path')
            if p and not (project_root / p).exists():
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
_SHOW_STOP_WORDS = frozenset({
    'at', 'as', 'with', 'behind', 'onlayer', 'zorder', 'expression',
})

_RE_SHOW_LINE = re.compile(
    r'^\s*(?P<verb>show|scene|hide)\s+(?P<rest>.*?)\s*$', re.MULTILINE)
_RE_AUDIO_LINE = re.compile(
    r'^\s*(?:play|queue|stop)\s+(?P<channel>music|sound|audio)\s+'
    r'["\']?(?P<name>[\w.]+)', re.MULTILINE)
_RE_AT_TRANSFORM = re.compile(r'\bat\s+(?P<name>[\w.]+)')
_RE_WITH_TRANSITION = re.compile(r'\bwith\s+(?P<name>[\w.]+)')
# voice 两种写法：字符串路径（voice "voice/a.ogg"）或裸变量/ID（voice v_001）。
# 之前这条正则路径完全没收 voice，导致语音资产永远显示"未使用"。
_RE_VOICE_LINE = re.compile(
    r'^\s*voice\s+(?:["\'](?P<q>[^"\']+)["\']|(?P<w>[\w.]+))', re.MULTILINE)


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
    """从 show/scene/hide 的剩余部分里切出图像名，遇到关键字即停。"""
    parts = []
    for tok in rest.split():
        low = tok.lower().rstrip(',')
        if low in _SHOW_STOP_WORDS:
            break
        parts.append(tok.rstrip(','))
    return ' '.join(parts)


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
_AUDIO_ID_PREFIXES = ('bgm_', 'bgm ', 'sfx_', 'sfx ', 'se_', 'se ',
                      'voice_', 'voice ', 'mus_', 'mus ', 'v_')


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
        self.audio = {}        # 音频别名（归一化）-> [id]
        self.image = {}        # 图片别名（归一化）-> [(id, is_loose)]
        self.prefix = []       # 排序后的 (别名, id)，用于前缀匹配（排除纯 tag）

        mapping = (('images', 'image'), ('music', 'music'),
                   ('sounds', 'sound'), ('voices', 'voice'),
                   ('transforms', 'transform'),
                   ('characters', 'character'))
        for attr, _kind in mapping:
            for aid in (getattr(registry, attr, {}) or {}):
                self.ids[aid] = True

        for attr in ('music', 'sounds', 'voices'):
            for aid, row in (getattr(registry, attr, {}) or {}).items():
                for alias in audio_aliases(aid, row or {}):
                    self.audio.setdefault(alias, [])
                    if aid not in self.audio[alias]:
                        self.audio[alias].append(aid)

        prefix_buf = set()
        for aid, row in (registry.images or {}).items():
            row = row or {}
            tag = _normalize_spec(row.get('Tag'))
            attrs = _normalize_spec(row.get('Attributes'))
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

    def resolve(self, candidate: str) -> list:
        if not candidate:
            return []
        cand = str(candidate).strip()
        if not cand:
            return []
        if cand in self.ids:
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
            if spec and not is_expr and not is_dynamic_image_spec(spec):
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
        except Exception:
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
        except Exception:                     # noqa: BLE001
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