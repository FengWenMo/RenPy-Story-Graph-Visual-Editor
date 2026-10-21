"""
判据 · Unity 资产包（2026-10-21，多引擎第三片）
================================================

用户目标：Unity 产物从"两个 JSON + 一份说明"升级成**可直接导入的
`.unitypackage`**，而且**没有 Unity 环境也能验证**。所以这里不测
"Unity 打开长什么样"（本机没有 Unity），只测四件能客观判定的事：

  ① **结构**：gzip + tar、每个 GUID 目录三件套齐全、`guid == 目录名`、
     `pathname` 以 `Assets/` 开头、不重复 —— 坏包要能被 `verify_package`
     定位到具体资产；
  ② **确定性**：同一输入两次导出**逐字节相同**（tar 元数据写死、gzip 头
     不写时间），否则"包变了没有"只能靠人眼看；
  ③ **同源**：包里的 JSON 与导出目录里散落的 JSON **逐字节相同**，
     GUID 由路径派生 → 重导是更新、不是新增；
  ④ **能编译**：包里三个 `.cs` + 一份 `UnityEngine` 桩，用 `dotnet build`
     真的编译（没有 dotnet 就 `skip`，**绝不静默 pass**）。

⚠ 第 ④ 条特意不往包里塞 Unity 工程文件：包只给资产，不猜 Unity 版本
（方案约束）。桩只补"这些脚本用到的 API"，编译过说明代码不是"看起来像
C#"的文本。
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.pipeline.engine_exporters import (            # noqa: E402
    NEUTRAL_IR_NAME, UNITY_DATA_NAME, UNITY_NOTES_NAME, UNITY_PACKAGE_NAME,
    build_neutral_ir, expected_files, export_ir, to_unity, _unity_notes)
from storygraph.pipeline.unity_package import (               # noqa: E402
    PACKAGE_ROOT, build_package, guid_for, package_files, read_package,
    verify_package, write_package)
from storygraph.story.beat_model import Beat, assign_identity  # noqa: E402


# ------------------------------------------------------------------
#  夹具 / 小工具
# ------------------------------------------------------------------

@pytest.fixture
def beats():
    """
    一段最小演出：两拍、一个 label、一句台词。

    够验证"包里的 JSON 与导出目录逐字节同源"（数据有中文、有数组/布尔字段），
    又不引入资源表等无关依赖。
    """
    items = [
        Beat(seq=1, label='ch01', scene='bg classroom', music='bgm_night',
             text='开场'),
        Beat(seq=2, label='ch01', speaker='e', show='eileen happy@leftish',
             text='你好。'),
    ]
    assign_identity(items)
    return items


@pytest.fixture
def exported(tmp_path, beats):
    """真跑一次 `export_ir('unity')`，返回 `(导出目录, 产物路径列表)`。"""
    out = tmp_path / 'unity'
    files = export_ir('unity', beats, out)
    return out, files


def _sample_files() -> dict:
    """给 `build_package` 的最小输入：含中文路径与中文内容。"""
    return {
        'Assets/StoryGraph/Runtime/DialogueData.cs':
            b'public class DialogueData { }',
        'Assets/StoryGraph/Resources/UnityDialogueData.json':
            '{"text": "中文台词"}'.encode('utf-8'),
        'Assets/StoryGraph/文档/说明.md': '# 中文\n这是内容'.encode('utf-8'),
    }


def _members(data: bytes) -> dict:
    """把包拆成 `{tar 条目名: 字节}`（测试自己拆，不复用被测的 read_package）。"""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
        for member in tar:
            if not member.isfile():
                continue
            fh = tar.extractfile(member)
            out[member.name] = fh.read() if fh is not None else b''
    return out


def _repack(entries: dict) -> bytes:
    """
    按被测模块同一条"可复现"规则重新打包。

    为什么要自己重打而不是 `read_package` + `build_package`：破坏性用例
    必须能造出**被测模块自己不会生成**的坏包（改 GUID 目录名、删 meta、
    重复 pathname），走 build_package 会被它自动修好。
    """
    buf = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buf, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w',
                          format=tarfile.USTAR_FORMAT) as tar:
            for name in sorted(entries):
                blob = entries[name]
                info = tarfile.TarInfo(name)
                info.size = len(blob)
                info.mtime = 0
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


def _guid_of(entries: dict, pathname: str) -> str:
    """在（可能被改坏的）tar 条目里按 pathname 找 GUID 目录。"""
    for name, blob in entries.items():
        if name.endswith('/pathname') and blob.decode('utf-8') == pathname:
            return name[:-len('/pathname')]
    raise AssertionError(f'包里找不到 pathname={pathname!r}；'
                         f'现有条目：{sorted(entries)}')


def _guid_map(data: bytes) -> dict:
    """`pathname -> guid`（目录条目也在内；它们同样该有稳定 GUID）。"""
    out = {}
    for name, blob in _members(data).items():
        if name.endswith('/pathname'):
            out[blob.decode('utf-8')] = name.rsplit('/', 1)[0]
    return out


# ------------------------------------------------------------------
#  结构 + 确定性
# ------------------------------------------------------------------

class TestPackageFormat:
    def test_verify_accepts_a_normal_package(self, tmp_path):
        """
        合格包必须**零问题**：这是"判据本身有效"的正面样本。

        没有它，`verify_package` 返回什么都会被下一堆"坏包要报错"的用例
        掩盖（一个永远返回问题的函数也能让所有负面用例变绿）。
        """
        data = build_package(_sample_files())
        assert verify_package(data) == [], verify_package(data)

        # 路径形式与字节形式必须给出同一个结论
        target = tmp_path / 'ok.unitypackage'
        assert write_package(target, _sample_files()) == str(target)
        assert target.is_file()
        assert verify_package(target) == [], verify_package(target)
        # 路径形式与字节形式解出来必须是同一份内容
        assert read_package(target) == _sample_files()

    def test_build_is_byte_for_byte_deterministic(self):
        """
        同一输入两次导出必须**逐字节相同**。

        为什么值得一条判据：团队会把 `.unitypackage` 和章节一起提交进 git。
        如果 gzip 头带当前时间、tar 记录当前用户，那么"只改了一句台词"
        也会让整个二进制文件变脏，review 时根本看不出哪里变了。
        """
        files = _sample_files()
        first = build_package(files)
        second = build_package(files)
        assert first == second, (
            '同一份输入两次 build_package 字节不同 —— gzip 头或 tar 元数据'
            '带上了时间/用户信息')

        # 字典顺序也不许影响结果
        reversed_files = dict(reversed(list(files.items())))
        assert build_package(reversed_files) == first, (
            '输入字典的顺序影响了产物；tar 条目必须按路径排序')

        # gzip 头的 MTIME 字段（第 4~8 字节，小端）必须是 0
        assert struct.unpack('<I', second[4:8])[0] == 0, (
            'gzip 头带时间戳：同一输入两次导出会字节不同')

        # tar 元数据写死（默认会取当前时间 / 当前用户）
        with tarfile.open(fileobj=io.BytesIO(first), mode='r:gz') as tar:
            members = [m for m in tar if m.isfile()]
            assert members
            for m in members:
                assert (m.mtime, m.uid, m.gid, m.uname, m.gname) == \
                    (0, 0, 0, '', ''), (
                    f'tar 条目 {m.name} 的元数据不稳定：'
                    f'mtime={m.mtime} uid={m.uid} gid={m.gid} '
                    f'uname={m.uname!r} gname={m.gname!r}')
            pathnames = [tar.extractfile(m).read().decode('utf-8')
                         for m in members if m.name.endswith('/pathname')]
        assert pathnames == sorted(pathnames), (
            f'tar 条目没有按包内路径排序：{pathnames}')

    def test_round_trip_keeps_chinese_paths_and_bytes(self):
        """
        `read_package(build_package(files)) == files`（含中文路径/内容）。

        这条保证"解包回的内容就是导入 Unity 的内容"：编码一旦在某处
        出错（utf-8 写、gbk 读之类），中文路径/台词会在包里悄悄变样。
        """
        files = _sample_files()
        data = build_package(files)
        assert read_package(data) == files
        # 路径形式走同一条
        assert read_package(data) == _sample_files()

    def test_every_asset_has_three_parts_and_a_folder_meta(self):
        """
        每个 GUID 目录必须是 `asset / asset.meta / pathname` 三件套；
        目录资产要有 `folderAsset: yes` + `DefaultImporter:`。

        为什么目录也要 meta：Unity 把文件夹也当资产。缺目录 meta 时，
        导入会临时分配一个 GUID，重导时目录身份就变了（同一路径出现两份）。
        """
        data = build_package(_sample_files())
        entries = _members(data)

        by_guid: dict = {}
        for name, blob in entries.items():
            guid, _, part = name.partition('/')
            by_guid.setdefault(guid, {})[part] = blob
        assert by_guid
        for guid, parts in by_guid.items():
            assert set(parts) == {'asset', 'asset.meta', 'pathname'}, (
                f'GUID {guid} 的条目不全：{sorted(parts)}')
            assert re.fullmatch(r'[0-9a-f]{32}', guid), guid
            assert f'guid: {guid}'.encode() in parts['asset.meta'], (
                f'{guid}：asset.meta 里的 guid 与目录名不一致')
            assert b'fileFormatVersion: 2' in parts['asset.meta']

        folders = [p for p in by_guid
                   if b'folderAsset: yes' in by_guid[p]['asset.meta']]
        assert folders, '包里的目录没有自己的 meta（folderAsset）'
        for guid in folders:
            meta = by_guid[guid]['asset.meta']
            assert b'DefaultImporter:' in meta, (
                f'目录 {by_guid[guid]["pathname"]!r} 的 importer 不对')
            assert by_guid[guid]['asset'] == b'', '目录的 asset 必须是空字节'

        # 文件资产的 importer 按类型选（.cs / json / md）
        for guid, parts in by_guid.items():
            if b'folderAsset: yes' in parts['asset.meta']:
                continue
            path = parts['pathname'].decode('utf-8')
            if path.endswith('.cs'):
                assert b'MonoImporter:' in parts['asset.meta'], path
            else:
                assert b'TextScriptImporter:' in parts['asset.meta'], path

    def test_failed_write_does_not_leave_temp_or_truncate(self, tmp_path):
        """
        写盘必须"临时文件 + os.replace"：失败时**目标不动、也不留 `.tmp`**。

        半截的 `.unitypackage` 在 Unity 里只报"无法解压"，而人看到的是
        "文件明明在"；残留的 `.tmp` 则会被用户当成另一个产物。
        """
        target = tmp_path / 'bad.unitypackage'
        target.mkdir()                     # 同名目录 → os.replace 一定失败
        with pytest.raises(OSError):
            write_package(target, _sample_files())
        assert target.is_dir(), '失败时把目标动掉了'
        assert not list(tmp_path.glob('*.tmp')), list(tmp_path.glob('*.tmp'))


# ------------------------------------------------------------------
#  verify_package：坏包必须能被**定位**
# ------------------------------------------------------------------

class TestVerifyTamperedPackage:
    """
    每组"手工破坏"都必须让 `verify_package` 返回**非空**，且信息里能
    找到出问题的 GUID / pathname。只断言"非空"是不够的：用户拿着一个
    坏包，需要知道是哪一条资产坏了。
    """

    def test_gid_directory_name_must_match_the_meta(self):
        """目录名与 `asset.meta` 的 guid 不一致 → Unity 当成两份资产。"""
        data = build_package(_sample_files())
        entries = _members(data)
        path = 'Assets/StoryGraph/Runtime/DialogueData.cs'
        old = _guid_of(entries, path)
        new = 'f' * 32 if old != 'f' * 32 else 'e' * 32
        for name in [n for n in entries if n.startswith(old + '/')]:
            entries[new + name[len(old):]] = entries.pop(name)

        problems = verify_package(_repack(entries))
        assert problems, '改坏 GUID 目录名后 verify_package 仍报合格'
        joined = '\n'.join(problems)
        assert path in joined, f'问题信息里找不到出错的资产：{problems}'
        assert old in joined and new in joined, (
            f'问题信息里没有两个 GUID，无法定位：{problems}')
        assert any('guid' in p.lower() for p in problems), problems

    def test_missing_asset_meta_is_reported_with_the_asset(self):
        """删掉 `asset.meta` → 资产没有 importer/guid，必须报出来。"""
        data = build_package(_sample_files())
        entries = _members(data)
        path = 'Assets/StoryGraph/Resources/UnityDialogueData.json'
        guid = _guid_of(entries, path)
        del entries[f'{guid}/asset.meta']

        problems = verify_package(_repack(entries))
        assert problems, '删掉 asset.meta 后 verify_package 仍报合格'
        joined = '\n'.join(problems)
        assert 'asset.meta' in joined, problems
        assert path in joined, f'问题信息里找不到出错的资产：{problems}'

    def test_pathname_must_start_with_assets(self):
        """Unity 只导入 `Assets/` 下的资产；别的路径导入包会被静默忽略。"""
        data = build_package(_sample_files())
        entries = _members(data)
        path = 'Assets/StoryGraph/Runtime/DialogueData.cs'
        guid = _guid_of(entries, path)
        bad_path = 'Runtime/DialogueData.cs'
        entries[f'{guid}/pathname'] = bad_path.encode('utf-8')

        problems = verify_package(_repack(entries))
        assert problems, 'pathname 不以 Assets/ 开头仍然报合格'
        joined = '\n'.join(problems)
        assert bad_path in joined, f'问题信息里找不到坏路径：{problems}'
        assert 'Assets/' in joined, problems

    def test_duplicate_pathname_is_reported(self):
        """
        同一个 pathname 出现在两个 GUID 目录里 → Unity 导入时二选一，
        另一份静默丢。这是"重导后资产越来越多"的典型成因。
        """
        data = build_package(_sample_files())
        entries = _members(data)
        path = 'Assets/StoryGraph/Runtime/DialogueData.cs'
        old = _guid_of(entries, path)
        new = 'd' * 32 if old != 'd' * 32 else 'c' * 32
        for part in ('asset', 'asset.meta', 'pathname'):
            blob = entries[f'{old}/{part}']
            if part == 'asset.meta':
                blob = re.sub(rb'(?m)^guid: .*$', f'guid: {new}'.encode(),
                              blob)
            entries[f'{new}/{part}'] = blob

        problems = verify_package(_repack(entries))
        assert problems, '重复 pathname 仍然报合格'
        joined = '\n'.join(problems)
        assert path in joined, f'问题信息里找不到重复的路径：{problems}'
        assert '重复' in joined, (
            f'没有指出"重复 pathname"这件事（用户不知道为什么会丢资产）：'
            f'{problems}')

    def test_path_used_as_both_file_and_folder_is_reported(self):
        """
        同一路径不能既是文件、又是别的资产的父目录 —— Unity 无法同时
        建文件和文件夹，导入会失败或悄悄改路径。
        """
        files = {
            'Assets/StoryGraph/Runtime': b'this is a file',
            'Assets/StoryGraph/Runtime/DialogueData.cs': b'class X {}',
        }
        problems = verify_package(build_package(files))
        assert problems, '同一路径既是文件又是目录，仍然报合格'
        assert any('既是文件' in p for p in problems), problems

    def test_not_a_package_is_a_problem_not_a_crash(self):
        """
        自检的输入可能是截断的下载文件 / 手工改坏的包 —— 它要把"坏"
        变成一条可读问题，而不是抛异常把整个导入前自检打断。
        """
        problems = verify_package(b'not a unitypackage at all')
        assert problems and '不是可读的 gzip+tar 包' in problems[0], problems


# ------------------------------------------------------------------
#  导出接线 + 字节同源 + GUID 稳定
# ------------------------------------------------------------------

class TestExportWiring:
    def test_expected_files_and_write_order_include_the_package(self,
                                                               exported):
        """
        `expected_files('unity')` 必须与真实产物**逐条且同序**对账。

        顺序不是摆设：`export_ir` 的返回值就是"按这个顺序写出的文件"，
        声明和实现分开写时，最容易漏掉新加的那个（这正是当初 `STORY_IR`
        大小写碰撞能活下来的原因 —— 没人逐条对账）。
        """
        out, files = exported
        names = [Path(f).name for f in files]
        assert names == expected_files('unity'), (
            f'产物顺序 {names} != 声明 {expected_files("unity")}')
        assert UNITY_PACKAGE_NAME in names
        assert (out / UNITY_PACKAGE_NAME).is_file(), '返回值里有、磁盘上没有'

    def test_package_json_is_byte_identical_to_the_exported_files(self,
                                                                  exported):
        """
        包里的 JSON 与导出目录里的 JSON **逐字节相同**。

        为什么必须逐字节（不是"解析后相等"）：用户会拿包里的数据和目录
        里的文件互相 diff / 校验；两份"语义一样但换行不同"的 JSON 会让
        所有 checksum 都对不上，也没法用"文件哈希有没有变"判断该不该重导。
        """
        out, _files = exported
        package = read_package(out / UNITY_PACKAGE_NAME)
        assert package[f'{PACKAGE_ROOT}/Resources/{UNITY_DATA_NAME}'] == \
            (out / UNITY_DATA_NAME).read_bytes(), (
            '包里的 UnityDialogueData.json 与导出目录里的不是同一份字节')
        assert package[f'{PACKAGE_ROOT}/{NEUTRAL_IR_NAME}'] == \
            (out / NEUTRAL_IR_NAME).read_bytes(), (
            '包里的 story_ir.json 与导出目录里的不是同一份字节')
        # 中立 IR 不许放进 `Resources/`：Unity 会把那个目录无条件打进构建
        assert not any(p.startswith(f'{PACKAGE_ROOT}/Resources/{NEUTRAL_IR_NAME}')
                       or p.startswith(f'{PACKAGE_ROOT}/Resources/')
                       and p.endswith(NEUTRAL_IR_NAME)
                       for p in package), 'story_ir.json 不该在 Resources 下'

    def test_guid_is_stable_and_path_derived(self, tmp_path, beats):
        """
        同一 pathname 在两次导出里 GUID 相同，且等于 `guid_for(路径)`。

        Unity 靠 GUID 判断"更新还是新增"。GUID 一旦随内容或时间变化，
        用户每导一次就在项目里多一份 `DialogueData.cs`，旧的还挂着引用 ——
        这是比"导入失败"更难查的脏。
        """
        out1 = tmp_path / 'u1'
        out2 = tmp_path / 'u2'
        export_ir('unity', beats, out1)
        export_ir('unity', beats, out2)
        first = (out1 / UNITY_PACKAGE_NAME).read_bytes()
        second = (out2 / UNITY_PACKAGE_NAME).read_bytes()

        assert first == second, (
            '两次导出（输出目录不同）的包字节不同 —— 产物里混进了路径/时间')
        guids1 = _guid_map(first)
        guids2 = _guid_map(second)
        assert guids1 and guids1 == guids2, (
            f'两次导出的 GUID 不一致：\n{guids1}\n{guids2}')
        for path, guid in guids1.items():
            assert guid == guid_for(path), (
                f'{path} 的 GUID {guid} 不是由路径派生的'
                f'（应为 {guid_for(path)}）')

    def test_runtime_scripts_live_under_the_package_root(self, exported):
        """
        `Runtime/*.cs` 必须都在 `Assets/StoryGraph/Runtime/` 下。

        这是"包能对上导出目录"的判据：包内路径与 README 里写的导入步骤
        是同一份事实 —— 脚本跑到别处，用户照着文档就找不到。
        """
        out, _files = exported
        package = read_package(out / UNITY_PACKAGE_NAME)
        runtime = {p: blob for p, blob in package.items()
                   if p.endswith('.cs')}
        want = {f'{PACKAGE_ROOT}/Runtime/DialogueData.cs',
                f'{PACKAGE_ROOT}/Runtime/DialogueDatabase.cs',
                f'{PACKAGE_ROOT}/Runtime/DialoguePlayer.cs'}
        assert set(runtime) == want, (
            f'包里的 C# 路径对不上：{sorted(runtime)}；应该是 {sorted(want)}')
        for path, blob in runtime.items():
            assert path.startswith(f'{PACKAGE_ROOT}/Runtime/'), path
            assert blob.strip(), f'{path} 是空文件'

    def test_notes_point_at_the_importable_package(self, exported):
        """说明文档必须告诉用户"这个包可以直接导入"，而不是让人自己找。"""
        out, _files = exported
        notes = (out / UNITY_NOTES_NAME).read_text(encoding='utf-8')
        assert UNITY_PACKAGE_NAME in notes, notes
        assert 'Custom Package' in notes, notes
        assert 'Import Package' in notes, notes


class TestPackageFiles:
    def test_generated_set_has_the_runtime_data_and_readme(self):
        """`package_files` 生成的是整套资产：3 个 C# + 2 个 JSON + README。"""
        ir = build_neutral_ir([Beat(seq=1, label='a', text='x')])
        files = package_files(ir, to_unity(ir), _unity_notes(ir))
        assert set(files) == {
            f'{PACKAGE_ROOT}/Runtime/DialogueData.cs',
            f'{PACKAGE_ROOT}/Runtime/DialogueDatabase.cs',
            f'{PACKAGE_ROOT}/Runtime/DialoguePlayer.cs',
            f'{PACKAGE_ROOT}/Resources/{UNITY_DATA_NAME}',
            # 中立 IR **不在** `Resources/` 下（Unity 会无条件打包那个目录）
            f'{PACKAGE_ROOT}/{NEUTRAL_IR_NAME}',
            f'{PACKAGE_ROOT}/README.md',
        }, sorted(files)
        assert all(isinstance(blob, bytes) for blob in files.values())
        # 目录由 build_package 统一补，package_files 不用手写重复字符串
        assert verify_package(build_package(files)) == []

    def test_readme_has_import_steps_field_meaning_and_edit_warning(self):
        """
        README 必须说清三件事：怎么导入、字段什么意思、**别在这改数据**。

        第三句是给下一个接手的人看的：包是导出产物，手改的 JSON 会被
        下一次导出覆盖 —— 这个误解会让人丢一整章的修改。
        """
        ir = build_neutral_ir([Beat(seq=1, label='a', text='x')])
        files = package_files(ir, to_unity(ir), _unity_notes(ir))
        readme = files[f'{PACKAGE_ROOT}/README.md'].decode('utf-8')

        assert 'Import Package' in readme and 'Custom Package' in readme
        assert '字段含义' in readme
        assert 'lineId' in readme, '字段含义没把主键说清楚'
        assert '导出产物' in readme and '改数据请回' in readme, readme

    def test_extra_assets_are_placed_and_never_override_generated_data(self):
        """
        `extra` 只做补充：相对路径自动挂在 `Assets/StoryGraph/` 下；
        与生成文件同名时**生成内容优先**（不许调用方无意破坏字节同源）。
        """
        ir = build_neutral_ir([Beat(seq=1, label='a', text='x')])
        unity_data = to_unity(ir)
        files = package_files(
            ir, unity_data, _unity_notes(ir),
            extra={
                'Runtime/ExtraPlayback.cs': b'class ExtraPlayback {}',
                f'{PACKAGE_ROOT}/Resources/notes.txt': b'note',
                f'{PACKAGE_ROOT}/Resources/{UNITY_DATA_NAME}': b'{"bad": 1}',
            })

        assert files[f'{PACKAGE_ROOT}/Runtime/ExtraPlayback.cs'] == \
            b'class ExtraPlayback {}'
        assert files[f'{PACKAGE_ROOT}/Resources/notes.txt'] == b'note'
        assert files[f'{PACKAGE_ROOT}/Resources/{UNITY_DATA_NAME}'] != \
            b'{"bad": 1}', 'extra 覆盖了同一次导出生成的数据'
        assert json.loads(
            files[f'{PACKAGE_ROOT}/Resources/{UNITY_DATA_NAME}']) == unity_data

    def test_data_class_fields_match_the_unity_json_keys(self):
        """
        C# 字段名必须与 `to_unity()` 的 JSON 键**完全一致**（JsonUtility
        按字段名匹配，拼错了不会报错，只会静默读成 null）。

        `params` 是 C# 关键字，必须写成 `@params` —— 编译后字段名仍是
        `params`，所以 JSON 键不用改；这条判据把"看起来像写对了"钉死。
        """
        ir = build_neutral_ir([Beat(seq=1, label='a', text='x',
                                    scene='bg room')])
        unity_data = to_unity(ir)
        files = package_files(ir, unity_data, _unity_notes(ir))
        cs = files[f'{PACKAGE_ROOT}/Runtime/DialogueData.cs'].decode('utf-8')

        for key in unity_data:
            assert re.search(rf'public\s+[^;]*\b{re.escape(key)}\s*;', cs), (
                f'DialogueData 里没有与 JSON 顶层键 `{key}` 对应的字段')
        for key in unity_data['lines'][0]:
            assert re.search(rf'public\s+[^;]*\b{re.escape(key)}\s*;', cs), (
                f'DialogueLine 里没有与 JSON 键 `{key}` 对应的字段')
        assert 'public string @params;' in cs, (
            '`params` 是 C# 关键字，字段必须声明成 `@params`（JSON 键仍是 params）')


# ------------------------------------------------------------------
#  真编译：没有 Unity 也要能证明"这些 C# 编译得过"
# ------------------------------------------------------------------

#: 只补三个脚本用到的 Unity API。**故意不写完整引擎桩**：
#: 桩越小，越能证明"我们没偷偷依赖 Unity 里别的东西"。
_UNITY_STUB = r'''
namespace UnityEngine
{
    public class Object { }
    public class Component : Object { }
    public class Behaviour : Component { }
    public class MonoBehaviour : Behaviour { }
    public class TextAsset : Object { public string text; }

    public static class Resources
    {
        public static T Load<T>(string path) { return default(T); }
    }

    public static class Debug
    {
        public static void Log(object message) { }
        public static void LogWarning(object message) { }
        public static void LogError(object message) { }
    }

    public enum KeyCode { Space = 32 }

    public static class Input
    {
        public static bool GetKeyDown(KeyCode key) { return false; }
    }

    public static class JsonUtility
    {
        public static T FromJson<T>(string json) { return default(T); }
    }
}

namespace UnityEngine.UI
{
    public class Graphic : UnityEngine.Object { }
    public class Text : Graphic { public string text; }
}
'''

_CSPROJ = '''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>{tfm}</TargetFramework>
    <OutputType>Library</OutputType>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
  </PropertyGroup>
</Project>
'''


def _dotnet_target_framework(dotnet: str) -> str:
    """按本机 SDK 主版本选 TFM（有 9 用 net9.0，只有 8 就用 net8.0）。"""
    try:
        proc = subprocess.run([dotnet, '--version'], capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=60)
        major = (proc.stdout or '').strip().split('.')[0]
    except (OSError, subprocess.SubprocessError):
        # 版本号拿不到不影响"有没有 dotnet"这个前提（上面已用 which 判过）；
        # 给一个最常见的 TFM 让编译自己报错，而不是在这里假装通过。
        major = ''
    return f'net{major}.0' if major.isdigit() else 'net9.0'


def test_csharp_sources_compile_against_a_unity_stub(tmp_path, exported):
    """
    把包里的三个 `.cs` + `UnityEngine` 桩一起 `dotnet build`，必须 0 error。

    为什么必须有这一条：`export_ir` 能证明"字符串写进去了"，
    证明不了"它是合法 C#、且没用 Unity 里不存在的 API"。领域里最典型的
    坑（`public string params;` 是关键字、`using UnityEngine.UI` 拼错）
    只有编译器能抓。

    **没有 dotnet 时 `skip`，不静默 pass**：跳过和通过是两件事，
    测试报告必须能区分"没验"和"验过了"。
    """
    dotnet = shutil.which('dotnet')
    if not dotnet:
        pytest.skip('本机没有 dotnet —— C# 编译判据未执行（不是通过）')

    out, _files = exported
    package = read_package(out / UNITY_PACKAGE_NAME)
    sources = {
        name: package[f'{PACKAGE_ROOT}/Runtime/{name}']
        for name in ('DialogueData.cs', 'DialogueDatabase.cs',
                     'DialoguePlayer.cs')
    }
    assert all(blob.strip() for blob in sources.values()), (
        f'包里缺 C# 源文件：{sorted(sources)}')

    work = tmp_path / 'csbuild'
    work.mkdir()
    for name, blob in sources.items():
        (work / name).write_bytes(blob)
    (work / 'UnityEngineStub.cs').write_text(_UNITY_STUB, encoding='utf-8')
    (work / 'Compile.csproj').write_text(
        _CSPROJ.format(tfm=_dotnet_target_framework(dotnet)),
        encoding='utf-8')

    env = dict(os.environ)
    # 别让 CI/打包机上的遥测和首次运行横幅拖慢甚至卡住构建
    env['DOTNET_CLI_TELEMETRY_OPTOUT'] = '1'
    env['DOTNET_NOLOGO'] = '1'
    env['DOTNET_SKIP_FIRST_TIME_EXPERIENCE'] = '1'

    try:
        proc = subprocess.run(
            [dotnet, 'build', str(work / 'Compile.csproj'),
             '--nologo', '-v', 'minimal'],
            cwd=work, env=env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=180)
    except subprocess.TimeoutExpired:
        pytest.fail('dotnet build 超过 180 秒 —— 生成出来的 C# 编译不过或'
                    '本机 SDK 太慢（第一次构建可能需要还原）')

    output = ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip()
    assert proc.returncode == 0, (
        'dotnet build 失败（生成出来的 C# 编译不过）：\n'
        f'--- 退出码 {proc.returncode} ---\n{output[-6000:]}')
