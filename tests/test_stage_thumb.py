"""
tests/test_stage_thumb.py
=========================
卡片上的**这一拍的舞台图**（用户 2026-10-27）

> 突然想起来还有个功能没做：那个每一拍的舞台信息那里……可以除文字描述之外，
> 给一张这一拍的图片吗？就放在文字描述之前的上面（与舞台预览一致的图片，
> 但只有一拍，每当这一排有修改时，重新刷新这一拍的图）

判据按"用户会看到什么"写，不按实现写：

  · 图上**真的是这一拍**：背景色 / 立绘 / 台词框都能在像素上认出来；
  · **改动就重画**：换了背景、保存了这一拍 → 图跟着变（指纹变了才重画，
    同内容连点保存不许反复渲 —— 真机一次 100ms~1.3s）；
  · 换拍时**立刻清掉上一张**：卡片写着第 N 拍却画着第 N-1 拍，比空着更糟；
  · 渲染**不占 GUI 线程**：`show_beat` 立刻返回，图在后台出来；
  · 没有画面时**说清原因**（空会话 / 一拍都没有 / 没装 Pillow）；
  · 卡片接线：图在「当前舞台」标题**下面**、摘要**左边**（288×162 的小图，
    用户 2026-10-27 两次点名："缩小！再缩小，直到能看到全貌"），
    且舞台块收起时它**还在**（真机一章 285 拍里 210 拍没有舞台改动，
    收起时正好是最需要看图的时候）。
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip('PySide6')

from PIL import Image                                       # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer               # noqa: E402

RED = (200, 30, 40)
BLUE = (20, 60, 220)
#: 第二张背景（换景测试用）：绿
GREEN = (20, 180, 60)


def _spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def _wait(cond, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        _spin(20)
    return bool(cond())


def _rgb(pix, x: int, y: int):
    """`QPixmap` 上某点（左上角原点）的 RGB —— 判"画的是不是这一拍"。"""
    c = pix.toImage().pixelColor(int(x), int(y))
    return (c.red(), c.green(), c.blue())


def _near(got, want, tol: int = 12) -> bool:
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(got, want))


# ==================================================================
#  一个"颜色可断言"的小工程
# ==================================================================

SCRIPT = '''define e = Character("爱莲", color="#c8ffc8", image="ling")

label ch:
    scene bg red
    show ling happy at leftish
    e "第一句"
    e "第二句"
    scene bg green
    e "第三句"
'''

#: 工程里的拍号（解析器会把 `show ling happy` 并进第一句台词，所以是 3 拍：
#: 0 = 红背景 + 立绘 + 第一句，1 = 继承（什么都没写），2 = 换绿背景 + 第三句）
BEAT_RED, BEAT_INHERIT, BEAT_GREEN = 0, 1, 2


@pytest.fixture
def color_project(tmp_path):
    """
    红背景 + 蓝立绘 + 三句台词（第 4 拍换绿背景）。

    为什么不用 `ui_project`：那个工程里的图是 1×1 透明 PNG，
    **渲染出来是黑的**——"图对不对"就没法用像素判了。
    """
    root = tmp_path / 'proj'
    game = root / 'game'
    (game / 'script').mkdir(parents=True)
    (game / 'images' / 'bg').mkdir(parents=True)
    (game / 'images' / 'ling').mkdir(parents=True)
    # ⚠ 背景必须**真的一屏那么大**（1920×1080）：渲染器按原图像素贴图，
    #   32×18 的图只会占住左上角一小块，剩下全黑（测试就会误判"背景没画"）。
    Image.new('RGB', (1920, 1080), RED).save(game / 'images' / 'bg' / 'red.png')
    Image.new('RGB', (1920, 1080), GREEN).save(
        game / 'images' / 'bg' / 'green.png')
    Image.new('RGB', (600, 1000), BLUE).save(
        game / 'images' / 'ling' / 'happy.png')
    (game / 'script' / 'ch.rpy').write_text(SCRIPT, encoding='utf-8')
    return root


@pytest.fixture
def session(color_project):
    from storygraph.app.editor import EditorSession

    s = EditorSession(project_root=color_project)
    s.load(color_project / 'game' / 'script' / 'ch.rpy')
    return s


@pytest.fixture
def thumb(qapp):
    from storygraph.ui_qt.stage_thumb import BeatStageThumb

    w = BeatStageThumb()
    w.resize(480, 270)                 # 与真实卡片列宽同量级
    w.show()
    qapp.processEvents()
    try:
        yield w
    finally:
        w.shutdown()
        w.close()


def _set(session, idx: int, **fields):
    """
    改一拍（走**和卡片同一条路**：`session.apply`）。

    `stage_loaded` 必须给"载入时那一格"：给了空串，`apply` 会以为用户手改了
    `Stage` 那一格，把 `Scene` 改回去（见 `session_table._apply_stage`）——
    这条坑是这套用例当场踩到的。
    """
    values = session.fields(idx)
    values.update(fields)
    session.apply(idx, values, [], advanced_open=False,
                  stage_loaded=values.get('Stage', ''))
    return values


@pytest.fixture
def card_window(qapp, color_project):
    """
    卡片页挂在一个**颜色可断言**的工程上（`ui_window` 的图是 1×1 透明的，
    "保存后画面变了"根本判不出来）。
    """
    from storygraph.ui_qt.main_window import MainWindowQt

    class _Engine:
        project_root = color_project

        def get_registry(self):
            return None

    script = color_project / 'game' / 'script' / 'ch.rpy'
    win = MainWindowQt(_Engine(), None, project_root=color_project)
    win.load_script(script, goto_card=True)
    qapp.processEvents()
    try:
        yield win
    finally:
        win.close()


# ==================================================================
#  一、图上是**这一拍**（像素判据）
# ==================================================================

class TestThePictureIsThisBeat:
    def test_draws_the_background_and_the_dialogue_box(self, thumb, session):
        thumb.show_beat(BEAT_RED, session)
        pix = thumb.render_now()
        assert pix is not None and not pix.isNull(), '没出图'
        assert not thumb.is_pending()
        assert thumb.note() == ''
        # 右上角：只有背景（立绘在左边，台词框在底部）
        assert _near(_rgb(pix, pix.width() - 20, 16), RED), \
            f'背景不是这一拍的红：{_rgb(pix, pix.width() - 20, 16)}'
        # 底部：台词框把背景盖住了（说明真的画了 say 窗口，不是只铺一张底图）
        bottom = _rgb(pix, pix.width() // 2, pix.height() - 4)
        assert not _near(bottom, RED, tol=6), f'底部没画台词框：{bottom}'

    def test_size_is_the_widget_size(self, thumb, session):
        """渲成控件那么大（不是每次都渲 1920×1080 再缩）。"""
        thumb.show_beat(BEAT_RED, session)
        pix = thumb.render_now()
        assert (pix.width(), pix.height()) == (480, 270)

    def test_inherited_beat_still_gets_a_picture(self, thumb, session):
        """
        第 2 拍**一个字都没写**舞台，但图上仍然是红背景（继承第 1 拍）。

        这就是这张图存在的理由：光看这一拍的三列看不出舞台上是什么。
        """
        thumb.show_beat(BEAT_INHERIT, session)
        pix = thumb.render_now()
        assert _near(_rgb(pix, pix.width() - 20, 16), RED)


# ==================================================================
#  二、缓存与"什么时候重画"
# ==================================================================

class TestRefresh:
    def test_same_beat_again_does_not_render_twice(self, thumb, session):
        """
        同一拍、同一内容 → **不重画**（真机一次 100ms~1.3s，翻回来要立刻有图）。
        """
        thumb.show_beat(BEAT_RED, session)
        first = thumb.render_now()
        calls = thumb.render_count
        thumb.show_beat(BEAT_RED, session)
        assert thumb.pixmap() is first, '同内容又渲了一遍'
        assert thumb.render_count == calls

    def test_flipping_away_and_back_hits_the_cache(self, thumb, session):
        thumb.show_beat(BEAT_RED, session)
        thumb.render_now()
        calls = thumb.render_count
        thumb.show_beat(BEAT_GREEN, session)
        thumb.render_now()
        thumb.show_beat(BEAT_RED, session)          # 翻回来
        assert thumb.pixmap() is not None, '翻回来没图了（应命中缓存）'
        assert thumb.render_count == calls + 1, '翻回来又重画了一次'

    def test_a_changed_beat_is_repainted(self, thumb, session):
        """
        这一拍改了（换背景）→ 指纹变了 → 重画，而且画的是**新的**那一拍。
        """
        thumb.show_beat(BEAT_RED, session)
        before = thumb.render_now()
        assert _near(_rgb(before, before.width() - 20, 16), RED)

        _set(session, BEAT_RED, Scene='bg green')
        thumb.show_beat(BEAT_RED, session)
        after = thumb.render_now()
        assert after is not None
        assert _near(_rgb(after, after.width() - 20, 16), GREEN), \
            '改了背景，卡片上的图没跟着变'

    def test_a_pure_text_change_also_repaints(self, thumb, session):
        """只改台词也要重画：台词框里的字是画面的一部分。"""
        thumb.show_beat(BEAT_RED, session)
        before = thumb.render_now()
        _set(session, BEAT_RED, Text='完全不一样的一句很长的台词')
        thumb.show_beat(BEAT_RED, session)
        after = thumb.render_now()
        assert after is not None and after.toImage() != before.toImage()

    def test_force_repaints_even_without_changes(self, thumb, session):
        thumb.show_beat(BEAT_RED, session)
        thumb.render_now()
        calls = thumb.render_count
        thumb.show_beat(BEAT_RED, session, force=True)
        thumb.render_now()
        assert thumb.render_count == calls + 1


# ==================================================================
#  三、不卡界面 / 不骗人
# ==================================================================

class TestNoBlockingNoLies:
    def test_switching_beats_clears_the_old_picture_at_once(self, thumb, session):
        """
        换拍时**立刻**清掉上一张（不是"上一张顶着 + 小角标"）：
        卡片是"第 N 拍的快照"，画着第 N-1 拍的舞台就是错的信息。
        """
        thumb.show_beat(BEAT_RED, session)
        thumb.render_now()
        assert thumb.pixmap() is not None
        thumb.show_beat(BEAT_GREEN, session)
        assert thumb.pixmap() is None, '换拍后还挂着上一拍的图'
        assert thumb.is_pending()
        assert '渲染' in thumb.note()

    def test_show_beat_returns_immediately(self, thumb, session, monkeypatch):
        """
        `show_beat` 不许在 GUI 线程里合成（真机第一次 1.3s）。

        把渲染器换成"睡 0.3 秒"，`show_beat` 仍然要立刻返回。
        """
        from storygraph.app import stage_render as R

        real = R.render_frame

        def _slow(*args, **kwargs):
            time.sleep(0.3)
            return real(*args, **kwargs)

        monkeypatch.setattr(R, 'render_frame', _slow)
        t0 = time.perf_counter()
        thumb.show_beat(BEAT_RED, session)
        dt = (time.perf_counter() - t0) * 1000
        assert dt < 150, f'show_beat 卡了 {dt:.0f} ms'
        assert thumb.is_pending()

    def test_the_picture_arrives_in_the_background(self, thumb, session):
        """后台那条路也能出图（不是只有 `render_now` 能）。"""
        thumb.show_beat(BEAT_RED, session)
        assert _wait(lambda: thumb.pixmap() is not None, timeout=15), \
            f'后台渲染没出图（note={thumb.note()!r}）'
        assert _near(_rgb(thumb.pixmap(), thumb.width() - 20, 16), RED)

    def test_stale_result_never_lands_on_the_new_beat(self, thumb, session):
        """
        连翻两拍、第一拍的渲染**迟到**回来 → 只进缓存，不许画到第二拍上。
        """
        from storygraph.app import stage_render as R

        real = R.render_frame
        calls = {'n': 0}

        def _first_slow(frame, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                time.sleep(0.4)              # 第一拍的图慢慢回来
            return real(frame, **kwargs)

        R.render_frame, monkeypatch_saved = _first_slow, R.render_frame
        try:
            thumb.show_beat(BEAT_RED, session)
            _wait(lambda: thumb.render_count >= 1, timeout=3)
            _spin(150)                        # 让第一张真的开始渲染
            thumb.show_beat(BEAT_GREEN, session)
            assert _wait(lambda: thumb.pixmap() is not None, timeout=15)
            pix = thumb.pixmap()
            assert _near(_rgb(pix, pix.width() - 20, 16), GREEN), \
                '第一拍的迟到结果盖到第二拍上了'
        finally:
            R.render_frame = monkeypatch_saved


# ==================================================================
#  四、没有画面时**说清原因**
# ==================================================================

class TestSayingWhy:
    def test_before_any_chapter(self, thumb):
        thumb.clear()
        assert thumb.pixmap() is None
        assert thumb.note(), '没图也不说一句，用户只能猜'

    def test_no_beats(self, thumb, qapp):
        """空会话（打开了一个空文件 / 还没载入章节）。"""
        from storygraph.app.editor import EditorSession

        thumb.show_beat(0, EditorSession())
        assert thumb.pixmap() is None
        assert '载入' in thumb.note()

    def test_index_out_of_range_says_so(self, thumb, session):
        thumb.show_beat(999, session)
        assert thumb.pixmap() is None
        assert '不存在' in thumb.note() or '越界' in thumb.note()

    def test_clearing_removes_the_picture(self, thumb, session):
        thumb.show_beat(BEAT_RED, session)
        thumb.render_now()
        assert thumb.pixmap() is not None
        thumb.clear()
        assert thumb.pixmap() is None

    def test_missing_assets_are_named_not_hidden(self, thumb, session):
        """
        脚本里写的图磁盘上找不到 → 图上要有「⚠ 缺 N 个素材」，tooltip 列名单。

        缺图是**要人去修**的事，不是"再等等"；静默画一张空背景等于骗人。
        """
        _set(session, BEAT_RED, Scene='bg nosuchbg')
        thumb.show_beat(BEAT_RED, session)
        assert thumb.render_now() is not None
        assert thumb.rendered_missing() == ['bg nosuchbg']
        tip = thumb.toolTip()
        assert 'nosuchbg' in tip and '缺' in tip, tip


# ==================================================================
#  五、卡片接线
# ==================================================================

def _find_beat(window, needle: str) -> int:
    for i, b in enumerate(window.beats):
        if needle in str(getattr(b, 'text', '') or ''):
            return i
    raise AssertionError(f'这一章里没有台词含 {needle!r} 的拍')


class TestCardWiring:
    def test_the_card_shows_the_loaded_beat(self, card_window):
        thumb = card_window._panel.stage_thumb()
        assert thumb.objectName() == 'stageThumb'
        assert thumb.beat_index() == card_window._loaded_idx

    def test_it_sits_under_the_current_stage_heading(self, card_window):
        """
        "当前舞台下面那一栏"：图在「当前舞台（这一拍生效）」标题**下面**，
        与文字摘要**并排**（图在左，摘要占右边剩下的宽度）。

        判据用**布局顺序**（标题 → 那一行 → [图, 摘要]），不靠像素 ——
        无头窗口里控件的几何尺寸不可靠，而"谁在谁旁边"就是布局顺序。
        """
        from PySide6.QtWidgets import QLabel

        panel = card_window._panel
        thumb = panel.stage_thumb()
        # ① 不再挂在卡片顶部（第一版的位置，用户否掉了）
        assert panel.layout().indexOf(thumb) == -1, \
            '图又回到卡片顶部了（应在「当前舞台」那一块里面）'
        assert thumb.parent() is panel._stage_now, '图不在「当前舞台」那一块里'
        # ② 那一块的第一行是标题，第二行才是 图 + 摘要
        lay = panel._stage_now.layout()
        head = lay.itemAt(0).widget()
        assert isinstance(head, QLabel) and '当前舞台' in head.text(), head
        row = lay.itemAt(1).layout()
        assert row is not None, '图与摘要不是并排的一行'
        assert row.itemAt(0).widget() is thumb, '图不在这一行的最左边'
        assert row.itemAt(1).widget() is panel._stage_label, \
            '摘要不在图的右边'

    def test_the_picture_is_a_small_whole_frame(self, card_window):
        """
        "缩小！再缩小，直到能看到全貌"：固定 16:9 的小图（288×162），
        **不是**按列宽撑满的大图（那样一屏看不全，也就没法照着调）。
        """
        from PySide6.QtCore import QSize

        from storygraph.ui_qt.stage_thumb import COMPACT_H, COMPACT_W

        thumb = card_window._panel.stage_thumb()
        assert (COMPACT_W, COMPACT_H) == (288, 162)
        assert thumb.sizeHint() == QSize(COMPACT_W, COMPACT_H), thumb.sizeHint()
        assert thumb.maximumWidth() <= 320, '小图不该跟着列宽长胖'
        assert thumb.maximumHeight() <= 180, '小图不该比一行摘要高太多'

    def test_it_stays_when_the_stage_block_is_collapsed(self, card_window):
        """
        没有舞台改动的那一拍（舞台块收起）**也要有图**。

        真机一章 285 拍里 210 拍是这种情况，而"背景和立绘都是继承来的"
        恰恰是最需要看一眼图的时候。
        """
        win = card_window
        win._show_index(BEAT_INHERIT)
        panel = win._panel
        assert panel._stage_box.isHidden(), '这一拍应该没有舞台块'
        thumb = panel.stage_thumb()
        assert not thumb.isHidden(), '舞台块收起时连图一起收掉了'
        assert thumb.beat_index() == BEAT_INHERIT

    def test_flipping_the_beat_flips_the_picture(self, card_window):
        thumb = card_window._panel.stage_thumb()
        card_window._show_index(BEAT_RED)
        assert thumb.beat_index() == BEAT_RED
        card_window._show_index(BEAT_GREEN)
        assert thumb.beat_index() == BEAT_GREEN

    def test_saving_the_beat_repaints_it(self, card_window):
        """
        "每当这一排有修改时，重新刷新这一拍的图"：
        改舞台 → 保存 → 图进入重画状态，重画后画面真的变了。
        """
        from PySide6.QtWidgets import QComboBox

        win = card_window
        thumb = win._panel.stage_thumb()
        before = thumb.render_now()
        assert before is not None
        scene = win._panel._widgets['Scene']
        assert isinstance(scene, QComboBox)
        assert scene.currentText().strip() == 'bg red', scene.currentText()
        scene.setCurrentText('bg green')
        win.save_current_beat()
        assert thumb.is_pending() or thumb.pixmap() is None, \
            '保存后没有请求重画'
        after = thumb.render_now()
        assert after is not None
        assert _near(_rgb(after, after.width() - 20, 16), GREEN), \
            '保存后重画的还是旧画面'

    def test_an_empty_session_clears_the_card_picture(self, card_window):
        thumb = card_window._panel.stage_thumb()
        thumb.show_beat(BEAT_RED, card_window._session)
        thumb.render_now()
        assert thumb.pixmap() is not None
        card_window._panel.clear_stage_frame()
        assert thumb.pixmap() is None
