# gemini-gw

国内手机直连 Gemini（Antigravity 账号），全程不需要 VPN。

- 接入说明：[PHONE.md](PHONE.md)
- 当前地址（固定链接，永远指向最新）：
  https://gh-proxy.com/https://raw.githubusercontent.com/mataqq/gemini-gw/main/LIVE_URL.txt
- 运行方式：GitHub Actions 上跑 `serve.sh`（relay + cloudflared quick tunnel），每 6 小时 cron 续一次。
- Key：`sk-gw-dev-9527`，默认模型 `gemini-2.5-flash`。

## 已验证（2026-11-15）

| 项目 | 结果 |
|---|---|
| 普通对话 | 20/20 `200`，p50 1.83s |
| 6 路并发 | 6/6 `200`，p50 1.71s |
| 流式 SSE | 4/4 `200`，首包 < 2s |
| 多轮工具调用（agentic） | 3 轮「列目录 → 读文件 → 出结论」全 `200`，流式/非流式各一遍 |
| 隧道轮换自愈 | 把缓存污染成死隧道后 3.85s 换回活的 |
| 守护 | `RelayShimWatch` 杀掉进程后自动拉起 |

## 本机固定入口

`http://127.0.0.1:8787/v1` —— 本机 shim（`local_shim.py`）常驻，自动跟随 Actions 换的隧道，
由计划任务 `RelayShimWatch` 守护。Cline / Antigravity 的 Base URL 填它就不会再因为隧道轮换而失效。

## 工具调用（thought_signature）

Gemini 要求 `functionCall` 带回上轮下发的 `thoughtSignature`（part 层的兄弟字段）。
网关按 `tool_call.id` 缓存签名、回放时挂回，取不到用 `skip_thought_signature_validator` 哨兵；
不再出现「第一轮出工具调用、第二轮 502」。
