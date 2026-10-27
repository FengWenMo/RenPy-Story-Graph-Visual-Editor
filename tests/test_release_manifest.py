"""
tests/test_release_manifest.py
==============================
发布包清单的判据（`packaging/make_release.py`）。

为什么要有它（2026-10-23，竞争分析 P0-1）：上一版 zip 实测
**xlsx = 0 / md = 0 / demo = 0** —— 双击能开，但"打开项目"没东西可开、
从零开始没模板、出问题没有一页说明。这份用例把"该有什么"钉死，
少一样测试先红，而不是等用户拿到包才发现。

不测 exe（PyInstaller 产物不进仓库）：用假 dist 目录验证"铺目录"这一步。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))
sys.path.insert(0, str(ROOT / 'tests'))

import make_release as MR                                   # noqa: E402


def _fake_project(root: Path) -> Path:
    """一个最小的"仓库形状"：LICENSE + 上手说明 + demo（带垃圾文件）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / 'LICENSE').write_text('MIT License\n', encoding='utf-8')
    docs = root / 'docs'
    docs.mkdir()
    (docs / '快速上手_3分钟.md').write_text('# 上手\n', encoding='utf-8')
    demo = root / 'demo'
    (demo / 'game' / 'script').mkdir(parents=True)
    (demo / 'game' / 'script' / 'ch01.rpy').write_text(
        'label ch01:\n    return\n', encoding='utf-8')
    (demo / '剧本表_ch01.xlsx').write_bytes(b'fake-xlsx')
    (demo / 'collab.db').write_bytes(b'user db')            # 本机数据，不发
    (demo / '_import_tmp.xlsx').write_bytes(b'tmp')         # 中间产物，不发
    (demo / 'build').mkdir()
    (demo / 'build' / 'junk.rpy').write_text('junk', encoding='utf-8')
    return root


def _fake_dist(root: Path) -> Path:
    dist = root / 'dist' / 'StoryGraph'
    dist.mkdir(parents=True)
    (dist / 'StoryGraph.exe').write_bytes(b'MZ')
    (dist / 'StoryGraph-CLI.exe').write_bytes(b'MZ')
    (dist / '_internal').mkdir()
    (dist / '_internal' / 'base_library.zip').write_bytes(b'zip')
    return dist


def test_template_is_a_real_workbook(tmp_path):
    """模板必须走真实导出器：自己拼表头的话，用户填了也导不回去。"""
    import openpyxl

    path = MR.write_template(tmp_path / '剧本空表格.xlsx')
    assert path.is_file()
    wb = openpyxl.load_workbook(path)
    assert 'Beats' in wb.sheetnames
    header = [c.value for c in wb['Beats'][1]]
    assert '行号' in header and '台词' in header, header


def test_demo_copy_skips_user_state_and_build_output(tmp_path):
    root = _fake_project(tmp_path / 'proj')
    files = [rel for _src, rel in MR.demo_files(root / 'demo')]
    assert 'game/script/ch01.rpy' in files
    assert '剧本表_ch01.xlsx' in files
    assert 'collab.db' not in files, '把用户的协作库打进发布包了'
    assert '_import_tmp.xlsx' not in files
    assert not any(f.startswith('build/') for f in files), '把构建产物打进去了'


def test_extras_have_all_four_groups(tmp_path):
    root = _fake_project(tmp_path / 'proj')
    extras = MR.collect_extras(root, workdir=tmp_path / 'work')
    dests = [rel for _src, rel in extras]
    assert '剧本空表格.xlsx' in dests
    assert '快速上手.md' in dests
    assert 'LICENSE' in dests
    assert 'demo/game/script/ch01.rpy' in dests


def test_missing_license_fails_loudly(tmp_path):
    root = _fake_project(tmp_path / 'proj')
    (root / 'LICENSE').unlink()
    with pytest.raises(FileNotFoundError):
        MR.collect_extras(root, workdir=tmp_path / 'work')


def test_stage_copies_the_exe_folder_and_extras(tmp_path):
    root = _fake_project(tmp_path / 'proj')
    dist = _fake_dist(root)
    extras = MR.collect_extras(root, workdir=tmp_path / 'work')
    out = MR.stage(dist, tmp_path / 'release', extras)
    for rel in ('StoryGraph.exe', 'StoryGraph-CLI.exe',
                '剧本空表格.xlsx', '快速上手.md', 'LICENSE',
                'demo/剧本表_ch01.xlsx'):
        assert (out / rel).is_file(), f'发布目录少了 {rel}'
    assert not (out / 'demo' / 'collab.db').exists()
    assert MR.check_manifest(out) == []


def test_manifest_reports_what_is_missing(tmp_path):
    out = tmp_path / 'release'
    out.mkdir()
    (out / 'StoryGraph.exe').write_bytes(b'MZ')
    missing = MR.check_manifest(out)
    assert 'StoryGraph-CLI.exe' in missing
    assert '快速上手.md' in missing


def test_license_is_gpl_everywhere_it_matters():
    """LICENSE 文件、pyproject 字段、README 段 —— 三件必须一致（GPLv3）。"""
    import tomllib

    license_text = (ROOT / 'LICENSE').read_text(encoding='utf-8')
    assert 'GNU GENERAL PUBLIC LICENSE' in license_text
    assert 'Version 3' in license_text
    data = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    declared = data['project']['license']
    declared_text = (declared.get('text')
                     if isinstance(declared, dict) else declared)
    assert 'GPL' in declared_text
    assert 'license-files' in data.get('tool', {}).get('setuptools', {})
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert '## License' in readme and 'GPLv3' in readme


def test_zip_has_a_top_level_folder(tmp_path):
    root = _fake_project(tmp_path / 'proj')
    dist = _fake_dist(root)
    extras = MR.collect_extras(root, workdir=tmp_path / 'work')
    out = MR.stage(dist, tmp_path / 'release', extras)
    zip_path = MR.make_zip(out, tmp_path / 'pkg.zip', root_name='StoryGraph')
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    assert names, '空包'
    assert all(n.startswith('StoryGraph/') for n in names), (
        '解压会撒一地 / 顶层目录名不对：条目必须以包名目录开头')
    assert 'StoryGraph/快速上手.md' in names
    assert 'StoryGraph/demo/game/script/ch01.rpy' in names
