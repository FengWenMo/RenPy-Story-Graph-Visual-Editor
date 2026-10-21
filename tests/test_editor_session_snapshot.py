"""
EditorSession **行为快照**（P2-6 的前置网）
==========================================

为什么需要它
------------
`storygraph/app/editor.py` 的 `EditorSession` 有 **61 个公共方法 / 1618 行**，
而针对它的测试只有 `test_ui_qt.py::TestEditorSession` 的 9 条 —— 覆盖率
大约 15%。在这个状态下"拆 God object"（P2-6）等于**闭眼重构**：
改完没人能说清"行为到底变没变"。

这个文件补的是**快照网**，不是新的行为断言：

  · 载入夹具工程 → 依次调用一大批公共 API → 把每一步的返回（经过
    **确定性规整**）写进一份 golden JSON；
  · 再跑时与 golden 逐字节比对。
  · 任何**行为**变化都会让这份 JSON 出现 diff —— 包括那些没有专门
    断言的路径。重构时"这个 diff 是我想改的吗？"就成了一个能回答的问题。

怎么更新 golden
---------------
确认 diff 是**想要的改动**之后：

    $env:STORYGRAPH_UPDATE_SNAPSHOTS='1'; python -m pytest tests/test_editor_session_snapshot.py

（不要为了让测试变绿而无脑更新 —— 那等于把网剪掉。）

规整规则（为什么必须规整）
--------------------------
快照要能跨机器比对，所以把**与机器相关的东西**换成占位符：
绝对路径、临时目录名、时间戳、`id()`、`Path` 对象。
其余一律原样。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from _ui_project import build_project

#: golden 文件（与测试同目录，便于 code review 时直接看 diff）
GOLDEN = Path(__file__).resolve().parent / 'snapshots' / \
    'editor_session.json'

#: 低于这个长度就说明"快照其实什么都没抓到"，快照网形同虚设
MIN_CAPTURED = 40


# ---------------------------------------------------------------------------
#  确定性规整
# ---------------------------------------------------------------------------

#: 绝对路径。⚠ `(?<!>)` 是给 `<PROJ>/game/…` 这种"占位符后面跟路径"的写法
#: 留的口子：不挡的话 Linux 上 `_norm_path` 会把 `<PROJ>/game/...` 里
#: `/game/...` 再打码成 `<ABS>`，于是 golden（Windows 生成的
#: `<PROJ>\game\...`）永远对不上 —— 2026-10-22 CI 就是这么红的。
_ABS_PATH = re.compile(r'(?:[A-Za-z]:[\\/]|/)[^\s"\'<>|]*')

#: `<PROJ>` / `<TMP>` 后面紧跟的那段相对路径（要保护起来，别再打码）。
_AFTER_TAG = re.compile(r'(<(?:PROJ|TMP)>)(/[^\s"\'<>|]*)')


def _normalize(value, root: Path):
    """
    把任意返回值转成**可 JSON 化 + 跨机器稳定**的表示。

    规整掉：绝对路径、临时目录名、`id()`、集合顺序、`Path`。
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Path):
        return _norm_path(str(value), root)
    if isinstance(value, str):
        return _norm_path(value, root)
    if isinstance(value, bytes):
        return f'<bytes {len(value)}>'
    if isinstance(value, (set, frozenset)):
        return sorted(_normalize(v, root) for v in value)
    if isinstance(value, (list, tuple)):
        return [_normalize(v, root) for v in value]
    if isinstance(value, dict):
        return {str(k): _normalize(v, root) for k, v in sorted(
            value.items(), key=lambda kv: str(kv[0]))}
    # 其余对象：只留**类名 + 我们关心的字段**，不留内存地址
    for attr in ('text', 'kind', 'speaker', 'seq', 'label', 'condition'):
        if hasattr(value, attr):
            return {'__obj__': type(value).__name__,
                    **{a: _normalize(getattr(value, a), root)
                       for a in ('seq', 'label', 'kind', 'speaker', 'text',
                                 'condition') if hasattr(value, a)}}
    return f'<{type(value).__name__}>'


def _norm_path(text: str, root: Path) -> str:
    """
    把 root 下的绝对路径换成一个稳定占位符。

    **分隔符统一成 `/`**（2026-10-22）：golden 是 Windows 上生成的
    （`<PROJ>\\game\\script\\ch01.rpy`），Linux 跑出来是
    `<PROJ>/game/script/ch01.rpy` —— 同一个行为、两种写法，golden 必然对不上。
    把两边都归一成 `/`，这份 golden 才真的"跨机器稳定"（它本来就是这个目的）。
    """
    s = str(text).replace('\\', '/')
    for prefix, tag in ((str(root), '<PROJ>'),
                        (str(Path(root).parent), '<TMP>')):
        prefix = prefix.replace('\\', '/')
        s = s.replace(prefix, tag)
    # **先把"占位符后面的相对路径"护住**：Linux 上 `<PROJ>/game/...` 里那段
    # `/game/...` 是以 `/` 开头的，会被 `_ABS_PATH` 当成又一个绝对路径再打码，
    # 变成 `<PROJ><ABS>` —— 2026-10-22 CI 红的就是这个（Windows 上是
    # `\game\...`，不以 `/` 开头，所以从来没见过）。
    # 把 `<PROJ>/game/...` 里的 `/` 换成 `\` —— golden 本来就是 Windows 风格
    # （`<PROJ>\\game\\...`），换成 `\` 之后两边一模一样，而且 `_ABS_PATH`
    # 只认 `[A-Za-z]:\\` 或 `/` 开头，不会再打一次码。
    s = _AFTER_TAG.sub(lambda m: m.group(1) + m.group(2).replace('/', '\\'), s)
    # 剩下的绝对路径（例如系统字体、site-packages）统一打码
    s = _ABS_PATH.sub('<ABS>', s)
    return s


# ---------------------------------------------------------------------------
#  抓取
# ---------------------------------------------------------------------------

class _Capture:
    """按顺序记录 `(标签, 规整后的返回值)`。异常也记（那是行为的一部分）。"""

    def __init__(self, root: Path):
        self.root = root
        self.steps = []

    def call(self, label: str, fn, *a, **kw):
        try:
            value = fn(*a, **kw)
            self.steps.append([label, 'ok', _normalize(value, self.root)])
        except Exception as e:                     # noqa: BLE001 - 异常也是行为
            self.steps.append([label, type(e).__name__,
                               _norm_path(str(e), self.root)[:200]])
        return self.steps[-1][2]

    def note(self, label: str, value):
        self.steps.append([label, 'note', _normalize(value, self.root)])


def _new_graph():
    from storygraph.graph.models import Graph
    return Graph()


def _first_node_id(graph) -> str:
    """图里第一个节点 id（`node_variables` 需要一个真实 id）。"""
    nodes = getattr(graph, 'nodes', None) or {}
    if isinstance(nodes, dict):
        return next(iter(nodes), '')
    return ''


def _beat_state(session) -> list:
    """全部拍的**关键字段**（不含路径/时间）。"""
    out = []
    for b in getattr(session, 'beats', []) or []:
        out.append({
            'seq': b.seq, 'label': b.label, 'kind': b.kind,
            'speaker': b.speaker, 'text': b.text, 'condition': b.condition,
            'scene': b.scene, 'show': b.show, 'hide': b.hide,
            'at': b.at, 'with_': b.with_, 'music': b.music,
            'sound': b.sound, 'voice': b.voice, 'comment': b.comment,
            'raw': b.raw, 'code': b.code,
        })
    return out


def _capture_sequence(project: Path) -> dict:
    """
    跑一遍有代表性的会话流程，返回快照字典。

    **代表性**比覆盖率高更重要：挑的是"人会真的做的事" ——
    载入 → 翻页 → 读字段 → 读卡片 → 编辑 → 撤销 → 快照/还原 →
    插入/删除 → 导出 → 各种诊断查询。
    """
    from storygraph.app.editor import EditorSession

    root = Path(project)
    script = root / 'game' / 'script' / 'ch01.rpy'
    cap = _Capture(root)
    s = EditorSession(project_root=root)

    # —— 载入 ——
    cap.call('load', s.load, script)
    cap.note('beats', _beat_state(s))
    cap.call('health', s.health)

    # —— 导航 + 逐拍投影 ——
    n = len(getattr(s, 'beats', []) or [])
    for i in range(n):
        cap.call(f'fields[{i}]', s.fields, i)
    for i in range(n):
        cap.call(f'cards[{i}]', s.cards, i)
    cap.call('frames', s.frames)
    cap.call('stage_digest_text(0)', s.stage_digest_text, 0)
    cap.call('stage_digest_text(1)', s.stage_digest_text, 1)
    cap.call('speaking_card_index(0)', s.speaking_card_index, 0)
    cap.call('list_entry(0)', s.list_entry, 0)
    cap.call('list_entry(1)', s.list_entry, 1)

    # —— 剧情图：真的导入章节（图相关方法都要它）——
    graph = cap.call('_graph.Graph()', _new_graph)
    if graph is None or isinstance(graph, str):
        graph = _new_graph()
    cap.call('import_chapters', s.import_chapters, [script], graph)
    cap.call('graph_files', s.graph_files, graph)
    cap.call('missing_chapter_files', s.missing_chapter_files, graph)
    cap.call('analyze_graph', s.analyze_graph, graph)
    cap.call('analyze_graph_problems', s.analyze_graph_problems, graph)
    cap.call('node_variables', s.node_variables, graph, _first_node_id(graph))

    # —— 查询类 ——
    cap.call('characters', s.characters)
    cap.call('participants', s.participants)
    cap.call('asset_choices(image)', s.asset_choices, 'image')
    cap.call('asset_choices(music)', s.asset_choices, 'music')
    cap.call('transform_index', s.transform_index)
    cap.call('screen_size', s.screen_size)
    cap.call('default_output_path', s.default_output_path)
    cap.call('should_write_transforms', s.should_write_transforms)
    cap.call('asset_overview', s.asset_overview)

    # —— 编辑：改台词 + 立绘 ——
    cap.call('apply(0)', s.apply, 0,
             {'Speaker': 'e', 'Text': '快照改过的台词'})
    cap.call('fields[0]-after-apply', s.fields, 0)
    cap.call('can_undo', s.can_undo)
    cap.call('undo', s.undo)
    cap.call('fields[0]-after-undo', s.fields, 0)

    # —— 编辑：立绘卡片 ——
    cards = [{'image': 'ling happy', 'expression': '', 'position': 'leftish',
              'at': '', 'order_index': 0, 'extra_rows': []}]
    cap.call('apply(0,cards)', s.apply, 0,
             {'Speaker': 'e', 'Text': 'x'}, cards)
    cap.call('fields[0]-after-cards', s.fields, 0)

    # —— 快照 / 还原（`snapshot(idx)` 返回句柄，`restore(snap)` 吃它）——
    cap.call('apply-for-snapshot', s.apply, 0,
             {'Speaker': 'e', 'Text': '改一版再快照'})
    snap = s.snapshot(0)
    cap.note('snapshot(0)', snap)
    cap.call('restore(snapshot(0))', s.restore, snap)
    cap.call('fields[0]-after-restore', s.fields, 0)

    # —— 插入 / 删除 / 恢复 ——
    cap.call('insert_beat', s.insert_beat, 1)
    cap.note('beats-after-insert', _beat_state(s))
    cap.call('has_removed', s.has_removed)
    cap.call('delete_beat', s.delete_beat, 1)
    cap.call('has_removed-after-delete', s.has_removed)
    cap.call('restore_last_removed', s.restore_last_removed)
    cap.note('beats-after-restore', _beat_state(s))

    # —— 说话人 / 立绘移除 ——
    cap.call('set_speaker(0)', s.set_speaker, 0, 'm')
    cap.call('remove_sprite(0)', s.remove_sprite, 0, 0)

    # —— 编译 / 导出 ——
    cap.call('compile', s.compile)
    out_dir = root / 'snap_out'
    cap.call('export_ir(json)', s.export_ir, 'json', out_dir)
    cap.call('export_control_flow', s.export_control_flow, graph,
             out_dir / 'control_flow.json')

    # —— 校验类 ——
    cap.call('check_variable_syntax', s.check_variable_syntax, 'x = 1')
    cap.call('check_variable_syntax-bad', s.check_variable_syntax, 'x = =')

    # —— 布局（写到 snap_out，不碰工程源文件）——
    cap.call('save_layout', s.save_layout, graph, out_dir / 'layout.json')
    cap.call('load_layout', s.load_layout, out_dir / 'layout.json')

    cap.note('beats-final', _beat_state(s))
    return {'steps': cap.steps}


# ---------------------------------------------------------------------------
#  测试
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def snapshot_data(tmp_path_factory):
    project = build_project(tmp_path_factory.mktemp('snap') / 'proj')
    return _capture_sequence(project)


def test_snapshot_captured_enough(snapshot_data):
    """快照必须真的抓到东西，否则这张网是假的。"""
    assert len(snapshot_data['steps']) >= MIN_CAPTURED, (
        f'只抓到 {len(snapshot_data["steps"])} 步 —— 快照网形同虚设')


def test_behavior_matches_golden(snapshot_data):
    """
    EditorSession 的行为必须与 golden 一致。

    看到 diff 时**先读 diff 再决定**：
      · 是这次改动**有意**改的行为 → 更新 golden（见模块 docstring）；
      · 不是 → 你刚引入了一个回归，这正是这张网的价值。
    """
    if os.environ.get('STORYGRAPH_UPDATE_SNAPSHOTS'):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            json.dumps(snapshot_data, ensure_ascii=False, indent=1,
                       sort_keys=True) + '\n', encoding='utf-8')
        pytest.skip(f'已更新 golden：{GOLDEN}')

    assert GOLDEN.exists(), (
        f'缺少 golden：{GOLDEN}\n'
        '首次生成：设 STORYGRAPH_UPDATE_SNAPSHOTS=1 跑一次本文件。')
    want = json.loads(GOLDEN.read_text(encoding='utf-8'))
    got = json.loads(json.dumps(snapshot_data, ensure_ascii=False,
                                indent=1, sort_keys=True))

    if want != got:
        d = _first_differences(want, got, limit=12)
        pytest.fail(
            'EditorSession 行为与 golden 不一致（前 %d 处）：\n%s\n\n'
            '确认是有意改动后，设 STORYGRAPH_UPDATE_SNAPSHOTS=1 更新 golden。'
            % (len(d), '\n'.join(d)))


def _first_differences(want, got, limit=10) -> list:
    """把两份快照的差异压成人能读的几行（步级，不是字符级）。"""
    wa = want.get('steps', [])
    ga = got.get('steps', [])
    out = []
    for i in range(max(len(wa), len(ga))):
        a = wa[i] if i < len(wa) else None
        b = ga[i] if i < len(ga) else None
        if a == b:
            continue
        la = a[0] if a else '<missing>'
        lb = b[0] if b else '<missing>'
        out.append(f'  步 {i}: golden={la!r} 现在={lb!r}')
        if a and b and a[0] == b[0] and a[1] != b[1]:
            out.append(f'      状态: {a[1]} → {b[1]}')
        if a and b and a[0] == b[0]:
            sa = json.dumps(a[2], ensure_ascii=False, sort_keys=True)
            sb = json.dumps(b[2], ensure_ascii=False, sort_keys=True)
            if sa != sb:
                out.append(f'      golden: {sa[:160]}')
                out.append(f'      现在  : {sb[:160]}')
        if len(out) >= limit:
            break
    return out
