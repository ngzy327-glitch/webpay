import asyncio
import os
import re
import json
import logging
import urllib.parse
import base64
import httpx
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestWebViewRequest, SendWebViewDataRequest
from telethon.tl.types import InputUser
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
KPAY_BOT = os.environ.get("KPAY_BOT", "kpay").lstrip('@')

# 百炼（仅作为 SVG 解析失败的备用方案）
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen-vl-max")
# =============================

if not all([API_ID, API_HASH, SESSIONS, BOT_TOKEN, MY_CHAT_ID]):
    print("❌ 缺少环境变量")
    exit(1)


async def send_tg(message: str):
    safe = urllib.parse.quote(message, encoding="utf-8")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={MY_CHAT_ID}&text={safe}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(url)
    except Exception as e:
        print(f"[推送失败] {e}")


def extract_token(text: str) -> str:
    m = re.search(r"web_([a-f0-9\-]+)", text)
    return m.group(1) if m else ""


def svg_char(svg_text: str) -> str:
    """从 SVG 中提取 <text> 标签里的字符（明文）"""
    m = re.search(r'<text[^>]*>([^<]+)</text>', svg_text)
    if m:
        return m.group(1).strip()
    return ""


async def fetch_svg(http_client, url: str) -> str:
    """下载 SVG 并提取字符"""
    try:
        resp = await http_client.get(url, timeout=10)
        if resp.status_code == 200:
            ch = svg_char(resp.text)
            if ch:
                return ch
            # SVG 没解析出字符，交给百炼兜底
            return await bailian_ocr(resp.content)
    except Exception as e:
        print(f"[SVG失败] {e}")
    return ""


async def bailian_ocr(image_bytes: bytes) -> str:
    """备用：百炼视觉识别"""
    if not DASHSCOPE_API_KEY or not DASHSCOPE_BASE_URL:
        return ""
    b64 = base64.b64encode(image_bytes).decode()
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                f"{DASHSCOPE_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}"},
                json={
                    "model": VISION_MODEL,
                    "max_tokens": 10,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            {"type": "text", "text": "图片上是一个汉字或字符，只回答这一个字符。"},
                        ],
                    }],
                },
            )
            if resp.status_code == 200:
                answer = resp.json()["choices"][0]["message"]["content"].strip()
                ch = "".join(c for c in answer if c.isalnum() or "\u4e00" <= c <= "\u9fff")
                return ch[:1]
    except Exception as e:
        print(f"[百炼失败] {e}")
    return ""


async def solve_verify(init_data: str, token: str, api_base: str) -> dict:
    """纯 HTTP 破解验证码"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://envelope.kpaye.com/",
        "Origin": "https://envelope.kpaye.com",
    }

    async with httpx.AsyncClient(timeout=25, headers=headers, follow_redirects=True) as client:
        # 1. POST /verification/init
        init_url = f"{api_base}/verification/init"
        print(f"[验证] POST {init_url}")
        try:
            init_resp = await client.post(
                init_url,
                json={"envelope_token": token, "init_data": init_data},
            )
        except Exception as e:
            return {"success": False, "error": f"init请求失败: {e}"}

        if init_resp.status_code != 200:
            return {"success": False, "error": f"init HTTP {init_resp.status_code}: {init_resp.text[:200]}"}

        try:
            init_json = init_resp.json()
        except Exception:
            return {"success": False, "error": f"init 非 JSON: {init_resp.text[:200]}"}

        if not init_json.get("success"):
            return {"success": False, "error": f"init失败: {init_json}"}

        data = init_json["data"]
        challenge_id = data["challenge_id"]
        target_urls = data.get("target_char_images", [])
        char_images = data.get("char_images", [])

        print(f"[验证] 目标 {len(target_urls)} 个，选项 {len(char_images)} 个")

        # 2. 并行下载所有 SVG，提取字符
        target_tasks = [fetch_svg(client, u) for u in target_urls]
        choice_tasks = [fetch_svg(client, item["url"]) for item in char_images]

        target_chars = await asyncio.gather(*target_tasks)
        choice_chars = await asyncio.gather(*choice_tasks)
        choice_codes = [item["code"] for item in char_images]

        print(f"[验证] 目标字符: {target_chars}")
        print(f"[验证] 选项字符: {choice_chars}")

        # 3. 按 target 顺序（01→02→03）逐个在 choices 里找
        answer = []
        for tch in target_chars:
            if not tch:
                continue
            matched = False
            for i, cch in enumerate(choice_chars):
                if cch == tch:
                    answer.append(choice_codes[i])
                    matched = True
                    break
            if not matched:
                print(f"[验证] ⚠️ 目标字符 {tch!r} 没找到匹配")

        print(f"[验证] 提交答案: {answer}")

        # 4. POST /verification/validate
        validate_url = f"{api_base}/verification/validate"
        try:
            validate_resp = await client.post(
                validate_url,
                json={
                    "challenge_id": challenge_id,
                    "init_data": init_data,
                    "answer": answer,
                },
            )
        except Exception as e:
            return {"success": False, "error": f"validate请求失败: {e}"}

        try:
            validate_json = validate_resp.json()
        except Exception:
            return {"success": False, "error": f"validate 非 JSON: {validate_resp.text[:200]}"}

        return validate_json


async def process_kpay(client, event, idx: int, token: str):
    try:
        # 1. 找 kpay
        kpay = await client.get_entity(KPAY_BOT)

        # 2. 发 /start web_<token>
        await client.send_message(kpay, f"/start web_{token}")
        await asyncio.sleep(2.5)

        # 3. 从最新回复里找 WebView URL
        webview_url = None
        messages = await client.get_messages(kpay, limit=5)
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
            print(f"[账号{idx}] 未找到 WebView URL")
            return

        print(f"[账号{idx}] WebView: {webview_url[:100]}...")

        # 4. RequestWebViewRequest 拿 initData
        bot_input = InputUser(
            user_id=kpay.id,
            access_hash=getattr(kpay, "access_hash", 0),
        )
        res = await client(RequestWebViewRequest(
            peer=kpay,
            bot=bot_input,
            platform="android",
            url=webview_url,
            theme_params=None,
            from_bot_menu=False,
            start_param=None,
        ))

        # 提取 initData
        m = re.search(r"tgWebAppData=([^&]+)", res.url)
        if not m:
            print(f"[账号{idx}] initData 提取失败")
            return
        init_data = urllib.parse.unquote(m.group(1))
        print(f"[账号{idx}] initData 长度: {len(init_data)}")

        # 5. 确定 API base
        parsed = urllib.parse.urlparse(webview_url)
        qs = urllib.parse.parse_qs(parsed.query)
        real_token = qs.get("token", [""])[0]
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        # KPay 的 API 在 /envelope 下
        api_base = f"{base_url}/envelope"

        print(f"[账号{idx}] API base: {api_base}")

        # 6. 破解验证码
        result = await solve_verify(init_data, real_token, api_base)

        if result.get("success"):
            vk = result.get("data", {}).get("verification_key", "")
            print(f"[账号{idx}] ✅ 验证成功: {vk}")

            # 7. sendData 通知 bot
            payload = {
                "type": "envelope_web_verification",
                "envelope_id": None,
                "envelope_token": real_token,
                "verified": True,
            }
            try:
                await client(SendWebViewDataRequest(
                    bot=bot_input,
                    button_text="立即验证",
                    data=json.dumps(payload, ensure_ascii=False),
                ))
                print(f"[账号{idx}] sendData 已发送")
            except Exception as e:
                print(f"[账号{idx}] sendData 失败: {e}")

            await send_tg(f"🎉 [账号{idx}] KPay 成功\nTOKEN: {real_token[:16]}...\nKey: {vk}")
        else:
            err = result.get("error", "未知错误")
            print(f"[账号{idx}] ❌ 失败: {err}")
            await send_tg(f"❌ [账号{idx}] KPay: {err}")

    except Exception as e:
        print(f"[账号{idx}] 异常: {e}")


async def start_account(session_string: str, idx: int):
    print(f"[账号{idx}] 初始化...")
    client = TelegramClient(
        StringSession(session_string),
        API_ID, API_HASH,
        connection_retries=5, retry_delay=5,
        auto_reconnect=True, request_retries=3,
        flood_sleep_threshold=86400, sequential_updates=False,
    )

    processed = set()

    async def handler(event):
        text = event.raw_text or ""
        if "web_" not in text:
            return
        if "kpay" not in text.lower():
            return
        token = extract_token(text)
        if not token or token in processed:
            return
        processed.add(token)
        if len(processed) > 500:
            processed.clear()
        print(f"[账号{idx}] 发现 KPay 红包: {token[:16]}...")
        await process_kpay(client, event, idx, token)

    try:
        await client.start(phone=lambda: "")
        client.add_event_handler(handler, events.NewMessage(incoming=True))
        print(f"[账号{idx}] ✅ 启动成功，监听 KPay 红包")
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