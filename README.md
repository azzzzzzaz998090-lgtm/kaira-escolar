# =========================================================
# REVISOR DE TRABAJOS - KAIRA
# Revisión previa, sin modificar ni entregar en Moodle.
# =========================================================

import os
import re
import json
import html
import time
import hashlib
from pathlib import Path


MAX_TEXTO = 45000
MAX_ITEMS = 40


def _limpiar_html(texto):
    texto = str(texto or "")
    texto = re.sub(r"<br\\s*/?>", "\n", texto, flags=re.I)
    texto = re.sub(r"</p>|</div>|</li>|</h[1-6]>", "\n", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = html.unescape(texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def _recortar(texto, limite=MAX_TEXTO):
    texto = str(texto or "")
    if len(texto) <= limite:
        return texto
    return texto[:limite] + "\n[CONTENIDO RECORTADO POR LONGITUD]"


def extraer_documento(ruta, nombre_archivo=None):
    nombre = os.path.basename(nombre_archivo or ruta or "archivo")
    ext = Path(nombre).suffix.lower()
    texto = []
    meta = {
        "nombre_archivo": nombre,
        "extension": ext,
        "paginas": None,
        "hojas": [],
        "diapositivas": None,
    }

    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(ruta)
        meta["paginas"] = len(reader.pages)
        for i, page in enumerate(reader.pages, start=1):
            try:
                contenido = page.extract_text() or ""
            except Exception:
                contenido = ""
            texto.append(f"[PÁGINA {i}]\n{contenido}")

    elif ext == ".docx":
        from docx import Document
        doc = Document(ruta)
        for p in doc.paragraphs:
            if p.text.strip():
                texto.append(p.text.strip())
        for ti, table in enumerate(doc.tables, start=1):
            texto.append(f"[TABLA {ti}]")
            for row in table.rows:
                texto.append(" | ".join(cell.text.strip() for cell in row.cells))

    elif ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(ruta, data_only=False, read_only=True)
        meta["hojas"] = list(wb.sheetnames)
        for ws in wb.worksheets:
            texto.append(f"[HOJA: {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                valores = ["" if v is None else str(v) for v in row]
                if any(v.strip() for v in valores):
                    texto.append(" | ".join(valores))

    elif ext == ".pptx":
        from pptx import Presentation
        prs = Presentation(ruta)
        meta["diapositivas"] = len(prs.slides)
        for i, slide in enumerate(prs.slides, start=1):
            partes = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text:
                    partes.append(shape.text.strip())
            texto.append(f"[DIAPOSITIVA {i}]\n" + "\n".join(partes))

    else:
        raise ValueError("Formato no compatible. Usa PDF, DOCX, XLSX o PPTX.")

    contenido = _recortar("\n\n".join(x for x in texto if x).strip())
    if not contenido:
        raise ValueError("No pude extraer texto del archivo. Si es un PDF escaneado, esta primera versión requiere OCR.")

    return {
        "meta": meta,
        "texto": contenido,
    }


def _normalizar_contexto(contexto):
    contexto = dict(contexto or {})
    tarea = contexto.get("tarea") or {}
    return {
        "actividad": tarea.get("name", "Actividad"),
        "curso": tarea.get("curso_nombre", "Curso"),
        "fecha_limite": tarea.get("duedate", 0),
        "instrucciones": _limpiar_html(
            tarea.get("intro") or tarea.get("description") or tarea.get("content") or ""
        ),
        "archivos_relacionados": contexto.get("archivos_relacionados", []),
        "materiales_relacionados": contexto.get("materiales_relacionados", []),
        "rubrica": contexto.get("rubrica") or {},
        "criterios": contexto.get("criterios") or [],
        "formato_solicitado": contexto.get("formato_solicitado") or "",
        "requisitos_detectados": contexto.get("requisitos_detectados") or [],
    }


def _prompt_revision(contexto, documento):
    base = _normalizar_contexto(contexto)
    meta = documento["meta"]
    texto = documento["texto"]

    return f"""
Eres el revisor académico previo de KAIRA. NO eres el profesor y NO asignas una calificación.

Tu trabajo es comparar el documento del alumno EXCLUSIVAMENTE contra requisitos que estén respaldados por la información de Moodle proporcionada abajo.

REGLAS OBLIGATORIAS:
1. Distingue siempre entre REQUISITO_EN_MOODLE y RECOMENDACION_IA.
2. Nunca inventes una portada, introducción, conclusión, referencias, número de páginas, formato o estructura como requisito si Moodle no lo exige.
3. Si no existe rúbrica/lista de cotejo disponible, dilo claramente.
4. Si algo no puede verificarse con el texto extraído, usa "no_verificable" en vez de afirmar que falta.
5. La fecha límite se informa, pero NO se usa para calificar el contenido.
6. El nombre del archivo solo es requisito si Moodle lo establece explícitamente. Si no, no lo marques como incorrecto.
7. Puedes hacer recomendaciones de calidad académica, pero deben aparecer separadas de los requisitos del profesor.
8. No digas que el trabajo tendrá cierta calificación.

ACTIVIDAD:
{base['actividad']}

CURSO:
{base['curso']}

FECHA LÍMITE (timestamp Moodle):
{base['fecha_limite']}

INSTRUCCIONES REALES DE MOODLE:
{base['instrucciones'] or '[NO DISPONIBLES]'}

ARCHIVOS RELACIONADOS DE MOODLE:
{json.dumps(base['archivos_relacionados'], ensure_ascii=False, indent=2)[:12000]}

MATERIALES RELACIONADOS EN LA MISMA SECCIÓN DE MOODLE:
{json.dumps(base['materiales_relacionados'], ensure_ascii=False, indent=2)[:12000]}

RÚBRICA REAL DE MOODLE:
{json.dumps(base['rubrica'], ensure_ascii=False, indent=2)[:16000]}

CRITERIOS/LISTA DE COTEJO DISPONIBLES:
{json.dumps(base['criterios'], ensure_ascii=False, indent=2)[:12000]}

FORMATO SOLICITADO EXPLÍCITAMENTE:
{base['formato_solicitado'] or '[NO ESPECIFICADO]'}

METADATOS DEL ARCHIVO DEL ALUMNO:
{json.dumps(meta, ensure_ascii=False, indent=2)}

CONTENIDO EXTRAÍDO DEL ARCHIVO:
{texto}

Devuelve SOLO JSON válido con esta estructura:
{{
  "resumen": "...",
  "requisitos": [
    {{"requisito": "...", "estado": "cumplido|faltante|no_verificable", "evidencia": "..."}}
  ],
  "formato": [
    {{"requisito": "...", "estado": "cumplido|faltante|no_verificable", "evidencia": "..."}}
  ],
  "recomendaciones": ["..."],
  "rubrica_disponible": true,
  "nota": "Esta revisión no sustituye la calificación del profesor."
}}
"""


def _parse_json(texto):
    texto = str(texto or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?", "", texto, flags=re.I).strip()
        texto = re.sub(r"```$", "", texto).strip()
    try:
        return json.loads(texto)
    except Exception:
        inicio = texto.find("{")
        fin = texto.rfind("}")
        if inicio >= 0 and fin > inicio:
            return json.loads(texto[inicio:fin + 1])
        raise


def revisar_con_gemini(contexto, documento, ruta_archivo=None):
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
    if not api_key:
        raise RuntimeError("Falta GEMINI_API_KEY.")

    from google import genai
    client = genai.Client(api_key=api_key)
    prompt = _prompt_revision(contexto, documento)
    contents = prompt
    # Para PDF, además del texto extraído, damos a Gemini el archivo en bytes
    # para que pueda interpretar mejor tablas o contenido visual cuando el
    # modelo/SDK lo admita. Si falla, continuamos con el texto extraído.
    if ruta_archivo and Path(ruta_archivo).suffix.lower() == ".pdf":
        try:
            from google.genai import types
            contents = [
                types.Part.from_bytes(
                    data=Path(ruta_archivo).read_bytes(),
                    mime_type="application/pdf",
                ),
                prompt,
            ]
        except Exception:
            contents = prompt

    respuesta = client.models.generate_content(
        model=model,
        contents=contents,
    )
    resultado = _parse_json(getattr(respuesta, "text", ""))
    if not isinstance(resultado, dict):
        raise ValueError("Gemini no devolvió una revisión válida.")
    resultado.setdefault("requisitos", [])
    resultado.setdefault("formato", [])
    resultado.setdefault("recomendaciones", [])
    resultado.setdefault("rubrica_disponible", bool((contexto or {}).get("rubrica")))
    resultado.setdefault("nota", "Esta revisión no sustituye la calificación del profesor.")
    return resultado


def guardar_revision_supabase(telegram_user_id, assignid, nombre_archivo, resultado):
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(url, sslmode="require", connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO kaira_revisiones
                    (telegram_user_id, moodle_assign_id, nombre_archivo, tipo_archivo, resultado, observaciones)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                    RETURNING id
                    """,
                    (
                        int(telegram_user_id),
                        int(assignid),
                        nombre_archivo,
                        Path(nombre_archivo).suffix.lower(),
                        json.dumps(resultado, ensure_ascii=False),
                        str(resultado.get("resumen", ""))[:4000],
                    ),
                )
                revision_id = cur.fetchone()[0]
                for grupo in (resultado.get("requisitos", []) or []) + (resultado.get("formato", []) or []):
                    if not isinstance(grupo, dict):
                        continue
                    cur.execute(
                        """
                        INSERT INTO kaira_revision_items
                        (revision_id, requisito, estado, comentario)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            revision_id,
                            str(grupo.get("requisito", ""))[:500],
                            str(grupo.get("estado", "no_verificable"))[:40],
                            str(grupo.get("evidencia", ""))[:2000],
                        ),
                    )
            conn.commit()
            return revision_id
        finally:
            conn.close()
    except Exception as error:
        print("⚠️ No pude guardar la revisión en Supabase:", error)
        return None


def revisar_archivo(ruta, nombre_archivo, contexto):
    documento = extraer_documento(ruta, nombre_archivo)
    resultado = revisar_con_gemini(contexto, documento, ruta_archivo=ruta)
    return {
        "resultado": resultado,
        "documento": documento,
    }
