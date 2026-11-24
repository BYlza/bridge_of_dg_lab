# 测试手册（导演剪辑版 1.004）

按顺序做，每一步都有明确的"通过标准"。**任何一步不对劲就停下来告诉我现象**，不要硬着头皮往下走。

准备：手机装好 DG-Lab App，郊狼 3.0 主机开机并与手机连上，手机和电脑在同一个 Wi-Fi。

> **网络先确认一下**：手机必须能直接访问电脑。把电脑和手机接到**同一个** Wi-Fi / 热点，然后在电脑上用 `ipconfig` 查当前网卡的 IPv4 地址（形如 `192.168.x.x`），这就是二维码里应该出现的地址。
> 二维码里的 IP 是**程序启动那一刻**取的，换网络后要重启程序重新生成，否则手机拿到的是旧地址。
>
> 下面的命令默认你 PowerShell 里的 `python` 就是 Python 3.12 且已装 pydglab-ws/pymem/qrcode。若机器上有多个 Python，把 `python` 换成全路径。

---

## 测试 0：协议自检（10 秒，不需要设备）

```powershell
cd <项目根目录>
python tests\test_protocol.py
```

**通过标准**：输出 7 个 ✓ 和"全部通过"。

---

## 测试 1：M0 —— 打通「电脑 ↔ App ↔ 主机」

```powershell
python tools\bench_ws.py
```

1. 终端会打印一个二维码（同时写入 `out/qr.svg`，看不清楚就用浏览器打开它）。
2. 手机 App → **SOCKET 控制** → 扫码。
3. 看到 `>>> 已绑定` 就说明链路通了。

### 扫码报「服务器连接失败」——按这个顺序排

1. **二维码里的 IP 过期了。** 换过网络（有线 → 手机热点、换 Wi-Fi）之后，旧二维码还指向旧地址，必须 Ctrl+C 重启程序重新生成。
   用 `ipconfig` 看 `WLAN` 那行的 IPv4，和二维码里的地址对一下。
2. **Windows 防火墙拦了入站。** 普通 PowerShell 里跑这一行，会弹 UAC 提权：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File <项目根目录>\tools\allow_firewall.ps1
   ```

   或直接开一个**管理员 PowerShell**（Win+X → 终端(管理员)）执行：

   ```powershell
   New-NetFirewallRule -DisplayName "Bridge of DG-LAB" -Direction Inbound -Protocol TCP -LocalPort 5678 -Action Allow -Profile Any
   ```

   WLAN 被识别成「公用网络」时，不放行就一定连不上。
   验证（注意规则名是 `Bridge of DG-LAB`，不带端口号）：

   ```powershell
   netsh advfirewall firewall show rule name="Bridge of DG-LAB"
   ```
3. **验证到底通不通**：手机浏览器打开 `http://<二维码里的IP>:5678`。
   - 立刻出现错误页/空白 → 网络通，问题在 App 或绑定流程；
   - 一直转圈/提示无法访问 → 网络不通，继续下一步。
4. **热点隔离 / 安全软件**：手机开热点给电脑用时，个别机型会隔离客户端；火绒这类安全软件的 WFP 驱动在「退出托盘」后仍会拦入站。
5. **不要用 TLS 隧道**：实测（ngrok `wss://` 从电脑侧握手成功、App 侧秒失败）**DG-LAB App 只接受 `ws://`**，ngrok / Cloudflare Tunnel 这类只给 `wss://` 的方案不可用。
   需要跨网时必须用提供 `ws://` 私有 IP 的组网：Tailscale / ZeroTier，然后 `--host-ip 100.x.x.x`。

依次输入：

| 输入 | 预期 |
|---|---|
| `a 5` | 主机 A 通道出**很轻**的感觉；终端打印 `[App] 强度 A=5 ... 上限 A=200` |
| `a 10` | 明显一点 |
| `p hit 3` | 3 秒锐利波形，结束后自动归零 |
| `p rumble 3` | 闷一点的振动 |
| `0` | 立刻停止 |

**通过标准**：三条命令都有反应，波形连续不断音，`0` 能立刻停。

**需要你告诉我的**：
- 终端里 `[App] 强度 A=.. 上限 A=..` 的上限是多少？
- 你主观能舒服承受的上限大概是多少档（这决定配置里的 `max_intensity`）？
- `p hit` 的波形是"刺痛"还是"发麻"？有没有断音？

> 如果扫码后连不上：先确认手机和电脑同一网段；再放行 Windows 防火墙里 Python 的入站；实在不行换端口 `--port 5680` 重新扫码。

---

## 测试 2：M1a —— 确认能读到游戏进程

启动游戏，**进入存档、能自由走动**（不要停在主菜单），然后：

```powershell
python tools/hp_probe.py --list-modules
```

**通过标准**：打印 `进程 ds.exe，模块基址 0x...`，并列出模块，第一行应该是 `ds.exe`，大小约 82.5 MB。

> 如果报"附加失败"，多半是权限：用管理员 PowerShell 再跑一次。

---

## 测试 3：M1b —— 找血量地址

> **好消息：不用 CE 了。** 血量结构是 `[int32 HP][float HP/1000][float HP/1000]` 三连，
> 只要报一个血条上的数字就能唯一定位：
>
> ```powershell
> python tools\relocate.py --hp 767 --write
> ```
>
> 扫全部内存约 80 秒。死亡 / 读档 / 重启游戏后地址会变，重新跑一次这条即可。
> 下面的 CE 流程保留作备用（指针扫描、更细的字段）。

这一步目前只能靠 CE，因为要跑指针扫描。装 CE 时注意安装器里的捆绑选项全部取消。

1. 下载安装 [Cheat Engine](https://www.cheatengine.org/downloads.php) 7.5 或更新。
2. 打开 CE → 左上角电脑图标 → 选 `ds.exe` → 附加。
3. 回游戏，让 Sam **受一次明确的伤**（找个小坡跳下去，或者让骡子打一枪）。
4. CE 里：
   - Value Type 选 **Float**；
   - 第一次选 **Unknown initial value** → First Scan；
   - 受伤后选 **Decreased value** → Next Scan；
   - 站着不动选 **Unchanged value** → Next Scan；
   - 重复"受伤→Decreased / 静止→Unchanged"三五轮，直到候选降到几十个以内。
   - 如果候选还是太多：吃一个血包（回血）后选 **Increased value**，能砍掉一大半。
5. 把剩下的候选双击加入下方列表。回游戏再受一次伤，**盯着列表里哪个值跟着血条变**——那就是血量。
   - 顺手确认数值范围：是 0–1 的归一化值，还是 0–100 / 0–1000 的整数刻度。
6. 右键这条地址 → **Find out what writes to this address** → 回游戏受伤 → 会出现一条（或几条）指令。记下指令地址，先别关。
7. 右键那条地址 → **Pointer scan for this address**：
   - Maximum level 先设 4；
   - 勾上 "Compare results with other saved pointer paths"（可选）；
   - 扫完会得到一大堆结果，先别慌。
8. **关掉游戏再重开**，重新进存档、重新受一次伤，CE 重新附加以找到新的血量地址；用 Pointer scan 窗口的 **Rescan memory** 指向新地址，过滤掉失效的链。
9. 剩下的链里挑 **层级最浅、偏移最规整**（比如都是 0x10/0x20/0x8 这种）的一条，形如：

   ```
   [[ds.exe+01A2B3C4] + 0x10 + 0x20 + 0x8]
   ```

10. 把这条链填进项目：

```powershell
python tools/hp_probe.py --set-chain 0x1A2B3C4 0x10 0x20 0x8 --max 1.0
```

（`--max` 按第 5 步确认的范围改：0–1 就写 1.0，0–100 就写 100）

**通过标准**：命令回显"已写入"，并且打印出的链与你 CE 里看到的一致。

---

## 测试 4：M1c —— 验证读数

```powershell
python tools/hp_probe.py --watch
```

依次做这些动作，观察终端：

| 动作 | 预期输出 |
|---|---|
| 站着不动 5 秒 | 偶尔一行"(静止)"，数值**完全不变** |
| 跳崖 / 挨打 | `← 掉血`，且掉血幅度与血条变化一致 |
| 喝血包 / 睡觉 | `← 回血` |
| 读档 / 快速旅行 | `← 大幅跳变` 标记 |

**通过标准**：静止时不抖、受伤必掉、回血必升。**如果静止时数值自己在跳，说明地址找错了**（可能是体力/耐力/某个动画参数），回测试 3 重找。

---

## 测试 5：M2 —— 完整联动

先预览（不会真的输出，只打印判定结果）：

```powershell
python -m bridge --channel A
```

扫码后进游戏走动、受伤，看终端是否打印：

```
受伤 -0.0620（6.2%）→ 目标强度 12.3
  血量=0.9380  强度= 12.3  dry  上限=20 看门狗=3s
```

确认「受伤才响、读档不响、静止不响」之后，再把上限压低一点，武装：

```powershell
# 先把 config/app.json 里 max_intensity 改成 8
python -m bridge --arm --channel A
```

**通过标准**：
- 受伤有反馈，强度与伤害大致成比例；
- 读档/传送**不会**突然来一发；
- 暂停、退出游戏、Ctrl+C 后输出立刻归零；
- 3 秒没有新伤害会自动安静。

---

## 需要反馈给我的信息（做完一起发我）

1. App 里的通道上限是多少？
2. 血量数值的范围（0–1 / 0–100 / 其他）？
3. 静止时读数是否稳定？
4. CE 指针链有几级、偏移长什么样？
5. 你主观能承受的强度上限（1–200 档位）？
6. 受伤反馈"太钝/太冲"，以及希望它是刺痛还是闷振？

有了 2、4，我就能把 profile 固化下来；有了 5、6，我就能把曲线调到你舒服的位置。
