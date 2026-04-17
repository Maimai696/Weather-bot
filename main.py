import time
import requests
import telebot
import os
import math
import queue
from threading import Thread
from datetime import datetime, timedelta, timezone

# --- 1. 配置与本地核心字典 ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MY_USER_ID = 6822447850
CHECKWX_API_KEY = os.getenv('CHECKWX_API_KEY')

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=20)

watchlist = [] 
positions = {} 
temp_memory = {} 
BROADCAST_INTERVAL = 30 
recovery_queue = queue.Queue() 

BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"}
CHECKWX_HEADERS = {"X-API-Key": CHECKWX_API_KEY, "User-Agent": BROWSER_HEADERS["User-Agent"]}

airport_metadata = {
    "LTAC": {"name": "TR-Ankara", "coords": (40.1281, 32.9951), "cc": "TR"},
    "LIMC": {"name": "IT-Milan", "coords": (45.6306, 8.7281), "cc": "IT"},
    "EPWA": {"name": "PL-Warsaw", "coords": (52.1657, 20.9671), "cc": "PL"},
    "EDDM": {"name": "DE-Munich", "coords": (48.3538, 11.7861), "cc": "DE"},
    "LEMD": {"name": "ES-Madrid", "coords": (40.4719, -3.5626), "cc": "ES"},
    "LFPG": {"name": "FR-Paris", "coords": (49.0128, 2.55), "cc": "FR"},
    "EGLC": {"name": "GB-London", "coords": (51.5053, 0.0553), "cc": "GB"},
    "DNMM": {"name": "NG-Lagos", "coords": (6.5774, 3.3215), "cc": "NG"},
    "ZSPD": {"name": "CN-Shanghai", "coords": (31.1434, 121.8052), "cc": "CN"},
    "ZHHH": {"name": "CN-Wuhan", "coords": (30.7838, 114.2081), "cc": "CN"},
    "KMIA": {"name": "US-Miami", "coords": (25.7932, -80.2906), "cc": "US"},
    "SAEZ": {"name": "AR-Buenos Aires", "coords": (-34.8222, -58.5358), "cc": "AR"},
    "SBGR": {"name": "BR-Sao Paulo", "coords": (-23.4356, -46.4731), "cc": "BR"},
    "RJTT": {"name": "JP-Tokyo", "coords": (35.5522, 139.7796), "cc": "JP"},
    "WSSS": {"name": "SG-Singapore", "coords": (1.3644, 103.9915), "cc": "SG"}
}

# --- 2. 后台复苏 (NOAA 官方版) ---
def recovery_worker():
    while True:
        icao = recovery_queue.get()
        try:
            url = f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json&hours=3"
            res = requests.get(url, headers=BROWSER_HEADERS, timeout=10).json()
            if res and isinstance(res, list):
                if icao not in temp_memory: temp_memory[icao] = []
                for item in res:
                    try:
                        obs_raw, temp_c = item.get('obsTime'), item.get('temp')
                        if obs_raw and temp_c is not None:
                            # 🌟 物理时间点记录
                            obs_dt = datetime.fromtimestamp(obs_raw, tz=timezone.utc) if isinstance(obs_raw, int) else datetime.strptime(obs_raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                            if not any(abs((d['time'] - obs_dt).total_seconds()) < 60 for d in temp_memory[icao]):
                                temp_memory[icao].append({'temp': temp_c, 'time': obs_dt})
                    except: pass
                temp_memory[icao] = sorted(temp_memory[icao], key=lambda x: x['time'])
                now = datetime.now(timezone.utc)
                temp_memory[icao] = [d for d in temp_memory[icao] if (now - d['time']).total_seconds() <= 3.5 * 3600]
                print(f"[{icao}] ✅ 历史物理坐标同步完成")
        except: pass
        time.sleep(2); recovery_queue.task_done()

Thread(target=recovery_worker, daemon=True).start()

# --- 3. 核心引擎：物理时间 OLS 算法 ---

def phi(z): return (1.0 + math.erf(z / math.sqrt(2.0))) / 2.0

def calculate_precise_win_rate(target, t_peak, hour, intent):
    try:
        sigma = 0.8 if hour < 15 else 0.35
        z = (float(target) - t_peak) / sigma
        return round((phi(z) if intent == "防守" else (1.0 - phi(z))) * 100, 1)
    except: return 50.0

def calc_ls_slope_v80(icao, curr_t, hour, metar_data):
    """
    🌟 V8.0 核心突破：采用报文物理观测时间，消除 API 延迟导致的斜率暴跳
    """
    # 提取物理观测时间
    raw_obs = metar_data.get('observed_unix') or metar_data.get('obsTime')
    obs_dt = datetime.fromtimestamp(raw_obs, tz=timezone.utc) if isinstance(raw_obs, (int,float)) else datetime.now(timezone.utc)

    if icao not in temp_memory: temp_memory[icao] = []
    if not any(abs((obs_dt - d['time']).total_seconds()) < 60 for d in temp_memory[icao]):
        temp_memory[icao].append({'temp': curr_t, 'time': obs_dt})
    
    now = datetime.now(timezone.utc)
    data = [d for d in temp_memory[icao] if (now - d['time']).total_seconds() <= 2.5 * 3600]
    if len(data) < 2: return "同步中", 0.0

    # 物理坐标回归分析
    t_v = [(d['time'] - data[0]['time']).total_seconds() / 3600.0 for d in data]
    T_v = [d['temp'] for d in data]
    n = len(data)
    denom = (n * sum(x**2 for x in t_v)) - (sum(t_v)**2)
    m_raw = 0.0 if denom == 0 else ((n * sum(x*y for x,y in zip(t_v, T_v))) - (sum(t_v)*sum(T_v))) / denom
    
    # 修正：移除过度的早间放大，回归自然热力学
    m_adj = m_raw * (1.1 if 8 <= hour < 14 else 0.8)
    if any(c.get('code') in ['OVC','BKN'] for c in metar_data.get('clouds', [])): m_adj *= 0.5
    
    slope_str = f"↑ {abs(round(m_adj,2))}" if m_adj > 0 else f"↓ {abs(round(m_adj,2))}"
    return slope_str, round(m_adj, 2)

# --- 4. 报告逻辑与方向觉醒 ---

def build_single_report(icao):
    icao = icao.upper()
    if icao not in airport_metadata: return f"❌ {icao} 未录入\n\n", False, icao

    curr_t, metar_data = "N/A", {}
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers=CHECKWX_HEADERS, timeout=3).json()
        metar_data = r['data'][0]; curr_t = metar_data.get('temperature', {}).get('celsius', "N/A")
        # 补全物理时间戳
        obs_str = metar_data.get('observed')
        if obs_str: 
            dt = datetime.strptime(obs_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            metar_data['observed_unix'] = dt.timestamp()
    except:
        try:
            r = requests.get(f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json", timeout=5).json()
            curr_t = r[0].get('temp', "N/A"); metar_data = {'obsTime':r[0].get('obsTime'), 'clouds':[]}
        except: pass

    lat, lon = airport_metadata[icao]['coords']
    g_max, m_max, e_max, offset = "N/A", "N/A", "N/A", 0
    try:
        f = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m&models=dwd_icon,ecmwf_ifs,gfs_seamless&forecast_days=3&timezone=auto", timeout=10).json()
        offset = f.get('utc_offset_seconds', 0)
        def gm(k): ts = [v for v in f.get('hourly',{}).get(k,[])[:24] if isinstance(v,(int,float))]; return round(max(ts),1) if ts else "N/A"
        g_max, m_max, e_max = gm('temperature_2m_gfs_seamless'), gm('temperature_2m_dwd_icon'), gm('temperature_2m_ecmwf_ifs')
    except: pass

    local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
    if not isinstance(curr_t, (int, float)): return f"⚪ {icao} | 实时: ⚠️ 断连\n\n", False, icao

    slope_str, m_val = calc_ls_slope_v80(icao, curr_t, local_dt.hour, metar_data)
    valid_f = [v for v in [g_max, m_max, e_max] if isinstance(v, (int, float))]
    tp_peak = max(valid_f + [curr_t + m_val*max(0,15-local_dt.hour)]) if valid_f else curr_t

    status_icon, is_risk, risk_info, pos_summary = "⚪", False, "", "--"
    if icao in positions:
        status_icon = "🟢"; pos_labels = []
        for target, direction, intent in positions[icao]:
            win_pct = calculate_precise_win_rate(target, tp_peak, local_dt.hour, intent)
            target_f = float(target); dist = abs(target_f - curr_t)
            
            if intent == "突破":
                if curr_t >= target_f + 0.1:
                    status_icon = "💰"; risk_info += f"\n✅ [目标击穿] 现价:{curr_t}° | 已获利"
                elif dist <= 1.1:
                    status_icon = "🎯"; is_risk = True
                    risk_info += f"\n🎯 [临界窗口] 胜算:{win_pct}% | 准备收网"
            else: # 防守
                if curr_t >= target_f - 0.5:
                    status_icon = "🔴"; is_risk = True
                    risk_info += f"\n🚨 [防线危急] 建议撤离！"
                elif dist >= 1.5 and (m_val <= -0.15 or local_dt.hour >= 17):
                    status_icon = "🛡️"; risk_info += f"\n🛡️ [防线稳固] 物理厚度形成"

            pos_labels.append(f"{target}N({intent}:{win_pct}%)")
        pos_summary = ", ".join(pos_labels)

    intensity = "同步中" if slope_str == "同步中" else ("稳步" if abs(m_val) >= 0.3 else "滞涨")
    cc = airport_metadata[icao]['cc']; flag = chr(ord(cc[0])+127397)+chr(ord(cc[1])+127397) if cc!='UN' else "🏳️"
    
    report = (f"{status_icon} {flag} {icao} ({airport_metadata[icao]['name']})\n"
              f"当地: {local_dt.strftime('%H:%M')} | 实时: {curr_t}°C [{intensity}]\n"
              f"斜率: {slope_str} | 🎯 仓位: {pos_summary}\n"
              f"预测: G:{g_max} I:{m_max} E:{e_max}\n"
              f"{risk_info}\n----------------------\n")
    return report, is_risk, icao

# --- 5. 指令与播报守护 ---

@bot.message_handler(commands=['pos'])
def cmd_pos(message):
    parts = message.text.split()[1:]; cur = None; i = 0
    while i < len(parts):
        p = parts[i].upper()
        if len(p) == 4 and p.isalpha():
            cur = p; positions[cur] = []
            if cur not in watchlist: watchlist.append(cur); recovery_queue.put(cur)
            i += 1
        elif cur and 'N' in p:
            val = "".join(filter(lambda x: x.isdigit() or x=='.', p))
            intent = parts[i+1] if i+1 < len(parts) and parts[i+1] in ["防守", "突破"] else "防守"
            if val: positions[cur].append((val, 'N', intent))
            i += (2 if i+1 < len(parts) and parts[i+1] in ["防守", "突破"] else 1)
        else: i += 1
    send_combined_report(list(watchlist), "仓位雷达更新")

@bot.message_handler(commands=['watch', 'now'])
def cmd_handle(message):
    if message.text.startswith('/watch'):
        new = [i.upper() for i in message.text.split()[1:] if len(i)==4]
        for i in new:
            if i not in watchlist: watchlist.append(i); recovery_queue.put(i)
        send_combined_report(new, "监控开启")
    else:
        if watchlist: send_combined_report(list(watchlist), "即时看板")

@bot.message_handler(commands=['unwatch', 'delpos'])
def cmd_del(message):
    icaos = [i.upper() for i in message.text.split()[1:]]
    for i in icaos:
        if message.text.startswith('/unwatch') and i in watchlist: watchlist.remove(i)
        positions.pop(i, None)
    bot.send_message(MY_USER_ID, f"🗑️ 清理完成: {', '.join(icaos)}")

def send_combined_report(target_icaos, title):
    if not target_icaos: return
    full, risks = "", []
    for icao in target_icaos:
        text, is_risk, name = build_single_report(icao); full += text
        if is_risk: risks.append(name)
    summary = "\n⚠️ **实战关注**\n• " + "\n• ".join(set(risks)) if risks else ""
    t = (datetime.now(timezone.utc)+timedelta(hours=8)).strftime('%H:%M')
    bot.send_message(MY_USER_ID, f"=== {title} ({t}) ===\n\n{full}{summary}")

def auto_broadcast_loop():
    while True:
        time.sleep(BROADCAST_INTERVAL * 60)
        if watchlist:
            try: send_combined_report(list(watchlist), f"{BROADCAST_INTERVAL}min 自动巡检")
            except: pass

if __name__ == "__main__":
    Thread(target=auto_broadcast_loop, daemon=True).start()
    bot.remove_webhook()
    print("🚀 交易雷达 V8.0 (物理对齐版) 启动...")
    bot.infinity_polling(timeout=25)
