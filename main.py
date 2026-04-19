import time, requests, telebot, os, math, threading, statistics, json
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 配置与持久化 (Zeabur 强固逻辑)
# ==========================================
DATA_FILE = "trading_data.json"
BOT_TOKEN = os.getenv('BOT_TOKEN') or "你的TOKEN"
CHECKWX_API_KEY = os.getenv('CHECKWX_API_KEY') or "你的APIKEY"
MY_USER_ID = 6822447850 
BROADCAST_INTERVAL = 3600 

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
watchlist, positions, temp_memory, prob_memory = [], {}, {}, {}
airport_hod = {} 

def save_data():
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump({"watchlist": watchlist, "positions": positions}, f)
    except: pass

def load_data():
    global watchlist, positions
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                watchlist = data.get("watchlist", [])
                positions = data.get("positions", {})
        except: pass

load_data()

airport_metadata = {
    "ZBAA": {"name": "CN-Beijing", "coords": (40.0799, 116.6031), "cc": "CN"},
    "ZSPD": {"name": "CN-Shanghai", "coords": (31.1434, 121.8052), "cc": "CN"},
    "ZUCK": {"name": "CN-Chongqing", "coords": (29.7192, 106.6417), "cc": "CN"},
    "ZHHH": {"name": "CN-Wuhan", "coords": (30.7838, 114.2081), "cc": "CN"},
    "WSSS": {"name": "SG-Singapore", "coords": (1.3644, 103.9915), "cc": "SG"},
    "RKPK": {"name": "KR-Busan", "coords": (35.1795, 128.9382), "cc": "KR"},
    "RKSI": {"name": "KR-Incheon", "coords": (37.4602, 126.4407), "cc": "KR"},
    "RJTT": {"name": "JP-Tokyo", "coords": (35.5522, 139.7796), "cc": "JP"},
    "LIMC": {"name": "IT-Milan", "coords": (45.6301, 8.7255), "cc": "IT"},
    "EDDM": {"name": "DE-Munich", "coords": (48.3538, 11.7861), "cc": "DE"},
    "LFPG": {"name": "FR-Paris", "coords": (49.0097, 2.5479), "cc": "FR"},
    "EGLC": {"name": "GB-London", "coords": (51.5053, 0.0543), "cc": "GB"},
    "KMIA": {"name": "US-Miami", "coords": (25.7959, -80.2870), "cc": "US"},
    "LTAC": {"name": "TR-Ankara", "coords": (40.1281, 32.9951), "cc": "TR"},
    "DNMM": {"name": "NG-Lagos", "coords": (6.5774, 3.3215), "cc": "NG"},
    "LEMD": {"name": "ES-Madrid", "coords": (40.4983, -3.5676), "cc": "ES"},
    "EPWA": {"name": "PL-Warsaw", "coords": (52.1657, 20.9671), "cc": "PL"}
}

NUM_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟", "1️⃣1️⃣", "1️⃣2️⃣", "1️⃣3️⃣", "1️⃣4️⃣", "1️⃣5️⃣", "1️⃣6️⃣", "1️⃣7️⃣", "1️⃣8️⃣", "1️⃣9️⃣", "2️⃣0️⃣", "2️⃣1️⃣", "2️⃣2️⃣", "2️⃣3️⃣", "2️⃣4️⃣", "2️⃣5️⃣", "2️⃣6️⃣", "2️⃣7️⃣", "2️⃣8️⃣", "2️⃣9️⃣", "3️⃣0️⃣"]

# ==========================================
# 2. 数学引擎 (A 逻辑极值修正)
# ==========================================
def phi(z): return (1.0 + math.erf(z / math.sqrt(2.0))) / 2.0

def calculate_precise_win_rate(target, t_peak, curr_t, hour, intent, wind=0, rh=0, icao=""):
    try:
        t_f = float(target)
        if icao in airport_hod and airport_hod[icao] >= t_f:
            if intent == "YES" or (intent == "突破" and airport_hod[icao] >= t_f + 1.0): return 100.0
            if intent == "防守": return 0.0
        damping = 0.0
        if hour >= 13:
            if wind > 25: damping += 0.85
            elif wind > 15: damping += 0.35
            else: damping += (wind * 0.012)
            if rh > 75: damping += 0.3
            if hour >= 16: damping += 0.55
        adj_peak = t_peak - damping if t_peak > curr_t else t_peak
        sigma = 1.0 if hour < 12 else (0.6 if hour < 15 else (0.3 if hour < 17 else 0.15))
        z1, z2 = (t_f - adj_peak) / sigma, (t_f + 1.0 - adj_peak) / sigma
        p_final_yes = max(0.0, 100.0 * (phi(z2) - phi(z1)))
        p_hit_yes = min(100.0, p_final_yes * 2.1) 
        p_final_brk = 100.0 * (1.0 - phi(z2))
        p_hit_brk = min(100.0, p_final_brk * 2.1)
        p_def = round(phi(z1) * 100, 1)

        if intent == "防守": return p_def
        if intent == "突破": return round(p_hit_brk, 1)
        return round(p_hit_yes, 1)
    except: return 0.0

def sync_airport_history(icao):
    try:
        url = f"https://api.checkwx.com/metar/{icao}/history/12"
        r = requests.get(url, headers={"X-API-Key": CHECKWX_API_KEY}, timeout=10).json()
        if 'data' in r and r['data']:
            pts = []
            for d in reversed(r['data']):
                t, obs = d.get('temperature', {}).get('celsius'), d.get('observed')
                if t is not None and obs:
                    ts = datetime.strptime(obs, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
                    pts.append({'temp': t, 'time': datetime.fromtimestamp(ts, tz=timezone.utc)})
            if pts:
                temp_memory[icao] = pts[-12:]
                airport_hod[icao] = max([p['temp'] for p in pts])
                return True
    except: pass
    return False

def calc_ls_slope(icao, curr_t, metar):
    if not isinstance(curr_t, (int, float)): return "同步中", 0.0
    if icao not in temp_memory: temp_memory[icao] = []
    obs_dt = datetime.fromtimestamp(metar.get('observed_unix') or time.time(), tz=timezone.utc)
    if not any(abs((obs_dt - d['time']).total_seconds()) < 60 for d in temp_memory[icao]):
        temp_memory[icao].append({'temp': curr_t, 'time': obs_dt})
    data = [d for d in temp_memory[icao] if (datetime.now(timezone.utc) - d['time']).total_seconds() <= 7200]
    temp_memory[icao] = data
    if len(data) < 2: return "同步中", 0.0
    t_v = [(d['time'] - data[0]['time']).total_seconds()/3600.0 for d in data]; T_v = [d['temp'] for d in data]
    n = len(data); denom = (n * sum(x**2 for x in t_v)) - (sum(t_v)**2)
    m = 0.0 if denom == 0 else ((n * sum(x*y for x,y in zip(t_v, T_v))) - (sum(t_v)*sum(T_v))) / denom
    return (f"↑{abs(round(m,2))}" if m > 0 else f"↓{abs(round(m,2))}"), round(m, 2)

# ==========================================
# 3. 视觉报告引擎 (保持 V16.4 战时模板)
# ==========================================
def build_single_report(icao):
    icao = icao.upper()
    if icao not in airport_metadata: return f"❌ {icao} 未录入元数据\n\n", []
    if icao not in airport_hod or len(temp_memory.get(icao, [])) < 2: sync_airport_history(icao)

    speci_tag, model_warn = "", ""
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers={"X-API-Key": CHECKWX_API_KEY}, timeout=5).json()
        metar = r['data'][0]
        if "SPECI" in metar.get('raw_text', ''): speci_tag = " [SPECI 突变]"
        curr_t = metar.get('temperature', {}).get('celsius', "N/A")
        if isinstance(curr_t, (int, float)): airport_hod[icao] = max(airport_hod.get(icao, -99), curr_t)
        if obs_str := metar.get('observed'): 
            metar['observed_unix'] = datetime.strptime(obs_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except: return f"❌ {icao} METAR获取失败\n\n", []

    cloud, rad, wind, rh, g_max, m_max, e_max, offset = "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", 0
    try:
        lat, lon = airport_metadata[icao]['coords']
        f = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,cloudcover,shortwave_radiation,windspeed_10m,relative_humidity_2m&models=dwd_icon,ecmwf_ifs,gfs_seamless&forecast_days=1&timezone=auto", timeout=10).json()
        hourly, offset = f.get('hourly', {}), f.get('utc_offset_seconds', 0)
        local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
        h_idx = min(23, max(0, local_dt.hour))
        def gv(k): v = hourly.get(k); return v[h_idx] if v and len(v) > h_idx else "N/A"
        cloud, rad, wind, rh = gv('cloudcover'), gv('shortwave_radiation'), gv('windspeed_10m'), gv('relative_humidity_2m')
        def gm(k): ts = [v for v in hourly.get(k, [])[:24] if isinstance(v, (int, float))]; return round(max(ts), 1) if ts else "N/A"
        g_max, m_max, e_max = gm('temperature_2m_gfs_seamless'), gm('temperature_2m_dwd_icon'), gm('temperature_2m_ecmwf_ifs')
        v_f = [v for v in [g_max, m_max, e_max] if isinstance(v, (int, float))]
        if len(v_f) >= 2 and (max(v_f) - min(v_f)) > 2.5: model_warn = " ⚠️ [模型严重分歧]"
    except: pass

    tp_peak = max(curr_t, statistics.median(v_f)) if 'v_f' in locals() and v_f and isinstance(curr_t, (int,float)) else 25.0
    slope_str, m_val = calc_ls_slope(icao, curr_t, metar)
    local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
    h, num_wind, num_rh = local_dt.hour, (wind if isinstance(wind, (int,float)) else 0), (rh if isinstance(rh, (int,float)) else 0)

    matrix_lines = []
    curr_base = math.floor(curr_t if isinstance(curr_t,(int,float)) else 25)
    for i in range(1, 6):
        lvl = (curr_base - 1) + i
        v_req = round((float(lvl) + 1.0 - curr_t) / max(0.5, 19 - h), 2) if isinstance(curr_t, (int,float)) else 0.0
        pb, pd, py = calculate_precise_win_rate(lvl, tp_peak, curr_t, h, "突破", num_wind, num_rh, icao), calculate_precise_win_rate(lvl, tp_peak, curr_t, h, "防守", num_wind, num_rh, icao), calculate_precise_win_rate(lvl, tp_peak, curr_t, h, "YES", num_wind, num_rh, icao)
        diag = " [已突破:结算Yes]" if icao in airport_hod and airport_hod[icao] >= lvl + 1.0 else (" 🚀 [突破中]" if pb > 65 else (" 🛡️ [防守中]" if pd > 85 else (" 💀 [极难触碰]" if v_req > m_val + 0.9 else "")))
        matrix_lines.append(f"{NUM_EMOJIS[i-1]} {lvl}N [需↑{v_req}|实{slope_str}] 突破:{pb}%|防守:{pd}%|YES:{py}%{diag}")

    pos_display, global_pos_list = "-- (空仓观望)", []
    cc, main_dot, airport_dots = airport_metadata[icao]['cc'], "⚪", []
    flag = chr(ord(cc[0])+127397)+chr(ord(cc[1])+127397)
    if icao in positions and positions[icao]:
        pos_lines = []
        for target, _, intent in positions[icao]:
            win_pct = calculate_precise_win_rate(target, tp_peak, curr_t, h, intent, num_wind, num_rh, icao)
            ma = "↑" if win_pct > prob_memory.get(f"{icao}_{target}_{intent}", win_pct) else ("↓" if win_pct < prob_memory.get(f"{icao}_{target}_{intent}", win_pct) else "-")
            prob_memory[f"{icao}_{target}_{intent}"] = win_pct
            dot = "🟢" if (win_pct >= 80 or (icao in airport_hod and airport_hod[icao] >= float(target))) else "🔴"
            airport_dots.append(dot)
            pos_lines.append(f"{dot} ({target}N | 胜率:{win_pct}% {ma}) {'🛡️ [防守中]' if intent=='防守' else '🚀 [博弈中]'} [胜率{'提升📈' if ma=='↑' else '下降📉' if ma=='↓' else '横盘➖'}]")
            global_pos_list.append(f"{flag} {icao} {target}N **{intent}** (胜率:{win_pct}% {ma})")
        pos_display, main_dot = "\n".join(pos_lines), ("🔴" if "🔴" in airport_dots else "🟢")

    report = (f"----------------------\n{main_dot} {flag} **{icao} ({airport_metadata[icao]['name']})**\n"
              f"**【盘口状态】** 当地 {local_dt.strftime('%H:%M')} | 实时 {curr_t}°C{speci_tag} | HOD: {airport_hod.get(icao, 'N/A')}°C | 斜率: {slope_str}\n"
              f"**【环境前瞻】** 云量: {cloud}% | 辐射: {rad}W/m² | 风速: {wind}km/h | 湿度: {rh}%\n"
              f"**【持仓监控】**\n{pos_display}\n"
              f"🔥 **【预测矩阵】**: (G:{g_max} I:{m_max} E:{e_max}){model_warn} | 基准: {round(tp_peak,1)}°C\n" + "\n".join(matrix_lines) + "\n")
    return report, global_pos_list

# ==========================================
# 4. 指令系统 (批量解析器)
# ==========================================
def send_chunked(chat_id, header, reports, footer):
    msg = header
    for r in reports:
        if len(msg) + len(r) > 3800: bot.send_message(chat_id, msg, parse_mode="Markdown"); msg = ""
        msg += r
    if footer: msg += footer
    if msg.strip(): bot.send_message(chat_id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['watch'])
def cmd_watch(message):
    icaos = [i.upper() for i in message.text.split()[1:] if len(i) == 4]
    added = []
    for icao in icaos:
        if icao not in watchlist:
            watchlist.append(icao)
            sync_airport_history(icao)
            added.append(icao)
    save_data()
    bot.reply_to(message, f"📡 批量开启监控: {', '.join(added)}" if added else "⚠️ 机场已在列表或格式错误")

@bot.message_handler(commands=['pos'])
def cmd_pos(message):
    parts, cur_icao, added = message.text.split()[1:], None, []
    i = 0
    while i < len(parts):
        p = parts[i].upper()
        if len(p) == 4 and p.isalpha():
            cur_icao = p
            if cur_icao not in watchlist: watchlist.append(cur_icao)
            i += 1
            continue
        if cur_icao and i + 1 < len(parts):
            target, intent = parts[i].upper().replace('N', ''), parts[i+1].upper()
            if intent in ['突破', '防守', 'YES']:
                if cur_icao not in positions: positions[cur_icao] = []
                if not any(x[0] == target and x[2] == intent for x in positions[cur_icao]):
                    positions[cur_icao].append((target, 'N', intent))
                    added.append(f"{cur_icao} {target}N {intent}")
                i += 2
                continue
        i += 1
    save_data(); bot.send_message(message.chat.id, "✅ 批量录入:\n" + "\n".join(added) if added else "⚠️ 识别失败")

@bot.message_handler(commands=['delpos'])
def cmd_delpos(message):
    parts = message.text.split()[1:]
    if not parts: bot.reply_to(message, "⚠️ 格式: `/delpos ICAO` 或 `/delpos all` "); return
    if parts[0].lower() == 'all':
        positions.clear(); watchlist.clear(); save_data(); bot.reply_to(message, "🗑️ 全部清空"); return
    
    cur_icao, removed = None, []
    i = 0
    while i < len(parts):
        p = parts[i].upper()
        # 1. 如果是 ICAO
        if len(p) == 4 and p.isalpha():
            cur_icao = p
            # 如果 ICAO 后面没有了，或者后面跟着的是另一个 ICAO，说明是删除整个机场
            if i + 1 == len(parts) or (len(parts[i+1]) == 4 and parts[i+1].isalpha()):
                if cur_icao in positions: del positions[cur_icao]
                if cur_icao in watchlist: watchlist.remove(cur_icao)
                removed.append(f"{cur_icao} (整体移除)")
                i += 1
                continue
            i += 1
            continue
        # 2. 如果是 仓位细节
        if cur_icao and i + 1 < len(parts):
            target, intent = parts[i].upper().replace('N', ''), parts[i+1].upper()
            if cur_icao in positions:
                # 手术刀删除
                original_len = len(positions[cur_icao])
                positions[cur_icao] = [x for x in positions[cur_icao] if not (x[0]==target and x[2]==intent)]
                if len(positions[cur_icao]) < original_len:
                    removed.append(f"{cur_icao} {target}N {intent}")
            i += 2
            continue
        i += 1
    save_data(); bot.send_message(message.chat.id, "🗑️ 删除结果:\n" + "\n".join(removed) if removed else "⚠️ 未找到对应项")

@bot.message_handler(commands=['list'])
def cmd_list(message):
    txt = "📋 **监控列表:** " + (", ".join(watchlist) if watchlist else "空")
    if positions:
        txt += "\n💰 **持仓分布:**\n"
        for icao, pos in positions.items():
            for p in pos: txt += f"• {icao}: {p[0]}N {p[2]}\n"
    bot.reply_to(message, txt)

@bot.message_handler(commands=['settime'])
def cmd_settime(message):
    global BROADCAST_INTERVAL
    try:
        mins = int(message.text.split()[1]); BROADCAST_INTERVAL = mins * 60
        bot.reply_to(message, f"✅ 播报频率改为: {mins} 分钟")
    except: pass

@bot.message_handler(commands=['now'])
def cmd_now(message):
    target = [i.upper() for i in message.text.split()[1:] if len(i)==4] or watchlist
    reports, all_pos = [], []
    for icao in target:
        txt, ps = build_single_report(icao); reports.append(txt); all_pos.extend(ps)
    risk_summary = "\n----------------------\n🚨 **【风险汇总】:**\n" + "\n".join([f"{NUM_EMOJIS[idx] if idx<30 else ''} {p}" for idx, p in enumerate(all_pos)]) if all_pos else ""
    send_chunked(message.chat.id, "📊 **【全量战报】**\n\n", reports, risk_summary)

def auto_broadcast_loop():
    while True:
        try:
            if watchlist:
                reports, all_pos = [], []
                for i in watchlist:
                    t, p = build_single_report(i); reports.append(t); all_pos.extend(p)
                risk_summary = "\n----------------------\n🚨 **【风险汇总】:**\n" + "\n".join([f"{NUM_EMOJIS[idx] if idx<30 else ''} {p}" for idx, p in enumerate(all_pos)]) if all_pos else ""
                send_chunked(MY_USER_ID, f"📢 **【定时播报】** ({BROADCAST_INTERVAL//60}min)\n\n", reports, risk_summary)
            time.sleep(BROADCAST_INTERVAL)
        except: time.sleep(60)

if __name__ == "__main__":
    for i in watchlist: sync_airport_history(i)
    threading.Thread(target=auto_broadcast_loop, daemon=True).start()
    bot.infinity_polling()
