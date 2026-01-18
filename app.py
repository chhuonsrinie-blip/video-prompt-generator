import os, hmac, io, json, math, re, textwrap, zipfile
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import requests
import streamlit as st
import json
import zipfile
from io import BytesIO
from typing import List


# ---------------------------
# Password gate (Codespaces-safe)
# ---------------------------
def get_password():
    try:
        return st.secrets.get("APP_PASSWORD", os.getenv("APP_PASSWORD", ""))
    except Exception:
        return os.getenv("APP_PASSWORD", "")

def require_password():
    st.session_state.setdefault("authed", False)
    expected = get_password()

    if not expected:
        st.warning("No password configured yet. Set APP_PASSWORD in Streamlit Secrets (Cloud) or env var (Codespaces).")
        st.stop()

    if st.session_state["authed"]:
        return

    st.title("🔒 Private App")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        if hmac.compare_digest(p, expected):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()

require_password()


# ---------------------------
# App config
# ---------------------------
st.set_page_config(page_title="Scene Cards Prompt Generator", layout="wide")

CATEGORIES = [
    "Auto",
    "Bushcraft",
    "Survival",
    "Shelter",
    "DIY",
    "Movie (Real-life)",
    "Animals (Real-life)",
]

STYLE_PRESETS = {
    "Bushcraft": {
        "style": "real-life bushcraft cinematic realism, natural textures, authentic outdoor gear, coherent grading",
        "camera": "documentary-cinema, stable handheld, 35mm look, shallow DOF",
        "lighting": "natural light, golden hour or overcast realism",
    },
    "Survival": {
        "style": "real-life survival cinematic realism, tense but grounded, documentary authenticity",
        "camera": "tracking handheld realism, close-medium emotion shots + wide context, 35mm look",
        "lighting": "moody overcast or dusk, realistic contrast",
    },
    "Shelter": {
        "style": "real-life shelter-building cinematic realism, continuity of progress across scenes, safe depiction",
        "camera": "clear framing, stable shots, medium-wide for environment, 35mm look",
        "lighting": "natural daylight shifting toward dusk, consistent time progression",
    },
    "DIY": {
        "style": "real-life DIY cinematic tutorial style, clean workspace, crisp detail, coherent lighting",
        "camera": "tripod-stable, top-down + medium shots, close-ups of hands, 35mm look",
        "lighting": "soft practical lighting, clean shadows, high clarity",
    },
    "Movie (Real-life)": {
        "style": "real-life cinematic film still, high production, coherent art direction, consistent grading",
        "camera": "one clear camera move per scene, 35mm film look, shallow DOF, cinematic composition",
        "lighting": "motivated cinematic lighting, practical sources, filmic contrast",
    },
    "Animals (Real-life)": {
        "style": "wildlife documentary realism, species-accurate behavior, true-to-life color",
        "camera": "telephoto look, gentle tracking, stable handheld",
        "lighting": "natural outdoor light, true-to-life exposure",
    },
}

NEGATIVE_DEFAULT = "text, watermark, logo, low-res, blurry, deformed, extra limbs, bad anatomy"

KW = {
    "Bushcraft": ["bushcraft","campfire","forest camp","tarp","cordage","axe","knife","kindling"],
    "Survival": ["survival","wilderness","stranded","lost","storm","rescue","signal","navigation","sos"],
    "Shelter": ["shelter","lean-to","debris hut","hut","tarp shelter","windbreak","insulation"],
    "DIY": ["diy","how to","tutorial","build","make","craft","workbench","assemble","tools"],
    "Animals (Real-life)": ["wildlife","animal","animals","nature","documentary","dog","cat","lion","tiger","elephant","bird","shark","whale"],
    "Movie (Real-life)": ["cinematic","film","movie","trailer","scene","thriller","noir","neon","action"],
}


# ---------------------------
# URL clone (metadata best-effort)
# ---------------------------
def detect_platform(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if "tiktok.com" in host: return "tiktok"
    if "instagram.com" in host: return "instagram"
    if "facebook.com" in host or "fb.watch" in host: return "facebook"
    if "youtube.com" in host or "youtu.be" in host: return "youtube"
    return "web"

def safe_get(url: str, timeout: int = 12) -> str:
    r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return r.text

def extract_opengraph(url: str):
    try:
        html = safe_get(url)
    except Exception:
        return {}
    soup = BeautifulSoup(html, "html.parser")

    def og(prop: str):
        t = soup.find("meta", attrs={"property": prop})
        return t.get("content","").strip() if t and t.get("content") else ""

    def md(name: str):
        t = soup.find("meta", attrs={"name": name})
        return t.get("content","").strip() if t and t.get("content") else ""

    title = og("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    desc = og("og:description") or md("description")
    image = og("og:image")
    return {"title": title, "description": desc, "image": image}

def build_clone_brief(url: str):
    platform = detect_platform(url)
    meta = extract_opengraph(url)
    hashtags = re.findall(r"#\w+", f"{meta.get('title','')} {meta.get('description','')}")
    hashtags = hashtags[:25]
    compact = f"Source={platform}; Title={meta.get('title','')}; Desc={meta.get('description','')}; Tags={' '.join(hashtags)}"
    return {
        "platform": platform,
        "title": meta.get("title",""),
        "description": meta.get("description",""),
        "og_image": meta.get("image",""),
        "hashtags": hashtags,
        "compact": compact.strip()
    }


# ---------------------------
# Category inference + scene plan
# ---------------------------
def infer_category(text: str) -> str:
    t = (text or "").lower()
    best = "Movie (Real-life)"
    best_score = 0
    for cat, words in KW.items():
        score = sum(1 for w in words if w in t)
        if score > best_score:
            best, best_score = cat, score
    return best if best_score >= 2 else "Movie (Real-life)"

def scene_count(total_seconds: int, scene_seconds: int) -> int:
    return max(1, int(math.ceil(total_seconds / max(scene_seconds, 5))))

def beat(i: int, n: int) -> str:
    if n == 1:
        return "Single beat: establish → main moment → settle."
    if i == 1:
        return "Establish the actor/subject, setting, and goal/tension."
    if i == n:
        return "Resolve with a satisfying closing moment; hold for cut."
    mid = max(2, n // 2)
    if i == mid:
        return "Turning point: biggest change/reveal; clear cause-effect."
    return "Progress logically from previous scene; small change, same continuity."

def scene_title(category: str, i: int) -> str:
    titles = {
        "Bushcraft": ["Forest Arrival","Site Chosen","Fire Prepared","Camp Set","Quiet Night"],
        "Survival": ["Weather Turns","Decision Point","Critical Move","Narrow Escape","Safe Outcome"],
        "Shelter": ["Frame Starts","Structure Grows","Protection Added","Warmth Secured","Shelter Complete"],
        "DIY": ["Materials Laid Out","Measure & Mark","Assembly Begins","Detail Work","Final Reveal"],
        "Animals (Real-life)": ["Habitat Establishing","Behavior Shift","Close Observation","Natural Interaction","Calm Exit"],
        "Movie (Real-life)": ["Opening Shot","Rising Tension","Turning Point","Consequences","Closing Beat"],
    }
    arr = titles.get(category, titles["Movie (Real-life)"])
    return arr[min(i-1, len(arr)-1)]

def build_prompts(i: int, n: int, category: str, bible: dict, clone_compact: str, orientation: str):
    p = STYLE_PRESETS[category]

    # Make prompts DIFFERENT per scene by injecting beat + title
    vp = f"""SCENE {i}/{n} — {scene_title(category,i)} (5–8s) [{orientation}]
CONTINUITY (LOCKED):
- Actor/Subject: {bible['actor']}
- Setting/World: {bible['setting']}
- Mood arc: {bible['mood']}
- Style: {p['style']}
- Camera: {p['camera']}
- Lighting: {p['lighting']}

CLONE CUES (style/intent only):
- {clone_compact}

SCENE ROLE:
- {beat(i,n)}

TIMING:
0–2s establish → 2–6s progress → 6–8s settle/hold
""".strip()

    ip = f"""IMAGE PROMPT — Scene {i}/{n} ({category}) [{orientation}]
Photorealistic real-life still. {p['lighting']}. {p['camera']}.
Same identity/outfit/markings as Scene 1: {bible['actor']}.
Same world continuity: {bible['setting']}.
Scene beat: {beat(i,n)}.
Clone style cues: {clone_compact}.
Negative: {NEGATIVE_DEFAULT}. No text, no watermark.
""".strip()

    return vp, ip


# ---------------------------
# Scene card PNG (this is your "image")
# ---------------------------
def make_scene_card_png(scene_label: str, story: str, w=1200, h=650) -> bytes:
    img = Image.new("RGB", (w, h), (12, 18, 32))
    d = ImageDraw.Draw(img)
    try:
        ft = ImageFont.truetype("arial.ttf", 34)
        fb = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        ft = ImageFont.load_default()
        fb = ImageFont.load_default()

    d.rectangle([0, 0, w, 70], fill=(7, 11, 22))
    d.text((18, 18), scene_label, font=ft, fill=(230, 240, 255))

    d.rectangle([18, 95, w-18, 250], outline=(60, 90, 140), width=2)
    d.text((32, 110), "STORY", font=fb, fill=(140, 190, 255))
    wrapped = "\n".join(textwrap.wrap(story, width=110))
    d.text((32, 140), wrapped, font=fb, fill=(210, 225, 245))

    d.rectangle([18, 280, w-18, h-18], outline=(60, 90, 140), width=2)
    d.text((32, 295), "PROMPT", font=fb, fill=(140, 190, 255))
    d.text((32, 325), "Copy prompts from the web app blocks.", font=fb, fill=(160, 175, 200))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@dataclass
class SceneOut:
    idx: int
    title: str
    story: str
    video_prompt: str
    image_prompt: str
    card_png: bytes

def build_zip(meta: dict, scenes: List[SceneOut]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("project/meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
        for s in scenes:
            base = f"project/scenes/scene_{s.idx:02d}"
            z.writestr(f"{base}/story.txt", s.story)
            z.writestr(f"{base}/video_prompt.txt", s.video_prompt)
            z.writestr(f"{base}/image_prompt.txt", s.image_prompt)
            z.writestr(f"{base}/scene_card.png", s.card_png)
    return buf.getvalue()


# ---------------------------
# UI (cards like your screenshot)
# ---------------------------
st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] {background:#0b1220;}
[data-testid="stSidebar"] {background:#070c16;}
h1,h2,h3,h4, p, div, span, label {color:#eaf2ff;}
.scene-card {background:linear-gradient(180deg, rgba(16,24,40,1), rgba(10,16,28,1));
  border:1px solid rgba(70,110,170,0.35); border-radius:16px; padding:14px 16px; margin-bottom:16px;}
.scene-header {display:flex; justify-content:space-between; align-items:center;
  font-weight:700; font-size:18px; color:#dcecff; margin-bottom:10px;}
.pill {font-size:12px; padding:4px 10px; border-radius:999px;
  background:rgba(50,90,150,0.25); border:1px solid rgba(90,140,210,0.35);}
</style>
""",
    unsafe_allow_html=True
)

st.title("Scene Cards Prompt Generator")
st.caption("Now each scene is unique + a scene-card image is generated for export (PNG).")

with st.sidebar:
    st.subheader("Inputs")
    url = st.text_input("Paste URL (FB/TikTok/IG/YT/Web)", "")
    manual_hints = st.text_area("Manual hints (optional)", "")
    category_choice = st.selectbox("Category", CATEGORIES, 0)

    st.divider()
    total_seconds = st.number_input("Total duration (seconds)", 6, 600, 30, 1)
    scene_seconds = st.number_input("Seconds per scene (5–8 recommended)", 5, 15, 6, 1)
    orientation = st.selectbox("Orientation", ["vertical 9:16","horizontal 16:9","square 1:1"], 0)

    st.divider()
    st.subheader("Continuity Bible")
    actor = st.text_area("Actor/Subject lock", "Same main subject across scenes (identity, outfit/markings consistent).")
    setting = st.text_area("Setting/World lock", "Same world continuity; location evolves logically scene-to-scene.")
    mood = st.text_area("Mood arc", "Coherent emotional progression; no random shifts.")

    generate = st.button("Generate Scene Cards", type="primary")

clone = {"platform":"web", "compact":"No URL provided."}
suggested = "Movie (Real-life)"
if url.strip():
    clone = build_clone_brief(url.strip())
    combined = f"{clone.get('title','')} {clone.get('description','')} {' '.join(clone.get('hashtags',[]))} {manual_hints}"
    suggested = infer_category(combined)

final_category = suggested if category_choice == "Auto" else category_choice

c1, c2 = st.columns(2)
with c1:
    st.subheader("Auto analysis")
    st.write(f"**Source:** `{clone.get('platform','')}`")
    st.write(f"**Suggested:** `{suggested}`")
    st.write(f"**Final:** `{final_category}`")
with c2:
    st.subheader("Clone cues")
    st.text_area("Used (style/intent only)", clone.get("compact",""), height=120)

if generate:
    n = scene_count(int(total_seconds), int(scene_seconds))
    bible = {"actor": actor.strip(), "setting": setting.strip(), "mood": mood.strip()}

    scenes: List[SceneOut] = []
    for i in range(1, n+1):
        title = scene_title(final_category, i)
        story = f"{beat(i,n)} Actor continuity: {bible['actor']} | Setting continuity: {bible['setting']}"
        vp, ip = build_prompts(i, n, final_category, bible, clone.get("compact",""), orientation)
        card = make_scene_card_png(f"SCENE {i} — {title}", story)

        scenes.append(SceneOut(i, title, story, vp, ip, card))

    st.divider()
    st.subheader("Scenes")

    for start in range(0, len(scenes), 2):
        row = scenes[start:start+2]
        cols = st.columns(2)
        for j, s in enumerate(row):
            with cols[j]:
                st.markdown(
                    f"""<div class="scene-card">
<div class="scene-header"><div>SCENE {s.idx} — {s.title}</div><div class="pill">{final_category}</div></div>
</div>""",
                    unsafe_allow_html=True
                )
                st.image(s.card_png, use_container_width=True)
                st.markdown("**STORY**")
                st.write(s.story)
                st.markdown("**PROMPT (VIDEO)**")
                st.code(s.video_prompt, language="text")
                st.markdown("**PROMPT (IMAGE)**")
                st.code(s.image_prompt, language="text")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    meta = {
        "url": url.strip(),
        "platform": clone.get("platform",""),
        "category_suggested": suggested,
        "category_final": final_category,
        "total_seconds": int(total_seconds),
        "scene_seconds": int(scene_seconds),
        "scene_count": len(scenes),
        "orientation": orientation,
        "clone_brief": clone,
        "bible": bible,
    }

    zip_bytes = build_zip(meta, scenes)
    st.divider()
    st.subheader("Export")

    st.download_button(
        "Download ZIP (scene txt + card PNG + meta.json)",
        data=zip_bytes,
        file_name=f"scene_cards_{stamp}.zip",
        mime="application/zip"
    )
    st.download_button(
        "Download JSON (meta only)",
        data=json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8"),
        file_name=f"scene_cards_meta_{stamp}.json",
        mime="application/json"
    )

    st.success("Done. Prompts are unique per scene now, and PNG card images are included in export.")
else:
    st.info("Paste a URL, set continuity, then click **Generate Scene Cards**.")
