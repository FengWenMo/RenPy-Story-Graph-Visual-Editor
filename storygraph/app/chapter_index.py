"""
`storygraph.app.chapter_index` —— 项目里**有哪些章可以打开**（左侧导航的数据源）。
================================================================================

为什么要有它（2026-10-23，用户给了参考界面）
--------------------------------------------
参考的那个界面左边常驻一棵「章节 / 片段」树 + 一个搜索框：**永远看得见
整个项目里有什么、现在在哪一章**。我们这个工具在此之前只有两条路：

  · 剧情图（只显示"导入过"的 label）；
  · 卡片页顶上那个 `label` 下拉框（只显示**当前这个文件**里的 label）。

于是"项目里另外 39 个文件"在界面上根本不存在 —— 用户想开某一章，得先去
`game/` 目录里翻文件名，再走「直接编辑单章」。而同一件事的反面：剧情图里
的块**不代表**项目的全部（那是刻意设计，用户明确要求过），所以图也不能
当目录用。

这个模块只做一件事：**把项目扫成一份目录**（文件 → label + 行号）。
它不许碰 Qt，也不解析剧本 —— 解析发生在真正打开那一章的时候
（`parsing.discover` 的注释里说明了为什么这里用正则就够了）。

刻意不做的事
------------
· **不缓存**。40 个文件读一遍 ~10ms（打开项目本身是 1.8 秒），缓存的
  失效条件反而多一个出错的地方；调用方（导航面板）只在"打开项目 /
  手动刷新 / 导入之后"这三处调用。
· **不猜**。读不出权限、文件被独占、编码坏掉 → 那一条**跳过**，
  并记进 `errors` 让界面说一声，而不是假装项目里只有 39 个文件。
· **不解析 label 名字里的人话**。`chapter1_1` 就显示成 `chapter1_1`。

排序：按相对路径（`game/` 之后的部分）字典序 —— 与文件管理器的顺序一致，
不按修改时间。理由：按时间排会让人"找不到昨天那个文件排到哪去了"，
而按路径排每次打开都一样。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.parsing import discover


@dataclass(frozen=True)
class LabelEntry:
    """一个 label：名字 + 在文件里第几行开始（1-based）。"""

    name: str
    line: int = 0

    def describe(self) -> str:
        """给导航那一行用的副标题（没有行号就只说名字）。"""
        return f'第 {self.line} 行' if self.line > 0 else ''


@dataclass(frozen=True)
class FileEntry:
    """一个 `.rpy` 文件 + 它声明的 label（按出现顺序）。"""

    path: str                        # 绝对路径（进 `load_script` 用这个）
    rel: str                         # 相对项目根，永远是 `/` 分隔
    labels: tuple = ()               # tuple[LabelEntry, ...]
    mtime: float = 0.0

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def stem(self) -> str:
        return Path(self.path).stem

    @property
    def label_count(self) -> int:
        return len(self.labels)

    def label_names(self) -> tuple:
        return tuple(l.name for l in self.labels)

    def has_label(self, label: str) -> bool:
        return any(l.name == label for l in self.labels)


@dataclass(frozen=True)
class SearchHit:
    """搜索结果：命中文件里**命中的那几个** label（文件自己也命中时是全部）。"""

    file: FileEntry
    labels: tuple


@dataclass
class ChapterIndex:
    """一份项目目录。空索引（没打开项目）是合法的：`bool(index)` 为假。"""

    root: str = ''
    files: tuple = ()                # tuple[FileEntry, ...]
    errors: tuple = ()               # tuple[(rel, 原因), ...] —— 读不出来的
    scanned: int = 0                 # 扫到几个 .rpy（含没 label 被滤掉的）

    # ---- 计数 / 判断 ----
    def __len__(self) -> int:
        """有几个**能打开的章**（有 label 的文件）。"""
        return len(self.files)

    def __bool__(self) -> bool:
        return bool(self.files)

    def label_count(self) -> int:
        return sum(f.label_count for f in self.files)

    def file_count(self) -> int:
        return len(self.files)

    def skipped_without_label(self) -> int:
        """扫到了、但里面没有 label 的文件数（`options.rpy` / `gui.rpy` 这类）。"""
        return max(0, int(self.scanned) - len(self.files))

    # ---- 查找 ----
    def find_file(self, path) -> FileEntry | None:
        """按绝对路径或相对路径找一条（找不到返回 None，不抛）。"""
        want = str(path or '')
        if not want:
            return None
        norm = want.replace('\\', '/')
        for f in self.files:
            if f.path.replace('\\', '/') == norm or f.rel == norm:
                return f
        # 只给了文件名（`ch01.rpy`）也认 —— 导航里右键复制出来的常是这个名字
        hits = [f for f in self.files if f.name == Path(norm).name]
        return hits[0] if len(hits) == 1 else None

    def find_label(self, path, label) -> LabelEntry | None:
        f = self.find_file(path)
        if f is None:
            return None
        for l in f.labels:
            if l.name == str(label or ''):
                return l
        return None

    def search(self, term: str, limit: int = 500) -> list:
        """
        按名字过滤（文件路径 / 文件名 / label 名），大小写不敏感。

        空词 → 全部（每条 hit 都带全部 label）。文件自己命中时也带全部
        label：搜 `ch01` 想看到的是"这个文件里有什么"，而不是"这个文件
        叫 ch01"这一条空结果。
        """
        q = str(term or '').strip().casefold()
        if not q:
            return [SearchHit(f, f.labels) for f in self.files]
        out: list = []
        for f in self.files:
            if q in f.rel.casefold() or q in f.name.casefold():
                out.append(SearchHit(f, f.labels))
                continue
            hits = tuple(l for l in f.labels if q in l.name.casefold())
            if hits:
                out.append(SearchHit(f, hits))
            if len(out) >= limit:
                break
        return out

    def signature(self) -> tuple:
        """`((rel, mtime), ...)` —— 便宜地判断"磁盘上变过没有"。"""
        return tuple((f.rel, round(f.mtime, 3)) for f in self.files)


def build_index(project_root, extra_dirs=None) -> ChapterIndex:
    """
    扫一遍项目，返回目录。**读不出来的文件不抛，记进 `errors`。**

    `extra_dirs` 与 `discover_scripts` 同义（额外扫描目录）。
    """
    root_text = str(project_root or '').strip()
    if not root_text:
        return ChapterIndex()
    root = Path(root_text)
    if not root.exists() or not root.is_dir():
        return ChapterIndex(root=root_text,
                            errors=(('', '目录不存在'),))

    scanned = 0
    files: list = []
    errors: list = []
    try:
        scripts = list(discover.discover_scripts(root, extra_dirs=extra_dirs))
    except Exception as e:                        # noqa: BLE001 - 扫盘失败要说清楚
        swallow('app.chapter_index.discover', e)
        return ChapterIndex(root=root_text, errors=(('', f'扫描失败：{e}'),))

    for fp in scripts:
        scanned += 1
        try:
            rel = fp.relative_to(root).as_posix()
        except ValueError:                        # extra_dirs 在根外面
            rel = fp.as_posix()
        try:
            labels = discover.label_lines_in_file(fp)
        except Exception as e:                    # noqa: BLE001 - 单个文件坏掉不拖垮整份目录
            swallow('app.chapter_index.labels', e)
            errors.append((rel, f'读不出 label：{e}'))
            continue
        if not labels:
            # `options.rpy` / `gui.rpy` / 纯 `init python` 的辅助文件：
            # 里面没有章可开，列出来只是噪音（但 `scanned` 里还是算它）。
            continue
        try:
            mtime = fp.stat().st_mtime
        except OSError:
            mtime = 0.0
        files.append(FileEntry(
            path=str(fp), rel=rel,
            labels=tuple(LabelEntry(name=n, line=int(ln))
                         for n, ln in labels),
            mtime=float(mtime)))

    files.sort(key=lambda f: f.rel.casefold())
    return ChapterIndex(root=root_text, files=tuple(files),
                        errors=tuple(errors), scanned=scanned)


__all__ = ['LabelEntry', 'FileEntry', 'SearchHit', 'ChapterIndex',
           'build_index']
