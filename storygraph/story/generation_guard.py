"""
storygraph.story.generation_guard
=================================
**生成安全门禁**：绝不把会让游戏跑不起来的脚本写进项目。

存在的理由是一次真实事故：生成的产物里躺着 18 行 `show <中文角色名>`
（Ren'Py 的图像 tag 只允许字母/数字/下划线），同一份文件还与原文
**重复定义了同一个 label** —— 而 Ren'Py 把 `game/` 下所有 .rpy 当同一个脚本，
重名 label 会让游戏直接起不来。

规则分级（宁可少报也不能把正常脚本拦下）：
  · `block` —— 中文图像名 / `at` 后面没有一个合法标识符 / **会被扫描的产物**
    与项目其他 .rpy 抢 label
  · `warn`  —— 产物落在 `game/` 里（建议挪到 `build/renpy/`）、还有未解决的 `# TODO`

`LABEL_COLLISION` 只在产物真会落进 `<项目根>/game/` 时判（见 `paths.inside_game_dir`）：
写到 `build/` 或临时目录的产物不被 Ren'Py 扫描，与原文同名无害。
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.paths import (
    _guess_project_root,
    inside_game_dir,
)




# ===================== 生成安全门禁 =====================
#
# 为什么必须有这道闸（2026-09-28 实测事故）：
# 真实项目 `sr_general/game/chapter/` 里躺着 18 行 `show <中文名>`——
# 而 Ren'Py 规定图像 tag/属性只能是字母、数字、下划线（见官方 Quickstart），
# 那 18 行是**语法非法**的；同一份生成文件还和原文重复定义了 `label chapter1_4`。
# 根因是"立绘列与舞台列双写"（见 `compose_show_cell` / `compose_stage_extra`），
# 但只要生成器还能**静默把坏脚本写进 game/**，这个坑就会反复被人踩回来。
#
# 所以这里刻意做成"纯函数 + 明确分级"：
#   · block 级 = 确定会让游戏跑不起来 → 拒写；
#   · warn  级 = 可疑但不一定错 → 照写，但报出来。
# 宁可少报也不能把正常脚本拦下来（记忆文件里的校验分级原则同样适用）。

#: Ren'Py 允许的图像 tag / 属性 / transform 名：字母数字下划线（官方规则）。
#: 中文 tag 不是"风格问题"，是语法错误——所以按"非 ASCII 即非法"判。
_ASCII_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

@dataclass(frozen=True)
class GuardIssue:
    """生成安全门禁的一条发现。"""

    code: str
    level: str                  # 'block' | 'warn'
    message: str
    line_no: int = 0

    @property
    def is_blocking(self) -> bool:
        return self.level == 'block'

    def format(self) -> str:
        where = f'第 {self.line_no} 行' if self.line_no else '整体'
        return f'[{self.level}] {self.code}（{where}）：{self.message}'



class GenerationBlocked(Exception):
    """生成门禁拦下了落盘（含 block 级问题清单）。"""

    def __init__(self, out_path, issues):
        self.out_path = Path(out_path)
        self.issues = list(issues)
        detail = '\n'.join('  ' + i.format() for i in self.issues)
        super().__init__(
            f'生成的脚本有 {len(self.issues)} 条致命问题，已拒绝写入 '
            f'{self.out_path}：\n{detail}')

    def format(self) -> str:
        return str(self)

    def __str__(self):
        detail = '\n'.join('  ' + i.format() for i in self.issues)
        return (f'生成门禁拦下 {self.out_path}：'
                f'{len(self.issues)} 条致命问题\n{detail}')



def occupied_labels(project_root=None, exclude=()) -> set:
    """
    扫项目里**其他** .rpy 已经定义的 label 名（生成前用来判重名）。

    Ren'Py 会把 game/ 下所有 .rpy 当成"一个大脚本"（官方 Quickstart 原话），
    所以不同文件里重名 label 是硬错误。`parsing.discover.discover_scripts`
    已经排除了 `tl/` 与 `*.generated.rpy`，正好复用它的口径。

    （2026-09-28：这里原先 import 的是 `pipeline.build.discover_scripts`，
    让 story 层反向依赖 pipeline 层、只能靠延迟 import 压着；该函数已下移到
    `parsing.discover`，依赖方向恢复单向。）

    project_root 为空、或项目里没有 game/ 时返回空集合——**宁可少报**，
    不要让没有项目的单测/CLI 场景被误拦。
    """
    if not project_root:
        return set()
    try:
        from storygraph.parsing.discover import discover_scripts
    except Exception:
        return set()

    exclude = {Path(p).resolve() for p in (exclude or ()) if p}
    found = set()
    try:
        scripts = discover_scripts(project_root)
    except Exception:
        return set()
    for fp in scripts:
        try:
            if Path(fp).resolve() in exclude:
                continue
            found.update(_declared_labels(fp))
        except OSError:
            continue
    return found



def _declared_labels(path) -> set:
    """文件里声明的 label 名（含中文 label——Ren'Py 的 label 名允许非 ASCII）。"""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return set()
    return set(re.findall(r'^label\s+([^\s:#]+)', text, re.M))

@dataclass(frozen=True)
class _ImageRef:
    """生成文本里的一条舞台指令：动词 + 图像名 + 行号。"""

    verb: str
    image: str
    line_no: int



#: `scene` / `show` / `hide` 后面跟的那个"图像名"只取**第一个 token**：
#: `show ling happy at left` 的 tag 部分就是 `ling`，
#: 而 `show ling_close_happy` 整体是一个 token。
_STAGE_VERB_RE = re.compile(r'^(\s*)(show|hide|scene)\s+(\S+)(.*)$')


#: 图像名后面挂的标点：`show icon:`（Screen Language 的属性写法）要当成 `icon`。
_TRAILING_PUNCT = ':,;'


#: 出现这些字符说明这不是"一个图像名"而是**表达式**——
#: `Transform(zoom=scale)`、`config.x`、`image_info[0]` 都合法，一律放行。
#: 宁可少报：把程序手写的 UI 代码拦下来，比漏掉一行中文 tag 更贵。
_EXPR_CHARS = '([{.'



def _is_valid_ident(name: str) -> bool:
    """
    这个名字像"合法的 Ren'Py 图像名 / transform 名"吗？

    Ren'Py 的规则是「字母开头，只含字母数字下划线」（官方 Quickstart）。
    但真实项目里 `at` 后面还经常跟**表达式**（`Transform(zoom=scale)`、
    `eye_tracking_transform(...)`）和 Screen Language 的 `at transform:`，
    这些都不是标识符却完全合法——所以先剥标点，再对表达式整体放行。
    """
    text = (name or '').strip().lstrip('\ufeff')
    text = text.rstrip(_TRAILING_PUNCT)
    if not text:
        return True
    if any(ch in text for ch in _EXPR_CHARS):
        return True
    return bool(_ASCII_IDENT_RE.match(text))



def _image_refs(text: str) -> list:
    """抽出生成文本里所有的 show/hide/scene 图像名（跳过注释行）。"""
    refs = []
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        m = _STAGE_VERB_RE.match(line)
        if m:
            refs.append(_ImageRef(m.group(2), m.group(3), i))
    return refs



def generation_guard(text: str, out_path, *,
                     project_root=None,
                     occupied_labels: set = None) -> list:
    """
    生成安全检查。**纯函数、不写盘、无 UI 依赖**，所以能被无头测试直接调用。

    规则（每条都会带上原文行号，方便对着表改）：

      · `CJK_IN_IMAGE_TAG`（block）—— show/hide/scene 的图像名不是
        "字母/数字/下划线"，也就是 Ren'Py 语法上根本不存在这个图像。
        中文名出现在这里是**事故**，不是风格问题。
      · `CJK_IN_TRANSFORM`（block）—— `at <非标识符>`，同理。
      · `LABEL_COLLISION`（block）—— 要写的 label 在项目其他 .rpy 里已存在。
        这类文件一旦落进 game/，游戏直接起不来。
      · `UNRESOLVED_TODO`（warn）—— 生成结果里有 `# TODO: 缺少资源 …`。
        这条本身是设计内的兜底（编译不因缺资源而失败），
        它提醒的是"这份脚本还不完整，别当成成品发出去"。
    """
    issues = []
    out_path = Path(out_path) if out_path else None

    # —— 1. 图像名 / transform 名的合法性 ——
    for ref in _image_refs(text):
        if not _is_valid_ident(ref.image):
            issues.append(GuardIssue(
                'CJK_IN_IMAGE_TAG', 'block',
                f'`{ref.verb} {ref.image}`：图像名含非法字符'
                f'（Ren\'Py 只允许字母/数字/下划线）。'
                f'常见原因：把角色**显示名**当成了 image tag，'
                f'或某个结构化列与「舞台（高级）」列写了同一拍的两份内容。',
                ref.line_no))
    # at 子句单独扫一遍：图像名与变换名是两个不同的合法性域。
    # 注意 `at` 后面可以跟**多个**变换（`at left slide_in`），所以逐 token
    # 判会误报——基准是"整个列表里至少有一个是合法标识符就算有人管"；
    # 一个都没有（`at 左边`）才说明这是自由文本混进了代码。
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        parts = line.split()
        if 'at' not in parts:
            continue
        after_at = parts[parts.index('at') + 1:]
        stopwords = ('with', 'behind', 'onlayer', 'zorder', 'as')
        spec = []
        for tok in after_at:
            if tok in stopwords:
                break
            spec.append(tok)
        if spec and not any(_is_valid_ident(tok) for tok in spec):
            issues.append(GuardIssue(
                'CJK_IN_TRANSFORM', 'block',
                f'`at {" ".join(spec)}`：变换名没有一个合法'
                f'（Ren\'Py 的 transform 名必须是 Python 标识符）。',
                i))

    # —— 2. label 重名 ——
    if occupied_labels:
        for name in re.findall(r'^label\s+([^\s:#]+)', text, re.M):
            if name in occupied_labels:
                issues.append(GuardIssue(
                    'LABEL_COLLISION', 'block',
                    f'`label {name}` 在项目其他 .rpy 里已经定义过。'
                    f'Ren\'Py 把 game/ 下所有 .rpy 当同一个脚本，'
                    f'重名 label 会让游戏直接起不来。'
                    f'请把产物写到 build/ 目录，或改 label 名。'))

    # —— 3. TODO 未解决 ——
    todo_lines = [i for i, line in enumerate(text.splitlines(), start=1)
                  if '# TODO:' in line]
    if todo_lines:
        issues.append(GuardIssue(
            'UNRESOLVED_TODO', 'warn',
            f'生成结果里有 {len(todo_lines)} 条 `# TODO: 缺少资源`'
            f'（第 {"、".join(str(n) for n in todo_lines[:8])}'
            f'{"…" if len(todo_lines) > 8 else ""} 行）：'
            f'这些语句没有被生成，脚本还不完整。'))

    # —— 4. 产物位置：落在 Ren'Py 扫描目录里要提醒 ——
    # 只在"项目里真有 game/ 且产物确实写进它"时报：其余情况（临时目录、
    # 单测的 tmp_path、非 Ren'Py 项目）一律不报，否则会变成噪音。
    if inside_game_dir(out_path, project_root):
        issues.append(GuardIssue(
            'OUTSIDE_BUILD_DIR', 'warn',
            f'产物写在 Ren\'Py 会扫描的 game/ 目录里（{out_path}）。'
            f'建议写到 build/renpy/：放在 game/ 下的生成文件会和'
            f'原文抢同一批 label。'))

    return issues

@dataclass(frozen=True)
class GeneratedArtifact:
    """一次"生成到磁盘"的完整结果（给 UI 用的、可测的返回值）。

    为什么做成纯逻辑而不是写在 `graph_view` 里：
    `graph_view` 是 tkinter 界面，测试环境里只有桩，装配很容易悄悄接错
    （记忆文件里就有过 `command=` 指向不存在方法的先例）。
    所以"写到哪、过不过门禁、产物副本存哪"全部放在这里，
    UI 只负责把结果摆到弹窗上。
    """

    out_path: Path
    review_path: 'Path | None'
    written: bool
    issues: list

    @property
    def blocking(self) -> list:
        return [i for i in self.issues if i.is_blocking]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if not i.is_blocking]

    @property
    def blocked(self) -> bool:
        return bool(self.blocking)



def planned_output_path(table_path, script_path=None, project_root=None) -> Path:
    """
    决定生成的 .rpy 写到哪里。

    **不再写回 .rpy 旁边。** 以前是 `table.with_suffix('.generated.rpy')`，
    而表就在 game/ 里，于是产物和原文躺在同一个目录、抢同一批 label
    （Ren'Py 把 game/ 下所有 .rpy 当同一个脚本）——一次真实事故。
    有项目根就写 `<项目根>/build/renpy/`，否则退回表旁边。
    """
    stem = Path(script_path).stem if script_path else Path(table_path).stem
    if project_root:
        return Path(project_root) / 'build' / 'renpy' / f'{stem}.rpy'
    return Path(table_path).with_suffix('.generated.rpy')



def evaluate_generation(text: str, out_path, *, project_root=None,
                        occupied: set = None) -> list:
    """跑一遍生成门禁，返回问题清单。**纯函数、不写盘。**

    给"先看结果、问过用户再写"的调用方用（UI 的确认框就该是这个顺序）。
    """
    return generation_guard(text, Path(out_path), project_root=project_root,
                            occupied_labels=occupied)



def labels_in_danger(out_path, project_root=None, exclude=()) -> set:
    """
    **只有产物真会落进 `<root>/game/` 时**才返回项目里已占用的 label 集合。

    为什么必须是**一个**函数：这个判据同时决定"要不要查重名"，
    而它以前在两个地方各写了一遍 —— 结果是 UI 绕过了 `write_generation`
    里的守卫，自己算出 50 个 label 传进去，于是**连写到 `build/renpy/`
    的产物都被判 `LABEL_COLLISION` 拒写**（用户 2026-09-28 实测：
    "我其实已经在 build 目录里面了，它还是拒绝导出"）。

    Ren'Py 把 game/ 下所有 .rpy 当同一个脚本，所以：
      · 产物会进 game/  → 重名致命，必须查；
      · 产物进 build/ 等 → 根本不被扫描，重名无害，**不许拦**。

    判定复用 `paths.inside_game_dir`（同一处实现，别再抄一份）。
    """
    if not inside_game_dir(out_path, project_root):
        return set()
    return occupied_labels(project_root=project_root, exclude=exclude)


def write_generation(text: str, out_path, *, review_on_block: bool = True,
                     project_root=None, occupied=None):
    """
    落盘，并保证"绝不把会让游戏跑不起来的脚本写进目标路径"。

      · 无 block 级问题 → 写 `out_path`，`written=True`；
      · 有 block 级问题 → **不写目标文件**，改写 `<stem>.blocked.rpy`
        供人对比，`written=False`、`review_path` 指向副本。

    为什么拦下也要留副本：只报错不给人看产物，等于让人猜哪里错了；
    但绝不能写进目标路径（那正是事故的形态）。

    `project_root` / `occupied` 决定 **LABEL_COLLISION 这条门禁判不判**：
      · 给了 project_root（或能从产物路径推出含 game/ 的项目根）→ 扫项目里
        其他 .rpy 已占用的 label，重名即 block；
      · 都没有 → 退回"不检查"（临时目录、单测场景），宁可少报。
    2026-09-28 修：以前这里写死 `project_root=None`，**等于把这条门禁关了**。

    调用方要"先问后写"就直接用 `evaluate_generation` + 自己写，
    这个函数是给"已经确认过，但还要兜一层底"的路径用的。
    """
    out_path = Path(out_path)
    root = project_root or _guess_project_root(out_path)
    if occupied is None:
        # 只有产物**会被 Ren'Py 扫描**（落在 <root>/game/ 里）时，
        # "和原文抢 label"才是致命问题；写到 build/ 或临时目录的产物
        # 根本不被扫描，与原文同名无害（见 labels_in_danger）。
        occupied = labels_in_danger(out_path, root, exclude={out_path})
    issues = generation_guard(text, out_path, project_root=root,
                              occupied_labels=occupied)
    blocking = [i for i in issues if i.is_blocking]
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if blocking and review_on_block:
            review = out_path.with_suffix('.blocked.rpy')
            review.write_text(text, encoding='utf-8')
            return GeneratedArtifact(out_path, review, False, issues)
        if blocking:
            return GeneratedArtifact(out_path, None, False, issues)
        out_path.write_text(text, encoding='utf-8')
        return GeneratedArtifact(out_path, None, True, issues)
    except OSError:
        # 写不出去（权限/占用）不该把门禁结论一起吞掉
        return GeneratedArtifact(out_path, None, False, issues)



def write_beats_to_rpy(beats: list, output_path,
                       label_name: Optional[str] = None,
                       registry=None, project_root=None) -> str:
    """
    渲染并落盘，返回生成的文件路径。

    **落盘前先过生成安全门禁**（见 `generation_guard`）：命中 block 级问题就
    拒写并抛 `GenerationBlocked`，绝不把一份会让游戏跑不起来的脚本写进项目。

    `project_root` 不给时按产物路径反推（含 game/ 的目录）——2026-09-28 修：
    以前这里调的是恒返回 None 的 `_guess_project_root()`，
    **LABEL_COLLISION 门禁一直是关的**。

    要"先看结果、问过用户再写"的调用方（例如 UI 的确认框）请用
    `evaluate_generation` + `write_generation` 自己编排顺序。
    """
    text = render_beats_to_rpy(beats, label_name=label_name,
                               registry=registry)
    output_path = Path(output_path)

    root = project_root or _guess_project_root(output_path)
    # label 重名**只在产物会被 Ren'Py 扫描时**才算致命（见 labels_in_danger）。
    occupied = labels_in_danger(output_path, root, exclude={output_path})
    issues = generation_guard(text, output_path, project_root=root,
                              occupied_labels=occupied)
    blocking = [i for i in issues if i.is_blocking]
    if blocking:
        raise GenerationBlocked(output_path, blocking)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding='utf-8')
    return str(output_path)
