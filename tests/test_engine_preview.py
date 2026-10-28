"""
tests/test_engine_preview.py
============================
「🎮 引擎试玩」后端（`storygraph.app.engine_preview`）的判据。

这一批**不启动真引擎**（CI 上没有 Ren'Py，也不该为一条用例等 10 秒），
而是把"最危险、也最容易悄悄错"的部分钉死：

  · **镜像绝不写用户项目**：脚本逐字节复制、资源目录 junction，源里删掉的
    文件镜像里也删，**目录从"资源"变成"脚本目录"时先把 junction 摘掉再复制**
    （反过来的顺序会把副本写进用户真实目录 —— 那是毁项目级别的事故）；
  · **入口注入**：`config.label_overrides["start"]` 指向注入 label，
    项目里撞名要自动换名；`mode='start'` 不注入任何东西；
  · **目标解析**：优先 `<label>_control`（编排层），找不到 label 明确报错；
    `mode='beat'` 生成合法的 `--warp game/…:行号`；
  · **启动参数/环境**：`--savedir` 在镜像里、`APPDATA`/`HOME` 重定向、
    `RENPY_SKIP_*` 与 `RENPY_DISABLE_BACKUPS` 在（否则点一次试玩就在用户
    真实 `%APPDATA%\\RenPy\\backups` 里多一份脚本备份）；
  · **失败说人话**：没引擎、项目不对、引擎起不来 → `EnginePreviewError`
    （消息能直接给用户看），不抛裸 `OSError`。

真引擎的端到端验收在 `tools/` 探针里做（本机有 SDK 时），CI 不跑。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app import engine_preview as EP      # noqa: E402

WIN_ONLY = pytest.mark.skipif(not sys.platform.startswith('win'),
                              reason='junction 是 Windows 的目录联接')


# ---------------------------------------------------------------------------
#  造工程 / 造引擎 / 环境隔离
# ---------------------------------------------------------------------------

@pytest.fixture
def cache(tmp_path, monkeypatch):
    """所有镜像都落在 tmp（绝不去动真实 `%LOCALAPPDATA%`）。"""
    root = tmp_path / 'engine_cache'
    monkeypatch.setenv('STORYGRAPH_ENGINE_CACHE', str(root))
    monkeypatch.setenv('STORYGRAPH_STAGE_EDITION', '1')
    monkeypatch.delenv('STORYGRAPH_RENPY_HOME', raising=False)
    monkeypatch.delenv('RENPY_SDK_HOME', raising=False)
    return root


@pytest.fixture
def project(tmp_path) -> Path:
    """一个"像真项目"的最小工程：有脚本 / rpyc / 资源目录 / 运行产物。"""
    root = tmp_path / 'MyGame'
    game = root / 'game'
    (game / 'chapter').mkdir(parents=True)
    (game / 'images' / 'bg').mkdir(parents=True)
    (game / 'bgm').mkdir()
    (game / 'saves').mkdir()
    (game / 'cache').mkdir()
    (game / 'chapter' / 'ch1.rpy').write_text(
        'label ch1:\n    "第一句"\n    return\n', encoding='utf-8')
    (game / 'chapter' / 'ch1.rpyc').write_bytes(b'compiled-rpyc')
    (game / 'chapter' / 'ch1.rpy.bak').write_bytes(b'backup')
    (game / 'chapter' / 'ctrl.rpy').write_text(
        'label ch1_control:\n    call ch1\n    return\n', encoding='utf-8')
    (game / 'images' / 'bg' / 'room.png').write_bytes(b'\x89PNG-room')
    (game / 'bgm' / 'night.ogg').write_bytes(b'ogg-night')
    (game / 'saves' / 's1.save').write_bytes(b'save')
    (game / 'cache' / 'shaders.txt').write_text('cache', encoding='utf-8')
    (game / 'old.rpyb').write_bytes(b'bytecode')
    (root / 'project.json').write_text('{"name": "MyGame"}', encoding='utf-8')
    (root / 'icon.ico').write_bytes(b'\x00\x00\x01\x00')
    return root


@pytest.fixture
def fake_engine(tmp_path) -> Path:
    """假引擎目录：只要求有 renpy.exe + renpy/（启动会被打桩，不会真跑）。"""
    home = tmp_path / 'sdk'
    (home / 'renpy').mkdir(parents=True)
    (home / 'renpy.exe').write_bytes(b'MZ-fake')
    (home / 'renpy' / 'vc_version.py').write_text(
        "version = '8.5.3.26051504'\n", encoding='utf-8')
    return home


@pytest.fixture
def no_dev_sdk(monkeypatch):
    """把"开发机上的 SDK 候选"清空，保证"没引擎"那条路径真的没引擎。"""
    monkeypatch.setattr(EP, '_DEV_SDK_CANDIDATES', ())
    monkeypatch.setattr(EP, 'app_dir', lambda: None)


class _FakeProc:
    """`subprocess.Popen` 的替身：记录参数、可假装退出。"""

    instances: list = []

    def __init__(self, args, **kwargs):
        self.args = list(args)
        self.kwargs = dict(kwargs)
        self.pid = 424242
        self.returncode = None
        self.terminated = False
        _FakeProc.instances.append(self)

    def poll(self):
        return self.returncode

    # `subprocess.run(...)` 内部是 `with Popen(...) as p:`，打桩要能进上下文
    # （镜像里建 junction / 停引擎都走 `subprocess.run`，少这一个方法就炸）
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, input=None, timeout=None):
        """`subprocess.run(capture_output=True)` 会调它（建 junction 用）。"""
        if self.returncode is None:
            self.returncode = 0
        return b'', b''


@pytest.fixture
def fake_popen(monkeypatch):
    """拦下 `Popen`：不真起进程，但参数/环境原样交给断言。"""
    _FakeProc.instances = []
    monkeypatch.setattr(EP.subprocess, 'Popen', _FakeProc)
    return _FakeProc


# ---------------------------------------------------------------------------
#  引擎发现
# ---------------------------------------------------------------------------

class TestFindEngine:

    def test_explicit_wins(self, fake_engine, no_dev_sdk):
        hit = EP.find_engine(explicit=fake_engine)
        assert hit is not None
        assert hit.home == fake_engine.resolve()
        assert hit.version() == '8.5.3.26051504'

    def test_half_copied_sdk_is_not_an_engine(self, tmp_path, no_dev_sdk):
        """只有 renpy/ 没有 renpy.exe 的目录 = 拷到一半，不能算找到。"""
        bad = tmp_path / 'bad'
        (bad / 'renpy').mkdir(parents=True)
        assert EP.find_engine(explicit=bad) is None

    def test_env_var_is_honoured(self, fake_engine, monkeypatch, no_dev_sdk):
        monkeypatch.setenv('STORYGRAPH_RENPY_HOME', str(fake_engine))
        hit = EP.find_engine()
        assert hit is not None and hit.home == fake_engine.resolve()

    def test_packaged_runtime_next_to_exe(self, fake_engine, monkeypatch):
        """演出专用版：exe 旁边的 `renpy_sdk/` 就是它（不用环境变量）。"""
        pkg = fake_engine.parent
        (pkg / EP.RUNTIME_DIRNAME / 'renpy').mkdir(parents=True)
        (pkg / EP.RUNTIME_DIRNAME / 'renpy.exe').write_bytes(b'MZ')
        monkeypatch.setattr(EP, 'app_dir', lambda: pkg)
        hit = EP.find_engine()
        assert hit is not None
        assert hit.home.name == EP.RUNTIME_DIRNAME

    def test_preview_supported_reports_how_to_fix(self, no_dev_sdk):
        ok, message = EP.preview_supported()
        assert ok is False
        assert '演出专用版' in message or 'STORYGRAPH_RENPY_HOME' in message

    def test_stage_hint_mentions_antivirus(self, monkeypatch, no_dev_sdk):
        monkeypatch.setenv('STORYGRAPH_STAGE_EDITION', '1')
        ok, message = EP.preview_supported()
        assert ok is False
        assert 'renpy_sdk' in message


# ---------------------------------------------------------------------------
#  目标解析
# ---------------------------------------------------------------------------

class TestMakeTarget:

    def test_prefers_control_label(self, project):
        t = EP.make_target(project, label='ch1')
        assert t.mode == 'chapter'
        assert t.label == 'ch1_control'       # 编排层：进章 + 后续菜单
        assert t.chapter_label == 'ch1'

    def test_falls_back_to_plain_label(self, project):
        (project / 'game' / 'chapter' / 'solo.rpy').write_text(
            'label solo:\n    return\n', encoding='utf-8')
        t = EP.make_target(project, label='solo')
        assert t.label == 'solo'

    def test_unknown_label_is_an_error_with_hint(self, project):
        with pytest.raises(EP.EnginePreviewError) as exc:
            EP.make_target(project, label='chapter9_9')
        assert 'chapter9_9' in str(exc.value)
        assert '导出' in exc.value.hint

    def test_not_a_project_is_an_error(self, tmp_path):
        with pytest.raises(EP.EnginePreviewError) as exc:
            EP.make_target(tmp_path, label='ch1')
        assert 'game/' in str(exc.value)

    def test_start_mode_needs_nothing(self, project):
        t = EP.make_target(project, mode='start')
        assert t.mode == 'start' and t.label == 'start'

    def test_beat_mode_builds_warp_spec(self, project):
        t = EP.make_target(project, label='ch1',
                           file_path=project / 'game/chapter/ch1.rpy',
                           line=2, mode='beat')
        assert t.mode == 'beat'
        assert t.warp_spec == 'game/chapter/ch1.rpy:2'

    def test_label_can_come_from_file_path(self, project):
        t = EP.make_target(project,
                           file_path=project / 'game/chapter/ch1.rpy')
        assert t.chapter_label == 'ch1'

    def test_empty_session_is_told_to_load_a_chapter(self, project):
        with pytest.raises(EP.EnginePreviewError) as exc:
            EP.make_target(project)
        assert '还不知道' in str(exc.value)
        assert '载入一章' in exc.value.hint


# ---------------------------------------------------------------------------
#  镜像
# ---------------------------------------------------------------------------

class TestMirror:

    def test_scripts_are_copied_byte_for_byte(self, project, cache):
        mir = EP.mirror_project(project)
        src = project / 'game/chapter/ch1.rpy'
        dst = mir / 'game/chapter/ch1.rpy'
        assert dst.read_bytes() == src.read_bytes()
        # mtime 保留 = 引擎不会白重编译一遍
        assert dst.stat().st_mtime_ns == src.stat().st_mtime_ns
        assert (mir / 'game/chapter/ch1.rpyc').read_bytes() == b'compiled-rpyc'

    def test_engine_byproducts_are_not_copied(self, project, cache):
        mir = EP.mirror_project(project)
        assert not (mir / 'game/saves').exists()
        assert not (mir / 'game/cache').exists()
        assert not (mir / 'game/chapter/ch1.rpy.bak').exists()
        assert not (mir / 'game/old.rpyb').exists()

    def test_root_small_files_are_copied(self, project, cache):
        mir = EP.mirror_project(project)
        assert (mir / 'project.json').is_file()
        assert (mir / 'icon.ico').is_file()

    @WIN_ONLY
    def test_asset_dirs_are_junctions_not_copies(self, project, cache):
        mir = EP.mirror_project(project)
        images = mir / 'game/images'
        assert images.is_dir()
        assert EP._is_junction(images), '资源目录应该是 junction（不复制 2GB 素材）'
        assert (images / 'bg' / 'room.png').read_bytes() == b'\x89PNG-room'

    def test_second_sync_does_not_touch_unchanged_files(self, project, cache):
        EP.mirror_project(project)
        mir = EP.mirror_project(project)
        dst = mir / 'game/chapter/ch1.rpy'
        before = dst.stat().st_mtime_ns
        time.sleep(0.02)
        EP.mirror_project(project)
        assert dst.stat().st_mtime_ns == before

    def test_deleted_source_files_are_removed_from_mirror(self, project, cache):
        mir = EP.mirror_project(project)
        assert (mir / 'game/chapter/ctrl.rpy').is_file()
        (project / 'game/chapter/ctrl.rpy').unlink()
        mir = EP.mirror_project(project)
        assert not (mir / 'game/chapter/ctrl.rpy').exists()

    @WIN_ONLY
    def test_dir_switching_from_junction_to_scripts_never_writes_source(
            self, project, cache):
        """
        最危险的一条：资源目录（junction）里后来出现了 `.rpy`。

        正确顺序是**先摘 junction、再复制**；反过来 `copy2` 会顺着 junction
        写进用户的真实目录 —— 工具"复制"一个文件，用户的源文件就被改了。
        """
        data = project / 'game' / 'data'
        data.mkdir()
        (data / 'notes.txt').write_text('notes', encoding='utf-8')
        mir = EP.mirror_project(project)
        assert EP._is_junction(mir / 'game' / 'data')

        (data / 'x.rpy').write_text('label x:\n    return\n', encoding='utf-8')
        mir = EP.mirror_project(project)
        assert not EP._is_junction(mir / 'game' / 'data')
        assert (mir / 'game/data/x.rpy').read_bytes() == \
            (data / 'x.rpy').read_bytes()

        # 改镜像里的副本，源文件必须原封不动
        (mir / 'game/data/x.rpy').write_text('CHANGED IN MIRROR',
                                             encoding='utf-8')
        assert (data / 'x.rpy').read_text(encoding='utf-8') == \
            'label x:\n    return\n'

    def test_source_project_is_byte_identical_after_mirror(
            self, project, cache):
        before = {p.relative_to(project).as_posix(): (p.read_bytes(),
                                                      p.stat().st_mtime_ns)
                  for p in project.rglob('*') if p.is_file()}
        EP.mirror_project(project)
        after = {p.relative_to(project).as_posix(): (p.read_bytes(),
                                                     p.stat().st_mtime_ns)
                 for p in project.rglob('*') if p.is_file()}
        assert before == after, '镜像过程写到了源项目'

    @WIN_ONLY
    def test_safe_rmtree_never_follows_junction(self, tmp_path):
        """删镜像树时 junction 只摘链接 —— 绝不跟进去删用户真文件。"""
        outside = tmp_path / 'outside'
        outside.mkdir()
        (outside / 'precious.rpy').write_text('do not delete', encoding='utf-8')
        mirror = tmp_path / 'mirror'
        (mirror / 'game').mkdir(parents=True)
        assert EP._make_junction(mirror / 'game' / 'images', outside)
        EP._rmtree_safe(mirror)
        assert not mirror.exists()
        assert (outside / 'precious.rpy').read_text(encoding='utf-8') == \
            'do not delete'

    def test_mirror_manifest_is_written(self, project, cache):
        mir = EP.mirror_project(project)
        data = json.loads((mir.parent / 'mirror.json').read_text(
            encoding='utf-8'))
        assert data['project'] == str(project.resolve())
        assert 'game/chapter/ch1.rpy' in data['files']


# ---------------------------------------------------------------------------
#  注入入口
# ---------------------------------------------------------------------------

class TestInjectedEntry:

    def test_chapter_entry_overrides_start(self, project, cache):
        mir = EP.mirror_project(project)
        t = EP.make_target(project, label='ch1')
        path = EP._write_entry(mir, t)
        text = path.read_text(encoding='utf-8')
        assert 'config.label_overrides["start"]' in text
        assert 'jump ch1_control' in text
        # 注入文件必须在 game/ 下（引擎只加载 game/ 里的脚本）
        assert path.parent == mir / 'game'

    def test_label_collision_gets_a_suffix(self, project):
        (project / 'game/chapter' / 'zz_collide.rpy').write_text(
            'label sg_engine_preview:\n    return\n', encoding='utf-8')
        assert EP._entry_label(project) != 'sg_engine_preview'

    def test_start_mode_writes_nothing(self, project, cache):
        mir = EP.mirror_project(project)
        t = EP.make_target(project, mode='start')
        EP._write_entry(mir, t)
        assert not (mir / 'game' / EP.INJECT_NAME).exists()


# ---------------------------------------------------------------------------
#  启动（打桩 Popen）
# ---------------------------------------------------------------------------

class TestLaunch:

    def test_command_and_env_are_isolated(self, project, cache, fake_engine,
                                         fake_popen):
        t = EP.make_target(project, label='ch1')
        run = EP.launch(t, engine=EP.find_engine(explicit=fake_engine))
        proc = fake_popen.instances[-1]
        cmd = proc.args
        assert cmd[0].endswith('renpy.exe')
        assert cmd[2] == 'run'
        assert '--savedir' in cmd
        savedir = Path(cmd[cmd.index('--savedir') + 1])
        assert str(savedir).startswith(str(run.mirror_root))
        env = proc.kwargs['env']
        assert env['RENPY_SKIP_MAIN_MENU'] == '1'
        assert env['RENPY_SKIP_SPLASHSCREEN'] == '1'
        assert env['RENPY_DISABLE_BACKUPS'] == 'I take responsibility for this.'
        assert str(run.mirror_root) in env['APPDATA']
        assert str(run.mirror_root) in env['HOME']
        # 注入文件必须在（否则引擎还是从 label start 开始）
        assert (run.project_dir / 'game' / EP.INJECT_NAME).is_file()

    def test_beat_mode_passes_warp(self, project, cache, fake_engine,
                                  fake_popen):
        t = EP.make_target(project, label='ch1',
                           file_path=project / 'game/chapter/ch1.rpy',
                           line=2, mode='beat')
        EP.launch(t, engine=EP.find_engine(explicit=fake_engine))
        cmd = fake_popen.instances[-1].args
        assert '--warp' in cmd
        assert cmd[cmd.index('--warp') + 1] == 'game/chapter/ch1.rpy:2'

    def test_capture_mode_injects_screenshot_hook(self, project, cache,
                                                  fake_engine, fake_popen):
        t = EP.make_target(project, label='ch1')
        run = EP.launch(t, engine=EP.find_engine(explicit=fake_engine),
                        capture=True)
        cap = run.project_dir / 'game' / 'zzz_storygraph_engine_capture.rpy'
        assert cap.is_file()
        assert 'renpy.screenshot' in cap.read_text(encoding='utf-8')

    def test_popen_failure_is_a_friendly_error(self, project, cache,
                                               fake_engine, monkeypatch):
        def boom(*_a, **_k):
            raise OSError('cannot execute')
        monkeypatch.setattr(EP.subprocess, 'Popen', boom)
        t = EP.make_target(project, label='ch1')
        with pytest.raises(EP.EnginePreviewError) as exc:
            EP.launch(t, engine=EP.find_engine(explicit=fake_engine))
        assert '引擎起不来' in str(exc.value)
        assert str(fake_engine) in exc.value.hint

    def test_missing_engine_is_a_friendly_error(self, project, cache,
                                                no_dev_sdk, monkeypatch):
        t = EP.make_target(project, label='ch1')
        with pytest.raises(EP.EnginePreviewError) as exc:
            EP.launch(t)
        assert '没找到内置' in str(exc.value)
        assert exc.value.hint

    def test_stop_terminates_process_and_is_idempotent(self, project, cache,
                                                      fake_engine, fake_popen):
        t = EP.make_target(project, label='ch1')
        run = EP.launch(t, engine=EP.find_engine(explicit=fake_engine))
        run.stop()
        assert fake_popen.instances[-1].terminated
        assert not run.running()
        run.stop()                        # 第二次不该抛


class TestRunDiagnostics:

    def test_error_report_prefers_errors_txt(self, project, cache,
                                            fake_engine, fake_popen):
        t = EP.make_target(project, label='ch1')
        run = EP.launch(t, engine=EP.find_engine(explicit=fake_engine))
        (run.project_dir / 'errors.txt').write_text(
            'NameError: love', encoding='utf-8')
        assert 'NameError' in run.error_report()

    def test_log_tail_is_available(self, project, cache, fake_engine,
                                   fake_popen):
        t = EP.make_target(project, label='ch1')
        run = EP.launch(t, engine=EP.find_engine(explicit=fake_engine))
        (run.project_dir / 'log.txt').write_text(
            'line-1\nline-2\nline-3\n', encoding='utf-8')
        tail = run.log_tail(2)
        assert 'line-2' in tail and 'line-3' in tail


# ---------------------------------------------------------------------------
#  演出专用版开关 / 缓存目录
# ---------------------------------------------------------------------------

class TestEditionAndCache:

    def test_env_switch_marks_stage_edition(self, monkeypatch):
        monkeypatch.setenv('STORYGRAPH_STAGE_EDITION', '1')
        assert EP.stage_edition() is True

    def test_marker_file_marks_stage_edition(self, tmp_path, monkeypatch):
        monkeypatch.delenv('STORYGRAPH_STAGE_EDITION', raising=False)
        pkg = tmp_path / 'pkg'
        pkg.mkdir()
        (pkg / EP.EDITION_MARKER).write_text('v1.0.0', encoding='utf-8')
        monkeypatch.setattr(EP, 'app_dir', lambda: pkg)
        assert EP.stage_edition() is True

    def test_standard_edition_has_no_marker(self, tmp_path, monkeypatch):
        monkeypatch.delenv('STORYGRAPH_STAGE_EDITION', raising=False)
        pkg = tmp_path / 'pkg'
        pkg.mkdir()
        monkeypatch.setattr(EP, 'app_dir', lambda: pkg)
        assert EP.stage_edition() is False

    def test_cache_root_follows_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv('STORYGRAPH_ENGINE_CACHE', str(tmp_path / 'c'))
        assert EP.cache_root() == tmp_path / 'c'

    def test_mirror_root_is_stable_per_project(self, project, cache):
        assert EP._mirror_root(project) == EP._mirror_root(project)
        other = project.parent / 'Other'
        assert EP._mirror_root(project) != EP._mirror_root(other)

    def test_prune_keeps_newest(self, tmp_path, cache):
        for n in range(EP.KEEP_MIRRORS + 3):
            d = cache / f'm{n}'
            d.mkdir(parents=True)
            os.utime(d, (time.time() + n, time.time() + n))
        EP._prune_mirrors(cache)
        left = sorted(p.name for p in cache.iterdir() if p.is_dir())
        assert len(left) == EP.KEEP_MIRRORS
        assert 'm0' not in left               # 最老的被清掉
