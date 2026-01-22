import os
import math
import streamlit as st
import google.generativeai as genai

# ---------------------------
# CONFIG
# ---------------------------

st.set_page_config(
    page_title="Storyboard Studio (Gemini)",
    page_icon="🎬",
    layout="wide"
)

def get_secret(name, default=""):
    try:
        return st.secrets[name]
    except:
        return os.getenv(name, default)

GEMINI_API_KEY = get_secret("AIzaSyBoIntVWG8SqmyccvcwtMKP20HTGNi_lcM")

if not GEMINI_API_KEY:
    st.error("❌ Gemini API key not found. Set GEMINI_API_KEY in Streamlit Secrets.")
    st.stop()

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-pro")

# ---------------------------
# UI
# ---------------------------

st.title("🎬 Storyboard Studio")
st.caption("Gemini generates scene-by-scene prompts. Images are generated manually in ImageFX / Whisk.")

with st.sidebar:
    idea = st.text_area("Idea (optional)", height=120)
    category = st.selectbox(
        "Category",
        ["Auto", "DIY", "Bushcraft", "Survival", "Shelter", "Movie (Real-life)", "Animals (Real-life)"]
    )
    orientation = st.selectbox("Resolution", ["vertical 9:16", "horizontal 16:9"])
    total_duration = st.number_input("Total duration (seconds)", 10, 600, 60)
    seconds_per_scene = st.number_input("Seconds per scene", 2, 30, 6)
    detail_level = st.selectbox("Detail level", ["Medium", "High", "Max"])

generate = st.button("🚀 Generate Storyboard")

# ---------------------------
# LOGIC
# ---------------------------

def generate_storyboard():
    scene_count = max(1, math.ceil(total_duration / seconds_per_scene))

    system_prompt = f"""
You are a professional film director and cinematographer.

Create a storyboard with {scene_count} scenes.
Category: {category}
Aspect ratio: {orientation}
Detail level: {detail_level}

Rules:
- Same main subject across all scenes
- Same world continuity
- Logical progression scene to scene
- Ultra-realistic cinematic style
- No text, no watermark
- Natural lighting, real physics
- Each scene must be unique

For each scene return:
1. Scene title
2. Beat (what happens)
3. Image prompt (for ImageFX / Whisk)
"""

    if idea:
        system_prompt += f"\nIdea:\n{idea}"

    response = model.generate_content(system_prompt)
    return response.text

# ---------------------------
# OUTPUT
# ---------------------------

if generate:
    with st.spinner("Generating storyboard with Gemini…"):
        try:
            result = generate_storyboard()
            st.success("Storyboard generated!")

            scenes = result.split("\n\n")

            for i, scene in enumerate(scenes, start=1):
                if len(scene.strip()) < 20:
                    continue

                with st.expander(f"🎞 Scene {i}", expanded=True):
                    st.code(scene, language="markdown")
                    st.caption("⬆ Copy this prompt into Google ImageFX / Whisk")

        except Exception as e:
            st.error(f"Gemini error: {e}")

# ---------------------------
# FOOTER
# ---------------------------

st.markdown("---")
st.caption(
    "⚠ ImageFX / Whisk has no public API. "
    "This tool generates best-possible prompts for manual image generation."
)
