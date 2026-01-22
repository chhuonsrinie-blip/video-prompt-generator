import io
import json
import math
import os
import re
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
import streamlit as st
from google import genai


# =========================
# CONFIG
# =========================
APP_TITLE = "Storyboard Studio (Gemini Only)"
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


# =========================
# SECRETS / AUTH
# =========================
def get_secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets.get(name, default))
    except Exception:
        pass
    return os.environ.get(name, default) or default


def auth_gate() -> bool:
    password = get_secret("1234", "")
    if not password:
        # Allow running without password if you want
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
        st.error("❌ Gemini API key not found. Set GEMINI_API_KEY in Streamlit Secrets.")
        st.stop()
    return genai.Client(api_key=key)


# =========================
# URL ANALYSIS (best-effort, no bs4)
# =========================
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
    return r.text[:600_000]


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
    meta.description = (extract_meta(html, "og:description") or extract_meta(html, "description"))[:600]
    meta.og_image = extract_meta(html, "og:image")[:500]
    return meta


# =========================
# GEMINI STORYBOARD
# =========================
def safe_json_from_text(text: str) -> dict:
    """
    Extract JSON object even if the model wrapped it.
    """
    t = (text or "").strip()
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if not m:
        raise ValueError("No JSON object found in Gemini response.")
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
    n_scenes = max(1, int(math.ceil(total_seconds / max(1, seconds_per_scene))))

    prompt = f"""
You are a professional storyboard engine.

Goal:
Generate a connected, scene-by-scene storyboard from Scene 1 to Scene {n_scenes}.
Scenes must be UNIQUE but consistent (same actor/subject identity, same world continuity, coherent mood arc).
Do NOT produce real-world dangerous step-by-step instructions; depict cinematically only.

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

Return STRICT JSON ONLY. No markdown. No extra text.

Schema:
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
      "video_prompt": "detailed 5–8s video prompt",
      "image_prompt": "detailed image prompt (even if not generating images)"
    }}
  ]
}}
""".strip()

    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )

    data = safe_json_from_text(resp.text)

    # Normalize scenes length and indices
    scenes = data.get("scenes", [])[:n_scenes]
    for i, s in enumerate(scenes, start=1):
        s["idx"] = i
    data["scenes"] = scenes

    # If model didn't set category_final, fallback
    if not data.get("category_final"):
        data["category_final"] = category_choice if category_choice != "Auto" else "Movie (Real-life)"

    return data


# =========================
# EXPORT ZIP
# =========================
def build_zip(project: dict) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(project, ensure_ascii=False, indent=2))
        for s in project["scenes"]:
            base = f"scenes/scene_{s['idx']:02d}"
            z.writestr(f"{base}/title.txt", s["title"])
            z.writestr(f"{base}/story.txt", s["story"])
            z.writestr(f"{base}/video_prompt.txt", s["video_prompt"])
            z.writestr(f"{base}/image_prompt.txt", s["image_prompt"])
    return mem.getvalue()


# =========================
# UI
# =========================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title("🎬 Storyboard Studio (Gemini Only)")
st.caption("Gemini generates: URL analysis + idea composing + detailed scene-by-scene storyboard. No ImageFX/Whisk required.")

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
    generate = st.button("Generate Storyboard", type="primary")

# Build metadata
meta = SourceMeta(url=url, source=detect_source(url))
if url:
    with st.spinner("Fetching URL metadata…"):
        meta = build_source_meta(url)

left, right = st.columns(2)
with left:
    st.subheader("Auto analysis")
    st.write(f"**Source:** `{meta.source}`")
    st.write(f"**Title:** {meta.title}")
with right:
    st.subheader("Description")
    st.write(meta.description if meta.description else "_No description extracted (some platforms block scraping)._")

if generate:
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
            st.error(f"Gemini error: {e}")
            st.stop()

    project = {
        "meta": asdict(meta),
        "inputs": {
            "idea": idea,
            "category_choice": category_choice,
            "orientation": orientation,
            "total_seconds": int(total_seconds),
            "seconds_per_scene": int(seconds_per_scene),
            "detail_level": detail_level,
        },
        "category_final": data["category_final"],
        "continuity": data.get("continuity", {}),
        "scenes": data["scenes"],
    }

    st.session_state["project"] = project
    st.success("Storyboard created. Scroll down for scene cards + export.")

if "project" in st.session_state:
    project = st.session_state["project"]

    st.divider()
    st.subheader(f"Category: {project['category_final']}")
    st.json(project["continuity"])

    st.divider()
    st.subheader("Scene Cards")

    # Export
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_bytes = build_zip(project)
    st.download_button(
        "Download ZIP (scene txt + project.json)",
        data=zip_bytes,
        file_name=f"storyboard_{stamp}.zip",
        mime="application/zip",
    )
    st.download_button(
        "Download JSON",
        data=json.dumps(project, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"storyboard_{stamp}.json",
        mime="application/json",
    )

    # Cards (2 per row)
    scenes = project["scenes"]
    for start in range(0, len(scenes), 2):
        row = scenes[start:start + 2]
        cols = st.columns(2)
        for j, s in enumerate(row):
            with cols[j]:
                st.markdown(f"### SCENE {s['idx']} — {s['title']}")
                st.markdown("**STORY**")
                st.write(s["story"])
                st.markdown("**VIDEO PROMPT**")
                st.code(s["video_prompt"], language="text")
                st.markdown("**IMAGE PROMPT**")
                st.code(s["image_prompt"], language="text")
