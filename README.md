# Jev Default Open

Windows 上下文感知的「打开方式」实验项目。目标是结合文件路径、来源应用、聊天场景以及用户标注的个人/工作 Profile，让 Jev 推荐一个 **Open Action（应用 + 可选 Profile）**，经用户确认后打开目标。

**当前版本是可测试的开发实现，真实 IM 接线和可靠全局拦截尚未完成。** Python 决策链、离线演示、加密状态和原生协议有独立测试；默认原生构建禁用注入，QQ/微信缺少可用的生产适配接线，不是只差现场验收。具体范围见 `docs/implementation-status.md`。

## 快速开始

使用 **Windows x64 + Python 3.12 x64**。默认准备过程只创建/更新本项目 `.venv`，安装开发依赖并运行检查，不安装全局工具、不提权、不启用 Hook。

```powershell
Set-Location 'D:\桌面\jev-default-open'
.\scripts\bootstrap.ps1

# 无网络、无 Hook、无真实文件打开的合成 UI 演示
.\.venv\Scripts\python.exe .\main.py --demo

# 只读诊断；缺少密钥或原生产物时返回非零，不表示离线演示不可用
.\.venv\Scripts\python.exe .\main.py --self-test
```

没有 `py -3.12` 时，显式传入解释器：

```powershell
.\scripts\bootstrap.ps1 -Python 'C:\Python312\python.exe'
```

原生编译与系统工具链安装是分开的显式操作：

```powershell
# 已安装 CMake 与 C++ 编译器时
.\scripts\build-native.ps1

# 仅明确需要安装系统级 VS 2022 C++ Build Tools/CMake 时；可能触发 UAC
.\scripts\bootstrap.ps1 -InstallToolchain -BuildNative
```

`-InstallToolchain` 会调用 winget 并接受对应包/源协议；可能需要较大下载。不会自动安装 Python、QQ 插件或 IM 数据库工具。脚本不会自行启用 Hook 或运行客户端集成测试。

原生构建默认 `JEV_ENABLE_EXPERIMENTAL_HOOK=OFF`：`open_hook.dll` 是禁用透传构建，Host 拒绝 `start_hook` 并返回 `ERROR_NOT_SUPPORTED`。本机已用 Zig/CMake 构建三个产物，CTest **3/3 通过**；这验证的是默认关闭分支。只有 MSVC 支持显式 `-EnableExperimentalHook`/CMake `ON`，该实验分支本轮**未构建、未验证**，不作为可用全局拦截功能交付。

## 正常启动与配置

```powershell
.\scripts\run.ps1
.\scripts\run.ps1 -Config 'C:\JevConfig\config.json'
# 等价入口
.\.venv\Scripts\python.exe .\main.py --config 'C:\JevConfig\config.json'
```

- 正常启动时 Hook 关闭，UI 保留引导/检查后的手动启用入口；默认禁用构建会明确拒绝启用。
- 不自动请求管理员权限。后续实验的目标边界为同一 Windows 用户、普通完整性级别和指定的传统 x64 桌面进程；当前未交付已验证的桌面拦截覆盖。
- `TYPESAFE_API_KEY` 从环境变量读取；不要把密钥写进仓库、配置、命令参数或截图。`TYPESAFE_MODEL` 与 `TYPESAFE_BASE_URL` 可覆盖模型和 API 地址。
- 默认配置位于 `%LOCALAPPDATA%\JevDefaultOpen\config.json`；显式 `--config` 用于覆盖配置。配置字段以 `src/jev_open/config.py` 为准。
- IM Provider 默认关闭；QQ 的 `qq_bridge_mode` 默认是 `disabled`，仅 `fixture` 模式可用于离线回放。`qq_bridge/src/integration.js` 的 authenticated transport/trusted adapter 工厂只定义接线边界；`src/jev_open/context/providers/qq_onebot.py` 已提供经过 HMAC、source PID、目标锚定和 10 条上限校验的 OneBot 11 事件解析，但仍需要匹配 QQ 版本的 NapCat/OneBot transport。QQ runtime 指纹只做显式版本/SHA-256 allow-list 门禁，不会启用聊天读取。微信没有具体 `VerifiedVisibleContextReader`，启用配置不会自动补齐这些实现。
- 可选的 `qq_runtime_path` 只在显式配置时读取本机 `QQ.exe` 的 SHA-256 和 Windows VERSIONINFO；`qq_allowed_versions` / `qq_allowed_sha256` 必须显式匹配，诊断只报告版本指纹，不会因此启用 QQ 聊天 adapter、named pipe 或 Hook。
- 浏览器/编辑器 Profile 的个人、工作等含义由用户标注，不自动推断账号归属。

配置的 `Open Action` 只接受绝对 `.exe` 路径；参数必须是独立 argv 模板并且恰好包含一个
`{target}`，不会经过 `cmd.exe` 或其他 shell。浏览器 Profile 的目录必须是绝对路径，发现时
拒绝符号链接，执行前还会重新确认目录仍存在；缺失或变更的 Profile 会失败关闭，不会自动
创建目录。文件扩展名与 URL scheme 匹配不区分大小写，ShellExecute/关联处理器的 verb 只允许
`open` 和 `edit`。候选应用和 native host 子进程会收到移除 Jev/API key 及常见 token/secret
变量的环境副本。系统回退仍使用 Windows `ShellExecuteW`；该 API 没有可传入的环境块参数，
因此无法为关联应用提供同样的环境隔离，当前实现不会把这条限制描述成已解决。

## 数据与边界

启用真实 Jev 决策后，当前请求使用的完整目标及已取得的聊天摘录可能发送给配置的 Jev API，并保存在本地加密历史中。不要用私人会话做初次联调。

状态使用 **SQLCipher（`sqlcipher3==0.6.2`）整库加密**，随机数据库密钥由 **DPAPI CurrentUser** 保护。默认数据库位于 `%LOCALAPPDATA%\JevDefaultOpen\state.db`，最多保留最近 1000 条历史。密钥丢失、用户不匹配或旧明文 SQLite 文件都会阻止打开；程序不会静默删除、重置或降级到明文。旧数据需保留，并显式选用新的状态目录。JSON 导出是明文，需要用户确认。

精确决策缓存已接入 SQLCipher。文件来源索引（provenance）的存取/恢复、启动时和每日维护以及 Provider 数据清理 UI 也已接入；这些能力仍需由可靠的 IM 适配器提供来源事件，不能自行取得真实聊天。

QQ/微信目前提供经过 fixture 验证的关联、校验和失败关闭逻辑，**真实聊天读取还缺生产接线**：QQ 的 OneBot 解析器只接收可信 transport 交给它的事件，当前机器的 NapCat `v4.18.28` 安装器因目标 QQ 版本下载返回 HTTP 404 而未安装；不能把解析器测试写成真实聊天已接通。微信的 reader 是待实现接口。飞书、Slack、钉钉仅有识别/接口骨架。32 位、受保护进程、AppContainer、应用内部导航和非 ShellExecute 路径不在承诺范围内。

## 检查与评测

```powershell
.\scripts\test.ps1
.\.venv\Scripts\python.exe .\scripts\evaluate.py
ctest --test-dir .\native\build -C Release --output-on-failure
```

`test.ps1` 在 offscreen 模式运行 Ruff 和 pytest。评测脚本默认只验证 24 个合成场景的结构/覆盖，不调用模型、不打开目标，**不能据此宣称达到 80% 准确率**。需要测量 Jev 时显式运行以下命令；它只发送合成数据，但产生网络请求和 API 费用：

```powershell
.\.venv\Scripts\python.exe .\scripts\evaluate.py --live --timeout-seconds 30
```

CI 配置为 Windows Python 3.12 lint/离线测试，并独立编译 MSVC x64 的默认禁用分支、运行无全局注入的 CTest。已在用户明确授权下运行一次 24 个合成场景的 Jev `jev-1.13.0` 评测：Action 与 Scene 准确率均为 100%，平均延迟 3083.970 ms，P95 延迟 7332.147 ms，24/24 请求成功。该结果只代表固定 synthetic fixture，不代表真实 IM、真实用户数据、其他模型或生产网络延迟；实验 Hook 和全桌面覆盖仍不属于自动化通过的含义。

## 导航

- `CONTEXT.md`：领域词汇。
- `docs/architecture.md`：模块、数据流和失效边界。
- `docs/implementation-status.md`：交付/验证状态与剩余验收。
- `docs/adr/`：重要架构取舍。

不要提交 API 密钥、IM 数据库、数据库密钥、真实聊天 fixture、日志或历史导出。
