# 服装主图与详情页生成 Skill

以已有主图、详情页为母版，结合新商品 SKU 图生成同版式商品图片。适用于不同服装品类，不绑定某件商品、品牌或固定页面结构。

## 核心方法

**母版决定怎么呈现，SKU 决定是什么商品。**

逐张阅读母版，将布局、图文位置、商品姿态、体积、褶皱、遮挡、层次和光影写成画面描述，再结合对应 SKU 的真实款式特征生成。描述随当前母版变化，不把一次测试的构图固定成通用模板。

- 长图先按完整版块拆分；已有单屏图直接使用。
- 先整理 SKU、资料缺口和组成方案，确认后生成；已有单张测试授权时先试图。
- 母版比例不在模型档位内时用最接近比例，保留该比例交付，不补边或裁切。
- 不执行生成后画面自检或自动补正重跑。
- 本仓库提供技能说明、服务适配、脚本和空白项目模板，不包含本地商品素材、生成结果、账户配置或请求回执。

## 安装与使用

将本仓库克隆到本机的 Skill 目录，文件夹名建议为 `fashion-listing-generator`，入口是 [SKILL.md](SKILL.md)。例如：

```bash
git clone https://github.com/fanzhantupo995-byte/fashion-listing-generator-skill.git \
  ~/.codex/skills/fashion-listing-generator
```

在新的 Agent 会话中提供母版图和 SKU 图，并请求：

```text
使用 fashion-listing-generator，按母版制作这件商品的主图和详情页。
先用画面描述方案试一张主图。
```

流程与提示词方法见 [SKILL.md](SKILL.md)。图像生成服务由使用者选择，差异记录在 [提示词](提示词/README.md)。

## 文件

| 路径 | 用途 |
|---|---|
| `SKILL.md` | 输入、母版解析、方案、画面描述、生成与交付 |
| `提示词/` | 服务和模型适配；当前包含 TokenGo、Seedream |
| `参考模板/` | 模板目录规则和空白店铺规范 |
| `结果/_项目模板/` | 商品信息与组成方案空表 |
| `脚本/split_sections.py` | 长图导航和人工确定边界后的切分 |
| `脚本/batch_generate.py` | TokenGo 批量执行辅助脚本 |

## 脚本

需要 Python 3；长图拆分依赖 Pillow：

```bash
python3 -m pip install -r requirements.txt
python3 脚本/split_sections.py scan 输入长图.jpg 临时拆分目录
```

批量脚本当前使用 TokenGo CLI，需要先由使用者安装 CLI 并授权对应 Agent。它是服务适配工具，不是通用生成接口。运行时显式传入 Agent 和模型：

```bash
python3 脚本/batch_generate.py 项目/参数/任务清单.json \
  --agent codex --model doubao-seedream-5-0-pro --dry-run
```

每项任务的 `prompt` 应填写已结合母版完成的画面描述。先检查清单，再去掉 `--dry-run` 提交生成。账户凭证由 CLI 管理，不写入任务清单。
