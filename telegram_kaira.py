import time
import asyncio
import threading
import re
import json
import calendar
import os
import tempfile
import requests
import uuid
import html

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from generador_archivos import procesar_peticion_generacion

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)


# =========================================================
# 🧩 CALLBACKS DE ACCESO (DEFINIDOS TEMPRANO)
# =========================================================
# Estas funciones se definen antes que cualquier posible inicio de
# Telegram para evitar errores de inicialización/carga parcial.

async def solicitud_acceso_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()

    user = query.from_user
    user_id = int(user.id)

    if usuario_admin(user_id):
        await query.edit_message_text(
            "👑 Ya tienes acceso de administrador."
        )
        return

    if usuario_bloqueado(user_id):
        await query.edit_message_text(
            "🔒 Tu acceso está bloqueado.\n\n"
            "Contacta al administrador para revisar tu acceso."
        )
        return

    if usuario_autorizado(user_id):
        await query.edit_message_text(
            "✅ Ya tienes acceso a KAIRA.\n\n"
            "Escribe /start para comenzar."
        )
        return

    if solicitud_acceso_pendiente(user_id):
        await query.edit_message_text(
            "⏳ Tu solicitud ya fue enviada.\n\n"
            "Espera a que el administrador la revise."
        )
        return

    registrar_solicitud_acceso(user)

    nombre = " ".join(
        x for x in (
            user.first_name or "",
            user.last_name or "",
        ) if x
    ).strip() or (
        "@"
        + (
            user.username or "sin_username"
        )
    )

    admin_id = _id_admin()

    if not admin_id:
        quitar_solicitud_acceso(user_id)
        await query.edit_message_text(
            "⚠️ No se encontró el administrador de KAIRA."
        )
        return

    username = (
        "@"
        + user.username
    ) if user.username else "sin username"

    teclado = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✅ AUTORIZAR",
                callback_data=(
                    f"solicitud_autorizar:{user_id}"
                ),
            ),
            InlineKeyboardButton(
                "❌ RECHAZAR",
                callback_data=(
                    f"solicitud_rechazar:{user_id}"
                ),
            ),
        ]]
    )

    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                "🔔 <b>NUEVA SOLICITUD DE ACCESO</b>\n\n"
                f"👤 <b>{nombre}</b>\n"
                f"🆔 Telegram ID: <code>{user_id}</code>\n"
                f"👤 Username: <code>{username}</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado,
        )

        await query.edit_message_text(
            "✅ <b>Solicitud enviada.</b>\n\n"
            "El administrador recibió tu solicitud.\n"
            "Espera a que autorice tu acceso.",
            parse_mode="HTML",
        )

    except Exception as error:
        print(
            "⚠️ Error enviando solicitud de acceso:",
            error
        )
        quitar_solicitud_acceso(user_id)

        try:
            await query.edit_message_text(
                "❌ No pude enviar tu solicitud. Inténtalo nuevamente."
            )
        except Exception:
            pass


async def admin_decision_acceso_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Atiende AUTORIZAR/DENEGAR. AUTORIZAR solo deja pendiente el plan."""
    query = update.callback_query
    if not query or not query.from_user:
        return
    if not usuario_admin(query.from_user.id):
        await query.answer("No tienes permiso.", show_alert=True)
        return
    await query.answer()
    data = query.data or ""
    try:
        accion, user_id_text = data.split(":", 1)
        user_id = int(user_id_text)
    except Exception:
        await query.edit_message_text("❌ Solicitud inválida.")
        return
    solicitud = cargar_solicitudes_acceso().get(str(user_id))
    if not solicitud:
        await query.edit_message_text("ℹ️ Esta solicitud ya fue atendida.")
        return
    nombre = " ".join(
        x for x in (solicitud.get("first_name", ""), solicitud.get("last_name", "")) if x
    ).strip() or "@usuario"
    if accion == "solicitud_rechazar":
        quitar_solicitud_acceso(user_id)
        quitar_autorizacion_pendiente(query.from_user.id)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ <b>Solicitud no aprobada.</b>\n\n"
                    "Tu solicitud de acceso a KAIRA fue rechazada.\n"
                    "Puedes solicitar acceso nuevamente más adelante."
                ),
                parse_mode="HTML",
            )
        except Exception as error:
            print("⚠️ No pude avisar rechazo:", error)
        await query.edit_message_text(
            "❌ <b>SOLICITUD RECHAZADA</b>\n\n"
            f"👤 {html.escape(nombre)}\n"
            f"🆔 <code>{user_id}</code>",
            parse_mode="HTML",
        )
        return
    if accion != "solicitud_autorizar":
        await query.edit_message_text("❌ Acción no reconocida.")
        return
    # IMPORTANTE: NO agregar al usuario autorizado todavía.
    guardar_autorizacion_pendiente(query.from_user.id, user_id, nombre)
    try:
        from membresias import listar_precios_planes
        precios = listar_precios_planes()
    except Exception:
        precios = {}
    await query.edit_message_text(
        "🟡 <b>AUTORIZACIÓN PENDIENTE DE PLAN</b>\n\n"
        f"👤 {html.escape(nombre)}\n"
        f"🆔 <code>{user_id}</code>\n\n"
        "La autorización fue aceptada, pero <b>todavía NO tiene acceso</b>.\n"
        "Ahora selecciona qué activar respondiendo en este chat:\n\n"
        "🎁 <b>Prueba</b> — 5 días · GRATIS\n"
        f"📅 <b>Semanal</b> — ${float(precios.get('Semanal', 0) or 0):.2f}\n"
        f"📆 <b>Mensual</b> — ${float(precios.get('Mensual', 0) or 0):.2f}\n"
        f"📊 <b>Trimestral</b> — ${float(precios.get('Trimestral', 0) or 0):.2f}\n"
        f"🗓️ <b>Semestral</b> — ${float(precios.get('Semestral', 0) or 0):.2f}\n"
        f"📚 <b>Anual</b> — ${float(precios.get('Anual', 0) or 0):.2f}\n\n"
        "Ejemplo: <code>Prueba</code> o <code>Mensual</code>",
        parse_mode="HTML",
    )
    quitar_solicitud_acceso(user_id)


# =========================================================
# CONFIGURACIÓN
# =========================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")



# =========================================================
# 📤 ESTADO DE ENTREGA DESDE TELEGRAM
# =========================================================

ARCHIVO_ENTREGA_PENDIENTE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "entregas_pendientes.json",
)


def cargar_entregas_pendientes():
    return _cargar_json(
        ARCHIVO_ENTREGA_PENDIENTE,
        {},
    )


def guardar_entregas_pendientes(datos):
    return _guardar_json(
        ARCHIVO_ENTREGA_PENDIENTE,
        datos,
    )


def guardar_entrega_pendiente(
    user_id,
    assignid,
    nombre_tarea="",
):
    datos = cargar_entregas_pendientes()

    datos[str(int(user_id))] = {
        "telegram_user_id": int(user_id),
        "assignid": int(assignid),
        "nombre_tarea": (
            nombre_tarea or "Actividad"
        ),
        "creado_en": datetime.now().isoformat(),
    }

    guardar_entregas_pendientes(datos)


def obtener_entrega_pendiente(
    user_id
):
    return cargar_entregas_pendientes().get(
        str(int(user_id))
    )


def quitar_entrega_pendiente(
    user_id
):
    datos = cargar_entregas_pendientes()
    datos.pop(
        str(int(user_id)),
        None,
    )
    guardar_entregas_pendientes(datos)


# =========================================================
# 🔎 REVISIÓN PREVIA DE TRABAJOS
# =========================================================

ARCHIVO_REVISION_PENDIENTE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "revisiones_pendientes.json",
)


def cargar_revisiones_pendientes():
    return _cargar_json(ARCHIVO_REVISION_PENDIENTE, {})


def guardar_revisiones_pendientes(datos):
    return _guardar_json(ARCHIVO_REVISION_PENDIENTE, datos)


def guardar_revision_pendiente(user_id, assignid, nombre_tarea, nombre_archivo, ruta_archivo, resultado=None, contexto=None):
    datos = cargar_revisiones_pendientes()
    datos[str(int(user_id))] = {
        "telegram_user_id": int(user_id),
        "assignid": int(assignid),
        "nombre_tarea": nombre_tarea or "Actividad",
        "nombre_archivo": nombre_archivo or "archivo",
        "ruta_archivo": ruta_archivo,
        "resultado": resultado or {},
        "contexto": contexto or {},
        "creado_en": datetime.now().isoformat(),
    }
    guardar_revisiones_pendientes(datos)


def obtener_revision_pendiente(user_id):
    datos = cargar_revisiones_pendientes()
    item = datos.get(str(int(user_id)))
    if not item:
        return None
    try:
        creado = datetime.fromisoformat(item.get("creado_en", ""))
        if (datetime.now() - creado).total_seconds() > 1800:
            ruta = item.get("ruta_archivo")
            if ruta and os.path.exists(ruta):
                os.remove(ruta)
            datos.pop(str(int(user_id)), None)
            guardar_revisiones_pendientes(datos)
            return None
    except Exception:
        pass
    return item


def quitar_revision_pendiente(user_id, borrar_archivo=True):
    datos = cargar_revisiones_pendientes()
    item = datos.pop(str(int(user_id)), None)
    guardar_revisiones_pendientes(datos)
    if borrar_archivo and item:
        ruta = item.get("ruta_archivo")
        if ruta and os.path.exists(ruta):
            try:
                os.remove(ruta)
            except Exception:
                pass


# =========================================================
# 📁 CARPETA DE ARCHIVOS PARA TELEGRAM
# =========================================================

CARPETA_ARCHIVOS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "archivos_telegram",
)

os.makedirs(
    CARPETA_ARCHIVOS,
    exist_ok=True,
)


# =========================================================
# 🧠 CEREBRO PRINCIPAL DE KAIRA
# =========================================================

procesar_mensaje_callback = None
ultimo_chat_id = None


ARCHIVO_CONFIG_TELEGRAM = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "telegram_config.json",
)


def cargar_ultimo_chat():
    global ultimo_chat_id

    try:
        if not os.path.exists(ARCHIVO_CONFIG_TELEGRAM):
            return

        with open(
            ARCHIVO_CONFIG_TELEGRAM,
            "r",
            encoding="utf-8",
        ) as archivo:
            datos = json.load(archivo)

        chat_id = datos.get("ultimo_chat_id")

        if chat_id is not None:
            ultimo_chat_id = int(chat_id)

            print(
                "📱 Chat de Telegram recuperado:",
                ultimo_chat_id
            )

    except Exception as error:
        print(
            "⚠️ No pude recuperar el chat de Telegram:",
            error
        )


def guardar_ultimo_chat(chat_id):
    try:
        with open(
            ARCHIVO_CONFIG_TELEGRAM,
            "w",
            encoding="utf-8",
        ) as archivo:
            json.dump(
                {
                    "ultimo_chat_id": chat_id
                },
                archivo,
                ensure_ascii=False,
                indent=4,
            )

    except Exception as error:
        print(
            "⚠️ No pude guardar el chat de Telegram:",
            error
        )


def registrar_cerebro(funcion):
    global procesar_mensaje_callback
    procesar_mensaje_callback = funcion



# =========================================================
# 🔐 VINCULACIÓN DE MOODLE SIN TOKEN MANUAL
# =========================================================

ARCHIVO_BAJA_PENDIENTE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "bajas_pendientes.json",
)


def cargar_bajas_pendientes():
    return _cargar_json(
        ARCHIVO_BAJA_PENDIENTE,
        {}
    )


def guardar_bajas_pendientes(datos):
    _guardar_json(
        ARCHIVO_BAJA_PENDIENTE,
        datos
    )


def marcar_baja_pendiente(user_id):
    datos = cargar_bajas_pendientes()
    datos[str(int(user_id))] = {
        "telegram_user_id": int(user_id),
        "creado_en": datetime.now().isoformat(),
    }
    guardar_bajas_pendientes(datos)


def quitar_baja_pendiente(user_id):
    datos = cargar_bajas_pendientes()
    datos.pop(str(int(user_id)), None)
    guardar_bajas_pendientes(datos)


def tiene_baja_pendiente(user_id):
    return str(int(user_id)) in cargar_bajas_pendientes()


ARCHIVO_LOGIN_MOODLE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "moodle_login_pendiente.json",
)


def cargar_logins_pendientes():
    return _cargar_json(
        ARCHIVO_LOGIN_MOODLE,
        {}
    )


def guardar_logins_pendientes(datos):
    _guardar_json(
        ARCHIVO_LOGIN_MOODLE,
        datos
    )


def borrar_login_pendiente(user_id):
    datos = cargar_logins_pendientes()
    datos.pop(str(user_id), None)
    guardar_logins_pendientes(datos)


def guardar_login_pendiente(
    user_id,
    etapa,
    usuario="",
):
    datos = cargar_logins_pendientes()
    datos[str(user_id)] = {
        "etapa": etapa,
        "usuario": usuario,
        "creado_en": datetime.now().isoformat(),
    }
    guardar_logins_pendientes(datos)


# =========================================================
# 🔐 CONTROL DE ACCESO MULTIUSUARIO
# =========================================================

ARCHIVO_ACCESO_TELEGRAM = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "telegram_acceso.json",
)

ARCHIVO_BLOQUEADOS_TELEGRAM = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "telegram_bloqueados.json",
)


ARCHIVO_SOLICITUDES_ACCESO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "solicitudes_acceso.json",
)


def cargar_solicitudes_acceso():
    datos = _cargar_json(
        ARCHIVO_SOLICITUDES_ACCESO,
        {}
    )
    return datos if isinstance(datos, dict) else {}


def guardar_solicitudes_acceso(datos):
    return _guardar_json(
        ARCHIVO_SOLICITUDES_ACCESO,
        datos
    )


def solicitud_acceso_pendiente(user_id):
    return str(
        int(user_id)
    ) in cargar_solicitudes_acceso()


def registrar_solicitud_acceso(user):
    datos = cargar_solicitudes_acceso()

    datos[str(int(user.id))] = {
        "telegram_user_id": int(user.id),
        "first_name": (
            user.first_name or ""
        ).strip(),
        "last_name": (
            user.last_name or ""
        ).strip(),
        "username": (
            user.username or ""
        ).strip(),
        "creado_en": datetime.now().isoformat(),
    }

    return guardar_solicitudes_acceso(
        datos
    )


def quitar_solicitud_acceso(user_id):
    datos = cargar_solicitudes_acceso()

    datos.pop(
        str(int(user_id)),
        None
    )

    return guardar_solicitudes_acceso(
        datos
    )



ARCHIVO_RESUMENES_ACADEMICOS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "resumenes_academicos.json",
)


def _id_admin():
    valor = os.getenv(
        "KAIRA_ADMIN_TELEGRAM_ID",
        ""
    ).strip()

    try:
        return int(valor)
    except Exception:
        return None


def _cargar_json(ruta, defecto):
    try:
        if not os.path.exists(ruta):
            return defecto

        with open(
            ruta,
            "r",
            encoding="utf-8",
        ) as archivo:
            datos = json.load(archivo)

        return datos
    except Exception as error:
        print(
            "⚠️ No pude cargar:",
            ruta,
            error,
        )
        return defecto


def _guardar_json(ruta, datos):
    try:
        with open(
            ruta,
            "w",
            encoding="utf-8",
        ) as archivo:
            json.dump(
                datos,
                archivo,
                ensure_ascii=False,
                indent=4,
            )

        return True

    except Exception as error:
        print(
            "⚠️ No pude guardar:",
            ruta,
            error,
        )
        return False


def _normalizar_usuario_registro(
    telegram_user_id,
    first_name="",
    last_name="",
    username="",
):
    return {
        "telegram_user_id": int(telegram_user_id),
        "first_name": (first_name or "").strip(),
        "last_name": (last_name or "").strip(),
        "username": (username or "").strip(),
        "autorizado_en": datetime.now().isoformat(),
        "ultimo_acceso": datetime.now().isoformat(),
    }


def cargar_usuarios_autorizados():
    """
    Guarda los usuarios autorizados como registros con nombre,
    apellido, username e ID.
    """

    datos = _cargar_json(
        ARCHIVO_ACCESO_TELEGRAM,
        [],
    )

    usuarios = {}

    if isinstance(datos, dict):
        datos = list(datos.values())

    if isinstance(datos, list):

        for item in datos:

            # Compatibilidad con el formato anterior:
            # una simple lista de IDs.
            if isinstance(item, int) or (
                isinstance(item, str)
                and item.isdigit()
            ):

                user_id = int(item)

                usuarios[user_id] = {
                    "telegram_user_id": user_id,
                    "first_name": "",
                    "last_name": "",
                    "username": "",
                    "autorizado_en": "",
                    "ultimo_acceso": "",
                }

                continue

            if not isinstance(item, dict):
                continue

            try:
                user_id = int(
                    item.get(
                        "telegram_user_id"
                    )
                )
            except Exception:
                continue

            usuarios[user_id] = {
                "telegram_user_id": user_id,
                "first_name": str(
                    item.get(
                        "first_name",
                        ""
                    )
                ),
                "last_name": str(
                    item.get(
                        "last_name",
                        ""
                    )
                ),
                "username": str(
                    item.get(
                        "username",
                        ""
                    )
                ),
                "autorizado_en": str(
                    item.get(
                        "autorizado_en",
                        ""
                    )
                ),
                "ultimo_acceso": str(
                    item.get(
                        "ultimo_acceso",
                        ""
                    )
                ),
            }

    # Agregar el administrador siempre.
    admin_id = _id_admin()

    if admin_id is not None and admin_id not in usuarios:
        usuarios[admin_id] = {
            "telegram_user_id": admin_id,
            "first_name": "Administrador",
            "last_name": "",
            "username": "",
            "autorizado_en": "",
            "ultimo_acceso": "",
        }

    return usuarios


def guardar_usuarios_autorizados(usuarios):
    datos = [
        usuarios[user_id]
        for user_id in sorted(
            usuarios.keys()
        )
    ]

    return _guardar_json(
        ARCHIVO_ACCESO_TELEGRAM,
        datos,
    )


def cargar_ids_bloqueados():
    datos = _cargar_json(
        ARCHIVO_BLOQUEADOS_TELEGRAM,
        [],
    )

    ids = set()

    if isinstance(datos, list):

        for valor in datos:

            try:
                ids.add(
                    int(valor)
                )
            except Exception:
                pass

    return ids


def guardar_ids_bloqueados(ids):
    return _guardar_json(
        ARCHIVO_BLOQUEADOS_TELEGRAM,
        sorted(
            int(valor)
            for valor in ids
        ),
    )


def usuario_admin(telegram_user_id):
    admin_id = _id_admin()

    try:
        return (
            admin_id is not None
            and int(telegram_user_id) == admin_id
        )
    except Exception:
        return False



def membresia_aun_vigente(
    telegram_user_id
):
    """
    Devuelve True si existe una membresía con fecha de vencimiento futura.
    No modifica el estado ni crea una nueva membresía.
    """
    try:
        from membresias import (
            obtener_usuario,
        )

        membresia=obtener_usuario(
            int(telegram_user_id)
        )

        if not membresia:
            return False

        fecha=membresia.get(
            "fecha_vencimiento"
        )

        if not fecha:
            return False

        # Normalizar formatos ISO conocidos.
        texto=str(
            fecha
        ).strip().replace(
            "Z",
            ""
        )

        try:
            vencimiento=datetime.fromisoformat(
                texto
            )
        except Exception:
            try:
                vencimiento=datetime.strptime(
                    texto[:19],
                    "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                return False

        return (
            vencimiento > datetime.now()
        )

    except Exception as error:
        print(
            "⚠️ No pude comprobar vigencia para reactivación:",
            error
        )
        return False


def membresia_expirada(
    telegram_user_id
):
    return not membresia_aun_vigente(
        telegram_user_id
    )


def usuario_bloqueado(telegram_user_id):
    try:
        return (
            int(telegram_user_id)
            in cargar_ids_bloqueados()
        )
    except Exception:
        return False


def usuario_autorizado(telegram_user_id):
    try:

        user_id = int(
            telegram_user_id
        )

        if usuario_bloqueado(
            user_id
        ):
            return False

        usuarios = (
            cargar_usuarios_autorizados()
        )

        return (
            user_id in usuarios
        )

    except Exception:
        return False


def registrar_datos_usuario(update):
    """
    Actualiza nombre/apellido/username del usuario cuando
    ya está autorizado.
    """

    if not update.effective_user:
        return

    user = update.effective_user

    user_id = int(
        user.id
    )

    usuarios = (
        cargar_usuarios_autorizados()
    )

    if user_id not in usuarios:
        return

    registro = usuarios[user_id]

    registro["first_name"] = (
        user.first_name or ""
    ).strip()

    registro["last_name"] = (
        user.last_name or ""
    ).strip()

    registro["username"] = (
        user.username or ""
    ).strip()

    registro["ultimo_acceso"] = (
        datetime.now().isoformat()
    )

    usuarios[user_id] = registro

    guardar_usuarios_autorizados(
        usuarios
    )


async def comprobar_acceso(update):
    """
    True si el usuario puede utilizar KAIRA.

    Usuario bloqueado: silencio total.
    Administrador: siempre permitido.
    Usuario sin membresía: acceso según su estado de autorización.
    Membresía vencida: se informa una vez y se bloquea.
    """

    if not update.effective_user:
        return False

    user_id = update.effective_user.id

    # Bloqueado por revocación: no responder.
    if usuario_bloqueado(user_id):
        print(
            f"🚫 Usuario bloqueado ignorado: {user_id}"
        )
        return False

    # Administrador siempre tiene acceso.
    if usuario_admin(user_id):
        registrar_datos_usuario(update)
        return True

    # Primero debe estar autorizado.
    if not usuario_autorizado(user_id):
        if update.message:
            try:
                teclado = InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            "📩 SOLICITAR ACCESO",
                            callback_data="solicitar_acceso"
                        )
                    ]]
                )

                await update.message.reply_text(
                    "🔒 <b>ACCESO PRIVADO</b>\n\n"
                    "Este bot de KAIRA es privado.\n"
                    "Para utilizarlo, solicita acceso al administrador.",
                    parse_mode="HTML",
                    reply_markup=teclado,
                )
            except Exception as error:
                print(
                    "⚠️ No pude informar acceso denegado:",
                    error
                )

        print(
            f"🔒 Acceso denegado a Telegram ID: {user_id}"
        )
        return False

    # Consultar membresía.
    try:
        from membresias import (
            crear_o_actualizar_usuario,
            actualizar_estados,
            obtener_usuario,
        )

        registrar_datos_usuario(update)

        actualizar_estados()
        membresia = obtener_usuario(user_id)

        # Si no existe ficha, solo se crea una prueba si todavía no fue usada.
        registro_aut = cargar_usuarios_autorizados().get(user_id, {})
        prueba_usada = bool(registro_aut.get("prueba_usada", False))

        if membresia is None:
            if prueba_usada:
                await mostrar_pantalla_membresia_bloqueada(update)
                return False
            crear_o_actualizar_usuario(
                user_id,
                update.effective_user.first_name or "",
                update.effective_user.last_name or "",
                update.effective_user.username or "",
            )
            usuarios_aut = cargar_usuarios_autorizados()
            usuarios_aut.setdefault(user_id, {})["prueba_usada"] = True
            guardar_usuarios_autorizados(usuarios_aut)
            membresia = obtener_usuario(user_id)

        if not membresia or membresia.get("estado") != "activa":
            await mostrar_pantalla_membresia_bloqueada(update)
            return False

    except Exception as error:
        print("⚠️ No pude comprobar la membresía:", error)
        # Fail-closed: si no podemos comprobar la membresía, no damos acceso.
        return False

    return True


def nombre_visible_usuario(registro):
    nombre = " ".join(
        parte
        for parte in (
            registro.get(
                "first_name",
                ""
            ).strip(),
            registro.get(
                "last_name",
                ""
            ).strip(),
        )
        if parte
    ).strip()

    if nombre:
        return nombre

    username = (
        registro.get(
            "username",
            ""
        ).strip()
    )

    if username:
        return (
            "@"
            + username.lstrip("@")
        )

    return "Sin nombre registrado"




def actualizar_nombre_desde_moodle(
    telegram_user_id,
    datos_moodle,
):
    """
    Después de validar el token, utiliza el nombre real de Moodle
    para que el administrador vea a quién pertenece la cuenta.
    """

    if not isinstance(datos_moodle, dict):
        return False

    try:
        user_id = int(
            telegram_user_id
        )
    except Exception:
        return False

    usuarios = (
        cargar_usuarios_autorizados()
    )

    if user_id not in usuarios:
        return False

    first = str(
        datos_moodle.get(
            "firstname",
            ""
        ) or ""
    ).strip()

    last = str(
        datos_moodle.get(
            "lastname",
            ""
        ) or ""
    ).strip()

    fullname = str(
        datos_moodle.get(
            "fullname",
            ""
        ) or ""
    ).strip()

    if not first and not last and fullname:

        partes = fullname.split()

        if len(partes) == 1:
            first = partes[0]
        elif len(partes) >= 2:
            first = partes[0]
            last = " ".join(
                partes[1:]
            )

    if first:
        usuarios[user_id][
            "first_name"
        ] = first

    if last:
        usuarios[user_id][
            "last_name"
        ] = last

    usuarios[user_id][
        "moodle_fullname"
    ] = fullname

    usuarios[user_id][
        "moodle_userid"
    ] = datos_moodle.get(
        "userid"
    )

    usuarios[user_id][
        "moodle_registrado_en"
    ] = datetime.now().isoformat()

    return guardar_usuarios_autorizados(
        usuarios
    )


async def comando_autorizar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    admin_id = update.effective_user.id

    if not usuario_admin(
        admin_id
    ):
        return

    argumentos = (
        context.args or []
    )

    if not argumentos:

        await update.message.reply_text(
            "Uso:\n"
            "/autorizar ID_TELEGRAM\n\n"
            "Después KAIRA te preguntará qué plan tendrá."
        )

        return

    try:
        usuario_id = int(
            argumentos[0]
        )
    except Exception:

        await update.message.reply_text(
            "⚠️ El ID debe ser un número."
        )

        return

    if usuario_id == _id_admin():

        await update.message.reply_text(
            "👑 Ese ID ya es el administrador principal."
        )

        return

    # Obtener nombre de Telegram antes de pedir el plan.
    nombre = ""

    try:

        chat = await context.bot.get_chat(
            usuario_id
        )

        first = (
            getattr(
                chat,
                "first_name",
                ""
            ) or ""
        ).strip()

        last = (
            getattr(
                chat,
                "last_name",
                ""
            ) or ""
        ).strip()

        nombre = (
            first
            + " "
            + last
        ).strip()

    except Exception as error:

        print(
            "⚠️ No pude obtener el nombre del usuario:",
            error
        )

    guardar_autorizacion_pendiente(
        admin_id,
        usuario_id,
        nombre,
    )

    try:
        from membresias import listar_precios_planes

        precios = listar_precios_planes()

        await update.message.reply_text(
            "👤 <b>REGISTRAR USUARIO</b>\n\n"
            f"Nombre: <b>{nombre or 'Pendiente'}</b>\n"
            f"ID: <code>{usuario_id}</code>\n\n"
            "Selecciona el plan respondiendo con uno de estos:\n\n"
            f"📅 Semanal — <b>${precios.get('Semanal', 0):.2f}</b>\n"
            f"📆 Mensual — <b>${precios.get('Mensual', 0):.2f}</b>\n"
            f"📊 Trimestral — <b>${precios.get('Trimestral', 0):.2f}</b>\n"
            f"🗓️ Semestral — <b>${precios.get('Semestral', 0):.2f}</b>\n"
            f"📚 Anual — <b>${precios.get('Anual', 0):.2f}</b>\n\n"
            "Ejemplo: <code>Mensual</code>",
            parse_mode="HTML",
        )

    except Exception:
        await update.message.reply_text(
            "👤 Usuario listo para registrar.\n\n"
            "Responde con:\n"
            "Semanal\n"
            "Mensual\n"
            "Trimestral\n"
            "Semestral\n"
            "Anual"
        )


async def comando_revocar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Revoca el acceso sin eliminar la ficha del usuario.
    Conserva historial, Moodle y datos de la cuenta.
    """
    if not update.message or not update.effective_user:
        return

    if not usuario_admin(
        update.effective_user.id
    ):
        return

    argumentos=context.args or []

    if not argumentos:
        await update.message.reply_text(
            "Uso:\n"
            "/revocar ID_TELEGRAM\n\n"
            "Ejemplo:\n"
            "/revocar 123456789"
        )
        return

    try:
        usuario_id=int(
            argumentos[0]
        )
    except Exception:
        await update.message.reply_text(
            "⚠️ El ID debe ser un número."
        )
        return

    if usuario_id==_id_admin():
        await update.message.reply_text(
            "⛔ No puedes revocar al administrador principal."
        )
        return

    usuarios=cargar_usuarios_autorizados()
    registro=usuarios.get(
        usuario_id
    )

    # Compatibilidad: si existe en bloqueados pero por una versión
    # anterior se había eliminado de usuarios, intentar reconstruir la ficha.
    if registro is None:
        bloqueados=cargar_ids_bloqueados()

        if usuario_id not in bloqueados:
            await update.message.reply_text(
                f"ℹ️ No encontré al usuario {usuario_id}."
            )
            return

        registro={
            "telegram_user_id": usuario_id,
            "first_name": "",
            "last_name": "",
            "username": "",
            "autorizado_en": "",
            "ultimo_acceso": "",
        }

        try:
            chat=await context.bot.get_chat(
                usuario_id
            )

            registro["first_name"]=(
                getattr(
                    chat,
                    "first_name",
                    ""
                ) or ""
            ).strip()

            registro["last_name"]=(
                getattr(
                    chat,
                    "last_name",
                    ""
                ) or ""
            ).strip()

            registro["username"]=(
                getattr(
                    chat,
                    "username",
                    ""
                ) or ""
            ).strip()
        except Exception as error:
            print(
                "⚠️ No pude recuperar el perfil revocado:",
                error
            )

        usuarios[usuario_id]=registro

    nombre=nombre_visible_usuario(
        registro
    )

    bloqueados=cargar_ids_bloqueados()
    bloqueados.add(
        usuario_id
    )

    # IMPORTANTE:
    # Nunca borrar la ficha.
    guardar_usuarios_autorizados(
        usuarios
    )
    guardar_ids_bloqueados(
        bloqueados
    )

    # Detener temporalmente el job del resumen sin borrar su configuración.
    try:
        if context.job_queue is not None:
            nombre_job=_nombre_job_resumen(
                usuario_id
            )

            for job in context.job_queue.get_jobs_by_name(
                nombre_job
            ):
                job.schedule_removal()
    except Exception as error:
        print(
            "⚠️ No pude detener el resumen:",
            error
        )

    vigente=membresia_aun_vigente(
        usuario_id
    )

    if vigente:
        acceso_futuro=(
            "✅ Conserva su membresía.\n"
            "🔄 Puede reactivarse mientras siga vigente."
        )
    else:
        acceso_futuro=(
            "🔴 La membresía ya no está vigente.\n"
            "👑 Para volver necesitará autorización del administrador."
        )

    await update.message.reply_text(
        "🔒 <b>ACCESO REVOCADO</b>\n\n"
        f"👤 {nombre}\n"
        f"🆔 <code>{usuario_id}</code>\n\n"
        "📋 La ficha del usuario permanece guardada.\n"
        "🎓 Moodle permanece vinculado.\n"
        "💳 El historial permanece guardado.\n\n"
        + acceso_futuro,
        parse_mode="HTML",
    )




async def comando_autorizados(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    if not usuario_admin(
        update.effective_user.id
    ):
        return

    usuarios = (
        cargar_usuarios_autorizados()
    )

    if not usuarios:

        await update.message.reply_text(
            "📋 No hay usuarios autorizados."
        )

        return

    admin_id = _id_admin()

    lineas = [
        "👥 USUARIOS AUTORIZADOS",
        "",
    ]

    contador = 0

    for user_id in sorted(
        usuarios.keys()
    ):

        contador += 1

        registro = usuarios[user_id]

        nombre = nombre_visible_usuario(
            registro
        )

        if user_id == admin_id:

            lineas.append(
                f"{contador}. 👑 {nombre}\n"
                f"   🆔 {user_id}\n"
                f"   🔐 ADMINISTRADOR"
            )

        else:

            moodle_estado = "No vinculado"

            try:

                from moodle_kaira import (
                    obtener_configuracion_moodle_usuario
                )

                if obtener_configuracion_moodle_usuario(
                    user_id
                ):
                    moodle_estado = "Vinculado"

            except Exception:
                pass

            lineas.append(
                f"{contador}. 👤 {nombre}\n"
                f"   🆔 {user_id}\n"
                f"   🎓 Moodle: {moodle_estado}"
            )

        lineas.append("")

    await update.message.reply_text(
        "\n".join(
            lineas
        )
    )


# Inicializar archivos.
cargar_usuarios_autorizados()
cargar_ids_bloqueados()


# =========================================================
# ⏰ ARCHIVO DE RECORDATORIOS
# =========================================================

# =========================================================
# ⏰ ARCHIVO DE RECORDATORIOS
# =========================================================

ARCHIVO_RECORDATORIOS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "recordatorios.json",
)


def cargar_recordatorios():
    if not os.path.exists(ARCHIVO_RECORDATORIOS):
        return []

    try:
        with open(
            ARCHIVO_RECORDATORIOS,
            "r",
            encoding="utf-8",
        ) as archivo:
            datos = json.load(archivo)

        if isinstance(datos, list):
            return datos

    except Exception as error:
        print(
            "⚠️ No pude cargar los recordatorios:",
            error,
        )

    return []


def guardar_recordatorios(recordatorios):
    try:
        with open(
            ARCHIVO_RECORDATORIOS,
            "w",
            encoding="utf-8",
        ) as archivo:
            json.dump(
                recordatorios,
                archivo,
                ensure_ascii=False,
                indent=4,
            )

    except Exception as error:
        print(
            "⚠️ No pude guardar los recordatorios:",
            error,
        )


def agregar_recordatorio(
    chat_id,
    tarea,
    fecha_hora,
):
    recordatorios = cargar_recordatorios()

    recordatorio = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "tarea": tarea,
        "fecha_hora": fecha_hora.isoformat(),
    }

    recordatorios.append(recordatorio)

    guardar_recordatorios(recordatorios)

    return recordatorio


def eliminar_recordatorio(recordatorio_id):
    recordatorios = cargar_recordatorios()

    nuevos = [
        r
        for r in recordatorios
        if r.get("id") != recordatorio_id
    ]

    guardar_recordatorios(nuevos)


# =========================================================
# ⏰ ENVIAR RECORDATORIO
# =========================================================

async def enviar_recordatorio(
    context: ContextTypes.DEFAULT_TYPE,
):
    datos = context.job.data

    chat_id = datos["chat_id"]
    tarea = datos["tarea"]
    recordatorio_id = datos.get("recordatorio_id")

    print(
        f"\n⏰ RECORDATORIO: {tarea}"
    )

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⏰ RECORDATORIO DE KAIRA\n\n"
                f"🔔 {tarea}"
            ),
        )

    except Exception as error:
        print(
            "⚠️ No pude enviar el recordatorio:",
            error,
        )
        return

    if recordatorio_id:
        eliminar_recordatorio(recordatorio_id)

        print(
            "🗑️ Recordatorio eliminado de memoria."
        )


# =========================================================
# 📤 ENVIAR ARCHIVO
# =========================================================

async def enviar_archivo_telegram(
    update,
    ruta,
):
    if not os.path.exists(ruta):
        await update.message.reply_text(
            "❌ No encontré ese archivo."
        )
        return False

    if not os.path.isfile(ruta):
        await update.message.reply_text(
            "❌ La ruta indicada no es un archivo."
        )
        return False

    nombre = os.path.basename(ruta)

    try:
        extensiones_imagen = (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
        )

        if nombre.lower().endswith(extensiones_imagen):

            with open(
                ruta,
                "rb",
            ) as archivo:
                await update.message.reply_photo(
                    photo=archivo,
                    caption=(
                        "🖼️ KAIRA\n\n"
                        f"📁 {nombre}"
                    ),
                )

        else:

            with open(
                ruta,
                "rb",
            ) as archivo:
                await update.message.reply_document(
                    document=archivo,
                    caption=(
                        "📁 KAIRA\n\n"
                        f"📄 {nombre}"
                    ),
                )

        print(
            f"📤 Archivo enviado a Telegram: {nombre}"
        )

        return True

    except Exception as error:

        print(
            "⚠️ Error enviando archivo:",
            error,
        )

        await update.message.reply_text(
            "❌ No pude enviar el archivo.\n"
            f"Error: {error}"
        )

        return False


# =========================================================
# 🔎 BUSCAR ARCHIVO
# =========================================================

def buscar_archivo(nombre):
    nombre = (
        nombre
        .strip()
        .strip('"')
        .strip("'")
    )

    if not nombre:
        return None

    ruta_exacta = os.path.join(
        CARPETA_ARCHIVOS,
        nombre,
    )

    if os.path.isfile(ruta_exacta):
        return ruta_exacta

    try:
        archivos = os.listdir(CARPETA_ARCHIVOS)
    except Exception:
        return None

    for archivo in archivos:

        if archivo.lower() == nombre.lower():

            ruta = os.path.join(
                CARPETA_ARCHIVOS,
                archivo,
            )

            if os.path.isfile(ruta):
                return ruta

    nombre_sin_extension = (
        os.path.splitext(nombre)[0].lower()
    )

    for archivo in archivos:

        archivo_sin_extension = (
            os.path.splitext(archivo)[0].lower()
        )

        if nombre_sin_extension in archivo_sin_extension:

            ruta = os.path.join(
                CARPETA_ARCHIVOS,
                archivo,
            )

            if os.path.isfile(ruta):
                return ruta

    return None


# =========================================================
# 📁 ÚLTIMO ARCHIVO
# =========================================================

def obtener_ultimo_archivo():
    try:
        archivos = []

        for nombre in os.listdir(CARPETA_ARCHIVOS):

            ruta = os.path.join(
                CARPETA_ARCHIVOS,
                nombre,
            )

            if os.path.isfile(ruta):
                archivos.append(ruta)

        if not archivos:
            return None

        archivos.sort(
            key=os.path.getmtime,
            reverse=True,
        )

        return archivos[0]

    except Exception as error:

        print(
            "⚠️ Error buscando último archivo:",
            error,
        )

        return None


# =========================================================
# 📋 LISTAR ARCHIVOS
# =========================================================

async def listar_archivos(update):

    try:
        archivos = []

        for nombre in os.listdir(CARPETA_ARCHIVOS):

            ruta = os.path.join(
                CARPETA_ARCHIVOS,
                nombre,
            )

            if os.path.isfile(ruta):
                archivos.append(nombre)

        if not archivos:

            await update.message.reply_text(
                "📁 La carpeta de archivos "
                "de KAIRA está vacía."
            )

            return

        archivos.sort(
            key=str.lower
        )

        lineas = [
            "📁 ARCHIVOS DE KAIRA\n"
        ]

        for numero, nombre in enumerate(
            archivos,
            start=1,
        ):
            lineas.append(
                f"{numero}. 📄 {nombre}"
            )

        await update.message.reply_text(
            "\n".join(lineas)
        )

    except Exception as error:

        print(
            "⚠️ Error listando archivos:",
            error,
        )

        await update.message.reply_text(
            "❌ No pude consultar los archivos."
        )


# =========================================================
# 📤 PROCESAR PETICIÓN DE ARCHIVO
# =========================================================

async def procesar_peticion_archivo(
    update,
    mensaje,
):
    texto = (
        mensaje
        .lower()
        .strip()
    )

    texto_limpio = re.sub(
        r"^kaira[\s,]*",
        "",
        texto,
    ).strip()

    # =====================================================
    # 📋 LISTAR ARCHIVOS
    # =====================================================

    palabras_lista = [
        "qué archivos tienes",
        "que archivos tienes",
        "qué archivos hay",
        "que archivos hay",
        "muéstrame los archivos",
        "muestrame los archivos",
        "mostrar archivos",
        "lista de archivos",
        "listar archivos",
    ]

    if any(
        frase in texto_limpio
        for frase in palabras_lista
    ):
        await listar_archivos(update)
        return True

    # =====================================================
    # 📤 ÚLTIMO ARCHIVO
    # =====================================================

    palabras_ultimo = [
        "envíame el último archivo",
        "enviame el ultimo archivo",
        "mándame el último archivo",
        "mandame el ultimo archivo",
        "envíame el último archivo que hicimos",
        "enviame el ultimo archivo que hicimos",
        "mándame el último archivo que hicimos",
        "mandame el ultimo archivo que hicimos",
    ]

    if any(
        frase in texto_limpio
        for frase in palabras_ultimo
    ):
        ruta = obtener_ultimo_archivo()

        if ruta is None:

            await update.message.reply_text(
                "📁 No tengo archivos guardados "
                "para enviarte."
            )

            return True

        await update.message.reply_text(
            "📤 Enviando el último archivo..."
        )

        await enviar_archivo_telegram(
            update,
            ruta,
        )

        return True

    # =====================================================
    # 📤 ARCHIVO ESPECÍFICO
    # =====================================================

    patrones = [

        r"(?:envíame|enviame)\s+(?:el\s+)?archivo\s+(.+)",

        r"(?:mándame|mandame)\s+(?:el\s+)?archivo\s+(.+)",

        r"(?:envía|envia)\s+(?:el\s+)?archivo\s+(.+)",

        r"(?:manda)\s+(?:el\s+)?archivo\s+(.+)",

        r"(?:envíame|enviame)\s+(?:el\s+)?pdf\s+(.+)",

        r"(?:mándame|mandame)\s+(?:el\s+)?pdf\s+(.+)",

        r"(?:envíame|enviame)\s+(?:la\s+)?imagen\s+(.+)",

        r"(?:mándame|mandame)\s+(?:la\s+)?imagen\s+(.+)",

        r"(?:envíame|enviame)\s+(?:el\s+)?código\s+(.+)",

        r"(?:envíame|enviame)\s+(?:el\s+)?codigo\s+(.+)",

        r"(?:mándame|mandame)\s+(?:el\s+)?código\s+(.+)",

        r"(?:mándame|mandame)\s+(?:el\s+)?codigo\s+(.+)",
    ]

    nombre_archivo = None

    for patron in patrones:

        coincidencia = re.search(
            patron,
            texto_limpio,
            re.IGNORECASE,
        )

        if coincidencia:
            nombre_archivo = (
                coincidencia.group(1)
                .strip()
            )
            break

    if nombre_archivo:

        nombre_archivo = (
            nombre_archivo
            .strip()
            .strip('"')
            .strip("'")
            .rstrip(".")
        )

        ruta = buscar_archivo(
            nombre_archivo
        )

        if ruta is None:

            await update.message.reply_text(
                "❌ No encontré el archivo:\n"
                f"📄 {nombre_archivo}\n\n"
                "Puedes decirme:\n"
                "📁 ¿Qué archivos tienes?"
            )

            return True

        await update.message.reply_text(
            "📤 Encontrado.\n"
            f"📄 {os.path.basename(ruta)}\n\n"
            "Enviándolo..."
        )

        await enviar_archivo_telegram(
            update,
            ruta,
        )

        return True

    return False


# =========================================================
# 🔐 VINCULACIÓN DE MOODLE
# =========================================================

async def comando_mi_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Permite que un usuario NUEVO conozca su propio ID para
    pedírselo al administrador.

    Un usuario revocado/bloqueado permanece completamente
    en silencio.
    """

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    # Usuario revocado: no responder absolutamente nada.
    if usuario_bloqueado(user_id):
        print(
            f"🚫 /mi_id ignorado para usuario bloqueado: {user_id}"
        )
        return

    if not update.message:
        return

    await update.message.reply_text(
        f"🆔 Tu ID de Telegram es: {user_id}\n\n"
        "Compártelo con el administrador para que pueda "
        "autorizar tu acceso a KAIRA."
    )

async def comando_moodle_token(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await comprobar_acceso(update):
        return

    if not update.message:
        return

    argumentos = context.args or []

    if not argumentos:
        await update.message.reply_text(
            "🎓 Ya no necesitas generar el token manualmente.\n\n"
            "Usa:\n"
            "/vincular_moodle\n\n"
            "KAIRA te pedirá tu usuario y después tu contraseña."
        )
        return

    token = argumentos[0].strip()

    if len(token) < 10:
        await update.message.reply_text(
            "⚠️ El token parece demasiado corto."
        )
        return

    try:

        from moodle_kaira import (
            guardar_token_moodle_usuario,
            establecer_usuario_telegram,
            moodle_api,
        )

        user_id = (
            update.effective_user.id
        )

        establecer_usuario_telegram(
            user_id
        )

        ok = guardar_token_moodle_usuario(
            user_id,
            token
        )

        if not ok:
            await update.message.reply_text(
                "❌ No pude guardar la vinculación de Moodle."
            )
            return

        prueba = moodle_api(
            "core_webservice_get_site_info"
        )

        if (
            not isinstance(
                prueba,
                dict
            )
            or "userid" not in prueba
        ):

            from moodle_kaira import (
                eliminar_cuenta_moodle_usuario
            )

            eliminar_cuenta_moodle_usuario(
                user_id
            )

            await update.message.reply_text(
                "❌ El token de Moodle no fue aceptado."
            )

            try:
                await update.message.delete()
            except Exception:
                pass

            return

        actualizar_nombre_desde_moodle(
            user_id,
            prueba
        )

        await update.message.reply_text(
            "✅ Moodle vinculado correctamente.\n\n"
            "Ya puedes usar tus consultas académicas."
        )

    except Exception as error:

        print(
            "⚠️ Error vinculando Moodle:",
            error
        )

        await update.message.reply_text(
            "❌ Ocurrió un problema al vincular Moodle."
        )

    try:
        await update.message.delete()
    except Exception:
        pass



async def comando_desvincular_moodle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await comprobar_acceso(update):
        return

    if not update.message:
        return

    try:
        from moodle_kaira import eliminar_cuenta_moodle_usuario
        ok = eliminar_cuenta_moodle_usuario(update.effective_user.id)
        if ok:
            await update.message.reply_text(
                "🗑️ Tu cuenta de Moodle fue desvinculada de KAIRA."
            )
        else:
            await update.message.reply_text(
                "⚠️ No pude desvincular tu cuenta de Moodle."
            )
    except Exception as error:
        print("⚠️ Error desvinculando Moodle:", error)
        await update.message.reply_text(
            "❌ Ocurrió un problema al desvincular Moodle."
        )



# =========================================================
# 🔔 ALERTAS DE VENCIMIENTO DE MEMBRESÍA
# =========================================================

ARCHIVO_ALERTAS_MEMBRESIA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "alertas_membresia.json",
)


def cargar_alertas_membresia():
    return _cargar_json(
        ARCHIVO_ALERTAS_MEMBRESIA,
        {}
    )


def guardar_alertas_membresia(datos):
    return _guardar_json(
        ARCHIVO_ALERTAS_MEMBRESIA,
        datos
    )


def registrar_alerta_membresia(
    telegram_user_id,
    fecha_vencimiento,
):
    datos = cargar_alertas_membresia()

    datos[str(int(telegram_user_id))] = {
        "telegram_user_id": int(telegram_user_id),
        "fecha_vencimiento": str(
            fecha_vencimiento
        ),
        "aviso_3_dias": False,
    }

    return guardar_alertas_membresia(
        datos
    )


async def comprobar_alerta_membresia(
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Se ejecuta cada hora. Cuando faltan <= 3 días y > 0,
    envía una sola alerta en la misma hora aproximada de la
    verificación diaria. También avisa al vencer.
    """

    try:
        from membresias import (
            listar_usuarios,
            dias_para_vencer,
        )

        usuarios = listar_usuarios()
        alertas = cargar_alertas_membresia()

        for usuario in usuarios:

            try:
                user_id = int(
                    usuario.get(
                        "telegram_user_id"
                    )
                )
            except Exception:
                continue

            if usuario.get(
                "estado"
            ) == "baja_usuario":
                continue

            if not usuario.get(
                "fecha_vencimiento"
            ):
                continue

            dias = dias_para_vencer(
                usuario.get(
                    "fecha_vencimiento"
                )
            )

            if dias is None:
                continue

            clave = str(
                user_id
            )

            registro_alerta = alertas.get(
                clave,
                {}
            )

            fecha_guardada = str(
                registro_alerta.get(
                    "fecha_vencimiento",
                    ""
                )
            )

            if (
                fecha_guardada
                != str(
                    usuario.get(
                        "fecha_vencimiento"
                    )
                )
            ):
                registro_alerta = {
                    "fecha_vencimiento": str(
                        usuario.get(
                            "fecha_vencimiento"
                        )
                    ),
                    "aviso_3_dias": False,
                    "aviso_vencimiento": False,
                }

            # Solo cuentas activas/autorizadas reciben aviso.
            if not usuario_autorizado(
                user_id
            ):
                continue

            if (
                dias <= 3
                and dias > 0
                and not registro_alerta.get(
                    "aviso_3_dias",
                    False
                )
            ):
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "⚠️ <b>Tu membresía está por vencer.</b>\n\n"
                            f"📅 Fecha de vencimiento: "
                            f"{usuario.get('fecha_vencimiento')}\n"
                            f"⏳ Te quedan aproximadamente <b>{dias} día(s)</b>.\n\n"
                            "💳 Renueva tu membresía para conservar tu acceso a KAIRA."
                        ),
                        parse_mode="HTML",
                    )

                    registro_alerta[
                        "aviso_3_dias"
                    ] = True

                except Exception as error:
                    print(
                        "⚠️ No pude enviar aviso de vencimiento:",
                        error
                    )

            if (
                dias < 0
                and not registro_alerta.get(
                    "aviso_vencimiento",
                    False
                )
            ):
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "🔴 <b>Tu membresía ha vencido.</b>\n\n"
                            "Tu acceso a KAIRA puede quedar suspendido.\n"
                            "Contacta al administrador para renovarla."
                        ),
                        parse_mode="HTML",
                    )

                    registro_alerta[
                        "aviso_vencimiento"
                    ] = True

                except Exception as error:
                    print(
                        "⚠️ No pude enviar aviso de membresía vencida:",
                        error
                    )

            alertas[clave] = registro_alerta

        guardar_alertas_membresia(
            alertas
        )

    except Exception as error:
        print(
            "⚠️ Error comprobando vencimientos de membresía:",
            error
        )


async def sincronizar_alertas_membresia(
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Registra o actualiza fechas de corte para que el sistema pueda
    detectar cambios de plan o renovación.
    """

    try:
        from membresias import listar_usuarios

        alertas = cargar_alertas_membresia()

        for usuario in listar_usuarios():

            fecha = usuario.get(
                "fecha_vencimiento"
            )

            if not fecha:
                continue

            uid = usuario.get(
                "telegram_user_id"
            )

            if uid is None:
                continue

            clave = str(
                int(uid)
            )

            anterior = alertas.get(
                clave,
                {}
            )

            if anterior.get(
                "fecha_vencimiento"
            ) != str(fecha):

                alertas[clave] = {
                    "telegram_user_id": int(uid),
                    "fecha_vencimiento": str(fecha),
                    "aviso_3_dias": False,
                    "aviso_vencimiento": False,
                }

        guardar_alertas_membresia(
            alertas
        )

    except Exception as error:
        print(
            "⚠️ Error sincronizando alertas de membresía:",
            error
        )


# =========================================================
# 💾 CONFIGURACIÓN DE RESÚMENES
# =========================================================

def cargar_resumenes_academicos():
    datos = _cargar_json(
        ARCHIVO_RESUMENES_ACADEMICOS,
        {}
    )

    if not isinstance(datos, dict):
        return {}

    return datos


def guardar_resumenes_academicos(datos):
    return _guardar_json(
        ARCHIVO_RESUMENES_ACADEMICOS,
        datos
    )


def guardar_configuracion_resumen(
    telegram_user_id,
    chat_id,
    hora,
    minuto
):
    datos = cargar_resumenes_academicos()

    datos[str(int(telegram_user_id))] = {
        "telegram_user_id": int(telegram_user_id),
        "chat_id": int(chat_id),
        "hora": int(hora),
        "minuto": int(minuto),
        "activo": True,
    }

    return guardar_resumenes_academicos(datos)


def eliminar_configuracion_resumen(
    telegram_user_id
):
    datos = cargar_resumenes_academicos()
    datos.pop(
        str(int(telegram_user_id)),
        None
    )
    return guardar_resumenes_academicos(datos)


def programar_resumen_usuario(
    job_queue,
    telegram_user_id,
    chat_id,
    hora,
    minuto
):
    if job_queue is None:
        return False

    nombre_job = _nombre_job_resumen(
        telegram_user_id
    )

    for job in job_queue.get_jobs_by_name(
        nombre_job
    ):
        job.schedule_removal()

    from datetime import time as dt_time

    hora_programada = dt_time(
        hour=hora,
        minute=minuto,
        tzinfo=ZoneInfo(
            "America/Mexico_City"
        )
    )

    job_queue.run_daily(
        enviar_resumen_academico,
        time=hora_programada,
        days=(0, 1, 2, 3, 4, 5, 6),
        data={
            "chat_id": int(chat_id),
            "telegram_user_id": int(telegram_user_id),
        },
        name=nombre_job,
    )

    return True


def recuperar_resumenes_academicos(
    job_queue
):
    if job_queue is None:
        return

    datos = cargar_resumenes_academicos()

    for clave, config in datos.items():

        try:
            if not config.get("activo", True):
                continue

            user_id = int(
                config["telegram_user_id"]
            )

            chat_id = int(
                config["chat_id"]
            )

            hora = int(
                config["hora"]
            )

            minuto = int(
                config["minuto"]
            )

            if not usuario_autorizado(user_id):
                continue

            programar_resumen_usuario(
                job_queue,
                user_id,
                chat_id,
                hora,
                minuto
            )

            print(
                f"🔄 Resumen académico recuperado para "
                f"{user_id}: {hora:02d}:{minuto:02d}"
            )

        except Exception as error:

            print(
                "⚠️ Error recuperando resumen académico:",
                error
            )


# =========================================================
# 🌅 RESUMEN ACADÉMICO DIARIO
# =========================================================

async def enviar_resumen_academico(
    context: ContextTypes.DEFAULT_TYPE
):
    datos = context.job.data or {}

    chat_id = datos.get(
        "chat_id"
    )

    telegram_user_id = datos.get(
        "telegram_user_id"
    )

    if not chat_id or not telegram_user_id:
        return

    if not usuario_autorizado(
        telegram_user_id
    ):
        return

    try:

        from moodle_kaira import (
            establecer_usuario_telegram,
            resumen_academico_diario,
        )

        establecer_usuario_telegram(
            telegram_user_id
        )

        respuesta = (
            resumen_academico_diario()
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=respuesta,
        )

    except Exception as error:

        print(
            "⚠️ Error enviando resumen académico:",
            error,
        )


def _nombre_job_resumen(
    telegram_user_id
):
    return (
        f"resumen_academico_{telegram_user_id}"
    )


async def comando_resumen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await comprobar_acceso(update):
        return

    if not update.message or not update.effective_user:
        return

    argumentos = (
        context.args or []
    )

    if not argumentos:

        await update.message.reply_text(
            "🌅 Configura tu resumen diario así:\n\n"
            "/resumen 07:00\n\n"
            "Ejemplos:\n"
            "/resumen 06:30\n"
            "/resumen 08:15"
        )

        return

    hora_texto = argumentos[0].strip()

    coincidencia = re.fullmatch(
        r"([01]\d|2[0-3]):([0-5]\d)",
        hora_texto,
    )

    if not coincidencia:

        await update.message.reply_text(
            "⚠️ Usa el formato HH:MM.\n\n"
            "Ejemplo: /resumen 07:00"
        )

        return

    hora = int(
        coincidencia.group(1)
    )

    minuto = int(
        coincidencia.group(2)
    )

    if context.job_queue is None:

        await update.message.reply_text(
            "⚠️ El programador de tareas no está disponible."
        )

        return

    telegram_user_id = (
        update.effective_user.id
    )

    chat_id = (
        update.effective_chat.id
    )

    ok = programar_resumen_usuario(
        context.job_queue,
        telegram_user_id,
        chat_id,
        hora,
        minuto
    )

    if not ok:
        await update.message.reply_text(
            "⚠️ No pude programar el resumen diario."
        )
        return

    guardar_configuracion_resumen(
        telegram_user_id,
        chat_id,
        hora,
        minuto
    )

    await update.message.reply_text(
        "✅ Resumen académico diario activado.\n\n"
        f"🌅 Hora: {hora_texto}\n\n"
        "KAIRA te avisará cada día sobre "
        "tareas atrasadas, entregas de hoy, "
        "entregas próximas y la información "
        "académica disponible en tu Moodle."
    )


async def comando_desactivar_resumen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await comprobar_acceso(update):
        return

    if not update.message or not update.effective_user:
        return

    if context.job_queue is not None:

        nombre_job = _nombre_job_resumen(
            update.effective_user.id
        )

        jobs = (
            context.job_queue
            .get_jobs_by_name(
                nombre_job
            )
        )

        for job in jobs:
            job.schedule_removal()

    eliminar_configuracion_resumen(
        update.effective_user.id
    )

    await update.message.reply_text(
        "🛑 Resumen académico diario desactivado."
    )


# =========================================================
# 💬 MENSAJE DE BIENVENIDA / FUNCIONES
# =========================================================

def obtener_mensaje_bienvenida(nombre=""):
    saludo = (
        f"🤖 Hola, {nombre}."
        if nombre
        else "🤖 Hola."
    )

    return (
        f"{saludo}\n\n"
        "Soy KAIRA, tu asistente personal y académico.\n\n"
        "📚 MOODLE\n"
        "• ¿Qué cursos tengo?\n"
        "• ¿Qué tareas tengo pendientes?\n"
        "• ¿Qué tareas tengo atrasadas?\n"
        "• ¿Qué tareas vencen hoy o mañana?\n"
        "• ¿Qué tengo esta semana?\n"
        "• ¿Cuál es mi próxima entrega?\n\n"
        "⏰ RECORDATORIOS\n"
        "• Recuérdame mañana a las 7 estudiar.\n"
        "• ¿Qué recordatorios tengo?\n"
        "• Cancela todos mis recordatorios.\n\n"
        "🧠 IA\n"
        "• Explícame cálculo diferencial.\n"
        "• Ayúdame con una tarea.\n"
        "• Hazme un resumen de este tema.\n\n"
        "🔐 CUENTA\n"
        "• /mi_id — ver tu ID de Telegram.\n"
        "• /vincular_moodle — conectar tu Moodle paso a paso.\n• /darme_de_baja — solicitar la baja de KAIRA.\n        "
        "• /desvincular_moodle — quitar Moodle.\n\n"
        "🌅 RESUMEN ACADÉMICO\n"
        "• /resumen 07:00 — recibir un resumen diario.\n"
        "• /desactivar_resumen — apagarlo.\n\n"
        "💡 Puedes escribirme de forma natural. "
        "No necesitas memorizar los comandos."
    )


async def enviar_instrucciones_autorizacion(
    bot,
    usuario_id,
    nombre="",
):
    try:
        await bot.send_message(
            chat_id=usuario_id,
            text=(
                "✅ <b>¡KAIRA te ha autorizado!</b>\n\n"
                "Tu acceso ya está activo.\n\n"
                "🎓 <b>Conecta tu Moodle</b> para consultar cursos, tareas, "
                "entregas, archivos y recordatorios.\n\n"
                "🔐 No necesitas generar ni copiar ningún token. "
                "KAIRA lo obtiene automáticamente."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 VINCULAR MOODLE", callback_data="menu_vincular_moodle")],
                [InlineKeyboardButton("🏠 Abrir KAIRA", callback_data="menu_principal")],
            ]),
        )

        return True

    except Exception as error:

        print(
            f"⚠️ No pude enviar instrucciones al usuario {usuario_id}:",
            error,
        )

        return False



async def comando_vincular_moodle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await comprobar_acceso(update):
        return

    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id

    guardar_login_pendiente(
        user_id,
        "usuario",
    )

    await update.message.reply_text(
        "🎓 <b>Vinculemos tu Moodle</b>\n\n"
        "Escribe ahora tu <b>usuario de Moodle</b>.\n\n"
        "Después te pediré tu contraseña.\n"
        "🔐 No guardaré tu contraseña.",
        parse_mode="HTML",
    )


async def recibir_credencial_moodle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return False

    user_id = update.effective_user.id

    if usuario_bloqueado(user_id):
        return True

    pendiente = (
        cargar_logins_pendientes()
        .get(str(user_id))
    )

    if not pendiente:
        return False

    texto = (
        update.message.text
        or ""
    ).strip()

    etapa = pendiente.get(
        "etapa"
    )

    # No permitir comandos como credenciales.
    if texto.startswith("/"):
        await update.message.reply_text(
            "⚠️ Escribe el dato solicitado o cancela con /cancelar_moodle."
        )
        return True

    if etapa == "usuario":

        guardar_login_pendiente(
            user_id,
            "password",
            texto,
        )

        try:
            await update.message.delete()
        except Exception:
            pass

        await update.message.reply_text(
            "🔐 Ahora escribe tu <b>contraseña de Moodle</b>.\n\n"
            "La contraseña se utilizará solo para autenticarte "
            "y no se guardará.",
            parse_mode="HTML",
        )

        return True

    if etapa == "password":

        usuario = pendiente.get(
            "usuario",
            ""
        )

        password = texto

        try:
            await update.message.delete()
        except Exception:
            pass

        await update.message.reply_text(
            "⏳ Verificando tu cuenta de Moodle..."
        )

        try:

            from moodle_kaira import (
                guardar_token_moodle_usuario,
                establecer_usuario_telegram,
                moodle_api,
                eliminar_cuenta_moodle_usuario,
            )

            moodle_base = os.getenv(
                "MOODLE_URL",
                "https://educacionadistancia.its-purhepecha.edu.mx",
            ).rstrip("/")

            autenticar = __import__("moodle_kaira", fromlist=["autenticar_moodle_usuario"]).autenticar_moodle_usuario
            token, error_login = autenticar(usuario, password, moodle_base)

            if not token:

                borrar_login_pendiente(
                    user_id
                )

                await update.message.reply_text(
                    "❌ Moodle no aceptó el usuario o la contraseña.\n\n"
                    "Puedes volver a intentar con /vincular_moodle."
                )

                return True

            establecer_usuario_telegram(
                user_id
            )

            if not guardar_token_moodle_usuario(
                user_id,
                token
            ):

                await update.message.reply_text(
                    "❌ No pude guardar la vinculación de Moodle."
                )

                borrar_login_pendiente(
                    user_id
                )

                return True

            info = moodle_api(
                "core_webservice_get_site_info"
            )

            if (
                not isinstance(
                    info,
                    dict
                )
                or "userid" not in info
            ):

                eliminar_cuenta_moodle_usuario(
                    user_id
                )

                borrar_login_pendiente(
                    user_id
                )

                await update.message.reply_text(
                    "❌ El acceso fue rechazado por Moodle.\n\n"
                    "Verifica tus datos e inténtalo nuevamente."
                )

                return True

            # Guardar nombre real de Moodle.
            actualizar_nombre_desde_moodle(
                user_id,
                info
            )

            try:
                from membresias import crear_o_actualizar_usuario

                crear_o_actualizar_usuario(
                    user_id,
                    info.get(
                        "firstname",
                        ""
                    ),
                    info.get(
                        "lastname",
                        ""
                    ),
                    update.effective_user.username or ""
                )
            except Exception as error:
                print(
                    "⚠️ No pude sincronizar membresía:",
                    error
                )

            borrar_login_pendiente(
                user_id
            )

            nombre = (
                info.get(
                    "fullname"
                )
                or (
                    str(info.get("firstname", ""))
                    + " "
                    + str(info.get("lastname", ""))
                ).strip()
                or "usuario"
            )

            await update.message.reply_text(
                "✅ <b>¡Bienvenido, "
                + str(nombre)
                + "!</b>\n\n"
                "🎓 Tu Moodle quedó vinculado correctamente.\n\n"
                "Ahora ya puedes consultar tus cursos, "
                "tareas y entregas.\n\n"
                + obtener_mensaje_bienvenida(
                    str(nombre)
                ),
                parse_mode="HTML",
            )

        except Exception as error:

            print(
                "⚠️ Error en vinculación automática de Moodle:",
                error
            )

            borrar_login_pendiente(
                user_id
            )

            await update.message.reply_text(
                "❌ No pude completar la vinculación.\n\n"
                "Comprueba tus datos e inténtalo nuevamente."
            )

        return True

    borrar_login_pendiente(
        user_id
    )

    return False


async def comando_cancelar_moodle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    user_id = update.effective_user.id

    if usuario_bloqueado(
        user_id
    ):
        return

    borrar_login_pendiente(
        user_id
    )

    await update.message.reply_text(
        "🛑 Vinculación de Moodle cancelada."
    )



async def comando_vencimientos(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    if not usuario_admin(update.effective_user.id):
        return

    try:
        from membresias import obtener_alertas_vencimiento

        alertas = obtener_alertas_vencimiento(
            dias_max=7
        )

        if not alertas:
            await update.message.reply_text(
                "🟢 No hay membresías vencidas ni por vencer en los próximos 7 días."
            )
            return

        lineas = [
            "🔔 <b>VENCIMIENTOS PRÓXIMOS</b>",
            ""
        ]

        for item in alertas:
            nombre = item["nombre"] or "Sin nombre"
            dias = item["dias"]

            if dias < 0:
                estado = "🔴 VENCIDA"
                detalle = f"Venció hace {abs(dias)} día(s)"
            elif dias == 0:
                estado = "🔴 VENCE HOY"
                detalle = "Vence hoy"
            elif dias == 1:
                estado = "🟠 VENCE MAÑANA"
                detalle = "Vence mañana"
            else:
                estado = "🟡 PRÓXIMA"
                detalle = f"Vence en {dias} día(s)"

            lineas.append(
                f"👤 <b>{nombre}</b>\n"
                f"🆔 {item['telegram_user_id']}\n"
                f"{estado} — {detalle}\n"
                f"📅 {item['fecha_vencimiento']}"
            )
            lineas.append("")

        await update.message.reply_text(
            "\n".join(lineas),
            parse_mode="HTML",
        )

    except Exception as error:
        print(
            "⚠️ Error obteniendo vencimientos:",
            error
        )
        await update.message.reply_text(
            "❌ No pude consultar los vencimientos."
        )


async def comando_reactivar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Permite al usuario reactivar su acceso por sí mismo
    únicamente mientras su membresía siga vigente.

    Si ya venció, la reactivación queda exclusivamente
    en manos del administrador.
    """
    if not update.effective_user or not update.message:
        return

    user_id=update.effective_user.id

    if usuario_admin(user_id):
        await update.message.reply_text(
            "👑 Tu cuenta de administrador ya está activa."
        )
        return

    bloqueados=cargar_ids_bloqueados()

    if user_id not in bloqueados:
        if usuario_autorizado(user_id):
            await update.message.reply_text(
                "✅ Tu acceso ya está activo."
            )
        else:
            await update.message.reply_text(
                "🔒 Tu cuenta no está activa.\n"
                "Necesitas autorización del administrador."
            )
        return

    # La única reactivación autónoma permitida es con membresía vigente.
    if not membresia_aun_vigente(user_id):
        await update.message.reply_text(
            "🔴 <b>MEMBRESÍA VENCIDA</b>\n\n"
            "Tu tiempo contratado ya terminó.\n"
            "🔐 No puedes reactivarte por tu cuenta.\n\n"
            "Solicita al administrador que te autorice nuevamente.",
            parse_mode="HTML",
        )
        return

    usuarios=cargar_usuarios_autorizados()
    registro=usuarios.get(
        user_id
    )

    if registro is None:
        await update.message.reply_text(
            "🔒 No encontré tu ficha de usuario.\n"
            "Necesitas autorización del administrador."
        )
        return

    bloqueados.discard(
        user_id
    )

    usuarios[user_id]=registro

    guardar_usuarios_autorizados(
        usuarios
    )
    guardar_ids_bloqueados(
        bloqueados
    )

    await update.message.reply_text(
        "✅ <b>ACCESO REACTIVADO</b>\n\n"
        f"👤 {nombre_visible_usuario(registro)}\n"
        "Tu membresía todavía está vigente.\n"
        "📋 Tus datos e historial se conservaron.\n"
        "🎓 Tu Moodle sigue vinculado.\n\n"
        "Ya puedes usar KAIRA nuevamente.",
        parse_mode="HTML",
    )


async def comando_darme_de_baja(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id

    # Ya revocado/bloqueado: silencio.
    if usuario_bloqueado(
        user_id
    ):
        return

    if usuario_admin(
        user_id
    ):
        await update.message.reply_text(
            "⛔ El administrador principal no puede darse de baja desde este comando."
        )
        return

    if not usuario_autorizado(
        user_id
    ):
        await update.message.reply_text(
            "ℹ️ Tu cuenta no está actualmente autorizada."
        )
        return

    marcar_baja_pendiente(
        user_id
    )

    await update.message.reply_text(
        "⚠️ <b>¿Seguro que quieres darte de baja?</b>\n\n"
        "Esto desactivará tu acceso a KAIRA, "
        "pero NO borrará tu cuenta.\n\n"
        "🎓 Tu Moodle seguirá vinculado.\n"
        "💳 Tu membresía conservará su fecha de vencimiento.\n"
        "📋 Tu historial seguirá guardado.\n\n"
        "Para confirmar escribe:\n"
        "<code>/confirmar_baja</code>\n\n"
        "Para cancelar:\n"
        "<code>/cancelar_baja</code>",
        parse_mode="HTML",
    )


async def comando_confirmar_baja(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not update.message:
        return

    user_id=update.effective_user.id

    if usuario_admin(user_id):
        await update.message.reply_text(
            "⛔ El administrador principal no puede darse de baja."
        )
        return

    if usuario_bloqueado(user_id):
        return

    if not tiene_baja_pendiente(user_id):
        await update.message.reply_text(
            "ℹ️ No tienes una baja pendiente."
        )
        return

    usuarios=cargar_usuarios_autorizados()
    registro=usuarios.get(
        user_id
    )

    if registro is None:
        quitar_baja_pendiente(user_id)
        await update.message.reply_text(
            "❌ No encontré tu ficha de usuario."
        )
        return

    nombre=nombre_visible_usuario(
        registro
    )

    # Mantener SIEMPRE la ficha autorizada.
    guardar_usuarios_autorizados(
        usuarios
    )

    # Bloquear solamente el acceso.
    bloqueados=cargar_ids_bloqueados()
    bloqueados.add(
        user_id
    )
    guardar_ids_bloqueados(
        bloqueados
    )

    # Detener temporalmente el resumen, pero NO borrar su configuración.
    try:
        if context.job_queue is not None:
            nombre_job=_nombre_job_resumen(
                user_id
            )

            for job in context.job_queue.get_jobs_by_name(
                nombre_job
            ):
                job.schedule_removal()
    except Exception as error:
        print(
            "⚠️ No pude detener el resumen por baja:",
            error
        )

    # NO desvincular Moodle.
    # NO borrar membresía.
    # NO cambiar fecha de vencimiento.
    # NO eliminar historial.

    vigente=membresia_aun_vigente(
        user_id
    )

    quitar_baja_pendiente(
        user_id
    )

    if vigente:
        mensaje_retorno=(
            "🔄 Mientras tu membresía siga vigente puedes "
            "usar <code>/reactivar</code> para volver a activar tu acceso."
        )
    else:
        mensaje_retorno=(
            "👑 Tu membresía ya venció. Para volver a utilizar KAIRA "
            "necesitarás autorización del administrador."
        )

    await update.message.reply_text(
        "✅ <b>ACCESO CANCELADO</b>\n\n"
        f"👤 {nombre}\n"
        f"🆔 <code>{user_id}</code>\n\n"
        "🔒 Tu acceso quedó desactivado.\n"
        "📋 Tu ficha e historial permanecen guardados.\n"
        "🎓 Tu vinculación de Moodle permanece guardada.\n"
        + mensaje_retorno,
        parse_mode="HTML",
    )




async def comando_cancelar_baja(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id

    if usuario_bloqueado(
        user_id
    ):
        return

    quitar_baja_pendiente(
        user_id
    )

    await update.message.reply_text(
        "✅ Baja cancelada. Tu acceso a KAIRA continúa activo."
    )



async def comando_precios(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    if not usuario_admin(
        update.effective_user.id
    ):
        return

    try:
        from membresias import (
            listar_precios_planes,
        )

        precios = listar_precios_planes()

        lineas = [
            "💰 <b>PRECIOS DE MEMBRESÍAS</b>",
            "",
            "🎁 Prueba gratuita: <b>5 días · GRATIS</b>",
            "",
            f"📅 Semanal: <b>${precios.get('Semanal', 0):.2f}</b>",
            f"📆 Mensual: <b>${precios.get('Mensual', 0):.2f}</b>",
            f"📊 Trimestral: <b>${precios.get('Trimestral', 0):.2f}</b>",
            f"🗓️ Semestral: <b>${precios.get('Semestral', 0):.2f}</b>",
            f"📚 Anual: <b>${precios.get('Anual', 0):.2f}</b>",
            "",
            "Para cambiar uno:",
            "<code>/precio Mensual 100</code>",
            "<code>/precio Semanal 30</code>",
            "",
            "El precio configurado para cada plan se aplicará "
            "automáticamente a las nuevas membresías y renovaciones.",
        ]

        await update.message.reply_text(
            "\n".join(lineas),
            parse_mode="HTML",
        )

    except Exception as error:
        print(
            "⚠️ Error mostrando precios:",
            error
        )

        await update.message.reply_text(
            "❌ No pude consultar los precios."
        )


async def comando_precio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    if not usuario_admin(
        update.effective_user.id
    ):
        return

    args = context.args or []

    if len(args) < 2:

        await update.message.reply_text(
            "Uso:\n"
            "/precio PLAN PRECIO\n\n"
            "Ejemplos:\n"
            "/precio Mensual 100\n"
            "/precio Semanal 30\n"
            "/precio Trimestral 250\n\n"
            "Planes válidos:\n"
            "Semanal, Mensual, Trimestral, Semestral, Anual"
        )

        return

    # Permitir precio con coma decimal.
    plan = args[0].strip().lower()

    equivalencias = {
        "semanal": "Semanal",
        "mensual": "Mensual",
        "trimestral": "Trimestral",
        "semestral": "Semestral",
        "anual": "Anual",
    }

    plan_real = equivalencias.get(
        plan
    )

    if not plan_real:
        await update.message.reply_text(
            "⚠️ Plan no válido.\n\n"
            "Usa: Semanal, Mensual, Trimestral, "
            "Semestral o Anual."
        )
        return

    try:
        precio = float(
            args[1].replace(
                ",",
                "."
            )
        )

        if precio < 0:
            raise ValueError

    except Exception:
        await update.message.reply_text(
            "⚠️ El precio debe ser un número positivo.\n\n"
            "Ejemplo: /precio Mensual 100"
        )
        return

    try:
        from membresias import (
            establecer_precio_plan,
        )

        precios = establecer_precio_plan(
            plan_real,
            precio
        )

        await update.message.reply_text(
            "✅ <b>Precio actualizado</b>\n\n"
            f"📦 Plan: <b>{plan_real}</b>\n"
            f"💰 Precio: <b>${precio:.2f}</b>\n\n"
            "Este precio se aplicará automáticamente "
            "a nuevas altas y renovaciones de ese plan.",
            parse_mode="HTML",
        )

    except Exception as error:
        print(
            "⚠️ Error cambiando precio:",
            error
        )

        await update.message.reply_text(
            "❌ No pude actualizar el precio."
        )



# =========================================================
# 👤 AUTORIZACIÓN + PLAN DE MEMBRESÍA DESDE TELEGRAM
# =========================================================

ARCHIVO_AUTORIZACIONES_PENDIENTES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "autorizaciones_pendientes.json",
)


PLANES_AUTORIZACION = (
    "Semanal",
    "Mensual",
    "Trimestral",
    "Semestral",
    "Anual",
)


def cargar_autorizaciones_pendientes():
    datos = _cargar_json(
        ARCHIVO_AUTORIZACIONES_PENDIENTES,
        {}
    )

    return datos if isinstance(datos, dict) else {}


def guardar_autorizaciones_pendientes(
    datos
):
    return _guardar_json(
        ARCHIVO_AUTORIZACIONES_PENDIENTES,
        datos
    )


def guardar_autorizacion_pendiente(
    admin_id,
    usuario_id,
    nombre="",
):
    datos = cargar_autorizaciones_pendientes()

    datos[str(int(admin_id))] = {
        "admin_id": int(admin_id),
        "usuario_id": int(usuario_id),
        "nombre": nombre,
        "creado_en": datetime.now().isoformat(),
    }

    return guardar_autorizaciones_pendientes(
        datos
    )


def obtener_autorizacion_pendiente(
    admin_id
):
    return (
        cargar_autorizaciones_pendientes()
        .get(
            str(int(admin_id))
        )
    )


def quitar_autorizacion_pendiente(
    admin_id
):
    datos = cargar_autorizaciones_pendientes()

    datos.pop(
        str(int(admin_id)),
        None
    )

    return guardar_autorizaciones_pendientes(
        datos
    )


async def procesar_seleccion_plan_autorizacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    texto,
):
    """Completa la autorización pendiente del administrador.

    AUTORIZAR no concede acceso. El acceso se activa únicamente después
    de elegir Prueba o un plan pagado.
    """
    if not update.effective_user or not update.message:
        return False
    admin_id = int(update.effective_user.id)
    if not usuario_admin(admin_id):
        return False
    pendiente = obtener_autorizacion_pendiente(admin_id)
    if not pendiente:
        return False
    texto_limpio = str(texto or "").strip().lower()
    equivalencias = {
        "prueba": "Prueba", "prueba gratis": "Prueba",
        "prueba gratuita": "Prueba", "gratis": "Prueba",
        "gratuita": "Prueba", "5 dias": "Prueba", "5 días": "Prueba",
        "semanal": "Semanal", "semana": "Semanal",
        "mensual": "Mensual", "mes": "Mensual",
        "trimestral": "Trimestral", "trimestre": "Trimestral",
        "semestral": "Semestral", "semestre": "Semestral",
        "anual": "Anual", "año": "Anual", "ano": "Anual",
    }
    plan = equivalencias.get(texto_limpio)
    if not plan:
        await update.message.reply_text(
            "⚠️ No reconocí esa opción.\n\n"
            "Responde con una de estas opciones:\n"
            "🎁 Prueba\n📅 Semanal\n📆 Mensual\n"
            "📊 Trimestral\n🗓️ Semestral\n📚 Anual"
        )
        return True
    usuario_id = int(pendiente["usuario_id"])
    if usuario_id == _id_admin():
        quitar_autorizacion_pendiente(admin_id)
        await update.message.reply_text("⛔ Ese ID es el administrador principal.")
        return True
    usuarios = cargar_usuarios_autorizados()
    bloqueados = cargar_ids_bloqueados()
    registro = usuarios.get(usuario_id, {
        "telegram_user_id": usuario_id,
        "first_name": pendiente.get("nombre", ""),
        "last_name": "", "username": "", "autorizado_en": "",
        "ultimo_acceso": "", "prueba_usada": False,
    })
    try:
        from membresias import (
            registrar_membresia, listar_precios_planes, obtener_usuario,
            fecha_hora_actual_local, fecha_iso_local,
            calcular_fecha_vencimiento,
        )
        ahora = fecha_hora_actual_local()
        inicio = fecha_iso_local(ahora)
        if plan == "Prueba":
            mem_existente = obtener_usuario(usuario_id)
            prueba_usada = bool(registro.get("prueba_usada", False))
            if mem_existente and int(mem_existente.get("prueba_usada", 0) or 0) == 1:
                prueba_usada = True
            if prueba_usada:
                await update.message.reply_text(
                    "❌ Ese usuario ya utilizó su prueba gratuita de 5 días.\n"
                    "Debes seleccionar un plan de pago."
                )
                return True
            vencimiento = fecha_iso_local(ahora + timedelta(days=5))
            precio = 0.0
            registrar_membresia(
                usuario_id, registro.get("first_name", pendiente.get("nombre", "")),
                registro.get("last_name", ""), inicio, vencimiento, precio, 1,
                "Prueba gratuita", "Prueba gratuita de 5 días", "activa", "Prueba"
            )
            registro["prueba_usada"] = True
        else:
            precios = listar_precios_planes()
            precio = float(precios.get(plan, 0) or 0)
            vencimiento = calcular_fecha_vencimiento(inicio, plan)
            registrar_membresia(
                usuario_id, registro.get("first_name", pendiente.get("nombre", "")),
                registro.get("last_name", ""), inicio, vencimiento, precio, 1,
                "Autorizado por administrador", "Membresía activada por administrador",
                "activa", plan
            )
            registro["prueba_usada"] = True
        registro["telegram_user_id"] = usuario_id
        registro["autorizado_en"] = datetime.now().isoformat()
        usuarios[usuario_id] = registro
        guardar_usuarios_autorizados(usuarios)
        bloqueados.discard(usuario_id)
        guardar_ids_bloqueados(bloqueados)
        quitar_autorizacion_pendiente(admin_id)
        quitar_solicitud_acceso(usuario_id)
        mem = obtener_usuario(usuario_id)
        fecha_vencimiento = mem.get("fecha_vencimiento", vencimiento) if mem else vencimiento
        nombre = nombre_visible_usuario(registro)
        await update.message.reply_text(
            "✅ <b>ACCESO ACTIVADO</b>\n\n"
            f"👤 {html.escape(nombre)}\n"
            f"🆔 <code>{usuario_id}</code>\n"
            f"📦 Plan: <b>{html.escape(plan)}</b>\n"
            f"💰 Precio: <b>${float(precio):.2f}</b>\n"
            f"⏳ Vence: <b>{html.escape(str(fecha_vencimiento))}</b>",
            parse_mode="HTML",
        )
        try:
            await context.bot.send_message(
                chat_id=usuario_id,
                text=(
                    "🤖 <b>KAIRA</b>\n\n"
                    f"Hola {html.escape(nombre)}.\n"
                    "Tu acceso ya está activo.\n\n"
                    f"🎫 Plan: <b>{html.escape(plan)}</b>\n"
                    f"⏳ Válida hasta: <b>{html.escape(str(fecha_vencimiento))}</b>\n\n"
                    "Selecciona una opción:"
                ),
                parse_mode="HTML",
                reply_markup=teclado_menu_usuario(usuario_id),
            )
        except Exception as error:
            print("⚠️ No pude enviar menú automático al usuario:", error)
        return True
    except Exception as error:
        print("⚠️ Error activando acceso/membresía:", error)
        await update.message.reply_text(
            "❌ No pude activar la membresía.\n"
            "El usuario NO fue autorizado.\n"
            "Revisa los registros de KAIRA antes de volver a intentarlo."
        )
        return True


async def comando_cancelar_autorizacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    if not usuario_admin(
        update.effective_user.id
    ):
        return

    quitar_autorizacion_pendiente(
        update.effective_user.id
    )

    if update.message:
        await update.message.reply_text(
            "🛑 Registro de usuario cancelado."
        )



# =========================================================
# 📩 SOLICITUD DE ACCESO
# =========================================================

async def solicitar_acceso_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query or not query.from_user:
        return

    await query.answer()

    user = query.from_user
    user_id = user.id

    if usuario_admin(
        user_id
    ):
        try:
            await query.edit_message_text(
                "👑 Ya tienes acceso de administrador."
            )
        except Exception:
            pass
        return

    if usuario_bloqueado(
        user_id
    ):
        try:
            await query.edit_message_text(
                "🔒 Tu acceso está bloqueado.\n\n"
                "Solicita al administrador que revise tu acceso."
            )
        except Exception:
            pass
        return

    if usuario_autorizado(
        user_id
    ):
        try:
            await query.edit_message_text(
                "✅ Ya tienes acceso a KAIRA.\n\n"
                "Escribe /start para comenzar."
            )
        except Exception:
            pass
        return

    if solicitud_acceso_pendiente(
        user_id
    ):
        try:
            await query.edit_message_text(
                "⏳ Tu solicitud ya fue enviada.\n\n"
                "Espera a que el administrador la revise."
            )
        except Exception:
            pass
        return

    registrar_solicitud_acceso(
        user
    )

    nombre = (
        (
            user.first_name or ""
        ).strip()
        + " "
        + (
            user.last_name or ""
        ).strip()
    ).strip()

    if not nombre:
        nombre = (
            "@"
            + (
                user.username or "sin_username"
            )
        )

    admin_id = _id_admin()

    if not admin_id:
        try:
            await query.edit_message_text(
                "⚠️ No se encontró el administrador de KAIRA.\n"
                "Inténtalo nuevamente más tarde."
            )
        except Exception:
            pass
        return

    teclado_admin = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ AUTORIZAR",
                    callback_data=(
                        f"solicitud_autorizar:{user_id}"
                    ),
                ),
                InlineKeyboardButton(
                    "❌ RECHAZAR",
                    callback_data=(
                        f"solicitud_rechazar:{user_id}"
                    ),
                ),
            ]
        ]
    )

    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                "🔔 <b>NUEVA SOLICITUD DE ACCESO</b>\n\n"
                f"👤 <b>{nombre}</b>\n"
                f"🆔 Telegram ID: <code>{user_id}</code>\n"
                f"👤 Username: "
                f"<code>@{user.username}</code>\n"
                if user.username
                else
                "🔔 <b>NUEVA SOLICITUD DE ACCESO</b>\n\n"
                f"👤 <b>{nombre}</b>\n"
                f"🆔 Telegram ID: <code>{user_id}</code>\n"
                "👤 Username: <code>sin username</code>\n"
            ),
            parse_mode="HTML",
            reply_markup=teclado_admin,
        )

        await query.edit_message_text(
            "✅ <b>Solicitud enviada.</b>\n\n"
            "El administrador recibió tu solicitud.\n"
            "Espera a que autorice tu acceso.",
            parse_mode="HTML",
        )

    except Exception as error:

        print(
            "⚠️ No pude enviar solicitud al administrador:",
            error
        )

        quitar_solicitud_acceso(
            user_id
        )

        try:
            await query.edit_message_text(
                "❌ No pude enviar tu solicitud.\n"
                "Inténtalo nuevamente."
            )
        except Exception:
            pass


async def admin_decision_acceso_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Atiende AUTORIZAR/DENEGAR. AUTORIZAR solo deja pendiente el plan."""
    query = update.callback_query
    if not query or not query.from_user:
        return
    if not usuario_admin(query.from_user.id):
        await query.answer("No tienes permiso.", show_alert=True)
        return
    await query.answer()
    data = query.data or ""
    try:
        accion, user_id_text = data.split(":", 1)
        user_id = int(user_id_text)
    except Exception:
        await query.edit_message_text("❌ Solicitud inválida.")
        return
    solicitud = cargar_solicitudes_acceso().get(str(user_id))
    if not solicitud:
        await query.edit_message_text("ℹ️ Esta solicitud ya fue atendida.")
        return
    nombre = " ".join(
        x for x in (solicitud.get("first_name", ""), solicitud.get("last_name", "")) if x
    ).strip() or "@usuario"
    if accion == "solicitud_rechazar":
        quitar_solicitud_acceso(user_id)
        quitar_autorizacion_pendiente(query.from_user.id)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ <b>Solicitud no aprobada.</b>\n\n"
                    "Tu solicitud de acceso a KAIRA fue rechazada.\n"
                    "Puedes solicitar acceso nuevamente más adelante."
                ),
                parse_mode="HTML",
            )
        except Exception as error:
            print("⚠️ No pude avisar rechazo:", error)
        await query.edit_message_text(
            "❌ <b>SOLICITUD RECHAZADA</b>\n\n"
            f"👤 {html.escape(nombre)}\n"
            f"🆔 <code>{user_id}</code>",
            parse_mode="HTML",
        )
        return
    if accion != "solicitud_autorizar":
        await query.edit_message_text("❌ Acción no reconocida.")
        return
    # IMPORTANTE: NO agregar al usuario autorizado todavía.
    guardar_autorizacion_pendiente(query.from_user.id, user_id, nombre)
    try:
        from membresias import listar_precios_planes
        precios = listar_precios_planes()
    except Exception:
        precios = {}
    await query.edit_message_text(
        "🟡 <b>AUTORIZACIÓN PENDIENTE DE PLAN</b>\n\n"
        f"👤 {html.escape(nombre)}\n"
        f"🆔 <code>{user_id}</code>\n\n"
        "La autorización fue aceptada, pero <b>todavía NO tiene acceso</b>.\n"
        "Ahora selecciona qué activar respondiendo en este chat:\n\n"
        "🎁 <b>Prueba</b> — 5 días · GRATIS\n"
        f"📅 <b>Semanal</b> — ${float(precios.get('Semanal', 0) or 0):.2f}\n"
        f"📆 <b>Mensual</b> — ${float(precios.get('Mensual', 0) or 0):.2f}\n"
        f"📊 <b>Trimestral</b> — ${float(precios.get('Trimestral', 0) or 0):.2f}\n"
        f"🗓️ <b>Semestral</b> — ${float(precios.get('Semestral', 0) or 0):.2f}\n"
        f"📚 <b>Anual</b> — ${float(precios.get('Anual', 0) or 0):.2f}\n\n"
        "Ejemplo: <code>Prueba</code> o <code>Mensual</code>",
        parse_mode="HTML",
    )
    quitar_solicitud_acceso(user_id)


async def comando_solicitar_acceso(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not update.message:
        return

    if usuario_admin(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "👑 Ya tienes acceso de administrador."
        )
        return

    if usuario_bloqueado(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "🔒 Tu acceso está bloqueado.\n"
            "Contacta al administrador."
        )
        return

    teclado = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "📩 SOLICITAR ACCESO",
                callback_data="solicitar_acceso",
            )
        ]]
    )

    await update.message.reply_text(
        "🔒 <b>ACCESO PRIVADO</b>\n\n"
        "Pulsa el botón para enviar tu solicitud.",
        parse_mode="HTML",
        reply_markup=teclado,
    )



# =========================================================
# 💳 SOLICITUDES DE RENOVACIÓN
# =========================================================

ARCHIVO_SOLICITUDES_CONTRATACION = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "solicitudes_contratacion_pendientes.json",
)


def cargar_solicitudes_contratacion():
    datos = _cargar_json(ARCHIVO_SOLICITUDES_CONTRATACION, {})
    return datos if isinstance(datos, dict) else {}


def guardar_solicitudes_contratacion(datos):
    return _guardar_json(ARCHIVO_SOLICITUDES_CONTRATACION, datos)


def registrar_solicitud_contratacion(user):
    datos = cargar_solicitudes_contratacion()
    datos[str(int(user.id))] = {
        "telegram_user_id": int(user.id),
        "first_name": (user.first_name or "").strip(),
        "last_name": (user.last_name or "").strip(),
        "username": (user.username or "").strip(),
        "creado_en": datetime.now().isoformat(),
    }
    return guardar_solicitudes_contratacion(datos)


def obtener_solicitud_contratacion(user_id):
    return cargar_solicitudes_contratacion().get(str(int(user_id)))


def quitar_solicitud_contratacion(user_id):
    datos = cargar_solicitudes_contratacion()
    datos.pop(str(int(user_id)), None)
    return guardar_solicitudes_contratacion(datos)


async def mostrar_pantalla_membresia_bloqueada(update):
    """Pantalla única para usuarios sin acceso por vencimiento."""
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 CONTRATAR MEMBRESÍA", callback_data="contratar_membresia")],
        [InlineKeyboardButton("📋 VER PLANES Y PRECIOS", callback_data="ver_planes_publicos")],
    ])
    mensaje = (
        "🔒 <b>TU ACCESO A KAIRA HA TERMINADO</b>\n\n"
        "Para continuar utilizando KAIRA, selecciona una opción:"
    )
    try:
        if update.callback_query and update.callback_query.message:
            await update.callback_query.edit_message_text(
                mensaje, parse_mode="HTML", reply_markup=teclado
            )
        elif update.message:
            await update.message.reply_text(
                mensaje, parse_mode="HTML", reply_markup=teclado
            )
    except Exception as error:
        print("⚠️ No pude mostrar pantalla de membresía:", error)


async def ver_planes_publicos_callback(update, context):
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    try:
        from membresias import listar_precios_planes
        precios = listar_precios_planes()
    except Exception:
        precios = {}
    texto = (
        "📋 <b>PLANES Y PRECIOS</b>\n\n"
        "🎁 Prueba gratuita — <b>5 días · GRATIS</b>\n\n"
        "📅 Semanal — <b>${:.2f}</b>\n"
        "📆 Mensual — <b>${:.2f}</b>\n"
        "📊 Trimestral — <b>${:.2f}</b>\n"
        "🗓️ Semestral — <b>${:.2f}</b>\n"
        "📚 Anual — <b>${:.2f}</b>\n\n"
        "Selecciona <b>CONTRATAR MEMBRESÍA</b> si deseas continuar."
    ).format(
        float(precios.get("Semanal", 0)),
        float(precios.get("Mensual", 0)),
        float(precios.get("Trimestral", 0)),
        float(precios.get("Semestral", 0)),
        float(precios.get("Anual", 0)),
    )
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 CONTRATAR MEMBRESÍA", callback_data="contratar_membresia")],
    ])
    await query.edit_message_text(texto, parse_mode="HTML", reply_markup=teclado)


async def contratar_membresia_callback(update, context):
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    user = query.from_user
    uid = int(user.id)
    if usuario_admin(uid):
        await query.edit_message_text("👑 El administrador no necesita contratar una membresía.")
        return

    pendiente = obtener_solicitud_contratacion(uid)
    if pendiente:
        await query.edit_message_text(
            "⏳ <b>SOLICITUD EN REVISIÓN</b>\n\n"
            "Tu solicitud ya fue enviada al administrador.\n"
            "Espera a que la revise.", parse_mode="HTML"
        )
        return

    registrar_solicitud_contratacion(user)
    admin_id = _id_admin()
    if not admin_id:
        quitar_solicitud_contratacion(uid)
        await query.edit_message_text("⚠️ No se encontró al administrador de KAIRA.")
        return

    nombre = " ".join(x for x in ((user.first_name or "").strip(), (user.last_name or "").strip()) if x).strip() or "@usuario"
    teclado_admin = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ AUTORIZAR", callback_data=f"contratacion_autorizar:{uid}"),
        InlineKeyboardButton("❌ DENEGAR", callback_data=f"contratacion_denegar:{uid}"),
    ]])
    texto_admin = (
        "🔔 <b>SOLICITUD DE MEMBRESÍA</b>\n\n"
        f"👤 <b>{html.escape(nombre)}</b>\n"
        f"🆔 Telegram ID: <code>{uid}</code>\n"
        f"👤 Username: <code>@{html.escape(user.username)}</code>" if user.username else
        "🔔 <b>SOLICITUD DE MEMBRESÍA</b>\n\n"
        f"👤 <b>{html.escape(nombre)}</b>\n"
        f"🆔 Telegram ID: <code>{uid}</code>\n"
        "👤 Username: <code>sin username</code>"
    )
    try:
        await context.bot.send_message(chat_id=admin_id, text=texto_admin, parse_mode="HTML", reply_markup=teclado_admin)
        await query.edit_message_text(
            "✅ <b>Solicitud enviada.</b>\n\n"
            "El administrador recibió tu solicitud.\n"
            "Espera a que la revise.", parse_mode="HTML"
        )
    except Exception as error:
        print("⚠️ No pude enviar solicitud de membresía:", error)
        quitar_solicitud_contratacion(uid)
        await query.edit_message_text("❌ No pude enviar la solicitud. Inténtalo nuevamente.")


async def admin_decision_contratacion_callback(update, context):
    query = update.callback_query
    if not query or not query.from_user:
        return
    if not usuario_admin(query.from_user.id):
        await query.answer("No tienes permiso.", show_alert=True)
        return
    await query.answer()
    try:
        accion, uid_text = (query.data or "").split(":", 1)
        uid = int(uid_text)
    except Exception:
        await query.edit_message_text("❌ Solicitud inválida.")
        return

    solicitud = obtener_solicitud_contratacion(uid)
    if not solicitud:
        await query.edit_message_text("ℹ️ Esta solicitud ya fue atendida.")
        return

    nombre = " ".join(x for x in (solicitud.get("first_name", ""), solicitud.get("last_name", "")) if x).strip() or "@usuario"

    if accion == "contratacion_denegar":
        quitar_solicitud_contratacion(uid)
        try:
            await context.bot.send_message(
                chat_id=uid,
                text="❌ <b>Solicitud no aprobada.</b>\n\nTu solicitud de membresía no fue autorizada.",
                parse_mode="HTML",
            )
        except Exception as error:
            print("⚠️ Aviso de denegación:", error)
        await query.edit_message_text(
            "❌ <b>SOLICITUD DENEGADA</b>\n\n"
            f"👤 {html.escape(nombre)}\n🆔 <code>{uid}</code>",
            parse_mode="HTML",
        )
        return

    try:
        from membresias import listar_precios_planes
        precios = listar_precios_planes()
    except Exception:
        precios = {}
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📅 Semanal ${precios.get('Semanal',0):.2f}", callback_data=f"contratacion_plan:Semanal:{uid}")],
        [InlineKeyboardButton(f"📆 Mensual ${precios.get('Mensual',0):.2f}", callback_data=f"contratacion_plan:Mensual:{uid}")],
        [InlineKeyboardButton(f"📊 Trimestral ${precios.get('Trimestral',0):.2f}", callback_data=f"contratacion_plan:Trimestral:{uid}")],
        [InlineKeyboardButton(f"🗓️ Semestral ${precios.get('Semestral',0):.2f}", callback_data=f"contratacion_plan:Semestral:{uid}")],
        [InlineKeyboardButton(f"📚 Anual ${precios.get('Anual',0):.2f}", callback_data=f"contratacion_plan:Anual:{uid}")],
    ])
    await query.edit_message_text(
        "✅ <b>SOLICITUD AUTORIZADA</b>\n\n"
        f"👤 {html.escape(nombre)}\n"
        f"🆔 <code>{uid}</code>\n\n"
        "Selecciona la membresía que vas a activar:",
        parse_mode="HTML", reply_markup=teclado
    )


async def procesar_plan_contratacion_callback(update, context):
    query = update.callback_query
    if not query or not query.from_user:
        return
    if not usuario_admin(query.from_user.id):
        await query.answer("No tienes permiso.", show_alert=True)
        return
    await query.answer()
    try:
        _, plan, uid_text = (query.data or "").split(":", 2)
        uid = int(uid_text)
    except Exception:
        await query.edit_message_text("❌ Selección inválida.")
        return
    if plan not in ("Semanal", "Mensual", "Trimestral", "Semestral", "Anual"):
        await query.edit_message_text("❌ Plan no válido.")
        return
    solicitud = obtener_solicitud_contratacion(uid)
    if not solicitud:
        await query.edit_message_text("ℹ️ La solicitud ya fue atendida.")
        return
    try:
        from membresias import registrar_membresia, listar_precios_planes
        precios = listar_precios_planes()
        precio = float(precios.get(plan, 0))
        inicio = datetime.now(ZoneInfo("America/Mexico_City")).isoformat(timespec="seconds")
        # Registrar/activar la membresía desde el momento de la autorización.
        registrar_membresia(
            uid, solicitud.get("first_name", ""), solicitud.get("last_name", ""),
            fecha_inicio=inicio, fecha_vencimiento="", precio=precio, pagado=1,
            metodo_pago="Autorizado por administrador", estado="activa", tipo_membresia=plan
        )
        usuarios = cargar_usuarios_autorizados()
        registro = usuarios.get(uid, {"telegram_user_id": uid})
        registro.update({
            "first_name": solicitud.get("first_name", ""),
            "last_name": solicitud.get("last_name", ""),
            "username": solicitud.get("username", ""),
            "prueba_usada": True,
            "autorizado_en": datetime.now().isoformat(),
        })
        usuarios[uid] = registro
        guardar_usuarios_autorizados(usuarios)
        bloqueados = cargar_ids_bloqueados()
        bloqueados.discard(uid)
        guardar_ids_bloqueados(bloqueados)
        mem = __import__("membresias").obtener_usuario(uid)
        fecha_vencimiento = mem.get("fecha_vencimiento", "") if mem else ""
        quitar_solicitud_contratacion(uid)
        await query.edit_message_text(
            "✅ <b>MEMBRESÍA ACTIVADA</b>\n\n"
            f"👤 {html.escape(solicitud.get('first_name','Usuario'))}\n"
            f"🆔 <code>{uid}</code>\n"
            f"📦 Plan: <b>{plan}</b>\n"
            f"💰 Precio: <b>${precio:.2f}</b>\n"
            f"⏳ Vence: <b>{fecha_vencimiento}</b>",
            parse_mode="HTML"
        )
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "✅ <b>MEMBRESÍA ACTIVADA</b>\n\n"
                    f"📦 Plan: <b>{plan}</b>\n"
                    f"💰 Precio: <b>${precio:.2f}</b>\n"
                    f"⏳ Válida hasta: <b>{fecha_vencimiento}</b>\n\n"
                    "🟢 Ya puedes utilizar KAIRA nuevamente."
                ), parse_mode="HTML"
            )
        except Exception as error:
            print("⚠️ No pude avisar activación:", error)
    except Exception as error:
        print("⚠️ Error activando contratación:", error)
        await query.edit_message_text("❌ No pude activar la membresía. Revisa los registros de KAIRA.")


ARCHIVO_RENOVACIONES_PENDIENTES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "renovaciones_pendientes.json",
)


def cargar_renovaciones_pendientes():
    datos = _cargar_json(
        ARCHIVO_RENOVACIONES_PENDIENTES,
        {}
    )
    return datos if isinstance(datos, dict) else {}


def guardar_renovaciones_pendientes(datos):
    return _guardar_json(
        ARCHIVO_RENOVACIONES_PENDIENTES,
        datos
    )


def registrar_renovacion_pendiente(
    user
):
    datos = cargar_renovaciones_pendientes()

    datos[str(int(user.id))] = {
        "telegram_user_id": int(user.id),
        "first_name": (
            user.first_name or ""
        ).strip(),
        "last_name": (
            user.last_name or ""
        ).strip(),
        "username": (
            user.username or ""
        ).strip(),
        "creado_en": datetime.now().isoformat(),
    }

    return guardar_renovaciones_pendientes(
        datos
    )


def quitar_renovacion_pendiente(
    user_id
):
    datos = cargar_renovaciones_pendientes()

    datos.pop(
        str(int(user_id)),
        None
    )

    return guardar_renovaciones_pendientes(
        datos
    )


def tiene_renovacion_pendiente(
    user_id
):
    return str(
        int(user_id)
    ) in cargar_renovaciones_pendientes()


def obtener_renovacion_pendiente(
    user_id
):
    return cargar_renovaciones_pendientes().get(
        str(int(user_id))
    )


async def comando_renovar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    El usuario solicita una renovación.
    Nunca modifica la membresía por sí solo.
    """

    if not update.effective_user or not update.message:
        return

    user = update.effective_user
    user_id = user.id

    if usuario_admin(
        user_id
    ):
        await update.message.reply_text(
            "👑 La cuenta del administrador no utiliza renovación de membresía."
        )
        return

    if usuario_bloqueado(
        user_id
    ):
        # Un usuario revocado/retirado no puede auto-reactivarse.
        await update.message.reply_text(
            "🔒 Tu acceso está bloqueado.\n\n"
            "Contacta al administrador para solicitar una nueva autorización."
        )
        return

    if not usuario_autorizado(
        user_id
    ):
        await update.message.reply_text(
            "🔒 Primero debes tener una membresía autorizada."
        )
        return

    if tiene_renovacion_pendiente(
        user_id
    ):
        await update.message.reply_text(
            "⏳ Ya tienes una solicitud de renovación pendiente.\n\n"
            "Espera a que el administrador la revise."
        )
        return

    registrar_renovacion_pendiente(
        user
    )

    nombre = (
        (
            user.first_name or ""
        ).strip()
        + " "
        + (
            user.last_name or ""
        ).strip()
    ).strip() or "@usuario"

    try:
        from membresias import obtener_usuario

        membresia = obtener_usuario(
            user_id
        )

    except Exception:
        membresia = None

    if membresia:
        plan_actual = (
            membresia.get(
                "tipo_membresia"
            )
            or "No definido"
        )

        fecha_actual = (
            membresia.get(
                "fecha_vencimiento"
            )
            or "Sin fecha"
        )

        precio_actual = (
            membresia.get(
                "precio"
            )
            or 0
        )

    else:
        plan_actual = "No definido"
        fecha_actual = "Sin fecha"
        precio_actual = 0

    admin_id = _id_admin()

    if not admin_id:
        quitar_renovacion_pendiente(
            user_id
        )

        await update.message.reply_text(
            "⚠️ No se encontró al administrador de KAIRA."
        )
        return

    teclado = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✅ APROBAR",
                callback_data=(
                    f"renovacion_aprobar:{user_id}"
                ),
            ),
            InlineKeyboardButton(
                "❌ RECHAZAR",
                callback_data=(
                    f"renovacion_rechazar:{user_id}"
                ),
            ),
        ]]
    )

    texto_admin = (
        "🔔 <b>SOLICITUD DE RENOVACIÓN</b>\n\n"
        f"👤 <b>{nombre}</b>\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n"
        f"📦 Plan actual: <b>{plan_actual}</b>\n"
        f"💰 Precio actual: <b>${float(precio_actual):.2f}</b>\n"
        f"⏳ Vencimiento: <b>{fecha_actual}</b>\n\n"
        "El usuario solicita renovar.\n"
        "<b>No se cambiará nada hasta que tú lo apruebes.</b>"
    )

    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=texto_admin,
            parse_mode="HTML",
            reply_markup=teclado,
        )

        await update.message.reply_text(
            "✅ <b>Solicitud enviada.</b>\n\n"
            "Tu renovación fue enviada al administrador.\n"
            "La membresía no se renovará automáticamente.",
            parse_mode="HTML",
        )

    except Exception as error:

        print(
            "⚠️ No pude enviar solicitud de renovación:",
            error
        )

        quitar_renovacion_pendiente(
            user_id
        )

        await update.message.reply_text(
            "❌ No pude enviar tu solicitud de renovación."
        )


async def procesar_renovacion_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query or not query.from_user:
        return

    if not usuario_admin(
        query.from_user.id
    ):
        await query.answer(
            "No tienes permiso.",
            show_alert=True,
        )
        return

    await query.answer()

    data = (
        query.data or ""
    )

    try:
        accion, user_id_text = data.split(
            ":",
            1
        )

        user_id = int(
            user_id_text
        )

    except Exception:

        await query.edit_message_text(
            "❌ Solicitud inválida."
        )
        return

    solicitud = obtener_renovacion_pendiente(
        user_id
    )

    if not solicitud:

        await query.edit_message_text(
            "ℹ️ Esta solicitud ya fue atendida."
        )
        return

    nombre = " ".join(
        x for x in (
            solicitud.get("first_name", ""),
            solicitud.get("last_name", ""),
        )
        if x
    ).strip() or "@usuario"

    if accion == "renovacion_rechazar":

        quitar_renovacion_pendiente(
            user_id
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ <b>Tu solicitud de renovación "
                    "no fue aprobada.</b>\n\n"
                    "Tu membresía no fue modificada."
                ),
                parse_mode="HTML",
            )
        except Exception as error:
            print(
                "⚠️ No pude avisar rechazo de renovación:",
                error
            )

        await query.edit_message_text(
            "❌ <b>RENOVACIÓN RECHAZADA</b>\n\n"
            f"👤 {nombre}\n"
            f"🆔 <code>{user_id}</code>\n\n"
            "La membresía no fue modificada.",
            parse_mode="HTML",
        )

        return

    if accion == "renovacion_aprobar":

        try:
            from membresias import (
                listar_precios_planes,
            )

            precios = listar_precios_planes()

        except Exception:
            precios = {}

        teclado = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"📅 Semanal ${precios.get('Semanal',0):.2f}",
                        callback_data=f"renovacion_plan:Semanal:{user_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"📆 Mensual ${precios.get('Mensual',0):.2f}",
                        callback_data=f"renovacion_plan:Mensual:{user_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"📊 Trimestral ${precios.get('Trimestral',0):.2f}",
                        callback_data=f"renovacion_plan:Trimestral:{user_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"🗓️ Semestral ${precios.get('Semestral',0):.2f}",
                        callback_data=f"renovacion_plan:Semestral:{user_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"📚 Anual ${precios.get('Anual',0):.2f}",
                        callback_data=f"renovacion_plan:Anual:{user_id}",
                    ),
                ],
            ]
        )

        await query.edit_message_text(
            "✅ <b>RENOVACIÓN APROBADA</b>\n\n"
            f"👤 {nombre}\n"
            f"🆔 <code>{user_id}</code>\n\n"
            "Ahora selecciona el nuevo plan.\n"
            "La fecha todavía no se ha cambiado.",
            parse_mode="HTML",
            reply_markup=teclado,
        )

        return


async def procesar_plan_renovacion_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query or not query.from_user:
        return

    if not usuario_admin(
        query.from_user.id
    ):
        await query.answer(
            "No tienes permiso.",
            show_alert=True,
        )
        return

    await query.answer()

    data = (
        query.data or ""
    )

    try:
        _, plan, user_id_text = data.split(
            ":",
            2
        )

        user_id = int(
            user_id_text
        )

    except Exception:

        await query.edit_message_text(
            "❌ Selección inválida."
        )
        return

    solicitud = obtener_renovacion_pendiente(
        user_id
    )

    if not solicitud:

        await query.edit_message_text(
            "ℹ️ La solicitud ya fue atendida."
        )
        return

    if plan not in (
        "Semanal",
        "Mensual",
        "Trimestral",
        "Semestral",
        "Anual",
    ):
        await query.edit_message_text(
            "❌ Plan no válido."
        )
        return

    # Un usuario eliminado/revocado no puede reactivarse
    # mediante una solicitud vieja.
    if usuario_admin(user_id):
        quitar_renovacion_pendiente(
            user_id
        )

        await query.edit_message_text(
            "⛔ Operación no permitida."
        )
        return

    try:
        from membresias import (
            obtener_usuario,
            renovar,
            listar_precios_planes,
        )

        actual = obtener_usuario(
            user_id
        )

        if not actual:
            await query.edit_message_text(
                "❌ No encontré el registro de membresía."
            )
            quitar_renovacion_pendiente(
                user_id
            )
            return

        precios = listar_precios_planes()
        precio = float(
            precios.get(
                plan,
                0
            )
        )

        # La renovación SIEMPRE empieza en el momento
        # de la aprobación del administrador.
        renovar(
            user_id,
            tipo_membresia=plan,
            precio=precio,
            metodo_pago="Aprobado por administrador",
        )

        # Reactivar acceso solo ahora.
        usuarios = cargar_usuarios_autorizados()
        bloqueados = cargar_ids_bloqueados()

        registro = usuarios.get(
            user_id
        )

        if registro is None:
            registro = {
                "telegram_user_id": user_id,
                "first_name": solicitud.get("first_name", ""),
                "last_name": solicitud.get("last_name", ""),
                "username": solicitud.get("username", ""),
                "telegram_first_name": solicitud.get("first_name", ""),
                "telegram_last_name": solicitud.get("last_name", ""),
                "autorizado_en": "",
                "ultimo_acceso": "",
            }

        registro["telegram_user_id"] = user_id
        registro["first_name"] = (
            solicitud.get(
                "first_name",
                ""
            )
        )
        registro["last_name"] = (
            solicitud.get(
                "last_name",
                ""
            )
        )
        registro["username"] = (
            solicitud.get(
                "username",
                ""
            )
        )

        usuarios[user_id] = registro
        bloqueados.discard(
            user_id
        )

        guardar_usuarios_autorizados(
            usuarios
        )
        guardar_ids_bloqueados(
            bloqueados
        )

        nueva = obtener_usuario(
            user_id
        )

        quitar_renovacion_pendiente(
            user_id
        )

        nombre = " ".join(
            x for x in (
                nueva.get("nombre", ""),
                nueva.get("apellido", ""),
            )
            if x
        ).strip() or solicitud.get(
            "first_name",
            "Usuario"
        )

        fecha_inicio = nueva.get(
            "fecha_inicio",
            ""
        )

        fecha_vencimiento = nueva.get(
            "fecha_vencimiento",
            ""
        )

        texto_ok = (
            "✅ <b>RENOVACIÓN REGISTRADA</b>\n\n"
            f"👤 {nombre}\n"
            f"🆔 <code>{user_id}</code>\n"
            f"📦 Plan: <b>{plan}</b>\n"
            f"💰 Precio: <b>${precio:.2f}</b>\n"
            f"📅 Inicio: <b>{fecha_inicio}</b>\n"
            f"⏳ Vence: <b>{fecha_vencimiento}</b>\n"
            "💳 Pago: <b>Autorizado</b>\n\n"
            "🟢 Acceso reactivado."
        )

        await query.edit_message_text(
            texto_ok,
            parse_mode="HTML",
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ <b>¡Tu renovación fue aprobada!</b>\n\n"
                    f"📦 Plan: <b>{plan}</b>\n"
                    f"💰 Precio: <b>${precio:.2f}</b>\n"
                    f"⏳ Válida hasta: <b>{fecha_vencimiento}</b>\n\n"
                    "🟢 Tu acceso a KAIRA está activo nuevamente."
                ),
                parse_mode="HTML",
            )

        except Exception as error:
            print(
                "⚠️ No pude notificar renovación al usuario:",
                error
            )

    except Exception as error:

        print(
            "⚠️ Error procesando renovación:",
            error
        )

        await query.edit_message_text(
            "❌ Ocurrió un error al registrar la renovación."
        )



async def abrir_renovacion_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query or not query.from_user:
        return

    await query.answer()

    # Convertir el botón en una solicitud real.
    await comando_renovar(
        update,
        context
    )



async def alertar_vencimientos_admin(
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Revisión cada hora:
    - 7 días: aviso preventivo al usuario + admin
    - 3 días: aviso al usuario + admin
    - 1 día: aviso al usuario + admin
    - vencido: aviso final + suspensión
    Cada evento se envía una sola vez por fecha de vencimiento.
    """

    admin_id=_id_admin()

    try:
        from membresias import (
            listar_usuarios,
            dias_para_vencer,
        )

        try:
            from membresias import limpiar_usuarios_inactivos
            eliminados = limpiar_usuarios_inactivos(30)
            if eliminados:
                autorizados = cargar_usuarios_autorizados()
                for uid_eliminado in eliminados:
                    autorizados.pop(int(uid_eliminado), None)
                    try:
                        from moodle_kaira import eliminar_cuenta_moodle_usuario
                        eliminar_cuenta_moodle_usuario(int(uid_eliminado))
                    except Exception as error_moodle:
                        print("⚠️ Limpieza Moodle:", error_moodle)
                guardar_usuarios_autorizados(autorizados)
        except Exception as error_limpieza:
            print("⚠️ Limpieza de usuarios inactivos:", error_limpieza)

        usuarios=listar_usuarios()
        alertas=cargar_alertas_membresia()

        for usuario in usuarios:
            try:
                uid=int(usuario.get("telegram_user_id"))
            except Exception:
                continue

            if uid == admin_id:
                continue

            fecha=usuario.get("fecha_vencimiento")
            if not fecha:
                continue

            dias=dias_para_vencer(fecha)
            if dias is None:
                continue

            clave=str(uid)
            registro=alertas.get(clave,{})
            fecha_reg=str(fecha)

            if registro.get("fecha_vencimiento") != fecha_reg:
                registro={
                    "fecha_vencimiento":fecha_reg,
                    "avisos":[],
                }

            avisos=registro.get("avisos",[])

            if dias == 2:
                codigo="2"
            elif dias == 1:
                codigo="1"
            elif dias == 0:
                codigo="0"
            else:
                codigo=None

            if codigo and codigo not in avisos:
                nombre=(
                    (usuario.get("nombre") or "")+" "+
                    (usuario.get("apellido") or "")
                ).strip() or "Usuario"

                if codigo == "2":
                    texto_usuario=(
                        "⚠️ <b>Tu acceso a KAIRA termina en 2 días.</b>\n\n"
                        f"⏳ Vencimiento: <b>{fecha}</b>\n\n"
                        "Si deseas continuar, puedes contratar una membresía."
                    )
                    texto_admin=(
                        "🟡 <b>MEMBRESÍA: 2 DÍAS</b>\n\n"
                        f"👤 {nombre}\n"
                        f"🆔 <code>{uid}</code>\n"
                        f"⏳ {fecha}"
                    )
                elif codigo == "1":
                    texto_usuario=(
                        "🔴 <b>Tu acceso a KAIRA termina mañana.</b>\n\n"
                        f"⏳ Vencimiento: <b>{fecha}</b>\n\n"
                        "Si deseas continuar, contrata una membresía."
                    )
                    texto_admin=(
                        "🔴 <b>MEMBRESÍA: 1 DÍA</b>\n\n"
                        f"👤 {nombre}\n"
                        f"🆔 <code>{uid}</code>\n"
                        f"⏳ {fecha}"
                    )
                else:
                    texto_usuario=(
                        "🔒 <b>Hoy termina tu acceso a KAIRA.</b>\n\n"
                        "A partir de ahora solo podrás ver planes y solicitar una membresía."
                    )
                    texto_admin=(
                        "🚨 <b>MEMBRESÍA VENCE HOY</b>\n\n"
                        f"👤 {nombre}\n"
                        f"🆔 <code>{uid}</code>\n"
                        f"⏳ {fecha}"
                    )

                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text=texto_usuario,
                        parse_mode="HTML",
                    )
                except Exception as error:
                    print("⚠️ Aviso usuario:",error)

                if admin_id:
                    try:
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=texto_admin,
                            parse_mode="HTML",
                        )
                    except Exception as error:
                        print("⚠️ Aviso admin:",error)

                avisos.append(codigo)

            # Al vencer se bloquea por estado de membresía, no por la lista
            # de revocados, para que el usuario siga viendo únicamente
            # CONTRATAR / PLANES.
            if dias < 0 and admin_id and "expirado" not in avisos:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=(
                            "🔒 <b>MEMBRESÍA VENCIDA</b>\n\n"
                            f"👤 {html.escape(nombre)}\n"
                            f"🆔 <code>{uid}</code>\n"
                            "El acceso quedó bloqueado hasta que se contrate una nueva membresía."
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                avisos.append("expirado")

            alertas[clave]=registro

        guardar_alertas_membresia(alertas)

    except Exception as error:
        print(
            "⚠️ Error alertando vencimientos:",
            error
        )



# =========================================================
# 👤 CUENTA DEL USUARIO
# =========================================================

async def comando_mi_cuenta(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await comprobar_acceso(update):
        return

    if not update.effective_user or not update.message:
        return

    user_id=update.effective_user.id

    try:
        from membresias import obtener_usuario
        cuenta=obtener_usuario(user_id)
    except Exception as error:
        print("⚠️ Error consultando mi cuenta:", error)
        cuenta=None

    nombre=(
        update.effective_user.full_name
        or "Usuario"
    )

    if not cuenta:
        await update.message.reply_text(
            "👤 <b>MI CUENTA</b>\n\n"
            f"Nombre: <b>{nombre}</b>\n"
            f"🆔 Telegram: <code>{user_id}</code>\n\n"
            "No tienes una membresía registrada.",
            parse_mode="HTML",
        )
        return

    moodle="❌ No vinculado"

    try:
        from moodle_kaira import obtener_configuracion_moodle_usuario
        if obtener_configuracion_moodle_usuario(user_id):
            moodle="✅ Vinculado"
    except Exception:
        pass

    await update.message.reply_text(
        "👤 <b>MI CUENTA</b>\n\n"
        f"Nombre: <b>{cuenta.get('nombre') or nombre}</b>\n"
        f"🆔 Telegram: <code>{user_id}</code>\n"
        f"🎓 Moodle: <b>{moodle}</b>\n\n"
        f"📦 Membresía: <b>{cuenta.get('tipo_membresia') or 'No definida'}</b>\n"
        f"💰 Precio: <b>${float(cuenta.get('precio') or 0):.2f}</b>\n"
        f"🗓️ Creado: <b>{cuenta.get('fecha_registro') or 'No disponible'}</b>\n"
        f"📅 Inicio: <b>{cuenta.get('fecha_inicio') or 'No disponible'}</b>\n"
        f"⏳ Vencimiento: <b>{cuenta.get('fecha_vencimiento') or 'No disponible'}</b>\n"
        f"💳 Pago: <b>{'Pagado' if int(cuenta.get('pagado',0) or 0) else 'No pagado'}</b>\n"
        f"🟢 Estado: <b>{cuenta.get('estado') or 'No definido'}</b>",
        parse_mode="HTML",
    )


# =========================================================
# 📋 HISTORIAL DE PAGOS DEL USUARIO
# =========================================================

async def comando_historial(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await comprobar_acceso(update):
        return

    if not update.effective_user or not update.message:
        return

    user_id=update.effective_user.id

    # El admin puede consultar otro ID; el usuario normal solo el suyo.
    args=context.args or []

    if usuario_admin(user_id) and args:
        try:
            target=int(args[0])
        except Exception:
            await update.message.reply_text(
                "⚠️ El ID debe ser numérico."
            )
            return
    else:
        target=user_id

    try:
        from membresias import historial_pagos, resumen_pagos

        pagos=historial_pagos(target,20)
        resumen=resumen_pagos(target)

    except Exception as error:
        print("⚠️ Error historial:",error)
        await update.message.reply_text(
            "❌ No pude consultar el historial de pagos."
        )
        return

    if not pagos:
        await update.message.reply_text(
            "📋 No hay pagos registrados para este usuario."
        )
        return

    lineas=[
        "💳 <b>HISTORIAL DE PAGOS</b>",
        f"🆔 <code>{target}</code>",
        "",
    ]

    for pago in reversed(pagos):
        lineas.append(
            f"📅 {pago['fecha_pago']}\n"
            f"📦 {pago['plan'] or 'Sin plan'}\n"
            f"💰 ${float(pago['precio'] or 0):.2f}\n"
            f"💵 {pago['metodo_pago'] or 'Sin método'}\n"
        )

    lineas.append(
        f"📊 Pagos: <b>{resumen['cantidad']}</b>\n"
        f"💰 Total: <b>${resumen['total']:.2f}</b>"
    )

    await update.message.reply_text(
        "\n".join(lineas),
        parse_mode="HTML",
    )


# =========================================================
# 👑 ADMIN: RESUMEN
# =========================================================

async def comando_resumen_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    if not usuario_admin(
        update.effective_user.id
    ):
        return

    try:
        from membresias import resumen_admin

        resumen=resumen_admin(
            excluir_telegram_id=update.effective_user.id
        )

        await update.message.reply_text(
            "📊 <b>KAIRA ADMIN</b>\n\n"
            f"👥 Usuarios: <b>{resumen['total']}</b>\n"
            f"🟢 Activos: <b>{resumen['activos']}</b>\n"
            f"🟡 Por vencer: <b>{resumen['por_vencer_7_dias']}</b>\n"
            f"🔴 Vencidos: <b>{resumen['vencidos']}</b>\n\n"
            f"💳 Pagados: <b>{resumen['pagados']}</b>\n"
            f"❌ Pendientes: <b>{resumen['pendientes']}</b>\n"
            f"💰 Ingresos registrados: <b>${resumen['ingresos']:.2f}</b>",
            parse_mode="HTML",
        )

    except Exception as error:
        print("⚠️ Error resumen admin:",error)
        await update.message.reply_text(
            "❌ No pude generar el resumen administrativo."
        )


# =========================================================
# 👑 ADMIN: BUSCAR USUARIO
# =========================================================

async def comando_buscar_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    if not usuario_admin(
        update.effective_user.id
    ):
        return

    args=context.args or []

    if not args:
        await update.message.reply_text(
            "Uso:\n"
            "/buscar Erick\n"
            "/buscar 5571546711"
        )
        return

    termino=" ".join(args).lower()

    try:
        from membresias import listar_usuarios
        usuarios=listar_usuarios()

    except Exception as error:
        print("⚠️ Error buscando usuarios:",error)
        await update.message.reply_text(
            "❌ No pude buscar usuarios."
        )
        return

    encontrados=[]

    for u in usuarios:
        texto=" ".join([
            str(u.get("telegram_user_id","")),
            str(u.get("nombre","")),
            str(u.get("apellido","")),
            str(u.get("username","")),
        ]).lower()

        if termino in texto:
            encontrados.append(u)

    if not encontrados:
        await update.message.reply_text(
            "🔎 No encontré usuarios que coincidan con:\n"
            f"<b>{termino}</b>",
            parse_mode="HTML",
        )
        return

    lineas=["🔎 <b>RESULTADOS</b>",""]

    for u in encontrados[:10]:
        lineas.append(
            f"👤 <b>{(u.get('nombre') or '')} {(u.get('apellido') or '')}</b>\n"
            f"🆔 <code>{u.get('telegram_user_id')}</code>\n"
            f"📦 {u.get('tipo_membresia') or 'Sin plan'}\n"
            f"💰 ${float(u.get('precio') or 0):.2f}\n"
            f"⏳ {u.get('fecha_vencimiento') or 'Sin fecha'}\n"
            f"💳 {'Pagado' if int(u.get('pagado',0) or 0) else 'No pagado'}\n"
            f"🔐 {u.get('estado') or 'Sin estado'}\n"
        )

    if len(encontrados)>10:
        lineas.append(
            f"… y {len(encontrados)-10} resultado(s) más."
        )

    await update.message.reply_text(
        "\n".join(lineas),
        parse_mode="HTML",
    )


def tiene_moodle_vinculado(telegram_user_id):
    try:
        from moodle_kaira import obtener_configuracion_moodle_usuario
        return bool(
            obtener_configuracion_moodle_usuario(int(telegram_user_id))
        )
    except Exception:
        return False


def preparar_usuario_moodle(telegram_user_id):
    """
    Fija el Telegram ID actual para que moodle_kaira utilice
    el token correcto de ese usuario.
    """
    try:
        from moodle_kaira import establecer_usuario_telegram
        establecer_usuario_telegram(int(telegram_user_id))
        return True
    except Exception as error:
        print("⚠️ No pude preparar usuario Moodle:", error)
        return False


async def editar_menu_callback(update, texto, teclado=None):
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                texto,
                parse_mode="HTML",
                reply_markup=teclado,
            )
        except Exception as error:
            # Telegram devuelve BadRequest cuando el mensaje y el teclado
            # son exactamente iguales. No es un fallo del bot; simplemente
            # no hay nada que actualizar.
            if "message is not modified" in str(error).lower() or "mensaje no se ha modificado" in str(error).lower():
                return
            raise
    elif update.message:
        await update.message.reply_text(
            texto,
            parse_mode="HTML",
            reply_markup=teclado,
        )


# =========================================================
# 🏠 MENÚ DE BOTONES
# =========================================================



# =========================================================
# 🎓 CONSULTA DIRECTA DE TEMAS DE MOODLE
# =========================================================

def ui_header(
    icono,
    titulo,
    subtitulo=""
):
    titulo_limpio=html.escape(
        str(titulo)
    )

    texto=(
        f"{icono} <b>{titulo_limpio}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    if subtitulo:
        texto+=(
            "\n<i>"
            + html.escape(
                str(subtitulo)
            )
            + "</i>"
        )

    return texto


def obtener_temas_curso_directo(courseid):
    """
    Obtiene las secciones/temas directamente desde Moodle.
    Evita depender de funciones adicionales de moodle_kaira.py.
    """
    try:
        from moodle_kaira import moodle_api

        datos=moodle_api(
            "core_course_get_contents",
            {
                "courseid": int(courseid),
            }
        )

        if isinstance(datos,dict) and datos.get("error"):
            return {
                "error": str(
                    datos.get(
                        "error",
                        "Moodle rechazó la consulta."
                    )
                )
            }

        if not isinstance(datos,list):
            return {
                "error": (
                    "Moodle no devolvió la estructura del curso."
                )
            }

        resultado=[]

        for indice,seccion in enumerate(
            datos
        ):
            if not isinstance(seccion,dict):
                continue

            actividades=[]

            for modulo in seccion.get(
                "modules",
                []
            ):
                if not isinstance(modulo,dict):
                    continue

                try:
                    visible=int(
                        modulo.get(
                            "visible",
                            1
                        ) or 1
                    )
                except Exception:
                    visible=1

                if visible==0:
                    continue

                actividades.append(
                    {
                        "id": modulo.get("id"),
                        "instance": modulo.get("instance"),
                        "name": modulo.get(
                            "name",
                            "Actividad"
                        ),
                        "modname": modulo.get(
                            "modname",
                            ""
                        ),
                        "url": modulo.get(
                            "url",
                            ""
                        ),
                        "description": modulo.get(
                            "description",
                            ""
                        ),
                        "visible": visible,
                    }
                )

            resultado.append(
                {
                    "id": seccion.get("id"),
                    "moodle_index": indice,
                    "name": (
                        seccion.get("name")
                        or f"Tema {indice+1}"
                    ),
                    "summary": seccion.get(
                        "summary",
                        ""
                    ),
                    "activities": actividades,
                }
            )

        return resultado

    except Exception as error:
        print(
            "⚠️ Error consultando temas directamente:",
            repr(error)
        )

        return {
            "error": str(error)
        }


_CURSOS_TEMAS_CACHE={}


def guardar_cache_temas(
    telegram_user_id,
    courseid,
    curso_nombre,
    temas,
):
    _CURSOS_TEMAS_CACHE[
        (
            int(telegram_user_id),
            int(courseid),
        )
    ]={
        "ts": time.time(),
        "curso_nombre": curso_nombre,
        "temas": temas,
    }


def leer_cache_temas(
    telegram_user_id,
    courseid,
):
    dato=_CURSOS_TEMAS_CACHE.get(
        (
            int(telegram_user_id),
            int(courseid),
        )
    )

    if not dato:
        return None

    if (
        time.time()
        - float(
            dato.get(
                "ts",
                0
            )
        )
        > 300
    ):
        _CURSOS_TEMAS_CACHE.pop(
            (
                int(telegram_user_id),
                int(courseid),
            ),
            None,
        )

        return None

    return dato


def _fecha_tarea_timestamp(tarea):
    try:
        ts=int(
            tarea.get("duedate",0)
            or 0
        )
        return datetime.fromtimestamp(ts)
    except Exception:
        return None


def _clasificar_tarea_calendario(
    tarea,
    ahora=None,
):
    if ahora is None:
        ahora=datetime.now()

    try:
        from moodle_kaira import (
            obtener_estado_entrega,
            obtener_usuario_moodle_id_actual,
        )

        assignid=int(
            tarea.get("id")
        )
        userid=obtener_usuario_moodle_id_actual()

        if userid:
            estado=obtener_estado_entrega(
                assignid,
                userid
            )
            if estado=="entregada":
                return "entregada"
    except Exception:
        pass

    fecha=_fecha_tarea_timestamp(tarea)

    if not fecha:
        return "pendiente"

    if fecha < ahora:
        return "atrasada"

    dias=(fecha-ahora).total_seconds()/86400

    if dias <= 2:
        return "proxima"

    return "pendiente"


def _obtener_tareas_calendario():
    try:
        from moodle_kaira import buscar_tareas_detalladas
        tareas=buscar_tareas_detalladas()

        if isinstance(tareas,list):
            return tareas
    except Exception:
        pass

    try:
        from moodle_kaira import obtener_tareas
        datos=obtener_tareas()

        resultado=[]

        if isinstance(datos,dict):
            for curso in datos.get(
                "courses",
                []
            ):
                for tarea in curso.get(
                    "assignments",
                    []
                ):
                    item=dict(tarea)
                    item.setdefault(
                        "curso_nombre",
                        curso.get(
                            "fullname",
                            "Curso"
                        )
                    )
                    resultado.append(item)

        return resultado
    except Exception:
        return []


def _eventos_por_fecha(
    tareas
):
    eventos={}

    for tarea in tareas:
        fecha=_fecha_tarea_timestamp(tarea)

        if not fecha:
            continue

        clave=fecha.date()

        eventos.setdefault(
            clave,
            []
        ).append(tarea)

    return eventos


def _icono_estado_calendario(
    tarea,
    ahora=None,
):
    estado=_clasificar_tarea_calendario(
        tarea,
        ahora
    )

    return {
        "entregada":"🟢",
        "atrasada":"🔴",
        "proxima":"🟠",
        "pendiente":"🟡",
    }.get(
        estado,
        "🟡"
    )


def _calendario_mes_texto(
    year,
    month,
    tareas,
):
    ahora=datetime.now()
    eventos=_eventos_por_fecha(
        tareas
    )

    cal=calendar.Calendar(
        firstweekday=6
    )

    nombres=[
        "DOM","LUN","MAR",
        "MIÉ","JUE","VIE","SÁB"
    ]

    lineas=[
        f"🗓️ <b>{calendar.month_name[month].upper()} {year}</b>",
        "",
        " ".join(
            f"{x:>4}"
            for x in nombres
        ),
    ]

    for semana in cal.monthdatescalendar(
        year,
        month
    ):
        celdas=[]

        for dia in semana:
            if dia.month != month:
                celdas.append(
                    "   "
                )
                continue

            items=eventos.get(
                dia,
                []
            )

            if items:
                # Prioriza estados importantes: atrasada > próxima > entregada > pendiente
                estados={
                    _clasificar_tarea_calendario(
                        x,
                        ahora
                    )
                    for x in items
                }

                if "atrasada" in estados:
                    icono="🔴"
                elif "proxima" in estados:
                    icono="🟠"
                elif "entregada" in estados:
                    icono="🟢"
                else:
                    icono="🟡"

                celda=f"{dia.day:02d}{icono}"
            else:
                celda=f"{dia.day:02d}"

            celdas.append(
                f"{celda:>4}"
            )

        lineas.append(
            "".join(celdas)
        )

    lineas.extend(
        [
            "",
            "🟢 Entregada   🟡 Pendiente",
            "🟠 Próxima     🔴 Atrasada",
        ]
    )

    return "\n".join(
        lineas
    )


def _calendario_semana_texto(
    fecha_base,
    tareas,
):
    inicio=(
        fecha_base.date()
        if isinstance(
            fecha_base,
            datetime
        )
        else fecha_base
    )

    # Semana lunes-domingo
    inicio=inicio.fromordinal(
        inicio.toordinal()
        - inicio.weekday()
    )

    fin=inicio.fromordinal(
        inicio.toordinal()+6
    )

    eventos=_eventos_por_fecha(
        tareas
    )

    lineas=[
        "📆 <b>SEMANA ACADÉMICA</b>",
        f"{inicio.strftime('%d/%m')} → {fin.strftime('%d/%m/%Y')}",
        "",
    ]

    nombres=[
        "Lunes","Martes","Miércoles",
        "Jueves","Viernes","Sábado","Domingo"
    ]

    ahora=datetime.now()

    for i in range(7):
        dia=inicio.fromordinal(
            inicio.toordinal()+i
        )
        items=eventos.get(
            dia,
            []
        )

        lineas.append(
            f"<b>{nombres[i]} {dia.strftime('%d/%m')}</b>"
        )

        if not items:
            lineas.append(
                "  — Sin tareas"
            )
        else:
            for tarea in sorted(
                items,
                key=lambda x: x.get(
                    "duedate",
                    0
                ) or 0
            )[:8]:
                fecha=_fecha_tarea_timestamp(
                    tarea
                )

                hora=(
                    fecha.strftime("%H:%M")
                    if fecha
                    else "--:--"
                )

                icono=_icono_estado_calendario(
                    tarea,
                    ahora
                )

                lineas.append(
                    f"  {icono} {str(tarea.get('name','Actividad'))[:42]}"
                    f" — {hora}"
                )

        lineas.append("")

    return "\n".join(
        lineas
    )


def teclado_calendario_mes(
    year,
    month,
):
    anterior_month=month-1
    anterior_year=year
    if anterior_month<1:
        anterior_month=12
        anterior_year-=1

    siguiente_month=month+1
    siguiente_year=year
    if siguiente_month>12:
        siguiente_month=1
        siguiente_year+=1

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "◀️",
                    callback_data=f"cal_mes:{anterior_year}:{anterior_month}",
                ),
                InlineKeyboardButton(
                    "📆 Semana",
                    callback_data="cal_semana",
                ),
                InlineKeyboardButton(
                    "▶️",
                    callback_data=f"cal_mes:{siguiente_year}:{siguiente_month}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📋 Ver tareas del mes",
                    callback_data=f"cal_lista:{year}:{month}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🏠 Menú",
                    callback_data="menu_principal",
                )
            ],
        ]
    )


def teclado_calendario_semana(
    fecha_base,
):
    try:
        d=fecha_base.strftime("%Y-%m-%d")
    except Exception:
        d=datetime.now().strftime("%Y-%m-%d")

    dt=datetime.strptime(
        d,
        "%Y-%m-%d"
    ).date()

    anterior=dt.fromordinal(
        dt.toordinal()-7
    )
    siguiente=dt.fromordinal(
        dt.toordinal()+7
    )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "◀️ Semana",
                    callback_data=f"cal_semana:{anterior.isoformat()}",
                ),
                InlineKeyboardButton(
                    "📅 Mes",
                    callback_data=f"cal_mes:{dt.year}:{dt.month}",
                ),
                InlineKeyboardButton(
                    "Semana ▶️",
                    callback_data=f"cal_semana:{siguiente.isoformat()}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🏠 Menú",
                    callback_data="menu_principal",
                )
            ],
        ]
    )


def teclado_calendario_fecha(
    year,
    month,
):
    return teclado_calendario_mes(
        year,
        month
    )


def teclado_menu_usuario(telegram_user_id=None):
    moodle_ok=(
        tiene_moodle_vinculado(telegram_user_id)
        if telegram_user_id is not None
        else False
    )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎓 Mis cursos",
                    callback_data="menu_cursos",
                ),
            ],
            [
                InlineKeyboardButton("📚 Mis tareas", callback_data="menu_tareas"),
                InlineKeyboardButton("🔴 Atrasadas", callback_data="menu_atrasadas"),
            ],
            [
                InlineKeyboardButton("📅 Hoy", callback_data="menu_hoy"),
                InlineKeyboardButton("📆 Esta semana", callback_data="menu_semana"),
            ],
            [
                InlineKeyboardButton("⏰ Recordatorios", callback_data="menu_recordatorios"),
                InlineKeyboardButton("👤 Mi cuenta", callback_data="menu_mi_cuenta"),
            ],
            [
                InlineKeyboardButton(
                    "🎓 Moodle ✅" if moodle_ok else "🎓 Vincular Moodle",
                    callback_data="menu_moodle",
                ),
                InlineKeyboardButton("💳 Mi membresía", callback_data="menu_membresia"),
            ],
            [
                InlineKeyboardButton("🔄 Renovar", callback_data="menu_renovar"),
                InlineKeyboardButton("🧠 Ayuda con IA", callback_data="menu_ia"),
            ],
            [
                InlineKeyboardButton(
                    "🗓️ Calendario académico",
                    callback_data="menu_calendario",
                ),
            ],
            [
                InlineKeyboardButton("🔔 Resumen diario", callback_data="menu_resumen"),
            ],
        ]
    )


def teclado_menu_admin(telegram_user_id=None):
    moodle_ok=(
        tiene_moodle_vinculado(telegram_user_id)
        if telegram_user_id is not None
        else False
    )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎓 Mis cursos",
                    callback_data="menu_cursos",
                ),
            ],
            [
                InlineKeyboardButton("📚 Mis tareas", callback_data="menu_tareas"),
                InlineKeyboardButton("🔴 Atrasadas", callback_data="menu_atrasadas"),
            ],
            [
                InlineKeyboardButton("📅 Hoy", callback_data="menu_hoy"),
                InlineKeyboardButton("📆 Esta semana", callback_data="menu_semana"),
            ],
            [
                InlineKeyboardButton("⏰ Recordatorios", callback_data="menu_recordatorios"),
                InlineKeyboardButton("👤 Mi cuenta", callback_data="menu_mi_cuenta"),
            ],
            [
                InlineKeyboardButton(
                    "🎓 Moodle ✅" if moodle_ok else "🎓 Vincular Moodle",
                    callback_data="menu_moodle",
                ),
                InlineKeyboardButton("💳 Mi membresía", callback_data="menu_membresia"),
            ],
            [
                InlineKeyboardButton("🔄 Renovar", callback_data="menu_renovar"),
                InlineKeyboardButton("🧠 Ayuda con IA", callback_data="menu_ia"),
            ],
            [
                InlineKeyboardButton(
                    "🗓️ Calendario académico",
                    callback_data="menu_calendario",
                ),
            ],
            [
                InlineKeyboardButton("🔔 Resumen diario", callback_data="menu_resumen"),
            ],
            [
                InlineKeyboardButton("👥 Usuarios", callback_data="admin_usuarios"),
                InlineKeyboardButton("📊 Resumen admin", callback_data="admin_resumen"),
            ],
            [
                InlineKeyboardButton("🔎 Buscar", callback_data="admin_buscar"),
                InlineKeyboardButton("💳 Pagos", callback_data="admin_pagos"),
            ],
            [
                InlineKeyboardButton("🟡 Vencimientos", callback_data="admin_vencimientos"),
                InlineKeyboardButton("💰 Precios", callback_data="admin_precios"),
            ],
        ]
    )


async def enviar_menu_principal(
    update,
    texto=None,
):
    if not update.effective_user:
        return

    if texto is None:
        nombre = (
            update.effective_user.first_name
            or ""
        ).strip()

        texto = (
            f"🤖 <b>KAIRA</b>\n\n"
            f"Hola {nombre or '👋'}.\n"
            "Selecciona una opción:"
        )

    teclado = (
        teclado_menu_admin(update.effective_user.id)
        if usuario_admin(update.effective_user.id)
        else teclado_menu_usuario(update.effective_user.id)
    )

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                texto,
                parse_mode="HTML",
                reply_markup=teclado,
            )
            return
        except Exception:
            pass

    if update.message:
        await update.message.reply_text(
            texto,
            parse_mode="HTML",
            reply_markup=teclado,
        )


def buscar_tarea_por_assignid(
    assignid
):
    try:
        from moodle_kaira import buscar_tareas_detalladas

        tareas=buscar_tareas_detalladas()

        if not isinstance(
            tareas,
            list
        ):
            return None

        for tarea in tareas:
            try:
                if int(
                    tarea.get("id")
                ) == int(assignid):
                    return tarea
            except Exception:
                continue

    except Exception as error:
        print(
            "⚠️ Error localizando tarea:",
            error
        )

    return None


async def mostrar_lista_tareas_botones(
    update,
    modo="tareas",
):
    if not await comprobar_acceso(update):
        return

    preparar_usuario_moodle(
        update.effective_user.id
    )

    try:
        from moodle_kaira import (
            obtener_tareas_proximas,
            obtener_tareas_atrasadas,
            obtener_tareas_dia,
        )

        if modo=="atrasadas":
            tareas=obtener_tareas_atrasadas()
            titulo="🔴 <b>TAREAS ATRASADAS</b>"
            subtitulo="Actividades cuyo plazo ya pasó"
        elif modo=="hoy":
            tareas=obtener_tareas_dia(0)
            titulo="📅 <b>TAREAS DE HOY</b>"
            subtitulo="Lo que tienes para hoy"
        elif modo=="semana":
            tareas=obtener_tareas_proximas(7)
            titulo="📆 <b>ESTA SEMANA</b>"
            subtitulo="Próximas actividades académicas"
        else:
            tareas=obtener_tareas_proximas(30)
            titulo="📚 <b>MIS TAREAS</b>"
            subtitulo="Tu actividad académica en Moodle"

        if isinstance(tareas,dict) and tareas.get("error"):
            await editar_menu_callback(
                update,
                "❌ <b>No pude consultar Moodle.</b>\n\n"
                + str(tareas["error"]),
                InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🏠 Menú",
                        callback_data="menu_principal",
                    )]
                ]),
            )
            return

        if not isinstance(tareas,list):
            tareas=[]

        if modo=="tareas":
            atrasadas=obtener_tareas_atrasadas()

            if isinstance(atrasadas,list):
                ids={
                    int(x.get("id"))
                    for x in tareas
                    if x.get("id") is not None
                }

                for x in atrasadas:
                    try:
                        xid=int(x.get("id"))
                    except Exception:
                        continue

                    if xid not in ids:
                        tareas.append(x)

        # Deduplicar.
        unicas={}

        for tarea in tareas:
            try:
                unicas[int(tarea.get("id"))]=tarea
            except Exception:
                continue

        tareas=list(unicas.values())

        tareas.sort(
            key=lambda x:int(
                x.get("duedate",0) or 0
            )
        )

        # =====================================================
        # 🎨 AGRUPACIÓN ESTÉTICA POR MATERIA
        # =====================================================
        grupos={}

        for tarea in tareas[:30]:
            curso=str(
                tarea.get(
                    "curso_nombre",
                    "Materia sin nombre"
                )
            ).strip()

            if not curso:
                curso="Materia sin nombre"

            grupos.setdefault(
                curso,
                []
            ).append(tarea)

        filas=[]

        # Encabezado visual.
        texto=titulo+"\n"
        texto+="━━━━━━━━━━━━━━━━━━━━\n"
        texto+=f"<i>{subtitulo}</i>\n\n"

        if not grupos:
            texto+=(
                "✨ <b>Todo en orden.</b>\n\n"
                "No encontré actividades en esta sección."
            )

        contador=0

        for numero_materia,(curso,actividades) in enumerate(
            sorted(
                grupos.items(),
                key=lambda item:item[0].lower()
            ),
            start=1,
        ):
            if contador>=30:
                break

            # Resumen por materia.
            resumen_estados=[]

            for tarea in actividades:
                try:
                    assignid=int(
                        tarea.get("id")
                    )
                except Exception:
                    continue

                icono="⏳"

                try:
                    from moodle_kaira import (
                        obtener_estado_entrega,
                        obtener_usuario_moodle_id_actual,
                    )

                    mid=obtener_usuario_moodle_id_actual()

                    if mid:
                        estado=obtener_estado_entrega(
                            assignid,
                            mid,
                        )

                        if estado=="entregada":
                            icono="✅"
                        elif estado=="no_entregada":
                            icono="⏳"
                except Exception:
                    pass

                resumen_estados.append(
                    icono
                )

            entregadas=resumen_estados.count("✅")
            pendientes=resumen_estados.count("⏳")

            texto+=(
                f"🎓 <b>{curso}</b>\n"
                f"   <i>{len(actividades)} actividad"
                f"{'' if len(actividades)==1 else 'es'}"
                f" · ✅ {entregadas} · ⏳ {pendientes}</i>\n\n"
            )

            for tarea in sorted(
                actividades,
                key=lambda x:int(
                    x.get("duedate",0) or 0
                )
            ):
                if contador>=30:
                    break

                try:
                    assignid=int(
                        tarea.get("id")
                    )
                except Exception:
                    continue

                nombre=str(
                    tarea.get(
                        "name",
                        "Actividad"
                    )
                ).strip()

                fecha=_fecha_tarea_timestamp(
                    tarea
                )

                icono="⏳"

                try:
                    from moodle_kaira import (
                        obtener_estado_entrega,
                        obtener_usuario_moodle_id_actual,
                    )

                    mid=obtener_usuario_moodle_id_actual()

                    if mid:
                        estado=obtener_estado_entrega(
                            assignid,
                            mid,
                        )

                        if estado=="entregada":
                            icono="✅"
                        elif estado=="no_entregada":
                            icono="⏳"
                except Exception:
                    pass

                fecha_texto=(
                    fecha.strftime(
                        "%d/%m · %H:%M"
                    )
                    if fecha
                    else
                    "Sin fecha"
                )

                # Línea visual compacta, mientras el botón
                # contiene la acción real.
                texto+=(
                    f"{icono} {nombre[:58]}\n"
                    f"   📅 {fecha_texto}\n"
                )

                filas.append(
                    [
                        InlineKeyboardButton(
                            f"{icono} {nombre[:48]}",
                            callback_data=(
                                f"tarea_detalle:{assignid}"
                            ),
                        )
                    ]
                )

                contador+=1

            # Separador entre materias.
            if contador<30:
                texto+="\n"

        texto+=(
            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <i>Toca una actividad para ver "
            "detalles, archivos, retroalimentación, "
            "historial y entrega.</i>"
        )

        filas.append(
            [
                InlineKeyboardButton(
                    "🗓️ Calendario académico",
                    callback_data="menu_calendario",
                )
            ]
        )

        filas.append(
            [
                InlineKeyboardButton(
                    "🏠 Menú principal",
                    callback_data="menu_principal",
                )
            ]
        )

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup(filas),
        )

    except Exception as error:
        print(
            "⚠️ Error mostrando tareas con botones:",
            error,
        )

        await editar_menu_callback(
            update,
            "❌ <b>No pude cargar las tareas.</b>\n\n"
            "Inténtalo nuevamente.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🏠 Menú",
                    callback_data="menu_principal",
                )]
            ]),
        )




async def enviar_adjunto_moodle_callback(
    update,
    context,
    assignid,
    indice,
):
    """
    Descarga temporalmente el archivo adjunto desde Moodle y se lo
    envía al chat de Telegram. Después elimina la copia temporal.
    """
    if not update.callback_query or not update.effective_user:
        return

    uid=update.effective_user.id
    preparar_usuario_moodle(uid)

    tarea=buscar_tarea_por_assignid(assignid)

    if not tarea:
        await update.callback_query.answer(
            "No encontré la actividad.",
            show_alert=True,
        )
        return

    try:
        from moodle_kaira import (
            archivos_de_tarea,
            descargar_archivo_moodle,
        )

        archivos=archivos_de_tarea(tarea)

        if indice < 0 or indice >= len(archivos):
            await update.callback_query.answer(
                "No encontré ese archivo.",
                show_alert=True,
            )
            return

        archivo=archivos[indice]

        nombre=(
            archivo.get("filename")
            or archivo.get("name")
            or "archivo_moodle"
        )

        # Enviar "procesando" en el propio callback.
        await update.callback_query.answer(
            "📥 Preparando archivo...",
        )

        import tempfile
        temp_dir=tempfile.mkdtemp(
            prefix="kaira_moodle_"
        )

        ruta=descargar_archivo_moodle(
            archivo,
            temp_dir,
        )

        if not ruta or not os.path.isfile(ruta):
            await update.callback_query.message.reply_text(
                "❌ No pude descargar el archivo desde Moodle."
            )
            return

        with open(ruta,"rb") as archivo_local:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=archivo_local,
                caption=(
                    "📚 <b>Archivo de Moodle</b>\n\n"
                    f"📎 {nombre}"
                ),
                parse_mode="HTML",
            )

        print(
            f"📤 Archivo de Moodle enviado a Telegram: {nombre}"
        )

    except Exception as error:
        print(
            "⚠️ Error enviando adjunto de Moodle:",
            error,
        )

        try:
            await update.callback_query.message.reply_text(
                "❌ No pude enviar el archivo.\n\n"
                "Inténtalo nuevamente."
            )
        except Exception:
            pass

    finally:
        try:
            import shutil
            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )
        except Exception:
            pass



def _escape_html_feedback(texto):
    return html.escape(
        str(texto or "")
    )


async def enviar_feedback_archivo_callback(
    update,
    context,
    assignid,
    indice,
):
    if not update.callback_query or not update.effective_user:
        return

    uid=update.effective_user.id
    preparar_usuario_moodle(uid)
    query=update.callback_query

    try:
        from moodle_kaira import (
            obtener_retroalimentacion_tarea,
            descargar_archivo_moodle,
        )

        feedback=obtener_retroalimentacion_tarea(
            assignid
        )
        archivos=feedback.get("archivos",[])

        indice=int(indice)
        if indice<0 or indice>=len(archivos):
            await query.answer(
                "No encontré ese archivo de retroalimentación.",
                show_alert=True,
            )
            return

        archivo=archivos[indice]
        nombre=(
            archivo.get("filename")
            or archivo.get("name")
            or "retroalimentacion"
        )

        await query.answer(
            "📥 Descargando archivo del profesor..."
        )

        temp_dir=tempfile.mkdtemp(
            prefix="kaira_feedback_"
        )

        ruta=descargar_archivo_moodle(
            archivo,
            temp_dir,
        )

        if not ruta or not os.path.isfile(ruta):
            await query.message.reply_text(
                "❌ No pude descargar el archivo de retroalimentación desde Moodle."
            )
            return

        with open(ruta,"rb") as archivo_local:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=archivo_local,
                caption=(
                    "👨‍🏫 <b>Archivo de retroalimentación</b>\n\n"
                    f"📎 {_escape_html_feedback(nombre)}"
                ),
                parse_mode="HTML",
            )

    except Exception as error:
        print(
            "⚠️ Error enviando archivo de retroalimentación:",
            error,
        )
        try:
            await query.message.reply_text(
                "❌ No pude enviar el archivo del profesor."
            )
        except Exception:
            pass
    finally:
        try:
            import shutil
            if 'temp_dir' in locals():
                shutil.rmtree(
                    temp_dir,
                    ignore_errors=True,
                )
        except Exception:
            pass


async def mostrar_retroalimentacion_tarea(
    update,
    assignid,
):
    if not await comprobar_acceso(update):
        return

    preparar_usuario_moodle(
        update.effective_user.id
    )

    try:
        from moodle_kaira import obtener_retroalimentacion_tarea
        feedback=obtener_retroalimentacion_tarea(
            assignid
        )
    except Exception as error:
        print(
            "⚠️ Error cargando retroalimentación:",
            error,
        )
        feedback={
            "ok":False,
            "tiene_feedback":False,
            "texto":"",
            "grado":"",
            "fecha":0,
            "archivos":[],
        }

    if not feedback.get("ok"):
        texto=(
            "👨‍🏫 <b>RETROALIMENTACIÓN</b>\n\n"
            "❌ No pude consultar la retroalimentación en Moodle."
        )
    elif not feedback.get("tiene_feedback"):
        texto=(
            "👨‍🏫 <b>RETROALIMENTACIÓN</b>\n\n"
            "🟢 El profesor todavía no ha dejado comentarios, archivos o calificación visibles."
        )
    else:
        lineas=[
            "👨‍🏫 <b>RETROALIMENTACIÓN DEL PROFESOR</b>",
            "",
        ]

        grado=feedback.get("grado")
        if grado:
            lineas.append(
                f"📝 Calificación: <b>{_escape_html_feedback(grado)}</b>"
            )

        fecha=feedback.get("fecha",0) or 0
        if fecha:
            try:
                lineas.append(
                    "📅 Revisado: <b>"
                    + datetime.fromtimestamp(
                        int(fecha)
                    ).strftime("%d/%m/%Y %H:%M")
                    + "</b>"
                )
            except Exception:
                pass

        comentario=feedback.get("texto","").strip()
        if comentario:
            lineas.extend([
                "",
                "💬 <b>Mensaje del profesor:</b>",
                _escape_html_feedback(comentario),
            ])

        archivos=feedback.get("archivos",[]) or []
        if archivos:
            lineas.extend([
                "",
                "📎 <b>ARCHIVOS DEL PROFESOR</b>",
                "Toca un archivo para recibirlo por Telegram:",
            ])

        texto="\n".join(lineas)

    botones=[]
    for indice,archivo in enumerate(
        feedback.get("archivos",[]) or []
    ):
        nombre=(
            archivo.get("filename")
            or archivo.get("name")
            or "Archivo del profesor"
        )
        botones.append([
            InlineKeyboardButton(
                f"📎 {str(nombre)[:48]}",
                callback_data=f"feedback_archivo:{int(assignid)}:{indice}",
            )
        ])

    botones.extend([
        [
            InlineKeyboardButton(
                "📋 Ver tarea",
                callback_data=f"tarea_detalle:{int(assignid)}",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Menú",
                callback_data="menu_principal",
            )
        ],
    ])

    await editar_menu_callback(
        update,
        texto,
        InlineKeyboardMarkup(botones),
    )


async def mostrar_historial_tarea(
    update,
    assignid,
):
    if not await comprobar_acceso(update):
        return

    preparar_usuario_moodle(
        update.effective_user.id
    )

    try:
        from moodle_kaira import historial_entrega_tarea
        historial=historial_entrega_tarea(
            assignid
        )
    except Exception as error:
        print(
            "⚠️ Error historial de tarea:",
            error,
        )
        historial={
            "ok":False,
            "intentos":[],
        }

    intentos=historial.get("intentos",[]) or []

    if not historial.get("ok"):
        texto="📚 <b>HISTORIAL DE ENTREGAS</b>\n\n❌ No pude consultar el historial."
    elif not intentos:
        texto="📚 <b>HISTORIAL DE ENTREGAS</b>\n\n🟢 No hay intentos anteriores registrados."
    else:
        lineas=[
            "📚 <b>HISTORIAL DE ENTREGAS</b>",
            "",
        ]

        for intento in intentos:
            numero=intento.get(
                "attemptnumber",
                "?"
            )
            grado=intento.get(
                "gradefordisplay",
                ""
            )

            linea=f"🔹 Intento <b>{numero}</b>"
            if grado:
                linea += f" — 📝 {_escape_html_feedback(grado)}"
            lineas.append(linea)

        texto="\n".join(lineas)

    await editar_menu_callback(
        update,
        texto,
        InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📋 Ver tarea",
                callback_data=f"tarea_detalle:{int(assignid)}",
            )],
            [InlineKeyboardButton(
                "🏠 Menú",
                callback_data="menu_principal",
            )],
        ]),
    )


async def mostrar_detalle_tarea_boton(
    update,
    assignid,
):
    if not await comprobar_acceso(update):
        return

    preparar_usuario_moodle(
        update.effective_user.id
    )

    query=update.callback_query

    tarea=buscar_tarea_por_assignid(
        assignid
    )

    if not tarea:
        await query.edit_message_text(
            "❌ No encontré esa actividad en Moodle.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Volver",
                        callback_data="menu_tareas",
                    )
                ]]
            ),
        )
        return

    try:
        from moodle_kaira import detalle_tarea

        detalle=detalle_tarea(
            tarea
        )

        texto=detalle.get(
            "texto",
            "No hay detalles disponibles."
        )

        archivos_moodle=detalle.get(
            "archivos",
            []
        ) or []

    except Exception:
        texto=(
            f"📚 <b>{tarea.get('curso_nombre','Curso')}</b>\n\n"
            f"📝 <b>{tarea.get('name','Actividad')}</b>"
        )
        archivos_moodle=(
            tarea.get("introattachments", [])
            or tarea.get("attachments", [])
            or []
        ) or []

    try:
        from moodle_kaira import (
            obtener_estado_entrega,
            obtener_usuario_moodle_id_actual,
            obtener_capacidad_entrega,
        )

        mid=obtener_usuario_moodle_id_actual()
        estado=obtener_estado_entrega(
            assignid,
            mid
        ) if mid else "desconocida"

        capacidad=obtener_capacidad_entrega(
            assignid,
            mid
        ) if mid else {
            "puede_entregar": False,
            "motivo": "cerrada",
            "fecha_cierre": 0,
        }

    except Exception as error:
        print(
            "⚠️ No pude comprobar capacidad de entrega:",
            error
        )
        estado="desconocida"
        capacidad={
            "puede_entregar": True,
            "motivo": "no_confirmado",
            "fecha_cierre": 0,
        }

    # Retroalimentación / historial visibles para el estudiante.
    try:
        from moodle_kaira import obtener_retroalimentacion_tarea
        feedback_tarea=obtener_retroalimentacion_tarea(
            assignid,
            mid,
        ) if mid else {
            "ok":False,
            "tiene_feedback":False,
            "intentos":[],
        }
    except Exception as error:
        print(
            "⚠️ No pude consultar retroalimentación:",
            error,
        )
        feedback_tarea={
            "ok":False,
            "tiene_feedback":False,
            "intentos":[],
        }

    # Botones para abrir/recibir cada archivo adjunto de Moodle.
    botones_archivos=[]

    for indice, archivo in enumerate(
        archivos_moodle
    ):
        if not isinstance(archivo, dict):
            continue

        nombre_archivo=(
            archivo.get("filename")
            or archivo.get("name")
            or "Archivo"
        )

        if not (
            archivo.get("fileurl")
            or archivo.get("url")
        ):
            continue

        botones_archivos.append(
            [
                InlineKeyboardButton(
                    f"📎 {str(nombre_archivo)[:48]}",
                    callback_data=(
                        f"moodle_archivo:{int(assignid)}:{int(indice)}"
                    ),
                )
            ]
        )

    if botones_archivos:
        texto += "\n\n📎 <b>TOCA UN ARCHIVO PARA RECIBIRLO</b>"

    # =====================================================
    # 📤 ESTADO DEL BUZÓN
    # =====================================================

    puede_entregar=bool(
        capacidad.get(
            "puede_entregar",
            True
        )
    )

    boton_revision=[
        [InlineKeyboardButton(
            "📄 REVISAR MI TRABAJO",
            callback_data=f"revisar_tarea:{int(assignid)}",
        )]
    ]

    motivo=capacidad.get(
        "motivo",
        "no_confirmado"
    )

    if estado=="entregada":
        texto += "\n\n✅ <b>Ya aparece como entregada en Moodle.</b>"

        extras=[]
        if feedback_tarea.get("tiene_feedback"):
            extras.append([
                InlineKeyboardButton(
                    "👨‍🏫 Retroalimentación",
                    callback_data=f"feedback_detalle:{int(assignid)}",
                )
            ])
        if feedback_tarea.get("intentos"):
            extras.append([
                InlineKeyboardButton(
                    "📚 Historial de entregas",
                    callback_data=f"historial_tarea:{int(assignid)}",
                )
            ])

        teclado=InlineKeyboardMarkup(
            botones_archivos
            + boton_revision
            + extras
            + [
                [
                    InlineKeyboardButton(
                        "⬅️ Volver a tareas",
                        callback_data="menu_tareas",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 Menú",
                        callback_data="menu_principal",
                    )
                ],
            ]
        )

    elif not puede_entregar:
        if motivo=="no_disponible":
            texto += (
                "\n\n🕐 <b>BUZÓN AÚN NO ABIERTO</b>"
            )
        else:
            texto += (
                "\n\n🔒 <b>BUZÓN VENCIDO / CERRADO</b>\n"
                "Moodle no permite nuevas entregas en este momento."
            )

        extras=[]
        if feedback_tarea.get("tiene_feedback"):
            extras.append([
                InlineKeyboardButton(
                    "👨‍🏫 Retroalimentación",
                    callback_data=f"feedback_detalle:{int(assignid)}",
                )
            ])
        if feedback_tarea.get("intentos"):
            extras.append([
                InlineKeyboardButton(
                    "📚 Historial de entregas",
                    callback_data=f"historial_tarea:{int(assignid)}",
                )
            ])

        teclado=InlineKeyboardMarkup(
            botones_archivos
            + boton_revision
            + extras
            + [
                [
                    InlineKeyboardButton(
                        "⬅️ Volver a tareas",
                        callback_data="menu_tareas",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 Menú",
                        callback_data="menu_principal",
                    )
                ],
            ]
        )

    else:
        if motivo=="tardia":
            texto += (
                "\n\n⚠️ <b>ENTREGA TARDÍA</b>\n"
                "Moodle todavía permite enviar el archivo."
            )

        extras=[]
        if feedback_tarea.get("tiene_feedback"):
            extras.append([
                InlineKeyboardButton(
                    "👨‍🏫 Retroalimentación",
                    callback_data=f"feedback_detalle:{int(assignid)}",
                )
            ])
        if feedback_tarea.get("intentos"):
            extras.append([
                InlineKeyboardButton(
                    "📚 Historial de entregas",
                    callback_data=f"historial_tarea:{int(assignid)}",
                )
            ])

        teclado=InlineKeyboardMarkup(
            botones_archivos
            + boton_revision
            + extras
            + [
                [
                    InlineKeyboardButton(
                        "📤 ENTREGAR ARCHIVO",
                        callback_data=f"entregar_tarea:{int(assignid)}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Volver a tareas",
                        callback_data="menu_tareas",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 Menú",
                        callback_data="menu_principal",
                    )
                ],
            ]
        )


    await editar_menu_callback(
        update,
        texto,
        teclado,
    )


async def iniciar_entrega_tarea(
    update,
    assignid,
):
    if not await comprobar_acceso(update):
        return

    preparar_usuario_moodle(
        update.effective_user.id
    )

    query=update.callback_query

    tarea=buscar_tarea_por_assignid(
        assignid
    )

    if not tarea:
        await query.edit_message_text(
            "❌ No encontré esa actividad en Moodle."
        )
        return

    try:
        from moodle_kaira import (
            obtener_usuario_moodle_id_actual,
            obtener_capacidad_entrega,
        )

        mid=obtener_usuario_moodle_id_actual()

        capacidad=obtener_capacidad_entrega(
            assignid,
            mid
        ) if mid else {
            "puede_entregar": False,
            "motivo": "cerrada",
        }

        if not capacidad.get(
            "puede_entregar",
            True
        ):
            motivo=capacidad.get(
                "motivo",
                "cerrada"
            )

            if motivo=="no_disponible":
                texto_cierre=(
                    "🕐 <b>BUZÓN AÚN NO ABIERTO</b>"
                )
            else:
                texto_cierre=(
                    "🔒 <b>BUZÓN VENCIDO / CERRADO</b>\n\n"
                    "Moodle no permite nuevas entregas."
                )

            await query.edit_message_text(
                texto_cierre,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ Volver a tareas",
                            callback_data="menu_tareas",
                        )
                    ]
                ]),
            )
            return

    except Exception as error:
        print(
            "⚠️ No pude comprobar el buzón antes de entregar:",
            error
        )

    nombre=tarea.get(
        "name",
        "Actividad",
    )

    guardar_entrega_pendiente(
        update.effective_user.id,
        assignid,
        nombre,
    )

    await query.edit_message_text(
        "📤 <b>ENTREGAR ACTIVIDAD</b>\n\n"
        f"📝 <b>{nombre}</b>\n\n"
        "Ahora envía aquí el archivo que deseas entregar.\n\n"
        "📎 El archivo se enviará directamente a Moodle.\n"
        "🗑️ KAIRA no conservará una copia permanente.\n\n"
        "Ejemplo: PDF, DOCX, XLSX, PPTX, etc.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "❌ Cancelar",
                    callback_data="cancelar_entrega",
                )
            ]]
        ),
    )


async def recibir_documento_entrega(
    update,
    context,
):
    if not update.message or not update.effective_user:
        return

    user_id=update.effective_user.id

    if not await comprobar_acceso(update):
        return

    pendiente=obtener_entrega_pendiente(
        user_id
    )

    if not pendiente:
        await update.message.reply_text(
            "📎 No hay ninguna entrega pendiente.\n\n"
            "Primero entra a una actividad y pulsa "
            "📤 Entregar archivo."
        )
        return

    document=update.message.document

    if not document:
        await update.message.reply_text(
            "❌ No recibí un archivo válido."
        )
        return

    nombre_archivo=(
        document.file_name
        or "archivo"
    )

    suffix=""
    if "." in nombre_archivo:
        suffix="."+nombre_archivo.rsplit(
            ".",
            1
        )[-1][:12]

    temp_path=None

    try:
        await update.message.reply_text(
            "⏳ <b>Subiendo tu archivo a Moodle...</b>\n\n"
            f"📎 {nombre_archivo}",
            parse_mode="HTML",
        )

        telegram_file=await document.get_file()

        with tempfile.NamedTemporaryFile(
            prefix="kaira_entrega_",
            suffix=suffix,
            delete=False,
        ) as temporal:
            temp_path=temporal.name

        await telegram_file.download_to_drive(
            custom_path=temp_path
        )

        assignid=int(
            pendiente["assignid"]
        )

        from moodle_kaira import entregar_archivo_tarea

        resultado=entregar_archivo_tarea(
            assignid,
            temp_path,
            nombre_archivo,
        )

        if not resultado.get("ok"):
            await update.message.reply_text(
                "❌ <b>No pude completar la entrega.</b>\n\n"
                + str(
                    resultado.get(
                        "error",
                        "Moodle rechazó el archivo."
                    )
                ),
                parse_mode="HTML",
            )
            return

        quitar_entrega_pendiente(
            user_id
        )

        nombre_tarea=pendiente.get(
            "nombre_tarea",
            "Actividad",
        )

        if resultado.get("enviada"):
            estado_final=(
                "🟢 Moodle confirmó que la entrega "
                "fue enviada para calificación."
            )
        else:
            estado_final=(
                "🟡 El archivo quedó guardado en tu entrega "
                "de Moodle, pero Moodle no permitió completar "
                "el paso final automáticamente."
            )

        await update.message.reply_text(
            "✅ <b>ENTREGA PROCESADA</b>\n\n"
            f"📚 <b>{nombre_tarea}</b>\n"
            f"📎 {nombre_archivo}\n\n"
            f"{estado_final}\n\n"
            "🗑️ La copia temporal de KAIRA fue eliminada.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📚 Mis tareas",
                            callback_data="menu_tareas",
                        ),
                        InlineKeyboardButton(
                            "🏠 Menú",
                            callback_data="menu_principal",
                        ),
                    ]
                ]
            ),
        )

    except Exception as error:
        print(
            "⚠️ Error procesando documento de entrega:",
            error
        )

        await update.message.reply_text(
            "❌ Ocurrió un error procesando el archivo.\n\n"
            "Inténtalo nuevamente.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "🏠 Menú",
                        callback_data="menu_principal",
                    )
                ]]
            ),
        )

    finally:
        quitar_entrega_pendiente(
            user_id
        )

        if temp_path:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception as error:
                print(
                    "⚠️ No pude borrar archivo temporal:",
                    error
                )


async def iniciar_revision_tarea(update, assignid):
    if not await comprobar_acceso(update):
        return

    user_id = update.effective_user.id
    preparar_usuario_moodle(user_id)
    query = update.callback_query

    try:
        from moodle_kaira import obtener_contexto_revision_tarea
        contexto = obtener_contexto_revision_tarea(int(assignid))
    except Exception as error:
        print("⚠️ Error preparando revisión:", error)
        contexto = {"ok": False, "error": "No pude consultar la actividad en Moodle."}

    if not contexto.get("ok"):
        await query.edit_message_text(
            "❌ <b>No pude preparar la revisión.</b>\n\n"
            + html.escape(str(contexto.get("error", "Moodle no devolvió la actividad."))),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                "⬅️ Volver a tarea",
                callback_data=f"tarea_detalle:{int(assignid)}",
            )]]),
        )
        return

    quitar_revision_pendiente(user_id)
    nombre = contexto.get("tarea", {}).get("name", "Actividad")
    await query.edit_message_text(
        "📄 <b>REVISAR MI TRABAJO</b>\n\n"
        f"📝 <b>{html.escape(str(nombre))}</b>\n\n"
        "Ahora envía el archivo que quieres revisar.\n\n"
        "📌 La revisión NO es una entrega en Moodle.\n"
        "📌 No cambia fechas ni calificaciones.\n"
        "📌 KAIRA comparará el archivo con los requisitos visibles de Moodle.\n\n"
        "Formatos: PDF, DOCX, XLSX o PPTX.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
            "❌ Cancelar",
            callback_data=f"tarea_detalle:{int(assignid)}",
        )]]),
    )
    # Guardamos el contexto académico en memoria temporal del proceso para que
    # la recepción del archivo sea inequívoca.
    datos = cargar_revisiones_pendientes()
    datos[str(int(user_id))] = {
        "telegram_user_id": int(user_id),
        "assignid": int(assignid),
        "nombre_tarea": nombre,
        "contexto": contexto,
        "creado_en": datetime.now().isoformat(),
    }
    guardar_revisiones_pendientes(datos)


def _icono_estado(estado):
    return {
        "cumplido": "✅",
        "faltante": "❌",
        "no_verificable": "⚠️",
    }.get(str(estado).lower(), "⚠️")


def _formatear_revision(resultado):
    lineas = ["🔎 <b>REVISIÓN PREVIA</b>", ""]
    resumen = str(resultado.get("resumen", "")).strip()
    if resumen:
        lineas += [html.escape(resumen), ""]

    requisitos = resultado.get("requisitos", []) or []
    if requisitos:
        lineas += ["<b>REQUISITOS DE MOODLE</b>"]
        for item in requisitos[:30]:
            if not isinstance(item, dict):
                continue
            estado = item.get("estado", "no_verificable")
            req = html.escape(str(item.get("requisito", "Requisito")))
            evidencia = str(item.get("evidencia", "")).strip()
            linea = f"{_icono_estado(estado)} {req}"
            if evidencia:
                linea += f"\n   <i>{html.escape(evidencia[:350])}</i>"
            lineas.append(linea)
        lineas.append("")

    formato = resultado.get("formato", []) or []
    if formato:
        lineas += ["<b>FORMATO</b>"]
        for item in formato[:15]:
            if not isinstance(item, dict):
                continue
            req = html.escape(str(item.get("requisito", "Formato")))
            evidencia = str(item.get("evidencia", "")).strip()
            linea = f"{_icono_estado(item.get('estado'))} {req}"
            if evidencia:
                linea += f"\n   <i>{html.escape(evidencia[:300])}</i>"
            lineas.append(linea)
        lineas.append("")

    recomendaciones = resultado.get("recomendaciones", []) or []
    if recomendaciones:
        lineas += ["💡 <b>RECOMENDACIONES DE KAIRA</b>"]
        for rec in recomendaciones[:8]:
            lineas.append("• " + html.escape(str(rec)))
        lineas.append("")

    if resultado.get("rubrica_disponible"):
        lineas.append("📋 La revisión tomó como referencia la rúbrica/criterios disponibles en Moodle.")
    else:
        lineas.append("ℹ️ No encontré una rúbrica o lista de cotejo accesible para esta cuenta; la revisión se basó en las instrucciones visibles de Moodle.")

    lineas += ["", "⚠️ <i>Esta revisión es asistencia previa y no sustituye la calificación oficial del profesor.</i>"]
    return "\n".join(lineas)


async def recibir_documento_revision(update, context, pendiente):
    user_id = update.effective_user.id
    document = update.message.document
    nombre_archivo = document.file_name or "archivo"
    ext = os.path.splitext(nombre_archivo)[1].lower()
    permitidas = {".pdf", ".docx", ".xlsx", ".pptx"}
    if ext not in permitidas:
        await update.message.reply_text("❌ Formato no compatible. Usa PDF, DOCX, XLSX o PPTX.")
        return

    temp_path = None
    try:
        await update.message.reply_text(
            "🔎 <b>Analizando tu trabajo...</b>\n\n"
            f"📎 {html.escape(nombre_archivo)}\n"
            "⏳ Comparando contra los requisitos visibles de Moodle.",
            parse_mode="HTML",
        )

        telegram_file = await document.get_file()
        suffix = ext[:12]
        with tempfile.NamedTemporaryFile(prefix="kaira_revision_", suffix=suffix, delete=False) as temporal:
            temp_path = temporal.name
        await telegram_file.download_to_drive(custom_path=temp_path)

        from revisor_trabajos import revisar_archivo, guardar_revision_supabase
        contexto = pendiente.get("contexto") or {}
        revision = await asyncio.to_thread(
            revisar_archivo,
            temp_path,
            nombre_archivo,
            contexto,
        )
        resultado = revision["resultado"]
        assignid = int(pendiente["assignid"])

        guardar_revision_supabase(
            user_id,
            assignid,
            nombre_archivo,
            resultado,
        )

        guardar_revision_pendiente(
            user_id,
            assignid,
            pendiente.get("nombre_tarea", "Actividad"),
            nombre_archivo,
            temp_path,
            resultado,
            contexto=contexto,
        )
        temp_path = None  # La conservamos temporalmente hasta decidir entregar/corregir.

        await update.message.reply_text(
            _formatear_revision(resultado),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 SUBIR CORRECCIÓN", callback_data=f"revisar_tarea:{assignid}")],
                [InlineKeyboardButton("📤 ENTREGAR DE TODOS MODOS", callback_data="revisar_entregar")],
                [InlineKeyboardButton("📋 VER ACTIVIDAD", callback_data=f"tarea_detalle:{assignid}")],
            ]),
        )
    except Exception as error:
        print("⚠️ Error revisando archivo:", error)
        await update.message.reply_text(
            "❌ <b>No pude revisar el archivo.</b>\n\n"
            + html.escape(str(error)[:800]),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                "📋 Ver actividad",
                callback_data=f"tarea_detalle:{int(pendiente['assignid'])}",
            )]]),
        )
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


async def entregar_archivo_revisado_callback(update, context):
    if not await comprobar_acceso(update):
        return
    user_id = update.effective_user.id
    preparar_usuario_moodle(user_id)
    query = update.callback_query
    pendiente = obtener_revision_pendiente(user_id)
    if not pendiente or not pendiente.get("ruta_archivo"):
        await query.answer("La revisión temporal ya expiró. Vuelve a subir el archivo.", show_alert=True)
        return

    ruta = pendiente.get("ruta_archivo")
    if not os.path.isfile(ruta):
        await query.answer("El archivo temporal ya no está disponible. Vuelve a subirlo.", show_alert=True)
        quitar_revision_pendiente(user_id, borrar_archivo=False)
        return

    assignid = int(pendiente["assignid"])
    try:
        from moodle_kaira import obtener_usuario_moodle_id_actual, obtener_capacidad_entrega, entregar_archivo_tarea
        mid = obtener_usuario_moodle_id_actual()
        capacidad = obtener_capacidad_entrega(assignid, mid) if mid else {"puede_entregar": False, "motivo": "cerrada"}
        if not capacidad.get("puede_entregar", False):
            await query.edit_message_text(
                "🔒 <b>BUZÓN CERRADO</b>\n\nMoodle no permite nuevas entregas para esta actividad.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Ver actividad", callback_data=f"tarea_detalle:{assignid}")]]),
            )
            return

        await query.edit_message_text("⏳ <b>Enviando el archivo revisado a Moodle...</b>", parse_mode="HTML")
        resultado = await asyncio.to_thread(
            entregar_archivo_tarea,
            assignid,
            ruta,
            pendiente.get("nombre_archivo", "archivo"),
        )
        if not resultado.get("ok"):
            await query.edit_message_text(
                "❌ <b>Moodle rechazó la entrega.</b>\n\n" + html.escape(str(resultado.get("error", "Error desconocido"))),
                parse_mode="HTML",
            )
            return

        nombre = html.escape(str(pendiente.get("nombre_archivo", "archivo")))
        quitar_revision_pendiente(user_id, borrar_archivo=True)
        estado = "🟢 Moodle confirmó el envío para calificación." if resultado.get("enviada") else "🟡 Moodle guardó el archivo, pero no confirmó el paso final de envío."
        await query.edit_message_text(
            "📤 <b>ENTREGA REALIZADA</b>\n\n"
            f"📎 {nombre}\n\n{estado}\n\n"
            "⚠️ La revisión previa no sustituye la calificación del profesor.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📚 Mis tareas", callback_data="menu_tareas")]]),
        )
    except Exception as error:
        print("⚠️ Error entregando revisión:", error)
        await query.edit_message_text(
            "❌ <b>No pude realizar la entrega.</b>\n\n" + html.escape(str(error)[:800]),
            parse_mode="HTML",
        )


async def recibir_documento_kaira(update, context):
    if not update.message or not update.effective_user:
        return
    pendiente_revision = obtener_revision_pendiente(update.effective_user.id)
    if pendiente_revision and pendiente_revision.get("contexto"):
        if not await comprobar_acceso(update):
            return
        await recibir_documento_revision(update, context, pendiente_revision)
        return
    await recibir_documento_entrega(update, context)


async def callbacks_menu(
    update,
    context,
):
    query=update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()

    data=query.data or ""
    uid=query.from_user.id

    if (
        data.startswith("menu_tareas")
        or data.startswith("menu_atrasadas")
        or data.startswith("menu_hoy")
        or data.startswith("menu_semana")
        or data.startswith("menu_moodle")
        or data.startswith("moodle_")
        or data.startswith("tarea_")
        or data.startswith("entregar_tarea:")
        or data.startswith("curso:")
        or data.startswith("curso_tema:")
    ):
        preparar_usuario_moodle(uid)

    # =====================================================
    # 🏠 MENÚ PRINCIPAL
    # =====================================================
    if data=="menu_principal":
        await enviar_menu_principal(update)
        return

    # =====================================================
    # 📚 MOODLE
    # =====================================================
    if data=="menu_moodle":
        if not await comprobar_acceso(update):
            return

        if tiene_moodle_vinculado(uid):
            texto=(
                "🎓 <b>MOODLE</b>\n\n"
                "✅ Tu cuenta ya está vinculada.\n"
                "KAIRA la utilizará automáticamente."
            )
            teclado=InlineKeyboardMarkup([
                [InlineKeyboardButton("📚 Mis tareas", callback_data="menu_tareas")],
                [InlineKeyboardButton("✅ Cuenta vinculada", callback_data="moodle_estado")],
                [
                    InlineKeyboardButton("🔄 Cambiar cuenta", callback_data="moodle_cambiar"),
                    InlineKeyboardButton("🔓 Desvincular", callback_data="menu_desvincular_moodle"),
                ],
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")],
            ])
        else:
            texto=(
                "🎓 <b>MOODLE</b>\n\n"
                "❌ Aún no tienes una cuenta vinculada.\n\n"
                "Vincúlala una sola vez y KAIRA la recordará."
            )
            teclado=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Vincular Moodle", callback_data="menu_vincular_moodle")],
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")],
            ])

        await editar_menu_callback(update,texto,teclado)
        return

    if data=="moodle_estado":
        await editar_menu_callback(
            update,
            "✅ <b>MOODLE VINCULADO</b>\n\n"
            "No necesitas volver a vincular tu cuenta.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📚 Mis tareas", callback_data="menu_tareas")],
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")],
            ]),
        )
        return

    if data=="menu_vincular_moodle":
        if not await comprobar_acceso(update):
            return

        if tiene_moodle_vinculado(uid):
            await editar_menu_callback(
                update,
                "✅ <b>Ya está vinculado.</b>\n\n"
                "KAIRA ya tiene una cuenta de Moodle asociada.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎓 Moodle", callback_data="menu_moodle")]
                ]),
            )
            return

        guardar_login_pendiente(uid,"usuario")

        await editar_menu_callback(
            update,
            "🎓 <b>VINCULAR MOODLE</b>\n\n"
            "Escribe tu <b>usuario de Moodle</b>.\n\n"
            "Después te pediré la contraseña.\n"
            "🔐 La contraseña no se guardará.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancelar", callback_data="menu_cancelar_moodle")]
            ]),
        )
        return

    if data=="moodle_cambiar":
        if not await comprobar_acceso(update):
            return

        try:
            from moodle_kaira import eliminar_cuenta_moodle_usuario
            eliminar_cuenta_moodle_usuario(uid)
        except Exception as error:
            print("⚠️ Error preparando cambio de Moodle:",error)

        guardar_login_pendiente(uid,"usuario")

        await editar_menu_callback(
            update,
            "🔄 <b>CAMBIAR CUENTA DE MOODLE</b>\n\n"
            "Escribe tu nuevo usuario de Moodle.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancelar", callback_data="menu_cancelar_moodle")]
            ]),
        )
        return

    if data=="menu_cancelar_moodle":
        try:
            borrar_login_pendiente(uid)
        except Exception:
            pass

        await enviar_menu_principal(
            update,
            "❌ Vinculación cancelada.",
        )
        return

    if data=="menu_desvincular_moodle":
        if not await comprobar_acceso(update):
            return

        try:
            from moodle_kaira import eliminar_cuenta_moodle_usuario
            ok=eliminar_cuenta_moodle_usuario(uid)
            texto=(
                "✅ <b>MOODLE DESVINCULADO</b>\n\n"
                "La cuenta fue desvinculada."
                if ok else
                "ℹ️ No había una cuenta de Moodle vinculada."
            )
        except Exception as error:
            print("⚠️ Error desvinculando Moodle:",error)
            texto="❌ No pude desvincular Moodle."

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
            ]),
        )
        return

    # =====================================================
    # 📚 TAREAS
    # =====================================================
    if data=="menu_cursos":
        if not await comprobar_acceso(update):
            return

        preparar_usuario_moodle(uid)

        try:
            from moodle_kaira import obtener_cursos

            cursos=obtener_cursos()

            if not isinstance(
                cursos,
                list
            ):
                raise RuntimeError(
                    "Moodle no devolvió la lista de cursos."
                )

            filas=[]
            lineas=[
                ui_header(
                    "🎓",
                    "MIS CURSOS",
                    "Selecciona una materia para explorar sus temas y actividades."
                ),
                "",
                "📚 <b>Materias disponibles</b>",
                "",
            ]

            for numero,curso in enumerate(
                cursos[:25],
                start=1
            ):
                try:
                    courseid=int(
                        curso.get("id")
                    )
                except Exception:
                    continue

                nombre=str(
                    curso.get(
                        "fullname",
                        "Curso"
                    )
                ).strip()

                # Texto compacto dentro del mensaje.
                lineas.append(
                    f"   {numero:02d} · 🎓 <b>{html.escape(nombre[:65])}</b>"
                )

                # Botón visualmente limpio y totalmente tocable.
                filas.append(
                    [
                        InlineKeyboardButton(
                            f"🎓  {numero:02d} · {nombre[:50]}",
                            callback_data=f"curso:{courseid}",
                        )
                    ]
                )

            if filas:
                lineas.extend(
                    [
                        "",
                        "━━━━━━━━━━━━━━━━━━━━",
                        f"✨ <b>{len(filas)}</b> "
                        f"materia{'s' if len(filas)!=1 else ''} disponible"
                        f"{'s' if len(filas)!=1 else ''}",
                    ]
                )
            else:
                lineas.extend(
                    [
                        "",
                        "✨ <b>No hay materias disponibles</b>",
                        "No se encontraron cursos asociados a tu cuenta de Moodle.",
                    ]
                )

            filas.append(
                [
                    InlineKeyboardButton(
                        "🏠 Menú principal",
                        callback_data="menu_principal",
                    )
                ]
            )

            await editar_menu_callback(
                update,
                "\n".join(lineas),
                InlineKeyboardMarkup(filas),
            )

        except Exception as error:
            print(
                "⚠️ Error cargando cursos:",
                repr(error)
            )

            await editar_menu_callback(
                update,
                ui_header(
                    "⚠️",
                    "MIS CURSOS",
                    "No pude cargar tus materias."
                )
                + "\n\n"
                "Revisa la vinculación y los permisos de Moodle.",
                InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🏠 Menú",
                            callback_data="menu_principal",
                        )
                    ]
                ]),
            )

        return



    if data.startswith("curso:"):
        if not await comprobar_acceso(update):
            return

        preparar_usuario_moodle(uid)

        try:
            courseid=int(
                data.split(
                    ":",
                    1
                )[1]
            )

            from moodle_kaira import obtener_cursos

            cursos=obtener_cursos()
            curso_nombre="Curso"

            if isinstance(cursos,list):
                for curso in cursos:
                    try:
                        if int(
                            curso.get("id")
                        )==courseid:
                            curso_nombre=str(
                                curso.get(
                                    "fullname",
                                    "Curso"
                                )
                            ).strip()
                            break
                    except Exception:
                        continue

            # Una sola consulta al contenido del curso.
            temas=obtener_temas_curso_directo(
                courseid
            )

            if isinstance(temas,dict) and temas.get("error"):
                raise RuntimeError(
                    temas["error"]
                )

            if not isinstance(
                temas,
                list
            ):
                raise RuntimeError(
                    "Moodle no devolvió los temas."
                )

            guardar_cache_temas(
                uid,
                courseid,
                curso_nombre,
                temas,
            )

            filas=[]
            lineas=[
                ui_header(
                    "🎓",
                    curso_nombre,
                    "Selecciona un tema para ver sus actividades."
                ),
                "",
                "📚 <b>TEMAS DEL CURSO</b>",
                "",
            ]

            visibles_temas=0

            for indice,tema in enumerate(
                temas
            ):
                actividades=[
                    a
                    for a in tema.get(
                        "activities",
                        []
                    )
                    if int(
                        a.get(
                            "visible",
                            1
                        ) or 1
                    )!=0
                ]

                if not actividades:
                    continue

                visibles_temas+=1

                nombre_tema=str(
                    tema.get(
                        "name",
                        f"Tema {indice+1}"
                    )
                ).strip()

                section_id=tema.get(
                    "id"
                )

                try:
                    section_id=int(
                        section_id
                    )
                except Exception:
                    section_id=indice

                filas.append(
                    [
                        InlineKeyboardButton(
                            f"📌 {visibles_temas:02d} · {nombre_tema[:48]}",
                            callback_data=(
                                f"curso_tema:{courseid}:{section_id}"
                            ),
                        )
                    ]
                )

                lineas.append(
                    f"• 📌 <b>{nombre_tema}</b> "
                    f"<i>({len(actividades)})</i>"
                )

            if visibles_temas==0:
                lineas.append(
                    "✨ <b>No hay temas con actividades visibles.</b>"
                )
            else:
                lineas.extend(
                    [
                        "",
                        "━━━━━━━━━━━━━━━━━━━━",
                        f"📌 <b>{visibles_temas}</b> "
                        f"tema{'s' if visibles_temas!=1 else ''}",
                    ]
                )

            filas.extend(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Mis cursos",
                            callback_data="menu_cursos",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🏠 Menú principal",
                            callback_data="menu_principal",
                        )
                    ],
                ]
            )

            await editar_menu_callback(
                update,
                "\n".join(lineas),
                InlineKeyboardMarkup(filas),
            )

        except Exception as error:
            print(
                "⚠️ ERROR REAL AL CARGAR TEMAS:",
                repr(error)
            )

            # Para poder diagnosticarlo desde Telegram sin mostrar
            # credenciales ni tokens.
            detalle=str(error).replace(
                "<",
                ""
            ).replace(
                ">",
                ""
            )[:500]

            await editar_menu_callback(
                update,
                "❌ <b>No pude cargar los temas de este curso.</b>\n\n"
                f"🔎 <i>{detalle}</i>\n\n"
                "Revisa la vinculación y los permisos de Moodle.",
                InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⬅️ Mis cursos",
                                callback_data="menu_cursos",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "🏠 Menú principal",
                                callback_data="menu_principal",
                            )
                        ],
                    ]
                ),
            )

        return



    if data.startswith("curso_tema:"):
        if not await comprobar_acceso(update):
            return

        preparar_usuario_moodle(uid)

        try:
            _,course_text,section_text=data.split(
                ":",
                2
            )

            courseid=int(course_text)
            section_id=int(section_text)

            cache=leer_cache_temas(
                uid,
                courseid
            )

            if cache is None:
                from moodle_kaira import obtener_cursos

                cursos=obtener_cursos()
                curso_nombre="Curso"

                if isinstance(cursos,list):
                    for curso in cursos:
                        try:
                            if int(
                                curso.get("id")
                            )==courseid:
                                curso_nombre=str(
                                    curso.get(
                                        "fullname",
                                        "Curso"
                                    )
                                ).strip()
                                break
                        except Exception:
                            continue

                temas=obtener_temas_curso_directo(
                    courseid
                )

                if isinstance(
                    temas,
                    dict
                ) and temas.get("error"):
                    raise RuntimeError(
                        temas.get(
                            "error",
                            "Error consultando Moodle."
                        )
                    )

                guardar_cache_temas(
                    uid,
                    courseid,
                    curso_nombre,
                    temas,
                )

                cache=leer_cache_temas(
                    uid,
                    courseid
                )

            if not cache:
                raise RuntimeError(
                    "No hay información del curso."
                )

            temas=cache.get(
                "temas",
                []
            )

            curso_nombre=cache.get(
                "curso_nombre",
                "Curso"
            )

            tema_seleccionado=None

            for indice,tema in enumerate(
                temas
            ):
                try:
                    actual_id=int(
                        tema.get(
                            "id"
                        )
                    )
                except Exception:
                    actual_id=indice

                if (
                    actual_id==section_id
                    or indice==section_id
                ):
                    tema_seleccionado=tema
                    break

            if not tema_seleccionado:
                raise RuntimeError(
                    "No encontré ese tema."
                )

            nombre_tema=str(
                tema_seleccionado.get(
                    "name",
                    "Tema"
                )
            ).strip()

            actividades=[
                a
                for a in tema_seleccionado.get(
                    "activities",
                    []
                )
                if int(
                    a.get(
                        "visible",
                        1
                    ) or 1
                )!=0
            ]

            lineas=[
                ui_header(
                    "📌",
                    nombre_tema,
                    curso_nombre,
                ),
                "",
                "📝 <b>ACTIVIDADES DEL TEMA</b>",
                "",
            ]

            filas=[]
            total=0

            for actividad in actividades:
                nombre=str(
                    actividad.get(
                        "name",
                        "Actividad"
                    )
                ).strip()

                modname=str(
                    actividad.get(
                        "modname",
                        ""
                    )
                ).lower()

                instance=actividad.get(
                    "instance"
                )

                if (
                    modname=="assign"
                    and instance is not None
                ):
                    assignid=int(
                        instance
                    )

                    filas.append(
                        [
                            InlineKeyboardButton(
                                f"📝 {nombre[:50]}",
                                callback_data=(
                                    f"tarea_detalle:{assignid}"
                                ),
                            )
                        ]
                    )

                    lineas.append(
                        f"• 📝 {nombre}"
                    )

                else:
                    url=(
                        actividad.get(
                            "url"
                        )
                        or ""
                    )

                    lineas.append(
                        f"• 🔗 {nombre}"
                    )

                    if url:
                        filas.append(
                            [
                                InlineKeyboardButton(
                                    f"🔗 {nombre[:50]}",
                                    url=url,
                                )
                            ]
                        )

                total+=1

            if total==0:
                lineas.append(
                    "✨ <b>No hay actividades visibles.</b>"
                )
            else:
                lineas.extend(
                    [
                        "",
                        "━━━━━━━━━━━━━━━━━━━━",
                        f"📚 <b>{total}</b> "
                        f"actividad"
                        f"{'' if total==1 else 'es'}",
                    ]
                )

            filas.extend(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Temas del curso",
                            callback_data=f"curso:{courseid}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🎓 Mis cursos",
                            callback_data="menu_cursos",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🏠 Menú principal",
                            callback_data="menu_principal",
                        )
                    ],
                ]
            )

            await editar_menu_callback(
                update,
                "\n".join(lineas),
                InlineKeyboardMarkup(filas),
            )

        except Exception as error:
            print(
                "⚠️ ERROR REAL AL CARGAR TEMA:",
                repr(error)
            )

            await editar_menu_callback(
                update,
                "❌ <b>No pude cargar las actividades de este tema.</b>\n\n"
                f"🔎 <i>{str(error)[:500]}</i>",
                InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ Volver al curso",
                            callback_data=f"curso:{courseid}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🏠 Menú principal",
                            callback_data="menu_principal",
                        )
                    ],
                ]),
            )

        return



    if data=="menu_tareas":
        await mostrar_lista_tareas_botones(update,"tareas")
        return

    if data=="menu_atrasadas":
        await mostrar_lista_tareas_botones(update,"atrasadas")
        return

    if data=="menu_hoy":
        await mostrar_lista_tareas_botones(update,"hoy")
        return

    if data=="menu_semana":
        await mostrar_lista_tareas_botones(update,"semana")
        return

    if data.startswith("moodle_archivo:"):
        try:
            _,assign_text,indice_text=data.split(
                ":",
                2,
            )
            assignid=int(assign_text)
            indice=int(indice_text)

            await enviar_adjunto_moodle_callback(
                update,
                context,
                assignid,
                indice,
            )
        except Exception as error:
            print(
                "⚠️ Callback archivo Moodle:",
                error,
            )
            try:
                await query.message.reply_text(
                    "❌ No pude abrir ese archivo."
                )
            except Exception:
                pass
        return

    if data.startswith("feedback_archivo:"):
        try:
            _,assign_text,indice_text=data.split(":",2)
            await enviar_feedback_archivo_callback(
                update,
                context,
                int(assign_text),
                int(indice_text),
            )
        except Exception as error:
            print("⚠️ feedback_archivo:",error)
            try:
                await query.message.reply_text(
                    "❌ No pude abrir el archivo de retroalimentación."
                )
            except Exception:
                pass
        return

    if data.startswith("feedback_detalle:"):
        try:
            assignid=int(data.split(":",1)[1])
            await mostrar_retroalimentacion_tarea(
                update,
                assignid,
            )
        except Exception as error:
            print("⚠️ feedback_detalle:",error)
            await editar_menu_callback(
                update,
                "❌ No pude cargar la retroalimentación.",
            )
        return

    if data.startswith("historial_tarea:"):
        try:
            assignid=int(data.split(":",1)[1])
            await mostrar_historial_tarea(
                update,
                assignid,
            )
        except Exception as error:
            print("⚠️ historial_tarea:",error)
            await editar_menu_callback(
                update,
                "❌ No pude cargar el historial.",
            )
        return

    if data.startswith("tarea_detalle:"):
        try:
            assignid=int(data.split(":",1)[1])
            await mostrar_detalle_tarea_boton(update,assignid)
        except Exception as error:
            print("⚠️ Detalle tarea:",error)
            await editar_menu_callback(
                update,
                "❌ No pude abrir el detalle de la tarea.",
            )
        return

    if data.startswith("entregar_tarea:"):
        try:
            assignid=int(data.split(":",1)[1])
            await iniciar_entrega_tarea(update,assignid)
        except Exception as error:
            print("⚠️ Iniciar entrega:",error)
            await editar_menu_callback(
                update,
                "❌ No pude iniciar la entrega.",
            )
        return

    if data=="cancelar_entrega":
        quitar_entrega_pendiente(uid)
        await enviar_menu_principal(update,"❌ Entrega cancelada.")
        return

    # =====================================================
    # ⏰ RECORDATORIOS
    # =====================================================
    if data=="menu_recordatorios":
        if not await comprobar_acceso(update):
            return

        propios=[
            r for r in cargar_recordatorios()
            if r.get("chat_id")==query.message.chat_id
        ]

        if not propios:
            texto="⏰ <b>RECORDATORIOS</b>\n\nNo tienes recordatorios pendientes."
        else:
            lineas=["⏰ <b>RECORDATORIOS</b>",""]
            for r in propios[:20]:
                lineas.append(
                    f"🔔 {r.get('tarea','Sin descripción')}\n"
                    f"📅 {r.get('fecha_hora','')}"
                )
            texto="\n\n".join(lineas)

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
            ]),
        )
        return

    # =====================================================
    # 👤 CUENTA / MEMBRESÍA / HISTORIAL
    # =====================================================
    if data=="menu_mi_cuenta":
        if not await comprobar_acceso(update):
            return

        try:
            from membresias import obtener_usuario
            cuenta=obtener_usuario(uid)
        except Exception:
            cuenta=None

        if not cuenta:
            texto=(
                "👤 <b>MI CUENTA</b>\n\n"
                f"🆔 Telegram: <code>{uid}</code>\n"
                "No tienes una membresía registrada."
            )
        else:
            texto=(
                "👤 <b>MI CUENTA</b>\n\n"
                f"Nombre: <b>{cuenta.get('nombre') or query.from_user.full_name}</b>\n"
                f"🆔 Telegram: <code>{uid}</code>\n"
                f"🎓 Moodle: <b>{'✅ Vinculado' if tiene_moodle_vinculado(uid) else '❌ No vinculado'}</b>\n\n"
                f"📦 Membresía: <b>{cuenta.get('tipo_membresia') or 'No definida'}</b>\n"
                f"💰 Precio: <b>${float(cuenta.get('precio') or 0):.2f}</b>\n"
                f"📅 Inicio: <b>{cuenta.get('fecha_inicio') or 'No disponible'}</b>\n"
                f"⏳ Vencimiento: <b>{cuenta.get('fecha_vencimiento') or 'No disponible'}</b>\n"
                f"💳 Pago: <b>{'Pagado' if int(cuenta.get('pagado',0) or 0) else 'No pagado'}</b>\n"
                f"🟢 Estado: <b>{cuenta.get('estado') or 'No definido'}</b>"
            )

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Historial", callback_data="menu_historial")],
                [InlineKeyboardButton("🎓 Moodle", callback_data="menu_moodle")],
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")],
            ]),
        )
        return

    if data=="menu_membresia":
        if not await comprobar_acceso(update):
            return

        try:
            from membresias import obtener_usuario
            cuenta=obtener_usuario(uid)
        except Exception:
            cuenta=None

        if not cuenta:
            texto="💳 <b>MI MEMBRESÍA</b>\n\nNo tienes una membresía registrada."
        else:
            texto=(
                "💳 <b>MI MEMBRESÍA</b>\n\n"
                f"📦 Plan: <b>{cuenta.get('tipo_membresia') or 'No definido'}</b>\n"
                f"💰 Precio: <b>${float(cuenta.get('precio') or 0):.2f}</b>\n"
                f"📅 Inicio: <b>{cuenta.get('fecha_inicio') or 'No disponible'}</b>\n"
                f"⏳ Vencimiento: <b>{cuenta.get('fecha_vencimiento') or 'No disponible'}</b>\n"
                f"💳 Pago: <b>{'Pagado' if int(cuenta.get('pagado',0) or 0) else 'No pagado'}</b>\n"
                f"🟢 Estado: <b>{cuenta.get('estado') or 'No definido'}</b>"
            )

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔄 Renovar", callback_data="menu_renovar"),
                    InlineKeyboardButton("💳 Historial", callback_data="menu_historial"),
                ],
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")],
            ]),
        )
        return

    if data=="menu_historial":
        if not await comprobar_acceso(update):
            return

        try:
            from membresias import historial_pagos, resumen_pagos
            pagos=historial_pagos(uid,20)
            resumen=resumen_pagos(uid)

            if not pagos:
                texto="💳 <b>HISTORIAL</b>\n\nTodavía no hay pagos registrados."
            else:
                lineas=["💳 <b>HISTORIAL DE PAGOS</b>",""]
                for pago in reversed(pagos):
                    lineas.append(
                        f"📅 {pago['fecha_pago']}\n"
                        f"📦 {pago['plan'] or 'Sin plan'}\n"
                        f"💰 ${float(pago['precio'] or 0):.2f}\n"
                        f"💵 {pago['metodo_pago'] or 'Sin método'}"
                    )
                lineas.append(
                    f"\n📊 Pagos: <b>{resumen['cantidad']}</b>\n"
                    f"💰 Total: <b>${resumen['total']:.2f}</b>"
                )
                texto="\n\n".join(lineas)
        except Exception as error:
            print("⚠️ Historial:",error)
            texto="❌ No pude consultar el historial."

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
            ]),
        )
        return

    # =====================================================
    # 🔄 RENOVAR
    # =====================================================
    if data=="menu_renovar":
        if usuario_admin(uid):
            await editar_menu_callback(
                update,
                "👑 <b>ADMINISTRADOR</b>\n\nLa cuenta del administrador no utiliza renovación.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
                ]),
            )
            return

        if not await comprobar_acceso(update):
            return

        if tiene_renovacion_pendiente(uid):
            texto="⏳ <b>RENOVACIÓN PENDIENTE</b>\n\nYa existe una solicitud pendiente."
        else:
            registrar_renovacion_pendiente(query.from_user)

            try:
                from membresias import obtener_usuario
                mem=obtener_usuario(uid) or {}
            except Exception:
                mem={}

            admin_id=_id_admin()

            if not admin_id:
                quitar_renovacion_pendiente(uid)
                texto="⚠️ No se encontró al administrador."
            else:
                texto_admin=(
                    "🔔 <b>SOLICITUD DE RENOVACIÓN</b>\n\n"
                    f"👤 <b>{query.from_user.full_name}</b>\n"
                    f"🆔 <code>{uid}</code>\n"
                    f"📦 Plan actual: <b>{mem.get('tipo_membresia') or 'No definido'}</b>\n"
                    f"💰 Precio actual: <b>${float(mem.get('precio') or 0):.2f}</b>\n"
                    f"⏳ Vencimiento: <b>{mem.get('fecha_vencimiento') or 'Sin fecha'}</b>\n\n"
                    "<b>No se cambiará nada hasta que el administrador apruebe.</b>"
                )

                teclado_admin=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ APROBAR", callback_data=f"renovacion_aprobar:{uid}"),
                    InlineKeyboardButton("❌ RECHAZAR", callback_data=f"renovacion_rechazar:{uid}"),
                ]])

                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=texto_admin,
                        parse_mode="HTML",
                        reply_markup=teclado_admin,
                    )
                    texto=(
                        "✅ <b>Solicitud enviada.</b>\n\n"
                        "El administrador recibió tu solicitud.\n"
                        "La membresía no se renovará automáticamente."
                    )
                except Exception as error:
                    print("⚠️ Renovación:",error)
                    quitar_renovacion_pendiente(uid)
                    texto="❌ No pude enviar la solicitud."

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
            ]),
        )
        return

    # =====================================================
    # 🧠 IA
    # =====================================================
    if data=="menu_ia":
        if not await comprobar_acceso(update):
            return

        await editar_menu_callback(
            update,
            "🧠 <b>AYUDA CON IA</b>\n\n"
            "Escríbeme directamente lo que necesites.\n\n"
            "Ejemplos:\n"
            "• Explícame una tarea\n"
            "• Ayúdame con un ejercicio\n"
            "• Hazme un resumen\n"
            "• Explícame este tema",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
            ]),
        )
        return

    # =====================================================
    # 🔔 RESUMEN DIARIO
    # =====================================================
    if data=="menu_calendario":
        if not await comprobar_acceso(update):
            return

        preparar_usuario_moodle(uid)

        ahora=datetime.now()
        tareas=_obtener_tareas_calendario()

        texto=_calendario_mes_texto(
            ahora.year,
            ahora.month,
            tareas,
        )

        await editar_menu_callback(
            update,
            texto,
            teclado_calendario_mes(
                ahora.year,
                ahora.month,
            ),
        )
        return

    if data.startswith("cal_mes:"):
        if not await comprobar_acceso(update):
            return

        preparar_usuario_moodle(uid)

        try:
            _,year_text,month_text=data.split(":")
            year=int(year_text)
            month=int(month_text)

            if month<1 or month>12:
                raise ValueError

            tareas=_obtener_tareas_calendario()

            texto=_calendario_mes_texto(
                year,
                month,
                tareas,
            )

            await editar_menu_callback(
                update,
                texto,
                teclado_calendario_mes(
                    year,
                    month,
                ),
            )
        except Exception as error:
            print("⚠️ Calendario mensual:",error)
            await editar_menu_callback(
                update,
                "❌ No pude cargar ese mes.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🏠 Menú",
                        callback_data="menu_principal",
                    )]
                ]),
            )
        return

    if data=="cal_semana":
        if not await comprobar_acceso(update):
            return

        preparar_usuario_moodle(uid)

        ahora=datetime.now()
        tareas=_obtener_tareas_calendario()

        texto=_calendario_semana_texto(
            ahora,
            tareas,
        )

        inicio=ahora.date()
        inicio=inicio.fromordinal(
            inicio.toordinal()-inicio.weekday()
        )
        fin=inicio.fromordinal(
            inicio.toordinal()+6
        )

        botones_tareas=[]

        for tarea in sorted(
            tareas,
            key=lambda x: x.get("duedate",0) or 0
        ):
            fecha=_fecha_tarea_timestamp(tarea)

            if not fecha or not (
                inicio <= fecha.date() <= fin
            ):
                continue

            try:
                assignid=int(
                    tarea.get("id")
                )
            except Exception:
                continue

            botones_tareas.append(
                [
                    InlineKeyboardButton(
                        f"{_icono_estado_calendario(tarea,ahora)} "
                        f"{str(tarea.get('name','Actividad'))[:48]}",
                        callback_data=f"tarea_detalle:{assignid}",
                    )
                ]
            )

        botones_tareas.extend(
            teclado_calendario_semana(
                ahora
            ).inline_keyboard
        )

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup(
                botones_tareas
            ),
        )
        return

    if data.startswith("cal_semana:"):
        if not await comprobar_acceso(update):
            return

        preparar_usuario_moodle(uid)

        try:
            fecha=data.split(":",1)[1]
            dt=datetime.strptime(
                fecha,
                "%Y-%m-%d"
            )

            tareas=_obtener_tareas_calendario()

            texto=_calendario_semana_texto(
                dt,
                tareas,
            )

            inicio=dt.date()
            inicio=inicio.fromordinal(
                inicio.toordinal()-inicio.weekday()
            )
            fin=inicio.fromordinal(
                inicio.toordinal()+6
            )

            botones_tareas=[]

            for tarea in sorted(
                tareas,
                key=lambda x: x.get("duedate",0) or 0
            ):
                fecha_tarea=_fecha_tarea_timestamp(tarea)

                if not fecha_tarea or not (
                    inicio <= fecha_tarea.date() <= fin
                ):
                    continue

                try:
                    assignid=int(
                        tarea.get("id")
                    )
                except Exception:
                    continue

                botones_tareas.append(
                    [
                        InlineKeyboardButton(
                            f"{_icono_estado_calendario(tarea,dt)} "
                            f"{str(tarea.get('name','Actividad'))[:48]}",
                            callback_data=f"tarea_detalle:{assignid}",
                        )
                    ]
                )

            botones_tareas.extend(
                teclado_calendario_semana(
                    dt
                ).inline_keyboard
            )

            await editar_menu_callback(
                update,
                texto,
                InlineKeyboardMarkup(
                    botones_tareas
                ),
            )
        except Exception as error:
            print("⚠️ Calendario semanal:",error)
            await editar_menu_callback(
                update,
                "❌ No pude cargar esa semana.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🏠 Menú",
                        callback_data="menu_principal",
                    )]
                ]),
            )
        return

    if data.startswith("cal_lista:"):
        if not await comprobar_acceso(update):
            return

        preparar_usuario_moodle(uid)

        try:
            _,year_text,month_text=data.split(":")
            year=int(year_text)
            month=int(month_text)

            tareas=_obtener_tareas_calendario()

            items=[
                x for x in tareas
                if (
                    _fecha_tarea_timestamp(x)
                    and _fecha_tarea_timestamp(x).year==year
                    and _fecha_tarea_timestamp(x).month==month
                )
            ]

            items.sort(
                key=lambda x: x.get(
                    "duedate",
                    0
                ) or 0
            )

            if not items:
                texto=(
                    f"📋 <b>TAREAS DE {calendar.month_name[month].upper()} {year}</b>\n\n"
                    "🟢 No encontré tareas para este mes."
                )
            else:
                lineas=[
                    f"📋 <b>TAREAS DE {calendar.month_name[month].upper()} {year}</b>",
                    "",
                ]

                ahora=datetime.now()

                for tarea in items:
                    fecha=_fecha_tarea_timestamp(
                        tarea
                    )
                    icono=_icono_estado_calendario(
                        tarea,
                        ahora
                    )

                    lineas.append(
                        f"{icono} <b>{str(tarea.get('name','Actividad'))[:55]}</b>\n"
                        f"📅 {fecha.strftime('%d/%m/%Y %H:%M') if fecha else 'Sin fecha'}\n"
                        f"📚 {tarea.get('curso_nombre','Curso')}"
                    )
                    lineas.append("")

                texto="\n".join(lineas)

            # Cada actividad del mes es tocable y abre sus detalles.
            botones_tareas=[]

            for tarea in items:
                try:
                    assignid=int(
                        tarea.get("id")
                    )
                except Exception:
                    continue

                nombre_boton=str(
                    tarea.get(
                        "name",
                        "Actividad"
                    )
                )[:48]

                botones_tareas.append(
                    [
                        InlineKeyboardButton(
                            f"{_icono_estado_calendario(tarea, ahora)} {nombre_boton}",
                            callback_data=f"tarea_detalle:{assignid}",
                        )
                    ]
                )

            botones_tareas.append(
                [
                    InlineKeyboardButton(
                        "📆 Calendario",
                        callback_data=f"cal_mes:{year}:{month}",
                    )
                ]
            )

            botones_tareas.append(
                [
                    InlineKeyboardButton(
                        "🏠 Menú",
                        callback_data="menu_principal",
                    )
                ]
            )

            await editar_menu_callback(
                update,
                texto,
                InlineKeyboardMarkup(
                    botones_tareas
                ),
            )
        except Exception as error:
            print("⚠️ Lista calendario:",error)
            await editar_menu_callback(
                update,
                "❌ No pude cargar las tareas de ese mes.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🏠 Menú",
                        callback_data="menu_principal",
                    )]
                ]),
            )
        return

    if data=="menu_resumen":
        if not await comprobar_acceso(update):
            return

        await editar_menu_callback(
            update,
            "🔔 <b>RESUMEN ACADÉMICO DIARIO</b>\n\nElige una hora:",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("06:30", callback_data="resumen_hora:06:30"),
                    InlineKeyboardButton("07:00", callback_data="resumen_hora:07:00"),
                ],
                [
                    InlineKeyboardButton("08:00", callback_data="resumen_hora:08:00"),
                    InlineKeyboardButton("09:00", callback_data="resumen_hora:09:00"),
                ],
                [InlineKeyboardButton("🛑 Desactivar", callback_data="resumen_desactivar")],
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")],
            ]),
        )
        return

    if data.startswith("resumen_hora:"):
        if not await comprobar_acceso(update):
            return

        hora_texto=data.split(":",1)[1]

        try:
            hora,minuto=map(int,hora_texto.split(":"))

            if context.job_queue is None:
                raise RuntimeError("JobQueue no disponible")

            ok=programar_resumen_usuario(
                context.job_queue,
                uid,
                query.message.chat_id,
                hora,
                minuto,
            )

            if not ok:
                raise RuntimeError("No se pudo programar")

            guardar_configuracion_resumen(
                uid,
                query.message.chat_id,
                hora,
                minuto,
            )

            texto=f"✅ <b>Resumen activado.</b>\n\n🌅 Hora: <b>{hora_texto}</b>"
        except Exception as error:
            print("⚠️ Resumen:",error)
            texto="❌ No pude programar el resumen."

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
            ]),
        )
        return

    if data=="resumen_desactivar":
        if not await comprobar_acceso(update):
            return

        try:
            if context.job_queue is not None:
                for job in context.job_queue.get_jobs_by_name(
                    _nombre_job_resumen(uid)
                ):
                    job.schedule_removal()

            eliminar_configuracion_resumen(uid)
            texto="🛑 <b>Resumen diario desactivado.</b>"
        except Exception as error:
            print("⚠️ Desactivar resumen:",error)
            texto="❌ No pude desactivar el resumen."

        await editar_menu_callback(
            update,
            texto,
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
            ]),
        )
        return

    # =====================================================
    # 👑 ADMINISTRACIÓN
    # =====================================================
    if data.startswith("admin_"):
        if not usuario_admin(uid):
            await editar_menu_callback(update,"⛔ No tienes permiso.")
            return

        if data=="admin_usuarios":
            try:
                usuarios=cargar_usuarios_autorizados()
                lineas=["👥 <b>USUARIOS</b>",""]

                if not usuarios:
                    lineas.append("No hay usuarios autorizados.")
                else:
                    for n,user_id in enumerate(sorted(usuarios),1):
                        reg=usuarios[user_id]
                        nombre=nombre_visible_usuario(reg)

                        try:
                            from membresias import obtener_usuario
                            mem=obtener_usuario(user_id) or {}
                        except Exception:
                            mem={}

                        moodle="✅" if tiene_moodle_vinculado(user_id) else "❌"

                        lineas.append(
                            f"{n}. 👤 <b>{nombre}</b>\n"
                            f"🆔 <code>{user_id}</code>\n"
                            f"🎓 Moodle: {moodle}\n"
                            f"📦 {mem.get('tipo_membresia') or 'Sin plan'}\n"
                            f"🔐 {mem.get('estado') or 'Sin estado'}"
                        )
                        lineas.append("")
                texto="\n".join(lineas)
            except Exception as error:
                print("⚠️ admin_usuarios:",error)
                texto="❌ No pude cargar los usuarios."

            await editar_menu_callback(
                update,
                texto,
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Actualizar", callback_data="admin_usuarios")],
                    [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")],
                ]),
            )
            return

        if data=="admin_resumen":
            try:
                from membresias import resumen_admin
                r=resumen_admin(excluir_telegram_id=uid)
                texto=(
                    "📊 <b>KAIRA ADMIN</b>\n\n"
                    f"👥 Usuarios: <b>{r['total']}</b>\n"
                    f"🟢 Activos: <b>{r['activos']}</b>\n"
                    f"🟡 Por vencer: <b>{r['por_vencer_7_dias']}</b>\n"
                    f"🔴 Vencidos: <b>{r['vencidos']}</b>\n\n"
                    f"💳 Pagados: <b>{r['pagados']}</b>\n"
                    f"❌ Pendientes: <b>{r['pendientes']}</b>\n"
                    f"💰 Ingresos: <b>${r['ingresos']:.2f}</b>"
                )
            except Exception as error:
                print("⚠️ admin_resumen:",error)
                texto="❌ No pude generar el resumen."

            await editar_menu_callback(
                update,
                texto,
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Actualizar", callback_data="admin_resumen")],
                    [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")],
                ]),
            )
            return

        if data=="admin_buscar":
            await editar_menu_callback(
                update,
                "🔎 <b>BUSCAR USUARIO</b>\n\n"
                "Escribe:\n"
                "<code>/buscar Erick</code>\n"
                "<code>/buscar 5571546711</code>",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
                ]),
            )
            return

        if data=="admin_pagos":
            try:
                from membresias import resumen_pagos
                r=resumen_pagos()
                texto=(
                    "💳 <b>PAGOS</b>\n\n"
                    f"✅ Movimientos: <b>{r['cantidad']}</b>\n"
                    f"💰 Ingresos: <b>${r['total']:.2f}</b>"
                )
            except Exception as error:
                print("⚠️ admin_pagos:",error)
                texto="❌ No pude consultar los pagos."

            await editar_menu_callback(
                update,
                texto,
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
                ]),
            )
            return

        if data=="admin_vencimientos":
            try:
                from membresias import obtener_alertas_vencimiento
                alertas=obtener_alertas_vencimiento(dias_max=7)

                if not alertas:
                    texto="🟢 No hay vencimientos en los próximos 7 días."
                else:
                    lineas=["🟡 <b>VENCIMIENTOS</b>",""]
                    for x in alertas:
                        dias=x.get("dias",0)
                        estado=(
                            "🔴 VENCIDA" if dias<0
                            else "🔴 VENCE HOY" if dias==0
                            else "🟠 VENCE MAÑANA" if dias==1
                            else f"🟡 {dias} día(s)"
                        )
                        lineas.append(
                            f"👤 <b>{x.get('nombre') or 'Sin nombre'}</b>\n"
                            f"🆔 <code>{x.get('telegram_user_id')}</code>\n"
                            f"{estado}\n"
                            f"📅 {x.get('fecha_vencimiento','')}"
                        )
                        lineas.append("")
                    texto="\n".join(lineas)
            except Exception as error:
                print("⚠️ admin_vencimientos:",error)
                texto="❌ No pude consultar los vencimientos."

            await editar_menu_callback(
                update,
                texto,
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Actualizar", callback_data="admin_vencimientos")],
                    [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")],
                ]),
            )
            return

        if data=="admin_precios":
            try:
                from membresias import listar_precios_planes
                p=listar_precios_planes()
                texto=(
                    "💰 <b>PRECIOS</b>\n\n"
                    "🎁 Prueba gratuita: <b>5 días · GRATIS</b>\n\n"
                    f"📅 Semanal: <b>${p.get('Semanal',0):.2f}</b>\n"
                    f"📆 Mensual: <b>${p.get('Mensual',0):.2f}</b>\n"
                    f"📊 Trimestral: <b>${p.get('Trimestral',0):.2f}</b>\n"
                    f"🗓️ Semestral: <b>${p.get('Semestral',0):.2f}</b>\n"
                    f"📚 Anual: <b>${p.get('Anual',0):.2f}</b>"
                )
            except Exception as error:
                print("⚠️ admin_precios:",error)
                texto="❌ No pude consultar los precios."

            await editar_menu_callback(
                update,
                texto,
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Menú", callback_data="menu_principal")]
                ]),
            )
            return


# =========================================================
# /START
# =========================================================

async def comando_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await comprobar_acceso(update):
        return

    await enviar_menu_principal(
        update
    )



# =========================================================
# 📋 MOSTRAR RECORDATORIOS
# =========================================================

async def mostrar_recordatorios(update):

    chat_id = update.effective_chat.id

    recordatorios = [
        r
        for r in cargar_recordatorios()
        if r.get("chat_id") == chat_id
    ]

    if not recordatorios:

        await update.message.reply_text(
            "📋 No tienes recordatorios pendientes."
        )

        return

    lineas = [
        "⏰ TUS RECORDATORIOS"
    ]

    for numero, recordatorio in enumerate(
        recordatorios,
        start=1,
    ):

        try:
            fecha_hora = datetime.fromisoformat(
                recordatorio["fecha_hora"]
            )

            fecha_texto = fecha_hora.strftime(
                "%d/%m/%Y %H:%M"
            )

        except Exception:
            fecha_texto = "Fecha no disponible"

        tarea = recordatorio.get(
            "tarea",
            "Sin descripción",
        )

        lineas.append(
            f"\n{numero}. 🔔 {tarea}\n"
            f"   📅 {fecha_texto}"
        )

    await update.message.reply_text(
        "\n".join(lineas)
    )


# =========================================================
# 🗑️ CANCELAR RECORDATORIOS
# =========================================================

async def cancelar_recordatorios(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    texto: str,
):

    chat_id = update.effective_chat.id

    recordatorios = cargar_recordatorios()

    propios = [
        r
        for r in recordatorios
        if r.get("chat_id") == chat_id
    ]

    if not propios:

        await update.message.reply_text(
            "📋 No tienes recordatorios pendientes."
        )

        return

    # =====================================================
    # CANCELAR TODOS
    # =====================================================

    if (
        "cancela todos" in texto
        or "cancelar todos" in texto
        or "elimina todos" in texto
        or "eliminar todos" in texto
        or "quita todos" in texto
        or "quitar todos" in texto
    ):

        if context.job_queue is not None:

            for recordatorio in propios:

                recordatorio_id = recordatorio["id"]

                jobs = (
                    context.job_queue
                    .get_jobs_by_name(
                        recordatorio_id
                    )
                )

                for job in jobs:
                    job.schedule_removal()

        nuevos = [
            r
            for r in recordatorios
            if r.get("chat_id") != chat_id
        ]

        guardar_recordatorios(nuevos)

        await update.message.reply_text(
            "🗑️ Listo. "
            "Cancelé todos tus recordatorios."
        )

        print(
            "🗑️ Todos los recordatorios "
            "del usuario fueron cancelados."
        )

        return

    # =====================================================
    # CANCELAR POR TEXTO
    # =====================================================

    palabras_cancelar = [
        "cancela el recordatorio de",
        "cancelar el recordatorio de",
        "cancela recordatorio de",
        "cancelar recordatorio de",
        "elimina el recordatorio de",
        "eliminar el recordatorio de",
        "elimina recordatorio de",
        "eliminar recordatorio de",
        "quita el recordatorio de",
        "quitar el recordatorio de",
        "quita recordatorio de",
        "quitar recordatorio de",
    ]

    tarea_buscada = None

    for inicio in palabras_cancelar:

        if texto.startswith(inicio):

            tarea_buscada = (
                texto[len(inicio):]
                .strip()
            )

            break

    if not tarea_buscada:

        await update.message.reply_text(
            "🤔 Dime qué recordatorio "
            "quieres cancelar.\n\n"
            "Ejemplo:\n"
            "Kaira, cancela el recordatorio de estudiar"
        )

        return

    tarea_buscada = (
        tarea_buscada
        .replace("kaira", "")
        .strip(" ,.")
    )

    encontrado = None

    for recordatorio in propios:

        tarea = (
            recordatorio.get(
                "tarea",
                "",
            )
            .lower()
        )

        if tarea_buscada.lower() in tarea:

            encontrado = recordatorio
            break

    if encontrado is None:

        await update.message.reply_text(
            "❌ No encontré un recordatorio "
            f"relacionado con: {tarea_buscada}"
        )

        return

    recordatorio_id = encontrado["id"]

    if context.job_queue is not None:

        jobs = (
            context.job_queue
            .get_jobs_by_name(
                recordatorio_id
            )
        )

        for job in jobs:
            job.schedule_removal()

    nuevos = [
        r
        for r in recordatorios
        if r.get("id") != recordatorio_id
    ]

    guardar_recordatorios(nuevos)

    await update.message.reply_text(
        "🗑️ Listo. Cancelé el recordatorio:\n"
        f"🔔 {encontrado['tarea']}"
    )

    print(
        "🗑️ Recordatorio cancelado:",
        encontrado["tarea"],
    )


# =========================================================
# 📱 RECIBIR MENSAJES
# =========================================================

async def recibir_mensaje(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global ultimo_chat_id

    if not update.message:
        return

    if not await comprobar_acceso(update):
        return

    # Si el usuario está en el flujo de vinculación de Moodle,
    # este mensaje debe ser atendido por ese flujo y no pasar
    # al cerebro general.
    if await recibir_credencial_moodle(
        update,
        context
    ):
        return

    if await procesar_seleccion_plan_autorizacion(
        update,
        context,
        update.message.text or ""
    ):
        return

    ultimo_chat_id = update.effective_chat.id

    guardar_ultimo_chat(
    	ultimo_chat_id
    )

    mensaje = update.message.text

    if not mensaje:
        return

    mensaje = mensaje.strip()

    print(
        f"\n📱 Telegram → KAIRA: {mensaje}"
    )

    texto_recordatorio = (
        mensaje
        .lower()
        .strip()
    )

    texto_comando = re.sub(
        r"^kaira[\s,]*",
        "",
        texto_recordatorio,
    ).strip()

    # =====================================================
    # 📁 ARCHIVOS EXISTENTES
    # =====================================================

    archivo_procesado = (
        await procesar_peticion_archivo(
            update,
            mensaje,
        )
    )

    if archivo_procesado:
        return

    # =====================================================
    # 🛠️ GENERAR ARCHIVO
    # =====================================================
    resultado_generacion = await asyncio.to_thread(
        procesar_peticion_generacion,
        mensaje,
    )

    if resultado_generacion is not None:
        if "error" in resultado_generacion:
            await update.message.reply_text(
                "❌ No pude generar el archivo.\n\n"
                f"Error: {resultado_generacion['error']}"
            )
            return

        ruta = resultado_generacion["ruta"]
        nombre = resultado_generacion["nombre"]

        await update.message.reply_text(
            "✅ Archivo generado.\n\n"
            f"📄 {nombre}\n\n"
            "📤 Te lo estoy enviando..."
        )

        enviado = await enviar_archivo_telegram(update, ruta)

        if enviado:
            await update.message.reply_text(
                "✅ Listo. Ya tienes el archivo."
            )
        return

    # =====================================================
    # 📋 CONSULTAR RECORDATORIOS
    # =====================================================

    if (
        "qué recordatorios tengo" in texto_comando
        or "que recordatorios tengo" in texto_comando
        or "mis recordatorios" in texto_comando
        or "muéstrame mis recordatorios" in texto_comando
        or "muestrame mis recordatorios" in texto_comando
        or "mostrar mis recordatorios" in texto_comando
        or "qué tengo pendiente" in texto_comando
        or "que tengo pendiente" in texto_comando
    ):

        await mostrar_recordatorios(update)

        return

    # =====================================================
    # 🗑️ CANCELAR RECORDATORIOS
    # =====================================================

    if (
        "cancela todos" in texto_comando
        or "cancelar todos" in texto_comando
        or "elimina todos" in texto_comando
        or "eliminar todos" in texto_comando
        or "quita todos" in texto_comando
        or "quitar todos" in texto_comando
        or texto_comando.startswith(
            "cancela el recordatorio de"
        )
        or texto_comando.startswith(
            "cancelar el recordatorio de"
        )
        or texto_comando.startswith(
            "cancela recordatorio de"
        )
        or texto_comando.startswith(
            "cancelar recordatorio de"
        )
        or texto_comando.startswith(
            "elimina el recordatorio de"
        )
        or texto_comando.startswith(
            "eliminar el recordatorio de"
        )
        or texto_comando.startswith(
            "elimina recordatorio de"
        )
        or texto_comando.startswith(
            "eliminar recordatorio de"
        )
        or texto_comando.startswith(
            "quita el recordatorio de"
        )
        or texto_comando.startswith(
            "quitar el recordatorio de"
        )
        or texto_comando.startswith(
            "quita recordatorio de"
        )
        or texto_comando.startswith(
            "quitar recordatorio de"
        )
    ):

        await cancelar_recordatorios(
            update,
            context,
            texto_comando,
        )

        return

    # =====================================================
    # ⏰ RECORDATORIO RELATIVO
    # =====================================================

    patron_relativo = re.search(
        r"(?:kaira[\s,]*)?"
        r"recu[eé]rdame\s+en\s+"
        r"(\d+)\s*"
        r"(segundos?|minutos?|horas?)"
        r"\s+(.+)",
        mensaje,
        re.IGNORECASE,
    )

    if patron_relativo:

        cantidad = int(
            patron_relativo.group(1)
        )

        unidad = (
            patron_relativo.group(2)
            .lower()
        )

        tarea = (
            patron_relativo.group(3)
            .strip()
        )

        if "segundo" in unidad:
            segundos = cantidad

        elif "minuto" in unidad:
            segundos = cantidad * 60

        elif "hora" in unidad:
            segundos = cantidad * 3600

        else:
            segundos = None

        if segundos is not None:

            if context.job_queue is None:

                await update.message.reply_text(
                    "⚠️ El sistema de recordatorios "
                    "no está disponible."
                )

                return

            fecha_hora = (
                datetime.now()
                + timedelta(
                    seconds=segundos
                )
            )

            recordatorio = agregar_recordatorio(
                update.effective_chat.id,
                tarea,
                fecha_hora,
            )

            context.job_queue.run_once(
                enviar_recordatorio,
                when=segundos,
                data={
                    "chat_id": update.effective_chat.id,
                    "tarea": tarea,
                    "recordatorio_id": recordatorio["id"],
                },
                name=recordatorio["id"],
            )

            if "segundo" in unidad:

                tiempo_texto = (
                    f"{cantidad} segundo"
                    + (
                        "s"
                        if cantidad != 1
                        else ""
                    )
                )

            elif "minuto" in unidad:

                tiempo_texto = (
                    f"{cantidad} minuto"
                    + (
                        "s"
                        if cantidad != 1
                        else ""
                    )
                )

            else:

                tiempo_texto = (
                    f"{cantidad} hora"
                    + (
                        "s"
                        if cantidad != 1
                        else ""
                    )
                )

            respuesta = (
                "⏰ Listo. "
                f"Te recordaré en {tiempo_texto}:\n"
                f"🔔 {tarea}"
            )

            print(
                "⏰ Recordatorio programado: "
                f"{tiempo_texto} → {tarea}"
            )

            await update.message.reply_text(
                respuesta
            )

            return

    # =====================================================
    # 📅 RECORDATORIO PARA MAÑANA
    # =====================================================

    patron_manana = re.search(
        r"(?:kaira[\s,]*)?"
        r"recu[eé]rdame\s+mañana\s+a\s+"
        r"(\d{1,2})"
        r"(?:[:.](\d{1,2}))?"
        r"\s*"
        r"(am|pm|de\s+la\s+mañana|"
        r"de\s+la\s+tarde|de\s+la\s+noche)?"
        r"\s+(.+)",
        mensaje,
        re.IGNORECASE,
    )

    if patron_manana:

        hora = int(
            patron_manana.group(1)
        )

        minuto = (
            int(patron_manana.group(2))
            if patron_manana.group(2)
            else 0
        )

        periodo = (
            patron_manana.group(3)
            or ""
        ).lower().strip()

        tarea = (
            patron_manana.group(4)
            .strip()
        )

        if hora < 1 or hora > 23:

            await update.message.reply_text(
                "⚠️ La hora no es válida."
            )

            return

        if minuto < 0 or minuto > 59:

            await update.message.reply_text(
                "⚠️ Los minutos no son válidos."
            )

            return

        if periodo in (
            "pm",
            "de la tarde",
            "de la noche",
        ):

            if hora < 12:
                hora += 12

        elif periodo in (
            "am",
            "de la mañana",
        ):

            if hora == 12:
                hora = 0

        ahora = datetime.now()

        manana = (
            ahora
            + timedelta(days=1)
        ).replace(
            hour=hora,
            minute=minuto,
            second=0,
            microsecond=0,
        )

        segundos = (
            manana - ahora
        ).total_seconds()

        if context.job_queue is None:

            await update.message.reply_text(
                "⚠️ El sistema de recordatorios "
                "no está disponible."
            )

            return

        recordatorio = agregar_recordatorio(
            update.effective_chat.id,
            tarea,
            manana,
        )

        context.job_queue.run_once(
            enviar_recordatorio,
            when=segundos,
            data={
                "chat_id": update.effective_chat.id,
                "tarea": tarea,
                "recordatorio_id": recordatorio["id"],
            },
            name=recordatorio["id"],
        )

        fecha_texto = manana.strftime(
            "%d/%m/%Y"
        )

        hora_texto = manana.strftime(
            "%H:%M"
        )

        print(
            "📅 Recordatorio programado: "
            f"{fecha_texto} {hora_texto} → {tarea}"
        )

        await update.message.reply_text(
            "📅 Listo.\n"
            f"⏰ Mañana a las {hora_texto}\n"
            f"🔔 {tarea}"
        )

        return

    # =====================================================
    # 🧠 CEREBRO PRINCIPAL
    # =====================================================

    if procesar_mensaje_callback is None:

        respuesta = (
            "Mi sistema principal todavía "
            "no está conectado."
        )

    else:

        try:

            respuesta = await asyncio.to_thread(
                procesar_mensaje_callback,
                mensaje,
                False,
                update.effective_user.id,
            )

        except Exception as error:

            print(
                "⚠️ Error procesando Telegram:",
                error,
            )

            respuesta = (
                "Tuve un problema al procesar "
                "ese comando."
            )

    if respuesta is None:

        respuesta = (
            "No obtuve una respuesta."
        )

    respuesta = str(
        respuesta
    ).strip()

    print(
        f"🤖 KAIRA → Telegram: {respuesta}"
    )

    limite = 4000

    for inicio in range(
        0,
        len(respuesta),
        limite,
    ):

        parte = respuesta[
            inicio:inicio + limite
        ]

        await update.message.reply_text(
            parte
        )


# =========================================================
# 🔄 RECUPERAR RECORDATORIOS
# =========================================================

def recuperar_recordatorios(app):

    recordatorios = cargar_recordatorios()

    if not recordatorios:

        print(
            "📂 No hay recordatorios pendientes."
        )

        return

    if app.job_queue is None:

        print(
            "⚠️ JobQueue no está disponible."
        )

        return

    ahora = datetime.now()

    pendientes = []

    for recordatorio in recordatorios:

        try:

            fecha_hora = datetime.fromisoformat(
                recordatorio["fecha_hora"]
            )

            if fecha_hora <= ahora:

                print(
                    "🗑️ Eliminando recordatorio vencido:",
                    recordatorio["tarea"],
                )

                continue

            segundos = (
                fecha_hora - ahora
            ).total_seconds()

            app.job_queue.run_once(
                enviar_recordatorio,
                when=segundos,
                data={
                    "chat_id": recordatorio["chat_id"],
                    "tarea": recordatorio["tarea"],
                    "recordatorio_id": recordatorio["id"],
                },
                name=recordatorio["id"],
            )

            pendientes.append(recordatorio)

            print(
                "🔄 Recordatorio recuperado:",
                fecha_hora.strftime(
                    "%d/%m/%Y %H:%M"
                ),
                "→",
                recordatorio["tarea"],
            )

        except Exception as error:

            print(
                "⚠️ Error recuperando "
                "recordatorio:",
                error,
            )

    guardar_recordatorios(pendientes)

    print(
        f"📂 {len(pendientes)} "
        "recordatorio(s) pendiente(s) "
        "recuperado(s)."
    )


# =========================================================
# 🔔 NUEVAS ACTIVIDADES DE MOODLE
# =========================================================
# Se mantiene en memoria para no mandar las actividades existentes
# al arrancar. Solo avisa de actividades que aparecen después.
ACTIVIDADES_MOODLE_CONOCIDAS = {}


def _texto_limpio_html(texto):
    texto = str(texto or "")
    texto = re.sub(r"<br\s*/?>", "\n", texto, flags=re.I)
    texto = re.sub(r"</p>|</div>|</li>", "\n", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", "", texto)
    texto = html.unescape(texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    return texto.strip()


def _detalle_nueva_actividad(tarea, nombre_curso):
    from moodle_kaira import formatear_fecha
    nombre = tarea.get("name") or "Actividad sin nombre"
    descripcion = _texto_limpio_html(tarea.get("intro", ""))
    if len(descripcion) > 900:
        descripcion = descripcion[:900].rstrip() + "…"
    fecha = tarea.get("duedate") or 0
    lineas = [
        "🆕 <b>NUEVA ACTIVIDAD</b>",
        "",
        f"📚 <b>{html.escape(str(nombre_curso))}</b>",
        f"📝 <b>{html.escape(str(nombre))}</b>",
    ]
    if descripcion:
        lineas += ["", "📋 <b>Detalles:</b>", html.escape(descripcion)]
    lineas += ["", f"📅 <b>Entrega:</b> {html.escape(formatear_fecha(fecha)) if fecha else 'Sin fecha'}"]
    return "\n".join(lineas)


async def revisar_nuevas_actividades_moodle(context):
    try:
        from membresias import listar_usuarios
        usuarios = listar_usuarios()
    except Exception as error:
        print("⚠️ No pude consultar usuarios para nuevas actividades:", error)
        return

    for usuario in usuarios:
        try:
            uid = int(usuario.get("telegram_user_id"))
        except Exception:
            continue

        if not usuario_autorizado(uid) or not membresia_aun_vigente(uid):
            continue
        if not tiene_moodle_vinculado(uid):
            continue

        try:
            preparar_usuario_moodle(uid)
            from moodle_kaira import obtener_tareas
            datos = await asyncio.to_thread(obtener_tareas)
            if not isinstance(datos, dict):
                continue

            conocidos = ACTIVIDADES_MOODLE_CONOCIDAS.setdefault(uid, set())
            actuales = set()
            nuevas = []

            for curso in datos.get("courses", []):
                nombre_curso = curso.get("fullname", "Curso sin nombre")
                for tarea in curso.get("assignments", []) or []:
                    tid = tarea.get("id")
                    if tid is None:
                        continue
                    clave = str(tid)
                    actuales.add(clave)
                    if clave not in conocidos:
                        nuevas.append((tarea, nombre_curso))

            # Primera consulta: solo inicializa, no molesta al usuario.
            if not conocidos:
                ACTIVIDADES_MOODLE_CONOCIDAS[uid] = actuales
                continue

            for tarea, nombre_curso in nuevas:
                await context.bot.send_message(
                    chat_id=uid,
                    text=_detalle_nueva_actividad(tarea, nombre_curso),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🤖 AYÚDAME CON ESTA ACTIVIDAD", callback_data="menu_tareas")],
                        [InlineKeyboardButton("📚 VER MIS TAREAS", callback_data="menu_tareas")],
                    ]),
                )

            ACTIVIDADES_MOODLE_CONOCIDAS[uid] = actuales

        except Exception as error:
            print(f"⚠️ Error revisando nuevas actividades para {uid}:", error)


# =========================================================
# 🧵 HILO DE TELEGRAM
# =========================================================

async def telegram_post_init(app):
    """
    Se ejecuta cuando Telegram ya tiene
    su event loop activo.
    """

    guardar_loop_telegram(app)

    try:
        recuperar_resumenes_academicos(
            app.job_queue
        )
    except Exception as error:
        print(
            "⚠️ No pude recuperar los resúmenes académicos:",
            error
        )

    print(
        "🔗 Conexión interna KAIRA ↔ Telegram establecida."
    )


def ejecutar_telegram():

    print()

    print(
        "===================================="
    )

    print(
        "       KAIRA - TELEGRAM"
    )

    print(
        "===================================="
    )

    print(
        "📱 Iniciando Telegram..."
    )

    cargar_ultimo_chat()

    try:

        app = (
            ApplicationBuilder()
            .token(TOKEN)
            .post_init(telegram_post_init)
            .build()
        )

        recuperar_recordatorios(
            app
        )

        app.add_handler(
            CommandHandler(
                "resumen",
                comando_resumen,
            )
        )

        app.add_handler(
            CommandHandler(
                "desactivar_resumen",
                comando_desactivar_resumen,
            )
        )

        app.add_handler(
            CommandHandler(
                "vencimientos",
                comando_vencimientos,
            )
        )

        app.add_handler(
            CommandHandler(
                "darme_de_baja",
                comando_darme_de_baja,
            )
        )
        app.add_handler(
            CommandHandler(
                "reactivar",
                comando_reactivar,
            )
        )

        app.add_handler(
            CommandHandler(
                "confirmar_baja",
                comando_confirmar_baja,
            )
        )

        app.add_handler(
            CommandHandler(
                "cancelar_baja",
                comando_cancelar_baja,
            )
        )

        app.add_handler(
            CommandHandler(
                "renovar",
                comando_renovar,
            )
        )

        app.add_handler(
            CommandHandler(
                "mi_cuenta",
                comando_mi_cuenta,
            )
        )

        app.add_handler(
            CommandHandler(
                "historial",
                comando_historial,
            )
        )

        app.add_handler(
            CommandHandler(
                "resumen_admin",
                comando_resumen_admin,
            )
        )

        app.add_handler(
            CommandHandler(
                "buscar",
                comando_buscar_admin,
            )
        )

        app.add_handler(
            CommandHandler(
                "start",
                comando_start,
            )
        )

        app.add_handler(
            CommandHandler(
                "mi_id",
                comando_mi_id,
            )
        )

        app.add_handler(
            CommandHandler(
                "autorizar",
                comando_autorizar,
            )
        )

        app.add_handler(
            CommandHandler(
                "revocar",
                comando_revocar,
            )
        )
        app.add_handler(
            CommandHandler(
                "cancelar_autorizacion",
                comando_cancelar_autorizacion,
            )
        )

        app.add_handler(
            CommandHandler(
                "precios",
                comando_precios,
            )
        )

        app.add_handler(
            CommandHandler(
                "precio",
                comando_precio,
            )
        )

        app.add_handler(
            CommandHandler(
                "autorizados",
                comando_autorizados,
            )
        )

        app.add_handler(
            CommandHandler(
                "desvincular_moodle",
                comando_desvincular_moodle,
            )
        )

        app.add_handler(
            CommandHandler(
                "vincular_moodle",
                comando_vincular_moodle,
            )
        )

        app.add_handler(
            CommandHandler(
                "cancelar_moodle",
                comando_cancelar_moodle,
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                contratar_membresia_callback,
                pattern=r"^contratar_membresia$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                ver_planes_publicos_callback,
                pattern=r"^ver_planes_publicos$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                admin_decision_contratacion_callback,
                pattern=r"^contratacion_(autorizar|denegar):\d+$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                procesar_plan_contratacion_callback,
                pattern=r"^contratacion_plan:(Semanal|Mensual|Trimestral|Semestral|Anual):\d+$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                abrir_renovacion_callback,
                pattern=r"^abrir_renovacion$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                procesar_renovacion_callback,
                pattern=r"^renovacion_(aprobar|rechazar):\d+$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                procesar_plan_renovacion_callback,
                pattern=r"^renovacion_plan:(Semanal|Mensual|Trimestral|Semestral|Anual):\d+$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                solicitud_acceso_callback,
                pattern=r"^solicitar_acceso$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                admin_decision_acceso_callback,
                pattern=r"^solicitud_(autorizar|rechazar):\d+$",
            )
        )

        app.add_handler(
            MessageHandler(
                filters.Document.ALL,
                recibir_documento_kaira,
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                iniciar_revision_tarea,
                pattern=r"^revisar_tarea:\d+$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                entregar_archivo_revisado_callback,
                pattern=r"^revisar_entregar$",
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                callbacks_menu,
                pattern=r"^(menu_|admin_|moodle_|resumen_|cal_|curso:|curso_tema:|feedback_|historial_tarea:|tarea_|entregar_tarea:|cancelar_entrega$)",
            )
        )

        app.add_handler(
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                recibir_mensaje,
            )
        )

        print(
            "✅ Telegram conectado "
            "al cerebro de KAIRA."
        )

        print(
            "📱 Puedes escribirle desde Telegram."
        )

        print(
            "📁 Carpeta de archivos:",
            CARPETA_ARCHIVOS,
        )

        print(
            "📤 Envío PC → Telegram disponible."
        )

        print(
            "===================================="
        )

        # 🔔 Alertas de vencimiento: comprobación cada hora.
        if app.job_queue is not None:
            app.job_queue.run_repeating(
                alertar_vencimientos_admin,
                interval=3600,
                first=20,
                name="alertas_admin_vencimientos",
            )

            app.job_queue.run_once(
                sincronizar_alertas_membresia,
                when=5,
                name="sincronizar_alertas_membresia",
            )

            app.job_queue.run_repeating(
                revisar_nuevas_actividades_moodle,
                interval=600,
                first=30,
                name="revisar_nuevas_actividades_moodle",
            )

        app.run_polling(
            drop_pending_updates=True
        )

    except Exception as error:

        print()

        print(
            "❌ ERROR DE TELEGRAM:"
        )

        print(
            error
        )


# =========================================================
# 📤 ENVIAR ARCHIVO AL ÚLTIMO CHAT DE TELEGRAM
# =========================================================

telegram_app = None
telegram_loop = None


def guardar_loop_telegram(app):
    global telegram_app
    global telegram_loop

    telegram_app = app

    try:
        telegram_loop = asyncio.get_running_loop()
    except Exception:
        telegram_loop = None


def enviar_archivo_a_ultimo_chat(ruta):
    global ultimo_chat_id
    global telegram_loop
    global telegram_app

    if not ultimo_chat_id:
        print(
            "⚠️ No hay un chat de Telegram disponible."
        )
        return False

    if not os.path.exists(ruta):
        print(
            "⚠️ No existe el archivo:",
            ruta
        )
        return False

    if telegram_loop is None or telegram_app is None:
        print(
            "⚠️ Telegram todavía no está conectado."
        )
        return False

    async def enviar():

        try:

            nombre = os.path.basename(ruta)

            extensiones_imagen = (
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                ".gif",
            )

            if nombre.lower().endswith(
                extensiones_imagen
            ):

                with open(
                    ruta,
                    "rb",
                ) as archivo:

                    await telegram_app.bot.send_photo(
                        chat_id=ultimo_chat_id,
                        photo=archivo,
                        caption=(
                            "🖼️ KAIRA\n\n"
                            f"📁 {nombre}"
                        ),
                    )

            else:

                with open(
                    ruta,
                    "rb",
                ) as archivo:

                    await telegram_app.bot.send_document(
                        chat_id=ultimo_chat_id,
                        document=archivo,
                        caption=(
                            "📁 KAIRA\n\n"
                            f"📄 {nombre}"
                        ),
                    )

            print(
                f"📤 Archivo enviado a Telegram: {nombre}"
            )

            return True

        except Exception as error:

            print(
                "⚠️ Error enviando archivo:",
                error
            )

            return False

    try:

        future = asyncio.run_coroutine_threadsafe(
            enviar(),
            telegram_loop,
        )

        resultado = future.result(
            timeout=60
        )

        return resultado

    except Exception as error:

        print(
            "⚠️ Error comunicando con Telegram:",
            error
        )

        return False


# =========================================================
# 🚀 INICIAR TELEGRAM
# =========================================================

def iniciar_telegram():

    hilo = threading.Thread(
        target=ejecutar_telegram,
        daemon=True,
    )

    hilo.start()

    return hilo
