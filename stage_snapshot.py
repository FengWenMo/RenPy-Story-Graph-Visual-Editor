"""
stage_snapshot.py
=================
舞台快照：把 Beat 序列推导成"每一拍舞台上到底是什么样"。

为什么需要它（二）：
  Excel 里的 Scene / Show / Hide 是三列**扁平**数据，文案看到的是
  「这一行写了什么」，而不是「这一句台词播出来时玩家看见什么」。
  立绘叠加、位置变换、背景切换、音乐起停都要靠脑子做叠加运算——
  于是"这句台词时爱莲还在台上吗""音乐是这时候停的吗"这类问题
  只能靠跑游戏去验证。

快照把它算清楚：逐 Beat 维护一个舞台状态，输出一行一拍的派生视图。
它同时是编辑器里的实时预览面板和 Excel 的「舞台快照」工作表。

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

from script_table import _split_show_items, _split_hide_items

# 这些 tag 是"图层"而不是"角色立绘"：背景 / CG / UI / 纯色板。
# 它们会被 show 出来，但不该出现在"台上站着谁"里。
_NON_ACTOR_TAGS = frozenset({
    'bg', 'background', 'cg', 'ui', 'gui', 'movie', 'video', 'logo',
    'black', 'white', 'overlay', 'screen', 'text', 'title', 'splash',
})


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


def stage_snapshots(beats: list) -> list:
    """
    逐 Beat 推导舞台状态。

    返回 [{line_id, seq, label, speaker, text, scene, actors, music,
           sound, voice, note, state}, ...]
      · scene  当前背景的图像名（'' 表示没切过背景）
      · actors 台上立绘 "图像@变换; 图像2@变换2"，按上场顺序
      · music  当前音乐（'' 表示无）
      · state  完整机器可读状态（供引擎导出复用）
    """
    state = {
        'scene': '',
        'actors': [],        # [[image, at], ...]
        'music': '',
        'voice': '',
    }
    out = []

    for beat in beats or []:
        notes = []
        actors = state['actors']

        for verb in _stage_order(beat):
            if verb == 'scene':
                image = (beat.scene or '').strip()
                if image:
                    if actors:
                        notes.append(f'清空立绘 ×{len(actors)}')
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
                    _apply_show(actors, image, at, state['scene'])
                    state['actors'] = actors
            elif verb == 'hide':
                for item in _split_hide_items(beat.hide):
                    image = (item.get('image') or '').strip()
                    if image:
                        _apply_hide(actors, image)
                state['actors'] = actors

        # ---- 音频 ----
        if beat.music:
            if (beat.music_action or 'play') == 'stop':
                state['music'] = ''
            else:
                state['music'] = beat.music
        elif (beat.music_action or '') == 'stop':
            state['music'] = ''
        if beat.sound:
            state['sounds'] = [beat.sound]
            notes.append(f"音效 {beat.sound}")
        if beat.voice:
            state['voice'] = beat.voice

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
            'sound': beat.sound if (beat.sound_action or 'play') != 'stop'
            else '',
            'voice': beat.voice,
            'note': ' / '.join(notes),
            'state': {
                'scene': state['scene'],
                'actors': [[s[0], s[1]] for s in state['actors']],
                'music': state['music'],
                'voice': state['voice'],
                'condition': beat.condition or '',
            },
        })

    return out


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


def stage_digest(beats: list, index: int) -> str:
    """
    单拍的可读摘要（编辑器里的预览面板用）。

    返回多行文本：背景 / 立绘 / 音乐 / 音效 / 语音 / 条件。
    """
    snaps = stage_snapshots(beats)
    if not snaps:
        return '（没有演出单元）'
    if index < 0 or index >= len(snaps):
        index = max(0, min(index, len(snaps) - 1))
    s = snaps[index]
    lines = [f"第 {s['seq']} 拍 · label {s['label'] or '(无)'}"]
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
