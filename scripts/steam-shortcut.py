#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
燕云十六声（国服）Steam 集成工具

用法:
  steam-shortcut.py add <exe路径> <显示名称>     # 新建/覆盖非Steam快捷方式
  steam-shortcut.py find-launcher                # 在 compatdata 里找 launcher.exe
  steam-shortcut.py repoint <新exe路径>          # 把已有快捷方式的目标改掉(保留appid/Proton设置)
  steam-shortcut.py show                        # 打印当前快捷方式

注意: 改 shortcuts.vdf 必须先退出 Steam，否则退出时会被覆盖。
"""
import os, sys, struct, zlib, glob, shutil, time, subprocess

HOME = os.path.expanduser("~")
STEAM = os.path.join(HOME, ".local/share/Steam")
USERDATA = os.path.join(STEAM, "userdata")
SHORTCUTS_NAME = "shortcuts.vdf"
LAUNCHER_REL = os.path.join("pfx", "drive_c", "Program Files", "yysls", "Win32", "deploy", "launcher.exe")
COMPAT_TOOL = "GE-Proton10-32"   # 已安装
ALT_COMPAT_TOOL = "proton_experimental"


# ---------------- binary VDF (shortcuts.vdf) ----------------
def _w_str(f, k, v):
    f.write(b"\x01" + k.encode("utf-8") + b"\x00" + v.encode("utf-8") + b"\x00")

def _w_int(f, k, v):
    f.write(b"\x02" + k.encode("utf-8") + b"\x00" + struct.pack("<I", v & 0xFFFFFFFF))

def _w_map(f, k, d):
    f.write(b"\x00" + k.encode("utf-8") + b"\x00")
    _write_map(f, d)
    f.write(b"\x08")

def _write_map(f, d):
    for k, v in d.items():
        t, val = v
        if t == "str":
            _w_str(f, k, val)
        elif t == "int":
            _w_int(f, k, val)
        elif t == "map":
            _w_map(f, k, val)

def _read_map(f):
    d = {}
    while True:
        t = f.read(1)
        if not t or t == b"\x08":
            return d
        if t == b"\x00":
            k = _read_cstr(f)
            d[k] = ("map", _read_map(f))
        elif t == b"\x01":
            k = _read_cstr(f)
            d[k] = ("str", _read_cstr(f))
        elif t == b"\x02":
            k = _read_cstr(f)
            d[k] = ("int", struct.unpack("<I", f.read(4))[0])
        else:
            raise ValueError("unknown vdf type %r" % t)

def _read_cstr(f):
    out = bytearray()
    while True:
        c = f.read(1)
        if c == b"\x00" or not c:
            break
        out += c
    return out.decode("utf-8", "replace")

def read_shortcuts(path):
    f = open(path, "rb")
    root = f.read(1)
    if root != b"\x00":
        raise ValueError("not a binary vdf")
    name = _read_cstr(f)
    data = _read_map(f)
    return name, data

def write_shortcuts(path, entries):
    # entries: list of dict
    root = {}
    for i, e in enumerate(entries):
        root[str(i)] = ("map", e)
    with open(path, "wb") as f:
        f.write(b"\x00shortcuts\x00")   # root key "shortcuts" -> map
        _write_map(f, root)               # contents of the shortcuts map
        f.write(b"\x08")                 # end shortcuts map
        f.write(b"\x08")                 # end root object


def make_entry(name, exe, startdir, appid):
    return {
        "appid": ("int", appid),
        "AppName": ("str", name),
        "Exe": ("str", '"%s"' % exe),
        "StartDir": ("str", '"%s"' % startdir),
        "icon": ("str", ""),
        "ShortcutPath": ("str", ""),
        "LaunchOptions": ("str", ""),
        "IsHidden": ("int", 0),
        "AllowDesktopConfig": ("int", 1),
        "AllowOverlay": ("int", 1),
        "OpenVR": ("int", 0),
        "Devkit": ("int", 0),
        "DevkitGameID": ("str", ""),
        "DevkitOverrideAppID": ("int", 0),
        "LastPlayTime": ("int", 0),
        "FlatpakAppID": ("str", ""),
        "tags": ("map", {}),
    }


def app_id(exe, name):
    """Steam 非Steam游戏 appid = crc32("<exe>" + "<name>") | 0x80000000"""
    return (zlib.crc32(('"%s"' % exe).encode("utf-8") + name.encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


# ---------------- helpers ----------------
def profiles():
    out = []
    for d in glob.glob(os.path.join(USERDATA, "*")):
        if os.path.isdir(os.path.join(d, "config")):
            out.append(d)
    return sorted(out, key=lambda p: os.path.getmtime(p), reverse=True)


def shortcuts_path(profile):
    return os.path.join(profile, "config", SHORTCUTS_NAME)


def steam_running():
    r = subprocess.run(["pgrep", "-x", "steam"], capture_output=True)
    return r.returncode == 0


def find_launcher():
    hits = []
    pat = os.path.join(STEAM, "steamapps/compatdata", "*", LAUNCHER_REL)
    for p in glob.glob(pat):
        try:
            hits.append((os.path.getmtime(p), p))
        except OSError:
            pass
    hits.sort(reverse=True)
    return [p for _, p in hits]


def compat_prefix_for_exe(exe):
    """根据 launcher.exe 路径推断 <appid> 目录名"""
    m = os.path.join(STEAM, "steamapps/compatdata")
    exe = os.path.abspath(exe)
    if exe.startswith(m + os.sep):
        return exe[len(m) + 1:].split(os.sep)[0]
    return None


def set_compat_tool(appid, tool=COMPAT_TOOL):
    """把 config.vdf 里加上 CompatToolMapping，让该 appid 强制用指定 Proton"""
    cfg = os.path.join(STEAM, "config", "config.vdf")
    if not os.path.exists(cfg):
        return False
    txt = open(cfg, encoding="utf-8", errors="replace").read()
    anchor = '"CompatToolMapping"\n\t\t\t\t{\n'
    if anchor not in txt:
        anchor = '"CompatToolMapping"\n\t\t\t\t{'
    key = '"%d"' % appid
    block = ('\t\t\t\t\t"%d"\n'
             '\t\t\t\t\t{\n'
             '\t\t\t\t\t\t"name"\t\t"%s"\n'
             '\t\t\t\t\t\t"config"\t\t""\n'
             '\t\t\t\t\t\t"priority"\t\t"250"\n'
             '\t\t\t\t\t}\n' % (appid, tool))
    if key in txt.split("CompatToolMapping", 1)[1][:2000]:
        print("[=] CompatToolMapping 已存在 %d" % appid)
        return True
    shutil.copy2(cfg, cfg + ".bak")
    if anchor.endswith("{"):
        new = txt.replace('"CompatToolMapping"\n\t\t\t\t{',
                          '"CompatToolMapping"\n\t\t\t\t{\n' + block, 1)
    else:
        new = txt.replace(anchor, anchor + block, 1)
    if new == txt:
        print("[!] 未能插入 CompatToolMapping（锚点不匹配）")
        return False
    open(cfg, "w", encoding="utf-8").write(new)
    print("[+] 已写入 CompatToolMapping: %d -> %s" % (appid, tool))
    return True


# ---------------- commands ----------------
def cmd_show():
    for prof in profiles():
        sp = shortcuts_path(prof)
        print("profiles:", prof, "shortcuts存在:", os.path.exists(sp))
        if os.path.exists(sp):
            name, data = read_shortcuts(sp)
            for k, v in data.items():
                if v[0] == "map":
                    d = v[1]
                    print("  [%s] appid=%s name=%s exe=%s" % (
                        k, d.get("appid", (None, "?"))[1],
                        d.get("AppName", (None, "?"))[1],
                        d.get("Exe", (None, "?"))[1]))
        print("  appid计算示例:", app_id("/home/deck/Games/yysls/yysls_1.9.36_netease_setup.exe", "燕云十六声（国服）"))


def cmd_add(exe, name):
    exe = os.path.abspath(exe)
    if not os.path.exists(exe):
        print("[!] 找不到文件:", exe); sys.exit(1)
    if steam_running():
        print("[!] Steam 正在运行，请先退出 Steam 再执行（否则会被覆盖）"); sys.exit(1)
    prof = profiles()[0]
    sp = shortcuts_path(prof)
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    startdir = os.path.dirname(exe)
    aid = app_id(exe, name)
    entry = make_entry(name, exe, startdir, aid)
    write_shortcuts(sp, [entry])
    print("[+] 写入:", sp)
    print("    appid = %d (0x%08x)" % (aid, aid))
    print("    Exe   = %s" % exe)
    set_compat_tool(aid)
    # 校验
    n, d = read_shortcuts(sp)
    e = d["0"][1]
    print("[✓] 校验: name=%s exe=%s appid=%s" % (e["AppName"][1], e["Exe"][1], e["appid"][1]))


def cmd_repoint(new_exe):
    new_exe = os.path.abspath(new_exe)
    if not os.path.exists(new_exe):
        print("[!] 找不到文件:", new_exe); sys.exit(1)
    if steam_running():
        print("[!] Steam 正在运行，请先退出 Steam 再执行"); sys.exit(1)
    prof = profiles()[0]
    sp = shortcuts_path(prof)
    name, data = read_shortcuts(sp)
    idx = list(data.keys())[0]
    e = data[idx][1]
    old = e["Exe"][1]
    e["Exe"] = ("str", '"%s"' % new_exe)
    e["StartDir"] = ("str", '"%s"' % os.path.dirname(new_exe))
    write_shortcuts(sp, [e])
    print("[+] 目标已改:\n    旧: %s\n    新: %s" % (old, new_exe))
    print("    appid 保持不变:", e["appid"][1])


def cmd_find_launcher():
    hits = find_launcher()
    if not hits:
        print("[-] 还没找到 launcher.exe（游戏可能尚未安装完成）")
        print("    将搜索: %s/steamapps/compatdata/*/%s" % (STEAM, LAUNCHER_REL))
        sys.exit(0)
    for p in hits:
        print("[+] %s" % p)
        print("    起始位置: %s" % os.path.dirname(p))
        pref = compat_prefix_for_exe(p)
        print("    compatdata 前缀: %s" % pref)


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "add":
        cmd_add(sys.argv[2], sys.argv[3])
    elif cmd == "repoint":
        cmd_repoint(sys.argv[2])
    elif cmd == "find-launcher":
        cmd_find_launcher()
    elif cmd == "show":
        cmd_show()
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
