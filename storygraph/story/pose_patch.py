"""
`storygraph.story.pose_patch`
=============================
**P4：把"改回空姿势要先 `hide`"补上**（用户 2026-10-05 的第二个目标）。

设计文档：`docs/archive/设计_合成立绘合成与姿势切换_2026-10-05.md` 第 11.4 节。

为什么不能硬编码：姿势表、空姿势的含义、要不要自动补 —— 都是**项目自己的约定**。
所以这里只做三件事：

1. `plan_pose_hides(beats, model, conventions)` —— **纯函数**：算出"哪一拍
   该补一句 `hide <tag>`"。不写盘、不改对象。
2. 只有在项目**声明**了 `hide_before_pose_switch: true` 时，调用方才把结果
   交给渲染器（`render_beats_to_rpy(..., pose_hides=)`）——
   于是补出来的 `hide` 是**生成内容**，照旧走增量补丁的校验与备份，
   **不手工改源文件**。
3. 没声明时也**照样报告**（预览状态行 / 体检报告），只是不自动写。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow


def _spec_of(beat) -> str:
    """这一拍的立绘 spec（`ye base_handdown happy` 这种）。"""
    show = getattr(beat, 'show', None)
    if show is None and isinstance(beat, dict):
        show = beat.get('show')
    show = str(show or '').strip()
    if not show:
        return ''
    # 一拍可能写了多个 `show`（`show a x; show b y`）——只看第一个 tag 的那串
    from storygraph.core.vocabulary import split_stage_items
    first = (split_stage_items(show) or [''])[0].strip()
    return first


def _hide_of(beat) -> set:
    """这一拍显式写了哪些 `hide <tag>`。"""
    out = set()
    raw = getattr(beat, 'hide', None)
    if raw is None and isinstance(beat, dict):
        raw = beat.get('hide')
    for tok in str(raw or '').replace(',', ' ').split():
        if tok:
            out.add(tok)
    for item in (getattr(beat, 'hides', None) or []):
        head = str(item or '').strip().split()
        if head:
            out.add(head[0])
    return out


def plan_pose_hides(beats, model, conventions=None) -> dict:
    """
    `{拍序号: ['hide ye', …]}` —— 哪些拍需要补 `hide` 才能改回空姿势。

    规则（全部来自项目自己的数据）：

      · 该 tag 有**姿势维度**（循环表或从 `when` 推出来的），且 `''` 在里面；
      · 上一拍结束时它的姿势是**非空**的（B）；
      · 这一拍要把它改成**空姿势**（没有选中任何姿势属性）；
      · 这一拍**没有**自己写 `hide <tag>`；
      · 这一拍的 `show` 里**该 tag 出现了**（否则它就该靠 `hide` 消失，
        那是另一回事）。

    `conventions` 只影响"报告口径"（比如清单里声明的 `poses` 更权威），
    不影响判断本身 —— 判断只看数据。
    """
    out = {}
    if model is None or not getattr(model, 'images', None):
        return out
    state = {}          # tag -> 上一拍结束时的姿势值
    poses = {}
    if conventions is not None and getattr(conventions, 'poses', None):
        poses = {k: [str(v) for v in v] for k, v in conventions.poses.items()}
    for idx, beat in enumerate(beats or ()):
        hides = _hide_of(beat)
        spec = _spec_of(beat)
        if spec:
            parts = spec.split()
            tag, attrs = parts[0], parts[1:]
            options = poses.get(tag) or list(model.pose_options(tag) or [])
            option_set = {o.lower() for o in options if o}
            if option_set:
                prev = state.get(tag, '')
                cur = next((a for a in attrs if a.lower() in option_set), '')
                if tag in hides:
                    prev = ''
                # B -> 空（普通）：`show` 表达不出来，必须 hide
                if prev and not cur and tag not in hides:
                    out[int(idx)] = [f'hide {tag}']
                    prev = ''
                state[tag] = cur
        # 显式 hide 之后该 tag 的姿势归零
        for tag in hides:
            state[tag] = ''
    return out


def apply_pose_hides(beats, model, conventions=None) -> dict:
    """
    给每拍挂上"要补的 hide 行"（**不改 beat 的持久字段**，只返回映射）。

    调用方（导出/补丁）把它交给渲染器；编辑器只用来显示提示。
    """
    try:
        return plan_pose_hides(beats, model, conventions)
    except Exception as e:                      # noqa: BLE001 - 算不出来就别挡导出
        swallow('story.pose_patch.apply_pose_hides', e)
        return {}


def pose_hide_report(beats, model, conventions=None) -> list:
    """
    人话报告：`['第 12 拍：ye 从「手放下」改回「普通」要先 hide ye', …]`。

    给预览状态行 / 体检报告用（**开关关掉时也照报**）。
    """
    out = []
    if model is None:
        return out
    plans = plan_pose_hides(beats, model, conventions)
    if not plans:
        return out
    labels = {}
    if conventions is not None:
        labels = dict(getattr(conventions, 'pose_labels', None) or {})
    state = {}
    for idx, beat in enumerate(beats or ()):
        spec = _spec_of(beat)
        tag = spec.split()[0] if spec else ''
        if idx in plans:
            options = (getattr(conventions, 'poses', None) or {}).get(tag) \
                or list(model.pose_options(tag) or [])
            option_set = {o.lower() for o in options if o}
            prev = state.get(tag, '')
            out.append(
                f'第 {idx + 1} 拍：`{tag}` 从「{labels.get(prev, prev)}」'
                f'改成「{labels.get("", "普通")}」，但 `show` 表达不出来 —— '
                f'要先 `hide {tag}`。')
            state[tag] = ''
            continue
        if tag:
            option_set = {o.lower() for o in
                          ((getattr(conventions, 'poses', None) or {})
                           .get(tag) or list(model.pose_options(tag) or []))
                          if o}
            cur = next((a for a in spec.split()[1:]
                        if a.lower() in option_set), '')
            if tag in _hide_of(beat):
                cur = ''
            state[tag] = cur
    return out


__all__ = ['plan_pose_hides', 'apply_pose_hides', 'pose_hide_report']
