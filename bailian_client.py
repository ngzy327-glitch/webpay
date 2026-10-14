"""
阿里云百炼视觉模型调用封装。
使用 OpenAI 兼容接口，base_url 为百炼的 compatible-mode/v1。
"""
import os
import base64
from openai import AsyncOpenAI

# 从环境变量读取百炼配置
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.environ.get(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen-vl-max")

_client = None

def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url=DASHSCOPE_BASE_URL,
        )
    return _client


async def identify_image(image_bytes: bytes, prompt: str = None) -> str:
    """
    用百炼视觉模型识别图片中的单个字符。
    image_bytes: 图片的原始字节（PNG/JPG/SVG 均可）
    prompt: 自定义提示词，默认要求只返回一个字符
    """
    if not image_bytes or not DASHSCOPE_API_KEY:
        return ""

    # 判断 MIME 类型
    mime = "image/png"
    if image_bytes[:4] == b"\x89PNG":
        mime = "image/png"
    elif image_bytes[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    elif image_bytes[:5] == b"<?xml" or image_bytes[:4] == b"<svg":
        mime = "image/svg+xml"

    b64 = base64.b64encode(image_bytes).decode()

    if not prompt:
        prompt = "图片上是一个汉字或字符，只回答这一个字符，不要任何其他内容。"

    try:
        resp = await get_client().chat.completions.create(
            model=VISION_MODEL,
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        answer = resp.choices[0].message.content.strip()
        # 只保留单个字符
        answer = "".join(c for c in answer if c.isalnum() or "\u4e00" <= c <= "\u9fff")
        return answer[:1] if answer else ""
    except Exception as e:
        print(f"[百炼识别异常] {e}")
        return ""


async def identify_multiple(images: list[bytes], prompt: str = None) -> list[str]:
    """批量识别，按顺序返回字符列表"""
    import asyncio
    tasks = [identify_image(img, prompt) for img in images]
    return await asyncio.gather(*tasks)