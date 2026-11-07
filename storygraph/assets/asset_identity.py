"""
storygraph.assets.asset_identity
================================
**"这张图是谁"的唯一真源**：把一行图片资产解析成
`Identity(kind, tag, attrs, display, source, showable)`。

为什么必须有它（2026-10-30 用户实测的 P0）
------------------------------------------
用户原话：

> 立绘怎么一堆单独的分类，什么 relaxing，resigned —— 这不都是凌的表情吗
> 只有文件名是角色名的可以匹配；直接放角色那个头像图就能动

根因不是"界面没做"，而是**没有"图像 → 角色/属性"的唯一真源**：
旧实现只看资源表「标签」列，Tag 空的行（全部 `cc_preview/<角色>/<表情>.png`
副本、头像）就只剩资产 ID，于是：

  · 每个表情被当成一个独立角色分类（110 个单图分类）；
  · 选中预览图写出非法的 `show refusing`（引擎里没有这张图）。

真实项目里**路径本身就把答案写着**：`<任意目录>/<角色 tag>/<表情>.png`。
所以这里只做一件事：**按字面量证据**（映射文件 → 资源表 Tag 列 → 路径目录段
→ 文件名 stem）判断身份；**认不出就 `unknown`，绝不猜**（猜一个名字比
"不知道"坏得多 —— 会静默产出一条游戏里找不到图的 `show`）。

判定顺序（契约 §1.1，只认字面量）
----------------------------------
顺序的**唯一真源**是 `core.authority.IDENTITY_ORDER`（层号见 `AUTHORITY`）：

① **映射精确覆盖**（asset ID / 项目相对路径）—— 人指着这一张说"它叫这个"，
   最高级别的人为意图（可以改项目声明，但只对这一张生效，且要留痕）；
② **项目自己声明过的图像名**（`image 名:` / ATL 块 / 脚本合并进来的
   `_from_script` 行）—— 名字是项目 `.rpy` 里的字面量，直接 `showable=True`；
③ 映射 glob 规则（按数组顺序，先中先得）—— **只补空缺**：不许改 ①②
   （§六 6.2「个体显式 > 批量规则」）；
④ 资源表「Tag」列（是**已知角色 tag** 才算 sprite；非角色词走 kind 判定）
⑤ 路径**目录段**命中已知角色 tag：`.../<tag>/<表情>.png`
⑥ 文件 stem 恰等于已知角色 tag（`.../ling.png`）
⑦ `unknown`（不猜，`showable=False`）

> 2026-10-31 之前 ①② 是**一个整体**且在声明之前，于是一条 `game/**` 的
> glob 就能把项目自己声明过的名字改掉；现在拆成两层、声明夹在中间。

kind 判定
---------
`kind ∈ sprite / preview / avatar / bg / cg / ui / other / unknown`。

  · 映射指定 > tag 词表（bg/cg/ui）> 角色 tag（sprite）；
  · **通用"副本检测"**（不写死 `cc_preview`）：同 tag、同 stem 还**另有一行
    资源表 Tag 非空** → 这张是副本 → `preview`；目录名里带
    `preview / thumb / thumbnail` 也算（同样是通用词，不是项目名）；
  · avatar 只认映射指定或**目录段明确写着 `avatar`**；宁可 `unknown`。

`showable` 是"能不能直接出现在 `show <spec>` 里"：真立绘 / 真背景 / 真-CG /
真界面图层为真；预览图、头像、认不出的图一律为假（界面据此拒绝或自动换真图）。

**禁止**在本模块 import 任何 UI 框架（契约 §1.1：纯函数层）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Mapping, Optional

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import NON_ACTOR_TAGS
from storygraph.assets.asset_dimensions import row_values
from storygraph.assets.asset_registry_constants import (
    CHARACTER_CONTAINER_WORDS, _PREVIEW_WORDS)
from storygraph.assets.atl_block import is_atl_row
#: 身份词汇与"基础读取"（`KINDS` / `Identity` / 路径归一 / 副本索引）挪去了
#: 叶子模块 `identity_core`（2026-11-01 解 `assets` 三模块环）：`asset_mapping`
#: 与 `mapping_excel` 也要这几个名字，留在这里就又是一条指回来的边。
#: **这里原样再导出** —— 外面一百多处 `from ...asset_identity import Identity`
#: 一行都不用改；带下划线的四个是同一个"基础读取"层，跨模块照旧用下划线名。
from storygraph.assets.identity_core import (
    KINDS,
    SHOWABLE_KINDS,
    Identity,
    _iter_rows,
    _row_relpath,
    _stem_of,
    asset_id_of,
    build_copy_index,
    normalize_relpath,
)
from storygraph.assets.identity_core import (
    under_game_images as _core_under_game_images,
)
#: 映射层（精确覆盖 / glob 规则的**只读取**，不碰读盘与写回）。
#: 2026-11-01 起可以是顶层 import 了：初稿生成（唯一需要反过来 import 本模块的
#: 地方）已经抬到 `assets.mapping_draft`，所以 `asset_mapping` 不再指回这里，
#: 那条边不再是环（见 §83）。原来是两处函数体内 import —— 只是因为环。
from storygraph.assets.asset_mapping import (mapping_cached,
                                             resolve_exact_image,
                                             resolve_rule_image)
#: `.rpy` 的 Python 块切分与"直接声明过的图像名"扫描拆到了叶子模块
#: （2026-11-04：`asset_identity.py` 撞 1500 行技术债红线；这两件事
#: 也不再和"图片是谁"耦合）。这里**原样再导出**，老 import 一行不用改。
from storygraph.assets.rpy_blocks import python_blocks as _python_blocks
from storygraph.assets.image_declarations import image_statement_words


#: 来源标记（对照表要显示"这个结论是谁给的"）。取值与层号的对应见
#: `core.authority.SOURCE_LAYER` —— 新增一个 source 必须同时登记那一层。
SOURCES = ('declared', 'mapping', 'mapping-rule', 'tag-column', 'folder',
           'stem', 'unknown')

#: 副本/缩略图目录的**通用词**（不是项目名！）。只用来判 kind=preview；
#: 判 tag 仍然只靠目录段命中的已知角色 tag。
#: ⚠ 与扫描器共用**同一份**（`asset_registry_constants._PREVIEW_WORDS`）：
#: 两处各写一个集合，迟早漂移成"扫描认预览、身份不认"。
_PREVIEW_DIR_WORDS = frozenset(_PREVIEW_WORDS)

#: 头像目录的**通用词**：只有目录段明确写着它才给 kind=avatar（宁可 unknown）。
_AVATAR_DIR_WORDS = frozenset({'avatar', 'avatars'})

#: 名字两侧的引号/空格（角色表里显示名常写成 `「凌跃捷」`）。
_QUOTE_CHARS = '「」『』""''\u3000 '

#: 别名分隔符（角色表 `Aliases` 一格里可写多个：`MOM|母亲|老妈`）。
_ALIAS_SEP = re.compile(r'[|｜,，、;；\s]+')

#: 目录名分词（与 `asset_registry_scan._segment_tokens` 同口径）。
_SEG_SPLIT = re.compile(r'[_\-.()\[\]\s]+')


# ---------------------------------------------------------------------------
#  路径 / 行 的基础读取
# ---------------------------------------------------------------------------


def _dirs_of(relpath: str) -> list:
    """项目相对路径 → 目录段（小写；不含文件名）。"""
    parts = PurePosixPath(str(relpath or '')).parts
    return [str(p).lower() for p in parts[:-1]]


def _tokens(segment: str) -> list:
    """目录段 → 词元（`cc_preview` → `['cc', 'preview']`）。"""
    return [t for t in _SEG_SPLIT.split(str(segment or '').lower()) if t]


def _has_dir_word(relpath: str, words) -> bool:
    """路径的**任一目录段**含 `words` 里的词（按词元精确匹配）。"""
    for seg in _dirs_of(relpath):
        toks = _tokens(seg)
        if seg in words or any(t in words for t in toks):
            return True
    return False


def used_sprite_attrs(project_root) -> dict:
    """
    **剧本里实际用过的**属性词 → `{tag: set(attrs)}`（`show <tag> a b` 统计）。

    为什么需要这一路（2026-11-04，用户："aa 明明就有定义，你这里怎么没扫出来"）：

    `aa` 在项目里是**界面换装循环的代号**，写在 `init python` 的数据结构里：

        CC_OUTFIT_CYCLE = {"lan": ["aa", "base_hand_up", "clothes2"], …}
        CC_OUTFIT_LABEL = {"aa": "普通", …}

    它不是 `layeredimage lan` 的 `attribute`（真机 `definitions.rpy:381` 里
    只有 21 个表情 + `clothes2` / `base_hand_up`），资源表里也没有登记 ——
    于是工具判定"没有这个属性"，而剧本里 `show lan aa …` 用了 **922 处**。

    对工具来说，**"项目自己一直在用"就是事实**（跟"项目自己声明过"同一档证据：
    都是作者写出来的）。所以这一路只做一件事：让"写了两百遍的词"不被判成
    不存在。它不是猜测 —— 判据是**字面出现**，不是模糊匹配。

    ⚠ 边界：一个**真的写错了**的词如果已经在剧本里，这里也会放过它。
    那是"既有事实"而不是"新错误" —— 新写错的词还没进剧本，照样会报。

    没有项目根 / 读不出来 → `{}`（不猜）。
    """
    root = str(project_root or '')
    if not root:
        return {}
    try:
        from storygraph.parsing.discover import discover_scripts
        files = list(discover_scripts(root))
    except Exception as e:                      # noqa: BLE001 - 读不出来就没这一路
        swallow('assets.asset_identity.used_sprite_attrs', e)
        return {}
    out: dict = {}
    # ⚠ `[^:\n]*`（**不是** `[^:]*`）：后者包含换行，会把下一行的台词
    # 当成属性词吃进来（真机实测：`lan` 的属性里混进了十几句对白）。
    show_re = re.compile(r'^\s*show\s+([A-Za-z_]\w*)([^:\n]*)$', re.M)
    for fp in files:
        try:
            text = Path(fp).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for m in show_re.finditer(text):
            tag = m.group(1)
            rest = m.group(2) or ''
            # `show lan happy at i11` → 只取 `at` 之前的属性词
            rest = rest.split('#')[0]                  # 行尾注释不算属性
            rest = re.split(r'\s+at\s+', rest, maxsplit=1)[0]
            words = {w for w in rest.split()
                     if w and not w.startswith(('@', '#')) and w != 'with'}
            if words:
                out.setdefault(tag, set()).update(words)
    return out


def project_literal_words(project_root) -> set:
    """
    项目在 `init python` / `python` 块里**定义过**的代号词（`{str}`，与 tag 无关）。

    为什么需要这一路（2026-11-04 用户："`aa` 明明是服装代号，这你也查不到？"）：

        # game/charactercenter/charactercenter.rpy
        init python:
            CC_OUTFIT_CYCLE = {"lan": ["aa", "base_hand_up", "clothes2"], …}
            CC_OUTFIT_LABEL = {"aa": "普通", …}

    `aa` 是**界面换装循环的代号**（"普通"）：它不是 `layeredimage lan` 的
    `attribute`，资源表里也没登记 —— 工具只看那两路就会说"没有这个属性"。

    ⚠⚠ 判据必须落在**定义位置**，不能落在**使用位置**（第一版用的是"剧本里
    `show` 过就算认识"，用户当场否掉："万一剧本错了呢？万一我用了 `ba` 呢？
    这你也当认识？太粗糙了"）。所以：

      · 只看 `init python:` / `python:` 块里的**字面量**；
      · 只收 **dict 的键** 与 **list / tuple / set 的字符串元素**
        —— 不收 dict 的值（那多半是中文说明，`{"aa": "普通"}` 里"普通"
        不该因此变成合法属性名）；
      · **剧本里 `show` 过什么都不算数**。

    读不出来 → `set()`（不猜）。
    """
    root = str(project_root or '')
    if not root:
        return set()
    try:
        from storygraph.parsing.discover import discover_scripts
        files = list(discover_scripts(root))
    except Exception as e:                      # noqa: BLE001 - 读不出来就没这一路
        swallow('assets.asset_identity.project_literal_words', e)
        return set()
    words: set = set()
    for fp in files:
        try:
            text = Path(fp).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for block in _python_blocks(text):
            words |= _literal_words_of(block)
        # 顶层 `define OUTFITS = [...]` 与 `init python` 里的同一张表等价
        # （2026-11-04 地狱测试：只扫 python 块时，define 写法的自定义结构被漏掉）。
        words |= _define_literal_words(text)
    return words


def declared_sprite_attrs(project_root) -> dict:
    """
    项目 `layeredimage` 声明过的**全部属性名** → `{tag: [属性, …]}`。

    与 `declared_sprite_words` 的分工：那一张是**反查表**
    `{tag: {图片 stem: 属性名}}`（回答"这张图的文件名该写哪个词"），两个
    属性指到同一张图时会互相覆盖；提示层要的是**属性名本身**，所以从
    `LayeredImage.all_attrs()` 直接取（2026-11-04 地狱测试：`base` 与
    `符_77` 都写 `'base'`，反查表只剩 `base`，卡片就误报 `符_77`）。

    没有项目根 / 读不出来 → `{}`（不猜）。
    """
    root = str(project_root or '')
    if not root:
        return {}
    try:
        from storygraph.story.layered_model import (
            declared_attr_names, load_layer_model_cached)
    except ImportError:                         # 判定层被裁掉的极端打包
        return {}
    model = load_layer_model_cached(root)
    try:
        return {str(tag): list(names)
                for tag, names in (declared_attr_names(model) or {}).items()
                if names}
    except Exception as e:                      # noqa: BLE001 - 结构怪就当没声明
        swallow('assets.asset_identity.declared_sprite_attrs', e)
        return {}


#: 一个"属性代号"该长什么样：纯标识符、不超过 32 字。
_WORD_RE = re.compile(r'[A-Za-z_]\w{0,31}')


def _literal_words_of(code: str, *, mode: str = 'exec') -> set:
    """一段 Python 代码里，**作为 dict 键 / 容器元素**出现的字符串字面量。

    2026-11-04 地狱测试把它从"只看顶层 `Assign`"改成**通用遍历**：
    定义藏在 `if` / `for` / `with` / `try` / 函数体 / 类体 / 推导式里时，
    旧实现一个词都收不到 → 卡片把项目自己定义的词判成"没有这个属性"。
    """
    import ast
    try:
        tree = ast.parse(code, mode=mode)
    except SyntaxError:                         # 片段不是完整语法 → 不猜
        return set()
    out: set = set()

    def visit(node, *, as_key_or_item: bool):
        if isinstance(node, ast.Expression):
            visit(node.body, as_key_or_item=False)
            return
        if isinstance(node, ast.Module):
            for st in node.body:
                visit(st, as_key_or_item=False)
            return
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if k is not None:
                    visit(k, as_key_or_item=True)
            for v in node.values:
                visit(v, as_key_or_item=False)   # 值不当词（多是中文说明）
            return
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for e in node.elts:
                visit(e, as_key_or_item=True)
            return
        if isinstance(node, ast.DictComp):
            visit(node.key, as_key_or_item=True)
            visit(node.value, as_key_or_item=False)
            for gen in node.generators:
                visit(gen, as_key_or_item=False)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            visit(node.elt, as_key_or_item=True)
            for gen in node.generators:
                visit(gen, as_key_or_item=False)
            return
        if as_key_or_item and isinstance(node, ast.Constant) \
                and isinstance(node.value, str):
            word = node.value.strip()
            # 只收**像标识符的短词**：`aa` / `clothes2` / `base_hand_up` ✓；
            # `bgm/ed.mp3`、中文说明、长句 ✗（它们也在 `init python` 的容器里，
            # 但那是资源路径/文案，不是属性代号）。
            if _WORD_RE.fullmatch(word):
                out.add(word)
            return
        # 其余节点（If / For / With / Try / FunctionDef / ClassDef / Call…）
        # 一律下钻：定义位置不因包在控制流/函数体里就作废。
        for child in ast.iter_child_nodes(node):
            visit(child, as_key_or_item=False)

    visit(tree, as_key_or_item=False)
    return out


#: `define NAME = <表达式>` / `default NAME = <表达式>`（Ren'Py 顶层语句）。
#: 名字允许点号（`config.name`）；`-1` 之类的 init 偏移也允许。
_DEFINE_RE = re.compile(
    r'^[ \t]*(?:define|default)[ \t]+(?:[-+]?\d+[ \t]+)?'
    r'[\w.]+[ \t]*=[ \t]*(?P<expr>.*)$')


def _bracket_delta(line: str) -> int:
    """一行里括号的净增量（忽略行尾注释；字符串里的括号不算）。"""
    depth = 0
    quote = ''
    escaped = False
    for ch in line:
        if quote:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == quote:
                quote = ''
            continue
        if ch == '#':
            break
        if ch in ('"', "'"):
            quote = ch
        elif ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
    return depth


def _define_literal_words(text: str) -> set:
    """
    顶层 `define` / `default` 的字面量值里的词（2026-11-04 地狱测试）。

    为什么算定义位置：`define OUTFITS = ["aa", …]` 与 `init python` 里的
    `OUTFITS = [...]` 是同一件事，只是 Ren'Py 语句写法不同。只收容器里的
    **键/元素**（`_literal_words_of` 的老判据），所以
    `define gui.name = "故事"` 这种裸字符串不会污染词表。
    多行表达式按括号配平往后收。
    """
    lines = text.split('\n')
    words: set = set()
    i = 0
    while i < len(lines):
        m = _DEFINE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        expr_lines = [m.group('expr')]
        depth = _bracket_delta(m.group('expr'))
        while depth > 0 and i + 1 < len(lines):
            i += 1
            expr_lines.append(lines[i])
            depth += _bracket_delta(lines[i])
        try:
            words |= _literal_words_of('\n'.join(expr_lines), mode='eval')
        except (SyntaxError, ValueError) as e:
            swallow('assets.asset_identity.define_literal', e)
        i += 1
    return words


def _under_game_images(relpath: str) -> bool:
    """
    这条路径在 `game/images/` 下面吗（Ren'Py **自动注册 image name** 的根）。

    判据本体在 `identity_core.under_game_images`（2026-11-07 起同一份给
    `asset_mapping.identity_from_entry` 用：映射的 `rules` 层不许替引擎答应
    "这个名字能 show"，两边口径必须同一份）。这里只是保留本模块的历史名字
    （`test_asset_identity` / 调用处都按 `_under_game_images` 找）。
    """
    return _core_under_game_images(relpath)


def is_under_sprite_container(relpath: str) -> bool:
    """
    这条路径的**目录段**里写着立绘容器词吗（`characters` / `立绘` / `sprite`…）。

    与 `path_kind`（以及扫描侧的 `_category_by_dir`）的分工：
      · 那一层认不出时会**退到位置猜测**（`images/<x>/…` 也可能是角色目录），
        所以 `game/images/nbg/bg1.1.png`（42 张背景）被判成了 `character`；
      · 本判据**只认写出来的目录名**，用来回答"这真的是立绘素材吗"，
        而不是"它可能是立绘"。

    真机用途（2026-11-03，用户报"qing 的立绘认不出来"）：
    `game/images/characters/qing/normal.png` 是**真立绘**（目录写着 characters），
    而它对应的角色（`q` /「青」）在角色表里 `立绘 tag` 是空的 —— 于是
    卡片里写 `qing base` 就被判"不是已知的角色立绘标签"。有了这条判据，
    提示才能说"它确实有立绘，只是没有角色认领"，而不是把用户推向"资源映射"。
    """
    return _has_dir_word(relpath, CHARACTER_CONTAINER_WORDS)


# ---------------------------------------------------------------------------
#  词表 / 角色表
# ---------------------------------------------------------------------------

def kind_from_tag_word(word: str) -> str:
    """
    一个 tag 词 → 它能代表的 kind（认不出 = ''）。

    只查**共享词表**（`core.vocabulary.NON_ACTOR_TAGS` 与扫描模块的
    bg/cg/ui 词表，都是全项目共用的那一份），不为任何具体项目加分支。
    """
    low = str(word or '').strip().lower()
    if not low:
        return ''
    if low in ('bg', 'bg_', 'background', 'backdrop', 'scene'):
        return 'bg'
    if low in ('cg', 'event', 'cutscene', 'illust', 'illustration', 'gallery'):
        return 'cg'
    if low in _PREVIEW_DIR_WORDS:
        return 'preview'
    if low in _AVATAR_DIR_WORDS:
        return 'avatar'
    if low in NON_ACTOR_TAGS:
        return 'ui'
    return ''


def _clean_display(text) -> str:
    """角色显示名去掉两侧的引号/空格（`「凌跃捷」` → `凌跃捷`）。"""
    return str(text or '').strip().strip(_QUOTE_CHARS).strip()


def _split_aliases(raw) -> tuple:
    """角色别名格 → 别名元组（保序去重；认不出分隔符就当一个）。"""
    words = [w for w in _ALIAS_SEP.split(str(raw or '')) if w]
    out = []
    for w in words:
        if w not in out:
            out.append(w)
    return tuple(out)


def known_characters(registry) -> dict:
    """
    资源表「角色」→ `{tag: {'tag','display','aliases','char_id','avatar'}}`。

    tag 取 `ImageTag`（`show` 要写的那一列）；老表没有该列时退回角色变量名
    （`dsl.Symbols.actor_image_tag` 的同一口径：`ImageTag or ID`）。
    **非角色 tag（bg/cg/ui…）一律排除** —— 它们不可能是台上的人。

    这是"已知角色 tag"的第一来源（另外两个：映射文件 `characters`、
    引擎事实里带属性的 tag；见 `known_tags_of` / `engine_fact_tags`）。
    """
    out: dict = {}
    chars = getattr(registry, 'characters', None) or {}
    if not isinstance(chars, Mapping):
        return out
    for cid, row in chars.items():
        row = row if isinstance(row, Mapping) else {}
        var = str(cid or '').strip()
        tag = str(row.get('ImageTag') or '').strip() or var
        if not tag or tag.lower() in NON_ACTOR_TAGS:
            continue
        entry = {
            'tag': tag,
            'char_id': var,
            'display': _clean_display(row.get('Name')),
            'aliases': _split_aliases(row.get('Aliases')),
            'avatar': '',
        }
        prev = out.get(tag)
        if prev is None:
            out[tag] = entry
            continue
        # 同一个 tag 有多个角色变量（别名角色）：只补空缺，不覆盖已有显示名。
        if not prev.get('display') and entry.get('display'):
            prev['display'] = entry['display']
        merged = list(prev.get('aliases') or ())
        for alias in entry.get('aliases') or ():
            if alias not in merged:
                merged.append(alias)
        prev['aliases'] = tuple(merged)
    return out


def _norm_chars(known_chars) -> dict:
    """`known_chars` 两种形态都认：`{tag: 显示名}` 或 `{tag: {...}}`。"""
    if not known_chars:
        return {}
    if not isinstance(known_chars, Mapping):
        return {}
    return dict(known_chars)


def _display_of(known_chars, tag: str) -> str:
    """已知角色表里这个 tag 的显示名（认不出 = ''；**不猜**）。"""
    chars = _norm_chars(known_chars)
    if not chars or not tag:
        return ''
    entry = chars.get(tag)
    if entry is None:
        for key, value in chars.items():
            if str(key).lower() == str(tag).lower():
                entry = value
                break
    if isinstance(entry, Mapping):
        return _clean_display(entry.get('display') or entry.get('Name')
                              or entry.get('name') or '')
    return _clean_display(entry)


def _tag_map(known_tags, known_chars=None) -> dict:
    """已知 tag 集合 → `{小写: 原始写法}`（大小写不敏感匹配，回报原写法）。"""
    out: dict = {}
    sources = []
    if known_tags:
        sources.append(known_tags)
    if isinstance(known_chars, Mapping):
        sources.append(known_chars.keys())
    for source in sources:
        try:
            items = source.keys() if isinstance(source, Mapping) else source
        except AttributeError:
            continue
        for tag in items or ():
            word = str(tag or '').strip()
            if not word or word.lower() in NON_ACTOR_TAGS:
                continue
            out.setdefault(word.lower(), word)
    return out


def known_tags_of(registry, mapping=None) -> set:
    """
    **项目已知角色 tag**：characters 表（`ImageTag or ID`）+ 映射 `characters`。

    第三个来源（引擎事实里带属性的 tag）在 `engine_fact_tags` 里单独取 ——
    它要读文件，跟这张表分开，调用方按需合并（`app.picker.pick_context`
    就是三路合并后的结果）。
    """
    tags = set(known_characters(registry))
    chars = (mapping or {}).get('characters') if isinstance(mapping, Mapping) else None
    if isinstance(chars, Mapping):
        for tag in chars:
            word = str(tag or '').strip()
            if word:
                tags.add(word)
    return {t for t in tags if t.lower() not in NON_ACTOR_TAGS}


def engine_fact_tags(project_root) -> set:
    """
    引擎事实（`<项目>/storygraph.engine_facts.json`）里**带属性的 tag**。

    为什么它算"已知角色 tag"：引擎自己枚举出了这个 tag 的属性
    （`layeredimage` 的 group）—— 能带属性，说明它是立绘/图像 tag，
    不是背景那种单张图。没有事实文件 / 事实不可用时返回空集（**不猜**）。
    """
    root = str(project_root or '')
    if not root:
        return set()
    try:
        from storygraph.core import engine_facts as EF
        path = EF.facts_path(root)
        if not path.is_file():
            return set()
        facts = EF.loads(path.read_text(encoding='utf-8'))
        ok, _why = EF.is_collectable(facts)
        if not ok:
            return set()
        attrs = EF.value_of(facts, 'attributes', {}) or {}
        tags = EF.value_of(facts, 'tags', []) or []
        if not isinstance(attrs, Mapping):
            return set()
        out = set()
        for tag in tags:
            name = str(tag or '').strip()
            if not name or name.lower() in NON_ACTOR_TAGS:
                continue
            rows = attrs.get(name) or []
            if any(isinstance(r, Mapping) and str(r.get('attribute') or '').strip()
                   for r in rows):
                out.add(name)
        return out
    except Exception as e:                      # noqa: BLE001 - 事实坏了不该组不出图
        swallow('assets.asset_identity.engine_fact_tags', e)
        return set()


# ---------------------------------------------------------------------------
#  副本检测（通用：不认项目名）
# ---------------------------------------------------------------------------


def declared_sprite_words(project_root) -> dict:
    """
    项目 `.rpy` 里的 `layeredimage` → `{tag: {图片文件名: 声明的属性名}}`。

    为什么它是"已知角色 tag"的**第四路**来源（2026-11-02 用户：`qing` 明明
    有立绘，工具却说"没有这个角色"、预览图也导不进来）：

      · 角色表（`characters.xlsx`）登记的是**会说话的人**。`qing` 在剧本里
        由 `q` 出声，自己只有一个 `layeredimage qing:` —— 前三路（角色表 /
        映射 `characters` / 引擎事实）里都没有她，于是她的立绘、预览图、头像
        全被归成"其他图片"，选择器里连"立绘：qing"这一组都不存在；
      · 而**项目自己声明过**的东西就是事实：能写 `show qing …`、带属性、
        有 `image_format`。判据与引擎事实同一条，只是不必先采过引擎。

    值那一层还回答另一个问题："这张图的文件名该写成哪个属性词" ——
    `attribute curious: 'stare'` 里，`show qing stare` 引擎不认，
    `show qing curious` 才是属性名（反查表在
    `story.layered_model.attribute_words`，这里只做取用与过滤）。

    没有项目根 / 读不出来 → `{}`（**不猜**）。
    """
    root = str(project_root or '')
    if not root:
        return {}
    try:
        from storygraph.story.layered_model import load_layer_model_cached
    except ImportError:                         # 判定层被裁掉的极端打包
        return {}
    model = load_layer_model_cached(root)
    words = getattr(model, 'words', None)
    if not isinstance(words, Mapping):
        return {}
    return {str(tag): dict(table) for tag, table in words.items()
            if isinstance(table, Mapping)}


def declared_sprite_tags(project_root) -> set:
    """
    项目**自己声明过 `layeredimage`** 的 tag（= 项目自己的立绘名单）。

    见 `declared_sprite_words`：这一路补的是"角色表没登记、但项目确实有立绘"
    的人（`qing`）。非角色 tag（`ui`/`bg`…）由 `_actor_words` 统一筛掉。
    """
    return set(declared_sprite_words(project_root))


def _actor_words(words) -> dict:
    """属性词表 → 只留**角色** tag（与 `known_tags` 同一把筛子，口径不许分叉）。"""
    if not isinstance(words, Mapping):
        return {}
    return {str(tag): dict(table) for tag, table in words.items()
            if str(tag or '').strip()
            and str(tag).strip().lower() not in NON_ACTOR_TAGS
            and isinstance(table, Mapping)}


def _is_copy(tag: str, stem: str, copy_index) -> bool:
    """这张（Tag 空的）图是不是"另一行 Tag 非空"行注册过的同 tag+同 stem 副本。"""
    if not tag or not stem or not copy_index:
        return False
    pool = copy_index.get(str(tag).strip().lower()) or ()
    return str(stem).strip().lower() in pool


# ---------------------------------------------------------------------------
#  kind 的弱线索（只在 Tag 列 / 目录段都给不出结论时才用）
# ---------------------------------------------------------------------------

def path_kind(relpath: str) -> str:
    """
    路径 → 弱 kind 线索（`bg` / `cg` / `ui`，认不出 = ''）。

    复用扫描器已经逐条对齐过真实项目的 `_detect_category`
    （就是 `test_asset_categorize.py` 钉住的那套规则：目录词元 > 文件名
    线索 > other）。**同包内**复用，不另造第二套目录词表。
    只有 `background/cg/ui` 会变成 kind；`character` 没有已知 tag 时
    **不猜是谁**（交给上层 unknown）。
    """
    rel = str(relpath or '')
    if not rel:
        return ''
    try:
        from storygraph.assets.asset_registry_scan import _detect_category
        cat = _detect_category(PurePosixPath(rel))
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_identity.path_kind', e)
        return ''
    return {'background': 'bg', 'cg': 'cg', 'ui': 'ui'}.get(cat, '')


# ---------------------------------------------------------------------------
#  核心：一行的身份
# ---------------------------------------------------------------------------

def resolve_image(row: dict, *, relpath: str = '', known_tags=None,
                  known_chars: Mapping | None = None, mapping=None,
                  rows=None, copy_index=None,
                  sprite_words=None) -> Identity:
    """
    一行图片资产 → `Identity`（判定顺序见模块 docstring）。

    参数
    ----
    row          资源表的一行（`ID/Path/Tag/Attributes/Value/...`）。
    relpath      项目相对路径；不给就用 `row['Path']` 现归一。
    known_tags   已知角色 tag（`app.picker.pick_context` 会给四路合并后的集合）。
    known_chars  `{tag: 显示名}` 或 `{tag: {'display': ...}}`（只要显示名）。
    mapping      映射文件数据（`asset_mapping.load_mapping` 的 dict）。
    rows         全部图片行（用于**副本检测**）；支持 dict / list。
    copy_index   `build_copy_index(rows)` 的结果；批量调用时传它可避免 O(n²)。
    sprite_words `{tag: {图片文件名: 声明的属性名}}`（`project_context` 的
                 `sprite_words`）—— 给了就把 sprite 的属性词换成项目
                 `layeredimage` 里**声明过的写法**（见 `_to_declared_words`）。
    """
    ident = _resolve_image_raw(row, relpath=relpath, known_tags=known_tags,
                               known_chars=known_chars, mapping=mapping,
                               rows=rows, copy_index=copy_index)
    return _to_declared_words(ident, sprite_words)


def _to_declared_words(identity, sprite_words) -> Identity:
    """
    sprite 的属性词 → 项目 `layeredimage` 里**声明过的写法**。

    真实项目（2026-11-02）：`layeredimage qing` 写着 `attribute curious:
    'stare'`，于是 `stare.png` 这张图能写的是 `show qing curious`；
    按文件名硬写 `show qing stare` 引擎不认（`stare` 只是文件名）。

    ⚠ **只改 `kind='sprite'`**：预览图/头像的 `attrs` 还要拿去和**行里的
    原词**（`Attributes` 列 / 文件名）对表（`find_real_sprite`）—— 换了词就
    找不到那张真立绘；换成真立绘之后重新解析**那一行**时会再走一次这里。
    没声明过（表里没有这个 tag / 这个词）就一个字都不动。
    """
    if not sprite_words or identity is None or identity.kind != 'sprite':
        return identity
    table = sprite_words.get(str(identity.tag or ''))
    if not table:
        return identity
    out, seen, changed = [], set(), False
    for word in identity.attrs or ():
        new = table.get(str(word).strip().lower()) or word
        changed = changed or new != word
        if str(new).strip().lower() in seen:
            continue
        seen.add(str(new).strip().lower())
        out.append(new)
    if not changed:
        return identity
    return replace(identity, attrs=tuple(out))


def _resolve_image_raw(row: dict, *, relpath: str = '', known_tags=None,
                       known_chars: Mapping | None = None, mapping=None,
                       rows=None, copy_index=None) -> Identity:
    """`resolve_image` 的判定主体（不含"属性词换成声明名"那一步）。"""
    row = dict(row or {})
    aid = asset_id_of(row)
    rel = normalize_relpath(relpath or str(row.get('Path') or ''))
    tag_map = _tag_map(known_tags, known_chars)
    attrs = tuple(row_values(row))

    # 判定顺序 = `core.authority.IDENTITY_ORDER`（**改这里必须同步那张表**）。
    #
    # 2026-10-31 改过一次（真源与优先级 §P0-1）：
    #   · 旧：映射（精确 + 规则）**整体**在最前 → 一条 `game/**` 的 glob
    #     就能把项目自己声明过的图像名改掉，而注释 ⓪ 明明写着"映射要治理的
    #     是『猜』，不是『项目自己声明过的』"——注释在第 553 行、映射在第 541 行，
    #     代码与意图相反；
    #   · 新：精确覆盖（点名的这一张）→ 项目声明 → glob 规则。
    #     判据：**规则只补空缺；要改就写精确覆盖**（§六 6.2）。

    # ① 映射的精确覆盖（`images` 表点名了资产 ID / 相对路径）
    hit = _mapping_layer(mapping, rel, aid, 'mapping-exact')
    if hit is not None:
        return _record_declared_override(_with_display(hit, known_chars),
                                         row, attrs, tag_map, known_chars,
                                         aid, rel)

    # ② 项目自己声明过的图像名（ATL 块 / 脚本合并行 / `layeredimage`）：名字
    # 是 `.rpy` 里的字面量 —— 不许因为"Tag 不在角色表里"就把一条**原本能用
    # 的 show** 封掉；也不许让 glob 规则把它改掉。
    if _is_declared_image(row):
        return _from_declared(row, attrs, tag_map, known_chars)

    # ③ 映射的 glob 规则（按数组顺序，先中先得）：只补上面两层没说话的行。
    hit = _mapping_layer(mapping, rel, aid, 'mapping-rule')
    if hit is not None:
        return _with_display(hit, known_chars)

    idx = copy_index
    if idx is None and rows is not None:
        idx = build_copy_index(rows)

    tag_col = str(row.get('Tag') or '').strip()
    if tag_col:
        return _from_tag_column(row, tag_col, rel, attrs, tag_map,
                                known_chars, idx, aid)
    return _from_path(row, rel, attrs, tag_map, known_chars, idx, aid)


def _mapping_layer(mapping, rel: str, aid: str, layer: str):
    """
    只取映射的**一层**（`mapping-exact` 或 `mapping-rule`）；没有就 `None`。

    为什么分两次调（而不是一次拿"最终赢家"）：项目声明那一层必须夹在
    两者**中间** —— 一次拿结果就没法在中间插队（§P0-1）。
    """
    if not isinstance(mapping, Mapping) or not mapping:
        return None
    try:
        if layer == 'mapping-exact':
            return resolve_exact_image(mapping, relpath=rel, asset_id=aid)
        return resolve_rule_image(mapping, relpath=rel, asset_id=aid)
    except Exception as e:                      # noqa: BLE001 - 映射坏了退到推断
        swallow('assets.asset_identity.resolve_image.mapping', e)
        return None


def _with_display(hit, known_chars):
    """映射没给显示名时，补上角色表的显示名（映射只写它要改的字段）。"""
    if not hit.display:
        hit = replace(hit, display=_display_of(known_chars, hit.tag))
    return hit


def _identity_value(identity) -> str:
    """一条身份的人话值（比较"值变没变"、写覆盖记录都用它）。"""
    parts = [str(getattr(identity, 'kind', '') or ''),
             str(getattr(identity, 'tag', '') or ''),
             ' '.join(str(a) for a in (getattr(identity, 'attrs', ()) or ()))]
    return ' '.join(p for p in parts if p).strip() or '（认不出）'


def _record_declared_override(result, row, attrs, tag_map, known_chars,
                              aid: str, rel: str):
    """
    精确覆盖赢的时候，如果这一行**项目自己声明过**，就留一条覆盖记录（P0-3）。

    为什么要多算一次：`resolve_image` 在①就返回了，根本不知道②会说什么。
    而"映射点名改掉了项目声明"正是最需要留痕的那一条（§七.1：覆盖可以改声明，
    但必须能说出原来是啥）。只多算**一次纯函数**（`_from_declared` 不读盘），
    且只在"这一行确实声明过"时才走 —— 轮不到的行一分钱不花。

    值一样就不记：那不是覆盖，是两层恰好说同一句话。
    """
    if not _is_declared_image(row):
        return result
    try:
        loser = _from_declared(row, attrs, tag_map, known_chars)
    except Exception as e:                      # noqa: BLE001 - 留痕失败不该拦判定
        swallow('assets.asset_identity.record_declared_override', e)
        return result
    if _identity_value(loser) == _identity_value(result):
        return result
    from storygraph.core.authority import Override

    record = Override(
        domain='identity',
        key=str(aid or rel or '').strip() or '（这张图）',
        winner_layer='mapping-exact',
        loser_layer='declared',
        winner_value=_identity_value(result),
        loser_value=_identity_value(loser),
        origin=str(rel or ''),
        match=(str(aid or ''),))
    return replace(result, overrides=tuple(result.overrides) + (record,))


def _is_declared_image(row: Mapping) -> bool:
    """
    这一行是不是项目 `.rpy` 里**字面量声明过**的图像名？

    三条证据（都来自项目数据，不是猜）：
      · `_atl` / `Notes` 里的 ATL 标记 → `image 名:` + 块体（贴图在块里）；
      · `_from_script` → 解析脚本时合并进来的 `image` 声明行；
      · `_layered` → `layeredimage <名>:` 声明。

    普通"磁盘扫描出来的图"这三条都没有 —— 所以这里不会把认不出的
    零散图片一股脑放行。
    """
    row = row or {}
    if row.get('_atl') or row.get('_from_script') or row.get('_layered'):
        return True
    try:
        return bool(is_atl_row(row))
    except Exception as e:                      # noqa: BLE001 - 标记坏了不该拦住选择
        swallow('assets.asset_identity._is_declared_image', e)
        return False


def _from_declared(row, attrs: tuple, tag_map: dict, known_chars) -> Identity:
    """
    声明过的图像名 → `Identity`（`showable=True`：名字在引擎里真实存在）。

    kind 仍按同一套词表判（`bg`/`cg`/`ui`/`sprite`/`other`），**不改 show 的
    合法性**；`tag` 就是声明名的第一个词、`attrs` 是剩下的词
    （`image logo blink:` → `logo blink`）。
    """
    tag_col = str(row.get('Tag') or '').strip()
    if not tag_col:
        # 名字都拆不出来：不编，按认不出处理。
        return Identity(kind='unknown', tag='', attrs=(), source='unknown',
                        showable=False)
    low = tag_col.lower()
    if low in tag_map:
        tag = tag_map[low]
        return Identity(kind='sprite', tag=tag, attrs=attrs,
                        display=_display_of(known_chars, tag),
                        source='declared', showable=True)
    kind = kind_from_tag_word(low) or 'other'
    return Identity(kind=kind, tag=tag_col, attrs=attrs,
                    source='declared', showable=True)


def _from_tag_column(row, tag_col: str, rel: str, attrs: tuple,
                     tag_map: dict, known_chars, copy_index, aid: str) -> Identity:
    """③ 资源表「Tag」列：是已知角色 tag → sprite；否则按词表判非角色 kind。"""
    low = tag_col.lower()
    if low in tag_map:
        tag = tag_map[low]
        # 预览副本 / 头像：**目录说了算，Tag 列说了不算**（2026-11-02）。
        # 表可能被手改过、也可能是旧扫描器写的（真机 159 行预览副本一度带着
        # 空 Tag，别家项目可能把真立绘的 tag 抄进去）；只要它在预览/头像目录里，
        # 它就**不是能直接上舞台的立绘** —— 写成 sprite 会让 `show lan angry`
        # 指到一张 1/4 预览图上。
        if _has_dir_word(rel, _AVATAR_DIR_WORDS):
            return Identity(kind='avatar', tag=tag, attrs=(),
                            display=_display_of(known_chars, tag),
                            source='tag-column', showable=False)
        if _has_dir_word(rel, _PREVIEW_DIR_WORDS):
            return Identity(kind='preview', tag=tag, attrs=attrs,
                            display=_display_of(known_chars, tag),
                            source='tag-column', showable=False)
        return Identity(kind='sprite', tag=tag, attrs=attrs,
                        display=_display_of(known_chars, tag),
                        source='tag-column', showable=True)
    kind = kind_from_tag_word(low)
    if kind in ('bg', 'cg', 'ui'):
        # 非角色图层：Tag 列明确写着它是哪一类。
        #
        # ⚠ 能不能 `show`，取决于**引擎会不会自动注册这个名字**
        # （2026-11-03，地狱测试第七轮 I7 抓到）：Ren'Py 只把
        # `game/images/…` 下的图注册成 image name（`images/ui/x.png` → `ui x`）。
        # 而界面素材常常住在 `game/charactercenter/` / `game/gui/` 这类目录里
        # ——**不注册**。真机 `charactercenter/` 下 24 张图原本全被判成
        # `showable=True`，选择器写出 `ui background` / `ui card_lan`，
        # 而卡片的提示又说"ui 不是立绘"（**提示是对的，选择器错了**）。
        showable = _under_game_images(rel)
        return Identity(kind=kind, tag=tag_col, attrs=attrs,
                        source='tag-column', showable=showable)
    if kind == 'avatar' or _has_dir_word(rel, _AVATAR_DIR_WORDS):
        tag = _char_hint(rel, attrs, tag_map)
        return Identity(kind='avatar', tag=tag, attrs=(),
                        display=_display_of(known_chars, tag),
                        source='tag-column', showable=False)
    if kind == 'preview' or _has_dir_word(rel, _PREVIEW_DIR_WORDS):
        tag = _char_hint(rel, attrs, tag_map)
        return Identity(kind='preview', tag=tag, attrs=attrs,
                        display=_display_of(known_chars, tag),
                        source='tag-column', showable=False)
    # Tag 写了、但不是已知角色 tag、也不是 bg/cg/ui —— 不猜它是谁。
    return Identity(kind='other', tag='', attrs=attrs,
                    source='tag-column', showable=False)


def _from_path(row, rel: str, attrs: tuple, tag_map: dict, known_chars,
               copy_index, aid: str) -> Identity:
    """④ 目录段 / ⑤ stem（+ 头像目录、副本检测、弱 kind 线索）。"""
    dirs = _dirs_of(rel)
    stem = _stem_of(rel)
    preview_path = _has_dir_word(rel, _PREVIEW_DIR_WORDS)

    # 头像：目录段明确写着 avatar（契约：宁可 unknown，也不把立绘猜成头像）
    if _has_dir_word(rel, _AVATAR_DIR_WORDS):
        tag = _char_hint(rel, attrs, tag_map)
        return Identity(kind='avatar', tag=tag, attrs=(),
                        display=_display_of(known_chars, tag),
                        source='folder', showable=False)

    # ④ 路径里**最近的**已知角色 tag 目录段（`.../<tag>/<表情>.png`）
    for i in range(len(dirs) - 1, -1, -1):
        tag = tag_map.get(dirs[i])
        if not tag:
            continue
        stem_attrs = _attrs_from_stem(stem, tag)
        copy = preview_path or _is_copy(tag, stem, copy_index)
        kind = 'preview' if copy else 'sprite'
        return Identity(kind=kind, tag=tag, attrs=stem_attrs,
                        display=_display_of(known_chars, tag),
                        source='folder', showable=(kind == 'sprite'))

    # 预览目录、但目录里没有已知角色 tag：tag 只能从 stem 精确命中
    if preview_path:
        tag = tag_map.get(stem.lower(), '')
        attrs_out = () if tag else (stem,)
        return Identity(kind='preview', tag=tag, attrs=attrs_out,
                        display=_display_of(known_chars, tag),
                        source='folder', showable=False)

    # ⑤ 文件 stem 恰等于已知角色 tag（`.../images/ling.png` 这种整图约定）
    tag = tag_map.get(stem.lower(), '')
    if tag:
        copy = _is_copy(tag, stem, copy_index)
        kind = 'preview' if copy else 'sprite'
        rest = tuple(a for a in attrs
                     if a.lower() not in (tag.lower(), stem.lower()))
        return Identity(kind=kind, tag=tag, attrs=rest,
                        display=_display_of(known_chars, tag),
                        source='stem', showable=(kind == 'sprite'))

    # 弱线索：路径像 bg/cg/ui（没有 Tag 列时也能归到对应组，但**不 show** ——
    # 没有 Tag/映射就说不出 `show` 该写什么，写 ID 等于骗人）。
    kind = path_kind(rel)
    if kind:
        return Identity(kind=kind, tag='', attrs=(stem,) if stem else (),
                        source='folder', showable=False)

    # ⑥ 认不出 = unknown（**不猜**）
    return Identity(kind='unknown', tag='', attrs=(), source='unknown',
                    showable=False)


def _attrs_from_stem(stem: str, tag: str) -> tuple:
    """
    `<角色目录>/<文件名>.png` → 属性元组。

    `happy.png` → `('happy',)`；`ling_happy.png` → `('happy',)`（去掉重复的
    角色 tag 前缀 —— 真实项目两种命名都有，去掉前缀后 `show ling happy`
    才是对的）；`_hd_cut.png`（下划线开头）原样保留。
    """
    stem = str(stem or '').strip()
    tag = str(tag or '').strip()
    if not stem or not tag:
        return (stem,) if stem else ()
    low_stem, low_tag = stem.lower(), tag.lower()
    if low_stem == low_tag:
        return ()
    for sep in ('_', '-', ' '):
        prefix = low_tag + sep
        if low_stem.startswith(prefix) and len(stem) > len(prefix):
            rest = stem[len(prefix):].strip()
            return (rest,) if rest else ()
    return (stem,)


# ---------------------------------------------------------------------------
#  批量解析 / 项目上下文（app 层与 UI 共用，避免各写一套"已知 tag"）
# ---------------------------------------------------------------------------

def resolve_images(rows, *, known_tags=None, known_chars=None, mapping=None,
                   project_root: str = '', copy_index=None,
                   sprite_words=None) -> dict:
    """
    一批图片行 → `{asset_id: Identity}`（**批量入口**，一次建好副本索引）。

    为什么要有它：选择器分组 / 用途过滤都要遍历全部图片；逐行调
    `resolve_image` 会每行重建一次副本索引（1720 行 = O(n²)）。这里
    先建索引再逐行解析，结果与单行调用完全一致。

    `sprite_words` 见 `resolve_image`（项目 `layeredimage` 声明的属性词表）。
    """
    row_list = _iter_rows(rows)
    out: dict = {}
    if not row_list:
        return out
    if copy_index is None:
        copy_index = build_copy_index(row_list)
    root = str(project_root or '')
    for row in row_list:
        aid = asset_id_of(row)
        if not aid:
            continue
        rel = normalize_relpath(str(row.get('Path') or ''), root)
        out[aid] = resolve_image(row, relpath=rel, known_tags=known_tags,
                                 known_chars=known_chars, mapping=mapping,
                                 rows=row_list, copy_index=copy_index,
                                 sprite_words=sprite_words)
    return out


def project_context(registry, project_root: str = '', *, mapping=None) -> dict:
    """
    装配"解析身份"要用的项目上下文（**唯一组装处**，界面不用自己读表）。

    返回::

        {'known_tags': frozenset[str],   # 角色表 + 映射 characters + 引擎事实 + 项目 layeredimage
         'known_chars': dict,            # known_characters() 的结果
         'mapping': dict,                # 没有映射 = {}
         'mapping_path': str,            # 没有 = ''
         'warnings': tuple[str, ...],    # 映射文件的问题（不抛）
         'rows': dict,                   # registry.images（{id: row}）
         'copy_index': dict,             # 批量解析用的副本索引
         'sprite_words': dict,           # {tag: {图片文件名: 声明的属性名}}（可空）
         'project_root': str}

    已知角色 tag **四个**来源（规划 §2.4 + 2026-11-02）：characters 表
    「立绘 tag」、映射 `characters`、引擎事实里**带属性**的 tag、项目
    `.rpy` 里 `layeredimage` 声明过的 tag（`declared_sprite_words` ——
    补 `qing` 这类"角色表没登记、但有立绘"的人）。四路都只是"候选集合"，
    真正的身份判定仍在 `resolve_image`（只认字面量）。
    """
    root = str(project_root or '')
    known_chars = known_characters(registry)
    warnings: tuple = ()
    mapping_path = ''
    if mapping is None:
        data = {}
        if root:
            try:
                data, warn, mapping_path = mapping_cached(root)
                warnings = tuple(warn or ())
            except Exception as e:              # noqa: BLE001 - 映射坏了不该打不开
                swallow('assets.asset_identity.project_context.mapping', e)
                data = {}
        mapping = data
    known = set(known_chars)
    chars = mapping.get('characters') if isinstance(mapping, Mapping) else None
    if isinstance(chars, Mapping):
        known |= {str(tag).strip() for tag in chars if str(tag).strip()}
    try:
        known |= engine_fact_tags(root)
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_identity.project_context.facts', e)
    # ④ 项目自己 `.rpy` 里的 `layeredimage`：角色表只登记"会说话的人"，
    # 而 `qing` 这种由别人出声、自己只有立绘声明的 tag 只能靠这一路认出来。
    sprite_words = _actor_words(declared_sprite_words(root))
    known |= set(sprite_words)
    known_tags = frozenset(t for t in known
                           if t and t.lower() not in NON_ACTOR_TAGS)
    rows = getattr(registry, 'images', None) if registry is not None else None
    rows = dict(rows) if isinstance(rows, Mapping) else {}
    return {
        'known_tags': known_tags,
        'known_chars': known_chars,
        'mapping': mapping if isinstance(mapping, Mapping) else {},
        'mapping_path': str(mapping_path or ''),
        'warnings': warnings,
        'rows': rows,
        'copy_index': build_copy_index(rows),
        'sprite_words': sprite_words,
        'project_root': root,
    }


def _char_hint(rel: str, attrs, tag_map: dict) -> str:
    """
    头像/预览目录里的文件名 → 角色 tag（只认**精确命中**已知 tag）。

    顺序：文件名 stem > 属性词 > 目录段（最近的）
    （`cc_preview/avatar/ling.png` → ling；`characters/avatar/lan.png`
    的 Tag 列写着 avatar、Attributes 写着 lan → lan）。认不出 = ''。
    """
    stem = _stem_of(rel)
    if stem and stem.lower() in tag_map:
        return tag_map[stem.lower()]
    for word in attrs or ():
        if str(word).strip().lower() in tag_map:
            return tag_map[str(word).strip().lower()]
    for seg in reversed(_dirs_of(rel)):
        if seg in tag_map:
            return tag_map[seg]
    return ''


# ---------------------------------------------------------------------------
#  预览图/头像 → 真立绘
# ---------------------------------------------------------------------------

def _pixels_of(row: Mapping, dimensions) -> int:
    """这一行的像素数（`dimensions` 给了才比；认不出 = 0）。"""
    if not isinstance(row, Mapping):
        return 0
    rid = asset_id_of(row)
    rel = _row_relpath(row)
    raw = str(row.get('Path') or '').strip()
    candidates = [rid, rel, raw, PurePosixPath(rel).name if rel else '']
    for key in candidates:
        if not key or not isinstance(dimensions, Mapping):
            continue
        value = dimensions.get(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            try:
                return int(value.get('width') or value.get('w') or 0) * \
                    int(value.get('height') or value.get('h') or 0)
            except (TypeError, ValueError):
                continue
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            try:
                return int(value[0]) * int(value[1])
            except (TypeError, ValueError):
                continue
    for wk, hk in (('Width', 'Height'), ('width', 'height'),
                   ('W', 'H'), ('w', 'h')):
        try:
            w, h = int(row.get(wk) or 0), int(row.get(hk) or 0)
        except (TypeError, ValueError):
            continue
        if w and h:
            return w * h
    return 0


def find_real_sprite(identity: Identity, rows, *,
                     dimensions=None) -> Optional[dict]:
    """
    预览图 / 头像 → **同一 tag 的真立绘行**（能直接 `show` 的那一行）。

    契约（§1.1）：优先 Tag 非空 → 属性对得上 → 像素更大（`dimensions` 给了才比）。

    认不出 / 没有候选 → `None`（调用方据此**拒绝**并给一句话，
    绝不拿预览图的名字硬写一条 `show`）。
    """
    if identity is None or identity.kind not in ('preview', 'avatar'):
        return None
    tag = str(identity.tag or '').strip()
    if not tag:
        return None
    want = [str(a).strip().lower() for a in identity.attrs if str(a).strip()]
    want_set = set(want)
    # 预览图要的是**同一个表情**：属性对不上的行不能当"对应的真立绘"
    # （否则会拿一个不存在的表情名去撞另一张脸）；头像没有属性，任何同 tag
    # 的真立绘都行。
    require_attr = (identity.kind == 'preview' and bool(want_set))
    best = None
    best_key = None
    for row in _iter_rows(rows):
        row_tag = str(row.get('Tag') or '').strip()
        if not row_tag or row_tag.lower() != tag.lower():
            continue
        words = {str(w).strip().lower() for w in row_values(row) if str(w).strip()}
        stem = _stem_of(str(row.get('Path') or row.get('ID') or '')).lower()
        aid = asset_id_of(row).lower()
        prefix = tag.lower() + '_'
        stripped = (aid[len(prefix):] if aid.startswith(prefix)
                    and len(aid) > len(prefix) else '')
        # 同一张脸可能有多种登记写法：真行的 `Attributes` 偶尔写成
        # `ling_happy`（连 ID 一起），而预览行 stem 只有 `happy` ——
        # 把 ID 去掉 tag 前缀后的部分也算一个可对应名字（仍是字面量）。
        tokens = set(words) | {stem, aid}
        if stripped:
            tokens.add(stripped)
        exact = 1 if want_set and (tokens & want_set) else 0
        hit = len(tokens & want_set)
        if require_attr and not exact and not hit:
            continue
        pixels = _pixels_of(row, dimensions)
        # 排序：文件名/属性**精确命中** > 命中词多 > 像素大 > 属性少 > ID 稳定
        key = (-exact, -hit, -pixels, len(words), aid or stem)
        if best_key is None or key < best_key:
            best, best_key = row, key
    return best


# ---------------------------------------------------------------------------
#  spec / 展示
# ---------------------------------------------------------------------------

def spec_for_identity(identity: Identity, row: dict | None = None) -> str:
    """
    `Identity` → 写回卡片的那段 **image spec**（`ling happy` / `bg rooftop`）。

    · 有 tag → `tag 属性…`（属性保序、去重、不重复 tag 本身）；
    · 没 tag → 退回资产 ID（宁可写 ID，也不凭空编一个 tag）；
    · 什么都没有 → ''。
    """
    if identity is None:
        return asset_id_of(row)
    tag = str(identity.tag or '').strip()
    if not tag:
        return asset_id_of(row)
    # ⚠ **文件名已经带 tag 前缀时不要再拼一次**（2026-11-03，地狱测试 I7）：
    # `game/images/bg_1.jpg` 的自动 image name 是 `bg_1`（单段），
    # 而 Tag 列写的是 `bg`、Attributes 列写的是 `bg_1` —— 拼起来成了
    # `bg bg_1`，引擎里没有这个名字。判据：**属性词恰好就是文件名本身**、
    # 且文件名以 `<tag>_` 开头 → 名字就是文件名。
    # （`images/bg/rooftop.png` 不受影响：stem=`rooftop` ≠ 属性词 `rooftop`
    #   的前缀关系不成立 —— 它要的是 `bg rooftop`。）
    stem = _stem_of(str((row or {}).get('Path') or ''))
    if stem and tag and stem.lower().startswith(tag.lower() + '_'):
        words = [str(w).strip() for w in (identity.attrs or ()) if str(w).strip()]
        if len(words) == 1 and words[0].lower() == stem.lower():
            return stem
    parts = [tag]
    seen = {tag.lower()}
    for word in identity.attrs or ():
        word = str(word or '').strip()
        if word and word.lower() not in seen:
            seen.add(word.lower())
            parts.append(word)
    return ' '.join(parts)


def spec_tag_problem(spec, known_tags) -> str:
    """
    `spec` 的首词**项目认不认**（空串 = 认 / 判不了）。

    为什么需要（2026-11-03，地狱测试第七轮 I7"自产自销"）：

    `storygraph.mapping.json` 允许这样写

        "images": {"qing": {"kind": "sprite", "char": "青", "attr": "base"}}

    `char` 是**填 image tag 的槽**，真机里用户填了**显示名**「青」。工具老老实实
    照做，`resolve_pick` 就返回 `spec='青 base'`；而引擎里根本没有这个 image
    name，`show 青 base` 直接报错。

    也就是说：**生成器产出了一个自己都不认识的名字**，而校验器（卡片提示）
    在旁边说"这不行" —— 同一件事两个结论，用户只会觉得工具坏了。

    这条判据让生成器**自己先跑一遍校验器**：不认识就不许输出。

    「判不了就闭嘴」：`known_tags` 为空（没打开项目 / 没建表）时返回空串，
    绝不因为"我这儿没有"就否掉用户项目里真实存在的名字。
    """
    tag = str(spec or '').strip().split(' ')[0]
    if not tag:
        return ''
    words = {str(t).strip().lower() for t in (known_tags or ())
             if str(t).strip()}
    if not words:
        return ''
    if tag.lower() in words:
        return ''
    if tag.lower() in NON_ACTOR_TAGS:
        return ''                       # bg / cg / ui … 是引擎内置语义，交给上层
    return (f'「{tag}」不是项目里任何图像声明的名字 —— 写进 `show` 引擎会报错。'
            f'多半是「资源映射」里把 image tag 填成了显示名'
            f'（该填 `qing` 这种，不该填「{tag}」这种）。')


def group_key_for_identity(identity: Identity) -> str:
    """
    选择器分类名（**角色优先**）：同一角色的所有表情归一组。

    `立绘：<显示名>（<tag>）`（没有显示名退 `立绘：<tag>`）；
    其余类别各自一组（预览图/头像 **不能上舞台**，所以名字里写明）。
    """
    if identity is None:
        return '未识别（不猜）'
    kind = identity.kind
    if kind == 'sprite':
        tag = str(identity.tag or '').strip()
        if not tag:
            return '立绘：未识别'
        display = _clean_display(identity.display)
        return f'立绘：{display}（{tag}）' if display else f'立绘：{tag}'
    return {
        'preview': '角色预览图（不能直接上舞台）',
        'avatar': '角色头像（不能直接上舞台）',
        'bg': '背景',
        'cg': 'CG',
        'ui': '界面/其他图层',
        'other': '其他图片',
        'unknown': '未识别（不猜）',
    }.get(kind, '未识别（不猜）')


#: 非立绘图片分组的展示顺序（立绘组永远在最前，其余按这张表）。
GROUP_ORDER = (
    '背景', 'CG', '界面/其他图层',
    '角色预览图（不能直接上舞台）', '角色头像（不能直接上舞台）',
    '其他图片', '未识别（不猜）',
)


__all__ = [
    'KINDS', 'SHOWABLE_KINDS', 'SOURCES', 'GROUP_ORDER', 'Identity',
    'normalize_relpath', 'asset_id_of', 'kind_from_tag_word',
    'known_characters', 'known_tags_of', 'engine_fact_tags',
    'declared_sprite_tags', 'declared_sprite_words', 'declared_sprite_attrs',
    'project_literal_words', 'image_statement_words',
    'build_copy_index', 'path_kind', 'resolve_image', 'resolve_images',
    'project_context', 'find_real_sprite', 'spec_for_identity',
    'group_key_for_identity',
]
