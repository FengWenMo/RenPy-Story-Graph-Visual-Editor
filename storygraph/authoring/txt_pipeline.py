"""
纯文字导入 · **流水线**（S3）
==============================

把"作者写的纯文字章节"接进工具：`.txt` →（`.docx` 待 S4）→ `.rpy` →
**先问清未映射写法** → 过生成门禁落盘 → 交给 `EditorSession.import_chapters`。

## 为什么分两遍（这是本模块最重要的设计）

未映射的写法（`MOM"…"`）如果直接落盘，产物里就是**未定义变量** ——
Ren'Py 到那一句直接 `NameError` 崩掉（真机 5 章 1373 行里有 23 处）。
所以绝不能"先写下去、以后再修"：

    ① **规划**（纯，不写盘）：把每个文件转成 `.rpy` 文本，收集未映射写法
       → 让用户做决定（弹窗）
    ② **冲突预检**（纯读，2026-10-26 用户要求"先问后写"）：撞 label /
       会覆盖手写文件时，把**新旧内容对比**摆到弹窗里，一份一份决定
       「覆盖旧文件 / 改个名字写入 / 这次跳过」；点取消 = 一个字节都不写
       （见 `authoring.import_review`）
    ③ **落地**：决定写回 `characters.xlsx` → **重跑转换**（这时写法都认得了）
       → 过 `write_generation` 落盘 → 导入剧情图

于是只有两种结局：**产物是干净的**，或者**什么都没写**。没有"写了一半"。

## 命名与落点

- **落点 = `<项目根>/game/<标签>.rpy`**（没有 game/ 就直接放项目根）——
  作者看得见自己导入了什么；`build/renpy/` 是表格导出的地盘，两个不要混。
- **标签（文件名）**：优先取文件名里的数字链前缀 → `chapter1_2`
  （`2-1-2凌跃捷线-定稿.txt` → `chapter2_1_2`，**段数不截断**），否则用
  文件名规范化（**确定性**，不掺时间戳）。与真机 `chapter1_2.rpy` 的习惯一致。
- 同批两个文件推成同一个 label → 文件名**和产物里的 label 一起**加 `_2`
  后缀，并在 messages 里说明（只改文件名会造出两份撞名产物，落盘必被
  `LABEL_COLLISION` 拦下 —— 2026-10-26 真机）。
- 目标**已存在且不是工具生成的** → `write_generation` 会拒写（手写护栏），
  这里原样报出来；目标存在且**带生成标记** → 允许重导（幂等）。

## 已知局限（**留了接口**，用户 2026-10-10 明确"先不考虑"）

同一个写法在不同章节可能指**不同角色**（`L` 在第 1 章是「蓝」、第 3 章是「李」）。
现在决策是**项目级**（写进 `characters.xlsx`，一次决定全项目生效）。
接口留在 `Decision.by_file`：每条决定可以带"只在某个文件里生效"的限定，
落地时按文件分组应用即可 —— 现在所有调用方都不传，行为就是全项目。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.authoring import char_decisions as CD
from storygraph.authoring.import_review import apply_decisions, preflight
from storygraph.authoring.txt_import import (CharacterMap, ConversionResult,
                                            chapter_id_from_filename,
                                            convert_text, label_from,
                                            read_text_file)
from storygraph.story.generation_guard import write_generation

#: 支持的输入后缀
TXT_SUFFIXES = ('.txt',)
DOCX_SUFFIXES = ('.docx',)


@dataclass
class PlannedFile:
    """一个待导入章节的完整处境（**还没写盘**）。"""
    source: Path
    target: Path
    label: str
    result: ConversionResult
    written: bool = False
    blocked: list = field(default_factory=list)     # 门禁 block 级问题
    error: str = ''
    #: 源文本（改名要**重跑转换**，见 `import_review.apply_decisions`）。
    source_text: str = ''
    #: 同批撞名后**改名前**的 label（空 = 没改名）。产物里的 label 已同步成
    #: `label`，这里只用来"说出口"（见 `run` 的 messages）。
    renamed_from: str = ''
    #: 冲突预检里人选了"覆盖旧文件" → 强制写（旧文件先备份 `.bak`）。
    force: bool = False
    #: 覆盖旧文件时，旧文件的全文/路径（喂给门禁算"哪些顶层块会丢"+ 沿用
    #: 它那副换行/BOM 载体风格）。普通写入留空。
    replace_source_text: str = ''
    replace_source_path: Path = None
    #: 门禁拦下时那份对照副本（`build/review/*.blocked.rpy`），好让用户找得到。
    review_path: Path = None
    #: 磁盘上已经是这份内容 → **没重写**（于是也没多一份 `.bak`）。
    unchanged: bool = False

    @property
    def rpy_text(self) -> str:
        return self.result.rpy_text


@dataclass
class Plan:
    """一批纯文字的导入规划（`run()` 第一遍的产物）。"""
    files: list = field(default_factory=list)            # [PlannedFile]
    skipped: list = field(default_factory=list)          # [(路径, 原因)]
    #: token → [候选…]（要人决定的写法）
    ambiguous: dict = field(default_factory=dict)
    unmapped: dict = field(default_factory=dict)
    proposals: list = field(default_factory=list)        # [CD.Proposal]
    note: str = ''

    @property
    def needs_decision(self) -> bool:
        return bool(self.proposals)

    @property
    def ok(self) -> bool:
        return bool(self.files) and not self.skipped


# ===================== 命名 =====================


def target_stem(source, label: str = '') -> str:
    """
    输入文件 → 目标文件名（**不含后缀**）。全部**确定性**（无时间戳/随机）。

    优先级（**实测校准过**，见下）：

      1. **章节头推出来的 label**（`1.0 chapter0-0 初入大学` → `chapter0_0`）——
         章节头是作者写的编号，最权威；
      2. 文件名的数字链前缀（`1-2蓝之初见-定稿.txt` → `chapter1_2`；
         `2-1-2凌跃捷线-定稿.txt` → `chapter2_1_2`，**段数不截断**）；
      3. 文件名规范化。

    ⚠ 为什么章节头优先：真机 5 章的**文件名编号与章节头差一位**
    （`1-0初入大学-定稿.txt` 的头是 `chapter0-0`）—— 拿文件名去命名就会得到
    `chapter1_0.rpy`，与作者心里的那一章对不上，还会和 `chapter0_0` 抢 label。

    ⚠ 数字链解析**只有一处实现**（`txt_import.chapter_id_from_filename`）：
    这里从前自己抄了一份只取两段的正则，正是"`2-1-2` 被判成 `chapter2_1`"
    那半个 bug 的来源（2026-10-26 真机）。
    """
    if label:
        return re.sub(r'[^A-Za-z0-9_\u4e00-\u9fff]', '_', label).strip('_') \
            or 'chapter'
    bid = chapter_id_from_filename(Path(source).name)
    if bid:
        return bid
    stem = Path(source).stem
    return re.sub(r'[^A-Za-z0-9_\u4e00-\u9fff]', '_', stem).strip('_') or 'chapter'


def plan_target(project_root, source, label: str = '', base_dir=None) -> Path:
    """
    决定写到哪：**默认 `<项目根>/game/<stem>.rpy`**，可显式指定导出文件夹。

    ⚠ 与 `generation_guard.planned_output_path`（表格导出走 `build/renpy/`）
    **刻意不同**：那条路是"表格 → 脚本"的产物目录；这条路是作者要把章节
    **加进项目**，必须落在 Ren'Py 真正会扫描的 `game/` 里。

    `base_dir`（2026-10-26 用户要求）：**不写死任何子目录**。
    有的项目章节全在 `game/chapter/`，有的根本没有这个目录 ——
    工具不再猜，界面会先问一句"导出到哪个文件夹"，把人选的目录传进来。
    不传时保持旧行为（`<根>/game/`，没有 game/ 就项目根）。
    """
    root = Path(project_root)
    stem = target_stem(source, label)
    if base_dir is not None:
        return Path(base_dir) / f'{stem}.rpy'
    base = root / 'game' if (root / 'game').is_dir() else root
    return base / f'{stem}.rpy'


# ===================== 第一遍：规划（纯，不写盘） =====================

def _read_source(path: Path) -> str:
    """读输入：`.txt` 走既有解码链；`.docx` 走标准库抽取（S4）。"""
    suffix = path.suffix.lower()
    if suffix in TXT_SUFFIXES:
        return read_text_file(path)
    if suffix in DOCX_SUFFIXES:
        from storygraph.authoring.docx_import import docx_text
        return docx_text(path)
    raise ValueError(f'不支持的输入类型 {suffix or "(没有后缀)"}：{path.name}')


def build_plan(project_root, files, rows=None, out_dir=None) -> Plan:
    """
    把一批纯文字文件转成 `.rpy` 文本并推 label（**纯函数：不写盘、不读配置**）。

    `rows` 是角色表（`{变量: 行}`）；不给他就只用"表命中/缩写形状"那两条判据。
    `out_dir` 是**导出文件夹**（界面先问人；不传 = 旧行为 `<根>/game/`）——
    见 `plan_target`。真机上"章节住在 `game/chapter/` 还是 `game/`"没有统一
    答案，所以工具不猜。
    """
    root = Path(project_root) if project_root else None
    chars = CharacterMap(rows)
    plan = Plan()
    seen_stems = {}
    for fp in (files or ()):
        path = Path(fp)
        try:
            text = _read_source(path)
        except (ValueError, UnicodeDecodeError, OSError) as exc:
            plan.skipped.append((str(path), str(exc)))
            continue
        if root is None:
            plan.skipped.append((str(path), '没有项目根，不知道写到哪'))
            continue
        # ⚠ label 与产物**必须同源**：label 在这里算一次，再显式传给
        # `convert_text`（它自己也会推一次；不显式传就可能出现
        # "文件名用 A、产物里写着 B"）。
        label = label_from(text, path.name)
        target = plan_target(root, path, label, base_dir=out_dir)
        renamed_from = ''
        # 同批里两个文件算出同一个目标名 → 必须错开，否则后一个会覆盖前一个
        if target in seen_stems:
            n = 2
            while target.with_name(f'{target.stem}_{n}.rpy') in seen_stems:
                n += 1
            renamed_from = label
            target = target.with_name(f'{target.stem}_{n}.rpy')
            # ⚠ **文件名改了，产物里的 label 必须跟着改**（P0，2026-10-26 真机）。
            # 只改文件名 = 两份产物声明同一个 label：第一份落进 `game/` 之后，
            # 第二份的 `LABEL_COLLISION` 必然命中，于是"作者看着文件叫
            # `chapter2_1_2`，门禁却说 `label chapter2_1` 重名"。
            # 门禁是对的（两个文件真会撞），错的是我们造了一对撞名的产物。
            label = target.stem
        seen_stems[target] = str(path)
        result = convert_text(text, characters=chars, filename=path.name,
                              label=label)
        plan.files.append(PlannedFile(source=path, target=target, label=label,
                                      result=result, source_text=text,
                                      renamed_from=renamed_from))
    if not plan.files and plan.skipped:
        plan.note = '没有任何文件可导入（看 skipped）'
    return plan


def proposals_for(plan: Plan, rows=None) -> list:
    """规划里所有"要人决定"的写法 → `[Proposal]`（弹窗/CLI 的数据源）。"""
    rows = rows or {}
    merged = {}
    for f in plan.files:
        for tok, n in (f.result.unmapped or {}).items():
            merged[tok] = merged.get(tok, 0) + int(n)
    # 用一份"合成转换结果"喂给 `suggestions`：这样弹窗里的样例/计数是全批的
    fake = ConversionResult(rpy_text='', label=plan.files[0].label if plan.files
                            else '', lines=[], counts={}, unmapped=merged)
    out = []
    for sug in CD.suggestions(fake, None, rows):
        # 样例从各文件里补（`suggestions` 只认 lines，这里手工拼）
        for f in plan.files:
            for tl in f.result.lines:
                if getattr(tl, 'speaker_token', '') == sug.token and \
                        len(sug.samples) < 3:
                    sug.samples.append((getattr(tl, 'no', 0),
                                        f'{f.source.name}: '
                                        f'{(getattr(tl, "raw", "") or "").strip()[:60]}'))
        out.append(CD.propose_one(rows, sug))
    return out


# ===================== 第二遍：落地 =====================

@dataclass
class ImportResult:
    """`run()` 的返回（UI 只需要看这个）。"""
    ok: bool = False
    written: list = field(default_factory=list)       # [Path]
    blocked: list = field(default_factory=list)       # [(路径, 原因)]
    skipped: list = field(default_factory=list)       # [(路径, 原因)]
    needs_decision: bool = False
    proposals: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    plan: Plan = None
    import_result: dict = None                        # EditorSession 那边的返回
    decisions: dict = field(default_factory=dict)     # 应用决定的统计
    #: 冲突预检结果（`import_review.Review`）。`items` 非空 = 有文件撞名 /
    #: 会覆盖手写文件。`needs_review=True` = 问了但人选择"先不写"。
    review: object = None
    needs_review: bool = False

    @property
    def summary(self) -> str:
        if self.needs_decision:
            return f'有 {len(self.proposals)} 种写法需要你确认角色'
        if self.needs_review:
            n = len(self.review.items) if self.review is not None else 0
            return f'{n} 个文件撞名/会覆盖手写文件 —— 你选择先不写'
        if self.blocked:
            return f'{len(self.written)} 个已导入，{len(self.blocked)} 个被门禁拦下'
        if self.skipped and not self.written:
            return f'没有任何文件可导入（{len(self.skipped)} 个跳过）'
        return f'导入 {len(self.written)} 个章节'


def _identical_to_disk(planned) -> bool:
    """
    磁盘上那一份是不是**已经就是**这份产物？

    为什么需要（2026-10-26，用户"我一个个删 .bak 老狼狈了"的账）：反复导入
    同一批 txt 时，产物逐字节没变，但每一次都会走一遍"备份 + 原子写"——
    于是 `*.rpy.bak` / `*.rpy.bak-<时间戳>` 越攒越多（每份最多留 5 代），
    mtime 也一直在跳。内容一样就**不碰文件**，那一份 `.bak` 自然也不产生。

    ⚠ 这是"读来比较"，不是"读来写回"（后者必须走 `source_style` 的严格解码，
    见 P0-B）。读不出来就当作"不一样"，照常走门禁。
    """
    try:
        return Path(planned.target).read_text(encoding='utf-8') \
            == planned.rpy_text
    except (OSError, UnicodeDecodeError):
        return False


def _write_all(plan: Plan, project_root, force: bool = False) -> None:
    """
    把规划里的每个文件过门禁落盘（**唯一 .rpy 落盘入口**）。

    `force` 是整批的逃生舱；单份的"强制更换"来自冲突预检里人选了
    「覆盖旧文件」（`PlannedFile.force`）—— 那条还额外把旧文件全文/路径
    喂给门禁，让它**算得出**"旧文件里哪些顶层块会丢"并记进回执。

    内容与磁盘**逐字节一致** → 不重写（也就不产生 `.bak`，见
    `_identical_to_disk`）。此时标 `written=True`：那一份产物确实已经在
    目标位置上了，界面上报"已导入"是诚实的；另标 `unchanged=True`
    让回执说清"没重写"。
    """
    for f in plan.files:
        if _identical_to_disk(f):
            f.written = True
            f.unchanged = True
            continue
        art = write_generation(
            f.rpy_text, f.target, project_root=project_root,
            force=bool(force or f.force),
            source_text=(f.replace_source_text or None),
            source_path=(f.replace_source_path or None))
        f.written = bool(art.written)
        f.review_path = getattr(art, 'review_path', None)
        f.blocked = [f'{i.code}: {i.message}' for i in art.blocking]
        if not art.written:
            f.error = '；'.join(f.blocked) or '门禁拦下（没有写盘）'


def run(game_dir, files, *, rows=None, project_root=None, decisions=None,
        import_chapters=None, ask=None, apply=None, dry_run=False,
        force=False, resolve=None, out_dir=None) -> ImportResult:
    """
    一条龙：纯文字 →（问角色）→（**问冲突**）→ 落盘 → 导入剧情图。

    参数：
      · `game_dir`：**章节 .rpy 落到哪**（UI 传 `self._project_root`，
        函数内部会优先用 `<root>/game/`）；
      · `files`：输入文件（`.txt`；`.docx` 走同一条链）；
      · `rows`：角色表 `{变量: 行}`；
      · `decisions`：`[CD.Proposal]`（弹窗里人点过的；**给空就直接用系统建议**）；
      · `import_chapters`：拿到写好的路径后调它（典型是
        `EditorSession.import_chapters`）——不给就只落盘不导图；
      · `ask`：`callable(proposals) -> [Proposal] | None`，第一遍遇到未映射写法
        时问一句；返回 `None` 表示"这次不决定"→ 返回 `needs_decision`，**不写盘**；
      · `apply`：`callable(decisions) -> dict`，**谁来把决定写进角色表**。
        默认 `CD.apply_decisions`（纯逻辑）。⚠ 会话层要传自己的实现：它写完之后
        还必须**重读角色表**，否则同一个写法下一次导入会**再问一遍**（实测踩到）。
      · `resolve`：`callable(review) -> [决定] | None`，**冲突预检**（2026-10-26
        用户要求"先问后写"）。`review` 是 `import_review.Review`；每份要人拿主意
        的文件给一条 `{'source': 源路径, 'action': 'replace'|'rename'|'skip',
        'label': 新名字(仅 rename)}`。返回 `None` = "这次先不写" → 返回
        `needs_review=True` 且**一个字节都不写**（也不产生 `.blocked.rpy`）。
        ⚠ 不给 `resolve`（CLI / 单测）时行为与从前**完全一致**：照旧写、
        照旧被门禁拦下、照旧留对照副本。
      · `dry_run=True`：只规划（**仍会算冲突预检**，好让界面提前说清），绝不写盘。
      · `out_dir`：**导出文件夹**（界面先问一句"导出到哪个文件夹"）。
        不传 = 旧行为 `<根>/game/`。⚠ 不写死 `game/chapter/` 这类子目录 ——
        很多项目根本没有它，工具不猜（2026-10-26 用户要求）。

    返回 `ImportResult`（含 `plan` / `review` / 门禁结论 / 导入结果）。
    """
    root = Path(project_root) if project_root else Path(game_dir or '.')
    plan = build_plan(root, files, rows, out_dir=out_dir)
    out = ImportResult(plan=plan, skipped=list(plan.skipped))
    # 冲突预检在**任何写入之前**跑（纯读）。dry_run 也跑：界面第一遍就能说
    # "有 3 个文件撞名"，而不是等点完角色弹窗再吓人一跳。
    out.review = preflight(plan, root, out_dir=out_dir)
    if dry_run or not plan.files:
        out.messages.append(plan.note or '没有可导入的文件')
        return out

    rows = rows or {}
    proposals = proposals_for(plan, rows)
    if proposals:
        chosen = None
        if decisions is not None:
            chosen = list(decisions)
        elif ask is not None:
            chosen = ask(proposals)
        if not chosen:
            # ⚠ 没决定就**一个字都不写**（产物里会有未定义变量 → 运行期必崩）
            out.needs_decision = True
            out.proposals = proposals
            out.messages.append(
                f'有 {len(proposals)} 种写法不在角色表里，需要先确认是谁'
                f'（未确认前不落盘 —— 否则产物里是未定义变量，游戏会崩）')
            return out
        if apply is None:
            stats = CD.apply_decisions(CD.asset_dir_of(root), chosen, rows)
        else:
            stats = apply(chosen)
        out.decisions = stats
        out.messages.extend(stats.get('messages') or [])
        # 决定写回了角色表 → **重新加载**（不能用旧的内存副本）
        rows = _reload_rows(root, rows)
        plan = build_plan(root, files, rows, out_dir=out_dir)
        out.plan = plan
        out.proposals = proposals_for(plan, rows)
        if out.proposals:
            # 还有认不出的（例如新建失败/跳过）→ 仍然不写
            out.needs_decision = True
            out.messages.append(
                f'还有 {len(out.proposals)} 种写法没有对应角色，未落盘')
            return out
        # 角色决定会重跑转换 → 预检要按**新的**产物重算（label 不变，
        # 但正文变了；对比栏里必须是最终产物，不能是上一轮的）。
        out.review = preflight(plan, root, out_dir=out_dir)

    review = out.review
    if review is not None and review.items:
        if resolve is None:
            # 无人可问（CLI / 单测）→ 保持老行为：照旧写完由门禁逐份裁决。
            out.messages.append(
                f'{review.summary()}（没有可问的人 → 照旧逐份落盘，'
                f'由生成门禁裁决；被拦下的会留对照副本）')
        else:
            picks = resolve(review)
            if picks is None:
                # "先不写" = **什么都不做**：不落盘、不留 .blocked.rpy。
                out.needs_review = True
                out.messages.append(
                    f'冲突预检：{review.summary()} —— 你选择先不写，'
                    f'这次一个字节都没动（也没有留下任何对照副本）')
                return out
            out.messages.extend(apply_decisions(plan, review, picks, rows))

    _write_all(plan, root, force=force)
    out.written = [f.target for f in plan.files if f.written]
    out.blocked = [(str(f.target), f.error) for f in plan.files
                   if not f.written and f.error]
    # ⚠ `skipped` 在函数开头是**拷贝**；冲突预检（`apply_decisions`）之后还会
    # 往 `plan.skipped` 里加"你选择跳过 / 名字不合法"这些条目 → 必须回填，
    # 否则界面拿到的 `skipped` 是旧的（实测：UI 只说"一个文件都没写"，
    # 却不说是**为什么**没写）。
    out.skipped = list(plan.skipped)
    for path, why in plan.skipped:
        out.messages.append(f'跳过 {Path(path).name}：{why}')
    for f in plan.files:
        # 同批撞名后改了 label 的，**必须说出口**：产物里的 label 与作者预期的
        # 那个名字不一样了，这属于"静默改名"—— 不报就是第二个 bug。
        if f.renamed_from:
            out.messages.append(
                f'[{f.source.name}] 和本批前面的文件撞了同一个章节名 '
                f'{f.renamed_from}（两份源文推成同一个 label）：已改名为 '
                f'{f.label}，文件名和产物里的 `label {f.label}:` 一起改了。'
                f'如果它们本来就是同一章，请只留一份再导入。')
        # 被门禁拦下时那份对照副本在哪 —— 用户找得到才删得掉（不然只能
        # 在 vscode 里一个个翻，2026-10-26 用户原话"老狼狈了"）。
        if not f.written and f.review_path:
            out.messages.append(
                f'[{f.source.name}] 对照副本：{f.review_path}'
                f'（它在 build/review/ 里，不在 Ren\'Py 扫描范围，确认后可删）')
        if f.unchanged:
            out.messages.append(
                f'[{f.source.name}] 和磁盘上的那份**一模一样**，没重写'
                f'（也就没多一份 .bak）')
        for w in (f.result.warnings or []):
            out.messages.append(f'[{f.source.name}] {w}')

    if out.written and import_chapters is not None:
        try:
            out.import_result = import_chapters([str(p) for p in out.written])
        except Exception as exc:                     # noqa: BLE001
            out.messages.append(f'导入剧情图失败：{exc}')
    out.ok = bool(out.written)
    return out


def _reload_rows(root, fallback):
    """
    决定写回表之后重新读角色表（**必须重读**：内存里那份没有新别名/新角色）。

    读不到就退回传进来的那份（宁可旧，也不让整批导入因为读表失败而崩）。
    """
    try:
        from storygraph.pipeline.build import load_asset_registry
        reg = load_asset_registry(Path(root))
        if reg is not None and getattr(reg, 'characters', None):
            return reg.characters
    except Exception:                               # noqa: BLE001
        from storygraph.core.safeguard import swallow
        swallow('txt_pipeline._reload_rows')
    return fallback


__all__ = ['DOCX_SUFFIXES', 'ImportResult', 'Plan', 'PlannedFile',
           'TXT_SUFFIXES', 'build_plan', 'plan_target', 'proposals_for', 'run',
           'target_stem']
