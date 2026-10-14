"""
KPay 网页红包自动领取 — 主程序。
监听群消息 → 提取 TOKEN → 获取 initData → 调用 Playwright 破解 → 通知 bot。
"""
import asyncio
import os
import re
import logging
import urllib.parse
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.messages import (
    RequestWebViewRequest, SendWebViewDataRequest
)
from telethon.tl.types import InputUser

from web_verify import solve_kpay_verify

logging.basicConfig(level=logging.CRITICAL)
for name in logging.root.manager.loggerDict:
    logging.getLogger(name).setLevel(logging.CRITICAL)
logging.getLogger('telethon').setLevel(logging.CRITICAL)

# ========== 环境变量 ==========
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSIONS_STR = os.environ.get("SESSIONS", "")
SESSIONS = [s.strip() for s in SESSIONS_STR.split("|") if s.strip()]
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MY_CHAT_ID = int(os.environ.get("MY_CHAT_ID", 0))

# KPay 相关
KPAY_BOT = os.environ.get("KPAY_BOT", "kpay")
TARGET_GROUPS_STR = os.environ.get("TARGET_GROUPS", "")
TARGET_GROUPS = [int(x.strip()) for x in TARGET_GROUPS_STR.split(",") if x.strip()]
# =============================

if not all([API_ID, API_HASH, SESSIONS, BOT_TOKEN, MY_CHAT_ID]):
    print("❌ 缺少环境变量")
    exit(1)


async def send_tg(message: str):
    """发通知到你的 TG"""
    safe = urllib.parse.quote(message, encoding="utf-8")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={MY_CHAT_ID}&text={safe}"
    req = urllib.request.Request(url, method="GET")
    try:
        def _send():
            with urllib.request.urlopen(req, timeout=10) as resp:
                pass
        await asyncio.to_thread(_send)
    except Exception as e:
        print(f"[推送失败] {e}")


def extract_token(text: str) -> str:
    """从红包消息里提取 web_<TOKEN> 的 TOKEN"""
    m = re.search(r"web_([a-f0-9\-]+)", text)
    return m.group(1) if m else ""


async def process_kpay_redpacket(client, event, idx: int, token: str):
    """
    处理单个 KPay 红包：
    1. 给 @kpay 发 /start web_<TOKEN>
    2. 从回复里拿"立即验证"按钮 URL
    3. RequestWebViewRequest 拿 initData
    4. 调用 Playwright 破解
    5. SendWebViewData 通知 bot
    """
    try:
        # 1. 找 @kpay bot
        kpay_entity = await client.get_entity(KPAY_BOT)

        # 2. 发 /start web_<TOKEN>
        await client.send_message(kpay_entity, f"/start web_{token}")
        await asyncio.sleep(2)

        # 3. 拿最新回复，找 WebView 按钮
        messages = await client.get_messages(kpay_entity, limit=3)
        webview_url = None
        for msg in messages:
            if not msg.reply_markup:
                continue
            for row in msg.reply_markup.rows:
                for btn in row.buttons:
                    url = getattr(btn, "url", None)
                    if url and "envelope.kpaye.com" in url:
                        webview_url = url
                        break
                if webview_url:
                    break
            if webview_url:
                break

        if not webview_url:
            print(f"[账号{idx}] 没找到验证按钮")
            return

        print(f"[账号{idx}] WebView URL: {webview_url}")

        # 4. RequestWebViewRequest 拿合法 initData
        bot_input = InputUser(
            user_id=kpay_entity.id,
            access_hash=getattr(kpay_entity, "access_hash", 0),
        )
        res = await client(RequestWebViewRequest(
            peer=kpay_entity,
            bot=bot_input,
            platform="android",
            url=webview_url,
            theme_params=None,
            from_bot_menu=False,
            start_param=None,
        ))

        # 从返回的 URL 里提取 initData
        # res.url 形如: https://...#tgWebAppData=<urlencoded initData>&...
        returned_url = res.url
        m = re.search(r"tgWebAppData=([^&]+)", returned_url)
        if not m:
            print(f"[账号{idx}] initData 提取失败")
            return
        init_data = urllib.parse.unquote(m.group(1))
        print(f"[账号{idx}] initData 长度: {len(init_data)}")

        # 从 webview_url 里提取 token 和 base_url
        parsed = urllib.parse.urlparse(webview_url)
        qs = urllib.parse.parse_qs(parsed.query)
        real_token = qs.get("token", [""])[0]
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        # 5. 调用 Playwright 破解
        result = await solve_kpay_verify(init_data, real_token, base_url)

        if result.get("success"):
            vk = result["verification_key"]
            print(f"[账号{idx}] 验证成功: {vk}")

            # 6. sendData 通知 bot
            payload = {
                "type": "envelope_web_verification",
                "envelope_id": None,
                "envelope_token": real_token,
                "verified": True,
            }
            import json
            await client(SendWebViewDataRequest(
                bot=kpay_entity,
                button_text="立即验证",
                data=json.dumps(payload, ensure_ascii=False),
            ))

            await send_tg(
                f"🎉 [账号{idx}] KPay 红包领取成功\n"
                f"TOKEN: {real_token[:16]}...\n"
                f"验证码: {vk}"
            )
        else:
            err = result.get("error", "未知错误")
            print(f"[账号{idx}] 验证失败: {err}")
            await send_tg(f"❌ [账号{idx}] KPay 红包失败: {err}")

    except Exception as e:
        print(f"[账号{idx}] KPay 处理异常: {e}")


async def start_account(session_string: str, idx: int):
    """启动单个账号"""
    client = TelegramClient(
        StringSession(session_string),
        API_ID, API_HASH,
        connection_retries=5, retry_delay=5,
        auto_reconnect=True, request_retries=3,
        flood_sleep_threshold=86400, sequential_updates=False,
    )

    # 去重：已处理的 TOKEN
    processed_tokens = set()

    async def handler(event):
        """监听群消息，找 KPay 红包"""
        text = event.raw_text or ""
        if "web_" not in text:
            return
        if "kpay" not in text.lower() and "KPay" not in text:
            return

        token = extract_token(text)
        if not token or token in processed_tokens:
            return
        processed_tokens.add(token)
        # 防止集合无限增长
        if len(processed_tokens) > 500:
            processed_tokens.clear()

        print(f"[账号{idx}] 发现 KPay 红包 TOKEN: {token[:16]}...")
        await process_kpay_redpacket(client, event, idx, token)

    try:
        await client.start(phone=lambda: "")
        # 监听所有群（也可以在 TARGET_GROUPS 里限定）
        client.add_event_handler(
            handler,
            events.NewMessage(incoming=True),
        )
        print(f"[账号{idx}] 启动成功，监听 KPay 红包")
        await client.run_until_disconnected()
    except Exception as e:
        print(f"[账号{idx}] 启动失败: {e}")


async def main():
    print(f"[启动] {len(SESSIONS)} 个账号")
    tasks = [start_account(s, i) for i, s in enumerate(SESSIONS)]
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass