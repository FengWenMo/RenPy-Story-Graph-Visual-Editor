"""
`storygraph.pipeline.engine_collect`
====================================
**引擎事实采集**：一次运行，把引擎自己知道的东西拿下来。

它跑什么（**一次**运行，三份产出）
----------------------------------
    Ren'Py SDK  →  <project>  lint --error-code --json-dump <f>
                    ↑ 顺带执行 init（实测：lint 期间 init 会跑）

      ① `lint` 报告      → 语法错误 / 引用错误 / 资源缺失 / **程序自己的 lint 回调输出**
      ② `--json-dump`    → 静态清单（transform / screen / define）
      ③ 注入探针         → 运行期事实（图像、属性与**分组**、兼容候选、label）

为什么探针**由工具生成**、而不是让人在项目里写死一个
----------------------------------------------------
手改 `renpy/__init__.py` 塞自画像代码，**引擎一升级就丢**；
项目里放一个固定探针文件，又要人记得删、记得别提交。
所以：**工具临时生成 → 注入 → 跑 → 连 `.rpyc` 一起删**（N7：实测孤儿 `.rpyc` 引擎不会自动清）。

两条硬规矩
----------
· **采不到 ≠ 没有变化**（坑 4）：`parse_clean` / `probe_complete` 为假 → 报失败，不产出"可用事实"。
· **不许污染项目**：采集前后对 `game/**` 的文本文件做 sha1 清单比对，除 `*.rpyc` 外必须一致。

⚠ 关于"为什么默认在**原项目**上跑，而不是先拷副本"：
真机项目 8GB（大部分是图片），拷不动；而我们要写的东西只有**一个探针文件**，
删掉之后由 sha1 清单证明"别的都没变"。小项目可以用 `mode='copy'`（测试走这条）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from storygraph.core import engine_facts as EF
from storygraph.core.safeguard import swallow

#: 注入的探针文件名（固定前缀，便于识别与清理）。
PROBE_FILENAME = 'zz_storygraph_probe.rpy'
#: 探针首行标记 —— 用来判"这个同名文件是不是我们写的"（**绝不覆盖别人的文件**）。
PROBE_MARK = '# storygraph 临时探针（工具生成，用完即删；请勿提交）'
#: 探针把结果写到这里（**项目之外**，由采集器指定）。
FACTS_ENV = 'STORYGRAPH_ENGINE_FACTS_OUT'
#: SDK 位置（换机器时指过来）。
SDK_ENV = 'STORYGRAPH_RENPY_SDK'

DEFAULT_SDK_CANDIDATES = (
    r'D:/software/renpy-8.4.1-sdk',
    r'C:/renpy',
)

DEFAULT_TIMEOUT = 600

#: 采集时**跳过**的目录（引擎自己的运行产物，不算"工程内容"）。
_SKIP_DIRS = {'cache', 'saves'}
#: 清单只覆盖**文本**文件 —— 理由见 `_sha1_manifest`。
_MANIFEST_SUFFIXES = ('.rpy', '.json', '.txt', '.rpyb')


# ---------------------------------------------------------------------------
#  探针源码（工具生成，绝不落进用户项目）
# ---------------------------------------------------------------------------

def probe_source() -> str:
    """
    探针 `.rpy` 的源码。

    设计要点（都被地狱测试验证过）：
      · `init 999 python:` —— 实测**在 lint 期间会执行**，且 999 是合法上限；
      · 每件事都单独 try —— 一样取不到不影响别的（`probe_complete` 只看文件有没有产出）；
      · 写到**环境变量指定的路径**（项目之外），项目里不留任何东西；
      · 只读：不注册语句、不改配置。
    """
    return '''%(mark)s
init 999 python:
    import json, os

    def _sg_engine_facts():
        out = {}

        def put(key, fn):
            try:
                out[key] = fn()
            except Exception as e:
                out[key + '_error'] = repr(e)

        put('images', lambda: sorted(renpy.list_images()))
        put('tags', lambda: sorted(renpy.get_available_image_tags()))
        put('labels', lambda: sorted(renpy.get_all_labels()))
        put('statements_registry_type',
            lambda: type(renpy.statements.registry).__name__)
        put('statements_registry_count',
            lambda: len(renpy.statements.registry)
            if isinstance(renpy.statements.registry, dict) else None)

        def _attrs():
            table = {}
            for tag in (out.get('tags') or []):
                rows = []
                img = renpy.get_registered_image(tag)
                for a in (getattr(img, 'attributes', None) or []):
                    rows.append({'group': str(getattr(a, 'group', '') or ''),
                                 'attribute': str(getattr(a, 'attribute', '') or ''),
                                 'type': type(a).__name__})
                table[str(tag)] = rows
            return table

        def _cands():
            table = {}
            for tag in (out.get('tags') or []):
                try:
                    table[str(tag)] = [str(x) for x in
                                       renpy.get_ordered_image_attributes(tag)]
                except Exception as e:
                    table[str(tag)] = []
                    out.setdefault('candidates_error', {})[str(tag)] = repr(e)
            return table

        put('attributes', _attrs)
        put('candidates', _cands)
        return out

    try:
        _p = os.environ.get('%(env)s') or ''
        if _p:
            with open(_p, 'w', encoding='utf-8') as f:
                json.dump(_sg_engine_facts(), f, ensure_ascii=False, indent=1)
    except Exception:
        pass
''' % {'env': FACTS_ENV, 'mark': PROBE_MARK}


# ---------------------------------------------------------------------------
#  环境与子进程
# ---------------------------------------------------------------------------

def find_sdk(explicit=None) -> Path:
    """
    SDK 位置：显式参数 > 环境变量 > 候选列表。**找不到就抛**（不猜）。
    """
    cands = [explicit, os.environ.get(SDK_ENV, ''),
             *DEFAULT_SDK_CANDIDATES]
    for c in cands:
        if not c:
            continue
        p = Path(c)
        if (p / 'renpy.py').is_file():
            return p
    raise FileNotFoundError(
        f'找不到 Ren\'Py SDK（想找 renpy.py）。用 --sdk 或环境变量 {SDK_ENV} 指定')


def _engine_python(sdk: Path) -> Path:
    """SDK 自带的 python（拿不到就退回当前解释器）。"""
    for rel in ('lib/py3-windows-x86_64/python.exe',
                'lib/py3-linux-x86_64/python'):
        p = Path(sdk) / rel
        if p.is_file():
            return p
    return Path(sys.executable)


def run_engine(sdk, project_root, args, timeout=DEFAULT_TIMEOUT) -> tuple:
    """
    跑一次引擎命令 → `(退出码, 合并输出)`。环境里带上 `SDL_* = dummy`（无头）。

    ⚠ 超时**不抛**：返回 `(-1, 说明)`。采集是"可失败的动作"，不该把调用方炸掉。
    """
    cmd = [str(_engine_python(Path(sdk))), str(Path(sdk) / 'renpy.py'),
           str(project_root)] + [str(a) for a in args]
    env = dict(os.environ)
    env['SDL_VIDEODRIVER'] = 'dummy'
    env['SDL_AUDIODRIVER'] = 'dummy'
    try:
        p = subprocess.run(cmd, cwd=str(sdk), env=env, capture_output=True,
                           text=True, errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f'引擎命令超时（>{timeout}s）：{" ".join(cmd[3:])}'
    except OSError as e:
        return -1, f'引擎命令起不来：{e!r}'
    return p.returncode, (p.stdout or '') + (p.stderr or '')


# ---------------------------------------------------------------------------
#  清单 / 探针的注入与清理
# ---------------------------------------------------------------------------

def _sha1_manifest(project_root) -> dict:
    """
    `game/**` 里**文本文件**的 sha1 清单（`{相对路径: 摘要}`）。

    为什么只扫文本、不扫图片：真机 `game/` 1.9GB（绝大多数是图片），
    全量哈希要读一整个 GB 级目录；而采集**从不对图片开写**。
    清单的目的是"证明我们没动项目" —— 覆盖脚本与配置就够了。

    ⚠ **不含项目根下的 `errors.txt` / `log.txt`**：那是引擎自己每次运行都会重写的日志，
    放进来清单**永远对不上**。其中 `errors.txt` 的风险（被我们误删）另由
    「挪开 → 跑 → 还原」的逻辑 + 专门用例兜住。
    """
    root = Path(project_root)
    base = root / 'game'
    out: dict = {}
    if not base.is_dir():
        return out
    for path in sorted(base.rglob('*')):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _MANIFEST_SUFFIXES:
            continue
        rel = path.relative_to(base)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        try:
            out[str(rel).replace('\\', '/')] = hashlib.sha1(
                path.read_bytes()).hexdigest()
        except OSError as e:
            swallow('pipeline.engine_collect.manifest', e)
    return out


def _write_probe(project_root) -> Path:
    """
    把探针写进 `game/`（这是**唯一**会新增的 `.rpy`）。

    ⚠ **绝不覆盖别人的文件**：同名文件已存在、且首行不是我们的标记 → **拒绝并报错**。
    这条与 `HANDWRITTEN_TARGET` 同一个道理：宁愿采集失败，也不能盖掉用户的一行东西。
    """
    target = Path(project_root) / 'game' / PROBE_FILENAME
    if target.exists():
        try:
            head = target.read_text(encoding='utf-8', errors='replace')[:400]
        except OSError as e:
            swallow('pipeline.engine_collect.write_probe.read', e)
            head = ''
        if PROBE_MARK not in head:
            raise FileExistsError(
                f'同名文件已存在且不是本工具写的，拒绝覆盖：{target}')
    target.write_text(probe_source(), encoding='utf-8')
    return target


def _remove_probe(project_root) -> list:
    """
    删掉探针 —— **`.rpy` 和它的 `.rpyc` 都要删**（规则 N7）。

    实测：只删 `.rpy` 时，孤儿 `.rpyc` **不会被引擎自动清理**，会一直留在 `game/` 里。
    """
    gamedir = Path(project_root) / 'game'
    removed = []
    stem = PROBE_FILENAME[:-len('.rpy')]
    for p in list(gamedir.glob(stem + '.rpy*')):
        try:
            p.unlink()
            removed.append(p.name)
        except OSError as e:
            swallow('pipeline.engine_collect.remove_probe', e)
    return removed


# ---------------------------------------------------------------------------
#  lint 报告分类（坑 1 / 坑 2）
# ---------------------------------------------------------------------------

_PARSE_MARK = ('expected ', 'KeyError', 'SyntaxError', 'ParseError')
_SEMANTIC_MARK = ('is not an image', 'is not loadable', 'not the prefix of',
                  'ambiguous', 'is not defined')


def classify_lint(text: str, clean: bool = True) -> dict:
    """
    lint 输出 → 三类。

      · `parse_errors`    —— **阻塞**：一旦有，后面全部语义检查都不跑（坑 2）
      · `semantic_errors` —— 需修但不阻塞（图像引用 / 资源缺失…）
      · `program_errors`  —— **程序自己的 `lint=` 回调报的**（规则 N4：白送的一层校验，要收进来）

    `clean=False`（有 `errors.txt`）时，输出里的报错一律归到 parse —— 因为那时
    引擎是**中止**的，语义检查没跑，"没有语义错误"本身不可信。
    """
    parse_errors, semantic, program = [], [], []
    for line in (text or '').splitlines():
        s = line.strip()
        if not s:
            continue
        is_report = ('.rpy' in s or s.startswith('File ')) and ':' in s
        if not is_report:
            continue
        if any(m in s for m in _SEMANTIC_MARK):
            (semantic if clean else parse_errors).append(s)
        elif any(m in s for m in _PARSE_MARK):
            parse_errors.append(s)
        else:
            program.append(s)
    return {'parse_errors': parse_errors[:80],
            'semantic_errors': semantic[:80],
            'program_errors': program[:80]}


def _read_json(path) -> object:
    """读一个 JSON 文件；失败返回 `None`（不抛）。"""
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        swallow('pipeline.engine_collect.read_json', e)
        return None


# ---------------------------------------------------------------------------
#  采集主流程
# ---------------------------------------------------------------------------

def _derived_overridden_builtins(project_root) -> list:
    """
    **规则 N2**：项目用 `register_statement(<内置名>)` 覆盖了内置语句 → 记下来。

    为什么必须做：实测（D1a/D1b/D2）覆盖**真的生效**，而且**完全替换**——
    覆盖之后内置的 parse 就没了。而如果那个覆盖自己不报错，**lint 一声不吭**（C7）。
    这是唯一"引擎接受了、工具却浑然不知"的口子，所以必须**静态扫出来**。
    """
    from storygraph.core import vocabulary as V
    from storygraph.story import cds_registry

    known = set()
    for attr in ('RENPY_KNOWN_HEADS', 'RENPY_STATEMENT_VERBS'):
        value = getattr(V, attr, None)
        if value:
            known |= set(str(x) for x in value)
    out = []
    try:
        for spec in cds_registry.scan_project_cds(project_root):
            name = str(getattr(spec, 'name', '') or '')
            if not name:
                continue
            for one in ([name] + list(getattr(spec, 'aliases', None) or [])):
                if one in known:
                    out.append({'name': one,
                                'file': str(getattr(spec, 'source_file', '') or ''),
                                'line': int(getattr(spec, 'source_line', 0) or 0),
                                'reason': '这条名字是内置语句；覆盖会**完全替换**它'})
                    break
    except Exception as e:                       # noqa: BLE001
        swallow('pipeline.engine_collect.derived', e)
    return out


def _derived_undeclared_heads(project_root, *, ask: bool = False,
                              ask_limit: int = 12) -> list:
    """
    **扫描线**：这个项目里有哪些"工具还不认识的写法"。

    为什么它要在**采集时**算一次（方案 §四 的 `derived.undeclared_heads`）：

      · 扫描结果以前只活在界面里（打开项目才算），于是**没法进版本库、没法比对**；
        采进 `engine_facts.json` 之后，它和"引擎事实"同生共死、可 diff；
      · 它也是**交给程序看的清单** —— 方案 §三/§九 说扫描要"喂给探针该测什么"：
        人拿着它去问程序"这几个写法是什么意思"，就是 ⑨ 三种回头的入口。

    `ask=True`（**可选、默认关**）时再走一步：把每个头**丢给引擎跑一遍**
    （`statement_probe.ask_many`，一次 lint 问完一批），把引擎的结论挂在每一条上
    （`engine_recognized` / `engine_error` / `engine_summary`）。
    于是"这个写法引擎到底认不认"不再靠猜 —— **能让引擎回答的就不让人填**。

    ⚠ 三条自我约束（都写进判据）：
      ① **只用 V2-1 之后的闸门版扫描器** —— 内置语句（`with`/`window`/`pause`…）
         和"台词被误读"的行**不进这份清单**（否则又是"工具在输出错误知识"）；
      ② 扫描器**只说我认出了 N 个**（规则 N1）—— 所以这里照抄
         `scan_disclaimer`，绝不宣称"项目里只有这些"；
      ③ 扫不出来（解析失败等）→ **空列表**，绝不编。
    """
    try:
        from storygraph.story.statement_registry import discover_undeclared
        found = discover_undeclared(project_root)
    except Exception as e:                       # noqa: BLE001 - 扫不动就不写
        swallow('pipeline.engine_collect.undeclared_heads', e)
        return []
    out = []
    for rec in (found or ()):
        head = str(rec.get('head') or '')
        if not head:
            continue
        out.append({'head': head,
                    'count': int(rec.get('count') or 0),
                    'files': [str(f) for f in (rec.get('files') or [])][:8],
                    'block': bool(rec.get('block'))})
    out.sort(key=lambda r: (-r['count'], r['head']))
    if ask and out:
        _attach_engine_verdicts(project_root, out, ask_limit)
    return out


def _attach_engine_verdicts(project_root, rows: list, limit: int) -> None:
    """
    把"引擎认不认"挂在扫描结果上（就地改 `rows`）。**问不到就说问不到。**

    ⚠ 这不是"猜"：结论来自引擎自己跑的 lint（`statement_probe`）。
    但**采不到就不写结论**（`engine_recognized` 留空 + `engine_error` 说明），
    与方案坑 4 同一条规矩。
    """
    try:
        from storygraph.pipeline import statement_probe as SP
        res = SP.ask_many(project_root, [r['head'] for r in rows],
                          limit=limit)
    except Exception as e:                       # noqa: BLE001 - 问不到不影响采集
        swallow('pipeline.engine_collect.attach_verdicts', e)
        for r in rows:
            r['engine_error'] = f'{type(e).__name__}: {e}'
        return
    if not res.get('ok'):
        for r in rows:
            r['engine_error'] = str(res.get('reason') or '问不到')
        return
    for r in rows:
        verdict = (res.get('results') or {}).get(r['head'])
        if not verdict:
            r['engine_skipped'] = True
            continue
        if verdict.get('recognized') is not None:
            r['engine_recognized'] = bool(verdict['recognized'])
        r['engine_summary'] = str(verdict.get('summary') or '')
        if verdict.get('evidence'):
            r['engine_error'] = str(verdict['evidence'][0])[:300]


def collect(project_root, sdk=None, *, mode='in-place', out_path=None,
            timeout=DEFAULT_TIMEOUT, ask: bool = False,
            ask_limit: int = 12) -> dict:
    """
    采一次，返回报告 dict（含 `facts` 与 `report`）。**不写盘**（写盘见 `write_facts`）。

    参数
    ----
    `mode` : `'in-place'`（默认，只增删一个探针文件）或 `'copy'`（整份拷到临时目录再采，测试用）。

    返回
    ----
    `{'ok': bool, 'reason': str, 'facts': dict, 'manifest_ok': bool,
      'removed': [...], 'output': str}`
    """
    root = Path(project_root)
    if not (root / 'game').is_dir():
        return {'ok': False, 'reason': f'不是工程根（没有 game/）：{root}',
                'facts': EF.empty_facts(), 'manifest_ok': None,
                'removed': [], 'output': ''}

    work = root
    tmp = None
    if mode == 'copy':
        tmp = Path(tempfile.mkdtemp(prefix='sg_engine_copy_'))
        work = tmp / root.name
        shutil.copytree(root, work)
    try:
        return _collect_at(work, sdk, out_path=out_path, timeout=timeout,
                           ask=ask, ask_limit=ask_limit)
    except FileNotFoundError as e:
        # 找不到 SDK 是"采不了"，不是"程序坏了" —— 如实报成一次失败的采集
        return {'ok': False, 'reason': str(e), 'facts': EF.empty_facts(),
                'manifest_ok': None, 'removed': [], 'output': '',
                'lint_exit': None}
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _collect_at(work: Path, sdk, *, out_path=None, timeout=DEFAULT_TIMEOUT,
                ask: bool = False, ask_limit: int = 12) -> dict:
    sdk_path = find_sdk(sdk)
    engine_root = Path(sdk_path)

    # ① 扫"覆盖内置"必须在**注入探针之前**（免得扫到我们自己的探针）
    overridden = _derived_overridden_builtins(work)

    manifest_before = _sha1_manifest(work)
    # 上一轮留下的 errors.txt 会让本轮误判 —— 但**它可能是用户自己的文件**，
    # 所以做法是「挪开 → 跑 → 读 → 还原」，**不是删掉**。
    errors_txt = work / 'errors.txt'
    errors_backup = None
    if errors_txt.exists():
        errors_backup = work / (errors_txt.name + '.sg-parked')
        try:
            errors_txt.replace(errors_backup)
        except OSError as e:
            swallow('pipeline.engine_collect.park_errors', e)
            errors_backup = None

    facts_out = Path(out_path) if out_path else (
        Path(tempfile.mkdtemp(prefix='sg_facts_')) / 'probe.json')
    facts_out.parent.mkdir(parents=True, exist_ok=True)
    dump_out = facts_out.parent / 'json_dump.json'

    _write_probe(work)
    env_backup = os.environ.get(FACTS_ENV)
    os.environ[FACTS_ENV] = str(facts_out)
    try:
        rc, out = run_engine(engine_root, work,
                             ['lint', '--error-code', '--json-dump', str(dump_out)],
                             timeout=timeout)
    finally:
        if env_backup is None:
            os.environ.pop(FACTS_ENV, None)
        else:
            os.environ[FACTS_ENV] = env_backup
        removed = _remove_probe(work)

    # ② 闸门：先看 parse 干净没有（坑 2 / 坑 4）。
    #    errors.txt 是本轮**新产生**的才算数 —— 读完把它清掉、把用户原来的放回去。
    produced_errors = ''
    try:
        if errors_txt.exists():
            produced_errors = errors_txt.read_text(
                encoding='utf-8', errors='replace').strip()
    except OSError as e:
        swallow('pipeline.engine_collect.read_errors', e)
    try:
        if errors_txt.exists():
            errors_txt.unlink()
        if errors_backup is not None:
            errors_backup.replace(errors_txt)
    except OSError as e:
        swallow('pipeline.engine_collect.restore_errors', e)
    clean = not produced_errors

    probe_data = _read_json(facts_out) or {}
    dump_data = _read_json(dump_out) or {}
    probe_ok = bool(probe_data) and facts_out.exists()

    lint = classify_lint(out, clean=clean)

    # ③ 合并（每条带来源；两个来源不一致 → 进 conflicts，不静默挑一个）
    dump_loc = (dump_data or {}).get('location') or {} if isinstance(dump_data, dict) else {}
    # 扫描线：这份清单**随事实一起进版本库**（方案 §四 的 derived.undeclared_heads）；
    # `ask=True` 时再让**引擎自己**回答"认不认这些写法"（能问引擎的就不让人填）
    undeclared = _derived_undeclared_heads(work, ask=ask, ask_limit=ask_limit)
    payloads = {
        'images': [(EF.SRC_PROBE, probe_data.get('images'))],
        'tags': [(EF.SRC_PROBE, probe_data.get('tags'))],
        'attributes': [(EF.SRC_PROBE, probe_data.get('attributes'))],
        'candidates': [(EF.SRC_PROBE, probe_data.get('candidates'))],
        'labels': [(EF.SRC_PROBE, probe_data.get('labels'))],
        'transforms': [(EF.SRC_JSON_DUMP, sorted(dump_loc.get('transform') or []))],
        'screens': [(EF.SRC_JSON_DUMP, sorted(dump_loc.get('screen') or []))],
        'defines': [(EF.SRC_JSON_DUMP, sorted(dump_loc.get('define') or []))],
    }
    facts = EF.build_facts(
        payloads,
        engine={'version': _engine_version(engine_root), 'sdk': str(engine_root)},
        fingerprint={'inputs': _fingerprint_inputs(manifest_before)},
        collection={'parse_clean': bool(clean), 'probe_complete': bool(probe_ok),
                    'lint_exit': rc},
        lint=lint,
        derived={'overridden_builtins': overridden,
                 'undeclared_heads': undeclared,
                 'scan_disclaimer': '以上是「我认出的」，不代表项目里只有这些（N1）'},
    )

    manifest_after = _sha1_manifest(work)
    manifest_ok = (manifest_before == manifest_after)
    if not manifest_ok:
        changed = sorted(set(manifest_before) ^ set(manifest_after))
        added = [k for k in changed if k not in manifest_before]
        dropped = [k for k in changed if k not in manifest_after]
        facts.setdefault('conflicts', []).append({
            'field': 'manifest', 'sources': [EF.SRC_SCANNER],
            'note': '采集前后 game/ 文本清单不一致（除 .rpyc 外本应一致）',
            'values': [f'新增 {added[:5]}', f'消失 {dropped[:5]}']})

    ok, reason = EF.is_collectable(facts)
    if not ok and manifest_ok is False:
        reason += '；另外：采集前后清单不一致'
    return {'ok': ok, 'reason': reason, 'facts': facts, 'manifest_ok': manifest_ok,
            'removed': removed, 'output': out, 'lint_exit': rc}


_VERSION_LINE = re.compile(r"""^\s*version\s*=\s*(['"])(?P<v>[^'"]+)\1\s*$""")


def _engine_version(engine_root: Path) -> str:
    """
    引擎版本（取自 SDK 的 `renpy/vc_version.py`）。

    ⚠ 只认**字面量**（`version = '8.5.3.26051504'`）—— 取不到就留空，
    **不猜**。留空的代价是"引擎升级"这一种漂移检测不到（内容指纹仍在），
    所以这里要如实：宁可空着，也不要写一个编出来的版本号。
    （实测踩到：原先找的是 `version_tuple`，而它在 `__init__.py` 里是**算**出来的，
      `vc_version.py` 里只有 `version = '…'` —— 于是版本一直是空的。）
    """
    path = Path(engine_root) / 'renpy' / 'vc_version.py'
    try:
        for line in path.read_text(encoding='utf-8',
                                   errors='replace').splitlines():
            m = _VERSION_LINE.match(line)
            if m:
                return m.group('v')
    except OSError as e:
        swallow('pipeline.engine_collect.engine_version', e)
    return ''


def _fingerprint_inputs(manifest: dict) -> dict:
    """清单 → 一个能代表"这次事实是照着哪份工程算的"的指纹。"""
    if not manifest:
        return {}
    blob = '\n'.join(f'{k}:{v}' for k, v in sorted(manifest.items()))
    return {'game_text_files': len(manifest),
            'game_text_sha1': hashlib.sha1(blob.encode('utf-8')).hexdigest()}


def write_facts(path, facts: dict) -> Path:
    """
    把事实写到盘上（**唯一落盘点**；已在 `test_single_write_entry.WRITERS` 登记）。

    为什么写盘要单独一个函数：本仓库有一条结构性守卫 —— 全包所有落盘点都必须在
    登记表里说清"写的是什么"，新增落盘点会让守卫变红。集中在这里，只有一处要登记。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(EF.dumps(facts), encoding='utf-8')
    return target


__all__ = [
    'PROBE_FILENAME', 'FACTS_ENV', 'SDK_ENV', 'DEFAULT_TIMEOUT',
    'probe_source', 'find_sdk', 'run_engine', 'classify_lint',
    'collect', 'write_facts',
]
