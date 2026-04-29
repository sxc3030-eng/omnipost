# 🚀 OmniPost — All-in-One Social Media Marketing Tool

<div align="center">

![OmniPost](https://img.shields.io/badge/OmniPost-v1.0.0-5b8fff?style=for-the-badge&logo=rocket&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10+-3dffb4?style=for-the-badge&logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-aa66ff?style=for-the-badge)
![Windows](https://img.shields.io/badge/Windows-11-0078d4?style=for-the-badge&logo=windows&logoColor=white)
![Local](https://img.shields.io/badge/100%25-Local-00ffaa?style=for-the-badge)

**Smart content creation • Viral scoring • Competitor analysis • Video maker • AI-powered**

[Quick Start](#-quick-start) • [Features](#-features) • [Installation](#-installation) • [Docs](#-documentation)

</div>

---

## ✨ Features

### ✍️ Smart Composer
- Write once, copy for every platform in 1 click
- **Subtitle support** — YouTube titles, Facebook hooks, TikTok captions
- **Text effects** — Bold 𝗕, Italic 𝘐, CAPS, 🔥 Emoji boost, ★ Stars, → Arrows, ① Numbered lists
- **Auto-open** — copies text AND opens the platform automatically
- Hashtag suggestions + tag library

### 🔥 Viral Score & A/B Testing
- Real-time viral score (0–100) as you type
- Analyzes: emojis, CTA, questions, hook words, length, hashtags
- **A/B Test** — write two versions, OmniPost picks the winner

### 🎨 Visual Creator
- **Quote generator** — 5 styles (Dark, Gradient, Minimal, Neon, Warm) × 3 formats (Square, Story, Landscape)
- **Carousel maker** — enter bullet points → get Instagram/LinkedIn slides
- Download PNG directly

### ✍️ Hook Library (50+ hooks)
- Categories: Curiosity, Fear/Urgency, Benefit, Story, Question, Controversy, Numbers, Secrets
- Click to insert directly in the composer

### ♻️ Content Recycler
- Take an old published post → generate 4 new versions (TikTok, Instagram, Facebook, Twitter)
- Claude AI adapts tone and format for each platform

### 👤 Bio Optimizer + Link in Bio
- Generate 3 optimized bios per platform with Claude AI
- Link in Bio manager → generates a downloadable HTML page

### 🔍 Competitor Analyzer
- Paste any URL → full analysis in 10 seconds
- SEO score, performance, 35+ technologies, security grade, e-commerce signals
- Traffic estimation, social profiles, keyword cloud
- Export report as TXT

### 🎬 Video Creator
- Slideshow from images + text overlay + music
- Optimizes output for TikTok (9:16), Reels, Shorts, YouTube (16:9), Facebook
- Requires FFmpeg

### ✨ AI Content Generator
- Powered by Claude (Anthropic)
- Generate platform-optimized posts with tone selection
- Auto-suggest improvements as you type
- 9 ready-to-use templates

### 📊 Analytics
- Track impressions, likes, comments, shares
- Per-platform breakdown with progress bars
- Demo data to preview the dashboard

### 💡 Tips & Best Times
- 19 platform-specific tips (Instagram, TikTok, Facebook, YouTube)
- Best posting times per platform shown in real-time

---

## 🔧 Installation

### Requirements
- Python 3.10+
- Windows 10/11
- `pip install websockets`

### Quick Start

```bash
# 1. Clone
git clone https://github.com/sxc3030-eng/omnipost.git
cd omnipost

# 2. Install
pip install websockets

# 3. Launch
python omnipost.py
```

The dashboard opens automatically in your browser.

### Optional: Competitor Analyzer (separate backend)

```bash
# In a second terminal
python competitor_analyzer.py
```

Then click **🔍 Concurrent** in the sidebar.

### Optional: Video Creator (requires FFmpeg)

Download FFmpeg from [ffmpeg.org](https://ffmpeg.org/download.html) and add it to your PATH.

### Optional: AI Features (requires Claude API key)

Get a free API key at [console.anthropic.com](https://console.anthropic.com), then enter it in **⚙️ Settings**.

---

## 🚀 Quick Start

```powershell
# Windows — double-click LANCER_OMNIPOST.bat
# Or from terminal:
python omnipost.py
```

**Ports used:**
| Service | Port |
|---|---|
| OmniPost WebSocket | 8860 |
| OmniPost Auth | 8861 |
| Competitor Analyzer | 8870 |

---

## 📁 Files

```
omnipost.py                   Main backend (WebSocket server)
omnipost_dashboard.html       Main dashboard
competitor_analyzer.py        Competitor analysis backend
competitor_analyzer.html      Competitor analysis dashboard
LANCER_OMNIPOST.bat           Windows launcher
LANCER_COMPETITOR.bat         Competitor analyzer launcher
BUILD_EXE.bat                 Build .exe with PyInstaller
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                    OmniPost v1.0                    │
├──────────────┬──────────────────┬───────────────────┤
│  Python      │  WebSocket       │  Competitor       │
│  Backend     │  :8860           │  Analyzer :8870   │
│  omnipost.py │  asyncio         │  competitor_*.py  │
├──────────────┴──────────────────┴───────────────────┤
│              HTML / JS Dashboard                    │
│  Composer • Viral Score • A/B • Visuals • Hooks    │
│  Recycler • Bio • Analytics • Video • Competitor   │
└─────────────────────────────────────────────────────┘
```

**Stack:** Python 3.10+ • websockets • asyncio • Canvas API • Claude API (optional)

---

## 📱 Supported Platforms

| Platform | Copy | Schedule | Direct Post |
|---|---|---|---|
| TikTok | ✅ | ✅ | API required |
| Instagram | ✅ | ✅ | Meta API required |
| Facebook | ✅ | ✅ | Meta API required |
| YouTube | ✅ | ✅ | Google API required |
| Pinterest | ✅ | ✅ | Pinterest API required |
| Twitter/X | ✅ | ✅ | Twitter API required |
| LinkedIn | ✅ | ✅ | LinkedIn API required |

> **Smart Copy mode**: OmniPost formats your content for each platform and copies it to clipboard, then opens the platform automatically. No API key required.

---

## ⚠️ Legal

OmniPost is for **personal and commercial use on your own accounts**.
Do not use to spam, violate platform terms of service, or automate interactions.

---

## 📄 License

MIT License — Free, open source, modifiable.

---

## 👤 Author

**sxc3030-eng**
🔗 [github.com/sxc3030-eng/omnipost](https://github.com/sxc3030-eng/omnipost)

---

### Method

Architecture-first, AI-paired. Built over **5 weeks (March-April 2026)** with **Claude (Opus 4.6)** as paired implementation and audit partner. Each commit cross-audited: code review, dependency check, UX pass on the multi-platform composer and viral-score engine. Claude API powers the optional AI content generator and competitor-analysis backends.

---

<div align="center">

⭐ **If OmniPost saves you time, please star it on GitHub!** ⭐

</div>
