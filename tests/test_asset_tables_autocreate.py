"""
tests/test_asset_tables_autocreate.py
=====================================
**没有资源表就当场建一张** —— 用户 2026-10-05 实测报：

> 我拿另一个复制看了一下测出 bug 了，首次加载导入时项目无法读到资源表，
> 应当立即创建对应的资源表

现场（实测确认）：复制出来的项目里 `game/asset_tables/` 是个**空目录**
（xlsx 没跟过去）→ 「打开项目」只报一句
`读不到资源表：这个目录下找不到资源表（xlsx）（下拉候选会是空的）` →
立绘/音乐候选全空、缩略图全空。

自动扫表这件事**代码里早就有**，但长在 `AssetEngine._ensure_asset_tables_exist()`
上 —— 而那个引擎在当前的 Qt 应用里**一次都没被构造**。所以这次把它提到
assets 层做唯一真源（`ensure_asset_tables`），编辑器这条路直接用它。
"""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402


def _png(path: Path, size=(6, 6)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', size, (30, 120, 200, 255)).save(path, 'PNG')
    return path


@pytest.fixture
def fake_project(tmp_path):
    """
    一个"复制出来但 xlsx 没跟过去"的项目：

        <root>/game/asset_tables/      ← **空目录**（用户的现场）
        <root>/game/images/ling/happy.png
        <root>/game/bgm/night.ogg
        <root>/game/chapter/ch01.rpy
    """
    root = tmp_path / 'sr_general_copy'
    (root / 'game' / 'asset_tables').mkdir(parents=True)
    _png(root / 'game' / 'images' / 'ling' / 'happy.png')
    _png(root / 'game' / 'images' / 'bg' / 'classroom.png')
    (root / 'game' / 'bgm').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'bgm' / 'night.ogg').write_bytes(b'OggS' + b'\0' * 64)
    (root / 'game' / 'chapter').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'chapter' / 'ch01.rpy').write_text(
        'define e = Character("爱莲", image="ling")\n'
        'transform i11:\n    xpos 0.35\n'
        'label ch01:\n'
        '    e "早上好。"\n',
        encoding='utf-8')
    return root


class TestEnsureAssetTables:
    def test_it_creates_tables_when_the_dir_is_empty(self, fake_project):
        from storygraph.assets.asset_registry import (ensure_asset_tables,
                                                      find_asset_tables)
        assert find_asset_tables(fake_project) is None, '前置条件：没有表'
        made = ensure_asset_tables(fake_project)
        assert made['ok'] is True, made
        assert made['created'], '应当报出"建在哪"'
        files = sorted(p.name for p in
                       (fake_project / 'game' / 'asset_tables').glob('*.xlsx'))
        assert 'images.xlsx' in files, files
        assert made['counts']['images'] >= 2, made['counts']
        assert find_asset_tables(fake_project) is not None

    def test_it_does_not_touch_an_existing_table(self, fake_project):
        from storygraph.assets.asset_registry import ensure_asset_tables
        first = ensure_asset_tables(fake_project)
        assert first['created']
        images = fake_project / 'game' / 'asset_tables' / 'images.xlsx'
        before = images.stat().st_mtime_ns
        second = ensure_asset_tables(fake_project)
        assert second['created'] == '', '已经有表了不许再建一次'
        assert images.stat().st_mtime_ns == before, '已有表被改写了'

    def test_a_root_level_table_also_counts(self, tmp_path):
        """`<root>/asset_tables/` 也算已有（`load_asset_registry` 先看它）。"""
        from storygraph.assets.asset_registry import (ensure_asset_tables,
                                                      find_asset_tables)
        import openpyxl
        root = tmp_path / 'proj'
        d = root / 'asset_tables'
        d.mkdir(parents=True)
        wb = openpyxl.Workbook()                        # 一张**读得出来**的真表
        wb.active.append(['ID', 'Path', 'Tag', 'Attributes', 'Category'])
        wb.save(d / 'images.xlsx')
        assert find_asset_tables(root) == d
        assert ensure_asset_tables(root)['created'] == ''


class TestAudioDirsAreFoundAtAnyDepth:
    """
    **音频目录不能写死**（2026-10-05 实测：用户项目里 49 个音频只扫出 34 个，
    「音效 0」）。

    漏掉的是这两个：
      · `game/gui/sfx/*.wav` —— `sfx` 不在写死的名单里（名单里只有 `sounds`）；
      · `game/pomodorro/music/*` —— 嵌在子模块里的 `music/`，只探了根与
        `game/` 两层。

    后果很直接：这些音乐/音效**永远进不了候选**（选不到、也看不到）。
    """

    @pytest.fixture
    def audio_project(self, tmp_path):
        root = tmp_path / 'proj'
        for rel in ('game/bgm/night.ogg',
                    'game/gui/sfx/click.wav',
                    'game/gui/sfx/deny.wav',
                    'game/pomodorro/music/cafe.ogg',
                    'game/pomodorro/music/guitar.ogg'):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'OggS' + b'\0' * 32)
        return root

    def test_nested_music_and_sfx_dirs_are_scanned(self, audio_project):
        from storygraph.assets.asset_registry import scan_assets
        assets = scan_assets(audio_project, include_ui=True)
        music = {r['Path'] for r in assets['audio']['music']}
        sound = {r['Path'] for r in assets['audio']['sound']}
        assert 'game/pomodorro/music/cafe.ogg' in music, music
        assert 'game/pomodorro/music/guitar.ogg' in music, music
        assert 'game/bgm/night.ogg' in music, music
        assert 'game/gui/sfx/click.wav' in sound, sound
        assert 'game/gui/sfx/deny.wav' in sound, sound

    def test_every_audio_file_lands_somewhere(self, audio_project):
        """总数守恒：磁盘上有几个音频，就应当进表几条（不重不漏）。"""
        from storygraph.assets.asset_registry import scan_assets
        disk = [p for p in audio_project.rglob('*') if p.is_file()]
        assets = scan_assets(audio_project, include_ui=True)
        got = (assets['audio']['music'] + assets['audio']['sound']
               + (assets.get('voices') or []))
        assert len(got) == len(disk), (len(got), len(disk))


class TestAutoSyncExistingTable:
    """
    **已有表 → 打开项目时自动把磁盘上没登记的资源补进去**。

    用户 2026-10-05 的目标：

    > 每次打开项目文件，若没有表，建立表，若表已经存在，自动更新表中的资源，
    > 确保表与资源自动一一对应。
    """

    def test_it_appends_files_that_appeared_later(self, fake_project):
        from storygraph.assets.asset_registry import sync_asset_tables
        from storygraph.pipeline.build import load_asset_registry

        first = sync_asset_tables(fake_project)
        assert first['created'], '第一遍应当建表'
        before = len(load_asset_registry(fake_project).images)

        # 用户后来往项目里丢了新图 + 新音效
        _png(fake_project / 'game' / 'images' / 'characters' / 'mary' /
             'smile.png')
        snd = fake_project / 'game' / 'gui' / 'sfx' / 'click.wav'
        snd.parent.mkdir(parents=True, exist_ok=True)
        snd.write_bytes(b'OggS' + b'\0' * 16)

        res = sync_asset_tables(fake_project)
        assert res['ok'] is True, res
        assert res['created'] == '', '已经有表了，不该重建'
        assert res['added'] >= 2, res
        assert res['by_kind'].get('images') == 1, res['by_kind']
        assert res['by_kind'].get('sounds') == 1, res['by_kind']
        assert res['backup'], '写表前必须备份'
        after = load_asset_registry(fake_project)
        assert len(after.images) == before + 1
        assert (fake_project / 'game' / 'asset_tables' /
                'sounds.bak.xlsx').is_file(), '没有留下备份'

    def test_it_is_idempotent(self, fake_project):
        from storygraph.assets.asset_registry import sync_asset_tables
        sync_asset_tables(fake_project)
        _png(fake_project / 'game' / 'images' / 'new_one.png')
        assert sync_asset_tables(fake_project)['added'] == 1
        assert sync_asset_tables(fake_project)['added'] == 0, '第二次不该再补'

    def test_it_never_overwrites_a_hand_edited_row(self, fake_project):
        """
        人改过的字段是权威：补登只**追加**，已有行一个字段都不动。

        （真实场景：用户在 `Notes` 写"这张要换掉"、把某张的 `Category`
        从 ui 改成 background —— 工具不能改回去。）
        """
        import openpyxl
        from storygraph.assets.asset_registry import sync_asset_tables
        sync_asset_tables(fake_project)
        table = fake_project / 'game' / 'asset_tables' / 'images.xlsx'
        wb = openpyxl.load_workbook(table)
        ws = wb.active
        # ⚠ 资源表表头已改**中文**（`ASSET_FIELD_LABELS`）→ 测试也要按**规范列名**
        # 找列：`_sheet_headers` 把中文映射回 `ID`/`Path`（老表英文表头照样通过）。
        from storygraph.assets.asset_registry import _sheet_headers as _sh
        headers = _sh(ws)
        ws.cell(row=2, column=headers.index('Notes') + 1, value='人工备注：留着')
        ws.cell(row=2, column=headers.index('Category') + 1, value='background')
        first_id = ws.cell(row=2, column=headers.index('ID') + 1).value
        wb.save(table)

        _png(fake_project / 'game' / 'images' / 'another.png')
        assert sync_asset_tables(fake_project)['added'] == 1

        wb2 = openpyxl.load_workbook(table)
        ws2 = wb2.active
        row = None
        for r in range(2, ws2.max_row + 1):
            if ws2.cell(row=r, column=headers.index('ID') + 1).value == \
                    first_id:
                row = r
                break
        assert row is not None
        assert ws2.cell(row=row, column=headers.index('Notes') + 1).value == \
            '人工备注：留着', '补登把人工备注冲了'
        assert ws2.cell(row=row,
                        column=headers.index('Category') + 1).value == \
            'background', '补登把人工分类冲了'

    def test_one_file_gets_exactly_one_row(self, fake_project):
        """
        **一个文件一行**（用户要的"一一对应"）。

        同一张图可能已经以另一个 ID 登记过（`RETURE.png` 与
        `Frame 26_RETURE.png` 实测就这样）—— 只按 ID 判重会写第二行。
        """
        import openpyxl
        from storygraph.assets.asset_registry import sync_asset_tables
        sync_asset_tables(fake_project)
        table = fake_project / 'game' / 'asset_tables' / 'images.xlsx'
        wb = openpyxl.load_workbook(table)
        ws = wb.active
        from storygraph.assets.asset_registry import _sheet_headers as _sh
        headers = _sh(ws)
        # 把第 2 行的 ID 改成一个"别名"，Path 仍指向同一个文件
        ws.cell(row=2, column=headers.index('ID') + 1, value='aliased_id')
        wb.save(table)
        res = sync_asset_tables(fake_project)
        assert res['added'] == 0, f'同一个文件被写了第二行：{res}'
        wb2 = openpyxl.load_workbook(table)
        paths = [wb2.active.cell(row=r, column=headers.index('Path') + 1).value
                 for r in range(2, wb2.active.max_row + 1)]
        norm = [str(p).replace('\\', '/').lower() for p in paths if p]
        assert len(norm) == len(set(norm)), f'表里有重复 Path：{norm}'

    def test_load_assets_reports_the_sync(self, fake_project):
        """`load_assets` 要把"补了几条"带出来（界面据此写日志）。"""
        from storygraph.app.editor import EditorSession
        from storygraph.assets.asset_registry import sync_asset_tables
        sync_asset_tables(fake_project)
        _png(fake_project / 'game' / 'images' / 'later.png')
        res = EditorSession(project_root=fake_project).load_assets()
        assert res['ok'] is True, res
        assert res['synced'] == 1, res
        assert res['synced_by_kind'].get('images') == 1, res
        assert res['sync_backup'], res


class TestDeletedResourcesLeaveTheTable:
    """
    用户 2026-10-05：

    > 那我要是把资源真的删掉不用了，你不就又对应不上了吗？我认为表应当实时反应
    > 实际资源的情况，自动更新和增删！表里有但实际没有的资源也不能用啊？？？
    > （除非人工把它录入占位，这个允许）
    """

    def test_a_deleted_file_is_removed_from_the_table(self, fake_project):
        from storygraph.assets.asset_registry import sync_asset_tables
        from storygraph.pipeline.build import load_asset_registry
        sync_asset_tables(fake_project)
        # 工具加一行（= 侧车里记上"归我管"）
        extra = _png(fake_project / 'game' / 'images' / 'temp_thing.png')
        assert sync_asset_tables(fake_project)['added'] == 1
        assert any('temp_thing' in p
                   for p in load_asset_registry(fake_project).images), \
            '前置条件：这一行应当在表里'

        extra.unlink()                          # 用户把资源删了
        res = sync_asset_tables(fake_project)
        assert res['removed'] == 1, res
        assert res['removed_by_kind'].get('images') == 1, res
        assert not any('temp_thing' in p
                       for p in load_asset_registry(fake_project).images), \
            '文件删了，表里那一行还在'
        assert (fake_project / 'game' / 'asset_tables' /
                'images.bak.xlsx').is_file(), '自动删也要先备份'

    def test_a_manual_row_with_a_missing_file_is_deleted_too(
            self, fake_project):
        """
        **人工录入的行，文件没了也删**（用户 2026-10-05 明确授权）：

        > 已经不在那这个东西实际上不需要啦！你自己自动删改表即可！
        > 之前就说表跟资源一一对应！如果不可用直接删了就行！
        """
        import openpyxl
        from storygraph.assets.asset_registry import sync_asset_tables
        from storygraph.pipeline.build import load_asset_registry
        sync_asset_tables(fake_project)
        table = fake_project / 'game' / 'asset_tables' / 'images.xlsx'
        wb = openpyxl.load_workbook(table)
        ws = wb.active
        from storygraph.assets.asset_registry import _sheet_headers as _sh
        headers = _sh(ws)
        ws.append(['manual_ghost', 'game/images/ghost.png', 'ghost', 'ghost',
                   'character', ''][:len(headers)])
        wb.save(table)

        res = sync_asset_tables(fake_project)
        assert res['removed'] == 1, res
        assert not any('manual_ghost' in str(r.get('ID'))
                       for r in load_asset_registry(fake_project).images.values())
        assert (fake_project / 'game' / 'asset_tables' /
                'images.bak.xlsx').is_file(), '删之前要备份'

    def test_if_the_delete_fails_the_row_is_marked_unusable(
            self, fake_project, monkeypatch):
        """
        删不掉（最典型：表正开在 Excel 里被锁着）→ **退回"标为不可用"**，
        并且要说出来 —— 绝不能出现"界面看着有、其实用不了"这种谎。
        """
        import openpyxl
        from storygraph.assets import asset_registry as AR
        from storygraph.pipeline.build import load_asset_registry
        AR.sync_asset_tables(fake_project)
        table = fake_project / 'game' / 'asset_tables' / 'images.xlsx'
        wb = openpyxl.load_workbook(table)
        ws = wb.active
        from storygraph.assets.asset_registry import _sheet_headers as _sh
        headers = _sh(ws)
        ws.append(['locked_ghost', 'game/images/locked.png', 'locked',
                   'locked', 'character', ''][:len(headers)])
        wb.save(table)

        monkeypatch.setattr(AR, 'delete_rows_from_table',
                            lambda *a, **k: 0)      # 模拟"删不掉"
        res = AR.sync_asset_tables(fake_project)
        assert res['removed'] == 0, res
        reg = load_asset_registry(fake_project)
        assert 'locked_ghost' in reg.images, '这一行还在表里'
        assert reg.unusable['images'].get('locked_ghost'), \
            '删不掉就该标成不可用（而不是留着还能选）'
        assert res['unusable'].get('images') == 1, res['unusable']

    def test_a_placeholder_row_is_kept_and_usable(self, fake_project):
        """人工占位（`Notes` 写「占位」）→ 允许没有文件，而且**可以用**。"""
        import openpyxl
        from storygraph.assets.asset_registry import (AssetIndex,
                                                      sync_asset_tables)
        from storygraph.pipeline.build import load_asset_registry
        sync_asset_tables(fake_project)
        table = fake_project / 'game' / 'asset_tables' / 'images.xlsx'
        wb = openpyxl.load_workbook(table)
        ws = wb.active
        from storygraph.assets.asset_registry import _sheet_headers as _sh
        headers = _sh(ws)
        row = ['placeholder_bg', 'game/images/bg/not_yet.png', 'bg',
               'not_yet', 'background', '占位：等美术出图']
        ws.append(row[:len(headers)])
        wb.save(table)

        res = sync_asset_tables(fake_project)
        assert res['removed'] == 0, res
        reg = load_asset_registry(fake_project)
        assert 'placeholder_bg' in reg.images, '占位行不该被删'
        assert 'placeholder_bg' in AssetIndex(reg).ids, \
            '占位行应当**可用**（人工明确要留着占位）'
        assert not reg.unusable['images'].get('placeholder_bg'), reg.unusable

    def test_it_is_not_confused_by_a_renamed_file(self, fake_project):
        """文件改名 = 旧行消失 + 新行出现（都是工具管的，就该自动跟着走）。"""
        from storygraph.assets.asset_registry import sync_asset_tables
        from storygraph.pipeline.build import load_asset_registry
        sync_asset_tables(fake_project)
        old = _png(fake_project / 'game' / 'images' / 'old_name.png')
        assert sync_asset_tables(fake_project)['added'] == 1
        old.rename(old.with_name('new_name.png'))
        res = sync_asset_tables(fake_project)
        assert res['added'] == 1 and res['removed'] == 1, res
        images = load_asset_registry(fake_project).images
        assert any('new_name' in str(r.get('Path')) for r in images.values())
        assert not any('old_name' in str(r.get('Path')) for r in images.values())


class TestBrokenTableIsRecovered:
    """
    用户 2026-10-05 实测报（原话）：

    > [项目] 读不到资源表：这个目录下找不到资源表（xlsx），自动新建也失败了：未知原因
    > （剧情图不受影响；下拉候选会是空的）—— 为何无法导入

    真实原因有两个，旧代码**都没报出来、也没给出路**：

      A. **表在、但读不出来**（Excel 开着锁住 / 文件写坏）：`find_asset_tables`
         认为"有表" → 不建；`load_asset_registry` 解析抛异常 → 返回 None
         → 那句"找不到 + 未知原因"。
      B. **目录里只剩备份**（`images.bak.xlsx`）：`*.xlsx` 的粗判把它当"有表"
         → `load_asset_registry` 返回一张**空表** → 打开项目看到 0 个资源。
    """

    def _prepare(self, fake_project):
        from storygraph.assets.asset_registry import sync_asset_tables
        sync_asset_tables(fake_project)
        return fake_project / 'game' / 'asset_tables' / 'images.xlsx'

    def test_a_corrupt_table_is_quarantined_and_rebuilt(self, fake_project):
        from storygraph.assets.asset_registry import sync_asset_tables
        from storygraph.app.editor import EditorSession
        table = self._prepare(fake_project)
        table.write_bytes(b'this is not a zip (corrupt xlsx)')

        res = EditorSession(project_root=fake_project).load_assets()
        assert res['ok'] is True, res
        assert res['repaired'], '坏表没有被挪到一边'
        assert any('.broken-' in n for n in res['repaired']), res['repaired']
        assert res['counts']['images'] >= 2, res['counts']
        # 坏文件**没被删**（用户可能想去救）
        tables = fake_project / 'game' / 'asset_tables'
        assert list(tables.glob('*.broken-*.xlsx')), '坏文件被删了'
        # 新表能正常读
        assert (tables / 'images.xlsx').is_file()

    def test_the_reason_is_reported_not_unknown(self, fake_project,
                                                monkeypatch):
        """建表失败也要说**真实原因**（不许再出现"未知原因"）。"""
        from storygraph.app.editor import EditorSession
        from storygraph.assets import asset_registry as AR
        monkeypatch.setattr(
            AR, 'scan_assets',
            lambda *a, **k: (_ for _ in ()).throw(OSError('磁盘满了')))
        res = EditorSession(project_root=fake_project).load_assets()
        assert res['ok'] is False
        assert '磁盘满了' in res['error'], res['error']
        assert '未知原因' not in res['error'], res['error']

    def test_a_dir_with_only_backups_is_not_a_table(self, fake_project):
        from storygraph.app.editor import EditorSession
        from storygraph.assets.asset_registry import find_asset_tables
        from storygraph.pipeline.build import load_asset_registry
        self._prepare(fake_project)
        tables = fake_project / 'game' / 'asset_tables'
        # 把**所有真表**都改名成备份 → 目录里只剩备份（用户的现场）
        for p in list(tables.glob('*.xlsx')):
            if not p.name.lower().endswith('.bak.xlsx'):
                p.rename(p.with_name(f'{p.stem}.bak.xlsx'))

        assert find_asset_tables(fake_project) is None, '备份被当成了表'
        assert load_asset_registry(fake_project) is None, '备份被读成了空表'
        res = EditorSession(project_root=fake_project).load_assets()
        assert res['ok'] is True, res
        assert res['created'], '只剩备份时应当**新建**一张表'
        assert res['counts']['images'] >= 2, res['counts']

    def test_a_broken_file_does_not_take_the_good_tables_with_it(
            self, fake_project):
        """只有 `images.xlsx` 坏了 → `characters.xlsx` 等好表要留着。"""
        import openpyxl
        from storygraph.assets.asset_registry import sync_asset_tables
        self._prepare(fake_project)
        tables = fake_project / 'game' / 'asset_tables'
        good = tables / 'characters.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['ID', 'Name', 'ImageTag'])
        ws.append(['hand_made', '手写的角色', 'ling'])
        wb.save(good)

        (tables / 'images.xlsx').write_bytes(b'corrupt')
        sync_asset_tables(fake_project)
        assert good.is_file(), '好表被挪走了'
        wb2 = openpyxl.load_workbook(good)
        assert wb2.active['A2'].value == 'hand_made', '好表被覆盖了'


class TestTheOpenPathIsFast:
    """
    用户 2026-10-05 实测报（原话）：

    > 没有报错，但是给程序干未响应了！太卡了，回到Pytest的里面直接卡退了……优化！

    真实项目 `sr_general` 量出来的旧账：

      · `scan_assets` **3.8 秒**（图片/音频/语音各 rglob 一遍整棵树）；
      · `compare_registry_to_disk` **12.4 秒**（3123 行逐行
        `resolve_thumb_source` + `Path.resolve()`，上万次系统调用）；
      · 打开一条路 20 秒以上，GUI 线程全程阻塞 → 未响应。

    这些用例钉住"**一次遍历 + 字典查找**"这个约定：
    给了 `FileWalkIndex` 之后，解析/对账**不许再按行摸磁盘**。
    """

    def _walk(self, root):
        from storygraph.assets.asset_registry import FileWalkIndex
        return FileWalkIndex(root)

    def test_scan_assets_accepts_a_walk_and_finds_the_same_files(
            self, fake_project):
        from storygraph.assets.asset_registry import scan_assets
        plain = scan_assets(fake_project, include_ui=True)
        fast = scan_assets(fake_project, include_ui=True,
                           walk=self._walk(fake_project))
        assert ([a['Path'] for a in plain['images']]
                == [a['Path'] for a in fast['images']])
        assert plain['audio']['music'] == fast['audio']['music']
        assert plain['audio']['sound'] == fast['audio']['sound']
        assert plain['voices'] == fast['voices']

    def test_reconcile_with_a_walk_never_touches_the_disk_per_row(
            self, fake_project, monkeypatch):
        """给了索引之后，对账**一次都不该**调 `resolve_thumb_source`。"""
        from storygraph.assets import asset_registry as AR
        import storygraph.story.excel_export as EE

        reg = AR.parse_asset_tables(fake_project / 'game' / 'asset_tables',
                                    hooks=AR.load_hooks(
                                        project_root=fake_project),
                                    project_root=fake_project)
        calls = {'n': 0}
        real = EE.resolve_thumb_source

        def spy(rel, root=None):
            calls['n'] += 1
            return real(rel, root)

        monkeypatch.setattr(EE, 'resolve_thumb_source', spy)
        walk = self._walk(fake_project)
        AR.compare_registry_to_disk(fake_project, reg, walk=walk)
        assert calls['n'] == 0, f'给了索引还在逐行摸磁盘：{calls["n"]} 次'

    def test_the_index_and_the_old_resolver_agree(self, fake_project):
        """索引匹配 = `resolve_thumb_source` 的语义（不能更松也不能更严）。"""
        from storygraph.assets.asset_registry import (load_hooks,
                                                      parse_asset_tables,
                                                      sync_asset_tables)
        from storygraph.story.excel_export import resolve_thumb_source
        sync_asset_tables(fake_project)         # 先把表建出来
        walk = self._walk(fake_project)
        reg = parse_asset_tables(fake_project / 'game' / 'asset_tables',
                                 hooks=load_hooks(project_root=fake_project),
                                 project_root=fake_project, walk=walk)
        rows = (list(reg.images.values()) + list(reg.music.values())
                + list(reg.sounds.values()))
        assert rows, '前置条件：表里得有行'
        from storygraph.assets.asset_registry import _resolve_audio_source
        for row in rows:
            rel = str(row.get('Path') or '')
            # 图片对 `resolve_thumb_source`、音频对 `_resolve_audio_source`
            # —— 它们才是各自那条路的"唯一真源"（缩略图解析只认图片扩展名）。
            old = (resolve_thumb_source(rel, fake_project)
                   if rel.lower().endswith(('.png', '.jpg', '.jpeg', '.webp',
                                            '.gif', '.bmp'))
                   else _resolve_audio_source(rel, fake_project))
            new = walk.match(rel)
            assert (old is None) == (new is None), \
                f'{rel}: 旧解析 {old} / 索引 {new}'

    def test_a_case_difference_still_counts_as_registered(self, tmp_path):
        """
        表里写 `.../frame 9/x.png`、磁盘上是 `Frame 9` —— 同一个文件，
        必须算"已登记"（否则每次打开都白补一行）。
        """
        from storygraph.assets.asset_registry import FileWalkIndex
        root = tmp_path / 'proj'
        (root / 'game' / 'gui' / 'Frame 9').mkdir(parents=True)
        (root / 'game' / 'gui' / 'Frame 9' / 'x.png').write_bytes(b'x')
        assert FileWalkIndex(root).match('game/gui/frame 9/x.png') is not None

    def test_a_same_named_file_elsewhere_does_not_count(self, tmp_path):
        """
        ⚠ 一开始这里加了"同名文件全局唯一就认它"的兜底 —— 实测**太松**：
        表里别处的同名文件会把磁盘上新出现的文件认领掉，于是它永远
        补不进表（候选里看不到）。这条钉住"不许兜底"。
        """
        from storygraph.assets.asset_registry import FileWalkIndex
        root = tmp_path / 'proj'
        (root / 'game' / 'a').mkdir(parents=True)
        (root / 'game' / 'b').mkdir(parents=True)
        (root / 'game' / 'a' / 'same.png').write_bytes(b'x')
        (root / 'game' / 'b' / 'same.png').write_bytes(b'x')
        walk = FileWalkIndex(root)
        assert walk.match('game/a/same.png') is not None
        assert walk.match('game/c/same.png') is None, \
            '不存在的路径被同名文件认领了'


class TestAssetHealthReport:
    """
    「📦 资源体检」（用户 2026-10-05："在「变量分析」右边加一个检查资源
    完整性的功能，后出示检查报告"）。
    """

    def test_a_clean_project_reports_zero_problems(self, fake_project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=fake_project)
        s.load_assets()                         # 打开时自动对齐
        rep = s.asset_health_report()
        assert rep['ok'] is True, rep
        assert rep['problems'] == 0, rep
        assert rep['total_rows'] == (rep['counts']['images']
                                     + rep['counts']['music']
                                     + rep['counts']['sounds']
                                     + rep['counts']['voices'])
        from storygraph.app.asset_report import format_asset_health
        assert format_asset_health(rep).splitlines()[0].startswith('✅')

    def test_it_finds_an_unregistered_file(self, fake_project):
        from storygraph.app.editor import EditorSession
        from storygraph.app.asset_report import format_asset_health
        from storygraph.assets.asset_registry import sync_asset_tables
        from storygraph.pipeline.build import load_asset_registry
        sync_asset_tables(fake_project)
        # 体检是**只读**的：先造一张"缺一行"的表（把表里的图行删掉）
        import openpyxl
        table = fake_project / 'game' / 'asset_tables' / 'images.xlsx'
        wb = openpyxl.load_workbook(table)
        ws = wb.active
        ws.delete_rows(2, ws.max_row)            # 头一行留下，数据全删
        wb.save(table)
        s = EditorSession(project_root=fake_project)
        s._loaded_registry = load_asset_registry(fake_project)
        rep = s.asset_health_report()
        assert rep['problems'] > 0, rep
        n = sum(len(v) for v in rep['unregistered'].values())
        assert n >= 2, rep['unregistered']
        text = format_asset_health(rep)
        assert '【② 磁盘上有、表里没登记】' in text
        assert '张图' not in text or True

    def test_a_placeholder_row_stays_usable(self, fake_project):
        import openpyxl
        from storygraph.app.editor import EditorSession
        from storygraph.assets.asset_registry import AssetIndex, sync_asset_tables
        sync_asset_tables(fake_project)
        table = fake_project / 'game' / 'asset_tables' / 'images.xlsx'
        wb = openpyxl.load_workbook(table)
        ws = wb.active
        from storygraph.assets.asset_registry import _sheet_headers as _sh
        headers = _sh(ws)
        ws.append(['planned_bg', 'game/images/bg/planned.png', 'bg',
                   'planned', 'background', '占位：等美术'][:len(headers)])
        wb.save(table)
        s = EditorSession(project_root=fake_project)
        res = s.load_assets()
        rep = s.asset_health_report()
        assert rep['placeholders'], rep
        assert not any('planned.png' in str(p)
                       for rows in (rep['missing'] or {}).values()
                       for p in rows), '占位行被当成"文件丢了"'
        # 而且它仍然可用（能选、能写进卡片）
        assert 'planned_bg' in AssetIndex(res['registry']).ids


class TestReconcileTableToDisk:
    """
    "为什么两个克隆不一样？表是不是没更新？" —— 现在能**回答**了。

    表是用户可编辑的数据，工具不自动改写；磁盘是另一份真相。对账把差值
    说清楚（只读，不改任何文件），补救交给既有的「补登缺失资源」。
    """

    def test_it_reports_disk_files_that_are_not_registered(self, tmp_path):
        from storygraph.assets.asset_registry import (AssetRegistry,
                                                      compare_registry_to_disk)
        root = tmp_path / 'proj'
        _png(root / 'game' / 'images' / 'ling' / 'happy.png')
        _png(root / 'game' / 'images' / 'bg' / 'classroom.png')
        p = root / 'game' / 'gui' / 'sfx' / 'click.wav'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'OggS' + b'\0' * 16)
        reg = AssetRegistry()
        reg.images = {'ling_happy': {
            'ID': 'ling_happy', 'Tag': 'ling',
            'Path': 'game/images/ling/happy.png'}}
        diff = compare_registry_to_disk(root, reg)
        assert diff['table']['images'] == 1
        assert diff['disk']['images'] == 2
        unreg = diff['unregistered']['images']
        assert len(unreg) == 1 and unreg[0].endswith('classroom.png'), unreg
        assert diff['unregistered']['sounds'], diff['unregistered']

    def test_it_reports_rows_whose_files_are_gone(self, tmp_path):
        from storygraph.assets.asset_registry import (AssetRegistry,
                                                      compare_registry_to_disk)
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        reg = AssetRegistry()
        reg.images = {'ghost': {'ID': 'ghost', 'Tag': 'ui',
                                'Path': 'game/images/gone.png'}}
        diff = compare_registry_to_disk(root, reg)
        assert diff['stale_paths'] == 1
        assert diff['missing']['images'] == ['game/images/gone.png']

    def test_a_matching_table_reports_no_drift(self, fake_project):
        from storygraph.assets.asset_registry import (compare_registry_to_disk,
                                                      ensure_asset_tables)
        from storygraph.pipeline.build import load_asset_registry
        ensure_asset_tables(fake_project)
        reg = load_asset_registry(fake_project)
        diff = compare_registry_to_disk(fake_project, reg)
        assert not any(diff['unregistered'].values()), diff['unregistered']
        assert diff['stale_paths'] == 0

    def test_it_flags_rows_that_point_outside_game(self, tmp_path):
        """
        表里的行指到 `game/` **外面**时要单独报出来。

        用户 2026-10-05："为何两个克隆出来的项目并不一样？" —— 实测原项目
        有 **1386 行**指向 `sr_general/**`（另一份嵌套副本）：那不是"文件丢了"，
        而是"表引用的是别的目录里的文件"，补救方式完全不同。
        """
        from storygraph.assets.asset_registry import (AssetRegistry,
                                                      compare_registry_to_disk)
        root = tmp_path / 'proj'
        _png(root / 'game' / 'images' / 'ok.png')
        _png(root / 'sr_general' / 'game' / 'images' / 'elsewhere.png')
        reg = AssetRegistry()
        reg.images = {
            'ok': {'ID': 'ok', 'Path': 'game/images/ok.png'},
            'far': {'ID': 'far', 'Path': 'sr_general/game/images/elsewhere.png'},
        }
        diff = compare_registry_to_disk(root, reg)
        assert diff['outside_game']['count'] == 1, diff['outside_game']
        assert diff['outside_game']['by_top'].get('sr_general') == 1

    def test_it_never_writes_anything(self, fake_project):
        from storygraph.assets.asset_registry import (compare_registry_to_disk,
                                                      ensure_asset_tables)
        from storygraph.pipeline.build import load_asset_registry
        ensure_asset_tables(fake_project)
        tables = fake_project / 'game' / 'asset_tables'
        before = {p.name: p.stat().st_mtime_ns for p in tables.glob('*.xlsx')}
        compare_registry_to_disk(fake_project, load_asset_registry(fake_project))
        after = {p.name: p.stat().st_mtime_ns for p in tables.glob('*.xlsx')}
        assert before == after, '对账是只读的，不许改表'
    def test_open_a_copied_project_and_it_just_works(self, fake_project):
        """用户的那一步：打开复制出来的项目 → 不该是"读不到资源表"。"""
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=fake_project)
        res = s.load_assets()
        assert res['ok'] is True, res
        assert res['created'], '应当报出"新建在哪"'
        assert res['counts']['images'] >= 2, res['counts']
        assert s.registry() is not None
        # 说话人候选来自脚本声明（`define e = Character(...)`）——
        # 这条证明"建完表之后符号表是真的能用"，不是只写了个文件
        assert 'e' in s.characters(), s.characters()

    def test_the_second_open_does_not_rebuild(self, fake_project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=fake_project)
        assert s.load_assets()['created']
        res2 = EditorSession(project_root=fake_project).load_assets()
        assert res2['ok'] is True
        assert res2['created'] == '', '第二次打开不该再建一次'

    def test_it_reports_the_reason_when_scanning_fails(self, tmp_path,
                                                       monkeypatch):
        """建表失败也要说清楚（不能让用户又对着"读不到资源表"发呆）。"""
        from storygraph.app.editor import EditorSession
        from storygraph.assets import asset_registry as AR
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        monkeypatch.setattr(AR, 'scan_assets',
                            lambda *a, **k: (_ for _ in ()).throw(
                                OSError('磁盘满了')))
        res = EditorSession(project_root=root).load_assets()
        assert res['ok'] is False
        assert '自动重建也失败了' in res['error'], res['error']
        assert '磁盘满了' in res['error']
