# Rules: src/progress_tracker/bot_api/

## Bot API endpoint + SOCKS5 routing (milestone 2.5 onward)

`build_bot()` inspects `BOT_API_URL` (custom `telegram-bot-api` server,
empty = cloud) and `SOCKS_PROXY_URL` (`socks5://user:pass@host:port`,
None = direct) and swaps in `SocksAiohttpSession` (extends aiogram's
`AiohttpSession`, replaces the default `TCPConnector` with
`aiohttp_socks.ProxyConnector.from_url`) when a proxy is set. aiohttp does
not support SOCKS5 natively — keep the custom session. When switching a
bot from cloud api.telegram.org to a custom server, the operator must run
`curl "https://api.telegram.org/bot<TOKEN>/logOut"` once before the first
connection.

## Two file-fetcher modes for ingest (`bot_api/fetcher.py`)

`RemoteFileFetcher` is the dev-from-home path: `bot.get_file` →
`normalize_remote_file_path` → `bot.download_file` over HTTPS, then
`DeleteFile` to ask the remote bot-api to drop its copy. `LocalFileFetcher`
is the co-located VDS path: `bot.get_file` → `validate_local_file_path` →
`shutil.copyfile` from a bind-mounted bot-api data dir; cleanup is a no-op
because per user direction source files persist on the VDS indefinitely.
Selection happens at startup via `BOT_API_LOCAL_FILES`. Two compose files
reflect the split: the default `docker-compose.yml` is the **production**
VDS stack (bot-app + telegram-bot-api + postgres on a shared docker
network) so a dumb cron CD can `git pull && docker compose up -d --build`;
`docker-compose.dev.yml` is the dev-from-home stack (bot + db only,
SOCKS+BasicAuth path against cloud Telegram). Don't drop the SOCKS+BasicAuth
code paths — dev still uses them.
