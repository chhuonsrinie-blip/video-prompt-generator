import io
import json
import math
import os
import re
import time
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
import streamlit as st
from google import genai


# ============================================================
# CONFIG
# ============================================================
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
    If not set -> allow access (so you can disable password easily).
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
        st.error("❌ Gemini API key not found. Set GEMINI_API_KEY in Streamlit Secrets.")
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
    Offline storyboard (no Gemini). Always works.
    """
    n = scene_count(total_seconds, seconds_per_scene)

    category_final = category_choice if category_choice != "Auto" else "Movie (Real-life)"

    # Auto continuity
    def cont(cat: str) -> dict:
        if cat == "Animals (Real-life)":
            return {
                "actor_lock": "Same animal subject across all scenes (consistent markings/features; natural behavior).",
                "setting_lock": "Same habitat; time-of-day evolves logically; respectful distance.",
                "mood_arc": "Observational calm → behavior highlight → calm exit.",
                "rules": "No text/logos. Wildlife realism. No human interference."
            }
        if cat == "DIY":
            return {
                "actor_lock": "Same craftsperson across scenes (consistent hands/outfit; same tools).",
                "setting_lock": "Same clean workshop/workbench; coherent lighting.",
                "mood_arc": "Clear progress beats → satisfying reveal.",
                "rules": "No text/logos. Realistic physics. No dangerous instructions."
            }
        if cat in ["Bushcraft", "Survival", "Shelter"]:
            return {
                "actor_lock": "Same outdoors person across scenes (consistent outfit/gear; same identity).",
                "setting_lock": "Same outdoor location continuity; weather/time shifts gradually.",
                "mood_arc": "Rising tension → action → relief." if cat == "Survival" else "Calm focus → steady progress → satisfying completion.",
                "rules": "No text/logos. Depict safely. No step-by-step dangerous instructions."
            }
        return {
            "actor_lock": "Same main character across scenes (consistent identity/outfit/props).",
            "setting_lock": "Same world continuity; location evolves logically scene-to-scene.",
            "mood_arc": "Cinematic build-up → turning point → resolution.",
            "rules": "No text/logos. Realistic physics. Continuity enforced."
        }

    continuity = cont(category_final)

    beats = [
        ("Opening Shot", "Establish subject, setting, and goal/tension."),
        ("Rising Tension", "Introduce a constraint; maintain continuity."),
        ("First Action", "Show meaningful progress with detail inserts."),
        ("Progress Check", "Show measurable progress; small improvement."),
        ("Turning Point", "Key milestone achieved; emotional beat increases."),
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
        story = f"{beat} Continuity: {continuity['actor_lock']} | {continuity['setting_lock']}"

        video_prompt = (
            f"SCENE {i}/{n} ({orientation})\n"
            f"Category: {category_final}\n"
            f"Title: {title}\n"
            f"Beat: {beat}\n"
            f"Continuity: {continuity['actor_lock']} ; {continuity['setting_lock']}\n"
            f"Mood arc: {continuity['mood_arc']}\n"
            f"Rules: {continuity['rules']}\n"
            f"Source: {meta.source} | {meta.title}\n"
        ).strip()

        image_prompt = (
            f"IMAGE PROMPT — Scene {i}/{n} — {title} ({category_final}) [{orientation}]\n"
            f"{detail_phrase(detail_level)}. Real-life cinematic look.\n"
            f"Subject lock: {continuity['actor_lock']}\n"
            f"Setting lock: {continuity['setting_lock']}\n"
            f"Beat: {beat}\n"
            f"No text, no watermark.\n"
        ).strip()

        scenes.append({
            "idx": i,
            "title": title,
            "story": story,
            "video_prompt": video_prompt,
            "image_prompt": image_prompt
        })

    return {
        "category_final": category_final,
        "continuity": continuity,
        "scenes": scenes
    }


# ============================================================
# GEMINI STORYBOARD (JSON)
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
      "video_prompt": "detailed 5–8s video prompt",
      "image_prompt": "detailed image prompt"
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

    # normalize scene count and indices
    scenes = data.get("scenes", [])[:n]
    for i, s in enumerate(scenes, start=1):
        s["idx"] = i
    data["scenes"] = scenes

    if not data.get("category_final"):
        data["category_final"] = category_choice if category_choice != "Auto" else "Movie (Real-life)"

    if "continuity" not in data:
        data["continuity"] = {}

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
            z.writestr(f"{base}/video_prompt.txt", s["video_prompt"])
            z.writestr(f"{base}/image_prompt.txt", s["image_prompt"])
    return mem.getvalue()


# ============================================================
# UI
# ============================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title("🎬 Storyboard Studio (Gemini Only)")
st.caption("Gemini generates all scene-by-scene prompts. No ImageFX/Whisk required.")

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

col1, col2 = st.columns(2)
with col1:
    st.subheader("Auto analysis")
    st.write(f"**Source:** `{meta.source}`")
    st.write(f"**Title:** {meta.title}")
with col2:
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
                # quota fallback
                if ("RESOURCE_EXHAUSTED" in msg) or ("429" in msg) or ("quota" in msg.lower()):
                    st.warning("Gemini quota exceeded. Using offline fallback storyboard (still good quality).")
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
    st.subheader("Scene Cards")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_bytes = build_zip(project)

    cA, cB = st.columns(2)
    with cA:
        st.download_button(
            "Download ZIP (scene txt + project.json)",
            data=zip_bytes,
            file_name=f"storyboard_{stamp}.zip",
            mime="application/zip",
        )
    with cB:
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
                st.markdown("**VIDEO PROMPT**")
                st.code(s["video_prompt"], language="text")
                st.markdown("**IMAGE PROMPT**")
                st.code(s["image_prompt"], language="text")
