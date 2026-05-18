"""
registro_app.py  —  Portal web de registro de afiliados AKP
Sirve index.html y expone dos endpoints:
  GET  /api/categorias   → lista de categorías desde la BD
  POST /api/registrar    → INSERT en miembros (tabla idéntica a BlackBelt)
  GET  /health           → healthcheck para Railway

Variables de entorno requeridas:
  DATABASE_URL_MIEMBROS  → URL de PostgreSQL (misma que usa BlackBelt)

Estructura de archivos en el repo:
  app.py           ← este archivo
  pg_conexion.py   ← copiado del actualizador
  requirements.txt
  Procfile
  static/
    index.html
"""
import hashlib
import logging
import os
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from pg_conexion import conectar_miembros, liberar_miembros

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _hash_cedula(cedula: str) -> str:
    """Contraseña inicial = hash SHA-256 de la cédula (igual que BlackBelt)."""
    return hashlib.sha256(cedula.strip().encode()).hexdigest()


def _parse_date(s: str):
    """Acepta YYYY-MM-DD o DD/MM/YYYY → objeto date."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except Exception:
            continue
    raise ValueError(f"Fecha inválida: {s!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Rutas estáticas
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/categorias
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/categorias")
def get_categorias():
    """Devuelve la lista de categorías activas de la BD."""
    try:
        conn = conectar_miembros()
        cur  = conn.cursor()
        cur.execute("SELECT nombre FROM categorias ORDER BY nombre")
        cats = [r[0] for r in cur.fetchall()]
        liberar_miembros(conn)
        return jsonify(cats)
    except Exception as e:
        log.error(f"[categorias] {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/registrar
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/registrar", methods=["POST"])
def registrar():
    """
    Inserta un nuevo miembro en la tabla miembros.
    Campos requeridos (igual que BlackBelt → ventana_registro):
      nombres, apellidos, cedula, telefono, direccion, correo,
      ciudad_nacimiento, ciudad_residencia,
      fecha_nacimiento (YYYY-MM-DD),
      fecha_ingreso    (YYYY-MM-DD),
      categoria, genero
    La edad NO se ingresa — se calcula desde fecha_nacimiento.
    La contraseña inicial es el hash SHA-256 de la cédula.
    """
    d = request.get_json(force=True) or {}

    CAMPOS_REQ = [
        "nombres", "apellidos", "cedula", "telefono", "direccion", "correo",
        "ciudad_nacimiento", "ciudad_residencia",
        "fecha_nacimiento", "fecha_ingreso",
        "categoria", "genero"
    ]
    vacios = [c for c in CAMPOS_REQ if not str(d.get(c, "")).strip()]
    if vacios:
        return jsonify({"error": f"Campos requeridos faltantes: {', '.join(vacios)}"}), 400

    try:
        fecha_nac = _parse_date(d["fecha_nacimiento"])
        fecha_ing = _parse_date(d["fecha_ingreso"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    conn = None
    try:
        conn = conectar_miembros()
        cur  = conn.cursor()

        pw_hash = _hash_cedula(d["cedula"])

        cur.execute("""
            INSERT INTO miembros (
                nombres, apellidos, cedula,
                telefono, direccion, correo,
                fecha_ingreso, categoria,
                ciudad_nacimiento, fecha_nacimiento,
                genero, password_hash, debe_cambiar_pass,
                ciudad_residencia
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s
            )
        """, (
            d["nombres"].strip(),           d["apellidos"].strip(),
            d["cedula"].strip(),
            d["telefono"].strip(),          d["direccion"].strip(),
            d["correo"].strip(),
            fecha_ing,                      d["categoria"].strip(),
            d["ciudad_nacimiento"].strip(), fecha_nac,
            d["genero"].strip(),            pw_hash,
            True,                           d["ciudad_residencia"].strip(),
        ))
        conn.commit()
        log.info(f"[registrar] Nuevo afiliado: {d['nombres']} {d['apellidos']} — {d['cedula']}")
        return jsonify({"ok": True, "mensaje": "Afiliado registrado correctamente."})

    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
        msg = str(e)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            return jsonify({"error": "Ya existe un afiliado con esa cédula."}), 409
        log.error(f"[registrar] {e}")
        return jsonify({"error": msg}), 500
    finally:
        if conn:
            liberar_miembros(conn)


# ─────────────────────────────────────────────────────────────────────────────
# GET /health
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return "ok", 200


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
