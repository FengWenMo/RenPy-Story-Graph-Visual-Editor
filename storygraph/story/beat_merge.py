"""
拍级**三向对账**（设计文档 §5 第 6 步：`base` / `left` / `right`）
================================================================================

用户 2026-10-07 问过"要不要像 git 一样加个 index 层"。设计文档的结论是：
我们**已经有** index（隐藏列是真状态、Excel 是视图），缺的是 git 那三件事 ——
其中第 3 件是"**能比较**"：现在只有 `ContentHash` 这种行级指纹，
**没有拍级的三方对账**，所以"表被改了 / 源码被改了 / 两边都改了"分不出来。

⚠ 本轮口径（刻意保守）：**只做只读对账 + 冲突报告，不做自动合并**。
没有并发编辑场景时，合并策略是纯负债；先让"谁改了什么、哪里冲突"看得见。

    fp(beat)          —— 语义指纹（只算**内容列**，不算行号/内部列）
    three_way(...)     —— 按 `line_id` 配对 → 分类每一拍
    format_report(...) —— 人话
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 参与指纹的**内容列**（刻意不含行号 `src_first/src_last`、不含内部隐藏列：
#: 那些变了不代表"内容变了"）。顺序固定 → 指纹可复现。
FINGERPRINT_FIELDS = (
    'kind', 'speaker', 'text', 'scene', 'scene_at', 'show', 'hide', 'at',
    'with_', 'bgm', 'bgm_params', 'sfx', 'sfx_params', 'voice', 'comment',
    'stage', 'raw', 'code', 'opaque', 'condition', 'stage_order', 'op_order',
    'spec_name', 'spec_head', 'loop_header', 'camera_header', 'nest_path',
    'music', 'music_params', 'sound', 'sound_params', 'name',
)


def fingerprint(beat) -> tuple:
    """
    一拍 → **语义指纹**（内容列的元组）。

    为什么不用现成的 `ContentHash`：那是**行级**（整张表/整行）的，
    三方对账要的是"**这一拍**变没变"。两个各有用途，别混。
    """
    return tuple(str(getattr(beat, f, '') or '') for f in FINGERPRINT_FIELDS)


def _key_of(beat) -> str:
    """配对键：`line_id` 优先（稳定 ID），没有就退回 `label.seq`。"""
    kid = str(getattr(beat, 'line_id', '') or '').strip()
    if kid:
        return kid
    return f'{getattr(beat, "label", "")}.{getattr(beat, "seq", 0)}'


def _index(beats) -> dict:
    out = {}
    for b in (beats or ()):
        out[_key_of(b)] = b
    return out


@dataclass
class Delta:
    """一次对账的结论（**只读**：不改任何 beat）。"""

    #: 只有 `left` 改了（相对 base）
    left_changed: list = field(default_factory=list)
    #: 只有 `right` 改了
    right_changed: list = field(default_factory=list)
    #: 两边都改了、而且**改得不一样** → 真冲突
    conflicts: list = field(default_factory=list)
    #: 两边都改了、但结果一致（不算冲突，只是"都动了"）
    both_same: list = field(default_factory=list)
    #: 只在表里有 / 只在源码里有（新增 / 消失）
    added_left: list = field(default_factory=list)
    added_right: list = field(default_factory=list)
    removed_left: list = field(default_factory=list)
    removed_right: list = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """**没有任何冲突**（别的都可以是"正常变化"）。"""
        return not self.conflicts

    def counts(self) -> dict:
        return {'left_changed': len(self.left_changed),
                'right_changed': len(self.right_changed),
                'conflicts': len(self.conflicts),
                'both_same': len(self.both_same),
                'added_left': len(self.added_left),
                'added_right': len(self.added_right),
                'removed_left': len(self.removed_left),
                'removed_right': len(self.removed_right)}

    def to_dict(self) -> dict:
        d = self.counts()
        d.update({'clean': self.clean, 'conflicts': [
            {'key': k, 'base': fp_base, 'left': fp_left, 'right': fp_right}
            for k, fp_base, fp_left, fp_right in self.conflicts]})
        return d


def three_way(base, left, right) -> Delta:
    """
    `base`（上次的表）/ `left`（当前表）/ `right`（从源码解析出来的拍）
    → `Delta`（逐拍判定）。

    判定表（**每个键只看这三份**）：

        left == right              → 无冲突（两边一致；可能都改了，`both_same`）
        left == base               → 只有 right 改（源码侧）
        right == base              → 只有 left 改（表侧）
        否则                        → **冲突**（两边都改且改得不一样）

    代码/源码两侧的"新增/消失"分别记进 `added_*` / `removed_*`，不当冲突 ——
    "多了一拍"与"同一拍被两边改成不同内容"是两件事，混在一起报会淹没真冲突。
    """
    kb, kl, kr = _index(base), _index(left), _index(right)
    d = Delta()
    for key in sorted(set(kl) | set(kr)):
        bl, br = kl.get(key), kr.get(key)
        b0 = kb.get(key)
        if bl is None:
            (d.added_right if b0 is not None else d.added_right).append(key)
            continue
        if br is None:
            d.removed_right.append(key)
            continue
        fl, fr = fingerprint(bl), fingerprint(br)
        f0 = fingerprint(b0) if b0 is not None else None
        if fl == fr:
            if f0 is not None and fl != f0:
                d.both_same.append(key)
            continue
        if f0 is not None and fl == f0:
            d.right_changed.append(key)
            continue
        if f0 is not None and fr == f0:
            d.left_changed.append(key)
            continue
        d.conflicts.append((key, f0, fl, fr))
    # base 里有、两边都没有 = 被删掉了
    for key in sorted(set(kb) - set(kl) - set(kr)):
        d.removed_left.append(key)
    return d


def format_report(d: Delta) -> str:
    """人话（结论在第一条）：**冲突必须首先被看见**。"""
    c = d.counts()
    head = (f'拍级对账：{"没有冲突" if d.clean else f"⚠ {c["conflicts"]} 处冲突"}'
            f'（表改 {c["left_changed"]} · 源码改 {c["right_changed"]}'
            f' · 两边都改且一致 {c["both_same"]}'
            f' · 新增 {c["added_right"]} · 消失 {c["removed_right"]}）')
    if d.clean:
        return head
    lines = [head]
    for key, _f0, _fl, _fr in d.conflicts[:20]:
        lines.append(f'  · {key}：表与源码都改了，且内容不同 —— 需要人来定')
    if len(d.conflicts) > 20:
        lines.append(f'  …还有 {len(d.conflicts) - 20} 处')
    return '\n'.join(lines)


__all__ = ['FINGERPRINT_FIELDS', 'Delta', 'fingerprint', 'three_way',
           'format_report']
