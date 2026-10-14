"""
KPay 网页验证自动化。
加载验证页 → 提取验证码图片 → 调用百炼识别 → 点击匹配 → 提交。
"""
import asyncio
import urllib.parse
from playwright.async_api import async_playwright

from bailian_client import identify_multiple


async def solve_kpay_verify(init_data: str, token: str, base_url: str) -> dict:
    """
    加载 KPay 验证页并自动破解验证码。

    参数：
        init_data: Telegram 签发的合法 initData（URL 编码前）
        token:     红包 TOKEN
        base_url:  验证页基础 URL，如 https://envelope.kpaye.com/uploads/envelope/index.html

    返回：
        {"success": True, "verification_key": "..."}
        或 {"success": False, "error": "..."}
    """
    enc_init = urllib.parse.quote(init_data, safe="")
    url = (
        f"{base_url}?token={token}&bot=kpay"
        f"#tgWebAppData={enc_init}"
        f"&tgWebAppVersion=9.6&tgWebAppPlatform=android"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.new_page()

        # 监听网络请求，捕获验证接口返回
        verify_result = {}

        async def on_response(response):
            if "/verification/init" in response.url:
                try:
                    verify_result["init"] = await response.json()
                except Exception:
                    pass
            if "/verification/validate" in response.url:
                try:
                    verify_result["validate"] = await response.json()
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            # 等验证码加载完成
            await page.wait_for_selector(".target-char-item", timeout=15000)

            # ---- 提取目标字符和选项字符 ----
            # 读取目标区域（带数字 01/02/03）
            targets_info = await page.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('.target-char-item'));
                return items.map(item => {
                    const img = item.querySelector('img');
                    const num = item.querySelector('.target-char-number');
                    return {
                        src: img ? img.src : null,
                        num: num ? num.textContent.trim() : null
                    };
                });
            }""")

            # 读取选项区域
            choices_info = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('.choice-button img'));
                return btns.map((img, idx) => ({ idx, src: img.src }));
            }""")

            if not targets_info or not choices_info:
                return {"success": False, "error": "验证码元素未找到"}

            # ---- 下载图片字节并调用百炼识别 ----
            async def download_image(src: str) -> bytes:
                """从 data URL 或 http URL 下载图片字节"""
                if src.startswith("data:"):
                    b64 = src.split(",", 1)[1]
                    import base64
                    return base64.b64decode(b64)
                else:
                    # 用 page 的 context 下载
                    resp = await page.context.request.get(src)
                    return await resp.body()

            # 下载目标图 + 选项图
            target_bytes = []
            for t in targets_info:
                if t["src"]:
                    target_bytes.append(await download_image(t["src"]))
                else:
                    target_bytes.append(b"")

            choice_bytes = []
            for c in choices_info:
                choice_bytes.append(await download_image(c["src"]))

            # 调用百炼批量识别
            target_chars = await identify_multiple(target_bytes)
            choice_chars = await identify_multiple(choice_bytes)

            print(f"[KPay] 目标字符: {target_chars}")
            print(f"[KPay] 选项字符: {choice_chars}")

            # ---- 按数字 01→02→03 顺序排序，逐个匹配点击 ----
            # 建立 数字 → 索引 的映射
            num_to_idx = {}
            for i, t in enumerate(targets_info):
                if t["num"]:
                    num_to_idx[t["num"]] = i

            # 按 01、02、03 顺序处理
            sorted_nums = sorted(num_to_idx.keys(), key=lambda x: int(x))
            clicked_count = 0

            for num in sorted_nums:
                idx = num_to_idx[num]
                target_ch = target_chars[idx] if idx < len(target_chars) else ""
                if not target_ch:
                    continue

                # 在选项里找匹配的字符
                for j, ch in enumerate(choice_chars):
                    if ch == target_ch:
                        await page.evaluate(
                            f"document.querySelectorAll('.choice-button')[{j}].click()"
                        )
                        clicked_count += 1
                        await asyncio.sleep(0.2)
                        break

            print(f"[KPay] 已点击 {clicked_count} 个选项")

            # ---- 点击提交 ----
            if clicked_count == 3:
                await page.evaluate(
                    "document.querySelector('.submit-button').click()"
                )
                # 等验证结果
                await asyncio.sleep(3)

            # ---- 收集结果 ----
            validate_data = verify_result.get("validate", {})
            if validate_data.get("success"):
                vk = validate_data.get("data", {}).get("verification_key", "")
                return {"success": True, "verification_key": vk}
            else:
                return {
                    "success": False,
                    "error": str(validate_data.get("message", "验证失败")),
                }

        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            await browser.close()