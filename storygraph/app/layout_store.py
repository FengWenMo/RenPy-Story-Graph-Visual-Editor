# -*- coding: utf-8 -*-
"""
**版式**的存取：只记"谁摆在哪"，不记"这一章演什么"。

## 为什么要独立一层（2026-10-23，W1a）

旧的 `storygraph_layout.json` 存的是**一整张图**：节点坐标、边，外加每个节点的
`operations` / `statements` 全文。于是：

1. 版式文件里塞着剧本内容 —— 两条真相，而且旧内容会盖住新扫描；
2. 加载布局是**整图替换** —— 实测：新增一章后再加载，那一章**从图上消失**；
3. 主键是内部 id（`"1"`、`"2"`…）—— 文件顺序一变 id 就串位（实测：`id1` 从 A 变 Z）。

这三件事合起来，就是用户说的"摆好又丢"，而且比丢更糟：**拿旧图盖新图**。

本模块把"版式"剥出来：

| 存什么 | 不存什么 |
|---|---|
| 每个节点的 `file::label` → `{x, y}` | 台词、语句、`operations` |
| 用户手画的草图边（`origin='manual'`）与编排线（`origin='control'`） | 从脚本派生的边（它们会重新生成） |

**扫描永远是权威**：`apply_placement` 只往已有的图上贴坐标，多的节点不删、
缺的节点不补 —— 匹配不上就交给自动排版。

## 主键为什么是 `相对路径::label`

| 候选 | 换机器 | 文件顺序变了 | 重名 |
|---|---|---|---|
| 内部 id | ✅ | ❌ 串位 | — |
| 纯 label 名 | ✅ | ✅ | ❌ |
| 绝对路径 | ❌ | ✅ | ✅ |
| **相对路径 + label** | ✅ | ✅ | ✅ |

相对路径的根是**项目根**（含 `game/` 的那一层），所以换机器、换盘符都不影响。
`::` 作分隔符：Windows 路径有 `C:`，用 `:` 会歧义。
"""
from __future__ import annotations

import json
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 当前版式格式版本。1 = 老的整图 JSON（只读兼容）。
PLACEMENT_VERSION = 2

#: 新版式文件的默认位置（相对项目根）。
PLACEMENT_REL_PATH = '.storygraph/layout.json'

#: 老版式文件的默认位置（相对项目根，仍在根目录）。
LEGACY_LAYOUT_NAME = 'storygraph_layout.json'


# ---------------------------------------------------------------------------
#  主键
# ---------------------------------------------------------------------------

def node_key(node, project_root=None) -> str:
    """
    节点的稳定身份：`<项目内相对路径>::<label>`。

    没有 `file_path` 的节点（图上手工建的）用 `::label` —— 它本来就不在
    任何文件里，硬编一个路径等于说谎。
    """
    label = str(getattr(node, 'label_name', '') or '')
    raw = str(getattr(node, 'file_path', '') or '').strip()
    if not raw:
        return f'::{label}'

    p = Path(raw)
    if project_root:
        try:
            p = p.resolve().relative_to(Path(project_root).resolve())
        except (ValueError, OSError) as e:
            # 项目外的文件：保留原路径（绝对就绝对）
            swallow('app.layout_store.node_key', e)
    return f'{p.as_posix()}::{label}'


def split_key(key: str) -> tuple:
    """`path::label` → `(path, label)`；没有 `::` 时当作纯 label。"""
    text = str(key or '')
    if '::' not in text:
        return '', text
    path, label = text.rsplit('::', 1)
    return path, label


# ---------------------------------------------------------------------------
#  写
# ---------------------------------------------------------------------------

def placement_dict(graph, project_root=None) -> dict:
    """
    图 → **瘦版式**（只有坐标 + 手工草图边）。

    刻意不写 `operations` / `statements`：那是内容，会过期，而且让文件大几十倍
    （真机 `storygraph_layout.json` 里同一句 `renpy.transition` 重复了几十遍）。
    """
    nodes = {}
    for node in (getattr(graph, 'nodes', {}) or {}).values():
        nodes[node_key(node, project_root)] = {
            'x': round(float(getattr(node, 'x', 0) or 0), 2),
            'y': round(float(getattr(node, 'y', 0) or 0), 2),
        }

    edges = []
    by_id = {nid: n for nid, n in (getattr(graph, 'nodes', {}) or {}).items()}
    for edge in (getattr(graph, 'edges', {}) or {}).values():
        # 只存**图上独有的边**：`source` 边从脚本派生，存了会被下次重建覆盖；
        # `manual`（草图）和 `control`（P2 编排层路由）都必须存 —— 后者
        # 不在章节源文件里，不存的话关掉再开就从图上消失了。
        origin = str(getattr(edge, 'origin', 'source') or 'source')
        if origin not in ('manual', 'control'):
            continue
        src = by_id.get(edge.from_id)
        dst = by_id.get(edge.to_id)
        if src is None or dst is None:
            continue
        edges.append({
            'from': node_key(src, project_root),
            'to': node_key(dst, project_root),
            'type': str(getattr(edge, 'type', 'jump') or 'jump'),
            'condition': str(getattr(edge, 'condition', '') or ''),
            'option_text': str(getattr(edge, 'option_text', '') or ''),
            'from_clause': str(getattr(edge, 'from_clause', '') or ''),
            'origin': origin,
            # 落点（P0，2026-10-24）：默认章末；P1 会写 `第 N 拍`；
            # P2 的编排线是 `control_end`。
            'position': str(getattr(edge, 'position', 'chapter_end')
                            or 'chapter_end'),
            'position_line_id': str(getattr(edge, 'position_line_id', '') or ''),
        })

    return {'version': PLACEMENT_VERSION, 'nodes': nodes, 'edges': edges}


def save_placement(graph, target, project_root=None) -> dict:
    """把瘦版式写到 `target`。返回 `{ok, path, nodes, edges, error}`。"""
    data = placement_dict(graph, project_root)
    path = Path(target)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding='utf-8')
    except OSError as exc:
        return {'ok': False, 'error': str(exc), 'path': str(path),
                'nodes': 0, 'edges': 0}
    return {'ok': True, 'error': '', 'path': str(path),
            'nodes': len(data['nodes']), 'edges': len(data['edges'])}


# ---------------------------------------------------------------------------
#  读（含老格式迁移）
# ---------------------------------------------------------------------------

def _norm_path(raw: str, project_root=None) -> str:
    """把任意写法（绝对 / 相对 / 反斜杠）统一成**项目内相对路径**。"""
    text = str(raw or '').strip()
    if not text:
        return ''
    p = Path(text)
    if project_root:
        try:
            p = p.resolve().relative_to(Path(project_root).resolve())
        except (ValueError, OSError) as e:
            # 项目外的文件：保留原样（它本来就不在项目里）
            swallow('app.layout_store.norm_path', e)
    return p.as_posix()


def norm_key(key: str, project_root=None) -> str:
    """
    规范化一个版式主键。

    为什么要在**读的时候**做（2026-10-23 真机抓到的）：老文件的 `file_path`
    存的是**绝对路径**（`D:/…/chapter1_2.rpy`），而扫描出来的节点是相对路径。
    不在读的时候统一，两份 key 永远对不上 —— 实测匹配率 **0/1**，
    表现出来就是"版式里有、图上一个也对不上"，等于版式白存。
    """
    path, label = split_key(key)
    if not path:
        return f'::{label}'
    return f'{_norm_path(path, project_root)}::{label}'


def _key_from_legacy_node(nd: dict) -> str:
    """老格式的节点条目 → 稳定键（`file_path::label`）。"""
    label = str(nd.get('label') or '')
    path = str(nd.get('file_path') or '').strip()
    return f'{Path(path).as_posix()}::{label}' if path else f'::{label}'


def read_placement(source, project_root=None) -> dict:
    """
    读一份版式（新旧都行），统一成 `{version, nodes, edges, migrated}`。

    - 新格式（`version >= 2`）读出来立刻规范化主键；
    - 老格式（整图 JSON）：**只取坐标和手工边**，`operations` / `statements`
      一概忽略 —— 那是内容，过期了会盖住新扫描；
    - `project_root` 给了就把绝对路径相对化（老文件全是绝对路径，不给就匹配不上）；
    - 读不了就返回 `{ok: False, error}`，不抛。
    """
    try:
        data = json.loads(Path(source).read_text(encoding='utf-8'))
    except Exception as exc:                      # noqa: BLE001
        return {'ok': False, 'error': f'读不了这个 JSON：{exc}',
                'version': 0, 'nodes': {}, 'edges': [], 'migrated': False}
    if not isinstance(data, dict) or 'nodes' not in data:
        return {'ok': False, 'error': '这个文件不是版式文件（缺 nodes）',
                'version': 0, 'nodes': {}, 'edges': [], 'migrated': False}
    # ⚠ `nodes` 不是对象就不是版式（2026-11-05：项目里别的 JSON 也可能有个
    #   `nodes` 键、值是列表 —— 直接 `.items()` 会 AttributeError，而调用方
    #   （自动认领）在"导入章节"这条路上，崩了就是整张图导不进来。
    if not isinstance(data.get('nodes'), dict):
        return {'ok': False, 'error': '这个文件不是版式文件（nodes 不是对象）',
                'version': 0, 'nodes': {}, 'edges': [], 'migrated': False}

    try:
        version = int(data.get('version') or 1)
    except (TypeError, ValueError):
        version = 1

    if version >= 2:
        # ⚠ 值不是对象的一律丢掉：`apply_placement` 里会 `pos.get('x')`，
        #   拿到列表就是 AttributeError —— 而这条路在"导入章节"上，崩了
        #   等于整张图导不进来（2026-11-05 自动认领把它变成了必经之路）。
        nodes = {norm_key(k, project_root): v
                 for k, v in (data.get('nodes') or {}).items()
                 if isinstance(v, dict)}
        edges = []
        for ed in (data.get('edges') or []):
            if not isinstance(ed, dict):
                continue
            item = dict(ed)
            item['from'] = norm_key(str(item.get('from') or ''), project_root)
            item['to'] = norm_key(str(item.get('to') or ''), project_root)
            edges.append(item)
        return {'ok': True, 'error': '', 'version': version,
                'nodes': nodes, 'edges': edges, 'migrated': False}

    # ---- 老格式：整图 JSON → 只留版式 ----
    nodes = {}
    for _nid, nd in (data.get('nodes') or {}).items():
        if not isinstance(nd, dict):
            continue
        nodes[norm_key(_key_from_legacy_node(nd), project_root)] = {
            'x': float(nd.get('x') or 0),
            'y': float(nd.get('y') or 0),
        }

    by_id = {str(nid): nd for nid, nd in (data.get('nodes') or {}).items()
             if isinstance(nd, dict)}
    edges = []
    for _eid, ed in (data.get('edges') or {}).items():
        if not isinstance(ed, dict):
            continue
        # 老文件没有 origin 的（2026-10-05 之前）全是解析出来的 → 不存；
        # 手工草图与 P2 编排线要留。
        origin = str(ed.get('origin') or 'source')
        if origin not in ('manual', 'control'):
            continue
        src = by_id.get(str(ed.get('from')))
        dst = by_id.get(str(ed.get('to')))
        if src is None or dst is None:
            continue
        edges.append({
            'from': norm_key(_key_from_legacy_node(src), project_root),
            'to': norm_key(_key_from_legacy_node(dst), project_root),
            'type': str(ed.get('type') or 'jump'),
            'condition': str(ed.get('condition') or ''),
            'option_text': str(ed.get('option_text') or ''),
            'from_clause': str(ed.get('from_clause') or ''),
            'origin': origin,
            # 老格式没有落点 → 默认章末（草图本来就没有拍）。
            'position': str(ed.get('position') or 'chapter_end'),
            'position_line_id': str(ed.get('position_line_id') or ''),
        })

    return {'ok': True, 'error': '', 'version': PLACEMENT_VERSION,
            'nodes': nodes, 'edges': edges, 'migrated': True}


# ---------------------------------------------------------------------------
#  贴到图上
# ---------------------------------------------------------------------------

def apply_placement(graph, data, project_root=None) -> dict:
    """
    把版式**贴到已经扫描好的图**上（不替换、不增删节点）。

    返回 `{ok, matched, missing, total, edges_added}`：
      · `matched` —— 成功贴上的节点数；
      · `missing` —— 版式里有、这张图上没有的键（多半是章节被删了/改名了）；
      · `total`   —— 这张图上一共有几个节点。

    **匹配不上不报错**：版式是审美，缺一个节点不该让整张图用不了。
    没匹配上的节点保留扫描时的坐标（通常是自动排版给的）。
    """
    if not data or not data.get('ok', True):
        return {'ok': False, 'matched': 0, 'missing': [], 'total': 0,
                'edges_added': 0,
                'error': (data or {}).get('error', '版式读不出来')}

    nodes = list((getattr(graph, 'nodes', {}) or {}).values())
    by_key = {node_key(n, project_root): n for n in nodes}

    matched = 0
    missing = []
    for key, pos in (data.get('nodes') or {}).items():
        node = by_key.get(str(key))
        if node is None:
            missing.append(str(key))
            continue
        try:
            node.x = float(pos.get('x', node.x))
            node.y = float(pos.get('y', node.y))
        except (TypeError, ValueError):
            missing.append(str(key))
            continue
        matched += 1

    edges_added = 0
    seen = {(e.from_id, e.to_id, str(getattr(e, 'type', '')))
            for e in (getattr(graph, 'edges', {}) or {}).values()}
    for ed in (data.get('edges') or []):
        if not isinstance(ed, dict):
            continue
        src = by_key.get(str(ed.get('from')))
        dst = by_key.get(str(ed.get('to')))
        if src is None or dst is None:
            continue
        sig = (src.id, dst.id, str(ed.get('type') or 'jump'))
        if sig in seen:
            continue
        origin = str(ed.get('origin') or 'manual')
        default_pos = ('control_end' if origin == 'control'
                       else 'chapter_end')
        graph.add_edge(src.id, dst.id,
                       etype=str(ed.get('type') or 'jump'),
                       condition=str(ed.get('condition') or ''),
                       option_text=str(ed.get('option_text') or ''),
                       from_clause=str(ed.get('from_clause') or ''),
                       origin=origin,
                       position=str(ed.get('position') or default_pos),
                       position_line_id=str(ed.get('position_line_id') or ''))
        seen.add(sig)
        edges_added += 1

    return {'ok': True, 'error': '', 'matched': matched,
            'missing': missing, 'total': len(nodes),
            'edges_added': edges_added}


# ---------------------------------------------------------------------------
#  默认路径
# ---------------------------------------------------------------------------

def default_paths(project_root) -> tuple:
    """
    `(新版式路径, 老版式路径)`。

    读的时候先看新路径，没有再看老的 —— 用户手里已有的版式不能白丢。
    """
    if not project_root:
        return None, None
    root = Path(str(project_root))
    return root / PLACEMENT_REL_PATH, root / LEGACY_LAYOUT_NAME


def find_existing(project_root):
    """找一份可读的版式：新版优先，其次老版。都没有返回 `None`。"""
    new, old = default_paths(project_root)
    for path in (new, old):
        try:
            if path is not None and path.is_file():
                return path
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
#  自动认领：项目里**别处**存的版式（2026-11-05）
# ---------------------------------------------------------------------------
#
# 用户真机报的：
#
# > 「为什么我明明导入了项目，却没有成功导入布局……？你能自动识别到对应布局
# >   的 json 文件，并自动导入吗？（不能就算，没有也静默，不用报错）」
#
# 事实：他的 `sr_general` 里有两份版式 ——
#   · `.storygraph/layout.json`（10-26 22:26，工具自动存的**旧**排列）；
#   · `game/chapter/目前保存的布局(自动生成的，不用看）.json`
#     （10-28 00:29，他**手动**「保存布局」存下的**新**排列，老格式整图）。
# 打开项目只认前一个 → 看到的是两天前摆的样子，而且那份老文件里的 **50 条
# 编排线**（`origin='control'`）也跟着一起丢了。
#
# 所以这里加一条"认领"：**项目里任何一份像版式的 JSON 都算候选**，
# 按"能对上多少块 → 文件多新"挑一份。挑不到就什么都不做（静默）。

#: 扫候选时跳过的目录（**性能护栏，不是对项目形态的断言**）：
#: 资源 / 引擎自带 / 缓存里不会有版式，进去只会白读几千个文件。
SCAN_SKIP_DIRS = frozenset({
    'images', 'audio', 'sounds', 'voices', 'music', 'gui', 'fonts', 'tl',
    'cache', 'saves', 'libs', 'build', 'node_modules', '__pycache__',
    'cc_preview', 'minigamepieces', 'renpy', 'common',
})

#: 扫候选的上限（文件数 / 单文件字节 / 目录深度）。超了就只是一份不认领。
SCAN_MAX_FILES = 200
SCAN_MAX_BYTES = 8 * 1024 * 1024
SCAN_MAX_DEPTH = 4

#: 先读文件头这么多字节找 `"nodes"`（大文件不整个读进来）
_HEAD_BYTES = 65536

#: 判"像版式"时最多往后读这么多（真机老格式版式 134 KB；再大就不是版式了）
_SCAN_READ_BYTES = 2 * 1024 * 1024

#: 版式文件的**结构特征**：一个 `"nodes"` 键，后面跟的是**对象**（不是列表）。
_NODES_KEY = b'"nodes"'


def _nodes_object_in(buf: bytes) -> bool:
    """
    `buf` 里有没有 `"nodes" : {`（**只看形状，不看文件名**）。

    为什么要求后面是 `{`（2026-11-05 用户："别通过名字认 json，看结构"）：
    项目里别的 JSON 完全可能也有一个叫 `nodes` 的键（列表 / 数字），
    那种文件按内容就不是版式 —— 靠文件名猜才会把它们混进来。
    """
    start = 0
    while True:
        i = buf.find(_NODES_KEY, start)
        if i < 0:
            return False
        j = i + len(_NODES_KEY)
        while j < len(buf) and buf[j] in b' \t\r\n':
            j += 1
        if buf[j:j + 1] == b':':
            j += 1
            while j < len(buf) and buf[j] in b' \t\r\n':
                j += 1
            if buf[j:j + 1] == b'{':
                return True
        start = i + 1


def _looks_like_layout(path) -> bool:
    """
    这个文件**可能**是版式吗。

    判据**只有内容形状**：JSON 里有 `"nodes": {…}` 这个对象。
    **不看文件名、不看所在目录、不看 `version` 字段** —— 真机上用户
    手动存的版式叫「目前保存的布局(自动生成的，不用看）.json」，名字毫无规律；
    而叫 `layout.json` 的文件也可能是别的项目留下的预设（`sr_general` 的
    `.storygraph/layout.json` 就和随包预设字节相同）。

    流式读（64 KB 一块），因为老格式版式把 `operations` 放在前面，
    `nodes` 可能在 100 KB 之后 —— 只看头 8 KB 会**漏掉**真正的版式。
    读到 `_SCAN_READ_BYTES` 还没找到就当它不是（比整个读 8 MB 便宜）。
    能不能真读出来由 `read_placement` 决定。
    """
    try:
        info = path.stat()
    except OSError:
        return False
    if not info.st_size or info.st_size > SCAN_MAX_BYTES:
        return False
    limit = min(int(info.st_size), _SCAN_READ_BYTES)
    carry = b''
    read = 0
    try:
        with open(path, 'rb') as f:
            while read < limit:
                chunk = f.read(min(_HEAD_BYTES, limit - read))
                if not chunk:
                    break
                read += len(chunk)
                buf = carry + chunk
                if _nodes_object_in(buf):
                    return True
                # 只留尾巴：跨块的关键字/冒号可能被切断
                carry = buf[-64:]
    except OSError:
        return False
    return False


def scan_candidates(project_root, *, limit: int = SCAN_MAX_FILES) -> list:
    """
    项目里所有**长得像版式**的 JSON（含标准位置），按修改时间从新到旧。

    判定只到"文件头像版式"这一层；能不能真读出来由 `read_placement` 决定
    （坏文件不在这里报错，挑的时候自然跳过）。
    """
    root = Path(str(project_root or ''))
    if not root.is_dir():
        return []
    found, seen = [], 0
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for path in entries:
            try:
                is_dir = path.is_dir()
            except OSError:
                continue
            if is_dir:
                name = path.name
                if depth >= SCAN_MAX_DEPTH or name in SCAN_SKIP_DIRS:
                    continue
                # 隐藏目录一律不进（`.git` / `.venv` …），只留 `.storygraph`
                if name.startswith('.') and name != '.storygraph':
                    continue
                stack.append((path, depth + 1))
                continue
            if path.suffix.lower() != '.json':
                continue
            seen += 1
            if seen > limit:
                return _newest_first(found)
            if _looks_like_layout(path):
                found.append(path)
    return _newest_first(found)


def _newest_first(paths) -> list:
    """按修改时间从新到旧（读不到时间的排最后，不丢）。"""
    def stamp(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    return sorted(paths, key=lambda p: (-stamp(p), str(p)))


def best_for(graph, project_root) -> dict:
    """
    给**当前这张图**挑一份最合适的版式 → `{ok, path, data, matched, total,
    candidates, reason}`。

    挑法（两个判据，缺一不可）：

      1. **能对上多少块**（`file::label` 交集）—— 对不上的版式再新也没用；
      2. 一样多时取**文件更新**的那份 —— "我最后存的那一版"。

    为什么不是"只看标准位置"：真机上用户手动存的版式落在 `game/chapter/`
    里（见本节开头），只认标准位置就会拿两天前的排列盖掉今天的。

    为什么不是"只看最新"：项目里可能有上一章/别的项目的残留版式，它很新，
    但一块都对不上 —— 那种必须跳过，不然会把图打散。
    """
    empty = {'ok': False, 'path': None, 'data': None, 'matched': 0,
             'total': 0, 'candidates': 0, 'reason': '没有可用的版式文件'}
    nodes = list((getattr(graph, 'nodes', {}) or {}).values())
    if not nodes:
        return {**empty, 'reason': '剧情图是空的，没有可贴的对象'}
    root = Path(str(project_root or ''))
    keys = {node_key(n, root) for n in nodes}
    best = None
    cands = scan_candidates(root)
    for path in cands:
        # ⚠ 不在这里包 `except`：`read_placement` 自己的判据已经够严（坏 JSON /
        #   缺 nodes / nodes 不是对象 都返回 `ok=False`），再包一层宽捕获只会
        #   让"技术债棘轮"（`broad_excepts`）多记一笔，却不多挡住什么。
        data = read_placement(path, root)
        if not data.get('ok'):
            continue
        matched = len(keys & set(data.get('nodes') or {}))
        if not matched:
            continue
        row = {'ok': True, 'path': path, 'data': data, 'matched': matched,
               'total': len(nodes), 'candidates': len(cands), 'reason': ''}
        if best is None:
            best = row
            continue
        # 先比"贴上多少块"，一样多再比"谁更新"（`scan_candidates` 已按新到旧，
        # 所以 `>` 就够了：平手保留先出现的那份 = 更新的那份）。
        if matched > best['matched']:
            best = row
    return best or {**empty, 'candidates': len(cands)}


__all__ = [
    'PLACEMENT_VERSION', 'PLACEMENT_REL_PATH', 'LEGACY_LAYOUT_NAME',
    'SCAN_MAX_BYTES', 'SCAN_MAX_DEPTH', 'SCAN_MAX_FILES', 'SCAN_SKIP_DIRS',
    'node_key', 'split_key', 'norm_key', 'placement_dict', 'save_placement',
    'read_placement', 'apply_placement', 'default_paths', 'find_existing',
    'scan_candidates', 'best_for',
]
