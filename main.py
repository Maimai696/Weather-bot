import time, requests, telebot, os, math, threading, statistics
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 配置中心
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN') or "你的TOKEN"
CHECKWX_API_KEY = os.getenv('CHECKWX_API_KEY') or "你的APIKEY"
MY_USER_ID = 6822447850 

BROADCAST_INTERVAL = 3600 

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
watchlist, positions, temp_memory, prob_memory = [], {}, {}, {}

airport_metadata = {
    "ZBAA": {"name": "CN-Beijing", "coords": (40.0799, 116.6031), "cc": "CN"},
    "ZSPD": {"name": "CN-Shanghai", "coords": (31.1434, 121.8052), "cc": "CN"},
    "ZUCK": {"name": "CN-Chongqing", "coords": (29.7192, 106.6417), "cc": "CN"},
    "ZHHH": {"name": "CN-Wuhan", "coords": (30.7838, 114.2081), "cc": "CN"},
    "WSSS": {"name": "SG-Singapore", "coords": (1.3644, 103.9915), "cc": "SG"},
    "RKPK": {"name": "KR-Busan", "coords": (35.1795, 128.9382), "cc": "KR"},
    "RKSI": {"name": "KR-Incheon", "coords": (37.4602, 126.4407), "cc": "KR"},
    "RJTT": {"name": "JP-Tokyo", "coords": (35.5522, 139.7796), "cc": "JP"},
    "VILK": {"name": "IN-Lucknow", "coords": (26.7606, 80.8893), "cc": "IN"},
    "LTAC": {"name": "TR-Ankara", "coords": (40.1281, 32.9951), "cc": "TR"},
    "LIMC": {"name": "IT-Milan", "coords": (45.6301, 8.7255), "cc": "IT"},
    "EPWA": {"name": "PL-Warsaw", "coords": (52.1657, 20.9671), "cc": "PL"},
    "EDDM": {"name": "DE-Munich", "coords": (48.3538, 11.7861), "cc": "DE"},
    "LEMD": {"name": "ES-Madrid", "coords": (40.4983, -3.5676), "cc": "ES"},
    "LFPG": {"name": "FR-Paris", "coords": (49.0097, 2.5479), "cc": "FR"},
    "EGLC": {"name": "GB-London", "coords": (51.5053, 0.0543), "cc": "GB"},
    "DNMM": {"name": "NG-Lagos", "coords": (6.5774, 3.3215), "cc": "NG"},
    "SAEZ": {"name": "AR-Buenos Aires", "coords": (-34.8222, -58.5358), "cc": "AR"},
    "SBGR": {"name": "BR-Sao Paulo", "coords": (-23.4356, -46.4731), "cc": "BR"},
    "KMIA": {"name": "US-Miami", "coords": (25.7959, -80.2870), "cc": "US"}
}

NUM_EMOJIS = [
    "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟",
    "1️⃣1️⃣", "1️⃣2️⃣", "1️⃣3️⃣", "1️⃣4️⃣", "1️⃣5️⃣", "1️⃣6️⃣", "1️⃣7️⃣", "1️⃣8️⃣", "1️⃣9️⃣", "2️⃣0️⃣",
    "2️⃣1️⃣", "2️⃣2️⃣", "2️⃣3️⃣", "2️⃣4️⃣", "2️⃣5️⃣", "2️⃣6️⃣", "2️⃣7️⃣", "2️⃣8️⃣", "2️⃣9️⃣", "3️⃣0️⃣"
]

# ==========================================
# 2. 核心数学模型
# ==========================================
def phi(z): return (1.0 + math.erf(z / math.sqrt(2.0))) / 2.0

def calculate_precise_win_rate(target, t_peak, curr_t, hour, intent, wind=0, rh=0):
    try:
        t_f = float(target)
        damping = 0.0
        if hour >= 14:
            damping += (wind * 0.015) 
            if rh > 70: damping += 0.2 
            if hour >= 16: damping += 0.4 
        adj_peak = t_peak - damping if t_peak > curr_t else t_peak
        sigma = 1.0 if hour < 12 else (0.6 if hour < 15 else (0.3 if hour < 17 else 0.15))
        
        if curr_t >= t_f + 1.0:
            if intent == "突破": return 100.0
            if intent == "防守": return 0.0
            return 0.0

        z1 = (t_f - adj_peak) / sigma
        z2 = (t_f + 1.0 - adj_peak) / sigma
        p_def = round(phi(z1) * 100, 1)
        p_brk = round((1.0 - phi(z2)) * 100, 1)
        p_yes = max(0.0, 100.0 - p_def - p_brk) 
        if intent == "防守": return p_def
        if intent == "突破": return p_brk
        return round(p_yes, 1)
    except: return 0.0

def calc_ls_slope(icao, curr_t, metar):
    if not isinstance(curr_t, (int, float)): return "同步中", 0.0
    if icao not in temp_memory: temp_memory[icao] = []
    obs_unix = metar.get('observed_unix') or time.time()
    obs_dt = datetime.fromtimestamp(obs_unix, tz=timezone.utc)
    if not any(abs((obs_dt - d['time']).total_seconds()) < 60 for d in temp_memory[icao]):
        temp_memory[icao].append({'temp': curr_t, 'time': obs_dt})
    data = [d for d in temp_memory[icao] if (datetime.now(timezone.utc) - d['time']).total_seconds() <= 7200]
    temp_memory[icao] = data
    if len(data) < 2: return "同步中", 0.0
    t_v = [(d['time'] - data[0]['time']).total_seconds()/3600.0 for d in data]; T_v = [d['temp'] for d in data]
    n = len(data); denom = (n * sum(x**2 for x in t_v)) - (sum(t_v)**2)
    m = 0.0 if denom == 0 else ((n * sum(x*y for x,y in zip(t_v, T_v))) - (sum(t_v)*sum(T_v))) / denom
    return (f"↑{abs(round(m,2))}" if m > 0 else f"↓{abs(round(m,2))}"), round(m, 2)

def get_trend_arrow(icao, target, sub_key, current_val):
    mem_key = f"{icao}_{target}_{sub_key}"
    last_val = prob_memory.get(mem_key, current_val); prob_memory[mem_key] = current_val
    return "↑" if current_val > last_val else ("↓" if current_val < last_val else "-")

# ==========================================
# 3. 报告引擎 (数据键路由深度重构)
# ==========================================
def build_single_report(icao):
    icao = icao.upper()
    if icao not in airport_metadata: return f"❌ {icao} 未录入\n\n", []
    lat, lon = airport_metadata[icao]['coords']
    
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers={"X-API-Key": CHECKWX_API_KEY}, timeout=5).json()
        metar = r['data'][0]
        curr_t = metar.get('temperature', {}).get('celsius', "N/A")
        obs_str = metar.get('observed')
        if obs_str: metar['observed_unix'] = datetime.strptime(obs_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except: return f"❌ {icao} METAR获取失败\n\n", []

    # 变量初始化
    cloud, rad, wind, rh, g_max, m_max, e_max, offset = "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", 0
    try:
        api_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,cloudcover,shortwave_radiation,windspeed_10m,relative_humidity_2m&models=dwd_icon,ecmwf_ifs,gfs_seamless&forecast_days=1&timezone=auto"
        f = requests.get(api_url, timeout=10).json()
        hourly = f.get('hourly', {})
        offset = f.get('utc_offset_seconds', 0)
        local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
        h_idx = min(23, max(0, local_dt.hour))

        # 核心修复：多模型数据路由系统
        def fetch_env(key):
            # 尝试 1: 通用键 (best-match)
            val_list = hourly.get(key)
            # 尝试 2: 降级到具体模型键 (优先 ECMWF，次选 GFS)
            if val_list is None: val_list = hourly.get(f"{key}_ecmwf_ifs")
            if val_list is None: val_list = hourly.get(f"{key}_gfs_seamless")
            
            if val_list and len(val_list) > h_idx:
                v = val_list[h_idx]
                return v if v is not None else "N/A"
            return "N/A"

        cloud, rad, wind, rh = fetch_env('cloudcover'), fetch_env('shortwave_radiation'), fetch_env('windspeed_10m'), fetch_env('relative_humidity_2m')
        
        # 提取三大模型峰值
        def gm(k): ts = [v for v in hourly.get(k, [])[:24] if isinstance(v, (int, float))]; return round(max(ts), 1) if ts else "N/A"
        g_max, m_max, e_max = gm('temperature_2m_gfs_seamless'), gm('temperature_2m_dwd_icon'), gm('temperature_2m_ecmwf_ifs')
    except: pass

    # 偏差审计
    valid_f = [v for v in [g_max, m_max, e_max] if isinstance(v, (int, float))]
    gap = max(valid_f) - min(valid_f) if len(valid_f) >= 2 else 0
    model_warn = f" | ⚠️ **[模型偏差: {round(gap,1)}°C]**" if gap >= 3.5 else ""
    base_f = statistics.median(valid_f) if valid_f else (curr_t if isinstance(curr_t, (int,float)) else 25.0)
    tp_peak = max(curr_t, base_f) if isinstance(curr_t, (int,float)) else base_f
    
    local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset); h = local_dt.hour
    slope_str, m_val = calc_ls_slope(icao, curr_t, metar)
    num_wind = wind if isinstance(wind, (int, float)) else 0
    num_rh = rh if isinstance(rh, (int, float)) else 0

    # 预测矩阵
    matrix_lines = []
    curr_base = math.floor(curr_t) if isinstance(curr_t, (int,float)) else 25
    rem_h = max(0.5, 19 - h)
    for i in range(1, 6):
        lvl = (curr_base - 1) + i
        v_req = round((float(lvl) + 1.0 - curr_t) / rem_h, 2) if isinstance(curr_t,(int,float)) else 0.0
        p_brk = calculate_precise_win_rate(lvl, tp_peak, curr_t, h, "突破", num_wind, num_rh)
        p_def = calculate_precise_win_rate(lvl, tp_peak, curr_t, h, "防守", num_wind, num_rh)
        p_yes = calculate_precise_win_rate(lvl, tp_peak, curr_t, h, "YES", num_wind, num_rh)
        diag = ""
        if curr_t >= lvl + 1.0: diag = " [已突破:立马结算]"
        elif p_brk > 65: diag = " 🚀 [突破中]"
        elif p_def > 85: diag = " 💰 [防守套利]" if p_def > 95 and (m_val < v_req - 0.4) else " 🛡️ [防守中]"
        elif v_req > m_val + 0.8: diag = " 💀 [停止不前]"
        ne = NUM_EMOJIS[i-1] if i-1 < len(NUM_EMOJIS) else f"{i}."
        matrix_lines.append(f"{ne} {lvl}N [需↑{v_req}|实{slope_str}] 突破:{p_brk}%|防守:{p_def}%|YES:{p_yes}%{diag}")

    # 持仓监控
    pos_display, global_pos_list = "-- (空仓观望)", []
    cc = airport_metadata[icao]['cc']; flag = chr(ord(cc[0])+127397)+chr(ord(cc[1])+127397)
    main_dot, airport_dots = "⚪", []
    
    if icao in positions and len(positions[icao]) > 0:
        pos_lines = []
        for target, _, intent in positions[icao]:
            pb, pd, py = calculate_precise_win_rate(target, tp_peak, curr_t, h, "突破", num_wind, num_rh), calculate_precise_win_rate(target, tp_peak, curr_t, h, "防守", num_wind, num_rh), calculate_precise_win_rate(target, tp_peak, curr_t, h, "YES", num_wind, num_rh)
            ab, ad, ay = get_trend_arrow(icao, target, "BRK", pb), get_trend_arrow(icao, target, "DEF", pd), get_trend_arrow(icao, target, "YES", py)
            win_pct = pb if intent == "突破" else (pd if intent == "防守" else py)
            ma = get_trend_arrow(icao, target, intent, win_pct)
            trend_txt = "[胜率提升📈]" if ma == "↑" else ("[胜率下降📉]" if ma == "↓" else "[胜率横盘➖]")
            airport_dots.append("🟢" if (win_pct >= 80 or (intent == "突破" and curr_t >= float(target) + 1.0)) else "🔴")
            
            if intent == "突破": reason = f"辐射 {rad}W/m² 支撑斜率 {slope_str}，冲刺 {float(target)+1.0}" if ma == "↑" else f"风阻 {wind}km/h 与湿度 {rh}% 阻尼动能"
            else: reason = f"风速 {wind}km/h 与湿度 {rh}% 热力阻尼，防线稳固" if ma == "↑" else f"斜率 {slope_str} 强于预期，防线受压"
            pos_lines.append(f"({target}N | 突破:{pb}% {ab} | 防守:{pd}% {ad} | YES区:{py}% {ay}) {'🚀 [突破套利]' if intent=='突破' else '🛡️ [防守中]'} {trend_txt}\n原因：{reason}")
            global_pos_list.append(f"{flag} {icao} {target}N **{intent}** (胜率:{win_pct}% {ma}) {trend_txt}\n理由：{reason}")
        pos_display = "\n".join(pos_lines)
        main_dot = "🔴" if "🔴" in airport_dots else "🟢"

    report = (f"----------------------\n{main_dot} {flag} **{icao} ({airport_metadata[icao]['name']})**\n"
              f"**【盘口状态】** 当地 {local_dt.strftime('%H:%M')} | 实时 {curr_t}°C | 斜率: {slope_str}\n"
              f"**【环境前瞻】** 云量: {cloud}% | 辐射: {rad}W/m² | 风速: {wind}km/h | 湿度: {rh}%\n"
              f"**【持仓监控】**\n{pos_display}\n"
              f"🔥 **【预测矩阵】**: (G:{g_max} I:{m_max} E:{e_max}) | 基准: {round(tp_peak,1)}°C{model_warn}\n" + "\n".join(matrix_lines) + "\n")
    return report, global_pos_list

# ==========================================
# 4. 指令与播报
# ==========================================
def format_exec_board(all_pos):
    res = ""
    for idx, p in enumerate(all_pos):
        ne = NUM_EMOJIS[idx] if idx < len(NUM_EMOJIS) else f"{idx+1}."
        res += f"{ne} {p}\n"
    return res

def auto_broadcast_loop():
    while True:
        try:
            if watchlist:
                reports, all_pos = [], []
                for i in watchlist:
                    t, p = build_single_report(i); reports.append(t); all_pos.extend(p)
                send_chunked_message(MY_USER_ID, f"📢 **【定时播报】**\n\n", reports, f"----------------------\n🚨 **【风险汇总】:**\n{format_exec_board(all_pos)}")
            time.sleep(BROADCAST_INTERVAL)
        except: time.sleep(60)

def send_chunked_message(chat_id, header, reports, footer):
    msg = header
    for r in reports:
        if len(msg) + len(r) > 3800: bot.send_message(chat_id, msg, parse_mode="Markdown"); msg = ""
        msg += r
    if footer: msg += footer
    if msg.strip(): bot.send_message(chat_id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['settime'])
def cmd_settime(message):
    global BROADCAST_INTERVAL
    try:
        mins = int(message.text.split()[1]); BROADCAST_INTERVAL = mins * 60
        bot.reply_to(message, f"✅ 已改为: {mins} 分钟")
    except: bot.reply_to(message, "⚠️ 格式: /settime 15")

@bot.message_handler(commands=['now'])
def cmd_now(message):
    icaos = [i.upper() for i in message.text.split()[1:] if len(i)==4]
    target_list = icaos if icaos else watchlist
    reports, all_pos = [], []
    for icao in target_list:
        txt, ps = build_single_report(icao); reports.append(txt); all_pos.extend(ps)
    send_chunked_message(message.chat.id, f"📊 **【全量战报】**\n\n", reports, f"----------------------\n🚨 **【风险汇总】:**\n{format_exec_board(all_pos)}")

@bot.message_handler(commands=['pos', 'POS', 'Pos'])
def cmd_pos(message):
    parts, current_icao, added = message.text.split()[1:], None, []
    i = 0
    while i < len(parts):
        p = parts[i].upper()
        if len(p) == 4 and p.isalpha(): current_icao = p; i += 1
        else:
            if current_icao and i + 1 < len(parts):
                target, intent = p.replace('N', ''), parts[i+1].upper()
                if intent in ['突破', '防守', 'YES']:
                    if current_icao not in positions: positions[current_icao] = []
                    positions[current_icao].append((target, 'N', intent))
                    if current_icao not in watchlist: watchlist.append(current_icao)
                    added.append(f"{current_icao} {target}N {intent}"); i += 2
                else: i += 1
            else: i += 1
    bot.send_message(message.chat.id, "✅ 录入:\n" + "\n".join(added) if added else "⚠️ 格式错误")

@bot.message_handler(commands=['delpos', 'DELPOS', 'Delpos'])
def cmd_delpos(message):
    parts, current_icao, removed = message.text.split()[1:], None, []
    i = 0
    while i < len(parts):
        p = parts[i].upper()
        if len(p) == 4 and p.isalpha():
            current_icao = p
            if i + 1 == len(parts) or (len(parts[i+1]) == 4 and parts[i+1].isalpha()):
                if current_icao in positions: del positions[current_icao]; removed.append(f"{current_icao} (清空)")
                i += 1
            else: i += 1
        else:
            if current_icao and current_icao in positions:
                target = p.replace('N', '')
                if i + 1 < len(parts) and parts[i+1].upper() in ['突破', '防守', 'YES']:
                    intent = parts[i+1].upper()
                    positions[current_icao] = [x for x in positions[current_icao] if not (x[0]==target and x[2]==intent)]
                    removed.append(f"{current_icao} {target}N {intent}"); i += 2
                else:
                    positions[current_icao] = [x for x in positions[current_icao] if x[0]!=target]
                    removed.append(f"{current_icao} {target}N (全向)"); i += 1
            else: i += 1
    for icao in list(positions.keys()):
        if not positions[icao]: del positions[icao]
    bot.send_message(message.chat.id, "🗑️ 删除:\n" + "\n".join(removed) if removed else "⚠️ 错误")

@bot.message_handler(commands=['watch', 'unwatch'])
def cmd_watch(message):
    parts = [p.upper() for p in message.text.split()[1:] if len(p) == 4]
    if '/watch' in message.text:
        added = [i for i in parts if i in airport_metadata and i not in watchlist]
        for i in added: watchlist.append(i)
        bot.send_message(message.chat.id, f"✅ 添加: {', '.join(added)}")
    else:
        for i in parts:
            if i in watchlist: watchlist.remove(i)
        bot.send_message(message.chat.id, f"✅ 移除: {', '.join(parts)}")

if __name__ == "__main__":
    threading.Thread(target=auto_broadcast_loop, daemon=True).start()
    bot.infinity_polling()
