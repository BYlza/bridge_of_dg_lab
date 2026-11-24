# Bridge of DG-LAB

把《死亡搁浅》里 Sam 的伤痛，变成你身上真实的触觉反馈。

> 一个独立运行的连接器：**只读**游戏内存 → 识别受伤/状态 → 驱动 [郊狼 3.0](https://www.dungeon-lab.com/) 脉冲输出。
> 不改游戏文件，不注入进程（v1），不需要任何游戏内 Mod。

**当前状态：v0.1 骨架可运行。** 协议层已基于 [PyDGLab-WS](https://github.com/Ljzd-PRO/PyDGLab-WS) 打通并通过自检（M0）；血量偏移待实测填入（M1），详见 [docs/TESTING.md](docs/TESTING.md)。

---

## ⚠️ 使用前必读（安全）

本项目控制的是**会向人体输出电流的物理设备**。请把它当医疗器械级别的严肃事项对待：

- **从最低强度开始**（建议 3–8），逐档上升；先脱离游戏、单独测试能承受的上限，再把上限写进配置。
- **电极禁止**放在心脏区域、胸前左右两侧、颈部、头面部、伤口或破损皮肤上。
- 装有心脏起搏器、胰岛素泵等植入式设备者**禁止使用**。
- 不要在驾驶、进食、洗浴、独处无人看护、饮酒或疲劳状态下使用。
- 单次连续使用建议不超过 30 分钟；出现灼痛、皮肤发红、心悸立即停止。
- **最终安全手段永远是物理断电**——直接关掉郊狼主机电源。

作者不对任何人身伤害、设备损坏、存档损坏或账号问题负责。**仅限单人离线模式使用。**

---

## 快速开始

环境：Windows + Python 3.12 + 已安装 `pymem` / `qrcode` / `pydglab-ws`。

```powershell
cd <项目根目录>

python tests/test_protocol.py     # 协议自检，不需要手机与设备
python tools/bench_ws.py          # M0：扫码后手动调强度、试波形
python -m bridge                  # 预览：连设备、读血量、只打印，不输出
python -m bridge --arm            # 武装：受伤才真的输出
```

- 通道用 `--channel A|B|dual` 选，默认 A；
- 强度上限、曲线参数都在 `config/app.json`；
- 多网卡 / 手机热点下扫码连不上：`--host-ip 192.168.137.1`（Windows 移动热点的默认网关地址，请按自己实际网段调整）。

完整分步测试见 [docs/TESTING.md](docs/TESTING.md)。

---

## 一、有没有人做过？——调研结论

**结论：《死亡搁浅》没有现成的郊狼连接器，但周边每一块都有成熟零件可以拼。**

### 1.1 郊狼侧：官方与社区都很成熟

| 项目 | 说明 |
|---|---|
| [DG-LAB-OPENSOURCE](https://github.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE) | 官方开源仓库，含 Socket 控制协议、BLE 协议、示例波形 |
| [open-DGLAB-controller](https://github.com/open-toys-controller/open-DGLAB-controller) | 郊狼 3.0 网络控制器，WebSocket API，任意语言可接 |
| [PyDGLab-WS](https://github.com/Ljzd-PRO/PyDGLab-WS) | Python 协议库，`asyncio`，自带 WS 服务端 + 本地终端，**本项目首选底座** |
| [DG-Lab-Coyote-Game-Hub](https://github.com/hyperzlib/DG-Lab-Coyote-Game-Hub) | 游戏控制器（战败惩罚），提供 HTTP API / MCP / CE SDK，社区插件多 |
| [dglab-kit](https://www.npmjs.com/package/dglab-kit) | Node SDK，内置官方波形数据（省去自己拼十六进制帧） |

⚠️ Socket 控制**仅支持郊狼 3.0 主机**（2.0 只能走蓝牙直连或 Game Hub 的蓝牙方案）。

### 1.2 游戏侧：已有三个同类先例

| 项目 | 做法 | 可借鉴点 |
|---|---|---|
| [HL2-DGLabInjuryExperienceMod](https://github.com/Ljzd-PRO/HL2-DGLabInjuryExperienceMod) | Source Mod + 独立 `PyDGLab-WS Connector.exe`，两个 WS 端口分别接 App 和游戏 | **架构几乎就是我们要的**：游戏侧只发事件，连接器负责协议与安全 |
| [Blank-DG-LAB](https://github.com/kskongbai/Blank-DG-LAB)（我的世界） | 伤害类型分倍率、低血量每 10% 一档持续脉冲、A/B 独立通道、软上限、暂停检测 | 映射模型与安全设计的参考 |
| [TazeU](https://star-whisper9.github.io/sts2-tazeu/zh/development/protocol.html)（杀戮尖塔 2） | 幂律校准强度、游戏内二维码直连、可视化调参 | 强度曲线与调参交互 |
| [IsaacCoyote](https://github.com/QDis233/IsaacCoyote)（以撒） | 游戏内 Mod 发事件 + 独立 `IsaacCoyote.exe` 控制器，`config.yaml` 热重载 | **外部控制器 + 游戏内 Mod** 的分工样板 |

三者共同的工程共识：**游戏侧只管"发生了什么"，强度、波形、上限、急停全部放在连接器里**。这条边界要守住。

### 1.3 死亡搁浅侧：工具链够用，但没有现成联动

- **没有官方 Mod SDK**。Mod 生态靠两块：
  - **资源层**：[Decima Workshop](https://github.com/ShadelessFox/decima) 可解包/编辑 `.core` 与打包 `.bin`（改数值、模型、贴图）。
  - **代码层**：ASI 代理 DLL（把 `dinput8.dll` 改名为 `version.dll` 放进游戏根目录）+ `.asi` 插件；[Walking-Man](https://github.com/HamzaEzzRa/Walking-Man) 已证明可用 MinHook 直接挂游戏内部系统。
- **内存层数据是公开的**：[Cheat Engine 表](https://www.nexusmods.com/deathstranding/mods/19) 长期维护血量、意识、耐力、BB 压力、货物耐久、载具电池等指针，原版与导演剪辑版都有。
- **外部只读内存有先例**：[DSDC Streaming Memory Unlock](https://github.com/PlushRapier145/dsdc-streaming-unlock) 用 `ReadProcessMemory` 从运行中的 `ds.exe` 读取真实显存预算。

也就是说：**读血量这条路是通的，只是没人把它接到郊狼上。**

---

## 二、技术方案

### 2.1 架构

```
┌──────────────┐   ReadProcessMemory    ┌──────────────────┐   WebSocket    ┌────────────┐
│  ds.exe      │ ◄───────────────────── │  Bridge of DG-LAB │ ─────────────► │ 郊狼 App   │
│ (游戏进程)    │   血量/耐力/BB 压力     │  (独立进程)        │  强度/波形      │ (手机)     │
└──────────────┘       16–33 ms 轮询    └──────────────────┘                └─────┬──────┘
                                                │                                 │ BLE
                                                │ 本地 WS :5678                    ▼
                                                │                          ┌────────────┐
                                                └──── 二维码扫码绑定 ──────► │ 郊狼 3.0 主机│
                                                                           └────────────┘
```

单进程即可完成全部工作（读内存 + 跑 WS 服务端 + 映射），不需要游戏内 Mod。

### 2.2 两条路线

| | **A. 外部内存读取（v1，推荐先做）** | **B. ASI 注入（v2，可选）** |
|---|---|---|
| 原理 | `OpenProcess` + `ReadProcessMemory` 读血量浮点，按变化量推断伤害 | `version.dll` 代理加载 `.asi`，MinHook 挂"写入血量"的指令或伤害函数 |
| 能拿到 | 当前数值、变化量 | 精确伤害事件、伤害类型、攻击者、是否致死 |
| 延迟 | 16–33 ms（够用，伤害反馈感知阈值约 50 ms） | < 5 ms |
| 稳定性 | 只读，几乎不会让游戏崩 | 注入有崩溃面，需处理卸载/异常 |
| 维护成本 | 偏移变了改配置 | 偏移变了要重找特征码、重编译 |
| 风险 | 无文件修改 | 修改进程内存，线上模式有风险 |

**建议：v1 只做 A，把映射和交互打磨好；确有精度需求再做 B，共用同一套事件接口。**

### 2.3 为什么不用 Cheat Engine 中转

CE + Lua 脚本 + 命名管道也能跑通，适合**验证阶段**（找偏移、看曲线），但不适合做分发：依赖 CE 安装、脚本易被杀软误报、无法做托盘 UI 与急停。定位偏移时用 CE，运行时用自己的程序。

---

## 三、游戏信号 → 触觉映射

郊狼有 A/B 两个独立通道，正好分成「**事件通道**」和「**状态通道**」。

| 信号 | 来源 | 通道 | 波形 | 强度 |
|---|---|---|---|---|
| 受到伤害 | 血量下降量 Δ | A | 短促打击（Batter/连击） | `Imax · (Δ/maxHP)^0.7` |
| 高处坠落 / 摔倒 | Δ 大且伴随垂直速度突变（可选） | A | 单次强脉冲 + 余震 | 接近上限，持续 0.5 s |
| 敌人枪击 | Δ 小但高频连续 | A | 锐利短脉冲 | 单发低，连击累加 |
| 被 BT 拖拽 | 意识/耐力异常 + 位置失控 | A | 递增持续 | 随拖拽时间上升 |
| 生命 < 30% | 血量绝对值 | B | 心跳节律 | 随血量下降加密、加强 |
| BB 压力升高 | BB 压力条 | B | 低频闷脉冲 | 默认关闭，可开 |
| 时间雨暴露 | 环境状态（需实测） | A | 极低频麻感 | 极低，缓慢累积 |
| 死亡 | 血量 → 0 | A | 满强度 3 s | 之后**强制归零** |
| 交货完成（可选） | 任务结算 | B | 一次"赞"脉冲 | 低 |

> 说明：具体内存字段与偏移**必须实测**，本文不提供未经核实的偏移值。血量可能是 `0–1` 归一化 float，也可能是分段整数；"意识/耐力/BB 压力"是独立字段。

### 3.1 强度模型

```
目标强度  I_target = Imax · clamp(Δ / maxHP, 0, 1)^γ        γ ≈ 0.7
实际上升  I ← min(I + rate · dt, I_target)                  rate ≈ 150 /s（限速）
自然衰减  I ← I · exp(-dt / τ)                              τ ≈ 1.2 s
死区      Δ / maxHP < 0.01 → 忽略（防抖、防误触）
```

幂律（γ<1）的意义：小伤害也有感知，大伤害不会瞬间顶到上限，比线性更"像受伤"。

**首版参数（已按实测调整）**：A 通道上限 1、B 通道上限 10，`gamma=0.7`，衰减 1.2 s。
注意上限为 1 时 A 通道只有 `0 / 1` 两档，等于"有反馈 / 没反馈"，只会在重伤害时出现；想要连续手感就把 `max_intensity_a` 提到 5–10 再试。

### 3.2 硬性安全层

1. **硬上限** `I_max = min(用户配置, App 上报的通道上限)`，代码里钳死，任何路径都不能越界。
2. **上升限速**：禁止一帧从 0 跳到 80。
3. **看门狗**：连续 3 s 没收到有效事件 → 强度归零并 `clear` 队列。
4. **急停热键**（默认 `F12`）：立即 `strength-1+2+0` / `strength-2+2+0` + `clear-A` / `clear-B`。
5. **状态守卫**：游戏暂停、菜单、读档、过场、进程退出、App 断连 → 一律静默归零。
6. **连续输出冷却**：A 通道累计输出超过 N 分钟 → 强制降到 0，冷却 60 s。
7. **启动自检**：读不到血量 / 进程名不符 / 特征码校验失败 → 拒绝输出并提示。

### 3.3 状态守卫（容易被忽略但很关键）

读档、传送、快速旅行、死亡重生都会让血量**瞬间跳变**。如果直接按"血量下降"判定，会在读档时给一发满强度。对策：

- 检测到游戏暂停/菜单/加载（可通过窗口状态 + 血量跳变 + 长时间无变化组合判断）→ 进入 `SUSPEND`，清空基线；
- 单帧变化超过 `30% maxHP` 视为"异常跳变"，只在非 SUSPEND 且前后帧连续时才认定为坠落伤害；
- 重生（血量从 0 恢复到满）后重置基线，不产生事件。

---

## 四、郊狼 Socket 协议速查

> 官方文档：[DG-LAB-OPENSOURCE/socket](https://github.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE/tree/main/socket)。以下为对接必需的要点。
> **实现层直接用 `pydglab-ws`，本表只用于排障和对照抓包**，不要照着自己重写协议。

**连接流程**：终端连上 WS 服务端 → 收到 `bind` 拿到自己的 `clientId` → 生成二维码给 App 扫 → 服务端转发绑定成功 → 之后所有消息带 `clientId` + `targetId`。

二维码内容格式：

```
https://www.dungeon-lab.com/app-download.php#DGLAB-SOCKET#ws://<ip>:<port>/<clientId>
```

消息统一为 JSON：

```json
{ "type": "msg", "clientId": "<终端uuid>", "targetId": "<App uuid>", "message": "指令字符串" }
```

| 指令 | message 格式 | 说明 |
|---|---|---|
| 设强度 | `strength-<ch>+<mode>+<value>` | `ch`: 1=A, 2=B；`mode`: 0=减, 1=加, 2=设为；`value`: 0–200 |
| 下发波形 | `pulse-<A\|B>:["<16位hex>", ...]` | 每帧 100 ms |
| 清空波形 | `clear-<A\|B>` | 切换波形前先清 |
| 强度回传（App→终端） | `strength-<A>+<B>+<limitA>+<limitB>` | 用来同步上限，**必须遵守** |
| 按钮反馈（App→终端） | `feedback-<n>` | 可做"确认/急停"按钮 |

示例：

```json
{ "type":"msg", "clientId":"...", "targetId":"...", "message":"strength-1+2+35" }
{ "type":"msg", "clientId":"...", "targetId":"...", "message":"pulse-A:[\"0A0A0A0A64646464\"]" }
```

**波形帧格式（V3）**：每帧 16 个十六进制字符 = 8 字节 = **4 个频率字节 + 4 个强度字节**，代表 100 ms，内含 4 组 25 ms 子脉冲：

```
[freq1][freq2][freq3][freq4][int1][int2][int3][int4]
```

- 强度字节 0–100（相对百分比），频率为 V3 压缩编码；
- 单次最多 100 帧（10 s），App 队列最多 500 帧（50 s），超出丢弃；
- 发送间隔应**略小于**数据时长，否则会断音；
- 消息总长上限 1950 字符，超长会被拒。

> 频率字节不要自己换算赫兹。直接复用官方 `DG_WAVES_V2_V3_simple.js` 里的帧数据，或 `dglab-kit` 的内置波形，自己拼极易出现"App 收到但打不出波形"。
>
> 协议存在版本差异（波形帧分 V2/V3 两套格式，部分新项目已在谈 V4），动手前以**官方仓库当前分支 + App 实测**为准，不要只信二手文档。

---

## 五、内存读取实现要点

### 5.1 定位血量（一次性工作，用 CE）

1. 进游戏，让 Sam 受一次可量化的伤（比如高处跳下）。
2. CE 附加 `ds.exe`，用「未知初始值 → 减少的数值 → 未变」反复扫描 float/dword。
3. 找到候选地址后，用「找出是什么改写了这个地址」确认它是真实血量写入点。
4. 指针扫描（Pointer Scan）得到静态链：`模块基址 + 偏移 → 一级指针 → ... → 血量`。
5. **不要只存偏移**。为每条链记录一段唯一的 AOB 特征码，启动时先校验特征码再取值，版本不符直接报错而不是读出垃圾数据。

### 5.2 运行时读取（Python 示例）

```python
import pymem

pm = pymem.Pymem("ds.exe")
base = pm.base_address

def read_ptr_chain(base, offsets):
    addr = base + offsets[0]
    for off in offsets[1:-1]:
        addr = pm.read_longlong(addr) + off
    return addr

hp_addr = read_ptr_chain(base, [0x0000000, 0x00, 0x00, 0x00])  # 占位，需实测
hp = pm.read_float(hp_addr)
```

轮询 30–60 Hz 足够；每次读取耗时约几十微秒，对帧率无影响。

### 5.3 多版本配置

```json
{
  "profile": "ds_directors_cut_1.004",
  "process": "ds.exe",
  "module": "ds.exe",
  "health": {
    "type": "float",
    "max": 1.0,
    "signature": "TODO_AOB_HEX",
    "pointer": { "module_offset": "0x0", "offsets": ["0x0", "0x0"] }
  },
  "stamina": { "...": "..." },
  "bb_stress": { "...": "..." }
}
```

原版与导演剪辑版的进程名、偏移都不同（原版多为 `DeathStranding.exe`，DC 为 `ds.exe`，以实际安装为准），**每个版本一份 profile**，启动时自动匹配，匹配不上就只做"预览模式"（能连郊狼、能手动测试，但不驱动游戏事件）。

### 5.4 可选：v2 注入路线

1. 用 Ultimate ASI Loader，把 `dinput8.dll` 改名 `version.dll` 放游戏根目录；
2. 编一个 `.asi`（本质是 x64 DLL），`DllMain` 里起线程等游戏就绪；
3. 用 MinHook 挂 5.1 中找到的写入指令，在 trampoline 里读旧值/新值算 Δ，并把伤害类型（若寄存器/调用栈可推断）一起打包；
4. 通过**命名管道**把事件发给连接器，连接器完全不用改。

这条路线的前提是先把 v1 的映射调好——**没有映射模型，注入了也没用**。

---

## 六、项目结构（规划）

```
bridge_of_dg_lab/
├─ README.md
├─ docs/
│  ├─ ONE-PAGER.md          # 一页纸方案
│  └─ TESTING.md            # 分步测试手册（M0 → M2）
├─ bridge/
│  ├─ __main__.py           # 入口：主循环 + 输出调度
│  ├─ dglab.py              # pydglab-ws 的线程安全薄封装 + 波形数据
│  ├─ qr.py                 # 二维码（终端 ASCII + out/qr.svg）
│  ├─ reader.py             # 进程/模块/指针链/AOB 校验
│  ├─ mapper.py             # 强度模型、限速、衰减、通道分配
│  ├─ safety.py             # 硬上限、看门狗、急停、冷却
│  └─ config.py             # 配置加载
├─ config/
│  ├─ app.json              # 用户配置（通道、上限、曲线）
│  └─ profiles/             # 每游戏版本一份内存 profile
├─ tools/
│  ├─ hp_probe.py           # 验证/写入血量指针链
│  └─ bench_ws.py           # 只测郊狼链路，手动打波形
├─ tests/
│  └─ test_protocol.py      # 不依赖设备的协议自检
└─ out/                     # 二维码、日志等运行产物
```

---

## 七、路线图

| 阶段 | 目标 | 验收标准 |
|---|---|---|
| **M0** | 协议打通 | ✅ 已完成（协议自检通过；待手机实测） |
| **M1** | 读到血量 | `hp_probe.py` 打印的血量曲线与游戏血条一致；跳崖/中枪/喝血包都能对应上 |
| **M2** | 映射引擎 | 受伤有反馈、死亡有强反馈、读档不误触、暂停立即静默 |
| **M3** | 可分发 | 绿色包、首次运行引导、安全说明、崩溃日志 |
| **M4** | 注入增强（可选） | 事件延迟 < 5 ms，能区分伤害类型 |

---

## 八、风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 游戏更新导致偏移失效 | 功能全废 | AOB 校验 + 版本 profile + 失败即拒绝输出 |
| 读档/传送误判为受伤 | 突然满强度，体验极差且有安全风险 | SUSPEND 状态机 + 跳变过滤 + 重生基线重置 |
| 长时间连续输出 | 皮肤灼伤、适应性下降 | 冷却计时 + 默认关闭持续型映射（B 通道） |
| 杀软误报 | 无法分发 | 只读内存、不写文件、开源、提供源码构建 |
| 线上模式 | 账号风险 | 默认检测到在线会话即禁用；文档明确只支持离线 |
| 直播平台规则 | 封禁 | 不含成人内容，README 明示；用户自负 |

---

## 九、参考

- DG-LAB 官方开源（Socket / BLE 协议、示例波形）：<https://github.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE>
- PyDGLab-WS 文档：<https://pydglab-ws.readthedocs.io>
- 郊狼游戏中心：<https://github.com/hyperzlib/DG-Lab-Coyote-Game-Hub>
- HL2 受伤 Mod（架构参考）：<https://github.com/Ljzd-PRO/HL2-DGLabInjuryExperienceMod>
- 我的世界 Blank-DG-LAB（映射参考）：<https://github.com/kskongbai/Blank-DG-LAB>
- TazeU 协议详解（波形格式）：<https://star-whisper9.github.io/sts2-tazeu/zh/development/protocol.html>
- Decima Workshop（资源 Mod）：<https://github.com/ShadelessFox/decima>
- Death Stranding CE 表（内存指针）：<https://www.nexusmods.com/deathstranding/mods/19>
- 外部只读内存先例：<https://github.com/PlushRapier145/dsdc-streaming-unlock>

---

## 十、声明

本项目与 Kojima Productions、505 Games、Sony Interactive Entertainment、DG-LAB 均无关联。
不分发任何游戏资源或游戏代码；v1 不修改游戏可执行文件与存档。
"死亡搁浅"及相关名称归其权利人所有。

License: TBD（建议 MIT，与 PyDGLab-WS 生态一致）。
