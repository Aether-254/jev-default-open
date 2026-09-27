# 实现与验证状态

更新日期：2026-09-27。此文件区分「代码存在」「自动化已覆盖」「真实集成已验证」，避免把架构计划当成交付证明。

## 可运行入口

| 入口 | 行为 | 不会发生的操作 |
| --- | --- | --- |
| `main.py --self-test` | 只读检查 Python、依赖、凭据存在性及原生产物；显式配置 `qq_runtime_path` 时读取 QQ.exe 版本/sha256 并报告 allow-list 匹配 | 不调用 Jev、不启动 Hook、不读取聊天/数据库；版本匹配也不等于 QQ adapter ready |
| `main.py --demo` | 合成数据驱动的离线确认 UI | 不调用 Jev、不安装 Hook、不启动真实 opener |
| `main.py [--config ...]` | 正常应用，保留引导后的手动启用入口；默认 native 构建拒绝启用 | 不自动提权、不自动启用 Hook |
| `scripts/evaluate.py` | 校验 24 个合成场景、八种 Scene 的结构与覆盖 | 不联网、不调用模型、不探查账号/聊天、不打开目标 |
| `scripts/evaluate.py --live` | 显式向 Jev 发送合成场景并生成评测报告 | 不访问真实聊天、不执行候选动作 |

缺少密钥/原生产物导致 `--self-test` 返回非零是诊断结果，不表示离线演示本身失败。`--live` 有 API 请求和费用；结果只适用于固定合成 fixture，不能替代真实用户场景验收。

## 模块状态

| 模块 | 当前交付 | 尚不能宣称 |
| --- | --- | --- |
| 领域/Broker | 类型、生命周期、限时决策、用户确认、偏好和回退路径；使用替身覆盖竞争/失败场景 | 全桌面真实事件下永不重复的现场保证 |
| Jev | typed Scene/Open Action 请求、响应校验、限时；精确缓存已接入 SQLCipher；MockTransport/fixture 测试 | 真实 API 延迟、准确率或当前服务兼容已通过 |
| 状态 | `sqlcipher3==0.6.2` 整库加密、DPAPI CurrentUser、历史/偏好；provenance 存取/恢复及启动、每日索引维护已接入 | 自动迁移旧明文数据、跨 Windows 用户恢复或已取得真实 IM 来源 |
| UI | Qt 托盘/批量确认、显式启停、Provider 数据清理、合成演示及 offscreen 测试 | 所有显示缩放/多显示器/实际 IM 窗口现场验收 |
| Actions | Windows 候选发现、大小写不敏感的扩展名/scheme 匹配、绝对 executable/Profile 路径校验、受控 argv 启动接口；候选应用子进程移除 Jev/API key 环境变量 | 当前机器每种应用/Profile 已真实启动验证；ShellExecuteW 系统回退没有环境块，不能宣称关联应用也完成凭据隔离 |
| 原生层 | Zig/CMake 默认禁用构建生成 Host、透传 Hook、原子 claim 三产物；CTest 3/3；显式 ON 在非 MSVC 工具链上会被配置门禁拒绝 | MSVC 显式 ON 实验分支已构建/验证，或可靠全局拦截已完成 |
| QQ | 目标/会话匹配、认证事件输入校验、显式 `createTrustedAdapter` / `createAuthenticatedTransport` 接口、OneBot 11 bounded parser 和合成 replay 测试；可选只读 `qq_runtime_path` 指纹与显式版本/SHA-256 allow-list；配置默认 `qq_bridge_mode=disabled` | 没有匹配当前 QQNT 的生产 transport/trusted adapter 或真实 QQ 聊天读取；NapCat v4.18.28 安装器检测本机 QQ 9.9.27/9.9.28 时下载目标 QQ 返回 HTTP 404，未写入 QQ 目录；runtime 指纹匹配不能进入普通聊天运行 |
| 微信 | Provider 和 `VerifiedVisibleContextReader` 接口，受控 reader fixture 测试；无具体 reader 实现 | 可自动读取当前客户端可见聊天，只差验收 |
| 飞书/Slack/钉钉 | 进程识别和明确返回不可用的接口骨架 | 官方 API 授权、消息关联和真实历史读取 |
| 构建/CI | 项目本地 bootstrap、失败码传播、Windows Python 3.12 和 MSVC CI 配置 | 本地编译即等于 GitHub 托管 CI 已执行成功 |

## 验证记录的解释

本轮最终集成检查使用以下命令；原生 Python 测试明确指向本次构建的 Host：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:JEV_NATIVE_TEST_HOST = (Resolve-Path '.\native\build\Release\native_host.exe').Path
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe .\scripts\evaluate.py
.\.venv\Scripts\python.exe .\main.py --self-test
.\.venv\Scripts\ctest.exe --test-dir .\native\build -C Release --output-on-failure
node --test qq_bridge/tests/protocol.test.js qq_bridge/tests/bridge.test.js
git diff --check
```

2026-09-27 最终本地结果：

| 检查 | 实际结果 |
| --- | --- |
| Python 完整测试（含 native test-mode、Qt offscreen、QQ replay、动作与凭据环境边界） | **335 passed，48.59 秒** |
| Ruff / `git diff --check` | 通过 |
| QQ Bridge Node 测试 | **29 passed**，仅替身与合成事件 |
| Native CTest | **3/3 passed**，不启用全局 Hook |
| 离线评测 fixture | 24 个合成场景通过结构与覆盖验证；准确率未测 |
| 正常入口 `--self-test` | 退出码 **1**；当前环境未配置 Jev key，默认原生构建禁用实验性 Hook，其余本地依赖/产物/协议检查通过 |

随后在用户明确授权下运行了一次在线合成评测，报告为
`artifacts/evaluation/evaluation-20260927T055526494376Z.json`：模型 `jev-1.13.0`，fixture
SHA-256 为 `56a643197c9025725661e1947f5ab931f7a453322f936e9bead21d3be94f17bc`，24/24
场景成功返回，Action top-1 与 Scene 准确率均为 **1.0**，错误数为 0，平均延迟
`3083.970 ms`，P95 `7332.147 ms`，Action 80% 门槛通过。该报告只包含合成场景标识、
预测结果和聚合指标，不包含 API key。这个结果不能外推到真实聊天、真实路径分布、其他模型
或生产网络延迟。

上述 Python 计数包含 1002 次历史插入的容量回归、实际临时 SQLCipher 数据库和 DPAPI
测试、清除期间的确定性并发回归，以及无真实启动的应用组合/离线演示测试。
没有运行真实 IM 采集、使用真实用户数据驱动的 Jev 决策或全局注入。未配置 `JEV_NATIVE_TEST_HOST` 的普通
`scripts/test.ps1` 会跳过依赖该显式路径的 Host 进程测试，不能与上述完整计数混淆。

- PowerShell 脚本已通过 Windows PowerShell 5.1 与 PowerShell 7 语法解析；外部命令非零退出的传播已用最小退出码测试验证。
- `.github/workflows/ci.yml` 配置了 Windows 离线检查与独立原生构建；仅提交 YAML 不代表远端工作流已运行。
- 默认检查不得要求真实 API 密钥或读取用户聊天，GUI 测试使用 `QT_QPA_PLATFORM=offscreen`。
- 原生 ownership CTest 只验证映射/原子竞争契约，不能充当全局注入实测。
- 本机已实际使用 Zig/CMake 默认 `JEV_ENABLE_EXPERIMENTAL_HOOK=OFF` 构建三个产物，CTest **3/3 通过**。默认 `open_hook.dll` 是禁用透传构建，Host 启用请求返回 `ERROR_NOT_SUPPORTED`。
- MSVC 显式 `ON` 实验分支本轮未构建、未验证。已存在捕获源文件不能替代该验证，也不能称可靠全局拦截已经交付。
- 已运行一次 24 场景合成 Jev 评测；上述指标仅证明该固定 fixture 在当次请求中通过，真实 IM、
  真实用户数据、其他模型和生产在线延迟仍未验收。

### 状态清除的并发边界

- Jev 使用请求 generation 防止清除前的 HTTP 请求/缓存读取回填旧结果；持久缓存
  namespace 包含模型、端点、Scene 定义、问题全文及 policy version。
- SQLCipher 在线程事务锁内校验 cache epoch；已排队的旧写入不能越过缓存失效。
  异步入口不等待该线程锁，避免阻塞 Broker 的 deadline 与事件循环。
- Provider 来源记录使用全局/Provider epoch；取消后仍在计算指纹的 worker 不能在
  清理完成后恢复已删除记录。清理后新的显式授权请求仍可产生新记录。
- `clean_shutdown` 运行标记不使模型缓存失效，持久缓存可跨正常重启复用；用户配置和
  偏好修改仍会失效。`clear_history` 不删除独立来源索引。
- 这些屏障作用于当前应用的存活 StateModule 实例，不承诺多个独立 Broker 进程并发管理
  同一数据库时的清除语义。
- Native protocol v2 的 offer ACK deadline 固定为 50 ms；配置字段仅为兼容旧配置而保留，
  其他数值会被拒绝，避免把未接线的运行时覆盖误认为已生效。

## 数据升级与恢复

旧明文 SQLite 文件会被拒绝且保留。不要删除旧文件来“修复”启动，也不要让程序自动创建明文后备库。先备份，再在配置中选择一个新的状态目录；已有加密数据库必须保留对应 DPAPI 保护密钥。同用户密钥丢失、不同 Windows 用户或数据库损坏也不会自动重置。

## 真实发布前的剩余验收

1. 实现并接线 QQ authenticated bridge transport 和当前版本 trusted adapter；实现微信具体 `VerifiedVisibleContextReader`。随后才能在独立测试账号中验证目标消息与会话关联。
2. 先构建并验证 MSVC 显式 ON 实验分支，再在受控桌面逐个记录 ShellExecute 来源、普通权限边界、透传条件、退出和紧急停用行为；默认 OFF 测试不能替代这一步。
3. 实际启动浏览器个人/工作 Profile、VS Code Profile 与 Excel，验证传参和失败后不重复启动。
4. 显式运行合成 Jev 评测，保存模型、fixture SHA-256、误差、P95 延迟与错误率；没有报告就不宣称达到模型指标。
5. 在准备承担高权限 IPC/启动隔离之前，维持不自动提权和手动启用策略。
