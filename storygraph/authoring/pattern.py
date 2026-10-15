"""
**位置序列**：把"自定义写法"从键值对换成"一排有顺序、有类型的格子"
=========================================================================

> 用户（程序员）2026-10-11 的撞墙记录，诊断原话：
> "它的根本错误在于：你试图用一个**扁平的键值对表单**，
>  去描述一个**复杂的树状语法结构**。"
> "这就好比你想教人做菜，但你的表单只给了三个填空：'食材'、'调料'、'火候'，
>  却没有地方让你写'先放油还是先放盐'。"

所以这里定义**位置序列**（`pattern`）—— 顺序、类型、可选、候选值全都放得下：

    pattern = [
        {'kind': 'keyword', 'text': 'show'},
        {'kind': 'variable', 'column': '角色'},
        {'kind': 'choice',  'column': '服装', 'group': 'outfit'},
        {'kind': 'choice',  'column': '神态', 'group': 'expression'},
        {'kind': 'optional', 'text': 'at', 'column': '位置'},
    ]

## 为什么要有 `match_line`

用户卡死的第一步是"**语句名该填什么、这一行会被怎么切**" —— 界面答不出来。
`match_line(pattern, '小明 泳装 开心')` 就是那个答案：它把一行样例切成
**每格吃到的字**，于是界面可以**当场**把切错的地方显示出来
（"顺序不对"、"这一格没东西"、"多出来一段没人认领"）。

⚠ 匹配用**带回溯的贪心**（模式很短，够用）：`optional` 段可以"这次不出现"。
靠"从左往右一路吃"是**错的** —— 那会把 `show a at left` 里的 `at` 当成变量。
"""

from __future__ import annotations

import re

#: 格子的类型（**这是"工具怎么知道小明是变量、泳装是枚举"的答案**）
SLOT_KINDS = ('keyword', 'variable', 'choice', 'optional')

#: 给界面/文档看的中文名（**用词说人话**：不出现 schema/JST 这类实现者词汇）
KIND_LABELS = {
    'keyword': '固定关键字',
    'variable': '任意变量',
    'choice': '从这些里选',
    'optional': '可选的固定段',
}

#: 一个"词"怎么切：非空白连续段（中文没有空格时**整段算一个词**，
#: 所以中文写法的样例要靠人给空格或靠候选值来切 —— 见 `_tokens`）
_TOKEN_RE = re.compile(r'\S+')


def _tokens(line: str, *, options=()) -> list:
    """
    把一行切成"词"。优先用空格；**没有空格时**用候选值列表做二次切分。

    为什么需要二次切分（中文项目的现实）：`show小明泳装开心` 没有空格，
    一眼看不出边界；但如果我们知道候选值是 `泳装/校服/开心/害羞…`，
    就能按"最长匹配"把它们切出来。⚠ 切不出来就**不硬切**（返回整段），
    让界面显示"这一格吃掉了整段"，而不是猜错。
    """
    text = str(line or '').strip()
    if not text:
        return []
    if re.search(r'\s', text):
        return _TOKEN_RE.findall(text)
    # 无空格：按候选值最长匹配切（**只在真的切得出来时才用**）
    words = sorted({str(o) for o in (options or ()) if str(o).strip()},
                   key=len, reverse=True)
    if not words:
        return [text]
    out, i, buf = [], 0, ''
    while i < len(text):
        hit = next((w for w in words if text.startswith(w, i)), '')
        if hit:
            if buf:                        # 攒下来的"切不出候选"的一段先交出去
                out.append(buf)
                buf = ''
            out.append(hit)
            i += len(hit)
        else:
            buf += text[i]
            i += 1
        # ⚠⚠ 2026-10-12 修掉的 bug（用户实测截图）：这里原来是
        #   `out.append(text[i]); i += 1` —— **逐字切**。后果：
        #   没有任何候选值匹配的整段（如试切只写 `show`）会被切成
        #   's','h','o','w'，**恰好填满 4 个格子**，界面于是显示
        #   "切出来是这样：s（固定）｜h（角色）｜o（服装）｜w（表情）"
        #   —— 一个**假成功**，比报错危险得多（人会以为格子对了）。
        #   这与本函数自己的注释（"切不出来就不硬切"）直接矛盾，现在按注释办：
        #   把"切不出候选"的连续字**攒成一段**交出去（`show` → 1 个词），
        #   界面就会如实说"切不上"，而不是编一个满分的答案。
    if buf:
        out.append(buf)
    return out


def parse_pattern(raw) -> list:
    """
    规范化/校验一串格子（**纯函数**，坏格子直接丢掉并标出来）。

    返回 `[slot…]`，每个 slot 一定是 `{'kind', 'text', 'column', 'group',
    'options', 'problems'}` 齐全的形状 —— 界面可以直接照它渲染。
    """
    out = []
    for i, item in enumerate(raw or ()):
        slot = {'kind': '', 'text': '', 'column': '', 'group': '',
                'options': [], 'limit': False, 'multi': False, 'problems': []}
        if isinstance(item, dict):
            kind = str(item.get('kind') or '').strip()
            slot['text'] = str(item.get('text') or '').strip()
            slot['column'] = str(item.get('column') or item.get('label')
                                 or '').strip()
            slot['group'] = str(item.get('group') or '').strip()
            opts = item.get('options') or item.get('choices') or []
            if isinstance(opts, str):
                opts = [o.strip() for o in opts.replace('，', ',').split(',')]
            slot['options'] = [str(o).strip() for o in opts if str(o).strip()]
            # ⚠ `limit`（2026-10-13 加）：**候选到底限不限死**。
            #   缺省 `False` —— 老声明（没有这个键）也一律按"候选只是建议"处理，
            #   这正是修"切不上"的那一半：存量声明不会因为候选不全而判人写错。
            slot['limit'] = bool(item.get('limit'))
            # ⚠ `multi`（2026-10-13 加）：这一格**可以吃多个词**。
            #   真机依据：`show lan happy sad` 的 `happy sad` 是同一格
            #   （`layeredimage` 的 `expressions` 组可以有多个属性）。
            #   缺省 `False` —— 老声明一个字不变。
            slot['multi'] = bool(item.get('multi'))
            # ⚠ **旧形状**（`{'name','label','choices'}`）要继续认（判据压着）：
            #   旧 `name` 是"剧本里怎么写的那串字" ⇒ 对应新模型的**固定关键字**；
            #   旧 `label` 是表格列名 ⇒ `column`；旧 `choices` ⇒ `options`。
            if not kind and item.get('name'):
                kind = 'keyword'
                slot['text'] = str(item.get('name')).strip()
        else:
            kind = str(item or '').strip()
        if kind not in SLOT_KINDS:
            # 兼容旧形状：`foo=`（带等号的）猜成 variable，纯词猜成 keyword
            if kind.endswith('='):
                kind, slot['column'] = 'variable', kind[:-1]
            elif kind:
                kind, slot['text'] = 'keyword', kind
            else:
                slot['problems'].append('这一格没写类型')
                kind = 'variable'
        slot['kind'] = kind
        if kind in ('keyword', 'optional') and not slot['text']:
            slot['problems'].append('固定段得写清是哪个字')
        if kind in ('variable', 'choice', 'optional') and not slot['column']:
            slot['problems'].append('这一格要有个列名（表格里显示什么）')
        if kind == 'choice' and slot['limit'] \
                and not (slot['options'] or slot['group']):
            # ⚠ "从这些里选"**没给候选不算毛病**（2026-10-13 改）：候选是下拉里的
            #   备选，不是必填项 —— 没候选就等于"随便写"。旧版在这里报一句红，
            #   逼人去贴一串候选；贴了之后旧代码又把那一格变成封闭枚举
            #   （→ "切不上"）。只有**显式勾了"只能选这些"却没给候选**才真矛盾。
            slot['problems'].append('勾了"只能选这些"，却一个候选都没给')
        out.append(slot)
    return out


def _cell(slot: dict, text: str) -> dict:
    return {'kind': slot['kind'], 'text': text, 'column': slot.get('column', ''),
            'group': slot.get('group', ''), 'ok': True}


#: 枚举格遇到"候选里的词出现在后面"时怎么办（`assign_tokens` 的 `policy`）。
#:
#: · `'order-first'`（**甲，默认**）：按书写顺序吃**当前**这个词。试切那一侧用。
#: · `'candidates'`：若**后面**出现了候选里的词，跳过中间那些去认它
#:   （把候选当"词典"消歧）。读值那一侧的历史口径。
#:
#: ⚠⚠ 两种策略**只在这一处会分岔**（枚举格后面还跟着别的会吃词的格子、
#:   而候选词出现在更后面）。这条分岔由判据
#:   `test_assign_tokens_single_source.py` 钉住，防止它再长出新分岔。
POLICIES = ('order-first', 'candidates')


def _can_allocate(tokens, slots, si: int, ti: int, groups) -> bool:
    """
    `slots[si:]` 从 `tokens[ti:]` 起**能不能全部吃到词**（纯判定，无副作用）。

    为什么要它：多值格（"这一格可以有多个"）到底该吃几个词，**不看自己看后面**
    —— 真机 `show lan happy sad` 的「服装」只该吃 `happy`，因为「表情」还要一个词；
    而 `show lan clothes2 happy shy` 里它该吃 `clothes2 happy`（后面只差一个）。
    没有这个判定就只能"贪心吃满"，于是后面那格永远是空的（**假成功**）。
    """
    if si >= len(slots):
        return True
    slot = slots[si]
    kind = str(slot.get('kind') or 'variable')
    text = str(slot.get('text') or '').strip()
    if kind == 'keyword':
        return (ti < len(tokens) and tokens[ti] == text
                and _can_allocate(tokens, slots, si + 1, ti + 1, groups))
    if kind == 'optional':
        if (ti + 1 < len(tokens) and tokens[ti] == text
                and tokens[ti + 1] not in ('', ':')):
            if _can_allocate(tokens, slots, si + 1, ti + 2, groups):
                return True
        return _can_allocate(tokens, slots, si + 1, ti, groups)
    allowed = _allowed_of(slot, groups)
    if kind == 'choice' and slot.get('limit') and not allowed:
        return False                       # 勾了限死却没候选 → 谁也不能通过
    if slot.get('multi'):
        stop = {str(s.get('text') or '').strip() for s in slots[si + 1:]
                if str(s.get('kind') or '') in ('keyword', 'optional')}
        stop.discard('')
        end = ti
        while end < len(tokens) and tokens[end] not in stop:
            end += 1
        return any(_can_allocate(tokens, slots, si + 1, ti + n, groups)
                   for n in range(end - ti, -1, -1))
    if ti >= len(tokens):
        return False
    if (kind == 'choice' and slot.get('limit') and allowed
            and tokens[ti] not in allowed):
        return False
    return _can_allocate(tokens, slots, si + 1, ti + 1, groups)


def _allowed_of(slot, groups) -> list:
    """这一格的候选：格子自己写的 + 它的词组给的。"""
    out = [str(o) for o in (slot.get('options') or ())]
    g = groups.get(str(slot.get('group') or ''))
    if isinstance(g, dict):
        out += [str(o) for o in (g.get('options') or ())]
    return out


def assign_tokens(tokens, slots, *, groups=None,
                  policy: str = 'order-first') -> dict:
    """
    **一行词 → 每格占哪几个词** —— 这件事的**唯一一份实现**。

    为什么要有它（2026-10-15 抽，对应路线图 D5 的"坏味道"）：
    在此之前，这件事有**三套并行实现**，规则已经漂移过：

      · `pattern.match_line`  —— 试切，递归回溯、会"切不上"；
      · `story/spec_args._claim`              —— 卡片读值那一侧的单趟认领；
      · `story/spec_args.slot_value_map`      —— 又把 `_claim` 抄了一遍。

    漂移的后果正是用户看到的那种自相矛盾："**试切说切上了、卡片读出来不一样**"。
    现在只有这一份；`match_line` 与 `_claim` 都只是它的**薄转发**，
    分别拿它多出来的 `bad_kw` / `rejected` / `used` 去说人话。

    规则（**甲 = 书写顺序**，与 `pattern` 模块的既有口径一致）：
      · `keyword`：必须**逐字相等**才吃（对不上记 `bad_kw`，是硬失败）；
      · `optional`：`下一个词 == 它的字` → 吃**两个**词（`at left`），否则跳过；
      · `multi`：**长的先试**，取第一个"让后面每一格也都还能吃到词"的长度
        （判据见 `_can_allocate`）；一个都不吃也是合法解；
      · `choice`：勾了 `limit` 就只认候选里的词（否则记 `rejected`）；
        `policy='candidates'` 时允许跳到后面出现的候选词；
      · 其余：吃一个词。
      · `multi` **不许吃后面格子的固定字**（否则 `mypose happy sad at left`
        会把 `at left` 一起吃进「表情」）。

    返回::

        {'spans': {第几格: (词起点, 词终点)},   # 终点不含；没认领到的不在里面
         'used': 吃到第几个词（后面的都是没人认领的）,
         'bad_kw': 第几格的关键字对不上（或 None）,
         'rejected': 第几格勾了"只能选这些"却来了候选外的词（或 None）,
         'policy': 用了哪一档}
    """
    tokens = list(tokens or ())
    slots = [dict(s) for s in (slots or ()) if isinstance(s, dict)]
    groups = groups or {}
    spans: dict = {}
    ti = 0
    bad_kw = None
    rejected = None
    #: **该吃词却没吃到**的格子（普通变量/枚举格）—— 它跟"没人认领的词"是两回事：
    #: 前者是"这一行少了那一格该有的词"（真失败），后者是"多出来了"。
    #: ⚠ 多值格与可选段**不算**：前者"一个都不吃"本来合法，后者"不出现"也合法。
    unfilled: list = []
    for si, slot in enumerate(slots):
        kind = str(slot.get('kind') or 'variable')
        text = str(slot.get('text') or '').strip()
        # ---- 固定关键字：必须**逐字相等** ----
        if kind == 'keyword':
            if ti < len(tokens) and tokens[ti] == text:
                spans[si] = (ti, ti + 1)
                ti += 1
            elif bad_kw is None:
                bad_kw = si
            continue
        # ---- 可选段：`at 位置` —— 出现就吃两个词，不出现就跳过 ----
        if kind == 'optional':
            if (ti + 1 < len(tokens) and tokens[ti] == text
                    and tokens[ti + 1] not in ('', ':')):
                spans[si] = (ti, ti + 2)
                ti += 2
            continue
        allowed = _allowed_of(slot, groups)
        # ---- 可重复的格子（`multi`）----
        if slot.get('multi'):
            stop = {str(s.get('text') or '').strip() for s in slots[si + 1:]
                    if str(s.get('kind') or '') in ('keyword', 'optional')}
            stop.discard('')
            most = ti
            while most < len(tokens) and tokens[most] not in stop:
                if (kind == 'choice' and slot.get('limit') and allowed
                        and tokens[most] not in allowed):
                    break
                most += 1
            chosen = None
            for n in range(most - ti, -1, -1):
                if _can_allocate(tokens, slots, si + 1, ti + n, groups):
                    chosen = n
                    break
            if chosen is None:
                chosen = most - ti          # 后面本来就不够 → 尽力吃满
            if chosen:
                spans[si] = (ti, ti + chosen)
            ti += chosen
            continue
        # ---- 勾了限死却一个候选都没给 = 前后矛盾，谁也不能通过 ----
        if kind == 'choice' and slot.get('limit') and not allowed:
            if rejected is None:
                rejected = si
            continue
        if ti >= len(tokens):
            unfilled.append(si)        # 这一格该吃词却没词了（调用方显示空格子）
            continue
        if kind == 'choice' and policy == 'candidates' and allowed:
            hit = next((t for t in tokens[ti:] if t in allowed), None)
            if hit is not None:
                ti = tokens.index(hit, ti)
        if (kind == 'choice' and slot.get('limit') and allowed
                and tokens[ti] not in allowed):
            if rejected is None:
                rejected = si
            continue
        spans[si] = (ti, ti + 1)
        ti += 1
    return {'spans': spans, 'used': ti, 'bad_kw': bad_kw,
            'rejected': rejected, 'unfilled': unfilled, 'policy': policy}

def match_line(pattern, line, *, groups=None) -> dict:
    """
    **把一行样例切成格子** —— 界面靠它当场告诉用户"你填的顺序对不对"。

    返回::

        {'ok': bool,
         'cells': [{'kind','text','column','group','ok'}…],   # 每格吃到的字
         'leftover': [没被认领的词…],                          # 多出来的
         'missing': [第几格没吃到东西…],
         'reason': 人话（不 ok 时说明卡在哪）}

    ⚠ 分配规则**不在这里** —— 它只有一份，见 `assign_tokens`（2026-10-15 抽出）。
    这里只做三件"试切"独有的事：把 tokens 交出去、把结果**对齐到格子下标**
    （缺席的格留一个空格子）、把"切不上"说成**人话**。
    """
    slots = parse_pattern(pattern)
    groups = groups or {}
    options = []
    for s in slots:
        options.extend(s.get('options') or [])
        g = groups.get(s.get('group') or '')
        if isinstance(g, dict):
            options.extend(g.get('options') or [])
    #: 无空格时也**尽量**切得出来：候选值 + 固定关键字一起当"可切的整词"
    #: （中文项目的现实：`show小明泳装开心` 没有空格）
    options = list(options)
    for s in slots:
        if s.get('text'):
            options.append(s['text'])
    tokens = _tokens(line, options=options)

    got = assign_tokens(tokens, slots, groups=groups, policy='order-first')
    spans, used = got['spans'], got['used']
    # ⚠⚠ **按格子下标归位**（2026-10-13 修）：缺席的格子（空的多值格 / 缺席的
    #   可选段）**也要产出一个空格子**，否则 `cells` 比 `pattern` 短、下标整体
    #   错位 —— 界面说"第 N 格没吃到"会指着**另一个格子**（人越改越错），
    #   卡片按格子取值那侧也跟着串位。
    cells = []
    for i in range(len(slots)):
        span = spans.get(i)
        cells.append(_cell(slots[i],
                           ' '.join(tokens[span[0]:span[1]]) if span else ''))
    leftover = tokens[used:]
    ok = (got['bad_kw'] is None and got['rejected'] is None
          and not got['unfilled'] and used >= len(tokens))
    if ok:
        reason = ''
    elif got['bad_kw'] is not None:
        i = got['bad_kw']
        reason = (f'第 {i + 1} 格要的关键字是「{slots[i]["text"]}」，'
                  f'这一行里对不上 —— 检查顺序、关键字写法')
    elif got['rejected'] is not None:
        i = got['rejected']
        reason = (f'第 {i + 1} 格勾了"只能从这些里选"，'
                  f'而这一行里的词不在候选里')
    elif leftover:
        reason = (f'后面还有 {len(leftover)} 个词没人认领：'
                  + ' '.join(leftover))
    else:
        i = got['unfilled'][0]
        name = str(slots[i].get('column') or slots[i].get('text') or '').strip()
        reason = (f'第 {i + 1} 格（{name or "这一格"}）该有一个词，'
                  f'但这一行里没有 —— 检查顺序、关键字写法，'
                  f'或者是不是少了哪一段')
    return {'ok': ok, 'cells': cells, 'leftover': leftover,
            'missing': [i for i, c in enumerate(cells)
                        if not str(c['text']).strip()],
            'reason': reason}


def columns_of(pattern) -> list:
    """这一串格子在表格里会产生哪几列（**按顺序**，去重）。"""
    out = []
    for s in parse_pattern(pattern):
        col = s.get('column') or ''
        if col and col not in out:
            out.append(col)
    return out


def groups_used(pattern) -> list:
    """这一串格子用到了哪些词组（去重、按顺序）—— 界面据此提示"还得建词组"。"""
    out = []
    for s in parse_pattern(pattern):
        g = s.get('group') or ''
        if g and g not in out:
            out.append(g)
    return out


def problems_of(pattern) -> list:
    """
    把所有格子的毛病汇总成**人话**清单（界面直接显示，不用自己拼）。

    ⚠ 除了每格自己的毛病，还查一条**跨格**的：**列名重名**
    （2026-10-13 加）。两个格子叫同一个列名时，切行照样切得出来，
    但它们在卡片/表里是**同一列** → 两个值互相覆盖，而人看不出来。
    这种"切得上、但结果不对"的毛病正是这个函数存在的理由。
    """
    parsed = parse_pattern(pattern)
    out = []
    for i, s in enumerate(parsed, 1):
        for p in s.get('problems') or []:
            out.append(f'第 {i} 格：{p}')
    seen: dict = {}
    for i, s in enumerate(parsed, 1):
        name = str(s.get('column') or '').strip()
        if not name:
            continue
        if name in seen:
            out.append(f'第 {seen[name]} 格与第 {i} 格**重名**（都叫「{name}」）——'
                       f'它们在卡片与表里是同一列，两个值会互相覆盖；'
                       f'改掉其中一个的名字')
        else:
            seen[name] = i
    return out


__all__ = ['KIND_LABELS', 'POLICIES', 'SLOT_KINDS', 'assign_tokens',
           'columns_of', 'groups_used',
           'match_line', 'parse_pattern', 'problems_of']
