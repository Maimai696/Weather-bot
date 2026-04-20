import requests
import time
import schedule

# ================= 配置区 =================
WALLET_ADDRESS = "0xd8022d5EF2d91B2f91ECD1514db8e56fF3C4D766".lower() # 必须小写
TELEGRAM_BOT_TOKEN = "8652315325:AAHMAi9otjI5dEKOiuiIIM_wprwYpkJRLIo"
TELEGRAM_CHAT_ID = "6822447850"

# Polymarket 子图节点 (用于查询链上余额)
SUBGRAPH_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/polymarket-subgraph/1.1.0/gn"
# ==========================================

def get_onchain_positions(address):
    """从 The Graph 自动抓取地址的所有持仓"""
    query = """
    {
      account(id: "%s") {
        userPositions(where: {balance_gt: "0"}) {
          balance
          id
          condition {
            id
            oracle
          }
        }
      }
    }
    """ % address
    
    try:
        res = requests.post(SUBGRAPH_URL, json={'query': query}, timeout=10).json()
        positions_data = res.get('data', {}).get('account', {}).get('userPositions', [])
        
        parsed_positions = []
        for p in positions_data:
            # Polymarket Token ID 格式通常包含 conditionId 和 outcomeIndex
            # 这里简化处理：通过 conditionId 去匹配市场
            condition_id = p['condition']['id']
            balance = float(p['balance']) / 10**6 # USDC 精度 6 位
            
            # 这里的 ID 末尾通常标识了是第几个选项 (0=YES, 1=NO)
            # 例如: "0x...-0" 是 YES, "0x...-1" 是 NO
            outcome_index = int(p['id'].split('-')[-1])
            
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
    """调用 Gamma API 获取市场名字和精准的 YES/NO 价格"""
    url = f"https://gamma-api.polymarket.com/markets/{condition_id}"
    try:
        res = requests.get(url, timeout=10).json()
        if "error" in res: return None
        
        question = res.get("question", "未知市场")
        prices = res.get("outcomePrices", ["0", "0"]) # ["YES价格", "NO价格"]
        outcomes = res.get("outcomes", ["YES", "NO"])
        
        # 根据我的索引定位价格
        # 如果 index=0，对应 prices[0] (YES)；如果 index=1，对应 prices[1] (NO)
        my_price = float(prices[my_index])
        my_side = outcomes[my_index]
        
        return {
            "question": question,
            "side": my_side,
            "price": my_price,
            "all_prices": prices
        }
    except:
        return None

def send_tg_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

def job():
    print(f"正在自动扫描地址: {WALLET_ADDRESS} ...")
    positions = get_onchain_positions(WALLET_ADDRESS)
    
    if not positions:
        print("未发现活跃仓位。")
        return

    msg = "🚀 *Polymarket 自动仓位同步*\n\n"
    total_value = 0
    
    for pos in positions:
        detail = get_market_detail_and_price(pos['condition_id'], pos['index'])
        if detail:
            val = pos['amount'] * detail['price']
            total_value += val
            
            msg += f"📍 *{detail['question']}*\n"
            msg += f"• 持仓: `{pos['amount']:.2f}` 股 *{detail['side']}*\n"
            msg += f"• 当前价格: `{detail['side']} ¢{detail['price']*100:.1f}`\n"
            msg += f"• 现值: `$ {val:.2f}`\n\n"
    
    msg += f"💰 *账户总评估: $ {total_value:.2f}*"
    send_tg_msg(msg)
    print("同步成功。")

# --- 运行设置 ---
if __name__ == "__main__":
    job() # 启动运行一次
    schedule.every(1).hours.do(job) # 每小时自动扫描一次地址
    
    while True:
        schedule.run_pending()
        time.sleep(1)
