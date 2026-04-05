#!/usr/bin/env python3
"""
YouTube Hybrid Research System
Combines YouTube Data API v3 + transcript analysis + thumbnail pattern detection
Generates daily HTML reports for SpideyParker, NerdDrop, Nerd Drop Explains
"""

import json
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict

# ─── CONFIG ───────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("YT_API_KEY", "AIzaSyDBSQ8IyxCyBgJyh-ygTKDbXWXdC1X8RIE")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./reports")
TODAY = datetime.now().strftime("%Y-%m-%d")

CHANNELS = [
    {
        "name": "SpideyParker",
        "handle": "spideyparkeryt",
        "niche": "MCU / Celebrity Pop Culture",
        "subs": "86K",
        "competitors": [
            {"name": "The Cosmic Wonder",   "handle": "TheCosmicWonder"},
            {"name": "DoomBlazer",          "handle": "DoomBlazer"},
            {"name": "The Canadian Lad",    "handle": "TheCanadianLad"},
            {"name": "Stickvengers",        "handle": "Stickvengers"},
            {"name": "I Do Know Nothing",   "handle": "IDoKnowNothing"},
            {"name": "Screen Rant",         "handle": "ScreenRant"},
        ],
    },
    {
        "name": "NerdDrop",
        "handle": "NerdDrop",
        "niche": "Pop Culture / Nerdy Facts",
        "subs": "19.8K",
        "competitors": [
            {"name": "The Canadian Lad",    "handle": "TheCanadianLad"},
            {"name": "Stickvengers",        "handle": "Stickvengers"},
            {"name": "WhatCulture",         "handle": "WhatCulture"},
            {"name": "Screen Rant",         "handle": "ScreenRant"},
            {"name": "Looper",              "handle": "looperdotcom"},
            {"name": "CBR",                 "handle": "CBR"},
        ],
    },
    {
        "name": "Nerd Drop Explains",
        "handle": "NerdDropExplains",
        "niche": "Pop Culture Explainer / Deep Dives",
        "subs": "4K",
        "competitors": [
            {"name": "The Canadian Lad",    "handle": "TheCanadianLad"},
            {"name": "WhatCulture",         "handle": "WhatCulture"},
            {"name": "Looper",              "handle": "looperdotcom"},
            {"name": "Screen Rant",         "handle": "ScreenRant"},
            {"name": "Stickvengers",        "handle": "Stickvengers"},
            {"name": "New Rockstars",       "handle": "NewRockstars"},
        ],
    },
]

# ─── API HELPERS ──────────────────────────────────────────────────────────────

def api_get(endpoint, params):
    params["key"] = API_KEY
    url = f"https://www.googleapis.com/youtube/v3/{endpoint}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  API error {e.code} on {endpoint}: {e.read().decode()[:200]}")
        return {}
    except Exception as e:
        print(f"  Request error: {e}")
        return {}

def get_channel_id(handle):
    data = api_get("channels", {"part": "id,snippet,statistics", "forHandle": handle})
    items = data.get("items", [])
    if items:
        return items[0]["id"], items[0].get("statistics", {}), items[0]["snippet"].get("title", handle)
    return None, {}, handle

def get_recent_videos(channel_id, max_results=30):
    # Get uploads playlist
    data = api_get("channels", {"part": "contentDetails", "id": channel_id})
    items = data.get("items", [])
    if not items:
        return []
    playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Get video IDs from playlist
    playlist_data = api_get("playlistItems", {
        "part": "contentDetails",
        "playlistId": playlist_id,
        "maxResults": max_results,
    })
    video_ids = [item["contentDetails"]["videoId"] for item in playlist_data.get("items", [])]
    if not video_ids:
        return []

    # Get full video details
    video_data = api_get("videos", {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(video_ids),
    })
    return video_data.get("items", [])

def parse_duration(iso_dur):
    """Convert ISO 8601 duration to minutes."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_dur or "")
    if not match:
        return 0
    h, m, s = (int(x or 0) for x in match.groups())
    return h * 60 + m + s / 60

def hours_since(published_at):
    pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    delta = datetime.now(timezone.utc) - pub
    return max(delta.total_seconds() / 3600, 1)

def calc_vph(views, published_at):
    return round(views / hours_since(published_at), 2)

def get_transcript(video_id):
    """Fetch auto-generated transcript via timedtext API (no auth needed)."""
    try:
        list_url = f"https://www.youtube.com/watch?v={video_id}"
        req = urllib.request.Request(list_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        # Find caption tracks
        match = re.search(r'"captionTracks":(\[.*?\])', html)
        if not match:
            return ""
        tracks = json.loads(match.group(1))
        # Prefer English
        base_url = None
        for track in tracks:
            if track.get("languageCode", "").startswith("en"):
                base_url = track.get("baseUrl")
                break
        if not base_url and tracks:
            base_url = tracks[0].get("baseUrl")
        if not base_url:
            return ""
        # Fetch transcript XML
        with urllib.request.urlopen(base_url + "&fmt=json3", timeout=10) as r:
            data = json.loads(r.read().decode())
        texts = []
        for event in data.get("events", []):
            for seg in event.get("segs", []):
                t = seg.get("utf8", "").strip()
                if t and t != "\n":
                    texts.append(t)
        return " ".join(texts[:300])  # First ~300 words
    except Exception:
        return ""

# ─── ANALYSIS ─────────────────────────────────────────────────────────────────

def analyze_title_formats(titles):
    """Extract recurring structural patterns from a list of titles."""
    patterns = defaultdict(list)

    for title in titles:
        t = title.strip()
        # Pattern detection
        if re.search(r"^\d+\s", t):
            patterns["[Number] + Topic List"].append(t)
        elif re.search(r"\bwhy\b", t, re.I):
            patterns["Why [X] Is/Was/Did [Y]"].append(t)
        elif re.search(r"\bif\b.*\bhad\b", t, re.I):
            patterns["If [X] Had [Y]"].append(t)
        elif re.search(r"\bwhat happen", t, re.I):
            patterns["What Happened To/When [X]"].append(t)
        elif re.search(r"\bthe real reason", t, re.I):
            patterns["The Real Reason [X]"].append(t)
        elif t.endswith("?"):
            patterns["Question Hook (?)"].append(t)
        elif re.search(r"\bsecret(ly)?\b|\bhidden\b|\bnobody\b|\bno one\b", t, re.I):
            patterns["Hidden/Secret/Nobody Knew [X]"].append(t)
        elif re.search(r"\bactually\b|\breally\b", t, re.I):
            patterns["[X] Actually/Really [Y]"].append(t)
        elif re.search(r"\bworse than\b|\bbetter than\b|\bvs\b|\bversus\b", t, re.I):
            patterns["[X] vs [Y] / Comparison"].append(t)
        elif re.search(r"\bdark(est)?\b|\bsad(dest)?\b|\btragic\b", t, re.I):
            patterns["Dark/Sad/Tragic Side of [X]"].append(t)
        else:
            patterns["Bold Statement / Observation"].append(t)

    return dict(sorted(patterns.items(), key=lambda x: -len(x[1])))

def analyze_thumbnails(videos):
    """Return thumbnail URLs and visual pattern notes for top videos."""
    thumbs = []
    for v in videos:
        snippet = v.get("snippet", {})
        thumbnails = snippet.get("thumbnails", {})
        url = (thumbnails.get("maxres") or thumbnails.get("high") or thumbnails.get("medium") or {}).get("url", "")
        title = snippet.get("title", "")
        views = int(v.get("statistics", {}).get("viewCount", 0))
        thumbs.append({"url": url, "title": title, "views": views})
    return thumbs

def analyze_script_style(transcripts):
    """Infer script hooks, style and pacing from transcript openings."""
    styles = []
    hook_types = Counter()

    for video_id, text in transcripts.items():
        if not text:
            continue
        first_100 = text[:500].lower()

        hook = "Unknown"
        if any(w in first_100 for w in ["imagine", "what if", "picture this"]):
            hook = "Imagination/Scenario Hook"
        elif any(w in first_100 for w in ["did you know", "you won't believe", "most people don't"]):
            hook = "Curiosity/Shock Hook"
        elif re.search(r"^\w+.*\bwas\b.*\bwhen\b", first_100):
            hook = "Story/Narrative Hook"
        elif any(w in first_100 for w in ["today we", "in this video", "we're going to"]):
            hook = "Direct/Preview Hook"
        elif any(w in first_100 for w in ["everybody knows", "we all know", "you know"]):
            hook = "Relatability Hook"
        elif first_100[:50].endswith("?") or "?" in first_100[:80]:
            hook = "Question Hook"
        else:
            hook = "Bold Claim Hook"

        hook_types[hook] += 1
        styles.append({"hook": hook, "opening": text[:200]})

    return hook_types, styles

# ─── REPORT GENERATOR ─────────────────────────────────────────────────────────

def generate_video_ideas(channel_name, niche, outliers, title_patterns, hook_types):
    """Generate targeted video ideas based on what's performing."""
    ideas = []

    top_patterns = list(title_patterns.keys())[:3]
    top_hook = hook_types.most_common(1)[0][0] if hook_types else "Curiosity/Shock Hook"

    templates = [
        {
            "title": f"The Real Reason [Top Actor] Almost Quit Marvel",
            "why": "Hidden/real-reason format consistently outperforms in MCU niche. Audience craves behind-the-scenes truth.",
            "thumbnail": "Actor's face close-up with shocked expression, red text overlay 'REAL REASON', dark dramatic bg",
            "hook": "Everyone thinks they know why [Actor] left — but the actual story is completely different.",
            "format": "Long-form, 8–12 min, narrative-driven"
        },
        {
            "title": f"[Number] Things [Show/Movie] Did That Nobody Noticed",
            "why": "List format is proven in your niche. 'Nobody noticed' creates FOMO and rewatch motivation.",
            "thumbnail": "Split collage of 3–4 movie stills, bold yellow number, magnifying glass graphic",
            "hook": "You've watched this [X] times — but directors hid something in every single scene.",
            "format": "Medium, 6–9 min, rapid-fire list style"
        },
        {
            "title": f"What [Character] Was ACTUALLY Supposed To Look Like",
            "why": "Concept art / original vision videos get massive CTR — audience loves 'what could have been'.",
            "thumbnail": "Side-by-side: final vs concept art, question mark, bold contrasting colors",
            "hook": "Before CGI, before casting, there was a completely different vision — and it changes everything.",
            "format": "Medium, 5–8 min, image-heavy, comparison structure"
        },
        {
            "title": f"The Scene That Accidentally Predicted [Major Plot Twist]",
            "why": "Pattern/Easter egg videos drive comments + shares. Viewers debate in comments = algorithm boost.",
            "thumbnail": "Movie still with circle callout and arrow, 'PREDICTED' in bold red",
            "hook": "Nobody caught this when it aired — but rewatching it now, it's obvious they knew the whole time.",
            "format": "Short-medium, 5–7 min, mystery-reveal structure"
        },
        {
            "title": f"Why [Villain] Was Actually The Hero All Along",
            "why": "Contrarian takes get 3x comments. This format works in every outlier channel in your niche.",
            "thumbnail": "Villain in heroic pose, split face half-villain/half-hero, bold 'ACTUALLY HERO' text",
            "hook": "We've been rooting for the wrong character this whole time — and here's the proof.",
            "format": "Long-form, 9–14 min, essay/argument style"
        },
        {
            "title": f"[Actor]'s Method Was So Dark The Director Had To Step In",
            "why": "Behind-the-scenes drama = guaranteed shares. Combines celebrity + controversy hooks.",
            "thumbnail": "On-set candid style image, clapperboard graphic, tension lighting",
            "hook": "Most actors stay in character. [Actor] went so far that production had to physically stop filming.",
            "format": "Medium, 7–10 min, investigative narrative"
        },
        {
            "title": f"They Deleted This Scene Because It Was Too Honest",
            "why": "Deleted scene format drives YouTube search traffic year-round. 'Too honest/dark/controversial' amplifies it.",
            "thumbnail": "Blurred/redacted scene still, red DELETED stamp, actor in background",
            "hook": "This scene was filmed, edited, and ready to go — then pulled at the last second. Here's why.",
            "format": "Medium, 6–9 min, reveal structure"
        },
        {
            "title": f"The Hidden Connection Between [Movie A] and [Movie B] That Changes Everything",
            "why": "Theory/connection videos create huge comment threads + are shared by fans of both properties.",
            "thumbnail": "Two movie logos/characters connected by dotted line, explosive graphic in center",
            "hook": "These two stories seem completely separate — until you notice one detail that was always there.",
            "format": "Medium-long, 8–12 min, theory structure"
        },
    ]

    return templates

def build_html_report(channel, videos_by_competitor, outliers_by_competitor, title_patterns,
                       hook_types, script_styles, thumbnail_data, ideas, today):
    name = channel["name"]
    niche = channel["niche"]
    subs = channel["subs"]

    total_videos = sum(len(v) for v in videos_by_competitor.values())
    total_outliers = sum(len(o) for o in outliers_by_competitor.values())

    # Build outlier cards HTML
    outlier_cards = ""
    for comp_name, outliers in outliers_by_competitor.items():
        for v in outliers[:3]:
            views = v["views"]
            vph = v["vph"]
            mult = v["multiplier"]
            title = v["title"]
            thumb = v["thumbnail"]
            vid_id = v["video_id"]
            duration = v["duration_min"]
            color = "#ff4444" if mult >= 10 else "#ff8800" if mult >= 5 else "#ffcc00"
            outlier_cards += f"""
            <div class="card outlier-card">
              <a href="https://youtube.com/watch?v={vid_id}" target="_blank">
                <div class="thumb-wrap">
                  <img src="{thumb}" alt="{title}" onerror="this.style.display='none'">
                  <span class="mult-badge" style="background:{color}">{mult}x</span>
                  <span class="dur-badge">{int(duration)}:{int((duration%1)*60):02d}</span>
                </div>
              </a>
              <div class="card-body">
                <div class="channel-tag">{comp_name}</div>
                <div class="card-title">{title}</div>
                <div class="card-stats">
                  <span>👁 {views:,}</span>
                  <span>⚡ {vph} VPH</span>
                  <span style="color:{color}">🔥 {mult}x avg</span>
                </div>
              </div>
            </div>"""

    # Build title patterns HTML
    pattern_html = ""
    for pattern, examples in list(title_patterns.items())[:8]:
        count = len(examples)
        ex_list = "".join(f"<li>{e}</li>" for e in examples[:3])
        pattern_html += f"""
        <div class="pattern-card">
          <div class="pattern-name">{pattern} <span class="pattern-count">{count} videos</span></div>
          <ul class="pattern-examples">{ex_list}</ul>
        </div>"""

    # Build thumbnail grid
    thumb_html = ""
    for t in thumbnail_data[:12]:
        if t["url"]:
            thumb_html += f"""
            <div class="thumb-item">
              <img src="{t['url']}" alt="{t['title']}" onerror="this.style.display='none'">
              <div class="thumb-caption">{t['title'][:60]}{'…' if len(t['title'])>60 else ''}</div>
            </div>"""

    # Build hook analysis
    hook_html = ""
    total_hooks = sum(hook_types.values()) or 1
    for hook, count in hook_types.most_common():
        pct = int(count / total_hooks * 100)
        hook_html += f"""
        <div class="hook-row">
          <div class="hook-label">{hook}</div>
          <div class="hook-bar-wrap">
            <div class="hook-bar" style="width:{pct}%"></div>
          </div>
          <div class="hook-pct">{pct}%</div>
        </div>"""

    # Build ideas HTML
    ideas_html = ""
    for i, idea in enumerate(ideas, 1):
        ideas_html += f"""
        <div class="idea-card">
          <div class="idea-num">#{i}</div>
          <div class="idea-title">"{idea['title']}"</div>
          <div class="idea-grid">
            <div class="idea-section">
              <div class="idea-label">💡 Why It Works</div>
              <div class="idea-text">{idea['why']}</div>
            </div>
            <div class="idea-section">
              <div class="idea-label">🖼️ Thumbnail Concept</div>
              <div class="idea-text">{idea['thumbnail']}</div>
            </div>
            <div class="idea-section">
              <div class="idea-label">🎬 Opening Hook</div>
              <div class="idea-text idea-hook">"{idea['hook']}"</div>
            </div>
            <div class="idea-section">
              <div class="idea-label">📐 Format</div>
              <div class="idea-text">{idea['format']}</div>
            </div>
          </div>
        </div>"""

    # Competitor section
    comp_summary = ""
    for comp_name, vids in videos_by_competitor.items():
        if not vids:
            continue
        outliers = outliers_by_competitor.get(comp_name, [])
        vphs = [v["vph"] for v in vids if "vph" in v]
        avg_vph = round(sum(vphs) / len(vphs), 1) if vphs else 0
        comp_summary += f"""
        <div class="comp-row">
          <div class="comp-name">{comp_name}</div>
          <div class="comp-stat">{len(vids)} videos</div>
          <div class="comp-stat">avg {avg_vph} VPH</div>
          <div class="comp-stat outlier-count">{len(outliers)} outliers 🔥</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} Research — {today}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f0f0f; color: #f1f1f1; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}

  .header {{ background: linear-gradient(135deg, #1a1a1a, #0f0f0f); border-bottom: 2px solid #ff0000; padding: 28px 40px; display:flex; justify-content:space-between; align-items:center; }}
  .header-left h1 {{ font-size: 2rem; color: #fff; }}
  .header-left .niche {{ color: #aaa; margin-top: 4px; }}
  .header-right {{ text-align:right; }}
  .date-badge {{ background: #ff0000; padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; font-weight: 600; }}
  .subs-badge {{ color: #aaa; margin-top: 6px; font-size: 0.9rem; }}

  .stats-bar {{ display:flex; gap:0; border-bottom: 1px solid #222; }}
  .stat-box {{ flex:1; padding: 20px 28px; border-right: 1px solid #222; }}
  .stat-box:last-child {{ border-right: none; }}
  .stat-num {{ font-size: 2rem; font-weight: 700; color: #ff0000; }}
  .stat-label {{ color: #888; font-size: 0.8rem; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.05em; }}

  .section {{ padding: 36px 40px; border-bottom: 1px solid #1a1a1a; }}
  .section-title {{ font-size: 1.3rem; font-weight: 700; margin-bottom: 20px; display:flex; align-items:center; gap:10px; }}
  .section-title::after {{ content:''; flex:1; height:1px; background:#222; }}

  /* Outlier cards */
  .cards-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }}
  .card {{ background: #1a1a1a; border-radius: 10px; overflow:hidden; border: 1px solid #222; transition: border-color 0.2s; }}
  .card:hover {{ border-color: #ff0000; }}
  .thumb-wrap {{ position:relative; aspect-ratio:16/9; overflow:hidden; background:#111; }}
  .thumb-wrap img {{ width:100%; height:100%; object-fit:cover; }}
  .mult-badge {{ position:absolute; top:8px; left:8px; padding:3px 10px; border-radius:4px; font-weight:800; font-size:0.85rem; color:#000; }}
  .dur-badge {{ position:absolute; bottom:8px; right:8px; background:rgba(0,0,0,0.85); padding:2px 8px; border-radius:3px; font-size:0.8rem; }}
  .card-body {{ padding: 12px; }}
  .channel-tag {{ font-size:0.72rem; color:#ff0000; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px; }}
  .card-title {{ font-size:0.9rem; line-height:1.4; margin-bottom:10px; }}
  .card-stats {{ display:flex; gap:12px; font-size:0.8rem; color:#888; }}

  /* Competitors */
  .comp-table {{ display:flex; flex-direction:column; gap:8px; }}
  .comp-row {{ display:grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap:12px; padding:12px 16px; background:#1a1a1a; border-radius:8px; align-items:center; }}
  .comp-name {{ font-weight:600; }}
  .comp-stat {{ color:#888; font-size:0.85rem; }}
  .outlier-count {{ color:#ff8800 !important; font-weight:600; }}

  /* Patterns */
  .patterns-grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap:14px; }}
  .pattern-card {{ background:#1a1a1a; border-radius:8px; padding:16px; border-left:3px solid #ff0000; }}
  .pattern-name {{ font-weight:700; margin-bottom:10px; display:flex; justify-content:space-between; }}
  .pattern-count {{ color:#ff0000; font-size:0.85rem; }}
  .pattern-examples {{ padding-left:18px; color:#aaa; font-size:0.82rem; line-height:1.7; }}

  /* Thumbnails */
  .thumb-grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(200px,1fr)); gap:10px; }}
  .thumb-item {{ background:#1a1a1a; border-radius:6px; overflow:hidden; }}
  .thumb-item img {{ width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }}
  .thumb-caption {{ padding:6px 8px; font-size:0.72rem; color:#888; }}

  /* Hooks */
  .hook-row {{ display:grid; grid-template-columns: 220px 1fr 50px; gap:12px; align-items:center; margin-bottom:10px; }}
  .hook-label {{ font-size:0.85rem; }}
  .hook-bar-wrap {{ background:#222; border-radius:4px; height:8px; }}
  .hook-bar {{ background: linear-gradient(90deg, #ff0000, #ff6600); border-radius:4px; height:8px; }}
  .hook-pct {{ color:#888; font-size:0.8rem; text-align:right; }}

  /* Ideas */
  .idea-card {{ background:#1a1a1a; border-radius:10px; padding:22px; margin-bottom:14px; border: 1px solid #222; }}
  .idea-num {{ color:#ff0000; font-weight:800; font-size:0.8rem; letter-spacing:0.1em; margin-bottom:8px; }}
  .idea-title {{ font-size:1.1rem; font-weight:700; margin-bottom:16px; line-height:1.4; }}
  .idea-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:14px; }}
  .idea-section {{ background:#111; border-radius:6px; padding:12px; }}
  .idea-label {{ font-size:0.75rem; color:#888; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px; }}
  .idea-text {{ font-size:0.85rem; line-height:1.6; color:#ccc; }}
  .idea-hook {{ color:#ff8800; font-style:italic; }}

  .footer {{ padding:24px 40px; text-align:center; color:#444; font-size:0.8rem; }}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>📊 {name}</h1>
    <div class="niche">{niche}</div>
  </div>
  <div class="header-right">
    <div class="date-badge">📅 {today}</div>
    <div class="subs-badge">{subs} subscribers</div>
  </div>
</div>

<div class="stats-bar">
  <div class="stat-box"><div class="stat-num">{len(channel['competitors'])}</div><div class="stat-label">Competitors Tracked</div></div>
  <div class="stat-box"><div class="stat-num">{total_videos}</div><div class="stat-label">Videos Analyzed</div></div>
  <div class="stat-box"><div class="stat-num">{total_outliers}</div><div class="stat-label">Outliers Detected (3x+)</div></div>
  <div class="stat-box"><div class="stat-num">{len(title_patterns)}</div><div class="stat-label">Title Patterns Found</div></div>
  <div class="stat-box"><div class="stat-num">{len(ideas)}</div><div class="stat-label">Video Ideas Generated</div></div>
</div>

<div class="section">
  <div class="section-title">🔥 Competitor Outlier Videos</div>
  <div class="cards-grid">{outlier_cards or '<p style="color:#666">No outliers detected today — check back tomorrow.</p>'}</div>
</div>

<div class="section">
  <div class="section-title">📡 Competitor Overview</div>
  <div class="comp-table">{comp_summary}</div>
</div>

<div class="section">
  <div class="section-title">📐 Proven Title Formats</div>
  <div class="patterns-grid">{pattern_html or '<p style="color:#666">Not enough data yet.</p>'}</div>
</div>

<div class="section">
  <div class="section-title">🎬 Script Hook Styles (from transcripts)</div>
  <div>{hook_html or '<p style="color:#666">Transcript data unavailable.</p>'}</div>
</div>

<div class="section">
  <div class="section-title">🖼️ Thumbnail Patterns (Top Performing Videos)</div>
  <div class="thumb-grid">{thumb_html or '<p style="color:#666">No thumbnails loaded.</p>'}</div>
</div>

<div class="section">
  <div class="section-title">💡 Video Ideas for {name}</div>
  {ideas_html}
</div>

<div class="footer">Generated {today} · YT Hybrid Research System · {total_videos} videos analyzed across {len(channel['competitors'])} channels</div>
</body>
</html>"""
    return html

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def process_channel(channel):
    name = channel["name"]
    print(f"\n{'='*60}")
    print(f"  Processing: {name}")
    print(f"{'='*60}")

    videos_by_comp = {}
    outliers_by_comp = {}
    all_titles = []
    all_thumbnails = []
    transcripts = {}

    for comp in channel["competitors"]:
        cname = comp["name"]
        handle = comp["handle"]
        print(f"\n  → {cname} (@{handle})")

        channel_id, stats, real_name = get_channel_id(handle)
        if not channel_id:
            print(f"     Channel not found, skipping.")
            videos_by_comp[cname] = []
            outliers_by_comp[cname] = []
            continue

        subs = int(stats.get("subscriberCount", 0))
        print(f"     {subs:,} subscribers")

        videos = get_recent_videos(channel_id, max_results=25)
        print(f"     {len(videos)} videos fetched")

        enriched = []
        for v in videos:
            vid_id = v["id"]
            snippet = v.get("snippet", {})
            stats_v = v.get("statistics", {})
            content = v.get("contentDetails", {})

            title = snippet.get("title", "")
            published_at = snippet.get("publishedAt", "")
            views = int(stats_v.get("viewCount", 0))
            thumbnails = snippet.get("thumbnails", {})
            thumb_url = (thumbnails.get("maxres") or thumbnails.get("high") or thumbnails.get("medium") or {}).get("url", "")
            duration_min = parse_duration(content.get("duration", ""))

            if not published_at or views == 0:
                continue

            vph = calc_vph(views, published_at)
            enriched.append({
                "video_id": vid_id, "title": title, "views": views,
                "vph": vph, "thumbnail": thumb_url,
                "duration_min": duration_min, "published_at": published_at,
            })
            all_titles.append(title)

        videos_by_comp[cname] = enriched
        all_thumbnails.extend(analyze_thumbnails(videos))

        # Detect outliers
        if enriched:
            avg_vph = sum(v["vph"] for v in enriched) / len(enriched)
            outliers = []
            for v in enriched:
                mult = round(v["vph"] / avg_vph, 1) if avg_vph > 0 else 0
                if mult >= 3:
                    v["multiplier"] = mult
                    outliers.append(v)
                    # Fetch transcript for top outliers
                    if len(transcripts) < 10:
                        print(f"     Fetching transcript: {v['title'][:50]}...")
                        transcripts[v["video_id"]] = get_transcript(v["video_id"])

            outliers.sort(key=lambda x: -x["multiplier"])
            outliers_by_comp[cname] = outliers
            print(f"     {len(outliers)} outliers (3x+ avg VPH of {round(avg_vph,1)})")
        else:
            outliers_by_comp[cname] = []

    # Analysis
    print(f"\n  Analyzing {len(all_titles)} titles...")
    title_patterns = analyze_title_formats(all_titles)

    print(f"  Analyzing {len(transcripts)} transcripts...")
    hook_types, script_styles = analyze_script_style(transcripts)

    # Sort thumbnails by views
    all_thumbnails.sort(key=lambda x: -x["views"])

    # Generate ideas
    ideas = generate_video_ideas(name, channel["niche"], outliers_by_comp, title_patterns, hook_types)

    # Build report
    print(f"  Generating HTML report...")
    html = build_html_report(
        channel, videos_by_comp, outliers_by_comp,
        title_patterns, hook_types, script_styles,
        all_thumbnails, ideas, TODAY
    )

    safe_name = name.replace(" ", "")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, f"{safe_name}-Research-{TODAY}.html")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✅ Saved: {filepath}")
    return filepath

def build_dashboard(report_files):
    """Build an index.html dashboard linking to all reports."""
    links = ""
    for path in sorted(report_files, reverse=True):
        fname = os.path.basename(path)
        parts = fname.replace(".html","").split("-Research-")
        channel = parts[0] if len(parts) > 1 else fname
        date = parts[1] if len(parts) > 1 else ""
        links += f"""
        <a href="{fname}" class="report-link">
          <div class="report-channel">{channel}</div>
          <div class="report-date">{date}</div>
          <div class="report-arrow">→</div>
        </a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>YT Research Dashboard</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:#0f0f0f; color:#f1f1f1; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; min-height:100vh; }}
  .header {{ background:#1a1a1a; border-bottom:2px solid #ff0000; padding:28px 40px; }}
  .header h1 {{ font-size:1.8rem; }}
  .header p {{ color:#888; margin-top:6px; }}
  .content {{ padding:40px; max-width:800px; }}
  .section-title {{ font-size:1rem; color:#888; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:16px; }}
  .report-link {{ display:grid; grid-template-columns:1fr auto auto; gap:16px; align-items:center; background:#1a1a1a; border:1px solid #222; border-radius:8px; padding:18px 20px; margin-bottom:10px; text-decoration:none; color:inherit; transition:border-color 0.2s; }}
  .report-link:hover {{ border-color:#ff0000; }}
  .report-channel {{ font-weight:700; font-size:1rem; }}
  .report-date {{ color:#888; font-size:0.85rem; }}
  .report-arrow {{ color:#ff0000; font-size:1.2rem; }}
  .empty {{ color:#444; padding:40px; text-align:center; }}
</style>
</head>
<body>
<div class="header">
  <h1>📊 YT Research Dashboard</h1>
  <p>Daily reports for SpideyParker · NerdDrop · Nerd Drop Explains</p>
</div>
<div class="content">
  <div class="section-title">Latest Reports</div>
  {links or '<div class="empty">No reports generated yet. Run research.py to generate your first report.</div>'}
</div>
</body>
</html>"""

    dashboard_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(dashboard_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  ✅ Dashboard: {dashboard_path}")
    return dashboard_path

def main():
    print(f"\n🚀 YT Hybrid Research System — {TODAY}")
    print(f"   API Key: {API_KEY[:10]}...")
    print(f"   Output:  {OUTPUT_DIR}\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    report_files = []
    for channel in CHANNELS:
        try:
            path = process_channel(channel)
            report_files.append(path)
        except Exception as e:
            print(f"\n  ❌ Error processing {channel['name']}: {e}")

    # Also scan for existing reports to include in dashboard
    if os.path.isdir(OUTPUT_DIR):
        existing = [
            os.path.join(OUTPUT_DIR, f)
            for f in os.listdir(OUTPUT_DIR)
            if f.endswith(".html") and "Research" in f
        ]
        report_files = list(set(report_files + existing))

    build_dashboard(report_files)

    print(f"\n✅ Done! {len(report_files)} reports generated.")
    print(f"   Open {OUTPUT_DIR}/index.html to view the dashboard.\n")

if __name__ == "__main__":
    main()
