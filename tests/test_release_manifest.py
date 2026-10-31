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
    """一个最小的"仓库形状"：LICENSE + 说明文档 + 手册截图 + demo（带垃圾文件）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / 'LICENSE').write_text('MIT License\n', encoding='utf-8')
    # 规则清单 / 映射表的来源（`collect_extras` 从 `storygraph/presets/` 取；
    # 映射表是 2026-11-01 随 rules 一起发的，见契约 §82）
    presets = root / 'storygraph' / 'presets'
    presets.mkdir(parents=True)
    (presets / 'storygraph.rules.json').write_text(
        '{"statements": []}\n', encoding='utf-8')
    (presets / 'storygraph.mapping.json').write_text(
        '{"schema": "storygraph.mapping/1"}\n', encoding='utf-8')
    docs = root / 'docs'
    docs.mkdir()
    (docs / '快速上手_3分钟.md').write_text('# 上手\n', encoding='utf-8')
    (docs / '新手快速操作手册.md').write_text('# 手册\n', encoding='utf-8')
    (docs / 'manual_img').mkdir()
    for name in ('01_structure.png', '02_card.png'):
        (docs / 'manual_img' / name).write_bytes(b'\x89PNG\r\n\x1a\n')
    demo = root / 'demo'
    (demo / 'game' / 'script').mkdir(parents=True)
    (demo / 'game' / 'script' / 'ch01.rpy').write_text(
        'label ch01:\n    return\n', encoding='utf-8')
    (demo / 'game' / 'images' / 'bg').mkdir(parents=True)
    (demo / 'game' / 'images' / 'bg' / 'bg_classroom.png').write_bytes(
        b'\x89PNG\r\n\x1a\n')
    (demo / '剧本表_ch01.xlsx').write_bytes(b'fake-xlsx')
    (demo / 'collab.db').write_bytes(b'user db')            # 本机数据，不发
    (demo / '_import_tmp.xlsx').write_bytes(b'tmp')         # 中间产物，不发
    (demo / 'build').mkdir()
    (demo / 'build' / 'junk.rpy').write_text('junk', encoding='utf-8')
    # ⚠ 2026-10-28 的三个真实现场（原来只测了 `build/`，于是它们全漏了）：
    #    产物目录叫 `build_out` / `build_renpy` / `build_unity`，
    #    开发者脚本叫 `make_art.py` / `make_demo.py`，演示残留 `协作/`。
    (demo / 'build_out').mkdir()
    (demo / 'build_out' / 'story_ir.json').write_text('{}', encoding='utf-8')
    (demo / 'build_renpy').mkdir()
    (demo / 'build_renpy' / 'story.generated.rpy').write_text(
        'label x:\n    return\n', encoding='utf-8')
    (demo / 'make_demo.py').write_text('print(1)\n', encoding='utf-8')
    (demo / '协作').mkdir()
    (demo / '协作' / 'ch01_frontend_alice.xlsx').write_bytes(b'fake-xlsx')
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
    # **前缀**匹配：`build` / `build_out` / `build_renpy` / `build_unity` 全挡
    assert not any(f.startswith('build') for f in files), (
        f'把构建产物打进去了：{[f for f in files if f.startswith("build")]}')
    assert 'make_demo.py' not in files, '把开发者重建 demo 的脚本打进去了'
    assert not any(f.startswith('协作/') for f in files), (
        '把协作演示残留打进去了（没有 collab.db，那张表是孤儿）')


def test_staging_rejects_anything_that_should_not_ship(tmp_path):
    """
    ⭐ 反方向自检：`check_forbidden` 必须挡住"多了"。

    2026-10-28 的真实现场：demo 里混进 8 个构建产物 + 2 个开发者脚本，
    exe 双击照样能开、selfcheck 照样过 —— 只有点开 zip 看才发现。
    所以判据不能只有"缺没缺"，必须有"多没多"。
    """
    root = _fake_project(tmp_path / 'proj')
    dist = _fake_dist(root)
    extras = MR.collect_extras(root, workdir=tmp_path / 'work')
    out = MR.stage(dist, tmp_path / 'release', extras)
    assert MR.check_forbidden(out) == [], MR.check_forbidden(out)

    # 手工再往里扔一份"不该发"的东西 —— 自检必须看得见
    junk = out / 'demo' / 'build_renpy'
    junk.mkdir(parents=True)
    (junk / 'story.generated.rpy').write_text('label x:\n', encoding='utf-8')
    (out / 'demo' / 'make_art.py').write_text('print(1)\n', encoding='utf-8')
    hits = MR.check_forbidden(out)
    assert any('build_renpy' in h for h in hits), hits
    assert any(h.endswith('make_art.py') for h in hits), hits


def test_extras_have_all_four_groups(tmp_path):
    root = _fake_project(tmp_path / 'proj')
    extras = MR.collect_extras(root, workdir=tmp_path / 'work')
    dests = [rel for _src, rel in extras]
    assert '剧本空表格.xlsx' in dests
    assert '快速上手.md' in dests
    assert '新手快速操作手册.md' in dests, '完整手册没进包 —— 用户遇到问题没有可查的一页'
    assert 'manual_img/02_card.png' in dests, '手册引用的截图没进包（md 里会是空图）'
    assert 'LICENSE' in dests
    assert 'demo/game/script/ch01.rpy' in dests


def test_program_edition_extras_have_no_demo(tmp_path):
    """2026-10-29「程序用的干净包」：示例不要了，`demo/` 一个文件都不收。"""
    root = _fake_project(tmp_path / 'proj')
    extras = MR.collect_extras(root, workdir=tmp_path / 'work', include_demo=False)
    dests = [rel for _src, rel in extras]
    assert dests, '程序版不能只剩下一堆空目录'
    assert not any(d.startswith('demo/') for d in dests), dests
    # "完全初始化"要留的东西：空表模板 + 能看懂的两份文档 + LICENSE
    assert '剧本空表格.xlsx' in dests
    assert '快速上手.md' in dests
    assert '新手快速操作手册.md' in dests
    assert 'manual_img/02_card.png' in dests
    # 规则清单：没有它，项目自己的语句在界面上就是裸文本（2026-10-29）
    assert 'storygraph.rules.json' in dests
    # 映射表（代号表）：没有它，`B7` 只能一个字母一个字母手填（2026-11-01）
    assert 'storygraph.mapping.json' in dests
    assert 'LICENSE' in dests


def test_program_edition_manifest_does_not_require_demo(tmp_path):
    """程序版清单只查 `REQUIRED_BASE`；两套判据真的是两套。"""
    root = _fake_project(tmp_path / 'proj')
    dist = _fake_dist(root)
    extras = MR.collect_extras(root, workdir=tmp_path / 'work', include_demo=False)
    out = MR.stage(dist, tmp_path / 'release', extras)
    assert not (out / 'demo').exists()
    assert MR.check_manifest(out, MR.REQUIRED_BASE) == []
    assert MR.check_forbidden(out, allow_demo=False) == []
    # 标准版完整清单当然会报"缺 demo" —— 证明不是把 REQUIRED 写松了
    assert 'demo/game/script/ch01.rpy' in MR.check_manifest(out)


def test_program_edition_rejects_a_demo_that_sneaks_back(tmp_path):
    """`--no-demo` 的包如果混回一棵 demo/，反向自检必须看得见。"""
    root = _fake_project(tmp_path / 'proj')
    dist = _fake_dist(root)
    extras = MR.collect_extras(root, workdir=tmp_path / 'work', include_demo=False)
    out = MR.stage(dist, tmp_path / 'release', extras)
    (out / 'demo' / 'game' / 'script').mkdir(parents=True)
    (out / 'demo' / 'game' / 'script' / 'ch01.rpy').write_text(
        'label x:\n', encoding='utf-8')
    hits = MR.check_forbidden(out, allow_demo=False)
    assert 'demo' in hits, hits
    # 同一棵 demo 在"标准版"眼里是合法的：判据按包的类型走，不是一刀切
    assert MR.check_forbidden(out, allow_demo=True) == []


def test_program_edition_cli_end_to_end(tmp_path):
    """`main(--no-demo --stage-only)` 真铺出干净目录（不只测零件）。"""
    root = _fake_project(tmp_path / 'proj')
    dist = _fake_dist(root)
    out = tmp_path / 'release'
    rc = MR.main(['--project-root', str(root), '--dist', str(dist),
                  '--out', str(out), '--no-demo', '--stage-only'])
    assert rc == 0
    assert not (out / 'demo').exists()
    assert (out / 'StoryGraph.exe').is_file()
    assert (out / '剧本空表格.xlsx').is_file()
    assert MR.check_forbidden(out, allow_demo=False) == []


def test_missing_manual_shots_fail_loudly(tmp_path):
    """手册截图缺了就别发：md 里那两行会变成破图，比没有手册更糟。"""
    root = _fake_project(tmp_path / 'proj')
    for p in (root / 'docs' / 'manual_img').glob('*.png'):
        p.unlink()
    with pytest.raises(FileNotFoundError):
        MR.collect_extras(root, workdir=tmp_path / 'work')


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
                '剧本空表格.xlsx', '快速上手.md', '新手快速操作手册.md',
                'manual_img/01_structure.png', 'manual_img/02_card.png',
                'LICENSE', 'demo/剧本表_ch01.xlsx'):
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
    assert 'StoryGraph/新手快速操作手册.md' in names
    assert 'StoryGraph/manual_img/02_card.png' in names
    assert 'StoryGraph/demo/game/script/ch01.rpy' in names
