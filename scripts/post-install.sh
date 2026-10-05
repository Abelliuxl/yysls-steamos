#!/usr/bin/env bash
# 燕云十六声（国服）安装完成后运行本脚本：
#   自动定位 launcher.exe，并把 Steam 快捷方式的启动目标改过去。
#   会自己处理"Steam 被 gamescope-session 自动拉起"的问题（mask 手法）。
#
# 用法:  ~/Games/yysls/post-install.sh
set -uo pipefail
cd "$(dirname "$0")"
STEAM="$HOME/.local/share/Steam"
REL="pfx/drive_c/Program Files/yysls/Win32/deploy/launcher.exe"

info(){ printf '\033[1;32m%s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m%s\033[0m\n' "$*"; }
err(){  printf '\033[1;31m%s\033[0m\n' "$*"; }

# ---------- 1. 找 launcher.exe ----------
info "== 1/4 搜索 launcher.exe =="
shopt -s nullglob
hits=( "$STEAM"/steamapps/compatdata/*/"$REL" )
if [ ${#hits[@]} -eq 0 ]; then
  err "没找到 launcher.exe。可能游戏还没装完。"
  echo "  再宽泛找一下含 launcher 的 exe:"
  find "$STEAM/steamapps/compatdata" -maxdepth 9 -type f -iname 'launcher*.exe' 2>/dev/null | head -20
  echo "  以及 yysls 目录:"
  find "$STEAM/steamapps/compatdata" -maxdepth 9 -type d -iname 'yysls' 2>/dev/null | head
  exit 1
fi
BEST=""
for h in "${hits[@]}"; do { [ -z "$BEST" ] || [ "$h" -nt "$BEST" ]; } && BEST="$h"; done
PREFIX="$(echo "$BEST" | sed -E "s#^$STEAM/steamapps/compatdata/([^/]+)/.*#\1#")"
info "发现: $BEST"
echo  "      compatdata 前缀: $PREFIX"
echo  "      起始位置        : $(dirname "$BEST")"

# ---------- 2. 挂保护，避免触发 SteamOS 的"自动修复" ----------
info "== 2/4 临时挂保护 + 停止 Steam =="
mkdir -p "$HOME/.config"
touch "$HOME/.config/inhibit-short-session-tracker"
cleanup(){ systemctl --user unmask steam-launcher 2>/dev/null; rm -f "$HOME/.config/inhibit-short-session-tracker"; }
trap cleanup EXIT

systemctl --user stop steam-launcher 2>/dev/null
systemctl --user mask steam-launcher 2>/dev/null
for i in 1 2 3 4 5; do
  pids=$(pgrep -f 'ubuntu12_32/stea[m]' 2>/dev/null)
  [ -z "$pids" ] && break
  for p in $pids; do kill -9 "$p" 2>/dev/null; done
  sleep 1
done
pkill -9 -x steamwebhelper 2>/dev/null
sleep 2
echo "  Steam 残留进程: $(pgrep -f 'ubuntu12_32/stea[m]' 2>/dev/null | wc -l)"
rm -f /tmp/steamos-short-session-tracker /tmp/steamos-short-session-start

# ---------- 3. 改启动目标 ----------
info "== 3/4 改写 Steam 快捷方式目标 =="
python3 steam-shortcut.py repoint "$BEST"
rc=$?
rm -f /tmp/steamos-short-session-tracker

# ---------- 4. 重启 Steam ----------
info "== 4/4 重启 Steam =="
systemctl --user unmask steam-launcher 2>/dev/null
systemctl --user start steam-launcher 2>/dev/null
trap - EXIT
rm -f "$HOME/.config/inhibit-short-session-tracker"

sleep 8
if [ "$rc" -eq 0 ]; then
  info "完成！现在在 Steam 库里点「燕云十六声（国服）」即可进入启动器。"
else
  err "改写失败，请看上面的输出。"
fi
