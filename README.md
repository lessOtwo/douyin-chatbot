# douyin-chatbot

抖音商家私信留资助手。当前运行 **业务服务 + FastGPT**：业务服务保存客户消息与会话、控制机器人暂停／恢复、调用 FastGPT 工作流，并通过统一发送队列回复。非同行消息先按商家已审核项目资料答疑；未留资时每轮在末尾轮换邀请留个联系方式，已留资后继续答疑但不再索要。

| 目录 | 用途 |
| --- | --- |
| `business-service/` | Rust/Axum 前后端一体后台、SQLite 消息与会话、发送记录 |
| `FastGPT/` | 上游源码；可视化行业流程、模型与商家知识 |
| `docs/` | 当前 PRD、业务流程图和实施计划 |

Windows 一键启动：`pwsh.exe -NoLogo -Command "& 'F:/douyin-chatbot/start.ps1'"`。Linux：`bash start.sh`。启动脚本仅补全 `.env` 缺少的配置，不覆盖已有值。

| 页面 | 本地地址 |
| --- | --- |
| 业务后台 | http://localhost:8088 |
| FastGPT 流程与模型 | http://localhost:3000 |

业务后台使用 `.env` 中的 `BUSINESS_API_KEY` 登录。侧栏可进入 FastGPT；首次仍需配置模型、工作流与应用 Key。默认 `SEND_MODE=mock`，模拟回复只保存本地。甲方／抖音原生私信接口尚未提供，当前使用标准化平台事件与发送适配契约。

“行业配置”页维护行业、项目及各项目开场问句，先保存草稿再发布；“商家入驻”页生成一次性邀请链接。商家通过链接选择行业和项目分组，填写数字抖音号、抖音昵称及门店资料。平台审核通过后自动生成账号并发布商家资料，由业务服务提供给 FastGPT；工作流应用与真实渠道授权仍需配置，入驻完成不代表机器人已启用。

`Typebot/` 仅是历史源码，不参与启动。当前实施以 [PRD](docs/PRD.md) 和 [计划](docs/PLAN.md)为准。

## 业务服务更新

在本地构建 `douyin-chatbot/business-service:0.1.0`，用 `docker save` 导出镜像并传到服务器。服务器执行 `docker load` 后，在现有 `/opt/douyin-chatbot` 目录用 `compose.server.yaml` 仅重建 `business-service` 容器并确认健康状态；保留原有 `.env`、`config/accounts.json` 和全部数据卷。FastGPT 工作流提示词变更需单独发布到已有工作流，不能只更新业务镜像。清理时只删除确认未被引用的项目旧镜像。
