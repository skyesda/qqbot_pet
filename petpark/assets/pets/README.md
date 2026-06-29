# 宠物种类图片

每个宠物种类一张图片，文件名与 `petpark/data.py` 中 `SPECIES` 的种类名一致：

```
petpark/assets/pets/<种类名>.jpg
```

规格：512×512、纯白背景、JPEG。

由 `tools/gen_pet_images.py` 在开发/部署期生成（调用 flyyye 生图 API，密钥经环境变量传入，不入库）：

```bash
FLYYYE_KEY=sk-xxxx python tools/gen_pet_images.py          # 生成所有缺失
FLYYYE_KEY=sk-xxxx python tools/gen_pet_images.py --smoke  # 先冒烟测 1 张
FLYYYE_KEY=sk-xxxx python tools/gen_pet_images.py --force 皮卡丘   # 重生成单张
```

运行时由 `petpark/images.py` 的 `pet_image_path(species)` 定位，
在『我的宠物』『宠物侦查』『宠物种类 名称』时随消息发送。
