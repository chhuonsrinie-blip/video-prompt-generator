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
    
    # Health Check
    try:
        from importlib.metadata import version
        ver = version("google-generativeai")
        st.caption(f"✅ System v{ver}")
    except:
        st.caption("⚠️ System Version Unknown")

    # API Key Handling
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("Key loaded from Secrets")
    else:
        api_key = st.text_input("Google API Key", type="password")

    st.divider()

    # Model Selector
    user_model_choice = st.selectbox(
        "Preferred Engine:",
        ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"],
        index=0
    )
    
    if "flash" in user_model_choice:
        border_color = "#F59E0B"
        st.info("⚡ **Flash:** Optimized for Speed")
    elif "1.5" in user_model_choice:
        border_color = "#8B5CF6"
        st.info("🧠 **Pro:** Optimized for Quality")
    else:
        border_color = "#3B82F6"
        st.info("🛡️ **Standard:** The Classic Model")

    # DEBUG BUTTON
    if st.button("❓ Debug: Check Access"):
        if not api_key:
            st.error("Enter Key first!")
        else:
            try:
                genai.configure(api_key=api_key)
                st.write("✅ **Your Key has access to:**")
                models = genai.list_models()
                found_any = False
                for m in models:
                    if 'generateContent' in m.supported_generation_methods:
                        st.code(m.name)
                        found_any = True
                if not found_any:
                    st.error("❌ Your Key has NO access to any text models. Create a NEW Key in a NEW Project.")
            except Exception as e:
                st.error(f"Connection Failed: {e}")

# --- AI FUNCTIONS ---
def get_gemini_response(prompt, preferred_model):
    if not api_key: return "NO_KEY"
    genai.configure(api_key=api_key)
    
    # FAIL-SAFE SYSTEM: We try the preferred model, then backups
    backup_models = ["gemini-1.5-flash", "gemini-1.5-flash-001", "gemini-1.5-pro", "gemini-pro"]
    
    # Put preferred model first
    if preferred_model in backup_models:
        backup_models.remove(preferred_model)
    model_list = [preferred_model] + backup_models

    for model_name in model_list:
        try:
            model = genai.GenerativeModel(model_name)
            return model.generate_content(prompt)
        except Exception:
            continue # Try next model
            
    return "ERROR: All models failed. Please check your API Key."

def create_director_plan(topic, model_name):
    # Prompt Template
    prompt = f"""
    Act as a Visionary Film Director.
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
    
    if response == "NO_KEY":
        st.error("Please enter your API Key.")
        return None
    if isinstance(response, str) and "ERROR" in response:
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

if generate:
    with st.spinner(f"🧠 writing script..."):
        data = create_director_plan(topic, user_model_choice)
    
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
