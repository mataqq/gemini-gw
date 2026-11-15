# 手机接入（免梯子）

## 0. 你上次「五次只成一次」的定位结论

网关这边已经用三组复现把责任排除了：

- 连续 20 次 `/healthz`：20/20 成功（0.85–1.08s）
- 连续 6 次非流式对话：6/6 `200`（1.5–1.6s，`len=288`）
- 5 路并发：5/5 `200`（1.3–1.6s）；5 次流式：5/5 正常出 SSE 首块
- TLS 握手 8/8 成功（0.52–0.56s）

所以问题在手机那侧或手机网络，几乎都是这三件事之一：**超时设置太短**、**没开流式**、**模型名写错触发降级链**。下面第 6 节是对应的改法。

网关这边已经按这个结论改好了（2026-11-15 实测）：

- 流式请求**首字节 0.81 秒**必到（之前是 4~7 秒才开始出字节，短超时的 App 直接判死）
- 上游思考期间每 3 秒发一次心跳包，客户端 3 秒 socket 超时也不会断
- 上游真挂了会在流里直说 `[relay-error] ...`，不再静默失败

**所以现在只要你把「流式」打开，超时 30 秒都不用改就能稳**（改成 ≥120 秒更保险）。

### 0.1 一直 502 的真正原因：上游配额 429（已修）

远程看了失败记录，你手机那 21 条 502 全是同一个样子：

- 你的 App 发的是 `model: gemini-3.8-flash-tiered`，上游回 **429 RESOURCE_EXHAUSTED**（免费额度按项目共享，连点重试会一次打空）
- 旧版降级链撞 429 就跳下一个，整条链全 429 → 网关报 502
- 免费项目内所有模型共用同一份每分钟配额，所以「换个模型」并不能救

现在的改法（2026-11-15 已上线）：

- 全局并发闸门：同时最多 2 路上游请求、最小间隔 0.35s，不再自己把配额打空
- 撞到 429 就地退避重试（1s / 2.2s / 4s，单模型最多 9 秒）再换下一个模型
- 某个模型刚 429 过就凉它 45 秒，降级链自动把它排到最后
- 真全耗尽返回 **429 + `Retry-After: 5`**（不再是 502）；流式里会写一句 `[relay-retry] 上游配额用尽（429），等几秒再发一次即可`
- 实测：8 路并发全打 `gemini-3.8-flash-tiered` → **8/8 `200`**，0 个 429、0 个 502

看到 429 或那句 `[relay-retry]` 就等几秒再发，别连点。

### 0.2 工具调用（agentic）已修好（含 Cline / 手机 Agent）

之前「第一轮能出工具调用、第二轮就 502」是上游对 **thought_signature** 的强制校验造成的：

- Gemini 的 `functionCall` 必须带回上一轮响应里下发的 `thoughtSignature`（**放在 part 层的兄弟字段**，不是塞进 `functionCall` 里面）
- 旧版网关把它丢掉 → 上游回 `400 Function call is missing a thought_signature…` → 整条降级链全 400 → 502

现在的改法（2026-11-15 已上线并实测）：

- 上游下发时按 `tool_call.id` 缓存签名（同时按 `函数名+参数` 兜底），上限 600 条
- 回放历史时按 id 取回签名并挂在 part 层；取不到就用 `skip_thought_signature_validator` 哨兵（上游同样接受）
- 空 `thought: true` 片段不再混进正文
- 撞到 `Invalid JSON payload` / `thought_signature` 属硬错误，不再把整条链打空

实测（`/v1/chat/completions`，3 轮工具循环，流式与非流式各跑一遍）：

```
r1 -> tool_calls [('list_dir', '{"path": "/etc"}')]
r2 -> tool_calls [('read_file', '{"path": "/etc/hostname"}')]
r3 -> FINAL TEXT: '主机名是：cpuniverse-host'
```

所以 Cline / Antigravity / 任何要「读文件→改文件→再读」的 agent，现在都能正常走完多轮。

## 1. 取当前地址（每次开机先做这一件事）

地址每 6 小时自动换一次，所以先读这一个固定链接，它永远指向当前可用的地址：

```
https://gh-proxy.com/https://raw.githubusercontent.com/mataqq/gemini-gw/main/LIVE_URL.txt
```

备用（内容一样）：

```
https://cdn.jsdelivr.net/gh/mataqq/gemini-gw@main/LIVE_URL.txt
```

打开后里面可能是**一行，也可能是两三行**，每行一个地址，例如：

```
https://xxxx-xxxx.trycloudflare.com
https://yyyy-yyyy.lhr.life
```

**第一行是主入口，后面的是备用入口**（走的是不同域名，某一条被运营商卡住时换另一条）。挑第一个能打开的用就行。

## 1.5 本机固定地址（Cline / Antigravity IDE 专用）

手机端只能读 LIVE_URL.txt 拿「当前」地址；本机上更省事的做法是用固定地址，永不变：

| 项目 | 值 |
|---|---|
| API Base / Base URL | **http://127.0.0.1:8787/v1** |
| API Key | `sk-gw-dev-9527` |
| Model | `gemini-2.5-flash` |

这是本机 shim（`gemini-gw\local_shim.py`）常驻在 127.0.0.1:8787 的入口：

- 每次请求前按 LIVE_URL.txt（gh-proxy + jsDelivr 双镜像，带时间戳穿透 CDN 缓存）拉最新隧道
- 对所有候选地址做 `/healthz` 探活，只挑真的活着的那条；缓存 45 秒
- 撞到 `502/503/504/520-530` 或连接异常 → 立刻重新探活换隧道并重放一次（实测自愈 3.85 秒）
- 计划任务 `RelayShimWatch`（开机 / 登录 / 每 3 分钟）发现 8787 不健康就自动重启

所以 Cline 里那条 `http://127.0.0.1:8787/v1` 可以永久不换，Actions 换隧道也不用改配置。

实测（2026-11-15，经 8787）：

```
sequential    20/20 ok  codes={'200': 20}  p50=1.83s max=7.15s
6-concurrent   6/6  ok  codes={'200': 6}   p50=1.71s max=3.29s
stream         4/4  ok  codes={'200': 4}   p50=1.79s max=1.89s
```
## 2. 填进客户端

任意支持 OpenAI 兼容接口的手机 App（ChatBox / NextChat / OpenCat / LobeChat 等）：

| 项目 | 值 |
|---|---|
| API Base / Base URL | 第 1 步拿到的地址，后面不要加 /v1 |
| API Key | `sk-gw-dev-9527` |
| Model | `gemini-2.5-flash` |
| 超时 / Timeout | **≥ 120 秒**（很多 App 默认 30s，这是五次只成一次的头号原因） |
| 流式 / Stream | **打开**（若关着就设 超时 ≥ 180 秒） |

Base URL 填 `https://xxxx.trycloudflare.com`，客户端会自己拼 `/v1/chat/completions`。
如果客户端要求填完整路径，就填 `https://xxxx.trycloudflare.com/v1`。
网关也容忍多写一层 `/v1`（`/v1/v1/chat/completions`）和尾部斜杠，写歪了不会 404。

## 3. 怎么确认拿到的地址是好的

浏览器打开 `https://地址/healthz`，看到下面这行就说明网关活着，可以直接用：

```
{"ok": true, "service": "gemini-relay", "model": "gemini-2.5-flash", "client": "antigravity"}
```

接口层面也全部实测通过：

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

**慢模型清单**（手机超时短的时候别用，它们单次要 5~8 秒甚至更久）：
`gemini-3.8-flash-tiered`（5.9~7.5s）、`claude-opus-5-5-high`（6.1s）、`claude-sonnet-5-5-high`（5.1s）。

## 5. 没有梯子也能用的原因

手机 → 网关：普通 HTTP，不出境，不需要任何代理。
网关 → Google：在 GitHub 的美国服务器上跑，本来就在墙外。
所以手机全程不需要 VPN，也不需要切流量。

## 6. 五次只成一次 / 卡住 / 转圈不出字 —— 按顺序改这四条

1. **打开流式（stream）—— 这条最关键**。网关已经改成 0.8 秒内必回首字节、思考期间每 3 秒一次心跳，流式下 30 秒超时的 App 也能稳定拿到回答。
2. **超时最好还是改到 ≥ 120 秒**。非流式（stream 关掉）只能干等整段生成，慢模型要 5~8 秒、排队时更久，30s 默认值下关流式就必然丢。
3. **模型写 `gemini-2.5-flash` 或干脆留空**。写错名字会先打到上游 404，再顺着降级链往下试，总耗时翻几倍，正好撞上超时。
4. **失败先开浏览器 `https://当前地址/healthz`**：能出 `"ok": true` 就是手机客户端配置问题；打不开就是隧道换了，回第 1 步重读链接、或换备用行那个地址。
5. **看到 429 或 `[relay-retry]` 先停几秒**。这是免费额度打满，网关已在自动退避重试；连点只会让配额更久不恢复。急用就切 `gemini-2.5-flash`（最省额度，1~2 秒回）。

另外两个常见误判：

- 显示 `530` / `1033` / 空白页 = 这次运行的隧道没起来（每 6 小时切换时有约 8~10 分钟空窗），等几分钟重读固定链接。
- 一直 401 = Key 写错了（注意不要带多余空格，值就是 `sk-gw-dev-9527`）。

## 7. 我来远程看你的失败记录

网关现在会把每一次请求的结果（路径、模型、状态码、耗时、User-Agent、错误原文）留在内存里，并自动公布到：

```
https://gh-proxy.com/https://raw.githubusercontent.com/mataqq/gemini-gw/main/REC.json
```

里面 `bad` 是失败条数，`bad_items` 是最近失败的明细，`uagents` 能看到是哪个 App 打进来的。
你复现一次「不出字」，把这行链接发我，我直接看是超时、401 还是模型名错。

## 8. 地址打不开时

1. 重新读第 1 步的固定链接，确认是不是又换地址了（可以试第二行那个备用地址）。
2. 打开 `https://当前地址/healthz`，看到 `"ok": true` 说明网关活着。
3. 看到 `530` / `1033` / 空白页 = 这次运行的隧道没起来，等 3~5 分钟后重读固定链接。
4. `/healthz` 里 `"client"` 不是 `antigravity` 或返回 `invalid_grant` = 账号授权过期，找我换。
