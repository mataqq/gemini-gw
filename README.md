# gemini-gw

国内手机直连 Gemini（Antigravity 账号），全程不需要 VPN。

- 接入说明：[PHONE.md](PHONE.md)
- 当前地址（固定链接，永远指向最新）：
  https://gh-proxy.com/https://raw.githubusercontent.com/mataqq/gemini-gw/main/LIVE_URL.txt
- 运行方式：GitHub Actions 上跑 `serve.sh`（relay + cloudflared quick tunnel），每 6 小时 cron 续一次。
- Key：`sk-gw-dev-9527`，默认模型 `gemini-2.5-flash`。
