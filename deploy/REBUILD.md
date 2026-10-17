# 容器重建指引（本地二次开发清单）

> 目的：把这个容器**相对上游镜像做的所有本地改动**记全，重建 / 升级镜像后照此文档逐节执行即可恢复。
> 生成时间 2026-10-14 · 上游 Hermes runtime `0.21.2`（`code_sha 53c57871…`）· Python 3.13.5 · Node v24.15.0
> 相关文档：`/opt/data/mp/HANDOFF.md`（跨会话交接真源）、`/opt/data/mp/SYNC.md`（三表面同步）、
> `/opt/data/mp/TOPICS.md`（选题规范）、`/opt/data/mp/STYLE.md`（排版配图规范）

---

## 0. 先读这一节：什么会丢、什么不会

| 路径 | 性质 | 重建后 |
|---|---|---|
| `/opt/data` | **卷** | 保留（项目、脚本、补丁、字体、素材、状态） |
| `/home/agent/.hermes` | **卷** | 保留（profile、配置、技能、cron、会话库） |
| `/home/agent/.hermes-web-ui` | **卷** | 保留（Web UI 库、上传、登录态、`.ekko/`） |
| `/opt/hermes`（含 `.venv`、`.playwright`） | 镜像层 | **丢** —— venv 包（**playwright + cairosvg 两个**）与浏览器都要重装，见 3.4 |
| `/app` | 镜像层 | **丢** —— 本地补丁打过的文件被还原 |
| `/usr/share/fonts` | 镜像层 | **丢** —— 中文字体要重放 |
| `/opt/android-sdk`、JDK | 镜像层 | **丢** —— 出 APK 前要重装 |
| `/usr/local/bin/node`、`/usr/local/bin/npx` | 镜像自带 | 跟随镜像 |

**核心结论：所有「二次开发」都以补丁脚本 + 卷内数据的形式存在，重建后靠本文档第 3 节重放。**

### ⚠ `docker restart` ≠ `docker recreate`

**重启（restart）不还原镜像层，重建（recreate / `docker compose up -d --force-recreate` / 换镜像 tag）才会。**
2026-10-14 17:32 那次容器重启后，10-08 打在 `/opt/hermes/.../callback_adapter.py` 上的补丁依然在
（mtime 2026-10-08 21:30，内容 grep 命中）—— 这正是「只重启、没重建」的证据。
**重建后必须重打补丁**，见 3.2。

---

## 1. 上游镜像与容器定义

- 镜像：`hermes-web-ui-local:latest`（由 `/app/Dockerfile` 构建，构建上下文 `/app`）
- 容器名：`hermes-webui`，`restart: unless-stopped`
- 容器内镜像自带的 compose 参考件：`/app/docker-compose.yml`
  ```yaml
  services:
    hermes-webui:
      build: { context: ., dockerfile: Dockerfile }
      image: ${WEBUI_IMAGE:-hermes-web-ui-local:latest}
      container_name: ${WEBUI_CONTAINER_NAME:-hermes-webui}
      ports:
        - "${PORT:-6060}:${PORT:-6060}"
        - "${PREVIEW_FRONTEND_PORT:-8651}:8651"
        - "${XAI_OAUTH_PORT:-56121}:56121"
      volumes:
        - ${HERMES_DATA_DIR:-./hermes_data}:/home/agent/.hermes
        - ${HERMES_DATA_DIR:-./hermes_data}/hermes-web-ui:/home/agent/.hermes-web-ui
      environment:
        - PORT=${PORT:-6060}
        - HERMES_HOME=/home/agent/.hermes
        - HERMES_BIN=/opt/hermes/.venv/bin/hermes
        - HERMES_WEB_UI_MANAGED_GATEWAY=1
        - HERMES_PATCH_SCRIPT=${HERMES_PATCH_SCRIPT:-}
        - HERMES_LAN_ADVERTISE_URL=${HERMES_LAN_ADVERTISE_URL:-}
        - HERMES_APP_ENTITLEMENT_REQUIRED=${HERMES_APP_ENTITLEMENT_REQUIRED:-true}
        - NPM_CONFIG_PREFIX=/home/agent/.hermes-web-ui/coding-agent/npm
        - HERMES_ALLOW_ROOT_GATEWAY=1
      restart: unless-stopped
      stdin_open: true
      tty: true
  ```
- `ENTRYPOINT ["/app/bin/start-studio-all.sh"]`，`CMD []`；该脚本在 exec node 前调用
  `$HERMES_PATCH_SCRIPT`（**未设置时静默跳过，不报错**）。

### ⚠ 实际部署的 compose 与上面这份**不同**，重建时必须补齐这些差异

宿主（群晖）上的 compose 额外做了这些事，容器里看不到，**重建前先把宿主的 compose 备份下来**。

1. **`- "8645:8645"` 端口发布** —— 企微回调 / DSH 推送的入口，漏了企微整个哑掉。
2. **`- HERMES_PATCH_SCRIPT=/opt/data/patch/apply-all.sh`** —— 让本地补丁每次开机自动重打。
3. **`- HERMES_WRITE_SAFE_ROOT=/opt/data:/home/agent/.hermes`** —— 放开文件工具写入范围
   （APK 工程 `/home/agent/.hermes/hermes-app` 与 `/opt/data` 才能被 file 工具直接写）。
4. **`- HERMES_LAZY_INSTALL_TARGET=/opt/data/lazy-packages`** + **`- HERMES_DISABLE_LAZY_INSTALLS=1`**
   —— 惰性安装的 Python 包落到卷里（现存：`edge-tts 7.2.7` 及其依赖），避免每次重建重下。
5. **`- HERMES_MEDIA_DELIVERY_STRICT=0`**、**`- HERMES_MEDIA_TRUST_RECENT_FILES=1`** —— 企微 MEDIA 附件投递放宽。
6. 网络：容器跑在 **bridge** 网络，**无 IPv6**。

> **📌 实测校正（2026-10-14）**：以上 6 项里，当前容器 PID 1 的 env 只落实了 **1/3/4/5 中的第 3、4 条**
> （`HERMES_WRITE_SAFE_ROOT`、`HERMES_LAZY_INSTALL_TARGET`、`HERMES_DISABLE_LAZY_INSTALLS` ✓），
> 而 **`HERMES_PATCH_SCRIPT`、`HERMES_MEDIA_DELIVERY_STRICT`、`HERMES_MEDIA_TRUST_RECENT_FILES` 都不在 env 里**
> —— 补丁是靠人工跑 + 只重启没重建才活下来的。**重建时这三项要真的写进宿主 compose**，
> 别默认「镜像里那份参考 compose 就够了」。

镜像自带但**未启用**的项（实测 10-14）：8651 预览前端、56121 xAI OAuth 回调**都没有进程监听**
（`/proc/net/tcp` 只有 6060 与 8645 在 LISTEN）。compose 里照旧映射即可，不影响功能。

---

## 2. 重建后的执行顺序（总览）

```
3.1 宿主 compose 差异项       → 端口 8645 / 环境变量（含 3 项当前缺失的）
3.2 本地补丁 apply-all.sh     → 五处补丁（企微 ×3 + Web UI 默认 profile + Web UI 上下文长度）
3.3 系统依赖                  → JDK + Android SDK（只在要出 APK 时）
3.4 Playwright + cairosvg     → 二维码登录中继 + SVG→PNG 栅格化（补 logo）
3.5 中文字体（黑体）           → 思源黑体/Noto Sans SC：卷内 + 下载 + 系统目录
3.6 网络服务与端口自检        → 8645 / 6060 / 8899 + 上游中转连通性
3.7 图标 logo 本地仓库        → assets/ + logos_override/（在卷里，只校验）
3.8 profile 内容              → SOUL / memories / skills / hooks / scripts
3.9 cron 任务                 → 8 个任务（在卷里，只校验）
3.10 provider 链 + 生图链     → config.yaml（在卷里，只校验）
3.11 .env 凭据                → 24 个键名（值不在本文档）
```

---

## 3. 分节详解

### 3.1 宿主 compose 差异项

在宿主上编辑 compose，确保包含：

```yaml
ports:
  - "6060:6060"        # Web UI
  - "8651:8651"        # 预览前端（镜像自带，当前无监听，映射无副作用）
  - "56121:56121"      # xAI OAuth 回调（同上）
  - "8645:8645"        # ★ 企微回调 /wecom/callback + 入站 webhook /hook
environment:
  - HERMES_PATCH_SCRIPT=/opt/data/patch/apply-all.sh          # ★ 当前容器缺这项
  - HERMES_WRITE_SAFE_ROOT=/opt/data:/home/agent/.hermes
  - HERMES_LAZY_INSTALL_TARGET=/opt/data/lazy-packages
  - HERMES_DISABLE_LAZY_INSTALLS=1
  - HERMES_MEDIA_DELIVERY_STRICT=0                             # ★ 当前容器缺这项
  - HERMES_MEDIA_TRUST_RECENT_FILES=1                          # ★ 当前容器缺这项
  - HERMES_ALLOW_ROOT_GATEWAY=1
  - HERMES_WEB_UI_MANAGED_GATEWAY=1
```

`HERMES_WEB_UI_MANAGED_GATEWAY=1` 的后果：**Web UI 会托管 gateway**，`hermes gateway stop` 几秒内会被重新拉起
（新 PID）。要停机做维护只能 `kill -9`，别指望 stop 能保持停机。

### 3.2 本地补丁（镜像层，重建必丢 → 必须重打）

补丁目录 `/opt/data/patch/`（在卷里，不丢），一键脚本 `apply-all.sh`：

```bash
bash /opt/data/patch/apply-all.sh      # 幂等、永不阻断启动、日志 /opt/data/patch/apply-all.log
```

包含**五处**补丁（三处企微 + 两处 Web UI），每处都自带自检（测试全过才算成功）：

| 补丁脚本 | 改的文件（镜像层） | 作用 |
|---|---|---|
| `wecom_typing_notice_patch.py` | `/opt/hermes/plugins/platforms/wecom/callback_adapter.py` | 企微「⏳ 正在思考处理，请稍候…」—— callback 模式没有 typing 接口，改成「超过 N 秒推一条真消息」，默认 20s，`platforms.wecom_callback.extra.typing_notice_after` 可调、`<=0` 关闭 |
| `wecom_crypto_receiveid_patch.py` | 企微 `wecom_crypto.py` | 群机器人回调载荷里 `receive_id` 为空 → 原检查 `receive_id == corp_id` 必失败（后台显示 403）。改成空值放行、非空错值照样拒，签名校验不动 |
| `wecom_hook_patch.py` | 同上（复用 8645 监听） | 入站 webhook `POST|GET /hook?token=…`，给 PC 端 DSH「企业微信群机器人」推送用 |
| `webui_default_profile_patch.py` | `/app/dist/client/assets/js/index-<hash>.js` | **新会话默认 profile 跟随服务端 `active_profile`**，不再粘在 `default`（见 3.2.1） |
| `webui_endpoint_context_patch.py` | `/app/dist/server/index.js` | **上下文长度兜底改读服务器数据**，不再硬编码 256000（见 3.2.2） |

自检命令（也可单独跑）：

```bash
/opt/hermes/.venv/bin/python /opt/data/patch/test_wecom_typing_notice_patch.py
/opt/hermes/.venv/bin/python /opt/data/patch/test_wecom_crypto_receiveid_patch.py
/opt/hermes/.venv/bin/python /opt/data/patch/test_wecom_hook_patch.py
/opt/hermes/.venv/bin/python /opt/data/patch/test_webui_default_profile_patch.py
/opt/hermes/.venv/bin/python /opt/data/patch/test_webui_endpoint_context_patch.py
```

**三处企微补丁只在 gateway 启动时导入 → 打完必须重启 gateway。**
**两处 Web UI 补丁不改 gateway**：前端那处要**浏览器硬刷新**；服务端那处（`index.js`）要**重启 Web UI 进程**（PID 1）才生效。

⚠️ **实测教训（2026-10-14）**：`apply-all.log` 里上一次运行是 **2026-10-05** ——
也就是说中途容器被**重建**过，`/opt/hermes` 与 `/app` 的镜像层被还原，**三处企微补丁静默消失了 9 天**，
没人发现（因为补丁失效只是少了「思考提示」和群机器人回调，不影响主链路）。
`HERMES_PATCH_SCRIPT` 至今**不在容器 env 里**（见 3.1），所以**不会自动重打** ——
每次重建后必须手动 `bash /opt/data/patch/apply-all.sh`。

#### 3.2.1 Web UI 新会话默认 profile 补丁（`webui_default_profile_patch.py`）

**症状**：新建对话落在 `default` profile，而不是 `active_profile`（本部署 = `enderiose`）。

**根因（三层，都在客户端）**：

1. `chat-<hash>.js` 握手时发 `query: { profile }`，值 = `profilesStore.activeProfileName || "default"`；
2. `index-<hash>.js` 里 profiles store 的 `activeProfileName` **只认浏览器 localStorage**
   （键 `hermes_active_profile_name`），localStorage 为空时回退到 **`profiles[0]`**；
3. 服务端 `z0()`（`/app/dist/server/index.js`）把 `default` **硬编码排在第一** ——
   于是「localStorage 空 / 被清过 / 新设备 / 重装 App」必然落到 `default`。
   服务端本身是对的：`/api/hermes/profiles` 会把真·active 标成 `active:true`，**但前端把这个标记丢掉了**。

**补丁**（改 profiles store 两处，`+51` 字节）：

| 原 | 改后 |
|---|---|
| `let S=e.value.find(P=>P.name===x)??null;` | `let S=(x&&x!=="default")?e.value.find(P=>P.name===x)??null:null;` |
| `!S&&e.value.length>0&&(S=e.value[0],` | `!S&&e.value.length>0&&(S=e.value.find(P=>P.active)\|\|e.value[0],` |

效果：存储值缺失**或为 `default`** → 取服务端标 `active` 的那个 profile。
自检是**行为级**的：把补丁后 bundle 里的真实片段抽出来交给 node 跑，断言
`fresh(空 localStorage)=enderiose`、`stuck(localStorage="default")=enderiose`、`explicit=enderiose`。

**注意**：
- bundle 名带内容哈希（`index-B9aOBwk9.js`），**Web UI 升级后哈希会变** → 补丁脚本按锚点 glob `index-*.js` 定位，不写死文件名；
- 打完要**浏览器硬刷新**（Ctrl/Cmd+Shift+R），前端 bundle 有缓存；
- 副作用：`default` 仍可在当前会话里选，但**刷新后不保留** —— 本部署不用 `default`，这是有意的。

#### 3.2.2 Web UI 上下文长度兜底读服务器数据补丁（`webui_endpoint_context_patch.py`）

**症状**：模型上下文长度兜底值恒为 **256000**，跟中转实际声明的不一致。

**根因**：`getModelContextLength`（`/app/packages/server/src/modules/hermes/services/models/context.ts`，
编译进 `/app/dist/server/index.js` 的 `function HZ(t)`）解析顺序是
DB `model_context` → `providers.<p>.models.<m>.context_length` → `custom_providers` →
`model.context_length` → `models_dev_cache.json` → **硬编码 `DEFAULT_CONTEXT_LENGTH = 256_000`**。

所以**只要模型没写进 config.yaml、又不在 models.dev 目录里，就静默拿 256000**。
而中转 `GET /v1/models` **每个模型都带 `context_length`**（2026-10-14 实测 workbuddy 79/79 全带：

| 模型 | 中转声明 |
|---|---|
| `global:deepseek-v4.1-flash` | ctx 1000000 / max_out 128000 |
| `cn:step-5-preview` | ctx 1000000 / max_out **64000** |
| `cn:hy4-preview` | ctx **960000** / max_out 64000 |
| `cn:auto` | ctx 256000 / max_out 32000 |

**Hermes agent 早就读了这份数据**（`agent/model_metadata.py` 的 `_CONTEXT_LENGTH_KEYS` 含
`context_length`），**只有 Web UI 不看**。

**补丁**（插入一个 helper + 改一处调用点，`+803` 字节）：

| 原 | 改后 |
|---|---|
| `return c||p_(e)}` | `return c||_epc(a,o,l)||p_(e)}` |

`_epc(cfg, model, provider)`：从 config 里取该 provider 的 `base_url`（支持 `custom:<name>` 形式，
回退到 `model.base_url`）→ 归一化（`strip().rstrip("/")`）→ **同步**读
`<hermes_home>/cache/endpoint_context_lengths.json` → 返回 `models[model].context_length`（模型名大小写不敏感）。

> **为什么不直接用 agent 的 `cache/endpoint_model_metadata.json`**：agent 的 `is_local_endpoint()`
> 把 RFC-1918（含 `192.168.2.6`）判为本地，而「local endpoints are never memoized」——
> 本部署的中转永远不会落进那个文件。所以由 `sync_model_context.py` 自己写一份。

**自检是行为级的**：把补丁后 bundle 里的真实 `_epc` 抽出来交给 node，配一个临时 memo，断言
`direct=960000 / custom:workbuddy=960000 / 大小写不敏感=960000 / 无 context_length=None / 未知模型=None / 无 base_url=None / 无 provider=None`。

**生效条件**：`index.js` 是服务端代码 → **要重启 Web UI 进程（PID 1）**。注意 PID 1 就是 Web UI 本身，
重启它 = 容器重启（`restart: unless-stopped` 会自动拉起），会短暂断开网页端与网关。

#### 3.2.3 上下文长度同步器（`sync_model_context.py`，不是补丁，是工具）

```bash
# 干跑看差异
/opt/hermes/.venv/bin/python /opt/data/patch/sync_model_context.py --dry-run
# 实际执行（LAN 中转要绕代理，NO_PROXY 必须带 192.168.2.6，否则 urllib/requests 会走 7890 拿 502）
NO_PROXY="localhost,127.0.0.1,192.168.2.6" \
  /opt/hermes/.venv/bin/python /opt/data/patch/sync_model_context.py
```

两件事：

1. **写 memo** `<hermes_home>/cache/endpoint_context_lengths.json`
   （`{归一化 base_url: {at, models: {id: {context_length, max_completion_tokens}}}}`）—— 供 3.2.2 的补丁读；
2. **校准 config.yaml** 里**已注册**模型的 `context_length` / `max_tokens`（只改值，不增删模型；写前自动备份 `.bak-ctxsync-*`）。

`apply-all.sh` 每次启动都会带 `timeout 180` 跑一遍（失败不阻断）。

**实测校准结果（2026-10-14）**：`workbuddy/cn:step-5-preview.max_tokens` `128000 → 64000`
（两个 profile 都改了）—— 原先写 128000，中转只允许 64000，**超过 64k 的请求会被上游拒**。
`context_length` 两个已注册模型本来就对（都是 1000000）。

**注意**：`traecode`（`192.168.2.6:8000`）的 `/v1/models` **不带** `context_length`（88 个模型，0 个带），
所以那条链路同步不到东西，只能靠 config 手写值。

`/opt/data/patch/bridge_pool.py` 是给旧镜像的替换型补丁，**当前镜像不需要**，`apply-all.sh` 里显式跳过。

### 3.3 系统依赖：JDK + Android SDK（只在需要出 APK 时）

两者都在镜像层外，重建即丢：

```bash
apt-get install -y --no-install-recommends default-jdk-headless        # JDK 21，含 javac/keytool
curl -sSL -o /tmp/clt.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
python3 -c "import zipfile;zipfile.ZipFile('/tmp/clt.zip').extractall('/tmp/clt')"
mkdir -p /opt/android-sdk/cmdline-tools && mv /tmp/clt/cmdline-tools /opt/android-sdk/cmdline-tools/latest
chmod +x /opt/android-sdk/cmdline-tools/latest/bin/*    # python 解压不带执行位，漏了 Permission denied
/opt/android-sdk/cmdline-tools/latest/bin/sdkmanager --sdk_root=/opt/android-sdk --licenses
/opt/android-sdk/cmdline-tools/latest/bin/sdkmanager --sdk_root=/opt/android-sdk "build-tools;34.0.0" "platforms;android-34"
```

出包：`ANDROID_SDK=/opt/android-sdk bash /home/agent/.hermes/hermes-app/build.sh`
（工程在卷里：`/home/agent/.hermes/hermes-app/`，版本真源 `VERSION`，产物 `shared/HermesMobile.apk`；
细节见 `/home/agent/.hermes/hermes-app/docs/CONTEXT.md`）

### 3.4 Playwright + cairosvg（venv 包，重建必丢）

> ⚠ **这两个包是绑在一起装的，别再只记 Playwright。** 2026-10-05 容器重置后一起补装过一次，
> 2026-10-14 又整对丢光（`/opt/hermes/.venv` 里两个都 `ModuleNotFoundError`，浏览器目录反而还在）。
> 一条命令装回两个，别分两次找。

| 包 | 版本 | 干什么用 | 谁在用 |
|---|---|---|---|
| `playwright` | 1.63.0 | 无头 Chromium 自动化 | `mp_qr_login.py`（二维码登录中继）、`mp_ip_update_ui.py`（改 IP 白名单）、`mp_appsecret_reset.py`、`devconsole_*.py` |
| **`cairosvg`** | 2.9.1 | **SVG → PNG 栅格化**（PIL 读不了 SVG） | `fetch_logos_lobehub.py`、`banner_v2.ensure_icon()`（补当日新厂牌 logo）、`brand-banner-composition` 技能 |

**`cairosvg` 是"把 lobehub 的 SVG logo 转成 PNG 存本地"那一步。** 它一缺，当天出现**新厂牌**时
`banner_v2.ensure_icon()` 返回 `None`、banner **静默少一个 logo**（不报错、不告警），
生图链和已缓存的 logo 全不受影响 —— 所以能好几周都发现不了。这就是它必须和 Playwright 记在一起的原因。

**为什么会丢**：`/opt/hermes/.venv` 是**镜像层**，重建 / 换镜像就还原。浏览器在
`/opt/hermes/.playwright`（也是镜像层，但实测**比包更常存活** —— 现存 `chromium_headless_shell-1243`、`ffmpeg-1011`）。

**装回（2026-10-14 实测 105s）**：

```bash
PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright \
  uv pip install --python /opt/hermes/.venv/bin/python playwright==1.63.0 cairosvg==2.9.1
```

> **venv 里没有 `pip`**（`No module named pip`，`/opt/hermes/.venv/bin/pip` 也不存在）——
> **别用 `python -m pip`**，用 `uv`（在 `/usr/local/bin/uv`，0.11.6）。
> 环境变量 **`PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright`** 必须设，否则浏览器装到 `~/.cache` 白装。

浏览器二进制若也丢了：

```bash
PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright \
  /opt/hermes/.venv/bin/python -m playwright install chromium
/opt/hermes/.venv/bin/python -m playwright install-deps chromium   # 缺 libnss3 / libatk 起不来
```

**自检（全过才算好，2026-10-14 实测全绿）**：

```bash
export PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright
/opt/hermes/.venv/bin/python - <<'EOF'
import cairosvg
print("cairosvg", cairosvg.__version__)
print("svg->png", len(cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"/>')), "bytes")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox"])
    pg = b.new_page(); pg.goto("http://127.0.0.1:6060/", timeout=20000)
    print("chromium title:", pg.title()); b.close()
EOF
# 补图全链路（CDN 拉取 → 栅格化 → 存盘）
/opt/hermes/.venv/bin/python /opt/data/mp/fetch_logos_lobehub.py openai
```

**用途**：`mp_qr_login.py`（无头版二维码中继）—— 生成高清二维码（3x + 40px 静默区）→ 局域网
`http://192.168.2.6:8899/mp_login_qr.png` → 等扫码 → 把会话写进 `.env` 的 `MP_COOKIE` →
`--explore` 侦察控制台接口 → `--then-set IP` 尝试写 API IP 白名单。8899 **按需启动**，平时不监听。
脚本 master 在 `/opt/data/mp/mp_qr_login.py`（新版，已迁到微信开发者平台登录页 + stable_token），
profile `scripts/` 里那份是**旧版**（两副本不一致，见 3.8 的「双副本缺口」）。

### 3.5 中文字体：黑体（思源黑体 / Noto Sans SC）

**公众号生图 / 配图 / banner / 封面里的所有文字，一律用黑体** —— 即 **思源黑体 / Noto Sans SC**
（`Source Han Sans` = `Noto Sans CJK SC` = `思源黑体`，同一套字体，别当成三种）。
**绝不让生图模型自己写字**（必出伪中文），文字一律由本地 PIL 用黑体渲染。

- **真身（卷内，不丢）**：`/opt/data/mp/fonts/NotoSansSC-{Regular,Bold,Medium}.otf`（各约 8.3MB）
  来源 `github.com/notofonts/noto-cjk` 的 `Sans/SubsetOTF/SC/`
- **系统目录（镜像层，重建丢）**：`/usr/share/fonts/opentype/noto-sc/` —— 只是拷贝

#### 卷内字体也没了 → 重新下载黑体

```bash
BASE=https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/SC
mkdir -p /opt/data/mp/fonts
for f in Regular Bold Medium; do
  curl -sSL -o "/opt/data/mp/fonts/NotoSansSC-$f.otf" "$BASE/NotoSansSC-$f.otf"
done
ls -l /opt/data/mp/fonts/     # 每个应约 8.3MB；<100KB 视为下载失败
```

> 三个 URL 2026-10-14 实测 HTTP 200。`hei_font.py` 在项目卷/系统目录**都缺时会自己按同一 URL 自动重下**，
> 所以正常不用手跑；这条是网络/权限出问题时的兜底。

#### 补系统目录 + 自检

```bash
mkdir -p /usr/share/fonts/opentype/noto-sc
cp /opt/data/mp/fonts/NotoSansSC-*.otf /usr/share/fonts/opentype/noto-sc/
fc-cache -f
/opt/hermes/.venv/bin/python /opt/data/mp/hei_font.py     # regular/bold/medium 三个都 OK 才算好
```

`hei_font.py` 解析顺序 = 项目卷 → 系统目录 → 自动重下 → （全挂才）回退 `wqy-zenhei.ttc` + stderr WARN。
**踩过的坑**：容器重建清掉 `/usr/share/fonts/` 后，旧写法**静默**换成文泉驿正黑，用户一眼看出「又用了旧字体」。
所以画字脚本**只从 `hei_font` 取字体**（`sys.path.insert(0,"/opt/data/mp")` → `from hei_font import FP, FP_BOLD`），
**别再自己拼字体路径**。
**`hei_font.py` 只在 `/opt/data/mp/` 一份，没有 profile 副本** —— 画字脚本（banner_v2 / make_cover /
banner_variants / novel/*）都在 `/opt/data/mp/` 下跑，不依赖 profile scripts 那份。

### 3.6 网络服务与端口

#### 容器内监听（2026-10-14 实测）

| 端口 | 服务 | 说明 |
|---|---|---|
| **8645** | hermes gateway HTTP（PID = `hermes gateway run`） | `/wecom/callback`（企微**加密**回调，`msg_signature` + AES XML）、`/hook`（DSH 群机器人**明文** JSON 推送）、`/health` |
| **6060** | Hermes Web UI（PID 1 = `node /app/dist/server/index.js`） | 由它托管 gateway（`HERMES_WEB_UI_MANAGED_GATEWAY=1`） |
| 8651 | Web UI 预览前端 | compose 里映射，**当前无监听** |
| 56121 | xAI OAuth 回调 | compose 里映射，**当前无监听** |
| 8899 | `mp_qr_login.py` 二维码中继 | **按需启动**；平时不监听 |

**`/wecom/callback` 和 `/hook` 是两套协议，别混**：前者只吃企业微信加密回调（智能机器人/自建应用），
后者吃明文群机器人 JSON（DSH 推送用）。DSH 的「企业微信群机器人」渠道**不能**填 `/wecom/callback`。

#### 公网链路（容器外，只能在宿主/外部验）

- Lucky 反代后端指向 `192.168.2.6:8645`
- 公网入口：`https://hp.286115.xyz:88/hook?token=<WECOM_HOOK_TOKEN>`（IPv4）
- `h.286115.xyz` 是 **IPv6-only**，容器 bridge 里没有 IPv6 → **在容器里 curl 这两个域名失败是正常现象**，
  别据此判故障。`hp.286115.xyz` 的 IPv4 从容器回环还要路由器 NAT 回环，同理。
- **端口发布验证（容器内可做）**：`curl http://192.168.2.6:8645/health` 与 `http://192.168.2.6:6060/`
  都回 200 = bridge 端口映射正常（实测 10-14 ✓）。

#### 出站依赖（上游中转与 API）

| 用途 | 地址 | 凭据（`.env` 键名） |
|---|---|---|
| 主模型中转（WorkBuddy） | `http://workbuddy2api:7863/v1`（容器名；**本容器解析不到 → 靠 /etc/hosts 钉网关 172.20.0.1**，见下） | key 内联在 `config.yaml` |
| 降级中转（TraeCode） | `http://192.168.2.6:8000/v1`（**局域网**） | `TRAE_API_KEY` |
| 404 Found | `https://api.404-found.uk/v1` | `404_API_KEY` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `ZHIPU_API_KEY` |
| DeepSeek | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| 阿里百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `QWEN_API_KEY` |
| 官方 Gemini | Google AI Studio | `GEMINI_API_KEY` + `GOOGLE_API_KEY`（**两个名字都要写**：Studio 只认前者、CLI 只认后者） |
| 生图 songbey | `https://draw.songbey.com/v1` | `SONGBEY_API_KEY` |
| 生图 腾讯 TokenHub | `https://tokenhub.tencentmaas.com/v1/...` | `HY_API_KEY` |
| 生图 qwen | `https://maas.qianwenaiapi.com/api/v1/...` | `QWEN_API_KEY` |
| 公众号 API | `https://api.weixin.qq.com` | `WX_APPID` / `WX_APPSECRET` / `MP_COOKIE` |
| 企微 | `https://qyapi.weixin.qq.com` | `WECOM_CALLBACK_*` / `WECOM_HOOK_*` |
| GitHub | `https://api.github.com` | `GH_PAT_TOKEN` |

**代理（PID 1 env，实测）**：`HTTPS_PROXY=http://192.168.2.2:7890`，
`NO_PROXY=localhost,127.0.0.1,192.168.2.0/24,.aliyuncs.com`。
→ 两个局域网中转（workbuddy / traecode）与 DashScope 走直连，其余公网 API 走 7890 代理。

**两个易踩的坑**：
1. **出口 IP 会变** → 公众号 API IP 白名单要跟着改。`mp-ip-whitelist-guard`（每 10 分钟）自动探活并告警。
   无损取当前出口 IP：真 AppID + 假 secret 调 `/token`，`40164` 的 errmsg 会回显微信看到的 IP。
2. **`api.404-found.uk` 挂在 Cloudflare 后面**，不带 User-Agent 的请求被按浏览器签名拒掉
   （`HTTP 403 error code: 1010`）。urllib 默认 UA 必被拒，探针/脚本要显式带 UA（`llm_chain_guard.py` 已统一加）。

#### 自检脚本

```bash
# 企微 8645 往返（真实 token/AES 做签名校验；全 PASS = 容器侧完好，问题必在 Lucky/公网侧）
/opt/hermes/.venv/bin/python /home/agent/.hermes/profiles/enderiose/scripts/check_wecom_8645.py

# 模型链路探活（按优先级探主通道 → fallback 链；--report 只打印不改 state 不告警）
/opt/hermes/.venv/bin/python /opt/data/mp/llm_chain_guard.py --report --wait 45 --retries 2

# 企微 hook 探针
/opt/hermes/.venv/bin/python /opt/data/mp/check_wecom_hooks.py
```

### 3.7 图标 / logo 本地仓库（都在卷里，重建不丢，只需校验）

**位置与优先级**：

1. `/opt/data/mp/logos_override/<name>.png` —— **最高优先**，人工定版的正确 logo
   现存（实测）：`amazon.png`、`amd.png`、`axera.png`、`globalfoundries.png`、`tsmc.png`、`tsmc1.png`
2. `/opt/data/mp/assets/logo_<厂商>.png` —— 35 个条目（30 个 `.png` + `_archive/` + 4 个 `.svg` 源图留证），含
   `logo_amd_baseline.png`（**AMD 唯一正源**，用户给的基线图）、
   `logo_globalfoundries_mark.png`（方形徽标，角标用；宽版 wordmark 缩到小角标会糊）、
   `logo_{anthropic,apple,axera,bytedance,capcom,deepseek,gemini,globalfoundries,grok,meta,microsoft,mistral,nvidia,openai,qwen,tsmc,amazon}.png`、
   `law_cover{,_alt,_c,_d}.png`（**已停用**，法律压轴取消）、
   `rock_cover_a~f.png`（摇滚篇封面，按日轮换 `(date.toordinal()+1) % 6`）
3. lobehub 在线缓存（`logos_lh/`）—— 最低优先，缺图时才拉

**工具**（都在 `/opt/data/mp/`）：

```bash
import_logo.py <name> <src>        # 本地文件或 URL；白底自动抠透明、颜色不改、--keep-src 存源图；统一进正方形画布
fetch_logos_lobehub.py             # 从 lobehub 批量取，输出同样统一进正方形画布
make_logo_amd.py                   # 由 assets/logo_amd_baseline.png 生成 logos_override/amd.png
fetch_logos.py                     # 旧的通用抓取
```

**本地 lobehub 缓存清单（2026-10-14 实测，全在卷里，重建不丢）**：

| 目录 | 内容 |
|---|---|
| `/opt/data/mp/logos_lh/*.png` | 27 个单色 PNG（含 `_montage` / `_zhipu_zai_check` 两张对照图）——2026-10-17 实测 |
| `/opt/data/mp/logos_lh/color/*.png` | 16 个品牌原色 PNG（2026-10-17 实测） |
| `/opt/data/mp/logos_lh/svg/*.svg` | 41 个原始 SVG（CDN 落盘留证；`fetch_logos_lobehub.py` 命中缓存就直接读它，不再联网） |

厂牌：`openai claude anthropic gemini github google grok zai qwen deepseek kimi doubao meta microsoft mistral nvidia huawei tencent bytedance moonshot apple suno zhipu`

⚠ **补新厂牌依赖 `cairosvg`**（装法见 **3.4**）—— `fetch_logos_lobehub.py` 与 `banner_v2.ensure_icon()`
都靠它把 SVG 栅格化成 PNG。包一丢，**新厂牌会静默补不上**（`ensure_icon()` 返回 `None`、banner 少一个 logo，
不报错不告警），已缓存的 logo 不受影响 —— 所以这个故障能藏好几周。

**定版规则（用户原话，别再自行发挥）**：
- 找不到的 logo 可以自己搜，**但必须本地存档**（`logos_override/` 或 `assets/`），不许只留 URL
- **一律优先正方形**；原版不是正方形也要放进正方形画布（透明底、内容居中）
- 多 logo 厂家（如 AMD）优先选**多色渲染的正方形**
- 原版确实没有正方形，才退回用原版
- **AMD 不许重排、不许改色**，用户给了基线图就以基线图为准（要换就换基线图重跑 `make_logo_amd.py`）
- lobehub 没有的（实测 `spacex`、`amd` 404）自动跳过、不占 banner 名额

**banner 厂商名单**：主日报 banner 走 `banner_v2.py` 本地合成（**不经生图链**），
`--topics <当日内容里出现的厂商>`：9 个默认底盘
（openai / claude / anthropic / **deepseek** / github / zai / grok / qwen / **gemini**）+ 当日新主题，
最多 11 家；新主题 > 2 家就按底盘顺序从后往前替换。**别再自己拼 `--logos`。**

#### LobeHub Icons 本地方案（离线优先，2026-10-17 补录）

> 上游 skill 原文：`https://lobehub.com/icons/skill.md`（`@lobehub/icons`，200+ AI 厂牌 SVG，npm 包 + CDN 静态资源）。
> 本部署**不按上游的 React 组件用法**，而是把图标源整体本地化：**先查本地缓存，命中就不联网**。

**解析优先级（`banner_v2.ensure_icon(slug)` 与 `fetch_logos_lobehub.py` 同此链）**：

```
logos_override/<slug>.png      ← 人工定版，最高优先
  → logos_lh/svg/<slug>.svg    ← 原始 SVG 缓存（>100B 即命中，直接栅格化，不联网）
    → logos_lh/<slug>.png      ← 已栅格化单色（纯黑 + alpha，bbox 裁切，宽 1200）
      → logos_lh/color/<slug>.png
        → unpkg @lobehub/icons-static-svg → registry.npmmirror.com 回退（才联网）
```

**本地 slug 真源文档（离线直接读，别重抓上游）**：

| 文件 | 内容 |
|---|---|
| `…/skills/creative/brand-banner-composition/references/lobehub-icons.md` | **本地整理版真源**：slug 清单、变体可用性、404 清单、错版记录、合成规范 |
| 同目录 `upstream-lobehub-skill.md` | 上游 skill 正文副本（React 组件 / `toc` / `IconType`），本地读 |
| `/opt/data/mp/references_lobehub-skill.md` | 上游 skill 正文的另一份副本 |

**本地化后固化的踩坑知识（本地方案的核心价值，别再靠联网试错）**：

- **变体可用性**：`-color` 有/无的 slug 已登记。只有 mono 的（官方 mark 本就是黑色）：
  `openai`、`anthropic`、`grok`、`suno`、`zai`、`midjourney`、`runway`、`elevenlabs`、`ideogram`、`flux`。
- **确认 404（无 slug）**：`amd`、`spacex`。硬件 / 航天类常无条目。`amd` 已有 `logos_override/amd.png`
  （**只由用户基线图 `make_logo_amd.py` 生成，不许从别的 CDN 重建**）；`spacex` 仍未覆盖。
- **已知错版（必须用 override，别用 slug）**：`doubao` = 三瓣抽象结，**不是豆包 App 图标**
  （App 图标不在该套里，`doubao.com` 的 favicon / apple-touch-icon 回 JSON / HTML）。
  CN 消费级 App 的 mark 上线前要跟真实 App 图标比对，对不上就存 `logos_override/<slug>.png`。
- **不在该套里的名字**：`glm`（用 `zai` 或旧 `zhipu`）、`llama`、`stable-diffusion`、`iflytek`、`sensetime`；
  `chatgpt` 无独立 slug（= `openai`）。
- **备选源（仅当该套没有 slug 时）**：`cdn.jsdelivr.net/gh/gilbarbara/logos/logos/<slug>.svg`（通常带当前 mark）；
  `simple-icons` 可能仍是旧版 lockup，采用前要肉眼比对形状。**不许混用多套 CDN** —— 混着用会让一整条 logo 带看着不齐。
- **合成规范**：mono 是纯黑 + alpha，**按背景染色**（浅底染深 `#1A1E26` 左右、深底染白），不按变体名判断；
  按墨量（裁切后 mean alpha）缩放到 ~0.36，因子 clamp 到 0.8–1.45；**该 clamp 只治密度不治宽度** ——
  长 wordmark（aspect ≫ 1）要显式宽度因子（4.2:1 的 mark 约需 ×0.55）。
  近白的彩色 mark（如 `kimi`）在浅底要回退到染色 mono，否则 logo 静默消失。
- **栅格化**：`cairosvg.svg2png(..., output_width=1200, background_color=None)` 再 `getbbox()` 裁边；
  **不要给不透明画布**（`getbbox()` 会返回整幅，mark 缩在空框里）。

**缺口（2026-10-17 实测）**：

- 上游 skill 的 `reference/providers.md`（`https://lobehub.com/only-ai/skills/icons/reference/providers`）
  **本地没有副本** —— 需要 provider 全表时得联网抓，或补一份到 `references/`。
- `logos_lh/` 只覆盖用到的厂牌，**不是全量 200+**；新厂牌靠 `cairosvg` 按需栅格化
  （包一丢就静默补不上，见上一节 ⚠）。
- pi 侧（`~/.pi/agent/skills/`、`~/.agents/skills/`）**未安装** lobe-icons skill，仅 NAS 的 hermes 有。

### 3.8 profile 内容（在卷里，重建不丢，只需确认）

`HERMES_HOME=/home/agent/.hermes/profiles/enderiose`，profile 名 **`enderiose`**（用户日常环境，
`user_profiles.is_default=1`）。

#### ★ 2026-10-14：根 profile 已同步成 enderiose 的镜像

用户要求「两个 profile 都变成同一套」。现在 **`default` ≡ `enderiose`** —— 两个都带那套 provider 链 /
技能 / hooks，不再有「网页端选择器必须选 enderiose」这个坑。重建后若根又只剩裸环境（只有
`providers.workbuddy`、68 个技能），照下表补：

| 从 `profiles/enderiose/` | → 根 `/home/agent/.hermes/` | 备注 |
|---|---|---|
| `config.yaml` | 同名 | 只允许 2 处按 profile 区分，见下 |
| `.env` | 同名（**合并**，不覆盖） | 根原有 4 键必须保留：`API_SERVER_KEY`、`HERMES_CUSTOM_API_B_AI_API_KEY`、`HTTP_PROXY`、`NO_PROXY` |
| `SOUL.md` | 同名 | |
| `skills/`（22 顶层 / 105 个 SKILL.md） | **合并**（只加不删） | 根原本 18 顶层，合并后 22 |
| `memories/`、`scripts/`、`agent-hooks/` | 同名目录 | |
| `.no-bundled-skills` | 同名 | 让根也退出内置技能 seeding |

**刻意不一致的 3 处（别照抄对齐，重建时保留）**：

1. **`platforms.wecom_callback.enabled`：根 = `false`，enderiose = `true`。**
   企微回调端口**全局唯一**，两个 profile 同时 enabled 会互相顶掉（gateway 的 multiplexer 自己就这么提示）。
   企微 bot 跑在 **enderiose 的 gateway** 上（`wecom_callback: connected`）。
   要改成「企微走 default」= 两边对调。
2. **MCP 的 `HERMES_WEB_UI_PROFILE`：各写自己的 profile 名**（根 = `default`，enderiose = `enderiose`）。
   含义是「工具调用不指定 profile 时的默认 profile」，按 profile 取值；webui 启动时也会自动改成这个值。
3. **`hooks.pre_llm_call` 两份 config 都指向 `profiles/enderiose/agent-hooks/session_handoff.py`** ——
   **故意的单源**：hook 脚本自身路径无关（全走 `/opt/data/mp/`），指同一份就不会 drift。

**没同步的（故意的，别补）**：

- `cron/` —— enderiose 那 8 个任务（含 8:00 公众号草稿）复制过去会**两个 profile 各跑一遍 → 每天出两份草稿**。保持只在 enderiose 跑。
- `auth.json`、`state.db`、`sessions/`、`logs/`、`gateway*`、`platforms/` —— 运行态，不是配置。

**已知残留（不是 bug）**：根比 enderiose 多 2 个内置技能
（`devops/ai-provider-health-probe`、`software-development/hermes-session-history-search`）——
合并是"只加不删"，所以根是**超集不是全等**。要严格全等就删掉这两个。

**备份**：`/opt/data/backups/default-profile-sync-<ts>/`（config.yaml / .env / SOUL.md / skills.tar.gz）

**验证**：`hermes profile show default` 应显示
`Model: global:deepseek-v4.1-flash (custom:workbuddy)` + `Skills: 107`。
会话跑在哪个 profile 由 `HERMES_SESSION_PROFILE` 决定；**当前会话已固化的系统提示中途改不了，改完必须新开会话才生效**。

| 项 | 路径 | 说明 |
|---|---|---|
| 人格与行为规则 | `SOUL.md` | 直接性要求 + **文件放置规则** + 交接指路 |
| 记忆 | `memories/MEMORY.md`（3.2KB）、`memories/USER.md`（268B） | ⚠ **memory 与 session_search 都是关闭的**（`memory.memory_enabled: false`、`user_profile_enabled: false`），这两个文件在磁盘上但**不进提示词**。要长期记住的事实写 `HANDOFF.md`，别只写这里 |
| 技能池 | `skills/` | **105 个 SKILL.md**（2026-10-14 实测），含 `social-media/wechat-mp-draft-pipeline`（公众号管线）、`devops/hermes-state-db-recovery`、`hermes-cron-reminder-jobs` 等 |
| 交接注入 hook | `agent-hooks/session_handoff.py` + `/opt/data/mp/handoff_rules.json` | `config.yaml` 的 `hooks.pre_llm_call` 调用；按话题分块注入 HANDOFF.md / SYNC.md 的对应小节，**每个新会话首轮自动注入** |
| 管线脚本 | `scripts/`（31 个 .py/.sh + 2 个 `.bak`） | cron 从这里跑；**与 `/opt/data/mp/` 的副本必须保持一致**（见下） |
| 配置 | `config.yaml` | provider 链、平台、hooks、MCP —— 见 3.10 |
| 凭据 | `.env`（`0600`） | 见 3.11 |
| 会话库 | `state.db` | 见第 5 节「已知故障」 |
| 交接文档 | `/opt/data/mp/HANDOFF.md`（96KB）、`SYNC.md`（33KB）、`TOPICS.md`、`STYLE.md` | **跨会话记忆的真源** |

**双副本规约与已知缺口（2026-10-14 实测 md5）**：管线脚本在 `/opt/data/mp/*.py` 与
`profiles/enderiose/scripts/*.py` 各一份，**改完必须 `cp` 同步**。

已验证一致：`mp_publish.py`、`mp_genimg.py`、llm_chain_guard.py`、`mp_review_notify.py`、`banner_v2.py`、
`songbey_genimg.py`、`hy_genimg.py`、`qwen_genimg.py`、`mp_reuse.py`、`mp_newsimg.py`、`mp_badge.py`、`mascot_compose.py`。

**已知不一致（cron 跑的是 `scripts/` 那份，注意别照旧版判断行为）**：
- `mp_qr_login.py` —— `scripts/` 是旧版（仍走 `mp.weixin.qq.com` 登录页 + `/token`）；
  master 新版已迁到微信开发者平台 + `/stable_token`。
- `mp_ip_guard.py` —— 同上，`scripts/` 是旧版（用 `/token` 会把缓存 token 顶死，一天 3 次 40001）；
  master 已改 `stable_token`。

**没有 profile 副本的**：`hei_font.py`、`import_logo.py`、`make_logo_amd.py`、`fetch_logos*.py`、
`news_freshness.py`、`topic_dedup.py`、`check_quote_len.py`、`check_article_images.py`、`check_captions.py`、
`mp_review_notify.py`（有）、`fetch_article.py`、`grab_page_imgs.py` 等 —— 这些只在 `/opt/data/mp/` 跑。

### 3.9 定时任务（在 `$HERMES_HOME/cron/jobs.json`，重建不丢）

| job id | 名称 | 调度 | 类型 | 说明 |
|---|---|---|---|---|
| `71d7927de917` | `mp-daily-draft-prep` | `0 8 * * *` | **agent** | 出 4 图文草稿 + 写审阅文件 `pending/YYYY-MM-DD.md`；model `global:deepseek-v4.1-flash` / provider `custom:workbuddy`；`deliver=local`（成功静默）、`failure_deliver=local`；兜底信号靠 8:30 那条 |
| `525c24df5727` | `mp-daily-review-notify` | `30 8 * * *` | no_agent | `script=mp_review_notify.py`；三态框架（完成 / 执行中 / 失败），数据源 `cron/executions.db` + `state.db` 的 cron 会话 + `drafts_log.jsonl`；`deliver=wecom_callback` |
| `c4913edd3a28` | `gateway-online-notify` | `* * * * *` | no_agent | `script=gateway-online-notify.sh`；gateway PID/启动时间变化时推「我上线了」到企微 |
| `648ba6bdaee6` | `mp-ip-whitelist-guard` | `*/10 * * * *` | no_agent | `script=mp_ip_guard.py`；探测公众号 API 出口 IP 白名单，失败必告警（含二维码链接），状态 `state/ip_guard.json` |
| `a7474e8d1c70` | `llm-chain-guard` | `*/30 * * * *` | no_agent | `script=llm_chain_guard.py`；主通道 + 链逐条探活；全通静默、降级只在状态变化时告警一次、全挂立即告警并每小时重报；状态 `state/llm_chain_guard.json` |
| `ec68b7181dc8` | `pat-rotate-reminder` | `0 9 * * *` | no_agent | `script=pat_rotate_reminder.sh`；**30 天节奏写在脚本里**（读 `/opt/data/.gh_pat_rotate_next.txt`），未到期静默；顺带现场体检 token（`GET /user` + 写探针） |
| `2b911bef6500` | `reload-gateway-stale-timeout` | every 2m | no_agent | **disabled** |
| `c3979ab45e5a` | `reload-gateway-channel-pin` | every 2m | no_agent | **disabled** |

**易踩**：
- 任务只存在于 **enderiose** profile。`/home/agent/.hermes/cron/`（default）里没有任何任务；
  Web UI 的 Jobs 页按当前 profile 取数，**profile 选错就是空列表**，别据此判定任务丢失、更别重建
  （会多一个 8:00 任务，每天出两份草稿）。
- **改 cron prompt 千万别用 `hermes cron edit <id> --prompt "$(cat file)"`** —— 若上一步生成 file 的命令失败，
  `$(cat ...)` 展开成空串 → prompt 被清空。规约：走 `python -c` + `from cron import jobs as cron_jobs`
  的 API（`use_cron_store(HOME)` → `update_job`），锚点先 `assert prompt.count(anchor)==1`，
  改完立刻 `cp` 一份到 `/opt/data/mp/state/daily_prompt_backup_<date>.txt`。
- `hermes cron run` 补跑**必须 background=true**：前台跑会被超时杀掉，执行落 `unknown` 且半途工作丢失。
- manual `cron run` 对 `wecom_callback` 投递**必失败**，只能等 builtin tick。

### 3.10 provider 链 与 生图链

#### 模型链（`config.yaml`）

- **主模型**：`model.default = global:deepseek-v4.1-flash`，`model.provider = custom:workbuddy`
  （base `http://workbuddy2api:7863/v1`，**key 内联在 config.yaml**，自足、不依赖 `.env`；2026-10-17 由 LAN IP 改成容器名，坑：`workbuddy2api` 与本容器不在同一 docker 网络，docker DNS 解析不到 → `/etc/hosts` 钉本网络默认网关（`/opt/data/patch/apply-all.sh` 启动时自愈）；想根治就给 compose 加 `extra_hosts` 或接到同一 network）
- **企微 bot channel override** 同值（`platforms.wecom_callback.channel_overrides.<chat_id>`）
- **fallback 链**（`fallback_providers`，按序）：

| # | provider | model | 备注 |
|---|---|---|---|
| 1 | `custom:workbuddy` | `cn:step-5-preview` | 限免预览模型，**降级第一优先**；自身是 429 queue.waiting 常客 |
| 2 | `custom:traecode` | `deepseek-v4.1-flash` | `192.168.2.6:8000/v1`，`TRAE_API_KEY`；82 个模型但只有部分对本账号可用 |
| 3 | `custom:404found` | `deepseek-v4.1-flash` | `https://api.404-found.uk/v1`，`404_API_KEY`；**不带 `cn:` 前缀** |
| 4 | `opencode-free` | `space-bunny-free` | 免密钥；**实测已死**（403 FreeTierError），留着不占事 |
| 5 | `zai` | `glm-4.6` | `open.bigmodel.cn`，`ZHIPU_API_KEY`，200k |
| 6 | `deepseek` | `deepseek-flash` | `api.deepseek.com/v1`，`DEEPSEEK_API_KEY` |
| 7 | `alibaba-cn` | `qwen3.8-flash` | dashscope 直连，`QWEN_API_KEY`，1M |

**改法**：`hermes config set fallback_providers '<多行 YAML>'` —— **patch / 写文件工具会被拒**（config.yaml 属安全配置）。

**注册新模型必须同时登记上下文长度**，否则静默回退 256000（不报错）：
写进 `providers.<name>.models` 的 `context_length` / `max_tokens` / `reasoning`；
Web UI 侧另在 `model_context` 写两行（`custom:X` 和裸 `X` 都写）。
`/api/studio/sessions/context-length` **传 provider 时必须带 `custom:` 前缀**。

**上下文长度以「服务器数据」为准（2026-10-14 起）**：中转 `GET /v1/models` 对每个模型都返回
`context_length` / `max_output_tokens`，别再手写猜值 —— 写完注册项后跑一次
`/opt/data/patch/sync_model_context.py`（见 **3.2.3**）自动校准 config 并刷新 Web UI 补丁读的 memo。
对照实测：`global:deepseek-v4.1-flash` 1000000、`cn:hy4-preview` 960000、`cn:auto` 256000；
`cn:step-5-preview` 的 `max_tokens` 曾写错成 128000（中转只给 64000），已由该脚本改回。
`traecode` 的 `/v1/models` 不带 `context_length`，只能手写。

**验证 provider 时别信 CLI 的 stdout**：`hermes chat --provider <X>` 在 X 没凭据/不可解析时会**静默改用默认/链上第一条**，
照样打印正常回答。判定必须看 `logs/agent.log` 里的 `agent.turn_context: ... model=<X> provider=<Y>`。

**不要再往链上加的死通道**：`xiaomi`（401）、`alibaba-coding-plan-cn` / `openai`（无 key）、
`qwen token-plan`（key 已失效 401）。官方 Gemini **不要放进链**（Pro 档免费层 `limit:0`，flash 与 workbuddy 重叠）。

#### 生图链（`mp_genimg.py`，链尾无兜底）

```
songbey > hy > qwen-pro > qwen > zhipu
```

| # | 通道 | 契约 | 实测 |
|---|---|---|---|
| 1 | **songbey** | Songbey Draw 官方 `draw.songbey.com/v1`，模型 `gpt-image-2.5`，返回 b64_json；`--ref` 走 `/v1/images/edits`（multipart，`image` 字段重复 1~9 次） | standard 10 积分/张 ≈37s、hd 4K 超分 20 积分/张；size 只认 `1024x1024`/`1024x1792`/`1792x1024`，实际输出 ≈1.57MP，2.6:1 靠 fix_ratio 裁 |
| 2 | **hy** | 腾讯云 TokenHub `hy-image-v3.5-preview`，`POST tokenhub.tencentmaas.com/v1/wand/hunyuan-image/v35-generation`（Chat-Messages 协议） | 12s、原生 1536×591（无需裁）、0.15 元/张，最快最便宜 |
| 3 | **qwen-pro** | DashScope `POST maas.qianwenaiapi.com/api/v1/services/aigc/multimodal-generation/generation`，模型 `qwen-image-3.0-pro` | ≈111s，画面最干净但免费额度有限（10 张） |
| 4 | **qwen** | 同上，模型 `qwen-image-3.0` | ≈37s，**会写伪中文** |
| 5 | **zhipu** | `POST open.bigmodel.cn/api/paas/v4/images/generations`，模型固定 `glm-image`，**必带 `watermark_enabled:false`** | 40~50s，1440×720 → 裁 2.6:1 |

**已整段删除、别再试**：`pm`（pmcat.top，自 10-04 起回 `200 + HTML` 站点首页，2026-10-13 用户裁定「太坑」再次移出）、
`bunny` / WorkBuddy、`ly`（用户：「太丑了」）、算力站 `api.suanli.cn`（余额 0，402）、小米 MiMo（**只能文本/语音，无图像输出路由**）。

**关键纪律**：
- **`.env` 变量名 = `SONGBEY_API_KEY`**（曾误拼 `SONGBYE_API_KEY` → 读不到 key、链静默落到 hy）
- 免费验 key（不消耗积分）：`curl -H "Authorization: Bearer ***" https://draw.songbey.com/v1/dashboard/billing/credit_summary`
  → 200 + `{"total_granted":…,"total_available":…,"credits":…}`；错 key 回 401
- **`GET /v1/images/generations` 会回 `200 + HTML` 站点首页 —— 200 非 JSON 一律判为通道故障**
- **`.env` 只放 KEY，不放 BASE_URL**（用户裁定）：内部 base_url 一律明文写死在脚本常量里，
  这样 `.env` 丢失也不会「key + base 成套丢」
- 强制单通道：`GENIMG_PROVIDER=songbey|hy|qwen-pro|qwen|zhipu`（失败即退出，不回退）；
  `GENIMG_NO_FALLBACK=1` 只试第一条
- **⛔ 定稿子文章之前不许生图**：先把 4 篇正文定稿再给确认成篇的篇目生图；**候选选题一律不生成封面**
- 计费纪律：探测/验证类调用要克制，能用 401/参数校验验证的不要真出图

### 3.11 `.env` 凭据（24 个键名，值不在本文档）

文件：`/home/agent/.hermes/profiles/enderiose/.env`（`0600`，root 所有）。
**这是凭据的唯一来源**（容器进程环境里残留过旧 `WX_APPSECRET` 导致 40125，脚本已改成 .env 优先）。

```
# 模型
TRAE_API_KEY 404_API_KEY ZHIPU_API_KEY DEEPSEEK_API_KEY QWEN_API_KEY GEMINI_API_KEY GOOGLE_API_KEY
# 生图
SONGBEY_API_KEY HY_API_KEY PM_API_KEY          # PM_API_KEY 已无引用（pm 通道已删），留着无害
# 公众号
WX_APPID WX_APPSECRET MP_COOKIE
# 企微
WECOM_CALLBACK_CORP_ID WECOM_CALLBACK_CORP_SECRET WECOM_CALLBACK_AGENT_ID
WECOM_CALLBACK_TOKEN WECOM_CALLBACK_ENCODING_AES_KEY
WECOM_HOOK_TOKEN WECOM_HOOK_CHAT
# GitHub
GH_PAT_TOKEN
# 运行时
TERMINAL_ENV HERMES_API_CALL_STALE_TIMEOUT HERMES_LOCAL_STREAM_STALE_TIMEOUT
```

重建时**手工重新填这些值**（值不在任何文档里，需从旧容器/密钥库取）。
`GEMINI_API_KEY` 与 `GOOGLE_API_KEY` **两个名字都要写**（Studio 只认前者、CLI 只认后者）。

`/opt/data/.gh_askpass_enderiose.sh` —— git 推送用的 GIT_ASKPASS 助手，运行时从 `.env` 读 `GH_PAT_TOKEN`，
token 不进命令行、不写 `.git/config`：

```bash
GIT_ASKPASS=/opt/data/.gh_askpass_enderiose.sh GIT_TERMINAL_PROMPT=0 git push origin main
```

PAT 是 fine-grained，`Contents: Read and write`，**30 天轮换**（下次到期见 `/opt/data/.gh_pat_rotate_next.txt`）。
推之前先跑无损写探针（只创建悬空 blob，不动 ref）：

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer ***" \
  -H 'Content-Type: application/json' -d '{"content":"probe","encoding":"utf-8"}' \
  https://api.github.com/repos/<owner>/<repo>/git/blobs     # 201=可写 403=不可写 401=token 已失效
```

---

## 4. 弃用 Ekko（2026-10-14 用户裁定）

**「弃用 ekko」= 本地引用改走上游自带的兼容入口，不停功能、不改工具名。**
上游在同一目录放了两个转发壳（内容完全相同，sha256 `abcaa894…`）：

```bash
# /app/bin/hermes-studio-mcp.mjs
#!/usr/bin/env node
// Compatibility entry point for existing MCP configurations.
import './ekko-studio-mcp.mjs'
```

`hermes-web-ui-mcp.mjs` 与之同 hash。所以 `hermes-studio-mcp.mjs <mode>` 与 `ekko-studio-mcp.mjs <mode>`
**完全等价**（实测 `--version` 都输出 `ekko-studio-mcp v0.7.21`），功能零损失。

### 4.1 需要改的地方

**A. 两个 profile 的 `config.yaml` `mcp_servers`（共 8 条）**

| profile | 文件 | 条数 |
|---|---|---|
| enderiose | `/home/agent/.hermes/profiles/enderiose/config.yaml` | 4（api / browser / devices / use） |
| default | `/home/agent/.hermes/config.yaml` | 4 |

（每个文件里 `ekko-studio-` 字样共 12 处 = 4 条服务 ×（服务名 + 脚本路径 + `HERMES_MCP_SERVER_NAME`），
`grep -c` 数的是行数不是条数。）

每条把 `args` 里的 `/app/bin/ekko-studio-mcp.mjs` 改成 `/app/bin/hermes-studio-mcp.mjs`，
`env.HERMES_MCP_SERVER_NAME` 的 `ekko-studio-*` 改成 `hermes-studio-*`。
`command`（`/usr/local/bin/node`）、`HERMES_MCP_TOOLSET`、`HERMES_WEB_UI_MANAGED_MCP=1`、
`timeout: 1860`（仅 use）**都别动**。

**B. MCP 自动注入会把改名写回去 —— 这是最大的坑**

上游 bundle `/app/dist/server/index.js` 里有 bootstrap 自动注入（`j1t` → `S8n`），
每次 Web UI 启动都对**每个 profile**（`jr()` = `default` + `profiles/` 下所有目录）执行：

1. 把 `mcp_servers` 里任何 `hermes-studio-*` 名字**改名回 `ekko-studio-*`**
   （代码：`let I=s.replace(/^ekko-/,"hermes-")` 的反向迁移逻辑，目的是兼容旧配置）；
2. 用 `T1t()` 生成的**标准条目覆盖**这四个服务，而 `T1t` → `L8n(t, r)` 在 `r`（脚本路径）存在时
   用 `args:[<path>, <toolset>]`，路径来自 `_1t()` 候选表 —— 表里 `ekko-studio-mcp.mjs` 排在前，
   `A8n()` 取**第一个存在的**，所以写进 config 的仍然是 **`/app/bin/ekko-studio-mcp.mjs`**；
3. 判定「是否已托管」看 `env.HERMES_WEB_UI_MANAGED_MCP === "1"` 或 `command` 在
   `f8n = {hermes-lan-peer-mcp, hermes-devices-mcp, hermes-web-ui-mcp, ekko-studio-mcp, hermes-studio-mcp}` 里；
4. `enabled: false` 或非托管条目 → 该条 `skipped`，**不改写、也不删**。

**结论：单纯改 config.yaml 会被下次启动的 autoinject 改回 `ekko-*`。** 三条可行路线：

- **路线 1（推荐）**：改 config 后，把 compose 环境变量
  **`HERMES_WEB_UI_DISABLE_MCP_AUTOINJECT=1`** 加上。`X8n()` 命中即整段跳过 autoinject，
  改名与覆盖都不会发生。代价：上游以后新增托管 MCP 不会再自动注入（当前只有这 4 个，影响为零）。
  这条路径下 config 想怎么写就怎么写（服务名 `hermes-studio-*`、路径 `hermes-studio-mcp.mjs` 都能留住）。
- **路线 2（唯一能留住非 ekko 路径、又不关 autoinject 的办法）**：
  **服务名保留 `ekko-studio-*`**，同时在 compose 里设
  **`HERMES_WEB_UI_MCP_BIN=/app/bin/hermes-studio-mcp.mjs`**。
  `_1t()` 的候选表里这个 env 排第一，`A8n()` 会选中它，于是 autoinject 每次写出的 args
  就是 hermes 壳（**实测模拟确认：这是唯一不被写回 ekko 路径的组合**）。
  config 的 args 可以照此写，也可以不写（反正会被覆盖成同样的值）。
  ⚠ 不要只改 config 的 args 而不设这个 env —— 下次启动必被覆盖回 `/app/bin/ekko-studio-mcp.mjs`。
  ⚠ 也不要把服务名改成 `hermes-studio-*` 就指望它活着：启动时 `S8n` 第一步就会把它**改名回 `ekko-studio-*`**。
- **路线 3（接受现实）**：什么都不改。`ekko-studio-mcp.mjs` 是镜像自带文件，
  重建后由上游镜像提供，不依赖任何本地补丁；工具名（`ekko_studio_*`）也不变，
  技能与历史脚本里的引用全部继续有效。**若「弃用」的诉求只是不让它碍事，这条路零成本。**

> 说明：路线 1 与路线 2 的环境变量都需重启 Web UI（PID 1）才生效。
> 改完必须**重启后复查** config.yaml 的 `mcp_servers` 是否还是目标形态 —— 补丁式改写只在启动时发生一次。

**C. 文档与技能里的 `ekko` 字样（不影响运行，纯可读性）**

- `agent-hooks/session_handoff.py` 第 68 行：「CLI 与 Ekko Studio 网页的 agent platform 都是 `cli`」
  → 可改成「Web UI」。
- 技能 `software-development/ekko-studio-api-integration/SKILL.md`（整篇讲 Ekko Studio API）
  —— 目录名与 frontmatter `name` 都是 `ekko-studio-api-integration`，
  **工具名 `ekko_studio_*` 不变的话这篇不用改**；要改名需同时改目录名 + `name:` 字段 + 所有引用。
- 其余命中只是散文里的「Ekko Studio」（android-apk-build / wechat-mp-draft-pipeline / hermes-provider-pool /
  hermes-gateway-platforms / hermes-session-handoff-injection / hermes-session-sync /
  grok-image-to-video / hyperframes / markdown-viewer / minimax-image-to-video / remotion / apikey-image-gen）
  —— 全部是描述性文字，不影响加载与匹配。

**D. 不要动的**

- `/app/bin/ekko-studio-mcp.mjs` 本体（镜像文件，改了重建就丢，且无收益）。
- 已加载的 MCP 进程：`ps -ef | grep ekko-studio-mcp` 会看到**两套完整 4 进程**
  （一套挂在 `hermes gateway run` PID 105 下，一套挂在 agent-bridge worker 213/267 下）——
  **这是正常形态，不是泄漏**；每进程 ~8-10MB RSS。改配置不会让它们退出，只能重启对应宿主进程。

### 4.2 弃用后自检

```bash
# 1. config 形态（应看到 hermes-studio-mcp.mjs / hermes-studio-*，或按所选路线的预期形态）
grep -n 'ekko\|hermes-studio' /home/agent/.hermes/profiles/enderiose/config.yaml | head -20
grep -n 'ekko\|hermes-studio' /home/agent/.hermes/config.yaml | head -20

# 2. autoinject 是否跳过（路线 1）
grep -i 'mcp-autoinject' /home/agent/.hermes-web-ui/logs/server.log | tail -5
#   期望："[mcp-autoinject] disabled by HERMES_WEB_UI_DISABLE_MCP_AUTOINJECT"

# 3. 重启后 config 没被写回（路线 1/2 必查）
#   等 Web UI 重启完成，重新跑第 1 步比对

# 4. MCP 工具仍可用（任选一个 surface 试）
#   ekko_studio_api_openapi_get / ekko_studio_use_sessions_list 等

# 5. 进程形态（两套 4 进程属正常）
ps -eo pid,ppid,rss,args | grep -E 'studio-mcp' | grep -v grep
```

---

## 5. 重建后自检清单

按序跑，全绿即恢复：

```bash
# 1. 补丁是否打上
grep -c 'typing_notice' /opt/hermes/plugins/platforms/wecom/callback_adapter.py
bash /opt/data/patch/apply-all.sh && tail -20 /opt/data/patch/apply-all.log

# 2. gateway 与 Web UI
curl -s -o /dev/null -w 'webui=%{http_code}\n' http://127.0.0.1:6060/
curl -s -o /dev/null -w 'hook=%{http_code}\n' http://127.0.0.1:8645/health
curl -s -o /dev/null -w 'lan8645=%{http_code}\n' http://192.168.2.6:8645/health   # 端口发布验证

# 3. 企微回调往返（真实签名校验）
/opt/hermes/.venv/bin/python /home/agent/.hermes/profiles/enderiose/scripts/check_wecom_8645.py

# 4. 模型链探活
/opt/hermes/.venv/bin/python /opt/data/mp/llm_chain_guard.py --report --wait 45 --retries 2

# 5. 字体
/opt/hermes/.venv/bin/python /opt/data/mp/hei_font.py

# 6. 生图链（选一条强制单通道，会花额度，谨慎）
GENIMG_PROVIDER=songbey /opt/hermes/.venv/bin/python /opt/data/mp/mp_genimg.py --dry-run "test"

# 7. cron 任务在册
python3 -c "import json;d=json.load(open('/home/agent/.hermes/profiles/enderiose/cron/jobs.json'));print(len(d if isinstance(d,list) else d.get('jobs',d)))"

# 8. 公众号 API 出口 IP 白名单
/opt/hermes/.venv/bin/python /opt/data/mp/mp_ip_guard.py
```

另外确认：**宿主 compose 里 8645 已发布**、`HERMES_PATCH_SCRIPT` 已设、
企微后台的 URL 回调仍指向 `https://hp.286115.xyz:88/wecom/callback`、公众号后台白名单含当前出口 IP。

---

## 6. 已知故障与处理

### 6.1 `state.db` 损坏 / 被换掉（2026-10-14 连发 4 次，有完整处置记录）

症状：`FATAL: state.db reported structural corruption (database disk image is malformed outside the FTS shadow tables)`
或 `FATAL: state.db was replaced underneath the gateway; refusing further writes to this file`。
后果：新消息不再入库，转录被转到 `sessions/<id>.jsonl` 与 `pending_messages/` 兜底。

处置（**完整流程见技能 `devops/hermes-state-db-recovery`**）：

1. **只在副本上确认损坏**，绝不把 writer 指向活库：
   `cp -a state.db state.db-wal state.db-shm <staging>/`
   `python3 -c "import sqlite3;print(sqlite3.connect('file:<staging>/state.db?mode=ro',uri=True).execute('PRAGMA integrity_check').fetchall())"`
2. `HERMES_HOME=<profile> /opt/hermes/.venv/bin/hermes -p enderiose sessions recover --source <profile>/state.db --inspect-only`
3. 重建：`… sessions recover --source <profile>/state.db --output <profile>/recovered-state.db --report <新路径>.json`
   （中途停就加 `--allow-partial`；`--report` 拒绝覆盖已存在文件）
4. 装前四验：`integrity_check`=ok、`foreign_key_check` 空、`messages_fts` 计数 = `messages` 计数、最新 `messages.id` ≥ 源库
5. **换入必须是单条命令**，`-wal`/`-shm` 的删除要和改名落在同一次执行里
   （**残留的旧 `-wal` 会在下次打开时被重放到新库上，这是唯一能毁掉新库的失败模式**）：

```bash
PROF=/home/agent/.hermes/profiles/enderiose
BK=$PROF/backups/state-corrupt-$(date +%Y%m%d-%H%M%S)
mkdir -p "$BK"
PID=$(pgrep -f "hermes gateway run --replace" | head -1); [ -n "$PID" ] && kill -9 "$PID"
mv -f $PROF/state.db $PROF/state.db-wal $PROF/state.db-shm "$BK"/ 2>/dev/null
rm -f $PROF/state.db-wal $PROF/state.db-shm
cp -a <recovered-state.db> $PROF/state.db
rm -f $PROF/state.db-wal $PROF/state.db-shm
```

**踩过的坑（务必避免）**：
- `hermes gateway stop` **不保证停机** —— `HERMES_WEB_UI_MANAGED_GATEWAY=1` 下几秒内以新 PID 重生。
  别把重生当停机失败，要看 PID。
- **换入命令里 `mv` 的源文件必须真的存在**：2026-10-14 16:37 那次
  `mv -f recovered-state-1506.db state.db` 报 `No such file or directory`，
  结果活库被搬走、新库没装上 → gateway 重生时**建了一个全新的空库**，
  108 个会话 / 17638 条消息从活库里消失（数据仍在 `backups/state-corrupt-final-20261014-163713/`）。
  **换入前先 `ls -l` 确认 recovered 文件存在**，且命令里加 `set -e` / 逐步 echo 回显。
- 旧 inode 的 writer 无害（写的是已 unlink 的 inode），代价是那些消息丢失，**在飞的对话要重开**。
- 损坏的 bundle 与 recovered 副本都留在 `<profile>/backups/state-corrupt-<ts>/`，**别放 `/tmp`**。
- `<profile>/.curator_backups/blobs/` 是内容寻址的 blob 存储，**不能当 state.db 快照用**。

**维护窗口提示**：换库会打断正在跑的对话。**先跟用户说明再动手**，别默默换。

### 6.2 Web UI「新会话思考后丢失 session」

根因（2026-10-14 定位）：`default` profile 的 `config.yaml` 被写成死通道
（`model.default=mimo-v2.6-flash-free` / `provider=opencode-free`，403 FreeTierError，UA 门控），
任何在 `default` profile 下新建的 web 会话第一次请求就被判 Non-retryable，只留 1 条消息、`end_reason=error`。
修复：`default` profile 注册 `providers.workbuddy`（内联 key）+ `model = custom:workbuddy / global:deepseek-v4.1-flash`。

### 6.3 企微 403 / 消息发不出去

- 后台配 URL 回调时 `DecryptError: receive_id mismatch` → 打 `wecom_crypto_receiveid_patch.py`
- **URL 校验通过 ≠ 消息能聊**：回复走 `message/send`（要 corp_secret + agent_id），
  群机器人回调里的 FromUserName 未必是该应用可见的成员，回复可能发不出去
- 排查法：拿日志里那条 `GET /wecom/callback?...&echostr=…` 逐项验 ——
  先 `sha1(sorted(token, ts, nonce, echostr))` 比对 `msg_signature`（判 token），
  再用自己的 AESKey 解 echostr（判 AESKey），最后看载荷尾部 receive_id（判 corp 校验）

---

## 7. 一句话总结

**上游镜像给的是「裸 Hermes + Web UI」；本地二次开发全部落在
`/opt/data/patch/`（五处补丁：三处企微 + 两处 Web UI；另有同步器 `sync_model_context.py`）、`/opt/data/mp/`（公众号管线 + 素材 + 字体 + 图标仓库 + 规范文档）、
`profiles/enderiose/`（SOUL / 技能池 / cron / scripts / config / .env）这三处卷里。
重建 = 起容器（补 8645 端口、`HERMES_PATCH_SCRIPT` 与其余环境变量）→ `apply-all.sh` →
装 Playwright + cairosvg + 字体（+ JDK/SDK 若要出 APK）→ 按第 4 节处理 ekko 弃用 → 跑第 5 节自检清单。**

---

## 6. hermes-agent 0.21.2 → 0.22.0 升级要点（2026-10-17）

`Dockerfile` 的 `FROM nousresearch/hermes-agent:latest` 本次重建时拉到 **v0.22.0**
（旧容器是 v0.21.2；Docker Hub 上**没有** v0.21.2 tag，最早 v0.21.6，无法精确锁回）。
0.22.0 重写了企微回调插件（`WecomCallbackAdapter`，530→315 行），并改成
**multiplex（一个 host gateway 服务所有 profile）** 架构。

### 6.1 企微 3 补丁（`deploy/patches/`，已适配 0.22.x）

| 补丁 | 0.22.x 适配点 |
|---|---|
| `wecom_crypto_receiveid_patch.py` | 锚点与 0.21.2 一致，**原样可用** |
| `wecom_hook_patch.py` | 仅 `__init__` 锚点改 PEP-585 小写泛型（`dict[str, str]`） |
| `wecom_typing_notice_patch.py` | 锚点小写化；原 `send()` 改名 **`_send_impl`**（0.22.x 已自带 `_send_text(app, chat, content)`，避免重名） |

编译期 `apply-wecom.sh` 跑 3 个补丁 + 3 个自检，全绿才 build 成功（fail-fast）。

### 6.2 运行时配置（在卷里，重建不丢；新部署必须设置）

0.22.x 的 gateway 自启**默认关闭**，且 named profile（`enderiose`）在 multiplex 下
**不允许有自己的 gateway** —— 否则 `hermes gateway run` 直接拒绝启动、8645 不监听。

本部署已设的两处（重建后请核对）：

1. **Studio App 配置** —— `/home/agent/.hermes-web-ui/config.json`
   （= `config.appHome/config.json`，**不是** `.ekko/config/config.json`）：

   ```json
   "gatewayAutoStart": { "enabled": true, "include": ["enderiose"] }
   ```

   - `enabled` 默认 false（源码注释："Only explicit true enables"）；不设则 bootstrap
     只打印 `[bootstrap] profile gateways checked`，**不启动任何 gateway**。
   - `include: ["enderiose"]` 让 autostart 只拉起 enderiose，不拉 default
     （default 的 `platforms.wecom_callback.enabled: false`，且 standalone 下二者会冲突）。

2. **enderiose profile 配置** —— `profiles/enderiose/config.yaml`：

   ```yaml
   gateway:
     standalone: true
   ```

   这是 0.22.0 拒绝启动时**报错原文推荐的临时兼容路径**，恢复 0.21.2 的 per-profile
   gateway 行为。长期方向是 `hermes gateway migrate --multiplex`（default 一个 host
   gateway 服务所有 profile）。

### 6.3 已知非致命日志

- `gateway start completed but did not report running within timeout` —— Studio 的
  `waitForGatewayRunning` 硬编码 15s，而 0.22.0 的 gateway 启动（含 turn machinery warm）
  常 >15s。**gateway 实际已在运行**（`ps` + 8645 health 可证），可忽略。
- `Failed to recover pending message ... FOREIGN KEY constraint failed`（3 条 `20261014_*`）
  —— 旧版遗留的 spooled 消息在新 schema 下无法回放，gateway 会
  "Held back N spooled transcript file(s)"，**不影响新消息**。
