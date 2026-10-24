"""
storygraph.story.chapter_tree
=============================
**整章树形真源**：章节 → 场景（label）→ 块（if / 循环 / 菜单 / 选项体 / 相机）
→ 拍。

为什么要有这一层（调研遗留项，2026-10-18）
------------------------------------------
`ChapterDocument` 已经收编了"章节级顶层块"，但拍列表仍然是**扁平**的：
删一张卡只是 `beats.pop()`。扁平模型回答不了"这一拍属于哪个块"，
于是：

  · 删掉 `menu:` 那张卡 → 选项拍原地变成裸旁白，块结构被拆散；
  · 在菜单头后面插一张卡 → 它落在菜单体里，但没有任何地方知道
    "这是菜单体"；
  · 删循环头 → 循环体掉到块外，从"执行 N 次"变成"执行一次"。

这些都不是"某一行写错了"，而是**结构没有名字**。所以这一层把结构显式
建出来，`ChapterDocument.beats` 退化成这棵树的**扁平投影**：

    `tree.flatten()`  ←→  `beats`（顺序、对象身份都逐一对上）

树只做三件事（刻意不做渲染/解析）：

  ① **建树**：按 `Beat.nest_path` 把拍挂到对应块下面（`None` = 老数据，
     留在场景层，不猜）；
  ② **结构编辑**：插入 / 删除 / 移动拍时，由树决定"新拍属于哪个块"，
     并且**拒绝**会拆散块的操作（删块头但有块体）；
  ③ **投影**：`flatten()` 还原成渲染器认识的扁平列表，**逐拍同一对象**。

边界
----
  · 菜单选项体的位置键（`A` / `B`）按**选项拍在菜单内的出现次序**算 ——
    与 `core.nest_path.option_key` 同一口径，空选项体也不会错位；
  · `nest_path is None` 的拍（老表 / 手工构造）**不进任何块**，也不被
    重新分配路径 —— "不知道"不许当成"在顶层"；
  · 块头（菜单 / 循环 / 相机 / 选项）有块体时**不允许单独删除**：
    返回 `has_children`，让调用方先删块体或明确要求删整块（本层暂不提供
    "删整块"，避免一次误点带走几十拍）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from storygraph.core.nest_path import (join_nest, option_index,
                                       option_key, split_nest)
from storygraph.story.beat_model import KIND_MENU, KIND_MENU_OPTION

__all__ = ['ChapterNode', 'ChapterTree', 'ChapterTreeError']


class ChapterTreeError(Exception):
    """结构编辑被拒绝（**不是**"工具崩了"，是"这一下会拆散块"）。"""

    def __init__(self, kind: str, message: str, count: int = 0):
        super().__init__(message)
        self.kind = kind
        self.count = int(count or 0)


@dataclass
class ChapterNode:
    """树上的一个节点（场景 / 块 / 拍）。"""

    #: `chapter` / `scene` / `beat` / `if` / `loop` / `camera` / `menu` /
    #: `option` / `block`
    kind: str
    #: 这一节点在 label 内的结构位（场景层是 `''`）
    path: str = ''
    #: 所属 label（场景节点上是自己的 label；其余节点继承）
    label: str = ''
    #: **块头拍**（`menu` / `loop` / `camera` / `option` 才有）
    header: object = None
    #: **叶子拍**（`beat` 才有）
    beat: object = None
    children: List['ChapterNode'] = field(default_factory=list)
    parent: Optional['ChapterNode'] = None

    # ---- 建树 / 遍历 ----
    def add(self, child: 'ChapterNode') -> 'ChapterNode':
        child.parent = self
        self.children.append(child)
        return child

    def walk(self):
        """先序遍历（含自己）。"""
        yield self
        for c in self.children:
            yield from c.walk()

    def count_beats(self) -> int:
        """这棵子树里有多少**拍**（含块头）。"""
        n = 1 if (self.header is not None or self.beat is not None) else 0
        return n + sum(c.count_beats() for c in self.children)

    def iter_beats(self):
        """按**显示顺序**吐出这一子树的拍（块头在前，块体在后）。"""
        if self.header is not None:
            yield self.header
        for c in self.children:
            yield from c.iter_beats()
        if self.beat is not None:
            yield self.beat

    def depth(self) -> int:
        d, cur = 0, self.parent
        while cur is not None:
            d += 1
            cur = cur.parent
        return d

    def describe(self) -> str:
        """人话摘要（日志 / 测试失败信息用）。"""
        name = {'scene': '场景', 'beat': '拍', 'if': 'if 块', 'loop': '循环',
                'camera': '相机块', 'menu': '菜单', 'option': '选项体',
                'block': '块', 'chapter': '章'}.get(self.kind, self.kind)
        head = ''
        if self.kind == 'scene':
            head = self.label
        elif self.header is not None:
            head = str(getattr(self.header, 'text', '')
                       or getattr(self.header, 'loop_header', '')
                       or getattr(self.header, 'camera_header', '')
                       or getattr(self.header, 'condition', ''))[:24]
        return f'{name}{("/" + head) if head else ""}'


def _is_menu_head(beat) -> bool:
    return str(getattr(beat, 'kind', '') or '') == KIND_MENU


def _is_loop_head(beat) -> bool:
    return (bool(str(getattr(beat, 'loop_header', '') or '').strip())
            and not str(getattr(beat, 'opaque', '') or '').strip())


def _is_camera_head(beat) -> bool:
    return (bool(str(getattr(beat, 'camera_header', '') or '').strip())
            and not str(getattr(beat, 'opaque', '') or '').strip())


def _seg_kind(seg: str) -> str:
    if seg.startswith('if:'):
        return 'if'
    if seg.startswith('loop:'):
        return 'loop'
    if seg.startswith('camera:'):
        return 'camera'
    if seg.startswith('menu:'):
        return 'menu'
    if option_index(seg):
        return 'option'
    return 'block'


class ChapterTree:
    """一章的结构树（见模块 docstring）。"""

    def __init__(self, file_blocks: str = ''):
        self.root = ChapterNode('chapter')
        self.file_blocks = str(file_blocks or '')
        #: `id(beat)` → 节点（只在树活着时有效；节点自己持有 beat）
        self._index: dict = {}

    # ------------------------------------------------------------------
    #  建树
    # ------------------------------------------------------------------
    @classmethod
    def build(cls, beats, file_blocks: str = '') -> 'ChapterTree':
        tree = cls(file_blocks=file_blocks)
        items = list(beats or ())
        i = 0
        while i < len(items):
            label = str(getattr(items[i], 'label', '') or '')
            j = i + 1
            while j < len(items) and str(getattr(items[j], 'label', '') or '') == label:
                j += 1
            scene = ChapterNode('scene', label=label)
            tree.root.add(scene)
            tree._fill_scene(scene, items[i:j])
            i = j
        tree._check_complete(items)
        return tree

    def _fill_scene(self, scene: ChapterNode, run: list) -> None:
        containers = {'': scene}
        for k, beat in enumerate(run):
            path = getattr(beat, 'nest_path', None)
            if path is None:
                # 老数据：不知道它在哪一层 → 留在场景层，绝不替它编路径。
                node = ChapterNode('beat', label=scene.label, beat=beat)
                scene.add(node)
                self._index[id(beat)] = node
                continue
            path = str(path)
            if _is_menu_head(beat):
                self._attach_header(scene, containers, run, k, beat, path, 'menu')
                continue
            if _is_loop_head(beat):
                self._attach_header(scene, containers, run, k, beat, path, 'loop')
                continue
            if _is_camera_head(beat):
                self._attach_header(scene, containers, run, k, beat, path, 'camera')
                continue
            if str(getattr(beat, 'kind', '') or '') == KIND_MENU_OPTION:
                menu = self._ensure(scene, containers, path)
                n = 1 + sum(1 for c in menu.children
                            if c.kind == 'option' and c.header is not None)
                body_path = join_nest(path, option_key(n))
                node = self._ensure(scene, containers, body_path)
                if node.header is not None and node.header is not beat:
                    # 路径复用（畸形数据）：另起一个同路径节点，保住顺序。
                    node = ChapterNode('option', path=body_path,
                                       label=scene.label)
                    menu.add(node)
                    containers[body_path] = node
                node.kind = 'option'
                node.header = beat
                node.label = scene.label
                self._index[id(beat)] = node
                continue
            node = self._ensure(scene, containers, path)
            leaf = ChapterNode('beat', path=path, label=scene.label, beat=beat)
            node.add(leaf)
            self._index[id(beat)] = leaf

    def _attach_header(self, scene, containers, run, k, beat, path, seg):
        """块头拍：找到它自己的块路径（编号在子孙路径里），挂成 header。"""
        block_seg = self._block_seg_from_descendants(run, k, path, seg)
        if not block_seg:
            # 空块（没有块体 / 老数据）→ 用不冲突的序号补一个，保住渲染顺序。
            n = 1
            taken = {c.path for c in (containers.get(path) or scene).children}
            while join_nest(path, f'{seg}:{n}') in taken:
                n += 1
            block_seg = f'{seg}:{n}'
        block_path = join_nest(path, block_seg)
        parent_path = path
        parent = self._ensure(scene, containers, parent_path) if parent_path else scene
        node = containers.get(block_path)
        if node is None or (node.header is not None and node.header is not beat):
            node = ChapterNode(seg, path=block_path, label=scene.label)
            parent.add(node)
            containers[block_path] = node
        node.kind = seg
        node.header = beat
        node.label = scene.label
        self._index[id(beat)] = node

    @staticmethod
    def _block_seg_from_descendants(run, k, path, seg) -> str:
        """从块体的 `nest_path` 里读块编号：`<path><seg>:n`。"""
        want = join_nest(path, f'{seg}:')
        for b in run[k + 1:]:
            q = getattr(b, 'nest_path', None)
            if q is None:
                continue
            q = str(q)
            if q.startswith(want):
                token = q[len(want):].split('>', 1)[0]
                if token.isdigit():
                    return f'{seg}:{token}'
        return ''

    def _ensure(self, scene: ChapterNode, containers: dict,
                path: str) -> ChapterNode:
        """确保 `path` 这条链上的节点都存在，返回最深那个。"""
        path = str(path or '')
        if path in containers:
            return containers[path]
        parent = scene
        cur = ''
        for seg in split_nest(path):
            cur = join_nest(cur, seg)
            node = containers.get(cur)
            if node is None:
                node = ChapterNode(_seg_kind(seg), path=cur, label=scene.label)
                parent.add(node)
                containers[cur] = node
            parent = node
        return parent

    def _check_complete(self, items: list) -> None:
        flat = self.flatten()
        if len(flat) != len(items):
            raise ChapterTreeError(
                'lost', f'建树后拍数不一致：{len(items)} → {len(flat)}')
        for i, (a, b) in enumerate(zip(items, flat)):
            if a is not b:
                raise ChapterTreeError(
                    'order', f'建树改变了第 {i + 1} 拍的顺序/对象')

    # ------------------------------------------------------------------
    #  投影
    # ------------------------------------------------------------------
    def flatten(self) -> list:
        """按显示顺序还原成扁平拍列表（**同一批对象**，不是副本）。"""
        out: list = []
        for scene in self.root.children:
            for beat in scene.iter_beats():
                out.append(beat)
        return out

    def matches(self, beats) -> bool:
        """这棵树现在是否正好投影成 `beats`（顺序 + 对象身份）。"""
        items = list(beats or ())
        flat = self.flatten()
        return (len(flat) == len(items)
                and all(a is b for a, b in zip(flat, items)))

    def node_of(self, beat) -> Optional[ChapterNode]:
        return self._index.get(id(beat))

    def scene_of(self, beat) -> Optional[ChapterNode]:
        node = self.node_of(beat)
        while node is not None and node.kind != 'scene':
            node = node.parent
        return node

    def scenes(self) -> list:
        return [c for c in self.root.children if c.kind == 'scene']

    def blocks(self) -> list:
        """所有块节点（不含场景 / 叶子）。"""
        return [n for n in self.root.walk()
                if n.kind in ('if', 'loop', 'camera', 'menu', 'option', 'block')]

    # ------------------------------------------------------------------
    #  结构编辑
    # ------------------------------------------------------------------
    def insert_beat(self, beat, at: int, inherit: bool = True,
                    retarget: bool = False, before: bool = False) -> int:
        """
        在扁平位置 `at` 插入一拍，返回实际位置。

        落点规则（**看邻拍，不看全局**）：

          · `after`（默认）—— `at > 0` 参考**前一张**拍（插在它后面）；
            `at == 0` 参考第一张拍（插在它前面）；
          · `before=True` —— 参考**第 `at` 张**拍，插在它前面（同容器）。

        `before` 是 2026-10-24 补的：原来只有"前一张 + after"一种落点，
        于是"插在章末 `return` 之前"会退化成"插进 `return` 前面的 `if`
        块体里"（`at-1` 是块体最后一拍）—— 线看起来接上了，实际只在
        条件成立时执行。章末接线必须能说"插在 return **前面**"。

        参考拍是块头时：`after` = 进它的块体（菜单/循环/选项体）；
        `before` = 与块头平级。参考拍是叶子时，新拍进同一个容器。
        """
        items = self.flatten()
        at = max(0, min(int(at), len(items)))
        if not items:
            scene = ChapterNode('scene',
                                label=str(getattr(beat, 'label', '') or ''))
            self.root.add(scene)
            node = ChapterNode('beat', beat=beat, label=scene.label)
            scene.add(node)
            self._index[id(beat)] = node
            return 0

        if before:
            parent, pos, body_path = self._slot_at(items, at, before=True)
        else:
            parent, pos, body_path = self._slot_at(items, at)
        if body_path is None:
            raise ChapterTreeError('bad_slot', '找不到插入落点')

        scene = self._scene_ancestor(parent)
        if scene is not None:
            # 跨场景：跟着参考拍走，别把一拍孤零零留在别的 label 里。
            beat.label = scene.label

        if inherit:
            if retarget or getattr(beat, 'nest_path', None) is None:
                beat.nest_path = body_path
            if parent.kind == 'if':
                cond = self._first_condition(parent)
                if retarget or not str(getattr(beat, 'condition', '') or '').strip():
                    beat.condition = cond
            elif retarget:
                beat.condition = ''

        node = ChapterNode('beat', path=body_path, label=beat.label, beat=beat)
        parent.children.insert(pos, node)
        node.parent = parent
        self._index[id(beat)] = node
        return self._flat_index(beat)

    def _slot_before(self, ref_node: ChapterNode):
        """参考节点 → `(父节点, 插入位置, 新拍的结构位)`：插在它**前面**。"""
        parent = ref_node.parent
        if parent is None:
            return ref_node, 0, self._body_path(ref_node)
        return parent, parent.children.index(ref_node), self._body_path(parent)

    def _flat_index(self, beat) -> int:
        """按**对象身份**找扁平下标（`Beat` 的 `==` 比字段，不能用 list.index）。"""
        for i, b in enumerate(self.flatten()):
            if b is beat:
                return i
        raise ChapterTreeError('missing', '这一拍不在树上')

    def _slot_for(self, ref_node: ChapterNode, after: bool):
        """参考节点 → `(父节点, 插入位置, 新拍的结构位)`。"""
        if ref_node.kind == 'beat':
            parent = ref_node.parent
            pos = parent.children.index(ref_node) + (1 if after else 0)
            return parent, pos, self._body_path(parent)
        if ref_node.kind in ('menu', 'loop', 'camera', 'option'):
            if after:
                # 块头后面 = 进块体（菜单→菜单体，循环→循环体，选项→选项体）
                return ref_node, 0, ref_node.path
            parent = ref_node.parent
            pos = parent.children.index(ref_node)
            return parent, pos, self._body_path(parent)
        # scene / chapter：直接追加
        return ref_node, len(ref_node.children), self._body_path(ref_node)

    @staticmethod
    def _body_path(parent: ChapterNode) -> str:
        if parent is None or parent.kind in ('chapter', 'scene'):
            return ''
        return parent.path

    @staticmethod
    def _scene_ancestor(node: Optional[ChapterNode]) -> Optional[ChapterNode]:
        while node is not None and node.kind != 'scene':
            node = node.parent
        return node

    @staticmethod
    def _first_condition(node: ChapterNode) -> str:
        for b in node.iter_beats():
            cond = str(getattr(b, 'condition', '') or '')
            if cond:
                return cond
        return ''

    def remove_beat(self, target, cascade: bool = False) -> object:
        """
        删掉一拍（`target` = 拍对象或扁平下标），返回被删的拍。

        块头（菜单 / 循环 / 相机 / 选项）带着块体时**默认拒绝**：
        `ChapterTreeError(kind='has_children', count=块体拍数)`。
        删块头会把选项拍/循环体变成"块外的普通拍"——那是静默改剧情，
        比报错坏得多。

        `cascade=True`：明确要"删整块" → 连同整棵子树一起删，返回块头拍
        （整批拍见 `remove_subtree`）。
        """
        if isinstance(target, int):
            items = self.flatten()
            if not 0 <= target < len(items):
                raise ChapterTreeError('missing', f'第 {target + 1} 拍不存在')
            beat = items[target]
        else:
            beat = target
        node = self._index.get(id(beat))
        if node is None or node.beat is not beat and node.header is not beat:
            raise ChapterTreeError('missing', '这一拍不在树上（可能已经被删过）')

        if node.kind == 'beat':
            beat_obj = node.beat
            parent = node.parent
            parent.children.remove(node)
            node.parent = None
            self._index.pop(id(beat_obj), None)
            self._prune(parent)
            return beat_obj

        # 块头
        count = node.count_beats() - 1
        if node.children:
            if not cascade:
                raise ChapterTreeError(
                    'has_children',
                    f'这是「{node.describe()}」的开头，下面还有 {count} 拍。'
                    f'删掉它会把块体变成块外的普通拍（静默改剧情）——'
                    f'请先删块体里的拍。',
                    count=count)
            return self.remove_subtree(beat)[0]

        parent = node.parent
        parent.children.remove(node)
        node.parent = None
        header = node.header
        self._index.pop(id(header), None)
        self._prune(parent)
        return header

    def remove_subtree(self, target) -> list:
        """删掉一个**块头连同整棵子树**，返回按显示顺序的整批拍。"""
        beat = target
        if isinstance(target, int):
            items = self.flatten()
            if not 0 <= target < len(items):
                raise ChapterTreeError('missing', f'第 {target + 1} 拍不存在')
            beat = items[target]
        node = self._index.get(id(beat))
        if node is None:
            raise ChapterTreeError('missing', '这一拍不在树上')
        if node.kind == 'beat':
            return [self.remove_beat(node.beat)]
        removed = list(node.iter_beats())
        parent = node.parent
        parent.children.remove(node)
        node.parent = None
        for b in removed:
            self._index.pop(id(b), None)
        self._prune(parent)
        return removed

    def _prune(self, node: Optional[ChapterNode]) -> None:
        """清掉空的中间节点（`if` 块空了就消失；场景永远保留）。"""
        while (node is not None and node.kind not in ('scene', 'chapter')
               and not node.children and node.header is None):
            parent = node.parent
            if parent is None:
                return
            parent.children.remove(node)
            node.parent = None
            node = parent

    def _slot_at(self, items: list, at: int, before: bool = False):
        """
        扁平位置 `at` → `(父节点, 插入位置, 新拍的结构位)`。

        从 `insert_beat` 里**原样搬出**（2026-10-24，审计第三轮 P0-11）：移动
        也要先问"落点在哪"，才能在没有真正插进去之前判断"会不会落到 return
        后面"。两处必须同一套判据，所以只留这一份实现。
        """
        at = max(0, min(int(at), len(items)))
        if before:
            if at >= len(items):
                return self._slot_for(self._index[id(items[-1])], True)
            return self._slot_before(self._index[id(items[at])])
        if at > 0:
            ref, after = items[at - 1], True
        else:
            ref, after = items[0], False
        return self._slot_for(self._index[id(ref)], after)

    def _guard_return(self, beat, parent: ChapterNode, pos: int) -> None:
        """
        "把一拍移到 `return` 后面 / 把 `return` 挪到中间"→ 拒绝（P0-11）。

        为什么必须拒绝（2026-10-24，审计第三轮）：`return` 是**一拍的邻居**，
        不是一条看不见的边界。审计实测 `move(0, 3)`：

            e "二"
            e "三"
            return
            e "一"        ← 永远不会执行（静默变死代码）

        `move(2, 0)` 反一反：整章在第一行就结束，后面两句话直接没了。两次都
        `ok=True`、无警告 —— "成功了但剧情断了"，是这一层最不该发生的事。

        判据（只看**新的兄弟位置**，不跨块猜）：

          · 挪动的是 `return` 一拍 → 它必须是这一段的**最后**一条；
          · 挪动的是别的拍 → 它不能落在某个 `return` 之后。

        合法的形态照旧放行：`return` 在某条分支体里（`if c: … return`）是那一段
        的最后一条；把拍移到**另一个**块里不受影响（兄弟列表是新的那一段）。
        """
        # 判据与渲染层**同一份实现**（`raw` / `opaque` 里的 `return`）——
        # 各写一份正是"两处对不上"的来源；而且 `beat_render` 很重，
        # 这里按需导入（`chapter_tree` 是纯结构层，不该把它拖进来）。
        from storygraph.story.beat_render import _is_return_beat

        siblings = list(parent.children)
        pos = max(0, min(int(pos), len(siblings)))
        if _is_return_beat(beat):
            if pos < len(siblings):
                raise ChapterTreeError(
                    'after_return',
                    '`return` 是一章的出口，不能挪到它后面还有内容的位置 —— '
                    '那样后面的内容永远不会执行。请把它留在这一段最后。')
            return
        for node in siblings[:pos]:
            if node.beat is not None and _is_return_beat(node.beat):
                raise ChapterTreeError(
                    'after_return',
                    '不能把一拍移到 `return` 之后：那里的内容永远不会执行'
                    '（整章已经结束）。要加内容请放在 `return` 前面。')

    def move_beat(self, src: int, dst: int) -> int:
        """
        把第 `src` 拍移到扁平位置 `dst`（移动后的下标），返回新下标。

        只允许移动**叶子拍**（块头带着块体，搬走就等于拆块）；落点跨块时
        会按落点所在容器**重写结构位**（`nest_path`），不会留下"路径还对
        应旧块"的拍。

        **不许越过 `return`**（2026-10-24，审计第三轮 P0-11）：落点在某条
        `return` 之后 → `ChapterTreeError('after_return')`；`return` 自己
        被挪到中间同样拒绝。见 `_guard_return`。
        """
        items = self.flatten()
        src = int(src)
        if not 0 <= src < len(items):
            raise ChapterTreeError('missing', f'第 {src + 1} 拍不存在')
        beat = items[src]
        node = self._index.get(id(beat))
        if node is None:
            raise ChapterTreeError('missing', '这一拍不在树上')
        if node.kind != 'beat':
            raise ChapterTreeError(
                'header', f'「{node.describe()}」的块头不能单独移动（会拆块）')
        snap = (getattr(beat, 'nest_path', None),
                getattr(beat, 'condition', ''),
                getattr(beat, 'label', ''))
        self.remove_beat(beat)
        remaining = self.flatten()
        dst = max(0, min(int(dst), len(remaining)))
        try:
            parent, pos, _body = self._slot_at(remaining, dst)
            self._guard_return(beat, parent, pos)
        except ChapterTreeError:
            # 守卫拒绝 → 把树恢复原状（"不猜"不能留下半截结构）。
            self.insert_beat(beat, src, inherit=True, retarget=True)
            (beat.nest_path, beat.condition, beat.label) = snap
            raise
        return self.insert_beat(beat, dst, inherit=True, retarget=True)

    # ------------------------------------------------------------------
    #  调试 / 报告
    # ------------------------------------------------------------------
    def outline(self, max_depth: int = 4) -> list:
        """树的人话大纲（每行一个节点，带缩进）。"""
        rows: list = []

        def walk(node, depth):
            if depth > max_depth:
                return
            if node.kind != 'chapter':
                rows.append('  ' * depth + node.describe()
                            + (f' [{node.path}]' if node.path else ''))
            for c in node.children:
                walk(c, depth + 1)

        walk(self.root, 0)
        return rows

    def validate(self) -> list:
        """自检：返回问题列表（空 = 成立）。给测试与调试用。"""
        problems: list = []
        seen: dict = {}
        for node in self.root.walk():
            if node.kind == 'chapter':
                continue
            if node.parent is None:
                problems.append(f'孤儿节点：{node.describe()}')
            for beat in (node.header, node.beat):
                if beat is None:
                    continue
                if id(beat) in seen:
                    problems.append(f'同一拍出现在两处：{beat!r}')
                seen[id(beat)] = node
            if node.header is not None and node.kind in ('beat', 'scene', 'if', 'block'):
                problems.append(f'{node.kind} 节点不该有 header：{node.describe()}')
            if node.beat is not None and node.kind != 'beat':
                problems.append(f'{node.kind} 节点不该有 beat：{node.describe()}')
        for key, node in self._index.items():
            beat = node.beat if node.beat is not None else node.header
            if beat is None or id(beat) != key:
                problems.append(f'索引里的拍不在节点上：{key!r}')
        return problems
