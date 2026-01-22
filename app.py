import streamlit as st
import google.generativeai as genai
import json
import time
import os

# --- APP CONFIGURATION ---
st.set_page_config(page_title="Google Studio: Omni", page_icon="♾️", layout="wide")

# Custom CSS for the "Pro" look
st.markdown("""
<style>
    .stApp { background-color: #0e1117; color: white; }
    .scene-card { 
        background: #1f2937; 
        padding: 20px; 
        border-radius: 12px; 
        margin-bottom: 20px; 
        border-left: 6px solid;
    }
    .model-tag {
        background: #374151;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 0.8em;
        color: #9ca3af;
    }
</style>
""", unsafe_allow_html=True)

# --- SIDEBAR: CONTROL CENTER ---
with st.sidebar:
    st.title("🎛️ Control Center")
    
    # API Key Input
    # Checks if key is in secrets (for GitHub Spaces) or asks user
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("✅ API Key loaded from Secrets")
    else:
        api_key = st.text_input("Google API Key", type="password")
        st.caption("Get it from: aistudio.google.com")
    
    st.divider()
    
    # THE NEW MODEL SELECTOR
    st.subheader("🤖 Select AI Model")
    selected_model = st.selectbox(
        "Choose your engine:",
        [
            "gemini-2.0-flash-exp",   # Newest, Best Overall
            "gemini-1.5-pro",         # Best for Storytelling/Creativity
            "gemini-1.5-flash",       # Fast & Reliable
            "gemini-1.5-flash-8b"     # Ultra Fast
        ],
        index=0 # Default to 2.0 Flash
    )
    
    # Dynamic Model Info
    if "2.0" in selected_model:
        st.info("✨ **Gemini 2.0 Flash:** The latest experimental model. Smartest and Multimodal.")
        border_color = "#3B82F6" # Blue
    elif "pro" in selected_model:
        st.info("🧠 **Gemini 1.5 Pro:** High intelligence. Best for complex scripts.")
        border_color = "#8B5CF6" # Purple
    else:
        st.info("⚡ **Gemini 1.5 Flash:** Optimized for speed and efficiency.")
        border_color = "#F59E0B" # Orange

# --- CORE AI FUNCTIONS ---
def get_gemini_response(prompt, model_name):
    if not api_key: return None
    genai.configure(api_key=api_key)
    try:
        model = genai.GenerativeModel(model_name)
        return model.generate_content(prompt)
    except Exception as e:
        st.error(f"Error with {model_name}: {e}")
        return None

def create_director_plan(topic, model_name):
    prompt = f"""
    Act as a Visionary Film Director using {model_name}.
    Create a highly detailed 3-Scene Storyboard for: '{topic}'
    
    Output STRICT JSON format (no markdown):
    {{
        "title": "Film Title",
        "genre": "Genre",
        "logline": "One sentence summary",
        "scenes": [
            {{
                "id": 1,
                "action": "Detailed action description",
                "veo_prompt": "Cinematic video, [SUBJECT], [ACTION], [LIGHTING], 4k, photorealistic, slow motion",
                "imagen_prompt": "Photorealistic close-up of [SUBJECT], [EXPRESSION], [LIGHTING], 8k, highly detailed"
            }}
        ]
    }}
    """
    response = get_gemini_response(prompt, model_name)
    if response:
        # Clean JSON string
        text = response.text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(text)
        except:
            st.error("Raw JSON Error. Try again.")
            return None
    return None

# --- MAIN UI ---
st.title("♾️ Google Studio: Omni Edition")
st.caption(f"Powered by **{selected_model}**")

col1, col2 = st.columns([3, 1])
with col1:
    topic = st.text_input("Enter your vision:", placeholder="Example: A documentary about life on Mars in the year 3000...")
with col2:
    st.write("") # Spacer
    st.write("") # Spacer
    generate = st.button("🚀 Generate Story", type="primary")

if generate and api_key:
    start_time = time.time()
    
    with st.spinner(f"🧠 {selected_model} is directing..."):
        data = create_director_plan(topic, selected_model)
    
    elapsed = round(time.time() - start_time, 2)
    
    if data:
        st.divider()
        st.success(f"Generated in {elapsed}s using {selected_model}")
        
        # Header
        st.header(data['title'])
        st.markdown(f"**Genre:** {data['genre']} | *{data['logline']}*")
        
        # Scenes Loop
        for scene in data['scenes']:
            with st.container():
                # Dynamic Border Color based on model
                st.markdown(
                    f"<div class='scene-card' style='border-left-color: {border_color};'>"
                    f"<h3>🎬 Scene {scene['id']}</h3>"
                    f"<p>{scene['action']}</p>"
                    f"</div>", 
                    unsafe_allow_html=True
                )
                
                c1, c2 = st.columns(2)
                with c1:
                    st.caption("📹 **Veo / VideoFX Prompt** (Copy for Google VideoFX)")
                    st.code(scene['veo_prompt'], language="text")
                with c2:
                    st.caption("📸 **Imagen / ImageFX Prompt** (Copy for Google ImageFX)")
                    st.code(scene['imagen_prompt'], language="text")

elif generate and not api_key:
    st.warning("⚠️ Please enter your Google API Key in the sidebar.")
