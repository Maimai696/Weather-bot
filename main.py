import time, requests, telebot, os, math, threading, statistics
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 配置中心
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN') or "你的TOKEN"
CHECKWX_API_KEY = os.getenv('CHECKWX_API_KEY') or "你的APIKEY"
MY_USER_ID = 6822447850 

BROADCAST_INTERVAL = 1800 

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

# ==========================================
# 2. 核心数学模型
# ==========================================
def phi(z): return (1.0 + math.erf(z / math.sqrt(2.0))) / 2.0

def calculate_precise_win_rate(target, t_peak, curr_t, hour, intent):
    try:
        t_f = float(target)
        sigma = 0.8 if hour < 15 else 0.35
        if hour >= 17:
            is_win = curr_t < t_f or curr_t >= t_f + 1.0
            return 100.0 if is_win else 0.0
        if intent == "突破" and curr_t >= t_f + 1.0: return 100.0
        
        z1 = (t_f - t_peak) / sigma
        z2 = (t_f + 1.0 - t_peak) / sigma
        
        if intent == "防守": return round(phi(z1) * 100, 1)
        if intent == "突破": return round((1.0 - phi(z2)) * 100, 1)
        if intent == "YES": return round((phi(z2) - phi(z1)) * 100, 1)
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
    t_v = [(d['time'] - data[0]['time']).total_seconds()/3600.0 for d in data]
    T_v = [d['temp'] for d in data]
    n = len(data); denom = (n * sum(x**2 for x in t_v)) - (sum(t_v)**2)
    m = 0.0 if denom == 0 else ((n * sum(x*y for x,y in zip(t_v, T_v))) - (sum(t_v)*sum(T_v))) / denom
    return (f"↑{abs(round(m,2))}" if m > 0 else f"↓{abs(round(m,2))}"), round(m, 2)

# ==========================================
# 3. 报告引擎
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
    except: return f"❌ {icao} METAR数据获取失败\n\n", []

    g_max, m_max, e_max, cloud, rad, offset = "N/A", "N/A", "N/A", 0, 0, 0
    try:
        f = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,cloudcover,shortwave_radiation&models=dwd_icon,ecmwf_ifs,gfs_seamless&forecast_days=1&timezone=auto", timeout=10).json()
        offset = f.get('utc_offset_seconds', 0)
        def gm(k): ts = [v for v in f.get('hourly',{}).get(k,[])[:24] if isinstance(v,(int,float))]; return round(max(ts),1) if ts else "N/A"
        g_max, m_max, e_max = gm('temperature_2m_gfs_seamless'), gm('temperature_2m_dwd_icon'), gm('temperature_2m_ecmwf_ifs')
        
        local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
        h_idx = min(23, max(0, local_dt.hour))
        cloud = f.get('hourly', {}).get('cloudcover', [0]*24)[h_idx]
        rad = f.get('hourly', {}).get('shortwave_radiation', [0]*24)[h_idx]
    except: pass

    valid_f = [v for v in [g_max, m_max, e_max] if isinstance(v, (int, float))]
    model_warn = ""
    if len(valid_f) >= 2:
        gap = max(valid_f) - min(valid_f)
        if gap >= 3.5: model_warn = f" | ⚠️ **[模型偏差: {round(gap,1)}°C]**"
    
    base_f = statistics.median(valid_f) if valid_f else (curr_t if isinstance(curr_t, (int,float)) else 25.0)
    tp_peak = max(curr_t, base_f) if isinstance(curr_t, (int,float)) else base_f
    
    local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
    h = local_dt.hour
    slope_str, m_val = calc_ls_slope(icao, curr_t, metar)

    matrix_lines = []
    curr_base = math.floor(curr_t) if isinstance(curr_t, (int,float)) else 25
    rem_h = max(0.5, 17 - h) if h < 17 else 0.1
    
    for i in range(1, 6):
        lvl = (curr_base - 1) + i
        v_req = round((float(lvl) + 1.0 - curr_t) / rem_h, 2) if isinstance(curr_t,(int,float)) else 0.0
        p_brk = calculate_precise_win_rate(lvl, tp_peak, curr_t, h, "突破")
        p_def = calculate_precise_win_rate(lvl, tp_peak, curr_t, h, "防守")
        p_yes = calculate_precise_win_rate(lvl, tp_peak, curr_t, h, "YES")
        
        diag = ""
        if curr_t >= lvl + 1.0: diag = " [已突破:立马结算]"
        elif p_brk > 65: diag = " 🚀 [突破中]"
        elif p_def > 85:
            if p_def > 95 and (m_val < v_req - 0.4 or cloud > 80):
                diag = " 💰 [防守套利]"
            else:
                diag = " 🛡️ [防守中]"
        elif v_req > m_val + 0.8: diag = " 💀 [停止不前]"
        
        matrix_lines.append(f"({i}) {lvl}N [需↑{v_req}|实{slope_str}] 突破:{p_brk}%|防守:{p_def}%|YES:{p_yes}%{diag}")

    pos_display, global_pos_list = "-- (空仓观望)", []
    cc = airport_metadata[icao]['cc']
    flag = chr(ord(cc[0])+127397)+chr(ord(cc[1])+127397)
    
    main_dot = "⚪" 
    airport_dots = []

    if icao in positions and len(positions[icao]) > 0:
        pos_lines = []
        for target, _, intent in positions[icao]:
            win_pct = calculate_precise_win_rate(target, tp_peak, curr_t, h, intent)
            mem_key = f"{icao}_{target}_{intent}"
            last_p = prob_memory.get(mem_key, win_pct)
            arrow = "↑" if win_pct > last_p else ("↓" if win_pct < last_p else "-")
            trend_txt = "[胜率提升📈]" if arrow == "↑" else "[胜率下降📉]"
            prob_memory[mem_key] = win_pct
            
            pos_dot = "🔴" 
            if (intent == "突破" and curr_t >= float(target) + 1.0) or win_pct >= 80:
                pos_dot = "🟢"
            airport_dots.append(pos_dot)

            if intent == "突破":
                reason = f"辐射 {rad}W/m² 支撑斜率 {slope_str}，冲刺 {float(target)+1.0} 结算点" if arrow == "↑" else f"云量 {cloud}% 导致动能流失，进坑流血中"
            else:
                reason = f"云量 {cloud}% 动能封顶，{target}.0 防线稳固" if arrow == "↑" else f"物理斜率 {slope_str} 异常，防线受压"
            
            def_val = 0.0 if intent == "突破" and isinstance(curr_t, (int,float)) and curr_t >= float(target) else win_pct
            
            pos_lines.append(f"{pos_dot} ({target}N | **突破:{win_pct}% {arrow}** | **防守:{def_val}% -** | **YES区:{round(100-win_pct,1)}% ↓**) {'🚀 [突破套利]' if intent=='突破' else '🛡️ [防守中]'} {trend_txt}\n原因：{reason}")
            global_pos_list.append(f"({pos_dot} {flag} {icao}) {target}N **{intent}** (胜率:{win_pct}% {arrow}) {trend_txt}\n理由：{reason}")
        
        pos_display = "\n".join(pos_lines)
        if "🔴" in airport_dots: main_dot = "🔴"
        else: main_dot = "🟢"

    report = (f"----------------------\n{main_dot} {flag} **{icao} ({airport_metadata[icao]['name']})**\n"
              f"**【盘口状态】** 当地 {local_dt.strftime('%H:%M')} | 实时 {curr_t}°C | 斜率: {slope_str}\n"
              f"**【环境前瞻】** 云量: {cloud}% | 辐射: {rad}W/m² | 风速: 同步中\n"
              f"**【持仓监控】**\n{pos_display}\n"
              f"👓 **【预测矩阵】**: (G:{g_max} I:{m_max} E:{e_max}) | 基准: {round(tp_peak,1)}°C{model_warn}\n" + "\n".join(matrix_lines) + "\n")
    return report, global_pos_list

def auto_broadcast_loop():
    while True:
        try:
            if watchlist:
                reports, all_pos = [], []
                for icao in watchlist:
                    txt, pos_sums = build_single_report(icao)
                    reports.append(txt)
                    all_pos.extend(pos_sums)
                
                header = f"📢 **【定时播报 · 战时雷达】**\n周期: {BROADCAST_INTERVAL//60}min\n\n"
                footer = "----------------------\n🔥 **【全仓风险汇总】:**\n" + ("\n".join(all_pos) if all_pos else "-- 无持仓")
                send_chunked_message(MY_USER_ID, header, reports, footer)
            time.sleep(BROADCAST_INTERVAL)
        except Exception as e:
            time.sleep(60)

# ==========================================
# 4. 指令执行区 (状态驻留批量解析引擎)
# ==========================================
def send_chunked_message(chat_id, header, reports, footer):
    msg = header
    for r in reports:
        if len(msg) + len(r) > 3800:
            bot.send_message(chat_id, msg, parse_mode="Markdown")
            msg = ""
        msg += r
    if footer: msg += footer
    if msg.strip(): bot.send_message(chat_id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(message.chat.id, "✅ 交易雷达 V15.7 状态驻留版上线。\n支持指令：\n/pos ZUCK 30N 防守 ZHHH 31N 突破\n/delpos ZHHH 31N (精准删除)\n/now")

@bot.message_handler(commands=['watch', 'unwatch'])
def cmd_watch(message):
    parts = [p.upper() for p in message.text.split()[1:] if len(p) == 4]
    if not parts: return bot.send_message(message.chat.id, "⚠️ 请输入代码，例: /watch RKPK")
    if '/watch' in message.text:
        added = [i for i in parts if i in airport_metadata and i not in watchlist]
        for i in added: watchlist.append(i)
        bot.send_message(message.chat.id, f"✅ 已添加: {', '.join(added)}")
    else:
        for i in parts:
            if i in watchlist: watchlist.remove(i)
        bot.send_message(message.chat.id, f"✅ 已移除: {', '.join(parts)}")

@bot.message_handler(commands=['now'])
def cmd_now(message):
    icaos = [i.upper() for i in message.text.split()[1:] if len(i)==4]
    target_list = icaos if icaos else watchlist
    if not target_list: return bot.send_message(message.chat.id, "⚠️ 无监控目标")
    
    bot.send_message(message.chat.id, "⏳ 执行物理审计...")
    reports, all_pos = [], []
    for icao in target_list:
        txt, pos_sums = build_single_report(icao)
        reports.append(txt); all_pos.extend(pos_sums)
    
    footer = "----------------------\n🔥 **【全仓风险汇总】 (指令执行板):**\n" + ("\n".join(all_pos) if all_pos else "-- 无持仓")
    send_chunked_message(message.chat.id, f"📊 **【全量仓位汇总 · 战时全数据版】**\n*更新时间: {datetime.now().strftime('%H:%M')}*\n\n", reports, footer)

@bot.message_handler(commands=['pos', 'POS', 'Pos'])
def cmd_pos(message):
    parts = message.text.split()[1:]
    if not parts: return bot.send_message(message.chat.id, "⚠️ 格式: /pos ZHHH 31N 防守 32N 突破")
    
    current_icao = None
    added = []
    i = 0
    while i < len(parts):
        p = parts[i].upper()
        if len(p) == 4 and p.isalpha():
            current_icao = p
            i += 1
        else:
            if current_icao and i + 1 < len(parts):
                target = p.replace('N', '')
                intent = parts[i+1].upper()
                if intent in ['突破', '防守', 'YES']:
                    if current_icao not in positions: positions[current_icao] = []
                    positions[current_icao].append((target, 'N', intent))
                    if current_icao not in watchlist: watchlist.append(current_icao)
                    added.append(f"{current_icao} {target}N {intent}")
                    i += 2
                else:
                    i += 1
            else:
                i += 1
                
    if added: bot.send_message(message.chat.id, "✅ 仓位批量录入:\n" + "\n".join(added))
    else: bot.send_message(message.chat.id, "⚠️ 未识别到仓位参数")

@bot.message_handler(commands=['delpos', 'DELPOS', 'Delpos'])
def cmd_delpos(message):
    parts = message.text.split()[1:]
    if not parts: return bot.send_message(message.chat.id, "⚠️ 格式: /delpos ZHHH (删全站) 或 /delpos ZHHH 31N 防守")
    
    current_icao = None
    removed = []
    i = 0
    while i < len(parts):
        p = parts[i].upper()
        if len(p) == 4 and p.isalpha():
            current_icao = p
            if i + 1 == len(parts) or (len(parts[i+1]) == 4 and parts[i+1].isalpha()):
                if current_icao in positions:
                    del positions[current_icao]
                    removed.append(f"{current_icao} (清空全部)")
                i += 1
            else:
                i += 1
        else:
            if current_icao and current_icao in positions:
                target = p.replace('N', '')
                if i + 1 < len(parts) and parts[i+1].upper() in ['突破', '防守', 'YES']:
                    intent = parts[i+1].upper()
                    positions[current_icao] = [x for x in positions[current_icao] if not (x[0]==target and x[2]==intent)]
                    removed.append(f"{current_icao} {target}N {intent}")
                    i += 2
                else:
                    positions[current_icao] = [x for x in positions[current_icao] if x[0]!=target]
                    removed.append(f"{current_icao} {target}N (全向)")
                    i += 1
            else:
                i += 1
                
    for icao in list(positions.keys()):
        if not positions[icao]: del positions[icao]

    if removed: bot.send_message(message.chat.id, "🗑️ 仓位已删除:\n" + "\n".join(removed))
    else: bot.send_message(message.chat.id, "⚠️ 未找到对应仓位或参数缺失")

if __name__ == "__main__":
    broadcast_thread = threading.Thread(target=auto_broadcast_loop, daemon=True)
    broadcast_thread.start()
    bot.infinity_polling()
