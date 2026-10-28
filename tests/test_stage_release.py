"""
tests/test_stage_release.py
===========================
「演出专用版」发布包的判据（`packaging/make_stage_release.py`）。

为什么单独一份（2026-10-29，用户拍板做第二个发布版）：
    演出专用版和标准版的差别不是「多几个文件」，而是**把完整 Ren'Py SDK
    塞进包里**（只去掉 vscode/ 与 tmp/）。这类包一旦出错，代价特别高：
      · 少了 `renpy_sdk/renpy.exe` → 策划双击后「演出预览」永远打不开；
      · 漏进 `vscode/` 或 `tmp/` → 包体多出 1GB，而且违背「不要 VSCode」的决定；
      · 漏了说明文档 / 预设 → 策划拿到包不知道从哪下手。
    这些**都不影响 exe 启动**，靠「双击能开」永远发现不了。所以这里把
    REQUIRED_STAGE / FORBIDDEN_STAGE 和 zip 行为钉成判据，少一样/多一样测试先红。

不碰真实 SDK（本机 1.3 GB）也不写仓库的 dist/：用例在 `tmp_path` 里造假 dist +
假 SDK，全部是空/几字节的小文件，跑起来秒级；真实 SDK 只在人工冒烟时跑。
"""

from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))
sys.path.insert(0, str(ROOT / 'tests'))

import make_stage_release as MSR                                # noqa: E402

#: 用例里统一的固定日期（真实构建默认今天，测试必须可复现）。
DATE = '2026-01-02'
#: 假项目里的版本号（与仓库当前 __version__ 无关：读的是假项目自己的文件）。
FAKE_VERSION = '1.0.0'
#: 假 SDK 的 Ren'Py 版本（刻意与目录名 `renpy-9.9.9-sdk` 不同，
#: 证明标记文件读的是 SDK 里的版本信息，不是目录名）。
FAKE_SDK_VERSION = '9.9.9.12345678'
PACKAGE_NAME = f'StoryGraph-Stage-{FAKE_VERSION}-win64-{DATE}'


# ---------------------------------------------------------------------------
# 造假输入：一个「仓库形状」+ 一个假 dist + 一个假 SDK
# ---------------------------------------------------------------------------

def _fake_project(root: Path) -> Path:
    """最小仓库形状：storygraph/__init__.py + 真实预设两份 + 演出专用说明。"""
    story = root / 'storygraph'
    story.mkdir(parents=True)
    (story / '__init__.py').write_text(
        '"""fake storygraph（打包测试用，不参与运行时 import）"""\n'
        f'__version__ = "{FAKE_VERSION}"\n', encoding='utf-8')

    presets = story / 'presets'
    presets.mkdir()
    (presets / 'storygraph.rules.json').write_text(
        '{"statements": []}\n', encoding='utf-8')
    (presets / 'layout.json').write_text(
        '{"version": 2, "nodes": {}}\n', encoding='utf-8')

    docs = root / 'docs'
    docs.mkdir()
    (docs / MSR.GUIDE_NAME).write_text(
        '# 演出专用说明\n\n给文案策划看的一页说明。\n', encoding='utf-8')
    return root


def _fake_dist(root: Path) -> Path:
    """假 PyInstaller onedir 产物：两个 exe + _internal + 已有发布文档/demo。"""
    dist = root / 'dist' / 'StoryGraph'
    dist.mkdir(parents=True)
    for exe in ('StoryGraph.exe', 'StoryGraph-CLI.exe'):
        (dist / exe).write_bytes(b'MZ')
    internal = dist / '_internal'
    internal.mkdir()
    (internal / 'base_library.zip').write_bytes(b'fake-pyz')
    pyside = internal / 'PySide6'
    pyside.mkdir()
    (pyside / 'Qt6Core.dll').write_bytes(b'fake-dll')
    # dist 里「已有内容」要原样带过去，脚本不重复造
    (dist / '快速上手.md').write_text('# 快速上手\n', encoding='utf-8')
    demo = dist / 'demo' / 'game' / 'script'
    demo.mkdir(parents=True)
    (demo / 'ch01.rpy').write_text('label ch01:\n    return\n', encoding='utf-8')
    return dist


def _fake_sdk(root: Path) -> Path:
    """
    假 Ren'Py SDK：保留被清单要的目录和文件，外加必须被排除的 vscode/、tmp/、
    log.txt。文件都很小（含 0 字节文件），但目录形状与真 SDK 一致。
    """
    sdk = root / 'renpy-9.9.9-sdk'
    sdk.mkdir(parents=True)
    (sdk / 'renpy.exe').write_bytes(b'MZ')
    (sdk / 'renpy.py').write_bytes(b'')          # 0 字节：zip 也必须能处理
    (sdk / 'LICENSE.txt').write_text("Ren'Py license\n", encoding='utf-8')
    (sdk / 'log.txt').write_text('打包机日志，不该进包\n', encoding='utf-8')

    renpy = sdk / 'renpy'
    renpy.mkdir()
    (renpy / 'vc_version.py').write_text(
        f"version = '{FAKE_SDK_VERSION}'\nversion_name = 'Test Release'\n",
        encoding='utf-8')
    common = renpy / 'common'
    common.mkdir()
    (common / '00console.rpy').write_text('# console\n', encoding='utf-8')

    lib = sdk / 'lib'
    win64 = lib / 'py3-windows-x86_64'
    win64.mkdir(parents=True)
    (win64 / 'python.exe').write_bytes(b'MZ')
    python312 = lib / 'python3.12'
    python312.mkdir(parents=True)
    (python312 / 'os.py').write_text('# stdlib\n', encoding='utf-8')
    # 「保留大文件」的替身：不是真大，但必须是**内容非空**的文件，
    # 防止哪天有人给 copytree 加了「>N MB 跳过」的优化。
    (python312 / 'big.dll').write_bytes(b'\0' * 1024)

    vscode = sdk / 'vscode' / 'VSCode-win32-x64'
    vscode.mkdir(parents=True)
    (vscode / 'Code.exe').write_bytes(b'MZ')
    tmp = sdk / 'tmp' / 'installer'
    tmp.mkdir(parents=True)
    (tmp / 'junk.bin').write_bytes(b'junk')
    return sdk


@pytest.fixture
def roots(tmp_path):
    """一份可复用的假仓库 / 假 dist / 假 SDK（每个用例独立 tmp_path）。"""
    return {
        'project': _fake_project(tmp_path / 'proj'),
        'dist': _fake_dist(tmp_path / 'build'),
        'sdk': _fake_sdk(tmp_path / 'sdkroot'),
        'tmp': tmp_path,
    }


def _stage(roots, tmp_path, *, date_str: str = DATE) -> Path:
    """跑一次完整「铺目录」，返回包目录路径。"""
    return MSR.stage_package(
        roots['dist'], roots['sdk'], tmp_path / 'out',
        project_root=roots['project'], date=date_str)


def _cli_args(roots, tmp_path, *extra) -> list:
    """CLI 参数（全部绝对路径，避免依赖当前工作目录）。"""
    return ['--project-root', str(roots['project']),
            '--dist', str(roots['dist']),
            '--sdk', str(roots['sdk']),
            '--out', str(tmp_path / 'out'),
            '--date', DATE, *extra]


# ---------------------------------------------------------------------------
# 1. 常量契约：运行时会按这几个名字找路径，不能单方面改名
# ---------------------------------------------------------------------------

def test_contract_names_exposed_for_runtime():
    """
    `RUNTIME_DIRNAME` / `PRESET_DIRNAME` / `EDITION_MARKER` 是打包脚本与
    运行时（engine_preview / UI）共享的契约 —— 这里钉死，改名前先改 runtime。
    """
    assert MSR.RUNTIME_DIRNAME == 'renpy_sdk'
    assert MSR.PRESET_DIRNAME == '项目预设'
    assert MSR.EDITION_MARKER == '演出专用版.txt'
    assert MSR.GUIDE_NAME == '演出专用说明.md'

    required = set(MSR.REQUIRED_STAGE)
    for rel in ('StoryGraph.exe', 'StoryGraph-CLI.exe',
                'renpy_sdk/renpy.exe', 'renpy_sdk/renpy.py',
                'renpy_sdk/renpy/',
                'renpy_sdk/lib/py3-windows-x86_64/',
                'renpy_sdk/lib/python3.12/',
                'renpy_sdk/LICENSE.txt',
                '项目预设/storygraph.rules.json',
                '项目预设/layout.json',
                '项目预设/README.md',
                '演出专用版.txt', '演出专用说明.md'):
        assert rel in required, f'REQUIRED_STAGE 少了契约项 {rel}'

    forbidden = set(MSR.FORBIDDEN_STAGE)
    for rel in ('renpy_sdk/vscode', 'renpy_sdk/tmp', 'renpy_sdk/log.txt'):
        assert rel in forbidden, f'FORBIDDEN_STAGE 少了 {rel}'

    assert MSR.MAX_ZIP_BYTES == 4 * 1024 ** 3, '包体上限必须是 4GiB'


# ---------------------------------------------------------------------------
# 2. 正常构建：清单齐全、SDK 被排除项干净、预设/说明/标记都在
# ---------------------------------------------------------------------------

def test_normal_build_has_complete_layout(roots, tmp_path):
    """正常构建：包目录名对、REQUIRED 全在、dist 已有内容原样带过去。"""
    pkg = _stage(roots, tmp_path)

    assert pkg.name == PACKAGE_NAME
    assert pkg.parent == tmp_path / 'out'
    for rel in MSR.REQUIRED_STAGE:
        path = pkg / rel.rstrip('/')
        if rel.endswith('/'):
            assert path.is_dir(), f'包里少了目录：{rel}'
        else:
            assert path.is_file(), f'包里少了文件：{rel}'

    # dist 里的东西原样带过来（脚本不重复造）
    assert (pkg / 'StoryGraph.exe').is_file()
    assert (pkg / 'StoryGraph-CLI.exe').is_file()
    assert (pkg / '_internal' / 'PySide6' / 'Qt6Core.dll').is_file()
    assert (pkg / '快速上手.md').is_file()
    assert (pkg / 'demo' / 'game' / 'script' / 'ch01.rpy').is_file()

    assert MSR.check_manifest(pkg) == []
    assert MSR.check_forbidden(pkg) == []


def test_project_placeholder_is_written(roots, tmp_path):
    """
    包里要留好 `项目/` 的位置 + 一行给第一次用的人看的说明。

    用户拿到的 zip 里没有 `项目/` 时，"把项目放到 exe 旁边"十有八九会放错
    层级（套两层 / 把 game/ 直接放出来）；留好位置 + 一句话最省事。
    运行时 `find_received_project` 认的第一个名字就是 `项目`。
    """
    pkg = _stage(roots, tmp_path)
    folder = pkg / '项目'
    assert folder.is_dir(), '包里没有留 项目/ 这个位置'
    placeholder = folder / '把项目放在这里.txt'
    assert placeholder.is_file()
    text = placeholder.read_text(encoding='utf-8')
    assert 'game' in text and 'StoryGraph.exe' in text
    assert MSR.check_manifest(pkg) == []


def test_build_never_copies_vscode_tmp_or_log(roots, tmp_path):
    """
    用户明确要求去 VSCode；tmp 是 SDK 作者自己的测试工程；log.txt 是
    打包机器的日志 —— 三样一个都不能出现在包里。
    """
    pkg = _stage(roots, tmp_path)
    sdk_staged = pkg / MSR.RUNTIME_DIRNAME

    assert not (sdk_staged / 'vscode').exists(), 'VSCode 被打进包里了'
    assert not (sdk_staged / 'tmp').exists(), 'SDK 作者的 tmp/ 被打进包里了'
    assert not (sdk_staged / 'log.txt').exists(), '打包机的 SDK 日志被打进包里了'

    # 「该留的」不能一起被误删：renpy/common 与 lib 下的文件都得在
    assert (sdk_staged / 'renpy' / 'common' / '00console.rpy').is_file()
    assert (sdk_staged / 'lib' / 'python3.12' / 'big.dll').is_file()


def test_forbidden_checker_catches_planted_junk(roots, tmp_path):
    """反方向自检：手工往包里塞 vscode/，`require_stage` 必须拒绝。"""
    pkg = _stage(roots, tmp_path)
    (pkg / 'renpy_sdk' / 'vscode').mkdir()
    (pkg / 'renpy_sdk' / 'log.txt').write_text('log', encoding='utf-8')

    hits = MSR.check_forbidden(pkg)
    assert 'renpy_sdk/vscode' in hits
    assert 'renpy_sdk/log.txt' in hits
    with pytest.raises(MSR.StageForbiddenError):
        MSR.require_stage(pkg)


def test_presets_are_real_and_readme_explains_auto_apply(roots, tmp_path):
    """项目预设必须是仓库 `storygraph/presets/` 的真实内容，README 说明自动套用。"""
    pkg = _stage(roots, tmp_path)
    preset_dir = pkg / MSR.PRESET_DIRNAME

    for name in MSR.PRESET_FILES:
        copied = (preset_dir / name).read_text(encoding='utf-8')
        source = (roots['project'] / 'storygraph' / 'presets' / name
                  ).read_text(encoding='utf-8')
        assert copied == source, f'{name} 不是从仓库预设原样复制的'

    readme = (preset_dir / 'README.md').read_text(encoding='utf-8')
    assert 'storygraph/presets' in readme, 'README 没交代预设从哪来'
    assert '自动套用' in readme, 'README 没交代什么时候自动套用'
    assert '只**补缺**' in readme, 'README 没写清只补缺、不覆盖已有文件'
    assert '绝不覆盖' in readme, 'README 没写清已有文件不覆盖（会让人以为会被改）'
    # 落点必须和运行时 stage_edition 的真实写入位置一致，否则策划手动放会放错
    assert 'storygraph.rules.json' in readme
    assert '.storygraph/layout.json' in readme, (
        'README 没写 layout 的真实落点（运行时写 <项目>/.storygraph/layout.json）')


def test_edition_marker_has_version_date_and_fingerprint(roots, tmp_path):
    """演出专用版.txt：版本 / 日期 / SDK 版本 / 来源指纹（预设 sha256）。"""
    pkg = _stage(roots, tmp_path)
    marker = (pkg / MSR.EDITION_MARKER).read_text(encoding='utf-8')

    assert FAKE_VERSION in marker
    assert DATE in marker
    assert FAKE_SDK_VERSION in marker, '没读 SDK 里 renpy/vc_version.py 的版本'
    assert 'sha256=' in marker, '来源指纹没写'
    digest = MSR.short_sha256(pkg / MSR.PRESET_DIRNAME / 'layout.json')
    assert digest in marker, '标记里的预设指纹和实际文件对不上'


def test_stage_guide_is_copied(roots, tmp_path):
    """`docs/演出专用说明.md` 必须随包（源文件缺了直接报错，见下一条用例）。"""
    pkg = _stage(roots, tmp_path)
    assert (pkg / MSR.GUIDE_NAME).is_file()
    assert '给文案策划' in (pkg / MSR.GUIDE_NAME).read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# 3. 缺任何 REQUIRED 项 / 缺说明文档 → 抛错（绝不静默降级成警告）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('case, expected_text', [
    ('dist_exe', 'StoryGraph.exe'),
    ('sdk_renpy_exe', 'renpy.exe'),
    ('preset_layout', 'layout.json'),
])
def test_missing_required_piece_fails_loudly(roots, tmp_path, case, expected_text):
    """
    REQUIRED_STAGE 少一样就失败（挑三类最要命的：exe、引擎、预设）。
    失败后不能留下半个包 —— 否则它会被误当成品发出去。
    """
    if case == 'dist_exe':
        (roots['dist'] / 'StoryGraph.exe').unlink()
    elif case == 'sdk_renpy_exe':
        (roots['sdk'] / 'renpy.exe').unlink()
    elif case == 'preset_layout':
        (roots['project'] / 'storygraph' / 'presets' / 'layout.json').unlink()
    else:                                                     # pragma: no cover
        raise AssertionError(f'未知用例 {case}')

    out = tmp_path / 'out'
    with pytest.raises(MSR.StageReleaseError) as excinfo:
        MSR.stage_package(roots['dist'], roots['sdk'], out,
                          project_root=roots['project'], date=DATE)
    assert expected_text in str(excinfo.value), (
        f'报错没提是哪个东西缺了：{excinfo.value}')
    assert not (out / PACKAGE_NAME).exists(), '构建失败后留下了半个包目录'


def test_missing_stage_guide_fails_loudly(roots, tmp_path):
    """
    `docs/演出专用说明.md` 不存在时**报错**，不是静默跳过。
    这份文档由 Lead/文档作者写；缺了说明的包对策划没有价值。
    """
    (roots['project'] / 'docs' / MSR.GUIDE_NAME).unlink()
    with pytest.raises(MSR.StageReleaseError) as excinfo:
        _stage(roots, tmp_path)
    assert MSR.GUIDE_NAME in str(excinfo.value)
    assert not (tmp_path / 'out' / PACKAGE_NAME).exists()


def test_missing_dist_directory_fails_with_clear_message(roots, tmp_path):
    """`--dist` 指错目录 → 明确报「找不到产物目录」，别让人对着 traceback 猜。"""
    missing = tmp_path / 'no-such-dist'
    with pytest.raises(MSR.StageReleaseError) as excinfo:
        MSR.stage_package(missing, roots['sdk'], tmp_path / 'out',
                          project_root=roots['project'], date=DATE)
    message = str(excinfo.value)
    assert '找不到' in message and str(missing) in message


# ---------------------------------------------------------------------------
# 4. 幂等：同名包目录重跑先清旧包，不残留上一次的文件
# ---------------------------------------------------------------------------

def test_rebuild_clears_old_package(roots, tmp_path):
    """
    行为钉子：同名包目录已存在时**整体删掉重建**。

    不这么做的话，上次构建留下的文件（比如曾经误复制的 vscode/、旧版说明）
    会跟着新包一起发出去 —— 而清单自检只查「缺没缺」，查不出旧文件。
    """
    pkg = _stage(roots, tmp_path)
    stale = pkg / 'stale_from_last_build.txt'
    stale.write_text('上一次构建的残留', encoding='utf-8')

    rebuilt = _stage(roots, tmp_path)                         # 同日期、同 out
    assert rebuilt == pkg
    assert not stale.exists(), '重跑没清旧包：旧文件会跟着新包发出去'
    assert MSR.check_manifest(rebuilt) == []


# ---------------------------------------------------------------------------
# 5. zip：内容、顶层目录、0 字节文件、4GB 上限
# ---------------------------------------------------------------------------

def test_zip_contains_sdk_runtime_and_guide(roots, tmp_path):
    """`--zip` 产物在包目录**外面**；里面能直接看到 SDK 的 renpy.exe 和说明。"""
    pkg = _stage(roots, tmp_path)
    zip_path = MSR.make_zip(pkg)

    assert zip_path == pkg.parent / f'{pkg.name}.zip'
    assert zip_path.is_file() and zip_path.stat().st_size > 0
    assert not (pkg / zip_path.name).exists(), 'zip 被放进包目录了（重跑会越滚越大）'

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert f'{pkg.name}/renpy_sdk/renpy.exe' in names
        assert f'{pkg.name}/演出专用说明.md' in names
        assert f'{pkg.name}/StoryGraph.exe' in names
        assert all(n.startswith(f'{pkg.name}/') for n in names), (
            '条目没有统一顶层目录：解压会撒一地')
        # 0 字节文件也必须进 zip（真 SDK 的 lib 里就有空文件）
        info = archive.getinfo(f'{pkg.name}/renpy_sdk/renpy.py')
        assert info.file_size == 0


def test_zip_size_limit_is_enforced(roots, tmp_path, monkeypatch):
    """zip 超过 MAX_ZIP_BYTES 必须抛错，并把超限的半成品 zip 删掉。"""
    pkg = _stage(roots, tmp_path)
    monkeypatch.setattr(MSR, 'MAX_ZIP_BYTES', 1)              # 1 字节，必超

    with pytest.raises(MSR.StageZipTooLargeError):
        MSR.make_zip(pkg)
    assert not (pkg.parent / f'{pkg.name}.zip').exists(), (
        '超限的 zip 留在磁盘上，容易被误发出去')


# ---------------------------------------------------------------------------
# 6. CLI 端到端（含 --zip / --stage-only / --dist 不存在）
# ---------------------------------------------------------------------------

def test_cli_zip_end_to_end(roots, tmp_path, capsys):
    """一条命令出「目录 + zip」，输出里说清包目录和 zip 大小。"""
    exit_code = MSR.main(_cli_args(roots, tmp_path, '--zip'))
    assert exit_code == 0
    pkg = tmp_path / 'out' / PACKAGE_NAME
    assert pkg.is_dir()
    assert (pkg.parent / f'{PACKAGE_NAME}.zip').is_file()

    out = capsys.readouterr().out
    assert '包目录' in out
    assert 'zip' in out and ('MB' in out or 'KB' in out)


def test_cli_stage_only_skips_zip(roots, tmp_path):
    """`--stage-only` 等价于不打 zip（目录照铺）。"""
    exit_code = MSR.main(_cli_args(roots, tmp_path, '--stage-only'))
    assert exit_code == 0
    assert (tmp_path / 'out' / PACKAGE_NAME).is_dir()
    assert not (tmp_path / 'out' / f'{PACKAGE_NAME}.zip').exists()


def test_cli_missing_dist_fails_with_message(roots, tmp_path, capsys):
    """CLI 的 `--dist` 不存在：退出码 1 + stderr 一句人话（不甩 traceback）。"""
    args = ['--project-root', str(roots['project']),
            '--dist', str(tmp_path / 'nope'),
            '--sdk', str(roots['sdk']),
            '--out', str(tmp_path / 'out'),
            '--date', DATE]
    exit_code = MSR.main(args)
    assert exit_code == 1
    err = capsys.readouterr().err
    assert '找不到' in err and 'nope' in err


# ---------------------------------------------------------------------------
# 7. 工具函数可单测：版本读取、日期、包名、SDK 默认路径
# ---------------------------------------------------------------------------

def test_read_version_from_project(roots):
    """版本号从 `storygraph/__init__.py` 的正则读出来，不 import 运行时。"""
    assert MSR.read_version(roots['project']) == FAKE_VERSION


def test_read_version_missing_or_bad_fails(tmp_path):
    with pytest.raises(MSR.StageReleaseError):
        MSR.read_version(tmp_path / 'empty-project')
    project = tmp_path / 'proj'
    (project / 'storygraph').mkdir(parents=True)
    (project / 'storygraph' / '__init__.py').write_text(
        '# 没有版本号\n', encoding='utf-8')
    with pytest.raises(MSR.StageReleaseError):
        MSR.read_version(project)


def test_date_helpers(tmp_path):
    """--date 接受严格 YYYY-MM-DD / date 对象，缺省是今天。"""
    assert MSR.normalize_date(DATE) == DATE
    assert MSR.normalize_date(date(2026, 1, 2)) == DATE
    assert MSR.normalize_date(None) == date.today().isoformat()

    for bad in ('2026/01/02', '2026-1-2', '20260102', '2026-13-01'):
        with pytest.raises(MSR.StageReleaseError):
            MSR.normalize_date(bad)


def test_package_name_and_version_validation():
    assert MSR.build_package_name(FAKE_VERSION, DATE) == PACKAGE_NAME
    # 版本号会拼进 Windows 目录名：路径分隔符 / 非法字符必须拒绝
    for bad in ('1/0.0', '1\\0.0', '', '1.0.0 beta'):
        with pytest.raises(MSR.StageReleaseError):
            MSR.build_package_name(bad, DATE)


def test_default_sdk_path_uses_env_var(monkeypatch, roots):
    """`--sdk` 默认值：环境变量优先，否则作者机器默认路径。"""
    monkeypatch.setenv(MSR.SDK_ENV_VAR, str(roots['sdk']))
    assert MSR.default_sdk_path() == roots['sdk']

    monkeypatch.delenv(MSR.SDK_ENV_VAR)
    assert MSR.default_sdk_path() == MSR.DEFAULT_SDK_PATH


def test_read_sdk_version_prefers_vc_version(roots):
    """SDK 版本读 `renpy/vc_version.py`（不硬编码、也不靠目录名）。"""
    assert MSR.read_sdk_version(roots['sdk']) == FAKE_SDK_VERSION


def test_read_sdk_version_falls_back_to_dir_name(tmp_path):
    """SDK 里没有版本文件时退回目录名 —— 至少标记文件里有个可读的版本线索。"""
    sdk = tmp_path / 'renpy-8.4.1-sdk'
    sdk.mkdir()
    assert MSR.read_sdk_version(sdk) == 'renpy-8.4.1-sdk'
