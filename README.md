# 燕云十六声（国服）SteamOS 兼容配置

把**网易国服 PC 端**（无 Linux 版、无 Steam 版）在 **SteamOS / Arch 系**上跑起来所需的全部
兼容层配置、启动脚本和防护服务，打包成**即食即用**的一键安装。

> 本仓库只包含「兼容配置」，**不含游戏本体**。游戏通过网易官方安装器下载，装在 Proton 前缀里。
> 适用于 Steam Deck / SteamOS，也适用于普通 Arch + Steam 的桌面。

---

## 为什么需要这些东西

燕云国服在 Proton 下能跑，但有 4 个坑，任其发展都会毁掉体验：

| 坑 | 现象 | 本仓库的对策 |
|---|---|---|
| **官方启动器显存泄漏** | `launcher.exe`（Qt）以 ~100–120 MB/s 泄漏显存，几分钟涨到 ~43 GB，撑爆 16G 显存 + GTT，触发 amdgpu/TTM `ww_mutex` 死锁，**整个桌面冻死，只能硬重启** | ① 强制 Qt **软件渲染**（`QT_OPENGL=software` / `QT_QUICK_BACKEND=software`，写进前缀注册表）；② `vram-guard.service` 看门狗兜底 |
| **游戏本体闪退** | 游戏是 **D3D12**，普通 `wine` 会挑自带的旧 vkd3d，一进 `InitState` 就空指针崩溃（`0xC0000005`） | 按 Proton 的做法强制 **VKD3D-Proton + DXVK** DLL 覆盖与搜索路径（写进 `yysls-launcher.service` 与燕窝配方） |
| **4K 下界面极小** | Wine 默认 96 DPI，而 KDE 缩放到 300%（`Xft.dpi=288`），启动器界面小到没法点 | 前缀注册表写入 `LogPixels`（自动读取 `Xft.dpi`） |
| **下载被误杀 → 重下 125G** | 直接 `SIGKILL` / 硬重启打断下载，启动器会判定「数据损坏」**从头重下 ~125G** | `vram-guard` 杀进程前先发 `WM_DELETE_WINDOW` 让它「正常关闭」；收尾顺序固定为 关窗 → 等 6s → TERM → KILL |

另外还顺手关掉了启动器的宠物特效（`pet_settings.ini`），进一步省显存/CPU。

---

## 即食即用（3 步）

前置：SteamOS 桌面模式（或 Arch + Steam），已安装 **GE-Proton**（推荐 `GE-Proton10-32` 或更新），
**能在 Steam 里跑到网易官方安装器**（参考 [docs/操作步骤.md](docs/操作步骤.md) 第 1–2 步）。

```bash
git clone https://github.com/Abelliuxl/yysls-steamos.git
cd yysls-steamos
./install.sh --steam        # 装脚本+服务+注册表修复，并把 Steam 快捷方式指回 launcher.exe
```

`install.sh` 会自动：

1. 找到 `launcher.exe` 与 Proton 前缀（`compatdata/<appid>/pfx`），推断 appid；
2. 把 `vram-guard.py` / `steam-shortcut.py` / `post-install.sh` 装到 `~/.local/share/yysls-steamos/`；
3. 用 GE-Proton 的 wine 写入注册表：**软件渲染 + DPI**；
4. 写入 `pet_settings.ini` 关闭宠物特效；
5. 生成并启用两个 systemd 用户服务：
   - `vram-guard.service` —— **立即启用并运行**（安全阀，必开）；
   - `yysls-launcher.service` —— 安装但**不**开机自启（下载/更新时手动起）；
6. `--steam` 时停止 Steam → 改写快捷方式目标 → 重启 Steam（保留 appid，避免重下）。

装完在 Steam 库里点「燕云十六声（国服）」即可（登录流程见 [docs/操作步骤.md](docs/操作步骤.md)）。

> 不想 `--steam`？也可以只跑 `./install.sh`，之后再手动运行
> `~/.local/share/yysls-steamos/post-install.sh`。

### 常用命令

```bash
systemctl --user status vram-guard      # 显存守卫状态
systemctl --user start  yysls-launcher  # 需要下载/更新时手动起启动器
tail -f ~/.local/state/yysls-steamos/vram-guard.log
~/.local/share/yysls-steamos/steam-shortcut.py show   # 查看 Steam 快捷方式
```

---

## 目录结构

```
yysls-steamos/
├── install.sh                     一键安装/修复（本仓库主角）
├── scripts/
│   ├── vram-guard.py              显存看门狗（唯一必须常驻的进程）
│   ├── post-install.sh            装完游戏后：把 Steam 快捷方式指回 launcher.exe
│   └── steam-shortcut.py          Steam 非 Steam 快捷方式读写（保留 appid）
├── systemd/
│   ├── vram-guard.service.in      模板（install.sh 代入路径后生成）
│   └── yysls-launcher.service.in  模板（含 VKD3D-Proton / DXVK 环境）
├── conf/
│   └── pet_settings.ini           关闭启动器宠物特效
└── docs/
    ├── 操作步骤.md                从下载安装到进游戏的完整步骤
    └── 排错.md                    常见问题排查
```

---

## 关键配置逐条解释

### 1. 强制 Qt 软件渲染（救命项，别删）

启动器是 Qt Quick 程序，走 GPU 渲染时会持续泄漏 GPU 缓冲，最终冻结整个系统。
写进前缀注册表后，**任何**启动方式都生效：

```
HKCU\Environment  QT_OPENGL        = software
HKCU\Environment  QT_QUICK_BACKEND = software
```

手动回滚（界面会恢复 GPU 渲染，但也可能恢复泄漏）：

```bash
rm "$PREFIX/user.reg"  # ❌ 别这样
# 正确：用 wine reg delete
```

### 2. VKD3D-Proton / DXVK DLL 覆盖

游戏是 D3D12。下面这些环境变量让 wine 使用 **VKD3D-Proton** 而不是自带的旧 vkd3d：

```bash
WINEDLLPATH=<GE>/files/lib/wine/vkd3d-proton:<GE>/files/lib/vkd3d:<GE>/files/lib/wine
WINEDLLOVERRIDES=d3d11=n,b;d3d10core=n,b;d3d10=n,b;d3d10_1=n,b;d3d9=n,b;dxgi=n,b;d3d12=n,b;d3d12core=n,b
```

验证方法：游戏进程 `/proc/<pid>/maps` 里**只有 `d3d12core`、没有 `libvkd3d`** = 用上了。✅

### 3. DPI

4K + KDE scale 3 时 `Xft.dpi=288`，`install.sh` 自动读取并写入：

```
HKCU\Control Panel\Desktop  LogPixels = 288
HKCU\Software\Wine\Fonts    LogPixels = 288
```

如果你的显示器/缩放不同，用 `./install.sh --dpi 192` 覆盖。

### 4. 显存看门狗 `vram-guard`

只读 `/sys/class/drm/*/mem_info_*` 与该前缀下每个进程的 `fdinfo`，**不改游戏**：

- **启动器阶段**：只盯 `launcher.exe` 自身（软件渲染下应稳定在 ~90MB；>2G 或 60s 涨 >2G 就杀）；
- **游戏阶段**：发现别的大进程在跑就切到游戏阈值（全局 vram >97% / GTT >92% / 60s 涨 >6G）；
- **杀之前**先给窗口发 `WM_DELETE_WINDOW`，等 6 秒让它自己退，避免下载缓存被判损坏。

可调环境变量（写进 `systemd/vram-guard.service` 的 `Environment=`）：

```
YYSLS_APPID=2885173776                # 游戏 appid（= compatdata 目录名）
YYSLS_VRAM_LOG=~/.local/state/.../vram-guard.log
VRAM_GUARD_INTERVAL=2                 # 轮询秒
VRAM_GUARD_LAUNCHER_GB=2              # 启动器自身显存上限
VRAM_GUARD_GROWTH_GB=2                # 60s 内增长上限
VRAM_GUARD_DRYRUN=1                   # 只记录不杀（调试）
```

---

## 已知限制 / 注意

- **反作弊是唯一变数**：目前能跑说明是用户态反作弊；若网易某次加入内核级反作弊
  （ACE 类），Proton 会失效。游戏更新后请重新确认。
- **game mode 首次登录**：历史版本里登录窗会被年龄提示卡遮挡。本仓库配套的
  [燕窝 Yanwo](https://github.com/Abelliuxl/yanwo) 启动器已在窗口层修复
  （把登录窗钉成 gamescope 显示窗 + 收掉年龄卡），现在**无需切桌面登录**。
  若你只用裸 Steam 启动、不用燕窝，遇到遮挡可临时切桌面模式登录一次（登录状态会保留）。
- **不要中途 kill -9 正在下载的启动器**：会触发「数据损坏」重下 ~125G。
  需要停就 `systemctl --user stop yysls-launcher`（走正常退出）。
- 本仓库**不分发游戏**，也不提供账号；请通过网易官方渠道获取。

---

## 相关项目

- [燕窝 Yanwo](https://github.com/Abelliuxl/yanwo) —— 第三方游戏聚合启动器，带手柄输入桥与
  gamescope 窗口规则引擎，本仓库的配置就是它的底层兼容层。

## 授权 / License

本项目采用 **GNU GPL-3.0**（GNU General Public License v3.0）。完整法律文本见 [LICENSE](LICENSE)。

- 可自由使用、修改、分发，甚至商用；
- 但你分发**修改版 / 衍生作品**时，必须**同样以 GPL-3.0 开源**并提供完整源代码；
- 必须保留版权与许可声明。

> GPL-3.0 是 OSI 认可的 copyleft 开源协议；它**不禁止**他人商用，只要求衍生作品开源。
