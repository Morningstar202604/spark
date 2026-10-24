#!/usr/bin/env sh
# Spark 一键安装（Linux / macOS）
#
#   curl -fsSL https://raw.githubusercontent.com/badhope/spark/main/scripts/install.sh | sh
#
# 行为：取最新 Release 的本平台产物 → 校验 SHA256SUMS → 解压到
# ${SPARK_INSTALL_DIR:-$HOME/.local/share/spark} → 软链到 ~/.local/bin/spark。
# 不 sudo、不改系统目录、不静默跳过校验。可用 SPARK_VERSION=v0.9.12 固定版本。
set -eu

REPO="badhope/spark"
API="https://api.github.com/repos/${REPO}/releases/latest"

die() { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[36m→\033[0m %s\n' "$1"; }

command -v curl >/dev/null 2>&1 || die "需要 curl"
command -v tar >/dev/null 2>&1 || die "需要 tar"

case "$(uname -s)" in
  Linux)  ASSET="spark-linux-x86_64.tar.gz" ;;
  Darwin) ASSET="spark-macos-arm64.tar.gz" ;;
  *) die "不支持的系统：$(uname -s)（Windows 请用 install.ps1）" ;;
esac

if [ -n "${SPARK_VERSION:-}" ]; then
  API="https://api.github.com/repos/${REPO}/releases/tags/${SPARK_VERSION}"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

info "查询最新版本…"
META="$TMP/release.json"
curl -fsSL "$API" -o "$META" || die "无法访问 GitHub Releases（内网请用 SPARK_MIRROR）"

json_field() {
  # 极简 JSON 取值：只用于我们自己的 release 元数据
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json,sys
data=json.load(open(sys.argv[1]))
for a in data.get("assets",[]):
    if a.get("name")==sys.argv[2]:
        print(a.get("browser_download_url",""))
        break
PY
}

if [ -n "${SPARK_MIRROR:-}" ]; then
  URL="${SPARK_MIRROR%/}/${ASSET}"
  SUMS_URL="${SPARK_MIRROR%/}/SHA256SUMS"
else
  URL="$(json_field "$META" "$ASSET")"
  SUMS_URL="$(json_field "$META" "SHA256SUMS")"
fi

[ -n "$URL" ] || die "Release 中没有 ${ASSET}；可用产物见 https://github.com/${REPO}/releases"
[ -n "$SUMS_URL" ] || die "Release 中没有 SHA256SUMS，拒绝安装未校验的产物"

info "下载 ${ASSET}…"
curl -fsSL "$URL" -o "$TMP/$ASSET" || die "下载失败"
curl -fsSL "$SUMS_URL" -o "$TMP/SHA256SUMS" || die "下载 SHA256SUMS 失败"

info "校验 SHA256…"
EXPECTED="$(awk -v n="$ASSET" '$2==n || $2=="*"n {print $1}' "$TMP/SHA256SUMS" | head -n1)"
[ -n "$EXPECTED" ] || die "SHA256SUMS 中没有 ${ASSET}"
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL="$(sha256sum "$TMP/$ASSET" | awk '{print $1}')"
else
  ACTUAL="$(shasum -a 256 "$TMP/$ASSET" | awk '{print $1}')"
fi
[ "$EXPECTED" = "$ACTUAL" ] || die "SHA256 不匹配（期望 ${EXPECTED}，实际 ${ACTUAL}）"

DEST="${SPARK_INSTALL_DIR:-$HOME/.local/share/spark}"
info "安装到 ${DEST}…"
mkdir -p "$DEST"
tar -xzf "$TMP/$ASSET" -C "$DEST" --strip-components=1 2>/dev/null || tar -xzf "$TMP/$ASSET" -C "$DEST"

BIN=""
for candidate in "$DEST/spark" "$DEST"/spark/spark; do
  [ -x "$candidate" ] && BIN="$candidate" && break
done
[ -n "$BIN" ] || die "解压后找不到 spark 可执行文件"

chmod +x "$BIN"
LINK_DIR="$HOME/.local/bin"
mkdir -p "$LINK_DIR"
ln -sf "$BIN" "$LINK_DIR/spark"

info "已安装：$("$LINK_DIR/spark" --version 2>/dev/null || echo 'spark')"
case ":$PATH:" in
  *":$LINK_DIR:"*) ;;
  *) printf '\033[33m注意:\033[0m 把 %s 加入 PATH：\n  export PATH="%s:$PATH"\n' "$LINK_DIR" "$LINK_DIR" ;;
esac
printf '\n下一步：\033[1mspark init\033[0m 配置模型 → \033[1mspark web\033[0m 打开界面\n'
