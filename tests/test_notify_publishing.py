"""
P2-9：**降级/受阻必须"说一声"** —— 通知端口要真的被用起来
==========================================================

清单那条："事件总线半接通（3 处 subscribe / 1 处 publish）。有人听，
几乎没人说"。

复验结论（2026-10-05）：**订阅端是齐的**（Qt 状态栏 + Toast 订阅了通知
端口），缺的是**发布端**。最典型的一处：

    构建/导出**已经算出了**降级结论 ——
      · `# TODO: 缺少资源 …` 有几条（`build()` 里就叫 `todo_count`）；
      · 生成门禁拦下了、产物**没写出去**（`artifact.written is False`）；
      · `generation_guard` 给的 warn 级 issue（如 `UNRESOLVED_TODO`）；
    但它们只进了"构建报告 / CI 退出码"，**一个通知都不发**。

无头环境（CLI / CI / 单测）默认没有订阅者，所以"没人听"时发通知是**零副作用**的
（见 `core.notify`：订阅者列表默认空）。也就是说这些通知只会在真正有人看
的界面里出现 —— 正是我们要的。
"""

from __future__ import annotations

import pytest

from storygraph.core import notify


@pytest.fixture
def captured():
    """订阅通知端口，收集 `(level, message, title)`。用完自动退订。"""
    got = []

    def listener(level, message, title):
        got.append((level, message, title))

    unsubscribe = notify.subscribe(listener)
    try:
        yield got
    finally:
        unsubscribe()


def _make_project(tmp_path):
    """一个最小但**真的能跑通构建**的项目目录。"""
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    (game / 'script.rpy').write_text(
        'label start:\n    "开场"\n    return\n', encoding='utf-8')
    return tmp_path


class TestBuildPublishesDegradation:
    """构建产出了降级内容时，必须发一条 warn。"""

    @staticmethod
    def _beats_with_missing_asset():
        """一拍引用"表里没有"的立绘 —— 有注册表时会被写成 `# TODO`。"""
        from storygraph.story.beat_model import Beat
        return [Beat(seq=1, label='start', kind='narration', text='一句'),
                Beat(seq=2, label='start', kind='show',
                     show='missing_sprite')]

    def test_render_marks_todo_when_registry_cannot_resolve(self):
        """
        先确认前提：**注册表里有这张表、但查不到这一条**时，产物里真的会
        出现 `# TODO: 缺少资源 …`。

        用**说话人**造：`Audit.image` 对拿不准的图片名故意放行（"能跑的
        脚本不许因为查不到就被改写"），所以图片不是合适的样本；
        角色查不到才会写成 `# TODO: 缺少资源 角色 '…'`。
        """
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.beat_model import Beat
        from storygraph.story.beat_render import render_beats_to_rpy

        reg = AssetRegistry()
        reg.characters = {'e': {'ID': 'e', 'Var': 'e', 'Name': 'Eileen'}}
        beats = [Beat(seq=1, label='start', kind='say', speaker='nobody',
                      text='一句')]
        text = render_beats_to_rpy(beats, label_name='start', registry=reg)
        assert '# TODO' in text, text

    def test_build_publishes_warn_for_todo(self, tmp_path, captured):
        """产物里有 `# TODO: 缺少资源 …` 时，构建必须发一条 warn。"""
        from storygraph.pipeline.build import _publish_build_degradation
        from storygraph.story.generation_guard import GeneratedArtifact

        target = tmp_path / 'story.generated.rpy'
        artifact = GeneratedArtifact(target, None, True, [])
        _publish_build_degradation(artifact, 3, target)
        warns = [m for lv, m, _t in captured if lv == notify.WARN]
        assert warns, f'没有发 warn：{captured}'
        assert any(('3' in m and '缺少资源' in m) or 'TODO' in m
                   for m in warns), warns

    def test_blocked_build_does_not_double_publish(self, tmp_path, captured):
        """
        门禁拦下时**不重复发**（`generation_guard` 已经带具体 issue 发过
        一条 error 了）。否则用户会连着看到两条说同一件事的通知。
        """
        from storygraph.pipeline.build import _publish_build_degradation
        from storygraph.story.generation_guard import GeneratedArtifact

        target = tmp_path / 'story.generated.rpy'
        artifact = GeneratedArtifact(target, None, False, [])
        _publish_build_degradation(artifact, 3, target)
        assert captured == [], captured

    def test_clean_build_publishes_nothing(self, tmp_path, captured):
        """没有 TODO、没有被拦下 → **什么也不发**（通知一多就没人看）。"""
        from storygraph.pipeline.build import _publish_build_degradation
        from storygraph.story.generation_guard import GeneratedArtifact

        target = tmp_path / 'story.generated.rpy'
        artifact = GeneratedArtifact(target, None, True, [])
        _publish_build_degradation(artifact, 0, target)
        assert captured == [], captured


class TestGuardPublishesBlocking:
    """生成门禁拦下产物时，必须发一条（error 或 warn）。"""

    def test_blocked_write_publishes(self, tmp_path, captured):
        from storygraph.story.generation_guard import write_generation

        # 中文图像名 = block 级（Ren'Py 语法上不存在这个图像）
        text = 'label a:\n    show 凌跃捷 开心\n'
        art = write_generation(text, tmp_path / 'out.rpy',
                               project_root=tmp_path)
        assert not art.written, art
        assert captured, '门禁拦下了产物却一个通知都没发'
        assert any(lv in (notify.WARN, notify.ERROR) for lv, _m, _t in captured)


class TestNotifyStaysSilentWhenClean:
    """没问题的构建**不许**打扰用户（否则通知很快就没人看）。"""

    def test_clean_build_does_not_warn_about_resources(self, tmp_path,
                                                       captured):
        from storygraph.story.generation_guard import write_generation

        text = 'label a:\n    "一句干净的台词"\n    return\n'
        art = write_generation(text, tmp_path / 'out.rpy',
                               project_root=tmp_path)
        assert art.written, art
        assert not [m for lv, m, _t in captured if lv == notify.ERROR], captured
