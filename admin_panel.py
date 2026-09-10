# =========================================================
# KAIRA - PANEL ADMINISTRATIVO
# =========================================================

import os
from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, render_template_string, session

import membresias
import json
from pathlib import Path

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCESO_FILE = os.path.join(BASE_DIR, "telegram_acceso.json")
BLOQUEADOS_FILE = os.path.join(BASE_DIR, "telegram_bloqueados.json")


def _json_cargar(ruta, default):
    try:
        if not os.path.exists(ruta):
            return default
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as error:
        print("⚠️ Error leyendo", ruta, ":", error)
        return default


def _json_guardar(ruta, datos):
    tmp = ruta + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=4)
        os.replace(tmp, ruta)
        return True
    except Exception as error:
        print("⚠️ Error guardando", ruta, ":", error)
        return False


def cargar_autorizados_telegram():
    datos = _json_cargar(ACCESO_FILE, [])
    usuarios = {}

    if isinstance(datos, dict):
        datos = list(datos.values())

    if not isinstance(datos, list):
        datos = []

    for item in datos:
        if isinstance(item, (int, str)) and str(item).isdigit():
            uid = int(item)
            usuarios[uid] = {
                "telegram_user_id": uid,
                "first_name": "",
                "last_name": "",
                "username": "",
            }
            continue

        if isinstance(item, dict):
            try:
                uid = int(item.get("telegram_user_id"))
            except Exception:
                continue

            usuarios[uid] = {
                "telegram_user_id": uid,
                "first_name": str(item.get("first_name", "") or ""),
                "last_name": str(item.get("last_name", "") or ""),
                "username": str(item.get("username", "") or ""),
            }

    admin_id = os.getenv("KAIRA_ADMIN_TELEGRAM_ID")
    try:
        if admin_id:
            aid = int(admin_id)
            usuarios.setdefault(aid, {
                "telegram_user_id": aid,
                "first_name": "Administrador",
                "last_name": "",
                "username": "",
            })
    except Exception:
        pass

    return usuarios


def guardar_autorizados_telegram(usuarios):
    datos = [usuarios[k] for k in sorted(usuarios)]
    return _json_guardar(ACCESO_FILE, datos)


def cargar_bloqueados_telegram():
    datos = _json_cargar(BLOQUEADOS_FILE, [])
    ids = set()
    if isinstance(datos, list):
        for x in datos:
            try:
                ids.add(int(x))
            except Exception:
                pass
    return ids


def guardar_bloqueados_telegram(ids):
    return _json_guardar(
        BLOQUEADOS_FILE,
        sorted(int(x) for x in ids)
    )


def sincronizar_usuarios_con_membresias():
    """
    Crea en la base de membresías a los usuarios que ya están
    autorizados en Telegram, sin modificar sus fechas si ya existen.
    """
    autorizados = cargar_autorizados_telegram()

    for uid, data in autorizados.items():
        try:
            actual = membresias.obtener_usuario(uid)

            if actual is None:
                membresias.crear_o_actualizar_usuario(
                    uid,
                    data.get("first_name", ""),
                    data.get("last_name", ""),
                    data.get("username", ""),
                )
        except Exception as error:
            print(
                "⚠️ No pude sincronizar usuario",
                uid,
                "con membresías:",
                error,
            )


def estado_acceso(uid):
    bloqueados = cargar_bloqueados_telegram()
    autorizados = cargar_autorizados_telegram()

    if int(uid) in bloqueados:
        return "revocado"

    if int(uid) in autorizados:
        return "autorizado"

    return "no_autorizado"


def obtener_nombre_registro(uid, registro):
    nombre = (
        registro.get("nombre")
        or registro.get("first_name")
        or ""
    ).strip()

    apellido = (
        registro.get("apellido")
        or registro.get("last_name")
        or ""
    ).strip()

    if nombre or apellido:
        return (nombre + " " + apellido).strip()

    return "Usuario " + str(uid)


ADMIN_PANEL_KEY = os.getenv(
    "KAIRA_ADMIN_PANEL_KEY",
    "cambia-esta-clave"
)

# La propia clave del panel se utiliza como secreto de sesión.
# No hace falta guardarla en cookies ni mostrarla en la URL.
app.secret_key = os.getenv(
    "KAIRA_ADMIN_SESSION_SECRET",
    ADMIN_PANEL_KEY
)


def autorizado():
    # Sesión normal del panel.
    if session.get("kaira_admin"):
        return True

    # Compatibilidad temporal con la URL antigua.
    key = (
        request.args.get("key")
        or request.form.get("key")
        or ""
    )

    if key and key == ADMIN_PANEL_KEY:
        session["kaira_admin"] = True
        return True

    return False


def cerrar_sesion():
    session.pop(
        "kaira_admin",
        None
    )


CSS = """
:root{
  --bg:#090b10;--panel:#12151c;--panel2:#191e28;--text:#f6f7fb;
  --muted:#9299a8;--line:#2c3341;--accent:#d64ec1;--purple:#8b5cf6;
  --green:#24c477;--yellow:#eab74f;--red:#ef6262;--blue:#4b9cff;
}
*{box-sizing:border-box}
body{
  margin:0;color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif;
  background:radial-gradient(circle at 10% 0%,rgba(214,78,193,.14),transparent 28%),
             radial-gradient(circle at 90% 10%,rgba(139,92,246,.12),transparent 25%),
             var(--bg);
}
.wrap{max-width:1350px;margin:auto;padding:28px}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:24px}
.brandRow{display:flex;align-items:center;gap:13px}
.logo{
  width:50px;height:50px;border-radius:16px;display:grid;place-items:center;
  background:linear-gradient(135deg,var(--accent),var(--purple));
  font-size:25px;box-shadow:0 12px 32px rgba(214,78,193,.25)
}
.brand{font-size:27px;font-weight:800}.subtitle{color:var(--muted);font-size:13px;margin-top:3px}
.actions{display:flex;gap:9px;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:15px}
.stat{
  border:1px solid var(--line);border-radius:18px;padding:18px;background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.012));
  box-shadow:0 15px 45px rgba(0,0,0,.28)
}
.icon{font-size:22px}.label{font-size:12px;color:var(--muted);margin-top:8px;letter-spacing:.06em}
.metric{font-size:31px;font-weight:850;margin-top:2px}.hint{font-size:12px;color:var(--muted);margin-top:4px}
.section{
  margin-top:18px;border:1px solid var(--line);border-radius:18px;background:rgba(18,21,28,.92);
  box-shadow:0 15px 45px rgba(0,0,0,.24);padding:18px
}
.sectionTitle{font-size:16px;font-weight:800;margin-bottom:12px}
.alerts{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.alert{border:1px solid var(--line);border-radius:14px;padding:13px;background:var(--panel2)}
.alert b{display:block;margin-bottom:4px}.muted{color:var(--muted);font-size:12px}
.toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin:22px 0 13px}
.btn,button{
  border:1px solid var(--line);border-radius:11px;padding:10px 13px;
  background:var(--accent);color:white;text-decoration:none;cursor:pointer;font-weight:700
}
.btn.secondary{background:#202631}.btn.green{background:var(--green);border-color:transparent}
.btn.red{background:var(--red);border-color:transparent}.btn.blue{background:var(--blue);border-color:transparent}
.search{width:min(420px,100%);padding:12px 13px;border-radius:11px;border:1px solid var(--line);background:#0e1117;color:var(--text);outline:none}
.tableWrap{border:1px solid var(--line);border-radius:18px;overflow:auto;background:rgba(18,21,28,.96)}
table{width:100%;border-collapse:collapse;min-width:1100px}
th,td{padding:14px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
th{background:#11141a;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
tr:hover td{background:rgba(255,255,255,.02)}
.person{display:flex;align-items:center;gap:10px}.avatar{
 width:38px;height:38px;border-radius:12px;display:grid;place-items:center;
 background:#242a36;border:1px solid var(--line);font-weight:800
}
.name{font-weight:750}.tiny{font-size:11px;color:var(--muted);margin-top:2px}
.badge{display:inline-flex;align-items:center;gap:5px;padding:6px 9px;border-radius:999px;font-size:11px;font-weight:750}
.green{color:#8cf1bb;background:rgba(36,196,119,.12);border:1px solid rgba(36,196,119,.18)}
.yellow{color:#f4d681;background:rgba(234,183,79,.12);border:1px solid rgba(234,183,79,.18)}
.red{color:#ff9c9c;background:rgba(239,98,98,.12);border:1px solid rgba(239,98,98,.18)}
.gray{color:#c8ced9;background:rgba(255,255,255,.05);border:1px solid var(--line)}
.rowActions{display:flex;gap:6px;flex-wrap:wrap}.rowActions .btn,.rowActions button{padding:7px 9px;font-size:11px}
.empty{text-align:center;padding:45px;color:var(--muted)}
.formWrap{max-width:780px;margin:auto}.formCard{border:1px solid var(--line);border-radius:20px;background:rgba(18,21,28,.96);padding:24px;box-shadow:0 15px 45px rgba(0,0,0,.25)}
.formgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.field label{display:block;font-size:12px;color:var(--muted);margin-bottom:7px}
.field input,.field select,.field textarea{width:100%;padding:12px;border-radius:11px;border:1px solid var(--line);background:#0e1117;color:var(--text)}
.full{grid-column:1/-1}.formActions{display:flex;gap:9px;flex-wrap:wrap;margin-top:18px}

.paymentCard{
  margin-top:16px;border:1px solid var(--line);border-radius:18px;
  background:rgba(18,21,28,.96);padding:20px
}
.paymentHeader{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.paymentTotal{font-size:30px;font-weight:850}
.paymentTable{width:100%;border-collapse:collapse;margin-top:14px}
.paymentTable th,.paymentTable td{padding:11px;border-bottom:1px solid var(--line);text-align:left}
.paymentTable th{color:var(--muted);font-size:11px;text-transform:uppercase}
.backLine{margin-top:14px}

.footer{text-align:center;color:var(--muted);font-size:11px;margin-top:18px}
@media(max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}.alerts{grid-template-columns:1fr}}
@media(max-width:640px){.wrap{padding:14px}.topbar{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr}.formgrid{grid-template-columns:1fr}.full{grid-column:auto}}
"""
LOGIN_HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KAIRA ADMIN - Acceso</title>
<style>
:root{
  --bg:#080a0f;
  --panel:#12151d;
  --text:#f7f8fb;
  --muted:#9aa2b1;
  --line:#293140;
  --accent:#d64ec1;
  --purple:#8b5cf6;
}
*{box-sizing:border-box}
body{
  margin:0;min-height:100vh;display:grid;place-items:center;
  background:
    radial-gradient(circle at 12% 8%,rgba(214,78,193,.20),transparent 29%),
    radial-gradient(circle at 88% 8%,rgba(139,92,246,.17),transparent 26%),
    var(--bg);
  color:var(--text);font-family:Segoe UI,Arial,sans-serif
}
.login{
  width:min(440px,92vw);padding:30px;border:1px solid var(--line);
  border-radius:24px;background:rgba(18,21,29,.97);
  box-shadow:0 30px 90px rgba(0,0,0,.45)
}
.logo{
  width:70px;height:70px;border-radius:20px;display:grid;place-items:center;
  background:linear-gradient(135deg,var(--accent),var(--purple));
  font-weight:900;font-size:25px;margin-bottom:20px
}
h1{margin:0 0 6px;font-size:30px}
.sub{margin:0 0 24px;color:var(--muted);line-height:1.5}
label{display:block;color:var(--muted);font-size:12px;margin-bottom:8px}
input{
  width:100%;padding:13px 14px;border-radius:12px;border:1px solid var(--line);
  background:#0e1117;color:var(--text);outline:none
}
input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(214,78,193,.10)}
button{
  width:100%;margin-top:14px;padding:13px;border:0;border-radius:12px;
  background:linear-gradient(135deg,var(--accent),var(--purple));
  color:#fff;font-weight:800;cursor:pointer
}
.error{margin-top:14px;padding:10px 12px;border-radius:10px;background:#431b22;color:#ffadb4;font-size:13px}
</style>
</head>
<body>
  <div class="login">
    <div class="logo">KA</div>
    <h1>KAIRA ADMIN</h1>
    <p class="sub">Panel privado para administrar usuarios, membresías, pagos y acceso.</p>
    <form method="post" action="{{ url_for('login') }}">
      <label for="key">Contraseña del administrador</label>
      <input id="key" name="key" type="password" autocomplete="current-password" required>
      <button type="submit">ENTRAR AL PANEL</button>
    </form>
    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}
  </div>
</body>
</html>
"""

HTML = """
<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KAIRA ADMIN</title><style>{{ css }}</style></head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brandRow">
      <div class="logo">KA</div>
      <div><div class="brand">KAIRA ADMIN</div><div class="subtitle">Usuarios · Membresías · Pagos · Acceso</div></div>
    </div>
    <div class="actions">
      <span class="badge gray">🔐 Panel privado</span>
      <a class="btn secondary" href="{{ url_for('logout') }}">Cerrar sesión</a>
    </div>
  </div>

  <div class="section" style="margin-top:0;margin-bottom:18px">
    <div class="sectionTitle">💰 Precios globales</div>
    <div class="muted" style="margin-bottom:12px">
      Estos precios se aplican automáticamente a nuevas membresías y renovaciones.
      También puedes modificarlos desde Telegram con <b>/precio PLAN PRECIO</b>.
    </div>
    <form method="post" action="{{ url_for('guardar_precios') }}">
      <div class="formgrid">
        <div class="field">
          <label>Semanal</label>
          <input name="Semanal" type="number" step="0.01" min="0"
                 value="{{ '%.2f'|format(precios_planes.get('Semanal',0)) }}">
        </div>
        <div class="field">
          <label>Mensual</label>
          <input name="Mensual" type="number" step="0.01" min="0"
                 value="{{ '%.2f'|format(precios_planes.get('Mensual',0)) }}">
        </div>
        <div class="field">
          <label>Trimestral</label>
          <input name="Trimestral" type="number" step="0.01" min="0"
                 value="{{ '%.2f'|format(precios_planes.get('Trimestral',0)) }}">
        </div>
        <div class="field">
          <label>Semestral</label>
          <input name="Semestral" type="number" step="0.01" min="0"
                 value="{{ '%.2f'|format(precios_planes.get('Semestral',0)) }}">
        </div>
        <div class="field">
          <label>Anual</label>
          <input name="Anual" type="number" step="0.01" min="0"
                 value="{{ '%.2f'|format(precios_planes.get('Anual',0)) }}">
        </div>
      </div>
      <div class="formActions">
        <button class="btn green" type="submit">✓ Guardar precios globales</button>
      </div>
    </form>
  </div>

  <div class="grid">
    <div class="stat"><div class="icon">👥</div><div class="label">USUARIOS</div><div class="metric">{{ resumen.usuarios_autorizados }}</div><div class="hint">Autorizados en Telegram</div></div>
    <div class="stat"><div class="icon">🟢</div><div class="label">ACTIVOS</div><div class="metric">{{ resumen.activos }}</div><div class="hint">Membresías vigentes</div></div>
    <div class="stat"><div class="icon">🟡</div><div class="label">POR VENCER</div><div class="metric">{{ resumen.por_vencer_7_dias }}</div><div class="hint">Próximos 7 días</div></div>
    <div class="stat"><div class="icon">🔴</div><div class="label">VENCIDOS / REVOCADOS</div><div class="metric">{{ resumen.vencidos + resumen.revocados }}</div><div class="hint">Requieren atención</div></div>
  </div>

  <div class="section">
    <div class="sectionTitle">🔔 Alertas</div><div class="muted" style="margin:-4px 0 12px">El administrador principal no se incluye en pagos, vencimientos ni métricas de membresías.</div>
    <div class="alerts">
      <div class="alert"><b>🟡 Vencimientos próximos</b><span class="muted">{{ resumen.por_vencer_7_dias }} usuario(s) vencen en 7 días o menos.</span></div>
      <div class="alert"><b>🔴 Membresías vencidas</b><span class="muted">{{ resumen.vencidos }} usuario(s) están vencidos.</span></div>
      <div class="alert"><b>💳 Pagos pendientes</b><span class="muted">{{ resumen.pendientes }} usuario(s) no están marcados como pagados.</span></div>
    </div>
  </div>

  <div class="toolbar">
    <div class="actions">
      <a class="btn" href="{{ url_for('nuevo') }}">＋ Registrar usuario</a>
      <a class="btn secondary" href="{{ url_for('index') }}">↻ Actualizar</a>
    </div>
    <input id="search" class="search" type="search" placeholder="🔎 Buscar por nombre, apellido o ID..." oninput="filtrar()">
  </div>

  <div class="tableWrap">
    <table id="usuarios">
      <thead><tr>
        <th>Usuario</th><th>Telegram ID</th><th>Tipo</th><th>Inicio</th><th>Vencimiento</th>
        <th>Precio</th><th>Pago</th><th>Estado</th><th>Acciones</th>
      </tr></thead>
      <tbody>
      {% for u in usuarios %}
        <tr>
          <td><div class="person"><div class="avatar">{{ (u.nombre[:1] if u.nombre else '?')|upper }}</div>
            <div><div class="name">{{ u.nombre }} {{ u.apellido }}</div><div class="tiny">{% if u.username %}@{{ u.username }}{% else %}Sin username{% endif %}</div></div>
          </div></td>
          <td><b>{{ u.telegram_user_id }}</b></td>
          <td>{{ u.tipo_membresia or '-' }}</td>
          <td>{{ u.fecha_inicio or '-' }}</td>
          <td>
            {% if u.fecha_vencimiento %}
              <span class="badge {% if u.estado == 'vencida' %}red{% elif u.estado == 'pendiente' %}yellow{% else %}green{% endif %}">
                {{ u.fecha_vencimiento }}
              </span>
            {% else %}-{% endif %}
          </td>
          <td><b>${{ '%.2f'|format(u.precio or 0) }}</b></td>
          <td>
            {% if u.telegram_user_id == resumen.admin_id %}
              <span class="badge gray">— No aplica</span>
            {% elif u.pagado %}
              <span class="badge green">✓ Pagado</span>
            {% else %}
              <span class="badge yellow">! No pagado</span>
            {% endif %}
          </td>
          <td>
            {% if u.acceso == 'revocado' %}
              <span class="badge red">● Revocado</span>
            {% elif u.estado == 'vencida' %}
              <span class="badge red">● Vencido</span>
            {% elif u.estado == 'pendiente' %}
              <span class="badge yellow">● Pendiente</span>
            {% else %}
              <span class="badge green">● Activo</span>
            {% endif %}
          </td>
          <td><div class="rowActions">
            {% if u.telegram_user_id == resumen.admin_id %}
              <span class="badge gray">👑 Administrador</span>
            {% else %}
            <a class="btn secondary" href="{{ url_for('editar', user_id=u.telegram_user_id) }}">Editar</a>
            <a class="btn secondary" href="{{ url_for('historial', user_id=u.telegram_user_id) }}">📋 Historial</a>
            <form method="post" action="{{ url_for('pago', user_id=u.telegram_user_id) }}">
              <button class="btn {% if u.pagado %}secondary{% else %}green{% endif %}" type="submit">
                {{ 'No pagado' if u.pagado else 'Marcar pagado' }}
              </button>
            </form>
            {% if u.acceso == 'revocado' %}
            <form method="post" action="{{ url_for('autorizar', user_id=u.telegram_user_id) }}">
              <button class="btn green" type="submit">Autorizar</button>
            </form>
            {% else %}
            <form method="post" action="{{ url_for('revocar', user_id=u.telegram_user_id) }}"
                  onsubmit="return confirm('¿Revocar el acceso de este usuario?');">
              <button class="btn red" type="submit">Revocar</button>
            </form>
            {% endif %}
            {% if u.telegram_user_id != resumen.admin_id %}
            <form method="post" action="{{ url_for('eliminar', user_id=u.telegram_user_id) }}"
                  onsubmit="return confirm('Esta acción ELIMINARÁ definitivamente el registro, quitará su autorización, bloqueará su ID y desvinculará Moodle. ¿Continuar?');">
              <button class="btn red" type="submit">Eliminar</button>
            </form>
            {% endif %}
            {% endif %}
          </div></td>
        </tr>
      {% else %}
        <tr><td colspan="9" class="empty">No hay usuarios registrados todavía.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="footer">
    KAIRA ADMIN · Panel privado ·
    <span style="color:#ef6262">Eliminar es una acción permanente.</span>
  </div>
</div>
<script>
function filtrar(){
  const q=(document.getElementById('search').value||'').toLowerCase().trim();
  document.querySelectorAll('#usuarios tbody tr').forEach(r=>{
    r.style.display=!q||r.innerText.toLowerCase().includes(q)?'':'none';
  });
}
</script>
</body></html>
"""
HISTORIAL_HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KAIRA ADMIN - Historial de pagos</title>
<style>{{ css }}</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brandRow">
      <div class="logo">KA</div>
      <div>
        <div class="brand">Historial de pagos</div>
        <div class="subtitle">Registro completo de movimientos del usuario</div>
      </div>
    </div>
    <a class="btn secondary" href="{{ url_for('index') }}">← Volver al panel</a>
  </div>

  <div class="paymentCard">
    <div class="paymentHeader">
      <div>
        <div class="sectionTitle">👤 {{ usuario.nombre }} {{ usuario.apellido }}</div>
        <div class="muted">Telegram ID: {{ usuario.telegram_user_id }}</div>
        <div class="muted">Plan actual: {{ usuario.tipo_membresia or 'Sin plan' }}</div>
      </div>
      <div>
        <div class="muted">TOTAL PAGADO</div>
        <div class="paymentTotal">${{ '%.2f'|format(resumen_pagos.total or 0) }}</div>
        <div class="muted">{{ resumen_pagos.cantidad }} movimiento(s)</div>
      </div>
    </div>

    {% if pagos %}
      <table class="paymentTable">
        <thead>
          <tr>
            <th>Fecha</th>
            <th>Plan</th>
            <th>Precio</th>
            <th>Método</th>
            <th>Observaciones</th>
          </tr>
        </thead>
        <tbody>
        {% for p in pagos %}
          <tr>
            <td>{{ p.fecha_pago }}</td>
            <td><b>{{ p.plan or '-' }}</b></td>
            <td><b>${{ '%.2f'|format(p.precio or 0) }}</b></td>
            <td>{{ p.metodo_pago or '-' }}</td>
            <td>{{ p.observaciones or '-' }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    {% else %}
      <div class="empty">No hay pagos registrados todavía.</div>
    {% endif %}

    <div class="backLine">
      <a class="btn secondary" href="{{ url_for('editar', user_id=usuario.telegram_user_id) }}">✏️ Editar usuario</a>
      <a class="btn secondary" href="{{ url_for('index') }}">← Volver</a>
    </div>
  </div>
</div>
</body>
</html>
"""

FORM = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KAIRA ADMIN - Membresía</title>
<style>{{ css }}</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brandRow">
      <div class="logo">KA</div>
      <div>
        <div class="brand">{{ 'Editar usuario' if usuario else 'Registrar usuario' }}</div>
        <div class="subtitle">Perfil y membresía</div>
      </div>
    </div>
    <a class="btn secondary" href="{{ url_for('index') }}">← Volver</a>
  </div>

  <div class="formWrap">
    <div class="formCard">
      <form method="post" id="membershipForm">
        <div class="formgrid">

          <div class="field">
            <label>Telegram ID *</label>
            <input name="telegram_user_id"
                   value="{{ usuario.telegram_user_id if usuario else '' }}"
                   {{ 'readonly' if usuario else '' }} required>
          </div>

          <div class="field">
            <label>Usuario de Telegram</label>
            <input name="username" value="{{ usuario.username if usuario else '' }}"
                   placeholder="@usuario">
          </div>

          <div class="field">
            <label>Nombre *</label>
            <input name="nombre" value="{{ usuario.nombre if usuario else '' }}" required>
          </div>

          <div class="field">
            <label>Apellido</label>
            <input name="apellido" value="{{ usuario.apellido if usuario else '' }}">
          </div>

          <div class="field">
            <label>Fecha de creación del perfil</label>
            <input value="{{ usuario.fecha_registro if usuario and usuario.fecha_registro else 'Se genera automáticamente' }}"
                   readonly>
          </div>

          <div class="field">
            <label>Plan de membresía</label>
            <select name="tipo_membresia" id="plan">
              <option value="">Seleccionar...</option>
              <option value="Semanal" {{ 'selected' if usuario and usuario.tipo_membresia == 'Semanal' else '' }}>Semanal · 7 días</option>
              <option value="Mensual" {{ 'selected' if usuario and usuario.tipo_membresia == 'Mensual' else '' }}>Mensual · 1 mes</option>
              <option value="Trimestral" {{ 'selected' if usuario and usuario.tipo_membresia == 'Trimestral' else '' }}>Trimestral · 3 meses</option>
              <option value="Semestral" {{ 'selected' if usuario and usuario.tipo_membresia == 'Semestral' else '' }}>Semestral · 6 meses</option>
              <option value="Anual" {{ 'selected' if usuario and usuario.tipo_membresia == 'Anual' else '' }}>Anual · 12 meses</option>
              <option value="Personalizada" {{ 'selected' if usuario and usuario.tipo_membresia == 'Personalizada' else '' }}>Personalizada</option>
            </select>
          </div>

          <div class="field">
            <label>Precio <span class="tiny">El precio global del plan se aplica automáticamente.</span></label>
            <input name="precio" id="precio" type="number" step="0.01" min="0"
                   value="{{ usuario.precio if usuario else 0 }}">
          </div>

          <div class="field">
            <label>Fecha de inicio</label>
            <input name="fecha_inicio" id="fecha_inicio" type="datetime-local"
                   value="{{ usuario.fecha_inicio[:16] if usuario and usuario.fecha_inicio else '' }}">
          </div>

          <div class="field">
            <label>Fecha y hora de corte / revocación</label>
            <input name="fecha_vencimiento" id="fecha_vencimiento" type="datetime-local"
                   value="{{ usuario.fecha_vencimiento[:16] if usuario and usuario.fecha_vencimiento else '' }}">
            <div class="tiny" id="corte_info">Se calculará automáticamente cuando marques el pago.</div>
          </div>

          <div class="field">
            <label>Pago</label>
            <select name="pagado" id="pagado">
              <option value="0" {{ 'selected' if usuario and not usuario.pagado else '' }}>❌ No pagado</option>
              <option value="1" {{ 'selected' if usuario and usuario.pagado else '' }}>✅ Pagado</option>
            </select>
          </div>

          <div class="field">
            <label>Método de pago</label>
            <select name="metodo_pago">
              <option value="">Seleccionar...</option>
              <option value="Transferencia" {{ 'selected' if usuario and usuario.metodo_pago == 'Transferencia' else '' }}>Transferencia</option>
              <option value="Efectivo" {{ 'selected' if usuario and usuario.metodo_pago == 'Efectivo' else '' }}>Efectivo</option>
              <option value="Otro" {{ 'selected' if usuario and usuario.metodo_pago == 'Otro' else '' }}>Otro</option>
            </select>
          </div>

          <div class="field full">
            <label>Notas</label>
            <textarea name="notas" rows="4" placeholder="Notas administrativas...">{{ usuario.notas if usuario else '' }}</textarea>
          </div>
        </div>

        <div class="formActions">
          <button class="btn green" type="submit">✓ Guardar membresía</button>
          <a class="btn secondary" href="{{ url_for('index') }}">Cancelar</a>
        </div>
      </form>
    </div>

    {% if usuario %}
    <div class="section" style="margin-top:14px">
      <div class="sectionTitle">🔄 Renovación rápida</div>
      <div class="muted" style="margin-bottom:12px">
        Elige un plan y la fecha de corte se recalculará automáticamente desde el momento de renovación.
      </div>
      <form method="post" action="{{ url_for('renovar_rapido', user_id=usuario.telegram_user_id) }}" id="renewForm">
        <div class="formgrid">
          <div class="field">
            <label>Plan</label>
            <select name="tipo_membresia" required>
              <option value="Semanal">Semanal · 7 días</option>
              <option value="Mensual" selected>Mensual · 1 mes</option>
              <option value="Trimestral">Trimestral · 3 meses</option>
              <option value="Semestral">Semestral · 6 meses</option>
              <option value="Anual">Anual · 12 meses</option>
            </select>
          </div>
          <div class="field">
            <label>Precio</label>
            <input name="precio" type="number" step="0.01" min="0" value="{{ usuario.precio if usuario else 0 }}">
          </div>
          <div class="field">
            <label>Método de pago</label>
            <select name="metodo_pago">
              <option value="Transferencia">Transferencia</option>
              <option value="Efectivo">Efectivo</option>
              <option value="Otro">Otro</option>
            </select>
          </div>
        </div>
        <div class="formActions">
          <button class="btn blue" type="submit">🔄 Renovar + marcar pagado</button>
        </div>
      </form>
    </div>
    {% endif %}
  </div>
</div>

<script>
(function(){
  const plan=document.getElementById('plan');
  const pago=document.getElementById('pagado');
  const inicio=document.getElementById('fecha_inicio');
  const venc=document.getElementById('fecha_vencimiento');
  const info=document.getElementById('corte_info');

  function addMonths(date, months){
    const d=new Date(date.getTime());
    const day=d.getDate();
    d.setMonth(d.getMonth()+months,1);
    const last=new Date(d.getFullYear(),d.getMonth()+1,0).getDate();
    d.setDate(Math.min(day,last));
    return d;
  }

  function calc(){
    if(!plan || !pago || !inicio || !venc) return;
    if(pago.value!=='1' || plan.value==='Personalizada' || !plan.value) return;

    let baseDate = inicio.value ? new Date(inicio.value) : new Date();
    let end = new Date(baseDate.getTime());

    if(plan.value==='Semanal') end.setDate(end.getDate()+7);
    if(plan.value==='Mensual') end=addMonths(baseDate,1);
    if(plan.value==='Trimestral') end=addMonths(baseDate,3);
    if(plan.value==='Semestral') end=addMonths(baseDate,6);
    if(plan.value==='Anual') end=addMonths(baseDate,12);

    const pad=n=>String(n).padStart(2,'0');
    venc.value = `${end.getFullYear()}-${pad(end.getMonth()+1)}-${pad(end.getDate())}T${pad(end.getHours())}:${pad(end.getMinutes())}`;
    info.textContent='Fecha de corte calculada automáticamente según el plan.';
  }

  if(plan) plan.addEventListener('change',calc);
  if(pago) pago.addEventListener('change',calc);
  if(inicio) inicio.addEventListener('change',calc);
  calc();
})();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    if request.args.get("key") == ADMIN_PANEL_KEY:
        session["kaira_admin"] = True
        return redirect(url_for("index"))

    if not autorizado():
        return redirect(url_for("login"))

    sincronizar_usuarios_con_membresias()
    membresias.actualizar_estados()

    autorizados = cargar_autorizados_telegram()
    bloqueados = cargar_bloqueados_telegram()
    usuarios = membresias.listar_usuarios()

    existentes = {
        int(u["telegram_user_id"])
        for u in usuarios
        if u.get("telegram_user_id") is not None
    }

    for uid, data in autorizados.items():
        if uid not in existentes:
            usuarios.append({
                "telegram_user_id": uid,
                "nombre": data.get("first_name", ""),
                "apellido": data.get("last_name", ""),
                "username": data.get("username", ""),
                "fecha_registro": "",
                "fecha_inicio": "",
                "fecha_vencimiento": "",
                "precio": 0,
                "pagado": 0,
                "estado": "pendiente",
                "metodo_pago": "",
                "notas": "",
                "tipo_membresia": "",
            })

    usuarios.sort(
        key=lambda u: (
            1 if not u.get("fecha_vencimiento") else 0,
            u.get("fecha_vencimiento") or "",
            (u.get("nombre") or "").lower(),
        )
    )

    for u in usuarios:
        uid = int(u["telegram_user_id"])
        u["acceso"] = estado_acceso(uid)

        if uid in autorizados:
            datos_tg = autorizados[uid]
            if not u.get("nombre"):
                u["nombre"] = datos_tg.get("first_name", "")
            if not u.get("apellido"):
                u["apellido"] = datos_tg.get("last_name", "")
            if not u.get("username"):
                u["username"] = datos_tg.get("username", "")

    resumen = membresias.resumen()
    clientes = [
        u for u in usuarios
        if int(u.get("telegram_user_id", 0))
        != int(
            os.getenv(
                "KAIRA_ADMIN_TELEGRAM_ID",
                "0"
            )
        )
    ]

    resumen["total"] = len(clientes)
    resumen["usuarios_autorizados"] = sum(
        1 for u in clientes
        if u.get("acceso") == "autorizado"
    )
    resumen["activos"] = sum(
        1 for u in clientes
        if u.get("estado") == "activa"
    )
    resumen["vencidos"] = sum(
        1 for u in clientes
        if u.get("estado") == "vencida"
    )
    resumen["pendientes"] = sum(
        1 for u in clientes
        if not int(u.get("pagado", 0) or 0)
    )

    resumen["por_vencer_7_dias"] = sum(
        1 for u in clientes
        if u.get("fecha_vencimiento")
        and membresias.dias_para_vencer(
            u.get("fecha_vencimiento")
        ) is not None
        and 0 <= membresias.dias_para_vencer(
            u.get("fecha_vencimiento")
        ) <= 7
    )

    resumen["revocados"] = sum(
        1 for u in clientes
        if u.get("acceso") == "revocado"
    )

    try:
        resumen["admin_id"] = int(
            os.getenv(
                "KAIRA_ADMIN_TELEGRAM_ID",
                "0"
            )
        )
    except Exception:
        resumen["admin_id"] = 0

    try:
        precios_planes = membresias.listar_precios_planes()
    except Exception:
        precios_planes = {}

    return render_template_string(
        HTML,
        css=CSS,
        key="",
        resumen=resumen,
        usuarios=usuarios,
        precios_planes=precios_planes,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        key = request.form.get("key", "")

        if key == ADMIN_PANEL_KEY:
            session["kaira_admin"] = True
            return redirect(
                url_for("index")
            )

        return render_template_string(
            LOGIN_HTML,
            error="Contraseña incorrecta."
        )

    if autorizado():
        return redirect(
            url_for("index")
        )

    return render_template_string(
        LOGIN_HTML,
        error=""
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(
        url_for("login")
    )


@app.route("/precios", methods=["POST"])
def guardar_precios():
    if not autorizado():
        return redirect(url_for("login"))

    try:
        for plan in (
            "Semanal",
            "Mensual",
            "Trimestral",
            "Semestral",
            "Anual",
        ):
            valor = float(
                request.form.get(
                    plan,
                    0
                ).replace(",", ".")
            )

            if valor < 0:
                raise ValueError

            membresias.establecer_precio_plan(
                plan,
                valor
            )

    except Exception as error:
        print(
            "⚠️ Error guardando precios del panel:",
            error
        )

    return redirect(
        url_for("index")
    )



@app.route("/historial/<int:user_id>")
def historial(user_id):
    if not autorizado():
        return redirect(url_for("login"))

    usuario=membresias.obtener_usuario(user_id)

    if usuario is None:
        return "Usuario no encontrado", 404

    pagos=membresias.historial_pagos(
        user_id,
        100
    )
    resumen_pagos=membresias.resumen_pagos(
        user_id
    )

    return render_template_string(
        HISTORIAL_HTML,
        css=CSS,
        usuario=usuario,
        pagos=pagos,
        resumen_pagos=resumen_pagos,
    )


@app.route("/nuevo", methods=["GET", "POST"])
def nuevo():
    if not autorizado():
        return redirect(url_for("login"))

    if request.method == "POST":

        uid = int(
            request.form["telegram_user_id"]
        )

        nombre = request.form["nombre"]
        apellido = request.form.get(
            "apellido",
            ""
        )
        username = request.form.get(
            "username",
            ""
        ).strip()

        tipo = request.form.get(
            "tipo_membresia",
            ""
        ).strip()

        pagado = int(
            request.form.get(
                "pagado",
                0
            )
        )

        fecha_inicio = request.form.get(
            "fecha_inicio",
            ""
        ).strip()

        fecha_vencimiento = request.form.get(
            "fecha_vencimiento",
            ""
        ).strip()

        precio = membresias.aplicar_precio_plan(
            tipo,
            request.form.get("precio", 0),
        )

        # Fecha de creación: siempre se genera en membresias.py
        # y se conserva en ediciones posteriores.
        membresias.registrar_membresia(
            uid,
            nombre,
            apellido,
            fecha_inicio,
            fecha_vencimiento,
            precio,
            pagado,
            request.form.get("metodo_pago", ""),
            request.form.get("notas", ""),
            tipo_membresia=tipo,
        )

        # Guardar/actualizar nombre de Telegram en acceso si ya existe.
        usuarios = cargar_autorizados_telegram()
        if uid in usuarios:
            usuarios[uid]["first_name"] = nombre
            usuarios[uid]["last_name"] = apellido
            usuarios[uid]["username"] = username
            guardar_autorizados_telegram(
                usuarios
            )

        return redirect(
            url_for("index")
        )

    return render_template_string(
        FORM,
        css=CSS,
        key="",
        usuario=None,
    )


@app.route("/editar/<int:user_id>", methods=["GET", "POST"])
def editar(user_id):
    if not autorizado():
        return redirect(url_for("login"))

    usuario = membresias.obtener_usuario(
        user_id
    )

    if usuario is None:
        return "Usuario no encontrado", 404

    if request.method == "POST":

        pagado = int(
            request.form.get(
                "pagado",
                0
            )
        )

        tipo = request.form.get(
            "tipo_membresia",
            ""
        ).strip()

        precio = membresias.aplicar_precio_plan(
            tipo,
            request.form.get("precio", 0),
        )

        fecha_inicio = request.form.get(
            "fecha_inicio",
            ""
        ).strip()

        fecha_vencimiento = request.form.get(
            "fecha_vencimiento",
            ""
        ).strip()

        # Si el usuario acaba de marcar "Pagado", el plan define
        # la nueva fecha de corte automáticamente.
        if (
            pagado == 1
            and tipo
            and tipo != "Personalizada"
        ):
            fecha_vencimiento = (
                membresias.calcular_fecha_vencimiento(
                    fecha_inicio,
                    tipo,
                )
            )

        membresias.registrar_membresia(
            user_id,
            request.form["nombre"],
            request.form.get(
                "apellido",
                ""
            ),
            fecha_inicio,
            fecha_vencimiento,
            precio,
            pagado,
            request.form.get(
                "metodo_pago",
                ""
            ),
            request.form.get(
                "notas",
                ""
            ),
            tipo_membresia=tipo,
        )

        usuarios = cargar_autorizados_telegram()
        if user_id in usuarios:
            usuarios[user_id]["first_name"] = (
                request.form.get("nombre", "")
            )
            usuarios[user_id]["last_name"] = (
                request.form.get("apellido", "")
            )
            usuarios[user_id]["username"] = (
                request.form.get("username", "")
            ).strip()
            guardar_autorizados_telegram(
                usuarios
            )

        return redirect(
            url_for("index")
        )

    return render_template_string(
        FORM,
        css=CSS,
        key="",
        usuario=usuario,
    )


@app.route("/renovar/<int:user_id>", methods=["POST"])
def renovar_rapido(user_id):
    if not autorizado():
        return redirect(url_for("login"))

    usuario = membresias.obtener_usuario(
        user_id
    )

    if usuario is None:
        return "Usuario no encontrado", 404

    tipo = request.form.get(
        "tipo_membresia",
        "Mensual"
    )

    precio = request.form.get(
        "precio",
        usuario.get("precio", 0)
    )

    metodo_pago = request.form.get(
        "metodo_pago",
        ""
    )

    membresias.renovar(
        user_id,
        tipo_membresia=tipo,
        precio=precio,
        metodo_pago=metodo_pago,
    )

    return redirect(
        url_for("index")
    )


@app.route("/pago/<int:user_id>", methods=["POST"])
def pago(user_id):
    if not autorizado():
        return redirect(url_for("login"))

    pagado = int(
        request.form.get(
            "pagado",
            0
        )
    ) == 1

    if pagado:
        usuario = membresias.obtener_usuario(
            user_id
        ) or {}

        tipo = request.form.get(
            "tipo_membresia"
        ) or usuario.get(
            "tipo_membresia"
        ) or "Mensual"

        membresias.renovar(
            user_id,
            tipo_membresia=tipo,
            precio=request.form.get(
                "precio",
                usuario.get("precio", 0)
            ),
            metodo_pago=request.form.get(
                "metodo_pago",
                usuario.get("metodo_pago", "")
            ),
        )
    else:
        membresias.marcar_pago(
            user_id,
            False
        )

    return redirect(
        url_for("index")
    )


@app.route("/autorizar/<int:user_id>", methods=["POST"])
def autorizar(user_id):
    if not autorizado():
        return redirect(url_for("login"))

    try:
        admin_id = int(
            os.getenv(
                "KAIRA_ADMIN_TELEGRAM_ID",
                "0"
            )
        )
    except Exception:
        admin_id = 0

    if user_id == admin_id:
        return redirect(url_for("index"))

    usuarios = cargar_autorizados_telegram()
    bloqueados = cargar_bloqueados_telegram()

    bloqueados.discard(
        user_id
    )

    if user_id not in usuarios:
        usuarios[user_id] = {
            "telegram_user_id": user_id,
            "first_name": "",
            "last_name": "",
            "username": "",
        }

    guardar_bloqueados_telegram(
        bloqueados
    )
    guardar_autorizados_telegram(
        usuarios
    )

    membresias.crear_o_actualizar_usuario(
        user_id
    )

    return redirect(
        url_for("index")
    )


@app.route("/revocar/<int:user_id>", methods=["POST"])
def revocar(user_id):
    if not autorizado():
        return redirect(url_for("login"))

    try:
        admin_id = int(
            os.getenv(
                "KAIRA_ADMIN_TELEGRAM_ID",
                "0"
            )
        )
    except Exception:
        admin_id = 0

    if user_id == admin_id:
        return redirect(url_for("index"))

    usuarios = cargar_autorizados_telegram()
    usuarios.pop(
        user_id,
        None
    )

    bloqueados = cargar_bloqueados_telegram()
    bloqueados.add(
        user_id
    )

    guardar_autorizados_telegram(
        usuarios
    )
    guardar_bloqueados_telegram(
        bloqueados
    )

    try:
        from moodle_kaira import eliminar_cuenta_moodle_usuario
        eliminar_cuenta_moodle_usuario(
            user_id
        )
    except Exception as error:
        print(
            "⚠️ No pude desvincular Moodle al revocar:",
            error
        )

    return redirect(
        url_for("index")
    )


@app.route("/eliminar/<int:user_id>", methods=["POST"])
def eliminar(user_id):
    if not autorizado():
        return redirect(url_for("login"))

    try:
        admin_id = int(
            os.getenv(
                "KAIRA_ADMIN_TELEGRAM_ID",
                "0"
            )
        )
    except Exception:
        admin_id = 0

    if user_id == admin_id:
        return redirect(
            url_for("index")
        )

    usuarios = cargar_autorizados_telegram()
    usuarios.pop(
        user_id,
        None
    )
    guardar_autorizados_telegram(
        usuarios
    )

    bloqueados = cargar_bloqueados_telegram()
    bloqueados.add(
        user_id
    )
    guardar_bloqueados_telegram(
        bloqueados
    )

    try:
        from moodle_kaira import eliminar_cuenta_moodle_usuario
        eliminar_cuenta_moodle_usuario(
            user_id
        )
    except Exception as error:
        print(
            "⚠️ No pude desvincular Moodle al eliminar:",
            error
        )

    try:
        membresias.eliminar_usuario(
            user_id
        )
    except Exception as error:
        print(
            "⚠️ No pude eliminar la membresía:",
            error
        )

    return redirect(
        url_for("index")
    )

if __name__ == "__main__":
    port = int(os.getenv("ADMIN_PANEL_PORT", "30244"))
    host = os.getenv("ADMIN_PANEL_HOST", "0.0.0.0")
    print(f"KAIRA ADMIN iniciando en {host}:{port}")
    app.run(host=host, port=port, debug=False)
