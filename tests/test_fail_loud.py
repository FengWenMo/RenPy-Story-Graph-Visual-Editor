"""
坏输入必须喊出来（2026-11-01，外部审查 H1 / 5.4）
===============================================

审查报告实机跑出来的那张表（**当时全部退出 0**）：

| 你做了什么 | 工具说 | 实际 |
|---|---|---|
| 传一个不存在的 .rpy | 成功（退出码 0） | 给你一张只有表头的空表 |
| 传一个不存在的项目目录 | 成功 | "章节 0 个，通过" |
| 把目录当 .rpy 传 | 成功 | 同上 |

对 CI 来说这是最坏的形态：它拿退出码判断成败，而工具对"我什么都没找到"
和"一切正常"给了同一个答案。这个文件守四条：

  1. 找不到文件 / 传了目录 / 后缀不对 → **退出码 2**，并点名路径；
  2. 项目目录不存在 / 里面没有一个 .rpy → **退出码 2**（不是"通过"）；
  3. 读到了文件但**一条语句都没有** → 退出码 1（空表不是成功）；
  4. `collab import-ir` 不许拿参数当输出路径覆盖剧本：已存在就要求 `--force`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph import cli                                 # noqa: E402
from storygraph.core import safeguard                      # noqa: E402

EXIT_USAGE = 2


@pytest.fixture(autouse=True)
def _clean_swallow():
    """静默失败是全局状态，别让上一条用例的记录串到下一题。"""
    safeguard.clear()
    safeguard.set_debug(None)
    yield
    safeguard.clear()
    safeguard.set_debug(None)


class TestTableCommandRefusesBadInput:

    def test_missing_rpy_exits_2_and_never_writes_a_table(self, tmp_path, capsys):
        missing = tmp_path / 'nope.rpy'
        code = cli.main(['table', 'rpy2xlsx', str(missing)])
        assert code == EXIT_USAGE
        assert '找不到剧本文件' in capsys.readouterr().err
        assert not (tmp_path / 'nope.xlsx').exists(), '还是生成了空表'

    def test_directory_given_as_rpy_exits_2(self, tmp_path, capsys):
        d = tmp_path / 'adir.rpy'
        d.mkdir()
        code = cli.main(['table', 'rpy2xlsx', str(d)])
        assert code == EXIT_USAGE
        assert '目录' in capsys.readouterr().err

    def test_wrong_suffix_exits_2(self, tmp_path, capsys):
        src = tmp_path / 'a.txt'
        src.write_text('label a:\n    return\n', encoding='utf-8')
        code = cli.main(['table', 'rpy2xlsx', str(src)])
        assert code == EXIT_USAGE
        assert '.rpy' in capsys.readouterr().err

    def test_empty_rpy_is_a_failure_not_an_empty_table(self, tmp_path, capsys):
        """空文件 → 退出码 1（"读到了，但里面什么都没有"）。"""
        src = tmp_path / 'empty.rpy'
        src.write_text('', encoding='utf-8')
        code = cli.main(['table', 'rpy2xlsx', str(src)])
        out = capsys.readouterr()
        assert code == 1
        assert '一条语句' in out.err

    def test_real_chapter_still_succeeds(self, tmp_path):
        src = tmp_path / 'ch.rpy'
        src.write_text('label ch:\n    "一句台词。"\n    return\n',
                       encoding='utf-8')
        assert cli.main(['table', 'rpy2xlsx', str(src)]) == 0
        assert (tmp_path / 'ch.xlsx').is_file()

    def test_unclosed_quote_is_reported_and_exits_nonzero(self, tmp_path, capsys):
        """
        引号没闭合：解析器记了错误，从前那个错误**没有任何出口**。
        现在 CLI 把它逐条打出来并退 1。
        """
        src = tmp_path / 'broken.rpy'
        src.write_text('label ch:\n    e "没闭合\n    return\n',
                       encoding='utf-8')
        code = cli.main(['table', 'rpy2xlsx', str(src)])
        err = capsys.readouterr().err
        assert code != 0, '引号没闭合仍然报成功'
        assert '引号没闭合' in err or '一条语句' in err
        assert '第 2 行' in err, '没点名是哪一行'


class TestValidateRefusesBadProject:

    def test_missing_project_dir_exits_2(self, tmp_path, capsys):
        code = cli.main(['validate', str(tmp_path / 'no_such_project')])
        assert code == EXIT_USAGE
        assert '找不到项目目录' in capsys.readouterr().err

    def test_dir_without_any_rpy_exits_2(self, tmp_path, capsys):
        """空目录不是"一个干净的项目"，是指错了地方。"""
        (tmp_path / 'game').mkdir()
        code = cli.main(['validate', str(tmp_path)])
        assert code == EXIT_USAGE
        assert '没有任何 .rpy' in capsys.readouterr().err

    def test_file_as_project_exits_2(self, tmp_path, capsys):
        f = tmp_path / 'a.rpy'
        f.write_text('label a:\n    return\n', encoding='utf-8')
        code = cli.main(['validate', str(f)])
        assert code == EXIT_USAGE
        assert '不是目录' in capsys.readouterr().err


class TestCollabImportIrDoesNotOverwrite:

    def test_existing_out_path_requires_force_and_is_left_untouched(
            self, tmp_path, capsys):
        """
        审查 C1 的事故形态：`--out <某个.rpy>` 把剧本写成 Excel。
        现在已存在的目标**必须显式 --force**，否则拒绝且一个字节不动。
        """
        project = tmp_path / 'proj'
        (project / 'game').mkdir(parents=True)
        (project / 'game' / 'ch01.rpy').write_text(
            'label ch01:\n    "一句。"\n    return\n', encoding='utf-8')
        victim = project / 'game' / 'ch02.rpy'
        original = b'label ch02:\n    "b"\n    return\n'
        victim.write_bytes(original)

        code = cli.main(['collab', 'import-ir', '--project', str(project),
                         '--out', str(victim), '--user', 'tester'])
        assert code == EXIT_USAGE
        assert '拒绝覆盖' in capsys.readouterr().err
        assert victim.read_bytes() == original, '剧本被 Excel 覆盖了'

    def test_refuses_non_xlsx_out_path(self, tmp_path, capsys):
        """后缀白名单：哪怕加了 --force，也不许把 .rpy 写成 Excel。"""
        project = tmp_path / 'proj2'
        (project / 'game').mkdir(parents=True)
        (project / 'game' / 'ch.rpy').write_text(
            'label ch:\n    "一句。"\n    return\n', encoding='utf-8')
        victim = tmp_path / 'notes.rpy'
        victim.write_text('label x:\n    return\n', encoding='utf-8')

        code = cli.main(['collab', 'import-ir', '--project', str(project),
                         '--out', str(victim), '--force', '--user', 'tester'])
        assert code == EXIT_USAGE
        assert '只允许' in capsys.readouterr().err
        assert victim.read_text(encoding='utf-8').startswith('label x:')


class TestDebugSwitch:
    """审查 5.4：`--debug` 写在 `main()` 里，却**没接到参数表上**（等于不存在）。"""

    def test_debug_flag_is_accepted(self):
        parser = cli.build_parser()
        args = parser.parse_args(['--debug', 'validate', '.'])
        assert args.debug is True

    def test_summary_line_reports_swallowed_errors(self, tmp_path, capsys):
        safeguard.clear()
        safeguard.swallow('demo.context', RuntimeError('boom'))
        line = safeguard.summary_line()
        assert '被兜住' in line and 'demo.context' in line

    def test_summary_line_is_empty_when_nothing_was_swallowed(self):
        safeguard.clear()
        assert safeguard.summary_line() == ''

    def test_debug_goes_to_standard_logging_too(self, tmp_path):
        """`logging` 是所有排障工具认得的那条线（审查 5.4：全项目 0 处）。"""
        import logging

        log_file = tmp_path / 'run.log'
        safeguard.clear()
        safeguard.configure(log_file=str(log_file))
        try:
            safeguard.swallow('demo.logging', ValueError('x'))
        finally:
            for h in list(safeguard.logger.handlers):
                safeguard.logger.removeHandler(h)
                h.close()
            safeguard.configure()
        text = log_file.read_text(encoding='utf-8')
        assert 'demo.logging' in text
        assert logging.getLogger(safeguard.LOGGER_NAME) is safeguard.logger
