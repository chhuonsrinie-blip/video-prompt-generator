import io
import json
import math
import os
import re
import time
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Tuple

import requests
import streamlit as st
from google import genai  # requires google-genai


# ============================================================
# CONFIG
# ============================================================
APP_TITLE = "Storyboard Studio (Gemini Only • Master Prompt)"
UA = {"User-Agent": "Mozilla/5.0 (StoryboardStudio/1.0)"}

CATEGORIES = [
    "Auto",
    "Bushcraft",
    "Survival",
    "Shelter",
    "DIY",
    "Movie (Real-life)",
    "Animals (Real-life)",
]

ORIENTATIONS = ["vertical 9:16", "horizontal 16:9", "square 1:1"]
DETAIL_LEVELS = ["Normal", "High", "Max"]


# ============================================================
# SECRETS + AUTH
# ============================================================
def get_secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets.get(name, default))
    except Exception:
        pass
    return os.environ.get(name, default) or default


def auth_gate() -> bool:
    """
    If APP_PASSWORD is set -> require login.
    If not set -> allow access.
    """
    password = get_secret("APP_PASSWORD", "")
    if not password:
        return True

    st.session_state.setdefault("authed", False)
    if st.session_state.authed:
        return True

    st.title("🔒 Private App")
    attempt = st.text_input("Password", type="password")
    if st.button("Login"):
        if attempt == password:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Wrong password.")
    return False


def get_gemini_client() -> genai.Client:
    key = get_secret("GEMINI_API_KEY", "")
    if not key:
        st.error("❌ GEMINI_API_KEY not found. Set it in Streamlit Secrets.")
        st.stop()
    return genai.Client(api_key=key)


# ============================================================
# URL METADATA (NO bs4)
# ============================================================
@dataclass
class SourceMeta:
    url: str
    source: str
    title: str = ""
    description: str = ""
    og_image: str = ""


def detect_source(url: str) -> str:
    u = (url or "").lower().strip()
    if "tiktok.com" in u:
        return "TikTok"
    if "instagram.com" in u or "instagr.am" in u:
        return "Instagram"
    if "facebook.com" in u or "fb.watch" in u:
        return "Facebook"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    return "Web"


def fetch_html(url: str, timeout: int = 12) -> str:
    r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text[:700_000]


def extract_meta(html: str, key: str) -> str:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for p in patterns:
        m = re.search(p, html, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def extract_title_tag(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()[:200]


def build_source_meta(url: str) -> SourceMeta:
    meta = SourceMeta(url=url, source=detect_source(url))
    if not url:
        return meta

    try:
        html = fetch_html(url)
    except Exception:
        return meta

    meta.title = (extract_meta(html, "og:title") or extract_title_tag(html))[:200]
    meta.description = (extract_meta(html, "og:description") or extract_meta(html, "description"))[:800]
    meta.og_image = extract_meta(html, "og:image")[:500]
    return meta


def scene_count(total_seconds: int, seconds_per_scene: int) -> int:
    return max(1, int(math.ceil(total_seconds / max(1, seconds_per_scene))))


# ============================================================
# QUOTA RETRY + FALLBACK
# ============================================================
def _extract_retry_seconds(msg: str, default: int = 60) -> int:
    m = re.search(r"retry in ([0-9.]+)s", msg, flags=re.IGNORECASE)
    if m:
        try:
            return max(5, int(float(m.group(1))))
        except Exception:
            return default
    m2 = re.search(r"retryDelay[^0-9]*([0-9.]+)", msg, flags=re.IGNORECASE)
    if m2:
        try:
            return max(5, int(float(m2.group(1))))
        except Exception:
            return default
    return default


def gemini_generate_with_retry(client: genai.Client, model: str, contents: str, max_retries: int = 2):
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as e:
            last_err = e
            msg = str(e)
            if ("RESOURCE_EXHAUSTED" in msg) or ("429" in msg) or ("quota" in msg.lower()):
                wait_s = _extract_retry_seconds(msg, default=60)
                if attempt < max_retries:
                    st.warning(f"Gemini quota hit. Waiting {wait_s}s then retrying ({attempt+1}/{max_retries})…")
                    time.sleep(wait_s)
                    continue
            raise
    raise last_err


def fallback_storyboard(
    meta: SourceMeta,
    idea: str,
    category_choice: str,
    orientation: str,
    total_seconds: int,
    seconds_per_scene: int,
    detail_level: str,
) -> dict:
    """
    Offline fallback: always returns scenes with MASTER_PROMPT.
    """
    n = scene_count(total_seconds, seconds_per_scene)
    category_final = category_choice if category_choice != "Auto" else "Movie (Real-life)"

    continuity = {
        "actor_lock": "Same main subject across scenes (identity + outfit/markings consistent).",
        "setting_lock": "Same world continuity; location evolves logically scene-to-scene.",
        "mood_arc": "Coherent progression; no random shifts.",
        "rules": "No text/watermarks/logos. Realistic physics. Continuity enforced."
    }

    beats = [
        ("Opening Shot", "Establish subject, setting, and goal/tension."),
        ("Rising Tension", "Introduce a constraint; maintain continuity."),
        ("First Action", "Show meaningful progress with detail inserts."),
        ("Progress Check", "Measurable progress; small improvement."),
        ("Turning Point", "Key milestone; emotional beat increases."),
        ("Resolution", "Satisfying completion; clean hold for cut."),
    ]
    beats = (beats * ((n // len(beats)) + 1))[:n]

    def detail_phrase(level: str) -> str:
        return {
            "Normal": "high realism, clean detail",
            "High": "ultra-detailed, micro-textures, crisp edges, natural grain",
            "Max": "extreme detail, micro-textures, realistic lighting physics, cinematic color science",
        }.get(level, "high realism, clean detail")

    scenes = []
    for i in range(1, n + 1):
        title, beat = beats[i - 1]
        master = f"""MASTER PROMPT — Scene {i}/{n} — {title} ({category_final}) [{orientation}] (5–8s)
{detail_phrase(detail_level)}. Cinematic realism, coherent color grading, no text/watermark.
SUBJECT LOCK: {continuity['actor_lock']}
SETTING LOCK: {continuity['setting_lock']}
MOOD ARC: {continuity['mood_arc']}
CAMERA: 35mm cinematic look; one clear camera move; shallow depth of field; stable framing.
LIGHTING: motivated practical/natural light; realistic shadows.
ACTION BEAT: {beat}
TIMING: 0–2s establish → 2–6s progress → 6–8s settle/hold for cut.
RULES: {continuity['rules']}
SOURCE CUES: {meta.source} | {meta.title}
""".strip()

        scenes.append({
            "idx": i,
            "title": title,
            "story": beat,
            "master_prompt": master
        })

    return {
        "category_final": category_final,
        "continuity": continuity,
        "scenes": scenes
    }


# ============================================================
# GEMINI STORYBOARD (MASTER_PROMPT JSON)
# ============================================================
def safe_json_from_text(text: str) -> dict:
    t = (text or "").strip()
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if not m:
        raise ValueError("No JSON found in Gemini response.")
    return json.loads(m.group(0))


def gemini_storyboard(
    client: genai.Client,
    meta: SourceMeta,
    idea: str,
    category_choice: str,
    orientation: str,
    total_seconds: int,
    seconds_per_scene: int,
    detail_level: str,
) -> dict:
    n = scene_count(total_seconds, seconds_per_scene)

    safety_rule = (
        "Do NOT provide real-world step-by-step instructions for dangerous activities. "
        "Depict cinematically without actionable guidance."
    )

    prompt = f"""
You are a professional storyboard engine.

Generate a connected storyboard with {n} scenes (Scene 1..Scene {n}).
All scenes must be UNIQUE but consistent (same actor/subject identity, same world continuity, coherent mood arc).

Inputs:
- URL: {meta.url}
- Platform: {meta.source}
- Title: {meta.title}
- Description: {meta.description}
- User idea (optional): {idea}

Constraints:
- Category preference: {category_choice}
- Orientation: {orientation}
- Total duration: {total_seconds}s
- Seconds per scene: {seconds_per_scene}s
- Detail level: {detail_level}
- {safety_rule}

Return STRICT JSON ONLY (no markdown, no commentary) with schema:
{{
  "category_final": "one of: Bushcraft|Survival|Shelter|DIY|Movie (Real-life)|Animals (Real-life)",
  "continuity": {{
    "actor_lock": "1-2 lines",
    "setting_lock": "1-2 lines",
    "mood_arc": "short arc",
    "rules": "short rules line"
  }},
  "scenes": [
    {{
      "idx": 1,
      "title": "short title",
      "story": "1-2 sentences",
      "master_prompt": "ONE MASTER PROMPT usable for BOTH video and image generation. Must include: 5–8s timing, camera move, shot framing, lighting, action beat, continuity locks, style, end-hold for cut, and 'no text/watermark'."
    }}
  ]
}}
""".strip()

    resp = gemini_generate_with_retry(
        client=client,
        model="gemini-2.0-flash",
        contents=prompt,
        max_retries=2
    )

    data = safe_json_from_text(resp.text)

    scenes = data.get("scenes", [])[:n]
    for i, s in enumerate(scenes, start=1):
        s["idx"] = i
    data["scenes"] = scenes

    if not data.get("category_final"):
        data["category_final"] = category_choice if category_choice != "Auto" else "Movie (Real-life)"

    if "continuity" not in data:
        data["continuity"] = {}

    # ensure master_prompt exists
    for s in data["scenes"]:
        if "master_prompt" not in s:
            s["master_prompt"] = f"MASTER PROMPT — Scene {s['idx']}/{n} — {s.get('title','Scene')} ({data['category_final']}) [{orientation}]"

    return data


# ============================================================
# EXPORT
# ============================================================
def build_zip(project: dict) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(project, ensure_ascii=False, indent=2))
        for s in project["scenes"]:
            base = f"scenes/scene_{s['idx']:02d}"
            z.writestr(f"{base}/title.txt", s["title"])
            z.writestr(f"{base}/story.txt", s["story"])
            z.writestr(f"{base}/master_prompt.txt", s["master_prompt"])
    return mem.getvalue()


# ============================================================
# UI
# ============================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title("🎬 Storyboard Studio (Gemini Only • Master Prompt)")
st.caption("Each scene outputs ONE MASTER PROMPT (usable as both VIDEO and IMAGE prompt). No ImageFX/Whisk needed.")

if not auth_gate():
    st.stop()

with st.sidebar:
    st.header("Inputs")
    url = st.text_input("Source URL (optional)")
    idea = st.text_area("Idea (optional)", height=100)
    category_choice = st.selectbox("Category", CATEGORIES, 0)
    orientation = st.selectbox("Orientation", ORIENTATIONS, 0)
    total_seconds = st.number_input("Total duration (seconds)", 10, 3600, 60, 5)
    seconds_per_scene = st.number_input("Seconds per scene", 3, 30, 6, 1)
    detail_level = st.selectbox("Detail level", DETAIL_LEVELS, 1)
    use_gemini = st.checkbox("Use Gemini", value=True)
    generate = st.button("Generate Storyboard", type="primary")

meta = SourceMeta(url=url, source=detect_source(url))
if url:
    with st.spinner("Fetching URL metadata…"):
        meta = build_source_meta(url)

c1, c2 = st.columns(2)
with c1:
    st.subheader("Auto analysis")
    st.write(f"**Source:** `{meta.source}`")
    st.write(f"**Title:** {meta.title}")
with c2:
    st.subheader("Description")
    st.write(meta.description if meta.description else "_No description extracted (some platforms block scraping)._")

if generate:
    if use_gemini:
        client = get_gemini_client()
        with st.spinner("Generating storyboard with Gemini…"):
            try:
                data = gemini_storyboard(
                    client=client,
                    meta=meta,
                    idea=idea,
                    category_choice=category_choice,
                    orientation=orientation,
                    total_seconds=int(total_seconds),
                    seconds_per_scene=int(seconds_per_scene),
                    detail_level=detail_level,
                )
            except Exception as e:
                msg = str(e)
                if ("RESOURCE_EXHAUSTED" in msg) or ("429" in msg) or ("quota" in msg.lower()):
                    st.warning("Gemini quota exceeded. Using offline fallback storyboard.")
                    data = fallback_storyboard(
                        meta=meta,
                        idea=idea,
                        category_choice=category_choice,
                        orientation=orientation,
                        total_seconds=int(total_seconds),
                        seconds_per_scene=int(seconds_per_scene),
                        detail_level=detail_level,
                    )
                else:
                    st.error(f"Gemini error: {e}")
                    st.stop()
    else:
        data = fallback_storyboard(
            meta=meta,
            idea=idea,
            category_choice=category_choice,
            orientation=orientation,
            total_seconds=int(total_seconds),
            seconds_per_scene=int(seconds_per_scene),
            detail_level=detail_level,
        )

    project = {
        "meta": asdict(meta),
        "inputs": {
            "idea": idea,
            "category_choice": category_choice,
            "orientation": orientation,
            "total_seconds": int(total_seconds),
            "seconds_per_scene": int(seconds_per_scene),
            "detail_level": detail_level,
            "use_gemini": use_gemini,
        },
        "category_final": data.get("category_final", category_choice),
        "continuity": data.get("continuity", {}),
        "scenes": data.get("scenes", []),
    }

    st.session_state["project"] = project
    st.success("Storyboard created. Scroll down for scene cards + export.")

if "project" in st.session_state:
    project = st.session_state["project"]

    st.divider()
    st.subheader(f"Category: {project['category_final']}")
    st.json(project["continuity"])

    st.divider()
    st.subheader("Scene Cards (Master Prompt)")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_bytes = build_zip(project)

    colA, colB = st.columns(2)
    with colA:
        st.download_button(
            "Download ZIP (master_prompt per scene + project.json)",
            data=zip_bytes,
            file_name=f"storyboard_{stamp}.zip",
            mime="application/zip",
        )
    with colB:
        st.download_button(
            "Download JSON",
            data=json.dumps(project, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"storyboard_{stamp}.json",
            mime="application/json",
        )

    scenes = project["scenes"]
    for start in range(0, len(scenes), 2):
        row = scenes[start:start + 2]
        cols = st.columns(2)
        for j, s in enumerate(row):
            with cols[j]:
                st.markdown(f"### SCENE {s['idx']} — {s['title']}")
                st.markdown("**STORY**")
                st.write(s["story"])
                st.markdown("**MASTER PROMPT (VIDEO + IMAGE)**")
                st.code(s["master_prompt"], language="text")
