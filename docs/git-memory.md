# Git 执行记忆：任务即分支（P1-8）

> 目标：让 agent 的每一次任务执行都拥有**独立、可回放、可安全遗弃**的 git 轨道——
> 任务即分支，分支即 worktree，步骤即提交。失败时整条 worktree 删掉，主树零污染。

## 心智模型

```
主树 (main, 干净)
 └── task/<slug>/        ← 每次任务 begin 出来的独立 worktree（独立分支）
        ├── step commit 1   (绿测后 auto-commit)
        ├── step commit 2   (绿测后 auto-commit)
        └── …
```

- **任务即分支**：`task_begin(task)` 把自由文本任务 id slug 成 `task/<slug>` 分支，
  并用 `git worktree add` 切出隔离工作区。主树的工作区与 HEAD 全程不被触碰。
- **步骤即提交**：每个有意义的小步完成后，`task_step_commit` 自动检测工作区变更并提交。
- **失败即遗弃**：`task_finish(success=False)` 强制删除 worktree 并丢弃任务分支，
  主树分支、主树文件、主树 status 全部保持原样。

## 三个接口（`spark.git_memory`）

| 接口 | 作用 |
| --- | --- |
| `task_begin(sb, TaskBeginArgs(task=…, base=None))` | 建 `task/<slug>` 分支 + worktree，返回 `branch` 与 `worktree_path` |
| `task_step_commit(sb, TaskStepCommitArgs(worktree=…, step=…, run_tests=True))` | 检测变更 → （可选）绿测门控 → 自动提交 |
| `task_finish(sb, TaskFinishArgs(worktree=…, branch=…, success=…))` | 成功：保留 worktree 并给合并指引；失败：force 删 worktree + 删分支 |

它们全部复用 `spark.tools.gitops` 既有的 `worktree_create / worktree_remove /
git_commit / run_tests / detect_test_command`，不重写其行为，只在其上串自动循环。

## Auto-commit 策略：测试通过才提交（aider 范式）

**本项采用「绿测才落盘」（test-gated auto-commit）。**

`task_step_commit` 的决策表：

| 情形 | 行为 | `test_gate` |
| --- | --- | --- |
| 工作区无变更 | 不提交，`committed=False` | `disabled` |
| 检测到测试入口且**通过** | `git add -A` 后用 `step` 作 subject 提交 | `passed` |
| 检测到测试入口且**失败** | **不提交**，工作区留红供下一轮修复，返回测试输出 | `failed` |
| 开了 `run_tests` 但仓库无测试入口 | 无法门控，照常提交并标注 | `not-detected` |
| `run_tests=False` | 步骤级即时 checkpoint，跳过测试 | `disabled` |

**为什么是绿测门控，而不是每步无脑提交：**

1. **aider 的核心契约**就是「测试绿了才落盘」——可运行的中间状态才配进历史，
   红色中间态留在工作区给下一轮修，而不是污染分支历史。
2. worktree 已经提供了「失败整体删除」的隔离；若在 worktree 里也把破损状态提交，
   分支历史会堆满红色 checkpoint，回溯与合并成本反而变高。
3. 仓库没有测试入口时自动降级为「无门可守，照常提交并标注」，不会卡死任务。
4. `step` 描述直接成为 commit subject，每一条提交都带任务语义、可独立 `git bisect`。

## 成功 vs 失败

- **成功**：`task_finish(success=True)` 保留 worktree 与分支，返回合并指引
  （`git merge <branch>` → 验收后 `git worktree remove`）。本层**绝不自动 merge/push**，
  合并是人的决定。
- **失败**：`task_finish(success=False)` 先 `worktree remove --force`，再 `git branch -D`
  丢弃分支，并在返回里记录被遗弃的分支 tip。主树的 HEAD、tracked 文件、status 均不变。

> 注：沙箱化的 worktree API 要求 worktree 位于主 checkout 之内，因此保留型 worktree 会在
> 主树 `git status` 里以 `?? task/`（已注册目录）出现——那是 worktree 本身，不是 tracked
> 文件污染；失败遗弃后该目录被删除，主树 status 完全干净。

## 快速验证

```bash
python3 -m pytest tests/test_p1_08_gitmemory.py -q
```

端到端在**临时真实 git 仓库**里跑：建 worktree → 绿测提交循环 → 红步被拦截 →
失败清理 → 主树 HEAD 与文件零变化。不依赖网络。
