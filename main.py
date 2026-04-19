import time
import requests
import telebot
import os
import math
import threading
import statistics 
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 配置中心 (全量字典与环境变量)
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
MY_USER_ID = 6822447850
CHECKWX_API_KEY = os.getenv('CHECKWX_API_KEY')

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)

watchlist, positions, temp_memory, prob_memory, slope_memory = [], {}, {}, {}, {}

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
# 2. 数学模型 (V15.2 修正版：突破立马结算逻辑)
# ==========================================
def phi(z): return (1.0 + math.erf(z / math.sqrt(2.0))) / 2.0

def calculate_precise_win_rate(target, t_peak, curr_t, hour, intent):
    """
    逻辑点：No 筹码胜率计算
    突破：旨在跳过当前坑位到达 Target+1.0，触达即 100%
    防守：旨在维持在 Target 以下，触达 Target 即归零 (但在 V14.7 修正中，进坑仍有反杀机会)
    """
    try:
        t_f = float(target)
        sigma = 0.8 if hour < 15 else 0.35
        
        # 17:00 结算逻辑
        if hour >= 17:
            is_win = curr_t < t_f or curr_t >= t_f + 1.0
            return 100.0 if is_win else 0.0
        
        # 物理实时立马结算：突破位跳过 Target+1 即反杀成功
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
# 3. 报告引擎 (V15.2 深度审计全流程)
# ==========================================
def build_single_report(icao):
    icao = icao.upper()
    if icao not in airport_metadata: return f"❌ {icao} 未录入\n\n", []
    lat, lon = airport_metadata[icao]['coords']
    
    # METAR 抓取
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers={"X-API-Key": CHECKWX_API_KEY}, timeout=5).json()
        metar = r['data'][0]
        curr_t = metar.get('temperature', {}).get('celsius', "N/A")
        obs_str = metar.get('observed')
        if obs_str: metar['observed_unix'] = datetime.strptime(obs_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except: return f"❌ {icao} METAR数据获取失败\n\n", []

    # Open-Meteo 多模型预测抓取
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

    # 模型偏差提示
    valid_f = [v for v in [g_max, m_max, e_max] if isinstance(v, (int, float))]
    model_warn = ""
    if len(valid_f) >= 2:
        gap = max(valid_f) - min(valid_f)
        if gap >= 3.5: model_warn = f" ⚠️ **[模型偏差: {round(gap,1)}°C]**"
    
    base_f = statistics.median(valid_f) if valid_f else (curr_t if isinstance(curr_t, (int,float)) else 25.0)
    tp_peak = max(curr_t, base_f) if isinstance(curr_t, (int,float)) else base_f
    
    local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
    h = local_dt.hour
    slope_str, m_val = calc_ls_slope(icao, curr_t, metar)

    # 👓 预测矩阵推演
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

    # 💼 持仓监控与理由审计
    pos_display, global_pos_list = "-- (空仓观望)", []
    cc = airport_metadata[icao]['cc']
    flag = chr(ord(cc[0])+127397)+chr(ord(cc[1])+127397)
    
    if icao in positions:
        pos_lines = []
        for target, _, intent in positions[icao]:
            win_pct = calculate_precise_win_rate(target, tp_peak, curr_t, h, intent)
            mem_key = f"{icao}_{target}_{intent}"
            last_p = prob_memory.get(mem_key, win_pct)
            arrow = "↑" if win_pct > last_p else ("↓" if win_pct < last_p else "-")
            trend_txt = "[胜率提升📈]" if arrow == "↑" else "[胜率下降📉]"
            prob_memory[mem_key] = win_pct
            
            # 理由审计同步
            if intent == "突破":
                reason = f"辐射 {rad}W/m² 支撑斜率 {slope_str}，冲刺 {float(target)+1.0} 结算点" if arrow == "↑" else f"云量 {cloud}% 导致动能流失，进坑流血中"
            else:
                reason = f"云量 {cloud}% 动能封顶，{target}.0 防线稳固" if arrow == "↑" else f"物理斜率 {slope_str} 异常，防线受压"
            
            # 25.0 进坑修正
            def_val = 0.0 if intent == "突破" and isinstance(curr_t, (int,float)) and curr_t >= float(target) else win_pct
            
            pos_lines.append(f"({target}N | {intent}:{win_pct}% {arrow} | 防守:{def_val}% - | YES区:{round(100-win_pct,1)}% ↓) {'🚀 [突破套利]' if intent=='突破' else '🛡️ [防守中]'} {trend_txt}\n原因：{reason}")
            global_pos_list.append(f"({flag} {icao}) {target}N **{intent}** (胜率:{win_pct}% {arrow}) {trend_txt}\n理由：{reason}")
        pos_display = "\n".join(pos_lines)

    report = (f"{flag} **{icao} ({airport_metadata[icao]['name']})**\n"
              f"**【盘口状态】** 当地 {local_dt.strftime('%H:%M')} | 实时 {curr_t}°C | 斜率: {slope_str}\n"
              f"**【环境前瞻】** 云量: {cloud}% | 辐射: {rad}W/m² | 风速: 同步中\n"
              f"**【持仓监控】**\n{pos_display}\n"
              f"👓 **【预测矩阵】**: (G:{g_max} I:{m_max} E:{e_max}) | 基准: {round(tp_peak,1)}°C{model_warn}\n" + "\n".join(matrix_lines) + "\n----------------------\n")
    return report, global_pos_list

# ==========================================
# 4. 指令执行区
# ==========================================
def send_chunked_message(header, reports, footer):
    msg = header
    for r in reports:
        if len(msg) + len(r) > 3800:
            bot.send_message(MY_USER_ID, msg, parse_mode="Markdown")
            msg = ""
        msg += r
    if footer: msg += footer
    if msg.strip(): bot.send_message(MY_USER_ID, msg, parse_mode="Markdown")

@bot.message_handler(commands=['now'])
def cmd_now(message):
    icaos = [i.upper() for i in message.text.split()[1:] if len(i)==4]
    target_list = icaos if icaos else watchlist
    if not target_list: return bot.send_message(MY_USER_ID, "⚠️ 列表为空")
    reports, all_pos = [], []
    for icao in target_list:
        txt, pos_sums = build_single_report(icao)
        reports.append(txt); all_pos.extend(pos_sums)
    footer = "🔥 **【全仓风险汇总】 (指令执行板):**\n" + ("\n".join(all_pos) if all_pos else "-- 无持仓")
    send_chunked_message(f"=== 全球高频雷达 ({datetime.now().strftime('%H:%M')}) ===\n\n", reports, footer)

@bot.message_handler(commands=['pos'])
def cmd_pos(message):
    parts = message.text.split()[1:]
    if len(parts) >= 3:
        icao = parts[0].upper(); target = parts[1].replace('N',''); intent = parts[2]
        if icao not in positions: positions[icao] = []
        positions[icao].append((target, 'N', intent))
        if icao not in watchlist: watchlist.append(icao)
        bot.send_message(MY_USER_ID, f"✅ {icao} {target}N {intent} 已录入")

@bot.message_handler(commands=['delpos'])
def cmd_delpos(message):
    parts = message.text.split()[1:]
    if parts:
        icao = parts[0].upper()
        if icao in positions:
            del positions[icao]
            bot.send_message(MY_USER_ID, f"✅ {icao} 仓位已清空")

if __name__ == "__main__":
    # 启动自动广播线程 (可选)
    print("🚀 交易雷达 V15.2 全量锁死版已启动...")
    bot.infinity_polling()
