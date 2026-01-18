import base64
import io
import json
import math
import os
import re
import zipfile
import textwrap
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

# =========================
# CONFIG
# =========================
APP_TITLE = "Storyboard Studio"
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

ORIENTATIONS = {
    "Landscape (16:9)": (1536, 1024),
    "Portrait (9:16)": (1024, 1536),
    "Square (1:1)": (1024, 1024),
}

DEFAULT_NEGATIVE = "text, watermark, logo, lowres, blurry, deformed, extra limbs, bad anatomy"
UA = {"User-Agent": "Mozilla/5.0 (StoryboardStudio/1.0)"}

STYLE_PRESETS = {
    "Bushcraft": dict(vibe="authentic bushcraft realism, tactile materials, practical outdoor gear",
                      camera="35mm documentary-cinema, stable handheld, shallow DOF",
                      lighting="natural light, golden hour or overcast realism"),
    "Survival": dict(vibe="survival realism, urgency, constraints, grounded decisions",
                     camera="cinematic documentary, close-medium emotion + wide context",
                     lighting="moody overcast or dusk, realistic contrast"),
    "Shelter": dict(vibe="shelter-building realism, clear progress beats, safe depiction",
                    camera="stable framing, medium-wide + detail inserts",
                    lighting="daylight shifting toward dusk, consistent progression"),
    "DIY": dict(vibe="clean DIY tutorial realism, satisfying progress beats, crisp detail",
                camera="tripod-stable, top-down + side angle, close-ups of hands/tools",
                lighting="soft practical lighting, clean shadows"),
    "Movie (Real-life)": dict(vibe="cinematic real-life film look, high production, coherent art direction",
                              camera="35mm film look, one clear camera move per scene, shallow DOF",
                              lighting="motivated cinematic lighting, practical sources"),
    "Animals (Real-life)": dict(vibe="wildlife documentary realism, species-accurate behavior, true-to-life color",
                                camera="telephoto look, stable handheld, natural bokeh",
                                lighting="natural outdoor light, true-to-life exposure"),
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
# MODELS
# =========================
@dataclass
class CloneMeta:
    url: str
    source: str
    title: str = ""
    description: str = ""
    og_image: str = ""


@dataclass
class SceneOut:
    idx: int
    title: str
    story: str
    prompt_imagefx: str
    prompt_video: str
    negative: str
    seed: int
    image_bytes: Optional[bytes] = None


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
    password = get_secret("APP_PASSWORD", "")
    if not password:
        st.warning("No password configured. Add APP_PASSWORD in Streamlit Secrets.")
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
# URL METADATA (NO BS4)
# =========================
def detect_source(url: str) -> str:
    u = (url or "").lower()
    if "tiktok.com" in u: return "TikTok"
    if "instagram.com" in u or "instagr.am" in u: return "Instagram"
    if "facebook.com" in u or "fb.watch" in u: return "Facebook"
    if "youtube.com" in u or "youtu.be" in u: return "YouTube"
    return "Web"


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=12, allow_redirects=True)
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


def build_clone_meta(url: str) -> CloneMeta:
    meta = CloneMeta(url=url, source=detect_source(url))
    if not url:
        return meta
    try:
        html = fetch_html(url)
    except Exception:
        return meta

    meta.title = (extract_meta(html, "og:title") or extract_title_tag(html))[:200]
    meta.description = (extract_meta(html, "og:description") or extract_meta(html, "description"))[:500]
    meta.og_image = extract_meta(html, "og:image")[:500]
    return meta


def infer_category(meta: CloneMeta, idea: str) -> str:
    text = f"{meta.title} {meta.description} {idea}".lower()
    best = "Movie (Real-life)"
    best_score = 0
    for cat, words in KW.items():
        score = sum(1 for w in words if w in text)
        if score > best_score:
            best, best_score = cat, score
    return best if best_score >= 2 else "Movie (Real-life)"


# =========================
# AUTO CONTINUITY (HIDDEN)
# =========================
def auto_continuity(category: str, meta: CloneMeta, idea: str) -> Dict[str, str]:
    flavor = ""
    if meta.title:
        flavor += f" Inspired by: {meta.title}."
    if idea:
        flavor += f" Idea: {idea}."

    if category == "Animals (Real-life)":
        actor = "Same wild animal subject across scenes (species-accurate markings, consistent features, natural behavior)." + flavor
        setting = "Same natural habitat; respectful distance; time-of-day evolves logically."
        mood = "Observational calm → behavior highlight → calm exit."
    elif category == "DIY":
        actor = "Same craftsperson across scenes (same outfit: dark shirt + apron; consistent hands/identity)." + flavor
        setting = "Same clean workshop/workbench; tools remain consistent; lighting coherent."
        mood = "Clean progress beats → satisfying reveal."
    elif category in ["Bushcraft", "Shelter", "Survival"]:
        actor = "Same outdoors person across scenes (olive jacket, cargo pants, boots, backpack; consistent identity)." + flavor
        setting = "Same outdoor location continuity; weather/time shift gradually; no random jumps."
        mood = "Rising tension → decision → action → relief." if category == "Survival" else "Calm focus → steady progress → satisfying completion."
    else:
        actor = "Same main character across scenes (consistent identity/outfit/props)." + flavor
        setting = "Same world continuity; location evolves logically scene-to-scene; consistent time progression."
        mood = "Cinematic build-up → turning point → resolution."

    rules = "Realistic physics. Continuity enforced. No text/watermarks/logos."
    return {"actor": actor, "setting": setting, "mood": mood, "rules": rules}


# =========================
# SCENE BEATS (UNIQUE)
# =========================
def build_beats(n: int, category: str) -> List[Tuple[str, str]]:
    if category == "Animals (Real-life)":
        base = [
            ("Establish Habitat", "Wide habitat shot; subtle animal presence."),
            ("First Sighting", "Animal enters naturally; no human influence."),
            ("Behavior Detail", "Close detail of natural behavior (foraging/grooming/listening)."),
            ("Interaction", "Non-violent interaction (pairing/parenting/group movement)."),
            ("Natural Challenge", "Terrain/weather obstacle; animal responds naturally."),
            ("Adaptation", "Highlight adaptation (speed/hearing/camouflage)."),
            ("Calm Moment", "Quiet pause; emphasize ambience."),
            ("Exit", "Animal leaves frame; soft ending."),
        ]
    else:
        base = [
            ("Opening Shot", "Establish environment and subject."),
            ("Introduce Goal", "Introduce the goal clearly."),
            ("First Action", "Start the first meaningful action."),
            ("Progress", "Show progress with detail inserts."),
            ("Complication", "Something goes wrong; realistic fix."),
            ("Second Action", "Continue with the next major step."),
            ("Turning Point", "Milestone achieved; satisfying moment."),
            ("Final Push", "Finish the final step; verify result."),
            ("Result Reveal", "Hero reveal of finished result."),
            ("Outro", "Calm ending; consistent final shot."),
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
# PROMPT BUILDER (ImageFX + Video prompt)
# =========================
def build_prompts(idx: int, total: int, title: str, beat: str, category: str, orientation: str,
                  cont: Dict[str, str], meta: CloneMeta, detail_level: str, negative: str) -> Tuple[str, str, str]:
    preset = STYLE_PRESETS[category]
    cam_move = vary_camera(idx)

    detail_phrase = {
        "Normal": "high realism, clean detail",
        "High": "ultra-detailed, micro-textures, crisp edges, natural grain",
        "Max": "extreme detail, micro-textures, realistic lighting physics, cinematic color science",
    }[detail_level]

    story = f"{beat}\nActor: {cont['actor']}\nSetting: {cont['setting']}\nMood: {cont['mood']}"

    prompt_imagefx = f"""Ultra-realistic cinematic photo.
{detail_phrase}.
{preset['vibe']}. {preset['camera']}. {preset['lighting']}.
Camera move feel: {cam_move}. Strong composition, readable action.
{beat}
Continuity: {cont['actor']} {cont['setting']}.
Mood arc: {cont['mood']}.
Rules: {cont['rules']}.
No text, no watermark.
""".strip()

    prompt_video = f"""Scene {idx+1}/{total} ({orientation})
Category: {category}
Title: {title}
Beat: {beat}
Camera move: {cam_move}
Continuity: {cont['actor']} | {cont['setting']}
Mood: {cont['mood']}
Inspired by: {meta.source} {meta.title}
""".strip()

    return story, prompt_imagefx, prompt_video


# =========================
# OPTIONAL: Create a placeholder image card (visual storyboard card)
# =========================
def placeholder_scene_image(title: str, prompt: str, w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (12, 18, 32))
    d = ImageDraw.Draw(img)
    try:
        ft = ImageFont.truetype("DejaVuSans.ttf", 30)
        fb = ImageFont.truetype("DejaVuSans.ttf", 18)
    except Exception:
        ft = ImageFont.load_default()
        fb = ImageFont.load_default()

    d.text((24, 20), title, fill=(235, 240, 255), font=ft)
    y = 80
    for line in textwrap.wrap(prompt[:900], width=70):
        d.text((24, y), line, fill=(200, 215, 235), font=fb)
        y += 22
        if y > h - 24:
            break

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# =========================
# Export ZIP/JSON
# =========================
def build_zip(payload: Dict, scenes: List[SceneOut]) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(payload, ensure_ascii=False, indent=2))
        for s in scenes:
            base = f"scenes/scene_{s.idx+1:02d}"
            z.writestr(f"{base}/story.txt", s.story)
            z.writestr(f"{base}/prompt_imagefx.txt", s.prompt_imagefx)
            z.writestr(f"{base}/prompt_video.txt", s.prompt_video)
            z.writestr(f"{base}/negative.txt", s.negative)
            z.writestr(f"{base}/seed.txt", str(s.seed))
            if s.image_bytes:
                z.writestr(f"{base}/scene.png", s.image_bytes)
    return mem.getvalue()


# =========================
# UI
# =========================
st.set_page_config(APP_TITLE, layout="wide")
st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] {background:#0b1220;}
[data-testid="stSidebar"] {background:#070c16;}
h1,h2,h3,h4, p, div, span, label {color:#eaf2ff;}
.scene-row {display:flex; gap:16px;}
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
st.caption("Format like your video: Concept/Prompt → Number of scenes → Resolution → Generate Storyboard → Scene cards.")

if not auth_gate():
    st.stop()

tab1, tab2, tab3 = st.tabs(["Prompt Generator", "Storyboard", "Image Analyzer (placeholder)"])

with tab1:
    st.subheader("Prompt Generator")
    st.write("Quick single prompt helper (optional). Use Storyboard tab for full workflow.")
    cat = st.selectbox("Category", CATEGORIES, 0, key="pg_cat")
    orient = st.selectbox("Orientation", list(ORIENTATIONS.keys()), 0, key="pg_or")
    concept = st.text_area("Concept / Prompt", height=140, key="pg_concept")
    if st.button("Generate Prompt", key="pg_btn"):
        meta = CloneMeta(url="", source="Manual", title="", description="", og_image="")
        category_final = cat if cat != "Auto" else "Movie (Real-life)"
        cont = auto_continuity(category_final, meta, concept)
        preset = STYLE_PRESETS[category_final]
        out = f"{preset['vibe']}. {preset['camera']}. {preset['lighting']}. Continuity: {cont['actor']} {cont['setting']}. No text."
        st.code(out, language="text")
        st.link_button("Open ImageFX", IMAGEFX_URL)

with tab2:
    st.subheader("Storyboard Studio")

    url = st.text_input("Source URL (optional)", key="sb_url")
    idea = st.text_area("Idea (optional)", key="sb_idea", height=90)

    category = st.selectbox("Category", CATEGORIES, 0, key="sb_cat")
    orientation = st.selectbox("Resolution", list(ORIENTATIONS.keys()), 0, key="sb_res")

    total_s = st.number_input("Total duration (s)", 10, 3600, 60, 5, key="sb_total")
    per_scene = st.number_input("Seconds per scene", 2, 60, 6, 1, key="sb_per")
    n = max(1, int(total_s // per_scene))

    detail_level = st.selectbox("Detail level", ["Normal", "High", "Max"], 1, key="sb_detail")
    negative = st.text_area("Negative prompt", DEFAULT_NEGATIVE, height=80, key="sb_neg")
    base_seed = st.number_input("Base seed", 0, 2_000_000_000, 123456, 1, key="sb_seed")

    # This app is ImageFX workflow (free): we generate placeholder storyboard images (not AI images)
    generate_btn = st.button("Generate Storyboard", type="primary", key="sb_go")

    if generate_btn:
        meta = build_clone_meta(url) if url else CloneMeta(url="", source="Manual")
        auto_cat = infer_category(meta, idea)
        category_final = auto_cat if category == "Auto" else category

        cont = auto_continuity(category_final, meta, idea)
        beats = build_beats(n, category_final)

        W, H = ORIENTATIONS[orientation]

        scenes: List[SceneOut] = []
        for i in range(n):
            title, beat = beats[i]
            seed = int(base_seed) + i * 17

            story, p_imgfx, p_vid = build_prompts(
                idx=i, total=n, title=title, beat=beat,
                category=category_final, orientation=orientation,
                cont=cont, meta=meta,
                detail_level=detail_level, negative=negative
            )

            # Placeholder image (since ImageFX has no API)
            card_img = placeholder_scene_image(f"SCENE {i+1} — {title}", p_imgfx, W, H)

            scenes.append(SceneOut(
                idx=i, title=title, beat=beat, story=story,
                prompt_imagefx=p_imgfx,
                prompt_video=p_vid,
                negative=negative,
                seed=seed,
                image_bytes=card_img
            ))

        payload = {
            "meta": asdict(meta),
            "category": category_final,
            "resolution": orientation,
            "total_seconds": total_s,
            "scene_seconds": per_scene,
            "idea": idea,
            "auto_continuity": cont,
            "scenes": [asdict(s) for s in scenes],
        }

        st.session_state["sb_scenes"] = scenes
        st.session_state["sb_payload"] = payload

        st.success("Storyboard generated. Use ImageFX to generate real images by pasting the Image Prompt.")

    if "sb_scenes" in st.session_state:
        scenes = st.session_state["sb_scenes"]
        payload = st.session_state["sb_payload"]

        st.divider()
        st.subheader("Storyboard Scenes")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_bytes = build_zip(payload, scenes)
        st.download_button("Download ZIP (scene prompts + placeholder images + JSON)", zip_bytes, f"storyboard_{stamp}.zip")

        colA, colB = st.columns(2)
        with colA:
            st.link_button("Open ImageFX", IMAGEFX_URL)
        with colB:
            st.link_button("Open Whisk", WHISK_URL)

        # Render scenes as image-left + prompt-right (like your video)
        for s in scenes:
            st.markdown(f"### SCENE {s.idx+1} — {s.title}")
            left, right = st.columns([1.1, 1.4])
            with left:
                st.image(s.image_bytes, use_container_width=True)
            with right:
                st.markdown("**STORY**")
                st.write(s.story)

                st.markdown("**PROMPT (ImageFX)**")
                st.code(s.prompt_imagefx, language="text")

                st.markdown("**PROMPT (Video)**")
                st.code(s.prompt_video, language="text")

                st.caption(f"Seed: {s.seed}")

        # One combined prompt pack for easy copy/paste
        whisk_pack = "\n\n".join([f"[Scene {s.idx+1}] {s.prompt_imagefx}" for s in scenes])
        st.divider()
        st.subheader("Copy/Paste Pack (ImageFX / Whisk)")
        st.text_area("All ImageFX prompts", value=whisk_pack, height=220)

with tab3:
    st.subheader("Image Analyzer (placeholder)")
    st.write("ImageFX/Whisk have no public API. This tab can later become a manual image upload analyzer.")
    st.info("If you want, I can add an image upload that extracts keywords and improves the prompts.")
