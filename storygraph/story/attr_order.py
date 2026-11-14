"""
storygraph.story.attr_order
===========================

**这个项目自己**把立绘属性写成什么顺序（`show lan aa shy` 还是 `lan shy aa`）。

为什么需要它（2026-11-14 用户："为什么还会出现 oh aa 一类的顺序乱套的情况呢？"）
-------------------------------------------------------------------------------
`app.beats.apply_attr_slot` 换一维时是**原位替换**（2026-11-05 修过一次），所以
"两个词都在"的时候顺序永远不动。真正漏掉的是**这一维原来没写**的时候：

    show lan shy          ← 作者（或卡片）只写了表情
    用户点「服装」选 aa
    → show lan shy aa     ← 新词被追加在末尾，和文件里别处的 `lan aa shy` 反着

真机 `chapter2_2_3.rpy` 就有 3 行这样（第 17/25/33 行 `show lan shy aa`，
同一文件第 252/271/395 行是 `lan aa shy`）—— 全项目 1269 条"服装在前"，
只有这 3 条反着。

Ren'Py 里属性顺序**语义等价**（属性是一个集合），所以这不是演出 bug，是
**同一个项目里写法不一致**：作者读自己的脚本，会当成"工具又乱改了"。

## 判据：读作者自己写过的行，不猜

只认同一个 tag 的 `show` 行里**作者实际写过的相对顺序**：
两条以上都一致才采信（否则返回空 = 调用方追加到末尾，行为与从前一样）。
读盘走 `story.scan_cache`（按文件 `mtime+size` 失效），结果按
`(项目根, tag)` 缓存 —— 卡片翻页时不会反复扫盘。
"""
from __future__ import annotations

import re

#: `show <tag> …` 一行（`show` 后面必须还有东西；缩进无所谓）。
_SHOW_RE = re.compile(
    r'^[ \t]*show[ \t]+(?P<tag>[A-Za-z_]\w*)[ \t]+(?P<rest>[^\n]+)$',
    re.MULTILINE)

#: 属性词后面跟这些就是**子句**了，不是属性（`at i11` / `with vpunch` / …）。
_CLAUSE_HEADS = frozenset({
    'at', 'with', 'behind', 'onlayer', 'as', 'zorder', 'transform',
})

#: 至少要有这么多条"两种维度都出现过"的行，才认这个项目的写法。
MIN_EVIDENCE = 2


def _attrs_of(rest: str) -> list:
    """一行 `show` 的尾巴 → 属性词列表（剥掉 `@位置` 与子句）。"""
    words = []
    for token in str(rest or '').split(';')[0].split():
        if token in _CLAUSE_HEADS:
            break
        word = token.split('@', 1)[0].strip()
        if word:
            words.append(word)
    return words


def _pair_votes(lines) -> dict:
    """统计 `{组A: {组B: 票数}}`：A 出现在 B 前面的次数（只看两者都出现的行）。"""
    votes: dict = {}
    for kinds in lines:
        for i, a in enumerate(kinds):
            for b in kinds[i + 1:]:
                if a == b:
                    continue
                votes.setdefault(a, {}).setdefault(b, 0)
                votes[a][b] += 1
    return votes


def _order_from_votes(votes: dict) -> list:
    """
    票数 → 组顺序（赢的场数减输的场数，高者靠前）。

    ⚠ 不搞拓扑排序：数据里可能两种顺序都有人写（作者自己不一致），
    拓扑会撞环然后"随机"挑一个。这里要的是**主流写法**，平手就按名字排
    （确定性优先，调用方也只拿它当"插哪一格"的提示）。
    """
    names = set(votes)
    for inner in votes.values():
        names |= set(inner)
    score = {}
    for a in names:
        win = sum(votes.get(a, {}).values())
        lose = sum(v.get(a, 0) for v in votes.values())
        score[a] = win - lose
    return sorted(names, key=lambda n: (-score[n], n))


def _scan(project_root: str, tag: str, group_of: dict) -> tuple:
    """扫一遍项目里的 `show <tag> …` → (`组顺序`, `证据行数`)。"""
    from storygraph.story import scan_cache as SC

    sequences = []
    for path in SC.discover(project_root):
        try:
            text = SC.read_text(path)[0]
        except (OSError, UnicodeDecodeError):
            continue
        for match in _SHOW_RE.finditer(text):
            if match.group('tag') != tag:
                continue
            kinds = [group_of[w] for w in _attrs_of(match.group('rest'))
                     if w in group_of]
            # 只看"真出现了两种以上维度"的行 —— 单维度的行说明不了顺序
            if len(set(kinds)) >= 2:
                sequences.append(kinds)
    return _order_from_votes(_pair_votes(sequences)), len(sequences)


def learned_order(project_root, tag: str, group_of: dict) -> list:
    """
    **这个项目**给 `tag` 写属性的顺序（组名列表）；证据不足 → `[]`。

    `group_of`：`{属性词: 组名}`（由调用方从 `attr_slot_rows` 的 `options`
    现取 —— 工具不自己发明"哪个词属于哪一组"）。

    ⚠ 这一层**不 try/except**（2026-11-14）：它是"插到哪一位"的**提示**，
    失败不该在这里被咽掉；调用方 `apply_attr_slot` 的入口
    （`ui_qt.sprite_grid._slot_apply`）已经有既有的宽捕获 + `swallow` 记痕。
    读盘失败由 `_scan` 里的**窄捕获**（`OSError` / `UnicodeDecodeError`）兜。
    """
    root = str(project_root or '').strip()
    tag = str(tag or '').strip()
    if not root or not tag or not group_of:
        return []
    from storygraph.story import scan_cache as SC

    files = SC.discover(root)
    fingerprint = SC.fingerprint(root, files)
    order, evidence = SC.memo(
        f'attr_order:{tag}', root, '', fingerprint,
        lambda: _scan(root, tag, dict(group_of)))
    return list(order) if evidence >= MIN_EVIDENCE else []


def insert_index(attrs, value_group: str, order, group_of: dict):
    """
    新词该插在第几个属性词之前 → 下标；`None` = 追加到末尾。

    `order` 是 `learned_order` 的结果（组名从前往后）。规则只有一条：
    **插在第一个"按这个项目写法排在该组之后"的词前面**。看不出就先不管
    （`None`，调用方追加 —— 与历史行为一致，绝不瞎插）。
    """
    order = [str(g) for g in (order or ())]
    if value_group not in order:
        return None
    rank = order.index(value_group)
    for i, attr in enumerate(attrs or ()):
        word = str(attr).split('@', 1)[0]
        group = group_of.get(word)
        if group in order and order.index(group) > rank:
            return i
    return None


__all__ = ['MIN_EVIDENCE', 'insert_index', 'learned_order']
