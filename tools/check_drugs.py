import json
with open("/root/AstrBot/data/plugin_data/astrbot_plugin_petpark/petpark.json", "r", encoding="utf-8") as f:
    data = json.load(f)
players = data.get("players", {})
found = 0
for key, pl in players.items():
    bag = pl.get("bag", {})
    jl = bag.get("聚灵丹", 0)
    exp = bag.get("普通经验书", 0)
    ws = bag.get("五色药", 0)
    if jl > 0 or exp > 0 or ws > 0:
        qq = pl.get("qq", "?")
        jifen = pl.get("jifen", 0)
        print(f"QQ={qq}: 聚灵丹={jl}, 经验书={exp}, 五色药={ws}, 积分={jifen}")
        found += 1
if not found:
    print("没有发现异常丹药")
