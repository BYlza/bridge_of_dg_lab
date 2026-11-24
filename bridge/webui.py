"""本地设置页面：双通道强度/曲线调节、实时状态、测试与急停。

    http://127.0.0.1:8765

设计要点：
  - 滑块改动立刻 POST（persist=false）即时生效，不会因为轮询被覆盖；
  - 点「保存」才写回 config/app.json；
  - 顶部 HP 条、双通道输出条、回环延迟、最近受伤记录一目了然。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .dglab import WAVEFORMS

log = logging.getLogger("webui")

PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bridge of DG-LAB</title>
<style>
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f4f5f7;color:#16181d}
.wrap{max-width:900px;margin:0 auto;padding:18px 16px 40px}
header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:12px;flex-wrap:wrap}
h1{font-size:19px;margin:0}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{font-size:12px;padding:3px 10px;border-radius:999px;background:#e8eaee;color:#4b5563}
.chip.on{background:#dcfce7;color:#166534}
.chip.warn{background:#fee2e2;color:#991b1b}
.card{background:#fff;border:1px solid #e6e8ec;border-radius:14px;padding:16px;margin-bottom:12px}
.card h2{font-size:13px;margin:0 0 12px;color:#6b7280;font-weight:600;letter-spacing:.02em}
.hprow{display:flex;justify-content:space-between;font-size:13px;color:#374151;margin-bottom:6px}
.hpbar{height:14px;border-radius:8px;background:#eceef2;overflow:hidden}
.hpbar i{display:block;height:100%;background:linear-gradient(90deg,#ef4444,#f59e0b);width:0;transition:width .12s}
.hpbar.low i{background:#dc2626}
.ch{display:grid;grid-template-columns:64px 1fr 96px;gap:12px;align-items:center;margin:14px 0}
.ch .name{font-weight:600}
.out{height:12px;border-radius:7px;background:#eceef2;overflow:hidden}
.out i{display:block;height:100%;width:0;transition:width .1s}
.out.a i{background:#2563eb}
.out.b i{background:#7c3aed}
.nc{font-variant-numeric:tabular-nums;font-size:13px;color:#374151;text-align:right}
.row{display:grid;grid-template-columns:132px 1fr 66px;gap:12px;align-items:center;margin:10px 0}
.row label{color:#374151}
.row .val{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
input[type=range]{width:100%;accent-color:#2563eb}
select{padding:7px 10px;border-radius:9px;border:1px solid #d6d9de;background:#fff;width:100%}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
button{border:0;border-radius:10px;padding:9px 14px;font-size:13px;cursor:pointer;background:#e8eaee;color:#111827}
button.primary{background:#2563eb;color:#fff}
button.danger{background:#dc2626;color:#fff}
.hint{color:#6b7280;font-size:12px;margin-top:10px}
canvas{width:100%;height:70px;display:block;margin-top:10px}
.log{font-size:12px;color:#4b5563;max-height:96px;overflow:auto;font-variant-numeric:tabular-nums}
.log div{padding:2px 0;border-bottom:1px dashed #f0f1f4}
</style></head><body><div class="wrap">
<header>
  <h1>Bridge of DG-LAB <span style="font-size:12px;color:#9ca3af;font-weight:400">死亡搁浅 DC</span></h1>
  <div class="chips">
    <span class="chip" id="c_conn">–</span>
    <span class="chip" id="c_arm">–</span>
    <span class="chip" id="c_lat">–</span>
  </div>
</header>

<div class="card">
  <h2>血量</h2>
  <div class="hprow"><span id="hp_txt">–</span><span id="hp_pct"></span></div>
  <div class="hpbar" id="hpbar"><i></i></div>
  <canvas id="spark" width="860" height="70"></canvas>
  <div class="log" id="log"></div>
</div>

<div class="card" id="relocate_card" style="display:none">
  <h2>血量地址失效</h2>
  <div class="hint" style="margin-top:0">退出游戏 / 读档后地址会变，这是正常的。填入游戏里当前的血量数字，后台重新定位（约 35 秒），完成后自动重新连接。</div>
  <div style="display:flex;gap:8px;margin-top:10px">
    <input id="hp_input" type="number" min="1" max="1000" placeholder="例如 823"
      style="flex:1;padding:9px 12px;border:1px solid #d6d9de;border-radius:9px;font-size:14px">
    <button class="primary" id="btn_relocate">重新定位</button>
  </div>
  <div class="hint" id="relocate_msg"></div>
</div>

<div class="card">
  <h2>输出</h2>
  <div class="ch"><div class="name">A 通道</div><div class="out a"><i id="barA"></i></div><div class="nc" id="ncA">0 / 0</div></div>
  <div class="ch"><div class="name">B 通道</div><div class="out b"><i id="barB"></i></div><div class="nc" id="ncB">0 / 0</div></div>
  <div class="btns">
    <button id="t_a">测试 A（2 秒）</button>
    <button id="t_b">测试 B（2 秒）</button>
    <button class="danger" id="stop">急停归零</button>
  </div>
  <div class="hint">测试期间游戏联动暂停，结束后自动归零。</div>
</div>

<div class="card">
  <h2>通道设置</h2>
  <div class="row"><label>启用通道</label>
    <select id="channel_mode"><option value="A">仅 A</option><option value="B">仅 B</option><option value="dual">A + B</option></select>
    <span class="val"></span></div>
  <div class="row"><label>A 通道上限</label><input id="max_intensity_a" type="range" min="1" max="100" step="1"><span class="val" id="v_a"></span></div>
  <div class="row"><label>B 通道上限</label><input id="max_intensity_b" type="range" min="1" max="100" step="1"><span class="val" id="v_b"></span></div>
</div>

<div class="card">
  <h2>曲线</h2>
  <div class="row"><label>小伤害保底</label><input id="floor_ratio" type="range" min="0" max="0.8" step="0.05"><span class="val" id="v_floor"></span></div>
  <div class="row"><label>曲线 gamma</label><input id="gamma" type="range" min="0.2" max="1" step="0.05"><span class="val" id="v_gamma"></span></div>
  <div class="row"><label>上升速率</label><input id="rise_rate" type="range" min="2" max="60" step="1"><span class="val" id="v_rise"></span></div>
  <div class="row"><label>衰减时间 τ</label><input id="decay_tau" type="range" min="0.3" max="3" step="0.1"><span class="val" id="v_decay"></span></div>
  <div class="row"><label>死区（血量）</label><input id="deadzone" type="range" min="0" max="0.05" step="0.001"><span class="val" id="v_dz"></span></div>
  <div class="btns">
    <button class="primary" id="save">保存</button>
    <button id="reload">放弃修改</button>
  </div>
  <div class="hint">拖动即时生效（不写文件）；「保存」才写入 config/app.json。</div>
</div>

<div class="card">
  <h2>输出模拟（这不只是示意图，是用运行中的同一套映射代码算出来的）</h2>
  <div class="row"><label>模拟掉血</label><input id="sim_damage" type="range" min="5" max="1000" step="5" value="150"><span class="val" id="v_dmg">150</span></div>
  <div class="btns" style="margin-top:0">
    <button data-d="30">轻伤 30</button>
    <button data-d="150">摔一跤 150</button>
    <button data-d="400">重摔 400</button>
    <button data-d="1000">致命 1000</button>
  </div>
  <canvas id="sim" width="860" height="220"></canvas>
  <div class="hint" id="sim_txt">–</div>
</div>
</div>
<script>
const NUM=["max_intensity_a","max_intensity_b","floor_ratio","gamma","rise_rate","decay_tau","deadzone"];
const SUF={max_intensity_a:"a",max_intensity_b:"b",floor_ratio:"floor",gamma:"gamma",rise_rate:"rise",decay_tau:"decay",deadzone:"dz"};
let cfg={}, busy=false, hist=[];
const $=id=>document.getElementById(id);
function fmt(k,v){
  if(k==="floor_ratio"||k==="gamma"||k==="deadzone")return (+v).toFixed(2);
  if(k==="decay_tau")return (+v).toFixed(1);
  return Math.round(v);
}
function bind(){
  NUM.forEach(k=>{
    const el=$(k);
    el.addEventListener("input",()=>{cfg[k]=parseFloat(el.value);$("v_"+SUF[k]).textContent=fmt(k,cfg[k]);push();});
  });
  $("channel_mode").addEventListener("change",e=>{cfg.channel_mode=e.target.value;push();});
}
let timer=null;
function push(){
  if(!cfg)return;
  busy=true;
  clearTimeout(timer);
  timer=setTimeout(()=>{
    const body=Object.assign({},cfg,{persist:false});
    fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})
      .finally(()=>{busy=false;refreshSim();});
  },120);
}
function paintCfg(){
  NUM.forEach(k=>{ $(k).value=cfg[k]; $("v_"+SUF[k]).textContent=fmt(k,cfg[k]); });
  $("channel_mode").value=cfg.channel_mode;
}
function dragging(){ return document.activeElement && document.activeElement.type==="range"; }
async function poll(){
  let s;
  try{ s=await (await fetch("/api/state")).json(); }catch(e){ return; }
  const hp=s.hp==null?0:s.hp, mx=(s.hp_max||1000), ratio=Math.max(0,Math.min(1,hp/mx));
  $("hp_txt").textContent = s.hp==null ? "未读取" : hp.toFixed(0)+" / "+mx.toFixed(0);
  $("hp_pct").textContent = s.hp==null ? "" : (ratio*100).toFixed(1)+"%";
  $("hpbar").firstElementChild.style.width=(ratio*100)+"%";
  $("hpbar").classList.toggle("low",ratio<0.25);

  $("barA").style.width=Math.min(100,s.out_a/Math.max(1,s.cap_a)*100)+"%";
  $("barB").style.width=Math.min(100,s.out_b/Math.max(1,s.cap_b)*100)+"%";
  $("ncA").textContent=s.out_a.toFixed(1)+" / "+Math.round(s.cap_a);
  $("ncB").textContent=s.out_b.toFixed(1)+" / "+Math.round(s.cap_b)+(s.limit_b<Math.round(s.cap_b)?" (App "+s.limit_b+")":"");

  $("c_conn").textContent=s.connected?"已连接":"等待扫码";
  $("c_conn").className="chip "+(s.connected?"on":"");
  $("c_arm").textContent=s.armed?"ARMED":"预览";
  $("c_arm").className="chip "+(s.armed?"on":"warn");
  $("c_lat").textContent=s.latency_ms==null?"延迟 –":("延迟 "+s.latency_ms.toFixed(0)+" ms");
  $("c_lat").className="chip "+((s.latency_ms!=null&&s.latency_ms<150)?"on":"");

  if(!busy && !dragging()){ cfg={...s.cfg}; paintCfg(); }
  if(s.last_damage){
    const d=s.last_damage;
    if(!window._lastDmg||window._lastDmg.t!==d.t){
      window._lastDmg=d;
      const el=document.createElement("div");
      el.textContent=new Date().toLocaleTimeString()+"  受伤 -"+d.delta.toFixed(0)+"  →  "+d.pct.toFixed(1)+"%  (峰值 "+d.peak.toFixed(0)+"%)";
      $("log").prepend(el);
      while($("log").childElementCount>8)$("log").lastChild.remove();
    }
  }
  if(s.hp_stale){ $("relocate_card").style.display="block"; }
  else { $("relocate_card").style.display="none"; }
  if(s.relocating){ $("relocate_msg").textContent="正在扫描内存…约 35 秒，别关窗口。"; }
  else if(s.relocate_failed){ $("relocate_msg").textContent="没找到匹配：确认填的是血条上的当前数字，然后重试。"; }

  hist.push(s.level); if(hist.length>120)hist.shift();
  drawSpark();
}
function drawSpark(){
  const c=$("spark"),ctx=c.getContext("2d"),w=c.width,h=c.height;
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle="#e5e7eb";ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(0,h-1);ctx.lineTo(w,h-1);ctx.stroke();
  if(hist.length<2)return;
  ctx.strokeStyle="#7c3aed";ctx.lineWidth=2;ctx.beginPath();
  hist.forEach((v,i)=>{const x=i/119*w,y=h-Math.min(1,v)*h*0.92-2; i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
  ctx.stroke();
}
let simData=null, simTimer=null;
async function refreshSim(){
  const d=parseFloat($("sim_damage").value)||150;
  $("v_dmg").textContent=Math.round(d);
  try{ simData=await (await fetch("/api/sim?damage="+d)).json(); }catch(e){ return; }
  drawSim();
}
function drawSim(){
  if(!simData)return;
  const c=$("sim"),ctx=c.getContext("2d"),W=c.width,H=c.height;
  const padL=42,padR=14,padT=12,padB=24;
  ctx.clearRect(0,0,W,H);
  const capMax=Math.max(simData.cap_a||1,simData.cap_b||1);
  const xmax=simData.t[simData.t.length-1]||4;
  const X=t=>padL+(t/xmax)*(W-padL-padR);
  const Y=v=>H-padB-(v/capMax)*(H-padT-padB);
  ctx.font="11px -apple-system,'Segoe UI',sans-serif";
  for(let i=0;i<=4;i++){
    const v=capMax*i/4, y=Y(v);
    ctx.strokeStyle="#eef0f3";ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(W-padR,y);ctx.stroke();
    ctx.fillStyle="#9ca3af";ctx.fillText(Math.round(v),8,y+4);
  }
  for(let s=0;s<=xmax;s+=1){
    const x=X(s);
    ctx.strokeStyle="#f6f7f9";ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(x,padT);ctx.lineTo(x,H-padB);ctx.stroke();
    ctx.fillStyle="#9ca3af";ctx.fillText(s+"s",x-5,H-8);
  }
  const draw=(arr,color)=>{
    ctx.strokeStyle=color;ctx.lineWidth=2.4;ctx.lineJoin="round";ctx.beginPath();
    simData.t.forEach((t,i)=>{const x=X(t),y=Y(arr[i]); i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
    ctx.stroke();
  };
  draw(simData.a,"#2563eb");
  draw(simData.b,"#7c3aed");
  const sa=simData.stats_a, sb=simData.stats_b;
  const appCapB=Math.min(simData.cap_b,simData.limit_b||simData.cap_b);
  let extra = appCapB < simData.cap_b ? `（App 上限 ${appCapB}，实际最高只到 ${appCapB}）` : "";
  $("sim_txt").innerHTML =
    `<span style="color:#2563eb;font-weight:600">A 峰值 ${sa.peak}</span> 达峰 ${sa.rise_ms}ms / 衰减到 10% ${sa.fall_ms}ms　`+
    `<span style="color:#7c3aed;font-weight:600">B 峰值 ${sb.peak}</span> 达峰 ${sb.rise_ms}ms / 衰减到 10% ${sb.fall_ms}ms ${extra}`;
}
$("sim_damage").addEventListener("input",()=>{
  $("v_dmg").textContent=Math.round(parseFloat($("sim_damage").value));
  clearTimeout(simTimer); simTimer=setTimeout(refreshSim,120);
});
document.querySelectorAll("[data-d]").forEach(b=>{
  b.onclick=()=>{ $("sim_damage").value=b.dataset.d; refreshSim(); };
});
$("save").onclick=async()=>{
  const body=Object.assign({},cfg,{persist:true});
  await fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  $("c_arm").textContent="已保存";setTimeout(poll,800);
};
$("reload").onclick=()=>poll();
$("t_a").onclick=()=>fetch("/api/test",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({channel:"A",seconds:2})});
$("t_b").onclick=()=>fetch("/api/test",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({channel:"B",seconds:2})});
$("stop").onclick=()=>fetch("/api/stop",{method:"POST"});
$("btn_relocate").onclick=async()=>{
  const hp=parseInt($("hp_input").value,10);
  if(!hp||hp<1||hp>1000){ $("relocate_msg").textContent="请填 1–1000 的血量数字。"; return; }
  $("relocate_msg").textContent="开始重定位…";
  await fetch("/api/relocate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({hp:hp})});
};
bind();poll();setInterval(poll,300);refreshSim();
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    panel: "ControlPanel" = None  # type: ignore

    def log_message(self, fmt, *args):  # noqa: A003
        return

    def _send(self, code: int, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/state"):
            self._send(200, json.dumps(self.panel.snapshot()).encode())
        elif self.path.startswith("/api/sim"):
            from urllib.parse import parse_qs, urlparse

            q = parse_qs(urlparse(self.path).query)
            try:
                damage = float(q.get("damage", ["150"])[0])
            except ValueError:
                damage = 150.0
            self._send(200, json.dumps(self.panel.simulate(damage)).encode())
        else:
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}

        if self.path.startswith("/api/config"):
            ok = self.panel.apply(data, persist=bool(data.get("persist", False)))
            self._send(200, json.dumps({"ok": ok}).encode())
        elif self.path.startswith("/api/relocate"):
            self.panel.relocate(int(data.get("hp", 0)))
            self._send(200, b'{"ok":true}')
        elif self.path.startswith("/api/test"):
            self.panel.test(str(data.get("channel", "B")), int(data.get("seconds", 2)))
            self._send(200, b'{"ok":true}')
        elif self.path.startswith("/api/stop"):
            self.panel.stop_output()
            self._send(200, b'{"ok":true}')
        else:
            self._send(404, b"{}")


class ControlPanel:
    def __init__(self, cfg, mapper, guard, link, state: dict, host="127.0.0.1", port=8765):
        self.cfg = cfg
        self.mapper = mapper
        self.guard = guard
        self.link = link
        self.state = state
        self.host = host
        self.port = port
        self.testing = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> bool:
        handler = type("Handler", (_Handler,), {"panel": self})
        try:
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as exc:
            log.error("设置页面启动失败（端口 %s）：%s", self.port, exc)
            return False
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="webui", daemon=True
        )
        self._thread.start()
        log.info("设置页面：http://%s:%s", self.host, self.port)
        return True

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    # ------------------------------------------------------------------ 数据

    def snapshot(self) -> dict:
        cfg = self.cfg
        return {
            "hp": self.state.get("hp"),
            "hp_max": self.state.get("hp_max", 1000),
            "level": round(self.mapper.level, 4),
            "out_a": round(self.mapper.intensity("A"), 2),
            "out_b": round(self.mapper.intensity("B"), 2),
            "cap_a": cfg.max_intensity_a,
            "cap_b": cfg.max_intensity_b,
            "connected": self.link.bound,
            "armed": self.state.get("armed", False),
            "limit_a": self.link.limit_a,
            "limit_b": self.link.limit_b,
            "latency_ms": self.state.get("latency_ms"),
            "last_damage": self.state.get("last_damage"),
            "relocating": bool(self.state.get("relocating")),
            "relocate_failed": bool(self.state.get("relocate_failed")),
            "hp_stale": self.state.get("hp") is None,
            "testing": self.testing.is_set(),
            "cfg": {
                "max_intensity_a": cfg.max_intensity_a,
                "max_intensity_b": cfg.max_intensity_b,
                "floor_ratio": cfg.floor_ratio,
                "gamma": cfg.gamma,
                "rise_rate": cfg.rise_rate,
                "decay_tau": cfg.decay_tau,
                "deadzone": cfg.deadzone,
                "channel_mode": cfg.channel_mode,
            },
        }

    def apply(self, data: dict, persist: bool = False) -> bool:
        cfg = self.cfg
        try:
            for key in (
                "max_intensity_a",
                "max_intensity_b",
                "floor_ratio",
                "gamma",
                "rise_rate",
                "decay_tau",
                "deadzone",
            ):
                if key in data:
                    setattr(cfg, key, float(data[key]))
            if "channel_mode" in data:
                cfg.channel_mode = str(data["channel_mode"]).upper()
        except (TypeError, ValueError):
            return False

        # 同步到运行中的对象，立刻生效
        m = self.mapper.cfg
        m.cap_a = cfg.max_intensity_a
        m.cap_b = cfg.max_intensity_b
        m.floor_ratio = cfg.floor_ratio
        m.gamma = cfg.gamma
        m.decay_tau = cfg.decay_tau
        m.rise_rate = cfg.rise_rate
        m.deadzone = cfg.deadzone
        m.channel_mode = cfg.channel_mode
        self.guard.hard_caps["A"] = max(0, int(cfg.max_intensity_a))
        self.guard.hard_caps["B"] = max(0, int(cfg.max_intensity_b))

        if persist:
            self._persist()
        return True

    def _persist(self) -> None:
        from .config import DEFAULT_APP_CONFIG

        try:
            with open(DEFAULT_APP_CONFIG, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            cfg = self.cfg
            raw.update(
                {
                    "max_intensity_a": cfg.max_intensity_a,
                    "max_intensity_b": cfg.max_intensity_b,
                    "floor_ratio": cfg.floor_ratio,
                    "gamma": cfg.gamma,
                    "decay_tau": cfg.decay_tau,
                    "rise_rate": cfg.rise_rate,
                    "deadzone": cfg.deadzone,
                    "channel_mode": cfg.channel_mode,
                }
            )
            with open(DEFAULT_APP_CONFIG, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, ensure_ascii=False, indent=2)
            log.info("配置已写回 config/app.json")
        except Exception as exc:  # noqa: BLE001
            log.warning("写回配置失败：%s", exc)

    # ------------------------------------------------------------------ 模拟

    def simulate(self, damage: float, max_hp: float = 1000.0, seconds: float = 4.0) -> dict:
        """用和实际运行完全相同的映射代码，跑一次伤害事件，返回曲线。"""
        from .mapper import IntensityMapper, MapperConfig

        cfg = self.cfg
        m = IntensityMapper(
            MapperConfig(
                channel_mode=cfg.channel_mode,
                cap_a=cfg.max_intensity_a,
                cap_b=cfg.max_intensity_b,
                min_intensity=cfg.min_intensity,
                floor_ratio=cfg.floor_ratio,
                gamma=cfg.gamma,
                rise_rate=cfg.rise_rate,
                decay_tau=cfg.decay_tau,
                deadzone=cfg.deadzone,
            )
        )
        m.on_damage(damage, max_hp)

        dt = 0.005
        ts, la, lb = [], [], []
        t = 0.0
        while t <= seconds + 1e-9:
            m.tick(dt)
            ts.append(round(t, 3))
            la.append(round(m.intensity("A"), 2))
            lb.append(round(m.intensity("B"), 2))
            t += dt

        # 抽稀到 240 点，减少传输
        step = max(1, len(ts) // 240)
        ts, la, lb = ts[::step], la[::step], lb[::step]

        def stats(series):
            peak = max(series) if series else 0
            if peak <= 0:
                return {"peak": 0, "rise_ms": 0, "fall_ms": 0}
            ip = series.index(peak)
            rise = ts[ip] * 1000
            fall = 0
            for i in range(ip, len(series)):
                if series[i] <= peak * 0.1:
                    fall = (ts[i] - ts[ip]) * 1000
                    break
            return {
                "peak": round(peak, 2),
                "rise_ms": int(rise),
                "fall_ms": int(fall),
                "peak_pct": round(peak / max(1, max(cfg.max_intensity_a, cfg.max_intensity_b)) * 100, 1),
            }

        return {
            "damage": damage,
            "t": ts,
            "a": la,
            "b": lb,
            "stats_a": stats(la),
            "stats_b": stats(lb),
            "cap_a": cfg.max_intensity_a,
            "cap_b": cfg.max_intensity_b,
            "limit_a": self.link.limit_a,
            "limit_b": self.link.limit_b,
        }

    # ------------------------------------------------------------------ 血量重定位

    def relocate(self, hp: int) -> None:
        """后台跑 relocate.py，成功后让主循环重新附加。"""
        if hp <= 0 or self.state.get("relocating"):
            return
        self.state["relocating"] = True
        self.state["relocate_failed"] = False

        def run():
            import subprocess

            from .config import DEFAULT_PROFILE, ROOT

            def current_addr():
                try:
                    with open(DEFAULT_PROFILE, "r", encoding="utf-8") as fh:
                        return (json.load(fh).get("health") or {}).get("address")
                except (OSError, ValueError):
                    return None

            before = current_addr()
            script = os.path.join(ROOT, "tools", "relocate.py")
            try:
                subprocess.run(
                    [sys.executable, script, "--hp", str(hp), "--write"], check=False
                )
                after = current_addr()
                if after and after != before:
                    self.state["need_reattach"] = True
                else:
                    self.state["relocate_failed"] = True
            finally:
                self.state["relocating"] = False

        threading.Thread(target=run, daemon=True, name="relocate").start()

    # ------------------------------------------------------------------ 测试 / 急停

    def test(self, channel: str = "B", seconds: int = 2) -> None:
        if self.testing.is_set():
            return
        self.testing.set()

        def run():
            ch = channel.upper() if channel.upper() in ("A", "B") else "B"
            cap = self.cfg.max_intensity_a if ch == "A" else self.cfg.max_intensity_b
            limit = self.link.limit_a if ch == "A" else self.link.limit_b
            value = int(min(cap, limit))
            frames = WAVEFORMS.get(self.cfg.waveform, WAVEFORMS["hit"])
            try:
                end = time.time() + seconds
                while time.time() < end:
                    self.link.set_strength(ch, value)
                    self.link.send_pulse(ch, frames)
                    time.sleep(0.75)
            finally:
                self.link.set_strength(ch, 0)
                self.link.clear(ch)
                self.testing.clear()

        threading.Thread(target=run, daemon=True).start()

    def stop_output(self) -> None:
        self.testing.clear()
        self.link.zero()
        self.mapper.reset()
