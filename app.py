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

def parse_fecha(s):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try: return datetime.strptime(s.strip(), fmt).date()
        except: pass
    raise ValueError(f"Fecha inválida: {s}")

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
        release(conn); return jsonify(cats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Clubs GET ──────────────────────────────────────────────────────────────
@app.route("/api/clubs")
def get_clubs():
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""SELECT id,nombre,ciudad,estado,
                              nombres_dueno,apellidos_dueno,cedula_dueno
                       FROM clubs ORDER BY nombre""")
        cols=["id","nombre","ciudad","estado","nombres_dueno","apellidos_dueno","cedula_dueno"]
        clubs=[dict(zip(cols,r)) for r in cur.fetchall()]
        release(conn); return jsonify(clubs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Clubs POST ─────────────────────────────────────────────────────────────
@app.route("/api/clubs", methods=["POST"])
def crear_club():
    d = request.get_json(force=True) or {}
    req=["nombre","nombres_dueno","apellidos_dueno","cedula_dueno"]
    vacios=[c for c in req if not str(d.get(c,"")).strip()]
    if vacios: return jsonify({"error":f"Faltan: {', '.join(vacios)}"}), 400
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""INSERT INTO clubs
            (nombre,ciudad,estado,nombres_dueno,apellidos_dueno,cedula_dueno,password_hash,debe_cambiar_pass)
            VALUES (%s,%s,%s,%s,%s,%s,%s,true)""",
            (d["nombre"].strip(),d.get("ciudad","").strip(),
             d.get("estado","activo"),d["nombres_dueno"].strip(),
             d["apellidos_dueno"].strip(),d["cedula_dueno"].strip(),
             hash_val(d["cedula_dueno"])))
        conn.commit()
        cur.execute("""SELECT id,nombre,ciudad,estado,nombres_dueno,apellidos_dueno,cedula_dueno
                       FROM clubs ORDER BY nombre""")
        cols=["id","nombre","ciudad","estado","nombres_dueno","apellidos_dueno","cedula_dueno"]
        clubs=[dict(zip(cols,r)) for r in cur.fetchall()]
        release(conn)
        return jsonify({"ok":True,"mensaje":"Club creado correctamente.","clubs":clubs})
    except Exception as e:
        try: conn.rollback(); release(conn)
        except: pass
        if "unique" in str(e).lower():
            return jsonify({"error":"Ya existe un club con esa cédula."}), 409
        return jsonify({"error":str(e)}), 500

# ── Afiliados del club ─────────────────────────────────────────────────────
@app.route("/api/clubs/<int:club_id>/afiliados")
def afiliados_club(club_id):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""SELECT id,nombres,apellidos,cedula,categoria,
                              ciudad_residencia,telefono,correo,
                              TO_CHAR(fecha_nacimiento,'DD/MM/YYYY'),
                              TO_CHAR(fecha_ingreso,'DD/MM/YYYY'),
                              DATE_PART('year',AGE(fecha_nacimiento))::int,
                              ciudad_nacimiento, genero
                       FROM miembros WHERE club_id=%s
                       ORDER BY apellidos,nombres""", (club_id,))
        cols=["id","nombres","apellidos","cedula","categoria","ciudad_residencia",
              "telefono","correo","fecha_nacimiento","fecha_ingreso","edad",
              "ciudad_nacimiento","genero"]
        rows=[dict(zip(cols,r)) for r in cur.fetchall()]
        release(conn); return jsonify(rows)
    except Exception as e:
        return jsonify({"error":str(e)}), 500

# ── Registrar afiliado ─────────────────────────────────────────────────────
@app.route("/api/registrar", methods=["POST"])
def registrar():
    d = request.get_json(force=True) or {}
    req=["nombres","apellidos","cedula","telefono","direccion","correo",
         "ciudad_nacimiento","ciudad_residencia","fecha_nacimiento","fecha_ingreso",
         "categoria","genero"]
    vacios=[c for c in req if not str(d.get(c,"")).strip()]
    if vacios: return jsonify({"error":f"Faltan: {', '.join(vacios)}"}), 400
    try:
        conn = get_conn(); cur = conn.cursor()
        club_id=int(d["club_id"]) if d.get("club_id") else None
        cur.execute("""INSERT INTO miembros
            (nombres,apellidos,cedula,telefono,direccion,correo,
             fecha_ingreso,categoria,ciudad_nacimiento,fecha_nacimiento,
             genero,password_hash,debe_cambiar_pass,ciudad_residencia,club_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s)""",
            (d["nombres"].strip(),d["apellidos"].strip(),d["cedula"].strip(),
             d["telefono"].strip(),d["direccion"].strip(),d["correo"].strip(),
             parse_fecha(d["fecha_ingreso"]),d["categoria"].strip(),
             d["ciudad_nacimiento"].strip(),parse_fecha(d["fecha_nacimiento"]),
             d["genero"].strip(),hash_val(d["cedula"]),
             d["ciudad_residencia"].strip(),club_id))
        conn.commit(); release(conn)
        return jsonify({"ok":True,"mensaje":"Afiliado registrado correctamente."})
    except Exception as e:
        try: conn.rollback(); release(conn)
        except: pass
        if "unique" in str(e).lower():
            return jsonify({"error":"Ya existe un afiliado con esa cédula."}), 409
        return jsonify({"error":str(e)}), 500

# ── Editar afiliado ────────────────────────────────────────────────────────
@app.route("/api/afiliados/<int:socio_id>", methods=["PUT"])
def editar_afiliado(socio_id):
    d = request.get_json(force=True) or {}
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""UPDATE miembros SET
            nombres=%s,apellidos=%s,cedula=%s,telefono=%s,direccion=%s,correo=%s,
            fecha_ingreso=%s,categoria=%s,ciudad_nacimiento=%s,fecha_nacimiento=%s,
            genero=%s,ciudad_residencia=%s
            WHERE id=%s""",
            (d["nombres"].strip(),d["apellidos"].strip(),d["cedula"].strip(),
             d["telefono"].strip(),d["direccion"].strip(),d["correo"].strip(),
             parse_fecha(d["fecha_ingreso"]),d["categoria"].strip(),
             d["ciudad_nacimiento"].strip(),parse_fecha(d["fecha_nacimiento"]),
             d["genero"].strip(),d["ciudad_residencia"].strip(),socio_id))
        conn.commit(); release(conn)
        return jsonify({"ok":True,"mensaje":"Afiliado actualizado."})
    except Exception as e:
        try: conn.rollback(); release(conn)
        except: pass
        return jsonify({"error":str(e)}), 500

# ── Plantillas ─────────────────────────────────────────────────────────────
@app.route("/api/plantillas")
def get_plantillas():
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT id,nombre FROM hist_plantillas WHERE activa=true ORDER BY nombre")
        plts=[{"id":r[0],"nombre":r[1]} for r in cur.fetchall()]
        release(conn); return jsonify(plts)
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/plantillas/<int:plt_id>/campos")
def get_campos(plt_id):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT id,etiqueta,tipo FROM hist_campos WHERE plantilla_id=%s ORDER BY orden",
                    (plt_id,))
        campos=[{"id":r[0],"etiqueta":r[1],"tipo":r[2]} for r in cur.fetchall()]
        release(conn); return jsonify(campos)
    except Exception as e:
        return jsonify({"error":str(e)}), 500

# ── Historial ──────────────────────────────────────────────────────────────
@app.route("/api/afiliados/<int:socio_id>/historial")
def get_historial(socio_id):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""SELECT hr.id, hp.nombre AS plantilla
                       FROM hist_registros hr
                       JOIN hist_plantillas hp ON hp.id=hr.plantilla_id
                       WHERE hr.socio_id=%s ORDER BY hr.id""", (socio_id,))
        registros=[]
        for rid, plt_nombre in cur.fetchall():
            cur.execute("""SELECT hc.etiqueta, hv.valor, hc.tipo
                           FROM hist_valores hv
                           JOIN hist_campos hc ON hc.id=hv.campo_id
                           WHERE hv.registro_id=%s ORDER BY hc.orden""", (rid,))
            campos=[{"etiqueta":r[0],"valor":r[1],"tipo":r[2]} for r in cur.fetchall()]
            registros.append({"id":rid,"plantilla":plt_nombre,"campos":campos})
        release(conn); return jsonify(registros)
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/afiliados/<int:socio_id>/historial", methods=["POST"])
def add_historial(socio_id):
    d = request.get_json(force=True) or {}
    plt_id  = d.get("plantilla_id")
    valores = d.get("valores", {})
    if not plt_id: return jsonify({"error":"Falta plantilla_id"}), 400
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT INTO hist_registros (socio_id,plantilla_id) VALUES (%s,%s) RETURNING id",
                    (socio_id, plt_id))
        reg_id = cur.fetchone()[0]
        for campo_id, valor in valores.items():
            cur.execute("INSERT INTO hist_valores (registro_id,campo_id,valor) VALUES (%s,%s,%s)",
                        (reg_id, int(campo_id), str(valor)))
        conn.commit(); release(conn)
        return jsonify({"ok":True,"mensaje":"Registro guardado."})
    except Exception as e:
        try: conn.rollback(); release(conn)
        except: pass
        return jsonify({"error":str(e)}), 500

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))
