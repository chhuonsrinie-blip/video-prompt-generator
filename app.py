import base64
import io
import json
import math
import os
import re
import time
import zipfile
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

# =========================
# App config
# =========================
APP_TITLE = "Scene Cards Prompt Generator"
DEFAULT_NEGATIVE = (
    "lowres, blurry, jpeg artifacts, watermark, text, logo, bad anatomy, "
    "extra limbs, deformed hands, disfigured, duplicate, cropped, worst quality"
)
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

ORIENTATIONS = {
    "horizontal 16:9": (1280, 720),
    "vertical 9:16": (720, 1280),
    "square 1:1": (1024, 1024),
}

STYLE_PRESETS = {
    "Bushcraft": dict(vibe="authentic bushcraft realism, tactile materials, practical tools",
                      camera="35mm documentary-cinema, stable handheld, shallow DOF",
                      lighting="natural light, golden hour or overcast realism"),
    "Survival": dict(vibe="survival realism, urgency and constraints, grounded decisions",
                     camera="cinematic documentary, close-medium emotion + wide context",
                     lighting="moody overcast or dusk, realistic contrast"),
    "Shelter": dict(vibe="shelter-building realism, clear progress beats, safe depiction",
                    camera="stable framing, medium-wide for structure + detail inserts",
                    lighting="daylight shifting toward dusk, consistent progression"),
    "DIY": dict(vibe="clean DIY tutorial realism, satisfying progress means",
                camera="tripod-stable, top-down + side angle, crisp detail",
                lighting="soft practical lighting, clean shadows"),
    "Movie (Real-life)": dict(vibe="cinematic real-life film look, high production",
                              camera="35mm film look, one clear camera move per scene, shallow DOF",
                              lighting="motivated cinematic lighting, practical sources"),
    "Animals (Real-life)": dict(vibe="wildlife documentary realism, species-accurate behavior",
                                camera="telephoto look, stable handheld, natural bokeh",
                                lighting="natural outdoor light, true-to-life exposure"),
}

KW = {
    "Bushcraft": ["bushcraft","campfire","forest camp","tarp","cordage","axe","knife","kindling","outdoor camp"],
    "Survival": ["survival","wilderness","stranded","lost","storm","rescue","signal","navigation","sos","water source"],
    "Shelter": ["shelter","lean-to","debris hut","hut","tarp shelter","windbreak","insulation","camp shelter"],
    "DIY": ["diy","how to","tutorial","build","make","craft","workbench","assemble","tools","woodworking"],
    "Animals (Real-life)": ["wildlife","animal","animals","nature","documentary","dog","cat","lion","tiger","elephant","bird","shark","whale"],
    "Movie (Real-life)": ["cinematic","film","movie","trailer","scene","thriller","noir","neon","action"],
}

WHISK_URL = "https://labs.google/fx/tools/whisk"


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
    image: str = ""


@dataclass
class SceneOut:
    idx: int
    title: str
    story: str
    video_prompt: str
    image_prompt: str
    negative_prompt: str
    seed: int
    image_bytes: Optional[bytes] = None


# =========================
# Password gate
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
        st.warning("No password configured. Set APP_PASSWORD in Streamlit Secrets or env var.")
        return False

    if "authed" not in st.session_state:
        st.session_state.authed = False

    if st.session_state.authed:
        return True

    st.markdown("## 🔒 Private App")
    attempt = st.text_input("Password", type="password")
    if st.button("Login"):
        if attempt == password:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Wrong password.")
    return False


# =========================
# URL clone (NO bs4)
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
    )[:400]

    meta.site_name = extract_meta(html, "og:site_name")[:120]
    meta.image = extract_meta(html, "og:image")[:500]
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
# AUTO Continuity Builder (NO UI fields)
# =========================
def auto_continuity(category: str, meta: CloneMeta, idea: str) -> Dict[str, str]:
    t = f"{meta.title} {meta.description} {idea}".strip()

    if category == "Animals (Real-life)":
        actor = "Same wild animal subject across scenes (species-accurate markings, consistent size/features, natural behavior)."
        setting = "Same natural habitat; camera keeps respectful distance; time-of-day evolves logically."
        mood = "Observational calm → behavior highlight → calm exit."
    elif category == "DIY":
        actor = "Same craftsperson across scenes (consistent hands/identity; same outfit: dark shirt + apron)."
        setting = "Same clean workshop and workbench; tools remain consistent; lighting stays coherent."
        mood = "Clean progress beats → satisfying reveal."
    elif category in ["Bushcraft", "Shelter", "Survival"]:
        actor = "Same outdoors person across scenes (consistent identity; same outfit: olive jacket, cargo pants, boots, backpack)."
        setting = "Same outdoor location continuity; weather/time shift gradually; no random location jumps."
        mood = "Calm focus → steady progress → satisfying completion." if category != "Survival" else "Rising tension → decision → action → relief."
    else:
        actor = "Same main character across scenes (consistent identity, outfit, hairstyle, props)."
        setting = "Same world continuity; location evolves logically scene-to-scene; time progression consistent."
        mood = "Cinematic build-up → turning point → resolution."

    rules = "Realistic physics. Continuity enforced. No random costume/prop changes. No text/watermarks/logos."

    # Add some meta flavor if present
    flavor = ""
    if meta.title:
        flavor += f" Inspired by source title: {meta.title}."
    if idea:
        flavor += f" Idea: {idea}."

    return {
        "actor_lock": actor + flavor,
        "setting_lock": setting,
        "mood_arc": mood,
        "rules": rules,
    }


# =========================
# Scene beats (unique)
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
            ("First Action", "Do first key step; include close details of hands/tools/materials."),
            ("Progress Check", "Show measurable progress; improve stability/efficiency."),
            ("Complication", "Something goes wrong; fix it logically (no magic)."),
            ("Second Action", "Next major step; emphasize technique and realism."),
            ("Turning Point", "Milestone achieved; satisfying progress moment."),
            ("Final Push", "Finish the final step; secure/verify result."),
            ("Result Reveal", "Hero reveal of the finished result; clean wide shot."),
            ("Outro", "Calm ending; confirm continuity; tease next project."),
        ]

    out = (base * ((n // len(base)) + 1))[:n]
    return [(f"{i+1}. {t}", b) for i, (t, b) in enumerate(out)]


def vary_camera(i: int) -> str:
    moves = [
        "slow push-in", "gentle pan", "static locked-off", "low-angle reveal",
        "over-the-shoulder detail insert", "macro close-up", "wide establishing",
        "rack focus", "top-down instructional angle", "handheld follow"
    ]
    return moves[i % len(moves)]


# =========================
# Prompt builder (detailed + unique)
# =========================
def build_prompts(
    idx: int,
    n: int,
    category: str,
    orientation: str,
    continuity: Dict[str, str],
    meta: CloneMeta,
    beat_title: str,
    beat_text: str,
    scene_seconds: int,
    seed: int,
    detail_level: str,
) -> Tuple[str, str]:
    preset = STYLE_PRESETS[category]
    cam_move = vary_camera(idx)

    detail_phrase = {
        "Normal": "high realism, clean detail",
        "High": "ultra-detailed, micro-textures, crisp edges, natural grain",
        "Max": "extreme detail, micro-textures, realistic lighting physics, cinematic color science",
    }[detail_level]

    clone_line = f"Source={meta.source}; Title={meta.title}; Desc={meta.description}" if (meta.title or meta.description) else f"Source={meta.source}"

    story = (
        f"{beat_text}\n"
        f"Continuity: {continuity['actor_lock']} | {continuity['setting_lock']}\n"
        f"Mood arc: {continuity['mood_arc']}\n"
        f"Clone cues: {clone_line}"
    )

    video_prompt = f"""SCENE {idx+1}/{n} — {beat_title} [{orientation}] (~{scene_seconds}s)
STYLE: {preset['vibe']}. {preset['camera']}. Lighting: {preset['lighting']}.
CAMERA MOVE: {cam_move}. Pacing: controlled, cinematic, clear cause-effect.
CONTINUITY (LOCKED): {continuity['actor_lock']} ; {continuity['setting_lock']}.
MOOD ARC: {continuity['mood_arc']}.
SCENE BEAT: {beat_text}
RULES: {continuity['rules']}
SEED NOTE (for consistency): {seed}
""".strip()

    image_prompt = f"""IMAGE PROMPT — Scene {idx+1}/{n} — {beat_title} ({category}) [{orientation}]
{detail_phrase}. {preset['vibe']}. {preset['camera']}. {preset['lighting']}.
Camera move feel: {cam_move}. Strong composition, readable action, no text.
Subject lock: {continuity['actor_lock']}.
Setting lock: {continuity['setting_lock']}.
Beat: {beat_text}.
Clone cues: {clone_line}.
""".strip()

    return story, video_prompt, image_prompt


# =========================
# Image generation (A1111 + OpenAI)
# =========================
def gen_image_a1111(
    a1111_url: str,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
    sampler: str,
) -> bytes:
    endpoint = a1111_url.rstrip("/") + "/sdapi/v1/txt2img"
    payload = {
        "prompt": prompt,
        "negative_prompt": negative,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg_scale": cfg,
        "seed": seed,
        "sampler_name": sampler,
        "batch_size": 1,
        "n_iter": 1,
    }
    r = requests.post(endpoint, json=payload, timeout=180)
    r.raise_for_status()
    data = r.json()
    return base64.b64decode(data["images"][0])


def gen_image_openai(
    prompt: str,
    width: int,
    height: int,
    api_key: str,
    model: str = "gpt-image-1",
) -> bytes:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set.")
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
# Scene card image (PNG export)
# =========================
def render_scene_card_png(scene: SceneOut, orientation: str) -> bytes:
    W, H = ORIENTATIONS[orientation]
    pad = 36
    bg = (12, 18, 32)
    fg = (235, 240, 255)
    accent = (60, 160, 255)

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("DejaVuSans.ttf", 44)
        font_h = ImageFont.truetype("DejaVuSans.ttf", 24)
        font_p = ImageFont.truetype("DejaVuSans.ttf", 18)
    except Exception:
        font_title = ImageFont.load_default()
        font_h = ImageFont.load_default()
        font_p = ImageFont.load_default()

    draw.text((pad, pad), f"SCENE {scene.idx+1} — {scene.title}", fill=fg, font=font_title)

    y = pad + 80
    img_h = int(H * 0.40)
    img_w = W - pad * 2

    if scene.image_bytes:
        try:
            gen = Image.open(io.BytesIO(scene.image_bytes)).convert("RGB")
            gen = gen.resize((img_w, img_h))
            img.paste(gen, (pad, y))
        except Exception:
            draw.rectangle([pad, y, pad + img_w, y + img_h], outline=accent, width=3)
            draw.text((pad + 12, y + 12), "Image decode failed", fill=accent, font=font_h)
    else:
        draw.rectangle([pad, y, pad + img_w, y + img_h], outline=accent, width=3)
        draw.text((pad + 12, y + 12), "No image generated", fill=accent, font=font_h)

    y2 = y + img_h + 22
    draw.text((pad, y2), "STORY", fill=accent, font=font_h)
    y2 += 30

    story = scene.story[:900].replace("\n", " ")
    wrapped = textwrap.wrap(story, width=75)
    for line in wrapped[:8]:
        draw.text((pad, y2), line, fill=fg, font=font_p)
        y2 += 22

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# =========================
# Export (ZIP/JSON/TXT)
# =========================
def build_zip(meta: Dict[str, Any], scenes: List[SceneOut], orientation: str) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(meta, ensure_ascii=False, indent=2))
        for s in scenes:
            base = f"scenes/scene_{s.idx+1:02d}"
            z.writestr(f"{base}/story.txt", s.story)
            z.writestr(f"{base}/video_prompt.txt", s.video_prompt)
            z.writestr(f"{base}/image_prompt.txt", s.image_prompt)
            z.writestr(f"{base}/negative.txt", s.negative_prompt)
            z.writestr(f"{base}/seed.txt", str(s.seed))
            if s.image_bytes:
                z.writestr(f"{base}/image.png", s.image_bytes)
            z.writestr(f"{base}/scene_card.png", render_scene_card_png(s, orientation))
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
st.caption("Now: URL + Idea only. Continuity is auto-built. Scenes are detailed and different. Optional A1111/OpenAI image generation.")

if not auth_gate():
    st.stop()

# Sidebar (minimal inputs only)
with st.sidebar:
    st.header("Inputs")
    url = st.text_input("URL (FB/TikTok/IG/YT/Web)", placeholder="Paste link here")
    idea = st.text_area("Idea (optional)", placeholder="One sentence about what you want (helps accuracy)", height=90)
    auto_meta = st.checkbox("Auto fetch metadata", True)

    st.divider()
    category = st.selectbox("Category", CATEGORIES, 0)
    orientation = st.selectbox("Orientation", list(ORIENTATIONS.keys()), 1)
    total_duration = st.number_input("Total duration (seconds)", 10, 3600, 60, 5)
    scene_len = st.number_input("Seconds per scene", 2, 60, 6, 1)
    detail_level = st.selectbox("Detail level", ["Normal", "High", "Max"], 1)
    negative = st.text_area("Negative prompt", DEFAULT_NEGATIVE, height=80)

    st.divider()
    st.subheader("Image Engine")
    backend = st.selectbox("Generate images with", ["None (prompts only)", "Local A1111 (free)", "OpenAI Images"], 0)

    if backend == "Local A1111 (free)":
        a1111_url = st.text_input("A1111 URL", "http://127.0.0.1:7860")
        steps = st.slider("Steps", 10, 60, 28)
        cfg = st.slider("CFG", 3.0, 12.0, 6.5)
        sampler = st.text_input("Sampler", "DPM++ 2M Karras")
    else:
        a1111_url = ""
        steps = 28
        cfg = 6.5
        sampler = "DPM++ 2M Karras"

    if backend == "OpenAI Images":
        openai_model = st.text_input("OpenAI image model", "gpt-image-1")
    else:
        openai_model = "gpt-image-1"

    base_seed = st.number_input("Base seed", 0, 2_000_000_000, 123456, 1)

    go = st.button("Generate Scene Cards", type="primary")

# Build clone metadata
meta = CloneMeta(url=url, source=detect_source(url))
if auto_meta and url:
    with st.spinner("Fetching metadata…"):
        meta = build_clone_meta(url)

auto_cat = suggest_category(meta, idea)
category_final = auto_cat if category == "Auto" else category

# Auto continuity (no UI)
continuity = auto_continuity(category_final, meta, idea)

# Display analysis
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
        beat_title, beat_text = beats[i]
        seed = int(base_seed) + i * 17

        story, vp, ip = build_prompts(
            idx=i, n=n, category=category_final, orientation=orientation,
            continuity=continuity, meta=meta,
            beat_title=beat_title, beat_text=beat_text,
            scene_seconds=int(scene_len), seed=seed, detail_level=detail_level
        )

        scenes.append(SceneOut(
            idx=i, title=beat_title, story=story,
            video_prompt=vp, image_prompt=ip,
            negative_prompt=negative, seed=seed, image_bytes=None
        ))

    # Generate images if chosen
    if backend != "None (prompts only)":
        st.info("Generating images…")
        for s in scenes:
            try:
                if backend == "Local A1111 (free)":
                    s.image_bytes = gen_image_a1111(
                        a1111_url=a1111_url, prompt=s.image_prompt, negative=s.negative_prompt,
                        width=W, height=H, steps=int(steps), cfg=float(cfg),
                        seed=int(s.seed), sampler=sampler
                    )
                elif backend == "OpenAI Images":
                    key = get_secret("OPENAI_API_KEY", "")
                    s.image_bytes = gen_image_openai(
                        prompt=s.image_prompt, width=W, height=H,
                        api_key=key, model=openai_model
                    )
            except Exception as e:
                s.image_bytes = None
                st.warning(f"Scene {s.idx+1} image failed: {e}")

    # Save to session
    st.session_state["scenes"] = scenes
    st.session_state["meta"] = meta
    st.session_state["category_final"] = category_final
    st.session_state["orientation"] = orientation
    st.session_state["continuity"] = continuity
    st.success("Generated. Scroll down to view scene cards + export.")

# Render
if "scenes" in st.session_state:
    scenes = st.session_state["scenes"]
    meta = st.session_state["meta"]
    category_final = st.session_state["category_final"]
    orientation = st.session_state["orientation"]
    continuity = st.session_state["continuity"]

    st.divider()
    st.subheader("Scene Cards")

    # Exports
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    meta_payload = {
        "meta": asdict(meta),
        "category": category_final,
        "orientation": orientation,
        "continuity_auto": continuity,
        "scenes": [asdict(s) for s in scenes],
    }

    colA, colB, colC = st.columns(3)
    with colA:
        st.download_button("Download JSON", json.dumps(meta_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                           file_name=f"project_{stamp}.json")
    with colB:
        zip_bytes = build_zip(meta_payload, scenes, orientation)
        st.download_button("Download ZIP (txt+images+cards)", zip_bytes, file_name=f"project_{stamp}.zip")
    with colC:
        st.link_button("Open Google Whisk", WHISK_URL)

    # Cards 2-per-row
    for r in range(0, len(scenes), 2):
        cols = st.columns(2)
        for j in range(2):
            k = r + j
            if k >= len(scenes): break
            s = scenes[k]
            with cols[j]:
                st.markdown(
                    f"""<div class="scene-card">
<div class="scene-header">
  <div>SCENE {s.idx+1} — {s.title}</div>
  <div class="pill">{category_final}</div>
</div>
</div>""",
                    unsafe_allow_html=True,
                )

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
