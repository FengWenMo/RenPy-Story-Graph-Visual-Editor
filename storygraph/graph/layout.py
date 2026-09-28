# layout.py
from collections import deque, defaultdict
import bisect

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

    def break_cycles(adj):
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

    reversed_edges = break_cycles(adj)

    dag_adj = {nid: set() for nid in graph.nodes}
    for u, v, _ in edges:
        if (u, v) in reversed_edges:
            dag_adj[v].add(u)
        else:
            dag_adj[u].add(v)

    def ensure_dag(dag_adj, nodes):
        while True:
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
            for u in remaining:
                for v in dag_adj[u]:
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

    dag_adj = ensure_dag(dag_adj, list(graph.nodes))

    indeg = {u: 0 for u in graph.nodes}
    for u in graph.nodes:
        for v in dag_adj[u]:
            indeg[v] += 1
    if start_id is not None and start_id in indeg:
        indeg[start_id] = 0

    sources = [u for u in graph.nodes if indeg[u] == 0]
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
    for nid in graph.nodes:
        if nid not in layers:
            layers[nid] = max_layer + 1
            max_layer = max(max_layer, layers[nid])

    preds = {u: set() for u in graph.nodes}
    for u, v, _ in edges:
        if (u, v) in reversed_edges:
            preds[u].add(v)
        else:
            preds[v].add(u)

    for _ in range(2):
        changed = False
        for u in sorted(graph.nodes, key=lambda x: layers[x]):
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
                vid = f"__virt_{eid}_{layer}"
                virtual_info[vid] = eid
                layer_nodes_ext[layer].append(vid)
                edge_segments.append((prev, vid, eid))
                prev = vid
            edge_segments.append((prev, high_node, eid))

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

    def count_crossings(order):
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

    ext_neighbors = defaultdict(set)
    for a, b, _ in edge_segments:
        ext_neighbors[a].add(b)
        ext_neighbors[b].add(a)

    def is_virtual(node_id):
        return isinstance(node_id, str) and node_id.startswith("__virt_")

    def barycenter_once(order):
        new_order = {lv: list(order[lv]) for lv in order}
        for lv in range(1, max_layer + 1):
            prev = new_order.get(lv - 1, [])
            if not prev:
                continue
            pos_prev = {nid: idx for idx, nid in enumerate(prev)}
            bary = {}
            for nid in new_order.get(lv, []):
                neighbors = [nb for nb in ext_neighbors[nid] if nb in pos_prev]
                if neighbors:
                    weights = [0.5 if is_virtual(nb) else 1.0 for nb in neighbors]
                    total_weight = sum(weights)
                    weighted_vals = sorted(pos_prev[nb] * w for nb, w in zip(neighbors, weights))
                    if total_weight == 0:
                        bary[nid] = len(prev) / 2
                    else:
                        cum = 0
                        target = total_weight / 2
                        for val, nb, w in sorted(zip(weighted_vals, neighbors, weights)):
                            cum += w
                            if cum >= target:
                                bary[nid] = val / w if w != 0 else val
                                break
                        else:
                            bary[nid] = weighted_vals[-1] / weights[-1] if weights[-1] != 0 else weighted_vals[-1]
                else:
                    bary[nid] = len(prev) / 2
            new_order[lv] = sorted(new_order[lv],
                                   key=lambda n: (bary.get(n, len(prev)/2), str(n)))
        for lv in range(max_layer - 1, -1, -1):
            next_layer = new_order.get(lv + 1, [])
            if not next_layer:
                continue
            pos_next = {nid: idx for idx, nid in enumerate(next_layer)}
            bary = {}
            for nid in new_order.get(lv, []):
                neighbors = [nb for nb in ext_neighbors[nid] if nb in pos_next]
                if neighbors:
                    weights = [0.5 if is_virtual(nb) else 1.0 for nb in neighbors]
                    total_weight = sum(weights)
                    weighted_vals = sorted(pos_next[nb] * w for nb, w in zip(neighbors, weights))
                    if total_weight == 0:
                        bary[nid] = len(next_layer) / 2
                    else:
                        cum = 0
                        target = total_weight / 2
                        for val, nb, w in sorted(zip(weighted_vals, neighbors, weights)):
                            cum += w
                            if cum >= target:
                                bary[nid] = val / w if w != 0 else val
                                break
                        else:
                            bary[nid] = weighted_vals[-1] / weights[-1] if weights[-1] != 0 else weighted_vals[-1]
                else:
                    bary[nid] = len(next_layer) / 2
            new_order[lv] = sorted(new_order[lv],
                                   key=lambda n: (bary.get(n, len(next_layer)/2), str(n)))
        return new_order

    for _ in range(max_iter):
        new_order = barycenter_once(order_ext)
        changed = any(order_ext[lv] != new_order.get(lv, []) for lv in order_ext)
        order_ext = new_order
        if not changed:
            break

    best_cross = count_crossings(order_ext)
    for _ in range(2):
        improved_global = False
        for lv in range(max_layer + 1):
            layer = order_ext[lv]
            if len(layer) < 2:
                continue
            local_improved = True
            while local_improved:
                local_improved = False
                for i in range(len(layer) - 1):
                    layer[i], layer[i+1] = layer[i+1], layer[i]
                    new_cross = count_crossings(order_ext)
                    if new_cross < best_cross:
                        best_cross = new_cross
                        local_improved = True
                        improved_global = True
                        break
                    else:
                        layer[i], layer[i+1] = layer[i+1], layer[i]
                if local_improved:
                    pass
            if improved_global:
                break
        if not improved_global:
            break

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

    for u, v in self_loops:
        if u in coords:
            coords[u] = (coords[u][0], coords[u][1] + node_gap * 0.5)

    for nid, (x, y) in coords.items():
        graph.nodes[nid].x = x
        graph.nodes[nid].y = y