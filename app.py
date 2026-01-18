import io
import json
import math
import os
import re
import textwrap
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from google import genai


# =========================
# CONFIG
# =========================
APP_TITLE = "Storyboard Studio (Gemini Prompts)"
IMAGEFX_URL = "https://labs.google/fx/tools/image-fx"
WHISK_URL = "https://labs.google/fx/tools/whisk"

CATEGORIES = [
    "Auto",
    "Bushcraft",
    "Survival",
    "Shelter",
    "DIY",
    "Movie (Real-life)",
    "Animals (Real-life)",
]

# Use prompt-only here; ImageFX sizes are UI-side; we store for prompt context
ORIENTATIONS = ["vertical 9:16", "horizontal 16:9", "square 1:1"]

UA = {"User-Agent": "Mozilla/5.0 (StoryboardStudio/1.0)"}


# =========================
# AUTH + SECRETS
# =========================
def get_secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets.get(name, default))
    except Exception:
        pass
    return os.environ.get(name, default) or default


def auth_gate() -> bool:
    password = get_secret("APP_PASSWORD", "")
    if not password:
        st.warning("No APP_PASSWORD set. Add it in Streamlit Secrets.")
        return False

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


# =========================
# URL METADATA (NO bs4)
# =========================
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
    return r.text[:500_000]


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


@dataclass
class CloneMeta:
    url: str
    source: str
    title: str = ""
    description: str = ""
    og_image: str = ""


def build_clone_meta(url: str) -> CloneMeta:
    meta = CloneMeta(url=url, source=detect_source(url))
    if not url:
        return meta

    try:
        html = fetch_html(url)
    except Exception:
        return meta

    meta.title = (extract_meta(html, "og:title") or extract_title_tag(html))[:200]
    meta.description = (
        extract_meta(html, "og:description") or extract_meta(html, "description")
    )[:600]
    meta.og_image = extract_meta(html, "og:image")[:500]
    return meta


# =========================
# Gemini storyboard generation
# =========================
def gemini_generate_storyboard(
    gemini_key: str,
    meta: CloneMeta,
    idea: str,
    category: str,
    orientation: str,
    total_seconds: int,
    seconds_per_scene: int,
    detail_level: str,
) -> Dict:
    """
    Returns dict with keys:
      - auto_continuity: actor_lock, setting_lock, mood_arc, rules
      - scenes: list of {idx,title,story,video_prompt,image_prompt}
      - category_final (optional)
    """
    if not gemini_key:
        raise RuntimeError("GEMINI_API_KEY missing in Streamlit Secrets.")

    n_scenes = max(1, math.ceil(total_seconds / max(1, seconds_per_scene)))

    client = genai.Client(api_key=gemini_key)

    # Keep prompts “safe”: no real-world dangerous instructions; depict only.
    safety_rule = (
        "Do NOT provide real-world step-by-step instructions for dangerous activities. "
        "Depict scenes cinematically without actionable survival/bushcraft instructions."
    )

    system = (
        "You are a storyboard engine. "
        "Generate a connected scene-by-scene storyboard. "
        "All scenes must be different but consistent in subject and world."
    )

    user = f"""
SOURCE URL: {meta.url}
SOURCE PLATFORM: {meta.source}
SOURCE TITLE: {meta.title}
SOURCE DESCRIPTION: {meta.description}

USER IDEA (optional): {idea}

CATEGORY (preferred): {category}
ORIENTATION: {orientation}
TOTAL DURATION: {total_seconds} seconds
SECONDS PER SCENE: {seconds_per_scene} (target)
SCENE COUNT: {n_scenes}
DETAIL LEVEL: {detail_level}

RULES:
- Continuity: same actor/subject identity, same world consistency, coherent mood arc.
- Scene progression: scene i must logically follow scene i-1.
- Output must be “prompt-ready” for ImageFX/Whisk.
- Include strong cinematography language (lens feel, composition, lighting).
- {safety_rule}

RETURN STRICT JSON ONLY with this schema:

{{
  "category_final": "one of: Bushcraft|Survival|Shelter|DIY|Movie (Real-life)|Animals (Real-life)",
  "auto_continuity": {{
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
      "image_prompt": "detailed image prompt for ImageFX/Whisk"
    }}
  ]
}}

No markdown. No extra text.
"""

    # Gemini call
    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[system, user],
    )

    raw = (resp.text or "").strip()

    # Robust JSON extraction (in case model wraps it)
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        raise RuntimeError("Gemini did not return JSON. Try again.")
    data = json.loads(m.group(0))

    # Basic validation / cleanup
    if "scenes" not in data or not isinstance(data["scenes"], list) or len(data["scenes"]) == 0:
        raise RuntimeError("Gemini JSON missing scenes list.")

    # Force correct idx numbering and trim to n_scenes
    scenes = data["scenes"][:n_scenes]
    for i, s in enumerate(scenes, start=1):
        s["idx"] = i
    data["scenes"] = scenes

    # Fill category_final if missing
    if "category_final" not in data or not data["category_final"]:
        data["category_final"] = category if category != "Auto" else "Movie (Real-life)"

    return data


# =========================
# Scene card PNG (preview image)
# =========================
def scene_card_png(scene_label: str, story: str, w=1200, h=650) -> bytes:
    img = Image.new("RGB", (w, h), (12, 18, 32))
    d = ImageDraw.Draw(img)

    try:
        ft = ImageFont.truetype("DejaVuSans.ttf", 34)
        fb = ImageFont.truetype("DejaVuSans.ttf", 18)
    except Exception:
        ft = ImageFont.load_default()
        fb = ImageFont.load_default()

    d.rectangle([0, 0, w, 70], fill=(7, 11, 22))
    d.text((18, 18), scene_label, font=ft, fill=(230, 240, 255))

    d.text((18, 100), "STORY", font=fb, fill=(140, 190, 255))
    wrapped = "\n".join(textwrap.wrap(story, width=110))
    d.text((18, 130), wrapped, font=fb, fill=(210, 225, 245))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# =========================
# Export ZIP
# =========================
def build_zip(meta: Dict, scenes: List[Dict], continuity: Dict) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        project = {
            "meta": meta,
            "continuity": continuity,
            "scenes": scenes,
        }
        z.writestr("project.json", json.dumps(project, ensure_ascii=False, indent=2))

        # Per-scene files
        for s in scenes:
            base = f"scenes/scene_{s['idx']:02d}"
            z.writestr(f"{base}/story.txt", s["story"])
            z.writestr(f"{base}/video_prompt.txt", s["video_prompt"])
            z.writestr(f"{base}/image_prompt.txt", s["image_prompt"])
            z.writestr(f"{base}/scene_card.png", scene_card_png(f"SCENE {s['idx']} — {s['title']}", s["story"]))

    return mem.getvalue()


# =========================
# UI Styling
# =========================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] {background:#0b1220;}
[data-testid="stSidebar"] {background:#070c16;}
h1,h2,h3,h4, p, div, span, label {color:#eaf2ff;}
.scene-card {background:linear-gradient(180deg, rgba(16,24,40,1), rgba(10,16,28,1));
  border:1px solid rgba(70,110,170,0.35); border-radius:16px; padding:14px 16px; margin-bottom:16px;}
.scene-header {display:flex; justify-content:space-between; align-items:center;
  font-weight:800; font-size:18px; color:#dcecff; margin-bottom:10px;}
.pill {font-size:12px; padding:4px 10px; border-radius:999px;
  background:rgba(50,90,150,0.25); border:1px solid rgba(90,140,210,0.35);}
</style>
""",
    unsafe_allow_html=True,
)

st.title("Storyboard Studio")
st.caption("Gemini API generates the storyboard prompts. Images are generated manually in ImageFX/Whisk (no public ImageFX API).")

if not auth_gate():
    st.stop()

# Tabs like your video
tab_gen, tab_story, tab_help = st.tabs(["Prompt Generator", "Storyboard", "Export/Help"])

with tab_gen:
    st.subheader("Prompt Generator (single)")
    concept = st.text_area("Concept / Prompt", height=140)
    category_pg = st.selectbox("Category", CATEGORIES, 0, key="pg_cat")
    orientation_pg = st.selectbox("Orientation", ORIENTATIONS, 0, key="pg_or")
    detail_pg = st.selectbox("Detail level", ["Normal", "High", "Max"], 1, key="pg_detail")

    if st.button("Generate Prompt", key="pg_btn"):
        cat = category_pg if category_pg != "Auto" else "Movie (Real-life)"
        preset = f"Category: {cat}. Orientation: {orientation_pg}. Detail: {detail_pg}."
        out = f"{preset}\nUltra-realistic cinematic photo. {concept}\nNo text, no watermark."
        st.code(out, language="text")
        st.link_button("Open ImageFX", IMAGEFX_URL)

with tab_story:
    st.subheader("Storyboard Studio")

    with st.sidebar:
        st.header("Storyboard Inputs")
        url = st.text_input("Source URL (optional)")
        idea = st.text_area("Idea (optional)", height=90)
        category = st.selectbox("Category", CATEGORIES, 0)
        orientation = st.selectbox("Resolution", ORIENTATIONS, 0)
        total_seconds = st.number_input("Total duration (seconds)", 10, 3600, 60, 5)
        seconds_per_scene = st.number_input("Seconds per scene", 3, 30, 6, 1)
        detail_level = st.selectbox("Detail level", ["Normal", "High", "Max"], 1)
        generate = st.button("Generate Storyboard", type="primary")

    if generate:
        gemini_key = get_secret("GEMINI_API_KEY", "")
        if not gemini_key:
            st.error("GEMINI_API_KEY missing. Add it in Streamlit Secrets.")
            st.stop()

        meta = build_clone_meta(url) if url else CloneMeta(url="", source="Manual")

        # Auto category suggestion if user picked Auto
        suggested = infer_category_from = infer_category(meta, idea) if False else None  # placeholder
        # We'll let Gemini decide final category; still pass user's choice as hint.

        try:
            with st.spinner("Generating storyboard with Gemini…"):
                data = gemini_generate_storyboard(
                    gemini_key=gemini_key,
                    meta=meta,
                    idea=idea,
                    category=category,
                    orientation=orientation,
                    total_seconds=int(total_seconds),
                    seconds_per_scene=int(seconds_per_scene),
                    detail_level=detail_level,
                )
        except Exception as e:
            st.error(f"Gemini error: {e}")
            st.stop()

        st.session_state["storyboard_data"] = data
        st.session_state["storyboard_meta"] = meta
        st.session_state["storyboard_inputs"] = {
            "url": url,
            "idea": idea,
            "category": category,
            "orientation": orientation,
            "total_seconds": int(total_seconds),
            "seconds_per_scene": int(seconds_per_scene),
            "detail_level": detail_level,
        }

        st.success("Storyboard generated. Scroll down to view scene cards.")

    if "storyboard_data" in st.session_state:
        data = st.session_state["storyboard_data"]
        meta = st.session_state["storyboard_meta"]
        inputs = st.session_state["storyboard_inputs"]

        category_final = data.get("category_final", inputs["category"])
        continuity = data.get("auto_continuity", {})
        scenes = data.get("scenes", [])

        st.divider()
        st.subheader("Scene Cards")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.link_button("Open ImageFX", IMAGEFX_URL)
        with col2:
            st.link_button("Open Whisk", WHISK_URL)
        with col3:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_bytes = build_zip(
                meta={
                    "source_url": meta.url,
                    "source": meta.source,
                    "title": meta.title,
                    "description": meta.description,
                    "category_final": category_final,
                    **inputs,
                },
                scenes=scenes,
                continuity=continuity,
            )
            st.download_button(
                "Download ZIP (prompts + scene cards + JSON)",
                data=zip_bytes,
                file_name=f"storyboard_{stamp}.zip",
                mime="application/zip",
            )

        st.markdown("### Auto Continuity (generated)")
        st.json(continuity)

        # 2 cards per row like your reference
        for start in range(0, len(scenes), 2):
            row = scenes[start:start+2]
            cols = st.columns(2)
            for j, s in enumerate(row):
                with cols[j]:
                    st.markdown(
                        f"""<div class="scene-card">
<div class="scene-header">
  <div>SCENE {s['idx']} — {s['title']}</div>
  <div class="pill">{category_final}</div>
</div>
</div>""",
                        unsafe_allow_html=True
                    )

                    # Preview card PNG (exported too)
                    st.image(scene_card_png(f"SCENE {s['idx']} — {s['title']}", s["story"]), use_container_width=True)

                    st.markdown("**STORY**")
                    st.write(s["story"])

                    st.markdown("**PROMPT (IMAGEFX/WHISK)**")
                    st.code(s["image_prompt"], language="text")

                    st.markdown("**PROMPT (VIDEO)**")
                    st.code(s["video_prompt"], language="text")

        st.divider()
        st.subheader("Copy/Paste Pack (ImageFX)")
        pack = "\n\n".join([f"[Scene {s['idx']}] {s['image_prompt']}" for s in scenes])
        st.text_area("All ImageFX prompts", value=pack, height=240)

with tab_help:
    st.subheader("How to generate images (free)")
    st.write(
        "This app generates prompts. ImageFX/Whisk do not provide a public API, so image generation is manual:\n"
        "1) Click **Open ImageFX**\n"
        "2) Copy a scene Image Prompt\n"
        "3) Paste in ImageFX and generate\n"
        "4) Repeat for each scene\n"
    )
