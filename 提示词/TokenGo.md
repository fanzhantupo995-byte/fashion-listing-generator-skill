# TokenGo 适配文件

> 本文件记录 TokenGo 图像生成的语法、硬限制与已验证的用法。
> 本文件处理服务参数与调用方式；参考图分工遵循 `SKILL.md`。
> 实测记录：2026-11-02 · 模型 `doubao-seedream-5-0-pro`

## 模型

| 模型 ID | 用途 | 参考图 | 备注 |
|---------|------|--------|------|
| `doubao-seedream-5-0-pro` | 详情页版块、主图 | 最多 10 张 | 本项目当前使用 |

其他模型用 `tokengo model list --agent workbuddy --type image` 查，再用 `tokengo model get` 看参数 schema。

## 参数 schema（`doubao-seedream-5-0-pro`）

| 参数 | 取值 | 说明 |
|------|------|------|
| `mode` | `text-to-image` / `image-edit` / `multi-reference` | 套版用 `multi-reference`；`image-edit` 只接受 1 张参考图 |
| `images` | 最多 10 个 URL | **母版放第 1 位**，SKU 图按顺序排在后面 |
| `size` | `1:1` `4:3` `3:4` `16:9` `9:16` `3:2` `2:3` `21:9` `auto` | 母版比例不在档位内时选最接近比例，保留生成比例交付，不补边或裁切 |
| `resolution` | `1k` / `2k` | `2k` 像素翻倍，价格约为 `1k` 的 2 倍 |
| `count` | 1–50 | 主图一张一张独立提交，绝不合并成一次多张 |

## 参考图指代语法

**用 `@image#N`**，N 对应 `images` 数组下标（从 1 开始）。

```
@image#1 是母版，@image#2 起是 SKU 图
```

实测：不需要再补一句「图1是XX、图2是YY」的类型说明，直接指代即可，商品替换定位准确。

## 命令

```bash
# 上传本地素材，拿 final_url
tokengo upload file --agent workbuddy --file ./SKU/上衣.png --content-type image/png

# 生成（params 用 UTF-8 JSON 文件传，嵌套数组字段不要拼在命令行里）
tokengo generation create --agent workbuddy \
  --model doubao-seedream-5-0-pro --type image \
  --params-file ./params/xxx.json --wait --timeout 15m
```

`params.json` 形态：

```json
{
  "mode": "multi-reference",
  "size": "1:1",
  "resolution": "1k",
  "count": 1,
  "images": ["母版URL", "SKU1URL", "SKU2URL"],
  "prompt": "按 @image#1 的画面制作同版图片，商品对应 @image#2、@image#3。画面描述：[填入当前母版的分区、图文位置、商品姿态、体积、层次和光影]。商品款式和细节以对应商品图为准，所有图文位置、大小比例与留白贴近 @image#1。"
}
```

## 已验证的硬限制与坑

1. **同一次 `create` 不要重复提交**——每次提交都是一次计费。批量生成用 `脚本/batch_generate.py`，它会跳过已存在的输出文件。
2. **嵌套数组参数必须走 `--params-file`**，不要把 JSON 拼在命令行里。
3. **`images` 顺序即 `@image#N` 编号**，母版必须是第 1 张。
4. **计费**：实测 `1k` 单张约 0.15–0.33 credits；`2k` 约为 2 倍。批量出图前先算笔账。
5. **使用画面描述**：按 `SKILL.md` 第五步从当前母版提取布局、商品姿态、体积、层次和光影；SKU 提供款式特征。参数示例的占位文字必须替换成具体描述后提交。
6. **CLI 有频率与每日限额**，报 `cli_daily_limit_exceeded` / `api_key_quota_exceeded` 时不要重试，按提示等待或换模型。

## 结果保存

保存生成结果和请求编号，不执行画面自检或自动补正。中断时查询原请求，不新建付费请求找回结果。
