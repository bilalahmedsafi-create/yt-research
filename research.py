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
    """Generate channel-specific video ideas tailored to each channel's style and niche."""

    # ── SpideyParker: MCU / Celebrity — dramatic, actor-driven, behind-the-scenes ──
    if channel_name == "SpideyParker":
        return [
            {
                "title": "The Real Reason [Top Actor] Almost Quit Marvel — And Nobody Talks About It",
                "why": "Hidden/real-reason format is the #1 outlier driver in MCU niche. Your audience wants insider truth, not press-release stories. Pairs perfectly with your celebrity angle.",
                "thumbnail": "Actor face extreme close-up, shocked or uncomfortable expression. Bold red 'REAL REASON' text, dark vignette bg. High contrast — no clutter.",
                "hook": "The official story is that [Actor] loved the role. But behind the scenes, there were three moments where they almost walked away for good.",
                "format": "Long-form, 9–13 min, investigative narrative. Build with timeline, quotes, and verified on-set reports."
            },
            {
                "title": "[Actor]'s On-Set Behavior Was So Bad Marvel Quietly Fired Them",
                "why": "Celebrity controversy within Marvel hits all your audience's triggers: MCU fandom + gossip. 'Quietly' creates intrigue. High comment volume = algorithm boost.",
                "thumbnail": "Actor mid-scene, 'FIRED' stamped across image in red, clapperboard + Marvel logo in corner. Dark moody tone.",
                "hook": "Marvel has fired two people quietly in the last four years. One of them has never been reported — until now.",
                "format": "Medium-long, 8–11 min. Use 'leaked', 'sources say' framing. End on a cliffhanger theory."
            },
            {
                "title": "The Scene Marvel Shot But Will Never Release — And Why",
                "why": "Deleted/hidden content is evergreen search traffic. 'Will never release' raises the stakes beyond a normal deleted scene video.",
                "thumbnail": "Blurred/censored movie still, bold red CLASSIFIED stamp, Marvel logo ghosted in background.",
                "hook": "This scene exists. It was fully filmed, fully edited, and then buried — and the reason they won't release it is uncomfortable.",
                "format": "Long-form, 10–14 min. Narrative structure: set up the scene → what it revealed → why it was cut."
            },
            {
                "title": "What [Movie] Was Actually About (And Marvel Hoped You Wouldn't Notice)",
                "why": "Subtext/meaning deep dives get massive watch time — audiences feel smart finishing them. 'Marvel hoped you wouldn't notice' adds a conspiratorial edge.",
                "thumbnail": "Movie poster with 'HIDDEN MEANING' text overlay, magnifying glass icon, muted but bold palette.",
                "hook": "On the surface this is a superhero movie. But every major scene is about something else entirely — and once you see it, you can't unsee it.",
                "format": "Long-form essay, 11–16 min. Analysis format: scene breakdown → real-world theme → why it was made this way."
            },
            {
                "title": "[Celebrity]'s Public Image vs. What Co-Stars Actually Say",
                "why": "The contrast format (public vs. private) creates tension that keeps viewers watching. Co-star quotes are more credible than rumor — keeps it safe and still viral.",
                "thumbnail": "Split image: polished PR photo left, candid or on-set photo right. 'VS.' in bold center. Contrasting warm/cold color grading.",
                "hook": "Everything you think you know about [Celebrity] comes from their publicist. Here's what people who actually worked with them say.",
                "format": "Medium, 8–12 min. Present quotes, interviews, behind-the-scenes accounts chronologically."
            },
            {
                "title": "[Number] Marvel Decisions That Were Supposed to Be Different — And Why They Changed",
                "why": "Production change videos get shared by hardcore fans AND casual viewers. List format = easy watch time. 'Why they changed' adds depth beyond a standard list.",
                "thumbnail": "Collage of character/movie stills with X-marks on rejected versions, bold number in corner.",
                "hook": "The Marvel we got was plan B. In every single phase, the original plan would have changed everything.",
                "format": "Long-form list, 10–15 min. Each item: original plan → what changed → how it affected the story."
            },
            {
                "title": "The Dark Side of [Fan-Favourite Character] Marvel Doesn't Want You To Think About",
                "why": "Reframing beloved characters is one of the highest-CTR formats in fan content. Forces viewers to engage emotionally — they'll either agree or argue in comments.",
                "thumbnail": "Fan-favourite character in shadow, half-lit dramatic lighting, 'DARK SIDE' in bold. Emotional, not gimmicky.",
                "hook": "We love [Character]. But if you look at what they actually do — not what they say — the picture gets uncomfortable fast.",
                "format": "Essay/argument style, 9–13 min. Structure: establish the love → introduce the evidence → reframe → final take."
            },
            {
                "title": "Why [MCU Movie] Failed — And What Nobody Wants To Admit",
                "why": "Critical takes on underperforming MCU entries drive enormous discussion. 'Nobody wants to admit' positions you as brave and honest, not just another critic.",
                "thumbnail": "Movie logo/poster with cracked glass effect, downward arrow graphic, serious tone — not mocking.",
                "hook": "The reviews said it was fine. The box office disagreed. And the real reason it failed is something Marvel themselves have never acknowledged.",
                "format": "Long-form opinion/analysis, 10–14 min. Structure: surface-level explanations → dig deeper → the uncomfortable truth."
            },
        ]

    # ── NerdDrop: Pop Culture / Nerdy Facts — fun, punchy, list-driven ──
    elif channel_name == "NerdDrop":
        return [
            {
                "title": "[Number] Nerdy Facts About [Show/Movie] That Will Ruin It For You",
                "why": "'Ruin it for you' is a top-performing hook in pop culture fact lists — it's playfully threatening. Combines your nerdy-facts DNA with a curiosity punch.",
                "thumbnail": "Show/movie still with magnifying glass, bold number, yellow/red text. Fun energy — not scary, just cheeky.",
                "hook": "You love [Show]. You've watched it twice. These facts are going to make that third watch very different.",
                "format": "Long-form list, 8–12 min. Rapid-fire but with 2–3 sentences of context per fact — not just trivia dumps."
            },
            {
                "title": "The Nerdiest [Fandom] Facts Nobody Bothers To Explain",
                "why": "The 'nobody bothers to explain' angle targets your core audience: people who are curious but feel under-served by surface-level content. High rewatch + share rate.",
                "thumbnail": "Character or prop from the fandom, 'NERD FACTS' text in your channel's tone, clean background.",
                "hook": "Most [Fandom] content tells you what happened. This video tells you why it happened — and the answers are way weirder than you'd expect.",
                "format": "Medium-long, 7–11 min. Mix well-known facts with obscure ones to reward both new and hardcore fans."
            },
            {
                "title": "We Ranked Every [Franchise] Movie/Season From Worst To Best — Here's The Science",
                "why": "'The Science' elevates a standard ranking video. Ranking content is inherently shareable — people send it to disagree. Comments go crazy.",
                "thumbnail": "Ranked list graphic, movie posters stacked 1-to-last, bold number rankings. Clean, confident layout.",
                "hook": "Everyone's got an opinion. But we actually looked at the data — box office, reviews, rewatchability, cultural impact — and made the definitive list.",
                "format": "Long-form, 10–15 min. Work bottom-to-top for suspense. Defend controversial placements with specific reasoning."
            },
            {
                "title": "[Number] Things [Beloved Franchise] Quietly Borrowed From [Unexpected Source]",
                "why": "The hidden-influence format is deeply satisfying to nerdy audiences. Makes viewers feel like insiders. High comment engagement: 'wait, really?!'",
                "thumbnail": "Two franchise logos side-by-side with arrow/connection line, '?' graphic, curious tone.",
                "hook": "You've seen [Franchise] a hundred times. But did you notice it's essentially a remix of [Unexpected Source]? The creators never hid it — you just had to know where to look.",
                "format": "Medium, 7–10 min. Side-by-side comparisons, visual evidence. Fun detective energy, not accusatory."
            },
            {
                "title": "The Pop Culture Moments That Defined [Year] — And Why They Still Matter",
                "why": "Nostalgia + 'why it matters' framing reaches both people who lived through it and younger audiences discovering it. Great for algorithm-boosting watch time.",
                "thumbnail": "Collage of 4–6 iconic moments from the year, bold year number, warm nostalgic color grade.",
                "hook": "If you were online in [Year], these moments hit different. Looking back now, they were actually predicting where pop culture was going.",
                "format": "Long-form, 10–14 min. Structure each moment with: what happened → why it blew up → what it actually meant."
            },
            {
                "title": "Why [Underrated Show/Movie] Is Actually a Masterpiece Nobody Watched",
                "why": "Underdog content performs extremely well in the NerdDrop niche — you're speaking for viewers who feel overlooked. Discovery + validation is a powerful combination.",
                "thumbnail": "Show/movie poster with 'MASTERPIECE' text, sad/overlooked visual treatment, contrasting with bold confident type.",
                "hook": "This [show/movie] had [X] viewers. The most-watched episode of [Popular Show] had [10x more]. That's the biggest injustice in pop culture history.",
                "format": "Essay, 8–12 min. Make the case passionately. Specific scenes, specific reasons — not vague praise."
            },
            {
                "title": "[Number] Plot Holes That Are Actually Hidden Details Hiding In Plain Sight",
                "why": "Plot hole content is one of the highest-volume YouTube search categories. Flipping it — 'it's not a hole, it's a detail' — instantly makes your video more satisfying than the competition.",
                "thumbnail": "Character looking confused + magnifying glass, 'NOT A PLOT HOLE' text, playful energy.",
                "hook": "The internet thinks [X] is a mistake. It's not. You just missed the detail they put there to explain it.",
                "format": "Long-form, 9–13 min. List format. For each 'hole': state the complaint → reveal the hidden explanation → show the evidence."
            },
            {
                "title": "The Most Dangerous Fan Theory About [Franchise] — And Why It's Probably True",
                "why": "'Dangerous' + 'probably true' is a high-intrigue combo. Theory content drives massive comment debates which feed the algorithm. NerdDrop viewers love intellectual rabbit holes.",
                "thumbnail": "Character with red string conspiracy board behind them, 'THEORY' in bold red, dramatic but fun.",
                "hook": "This theory started on a forum three years ago. Since then, every piece of new content has quietly confirmed it — and the creators have never denied it.",
                "format": "Medium-long, 8–12 min. Present the theory → build the evidence chronologically → end on an open question."
            },
        ]

    # ── Nerd Drop Explains: Explainer / Deep Dives — thorough, analytical, educational ──
    else:  # Nerd Drop Explains
        return [
            {
                "title": "The Complete [Character] Story — Every Detail You Need To Understand Them",
                "why": "Full-character explainers are the backbone of the explainer niche. People search for these before watching a new season or after getting confused. Evergreen traffic.",
                "thumbnail": "Character in iconic pose, 'EXPLAINED' text, timeline graphic behind them. Authoritative, not clickbaity.",
                "hook": "You've seen [Character] in [X] projects. But their actual story — origins, motivations, every arc — is more complex than any single video has covered. Let's fix that.",
                "format": "Long-form, 14–20 min. Chronological structure: origins → turning point → current arc → what's next. Cover comics/source material too."
            },
            {
                "title": "Why [Plot Point Everyone Was Confused By] Actually Makes Perfect Sense",
                "why": "Confusion-resolution videos have massive search volume. Your audience already has the question — you're giving them the answer they couldn't find. High completion rates.",
                "thumbnail": "Confused-face emoji or character, '?' → '!' transformation graphic, clean explainer style.",
                "hook": "When [Plot Point] happened, half the audience checked out. The other half went to Reddit. Neither found a satisfying answer — but it's actually right there in the text.",
                "format": "Medium-long, 10–15 min. Start by validating the confusion, then build the explanation layer by layer."
            },
            {
                "title": "The Real-World [Concept] Behind [Famous Franchise] — Explained",
                "why": "Bridging pop culture to real-world concepts (science, history, psychology) doubles your audience: fans of the franchise AND people interested in the concept. Great for shares.",
                "thumbnail": "Franchise imagery blended with real-world photo (e.g., actual science lab). 'REAL SCIENCE/HISTORY' text overlay.",
                "hook": "The writers of [Franchise] didn't make this up. They took a real [concept] and wrapped a story around it — and the real version is even more fascinating.",
                "format": "Long-form, 12–18 min. Structure: what the show shows → what's real → how accurately it's depicted → why that matters."
            },
            {
                "title": "[Franchise] Timeline Explained — In The Correct Order",
                "why": "Timeline explainers have permanent search value, especially for franchises with complicated chronology. People return to these repeatedly. One of the most-shared formats.",
                "thumbnail": "Timeline graphic with key moments, franchise logo, 'FULL TIMELINE' text. Clean, organized visual.",
                "hook": "Watching [Franchise] in release order gives you one experience. Watching it in chronological order gives you a completely different story — and honestly, a better one.",
                "format": "Very long-form, 18–25 min. Structured by time period, not release date. Include context for why each period matters."
            },
            {
                "title": "The Philosophy of [Character] — What They Actually Believe And Why It Matters",
                "why": "Philosophy/ideology breakdowns perform exceptionally well with your deep-dive audience. Elevates you above recap channels. Drives long comment discussions.",
                "thumbnail": "Character thoughtful/serious expression, philosophical quote overlay, dark cinematic tone. Intellectual energy.",
                "hook": "[Character] isn't just a [hero/villain]. They have a coherent worldview — one that's internally consistent, backed by their history, and genuinely worth understanding.",
                "format": "Essay/analysis, 12–18 min. Extract their core beliefs from actions, not just dialogue. Compare to real philosophical frameworks."
            },
            {
                "title": "Every Foreshadow In [Movie/Show] — All [X] Of Them, Explained",
                "why": "Comprehensive foreshadow breakdowns are deeply satisfying and highly shareable. Positioned as 'all of them' makes it the definitive resource — hard for other creators to compete with.",
                "thumbnail": "Key foreshadow scene with circle callout, 'ALL [X] FORESHADOWS' text, rewatch-bait energy.",
                "hook": "The writers hid the ending in the very first episode. Then they did it again. And again. This video finds every single one — and explains exactly what it was telling you.",
                "format": "Long-form, 14–22 min. Go in chronological order. For each foreshadow: show the moment → show the payoff → explain the craft decision."
            },
            {
                "title": "The [Villain]'s Plan Explained — It Was Actually Brilliant",
                "why": "Villain-logic breakdowns generate massive engagement because viewers love defending or attacking villain plans. 'Actually brilliant' is a contrarian hook that earns clicks.",
                "thumbnail": "Villain mid-monologue or scheming, 'GENIUS PLAN' text, chess piece or strategy graphic overlay.",
                "hook": "Everyone says [Villain]'s plan had plot holes. They're wrong. Break it down step by step and it's one of the most internally consistent schemes in [franchise] history.",
                "format": "Analysis, 10–15 min. Walk through the plan phase by phase. Address every common criticism with specific textual evidence."
            },
            {
                "title": "What [Franchise] Gets Right (And Wrong) About [Real Topic] — Expert Breakdown",
                "why": "'Gets right and wrong' framing is balanced and intellectually honest, which your audience respects. Positions you as an authority. Can collaborate with actual experts for credibility.",
                "thumbnail": "Franchise + real-world split visual, tick/cross graphic, 'FACT vs FICTION' or 'EXPERT BREAKDOWN' text.",
                "hook": "We asked [or: We looked at what] actual [experts/historians/scientists] think about how [Franchise] portrays [Topic]. The results are more interesting than you'd expect.",
                "format": "Long-form, 13–18 min. Structure: introduce claim → check it against reality → explain the gap → why the writers made that choice."
            },
        ]

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
        for v in outliers[:10]:
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

        videos = get_recent_videos(channel_id, max_results=50)
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

            # Long-form only: skip Shorts and mini-videos (< 5 minutes)
            if duration_min < 5:
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
                    if len(transcripts) < 20:
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
