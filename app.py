import base64
import io
import json
import math
import os
import re
import textwrap
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont


# =========================
# Constants / Config
# =========================
APP_TITLE = "Scene Cards Prompt Generator (Web + OpenAI Images)"
WHISK_URL = "https://labs.google/fx/tools/whisk"
UA = {"User-Agent": "Mozilla/5.0 (SceneCards/1.0)"}

CATEGORIES = [
    "Auto",
    "Bushcraft",
    "Survival",
    "Shelter",
    "DIY",
    "Movie (Real-life)",
    "Animals (Real-life)",
]

# OpenAI image sizes: keep to common supported sizes
# (portrait, landscape, square)
ORIENTATIONS = {
    "vertical 9:16": (1024, 1536),
    "horizontal 16:9": (1536, 1024),
    "square 1:1": (1024, 1024),
}

DEFAULT_NEGATIVE = (
    "text, watermark, logo, lowres, blurry, jpeg artifacts, deformed, extra limbs, "
    "bad anatomy, duplicate, cropped, worst quality"
)

STYLE_PRESETS = {
    "Bushcraft": dict(
        vibe="authentic bushcraft realism, tactile materials, practical outdoor gear, detailed hands, natural textures",
        camera="35mm documentary-cinema, stable handheld, shallow depth of field",
        lighting="natural light, golden hour or overcast realism, soft shadows",
    ),
    "Survival": dict(
        vibe="survival realism, urgency and constraints, grounded decisions, realistic weather pressure",
        camera="cinematic documentary, close-medium emotion + wide context, 35mm look",
        lighting="moody overcast or dusk, realistic contrast",
    ),
    "Shelter": dict(
        vibe="shelter-building realism, clear progress beats, safe depiction, materials consistency",
        camera="stable framing, medium-wide for structure + detail inserts",
        lighting="daylight shifting toward dusk, consistent time progression",
    ),
    "DIY": dict(
        vibe="clean DIY tutorial realism, satisfying progress beats, crisp detail, clean workspace",
        camera="tripod-stable, top-down + side angle, close-ups of hands/tools",
        lighting="soft practical lighting, clean shadows, high clarity",
    ),
    "Movie (Real-life)": dict(
        vibe="cinematic real-life film look, high production, coherent art direction, strong composition",
        camera="35mm film look, one clear camera move per scene, shallow DOF",
        lighting="motivated cinematic lighting, practical sources, filmic contrast",
    ),
    "Animals (Real-life)": dict(
        vibe="wildlife documentary realism, species-accurate behavior, true-to-life color, respectful distance",
        camera="telephoto look, stable handheld, natural bokeh",
        lighting="natural outdoor light, true-to-life exposure",
    ),
}

KW = {
    "Bushcraft": ["bushcraft", "campfire", "forest camp", "tarp", "cordage", "axe", "knife", "kindling"],
    "Survival": ["survival", "wilderness", "stranded", "lost", "storm", "rescue", "signal", "navigation", "sos"],
    "Shelter": ["shelter", "lean-to", "debris hut", "hut", "tarp shelter", "windbreak", "insulation"],
    "DIY": ["diy", "how to", "tutorial", "build", "make", "craft", "workbench", "assemble", "tools"],
    "Animals (Real-life)": ["wildlife", "animal", "animals", "nature", "documentary", "dog", "cat", "lion", "tiger", "elephant", "bird"],
    "Movie (Real-life)": ["cinematic", "film", "movie", "trailer", "scene", "thriller", "noir", "neon", "action"],
}


# =========================
# Data models
# =========================
@dataclass
class CloneMeta:
    url: str
    source: str
    title: str = ""
    description: str = ""
    site_name: str = ""
    og_image: str = ""


@dataclass
class SceneOut:
    idx: int
    title: str
    beat: str
    story: str
    video_prompt: str
    image_prompt: str
    negative_prompt: str
    seed: int
    image_bytes: Optional[bytes] = None


# =========================
# Secrets / Auth
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
        st.warning("No password configured. Set APP_PASSWORD in Streamlit Secrets.")
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
# URL clone (no bs4)
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
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t[:200]


def build_clone_meta(url: str) -> CloneMeta:
    meta = CloneMeta(url=url, source=detect_source(url))
    if not url:
        return meta

    try:
        html = fetch_html(url)
    except Exception:
        return meta

    meta.title = (
        extract_meta(html, "og:title")
        or extract_meta(html, "twitter:title")
        or extract_title_tag(html)
    )[:200]

    meta.description = (
        extract_meta(html, "og:description")
        or extract_meta(html, "twitter:description")
        or extract_meta(html, "description")
    )[:500]

    meta.site_name = extract_meta(html, "og:site_name")[:120]
    meta.og_image = extract_meta(html, "og:image")[:500]
    return meta


def suggest_category(meta: CloneMeta, idea: str) -> str:
    text = f"{meta.title} {meta.description} {meta.site_name} {idea}".lower()
    best = "Movie (Real-life)"
    best_score = 0
    for cat, words in KW.items():
        score = sum(1 for w in words if w in text)
        if score > best_score:
            best, best_score = cat, score
    return best if best_score >= 2 else "Movie (Real-life)"


# =========================
# Auto continuity (no UI fields)
# =========================
def auto_continuity(category: str, meta: CloneMeta, idea: str) -> Dict[str, str]:
    flavor = ""
    if meta.title:
        flavor += f" Source inspiration: {meta.title}."
    if idea:
        flavor += f" Idea: {idea}."

    if category == "Animals (Real-life)":
        actor = "Same wild animal subject across scenes (species-accurate markings, consistent size/features, natural behavior)." + flavor
        setting = "Same natural habitat; respectful distance; time-of-day evolves logically."
        mood = "Observational calm → behavior highlight → calm exit."
    elif category == "DIY":
        actor = "Same craftsperson across scenes (consistent hands/identity; same outfit: dark shirt + apron)." + flavor
        setting = "Same clean workshop/workbench; tools remain consistent; lighting coherent."
        mood = "Clean progress beats → satisfying reveal."
    elif category in ["Bushcraft", "Shelter", "Survival"]:
        actor = "Same outdoors person across scenes (consistent identity; same outfit: olive jacket, cargo pants, boots, backpack)." + flavor
        setting = "Same outdoor location continuity; weather/time shifts gradually; no random jumps."
        mood = "Rising tension → decision → action → relief." if category == "Survival" else "Calm focus → steady progress → satisfying completion."
    else:
        actor = "Same main character across scenes (consistent identity, outfit, hairstyle, props)." + flavor
        setting = "Same world continuity; location evolves logically scene-to-scene; consistent time progression."
        mood = "Cinematic build-up → turning point → resolution."

    rules = "Realistic physics. Continuity enforced. No random costume/prop changes. No text/watermarks/logos."
    return {"actor_lock": actor, "setting_lock": setting, "mood_arc": mood, "rules": rules}


# =========================
# Scene planner (unique beats)
# =========================
def build_beats(n: int, category: str) -> List[Tuple[str, str]]:
    if category == "Animals (Real-life)":
        base = [
            ("Establish Habitat", "Wide habitat shot; subtle animal presence."),
            ("First Sighting", "Animal enters naturally; no human influence."),
            ("Behavior Detail", "Close detail of natural behavior (foraging/grooming/listening)."),
            ("Interaction", "Non-violent interaction (pairing/parenting/group movement)."),
            ("Natural Challenge", "Terrain/weather obstacle; animal responds naturally."),
            ("Adaptation", "Highlight a species adaptation (speed/hearing/camouflage)."),
            ("Calm Moment", "Quiet pause; emphasize ambience."),
            ("Exit", "Animal leaves frame; habitat remains; soft ending."),
        ]
    else:
        base = [
            ("Opening Shot", "Establish subject, location, and goal; show the ‘before’ state."),
            ("Rising Tension", "Introduce a constraint (time/weather/missing item/unexpected issue)."),
            ("First Action", "Do first key step; include close detail of hands/tools/materials."),
            ("Progress Check", "Show measurable progress; improve stability/efficiency."),
            ("Complication", "Something goes wrong; fix it logically (no magic)."),
            ("Second Action", "Next major step; emphasize technique and realism."),
            ("Turning Point", "Milestone achieved; satisfying progress moment."),
            ("Final Push", "Finish the final step; secure/verify result."),
            ("Result Reveal", "Hero reveal of the finished result; clean wide shot."),
            ("Outro", "Calm ending; confirm continuity; tease next project."),
        ]
    return (base * ((n // len(base)) + 1))[:n]


def vary_camera(i: int) -> str:
    moves = [
        "slow push-in", "gentle pan", "static locked-off", "low-angle reveal",
        "over-the-shoulder detail insert", "macro close-up", "wide establishing",
        "rack focus", "top-down instructional angle", "handheld follow"
    ]
    return moves[i % len(moves)]


# =========================
# Scene prompt builder (high detail)
# =========================
def build_scene_prompts(
    idx: int,
    n: int,
    category: str,
    orientation: str,
    continuity: Dict[str, str],
    meta: CloneMeta,
    idea: str,
    title: str,
    beat: str,
    scene_seconds: int,
    seed: int,
    detail_level: str,
    negative: str
) -> Tuple[str, str, str]:
    preset = STYLE_PRESETS[category]
    cam_move = vary_camera(idx)

    detail_phrase = {
        "Normal": "high realism, clean detail",
        "High": "ultra-detailed, micro-textures, crisp edges, natural grain",
        "Max": "extreme detail, micro-textures, realistic lighting physics, cinematic color science",
    }[detail_level]

    clone_line = f"Source={meta.source}; Title={meta.title}; Desc={meta.description}" if (meta.title or meta.description) else f"Source={meta.source}"

    story = (
        f"{beat}\n"
        f"Continuity: {continuity['actor_lock']} | {continuity['setting_lock']}\n"
        f"Mood arc: {continuity['mood_arc']}\n"
        f"Clone cues: {clone_line}"
    ).strip()

    video_prompt = f"""SCENE {idx+1}/{n} — {title} [{orientation}] (~{scene_seconds}s)
STYLE: {preset['vibe']}. Camera: {preset['camera']}. Lighting: {preset['lighting']}.
CAMERA MOVE: {cam_move}. Pacing: controlled, cinematic, cause→effect.
CONTINUITY (LOCKED): {continuity['actor_lock']} ; {continuity['setting_lock']}.
MOOD ARC: {continuity['mood_arc']}.
SCENE BEAT: {beat}
RULES: {continuity['rules']}
SEED NOTE: {seed}
""".strip()

    image_prompt = f"""IMAGE PROMPT — Scene {idx+1}/{n} — {title} ({category}) [{orientation}]
{detail_phrase}. {preset['vibe']}. Camera: {preset['camera']}. Lighting: {preset['lighting']}.
Camera move feel: {cam_move}. Strong composition, readable action, no text.
Subject lock: {continuity['actor_lock']}.
Setting lock: {continuity['setting_lock']}.
Beat: {beat}.
Clone cues: {clone_line}.
""".strip()

    return story, video_prompt, image_prompt


# =========================
# OpenAI Images (web-only generation)
# =========================
def gen_image_openai(prompt: str, width: int, height: int, api_key: str, model: str = "gpt-image-1") -> bytes:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in Streamlit Secrets.")
    url = "https://api.openai.com/v1/images/generations"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "prompt": prompt,
        "size": f"{width}x{height}",
        "n": 1,
        "response_format": "b64_json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=180)
    r.raise_for_status()
    j = r.json()
    return base64.b64decode(j["data"][0]["b64_json"])


# =========================
# ZIP Export
# =========================
def build_zip(payload: Dict, scenes: List[SceneOut]) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(payload, ensure_ascii=False, indent=2))
        for s in scenes:
            base = f"scenes/scene_{s.idx+1:02d}"
            z.writestr(f"{base}/story.txt", s.story)
            z.writestr(f"{base}/video_prompt.txt", s.video_prompt)
            z.writestr(f"{base}/image_prompt.txt", s.image_prompt)
            z.writestr(f"{base}/negative.txt", s.negative_prompt)
            z.writestr(f"{base}/seed.txt", str(s.seed))
            if s.image_bytes:
                z.writestr(f"{base}/image.png", s.image_bytes)
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

st.title(APP_TITLE)
st.caption("Web-only: URL + Idea → auto continuity → detailed prompts → OpenAI images per scene → ZIP/JSON export.")

if not auth_gate():
    st.stop()

with st.sidebar:
    st.header("Inputs")
    url = st.text_input("URL (FB/TikTok/IG/YT/Web)", placeholder="Paste link here")
    idea = st.text_area("Idea (optional)", placeholder="One sentence about what you want (improves accuracy)", height=90)
    auto_meta = st.checkbox("Auto fetch metadata", True)

    st.divider()
    category = st.selectbox("Category", CATEGORIES, 0)
    orientation = st.selectbox("Orientation", list(ORIENTATIONS.keys()), 0)
    total_duration = st.number_input("Total duration (seconds)", 10, 3600, 60, 5)
    scene_len = st.number_input("Seconds per scene", 2, 60, 6, 1)
    detail_level = st.selectbox("Detail level", ["Normal", "High", "Max"], 1)
    negative = st.text_area("Negative prompt", DEFAULT_NEGATIVE, height=80)

    st.divider()
    st.subheader("Image Engine (Web)")
    backend = st.selectbox("Generate images with", ["None (prompts only)", "OpenAI Images (web)"], 1)
    openai_model = st.text_input("OpenAI image model", "gpt-image-1")
    base_seed = st.number_input("Base seed", 0, 2_000_000_000, 123456, 1)

    go = st.button("Generate Scene Cards", type="primary")

meta = CloneMeta(url=url, source=detect_source(url))
if auto_meta and url:
    with st.spinner("Fetching metadata…"):
        meta = build_clone_meta(url)

auto_cat = suggest_category(meta, idea)
category_final = auto_cat if category == "Auto" else category
continuity = auto_continuity(category_final, meta, idea)

c1, c2 = st.columns(2)
with c1:
    st.subheader("Auto Analysis")
    st.write(f"**Source:** `{meta.source}`")
    st.write(f"**Suggested category:** `{auto_cat}`")
    st.write(f"**Final category:** `{category_final}`")
with c2:
    st.subheader("Clone Meta")
    st.write(f"**Title:** {meta.title}")
    st.write(f"**Description:** {meta.description}")

if go:
    W, H = ORIENTATIONS[orientation]
    n = max(1, int(total_duration // scene_len))
    beats = build_beats(n, category_final)

    scenes: List[SceneOut] = []
    for i in range(n):
        title, beat = beats[i]
        seed = int(base_seed) + i * 17

        story, vp, ip = build_scene_prompts(
            idx=i, n=n, category=category_final, orientation=orientation,
            continuity=continuity, meta=meta, idea=idea,
            title=title, beat=beat,
            scene_seconds=int(scene_len), seed=seed,
            detail_level=detail_level, negative=negative
        )

        scenes.append(SceneOut(
            idx=i, title=title, beat=beat, story=story,
            video_prompt=vp, image_prompt=ip,
            negative_prompt=negative, seed=seed,
            image_bytes=None
        ))

    if backend == "OpenAI Images (web)":
        api_key = get_secret("OPENAI_API_KEY", "")
        st.info("Generating images via OpenAI (web)…")
        for s in scenes:
            try:
                s.image_bytes = gen_image_openai(s.image_prompt, W, H, api_key=api_key, model=openai_model)
            except Exception as e:
                s.image_bytes = None
                st.warning(f"Scene {s.idx+1} image failed: {e}")

    payload = {
        "meta": asdict(meta),
        "idea": idea,
        "category": category_final,
        "orientation": orientation,
        "continuity_auto": continuity,
        "scenes": [asdict(s) for s in scenes],
    }
    st.session_state["scenes"] = scenes
    st.session_state["payload"] = payload
    st.success("Done. Scroll down for cards + export.")

if "scenes" in st.session_state:
    scenes = st.session_state["scenes"]
    payload = st.session_state["payload"]

    st.divider()
    st.subheader("Scene Cards")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_bytes = build_zip(payload, scenes)

    colA, colB, colC = st.columns(3)
    with colA:
        st.download_button("Download JSON", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                           file_name=f"project_{stamp}.json")
    with colB:
        st.download_button("Download ZIP (txt+images)", zip_bytes,
                           file_name=f"project_{stamp}.zip", mime="application/zip")
    with colC:
        st.link_button("Open Google Whisk", WHISK_URL)

    for r in range(0, len(scenes), 2):
        cols = st.columns(2)
        for j in range(2):
            k = r + j
            if k >= len(scenes):
                break
            s = scenes[k]
            with cols[j]:
                st.markdown(f"### SCENE {s.idx+1} — {s.title}")
                if s.image_bytes:
                    st.image(s.image_bytes, use_container_width=True)
                else:
                    st.caption("No image generated (prompts only).")

                st.markdown("**STORY**")
                st.write(s.story)

                st.markdown("**PROMPT (VIDEO)**")
                st.code(s.video_prompt, language="text")

                st.markdown("**PROMPT (IMAGE)**")
                st.code(s.image_prompt, language="text")

                st.caption(f"Seed: {s.seed}")

    st.divider()
    st.subheader("Whisk-ready (copy/paste)")
    whisk_pack = "\n\n".join([f"[Scene {s.idx+1}] {s.image_prompt}" for s in scenes])
    st.text_area("All IMAGE prompts", value=whisk_pack, height=220)
