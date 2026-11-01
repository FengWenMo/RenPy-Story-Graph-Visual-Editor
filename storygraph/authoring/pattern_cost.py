"""
代价表与取词助手：**一个词落到某一格值多少分**。

这一层只认"词 / 候选集 / 归属表"，不认行、不认格子结构、也不 import 任何
别的 storygraph 模块 —— 所以它是叶子，`pattern.py`（分词 + 报告 + 入口）
单向依赖它。

为什么单独一个文件（2026-11-01）：原先这 245 行住在 `pattern.py` 里，那个
文件拆完 `_spans` 之后越过 1500 行门槛；而这一块是**纯表 + 无状态函数**，
和"怎么切一行"是两件事 —— 分开之后两边都能单独读完。

⚠ 这些说明都是被实测抓出来的（`show lan lan` 抢词、`show lan "" …` 值左移），
  搬动时逐字保留。
"""

#: **空引号**（`""` / `''` / `"   "`）在词流里的占位符。
#:
#: 为什么占位而不是丢掉（2026-10-18 第三轮攻击修，用户点名的第一条）：
#:
#:     show lan "" base_swimsuit happy
#:
#: 人写的是**四个位置**（第 2 个是空的）。以前 `_atoms` 里 `if text:` 把它连同
#: 毛病一起吞掉 → 3 个词对 3 个值格 → `服装=base_swimsuit`、`表情=happy`，
#: **还报绿灯**：人看到的是一条切得整整齐齐、但整行右移了一格的结果。
#:
#: 它同时是一个**输入错误**，不是"这一格留空"的写法：Ren'Py 里
#: `show lan "" happy` 的 `""` 是一个**空属性名**，跑不起来。想留空就别写那个词
#: （`show lan happy` → 服装空，那是绿测）。
#:
#: 取值用 `\x00`：真实的人名/属性名里不可能出现它，于是它既不会被任何人
#: 认成候选，也不会混进写回原文（`_span_text` 把它滤掉 → 那一格的值是 `''`）。
EMPTY_TOKEN = '\x00'


#: 一个词落到某一格时的**代价** —— 分词器思路的核心（2026-10-17 重构）。
#:
#: 用户现场：`show lan base_swimsuit happy` 被切成
#: 「`lan base_swimsuit` ｜ 空 ｜ `happy`」。「服装」勾了"可有多个"，于是它可以
#: **一个都不吃**；旧实现只问"后面每一格还能不能吃到词"，**从不问"这个词是谁的"**，
#: 于是前一格把后一格的候选词当成了自己的第二个值。
#:
#: 现在的规矩：每个词先查**归属**（`_owners_of`），落到
#:   · 自己的候选里 → 0（本该如此）；
#:   · 谁也不认识 → 2（自由文本，正常）；
#:   · **别的格子**的候选里 → 20（跨格抢词，几乎总是错的）。
#: 整行取**总代价最小**的切法（`_spans`）。
#:
#: ⚠⚠ 20 是刻意留的大数，不是"稍微贵一点"（2026-10-17 第二轮攻击修）：
#:   不抢词那一侧付的是 `EMPTY_SLOT(7) + SKIP_WORD(3) = 10`。旧值 8 比它便宜，
#:   于是"抢"**永远**赢 —— 实测 `show lan lan` 会给出一条不存在的服装
#:   （`服装 = lan`），界面还报绿灯。第一轮只比了 8 > 7，漏了被抢那个词本来
#:   要付的 3。
#:   兜底判据：`COST_STOLEN > EMPTY_SLOT + SKIP_WORD`（`test_cut_tokenizer_attack2`）。
#:   贵过这条线之后，"抢"在任何排版里都不会再被选中；把数留着而不是删掉分支，
#:   是为了代价表保持**全函数**（每个词×每格都有定义），将来要放开只改一个数。
COST_OWN = 0
#: 这一格**没有候选**（任意变量 / 没给候选的枚举格）→ 什么词都只是自由文本。
COST_UNKNOWN = 2
#: 这一格**随便写**（没有候选），而这个词是**别的格子写明的候选** —— "借"。
#:
#: ⚠ 8 被两条真需求夹出来的（2026-10-17）：
#:   · **必须比 `EMPTY_SLOT`（7）贵**：`[人物][表情(可多个)]` 遇上只有 `happy`，
#:     人物是"随便写"的格子，它抢走 `happy` 就会让表情整格空着 ——
#:     人写了 `happy`（表情的候选），卡片上"表情"却是空的，那是假成功。
#:     7 < 8 → 空着那一边赢（判据 `test_试切与读值在策略不冲突的形状上逐格相同`
#:     里 `happy` 那一例）。
#:   · **必须比 `EMPTY_SLOT + COST_EXTRA`（9）便宜**：`mypose happy sad at left`
#:     里人物抽走 `happy`、表情拿 `sad` 是 8 点；人物空着、表情吃两个是 7+2=9 点。
#:     8 < 9 → 人物拿到 `happy`，"谁在摆姿势"才不会莫名其妙变成空的
#:     （判据 `test_cut_tokenizer_attack2::test_任意变量格抢词这条需求不许被修坏`）。
#:   两个方向只留 1 点余量，所以**别随手改这两个数里的任何一个**。
COST_BORROW = 8
#: 这一格**有候选**，却吃了一个**谁也不认识**的词 = "值不在候选里"。
#:
#: ⚠ 比 `COST_UNKNOWN`（2）贵（2026-10-17 第二轮攻击修）：同样的词，
#:   落进"随便写"的格子是正常，落进**封闭枚举**是**异常**（界面把那一格标黄）。
#:   贵一点才使得"跳过它去认自己真正的候选"划算 —— 见 `SKIP_JUNK`。
#:   它必须**严格小于** `COST_STOLEN`（抢别人写明的候选永远更糟）。
COST_OFFLIST = 5
#: 封闭枚举格吃了一个**别的格子写明的候选** = 跨格抢词。
#:
#: ⚠⚠ 20 是刻意留的大数，不是"稍微贵一点"（2026-10-17 第二轮攻击修）：
#:   不抢词那一侧付的是 `EMPTY_SLOT(7) + SKIP_WORD(3) = 10`。旧值 8 比它便宜，
#:   于是"抢"**永远**赢 —— 实测 `show lan lan` 会给出一条不存在的服装
#:   （`服装 = lan`），界面还报绿灯。第一轮只比了 8 > 7，漏了被抢那个词本来
#:   要付的 3。
#:   兜底判据：`COST_STOLEN > EMPTY_SLOT + SKIP_WORD`（`test_cut_tokenizer_attack2`）。
#:   贵过这条线之后，"抢"在任何排版里都不会再被选中；把数留着而不是删掉分支，
#:   是为了代价表保持**全函数**（每个词×每格都有定义），将来要放开只改一个数。
COST_STOLEN = 20
#: 多值格从**第 2 个**词起每个加一点：能分开就别堆在一格
#: （两个相邻格子都勾了"可有多个"时，这一步就是"甲吃甲、乙吃乙"的仲裁）。
COST_EXTRA = 2

#: "没做成"那一档的代价（四类失败各记一点，数值只有相对大小有意义）。
#:
#: ⚠⚠ 这几个数**不是**"切不切得上"的判据 —— 判据是 `_report` 从同一份
#:   `spans` 倒推出来的四件事（关键字对不上 / 限死挡词 / 该吃词的格子空着 /
#:   有词没人认领）。代价只负责**选哪一种排版最说得通**（失败那一屏也要好看），
#:   以及"能塞进某一格就别晾着"这种偏好。
#:   为什么不能拿"总代价 == 0"当成功：吃一个谁都不认识的词（自由文本，真机常态）
#:   本身就要 `COST_UNKNOWN` 2 点，那不是失败。
#:
#: ⚠ `SKIP_WORD`（3）**故意比** `COST_UNKNOWN`（2）大一点：意思是"能塞进某一格（2）
#:   就塞，塞不进去才晾着（3）"。太便宜的话，什么词都会被判成"没人认领"。
SKIP_WORD = 3
#: 一个**必填格**空着（该有一个词，却没有）。
#:
#: ⚠ 这个数字与"抢别人的词"是**一对**：抢 = 20 > 空着 7 + 那个词晾着 3 = 10，
#:   所以"能不动别人的词就不动"是**硬的**，不是"略便宜"。
#:   （2026-10-17 第一轮它写成 7 对 8，只比了 7 < 8，漏了被抢那个词本来要付的
#:   3 —— 于是 `show lan lan` 会给出一条不存在的服装 `服装 = lan`，还报绿灯。
#:   判据：`test_cut_tokenizer_attack2::test_一个词不许被别的格子偷走`。）
#:
#:   `mypose happy sad at left`（人物格没有候选 → 自由文本 2）这条真需求**不是**
#:   靠这个数保住的，而是靠"闭枚举 vs 随便写"（见 `COST_UNKNOWN` / `COST_OFFLIST`）。
EMPTY_SLOT = 7
MISS_KW = 2
#: **跳过一个"谁也不认识的词"**，好让自己那一串候选从头开始。
#:
#: 为什么要跳（用户现场 2026-10-17 的第二次地狱测试）：
#:   `show lan dirty base_swimsuit happy` 里的 `dirty` 是垃圾词。「服装」如果
#:   "从当前位置开始吃"，就只能把它焊进值里（`dirty base_swimsuit`）—— 那正是
#:   用户点名的"垃圾被焊进某个合法值"。有了这一条，正确排版是
#:   `服装 = base_swimsuit` + "第 3 个词「dirty」没人认领"。
#:
#: ⚠ 跳过的**总**代价 = `SKIP_JUNK + SKIP_WORD`（2026-10-17 第二轮攻击修）：
#:   被跳过的词最后一定会被 `_report` 算进 `leftover`，而"晾着一个词"本来就要
#:   付 `SKIP_WORD`（3）—— 只记 `SKIP_JUNK` 等于让"跳过"变**免费**，
#:   于是 `小明 龙袍 泳装` 会跳过 `龙袍` 去吃 `泳装`（2 点 vs 4 点），
#:   而 `clothes2 junk happy` 里表情宁可跟垃圾焊在一起。
#:   合并之后（每个被跳过的词 1+3 = 4 点）：
#:   · 比"把这个词当值吃进闭枚举"（`COST_OFFLIST` 5）便宜 → 会去认自己的候选；
#:   · 比"跳过一个本来就该晾着的末位词"贵（4 > 2）→ 末位垃圾词仍然被吃掉。
SKIP_JUNK = 1
#: 固定关键字对不上时，**把这个错词也吃掉**的代价（比"晾着"贵一点）。
#:
#: 为什么需要它：`shw lan base_swimsuit happy` 里 `shw` 是**拼错的关键字**。
#: 如果它不被吃掉，第 2 格（角色）就会从 `shw` 开始，只能把拼错的关键字
#: 当成一个角色名（实测界面显示 `角色 = "shw lan"`，而 reason 说"关键字错了"
#: —— 两条互相打脸的线索）。吃掉它 = 承认"这个词本来该是关键字"。
MISS_KW_SKIP = 4


def _allowed_of(slot, groups) -> list:
    """这一格的候选：格子自己写的 + 它的词组给的。"""
    out = [str(o) for o in (slot.get('options') or ())]
    g = groups.get(str(slot.get('group') or ''))
    if isinstance(g, dict):
        out += [str(o) for o in (g.get('options') or ())]
    return out


def _owners_of(slots, groups) -> dict:
    """
    每个词**被哪些格子写成了候选** —— `{词: {格子下标…}}`。

    这是"分词器"与"贪婪捕获"的分水岭：切之前先知道"这个词是谁的"，
    后面就不需要靠"多吃少吃"来猜。候选本身含空格时（`base hands down`）
    连每个词也单独登记，于是 `base` / `hands` / `down` 都算那一格的东西。
    """
    owners: dict = {}
    for si, slot in enumerate(slots):
        known = [str(o).strip() for o in _allowed_of(slot, groups)
                 if str(o).strip()]
        literal = str(slot.get('text') or '').strip()
        if literal:
            known.append(literal)          # 固定字也是"这一格的东西"
        for opt in known:
            for word in {opt, *opt.split()}:
                if word:
                    owners.setdefault(word, set()).add(si)
    return owners


def _tok_cost(tok, si: int, allowed, owners, limited: bool, dead=()):
    """
    一个词单独交给第 `si` 格，代价多少；`None` = **这一格不能这么吃**。

    ⚠ `limit`（"只能从这些里选"）是一条**硬**规矩：候选外的词一概不收，
       返回 `None` 让分配器换别的路（换不了就由 `_report` 说清是谁挡的）。

    ⚠⚠ **闭枚举格 vs 随便写的格子**（2026-10-17 第二轮攻击修）：
       `allowed` 非空 = 这一格是**封闭枚举**（"这个词落在这一格"有明确归属），
       别人的候选落在里面就是**抢**（20）；
       `allowed` 为空 = 这一格**随便写**（任意变量 / 没给候选的枚举格），
       对它来说任何词都只是自由文本（2）—— 于是
       `mypose happy sad at left` 里人物格拿走 `happy` 仍然便宜，
       "谁在摆姿势"不会莫名其妙变成空。
       这一条是"不许偷词"与"变量格要能抢到第一个词"能同时成立的**唯一**依据。

    ⚠ `dead`（2026-10-18）：**被空引号废掉的那几格**。它们的候选词不再是
      "别人写明的候选" —— 那一格这一行没有值，它的候选词就是**没人要**的，
      后面那一格按位置接住才是对的（`show lan "" base_swimsuit happy`
      里 `base_swimsuit` 要落到「表情」）。但它们在**跳过**那一侧仍然是
      "有主的"（`_spans` 的跳过循环照样停），所以不会被当成垃圾晾掉。

    ⚠⚠ **只属于死格的候选词按"没人认识"（`COST_UNKNOWN`）计价**（2026-10-18
      第四轮攻击修）：按"候选外"（`COST_OFFLIST` 5）计价时，"接住"只在**必填格**
      成立 —— 多值格宁可空着（0 + 晾着 3 = 3 < 5），于是
      `show lan "" base_swimsuit happy` 在"服装 multi"下把两个词一起晾着。
      降成 2 之后：必填格 2 < 空着 7 + 3；多值格 2 < 空着 0 + 3 —— 两种都接住。
      只属于死格 = `owners[tok] - dead` 为空；仍然属于某个**活格**的词照旧是"抢"。
    """
    if tok == EMPTY_TOKEN:
        # 空引号：**任何格子都吃得下，代价 0**。它是"这一格这一行没有值"的占位，
        # 不是值本身 —— 认领它 = 那一格停在空引号那里，后面的值不许前移。
        # ⚠ 必须在 `limited`（"只能从这些里选"）之前：那是一条硬规矩，但空引号
        #   根本不参与"值在不在候选里"这件事，不拦它。
        return COST_OWN
    if tok in allowed:
        return COST_OWN
    if limited:
        return None
    foreign = bool(owners.get(tok)) and bool(owners[tok] - {si} - set(dead))
    if not allowed:
        #: 这一格**随便写**：谁的词都能进来，只是"占了别人写明的候选"贵一点
        return COST_BORROW if foreign else COST_UNKNOWN
    if foreign:
        return COST_STOLEN            # 封闭枚举去抢别人写明的候选：几乎总是错的
    if dead and owners.get(tok) and not (owners[tok] - set(dead)):
        #: 这个词**只**属于被空引号废掉的那几格 —— 那一格这一行没有值，它的候选
        #: 已经**没人要**了。按"候选外"（5）计价的话，多值格宁可空着
        #: （0 + 晾着 3 = 3 < 5）也不接它：`show lan "" base_swimsuit happy`
        #: 里 `base_swimsuit` 明明该按位置落到下一格，却被和 `happy` 一起晾着
        #: （2026-10-18 第四轮攻击修）。按"谁都不认识的词"（2）计价：
        #:   必填格 2 < 空着 7 + 晾着 3；多值格 2 < 空着 0 + 晾着 3 —— 两头都接住。
        #: ⚠ 只在**有死格**的行生效（`dead` 非空），普通行一个数都不动。
        return COST_UNKNOWN
    return COST_OFFLIST               # 封闭枚举吃了候选外的词 = "值不在候选里"


def _span_hit(tokens, a: int, b: int, allowed) -> bool:
    """
    `tokens[a:b]` 整段**能不能算某一格的候选值**（两种拼法都认）：

      · `' '.join` —— 候选本身含空格（真机立绘的 `base hands down`）；
      · `''.join` —— 候选是一个"连写"的词，而它被切成了几段
        （`abcd` 切成 `ab`+`cd`、`小明泳装` 切成 `小明`+`泳装`）。
        没有这一条，切得越细反而越认不出整块候选 —— 那是"分词"与"路由"
        互相打架的老病根。
    """
    if not allowed or b <= a:
        return False
    if b - a == 1:
        return tokens[a] in allowed
    if ' '.join(tokens[a:b]) in allowed:
        return True
    return ''.join(tokens[a:b]) in allowed


def _show_token(tok) -> str:
    """一个词给**人看**的样子：空引号的占位符显示成 `""`（不是 `\\x00`）。"""
    return '""' if tok == EMPTY_TOKEN else str(tok)


def _span_text(tokens, a: int, b: int, allowed) -> str:
    """
    `tokens[a:b]` 这一格**显示/写回**用的值。

    默认是"原样的几个词，用空格连起来"；但如果整段正好是某一格写着的候选
    （含把连写的词拼回去），给的就是**那个候选** —— 否则 `base`+`_swimsuit`
    会在界面上显示成 `base _swimsuit`，而人写的明明是 `base_swimsuit`。

    ⚠ 空引号的占位符**不算值**（2026-10-18）：它只占位置，滤掉之后这一格
      的值就是空串 —— 于是界面显示空格子、卡片读出来是空，谁都不会把它
      当成一个属性名写进 `.rpy`。
    """
    parts = [t for t in tokens[a:b] if t != EMPTY_TOKEN]
    if not parts:
        return ''
    if len(parts) > 1 and allowed:
        joined = ' '.join(parts)
        if joined in allowed:
            return joined
        tight = ''.join(parts)
        if tight in allowed:
            return tight
    return ' '.join(parts)


__all__ = ['EMPTY_TOKEN', 'COST_OWN', 'COST_UNKNOWN', 'COST_BORROW',
           'COST_OFFLIST', 'COST_STOLEN', 'COST_EXTRA', 'EMPTY_SLOT',
           'SKIP_WORD', 'SKIP_JUNK', 'MISS_KW', 'MISS_KW_SKIP',
           '_allowed_of', '_owners_of', '_tok_cost', '_span_hit',
           '_show_token', '_span_text']
