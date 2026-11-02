# -*- coding: utf-8 -*-
"""
导出前的**最后一道门禁**：这几条 `show` 的名字，游戏里真的存在吗？
==============================================================

用户 2026-10-30（P0 反馈的原话）：

> 立绘怎么一堆单独的分类……只有文件名是角色名的可以匹配；直接放角色那个
> 头像图就能动

> 不许静默写出 `show refusing`

前一句是**选择器的错**（已由 `assets.asset_identity` + `app.picker` 断掉：
认不出就拒绝，预览图自动换真立绘）。这一模块补的是**导出前的最后一道**：
卡片上的红字提示只是提示 —— 它挡不住 Stage 列手写、挡不住旧数据、
挡不住从 `.xlsx` 导回来的表。导出那一刻必须有人再看一眼，而且**不许静默
放行**（也不许静默拦：能不能导出仍由人在弹窗里拍板）。

判据：只认字面量，不猜
----------------------
一拍「立绘」列（`Beat.show`）里可能有几条 show 项，用 `;` 分开
（`ling happy; cg cg01`）。逐条取 **spec 首词**（`ling` / `cg`），它必须是
下面几类**项目里真有**的名字之一：

  1. **已知角色 tag** —— 角色表「立绘 tag」（无则角色变量名）、映射文件
     `characters`、引擎事实里带属性的 tag（三路合并在
     `assets.asset_identity.project_context`）；
  2. **项目自己声明过的图像名** —— `.rpy` 里的 `image 名:` / ATL 块 /
     `layeredimage 名:` / 脚本合并行（`_from_script`）。名字是脚本字面量，
     不许因为"角色表里没这行"就封掉一条原本能用的 `show`；这一类有两条
     来源：内存行上的声明标记（`declared_names`）**和**磁盘 `.rpy` 文本
     （`project_script_names`，2026-10-30 真机实测补的：标记不落 xlsx，
     只靠内存行会有 26 条假警报）；
  3. **项目里已经在用的 show 名字** —— 磁盘 `.rpy` 里已存在的 `show X`
     首词。那是**导出前就已经在项目里**的字面量，不是这一次工具写出来的；
     本门禁挡的是"工具写出游戏里没有的名字"，不该对着项目自己的原文唠叨
     （用户已点过「原样导出」的，不反复报；第一次一定报）；
  4. **非角色图层名**（`bg` / `cg` / `ui` / `logo` …，见
     `core.vocabulary.NON_ACTOR_TAGS`）—— `show bg rooftop` 是合法语句；
  5. **`show` 的关键字**（`screen` / `expression` / `layer` / `image` …，
     见 `_SHOW_KEYWORD_WORDS`）—— 那不是图像名，判不了，**不报**。

其余一律报出来。典型的：

    show refusing                 ← 预览图/文件 stem，引擎里没有这个图像名
    show 凌跃捷 开心              ← 角色**显示名**写进了 show（要写立绘 tag）

`showable` 的口径与 `asset_identity.Identity.showable` 一致：认不出就不算
能上舞台 —— 但**没有真值依据时不判**（见下）。

没有依据时**不判**（否则每次导出都冲出一堆假警报）
--------------------------------------------------
判据依赖"项目认得哪些名字"。拿不到资源表 / 角色表 / 映射时，白名单必然是
残缺的，此时报出来的东西大半是假警报 —— 那比不报更坏（用户会学会无视这个
弹窗，真正的问题也就跟着被无视）。所以 `ScanResult.degraded` 标出这种情况：
**一个 suspect 都不产生**，由调用方决定要不要在日志里说一句。

这个模块不做的事
----------------
· **不判断表情/属性存不存在**（`show ling 不存在的表情`）—— 那要枚举每个
  layeredimage 的属性，属于更深的一层；本模块只管"首词是不是能上舞台的名字"；
· **不改任何字符**：`scan` 是纯只读的（`tests/test_stage_identity_guard.py`
  用 `to_row()` 前后对比钉住这一点）。要不要改、改成什么，由人在弹窗里拍板。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Mapping

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import NON_ACTOR_SHOW_TARGETS, NON_ACTOR_TAGS

#: `Beat.show` 的多值分隔符（与 `story.beat_model._MULTI_VALUE_SEP` 同口径；
#: 两处字符串一旦漂移，这里就会把一整串当一项，判出来的首词是错的）。
_MULTI_VALUE_SEP = ';'

#: 项目 `.rpy` 文本里**声明图像名**的行：`image 名:` / `image 名 = …` /
#: `layeredimage 名:`（取第一个名字词就够了 —— 本门禁只判首词）。
_DECL_IMAGE_RE = re.compile(r'^\s*(?:layeredimage|image)\s+([A-Za-z_][A-Za-z0-9_]*)')

#: 项目 `.rpy` 文本里已存在的 `show` 语句（首词 = 项目自己写过的名字）。
_SHOW_LINE_RE = re.compile(r'^\s*show\s+([A-Za-z_][A-Za-z0-9_]*)')

#: `project_script_names` 的缓存：`root → (签名, 名字集合)`。
#:
#: 签名 = 每个 `.rpy` 的 `(路径, mtime_ns, 大小)`。导出前调一次，第二次起
#: 只 stat 一遍（96 个文件 ~2ms），不重复读 1.2 MB 文本。
#: 为什么缓存必须带签名：用户改完脚本立刻再导出，白名单不能还是旧的
#: （旧名单少一个刚声明的名字 → 假警报）。文件被替换而 mtime/大小都没变
#: 属于极端情况，那时最多多问一句，不会漏掉"工具新写的坏名字"。
_SCRIPT_NAMES_CACHE: dict = {}

#: 非角色图层 kind → 人话（写进"为什么"那句）。
_KIND_CN = {
    'preview': '角色预览图/缩略图',
    'avatar': '角色头像',
    'other': '其他图片',
    'unknown': '认不出的图片',
}

#: 首词是**关键字**、不是图像名的 show 写法。
#:
#: `NON_ACTOR_SHOW_TARGETS` 是项目既有的口径（`screen` / `layer` /
#: `expression` / `movie` / `text`）。这里额外收 `image` —— Ren'Py 的
#: `show image <表达式>` 写法（真机 `mini_game_puzzle.rpy` 就有
#: `show image puzzle_path at Transform(...)`），首词是关键字，不是名字。
_SHOW_KEYWORD_WORDS = frozenset(NON_ACTOR_SHOW_TARGETS) | frozenset({'image'})


# ---------------------------------------------------------------------------
#  结果
# ---------------------------------------------------------------------------

@dataclass
class IdentitySuspect:
    """一条"写的名字项目里找不到"的 show 项（一个字都不动，只指认）。"""

    index: int                 # 第几拍（0 基，按传入的 beats 顺序）
    seq: object = ''           # `Beat.seq`（卡片上显示的拍号）
    label: str = ''
    spec: str = ''             # 原文：这条 show 项（如 `refusing happy`）
    tag: str = ''              # spec 首词（它想写的图像名/角色 tag）
    reason: str = ''           # 人话：为什么觉得这个名字不存在
    target: object = None      # 指向那个 Beat（内部用，不参与展示）

    def as_dict(self) -> dict:
        return {'index': self.index, 'seq': self.seq, 'label': self.label,
                'spec': self.spec, 'tag': self.tag, 'reason': self.reason}


@dataclass
class ScanResult:
    """`scan_stage_identity` 的返回值（结构照 `story.stage_suspect.ScanResult`）。"""

    suspects: list = field(default_factory=list)   # [IdentitySuspect]
    checked: int = 0                                # 看了几条 show 项
    known_tags: frozenset = frozenset()             # 这次用的"已知角色 tag"
    declared: frozenset = frozenset()               # 项目声明过的图像名（首词）
    #: 项目 `.rpy` 里**项目自己写过**的名字首词（声明的图像名 + 已有 show 首词）。
    #:
    #: 为什么必须单独一类（2026-10-30 真机实测）：资源表是 xlsx 的投影，
    #: `_from_script` 这类"声明行"标记**不落表** —— 于是从 xlsx 加载的
    #: registry 里 `declared` 永远是空集，而真机 26 条假警报全来自脚本里
    #: 真有的名字（`image bgocean = …` / `layeredimage qing:`）。
    #: 这类名字是项目自己的字面量，**工具不该把它当成"用户新写错的名字"**。
    project_names: frozenset = frozenset()
    degraded: bool = False                          # True = 没有依据，一条都没判

    def __bool__(self) -> bool:
        return bool(self.suspects)

    def __len__(self) -> int:
        return len(self.suspects)

    def report(self) -> str:
        """命令行/日志用的一段人话（CLI 拿它打印，不阻塞）。"""
        if not self.suspects:
            return ''
        lines = [f'「立绘 / 舞台」里有 {len(self.suspects)} 条 show '
                 f'写的名字在项目里找不到：']
        for it in self.suspects:
            lines.append(f'  · 第 {it.seq} 拍：{it.spec}　—— {it.reason}')
        lines.append('  导出后这些 show 在游戏里找不到图像（运行时报错）。'
                     '请在卡片里把「立绘」列（或「舞台」里的 show）改成能上舞台的'
                     '名字：角色的立绘 tag，或项目 .rpy 里声明过的图像名。')
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
#  show 文本 → spec → 首词
# ---------------------------------------------------------------------------

def split_show_specs(text) -> list:
    """
    `Beat.show` 的原文 → 一条条 show 项（`'ling happy; cg cg01'` → 两项）。

    只按 `;` 切（与 `beat_model` / 渲染层同一个分隔符口径），空段丢掉。
    """
    return [p.strip() for p in str(text or '').split(_MULTI_VALUE_SEP)
            if p.strip()]


def spec_tag(spec) -> str:
    """
    一条 show 项 → **首词**（它想写的图像名/角色 tag；认不出 = ''）。

    为什么截 `@`：单图带位置时单元格里写成 `ling@i11`（`show_item_part` 的
    紧凑编码），`@` 后面是变换不是名字。多词图像名（`cg cg01`）首词仍然只是
    `cg` —— 本门禁只判首词。
    """
    words = str(spec or '').split()
    if not words:
        return ''
    return words[0].split('@', 1)[0].strip()


# ---------------------------------------------------------------------------
#  白名单：已知 tag / 项目声明过的图像名
# ---------------------------------------------------------------------------

def _rows(rows) -> list:
    """行集合 → 行列表（同时接受 `{id: row}` 映射与 `[row, ...]` 列表）。"""
    if not rows:
        return []
    if isinstance(rows, Mapping):
        return [dict(r) for r in rows.values() if isinstance(r, Mapping)]
    out = []
    for r in rows:
        if isinstance(r, Mapping):
            out.append(dict(r))
    return out


def is_declared_row(row: Mapping) -> bool:
    """
    这行图片资产是不是项目 `.rpy` 里**字面量声明过**的图像名？

    三条证据（与 `asset_identity._is_declared_image` 同口径，都是项目数据）：
      · `_atl` / `_from_script` / `_layered` 标记（脚本解析合并进来的行）；
      · `assets.atl_block.is_atl_row`（ATL 块内容在 Notes/ATL 列里的行）。
    """
    if not isinstance(row, Mapping):
        return False
    if row.get('_atl') or row.get('_from_script') or row.get('_layered'):
        return True
    try:
        from storygraph.assets.atl_block import is_atl_row
        return bool(is_atl_row(row))
    except Exception as e:                      # noqa: BLE001 - 标记坏了就当没声明
        swallow('app.stage_identity_guard.is_declared_row', e)
        return False


def declared_names(rows) -> frozenset:
    """
    项目声明过的图像名 → **首词集合**（`image logo blink:` → `logo`）。

    为什么只要首词：本门禁判的是 spec 首词（见模块 docstring）。名字从
    资源表行的 `Tag` 列取（`beat_build` 把声明名拆成 `Tag` + `Attributes`
    两列，`Tag` 就是第一个词）。

    ⚠ 依赖行上的 `_from_script` / `_atl` / `_layered` 标记，而这些标记
    **不落 xlsx** —— 从资源表加载的 registry 里这一集合通常是空的。
    脚本里真有的名字由 `project_script_names` 从磁盘文本直接兜住。
    """
    out = set()
    for row in _rows(rows):
        if not is_declared_row(row):
            continue
        word = spec_tag(row.get('Tag'))
        if word:
            out.add(word)
    return frozenset(out)


def _script_files(project_root: str) -> list:
    """项目里要扫的 `.rpy`（`<root>/game/**/*.rpy`；没有 game/ = 空）。"""
    game = Path(str(project_root or '')) / 'game'
    try:
        if not game.is_dir():
            return []
        return sorted(game.rglob('*.rpy'))
    except OSError as e:                        # noqa: BLE001 - 读不了就不给依据
        swallow('app.stage_identity_guard._script_files', e)
        return []


def _script_signature(files) -> tuple:
    """缓存签名：每个文件的 `(路径, mtime_ns, 大小)`。"""
    sig = []
    for path in files:
        try:
            st = path.stat()
            sig.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(sig)


def project_script_names(project_root: str) -> frozenset:
    """
    项目 `.rpy` 里**项目自己字面量写过**的图像名首词（只读磁盘文本）。

    两类证据（都是一行一个，够本门禁判首词用）：

      · 图像声明：`image 名:` / `image 名 = …` / `layeredimage 名:`；
      · 已存在的 `show` 语句首词（`show bgocean with dissolve` → `bgocean`）。

    为什么要有这一路（真机实测，2026-10-30）：真机 96 个 `.rpy` 里
    `image bgocean = Transform(…)` / `layeredimage qing:` 这类声明**不在
    xlsx 资源表里**（`_from_script` 标记不落表），只看资源表会把这些名字
    当成"用户新写错的"——26 条假警报，每次导出都弹，比不报还坏。

    为什么"已存在的 show 首词"也算证据：那是**导出前就已经在项目里**的字面量
    （可能是历史遗留/手工写的），不是这一次工具写出来的。本门禁要挡的是
    "工具往脚本里写出游戏里没有的名字"，不该对着项目自己的原文唠叨；
    工具真写坏一次之后名字会进 `.rpy`，那已经是**人点过「原样导出」**的
    结果 —— 同一处不重复报（用户已拍板），但第一次一定报。

    只读、有缓存：开销 = 每个 `.rpy` 一次 `stat`（96 个文件 ~2ms），
    文本只在签名变化时读一次（第一次 ~20ms）。
    """
    root = str(project_root or '')
    if not root:
        return frozenset()
    files = _script_files(root)
    if not files:
        return frozenset()
    sig = _script_signature(files)
    cached = _SCRIPT_NAMES_CACHE.get(root)
    if cached is not None and cached[0] == sig:
        return cached[1]
    names = set()
    for path in files:
        try:
            text = path.read_text(encoding='utf-8-sig', errors='ignore')
        except OSError as e:                    # noqa: BLE001 - 单个文件读不了就跳过
            swallow('app.stage_identity_guard.project_script_names', e)
            continue
        for line in text.splitlines():
            s = line.lstrip()
            if not s or s.startswith('#'):
                continue
            m = _DECL_IMAGE_RE.match(s) or _SHOW_LINE_RE.match(s)
            if m:
                names.add(m.group(1))
    out = frozenset(names)
    _SCRIPT_NAMES_CACHE[root] = (sig, out)
    return out


def _known_index(known_tags) -> dict:
    """已知角色 tag → `{小写: 原写法}`（大小写不敏感匹配，回报原写法）。"""
    out = {}
    try:
        items = known_tags.keys() if isinstance(known_tags, Mapping) else known_tags
    except AttributeError:
        return out
    for tag in items or ():
        word = str(tag or '').strip()
        if word:
            out.setdefault(word.lower(), word)
    return out


def _is_showable(tag: str, known_lower: Mapping, declared_lower,
                 project_lower=frozenset()) -> bool:
    """
    首词是不是"能上舞台的名字"（判据见模块 docstring，**只认字面量**）。

    放行的四类：`show` 关键字（不是图像名）/ 非角色图层名 / 已知角色 tag /
    项目里声明过或在用的图像名（`.`rpy` 磁盘文本，见 `project_script_names`）。
    其余一律 False（= 报出来问人）。
    """
    low = str(tag or '').strip().lower()
    if not low:
        return False
    if low in _SHOW_KEYWORD_WORDS:
        # `show screen msg` / `show expression F(x)` / `show layer master` /
        # `show image puzzle_path`：这些**不是图像名**，判不了 —— 不报
        # （报了就是每次导出都问一遍）。
        return True
    if low in NON_ACTOR_TAGS:
        # `show bg rooftop` / `show cg cg01` / `show black`：真图层，合法。
        return True
    return (low in known_lower or low in declared_lower
            or low in project_lower)


def is_showable_name(tag, *, known_tags=(), declared=(),
                     project_names=()) -> bool:
    """单点判据（给测试 / 别处复用）：这个名字能不能直接写进 `show`。"""
    return _is_showable(
        tag, _known_index(known_tags),
        {str(d or '').strip().lower() for d in declared or ()},
        {str(d or '').strip().lower() for d in project_names or ()})


# ---------------------------------------------------------------------------
#  "为什么"：尽量给一句能照着改的人话
# ---------------------------------------------------------------------------

def _display_index(known_chars) -> dict:
    """
    角色显示名/别名 → `(显示名, 立绘 tag)`（`known_chars` 两种形态都认）。

    用途：用户把**显示名**写进了 show（`show 凌跃捷 开心`）时，告诉他
    "要写的是立绘 tag"，而不是只说一句看不懂。
    """
    out = {}
    if not isinstance(known_chars, Mapping):
        return out
    for key, entry in known_chars.items():
        tag = str(key or '').strip()
        if not tag:
            continue
        if isinstance(entry, Mapping):
            display = str(entry.get('display') or entry.get('Name')
                          or entry.get('name') or '').strip()
            aliases = entry.get('aliases') or entry.get('Aliases') or ()
        else:
            display = str(entry or '').strip()
            aliases = ()
        if isinstance(aliases, str):
            aliases = [aliases]
        names = [display] + [str(a or '') for a in aliases]
        for name in names:
            name = name.strip().strip('「」『』"\'')
            if name and name.lower() not in out:
                out[name.lower()] = (name, tag)
    return out


class _Hints:
    """
    "为什么"用到的附加证据（**按需**才建：没有 suspect 时一次都不解析）。

    真实项目 1700+ 行图片：为了给一句提示去全量解析身份是有成本的，而
    "一条都没报"才是常态 —— 所以解析放在 `_fill_reasons` 里，只在真有
    suspect 时跑一次。
    """

    def __init__(self, *, known_tags=(), known_chars=None, mapping=None,
                 all_rows=None, project_root='', dimensions=None):
        self.known_tags = known_tags
        self.known_chars = known_chars
        self.mapping = mapping
        self.project_root = str(project_root or '')
        self.dimensions = dimensions
        self.rows = _rows(all_rows)
        self._idents = None
        self._name_hits = None
        self._copy_index = None
        self._words = None
        self._display = _display_index(known_chars)

    @property
    def words(self) -> dict:
        """
        项目 `layeredimage` 的属性词表（`{tag: {图片文件名: 属性名}}`）。

        为什么要它：`show qing stare` 里 `stare` 只是文件名，项目声明里
        这条属性叫 `curious` —— 提示里必须写**引擎认的那个词**，否则
        "照着提示改"改出来还是错。读不出来 = `{}`（退回按文件名写）。
        """
        if self._words is None:
            from storygraph.assets import asset_identity as AI
            self._words = AI.declared_sprite_words(self.project_root)
        return self._words

    # -- 解析（懒） --------------------------------------------------

    def _ensure(self) -> None:
        if self._idents is not None:
            return
        self._idents = {}
        self._name_hits = {}
        self._copy_index = {}
        if not self.rows:
            return
        try:
            from storygraph.assets import asset_identity as AI
            self._copy_index = AI.build_copy_index(self.rows)
            self._idents = AI.resolve_images(
                self.rows, known_tags=self.known_tags,
                known_chars=self.known_chars, mapping=self.mapping,
                project_root=self.project_root,
                copy_index=self._copy_index,
                sprite_words=self.words)
            for row in self.rows:
                ident = self._idents.get(AI.asset_id_of(row))
                if ident is None or ident.showable:
                    continue
                for name in self._row_names(row, AI):
                    self._name_hits.setdefault(name, ident)
        except Exception as e:                  # noqa: BLE001 - 提示没人看也得能导出
            swallow('app.stage_identity_guard.hints', e)
            self._idents, self._name_hits = {}, {}

    @staticmethod
    def _row_names(row: Mapping, AI) -> set:
        """这一行可能被人当成"名字"来写的字面量（资产 ID / 路径文件名）。"""
        names = set()
        aid = str(AI.asset_id_of(row) or '').strip()
        if aid:
            names.add(aid.lower())
        rel = AI.normalize_relpath(row.get('Path') or '')
        if rel:
            stem = PurePosixPath(rel).stem.strip()
            if stem:
                names.add(stem.lower())
        return names

    # -- 三种提示 ----------------------------------------------------

    def display_hit(self, tag):
        return self._display.get(str(tag or '').strip().lower())

    def asset_hit(self, tag):
        self._ensure()
        return (self._name_hits or {}).get(str(tag or '').strip().lower())

    def real_sprite_spec(self, ident):
        """预览图/头像 → 同角色真立绘的 spec（找不到 = ''）。"""
        if ident is None or not getattr(ident, 'tag', ''):
            return ''
        try:
            from storygraph.assets import asset_identity as AI
            real = AI.find_real_sprite(ident, self.rows,
                                       dimensions=self.dimensions)
            if real is None:
                return ''
            real_ident = AI.resolve_image(
                real, known_tags=self.known_tags,
                known_chars=self.known_chars, mapping=self.mapping,
                rows=self.rows, copy_index=self._copy_index or None,
                sprite_words=self.words)
            return AI.spec_for_identity(real_ident, real)
        except Exception as e:                  # noqa: BLE001
            swallow('app.stage_identity_guard.real_sprite_spec', e)
            return ''


def _fill_reasons(suspects, hints: _Hints) -> None:
    """给每条 suspect 补一句"为什么"（人话，尽量能照着改）。"""
    for it in suspects:
        words = str(it.tag or '').strip()
        hit = hints.display_hit(words)
        if hit is not None:
            display, tag = hit
            kind = '显示名' if display == words else '显示名/别名'
            it.reason = (f'「{words}」是角色「{display}」的{kind}，'
                         f'不是立绘 tag —— show 要写「{tag}」这一列的名字')
            continue
        ident = hints.asset_hit(words)
        if ident is not None:
            kind = _KIND_CN.get(getattr(ident, 'kind', ''),
                                '认不出的图片')
            spec = hints.real_sprite_spec(ident)
            if spec:
                it.reason = (f'「{words}」是{kind}，不是能上舞台的名字；'
                             f'同角色的真立绘是「{spec}」，用它')
            elif getattr(ident, 'tag', ''):
                it.reason = (f'「{words}」是{kind}，不是能上舞台的名字；'
                             f'没找到同角色的真立绘，请在「立绘」里重新选')
            else:
                it.reason = (f'资源表里有叫「{words}」的图片，但它是{kind}，'
                             f'不是能直接 show 的图像名')
            continue
        it.reason = (f'「{words}」不是任何角色的立绘 tag，也不是项目里声明过的'
                     f'图像名 —— 导出后游戏里可能找不到这张图')


# ---------------------------------------------------------------------------
#  主入口
# ---------------------------------------------------------------------------

def scan_stage_identity(beats, *, project_root: str = '',
                        known_tags=frozenset(), known_chars=None,
                        mapping=None, all_rows=None,
                        dimensions=None) -> ScanResult:
    """
    扫一遍这批拍的 `show` 项，挑出"写的名字项目里找不到"的那些。

    **只读**：不碰 `Beat.show`、不碰文件（`to_row()` 前后逐字节一致）。
    导出前由 `ui_qt.window_export._review_stage_suspects` 调用，结果与
    「舞台可疑行」一起摆进**同一个**确认弹窗 —— 要不要照原样导出，人拍板。

    参数（项目上下文由 `assets.asset_identity.project_context` 一次装配好）：
      · `project_root` 项目根（用来从磁盘 `.rpy` 取"项目自己写过的名字"，
                      见 `project_script_names`；不给 = 这类证据为空）；
      · `known_tags`  已知角色 tag（角色表 + 映射 + 引擎事实）；
      · `known_chars` `{tag: {display, aliases, ...}}`（只用来写人话提示）；
      · `mapping`     映射文件数据（`assets.asset_mapping.load_mapping`）；
      · `all_rows`    全部图片行（`{id: row}` 或 list；用来认"项目声明过的
                      图像名"，也给"这是预览图，真立绘是 xxx"这句提示）；
      · `dimensions`  图片尺寸（给 `find_real_sprite` 挑更合适的真立绘，可省）。
    """
    known = _known_index(known_tags)
    declared = declared_names(all_rows)
    declared_lower = {str(d).lower() for d in declared}
    project_names = project_script_names(project_root)
    project_lower = {str(d).lower() for d in project_names}
    out = ScanResult(known_tags=frozenset(known.values()),
                     declared=declared, project_names=project_names)

    # 没有依据就不判：白名单残缺时报出来的大半是假警报，宁可不打扰
    # （由调用方决定要不要在日志里说一句，见 `degraded`）。
    if not known and not declared_lower and not project_lower:
        out.degraded = True
        return out

    for i, beat in enumerate(beats or ()):
        specs = split_show_specs(getattr(beat, 'show', '') or '')
        if not specs:
            continue
        for spec in specs:
            out.checked += 1
            tag = spec_tag(spec)
            if not tag or _is_showable(tag, known, declared_lower,
                                       project_lower):
                continue
            out.suspects.append(IdentitySuspect(
                index=i, seq=getattr(beat, 'seq', ''),
                label=getattr(beat, 'label', ''), spec=spec, tag=tag,
                target=beat))

    if out.suspects:
        try:
            _fill_reasons(out.suspects, _Hints(
                known_tags=known_tags, known_chars=known_chars,
                mapping=mapping, all_rows=all_rows,
                project_root=project_root, dimensions=dimensions))
        except Exception as e:                  # noqa: BLE001 - 提示写不出也不拦
            swallow('app.stage_identity_guard.scan.reasons', e)
    return out


__all__ = [
    'IdentitySuspect', 'ScanResult', 'is_declared_row', 'declared_names',
    'project_script_names', 'is_showable_name', 'spec_tag',
    'split_show_specs', 'scan_stage_identity',
]
