"""
**把"工具不认识的写法"直接问引擎**（方案 §三 说的"扫描喂给探针该测什么"）
==========================================================================

用户 2026-10-11 拍板："做实现！"；口径沿用他那张表："以实际资源为准"。

## 为什么需要它（新思路的延伸）

    `discover_undeclared` 扫出"工具不认识的写法"（`weather rain 强度=3`）
        → 报告里列出来（"报出来"已完成）
        → **然后呢？** 人只能凭猜去填声明（参数名、有没有块…）

但**引擎自己知道**：那些写法要么是项目注册过的语句（引擎认识 → 该学它的形状），
要么是**写错了**（引擎会说 `expected statement.`）。所以下一步是：

    **把这一行丢给引擎跑一遍，让它作证。**

接上方案那句"**能用引擎自己拿到的，就不让人填**" ——
"认不认 / 带不带块 / 报什么错"由**引擎**回答，人只负责中文说法。

## 判据是「引擎的报错」，不是任何间接信号（这一段是真机三次实验换来的）

真机实测（`sr_seneral`，SDK 8.5.3）确认的 lint 真实形状：

    File "game/zz_storygraph_ask_bare.rpy", line 3: expected statement.
        sg_unknown_xyz sg_arg1

两条关键性质：

  1. **报错行与源码行是分开两行**，语句头在**下一行** → 判据要"报错行提到
     我们那个探针文件" ⇒ 该文件里的写法**被拒**；
  2. **合法的探针文件在 lint 里一个字都不提**（真机 `pause` 那例实测）
     → "没被提到"就是**被接受**。

⚠ 两个**走不通**的路子（都试过、都记在这里，免得以后重走）：

  · **在 label 里留"我跑到这里了"的凭证**：真机证明 **lint 不执行 label**
    （只执行 `init`），所以凭证永远不产出，"没产出"也就分不出
    "项目里有别人的 parse error"和"我们自己那一行被拒"；
  · **`renpy.game.script.load_string()`**（引擎自己的解析 API）：真机实测它
    **不做词法校验** —— 连 `sg_definitely_unknown_xyz` 这种胡写都"接受"，
    所以它答不了"认不认识"这个问题。

## 三个形态、各自一个文件（真机实测逼出来的分工）

  · `bare`   —— **认识这个语句头吗**。这是**唯一可靠**的信号：
    真机实测 `sg_definitely_unknown_xyz sg_arg1` → `expected statement.`；
    而**同名的内置语句**（`pause 1.0`）一个字都不报 ⇒ "报错 = 不认识"。
  · `block`  —— ⚠ **带 `:` 的形态连胡写的头都会被接受**（实测
    `sg_definitely_unknown_xyz sg_arg1:` + 缩进体 = 解析通过，因为 Ren'Py
    把它当成一个合法的"带块语句"）。所以它**答不了"认不认识"**，
    只能在 `bare` 已被接受时用来确认"能不能带块"。**没确认就报 None，不猜**。
  · `params` —— 同理，只有在 `bare` 被接受时才有意义。

每个形态**单独一个文件**：实测引擎对**一个文件只报第一处** parse error，
挤在一起后两条会被吞掉。

## ⚠ 一个**根本限制**（试过两种绕法，都失败 —— 如实写在这里）

> **项目里只要有引擎不认识的写法，引擎就编译不过，于是也答不了我们任何问题。**

原因很简单：那种写法本身就是 parse error（`expected statement.`），
而引擎**第一处 parse error 就停**。所以"扫描扫到了未知写法"和"引擎能回答"
这两件事**在当前工具形态下不能同时成立**（真机实测：注入未知写法后，
lint 停在那一行、探针根本没被解析）。

试过的两种绕法**都不行**：
  · 把写法放进 `python` 块当字符串 —— 能解析，但**扫描器也扫不到**（没有收益）；
  · 注入一段 `register_statement("weather", …)` 再使用它 —— 仍然
    `expected statement.`（注册没生效或引擎不认那种参数形式）。

**所以这条链路的正确用法是**：

  · **扫描线**（`discover_undeclared`）负责"发现"—— 它不需要引擎，
    自己就能判定"这个头没人认领"；
  · **探针线**（本模块）负责"作证"—— 用来回答**那些引擎能解析的东西**：
    头是认识的、只是工具没建模（此时 `bare` 被接受 = 引擎确实认识它）；
    或者在**干净项目**上确认我们猜的参数形态能不能被接受；
  · 项目**本身就有 parse error** 时，本模块一律返回 `ok=False` 并说明
    **被哪个文件挡住**（`blocked_by`）—— 绝不编一个结论出来。

## 三条硬规矩

  ① **永远在副本上跑**（`mode='copy'` 默认）—— 真机只读，绝不往项目里写东西；
  ② **说不准就说说不准**（`ok=False`），绝不把"没问到"当成"引擎说没问题"；
  ③ 探针删干净（`.rpy` **和 `.rpyc`** —— 规则 N7：孤儿 `.rpyc` 不会被自动清）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 探针文件名前缀（与 E1 的 `zz_storygraph_probe.rpy` **分开**：
#: 那个取引擎事实，这个问语句能否解析 —— 混在一起会互相打架）
ASK_PREFIX = 'zz_storygraph_ask_'
ASK_MARK = '# storygraph 语句探针（工具生成，用完即删；请勿提交）'

#: 默认超时（秒）—— 只跑 lint，比完整采集短
DEFAULT_TIMEOUT = 300

#: **不问语法就不必拷**的重目录（真机实测：整份 copytree 一个 8GB 项目会
#: 把磁盘写满 —— 五个用例就满了 C 盘，这是我踩过的真坑）。
#: 探针只问"这一行**能不能被解析**"，而解析只跟 `.rpy` 文本有关；
#: 图像/音频/字体缺失只会变成语义错误（`is not an image`），不影响 parse 结论。
_COPY_SKIP_DIRS = frozenset({
    'images', 'bgm', 'sounds', 'voices', 'music', 'audio', 'sfx', 'voice',
    'fonts', 'cache', 'saves', 'cc_preview', 'minigamepieces', 'photo',
    'movie', 'videos', 'gui', 'tmp',
})
#: 单个大文件的上限（拷超过它就没意义了：语法在 `.rpy` 里）
_COPY_MAX_BYTES = 4 * 1024 * 1024
#: 只拷这些扩展名（语法/配置都在这些里；图像音频一律不要）
_COPY_KEEP_SUFFIXES = ('.rpy', '.json', '.txt')
#: lint 里"这是 parse error"的标记（用来判"引擎停在哪"）
_PARSE_MARKS = ('expected ', 'syntaxerror', 'parseerror', 'invalid syntax',
                'unexpected token')

#: `test` 报告里的状态行（**这是它的判据**，不是"进程退没退出"）
_RPYTEST_STATUS = re.compile(r'\[rpytest\]\s*Status:\s*(\w+)')

#: `test` 需要显示（`register_command("test", …, uses_display=True)`）
TEST_NEEDS_DISPLAY = True

#: 行为级探针要额外拷的目录（`test` 与 `lint` **要的不是同一批文件**）：
#: 实测撞出来的两处 —— `gui/window_icon.png` 与 `fonts/…/Plix-Regular.otf`。
TEST_EXTRA_DIRS = ('gui', 'fonts')

#: 行为级探针的默认超时（秒）：`test` 跑完**不会自己退出**，所以我们自己收
TEST_TIMEOUT = 180


# ---------------------------------------------------------------------------
#  ① 探针源码与形态（纯函数，可单测）
# ---------------------------------------------------------------------------

def ask_variants(head: str, lines=None) -> list:
    """
    要测的**形态**：`[{'what', 'lines', 'file'}, …]`。

    默认三个，回答三个不同的问题：`bare`（认不认）/ `block`（带不带块）/
    `params`（参数形态对不对）。

    ⚠ 每个形态**一个文件**：实测引擎对**一个文件只报第一处** parse error，
    三条挤在一个文件里，后两条会被吞掉（那样 `block` 的结论就是假的）。
    """
    head = str(head or '').strip()
    if lines is not None:
        return [{'what': 'custom', 'file': 'custom',
                 'lines': [str(x) for x in lines]}]
    return [
        {'what': 'bare', 'file': 'bare', 'lines': [f'{head} sg_arg1']},
        {'what': 'block', 'file': 'block',
         'lines': [f'{head} sg_arg1:', '    pass']},
        {'what': 'params', 'file': 'params',
         'lines': [f'{head} sg_arg1 sg_key=sg_val']},
    ]


def variant_source(head: str, variant: dict) -> str:
    """
    一个形态的探针源码（**纯函数**）。

    · `label _sg_ask_<file>:` —— label 名按形态区分，互不冲突；
    · 待测写法缩进 4 格；
    · 末尾 `return` —— label 必须有终点，否则语义检查会另报一堆。
    """
    name = f"{_slug(head)}_{variant.get('file') or 'x'}"
    out = [ASK_MARK, '', f'label _sg_ask_{name}:']
    for ln in (variant.get('lines') or ()):
        out.append('    ' + str(ln))
    out.append('    return')
    out.append('')
    return '\n'.join(out)


def _slug(head: str) -> str:
    """label 名里的安全片段（`weather-2` → `weather_2`）。"""
    keep = [c if (c.isalnum() or c == '_') else '_' for c in str(head or '')]
    return ''.join(keep) or 'x'


def ask_filename(variant: dict) -> str:
    return f"{ASK_PREFIX}{variant.get('file') or 'x'}.rpy"


# ---------------------------------------------------------------------------
#  ② 从 lint 输出读结论（纯函数）
# ---------------------------------------------------------------------------

def read_verdict(head: str, lint_text: str, *, variants=None,
                 exit_code=None) -> dict:
    """
    lint 输出 → 关于 `head` 的**结论**（纯函数）。

    返回::

        {'ok': bool,                 # 有没有拿到结论（拿不到 = False，绝不猜）
         'reason': str,              # ok=False 时的人话原因
         'recognized': bool | None,  # 引擎认不认这个语句头
         'blocks': bool | None,      # 带块的形态认不认
         'params': bool | None,      # 我们猜的参数形态认不认
         'evidence': [人话…],        # 引擎报的错（**原文**，给人看）
         'by_what': {形态: {'accepted','file','lines','error'}}}

    判据（真机实测的形状，见模块 docstring）：
      · lint 里**提到**某个探针文件 → 那个形态**被拒**；
      · **完全没提到** → **被接受**；
      · 一个文件都没提到、且退出码不是 0/1 → 说不准（不猜）。
    """
    head = str(head or '').strip()
    out = {'ok': False, 'reason': '', 'recognized': None, 'blocks': None,
           'params': None, 'evidence': [], 'by_what': {}}
    if not head:
        out['reason'] = '没有语句头'
        return out
    variants = list(variants or ask_variants(head))
    lines = (lint_text or '').splitlines()
    by_what = {}
    for v in variants:
        what = str(v.get('what') or '')
        fname = ask_filename(v)
        first = str((v.get('lines') or [''])[0]).strip()
        error, rejected = '', False
        for i, raw in enumerate(lines):
            if fname not in raw:
                continue
            # ⚠ 报错行 + **紧随的源码行**（真机实测：语句头在**下一行**）。
            #   紧跟那行必须真的是我们写的那句，否则不算（lint 报告里
            #   文件名可能出现在别的上下文里）。
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ''
            if nxt and first and nxt.startswith(first.split()[0]):
                rejected = True
                error = raw.strip()
                break
        by_what[what] = {'accepted': not rejected, 'file': fname,
                         'lines': v.get('lines') or [], 'error': error}
        if rejected:
            out['evidence'].append(f'{what}（{fname}）: {error}')
    out['by_what'] = by_what
    if not by_what:
        out['reason'] = '一个形态都没测（内部错误）'
        return out
    if exit_code not in (0, 1, None):
        out['reason'] = (f'引擎退出码 {exit_code} —— 这轮 lint 没正常跑完，'
                         f'说不准，不猜')
        return out
    out['ok'] = True
    base = by_what.get('bare') or by_what.get('custom') or {}
    out['recognized'] = bool(base.get('accepted'))
    # ⚠ **只有 `bare` 被接受时**，另外两个形态的"没报错"才能当结论。
    #   否则它们是"根本没测出东西"（带 `:` 的形态连胡写的头都接受 —— 真机实测），
    #   那就必须留 `None`（说不知道），**绝不拿"没报错"冒充"支持"**。
    if out['recognized']:
        if 'block' in by_what:
            out['blocks'] = bool(by_what['block'].get('accepted'))
        if 'params' in by_what:
            out['params'] = bool(by_what['params'].get('accepted'))
    else:
        for what in ('block', 'params'):
            if what in by_what:
                by_what[what]['unconfirmed'] = True
    return out


def summarize(verdict: dict) -> str:
    """结论 → 一句人话（界面/CLI 直接用）。"""
    if not verdict.get('ok'):
        return f'说不准：{verdict.get("reason") or "没拿到结论"}'
    if not verdict.get('recognized'):
        return ('引擎**不认识**这个写法 —— 它既没被注册成语句，'
                '也不是 Ren\'Py 的语法（要么项目里少了一段 '
                '`register_statement`，要么这一行就是写错了）')
    parts = ['引擎认识它']
    if verdict.get('blocks') is False:
        parts.append('但**不带块**（后面不能跟缩进的内容）')
    elif verdict.get('blocks'):
        parts.append('可以带块')
    elif verdict.get('blocks') is None:
        parts.append('带不带块**没测出来**')
    if verdict.get('params') is False:
        parts.append('我们猜的参数形态**被拒**（参数名/写法可能不对）')
    return '；'.join(parts)


# ---------------------------------------------------------------------------
#  ③ 真跑（永远在副本上）
# ---------------------------------------------------------------------------

def _copy_for_ask(root: Path, tmp: Path) -> Path:
    """
    **给探针准备一份轻量副本**（只拷"能解析就行"的东西）。

    为什么不能整份 `copytree`：真机项目 8GB，跑五个待测写法就写满了整个 C 盘
    （实测踩到，属"我的代码把用户机器搞坏了"那一类，必须从设计上杜绝）。

    跳过的都是**与语法无关**的重目录（图像/音频/字体/缓存/存档…）——
    它们缺失只会让 lint 多报几条 `is not an image`（语义错，**不影响 parse**），
    而我们问的正是"能不能解析"。目录清单见 `_COPY_SKIP_DIRS`。

    ⚠ **保留目录结构**（空目录也建）：Ren'Py 对 `game/` 的扫描依赖目录存在。
    """
    work = tmp / root.name
    keep_dirs = []
    for src in sorted(root.rglob('*')):
        rel = src.relative_to(root)
        parts = rel.parts
        if any(p in _COPY_SKIP_DIRS for p in parts[:-1]):
            continue
        if src.is_dir():
            if rel.parts and rel.parts[-1] in _COPY_SKIP_DIRS:
                continue
            keep_dirs.append(rel)
            continue
        if not src.is_file():
            continue
        if src.suffix.lower() not in _COPY_KEEP_SUFFIXES:
            continue
        try:
            if src.stat().st_size > _COPY_MAX_BYTES:
                continue
        except OSError:
            continue
        dest = work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    for rel in keep_dirs:                     # 空目录也建出来（结构别缺）
        (work / rel).mkdir(parents=True, exist_ok=True)
    (work / 'game').mkdir(parents=True, exist_ok=True)
    return work


def ask_many(project_root, heads, *, sdk=None, timeout=DEFAULT_TIMEOUT,
             limit: int = 12) -> dict:
    """
    **问一批头** → `{head: verdict}`。

    ## ⚠ 为什么**一个头一次 lint**（我第一版想当然地"一次问完一批"，被真机打回）

    我原以为"把每个头各写进一个自己的文件、跑一次 lint"就能全问出来 ——
    **错**，因为 **引擎报第一处 parse error 就停**。真机实测（注入 `weather`
    之后跑批量）：

        File "game/zz_demo_custom_actions.rpy", line 2: expected statement.
            weather rain 强度=3

    引擎在**用户自己的文件**上就停了，**根本没走到探针** —— 而我的判据把
    "lint 没提到探针"当成"接受"，于是**把不认识的头判成了认识**（假绿）。
    所以每个头必须**单独跑**（问完一个就把探针删掉）。

    ⚠ 由此还有一条**必须如实说**的限制：**项目自己就有 parse error 时，
    我们问不出结论**（引擎提前中止）。这时返回 `ok=False` 并说明"被谁挡住了"，
    **绝不说"引擎说没问题"**（方案坑 2/坑 4 同源）。

    返回 `{'ok', 'reason', 'results': {head: verdict}, 'lints': {head: 片段},
    'exit': 最后一次退出码, 'asked', 'skipped', 'blocked_by'}`。
    """
    heads = [str(h or '').strip() for h in (heads or ())]
    ordered, seen = [], set()
    for h in heads:
        if h and h not in seen:
            seen.add(h)
            ordered.append(h)
    asked, skipped = ordered[:max(0, int(limit))], ordered[max(0, int(limit)):]
    out = {'ok': False, 'reason': '', 'results': {}, 'lints': {},
           'exit': None, 'asked': asked, 'skipped': skipped,
           'blocked_by': ''}
    root = Path(project_root)
    if not root.is_dir() or not (root / 'game').is_dir():
        out['reason'] = f'不是工程根（没有 game/）：{root}'
        return out
    if not asked:
        out['reason'] = '没有要问的写法'
        return out
    from storygraph.pipeline.engine_collect import find_sdk, run_engine
    try:
        sdk_path = find_sdk(sdk)
    except FileNotFoundError as e:
        out['reason'] = str(e)
        return out

    tmp = Path(tempfile.mkdtemp(prefix='sg_askmany_copy_'))
    try:
        work = _copy_for_ask(root, tmp)
        # 先看这个项目**自己**干不干净：它自己有 parse error 时引擎会提前停下，
        # 我们问不到任何结论（但要如实说出"被谁挡住"）。
        base_rc, base_text = _lint(work, sdk_path, timeout)
        blocked = _matching_parse_error(base_text, ASK_PREFIX)
        if blocked:
            out['blocked_by'] = blocked
            out['reason'] = (f'项目的 {blocked} 里就有 parse error —— 引擎会在'
                             f'第一处停下，我们问不到关于语句头的结论（采不到'
                             f'就不下结论）')
            out['exit'] = base_rc
            return out
        target = work / 'game' / ask_filename(_bare_variant(asked[0]))
        for head in asked:
            variant = _bare_variant(head)
            _write_ask(target, variant_source(head, variant))
            try:
                rc, text = _lint(work, sdk_path, timeout)
                out['exit'] = rc
                out['lints'][head] = text[-4000:]
                out['results'][head] = read_verdict(
                    head, text, variants=[variant], exit_code=rc)
            finally:
                _drop_probe(target)
        out['ok'] = True
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _lint(work: Path, sdk_path, timeout):
    """在这个副本上跑一次 lint（**errors.txt 先挪开** —— 见 `ask_engine` 的说明）。"""
    from storygraph.pipeline.engine_collect import run_engine
    errors_txt = work / 'errors.txt'
    parked = None
    if errors_txt.exists():
        try:
            parked = work / (errors_txt.name + '.sg-ask-parked')
            errors_txt.replace(parked)
        except OSError as e:
            swallow('pipeline.statement_probe.lint.park_errors', e)
            parked = None
    try:
        rc, text = run_engine(sdk_path, work, ['lint', '--error-code'],
                              timeout=timeout)
    finally:
        try:
            if errors_txt.exists():
                errors_txt.unlink()
            if parked is not None:
                parked.replace(errors_txt)
        except OSError as e:
            swallow('pipeline.statement_probe.lint.restore_errors', e)
    return rc, text


def _matching_parse_error(lint_text: str, prefix: str) -> str:
    """
    lint 文本里**第一处 parse error 所在的文件**（没有就返回 `''`）。

    用来回答"我们问得到吗"：如果第一处 parse error **不是**我们的探针文件，
    说明引擎在到达探针之前就停了 —— 那这一轮什么结论都没有。
    """
    lines = (lint_text or '').splitlines()
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s.startswith('File "') or 'line' not in s:
            continue
        if not any(m in s.lower() for m in _PARSE_MARKS):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ''
        if nxt:                                  # 报错行后面紧跟源码行
            here = prefix in s
            return '' if here else s[:200]
    return ''


def _drop_probe(target: Path) -> None:
    """
    删掉探针 —— **`.rpy` 和它的 `.rpyc` 都要删**（规则 N7：孤儿 `.rpyc`
    不会被引擎自动清理，会一直留在 `game/` 里）。
    """
    target = Path(target)
    stem = target.name[:-len('.rpy')] if target.name.endswith('.rpy') else target.name
    for p in list(target.parent.glob(stem + '.rpy*')):
        try:
            p.unlink()
        except OSError as e:
            swallow('pipeline.statement_probe.drop_probe', e)


#: `lint`/`test` 输出里"引擎说这一行不认识"的形状：
#: `File "game/x.rpy", line 12: expected statement.`
_PARSE_LOC = re.compile(
    r'File\s+"([^"]+)"\s*,\s*line\s+(\d+)\s*:\s*(expected\s+statement|'
    r'invalid\s+syntax|syntax\s+error)', re.IGNORECASE)


def first_parse_error(text: str) -> str:
    """
    从引擎输出里抽出**第一处**"这一行它不认识"的位置（纯函数，抽不到 → `''`）。

    为什么要它（用户 2026-10-11："回到 UI"）：验证没通过时只显示"没证明成"，
    人**不知道该去改哪儿**。引擎其实说得清清楚楚 ——
    `File "game/hell_天气.rpy", line 12: expected statement.` —— 只是被埋在
    几十行输出里。这里把它抬到结论最上面。
    """
    m = _PARSE_LOC.search(str(text or ''))
    if not m:
        return ''
    path, line, what = m.group(1), m.group(2), m.group(3).strip()
    return f'{path}:{line}（{what}）'


def behavior_probe_source(label: str, *, evidence: str = '',
                          testcase: str = '') -> str:
    """
    **第三层探针**：一个能证明"这段真的执行了"的 `testcase`（纯函数）。

    ## 真机结论：哪一种是**能用的**（2026-10-11，两种都实测过）

    | 写法 | 结果 |
    |---|---|
    | `assert label X timeout T` + `run Jump(X)` | ❌ **不成立**：它等的是"**当前停在该 label**"，而我们跳过去秒回、就错过去了（①暂停 ②死循环两种"停住"办法也都没让证据落地 —— jump 似乎根本没把它跑起来） |
    | **`assert eval "外部证据存在" timeout T`** + `run Jump(X)` | ✅ **成立**：证据文件真的被写、`Status: PASSED`、断言 `1 passed` |

    ⚠ 所以产品用**证据轮询**这一种：断言里问的是"**那句话的副作用发生了吗**"
    （写文件 / 改标记），而不是"引擎当前在哪"。
    ⚠ 但它有反例边界的风险（`assert eval` 在**条件恒真**时也会 PASSED），
    所以**证据必须是"只有真的执行了才会出现"的东西**，并且产品侧还要
    **自己检查证据**（`behavior_verdict` 只信 Status，证据由调用方复核）。

    ## 顺序

        testcase <testcase>:
            assert eval "<证据存在>" timeout 30   ← **先挂上等待**
            run Jump("<label>")                   ← **再跳**

    `assert` 是"等到条件成立"，必须先挂、再触发（反过来就是错过那一刻）。
    """
    label = str(label or '').strip() or 'sg_behavior_target'
    case = str(testcase or '').strip() or 'sg_behavior_check'
    cond = str(evidence or '').strip() or 'True'
    # ⚠⚠ **Windows 路径的反斜杠会毁掉 `assert eval` 里的字符串**（真机实测）：
    #   `r'C:\Users\…'` 那种写法让 eval 失败/条件永不成立 ——
    #   表现是"断言计数 0、用例 0.007 秒就结束"，看起来像"语句没执行"。
    #   统一转成前向斜杠（Ren'Py 自己就认这种）之后一切正常。
    cond = cond.replace('\\', '/')
    path = _evidence_path(cond).replace('\\', '/')
    body = [ASK_MARK, '',
            'init python:',
            '    import os',
            '',
            f'label {label}:',
            '    $ ' + cond,
            '    return',
            '',
            '',
            f'testcase {case}:',
            f'    assert eval "os.path.exists(r\'{path}\')" timeout 30',
            f'    run Jump("{label}")', '']
    return '\n'.join(body)


def _evidence_path(evidence: str) -> str:
    """
    从证据语句里抽出**要检查的那个文件路径**（`open(r"…","w")` 里的那一段）。

    抽不出来 → 返回 `''`，调用方据此刻意生成一个**永远不成立**的条件
    （宁可判"没验证成功"，也不要造一个恒真的条件来假装通过）。
    """
    m = re.search(r'open\(\s*r?[\'"]([^\'"]+)[\'"]', str(evidence or ''))
    return m.group(1) if m else ''


def read_test_status(text: str) -> dict:
    """
    `test` 的输出 → 结论（纯函数）。

    返回 `{'status': 'PASSED'|'FAILED'|'', 'cases': int, 'assertions': int,
           'failed': int, 'reason': str}`。

    ⚠⚠ **读不到 `Status:` 时绝不当成通过**（我第一版差点这样）：
    夹具自身出错时进程会崩、什么都不打印。`status == ''` 就是"没测出来"。
    """
    text = str(text or '')
    status, cases, assertions, failed = '', 0, 0, 0
    m = _RPYTEST_STATUS.search(text)
    if m:
        status = m.group(1).upper()
    m = re.search(r'\[rpytest\]\s*Test cases\s*:\s*\d+\s*\|\s*(\d+) passed'
                  r'\s*\|\s*\d+ xfailed\s*\|\s*(\d+) failed', text)
    if m:
        cases, failed = int(m.group(1)), int(m.group(2))
    m = re.search(r'\[rpytest\]\s*Assertions\s*:\s*\d+\s*\|\s*(\d+) passed'
                  r'\s*\|\s*\d+ xfailed\s*\|\s*(\d+) failed', text)
    if m:
        assertions = int(m.group(1))
    reason = ''
    if not status:
        reason = ('没读到 `[rpytest] Status:` —— 夹具可能自己崩了。'
                  '**没读到 ≠ 通过**（第四层：如实报"测不出来"）')
    elif status == 'FAILED':
        m = re.search(r'(renpy\.test\.types\.\w+:.*)', text)
        reason = m.group(1)[:300] if m else '有断言/用例失败'
    return {'status': status, 'cases': cases, 'assertions': assertions,
            'failed': failed, 'reason': reason}


def behavior_verdict(status: dict, *, evidence_path='') -> dict:
    """
    `read_test_status` 的结论 + **证据复核** → 判定（纯函数）。

    ## ⚠⚠ 为什么必须复核证据（真机实测，这是第三层最阴的假绿）

    我实测到 `assert eval "os.path.exists(…)"` 在**条件不成立**时也出现过
    `Status: PASSED` + `Assertions: 1 passed`，而**证据文件根本没被写**
    （同一套探针在反例上跑出来的）。⇒ **光看 `Status` 会判出假绿。**

    所以判定规则是"两把锁"：

      1. `Status` 必须是 `PASSED`（引擎自己说的）；
      2. **给了 `evidence_path` 时，那个文件必须真的存在**（铁证复核）。
         两条都满足才 `verified=True`；`Status=PASSED` 但证据不在 →
         **`verified=False`**（如实报"没证明成"）。

    ⚠ **没给证据路径 → 不给"通过"**（真机踩过）：抽不出证据时如果退回"只信
    `Status`"，就会出现"反例也报 verified=True"的**产品级假绿**。
    所以这一档一律 `verified=None`（"没证明成"）+ 说明要什么才算。
    """
    st = str((status or {}).get('status') or '').upper()
    out = {'ok': False, 'reason': '', 'verified': None, 'summary': ''}
    ev = str(evidence_path or '')
    if not ev:
        out.update({'ok': True, 'verified': None,
                    'reason': '没有可观察的证据（需要"只有真的执行了才会出现"的东西，'
                              '例如写一个文件）',
                    'summary': ('行为级**没证明成**：没有可观察的证据 —— '
                                '⚠ 只凭 `Status` 不足以判通过（真机实测：'
                                '条件不成立时它也可能报 PASSED）')})
        return out
    try:
        exists = Path(ev).is_file()
    except OSError as e:                        # noqa: BLE001 - 判不了就当不存在
        swallow('pipeline.statement_probe.behavior_verdict.evidence', e)
        exists = False
    if not exists:
        out.update({'ok': True, 'verified': False,
                    'reason': f'证据文件不存在：{ev}',
                    'summary': ('行为级**没证明成**：证据文件不存在'
                                '（说明那句没真的执行）—— '
                                '⚠ 即使 test 报 PASSED 也不能当通过')})
        return out
    if st == 'PASSED':
        out.update({'ok': True, 'verified': True,
                    'summary': '行为级验证通过（引擎报 PASSED，且证据文件确实存在）'})
    elif st == 'FAILED':
        out.update({'ok': True, 'verified': False,
                    'reason': str((status or {}).get('reason') or ''),
                    'summary': '行为级验证**失败**：没执行到预期的地方'})
    else:
        out['reason'] = str((status or {}).get('reason')
                            or '没拿到 `[rpytest] Status:`')
        out['summary'] = f'行为级**测不出来**：{out["reason"]}'
    return out


def verify_behavior(project_root, label: str, *, evidence: str = '',
                    sdk=None, timeout: int = TEST_TIMEOUT,
                    evidence_path: str = '', mode: str = 'copy') -> dict:
    """
    **第三层的入口**：真跑一次 `test`，回答"那句到底有没有执行"。

    这是给"教它"那条链用的：声明完一个动作之后，可以**当场验证**它真的能跑，
    而不是只写一份文件就当学会了。

    ## 与 `ask_engine` 的分工

      · `ask_engine` 问"**引擎认不认**这个写法"（解析层，跑 `lint`）；
      · `verify_behavior` 问"**它真的执行了吗**"（行为层，跑 `test`）。

    ## 真机验证过的跑法（照抄，别改）

      ① **永远在副本上跑**（`mode='copy'`）—— 而且要额外拷 `TEST_EXTRA_DIRS`
         （`gui/` / `fonts/`），否则 `test` 起界面就崩；
      ② `test` 需要**显示**（`TEST_NEEDS_DISPLAY`）—— 无头会真卡住，
         所以拿不到就不假装能跑；
      ③ `test` **跑完不自己退出** → **边读输出边看 `[rpytest] Status:`**，
         见到就把进程收掉（拿 `subprocess.run(timeout=)` 等会误判成"卡住"）；
      ④ 判定是**两把锁**（见 `behavior_verdict`）：引擎报 PASSED **且**
         证据文件真的存在。

    返回 `behavior_verdict(...)` 的结论，外加
    `{'status', 'raw_tail', 'probe', 'exit': None, 'evidence': 路径}`。
    """
    root = Path(project_root) if project_root else None
    empty = {'ok': False, 'reason': '', 'verified': None, 'summary': '',
             'status': '', 'raw_tail': [], 'probe': '', 'evidence': ''}
    if root is None or not (root / 'game').is_dir():
        empty['reason'] = f'不是工程根（没有 game/）：{root}'
        empty['summary'] = f'行为级**测不出来**：{empty["reason"]}'
        return empty
    from storygraph.pipeline.engine_collect import find_sdk
    try:
        sdk_path = find_sdk(sdk)
    except FileNotFoundError as e:
        empty['reason'] = str(e)
        empty['summary'] = f'行为级**测不出来**：{e}'
        return empty

    src = behavior_probe_source(label, evidence=evidence,
                                testcase='sg_behavior_check')
    empty['probe'] = src
    if not evidence_path:
        evidence_path = _evidence_path(str(evidence or '').replace('\\', '/'))

    tmp = None
    work = root
    if mode == 'copy':
        tmp = Path(tempfile.mkdtemp(prefix='sg_behavior_copy_'))
        work = behavior_copy(root, tmp)          # ← 必须带 gui/fonts
    try:
        _write_ask(work / 'game' / f'{ASK_PREFIX}behavior.rpy', src)
        out, tail = _run_test_capture(work, sdk_path, 'sg_behavior_check',
                                      timeout)
        status = read_test_status(out)
        verdict = behavior_verdict(status, evidence_path=evidence_path)
        verdict.update({'status': status.get('status') or '',
                        'raw_tail': tail, 'probe': src,
                        'evidence': evidence_path})
        return verdict
    except Exception as e:                       # noqa: BLE001 - 跑不起来就如实说
        swallow('pipeline.statement_probe.verify_behavior', e)
        empty['reason'] = f'{type(e).__name__}: {e}'
        empty['summary'] = f'行为级**测不出来**：{empty["reason"]}'
        return empty
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _run_test_capture(work: Path, sdk_path, case: str, timeout: int):
    """
    跑 `test` 并**在见到 `[rpytest] Status:` 时收手**（它自己不会退出）。

    返回 `(输出全文, 末尾若干行)`。⚠ 超时也必须返回**已经读到的**输出 ——
    那里面往往带着"为什么没跑完"。
    """
    from storygraph.pipeline.engine_collect import run_engine
    # ⚠ 不能用 `run_engine`（它等进程结束）；`test` 跑完不退，得自己边读边收。
    import subprocess
    from storygraph.pipeline.engine_collect import _engine_python
    cmd = [str(_engine_python(Path(sdk_path))),
           str(Path(sdk_path) / 'renpy.py'), str(work), 'test', case]
    env = dict(os.environ)
    env.setdefault('SDL_VIDEODRIVER', os.environ.get('SDL_VIDEODRIVER', ''))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding='utf-8', errors='replace')
    buf, started = [], _now()
    try:
        for line in p.stdout:
            buf.append(line)
            if _RPYTEST_STATUS.search(line):
                break
            if _now() - started > timeout:
                break
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    text = ''.join(buf)
    tail = [ln.rstrip('\n') for ln in buf if ln.strip()][-25:]
    return text, tail


def _now() -> float:
    import time
    return time.time()


def record_behavior(facts: dict, name: str, verdict: dict) -> dict:
    """
    把一次行为验证的结论**记进事实**（就地改，返回 `facts`）。纯函数。

    为什么要有它：`verify_behavior` 只是**跑一次**；报告要显示"验过几个"，
    就必须有人把结论**落进 `facts['behavior']`**（采集/验证方调它）。
    形状见 `core.engine_facts.behavior_facts`。

    ⚠ `verified` 三态**原样保留**（`True`/`False`/`None`）——
    `None`（没证明成）绝不折成 `False`。
    """
    if not isinstance(facts, dict) or not name:
        return facts
    # ⚠ 两个形状细节都是判据抓出来的：
    #   ① 要走 **`facts['facts'][key]`** 这一层（`value_of` 读的是它）；
    #   ② 每条要写成 `{'value': …, 'source': …}`。
    #   我第一版写成"顶层 + 裸表"，读取端一个都读不到。
    container = facts.get('facts')
    if not isinstance(container, dict):
        container = {}
        facts['facts'] = container
    entry = container.get('behavior')
    if not isinstance(entry, dict) or 'value' not in entry:
        entry = {'value': {}, 'source': 'probe'}
        container['behavior'] = entry
    table = entry.get('value')
    if not isinstance(table, dict):
        table = {}
        entry['value'] = table
    v = (verdict or {}).get('verified')
    table[str(name)] = {'verified': None if v is None else bool(v),
                        'why': str((verdict or {}).get('summary')
                                   or (verdict or {}).get('reason') or '')[:300]}
    return facts


def verify_head(project_root, head: str, *, sdk=None,
                timeout: int = TEST_TIMEOUT) -> dict:
    """
    **"这个动作到底有没有被执行过"** —— 给界面按钮用的入口（第三层落地）。

    做法（全部真机验证过）：

      1. 找出**第一条用到这个写法的行**在哪个 label 里
         （`find_head_label`；找不到 → 如实说"没找到，验不了"）；
      2. 在那个 label **末尾注入一句证据**（`record()` 写一个文件），
         并造一个跳进该 label 的行为探针；
      3. 跑 `verify_behavior` → 两把锁判定。

    ⚠ **注意它证的是什么**：证的是"**这个 label 被执行到了**"，
    而"那一句动作本身执行成功"由引擎在跑的时候自己保证 ——
    如果那一句会崩，`test` 会红（`Status: FAILED`）。
    所以结论 = "跑到过这一处 + 跑的时候没崩"。
    """
    out = {'ok': False, 'reason': '', 'verified': None, 'summary': '',
           'label': '', 'status': '', 'evidence': '', 'raw_tail': []}
    root = Path(project_root) if project_root else None
    if root is None or not (root / 'game').is_dir():
        out['reason'] = f'不是工程根（没有 game/）：{root}'
        out['summary'] = f'行为级**测不出来**：{out["reason"]}'
        return out
    label = find_head_label(root, head)
    if not label:
        out['reason'] = (f'没找到用到 "{head}" 的 label —— 验不了'
                         f'（先把这一句写进某一章，或者手工指定一个 label）')
        out['summary'] = f'行为级**测不出来**：{out["reason"]}'
        return out
    out['label'] = label
    tmp = Path(tempfile.mkdtemp(prefix='sg_verify_head_'))
    ev = tmp / 'evidence.txt'
    ev_posix = str(ev).replace('\\', '/')
    work = behavior_copy(root, tmp / 'work')     # ← 必须带 gui/fonts（否则 test 崩）
    try:
        # 把证据注入到那个 label 的末尾（**副本里**）
        target = _inject_evidence(work, label, ev_posix)
        if not target:
            out['reason'] = f'找不到 label "{label}" 所在文件，没法注入证据'
            out['summary'] = f'行为级**测不出来**：{out["reason"]}'
            return out
        out['evidence'] = ev_posix
        v = verify_behavior(work, label,
                            evidence=f'open(r"{ev_posix}", "w").write("hit")',
                            sdk=sdk, timeout=timeout, evidence_path=str(ev),
                            mode='in-place')
        v['label'] = label
        return v
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def find_head_label(project_root, head: str, *, limit_files: int = 400) -> str:
    """
    第一条用到 `head` 的行落在**哪个 label** 里（找不到 → `''`）。纯读。

    ⚠ 只用"最近见过的 `label X:`"来判断归属 —— 缩进不管（Ren'Py 的 label 都在
    顶层）。宁可保守：找不到就返回空，让调用方如实说"验不了"。
    """
    head = str(head or '').strip()
    root = Path(project_root) if project_root else None
    if not head or root is None or not (root / 'game').is_dir():
        return ''
    label_re = re.compile(r'^label\s+([A-Za-z_][\w.]*)\s*(?:\(.*\))?\s*:')
    head_re = re.compile(r'^\s*' + re.escape(head) + r'(?:\s|$)')
    files = sorted(root.rglob('*.rpy'))[:limit_files]
    for fp in files:
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except OSError as e:                     # noqa: BLE001
            swallow('pipeline.statement_probe.find_head_label', e)
            continue
        current = ''
        for raw in text.splitlines():
            m = label_re.match(raw)
            if m:
                current = m.group(1)
                continue
            if current and head_re.match(raw):
                return current
    return ''


def _inject_evidence(work: Path, label: str, evidence_path: str) -> str:
    """
    在副本里把一句证据**追加到 `label` 的末尾**（返回被改的文件；失败 → `''`）。

    ⚠ 只改**副本**（调用方保证）。注入方式是"在该 label 的块尾加一行 `$ …`"：
    找到 label 行，然后往下走到**下一个顶层语句**，在那之前插入。
    """
    label_re = re.compile(r'^label\s+' + re.escape(label) + r'\s*(?:\(.*\))?\s*:')
    line = f'    $ open(r"{evidence_path}", "w").write("hit")'
    for fp in sorted(work.rglob('*.rpy')):
        try:
            lines = fp.read_text(encoding='utf-8',
                                 errors='replace').splitlines()
        except OSError as e:                     # noqa: BLE001
            swallow('pipeline.statement_probe.inject_evidence.read', e)
            continue
        start = next((i for i, ln in enumerate(lines) if label_re.match(ln)),
                     None)
        if start is None:
            continue
        # 走到下一个**顶层**行（非空、非缩进）之前
        end = len(lines)
        for i in range(start + 1, len(lines)):
            s = lines[i]
            if s.strip() and not s.startswith((' ', '\t')):
                end = i
                break
        else:
            end = len(lines)
        lines[end:end] = ['', line]
        try:
            fp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            return str(fp)
        except OSError as e:                     # noqa: BLE001
            swallow('pipeline.statement_probe.inject_evidence.write', e)
            return ''
    return ''


def behavior_copy(root: Path, tmp: Path) -> Path:
    """
    **行为级专用**的副本：轻量副本 **+ `TEST_EXTRA_DIRS`**（`gui/` / `fonts/`）。

    ⚠ 为什么必须单独一个函数（真机踩到的缺陷）：`lint` 只要 `.rpy` 文本，
    而 `test` **要真的把游戏跑起来** —— 少了 `gui/` 会在起界面时崩：
    `FileNotFoundError: Couldn't find file 'gui/window_icon.png'`。
    我第一版给 `verify_behavior` 拷了、**忘了给 `verify_head` 拷**，
    于是"点按钮验证"那条路永远报"没证明成"。抽成一个函数就不会再漏。
    """
    work = _copy_for_ask(root, tmp)
    for extra in TEST_EXTRA_DIRS:
        s = root / 'game' / extra
        if s.is_dir():
            try:
                shutil.copytree(s, work / 'game' / extra, dirs_exist_ok=True)
            except OSError as e:                 # noqa: BLE001
                swallow('pipeline.statement_probe.behavior_copy.extra', e)
    return work


def _bare_variant(head: str) -> dict:
    """批量场景用哪个形态：`bare`（"认不认识"唯一可靠的信号）。"""
    return ask_variants(head)[0]


def ask_engine(project_root, head: str, *, sdk=None, lines=None,
               timeout=DEFAULT_TIMEOUT, mode='copy') -> dict:
    """
    把 `head` 丢给引擎跑一遍 lint，回答"认不认识 / 带不带块 / 参数对不对"。

    `mode='copy'`（默认）**整份拷到临时目录再跑** —— 真项目一个字节都不动。
    `mode='in-place'` 只给"已经确定是副本"的调用方用。

    返回 `read_verdict(...)` 的结论，外加
    `{'summary': 人话, 'probes': {形态: 源码}, 'lint': lint 全文, 'exit': 码}`。
    """
    empty = {'ok': False, 'reason': '', 'recognized': None, 'evidence': [],
             'blocks': None, 'params': None, 'by_what': {}, 'summary': '',
             'probes': {}, 'lint': '', 'exit': None}
    root = Path(project_root)
    if not (root / 'game').is_dir():
        empty['reason'] = f'不是工程根（没有 game/）：{root}'
        empty['summary'] = summarize(empty)
        return empty
    from storygraph.pipeline.engine_collect import find_sdk, run_engine
    try:
        sdk_path = find_sdk(sdk)
    except FileNotFoundError as e:
        empty['reason'] = str(e)
        empty['summary'] = summarize(empty)
        return empty

    variants = ask_variants(head, lines=lines)
    probes = {str(v.get('what')): variant_source(head, v) for v in variants}

    tmp = None
    work = root
    if mode == 'copy':
        tmp = Path(tempfile.mkdtemp(prefix='sg_ask_copy_'))
        work = _copy_for_ask(root, tmp)
    try:
        for v in variants:
            _write_ask(work / 'game' / ask_filename(v),
                       probes[str(v.get('what'))])
        # ⚠ **上一轮留下的 `errors.txt` 会让本轮引擎直接中止**（真机实测：
        # 副本里带着上一次的 errors.txt，四条用例全部"探针没产出"，
        # 看起来像判据坏了）。与 E1 的 `_collect_at` 同一做法：
        # **挪开 → 跑 → 读完 → 还原**，绝不删用户的文件。
        errors_txt = work / 'errors.txt'
        parked = None
        if errors_txt.exists():
            try:
                parked = work / (errors_txt.name + '.sg-ask-parked')
                errors_txt.replace(parked)
            except OSError as e:
                swallow('pipeline.statement_probe.park_errors', e)
                parked = None
        try:
            rc, text = run_engine(sdk_path, work, ['lint', '--error-code'],
                                  timeout=timeout)
        finally:
            try:
                if errors_txt.exists():
                    errors_txt.unlink()
                if parked is not None:
                    parked.replace(errors_txt)
            except OSError as e:
                swallow('pipeline.statement_probe.restore_errors', e)
        verdict = read_verdict(head, text, variants=variants, exit_code=rc)
        verdict.update({'probes': probes, 'lint': text[:20000], 'exit': rc})
        verdict['summary'] = summarize(verdict)
        return verdict
    except Exception as e:                       # noqa: BLE001 - 问不到就如实说
        swallow('pipeline.statement_probe.ask_engine', e)
        empty['reason'] = f'{type(e).__name__}: {e}'
        empty['probes'] = probes
        empty['summary'] = summarize(empty)
        return empty
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _write_ask(target: Path, src: str) -> None:
    """
    写探针 —— ⚠ **绝不覆盖别人的文件**（与 E1 的 `_write_probe` 同一规矩：
    同名文件存在且首行不是我们的标记 → 拒绝）。因为我们永远写在副本里，
    正常情况走不到那一支，但规矩不能少。
    """
    target = Path(target)
    if target.exists():
        head = target.read_text(encoding='utf-8', errors='replace')[:400]
        if ASK_MARK not in head:
            raise FileExistsError(f'同名文件已存在且不是本工具写的，拒绝覆盖：{target}')
    target.write_text(src, encoding='utf-8')


__all__ = ['ASK_MARK', 'ASK_PREFIX', 'TEST_EXTRA_DIRS', 'TEST_NEEDS_DISPLAY',
           'TEST_TIMEOUT', 'ask_engine', 'ask_filename', 'ask_many',
           'ask_variants', 'behavior_probe_source', 'behavior_verdict',
           'read_test_status', 'read_verdict', 'record_behavior', 'summarize',
           'variant_source', 'verify_behavior', 'verify_head',
           'behavior_copy', 'find_head_label', 'first_parse_error']
