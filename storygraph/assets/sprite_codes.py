# -*- coding: utf-8 -*-
"""
storygraph.assets.sprite_codes
==============================
**立绘代号表**：`B7` 这种"服装字母 + 表情编号"的写法 ↔ 剧本里真正要写的属性。

用户 2026-10-31（原话）::

    你看看真实项目之中的注释：#B7 这些注释，B对应的是服装，而7对应的是这个
    角色的第7号表情，那请问我该如何通过你这个映射表把 lan swim_suit angry
    映射为 lan B 7 呢……？不太方便吧？？？你再想想……？

所以这一层要回答的**只有一件事**：`lan swim_suit angry` ⇄ `lan B7`。

## 真实项目里的三处事实（都是读出来的，不是猜的）

1. `game/images/characters/<角色>/对应.txt`（有的项目叫 `中英对应.txt`）
   一行一个表情：`7 不在意-indifferent` / `表情1-哈哈 — haha` /
   `2 close_happy-闭眼微笑` —— **三种写法都有**，谁前谁后不固定，
   所以这里按"哪一半是纯 ASCII 标识符"判，不按位置。
2. `game/definitions.rpy` 的 `layeredimage <角色>:` 里
   `group expressions` / `group outfit` 各自 `attribute xxx:` 声明了
   合法的属性名。⚠ `attribute happy default:` 带 `default` 也算。
3. 剧本注释 `# 表情B7` 后面紧跟的那条 `show`，就是这条代号的**证据**：
   `# 表情B6 拉进` + `show ye base_handdown happy` → `B`=`base_handdown`、`6`=`happy`。

第 3 条是自动建表的钥匙 —— 字母和编号的关系**不用人去抄美术需求表**，
从剧本自己身上数出来即可；数不出来（剧本里没出现过）就**留空并说清楚**，
绝不硬编（铁律：不猜、不静默）。

## 两种写法都要能认

    lan swim_suit angry   →  B7    （人话属性 → 代号）
    lan B7                →  show lan base_swimsuit angry（代号 → 剧本）

所以表里存的是三元组 `角色 / 代号 / 剧本里怎么写`，换算只是查表。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 服装代号的字母表（美术需求表就是 A/B/C…；用完就照写，不自动扩展）。
LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

#: 映射里两段的键名（`mapping_edit.SPECS` / Excel 两张表 / JSON 顶层都用它）。
SECTION_OUTFITS = 'outfit_codes'
SECTION_EXPRESSIONS = 'expression_codes'

#: 一段里一行的字段（顺序 = 表里列的顺序）。
OUTFIT_FIELDS = ('char', 'code', 'attr', 'label')
EXPRESSION_FIELDS = ('char', 'no', 'attr', 'label')

#: 表情 / 服装在 layeredimage 里可能的分组名（小写开头比较）。
_EXPR_GROUPS = ('expr', 'face', 'eme', '表情')
_OUTFIT_GROUPS = ('outfit', 'clothes', 'body', '服装', '服')

#: 注释里的代号：`B7` / `B 7` / `A10`（前面不能粘着字母数字，免得 `bg9` 混进来）。
_CODE_RE = re.compile(r'(?<![A-Za-z0-9 _])([A-Za-z])\s?(\d{1,2})(?![\d.])')

#: `show <tag> <属性...>`（`show aa ye hi` 这种写错的由调用方过滤）。
_SHOW_RE = re.compile(r'^(\s*)show\s+(\w+)(.*)$')

#: 纯粹的标识符（`indifferent` / `base_hand_up`）。
_WORD_RE = re.compile(r'^[A-Za-z_]\w*$')

#: `show` 后面这些是**参数**不是属性。
_NOT_ATTRS = frozenset({'at', 'with', 'behind', 'onlayer', 'as', 'zorder',
                        'tag', 'expression', 'transition'})


# ---------------------------------------------------------------------------
#  归一化 / 小工具
# ---------------------------------------------------------------------------

def norm(text) -> str:
    """
    比对用的归一写法：小写、去掉一切非字母数字（含中文换成小写），`base_` 前缀去掉。

    `swim_suit` / `base_swimsuit` / `Swimsuit` → `swimsuit`；
    `泳装` → `泳装`。**只用于比对，绝不写回文件**（写回去必须是原样）。
    """
    got = str(text if text is not None else '').strip().lower()
    got = got.removeprefix('base_').removeprefix('base')
    return re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', got)


def chars_in(data) -> list:
    """映射两段里出现过的角色写法（保序去重）—— 界面的例子 / 换算都靠它。"""
    seen = []
    for section in (SECTION_OUTFITS, SECTION_EXPRESSIONS):
        for row in data.get(section) or ():
            tag = str((row or {}).get('char') or '').strip()
            if tag and tag not in seen:
                seen.append(tag)
    return seen


def _sorted_chars(data) -> list:
    """旧名字（只在本模块里用）：等于 `chars_in`。"""
    return chars_in(data)


# ---------------------------------------------------------------------------
#  一、读"哪一号是哪个表情"（对应.txt 的三种写法）
# ---------------------------------------------------------------------------

#: 行尾可能跟着的那张图（`1 闭眼笑-close_happy images\characters\lan\x.png`）。
_TRAIL_PATH_RE = re.compile(r'\s+\S*[\\/]\S*\.(?:png|jpe?g|webp|bmp|gif)\s*$',
                            re.IGNORECASE)


def parse_expression_line(line) -> tuple:
    """
    一行 → `(编号, 属性, 中文)`；认不出返回 `('', '', '')`。

    四种真实写法都要吃：

        7 不在意-indifferent                        （lan / ye 的 对应.txt）
        2 close_happy-闭眼微笑                      （ling：属性在前）
        表情1-哈哈 — haha                           （xing：破折号是 —）
        1 闭眼笑-close_happy images\\characters\\lan\\close_happy.png
                                                    （lan 的 中英对应.txt：
                                                     行尾还带着一张图的路径）

    最后那种第一版没认出来 —— 于是 lan 的两份编号表只剩一份，
    "两份对不上"这件真事就**看不到了**。
    """
    text = str(line if line is not None else '').strip()
    if not text or text.startswith('#'):
        return ('', '', '')
    text = re.sub(r'^表情\s*', '', text)
    m = re.match(r'^(\d{1,3})\s*[-.、)]?\s*(.+)$', text)
    if not m:
        return ('', '', '')
    number = m.group(1).lstrip('0') or '0'
    rest = _TRAIL_PATH_RE.sub('', m.group(2).strip()).strip()
    parts = [p.strip() for p in re.split(r'\s*[-—–－~～]\s*', rest) if p.strip()]
    if not parts:
        return ('', '', '')
    # 纯 ASCII 标识符的那一半才是属性（中文那半是给人看的名）。
    ascii_parts = [p for p in parts if _WORD_RE.match(p)]
    if not ascii_parts:
        return ('', '', '')
    attr = ascii_parts[-1]
    label = ''
    for part in parts:
        if part != attr and not _WORD_RE.match(part):
            label = part.strip()
            break
    return (number, attr, label)


def parse_expression_text(text) -> dict:
    """一段 `对应.txt` 的文字 → `{编号: (属性, 中文)}`（认不出的行跳过）。"""
    table = {}
    for line in str(text or '').splitlines():
        number, attr, label = parse_expression_line(line)
        if number and attr:
            table[number] = (attr, label)
    return table


def read_expression_tables(char_dir) -> tuple:
    """
    一个角色文件夹里的**所有**编号表 → `({文件名: {编号: (属性, 中文)}}, [冲突])`。

    ⚠ 真实项目踩到过：`对应.txt` 和 `中英对应.txt` 的编号**不一样**
    （lan 的 8 一份写 `angry`、一份写 `angry2`）。这里不替人裁决 ——
    每份都原样交回去，谁跟剧本注释对得上由调用方（`scan_project`）用票数挑。
    """
    folder = Path(str(char_dir or ''))
    found = {}
    if not folder.is_dir():
        return ({}, [])
    try:
        files = sorted(p for p in folder.glob('*.txt') if p.is_file())
    except OSError:
        return ({}, [])
    for path in files:
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        table = parse_expression_text(text)
        if table:
            found[path.name] = table
    if len(found) < 2:
        return (found, [])
    conflicts = []
    names = list(found)
    numbers = sorted({n for table in found.values() for n in table}, key=_num_key)
    for number in numbers:
        cells = {name: found[name][number] for name in names
                 if number in found[name]}
        if len({attr for attr, _label in cells.values()}) < 2:
            continue
        detail = '、'.join(f'{name} 写 {attr}' for name, (attr, _l)
                          in sorted(cells.items()))
        conflicts.append(f'第 {number} 号表情两份表对不上（{detail}）')
    return (found, conflicts)


def agreement(table, votes) -> int:
    """这份编号表和剧本注释对上了几处（`votes` 是 `{(编号, 属性): 次数}`）。"""
    return sum(count for (number, attr), count in votes.items()
               if table.get(number, ('', ''))[0] == attr)


def _num_key(text):
    try:
        return (0, int(text))
    except (TypeError, ValueError):
        return (1, str(text))


# ---------------------------------------------------------------------------
#  二、读 layeredimage（哪些属性是表情、哪些是服装）
# ---------------------------------------------------------------------------

def parse_layeredimage(text) -> dict:
    """
    一段 rpy 的文字 → `{角色: {'expr': [...], 'outfit': [...]}}`。

    只认 `layeredimage <tag>:` 下面、按 `group` 归类的 `attribute`。
    ⚠ `attribute happy default:`（带 `default`）**必须**算进去 ——
    第一版漏了它，结果把 `happy` 当成了服装，整张表全歪。
    """
    out = {}
    current = ''
    group = ''
    for line in str(text or '').splitlines():
        head = re.match(r'^layeredimage\s+(\w+)\s*:\s*$', line)
        if head:
            current, group = head.group(1), ''
            out.setdefault(current, {'expr': [], 'outfit': []})
            continue
        if current and line and not line[:1].isspace():
            current, group = '', ''
            continue
        if not current:
            continue
        g = re.match(r'^\s+group\s+(\w+)', line)
        if g:
            group = g.group(1).lower()
            continue
        a = re.match(r'^\s+attribute\s+(\w+)(?:\s+\w+)*\s*:', line)
        if not a:
            continue
        name = a.group(1)
        slot = out[current]
        if group.startswith(_OUTFIT_GROUPS):
            if name not in slot['outfit']:
                slot['outfit'].append(name)
        elif group.startswith(_EXPR_GROUPS):
            if name not in slot['expr']:
                slot['expr'].append(name)
    return out


# ---------------------------------------------------------------------------
#  三、从剧本注释里数"字母 ↔ 服装"、"编号 ↔ 表情"
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Hit:
    """一条代号注释 + 它后面那条 `show` 的现场。"""

    where: str        # `chapter1_1.rpy:265`
    char: str
    letter: str
    number: str
    expr: str         # 当时的表情属性（`''` = 这条 show 没换表情）
    outfit: str       # 当时的服装属性（`''` = 判断不出）


def scan_script_text(text, *, name='', known=None) -> list:
    """
    一段剧本 → 代号证据列表。

    `known` 是 `{角色: {'expr': {...}, 'outfit': {...}}}`（`parse_layeredimage`
    的结果）；给了就**只认声明过的属性**，不给就按"位置"经验判断。

    规则（按 Ren'Py layeredimage 的真实语义：**一组只有一个属性**）：

    · `show <角色> <属性...>` 里属于 expressions 组的**替换**表情，
      属于 outfit 组的**替换**服装，都更新"当前状态"；
    · 注释里的代号取**最后**一个（`# 表情B6换到表情B7` 说的是换到 B7）；
    · 注释后面那条 show 更新完状态后，记一条证据。
    """
    hits = []
    state = defaultdict(dict)         # 角色 -> {'expr': attr, 'outfit': attr}
    pending = None                    # (letter, number)
    known = {str(k): v for k, v in (known or {}).items()}
    # 所有角色声明过的属性（跨角色一起看）：`show ye uh` 里的 `uh` 是 **lan 的
    # 表情**，是剧本手误，不是 ye 的服装 —— 别让它污染服装投票。
    declared = set()
    for groups in known.values():
        declared |= set(groups.get('expr') or ())
        declared |= set(groups.get('outfit') or ())
    for lineno, raw in enumerate(str(text or '').splitlines(), start=1):
        line = raw.strip()
        if line.startswith('#'):
            codes = _CODE_RE.findall(line)
            if codes:
                pending = (codes[-1][0].upper(), codes[-1][1])
            continue
        m = _SHOW_RE.match(raw)
        if not m:
            tag = ''
            if line.startswith(('hide ', 'scene ')):
                tag = line.split()[1] if len(line.split()) > 1 else ''
            if tag:
                state.pop(tag, None)
                pending = None
            elif line:
                pending = None
            continue
        char, rest = m.group(2), m.group(3)
        words = _attr_words(rest)
        # `show aa ye hi`（真实项目里的手误）：第一个词不是角色名就不当证据。
        if known and char not in known:
            if words and words[0] in known:
                char, words = words[0], words[1:]
            else:
                pending = None
                continue
        groups = known.get(char) or {}
        exprs = set(groups.get('expr') or ())
        outfits = set(groups.get('outfit') or ())
        slot = state[char]
        shown_expr = [w for w in words if w in exprs] if exprs else []
        shown_out = [w for w in words if w in outfits] if outfits else []
        stray = [w for w in words if w not in exprs and w not in outfits]
        if shown_expr:
            slot['expr'] = shown_expr[-1]
        if shown_out:
            slot['outfit'] = shown_out[-1]
        elif stray:
            # 声明里没有、剧本里却写了，且**哪个角色都没声明过**（真实项目：
            # `aa` = A 号服装的标记，常和表情写在同一行：`show ye aa happy`）。
            # `uh` 这种"别的角色的表情"不算（那是剧本手误）。
            markers = [w for w in stray if w not in declared]
            if markers:
                slot['outfit'] = markers[-1]
        if not pending:
            continue
        letter, number = pending
        pending = None
        hits.append(Hit(f'{name}:{lineno}' if name else f'第 {lineno} 行',
                        char, letter, number,
                        slot.get('expr', ''), slot.get('outfit', '')))
    return hits


def _attr_words(rest) -> list:
    """`show` 后面的词里哪些是属性（`at i11` / `with dissolve` 这些不算）。"""
    words = str(rest or '').split()
    for stop in ('at', 'with', 'behind', 'onlayer', 'as', 'zorder'):
        if stop in words:
            words = words[:words.index(stop)]
    return [w for w in words if _WORD_RE.match(w) and w not in _NOT_ATTRS]


# ---------------------------------------------------------------------------
#  四、扫描整个项目 → 两段的行
# ---------------------------------------------------------------------------

@dataclass
class Scan:
    """自动建表的结果（两段的行 + 扫描时要说的话）。"""

    outfits: list = field(default_factory=list)
    expressions: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def scan_project(project_root) -> Scan:
    """
    按项目现状**自动建表**：`对应.txt` 给编号，剧本注释给字母。

    认不出来的（剧本里没出现过那种号）**留空**，并在 `notes` 里点名，
    不拿"顺序"去硬凑一个字母 —— 凑错了比空着更难发现。
    """
    raw_root = str(project_root or '').strip()
    root = Path(raw_root)
    scan = Scan()
    if not raw_root or not root.is_dir():
        scan.notes.append('没有打开项目 —— 建不了表。')
        return scan
    texts = _script_texts(root)
    known = _known_tags(texts)
    char_dirs = _char_dirs(root)
    for tag in char_dirs:
        known.setdefault(tag, {'expr': [], 'outfit': []})
    hits_all = []
    for rel, text in texts.items():
        hits_all.extend(scan_script_text(text, name=rel.rsplit('/', 1)[-1],
                                         known=known))
    chars = _character_tags(texts, known, char_dirs) | {h.char for h in hits_all}
    known = {tag: groups for tag, groups in known.items() if tag in chars}

    # -- 证据 ---------------------------------------------------------
    hits = [h for h in hits_all if h.char in chars]

    # -- 表情：哪份编号表和剧本对得上 ----------------------------------
    _scan_expressions(scan, known, char_dirs, hits)
    # -- 服装：字母从剧本注释里数 --------------------------------------
    _scan_outfits(scan, known, hits)
    scan.outfits.sort(key=lambda r: (r['char'], r['code'] or 'zz', r['attr']))
    scan.expressions.sort(key=lambda r: (r['char'], _num_key(r['no'])))
    return scan


def _known_tags(texts: dict) -> dict:
    """剧本里的 `layeredimage` 声明 → `{tag: {'expr': [...], 'outfit': [...]}}`。"""
    known = {}
    for rel, text in texts.items():
        for tag, groups in parse_layeredimage(text).items():
            slot = known.setdefault(tag, {'expr': [], 'outfit': []})
            for key in ('expr', 'outfit'):
                for name in groups[key]:
                    if name not in slot[key]:
                        slot[key].append(name)
    return known


def _char_dirs(root: Path) -> dict:
    """`game/images/characters/<tag>/` → `{tag: 目录}`（读不了 = 没有角色文件夹）。"""
    char_dirs = {}
    base = root / 'game' / 'images' / 'characters'
    if base.is_dir():
        try:
            for child in sorted(base.iterdir()):
                if child.is_dir():
                    char_dirs[child.name] = child
        except OSError as e:                   # 目录读不了 = 没有角色文件夹
            swallow('assets.sprite_codes.scan_project.char_dirs', e)
    return char_dirs


def _character_tags(texts: dict, known: dict, char_dirs: dict) -> set:
    """只留下**真的是一张立绘**的那些 tag。

    `layeredimage lan_cc` 是预览图用的变体，不是剧本里 show 的角色（真实项目里
    它会让表里凭空多出四行）。判据：有 `images/characters/<tag>/` 文件夹，
    或者剧本里 `show` 过它。
    """
    shown = set()
    for text in texts.values():
        shown |= set(re.findall(r'^\s*show\s+(\w+)', text, re.MULTILINE))
    return set(char_dirs) | (shown & set(known))


def _scan_expressions(scan: Scan, known: dict, char_dirs: dict,
                      hits: list) -> None:
    """表情：按剧本注释给每份编号表投票，选出对得最多的那份，逐号落表。"""
    for tag in sorted(known):
        folder = char_dirs.get(tag)
        if folder is None:
            continue
        files, conflicts = read_expression_tables(folder)
        if not files:
            continue
        votes = Counter()
        for hit in hits:
            if hit.char == tag and hit.expr:
                votes[(hit.number, hit.expr)] += 1
        # 有几份编号表就比几份：跟剧本注释对得最多的那份才用（并列取先读到的）。
        best_name = max(files, key=lambda name: (agreement(files[name], votes),
                                                 -list(files).index(name)))
        table = files[best_name]
        if conflicts and len(files) > 1:
            scan.notes.append(
                f'{tag}：{len(conflicts)} 处编号两份表对不上；表里用的是'
                f'「{best_name}」——它和剧本注释对上了 '
                f'{agreement(table, votes)} 处，'
                + '、'.join(f'「{n}」{agreement(t, votes)} 处'
                           for n, t in files.items() if n != best_name))
        for conflict in conflicts[:3]:
            scan.notes.append(f'{tag}：{conflict}')
        # 表选好了还要问一句**它对得上剧本吗**。
        #
        # 真实项目（2026-11-01）：`对应.txt` 被重新编号（`angry2` 从 8 号挪到
        # 19 号），可剧本注释还按老编号写 —— lan 的 26 处证据里这张表只对上
        # **5** 处，老编号那份（`中英对应.txt`）对上 16 处，而老编号那份只剩在
        # 另一个镜像项目里。两份表打架的提示只报给"手上有两份"的人；
        # 只剩一份时它一声不响，整张表就静默地错一半。
        #
        # 这里**不替人裁决**（阈值只是"值得说一句"，不改任何一行数据），
        # 把数字原样交出去：注释按哪套编号写，表就该是哪套。
        total = sum(votes.values())
        if total >= 2:
            got = agreement(table, votes)
            if got * 2 < total:
                scan.notes.append(
                    f'{tag}：编号表「{best_name}」和剧本注释只对上 {got}/{total} '
                    '—— 脚本里可能混用了两套编号（说话的是注释，不是文件日期）')
        for number in sorted(table, key=_num_key):
            attr, label = table[number]
            scan.expressions.append({'char': tag, 'no': number,
                                     'attr': attr, 'label': label})
        # 剧本注释里出现、编号表里却没有的号 —— 说明某一边漏了。
        for (number, attr), count in sorted(votes.items(),
                                            key=lambda kv: _num_key(kv[0][0])):
            if number in table or count < 2:
                continue
            scan.expressions.append({'char': tag, 'no': number, 'attr': attr,
                                     'label': f'（剧本注释里出现 {count} 次，'
                                              '编号表里没有这个号）'})
            scan.notes.append(f'{tag}：剧本注释写着第 {number} 号是 {attr}，'
                              '但编号表里没有这个号（已补一行，请核对）')


def _scan_outfits(scan: Scan, known: dict, hits: list) -> None:
    """服装：字母从剧本注释里数（票数弱的留空，只出现一次的只提醒）。"""
    # 引擎（`layeredimage` 的 outfit 组）认哪些词 —— 判"剧本里写的这身衣服，
    # 引擎到底有没有"。空列表 = 这个项目没有可读的 layeredimage 声明，那就
    # **无从判断**，一句都不多说（老项目行为不变）。
    declared = {tag: [str(a) for a in ((groups or {}).get('outfit') or ())]
                for tag, groups in (known or {}).items()}
    votes = defaultdict(Counter)
    for hit in hits:
        if hit.outfit:
            votes[(hit.char, hit.letter)][hit.outfit] += 1
    claimed = {}                      # 角色 -> {属性: (字母, 票数)}
    for (tag, letter), counter in sorted(votes.items()):
        outfit, count = counter.most_common(1)[0]
        if count < 2:
            # 只出现过一次：可能是手误（真实项目里有 `show lan p happy`），
            # 宁可空着让人填，也不把一个字母钉在错的地方。
            scan.notes.append(f'{tag}：剧本里「{letter}」号服装只对上一次'
                              f'（{outfit}），证据太弱，代号留空了')
            continue
        taken = claimed.setdefault(tag, {})
        if outfit in taken:
            other_letter, other_count = taken[outfit]
            if other_count >= count:
                scan.notes.append(
                    f'{tag}：「{letter}」和「{other_letter}」都指向 {outfit}，'
                    f'按票数留了「{other_letter}」，「{letter}」留空了')
                continue
            taken[outfit] = (letter, count)
            scan.notes.append(
                f'{tag}：「{letter}」和「{other_letter}」都指向 {outfit}，'
                f'按票数留了「{letter}」，「{other_letter}」留空了')
        else:
            taken[outfit] = (letter, count)
        label = f'剧本里出现 {count} 处'
        allowed = declared.get(tag) or []
        if allowed and outfit not in allowed:
            # 剧本里写了、`layeredimage` 里没有 —— **行留着**（人是照它写剧本
            # 的，藏起来等于骗人），但必须当场说清。真实项目 `sr_general` 就是
            # 这么来的：`aa` 在 33 处 `show` 里当"普通"那身衣服用，注释也写着
            # `# 表情A14`，可四个 `layeredimage` 里一个 `attribute aa` 都没有
            # —— Ren'Py 8.4 的 lint 原话是 `'lan aa happy' is not an image`。
            label += ' · ⚠ 引擎里没有这身衣服'
            scan.notes.append(
                f'{tag}：剧本里 {count} 处写了「{outfit}」，但 `layeredimage` 的 '
                f'outfit 组里没有它（有：{"、".join(allowed)}）—— Ren\'Py 会报 '
                f'"not an image"；要么补 `attribute {outfit}`，要么把剧本改掉')
        # 只提有分量的另一种说法（一次的大多是手误，列出来只会让人以为要处理）
        runner = [(k, v) for k, v in counter.most_common()[1:] if v >= 2]
        if runner:
            label += '；另有 ' + '、'.join(f'{k}={v}' for k, v in runner)
        scan.outfits.append({'char': tag, 'code': letter, 'attr': outfit,
                             'label': label})
    # 剧本里没出现过的服装：留一行空代号，等人照美术需求表填
    used = {(row['char'], row['attr']) for row in scan.outfits}
    for tag in sorted(declared):
        for attr in declared.get(tag) or ():
            if (tag, attr) in used:
                continue
            scan.outfits.append({'char': tag, 'code': '', 'attr': attr,
                                 'label': '剧本里没出现过这个服装的代号'})


def _script_texts(root: Path) -> dict:
    """
    项目里**作者写的**剧本 rpy 原文（键 = 项目相对路径 posix）。

    ⚠ 不许直接 `game.rglob('*.rpy')` 全收（2026-10-31 用户实测的 P0）：

      同一章旁边多一个 `chapter1_4.generated.rpy`（工具早先的导出产物），
      `show xing base_hands_down` 就被数了**两遍** —— 服装代号原本
      "证据太弱、留空"，副本一进来票数变 2，静默变成「B」。
      两个近乎克隆的项目因此给出不同的代号表，而项目的声明**一个字都没改**。

    判据来自唯一真源 `parsing.discover.DERIVED_SUFFIXES` / `SKIP_DIR_PARTS`：
    派生文件（`*.generated.rpy` / `*.blocked.rpy` / `*.bak.rpy`）是**投影**，
    不是事实；`tl/` `common/` `renpy/` 是翻译与引擎自带。任何"项目里有什么"
    的统计都不许把它们算进去 —— 否则结论取决于"扫到了几个文件"。
    """
    out = {}
    game = root / 'game'
    if not game.is_dir():
        return out
    from storygraph.parsing.discover import DERIVED_SUFFIXES, SKIP_DIR_PARTS
    # `build/` / `libs/`：工具落点与引擎库（历史行为，别改）。
    skip = set(SKIP_DIR_PARTS) | {'libs', 'build'}
    try:
        files = sorted(game.rglob('*.rpy'))
    except OSError:
        return out
    for path in files:
        if skip & set(path.parts):
            continue
        if path.name.endswith(DERIVED_SUFFIXES):
            continue
        try:
            out[path.relative_to(root).as_posix()] = path.read_text(
                encoding='utf-8', errors='replace')
        except OSError:
            continue
    return out


# ---------------------------------------------------------------------------
#  五、换算：人话属性 ⇄ 代号
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Reading:
    """一次换算的结果（`ok=False` 时 `note` 说清缺什么）。"""

    ok: bool
    text: str
    char: str = ''
    outfit_code: str = ''
    outfit_attr: str = ''
    expr_no: str = ''
    expr_attr: str = ''
    note: str = ''

    @property
    def code(self) -> str:
        """`B7`（缺一半就只给有的那一半）。"""
        return f'{self.outfit_code}{self.expr_no}'.strip()

    @property
    def show(self) -> str:
        """剧本里真正要写的那一行（`show lan base_swimsuit angry`）。"""
        if not (self.char and self.ok):
            return ''
        attrs = [self.outfit_attr, self.expr_attr]
        words = ' '.join(a for a in attrs if a)
        return f'show {self.char} {words}'.strip()


def _fit(token, rows, key) -> str:
    """在一个角色的行里找这个写法 → 属性；找不到 `''`。"""
    want = norm(token)
    if not want:
        return ''
    for row in rows:
        if norm(row.get(key)) == want:
            return str(row.get(key) or '')
    for row in rows:
        for value in (row.get(key), row.get('label')):
            if value and norm(value) == want:
                return str(row.get(key) or '')
    # 前缀（`swim` → `base_swimsuit`）：只在唯一命中时才认，免得乱配
    hits = {str(row.get(key) or '') for row in rows
            if norm(row.get(key)).startswith(want)
            or norm(row.get('label') or '').startswith(want)}
    return hits.pop() if len(hits) == 1 else ''


def translate(text, data) -> Reading:
    """
    `lan swim_suit angry` / `lan B7` / `lan B 7` → 一份 `Reading`。

    两条路都走同一张表：属性 → 代号、代号 → 属性。
    中间那步永远是"查表"，所以**表填错了这里就错** —— 这是对的：
    要改的是表，不是悄悄改换算规则。
    """
    raw = str(text or '').strip()
    if not raw:
        return Reading(False, raw, note='先说哪个角色、穿哪件、什么表情。')
    chars = chars_in(data)
    outfit_rows = list(data.get(SECTION_OUTFITS) or ())
    expr_rows = list(data.get(SECTION_EXPRESSIONS) or ())
    tokens = [t for t in re.split(r'[\s,，、|]+', raw) if t]
    char = ''
    for index, token in enumerate(tokens):
        found = next((c for c in chars if c.lower() == token.lower()), '')
        if found:
            char = found
            del tokens[index]
            break
    if not char:
        if len(chars) == 1:
            char = chars[0]
        else:
            return Reading(False, raw,
                           note='没说是哪个角色 —— 在前面加上角色写法（例如 '
                                f'{chars[0] if chars else "lan"} {raw}）。')
    mine_out = [r for r in outfit_rows if str(r.get('char')) == char]
    mine_expr = [r for r in expr_rows if str(r.get('char')) == char]

    letter = no = ''
    outfit_attr = expr_attr = ''
    for token in tokens:
        code = re.match(r'^([A-Za-z])\s?(\d{1,2})$', token)
        if code:
            letter, no = code.group(1).upper(), code.group(2)
            continue
        if re.fullmatch(r'[A-Za-z]', token):
            letter = token.upper()
            continue
        if re.fullmatch(r'\d{1,2}', token):
            no = token
            continue
        got = _fit(token, mine_out, 'attr')
        if got:
            outfit_attr = got
            continue
        got = _fit(token, mine_expr, 'attr')
        if got:
            expr_attr = got
            continue
    missing = []
    if letter and not outfit_attr:
        row = next((r for r in mine_out
                    if str(r.get('code') or '').upper() == letter), None)
        if row is None:
            known = '、'.join(sorted({str(r.get('code') or '') for r in mine_out
                                     if r.get('code')})) or '（还没填）'
            missing.append(f'{char} 的服装代号里没有「{letter}」'
                           f'（表里有：{known}）')
        else:
            outfit_attr = str(row.get('attr') or '')
    elif outfit_attr and not letter:
        row = next((r for r in mine_out
                    if str(r.get('attr') or '') == outfit_attr), None)
        letter = str((row or {}).get('code') or '').upper()
        if not letter:
            missing.append(f'{char} 的「{outfit_attr}」还没填服装代号'
                           '（去「服装代号」那段给它一个字母）')
    if no and not expr_attr:
        row = next((r for r in mine_expr if str(r.get('no') or '') == no), None)
        if row is None:
            missing.append(f'{char} 的表情编号里没有「{no}」')
        else:
            expr_attr = str(row.get('attr') or '')
    elif expr_attr and not no:
        row = next((r for r in mine_expr
                    if str(r.get('attr') or '') == expr_attr), None)
        no = str((row or {}).get('no') or '')
        if not no:
            missing.append(f'{char} 的「{expr_attr}」还没编号')
    if not tokens:
        missing.append('只说了角色 —— 后面还要写服装和表情')
    note = '；'.join(missing)
    return Reading(not missing, raw, char, letter, outfit_attr, no, expr_attr,
                   note)


def template_rows(scan: Scan) -> dict:
    """把一次扫描变成可以直接塞进映射的两段（没有属性的行不写）。"""
    return {
        SECTION_OUTFITS: [dict(r) for r in scan.outfits if r.get('attr')],
        SECTION_EXPRESSIONS: [dict(r) for r in scan.expressions
                              if r.get('attr')],
    }


__all__ = ['LETTERS', 'SECTION_OUTFITS', 'SECTION_EXPRESSIONS',
           'OUTFIT_FIELDS', 'EXPRESSION_FIELDS', 'Hit', 'Scan', 'Reading',
           'norm', 'agreement', 'chars_in', 'parse_expression_line',
           'parse_expression_text', 'read_expression_tables',
           'parse_layeredimage', 'scan_script_text', 'scan_project',
           'translate', 'template_rows']
