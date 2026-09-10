# =========================================================
# MOODLE - CEREBRO ACADÉMICO DE KAIRA
# =========================================================

import os
import time
import sqlite3
from contextvars import ContextVar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests


# =========================================================
# CONFIGURACIÓN
# =========================================================

MOODLE_URL = os.getenv(
    "MOODLE_URL",
    "https://educacionadistancia.its-purhepecha.edu.mx"
).rstrip("/")

MOODLE_TOKEN = os.getenv("MOODLE_TOKEN")

# Base de datos de cuentas Moodle por usuario de Telegram.
#
# En producción se utiliza PostgreSQL mediante DATABASE_URL (Supabase).
# SQLite se conserva únicamente como respaldo para migrar instalaciones
# antiguas que todavía tengan usuarios_moodle.db local.
ARCHIVO_USUARIOS_MOODLE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "usuarios_moodle.db"
)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:
    psycopg2 = None
    RealDictCursor = None


USUARIO_TELEGRAM_ACTUAL = ContextVar(
    "usuario_telegram_actual",
    default=None
)

MOODLE_ENDPOINT = (
    MOODLE_URL +
    "/webservice/rest/server.php"
)

TIMEOUT_MOODLE = 15


# =========================================================
# ESTADO
# =========================================================

ULTIMA_RESPUESTA_MOODLE = None


# =========================================================
# CUENTAS MOODLE POR TELEGRAM
# =========================================================

def _conectar_sqlite_usuarios_moodle():
    conexion = sqlite3.connect(
        ARCHIVO_USUARIOS_MOODLE,
        timeout=10
    )
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios_moodle (
            telegram_user_id INTEGER PRIMARY KEY,
            moodle_token TEXT NOT NULL,
            moodle_url TEXT NOT NULL,
            creado_en TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        )
        """
    )
    conexion.commit()
    return conexion


def _conectar_usuarios_moodle():
    """Conecta a PostgreSQL de Supabase.

    Si DATABASE_URL no está disponible, usa SQLite local para no romper
    una instalación antigua. En Render, DATABASE_URL debe estar definida.
    """
    if DATABASE_URL:
        if psycopg2 is None:
            raise RuntimeError(
                "Falta psycopg2-binary para conectar con PostgreSQL."
            )

        return psycopg2.connect(
            DATABASE_URL,
            sslmode="require",
            connect_timeout=10,
        )

    return _conectar_sqlite_usuarios_moodle()


def _inicializar_postgres_usuarios_moodle():
    """Crea la tabla de cuentas Moodle en Supabase si no existe."""
    conexion = _conectar_usuarios_moodle()
    try:
        with conexion.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS usuarios_moodle (
                    telegram_user_id BIGINT PRIMARY KEY,
                    moodle_token TEXT NOT NULL,
                    moodle_url TEXT NOT NULL,
                    creado_en TIMESTAMPTZ NOT NULL,
                    actualizado_en TIMESTAMPTZ NOT NULL
                )
                """
            )
        conexion.commit()
    finally:
        conexion.close()


def _migrar_sqlite_a_postgres():
    """Migra una base SQLite antigua a Supabase una sola vez."""
    if not DATABASE_URL or not os.path.exists(ARCHIVO_USUARIOS_MOODLE):
        return

    try:
        sqlite_conn = _conectar_sqlite_usuarios_moodle()
        filas = sqlite_conn.execute(
            """
            SELECT telegram_user_id, moodle_token, moodle_url,
                   creado_en, actualizado_en
            FROM usuarios_moodle
            """
        ).fetchall()
        sqlite_conn.close()

        if not filas:
            return

        postgres_conn = _conectar_usuarios_moodle()
        try:
            with postgres_conn.cursor() as cursor:
                for fila in filas:
                    cursor.execute(
                        """
                        INSERT INTO usuarios_moodle
                            (telegram_user_id, moodle_token, moodle_url, creado_en, actualizado_en)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (telegram_user_id) DO UPDATE SET
                            moodle_token = EXCLUDED.moodle_token,
                            moodle_url = EXCLUDED.moodle_url,
                            actualizado_en = EXCLUDED.actualizado_en
                        """,
                        fila
                    )
            postgres_conn.commit()
            print(
                f"☁️ Migración Moodle → Supabase completada: {len(filas)} cuenta(s)."
            )
        finally:
            postgres_conn.close()
    except Exception as error:
        print("⚠️ No se pudo migrar SQLite → Supabase:", repr(error))


def inicializar_usuarios_moodle():
    if DATABASE_URL:
        _inicializar_postgres_usuarios_moodle()
        _migrar_sqlite_a_postgres()
    else:
        conexion = _conectar_sqlite_usuarios_moodle()
        conexion.close()


def establecer_usuario_telegram(telegram_user_id):
    try:
        return USUARIO_TELEGRAM_ACTUAL.set(
            int(telegram_user_id)
        )
    except Exception:
        return USUARIO_TELEGRAM_ACTUAL.set(None)


def obtener_usuario_telegram():
    return USUARIO_TELEGRAM_ACTUAL.get()


def guardar_token_moodle_usuario(
    telegram_user_id,
    moodle_token,
    moodle_url=None
):
    if not telegram_user_id or not moodle_token:
        return False

    url = (moodle_url or MOODLE_URL).rstrip("/")
    ahora = datetime.now(ZoneInfo("America/Mexico_City"))

    try:
        conexion = _conectar_usuarios_moodle()
        if DATABASE_URL:
            with conexion.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO usuarios_moodle
                        (telegram_user_id, moodle_token, moodle_url, creado_en, actualizado_en)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (telegram_user_id) DO UPDATE SET
                        moodle_token = EXCLUDED.moodle_token,
                        moodle_url = EXCLUDED.moodle_url,
                        actualizado_en = EXCLUDED.actualizado_en
                    """,
                    (int(telegram_user_id), moodle_token.strip(), url, ahora, ahora)
                )
        else:
            conexion.execute(
                """
                INSERT INTO usuarios_moodle
                    (telegram_user_id, moodle_token, moodle_url, creado_en, actualizado_en)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    moodle_token=excluded.moodle_token,
                    moodle_url=excluded.moodle_url,
                    actualizado_en=excluded.actualizado_en
                """,
                (int(telegram_user_id), moodle_token.strip(), url, ahora.isoformat(), ahora.isoformat())
            )
        conexion.commit()
        conexion.close()
        return True
    except Exception as e:
        print("⚠️ Error guardando cuenta Moodle:", e)
        return False


def obtener_configuracion_moodle_usuario(telegram_user_id=None):
    user_id = telegram_user_id or obtener_usuario_telegram()

    if not user_id:
        return None

    try:
        conexion = _conectar_usuarios_moodle()
        if DATABASE_URL:
            with conexion.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT moodle_token, moodle_url
                    FROM usuarios_moodle
                    WHERE telegram_user_id = %s
                    """,
                    (int(user_id),)
                )
                fila = cursor.fetchone()
        else:
            fila = conexion.execute(
                """
                SELECT moodle_token, moodle_url
                FROM usuarios_moodle
                WHERE telegram_user_id = ?
                """,
                (int(user_id),)
            ).fetchone()
        conexion.close()

        if not fila:
            return None

        if DATABASE_URL:
            return {
                "token": fila["moodle_token"],
                "url": fila["moodle_url"]
            }

        return {
            "token": fila[0],
            "url": fila[1]
        }
    except Exception as e:
        print("⚠️ Error leyendo cuenta Moodle:", e)
        return None


def eliminar_cuenta_moodle_usuario(telegram_user_id=None):
    user_id = telegram_user_id or obtener_usuario_telegram()

    if not user_id:
        return False

    try:
        conexion = _conectar_usuarios_moodle()
        if DATABASE_URL:
            with conexion.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM usuarios_moodle WHERE telegram_user_id = %s",
                    (int(user_id),)
                )
        else:
            conexion.execute(
                "DELETE FROM usuarios_moodle WHERE telegram_user_id = ?",
                (int(user_id),)
            )
        conexion.commit()
        conexion.close()
        return True
    except Exception as e:
        print("⚠️ Error eliminando cuenta Moodle:", e)
        return False


def obtener_token_moodle_actual():
    configuracion = obtener_configuracion_moodle_usuario()

    if configuracion:
        return configuracion["token"]

    # Compatibilidad/migración: el token del .env SOLO se usa si
    # KAIRA_ADMIN_TELEGRAM_ID coincide con el usuario actual.
    admin_id = os.getenv("KAIRA_ADMIN_TELEGRAM_ID")
    usuario_actual = obtener_usuario_telegram()

    if (
        MOODLE_TOKEN
        and admin_id
        and usuario_actual
        and str(admin_id).strip() == str(usuario_actual)
    ):
        return MOODLE_TOKEN

    return None


def autenticar_moodle_usuario(usuario, password, moodle_url=None):
    """Obtiene automáticamente el token Moodle usando usuario/contraseña.

    La contraseña solo se usa durante esta petición y no se almacena.
    Devuelve (token, mensaje_error).
    """
    usuario = str(usuario or "").strip()
    password = str(password or "")
    url = (moodle_url or MOODLE_URL).rstrip("/")

    if not usuario or not password:
        return None, "Debes proporcionar usuario y contraseña."

    try:
        respuesta = requests.post(
            url + "/login/token.php",
            data={
                "username": usuario,
                "password": password,
                "service": "moodle_mobile_app",
            },
            timeout=20,
        )
        respuesta.raise_for_status()
        datos = respuesta.json()

        token = datos.get("token") if isinstance(datos, dict) else None
        if token:
            return str(token).strip(), None

        return None, str(
            (datos or {}).get("error", "Usuario o contraseña no válidos.")
        )
    except requests.exceptions.Timeout:
        return None, "Moodle tardó demasiado en responder."
    except requests.exceptions.RequestException:
        return None, "No pude conectar con Moodle para validar tus datos."
    except Exception:
        return None, "No pude validar las credenciales de Moodle."


def obtener_url_moodle_actual():
    configuracion = obtener_configuracion_moodle_usuario()

    if configuracion:
        return configuracion["url"].rstrip("/")

    return MOODLE_URL


def usuario_moodle_configurado():
    return bool(
        obtener_token_moodle_actual()
        and obtener_url_moodle_actual()
    )


inicializar_usuarios_moodle()


# =========================================================
# UTILIDADES
# =========================================================

def moodle_configurado():
    """Comprueba si el usuario actual de Telegram tiene Moodle vinculado."""
    return usuario_moodle_configurado()


def _aplanar_parametros(
    datos,
    prefijo=""
):
    """
    Convierte estructuras Python como:

        {
            "courseids": [1, 2, 3]
        }

    en parámetros compatibles con Moodle:

        courseids[0]=1
        courseids[1]=2
        courseids[2]=3
    """

    resultado = []

    for clave, valor in datos.items():

        nombre = (
            f"{prefijo}[{clave}]"
            if prefijo
            else clave
        )

        if isinstance(valor, dict):

            resultado.extend(
                _aplanar_parametros(
                    valor,
                    nombre
                )
            )

        elif isinstance(valor, (list, tuple)):

            for indice, elemento in enumerate(valor):

                nombre_lista = (
                    f"{nombre}[{indice}]"
                )

                if isinstance(
                    elemento,
                    (dict, list, tuple)
                ):

                    if isinstance(elemento, dict):

                        resultado.extend(
                            _aplanar_parametros(
                                elemento,
                                nombre_lista
                            )
                        )

                else:

                    resultado.append(
                        (
                            nombre_lista,
                            elemento
                        )
                    )

        elif valor is not None:

            resultado.append(
                (
                    nombre,
                    valor
                )
            )

    return resultado


# =========================================================
# LLAMADA GENERAL A MOODLE
# =========================================================

def moodle_api(
    funcion,
    parametros=None
):
    """
    Ejecuta una función del Web Service REST
    de Moodle.
    """

    global ULTIMA_RESPUESTA_MOODLE

    if not moodle_configurado():

        return {
            "error": (
                "Moodle todavía no está configurado "
                "con un token."
            )
        }

    if parametros is None:
        parametros = {}

    token = obtener_token_moodle_actual()
    url = obtener_url_moodle_actual()

    if not token:
        return {
            "error": (
                "No tienes una cuenta de Moodle vinculada. "
                "Usa /moodle_token seguido de tu token de Moodle."
            )
        }

    datos = {
        "wstoken": token,
        "wsfunction": funcion,
        "moodlewsrestformat": "json"
    }

    datos.update(
        parametros
    )

    try:

        endpoint = url + "/webservice/rest/server.php"

        respuesta = requests.post(
            endpoint,
            data=_aplanar_parametros(datos),
            timeout=TIMEOUT_MOODLE
        )

        respuesta.raise_for_status()

        datos_respuesta = (
            respuesta.json()
        )

        ULTIMA_RESPUESTA_MOODLE = (
            datos_respuesta
        )

        if isinstance(
            datos_respuesta,
            dict
        ):

            if "exception" in datos_respuesta:

                return {
                    "error": datos_respuesta.get(
                        "message",
                        "Moodle rechazó la solicitud."
                    ),
                    "exception": datos_respuesta.get(
                        "exception"
                    )
                }

        return datos_respuesta

    except requests.exceptions.Timeout:

        return {
            "error":
                "Moodle tardó demasiado en responder."
        }

    except requests.exceptions.ConnectionError:

        return {
            "error":
                "No pude conectarme con Moodle."
        }

    except Exception as error:

        print(
            "⚠️ Error Moodle:",
            error
        )

        return {
            "error":
                "Ocurrió un error al consultar Moodle."
        }


# =========================================================
# INFORMACIÓN DE LA CUENTA
# =========================================================

def obtener_info_moodle():

    return moodle_api(
        "core_webservice_get_site_info"
    )


# =========================================================
# CURSOS
# =========================================================

def obtener_cursos():

    info = obtener_info_moodle()

    if (
        not isinstance(info, dict)
        or "userid" not in info
    ):
        return info

    userid = info["userid"]

    return moodle_api(
        "core_enrol_get_users_courses",
        {
            "userid": userid
        }
    )

def obtener_contenido_curso(courseid):
    """Obtiene secciones/temas y módulos del curso desde Moodle."""
    try:
        return moodle_api(
            "core_course_get_contents",
            {"courseid": int(courseid)}
        )
    except Exception as error:
        print("⚠️ Error obteniendo contenido del curso:", error)
        return {"error": "No pude obtener los temas del curso."}



def obtener_asignaciones_curso(
    courseid
):
    """
    Obtiene únicamente las tareas (assignments) del curso seleccionado.
    Evita descargar todos los cursos del usuario al navegar por un tema.
    """
    datos=moodle_api(
        "mod_assign_get_assignments",
        {
            "courseids": [
                int(courseid)
            ]
        }
    )

    if not isinstance(datos,dict):
        return datos

    cursos=datos.get(
        "courses",
        []
    )

    if not cursos:
        return []

    resultado=[]

    for curso in cursos:
        curso_nombre=curso.get(
            "fullname",
            "Curso"
        )

        for tarea in curso.get(
            "assignments",
            []
        ):
            copia=dict(tarea)
            copia["curso_nombre"]=curso_nombre
            resultado.append(copia)

    return resultado


def obtener_asignacion_por_id_en_curso(
    courseid,
    assignid
):
    tareas=obtener_asignaciones_curso(
        courseid
    )

    if not isinstance(
        tareas,
        list
    ):
        return tareas

    for tarea in tareas:
        try:
            if int(
                tarea.get("id")
            )==int(assignid):
                return tarea
        except Exception:
            continue

    return None


def obtener_actividades_curso_por_temas(courseid):
    """
    Devuelve las secciones reales de Moodle y sus módulos.
    Los módulos tipo 'assign' incluyen 'instance' = assignid.
    """
    contenidos=obtener_contenido_curso(courseid)

    if not isinstance(contenidos,list):
        return contenidos

    resultado=[]

    for numero,seccion in enumerate(contenidos,start=1):
        if not isinstance(seccion,dict):
            continue

        nombre=(
            seccion.get("name")
            or f"Tema {numero}"
        )

        actividades=[]

        for modulo in seccion.get("modules",[]):
            if not isinstance(modulo,dict):
                continue

            actividades.append({
                "id": modulo.get("id"),
                "instance": modulo.get("instance"),
                "name": modulo.get("name","Actividad"),
                "modname": modulo.get("modname",""),
                "url": modulo.get("url",""),
                "description": modulo.get("description",""),
                "visible": modulo.get("visible",1),
                "section": nombre,
            })

        if actividades:
            resultado.append({
                "id": seccion.get("id"),
                "name": nombre,
                "summary": seccion.get("summary",""),
                "activities": actividades,
            })

    return resultado




# =========================================================
# TAREAS
# =========================================================

def obtener_tareas():

    cursos = obtener_cursos()

    if not isinstance(
        cursos,
        list
    ):
        return cursos

    courseids = []

    for curso in cursos:

        if isinstance(
            curso,
            dict
        ):

            if "id" in curso:

                courseids.append(
                    curso["id"]
                )

    if not courseids:

        return []

    return moodle_api(
        "mod_assign_get_assignments",
        {
            "courseids": courseids
        }
    )


# =========================================================
# TAREAS DE LOS PRÓXIMOS DÍAS
# =========================================================

def obtener_tareas_proximas(
    dias=7
):

    datos = obtener_tareas()

    if not isinstance(
        datos,
        dict
    ):
        return datos

    cursos = datos.get(
        "courses",
        []
    )

    ahora = int(
        time.time()
    )

    limite = int(
        (
            datetime.now()
            + timedelta(days=dias)
        ).timestamp()
    )

    resultado = []

    for curso in cursos:

        nombre_curso = curso.get(
            "fullname",
            "Curso sin nombre"
        )

        for tarea in curso.get(
            "assignments",
            []
        ):

            fecha = tarea.get(
                "duedate",
                0
            )

            if not fecha:
                continue

            if (
                ahora <= fecha <= limite
            ):

                copia = dict(
                    tarea
                )

                copia[
                    "curso_nombre"
                ] = nombre_curso

                resultado.append(
                    copia
                )

    resultado.sort(
        key=lambda x:
            x.get("duedate", 0)
    )

    return resultado


# =========================================================
# FORMATO DE FECHA
# =========================================================

def formatear_fecha(
    timestamp
):

    if not timestamp:

        return "Sin fecha"

    try:

        fecha = datetime.fromtimestamp(
            timestamp,
            tz=ZoneInfo("America/Mexico_City")
        )

        return fecha.strftime(
            "%d/%m/%Y %H:%M"
        )

    except Exception:

        return "Fecha desconocida"


# =========================================================
# PRÓXIMA ENTREGA
# =========================================================

def obtener_proxima_entrega():

    tareas = obtener_tareas_proximas(
        dias=60
    )

    if not isinstance(
        tareas,
        list
    ):
        return tareas

    ahora = int(
        time.time()
    )

    futuras = [
        tarea
        for tarea in tareas
        if tarea.get(
            "duedate",
            0
        ) > ahora
    ]

    if not futuras:

        return None

    futuras.sort(
        key=lambda x:
            x.get(
                "duedate",
                0
            )
    )

    return futuras[0]


# =========================================================
# TAREAS DE HOY
# =========================================================

def obtener_tareas_dia(offset_dias=0):
    """
    Obtiene las tareas cuya fecha de entrega cae
    dentro de un día específico.

    offset_dias=0  -> hoy
    offset_dias=1  -> mañana
    """

    datos = obtener_tareas()

    if not isinstance(datos, dict):
        return datos

    cursos = datos.get(
        "courses",
        []
    )

    zona = ZoneInfo("America/Mexico_City")
    ahora = datetime.now(zona)

    inicio_dia = datetime(
        ahora.year,
        ahora.month,
        ahora.day,
        tzinfo=zona
    ) + timedelta(days=offset_dias)

    fin_dia = inicio_dia + timedelta(days=1)

    inicio_timestamp = int(
        inicio_dia.timestamp()
    )

    fin_timestamp = int(
        fin_dia.timestamp()
    )

    resultado = []

    for curso in cursos:

        nombre_curso = curso.get(
            "fullname",
            "Curso sin nombre"
        )

        for tarea in curso.get(
            "assignments",
            []
        ):

            fecha = tarea.get(
                "duedate",
                0
            )

            if not fecha:
                continue

            if (
                inicio_timestamp
                <= fecha
                < fin_timestamp
            ):

                copia = dict(
                    tarea
                )

                copia[
                    "curso_nombre"
                ] = nombre_curso

                resultado.append(
                    copia
                )

    resultado.sort(
        key=lambda x:
            x.get(
                "duedate",
                0
            )
    )

    return resultado


def resumen_tareas_dia(
    offset_dias=0
):

    tareas = obtener_tareas_dia(
        offset_dias=offset_dias
    )

    if isinstance(
        tareas,
        dict
    ) and "error" in tareas:

        return (
            "❌ No pude consultar Moodle.\n\n"
            + tareas["error"]
        )

    titulo = (
        "📅 TAREAS QUE VENCEN MAÑANA"
        if offset_dias == 1
        else "📅 TAREAS QUE VENCEN HOY"
    )

    return resumen_tareas_formateado(
        tareas,
        titulo
    )


# =========================================================
# TAREAS ATRASADAS
# =========================================================

# =========================================================
# PERIODO ACADÉMICO ACTUAL
# =========================================================

def es_tarea_del_periodo_actual(
    fecha,
    nombre_curso=""
):
    """
    Evita considerar actividades antiguas de otros periodos.

    AGO-DIC:
        desde el 1 de agosto del año actual.

    ENE-JUN / ENE-JUL:
        desde el 1 de enero del año actual.

    Si el curso no indica un periodo:
        se acepta únicamente el año actual.
    """

    if not fecha:
        return False

    try:

        zona = ZoneInfo(
            "America/Mexico_City"
        )

        fecha_tarea = datetime.fromtimestamp(
            int(fecha),
            tz=zona
        )

        ahora = datetime.now(
            zona
        )

        nombre = str(
            nombre_curso
        ).lower()

        # -----------------------------------------------------
        # AGO-DIC
        # -----------------------------------------------------

        if (
            "ago-dic" in nombre
            or "ago - dic" in nombre
            or "agosto-diciembre" in nombre
            or "agosto - diciembre" in nombre
        ):

            inicio = datetime(
                ahora.year,
                8,
                1,
                tzinfo=zona
            )

            return (
                fecha_tarea >= inicio
                and fecha_tarea.year == ahora.year
            )

        # -----------------------------------------------------
        # ENE-JUN / ENE-JUL
        # -----------------------------------------------------

        if (
            "ene-jun" in nombre
            or "ene - jun" in nombre
            or "ene-jul" in nombre
            or "ene - jul" in nombre
            or "enero-junio" in nombre
            or "enero - junio" in nombre
            or "enero-julio" in nombre
            or "enero - julio" in nombre
        ):

            inicio = datetime(
                ahora.year,
                1,
                1,
                tzinfo=zona
            )

            return (
                fecha_tarea >= inicio
                and fecha_tarea.year == ahora.year
            )

        # -----------------------------------------------------
        # CURSO SIN PERIODO IDENTIFICABLE
        # -----------------------------------------------------

        return (
            fecha_tarea.year == ahora.year
        )

    except Exception as e:

        print(
            "⚠️ Error comprobando periodo:",
            e
        )

        return False


# =========================================================
# ESTADO REAL DE ENTREGA
# =========================================================


def obtener_info_entrega(
    assignid,
    userid
):
    """
    Obtiene el estado y las capacidades reales de la entrega.

    Campos relevantes:
      - status: submitted/new/draft/...
      - cansubmit: si Moodle permite enviar ahora
      - canedit: si Moodle permite editar
      - extensionduedate: extensión individual, cuando exista
    """
    if not assignid or not userid:
        return {
            "ok": False,
            "status": "desconocida",
            "cansubmit": None,
            "canedit": None,
        }

    try:
        datos=moodle_api(
            "mod_assign_get_submission_status",
            {
                "assignid": int(assignid),
                "userid": int(userid),
            }
        )

        if not isinstance(datos,dict):
            return {
                "ok": False,
                "status": "desconocida",
                "cansubmit": None,
                "canedit": None,
            }

        if "error" in datos:
            return {
                "ok": False,
                "status": "desconocida",
                "cansubmit": None,
                "canedit": None,
                "error": datos.get(
                    "error",
                    "Moodle rechazó la consulta."
                ),
            }

        lastattempt=datos.get(
            "lastattempt",
            {}
        )
        if not isinstance(lastattempt,dict):
            lastattempt={}

        submission=lastattempt.get(
            "submission",
            {}
        )
        if not isinstance(submission,dict):
            submission={}

        estado=(
            submission.get("status")
            or datos.get("status")
            or "desconocida"
        )

        # Moodle puede devolver estas capacidades en el nivel raíz
        # o dentro de la información del último intento.
        cansubmit=datos.get("cansubmit")
        if cansubmit is None:
            cansubmit=lastattempt.get("cansubmit")

        canedit=datos.get("canedit")
        if canedit is None:
            canedit=lastattempt.get("canedit")

        return {
            "ok": True,
            "status": str(
                estado
            ).lower().strip(),
            "cansubmit": cansubmit,
            "canedit": canedit,
            "extensionduedate": datos.get(
                "extensionduedate",
                0
            ),
            "raw": datos,
        }

    except Exception as error:
        print(
            "⚠️ Error obteniendo capacidades de entrega:",
            error
        )

        return {
            "ok": False,
            "status": "desconocida",
            "cansubmit": None,
            "canedit": None,
        }


def obtener_capacidad_entrega(
    assignid,
    userid
):
    """
    Determina si se puede intentar entregar la actividad.

    Devuelve:
      - puede_entregar=True/False
      - motivo: abierta, tardia, cerrada, no_disponible
      - fecha_cierre
    """
    info=obtener_info_entrega(
        assignid,
        userid
    )

    if not info.get("ok"):
        # Si Moodle no devolvió capacidad, no bloqueamos por
        # una suposición. La subida intentará consultar a Moodle
        # y mostrará el error real si no está permitida.
        return {
            "puede_entregar": True,
            "motivo": "no_confirmado",
            "fecha_cierre": 0,
            "detalle": info,
        }

    ahora=int(
        time.time()
    )

    cansubmit=info.get(
        "cansubmit"
    )

    # Consultar fechas de la actividad por los datos de assignments.
    cutoffdate=0
    allowsubmissionsfromdate=0

    try:
        tareas=obtener_tareas()

        if isinstance(tareas,dict):
            for curso in tareas.get(
                "courses",
                []
            ):
                for tarea in curso.get(
                    "assignments",
                    []
                ):
                    try:
                        if int(tarea.get("id",0)) == int(assignid):
                            cutoffdate=int(
                                tarea.get(
                                    "cutoffdate",
                                    0
                                ) or 0
                            )
                            allowsubmissionsfromdate=int(
                                tarea.get(
                                    "allowsubmissionsfromdate",
                                    0
                                ) or 0
                            )
                            break
                    except Exception:
                        continue
    except Exception:
        pass

    # Una extensión individual puede mover el cierre.
    extensionduedate=int(
        info.get(
            "extensionduedate",
            0
        ) or 0
    )

    if extensionduedate:
        cutoffdate=max(
            cutoffdate,
            extensionduedate
        )

    if (
        allowsubmissionsfromdate
        and ahora < allowsubmissionsfromdate
    ):
        return {
            "puede_entregar": False,
            "motivo": "no_disponible",
            "fecha_cierre": allowsubmissionsfromdate,
            "detalle": info,
        }

    if (
        cutoffdate
        and ahora > cutoffdate
    ):
        return {
            "puede_entregar": False,
            "motivo": "cerrada",
            "fecha_cierre": cutoffdate,
            "detalle": info,
        }

    # La fecha de entrega puede haber pasado, pero si no existe
    # cierre (cutoff) o todavía no ha pasado, Moodle puede aceptar
    # una entrega tardía.
    return {
        "puede_entregar": True,
        "motivo": (
            "tardia"
            if (
                obtener_fecha_entrega_asignacion(
                    assignid
                )
                and ahora > obtener_fecha_entrega_asignacion(assignid)
            )
            else "abierta"
        ),
        "fecha_cierre": cutoffdate,
        "detalle": info,
    }


def obtener_fecha_entrega_asignacion(
    assignid
):
    try:
        tareas=obtener_tareas()
        if not isinstance(tareas,dict):
            return 0

        for curso in tareas.get(
            "courses",
            []
        ):
            for tarea in curso.get(
                "assignments",
                []
            ):
                try:
                    if int(tarea.get("id",0)) == int(assignid):
                        return int(
                            tarea.get(
                                "duedate",
                                0
                            ) or 0
                        )
                except Exception:
                    continue
    except Exception:
        pass

    return 0



def _recorrer_estructura_moodle(valor, ruta=""):
    """Generador recursivo para recorrer feedbackplugins y estructuras anidadas."""
    if isinstance(valor, dict):
        yield ruta, valor
        for clave, hijo in valor.items():
            nueva=f"{ruta}.{clave}" if ruta else str(clave)
            yield from _recorrer_estructura_moodle(hijo, nueva)
    elif isinstance(valor, list):
        for indice, hijo in enumerate(valor):
            nueva=f"{ruta}[{indice}]"
            yield from _recorrer_estructura_moodle(hijo, nueva)


def _limpiar_texto_feedback(texto):
    if texto is None:
        return ""
    texto=str(texto)
    return _limpiar_html(texto).strip()


def obtener_retroalimentacion_tarea(assignid, userid=None):
    """
    Obtiene la retroalimentación disponible para el estudiante desde
    mod_assign_get_submission_status.

    Soporta, cuando Moodle los devuelve:
      - comentario del profesor
      - calificación
      - fecha de revisión
      - archivos de retroalimentación
      - intentos anteriores
    """
    if userid is None:
        userid=obtener_usuario_moodle_id_actual()

    if not assignid or not userid:
        return {
            "ok":False,
            "tiene_feedback":False,
            "texto":"",
            "grado":"",
            "fecha":0,
            "archivos":[],
            "intentos":[],
        }

    try:
        datos=moodle_api(
            "mod_assign_get_submission_status",
            {
                "assignid":int(assignid),
                "userid":int(userid),
            },
        )

        if not isinstance(datos,dict) or datos.get("error"):
            return {
                "ok":False,
                "tiene_feedback":False,
                "texto":"",
                "grado":"",
                "fecha":0,
                "archivos":[],
                "intentos":[],
                "error":(
                    datos.get("error")
                    if isinstance(datos,dict) else
                    "Respuesta inválida de Moodle."
                ),
            }

        feedback=datos.get("feedback",{}) or {}
        if not isinstance(feedback,dict):
            feedback={}

        textos=[]
        archivos=[]
        vistos_archivos=set()

        # Comentario directo / texto de feedback.
        for clave in ("commenttext","feedbacktext","text","comment","content"):
            valor=feedback.get(clave)
            limpio=_limpiar_texto_feedback(valor)
            if limpio and limpio not in textos:
                textos.append(limpio)

        # Recorrer feedbackdata y detectar comentarios / archivos del plugin.
        feedbackdata=feedback.get("feedbackdata",[])
        for ruta, nodo in _recorrer_estructura_moodle(feedbackdata):
            if not isinstance(nodo,dict):
                continue

            for clave in ("commenttext","feedbacktext","text","comment","content"):
                valor=nodo.get(clave)
                limpio=_limpiar_texto_feedback(valor)
                if limpio and len(limpio) > 0 and limpio not in textos:
                    # Evitar guardar textos que sean realmente nombres de archivo/URLs.
                    if not (limpio.startswith("http://") or limpio.startswith("https://")):
                        textos.append(limpio)

            url=(
                nodo.get("fileurl")
                or nodo.get("url")
            )
            nombre=(
                nodo.get("filename")
                or nodo.get("name")
                or nodo.get("filepath")
            )

            if url and nombre:
                clave_archivo=(str(url),str(nombre))
                if clave_archivo not in vistos_archivos:
                    vistos_archivos.add(clave_archivo)
                    archivos.append({
                        "filename":str(nombre),
                        "fileurl":str(url),
                        "source":"feedback",
                    })

        grado=(
            feedback.get("gradefordisplay")
            or datos.get("gradefordisplay")
            or ""
        )

        fecha=int(
            feedback.get("gradeddate",0)
            or 0
        )

        intentos=datos.get(
            "previousattempts",[]
        ) or []
        if not isinstance(intentos,list):
            intentos=[]

        # No duplicar feedbackdata bruto si no hubo contenido.
        tiene=bool(
            textos
            or archivos
            or grado
            or fecha
        )

        return {
            "ok":True,
            "tiene_feedback":tiene,
            "texto":"\n\n".join(textos),
            "grado":str(grado),
            "fecha":fecha,
            "archivos":archivos,
            "intentos":intentos,
            "raw":datos,
        }

    except Exception as error:
        print("⚠️ Error obteniendo retroalimentación:",error)
        return {
            "ok":False,
            "tiene_feedback":False,
            "texto":"",
            "grado":"",
            "fecha":0,
            "archivos":[],
            "intentos":[],
            "error":str(error),
        }


def historial_entrega_tarea(assignid, userid=None):
    """Resumen legible de intentos anteriores de una actividad."""
    info=obtener_retroalimentacion_tarea(
        assignid,
        userid,
    )

    if not info.get("ok"):
        return {
            "ok":False,
            "intentos":[],
        }

    return {
        "ok":True,
        "intentos":info.get("intentos",[]),
    }


def obtener_estado_entrega(
    assignid,
    userid
):
    """
    Consulta Moodle para saber si la actividad
    fue entregada.

    Devuelve:
        entregada
        no_entregada
        desconocida
    """

    if not assignid:
        return "desconocida"

    try:

        datos = moodle_api(
            "mod_assign_get_submission_status",
            {
                "assignid": assignid,
                "userid": userid
            }
        )

        if not isinstance(
            datos,
            dict
        ):
            return "desconocida"

        if "error" in datos:

            print(
                "⚠️ No se pudo consultar "
                "el estado de la entrega:",
                datos["error"]
            )

            return "desconocida"

        # Moodle devuelve normalmente:
        # lastattempt -> submission -> status
        lastattempt = datos.get(
            "lastattempt",
            {}
        )

        if isinstance(
            lastattempt,
            dict
        ):

            submission = lastattempt.get(
                "submission"
            )

            if isinstance(
                submission,
                dict
            ):

                estado = str(
                    submission.get(
                        "status",
                        ""
                    )
                ).lower().strip()

                if estado in (
                    "submitted",
                    "graded",
                    "reopened"
                ):

                    return "entregada"

                if estado in (
                    "new",
                    "draft"
                ):

                    return "no_entregada"

        # Algunos Moodle pueden devolver
        # información de estado en otro nivel.
        estado_directo = str(
            datos.get(
                "status",
                ""
            )
        ).lower().strip()

        if estado_directo in (
            "submitted",
            "graded",
            "reopened"
        ):

            return "entregada"

        if estado_directo in (
            "new",
            "draft"
        ):

            return "no_entregada"

        return "desconocida"

    except Exception as e:

        print(
            "⚠️ Error consultando entrega:",
            e
        )

        return "desconocida"


def obtener_tareas_atrasadas():

    datos = obtener_tareas()

    if not isinstance(
        datos,
        dict
    ):
        return datos

    cursos = datos.get(
        "courses",
        []
    )

    # Identificar al usuario actual.
    info = obtener_info_moodle()

    if not isinstance(
        info,
        dict
    ) or "userid" not in info:

        return {
            "error":
                "No pude identificar tu usuario de Moodle."
        }

    userid = info[
        "userid"
    ]

    ahora = int(
        time.time()
    )

    resultado = []

    for curso in cursos:

        nombre_curso = curso.get(
            "fullname",
            "Curso sin nombre"
        )

        for tarea in curso.get(
            "assignments",
            []
        ):

            fecha = tarea.get(
                "duedate",
                0
            )

            # Sin fecha no podemos determinar
            # si está atrasada.
            if not fecha:
                continue

            # Eliminar actividades de periodos antiguos.
            if not es_tarea_del_periodo_actual(
                fecha,
                nombre_curso
            ):
                continue

            # Solo actividades cuya fecha ya pasó.
            if fecha >= ahora:
                continue

            assignid = tarea.get(
                "id"
            )

            # Consultar directamente el estado
            # de entrega en Moodle.
            estado = obtener_estado_entrega(
                assignid,
                userid
            )

            # Una tarea vencida entra en "Atrasadas" siempre que
            # Moodle no la marque como ya entregada.
            # Esto evita perder actividades por una respuesta
            # de estado "desconocida".
            if estado == "entregada":
                continue

            copia = dict(
                tarea
            )

            copia[
                "curso_nombre"
            ] = nombre_curso

            copia[
                "estado_entrega"
            ] = estado

            resultado.append(
                copia
            )

    resultado.sort(
        key=lambda x:
            x.get(
                "duedate",
                0
            ),
        reverse=True
    )

    return resultado


def resumen_tareas_atrasadas():

    tareas = obtener_tareas_atrasadas()

    if isinstance(
        tareas,
        dict
    ) and "error" in tareas:

        return (
            "❌ No pude consultar Moodle.\n\n"
            + tareas["error"]
        )

    if not tareas:

        return (
            "🟢 No encontré tareas atrasadas "
            "pendientes de entrega en el periodo actual de Moodle."
        )

    lineas = [
        "🔴 Tareas atrasadas:"
    ]

    for tarea in tareas:

        nombre = tarea.get(
            "name",
            "Tarea sin nombre"
        )

        curso = tarea.get(
            "curso_nombre",
            "Curso desconocido"
        )

        fecha = formatear_fecha(
            tarea.get(
                "duedate",
                0
            )
        )

        lineas.append(
            f"• {nombre}\n"
            f"  📚 Curso: {curso}\n"
            f"  📅 Venció: {fecha}"
        )

    return "\n\n".join(
        lineas
    )




# =========================================================
# 🔎 CONTEXTO PARA REVISIÓN PREVIA DE TRABAJOS
# =========================================================

def _buscar_cmid_de_tarea(assignid, tarea=None):
    """Obtiene el course-module id (cmid) real de una tarea."""
    tarea = tarea or {}
    for clave in ("cmid", "cmidnumber", "coursemoduleid"):
        valor = tarea.get(clave)
        if valor:
            try:
                return int(valor)
            except Exception:
                pass

    courseid = tarea.get("courseid")
    if not courseid:
        return None

    try:
        contenidos = obtener_contenido_curso(int(courseid))
        if not isinstance(contenidos, list):
            return None
        for seccion in contenidos:
            for modulo in (seccion.get("modules", []) or []):
                try:
                    if int(modulo.get("instance", 0)) == int(assignid) and str(modulo.get("modname", "")) == "assign":
                        if modulo.get("id"):
                            return int(modulo["id"])
                except Exception:
                    continue
    except Exception as error:
        print("⚠️ No pude localizar cmid de la tarea:", error)
    return None


def obtener_rubrica_tarea(assignid, tarea=None):
    """
    Consulta la definición de calificación avanzada de Moodle.
    No inventa una rúbrica: si Moodle no la expone para el alumno,
    devuelve disponible=False.
    """
    tarea = tarea or {}
    cmid = _buscar_cmid_de_tarea(assignid, tarea)
    if not cmid:
        return {"disponible": False, "motivo": "No pude identificar el módulo de Moodle."}

    try:
        modulo = moodle_api(
            "core_course_get_course_module",
            {"cmid": int(cmid)},
        )
    except Exception as error:
        print("⚠️ Error consultando módulo Moodle:", error)
        modulo = {}

    areas = []
    cm = modulo.get("cm", {}) if isinstance(modulo, dict) else {}
    for item in (cm.get("advancedgrading", []) or []):
        if isinstance(item, dict):
            areas.append(item)

    # En Moodle el área habitual de Assignment para la calificación
    # avanzada es "submissions". Usamos la información real del módulo
    # cuando está disponible y solo después probamos ese nombre estándar.
    area_names = []
    for item in areas:
        area = item.get("area")
        if area:
            area_names.append(str(area))
    if "submissions" not in area_names:
        area_names.append("submissions")

    ultimo_error = None
    for area in area_names:
        for parametros in (
            {"cmids": [int(cmid)], "areaname": area, "activeonly": 1},
            {"cmids": [int(cmid)], "areaname": area},
        ):
            try:
                datos = moodle_api("core_grading_get_definitions", parametros)
                if not isinstance(datos, dict) or datos.get("error"):
                    ultimo_error = datos
                    continue
                definiciones = datos.get("definitions", []) or []
                if definiciones:
                    return {
                        "disponible": True,
                        "cmid": cmid,
                        "area": area,
                        "metodo": next((x.get("method") for x in areas if x.get("area") == area), None),
                        "definiciones": definiciones,
                        "raw": datos,
                    }
            except Exception as error:
                ultimo_error = error

    return {
        "disponible": False,
        "cmid": cmid,
        "motivo": "Moodle no expuso una rúbrica/lista de calificación disponible para esta cuenta.",
        "detalle": str(ultimo_error)[:1000] if ultimo_error else "sin definición activa",
    }


def obtener_contexto_revision_tarea(assignid):
    """Reúne únicamente información académica publicada/visible para el alumno."""
    tarea = None
    try:
        todas = buscar_tareas_detalladas()
        if isinstance(todas, list):
            for item in todas:
                try:
                    if int(item.get("id", 0)) == int(assignid):
                        tarea = dict(item)
                        break
                except Exception:
                    continue
    except Exception as error:
        print("⚠️ No pude localizar tarea para revisión:", error)

    if not tarea:
        return {"ok": False, "error": "No encontré la actividad en Moodle."}

    detalle = detalle_tarea(tarea)
    rubrica = obtener_rubrica_tarea(assignid, tarea)

    archivos = []
    for archivo in (detalle.get("archivos", []) or []):
        if not isinstance(archivo, dict):
            continue
        archivos.append({
            "filename": archivo.get("filename") or archivo.get("name"),
            "url": archivo.get("fileurl") or archivo.get("url"),
            "mimetype": archivo.get("mimetype") or archivo.get("mimetype"),
        })

    materiales_relacionados = []
    courseid = tarea.get("courseid")
    if courseid:
        try:
            contenidos = obtener_contenido_curso(int(courseid))
            if isinstance(contenidos, list):
                for seccion in contenidos:
                    mods = seccion.get("modules", []) or []
                    pertenece = any(
                        isinstance(mod, dict) and int(mod.get("instance", 0) or 0) == int(assignid)
                        for mod in mods
                    )
                    if not pertenece:
                        continue
                    for mod in mods:
                        if not isinstance(mod, dict):
                            continue
                        if str(mod.get("modname", "")) == "assign" and int(mod.get("instance", 0) or 0) == int(assignid):
                            continue
                        materiales_relacionados.append({
                            "nombre": mod.get("name", ""),
                            "tipo": mod.get("modname", ""),
                            "url": mod.get("url", ""),
                            "descripcion": _limpiar_html(mod.get("description", "") or ""),
                        })
                    break
        except Exception as error:
            print("⚠️ No pude obtener materiales relacionados:", error)

    return {
        "ok": True,
        "tarea": tarea,
        "detalle": detalle,
        "archivos_relacionados": archivos,
        "materiales_relacionados": materiales_relacionados,
        "rubrica": rubrica if rubrica.get("disponible") else {},
        "criterios": [],
        "rubrica_disponible": bool(rubrica.get("disponible")),
    }

# =========================================================
# 📤 ENTREGA DIRECTA DE ARCHIVOS A MOODLE
# =========================================================

def subir_archivo_a_moodle(
    ruta_archivo,
    nombre_archivo=None,
):
    """
    Sube un archivo al área temporal (draft) del usuario en Moodle.

    El archivo se envía directamente a Moodle mediante /webservice/upload.php.
    KAIRA no conserva una copia permanente del archivo.
    """
    token = obtener_token_moodle_actual()
    url = obtener_url_moodle_actual()

    if not token:
        return {
            "ok": False,
            "error": "No hay una cuenta de Moodle vinculada."
        }

    if not ruta_archivo or not os.path.isfile(ruta_archivo):
        return {
            "ok": False,
            "error": "El archivo temporal no existe."
        }

    nombre = (
        nombre_archivo
        or os.path.basename(ruta_archivo)
        or "archivo"
    )
    nombre = os.path.basename(nombre)

    endpoint = (
        url.rstrip("/")
        + "/webservice/upload.php"
    )

    try:
        with open(ruta_archivo, "rb") as archivo:
            respuesta = requests.post(
                endpoint,
                params={
                    "token": token,
                    "itemid": 0,
                    "filepath": "/",
                },
                files={
                    "file_1": (
                        nombre,
                        archivo,
                        "application/octet-stream",
                    )
                },
                timeout=60,
            )

        respuesta.raise_for_status()

        datos = respuesta.json()

        if isinstance(datos, dict) and datos.get("error"):
            return {
                "ok": False,
                "error": datos.get(
                    "error",
                    "Moodle rechazó la subida."
                ),
            }

        if not isinstance(datos, list) or not datos:
            return {
                "ok": False,
                "error": "Moodle no devolvió información del archivo."
            }

        primero = datos[0]

        if not isinstance(primero, dict):
            return {
                "ok": False,
                "error": "Respuesta de Moodle no válida."
            }

        if primero.get("error"):
            return {
                "ok": False,
                "error": primero.get(
                    "error",
                    "Moodle rechazó el archivo."
                ),
            }

        draft_itemid = primero.get("itemid")

        if draft_itemid is None:
            return {
                "ok": False,
                "error": "Moodle no devolvió el ID temporal del archivo."
            }

        return {
            "ok": True,
            "draft_itemid": int(draft_itemid),
            "archivo": primero,
        }

    except requests.exceptions.Timeout:
        return {
            "ok": False,
            "error": "Moodle tardó demasiado en recibir el archivo."
        }

    except requests.exceptions.ConnectionError:
        return {
            "ok": False,
            "error": "No pude conectar con Moodle para subir el archivo."
        }

    except Exception as error:
        print(
            "⚠️ Error subiendo archivo a Moodle:",
            error
        )

        return {
            "ok": False,
            "error": "Ocurrió un error al subir el archivo a Moodle."
        }


def guardar_entrega_archivo(
    assignid,
    draft_itemid,
):
    """
    Mueve el archivo desde el draft de Moodle
    al envío de la actividad.
    """
    if not assignid:
        return {
            "ok": False,
            "error": "Actividad no válida."
        }

    if not draft_itemid:
        return {
            "ok": False,
            "error": "Archivo temporal de Moodle no válido."
        }

    respuesta = moodle_api(
        "mod_assign_save_submission",
        {
            "assignmentid": int(assignid),
            "plugindata": {
                "files_filemanager": int(draft_itemid),
            },
        },
    )

    if (
        isinstance(respuesta, dict)
        and respuesta.get("error")
    ):
        return {
            "ok": False,
            "error": respuesta.get(
                "error",
                "Moodle no pudo guardar la entrega."
            ),
            "respuesta": respuesta,
        }

    return {
        "ok": True,
        "respuesta": respuesta,
    }


def enviar_entrega_para_calificacion(
    assignid,
):
    """
    Intenta finalizar la entrega usando
    mod_assign_submit_for_grading.
    """
    respuesta = moodle_api(
        "mod_assign_submit_for_grading",
        {
            "assignmentid": int(assignid),
        },
    )

    if (
        isinstance(respuesta, dict)
        and respuesta.get("error")
    ):
        return {
            "ok": False,
            "error": respuesta.get(
                "error",
                "Moodle no pudo finalizar la entrega."
            ),
            "respuesta": respuesta,
        }

    return {
        "ok": True,
        "respuesta": respuesta,
    }


def obtener_usuario_moodle_id_actual():
    info = obtener_info_moodle()

    if (
        isinstance(info, dict)
        and info.get("userid")
    ):
        try:
            return int(info["userid"])
        except Exception:
            pass

    return None


def entregar_archivo_tarea(
    assignid,
    ruta_archivo,
    nombre_archivo=None,
):
    """
    Flujo completo:

    1. Sube el archivo al draft de Moodle.
    2. Lo guarda como entrega de la actividad.
    3. Intenta finalizar la entrega para calificación.

    El archivo original NO se conserva en KAIRA.
    """
    if not assignid:
        return {
            "ok": False,
            "error": "No se recibió el ID de la actividad."
        }

    subida = subir_archivo_a_moodle(
        ruta_archivo,
        nombre_archivo,
    )

    if not subida.get("ok"):
        return subida

    guardado = guardar_entrega_archivo(
        assignid,
        subida["draft_itemid"],
    )

    if not guardado.get("ok"):
        return {
            "ok": False,
            "fase": "guardar",
            "error": guardado.get(
                "error",
                "No pude guardar la entrega en Moodle."
            ),
        }

    finalizada = enviar_entrega_para_calificacion(
        assignid
    )

    return {
        "ok": True,
        "guardada": True,
        "enviada": bool(
            finalizada.get("ok")
        ),
        "error_envio": (
            None
            if finalizada.get("ok")
            else finalizada.get("error")
        ),
    }


# =========================================================
# EXÁMENES / QUIZZES PRÓXIMOS
# =========================================================

def obtener_examenes_proximos(dias=7):
    """
    Consulta quizzes/exámenes de los cursos del usuario.
    Si el servicio no está habilitado, devuelve una lista vacía.
    """

    cursos = obtener_cursos()

    if not isinstance(cursos, list):
        return cursos

    courseids = [
        curso.get("id")
        for curso in cursos
        if isinstance(curso, dict)
        and curso.get("id")
    ]

    if not courseids:
        return []

    datos = moodle_api(
        "mod_quiz_get_quizzes_by_courses",
        {
            "courseids": courseids
        }
    )

    if not isinstance(datos, dict):
        return datos

    if "error" in datos:
        # No todos los Moodle exponen este servicio al usuario.
        return []

    quizzes = datos.get(
        "quizzes",
        []
    )

    if not isinstance(quizzes, list):
        return []

    ahora = int(time.time())
    limite = int(
        (
            datetime.now()
            + timedelta(days=dias)
        ).timestamp()
    )

    cursos_por_id = {
        curso.get("id"): curso.get(
            "fullname",
            "Curso sin nombre"
        )
        for curso in cursos
        if isinstance(curso, dict)
    }

    resultado = []

    for quiz in quizzes:

        if not isinstance(quiz, dict):
            continue

        # Preferir la apertura del examen; si no existe,
        # usar el cierre como referencia.
        fecha = quiz.get("timeopen") or 0

        if not fecha:
            fecha = quiz.get("timeclose") or 0

        if not fecha:
            continue

        if not (
            ahora <= fecha <= limite
        ):
            continue

        copia = dict(quiz)

        copia["curso_nombre"] = (
            cursos_por_id.get(
                quiz.get("course")
            )
            or quiz.get(
                "coursefullname",
                "Curso desconocido"
            )
        )

        copia["fecha_examen"] = fecha

        resultado.append(copia)

    resultado.sort(
        key=lambda x:
            x.get("fecha_examen", 0)
    )

    return resultado


def resumen_examenes_proximos(dias=7):

    examenes = obtener_examenes_proximos(
        dias=dias
    )

    if isinstance(
        examenes,
        dict
    ) and "error" in examenes:
        return []

    if not examenes:
        return []

    lineas = [
        "📝 Exámenes próximos:"
    ]

    for examen in examenes:

        nombre = examen.get(
            "name",
            "Examen"
        )

        curso = examen.get(
            "curso_nombre",
            "Curso desconocido"
        )

        fecha = formatear_fecha(
            examen.get(
                "fecha_examen",
                0
            )
        )

        lineas.append(
            f"• {nombre}\n"
            f"  📚 Curso: {curso}\n"
            f"  📅 Fecha: {fecha}"
        )

    return lineas


# =========================================================
# RESUMEN ACADÉMICO DIARIO
# =========================================================

def resumen_academico_diario():
    """
    Construye un resumen corto con atrasadas, hoy,
    próximas entregas y exámenes disponibles.
    """

    bloques = [
        "🌅 RESUMEN ACADÉMICO DE KAIRA",
        ""
    ]

    # -----------------------------------------------------
    # ATRASADAS
    # -----------------------------------------------------

    try:
        atrasadas = obtener_tareas_atrasadas()

        if isinstance(atrasadas, list):

            if atrasadas:
                bloques.append(
                    f"🔴 Tienes {len(atrasadas)} "
                    "tarea(s) atrasada(s)."
                )
            else:
                bloques.append(
                    "🟢 No tienes tareas atrasadas pendientes."
                )

    except Exception as error:
        print(
            "⚠️ Error en resumen de atrasadas:",
            error
        )

    # -----------------------------------------------------
    # HOY
    # -----------------------------------------------------

    try:
        hoy = obtener_tareas_dia(
            offset_dias=0
        )

        if isinstance(hoy, list) and hoy:

            bloques.append(
                f"📅 Hoy tienes {len(hoy)} "
                "entrega(s)."
            )

            for tarea in hoy[:5]:

                nombre = tarea.get(
                    "name",
                    "Tarea"
                )

                curso = tarea.get(
                    "curso_nombre",
                    "Curso desconocido"
                )

                fecha = formatear_fecha(
                    tarea.get(
                        "duedate",
                        0
                    )
                )

                bloques.append(
                    f"• {nombre}\n"
                    f"  📚 {curso}\n"
                    f"  ⏰ {fecha}"
                )
        else:
            bloques.append(
                "🟢 Hoy no tienes entregas registradas."
            )

    except Exception as error:
        print(
            "⚠️ Error en resumen de hoy:",
            error
        )

    # -----------------------------------------------------
    # MAÑANA
    # -----------------------------------------------------

    try:
        manana = obtener_tareas_dia(
            offset_dias=1
        )

        if isinstance(manana, list) and manana:

            bloques.append(
                f"📆 Mañana tienes {len(manana)} "
                "entrega(s)."
            )

            for tarea in manana[:5]:

                nombre = tarea.get(
                    "name",
                    "Tarea"
                )

                curso = tarea.get(
                    "curso_nombre",
                    "Curso desconocido"
                )

                fecha = formatear_fecha(
                    tarea.get(
                        "duedate",
                        0
                    )
                )

                bloques.append(
                    f"• {nombre}\n"
                    f"  📚 {curso}\n"
                    f"  ⏰ {fecha}"
                )

    except Exception as error:
        print(
            "⚠️ Error en resumen de mañana:",
            error
        )

    # -----------------------------------------------------
    # EXÁMENES
    # -----------------------------------------------------

    try:
        examenes = resumen_examenes_proximos(
            dias=7
        )

        if examenes:
            bloques.extend(
                ["", *examenes]
            )
        else:
            bloques.append(
                "📝 No encontré exámenes próximos "
                "registrados en Moodle."
            )

    except Exception as error:
        print(
            "⚠️ Error en resumen de exámenes:",
            error
        )

    return "\n".join(
        bloques
    )



# =========================================================
# DETALLE DE TAREA Y ARCHIVOS
# =========================================================

def _limpiar_html(texto):
    if not texto:
        return ""

    import re
    texto = str(texto)

    texto = re.sub(
        r"<br\s*/?>",
        "\n",
        texto,
        flags=re.IGNORECASE
    )

    texto = re.sub(
        r"</p\s*>",
        "\n",
        texto,
        flags=re.IGNORECASE
    )

    texto = re.sub(
        r"<[^>]+>",
        "",
        texto
    )

    return (
        texto
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .strip()
    )


def buscar_tareas_detalladas():
    datos = obtener_tareas()

    if not isinstance(
        datos,
        dict
    ):
        return []

    cursos = datos.get(
        "courses",
        []
    )

    resultado = []

    for curso in cursos:

        curso_nombre = curso.get(
            "fullname",
            "Curso sin nombre"
        )

        for tarea in curso.get(
            "assignments",
            []
        ):

            copia = dict(tarea)

            copia[
                "curso_nombre"
            ] = curso_nombre

            resultado.append(
                copia
            )

    # Orden cronológico: fecha y hora.
    resultado.sort(
        key=lambda x:
            int(
                x.get(
                    "duedate",
                    0
                ) or 0
            )
    )

    return resultado


def buscar_tarea_por_texto(
    texto
):

    texto = str(
        texto
    ).lower().strip()

    tareas = (
        buscar_tareas_detalladas()
    )

    coincidencias = []

    for tarea in tareas:

        nombre = str(
            tarea.get(
                "name",
                ""
            )
        ).lower()

        curso = str(
            tarea.get(
                "curso_nombre",
                ""
            )
        ).lower()

        if (
            texto in nombre
            or nombre in texto
            or texto in curso
        ):

            coincidencias.append(
                tarea
            )

    return coincidencias


def obtener_primera_tarea_relevante():
    """
    Primero intenta tomar la primera tarea atrasada pendiente,
    porque normalmente es la que el usuario acaba de consultar.
    Si no hay atrasadas, toma la primera futura.
    """

    atrasadas = (
        obtener_tareas_atrasadas()
    )

    if isinstance(
        atrasadas,
        list
    ) and atrasadas:

        atrasadas.sort(
            key=lambda x:
                int(
                    x.get(
                        "duedate",
                        0
                    ) or 0
                )
        )

        return atrasadas[0]

    tareas = (
        buscar_tareas_detalladas()
    )

    ahora = int(
        time.time()
    )

    futuras = [
        tarea
        for tarea in tareas
        if int(
            tarea.get(
                "duedate",
                0
            ) or 0
        ) >= ahora
    ]

    if futuras:
        return futuras[0]

    if tareas:
        return tareas[0]

    return None


def detalle_tarea(
    tarea
):

    nombre = tarea.get(
        "name",
        "Tarea sin nombre"
    )

    curso = tarea.get(
        "curso_nombre",
        "Curso desconocido"
    )

    fecha = formatear_fecha(
        tarea.get(
            "duedate",
            0
        )
    )

    descripcion = (
        tarea.get(
            "intro",
            ""
        )
        or tarea.get(
            "description",
            ""
        )
        or tarea.get(
            "content",
            ""
        )
        or ""
    )

    descripcion = _limpiar_html(
        descripcion
    )

    archivos = (
        tarea.get(
            "introattachments",
            []
        )
        or tarea.get(
            "attachments",
            []
        )
        or []
    )

    lineas = [
        "┏━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📚 MATERIA",
        curso,
        "",
        "📝 TEMA / ACTIVIDAD",
        nombre,
        "",
        "📅 ENTREGA",
        fecha,
        "",
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if descripcion:

        lineas.extend([
            "",
            "📋 INDICACIONES",
            descripcion,
        ])

    else:

        lineas.extend([
            "",
            "📋 INDICACIONES",
            "No encontré indicaciones disponibles en Moodle.",
        ])

    if archivos:

        lineas.extend([
            "",
            "📎 ARCHIVOS ADJUNTOS",
        ])

        for archivo in archivos:

            if not isinstance(
                archivo,
                dict
            ):
                continue

            nombre_archivo = (
                archivo.get(
                    "filename"
                )
                or archivo.get(
                    "name"
                )
                or "archivo"
            )

            lineas.append(
                f"• {nombre_archivo}"
            )

    else:

        lineas.extend([
            "",
            "📎 ARCHIVOS ADJUNTOS",
            "No hay archivos adjuntos disponibles.",
        ])

    return {
        "texto": "\n".join(
            lineas
        ),
        "archivos": archivos,
    }


def archivos_de_tarea(
    tarea
):

    archivos = (
        tarea.get(
            "introattachments",
            []
        )
        or tarea.get(
            "attachments",
            []
        )
        or []
    )

    return [
        archivo
        for archivo in archivos
        if isinstance(
            archivo,
            dict
        )
        and (
            archivo.get(
                "fileurl"
            )
            or archivo.get(
                "url"
            )
        )
    ]


def descargar_archivo_moodle(
    archivo,
    carpeta_destino
):

    import os
    import requests

    token = (
        obtener_token_moodle_actual()
    )

    if not token:
        return None

    url = (
        archivo.get(
            "fileurl"
        )
        or archivo.get(
            "url"
        )
    )

    if not url:
        return None

    try:

        respuesta = requests.get(
            url,
            params={
                "token": token,
            },
            timeout=TIMEOUT_MOODLE,
        )

        respuesta.raise_for_status()

        os.makedirs(
            carpeta_destino,
            exist_ok=True,
        )

        nombre = (
            archivo.get(
                "filename"
            )
            or archivo.get(
                "name"
            )
            or "archivo_moodle"
        )

        nombre = os.path.basename(
            nombre
        )

        ruta = os.path.join(
            carpeta_destino,
            nombre
        )

        with open(
            ruta,
            "wb"
        ) as salida:

            salida.write(
                respuesta.content
            )

        return ruta

    except Exception as e:

        print(
            "⚠️ Error descargando archivo Moodle:",
            e
        )

        return None


def resumen_tareas_formateado(
    tareas,
    titulo
):

    if not tareas:

        return (
            f"{titulo}\n\n"
            "🟢 No encontré tareas para ese periodo."
        )

    agrupadas = {}

    for tarea in tareas:

        ts = int(
            tarea.get(
                "duedate",
                0
            ) or 0
        )

        try:

            zona = ZoneInfo(
                "America/Mexico_City"
            )

            dt = datetime.fromtimestamp(
                ts,
                tz=zona
            )

            clave_dia = dt.strftime(
                "%Y-%m-%d"
            )

            fecha = dt.strftime(
                "%d/%m/%Y"
            )

            hora = dt.strftime(
                "%H:%M"
            )

        except Exception:

            clave_dia = "0000-00-00"
            fecha = "Fecha desconocida"
            hora = "Hora desconocida"

        copia = dict(
            tarea
        )

        copia[
            "_fecha"
        ] = fecha

        copia[
            "_hora"
        ] = hora

        agrupadas.setdefault(
            clave_dia,
            []
        ).append(
            copia
        )

    lineas = [
        titulo,
        "",
    ]

    for clave_dia in sorted(
        agrupadas.keys()
    ):

        grupo = sorted(
            agrupadas[clave_dia],
            key=lambda x:
                int(
                    x.get(
                        "duedate",
                        0
                    ) or 0
                )
        )

        lineas.extend([
            f"📅 {grupo[0]['_fecha']}",
            "",
        ])

        for numero, tarea in enumerate(
            grupo,
            start=1
        ):

            nombre = tarea.get(
                "name",
                "Tarea sin nombre"
            )

            curso = tarea.get(
                "curso_nombre",
                "Curso desconocido"
            )

            hora = tarea.get(
                "_hora",
                "Sin hora"
            )

            lineas.extend([
                "┏━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"📚 MATERIA: {curso}",
                f"📝 TEMA: {nombre}",
                f"⏰ HORA: {hora}",
                f"🔢 TAREA: {numero}",
                "┗━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
            ])

    return "\n".join(
        lineas
    )

# =========================================================
# RESUMEN DE TAREAS
# =========================================================




def resumen_tareas(
    dias=7
):

    tareas = obtener_tareas_proximas(
        dias=dias
    )

    if isinstance(
        tareas,
        dict
    ) and "error" in tareas:

        return (
            "No pude consultar Moodle. "
            + tareas["error"]
        )

    titulo = (
        "📚 TAREAS DE HOY"
        if dias == 1
        else (
            "📚 TAREAS DE LA SEMANA"
            if dias == 7
            else (
                "📚 TAREAS DEL MES"
                if dias == 31
                else f"📚 TAREAS DE LOS PRÓXIMOS {dias} DÍAS"
            )
        )
    )

    return resumen_tareas_formateado(
        tareas,
        titulo
    )


# =========================================================
# RESUMEN ACADÉMICO DIARIO
# =========================================================

def resumen_academico_diario():

    """
    Genera un resumen sencillo para el envío automático
    de cada mañana usando exclusivamente el Moodle
    del usuario de Telegram actual.

    Incluye:
        - tareas atrasadas pendientes
        - tareas que vencen hoy
        - tareas que vencen mañana
        - próxima entrega

    Los exámenes no se inventan: solo se mostrarían
    cuando exista una fuente explícita de datos para ellos.
    """

    partes = [
        "🌅 RESUMEN ACADÉMICO DE KAIRA"
    ]

    # -----------------------------------------------------
    # TAREAS ATRASADAS
    # -----------------------------------------------------

    try:

        atrasadas = resumen_tareas_atrasadas()

        if (
            isinstance(atrasadas, str)
            and atrasadas.strip()
        ):

            partes.append(
                "\n" + atrasadas
            )

    except Exception as e:

        print(
            "⚠️ Error generando resumen de tareas atrasadas:",
            e
        )

    # -----------------------------------------------------
    # TAREAS DE HOY
    # -----------------------------------------------------

    try:

        hoy = resumen_tareas_dia(
            offset_dias=0
        )

        if (
            isinstance(hoy, str)
            and hoy.strip()
        ):

            partes.append(
                "\n" + hoy
            )

    except Exception as e:

        print(
            "⚠️ Error generando resumen de hoy:",
            e
        )

    # -----------------------------------------------------
    # TAREAS DE MAÑANA
    # -----------------------------------------------------

    try:

        manana = resumen_tareas_dia(
            offset_dias=1
        )

        if (
            isinstance(manana, str)
            and manana.strip()
        ):

            partes.append(
                "\n" + manana
            )

    except Exception as e:

        print(
            "⚠️ Error generando resumen de mañana:",
            e
        )

    # -----------------------------------------------------
    # PRÓXIMA ENTREGA
    # -----------------------------------------------------

    try:

        proxima = obtener_proxima_entrega()

        if (
            isinstance(proxima, dict)
            and "error" not in proxima
        ):

            nombre = proxima.get(
                "name",
                "Tarea"
            )

            curso = proxima.get(
                "curso_nombre",
                "Curso desconocido"
            )

            fecha = formatear_fecha(
                proxima.get(
                    "duedate",
                    0
                )
            )

            partes.append(
                "\n⭐ Próxima entrega:\n"
                f"• {nombre}\n"
                f"  📚 {curso}\n"
                f"  📅 {fecha}"
            )

        elif proxima is None:

            partes.append(
                "\n⭐ Próxima entrega:\n"
                "No encontré una próxima entrega."
            )

    except Exception as e:

        print(
            "⚠️ Error obteniendo próxima entrega:",
            e
        )

    partes.append(
        "\n💡 Este resumen usa únicamente "
        "la información disponible en tu Moodle."
    )

    return "\n\n".join(
        partes
    )


# =========================================================
# DETECTOR DE COMANDOS DE KAIRA
# =========================================================

def procesar_moodle(
    mensaje
):

    texto = (
        str(mensaje)
        .lower()
        .strip()
    )

    # -----------------------------------------------------
    # CURSOS
    # -----------------------------------------------------

    if any(
        frase in texto
        for frase in (
            "mis cursos",
            "que cursos tengo",
            "qué cursos tengo",
            "mis materias",
            "que materias tengo",
            "qué materias tengo"
        )
    ):

        cursos = obtener_cursos()

        if isinstance(
            cursos,
            dict
        ) and "error" in cursos:

            return (
                "No pude consultar tus cursos. "
                + cursos["error"]
            )

        if not cursos:

            return (
                "No encontré cursos inscritos "
                "en Moodle."
            )

        nombres = []

        for curso in cursos:

            nombre = curso.get(
                "fullname"
            )

            if nombre:

                nombres.append(
                    nombre
                )

        return (
            "Tienes estos cursos:\n\n"
            +
            "\n".join(
                f"• {nombre}"
                for nombre in nombres
            )
        )

    # -----------------------------------------------------
    # TAREAS ATRASADAS
    # -----------------------------------------------------

    if (
        (
            "atrasada" in texto
            or "atrasadas" in texto
            or "atrasado" in texto
            or "atrasados" in texto
            or "pendiente de entregar" in texto
            or "pendientes de entregar" in texto
        )
        and (
            "tarea" in texto
            or "tareas" in texto
            or "trabajo" in texto
            or "trabajos" in texto
            or "entrega" in texto
            or "entregas" in texto
        )
    ):

        return resumen_tareas_atrasadas()


    # -----------------------------------------------------
    # TAREAS QUE VENCEN MAÑANA
    # -----------------------------------------------------

    if (
        "mañana" in texto
        and (
            "tarea" in texto
            or "tareas" in texto
            or "trabajo" in texto
            or "trabajos" in texto
            or "entrega" in texto
            or "entregas" in texto
            or "vence" in texto
            or "vencen" in texto
        )
    ):

        return resumen_tareas_dia(
            offset_dias=1
        )

    # -----------------------------------------------------
    # TAREAS QUE VENCEN HOY
    # -----------------------------------------------------

    if (
        "hoy" in texto
        and (
            "tarea" in texto
            or "tareas" in texto
            or "trabajo" in texto
            or "trabajos" in texto
            or "entrega" in texto
            or "entregas" in texto
            or "vence" in texto
            or "vencen" in texto
        )
    ):

        return resumen_tareas_dia(
            offset_dias=0
        )


    # -----------------------------------------------------
    # ESTE MES
    # -----------------------------------------------------

    if any(
        frase in texto
        for frase in (
            "este mes",
            "este mes que tengo",
            "qué tengo este mes",
            "que tengo este mes",
            "tareas de este mes",
            "trabajos de este mes",
        )
    ):

        return resumen_tareas(
            dias=31
        )

    # -----------------------------------------------------
    # TAREAS
    # -----------------------------------------------------

    if any(
        frase in texto
        for frase in (
            "trabajos pendientes",
            "tareas pendientes",
            "tareas tengo",
            "que tareas tengo",
            "qué tareas tengo",
            "trabajos tengo",
            "que trabajos tengo",
            "qué trabajos tengo",
            "tareas de moodle",
            "trabajos de moodle"
        )
    ):

        return resumen_tareas(
            dias=30
        )

    # -----------------------------------------------------
    # ESTA SEMANA
    # -----------------------------------------------------

    if any(
        frase in texto
        for frase in (
            "esta semana",
            "esta semana que tengo",
            "qué tengo esta semana",
            "que tengo esta semana"
        )
    ):

        return resumen_tareas(
            dias=7
        )

    # -----------------------------------------------------
    # PRÓXIMA ENTREGA
    # -----------------------------------------------------

    if any(
        frase in texto
        for frase in (
            "proxima entrega",
            "próxima entrega",
            "siguiente entrega",
            "que sigue",
            "qué sigue"
        )
    ):

        tarea = obtener_proxima_entrega()

        if isinstance(
            tarea,
            dict
        ) and "error" in tarea:

            return (
                "No pude consultar tu próxima "
                "entrega. "
                + tarea["error"]
            )

        if not tarea:

            return (
                "No encontré una próxima entrega."
            )

        nombre = tarea.get(
            "name",
            "Tarea"
        )

        curso = tarea.get(
            "curso_nombre",
            "Curso desconocido"
        )

        fecha = formatear_fecha(
            tarea.get(
                "duedate",
                0
            )
        )

        return (
            f"Tu próxima entrega es "
            f"{nombre}, de {curso}, "
            f"para el {fecha}."
        )


    # -----------------------------------------------------
    # DETALLE DE UNA TAREA
    # -----------------------------------------------------

    palabras_detalle = (
        "dame las indicaciones",
        "dame indicaciones",
        "que voy a hacer",
        "qué voy a hacer",
        "que debo hacer",
        "qué debo hacer",
        "detalles de la tarea",
        "detalle de la tarea",
        "indicaciones de la tarea",
        "abre la primera",
        "dame la primera",
        "dame la primera tarea",
        "muéstrame la primera",
        "muestrame la primera",
        "primera tarea",
    )

    if any(
        frase in texto
        for frase in palabras_detalle
    ):

        tarea = None

        # Caso más claro: "primera" -> primera atrasada
        # si existe; en caso contrario primera futura.
        if (
            "primera" in texto
            or "primero" in texto
        ):

            tarea = (
                obtener_primera_tarea_relevante()
            )

        # Intentar encontrar una coincidencia por nombre.
        if tarea is None:

            coincidencias = (
                buscar_tarea_por_texto(
                    texto
                )
            )

            if coincidencias:

                tarea = coincidencias[0]

        if tarea is not None:

            detalle = detalle_tarea(
                tarea
            )

            return detalle[
                "texto"
            ]

        return (
            "❌ No pude localizar la tarea "
            "para mostrarte sus indicaciones."
        )

    return None
