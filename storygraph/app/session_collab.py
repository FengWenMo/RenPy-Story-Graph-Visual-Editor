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
