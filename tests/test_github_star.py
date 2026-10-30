"""
tests/test_github_star.py
=========================
卡片编辑页右上角的 **⭐ GitHub 快速 star**（用户 2026-10-27）

> 再在（卡片编辑器）的稍稍右上方空白处去做一个 github 的快速 star 呢？
> （点击这个如果能连得上网的话最好就能给我个标个星，如果不能联网也没关系）
> 注意：我希望它是一个独立于整体界面的代码块，到时候方便取下来，
> 不要耽误别人到时候对这个界面的改造

所以这里钉三件事：

  1. **它真的是独立件**：`ui_qt/github_star.py` 不 import 任何窗口/会话代码，
     整个 UI 包只有 `main_window.py` 一处挂载；
  2. **点一下有用**：有 token → 直接 star；没 token → **立刻**打开仓库页
     （开页面是本机动作，不许被网络自检挡着）；
  3. **探测只做提示、不许挡路**（2026-10-30 真机反馈）：拿不拿得到探测结果，
     页面都照开；"任何 HTTP 响应（含 404/403/429/5xx）都算网络通"，超时 6~8s
     且失败重试一次 —— v1 的"探不通就不开死页面"恰恰是"明明能打开却什么都不做"。
     网络全在后台线程，GUI 线程一秒都不等。
"""

from __future__ import annotations

import time
import urllib.error

import pytest

pytest.importorskip('PySide6')

from PySide6.QtCore import QEventLoop, QTimer               # noqa: E402

PKG = __import__('pathlib').Path(__file__).resolve().parents[1] / 'storygraph'


def _spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def _wait(cond, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        _spin(20)
    return bool(cond())


@pytest.fixture
def star(qapp):
    """一个干净的徽标（不读环境变量里的真 token：显式注入空串）。"""
    from storygraph.ui_qt.github_star import GitHubStarBadge

    badge = GitHubStarBadge(repo='someone/some-repo', token='')
    try:
        yield badge
    finally:
        badge.shutdown()
        badge.close()


# ==================================================================
#  一、独立件（这是用户点名的约束）
# ==================================================================

class TestItStaysDetachable:
    def test_the_module_does_not_import_the_ui_tree(self):
        """
        它只许依赖 Qt + 标准库 + 一个颜色常量模块 —— **不许** import 任何
        窗口 / 会话 / 卡片代码，否则"取下来"就会扯到别处。
        """
        text = (PKG / 'ui_qt' / 'github_star.py').read_text(encoding='utf-8')
        bad = [name for name in ('main_window', 'window_card', 'window_nav',
                                 'storygraph.app', 'storygraph.story',
                                 'storygraph.pipeline', 'editor')
               if f'import {name}' in text or f'from {name}' in text]
        assert bad == [], f'这个独立件开始依赖界面/业务代码了：{bad}'

    def test_only_the_mode_bar_mounts_it(self):
        """
        整个界面里**只有一处**挂载（`window_nav._build_mode_bar` 的三行 try 块）。

        这条判据的意义：以后谁重构界面，删掉那一处 + 那个文件就干净了；
        一旦有人在别的窗口里也 import 它，这条会红。
        """
        hits = []
        for path in sorted((PKG / 'ui_qt').glob('*.py')):
            if path.name == 'github_star.py':
                continue
            text = path.read_text(encoding='utf-8')
            if 'github_star' in text:
                hits.append(path.name)
        assert hits == ['window_nav.py'], f'挂载点不止一处：{hits}'


# ==================================================================
#  二、点一下：三种结局
# ==================================================================

class TestOneClick:
    def test_with_a_token_it_stars_directly(self, star, monkeypatch):
        from storygraph.ui_qt import github_star as G

        calls = []

        def _star(repo, token, timeout=G.TIMEOUT):
            calls.append((repo, token))
            return (True, '已 star（204）')

        opened = []
        monkeypatch.setattr(G, 'star_repo', _star)
        monkeypatch.setattr(G, 'open_repo_page',
                            lambda repo: (opened.append(repo), True)[1])
        star._token = 'tok123'
        star.click()
        assert _wait(lambda: '已 star' in star.text()), star.text()
        assert calls == [('someone/some-repo', 'tok123')]
        assert opened == [], '已经 star 成功了不该再开网页'

    def test_without_a_token_it_opens_the_repo_page(self, star, monkeypatch):
        from storygraph.ui_qt import github_star as G

        opened = []
        monkeypatch.setattr(G, 'probe_repo', lambda repo, timeout=0: True)
        monkeypatch.setattr(G, 'open_repo_page',
                            lambda repo: (opened.append(repo), True)[1])
        star._token = ''
        star.click()
        assert opened == ['someone/some-repo'], '点一下就该把页面打开'
        assert _wait(lambda: '点一下 Star' in star.text()), star.text()

    def test_offline_probe_still_opens_the_page_and_says_so(self, star,
                                                           monkeypatch):
        """
        **v1 的旧断言已推翻**：探测没通过**照样**开页面。

        浏览器走系统代理/PAC，探测失败（2.5s 超时 / 把 404 当断网）时浏览器
        往往照样打得开 —— 旧行为"探不通就不开"等于"明明能打开却什么都不做"，
        正是用户报"连不上，但其实我能打开"的直接原因。现在只补一句提示。
        """
        from storygraph.ui_qt import github_star as G

        opened = []
        monkeypatch.setattr(G, 'probe_repo', lambda repo, timeout=0: False)
        monkeypatch.setattr(G, 'open_repo_page',
                            lambda repo: (opened.append(repo), True)[1])
        star._token = ''
        star.click()
        assert opened == ['someone/some-repo'], '探测失败也必须把页面打开'
        assert _wait(lambda: '网络自检没通过' in star.text()), star.text()
        assert '代理' in star.text() or '网络' in star.text(), star.text()

    # --------------------------------------------------------------
    #  2026-10-30 真机反馈：探测判据本身也要修
    # --------------------------------------------------------------

    @pytest.mark.parametrize('code', [404, 403, 429, 500])
    def test_any_http_response_counts_as_online(self, monkeypatch, code):
        """
        **404/403/429/5xx 也算网络通**：那是 GitHub 自己回的话，说明
        DNS/TCP/TLS/代理全活着（私有仓库、改名后 404、限流 429，浏览器都能开）。
        v1 把它们全当"连不上"，是误报的另一半原因。
        """
        from storygraph.ui_qt import github_star as G

        def _urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, code, 'nope', None, None)

        monkeypatch.setattr(G.urllib.request, 'urlopen', _urlopen)
        assert G.probe_repo('someone/some-repo') is True

    def test_a_404_page_still_counts_as_online(
            self, star, monkeypatch):
        """端到端：探测拿到 404 → 页面已开 → 提示"点一下 Star 就行"。"""
        from storygraph.ui_qt import github_star as G

        opened = []

        def _urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', None, None)

        monkeypatch.setattr(G.urllib.request, 'urlopen', _urlopen)
        monkeypatch.setattr(G, 'open_repo_page',
                            lambda repo: (opened.append(repo), True)[1])
        star._token = ''
        star.click()
        assert opened == ['someone/some-repo']
        assert _wait(lambda: '点一下 Star' in star.text()), star.text()

    def test_probe_timeout_is_patient_and_retries_once(self, monkeypatch):
        """超时不再是 v1 的 2.5s（同网络 5 次里 1 次 >6s），且失败重试一次。"""
        from storygraph.ui_qt import github_star as G

        assert 6.0 <= G.PROBE_TIMEOUT <= 8.0, G.PROBE_TIMEOUT
        seen = []

        def _urlopen(req, timeout=None):
            seen.append(timeout)
            raise urllib.error.URLError('timed out')

        monkeypatch.setattr(G.urllib.request, 'urlopen', _urlopen)
        assert G.probe_repo('someone/some-repo') is False
        assert len(seen) == 2, f'失败要重试一次，实际试了 {len(seen)} 次'
        assert seen == [G.PROBE_TIMEOUT, G.PROBE_TIMEOUT]

    def test_an_http_response_is_not_retried(self, monkeypatch):
        """拿到 HTTP 响应就已经是结论了，不需要再重试（也别多打一次网络）。"""
        from storygraph.ui_qt import github_star as G

        seen = []

        def _urlopen(req, timeout=None):
            seen.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 404, 'nope', None, None)

        monkeypatch.setattr(G.urllib.request, 'urlopen', _urlopen)
        assert G.probe_repo('someone/some-repo') is True
        assert len(seen) == 1

    def test_probe_timeout_still_opens_the_page(self, star, monkeypatch):
        """真超时（后台抛出 TimeoutError）也**不耽误**页面已经打开。"""
        from storygraph.ui_qt import github_star as G

        opened = []

        def _timeout(repo, timeout=0, *a, **k):
            raise TimeoutError('timed out')

        monkeypatch.setattr(G, 'probe_repo', _timeout)
        monkeypatch.setattr(G, 'open_repo_page',
                            lambda repo: (opened.append(repo), True)[1])
        star._token = ''
        star.click()
        assert opened == ['someone/some-repo'], '超时也必须先把页面打开'
        assert _wait(lambda: '网络自检没通过' in star.text()), star.text()
        assert star.isEnabled()

    def test_click_returns_immediately_without_waiting_for_probe(
            self, star, monkeypatch):
        """
        点一下**不许等探测**：`click()` 立刻返回，且页面在 `click()` 返回时
        就已经打开了（v1 要等 2.5~3s 探测完才决定开不开）。
        """
        from storygraph.ui_qt import github_star as G

        opened = []

        def _slow(repo, timeout=0, *a, **k):
            time.sleep(0.5)
            return True

        monkeypatch.setattr(G, 'probe_repo', _slow)
        monkeypatch.setattr(G, 'open_repo_page',
                            lambda repo: (opened.append(repo), True)[1])
        star._token = ''
        t0 = time.perf_counter()
        star.click()
        dt = (time.perf_counter() - t0) * 1000
        assert opened == ['someone/some-repo'], 'click() 返回时页面就该开了'
        assert dt < 150, f'点一下就卡了 {dt:.0f} ms（在等探测）'
        assert star.isEnabled() is False, '自检期间不许连点'
        assert _wait(lambda: star.isEnabled(), timeout=5)
        assert '点一下 Star' in star.text(), star.text()

    def test_a_bad_token_falls_back_to_the_page(self, star, monkeypatch):
        """token 过期 → 自动 star 失败 → 退回开网页，并且**说清为什么**。"""
        from storygraph.ui_qt import github_star as G

        opened = []
        monkeypatch.setattr(G, 'star_repo',
                            lambda repo, token, timeout=0: (False, 'token 无效或已过期'))
        monkeypatch.setattr(G, 'open_repo_page',
                            lambda repo: (opened.append(repo), True)[1])
        star._token = 'expired'
        star.click()
        assert _wait(lambda: opened == ['someone/some-repo']), star.text()
        assert 'token 无效' in star.text(), star.text()

    def test_no_browser_available_still_tells_the_url(self, star, monkeypatch):
        from storygraph.ui_qt import github_star as G

        monkeypatch.setattr(G, 'probe_repo', lambda repo, timeout=0: True)
        monkeypatch.setattr(G, 'open_repo_page', lambda repo: False)
        star._token = ''
        star.click()
        # 先把"打不开浏览器"说出来（点一下立刻就有），自检完再补上地址
        assert '打不开浏览器' in star.text(), star.text()
        assert _wait(lambda: '地址是' in star.text()), star.text()
        assert 'github.com/someone/some-repo' in star.text()

    def test_click_returns_immediately(self, star, monkeypatch):
        """
        网络在后台：`click()` 必须**立刻**返回（真机上一次网络要 2~3 秒）。
        """
        from storygraph.ui_qt import github_star as G

        def _slow(repo, token, timeout=G.TIMEOUT):
            time.sleep(0.4)
            return (True, 'ok')

        monkeypatch.setattr(G, 'star_repo', _slow)
        star._token = 'tok'
        t0 = time.perf_counter()
        star.click()
        dt = (time.perf_counter() - t0) * 1000
        assert dt < 150, f'点一下就卡了 {dt:.0f} ms'
        assert star.text() != '★ Star on GitHub'
        assert star.isEnabled() is False        # 忙碌期间不许连点
        assert _wait(lambda: star.isEnabled(), timeout=5)


# ==================================================================
#  三、token 从哪来 / HTTP 语义（纯函数，不碰界面）
# ==================================================================

class TestTokenAndHttp:
    def test_constructing_never_touches_the_network(self, qapp, monkeypatch):
        """
        **启动时不联网**：建控件不许发请求（不探测、不拉 star 数）。

        理由：它是界面上的一块装饰，不许在打开工具时偷偷发网络包，更不许
        因为没网而让启动变慢或弹错。
        """
        from storygraph.ui_qt import github_star as G

        def _boom(*_a, **_k):
            raise AssertionError('启动时联网了')

        monkeypatch.setattr(G.urllib.request, 'urlopen', _boom)
        badge = G.GitHubStarBadge(repo='someone/some-repo', token='')
        try:
            assert badge.text().startswith('★')
        finally:
            badge.close()

    def test_env_var_wins(self, monkeypatch):
        from storygraph.ui_qt import github_star as G

        monkeypatch.setenv('STORYGRAPH_GITHUB_TOKEN', 'envtok')
        monkeypatch.delenv('GITHUB_TOKEN', raising=False)
        monkeypatch.delenv('GH_TOKEN', raising=False)
        assert G.read_token() == 'envtok'

    def test_file_fallback(self, tmp_path, monkeypatch):
        """普通人不会设环境变量：`%LOCALAPPDATA%/StoryGraph/github.json` 也认。"""
        from storygraph.ui_qt import github_star as G

        for name in ('STORYGRAPH_GITHUB_TOKEN', 'GITHUB_TOKEN', 'GH_TOKEN'):
            monkeypatch.delenv(name, raising=False)
        (tmp_path / 'StoryGraph').mkdir()
        (tmp_path / 'StoryGraph' / 'github.json').write_text(
            '{"token": "filetok"}', encoding='utf-8')
        monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
        assert G.read_token() == 'filetok'

    def test_no_token_anywhere_is_empty(self, tmp_path, monkeypatch):
        from storygraph.ui_qt import github_star as G

        for name in ('STORYGRAPH_GITHUB_TOKEN', 'GITHUB_TOKEN', 'GH_TOKEN'):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
        assert G.read_token() == ''

    def test_star_repo_success_is_idempotent_204(self, monkeypatch):
        """已经 star 过也是 204 —— 不能被当成失败。"""
        from storygraph.ui_qt import github_star as G

        class _Resp:
            status = 204

            def read(self, *_a):
                return b''

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        seen = {}

        def _urlopen(req, timeout=None):
            seen['method'] = req.get_method()
            seen['url'] = req.full_url
            seen['auth'] = req.get_header('Authorization')
            return _Resp()

        monkeypatch.setattr(G.urllib.request, 'urlopen', _urlopen)
        ok, msg = G.star_repo('someone/some-repo', 'tok')
        assert ok and '204' in msg
        assert seen['method'] == 'PUT'
        assert seen['url'].endswith('/user/starred/someone/some-repo')
        assert seen['auth'] == 'Bearer tok'

    @pytest.mark.parametrize('code,expect', [
        (401, 'token 无效'),
        (403, '限流'),
        (404, '仓库名'),
    ])
    def test_star_repo_maps_http_errors_to_words(self, monkeypatch, code, expect):
        from storygraph.ui_qt import github_star as G

        def _urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, code, 'nope', None, None)

        monkeypatch.setattr(G.urllib.request, 'urlopen', _urlopen)
        ok, msg = G.star_repo('someone/some-repo', 'tok')
        assert ok is False and expect in msg, msg

    def test_star_repo_without_token_never_calls_the_network(self, monkeypatch):
        from storygraph.ui_qt import github_star as G

        def _boom(*_a, **_k):
            raise AssertionError('没有 token 还去联网了')

        monkeypatch.setattr(G.urllib.request, 'urlopen', _boom)
        ok, msg = G.star_repo('someone/some-repo', '')
        assert ok is False and 'token' in msg

    def test_probe_and_star_turn_offline_into_words(self, monkeypatch):
        from storygraph.ui_qt import github_star as G

        def _urlopen(req, timeout=None):
            raise urllib.error.URLError('getaddrinfo failed')

        monkeypatch.setattr(G.urllib.request, 'urlopen', _urlopen)
        assert G.probe_repo('someone/some-repo') is False
        ok, msg = G.star_repo('someone/some-repo', 'tok')
        assert ok is False and '连不上' in msg


# ==================================================================
#  四、顶部模式条接线
# ==================================================================

class TestModeBarWiring:
    def test_the_badge_sits_right_of_the_card_tab(self, ui_window):
        """
        "你应当放第四张图这里，卡片编辑右边"：
        徽标在**顶部模式条**上，紧挨「✎ 卡片编辑」标签右边 —— 不是卡片页右栏。
        """
        badge = getattr(ui_window, '_github_star', None)
        assert badge is not None, '模式条上没有 star 徽标'
        assert badge.objectName() == 'githubStar'
        assert badge.text().startswith('★')

        bar = ui_window._tb_modes
        assert badge.parentWidget() is bar, '徽标不在顶部模式条上'
        acts = bar.actions()

        def _at(widget):
            for i, a in enumerate(acts):
                if a.defaultWidget() is widget:
                    return i
            return -1

        tabs_at = _at(ui_window._mode_tabs)
        badge_at = _at(badge)
        assert tabs_at >= 0 and badge_at >= 0, '徽标或模式标签不在模式条上'
        assert badge_at == tabs_at + 1, '徽标不在「卡片编辑」标签右边'

    def test_it_is_not_in_the_card_column(self, ui_window):
        """
        卡片页右栏"几乎全留给卡片"（用户 2026-10-27）—— 徽标不许再挤在那里。
        """
        badge = ui_window._github_star
        page = ui_window._stack.widget(ui_window.PAGE_CARD)
        assert not page.isAncestorOf(badge), '徽标又跑回卡片页里了'

    def test_removing_it_leaves_nothing_behind(self, ui_window):
        """
        删掉徽标之后，界面照常工作（= 它没有别的地方依赖）。

        判据不是"删除代码"，而是"删掉这个控件之后不残留任何东西"：
        模式条上少一个动作、窗口上那个引用是它本身（删代码时一起删）。
        """
        badge = ui_window._github_star
        bar = ui_window._tb_modes
        act = next(a for a in bar.actions() if a.defaultWidget() is badge)
        bar.removeAction(act)
        badge.deleteLater()
        assert not any(a.defaultWidget() is badge for a in bar.actions())
        # 卡片页其余部分不受影响
        assert ui_window._panel is not None
        ui_window._show_index(0)
        assert ui_window._panel.stage_thumb().beat_index() == 0
