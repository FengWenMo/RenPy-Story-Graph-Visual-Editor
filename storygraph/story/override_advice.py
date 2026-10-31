# -*- coding: utf-8 -*-
"""
storygraph.story.override_advice
================================
**本景里有多少处"单独改写"** —— 只读检测层（2026-10-31）。

## 它回答的那个问题

尺寸面板在**最初写下这一行的那一拍**改 `at`（背景 = 那条 `scene`，CG/UI =
那条 `show`）—— 因为尺寸是**持续状态**，不是这一拍的事件。可是同一景里
后面只要有谁又 `show` 了一次同一个对象（带不带 `at` 都算），Ren'Py 就**重建**
那个 displayable，前面那行 `at` 从那一刻起**不再生效**：

    scene bg bg7_0 at bgfit        # 第 3 拍：改的就是这一行
    ...                            # 第 4–11 拍：铺满（对的）
    show bg bg7_0 at half          # 第 12 拍：后面全变成 half 了

用户在尺寸面板里改完、预览看了一眼就走了，以为改好了 —— 因为**他当时看的是
第 4 拍**。这是本项目最贵的那类缺陷（"看到的 ≠ 生成的"）的同一个根。

所以这个模块只做一件事：**把"会被盖回去"的那几处数出来、说清楚**。

## 为什么它是只读的（`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5）

那份文档的结论是"能靠读解决的，绝不写"，报警强度**由算得出的事实决定**，
不由档位决定。这个功能正好落在最低的那一档：

    · 它不改任何东西（不写盘、不改 Beat、不删行）；
    · 它的数字只用来**说**。

合并回源（`story.at_merge`）是**另一档**，而且是**另一种做法**：不是"把那
几处删掉"，而是把那几处的 `@at` 改成和源一样（`show` 会重建 displayable，
删掉 at 等于回到原样，不是继承）。它动的是用户的 `.rpy` 行，按 §5.5 的 L4
纪律**逐处确认、逐处可撤**，所以是**人按下按钮**才发生的事 —— 这个模块
永远只报数，不替任何人做决定。

## 多级提示（同一份事实，三种强度）

| 事实                       | 级别      | 表现                     |
|---------------------------|-----------|--------------------------|
| 0 处（改源在整个本景有效）   | `none`    | 一个字都不显示           |
| 1–2 处                     | `hint`    | 行下一句话（不拦）        |
| ≥3 处（`NOTICE_AT`）        | `notice`  | 一句话 + 影响清单 + 建议  |
| **那几处和源写着同一个值**   | `hint`    | 去掉 ⚠、说"不会再盖回"（仍报，源下次再改还是会断） |
| **算不出来**                | `hint`    | 注明"未能统计"，**不许当 0** |

最后一行是文档 §5.5「防误报」表里的那条：引用数算不出来时**降档**并注明，
但绝不把"不知道"说成"没有"—— 漏报的代价是用户以为改好了。

## 判据（`tests/test_override_advice.py`）

最关键的一条：**这里数出来的处数，必须和实际会盖回去的处数一致**。所以

  · 生效的 `at` 用 `beat_stage_parse.item_at`（与 `stage_snapshot` 同一份）；
  · "是不是同一个图层"用 `core.vocabulary.same_layer_tag`（背景三种写法）；
  · 处数按 `Site` 列表算，`text()` 里的数字直接取 `len(sites)` —— 不许另算。
"""

from __future__ import annotations

from dataclasses import dataclass

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import BACKGROUND_TAGS

#: 三个级别（与 `docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5 同名同义）。
NONE = 'none'
HINT = 'hint'
NOTICE = 'notice'

LEVELS = (NONE, HINT, NOTICE)

#: 到几处才升到 `notice`（1–2 处属于"偶尔一处"；3 处起就是这一景的常态了，
#: 那时人需要的是"往回收"，不是"再多改一处"）。
NOTICE_AT = 3

#: 文字里最多点名几拍（再多就"等 N 处"）。
MAX_NAMES = 4


@dataclass(frozen=True)
class Site:
    """一处单独改写（一拍**自己**写了一次这个对象）。"""

    #: 拍号（0 基）
    index: int = -1
    #: 这一处**生效**的 at（空 = 不带变换 = 按原图尺寸画，"原样"）
    at: str = ''
    #: 图名（`bg bg7_0` / `cg cg01`）
    image: str = ''
    #: 原样写法（`bg bg7_0 at half`）—— 给人看的坐标，不重新拼
    clause: str = ''

    @property
    def label(self) -> str:
        return f'第 {self.index + 1} 拍'

    def as_dict(self) -> dict:
        return {'index': self.index, 'at': self.at, 'image': self.image,
                'clause': self.clause}


def override_map(beats, start: int = 0) -> dict:
    """
    从第 `start` 拍**之后**到下一次 `scene` 之前，各类对象被单独改写的处。

    返回 `{key: (Site, …)}`：背景进 `'scene'` 桶（`bg` / `background` / `bg_`
    三种写法都算背景），CG/UI 等图层按自己的 tag 分桶。

    **一次扫描**：尺寸面板一帧最多 4 行（1 背景 + 3 图层），每行各扫一遍整景
    是 4 倍白算。调用方先用 `min(各行的 source)` 扫一次，再逐行切片
    （`override_advice(..., prefetched=map)`）。

    边界（与 Ren'Py 语义一致，不猜）：

      · 遇到**自己写了 `scene`** 的拍 → 本景结束（清台重设，前面的一切作废）；
      · 只数 `show`：`hide` 不改变"这个对象被重写过几次"（撤下之后又 `show`
        一次，仍是另一处单独改写）—— 所以这里不把 `hide` 当边界，
        也不把它当"一处"。

    ⚠ 只用 `show` 还有一个原因：`hide` 的 item 与 `show` 的 item 形状不同
    （`{'image','with'}` vs `{'image','at',…}`），把它并进来的唯一收益是
    "对象在某一拍消失"，而这件事对"改源会被谁盖回去"没有任何影响。
    """
    out: dict = {}
    beats = list(beats or ())
    try:
        src = int(start)
    except (TypeError, ValueError):
        return out
    if src < 0:
        return out
    from storygraph.story.beat_model import show_item_part, split_show_items
    from storygraph.story.beat_stage_parse import item_at

    for j in range(src + 1, len(beats)):
        beat = beats[j]
        if str(getattr(beat, 'scene', '') or '').strip():
            break                                  # 新景：本景到此为止
        for item in split_show_items(str(getattr(beat, 'show', '') or '')):
            image = str(item.get('image') or '').strip()
            if not image:
                continue
            tag = image.split()[0] if image.split() else ''
            key = 'scene' if tag in BACKGROUND_TAGS else tag
            if not key:
                continue
            out.setdefault(key, []).append(
                Site(index=j, at=item_at(item, beat), image=image,
                     clause=show_item_part(item)))
    return {k: tuple(v) for k, v in out.items()}


@dataclass(frozen=True)
class OverrideAdvice:
    """
    本景"单独改写"的结论（`level` + 那几处）。

    它的 `text()` 是**唯一**一份措辞：面板那一行与确认框里那句话都从这里取，
    不许各写一遍（文档 §5.5 最后一条判据：报的数字 == 实际改的数字）。
    """

    level: str = NONE
    sites: tuple = ()
    #: 算不出来（帧/拍表缺失、解析炸了）：**不许当成 0 处**。
    unknown: bool = False
    #: `unknown=True` 时说明为什么算不出（人话）
    reason: str = ''
    #: **源那一行自己写着**的 at（`None` = 不知道，别猜）。有了它才能分辨
    #: "那几处写的是另一个值（会盖回）"和"那几处本来就写着同一个值（不会
    #: 盖回，但源再改仍只到那一拍有效）"—— 两种事实，两句话。
    source_at: object = None

    @property
    def agreed(self) -> bool:
        """那几处写的**都是**源那个值（= 合并过 / 本来就是一致的）。"""
        if self.source_at is None or not self.sites:
            return False
        want = str(self.source_at or '')
        return all(str(s.at or '') == want for s in self.sites)

    # ---------- 数字（全部从 sites 推，不另存一份） ----------

    @property
    def count(self) -> int:
        return len(self.sites)

    @property
    def beats(self) -> tuple:
        return tuple(s.index for s in self.sites)

    @property
    def first(self):
        return self.sites[0].index if self.sites else None

    @property
    def ends_at(self):
        """
        这次改动**生效到哪一拍为止**（0 基）= 第一处会被盖回去的拍号。

        人话就是"改源只到第 12 拍有效"。没有那一处时 `None` = 整景有效。
        """
        return self.first

    @property
    def same_at(self) -> bool:
        """那几处是不是**都写着同一个** at（同样的写法重复了 N 次）。"""
        if not self.sites:
            return True
        return len({s.at for s in self.sites}) == 1

    def conflicts(self, new_at: str) -> tuple:
        """哪几处**会盖回**这次改动（写的不是 `new_at` 这个值）。"""
        new_at = str(new_at or '')
        return tuple(s for s in self.sites if str(s.at or '') != new_at)

    def agreeing(self, new_at: str) -> tuple:
        """哪几处写的**正好是**新值（它们不会盖回，但仍是重复写法）。"""
        new_at = str(new_at or '')
        return tuple(s for s in self.sites if str(s.at or '') == new_at)

    def as_dict(self) -> dict:
        return {'level': self.level, 'count': self.count,
                'beats': list(self.beats), 'unknown': self.unknown,
                'reason': self.reason, 'agreed': self.agreed,
                'source_at': self.source_at,
                'sites': [s.as_dict() for s in self.sites]}

    # ---------- 唯一一份措辞 ----------

    def _names(self, sites) -> str:
        """`第 4 拍` / `第 4、6、7 拍` / `第 4、6、7、9 拍 等 7 处`。"""
        sites = list(sites)
        if not sites:
            return ''
        shown = sites[:MAX_NAMES]
        nums = '、'.join(str(s.index + 1) for s in shown)
        more = len(sites) - MAX_NAMES
        word = f'第 {nums} 拍'
        return word + (f' 等 {len(sites)} 处' if more > 0 else '')

    def short(self, label: str = '') -> str:
        """
        **一行**（面板槽位里那一句）：几处、到第几拍失效。长解释放 tooltip。

        按文档的分档，`hint` 与 `notice` 在界面上只差"要不要再多说一句"——
        这里不靠加长句子区分，靠**颜色**（`notice` 用 `role="warn"`）与
        tooltip 里的完整版本来区分。
        """
        if self.unknown:
            return '⚠ 统计不出本景有几处单独改写（改源可能被后面的行盖回去）'
        if not self.sites:
            return ''
        what = _label_phrase(label)
        if self.agreed:
            # 那几处和源写的是同一个值：**不再吓人**，但也不能消失 ——
            # 源下次再改，仍然只到第一处有效（陷阱还在，只是这次没踩）。
            return (f'本景还有 {self.count} 处写着同一个 at'
                    f'（{self._names(self.sites)}）：再改源只到'
                    f'{self.sites[0].label}有效')
        head = (f'⚠ 本景还有 {self.count} 处单独' + _said('改了', what)
                + f'（{self._names(self.sites)}）')
        return head + f'：改源只到{self.sites[0].label}有效'

    def text(self, new_at=None, label: str = '') -> str:
        """
        给人看的那句话（`''` = 什么都不说，与 `level='none'` 对应）。

        `new_at=None`  = **还不知道**要写成什么（面板：只说"有几处、到第几拍失效"）
        `new_at=''`    = 要写的是"原样"（去掉 at）
        `new_at='bg'`  = 要写的是这个 transform（弹框：能说"哪几处会盖回"）
        """
        if self.unknown:
            why = f'（{self.reason}）' if self.reason else ''
            return (f'⚠ 统计不出本景有几处单独改写{why} —— 改最初那一行'
                    '可能被后面的行盖回去，请自己看一眼。')
        if not self.sites:
            return ''
        what = _label_phrase(label)
        if self.agreed:
            lines = [f'本景后面还有 {self.count} 处写着同一个值'
                     f'（{self._names(self.sites)}），和源一样 —— '
                     '它们**不会**盖回这次改动。']
            lines.append(f'但那 {self.count} 处仍是独立写法：源以后再改，'
                         f'仍只到{self.sites[0].label}有效。')
            return '\n'.join(lines)
        head = (f'⚠ 本景后面还有 {self.count} 处单独' + _said('改了', what)
                + f'（{self._names(self.sites)}）')
        if self.same_at and self.sites[0].at:
            head += f'，写的都是 `{self.sites[0].at}`'
        elif not self.same_at:
            kinds = _at_kinds(self.sites)
            if kinds:
                head += f'，写法各不相同（{kinds}）'
        lines = [head]
        if new_at is None:
            lines.append(f'改最初那一行（源）只到{self.sites[0].label}有效。')
        else:
            bad = self.conflicts(new_at)
            if bad:
                lines.append(f'其中{self._names(bad)}写的不是这个值，'
                             '会把这次改动盖回去。')
            else:
                lines.append(f'那 {self.count} 处写的都是这个值 —— 不会盖回'
                             '这次改动（但仍是同一写法的重复）。')
        if self.level == NOTICE:
            lines.append(f'要一次改到底：用「一并改成一样」（合并回源）把那 '
                         f'{self.count} 处改成同一个值（逐处确认、可撤销）。')
        return '\n'.join(lines)


def _label_phrase(label: str) -> str:
    """
    `背景` → `背景的尺寸`；`CG` → `CG 的尺寸`（拉丁字母后面留一个空格）。

    行里的 label 是 `LAYER_CN_TAG` 给的人话（`背景` / `CG` / `界面` / 也可能是
    没登记过的 tag 原文）—— 直接拼中文会得到 `CG的尺寸` 这种挤在一起的写法。
    """
    label = str(label or '').strip()
    if not label:
        return '这一行'
    pad = ' ' if label[:1].isascii() and label[:1].isalnum() else ''
    return f'{label}{pad}的尺寸'


def _said(verb: str, what: str) -> str:
    """
    `改了` + `CG 的尺寸` → `改了 CG 的尺寸`；中文开头不留空格（`改了背景的尺寸`）。

    中英混排的这点空隙在 340px 的右栏里特别显眼（`改了CG 的尺寸` 挤成一团），
    所以**规则只写这一处**，两个句子都用它。
    """
    what = str(what or '')
    return f'{verb} {what}' if what[:1].isascii() else f'{verb}{what}'


def _at_kinds(sites, limit: int = 3) -> str:
    """几处各写了什么（去重、不带 at 的写"原样"），给"写法各不相同"用。"""
    seen = []
    for site in sites:
        word = f'`{site.at}`' if site.at else '原样（不带 at）'
        if word not in seen:
            seen.append(word)
    if not seen:
        return ''
    return ' / '.join(seen[:limit]) + ('…' if len(seen) > limit else '')


def override_advice(beats, source, *, kind: str = 'scene', tag: str = '',
                    prefetched: dict = None, source_at=None) -> OverrideAdvice:
    """
    第 `source` 拍写下的那个对象，在本景里**后面还有多少处单独改写**。

    `kind='scene'` → 背景（`tag` 忽略，三种背景写法都算同一个图层）；
    `kind='layer'` → 那一条 `show <tag> …`。

    `prefetched` 是 `override_map(beats, 更早的起点)` 的结果（面板一帧多行时
    复用同一次扫描）；不给就现扫。

    `source_at` 是**源那一行自己写着**的 at（`''` = 原样；`None` = 不知道）。
    给了才能分清"那几处写的是另一个值（会盖回）"与"那几处本来就写着同一个
    值（不会盖回）"，见 `OverrideAdvice.agreed`。给 `None` 时一律按"可能会
    盖回"处理 —— 不知道就别往轻里说。

    什么时候返回 `unknown=True`：

      · `source < 0`（跨章继承，压根没有"最初那一拍"）→ **不算**，返回
        `level='none'`（改不了就没有"改源被盖回"这回事；
        `size_rows` 那边 `why` 已经说清了）；
      · `beats` 不是序列 / 解析抛异常 → `unknown=True` + `level='hint'`。
    """
    try:
        beats = list(beats or ())
    except TypeError:
        return OverrideAdvice(level=HINT, unknown=True,
                              reason='拍表读不出来')
    try:
        src = int(source)
    except (TypeError, ValueError):
        return OverrideAdvice(level=HINT, unknown=True, reason='出处不是拍号')
    if src < 0:
        # 没有"最初那一拍"：这一行本来就改不了（`why` 会说），不冒充"0 处"
        return OverrideAdvice(level=NONE, unknown=False,
                              reason='没有最初那一拍（多半是上一章继承来的）')
    if not 0 <= src < len(beats):
        return OverrideAdvice(level=HINT, unknown=True,
                              reason='出处拍号越界')
    sites = ()
    try:
        table = prefetched if prefetched is not None \
            else override_map(beats, src)
        key = 'scene' if str(kind or '') == 'scene' else str(tag or '')
        # 桶是按 tag 分的（背景统一进 `'scene'`），所以这里只按拍号切片 ——
        # `prefetched` 若从更早的起点扫起，会把 `src` 之前/本拍的也带进来。
        pool = tuple(table.get(key) or ())
        sites = tuple(s for s in pool if s.index > src)
    except Exception as e:                         # noqa: BLE001 - 算不出就降档
        swallow('story.override_advice', e)
        return OverrideAdvice(level=HINT, unknown=True,
                              reason='统计时出错了')
    level = NONE if not sites else (NOTICE if len(sites) >= NOTICE_AT else HINT)
    advice = OverrideAdvice(level=level, sites=sites, source_at=source_at)
    if advice.agreed and level == NOTICE:
        # 那几处写的都跟源一样：**仍然要说**（源下次再改还是只到第一处），
        # 但没到"你要往回收"的程度 —— 降回一句话，不上琥珀色。
        advice = OverrideAdvice(level=HINT, sites=sites, source_at=source_at)
    return advice


__all__ = ['HINT', 'LEVELS', 'MAX_NAMES', 'NONE', 'NOTICE', 'NOTICE_AT',
           'OverrideAdvice', 'Site', 'override_advice', 'override_map']
