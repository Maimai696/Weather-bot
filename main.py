import requests
import time
import schedule
import threading
import telebot

# ================= 配置区 =================
# ⚠️ 注意：这里必须填你在 Polymarket 主页 (Profile) 上的收款地址，不要填小狐狸的原始地址！
WALLET_ADDRESS = "0xd8022d5EF2d91B2f91ECD1514db8e56fF3C4D766".lower() 

TELEGRAM_BOT_TOKEN = "8652315325:AAHMAi9otjI5dEKOiuiIIM_wprwYpkJRLIo"
TELEGRAM_CHAT_ID = "6822447850"

# Polymarket 官方目前最稳定的去中心化节点
GOLDSKY_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/polymarket-subgraph/1.1.0/gn"
# ==========================================

# 初始化 Telegram 机器人
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def get_onchain_positions(address):
    """【强力版】从链上自动抓取地址的所有持仓"""
    query = """
    {
      account(id: "%s") {
        userPositions(where: {balance_gt: "0"}) {
          balance
          id
          condition {
            id
          }
        }
      }
    }
    """ % address
    
    try:
        res = requests.post(GOLDSKY_URL, json={'query': query}, timeout=15).json()
        data = res.get('data', {}).get('account')
        
        if not data:
            return []
            
        positions_data = data.get('userPositions', [])
        parsed_positions = []
        
        for p in positions_data:
            condition_id = p['condition']['id']
            # USDC 精度为 6 位
            balance = float(p['balance']) / 10**6 
            
            # 提取 YES/NO 索引
            try:
                outcome_index = int(p['id'].split('-')[-1])
            except:
                outcome_index = 0
                
            parsed_positions.append({
                "condition_id": condition_id,
                "amount": balance,
                "index": outcome_index
            })
            
        return parsed_positions
    except Exception as e:
        print(f"读取链上数据失败: {e}")
        return []

def get_market_detail_and_price(condition_id, my_index):
    """调用 Gamma API 获取市场详情和价格"""
    url = f"https://gamma-api.polymarket.com/markets/{condition_id}"
    try:
        res = requests.get(url, timeout=10).json()
        if "error" in res: return None
        
        question = res.get("question", "未知市场")
        prices = res.get("outcomePrices", ["0", "0"])
        outcomes = res.get("outcomes", ["YES", "NO"])
        
        my_price = float(prices[my_index])
        my_side = outcomes[my_index]
        return {"question": question, "side": my_side, "price": my_price}
    except:
        return None

def build_portfolio_message():
    """组装持仓信息文字"""
    positions = get_onchain_positions(WALLET_ADDRESS)
    if not positions:
        return "⚠️ 未发现任何活跃仓位。请确认地址是否为 Polymarket Profile 上的代理地址。"

    msg = "🚀 *Polymarket 实时仓位报表*\n\n"
    total_value = 0
    
    for pos in positions:
        detail = get_market_detail_and_price(pos['condition_id'], pos['index'])
        if detail:
            val = pos['amount'] * detail['price']
            total_value += val
            msg += f"📍 *{detail['question']}*\n"
            msg += f"• 持仓: `{pos['amount']:.2f}` 股 *{detail['side']}*\n"
            msg += f"• 实时单价: `{detail['side']} ¢{detail['price']*100:.1f}`\n"
            msg += f"• 仓位现值: `$ {val:.2f}`\n\n"
            
    msg += f"💰 *总评估价值: $ {total_value:.2f}*"
    return msg

# ================= 机器人指令交互区 =================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "👋 你好！我是你的 Polymarket 追踪小助手。\n\n"
        "可用指令：\n"
        "🔹 /now - 立即扫描并同步当前仓位\n"
        "🔹 /help - 查看此菜单\n\n"
        "💡 _程序在后台运行，每小时也会自动推送一次最新动态。_"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

@bot.message_handler(commands=['now', 'sync'])
def sync_now(message):
    bot.reply_to(message, "⏳ 正在连接 Polygon 链读取您的仓位，请稍候...")
    try:
        msg_text = build_portfolio_message()
        bot.send_message(message.chat.id, msg_text, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, "❌ 查询失败，请稍后再试。")

# ================= 定时任务区 =================

def scheduled_job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 触发定时任务，正在推送...")
    try:
        msg_text = build_portfolio_message()
        bot.send_message(TELEGRAM_CHAT_ID, msg_text, parse_mode="Markdown")
    except Exception as e:
        print(f"定时推送失败: {e}")

def run_schedule():
    # 设置定时推送频率，默认每 1 小时。可改为 .minutes.do(scheduled_job)
    schedule.every(1).hours.do(scheduled_job)
    while True:
        schedule.run_pending()
        time.sleep(1)

# ================= 启动程序 =================

if __name__ == "__main__":
    print("🤖 程序启动！开启定时任务线程...")
    # 开启独立线程运行定时任务，避免阻塞 TG 机器人的指令接收
    threading.Thread(target=run_schedule, daemon=True).start()
    
    print("✅ Telegram 机器人轮询已启动，随时可以发送 /now 测试。")
    # 无限轮询，保持机器人一直在线接收指令
    bot.infinity_polling()
