import streamlit as st
import google.generativeai as genai
import json

# --- APP SETUP ---
st.set_page_config(page_title="Google Studio: Omni", page_icon="♾️", layout="wide")

# Check system health
try:
    from importlib.metadata import version
    ver = version("google-generativeai")
    st.success(f"✅ System Healthy: Library Version {ver}")
except:
    st.warning("⚠️ Could not verify library version.")

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
    
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("Key loaded from Secrets")
    else:
        api_key = st.text_input("Google API Key", type="password")

    st.divider()

    # MODEL SELECTOR (With Exact Names)
    # We added "-001" to the names, which fixes the 404 error often
    selected_model_name = st.selectbox(
        "Choose your engine:",
        [
            "gemini-1.5-flash-001",       # SAFER NAME
            "gemini-1.5-pro-001",         # SAFER NAME
            "gemini-pro",                 # OLD RELIABLE (Backup)
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

    # DEBUGGER BUTTON
    st.divider()
    if st.button("❓ Test Connection"):
        if not api_key:
            st.error("Enter Key first!")
        else:
            try:
                genai.configure(api_key=api_key)
                st.write("Available Models:")
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
                "
