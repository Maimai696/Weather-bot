import time
import requests
import telebot
import os
import math
import threading
import statistics 
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 配置中心
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
# 2. 核心数学模型
# ==========================================
def phi(z): return (1.0 + math.erf(z / math.sqrt(2.0))) / 2.0

def calculate_precise_win_rate(target, t_peak, curr_t, hour, intent):
    try:
        target_f = float(target)
        sigma = 0.8 if hour < 15 else 0.35
        # 17:00 结算边界保护
        if hour >= 17 and isinstance(curr_t, (int, float)):
            if intent == "突破":
                return 100.0 if curr_t >= target_f + 1.0 else 0.0
            elif intent == "防守":
                return 100.0 if curr_t < target_f else 0.0
            elif intent == "YES":
                return 100.0 if target_f <= curr_t < target_f + 1.0 else 0.0

        if intent == "防守": return round(phi((target_f - t_peak) / sigma) * 100, 1)
        elif intent == "突破": return round((1.0 - phi(((target_f + 1.0) - t_peak) / sigma)) * 100, 1)
        elif intent == "YES":
            z1, z2 = (target_f - t_peak) / sigma, ((target_f + 1.0) - t_peak) / sigma
            return round((phi(z2) - phi(z1)) * 100, 1)
    except: return 0.0

def calc_ls_slope(icao, curr_t, metar):
    if not isinstance(curr_t, (int, float)): return "同步中", 0.0
    if icao not in temp_memory: temp_memory[icao] = []
    obs_unix = metar.get('observed_unix') or time.time()
    obs_dt = datetime.fromtimestamp(obs_unix, tz=timezone.utc)
    if not any(abs((obs_dt - d['time']).total_seconds()) < 60 for d in temp_memory[icao]):
        temp_memory[icao].append({'temp': curr_t, 'time': obs_dt})
    # 保留最近2小时数据用于回归
    data = [d for d in temp_memory[icao] if (datetime.now(timezone.utc) - d['time']).total_seconds() <= 7200]
    temp_memory[icao] = data
    if len(data) < 2: return "同步中", 0.0
    t_v = [(d['time'] - data[0]['time']).total_seconds()/3600.0 for d in data]
    T_v = [d['temp'] for d in data]
    n = len(data); denom = (n * sum(x**2 for x in t_v)) - (sum(t_v)**2)
    m = 0.0 if denom == 0 else ((n * sum(x*y for x,y in zip(t_v, T_v))) - (sum(t_v)*sum(T_v))) / denom
    return (f"↑{abs(round(m,2))}" if m > 0 else f"↓{abs(round(m,2))}"), round(m, 2)

# ==========================================
# 3. 报告引擎 (V11.1)
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

    # 宏观超算 + 物理先行指标
    g_max, m_max, e_max, cloud, wind_deg, offset = "N/A", "N/A", "N/A", 0, 0, 0
    try:
        f = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,cloudcover,winddirection_10m&models=dwd_icon,ecmwf_ifs,gfs_seamless&forecast_days=1&timezone=auto", timeout=10).json()
        offset = f.get('utc_offset_seconds', 0)
        def gm(k): ts = [v for v in f.get('hourly',{}).get(k,[])[:24] if isinstance(v,(int,float))]; return round(max(ts),1) if ts else "N/A"
        g_max, m_max, e_max = gm('temperature_2m_gfs_seamless'), gm('temperature_2m_dwd_icon'), gm('temperature_2m_ecmwf_ifs')
        
        local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
        h_idx = min(23, max(0, local_dt.hour))
        cloud = f.get('hourly', {}).get('cloudcover', [0]*24)[h_idx]
        wind_deg = f.get('hourly', {}).get('winddirection_10m', [0]*24)[h_idx]
    except: pass

    local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
    slope_str, m_val = calc_ls_slope(icao, curr_t, metar)
    
    slope_trend = ""
    if slope_str != "同步中":
        prev_m = slope_memory.get(icao)
        if prev_m is not None:
            diff = round(m_val - prev_m, 2)
            slope_trend = f" **(↗{abs(diff)})**" if diff > 0 else (f" **(↘{abs(diff)})**" if diff < 0 else " **(-)**")
        slope_memory[icao] = m_val

    # 模型分歧监控
    valid_f = [v for v in [g_max, m_max, e_max] if isinstance(v, (int, float))]
    model_warn = ""
    if len(valid_f) >= 2:
        gap = max(valid_f) - min(valid_f)
        if gap >= 4.0: model_warn = f" ⚠️ **[模型偏差: {round(gap,1)}°C]**"

    if valid_f:
        base_f = statistics.median(valid_f)
        if not model_warn and isinstance(e_max, (int,float)) and abs(base_f - e_max) > 1.0:
            base_f = (base_f * 0.4) + (e_max * 0.6)
    else: base_f = curr_t if isinstance(curr_t, (int,float)) else 25.0

    # 环境物理截断
    env_warn = ""
    if icao in ["RKSI", "RKPK", "RJTT", "KMIA", "EGLC"] and (240 <= wind_deg <= 320): env_warn = " 👉 ⚠️ **[海风锋面介入]**"
    if cloud > 70: env_warn = " 👉 ⚠️ **[强云层遮蔽]**"

    tp_peak = max(curr_t, base_f) if isinstance(curr_t, (int,float)) else base_f

    # 👓 预测矩阵推演
    matrix_lines = []
    base_lvl = math.floor(tp_peak) - 1
    rem_h = max(0.5, 17 - local_dt.hour) if local_dt.hour < 17 else 0.1
    for i in range(1, 5):
        lvl = base_lvl + i
        v_req = round((float(lvl) + 1.0 - curr_t) / rem_h, 2) if isinstance(curr_t,(int,float)) else 0.0
        p_brk = calculate_precise_win_rate(lvl, tp_peak, curr_t, local_dt.hour, "突破")
        p_def = calculate_precise_win_rate(lvl, tp_peak, curr_t, local_dt.hour, "防守")
        p_yes = calculate_precise_win_rate(lvl, tp_peak, curr_t, local_dt.hour, "YES")
        
        diag = ""
        if local_dt.hour >= 17: diag = " 👉 [已结算]"
        elif v_req > m_val + 0.5: diag = f" 👉 ❌ **[物理死亡: 缺口{round(v_req-m_val,2)}过大]**"
        elif m_val > v_req + 0.3 and p_def > 60: diag = f" 👉 ✅ **[防守套利: 动能不足以攀爬]**"
        elif cloud > 70 and p_brk > 40: diag = f" 👉 ⚠️ **[虚假繁荣: 云层遮蔽{cloud}%]**"
        
        matrix_lines.append(f"({i}) {lvl}N [需↑{v_req if local_dt.hour<17 else 0}|实{slope_str}] 破:{p_brk}%|守:{p_def}%|YES:{p_yes}%{diag}")

    cc = airport_metadata[icao]['cc']
    flag = chr(ord(cc[0])+127397)+chr(ord(cc[1])+127397) if cc!='UN' else "🏳️"
    
    # 💼 持仓监控
    pos_display, global_pos_list = "-- (空仓观望)", []
    if icao in positions:
        pos_lines = []
        for idx, (target, _, intent) in enumerate(positions[icao], 1):
            win_pct = calculate_precise_win_rate(target, tp_peak, curr_t, local_dt.hour, intent)
            mem_key = f"{icao}_{target}_{intent}"
            diff_str = ""
            if mem_key in prob_memory:
                d = round(win_pct - prob_memory[mem_key], 1)
                diff_str = f" (**{'↑' if d>0 else '↓'}{abs(d)}%**)" if d != 0 else " (**-**)"
            prob_memory[mem_key] = win_pct
            
            target_f = float(target)
            tag = ""
            if intent == "突破":
                v_need = round((target_f + 1.0 - curr_t) / rem_h, 2) if isinstance(curr_t, (int,float)) else 0
                if isinstance(curr_t,(int,float)) and curr_t >= target_f + 1.0: tag = " 👉 ✅ **[稳拿: 已击穿目标]**"
                elif local_dt.hour < 17 and v_need > m_val + 0.6: tag = f" 👉 ❌ **[物理死亡: 缺口{round(v_need-m_val,2)}]**"
                elif abs(target_f + 1.0 - curr_t) < 1.0: tag = " 👉 🎯 **[临界击穿]**"
            elif intent == "防守":
                if isinstance(curr_t,(int,float)) and curr_t < target_f - 0.5: tag = " 👉 ✅ **[套利稳拿: 防线稳固]**"
            
            pos_lines.append(f"({idx}) • {target}N {intent} | 胜率: {win_pct}%{diff_str}{tag}")
            global_pos_list.append(f"({idx}) {flag} {icao} | {target}N {intent} (胜率:{win_pct}%{diff_str}){tag}")
        pos_display = "\n".join(pos_lines)

    report = (f"{flag} **{icao} ({airport_metadata[icao]['name']})**\n"
              f"**【盘口状态】** 当地 {local_dt.strftime('%H:%M')} | 实时 {curr_t}°C | 斜率: {slope_str}{slope_trend}\n"
              f"**【环境前瞻】** 云量: {cloud}% | 风向: {wind_deg}°{env_warn}\n"
              f"**【持仓监控】**\n{pos_display}\n"
              f"👓 **【预测矩阵】**: (G:{g_max} I:{m_max} E:{e_max}) | 基准: {round(base_f,1)}°C{model_warn}\n" + 
              "\n".join(matrix_lines) + "\n----------------------\n")
    return report, global_pos_list

def send_chunked_message(header, reports, footer):
    msg = header
    for r in reports:
        if len(msg) + len(r) > 3800:
            bot.send_message(MY_USER_ID, msg, parse_mode="Markdown")
            msg = ""
        msg += r
    if footer: msg += footer
    if msg.strip(): bot.send_message(MY_USER_ID, msg, parse_mode="Markdown")

def auto_broadcast():
    while True:
        try:
            if watchlist:
                reports, all_pos = [], []
                for icao in watchlist:
                    txt, pos_sums = build_single_report(icao)
                    reports.append(txt); all_pos.extend(pos_sums)
                header = f"=== 自动监控预警 ({datetime.now().strftime('%H:%M')}) ===\n\n"
                footer = "\n🚨 **【全仓风控汇总】 (指令执行板):**\n" + ("\n".join(all_pos) if all_pos else "-- 无持仓")
                send_chunked_message(header, reports, footer)
            time.sleep(1800)
        except: time.sleep(60)

# ==========================================
# 4. 指令执行区
# ==========================================
@bot.message_handler(commands=['now'])
def cmd_now(message):
    icaos = [i.upper() for i in message.text.split()[1:] if len(i)==4]
    target_list = icaos if icaos else watchlist
    if not target_list: return bot.send_message(MY_USER_ID, "⚠️ 列表为空")
    reports, all_pos = [], []
    for icao in target_list:
        txt, pos_sums = build_single_report(icao)
        reports.append(txt); all_pos.extend(pos_sums)
    footer = "\n🚨 **【全仓风控汇总】 (指令执行板):**\n" + ("\n".join(all_pos) if all_pos else "-- 无持仓")
    send_chunked_message(f"=== 全球高频雷达 ({datetime.now().strftime('%H:%M')}) ===\n\n", reports, footer)

@bot.message_handler(commands=['watch', 'unwatch'])
def cmd_watch(message):
    parts = [p.upper() for p in message.text.split()[1:] if len(p) == 4]
    if '/watch' in message.text:
        for i in parts: 
            if i in airport_metadata and i not in watchlist: watchlist.append(i)
    else:
        for i in parts:
            if i in watchlist:
                watchlist.remove(i)
                if i in temp_memory: del temp_memory[i]
                if i in slope_memory: del slope_memory[i]
    bot.send_message(MY_USER_ID, f"✅ 监控列表: {', '.join(watchlist)}")

@bot.message_handler(commands=['pos'])
def cmd_pos(message):
    parts = message.text.split()[1:]
    cur = None; i = 0
    while i < len(parts):
        p = parts[i].upper()
        if len(p) == 4 and p in airport_metadata:
            cur = p
            if cur not in positions: positions[cur] = []
            if cur not in watchlist: watchlist.append(cur)
            i += 1
        elif cur and 'N' in p:
            val = "".join(filter(lambda x: x.isdigit() or x=='.', p))
            intent = parts[i+1] if i+1 < len(parts) and parts[i+1] in ["防守", "突破"] else "防守"
            positions[cur].append((val, 'N', intent))
            i += 2
        else: i += 1
    bot.send_message(MY_USER_ID, "✅ 仓位已记录")

@bot.message_handler(commands=['delpos'])
def cmd_delpos(message):
    parts = message.text.split()[1:]; icao = parts[0].upper() if parts else ""
    if icao in positions:
        if len(parts) == 1: del positions[icao]
        else:
            t = "".join(filter(lambda x: x.isdigit() or x=='.', parts[1].upper()))
            positions[icao] = [p for p in positions[icao] if p[0] != t]
            if not positions[icao]: del positions[icao]
        bot.send_message(MY_USER_ID, f"✅ {icao} 仓位已清理")

if __name__ == "__main__":
    threading.Thread(target=auto_broadcast, daemon=True).start()
    print("🚀 交易雷达 V11.1 逻辑闭环版已启动...")
    bot.infinity_polling()
