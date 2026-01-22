import streamlit as st
import google.generativeai as genai
import json

# --- APP SETUP ---
st.set_page_config(page_title="Google Studio: Omni", page_icon="♾️", layout="wide")

# Custom CSS
st.markdown("""
<style>
    .stApp { background-color: #0e1117; color: white; }
    .scene-card { background: #1f2937; padding: 20px; border-radius: 12px; margin-bottom: 20px; border-left: 6px solid; }
</style>
""", unsafe_allow_html=True)

# --- SIDEBAR ---
with st.sidebar:
    st.title("🎛️ Control Center")
    
    # Check system health
    try:
        from importlib.metadata import version
        ver = version("google-generativeai")
        st.success(f"✅ System Healthy: v{ver}")
    except:
        st.warning("⚠️ Could not verify library version.")

    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("Key loaded from Secrets")
    else:
        api_key = st.text_input("Google API Key", type="password")

    st.divider()

    # MODEL SELECTOR (Fixed Names)
    # We added "-001" which fixes the 404 "Not Found" error
    selected_model_name = st.selectbox(
        "Choose your engine:",
        [
            "gemini-1.5-flash-001",       # SAFER NAME
            "gemini-1.5-pro-001",         # SAFER NAME
            "gemini-pro",                 # BACKUP
        ],
        index=0
    )
    
    if "flash" in selected_model_name:
        border_color = "#F59E0B"
        st.info("⚡ **Flash:** Speed & Efficiency")
    elif "1.5-pro" in selected_model_name:
        border_color = "#8B5CF6"
        st.info("🧠 **Pro 1.5:** High Intelligence")
    else:
        border_color = "#3B82F6"
        st.info("🛡️ **Gemini Pro:** The classic backup")

    # DEBUGGER BUTTON (Finds exactly what models you have access to)
    st.divider()
    if st.button("❓ Debug: List My Models"):
        if not api_key:
            st.error("Enter Key first!")
        else:
            try:
                genai.configure(api_key=api_key)
                st.write("✅ Your API Key can see these models:")
                for m in genai.list_models():
                    if 'generateContent' in m.supported_generation_methods:
                        st.code(m.name)
            except Exception as e:
                st.error(f"Connection Failed: {e}")

# --- AI FUNCTIONS ---
def get_gemini_response(prompt, model_name):
    if not api_key: return None
    genai.configure(api_key=api_key)
    try:
        # We try to use the model
        model = genai.GenerativeModel(model_name)
        return model.generate_content(prompt)
    except Exception as e:
        return f"ERROR: {str(e)}"

def create_director_plan(topic, model_name):
    # FIXED: The triple quotes below are now safe
    prompt = f"""
    Act as a Visionary Film Director using {model_name}.
    Create a 3-Scene Storyboard for: '{topic}'
    
    Output STRICT JSON format (no markdown):
    {{
        "title": "Film Title",
        "genre": "Genre",
        "scenes": [
            {{
                "id": 1,
                "action": "Action description",
                "veo_prompt": "Cinematic video, [SUBJECT], [ACTION], 4k",
                "imagen_prompt": "Photorealistic photo of [SUBJECT], 8k"
            }}
        ]
    }}
    """
    
    response = get_gemini_response(prompt, model_name)
    
    # If response is a string, it's an error message
    if isinstance(response, str):
        st.error(response)
        return None
        
    if response:
        try:
            text = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except:
            st.error("AI Output Error. Try again.")
            return None
    return None

# --- MAIN UI ---
st.title("♾️ Google Studio: Omni Edition")

topic = st.text_input("Enter your vision:", placeholder="E.g., A cyberpunk detective story...")
generate = st.button("🚀 Generate Story", type="primary")

if generate and api_key:
    with st.spinner(f"🧠 {selected_model_name} is working..."):
        data = create_director_plan(topic, selected_model_name)
    
    if data:
        st.divider()
        st.header(f"{data['title']} ({data['genre']})")
        
        for scene in data['scenes']:
            st.markdown(
                f"<div class='scene-card' style='border-left-color: {border_color};'>"
                f"<h3>🎬 Scene {scene['id']}</h3>"
                f"<p>{scene['action']}</p>"
                f"</div>", 
                unsafe_allow_html=True
            )
            c1, c2 = st.columns(2)
            with c1:
                st.code(scene['veo_prompt'], language="text")
                st.caption("Video Prompt")
            with c2:
                st.code(scene['imagen_prompt'], language="text")
                st.caption("Image Prompt")
