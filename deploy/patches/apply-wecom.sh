#!/usr/bin/env bash
# 编译期应用「企微插件」补丁（作用于 base image 的 /opt/hermes/plugins/platforms/wecom/*.py）。
#
# 与运行时 /opt/data/patch/apply-all.sh 的分工：
#   * Web UI 两处（新会话默认 profile / 上下文长度兜底）已改成 **源码级改动**，
#     编译即生效 —— 不在这里重复打。
#   * 企微三处改的是 base image 里的文件，源码不在本仓库 —— 必须编译期打。
# 编译期应当 fail-fast：任一步失败即中断 build（运行时脚本则相反，永不阻断启动）。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=/opt/hermes/.venv/bin/python

# 补丁脚本会往 /opt/data/patch/ 写一份 .patched 副本；运行时那里是卷，编译期不存在 → 先建。
mkdir -p /opt/data/patch

for p in wecom_typing_notice_patch wecom_crypto_receiveid_patch wecom_hook_patch; do
  echo "=== ${p} ==="
  "$PY" "$DIR/$p.py"
  echo "--- self-test ---"
  "$PY" "$DIR/test_$p.py"
done

echo "=== 企微补丁全部应用完成 ==="
