"""
unity_package.py
================
把 Unity 导出的"几个 JSON + 一份说明"升级成**可直接导入的资产包**
（`.unitypackage`）—— 用户不用再自己写 `DialogueData` / 加载器。

为什么需要这一层（2026-10-21，多引擎导出第三片）：

  · 之前 Unity 导出目录里数据是全的，但 Unity 侧**一行代码都没有**：
    用户要照着 `UNITY_IMPORT.md` 手抄类、手写加载器、再挂脚本 ——
    这一步几乎每个项目都会做错一次（字段名抄错 / `params` 是 C# 关键字 /
    忘了 `JsonUtility` 不支持 Dictionary）；
  · `.unitypackage` 是 Unity 自己认的发行单位：`Assets → Import Package →
    Custom Package…` 一步导入，C# 与数据一起进项目。

两条**不能妥协**的纪律（都有测试守着）：

  1. **GUID 由包内路径确定性派生，不用随机数、不用内容哈希**。
     Unity 靠 GUID 判断"这是同一个资产"。同一路径重导必须是**更新**而不是
     新增；内容哈希会让"改一句台词"变成"多一份脚本"。
  2. **同一输入两次导出逐字节相同**。tar 条目的 `mtime=0 / uid=gid=0 /
     uname=gname=''`、目录按路径排序、gzip 头不写时间与文件名 ——
     否则"包有没有变"无法用 diff/哈希回答，团队没法把产物提交进 git。

包格式（Unity 读的就是这个）：外层 gzip，里面 tar；每个资产一个以 GUID
命名的目录，目录里固定三条：

    <guid>/pathname    —— 项目内相对路径（如 Assets/StoryGraph/README.md）
    <guid>/asset       —— 资产原始字节
    <guid>/asset.meta  —— Unity 的 .meta 文本（`guid:` 必须等于目录名）

**目录也会作为资产写进包**（`folderAsset: yes` + `DefaultImporter:`）：
Unity 给文件夹也分配 GUID，带上文件夹的 meta，重导时目录身份才是稳定的。
`read_package()` 只回**有内容**的条目 —— 目录没有内容，所以不放进
`{路径: 字节}`（这样 `read_package(build_package(files)) == files` 才成立）。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import tarfile
from contextlib import suppress
from pathlib import Path

__all__ = [
    'PACKAGE_ROOT', 'UNITY_PACKAGE_NAME', 'build_package', 'guid_for',
    'package_files', 'read_package', 'verify_package', 'write_package',
]

#: 导出目录里这个包的文件名（`engine_exporters.expected_files` 也引用它）。
UNITY_PACKAGE_NAME = 'StoryGraph.unitypackage'

#: 包内根目录。Unity 只导入 `Assets/` 下的资产，别的路径导入不了。
PACKAGE_ROOT = 'Assets/StoryGraph'

#: 每个资产目录里的三个固定条目。
_ASSET_PART = 'asset'
_META_PART = 'asset.meta'
_PATHNAME_PART = 'pathname'
_PARTS = (_ASSET_PART, _META_PART, _PATHNAME_PART)

#: Unity 的 GUID 是 32 位小写十六进制（不带连字符）。
_GUID_RE = re.compile(r'^[0-9a-f]{32}$')
#: `asset.meta` 里的 `guid:` 行（允许行尾空白；不许被别的键粘上）。
_META_GUID_RE = re.compile(r'^guid:\s*([0-9a-fA-F]{32})\s*$', re.M)


# ==================================================================
#  1. GUID 与路径
# ==================================================================

def guid_for(path: str) -> str:
    """
    包内路径 → Unity GUID（32 位小写十六进制，确定性）。

    **为什么用路径而不是内容做种子**：Unity 把 GUID 当资产身份。用内容
    哈希的话，改一句台词 = 换 GUID = Unity 里新增一份资产（旧的还在），
    重导几次项目里就会堆出好几份 `DialogueData.cs`。用路径派生则
    "同一路径 → 同一 GUID"，Unity 执行的是**更新**。

    前缀 `'StoryGraph/'` 与 `PACKAGE_ROOT` 解耦：即使将来更换包内根目录，
    已有资产的 GUID 也不会整体漂移（那会让 Unity 把所有资产当成新的）。
    """
    return hashlib.md5(('StoryGraph/' + str(path)).encode('utf-8')).hexdigest()


# ==================================================================
#  2. build_package：{包内路径: 字节} → .unitypackage 字节
# ==================================================================

def _split_input_path(raw_path) -> tuple:
    """
    调用方给的路径 → `(标准包内路径, 是否目录)`。

    只做"能一眼看懂的归一"：反斜杠改正斜杠（Windows 调用方会传
    `Assets\\StoryGraph\\...`）、去掉 `./` 前缀、目录以结尾 `/` 表示。
    **不要**顺手去掉开头的 `Assets/` 或 `..` —— 那会把错误路径伪装成
    合法路径；让 `verify_package()` 如实报出来。
    """
    path = str(raw_path).replace('\\', '/')
    while path.startswith('./'):
        path = path[2:]
    is_folder = path.endswith('/')
    path = path.rstrip('/')
    if not path:
        raise ValueError(f'包内路径不能为空：{raw_path!r}')
    return path, is_folder


def _as_bytes(value, path: str) -> bytes:
    """内容接受 bytes / bytearray / str（字符串按 UTF-8）；类型错就明说。"""
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode('utf-8')
    raise TypeError(f'包内资产 {path} 的内容必须是 bytes（拿到 '
                    f'{type(value).__name__}）')


def _collect_assets(files: dict) -> dict:
    """
    `{路径: 内容}` → `{路径: (内容, 是否目录)}`，并补上**隐式父目录**。

    为什么补父目录：Unity 的文件夹也是资产。包里有
    `Assets/StoryGraph/Runtime/DialogueData.cs` 却没有
    `Assets/StoryGraph/Runtime` 的 meta 时，Unity 导入会自动建目录并
    临时分配 GUID —— 下次重导就换成另一个 GUID，目录身份不稳定。
    调用方只该关心"文件"，目录由这里统一补齐（不用手写一堆重复字符串）。

    `Assets` 本身**不补**：它属于 Unity 工程，不该被一个导入包声明所有权。
    """
    assets: dict = {}
    for raw_path, content in (files or {}).items():
        path, is_folder = _split_input_path(raw_path)
        if path in assets:
            raise ValueError(f'包内路径重复（归一后都是 `{path}`）：{raw_path!r}')
        # 目录的 asset 必须是空字节（Unity 用 meta 里的 folderAsset 认它，
        # 内容没有意义）；调用方误传内容时以结构正确为准。
        data = b'' if is_folder else _as_bytes(content, path)
        assets[path] = (data, is_folder)

    for path in list(assets):
        _data, is_folder = assets[path]
        if is_folder or not path.startswith('Assets/'):
            continue
        parts = path.split('/')
        # range 从 2 开始：`Assets` 不补，`Assets/StoryGraph` 起才补。
        for i in range(2, len(parts)):
            parent = '/'.join(parts[:i])
            if parent in assets:
                # 已存在：是目录就继续；是文件则保留 —— 这种"同路径既是
                # 文件又是目录"的冲突由 `verify_package()` 报，不在这里猜。
                continue
            assets[parent] = (b'', True)
    return assets


def _meta_text(path: str, guid: str, is_folder: bool) -> str:
    """
    最小可用的 Unity `.meta`：`fileFormatVersion` + `guid` + 对应 importer。

    只写 Unity 认这些资产**必须**有的键，不猜 Unity 版本、不写
    `ProjectSettings`（方案约束："不猜 Unity 版本，只给资产"）。多余字段
    Unity 会补，缺 `guid` 则导入时会重新分配 GUID（身份就丢了）。
    """
    lines = ['fileFormatVersion: 2', f'guid: {guid}']
    if is_folder:
        lines += [
            'folderAsset: yes',
            'DefaultImporter:',
            '  externalObjects: {}',
            '  userData: ',
            '  assetBundleName: ',
            '  assetBundleVariant: ',
        ]
    elif path.lower().endswith('.cs'):
        lines += [
            'MonoImporter:',
            '  externalObjects: {}',
            '  serializedVersion: 2',
            '  defaultReferences: []',
            '  executionOrder: 0',
            '  icon: {instanceID: 0}',
            '  userData: ',
            '  assetBundleName: ',
            '  assetBundleVariant: ',
        ]
    else:
        # `.json` / `.md` 在 Unity 里都是 TextAsset，走 TextScriptImporter。
        lines += [
            'TextScriptImporter:',
            '  externalObjects: {}',
            '  userData: ',
            '  assetBundleName: ',
            '  assetBundleVariant: ',
        ]
    return '\n'.join(lines) + '\n'


def _tarinfo(name: str, size: int) -> tarfile.TarInfo:
    """
    一条**逐字节可复现**的 tar 条目。

    `mtime` / `uid` / `gid` / `uname` / `gname` 全部写死：它们默认会取
    当前时间 / 当前用户，两次导出就不一样了 —— 那样"包变了没有"
    只能靠解包比对，没法用哈希或 git diff 回答。
    """
    info = tarfile.TarInfo(name)
    info.size = size
    info.type = tarfile.REGTYPE
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ''
    info.gname = ''
    return info


def build_package(files: dict) -> bytes:
    """
    `{包内路径: 内容}` → `.unitypackage` 字节。

    输出是**确定性**的：资产按路径排序、每个 GUID 目录内按
    `asset / asset.meta / pathname` 排序，tar 元数据写死，gzip 头不写
    时间与文件名。同一份输入两次调用逐字节相同（`tests/test_unity_package.py`
    守着）；否则"重导一个包"在 git 里永远显示 changed。

    路径以 `/` 结尾表示目录（会写成 `folderAsset: yes`）；文件路径的父目录
    会自动补齐，调用方只传文件即可。
    """
    assets = _collect_assets(files)
    buf = io.BytesIO()
    # 不用 `tarfile.open(mode='w:gz')`：那个模式的 gzip 头会写当前时间，
    # 同一输入两次导出字节不同。`mtime=0` + 空 filename 才可复现。
    with gzip.GzipFile(filename='', mode='wb', fileobj=buf, mtime=0,
                       compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode='w',
                          format=tarfile.USTAR_FORMAT) as tar:
            for path in sorted(assets):
                content, is_folder = assets[path]
                guid = guid_for(path)
                meta = _meta_text(path, guid, is_folder).encode('utf-8')
                blobs = (
                    (_ASSET_PART, content),
                    (_META_PART, meta),
                    (_PATHNAME_PART, path.encode('utf-8')),
                )
                for part, blob in blobs:
                    tar.addfile(_tarinfo(f'{guid}/{part}', len(blob)),
                                io.BytesIO(blob))
    return buf.getvalue()


def write_package(path, files: dict) -> str:
    """
    原子写盘：先写同目录 `.tmp`，再 `os.replace` 成目标文件。

    与 `engine_exporters._atomic_write` 同一条纪律（但这里是**二进制**，
    所以单独实现，不去改那个文本写函数的签名）：磁盘满 / 被中断时，
    目标要么是旧的完整包、要么是新的完整包，绝不会是半截 tar.gz ——
    半截包在 Unity 里报的是"无法解压"，而人看到的是"文件明明在"。
    失败路径会自己清掉 `.tmp`，不给导出目录留残渣。
    """
    path = Path(path)
    data = build_package(files)
    tmp = path.with_name(path.name + '.tmp')
    try:
        with tmp.open('wb') as fh:
            fh.write(data)
        # 目标若正被别的程序独占打开（用户可能正拿 Unity 导着它），这里
        # 会抛 PermissionError —— 那是**该报出来**的失败，不吞。
        os.replace(tmp, path)
    except OSError:
        with suppress(OSError):
            tmp.unlink()
        raise
    return str(path)


# ==================================================================
#  3. read_package / verify_package
# ==================================================================

def _load_bytes(path_or_bytes) -> bytes:
    """接受"包文件路径"或"包字节"；别的类型直接说明。"""
    if isinstance(path_or_bytes, (bytes, bytearray, memoryview)):
        return bytes(path_or_bytes)
    if isinstance(path_or_bytes, (str, os.PathLike)):
        return Path(path_or_bytes).read_bytes()
    raise TypeError('read_package/verify_package 需要路径或 bytes，拿到 '
                    f'{type(path_or_bytes).__name__}')


def _member_parts(name: str) -> list:
    """tar 条目名 → 路径段（兼容 `./guid/asset` 这种带 `./` 的写法）。"""
    text = str(name).replace('\\', '/')
    while text.startswith('./'):
        text = text[2:]
    return text.split('/')


def _group_members(data: bytes) -> dict:
    """
    tar 条目 → `{guid: {asset|asset.meta|pathname: bytes}}`。

    只收**两段式**条目（`<guid>/<part>`）；结构不对的条目由调用方
    （`verify_package`）报，这里不擅自丢信息 —— 所以额外返回原始条目数
    是不必要的，`verify_package` 走自己的循环。
    """
    grouped: dict = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
        for member in tar:
            if not member.isfile():
                continue
            parts = _member_parts(member.name)
            if len(parts) != 2:
                continue
            guid, part = parts
            fh = tar.extractfile(member)
            grouped.setdefault(guid, {})[part] = (
                fh.read() if fh is not None else b'')
    return grouped


def read_package(path_or_bytes) -> dict:
    """
    解包回 `{包内路径: 内容}`（只含有内容的资产，目录条目不回）。

    用途有两个：测试里做字节往返；导入前自检（"包里到底是什么"）。
    目录条目没有内容 —— `build_package` 会自动补目录 meta，但调用方
    传进来的是文件，所以这里也**只回文件**，往返才等价。

    包坏了（不是 gzip/tar）会抛 `tarfile.TarError` / `OSError`；
    要"拿问题列表而不是异常"，用 `verify_package()`。
    """
    grouped = _group_members(_load_bytes(path_or_bytes))
    files: dict = {}
    for guid, parts in grouped.items():
        raw_path = parts.get(_PATHNAME_PART)
        if raw_path is None:
            continue                      # 缺 pathname 的坏条目：verify 报
        try:
            path = raw_path.decode('utf-8')
        except UnicodeDecodeError as e:
            # 路径都解不出来就没法当 key；这是"包坏了"，不是"没有这个资产"，
            # 必须让调用方知道（verify 会把它变成一条可定位的问题）。
            raise ValueError(f'包内 pathname 不是 UTF-8（GUID {guid}）：{e}')
        if not path:
            continue
        meta = parts.get(_META_PART, b'')
        if b'folderAsset: yes' in meta:
            continue                      # 目录没有内容，不属于 {路径: 字节}
        files[path] = parts.get(_ASSET_PART, b'')
    return dict(sorted(files.items()))


def verify_package(path_or_bytes) -> list:
    """
    结构自检：返回问题清单（空列表 = 合格）。给测试、也给"导入前自检"。

    判据（每条都对应 Unity 会踩的一个坑）：

      · 包可解（gzip + tar），且**非空**；
      · 每个 GUID 目录三件套齐全（`pathname` / `asset` / `asset.meta`）；
      · 目录名是 32 位小写十六进制，且与 `asset.meta` 里的 `guid:` 一致
        （不一致 Unity 会当成另一份资产，重导变成重复资产）；
      · `pathname` 是 UTF-8、以 `Assets/` 开头、用正斜杠、不含 `..`；
      · 同一 pathname 只出现一次（重复会让 Unity 二选一，另一个静默丢）；
      · 每个资产的 `asset.meta` 有 `fileFormatVersion` 与对应 importer
        （目录 `DefaultImporter` / `.cs` `MonoImporter` / 其它
        `TextScriptImporter`）；
      · 目录资产的 `asset` 为空；
      · 资产路径本身不以 `.meta` 结尾（会和 Unity 自动生成的 meta 撞名）；
      · 同一路径不能既是文件又是别的资产的父目录。

    注意这里**不**检查"GUID 是否等于 md5(路径)"：那是本模块导出的
    内部约定（由 `guid_for` 保证、由确定性/GUID 稳定测试守着）。
    自检面对的是"别人拿来的包"，只该断言 Unity 读得懂的结构。
    """
    problems: list = []
    try:
        data = _load_bytes(path_or_bytes)
    except (OSError, TypeError) as e:
        return [f'读不出包：{type(e).__name__}: {e}']

    try:
        members = []
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            for member in tar:
                if not member.isfile():
                    continue
                fh = tar.extractfile(member)
                members.append((member.name,
                                fh.read() if fh is not None else b''))
    except Exception as e:                # noqa: BLE001 - 坏包是**预期输入**
        # 自检的输入本来就可能是截断的下载文件 / 手工改坏的包 ——
        # 那不是本函数的异常，而是要报告的"第 0 条问题"。
        return [f'不是可读的 gzip+tar 包：{type(e).__name__}: {e}']

    if not members:
        return ['包里没有任何资产条目（空包）']

    groups: dict = {}
    for name, blob in members:
        parts = _member_parts(name)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            problems.append(
                f'无法识别的包内条目 `{name}`（应形如 '
                f'`<guid>/{_ASSET_PART}|{_META_PART}|{_PATHNAME_PART}`）')
            continue
        guid, part = parts
        if part not in _PARTS:
            problems.append(
                f'`{name}`：未知条目 `{part}`（每个资产只应有 asset / '
                f'asset.meta / pathname）')
            continue
        bucket = groups.setdefault(guid, {})
        if part in bucket:
            problems.append(f'GUID {guid} 下 `{part}` 出现了不止一次')
            continue
        bucket[part] = blob

    pathname_map: dict = {}               # pathname -> guid（查重复）
    folder_flags: dict = {}               # pathname -> 是否目录
    for guid in sorted(groups):
        parts = groups[guid]
        if not _GUID_RE.match(guid):
            problems.append(
                f'`{guid}`：GUID 目录名不是 32 位小写十六进制')

        path = ''
        raw_path = parts.get(_PATHNAME_PART)
        if raw_path is not None:
            try:
                path = raw_path.decode('utf-8')
            except UnicodeDecodeError as e:
                problems.append(f'GUID {guid}：pathname 不是 UTF-8：{e}')
            if raw_path is not None and not path:
                problems.append(f'GUID {guid}：pathname 为空')
        label = f'{path or guid}（GUID {guid}）'

        missing = [part for part in _PARTS if part not in parts]
        if missing:
            problems.append(
                f'{label}：缺少 {" / ".join(missing)}（一个资产必须三项齐全）')

        if path:
            if not path.startswith('Assets/'):
                problems.append(
                    f'{label}：pathname 必须以 `Assets/` 开头'
                    '（Unity 只导入 Assets 下的资产）')
            if '\\' in path:
                problems.append(
                    f'{label}：pathname 用了反斜杠，Unity 只认正斜杠')
            if any(seg in ('', '.', '..') for seg in path.split('/')):
                problems.append(
                    f'{label}：pathname 含空段 / `.` / `..`（路径不干净）')
            if path in pathname_map:
                problems.append(
                    f'{label}：pathname 重复（另一个 GUID 是 '
                    f'{pathname_map[path]}）—— Unity 会二选一，另一份静默丢')
            else:
                pathname_map[path] = guid
            if path.lower().endswith('.meta'):
                problems.append(
                    f'{label}：资产路径本身以 `.meta` 结尾，会与 Unity '
                    '自动生成的 .meta 同名冲突')
        else:
            folder_flags[path] = False

        meta = parts.get(_META_PART)
        is_folder = False
        if meta is not None:
            try:
                meta_text = meta.decode('utf-8')
            except UnicodeDecodeError as e:
                problems.append(f'{label}：asset.meta 不是 UTF-8：{e}')
                meta_text = ''
            if meta_text:
                if 'fileFormatVersion:' not in meta_text:
                    problems.append(
                        f'{label}：asset.meta 缺少 `fileFormatVersion`')
                found = _META_GUID_RE.search(meta_text)
                if found is None:
                    problems.append(f'{label}：asset.meta 缺少 `guid:`')
                elif found.group(1).lower() != guid.lower():
                    problems.append(
                        f'{label}：asset.meta 的 guid（{found.group(1)}）'
                        f'与目录名（{guid}）不一致 —— Unity 会把它当成'
                        '另一份资产')
                is_folder = 'folderAsset: yes' in meta_text
                if is_folder:
                    expected = 'DefaultImporter:'
                    why = '目录资产'
                elif path.lower().endswith('.cs'):
                    expected = 'MonoImporter:'
                    why = 'C# 脚本'
                else:
                    expected = 'TextScriptImporter:'
                    why = '.json / .md 等文本资产'
                if expected not in meta_text:
                    problems.append(
                        f'{label}：asset.meta 应使用 {expected}'
                        f'（{why}的最小 importer）')
                if is_folder and parts.get(_ASSET_PART):
                    problems.append(f'{label}：目录资产的 asset 应为空字节')
        if path:
            folder_flags[path] = is_folder

    # 同一路径不能既是文件、又是别的资产的父目录（Unity 无法同时建文件和
    # 文件夹）。`build_package` 不会制造这种情况；手工拼包时很容易。
    for path in pathname_map:
        segments = path.split('/')
        for i in range(2, len(segments)):
            parent = '/'.join(segments[:i])
            parent_guid = pathname_map.get(parent)
            if parent_guid is not None and not folder_flags.get(parent, False):
                problems.append(
                    f'{parent}（GUID {parent_guid}）：既是文件资产，'
                    f'又被 {path} 当作目录使用 —— 同一个路径不能既是'
                    '文件又是文件夹')

    return problems


# ==================================================================
#  4. package_files：由 IR 生成整套资产内容
# ==================================================================

#: 与 `to_unity()` 输出的 JSON 字段**逐字对应**（camelCase）。
#: `params` 是 C# 关键字，字段必须写 `@params`；编译后的字段名仍是
#: `params`，JsonUtility 按字段名对 JSON，照样能读到。
_DIALOGUE_DATA_CS = '''using System;

namespace StoryGraph
{
    // Unity 的 JsonUtility **不支持 Dictionary / 接口 / 多态**，
    // 所以数据类全部是 public 字段 + 数组，字段名就是 JSON 里的键。
    // 改这里的字段名等于改导出契约，StoryGraph 的 to_unity() 也要同步。
    [Serializable]
    public class TimelineEvent
    {
        public string op;
        public string target;
        public string at;
        public string clause;
        public string transition;
        public string action;
        // `params` 是 C# 关键字，声明时只能写 `@params`；
        // 字段的**名字**仍然是 `params`，与 JSON 键一致。
        public string @params;
        public int order;
    }

    [Serializable]
    public class DialogueLine
    {
        public string lineId;
        public string label;
        public int seq;
        public string kind;
        public string speakerId;
        public string speakerName;
        public string text;
        public string condition;
        public string background;
        public string[] poses;
        public string bgm;
        public string bgmAction;
        public string sfx;
        public string sfxAction;
        public string voice;
        public string nest;
        public string structurePath;
        public string structureKind;
        // false = 老表不知道这一拍在结构里的位置，别当"顶层"。
        public bool structureKnown;
        public TimelineEvent[] timeline;
        public string transition;
        public string[] targets;
        public string lineHash;
    }

    [Serializable]
    public class DialogueCharacter
    {
        public string id;
        public string displayName;
        public string color;
        public string imageTag;
        public string voiceTag;
    }

    [Serializable]
    public class DialogueAsset
    {
        public string id;
        public string type;
        public string path;
        public string tag;
        public string category;
        public string doc;
    }

    [Serializable]
    public class DialogueData
    {
        public string schema;
        public string project;
        public DialogueLine[] lines;
        public DialogueCharacter[] characters;
        public DialogueAsset[] assets;
    }
}
'''

_DIALOGUE_DATABASE_CS = '''using System;
using System.Collections.Generic;
using UnityEngine;

namespace StoryGraph
{
    /// <summary>
    /// 把 UnityDialogueData.json 读进内存，并按 lineId / label 建索引。
    ///
    /// 为什么这里可以用 Dictionary：JsonUtility **不序列化** Dictionary，
    /// 所以"数据类"（DialogueData）里一个都不许有；但本类只在运行时建
    /// 索引、不从 JSON 反序列化它，用 Dictionary 是安全的，查询也是 O(1)。
    /// </summary>
    public class DialogueDatabase : MonoBehaviour
    {
        // 留空时从 Resources 载入。包里的数据就在
        // Assets/StoryGraph/Resources/ 下，Unity 会把它编进 Resources。
        public TextAsset dataFile;

        private DialogueData data;
        private readonly Dictionary<string, DialogueLine> byId =
            new Dictionary<string, DialogueLine>();
        private readonly Dictionary<string, List<DialogueLine>> byLabel =
            new Dictionary<string, List<DialogueLine>>();

        public DialogueData Data
        {
            get { return data; }
        }

        private void Awake()
        {
            if (!LoadData())
            {
                return;
            }
            BuildIndexes();
        }

        private bool LoadData()
        {
            string json = null;
            if (dataFile != null)
            {
                json = dataFile.text;
            }
            else
            {
                // `Resources.Load` 的路径**相对 Resources 目录**、且不带扩展名。
                // 数据在 Assets/StoryGraph/Resources/UnityDialogueData.json，
                // 所以这里写的是 "UnityDialogueData"（不是从 Assets 起算）。
                TextAsset asset = Resources.Load<TextAsset>("UnityDialogueData");
                if (asset != null)
                {
                    json = asset.text;
                }
            }

            if (string.IsNullOrEmpty(json))
            {
                Debug.LogError(
                    "DialogueDatabase：找不到数据。请把 Data File 指到 " +
                    "Assets/StoryGraph/Resources/UnityDialogueData.json，" +
                    "或确认该文件仍在某个 Resources 目录下" +
                    "（Resources.Load 的路径是 \\"UnityDialogueData\\"）。");
                return false;
            }

            try
            {
                data = JsonUtility.FromJson<DialogueData>(json);
            }
            catch (Exception e)
            {
                // 字段类型对不上时 JsonUtility 会抛 —— 报出原文，别让它变成
                // "界面空白但没有任何日志"。
                Debug.LogError("DialogueDatabase：JSON 解析失败：" + e.Message);
                return false;
            }

            if (data == null || data.lines == null)
            {
                Debug.LogError(
                    "DialogueDatabase：JSON 里没有 lines 数组，数据可能不完整。");
                return false;
            }
            return true;
        }

        private void BuildIndexes()
        {
            byId.Clear();
            byLabel.Clear();
            for (int i = 0; i < data.lines.Length; i++)
            {
                DialogueLine line = data.lines[i];
                if (line == null)
                {
                    continue;
                }
                if (!string.IsNullOrEmpty(line.lineId))
                {
                    byId[line.lineId] = line;
                }
                string label = line.label ?? string.Empty;
                List<DialogueLine> bucket;
                if (!byLabel.TryGetValue(label, out bucket))
                {
                    bucket = new List<DialogueLine>();
                    byLabel[label] = bucket;
                }
                bucket.Add(line);
            }
        }

        /// <summary>按 lineId 取一行；找不到返回 null（存档/埋点常用主键）。</summary>
        public DialogueLine GetLine(string lineId)
        {
            if (string.IsNullOrEmpty(lineId))
            {
                return null;
            }
            DialogueLine line;
            return byId.TryGetValue(lineId, out line) ? line : null;
        }

        /// <summary>某个 label 下的所有行，按导出顺序（= 剧本表顺序）。</summary>
        public List<DialogueLine> LinesForLabel(string label)
        {
            List<DialogueLine> bucket;
            string key = label ?? string.Empty;
            if (byLabel.TryGetValue(key, out bucket))
            {
                return bucket;
            }
            return new List<DialogueLine>();
        }
    }
}
'''

_DIALOGUE_PLAYER_CS = '''using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace StoryGraph
{
    /// <summary>
    /// 最小播放器：把第一个 label 的台词按顺序显示，空格前进一行。
    ///
    /// 刻意只依赖 `UnityEngine.UI`（不引 TMP / Addressables / Input System）：
    /// 导入即可编译、即可跑通，是"这份导出真的能用"的最小证明；
    /// 场景、立绘、音频仍由项目自己接。
    /// </summary>
    public class DialoguePlayer : MonoBehaviour
    {
        public DialogueDatabase db;
        public Text speaker;
        public Text body;

        private List<DialogueLine> lines = new List<DialogueLine>();
        private int index = -1;

        private void Start()
        {
            if (db == null)
            {
                Debug.LogError(
                    "DialoguePlayer：Db 没有指到 DialogueDatabase。");
                return;
            }

            DialogueData data = db.Data;
            if (data == null || data.lines == null || data.lines.Length == 0)
            {
                Debug.LogError(
                    "DialoguePlayer：没有可播放的台词" +
                    "（DialogueDatabase 可能没载入成功）。");
                return;
            }

            // 第一句所在的 label 就是开场章节。导出顺序 = 剧本表顺序，
            // 所以"第一条的 label"是稳定的，不依赖场景里手填章节名。
            string firstLabel = data.lines[0] != null
                ? data.lines[0].label
                : string.Empty;
            lines = db.LinesForLabel(firstLabel);
            index = -1;

            if (lines == null || lines.Count == 0)
            {
                Debug.LogWarning(
                    "DialoguePlayer：label '" + firstLabel + "' 下没有台词。");
                return;
            }
            Advance();
        }

        private void Update()
        {
            if (Input.GetKeyDown(KeyCode.Space))
            {
                Advance();
            }
        }

        private void Advance()
        {
            if (lines == null || lines.Count == 0)
            {
                return;
            }

            index++;
            if (index >= lines.Count)
            {
                // 停在最后一句上：再按空格只提示，不越界、不把界面清空。
                index = lines.Count - 1;
                Debug.Log("DialoguePlayer：已经到最后一句。");
                return;
            }

            DialogueLine line = lines[index];
            if (line == null)
            {
                Debug.LogWarning(
                    "DialoguePlayer：第 " + index + " 句是 null（数据里有空洞）。");
                return;
            }

            if (speaker != null)
            {
                speaker.text = line.speakerName;
            }
            if (body != null)
            {
                body.text = line.text;
            }
            Debug.Log("[" + line.lineId + "] " + line.speakerName +
                      "：" + line.text);
        }
    }
}
'''


def _json_bytes(obj) -> bytes:
    """
    与 `engine_exporters._write_json()` 逐字节同源的 JSON。

    那个函数用文本模式（`newline=None`）写盘 —— Windows 上会把 `\\n`
    折成 `\\r\\n`。包里的这份必须与导出目录里那份**完全相同**（测试逐字节
    比、用户也会拿两边 diff），所以这里照抄同一条折行规则，而不是用
    "看起来一样"的裸 `json.dumps(...).encode("utf-8")`。
    """
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return text.replace('\n', os.linesep).encode('utf-8')


def _readme(notes_md: str, neutral_name: str = 'story_ir.json') -> str:
    """包内 README：导入步骤 + 包内容 + 字段含义（直接嵌 `UNITY_IMPORT.md`）。"""
    notes = str(notes_md or '').strip()
    return f'''# StoryGraph · Unity 资产包

本包由 StoryGraph 剧本表导出。**这是导出产物，改数据请回编辑器
（StoryGraph 的剧本表）；直接改这里的 JSON，下次导出会被覆盖。**

## 导入步骤

1. Unity 菜单 `Assets → Import Package → Custom Package…`，选
   `{UNITY_PACKAGE_NAME}`。
2. 全选 → Import。包内已经带好 C# 与数据，不用再手写加载器：
   - `Runtime/DialogueData.cs` —— 与 JSON 字段一一对应的纯数据类；
   - `Runtime/DialogueDatabase.cs` —— 载入 + 按 `lineId` / `label` 建索引；
   - `Runtime/DialoguePlayer.cs` —— 最小播放器（空格前进）。
3. 场景里建一个空 GameObject，挂 `DialogueDatabase`；`Data File` 留空时
   会从 `Resources.Load<TextAsset>("UnityDialogueData")` 读数据
   （文件在 `Assets/StoryGraph/Resources/` 下）。
4. 再挂 `DialoguePlayer`：`Db` 指向上一步的组件，`Speaker` / `Body`
   指向两个 `UnityEngine.UI.Text`。运行后按空格前进一行。

## 包内数据

- `Resources/UnityDialogueData.json` —— `JsonUtility.FromJson<DialogueData>`
  直接吃；字段是标量 / `string[]` / `TimelineEvent[]`，没有嵌套字典。
- `{neutral_name}`（包根目录，**不在** `Resources/` 里）—— 引擎中立 IR
  （字段更全，含 stage 快照与资源清单），需要自定义解析时用它。
  不放进 `Resources/` 是因为 Unity 会把那个目录**无条件打进构建**。
- `Runtime/*.cs` —— 运行时代码，注释里写了"为什么"这么写。

## 字段含义

{notes}
'''


def package_files(ir: dict, unity_data: dict, notes_md: str,
                  extra: dict = None) -> dict:
    """
    由 IR 生成整套 Unity 资产内容：`{包内路径: 字节}`。

    数据与代码来自**同一次**导出：`Resources/UnityDialogueData.json` 与
    `Resources/story_ir.json` 用与 `engine_exporters._write_json()` 相同的
    序列化/折行规则生成，所以包里的字节与目录里散落的文件逐字节相同。

    `extra`：额外资产（相对 `PACKAGE_ROOT`，或直接给 `Assets/...` 绝对包
    路径都行）。只做**补充**：与自动生成的文件同名时以生成内容为准 ——
    否则调用方可以无意间破坏"包内数据 == 目录数据"这条保证。
    """
    # 延迟 import：`engine_exporters` 在顶层 import 本模块（导出时调
    # `package_files`），这里再顶层 import 它会成环。文件名仍只有那一份
    # 真源，不在两个文件里各抄一遍。
    from storygraph.pipeline.engine_exporters import (NEUTRAL_IR_NAME,
                                                      UNITY_DATA_NAME)

    files = {
        f'{PACKAGE_ROOT}/Runtime/DialogueData.cs':
            _DIALOGUE_DATA_CS.encode('utf-8'),
        f'{PACKAGE_ROOT}/Runtime/DialogueDatabase.cs':
            _DIALOGUE_DATABASE_CS.encode('utf-8'),
        f'{PACKAGE_ROOT}/Runtime/DialoguePlayer.cs':
            _DIALOGUE_PLAYER_CS.encode('utf-8'),
        f'{PACKAGE_ROOT}/Resources/{UNITY_DATA_NAME}':
            _json_bytes(unity_data),
        # ⚠ **不放 `Resources/`**：Unity 会把 `Resources` 下的东西**无条件
        # 打进播放器包**（哪怕一次都没 Load 过）。立 IR 只给自定义解析用，
        # 不该跟着每一次构建走；放包根目录即可，需要时按路径读。
        f'{PACKAGE_ROOT}/{NEUTRAL_IR_NAME}':
            _json_bytes(ir),
        f'{PACKAGE_ROOT}/README.md':
            _readme(notes_md, NEUTRAL_IR_NAME).encode('utf-8'),
    }

    for raw_path, content in (extra or {}).items():
        path = str(raw_path).replace('\\', '/').lstrip('/')
        if not path:
            continue
        if not path.startswith('Assets/'):
            path = f'{PACKAGE_ROOT}/{path}'
        if path in files:
            # 同一次导出的数据必须来自同一次 `to_unity`，extra 不许覆盖。
            continue
        files[path] = _as_bytes(content, path)
    return files
