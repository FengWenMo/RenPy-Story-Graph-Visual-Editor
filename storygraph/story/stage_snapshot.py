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

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import (
    LAYER_CN_TAG as _LAYER_CN_TAG,
    NON_ACTOR_TAGS as _NON_ACTOR_TAGS,
)
from storygraph.story.beat_stage_parse import item_at
from storygraph.story.beat_model import (
    _split_hide_items,
    _split_show_items,
    menu_caption_text,
)

# 「哪些 tag 是图层而不是角色立绘」已收敛到
# `storygraph.core.vocabulary.NON_ACTOR_TAGS`（顶部以 `_NON_ACTOR_TAGS` 导入）。
# 2026-09-28 之前这里与 `dsl.NON_ACTOR_TAGS` **内容不同**——同一个画面，
# 编译器与快照会给出不同结论，那不是重复而是分歧。


def _stage_order(beat) -> list:
    """
    取这一拍的舞台指令顺序。

    没有 StageOrder（老表/手工新增行）时退化成 scene→show→hide，
    与 render_beat_to_rpy 的兜底一致。

    ⚠ **2026-10-05 改用 `Beat.stage_verbs`**（用户实测报："我已经保存该卡片了，
    可是当前舞台变化没有生效"）：从前这里只回放 `stage_order` / `op_order`
    里**列出来的** verb，而卡片加一张立绘只写 `beat.show` —— `stage_order`
    还停在 `'scene'`（解析时那一拍只有 `scene black`）→ 快照与预览都看不到
    这个人，**而产物里其实有 `show`**（渲染器会补齐）。

        "当前舞台：立绘（台上没人）"  ← 与产物、与卡片都不一致

    现在与渲染器共用同一条规矩（见 `Beat.stage_verbs`）：结构化列里有的
    verb 一定出现，顺序仍按记录的真迹排。
    """
    return list(beat.stage_verbs)


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


def _as_src(value) -> int:
    """
    "第几拍写的" → 整数；认不出就 -1（不知道）。

    为什么不用 `int(value or -1)`：`-1` 是合法值（不知道），而 `0` 会被
    `or` 吞成 `-1` —— 正好把"第 0 拍写的"说成"不知道"。这条坑项目里踩过
    不止一次（`int(x or -1)` 把第 0 拍当没载入）。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _apply_show_verb(item, beat, actors, state, src_index=-1):
    """
    处理一个 `show` item（角色 / 非角色图层 / 换背景）。

    返回**新的 actors 列表**与要追加的 changes。

    `src_index` 是"这条 show 写在第几拍"（0 基）—— 只记进 `scene_src` /
    `layer_src` 两个**内部键**，给"改尺寸"找得到该改哪一行用（2026-10-31）。

    ⚠ `actors` 是**重新绑定**的（`_apply_show` 可能就地改）——
    所以必须把新列表返回给调用方，不能只改 `state['actors']`
    （外面那个局部变量 `actors` 还指着旧对象）。
    """
    changes = []
    image = (item.get('image') or '').strip()
    if not image:
        return actors, changes
    # 口径唯一（`beat_stage_parse.item_at`）：item 自己 > 拍级 `at` > `transform`。
    # 抽出去是因为 `story.override_advice` 要数"哪几处单独改了尺寸" ——
    # 两份各算一遍，报告里的处数就会和实际会盖回去的处数对不上。
    at = item_at(item, beat)
    had_actor = [(s[0], s[1]) for s in actors]
    _apply_show(actors, image, at, state['scene'])
    state['actors'] = actors
    if [(s[0], s[1]) for s in actors] != had_actor:
        added = not any(s[0] == image for s in had_actor)
        verb_word = '登场' if added else '换表情/换位'
        changes.append({'kind': 'actor', 'column': 'actors', 'text':
                        f'+ {image}'
                        + (f' @ {at}' if at else '')
                        + f'（{verb_word}）'})
        return actors, changes
    # 没进 actors：要么是同 tag 换背景，要么是非角色图层（cg/ui/movie…）。
    # 这些同样是"舞台变了"，必须记下来，
    # 否则"只换 CG"的那一拍会看起来什么都没变。
    kind = _layer_kind(image, state['scene'])
    if kind is None:
        return actors, changes                 # 幂等重复，确实没变
    if kind == 'scene':
        # `show bg rooftop` 就是换背景；`at`（`show bg X at bgfit`）同样要
        # 写进**持续状态**（Ren'Py 里变换挂在背景 displayable 上）。
        if state['scene'] != image:
            state['scene'] = image
        state['scene_at'] = at
        state['scene_src'] = _as_src(src_index)
        state['unknown'].discard('scene')
        return actors, changes
    key = image.split()[0]
    # 图层自己的变换：`show cg cg01 at cg38402160` —— show 时记下，
    # hide 时清掉（下面 `_apply_hide_verb`）。同一图重复 show 时引擎会
    # 重新套 transform，所以这里**覆盖**而不是"只记第一次"。
    state['layer_at'][key] = at
    state.setdefault('layer_src', {})[key] = _as_src(src_index)
    if state['layers'].get(key) != image:
        state['layers'][key] = image
        changes.append({'kind': kind, 'column': 'layer', 'text':
                        f'{_layer_cn(kind)} → {image}'
                        + (f' @ {at}' if at else '')})
    return actors, changes


def _apply_hide_verb(item, actors, state):
    """
    处理一个 `hide` item（角色退场 / 撤图层 / 撤掉当前背景）。

    返回新的 actors 列表与要追加的 changes。
    """
    changes = []
    image = (item.get('image') or '').strip()
    if not image:
        return actors, changes
    had = [s for s in actors
           if s[0] == image or s[0].startswith(image + ' ')]
    _apply_hide(actors, image)
    if had:
        changes.append({'kind': 'actor', 'column': 'actors',
                        'text': f'− {image}（退场）'})
    tag = image.split()[0]
    if state['layers'].pop(tag, None) is not None:
        changes.append({'kind': 'layer', 'column': 'layer',
                        'text': f'− 撤下 {image}'})
    state['layer_at'].pop(tag, None)
    srcs = state.get('layer_src')
    if isinstance(srcs, dict):
        srcs.pop(tag, None)
    # 退场也把"背景就是这张图"的情况算上
    if state['scene'] and state['scene'].split()[0] == tag:
        state['scene'] = ''
        state['scene_at'] = ''
        state['scene_src'] = -1
    return actors, changes


def _initial_state(initial_state: Optional[dict]) -> dict:
    """
    快照的起始状态。

    `initial_state=None`（默认）时与历史行为**逐字节一致**：
    背景空 / 台上没人 / 没有图层 / 没有音乐 / 没有语音。

    传了 `initial_state` 就是"接着上一章演"（跨章连续性用）：
    值是上一章的出口状态，`unknown` 是里面**还不能确定**的元素集合
    （例如上游有分支汇聚 —— 不知道玩家走的是哪条线）。
    """
    init = dict(initial_state or {})
    unknown = set(init.get('unknown') or ())
    actors = []
    for item in init.get('actors') or ():
        try:
            image = str(item[0] or '')
            at = str(item[1] or '') if len(item) > 1 else ''
        except (TypeError, IndexError, KeyError):
            continue
        if image:
            actors.append([image, at])
    return {
        'scene': str(init.get('scene') or ''),
        #: 背景自己的变换是**持续状态**（`scene bg X at bgfit` 之后，直到
        #: 下一次 `scene` 之前，背景一直带着这个 transform）—— 2026-10-30
        #: ⑦ P0 补上；老 `initial_state` 没有这两个键，缺省即"空/空"。
        'scene_at': str(init.get('scene_at') or ''),
        'actors': actors,
        'layers': {str(k): str(v)
                   for k, v in (init.get('layers') or {}).items()},
        #: 图层 tag → 它的 `at`（CG/UI 等非角色图层）。同上，缺省空。
        'layer_at': {str(k): str(v)
                     for k, v in (init.get('layer_at') or {}).items()},
        #: **出处**（2026-10-31）：`scene_src` = 当前背景是第几拍写的（0 基），
        #: `layer_src` = 每个图层 tag 是第几拍 show 的；-1 = 不知道（例如从
        #: 上一章继承来的，跨章没有"本文件的拍号"可说）。
        #:
        #: 为什么要有：背景变换 / 图层变换是**持续状态**，而"改尺寸"必须改
        #: **写它的那一行**。只记值不记出处，UI 就只能在"这一拍自己写了
        #: scene/show"的那 5% 拍上动手（真机实测：1013 个有背景的拍里 972 个
        #: 是继承来的）。它只进 state，**不进显示列**。
        'scene_src': -1,
        'layer_src': {},
        'music': str(init.get('music') or ''),
        'voice': str(init.get('voice') or ''),
        #: 内部标记：这些元素的值"还不知道"，不是"空"。
        'unknown': unknown,
    }


def _apply_audio(beat, state):
    """
    音乐 / 音效 / 语音：就地改 `state`，返回 `(changes, notes)`。

    音效是**一次性**事件、不是状态（只有本拍有）；音乐与语音是状态。
    """
    changes = []
    notes = []
    # 音乐是单值通道：本拍只要**显式**写了 play / stop，它的值就重新已知
    # （上游分支汇聚导致的"不知道"到这里被一个明确的动作替代了）。
    if beat.music or (beat.music_action or '') == 'stop':
        state['unknown'].discard('music')
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
    return changes, notes


def _changed_columns(state, before, changes, note_scene):
    """
    整块状态 diff → `changed` 列集合，并**补上**推断出来的变化描述。

    以 diff 为准（只看 `changes` 会漏：`show bg X` 换背景不产生文字描述），
    再用 `changes` 里记的 column 精确到"哪一列变了"。

    `note_scene` 是调用方的闭包（它只往 `changes` 里加一条背景变化），
    传进来是为了**保持原来的调用形态**（原来 `note_scene` 就在
    `stage_snapshots` 内部定义并使用）。
    """
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
    return changed


def _note_add_then_clear(changes, state):
    """
    消歧：本拍"上了台又立刻被清掉"时补一句说明。

    用户 2026-09-28 报过"逻辑矛盾"：第 43 拍的变化清单里同时有
    `+ ling oh @ i11（登场）` 和 `× 清空立绘`，最终状态却是"台上没人"。
    **快照没算错**（源码就是 `show ling oh` → `scene bg bg6_2`，
    scene 会清空全部立绘），但清单读起来像自相矛盾。

    判定必须精确，否则会误报：`hide cg03 → scene bg X → show ling shy`
    （第 67 拍）最后**是有人**的，不该加这句。
    """
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


def stage_snapshots(beats: list, initial_state: Optional[dict] = None) -> list:
    """
    逐 Beat 推导舞台状态 + 本拍变化。

    `initial_state`（可选，跨章连续性用）：从上一章的出口状态接着演。
    不传时与历史行为**逐字节一致**（全新舞台）。传了以后：
      · 值照常参与推导；
      · `state['unknown']` 里的元素是"还不知道"的（上游分支汇聚/循环），
        本拍只要**显式**写到了它（`scene` / `play|stop music` 等）
        就重新变回已知；没写到就继续"未知"，**绝不替它猜**。

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
    state = _initial_state(initial_state)
    out = []

    for bi, beat in enumerate(beats or []):
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

        def note_scene(new_image):
            changes.append({'kind': 'scene', 'column': 'scene',
                            'text': f'背景 → {new_image}'
                            if new_image else '背景 → （清空）'})

        _apply_stage_ops(beat, state, bi, changes)

        # ---- 音频 ----
        _audio_changes, _audio_notes = _apply_audio(beat, state)
        changes.extend(_audio_changes)
        notes.extend(_audio_notes)

        # ---- 变化判定：整块状态 diff（唯一可靠的做法）----
        # 只看 `changes` 会漏：某些写法改了状态却不产生一条文字描述
        # （例如 `show bg X` 换背景）。所以以 diff 为准，再用 changes 里
        # 记的 column 精确到「哪一列变了」。
        changed = _changed_columns(state, before, changes, note_scene)

        # ---- 消歧：本拍"上了台又立刻被清掉" ----
        _note_add_then_clear(changes, state)

        if beat.raw:
            notes.append('原始语句')
        if beat.code:
            notes.append('代码块')
        if beat.condition:
            notes.append(f"条件 {beat.condition}")

        out.append(_snapshot_row(beat, state, changes, changed, notes))

    return out


def _apply_stage_ops(beat, state, bi, changes) -> None:
    """
    把这一拍的 `scene` / `show` / `hide` 按 `op_tokens_full()` 的顺序落到
    `state` 上，变化记进 `changes`（就地改，不返回）。

    ---- 舞台：**逐条**按顺序应用（缺口 4，2026-10-07）----

    判据与渲染器**同一条**（`Beat.op_tokens_full`）。从前这里按
    **verb 分组**应用（先把所有 show 落上、再处理所有 hide），于是

        show A / hide A / show A

    的**当前舞台**是"台上没人"，而**产物**是"A 站着"：
    预览/卡片与产物说法相反。这一条是**视觉复核截图**当场看出来的
    （`tools/shot_stage_order.py`：修后那一格仍写"（台上没人）"）。
    """
    actors = state['actors']
    show_items = _split_show_items(beat.show)
    hide_items = _split_hide_items(beat.hide)
    seen = {'show': 0, 'hide': 0}

    def _pick(kind, items, idx):
        """这一条 token → 实际要应用的条目（`idx=None` = 还没用过的那些）。"""
        if idx is not None and 0 <= int(idx) < len(items):
            seen[kind] = max(seen[kind], int(idx) + 1)
            return [items[int(idx)]]
        out = items[seen[kind]:]
        seen[kind] = len(items)
        return out

    tokens = beat.op_tokens_full() or [(v, None) for v in _stage_order(beat)]
    for verb, idx in tokens:
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
                # `scene` 清的是**整个 master 层**（Ren'Py 官方措辞：
                # "removes all displayables from the master layer"）——
                # CG/UI 那些 `show cg …` 的图层和立绘一样会被清掉。
                # 2026-10-31 修：从前这里只清 actors，layers 原样留着，
                # 于是**产物里已经没了的 CG，预览/卡片还在画**。
                # 真机实测（sr_general，61 个 scene 拍）：**40 拍的图层
                # 其实被清掉、工具却继续显示** —— 正是本项目最贵的那类
                # "看到的 ≠ 生成的"。清了之后"换景会清掉什么"才说得准
                # （新「换景」入口的确认框直接读这里）。
                if state['layers']:
                    changes.append(
                        {'kind': 'clear', 'column': 'layer',
                         'text':
                         f'× 清空图层（{len(state["layers"])} 个）'})
                    state['layers'] = {}
                    state['layer_at'] = {}
                    srcs = state.get('layer_src')
                    if isinstance(srcs, dict):
                        srcs.clear()
                state['scene'] = image
                # `scene bg X at bgfit` 的变换是背景 displayable 的一部分，
                # 之后所有继承背景的拍都要带着它（引擎语义，见 docstring）。
                state['scene_at'] = str(
                    getattr(beat, 'scene_at', '') or '').strip()
                # 出处：这一拍的 `scene` 就是当前背景的来源（改尺寸时
                # 要回到这一行去写 at，不能在继承它的拍上硬塞）。
                state['scene_src'] = bi
                # `scene` 写明了背景，还清空了立绘 → 这两个元素重新已知
                state['unknown'].discard('scene')
                state['unknown'].discard('actors')
        elif verb == 'show':
            for item in _pick('show', show_items, idx):
                actors, _ch = _apply_show_verb(item, beat, actors, state,
                                               src_index=bi)
                changes.extend(_ch)
        elif verb == 'hide':
            for item in _pick('hide', hide_items, idx):
                actors, _ch = _apply_hide_verb(item, actors, state)
                changes.extend(_ch)
            state['actors'] = actors

    # ---- 补齐：令牌没点到的 show/hide 条目 ----
    # 与渲染器 `beat_render._emit_leftover_stage` **同一条规矩**：
    # `op_order` 只点到前几条时，渲染器会把剩下的追加在末尾；快照从前
    # 直接丢掉 → "看到的 ≠ 生成的"（卡片加第二个立绘，预览只显示一个，
    # 产物却两个）。2026-10-31 实测：真机两个项目 0 条触发，但合成用例
    # `show='ling a; ye b'` + `op_order='show#0'` 当场复现。
    for item in show_items[seen['show']:]:
        actors, _ch = _apply_show_verb(item, beat, actors, state,
                                       src_index=bi)
        changes.extend(_ch)
    for item in hide_items[seen['hide']:]:
        actors, _ch = _apply_hide_verb(item, actors, state)
        changes.extend(_ch)
        state['actors'] = actors


def _snapshot_row(beat, state, changes, changed, notes) -> dict:
    """一拍 → 一行快照（显示列 + `state` 全量状态）。"""
    return {
        'line_id': getattr(beat, 'line_id', '') or '',
        'seq': beat.seq,
        'label': beat.label,
        'speaker': beat.speaker,
        # 菜单拍的"无标题"占位符**不能漏进 Excel**：`\x00` 是控制字符，
        # openpyxl 会直接抛 `IllegalCharacterError`（整张表写不出去）。
        'text': menu_caption_text(beat.text),
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
            # 背景变换是**持续状态**（2026-10-30，⑦ P0）：
            # `scene bg X at bgfit` 之后每一拍都带着它，直到下一次 scene。
            # 只加内部键，**不改任何显示列**；老调用方缺键用默认空串。
            'scene_at': state['scene_at'],
            'actors': [[s[0], s[1]] for s in state['actors']],
            'layers': dict(state['layers']),
            # 图层 tag → `at`（`show cg cg01 at cg38402160` 的变换）；
            # show 时记、hide 时清。缺键默认 {}。
            'layer_at': dict(state['layer_at']),
            # 出处（2026-10-31）：背景 / 每个图层是**第几拍**写的。
            # 只给 UI 与"改尺寸"用，不参与任何显示列；跨章继承时是 -1。
            'scene_src': state['scene_src'],
            'layer_src': dict(state['layer_src']),
            'music': state['music'],
            'voice': state['voice'],
            'condition': beat.condition or '',
            # 只在"确实有不知道的元素"时出现 —— 默认路径（全新舞台）
            # 的输出与历史**逐字节一致**，不惊动既有 golden。
            **({'unknown': sorted(state['unknown'])}
               if state['unknown'] else {}),
        },
    }


#: 跨章状态要看的那几样（`stage_exit_state` 的输出键）。
#:
#: ⚠ `scene_at` / `layer_at` 必须在这里（2026-10-30，P1）：`_initial_state`
#:   早就支持这两个键，但出口状态不带它们 → 下一章第一拍就丢背景 fit / CG
#:   变换（`scene bg room at bgfit` / `show cg cg01 at cg38402160`）。
EXIT_STATE_KEYS = ('scene', 'scene_at', 'actors', 'layers', 'layer_at',
                   'music', 'voice')


def stage_exit_state(beats: list, initial_state: Optional[dict] = None) -> dict:
    """
    **跑完这些拍之后**的舞台状态 + 哪些元素仍然未知（跨章连续性用）。

    与 `stage_snapshots` 共用同一套推导（`initial_state` = 上一章的出口），
    所以"预览/快照说这样"与"跨章判据说这样"不可能各说一套。

    返回 `{scene, scene_at, actors, layers, layer_at, music, voice, unknown}`：
      · `unknown` 是**集合**（不是JSON）：这些元素的值还不知道 ——
        调用方必须把它们当"不判"，不能当空串；
      · `scene_at`（背景变换，str）与 `layer_at`（图层 tag → at 的**字典副本**）
        是"持续状态"，跨章必须一起继承；空也原样输出（结构稳定）；
      · 没有拍时原样返回入口状态（空列表 → 空转）。
    """
    beats = list(beats or ())
    rows = stage_snapshots(beats, initial_state=initial_state)
    st = rows[-1]['state'] if rows else _initial_state(initial_state)
    out = {}
    for key in EXIT_STATE_KEYS:
        if key == 'actors':
            out[key] = [[str(a), str(at)] for a, at in (st.get(key) or ())]
        elif key in ('layers', 'layer_at'):
            # 两个 dict 都要**副本**：调用方会改这份状态，不能把快照/入口
            # 那本字典直接递出去（`layer_at` 空也输出 `{}`）。
            out[key] = dict(st.get(key) or {})
        else:
            out[key] = str(st.get(key) or '')
    out['unknown'] = set(st.get('unknown') or ())
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


def snapshot_rows(beats: list, initial_state: Optional[dict] = None) -> list:
    """
    给 Excel 工作表用的行（字段名已是中文友好的展示形态）。

    `initial_state`（2026-10-31）：**接着上一章演**。传了它，第 0 拍之前就
    已经有背景/立绘/图层 —— 那些行的 `scene_src` / `layer_src` 是 `-1`
    （出处不在这本剧本里），"改尺寸"因此只能走"本章第一拍补一条"
    （`app.stage.MODE_CHAPTER_FIRST`）。不传时与历史行为逐字节一致。
    """
    return stage_snapshots(beats, initial_state=initial_state)


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


#: "这个通道还不知道"的括注。**必须带上"不是没有"**：卡片上这一格最常见的
#: 误读就是"没写 = 台上没有"，而"不知道"与"确定没有"是两件事。
#: （为什么不知道：入口状态是 `stage_exit_state` 一路传下来的，链根/分支汇聚/
#:  环里的章节从一开始就不知道 —— 详见 `pipeline.continuity` 的报告。）
_UNKNOWN_NOTE = '（不知道：上游没定下来，不是「没有」）'


def _inherited_line(state: dict) -> str:
    """这一拍的状态里**哪些是本文件没写过的**（跨章继承，`*_src < 0`）。"""
    parts = []
    try:
        scene = str(state.get('scene') or '').strip()
        # **缺键 ≠ 继承**：手工构造的帧（测试 / 探头）不带出处，不能因此
        # 给每一拍都贴一句"来自上一章"。
        if scene and 'scene_src' in state and int(state['scene_src']) < 0:
            parts.append('背景')
    except (TypeError, ValueError) as e:
        swallow('story.stage_snapshot.inherited_scene_src', e)
    for tag, src in (state.get('layer_src') or {}).items():
        try:
            if int(src) < 0:
                parts.append(f'图层 {tag}')
        except (TypeError, ValueError):
            continue
    return '、'.join(parts)


def stage_digest(beats: list, index: int,
                 initial_state: Optional[dict] = None,
                 entry_note: str = '', entry_why: str = '') -> str:
    """
    单拍的可读摘要（卡片上的「当前舞台」与旧版预览面板共用）。

    返回多行文本：变化 / 背景 / 立绘 / CG图层 / 音乐 / 音效 / 语音 / 条件 / 备注。

    `initial_state`（2026-10-31）：**接着上一章演**（同 `stage_snapshots`）。
    给了它，第 1 拍就可能已经有背景/立绘/CG —— 而它们的出处不在本文件里，
    所以第一行会写清「跨章继承：…（本章没写过，来自上一章）」，
    `entry_note` 是那句的来源说明（`ChapterEntry.describe()`）。

    `unknown` 里的通道一律写「不知道」，**绝不**退化成"（未设置）/（台上没人）"
    —— 那两句话是**确定的事**，混在一起就是骗人。
    """
    snaps = stage_snapshots(beats, initial_state=initial_state)
    if not snaps:
        return '（没有演出单元）'
    if index < 0 or index >= len(snaps):
        index = max(0, min(index, len(snaps) - 1))
    s = snaps[index]
    state = s['state']
    unknown = set(state.get('unknown') or ())
    lines = []
    inherited = _inherited_line(state)
    if inherited:
        lines.append(f'跨章继承：{inherited}（本章没写过，来自上一章）')
        if str(entry_note or '').strip():
            lines.append(f'  {str(entry_note).strip()}')
    lines.append(f"第 {s['seq']} 拍 · label {s['label'] or '(无)'}")
    if s['display_changes']:
        lines.append('本拍变化：')
        for ln in s['display_changes'].splitlines():
            lines.append(f'  {ln}')
    else:
        lines.append('本拍变化：（舞台无变化）')
    lines.append('背景：' + (_UNKNOWN_NOTE if 'scene' in unknown
                          else (s['scene'] or '（未设置）')))
    lines.append('立绘：' + (_UNKNOWN_NOTE if 'actors' in unknown
                          else (s['actors'] or '（台上没人）')))
    # CG / 图层也要写出来：`cg cg03` 这类整屏图不是"立绘"，
    # 少了这一行就会出现"舞台说明里看不出屏幕上有张 CG"。
    if 'layers' in unknown:
        lines.append(f'CG/图层：{_UNKNOWN_NOTE}')
    elif s.get('layers'):
        lines.append(f"CG/图层：{s['layers']}")
    lines.append('音乐：' + (_UNKNOWN_NOTE if 'music' in unknown
                          else (s['music'] or '（静音）')))
    if s['sound']:
        lines.append(f"音效：{s['sound']}")
    if 'voice' in unknown:
        lines.append(f'语音：{_UNKNOWN_NOTE}')
    elif s['voice']:
        lines.append(f"语音：{s['voice']}")
    if state.get('condition'):
        lines.append(f"条件：{state['condition']}")
    if s['note']:
        lines.append(f"备注：{s['note']}")
    # **没有入口状态**时把"为什么"也说清（还没分析 / 这个文件不是章节…）：
    # 这一格空着**不等于**"上一章什么都没留" —— 那正是要治的那个病。
    # 只在**第 1 拍**说（入口状态是章级事实，每拍重复一遍就成噪声；
    # 其余拍由 `跨章继承` 那一段和"不知道"括注说清）。
    # （单文件模式由调用方决定不传：跨章本来不存在，说了只是噪声。）
    why = str(entry_why or '').strip()
    if why and not inherited and index == 0:
        lines.append(f'跨章：上一章留了什么 —— 还不知道（{why}）')
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
