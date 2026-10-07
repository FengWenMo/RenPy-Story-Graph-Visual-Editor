"""
tests/test_crashlog.py
======================
**打包成 exe 之后崩溃必须留下痕迹** —— `storygraph/core/crashlog.py` 的守卫。

为什么单独测（而不是顺手测一下 `main.py`）：这条能力只在**冻结环境**
（`sys.frozen`）里才启用，而测试是源码运行的 —— 也就是说
"打成 exe 之后日志到底写不写得出来"在普通测试里**永远不会被跑到**。
所以这里直接测 crashlog 本身：路径怎么选、写不写得进去、钩子挂了之后
原来的钩子还叫不叫。

判据刻意选"**内容真的能读回来**"而不是"函数被调用了"：
这个模块的价值就是"出事时磁盘上真有一份能看的文件"。
"""

from __future__ import annotations

import sys

import pytest


def _encodable(ch: str, encoding: str) -> bool:
    """这个字符在 `encoding` 下编得出来吗？"""
    try:
        ch.encode(encoding)
        return True
    except UnicodeEncodeError:
        return False


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    """把日志强制指到 tmp 下，并清掉模块内缓存（避免测试间串味）。"""
    from storygraph.core import crashlog

    target = tmp_path / 'crash.log'
    monkeypatch.setenv(crashlog.ENV_OVERRIDE, str(target))
    monkeypatch.setattr(crashlog, '_resolved', None, raising=False)
    monkeypatch.setattr(crashlog, '_installed', False, raising=False)
    return target


class TestLogPath:

    def test_override_env_wins(self, log_file):
        from storygraph.core import crashlog
        assert crashlog.log_path() == log_file

    def test_falls_back_to_something_writable(self, monkeypatch):
        """没有任何环境变量时也必须落到一个**真的能写**的地方。"""
        from storygraph.core import crashlog

        monkeypatch.delenv(crashlog.ENV_OVERRIDE, raising=False)
        monkeypatch.setattr(crashlog, '_resolved', None, raising=False)
        p = crashlog.log_path()
        assert p.parent.is_dir(), f'{p.parent} 没建出来'
        p.write_text('x', encoding='utf-8')      # 真写一次才算数

    def test_path_is_stable_after_first_resolution(self, log_file, monkeypatch):
        """选定的路径不再变 —— 否则"崩了去哪看"每次都不一样。"""
        from storygraph.core import crashlog
        first = crashlog.log_path()
        monkeypatch.setenv(crashlog.ENV_OVERRIDE, 'D:/别的/地方.log')
        assert crashlog.log_path() == first


class TestRecord:

    def test_record_writes_and_reads_back(self, log_file):
        from storygraph.core import crashlog
        crashlog.record('第一段')
        crashlog.record('第二段')
        text = log_file.read_text(encoding='utf-8')
        assert '第一段' in text and '第二段' in text

    def test_never_raises_on_a_bad_path(self, monkeypatch):
        """路径写不进去也**绝不许抛** —— 它就在崩溃路径上。"""
        from storygraph.core import crashlog

        monkeypatch.setattr(crashlog, '_resolved',
                            __import__('pathlib').Path(
                                'Z:/绝对不存在的盘/x/crash.log'),
                            raising=False)
        crashlog.record('这条应该被安静地丢掉')

    @pytest.mark.parametrize('garbage', [None, 123, object()])
    def test_record_survives_garbage(self, log_file, garbage):
        from storygraph.core import crashlog
        crashlog.record(garbage)              # str() 会兜住


class TestInstall:

    def test_install_chains_to_the_previous_hook(self, log_file, monkeypatch):
        """
        **加了钩子不许改变退出行为**：原来的 `sys.excepthook` 必须照常被叫。

        这一条是"只加痕迹、不改行为"的判据 —— 如果 crashlog 把异常吞了，
        崩溃就会变成"安静退出"，比没有日志更糟。
        """
        from storygraph.core import crashlog

        seen = []
        monkeypatch.setattr(sys, 'excepthook',
                            lambda *a: seen.append(a[0]), raising=False)
        path = crashlog.install()
        assert path == log_file
        try:
            raise RuntimeError('演示崩溃')
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
        assert seen and seen[0] is RuntimeError, '原来的钩子没被调用'
        assert '演示崩溃' in log_file.read_text(encoding='utf-8')

    def test_install_is_idempotent(self, log_file):
        from storygraph.core import crashlog
        a = crashlog.install()
        b = crashlog.install()
        assert a == b == log_file

    def test_thread_hook_records_background_failures(self, log_file):
        """
        后台线程（缩略图 / 资源扫描）的异常也要进日志。

        冻结成窗口程序后，线程里的 traceback 默认打到空的 stderr 上 ——
        和主线程崩溃一样"看不见"。

        ⚠ 这里**不真的在测试线程里 raise**（那会让 pytest 报
        `PytestUnhandledThreadExceptionWarning`，把真实告警淹掉）；
        也**不从 `threading.excepthook` 调**（那是全局钩子，pytest 自己的
        `threadexception` 插件会在外面包一层，调它等于测别人的包装）。
        直接调 `_make_thread_hook` 造的那个钩子。
        """
        import threading

        from storygraph.core import crashlog
        hooked = []
        hook = crashlog._make_thread_hook(lambda args: hooked.append(args))
        args = threading.ExceptHookArgs(
            (ValueError, ValueError('后台线程炸了'), None,
             threading.current_thread()))
        hook(args)
        assert '后台线程炸了' in log_file.read_text(encoding='utf-8')
        # **先记日志、再交给原来的钩子** —— 只加痕迹，不改行为
        assert hooked == [args], '没有把异常继续交给原来的线程钩子'

    def test_install_exposes_the_thread_hook_it_installed(self, log_file):
        """`install()` 要留下钩子引用 —— 否则那条路只能靠全局钩子去测。"""
        from storygraph.core import crashlog
        crashlog.install()
        assert crashlog.thread_excepthook is not None


class TestItIsWired:

    def test_frozen_entry_installs_it(self, monkeypatch):
        """
        `storygraph.main` 在**冻结环境**里必须真的装 —— 否则这个模块
        写得再好也没人调（"做了但没接上"正是本仓库反复踩的坑）。
        """
        import storygraph.main as m

        called = {}

        def fake_install():
            called['yes'] = True
            return 'X:/fake/crash.log'

        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        monkeypatch.setattr('storygraph.core.crashlog.install', fake_install)
        assert m._install_crash_log() == 'X:/fake/crash.log'
        assert called.get('yes'), '冻结环境里没有装崩溃日志'

    def test_source_run_does_not_install(self, monkeypatch):
        """源码运行（有控制台）**不装** —— 保持既有行为，不产生多余文件。"""
        import storygraph.main as m

        monkeypatch.delattr(sys, 'frozen', raising=False)
        assert m._install_crash_log() is None


class TestConsoleEncoding:
    """
    打包后**连 `--help` 都打不开**的那个缺陷的守卫。

    实测（2026-10-05）：`StoryGraph-CLI.exe --help` 在简体中文 Windows
    （控制台代码页 936）上抛

        UnicodeEncodeError: 'gbk' codec can't encode character '\\u2194'

    直接 `[PYI-…:ERROR] Failed to execute script` —— 因为 `--help` 文案里有
    「Excel ↔ .rpy 双向联通」。它比"显示难看"严重得多：CI 里
    `StoryGraph-CLI.exe validate …` 会崩，而**退出码是 1**，
    看起来像"校验不通过"，排查方向完全错。
    """

    def test_configure_switches_to_utf8(self):
        import io

        from storygraph.core import consoleio

        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='gbk')
        try:
            assert consoleio._try_reconfigure(stream, 'utf-8',
                                              'backslashreplace') is True
            assert stream.encoding.lower().replace('-', '') == 'utf8'
            # 关键字符真的写得出去（这才是判据，不是"编码名对不对"）
            stream.write('↔ ✓ →')
            stream.flush()
            assert raw.getvalue().decode('utf-8') == '↔ ✓ →'
        finally:
            stream.close()

    def test_unencodable_stream_is_left_usable(self):
        """
        UTF-8 也换不上时不许把信息丢掉，更不许抛。

        `backslashreplace` → 打不出的字符写成 `\\u2194`（还认得出原来是什么）；
        换 `replace` 就只剩 `?` 了。
        """
        import io

        from storygraph.core import consoleio

        raw = io.BytesIO()
        # cp1252 编不出 ✓（U+2713）
        stream = io.TextIOWrapper(raw, encoding='cp1252', errors='strict')
        try:
            assert consoleio._try_reconfigure(
                stream, 'utf-8', 'backslashreplace') is True
            # 换回一个编不出的编码，走"兜底"那条路
            stream.reconfigure(encoding='cp1252', errors='strict')
            assert consoleio._fallback(stream) is True
            stream.write('✓')
            stream.flush()
            assert b'\\u2713' in raw.getvalue(), raw.getvalue()
        finally:
            stream.close()

    def test_configure_tolerates_streams_without_reconfigure(self, monkeypatch):
        """
        `sys.stdout` 被换成没有 `reconfigure` 的对象（pytest 捕获、
        自定义包装）时：**什么都不做，绝不抛**。
        """
        import io

        from storygraph.core import consoleio

        monkeypatch.setattr(sys, 'stdout', io.StringIO(), raising=False)
        monkeypatch.setattr(sys, 'stderr', io.StringIO(), raising=False)
        assert consoleio.configure() is False       # 没换成功，但没崩
        # 还能正常写（说明我们没把它弄坏）
        sys.stdout.write('ok')

    def test_configure_never_raises_on_broken_stream(self, monkeypatch):
        """`reconfigure` 自己抛也不许逃出去。"""
        from storygraph.core import consoleio

        class Boom:
            def reconfigure(self, **kw):
                raise RuntimeError('被重定向到奇怪的东西了')

        monkeypatch.setattr(sys, 'stdout', Boom(), raising=False)
        monkeypatch.setattr(sys, 'stderr', Boom(), raising=False)
        assert consoleio.configure() is False

    def test_the_help_text_really_has_the_bad_character(self):
        """
        反向确认：**这个缺陷的前提还在**（`--help` 里确实有非 GBK 字符）。

        为什么值得钉住：哪天有人把 `↔` 改成 `->`，这条用例会提醒他
        "那你可能可以把 consoleio 一起去掉" —— 而不是让一段
        "看起来没用"的防御代码无声地留着（或者反过来被误删）。
        """
        from storygraph.cli import build_parser

        text = build_parser().format_help()
        # 模拟 GBK 控制台：有一个字符编不出来就说明这层防御仍然必要
        bad = [ch for ch in text if not _encodable(ch, 'gbk')]
        assert bad, (
            '`--help` 文案已经全是 GBK 能编的字符了 —— 如果确实如此，'
            'core/consoleio.py 的这层防御可以评估是否还需要')
