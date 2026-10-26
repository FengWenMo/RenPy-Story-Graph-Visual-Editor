"""
纯文字导入 · **冲突预检 / 先问后写**
====================================

用户 2026-10-26 实机测试（原话）：

> 我一个个删 vscode 里面的 .bak 和 blocked 里面的文件老狼狈了……
> 当检测到有 label 重复的时候，能不能不要擅自不落盘，或者擅自全量替换呀……
> 能不能给出一个 UI 提醒，让我能够对着 txt 换 label？如果撞 label，
> 显示新内容与旧内容对比，再决定是否强制更换（可以全部强制更换，
> 也可以一个个文件分……）

所以纯文字导入从"写的时候被门禁一条条拦下"改成"**写之前把冲突摆到台面上**"：

    规划 →（角色决定）→ **冲突预检（本模块）** → 人拍板 → 落盘

## 为什么预检必须在写之前（而不是继续靠门禁兜底）

`write_generation` 是**最后一道闸**，它只能在"已经决定要写这一份"之后说
"不许写"；而它拒写时会留一份 `<项目根>/build/review/*.blocked.rpy` 供人对照
—— 每试一次就多一份，用户得一个个删。冲突预检把同一件事提前到底：
**你点了"跳过"，就一份都不产生。**

## 三种结局（"擅自"两个字各自对应哪一种）

    撞上了什么                          可选动作
    ──────────────────────────────────────────────────────────────────
    `label X` 已被别的 .rpy 定义         覆盖旧文件 / 改个名字写入 / 这次跳过
    目标 .rpy 已存在且是**手写**         覆盖旧文件 / 改个名字写入 / 这次跳过
    项目里**本来就有两个**同名 label     改个名字写入 / 这次跳过（覆盖救不了）
    ──────────────────────────────────────────────────────────────────

三条语义都是**用户定的**，工具不替他选：

  · **覆盖旧文件** = 把新产物写进"那个已经占着位置的 .rpy"（不是写到计划
    落点、留下一份撞名产物）。旧文件先备份成 `.bak`；它是手写文件时会走
    `write_generation(force=True)` —— 这是"强制更换"唯一的实现方式，
    也**只**跳过"手写护栏"那条（语法/引擎/编码等门禁一条都不越）；
  · **改个名字写入** = 只用新的 label，旧文件一个字不动；新文件写到
    **旧文件旁边**（真机章节住在 `game/chapter/`，跟着它走，不要另开一窝）；
  · **这次跳过** = 保留旧文件，这一份不写，也不产生 `.blocked.rpy`。

## 这是"提前告知"，不是"另一套判据"

预检只判两件**能当场确定**的事（谁占着 label / 目标是不是手写），真正的落盘
判据仍然是 `generation_guard.write_generation`。两边**故意不共用结论**：
预检负责"问得准"，门禁负责"拦得住"。所以哪怕人的决定让产物仍然有问题
（例如改的新名字又被别人占了），门禁照旧拦下并如实报 —— 预检**不替门禁放行**。

## 没有 `resolve` 回调时（CLI / 单测）

行为与从前**一字不改**：照旧写、照旧被门禁拦下、照旧留对照副本。
界面传 `resolve=` 才进入"先问后写"。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from storygraph.core.safeguard import swallow
from storygraph.core.textlines import split_lines
from storygraph.story.generation_guard import (declared_labels,
                                               is_handwritten_script,
                                               label_defined_in, label_owners,
                                               opaque_loss, relative_hint)
from storygraph.story.paths import inside_game_dir

# ===================== 三种动作（唯一来源） =====================

#: 把新产物写进"已经占着位置的那个 .rpy"（旧文件先备份 `.bak`）。
ACT_REPLACE = 'replace'
#: 换个 label，另写一个文件；旧文件一个字不动。
ACT_RENAME = 'rename'
#: 这次不写这一份（保留旧文件；**不产生** `.blocked.rpy`）。
ACT_SKIP = 'skip'

ALL_ACTIONS = (ACT_REPLACE, ACT_RENAME, ACT_SKIP)

#: Ren'Py label 名：字母/数字/下划线，不以数字开头；允许点号分段
#: （与 `ui_qt.dialogs.valid_label` 同一条规则 —— 这里再判一次是因为
#: **不能只靠界面守**：脚本/CLI 也能直接给动作）。
_LABEL_RE = re.compile(
    r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$')


def valid_label(name: str) -> bool:
    """合法的 Ren'Py label 名？（中文/空格/短横线都会生成坏脚本，必须挡）"""
    return bool(_LABEL_RE.match(str(name or '').strip()))


def suggest_free_label(label: str, taken) -> str:
    """
    给一个**没被占用**的建议名（`chapter3_1` → `chapter3_1_2` / `_3` …）。

    为什么是"加后缀"而不是猜一个新名字：作者写的是"章-节-段"，工具猜不出
    他心里的编号（猜一个名字比说"不知道"坏得多，铁律 4）。加后缀是**确定性**
    的，人看一眼就知道这不是原来的名字，可以随手改成自己想要的。
    """
    taken = {str(x) for x in (taken or ()) if x}
    base = str(label or '').strip() or 'chapter'
    if base not in taken:
        return base
    n = 2
    while f'{base}_{n}' in taken:
        n += 1
    return f'{base}_{n}'


# ===================== 新旧对比（纯文本，不依赖 Qt） =====================

@dataclass(frozen=True)
class DiffRow:
    """对比视图里的一行：两边各占一栏（空 = 这一边没有这行）。"""
    tag: str                # 'eq' | 'chg' | 'add' | 'del'
    old_no: int = 0         # 0 = 旧文件里没有这一行
    new_no: int = 0         # 0 = 新产物里没有这一行
    old: str = ''
    new: str = ''


@dataclass
class DiffStats:
    """对比摘要（弹窗顶部那行"旧 N 行 → 新 M 行：改 X / 增 Y / 删 Z"）。"""
    old_lines: int = 0
    new_lines: int = 0
    same: int = 0
    added: int = 0
    removed: int = 0
    changed: int = 0

    @property
    def identical(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def summary(self) -> str:
        if self.identical:
            return f'内容完全一样（共 {self.same} 行）'
        bits = []
        if self.changed:
            bits.append(f'改了 {self.changed} 行')
        if self.added:
            bits.append(f'新增 {self.added} 行')
        if self.removed:
            bits.append(f'删掉 {self.removed} 行')
        return (f'旧 {self.old_lines} 行 → 新 {self.new_lines} 行：'
                + ' · '.join(bits))


def compare_texts(old_text, new_text) -> tuple:
    """
    逐行对比两份文本 → `([DiffRow…], DiffStats)`（左右并排画对比用）。

    **为什么要自己对齐而不是给两份原文**：一场戏 200~400 行，人眼在
    两栏滚动里找"哪一句被改了"等于没对比。这里用 `difflib.SequenceMatcher`
    按行对齐（`autojunk=False` —— 正文里重复的短句很多，默认的"垃圾启发式"
    会把它们当噪声跳过，对齐就乱了），并给每行标 `eq/chg/add/del`。

    ⚠ 换行一律走 `split_lines`（`str.splitlines()` 会把 U+2028 软回车也当
    换行，而 Ren'Py 只认 `\\n` —— P0-A 的老坑）。这里为了展示宽容一点无妨，
    但**口径要统一**，否则"对比说没改"和"实际改名了"会打架。

    ⚠ **行尾 `\\r` 要在展示层去掉**（2026-10-26，队友实测）：`split_lines`
    只按 `\\n` 切，所以 CRLF 文件切出来的每行都带一个 `\\r` —— 旧 .rpy 是
    CRLF（vscode 手写最常见）时，内容明明一样，对比却把**每一行**都标成
    "改了"。这里只影响**显示**（落盘口径另说，见 `source_style`）。
    """
    old = [str(ln).rstrip('\r') for ln in split_lines(old_text or '')]
    new = [str(ln).rstrip('\r') for ln in split_lines(new_text or '')]
    rows: list = []
    stats = DiffStats(old_lines=len(old), new_lines=len(new))
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                rows.append(DiffRow('eq', i1 + k + 1, j1 + k + 1,
                                    old[i1 + k], new[j1 + k]))
                stats.same += 1
        elif tag == 'delete':
            for i in range(i1, i2):
                rows.append(DiffRow('del', i + 1, 0, old[i], ''))
                stats.removed += 1
        elif tag == 'insert':
            for j in range(j1, j2):
                rows.append(DiffRow('add', 0, j + 1, '', new[j]))
                stats.added += 1
        else:                                   # replace：能配上的按"改"算
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                has_old = (i1 + k) < i2
                has_new = (j1 + k) < j2
                if has_old and has_new:
                    rows.append(DiffRow('chg', i1 + k + 1, j1 + k + 1,
                                        old[i1 + k], new[j1 + k]))
                    stats.changed += 1
                elif has_old:
                    rows.append(DiffRow('del', i1 + k + 1, 0, old[i1 + k], ''))
                    stats.removed += 1
                else:
                    rows.append(DiffRow('add', 0, j1 + k + 1, '', new[j1 + k]))
                    stats.added += 1
    return rows, stats


# ===================== 预检的数据 =====================

#: `Conflict.code` 的取值（与门禁的 issue code 同名，便于对账）。
CODE_LABEL_COLLISION = 'LABEL_COLLISION'
CODE_HANDWRITTEN = 'HANDWRITTEN_TARGET'
CODE_ALREADY_DUPLICATED = 'ALREADY_DUPLICATED'


@dataclass
class Conflict:
    """一处会让这份产物写不进去（或写进去会毁东西）的冲突。"""
    code: str
    message: str
    other_path: Optional[Path] = None
    other_text: str = ''
    other_is_generated: bool = True
    other_labels: tuple = ()

    @property
    def is_label(self) -> bool:
        return self.code in (CODE_LABEL_COLLISION, CODE_ALREADY_DUPLICATED)


@dataclass
class ReviewItem:
    """**一个文件**的处境 + 人要做的那一个决定（还没决定时 `action == ''`）。"""
    source: Path
    target: Path                        # 计划落点（还没决定时）
    label: str                          # 计划 label
    new_text: str                       # 转换出来的产物（对比用）
    conflicts: list = field(default_factory=list)      # [Conflict]
    old_path: Optional[Path] = None     # 对比基准文件（撞上的那份 / 目标自己）
    old_text: str = ''
    replace_path: Optional[Path] = None  # 选"覆盖旧文件"时写到这里（None = 不能覆盖）
    replace_is_generated: bool = True
    writable_dir: Path = None           # "改个名字写入"时落在哪个目录
    lost_chunks: tuple = ()             # 覆盖旧文件会丢的顶层块 [(行号, 语句头)]
    lost_labels: tuple = ()             # 旧文件里别的 label（覆盖会一起消失）
    #: 人回填的结论（`action` 空 = 还没决定；落盘时按 `ACT_SKIP` 处理）。
    action: str = ''
    new_label: str = ''

    # ---- 只读派生 ----
    @property
    def code(self) -> str:
        return self.conflicts[0].code if self.conflicts else ''

    @property
    def other_path(self) -> Optional[Path]:
        return self.conflicts[0].other_path if self.conflicts else None

    @property
    def is_label_collision(self) -> bool:
        return any(c.is_label for c in self.conflicts)

    @property
    def allows_replace(self) -> bool:
        return self.replace_path is not None

    @property
    def allows_rename(self) -> bool:
        return True                     # 换个名字永远可以（名字合法性另判）

    @property
    def options(self) -> tuple:
        acts = []
        if self.allows_replace:
            acts.append(ACT_REPLACE)
        acts.extend([ACT_RENAME, ACT_SKIP])
        return tuple(acts)

    @property
    def default_action(self) -> str:
        """
        弹窗里**预选**哪一个。

        判据只有一条：旧的那份是**工具自己以前生成的**（可安全重导）→ 覆盖；
        其余（手写的 / 项目本来就有重名）→ 跳过。**默认永远是"不改变现状"**：
        用户点错了"继续"也不至于毁掉手写文件。
        """
        if self.allows_replace and self.replace_is_generated:
            return ACT_REPLACE
        return ACT_SKIP

    def conflict_text(self) -> str:
        """一句话说清"撞上了什么"（表格里那一列就显示它）。"""
        return '；'.join(c.message for c in self.conflicts) or '没有冲突'

    def rename_target(self, label: str = '') -> Path:
        """改个名字之后写到哪（默认落在**旧章节所在的目录**）。"""
        base = Path(self.writable_dir or self.target.parent)
        return base / f'{label or self.new_label or self.label}.rpy'

    def suggest_label(self, taken=None) -> str:
        return suggest_free_label(self.label, taken)

    def diff(self) -> tuple:
        """`(DiffRow…, DiffStats)`：旧文件（`old_path`）→ 新产物。"""
        return compare_texts(self.old_text, self.new_text)

    def risk_text(self) -> str:
        """"覆盖旧文件"会付出什么代价（没有代价就返回空串）。"""
        bits = []
        if self.lost_chunks:
            heads = '、'.join(f'第 {n} 行 `{t}`'
                              for n, t in list(self.lost_chunks)[:3])
            more = (f' 等 {len(self.lost_chunks)} 处'
                    if len(self.lost_chunks) > 3 else '')
            bits.append(f'旧文件里有 {len(self.lost_chunks)} 处顶层语句不会保留'
                        f'（{heads}{more}）')
        if self.lost_labels:
            bits.append('旧文件里还有别的 label 会一起消失：'
                        + '、'.join(f'`{x}`' for x in self.lost_labels))
        return '；'.join(bits)


@dataclass
class Review:
    """一整批的冲突预检结果（`items` 空 = 没有冲突，可以直接写）。"""
    root: Optional[Path] = None
    items: list = field(default_factory=list)          # [ReviewItem]
    taken_labels: set = field(default_factory=set)     # 项目 + 本批已占用的 label
    note: str = ''

    @property
    def ok(self) -> bool:
        return not self.items

    @property
    def needs_review(self) -> bool:
        return bool(self.items)

    def item_for(self, source) -> Optional[ReviewItem]:
        key = str(source)
        for item in self.items:
            if str(item.source) == key:
                return item
        return None

    @property
    def label_collisions(self) -> int:
        return sum(1 for i in self.items if i.is_label_collision)

    @property
    def handwritten(self) -> int:
        return sum(1 for i in self.items
                   if any(c.code == CODE_HANDWRITTEN for c in i.conflicts))

    def summary(self) -> str:
        if not self.items:
            return '没有会撞名 / 覆盖手写文件的产物'
        bits = [f'{len(self.items)} 个文件要你拿主意']
        if self.label_collisions:
            bits.append(f'{self.label_collisions} 个撞 label')
        if self.handwritten:
            bits.append(f'{self.handwritten} 个会覆盖手写文件')
        return '，'.join(bits)

    def rename_problem(self, item, label: str) -> str:
        """
        界面里"这个新名字能不能用"的**即时校验**（空串 = 能）。

        ⚠ 这是**提示**，不是最终判据：真正落地时 `apply_decisions` 还会
        按当时的项目再校验一次（`why_rename_failed`）—— 界面拦得住 99%，
        但"点确定的瞬间别人刚好写了个同名文件"只有落盘那层拦得住。
        两处口径**都来自** `valid_label` / 占用集合，不是两套规则。
        """
        label = str(label or '').strip()
        if not label:
            return '还没有填新名字'
        if not valid_label(label):
            return ('不是合法的 Ren\'Py label 名'
                    '（只能用字母/数字/下划线，不能数字开头）')
        if label == item.label:
            return f'`{label}` 就是撞上的那个名字 —— 换个名字，或者选「覆盖旧文件」'
        if label in self.taken_labels:
            return f'`{label}` 已经被项目里的文件占用了，换一个'
        return ''


# ===================== 预检：规划 → Review =====================

def _read_text(path) -> str:
    """读来**只给人看**（对比栏）：解不出来也要显示个大概，不抛。"""
    try:
        return Path(path).read_bytes().decode('utf-8', 'replace')
    except OSError as e:                    # 读不出来就空着（对比栏会说明）
        swallow('import_review._read_text', e)
        return ''


def _same_path(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


def _owner_flavour(path: Path) -> str:
    return '手写的' if is_handwritten_script(path) else '工具以前生成的'


def preflight(plan, project_root) -> Review:
    """
    对一份 `txt_pipeline.Plan` 做冲突预检（**纯读，不写任何文件**）。

    对每个计划文件只问两件事：

      ① 它的 `label` 有没有被**别的** .rpy 占着（Ren'Py 把 game/ 下所有
         .rpy 当一个脚本，重名 = 游戏起不来）；
      ② 它的落点文件是不是**手写**的（覆盖会毁掉 define / init python）。

    两者都没有 → 这个文件不进 `items`（照旧直接写）。
    """
    root = Path(project_root) if project_root else None
    owners = label_owners(root) if root else {}
    review = Review(root=root, taken_labels=set(owners)
                    | {f.label for f in (getattr(plan, 'files', None) or ())})
    for planned in (getattr(plan, 'files', None) or ()):
        item = _review_one(planned, owners, root)
        if item is not None:
            review.items.append(item)
    if review.items:
        review.note = (f'有 {len(review.items)} 个文件在落盘前需要你决定'
                       f'（撞 label / 覆盖手写文件）—— 没决定之前一个字节都不写')
    return review


def _review_one(planned, owners: dict, root) -> Optional[ReviewItem]:
    target = Path(planned.target)
    target_exists = target.is_file()
    target_text = _read_text(target) if target_exists else ''
    target_labels = declared_labels(target) if target_exists else set()
    handwritten = is_handwritten_script(target) if target_exists else False
    # 与门禁同一口径：只有产物**真会被 Ren'Py 扫描**（落在 <根>/game/ 里）时，
    # "和原文抢 label"才是致命问题（`labels_in_danger`）。
    label_matters = bool(root) and inside_game_dir(target, root)
    others = []
    if label_matters:
        others = [p for p in (owners.get(planned.label) or ())
                  if not _same_path(p, target)]
    duplicated = bool(others) and planned.label in target_labels

    conflicts = []
    if handwritten:
        conflicts.append(Conflict(
            code=CODE_HANDWRITTEN,
            message=(f'目标 {relative_hint(target, root)} 已经存在，而且不是工具生成的'
                     f'（首行没有生成标记）—— 覆盖它，那份文件里的 define / '
                     f'init python / transform 一行都留不下'),
            other_path=target, other_text=target_text,
            other_is_generated=False,
            other_labels=tuple(sorted(target_labels))))
    if others:
        owner = Path(others[0])
        # "项目里**本来就**有两个文件定义同一个 label"与"这个名字被一份别的
        # 文件占着"是两件不同的事：后者覆盖旧文件就能收场，前者无论怎么覆盖
        # 都还是重名（另一个文件还占着），必须先手工修掉一个。
        already_broken = duplicated or len(others) > 1
        if already_broken:
            involved = ([target] if duplicated else []) + others
            shown = '、'.join(relative_hint(p, root) for p in involved[:3])
            more = f' 等 {len(involved)} 个文件' if len(involved) > 3 else ''
            conflicts.append(Conflict(
                code=CODE_ALREADY_DUPLICATED,
                message=(f'项目里**本来就有**多个文件定义 `label {planned.label}`'
                         f'（{shown}{more}）—— 不是这次导入造成的，'
                         f'覆盖也修不好；先手工删掉/改名其中一个'),
                other_path=owner, other_text=_read_text(owner),
                other_is_generated=not is_handwritten_script(owner),
                other_labels=tuple(sorted(declared_labels(owner)))))
        else:
            conflicts.append(Conflict(
                code=CODE_LABEL_COLLISION,
                message=(f'`label {planned.label}` 已经被 '
                         f'{relative_hint(owner, root)} 定义'
                         f'（{_owner_flavour(owner)}）—— Ren\'Py 把 game/ 下所有 '
                         f'.rpy 当同一个脚本，重名会让游戏直接起不来'),
                other_path=owner, other_text=_read_text(owner),
                other_is_generated=not is_handwritten_script(owner),
                other_labels=tuple(sorted(declared_labels(owner)))))
    if not conflicts:
        return None

    # —— 覆盖旧文件：写到哪、代价是什么 ——
    # 只有"一个明确的占位文件"才给覆盖：
    #   · 名字被**一份**别的文件占着 → 覆盖它；
    #   · 目标自己存在且手写（名字没被别人占）→ 覆盖目标；
    #   · 项目本来就重名 / 两边都占着 → **不给**覆盖（说不清该覆盖谁，
    #     宁可少一个选项，也不能替人猜）。
    already_broken = any(c.code == CODE_ALREADY_DUPLICATED for c in conflicts)
    replace_path = None
    if not already_broken:
        if others and not handwritten:
            replace_path = Path(others[0])
        elif handwritten and not others:
            replace_path = target
    replace_is_generated = (not is_handwritten_script(replace_path)
                            if replace_path is not None else True)
    old_path = (Path(others[0]) if others else
                (target if handwritten else None))
    old_text = (_read_text(old_path) if old_path is not None else '')
    lost_chunks: tuple = ()
    lost_labels: tuple = ()
    if replace_path is not None:
        lost_chunks = tuple(opaque_loss(old_text, planned.rpy_text))
        lost_labels = tuple(sorted(declared_labels(replace_path) - {planned.label}))
    writable_dir = (Path(others[0]).parent if others else target.parent)
    return ReviewItem(
        source=Path(planned.source), target=target, label=planned.label,
        new_text=planned.rpy_text, conflicts=conflicts,
        old_path=old_path, old_text=old_text,
        replace_path=replace_path, replace_is_generated=replace_is_generated,
        writable_dir=writable_dir,
        lost_chunks=lost_chunks, lost_labels=lost_labels)


# ===================== 落地：人的决定 → 规划 =====================

def _apply_replace(planned, item: ReviewItem, root) -> str:
    """
    覆盖旧文件：**落点改成旧文件**，并显式带上旧文本（写前备份 `.bak`）。

    为什么 `force=True`：这是"强制更换"唯一的实现 —— 目标可能是手写文件，
    `write_generation` 的手写护栏本来会拦。只跳过那一条；`LABEL_COLLISION` /
    语法 / 引擎 / 编码门禁照旧生效（见 `write_generation` 文档）。
    带 `source_text` 是为了让门禁**算出**"旧文件里哪些顶层块会丢"并记进
    回执（这里已经 `force`，所以那条降级成 warn，不会二次拦人）。
    """
    old_path = Path(item.replace_path)
    planned.target = old_path
    planned.force = True
    planned.replace_source_path = old_path
    planned.replace_source_text = item.old_text
    risk = item.risk_text()
    return (f'[{Path(planned.source).name}] 覆盖 '
            f'{relative_hint(old_path, root)}'
            f'（旧文件先备份成 .bak' + (f'；{risk}' if risk else '') + '）')


def _apply_rename(planned, item: ReviewItem, label: str, chars,
                  review: Review, occupied: set, planned_labels: set) -> tuple:
    """
    改个名字写入：**校验** → 重跑转换（label 会写进产物第一行）→ 换落点。

    旧文件一个字不动；新文件写到**旧章节旁边的目录**（`item.writable_dir`）。
    返回 `(ok, why)`；`why` 与弹窗里那句校验话是**同一个函数**给的
    （`why_rename_failed`）—— 两处各写一套早晚会漂成"弹窗说行、落地说不行"。
    """
    label = str(label or '').strip() or item.suggest_label(occupied)
    why = why_rename_failed(item, label, review, occupied, planned_labels)
    if why:
        return False, why
    old_label = planned.label
    from storygraph.authoring.txt_import import convert_text
    source_text = getattr(planned, 'source_text', '') or _read_text(planned.source)
    planned.result = convert_text(source_text, filename=Path(planned.source).name,
                                  characters=chars, label=label)
    planned.renamed_from = old_label
    planned.label = label
    planned.target = Path(item.writable_dir or planned.target.parent) \
        / f'{label}.rpy'
    planned_labels.discard(old_label)
    planned_labels.add(label)
    return True, ''


def why_rename_failed(item: ReviewItem, label: str, review: Review,
                      occupied: set, planned_labels: set) -> str:
    """
    这个"新名字"为什么不能用（空串 = 可以用）。界面和落盘**共用这一套理由**。
    """
    label = str(label or '').strip()
    old_label = item.label
    if not label:
        return '还没有填新名字'
    if not valid_label(label):
        return (f'`{label}` 不是合法的 Ren\'Py label 名'
                f'（只能用字母/数字/下划线，不能数字开头）')
    if label == old_label:
        return f'`{label}` 就是撞上的那个名字 —— 换个名字，或者选「覆盖旧文件」'
    if label in occupied:
        owner = label_defined_in(label, review.root) if review.root else None
        where = f'（{relative_hint(owner, review.root)}）' if owner else ''
        return f'`{label}` 在项目里已经有文件定义了{where}，换一个'
    if label in planned_labels - {old_label}:
        return f'`{label}` 和这一批里的另一个文件撞名了，换一个'
    return ''


def apply_decisions(plan, review: Review, decisions, rows=None) -> list:
    """
    把弹窗里人选的结果落到规划上；返回要**说出口**的话（messages）。

    `decisions`：`[{'source': 源文件路径, 'action': 'replace'|'rename'|'skip',
    'label': 新名字(仅 rename)}]`。**没给结论的按"跳过"处理** —— 宁可少写，
    不可替人赌（尤其"覆盖手写文件"这种事，默认必须是"不动"）。
    """
    by_source = {str(i.source): i for i in (review.items or ())}
    picked = {}
    for d in (decisions or ()):
        if isinstance(d, dict) and d.get('source'):
            picked[str(d['source'])] = d
    rows = rows or {}
    from storygraph.authoring.txt_import import CharacterMap
    chars = CharacterMap(rows)
    root = review.root
    # 改名合法性要用**当前**项目口径（本批文件还没写，所以现扫一遍就够）
    occupied = set(label_owners(root)) if root else set()
    planned_labels = {f.label for f in (getattr(plan, 'files', None) or ())}
    kept = []
    messages = []
    for planned in (getattr(plan, 'files', None) or ()):
        item = by_source.get(str(planned.source))
        if item is None:                    # 没冲突的文件照旧写
            kept.append(planned)
            continue
        choice = picked.get(str(planned.source)) or {}
        action = str(choice.get('action') or ACT_SKIP)
        if action == ACT_REPLACE and item.replace_path is not None:
            kept.append(planned)
            messages.append(_apply_replace(planned, item, root))
        elif action == ACT_RENAME:
            label = str(choice.get('label') or '').strip()
            ok, why = _apply_rename(planned, item, label, chars, review,
                                    occupied, planned_labels)
            if not ok:
                plan.skipped.append((str(planned.source), why))
                messages.append(f'[{Path(planned.source).name}] 没改成：{why}'
                                f'（这一份先不写）')
                continue
            kept.append(planned)
            messages.append(
                f'[{Path(planned.source).name}] 撞名 → 改名为 `{planned.label}`，'
                f'写到 {relative_hint(planned.target, root)}'
                f'（旧文件一个字没动）')
        else:
            old = (relative_hint(item.old_path, root)
                   if item.old_path is not None else '旧文件')
            plan.skipped.append(
                (str(planned.source), f'冲突预检：你选择先不写（保留 {old}）'))
            messages.append(f'[{Path(planned.source).name}] 按你的选择跳过：'
                            f'保留 {old}，这一份不写')
    plan.files[:] = kept
    return messages


__all__ = ['ACT_REPLACE', 'ACT_RENAME', 'ACT_SKIP', 'ALL_ACTIONS',
           'CODE_ALREADY_DUPLICATED', 'CODE_HANDWRITTEN', 'CODE_LABEL_COLLISION',
           'Conflict', 'DiffRow', 'DiffStats', 'Review', 'ReviewItem',
           'apply_decisions', 'compare_texts', 'preflight', 'suggest_free_label',
           'valid_label', 'why_rename_failed']
