"""
stage_snapshot.py
=================
舞台快照：把 Beat 序列推导成"每一拍舞台上到底是什么样"，并且**标出变化**。

为什么需要它（二）：
  Excel 里的 Scene / Show / Hide 是三列**扁平**数据，文案看到的是
  「这一行写了什么」，而不是「这一句台词播出来时玩家看见什么」。
  立绘叠加、位置变换、背景切换、音乐起停都要靠脑子做叠加运算——
  于是"这句台词时爱莲还在台上吗""音乐是这时候停的吗"这类问题
  只能靠跑游戏去验证。

第一版快照把**整台舞台每行重抄一遍**，结果每行看起来都一样，还是看不出
"哪行切了 BGM、谁入场"（用户原话：看起来都一样，无法快速看出）。所以现在
每一拍都算出一份**变化清单**（delta）：

  · 「本拍变化」列 —— 只列这一步真正改了什么（`+爱莲 换表情 开心`、
    `♪ 音乐 → bgm/夜.mp3`、`−蓝 退场`）。一眼扫下来就是演出时间轴。
  · 各状态列 —— 仍然给全量状态，但**继承来的值标灰、本拍变化的值标黄**，
    这样"谁一直是站着的""哪一刻变的"同时看得到。

推导规则与 script_table.render_beat_to_rpy 保持一致（这是关键：
预览和真正生成的脚本必须是同一个语义，否则预览就是骗人的）：
  · scene  → 清空全部立绘，重设背景
  · show   → 按 `图像@变换` 叠上去；同一图像已有则原地更新（不重复）
  · hide   → 移除该图像
  · StageOrder 存在时按它在源码里的先后应用（hide+show = 重置立绘）
  · 音乐是"通道状态"：play/queue 起播，stop 停
"""

from __future__ import annotations

from typing import Optional

from storygraph.core.vocabulary import (
    LAYER_CN_TAG as _LAYER_CN_TAG,
    NON_ACTOR_TAGS as _NON_ACTOR_TAGS,
)
from storygraph.story.beat_model import _split_show_items, _split_hide_items

# 「哪些 tag 是图层而不是角色立绘」已收敛到
# `storygraph.core.vocabulary.NON_ACTOR_TAGS`（顶部以 `_NON_ACTOR_TAGS` 导入）。
# 2026-09-28 之前这里与 `dsl.NON_ACTOR_TAGS` **内容不同**——同一个画面，
# 编译器与快照会给出不同结论，那不是重复而是分歧。


def _stage_order(beat) -> list:
    """
    取这一拍的舞台指令顺序。

    没有 StageOrder（老表/手工新增行）时退化成 scene→show→hide，
    与 render_beat_to_rpy 的兜底一致。
    """
    order = [t.strip().lower() for t in (beat.stage_order or '').split(',')
             if t.strip()]
    if order:
        return order
    fallback = []
    if beat.scene:
        fallback.append('scene')
    if beat.show:
        fallback.append('show')
    if beat.hide:
        fallback.append('hide')
    return fallback


def _apply_show(actors: list, image: str, at: str, scene: str = ''):
    """
    把一张图放上舞台。

    两类东西**不算立绘**，不该进 actors：
      · 背景 / CG / UI 这种非角色图层（tag 在 _NON_ACTOR_TAGS 里）
      · 和当前背景同 tag 的图（`show bg rooftop` 是换背景）
    否则快照会显示"台上站着 bg rooftop"，文案会以为要摆立绘。

    Ren'Py 的立绘语义：同一 **tag** 的 `show` 是"换表情"（原地替换），
    不是叠一张新的。所以 `show eileen happy` 要把台上的 `eileen sad`
    换掉，否则快照会显示两个爱莲——正是文案最容易被骗的地方。
    """
    tag = image.split()[0] if image else ''
    scene_tag = scene.split()[0] if scene else ''
    if tag and (tag in _NON_ACTOR_TAGS or tag == scene_tag):
        return

    for slot in actors:
        if slot[0] == image:
            slot[1] = at or slot[1]
            return
    if tag:
        for slot in actors:
            if slot[0].split()[0] == tag:
                slot[0] = image
                slot[1] = at or slot[1]
                return
    actors.append([image, at])


def _apply_hide(actors: list, image: str):
    """摘掉立绘：全名（`hide eileen happy`）与 tag（`hide eileen`）都认。"""
    actors[:] = [s for s in actors
                 if not (s[0] == image or s[0].startswith(image + ' '))]


#: 非角色图层的中文名（进「本拍变化」列给人看）：已收敛到
#: `storygraph.core.vocabulary.LAYER_CN_TAG`，顶部以同名私有别名导入。


def _layer_kind(image: str, scene: str) -> Optional[str]:
    """
    这张图属于哪一类"非角色图层"；既不是角色也不是图层（幂等重复）返回 None。

    `show bg rooftop` 是**换背景**（不是图层）；`show cg cg01` / `show ui x`
    是图层。区分它们，快照才能既不说"台上站着 bg"，又不漏掉 CG 切换。
    """
    tag = image.split()[0] if image else ''
    if not tag:
        return None
    scene_tag = scene.split()[0] if scene else ''
    if tag == scene_tag or tag in ('bg', 'background'):
        return 'scene'
    if tag in _NON_ACTOR_TAGS:
        return tag
    return None


def _layer_cn(kind: str) -> str:
    return _LAYER_CN_TAG.get(kind, '图层')


# 列名 → 状态字典里的键。变化标记与色彩都按这些列走。
SNAPSHOT_COLUMNS = ('scene', 'actors', 'music', 'sound', 'voice')


def stage_snapshots(beats: list) -> list:
    """
    逐 Beat 推导舞台状态 + 本拍变化。

    返回 [{line_id, seq, label, speaker, text, scene, actors, music, sound,
           voice, note, changes, display_changes, changed, state}, ...]

      · scene/actors/music/sound/voice  这一拍结束后的**全量**状态
      · changes         本拍真正改动的机器可读清单
                        [{kind, text}, ...]，kind ∈ scene/actor/music/sound/
                        voice/clear
      · changed         列名集合：这些列的值是"本拍变的"（Excel 里标黄），
                        其余是继承来的（标灰）
      · state           完整状态（供引擎导出复用）
    """
    state = {
        'scene': '',
        'actors': [],        # [[image, at], ...]
        # 非角色图层（cg / ui / movie…）：{tag: image}。它们不算"立绘"，
        # 但**也是舞台变化**——只换 CG 的那一拍必须能被看出来，否则
        # 快照/时间轴会显示"什么都没变"，而脚本明明切了图（假阴性）。
        'layers': {},
        'music': '',
        'voice': '',
    }
    out = []

    for beat in beats or []:
        notes = []
        changes = []
        # 舞台状态在这一拍**开始前**的快照。所有"变了没有"的判定都拿它对比。
        # （踩过的坑：更新之后再比，`scene` 已经等于新值，永远判成"没变"，
        #   于是换背景的那一拍显示成"什么都没发生"。）
        before = {
            'scene': state['scene'],
            'actors': [(s[0], s[1]) for s in state['actors']],
            'layers': dict(state['layers']),
        }
        actors = state['actors']

        def note_scene(new_image):
            changes.append({'kind': 'scene', 'column': 'scene',
                            'text': f'背景 → {new_image}'
                            if new_image else '背景 → （清空）'})

        for verb in _stage_order(beat):
            if verb == 'scene':
                image = (beat.scene or '').strip()
                if image:
                    if actors:
                        changes.append(
                            {'kind': 'clear', 'column': 'actors',
                             'text':
                             f'× 清空立绘（{len(actors)} 个）'})
                    actors = []
                    state['actors'] = actors
                    state['scene'] = image
            elif verb == 'show':
                for item in _split_show_items(beat.show):
                    image = (item.get('image') or '').strip()
                    if not image:
                        continue
                    at = (item.get('at') or '').strip() \
                        or (beat.at or '').strip() \
                        or (beat.transform or '').strip()
                    had_actor = [(s[0], s[1]) for s in actors]
                    _apply_show(actors, image, at, state['scene'])
                    state['actors'] = actors
                    if [(s[0], s[1]) for s in actors] != had_actor:
                        added = not any(s[0] == image for s in had_actor)
                        verb_word = '登场' if added else '换表情/换位'
                        changes.append({'kind': 'actor',
                                        'column': 'actors', 'text':
                                        f'+ {image}'
                                        + (f' @ {at}' if at else '')
                                        + f'（{verb_word}）'})
                        continue
                    # 没进 actors：要么是同 tag 换背景，要么是非角色图层
                    # （cg/ui/movie…）。这些同样是"舞台变了"，必须记下来，
                    # 否则"只换 CG"的那一拍会看起来什么都没变。
                    kind = _layer_kind(image, state['scene'])
                    if kind is None:
                        continue                       # 幂等重复，确实没变
                    if kind == 'scene':
                        # `show bg rooftop` 就是换背景
                        if state['scene'] != image:
                            state['scene'] = image
                        continue
                    key = image.split()[0]
                    if state['layers'].get(key) != image:
                        state['layers'][key] = image
                        changes.append({'kind': kind,
                                        'column': 'layer', 'text':
                                        f'{_layer_cn(kind)} → {image}'
                                        + (f' @ {at}' if at else '')})
            elif verb == 'hide':
                for item in _split_hide_items(beat.hide):
                    image = (item.get('image') or '').strip()
                    if not image:
                        continue
                    had = [s for s in actors
                           if s[0] == image or s[0].startswith(image + ' ')]
                    _apply_hide(actors, image)
                    if had:
                        changes.append({'kind': 'actor',
                                        'column': 'actors',
                                        'text': f'− {image}（退场）'})
                    tag = image.split()[0]
                    if state['layers'].pop(tag, None) is not None:
                        changes.append({'kind': 'layer',
                                        'column': 'layer',
                                        'text': f'− 撤下 {image}'})
                    # 退场也把"背景就是这张图"的情况算上
                    if state['scene'] and state['scene'].split()[0] == tag:
                        state['scene'] = ''
                state['actors'] = actors

        # ---- 音频 ----
        if beat.music:
            if (beat.music_action or 'play') == 'stop':
                if state['music']:
                    changes.append({'kind': 'music', 'column': 'music',
                                    'text': '♪ 停止音乐'})
                state['music'] = ''
            else:
                if state['music'] != beat.music:
                    changes.append({'kind': 'music', 'column': 'music',
                                    'text': f'♪ 音乐 → {beat.music}'
                                            + (f' {beat.music_params}'
                                               if beat.music_params else '')})
                state['music'] = beat.music
        elif (beat.music_action or '') == 'stop':
            if state['music']:
                changes.append({'kind': 'music', 'column': 'music',
                                'text': '♪ 停止音乐'})
            state['music'] = ''
        if beat.sound:
            if (beat.sound_action or 'play') == 'stop':
                changes.append({'kind': 'sound', 'column': 'sound',
                                'text': f'♫ 停止音效 {beat.sound}'})
            else:
                changes.append({'kind': 'sound', 'column': 'sound',
                                'text': f'♫ 音效 {beat.sound}'
                                        + (f' {beat.sound_params}'
                                           if beat.sound_params else '')})
            notes.append(f"音效 {beat.sound}")
        if beat.voice:
            if state['voice'] != beat.voice:
                changes.append({'kind': 'voice', 'column': 'voice',
                                'text': f'🎙 语音 {beat.voice}'})
            state['voice'] = beat.voice

        # ---- 变化判定：整块状态 diff（唯一可靠的做法）----
        # 只看 `changes` 会漏：某些写法改了状态却不产生一条文字描述
        # （例如 `show bg X` 换背景）。所以以 diff 为准，再用 changes 里
        # 记的 column 精确到「哪一列变了」。
        changed = {c.get('column') for c in changes if c.get('column')}
        if state['scene'] != before['scene']:
            changed.add('scene')
            if not any(c['kind'] == 'scene' for c in changes):
                note_scene(state['scene'])
        if [(s[0], s[1]) for s in state['actors']] != before['actors']:
            changed.add('actors')
            if not any(c['kind'] == 'actor' for c in changes):
                changes.append({'kind': 'actor', 'column': 'actors',
                                'text': '台上立绘发生变化'})
        if state['layers'] != before['layers']:
            changed.add('layer')
            if not any(c['kind'] == 'layer' for c in changes):
                changes.append({'kind': 'layer', 'column': 'layer',
                                'text': 'CG/图层发生变化'})

        # ---- 消歧：本拍"上了台又立刻被清掉" ----
        # 用户 2026-09-28 报过一个"逻辑矛盾"：第 43 拍的变化清单里同时有
        # `+ ling oh @ i11（登场）` 和 `× 清空立绘`，最终状态却是"台上没人"。
        # **快照没算错**（源码就是 `show ling oh` → `scene bg bg6_2`，
        # scene 会清空全部立绘），但清单读起来像自相矛盾。
        # 这里补一句说明，让人一眼看出"这个 show 被后面的 scene 清掉了"。
        #
        # 判定必须精确，否则会误报：`hide cg03 → scene bg X → show ling shy`
        # （第 67 拍）最后**是有人**的，不该加这句。
        add_at = cdr_at = None
        for i, c in enumerate(changes):
            t = str(c.get('text', ''))
            if c.get('kind') == 'actor' and t.startswith('+') and add_at is None:
                add_at = i
            if c.get('kind') == 'clear' and cdr_at is None:
                cdr_at = i
        if add_at is not None and cdr_at is not None and cdr_at > add_at:
            # 清空之后还有没有"重新登场"？有就说明台上最终有人，不加说明。
            readded = any(str(c.get('text', '')).startswith('+')
                          for c in changes[cdr_at + 1:])
            if not readded and not state['actors']:
                changes.append({
                    'kind': 'note', 'column': '',
                    'text': '注：上面的「+ …（登场）」在本拍内又被「scene」'
                            '清掉了，所以最终台上没人'
                            '（源码顺序如此，快照与生成脚本一致）'})


        if beat.raw:
            notes.append('原始语句')
        if beat.code:
            notes.append('代码块')
        if beat.condition:
            notes.append(f"条件 {beat.condition}")

        out.append({
            'line_id': getattr(beat, 'line_id', '') or '',
            'seq': beat.seq,
            'label': beat.label,
            'speaker': beat.speaker,
            'text': beat.text,
            'scene': state['scene'],
            'actors': _render_actors(state['actors']),
            'music': state['music'],
            # 音效是**一次性**事件，不是状态：只有本拍有
            'sound': (beat.sound
                      if (beat.sound_action or 'play') != 'stop' else ''),
            'voice': beat.voice,
            'note': ' / '.join(notes),
            'changes': changes,
            'display_changes': _render_changes(changes),
            'changed': changed,
            'layers': _render_layers(state['layers']),
            'state': {
                'scene': state['scene'],
                'actors': [[s[0], s[1]] for s in state['actors']],
                'layers': dict(state['layers']),
                'music': state['music'],
                'voice': state['voice'],
                'condition': beat.condition or '',
            },
        })

    return out


def _render_layers(layers: dict) -> str:
    """`{'cg': 'cg cg01', 'ui': 'ui msg'}` → `cg cg01; ui msg`（按 tag 排序，稳定）"""
    return '; '.join(layers[k] for k in sorted(layers))


def _render_changes(changes: list) -> str:
    """
    变化清单 → 一格可读文本（每行一条）。

    每行一条（而不是 `; ` 串成一行）是刻意的：Excel 里换行 + 自动换行
    才能一眼扫出"这一步动了什么"，串起来就又变成一坨看不清的东西。
    """
    return '\n'.join(c['text'] for c in changes if c.get('text'))


def _render_actors(actors) -> str:
    """`[['eileen happy','leftish'], ['mary smile','right']]`
       → `eileen happy@leftish; mary smile@right`"""
    parts = []
    for image, at in actors:
        parts.append(f'{image}@{at}' if at else image)
    return '; '.join(parts)


def snapshot_rows(beats: list) -> list:
    """给 Excel 工作表用的行（字段名已是中文友好的展示形态）。"""
    return stage_snapshots(beats)


def snapshot_summary(beats: list, limit: int = 200) -> list:
    """
    「只看变化」的精简视图：跳过完全没动的拍。

    用户要的是"快速看出哪行切了 BGM、谁入场"。全量表几百行时，
    一屏里可能只有几行真的有变化——这个视图把这几十行单独抽出来，
    是真正的"演出时间轴"。
    """
    out = []
    for s in stage_snapshots(beats):
        if not s['display_changes']:
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def stage_digest(beats: list, index: int) -> str:
    """
    单拍的可读摘要（编辑器里的预览面板用）。

    返回多行文本：变化 / 背景 / 立绘 / 音乐 / 音效 / 语音 / 条件。
    """
    snaps = stage_snapshots(beats)
    if not snaps:
        return '（没有演出单元）'
    if index < 0 or index >= len(snaps):
        index = max(0, min(index, len(snaps) - 1))
    s = snaps[index]
    lines = [f"第 {s['seq']} 拍 · label {s['label'] or '(无)'}"]
    if s['display_changes']:
        lines.append('本拍变化：')
        for ln in s['display_changes'].splitlines():
            lines.append(f'  {ln}')
    else:
        lines.append('本拍变化：（舞台无变化）')
    lines.append(f"背景：{s['scene'] or '（未设置）'}")
    lines.append(f"立绘：{s['actors'] or '（台上没人）'}")
    lines.append(f"音乐：{s['music'] or '（静音）'}")
    if s['sound']:
        lines.append(f"音效：{s['sound']}")
    if s['voice']:
        lines.append(f"语音：{s['voice']}")
    if s['state']['condition']:
        lines.append(f"条件：{s['state']['condition']}")
    if s['note']:
        lines.append(f"备注：{s['note']}")
    return '\n'.join(lines)


def on_stage(beats: list, label: str, index: int) -> list:
    """
    第 index 拍时台上站着的角色（编辑器"谁在场"用）。

    返回 [{'image','at','tag'}, ...]。
    """
    for s in stage_snapshots(beats):
        if s['label'] == label and s['seq'] == index:
            return [{'image': a[0], 'at': a[1],
                     'tag': a[0].split()[0] if a[0] else ''}
                    for a in s['state']['actors']]
    return []
