import asyncio
import os
import re
import random
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SendMessageRequest
from telethon.errors import FloodWaitError

# 彻底关闭所有日志
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MY_CHAT_ID = int(os.environ.get("MY_CHAT_ID", 0))
# =============================

if not all([API_ID, API_HASH, SESSIONS]):
    print("❌ 缺少 API_ID / API_HASH / SESSIONS")
    exit(1)

# 按钮关键词：命中任意一个就点
CLAIM_KEYWORDS = ['领取红包', '🧧', '领取', '领红包', '抢红包', '点我领取']

def is_claim(text: str) -> bool:
    """极致速度：不做清洗，直接 in 判断"""
    for k in CLAIM_KEYWORDS:
        if k in text:
            return True
    return False


async def notify(msg: str):
    """通知（异步，不阻塞主流程）"""
    if not BOT_TOKEN or not MY_CHAT_ID:
        return
    import urllib.parse, urllib.request
    try:
        safe = urllib.parse.quote(msg)
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={MY_CHAT_ID}&text={safe}"
        await asyncio.to_thread(lambda: urllib.request.urlopen(url, timeout=5).read())
    except Exception:
        pass


async def run_account(session: str, idx: int):
    client = TelegramClient(
        StringSession(session),
        API_ID, API_HASH,
        connection_retries=999,
        retry_delay=0,
        auto_reconnect=True,
        request_retries=5,
        flood_sleep_threshold=86400,
        sequential_updates=False,   # 并发处理更新，不排队
        use_ipv6=False,
    )

    # 预热目标 peer
    target_id = None

    async def handler(event):
        """收到消息立刻扫描按钮，无任何多余操作"""
        markup = event.message.reply_markup
        if not markup:
            return

        for row in markup.rows:
            for btn in row.buttons:
                txt = btn.text or ''
                if is_claim(txt):
                    # 立即点击
                    try:
                        await event.click(i=markup.rows.index(row),
                                           j=row.buttons.index(btn))
                        asyncio.create_task(notify(f"[{idx}] 抢到: {txt}"))
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                    except Exception:
                        pass
                    return

    try:
        await client.start(phone=lambda: "")
        entity = await client.get_entity(TARGET_BOT)
        target_id = entity.id
        print(f"[{idx}] ready -> @{TARGET_BOT} ({target_id})")

        # 同时监听新消息和编辑消息
        client.add_event_handler(
            handler,
            events.NewMessage(from_users=target_id, incoming=True)
        )
        client.add_event_handler(
            handler,
            events.MessageEdited(from_users=target_id, incoming=True)
        )

        await client.run_until_disconnected()
    except Exception as e:
        print(f"[{idx}] error: {e}")


async def main():
    print(f"[start] {len(SESSIONS)} account(s) -> @{TARGET_BOT}")
    tasks = [run_account(s, i) for i, s in enumerate(SESSIONS)]
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass