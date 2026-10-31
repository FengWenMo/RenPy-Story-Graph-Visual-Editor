"""
storygraph/story/sg_transforms.py
=================================
**生成 `sg_*` 尺寸变换**（P1，2026-10-31）：把"铺满 / 适应 / 50%"写成一个真正的
Ren'Py transform 定义文件，而不是让人自己去 `transform.rpy` 里手抄。

用户拍板的前提（原话："选 1（允许生成 sg），但要加三个前提条件……一条都别省"）：

  ① **文件头"可检测的生成标记 + 内容 sha256"**：手改过就 **abort 不覆盖**，
     标记字符串保持稳定（`MARKER_PREFIX`），将来换版本靠 `MARKER_VERSIONS`；
  ② **生成前查重**：同名且内容一样 / 几何上能匹配到项目里已有的 transform
     → **复用，不写文件**；
  ③ **引用存在性检测**：脚本里 `at sg_*` 但项目里没有这个定义 → 点名
     （`missing_references` → `pipeline.validate` 的一条 L3 错误）。

## 为什么必须要 ①：生成文件和手写文件长得一模一样

`game/sg_transforms.rpy` 里只有 `transform sg_fit_cover:` 几行 —— 人完全可能
顺手在里面加一条自己的变换，或者把 `fit "cover"` 改成 `zoom 2`。工具下一次
"追加一条"时如果整份重写，那条手写内容就**没了**，而且没有任何地方会报错。
所以文件头存一个**内容指纹**：写盘前重算一遍，对不上就拒绝写，并由 UI 说清
"这个文件被改过，工具不会覆盖它"。

线尾（CRLF）不算手改：指纹按 LF 归一后再算。指纹对不上只说明内容变了，
不猜是哪一行变的 —— 那正是"不猜"的意思。

## 为什么 ② 放在这里而不是只在面板里

面板（`app/stage_render.size_rows`）先按几何匹配项目已有的 transform，匹配不到
才列出"生成 xxx"这一项。但那中间隔着一次刷新、一次点击、一次确认 —— 磁盘上的
`transform.rpy` 随时可能被别人改。所以**写盘之前**再查一遍重：同名同内容就
复用，几何能匹配就用那条现成的，一条都不写。

## 命名

名字是**可预测的**（人读 `.rpy` 时能一眼看出它干什么），也**不带图名**
（同一个"铺满"对所有图都成立，带上图名会生成一堆重复定义）：

    cover            → sg_fit_cover       (fit "cover")
    contain          → sg_fit_contain     (fit "contain")
    half / 自定义    → sg_zoom_050 / sg_zoom_075 …  (zoom 0.5 / 0.75)

名字被别的内容占了（人自己写过 `transform sg_fit_cover: zoom 3`）时**不改别人
的定义**，退到 `sg_fit_cover_2`，并在说明里写清为什么改叫这个。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.story import size_presets as SP
from storygraph.story.atl import parse_transforms
from storygraph.story.paths import write_text_atomic

#: 生成文件的固定名字（放在 `game/` 下，Ren'Py 启动时自动读入）。
FILE_NAME = 'sg_transforms.rpy'

#: 文件第一行的人话叮嘱（**不是**判据；判据是下一行的指纹）。
HEADER_NOTE = ('# 本文件由 Renpy Story Graph Editor 生成（尺寸变换 sg_*）。'
               '请不要手工修改。')
HEADER_NOTE2 = ('# 工具靠下一行的指纹判断它有没有被改过：改过就不再覆盖，'
                '只会告诉你"这个文件被改过"。')

#: 认标记时只看**前缀**：版本号与指纹在它后面，格式见 `MARKER_RE`。
MARKER_PREFIX = '# sg-gen'

#: `# sg-gen v1 sha256 <64 位十六进制>`。
MARKER_RE = re.compile(r'^#\s*sg-gen\s+v(\d+)\s+sha256\s+([0-9a-f]{64})\s*$')

#: 认识的标记版本。将来正文格式变了就加一版，**老版本仍然认**（用户要求：
#: 标记字符串保持稳定，或至少维护一份历史列表）。
MARKER_VERSIONS = (1,)

#: 找标记只看头几行（生成器总写在最上面；留点余量给人加注释）。
MARKER_SEARCH_LINES = 8

#: 一个 transform 定义的开头行（只认**顶层**、且不带参数 —— 我们只生成这两种）。
_NAME_RE = re.compile(r'^transform\s+([A-Za-z_]\w*)\s*:\s*$')

#: `at` 子句出现在这些语句后面（Ren'Py 里带 `at` 的就这几种）。
_AT_HEAD_RE = re.compile(r'^\s*(?:scene|show|add|camera)\b')

#: `at` 子句的**终点**：后面这些都是另一段子句了。
_AT_CUT_RE = re.compile(r'\s+(?:with|behind|onlayer|zorder|as)\s+')

#: `at` 子句的起点。
_AT_START_RE = re.compile(r'\bat\s+(.+)$')

_WORD_RE = re.compile(r'^([A-Za-z_]\w*)')

# 文件状态（`SgFile.state`）
MISSING = 'missing'            # 文件不存在（可以新建）
OK = 'ok'                      # 工具生成、指纹对得上（可以追加）
FOREIGN = 'foreign'            # 同名文件但不是工具生成的（绝不碰）
HAND_EDITED = 'hand_edited'    # 指纹对不上（绝不覆盖）
BAD_VERSION = 'bad_version'    # 指纹版本不认识
UNREADABLE = 'unreadable'      # 读不出来（权限 / 编码）

#: 计划动作
CREATE = 'create'
APPEND = 'append'
REUSE = 'reuse'
NOOP = 'noop'
REFUSE = 'refuse'


def _sha(text: str) -> str:
    """内容指纹（UTF-8、LF 归一后算 —— 见模块 docstring 里"线尾不算手改"）。"""
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


def file_path(project_root) -> Path:
    """生成文件的位置：`<项目根>/game/sg_transforms.rpy`。"""
    root = Path(project_root)
    try:
        if (root / 'game').is_dir():
            return root / 'game' / FILE_NAME
    except OSError as e:                       # noqa: BLE001 - 判不了就按约定拼
        swallow('story.sg_transforms.file_path', e)
    if root.name.lower() == 'game':
        return root / FILE_NAME
    return root / 'game' / FILE_NAME


def _rel_label(root, path) -> str:
    """给人看的相对路径（`game/sg_transforms.rpy`）；算不出就用文件名。"""
    try:
        return str(Path(path).relative_to(Path(root))).replace('\\', '/')
    except (ValueError, TypeError, OSError):
        return getattr(path, 'name', '') or FILE_NAME


# ======================================================================
#  组合
# ======================================================================

def transform_name(key: str, zoom: float = None) -> str:
    """
    预设 → 生成的 transform 名（`''` = 这个预设不生成任何东西）。

        cover → `sg_fit_cover` / contain → `sg_fit_contain`
        half  → `sg_zoom_050`   / 0.75    → `sg_zoom_075`

    名字里**不带图名**：同一个"铺满"对所有图都成立；带上图名会给 300 张图
    生成 300 条一模一样的定义。缩放用百分比整数（不足三位补零），这样
    `0.5` / `0.05` 不会因为 `%g` 的写法不同而生成两个名字。
    """
    key = str(key or '')
    if key == 'cover':
        return 'sg_fit_cover'
    if key == 'contain':
        return 'sg_fit_contain'
    z = SP.target_zoom(key, zoom)
    if z is None:
        return ''
    pct = float(z) * 100.0
    if abs(pct - round(pct)) < 1e-9:
        return f'sg_zoom_{int(round(pct)):03d}'
    tail = f'{float(z):g}'.replace('.', 'p')
    return f'sg_zoom_{tail}'


def definition_text(name: str, key: str, zoom: float = None) -> str:
    """
    一条完整的 transform 定义（带行尾换行；`''` = 这个预设不生成）。

    体来自 `size_presets.atl_body`（**唯一出处**）—— 生成器不自己拼 ATL。
    """
    stmt = SP.atl_body(key, zoom)
    if not stmt or not str(name or '').strip():
        return ''
    body = '\n'.join('    ' + line for line in str(stmt).split('\n'))
    return f'transform {str(name).strip()}:\n{body}\n'


def transform_def(name: str, key: str, zoom: float = None):
    """
    生成的那条定义 → `atl.TransformDef`（给"还没写盘"的计划算几何用）。

    刻意**从生成的文本再解析一次**，而不是手工造一个 `TransformDef`：
    否则"计划里算的几何"和"文件里写的东西"是两份代码，迟早漂移。
    解析不出来返回 None（调用方自己拒绝）。
    """
    text = definition_text(name, key, zoom)
    if not text:
        return None
    try:
        return parse_transforms(text).get(str(name).strip())
    except Exception as e:                     # noqa: BLE001 - 坏定义就是没有
        swallow('story.sg_transforms.transform_def', e)
        return None


def compose(entries) -> str:
    """
    条目列表 → **整份文件文本**（头 + 指纹 + 正文）。

    正文格式刻意最简：每两条定义之间一个空行、文件以换行结尾 —— 这样
    `git diff` 追加一条就是 "+4 行"，人一眼看得懂。
    """
    blocks = [str((e or {}).get('block') or '').strip('\n')
              for e in (entries or ())]
    blocks = [b for b in blocks if b.strip()]
    body = ('\n' + '\n\n'.join(blocks) + '\n') if blocks else '\n'
    marker = f'{MARKER_PREFIX} v1 sha256 {_sha(body)}'
    return f'{HEADER_NOTE}\n{HEADER_NOTE2}\n{marker}\n{body}'


# ======================================================================
#  解析 / 体检
# ======================================================================

@dataclass
class SgFile:
    """`game/sg_transforms.rpy` 的体检结果（**只读**，不写盘）。"""

    path: object = None
    rel: str = FILE_NAME
    exists: bool = False
    state: str = MISSING
    #: 标记行之后的全部内容（含开头那个空行）—— 指纹就是它的 sha256。
    body: str = ''
    #: `[{'name', 'block'}, …]`（按文件里的顺序）
    entries: list = field(default_factory=list)
    #: `state != ok/missing` 时的人话（**唯一措辞**，UI 直接显示）
    reason: str = ''
    #: 磁盘原文（测试/诊断用）
    text: str = ''

    @property
    def ok(self) -> bool:
        """能不能拿它继续写（不存在 = 可以新建）。"""
        return self.state in (MISSING, OK)

    def names(self) -> list:
        return [str(e.get('name') or '') for e in self.entries]

    def block_of(self, name: str):
        for e in self.entries:
            if str(e.get('name') or '') == str(name):
                return str(e.get('block') or '')
        return None

    def stmt_of(self, name: str):
        """某条定义的**属性行**（去掉 `transform …:` 与缩进）；没有 → None。"""
        block = self.block_of(name)
        if block is None:
            return None
        lines = block.strip('\n').split('\n')[1:]
        return '\n'.join(line[4:] if line.startswith('    ') else line.strip()
                         for line in lines).strip()


def _parse_entries(body: str) -> tuple:
    """
    正文 → `([{'name','block'}, …], [看不懂的行…])`。

    看不懂的行**不丢**：它们意味着这个文件里有工具不认识的内容（手写进去的
    `define` / 带参数的 transform…），调用方据此判 `hand_edited` 并拒绝覆盖。
    指纹能对上时不该出现这种行；真出现了，说明是我们自己的 bug 或有人连指纹
    一起改了 —— 两种都不该悄悄覆盖。
    """
    entries: list = []
    stray: list = []
    cur = None

    def close():
        nonlocal cur
        if cur is not None:
            cur['block'] = str(cur['block']).rstrip()
            entries.append(cur)
            cur = None

    for line in str(body or '').split('\n'):
        m = _NAME_RE.match(line)
        if m:
            close()
            cur = {'name': m.group(1), 'block': line}
            continue
        if not line.strip():
            close()                            # 空行 = 条目之间
            continue
        if cur is not None and (line.startswith(' ') or line.startswith('\t')):
            cur['block'] = f"{cur['block']}\n{line}"
            continue
        stray.append(line)
    close()
    return entries, stray


def parse(text: str, path=None, rel: str = FILE_NAME) -> SgFile:
    """
    文件文本 → `SgFile`（**纯函数**：不读盘，判据全在这里）。

    四道判据，顺序就是"能相信到什么程度"：
      ① 头几行里有没有 `MARKER_PREFIX`（没有 = 别人的文件）；
      ② 版本号认不认识；
      ③ 正文 sha256 与标记里的是不是一样（不一样 = 被手改过）；
      ④ 正文里有没有工具不认识的行。
    """
    raw = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    if raw.startswith('\ufeff'):
        raw = raw[1:]
    lines = raw.split('\n')
    idx = -1
    for i, line in enumerate(lines[:MARKER_SEARCH_LINES]):
        if MARKER_RE.match(line.strip()):
            idx = i
            break
    if idx < 0:
        return SgFile(path=path, rel=rel, exists=True, state=FOREIGN,
                      text=raw,
                      reason=(f'{rel} 不是工具生成的（头 {MARKER_SEARCH_LINES} 行里'
                              '没有 sg-gen 指纹），工具不会改动它'))
    m = MARKER_RE.match(lines[idx].strip())
    version = int(m.group(1))
    want = m.group(2)
    body = '\n'.join(lines[idx + 1:])
    out = SgFile(path=path, rel=rel, exists=True, body=body, text=raw)
    if version not in MARKER_VERSIONS:
        out.state = BAD_VERSION
        out.reason = (f'{rel} 的指纹版本是 v{version}，这个版本的工具有点老，'
                      f'只认 v{"/v".join(str(v) for v in MARKER_VERSIONS)}，'
                      '不会改它')
        return out
    if _sha(body) != want:
        out.state = HAND_EDITED
        out.reason = (f'{rel} 在上次生成之后被改过（指纹对不上），工具不会覆盖它。'
                      '把手改的内容挪到别的 .rpy，或者删掉这个文件让工具重新生成')
        return out
    entries, stray = _parse_entries(body)
    out.entries = entries
    if stray:
        out.state = HAND_EDITED
        out.reason = (f'{rel} 里有工具不认识的内容'
                      f'（{stray[0].strip()[:40]}…），工具不会覆盖它')
        return out
    out.state = OK
    return out


def inspect(project_root) -> SgFile:
    """读盘 + `parse`（**只读**；读不出来也不抛）。"""
    if not project_root:
        return SgFile(state=MISSING, reason='还没打开项目')
    root = Path(project_root)
    path = file_path(root)
    rel = _rel_label(root, path)
    try:
        exists = path.is_file()
    except OSError as e:                       # noqa: BLE001 - 读不出来就别赌
        swallow('story.sg_transforms.inspect.stat', e)
        return SgFile(path=path, rel=rel, exists=True, state=UNREADABLE,
                      reason=f'{rel} 读不出来（{e}），工具不会动它')
    if not exists:
        return SgFile(path=path, rel=rel, state=MISSING)
    try:
        data = path.read_bytes()
    except OSError as e:                       # noqa: BLE001 - 权限/占用
        swallow('story.sg_transforms.inspect.read', e)
        return SgFile(path=path, rel=rel, exists=True, state=UNREADABLE,
                      reason=f'{rel} 读不出来（{e}），工具不会动它')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as e:
        swallow('story.sg_transforms.inspect.decode', e)
        return SgFile(path=path, rel=rel, exists=True, state=UNREADABLE,
                      reason=f'{rel} 不是 UTF-8 文本，工具不会动它')
    return parse(text, path=path, rel=rel)


# ======================================================================
#  计划（查重 → 生成）
# ======================================================================

@dataclass
class SgPlan:
    """
    一次"写一条 `sg_*` 定义"的计划（**只算，不写**；确认后调 `apply`）。

    `action` 只可能是 `create` / `append`（要写）、`reuse` / `noop`
    （不用写 —— 查重命中）或 `refuse`（护栏拦下）。
    """

    action: str = REFUSE
    name: str = ''
    key: str = ''
    zoom: object = None
    rel: str = FILE_NAME
    path: object = None
    #: 要写的整份文件文本（`create` / `append` 才有）
    text: str = ''
    #: 写完之后的条目（追加前的那些 + 新的那条）
    entries: list = field(default_factory=list)
    #: 为什么改叫 `_2` 之类的补充说明（空 = 没有）
    note: str = ''
    reason: str = ''

    @property
    def ok(self) -> bool:
        """能不能继续（`reuse` / `noop` 也算能 —— 只是不用写文件）。"""
        return self.action != REFUSE

    @property
    def writes(self) -> bool:
        return self.action in (CREATE, APPEND)

    @property
    def block(self) -> str:
        for e in reversed(self.entries or ()):
            if str(e.get('name') or '') == str(self.name or ''):
                return str(e.get('block') or '')
        return ''

    def diff_text(self) -> str:
        """确认框里的那一段（**唯一措辞**：窗口层直接拼进对话框）。"""
        if self.action == REFUSE:
            return f'不能生成 transform：{self.reason}'
        if self.action == REUSE:
            return f'不用新建：{self.reason}'
        if self.action == NOOP:
            return f'不用写文件：{self.rel} 里已经有 {self.name}'
        head = (f'新建 {self.rel}（工具生成、带指纹；以后手改过就拒绝覆盖）'
                if self.action == CREATE
                else f'在 {self.rel} 里追加一条（其余条目不动）')
        plus = '\n'.join('+ ' + line for line in self.block.split('\n'))
        return f'{head}：\n\n{plus}' + (f'\n\n{self.note}' if self.note else '')

    def apply(self):
        """
        把计划写盘（`create` / `append`）；`reuse` / `noop` / `refuse` → None。

        写之前**再看一眼**磁盘：从 `plan_define` 到现在隔着一次确认框，
        这中间文件可能被人改过。状态不是"可写"、或者条目对不上 → 抛
        `RuntimeError`（不写），由 UI 把原因说出来 —— **绝不闷头覆盖**。
        """
        if not self.writes:
            return None
        path = Path(self.path) if self.path is not None else None
        if path is None:
            raise RuntimeError('没有目标文件（计划不完整）')
        try:
            exists = path.is_file()
        except OSError as e:                   # noqa: BLE001 - 判不了就别写
            raise RuntimeError(f'{self.rel} 判不了在不在：{e}') from e
        if exists:
            try:
                text = path.read_text(encoding='utf-8-sig', errors='replace')
            except OSError as e:               # noqa: BLE001 - 读不出来就别写
                raise RuntimeError(f'{self.rel} 读不出来：{e}') from e
            now = parse(text, path=path, rel=self.rel)
            if not now.ok:
                raise RuntimeError(now.reason or f'{self.rel} 现在的状态不能写')
            before = [(e.get('name'), e.get('block')) for e in now.entries]
            want = [(e.get('name'), e.get('block')) for e in self.entries[:-1]]
            if before != want:
                raise RuntimeError(f'{self.rel} 在这中间被改过了（条目对不上），'
                                   '这次没有写')
        elif self.action == APPEND:
            raise RuntimeError(f'{self.rel} 不见了，这次没有写')
        write_text_atomic(path, self.text)
        return path


def _unique_name(base: str, taken) -> str:
    """`base` 被占了就 `base_2` / `base_3`…（**不改别人的定义**）。"""
    base = str(base or '').strip()
    if not base:
        return ''
    taken = {str(t) for t in (taken or ())}
    if base not in taken:
        return base
    n = 2
    while f'{base}_{n}' in taken:
        n += 1
    return f'{base}_{n}'


def _def_stmt(defn) -> str:
    """`TransformDef` 的顶层语句 → 一行文本（与我们生成的 `atl_body` 比）。"""
    try:
        return '; '.join(str(s).strip() for s in (defn.body or ()))
    except AttributeError:
        return ''


def plan_define(sg, key: str, zoom: float = None, *, index=None, natural=None,
                screen=None) -> SgPlan:
    """
    预设计划（**只算，不写盘**）：查重 → 决定写不写 / 写在哪。

    `sg` 是 `SgFile`（`inspect` 的结果）。`index` 是项目 transform 表
    （`atl.scan_transforms`），`natural` / `screen` 只用于**几何查重**
    （没有就不查 —— 不猜）。

        reuse  → 项目里已经有等价的东西，**不写文件**
        noop   → 文件里已经有同名同内容的定义
        create → 新建文件
        append → 往工具生成的文件里追加一条
        refuse → 护栏拦下（文件被手改 / 不是工具生成的 / 预设不可生成）
                  ⚠ "名字被别人占了"**不是** refuse：退到 `sg_xxx_2` 照样能写
    """
    key = str(key or '')
    if key == 'as_is':
        return SgPlan(action=REFUSE, key=key,
                      reason='「原样」是去掉 at，不生成任何 transform')
    if key not in SP.PRESET_ORDER:
        return SgPlan(action=REFUSE, key=key,
                      reason=f'「{key}」不是可生成的尺寸预设')
    stmt = SP.atl_body(key, zoom)
    if not stmt:
        return SgPlan(action=REFUSE, key=key,
                      reason=f'「{key}」没有可写的 ATL 体')
    index = dict(index or {})
    base = transform_name(key, zoom)
    if not base:
        return SgPlan(action=REFUSE, key=key,
                      reason=f'「{key}」算不出 transform 名')
    rel = getattr(sg, 'rel', FILE_NAME)

    # ② 查重之一：同名同内容 → 复用（连文件都不用读第二遍）
    defn = index.get(base)
    if defn is not None and _def_stmt(defn) == stmt:
        return SgPlan(action=REUSE, name=base, key=key, zoom=zoom, rel=rel,
                      reason=f'项目里已经有 {base}（{stmt}），直接用它')
    # ② 查重之二：几何上能匹配到别的 transform → 复用
    if natural:
        try:
            hit = SP.match_preset(key, natural, screen, index, zoom=zoom)
        except Exception as e:                 # noqa: BLE001 - 匹配失败就当没有
            swallow('story.sg_transforms.plan_define.match', e)
            hit = None
        if hit is not None:
            return SgPlan(action=REUSE, name=str(hit.get('name') or ''),
                          key=key, zoom=zoom, rel=rel,
                          reason=(f'项目里已有等价变换 {hit.get("name")}'
                                  f'（{hit.get("brief")}），复用不新建'))
    # ③ 护栏：文件本身可不可写
    if not getattr(sg, 'ok', False):
        return SgPlan(action=REFUSE, key=key, zoom=zoom, rel=rel,
                      reason=(getattr(sg, 'reason', '')
                              or f'{rel} 现在不能写'))
    # 文件里已经有这一条（同名同内容）→ 什么都不用做
    cur = sg.stmt_of(base) if hasattr(sg, 'stmt_of') else None
    if cur is not None and cur == stmt:
        return SgPlan(action=NOOP, name=base, key=key, zoom=zoom, rel=rel,
                      path=getattr(sg, 'path', None),
                      entries=list(getattr(sg, 'entries', ()) or ()),
                      reason=f'{rel} 里已经有 {base}')

    taken = set(index) | set(getattr(sg, 'names', lambda: [])())
    name = _unique_name(base, taken)
    note = ''
    if name != base:
        note = (f'（{base} 这个名字已经被别的内容占用，所以叫 {name} —— '
                '工具不会改别人的定义）')
    block = definition_text(name, key, zoom).rstrip('\n')
    entries = list(getattr(sg, 'entries', ()) or ()) + \
        [{'name': name, 'block': block}]
    return SgPlan(action=(APPEND if getattr(sg, 'exists', False) else CREATE),
                  name=name, key=key, zoom=zoom, rel=rel,
                  path=getattr(sg, 'path', None),
                  text=compose(entries), entries=entries, note=note,
                  reason=f'{name} = {stmt}')


def plan_for_project(project_root, key: str, zoom: float = None, *, index=None,
                     natural=None, screen=None) -> SgPlan:
    """`inspect` + `plan_define`（窗口层用这个；一次点击只读一次文件）。"""
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return SgPlan(action=REFUSE, key=str(key or ''),
                      reason='还没打开项目（没有项目根），生成不了')
    try:
        if not (root / 'game').is_dir():
            return SgPlan(action=REFUSE, key=str(key or ''),
                          reason=f'{root} 下没有 game/ 目录，这是 Ren\'Py 工程吗？')
    except OSError as e:                       # noqa: BLE001 - 判不了就别写
        swallow('story.sg_transforms.plan_for_project.game_dir', e)
        return SgPlan(action=REFUSE, key=str(key or ''),
                      reason=f'读不出来 {root} 下有没有 game/ 目录：{e}')
    return plan_define(inspect(root), key, zoom, index=index, natural=natural,
                       screen=screen)


# ======================================================================
#  ③ 引用存在性：`at sg_*` 但项目里没这个定义
# ======================================================================

def _strip_comment(line: str) -> str:
    """去掉行尾注释（`#` 在引号里不算注释）。"""
    out, quote = [], ''
    for ch in str(line or ''):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ''
            continue
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            continue
        if ch == '#':
            break
        out.append(ch)
    return ''.join(out)


def _script_files(project_root, limit_files: int = 400) -> list:
    """项目里的 `*.rpy`（与 `atl.scan_transforms` 同一套找法，**有界**）。"""
    root = Path(project_root)
    files: list = []
    for base in (root / 'game', root):
        try:
            if not base.is_dir():
                continue
        except OSError as e:                   # noqa: BLE001 - 判不了就跳过
            swallow('story.sg_transforms.script_files.is_dir', e)
            continue
        try:
            files.extend(sorted(base.rglob('*.rpy')))
        except OSError as e:                   # noqa: BLE001 - 扫不动就跳过
            swallow('story.sg_transforms.script_files.rglob', e)
        if files:
            break
    out = []
    for path in files[:max(1, int(limit_files))]:
        try:
            if any(part in ('.git', '__pycache__', 'build_out')
                   for part in path.parts):
                continue
        except TypeError:
            continue
        out.append(path)
    return out


def scan(project_root, limit_files: int = 400) -> tuple:
    """
    **一次读完**项目里的 `at sg_*` 引用与 transform 定义：`(refs, index)`。

    为什么合成一遍（2026-10-31 实测）：真机两个项目各 200 / 95 个 `.rpy`，
    分两遍扫是 2×355ms；"体检"是一次点击的事，但没理由让它读两遍同样的
    文件。两遍读法还多一个隐患 —— 两份结果来自**不同时刻**的磁盘。
    """
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return [], {}
    from storygraph.story.atl import parse_transforms

    refs: list = []
    index: dict = {}
    for path in _script_files(root, limit_files):
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError as e:                   # noqa: BLE001 - 读不出来就跳过
            swallow('story.sg_transforms.scan.read', e)
            continue
        try:
            for name, defn in parse_transforms(text).items():
                index.setdefault(name, defn)   # 先定义的赢（与 scan_transforms 同）
        except Exception as e:                 # noqa: BLE001 - 坏文件跳过
            swallow('story.sg_transforms.scan.transforms', e)
        rel = _rel_label(root, path)
        for i, raw in enumerate(text.split('\n')):
            line = _strip_comment(raw)
            if not _AT_HEAD_RE.match(line):
                continue
            m = _AT_START_RE.search(line)
            if not m:
                continue
            clause = m.group(1)
            cut = _AT_CUT_RE.search(clause)
            if cut:
                clause = clause[:cut.start()]
            for part in clause.split(','):
                wm = _WORD_RE.match(part.strip())
                if not wm:
                    continue
                name = wm.group(1)
                if not name.startswith('sg_'):
                    continue
                refs.append({'name': name, 'file': rel, 'line': i,
                             'text': line.strip()})
    return refs, index


def references(project_root, limit_files: int = 400) -> list:
    """
    项目里**全部** `at sg_*` 引用：`[{'name','file','line','text'}, …]`。

    `line` 是 0 基行号；`file` 是相对项目根的路径（`game/chapter/x.rpy`）。
    只看 `scene` / `show` / `add` / `camera` 这几种带 `at` 的语句 —— 台词里
    正好写着 "meet me at sg_home" 那种不算。
    """
    return scan(project_root, limit_files=limit_files)[0]


def missing_references(project_root, index=None, limit_files: int = 400) -> list:
    """
    引用了、但项目里**没有定义**的 `sg_*`：`[{'name','file','line','text'}, …]`。

    `index` 给了就用它（调用方可能已经有一份）；没给就**同一次扫描**里顺便
    把定义收齐（`scan`）。同一个 (名字, 文件, 行) 只留一条 —— 一份报告里
    重复三遍只会让人不看了。
    """
    refs, scanned = scan(project_root, limit_files=limit_files)
    if not refs:
        return []
    index = scanned if index is None else (index or {})
    seen = set()
    out = []
    for ref in refs:
        name = str(ref.get('name') or '')
        if not name or name in index:
            continue
        key = (name, ref.get('file'), ref.get('line'))
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    out.sort(key=lambda r: (str(r.get('file') or ''), int(r.get('line') or 0),
                            str(r.get('name') or '')))
    return out


__all__ = ['APPEND', 'BAD_VERSION', 'CREATE', 'FILE_NAME', 'FOREIGN',
           'HAND_EDITED', 'HEADER_NOTE', 'MARKER_PREFIX', 'MARKER_RE',
           'MARKER_VERSIONS', 'MISSING', 'NOOP', 'OK', 'REFUSE', 'REUSE',
           'SgFile', 'SgPlan', 'UNREADABLE', 'compose', 'definition_text',
           'file_path', 'inspect', 'missing_references', 'parse', 'plan_define',
           'plan_for_project', 'references', 'scan', 'transform_def',
           'transform_name']
