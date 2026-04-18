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

# 🌟 全局记忆体：斜率加速度与胜率动能
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
# 2. 核心概率与物理模型 (含日落熔断)
# ==========================================
def phi(z): return (1.0 + math.erf(z / math.sqrt(2.0))) / 2.0

def calculate_precise_win_rate(target, t_peak, curr_t, hour, intent):
    try:
        target_f = float(target)
        sigma = 0.8 if hour < 15 else 0.35
        
        # 日落物理熔断：当地时间 17:00 后且温度已跌破目标，宣判死刑
        if hour >= 17 and isinstance(curr_t, (int, float)):
            if intent == "突破":
                real_t = target_f + 1.0
                if curr_t < real_t - 0.5: return 0.0 
            elif intent == "防守":
                real_t = target_f
                if curr_t < real_t - 0.5: return 100.0 
            elif intent == "YES":
                real_t1 = target_f
                real_t2 = target_f + 1.0
                if curr_t < real_t1 - 0.5 or curr_t >= real_t2: return 0.0

        # 常规概率计算
        if intent == "防守":
            z = (target_f - t_peak) / sigma
            return round(phi(z) * 100, 1)
        elif intent == "突破":
            z = ((target_f + 1.0) - t_peak) / sigma
            return round((1.0 - phi(z)) * 100, 1)
        elif intent == "YES":
            z1 = (target_f - t_peak) / sigma
            z2 = ((target_f + 1.0) - t_peak) / sigma
            return round((phi(z2) - phi(z1)) * 100, 1)
    except: return 0.0

def calc_ls_slope(icao, curr_t, hour, metar):
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
# 3. 报告生成引擎 (胜率动能追踪 + 斜率加速度)
# ==========================================
def build_single_report(icao):
    icao = icao.upper()
    if icao not in airport_metadata: return f"❌ {icao} 未录入\n\n", [], []
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers={"X-API-Key": CHECKWX_API_KEY}, timeout=5).json()
        metar_data = r['data'][0]; curr_t = metar_data.get('temperature', {}).get('celsius', "N/A")
        obs_str = metar_data.get('observed')
        if obs_str: metar_data['observed_unix'] = datetime.strptime(obs_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except: return f"❌ {icao} 接口异常\n\n", [], []
    
    lat, lon = airport_metadata[icao]['coords']
    g_max, m_max, e_max, offset = "N/A", "N/A", "N/A", 0
    try:
        f = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m&models=dwd_icon,ecmwf_ifs,gfs_seamless&forecast_days=3&timezone=auto", timeout=10).json()
        offset = f.get('utc_offset_seconds', 0)
        def gm(k): ts = [v for v in f.get('hourly',{}).get(k,[])[:24] if isinstance(v,(int,float))]; return round(max(ts),1) if ts else "N/A"
        g_max, m_max, e_max = gm('temperature_2m_gfs_seamless'), gm('temperature_2m_dwd_icon'), gm('temperature_2m_ecmwf_ifs')
    except: pass

    local_dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
    slope_str, m_val = calc_ls_slope(icao, curr_t, local_dt.hour, metar_data)
    
    # 🌟 斜率加速度(二阶导数)追踪
    slope_trend_str = ""
    if slope_str != "同步中":
        prev_m = slope_memory.get(icao)
        if prev_m is not None:
            m_diff = round(m_val - prev_m, 2)
            if m_diff > 0: slope_trend_str = f" **(↗ {abs(m_diff):.2f})**"
            elif m_diff < 0: slope_trend_str = f" **(↘ {abs(m_diff):.2f})**"
            else: slope_trend_str = " **(-)**"
        slope_memory[icao] = m_val
    
    valid_f = [v for v in [g_max, m_max, e_max] if isinstance(v, (int, float))]
    
    # 🌟 中位数+权重基准抗干扰引擎
    if valid_f:
        if len(valid_f) == 3: base_f = statistics.median(valid_f)
        elif len(valid_f) == 2: base_f = sum(valid_f) / 2.0
        else: base_f = valid_f[0]
        
        # ECMWF(欧洲中心)权重保护
        if isinstance(e_max, (int, float)) and abs(base_f - e_max) > 1.0:
            base_f = (base_f * 0.4) + (e_max * 0.6)
    else:
        base_f = curr_t
        
    decay = math.sqrt(max(0, 14 - local_dt.hour))
    tp_peak_calc = max(base_f, min(curr_t + m_val*decay if isinstance(curr_t, (int,float)) else base_f, base_f + 2.5))
    tp_peak = max(curr_t, tp_peak_calc) if isinstance(curr_t, (int, float)) else tp_peak_calc

    matrix_lines = []
    base_lvl = math.floor(tp_peak) - 1
    for i in range(4):
        lvl = base_lvl + i
        p_brk = calculate_precise_win_rate(lvl, tp_peak, curr_t, local_dt.hour, "突破")
        p_def = calculate_precise_win_rate(lvl, tp_peak, curr_t, local_dt.hour, "防守")
        p_yes = calculate_precise_win_rate(lvl, tp_peak, curr_t, local_dt.hour, "YES")
        matrix_lines.append(f"{i+1}️⃣ {lvl}N: 突破:{p_brk}% | 防守:{p_def}% | YES区:{p_yes}%")
    
    cc = airport_metadata[icao]['cc']
    flag = chr(ord(cc[0])+127397)+chr(ord(cc[1])+127397) if cc!='UN' else "🏳️"
    
    icao_risks, pos_list, global_pos_list, cur_icon = [], [], [], "⚪"
    priority = {"⚪":0, "🟢":1, "🛡️":2, "🎯":3, "🔴":4, "💰":5}
    
    if icao in positions:
        cur_icon = "🟢"
        for target, direction, intent in positions[icao]:
            win_pct = calculate_precise_win_rate(target, tp_peak, curr_t, local_dt.hour, intent)
            
            # 🌟 胜率动能记忆计算
            mem_key = f"{icao}_{target}_{intent}"
            prev_pct = prob_memory.get(mem_key)
            trend_str = "**-**"
            
            if prev_pct is not None:
                diff = round(win_pct - prev_pct, 1)
                if diff > 0.0: trend_str = f"**↑{abs(diff)}%**"
                elif diff < 0.0: trend_str = f"**↓{abs(diff)}%**"
            
            prob_memory[mem_key] = win_pct 
            
            # 状态与风险扫描
            target_f = float(target)
            this_icon = "🟢"
            if intent == "突破":
                real_t = target_f + 1.0
                dist = abs(real_t - curr_t) if isinstance(curr_t, (int, float)) else 99
                if isinstance(curr_t, (int, float)) and curr_t >= real_t: 
                    this_icon = "💰"; icao_risks.append(f"💰 {icao}|{target}N 击穿(越过{real_t}稳赢)")
                elif dist <= 1.2: 
                    this_icon = "🎯"; icao_risks.append(f"🎯 {icao}|{target}N 临界(距{real_t}仅{round(dist,1)}°)")
            else:
                real_t = target_f
                dist = abs(real_t - curr_t) if isinstance(curr_t, (int, float)) else 99
                if isinstance(curr_t, (int, float)) and curr_t >= real_t - 0.5: 
                    this_icon = "🔴"; icao_risks.append(f"🚨 {icao}|{target}N 危急(距防线{real_t}仅{round(dist,1)}°)")
            
            if priority[this_icon] > priority[cur_icon]: cur_icon = this_icon
            
            pos_list.append(f"• {target}N({intent}:{win_pct}% {trend_str})")
            global_pos_list.append(f"{flag} {icao} | {target}N ({intent}:{win_pct}% {trend_str})")

    # 🌟 动能状态机修正
    if slope_str == "同步中":
        intensity = "同步中"
    else:
        if m_val >= 0.8: intensity = "拉升"
        elif m_val >= 0.3: intensity = "稳升"
        elif m_val > -0.3: intensity = "滞涨"
        elif m_val > -0.8: intensity = "回落"
        else: intensity = "跳水"
        
    pos_display = "\n" + "\n".join(pos_list) if pos_list else "--"
    final_slope_display = f"{slope_str}{slope_trend_str}"
    
    report = (f"{cur_icon} {flag} {icao} ({airport_metadata[icao]['name']})\n"
              f"当地: {local_dt.strftime('%H:%M')} | 实时: {curr_t}°C [{intensity}]\n"
              f"斜率: {final_slope_display} | 🎯 **仓位**: {pos_display}\n"
              f"预测: G:{g_max} I:{m_max} E:{e_max} (基准:{round(base_f,1)})\n"
              f"📊 战术矩阵:\n" + "\n".join(matrix_lines) + "\n----------------------\n")
    return report, icao_risks, global_pos_list

# ==========================================
# 4. 基础通信组件
# ==========================================
def send_chunked_message(header, reports, footer):
    msg = header
    for r in reports:
        if len(msg) + len(r) > 3500:
            bot.send_message(MY_USER_ID, msg, parse_mode="Markdown")
            msg = ""
        msg += r
    if footer:
        if len(msg) + len(footer) > 3500:
            bot.send_message(MY_USER_ID, msg, parse_mode="Markdown")
            msg = footer
        else:
            msg += footer
    if msg.strip(): bot.send_message(MY_USER_ID, msg, parse_mode="Markdown")

def auto_broadcast():
    print("📢 后台风控线程运转中...")
    while True:
        try:
            if watchlist:
                reports, global_risks, all_positions = [], [], []
                for icao in watchlist:
                    txt, risks, pos_sums = build_single_report(icao)
                    reports.append(txt)
                    global_risks.extend(risks)
                    all_positions.extend(pos_sums)
                
                if global_risks:
                    header = f"=== 自动监控预警 ({datetime.now().strftime('%H:%M')}) ===\n\n"
                    footer = ""
                    if all_positions:
                        footer += "\n📋 **当前全仓位汇总:**\n"
                        for p in all_positions: footer += f"{p}\n"
                        footer += "----------------------"
                    footer += "\n⚠️ **综合风险看板:**\n"
                    for idx, r in enumerate(global_risks, 1): footer += f"{idx}️⃣ {r}\n"
                    footer += "=========================="
                    send_chunked_message(header, reports, footer)
            time.sleep(1800)
        except Exception as e:
            print(f"❌ 线程异常: {e}"); time.sleep(60)

# ==========================================
# 5. 指令执行区 (静默与批量支持)
# ==========================================

@bot.message_handler(commands=['now'])
def cmd_now(message):
    icaos = [i.upper() for i in message.text.split()[1:] if len(i)==4]
    target_list = icaos if icaos else watchlist
    if not target_list: 
        bot.send_message(MY_USER_ID, "⚠️ 监控列表为空。")
        return
        
    reports, global_risks, all_positions = [], [], []
    for icao in target_list:
        txt, risks, pos_sums = build_single_report(icao)
        reports.append(txt)
        global_risks.extend(risks)
        all_positions.extend(pos_sums)
    
    header = f"=== 全球实时雷达 ({datetime.now().strftime('%H:%M')}) ===\n\n"
    footer = ""
    if all_positions:
        footer += "\n📋 **当前全仓位汇总:**\n"
        for p in all_positions: footer += f"{p}\n"
        footer += "----------------------"
    if global_risks:
        footer += "\n⚠️ **综合风险看板:**\n"
        for idx, r in enumerate(global_risks, 1): footer += f"{idx}️⃣ {r}\n"
    if footer: footer += "\n=========================="
    send_chunked_message(header, reports, footer)

@bot.message_handler(commands=['watch', 'unwatch'])
def cmd_watch_manage(message):
    parts = [p.upper() for p in message.text.split()[1:] if len(p) == 4]
    if not parts: return
    
    processed = []
    if '/watch' in message.text:
        for i in parts:
            if i in airport_metadata and i not in watchlist:
                watchlist.append(i); processed.append(i)
        if processed: bot.send_message(MY_USER_ID, f"✅ 已添加监控: {', '.join(processed)}")
    else:
        for i in parts:
            if i in watchlist:
                watchlist.remove(i); processed.append(i)
                # 清除记忆防污染
                if i in slope_memory: del slope_memory[i]
                keys_to_del = [k for k in prob_memory if k.startswith(f"{i}_")]
                for k in keys_to_del: del prob_memory[k]
        if processed: bot.send_message(MY_USER_ID, f"✅ 已移除监控: {', '.join(processed)}")

@bot.message_handler(commands=['pos'])
def cmd_pos(message):
    parts = message.text.split()[1:]
    cur, i = None, 0
    added_summary = []
    
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
            added_summary.append(f"{cur}|{val}N({intent})")
            
            # 初始化记忆位
            mem_key = f"{cur}_{val}_{intent}"
            if mem_key not in prob_memory: prob_memory[mem_key] = 0.0
            i += 2
        else: i += 1
            
    if added_summary:
        bot.send_message(MY_USER_ID, f"✅ 仓位建立成功: {', '.join(added_summary)}")

@bot.message_handler(commands=['delpos'])
def cmd_delpos(message):
    parts = message.text.split()[1:]
    if not parts: return
    
    icao = parts[0].upper()
    if icao not in positions:
        bot.send_message(MY_USER_ID, f"⚠️ {icao} 查无持仓。")
        return
        
    if len(parts) == 1:
        del positions[icao]
        keys_to_del = [k for k in prob_memory if k.startswith(f"{icao}_")]
        for k in keys_to_del: del prob_memory[k]
        bot.send_message(MY_USER_ID, f"✅ 已清空 {icao} 所有仓位。")
    else:
        targets = ["".join(filter(lambda x: x.isdigit() or x=='.', p.upper())) for p in parts[1:] if 'N' in p.upper()]
        if not targets: return
        positions[icao] = [p for p in positions[icao] if p[0] not in targets]
        if not positions[icao]: del positions[icao]
        bot.send_message(MY_USER_ID, f"✅ 成功平仓: {icao} [{', '.join([t+'N' for t in targets])}]")

if __name__ == "__main__":
    t = threading.Thread(target=auto_broadcast, daemon=True)
    t.start()
    print("🚀 交易雷达 V9.9 (终极状态机与动能加速版) 启动...")
    bot.infinity_polling()
