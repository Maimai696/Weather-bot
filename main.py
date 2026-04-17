import time
import requests
import telebot
import os
import math
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 配置中心 (请确保 Zeabur 环境变量已填入)
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
MY_USER_ID = 6822447850
CHECKWX_API_KEY = os.getenv('CHECKWX_API_KEY')

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
watchlist, positions, temp_memory = [], {}, {}

# === 20 个全球主力机场元数据 ===
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
# 2. 核心数学与物理引擎区
# ==========================================
def phi(z): return (1.0 + math.erf(z / math.sqrt(2.0))) / 2.0

def calculate_precise_win_rate(target, t_peak, hour, intent):
    """强同步概率引擎"""
    try:
        sigma = 0.8 if hour < 15 else 0.35
        z = (float(target) - t_peak) / sigma
        p = (phi(z) if intent == "防守" else (1.0 - phi(z))) * 100
        return round(p, 1)
    except: return 50.0

def calc_ls_slope(icao, curr_t, hour, metar):
    """对齐 METAR 观测时间的最小二乘法斜率"""
    raw_obs = metar.get('observed_unix') or metar.get('obsTime')
    obs_dt = datetime.fromtimestamp(raw_obs, tz=timezone.utc) if isinstance(raw_obs, (int,float)) else datetime.now(timezone.utc)
    if icao not in temp_memory: temp_memory[icao] = []
    if not any(abs((obs_dt - d['time']).total_seconds()) < 60 for d in temp_memory[icao]):
        temp_memory[icao].append({'temp': curr_t, 'time': obs_dt})
    data = [d for d in temp_memory[icao] if (datetime.now(timezone.utc) - d['time']).total_seconds() <= 9000]
    if len(data) < 2: return "同步中", 0.0
    t_v, T_v = [(d['time'] - data[0]['time']).total_seconds()/3600.0 for d in data], [d['temp'] for d in data]
    n = len(data); denom = (n * sum(x**2 for x in t_v)) - (sum(t_v)**2)
    m = 0.0 if denom == 0 else ((n * sum(x*y for x,y in zip(t_v, T_v))) - (sum(t_v)*sum(T_v))) / denom
    m *= (1.1 if 8 <= hour < 14 else 0.8)
    return (f"↑ {abs(round(m,2))}" if m > 0 else f"↓ {abs(round(m,2))}"), round(m, 2)

# ==========================================
# 3. 报告生成与热力平衡逻辑
# ==========================================
def build_single_report(icao):
    icao = icao.upper()
    if icao not in airport_metadata: return f"❌ {icao} 未录入\n\n", []
    
    # 获取实时 METAR
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers={"X-API-Key": CHECKWX_API_KEY}, timeout=5).json()
        metar_data = r['data'][0]; curr_t = metar_data.get('temperature', {}).get('celsius', "N/A")
        obs_str = metar_data.get('observed')
        if obs_str: metar_data['observed_unix'] = datetime.strptime(obs_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except: return f"❌ {icao} 接口异常\n\n", []

    # 获取超算预测
    lat, lon = airport_metadata[icao]['coords']
    g_max, m_max, e_max, offset = "N/A", "N/A", "N/A", 0
    try:
        f = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m&models=dwd_icon,ecmwf_ifs,gfs_seamless&forecast_days=3&timezone=auto", timeout=10).json()
        offset = f.get('utc_offset_seconds', 0)
        def gm(k): ts = [v for v in f.get('hourly',{}).get(k,[])[:24] if isinstance(v,(int,float))]; return round(max(ts),1) if ts else "N/A"
        g_max, m_max, e_max = gm('temperature_2m_gfs_seamless'), gm('temperature_2m_dwd_icon'), gm('temperature_2m_ecmwf_ifs')
    except: pass

    # --- 核心：热力平衡推演锚点 (V8.7) ---
    local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
    current_hour = local_dt.hour
    slope_str, m_val = calc_ls_slope(icao, curr_t, current_hour, metar_data)
    
    valid_f = [v for v in [g_max, m_max, e_max] if isinstance(v, (int, float))]
    base_forecast = max(valid_f) if valid_f else curr_t
    
    # 引入平方根衰减衰减修正
    hours_left = max(0, 14 - current_hour)
    decay_modifier = math.sqrt(hours_left) if hours_left > 0 else 0
    momentum_forecast = curr_t + (m_val * decay_modifier)
    
    # 最终锚点同步：取基准值与动能值的融合，加绝对物理压制 (+2.5°C)
    tp_peak = max(base_forecast, min(momentum_forecast, base_forecast + 2.5))

    # 生成战术矩阵
    matrix_lines = []
    base_level = math.floor(tp_peak) - 1
    for i in range(4):
        lvl = base_level + i
        p_brk = calculate_precise_win_rate(lvl, tp_peak, current_hour, "突破")
        matrix_lines.append(f"{i+1}️⃣ {lvl}N: 突破:{p_brk}% | 防守:{round(100-p_brk,1)}%")
    market_matrix = "📊 战术矩阵:\n" + "\n".join(matrix_lines)

    # 扫描风险与仓位
    icao_risks, pos_list, current_icon = [], [], "⚪"
    priority_map = {"⚪": 0, "🟢": 1, "🛡️": 2, "🎯": 3, "🔴": 4, "💰": 5}
    
    if icao in positions:
        current_icon = "🟢"
        for target, direction, intent in positions[icao]:
            win_pct = calculate_precise_win_rate(target, tp_peak, current_hour, intent)
            target_f = float(target); dist = abs(target_f - curr_t)
            this_icon = "🟢"
            
            if intent == "突破":
                if curr_t >= target_f + 0.1: this_icon = "💰"; icao_risks.append(f"💰 **{icao} | {target}N 击穿** (已获利)")
                elif dist <= 1.1: this_icon = "🎯"; icao_risks.append(f"🎯 **{icao} | {target}N 临界** (胜算:{win_pct}% 距{round(dist,1)}°)")
                elif win_pct < 40: icao_risks.append(f"🚨 **{icao} | {target}N 风险** (动能不足胜算:{win_pct}%)")
            else: # 防守
                if curr_t >= target_f - 0.5: this_icon = "🔴"; icao_risks.append(f"🚨 **{icao} | {target}N 危急** (距防线{round(dist,1)}°)")
                elif dist >= 1.5 and (m_val <= -0.1 or current_hour >= 17): this_icon = "🛡️"
            
            if priority_map[this_icon] > priority_map[current_icon]: current_icon = this_icon
            pos_list.append(f"• {target}N({intent}:{win_pct}%)")

    # 组合报表
    intensity = "同步中" if slope_str == "同步中" else ("稳步" if abs(m_val) >= 0.3 else "滞涨")
    cc = airport_metadata[icao]['cc']; flag = chr(ord(cc[0])+127397)+chr(ord(cc[1])+127397) if cc!='UN' else "🏳️"
    
    report = (f"{current_icon} {flag} {icao} ({airport_metadata[icao]['name']})\n"
              f"当地: {local_dt.strftime('%H:%M')} | 实时: {curr_t}°C [{intensity}]\n"
              f"斜率: {slope_str} | 🎯 仓位: {', '.join(pos_list) if pos_list else '--'}\n"
              f"预测: G:{g_max} I:{m_max} E:{e_max}\n"
              f"{market_matrix}\n----------------------\n")
    return report, icao_risks

# ==========================================
# 4. 指令执行与全局看板
# ==========================================
@bot.message_handler(commands=['now', 'watch'])
def handle_cmd(message):
    icaos = [i.upper() for i in message.text.split()[1:] if len(i)==4] if '/watch' in message.text else watchlist
    if '/watch' in message.text: 
        for i in icaos: 
            if i not in watchlist: watchlist.append(i)
    if not icaos: return
    
    main_text, global_risks = "", []
    for icao in icaos:
        txt, risks = build_single_report(icao)
        main_text += txt
        global_risks.extend(risks)
    
    risk_footer = ""
    if global_risks:
        risk_footer = "\n⚠️ **综合风险看板 (全域扫描):**\n"
        for idx, r in enumerate(global_risks, 1):
            risk_footer += f"{idx}️⃣ {r}\n"
        risk_footer += "=========================="
    
    bot.send_message(MY_USER_ID, f"=== 全球实时雷达 ({datetime.now().strftime('%H:%M')}) ===\n\n{main_text}{risk_footer}", parse_mode="Markdown")

@bot.message_handler(commands=['pos'])
def cmd_pos(message):
    parts = message.text.split()[1:]; cur = None; i = 0
    while i < len(parts):
        p = parts[i].upper()
        if len(p) == 4: cur = p; positions[cur] = []; i += 1
        elif cur and 'N' in p:
            val = "".join(filter(lambda x: x.isdigit() or x=='.', p))
            intent = parts[i+1] if i+1 < len(parts) and parts[i+1] in ["防守", "突破"] else "防守"
            positions[cur].append((val, 'N', intent)); i += 2
        else: i += 1
    handle_cmd(message)

if __name__ == "__main__":
    print("🚀 交易雷达 V8.7 (热力平衡最终版) 启动..."); bot.infinity_polling()
