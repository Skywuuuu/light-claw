# light-claw 改进研究

日期：2026-03-09

## 结论先行

`light-claw` 现在最值得继续强化的方向，不是立刻变成一个“大而全”的 agent 平台，而是先把下面三件事做扎实：

1. 可靠性：让配置、超时、重试、任务恢复、长连接自愈更稳。
2. 可运维性：让操作者更容易看见当前状态、失败原因、任务历史和资源消耗。
3. 低复杂度扩展：在不背离仓库“轻、直、少层次”的前提下，逐步补 onboarding、第二 provider、有限的多渠道支持。

一句话判断：`light-claw` 现在已经有“正确的骨架”，缺的是“把单机 MVP 打磨成可长期值守系统”的那层工程化。

## light-claw 已经做对了什么

### 1. 架构边界清楚

- 单机部署
- SQLite 持久化
- 每个 agent 独立 Feishu app / 独立 workspace / 独立 session
- CLI provider 抽象已经留好，但还没有过度设计
- chat、task、cron、heartbeat 已经复用同一套执行主链路

这是好事，因为它让系统仍然容易理解，也符合仓库自己的工程守则：优先正确、可读、简单，而不是追求“架构感”。

### 2. workspace 作为隔离单元很对

`AGENTS.md`、`memory/`、`.light-claw/` 和 CLI 会话状态都跟 workspace 绑在一起，这比“所有消息共用一个 agent 上下文”更稳，也更利于后面做任务、计划、长期记忆和多 agent。

### 3. 背景任务能力已经跨过了 MVP 门槛

现在已经有：

- `workspace_task`
- `scheduled_task`
- `task_run`
- `WorkspaceHeartbeatService`
- `CronService`
- `TaskExecutor`

这说明项目已经从“只会即时问答”跨到了“可以持续执行”的阶段。后续很多能力，其实都可以在这个骨架上做增量改进，而不需要重写系统。

## 当前最明显的短板

### 1. 配置与测试隔离还不够稳

本地测试结果：

```bash
UV_CACHE_DIR=/tmp/light-claw-uv-cache uv run pytest -q
```

结果：

- `37 passed`
- `2 failed`

失败集中在 `tests/test_server.py`，根因仍然是配置路径的密封性不够：

- `load_dotenv()` 在模块导入时执行，副作用太早
- `LIGHT_CLAW_DATA_DIR` 这类相对路径没有统一按 `base_dir` 解析
- 测试希望把数据放进临时目录，但实际 SQLite 仍可能落回进程当前目录
- 最终表现成 `sqlite3.OperationalError: attempt to write a readonly database`

这不是小问题。它会影响：

- 测试可靠性
- `systemd` 部署可预期性
- 多实例/多目录运行时的隔离性

### 2. 执行可靠性还偏“尽力而为”

当前 Codex 执行链路有超时、stall 检测和错误透传，但还缺少：

- 错误分类后的重试策略
- 长连接任务失败后的局部自愈
- 对“可恢复失败”和“不可恢复失败”的区分
- 更细粒度的执行日志

现状更像“失败就报错给用户”，而不是“尽量自己恢复，实在不行再报错给用户”。

### 3. 任务系统有骨架，但还不够像一个真正可值守的 task system

现在 task/cron 已经能跑，但仍然缺少几个值守型系统常见能力：

- 更明确的 lease / ownership 语义
- 更细的 run 状态与失败原因
- run-now / pause / resume / disable 之类的操作
- 并发控制和过载保护
- 更好的结果摘要与历史查看

如果未来任务量上来，现在的“顺序轮询 + 逐个执行”模型会先在吞吐和可观测性上吃亏。

### 4. 操作者视角的信息面还比较窄

项目已经有 `/healthz` 和 `/healthz/details`，但从长期运维角度看，仍然缺：

- 每个 agent 当前是否在线
- 当前运行中的 task 数量
- 最近失败的任务 / 定时任务 / Codex run
- 最近一次成功/失败的 CLI 执行摘要
- backlog / due task 数量
- 更可读的错误聚合

这类信息如果不能快速看见，系统一旦进入“还能跑，但跑得不对”的状态，排障成本会很高。

### 5. 交互层仍然太“纯文本命令行”

`/workspace`、`/task`、`/cron` 这些命令已经够工程化，但还不够产品化。问题不是命令本身不好，而是：

- 状态回显不够结构化
- 列表和详情的信息密度不高
- 用户很难一眼看出“下一步该发什么”
- 失败提示对操作者友好，但对普通使用者还不够友好

这个问题不一定要靠复杂卡片解决，但至少应该有更好的文本交互编排。

### 6. 可扩展性还停在“保留槽位”，没有形成“最小可扩展闭环”

目前 provider registry 已经有了，但真正可用的只有 `codex`。这本身没问题，问题在于：

- 第二个 provider 没接上前，很难验证抽象是否刚好
- transport 仍然基本绑定 Feishu
- 工具能力更多依赖 Codex 自身，而不是 `light-claw` 有明确的宿主边界

这意味着项目离“有演化空间”只差一步，但这一步得谨慎做，不然很容易走向过度抽象。

## 对标仓库观察

下面分成两部分：

- 可借鉴点：值得学
- 反面教训：不一定是对方“做错了”，很多是我基于其公开能力范围做出的复杂度推断。也就是说，这些更像“代价提醒”，不是简单批评。

### 一、Nanobot

仓库：<https://github.com/HKUDS/nanobot>

#### 值得学

1. **轻核心 + 广生态的产品叙事**

Nanobot 一边强调“all-in-one AI agent ecosystem”，一边强调核心代码量控制得很小，还提供大量 provider、channel、memory、MCP、工具和技能扩展位。这个叙事很强，因为它告诉用户两件事：

- 我能做很多事
- 但我不是一坨不可维护的大泥球

`light-claw` 可以借鉴这种表达方式，但不必照搬能力面。

2. **把“技能 / 工具 / 渠道 / 计划任务”讲成统一体系**

Nanobot 的 README 里把 skills、MCP、progress updates、scheduled tasks、跨渠道支持等能力串成了一个整体，而不是若干零散 feature。这一点值得学，因为 `light-claw` 现在其实也已经有类似骨架，只是表达和界面还比较原始。

3. **用户能很快理解“这个系统到底能做什么”**

Nanobot 的对外表达比很多 agent 项目更清楚。`light-claw` 也需要这种 clarity，尤其是：

- 它和普通聊天机器人有什么不同
- 为什么要用 workspace
- task / cron / agent / provider 的关系是什么

#### 反面教训

1. **能力面过宽会快速抬高配置矩阵**

这是基于公开能力范围的推断。provider、channel、memory、MCP、skills、cron 一起扩展时，测试矩阵、文档矩阵、用户心智复杂度都会迅速膨胀。`light-claw` 不应该为了“看起来全面”而同时追很多方向。

2. **“生态”很容易吞掉“可维护性”**

当一个项目开始强调插件生态、扩展市场、统一入口时，通常也意味着更多边界条件、兼容负担和版本管理负担。`light-claw` 当前的仓库守则明显不鼓励这条路。

对 `light-claw` 的启发是：可以先做“本仓库内可控的 extension points”，不要急着做开放插件系统。

### 二、OpenClaw

仓库：<https://github.com/openclaw/openclaw>

#### 值得学

1. **onboarding 和 operator experience 更像产品**

OpenClaw 的文档和仓库能力展示里，有 `setup`、`doctor`、pairing、插件、脚本、后台 daemon、usage tracking 等概念。它强的地方不是“会不会调用模型”，而是“一个普通用户能不能把它装起来并持续用下去”。

这是 `light-claw` 现在最值得学的一点。

2. **多渠道视角更完整**

OpenClaw 把 Slack、Discord、terminal 放在同一产品表述里。这说明它把“assistant 不等于某个平台机器人”这件事讲清楚了。

对 `light-claw` 来说，不一定马上支持多个渠道，但至少应该在内部把“Feishu 入口”和“任务/执行引擎”分得更清楚。

3. **围绕长期运行做了更多运维辅助**

`doctor`、usage tracking、retry policy 这类能力，本质上都不是 flashy feature，而是“让系统更像一个能长期值守的 agent”。这正是 `light-claw` 下一阶段该补的层。

#### 反面教训

1. **daemon、pairing、auth、plugins、scripts、control UI 一起上，会让系统边界变重**

这是基于公开能力范围的推断。OpenClaw 的产品面更完整，但同时也明显更重。`light-claw` 如果一次性追这些方向，很容易失去现在的简单性。

2. **多渠道和多插件容易把核心问题掩盖掉**

如果核心执行链路、超时、恢复、可观测性还不够稳，就先去做很多接入层，会让项目看起来更大，但系统质量不一定真的提升。

### 三、Aider

仓库：<https://github.com/Aider-AI/aider>

#### 值得学

1. **repo-aware coding workflow 非常成熟**

Aider 很擅长把“AI 编码”做成一个对工程师友好的闭环，而不是单次聊天。它会强调仓库上下文、代码映射、git 工作流、自动提交、lint/test 等与真实开发流程强相关的能力。

`light-claw` 如果要继续强化“coding agent behind Feishu”这个定位，最值得学的就是这种 repo-aware workflow，而不是泛化聊天能力。

2. **把安全感建立在 git 和验证上**

工程师愿意长期用 Aider，一个重要原因是它尽量让改动可见、可回退、可验证。`light-claw` 后面如果要继续做 coding task，应该把“是否改了文件、是否跑了测试、结果是否可审计”变成一等输出。

#### 反面教训

1. **Aider 的主场是终端结对编程，不是消息驱动的长期值守机器人**

这不是缺点，而是边界。`light-claw` 不应机械地把所有 Aider 的交互范式搬进 Feishu，因为两者的使用场景不同。

2. **过度追求 developer ergonomics，也可能牺牲 bot ergonomics**

例如某些非常 terminal-native 的交互，在聊天窗口里并不好用。`light-claw` 需要的是“对话式任务编排”，而不是把终端 UI 生搬硬套到 Feishu。

### 四、OpenHands

仓库：<https://github.com/All-Hands-AI/OpenHands>

#### 值得学

1. **任务导向的产品表述很强**

OpenHands 更像一个完整的软件开发 agent 平台。它的一个启发是：用户其实不关心内部叫不叫 heartbeat、cron、session，更关心“这个 agent 能不能持续把任务做完”。

`light-claw` 在对外表达和交互设计上，也应该更任务导向。

2. **控制面和运行面的区分更清楚**

OpenHands 这类系统通常会把 UI/API/control plane 和 runtime/executor 区分开。`light-claw` 不需要照搬重架构，但值得借鉴这层意识：

- 用户入口
- 任务状态
- 执行引擎
- 运行时环境

这些东西最好逐步分清。

#### 反面教训

1. **重量级平台路线和 `light-claw` 的仓库哲学冲突**

这是最重要的一条。OpenHands 很强，但它代表的是“平台化、多组件、强控制面”的路线。`light-claw` 当前明确强调少依赖、少层次、少 moving parts，这两条路不是一回事。

2. **过早平台化会让项目失去速度**

如果 `light-claw` 在现阶段就去追 UI、大型 runtime、复杂 orchestration、云化部署，极大概率会拖慢主线问题的解决。

## 建议路线图

下面不是“大重构路线图”，而是按仓库现状排的“最小必要改进”。

### P0：先把系统做成更稳的单机值守器

#### 1. 修配置密封性和测试隔离

目标：

- 所有相对路径统一相对 `base_dir`
- 避免模块导入时就执行 `load_dotenv()`
- 测试和生产目录行为一致

建议做法：

- 把 `.env` 加载移动到更明确的入口层
- 把 `LIGHT_CLAW_DATA_DIR`、`LIGHT_CLAW_ARCHIVE_DIR`、agents file、skills/mcp path 的相对路径统一走同一套 resolver
- 先把现有 `2` 个失败测试修到全绿

这是最值得先做的一项，因为它收益大、改动小、风险低。

#### 2. 做有限但明确的失败恢复机制

目标：

- Feishu 长连接掉线后能自动重连
- Codex 的可恢复错误能做有限重试
- 错误类型能区分：超时、stall、启动失败、退出非零、解析失败

建议做法：

- 不引入额外队列系统
- 只加本地退避重试和错误分类
- 每类错误只保留少数几种可操作状态

重点是“有限重试”，不要做复杂调度器。

#### 3. 补最小可观测性

目标：

- 一眼看到 agent 是否在线
- 一眼看到是否有 due task 堵住
- 一眼看到最近一次失败是什么

建议做法：

- 扩展 `/healthz/details`
- 增加每 agent 的 task/schedule/run 计数与最后错误摘要
- 把关键 run 事件打成结构化日志

不需要马上做 UI，一个更清楚的 JSON 健康详情就够有价值。

#### 4. 让 task/cron 更像“能管的任务”

目标：

- 能看
- 能停
- 能重跑
- 能知道失败在哪

建议做法：

- 增加 `/task run <id>`
- 增加 `/task pause <id>` / `/task resume <id>`
- `/task status` 返回最近几次 run 历史
- `/cron list` 返回 next run / last run / last error

这属于很高 ROI 的交互改进。

### P1：把产品体验补齐，但保持轻量

#### 1. 增加 `/doctor`

检查项可以只做最有用的几类：

- Feishu 配置是否完整
- `codex` 是否可执行
- 数据目录是否可写
- 当前 provider 是否可用
- 关键 env 是否冲突

这项会明显降低部署和排障成本，OpenClaw 在这方面值得学。

#### 2. 改善文本交互编排

不一定要飞书卡片，但至少可以优化：

- `/help` 的信息结构
- `/task` / `/cron` 的列表展示
- 失败提示的下一步建议
- 超时时给出更清楚的原因和建议动作

即使继续使用纯文本，也可以让体验明显更像产品。

#### 3. 强化 coding-task 输出

如果 `light-claw` 继续定位在 coding agent，就应该补这些“结果面”：

- 本次是否修改文件
- 本次是否创建 commit
- 本次是否运行测试
- 本次失败发生在哪一步

这一点最值得向 Aider 学。

#### 4. 接上第二个 provider，但只接一个

建议只做一件事：

- 真正接通 `claude-code` 或另一个明确目标 provider

理由：

- 这样可以验证当前 provider abstraction 是否够简洁
- 可以暴露当前哪些地方仍然过度依赖 Codex 语义

不要在这一阶段同时接多个 provider。

### P2：只在有真实需求时再做的方向

#### 1. 第二个 transport

例如 terminal、Slack 或 Discord 中的一个。目的不是铺渠道，而是验证“Feishu 入口”和“任务执行内核”是否真正解耦。

#### 2. 最小只读控制面

如果后续运维信息越来越多，可以考虑一个很轻的只读页面，展示：

- agent 在线状态
- 最近任务
- 最近错误
- workspace 数量
- cron 状态

但这应该排在 P0/P1 后面。

#### 3. 更正式的 extension model

只有当第二个 provider、第二个 transport、以及一批稳定的内部扩展点都已经出现后，才值得讨论是否要做更正式的 extension model。

现在不适合急着做开放插件系统。

## 明确不建议现在做的事

### 1. 不要过早放弃 SQLite

在当前单机模型下，SQLite 仍然是合适的。现在的主要问题不是数据库太弱，而是配置、恢复和可观测性还没打磨好。

### 2. 不要上消息队列或分布式任务系统

现阶段引入 Redis、Celery、Kafka 之类，只会显著增加复杂度，收益并不匹配。

### 3. 不要先做“大插件平台”

`light-claw` 现在更适合做“少量、明确、内建的扩展点”，而不是 marketplace 式的插件体系。

### 4. 不要先做复杂 UI

先把文本命令、健康检查、日志、状态接口做好，再决定是否需要 UI。

### 5. 不要为了追求多 provider / 多渠道而牺牲当前主线稳定性

先把 Codex + Feishu + task/cron 这条主线打磨好，再谈横向扩展。

## 我会怎么排接下来 3 个实际迭代

### 迭代 1

- 修配置路径解析与 `.env` 加载时机
- 修复当前 `2` 个 server 测试失败
- 扩展 `/healthz/details`
- 优化超时/错误分类文案

### 迭代 2

- 增加长连接重连和有限退避
- 增加 task pause/resume/run-now
- 改进 `/task status` 和 `/cron list`
- 增加 `/doctor`

### 迭代 3

- 强化 coding task 结果输出
- 验证第二个 provider
- 视实际需求决定是否做第二 transport

## 参考资料

### 本地代码与测试

- `README.md`
- `AGENTS.md`
- `src/light_claw/config.py`
- `src/light_claw/task_executor.py`
- `src/light_claw/codex_runner.py`
- `src/light_claw/cron.py`
- `src/light_claw/heartbeat.py`
- `src/light_claw/store.py`
- `tests/test_server.py`

### 外部对标仓库

- Nanobot: <https://github.com/HKUDS/nanobot>
- OpenClaw: <https://github.com/openclaw/openclaw>
- Aider: <https://github.com/Aider-AI/aider>
- OpenHands: <https://github.com/All-Hands-AI/OpenHands>
- Aider docs: <https://aider.chat/docs/usage/commands.html>
- OpenHands docs: <https://docs.all-hands.dev/modules/usage/how-to/cli-mode>
