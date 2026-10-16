# manjukit — 漫剧全自动流水线

**小说文本 → 分集 → LLM 分镜 → 免费文生图 → gTTS 配音 → SRT 字幕 → ffmpeg 合成 MP4**
全流程零 key、零付费、全本地可控。一条命令出一部漫剧。

## 特性

- **一条命令出片**：`python pipeline.py run 小说.txt --episodes 8`，跑完直接在 `projects/<小说名>/` 拿 MP4
- **6 种风格预设**：国风动漫 / 日式动漫 / 写实电影 / 赛博朋克 / 水彩绘本 / 3D动画
- **LLM 分镜**：自动生成镜头（景别 medium/close-up/over-shoulder…）、画面描述（英文，喂给文生图）、旁白、时长估算
- **免费文生图**：pollinations.ai 免费接口（`model=flux`），**不需要任何 key**；内置 402 限流长退避 + seed 变化
- **免费配音**：gTTS（Google TTS 免费通道），中文普通话
- **字幕烧录**：SRT 生成 + ffmpeg `subtitles` 滤镜硬字幕（微软雅黑、白字黑边）
- **断点续跑**：`run` 会跳过已存在的分镜图/音频，中断后重跑不重复消耗
- **双降级链路**：LLM 不可用 → 本地按句切分分镜；`--no-image` 纯音频稿；`--no-voice` 纯画面稿
- **项目清单**：`status` 看每个项目的镜头数/成片/体积；`list` 看全部

## 依赖

| 组件 | 安装 |
|---|---|
| Python 3.10+ | — |
| ffmpeg（含 ffprobe） | `winget install Gyan.FFmpeg` 或官网，需在 PATH |
| gTTS | `pip install gTTS` |
| LLM（可选） | 任意外露的 OpenAI 兼容端点；不配则走本地降级分镜 |

## 快速开始

```bash
pip install gTTS

# 最简：1 集、每集 3 镜、日式动漫
python pipeline.py run _test_novel.txt --episodes 1 --shots 3

# 完整：8 集、每集 6 镜、国风动漫
python pipeline.py run 我的小说.txt --episodes 8 --shots 6 --style 国风动漫

# 只要音频稿（不出图不出片，最快）
python pipeline.py run 我的小说.txt --episodes 4 --no-image

# 纯画面（无配音，适合后期自己配）
python pipeline.py run 我的小说.txt --episodes 4 --no-voice
```

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `MANJU_LLM_BASE` | OpenAI 兼容端点 base | `https://text.pollinations.ai/openai` |
| `MANJU_LLM_MODEL` | 模型名 | `openai` |
| `MANJU_LLM_KEY` | API key（免费端点留空） | 空 |

```bash
# 接入自己的 LLM（分镜质量更高）
set MANJU_LLM_BASE=https://www.985la.cn/v1
set MANJU_LLM_MODEL=gpt-5.6-sol
set MANJU_LLM_KEY=sk-xxxx
python pipeline.py run 小说.txt --episodes 8
```

## 输出结构

```
projects/我的小说/
├── ep01/
│   ├── shots/shot_001.jpg …        # 分镜图（1280×720）
│   ├── audio/nar_001.mp3 …         # 旁白配音
│   ├── seg_001.mp4 …               # 单镜头片段（图+音+时长对齐）
│   ├── concat.txt                  # ffmpeg concat 清单
│   ├── subs.srt                    # 字幕
│   ├── raw.mp4                     # 无字幕版
│   └── ep01.mp4                    # 最终成片（硬字幕）
├── ep02/ …
└── manifest.json                   # 全项目清单（集数/镜头/成片路径/体积）
```

## 已验证的真实结果（2026-10-16）

测试小说：两章古风言情，`--episodes 2 --shots 3 --style 国风动漫`：

| 集 | 时长 | 编码 | 分辨率 | 体积 |
|---|---|---|---|---|
| ep01 | 26.27s | h264 + aac | 1024×576 | 679 KB |
| ep02 | 15.98s | h264 + aac | 1024×576 | 458 KB |

LLM 分镜实录（非降级、非模板）：

```
镜1 [medium]   暮春三月，桃花开得正盛。苏晚晴提着裙摆跑过回廊…
   visual: Young woman in flowing pastel dress running through classical
           Chinese courtyard corridor, peach blossoms, soft morning light
镜2 [over-shoulder] 姑娘，你的帕子掉了。身后传来清冷的男声。
   visual: Elegant young man in pristine white traditional robes standing
           among blooming peach trees, back view, shallow depth of field
镜3 [close-up] 她回头，撞进一双深不见底的眼眸…
   visual: Close-up of two pairs of hands meeting as the handkerchief is
           exchanged, warm sunlight, romantic atmosphere
```

## 实现要点（踩过的坑）

1. **pollinations 图像接口偶发 HTTP 402**：这是限流信号，**短退避会连续 402**。解法：`wait = 12 + a*10` 秒长退避 + 每次换 seed + 固定 `model=flux`。
2. **LLM 输出要抠 JSON**：用 `_extract_json()` 从返回文本里正则抓 `[...]` 数组，兼容 markdown 代码块包裹。
3. **ffmpeg 字幕路径转义**：Windows 下 `subtitles=` 滤镜里的 `:` 和 `\` 必须转义，否则滤镜图解析失败。
4. **分镜图 1280×720 但成片 1024×576**： pollinations 按请求尺寸出图， concat 时以首片尺寸为准，保持一致即可。

## 法律与版权边界

- 输入小说请使用**你自己拥有版权**或**已进入公有领域**的文本。
- 本工具只做"文本 → 画面"的自动化合成，不对生成内容的版权归属作任何担保。
- 免费图库/免费 TTS 的具体授权条款以对应服务商为准。

## 许可

MIT
