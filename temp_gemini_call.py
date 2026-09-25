import os
import traceback
from dotenv import load_dotenv
load_dotenv()
# Also try loading a project-level .env if present
project_env = os.path.join(os.path.dirname(__file__), "AngazaCare", ".env")
if os.path.exists(project_env):
    load_dotenv(project_env)
import google.generativeai as genai

print('API KEY LOADED', bool(os.getenv('GEMINI_API_KEY')))
api_key = os.getenv('GEMINI_API_KEY')
print('API KEY VALUE', api_key[:10] + '...' if api_key else None)
genai.configure(api_key=api_key)

system_prompt = (
    "You are AngazaCare AI, a compassionate mental health companion. Respond with empathy and care. "
    "Never diagnose. Always encourage professional help for serious concerns. Keep responses under 100 words."
)

messages = [
    {"role": "system", "parts": [system_prompt]},
    {"role": "user", "parts": ["hello"]},
]

try:
    print('LISTING MODELS...')
    models = genai.list_models()
    for m in models:
        print('MODEL', getattr(m, 'name', None), 'SUPPORT', getattr(m, 'supported_generation_methods', None))
except Exception as e:
    print('LIST MODELS ERROR', type(e).__name__, e)
    traceback.print_exc()

try:
    model = genai.GenerativeModel(model_name="gemini-2.0-flash")
    response = model.generate_content(messages)
    print('RESPONSE OBJ', type(response))
    try:
        print('RESPONSE TEXT', getattr(response, 'text', None))
    except Exception as e:
        print('TEXT ACCESS ERROR', e)
    print('RESPONSE ATTRS', [a for a in dir(response) if not a.startswith('_')][:50])
except Exception as e:
    print('ERROR', type(e).__name__, e)
    traceback.print_exc()
