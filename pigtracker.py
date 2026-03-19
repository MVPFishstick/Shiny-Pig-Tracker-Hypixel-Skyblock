#!/usr/bin/env python3
"""Shiny Pig Profit Tracker — http://localhost:7878"""
import os, re, time, json, threading, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
CONFIG_FILE  = SCRIPT_DIR / "log_config.json"
DEFAULT_PATH = Path(os.environ.get("MC_LOG",
    Path.home() / "AppData" / "Roaming" / ".minecraft" / "logs" / "latest.log"))

def load_log_path():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            p = Path(data.get("log_path", ""))
            if str(p) != ".":
                return p
        except: pass
    return DEFAULT_PATH

def save_log_path(path):
    CONFIG_FILE.write_text(json.dumps({"log_path": str(path)}, indent=2))

LOG_PATH = load_log_path()
PORT = 7878

state = {
    "events": [], "bazaar": {}, "display_lookup": {}, "log_path": str(LOG_PATH),
    "bazaar_ts": 0, "running": True,
}
state_lock = threading.Lock()

# Fixed prices for items not on bazaar (coins per unit)
FIXED_PRICES = {
    "farming for dummies": 900_000,
    "harvesting vi":        2_800_000,
    "potato talisman":     29_000_000,
    "blood god crest":      1_000_000,
}

# Manual aliases for items whose display name differs from their bazaar product ID
ALIASES = {
    "enchanted raw porkchop":     "ENCHANTED_PORK",
    "enchanted porkchop":         "ENCHANTED_PORK",
    "experience bottle":          "EXP_BOTTLE",
    "large experience bottle":    "LARGE_EXP_BOTTLE",
    "medium experience bottle":   "MEDIUM_EXP_BOTTLE",
    "grand experience bottle":    "GRAND_EXP_BOTTLE",
    "titanic experience bottle":  "TITANIC_EXP_BOTTLE",
    "colossal experience bottle": "COLOSSAL_EXP_BOTTLE",
}

# ── data fetching ─────────────────────────────────────────────────────────────

def fetch_items():
    """Build display_name.lower() -> product_id map from the items endpoint."""
    try:
        with urllib.request.urlopen(
                "https://api.hypixel.net/v2/resources/skyblock/items", timeout=15) as r:
            data = json.loads(r.read())
        dl = {}
        for item in data.get("items", []):
            iid  = item.get("id", "")
            name = item.get("name", "")
            if iid and name:
                dl[name.lower()] = iid
        with state_lock:
            state["display_lookup"] = dl
        print(f"[items] Loaded {len(dl)} display names")
    except Exception as e:
        print(f"[items] Error: {e}")

def fetch_bazaar():
    """
    Prices from the bazaar endpoint.
    sell_summary = people listing to SELL  -> what YOU pay when your buy order fills
    buy_summary  = people bidding to BUY   -> what YOU receive when your sell offer fills

    So:  buyOrderPrice  = sell_summary[0].pricePerUnit
         sellOfferPrice = buy_summary[0].pricePerUnit
    """
    try:
        with urllib.request.urlopen(
                "https://api.hypixel.net/v2/skyblock/bazaar", timeout=15) as r:
            data = json.loads(r.read())
        products = {}
        for pid, pdata in data.get("products", {}).items():
            sell_sum = pdata.get("sell_summary", [])
            buy_sum  = pdata.get("buy_summary",  [])
            products[pid] = {
                "buyOrderPrice":  sell_sum[0]["pricePerUnit"] if sell_sum else 0,
                "sellOfferPrice": buy_sum[0]["pricePerUnit"]  if buy_sum  else 0,
            }
        with state_lock:
            state["bazaar"] = products
            state["bazaar_ts"] = time.time()
        print(f"[bazaar] Loaded {len(products)} products")
    except Exception as e:
        print(f"[bazaar] Error: {e}")

def bazaar_refresher():
    # Fetch synchronously first so tail_log can wait on it
    fetch_items()
    fetch_bazaar()
    while state["running"]:
        time.sleep(120)
        fetch_items()
        fetch_bazaar()

# ── price lookup ──────────────────────────────────────────────────────────────

def get_bazaar_price(item_name):
    """
    Return (buyOrderPrice, sellOfferPrice) or (None, None).
    Lookup order:
      1. display_lookup  — items endpoint  (name -> exact product id)
      2. ALIASES         — manual overrides for known mismatches
      3. exact key       — item name uppercased as product id
      4. fuzzy           — shortest bazaar key that contains the name
    """
    needle = item_name.lower().strip()
    with state_lock:
        bz = dict(state["bazaar"])
        dl = dict(state["display_lookup"])

    def hit(pid):
        if pid in bz:
            v = bz[pid]
            return v["buyOrderPrice"], v["sellOfferPrice"]
        return None

    # 0. Fixed prices (items not on bazaar)
    if needle in FIXED_PRICES:
        p = FIXED_PRICES[needle]
        print(f"[price] fixed: {needle!r} -> {p:,}")
        return p, p

    # 1. Items endpoint display name (most reliable — covers Farming for Dummies etc)
    pid = dl.get(needle)
    if pid:
        r = hit(pid)
        if r:
            print(f"[price] display: {needle!r} -> {pid}")
            return r
        print(f"[price] display found pid={pid!r} but not in bazaar ({len(bz)} products)")

    # 2. Manual aliases
    pid = ALIASES.get(needle)
    if pid:
        r = hit(pid)
        if r:
            print(f"[price] alias: {needle!r} -> {pid}")
            return r
        print(f"[price] alias found pid={pid!r} but not in bazaar")

    # 3. Exact product ID
    pid = needle.upper().replace(" ", "_")
    r = hit(pid)
    if r:
        print(f"[price] exact: {pid}")
        return r

    # 4. Fuzzy: find shortest bazaar key whose readable name contains the needle
    candidates = []
    for k, v in bz.items():
        kn = k.replace("_", " ").lower()
        if needle == kn:
            print(f"[price] fuzzy-exact: {k}")
            return v["buyOrderPrice"], v["sellOfferPrice"]
        if needle in kn:
            candidates.append((len(kn), k, v))
    if candidates:
        candidates.sort()
        _, bk, v = candidates[0]
        print(f"[price] fuzzy: {needle!r} -> {bk}")
        return v["buyOrderPrice"], v["sellOfferPrice"]

    print(f"[price] NO MATCH: {item_name!r}")
    return None, None

# ── regex patterns ─────────────────────────────────────────────────────────────
RE_SHINY_COINS = re.compile(r"SHINY!.*?extracted.*?\+?([\d,]+(?:\.\d+)?)\s+Coins", re.IGNORECASE)
RE_SHINY_XP    = re.compile(r"SHINY!.*?extracted.*?\+?([\d,]+(?:,\d+)?)\s+(Enchanting XP|Farming XP|Mining XP|Combat XP|Fishing XP|\w+ XP)", re.IGNORECASE)
RE_SHINY_ITEM  = re.compile(r"SHINY!.*?extracted.*?\b(\d+)x\s+(.+?)\s+from", re.IGNORECASE)
RE_SHINY_BOOK  = re.compile(r"SHINY!.*?extracted.*?Shiny Shard and ([A-Za-z ]+(?:VI|VII|VIII|IX|IV|V?I{0,3}|X{1,2}))\s+from", re.IGNORECASE)
RE_BUY         = re.compile(r"You bought (.+?)\s+x([\d,]+)\s+for\s+([\d,]+(?:\.\d+)?)\s+Coins?", re.IGNORECASE)
RE_BUY2        = re.compile(r"[Bb]ought\s+([\d,]+)x\s+(.+?)\s+for\s+([\d,]+(?:\.\d+)?)\s+[Cc]oins?")
RE_BUY_ORDER   = re.compile(r"Buy Order filled[!.]?\s+([\d,]+)x\s+(.+?)\s+for\s+([\d,]+(?:\.\d+)?)\s+[Cc]oins?", re.IGNORECASE)
RE_SELL_ORDER  = re.compile(r"Sell Offer filled[!.]?\s+([\d,]+)x\s+(.+?)\s+for\s+([\d,]+(?:\.\d+)?)\s+[Cc]oins?", re.IGNORECASE)

# ── helpers ────────────────────────────────────────────────────────────────────
def parse_num(s):
    return float(str(s).replace(",", ""))

def strip_color(s):
    return re.sub(r"§[0-9a-fk-or]", "", s, flags=re.IGNORECASE)

def extract_chat(raw_line):
    line = strip_color(raw_line)
    m = re.search(r"\[CHAT\]\s*(.*)", line)
    if m:
        return m.group(1).strip()
    m = re.search(r"\]\s*:\s+(.*)", line)
    if m:
        msg = m.group(1).strip()
        keywords = ["SHINY", "You bought", "You extracted", "Buy Order",
                    "Sell Offer", "Bazaar", "piglet", "Bought", "Sold"]
        if any(kw.lower() in msg.lower() for kw in keywords):
            return msg
    return None

def is_player_message(line):
    if re.search(r"^\[(?:G|P|F|Co-op)\]\s+\w+:", line):
        return True
    if re.match(r"^\w{2,16}(?:\s\w+)?:", line) and "SHINY" not in line and "You " not in line:
        return True
    return False

# ── parser ─────────────────────────────────────────────────────────────────────
def parse_line(raw_line):
    line = extract_chat(raw_line)
    if not line or is_player_message(line):
        return None
    if "orb is charged" in line.lower():
        return None

    events = []

    m = RE_SHINY_COINS.search(line)
    if m:
        events.append({"type": "shiny_coins", "coins": parse_num(m.group(1)), "raw": line})

    if not events:
        m = RE_SHINY_ITEM.search(line)
        if m:
            qty = int(m.group(1))
            item = m.group(2).strip()
            _, sell_p = get_bazaar_price(item)
            value = sell_p * qty if sell_p else None
            events.append({"type": "shiny_item", "item": item, "qty": qty,
                           "sell_price": sell_p, "value": value, "raw": line})

    if not events and "SHINY" in line:
        m = RE_SHINY_BOOK.search(line)
        if m:
            enchant = m.group(1).strip()
            needle  = enchant.lower()
            if needle in FIXED_PRICES:
                p     = FIXED_PRICES[needle]
                value = p  # qty is always 1 for these drops
                print(f"[price] fixed enchant: {enchant!r} -> {p:,}")
                events.append({"type": "shiny_item", "item": enchant, "qty": 1,
                               "sell_price": p, "value": value, "raw": line})
            else:
                events.append({"type": "shiny_enchant", "enchant": enchant, "raw": line})
        elif "XP" in line:
            m2 = RE_SHINY_XP.search(line)
            if m2:
                events.append({"type": "shiny_xp", "amount": parse_num(m2.group(1)),
                               "xp_type": m2.group(2), "raw": line})
        else:
            events.append({"type": "shiny_other", "raw": line})

    m = RE_BUY.search(line)
    if m:
        events.append({"type": "buy", "item": m.group(1).strip(),
                       "qty": int(parse_num(m.group(2))), "coins": parse_num(m.group(3)), "raw": line})

    if not any(e["type"] == "buy" for e in events):
        m = RE_BUY2.search(line)
        if m:
            events.append({"type": "buy", "item": m.group(2).strip(),
                           "qty": int(parse_num(m.group(1))), "coins": parse_num(m.group(3)), "raw": line})

    m = RE_BUY_ORDER.search(line)
    if m:
        events.append({"type": "buy", "item": m.group(2).strip(),
                       "qty": int(parse_num(m.group(1))), "coins": parse_num(m.group(3)), "raw": line})

    m = RE_SELL_ORDER.search(line)
    if m:
        events.append({"type": "sell", "item": m.group(2).strip(),
                       "qty": int(parse_num(m.group(1))), "coins": parse_num(m.group(3)), "raw": line})

    return events if events else None

# ── log tailer ─────────────────────────────────────────────────────────────────
def tail_log():
    print("[log] Waiting for bazaar data...")
    while state["running"]:
        with state_lock:
            ready = len(state["bazaar"]) > 0 and len(state["display_lookup"]) > 0
        if ready:
            break
        time.sleep(0.5)
    print("[log] Ready — starting log tail")
    last_inode  = None
    last_pos    = 0
    last_watched = None
    while state["running"]:
        try:
            with state_lock:
                log_path = Path(state["log_path"])
            if log_path != last_watched:
                print(f"[log] Watching: {log_path}")
                last_watched = log_path
                last_inode   = None
                last_pos     = 0
            if not log_path.exists():
                time.sleep(2)
                continue
            inode = os.stat(log_path).st_ino
            if inode != last_inode:
                last_inode = inode
                last_pos   = 0
                print("[log] New log file detected")
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last_pos)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    last_pos = f.tell()
                    result = parse_line(line)
                    if result:
                        ts = time.strftime("%H:%M:%S")
                        with state_lock:
                            for ev in result:
                                ev["ts"] = ts
                                state["events"].append(ev)
                                print(f"[event] {ev['type']} | {ev.get('item', ev.get('coins', ''))}")
        except Exception as e:
            print(f"[log] Error: {e}")
        time.sleep(0.5)


HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Shiny Pig Tracker</title>
<style>
:root{--bg:#0e0e0f;--bg2:#161618;--bg3:#1e1e21;--border:rgba(255,255,255,0.08);
  --amber:#f5a623;--green:#4ade80;--red:#f87171;--blue:#60a5fa;--pink:#f472b6;
  --text:#e8e8ea;--muted:#888;--font:'Cascadia Code','Fira Code','Consolas',monospace;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px;}
.app{max-width:900px;margin:0 auto;padding:24px 16px;}
header{display:flex;align-items:center;gap:14px;margin-bottom:24px;}
.pig-icon{font-size:32px;}
h1{font-size:20px;font-weight:600;letter-spacing:-0.02em;color:var(--amber);}
h1 span{color:var(--text);font-weight:400;font-size:14px;margin-left:8px;opacity:0.6;}
.status{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);
  background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:8px 14px;margin-bottom:20px;}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);flex-shrink:0;}
.dot.warn{background:var(--amber);animation:blink 1s infinite;}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:20px;}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px 16px;}
.card-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;}
.card-val{font-size:20px;font-weight:600;}
.amber{color:var(--amber);}.green{color:var(--green);}.red{color:var(--red);}.blue{color:var(--blue);}
.section-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;}
.feed{display:flex;flex-direction:column;gap:5px;max-height:420px;overflow-y:auto;}
.feed::-webkit-scrollbar{width:4px;}.feed::-webkit-scrollbar-thumb{background:var(--bg3);border-radius:4px;}
.row{display:grid;grid-template-columns:60px 90px 1fr auto;gap:10px;align-items:center;
  background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 12px;
  animation:fadein .3s ease;}
@keyframes fadein{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
.row-ts{color:var(--muted);font-size:11px;}
.badge{font-size:10px;padding:2px 7px;border-radius:4px;font-weight:600;letter-spacing:.04em;white-space:nowrap;}
.badge.coins{background:#2a1f00;color:var(--amber);}.badge.buy{background:#001a3a;color:var(--blue);}
.badge.sell{background:#0a2a14;color:var(--green);}.badge.item{background:#2a002a;color:var(--pink);}
.badge.other{background:#1a1a1a;color:var(--muted);}
.row-desc{color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.row-val{font-weight:600;text-align:right;white-space:nowrap;}
.row-val.pos{color:var(--green);}.row-val.neg{color:var(--red);}.row-val.neutral{color:var(--muted);}
.empty{text-align:center;padding:32px;color:var(--muted);}
.toolbar{display:flex;gap:8px;margin-bottom:12px;}
.toolbar input{flex:1;background:var(--bg2);border:1px solid var(--border);border-radius:6px;
  color:var(--text);padding:7px 12px;font-family:var(--font);font-size:12px;}
.toolbar input:focus{outline:none;border-color:var(--amber);}
.toolbar button{background:transparent;border:1px solid var(--border);border-radius:6px;
  color:var(--muted);padding:7px 14px;cursor:pointer;font-family:var(--font);font-size:12px;}
.toolbar button:hover{background:var(--bg3);color:var(--text);}

.settings-bar{display:flex;gap:8px;align-items:center;margin-bottom:12px;padding:10px 14px;
  background:var(--bg2);border:1px solid var(--border);border-radius:8px;}
.settings-bar label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;}
.settings-bar input{flex:1;background:var(--bg3);border:1px solid var(--border);border-radius:6px;
  color:var(--text);padding:6px 10px;font-family:var(--font);font-size:12px;}
.settings-bar input:focus{outline:none;border-color:var(--amber);}
.settings-bar button{background:transparent;border:1px solid var(--border);border-radius:6px;
  color:var(--muted);padding:6px 14px;cursor:pointer;font-family:var(--font);font-size:12px;white-space:nowrap;}
.settings-bar button:hover{background:var(--bg3);color:var(--text);}
.settings-msg{font-size:11px;white-space:nowrap;}
.settings-msg.ok{color:var(--green);}.settings-msg.err{color:var(--red);}
</style></head><body>
<div class="app">
  <header><div class="pig-icon">🐷</div><div><h1>Shiny Pig Tracker <span>live</span></h1></div></header>
  <div class="status">
    <div class="dot" id="dot"></div>
    <span id="statusMsg">Connecting...</span>
    <span style="margin-left:auto;font-size:11px" id="bzStatus"></span>
  </div>
  <div class="stats">
    <div class="card"><div class="card-label">Orbs Bought</div><div class="card-val blue" id="sOrbCost">—</div></div>
    <div class="card"><div class="card-label">Coin Drops</div><div class="card-val amber" id="sCoinDrops">—</div></div>
    <div class="card"><div class="card-label">Item Value</div><div class="card-val" id="sItemVal" style="color:var(--pink)">—</div></div>
    <div class="card"><div class="card-label">Net Profit</div><div class="card-val" id="sNet">—</div></div>
    <div class="card"><div class="card-label">Pigs Farmed</div><div class="card-val blue" id="sPigs">—</div></div>
    <div class="card"><div class="card-label">Profit / Pig</div><div class="card-val green" id="sProfitPig">—</div></div>
    <div class="card"><div class="card-label">Uptime</div><div class="card-val" id="sUptime" style="color:var(--muted);font-size:17px">0:00:00</div></div>
    <div class="card"><div class="card-label">Profit / Hour</div><div class="card-val green" id="sProfitH">—</div></div>
  </div>
  <div class="settings-bar">
    <label>log path</label>
    <input type="text" id="logPathInput" placeholder="C:\path\to\latest.log" />
    <button onclick="savePath()">Save</button>
    <span class="settings-msg" id="pathMsg"></span>
  </div>
  <div class="toolbar">
    <input type="text" id="filterInput" placeholder="Filter events..." />
    <button onclick="clearEvents()">Clear</button>
  </div>
  <div class="section-label">event log</div>
  <div class="feed" id="feed"><div class="empty">Waiting for Minecraft events...</div></div>
</div>
<script>
let allEvents=[],lastCount=0;
const startTime=Date.now();
function fmt(n){if(n===null||n===undefined||isNaN(n))return'—';if(Math.abs(n)>=1e6)return(n/1e6).toFixed(2)+'M';if(Math.abs(n)>=1e3)return(n/1e3).toFixed(1)+'k';return Math.round(n).toLocaleString();}
function fmtUptime(ms){const s=Math.floor(ms/1000);return Math.floor(s/3600)+':'+String(Math.floor((s%3600)/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');}
setInterval(()=>{const el=document.getElementById('sUptime');if(el)el.textContent=fmtUptime(Date.now()-startTime);},1000);
function eventRow(ev){
  let badge='',desc='',val='',vc='neutral';
  if(ev.type==='shiny_coins'){badge='<span class="badge coins">COINS</span>';desc='Coin drop';val='+'+fmt(ev.coins);vc='pos';}
  else if(ev.type==='shiny_item'){badge='<span class="badge item">ITEM</span>';desc=ev.qty+'x '+ev.item;val=ev.value?'~'+fmt(ev.value):'—';vc=ev.value?'pos':'neutral';}
  else if(ev.type==='shiny_enchant'){badge='<span class="badge item">BOOK</span>';desc=ev.enchant;val='—';}
  else if(ev.type==='shiny_xp'){badge='<span class="badge other">XP</span>';desc=fmt(ev.amount)+' '+ev.xp_type;val='—';}
  else if(ev.type==='shiny_other'){badge='<span class="badge other">SHINY</span>';desc=(ev.raw||'').substring(0,60);val='—';}
  else if(ev.type==='buy'){badge='<span class="badge buy">BUY</span>';desc=ev.qty+'x '+ev.item;val='-'+fmt(ev.coins);vc='neg';}
  else if(ev.type==='sell'){badge='<span class="badge sell">SELL</span>';desc=ev.qty+'x '+ev.item;val='+'+fmt(ev.coins);vc='pos';}
  return`<div class="row"><span class="row-ts">${ev.ts||''}</span>${badge}<span class="row-desc" title="${(ev.raw||'').replace(/"/g,'&quot;')}">${desc}</span><span class="row-val ${vc}">${val}</span></div>`;
}
function applyFilter(){
  const q=document.getElementById('filterInput').value.toLowerCase();
  const f=q?allEvents.filter(e=>(e.item||'').toLowerCase().includes(q)||(e.type||'').includes(q)||(e.raw||'').toLowerCase().includes(q)):allEvents;
  document.getElementById('feed').innerHTML=f.length?[...f].reverse().map(eventRow).join(''):'<div class="empty">No events yet...</div>';
}
function updateStats(){
  let orbCost=0,coinDrops=0,itemVal=0;
  for(const ev of allEvents){
    if(ev.type==='buy')orbCost+=ev.coins||0;
    if(ev.type==='shiny_coins')coinDrops+=ev.coins||0;
    if(ev.type==='shiny_item'&&ev.value)itemVal+=ev.value;
  }
  const pigs=allEvents.filter(e=>e.type==='shiny_coins').length||allEvents.filter(e=>['shiny_item','shiny_enchant'].includes(e.type)).length;
  const net=coinDrops+itemVal-orbCost;
  const h=(Date.now()-startTime)/3600000;
  document.getElementById('sOrbCost').textContent=fmt(orbCost);
  document.getElementById('sCoinDrops').textContent=fmt(coinDrops);
  document.getElementById('sItemVal').textContent=fmt(itemVal);
  const ne=document.getElementById('sNet');ne.textContent=(net>=0?'+':'')+fmt(net);ne.className='card-val '+(net>0?'green':net<0?'red':'');
  document.getElementById('sPigs').textContent=pigs;
  document.getElementById('sProfitPig').textContent=pigs?fmt((coinDrops+itemVal)/pigs):'—';
  const ph=h>0?net/h:0;const pe=document.getElementById('sProfitH');
  pe.textContent=fmt(ph)+'/h';pe.className='card-val '+(ph>0?'green':ph<0?'red':'');
}
function clearEvents(){allEvents=[];lastCount=0;document.getElementById('feed').innerHTML='<div class="empty">Cleared.</div>';updateStats();fetch('/clear',{method:'POST'});}
document.getElementById('filterInput').addEventListener('input',applyFilter);
async function poll(){
  try{
    const r=await fetch('/events?since='+lastCount);
    const d=await r.json();
    if(d.events&&d.events.length>0){allEvents=allEvents.concat(d.events);lastCount+=d.events.length;applyFilter();updateStats();}
    document.getElementById('dot').className='dot';
    document.getElementById('statusMsg').textContent='Live — watching latest.log';
    if(d.bazaar_count)document.getElementById('bzStatus').textContent=`Bazaar: ${d.bazaar_count} items`;
  }catch(e){document.getElementById('dot').className='dot warn';document.getElementById('statusMsg').textContent='Reconnecting...';}
  setTimeout(poll,800);
}

async function loadConfig(){
  try{
    const r=await fetch('/config');
    const d=await r.json();
    document.getElementById('logPathInput').value=d.log_path||'';
  }catch(e){}
}

async function savePath(){
  const p=document.getElementById('logPathInput').value.trim();
  const msg=document.getElementById('pathMsg');
  if(!p){msg.textContent='enter a path';msg.className='settings-msg err';return;}
  try{
    const r=await fetch('/setpath',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({log_path:p})});
    const d=await r.json();
    if(d.ok){msg.textContent='saved ✓';msg.className='settings-msg ok';}
    else{msg.textContent='invalid path';msg.className='settings-msg err';}
  }catch(e){msg.textContent='error';msg.className='settings-msg err';}
  setTimeout(()=>{msg.textContent='';},3000);
}

loadConfig();

poll();
</script></body></html>
"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self,format,*args): pass
    def do_GET(self):
        if self.path in ("/","/index.html"):
            self.send_response(200);self.send_header("Content-Type","text/html;charset=utf-8");self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path.startswith("/events"):
            since=0
            try: since=int(self.path.split("since=")[1])
            except: pass
            with state_lock: ev=state["events"][since:]; bz=len(state["bazaar"])
            self.send_response(200);self.send_header("Content-Type","application/json")
            self.send_header("Access-Control-Allow-Origin","*");self.end_headers()
            self.wfile.write(json.dumps({"events":ev,"bazaar_count":bz}).encode())
        elif self.path=="/config":
            with state_lock: lp = state["log_path"]
            self.send_response(200);self.send_header("Content-Type","application/json")
            self.send_header("Access-Control-Allow-Origin","*");self.end_headers()
            self.wfile.write(json.dumps({"log_path":lp}).encode())
        else: self.send_response(404);self.end_headers()
    def do_POST(self):
        if self.path=="/setpath":
            length=int(self.headers.get("Content-Length",0))
            body=json.loads(self.rfile.read(length))
            new_path=body.get("log_path","").strip()
            if new_path:
                save_log_path(Path(new_path))
                with state_lock: state["log_path"]=new_path
                print(f"[config] Log path updated: {new_path}")
                self.send_response(200)
            else:
                self.send_response(400)
            self.send_header("Content-Type","application/json")
            self.send_header("Access-Control-Allow-Origin","*");self.end_headers()
            self.wfile.write(json.dumps({"ok":bool(new_path)}).encode())
        elif self.path=="/clear":
            with state_lock: state["events"].clear()
            self.send_response(200);self.end_headers()
        else: self.send_response(404);self.end_headers()

if __name__=="__main__":
    print("="*50)
    print("  Shiny Pig Profit Tracker")
    print(f"  Log : {LOG_PATH}")
    print(f"  URL : http://localhost:{PORT}")
    print("  Ctrl+C to stop")
    print("="*50)
    threading.Thread(target=bazaar_refresher,daemon=True).start()
    threading.Thread(target=tail_log,daemon=True).start()
    server=HTTPServer(("localhost",PORT),Handler)
    try:
        import webbrowser;webbrowser.open(f"http://localhost:{PORT}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[stopped]");state["running"]=False
