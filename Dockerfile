# 使用官方 Playwright 镜像（已含 Chromium + 所有系统依赖）
FROM mcr.microsoft.com/playwright:v1.52.0-noble

WORKDIR /app

# 安装 Python
RUN apt-get update && apt-get install -y python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# 创建虚拟环境并安装依赖
RUN python3 -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 启动
CMD ["python3", "main.py"]