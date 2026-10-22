# 项目约定

- 当前选型：私信适配 → 业务服务保存会话与消息 → FastGPT 工作流 → 业务服务发送。行业话术、分支与留资流程由 FastGPT 工作流维护；业务后台管理机器人暂停／恢复和未解答问题。
- 两个当前子项目：`FastGPT/`、`business-service/`。上游源码独立维护，先通过 API 集成。
- `docs/` 只保留 `PRD.md`、`PLAN.md` 和 `business-flow.png`；运行检查产物放 `artifacts/`，当前实施以 `docs/PLAN.md` 为准。
- 仅修改任务相关内容。未拿到甲方接口契约前，不编造抖音接口、Token 或签名格式。
- 区分模型生成、待发送、平台受理、模拟发送、待核对和线索跟进；不要把它们当成同一个状态。
- 不自行运行全量测试。只进行与变更有关的定向检查，记录实际验证范围。
- PowerShell 命令通过 `pwsh.exe -NoLogo -Command` 执行，写文件使用 UTF-8 无 BOM。
- 部署保持持久卷；不得使用全局 Docker prune 或自动删除数据库卷。
- `FastGPT/` 内继续遵循其 AGENTS.md。用户已授权本项目的 Docker 一键启动配置。

