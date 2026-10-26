# AGENTS.md —— 给在本仓库工作的 AI / 自动化 agent

> TL;DR：**跑全量测试只用 `python tools/fasttest/run.py`**。不要用裸
> `python -m pytest` 跑全量（串行约 20 分钟，而且和另一个 pytest 并发会互删
> `.pip-tmp/pytest`，炸出成百上千个假 ERROR）。

## 一、全量测试：唯一推荐入口

```powershell
python tools/fasttest/run.py
```

- 它是**全量**：`tests/` 下所有用例，不跳过、不筛选用例、不改判据；
  只做并行调度、独立临时目录、重文件优先。
- 退出码：`0` 全绿 / `1` 有失败 / `2` 用法或环境问题 / `5` 没收集到用例。
- 失败节点、完整日志、JUnit 都在 `.pip-tmp/fasttest/runs/<时间戳>/`；
  最后一行会直接打印失败 nodeid，复制即可定向复跑。
- 怀疑某条失败是"并行假失败"（偶发/资源竞争）时，单跑它对比：
  `python tools/fasttest/run.py --serial <nodeid>`。
- 需要 pytest 参数就原样透传：

  ```powershell
  python tools/fasttest/run.py -x --tb=short
  python tools/fasttest/run.py -k "golden or golden_scan" --workers 4
  ```

- 机器很忙时降并发：`--workers 8`；不要用 `-n 0`。
- 单文件调试仍可用 `python -m pytest tests/test_xxx.py -q`；
  **提交/收尾前必须再用 fasttest 跑一次全量**。
- 不要同时跑两个全量：fasttest 自带运行锁；**裸 pytest 不受锁保护**，
  两个进程会互相删 `.pip-tmp/pytest`（已知 D6）。
- 没装 `pytest-xdist` 时先 `python -m pip install -r requirements-dev.txt`；
  只跑无界面链路也要装，否则 UI 测试会被 skip，不算"全量"。

细节（原理、选项、排障、seed 维护）：`tools/fasttest/README.md`。

## 二、动手前先读项目记忆

- `.workbuddy/memory/MEMORY.md`：十条铁律、最高频实操坑、当前片的状态。
- 契约速查：`docs/契约速查_storygraph.md`（很大，按需 grep）。
- 改代码前先 `git status` + 看文件时间戳：本项目常有并行工作者，
  **文档只追加、提交按文件分组、写完回读校验**。
