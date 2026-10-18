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
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import (GENERATOR_MARKER,
                                        SHOW_CLAUSE_KEYWORDS)
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.paths import (
    _guess_project_root,
    inside_game_dir,
    write_text_atomic,
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

#: 认标记时只看**不变的那一段**（逗号之前）。
#:
#: 为什么要截断（2026-10-05，我自己写的测试抓到的）：完整标记是
#: `# 由剧本表自动生成，请勿在此文件手工改动结构`，而人（和测试）写
#: `# 由剧本表自动生成` 就够了 —— 逗号后面那句是"叮嘱",不是身份。
#: 要求整串相等的话，任何只写前半句的产物都会被当成手写文件拒写，
#: 而那正是"我明明是用工具生成的"这类最难自查的事故。
_MARKER_PREFIX = GENERATOR_MARKER.split('，')[0]


def _looks_handwritten(path: Path) -> bool:
    """
    `path` 是不是**手写文件**（已存在、且首行没有生成标记）？

    判据刻意只看**首行**（生成器总是把标记写在第 1 行）：

      · 文件不存在            → False（不是"手写"，是"新建"，放行）；
      · 首行含 `GENERATOR_MARKER` → False（工具自己写的，可以再导出）；
      · 读不出来（权限/编码） → **True**（读不出来就别赌，宁可拦下）。

    为什么要单独一个函数：判据会在"写盘前"和"CLI 预检"两处用到，
    抄两份迟早漂移（`excel/cells.py` 的教训）。
    """
    p = Path(path)
    try:
        if not p.is_file():
            return False
    except OSError:
        return True
    try:
        with p.open('r', encoding='utf-8', errors='replace') as fh:
            first = fh.readline()
    except OSError:
        return True
    return _MARKER_PREFIX not in first


def _make_blocking_issue(path: Path, reason: str) -> GuardIssue:
    """把"不许覆盖手写文件"包成一条 block 级发现（与语法门禁同一套类型）。"""
    return GuardIssue(code='HANDWRITTEN_TARGET', level='block',
                      message=f'{path}：{reason}')


def would_destroy_opaque(target, source_path=None) -> bool:
    """
    "把全量产物写到 `target`"，会毁掉**工具不认识的内容**吗？

    这是 `EditorSession.export` 拦不拦的判据（P0，2026-10-05）。
    全量生成只认得"拍"，`default` / `define` / `init python` /
    `transform` / `screen` 一行都留不下 —— 所以只有当目标文件里
    **可能真的有那些东西**时才必须拦：

      · `target` 就是**源 .rpy**（原地导出）→ True
        —— 作者手写的章节文件，几乎必然含第三类内容；
      · `target` 已存在且**没有生成标记** → True —— 别人的手写文件；
      · 其余（目标不存在 / 是工具以前生成的）→ False
        —— 覆盖它毁不掉任何东西，全量正是要做的事。

    为什么不只调 `_looks_handwritten`：原地导出那条要单独判。
    它可能正是一份**带生成标记**的文件（工具以前生成的），
    但**源区间对不上**导致补丁失败 —— 那种情况覆盖它是安全的；
    可是"源 == 目标"意味着用户在编辑自己的章节，
    退回全量会把 `init python` 冲掉。所以两条都要看。

    **这条判据刻意只管"会不会毁东西"**，不管"补丁为什么失败"：
    编辑 ch01、导出到一个**新**路径时补丁也会失败，而那是完全正常的
    操作（`test_export_reflects_the_edit` 就是这条，第一版把它拦掉了）。
    """
    t = Path(target) if target else None
    if t is None:
        return False
    try:
        if source_path and Path(source_path) == t:
            return True
    except OSError:
        return True
    return _looks_handwritten(t)


def merging_onto(out_path, source_path) -> bool:
    """
    这次增量合并的**底稿**，就是马上要写的那个文件吗？

    这是"手写护栏要不要让路"的**唯一判据**（2026-10-05 实测修，见
    `write_generation`）。为什么必须是"底稿 == 目标"而不是笼统的
    "这次走了增量"：

      · 底稿 == 目标（用户改自己这一章、原地写回）→ 合并出来的全文
        **包含目标文件的全部内容**（改过的那几行除外）→ 覆盖它
        **一个字节的非本拍内容都不会丢** → 放行是对的；
      · 底稿 != 目标（编辑 ch01、把产物写到别人的手写 ch02 上）→
        产物里有 ch01 的全部内容，**却没有 ch02 的**
        `default` / `init python` → 覆盖会把 ch02 的定义全抹掉 →
        **必须照旧拦下**。

    第一版把这条判据交给调用方口头声明（`merged_from_source=True`），
    于是"说走了增量"就能越过护栏 —— 上面第二种情况会被静默放行。
    现在改成报**底稿路径**，由这里自己比，调用方无从搞错。

    比对用 `Path` 相等（与 `would_destroy_opaque` 同一口径）：
    两边都由调用方从同一来源取路径，不做 resolve —— 免得符号链接 /
    大小写归一化把"同一个文件"判成两个。
    """
    if not source_path or not out_path:
        return False
    try:
        return Path(source_path) == Path(out_path)
    except TypeError:
        return False


def ensure_generator_marker(text: str) -> str:
    """
    保证产物首行是 `GENERATOR_MARKER`；已经有了就**一个字不动**。

    为什么必须有这一步（2026-10-05 实测）：护栏靠"首行有没有标记"判断
    一个已存在的文件是不是工具自己写的。而**增量补丁那条路**产出的首行是
    **源文件的首行**（合并策略是"保留原文那一行"，常常是 `default …`），
    于是补丁产物没有标记 —— 护栏认不出自家产物：

        第一次导出 → ok=True    （补丁成功，写出去的没有标记）
        第二次导出 → 拒绝覆盖   （被判成"手写文件"）

    也就是说"再导出一次"在一个已由工具生成的文件上**必然失败**。
    所以标记要在**写盘之前**确保，而不是只在全量渲染时写。

    为什么放在这里而不是 `beat_render`：判据与"认标记"（`_looks_handwritten`）
    是一对，必须同进同退 —— 两处分头写就是下一次漂移的起点。
    """
    body = str(text or '')
    first = body.split('\n', 1)[0]
    if _MARKER_PREFIX in first:
        return body
    if not body.strip():
        return GENERATOR_MARKER + '\n'
    return f'{GENERATOR_MARKER}\n\n{body}'


# ---------------------------------------------------------------------------
#  「第三类内容会不会丢」—— 全量生成的产物 vs 它原来的那一章
# ---------------------------------------------------------------------------

def _squash(line: str) -> str:
    """去掉全部空白（比对用）——不要引进来 `patcher._squash`，那是同层的私有名。"""
    return re.sub(r'\s+', '', str(line or ''))


def top_level_chunks(text: str) -> list:
    """
    **顶层语句块**（不在任何 `label` 里的那部分）：`[(行号, [行…]), …]`。

    为什么需要它（2026-10-05 拓展边界②的收尾）：全量重生成只认得"拍"，
    而 `define` / `default` / `init python` / `transform` / `screen` / `image`
    全在**顶层**，一行都留不下 —— 丢这些，游戏**起不来**。
    增量补丁那条路之所以安全，正是因为它"不动顶层"；一旦补丁失败退回
    全量，这件事就必须被**算出来**，不能靠人记得。

    规则：
      · 顶层 = 行首无缩进；
      · 空行与 `#` 注释**不算**（文件头注释丢了不难看，不值得拦人 ——
        "宁可少报"是这道门禁的既定口径）；
      · `label x:` 及其块体跳过（那属于"拍"，由增量/全量各自负责）。
    """
    lines = str(text or '').splitlines()
    out = []
    i, n = 0, len(lines)
    while i < n:
        cur = lines[i]
        s = cur.strip()
        if not s or s.startswith('#') or cur[:1] in (' ', '\t'):
            i += 1
            continue
        if re.match(r'^label\s', s):
            i += 1
            continue                       # label 块体交给"拍"那一层
        chunk = [cur]
        j = i + 1
        while j < n:
            nxt = lines[j]
            if not nxt.strip() or nxt[:1] in (' ', '\t'):
                chunk.append(nxt)
                j += 1
                continue
            break
        out.append((i + 1, chunk))
        i = j
    return out


def opaque_loss(source_text, product_text) -> list:
    """
    源文件里**有、产物里没有**的顶层语句：`[(行号, 语句头), …]`。

    这是"退回全量会不会毁东西"的**可计算判据**（拓展边界②的收尾）。
    以前的判据只有 `would_destroy_opaque(target, source_path)` —— 它看的是
    **目标**，回答"覆盖它会不会毁掉工具不认识的内容"。而 CLI 那条路
    （`--source X --out 新路径`）里目标是新的、不存在，护栏放行，于是
    **源文件里的 `define` / `init python` 在产物里悄悄没了**。

    实测（`game/pomodorro/pomodorro.rpy`，真实项目）：补丁没配上 → 退回全量
    → 产物里 `define t = Character("Roro", image="dororo")`、
    `image dororo neutral = "..."` 等**整批消失**，而 CLI 退出码是 0。
    这正是本仓库最贵的一类缺陷（契约 H1：静默改数据比报错坏得多）。

    比对口径：顶层块的**每一行**（去空白）都要能在产物的某一行里找到。
    宽松是刻意的 —— 产物可能重排、可能加缩进，只要东西还在就不算丢。
    """
    product = {_squash(ln) for ln in str(product_text or '').splitlines()}
    product.discard('')
    lost = []
    for line_no, chunk in top_level_chunks(source_text):
        sig = [_squash(ln) for ln in chunk
               if ln.strip() and not ln.lstrip().startswith('#')]
        if not sig:
            continue
        if any(item not in product for item in sig):
            lost.append((line_no, chunk[0].strip()[:60]))
    return lost


def _make_opaque_lost_issue(lost: list, source_path=None,
                            source_given: bool = False) -> GuardIssue:
    """
    把"顶层语句要丢"包成一条 block 级发现。

    `source_given`（2026-10-19，审计 P1-10）：用户**给过** `--source` 却说
    "加 --source" 是驴唇不对马嘴 —— 他要走的就是那条路，没走成的是
    **对位**。两种情形给两句不同的话：
      · 没给源 → 告诉他怎么走增量（这条路能保住东西）；
      · 给了源但对不上 → 告诉他先对齐（重新载入这一章 / 手工改过就改回去）。
    """
    head = '、'.join(f'第 {n} 行 `{t}`' for n, t in lost[:3])
    more = f'（共 {len(lost)} 处）' if len(lost) > 3 else ''
    where = f'{source_path} ' if source_path else ''
    if source_given:
        hint = ('你给的那份**底稿**（命令行 `--source` / 界面里选中的章节）'
                '没能和表对上，**不是"没给底稿"** —— 所以这些行没有原文可保。'
                '先重新载入这一章再导出；若这一章的结构被手工改过'
                '（增删了行、把菜单标题挪了行），先改回"一行一个结构"。')
    else:
        hint = ('要么指定这一章的 .rpy 走增量合并'
                '（命令行 `--source`；界面里先选中这个章节再导出），'
                '要么确认这些定义在别处也有、用 `--force` 再来一次。')
    return GuardIssue(
        'OPAQUE_LOST', 'block',
        f'{where}有 {len(lost)} 处顶层语句**没有出现在产物里**：{head}{more}。'
        f'全量重生成只认得"拍"，`define` / `default` / `init python` / '
        f'`transform` / `screen` 一行都留不下 —— 丢这些游戏**起不来**。'
        + hint)


# ---------------------------------------------------------------------------
#  结构位（嵌套）门禁 —— 见不变量 74
# ---------------------------------------------------------------------------

#: 会开一个缩进块的语句头（用来数"这个选项体里有几个块"）。
_BLOCK_HEAD_RE = re.compile(r'^(?:if|elif|else|menu|for|while|python|init'
                            r'|screen|style|translate|\$)\b|:$')


def _subtree_shape(lines) -> tuple:
    """
    一棵子树 → `(语句数, 块数)`。注释与空行不算语句。

    为什么是这两个数（而不是把整棵树序列化）：要判的是"**选项体里的内容
    有没有被搬走**" —— 搬走会让语句数变（`pass` 让 2 变 0），
    嵌套被压平会让块数变（`if` 里的两条被提出来 → 块数 1 变 0）。
    两个数一起看，够用且**不会因为文本改写而误报**（台词改了不影响它们）。
    """
    n_stmt = 0
    n_block = 0
    for ln in lines or ():
        text = (ln.text or '').strip()
        if not text or text.startswith('#'):
            continue
        n_stmt += 1
        if _BLOCK_HEAD_RE.search(text) and getattr(ln, 'subblock', None):
            n_block += 1
        sub = _subtree_shape(getattr(ln, 'subblock', None))
        n_stmt += sub[0]
        n_block += sub[1]
    return n_stmt, n_block


def _menu_signature(lines) -> list:
    """
    一段子树里的**所有菜单**（含嵌套），按文档顺序 →
    `[[(选项体语句数, 选项体块数), …], …]`。

    只认"菜单块里的选项"（头以引号开头），不碰 `screen` / `translate`
    里那些长得像块的语句 —— 那是第一版探针踩过的坑（把 374 个块当成选项）。
    """
    out: list = []
    for ln in lines or ():
        text = (ln.text or '').strip()
        if text.startswith('menu') and getattr(ln, 'subblock', None):
            bodies = []
            for opt in ln.subblock:
                head = (opt.text or '').strip()
                if not (head.startswith('"') or head.startswith("'")):
                    continue
                bodies.append(_subtree_shape(opt.subblock))
            out.append(bodies)
        out.extend(_menu_signature(getattr(ln, 'subblock', None)))
    return out


def menu_structure(text) -> dict:
    """
    `{label 名: 菜单结构指纹}` —— 每个 label 的每个菜单、每个选项体的形状。

    这是"**结构位有没有被压平**"的可计算判据（交付审查的 A 档止血，
    方案见 `docs/archive/嵌套与行尾注释_修复方案_2026-10-05.md`）。

    为什么只比菜单、不比全部嵌套：
      · **纯 `if` 嵌套**在全量路径上走"条件合取"近似（`if a:` 里套 `if b:`
        → `if (a) and (b):`）—— 那是**设计内**的，语义大体等价。
        真实项目里这种嵌套有 **109 处**，报它就是刷屏，等于没报；
      · 坏的只有**菜单选项体**：选项体里一旦有第二条语句或嵌套块，
        "这条拍属于哪个选项"就只能靠 `Condition` 猜 → 实测被压平成
        `"选项": pass` + 内容提到 label 级（**不管玩家选没选都会执行**）。
    """
    from storygraph.parsing.renpy_parser import RenPyLexer

    try:
        tree = RenPyLexer().tokenize_text(text)
    except Exception as e:                   # noqa: BLE001 - 解析不了就不判
        # 判不了就**不报**（宁可漏报也不许凭空报"结构坏了"）；
        # 但要有痕迹：解析器真出错时 `safeguard.report()` 里看得到。
        swallow('generation_guard.menu_structure', e)
        return {}
    out: dict = {}
    for ln in tree:
        text_ = (ln.text or '').strip()
        if text_.startswith('label ') and text_.endswith(':'):
            name = text_[len('label '):-1].strip().split()[0] if text_ else ''
            if name:
                out[name] = _menu_signature(ln.subblock)
    return out


def structure_loss(source_text, product_text) -> list:
    """
    源与产物的**菜单结构**对不上的 label：`[(label, 原样, 产物), …]`。

    ⚠ 这是 **warn 级**判据，**故意不 block**（2026-10-05 实测的取舍）：
    结构"变了"有两种可能 ——
      · **被压平**（缺陷）：产物里 `"选项": pass`、内容跑到菜单外面；
      · **人有意改的**（正常）：策划在表里给某个选项加了一句，
        或在卡片编辑器里往选项体里插了一张卡。
    只看"源 vs 产物"分不出这两者。而"假阳性比漏报更贵"（本项目既有立场）——
    拦错一次就会让人学会忽略这条警告。所以：**报出来、别拦**。
    等结构位进了表（方案 B），判据就能变成"产物 vs 表里说的结构"，
    那时才可以升成 block。
    """
    src = menu_structure(source_text)
    if not src:
        return []
    prod = menu_structure(product_text)
    lost = []
    for label, sig in src.items():
        if label not in prod:
            continue                          # label 缺失是另一条门禁的事
        if sig != prod[label]:
            lost.append((label, sig, prod[label]))
    return lost


def nest_conflict_warnings(beats, source_path=None) -> list:
    """
    结构列（`Nest`）vs 老推断不一致 → 一条 **warn** 级发现（B 阶段一）。

    与 A 档的 `STRUCTURE_LOST` 分工**不同、互补**：
      · `STRUCTURE_LOST` 比的是"源文本 vs 产物文本" —— 它管**结果**，
        但没有源可比时就不说话；
      · 这一条比的是"**结构列说这条拍在哪** vs **老推断把它摆在哪**" ——
        它管**过程**，逐拍、指名道姓，而且**表格往返之后照样有效**
        （`Nest` 随表走，`_menu_member` 走不了）。

    两者都是 warn：这一版**不改行为**（渲染仍按老推断），对拍只是把不一致
    说出来。等它对真实项目全绿（已实测 40 个文件 0 处），阶段二才让渲染
    改用结构列；那时这条警告才会变成"表坏了"的信号。
    """
    if not beats:
        return []
    from storygraph.story.beat_render import nest_placement_conflicts
    conflicts = nest_placement_conflicts(beats)
    if not conflicts:
        return []
    name, idx, claim, got = conflicts[0]
    more = f'（共 {len(conflicts)} 条拍）' if len(conflicts) > 1 else ''
    where = f'{source_path} ' if source_path else ''
    return [GuardIssue(
        'NEST_CONFLICT', 'warn',
        f'{where}结构列与老推断对不上{more}：`{name}` 第 {idx + 1} 条拍 —— '
        f'结构列说 {"顶层" if not claim else claim}，'
        f'老推断摆在 {"顶层" if not got else got}。'
        f'说明"这条拍属于哪个选项"两套判断不一致；产物很可能把选项体压平了'
        f'（`"选项": pass` + 内容提到菜单外）。'
        f'这一版渲染仍按老推断走（B 阶段一只双写不切换）——'
        f'先照这条线索核对那几拍，方案见 '
        f'docs/archive/嵌套与行尾注释_修复方案_2026-10-05.md')]


def _make_structure_issue(lost: list, source_path=None) -> GuardIssue:
    """把"菜单结构对不上"包成一条 **warn** 级发现（不拦写盘，见上）。"""
    label, want, got = lost[0]
    more = f'（共 {len(lost)} 个 label）' if len(lost) > 1 else ''
    where = f'{source_path} ' if source_path else ''
    return GuardIssue(
        'STRUCTURE_LOST', 'warn',
        f'{where}菜单结构对不上{more}：`{label}` 原文各选项体 '
        f'{[tuple(x) for x in want]} / 产物 {[tuple(x) for x in got]}。'
        f'全量重生成只认得"拍"，而"这条拍属于哪个选项"**没进表** ——'
        f'选项体里有第二条语句或嵌套块时会被压平（`"选项": pass`、'
        f'内容提到菜单外面 → 玩家选没选都会执行）。'
        f'如果这是你有意改的结构，忽略本条；如果不是，'
        f'加 `--source <这一章原来的 .rpy>` 走增量，别走全量。'
        f'（方案见 docs/archive/嵌套与行尾注释_修复方案_2026-10-05.md）')


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



#: 产物里 `python:` 块头的**合法形态**：`python:` / `init python:` /
#: `init 1 python:` / `python early:` / `init python in store:`。
#:
#: 为什么不再只认 exactly `python:`（2026-10-05 拓展边界）：
#:   · 顶层 `init python:` 现在会被保真带回产物（见 `ParseResult.file_blocks`），
#:     它同样是"会被 Ren'Py 当 Python 编译的一段"，漏检等于把坏代码放行；
#:   · 旧判据 `stripped == 'python:'` 对 `init python:` **完全不看**。
_PY_BLOCK_HEAD_RE = re.compile(
    r'^(?:init(?:\s+\d+)?\s+)?python(?:\s+early)?(?:\s+in\s+[\w.]+)?\s*:$')


def _iter_python_blocks(lines: list):
    """
    产出产物里每一个 `python:` 块的 `(块头行号, 块头文本, 块体行列表)`。

    块体 = 紧随其后、缩进**比块头更深**的所有行（含"后面还有块体行"的空行）。
    空行的判据与 `beat_render._render_marked_code_block` 同口径：空行后面
    还有块体行才算块内空行，否则它是块与下一段之间的分隔。
    """
    i, n = 0, len(lines)
    while i < n:
        head = lines[i].strip()
        if not _PY_BLOCK_HEAD_RE.match(head):
            i += 1
            continue
        base = len(lines[i]) - len(lines[i].lstrip())
        body = []
        j = i + 1
        while j < n:
            cur = lines[j]
            if not cur.strip():
                k = j
                while k < n and not lines[k].strip():
                    k += 1
                if k < n and (len(lines[k]) - len(lines[k].lstrip())) > base:
                    body.extend(lines[j:k])
                    j = k
                    continue
                break
            if (len(cur) - len(cur.lstrip())) <= base:
                break
            body.append(cur)
            j += 1
        yield i + 1, lines[i], body
        i = max(j, i + 1)


def _python_syntax_issues(text: str) -> list:
    """
    产物里的 `$ …` 语句与 `python:` 块体，各过一次 Python 编译器。

    为什么必须拦（P0，2026-10-05，真实项目 `script.rpy:93` 实测）：

        原文：  l oh "3"            ← Ren'Py 当台词读，**脚本能跑**
        产物：  $ l oh "3"          ← Python 语法错误，**Ren'Py 加载就报错**

    也就是说工具的产物比作者原文**更糟**。这类问题以前只有
    `tools/audit_roundtrip.py` 事后能看见，而**工具照样把文件写出去** ——
    和 P0 静默覆盖同一个性质：算出了"要出事"，却没拦住。

    ⚠ **粒度必须是"整块 / 整条"，不是"逐行"**（2026-10-05 拓展边界①，
    这次是**假阳性**把 `build` 卡死）：

    第一版对 `python:` 块体**一行一行**编译，于是三件完全正常的事全被判成
    "Python 语法错误"：

        positions = [                       ← 多行语句的续行：单看第一行是
            (200, 200), (600, 200),         ← `'[' was never closed`
        ]                                   ← 单看最后一行是 `unmatched ']'`
        for i, digit in enumerate(x):       ← 单看块头是
            …                               ← `expected an indented block`

    真实项目上实测：`build`（全项目合并）报出 **10 条 `PYTHON_SYNTAX_ERROR`**，
    逐条核对**全是假阳性** —— 把产物里 7 个 `python:` 块整块 `dedent` 后
    编译，**一个都不错**（复现入口：`tools/probe_python_block.py`，
    它现在把这四档分开报：在 / 行内注释 / 规范成 `$` / **真丢代码**）。
    后果是门禁把一份**完全合法**的产物拦在门外：`build` 在真实项目上
    "必然失败"，且失败理由指向不存在的问题。

    所以现在：

      · `python:` 块体 → **整块**（`dedent` 后）一次编译，块层级/续行都是
        Python 自己的语法，不再由我们逐行猜；
      · `$ …` 语句 → **整条**（用 `beat_render` 的括号平衡分组，续行算同一条）
        一次编译 —— 单行 `$ x = [` 同样会误报；
      · 真的坏（块里混进 `window hide` 这种 Ren'Py 语句）**照样报**，
        因为整块编译它就是个语法错误。

    判据尽量窄，避免误报：`#` 注释与空行不参与编译（除非它们把块体切开）。
    编译不过就是 block：**这一份产物写进 game/ 会让游戏起不来**。
    """
    out = []
    lines = text.splitlines()

    for line_no, header, body in _iter_python_blocks(lines):
        code = textwrap.dedent('\n'.join(body))
        if not code.strip():
            continue
        try:
            compile(code, '<generated>', 'exec')
        except SyntaxError as exc:
            at = line_no + max(1, int(exc.lineno or 1))
            bad = (body[int(exc.lineno or 1) - 1].strip()
                   if 0 < int(exc.lineno or 1) <= len(body) else '')
            out.append(GuardIssue(
                'PYTHON_SYNTAX_ERROR', 'block',
                f'`{header.strip()}` 块体里的 Python 语法错误：{exc.msg}'
                f'（第 {at} 行：`{bad}`）。这一块会让 Ren\'Py 加载脚本时'
                f'直接报错，游戏起不来。常见原因：块体里混进了一句 '
                f'Ren\'Py 语句（比如 `window hide` / `show screen x`），'
                f'或者括号/缩进真的坏了。',
                at))

    for line_no, code in _iter_dollar_statements(lines):
        if not code.strip() or code.lstrip().startswith('#'):
            continue
        try:
            compile(code, '<generated>', 'exec')
        except SyntaxError as exc:
            out.append(GuardIssue(
                'PYTHON_SYNTAX_ERROR', 'block',
                f'`$` 语句里的 Python 语法错误：{exc.msg}'
                f'（`{code.splitlines()[0].strip()}`）。这一行会让 Ren\'Py '
                f'加载脚本时直接报错，游戏起不来。常见原因：作者手写了一句 '
                f'Ren\'Py 语句（比如台词 `l oh "3"`）被当成了代码。',
                line_no))
    return out


def _iter_dollar_statements(lines: list):
    """
    产出产物里每一条 `$ …` 语句的 `(行号, 代码)`；**续行算同一条**。

    为什么不能逐行（见 `_python_syntax_issues`）：`$ x = [` 单看第一行是
    `'[' was never closed`。分组复用 `beat_render._group_code_statements`
    —— 那是"一条代码语句到哪里结束"的**唯一实现**（括号/引号平衡），
    在这儿再写一份就是下一个漂移点。
    """
    from storygraph.story.beat_render import _group_code_statements

    i, n = 0, len(lines)
    while i < n:
        head = lines[i].strip()
        if not head.startswith('$'):
            i += 1
            continue
        group = _group_code_statements(lines[i:])
        chunk = group[0][0] if group else lines[i]
        body = chunk.splitlines()
        head_line = body[0].strip()
        body[0] = head_line[2:] if head_line.startswith('$ ') else head_line[1:]
        yield i + 1, textwrap.dedent('\n'.join(body))
        i += max(1, len(body))


def _python_block_line_numbers(text: str) -> set:
    r"""
    产物里**哪些行在 `python:` / `init python:` 块体里**（1-based 行号）。

    为什么门禁需要它（2026-10-18，官方 tutorial 实测）：

        init python:
            ...
            hide = False        ← 一行普通的 python 赋值

    `_image_refs` 看到行首是 `hide ` 就当成了 `hide` 语句，图像名取到
    `= False` → 报 `CJK_IN_IMAGE_TAG`（block）→ **构建被自家门禁拦下**，
    而被拦的产物其实是合法的。门禁误报比漏报更贵：它会让人开始绕过门禁。

    判据只看缩进（与 Ren'Py 的块语法一致）：块头行 `… python:` 之后，
    缩进更深的行都算块体；回到同一层或更浅就出块。
    """
    inside = set()
    base = None                      # 当前 python 块的块头缩进
    for i, line in enumerate(str(text or '').splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if base is not None and indent <= base:
            base = None
        if base is not None:
            inside.add(i)
        if stripped.endswith('python:') and (
                stripped == 'python:' or stripped.startswith('init ')
                or stripped.startswith('python ')):
            base = indent if base is None else base
    return inside


def _image_refs(text: str) -> list:
    """抽出生成文本里所有的 show/hide/scene 图像名（跳过注释行与 python 块）。"""
    refs = []
    in_python = _python_block_line_numbers(text)
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith('#') or i in in_python:
            continue
        m = _STAGE_VERB_RE.match(line)
        if m:
            image = m.group(3)
            # `hide = False` 这种**python 赋值**不是舞台指令（见上）。
            if image.startswith('='):
                continue
            refs.append(_ImageRef(m.group(2), image, i))
    return refs



def generation_guard(text: str, out_path, *,
                     project_root=None,
                     occupied_labels: set = None,
                     beats: list = None,
                     unmanaged_blocks: list = None) -> list:
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
      · `UNMANAGED_BLOCK`（warn）—— 产物里有**工具不认识的块语句**
        （项目自定义的块）。它们的**内容已经逐字保真**（M1：见
        `Beat.opaque`），但工具**不管辖**它们：不校验、不渲染、不编辑。
        这条存在的意义就是"**不许静默**"—— 以前这类块会连头带块体
        从模型里消失，只留一条没人看的解析 warning，而门禁**无从得知**
        它存在过（内容没进任何通道）。现在它必须**说出口**。

    判断"有没有未建模块"要看 `beats`（产物文本里认不出来）：给了就报，
    没给就退回"不检查"——宁可少报，也不要凭空猜出一堆假警报。
    """
    issues = []
    out_path = Path(out_path) if out_path else None
    #: 哪些行在 `python:` 块体里（舞台/变换名扫描要跳过它们，见
    #: `_python_block_line_numbers` 的说明）。
    _PY_BLOCK_LINES = _python_block_line_numbers(text)

    # —— 0. 产物里的 Python 语法（P0，2026-10-05）——
    # 放在最前面：这是**唯一**一类"Ren'Py 连加载都加载不了"的错误，
    # 比图像名/标签重名更致命（那两类至少能进到解析阶段）。
    issues.extend(_python_syntax_issues(text))

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
        if not s or s.startswith('#') or i in _PY_BLOCK_LINES:
            continue
        parts = line.split()
        if 'at' not in parts:
            continue
        after_at = parts[parts.index('at') + 1:]
        # 变换列表到哪里为止 —— **唯一真源**在 `core.vocabulary`（2026-10-15 收重复）。
        # ⚠ 这一份**刻意不含 `at`**：它本来就用在 `at` 之后，`at` 不会再出现。
        stopwords = SHOW_CLAUSE_KEYWORDS - {'at'}
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

    # —— 3b. 工具不认识的块语句（M1，2026-10-07）——
    #
    # **这条是"不许静默"的落地**：内容已经被 `Beat.opaque` 逐字保住了，
    # 所以它不再是一条"丢内容"的告警，而是一条"这几行不归我管"的告知。
    # 它是 warn 而不是 block：工具不认识 != 脚本坏了 —— 拦下导出只会
    # 把用户赶回"工具没用"那一步（D1 的选择是"透传 + 报警 + 报告"）。
    # **三种"原样透传"必须分开说**，因为它们指向完全不同的动作：
    #   · declared  → 项目声明过（T1）：**工具知道你是谁**，按通用行处理，
    #                 这条不报警（它已经被管理了）；
    #   · custom    → 真没声明（T3）：催用户/程序写下来；
    #   · standard  → `while`/`for` 这类标准流程块（T2）：工具认识形状，
    #                 该催的是**我们**把它建模。
    # 混成一句"工具不认识"会把人引向错误的修法（真机实测逼出来的）。
    # `declared` 这一桶**刻意不报警**（T1 已经被管理了）；它在这里只是
    # 用来把"声明过的"从 custom 里摘出去，所以取出来不用。
    _declared, custom, standard = block_buckets(beats, unmanaged_blocks)
    if custom:
        shown = custom[:5]
        more = f'（另 {len(custom) - 5} 处）' if len(custom) > 5 else ''
        issues.append(GuardIssue(
            'UNMANAGED_BLOCK', 'warn',
            f'有 {len(custom)} 处**自定义块语句**，工具完全不懂它：'
            f'{"；".join(shown)}{more}。'
            f'内容已经原样保住、不会丢，但工具不会替你校验或重排它们 —— '
            f'改到附近时请对着原文核对。'
            f'想让它被管理：在项目里写 storygraph.rules.json，'
            f'或让程序用 renpy.register_statement 声明它的形状。'))
    if standard:
        shown = standard[:5]
        more = f'（另 {len(standard) - 5} 处）' if len(standard) > 5 else ''
        issues.append(GuardIssue(
            'KNOWN_UNMODELED_BLOCK', 'warn',
            f'有 {len(standard)} 处**标准流程块**（{ "；".join(shown) }{more}）'
            f'工具认识它的形状，但还没有把它结构化建模 —— '
            f'它们原样透传、内容不会丢。这一条是**给我们**的待办，'
            f'不是要你去改脚本。'))

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
    #: 这一次落盘前的**引擎 lint 结论**（`pipeline.engine_lint.LintReport`）。
    #: 没跑（`engine_lint=False` / 没有 SDK / 抛了）就是 None。
    #:
    #: 为什么要带出来（2026-10-19，审计 P2-B）：构建报告从前用
    #: "有没有 ENGINE_ 开头的 issue"反推"跑没跑"——lint **通过了**就没有
    #: issue，于是报告写 `ran=false`。闸门是开的，报告在"通过"这一侧说假话。
    engine_lint: object = None

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



def unmanaged_block_heads(beats, blocks) -> list:
    """
    「工具不认识的块语句」→ 头行清单（报告用，**每一项就是一处**）。

    两个入口，因为调用方的信息形态不同：

      · 有 `beats` 的（UI / CLI 生成）：从 `Beat.opaque` 取整块原文，
        每张拍算一处，取它的第一行当标题；
      · 只有文本的（`app.tables.apply_export` 拿不到 beat 对象，
        只有 `export_table` 传下来的一份结论）：直接用现成的清单。

    `blocks` 显式给了就**以它为准**（它是上游算好的、口径可追溯的那一份）。
    """
    if blocks is not None:
        return [str(h) for h in blocks if str(h or '').strip()]
    out = []
    for b in (beats or ()):
        text = str(getattr(b, 'opaque', '') or '')
        if not text.strip():
            continue
        first = next((ln.strip() for ln in text.split('\n') if ln.strip()), '')
        out.append(first or '(未命名的自定义块)')
    return out


def block_buckets(beats, blocks=None) -> tuple:
    """
    「原样透传的语句」分三桶：`(declared, custom, standard)`。

    · `declared` —— 项目声明过的（`Beat.spec_name` 非空）→ **T1，不报警**；
    · `standard` —— `while` / `for` 这类 Ren'Py 标准流程块（T2）；
    · `custom`   —— 剩下的，工具真不懂（T3）。

    只有 `beats` 才能给出"声明过没有"（那是逐拍的事实）；调用方只交了
    一份头清单（`app.tables.apply_export` 那条路）时，`declared` 只能是空
    —— 那时宁可把它算成 custom（**少报"已管理"，不假装已管理**）。
    """
    from storygraph.core.vocabulary import is_known_flow_block
    if beats is None and blocks is None:
        return [], [], []
    if beats is None:
        heads = [str(h) for h in blocks if str(h or '').strip()]
        return [], [h for h in heads if not is_known_flow_block(h)], \
            [h for h in heads if is_known_flow_block(h)]
    declared, custom, standard = [], [], []
    for b in beats:
        text = str(getattr(b, 'opaque', '') or '')
        if not text.strip():
            continue
        # 循环块（D2）与相机块（3D 舞台）已经**内置建模**（T0b：头行结构化、
        # 块体逐字）—— 它们不再是"工具不管辖"，所以不进任何一桶。
        # ⚠ 这个判据必须**跟着新建模的块类型一起加**：漏一处，报告就会一直
        # 催我们去做一件已经做完的事（`while` 那次就是靠这条才闭嘴的）。
        if (str(getattr(b, 'loop_header', '') or '')
                or str(getattr(b, 'camera_header', '') or '')):
            continue
        head = next((ln.strip() for ln in text.split('\n') if ln.strip()), '')
        name = str(getattr(b, 'spec_name', '') or '')
        if name:
            if name not in declared:
                declared.append(name)
        elif is_known_flow_block(head):
            standard.append(head or '(未命名)')
        else:
            custom.append(head or '(未命名的自定义块)')
    return declared, custom, standard


def evaluate_generation(text: str, out_path, *, project_root=None,
                        occupied: set = None, beats: list = None,
                        unmanaged_blocks: list = None,
                        source_text: str = None,
                        source_path=None) -> list:
    """跑一遍生成门禁，返回问题清单。**纯函数、不写盘。**

    给"先看结果、问过用户再写"的调用方用（UI 的确认框就该是这个顺序）。

    `beats` 给了才能报 `UNMANAGED_BLOCK`（工具不认识的块）——
    那条判据只看产物文本认不出来，见 `generation_guard` 的说明。

    `source_text`（2026-10-19，审计 P1-7）：这一章的**原文**。给了就能
    报 `OPAQUE_LOST`（顶层语句要丢）—— "写回原文件"那条路必须有这一条，
    否则补丁没配上时，`default` / `init python` 会静默消失（从前是
    写 build/ 才显得安全；现在默认写回原章节，判据必须跟上）。
    """
    issues = generation_guard(text, Path(out_path), project_root=project_root,
                              occupied_labels=occupied, beats=beats,
                              unmanaged_blocks=unmanaged_blocks)
    if source_text is not None:
        lost = opaque_loss(source_text, text)
        if lost:
            issues.append(_make_opaque_lost_issue(
                lost, source_path, source_given=bool(source_path)))
    return issues



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
                     project_root=None, occupied=None, force: bool = False,
                     merged_from_source=None, source_text=None,
                     source_path=None, beats=None,
                     unmanaged_blocks=None,
                     engine_lint: bool = False,
                     lint_ignore_heads=()):
    """
    落盘，并保证"绝不把会让游戏跑不起来的脚本写进目标路径"。

      · 无 block 级问题 → 写 `out_path`，`written=True`；
      · 有 block 级问题 → **不写目标文件**，改写 `<stem>.blocked.rpy`
        供人对比，`written=False`、`review_path` 指向副本。
      · **目标已存在且不是工具生成的** → 同样不写（见下，P0）。

    为什么拦下也要留副本：只报错不给人看产物，等于让人猜哪里错了；
    但绝不能写进目标路径（那正是事故的形态）。

    `project_root` / `occupied` 决定 **LABEL_COLLISION 这条门禁判不判**：
      · 给了 project_root（或能从产物路径推出含 game/ 的项目根）→ 扫项目里
        其他 .rpy 已占用的 label，重名即 block；
      · 都没有 → 退回"不检查"（临时目录、单测场景），宁可少报。
    2026-09-28 修：以前这里写死 `project_root=None`，**等于把这条门禁关了**。

    **"目标已存在"护栏**（P0，2026-10-05，见
    `docs/archive/P0_静默全量覆盖_2026-10-03.md`）：

      目标已存在 **且** 首行不含工具生成标记 → 判为**手写文件**，拒绝覆盖。
      理由是"全量重生成只认得拍"：`default` / `define` / `init python` /
      `transform` / `screen` 一行都留不下，覆盖手写文件等于**静默毁掉定义**。

      · 首行带 `GENERATOR_MARKER`（工具自己以前写的）→ 允许覆盖，
        "再导出一次"是正常操作，不能拦；
      · 目标不存在 → 正常写（**新建文件是全量的正当用途**）；
      · `force=True` → 明确知道自己在做什么，放行（逃生舱 / CLI `--force`）。

    `merged_from_source`（2026-10-05 拓展边界②）：**这份文本是在哪个 .rpy 上
    增量合并出来的**（底稿路径；没走增量就给 `None`）。底稿**就是**
    `out_path` 本身时，那条"目标已存在且无标记 → 拒写"的护栏必须**让路** ——
    否则"改一句台词、原地写回自己这一章"永远会被自家护栏拦下，而这恰恰是
    **唯一安全**的改法：

        全量重生成 → 丢掉 default / init python（危险，必须拦）
        增量合并   → 原文逐字节保留，只换改过的那几行（安全，必须放行）

    ⚠ **判据是"底稿 == 目标"，不是"这次走了增量"**（2026-10-05 实测修）：
    编辑 ch01、把产物写到别人的手写 ch02 上时，产物里没有 ch02 的任何内容
    —— 那种情况照旧拦（见 `merging_onto`）。所以这里收的是**路径**而不是
    一个布尔：让调用方声明"我走了增量"就等于给它一把万能钥匙，而
    `EditorSession.export` / `cli table xlsx2rpy` 两条路都会经过这里。

    越过的**只有**这一条：`PYTHON_SYNTAX_ERROR` / `CJK_IN_IMAGE_TAG` /
    `LABEL_COLLISION` 等"产物自己坏了"的门禁**一条都不越**。

    `source_text`（2026-10-05，拓展边界②的收尾）：**这一章原文的全文**。
    给了它，门禁就能**算出来**产物是不是丢了顶层语句（`opaque_loss`），
    丢了就报 `OPAQUE_LOST` 并拒写。为什么必须有这一条：

        `--source X --out 新路径` 时目标是**新的**，上面那条手写护栏
        根本不适用 → 补丁失败退回全量 → 源里的 `define` / `init python`
        在产物里没了，而退出码是 0（真实项目 `pomodorro.rpy` 实测）。

    `force=True` 时 `OPAQUE_LOST` **降级为 warn**（不再是 block）：
    它与 `HANDWRITTEN_TARGET` 同类 —— 说的不是"产物自己坏了"，
    而是"这个目标配不上这份产物"，该由人拍板。其余 block 依旧不越。

    调用方要"先问后写"就直接用 `evaluate_generation` + 自己写，
    这个函数是给"已经确认过，但还要兜一层底"的路径用的。
    """
    out_path = Path(out_path)
    root = project_root or _guess_project_root(out_path)
    merged_onto_target = merging_onto(out_path, merged_from_source)
    if not force and not merged_onto_target and _looks_handwritten(out_path):
        reason = ('目标已存在且不是工具生成的（没有生成标记），'
                  '拒绝覆盖手写 .rpy')
        blocking = [_make_blocking_issue(out_path, reason)]
        review = None
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if review_on_block:
                review = out_path.with_suffix('.blocked.rpy')
                write_text_atomic(review, text)
        except OSError:
            review = None
        _publish_blocked(out_path, blocking, review)
        return GeneratedArtifact(out_path, review, False, blocking)

    if occupied is None:
        # 只有产物**会被 Ren'Py 扫描**（落在 <root>/game/ 里）时，
        # "和原文抢 label"才是致命问题；写到 build/ 或临时目录的产物
        # 根本不被扫描，与原文同名无害（见 labels_in_danger）。
        occupied = labels_in_danger(out_path, root, exclude={out_path})
    issues = generation_guard(text, out_path, project_root=root,
                              occupied_labels=occupied, beats=beats,
                              unmanaged_blocks=unmanaged_blocks)
    if source_text is not None:
        lost = opaque_loss(source_text, text)
        if lost:
            issue = _make_opaque_lost_issue(lost, source_path,
                                            source_given=bool(source_path))
            if force:
                # 逃生舱：人明确知道自己在做什么 —— 降级成 warn，
                # 但**仍然报出来**（报告里看得见丢了几处）。
                issue = GuardIssue(issue.code, 'warn', issue.message,
                                   issue.line_no)
            issues.append(issue)
        # 结构位（菜单选项体）对不上 —— **只 warn，不拦**（见
        # `structure_loss` 的 docstring：分不出"被压平"还是"人有意改的"）。
        structure = structure_loss(source_text, text)
        if structure:
            issues.append(_make_structure_issue(structure, source_path))
    # **引擎闸门**（2026-10-18，审计 P1-9 / 修复顺序第一条）：把产物真的丢给
    # Ren'Py 的 `lint` 读一遍。工具自己的三层校验验的是"内部前后一致"，
    # 而"引擎能不能解析这个产物"从来没进过判据 —— P0-1~P0-6 全是
    # "引擎会报、工具不会报"的错，于是"全绿"与"跑不起来"同时成立。
    # 只把**语法**错误当阻塞；语义错误（缺图缺角色）要项目上下文，只 warn。
    _lint = None
    if engine_lint:
        try:
            from storygraph.pipeline.engine_lint import lint_text
            _rep = lint_text(text, ignore_heads=lint_ignore_heads)
            _lint = _rep
            if _rep.ran and not _rep.ok:
                issues.append(GuardIssue(
                    'ENGINE_PARSE_ERROR', 'block',
                    '引擎读不了这份产物（SDK lint）：'
                    + '；'.join(_rep.parse_errors[:3])
                    + (f'（共 {len(_rep.parse_errors)} 处）'
                       if len(_rep.parse_errors) > 3 else '')))
            elif _rep.ran and _rep.semantic_errors:
                issues.append(GuardIssue(
                    'ENGINE_LINT_WARN', 'warn',
                    '引擎 lint 有语义提示（不影响解析）：'
                    + '；'.join(_rep.semantic_errors[:3])))
        except Exception as e:                  # noqa: BLE001 - lint 是增强，不拖垮导出
            swallow('generation_guard.engine_lint', e)
    blocking = [i for i in issues if i.is_blocking]
    # **载体风格**（2026-10-18，审计 P0-1）：源文件带 BOM / CRLF 时，产物必须
    # 按同一副样子写回去（否则"打开再导出"每个字节都变：审计实测官方
    # `tutorial` 23 个脚本全是 CRLF + BOM）。只在**落盘这一层**做一次 ——
    # 门禁看到的仍是干净的 LF 文本（它要按行解析），而
    # `write_text_atomic` 用 `newline=''` 写，不会再翻译任何东西。
    write_text_value = text
    _style_source = source_path or merged_from_source
    if _style_source:
        from storygraph.story.source_style import detect as _detect_style
        write_text_value = _detect_style(_style_source).apply(text)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if blocking and review_on_block:
            review = out_path.with_suffix('.blocked.rpy')
            write_text_atomic(review, write_text_value)
            _publish_blocked(out_path, blocking, review)
            return GeneratedArtifact(out_path, review, False, issues, _lint)
        if blocking:
            _publish_blocked(out_path, blocking, None)
            return GeneratedArtifact(out_path, None, False, issues, _lint)
        # **原子写 + 保留换行 + 写前备份**（P1-2）：目标就是游戏脚本，
        # `write_text` 的"先截断再写"在崩溃/占用时会留下半截文件，
        # 而 Windows 上它还会把 LF 全翻成 CRLF（违背"逐字节保真"）。
        write_text_atomic(out_path, write_text_value, backup=True)
        return GeneratedArtifact(out_path, None, True, issues, _lint)
    except OSError:
        # 写不出去（权限/占用）不该把门禁结论一起吞掉
        return GeneratedArtifact(out_path, None, False, issues, _lint)



def _publish_blocked(out_path, blocking, review) -> None:
    """
    **把"产物没写出去"这件事说出去**（P2-9）。

    为什么必须在这里发：门禁拦下时目标文件**没有**被写出去，而调用方
    （`build` / UI 导出）拿到的只是一个 `written=False` —— 界面上很可能
    只显示"导出完成"，用户以为产物在，实际 `game/` 里什么都没有。
    CI 靠退出码能发现，人在界面上发现不了。

    无头环境（CLI / 单测）默认没有订阅者，发通知是**零副作用**的
    （见 `core.notify`：订阅者列表默认空），所以只会在真有人看时出现。
    """
    from storygraph.core import notify as _notify
    try:
        first = blocking[0].format() if blocking else ''
        extra = f'（另有 {len(blocking) - 1} 条）' if len(blocking) > 1 else ''
        tail = f'已写出待对照的副本：{review.name}' if review else '产物未写出。'
        _notify.error(
            f'生成被拦下，没有写入 {Path(out_path).name}：{first}{extra}。{tail}',
            title='生成被拦下')
    except Exception as e:                  # noqa: BLE001 - 通知失败绝不拖垮导出
        swallow('generation_guard._publish_blocked', e)



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
                              occupied_labels=occupied, beats=beats)
    blocking = [i for i in issues if i.is_blocking]
    if blocking:
        raise GenerationBlocked(output_path, blocking)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(output_path, text, backup=True)
    return str(output_path)
