#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TokenGo 批量执行适配脚本（不绑定具体商品或品类）。

设计原则
--------
1. 一切由任务清单 JSON 驱动：母版、参考图、提示词、尺寸、出图文件名全在清单里。
   换商品、换品类、换母版，只改清单，脚本零改动。
2. 母版永远只传 1 张，其余为 SKU/参考素材，保证「一张母版出变体」。
3. 完整画面描述由清单的 prompt 提供，脚本不自行猜测画面。
4. 已存在的输出默认跳过；失败记录到输出目录的 .failed.json，便于重跑。

用法
----
    python3 脚本/batch_generate.py <任务清单.json> [--dry-run] [--only 名称] [--force]

任务清单格式
------------
{
  "output_dir": "02-生成",
  "valid_sizes": ["1:1", "3:4", "9:16"],   # 可选，覆盖模型支持的比例档位
  "max_refs": 10,                          # 可选，单次请求参考图数量上限
  "tasks": [
    {
      "name": "主图-白花灰杏",
      "master": "01-素材/母版/主图/01-黑黑.jpg",
      "refs": ["01-素材/SKU/上衣-白花灰.png", "01-素材/SKU/裤子-杏色.png"],
      "prompt": "填写按当前母版实际编写的画面描述，包含布局、各处商品呈现、图文和商品特征约束。",
      "size": "1:1",
      "resolution": "1k",
      "count": 1
    }
  ]
}

size 支持三种写法
------------------
  "3:4"            已是支持档位，直接使用
  "800x1200"       精确像素，自动换算成最接近的档位，交付保留生成比例
  "800x1200,3:4"   首选档位 3:4，800x1200 仅作提示

**不会静默回退成 1:1。** 比例不匹配一定会在日志里打出来。

edits 结构（兼容旧清单；新任务推荐完整 prompt）
-----------------------------------
也可用 edits 代替 prompt，脚本按顺序合成提示词行：

  {
    "name": "主图-白花灰杏",
    "master": "01-素材/母版/主图/01-黑黑.jpg",
    "refs": ["01-素材/SKU/上衣-白花灰.png", "01-素材/SKU/裤子-杏色.png"],
    "edits": [
      {"type": "rewrite", "target": "美式复古夹克", "to": "立领撞色开衫"},
      {"type": "present", "to": "{ref:0}、{ref:1}"}
    ]
  }

| type | 含义 | 合成结果 |
|------|------|----------|
| `rewrite` | 改写文字 | `@image#1 文字「X」改为「Y」` |
| `present` | 按母版形式展示商品（默认） | 通用参考图分工句式 |
| `replace` | 兼容旧清单的显式整体替换指令 | `@image#1 <target> 整体替换为 <to>` |
| `style` | 只换款式、保留母版呈现层 | `@image#1 <target> 款式替换为 <to>，只修改款式` |
| `state` | 指定状态项 | `@image#1 <target> 改为 <to>` |
| `add` | 增补 SKU 有而母版没有的元素 | `@image#1 <target> 替换为 <to>` |
| `remove` | 移除母版有而 SKU 没有的元素 | `@image#1 去除 <target>` |
| `lock` | 锁定锚点 | `@image#1 的 <target> 保持不变` |
| `raw` | 自由文本行 | 原样输出 |

占位符：`{master}` → `@image#1`，`{ref:0}` → `@image#2`（refs 下标从 0 起）。
这样清单作者不必硬编码图片编号，refs 顺序变了也不会指错。

base 相对清单文件所在目录解析，素材路径相对 base；
缺省 base = 清单所在目录的上一级（项目根）。
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TOKENDO = os.path.expanduser("~/.local/bin/tokengo")

# 图像模型的宽高比枚举（以 model get 返回的 schema 为准，清单里可用 valid_sizes 覆盖）
DEFAULT_VALID_SIZES = ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9", "auto"]

# 单次请求参考图数量上限（清单里可用 max_refs 覆盖）
DEFAULT_MAX_REFS = 10


def ratio_to_float(size):
    """'3:4' -> 0.75；'auto' 或非法值返回 None。"""
    if not size or size == "auto":
        return None
    try:
        w, h = size.split(":")
        return int(w) / int(h)
    except (ValueError, ZeroDivisionError):
        return None


def nearest_size(target_ratio, valid_sizes):
    """在支持的比例里找最接近 target_ratio 的一个。"""
    if target_ratio is None:
        return None
    best, best_diff = None, None
    for s in valid_sizes:
        r = ratio_to_float(s)
        if r is None:
            continue
        diff = abs(r - target_ratio)
        if best_diff is None or diff < best_diff:
            best, best_diff = s, diff
    return best


def compose_prompt(task):
    """把 edits 结构合成成提示词文本。

    句式规则见文件头。{master} → @image#1，{ref:i} → @image#(i+2)。
    """
    edits = task.get("edits")
    if not edits:
        p = task.get("prompt")
        if not p:
            raise SystemExit(f"任务 {task.get('name')} 既没有 prompt 也没有 edits")
        return p if isinstance(p, str) else "\n".join(p)

    n_refs = len(task.get("refs", []))

    def fill(text):
        text = str(text)
        text = text.replace("{master}", "@image#1")
        for i in range(n_refs):
            text = text.replace(f"{{ref:{i}}}", f"@image#{i + 2}")
        return text

    lines = []
    for e in edits:
        etype = e.get("type", "present")
        target = fill(e.get("target", ""))
        to = fill(e.get("to", ""))
        if etype == "raw":
            lines.append(fill(e.get("text", "")))
        elif etype == "present":
            products = to or "、".join(f"@image#{i + 2}" for i in range(n_refs))
            if not products:
                raise SystemExit(f"任务 {task.get('name')} 的 present 缺少商品参考图")
            lines.append(
                f"以 @image#1 为母版，按 @image#1 中商品的呈现方式展示 {products} 对应的商品。"
                "商品特征以对应商品图为准，其余保持 @image#1 不变。"
            )
        elif etype == "rewrite":
            lines.append(f'"{target}"改为"{to}"。')
        elif etype == "replace":
            lines.append(f"@image#1 {target} 整体替换为 {to}")
        elif etype == "style":
            lines.append(f"@image#1 {target} 款式替换为 {to}，只修改款式")
        elif etype == "state":
            lines.append(f"@image#1 {target} 改为 {to}")
        elif etype == "add":
            lines.append(f"@image#1 {target} 替换为 {to}")
        elif etype == "remove":
            lines.append(f"@image#1 去除 {target}")
        elif etype == "lock":
            lines.append(f"@image#1 的 {target} 保持不变")
        else:
            raise SystemExit(f"未知的 edit 类型: {etype}")

    for lk in task.get("lock", []):
        lines.append(f"@image#1 的 {fill(lk)} 保持不变")
    return "\n".join(l for l in lines if l.strip())


def resolve_size(size, valid_sizes):
    """
    把请求的尺寸解析成模型支持的档位。

    支持三种写法：
      '3:4'            —— 已是支持档位，直接用
      '800x1200'       —— 精确像素，换算成最接近的档位（保留生成比例）
      '800x1200,3:4'   —— 首选档位 3:4，800x1200 仅作提示

    关键：不允许静默回退成 1:1。比例不匹配必须让调用方看见。
    """
    if size in valid_sizes:
        return size, None

    pixels = None
    preferred = None
    for part in str(size).split(","):
        part = part.strip()
        if "x" in part and "×" not in part:
            w, h = part.split("x")
            pixels = (int(w), int(h))
        elif part and part != "auto":
            preferred = part

    if pixels:
        w, h = pixels
        snapped = preferred if preferred in valid_sizes else nearest_size(w / h, valid_sizes)
        note = (f"母版尺寸 {w}x{h}，已选择比例档位 {snapped}，"
                "保留生成比例交付，不补边或裁切回母版比例")
        return snapped, note

    if preferred:
        if preferred in valid_sizes:
            return preferred, None
        snapped = nearest_size(ratio_to_float(preferred), valid_sizes)
        if snapped:
            return snapped, f"比例 {preferred} 不支持，已用最接近的 {snapped}"
        return "1:1", f"比例 {preferred} 无法解析，已用 1:1，请检查清单"

    return "1:1", f"尺寸 {size} 无法识别，已用 1:1，请检查清单"


def find_tokengo():
    for p in (TOKENDO, "/opt/homebrew/bin/tokengo", "/usr/local/bin/tokengo"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    from shutil import which
    w = which("tokengo")
    if w:
        return w
    raise SystemExit("找不到 tokengo 可执行文件，请确认已安装并登录。")


def run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def upload(cli, path, agent):
    """上传本地素材，返回平台 URL。失败抛异常。"""
    ext = Path(path).suffix.lower()
    ctype = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
             ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "application/octet-stream")
    r = run([cli, "upload", "file", "--agent", agent,
             "--file", str(path), "--content-type", ctype], timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"上传失败 {path}: {r.stderr.strip()[:300]}")
    for line in r.stdout.strip().splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("final_url"):
            return d["final_url"]
    raise RuntimeError(f"上传返回无 final_url: {r.stdout[:200]}")


_URL_CACHE = {}


def materialize(cli, ref, agent, cache):
    """把参考图解析为可用的 URL：已是 http(s) 就直接用，否则上传并缓存。"""
    if str(ref).startswith(("http://", "https://")):
        return str(ref)
    key = str(Path(ref).resolve())
    if key in cache:
        return cache[key]
    url = upload(cli, ref, agent)
    cache[key] = url
    return url


def generate(cli, params, agent, timeout_min=20):
    """保存原请求与结果；恢复时不重复提交生成。"""
    pf = Path(params["_params_file"])
    payload = {key: value for key, value in params.items() if not key.startswith("_")}
    receipt = pf.with_suffix(".receipt.json")
    progress = pf.with_suffix(".progress.jsonl")
    if pf.exists() and json.loads(pf.read_text(encoding="utf-8")) != payload:
        raise RuntimeError(f"已有不同参数，请使用新的任务名保留原记录: {pf}")
    pf.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if receipt.exists():
        return json.loads(receipt.read_text(encoding="utf-8"))
    cmd = [cli, "generation", "create", "--agent", agent,
           "--model", params.get("_model", "doubao-seedream-5-0-pro"),
           "--type", "image", "--params-file", str(pf),
           "--wait", "--timeout", f"{timeout_min}m"]
    if progress.exists():
        events = []
        for line in progress.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        generations = [e["generation_id"] for e in events if e.get("generation_id")]
        requests = [e["request_id"] for e in events if e.get("request_id")]
        if generations:
            cmd = [cli, "generation", "wait", "--agent", agent,
                   "--generation", generations[-1], "--timeout", f"{timeout_min}m"]
        elif requests:
            status = run([cli, "generation", "request", "--agent", agent,
                          "--request", requests[-1]], timeout=60)
            raise RuntimeError(f"原请求需恢复，请勿新建重复请求: {status.stdout.strip()}")
        else:
            raise RuntimeError(f"已有不完整提交记录，先核对再继续: {progress}")
    with progress.open("a", encoding="utf-8") as log:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=log, text=True,
                           timeout=timeout_min * 60 + 120)
    out = (r.stdout or "").strip()
    obj = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("status") in ("completed", "failed") or d.get("result"):
                obj = d
    if obj is None:
        raise RuntimeError(f"生成未返回结果，恢复编号保存在 {progress}: {out[:400]}")
    if obj.get("status") != "completed":
        raise RuntimeError(f"生成失败: {str(obj)[:300]}")
    receipt.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return obj


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(dest.suffix + ".part")
    subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error",
                    "--max-time", "180", url, "--output", str(temp)], check=True)
    temp.replace(dest)
    return dest.stat().st_size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="只跑名字包含该关键字的任务")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--agent", default="workbuddy")
    ap.add_argument("--model", default="doubao-seedream-5-0-pro")
    ap.add_argument("--sleep", type=float, default=2.0, help="两次生成之间的间隔秒数")
    args = ap.parse_args()

    mpath = Path(args.manifest).resolve()
    man = json.loads(mpath.read_text(encoding="utf-8"))
    base = ((mpath.parent / man["base"]).resolve()
            if man.get("base") else mpath.parent.parent)
    outdir = base / man.get("output_dir", "02-生成")
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = base / ".workbuddy" / "gen"
    workdir.mkdir(parents=True, exist_ok=True)

    cli = find_tokengo()
    tasks = man.get("tasks", [])
    if args.only:
        tasks = [t for t in tasks if args.only in t.get("name", "")]

    valid_sizes = man.get("valid_sizes") or DEFAULT_VALID_SIZES
    max_refs = man.get("max_refs") or DEFAULT_MAX_REFS

    cachefile = workdir / "url_cache.json"
    cache = json.loads(cachefile.read_text()) if cachefile.exists() else {}

    failed = {}
    ff = workdir / "failed.json"
    if ff.exists():
        failed = json.loads(ff.read_text())

    total = len(tasks)
    for i, t in enumerate(tasks, 1):
        name = t["name"]
        dest = outdir / t.get("output", f"{name}.jpg")
        if dest.exists() and not args.force:
            print(f"[{i}/{total}] 跳过 {name}（已存在）")
            continue

        master = base / t["master"]
        refs = [base / r for r in t.get("refs", [])]
        if len(refs) + 1 > max_refs:
            raise SystemExit(
                f"[{i}/{total}] {name} 参考图 {len(refs) + 1} 张超过上限 {max_refs}，"
                f"请精简 SKU 参考图或分批生成")
        if args.dry_run:
            images = [str(master), *map(str, refs)]
        else:
            images = [materialize(cli, master, args.agent, cache),
                      *[materialize(cli, r, args.agent, cache) for r in refs]]
            cachefile.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

        size, size_note = resolve_size(t.get("size", "1:1"), valid_sizes)
        if size_note:
            print(f"[{i}/{total}] ⚠ {name}: {size_note}", flush=True)
        params = {
            "_model": args.model,
            "_params_file": str(workdir / f"{name}.json"),
            "mode": t.get("mode", "multi-reference" if len(images) > 1 else "image-edit"),
            "size": size,
            "resolution": t.get("resolution", "1k"),
            "count": t.get("count", 1),
            "images": images,
            "prompt": compose_prompt(t),
        }

        if args.dry_run:
            print(f"[{i}/{total}] DRY {name}: {json.dumps(params, ensure_ascii=False)}")
            continue

        print(f"[{i}/{total}] 生成 {name} …", flush=True)
        try:
            obj = generate(cli, params, args.agent, timeout_min=t.get("timeout_min", 20))
            url = obj.get("result_url") or obj.get("image_url") or obj["result"]["images"][0]["url"]
            n = download(url, dest)
            cost = (obj.get("billing") or {}).get("actual_cost_credits")
            print(f"    ✓ {dest.name}  {n/1024:.0f}KB" + (f"  {cost} credits" if cost else ""), flush=True)
            failed.pop(name, None)
        except Exception as e:
            print(f"    ✗ {name}: {e}", flush=True)
            failed[name] = {"error": str(e)[:500], "params": params["prompt"]}
            ff.write_text(json.dumps(failed, ensure_ascii=False, indent=1), encoding="utf-8")
        cachefile.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        if i < total:
            time.sleep(args.sleep)

    if not args.dry_run:
        ff.write_text(json.dumps(failed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完成。输出目录: {outdir}")
    if failed:
        print(f"失败 {len(failed)} 项: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
