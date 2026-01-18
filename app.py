import io, json, math, os, re, zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Tuple

import requests
import streamlit as st

# =========================
# CONFIG
# =========================
APP_TITLE = "Scene Prompt Generator (ImageFX Workflow)"
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

ORIENTATIONS = ["vertical 9:16", "horizontal 16:9", "square 1:1"]

DEFAULT_NEGATIVE = "text, watermark, logo, UI, labels, low quality, blurry"

# =========================
# DATA MODELS
# =========================
@dataclass
class CloneMeta:
    url: str
    source: str
    title: str = ""
    description: str = ""

@dataclass
class Scene:
    idx: int
    title: str
    story: str
    image_prompt: str
    video_prompt: str

# =========================
# AUTH
# =========================
def secret(name, default=""):
    return st.secrets.get(name, os.environ.get(name, default))

def auth():
    pwd = secret("APP_PASSWORD")
    if not pwd:
        return True
    st.session_state.setdefault("ok", False)
    if st.session_state.ok:
        return True
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        if p == pwd:
            st.session_state.ok = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False

# =========================
# URL ANALYSIS (lightweight)
# =========================
def detect_source(url):
    u = url.lower()
    if "youtube" in u: return "YouTube"
    if "tiktok" in u: return "TikTok"
    if "instagram" in u: return "Instagram"
    if "facebook" in u: return "Facebook"
    return "Web"

def fetch_meta(url):
    meta = CloneMeta(url=url, source=detect_source(url))
    try:
        html = requests.get(url, timeout=10).text
        t = re.search(r"<title>(.*?)</title>", html, re.I|re.S)
        if t: meta.title = t.group(1).strip()[:200]
        d = re.search(r'name="description" content="(.*?)"', html, re.I)
        if d: meta.description = d.group(1)[:400]
    except:
        pass
    return meta

# =========================
# AUTO CONTINUITY
# =========================
def continuity(category):
    if category == "Animals (Real-life)":
        return "Same animal subject, same species markings, natural habitat continuity."
    if category == "DIY":
        return "Same hands, same tools, same workspace, clean step-by-step progression."
    if category in ["Bushcraft","Survival","Shelter"]:
        return "Same outdoors person, same outfit and tools, same location evolution."
    return "Same main character, same world, cinematic continuity."

# =========================
# SCENE PLANNING
# =========================
def beats(n):
    base = [
        "Establish environment and subject",
        "Introduce goal",
        "First action",
        "Progress detail",
        "Complication",
        "Solution",
        "Final reveal",
        "Ending calm shot"
    ]
    return (base * 10)[:n]

# =========================
# PROMPT BUILDER (ImageFX-optimized)
# =========================
def build_scene(idx, total, beat, cat, orient, cont, meta, idea):
    image_prompt = f"""
Ultra-realistic cinematic photo.
{beat}.
{cont}
Natural lighting, real physics, authentic materials.
Camera: cinematic composition, shallow depth of field.
No text, no watermark.
""".strip()

    video_prompt = f"""
Scene {idx+1}/{total} ({orient})
Category: {cat}
Beat: {beat}
Continuity: {cont}
Inspired by: {meta.title}
""".strip()

    story = f"{beat}. Continuity maintained. Source inspiration: {meta.title or meta.url}"

    return image_prompt, video_prompt, story

# =========================
# EXPORT
# =========================
def build_zip(scenes, payload):
    mem = io.BytesIO()
    with zipfile.ZipFile(mem,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(payload,indent=2))
        for s in scenes:
            base = f"scene_{s.idx+1:02d}"
            z.writestr(f"{base}/image_prompt.txt", s.image_prompt)
            z.writestr(f"{base}/video_prompt.txt", s.video_prompt)
            z.writestr(f"{base}/story.txt", s.story)
    return mem.getvalue()

# =========================
# UI
# =========================
st.set_page_config(APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption("FREE workflow: Prompt → ImageFX / Whisk → Generate images manually")

if not auth():
    st.stop()

with st.sidebar:
    url = st.text_input("Source URL")
    idea = st.text_area("Idea (optional)")
    category = st.selectbox("Category", CATEGORIES)
    orient = st.selectbox("Orientation", ORIENTATIONS)
    total = st.number_input("Total duration (s)", 10, 600, 60)
    per = st.number_input("Seconds per scene", 3, 15, 6)
    go = st.button("Generate")

if go:
    meta = fetch_meta(url) if url else CloneMeta("", "Manual")
    cat = category if category != "Auto" else "Movie (Real-life)"
    cont = continuity(cat)
    n = max(1, total // per)
    bs = beats(n)

    scenes: List[Scene] = []
    for i in range(n):
        ip, vp, stx = build_scene(i, n, bs[i], cat, orient, cont, meta, idea)
        scenes.append(Scene(i, bs[i], stx, ip, vp))

    payload = {
        "meta": asdict(meta),
        "category": cat,
        "orientation": orient,
        "idea": idea,
        "continuity": cont,
    }

    st.success("Scenes generated")

    col1, col2 = st.columns(2)
    with col1:
        st.link_button("Open ImageFX", IMAGEFX_URL)
    with col2:
        st.link_button("Open Whisk", WHISK_URL)

    zip_bytes = build_zip(scenes, payload)
    st.download_button("Download ZIP", zip_bytes, "project.zip")

    for s in scenes:
        st.markdown(f"## Scene {s.idx+1}: {s.title}")
        st.code(s.image_prompt)
        st.code(s.video_prompt)
