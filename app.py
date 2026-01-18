import streamlit as st
import math
import requests
from bs4 import BeautifulSoup

# -----------------------------
# PAGE CONFIG
# -----------------------------
st.set_page_config(
    page_title="Storyboard Studio – Scene Prompt Generator",
    layout="wide",
)

st.title("🎬 Storyboard Studio")
st.caption("URL + Idea → Deep cinematic scene-by-scene prompts (ImageFX / Whisk ready)")

# -----------------------------
# HELPERS
# -----------------------------
def extract_text_from_url(url: str) -> str:
    try:
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        text = " ".join(p.get_text() for p in soup.find_all("p"))
        return text[:3000]
    except:
        return ""

def build_scene_prompt(scene_idx, total_scenes, beat, category, orientation, continuity):
    return f"""
Ultra-realistic cinematic image.

Scene {scene_idx}/{total_scenes}: {beat}

Category: {category}
Orientation: {orientation}

Continuity rules:
- Same main subject across all scenes
- Same world and logical progression
- Coherent emotional arc

Visual style:
- Natural lighting
- Real physics
- Authentic materials
- Cinematic composition
- Shallow depth of field
- Film still, high dynamic range

Restrictions:
- No text
- No watermark
- No logo
""".strip()

# -----------------------------
# INPUTS
# -----------------------------
with st.sidebar:
    st.header("Input")

    source_url = st.text_input("Source URL (optional)")
    idea = st.text_area("Idea / Concept (optional)", height=120)

    category = st.selectbox(
        "Category",
        ["Auto", "Movie (Real-life)", "Bushcraft", "Survival", "Shelter", "DIY", "Animals (Real-life)"],
    )

    orientation = st.selectbox(
        "Orientation",
        ["horizontal 16:9", "vertical 9:16", "square 1:1"],
    )

    total_duration = st.slider("Total duration (seconds)", 30, 300, 120)
    seconds_per_scene = st.slider("Seconds per scene", 3, 20, 6)

    generate = st.button("🚀 Generate Storyboard")

# -----------------------------
# GENERATION
# -----------------------------
if generate:
    base_text = ""

    if source_url:
        base_text += extract_text_from_url(source_url)

    if idea:
        base_text += "\n" + idea

    if not base_text.strip():
        st.error("Please provide a URL or an idea.")
        st.stop()

    total_scenes = max(1, math.ceil(total_duration / seconds_per_scene))

    st.subheader("📖 Storyboard Scenes")

    continuity = {
        "actor": "Same main subject across scenes",
        "world": "Same world, evolving logically",
        "mood": "Coherent emotional progression",
    }

    beats = [
        "Establish environment and subject",
        "Introduce goal or tension",
        "Rising action",
        "Midpoint development",
        "Escalation",
        "Complication",
        "Climax preparation",
        "Climax",
        "Resolution",
        "Aftermath"
    ]

    for i in range(total_scenes):
        beat = beats[i] if i < len(beats) else "Story progression"

        with st.expander(f"Scene {i+1}: {beat}", expanded=(i == 0)):
            prompt = build_scene_prompt(
                i + 1,
                total_scenes,
                beat,
                category if category != "Auto" else "Cinematic Real-life",
                orientation,
                continuity,
            )

            st.text_area(
                "Image Prompt (copy into Google ImageFX / Whisk)",
                prompt,
                height=220,
            )

            st.caption(
                f"Scene {i+1}/{total_scenes} • {seconds_per_scene}s • {orientation}"
            )

# -----------------------------
# FOOTER
# -----------------------------
st.divider()
st.caption(
    "⚠️ Image generation is NOT run inside this app. "
    "Copy prompts into Google ImageFX / Whisk / Midjourney manually."
)
