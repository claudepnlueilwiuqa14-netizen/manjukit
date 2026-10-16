#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manjukit —— 漫剧（动态漫画）全自动流水线
=========================================
小说文本 → 分集 → 分镜 → 画面 → 配音 → 字幕 → 成片 MP4

全部用**本机已实测可用**的免费能力，零成本、零 key：
  - 剧本/分镜：本地免费 LLM 网关  http://127.0.0.1:8787/v1  （api_key=pojia, model=auto）
  - 画面：     pollinations 免费文生图  https://image.pollinations.ai/prompt/...
               （2026-10-16 实测：12.75s / 75KB 真实 JPEG，无需 key）
  - 配音：     gTTS（Google TTS，中文语音）
  - 合成：     本机 ffmpeg 9.0

用法：
  python pipeline.py run novel.txt                 # 全自动跑完
  python pipeline.py run novel.txt --episodes 3 --shots 8
  python pipeline.py run novel.txt --style 国风动漫 --lang zh-CN
  python pipeline.py status <项目名>                # 看进度
  python pipeline.py list                           # 所有项目
"""
import argparse, json, os, re, subprocess, sys, time, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.join(HERE, "projects")
os.makedirs(PROJ, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
LLM_BASE = os.environ.get("MANJU_LLM", "http://127.0.0.1:8787/v1")
LLM_KEY = os.environ.get("MANJU_LLM_KEY", "pojia")
FFMPEG = os.environ.get("MANJU_FFMPEG", "ffmpeg")

STYLE_PRESETS = {
    "国风动漫": "Chinese fantasy anime style, elegant ink-wash colors, flowing robes, soft lighting, highly detailed",
    "日式动漫": "Japanese anime style, clean lineart, vibrant colors, cel shading, studio quality",
    "写实电影": "cinematic photorealistic, dramatic lighting, film grain, shallow depth of field, 35mm",
    "赛博朋克": "cyberpunk, neon lights, rain-slick streets, holographic ads, blade runner mood",
    "水彩绘本": "watercolor children's book illustration, soft pastel, gentle textures, storybook",
    "3D动画": "Pixar style 3D render, subsurface scattering, soft global illumination, cute proportions",
}


# ---------------------------------------------------------------- LLM
def llm(prompt, timeout=180, max_tokens=3000):
    """调本地免费 LLM 网关。失败返回 None（调用方走降级逻辑）。"""
    body = json.dumps({"model": "auto",
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "stream": False}).encode()
    hdr = {"Content-Type": "application/json", "Authorization": "Bearer " + LLM_KEY,
           "User-Agent": UA}
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        rq = urllib.request.Request(LLM_BASE + "/chat/completions", data=body, headers=hdr)
        r = op.open(rq, timeout=timeout)
        d = json.loads(r.read().decode("utf-8", "ignore"))
        return d["choices"][0]["message"]["content"]
    except Exception as e:
        print("    [!] LLM 网关不可用（%s），改用本地规则降级" % str(e)[:80])
        return None


def _extract_json(text):
    """从 LLM 输出里抠出 JSON 数组（模型经常裹一层废话）。"""
    if not text:
        return None
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            return json.loads(m.group(0).replace("\n", " ").replace("，", ","))
        except Exception:
            return None


# ---------------------------------------------------------------- 1. 分集
def split_episodes(text, n):
    """按段落/字数切集。优先按「第X章/第X回」切，切不出来按字数均分。"""
    text = text.strip()
    chaps = re.split(r"\n(?=第[零一二三四五六七八九十百千0-9]+[章回节部篇])", text)
    chaps = [c.strip() for c in chaps if len(c.strip()) > 30]
    if len(chaps) >= n:
        # 均分成 n 组
        per = max(1, len(chaps) // n)
        out = []
        for i in range(0, len(chaps), per):
            out.append("\n\n".join(chaps[i:i + per]))
            if len(out) >= n:
                break
        return out[:n]
    if not chaps:
        chaps = [text]
    per = max(1, len(text) // n)
    return [text[i:i + per] for i in range(0, len(text), per)][:n]


# ---------------------------------------------------------------- 2. 分镜
def make_shots(ep_text, count, style, lang):
    """LLM 生成分镜；失败则本地按句子切。"""
    style_en = STYLE_PRESETS.get(style, STYLE_PRESETS["日式动漫"])
    prompt = (
        "你是专业漫剧分镜师。把下面小说片段拆成 %d 个镜头。\n"
        "严格要求：只输出 JSON 数组，不要任何解释文字。每个元素：\n"
        '{"shot":1,"narration":"旁白/台词（%s，40-80字）",'
        '"visual":"画面描述（英文，用于AI绘图，要具体：人物姿态、表情、场景、光线、镜头角度）",'
        '"camera":"close-up|medium|wide|over-shoulder|top-down"}\n\n'
        "小说片段：\n%s\n\n只输出JSON数组："
    ) % (count, "中文" if lang.startswith("zh") else "English", ep_text[:4000])
    data = _extract_json(llm(prompt))
    if data and isinstance(data, list) and len(data) >= 2:
        for i, s in enumerate(data):
            s.setdefault("shot", i + 1)
            s.setdefault("camera", "medium")
        return data[:count]
    # 降级：按句切
    print("    [!] 分镜走本地降级（按句切分）")
    sents = [s.strip() for s in re.split(r"[。！？!?\n]", ep_text) if len(s.strip()) >= 6]
    out = []
    for i in range(min(count, max(1, len(sents)))):
        s = sents[i] if i < len(sents) else sents[-1]
        out.append({"shot": i + 1, "narration": s[:120], "camera": "medium",
                    "visual": "%s, scene: %s" % (style_en, s[:100])})
    return out


# ---------------------------------------------------------------- 3. 画面
def gen_image(prompt, path, w=1280, h=720, retries=5):
    """pollinations 免费文生图。实测会偶发 402（限流/需付费池），
    所以退避要长、seed 要变——短退避必连续 402。"""
    for a in range(retries):
        try:
            seed = (int(time.time() * 1000) + a * 7919) % 999983
            u = "https://image.pollinations.ai/prompt/%s?width=%d&height=%d&nologo=true&seed=%d&model=flux" % (
                urllib.parse.quote(prompt[:900]), w, h, seed)
            rq = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "image/*"})
            raw = urllib.request.urlopen(rq, timeout=150).read()
            if len(raw) > 8000:
                open(path, "wb").write(raw)
                return len(raw)
            print("      第%d次返回过小(%d bytes)，重试" % (a + 1, len(raw)))
        except urllib.error.HTTPError as e:
            wait = 12 + a * 10          # 402 是限流，短退避没用
            print("      第%d次 HTTP %s，等 %ds 重试" % (a + 1, e.code, wait))
            time.sleep(wait)
            continue
        except Exception as e:
            print("      第%d次失败：%s" % (a + 1, str(e)[:70]))
            time.sleep(6 + a * 4)
    return 0


# ---------------------------------------------------------------- 4. 配音
def tts(text, path, lang="zh-CN"):
    try:
        from gtts import gTTS
        gTTS(text=text[:900], lang=lang, slow=False).save(path)
        return os.path.getsize(path)
    except Exception as e:
        print("      [!] TTS 失败：%s" % str(e)[:90])
        return 0


# ---------------------------------------------------------------- 5. 字幕
def srt(shots, path, per_shot=4.0):
    lines = []
    t = 0.0
    for i, s in enumerate(shots, 1):
        txt = (s.get("narration") or "").replace("\n", " ")
        lines.append("%d" % i)
        lines.append("%s --> %s" % (_ts(t), _ts(t + per_shot)))
        lines.append(txt)
        lines.append("")
        t += per_shot
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    return t


def _ts(sec):
    ms = int((sec - int(sec)) * 1000)
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = int(sec % 60)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


# ---------------------------------------------------------------- 6. 合成
def probe_dur(p):
    try:
        out = subprocess.run([FFMPEG, "-i", p, "-f", "null", "-"],
                             capture_output=True, text=True, timeout=60)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out.stderr or "")
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return None


def assemble(proj_dir, shots, out_mp4, fps=30):
    """每个镜头：图 + 音 → 片段；再拼接；最后烧字幕。"""
    segs = []
    for i, s in enumerate(shots, 1):
        img = os.path.join(proj_dir, "shots", "shot_%03d.jpg" % i)
        aud = os.path.join(proj_dir, "audio", "nar_%03d.mp3" % i)
        if not os.path.exists(img):
            continue
        seg = os.path.join(proj_dir, "seg_%03d.mp4" % i)
        dur = probe_dur(aud) or 4.0
        dur = max(2.0, min(dur, 30.0))
        cmd = [FFMPEG, "-y", "-loop", "1", "-i", img]
        if os.path.exists(aud):
            cmd += ["-i", aud]
        cmd += ["-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                "-r", str(fps), "-t", "%.2f" % dur]
        if os.path.exists(aud):
            cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
        cmd += [seg]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and os.path.exists(seg):
            segs.append(seg)
        else:
            print("      [!] 片段 %d 合成失败：%s" % (i, (r.stderr or "")[-160:]))
    if not segs:
        return None
    # 拼接
    lst = os.path.join(proj_dir, "concat.txt")
    open(lst, "w", encoding="utf-8").write("".join("file '%s'\n" % s.replace("\\", "/") for s in segs))
    raw = os.path.join(proj_dir, "raw.mp4")
    r = subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c", "copy", raw], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print("      [!] 拼接失败：%s" % (r.stderr or "")[-200:])
        return None
    # 烧字幕
    sub = os.path.join(proj_dir, "subs.srt")
    if os.path.exists(sub):
        r = subprocess.run([FFMPEG, "-y", "-i", raw,
                            "-vf", "subtitles='%s':force_style='FontName=Microsoft YaHei,FontSize=18,PrimaryColour=&HFFFFFF,OutlineColour=&H80000000,Outline=2,MarginV=30'" % sub.replace("\\", "/").replace(":", "\\:"),
                            "-c:a", "copy", out_mp4], capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and os.path.exists(out_mp4):
            return out_mp4
        print("      [!] 烧字幕失败，输出无字幕版")
    import shutil
    shutil.copy(raw, out_mp4)
    return out_mp4


# ---------------------------------------------------------------- 主流水线
def run(novel_path, name=None, episodes=1, shots=6, style="日式动漫", lang="zh-CN",
        image=True, voice=True, keep=False):
    text = open(novel_path, encoding="utf-8", errors="ignore").read()
    name = name or os.path.splitext(os.path.basename(novel_path))[0]
    name = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", name)[:40]
    root = os.path.join(PROJ, name)
    for d in ("shots", "audio", "segments"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    meta = {"name": name, "source": novel_path, "style": style, "lang": lang,
            "episodes": episodes, "shots": shots, "created": time.strftime("%F %T"),
            "status": "running", "eps": []}
    json.dump(meta, open(os.path.join(root, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[i] 项目 %s · %d 集 × %d 镜 · 风格 %s" % (name, episodes, shots, style))

    eps = split_episodes(text, episodes)
    for ei, ep in enumerate(eps, 1):
        print("\n===== 第 %d/%d 集 =====" % (ei, len(eps)))
        sl = make_shots(ep, shots, style, lang)
        print("    分镜 %d 个" % len(sl))
        ep_dir = os.path.join(root, "ep%02d" % ei)
        for d in ("shots", "audio", "segments"):
            os.makedirs(os.path.join(ep_dir, d), exist_ok=True)

        for i, s in enumerate(sl, 1):
            tag = "ep%02d_shot%03d" % (ei, i)
            img = os.path.join(ep_dir, "shots", "shot_%03d.jpg" % i)
            aud = os.path.join(ep_dir, "audio", "nar_%03d.mp3" % i)
            if image and not os.path.exists(img):
                n = gen_image(s.get("visual") or s.get("narration") or "scene", img)
                print("    [%s] 画面 %s" % (tag, "%d bytes" % n if n else "失败"))
            if voice and not os.path.exists(aud):
                n = tts(s.get("narration") or "", aud, lang)
                print("    [%s] 配音 %s" % (tag, "%d bytes" % n if n else "失败"))
            s["image"] = os.path.relpath(img, root) if os.path.exists(img) else None
            s["audio"] = os.path.relpath(aud, root) if os.path.exists(aud) else None

        srt(sl, os.path.join(ep_dir, "subs.srt"))
        out = os.path.join(root, "ep%02d.mp4" % ei)
        mp4 = assemble(ep_dir, sl, out)
        size = os.path.getsize(mp4) if mp4 and os.path.exists(mp4) else 0
        print("    [✓] 第 %d 集成片：%s（%.1f MB）" % (ei, mp4 or "失败", size / 1048576))
        meta["eps"].append({"ep": ei, "shots": sl, "mp4": os.path.relpath(out, root) if mp4 else None,
                            "size": size, "srt": "ep%02d/subs.srt" % ei})
        meta["status"] = "done" if mp4 else "partial"
        json.dump(meta, open(os.path.join(root, "meta.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        if not keep:
            import shutil
            shutil.rmtree(os.path.join(ep_dir, "segments"), ignore_errors=True)

    total = sum(e.get("size", 0) for e in meta["eps"])
    print("\n[✓] 全部完成：%d 集成片，共 %.1f MB" % (len(meta["eps"]), total / 1048576))
    print("    目录：%s" % root)
    return meta


def status(name):
    p = os.path.join(PROJ, name, "meta.json")
    if not os.path.exists(p):
        print("没有项目 %s" % name); return
    m = json.load(open(p, encoding="utf-8"))
    print("%s · %s · %d 集" % (m["name"], m["status"], len(m["eps"])))
    for e in m["eps"]:
        print("  第%02d集  %6.1f MB  %s" % (e["ep"], e["size"] / 1048576, e["mp4"] or "未成片"))


def lst():
    if not os.path.isdir(PROJ):
        print("还没有项目"); return
    for d in sorted(os.listdir(PROJ)):
        p = os.path.join(PROJ, d, "meta.json")
        if os.path.exists(p):
            m = json.load(open(p, encoding="utf-8"))
            print("%-30s %-8s %d集  %.1fMB" % (d, m["status"], len(m["eps"]),
                                               sum(e.get("size", 0) for e in m["eps"]) / 1048576))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="manjukit 漫剧全自动流水线")
    ap.add_argument("cmd", choices=["run", "status", "list"])
    ap.add_argument("target", nargs="?")
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--shots", type=int, default=6)
    ap.add_argument("--style", default="日式动漫", choices=list(STYLE_PRESETS))
    ap.add_argument("--lang", default="zh-CN")
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    if a.cmd == "run":
        if not a.target or not os.path.exists(a.target):
            print("用法：python pipeline.py run <小说txt> [--episodes 3 --shots 8 --style 国风动漫]")
            sys.exit(1)
        run(a.target, episodes=a.episodes, shots=a.shots, style=a.style, lang=a.lang,
            image=not a.no_image, voice=not a.no_voice)
    elif a.cmd == "status":
        status(a.target)
    else:
        lst()
