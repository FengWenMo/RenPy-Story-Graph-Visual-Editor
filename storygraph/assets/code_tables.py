# -*- coding: utf-8 -*-
"""
项目里**任何**一张"编号 ↔ 资产"表：读 `对应.txt`，对到目录里的文件，产出词汇行。

为什么单独一个模块（2026-11-08）：

`sprite_codes` 只认**角色文件夹**里的表情/服装编号（`images/characters/<tag>/
对应.txt`）。可真实项目 `sr_general` 的 `game/bgm/对应.txt` 是同一件事的音乐
版 —— 剧本写 `# bgm13`，13 号是「无法言说的事情……」，表里一行一个号 —— 而
映射里只有 `outfit_codes` / `expression_codes` 两段，"导入过的资产都能映射"
这句话对音乐就不成立了。

这里把"编号表"这件事抽成**通用的**：

  · **哪个目录里有 `对应.txt`，就按那个目录的资产类型读一张表**；
  · 号后面的名字（含 `……` 截断、`（备注）`）对到目录里真实的文件名；
  · 对不上的、没号的、同一首挂两个号的，都写进 `notes` —— 缺口要报出来，
    **不替人编号**（编错了比空着更难发现）。

`sprite_codes` 那套（表情/服装）仍归它自己：角色文件夹这里**跳过**，
免得同一条在「表情编号」和「中文叫法」两段各登记一次。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: 目录里叫这两个名字的文件就是一张编号表（与 `sprite_codes` 同一份口径）。
TABLE_NAMES = ('对应.txt', '中英对应.txt')

#: 不进的目录（与 `vocabulary._project_texts` 同一份口径）。
SKIP_DIRS = frozenset({'cache', 'saves', 'libs', 'build', '__pycache__', 'tl'})

#: 哪些后缀算"这个目录里的资产"（别把 .pyc / .txt / 备份算进来）。
ASSET_EXTS = frozenset({
    '.mp3', '.ogg', '.wav', '.opus', '.flac', '.m4a', '.aac',
    '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif',
})

#: 一行编号的写法：`1——Empt1y` / `7 不在意-indifferent` / `2. 名字`。
#: 分隔符认全角/半角破折号、点号、顿号、右括号和空白（真实项目四种都写）。
_LINE_RE = re.compile(r'^\s*(\d{1,3})\s*(?:[—–－~～-]{1,3}|[.、)]|\s+)\s*(.*)$')

#: 名字尾巴上的备注（`青蓝实验室（剧本未出现7）`）和截断号（`……`）。
_TAIL_NOTE_RE = re.compile(r'[（(][^（()）]*[）)]\s*$')
_TRAIL_ELLIPSIS_RE = re.compile(r'(?:…+|\.{2,})\s*$')

#: 剧本注释里的编号（`# bgm13` / `# se9` / `# bg12.3`），只用来**报缺口**。
_LIVE_RE = {
    'music': re.compile(r'(?<![A-Za-z])(?:bgm|BGM)\s?(\d{1,3})(?!\d)'),
    'sound': re.compile(r'(?<![A-Za-z])(?:se|SE)\s?(\d{1,3})(?!\d)'),
    'bg': re.compile(r'(?<![A-Za-z])(?:bg|BG)\s?(\d{1,2}(?:[._]\d{1,2})?)'
                     r'(?!\d)'),
}


# ---------------------------------------------------------------------------
#  一、读一张表
# ---------------------------------------------------------------------------

def parse_line(line) -> tuple:
    """
    一行 → `(编号, 名字)`；认不出返回 `('', '')`。

    空号（`8——`）**算认出来**：名字是空串 —— 那是"预留了号还没填"，
    与"这行不是编号"必须分开（否则缺口就报不出来）。
    """
    text = str(line if line is not None else '').strip()
    if not text or text.startswith('#'):
        return ('', '')
    m = _LINE_RE.match(text)
    if not m:
        return ('', '')
    return (m.group(1).lstrip('0') or '0', m.group(2).strip())


def parse_table(text) -> list:
    """
    一段 `对应.txt` 的文字 → `[(编号, 名字, 行号), …]`（保序，重复号只留第一条）。

    行号从 1 数，给人看的（"来自 对应.txt 第 9 行"）；重复号不静默丢，
    交给 `scan_project` 说一句"第 5、7 号都写着同一个名字"。
    """
    out = []
    seen = {}
    for lineno, raw in enumerate(str(text or '').splitlines(), start=1):
        code, label = parse_line(raw)
        if not code:
            continue
        if code in seen:
            seen[code].append(lineno)
            continue
        seen[code] = [lineno]
        out.append((code, label, lineno))
    return out


def clean_label(label) -> str:
    """
    编号表里的名字 → 能拿去对文件名的那个（去掉尾部备注和 `……`）。

    `青蓝实验室（剧本未出现7）` → `青蓝实验室`；
    `冬天的落日后……` → `冬天的落日后`（真文件叫 `冬天的落日后，我在风中思念某人`）。
    """
    text = str(label or '').strip()
    text = _TAIL_NOTE_RE.sub('', text).strip()
    text = _TRAIL_ELLIPSIS_RE.sub('', text).strip()
    return text


def match_asset(label, names) -> tuple:
    """
    表里的名字 → 目录里的文件名 → `(名字, 怎么对上的)`。

    `怎么对上的`：`exact` / `prefix` / `contains` / `missing` / `ambiguous`。
    **只认唯一命中**：两个文件都以同一个前缀开头时返回空 —— 挑一个更像
    在猜，猜错的那一首从此就"有号了"，比空着更坏。
    """
    want = clean_label(label)
    pool = [str(n) for n in (names or ()) if str(n)]
    if not want:
        return ('', 'missing')
    if want in pool:
        return (want, 'exact')
    pref = [n for n in pool if n.startswith(want)]
    if len(pref) == 1:
        return (pref[0], 'prefix')
    if pref:
        return ('', 'ambiguous')
    inside = [n for n in pool if want in n]
    if len(inside) == 1:
        return (inside[0], 'contains')
    return ('', 'missing')


# ---------------------------------------------------------------------------
#  二、这个目录是哪类资产
# ---------------------------------------------------------------------------

def kind_for_folder(folder_rel) -> str:
    """
    项目相对目录 → 词汇表的 `kind`（`music` / `sound` / `voice` / `bg` / `''`）。

    认不出就返回空串（**不默认成任何一种**）：默认成 music 会把一堆
    认不出的表塞进音乐那一类，人还以为是工具认得的。
    """
    parts = [p.lower() for p in
             str(folder_rel or '').replace('\\', '/').split('/') if p]
    if 'characters' in parts and 'images' in parts:
        # 角色文件夹归 `sprite_codes`（表情编号 / 服装代号两段）。
        return ''
    if 'sfx' in parts or 'sound' in parts or 'sounds' in parts:
        return 'sound'
    if 'voice' in parts or 'voices' in parts:
        return 'voice'
    if 'bgm' in parts or 'music' in parts:
        return 'music'
    if 'bg' in parts or 'background' in parts or 'backgrounds' in parts:
        return 'bg'
    return ''


# ---------------------------------------------------------------------------
#  三、扫一个项目
# ---------------------------------------------------------------------------

@dataclass
class Table:
    """一张读出来的编号表。"""

    path: Path
    folder: str          # 表所在目录（项目相对，posix）
    kind: str
    entries: list        # [(编号, 名字, 行号), …]


@dataclass
class Scan:
    """一次扫描的结果：能直接塞进 `vocabulary` 的行 + 要说的话。"""

    rows: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def find_tables(project_root) -> list:
    """项目里**所有**非角色文件夹的 `对应.txt` → `[Table, …]`（按路径排序）。"""
    raw = str(project_root or '').strip()
    root = Path(raw)
    game = root / 'game'
    if not raw or not game.is_dir():
        return []
    out = []
    for path in sorted(game.rglob('*')):
        if path.name not in TABLE_NAMES:
            continue
        try:
            folder = path.parent.relative_to(root).as_posix()
        except ValueError:
            continue
        if SKIP_DIRS & {p.lower() for p in folder.split('/')}:
            continue
        kind = kind_for_folder(folder)
        if not kind:
            continue
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            # 读不了就不算一张表（窄捕获：`is_file` / `read_text` 只会抛 OSError）。
            continue
        entries = parse_table(text)
        if entries:
            out.append(Table(path, folder, kind, entries))
    return out


def scan_project(project_root) -> Scan:
    """
    扫项目里的编号表 → 词汇行 + 缺口说明（纯读，不改任何文件）。

    行的形状就是 `vocabulary` 段的一条：
    `{'kind', 'id', 'code', 'label', 'note'}`；`id` 是剧本里会写的那个写法
    （`bgm/嗯？.mp3`，去掉开头的 `game/`）。

    没有表的音频种类也要说一句：剧本写 `# se6`，项目里却没有音效的
    `对应.txt` —— 那 6 号**永远对不上文件**，静悄悄跳过等于假装它没事。
    """
    scan = Scan()
    raw = str(project_root or '').strip()
    root = Path(raw)
    tables = find_tables(raw)
    live = _live_codes(root)
    for table in tables:
        _scan_table(scan, root, table, live)
    covered = {table.kind for table in tables}
    for kind in ('music', 'sound', 'voice'):
        codes = live.get(kind) or set()
        if not codes or kind in covered:
            continue
        shown = '、'.join(sorted(codes, key=_num_key)[:8])
        more = f' 等 {len(codes)} 个' if len(codes) > 8 else ''
        scan.notes.append(
            f'剧本注释在用 {kind} 号（{shown}{more}），但项目里没有这类资产的 '
            '对应.txt —— 这些号对不上文件')
    return scan


def _scan_table(scan: Scan, root: Path, table: Table, live: dict) -> None:
    """一张表：号 → 文件，落成行；对不上的写进 `notes`。"""
    folder = root / table.folder
    assets = _assets_in(folder)
    stems = {stem: path for stem, path in assets.items()}
    table_name = f'{table.folder}/{table.path.name}'.replace('game/', '', 1)
    used = {}
    blanks = []
    unmatched = []
    twinned = []
    for code, label, lineno in table.entries:
        if not clean_label(label):
            blanks.append(code)
            continue
        stem, how = match_asset(label, stems)
        if not stem:
            what = ('目录里有两个文件都对得上这个名字' if how == 'ambiguous'
                    else '目录里没有这个文件')
            unmatched.append(f'{table_name} 第 {lineno} 行：第 {code} 号'
                             f'「{label}」{what}')
            continue
        ident = _asset_id(root, stems[stem])
        if stem in used:
            twinned.append(f'第 {used[stem]["code"]} 号和第 {code} 号都指向'
                           f'同一个文件（{Path(stem).name}）'
                           f'—— 同一首挂了两个号')
            continue
        used[stem] = {'kind': table.kind, 'id': ident, 'code': code,
                      'label': str(label).strip(),
                      'note': f'来自 {table_name} 第 {lineno} 行'}
    scan.rows.extend(used.values())

    # 先说"剧本已经在用、表里却空着"的 —— 那是眼下的卡点；再报格式问题。
    live_codes = live.get(table.kind) or set()
    if live_codes:
        named = {code for code, label, _line in table.entries
                 if clean_label(label)}
        reserved = [c for c in sorted(live_codes, key=_num_key)
                    if c not in named]
        if reserved:
            scan.notes.append(
                '剧本注释在用、表里却还是空号（或根本没有）的 '
                f'{table.kind} 号：' + '、'.join(reserved))
    if blanks:
        scan.notes.append(
            f'{table_name}：{len(blanks)} 个空号（' + '、'.join(blanks)
            + '）—— 预留的号还没填名字')
    scan.notes.extend(unmatched)
    scan.notes.extend(twinned)
    missing = [stem for stem in stems if stem not in used]
    if missing:
        names = '、'.join(str(s) for s in missing[:6])
        more = f' 等 {len(missing)} 个' if len(missing) > 6 else ''
        scan.notes.append(
            f'{table.folder}/ 里还有 {len(missing)} 个文件没有编号：'
            f'{names}{more}')
        for stem in missing:
            scan.rows.append({'kind': table.kind,
                              'id': _asset_id(root, stems[stem]),
                              'code': '', 'label': '',
                              'note': f'来自 {table_name}：没有编号'})


def _assets_in(folder: Path) -> dict:
    """目录里（第一层）的资产 → `{文件名（不含后缀）: 路径}`。"""
    out = {}
    try:
        items = sorted(p for p in folder.iterdir() if p.is_file())
    except OSError:
        # 目录读不了 = 这个目录没有资产（窄捕获，不吞别的错）。
        return out
    for path in items:
        if path.suffix.lower() in ASSET_EXTS:
            out.setdefault(path.stem, path)
    return out


def _asset_id(root: Path, path: Path) -> str:
    """文件 → 剧本里的写法（项目相对、去掉开头的 `game/`、正斜杠）。"""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return rel[5:] if rel.startswith('game/') else rel


def _live_codes(root: Path) -> dict:
    """剧本注释里出现过的编号 → `{'music': {'13', …}, …}`（只用来报缺口）。"""
    found = {kind: set() for kind in _LIVE_RE}
    game = root / 'game'
    if not game.is_dir():
        return found
    for path in game.rglob('*.rpy'):
        if SKIP_DIRS & {p.lower() for p in path.parts}:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            # 读不了就少一条证据（窄捕获；坏编码由 errors='replace' 兜住）。
            continue
        for line in text.splitlines():
            if not line.lstrip().startswith('#'):
                continue
            for kind, pattern in _LIVE_RE.items():
                for m in pattern.finditer(line):
                    found[kind].add(str(m.group(1)).lstrip('0') or '0')
    return found


def _num_key(code) -> tuple:
    """编号排序：纯数字按数值，其余按文字（`bg9.2` 这类不在本模块里）。"""
    text = str(code or '')
    return (0, int(text), '') if text.isdigit() else (1, 0, text)


__all__ = ['TABLE_NAMES', 'SKIP_DIRS', 'ASSET_EXTS', 'Table', 'Scan',
           'parse_line', 'parse_table', 'clean_label', 'match_asset',
           'kind_for_folder', 'find_tables', 'scan_project']
