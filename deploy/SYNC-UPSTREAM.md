# 同步上游到 custom（本 fork 的维护流程）

- 上游：`https://github.com/EKKOLearnAI/ekko-studio`（remote 名 **`upstream`**）
- 本 fork：`https://github.com/Enderiose/hermes-webui`（remote 名 **`origin`**，分支 **`custom`**）
- 工作副本（NAS）：`/volume1/docker/hermes/ekko-src`

## 一次同步

```bash
cd /volume1/docker/hermes/ekko-src
git fetch --no-tags upstream main:refs/remotes/upstream/main
git checkout custom
git merge upstream/main        # 或 git rebase upstream/main（历史更干净，但需 force push）
# → 解决冲突，重点是下面「本地定制文件清单」
git push origin custom
```

## ⚠ 本地定制文件清单（同步时重点检查，别被上游覆盖）

这是本 fork 相对上游的**二次开发**，同步后必须逐项确认仍在：

| 类别 | 路径 | 说明 |
|---|---|---|
| 客户端定制 | `packages/client/src/App.vue` | 移除远程公告弹窗 `StudioAnnouncementPrompt` |
| 客户端定制 | `packages/client/src/components/hermes/connections/AppConnectionsPanel.vue` | 移除 App 付费 / 定价入口 |
| 服务端定制 | `packages/server/src/modules/hermes/services/models/context.ts` | 新增 `lookupEndpointContextLength`，在 fallback 前读 `cache/endpoint_context_lengths.json`，不再硬编码 `256000` |
| 客户端定制 | `packages/client/src/stores/hermes/profiles.ts` | 新会话默认 profile 跟随服务端 `active`，不再粘 `default` |
| 构建定制 | `Dockerfile` | 追加「本地定制层」：黑体字体 / Playwright+cairosvg / 企微补丁 / REBUILD.md |
| 定制目录 | `deploy/**` | **纯新增文件**，不碰上游源码，天然不冲突 |

> **会与上游冲突的只有 `Dockerfile` 和上面 4 个源码文件**（仅当上游改了同一处）。
> `deploy/` 永不冲突。

冲突处理原则：

1. **4 个源码文件** —— 以本 fork 的改动为准。上游至今未修这几处；若上游某天自己修了，
   则改为采用上游版本并删掉本 fork 的对应改动（同时更新本文档）。
2. **`Dockerfile`** —— 保留上游对构建步骤的改动，把「本地定制层」那段重新追加到
   `ENTRYPOINT` 之前即可。

## 同步记录

| 日期 | 上游 HEAD | 结果 |
|---|---|---|
| 2026-10-17 | `2325aa4e`（v0.7.36） | custom 快进 + 重放定制，ahead 19；本地定制层 `deploy/` 与 4 处源码改动就位 |
