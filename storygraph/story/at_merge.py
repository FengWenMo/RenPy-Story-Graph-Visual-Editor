# -*- coding: utf-8 -*-
"""
storygraph.story.at_merge
=========================
**合并回源**：把本景后面那几处"单独改写"的 at 改成和源那一行一样（2026-10-31）。

## 它解决的那半个问题

`story.override_advice` 只**报数**：改源之后本景还有 N 处又 `show` 了一次同一
个对象，源里的 `at` 从那一拍起不再生效。报完就完了 —— 用户要么一处一处手改，
要么接受"整景只改了前 12 拍"。

这个模块补上后半步：**把那 N 处的 `at` 改成和源一样的值**。

## 为什么是"改成一样的值"，不是"删掉那几行"

最容易想到的做法是"把后来那几条 `show … at …` 删了，让源的 at 生效"。
**不行**，两个原因：

  ① Ren'Py 里 `show` 会**重建** displayable：删掉 `at` 不等于"继承前面的 at"，
     等于回到**原样**（不带变换）。删完看着像"合了"，其实是把整景改成原样。
  ② 那几条 `show` 往往**同时换了图**（`show cg cg02`）—— 删行就把图换了。

所以合并 = **只动 `@at` 那一段字**，`show` 里的图名、`behind / with / as`
一个字都不碰。产物里那几行的 `at` 变成同一个值，于是"从第 943 拍起变回
原样"这件事不再发生 —— 而**行数不变、图不变、行序不变**。

## 纪律（`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5 的 L4）

动的是用户的 `.rpy` 行，所以：

  · **逐处**：每一处单独列出来（拍号、原文、改后），确认框里一个勾一个；
  · **可撤**：整批算**一次**编辑，撤销一次全回去；
  · **改不了就说**：复杂子句（`behind / with / at 全写`）、`expression`、
    拍级 `at` 会盖住的，一律进 `skipped` 并说清为什么 —— **绝不静默跳过**
    （跳过而不说，人就会以为整景都合了，这正是这轮要治的病）；
  · 只改**内存里的 Beat**：落盘仍走既有 patch/导出 diff 门禁。

## 数字同源

处数**不自己数**：直接吃 `override_advice(beats, source, kind, tag).sites` ——
面板上那句"还有 2 处"和这里真要改的 2 处必须是**同一批拍**。自己再扫一遍
是本项目最贵的那类缺陷（"报的数字 ≠ 实际改的数字"）的入口。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import BACKGROUND_TAGS


def _tag_of_image(image: str) -> str:
    """`cg cg1` → `cg`（图层桶按 tag 分；背景三种写法归一个桶）。"""
    words = str(image or '').split()
    return words[0] if words else ''


def _same_tag(a: str, b: str) -> bool:
    """背景的 `bg` / `background` / `bg_` 是**同一个图层**（`same_layer_tag`）。"""
    from storygraph.core.vocabulary import same_layer_tag

    a, b = str(a or ''), str(b or '')
    return a == b or same_layer_tag(a, b)


def source_at_of(beats, index: int, kind: str = 'scene', tag: str = '') -> str:
    """
    **源那一行自己写着**的 at（空 = 原样）。

    为什么不能拿"当前这一拍看到的 at"（`frame.scene_at`）当合并的目标：那一拍
    很可能已经在某处单独改写**之后**了 —— 看到的是那处的值（比如 `half`），
    拿它回填会把"源本来写的是 `fit`"这件事一起改掉，整景反而更乱。

    取法（与 `override_advice.override_map` 读的是同一批行）：

      · 源那一拍自己有 `show <这个 tag> …` → 那一条的 `at`（`item_at`：
        item 自己的 > 拍级 `at` > 拍级 `transform`）；
      · 否则是 `scene` 写的背景 → `beat.scene_at`；
      · 都没有 → `''`（原样）。
    """
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return ''
    beats = list(beats or ())
    if not 0 <= idx < len(beats):
        return ''
    beat = beats[idx]
    tag = str(tag or '').strip()
    want = 'bg' if str(kind or '') == 'scene' else tag
    if not want:
        return ''
    from storygraph.story.beat_model import split_show_items
    from storygraph.story.beat_stage_parse import item_at

    last = None
    for item in split_show_items(str(getattr(beat, 'show', '') or '')):
        image = str(item.get('image') or '').strip()
        if _same_tag(_tag_of_image(image), want):
            last = item
    if last is not None:
        return str(item_at(last, beat) or '').strip()
    if want in BACKGROUND_TAGS or str(kind or '') == 'scene':
        return str(getattr(beat, 'scene_at', '') or '').strip()
    return ''


@dataclass(frozen=True)
class MergeStep:
    """一处要改的行（一个拍、一条 show 条目）。"""

    index: int = -1
    image: str = ''
    #: 改写**前**这一拍的整个 `show` 字段（原文，撤销用）
    before: str = ''
    #: 改写后（只有那一条的 `@at` 变了）
    after: str = ''
    before_at: str = ''
    after_at: str = ''
    #: 那一条的原文（`cg cg1@half`）与改后，给人对眼睛
    clause_before: str = ''
    clause_after: str = ''

    @property
    def label(self) -> str:
        return f'第 {self.index + 1} 拍'

    def as_dict(self) -> dict:
        return {'index': self.index, 'image': self.image,
                'before': self.before, 'after': self.after,
                'before_at': self.before_at, 'after_at': self.after_at,
                'clause_before': self.clause_before,
                'clause_after': self.clause_after}


@dataclass(frozen=True)
class MergeSkip:
    """一处**改不了**的（说清为什么，绝不静默跳过）。"""

    index: int = -1
    reason: str = ''

    @property
    def label(self) -> str:
        return f'第 {self.index + 1} 拍'

    def as_dict(self) -> dict:
        return {'index': self.index, 'reason': self.reason}


@dataclass
class MergePlan:
    """
    一次合并的计划（**只算，不动 Beat**；`apply` 才写内存）。

    与 `app.stage.AtEdit` 的接口刻意一致（`ok` / `apply` / `revert` /
    `diff_text`），窗口层的门禁、日志、撤销栈都能直接用它 —— 差别只有一处：
    它一次动**好几拍**，所以没有 `beat_index`，有 `steps`。
    """

    kind: str = 'scene'
    tag: str = ''
    #: 人话（`背景` / `CG` / 没登记的 tag 原文）—— 措辞从 `LAYER_CN_TAG` 来
    label: str = '背景'
    source: int = -1
    #: 要写成的值（源那一行自己的 at；`''` = 原样）
    at: str = ''
    steps: tuple = ()
    skipped: tuple = ()
    #: 非空 = 这次不算成功（没有要合的 / 全改不了）
    reason: str = ''
    _beats: list = field(default=None, repr=False, compare=False)

    # ---------- 数字（从 steps 推，不另存） ----------

    @property
    def ok(self) -> bool:
        return not self.reason and bool(self.steps)

    @property
    def count(self) -> int:
        return len(self.steps)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def beats(self) -> tuple:
        return tuple(s.index for s in self.steps)

    @property
    def value_text(self) -> str:
        return f'`{self.at}`' if self.at else '原样（不带 at）'

    # ---------- 写 / 撤 ----------

    def apply(self, selected=None) -> bool:
        """
        写进内存里的 Beat；成功返回 True。

        `selected` = 只要这几处的拍号（确认框里勾掉的就不动）；`None` = 全部。
        返回 True 时 `steps` 已收窄成**实际改过的**那几处 —— 撤销栈据此原样
        还原，不多撤一处。
        """
        if not self.ok:
            return False
        beats = self._beats if self._beats is not None else []
        keep = []
        for step in self.steps:
            if selected is not None and step.index not in selected:
                continue
            if not 0 <= step.index < len(beats):
                continue
            try:
                beats[step.index].show = step.after
            except Exception as e:             # noqa: BLE001 - 一处失败就停
                swallow('story.at_merge.apply', e)
                break
            keep.append(step)
        if not keep:
            return False
        self.steps = tuple(keep)
        return True

    def revert(self) -> bool:
        """撤销：逐处还原成计划时的原文（可重复调用）。"""
        beats = self._beats if self._beats is not None else []
        for step in self.steps:
            if not 0 <= step.index < len(beats):
                continue
            try:
                beats[step.index].show = step.before
            except Exception as e:             # noqa: BLE001 - 撤不动就继续
                swallow('story.at_merge.revert', e)
        return True

    # ---------- 给人看 ----------

    def _names(self, steps, limit: int = 4) -> str:
        steps = list(steps)
        if not steps:
            return ''
        nums = '、'.join(str(s.index + 1) for s in steps[:limit])
        more = len(steps) - limit
        return f'第 {nums} 拍' + (f' 等 {len(steps)} 处' if more > 0 else '')

    def diff_text(self) -> str:
        """确认框正文：**先说是哪几拍、会发生什么**，再逐处列 diff。"""
        if self.reason:
            return f'合并回源没做：{self.reason}'
        lines = [f'合并回源：把本景 {self.count} 处单独改写的 at '
                 f'改成 {self.value_text}',
                 f'源：第 {self.source + 1} 拍 · {self.label}']
        if self.steps:
            lines.append(f'会改：{self._names(self.steps)}')
        lines.append('')
        for step in self.steps:
            lines.append(f'· {step.label} · {step.image}：'
                         f'{step.before_at or "原样"} → '
                         f'{step.after_at or "原样"}')
            lines.append(f'    - {step.clause_before}')
            lines.append(f'    + {step.clause_after}')
        if self.skipped:
            lines.append('')
            lines.append(f'改不了 {self.skipped_count} 处（这几处要你自己改）：')
            for skip in self.skipped:
                lines.append(f'· {skip.label}：{skip.reason}')
        lines.append('')
        lines.append('只改那几处的 `@at`：不删行、不加行、图名与'
                     '`behind/with/as` 一个字都不动。')
        lines.append('现在只改内存里的 Beat，不写盘 —— 导出时还会逐行给你看 '
                     'diff，那一步仍能反悔。')
        return '\n'.join(lines)


def _skip_reason(res) -> str:
    """`set_item_at` 的结局 → 人话（**只在这一处**写）。"""
    from storygraph.story.beat_stage_parse import (AT_BLOCKED, AT_COMPLEX,
                                                   AT_EXPRESSION, AT_MISSING)

    if res == AT_MISSING:
        return '这一拍找不到那条 show（可能写成了 expression，或被手改过）'
    if res == AT_COMPLEX:
        return ('这条 show 是复杂子句（含 at/behind/with 等），'
                '请在「高级区 · Stage」里手改；预览不猜')
    if res == AT_EXPRESSION:
        return '`show expression` 的图像名是表达式，改不了它的 at'
    if res == AT_BLOCKED:
        return '这一拍有拍级 at / transform 会盖住它，只改这一条改不动'
    return '这一处改不了'


def plan_at_merge(beats, source: int, *, kind: str = 'scene', tag: str = '',
                  label: str = '') -> MergePlan:
    """
    计划一次**合并回源**（只算，不动 Beat）。

    `kind='scene'` → 背景（`tag` 忽略，三种背景写法同一个桶）；
    `kind='layer'` → 那一条 `show <tag> …`。

    要写成的值不是调用方给的，而是**源那一行自己写着的**（`source_at_of`）：
    尺寸面板显示的是"当前这一拍看到的 at"，它可能已经被某处单独改写覆盖了
    —— 拿它当目标会把源一起改掉。

    `skipped` 里的每一处都带原因；`steps` 与 `skipped` 加起来才是"本景还有
    几处单独改写"那个数（与 `override_advice` 同一个来源）。
    """
    from storygraph.story.override_advice import override_advice

    beats = list(beats or ())
    try:
        src = int(source)
    except (TypeError, ValueError):
        return MergePlan(kind=kind, tag=str(tag or ''), label=label,
                         reason='源不是拍号')
    kind = 'layer' if str(kind or '') == 'layer' else 'scene'
    tag = str(tag or '').strip()
    if not beats:
        return MergePlan(kind=kind, tag=tag, label=label, source=src,
                         reason='拍表是空的')
    if not 0 <= src < len(beats):
        return MergePlan(kind=kind, tag=tag, label=label, source=src,
                         reason='源那一拍的拍号越界')
    if kind == 'layer' and not tag:
        return MergePlan(kind=kind, tag=tag, label=label, source=src,
                         reason='没有指定图层 tag')
    at = source_at_of(beats, src, kind=kind, tag=tag)
    if '@' in at:
        return MergePlan(kind=kind, tag=tag, label=label, source=src, at=at,
                         reason=f'源里的变换「{at}」带 `@`，不敢据它改别的行')
    plan = MergePlan(kind=kind, tag=tag, label=label, source=src, at=at,
                     _beats=beats)
    advice = override_advice(beats, src, kind=kind, tag=tag)
    if advice.unknown:
        plan.reason = ('统计不出本景有几处单独改写'
                       + (f'（{advice.reason}）' if advice.reason else ''))
        return plan
    if not advice.sites:
        plan.reason = '本景没有别处单独改写这个对象（没有要合并的）'
        return plan
    from storygraph.story.beat_model import show_item_part, split_show_items
    from storygraph.story.beat_stage_parse import (AT_OK, set_item_at)

    steps: dict = {}
    skipped: list = []
    seen: set = set()
    for site in advice.sites:
        if site.index in seen:
            # 同一拍里同一个 tag 写了多条：`set_item_at` 改的永远是**最后
            # 一条**（Ren'Py 侧后 show 的赢），所以一处一拍就够。
            continue
        seen.add(site.index)
        if str(site.at or '') == at:
            continue                           # 本来就是同一个值：不动
        hit = beats[site.index] if 0 <= site.index < len(beats) else None
        if hit is None:
            skipped.append(MergeSkip(index=site.index, reason='拍号越界'))
            continue
        before = str(getattr(hit, 'show', '') or '')
        res = set_item_at(before, _tag_of_image(site.image), at, hit)
        if res.status != AT_OK:
            skipped.append(MergeSkip(index=site.index,
                                     reason=_skip_reason(res.status)))
            continue
        after_part, before_part = '', ''
        try:
            after_items = split_show_items(res.text)
            if 0 <= res.index < len(after_items):
                after_part = show_item_part(after_items[res.index])
            before_items = split_show_items(before)
            if 0 <= res.index < len(before_items):
                before_part = show_item_part(before_items[res.index])
        except Exception as e:                 # noqa: BLE001 - 只影响展示
            swallow('story.at_merge.plan.parts', e)
        steps[site.index] = MergeStep(
            index=site.index, image=site.image, before=before,
            after=res.text, before_at=res.before_at, after_at=at,
            clause_before=before_part, clause_after=after_part)
    plan.steps = tuple(steps[i] for i in sorted(steps))
    plan.skipped = tuple(sorted(skipped, key=lambda s: s.index))
    if not plan.steps:
        if plan.skipped:
            plan.reason = (f'{plan.skipped_count} 处都改不了（复杂子句 / '
                           '拍级 at），只能手改')
        else:
            plan.reason = (f'那 {advice.count} 处已经都写着 {plan.value_text} 了'
                           '（没有要改的）')
    return plan


__all__ = ['MergePlan', 'MergeSkip', 'MergeStep', 'plan_at_merge',
           'source_at_of']
