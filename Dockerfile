# agent-gavel —— MCP over stdio 镜像
#
# 构建:  docker build -t agent-gavel .
# 运行:  docker run -i --rm -e AGENT_GAVEL_HEADLESS=1 agent-gavel
#
# 发布加固：默认开启域名限制且白名单为空（= 拒绝所有域名）。要访问站点必须显式
# 传入白名单，例如：-e AGENT_GAVEL_ALLOW_DOMAINS=example.com,en.wikipedia.org
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_GAVEL_HEADLESS=1 \
    AGENT_GAVEL_DOMAIN_GUARD=1

WORKDIR /app

# Playwright 自带的 chromium 需要这些系统库；用 --with-deps 一次性装齐。
COPY pyproject.toml README.md LICENSE ./
COPY agent_gavel ./agent_gavel
RUN pip install . \
    && playwright install --with-deps chromium \
    && rm -rf /root/.cache

# 不打包/不运行旧通道：仓库根 legacy/ 不在此镜像内。
ENTRYPOINT ["python", "-m", "agent_gavel.main"]
