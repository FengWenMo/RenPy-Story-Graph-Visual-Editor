"""
storygraph.authoring.import_cleanup
===================================
**导入残留清理**：把"纯文字导入"落盘时留下的副本扫出来，安全地删掉。

用户 2026-10-26 的原话：

> 我一个个删 vscode 里面的 .bak 和 blocked 里面的文件老狼狈了……

## 这些东西是哪来的（落盘侧两处留下的）

  · `story.paths.write_text_atomic(..., backup=True)` 每次覆盖一个已存在的
    文件前，会先把它备份成 `<名字>.rpy.bak`；第二次再写时 `.bak` 已经在了，
    于是新备份改名叫 `<名字>.rpy.bak-<时间戳>`，并且时间戳备份只保留最近
    5 份（见 `write_text_atomic` 里那段说明）。`<名字>.rpy.bak` 本身每轮
    都会留着、不会自动清 —— 它也是用户最想清掉的那一类。
  · `story.generation_guard.write_generation` 被生成门禁拦下时，会把"供人
    对照"的产物写到 `<项目根>/build/review/<名字>.blocked.rpy`
    （见 `review_path_for`：绝不能落进 game/，那是 Ren'Py 的扫描范围）。
  · 另外还有更早的写法留下的 `<名字>.bak.rpy`（历史遗留，现在已不再产生）。

它们**都不参与游戏运行**（`.blocked.rpy` 在 `build/` 里，Ren'Py 不扫描；
`.rpy.bak` / `.bak.rpy` 也不会被当成 `.rpy` 脚本加载），但会一直堆在项目里。
用户只能在 vscode 里一个个找、一个个删。本模块就是那条"扫描 + 安全删除"
的纯逻辑：界面只负责把 `scan_import_junk` 的结果摆出来、把 `summarize`
的计数显示成一行，用户确认后调 `delete_import_junk`。

## 安全红线（比"删得掉"重要得多）

1. **只认四类命名**（`KIND_*` 常量）。特别地，`characters.xlsx.bak`
   （资源表备份）**既不许删、也不许列出来**：列出来用户会顺手全选删掉，
   而那是他**唯一**的表备份 —— 删了没处恢复。所以扫描按"文件名的完整后缀"
   判，不做 `*.bak` 这种宽匹配（`其他.txt.bak` / `random.bak` 也会被宽匹配
   卷进来）。
2. **只在 `<项目根>/game/` 与 `<项目根>/build/review/` 两个目录里工作**。
   别处哪怕有同名文件也一律不列：项目外另一个 `game/` 不是这个项目的。
3. `scan` 对"项目不存在 / 没有 game 目录 / 参数不是路径"一律返回 `[]`，
   **不抛异常** —— 界面会在打开项目时顺手调一次，没有项目时不该弹错误框。
4. `delete` **不信任调用方**：对每条路径再查一遍"命名仍属于四类、仍在允许的
   目录里、不是符号链接/junction"，不匹配就进 `failed` 并给中文原因，
   **绝不删**。理由很直接：失败是可见的，误删是**不可恢复**的。
5. 单个删除失败（被占用 / 权限 / 中途被别人删掉）不影响其它文件，进
   `failed`；用 `safeguard.swallow` 留痕，不写 `except: pass`。

## 不做的事

不 import Qt、不 import app 层：界面另一个人做，这里只放能被无头测试钉住的
纯函数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from storygraph.core.safeguard import swallow

# ===================== 四类残留（判据的唯一来源） =====================

#: `write_text_atomic(..., backup=True)` 的**第一份**备份：`<名字>.rpy.bak`。
#: ⚠ 它可能是覆盖手写文件前留下的**唯一**旧版本（见 `write_text_atomic` 里
#: "第一份备份最值钱"那段），所以界面必须**逐条让用户勾选**再删；本模块的
#: `scan` 只负责如实把它列出来，`delete` 只删用户点名的那几条。
KIND_BAK = 'bak'
#: 同一个目标第二次及以后被覆盖时的备份：`<名字>.rpy.bak-<时间戳>`
#: （`write_text_atomic` 只保留最近 5 份，更早的本来就是可丢的历史）。
KIND_BAK_STAMPED = 'bak_stamped'
#: 生成门禁拦下时留在 `build/review/` 的对照副本：`<名字>.blocked.rpy`。
KIND_BLOCKED = 'blocked'
#: 历史遗留的旧备份写法：`<名字>.bak.rpy`（现在已不再产生，清掉即可）。
KIND_BAK_RPY = 'bak_rpy'

#: 四类 kind 的固定顺序（界面按这个顺序渲染，`summarize` 的键也按它）：
#: 从"最像普通备份"到"最像历史垃圾"。
ALL_KINDS = (KIND_BAK, KIND_BAK_STAMPED, KIND_BLOCKED, KIND_BAK_RPY)

#: 给界面直接显示的中文说法（界面不要自己再拼一套，否则两处会漂移）。
KIND_LABELS = {
    KIND_BAK: '自动备份（.rpy.bak）',
    KIND_BAK_STAMPED: '时间戳备份（.rpy.bak-<时间戳>）',
    KIND_BLOCKED: '门禁对照副本（.blocked.rpy）',
    KIND_BAK_RPY: '历史遗留备份（.bak.rpy）',
}

#: 时间戳备份的**完整**形状：`chapter1.rpy.bak-20261026-210509-123456`。
#: 只认"短横线 + 数字"，且至少一位数字 —— `x.rpy.bak-老版本` 不认：
#: 那是人手工起的名字，可能是他要留的东西。
_STAMPED_RE = re.compile(r'\.rpy\.bak-[0-9][0-9-]*$')

#: 只有 `blocked` 也允许出现在 `build/review/` 下；另外三类只可能在 game/。
_GAME_ONLY_KINDS = frozenset((KIND_BAK, KIND_BAK_STAMPED, KIND_BAK_RPY))
_ALL_KINDS = frozenset(ALL_KINDS)


@dataclass(frozen=True)
class JunkFile:
    """
    一条**可删的**导入残留（界面列表里的一行）。

    只带界面需要的四个字段，刻意**不带项目根**：
      · `path`  完整路径（界面的"在 vscode 里打开"要用）；
      · `kind`  四类之一（`KIND_*`；界面用 `KIND_LABELS` 显示）；
      · `size`  字节数（配合 `human_size` 显示）；
      · `mtime` 修改时间（Unix 时间戳；按时间排序/显示"多久以前的"用）。

    为什么 `frozen`：扫描结果会跨线程交给界面，冻结值对象不会被谁顺手改掉。
    """

    path: Path
    kind: str
    size: int
    mtime: float


def _path_key(item) -> str:
    """
    排序键：完整路径字符串（确定性；`scan` / `summarize` / `delete` 共用）。

    `item` 可以是 `JunkFile`，也可以是裸路径（`delete` 接受调用方手输的路径）。
    """
    return str(getattr(item, 'path', item))


# ===================== 命名判据 =====================

def _kind_of_name(name) -> str:
    """
    这个**文件名**属于四类残留里的哪一类？不是 → 返回 `''`。

    判据刻意按"完整后缀"写（`.rpy.bak` / `.rpy.bak-<数字>` /
    `.blocked.rpy` / `.bak.rpy`），**不做 `*.bak` 宽匹配**：

      · `characters.xlsx.bak`（资源表备份）是用户唯一的表备份，宽匹配会把它
        卷进"垃圾"列表 —— 用户顺手全选就把它删了，那是**丢数据**；
      · `其他.txt.bak` / `random.bak` 同理，不是本工具产的，不归这里管。

    大小写不敏感：Windows 文件系统本来就不区分，`X.RPY.BAK` 也还是备份。
    """
    text = str(name or '').lower()
    if text.endswith('.blocked.rpy'):
        return KIND_BLOCKED
    if _STAMPED_RE.search(text):
        return KIND_BAK_STAMPED
    if text.endswith('.rpy.bak'):
        return KIND_BAK
    if text.endswith('.bak.rpy'):
        return KIND_BAK_RPY
    return ''


def _dir_roles(path: Path) -> set:
    """
    这条路径落在哪个"允许目录"里：`{'game'}` / `{'review'}` / 空集。

    `JunkFile` 里**没有项目根**（四个字段是给界面的最小集合），所以
    `delete` 只能按**路径结构**复核：某个祖先目录叫 `game`，或者祖先里
    出现 `build/review` 这一对。这不是"信任调用方"—— 在只知道路径的前提下
    这已经是最强判据；扫描本身已经把范围钉死在那两个目录里。

    为什么用"祖先里有 game"而不是"项目根/game"：`delete` 拿不到项目根，
    而 `<任意路径>/game/...` 正是扫描能产出的唯一形态。
    """
    roles = set()
    for parent in Path(path).parents:
        name = parent.name.lower()
        if name == 'game':
            roles.add('game')
        elif name == 'review' and parent.parent.name.lower() == 'build':
            roles.add('review')
    return roles


def _allowed_here(path: Path, kind: str) -> bool:
    """
    `kind` 这类残留允许出现在 `path` 所在的目录吗？

    `blocked` 允许在 game/（历史遗留）或 build/review/（门禁现在写这里）；
    另外三类只允许在 game/ 下 —— 与 `scan_import_junk` 的扫描范围同源，
    否则会出现"扫不出来、删的时候却放行"的口径漂移。
    """
    roles = _dir_roles(path)
    if 'game' in roles:
        return True
    return kind not in _GAME_ONLY_KINDS and 'review' in roles


def _is_link(path: Path) -> bool:
    """
    是符号链接 / junction 吗？（两种都**不删**。）

    为什么要单独判：符号链接的 `is_file()` 会**跟着目标**返回 True
    （看着像普通文件），`unlink` 删掉的却是链接本身；junction 更糟 ——
    它会被路径 API 当成目录，删除行为随平台而变。删文件是不可恢复的，
    所以宁可跳过并让 `failed` 说清楚。

    读不出来（权限/路径坏了）也按"链接"处理 → 不删：判不了的事不做。
    """
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, 'is_junction', None)
        if is_junction is not None and is_junction():
            return True
    except OSError as e:                       # noqa: BLE001 - 判不了就不删
        swallow('import_cleanup.is_link', e)
        return True
    return False


# ===================== 扫描 =====================

def _collect_under(base: Path, kinds, out: list) -> None:
    """
    递归 `base`，把"文件名属于 `kinds` 且是普通文件"的条目收进 `out`。

    单条出错（权限、扫描过程中被删）只留痕并跳过，不影响整次扫描 ——
    扫描是界面打开项目时顺手刷新的一下，不该因为一个坏文件整个列表空掉。
    """
    try:
        for path in base.rglob('*'):
            try:
                if _is_link(path):
                    # 符号链接只可能**指向**残留，删链接不是本模块的事
                    # （而且会让人以为"清理了"，其实目标还在）。
                    continue
                kind = _kind_of_name(path.name)
                if not kind or kind not in kinds:
                    continue
                if not path.is_file():
                    continue
                st = path.stat()
            except OSError as e:               # noqa: BLE001 - 单条坏了不拖垮扫描
                swallow('import_cleanup.scan.entry', e)
                continue
            out.append(JunkFile(path=path, kind=kind,
                                size=int(st.st_size),
                                mtime=float(st.st_mtime)))
    except OSError as e:                       # noqa: BLE001 - 整个目录读不进去
        swallow('import_cleanup.scan.walk', e)


def scan_import_junk(project_root) -> list:
    """
    扫出项目里**可以删的**导入残留（按路径排序；`[]` = 没有 / 项目不成立）。

    扫描范围**只有两处**（多一个目录都不行）：
      · `<项目根>/game/` 递归找四类（作者看不见的地方就没人会去删，
        所以必须递归到 `game/chapter/` 这种子目录）；
      · `<项目根>/build/review/` 递归只找 `.blocked.rpy`（另外三类不会
        出现在那儿，也不该由这里管）。

    没有 `game/` 直接返回 `[]`：没有 game/ 的目录**不是这个工具认的项目**
    （`txt_pipeline.plan_target` / `inside_game_dir` 也这么判）；而
    `build/review/` 单独存在时无从确认它属于哪个项目 —— 宁可少报，
    也不能把隔壁项目的残留列进来（用户会全选删掉的）。

    不抛异常：项目不存在、`project_root` 是 `None`/空串/不是路径，
    一律 `[]`。
    """
    if not project_root:
        return []
    try:
        root = Path(project_root)
    except (TypeError, ValueError) as e:       # noqa: BLE001 - 参数不是路径
        swallow('import_cleanup.scan.root', e)
        return []
    try:
        if not root.is_dir():
            return []
        game = root / 'game'
        if not game.is_dir():
            return []
    except OSError as e:                       # noqa: BLE001 - 盘符/权限问题
        swallow('import_cleanup.scan.root', e)
        return []

    found: list = []
    _collect_under(game, _ALL_KINDS, found)
    # `build/review/` 只认"不属于 game 专属"的那一类（现在就是 blocked）：
    # 用集合差而不是写死 `{KIND_BLOCKED}`，将来加新 kind 时两处不会漂移。
    _collect_under(root / 'build' / 'review',
                   _ALL_KINDS - _GAME_ONLY_KINDS, found)
    found.sort(key=_path_key)
    return found


# ===================== 汇总 / 显示 =====================

#: `human_size` 的单位（B 单独处理，因为整数不带小数）。
_UNITS = ('KB', 'MB', 'GB', 'TB')


def human_size(n) -> str:
    """
    字节数 → 给人看的字符串：`0 B` / `1023 B` / `12.3 KB` / `1.2 MB`。

    为什么界面不要自己除 1024：删之前用户要看"这一堆占多大"，
    `12595` 这种数得自己换算；而且"1024 的几次方换单位"和
    "1023.99 KB 该显示成 1.0 MB 还是 1024.0 KB"这种边角只该有一处实现。
    非数字 / 负数按 0 处理（不知道多大就显示 0 B，不影响删除判据）。
    """
    try:
        size = int(n)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return '0 B'
    if size < 1024:
        return f'{size} B'
    value = float(size)
    for unit in _UNITS:
        value /= 1024.0
        text = f'{value:.1f}'
        if unit == _UNITS[-1] or value < 1024.0:
            # 1023.99 KB 四舍五入会变成 "1024.0 KB" —— 往上进一个单位显示成
            # "1.0 MB"，别让界面出现"1024.0 KB"这种读起来别扭的数。
            if text == '1024.0' and unit != _UNITS[-1]:
                continue
            return f'{text} {unit}'
    return f'{value:.1f} {_UNITS[-1]}'


def summarize(files) -> dict:
    """
    一次扫描结果 → 界面直接能用的汇总。

    返回（字段名是给界面的契约，别随手改）：
      · `counts`      `{kind: 个数}`（**四类键都在**，没有的计 0）；
      · `by_kind`     `{kind: [JunkFile, …]}`（同一套键；列表按路径排序）；
      · `total`       总数；
      · `bytes`       总字节数；
      · `human_bytes` `human_size(bytes)`（界面直接显示，不要自己除 1024）。

    为什么"四类键都在"：界面可以按固定顺序渲染
    "自动备份 3 · 时间戳备份 0 · 对照副本 2 · 历史遗留 0"，
    偶尔缺一行比显示 0 更容易被当成"没扫到"而漏删。
    """
    counts = {kind: 0 for kind in ALL_KINDS}
    by_kind = {kind: [] for kind in ALL_KINDS}
    total = 0
    total_bytes = 0
    for item in (files or ()):
        kind = str(getattr(item, 'kind', '') or '')
        if kind not in counts:
            # 不是四类之一（正常输入不会有）→ 不进汇总：既不计也绝不显示，
            # 免得界面把不认识的东西也摆成"可删"。
            continue
        counts[kind] += 1
        by_kind[kind].append(item)
        total += 1
        try:
            total_bytes += max(0, int(getattr(item, 'size', 0) or 0))
        except (TypeError, ValueError) as e:   # noqa: BLE001 - 大小坏了不算
            swallow('import_cleanup.summarize.size', e)
    for items in by_kind.values():
        items.sort(key=_path_key)
    return {
        'counts': counts,
        'by_kind': by_kind,
        'total': total,
        'bytes': total_bytes,
        'human_bytes': human_size(total_bytes),
    }


# ===================== 安全删除 =====================

def _refusal(path: Path) -> str:
    """
    这条路径**为什么不能删**？返回中文原因；`''` = 可以删。

    判据顺序有意如此：先看链接（`is_file()` 会跟着链接骗人），再核命名，
    再核目录，最后才看它是不是普通文件。任何一步读不出来都按"不删"处理。
    """
    try:
        if _is_link(path):
            return ('是符号链接/junction（不删：删掉的只会是链接本身，'
                    '目标不在本模块的管辖范围）')
        kind = _kind_of_name(path.name)
        if not kind:
            return ('文件名不是四类导入残留之一（'
                    + '、'.join(ALL_KINDS) + '），不删')
        if not _allowed_here(path, kind):
            return '不在 <项目根>/game 或 <项目根>/build/review 里，不删'
        if not path.is_file():
            return '不是普通文件（可能是目录，或已经被删掉了），不删'
    except OSError as e:                       # noqa: BLE001 - 读不到就不删
        swallow('import_cleanup.delete.check', e)
        return f'检查时读不到（{e}），不删'
    return ''


def delete_import_junk(files) -> tuple:
    """
    删掉 `files` 里**确实还是**导入残留的那些文件。

    返回 `(deleted, failed)`：
      · `deleted`：真的删掉了的路径 `[Path, …]`；
      · `failed`：`[(路径, 中文原因), …]`（没删的，**一条都不瞒**）。
    两边都按路径排序（确定性，便于界面显示与测试）。

    为什么每条都要**再查一次**（而不是信任 `scan_import_junk` 的结果）：
    界面从"扫描"到"用户点确认删除"之间，列表可能已经过期（文件被删/改名），
    调用方也可能把自己的路径塞进来。删文件不可恢复，所以这里不信任任何输入：
    命名不属于四类、不在允许的目录、是链接/目录、检查时出错，一律**不删**
    并进 `failed`。

    `unlink(missing_ok=False)` 也是刻意这么传的：文件在扫描之后消失时要
    **报出来**（进 `failed`），而不是假装删成功 —— 静默成功会让界面显示
    "清理了 3 个"，实际有一个根本没删过。

    单个失败不影响其它（被占用/权限在 Windows 上太常见）；失败原因带原始
    异常文本，界面直接显示即可。也接受裸路径（手工构造或 CLI 传参），
    但安全检查一条不少。
    """
    deleted: list = []
    failed: list = []
    items = []
    for raw in (files or ()):
        try:
            path = Path(getattr(raw, 'path', raw))
        except (TypeError, ValueError) as e:   # noqa: BLE001 - 不是路径就没法删
            swallow('import_cleanup.delete.path', e)
            continue
        items.append(path)
    items.sort(key=_path_key)
    for path in items:
        reason = _refusal(path)
        if reason:
            failed.append((path, reason))
            continue
        try:
            path.unlink(missing_ok=False)
        except OSError as e:                   # noqa: BLE001 - 被占用/权限/已删
            swallow('import_cleanup.delete.unlink', e)
            failed.append((path, f'删除失败：{e}'))
            continue
        deleted.append(path)
    return deleted, failed


__all__ = ['ALL_KINDS', 'JunkFile', 'KIND_BAK', 'KIND_BAK_RPY',
           'KIND_BAK_STAMPED', 'KIND_BLOCKED', 'KIND_LABELS',
           'delete_import_junk', 'human_size', 'scan_import_junk',
           'summarize']
