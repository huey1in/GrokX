# GrokX

基于 HTTP + gRPC-Web 纯协议实现的 x.ai / Grok 账号注册工具。

[![GitHub stars](https://img.shields.io/github/stars/huey1in/GrokX)](https://github.com/huey1in/GrokX/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/huey1in/GrokX)](https://github.com/huey1in/GrokX/network)
[![release](https://img.shields.io/badge/version-1.0.0-blue)]()
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![curl_cffi](https://img.shields.io/badge/curl_cffi-%3E%3D0.7-4B8BBE)]()
[![Node](https://img.shields.io/badge/Node.js-22.13%2B-339933?logo=node.js&logoColor=white)]()
<a href="https://linux.do"><img src="https://img.shields.io/badge/LINUX%20DO-社区-f0b752?style=flat-square" alt="LINUX
   DO"></a>

## 简介

`GrokX` 是一个纯协议实现的 x.ai（Grok）账号注册工具：通过 `curl_cffi` 模拟 Chrome TLS 指纹，直接向 x.ai 的 gRPC-Web 后端接口发送注册 RPC，无需启动浏览器。整合 MoeMail 临时邮箱、CapSolver 人机验证、Castle 反滥用令牌等服务，自动完成从创建邮箱到导出 SSO 会话凭据的整条链路，支持多线程批量注册。

## 工作原理

浏览器里完成的注册，在 `GrokX` 中被拆解为一组**协议级调用**：

```
你的机器 ── curl_cffi(Chrome 指纹) ──▶ https://accounts.x.ai
   │                                    │  /auth_mgmt.AuthManagement (gRPC-Web)
   ├─ MoeMail  ── 创建临时邮箱 / 收验证码 ─┘
   ├─ CapSolver ── 求解 Turnstile 人机验证  ┘
   └─ Castle ── 生成反滥用 Request Token ──┘
```

核心思路：

- **协议客户端**（`registration/protocol_client.py`）手写实现 protobuf 字段编码与 gRPC-Web 帧解码，向 `CreateEmailValidationCode` / `VerifyEmailValidationCode` / `CreateUserAndSessionV2` 等 RPC 发送请求。
- **TLS 指纹**（`network/fingerprint.py`）随机生成 Chrome 126–135 的浏览器指纹（User-Agent、`sec-ch-ua`、Accept-Language 等），并使用 `curl_cffi` 的 `impersonate="chrome"` 伪装。
- **反滥用链路**：发信与注册两个阶段各需要一个 Castle Request Token；默认通过官方 `@castleio/castle-js` SDK 在 Node（jsdom）中实时生成，一次进程调用批量产出两个 token。

## 注册流程

一条完整的注册共 **11 个阶段**：

```text
1. 初始化注册任务
2. 建立协议会话           bootstrap 注册页
3. 创建临时邮箱           MoeMail 生成一次性收件箱
4. 生成邮件阶段 Castle Token
5. 发送邮箱验证码         CreateEmailValidationCode RPC
6. 获取邮箱验证码         轮询 MoeMail 收件箱并提取
7. 确认邮箱验证码         VerifyEmailValidationCode RPC
8. 完成人机验证           CapSolver 求解 Turnstile
9. 生成注册阶段 Castle Token
10. 提交账号注册请求       CreateUserAndSessionV2 RPC
11. 获取 SSO 凭据          从响应 Cookie / 消息中提取 sso
```

## 项目结构

```text
GrokX/
├── main.py                        # CLI 根入口
├── registration/
│   ├── cli.py                     # 命令行：并发、进度、结果落盘
│   ├── flow.py                    # 注册状态机（11 阶段编排）
│   └── protocol_client.py         # gRPC-Web 协议客户端（protobuf 编解码 + RPC）
├── providers/
│   ├── castle.py                  # Castle 令牌提供者 + MoeMail 适配
│   ├── castle_sdk/                # Node 子包：用官方 Castle JS SDK 生成 token
│   │   ├── mint.mjs
│   │   └── package.json
│   ├── capsolver.py               # CapSolver 求解 Turnstile
│   ├── mail.py                    # MoeMail OpenAPI 客户端
│   └── turnstile_flow.py          # 挑战上下文 / 已获取令牌模型
├── network/
│   ├── fingerprint.py             # Chrome 指纹生成（参数已硬编码为常量）
│   └── proxy.py                   # 代理 URL 解析 / 归一化 / 脱敏
├── config/
│   └── loader.py                  # .env 加载
├── tests/                         # 单元测试
└── output/                        # 注册结果（gitignored）
```

## 环境要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | ≥ 3.11 | 运行环境 |
| [curl_cffi](https://pypi.org/project/curl_cffi/) | ≥ 0.7 | Chrome TLS 指纹 HTTP 客户端 |
| Node.js | ≥ 22.13 | 运行 Castle SDK（生成反滥用 token） |
| [uv](https://docs.astral.sh/uv/)（可选） | — | 依赖管理 |

## 安装

```bash
# 1. Python 依赖
uv sync              # 或：pip install "curl_cffi>=0.7"

# 2. Castle SDK 的 Node 依赖
cd providers/castle_sdk && npm ci && cd ../..
```

## 配置

复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
```

| 变量 | 必填 | 说明 |
|------|:----:|------|
| `MOEMAIL_API_BASE` / `MOEMAIL_API_KEY` | ✅ | MoeMail 临时邮箱服务 |
| `CAPSOLVER_API_KEY` | ✅ | CapSolver 人机验证 |
| `PROXY` / `PROXY_ENABLED` | ❌ | 代理（`socks5://user:pass@host:port` 等；留空则直连） |
| `PROTOCOL_PAGE_URL` | ❌ | 注册页地址（默认 `https://accounts.x.ai/sign-up`） |
| `PROTOCOL_TURNSTILE_ACTION` / `PROTOCOL_TOS_ACCEPTED_VERSION` | ❌ | Turnstile action / ToS 版本 |
| `MOEMAIL_DOMAIN` / `MOEMAIL_EXPIRY_TIME` / `MOEMAIL_USE_PROXY` | ❌ | MoeMail 细节 |
| `CAPSOLVER_TIMEOUT_SEC` / `CAPSOLVER_POLL_INTERVAL_SEC` | ❌ | 人机验证超时/轮询 |
| `CASTLE_PROVIDER_URL` / `CASTLE_PROVIDER_KEY` | ❌ | 可选：改用远程 Castle token 供应服务 |
| `CASTLE_EMAIL_TOKEN` / `CASTLE_FINAL_TOKEN` | ❌ | 可选：直接使用静态 token（跳过 SDK） |
| `DEFAULT_DOMAINS` | ❌ | 默认邮箱域名（`config.loader` 使用） |

> `PROTOCOL_TURNSTILE_SITEKEY`、`PROTOCOL_CASTLE_PUBLISHABLE_KEY`、浏览器指纹参数均已**硬编码在代码中**（`registration/cli.py`、`network/fingerprint.py`），无需配置。

## 使用

```bash
# 注册 1 个账号
python main.py

# 批量注册 10 个，4 线程并发
python main.py -n 10 -j 4

# 只检查配置是否齐全，不发请求
python main.py --check

# 测试代理连通性
python main.py --proxy-check

# JSONL 事件输出（便于外部集成/日志收集）
python main.py --events

# 自定义结果文件路径
python main.py --output-json path/to/result.json
```

命令行参数：

| 参数 | 说明 |
|------|------|
| `--env <path>` | `.env` 路径（默认项目根目录 `.env`） |
| `-n, --count <n>` | 注册数量（默认 1） |
| `-j, --jobs <n>` | 并发任务数（默认 1，受 `-n` 约束） |
| `--check` | 检查必需配置，退出 0/2 |
| `--proxy-check` | 代理连通性测试 |
| `--events` | 输出 JSONL 进度事件（不输出人类可读日志） |
| `--output-json <path>` | 结果文件路径（默认 `output/web_register_result.json`） |

### 输出

每个成功注册的账号写入：

- **`output/web_register_result.json`** — 追加式数组，每条含：

  ```json
  {
    "created_at": "2026-08-16T14:57:34.575846+00:00",
    "email": "sj8x2x71ukht@91dick.com",
    "password": "N!UnGjzTv5wSnlezOQGl#7",
    "sso": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
  }
  ```

- **`output/web_register_result.txt`** — 纯 SSO token，每行一个，便于直接导入。

## 测试

```bash
python -m unittest discover tests
```

## 贡献指南

欢迎提交 PR 与 Issue。请遵循以下约定：

1. **分支**：从 `main` 拉取独立功能分支（`feature/xxx` 或 `fix/xxx`），不要直接往 `main` 提交。
2. **风格**：与现有代码保持一致——中文注释、模块级 `from __future__ import annotations`、函数自带类型标注。
3. **协议改动**：涉及 `registration/protocol_client.py` 的字段编码或 RPC 方法时，务必对照抓包的 protobuf 字段号，改动需可回放验证。
4. **测试**：提交前确保 `python -m unittest discover tests` 全部通过；新增逻辑尽量补充单元测试。
5. **提交信息**：简洁描述改动，遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `refactor:`）。
6. **PR 说明**：说明改动动机、验证方式；不要提交 `.env`、`output/` 等敏感或生成文件。
