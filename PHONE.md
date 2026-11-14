# 手机接入（免梯子）

## 1. 取当前地址（每次开机先做这一件事）

地址每 6 小时自动换一次，所以先读这一个固定链接，它永远指向当前可用的地址：

```
https://gh-proxy.com/https://raw.githubusercontent.com/mataqq/gemini-gw/main/LIVE_URL.txt
```

备用（内容一样）：

```
https://cdn.jsdelivr.net/gh/mataqq/gemini-gw@main/LIVE_URL.txt
```

打开后里面就一行，形如 `https://xxxx-xxxx.trycloudflare.com`，把它复制下来。

## 2. 填进客户端

任意支持 OpenAI 兼容接口的手机 App（ChatBox / NextChat / OpenCat / LobeChat 等）：

| 项目 | 值 |
|---|---|
| API Base / Base URL | 第 1 步拿到的地址，后面不要加 /v1 |
| API Key | `sk-gw-dev-9527` |
| Model | `gemini-2.5-flash` |

Base URL 填 `https://xxxx.trycloudflare.com`，客户端会自己拼 `/v1/chat/completions`。
如果客户端要求填完整路径，就填 `https://xxxx.trycloudflare.com/v1`。

## 3. 现在这条地址已验证可用

当前：`https://hiking-enclosed-purse-mercy.trycloudflare.com`

- `/healthz` → `{"ok": true, "service": "gemini-relay", "model": "gemini-2.5-flash", "client": "antigravity"}`
- `/v1/models` → 返回 `gemini-2.5-flash`
- `POST /v1/chat/completions` → `200`，正常返回内容
- 流式（`"stream": true`）→ 正常 SSE 增量输出

## 4. 模型

默认 `gemini-2.5-flash`。以下都在你这个账号上实测 `200` 可用：

`gemini-2.5-flash`、`gemini-2.5-flash-thinking`、`gemini-2.5-flash-lite`、
`gemini-3-flash`、`gemini-3.1-flash-lite`、`gemini-3.5-flash-lite`、
`claude-opus-5-5-medium`、`claude-sonnet-5-5-medium`、`gpt-oss-120b-medium`

`gemini-2.5-pro` 在这台服务器上没容量（上游 503 MODEL_CAPACITY），
所以网关会自动降级到 `gemini-2.5-flash` 而不是报错 —— 你写 pro 也能正常用。

## 5. 没有梯子也能用的原因

手机 → 网关：普通 HTTP，不出境，不需要任何代理。
网关 → Google：在 GitHub 的美国服务器上跑，本来就在墙外。
所以手机全程不需要 VPN，也不需要切流量。

## 6. 地址打不开时

1. 重新读第 1 步的固定链接，确认是不是又换地址了。
2. 打开 `https://当前地址/healthz`，看到 `"ok": true` 说明网关活着。
3. 看到 `530` / `1033` / 空白页 = 这次运行的隧道没起来，等 3~5 分钟后重读固定链接。
4. `/healthz` 里 `"client"` 不是 `antigravity` 或返回 `invalid_grant` = 账号授权过期，找我换。
