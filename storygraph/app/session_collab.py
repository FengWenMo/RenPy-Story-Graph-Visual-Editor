"""
storygraph.app.session_collab
=============================
`EditorSession` 的**协作那一半**：SQLite ↔ Excel 的三条命令 + 建库登记。

这一块是纯**转发**（每个方法就是 `collab.collab_store` 的薄壳 + 异常兜底），
唯一用到宿主的是 `registry()`（`collab_pull` 要写候选表）。所以它风险最低，
拆出来主要是让 `editor.py` 少一块无关的领域词。

为什么单独一个文件而不是跟导出挤在 `session_export.py`：
两者只是"清单里排在一行"而已（`collab_status` / `collab_pull`… 那 4 个
方法是独立的领域），域不同就别硬塞一个模块。文件各自 100 行左右，
合起来反而难以定位。
"""
from __future__ import annotations

from pathlib import Path


__all__ = ['CollabMixin']


class CollabMixin:
    """
    协作那一半（见模块 docstring）。

    只**要求宿主提供** `registry()`（`collab_pull` 写候选表要用）。
    """

    REQUIRES = ('registry',)

    def collab_status(self, db_path) -> dict:
        """协作总览：谁锁了多少、各状态多少、最近改了什么。"""
        from storygraph.collab.collab_store import status_summary
        try:
            data = status_summary(Path(db_path))
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'summary': {}}
        return {'ok': True, 'error': '', 'summary': data or {}}
    def collab_pull(self, db_path, sheet_path, actor: str = '') -> dict:
        """数据库 → Excel（前端）：写 Beats + 候选表 + 协作信息表。"""
        from storygraph.collab.collab_store import pull
        try:
            res = pull(Path(db_path), Path(sheet_path), actor=actor,
                       registry=self.registry())
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'result': {}}
        return {'ok': True, 'error': '', 'result': res or {}}
    def collab_push(self, db_path, sheet_path, actor: str,
                    force: bool = False, only=None) -> dict:
        """Excel（前端）→ 数据库；冲突/被锁一律不静默覆盖。

        `only`：**部分提交** —— 只写这些 LineId（其余冲突回头再说）。
        """
        from storygraph.collab.collab_store import push
        try:
            res = push(Path(db_path), Path(sheet_path), actor=actor,
                       force=force, only=only)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'result': {}}
        # push 在**权限拒绝**时会带 `ok=False`（正常冲突不带 ok）；
        # 转发层不能把它吞成成功。
        return {'ok': bool((res or {}).get('ok', True)),
                'error': (res or {}).get('error', '') or '',
                'result': res or {}}

    def collab_resolve(self, db_path, sheet_path, actor: str,
                       resolutions) -> dict:
        """把「你和 TA 改了同一句」里用户的选择落地（写库 + 写回本地表）。"""
        from storygraph.collab.collab_store import resolve_conflicts
        try:
            res = resolve_conflicts(Path(db_path), Path(sheet_path),
                                    actor=actor, resolutions=resolutions)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'result': {}}
        return {'ok': True, 'error': '', 'result': res or {}}
    def collab_init(self, db_path, users=(), owner: str = '') -> dict:
        """建库并登记成员（角色：只读 / 编辑 / 所有者）。"""
        from storygraph.collab.collab_store import init_db
        try:
            path = init_db(Path(db_path), users=tuple(users or ()),
                           owner=owner)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'path': str(db_path)}
        return {'ok': True, 'error': '', 'path': str(path)}

    def collab_members(self, db_path) -> dict:
        """成员 + 角色（只读 / 编辑 / 所有者）。"""
        from storygraph.collab.collab_store import members
        try:
            rows = members(Path(db_path))
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'members': []}
        return {'ok': True, 'error': '', 'members': rows}

    def collab_set_role(self, db_path, member: str, role: str,
                        actor: str = '') -> dict:
        """改成员角色（只有所有者能改；新库无所有者时允许第一次认领）。"""
        from storygraph.collab.collab_store import set_role
        try:
            res = set_role(Path(db_path), member, role, actor=actor)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e)}
        return {'ok': bool(res.get('ok')), 'error': res.get('reason', ''),
                'result': res}

    def collab_renew_lock(self, db_path, line_id: str, actor: str,
                          ttl: int = 0) -> dict:
        """续租一行锁（租约到点前续上，别人就还能被挡在外面）。"""
        from storygraph.collab.collab_store import renew_lock
        try:
            res = renew_lock(Path(db_path), line_id, actor, ttl=ttl)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e)}
        return {'ok': bool(res.get('ok')), 'error': res.get('reason', ''),
                'result': res}

    # ------------------------------------------------------------------
    #  锁（2026-10-23 接线：`collab_store.lock/unlock/lock_range/locks`
    #  从前**只有测试在调**，界面上一个入口都没有 —— 于是
    #  `renew_lock` 也成了死路：没人能上锁，自然没人需要续租）
    # ------------------------------------------------------------------
    def collab_lock(self, db_path, line_id: str, actor: str, note: str = '',
                    ttl: int = 0) -> dict:
        """锁住一句（带租约，默认 15 分钟）。别人没到期就提交会被拒。"""
        from storygraph.collab.collab_store import lock
        try:
            res = lock(Path(db_path), line_id, actor, note=note, ttl=ttl)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'result': {}}
        return {'ok': bool(res.get('ok')), 'error': res.get('reason', ''),
                'result': res}

    def collab_unlock(self, db_path, line_id: str, actor: str,
                      force: bool = False) -> dict:
        """解锁一句。`force=True` 只有所有者能做（**绕过别人的锁**要留审计）。"""
        from storygraph.collab.collab_store import unlock
        try:
            res = unlock(Path(db_path), line_id, actor, force=force)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'result': {}}
        return {'ok': bool(res.get('ok')), 'error': res.get('reason', ''),
                'result': res}

    def collab_lock_range(self, db_path, label: str, actor: str,
                          ttl: int = 0) -> dict:
        """整章上锁（"这一章我在改"）。已被别人锁着的行会跳过并报数。"""
        from storygraph.collab.collab_store import lock_range
        try:
            res = lock_range(Path(db_path), label, actor, ttl=ttl)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'result': {}}
        return {'ok': bool(res.get('ok', True)), 'error': res.get('reason', ''),
                'result': res}

    def collab_locks(self, db_path) -> dict:
        """
        当前所有锁（含台词摘要与租约状态）。界面据此显示"谁在改哪几句"。

        ⚠ **库不存在就直接说，不去碰盘**：`collab_store.connect()` 会建目录、
        建库、跑 DDL —— 那对"提交 / 建库"是对的，对"看一眼锁列表"却是
        灾难性的：用户手填错一个路径，工具就在那儿悄悄造出一个空协作库，
        而界面上显示"没人锁"（看起来一切正常）。
        """
        from storygraph.collab.collab_store import lock_summaries
        path = Path(db_path)
        if not path.is_file():
            return {'ok': False, 'error': f'还没有协作库：{path}', 'locks': []}
        try:
            rows = lock_summaries(path)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'locks': []}
        return {'ok': True, 'error': '', 'locks': rows}
