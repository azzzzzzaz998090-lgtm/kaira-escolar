# =========================================================
# KAIRA - MEMBRESIAS
# =========================================================

import os
import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "membresias.db")
ZONA = ZoneInfo("America/Mexico_City")


def conectar():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def inicializar():
    con = conectar()
    con.execute("""
        CREATE TABLE IF NOT EXISTS membresias (
            telegram_user_id INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL DEFAULT '',
            apellido TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            fecha_registro TEXT NOT NULL,
            fecha_inicio TEXT,
            fecha_vencimiento TEXT,
            precio REAL DEFAULT 0,
            pagado INTEGER DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            metodo_pago TEXT DEFAULT '',
            notas TEXT DEFAULT '',
            tipo_membresia TEXT DEFAULT '',
            prueba_usada INTEGER DEFAULT 0
        )
    """)
    con.commit()
    con.close()



def asegurar_tabla_historial_pagos():
    con = conectar()

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS historial_pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL,
            fecha_pago TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT '',
            precio REAL DEFAULT 0,
            metodo_pago TEXT DEFAULT '',
            observaciones TEXT DEFAULT ''
        )
        """
    )

    con.commit()
    con.close()


def asegurar_columnas():
    con = conectar()
    columnas = {
        fila[1]
        for fila in con.execute(
            "PRAGMA table_info(membresias)"
        ).fetchall()
    }

    if "tipo_membresia" not in columnas:
        con.execute(
            "ALTER TABLE membresias ADD COLUMN tipo_membresia TEXT DEFAULT ''"
        )

    if "prueba_usada" not in columnas:
        con.execute(
            "ALTER TABLE membresias ADD COLUMN prueba_usada INTEGER DEFAULT 0"
        )

    con.commit()
    con.close()



def ahora_iso():
    return datetime.now(ZONA).isoformat(timespec="seconds")


def crear_o_actualizar_usuario(
    telegram_user_id,
    nombre="",
    apellido="",
    username="",
):
    """
    Crea el perfil de membresía. Para un usuario nuevo autorizado,
    inicia automáticamente una prueba gratuita de 5 días.
    La prueba se usa una sola vez y no genera un registro de venta.
    """
    uid = int(telegram_user_id)
    con = conectar()

    fila = con.execute(
        "SELECT * FROM membresias WHERE telegram_user_id = ?",
        (uid,)
    ).fetchone()

    if fila is None:
        inicio = ahora_iso()
        vencimiento = (
            datetime.now(ZONA) + timedelta(days=5)
        ).isoformat(timespec="seconds")

        con.execute("""
            INSERT INTO membresias (
                telegram_user_id, nombre, apellido, username,
                fecha_registro, fecha_inicio, fecha_vencimiento,
                precio, pagado, estado, metodo_pago, notas,
                tipo_membresia, prueba_usada
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 'activa', '', '', 'Prueba', 1)
        """, (
            uid,
            (nombre or "").strip(),
            (apellido or "").strip(),
            (username or "").strip(),
            inicio,
            inicio,
            vencimiento,
        ))
    else:
        # Nunca reiniciar una prueba existente. Solo actualizar datos básicos.
        con.execute("""
            UPDATE membresias
            SET nombre = ?, apellido = ?, username = ?
            WHERE telegram_user_id = ?
        """, (
            (nombre or "").strip(),
            (apellido or "").strip(),
            (username or "").strip(),
            uid,
        ))

    con.commit()
    con.close()


def actualizar_moodle_nombre(
    telegram_user_id,
    nombre,
    apellido,
):
    crear_o_actualizar_usuario(
        telegram_user_id,
        nombre,
        apellido,
        "",
    )



# =========================================================
# PLANES DE MEMBRESÍA Y FECHAS AUTOMÁTICAS
# =========================================================

ZONA_MEXICO = ZoneInfo(
    "America/Mexico_City"
)


PLANES_MEMBRESIA = {
    "Semanal": {
        "dias": 7,
    },
    "Mensual": {
        "meses": 1,
    },
    "Trimestral": {
        "meses": 3,
    },
    "Semestral": {
        "meses": 6,
    },
    "Anual": {
        "meses": 12,
    },
}

ARCHIVO_PRECIOS_PLANES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "precios_membresias.json",
)


PRECIOS_PLANES_DEFECTO = {
    "Semanal": 0.0,
    "Mensual": 0.0,
    "Trimestral": 0.0,
    "Semestral": 0.0,
    "Anual": 0.0,
}


def cargar_precios_planes():
    try:
        if os.path.exists(
            ARCHIVO_PRECIOS_PLANES
        ):
            with open(
                ARCHIVO_PRECIOS_PLANES,
                "r",
                encoding="utf-8",
            ) as f:
                datos = json.load(f)

            if isinstance(datos, dict):
                resultado = dict(
                    PRECIOS_PLANES_DEFECTO
                )

                for plan in resultado:
                    try:
                        resultado[plan] = float(
                            datos.get(
                                plan,
                                resultado[plan]
                            )
                        )
                    except Exception:
                        pass

                return resultado
    except Exception as error:
        print(
            "⚠️ Error leyendo precios de membresías:",
            error
        )

    return dict(
        PRECIOS_PLANES_DEFECTO
    )


def guardar_precios_planes(
    precios
):
    datos = dict(
        PRECIOS_PLANES_DEFECTO
    )

    for plan in datos:
        try:
            datos[plan] = float(
                precios.get(
                    plan,
                    datos[plan]
                )
            )
        except Exception:
            pass

    tmp = ARCHIVO_PRECIOS_PLANES + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            datos,
            f,
            ensure_ascii=False,
            indent=4
        )

    os.replace(
        tmp,
        ARCHIVO_PRECIOS_PLANES
    )

    return datos



def listar_precios_planes():
    return cargar_precios_planes()



def obtener_precio_plan(
    tipo_membresia
):
    precios = cargar_precios_planes()

    return float(
        precios.get(
            tipo_membresia,
            0.0
        )
    )


def establecer_precio_plan(
    tipo_membresia,
    precio
):
    if tipo_membresia not in PRECIOS_PLANES_DEFECTO:
        raise ValueError(
            "Plan de membresía no válido."
        )

    precio = float(precio)

    if precio < 0:
        raise ValueError(
            "El precio no puede ser negativo."
        )

    precios = cargar_precios_planes()

    precios[
        tipo_membresia
    ] = precio

    return guardar_precios_planes(
        precios
    )


def aplicar_precio_plan(
    tipo_membresia,
    precio=None
):
    """
    Precio centralizado:
    - si viene un precio explícito > 0, se respeta;
    - si viene vacío/0, se toma el precio global del plan.
    """
    if precio is not None:
        try:
            precio_numerico = float(
                precio
            )
            if precio_numerico > 0:
                return precio_numerico
        except Exception:
            pass

    return obtener_precio_plan(
        tipo_membresia
    )




def fecha_hora_actual_local():
    return datetime.now(
        ZONA_MEXICO
    )


def parsear_fecha_local(valor):
    if not valor:
        return None

    try:
        texto = str(
            valor
        ).strip()

        dt = datetime.fromisoformat(
            texto
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=ZONA_MEXICO
            )

        return dt

    except Exception:
        return None


def fecha_iso_local(dt):
    if dt is None:
        return ""

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=ZONA_MEXICO
        )

    return dt.isoformat(
        timespec="minutes"
    )


def sumar_meses(dt, meses):
    """
    Suma meses sin depender de librerías externas.
    Conserva el día cuando es posible y ajusta al último día
    del mes cuando sea necesario.
    """

    total = (
        dt.year * 12
        + (dt.month - 1)
        + int(meses)
    )

    nuevo_anio = total // 12
    nuevo_mes = total % 12 + 1

    # Días por mes.
    if nuevo_mes == 12:
        siguiente = datetime(
            nuevo_anio + 1,
            1,
            1,
            tzinfo=ZONA_MEXICO,
        )
    else:
        siguiente = datetime(
            nuevo_anio,
            nuevo_mes + 1,
            1,
            tzinfo=ZONA_MEXICO,
        )

    ultimo_dia = (
        siguiente.date()
        - __import__("datetime").timedelta(
            days=1
        )
    ).day

    return dt.replace(
        year=nuevo_anio,
        month=nuevo_mes,
        day=min(
            dt.day,
            ultimo_dia
        ),
    )


def calcular_fecha_vencimiento(
    fecha_inicio=None,
    tipo_membresia="Mensual",
):
    """
    Devuelve automáticamente la fecha de corte del plan.
    """

    dt_inicio = (
        parsear_fecha_local(
            fecha_inicio
        )
        if fecha_inicio
        else fecha_hora_actual_local()
    )

    if dt_inicio is None:
        dt_inicio = fecha_hora_actual_local()

    plan = PLANES_MEMBRESIA.get(
        tipo_membresia,
        {}
    )

    if "dias" in plan:
        vencimiento = (
            dt_inicio
            + __import__("datetime").timedelta(
                days=plan["dias"]
            )
        )
    elif "meses" in plan:
        vencimiento = sumar_meses(
            dt_inicio,
            plan["meses"]
        )
    else:
        # Personalizada: se conserva la fecha existente.
        vencimiento = dt_inicio

    return fecha_iso_local(
        vencimiento
    )


def normalizar_fechas_membresia(
    fecha_inicio="",
    fecha_vencimiento="",
    tipo_membresia="Mensual",
    pagado=0,
):
    """
    Si el usuario está pagado y no hay una fecha de corte,
    la calcula automáticamente desde la fecha de inicio.
    """

    inicio = fecha_inicio or ""
    vencimiento = fecha_vencimiento or ""

    if int(pagado or 0) == 1:

        if not inicio:
            inicio = fecha_iso_local(
                fecha_hora_actual_local()
            )

        if (
            not vencimiento
            and tipo_membresia
            != "Personalizada"
        ):
            vencimiento = (
                calcular_fecha_vencimiento(
                    inicio,
                    tipo_membresia,
                )
            )

    return (
        inicio,
        vencimiento,
    )


def registrar_membresia(
    telegram_user_id,
    nombre,
    apellido,
    fecha_inicio="",
    fecha_vencimiento="",
    precio=0,
    pagado=0,
    metodo_pago="",
    notas="",
    estado=None,
    tipo_membresia="",
):
    uid = int(
        telegram_user_id
    )

    tipo_membresia = (
        tipo_membresia or "Mensual"
    ).strip()

    fecha_inicio, fecha_vencimiento = (
        normalizar_fechas_membresia(
            fecha_inicio=fecha_inicio,
            fecha_vencimiento=fecha_vencimiento,
            tipo_membresia=tipo_membresia,
            pagado=pagado,
        )
    )

    if estado is None:

        if int(pagado or 0) == 1:
            estado = "activa"
        else:
            estado = "pendiente"

    precio = aplicar_precio_plan(
        tipo_membresia,
        precio,
    )

    con = conectar()

    existente = con.execute(
        """
        SELECT fecha_registro
        FROM membresias
        WHERE telegram_user_id = ?
        """,
        (uid,),
    ).fetchone()

    # Se fija UNA SOLA vez al crear el perfil.
    fecha_registro = (
        existente["fecha_registro"]
        if existente
        and existente["fecha_registro"]
        else fecha_iso_local(
            fecha_hora_actual_local()
        )
    )

    # SQLite migrations: ensure new column exists.
    columnas = {
        fila[1]
        for fila in con.execute(
            "PRAGMA table_info(membresias)"
        ).fetchall()
    }

    if "tipo_membresia" not in columnas:
        con.execute(
            "ALTER TABLE membresias "
            "ADD COLUMN tipo_membresia TEXT DEFAULT ''"
        )

    if "prueba_usada" not in columnas:
        con.execute(
            "ALTER TABLE membresias "
            "ADD COLUMN prueba_usada INTEGER DEFAULT 0"
        )

    con.execute(
        """
        INSERT INTO membresias (
            telegram_user_id,
            nombre,
            apellido,
            fecha_registro,
            fecha_inicio,
            fecha_vencimiento,
            precio,
            pagado,
            estado,
            metodo_pago,
            notas,
            tipo_membresia,
            prueba_usada
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_user_id) DO UPDATE SET
            nombre=excluded.nombre,
            apellido=excluded.apellido,
            fecha_inicio=excluded.fecha_inicio,
            fecha_vencimiento=excluded.fecha_vencimiento,
            precio=excluded.precio,
            pagado=excluded.pagado,
            estado=excluded.estado,
            metodo_pago=excluded.metodo_pago,
            notas=excluded.notas,
            tipo_membresia=excluded.tipo_membresia,
            prueba_usada=excluded.prueba_usada
        """,
        (
            uid,
            (nombre or "").strip(),
            (apellido or "").strip(),
            fecha_registro,
            fecha_inicio,
            fecha_vencimiento,
            float(precio or 0),
            int(pagado or 0),
            estado,
            (metodo_pago or "").strip(),
            (notas or "").strip(),
            tipo_membresia,
            1 if tipo_membresia == "Prueba" else 1,
        ),
    )

    con.commit()
    con.close()



def registrar_pago(
    telegram_user_id,
    plan,
    precio,
    metodo_pago="",
    observaciones="",
):
    uid=int(telegram_user_id)
    con=conectar()
    con.execute(
        """
        INSERT INTO historial_pagos (
            telegram_user_id,
            fecha_pago,
            plan,
            precio,
            metodo_pago,
            observaciones
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            ahora_iso(),
            (plan or "").strip(),
            float(precio or 0),
            (metodo_pago or "").strip(),
            (observaciones or "").strip(),
        )
    )
    con.commit()
    con.close()


def historial_pagos(
    telegram_user_id,
    limite=20,
):
    uid=int(telegram_user_id)

    con=conectar()
    filas=con.execute(
        """
        SELECT
            id,
            telegram_user_id,
            fecha_pago,
            plan,
            precio,
            metodo_pago,
            observaciones
        FROM historial_pagos
        WHERE telegram_user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (uid, int(limite)),
    ).fetchall()

    con.close()

    return [
        dict(fila)
        for fila in filas
    ]


def resumen_pagos(
    telegram_user_id=None
):
    con=conectar()

    if telegram_user_id is None:
        fila=con.execute(
            """
            SELECT
                COUNT(*) AS cantidad,
                COALESCE(SUM(precio),0) AS total
            FROM historial_pagos
            """
        ).fetchone()
    else:
        fila=con.execute(
            """
            SELECT
                COUNT(*) AS cantidad,
                COALESCE(SUM(precio),0) AS total
            FROM historial_pagos
            WHERE telegram_user_id = ?
            """,
            (int(telegram_user_id),),
        ).fetchone()

    con.close()

    return {
        "cantidad": int(fila["cantidad"] or 0),
        "total": float(fila["total"] or 0),
    }



def marcar_pago(
    telegram_user_id,
    pagado,
    metodo_pago=None,
    notas=None,
):
    uid = int(telegram_user_id)
    con = conectar()

    campos = ["pagado = ?"]
    valores = [1 if pagado else 0]

    if pagado:
        campos.append("estado = 'activa'")
    else:
        campos.append("estado = 'pendiente'")

    if metodo_pago is not None:
        campos.append("metodo_pago = ?")
        valores.append(metodo_pago)

    if notas is not None:
        campos.append("notas = ?")
        valores.append(notas)

    valores.append(uid)

    con.execute(
        f"UPDATE membresias SET {', '.join(campos)} WHERE telegram_user_id = ?",
        valores,
    )
    con.commit()
    con.close()


def renovar(
    telegram_user_id,
    fecha_inicio="",
    fecha_vencimiento="",
    precio=None,
    tipo_membresia=None,
    metodo_pago=None,
):
    uid = int(
        telegram_user_id
    )

    con = conectar()

    if not fecha_inicio:
        fecha_inicio = fecha_iso_local(
            fecha_hora_actual_local()
        )

    if (
        not fecha_vencimiento
        and tipo_membresia
        and tipo_membresia != "Personalizada"
    ):
        fecha_vencimiento = (
            calcular_fecha_vencimiento(
                fecha_inicio,
                tipo_membresia,
            )
        )

    campos = [
        "fecha_inicio = ?",
        "fecha_vencimiento = ?",
        "estado = 'activa'",
        "pagado = 1",
    ]

    valores = [
        fecha_inicio,
        fecha_vencimiento,
    ]

    precio_final = aplicar_precio_plan(
        tipo_membresia or "Mensual",
        precio,
    )

    campos.append("precio = ?")
    valores.append(
        float(precio_final)
    )

    if tipo_membresia is not None:
        campos.append("tipo_membresia = ?")
        valores.append(
            tipo_membresia
        )

    if metodo_pago is not None:
        campos.append("metodo_pago = ?")
        valores.append(
            metodo_pago
        )

    valores.append(
        uid
    )

    con.execute(
        f"""
        UPDATE membresias
        SET {', '.join(campos)}
        WHERE telegram_user_id = ?
        """,
        valores,
    )

    con.commit()
    con.close()

    registrar_pago(
        uid,
        tipo_membresia or "Mensual",
        precio_final,
        metodo_pago or "",
        "Renovación aprobada",
    )



def obtener_usuario(telegram_user_id):
    con = conectar()
    fila = con.execute(
        "SELECT * FROM membresias WHERE telegram_user_id = ?",
        (int(telegram_user_id),)
    ).fetchone()
    con.close()
    return dict(fila) if fila else None





def marcar_pago_con_plan(
    telegram_user_id,
    tipo_membresia,
    precio=0,
    metodo_pago="",
    fecha_inicio=None,
):
    """
    Marca como pagado y recalcula automáticamente la fecha de
    vencimiento según el plan.
    """
    uid = int(
        telegram_user_id
    )

    inicio = (
        fecha_inicio
        or fecha_iso_local(
            fecha_hora_actual_local()
        )
    )

    vencimiento = calcular_fecha_vencimiento(
        inicio,
        tipo_membresia,
    )

    return registrar_membresia(
        uid,
        "",
        "",
        inicio,
        vencimiento,
        precio,
        1,
        metodo_pago,
        "",
        "activa",
        tipo_membresia,
    )



def marcar_baja_usuario(
    telegram_user_id
):
    """
    Marca la membresía como baja solicitada por el usuario,
    conservando el historial para administración.
    """
    uid = int(telegram_user_id)

    con = conectar()
    con.execute(
        """
        UPDATE membresias
        SET estado = 'baja_usuario',
            pagado = 0
        WHERE telegram_user_id = ?
        """,
        (uid,)
    )
    afectadas = con.total_changes
    con.commit()
    con.close()

    return afectadas > 0



def eliminar_usuario(
    telegram_user_id
):
    """
    Elimina definitivamente el registro de membresía de un usuario.
    No modifica Telegram; eso lo realiza el panel para mantener
    ambas fuentes sincronizadas.
    """

    uid = int(
        telegram_user_id
    )

    con = conectar()

    con.execute(
        "DELETE FROM membresias WHERE telegram_user_id = ?",
        (uid,)
    )

    afectadas = con.total_changes

    con.commit()
    con.close()

    return afectadas > 0



def listar_usuarios():
    con = conectar()
    filas = con.execute("""
        SELECT *
        FROM membresias
        ORDER BY
            CASE
                WHEN fecha_vencimiento IS NULL OR fecha_vencimiento = '' THEN 1
                ELSE 0
            END,
            fecha_vencimiento ASC,
            nombre ASC
    """).fetchall()
    con.close()
    return [dict(fila) for fila in filas]


def dias_para_vencer(fecha_vencimiento):
    if not fecha_vencimiento:
        return None
    try:
        fecha = datetime.fromisoformat(fecha_vencimiento)
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=ZONA)
        ahora = datetime.now(ZONA)
        return (fecha.date() - ahora.date()).days
    except Exception:
        return None


def actualizar_estados():
    con = conectar()
    filas = con.execute("""
        SELECT telegram_user_id, fecha_vencimiento, pagado, estado
        FROM membresias
    """).fetchall()

    for fila in filas:
        dias = dias_para_vencer(fila["fecha_vencimiento"])
        if dias is None:
            continue

        if dias < 0:
            nuevo = "vencida"
            # El vencimiento termina el derecho de uso; el historial de pago
            # se conserva por separado. La ficha operativa pasa a no pagada.
            con.execute(
                "UPDATE membresias SET estado = ?, pagado = 0 WHERE telegram_user_id = ?",
                (nuevo, fila["telegram_user_id"])
            )
            continue
        elif int(fila["pagado"] or 0) == 0:
            nuevo = "pendiente"
        else:
            nuevo = "activa"

        con.execute(
            "UPDATE membresias SET estado = ? WHERE telegram_user_id = ?",
            (nuevo, fila["telegram_user_id"])
        )

    con.commit()
    con.close()



def limpiar_usuarios_inactivos(dias=30):
    """
    Elimina únicamente fichas operativas que llevan al menos `dias`
    vencidas/no pagadas. El historial_pagos nunca se elimina aquí.
    Devuelve la lista de IDs eliminados.
    """
    try:
        dias = max(1, int(dias))
    except Exception:
        dias = 30

    ahora = datetime.now(ZONA)
    limite = ahora - timedelta(days=dias)
    eliminados = []

    con = conectar()
    filas = con.execute("""
        SELECT telegram_user_id, fecha_vencimiento, pagado, estado
        FROM membresias
    """).fetchall()

    for fila in filas:
        fecha = parsear_fecha_local(fila["fecha_vencimiento"])
        if not fecha or fecha >= limite:
            continue

        # Solo se limpian cuentas ya vencidas y que no están pagadas.
        if int(fila["pagado"] or 0) != 0:
            continue

        uid = int(fila["telegram_user_id"])
        con.execute(
            "DELETE FROM membresias WHERE telegram_user_id = ?",
            (uid,)
        )
        eliminados.append(uid)

    con.commit()
    con.close()
    return eliminados


def resumen_admin(
    excluir_telegram_id=None
):
    usuarios=listar_usuarios()

    if excluir_telegram_id is not None:
        usuarios=[
            u for u in usuarios
            if int(
                u.get("telegram_user_id",0)
            ) != int(excluir_telegram_id)
        ]

    total=len(usuarios)
    activos=sum(
        1 for u in usuarios
        if u.get("estado") == "activa"
    )
    vencidos=sum(
        1 for u in usuarios
        if u.get("estado") == "vencida"
    )
    pagados=sum(
        1 for u in usuarios
        if int(u.get("pagado",0) or 0)==1
    )
    pendientes=total-pagados

    por_vencer=0
    for u in usuarios:
        fecha=u.get("fecha_vencimiento")
        if not fecha:
            continue

        dias=dias_para_vencer(
            fecha
        )

        if dias is not None and 0 <= dias <= 7:
            por_vencer += 1

    pagos = resumen_pagos()

    return {
        "total": total,
        "activos": activos,
        "vencidos": vencidos,
        "pagados": pagados,
        "pendientes": pendientes,
        "por_vencer_7_dias": por_vencer,
        "ingresos": pagos["total"],
    }


def resumen():
    actualizar_estados()
    usuarios = listar_usuarios()

    activos = sum(u["estado"] == "activa" for u in usuarios)
    pendientes = sum(u["estado"] == "pendiente" for u in usuarios)
    vencidos = sum(u["estado"] == "vencida" for u in usuarios)

    por_vencer = 0
    for u in usuarios:
        d = dias_para_vencer(u["fecha_vencimiento"])
        if d is not None and 0 <= d <= 7:
            por_vencer += 1

    return {
        "total": len(usuarios),
        "activos": activos,
        "pendientes": pendientes,
        "vencidos": vencidos,
        "por_vencer_7_dias": por_vencer,
    }


inicializar()


def acceso_permitido(telegram_user_id):
    """
    Admin is handled by Telegram. For regular users:
    - no record => False
    - active => True
    - pending => True (allows onboarding until admin decides otherwise)
    - expired => False
    """
    usuario = obtener_usuario(telegram_user_id)
    if usuario is None:
        return False

    estado = usuario.get("estado", "pendiente")

    return estado == "activa"


def obtener_alertas_vencimiento(dias_max=7):
    actualizar_estados()
    resultado = []

    for usuario in listar_usuarios():
        dias = dias_para_vencer(
            usuario.get("fecha_vencimiento")
        )

        if dias is None:
            continue

        if dias <= dias_max:
            resultado.append({
                "telegram_user_id": usuario["telegram_user_id"],
                "nombre": (
                    (usuario.get("nombre") or "")
                    + " "
                    + (usuario.get("apellido") or "")
                ).strip(),
                "dias": dias,
                "estado": usuario.get("estado"),
                "fecha_vencimiento": usuario.get("fecha_vencimiento"),
            })

    resultado.sort(
        key=lambda x: (x["dias"], x["nombre"])
    )

    return resultado

asegurar_columnas()

# Ejecutar migración/creación del historial al iniciar.
asegurar_tabla_historial_pagos()
