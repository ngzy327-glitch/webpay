import asyncio
import os
import re
import logging
import threading
import urllib.parse
import urllib.request
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

# 多机器人监听（逗号分隔，不带 @）
TARGET_BOTS_STR = os.environ.get("TARGET_BOTS", "fllqb")
TARGET_BOTS = [b.strip().lstrip('@') for b in TARGET_BOTS_STR.split(",") if b.strip()]

# 多群组监听（逗号分隔，负整数）
TARGET_GROUPS_STR = os.environ.get("TARGET_GROUPS", "")
TARGET_GROUPS = [int(x.strip()) for x in TARGET_GROUPS_STR.split(",") if x.strip()]

# 通知 Bot
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MY_CHAT_ID = int(os.environ.get("MY_CHAT_ID", 0))
# =============================

if not all([API_ID, API_HASH, SESSIONS]):
    print("❌ 缺少 API_ID / API_HASH / SESSIONS")
    exit(1)

# 只点含这些关键词的按钮
CLAIM_KEYWORDS = ['领取红包', '🧧', '领取', '红包', '领红包', '抢红包', '点我领取']

def is_claim(text: str) -> bool:
    return any(k in text for k in CLAIM_KEYWORDS)

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00a0]', '', text).strip()


# ================== 健康检查服务器 ==================
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


# ================== 通知 ==================
async def notify(msg: str):
    if not BOT_TOKEN or not MY_CHAT_ID:
        return
    try:
        safe = urllib.parse.quote(msg)
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={MY_CHAT_ID}&text={safe}"
        await asyncio.to_thread(lambda: urllib.request.urlopen(url, timeout=5).read())
    except Exception:
        pass


# ================== 点击逻辑 ==================
async def click_claim_buttons(event, idx: int, source: str):
    markup = event.message.reply_markup
    if not markup:
        return

    for i, row in enumerate(markup.rows):
        for j, btn in enumerate(row.buttons):
            if getattr(btn, 'url', None):
                continue
            txt = clean_text(btn.text or '')
            if not is_claim(txt):
                continue

            try:
                await event.click(i=i, j=j)

                # 拼日志
                try:
                    chat = await event.get_chat()
                    chat_title = getattr(chat, 'title', '未知群')
                except Exception:
                    chat_title = '未知群'
                chat_id = event.chat_id
                msg_id = event.message.id
                raw_id = str(chat_id).replace('-100', '')
                link = f"https://t.me/c/{raw_id}/{msg_id}"

                asyncio.create_task(notify(
                    f"✅ [账号{idx}] 抢到红包\n"
                    f"来源：{source}\n"
                    f"按钮：{txt}\n"
                    f"群名：{chat_title}\n"
                    f"群ID：{chat_id}\n"
                    f"链接：{link}"
                ))
                return  # 每条消息只点一个
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception as e:
                asyncio.create_task(notify(f"❌ [账号{idx}] 点击失败: {e}"))


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

    async def bot_handler(event):
        await click_claim_buttons(event, idx, "机器人")

    async def group_handler(event):
        await click_claim_buttons(event, idx, "群红包")

    try:
        await client.start(phone=lambda: "")

        # 1. 解析所有目标机器人
        bot_ids = []
        for uname in TARGET_BOTS:
            try:
                entity = await client.get_entity(uname)
                bot_ids.append(entity.id)
            except Exception:
                pass

        # 2. 监听机器人消息
        if bot_ids:
            client.add_event_handler(
                bot_handler,
                events.NewMessage(from_users=bot_ids, incoming=True)
            )
            client.add_event_handler(
                bot_handler,
                events.MessageEdited(from_users=bot_ids, incoming=True)
            )

        # 3. 监听指定群
        if TARGET_GROUPS:
            client.add_event_handler(
                group_handler,
                events.NewMessage(chats=TARGET_GROUPS, incoming=True)
            )
            client.add_event_handler(
                group_handler,
                events.MessageEdited(chats=TARGET_GROUPS, incoming=True)
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
