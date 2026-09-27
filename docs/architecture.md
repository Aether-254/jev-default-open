# 架构与运行边界

Jev Default Open 把「捕获打开请求」「采集上下文」「建议动作」「确认并执行」分开。领域词汇以 `CONTEXT.md` 为准；本文描述当前代码边界，不把规划中的适配能力写成已完成事实。

## 组件

| 组件 | 对外职责 | 内部隐藏的机制 |
| --- | --- | --- |
| Python Broker | 编排一次 Open Request，维护确认和终态 | deadline、去重、取消、回退、历史和偏好 |
| Interception | 手动启停、验证请求、传递已接管请求 | Native Host 握手、IPC、所有权竞争、进程退出 |
| Context | 构造 Context Envelope | 路径/URI 规范化、目录标签、Provider 超时和目标/会话匹配 |
| Actions | 发现兼容 Open Action、执行已确认选择 | Windows 关联、应用/Profile、参数数组和执行前校验 |
| Decision | 为上下文选择 Scene 和 Open Action | Jev typed choice、概率校验、阈值、限时网络和精确缓存 |
| State | 偏好、历史、设置、精确缓存与来源记录 | SQLCipher、DPAPI、事务、最多 1000 条历史、账户隔离及索引维护 |
| UI | 引导、托盘、批量确认与诊断 | Qt 主线程、请求更新、场景纠正、取消、Provider 数据清理与显式导出 |

组合根位于 `src/jev_open/bootstrap.py`，测试通过接口注入替身，不要求启动真实应用。

### 三个原生产物

- `native_host.exe`：Python 的 stdio 协议对端；处理 Hook 生命周期和紧急停用。
- `open_hook.dll`：默认构建禁用；发布构建显式使用已验证的 MSVC x64 ON 分支。
- `native_claim.dll`：供 Broker 与 DLL 对同一请求进行原子所有权裁决，避免 ACK 迟到后原调用和 Broker 同时打开目标。

三者由同一次 x64 构建生成并一起使用。`JEV_ENABLE_EXPERIMENTAL_HOOK=OFF` 仍是源码默认值；发布包显式选用独立的 MSVC x64 ON 构建。该构建通过 CTest、start/stop 和包内 ShellExecute 捕获探针。Explorer 通过用户级右键/Open With 进入，并由本地 IPC 转发到已运行实例。

## 决策链与实验入口

1. 当前离线链由合成请求/替身入口驱动。未来实验分支的入口是手动启用后捕获符合范围的 `open`/`edit` 请求；默认构建不执行该捕获。
2. 原调用与 Broker 通过有界 IPC 和原子所有权状态竞争。只有取得所有权的分支可继续负责打开；连接失败、超时或接管失败由原调用继续。
3. Broker 在 deadline 内解析目标，收集已授权且可验证的聊天证据、路径标签，再发现兼容 Open Action。
4. 明确偏好可跳过 Jev；精确模型缓存通过 SQLCipher 读写；未命中时一个请求内分别询问 Scene 与 Open Action。仅有一个候选时不构造无意义的多选题，无候选则回退。
5. UI 展示建议与证据，用户独立修正场景和动作。关闭/取消不启动目标；用户确认后才执行动作。
6. 终态和显式记忆范围写入加密状态。晚到模型答案、重复事件和取消不得造成第二次启动。

文件 provenance 的存取与恢复已接入 Context/State；索引在启动和每日维护，Provider 数据清理 UI 会调用相应清理边界。链路接入并不等于 IM 来源事件已可实际采集。

系统回退仍可能打开真实文件或 URL，因此离线演示与测试使用替身执行器。`--demo` 不等价于“开启真实 Hook 的演示配置”：前者完全离线；配置的 `mode: demo` 仅改变真实路径中的等待预算。

## 安全、身份与故障边界

- 应用不自动提权，不把低权限客户端的 IPC 作为管理员启动任意程序的入口。普通启动 Hook 关闭；UI 手动启用仍受 native 编译能力检查，默认构建明确拒绝。
- 原生客户端身份、会话、消息大小、请求结构和所有权在执行前校验；模型只能选已枚举候选，不能生成任意命令。
- 路径、URL、聊天正文都属于不可信数据。聊天关联不确定、账号不匹配、客户端版本不受支持或提取超时，返回无上下文，不猜测其他会话。
- SQLCipher 不可用、DPAPI 无法解包、密钥缺失、旧明文数据库或未知 schema 都是阻断错误，不能静默降级为明文 SQLite。
- 最终启动参数通过独立 argv/受控 Windows 调用传递，不能把来自聊天或模型的文本拼进 shell 命令。
- Actions 发现只保留绝对 `.exe`、有限 `open`/`edit` verb 和单一 `{target}` argv 模板；
  Profile 目录必须是绝对、非符号链接目录，并在执行前重新校验。候选应用与 native host
  使用去除 Jev/API key 及常见凭据变量的子进程环境。Windows `ShellExecuteW` 回退没有环境
  块参数，因此关联应用仍可能继承 Broker 环境，不能把该回退路径视作凭据隔离完成。
- 停用 Hook 与待处理请求清理分别处理；实际目标只应有一个终态。自动化测试覆盖终态竞争，真实桌面恢复仍需专门验收。

## 集成层的真实状态

QQ Provider 具备协议、目标匹配和认证事件输入边界。`qq_bridge/src/integration.js` 提供显式的 `createTrustedAdapter` / `createAuthenticatedTransport` 工厂，Python Provider 也只接受绑定传输在每个事件上报告的认证结果；配置默认 `qq_bridge_mode=disabled`，`fixture` 仅用于合成回放。`qq_runtime_path` 若显式配置，只读 `QQ.exe` 的 SHA-256 和 VERSIONINFO，并要求 `qq_allowed_versions` 或 `qq_allowed_sha256` 显式匹配；这只是版本门禁，不读取聊天，也不使 adapter ready。工厂不会实现 named-pipe 认证，当前版本 trusted adapter 和 transport 仍没有生产接线，普通运行不会从 QQ 自动取得聊天。微信依赖 `VerifiedVisibleContextReader`，尚无具体 reader 实现，因此同样不会自动读取当前可见聊天。这两项需要补实现，再做测试账号/客户端版本现场验证；不能表述成「全部实现，仅差验收」。飞书、Slack、钉钉的骨架当前返回不可用，不提供聊天正文。

Windows 关联发现、Profile 枚举和 native Hook 受操作系统及应用实现限制。当前不保证 32 位、受保护进程、AppContainer、跨完整性级别，以及浏览器内部导航。后续提升权限的设计必须先独立处理 IPC 身份、允许列表和非提权启动器，参见 `docs/adr/0004-verified-rollout.md`。

## 验证层次

1. 类型/策略/协议的确定性单元测试。
2. Broker 与 UI 的 fake adapter/offscreen 集成测试。
3. SQLCipher/DPAPI 的 Windows 加密状态测试。
4. 已完成本机 Zig/CMake 默认禁用构建与 native CTest 3/3；CI 配置 MSVC 默认分支，远端执行结果另行记录。
5. 24 个合成 Context Envelope 的离线验证；显式 `--live` 才测 Jev 准确率。
6. 待补实现：QQ 认证传输/trusted adapter、微信具体 reader；待单独验收：具体 IM 客户端调用形态、真实 Profile 启动及异常退出恢复。

每一级的通过都不能替代下一层；最终状态记录在 `docs/implementation-status.md`。
