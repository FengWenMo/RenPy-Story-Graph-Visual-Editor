"""
**引擎对 image 名的裁决**（2026-11-04）
=====================================

用户：「不要做成按需的按钮……慢就慢点，要真问！！而且自动问！（在打开项目的
那一刻就该问了！！！）」

## 为什么要问引擎

`show lan aa happy` 合不合法，**只有引擎说了算**。工具的"声明 / 表 /
`init python` 字面量"三路都是**间接证据**：

- `aa` 写在 `CC_OUTFIT_CYCLE`（界面换装代号）里，三路都能"认出"它；
- 但引擎在最小项目里实测原话是：

      game/script.rpy:12 'lan aa happy' is not an image.

  —— 即使 `aa.png` **文件存在**也不认（layeredimage 只认声明过的 attribute）。

所以判据的**最高一层必须是引擎**，而不是我们猜的那些间接证据。

## 怎么问（不碰用户项目）

1. **镜像**：`.rpy` 拷全文；`game/` 下的图片建**空文件占位**
   （引擎判"这个名字存不存在"只看文件名与目录结构，不看像素）；
   `.git` / `build` / `cache` 跳过。
2. **跑 lint**：`renpy.exe <镜像> lint`（真项目 96 个 `.rpy`，几分钟）。
3. **解析**：只收 `'<名字>' is not an image.` 那一种 —— 它是引擎对
   "这个 image 名不存在"的**明确否定**。
4. **缓存**：存用户级目录（`%LOCALAPPDATA%/StoryGraph/engine_images/`），
   键 = 项目根；失效判据 = **脚本指纹 + 图片数量**
   （改了 `.rpy` 或加了图 → 重跑）。

⚠ 没有 `valid` 名单：lint 只检查**被用到**的 image 名，没被 `show` 过的
它不说。所以这里只提供**否定**证据（`invalid`），肯定方向仍走老三层。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 会被当成"图片"的扩展名（镜像时建空占位）
_IMG_EXT = frozenset({'.png', '.jpg', '.jpeg', '.webp', '.avif', '.gif'})
#: 镜像时整目录跳过（与语法无关，只会拖慢）
_SKIP_PARTS = frozenset({'.git', 'build', 'cache', '__pycache__', '.pip-tmp'})
#: 默认超时（真项目 lint 实测数分钟）
DEFAULT_TIMEOUT = 1800
#: 缓存版本（解析格式变了就 +1，老缓存自然失效）
CACHE_VERSION = 2

_NOT_IMAGE_RE = re.compile(r"'([^']+)' is not an image")


def cache_dir() -> Path:
    """用户级缓存目录（**不写用户项目**）。"""
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME')
    root = Path(base) if base else Path.home() / '.cache'
    d = root / 'StoryGraph' / 'engine_images'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm_root(project_root) -> str:
    """
    项目根 → **稳定字符串**（缓存键用）。

    ⚠ 三个必须消掉的差异（2026-11-04 踩到前两个）：
      · `D:/x` 与 `D:////x`（斜杠方向）→ `Path` 统一；
      · 字符串里混进 `\r` / `\n`（探针被 shell 转义过）→ 先剔控制字符；
      · 大小写 → lower（Windows 不区分）。
    同一份缓存由一个 key 决定，`str` 与 `Path` 传进来必须落到同一个文件。
    """
    raw = str(project_root or '')
    raw = ''.join(ch for ch in raw if ch >= ' ').strip()
    if not raw:
        return ''
    try:
        raw = str(Path(raw))
    except (OSError, ValueError) as e:
        swallow('assets.engine_images.norm_root', e)
    return raw.lower()


def cache_path(project_root) -> Path:
    key = hashlib.sha1(_norm_root(project_root).encode('utf-8')).hexdigest()
    return cache_dir() / f'{key[:16]}.json'


def rpy_stamp(project_root) -> str:
    """
    **只扫 `.rpy`** 的指纹（给"裁决还新不新"用）。

    为什么单开一个（2026-11-05 L1-2）：`project_stamp()` 要 `rglob` 全项目
    （真机 3450 个文件 / **356 ms**），而那些图**不影响**"引擎认哪些 image 名"
    —— 判名字看的是注册表（`.rpy` 里的声明）。只 stat 93 个 `.rpy`：

        第 1 次 67.6 ms / 第 2 次 39.4 ms / 第 3 次 45.2 ms（真机实测）

    ⚠ **仍然超过 20 ms**，所以它**不放进** `Symbols.__init__` —— 由
    `Symbols.engine_verdict_state()` 第一次问的时候才算（懒加载）。
    """
    root = Path(str(project_root or ''))
    if not root.is_dir():
        return ''
    try:
        from storygraph.parsing.discover import discover_scripts
        files = sorted(discover_scripts(str(root)))
    except Exception as e:                      # noqa: BLE001 - 扫不动就是空指纹
        swallow('assets.engine_images.rpy_stamp', e)
        return ''
    parts = []
    for fp in files:
        try:
            st = Path(fp).stat()
        except OSError:
            continue
        try:
            rel = Path(fp).relative_to(root).as_posix()
        except ValueError:
            rel = Path(fp).as_posix()
        parts.append(f'{rel}:{st.st_mtime_ns}:{st.st_size}')
    if not parts:
        return ''
    return hashlib.sha1('\n'.join(parts).encode('utf-8')).hexdigest()


def verdict_state(project_root) -> str:
    """
    这份裁决还**算不算数** → `'fresh' | 'stale' | 'unknown'`。

    · `unknown`：没有缓存 / 缓存版本不对（**没问过**，不是"结论没问题"）；
    · `stale`  ：有缓存，但 `.rpy` 变了（或老缓存没指纹）；
    · `fresh`  ：`.rpy` 没动，裁决可用。

    ⚠ **不算图片数量**：引擎判"这个名字存不存在"看注册表，加一张图不会让
    裁决失效。算图片会让每次都要 `rglob` 全项目（356 ms）。
    """
    hit = cached_fast(project_root)
    if not hit.get('ok'):
        return 'unknown'
    stamp = str(hit.get('stamp_rpy') or '')
    if not stamp:
        return 'stale'                          # 老缓存（v1）没有指纹
    now = rpy_stamp(project_root)
    if not now:
        return 'stale'                          # 算不出来 → 不敢说 fresh
    return 'fresh' if now == stamp else 'stale'


def project_stamp(project_root) -> dict:
    """`{n_rpy, newest_rpy, n_images}` —— 变了就重问。"""
    root = Path(project_root)
    n_rpy = 0
    newest = 0.0
    n_img = 0
    try:
        for p in root.rglob('*'):
            rel = p.relative_to(root)
            if any(part in _SKIP_PARTS for part in rel.parts):
                continue
            if p.is_dir():
                continue
            suf = p.suffix.lower()
            if suf == '.rpy':
                n_rpy += 1
                try:
                    newest = max(newest, p.stat().st_mtime)
                except OSError:
                    continue
            elif suf in _IMG_EXT:
                n_img += 1
    except OSError as e:                          # noqa: BLE001
        swallow('assets.engine_images.stamp', e)
    return {'n_rpy': n_rpy, 'newest_rpy': round(newest, 3), 'n_images': n_img}


def cached_fast(project_root) -> dict:
    """
    只读缓存文件 + 版本检查，**不算指纹**（快，给 `Symbols` 构造用）。

    ⚠ 为什么不校验指纹（2026-11-04 实测）：`project_stamp()` 要 `rglob`
    整个项目（真机 3000+ 图），**3.2 s** —— 塞进 `Symbols.__init__` 之后
    每次建符号表都要等 3 秒。指纹校验留给"要不要重跑引擎"那条路
    （`cached()`），而读裁决只需要"有一份就能用"。
    """
    try:
        data = json.loads(cache_path(project_root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'ok': False, 'source': 'engine', 'cached': False,
                'invalid': [], 'reason': '没有缓存'}
    if data.get('version') != CACHE_VERSION:
        return {'ok': False, 'source': 'engine', 'cached': False,
                'invalid': [], 'reason': '缓存版本过期'}
    data['cached'] = True
    return data


def cached(project_root) -> dict:
    """
    读缓存 + **校验指纹**（项目变了 → `ok=False`，调用方据此决定重跑）。

    返回值形状与 `verdicts()` 一致，多一个 `cached: True`。
    """
    data = cached_fast(project_root)
    if not data.get('ok'):
        return data
    if data.get('stamp') != project_stamp(project_root):
        return {'ok': False, 'source': 'engine', 'cached': False,
                'invalid': [], 'reason': '项目变了（脚本或图片数量）'}
    return data


def _mirror(root: Path, work: Path) -> dict:
    """`.rpy` 拷全文；图片建空占位；其余跳过。**不改用户项目**。"""
    n_rpy = n_img = 0
    for src in root.rglob('*'):
        rel = src.relative_to(root)
        if any(part in _SKIP_PARTS for part in rel.parts):
            continue
        dst = work / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        suf = src.suffix.lower()
        if suf == '.rpy':
            try:
                shutil.copy2(src, dst)
                n_rpy += 1
            except OSError:
                continue
        # ⚠ **不建图片占位**（2026-11-04 用户："应该从变量入手，看变量有没有
        # 被定义，而不是有没有 png 文件吧……万一 show 的不是 png 呢？"）。
        #
        # 引擎判 `'lan aa happy' is not an image.` 看的是 **image 注册表**
        # （`layeredimage` 的 attribute / `image` 语句），**与文件在不在无关**：
        # 最小项目实测 —— `aa.png` **文件存在**时引擎照样报这句话。
        # 所以这里一个像素都不用拷，`.rpy` 就是全部依据。
    (work / 'game').mkdir(parents=True, exist_ok=True)
    return {'rpy': n_rpy, 'images': n_img}


def _sdk_from_facts(project_root) -> str:
    """SDK 路径：环境变量优先，其次本工具记过的（`core.engine_facts`）。"""
    env = os.environ.get('STORYGRAPH_SDK') or os.environ.get('RENPY_SDK')
    if env:
        return env
    try:
        from storygraph.core import engine_facts as EF
        for key in ('sdk', 'sdk_path', 'renpy_sdk'):
            val = (EF.value_of(EF.loads(EF.facts_path(project_root)
                                        .read_text(encoding='utf-8')), key)
                   if EF.facts_path(project_root).is_file() else None)
            if val:
                return str(val)
    except Exception as e:                        # noqa: BLE001
        swallow('assets.engine_images.sdk', e)
    return ''


def verdicts(project_root, *, sdk='', timeout=DEFAULT_TIMEOUT,
             force=False) -> dict:
    """
    **真跑一次 lint**，拿引擎"这些名字不是图像"的裁决。

    缓存新鲜且 `force=False` → 直接用（`cached: True`）。
    缓存路径见 `cache_path()`（用户级目录）。
    """
    root = Path(project_root)
    if not (root / 'game').is_dir():
        return {'ok': False, 'source': 'engine', 'cached': False,
                'invalid': [], 'reason': f'不是工程根：{project_root}'}
    if not force:
        hit = cached(root)
        if hit.get('ok'):
            return hit

    exe_sdk = str(sdk or _sdk_from_facts(root) or '')
    exe = (Path(exe_sdk) / 'renpy.exe') if exe_sdk else Path('renpy.exe')
    if exe_sdk and not exe.is_file():
        return {'ok': False, 'source': 'engine', 'cached': False,
                'invalid': [], 'reason': f'SDK 里没有 renpy.exe：{exe}'}
    if not exe_sdk:
        return {'ok': False, 'source': 'engine', 'cached': False,
                'invalid': [],
                'reason': '没配 SDK（设环境变量 STORYGRAPH_SDK 指向 Ren\'Py SDK）'}

    stamp = project_stamp(root)
    tmp = Path(tempfile.mkdtemp(prefix='sg_engine_img_'))
    t0 = time.time()
    try:
        work = tmp / root.name
        _mirror(root, work)
        env = dict(os.environ)
        env['RENPY_SKIP_SPLASHSCREEN'] = '1'
        env.setdefault('RENPY_DISABLE_BACKUPS', '1')
        try:
            proc = subprocess.run([str(exe), str(work), 'lint'],
                                  capture_output=True, text=True,
                                  encoding='utf-8', errors='replace',
                                  timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return {'ok': False, 'source': 'engine', 'cached': False,
                    'invalid': [], 'stamp': stamp,
                    'reason': f'lint 超过 {timeout}s 没跑完（项目太大）'}
        except OSError as e:                      # noqa: BLE001
            return {'ok': False, 'source': 'engine', 'cached': False,
                    'invalid': [], 'stamp': stamp,
                    'reason': f'引擎起不来：{e}'}
        text = (proc.stdout or '') + (proc.stderr or '')
        invalid = sorted({m.group(1).strip()
                          for m in _NOT_IMAGE_RE.finditer(text)})
        out = {
            'ok': True, 'source': 'engine', 'cached': False,
            'invalid': invalid,
            'count': len(invalid),
            'seconds': round(time.time() - t0, 1),
            'stamp': stamp,
            'stamp_rpy': rpy_stamp(root),
            'version': CACHE_VERSION,
            'reason': '',
            'note': ('只有**否定**证据：lint 只检查被 `show` 用到的名字，'
                     '没被用过的不在其中'),
        }
        try:
            cache_path(root).write_text(
                json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
        except OSError as e:                      # noqa: BLE001 - 写不进缓存不算失败
            swallow('assets.engine_images.cache_write', e)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def spec_key(spec) -> str:
    """
    image 名 → **与属性顺序无关**的键（`lan aa happy` → `lan|aa|happy`）。

    为什么必须归一化：引擎报的是**它看到的那个写法**（`lan aa happy`），
    而卡片里可能是 `lan happy aa`（`preferred_image_attrs` 会重排）。
    顺序不同就漏判 —— 与提示层那条 I12 是同一个道理。
    """
    parts = [p for p in str(spec or '').split() if p]
    if not parts:
        return ''
    return '|'.join([parts[0]] + sorted(parts[1:]))


def bad_keys(project_root) -> set:
    """缓存里"引擎说不是图像"的**归一化键**集合（快读，不校验指纹、不跑引擎）。"""
    hit = cached_fast(project_root)
    if not hit.get('ok'):
        return set()
    return {spec_key(x) for x in (hit.get('invalid') or []) if str(x).strip()}


def bad_specs(project_root) -> set:
    """缓存里的"引擎说不是图像"的**小写名字集合**（快读，不跑引擎）。"""
    hit = cached_fast(project_root)
    return {str(x).strip().lower() for x in (hit.get('invalid') or [])
            if str(x).strip()} if hit.get('ok') else set()


# ===========================================================================
#  二、**验证生成出来的 `show` 行能不能跑**（2026-11-05）
# ===========================================================================
#
# 用户：「再写，再查（必须保证能跑不报错！！！）后续因为有引擎检查……所以可能甚至
# 不会有 json 文件给你查！必须从引擎里面直接审查……」
#
# 所以判据不能只比字符串（"顺序对不对"），必须**真让引擎读一遍**：
# 把待验的 spec 塞进一个临时项目的一个 label 里，跑 lint，看它报什么。
#
# ⚠ 与 `verdicts()` 的区别：
#   · `verdicts()` 回答"**项目里现有的** show 行哪些不是图像"（不改项目）；
#   · 这个回答"**新生成的**这一行，引擎肯不肯接受"（语法 + 语义都看）。

#: lint 里算"这一行**根本跑不了**"的话（语法/结构错）
_SPEC_FATAL_HINTS = (
    'expected statement',
    'end of line expected',
    'invalid syntax',
    'unexpected at',
    'expected menuitem',
    'could not parse',
)


def verify_show_specs(project_root, specs, *, sdk: str = '',
                      timeout: int = 300) -> dict:
    """
    一批 `show` spec → **引擎怎么说**。

    做法：镜像项目（只拷 `.rpy`）→ 写一个 `_sg_verify.rpy`，里面每条 spec 一行
    `show …` → 跑一次 lint → 把报错按行分回每个 spec。

    返回::

        {'ok': bool,                      # 全部没被引擎挑刺
         'source': 'engine',
         'sdk': str, 'reason': str,
         'fatal': [{'spec','line','why'}],     # 语法/结构错（**真跑不了**）
         'unknown_image': [{'spec','line','why'}],  # `is not an image`（名字不存在）
         'output': str}

    为什么 `fatal` 和 `unknown_image` 分开：后者在"资源还没做齐"的项目里是**预期
    内的**（`is not an image` 也可能是资源没生成），拿它当"顺序写错了"会误判。
    """
    root = Path(str(project_root or ''))
    if not (root / 'game').is_dir():
        return {'ok': False, 'source': 'engine', 'reason': '不是工程根',
                'fatal': [], 'unknown_image': [], 'output': ''}
    items = [str(x or '').strip() for x in (specs or ())]
    items = [x for x in items if x]
    if not items:
        return {'ok': True, 'source': 'engine', 'reason': '', 'fatal': [],
                'unknown_image': [], 'output': ''}

    exe_sdk = str(sdk or _sdk_from_facts(root) or '')
    if not exe_sdk:
        return {'ok': False, 'source': 'engine',
                'reason': "没配 SDK（设 STORYGRAPH_SDK）", 'fatal': [],
                'unknown_image': [], 'output': ''}
    exe = Path(exe_sdk) / 'renpy.exe'
    if not exe.is_file():
        return {'ok': False, 'source': 'engine',
                'reason': f'SDK 里没有 renpy.exe：{exe}', 'fatal': [],
                'unknown_image': [], 'output': ''}

    tmp = Path(tempfile.mkdtemp(prefix='sg_verify_'))
    try:
        work = tmp / root.name
        _mirror(root, work)
        # 每条 spec 一行；行号从 1 开始好对回 spec
        body = ['label _sg_verify_specs:']
        for i, spec in enumerate(items, 1):
            body.append(f'    show {spec}')
        body.append('    return')
        (work / 'game' / '_sg_verify.rpy').write_text(
            '\n'.join(body) + '\n', encoding='utf-8')
        # 让这次 lint 从我们的 label 起（不影响原项目）
        (work / 'game' / '_sg_verify_entry.rpy').write_text(
            'label _sg_verify_start:\n    call _sg_verify_specs\n    return\n',
            encoding='utf-8')
        env = dict(os.environ)
        env['RENPY_SKIP_SPLASHSCREEN'] = '1'
        env.setdefault('RENPY_DISABLE_BACKUPS', '1')
        try:
            proc = subprocess.run([str(exe), str(work), 'lint'],
                                  capture_output=True, text=True,
                                  encoding='utf-8', errors='replace',
                                  timeout=timeout, env=env)
        except (subprocess.TimeoutExpired, OSError) as e:
            return {'ok': False, 'source': 'engine',
                    'reason': f'lint 跑不动：{type(e).__name__}', 'fatal': [],
                    'unknown_image': [], 'output': ''}
        text = (proc.stdout or '') + (proc.stderr or '')
        fatal, unknown = [], []
        for ln in text.split('\n'):
            m = re.search(r'_sg_verify\.rpy",?\s*line\s+(\d+)', ln)
            if not m:
                continue
            try:
                idx = int(m.group(1)) - 2          # 第 1 行是 label，spec 从第 2 行起
            except ValueError:
                continue
            if not (0 <= idx < len(items)):
                continue
            spec = items[idx]
            why = ln.strip()[:160]
            low = why.lower()
            if any(h in low for h in _SPEC_FATAL_HINTS):
                fatal.append({'spec': spec, 'line': idx + 1, 'why': why})
            elif 'is not an image' in low:
                unknown.append({'spec': spec, 'line': idx + 1, 'why': why})
        return {'ok': not fatal, 'source': 'engine', 'reason': '',
                'sdk': exe_sdk, 'fatal': fatal, 'unknown_image': unknown,
                'count': len(items), 'output': text[-4000:]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
