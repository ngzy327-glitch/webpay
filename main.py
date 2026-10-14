import asyncio
import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

logging.basicConfig(level=logging.CRITICAL)
for name in logging.root.manager.loggerDict:
    logging.getLogger(name).setLevel(logging.CRITICAL)
logging.getLogger('telethon').setLevel(logging.CRITICAL)

# ========== 环境变量 ==========
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSIONS_STR = os.environ.get("SESSIONS") or os.environ.get("SESSION_STRING", "")
SESSIONS = [s.strip() for s in SESSIONS_STR.split("|") if s.strip()]
TARGET_BOT = os.environ.get("TARGET_BOT", "flqb").lstrip('@')
# =============================

if not all([API_ID, API_HASH, SESSIONS]):
    print("❌ 缺少 API_ID / API_HASH / SESSIONS")
    exit(1)

# 按钮关键词
CLAIM_KEYWORDS = ['领取红包', '🧧', '领取', '领红包', '抢红包', '点我领取']

def is_claim(text: str) -> bool:
    return any(k in text for k in CLAIM_KEYWORDS)


# ================== 健康检查服务器（防 Railway 18 秒杀容器） ==================
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    except Exception:
        pass


# ================== 单账号运行 ==================
async def run_account(session: str, idx: int):
    client = TelegramClient(
        StringSession(session),
        API_ID, API_HASH,
        connection_retries=999,
        retry_delay=0,
        auto_reconnect=True,
        request_retries=5,
        flood_sleep_threshold=86400,
        sequential_updates=False,
    )

    async def handler(event):
        markup = event.message.reply_markup
        if not markup:
            return
        for i, row in enumerate(markup.rows):
            for j, btn in enumerate(row.buttons):
                txt = btn.text or ''
                if is_claim(txt):
                    try:
                        await event.click(i=i, j=j)
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                    except Exception:
                        pass
                    return

    try:
        await client.start(phone=lambda: "")
        entity = await client.get_entity(TARGET_BOT)
        target_id = entity.id

        client.add_event_handler(
            handler,
            events.NewMessage(from_users=target_id, incoming=True)
        )
        client.add_event_handler(
            handler,
            events.MessageEdited(from_users=target_id, incoming=True)
        )

        await client.run_until_disconnected()
    except Exception:
        pass


async def main():
    start_health_server()
    tasks = [run_account(s, i) for i, s in enumerate(SESSIONS)]
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
