"""
storygraph.app.editor
=====================
**编辑器会话门面** —— 前端唯一需要认识的对象。

它是什么（深模块）
------------------
一个"打开了一章、正在编辑哪一拍、改完怎么落盘"的会话。接口很小：

    s = EditorSession(registry_provider=..., project_root=...)
    s.load('game/chapter/ch1.rpy')
    while s.has(i):
        fields = s.fields(i)          # → 直接塞进表单
        cards  = s.cards(i)           # → 直接塞进立绘网格
        ...用户改...
        s.apply(i, fields, cards)     # 表单 → Beat

    名字      一次导出          # 编译 + 增量补丁 + 安全门禁，**一个调用**
    体检      项目体检

实现里装着的全部业务不变量（前端再也碰不到，也就不可能各写一套）：

  · **`show` 是投影，不是输入**：表情/位置住在卡片 chip 上，
    写回时按出场顺序拼成 `show`（多属性不压平、extras 不丢、
    位置只有统一才整拍）；
  · **Stage 每次重算**（它是派生视图，不是用户输入）；
  · **空网格不清空立绘**（防误删）；
  · **高级区只在展开时才写回**（收起 ≠ 清空）；
  · 翻页是**纯导航**，不碰别的拍；
  · 落盘一律过生成门禁（`generation_guard`）。

为什么值得存在
--------------
拆分前，上面每一条都在 tkinter 的 `BeatEditorPanel` 里，而 Qt 版
`main_window.py` 只能**照抄一遍**并伸手去摸 `self._panel._speaker`
这种私有属性 —— 两套前端、两套语义，改一处必漏一处。

**禁止**在本模块里 import 任何 UI 框架。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from storygraph.app import beats as B
from storygraph.app.stage import build_frames
from storygraph.story.beat_build import (beats_from_parse_result,
                                         hydrate_from_dsl, stage_only_ops)
from storygraph.story.beat_model import Beat, compose_stage_extra
from storygraph.story.beat_render import make_symbols
from storygraph.story.generation_guard import (evaluate_generation,
                                               labels_in_danger,
                                               write_generation)

#: 主区字段（每一拍真正常改的）—— 与两套前端共用同一份划分
PRIMARY_FIELDS = ['Speaker', 'Text', 'Scene', 'BGM', 'Voice', 'Comment']
#: 高级区字段（默认折叠）
ADVANCED_FIELDS = ['SFX', 'CodeBlock', 'Label', 'Kind', 'Notes', 'Stage']


#: 撤销快照要装的字段。
#:
#: 选取原则：**所有会进渲染 / 进 Excel 的标量**（少一个，撤销就会留下
#: 半新半旧的状态），加上 `ordered`（在 `snapshot()` 里单独深拷贝）。
#: 刻意不含 `src_first` / `src_last`（增量导出的源文件行号）与
#: `_from_script`：撤销一次字段编辑不该改动"这一拍在源文件的哪几行"。
_SNAPSHOT_FIELDS = (
    'seq', 'label', 'line_id', 'content_hash', 'condition', 'comment',
    'kind', 'speaker', 'text', 'scene', 'stage', 'bgm', 'sfx',
    'show', 'hide', 'at', 'scene_at', 'with_', 'transform', 'stage_order',
    'op_order', 'raw', 'code', 'music', 'music_params', 'music_action',
    'sound', 'sound_params', 'sound_action', 'voice', 'notes',
)


@dataclass
class BeatSnapshot:
    """
    一拍在某个时刻的**内容快照**（撤销的载体）。

    为什么不用 `copy.deepcopy(beat)`：`deepcopy` 会把 `ordered`、
    `_hydrated_stage` 这些派生状态一起抄走，撤销之后可能带着一份
    "已经吸过"的标记却对应着新的 stage —— 于是 hydrate 跳过、立绘丢失。
    所以只抄**明确列出的字段**，其余由 `restore` 之后的重算负责。
    """
    idx: int
    data: dict = field(default_factory=dict)

    def get(self, name, default=''):
        return self.data.get(name, default)

    def fingerprint(self) -> str:
        """
        快照内容的指纹（用来判"这次保存到底改了没有"）。

        **不能拿 `Beat.content_hash` 当判据**：那是**持久化**字段
        （进 Excel 的「内容指纹」列、用于协作冲突检测），只在
        `_assign_identity` 时重算 —— 编辑之后它还是旧值，于是
        "改了内容却判成没变"（正好是这个功能的判据要避免的事）。

        这里按快照里**当前的**值现算一个短哈希：字段集固定，
        所以两份快照可比。
        """
        import hashlib
        blob = '\x1f'.join(f'{k}={self.data.get(k)!r}'
                           for k in sorted(self.data))
        return hashlib.md5(blob.encode('utf-8', 'replace')).hexdigest()[:12]


#: "演出语句"的正则：**有这些就说明这个文件里有正文**（台词/舞台/音频/语音）。
#: 刻意**不包含** menu —— 在用户的分工里，选择与条件属于控制流，不属于章节正文。
_BODY_LINE_RE = re.compile(
    r'^[ \t]*(?:'
    r'(?:[A-Za-z_]\w*[ \t]+)*"|'          # "台词" / jc "台词"
    r"(?:[A-Za-z_]\w*[ \t]+)*'|"          # 单引号写法
    r'(?:scene|show|hide|play|stop|queue|voice|window|extend|nvl)\b'
    r')', re.MULTILINE)


def files_look_like_control_flow(paths) -> bool:
    """
    这批 .rpy 是不是"**控制流脚本**"？（判据只看**内容**，不看文件名/标签名）

    用户 2026-10-05 的要求：

    > 要是有人非要以 A_control 这种标签起名怎么办？……不得是变成保留字，
    > 因为你不能阻拦用户起什么名。

    判据：文件里**一条演出语句都没有**（没有台词、没有 scene/show/hide、
    没有 play/voice）→ 控制流脚本。menu / if / call / jump / $ /
    
eturn 都算控制流，不算正文 —— 与用户的分工一致（选择分支与条件分支
    住在控制流里）。
    """
    seen = False
    for fp in (paths or ()):
        try:
            text = Path(fp).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        seen = True
        if _BODY_LINE_RE.search(text):
            return False
    return seen


def graph_from_layout_dict(data) -> 'object':
    """
    布局 JSON（dict）→ 新的 `Graph`。**只做数据转换，不碰文件**。

    为什么单独一个函数：两条路都要用它 ——
      · `EditorSession.load_layout`（从文件加载）；
      · Qt 窗口的 `_apply_layout_data`（旧版 `LoadLayoutCommand` 回调，
        图级撤销/重做要用）。
    两份转换逻辑一漂移，"撤销后加载的图"和"直接加载的图"就会不一样。

    未知字段忽略（旧文件里有 `is_current` 之类现在不用的键）；
    `next_ids` 缺失时按现有最大 id 兜底（手改过的文件也不至于崩）。
    """
    from storygraph.graph.models import Edge, Graph, Node
    graph = Graph()
    for nid_str, nd in ((data or {}).get('nodes') or {}).items():
        nid = int(nid_str)
        nd = nd or {}
        node = Node(nid, str(nd.get('label') or ''),
                    float(nd.get('x') or 0), float(nd.get('y') or 0))
        node.is_current = bool(nd.get('is_current', False))
        # 迁移（2026-10-05）：新文件带 `statements`（结构化，含出处/条件），
        # 老文件只有 `operations`（裸字符串）—— 两种都能读，
        # 读不出结构就退回字符串路径（老的 `.json` 一张都不会作废）。
        stmts = nd.get('statements')
        if isinstance(stmts, list) and stmts:
            from storygraph.core.statement import Statement
            try:
                node.statements = [Statement.from_dict(s) for s in stmts
                                   if isinstance(s, dict)]
            except Exception as e:            # noqa: BLE001 - 坏文件别让人打不开
                swallow('editor.graph_from_dict.statements', e)
                node.set_operations(list(nd.get('operations') or []))
        else:
            node.set_operations(list(nd.get('operations') or []))
        node.file_path = nd.get('file_path')
        node.line_number = nd.get('line_number')
        graph.nodes[nid] = node
    for eid_str, ed in ((data or {}).get('edges') or {}).items():
        eid = int(eid_str)
        ed = ed or {}
        graph.edges[eid] = Edge(
            eid, ed.get('from'), ed.get('to'),
            str(ed.get('type') or 'jump'),
            str(ed.get('condition') or ''),
            str(ed.get('option_text') or ''),
            str(ed.get('from_clause') or ''))
    nx = (data or {}).get('next_ids') or {}
    graph.next_node_id = int(nx.get('node')
                             or (max(graph.nodes) + 1 if graph.nodes else 1))
    graph.next_edge_id = int(nx.get('edge')
                             or (max(graph.edges) + 1 if graph.edges else 1))
    return graph


class EditorSession:
    """
    一章的编辑会话。**前端只碰这个类**（不碰 Beat 内部字段名）。

    registry_provider: 返回资产 registry 的可调用对象（可为 None）。
        故意收一个函数而不是 engine 对象 —— 会话不需要知道"引擎"是什么。
    """

    def __init__(self, registry_provider: Optional[Callable] = None,
                 project_root=None):
        self._registry_provider = registry_provider
        self.project_root = (Path(project_root)
                             if project_root is not None else None)
        self.script_path: Optional[Path] = None
        self.beats: List[Beat] = []
        self.symbols = None
        #: 「打开项目」读进来的资源表（xlsx）。**只服务资源侧**
        #: （下拉候选 / 缩略图 / 参与角色 / 资产选择器），与剧情图无关。
        self._loaded_registry = None
        #: 逐拍的"上一次保存前"快照（撤销用）。**单级**：每个 Beat 一份。
        #: 只装拍号，不装 Beat 对象 —— 拍的增删由载入负责，
        #: 这里只记内容，避免"撤销把别人新插入的行顶掉"。
        self._last_saved: dict = {}
        #: 前端最近一次载入的拍号（撤销不带参数时的默认目标）。
        self._last_idx: Optional[int] = None
        #: 上一次删掉的卡片 `(原拍号, Beat)` —— 单级结构撤销用（见 restore_last_removed）
        self._last_removed = None
        #: 角色↔剧本 linker（按需构建，见 `asset_linker()`）
        self._linker = None

    # ------------------------------------------------------------------
    #  资产
    # ------------------------------------------------------------------

    def registry(self):
        """当前资产 registry（拿不到就 None —— 界面降级但不崩）。"""
        if self._loaded_registry is not None:
            return self._loaded_registry
        if self._registry_provider is None:
            return None
        try:
            return self._registry_provider()
        except Exception:                    # noqa: BLE001
            return None

    def load_assets(self) -> dict:
        """
        「打开项目」：**只**重读资源表（xlsx）—— 图片 / 音乐 / 音效 /
        语音 / 变换 / 角色。

        为什么单独一个方法、而且**不收 graph 参数**（用户 2026-10-03）：

        > 所谓的导入项目只是为了表格的 xlsx 的资源管理系统！
        > 不是把整个项目都排在剧情图上面！！！

        资源管理（立绘/音乐候选、缩略图、参与角色）需要整个项目；
        剧情图**只放用户自己导入的章节**。两者以前被混在一句话里
        （"打开项目 → 建出结构图"），于是 40 个脚本一次性灌进图里变成
        51 个块，"看都要看瞎了"。这里从签名上就断开这种可能：
        这个方法**拿不到图**，想灌也灌不进去。
        """
        if not self.project_root:
            return {'ok': False, 'error': '还没有选目录', 'counts': {}}
        from storygraph.pipeline.build import load_asset_registry
        try:
            reg = load_asset_registry(self.project_root)
        except Exception as e:                # noqa: BLE001
            self._loaded_registry = None
            return {'ok': False, 'error': str(e), 'counts': {}}
        if reg is None:
            self._loaded_registry = None
            return {'ok': False, 'error': '这个目录下找不到资源表（xlsx）',
                    'counts': {}}
        self._loaded_registry = reg
        self.refresh_symbols()
        counts = {k: len(getattr(reg, k, []) or [])
                  for k in ('images', 'music', 'sounds', 'voices',
                            'transforms', 'characters')}
        return {'ok': True, 'error': '', 'counts': counts, 'registry': reg}


    def source(self):
        """
        把 registry 适配成"看起来像 engine"的对象。

        `app.beats` 里那批检索函数（`participants` / `search_assets` /
        `asset_row` …）历史上收的都是 **engine**（用 `engine.get_registry()`
        取表）。会话手上只有 registry，于是统一过一次
        `RegistrySource` 适配 —— **不在会话里重写一遍检索逻辑**。
        """
        from storygraph.app.picker import RegistrySource
        return RegistrySource(self.registry(), self.project_root)

    def refresh_symbols(self):
        """重算符号表（脚本声明变了 / 换了项目时调用）。"""
        self.symbols = make_symbols(self.registry())
        return self.symbols

    def characters(self) -> list:
        """可用说话人 ID（排序）。"""
        reg = self.registry()
        try:
            return sorted((getattr(reg, 'characters', {}) or {}).keys())
        except Exception:                    # noqa: BLE001
            return []

    def participants(self, limit: int = 8) -> list:
        """本项目的参与角色（含脚本声明；见 `beats.participants`）。"""
        if self.registry() is None:
            return []
        try:
            return B.participants(self.source(), limit=limit)
        except Exception:                    # noqa: BLE001
            return []

    def assets_registry(self):
        """
        「打开项目」装进来的那份资源表（**不回落** provider，见下）。

        为什么要单独一个访问器：`registry()` 会在没有已加载资源表时去问
        `registry_provider`，而主窗口的 provider 又要优先看这份 —— 两边互相
        调用就成环了。所以这里只回答"我手上有没有"，有没有都由调用方决定。
        """
        return self._loaded_registry

    #: 资产种类 → 资源表字段名（与 `_write_candidate_sheets` 的映射同源）
    ASSET_KINDS = {'character': 'characters', 'image': 'images',
                   'transform': 'transforms', 'music': 'music',
                   'sound': 'sounds', 'voice': 'voices'}

    def asset_choices(self, asset_type: str) -> list:
        """
        资源表 → 卡片下拉/选择器的候选：`[(值, 说明), ...]`（按值排序）。

        用户 2026-10-03：

        > 添加立绘，添加语音，添加音乐，全部都没有反应！
        > 我不是已经注册了资产吗？为何不能从中选取并导入呢？？？

        **值是"脚本里该写的那个东西"**：
          · image      → image spec（`ling happy` / `bg bg9_5`），与选择器一致
          · music/sound/voice → **资产 ID**（与 Excel 的候选表同源！真实路径
            由渲染器 `_render_audio_from_fields` 翻出来，查不到就写
            `# TODO: 缺少资源 …`，所以写 ID 是安全的）
          · transform/character → ID

        候选来源与 Excel 的下拉**同一份数据**（`registry.<复数>`），
        说明文字复用 `describe_asset` —— 不另造一套"什么算可选资产"。
        """
        reg = self.registry()
        field = self.ASSET_KINDS.get(str(asset_type or ''))
        if reg is None or not field:
            return []
        rows = getattr(reg, field, None) or {}
        out = []
        for aid in sorted(rows.keys()):
            row = rows.get(aid) or {}
            if asset_type == 'image':
                value = self._image_spec_of(aid, row)
            else:
                value = aid
            out.append((value, self._describe_asset(asset_type, row)))
        return out

    @staticmethod
    def _image_spec_of(aid, row) -> str:
        """图片行 → image spec（`tag attr`）。复用 `app.picker`（选择器同源）。"""
        try:
            from storygraph.app import picker as P
            return P.image_spec_of(aid, row)
        except Exception:                     # noqa: BLE001
            return str(aid)

    @staticmethod
    def _describe_asset(asset_type, row) -> str:
        try:
            from storygraph.assets.asset_registry import describe_asset
            return describe_asset(asset_type, row)
        except Exception:                     # noqa: BLE001
            return ''

    # ------------------------------------------------------------------
    #  载入
    # ------------------------------------------------------------------

    def load(self, path) -> int:
        """
        载入一个 .rpy，解析成 Beat 列表。返回拍数。

        与旧路径**同一条语义**：ParseResult → beats → 补 Stage → hydrate DSL。
        """
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        path = Path(path)
        pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
        raw = beats_from_parse_result(pr)
        symbols = self.refresh_symbols()
        self.beats = list(raw)
        for b in self.beats:
            b.stage = compose_stage_extra(b, symbols)
        hydrate_from_dsl(self.beats, symbols)
        self.script_path = path
        # 换了文件，旧的撤销记录就没有意义了（拍号对不上别的章节）。
        self.forget_undo()
        return len(self.beats)

    def has(self, idx: int) -> bool:
        return 0 <= idx < len(self.beats)

    # ------------------------------------------------------------------
    #  剧情图：保存 / 加载布局 + 导出控制流（旧版右栏「画布」那一组）
    # ------------------------------------------------------------------

    #: 布局 JSON 格式版本（**与旧版 tkinter 逐字段一致**，见 save_layout）
    LAYOUT_KEYS = ('nodes', 'edges', 'next_ids')

    def save_layout(self, graph, target) -> dict:
        """
        把剧情图（含每个块的手工坐标）存成 JSON。

        用户 2026-10-04：

        > 我的旧版本有…有保存布局.json！现在这个新版本呢？
        > 为何模块编辑器的功能缺失那么多？？？

        字段**故意和旧版一模一样**（旧版 `graph_view.save_layout`）：

            {"nodes": {"<id>": {label,x,y,is_current,operations,file_path,
                                line_number}},
             "edges": {"<id>": {from,to,type,condition,option_text}},
             "next_ids": {"node": n, "edge": m}}

        这样用户以前存过的 `.json` 还能直接加载回来（新格式会让旧文件
        白白作废，而这是一份"手排的版式"，重建很贵）。

        迁移（2026-10-05）：**新增** `statements` 字段（结构化，含 `source`
        文件/行号与 `condition`），`operations` 照旧写 —— 新版本能读回全部
        信息，老版本读 `operations` 也不受影响（只多一个它不认识的键）。
        """
        import json
        data = {
            'nodes': {str(nid): {
                'label': n.label_name,
                'x': n.x, 'y': n.y,
                'is_current': bool(getattr(n, 'is_current', False)),
                'operations': list(getattr(n, 'operations', []) or []),
                # 结构化真源（老读者会忽略这一项）
                'statements': [s.to_dict()
                               for s in (getattr(n, 'statements', []) or [])],
                'file_path': getattr(n, 'file_path', None),
                'line_number': getattr(n, 'line_number', None),
            } for nid, n in (getattr(graph, 'nodes', {}) or {}).items()},
            'edges': {str(eid): {
                'from': e.from_id, 'to': e.to_id, 'type': e.type,
                'condition': e.condition, 'option_text': e.option_text,
                # `from` 子句也要存：不存的话"存布局 → 读布局"就把它丢了
                'from_clause': getattr(e, 'from_clause', ''),
            } for eid, e in (getattr(graph, 'edges', {}) or {}).items()},
            'next_ids': {
                'node': int(getattr(graph, 'next_node_id', 1)),
                'edge': int(getattr(graph, 'next_edge_id', 1)),
            },
        }
        try:
            Path(target).write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding='utf-8')
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'path': str(target),
                    'nodes': 0, 'edges': 0}
        return {'ok': True, 'error': '', 'path': str(target),
                'nodes': len(data['nodes']), 'edges': len(data['edges'])}

    def load_layout(self, source) -> dict:
        """
        读回一份布局 JSON，返回**新的** `Graph`（不动调用方手上那张图）。

        `next_ids` 缺失时按最大值兜底（手工编辑过的文件也不至于崩）。
        未知字段一律忽略 —— 旧文件里有 `is_current` 之类我们现在不用的键。
        """
        import json
        try:
            data = json.loads(Path(source).read_text(encoding='utf-8'))
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': f'读不了这个 JSON：{e}',
                    'graph': None, 'nodes': 0, 'edges': 0}
        if not isinstance(data, dict) or 'nodes' not in data:
            return {'ok': False, 'error': '这个文件不是剧情图的布局（缺 nodes）',
                    'graph': None, 'nodes': 0, 'edges': 0}

        try:
            graph = graph_from_layout_dict(data)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': f'布局文件里有读不懂的条目：{e}',
                    'graph': None, 'nodes': 0, 'edges': 0}
        return {'ok': True, 'error': '', 'graph': graph,
                'nodes': len(graph.nodes), 'edges': len(graph.edges),
                'path': str(source)}

    def export_control_flow(self, graph, target, wrapper=None) -> dict:
        """
        把剧情图导出成 Ren'Py **控制流脚本**（`<label>_control` 那些 label）。

        用户 2026-10-04：

        > 我的旧版本有导出控制流图.rpy文件，label末尾加_controller的那个，
        > 你自己找！……现在这个新版本呢？

        `pipeline.renpy_exporter.export_to_renpy` **一直在**（旧版右栏
        「导出为Ren'Py（控制流图）」调的就是它），Qt 版从没接上而已。
        这里只做"调用 + 把结果变成结构化返回值"，不改它的生成规则。
        """
        from storygraph.pipeline.renpy_exporter import export_to_renpy
        n_nodes = len(getattr(graph, 'nodes', {}) or {})
        if not n_nodes:
            return {'ok': False, 'error': '剧情图是空的，没有可导出的控制流',
                    'path': str(target), 'labels': [], 'count': 0}
        try:
            export_to_renpy(graph, str(target), wrapper=wrapper)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'path': str(target),
                    'labels': [], 'count': 0}
        # `export_to_renpy` 自己吞掉写盘异常（只 notify），所以要看文件在不在
        if not Path(target).is_file():
            return {'ok': False, 'error': '文件没有生成（写盘失败？）',
                    'path': str(target), 'labels': [], 'count': 0}
        # 与导出器**同一处判定**（算两遍必然漂移：曾经 wrapper=False 时
        # 模式文案还写着"补 _control"，而标签其实没有补）
        controller_style = (bool(getattr(graph, 'control_flow', False))
                            if wrapper is None else not bool(wrapper))
        mode = ('原样重建（控制流图）' if controller_style
                else '补 _control 包装（章节图）')
        # 混合图（既导了章节、又导了 controller）如实告知一句：
        # 章节图模式下 controller 的 chapter0_0_control 会被再包一层
        # （chapter0_0_control_control）—— 产物能跑但冗余、容易看错。
        # **不在这里猜哪个节点才是控制流标签**（那又要拿名字当保留字用），
        # 只告知 + 让用户改用另一种导出入口。
        warnings = []
        if not controller_style:
            names = {str(n.label_name or '')
                     for n in (getattr(graph, 'nodes', {}) or {}).values()}
            mixed = sorted(n for n in names if f'{n}_control' in names)
            if mixed:
                warnings.append(
                    f'这张图同时含章节与已有控制流标签（如 {mixed[0]}）：'
                    f'章节图模式会给它们再包一层。建议分开导出，'
                    f'或用「按控制流图原样导出」。')
        labels = []
        for n in (getattr(graph, 'nodes', {}) or {}).values():
            name = str(n.label_name or '')
            if controller_style:
                labels.append(name)
            else:
                # **一律**补一层（哪怕章节就叫 A_control）——不许拿名字当保留字
                labels.append(f'{name}_control')
        return {'ok': True, 'error': '', 'path': str(target),
                'labels': labels, 'count': len(labels), 'mode': mode,
                'warnings': warnings}

    # ------------------------------------------------------------------
    #  多引擎导出 / 资产统计 / 协作（原先"后端有、界面没入口"的三样）
    # ------------------------------------------------------------------

    #: `pipeline.engine_exporters` 支持的引擎（顺序即界面下拉顺序）
    EXPORT_ENGINES = ('unity', 'godot', 'json', 'csv', 'renpy')

    def export_ir(self, engine: str, out_dir) -> dict:
        """
        把当前章节的演出表导出成**其他引擎**要的格式。

        用户 2026-10-05："请确保所有的功能都接上UI" —— 这条以前只有
        `pipeline.engine_exporters.export_ir` 这个后端函数，Qt 版没有任何入口。

        `engine`: unity / godot / json / csv / renpy（见 `EXPORT_ENGINES`）。
        返回 `{ok, engine, out_dir, files, error}`。
        """
        engine = str(engine or '').lower()
        if engine not in self.EXPORT_ENGINES:
            return {'ok': False, 'engine': engine, 'out_dir': str(out_dir),
                    'files': [],
                    'error': f'不支持的引擎：{engine or "（空）"}'}
        if not self.beats:
            return {'ok': False, 'engine': engine, 'out_dir': str(out_dir),
                    'files': [], 'error': '还没有载入章节，没有可导出的演出表'}
        from storygraph.pipeline.engine_exporters import export_ir
        try:
            files = export_ir(engine, self.beats, Path(out_dir),
                              registry=self.registry(),
                              project=str(self.project_root or ''),
                              title=Path(self.script_path).stem
                              if self.script_path else '')
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'engine': engine, 'out_dir': str(out_dir),
                    'files': [], 'error': str(e)}
        return {'ok': True, 'engine': engine, 'out_dir': str(out_dir),
                'files': [str(f) for f in (files or [])], 'error': ''}

    def asset_linker(self):
        """
        角色 ↔ 剧本的引用关系（角色台词量排名 / 未定义角色 / 没出场的角色）。

        `AssetPanelData` 要 `engine.linker`，而 Qt 会话手上没有引擎 ——
        这里按需**解析一次全项目脚本**并缓存。成本只付一次（40 个文件约 1 秒），
        而且只在用户真的打开"资产统计"时才付。
        """
        if getattr(self, '_linker', None) is not None:
            return self._linker
        if not self.project_root:
            return None
        try:
            from storygraph.parsing.character_script_linker import \
                CharacterScriptLinker
            from storygraph.parsing.discover import discover_scripts
            from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
            analysis = IncrementalRenPyAnalyzer().analyze(
                files=discover_scripts(self.project_root), graph=None)
            parse_result = analysis[0] if isinstance(analysis, tuple) else analysis
            self._linker = CharacterScriptLinker(parse_result)
            # `link()` 才建立反向索引（台词量 / label 归属）；不调它就是全 0
            try:
                self._linker.link()
            except Exception:                 # noqa: BLE001
                swallow('editor.asset_linker')
        except Exception:                     # noqa: BLE001
            self._linker = None
        return self._linker

    def asset_overview(self, top: int = 15) -> dict:
        """
        资产 / 角色的只读统计（资产面板那一套查询，界面用）。

        数据源是 `app.asset_panel.AssetPanelData`（框架无关的查询层）——
        界面**不重写**"按 category 分组 / 角色台词量排名"这类逻辑。
        返回 `{ok, statistics, categories, top_characters, undefined,
        unused_characters, unused_assets, broken, error}`。
        """
        from storygraph.app.asset_panel import AssetPanelData
        if self.registry() is None:
            return {'ok': False, 'error': '还没有资源表 —— 先「📁 打开项目」',
                    'statistics': {}, 'categories': {}, 'top_characters': [],
                    'undefined': [], 'unused_characters': [],
                    'unused_assets': [], 'broken': []}
        panel = AssetPanelData(self.source())
        # 角色相关的查询走 linker（`AssetPanelData` 从 engine 上取）：
        # 没有它，"角色台词量 / 未定义角色 / 没出场的角色"永远是空的。
        linker = self.asset_linker()
        if linker is not None:
            try:
                panel.engine.linker = linker
            except Exception:                 # noqa: BLE001
                swallow('editor.asset_overview')
        out = {'ok': True, 'error': ''}
        for key, fn in (('statistics', panel.get_statistics),
                        ('categories', panel.get_category_breakdown),
                        ('undefined', panel.get_undefined_characters),
                        ('unused_characters', panel.get_unused_characters),
                        ('broken', panel.get_broken_references),
                        ('unused_assets', lambda: panel.get_unused_assets())):
            try:
                out[key] = fn()
            except Exception as e:            # noqa: BLE001
                out[key] = {} if key in ('statistics', 'categories') else []
                out.setdefault('warnings', []).append(f'{key}: {e}')
        try:
            out['top_characters'] = panel.get_character_ranking()[:top]
        except Exception:                     # noqa: BLE001
            out['top_characters'] = []
        # 大列表只给前 N 条（界面是给人看的，不是 dump）
        for key in ('unused_assets', 'broken', 'undefined'):
            if isinstance(out.get(key), list) and len(out[key]) > 200:
                out[key] = out[key][:200]
        return out

    # ---- 协作（`collab.collab_store`：SQLite ↔ Excel）----

    def collab_status(self, db_path) -> dict:
        """协作总览：谁锁了多少、各状态多少、最近改了什么。"""
        from storygraph.collab.collab_store import status_summary
        try:
            data = status_summary(Path(db_path))
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'summary': {}}
        return {'ok': True, 'error': '', 'summary': data or {}}

    def collab_pull(self, db_path, sheet_path, actor: str = '') -> dict:
        """数据库 → Excel（前端）：写 Beats + 候选表 + 协作信息表。"""
        from storygraph.collab.collab_store import pull
        try:
            res = pull(Path(db_path), Path(sheet_path), actor=actor,
                       registry=self.registry())
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'result': {}}
        return {'ok': True, 'error': '', 'result': res or {}}

    def collab_push(self, db_path, sheet_path, actor: str,
                    force: bool = False) -> dict:
        """Excel（前端）→ 数据库；冲突/被锁一律不静默覆盖。"""
        from storygraph.collab.collab_store import push
        try:
            res = push(Path(db_path), Path(sheet_path), actor=actor,
                       force=force)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'result': {}}
        return {'ok': True, 'error': '', 'result': res or {}}

    def collab_init(self, db_path, users=()) -> dict:
        """建库并登记成员（角色：文案 / 策划 / 程序）。"""
        from storygraph.collab.collab_store import init_db
        try:
            path = init_db(Path(db_path), users=tuple(users or ()))
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'path': str(db_path)}
        return {'ok': True, 'error': '', 'path': str(path)}

    # ------------------------------------------------------------------
    #  增 / 删卡片（结构编辑）
    # ------------------------------------------------------------------

    def insert_beat(self, idx: int = None, after: bool = True) -> dict:
        """
        在当前这一拍**后面**插入一张空卡片，返回新拍号。

        用户 2026-10-03：

        > 你需要加入一个：插入新卡片的功能和删除该卡片的功能，
        > 不然只能够修改，不能增删，还是没用！

        新卡的字段**刻意是空的**（不是从上一拍复制的）：
        复制会让"背景/音乐"这类持续状态被**显式写死**到新的一拍上 ——
        用户以为自己只是插了一句话，结果脚本里多了一条 `scene`。

        `label` 沿用邻拍的（新卡要落在同一个 label 块里，否则生成的代码
        会跑到别的章节去）。`seq`/`line_id` 由这里重排，保证唯一。
        """
        if not self.beats:
            return {'ok': False, 'error': '还没有载入章节', 'index': -1}
        pos = len(self.beats) if idx is None else int(idx)
        pos = max(0, min(pos, len(self.beats) - 1))
        at = pos + 1 if after else pos
        from storygraph.story.beat_model import Beat
        template = self.beats[pos]
        beat = Beat(seq=0, label=str(getattr(template, 'label', '') or ''),
                    kind='say', text='')
        self.beats.insert(at, beat)
        self._renumber()
        self.forget_undo()                    # 拍号变了，旧的撤销记录对不上
        return {'ok': True, 'error': '', 'index': at,
                'label': beat.label, 'count': len(self.beats)}

    def delete_beat(self, idx: int) -> dict:
        """
        删掉第 idx 张卡片，返回 `{ok, index, removed, count}`。

        **只改内存**：落盘仍要按「⤓ 导出」，所以误删的退路是
        `restore_last_removed()`（单级）或重新载入这一章。
        """
        if not self.has(idx):
            return {'ok': False, 'error': '这一拍不存在', 'removed': None}
        if len(self.beats) <= 1:
            return {'ok': False, 'error': '一章至少要留一拍', 'removed': None}
        removed = self.beats.pop(idx)
        self._last_removed = (idx, removed)
        self._renumber()
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': min(idx, len(self.beats) - 1),
                'removed': removed, 'count': len(self.beats)}

    def restore_last_removed(self) -> dict:
        """把**上一次删掉的那张卡片**插回原位（单级撤销）。"""
        if self._last_removed is None:
            return {'ok': False, 'error': '没有可恢复的卡片', 'index': -1}
        idx, beat = self._last_removed
        at = max(0, min(int(idx), len(self.beats)))
        self.beats.insert(at, beat)
        self._last_removed = None
        self._renumber()
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': at, 'count': len(self.beats)}

    def has_removed(self) -> bool:
        return self._last_removed is not None

    def _renumber(self):
        """重排 `seq` 与 `line_id`（拍号变了以后必须一致，否则协作/定位错位）。"""
        label = ''
        for b in self.beats:
            if str(getattr(b, 'label', '') or '').strip():
                label = str(b.label)
                break
        for i, b in enumerate(self.beats, 1):
            b.seq = i
            if not str(getattr(b, 'label', '') or '').strip():
                b.label = label
            b.line_id = f'{b.label}.{i:04d}'

    def __len__(self) -> int:
        return len(self.beats)

    # ------------------------------------------------------------------
    #  读：Beat → 表单（前端直接塞控件）
    # ------------------------------------------------------------------

    def fields(self, idx: int) -> dict:
        """这一拍的主区+高级区字段（供表单显示）。"""
        if not self.has(idx):
            return {}
        return B.beat_to_fields(self.beats[idx], self.symbols)

    def cards(self, idx: int) -> list:
        """
        这一拍的**立绘卡**（供网格显示）。

        卡的数据契约（与两套前端一致）：
            {image, expression, position, at, speaking, order_index, extra_rows,
             effective_at, inherited_at}

        `effective_at` / `inherited_at` 是**显示用**的补充（用户 2026-10-03）：

        > 我说的位置不显示，是这个立绘下面的位置没有显示……这是bug，
        > 明明说了在i11……

        根因：`at` 在 Ren'Py 里是**持续状态** —— 这一拍只写
        `show ye base_handdown care`（没有 `at`），位置是更早那一拍
        `show ye care at i11` 留下的。舞台快照（累积推导）知道它现在在 `i11`，
        而卡片只读"这一拍自己写的"，于是显示成 `—`。

        所以这里把**当前生效**的位置也算出来给前端显示（带"继承"标记）；
        写回仍然只写 `position`/`at` —— 用户没碰那一格就不会把继承值
        物化到这一拍（那是"改了才写"的底线）。
        """
        if not self.has(idx):
            return []
        rows = B.beat_sprite_rows(self.beats[idx], self.symbols)
        effective = self._effective_positions(idx)
        items = []
        for card in B.group_sprites_by_tag(rows):
            it = dict(card.get('active') or {})
            it['extra_rows'] = list(card.get('extras') or [])
            it['order_index'] = card.get('first_index', 0)
            own = (str(it.get('at') or '').strip()
                   or str(it.get('position') or '').strip())
            eff = effective.get(str(it.get('image') or '').strip(), '')
            it['effective_at'] = eff
            it['inherited_at'] = '' if own else (eff or '')
            items.append(it)
        return items

    def _effective_positions(self, idx: int) -> dict:
        """
        这一拍**屏幕上真正生效**的立绘位置：`{image: at}`。

        来自 `snapshot_rows`（与预览、与 Excel 的舞台快照表同源）——
        那里按 Ren'Py 的语义累积：`at` 不写就沿用上一次。
        """
        try:
            from storygraph.story.stage_snapshot import snapshot_rows
            rows = snapshot_rows(self.beats)
            if idx >= len(rows):
                return {}
            actors = (rows[idx].get('state') or {}).get('actors') or []
            return {str(img): str(at or '') for img, at in actors}
        except Exception:                     # noqa: BLE001
            return {}

    def list_entry(self, idx: int) -> dict:
        """章节列表要用的一行摘要（前缀图标 / 说话人 / 台词预览）。"""
        b = self.beats[idx]
        prefix = {'narration': '☁', 'menu': '⚑', 'menu_option': '➜'}.get(
            getattr(b, 'kind', ''), '')
        return {
            'index': idx,
            'who': (getattr(b, 'speaker', '') or '').strip(),
            'prefix': prefix,
            'text': (getattr(b, 'text', '') or '').strip() or '（无台词）',
            'line_id': getattr(b, 'line_id', '') or '',
        }

    def inherited_hint(self, idx: int) -> str:
        """继承提示（背景/音乐沿用上一拍时给策划的提示）。"""
        if not self.has(idx):
            return ''
        return B.inherited_state_hint(self.beats, idx)

    def stage_digest_text(self, idx: int) -> str:
        """
        这一拍**生效的舞台状态**（变化 / 背景 / 立绘 / CG / 音乐 / 音效 /
        语音 / 条件 / 备注），多行文本。

        用户 2026-10-03：

        > 为何不写明现在的舞台情况是什么样的，旧版明明都有……

        旧版的预览面板用的是 `story.stage_snapshot.stage_digest`，卡片搬 Qt
        时**没把它接上**（函数一直在，只是没人调用）。这里复用同一份
        `stage_snapshots` 推导 —— 与预览、与 Excel 的「舞台快照」表同源，
        不另写一套"当前舞台"的算法。
        """
        if not self.has(idx):
            return ''
        try:
            from storygraph.story.stage_snapshot import stage_digest
            return stage_digest(self.beats, idx)
        except Exception:                     # noqa: BLE001
            return ''

    def missing_chapter_files(self, graph, project_root=None) -> dict:
        """
        图上"被别的章节跳到、但那个文件还没导入"的 label 分别在哪个文件里。

        用户 2026-10-03：

        > 我连线呢！？？？tkinter那一版都有连线，你这个为何显示不出来？

        实测原因不是渲染坏了，而是**他导入的那几章彼此之间没有跳转** ——
        `chapter1_1/1_2/1_3.rpy` 各自只有一个 label + `return`，串起它们的是
        `chapter_controller.rpy`。所以图里确实没有线可画。

        工具必须把这句话说出来，而不是让用户对着一张没有线的图猜。
        返回 `{'files': [...], 'unknown': [...], 'labels': [...]}`：

          · `files`   声明了那些 label 的文件（**建议一起导入** —— 导入后
            连线就出现了）；
          · `unknown` 任何文件里都找不到的 label（脚本里写错了 / 动态跳转）。
        """
        nodes = getattr(graph, 'nodes', None) or {}
        labels = sorted({str(getattr(n, 'label_name', '') or '')
                         for n in nodes.values()
                         if not getattr(n, 'file_path', None)} - {''})
        root = project_root or self.project_root
        if not root:
            return {'files': [], 'unknown': list(labels), 'labels': labels}
        already = {str(Path(f)) for f in self.graph_files(graph)}
        files = set()
        unknown = []
        try:
            from storygraph.parsing.discover import (
                find_files_referencing_labels, find_label_files)
            # ① 声明方向：图上的占位块（被跳到、但文件没导入）由谁声明
            if labels:
                declared = find_label_files(root, labels)
                files |= {str(p) for p in declared.values()}
                unknown = sorted(lb for lb in labels if lb not in declared)
            # ② 引用方向：**谁引用了我已经导入的这些章节**
            #    （导入的章节自己不含跳转时，串起它们的就是这个文件）
            imported_labels = sorted({str(getattr(n, 'label_name', '') or '')
                                      for n in nodes.values()
                                      if getattr(n, 'file_path', None)} - {''})
            if imported_labels:
                refs = find_files_referencing_labels(root, imported_labels)
                for paths in refs.values():
                    files |= {str(p) for p in paths}
        except Exception:                     # noqa: BLE001
            return {'files': [], 'unknown': list(labels), 'labels': labels}
        files = sorted(f for f in files if str(Path(f)) not in already)
        return {'files': files, 'unknown': unknown, 'labels': labels}

    def speaking_card_index(self, idx: int) -> int:
        """当前说话人对应哪张立绘卡（-1 = 没有）。"""
        if not self.has(idx):
            return -1
        rows = B.beat_sprite_rows(self.beats[idx], self.symbols)
        return B.speaking_sprite_index(self.beats[idx], rows, self.symbols)

    # ------------------------------------------------------------------
    #  写：表单 → Beat
    # ------------------------------------------------------------------

    def apply(self, idx: int, fields: dict, cards: list = None,
              advanced_open: bool = False,
              stage_loaded: str = None) -> Optional[Beat]:
        """
        把表单内容写回第 idx 拍。返回那一拍（越界返回 None）。

        语义（与 tkinter 版 `collect_beat` 一字不差，现在只有这一份实现）：
          · 主区**始终**写回；
          · 高级区**只在展开时**写回（收起 ≠ 清空）；
          · `show` 只有拼得出内容时才覆盖（**空网格不清空立绘**）；
          · `at` 只在"所有立绘位置一致"时才整拍（否则留空，防错位）；
          · `Stage` **每次重算**（它是投影，不是输入），**除非用户真的手改了
            那一格**（见下）；
          · 最后过一遍 `hydrate_from_dsl`，保证与 .rpy 同源。

        `stage_loaded`：表单**载入时**那一格里的原文（Q 前端从
            `BeatCardEditor.load_fields` 里记下来回传）。
            用来判断"用户手改了 Stage 没有" —— 判据是
            `typed != loaded`，而不是"格子非空"：`beat_to_fields` 会把
            结构化列投影进这一格显示，所以非空**不代表**人改过。
            传 None 时退回用 `beat.stage`（老调用方的行为）。
        """
        if not self.has(idx):
            return None
        b = self.beats[idx]
        fields = dict(fields or {})

        b.speaker = str(fields.get('Speaker') or '').strip()
        b.text = str(fields.get('Text') or '').strip()
        b.scene = str(fields.get('Scene') or '').strip()
        b.bgm = str(fields.get('BGM') or '').strip()
        b.voice = str(fields.get('Voice') or '').strip()
        b.comment = str(fields.get('Comment') or '').strip()

        if advanced_open:
            b.sfx = str(fields.get('SFX') or '').strip()
            b.code = str(fields.get('CodeBlock') or '').strip()
            b.raw = ''
            b.label = str(fields.get('Label') or '').strip() or b.label
            b.kind = str(fields.get('Kind') or '').strip() or b.kind
            b.notes = str(fields.get('Notes') or '').strip()

        if cards is not None:
            show = B.cards_to_show(cards, self.symbols)
            if show:
                b.show = show
            b.at = uniform_position_of(cards)

        # 条件显示（卡片上的「＋ 条件」）。**只在调用方真的给了这个键时才写**：
        # 用 `fields.get(..., b.condition)` 会让"没给"和"给了空串"分不开，
        # 而这两件事的语义完全相反（不改 vs 清空条件）。
        if 'condition' in fields:
            b.condition = str(fields.get('condition') or '').strip()

        self._apply_stage(b, fields, stage_loaded)

        try:
            hydrate_from_dsl([b], self.symbols)
        except Exception:                    # noqa: BLE001 - 与旧实现一致的兜底
            swallow('editor.apply')
        return b

    def _apply_stage(self, b: Beat, fields: dict, stage_loaded):
        """
        `Stage` 那一格的写回规则（**这是"自己吃自己"事故的正解**）。

        背景：`Stage` 是结构化列的**投影**（`beat_to_fields` 把它算出来显示），
        所以把显示值原样写回再 hydrate 就是自己吃自己 —— 实测
        `hide cg03` 会静默变成 `cg03; cg03`（305 拍里 11 拍被改写）。

        两条分支：
          · **用户真的改了这一格**（`typed` 非空且 ≠ 载入时的原文）：
            以人写的为准，并且**先清掉结构化舞台字段再 hydrate**，
            否则"旧投影 + 新文本"会叠加成两份指令。
          · **没改**：重新投影，并把"结构化列永远表达不了"的那部分
            （`[停顿] 1.0` 之类）原样留下 —— 否则打开卡片点一下下一句，
            作者手写的东西就没了。

        为什么"空值"不算手改：把格子清空是很容易的误操作，而它的后果是
        抹掉这一拍的 scene/show/hide。语料里"我在 Stage 框里删光"不是
        一种需求，所以空值一律走重新投影（**永远不因为空 Stage 丢数据**）。
        """
        typed = None
        if 'Stage' in fields:                # 表单真的给了这一格（高级区展开）
            typed = str(fields.get('Stage') or '').strip()
        loaded = (b.stage if stage_loaded is None
                  else str(stage_loaded or '')).strip()

        if typed and typed != loaded:
            b.stage = typed
            for attr in ('scene', 'scene_at', 'show', 'hide', 'at',
                         'with_', 'transform', 'stage_order'):
                setattr(b, attr, '')
            return

        # 没手改：结构化列是唯一真源 → 重新投影；
        # 表达不了的部分（pause / 认不出的原生语句）从原文里捞回来。
        parts = [t for t in (compose_stage_extra(b, self.symbols),
                             stage_only_ops(loaded, self.symbols)) if t]
        b.stage = '; '.join(parts)
        # 投影已经在结构化字段里了 → 标记成"已吸过"，
        # 免得下面那次 hydrate 又把它吸第二遍（见 `hydrate_from_dsl`）。
        try:
            b._hydrated_stage = b.stage
        except Exception:                    # noqa: BLE001 - Beat 不该是槽位
            swallow('editor._apply_stage')

    def set_speaker(self, idx: int, cid: str) -> bool:
        """只改说话人（左栏双击角色时用）。"""
        if not self.has(idx):
            return False
        self.beats[idx].speaker = str(cid or '')
        return True

    def remove_sprite(self, idx: int, image_spec: str) -> Optional[Beat]:
        """从这一拍删掉某个立绘（按 image spec 精确匹配）。"""
        if not self.has(idx):
            return None
        b = self.beats[idx]
        rows = [r for r in B.beat_sprite_rows(b, self.symbols)
                if B.image_spec_of_row(r) != image_spec]
        new_show = B.apply_sprite_rows(getattr(b, 'show', '') or '', rows)
        b.show = new_show
        b.at = B.uniform_sprite_position(rows)
        b.stage = compose_stage_extra(b, self.symbols)
        return b

    # ------------------------------------------------------------------
    #  保存 / 撤销（"这张卡片的修改"）
    # ------------------------------------------------------------------

    def snapshot(self, idx: int) -> Optional[BeatSnapshot]:
        """
        给第 idx 拍拍一张快照（撤销/比较用）。越界返回 None。

        只装**标量字段** + `ordered`（后者是 raw/code 的交错顺序，
        丢了会把 `$ f()` 与 `show screen` 的交替演出排乱）。
        其它状态（`src_first` 等增量导出用的行号、`_from_script` 标记）
        刻意**不进快照**：撤销一次编辑不该改掉它们在源文件里的位置。
        """
        if not self.has(idx):
            return None
        b = self.beats[idx]
        data = {name: getattr(b, name, '') for name in _SNAPSHOT_FIELDS}
        data['ordered'] = copy.deepcopy(list(getattr(b, 'ordered', None) or []))
        return BeatSnapshot(idx=idx, data=data)

    def restore(self, snap: BeatSnapshot) -> Optional[Beat]:
        """把快照摊回它对应的那一拍（越界返回 None）。"""
        if snap is None or not self.has(snap.idx):
            return None
        b = self.beats[snap.idx]
        for name, value in snap.data.items():
            if name == 'ordered':
                b.ordered = copy.deepcopy(list(value or []))
            else:
                setattr(b, name, value)
        # 还原出来的 stage 已经和结构化字段一致 → 标记"吸过"，
        # 免得下次 hydrate 把同一段原文再吸一遍（翻倍）。
        try:
            b._hydrated_stage = b.stage
        except Exception:                    # noqa: BLE001
            swallow('editor.restore')
        return b

    def save(self, idx: int, fields: dict, cards: list = None,
             advanced_open: bool = False, stage_loaded: str = None) -> dict:
        """
        **保存这张卡片的修改** —— 前端两个按钮里的那一个（另一个是撤销）。

        与 `apply` 的区别不是语义（内部就是调它），而是**问了"改没改"**：

          · 没改动 → 不压撤销栈（`changed=False`），撤销按钮不必亮起来；
          · 有改动 → 把**保存前**的那一拍压进撤销栈（单级：只记最近一次），
            所以「撤销修改」能把这张卡恢复成上一次保存时的样子。

        返回：
            {ok, beat, changed, line_id, index}
            · `ok=False` 表示没有载入任何一拍；
            · `changed=False` 表示内容一个字节都没变（含"翻页顺带提交"）。

        判"改没改"用的是**保存前后两份快照的指纹**（见
        `BeatSnapshot.fingerprint`），而不是 `Beat.content_hash` ——
        后者是持久化字段，编辑后还是旧值，拿它当判据会把"改了"判成"没变"。
        """
        if not self.has(idx):
            return {'ok': False, 'beat': None, 'changed': False,
                    'line_id': '', 'index': idx}
        before = self.snapshot(idx)
        beat = self.apply(idx, fields, cards=cards,
                          advanced_open=advanced_open,
                          stage_loaded=stage_loaded)
        changed = (self.snapshot(idx).fingerprint() != before.fingerprint())
        if changed:
            self._last_saved[idx] = before
        return {
            'ok': True,
            'beat': beat,
            'changed': changed,
            'line_id': getattr(beat, 'line_id', '') or f'第 {idx + 1} 拍',
            'index': idx,
        }

    def can_undo(self, idx: int = None) -> bool:
        """这一拍能不能撤销（没保存过、或没变过就是 False）。"""
        if idx is None:
            idx = self._last_idx
        return idx is not None and idx in self._last_saved

    def undo(self, idx: int = None) -> Optional[Beat]:
        """
        **撤销这张卡片的修改** —— 把它恢复成上一次保存时的样子。

        单级撤销（每个 Beat 只留最近一次保存前快照）：撤销一次即回到
        "上一次保存"；再撤就没了（返回 None 且不谎报成功）。
        前端据此决定按钮是否可用、以及要不要提示"没有可撤销的修改"。

        作用域刻意是**逐拍**而不是整章：用户按的是"这张卡片"的撤销，
        回退整章会把别的拍的改动一起抹掉（那才是灾难）。
        """
        if idx is None:
            idx = self._last_idx
        snap = self._last_saved.pop(idx, None) if idx is not None else None
        if snap is None:
            return None
        return self.restore(snap)

    def has_undo(self, idx: int) -> bool:
        """第 idx 拍有没有可撤销的修改（撤销按钮的 enabled 就用它）。"""
        return idx in self._last_saved

    def mark_loaded(self, idx: int) -> None:
        """
        告诉会话"前端现在显示的是第 idx 拍"。

        会话自己不跟踪用户在翻哪一页（那是前端的状态），但撤销需要一个
        **默认目标**：`undo()` 不带参数时要撤哪一拍。前端每次载入后调一下。
        """
        self._last_idx = idx if self.has(idx) else None

    @property
    def loaded_index(self):
        """最近一次 `mark_loaded` 的拍号（没载入过是 None）。"""
        return self._last_idx

    def forget_undo(self) -> None:
        """丢弃全部撤销记录（换章/重新载入时调用，免得撤回到别的文件）。"""
        self._last_saved.clear()
        self._last_idx = None

    # ------------------------------------------------------------------
    #  演出预览
    # ------------------------------------------------------------------

    def frames(self) -> list:
        """
        整章的舞台帧（供预览窗口播放）。

        `transform_index` / `screen` 来自项目源码（`transform.rpy` 的
        `transform f21: focus(320)` 与 `gui.init(1920, 1080)`）—— 用户
        2026-10-05："transform 未对应到舞台真实位置"。**按项目根缓存**：
        扫一遍源码是有代价的，但预览只在打开时取一次。
        """
        return build_frames(self.beats, self.registry(),
                            transform_index=self.transform_index(),
                            screen=self.screen_size())

    def transform_index(self) -> dict:
        """项目里的 transform 定义（按项目根缓存；换项目自动失效）。"""
        root = str(self.project_root or '')
        cached = getattr(self, '_transform_index_cache', None)
        if cached is not None and cached[0] == root:
            return cached[1]
        index = {}
        try:
            from storygraph.story.atl import scan_transforms
            index = scan_transforms(self.project_root) if root else {}
        except Exception:                     # noqa: BLE001 - 扫不动就退回启发式
            index = {}
        self._transform_index_cache = (root, index)
        return index

    def screen_size(self) -> tuple:
        """游戏分辨率（`gui.init(w, h)`）；读不到用 1920×1080。"""
        root = str(self.project_root or '')
        cached = getattr(self, '_screen_cache', None)
        if cached is not None and cached[0] == root:
            return cached[1]
        from storygraph.story.atl import DEFAULT_SCREEN, project_screen
        size = project_screen(self.project_root) if root else DEFAULT_SCREEN
        self._screen_cache = (root, size)
        return size

    # ------------------------------------------------------------------
    #  导出（编译 + 增量补丁 + 安全门禁）
    # ------------------------------------------------------------------

    def compile(self):
        """编译当前 beats（返回编译结果对象，不落盘）。"""
        from storygraph.pipeline.compile import compile_beats
        return compile_beats(self.beats, registry=self.registry(),
                             include_transforms=self.should_write_transforms())

    def register_missing_assets(self, asset_dir=None) -> dict:
        """
        把**磁盘上有、资产表里没登记**的资源补进表，然后重新读表。

        用户 2026-10-05：

        > 先查背景图为何没有被登记进去，然后，让背景图被登记进去。

        实现只在 `assets.asset_registry.register_missing_assets`（只追加缺失行、
        写前备份、图片属性做 `.`→`_` 归一），这里只负责"改完立刻重载"。
        """
        from storygraph.assets.asset_registry import register_missing_assets
        if not self.project_root:
            return {'ok': False, 'error': '还没有项目根目录', 'added': 0}
        res = register_missing_assets(self.project_root, asset_dir=asset_dir)
        if res.get('ok') and res.get('added'):
            try:
                self.load_assets()            # 重读表：界面马上能选到新资源
            except Exception:                 # noqa: BLE001
                swallow('editor.register_missing_assets')
            self._transforms_decision = None  # 资源表变了 → 判定重算
        return res

    def should_write_transforms(self) -> bool:
        """
        生成物**要不要自带 transform 定义段**？

        用户 2026-10-05：

        > 全量不要重复写 transform 定义 —— 项目里已经有 definitions.rpy

        判定：这一章用到的自定义变换，**项目里别的 .rpy 已经声明过**
        （`transform <名字>:`）→ 不写（写了就是重复定义，Ren'Py 里后者
        覆盖前者，章节文件被悄悄改语义）。
        找不到就老实写上，让产物自包含。

        成本：一次正则扫项目脚本（40 个文件约 0.3s），结果按需缓存。
        """
        if getattr(self, '_transforms_decision', None) is not None:
            return self._transforms_decision
        decision = True
        try:
            reg = self.registry()
            if reg is None or not self.project_root or not self.beats:
                return True
            from storygraph.parsing.discover import discover_scripts
            from storygraph.story.beat_render import collect_used_transforms
            used = collect_used_transforms(self.beats)
            # 只关心**资源表里带 ATL 的那些**（`render_transform_definitions`
            # 会真正写出来的就是它们）——`used` 里还混着 `left` / `dissolve`
            # 这类 Ren'Py 内置变换，它们项目里当然没人声明，拿它们一起判
            # 会永远得出"其他文件没定义全 → 要写"。
            rows = getattr(reg, 'transforms', None) or {}
            used = {t for t in (used or ()) if t in rows}
            if not used:
                return True
            # 排除"正在导出的这一章"自己（它里面的 transform 定义不算数）
            target = Path(self.script_path) if self.script_path else None
            pattern = re.compile(
                r'^[ \t]*transform[ \t]+([A-Za-z_]\w*)', re.MULTILINE)
            found: set = set()
            for fp in discover_scripts(self.project_root):
                if target is not None and Path(fp) == target:
                    continue
                try:
                    text = Path(fp).read_text(encoding='utf-8',
                                              errors='replace')
                except OSError:
                    continue
                found.update(pattern.findall(text))
                if used <= found:
                    break
            decision = not (used <= found)
        except Exception:                     # noqa: BLE001 - 判不了就老实写
            decision = True
        self._transforms_decision = decision
        return decision

    def default_output_path(self) -> Path:
        stem = self.script_path.stem if self.script_path else 'out'
        base = (self.project_root or Path('.')) / 'build' / 'renpy'
        return base / f'{stem}.generated.rpy'

    def export(self, target) -> dict:
        """
        落盘导出。**所有安全检查都在这里**，前端只管挑路径。

        返回：{ok, path, note, todos, blocking}
          · ok=False 时 `blocking` 是拦截原因（前端展示即可）；
          · note 说明用了增量补丁还是全量重生成。
        """
        target = Path(target)
        result = self.compile()
        if getattr(result, 'blocking', None):
            return {'ok': False, 'path': target, 'blocking': list(result.blocking),
                    'note': '', 'todos': 0}

        text = result.rpy_text
        note = self._patch_note(text)
        if note['used_patch']:
            text = note['text']

        occupied = labels_in_danger(target, self.project_root,
                                   exclude={target, self.script_path})
        issues = evaluate_generation(text, target,
                                     project_root=self.project_root,
                                     occupied=occupied)
        blocking = [i for i in issues if i.is_blocking]
        if blocking:
            return {'ok': False, 'path': target,
                    'blocking': [getattr(i, 'message', str(i)) for i in blocking],
                    'note': note['text_note'], 'todos': 0}

        write_generation(text, target, project_root=self.project_root)
        return {'ok': True, 'path': target, 'blocking': [],
                'note': note['text_note'],
                'todos': getattr(result, 'todo_count', 0)}

    def _patch_note(self, text: str) -> dict:
        """试着走增量补丁；不行就全量重生成，并把原因写成一句人话。"""
        from storygraph.story.patcher import patch_export
        try:
            src = (self.script_path
                   if self.script_path and Path(self.script_path).exists()
                   else None)
            res = patch_export(self.beats, src, text, registry=self.registry())
            if res.ok:
                return {'used_patch': True, 'text': res.text,
                        'text_note': f'增量补丁：对位 {res.matched_beats}/'
                                     f'{res.total_beats} 拍，'
                                     f'改动 {res.changed_lines} 行'}
            reason = ('；'.join(res.reasons)
                      or f'对位率不足（{res.coverage:.0%}）')
            return {'used_patch': False, 'text': text,
                    'text_note': f'全量重生成（增量未采用：{reason}）'}
        except Exception as e:               # noqa: BLE001
            return {'used_patch': False, 'text': text,
                    'text_note': f'全量重生成（增量不可用：{e}）'}

    def path_of_image(self, image_spec: str):
        """
        立绘 image spec（`ling happy`）→ 磁盘上的真实图片路径（找不到 None）。

        **这是立绘缩略图的真源**：路径只在资源表的 `Path` 列里，
        而 `symbols` 里没有资源表 —— 早期前端拿 `symbols.registry` 去取，
        恒为 None，于是缩略图静默地永远加载不出来。
        """
        from storygraph.app.picker import thumbnail_path
        spec = str(image_spec or '').strip()
        if not spec or self.registry() is None:
            return None
        try:
            from storygraph.app.beats import lookup_image_id
            images = getattr(self.registry(), 'images', {}) or {}
            parts = spec.split()
            if not parts:
                return None
            tag, rest = parts[0], parts[1:]
            if not rest:
                return None
            # `ling happy` → `ling_happy` 这类 ID，先在表里找准
            aid = lookup_image_id(images, tag, rest)
            if not aid:
                aid = '_'.join([tag] + rest)
            hit = thumbnail_path(self.registry(), 'image', aid,
                                 self.project_root)
            if hit is None:
                # 表里查不到这个写法 → 照**脚本里的 `image` 声明**认（与演出预览
                # 同一条路，见 `app.stage.script_images`）。真实项目实测：
                # `bg bg11_0` 的别名只写在脚本里（`image bg bg11_0 = "…/bg11.png"`），
                # 不这么兜的话预览画得出来、卡片上却是一片空白。
                from storygraph.app.stage import image_index, resolve_stage_asset
                hit = resolve_stage_asset(spec, images, self.project_root,
                                          image_index(self.project_root))
            return hit
        except Exception:                    # noqa: BLE001
            return None

    def health(self, limit: int = 30) -> dict:
        """项目体检（**只读，不写盘**）。返回 {'issues': [...], 'total': n}。"""
        if not self.project_root:
            return {'issues': [], 'total': 0,
                    'error': '还没有打开项目'}
        from storygraph.pipeline.build import collect_report
        r = collect_report(self.project_root)
        issues = list(getattr(r, 'issues', []) or [])
        return {
            'issues': [str(getattr(i, 'message', i)) for i in issues[:limit]],
            'total': len(issues),
        }

    # ------------------------------------------------------------------
    #  模块的**剧情变量**（技术代码不进这个视图）
    # ------------------------------------------------------------------

    def node_variables(self, graph, nid) -> dict:
        """
        一个模块里**给策划看的剧情变量**（技术代码一条都不返回）。

        用户 2026-10-03：

        > 这个所谓的"技术代码"，它不是变量，是具体的实现细节！……
        > 我不要再在变量的窗口上看到任何内部技术代码的名字！编辑变量是为了
        > 让策划和编剧调整和编排剧情分支用的地方！不是拿来填细节的！！！

        返回 `{ok, label, variables, tech_count, file, line}`；
        `tech_count` 只用于**说明"这个块还有 N 条实现细节，不在这里编辑"**，
        连它的名字都不给前端 —— 给了就一定会被显示出来。
        """
        node = (getattr(graph, 'nodes', {}) or {}).get(nid)
        if node is None:
            return {'ok': False, 'label': '', 'variables': [], 'tech_count': 0,
                    'file': '', 'line': None}
        from storygraph.story import story_ops
        return {
            'ok': True,
            'label': getattr(node, 'label_name', '') or '',
            'variables': list(story_ops.narrative_operations(node)),
            'tech_count': len(story_ops.tech_operations(node)),
            'file': str(getattr(node, 'file_path', '') or ''),
            'line': getattr(node, 'line_number', None),
        }

    def set_node_variables(self, graph, nid, variables) -> dict:
        """
        写回**只改剧情变量**；技术代码**原位保留**。

        见 `story_ops.merge_narrative_operations`：直接把整份 operations
        换成编辑器内容的话，"改一个 love += 1"会顺手把 `renpy.transition(...)`
        全删掉（用户看不见它们，也就不会发现自己删了东西）。

        迁移（2026-10-05）：走**结构化**合并（`merge_narrative_statements`）——
        技术代码那几条是原对象带回去的，`source`（文件/行号）与 `condition`
        不会因为"变量编辑器保存一次"就丢。
        """
        node = (getattr(graph, 'nodes', {}) or {}).get(nid)
        if node is None:
            return {'ok': False, 'error': '这个模块不在了', 'variables': 0,
                    'kept_tech': 0}
        from storygraph.story import story_ops
        original = list(getattr(node, 'statements', []) or [])
        if original:
            node.statements = story_ops.merge_narrative_statements(
                original, variables)
        else:                                 # 老路径（纯字符串构造出来的节点）
            merged = story_ops.merge_narrative_operations(
                list(getattr(node, 'operations', []) or []), variables)
            node.set_operations(merged)
        merged_ops = list(node.operations)
        return {
            'ok': True, 'error': '',
            'variables': len(story_ops.narrative_operations(merged_ops)),
            'kept_tech': len(story_ops.tech_operations(merged_ops)),
        }

    def check_variable_syntax(self, lines) -> list:
        """
        「语法检查」（旧版变量对话框里就有这个按钮）：逐行喂给区间分析器，
        把抛异常的行挑出来返回 `['第2行错误: xxx - ...']`（空列表 = 全过）。

        **只校验，不改任何数据** —— 变量写错一个符号就会让整章的区间分析
        跑偏，在写回之前先说一声比事后在图上看到一片红好。
        """
        from storygraph.graph.analysis import VariableState, apply_operation
        errors = []
        state = VariableState(['test'])
        for i, line in enumerate(list(lines or []), 1):
            text = str(line).strip()
            if not text:
                continue
            try:
                state = apply_operation(state, text)
            except Exception as e:                # noqa: BLE001
                errors.append(f'第{i}行错误: {text} - {e}')
        return errors

    # ------------------------------------------------------------------
    #  剧情图的**范围**：只放用户导入过的章节
    # ------------------------------------------------------------------

    def import_chapters(self, files, graph=None) -> dict:
        """
        把用户选中的章节 `.rpy` 解析进剧情图（**累加**，不动没选中的章节）。

        用户 2026-10-03 的原话（这条语义就是这么定的）：

        > 我没有叫你所有的文件全部导进剧情图内！……我是让你让用户自己导入
        > 章节！所谓的导入项目只是为了表格的 xlsx 的资源管理系统！
        > 不是把整个项目都排在剧情图上面！！！剧情图上只有用户自己导入的
        > 章节排版！！！跟条件设置！

        所以：
          · 图里**只**出现用户导入过的文件里的 label（以及它们之间的
            jump / call / 菜单选项与条件）；
          · 再导入一批**累加**，不覆盖上一批（`clear_graph=False`，
            后端 `GraphBuilder._prepare` 只在 clear 时才删节点）；
          · 同一个文件重复导入不会长重复节点（边按
            `(from,to,type,condition,option_text)` 去重）。

        顺带把脚本里的 `define e = Character(...)` 合并进资源表 —— 说话人
        候选就是这么来的（这一步原来在"重新扫描项目"里，而那个动作现在
        只管资源表，不能再顺手碰图）。
        """
        paths = []
        for fp in (files or ()):
            p = Path(fp)
            if p.exists() and p.suffix.lower() == '.rpy':
                paths.append(p)
        if not paths:
            return {'ok': False, 'error': '没有选中任何 .rpy 文件',
                    'graph': graph, 'added_nodes': 0, 'added_edges': 0,
                    'nodes': len(getattr(graph, 'nodes', {}) or {}),
                    'edges': len(getattr(graph, 'edges', {}) or {}),
                    'files': [], 'warnings': [], 'errors': []}

        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.graph.models import Graph

        if graph is None:
            graph = Graph()
        # "这张图是控制流图还是章节图" —— 按**导入内容**判定（不看名字）
        batch_control_only = files_look_like_control_flow(paths)
        before_nodes = len(graph.nodes or {})
        before_edges = len(graph.edges or {})

        analyzer = IncrementalRenPyAnalyzer()
        try:
            parse_result, graph = analyzer.analyze(
                files=paths, graph=graph, clear_graph=False)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'graph': graph,
                    'added_nodes': 0, 'added_edges': 0,
                    'nodes': before_nodes, 'edges': before_edges,
                    'files': [str(p) for p in paths],
                    'warnings': [], 'errors': []}

        # 这张图的性质（控制流图 / 章节图）：**累加式**判定 ——
        # 只有"到目前为止导入的每一批都是控制流脚本"才算控制流图。
        # 于是"先导章节、再补 controller"= 章节图（导出时按章节补 _control）；
        # "只导 controller"= 控制流图（导出时原样重建）。名字不参与判断。
        if before_nodes == 0:
            graph.control_flow = bool(batch_control_only)
        else:
            graph.control_flow = (bool(getattr(graph, 'control_flow', False))
                                  and bool(batch_control_only))

        reg = self.registry()
        if reg is not None:
            try:
                from storygraph.story.beat_build import apply_script_definitions
                apply_script_definitions(reg, parse_result)
            except Exception:                 # noqa: BLE001
                swallow('editor.import_chapters')
        self.refresh_symbols()

        return {
            'ok': True, 'error': '',
            'graph': graph,
            'control_flow': bool(getattr(graph, 'control_flow', False)),
            'files': [str(p) for p in paths],
            'added_nodes': max(0, len(graph.nodes or {}) - before_nodes),
            'added_edges': max(0, len(graph.edges or {}) - before_edges),
            'nodes': len(graph.nodes or {}),
            'edges': len(graph.edges or {}),
            'warnings': [str(w) for w in (parse_result.warnings or [])],
            'errors': [str(e) for e in (parse_result.errors or [])],
        }

    def graph_files(self, graph) -> list:
        """图上已经导入了哪些文件（去重、排序）。"""
        seen = set()
        for node in (getattr(graph, 'nodes', {}) or {}).values():
            fp = getattr(node, 'file_path', None)
            if fp:
                seen.add(str(Path(str(fp))))
        return sorted(seen)

    def drop_chapters(self, graph, files) -> dict:
        """
        把某些章节**从剧情图上移除**（只改内存里的图，不动磁盘上的 .rpy）。

        按**文件**整章移除：用户心智里"这一章"就是一个 .rpy，
        逐个 label 删会留下半章残骸和悬空连线。
        """
        if graph is None:
            return {'removed_nodes': 0, 'nodes': 0, 'edges': 0}
        wanted = set()
        for fp in (files or ()):
            try:
                wanted.add(str(Path(str(fp))))
            except Exception:                 # noqa: BLE001
                continue
        doomed = [nid for nid, node in (graph.nodes or {}).items()
                  if str(getattr(node, 'file_path', '') or '') in wanted]
        for nid in doomed:
            graph.remove_node(nid)            # 连同它的连线一起删
        return {'removed_nodes': len(doomed),
                'nodes': len(graph.nodes or {}),
                'edges': len(graph.edges or {})}

    # ------------------------------------------------------------------
    #  结构分析（变量区间 / 可达性）
    # ------------------------------------------------------------------

    def analyze_graph_problems(self, graph) -> dict:
        """
        结构分析（变量区间 + 可达性），**同时**给人看的文本与给机器看的节点集合。

        多返回 `stranded_ids` / `independent_ids` / `entry_id` 的原因：
        前端以前只能拿到一段文字，于是"哪些块有问题"只能靠人眼在文字里找，
        图上什么都不会变。静态分析的结论必须能**画回图上**（标红/定位），
        否则它就只是"另一个要点开的对话框"。

        `independent` 是 label 名、`stranded` 是节点 id —— 这个口径差异
        以前散在各个前端里，拼错就把整个工程显示成一堆"不可达"。
        现在会话负责拼对一次（**按 label 名反查回节点 id**，同名节点全都算）。
        """
        blank = {
            'ok': False,
            'text': '（还没有可分析的图 —— 先用「➕ 导入章节 .rpy」加几章）',
            'summary': '还没有可分析的图 —— 先用「➕ 导入章节 .rpy」加几章。',
            'entry_id': None, 'entry_name': '',
            'stranded_ids': [], 'independent_ids': [], 'issue_ids': [],
            'dead_end_ids': [], 'problem_ids': [],
            'warnings': [], 'notes': [],
            'counts': {'nodes': 0, 'edges': 0, 'stranded': 0,
                       'dead_end': 0, 'independent': 0, 'issues': 0},
        }
        if graph is None or not getattr(graph, 'nodes', None):
            return blank

        from storygraph.graph.analysis import (analyze_reachability_full,
                                               reachable_from_all_roots,
                                               root_nodes,
                                               visualize_analysis_results)
        from storygraph.story import story_ops
        res = analyze_reachability_full(graph)

        by_name: dict = {}
        for nid, node in graph.nodes.items():
            by_name.setdefault(getattr(node, 'label_name', ''), []).append(nid)

        def ids_for(names):
            out = []
            for name in names or ():
                out.extend(by_name.get(name, ()))
            return out

        # —— 把"走不到"拆成两种，只有真断了才标红 ——
        #
        # 真实项目实测（`sr_general`：51 块 / 40 边，入口是权威的 `start`）：
        # 分析层把 40 个块全判成"不可达"，可它们绝大多数是各章节文件自己的
        # label（`chapter1_1`、`mini_game`…），由菜单 / `call screen` / 外部
        # 跳转进入，而解析器只认字面 jump/call/menu。40/51 全红 = 没标。
        #
        # 所以分三类：
        #   · 从**任何**入口都走不到（关在没有入口的环里）→ 真断了，标红；
        #   · 从别的入口可达，只是从这个入口走不到 → 独立入口，灰虚线，不算问题；
        #   · 分支条件恒假（下落风险）→ 真的死胡同，标红。
        root_ids = set(root_nodes(graph))
        any_root = reachable_from_all_roots(graph)
        unreached = ids_for(res.get('stranded'))
        stranded_ids = [nid for nid in unreached if nid not in any_root]
        independent_ids = (ids_for(res.get('independent'))
                           + [nid for nid in unreached if nid in any_root])
        dead_end_ids = ids_for([str(w).split('：', 1)[-1].split(' 的')[0].strip()
                                for w in (res.get('warnings') or [])
                                if '下落风险' in str(w)])

        # 告警按同一口径过滤：指向"独立入口"的那几条不再当问题报，
        # 否则右侧面板说"不可达"、图上却是灰虚线，两边自相矛盾。
        issue_lines, note_lines = self._split_analysis_warnings(
            res.get('warnings') or [], graph, stranded_ids, independent_ids)

        text = visualize_analysis_results(
            graph,
            res.get('states') or {},
            res.get('warnings') or [],
            entry_id=res.get('entry_id'),
            independent=res.get('independent') or [],
            independent_ids=independent_ids,
            # 变量的视图里**只出现剧情变量**（用户 2026-10-03：
            # "技术代码不是变量，是具体的实现细节"）
            operations_filter=story_ops.narrative_operations,
        )
        problem_ids = list(stranded_ids) + [nid for nid in dead_end_ids
                                            if nid not in stranded_ids]
        n_nodes = len(graph.nodes)
        n_edges = len(getattr(graph, 'edges', {}) or {})
        summary = (f'节点 {n_nodes} 个 / 连线 {n_edges} 条；'
                   f'分析入口「{res.get("entry_name") or "—"}」'
                   f'{"（权威）" if res.get("authoritative") else "（猜测，不断言不可达）"}；'
                   f'真断了 {len(stranded_ids)} 个 / '
                   f'死分支 {len(dead_end_ids)} 个 / '
                   f'其它入口可达 {len(independent_ids)} 个 / '
                   f'其它告警 {len(issue_lines)} 条')
        return {
            'ok': True,
            'text': (summary + '\n\n' + text),
            'summary': summary,
            'entry_id': res.get('entry_id'),
            'entry_name': res.get('entry_name') or '',
            'stranded_ids': stranded_ids,
            'dead_end_ids': dead_end_ids,
            'independent_ids': independent_ids,
            'problem_ids': problem_ids,
            'issue_ids': problem_ids,
            'warnings': issue_lines,
            'notes': note_lines,
            'counts': {'nodes': n_nodes, 'edges': n_edges,
                       'stranded': len(stranded_ids),
                       'dead_end': len(dead_end_ids),
                       'independent': len(independent_ids),
                       'issues': len(issue_lines)},
            'authoritative': bool(res.get('authoritative')),
        }

    @staticmethod
    def _split_analysis_warnings(warnings, graph, stranded_ids, independent_ids):
        """
        把分析层的告警文本按节点集合分成"真问题"与"说明"。

        分析层给的是一串**人读的字符串**（"🚫 不可达节点：X（…）"），
        没有结构化字段 —— 所以这里按前缀 + label 名反查分类。
        分不开的那几条（下落风险 / 迭代上限 / 入口提示）一律留在"真问题"里：
        宁可多报一条，也不要悄悄吞掉一个真的警告。
        """
        stranded_names = {getattr(graph.nodes.get(nid), 'label_name', '')
                          for nid in stranded_ids}
        independent_names = {getattr(graph.nodes.get(nid), 'label_name', '')
                             for nid in independent_ids}
        issues, notes = [], []
        for raw in warnings:
            w = str(raw)
            body = w.lstrip('🚫⚠️ℹ️ ').strip()
            name = ''
            if body.startswith('不可达节点：'):
                name = body[len('不可达节点：'):].split('（')[0].strip()
            if name and name in stranded_names:
                issues.append(w)
            elif name and name in independent_names:
                notes.append(f'🔌 独立入口 / 别处可达（不算问题）：{name}')
            elif body.startswith('未接入主线的独立入口'):
                notes.append(w)
            else:
                issues.append(w)
        return issues, notes

    def analyze_graph(self, graph) -> str:
        """变量区间 + 可达性分析，只要**人看的文本**（`analyze_graph_problems` 的薄壳）。"""
        return self.analyze_graph_problems(graph)['text']


def uniform_position_of(cards: list) -> str:
    """
    卡数组 → 整拍位置。**只有所有立绘位置一致才返回它**，否则留空。

    留空很关键：位置是"整拍"的属性，若不一致还硬写一个，
    会兜底套到别的立绘头上 → 立绘错位（实测踩过）。
    """
    rows = [{'at': (str(c.get('position') or '').strip()
                    or str(c.get('at') or '').strip())} for c in (cards or [])]
    return B.uniform_sprite_position(rows)


__all__ = ['EditorSession', 'BeatSnapshot', 'PRIMARY_FIELDS',
           'ADVANCED_FIELDS', 'uniform_position_of']
