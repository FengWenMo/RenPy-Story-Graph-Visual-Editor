# layout.py
"""
剧情图的**分层布局**。

本来是一个 358 行、圈复杂度 147 的函数（全项目第一，第二 111）——
`layout_graph` 里塞着 7 个闭包加一整段主流程。2026-10-05（P3-12）
把"有名字、有明确输入输出"的那几段提成**模块级私有函数**：
它们本来就是以 `def` 写在里面的，说明原作者也想分开，只是没搬出去。

拆分原则是**只搬位置**：
  · 参数全部显式传入（原来靠闭包捕获的也传进去），不引入新的全局状态；
  · 不加、不减、不重排任何一步 —— 这个函数决定剧情图的排版，
    `tests/test_layout*.py` 与主窗口快照都盯着它。

`layout_graph` 只负责**串流程**：摘自环 → 破环 → 修成 DAG → 分层 →
插虚拟节点 → 排序 → 减少交叉 → 落坐标。

顺带修掉一个**既有死循环**（见 `_ensure_dag`）：病态图上它会永不返回，
而它在 UI 线程上被调用 —— 后果是窗口整块卡死。
"""
from collections import deque, defaultdict
import bisect


#: 虚拟节点的前缀（跨层边会被拆成一串虚拟节点）。
VIRTUAL_PREFIX = '__virt_'


def _is_virtual(node_id) -> bool:
    return isinstance(node_id, str) and node_id.startswith(VIRTUAL_PREFIX)


def _edge_count(dag_adj) -> int:
    return sum(len(v) for v in dag_adj.values())


def _break_cycles(adj):
    """
    把有向图里检测到的环**逐条反向**，返回被反向的边集合。

    先做一遍 Kahn 拓扑；剩下的就是环里的节点，每次挑出度最小的那个、
    反它一条出边，再继续拓扑。
    """
    indeg = {u: 0 for u in adj}
    for u in adj:
        for v in adj[u]:
            indeg[v] = indeg.get(v, 0) + 1

    reversed_edges = set()
    q = deque([u for u in indeg if indeg[u] == 0])
    processed = set()

    while q:
        u = q.popleft()
        processed.add(u)
        for v in adj.get(u, []):
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)

    remaining = [u for u in adj if u not in processed]
    while remaining:
        u = min(remaining, key=lambda x: len(adj.get(x, [])))
        candidates = [v for v in adj.get(u, []) if v in remaining]
        if not candidates:
            candidates = adj.get(u, [])
        v = candidates[0] if candidates else None
        if v is None:
            break
        reversed_edges.add((u, v))
        adj[u].remove(v)
        adj.setdefault(v, []).append(u)
        indeg[v] += 1
        indeg[u] -= 1
        if indeg[u] == 0:
            q.append(u)
        while q:
            node = q.popleft()
            processed.add(node)
            for nb in adj.get(node, []):
                indeg[nb] -= 1
                if indeg[nb] == 0:
                    q.append(nb)
        remaining = [x for x in adj if x not in processed]

    return reversed_edges


def _ensure_dag(dag_adj, nodes):
    """
    把 `dag_adj` 里**剩下的**环继续反边，直到它真的是 DAG。

    ⚠ **必须封顶**（2026-10-05 修，P3-12 顺带发现的真 bug）
    ------------------------------------------------------
    原实现是 `while True:` —— **正常输入上没问题，病态输入上会死循环**：
    反一条边（`u→v` 改成 `v→u`）有可能**造出新的环**，下一轮再反另一条，
    如此往复。实测一个 25 节点 / 50 边的随机图就能让它**几分钟不返回**
    （HEAD 版本同样如此 → 这是既有 bug，不是拆分引入的）。

    后果不是"布局难看"，是**界面整块卡死**：`layout_graph` 在 UI 线程上
    被调用，它不返回，窗口就不响应。而且它发生在 `_ensure_dag` 里 ——
    连分层都还没走到。真实验收里 51 个块的图 0.11 秒就跑完了，
    所以这个坑一直没被踩到；换一张环多一点的剧情图就会踩上。

    为什么用"预算"而不是"换个算法"：换算法会改变**现有正常图**的布局，
    而布局是用户盯着的东西（`tests/test_layout*`、主窗口快照）。
    预算的边界很干净：**能正常跑完的图一次都不会碰到它**
    （正常规模只翻十几次边，预算给的是 `边数*4+64`）。
    只有病态图会被截断，那时布局**仍然可用**（可能残留一个环 →
    后续分层把它当普通边处理），远好过卡死几分钟。
    """
    budget = _edge_count(dag_adj) * 4 + 64
    while budget > 0:
        budget -= 1
        indeg = {u: 0 for u in nodes}
        for u in nodes:
            for v in dag_adj[u]:
                indeg[v] += 1
        q = deque([u for u in nodes if indeg[u] == 0])
        processed = set()
        while q:
            u = q.popleft()
            processed.add(u)
            for v in dag_adj[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    q.append(v)
        if len(processed) == len(nodes):
            break
        remaining = [u for u in nodes if u not in processed]
        cycle_edge = None
        # ⚠ **必须按确定顺序遍历**（2026-10-05 修，P3-12 顺带发现的第二个真 bug）
        # ---------------------------------------------------------------
        # `dag_adj[u]` 是 **set**，而 set 的迭代顺序取决于字符串哈希 ——
        # 也就是取决于 `PYTHONHASHSEED`，**每个进程都不一样**。
        # 原实现直接 `for v in dag_adj[u]` 取"第一条环内边"，
        # 于是**同一张图在不同次启动里反的是不同的边**，
        # 布局跟着变。实测（`tools/_hashseed_demo.py`）：
        #
        #     同一张 20 节点 / 24 边的图，6 个 hash seed 下**4 种坐标和**
        #
        # 用户侧的表现是"剧情图每次打开都长得不一样" ——
        # 这比"排得不够好"更让人不安（会怀疑自己是不是动过什么）。
        # 按 `str` 排序即可：与 hash seed 无关，且对同一张图恒定。
        for u in remaining:
            for v in sorted(dag_adj[u], key=str):
                if v in remaining:
                    cycle_edge = (u, v)
                    break
            if cycle_edge:
                break
        if not cycle_edge:
            break
        u, v = cycle_edge
        dag_adj[u].discard(v)
        dag_adj[v].add(u)
    return dag_adj


def _assign_layers(dag_adj, nodes, start_id):
    """
    Kahn 拓扑定层：源点在第 0 层，其余取"前驱层号 + 1"的最大值。

    返回 `(layers, preds, sources, max_layer)`；`preds` 此时是**空壳**，
    由调用方按"边有没有被反向"填（那需要 `edges` 与 `reversed_edges`，
    这里拿不到）。孤立节点补在最后一层之后。

    `start_id` 给了就把它强制当源点 —— 用户指定的入口应该排在第一层，
    而不是因为它有入边被推到后面。
    """
    indeg = {u: 0 for u in nodes}
    for u in nodes:
        for v in dag_adj[u]:
            indeg[v] += 1
    if start_id is not None and start_id in indeg:
        indeg[start_id] = 0

    sources = [u for u in nodes if indeg[u] == 0]
    q = deque(sources)
    layers = {u: 0 for u in sources}
    indeg2 = indeg.copy()
    while q:
        u = q.popleft()
        for v in dag_adj[u]:
            layers[v] = max(layers.get(v, 0), layers[u] + 1)
            indeg2[v] -= 1
            if indeg2[v] == 0:
                q.append(v)

    max_layer = max(layers.values()) if layers else 0
    for nid in nodes:
        if nid not in layers:
            layers[nid] = max_layer + 1
            max_layer = max(max_layer, layers[nid])

    preds = {u: set() for u in nodes}
    return layers, preds, sources, max_layer


def _pull_up_layers(layers, preds, dag_adj, nodes):
    """
    两轮"往上收"：一个节点如果**所有后继都更靠下**、且能移到
    "最深前驱 + 1"，就上移。

    两个条件必须同时成立 —— 只看 `new_layer < layers[u]`
    会把节点插到它的后继之间（边就倒过来了）。
    """
    for _ in range(2):
        changed = False
        for u in sorted(nodes, key=lambda x: layers[x]):
            if not preds[u]:
                new_layer = 0
            else:
                max_pred_layer = max(layers[p] for p in preds[u])
                new_layer = max_pred_layer + 1
            if new_layer < layers[u]:
                if all(layers[v] > new_layer for v in dag_adj[u]):
                    layers[u] = new_layer
                    changed = True
        if not changed:
            break


def _insert_virtual_nodes(edges, layers):
    """
    跨多层的边拆成一串**虚拟节点**（每层一个），让"长边"也能被排序算法
    当成相邻关系处理，交叉数才会下降。

    返回 `(virtual_info, layer_nodes_ext, edge_segments, max_layer)`。
    """
    max_layer = max(layers.values()) if layers else 0

    virtual_info = {}
    layer_nodes_ext = {lv: [] for lv in range(max_layer + 1)}
    for nid, lv in layers.items():
        layer_nodes_ext[lv].append(nid)

    edge_segments = []
    for u, v, eid in edges:
        lu, lv = layers[u], layers[v]
        if lu == lv:
            continue
        low_node, high_node = (u, v) if lu < lv else (v, u)
        low_layer, high_layer = sorted((lu, lv))
        if high_layer - low_layer == 1:
            edge_segments.append((low_node, high_node, eid))
        else:
            prev = low_node
            for layer in range(low_layer + 1, high_layer):
                vid = f"{VIRTUAL_PREFIX}{eid}_{layer}"
                virtual_info[vid] = eid
                layer_nodes_ext[layer].append(vid)
                edge_segments.append((prev, vid, eid))
                prev = vid
            edge_segments.append((prev, high_node, eid))

    return virtual_info, layer_nodes_ext, edge_segments, max_layer


def _build_initial_order(nodes, dag_adj, layers, sources, max_layer,
                         layer_nodes_ext):
    """
    初始层内顺序：**先按 BFS 顺序**（尊重图的走向），
    没被 BFS 覆盖到的按层号补进去，最后按 `str(id)` 排一下 ——
    这一步保证**确定性**（同样输入两次跑出的布局必须一样）。
    """
    order_ext = {lv: [] for lv in range(max_layer + 1)}
    visited_bfs = set()
    bfs_queue = deque(sources)
    while bfs_queue:
        u = bfs_queue.popleft()
        if u in visited_bfs:
            continue
        visited_bfs.add(u)
        order_ext[layers[u]].append(u)
        for v in dag_adj[u]:
            if v not in visited_bfs:
                bfs_queue.append(v)
    for nid, lv in layers.items():
        if nid not in visited_bfs:
            order_ext[lv].append(nid)
    for lv in range(max_layer + 1):
        for nid in layer_nodes_ext[lv]:
            if nid not in order_ext[lv]:
                order_ext[lv].append(nid)
    for lv in order_ext:
        order_ext[lv].sort(key=lambda x: str(x))
    return order_ext


def _count_crossings(order, edge_segments, max_layer):
    """
    相邻两层之间的**边交叉数**（用 `bisect` 数逆序对，O(n log n)）。
    """
    total = 0
    for lv in range(max_layer):
        left = order.get(lv, [])
        right = order.get(lv + 1, [])
        if not left or not right:
            continue
        pos_left = {nid: idx for idx, nid in enumerate(left)}
        pos_right = {nid: idx for idx, nid in enumerate(right)}
        groups = defaultdict(list)
        for a, b, _ in edge_segments:
            if a in pos_left and b in pos_right:
                groups[pos_left[a]].append(pos_right[b])
            elif b in pos_left and a in pos_right:
                groups[pos_left[b]].append(pos_right[a])
        sorted_seen = []
        inv = 0
        for li in sorted(groups.keys()):
            rights = groups[li]
            for r in rights:
                pos = bisect.bisect_right(sorted_seen, r)
                inv += len(sorted_seen) - pos
            for r in rights:
                bisect.insort(sorted_seen, r)
        total += inv
    return total


def _barycenter_once(order, max_layer, ext_neighbors):
    """
    一轮重心排序：先自下而上（看上一层）、再自上而下（看下一层）。

    虚拟节点权重算 0.5 —— 它只是"一条边被拉直"的中间点，
    不该和真节点等价地拉动顺序。

    ⚠ **邻居要按 id 排序**（2026-10-05 修，P3-12 顺带发现的第二个真 bug）
    -------------------------------------------------------------------
    `ext_neighbors` 是 `defaultdict(set)` —— **set 的迭代顺序取决于
    字符串哈希**，也就是取决于 `PYTHONHASHSEED`、**每个进程都不一样**。
    而下面算重心时：

        sorted(zip(weighted_vals, neighbors, weights))

    `sorted` 比的是**元组**，第一个元素 `weighted_vals` 相等（很常见 ——
    多个邻居位置相同、或权重都是 0.5）时，就退化成比较第二个元素
    `neighbors` 的顺序 —— 而那正是 set 的随机顺序。

    后果：**同一张图每次打开排得都不一样**。实测
    （`tools/_trace_layout.py`，逐步摘要）：前 8 个阶段在所有 hash seed
    下完全一致，**从 `bary_1` 起就分叉**，一路传到坐标。
    6 个 seed 给出 4 种坐标和（`tools/_hashseed_demo.py`）。

    按 `str(nid)` 排一遍即可：与哈希无关，对同一张图恒定。
    """
    new_order = {lv: list(order[lv]) for lv in order}

    def pass_dir(lv_range, ref_of):
        for lv in lv_range:
            ref = new_order.get(ref_of(lv), [])
            if not ref:
                continue
            pos_ref = {nid: idx for idx, nid in enumerate(ref)}
            bary = {}
            for nid in new_order.get(lv, []):
                neighbors = sorted((nb for nb in ext_neighbors[nid]
                                    if nb in pos_ref), key=str)
                if neighbors:
                    weights = [0.5 if _is_virtual(nb) else 1.0
                               for nb in neighbors]
                    total_weight = sum(weights)
                    weighted_vals = sorted(pos_ref[nb] * w
                                           for nb, w in zip(neighbors, weights))
                    if total_weight == 0:
                        bary[nid] = len(ref) / 2
                    else:
                        cum = 0
                        target = total_weight / 2
                        for val, _nb, w in sorted(zip(weighted_vals, neighbors,
                                                      weights)):
                            cum += w
                            if cum >= target:
                                bary[nid] = val / w if w != 0 else val
                                break
                        else:
                            bary[nid] = (weighted_vals[-1] / weights[-1]
                                         if weights[-1] != 0
                                         else weighted_vals[-1])
                else:
                    bary[nid] = len(ref) / 2
            new_order[lv] = sorted(
                new_order[lv],
                key=lambda n: (bary.get(n, len(ref) / 2), str(n)))

    pass_dir(range(1, max_layer + 1), lambda lv: lv - 1)
    pass_dir(range(max_layer - 1, -1, -1), lambda lv: lv + 1)
    return new_order


#: 降交叉的**工作量预算**（纯计数，确定性）。
#:
#: **必须是纯计数、不能带时间**（2026-10-05 教训）
#: -------------------------------------------------
#: 第一版封顶用的是 `time.monotonic()` 截止时间。功能上它确实挡住了卡死，
#: 但它让**布局变成不确定的**：同一张图，机器忙一点就多截断几次、
#: 层内排法就换一个。布局是用户盯着看的东西，反复重排不可接受 ——
#: 而这是实测出来的：把两道闸分别开到无穷去对数，**带时间闸时有 5 个
#: 图形与 HEAD 不一致，改用纯计数后全部一致**。
#:
#: 预算要足够大到正常图永远碰不到。实测
#: （`tools/_measure_budget.py`，把预算开到无穷量"真正需要多少"）：
#:
#:     真实规模 51/70      224 ~  316 次（0.03~0.06s）
#:     真实较密 51/140     244 ~  700 次（0.16~0.46s）
#:     大图   100/200     1076 ~ 2593 次（0.85~1.88s）
#:     20~40 节点量级       25 ~  198 次
#:
#: 取 20000：对最大的正常图（2593）还有近 8 倍余量，
#: 而病态图会被截断在几秒内。只影响"交叉数有没有降到最优"，
#: 不影响合法性 —— `order_ext` 在任何时刻都是完整的层内排列。
_REDUCE_BUDGET = 20000


def _reduce_crossings(order_ext, edge_segments, max_layer):
    """
    相邻层**暴力交换**降交叉：只接受能真正减少交叉数的交换。

    ⚠ **必须封顶**（2026-10-05，同 `_ensure_dag`）
    ---------------------------------------------
    它**必然**终止（交叉数严格下降且有下界 0），但**终止得很慢**：
    每接受一次交换，`while local_improved` 就从该层最左边**重扫一遍**，
    而这层几乎每次都能再找到一个可接受交换。

    病态图上它会从几秒滑到**几分钟** —— 而它在 UI 线程上被调用，
    所以后果是"点了没反应、窗口卡死"。
    """
    budget = _REDUCE_BUDGET + len(edge_segments)
    best_cross = _count_crossings(order_ext, edge_segments, max_layer)
    budget -= 1
    for _ in range(2):
        if budget <= 0:
            break
        improved_global = False
        for lv in range(max_layer + 1):
            layer = order_ext[lv]
            if len(layer) < 2:
                continue
            local_improved = True
            while local_improved and budget > 0:
                local_improved = False
                for i in range(len(layer) - 1):
                    if budget <= 0:
                        break
                    budget -= 1
                    layer[i], layer[i + 1] = layer[i + 1], layer[i]
                    new_cross = _count_crossings(order_ext, edge_segments,
                                                 max_layer)
                    if new_cross < best_cross:
                        best_cross = new_cross
                        local_improved = True
                        improved_global = True
                        break
                    else:
                        layer[i], layer[i + 1] = layer[i + 1], layer[i]
            if improved_global:
                break
        if not improved_global:
            break
    return order_ext


def layout_graph(graph, start_id=None, layer_gap=180, node_gap=60, max_iter=10):
    if not graph.nodes:
        return

    adj = {nid: [] for nid in graph.nodes}
    undirected_neighbors = {nid: set() for nid in graph.nodes}
    edges = []
    self_loops = []
    for eid, edge in graph.edges.items():
        u, v = edge.from_id, edge.to_id
        if u == v:
            self_loops.append((u, v))
            continue
        adj[u].append(v)
        undirected_neighbors[u].add(v)
        undirected_neighbors[v].add(u)
        edges.append((u, v, eid))

    reversed_edges = _break_cycles(adj)

    dag_adj = {nid: set() for nid in graph.nodes}
    for u, v, _ in edges:
        if (u, v) in reversed_edges:
            dag_adj[v].add(u)
        else:
            dag_adj[u].add(v)

    dag_adj = _ensure_dag(dag_adj, list(graph.nodes))

    layers, preds, sources, max_layer = _assign_layers(
        dag_adj, list(graph.nodes), start_id)

    for u, v, _ in edges:
        if (u, v) in reversed_edges:
            preds[u].add(v)
        else:
            preds[v].add(u)

    _pull_up_layers(layers, preds, dag_adj, list(graph.nodes))

    virtual_info, layer_nodes_ext, edge_segments, max_layer = \
        _insert_virtual_nodes(edges, layers)

    order_ext = _build_initial_order(
        list(graph.nodes), dag_adj, layers, sources, max_layer,
        layer_nodes_ext)

    ext_neighbors = defaultdict(set)
    for a, b, _ in edge_segments:
        ext_neighbors[a].add(b)
        ext_neighbors[b].add(a)

    for _ in range(max_iter):
        new_order = _barycenter_once(order_ext, max_layer, ext_neighbors)
        changed = any(order_ext[lv] != new_order.get(lv, [])
                      for lv in order_ext)
        order_ext = new_order
        if not changed:
            break

    order_ext = _reduce_crossings(order_ext, edge_segments, max_layer)

    coords = {}
    for lv, layer in order_ext.items():
        total_height = (len(layer) - 1) * node_gap
        start_y = -total_height / 2
        x = lv * layer_gap
        for i, nid in enumerate(layer):
            y = start_y + i * node_gap
            if nid in graph.nodes:
                coords[nid] = (x, y)

    for nid in graph.nodes:
        if nid not in coords:
            coords[nid] = (max_layer + 1, 0)

    # 自环**不再**给节点加偏移。
    #
    # 旧实现把带自环的节点沿 y 推 `node_gap * 0.5`，本意是"给自环腾地方"，
    # 实际效果是把这块**正好推到同层邻居身上**（同层间距就是 node_gap，
    # 推半格 = 压掉一半）——真实项目 51 个块里有 3 对因此重叠。
    # 自环现在由前端画在块的右侧（见 `ui_qt.graph_canvas.EdgeItem._points`），
    # 不需要在坐标里腾地方。
    # 自环本身已经在上面的破环之前被摘出来（不参与分层），
    # 所以这里什么都不用做。

    for nid, (x, y) in coords.items():
        graph.nodes[nid].x = x
        graph.nodes[nid].y = y

    _ = (undirected_neighbors, self_loops, virtual_info)
