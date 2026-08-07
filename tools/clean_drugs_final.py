"""清理丹药：聚灵丹/普通经验书/五色药 每人留2个，多余按原价退款。"""
import json
import os

DATA_PATH = "/root/AstrBot/data/plugin_data/astrbot_plugin_petpark/petpark.json"

# 备份
bak_path = DATA_PATH + ".bak_drugs_final"
os.system(f"cp {DATA_PATH} {bak_path}")
print(f"已备份到: {bak_path}")

with open(DATA_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

players = data.get("players", {})

# 配置：{物品名: (保留数量, 退款单价)}
RULES = {
    "聚灵丹": (2, 8000),
    "普通经验书": (2, 9600),
    "五色药": (2, 10400),
}

total_refunded = 0
total_removed = {}

for key, pl in players.items():
    bag = pl.get("bag", {})
    qq = pl.get("qq", "?")
    for item_name, (keep, refund_price) in RULES.items():
        cnt = bag.get(item_name, 0)
        if cnt <= keep:
            continue
        excess = cnt - keep
        bag[item_name] = keep
        refund = excess * refund_price
        pl["jifen"] = pl.get("jifen", 0) + refund
        total_refunded += refund
        total_removed[item_name] = total_removed.get(item_name, 0) + excess
        print(f"[{item_name}] QQ={qq}: {cnt}→{keep}个, 退款 {refund} 积分")

with open(DATA_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n=== 汇总 ===")
for item_name, cnt in total_removed.items():
    print(f"移除 {item_name}: {cnt} 个")
print(f"退款总积分: {total_refunded}")
