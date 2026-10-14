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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MY_CHAT_ID = int(os.environ.get("MY_CHAT_ID", 0))

BOT_USERNAMES_STR = os.environ.get("BOT_USERNAMES", "fllqb,hbfasongkbot,kkpay")
TARGET_BOT_USERNAMES = [u.strip().lstrip('@') for u in BOT_USERNAMES_STR.split(",") if u.strip()]

TARGET_GROUPS_STR = os.environ.get("TARGET_GROUPS", "-1003165336676,-1002803069645")
TARGET_GROUPS_SET = set()
for x in TARGET_GROUPS_STR.split(","):
    x = x.strip()
    if x:
        try:
            TARGET_GROUPS_SET.add(int(x))
        except ValueError:
            pass
# =============================

if not all([API_ID, API_HASH, SESSIONS, BOT_TOKEN, MY_CHAT_ID, TARGET_BOT_USERNAMES]):
    print("❌ 缺少环境变量")
    exit(1)

# ========== 屏蔽配置 ==========
BANNED_KEYWORDS = ['kkcoin', 'jiba', 'kk币']

# 全角/异体字符归一化（防止伪装绕过）
CHAR_NORMALIZE = {
    'ｋ': 'k', 'ｃ': 'c', 'ｏ': 'o', 'ｉ': 'i', 'ｎ': 'n',
    'Ｋ': 'k', 'Ｃ': 'c', 'Ｏ': 'o', 'Ｉ': 'i', 'Ｎ': 'n',
    '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
    '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
    '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
    '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
    '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
    '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
    'ǀ': 'i', 'ι': 'i', 'ı': 'i',
}

def normalize_chars(text):
    if not text:
        return ""
    return "".join(CHAR_NORMALIZE.get(ch, ch) for ch in text)

def contains_banned(text: str) -> bool:
    if not text:
        return False
    t = normalize_chars(text).lower()
    return any(kw in t for kw in BANNED_KEYWORDS)

def check_banned_before_click(event, markup) -> tuple:
    """
    点击前彻底检查：消息文本 + 所有按钮文字。
    返回 (是否含屏蔽词, 命中来源)
    """
    raw = event.raw_text or ""
    if contains_banned(raw):
        return True, f"消息文本: {raw[:80]}"

    if markup:
        for row in markup.rows:
            for btn in markup.rows[0].buttons:  # 占位，会被下面覆盖
                pass
        for row in markup.rows:
            for btn in row.buttons:
                bt = btn.text or ""
                if contains_banned(bt):
                    return True, f"按钮文字: {bt[:80]}"

    return False, ""

# ========== Emoji 数字映射 ==========
EMOJI_NUM_MAP = {
    '1️⃣': '1', '2️⃣': '2', '3️⃣': '3', '4️⃣': '4', '5️⃣': '5', '6️⃣': '6', '7️⃣': '7', '8️⃣': '8', '9️⃣': '9', '0️⃣': '0',
    '1⃣': '1', '2⃣': '2', '3⃣': '3', '4⃣': '4', '5⃣': '5', '6⃣': '6', '7⃣': '7', '8⃣': '8', '9⃣': '9', '0⃣': '0',
    '①': '1', '②': '2', '③': '3', '④': '4', '⑤': '5', '⑥': '6', '⑦': '7', '⑧': '8', '⑨': '9', '⓪': '0',
    '一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '零': '0',
}

CLAIM_BUTTON_KEYWORDS = ['领取红包', '🧧', '领取', '领红包', '抢红包', '点我领取']

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00a0]', '', text).strip()

def normalize_emoji_numbers(text):
    if not text:
        return ""
    for emoji, num in EMOJI_NUM_MAP.items():
        text = text.replace(emoji, num)
    return text

def is_claim_button(btn_text):
    cleaned = clean_text(btn_text)
    return any(k in cleaned for k in CLAIM_BUTTON_KEYWORDS)

def solve_math(text):
    if not text:
        return None
    t = normalize_emoji_numbers(text).lower()
    t = t.replace('＋', '+').replace('－', '-').replace('×', '*').replace('÷', '/')
    t = t.replace('加', '+').replace('减', '-').replace('乘', '*').replace('除', '/')
    m = re.search(r'(\d+)\s*([\+\-\*xX\/])\s*(\d+)', t)
    if not m:
        return None
    try:
        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
        if op == '+': return str(a + b)
        if op == '-': return str(a - b)
        if op in ['*', 'x']: return str(a * b)
        if op == '/': return str(a // b) if a % b == 0 else str(round(a / b, 2))
    except Exception:
        return None
    return None

def extract_verify_code(text):
    if not text:
        return None
    m = re.search(r'验证码\s*([A-Za-z0-9]+)', text)
    if m:
        return m.group(1).strip()
    return None

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

# ================== 单账号 ==================
async def start_account(session_string, idx):
    client = TelegramClient(
        StringSession(session_string),
        API_ID, API_HASH,
        connection_retries=5, retry_delay=5,
        auto_reconnect=True, request_retries=3,
        flood_sleep_threshold=86400, sequential_updates=False,
    )

    bot_id_to_username = {}

    async def do_click(event, i, j, btn_text, source):
        """点击并通知（点击前不做屏蔽，屏蔽已在 handler 中完成）"""
        try:
            await event.click(i=i, j=j)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return
        except Exception:
            return

        try:
            chat = await event.get_chat()
            chat_title = getattr(chat, 'title', '未知群')
        except Exception:
            chat_title = '未知群'
        chat_id = event.chat_id
        msg_id = event.message.id
        link = f"https://t.me/c/{str(chat_id).replace('-100','')}/{msg_id}"

        asyncio.create_task(notify(
            f"✅ [账号{idx}] 抢到红包\n"
            f"来源：{source}\n"
            f"按钮：{btn_text}\n"
            f"群名：{chat_title}\n"
            f"群ID：{chat_id}\n"
            f"链接：{link}"
        ))

    async def handler(event):
        # 群组白名单
        if TARGET_GROUPS_SET and event.chat_id not in TARGET_GROUPS_SET:
            return

        text = clean_text(event.raw_text or "")
        markup = event.message.reply_markup

        # ========== 点击前屏蔽检查：含屏蔽币种直接跳过 ==========
        hit, detail = check_banned_before_click(event, markup)
        if hit:
            asyncio.create_task(notify(
                f"🛡️ [账号{idx}] 已跳过屏蔽红包\n"
                f"命中：{detail}"
            ))
            return  # 绝不点击

        if not markup:
            return

        # ---------- 1. 验证码红包 ----------
        is_code = ("验证码" in text) or ("+" in text and "=" in text) or ("＋" in text and "=" in text)
        if is_code:
            answer = solve_math(text)
            if not answer:
                answer = extract_verify_code(text)

            if answer:
                for i, row in enumerate(markup.rows):
                    for j, button in enumerate(row.buttons):
                        btn_text = clean_text(button.text or '')
                        if btn_text.upper() == answer.upper():
                            # 按钮文字二次检测
                            if contains_banned(btn_text):
                                return
                            await do_click(event, i, j, btn_text, f"验证码({answer})")
                            return

        # ---------- 2. 普通红包 ----------
        for i, row in enumerate(markup.rows):
            for j, button in enumerate(row.buttons):
                if getattr(button, 'url', None):
                    continue
                btn_text = clean_text(button.text or '')
                if is_claim_button(btn_text):
                    # 按钮文字二次检测
                    if contains_banned(btn_text):
                        asyncio.create_task(notify(
                            f"🛡️ [账号{idx}] 按钮含屏蔽词，跳过：{btn_text[:80]}"
                        ))
                        return
                    await do_click(event, i, j, btn_text, "公开红包")
                    return

    try:
        await client.start(phone=lambda: "")
        bot_ids = []
        for uname in TARGET_BOT_USERNAMES:
            try:
                entity = await client.get_entity(uname)
                bot_ids.append(entity.id)
                bot_id_to_username[entity.id] = uname
                print(f"[账号{idx}] @{uname} -> {entity.id}")
            except Exception:
                print(f"[账号{idx}] 无法解析 @{uname}")

        if bot_ids:
            client.add_event_handler(
                handler,
                events.NewMessage(from_users=bot_ids, incoming=True)
            )
            if TARGET_GROUPS_SET:
                print(f"[账号{idx}] 监听 {len(bot_ids)} 个 Bot，群组白名单：{sorted(TARGET_GROUPS_SET)}")
            else:
                print(f"[账号{idx}] 监听 {len(bot_ids)} 个 Bot（不限群）")

        await client.run_until_disconnected()
    except Exception as e:
        print(f"[账号{idx}] 启动失败: {e}")

async def main():
    print(f"[启动] {len(SESSIONS)} 个账号，监听：{TARGET_BOT_USERNAMES}")
    if TARGET_GROUPS_SET:
        print(f"[启动] 群组白名单：{sorted(TARGET_GROUPS_SET)}")
    print(f"[屏蔽] 关键词：{BANNED_KEYWORDS}")
    tasks = [start_account(s, i) for i, s in enumerate(SESSIONS)]
    await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
