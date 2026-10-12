"""
`asset_registry` 的**扫描族**（2026-10-11 从 `asset_registry.py` 搬出）
========================================================================

为什么单独一个模块：`asset_registry.py` 曾是本仓最长模块（3614 行，
其中模块级函数 2711 行 / 87 个）。按
`docs/重构方案_拆分_renpy_parser_2026-10-11.md` 的**实测闭包表**，
扫描族初选 5 个、闭包 27 个定义 / 892 行 ——
而常量层先搬走之前是 49 个定义：**这就是"先搬叶子层"的收益**。

⚠ 搬家纪律：**逐字照搬**（连注释与空行），`git diff` 一眼能看出"只是搬家"。
"""


from __future__ import annotations

from pathlib import Path
from storygraph.assets.asset_registry_constants import SUPPORTED_IMAGE_EXT, SUPPORTED_AUDIO_EXT, CATEGORY_KEYWORDS, ASSET_FIELD_LABELS, _asset_set_text, _asset_append_row, _UI_WORDS, _UI_SUFFIXES, _CHARACTER_CONTAINER_WORDS, _GENERIC_IMAGE_ROOTS, _BG_WORDS, _CG_WORDS, _BG_NAME_RE, _CG_NAME_RE, _UI_NAME_RE, _BG_NAME_HINTS, _CG_NAME_HINTS, _UI_NAME_HINTS, _NON_CHARACTER_FIRST_WORDS, _SKIP_SCAN_DIRS, _GENERIC_IMAGE_DIRS, _MUSIC_DIR_NAMES, _SOUND_DIR_NAMES, _SOUND_NAME_HINTS, _MUSIC_NAME_HINTS, _ASSET_KINDS, _STANDARD_TABLES, _TABLE_FIELDS, _PLACEHOLDER_MARKS, _COLOR_PATTERN, _SHOW_STOP_WORDS, _RE_SHOW_LINE, _RE_AUDIO_LINE, _RE_AT_TRANSFORM, _RE_WITH_TRANSITION, _RE_VOICE_LINE, _AUDIO_ID_PREFIXES
from storygraph.core.safeguard import swallow
from storygraph.assets.asset_registry_constants import (
    CATEGORY_KEYWORDS,
    SUPPORTED_AUDIO_EXT,
    SUPPORTED_IMAGE_EXT,
    _BG_NAME_HINTS,
    _BG_NAME_RE,
    _BG_WORDS,
    _CG_NAME_HINTS,
    _CG_NAME_RE,
    _CG_WORDS,
    _CHARACTER_CONTAINER_WORDS,
    _GENERIC_IMAGE_DIRS,
    _GENERIC_IMAGE_ROOTS,
    _MUSIC_DIR_NAMES,
    _MUSIC_NAME_HINTS,
    _NON_CHARACTER_FIRST_WORDS,
    _SKIP_SCAN_DIRS,
    _SOUND_DIR_NAMES,
    _SOUND_NAME_HINTS,
    _UI_NAME_HINTS,
    _UI_NAME_RE,
    _UI_SUFFIXES,
    _UI_WORDS,
)


def _segment_tokens(segment: str) -> list:
    """目录名 → 词元（`Dynamic_Images` → ['dynamic','images']）。"""
    s = str(segment or '').lower()
    for ch in ('_', '-', '.', '(', ')', '[', ']'):
        s = s.replace(ch, ' ')
    return [t for t in s.split() if t]

def _segment_is_ui(segment: str) -> bool:
    """
    这一段是 UI 容器吗（`gui` / `dutui` / `charactercenter` / `button` …）？

    ⚠ **不能拿整条路径做子串匹配**（2026-10-05 实测踩到的坑）：
    `'ui' in path` 会把 `build` / `guide` / `fruit` 也算成 UI；
    `'se'` 会把 `displeased` 算成音效；`'bg'` 会把
    `choice_hover_background.png` 算成背景。所以：

      · 先按词元**精确**匹配（`my_ui_button` → ['my','ui','button'] ✓）；
      · 再按**后缀**收几个常见的复合词（`dutui` 结尾是 `ui`、
        `charactercenter` 结尾是 `center`）—— 这些词尾几乎不会误伤
        （`build` 不以 `ui` 结尾）。
    """
    toks = _segment_tokens(segment)
    if any(t in _UI_WORDS for t in toks):
        return True
    low = str(segment or '').lower().strip()
    return any(low.endswith(sfx) for sfx in _UI_SUFFIXES)

def _under_skip_dir(rel_path: Path) -> bool:
    """这一条路径下面是不是有"整目录跳过"的名字？"""
    for part in rel_path.parts[:-1]:
        if part.lower() in _SKIP_SCAN_DIRS:
            return True
    return False

def _path_under_skip_dir(path, scan_root) -> bool:
    """
    `_under_skip_dir` 的**安全版**：路径可能不在 `scan_root` 底下
    （音频目录允许在项目外），那时只拿绝对路径自己比。

    ⚠ **调用方必须传"这个文件是被哪个目录扫进来的"那个目录**，不能一律传
    项目根（2026-10-07 修，长期红的 `test_custom_audio_dirs` 就是它）：

      · `scan_root` 内的文件 → 相对路径照旧 ✓；
      · **项目外**的音频目录 → 相对路径算不出来，退回**绝对路径**逐段比，
        于是路径里任何一段叫 `temp` / `build` / `cache` / `logs` … 都会命中
        `_SKIP_SCAN_DIRS` 而被**整条丢掉** ——
        实测 `…\\AppData\\Local\\Temp\\extra_audio\\theme_song.mp3` 里的
        `Temp` 命中，音乐/音效**双双为空**；
        真实项目只要放在 `D:\\build\\我的游戏\\…` 这种路径下，音频会**全部消失**。

      传"扫描基准目录"（`adir` / `vdir`）之后：`build/` 这类子目录照样跳过
      （它就在基准下面），而 `Temp` / `AppData` 这些**无关祖先**不再参与判定。
    """
    try:
        rel = Path(path).relative_to(scan_root)
    except ValueError as e:                     # 不在基准目录内：按绝对路径比
        swallow('assets.asset_registry._path_under_skip_dir', e)
        rel = Path(path)
    return _under_skip_dir(rel)

def _detect_category(rel_path: Path) -> str:
    """
    路径 → 资产类别（`background` / `cg` / `character` / `ui` / `other`）。

    **唯一真源**：只认**目录段**的语义，从最靠近文件的目录往外找；
    文件名只在"一点目录线索都没有"时当弱线索。

    为什么重写（2026-10-05 用户实测："现在的 ui bg 一类的仍然不太准"）：
    旧实现把**整条路径**当字符串做 `kw in path_str`，于是

      · `game/photo/music_room_photo/*.png` → `music`（图片被判成音乐，16 行）；
      · `game/pomodorro/images/dororo_displeased.png` → `sound`
        （`se` 命中了 `displea**se**d`，12 行）；
      · `game/gui/button/choice_hover_background.png` → `background`
        （按钮图被当成背景，16 行）；
      · `game/charactercenter/base.png` → `character`（界面图被当成立绘）；
      · `game/gui/CG_frame.png` / `game/gui/dutui/**/CG鉴赏.png` → `cg`
        （界面帧被当成 CG）。

    新规则（真实项目逐条对齐过）：

      · **最近目录优先**：`gui/dutui/Frame 24/CG鉴赏.png` → 最近的是 `dutui`
        → ui ✓（`gui` 更远，但同样是 ui）；
      · 目录语义**压过**文件名线索：`gui/button/*_background.png` → ui ✓；
      · 只有"整条路径一个语义目录都没有"时才看文件名：
        `images/bg_1.jpg` → background ✓、`images/cg01.png` → cg ✓；
      · 都不认 → `other`（**不再瞎猜**：`pomodorro/images/*`、`minigamepieces/*`
        这些就是 other）。
    """
    dirs = [p for p in rel_path.parts[:-1] if p]
    for seg in reversed(dirs):                     # 从最近往外
        toks = _segment_tokens(seg)
        if not toks:
            continue
        if any(t in _CG_WORDS for t in toks):
            return 'cg'
        if any(t in _BG_WORDS for t in toks):
            return 'background'
        if any(t in _CHARACTER_CONTAINER_WORDS for t in toks):
            return 'character'
        if _segment_is_ui(seg):
            return 'ui'
    # `images/<tag>/x.png`：**`images` 的直接子目录**就是角色目录 —— 这是最常见的
    # 立绘布局（`game/images/eileen/happy.png`），而目录名本身（`eileen`）
    # 看不出任何语义。⚠ 只认"父目录的父目录叫 images"这一种：
    # `game/pomodorro/images/dororo.png`（文件直接躺在 `images/` 里）不算，
    # `game/minigamepieces/logo/x.png` 也不算（那个 `logo` 上面不是 `images`）。
    if len(dirs) >= 2 and dirs[-2].lower() in _GENERIC_IMAGE_ROOTS:
        return 'character'
    name = rel_path.name.lower()                   # 弱线索：只看文件名
    stem = rel_path.stem.lower()
    if _CG_NAME_RE.match(stem) or any(h in name for h in _CG_NAME_HINTS):
        return 'cg'
    if _BG_NAME_RE.match(stem) or any(h in name for h in _BG_NAME_HINTS):
        return 'background'
    if _UI_NAME_RE.match(stem) or any(h in name for h in _UI_NAME_HINTS):
        return 'ui'
    # **平铺约定**：`images/eileen_happy.png` —— 目录无语义，但文件名是
    # `<tag>_<属性>`（Ren'Py 自动图像的惯例），按立绘收，tag = 首段。
    #
    # ⚠ 只认"确实在 `images/` 下"的：项目根目录的零散图
    # （`about_collection_smoke.png`）不算 —— 否则会给它编出一个
    # 脚本里根本不存在的角色 tag（用户表里那些行 Tag 是空的）。
    low_dirs = [d.lower() for d in dirs]
    if 'images' in low_dirs or 'image' in low_dirs:
        toks = _segment_tokens(stem)
        if len(toks) >= 2 and toks[0] not in _NON_CHARACTER_FIRST_WORDS:
            return 'character'
    return 'other'

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

    规则（**真实项目逐条对齐过**，2026-10-05 重写）：

      · 背景 / CG / UI → 固定 tag（`bg` / `cg` / `ui`）；
      · 立绘：`characters/<tag>/…` 里 **`<tag>` 就是角色名** —— 取容器目录的
        **下一段**，不再"从最近往外找第一个非通用目录"：
          `images/characters/lan/dynamic_images/lan_body.png` → `lan`
          （旧实现给的是 `dynamic images` ✗ 22 行）
          `images/characters/avatar/lan.png` → `avatar`
          （旧实现给 `''` ✗ —— 因为 `avatar` 在通用目录表里，
            可这里它就是角色名）
      · 没有 `characters/` 容器时：文件名首段（`eileen_happy.png` → `eileen`）；
      · **其余一律空**：`other` 类的图（`pomodorro/images/*`、`minigamepieces/*`、
        根目录的零散图）**不再拿目录名硬编一个 tag** —— 实测那会造出
        `pomodorro` / `ball moving game` / `music room photo` / `logo` 这些
        **脚本里根本不存在的 tag**（用户表里这些行的 Tag 是空的）。
    """
    if category == 'background':
        return 'bg'
    if category == 'cg':
        return 'cg'
    if category == 'ui':
        return 'ui'

    parts = list(rel_path.parts[:-1])              # 目录段（不含文件名）
    for i, seg in enumerate(parts):
        toks = _segment_tokens(seg)
        if any(t in _CHARACTER_CONTAINER_WORDS for t in toks):
            # 容器目录的下一段 = 角色名（`characters/ling/happy.png` → ling）
            if i + 1 < len(parts):
                nxt = _normalize_tag(parts[i + 1])
                if nxt:
                    return nxt
            stem = file.stem
            return _normalize_tag(stem.split('_')[0]) if '_' in stem else ''
    if category != 'character':
        return ''
    # **没有 `characters/` 容器**（`images/eileen/happy.png` 这种最常见）：
    #   · 文件就躺在通用图片目录下（`images/dororo_displeased.png`）→ 平铺约定，
    #     tag = 文件名首段；
    #   · 否则 → 最近的那个有语义的目录名（`images/eileen/happy.png` → eileen）。
    parent = parts[-1] if parts else ''
    if not parent or parent.lower() in _GENERIC_IMAGE_DIRS:
        stem = file.stem
        if '_' in stem:
            return _normalize_tag(stem.split('_')[0])
        return ''
    for seg in reversed(parts):
        low = seg.lower()
        if low in _GENERIC_IMAGE_DIRS:
            continue
        if any(t in _CHARACTER_CONTAINER_WORDS
               for t in _segment_tokens(seg)):
            continue
        return _normalize_tag(seg)
    return ''

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

def _scan_result(images, music, sound, voices) -> dict:
    """`scan_assets` 的返回形状（原先四处各写一遍）。"""
    return {
        'images': list(images or []),
        'audio': {'music': list(music or []), 'sound': list(sound or [])},
        'voices': list(voices or []),
    }

def _music_row(stem: str, rel_posix: str) -> dict:
    """音乐行（`bgm_` 前缀只补一次 —— 原来两处各写一遍，必须一致）。"""
    return {'ID': f"bgm_{stem}" if not stem.startswith('bgm_') else stem,
            'Path': rel_posix, 'Loop': True, 'Volume': 1.0, 'Notes': ''}

def _sound_row(stem: str, rel_posix: str) -> dict:
    """音效行（同上，`sfx_` 前缀只补一次）。"""
    return {'ID': f"sfx_{stem}" if not stem.startswith('sfx_') else stem,
            'Path': rel_posix, 'Notes': ''}

def _is_under_any(file: Path, dirs) -> bool:
    """`file` 是否落在 `dirs` 里任一目录下（语音目录单独扫过时用它去重）。"""
    for d in dirs:
        try:
            file.relative_to(d)
            return True
        except ValueError:
            continue
    return False

def _scan_images(items, include_ui, existing_image_ids) -> list:
    """
    图片扫描：扩展名/噪声/跳过目录/UI 过滤 → 归类 → tag → 唯一 ID。

    ⚠ **tag 只有一个真源** = `_detect_image_tag`（2026-10-05 修）。
    这里从前对 `category == 'character'` 另写了一套内联逻辑（找"含
    character/char/sprite… 关键词的目录段"），于是：
      `game/images/eileen/happy.png`（**没有** `characters/` 容器，最常见的
      立绘布局）走的是内联那套 → 找不到关键词 → tag 退成文件名首段 `happy`
      → 脚本里的 `show eileen happy` 解析不到它 → 整组立绘被报"未使用"。
    两套实现必然漂移，所以直接把内联那套删掉。
    """
    out = []
    for rel_low, file in items:
        if file.suffix.lower() not in SUPPORTED_IMAGE_EXT:
            continue
        if _is_scan_noise(file.name):
            continue
        rel_path = Path(rel_low)
        rel_str = rel_low

        if _under_skip_dir(rel_path):
            continue                            # tmp/ 之类的目录不是素材

        if not include_ui:
            if any(kw in rel_str
                   for kw in ('gui', 'ui', 'dutui', 'interface', 'system')):
                continue

        category = _detect_category(rel_path)
        parent_dirs = [Path(seg).name for seg in rel_low.split('/')[:-1]]
        tag = _detect_image_tag(file, rel_path, category)
        attributes = file.stem
        unique_id = _make_unique_id(file.stem, parent_dirs, existing_image_ids)
        out.append({
            'ID': unique_id,
            'Path': rel_path.as_posix(),
            'Tag': tag,
            'Attributes': attributes,
            'Category': category,
            'Notes': '',
        })
    return out

def _scan_audio_from_index(items):
    """
    **有 `walk` 时音频全程从那一份索引里取**（一次遍历）：目录探测与文件枚举
    都不再各走一遍树。返回 `(music, sound, voices)`。

    语音目录（任意深度的 `voice/` / `voices/`）单独收，不混进音效候选。
    """
    music, sound, voices = [], [], []
    for rel_low, file in items:
        if file.suffix.lower() not in SUPPORTED_AUDIO_EXT:
            continue
        if _is_scan_noise(file.name):
            continue
        rel_path = Path(rel_low)
        if _under_skip_dir(rel_path):
            continue
        stem = file.stem
        if any(p.lower() in ('voice', 'voices')
               for p in rel_path.parts[:-1]):
            voices.append({'ID': stem, 'Path': rel_path.as_posix(),
                           'Notes': ''})
            continue
        if _classify_audio(rel_path) == 'music':
            music.append(_music_row(stem, rel_path.as_posix()))
        else:
            sound.append(_sound_row(stem, rel_path.as_posix()))
    return music, sound, voices

def _scan_audio_from_dirs(audio_dirs, scan_root, voice_dirs) -> tuple:
    """没给 `walk` 时的音频扫描（按目录列表 rglob）。返回 `(music, sound)`。"""
    voice_resolved = [Path(d) for d in voice_dirs]
    music, sound = [], []
    for adir in audio_dirs:
        adir = Path(adir)
        if not adir.exists():
            continue
        for file in adir.rglob('*'):
            if not file.is_file() or file.suffix.lower() not in SUPPORTED_AUDIO_EXT:
                continue
            if _is_scan_noise(file.name):
                continue
            if _path_under_skip_dir(file, adir):
                continue
            # 语音目录已经单独扫过，别再把同一个文件当作音效收一遍
            if _is_under_any(file, voice_resolved):
                continue
            try:
                rel_path = file.relative_to(scan_root)
            except ValueError:
                rel_path = file
            stem = file.stem
            if _classify_audio(rel_path) == 'music':
                music.append(_music_row(stem, rel_path.as_posix()))
            else:
                sound.append(_sound_row(stem, rel_path.as_posix()))
    return music, sound

def _scan_voices_from_dirs(voice_dirs, scan_root) -> list:
    """语音扫描（独立目录，不与音效混）。"""
    out = []
    for vdir in voice_dirs:
        vdir = Path(vdir)
        if not vdir.exists():
            continue
        for file in vdir.rglob('*'):
            if not file.is_file() or file.suffix.lower() not in SUPPORTED_AUDIO_EXT:
                continue
            if _is_scan_noise(file.name):
                continue
            if _path_under_skip_dir(file, vdir):
                continue
            try:
                rel_path = file.relative_to(scan_root)
            except ValueError:
                rel_path = file
            out.append({'ID': file.stem, 'Path': rel_path.as_posix(),
                        'Notes': ''})
    return out

def _detect_audio_dirs(scan_root) -> list:
    """
    音频目录自动探测：按**目录名**在任意深度认（`sfx` / `se` 这类写法也认）。

    要同时看项目根和 Ren'Py 的 `game/` 目录 —— 真实项目里音频在
    `game/audio/`、`game/music/`，只探根目录会全部漏掉，于是退化成
    "整棵树 rglob"，全靠文件名猜音乐还是音效。

    ⚠ 2026-10-05 实测修：以前这里只探**写死的几个名字**（`audio` / `music` /
    `sounds` / `bgm`，且只在根与 `game/` 两层）—— 用户项目里
    `game/gui/sfx/*.wav`（8 个音效）和 `game/pomodorro/music/*`（7 个音乐，
    嵌在子模块里）**根本没进目录列表**，于是 49 个音频文件只扫出 34 个、
    「音效 0」。认不到任何目录时才退回"整棵树"。
    """
    known = {'audio', 'music', 'bgm', 'sound', 'sounds', 'sfx', 'se',
             'voice', 'voices'}
    found = []
    try:
        for d in scan_root.rglob('*'):
            try:
                if d.is_dir() and d.name.lower() in known:
                    found.append(d)
            except OSError:
                continue
    except OSError as e:                    # noqa: BLE001 - 目录读不动就算了
        swallow('assets.asset_registry.scan_assets.audio_dirs', e)
    return found if found else [scan_root]

def _detect_voice_dirs(scan_root) -> list:
    """语音目录自动探测（语音常独立存放，不应混进音效候选）。"""
    vcands = []
    for base in (scan_root, scan_root / 'game'):
        vcands += [base / 'voice', base / 'audio' / 'voice',
                   base / 'voices', base / 'sound' / 'voice']
    # 任意深度的 `voice/` / `voices/` 也算（`game/pomodorro/voice/` 这种）
    known_voice = {'voice', 'voices'}
    try:
        for d in scan_root.rglob('*'):
            try:
                if d.is_dir() and d.name.lower() in known_voice:
                    vcands.append(d)
            except OSError:
                continue
    except OSError as e:                    # noqa: BLE001
        swallow('assets.asset_registry.scan_assets.voice_dirs', e)
    return [d for d in vcands if d.exists()]

def _scan_file_items(scan_root, walk) -> list:
    """
    枚举 `(相对小写路径, Path)` —— 有索引走索引（**一次遍历**），否则自己 rglob。

    ⚠ 用索引里**已经算好的相对路径**，别再逐文件 `Path.relative_to()`：
    Windows 上一次 relative_to 要 0.34 ms，3273 张图实测 **2.3 秒**。
    排序是为了**确定性**（遍历顺序随目录栈而变；同名文件靠
    `_make_unique_id` 编后缀时，顺序不稳就会得到不同的 ID）。

    ⚠⚠ **嵌套副本不算项目资源**（`sr_general/game/…`）：把它们扫进来会编出
    `ling_angry` 这种 ID 去跟真正的"tag_attribute"抢解析，实测直接导致
    "立绘不显示 + 表情调不了"。
    """
    if walk is not None:
        return sorted(((rel, p) for rel, p in walk.files().items()
                       if not walk.is_nested(rel)), key=lambda kv: kv[0])
    items = []
    for p in scan_root.rglob('*'):
        if not p.is_file():
            continue
        try:
            items.append((p.relative_to(scan_root).as_posix().lower(), p))
        except ValueError:
            continue
    return items

def scan_assets(scan_root, include_ui=True, audio_dirs=None,
                voice_dirs=None, walk=None) -> dict:
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
    #
    # ⚠ 2026-10-05 实测修：以前这里只探**写死的几个名字**（`audio` / `music` /
    # `sounds` / `bgm`，且只在根与 `game/` 两层）—— 用户项目里
    # `game/gui/sfx/*.wav`（8 个音效）和 `game/pomodorro/music/*`（7 个音乐，
    # 嵌在子模块里）**根本没进目录列表**，于是 49 个音频文件只扫出 34 个、
    # 「音效 0」。
    #
    # 现在按**目录名**在任意深度认（`sfx` / `se` 这类常见写法也认），
    # 认不到任何目录时才退回"整棵树"。
    #
    # ⚠ 有 `walk` 时这两段探测**整个跳过**：走索引那条路按**目录段名**判类型
    # （`_classify_audio` + `voice/` 段名），根本不需要目录列表；而这两个
    # `rglob` 在真实项目要白跑 1.9 秒（用户："太卡了"）。
    # 音频/语音目录探测；⚠ 有 `walk` 时**整个跳过**：走索引那条路按
    # **目录段名**判类型（`_classify_audio` + `voice/` 段名），不需要目录
    # 列表；而这两个 `rglob` 在真实项目要白跑 1.9 秒（用户："太卡了"）。
    if audio_dirs is None and walk is None:
        audio_dirs = _detect_audio_dirs(scan_root)
    if voice_dirs is None and walk is None:
        voice_dirs = _detect_voice_dirs(scan_root)
    voice_dirs = list(voice_dirs or [])

    existing_image_ids = set()

    # ---------- 图片扫描 ----------
    # 文件清单只枚举**一次**（原先图片/音频/语音各 rglob 一遍，真实项目
    # 三遍树 ~3.8 秒）；细节见 `_scan_file_items`。
    _items = _scan_file_items(scan_root, walk)
    image_assets = _scan_images(_items, include_ui,
                                 existing_image_ids)

    # ---------- 音频扫描 ----------
    #
    # **有 `walk` 时全部从那一份索引里取**（一次遍历）：目录探测与文件枚举
    # 都不再各走一遍树。没给 walk 时保持老路（其它调用方/测试照旧）。
    if walk is not None:
        audio_music, audio_sound, voice_assets = _scan_audio_from_index(_items)
        return _scan_result(image_assets, audio_music, audio_sound,
                            voice_assets)


    audio_music, audio_sound = _scan_audio_from_dirs(audio_dirs, scan_root,
                                                    voice_dirs)
    voice_assets = _scan_voices_from_dirs(voice_dirs, scan_root)
    return _scan_result(image_assets, audio_music, audio_sound, voice_assets)

def _table_paths_from_registry(registry, table_paths, missing, outside, root, game_dir, walk, resolve_thumb_source, outside_total):
    """登记行 → `table_paths` / `missing` / `outside`（M4 从 `compare_registry_to_disk` 搬出，行为一字不改）。返回更新后的 `outside_total`。"""
    for kind, attr in (('image', 'images'), ('music', 'music'),
                       ('sound', 'sounds'), ('voice', 'voices')):
        key = {'image': 'images', 'music': 'music', 'sound': 'sounds',
               'voice': 'voices'}[kind]
        bucket = set()
        gone = []
        for _aid, row in ((getattr(registry, attr, {}) or {}) if registry
                          else {}).items():
            rel = str((row or {}).get('Path') or '').strip()
            if not rel:
                continue
            # ⚠ 有 `walk` 时**只查字典**：原先这里对每一行做
            # `resolve_thumb_source`（逐个基准 `is_file()`）+ `Path.resolve()`
            # —— 3123 行要上万次系统调用，实测这一步 **12.4 秒**，正是用户
            # "程序干未响应了！太卡了"的元凶。见 `FileWalkIndex`。
            if walk is not None:
                real = walk.match(rel)
            elif kind == 'image':
                real = resolve_thumb_source(rel, root)
            else:
                real = _resolve_audio_source(rel, root)
            if real is None:
                gone.append(rel)
                continue
            bucket.add(str(real))
            # **表里指到 `game/` 外面的行**：真实项目实测有 1386 行指向
            # `sr_general/**`（**另一份嵌套副本**）—— 这就是"两个克隆数字
            # 为什么不一样"的答案：表引用的是别的目录里的文件。
            try:
                Path(real).relative_to(game_dir)
            except ValueError:
                outside_total += 1
                try:
                    top = real.relative_to(root).parts[0]
                except ValueError:
                    top = str(real.drive or real.anchor or '')
                outside[top] = outside.get(top, 0) + 1
        table_paths[key] = bucket
        if gone:
            missing[key] = gone
    return outside_total

def _unregistered_from_disk(disk, table_paths, unregistered, root, walk, resolve_thumb_source):
    """盘上有、表里没有的 → `unregistered`（M4 从 `compare_registry_to_disk` 搬出，行为一字不改）。"""
    for key, paths in disk.items():
        have = table_paths.get(key) or set()
        extra = []
        for rel in paths:
            if not rel:
                continue
            if walk is not None:
                real = walk.match(rel)
            elif key == 'images':
                real = resolve_thumb_source(rel, root)
            else:
                real = _resolve_audio_source(rel, root)
            if real is None:
                continue
            # ⚠ 有 `walk` 时**别再 `resolve()`**：两边都出自同一份索引，
            # 字符串本来就一致。实测这里的 `Path.resolve()` 3373 次要 **3.8 秒**
            # （Windows 上 resolve 会摸文件系统）—— 这是"对账还要 4 秒"的元凶。
            same = str(real) if walk is not None else str(Path(real).resolve())
            if same not in have:
                extra.append(str(rel))
        if extra:
            unregistered[key] = extra

def compare_registry_to_disk(project_root, registry, include_ui=True,
                             walk=None, assets=None) -> dict:
    """
    **资源表 vs 磁盘**的对账（**只读**，一个文件都不改）。

    返回：

        {
          'disk':  {'images': 1645, 'music': 41, 'sounds': 8, 'voices': 0},
          'table': {'images': 2890, 'music': 41, 'sounds': 8, 'voices': 0},
          'unregistered': {'music': ['game/gui/sfx/点击.wav', ...]},  # 盘上有、表里没有
          'missing':      {'images': ['game/images/gone.png', ...]},  # 表里有、盘上找不到
          'stale_paths': 0,
        }

    为什么要它（用户 2026-10-05 实测报）：

    > 你确定这对吗？为何两个克隆出来的项目并不一样？
    > 每次导入的时候你的表是不是没更新？？？

    两个克隆报出来的数字不一样，原因是**资源的"真相"有两个**：

      · **表**（`game/asset_tables/*.xlsx`）是**用户可编辑的数据**，
        工具**从不自动改写**（只在完全没有表时才新建）；
      · **磁盘**是另一份真相。

    两者漂移时，界面上（下拉候选、缩略图）看到的是**表**，而用户以为
    看到的是项目 —— 于是"为什么这个克隆少了一千多张图"这种问题只能靠猜。
    实测那次现场就是：自动新建的表漏了 15 个音频（`game/gui/sfx/*.wav`
    与 `game/pomodoro/music/*` 不在写死的音频目录列表里，已修），
    而旧表里那 15 个是有的。

    这个函数只回答"差在哪"，补登由 `session.register_missing_assets()`
    （UI 的「补登缺失资源」，写前自动备份）负责。
    """
    from storygraph.story.excel_export import resolve_thumb_source
    root = Path(project_root)
    game_dir = root / 'game'                    # 只用于"是不是在 game/ 外"的判断
    table_paths = {}
    missing = {}
    #: 表里指到 `game/` **外面**的行（`{顶层目录: 行数}`）—— 见下面注释
    outside = {}
    outside_total = 0
    outside_total = _table_paths_from_registry(
        registry, table_paths, missing, outside, root, game_dir,
        walk, resolve_thumb_source, outside_total)

    if assets is None:
        assets = scan_assets(root, include_ui=include_ui, walk=walk)
    disk = {
        'images': [a.get('Path') for a in (assets.get('images') or [])],
        'music': [a.get('Path') for a in
                  ((assets.get('audio') or {}).get('music') or [])],
        'sounds': [a.get('Path') for a in
                   ((assets.get('audio') or {}).get('sound') or [])],
        'voices': [a.get('Path') for a in (assets.get('voices') or [])],
    }
    disk_counts = {k: len([p for p in v if p]) for k, v in disk.items()}
    unregistered = {}
    _unregistered_from_disk(disk, table_paths, unregistered,
                             root, walk, resolve_thumb_source)
    return {
        'disk': disk_counts,
        'table': {k: len(v) for k, v in table_paths.items()},
        #: 表里各类型**解析后的绝对路径**（判重必须用它，见 `sync_asset_tables`）。
        'table_resolved': {k: set(v) for k, v in table_paths.items()},
        'unregistered': unregistered,
        'missing': missing,
        'stale_paths': sum(len(v) for v in missing.values()),
        #: 表里指到 `<root>/game/` 外面的行数 + 按顶层目录的分布。
        #: 真实项目实测：1386 行落在 `sr_general/**`（另一份嵌套副本）——
        #: 那不是"文件丢了"，而是"表引用的是别的目录"，两种情况给出的
        #: 补救完全不同，所以必须分开报。
        'outside_game': {'count': outside_total, 'by_top': dict(outside)},
    }

def _resolve_audio_source(rel, project_root=None, walk=None):
    """
    音频的 Path 基准和图片一样乱，用同一套候选（只是扩展名不同）。

    给了 `walk`（`FileWalkIndex`）就**只在字典里查**（不碰磁盘）——
    这是"打开项目 N 秒 → 毫秒"的关键，见 `FileWalkIndex` 的说明。
    """
    if walk is not None:
        hit = walk.match(rel)
        return hit
    text = str(rel or '').strip().replace('\\', '/')
    if not text:
        return None
    p = Path(text)
    cands = []
    if p.is_absolute():
        cands.append(p)
    else:
        parts = [q for q in text.split('/') if q not in ('', '.')]
        bases = [Path(project_root) if project_root else Path.cwd()]
        if project_root:
            root = Path(project_root)
            bases = [root, root.parent, root / 'game', Path.cwd()]
        for base in bases:
            cands.append(base / text)
            if len(parts) > 1:
                cands.append(base / '/'.join(parts[1:]))
                cands.append(base / 'game' / '/'.join(parts[1:]))
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None

class FileWalkIndex:
    """
    **一次目录遍历**的产物：相对路径表 + 文件名表，用来 O(1) 匹配资源表里的 Path。

    为什么需要（2026-10-05 用户实测："给程序干未响应了！太卡了"）：

    真实项目 `sr_general` 量出来的数字：

      · 打开项目那条路原先要**走三遍**整棵树（图片一组、音频两组 rglob）；
      · 每走一遍 ~0.6 s，`scan_assets` 一共 **3.8 s**；
      · 对账 `compare_registry_to_disk` **12.4 s** —— 每一行都要
        `resolve_thumb_source`（逐个基准 `is_file()`）+ `Path.resolve()`
        （Windows 上 resolve 会摸文件系统），3123 行 + 3273 个磁盘文件
        加起来上万次系统调用；
      · 于是"打开项目"要几十秒，GUI 线程全程阻塞 → 未响应。

    有了它：走一遍树（0.6 s）建两张表，之后所有"这个相对路径在不在"
    都是**字典查找**，不碰磁盘。
    """

    def __init__(self, root):
        self.root = Path(root)
        self.by_rel = {}          # 'game/images/x.png'(小写) -> Path
        self.by_name = {}         # 'x.png' -> [相对路径, …]
        #: **嵌套项目**的前缀（小写、带尾 `/`）：`sr_general/game/…` 的 `sr_general/`
        self.nested = set()
        self._walk()
        self._find_nested_projects()

    def _find_nested_projects(self):
        """
        找出"自己带 `game/` 的子目录"= **另一份项目副本**。

        实测（用户 2026-10-05）：项目根下有个 `sr_general/`，里面是
        **同一份项目**的副本（表里 1386 行指向它）。这些文件若被当成
        "项目资源"扫进来，会以 `sr_general_angry` / `ling_angry` 这种
        **编出来的 ID** 写进表 —— 而 `ling_angry` 恰好就是
        "tag_attribute" 的写法，于是卡片里的 `ling_angry` 查到的是
        **那一行**（Tag 为空、图在 `cc_preview/` 里）→
        **立绘不显示、表情候选为空**。

        判据：路径里出现在**开头之外**的 `game/` 段（`sr_general/game/…`）。
        项目自己的 `game/` 永远在开头，不会被误判。
        """
        for rel in self.by_rel:
            parts = rel.split('/')
            for i, seg in enumerate(parts[1:], start=1):
                if seg == 'game':
                    self.nested.add('/'.join(parts[:i]) + '/')
                    break

    def nested_prefixes(self) -> set:
        """嵌套项目的前缀集合（扫描时整段跳过）。"""
        return self.nested

    def is_nested(self, rel_low: str) -> bool:
        """这一条相对路径是不是在**嵌套副本**里？"""
        low = str(rel_low or '').lower()
        for pref in self.nested:
            if low.startswith(pref):
                return True
        return False

    def _walk(self):
        import os
        root_str = str(self.root)
        try:
            # ⚠ 相对路径**用字符串拼**，不要 `Path.relative_to()`：
            # Windows 上一次 relative_to 实测 0.34 ms，5854 个文件就是 **2 秒**
            # （原先这里是"打开项目还要 4 秒"的大头）。
            stack = [(root_str, '')]
            while stack:
                cur, prefix = stack.pop()
                try:
                    with os.scandir(cur) as it:
                        for e in it:
                            try:
                                name = e.name
                                if e.is_dir(follow_symlinks=False):
                                    # 跳过 `tmp/`、`.git/`、`__pycache__/`、`build/`…
                                    # —— 它们永远不是游戏素材（扫描规则同源：
                                    # `_SKIP_SCAN_DIRS`），而且 `.git` 里可能有
                                    # 上万个对象文件，白走一遍就是几百毫秒。
                                    if name.lower() in _SKIP_SCAN_DIRS:
                                        continue
                                    stack.append((e.path, prefix + name + '/'))
                                elif e.is_file(follow_symlinks=False):
                                    key = (prefix + name).lower()
                                    self.by_rel[key] = Path(e.path)
                                    self.by_name.setdefault(
                                        name.lower(), []).append(key)
                            except OSError:
                                continue
                except OSError as e:            # noqa: BLE001 - 目录读不动就跳过
                    swallow('assets.asset_registry.FileWalkIndex._walk', e)
        except Exception as e:                  # noqa: BLE001
            swallow('assets.asset_registry.FileWalkIndex._walk', e)

    # ---------- 匹配 ----------

    def match(self, rel):
        """
        资源表里那一串 `Path` → 磁盘上的真实文件（找不到返回 None）。

        **候选顺序与 `resolve_thumb_source` 一致**（那是"唯一真源"的语义），
        只是把"逐个 `is_file()`"换成字典查找：

            base = 项目根：`text` / 去掉第一段 / `game/` + 去掉第一段 / `game/` + text

        ⚠ 一开始这里还有一条"**同名文件**全局唯一就认它"的兜底 —— 实测**太松**：
        `game/gui/dutui/frame 9/返回上一界面.png` 会被表里**别处**的同名文件
        认领，于是它被当成"已登记"、再也不会补进表（候选里就看不到它）。
        现在没有兜底；索引里没命中、且基准落在**项目外**（`root.parent` / cwd）
        时才退回磁盘探测 —— 那种行很少（真实项目 3123 行里 12 行）。
        """
        text = str(rel or '').strip().replace('\\', '/')
        if not text:
            return None
        while text.startswith('./'):
            text = text[2:]
        if text.startswith('/') or (len(text) > 1 and text[1] == ':'):
            return None                          # 绝对路径：索引不覆盖，不猜
        low = text.lower()
        parts = [p for p in low.split('/') if p not in ('', '.')]
        if not parts:
            return None
        cands = [low]
        if len(parts) > 1:
            cands.append('/'.join(parts[1:]))
            cands.append('game/' + '/'.join(parts[1:]))
        cands.append('game/' + low)
        for c in cands:
            hit = self.by_rel.get(c)
            if hit is not None:
                return hit
        return self._probe_outside(text, parts)

    def _probe_outside(self, text, parts):
        """
        索引没命中时，照顾"基准在项目外"的老行为（`root.parent` / cwd）。

        只在**未命中的行**上摸磁盘（几条 `is_file()`），顺序与
        `resolve_thumb_source` 的那两个基准一致。
        """
        try:
            bases = [self.root.parent, Path.cwd()]
        except Exception as e:                  # noqa: BLE001
            swallow('assets.asset_registry.FileWalkIndex._probe_outside', e)
            return None
        seen = set()
        for base in bases:
            cands = [base / text]
            if len(parts) > 1:
                cands.append(base / '/'.join(parts[1:]))
                cands.append(base / 'game' / '/'.join(parts[1:]))
            cands.append(base / 'game' / text)
            for c in cands:
                key = str(c).lower()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    if c.is_file():
                        return c
                except OSError:
                    continue
        return None

    def files(self):
        """所有文件的相对路径（小写 posix）→ Path。"""
        return self.by_rel
