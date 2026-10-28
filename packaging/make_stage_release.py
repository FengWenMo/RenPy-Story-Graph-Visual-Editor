"""
演出专用版（Stage Edition）打包脚本
====================================

为什么要有第二个发布版（2026-10-29，用户拍板）
----------------------------------------------
标准版（`packaging/make_release.py`）只发 StoryGraph 自己的 exe 和说明文档；
用户要做「演出预览 / 真机预览 / 构建」时，还得自己装 Ren'Py SDK。对开发者和
业务策划来说这没问题，但**演出专用版面向的是没有 VSCode、没有 Ren'Py、
没有 Python 的文案策划**：双击 exe 就该能打开项目、看到真实演出效果，
不能先教人装环境。

所以这个脚本把**完整 Ren'Py SDK（只去掉 vscode/ 和 tmp/）** 塞进发布包：

    <pkg>/
      StoryGraph.exe / StoryGraph-CLI.exe / _internal/ ...   ← dist 原样
      renpy_sdk/                                             ← 完整 SDK（去 vscode、tmp、log.txt）
      项目预设/                                              ← 真实项目的规则清单 + 布局 + 说明
      演出专用版.txt                                          ← 版本 / 日期 / SDK 版本 / 预设指纹
      演出专用说明.md                                         ← 给策划看的一页（docs/ 里那份）
      （快速上手.md、新手快速操作手册.md、manual_img/、剧本空表格.xlsx、
        demo/、LICENSE 等由 --dist 里已有内容原样带过去，本脚本不重复造）

和标准版的区别（为什么不能复用 make_release.py）：

  标准版                              演出专用版
  ---------------------------------   ------------------------------------------
  只带 exe，预览/构建要用户自装 SDK     自带完整 SDK，开箱即用
  zip 约 70 MB                        包体不设限，但 zip 必须 ≤ 4GB（MAX_ZIP_BYTES）
  给开发者 / 已有 Ren'Py 环境的人       给只有普通 Windows 的文案策划
  不含「项目预设」                     带真实项目预设 + 自动套用说明

为什么不干脆把 SDK 装进 exe 内部：Ren'Py 的 SDK 是**外部运行时**，它自己会
去 SDK 根找 `renpy/`、`lib/python3.12/`、`lib/py3-windows-x86_64/`，还要能被
当子进程调用；塞进 PyInstaller 的 `_internal/` 后既跑不起来、也没法更新。
所以是「exe 目录 + 旁边一个 renpy_sdk/」，运行时（Lead 的 engine_preview /
UI）按 `RUNTIME_DIRNAME` 这个目录名去找。

排除清单（用户明确要求，少一样都不行；`FORBIDDEN_STAGE` 会收尾再查一次）：

  · `renpy_sdk/vscode`   —— 用户明确不要 VSCode；它也是包体的大头（约 450 MB）
  · `renpy_sdk/tmp`      —— SDK 作者自己的测试工程/安装残留，不是运行时依赖
  · `renpy_sdk/log.txt`  —— 是**这台打包机器**上次运行 SDK 的日志，绝对不能发

怎么构建（在仓库根跑；`dist/StoryGraph` 先由 PyInstaller 生成）：

    # 只铺目录，不压 zip（默认行为，方便先看目录）
    python packaging/make_stage_release.py

    # 完整出包（默认 --dist dist/StoryGraph --out dist/stage）
    python packaging/make_stage_release.py --zip

    # 指定 SDK / 产物 / 输出 / 日期（日期默认今天，测试可注入）
    python packaging/make_stage_release.py --sdk "D:/software/renpy-8.4.1-sdk" \\
        --dist dist/StoryGraph --out dist/stage --date 2026-10-29 --zip

`--sdk` 的默认值：环境变量 `STORYGRAPH_RENPY_SDK` → 否则
`D:/software/renpy-8.4.1-sdk`（作者机器上的默认装法）。找不到就报错，
不会去猜别的目录。

产物：`<out>/StoryGraph-Stage-<version>-win64-<YYYY-MM-DD>/`
zip：`<out>/StoryGraph-Stage-<version>-win64-<YYYY-MM-DD>.zip`
（zip **不**放进包目录里 —— 否则每次重跑都会把上次的 zip 一起压进去，越滚越大。）

怎么验收（自动化判据见 `tests/test_stage_release.py`）：

  1. 包目录存在，两个 exe 都在，`renpy_sdk/renpy.exe`、`renpy_sdk/renpy.py`、
     `renpy_sdk/renpy/`、`renpy_sdk/lib/py3-windows-x86_64/`、
     `renpy_sdk/lib/python3.12/`、`renpy_sdk/LICENSE.txt` 都在；
  2. `renpy_sdk/vscode`、`renpy_sdk/tmp`、`renpy_sdk/log.txt` **一个都不在**；
  3. `项目预设/` 三个文件（rules / layout / README）在，规则和布局是**真实项目**
     的那两份（从仓库 `storygraph/presets/` 复制）；
  4. `演出专用版.txt` 写了 StoryGraph 版本、打包日期、Ren'Py SDK 版本和预设指纹；
  5. `演出专用说明.md` 在（源文件 `docs/演出专用说明.md` 缺了**直接报错**，
     不静默跳过 —— 策划拿到包不能只有 exe 没有说明）；
  6. `--zip` 时 zip 存在、里面有 `renpy_sdk/renpy.exe` 和说明文档、
     大小 ≤ 4GB。

重建行为（幂等）：同名包目录已存在时，先整体删掉再重新铺 —— 保证重跑不会
残留上一次构建的文件（比如上次有、这次被排除的 `vscode/` 还躺在里面）。
构建中途失败也会把半成品目录清掉，不留下一个「看起来像成品」的目录。

只做打包，不改任何运行时代码：`RUNTIME_DIRNAME` / `PRESET_DIRNAME` /
`EDITION_MARKER` 这几个名字是与运行时共享的契约，不要单方面改。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 与运行时共享的契约名字（Lead 的 engine_preview / UI 按这些名字找路径；
# 测试 test_contract_constants_match_documented_layout 会把它们钉死）
# ---------------------------------------------------------------------------
#: 随包的 Ren'Py SDK 目录名。运行时按 `<exe 所在目录>/renpy_sdk/renpy.exe` 找引擎。
RUNTIME_DIRNAME = 'renpy_sdk'
#: 包内项目预设目录名（运行时打开项目时从这里套用默认规则/布局）。
PRESET_DIRNAME = '项目预设'
#: 包内版本标记文件名（说明这是演出专用版、由哪个版本打出来的）。
EDITION_MARKER = '演出专用版.txt'
#: 给策划看的说明文档：包内名字 + 仓库里的源文件。
GUIDE_NAME = '演出专用说明.md'
GUIDE_SOURCE = f'docs/{GUIDE_NAME}'
#: 项目预设的仓库来源目录 + 随包文件（真实项目的规则清单和布局）。
PRESET_SOURCE_DIR = 'storygraph/presets'
PRESET_FILES = ('storygraph.rules.json', 'layout.json')
#: 包内给"收到的项目"留的位置（运行时 `find_received_project` 认的第一个名字）。
PROJECT_DIRNAME = '项目'
PROJECT_PLACEHOLDER = '把项目放在这里.txt'
#: 包名：StoryGraph-Stage-<version>-win64-<YYYY-MM-DD>
PACKAGE_PREFIX = 'StoryGraph-Stage'
PLATFORM_TAG = 'win64'

#: 包内**必须存在**的东西（少一样就失败，绝不降级成警告）。
#: 结尾带 `/` 的是目录，其余是文件；`check_manifest` 按这个区分。
REQUIRED_STAGE = (
    'StoryGraph.exe',
    'StoryGraph-CLI.exe',
    f'{RUNTIME_DIRNAME}/renpy.exe',
    f'{RUNTIME_DIRNAME}/renpy.py',
    f'{RUNTIME_DIRNAME}/renpy/',
    f'{RUNTIME_DIRNAME}/lib/py3-windows-x86_64/',
    f'{RUNTIME_DIRNAME}/lib/python3.12/',
    f'{RUNTIME_DIRNAME}/LICENSE.txt',
    f'{PRESET_DIRNAME}/storygraph.rules.json',
    f'{PRESET_DIRNAME}/layout.json',
    f'{PRESET_DIRNAME}/README.md',
    # 空项目位：解压出来就有一个 `项目/` 文件夹，策划把完整项目**拖进去**即可；
    # 没有它的话"把项目放到 exe 旁边"要靠人自己新建文件夹，十有八九放错层。
    f'{PROJECT_DIRNAME}/{PROJECT_PLACEHOLDER}',
    EDITION_MARKER,
    GUIDE_NAME,
)

#: 包内**绝对不能出现**的东西（用户明确要求去 VSCode；tmp 是 SDK 作者自己的
#: 测试工程；log.txt 是打包机器的运行日志）。收尾自检 `check_forbidden` 用。
FORBIDDEN_STAGE = (
    f'{RUNTIME_DIRNAME}/vscode',
    f'{RUNTIME_DIRNAME}/tmp',
    f'{RUNTIME_DIRNAME}/log.txt',
)

#: 复制 SDK 时**只在 SDK 根目录**排除的名字（不是全局排除：SDK 内部可能也有
#: 合法叫 `tmp` 的子目录，一刀切会切掉运行时依赖）。
SDK_EXCLUDE_DIRS = frozenset({'vscode', 'tmp'})
SDK_EXCLUDE_FILES = frozenset({'log.txt'})

#: zip 硬上限：4 GiB（用户拍板的包体上限；超过就构建失败，不发出坏包）。
MAX_ZIP_BYTES = 4 * 1024 ** 3
#: zip 压缩级别。SDK 里大量 .py/.dll/字体，6 是体积与耗时的平衡点；
#: 再往上（9）对这类数据收益很小、打包时间却翻倍。
ZIP_COMPRESS_LEVEL = 6

#: `--sdk` 未显式给出时的默认 SDK 路径（作者机器上的 SDK 装法）。
DEFAULT_SDK_PATH = Path(r'D:\software\renpy-8.4.1-sdk')
#: 用环境变量覆盖默认 SDK 路径（构建机/CI 用，不写死在脚本参数里）。
SDK_ENV_VAR = 'STORYGRAPH_RENPY_SDK'
#: StoryGraph 版本号的来源文件（相对仓库根）。
VERSION_SOURCE = 'storygraph/__init__.py'


# ---------------------------------------------------------------------------
# 异常：全都能被 main 捕获，打印一句人话后返回 1（不甩 traceback 给打包的人）
# ---------------------------------------------------------------------------

class StageReleaseError(RuntimeError):
    """演出专用版打包失败的基类（输入缺失、清单不符、zip 超限都归它）。"""


class StageInputError(StageReleaseError):
    """输入不成立：--dist/--sdk 目录不存在、说明文档缺失、预设缺失。"""


class StageManifestError(StageReleaseError):
    """铺完之后 REQUIRED_STAGE 缺项（清单自检的第一半：防「缺」）。"""


class StageForbiddenError(StageReleaseError):
    """铺完之后 FORBIDDEN_STAGE 出现（清单自检的第二半：防「多」）。"""


class StageZipTooLargeError(StageReleaseError):
    """zip 超过 MAX_ZIP_BYTES（4GB）上限。"""


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

_VC_VERSION_RE = re.compile(
    r"^\s*version\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_VERSION_RE = re.compile(
    r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
#: 包名里不允许出现的字符（Windows 文件名非法字符 + 路径分隔符）。
_BAD_NAME_CHARS = frozenset('\\/:*?"<>|')


def resolve_against(value, base) -> Path:
    """
    把 `value` 解析成路径：绝对路径原样，相对路径按 `base` 解析。

    与 `make_release.py` 的语义一致：`--dist dist/StoryGraph` 这种默认值
    永远相对**仓库根**（`--project-root`），不依赖当前 shell 在哪。
    """
    path = Path(value)
    return path if path.is_absolute() else Path(base) / path


def read_version(project_root=ROOT) -> str:
    """
    从 `storygraph/__init__.py` 读 `__version__`（当前 "1.0.0"）。

    用正则读文件而不是 `import storygraph`：打包脚本不应该因为运行时代码
    有语法错误/依赖缺失就起不来 —— 版本号是纯文本事实。
    """
    path = Path(project_root) / VERSION_SOURCE
    if not path.is_file():
        raise StageInputError(f'读不到版本号来源：{path}')
    match = _VERSION_RE.search(path.read_text(encoding='utf-8'))
    if match is None:
        raise StageReleaseError(
            f'{path} 里找不到 `__version__ = "..."`，拒绝用空版本号出包')
    return match.group(1).strip()


def default_sdk_path() -> Path:
    """
    `--sdk` 的默认值：先看环境变量 `STORYGRAPH_RENPY_SDK`，否则用
    `D:/software/renpy-8.4.1-sdk`。

    为什么不扫描常见目录猜一个：猜错了会把一个**半坏**的 SDK 打进包里，
    验收时才发现缺 `lib/python3.12/`；宁可在一开始就明确报「SDK 路径不对」。
    """
    env = (os.environ.get(SDK_ENV_VAR) or '').strip()
    if env:
        return Path(env)
    return DEFAULT_SDK_PATH


def normalize_date(value=None) -> str:
    """
    把 `--date` 规整成 `YYYY-MM-DD`；`None`/空字符串 = 今天。

    接受 `datetime.date`（测试注入用）或字符串；字符串必须是严格的
    `YYYY-MM-DD`（`2026-1-2` 这种也拒绝 —— 包名是给人看、给脚本解析的，
    宽度必须固定）。
    """
    if value is None or value == '':
        return date.today().isoformat()
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != text:
        raise StageReleaseError(
            f'--date 必须是 YYYY-MM-DD（例如 2026-10-29），收到：{value!r}')
    return text


def _validate_name_token(value, field: str) -> str:
    """校验要拼进包名的 token（版本号）：不能为空、不能带路径/非法字符。"""
    text = str(value).strip()
    if not text:
        raise StageReleaseError(f'{field} 不能为空')
    if any(ch in _BAD_NAME_CHARS or ch.isspace() for ch in text):
        raise StageReleaseError(
            f'{field} 含有不能进包名的字符：{value!r}（会破坏目录名/zip 名）')
    if text in ('.', '..'):
        raise StageReleaseError(f'{field} 不能是 {text!r}')
    return text


def build_package_name(version, date_str) -> str:
    """包名/目录名：`StoryGraph-Stage-<version>-win64-<YYYY-MM-DD>`。"""
    version = _validate_name_token(version, 'version')
    date_str = normalize_date(date_str)
    return f'{PACKAGE_PREFIX}-{version}-{PLATFORM_TAG}-{date_str}'


def read_sdk_version(sdk_dir) -> str:
    """
    读 SDK 自己的 Ren'Py 版本，用于写进「演出专用版.txt」。

    优先 `renpy/vc_version.py` 的 `version = '...'`（官方 SDK 都带），
    其次 `update/version.json`，最后退回 SDK 目录名（例如
    `renpy-8.4.1-sdk`）—— **不硬编码版本号**，SDK 升级后标记文件自动跟着变。
    """
    sdk_dir = Path(sdk_dir)
    vc_version = sdk_dir / 'renpy' / 'vc_version.py'
    if vc_version.is_file():
        match = _VC_VERSION_RE.search(
            vc_version.read_text(encoding='utf-8', errors='replace'))
        if match is not None:
            return match.group(1).strip()

    update_json = sdk_dir / 'update' / 'version.json'
    if update_json.is_file():
        try:
            data = json.loads(update_json.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            sdk_info = data.get('sdk')
            candidates = []
            if isinstance(sdk_info, dict):
                candidates.append(sdk_info.get('renpy_version'))
            candidates.append(data.get('renpy_version'))
            for candidate in candidates:
                if candidate:
                    return str(candidate)
    return sdk_dir.name


def human_bytes(num: int) -> str:
    """给人看的大小：B / KB / MB / GB（保留 1 位小数）。"""
    size = float(num)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.1f} {unit}' if unit != 'B' else f'{int(size)} B'
        size /= 1024
    return f'{size:.1f} GB'


def short_sha256(path, length: int = 12) -> str:
    """文件 sha256 的前 `length` 位（写进来源指纹；不用于安全用途）。"""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()[:length]


# ---------------------------------------------------------------------------
# 目录自检：防「缺」+ 防「多」
# ---------------------------------------------------------------------------

def check_manifest(staging) -> list:
    """
    返回包目录里**缺失**的 REQUIRED_STAGE 项（空列表 = 齐了）。

    目录项（结尾带 `/`）用 `is_dir()`，文件用 `is_file()` —— 一个叫
    `renpy/` 的空文件不算「有 renpy 运行时」。这里**不抛异常**，由调用方
    决定是失败退出还是打印报告（测试也直接用它看缺了什么）。
    """
    staging = Path(staging)
    missing = []
    for rel in REQUIRED_STAGE:
        path = staging / rel.rstrip('/')
        ok = path.is_dir() if rel.endswith('/') else path.is_file()
        if not ok:
            missing.append(rel)
    return missing


def check_forbidden(staging) -> list:
    """
    返回包目录里**不该有**的 FORBIDDEN_STAGE 项（空列表 = 干净）。

    与 `check_manifest` 互补：那个防「缺」，这个防「多」。VSCode 和 tmp
    就是几千个文件、几百 MB，缺了/多了都不会影响 exe 启动，靠肉眼看 zip
    才发现成本太高，所以必须变成自动判据。
    """
    staging = Path(staging)
    return [rel for rel in FORBIDDEN_STAGE if (staging / rel).exists()]


def require_stage(staging) -> Path:
    """清单自检：缺项抛 `StageManifestError`，禁项抛 `StageForbiddenError`。"""
    staging = Path(staging)
    missing = check_manifest(staging)
    if missing:
        raise StageManifestError(
            '演出专用版缺东西，拒绝出包：\n  ' + '\n  '.join(missing)
            + f'\n（清单定义见 packaging/make_stage_release.py::REQUIRED_STAGE）')
    forbidden = check_forbidden(staging)
    if forbidden:
        raise StageForbiddenError(
            '演出专用版里有绝对不能发的东西，拒绝出包：\n  '
            + '\n  '.join(forbidden)
            + '\n（用户明确要求不带 vscode/、tmp/；log.txt 是打包机器的日志）')
    return staging


# ---------------------------------------------------------------------------
# 生成包内的小文件（预设 README / 版本标记）
# ---------------------------------------------------------------------------

def preset_readme_text(*, version: str, date_str: str) -> str:
    """
    `项目预设/README.md` 的正文。

    这份 README 是**给策划看的**：说明两个 json 从哪来、什么时候会被自动
    套用、补到项目的哪个位置、以及为什么不能随手改。措辞尽量不用
    「清单/序列化」这类词。

    ⚠ 补位落点必须与运行时 `storygraph.app.stage_edition` 的
    `RULES_WRITE` / `LAYOUT_WRITE` / `*_TARGETS` 一致（rules 在项目根，
    layout 在 `<项目>/.storygraph/layout.json`）—— 两边说法不一致时，
    策划按 README 手动放文件会放错地方。
    """
    return (
        '# 项目预设（演出专用版）\n'
        '\n'
        '这两个文件是「演出专用版」随包携带的**默认预设**，来源是 StoryGraph\n'
        f'仓库的 `{PRESET_SOURCE_DIR}/`，由 `packaging/make_stage_release.py`\n'
        '原样复制进包，不是运行时生成的。\n'
        '\n'
        '- `storygraph.rules.json`：真实项目在用的规则清单'
        '（自定义语句的形状 / 表格列 / 分组）\n'
        '- `layout.json`：真实项目的节点布局（图上的节点位置）\n'
        '\n'
        '## 什么时候自动套用\n'
        '\n'
        '用「演出专用版」打开项目时，如果项目里还**没有**下面这份配置，\n'
        '工具会把预设补进去，让第一次打开就能看到完整的语句分栏和版式：\n'
        '\n'
        '- 规则清单 → 项目根 `storygraph.rules.json`\n'
        '  （项目根或 `game/` 下已有 `storygraph.rules.json` 就算已有，不补）\n'
        '- 版式 → `<项目>/.storygraph/layout.json`\n'
        '  （已有 `storygraph_layout.json` 的旧项目也算已有，不补）\n'
        '\n'
        '只**补缺**，绝不覆盖：项目里已经有同名文件时一个字都不动\n'
        '（连修改时间都不碰）—— 那两个文件是项目和用户的真源，\n'
        '预设只在「还没有」的时候补位。\n'
        '\n'
        '想手动用也可以：rules 复制到项目根，layout 复制到\n'
        '`<项目>/.storygraph/layout.json`。\n'
        '\n'
        f'预设版本：StoryGraph {version} · 打包日期：{date_str}\n'
    )


def write_preset_readme(path, *, version: str, date_str: str) -> Path:
    """把 `preset_readme_text()` 写到 `项目预设/README.md`。"""
    path = Path(path)
    path.write_text(preset_readme_text(version=version, date_str=date_str),
                    encoding='utf-8')
    return path


def project_placeholder_text() -> str:
    """`项目/把项目放在这里.txt` 的正文：写给第一次用的人，越短越不像说明书。"""
    return (
        '把「完整项目文件夹」放在这个目录里（整个文件夹拖进来，'
        '文件夹里能看到 game/）。\n'
        '\n'
        '放好之后双击上一层的 StoryGraph.exe：\n'
        '  · 会自动打开它；\n'
        '  · 项目里缺规则 / 布局时，会用随包的项目预设补上（只补缺，不覆盖）；\n'
        '  · 卡片页工具栏的「🎮 引擎试玩」能真跑这一章的演出。\n'
        '\n'
        '这个文件可以删；`项目/` 这个目录名请保留（工具靠它找项目）。\n'
    )


def write_project_placeholder(package_dir) -> Path:
    """
    在包里留一个空 `项目/` 位 + 一行说明。

    为什么需要：用户拿到的 zip 里本来没有 `项目/`，让策划自己"在 exe 旁边
    新建一个文件夹再把项目拖进去"，十有八九会变成
    `项目/项目/…`、`项目/game/` 放成 `game/` 各种层级 —— 而工具找错一层就
    不认。留好位置 + 一行字，比说明书写三遍都管用。
    """
    folder = Path(package_dir) / PROJECT_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PROJECT_PLACEHOLDER
    path.write_text(project_placeholder_text(), encoding='utf-8')
    return path


def edition_marker_text(*, version: str, date_str: str, sdk_version: str,
                        sdk_source, dist_source, preset_dir) -> str:
    """
    `演出专用版.txt` 的正文：版本 / 日期 / SDK 版本 / 来源 / 预设指纹。

    「来源指纹」是两份真实预设 json 的 sha256 前 12 位 —— 以后用户报
    「预设不对」，一对指纹就能确认他手里是哪一个版本打出来的，不用猜。
    """
    preset_dir = Path(preset_dir)
    lines = [
        'StoryGraph 演出专用版（Stage Edition）',
        '======================================',
        '',
        f'版本：{version}',
        f'打包日期：{date_str}',
        f'Ren\'Py SDK 版本：{sdk_version}',
        f'SDK 来源：{sdk_source}',
        f'  （随包放在 {RUNTIME_DIRNAME}/；已去掉 vscode/、tmp/、log.txt）',
        f'程序来源：{dist_source}',
        '',
        '项目预设指纹：',
    ]
    for name in PRESET_FILES:
        path = preset_dir / name
        digest = short_sha256(path) if path.is_file() else '缺失'
        lines.append(f'  {name}  sha256={digest}')
    lines.extend([
        '',
        '这个包自带完整 Ren\'Py SDK：演出预览 / 构建不需要另外安装 Ren\'Py、',
        'VSCode 或 Python。第一次使用先看同目录的「演出专用说明.md」。',
        '',
    ])
    return '\n'.join(lines)


def write_edition_marker(path, *, version: str, date_str: str,
                         sdk_version: str, sdk_source, dist_source,
                         preset_dir) -> Path:
    """把版本/来源标记写进包根，文件名固定为 `EDITION_MARKER`。"""
    path = Path(path)
    path.write_text(
        edition_marker_text(version=version, date_str=date_str,
                            sdk_version=sdk_version, sdk_source=sdk_source,
                            dist_source=dist_source, preset_dir=preset_dir),
        encoding='utf-8')
    return path


# ---------------------------------------------------------------------------
# 复制
# ---------------------------------------------------------------------------

def make_sdk_ignore(sdk_root):
    """
    `shutil.copytree(ignore=...)` 用的过滤器：只在 **SDK 根目录**排除
    `vscode/`、`tmp/`、`log.txt`。

    为什么只在根目录：`tmp` / `log.txt` 这种名字在 SDK 深层也可能出现，
    全局按名字过滤会切掉引擎运行时依赖；用户要去的其实就那三样。

    这里不限制文件个数、不跳过 >N MB 的大文件：Ren'Py 的
    `lib/python3.12/`（标准库 + 依赖 DLL）、`renpy/common/`（字体/主题）
    都得在。缺一个大文件的表现往往是「预览能开、一构建就炸」，最难查。
    """
    root = Path(sdk_root).resolve()

    def _ignore(directory, names):
        if Path(directory).resolve() != root:
            return set()
        skip = set()
        for name in names:
            folded = name.casefold()
            if folded in SDK_EXCLUDE_DIRS or folded in SDK_EXCLUDE_FILES:
                skip.add(name)
        return skip

    return _ignore


def preflight_inputs(dist_dir, sdk_dir, project_root=ROOT) -> None:
    """
    复制前先把输入检查一遍：目录在不在、说明文档/预设在不在。

    为什么要在复制前：真 SDK 有 1.3GB，铺到一半才发现 `docs/演出专用说明.md`
    不存在（或预设缺一份），白等几十秒还留下半个目录。输入问题一律
    `StageInputError`，错误信息里带完整路径 —— 打包的人一眼知道少了什么。
    """
    dist = Path(dist_dir)
    sdk = Path(sdk_dir)
    root = Path(project_root)

    if not dist.is_dir():
        raise StageInputError(
            f'找不到 PyInstaller 产物目录：{dist}\n'
            f'先跑 packaging/build_exe.ps1（或手动 PyInstaller）再来组装。')
    if not sdk.is_dir():
        raise StageInputError(
            f'找不到 Ren\'Py SDK 目录：{sdk}\n'
            f'用 --sdk 指定，或设置环境变量 {SDK_ENV_VAR}。')

    # dist 是「StoryGraph 自己的产物」，SDK 一律由 --sdk 复制。如果 dist 里
    # 已经躺着 renpy_sdk/（比如误把上一次的演出包当 dist），copytree 会抛
    # FileExistsError；这里提前给一句人话，免得打包的人对着 traceback 猜。
    if (dist / RUNTIME_DIRNAME).exists():
        raise StageInputError(
            f'--dist 里已经有 {RUNTIME_DIRNAME}/：{dist / RUNTIME_DIRNAME}\n'
            f'演出专用版的 SDK 由本脚本从 --sdk 复制，请传一个不含它的 dist 目录。')

    guide = root / GUIDE_SOURCE
    if not guide.is_file():
        raise StageInputError(
            f'缺少演出专用说明：{guide}\n'
            f'这份文档是给策划看的随包说明书，不能静默跳过（先把它写出来）。')

    for name in PRESET_FILES:
        preset = root / PRESET_SOURCE_DIR / name
        if not preset.is_file():
            raise StageInputError(f'缺少项目预设：{preset}')


def _copy_presets(project_root, staging, *, version: str, date_str: str) -> Path:
    """把真实项目预设两份 json + 生成的 README 放进 `项目预设/`。"""
    root = Path(project_root)
    preset_dir = Path(staging) / PRESET_DIRNAME
    preset_dir.mkdir(parents=True, exist_ok=True)
    for name in PRESET_FILES:
        shutil.copy2(root / PRESET_SOURCE_DIR / name, preset_dir / name)
    write_preset_readme(preset_dir / 'README.md',
                        version=version, date_str=date_str)
    return preset_dir


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def stage_package(dist_dir, sdk_dir, out_dir, *, project_root=ROOT,
                  version=None, date=None) -> Path:
    """
    铺演出专用版包目录，返回包目录路径。

    步骤：输入预检 → 算包名 → （已存在则先删旧包）→ 复制 dist →
    复制 SDK（排除 vscode/tmp/log.txt）→ 项目预设 → 说明文档 → 版本标记 →
    清单自检（缺项/禁项都抛错）。

    幂等：同名包目录已存在时**整体删掉重建**，保证不残留上一次构建的文件。
    失败时把这次铺到一半的包目录也清掉，不留下「看起来像成品」的东西。
    """
    root = Path(project_root)
    dist = Path(dist_dir)
    sdk = Path(sdk_dir)
    out = Path(out_dir)

    version = _validate_name_token(version or read_version(root), 'version')
    date_str = normalize_date(date)
    package_name = build_package_name(version, date_str)

    preflight_inputs(dist, sdk, root)

    if out.exists() and not out.is_dir():
        raise StageInputError(f'--out 不是目录：{out}')
    out.mkdir(parents=True, exist_ok=True)
    staging = out / package_name

    # 幂等：旧包整个删掉再铺，否则上次的 vscode/ 之类会残留（用户明确不要）。
    if staging.exists():
        if staging.is_dir():
            shutil.rmtree(staging)
        else:
            staging.unlink()

    try:
        # 1) dist 原样（exe / _internal / demo / 快速上手.md ... 都从这里来）。
        shutil.copytree(dist, staging)
        # 2) 完整 SDK，只排除三样（见 make_sdk_ignore）。
        shutil.copytree(sdk, staging / RUNTIME_DIRNAME,
                        ignore=make_sdk_ignore(sdk))
        # 3) 真实项目预设 + 自动套用说明。
        preset_dir = _copy_presets(root, staging,
                                   version=version, date_str=date_str)
        # 3.5) 留好"收到的项目"的位置（`项目/` + 一行说明）。
        write_project_placeholder(staging)
        # 4) 给策划的说明书（源文件缺了 preflight 已经拦过）。
        shutil.copy2(root / GUIDE_SOURCE, staging / GUIDE_NAME)
        # 5) 版本/日期/SDK 版本/来源指纹。
        write_edition_marker(
            staging / EDITION_MARKER,
            version=version, date_str=date_str,
            sdk_version=read_sdk_version(sdk),
            sdk_source=sdk, dist_source=dist, preset_dir=preset_dir)

        # 6) 收尾自检：缺一样、多一样都拒绝出包。
        require_stage(staging)
    except BaseException:
        # 清理半成品：一个「缺了 renpy.exe 的包目录」比没有包目录更危险。
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def make_zip(package_dir, zip_path=None, *, root_name=None,
             max_bytes=None) -> Path:
    """
    把包目录压成 zip，返回 zip 路径。

    · 条目一律以 `<根目录名>/` 开头（解压不撒一地）；
    · zip 放在包目录**外面**（默认 `<out>/<包名>.zip`），否则重跑时会把
      上一次的 zip 一起压进去；
    · 压缩级别 `ZIP_COMPRESS_LEVEL`（6），打印大小 + 耗时；
    · 结尾断言 `size <= MAX_ZIP_BYTES`（4GB），超限就删掉半成品并抛
      `StageZipTooLargeError` —— 不把超限包留在磁盘上等人误发。
    """
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        raise StageInputError(f'要压 zip 的包目录不存在：{package_dir}')
    zip_path = (Path(zip_path) if zip_path is not None
                else package_dir.parent / f'{package_dir.name}.zip')
    root_name = str(root_name or package_dir.name)
    limit = MAX_ZIP_BYTES if max_bytes is None else int(max_bytes)

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    started = time.perf_counter()
    count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED,
                         compresslevel=ZIP_COMPRESS_LEVEL) as archive:
        for path in sorted(package_dir.rglob('*')):
            if not path.is_file():
                continue
            rel = path.relative_to(package_dir).as_posix()
            archive.write(str(path), f'{root_name}/{rel}')
            count += 1
    elapsed = time.perf_counter() - started
    size = zip_path.stat().st_size

    if size > limit:
        zip_path.unlink(missing_ok=True)
        raise StageZipTooLargeError(
            f'zip 超过 {human_bytes(limit)} 上限（实际 {human_bytes(size)}）：'
            f'{zip_path.name}\n'
            f'（MAX_ZIP_BYTES = {limit}；先确认 SDK 里没有漏排 vscode/tmp）')

    print(f'zip：{zip_path}（{human_bytes(size)}，{count} 个文件，'
          f'压缩级别 {ZIP_COMPRESS_LEVEL}，耗时 {elapsed:.1f} 秒）')
    return zip_path


def _dir_stats(root) -> tuple:
    """(文件数, 总字节数)；用于构建结束打印，不做任何过滤。"""
    files = 0
    total = 0
    for path in Path(root).rglob('*'):
        if path.is_file():
            files += 1
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return files, total


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='组装「演出专用版」发布包（StoryGraph + 完整 Ren\'Py SDK）')
    parser.add_argument(
        '--project-root', default=str(ROOT),
        help='仓库根（默认脚本所在仓库；测试可注入一个假仓库）')
    parser.add_argument(
        '--dist', default='dist/StoryGraph',
        help='PyInstaller onedir 产物目录（默认 dist/StoryGraph，相对 --project-root）')
    parser.add_argument(
        '--sdk', default=None,
        help=f'Ren\'Py SDK 根目录；默认读环境变量 {SDK_ENV_VAR}，'
             f'再否则 D:/software/renpy-8.4.1-sdk')
    parser.add_argument(
        '--out', default='dist/stage',
        help='输出父目录（默认 dist/stage，相对 --project-root）')
    parser.add_argument(
        '--date', default=None,
        help='打包日期 YYYY-MM-DD（默认今天；测试用固定日期）')
    parser.add_argument(
        '--version', default=None,
        help='覆盖版本号（默认读 storygraph/__init__.py 的 __version__）')
    parser.add_argument(
        '--zip', action='store_true',
        help='同时压 zip（默认只铺目录）')
    parser.add_argument(
        '--stage-only', action='store_true',
        help='等价于不打 zip（保留这个开关是为了和 make_release.py 的命令行习惯一致）')
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """命令行入口：失败返回 1（并给一句人话），成功返回 0。"""
    args = parse_args(argv)
    root = Path(args.project_root)
    dist = resolve_against(args.dist, root)
    out = resolve_against(args.out, root)
    sdk = resolve_against(args.sdk if args.sdk else default_sdk_path(), root)

    started = time.perf_counter()
    try:
        staging = stage_package(
            dist, sdk, out,
            project_root=root, version=args.version, date=args.date)
        date_str = normalize_date(args.date)
        version = args.version or read_version(root)

        files, total = _dir_stats(staging)
        sdk_files, sdk_total = _dir_stats(staging / RUNTIME_DIRNAME)
        print(f'版本：{version} · 打包日期：{date_str}')
        print(f'包目录：{staging}')
        print(f'  {files} 个文件（{human_bytes(total)}）；'
              f'其中 {RUNTIME_DIRNAME}/ {sdk_files} 个文件'
              f'（{human_bytes(sdk_total)}）')
        print(f'  Ren\'Py SDK：{read_sdk_version(sdk)}（来源：{sdk}）')
        print(f'清单自检：{len(REQUIRED_STAGE)}/{len(REQUIRED_STAGE)} 齐全；'
              f'禁项 {len(FORBIDDEN_STAGE)} 项均不在包内')

        if args.zip and not args.stage_only:
            make_zip(staging)
        elif args.stage_only:
            print('（--stage-only：只铺目录，没有压 zip）')
        else:
            print('（未压 zip；要出可发布的 zip 加 --zip）')

        print(f'总耗时 {time.perf_counter() - started:.1f} 秒')
        return 0
    except StageReleaseError as exc:
        print(f'\n演出专用版打包失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
