import os
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

# ============================================================
# KAIRA CLOUD - ENTRADA PRINCIPAL
# ============================================================

ENV_PATH = "/home/container/.env"

def cargar_env(path=ENV_PATH):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave, valor = linea.split("=", 1)
                os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))
    except Exception as e:
        print("⚠️ No pude leer .env:", e)


cargar_env()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

print("========================================")
print("          KAIRA CLOUD ESCOLAR")
print("========================================")

if not TELEGRAM_BOT_TOKEN:
    print("❌ Falta TELEGRAM_BOT_TOKEN")
    sys.exit(1)

if not GEMINI_API_KEY:
    print("❌ Falta GEMINI_API_KEY")
    sys.exit(1)

# ============================================================
# BASES DE DATOS / MOODLE
# ============================================================

try:
    import membresias
    print("✓ Membresías cargadas")
except Exception as e:
    print("❌ Error cargando membresías:", e)
    sys.exit(1)

try:
    from moodle_kaira import procesar_moodle, establecer_usuario_telegram
    print("✓ Moodle multiusuario cargado")
except Exception as e:
    print("❌ Error cargando Moodle:", e)
    sys.exit(1)

try:
    import telegram_kaira
    print("✓ Telegram cargado")
except Exception as e:
    print("❌ Error cargando Telegram:", e)
    sys.exit(1)

# ============================================================
# PANEL ADMINISTRATIVO
# ============================================================

def iniciar_panel():
    try:
        import admin_panel
        host = os.getenv("ADMIN_PANEL_HOST", "0.0.0.0")
        port = int(os.getenv("ADMIN_PANEL_PORT", os.getenv("PORT", "30244")))
        print(f"🌐 Panel KAIRA: http://{host}:{port}")
        admin_panel.app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        print("❌ Error iniciando panel:", e)

threading.Thread(target=iniciar_panel, name="KairaAdminPanel", daemon=True).start()

# ============================================================
# CEREBRO
# ============================================================

def procesar_mensaje(mensaje, hablar_por_voz=False, telegram_user_id=None):
    mensaje = str(mensaje or "").strip()
    if not mensaje:
        return "No recibí ningún mensaje."

    print(f"📩 Telegram [{telegram_user_id}]: {mensaje}")

    if telegram_user_id is not None:
        try:
            establecer_usuario_telegram(telegram_user_id)
        except Exception as e:
            print("⚠️ No pude establecer usuario Moodle:", e)

    # Hora local de México
    ml = mensaje.lower()
    if ml in ("hora", "qué hora es", "que hora es") or "dime la hora" in ml:
        return "Son las " + datetime.now(ZoneInfo("America/Mexico_City")).strftime("%I:%M %p") + "."

    # Moodle tiene prioridad para consultas académicas.
    try:
        respuesta = procesar_moodle(mensaje)
        if respuesta:
            return respuesta
    except Exception as e:
        print("⚠️ Error Moodle:", e)

    # Gemini
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""Eres KAIRA, una asistente personal de inteligencia artificial.
Responde en español, de forma natural, clara, útil y breve cuando la pregunta sea sencilla.
No inventes datos académicos. Si la pregunta requiere datos de Moodle, usa solamente la información que KAIRA haya podido obtener de Moodle.

Mensaje del usuario:
{mensaje}
"""
        respuesta = client.models.generate_content(model=MODEL, contents=prompt)
        texto = getattr(respuesta, "text", None)
        return texto.strip() if texto else "No pude generar una respuesta."
    except Exception as e:
        print("❌ Error Gemini:", e)
        return "Tuve un problema al procesar tu mensaje. Inténtalo nuevamente."

# ============================================================
# CONECTAR CEREBRO Y TELEGRAM
# ============================================================

telegram_kaira.registrar_cerebro(procesar_mensaje)
print("✓ Cerebro de KAIRA conectado con Telegram")
print("🚀 Telegram se ejecutará en el hilo principal")

if __name__ == "__main__":
    telegram_kaira.ejecutar_telegram()
