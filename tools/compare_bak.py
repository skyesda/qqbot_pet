import json

for fname in ["petpark.json.bak_julingdan2", "petpark.json"]:
    path = "/root/AstrBot/data/plugin_data/astrbot_plugin_petpark/" + fname
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    players = data.get("players", {})
    for key, pl in players.items():
        bag = pl.get("bag", {})
        jl = bag.get("聚灵丹", 0)
        if jl > 2:
            qq = pl.get("qq", "?")
            jifen = pl.get("jifen", 0)
            print(f"[{fname}] QQ={qq}: 聚灵丹={jl}, 积分={jifen}")
