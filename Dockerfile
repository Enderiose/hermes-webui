ARG BASE_IMAGE=nousresearch/hermes-agent:latest
FROM ${BASE_IMAGE}

ARG NODE_VERSION=24.15.0

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "amd64" ]; then NODE_ARCH="x64"; else NODE_ARCH="$ARCH"; fi \
    && echo "Downloading Node.js v${NODE_VERSION} for ${NODE_ARCH}" \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.gz" \
       -o /tmp/node.tar.gz \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
       /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
    && tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1 \
    && rm -f /tmp/node.tar.gz \
    && node --version \
    && npm --version

WORKDIR /app

COPY package*.json ./
# Increase Node.js memory limit to prevent OOM during build
ENV NODE_OPTIONS=--max-old-space-size=4096
RUN npm ci --ignore-scripts && npm rebuild node-pty

COPY . .

RUN npm run build && npm prune --omit=dev
RUN npm run verify:sharp-runtime

ENV NODE_ENV=production
ENV HOME=/home/agent
ENV HERMES_HOME=/home/agent/.hermes
ENV HERMES_WEB_UI_MANAGED_GATEWAY=1
ENV STUDIO_P2P_UDP_PORT_RANGE=50000-50127
# Keep runtime-installed coding agent CLIs in the existing Studio data volume.
ENV NPM_CONFIG_PREFIX=/home/agent/.hermes-web-ui/coding-agent/npm
ENV PATH=/home/agent/.hermes-web-ui/coding-agent/npm/bin:/opt/hermes/.venv/bin:$PATH

EXPOSE 6060
EXPOSE 50000-50127/udp

# ============================================================================
# 本地定制层（本 fork 的二次开发；清单见 deploy/REBUILD.md 与 deploy/SYNC-UPSTREAM.md）
# 同步上游时的注意事项见 deploy/SYNC-UPSTREAM.md —— 这段是唯一会与上游冲突的构建改动。
# ============================================================================

# --- 1. 中文字体：黑体（思源黑体 / Noto Sans SC）---------------------------
# 公众号生图 / banner / 封面里的文字一律用它本地渲染（绝不让生图模型写字）。
RUN set -eux; \
    mkdir -p /usr/share/fonts/opentype/noto-sc; \
    for f in Regular Bold Medium; do \
      curl -fsSL -o "/usr/share/fonts/opentype/noto-sc/NotoSansSC-$f.otf" \
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/SC/NotoSansSC-$f.otf"; \
    done; \
    ls -l /usr/share/fonts/opentype/noto-sc/; \
    fc-cache -f

# --- 2. Playwright + cairosvg（二维码登录中继 / SVG→PNG 栅格化）-------------
# 一对一起装：cairosvg 一缺，新厂牌 logo 会静默补不上（不报错、banner 少一个）。
# venv 里没有 pip，必须用 uv。
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright
RUN uv pip install --python /opt/hermes/.venv/bin/python \
      playwright==1.63.0 \
      cairosvg==2.9.1 \
    && /opt/hermes/.venv/bin/python -m playwright install --with-deps chromium

# --- 3. 企微插件补丁（改的是 base image 里的 wecom 插件，源码不在本仓库）---
# 编译期打好；Web UI 那两处已改成源码级改动，不在此列。
COPY deploy/patches /opt/deploy/patches
RUN bash /opt/deploy/patches/apply-wecom.sh

# --- 4. 本部署重建指引（随镜像发布）----------------------------------------
COPY deploy/REBUILD.md /app/REBUILD.md

# 强制覆盖基础镜像的默认启动脚本，让镜像本身具备独立运行的能力
ENTRYPOINT ["/app/bin/start-studio-all.sh"]
CMD []
