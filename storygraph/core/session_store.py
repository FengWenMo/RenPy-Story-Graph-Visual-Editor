"""
**会话记忆**：下次打开时自动回到上次那个项目、并把布局摆回去。
==========================================================================

用户 2026-10-13：

> 再给这个工具加个"记忆"功能，当第二次打开时自动读取之前打开过的那个项目，
> 如果项目内部有保存布局，一并读取，能做到吗？

## 存哪儿（这条最要紧）

**一律存"用户级"目录，绝不写进用户的项目仓库**：

```
%LOCALAPPDATA%/StoryGraph/session.json        （Windows）
$XDG_STATE_HOME/storygraph/session.json       （其它平台）
```

与 `core/ai_settings.py`（`ai.json`）、`core/crashlog.py` 同一处约定；
用 `STORYGRAPH_SESSION_CONFIG` 覆盖（打包 / 多用户 / 测试都要用得上）。

⚠ **为什么不写进项目里的 `storygraph.rules.json`**：那份文件是**项目的**
（进 git、要评审）；"我上次把左栏拖多宽"属于**这台机器的这个人**，
写进去会污染项目 diff，还会在多人协同时互相打架。

## "项目内部有保存布局"怎么满足

如果项目根**自己**有一份 `storygraph.session.json`，本模块会读它并合到
用户级之上（项目级的字段优先）—— 于是"项目里存了布局"也生效。
想固定一份团队布局，就手工放进项目根。

## 唯一的例外：卡片折叠状态**跟项目走**（2026-10-22）

```json
{ "cards": { "folded": { "game/script/ch01.rpy": ["ch01_roof"] } } }
```

用户 2026-10-22：

> 继续完成折叠状态跟项目走

折叠状态以前是**偏好**（按绝对路径存在注册表里），于是"项目搬到别的目录 /
换台机器"就全展开。现在：**文件在项目根里面 → 写项目里这份 `cards` 段**；
文件不在项目里（直接打开项目外的 `.rpy`）→ 仍写用户偏好。

为什么这条例外可以破 —— 三条一起看：

1. 值用**项目内相对路径**（`game/script/ch01.rpy`）当键，整目录搬走仍然对得上；
2. 写的是**同一份** `storygraph.session.json` 里的**另一段**：写之前把整份
   读出来、只改 `cards.folded` 里那一个键再盖回去，`layout` 段一字不动；
3. 读不回来 / 写不进去**都不拦人**（只记台账），界面继续按当前状态走；
   但**读不懂的那份绝不盖回去** —— 这份文件人也会手改（团队往里放 `layout`），
   "解析失败就写回一个只有 `cards` 的新文件"等于把别人写的内容删掉，
   所以那种情况直接失败、让调用方退回偏好。

`remember_project()` / `save()` 仍然**只写用户级**：这份项目文件的其它字段
（`layout`）依旧"只读"，唯一的写入点是 `remember_cards()`。

## 存什么

```json
{
  "version": 1,
  "last_project": "D:/千连万理/RenpyTest/sr_general",
  "auto_open": true,
  "recent": ["D:/…/sr_general", "D:/…/RenpyTest"],
  "projects": {
    "D:/千连万理/RenpyTest/sr_general": {
      "project_name": "sr_general",
      "last_chapter": "D:/…/game/chapter/ch01.rpy",
      "last_chapter_rel": "game/chapter/ch01.rpy",
      "page": 0,
      "card_index": 3,
      "geometry": [1600, 950, 200, 120],   // 宽 高 x y（x/y 为 null 表示没记）
      "split_graph": [1080, 340],
      "split_card": [260, 900],
      "updated": "2026-10-13T21:00:00"
    }
  }
}
```

## 三条硬规矩（都在判据里钉着）

1. **读不回来就当没有**（文件坏了 / 项目没了 / 字段缺）—— 绝不抛异常、
   绝不拦启动，只把原因记进 `swallow` 台账；
2. **项目路径不存在就不自动打开**（否则用户会看到"打开了一个不存在的项目"）；
3. **`STORYGRAPH_NO_SESSION` / `auto_open: false` 能一键关掉**
   （打包自检、自动化、以及"我今天想空着开"都要用得上）。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 用户级会话文件（`STORYGRAPH_SESSION_CONFIG` 可覆盖）
_ENV = 'STORYGRAPH_SESSION_CONFIG'
#: 一键关掉自动打开（任何非空值都算关）—— 打包自检 / 自动化用
_ENV_OFF = 'STORYGRAPH_NO_SESSION'
#: 项目根里**可选**的那份（`layout` 只读；只有 `cards` 段归工具写）
PROJECT_FILE = 'storygraph.session.json'
#: 项目文件里"卡片视图"那一段（见模块 docstring 的例外说明）
CARDS_SECTION = 'cards'
#: 最多记几个最近项目
MAX_RECENT = 8
#: 当前结构版本（以后加字段靠它迁移；读到更大的版本就当读不懂）
VERSION = 1


def config_path() -> Path:
    """
    用户级会话文件路径。

    与 `core/ai_settings.config_path()`（`ai.json`）同一处约定 ——
    用户已经知道"这工具的东西存在 `%LOCALAPPDATA%/StoryGraph/`"，
    再开一个新地方会让人找不到该删什么。
    """
    env = os.environ.get(_ENV, '').strip()
    if env:
        return Path(env)
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or ''
        if base:
            return Path(base) / 'StoryGraph' / 'session.json'
    base = os.environ.get('XDG_STATE_HOME') or ''
    root = Path(base) if base else Path.home() / '.local' / 'state'
    return root / 'storygraph' / 'session.json'


def disabled() -> bool:
    """这一趟**不要**自动打开任何项目（`STORYGRAPH_NO_SESSION`）。"""
    return bool(os.environ.get(_ENV_OFF, '').strip())


#: 总开关：`False` 时**连写都不写**（打包自检 / 测试用）。
#:
#: 为什么要有它（不是洁癖）：窗口在 `closeEvent` 里落一次记忆，
#: 而测试会造几百个临时项目当窗口 —— 那会把你**真实的**最近项目表
#: 灌满 `tmp_path`，还会把你自己的项目挤出列表。所以测试里直接
#: `SS.set_enabled(False)`（见 `tests/conftest.py` 的 autouse 夹具）。
_ENABLED = True


def set_enabled(on: bool):
    """全局开 / 关记忆（`closeEvent` 那一侧看这个）。"""
    global _ENABLED
    _ENABLED = bool(on)


def enabled() -> bool:
    """现在允许读写记忆吗？"""
    return bool(_ENABLED) and not disabled()


def _key(root) -> str:
    """
    项目在表里的键 = **规范化后的绝对路径**（正斜杠）。

    ⚠ 不做 `resolve()`（会解符号链接、也会碰盘）—— 只做 `abspath` + 统一斜杠：
    Windows 上 `D:\\a\\b` 与 `D:/a/b` 是同一个地方，键必须一样。
    大小写故意**不统一**：`C:\\Proj` 与 `c:\\proj` 在两个平台上都可能是
    不同的目录（Windows 上多数情况同一个，但改成小写会让键看起来不是用户给的路径）。
    """
    try:
        return os.path.abspath(str(root or '')).replace('\\', '/')
    except Exception as e:                       # noqa: BLE001 - 算不出就用原串
        swallow('core.session_store.key', e)
        return str(root or '')


# ===========================================================================
#  读
# ===========================================================================

def load(path=None) -> dict:
    """
    读会话表（**读不出来 = 空表**，绝不抛）。

    结构不对（不是 dict / 版本太新 / `projects` 不是 dict）→ 当空表：
    宁可"这次不记忆"，也不要把半个坏表带进启动流程。
    """
    fp = Path(path) if path else config_path()
    empty = {'version': VERSION, 'last_project': '', 'auto_open': True,
             'recent': [], 'projects': {}}
    try:
        if not fp.is_file():
            return empty
        data = json.loads(fp.read_text(encoding='utf-8'))
    except Exception as e:                       # noqa: BLE001 - 手写 / 半截文件容错
        swallow('core.session_store.load', e)
        return empty
    if not isinstance(data, dict):
        return empty
    try:
        version = int(data.get('version') or VERSION)
    except (TypeError, ValueError):
        version = VERSION
    if version > VERSION:
        return empty                             # 更新的工具写的：读不懂就别猜
    out = dict(empty)
    out['last_project'] = str(data.get('last_project') or '')
    out['auto_open'] = bool(data.get('auto_open', True))
    recent = data.get('recent')
    if isinstance(recent, (list, tuple)):
        out['recent'] = [str(r) for r in recent if str(r or '').strip()][:MAX_RECENT]
    projects = data.get('projects')
    if isinstance(projects, dict):
        out['projects'] = {str(k): v for k, v in projects.items()
                           if isinstance(v, dict)}
    return out


def project_layout(root, path=None) -> dict:
    """
    某个项目的布局（**用户级在前，项目里那份覆盖它**）。

    项目根里的 `storygraph.session.json` 是**可选**的：有就用（团队可以
    把一份布局放进项目一起评审 / 一起用），没有就完全靠用户级。
    两份都读不出来 → `{}`（调用方按默认布局走）。
    """
    merged: dict = {}
    if not root:
        return merged
    try:
        data = load(path)
        hit = data.get('projects', {}).get(_key(root))
        if isinstance(hit, dict):
            merged.update(hit)
    except Exception as e:                       # noqa: BLE001 - 用户级读不出就算了
        swallow('core.session_store.project_layout.user', e)
    try:
        fp = Path(root) / PROJECT_FILE
        if fp.is_file():
            proj = json.loads(fp.read_text(encoding='utf-8'))
            if isinstance(proj, dict):
                # 允许两种形状：直接给字段，或者包在 `layout` 里
                layout = proj.get('layout') if isinstance(proj.get('layout'), dict) \
                    else proj
                for k, v in layout.items():
                    # `cards` 是**卡片视图**（折叠状态），不是布局字段：
                    # 混进来会被下游当成一个不认识的版式键带回去。
                    if k not in ('projects', CARDS_SECTION):
                        merged[k] = v
    except Exception as e:                       # noqa: BLE001 - 项目里那份坏了也别拦
        swallow('core.session_store.project_layout.project', e)
    return merged


def project_file(root) -> Path:
    """项目根里那份可选文件的路径（**不保证存在**）。"""
    return Path(str(root)) / PROJECT_FILE


def read_project_file(root) -> dict:
    """
    读项目根那份文件（**读不出来就是 `{}`**，绝不抛）。

    为什么不复用 `load()`：那份是**用户级**会话表（有版本号、`projects` 分节），
    形状完全不同 —— 混着读会把"项目里的 `cards`"当成"用户级的字段"。
    """
    fp = project_file(root)
    try:
        if not fp.is_file():
            return {}
        # `utf-8-sig`：用户可能拿编辑器改过这份文件，Windows 上很容易带上 BOM。
        # 不带 BOM 时它和 `utf-8` 完全一样（多一个字符都不吃）。
        data = json.loads(fp.read_text(encoding='utf-8-sig'))
    except Exception as e:                       # noqa: BLE001 - 手写 / 半截文件容错
        swallow('core.session_store.read_project_file', e)
        return {}
    return data if isinstance(data, dict) else {}


def project_cards(root) -> dict:
    """
    项目里记的折叠状态：`{项目内相对路径: [折起的 label, ...]}`。

    键**缺了**与**值是空表**是两件事：
      · 缺键 = "这个文件从来没折过"（调用方可以去旧偏好里找一次）；
      · `[]` = "折过、后来又都展开了"（明确的"没有"，不该再翻旧偏好）。
    所以 `remember_cards(..., [])` 会**留下**这个键，而不会把它删掉。
    """
    cards = read_project_file(root).get(CARDS_SECTION)
    folded = cards.get('folded') if isinstance(cards, dict) else None
    if not isinstance(folded, dict):
        return {}
    out: dict = {}
    for rel, names in folded.items():
        rel = str(rel or '')
        if not rel:
            continue
        if isinstance(names, (list, tuple)):
            out[rel] = [str(n) for n in names if str(n or '').strip()]
    return out


def last_project(path=None) -> str:
    """上次打开的项目根（**目录不存在就返回空** —— 不自动打开不存在的东西）。"""
    try:
        data = load(path)
    except Exception as e:                       # noqa: BLE001
        swallow('core.session_store.last_project', e)
        return ''
    root = str(data.get('last_project') or '')
    if not root:
        return ''
    try:
        if not Path(root).is_dir():
            return ''
    except Exception as e:                       # noqa: BLE001 - 判不了就当没有
        swallow('core.session_store.last_project.exists', e)
        return ''
    return root


def recent_projects(path=None) -> list:
    """最近项目列表（**已过滤掉不存在的**，最近的在最前）。"""
    try:
        data = load(path)
    except Exception as e:                       # noqa: BLE001
        swallow('core.session_store.recent_projects', e)
        return []
    out = []
    for root in data.get('recent') or []:
        try:
            if Path(root).is_dir() and root not in out:
                out.append(root)
        except Exception as e:                   # noqa: BLE001
            swallow('core.session_store.recent_projects.entry', e)
    return out


# ===========================================================================
#  写
# ===========================================================================

def save(data: dict, path=None) -> dict:
    """
    写会话表。返回 `{'ok', 'path', 'reason'}`（**不抛**）。

    ⚠ 写的是**用户级**文件；失败只是"这次没记住"，绝不拦用户干活
    （目录建不出来、盘只读、被别人锁着 —— 都只记台账）。
    """
    fp = Path(path) if path else config_path()
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': VERSION,
            'last_project': str((data or {}).get('last_project') or ''),
            'auto_open': bool((data or {}).get('auto_open', True)),
            'recent': [str(r) for r in ((data or {}).get('recent') or [])
                       if str(r or '').strip()][:MAX_RECENT],
            'projects': {str(k): v for k, v in
                         (((data or {}).get('projects') or {})).items()
                         if isinstance(v, dict)},
        }
        tmp = fp.with_suffix(fp.suffix + '.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding='utf-8')
        os.replace(tmp, fp)                      # 原子替换：半截文件不会留在原地
        return {'ok': True, 'path': str(fp), 'reason': ''}
    except Exception as e:                       # noqa: BLE001 - 记忆失败不该拦人
        swallow('core.session_store.save', e)
        return {'ok': False, 'path': str(fp),
                'reason': f'{type(e).__name__}: {e}'}


def remember_project(root, layout: dict = None, path=None) -> dict:
    """
    记下"我打开了这个项目"（并可选地更新它的布局）。

    **一次调用就能同时更新三处**：`last_project` / `recent`（去重置顶）/
    `projects[键]`（与旧字段**合并**，不是覆盖 —— 只报页面时不该把分栏宽度抹掉）。
    """
    root = str(root or '')
    if not root:
        return {'ok': False, 'path': str(path or config_path()),
                'reason': '没给项目根'}
    data = load(path)
    key = _key(root)
    data['last_project'] = root
    recent = [r for r in (data.get('recent') or []) if _key(r) != key]
    data['recent'] = [root] + recent
    entry = dict(data.get('projects', {}).get(key) or {})
    entry['project_name'] = Path(root).name
    if layout:
        for k, v in layout.items():
            if v is not None:
                entry[k] = v
    entry['updated'] = datetime.now().isoformat(timespec='seconds')
    data.setdefault('projects', {})[key] = entry
    res = save(data, path)
    res['key'] = key
    return res


def set_auto_open(on: bool, path=None) -> dict:
    """开 / 关"下次自动打开上次的项目"（界面上那个勾就是这个）。"""
    data = load(path)
    data['auto_open'] = bool(on)
    return save(data, path)


def remember_graph_files(root, files, path=None) -> dict:
    """
    记住"这个项目的剧情图上导入了哪些文件"（L7 完整版，2026-10-24）。

    ## 为什么需要

    W1b 只在"导入章节之后"贴版式 —— 可是**重开软件时图是空的**，
    没有节点可贴，版式照样用不上。用户看到的就是"上次摆好的又没了"。

    记住这批文件之后，下次打开项目可以**自动重建同一张图**，
    再贴版式，于是"摆好的版式"真的回来了。

    ## 形状

    存项目内**相对路径**（换机器、换盘符都还在），落在用户级会话表的
    `projects[键].graph_files`；也可以被项目根的 `storygraph.session.json`
    覆盖（团队共用一份时）。

    空表也要写：它表示"这个项目确实没有图"，和"从没记过"不是一回事。
    """
    root_s = str(root or '')
    if not root_s:
        return {'ok': False, 'reason': '没给项目根', 'count': 0}
    rels = []
    for fp in (files or ()):
        text = str(fp or '').strip()
        if not text:
            continue
        try:
            rel = Path(text).resolve().relative_to(Path(root_s).resolve())
            rels.append(rel.as_posix())
        except (ValueError, OSError):
            rels.append(Path(text).as_posix())     # 项目外的文件：原样记
    seen, ordered = set(), []
    for rel in rels:
        if rel not in seen:
            seen.add(rel)
            ordered.append(rel)
    res = remember_project(root_s, layout={'graph_files': ordered}, path=path)
    res['count'] = len(ordered)
    return res


def graph_files(root, path=None) -> list:
    """
    读回"上次导入剧情图的文件"，**绝对路径、按记录顺序**。

    文件已经不存在的**不返回**（那是被删/改名了，硬塞回去只会多出空节点）；
    调用方拿到的就是"现在还能用的那批"。
    """
    root_s = str(root or '')
    if not root_s:
        return []
    rels = project_layout(root_s, path).get('graph_files') or []
    out = []
    base = Path(root_s)
    for rel in rels:
        p = Path(str(rel))
        full = p if p.is_absolute() else (base / p)
        try:
            if full.is_file():
                out.append(str(full))
        except OSError:
            continue
    return out


def remember_cards(root, rel, folded) -> dict:
    """
    把**一个文件**的折叠状态写进项目根那份 `storygraph.session.json`。

    参数
    ----
    `rel`    ：项目内相对路径（正斜杠），例如 `game/script/ch01.rpy`；
    `folded` ：折起来的 label 名（**空表也要写** —— 它表示"折过、又都展开了"，
               删掉这个键会让下次打开误以为"从没折过"而去翻旧偏好）。

    形状（只动 `cards.folded` 这一小块）：

    ```json
    { "cards": { "folded": { "game/script/ch01.rpy": ["ch01_roof"] } } }
    ```

    ⚠ 三条硬规矩：
      1. **读-改-写**：整份读出来，只替换那一个键，其余字段（尤其 `layout`）
         原样写回；键没变化就**不写盘**（翻页 / 展开不该反复改项目文件）；
      2. **原子替换**（`*.tmp` + `os.replace`）：写一半断电不会留下半截 JSON；
      3. **失败不拦人**：返回 `ok=False` + 原因，调用方退回用户偏好。

    返回 `{'ok','path','reason','changed'}`。
    """
    rel = str(rel or '').replace('\\', '/').strip()
    root = str(root or '')
    fp = project_file(root) if root else None
    if not root or not rel:
        return {'ok': False, 'path': str(fp or ''),
                'reason': '没给项目根或文件相对路径', 'changed': False}
    names = [str(n) for n in (folded or ()) if str(n or '').strip()]
    data = {}
    if fp.is_file():
        # ⚠ **读不懂就不写**：这份文件是人也会手改的（团队会往里放 layout）。
        # 解析失败时"盖回去"等于把用户手写的内容删掉 —— 宁可这次记进偏好，
        # 也不能悄悄毁掉项目里那份。
        try:
            raw = fp.read_text(encoding='utf-8-sig')
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                raise ValueError('顶层不是一个对象')
        except Exception as e:                   # noqa: BLE001
            swallow('core.session_store.remember_cards.read', e)
            return {'ok': False, 'path': str(fp),
                    'reason': '项目里这份 storygraph.session.json 读不懂'
                              '（可能手写坏了），这次不覆盖它',
                    'changed': False}
    cards = data.get(CARDS_SECTION)
    if not isinstance(cards, dict):
        cards = {}
    folded_map = cards.get('folded')
    if not isinstance(folded_map, dict):
        folded_map = {}
    before = folded_map.get(rel)
    if isinstance(before, (list, tuple)) and list(before) == names:
        return {'ok': True, 'path': str(fp), 'reason': '', 'changed': False}
    folded_map[rel] = names
    cards['folded'] = folded_map
    data[CARDS_SECTION] = cards
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_suffix(fp.suffix + '.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding='utf-8')
        os.replace(tmp, fp)                      # 原子替换：半截文件不会留在原地
    except Exception as e:                       # noqa: BLE001 - 记不住不影响干活
        swallow('core.session_store.remember_cards', e)
        return {'ok': False, 'path': str(fp),
                'reason': f'{type(e).__name__}: {e}', 'changed': False}
    return {'ok': True, 'path': str(fp), 'reason': '', 'changed': True}


def forget(root=None, path=None) -> dict:
    """
    忘掉一个项目（`root` 为空 = **全忘**：最近列表、上次项目、所有布局）。

    用户级的"清除记忆"入口用它 —— 不给人一个清不掉的记忆。
    """
    if not root:
        return save({'version': VERSION, 'last_project': '', 'auto_open': True,
                     'recent': [], 'projects': {}}, path)
    data = load(path)
    key = _key(root)
    if _key(data.get('last_project') or '') == key:
        data['last_project'] = ''
    data['recent'] = [r for r in (data.get('recent') or []) if _key(r) != key]
    data.get('projects', {}).pop(key, None)
    return save(data, path)


# ===========================================================================
#  布局：与 Qt 无关的纯数据（判据不需要开窗口就能测）
# ===========================================================================

def normalize_geometry(values) -> list:
    """`[宽, 高, x, y]` → 干净的四元组（坏值 → 空列表，**不猜**）。"""
    if not isinstance(values, (list, tuple)) or len(values) < 2:
        return []
    try:
        w, h = int(values[0]), int(values[1])
    except (TypeError, ValueError):
        return []
    if w <= 0 or h <= 0:
        return []
    out = [w, h]
    for raw in list(values[2:4]):
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            out.append(None)
    while len(out) < 4:
        out.append(None)
    return out


def normalize_sizes(values) -> list:
    """分栏尺寸 → 一串正整数（少于 2 个 / 有 <=0 的 → 空列表）。"""
    if not isinstance(values, (list, tuple)) or len(values) < 2:
        return []
    out = []
    for v in values:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return []
        if n <= 0:
            return []
        out.append(n)
    return out


def layout_from(*, page=None, card_index=None, chapter=None, geometry=None,
                split_graph=None, split_card=None, root=None,
                dock_state=None) -> dict:
    """
    界面状态 → **可存的布局**（只放"真的取到了"的字段）。

    `chapter` 同时记**绝对**与**相对项目根**两份：项目整个搬走之后，
    相对那份还能把章节找回来（绝对那份就失效了）。

    `dock_state` 是 `QMainWindow.saveState()` 的 base64（dock 的位置 / 宽度 /
    浮出 / 关掉）。**这里只当字符串存**，不解释它 —— Qt 的格式不归我们解析，
    读的时候 `restoreState` 认不认由它说了算（认不出就当没记）。
    """
    out: dict = {}
    if page is not None:
        try:
            out['page'] = int(page)
        except (TypeError, ValueError) as e:
            # ⚠ 不许 `pass`（`test_no_silent_except` 钉着）：这里"没记下页号"
            #   是一个**会发生**的事实，得有台账可查。
            swallow('core.session_store.layout.page', e)
    if card_index is not None:
        try:
            out['card_index'] = int(card_index)
        except (TypeError, ValueError) as e:
            swallow('core.session_store.layout.card_index', e)
    if chapter:
        abs_ch = str(chapter)
        out['last_chapter'] = abs_ch
        if root:
            try:
                out['last_chapter_rel'] = str(
                    Path(abs_ch).resolve().relative_to(Path(root).resolve())
                ).replace('\\', '/')
            except Exception as e:               # noqa: BLE001 - 不在项目里就算了
                swallow('core.session_store.layout.chapter_rel', e)
    g = normalize_geometry(geometry)
    if g:
        out['geometry'] = g
    sg = normalize_sizes(split_graph)
    if sg:
        out['split_graph'] = sg
    sc = normalize_sizes(split_card)
    if sc:
        out['split_card'] = sc
    if dock_state:
        out['dock_state'] = str(dock_state)
    return out


def resolve_chapter(layout: dict, root=None) -> str:
    """
    布局里记的章节 → **现在还在的那个路径**（找不到就 `''`）。

    顺序：绝对路径（若还在）→ **相对项目根那份**（项目搬过家也能找回来）。
    """
    if not isinstance(layout, dict):
        return ''
    abs_ch = str(layout.get('last_chapter') or '')
    if abs_ch:
        try:
            if Path(abs_ch).is_file():
                return abs_ch
        except Exception as e:                   # noqa: BLE001
            swallow('core.session_store.resolve_chapter.abs', e)
    rel = str(layout.get('last_chapter_rel') or '')
    if rel and root:
        try:
            fp = Path(root) / rel
            if fp.is_file():
                return str(fp)
        except Exception as e:                   # noqa: BLE001
            swallow('core.session_store.resolve_chapter.rel', e)
    return ''


__all__ = ['PROJECT_FILE', 'MAX_RECENT', 'VERSION', 'config_path', 'disabled',
           'set_enabled', 'enabled',
           'load', 'save', 'project_layout', 'last_project', 'recent_projects',
           'remember_project', 'set_auto_open', 'forget',
           'remember_graph_files', 'graph_files',
           'normalize_geometry', 'normalize_sizes', 'layout_from',
           'resolve_chapter']
