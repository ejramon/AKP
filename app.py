import os, hashlib
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
import psycopg2
from psycopg2 import pool as pg_pool

app = Flask(__name__, static_folder="static", static_url_path="")
DATABASE_URL = os.environ.get("DATABASE_URL_MIEMBROS", os.environ.get("DATABASE_URL", ""))
_pool = None

def get_conn():
    global _pool
    if _pool is None:
        _pool = pg_pool.SimpleConnectionPool(1, 5, DATABASE_URL)
    return _pool.getconn()

def release(conn):
    global _pool
    if _pool: _pool.putconn(conn)

def hash_val(s):
    return hashlib.sha256(s.strip().encode()).hexdigest()

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

# ── Categorías ─────────────────────────────────────────────────────────────
@app.route("/api/categorias")
def categorias():
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT nombre FROM categorias ORDER BY nombre")
        cats = [r[0] for r in cur.fetchall()]
        release(conn)
        return jsonify(cats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Clubs GET ──────────────────────────────────────────────────────────────
@app.route("/api/clubs")
def get_clubs():
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""SELECT id, nombre, ciudad, estado,
                              nombres_dueno, apellidos_dueno, cedula_dueno
                       FROM clubs ORDER BY nombre""")
        cols = ["id","nombre","ciudad","estado","nombres_dueno","apellidos_dueno","cedula_dueno"]
        clubs = [dict(zip(cols, r)) for r in cur.fetchall()]
        release(conn)
        return jsonify(clubs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Clubs POST ─────────────────────────────────────────────────────────────
@app.route("/api/clubs", methods=["POST"])
def crear_club():
    d = request.get_json(force=True) or {}
    req = ["nombre","nombres_dueno","apellidos_dueno","cedula_dueno"]
    vacios = [c for c in req if not str(d.get(c,"")).strip()]
    if vacios:
        return jsonify({"error": f"Faltan campos: {', '.join(vacios)}"}), 400
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""INSERT INTO clubs
            (nombre, ciudad, estado, nombres_dueno, apellidos_dueno,
             cedula_dueno, password_hash, debe_cambiar_pass)
            VALUES (%s,%s,%s,%s,%s,%s,%s,true)""",
            (d["nombre"].strip(), d.get("ciudad","").strip(),
             d.get("estado","activo").strip(),
             d["nombres_dueno"].strip(), d["apellidos_dueno"].strip(),
             d["cedula_dueno"].strip(), hash_val(d["cedula_dueno"])))
        conn.commit()
        # Devolver lista actualizada
        cur.execute("""SELECT id,nombre,ciudad,estado,nombres_dueno,apellidos_dueno,cedula_dueno
                       FROM clubs ORDER BY nombre""")
        cols = ["id","nombre","ciudad","estado","nombres_dueno","apellidos_dueno","cedula_dueno"]
        clubs = [dict(zip(cols, r)) for r in cur.fetchall()]
        release(conn)
        return jsonify({"ok": True, "mensaje": "Club creado correctamente.", "clubs": clubs})
    except Exception as e:
        try: conn.rollback(); release(conn)
        except: pass
        if "unique" in str(e).lower():
            return jsonify({"error": "Ya existe un club con esa cédula."}), 409
        return jsonify({"error": str(e)}), 500

# ── Registrar Afiliado ─────────────────────────────────────────────────────
@app.route("/api/registrar", methods=["POST"])
def registrar():
    d = request.get_json(force=True) or {}
    req = ["nombres","apellidos","cedula","telefono","direccion","correo",
           "ciudad_nacimiento","ciudad_residencia","fecha_nacimiento","fecha_ingreso",
           "categoria","genero"]
    vacios = [c for c in req if not str(d.get(c,"")).strip()]
    if vacios:
        return jsonify({"error": f"Faltan campos: {', '.join(vacios)}"}), 400
    def parse(s):
        for fmt in ("%Y-%m-%d","%d/%m/%Y"):
            try: return datetime.strptime(s.strip(), fmt).date()
            except: pass
        raise ValueError(f"Fecha inválida: {s}")
    try:
        conn = get_conn(); cur = conn.cursor()
        club_id = int(d["club_id"]) if d.get("club_id") else None
        cur.execute("""INSERT INTO miembros
            (nombres,apellidos,cedula,telefono,direccion,correo,
             fecha_ingreso,categoria,ciudad_nacimiento,fecha_nacimiento,
             genero,password_hash,debe_cambiar_pass,ciudad_residencia,club_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s)""",
            (d["nombres"].strip(),d["apellidos"].strip(),d["cedula"].strip(),
             d["telefono"].strip(),d["direccion"].strip(),d["correo"].strip(),
             parse(d["fecha_ingreso"]),d["categoria"].strip(),
             d["ciudad_nacimiento"].strip(),parse(d["fecha_nacimiento"]),
             d["genero"].strip(),hash_val(d["cedula"]),
             d["ciudad_residencia"].strip(),club_id))
        conn.commit(); release(conn)
        return jsonify({"ok": True, "mensaje": "Afiliado registrado correctamente."})
    except Exception as e:
        try: conn.rollback(); release(conn)
        except: pass
        if "unique" in str(e).lower():
            return jsonify({"error": "Ya existe un afiliado con esa cédula."}), 409
        return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))
