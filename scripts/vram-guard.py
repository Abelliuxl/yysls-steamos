#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VRAM 守卫 —— 防止「燕云十六声（国服）」启动器/游戏把显存+GTT 撑爆，
导致 amdgpu/TTM 在 ww_mutex 上死锁、整个桌面冻死（2026-10-03 已发生过一次，
启动器 launcher.exe 约 7 分钟吃掉了 ~43 GB GPU 缓冲）。

原理：只做“看门狗”，不改游戏。周期性读取
  - 全局 /sys/class/drm/card0/device/mem_info_{vram,gtt}_used
  - 该 Proton 前缀(2885173776)下每个进程的 /proc/<pid>/fdinfo/* 里的 drm-total-vram / drm-total-gtt
一旦发现异常增长/接近上限，立刻把整个 Wine 进程组杀掉（下载进度在磁盘上，不会丢）。

可调参数（环境变量）：
  VRAM_GUARD_INTERVAL=2        轮询间隔秒
  VRAM_GUARD_LAUNCHER_GB=10    启动器模式下，进程组显存超过该值就杀
  VRAM_GUARD_GROWTH_GB=3       30 秒内增长超过该值就杀
  VRAM_GUARD_VRAM_PCT=90       全局显存使用率(%)超过就杀
  VRAM_GUARD_GTT_PCT=85        全局 GTT 使用率(%)超过就杀
  VRAM_GUARD_DRYRUN=1          只记录不杀（调试用）
"""
import os, sys, glob, time, signal, subprocess, datetime

APPID = os.environ.get("YYSLS_APPID", "2885173776")
PREFIX_HINT = APPID.encode()


def _find_drm_base():
    """找到第一张带显存统计的 DRM 卡（通常是 card0）。"""
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        if os.path.exists(os.path.join(card, "device/mem_info_vram_total")):
            return os.path.join(card, "device")
    return "/sys/class/drm/card0/device"


BASE = os.environ.get("VRAM_GUARD_DRM_BASE") or _find_drm_base()
_DEFAULT_LOG = os.path.expanduser("~/.local/state/yysls-steamos/vram-guard.log")
LOG = os.environ.get("YYSLS_VRAM_LOG") or _DEFAULT_LOG
LOG_MAX = 2 * 1024 * 1024

INTERVAL = float(os.environ.get("VRAM_GUARD_INTERVAL", "2"))
LAUNCHER_GB = float(os.environ.get("VRAM_GUARD_LAUNCHER_GB", "2"))
GROWTH_GB = float(os.environ.get("VRAM_GUARD_GROWTH_GB", "2"))
VRAM_PCT = float(os.environ.get("VRAM_GUARD_VRAM_PCT", "90"))
GTT_PCT = float(os.environ.get("VRAM_GUARD_GTT_PCT", "85"))
GAME_VRAM_PCT = float(os.environ.get("VRAM_GUARD_GAME_VRAM_PCT", "97"))
GAME_GTT_PCT = float(os.environ.get("VRAM_GUARD_GAME_GTT_PCT", "92"))
GAME_GROWTH_GB = float(os.environ.get("VRAM_GUARD_GAME_GROWTH_GB", "6"))
DRYRUN = os.environ.get("VRAM_GUARD_DRYRUN", "") not in ("", "0")
GROWTH_WINDOW = float(os.environ.get("VRAM_GUARD_WINDOW", "60"))
GB = 1024 ** 3


def log(msg):
    ts = datetime.datetime.now().strftime("%F %T")
    line = "%s %s" % (ts, msg)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
    except OSError:
        pass
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def read_int(path, default=0):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return default


def wine_pids():
    """返回该 Proton 前缀下的所有进程 pid（含 wineserver / launcher / 游戏）。"""
    out = {}
    for p in glob.glob("/proc/[0-9]*"):
        pid = int(p.rsplit("/", 1)[1])
        if pid == os.getpid():
            continue
        try:
            env = open(p + "/environ", "rb").read()
        except Exception:
            continue
        hit = False
        for kv in env.split(b"\0"):
            if kv.startswith(b"WINEPREFIX=") and PREFIX_HINT in kv:
                hit = True
                break
        if not hit:
            continue
        try:
            comm = open(p + "/comm").read().strip()
        except Exception:
            comm = "?"
        out[pid] = comm
    return out


def pid_gpu(pid):
    v = g = 0
    for fd in glob.glob("/proc/%d/fdinfo/*" % pid):
        try:
            txt = open(fd).read()
        except Exception:
            continue
        for line in txt.splitlines():
            if line.startswith("drm-total-vram:"):
                v += int(line.split()[1]) * 1024
            elif line.startswith("drm-total-gtt:"):
                g += int(line.split()[1]) * 1024
    return v, g


def close_windows(pids):
    """先给窗口发 WM_DELETE_WINDOW，让启动器“正常关闭”（保存下载状态），减少缓存损坏。"""
    disp = os.environ.get("DISPLAY", ":0")
    env = dict(os.environ, DISPLAY=disp)
    closed = 0
    for p in pids:
        try:
            r = subprocess.run(["xdotool", "search", "--pid", str(p)],
                               capture_output=True, env=env, timeout=5)
        except Exception:
            return closed
        for wid in r.stdout.split():
            try:
                subprocess.run(["xdotool", "windowclose", wid.decode()],
                               capture_output=True, env=env, timeout=5)
                closed += 1
            except Exception:
                pass
    return closed


def kill_group(pids, reason):
    names = ", ".join("%d:%s" % (p, c) for p, c in sorted(pids.items()))
    log("!!! 触发: %s" % reason)
    log("    目标进程: %s" % names)
    if DRYRUN:
        log("    (DRYRUN，不杀)")
        return
    n = close_windows(pids)
    if n:
        log("    已向 %d 个窗口发关闭事件，等 6 秒让它自己退" % n)
        time.sleep(6)
        if not wine_pids():
            log("    启动器已正常退出（显存回收）")
            return
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    time.sleep(3)
    for p in pids:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    log("    已发送 TERM/KILL，等待显存回收")


def main():
    vram_total = read_int(BASE + "/mem_info_vram_total")
    gtt_total = read_int(BASE + "/mem_info_gtt_total")
    log("=== vram-guard 启动 pid=%d vram_total=%.1fG gtt_total=%.1fG "
        "launcher_limit=%.1fG growth=%.1fG/%ds vram_pct=%.0f%% gtt_pct=%.0f%% "
        "game: vram>%.0f%% gtt>%.0f%% growth>%.1fG dryrun=%s"
        % (os.getpid(), vram_total / GB, gtt_total / GB, LAUNCHER_GB,
           GROWTH_GB, int(GROWTH_WINDOW), VRAM_PCT, GTT_PCT,
           GAME_VRAM_PCT, GAME_GTT_PCT, GAME_GROWTH_GB, DRYRUN))
    hist = []          # [(t, group_vram)]
    lhist = []         # [(t, launcher_only_vram)]
    last_status = 0.0
    while True:
        now = time.time()
        pids = wine_pids()
        gv = gg = 0
        lv = 0            # 仅 launcher.exe 自己的显存
        per = {}
        for p, c in pids.items():
            v, g = pid_gpu(p)
            per[p] = (c, v, g)
            gv += v
            gg += g
            if c == "launcher.exe":
                lv += v
        uvram = read_int(BASE + "/mem_info_vram_used")
        ugtt = read_int(BASE + "/mem_info_gtt_used")
        has_launcher = any(c == "launcher.exe" for c, _, _ in per.values())
        # 除启动器/系统进程外，有别的进程吃了 >2G ⇒ 游戏已经在跑了
        game_running = any(c not in ("launcher.exe", "explorer.exe", "start.exe",
                                     "wineserver", "services.exe", "winedevice.exe",
                                     "svchost.exe", "plugplay.exe", "rpcss.exe",
                                     "tabtip.exe", "xalia.exe")
                            and v > 2 * GB for c, v, _ in per.values())

        hist.append((now, gv))
        hist = [(t, v) for t, v in hist if now - t <= GROWTH_WINDOW]
        base_v = min([v for _, v in hist], default=gv)
        growth = gv - base_v
        lhist.append((now, lv))
        lhist = [(t, v) for t, v in lhist if now - t <= GROWTH_WINDOW]
        lgrowth = lv - min([v for _, v in lhist], default=lv)

        reason = None
        if pids:
            if has_launcher and not game_running:
                # 启动器阶段：只盯 launcher.exe 自己（软件渲染下它只有 ~100MB）
                if lv > LAUNCHER_GB * GB:
                    reason = "启动器自身显存 %.1fG > %.1fG" % (lv / GB, LAUNCHER_GB)
                elif lgrowth > GROWTH_GB * GB:
                    reason = "启动器显存 %.0fs 内增长 %.1fG (%.0f MB/s)" % (
                        GROWTH_WINDOW, lgrowth / GB, lgrowth / GROWTH_WINDOW / 1024 / 1024)
                elif uvram > vram_total * VRAM_PCT / 100.0:
                    reason = "全局显存 %.1fG > %.0f%% (%.1fG)" % (uvram / GB, VRAM_PCT, vram_total * VRAM_PCT / 100 / GB)
                elif ugtt > gtt_total * GTT_PCT / 100.0:
                    reason = "全局 GTT %.1fG > %.0f%% (%.1fG)" % (ugtt / GB, GTT_PCT, gtt_total * GTT_PCT / 100 / GB)
            else:
                # 游戏阶段：只在极端失控时兜底（游戏本身可以合法吃很多显存）
                if uvram > vram_total * GAME_VRAM_PCT / 100.0:
                    reason = "全局显存 %.1fG > %.0f%% (%.1fG)" % (uvram / GB, GAME_VRAM_PCT, vram_total * GAME_VRAM_PCT / 100 / GB)
                elif ugtt > gtt_total * GAME_GTT_PCT / 100.0:
                    reason = "全局 GTT %.1fG > %.0f%% (%.1fG)" % (ugtt / GB, GAME_GTT_PCT, gtt_total * GAME_GTT_PCT / 100 / GB)
                elif growth > GAME_GROWTH_GB * GB:
                    reason = "进程组显存 %.0fs 内增长 %.1fG (%.0f MB/s)" % (
                        GROWTH_WINDOW, growth / GB, growth / GROWTH_WINDOW / 1024 / 1024)

        if now - last_status >= 30.0:
            last_status = now
            if pids:
                top = sorted(per.items(), key=lambda kv: -kv[1][1])[:3]
                tops = " | ".join("%s=%dMB" % (c, v / 1024 / 1024) for _, (c, v, _) in top)
                log("状态: 组显存=%.0fMB (启动器%.0fMB) 组GTT=%.0fMB 全局vram=%.0fMB 全局gtt=%.0fMB 增长=%.0fMB/%.0fs %s  %s"
                    % (gv / 1024 / 1024, lv / 1024 / 1024, gg / 1024 / 1024,
                       uvram / 1024 / 1024, ugtt / 1024 / 1024,
                       lgrowth / 1024 / 1024, GROWTH_WINDOW,
                       "[游戏运行中]" if game_running else "[启动器]", tops))
            else:
                log("状态: 空闲 (全局vram=%.0fMB 全局gtt=%.0fMB)" % (uvram / 1024 / 1024, ugtt / 1024 / 1024))

        if reason:
            kill_group(pids, reason)
            hist = []
            lhist = []
            time.sleep(15)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
