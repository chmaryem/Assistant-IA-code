import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

# Configurer la clé API
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

print("📋 Modèles Gemini disponibles :\n")

# Lister TOUS les modèles
for model in genai.list_models():
    if 'generateContent' in model.supported_generation_methods:
        print(f"✅ {model.name}")
        print(f"   Display: {model.display_name}")
        print(f"   Description: {model.description[:100]}...")
        print()