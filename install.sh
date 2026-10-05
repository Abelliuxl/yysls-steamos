#!/usr/bin/env bash
# ============================================================================
#  燕云十六声（国服）SteamOS 兼容配置 —— 一键安装 / 修复
#
#  用法：
#     ./install.sh                 安装脚本 + systemd 服务 + 注册表修复
#     ./install.sh --steam         额外把 Steam 快捷方式指回真正的 launcher.exe
#     ./install.sh --dpi 288       手动指定 Wine DPI（默认自动读 Xft.dpi）
#     ./install.sh --dry-run       只打印将要做什么，不落盘
#     ./install.sh --yes           跳过交互确认
#
#  说明：全部是用户级操作，**不需要 root**、不动 SteamOS 只读分区。
# ============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$HOME"
STEAM="$HOME_DIR/.local/share/Steam"
INSTALL_DIR="$HOME_DIR/.local/share/yysls-steamos"
UNIT_DIR="$HOME_DIR/.config/systemd/user"

APPID="${YYSLS_APPID:-2885173776}"
APPNAME="${YYSLS_APPNAME:-燕云十六声（国服）}"
GE_GLOB="$STEAM/compatibilitytools.d/GE-Proton*"

DO_REGISTRY=1
DO_STEAM=0
DO_YES=0
DRY=0
DPI=""
LAUNCHER_AUTOSTART=0

# ---------------------------------------------------------------- 输出工具
c_ok(){   printf '\033[1;32m%s\033[0m\n' "$*"; }
c_info(){ printf '\033[1;36m%s\033[0m\n' "$*"; }
c_warn(){ printf '\033[1;33m%s\033[0m\n' "$*"; }
c_err(){  printf '\033[1;31m%s\033[0m\n' "$*" >&2; }
die(){ c_err "错误：$*"; exit 1; }
run(){ if [ "$DRY" = 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }

usage(){ sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# ---------------------------------------------------------------- 参数
while [ $# -gt 0 ]; do
  case "$1" in
    --steam|--steam-repoint) DO_STEAM=1 ;;
    --no-registry)   DO_REGISTRY=0 ;;
    --launcher-autostart) LAUNCHER_AUTOSTART=1 ;;
    --dpi)           DPI="${2:?--dpi 需要一个数字}"; shift ;;
    --dry-run)       DRY=1 ;;
    --yes|-y)        DO_YES=1 ;;
    -h|--help)       usage ;;
    *) die "未知参数：$1（--help 查看用法）" ;;
  esac
  shift
done

confirm(){
  [ "$DO_YES" = 1 ] && return 0
  printf '%s [y/N] ' "$1"
  read -r ans </dev/tty || ans=n
  case "$ans" in y|Y|yes|YES) return 0;; *) return 1;; esac
}

# ---------------------------------------------------------------- 0. 前置检查
c_info "== 0/7 环境检查 =="
[ -d "$STEAM" ] || die "找不到 Steam 目录：$STEAM"
command -v python3 >/dev/null || die "缺少 python3"
command -v systemctl >/dev/null || die "缺少 systemctl"
command -v xdotool >/dev/null || c_warn "未找到 xdotool（显存守卫优雅关窗会退化为直接 TERM）"

# GE-Proton（取版本号最大的一个）
PROTON_DIR=""
if compgen -G "$GE_GLOB" >/dev/null; then
  PROTON_DIR="$(printf '%s\n' $GE_GLOB | sort -V | tail -1)"
fi
if [ -z "$PROTON_DIR" ]; then
  c_warn "没找到 GE-Proton（$GE_GLOB）"
  c_warn "先在 Steam 里装一个 GE-Proton，或用桌面模式『ProtonUp-Qt』装。"
  c_warn "没有它也能继续，但启动器/游戏服务会指不到 wine。"
else
  c_ok "GE-Proton: $PROTON_DIR"
fi

# ---------------------------------------------------------------- 1. 定位游戏
c_info "== 1/7 定位燕云安装 =="
LAUNCHER_REL="pfx/drive_c/Program Files/yysls/Win32/deploy/launcher.exe"
BEST=""
for d in "$STEAM"/steamapps/compatdata/*/; do
  [ -f "$d$LAUNCHER_REL" ] && BEST="$d$LAUNCHER_REL"
done

PREFIX=""
if [ -n "$BEST" ]; then
  PREFIX="${BEST%%/drive_c/*}"           # …/compatdata/2885173776/pfx
  APPID="$(echo "$PREFIX" | sed -E 's#.*/compatdata/([^/]+)/pfx#\1#')"
  WORKDIR="$(dirname "$BEST")"
  c_ok "launcher.exe: $BEST"
  c_ok "appid=$APPID  前缀=$PREFIX"
else
  PREFIX="$STEAM/steamapps/compatdata/$APPID/pfx"
  WORKDIR="$PREFIX/drive_c/Program Files/yysls/Win32/deploy"
  c_warn "还没找到 launcher.exe（游戏可能尚未安装完成）"
  c_warn "将按默认 appid=$APPID 生成配置；等游戏装完后重跑本脚本即可。"
fi

# ---------------------------------------------------------------- 2. 安装文件
c_info "== 2/7 安装脚本到 $INSTALL_DIR =="
run mkdir -p "$INSTALL_DIR" "$UNIT_DIR" "$(dirname "${LOG:-$HOME_DIR/.local/state/yysls-steamos/vram-guard.log}")"
for f in vram-guard.py steam-shortcut.py post-install.sh; do
  run install -m 755 "$REPO_DIR/scripts/$f" "$INSTALL_DIR/$f"
done
run install -m 644 "$REPO_DIR/conf/pet_settings.ini" "$INSTALL_DIR/pet_settings.ini"
c_ok "已安装：vram-guard.py / steam-shortcut.py / post-install.sh"

# ---------------------------------------------------------------- 3. 自动 DPI
if [ -z "$DPI" ]; then
  DPI="$(xrdb -query 2>/dev/null | awk -F'[ \t]+' '/Xft\.dpi/{print $2; exit}' || true)"
  DPI="${DPI%%.*}"; [ -n "$DPI" ] && [ "$DPI" -gt 0 ] 2>/dev/null || DPI=96
fi
c_ok "Wine DPI = $DPI"

# ---------------------------------------------------------------- 4. 注册表修复
if [ "$DO_REGISTRY" = 1 ]; then
  c_info "== 3/7 写入 Wine 前缀注册表（软件渲染 + DPI）=="
  if [ -z "$PROTON_DIR" ]; then
    c_warn "没有 GE-Proton，跳过注册表修复。"
  elif [ ! -f "$PREFIX/user.reg" ]; then
    c_warn "前缀还没初始化（$PREFIX/user.reg 不存在），先跑一次游戏再重跑本脚本。"
  elif pgrep -f "$APPID" >/dev/null 2>&1; then
    c_warn "检测到该前缀有进程在跑，跳过注册表修改以免冲突。"
  else
    WINE="$PROTON_DIR/files/bin/wine"
    export WINEPREFIX="$PREFIX"
    export LD_LIBRARY_PATH="$PROTON_DIR/files/lib/x86_64-linux-gnu:$PROTON_DIR/files/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    # ① 强制 Qt 软件渲染：官方启动器在 GPU 下会以 ~100MB/s 泄漏显存，最终冻死整个桌面。
    run "$WINE" reg add 'HKCU\Environment' /v QT_OPENGL       /t REG_SZ    /d software /f >/dev/null
    run "$WINE" reg add 'HKCU\Environment' /v QT_QUICK_BACKEND /t REG_SZ   /d software /f >/dev/null
    # ② 4K + KDE 缩放下的 DPI：Wine 默认 96，界面会小到没法点。
    run "$WINE" reg add 'HKCU\Control Panel\Desktop' /v LogPixels /t REG_DWORD /d "$DPI" /f >/dev/null
    run "$WINE" reg add 'HKCU\Software\Wine\Fonts'   /v LogPixels /t REG_DWORD /d "$DPI" /f >/dev/null
    c_ok "注册表已修复（QT 软件渲染 + LogPixels=$DPI）"
  fi
else
  c_info "== 3/7 跳过注册表修复（--no-registry）=="
fi

# ---------------------------------------------------------------- 5. 宠物特效（省显存）
c_info "== 4/7 关闭启动器宠物特效（省显存/CPU）=="
PET="$WORKDIR/saved/pet/pet_settings.ini"
if [ -f "$PET" ]; then
  run cp -f "$REPO_DIR/conf/pet_settings.ini" "$PET"
  c_ok "已写入 $PET"
else
  c_warn "未找到 pet_settings.ini，跳过（进过游戏后就会有）。"
fi

# ---------------------------------------------------------------- 6. systemd 服务
c_info "== 5/7 生成 systemd 用户服务 =="
LOG="$HOME_DIR/.local/state/yysls-steamos/vram-guard.log"
run mkdir -p "$(dirname "$LOG")"
gen(){
  local src="$1" dst="$2"
  sed -e "s#@PROTON_DIR@#$PROTON_DIR#g" \
      -e "s#@PREFIX@#$PREFIX#g" \
      -e "s#@WORKDIR@#$WORKDIR#g" \
      -e "s#@INSTALL_DIR@#$INSTALL_DIR#g" \
      -e "s#@APPID@#$APPID#g" \
      -e "s#@UID@#$(id -u)#g" \
      -e "s#@LOG@#$LOG#g" \
      "$src" > "$dst"
}
[ -f "$UNIT_DIR/vram-guard.service" ] && run cp -f "$UNIT_DIR/vram-guard.service" "$UNIT_DIR/vram-guard.service.bak-$(date +%s)"
[ -f "$UNIT_DIR/yysls-launcher.service" ] && run cp -f "$UNIT_DIR/yysls-launcher.service" "$UNIT_DIR/yysls-launcher.service.bak-$(date +%s)"
run gen "$REPO_DIR/systemd/vram-guard.service.in"    "$UNIT_DIR/vram-guard.service"
run gen "$REPO_DIR/systemd/yysls-launcher.service.in" "$UNIT_DIR/yysls-launcher.service"
c_ok "已写入 $UNIT_DIR/{vram-guard,yysls-launcher}.service"

c_info "== 6/7 启用服务 =="
if [ "$DRY" = 0 ]; then
  systemctl --user daemon-reload
  systemctl --user enable --now vram-guard.service
  c_ok "vram-guard.service 已启用并运行（防止显存泄漏冻死桌面）"
  if [ "$LAUNCHER_AUTOSTART" = 1 ]; then
    systemctl --user enable --now yysls-launcher.service
    c_ok "yysls-launcher.service 已启用并运行"
  else
    c_warn "yysls-launcher.service 已安装但未开机自启；需要下载/更新时手动："
    echo "      systemctl --user start yysls-launcher"
  fi
else
  echo "  [dry-run] systemctl --user enable --now vram-guard.service"
fi

# ---------------------------------------------------------------- 7. Steam 快捷方式
c_info "== 7/7 Steam 快捷方式 =="
if [ "$DO_STEAM" = 1 ]; then
  if [ -z "$BEST" ]; then
    c_err "还没找到 launcher.exe，无法改写 Steam 快捷方式（先装完游戏）。"
  else
    c_info "将停止 Steam、把快捷方式指回 launcher.exe、再重启 Steam。"
    if confirm "现在执行吗？（会中断正在运行的 Steam）"; then
      if [ "$DRY" = 1 ]; then
        echo "  [dry-run] $INSTALL_DIR/post-install.sh"
      else
        APPID="$APPID" bash "$INSTALL_DIR/post-install.sh"
      fi
    else
      c_warn "已跳过。以后可手动运行：$INSTALL_DIR/post-install.sh"
    fi
  fi
else
  c_info "未指定 --steam，跳过。若 Steam 里点开始仍会跑安装器，执行："
  echo "      $INSTALL_DIR/post-install.sh"
fi

echo
c_ok "全部完成 ✅"
cat <<EOF

下一步：
  1. 游戏模式下在 Steam 库点「$APPNAME」即可进启动器（登录流程见 docs/操作步骤.md）。
  2. 查看显存守卫： systemctl --user status vram-guard
                      tail -f $LOG
  3. 下载/更新走服务： systemctl --user start yysls-launcher
  4. 出问题先看：      docs/排错.md
EOF
