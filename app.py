import os, hashlib, logging, secrets
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("akp")

app = Flask(__name__, static_folder="static", static_url_path="")
DATABASE_URL = os.environ.get("DATABASE_URL_MIEMBROS", os.environ.get("DATABASE_URL", ""))
# Tamaño del pool configurable por variable de entorno.
# Plan básico Railway (~20-25 conexiones totales): dejar en 10 da margen.
# Cuando escales a 400 clubes + plan mayor de Railway: subir POOL_MAX a 20.
POOL_MAX = int(os.environ.get("POOL_MAX", 10))
_pool = None

def _crear_pool():
    # keepalives: si Railway reinicia la DB, las conexiones muertas se detectan
    # y se descartan en vez de quedar colgadas.
    return pg_pool.SimpleConnectionPool(
        1, POOL_MAX, DATABASE_URL,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )

def get_conn():
    global _pool
    try:
        if _pool is None:
            _pool = _crear_pool()
        return _pool.getconn()
    except Exception as e:
        # Si el pool quedó inservible (DB reiniciada), se descarta para que
        # el próximo request lo recree desde cero.
        logger.error("Pool inservible, se recreará: %s", e)
        _pool = None
        raise

def release(conn):
    global _pool
    if _pool and conn:
        try:
            _pool.putconn(conn)
        except Exception as e:
            logger.error("Error al liberar conexión: %s", e)

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
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT nombre FROM categorias ORDER BY nombre")
        cats = [r[0] for r in cur.fetchall() if "admin" not in r[0].lower()]
        return jsonify(cats)
    except Exception as e:
        logger.error("categorias: %s", e)
        return jsonify({"error": str(e)}), 500
    finally:
        release(conn)

# ── Clubs GET ──────────────────────────────────────────────────────────────
@app.route("/api/clubs")
def get_clubs():
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""SELECT id,nombre,ciudad,estado,
                              nombres_dueno,apellidos_dueno,cedula_dueno
                       FROM clubs ORDER BY nombre""")
        cols=["id","nombre","ciudad","estado","nombres_dueno","apellidos_dueno","cedula_dueno"]
        clubs=[dict(zip(cols,r)) for r in cur.fetchall()]
        return jsonify(clubs)
    except Exception as e:
        logger.error("get_clubs: %s", e)
        return jsonify({"error": str(e)}), 500
    finally:
        release(conn)

# ── Clubs POST ─────────────────────────────────────────────────────────────
@app.route("/api/clubs", methods=["POST"])
def crear_club():
    d = request.get_json(force=True) or {}
    req=["nombre","nombres_dueno","apellidos_dueno","cedula_dueno"]
    vacios=[c for c in req if not str(d.get(c,"")).strip()]
    if vacios: return jsonify({"error":f"Faltan: {', '.join(vacios)}"}), 400
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        token = secrets.token_urlsafe(8)   # genera token único ej: "a3f9k2Xw"
        cur.execute("""INSERT INTO clubs
            (nombre,ciudad,estado,nombres_dueno,apellidos_dueno,cedula_dueno,password_hash,debe_cambiar_pass,token)
            VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s)""",
            (d["nombre"].strip(),d.get("ciudad","").strip(),
             d.get("estado","activo"),d["nombres_dueno"].strip(),
             d["apellidos_dueno"].strip(),d["cedula_dueno"].strip(),
             hash_val(d["cedula_dueno"]),token))
        conn.commit()
        cur.execute("""SELECT id,nombre,ciudad,estado,nombres_dueno,apellidos_dueno,cedula_dueno,token
                       FROM clubs ORDER BY nombre""")
        cols=["id","nombre","ciudad","estado","nombres_dueno","apellidos_dueno","cedula_dueno","token"]
        clubs=[dict(zip(cols,r)) for r in cur.fetchall()]
        return jsonify({"ok":True,"mensaje":"Club creado correctamente.","clubs":clubs,"token":token})
    except Exception as e:
        try: conn.rollback()
        except: pass
        logger.error("crear_club: %s", e)
        if "unique" in str(e).lower():
            return jsonify({"error":"Ya existe un club con esa cédula."}), 409
        return jsonify({"error":str(e)}), 500
    finally:
        release(conn)

# ── Club por token (acceso individual sin login) ───────────────────────────
@app.route("/api/mi-club/<token>")
def mi_club(token):
    """Devuelve los datos del club que corresponde al token."""
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""SELECT id,nombre,ciudad,estado,
                              nombres_dueno,apellidos_dueno,cedula_dueno
                       FROM clubs WHERE token=%s""", (token,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Token inválido"}), 404
        cols = ["id","nombre","ciudad","estado","nombres_dueno","apellidos_dueno","cedula_dueno"]
        return jsonify(dict(zip(cols, row)))
    except Exception as e:
        logger.error("mi_club: %s", e)
        return jsonify({"error": str(e)}), 500
    finally:
        release(conn)


@app.route("/api/clubs/<int:club_id>/afiliados")
def afiliados_club(club_id):
    conn = None
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
        return jsonify(rows)
    except Exception as e:
        logger.error("afiliados_club: %s", e)
        return jsonify({"error":str(e)}), 500
    finally:
        release(conn)

# ── Registrar afiliado ─────────────────────────────────────────────────────
@app.route("/api/registrar", methods=["POST"])
def registrar():
    d = request.get_json(force=True) or {}
    req=["nombres","apellidos","cedula","telefono","direccion","correo",
         "ciudad_nacimiento","ciudad_residencia","fecha_nacimiento","fecha_ingreso",
         "categoria","genero"]
    vacios=[c for c in req if not str(d.get(c,"")).strip()]
    if vacios: return jsonify({"error":f"Faltan: {', '.join(vacios)}"}), 400
    conn = None
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
        conn.commit()
        return jsonify({"ok":True,"mensaje":"Afiliado registrado correctamente."})
    except Exception as e:
        try: conn.rollback()
        except: pass
        logger.error("registrar: %s", e)
        if "unique" in str(e).lower():
            return jsonify({"error":"Ya existe un afiliado con esa cédula."}), 409
        return jsonify({"error":str(e)}), 500
    finally:
        release(conn)

# ── Editar afiliado ────────────────────────────────────────────────────────
@app.route("/api/afiliados/<int:socio_id>", methods=["PUT"])
def editar_afiliado(socio_id):
    d = request.get_json(force=True) or {}
    conn = None
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
        conn.commit()
        return jsonify({"ok":True,"mensaje":"Afiliado actualizado."})
    except Exception as e:
        try: conn.rollback()
        except: pass
        logger.error("editar_afiliado: %s", e)
        return jsonify({"error":str(e)}), 500
    finally:
        release(conn)

# ── Plantillas ─────────────────────────────────────────────────────────────
@app.route("/api/plantillas")
def get_plantillas():
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT id,nombre FROM hist_plantillas WHERE activa=true ORDER BY nombre")
        plts=[{"id":r[0],"nombre":r[1]} for r in cur.fetchall()]
        return jsonify(plts)
    except Exception as e:
        logger.error("get_plantillas: %s", e)
        return jsonify({"error":str(e)}), 500
    finally:
        release(conn)

@app.route("/api/plantillas/<int:plt_id>/campos")
def get_campos(plt_id):
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT id,etiqueta,tipo FROM hist_campos WHERE plantilla_id=%s ORDER BY orden",
                    (plt_id,))
        campos=[{"id":r[0],"etiqueta":r[1],"tipo":r[2]} for r in cur.fetchall()]
        return jsonify(campos)
    except Exception as e:
        logger.error("get_campos: %s", e)
        return jsonify({"error":str(e)}), 500
    finally:
        release(conn)

# ── Historial ──────────────────────────────────────────────────────────────
@app.route("/api/afiliados/<int:socio_id>/historial")
def get_historial(socio_id):
    conn = None
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
        return jsonify(registros)
    except Exception as e:
        logger.error("get_historial: %s", e)
        return jsonify({"error":str(e)}), 500
    finally:
        release(conn)

@app.route("/api/afiliados/<int:socio_id>/historial", methods=["POST"])
def add_historial(socio_id):
    d = request.get_json(force=True) or {}
    plt_id  = d.get("plantilla_id")
    valores = d.get("valores", {})
    if not plt_id: return jsonify({"error":"Falta plantilla_id"}), 400
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT INTO hist_registros (socio_id,plantilla_id) VALUES (%s,%s) RETURNING id",
                    (socio_id, plt_id))
        reg_id = cur.fetchone()[0]
        for campo_id, valor in valores.items():
            cur.execute("INSERT INTO hist_valores (registro_id,campo_id,valor) VALUES (%s,%s,%s)",
                        (reg_id, int(campo_id), str(valor)))
        conn.commit()
        return jsonify({"ok":True,"mensaje":"Registro guardado."})
    except Exception as e:
        try: conn.rollback()
        except: pass
        logger.error("add_historial: %s", e)
        return jsonify({"error":str(e)}), 500
    finally:
        release(conn)

# ── Asistencia ─────────────────────────────────────────────────────────────
@app.route("/api/clubs/<int:club_id>/asistencia")
def get_asistencia(club_id):
    """Devuelve todos los registros de asistencia del club. Opcional: ?fecha=YYYY-MM-DD"""
    fecha = request.args.get("fecha")
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        if fecha:
            cur.execute("""SELECT a.socio_id, a.estado
                           FROM asistencia a
                           WHERE a.club_id=%s AND a.fecha=%s""",
                        (club_id, fecha))
            rows = {str(r[0]): r[1] for r in cur.fetchall()}
        else:
            cur.execute("""SELECT TO_CHAR(a.fecha,'YYYY-MM-DD'), a.socio_id, a.estado
                           FROM asistencia a
                           WHERE a.club_id=%s ORDER BY a.fecha""", (club_id,))
            rows = {}
            for fecha_s, sid, estado in cur.fetchall():
                rows.setdefault(fecha_s, {})[str(sid)] = estado
        return jsonify(rows)
    except Exception as e:
        logger.error("get_asistencia: %s", e)
        return jsonify({"error": str(e)}), 500
    finally:
        release(conn)

@app.route("/api/clubs/<int:club_id>/asistencia", methods=["POST"])
def guardar_asistencia(club_id):
    """Guarda/actualiza asistencia para una fecha. Body: {fecha, registros: [{socio_id, estado}]}"""
    d = request.get_json(force=True) or {}
    fecha = d.get("fecha")
    registros = d.get("registros", [])
    if not fecha:
        return jsonify({"error": "Falta fecha"}), 400
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        # ── INSERT masivo: una sola query para TODOS los registros del club ──
        # Antes era un INSERT por deportista (bucle). Con clubes grandes en hora
        # pico eso ocupaba la conexión cientos de ms. Ahora es un solo round-trip.
        filas = [
            (club_id, int(reg["socio_id"]), fecha, reg.get("estado", "sin_registro"))
            for reg in registros
        ]
        if filas:
            execute_values(cur,
                """INSERT INTO asistencia (club_id, socio_id, fecha, estado)
                   VALUES %s
                   ON CONFLICT (club_id, socio_id, fecha)
                   DO UPDATE SET estado=EXCLUDED.estado""",
                filas)
        conn.commit()
        return jsonify({"ok": True, "mensaje": f"Asistencia guardada para {fecha}."})
    except Exception as e:
        try: conn.rollback()
        except: pass
        logger.error("guardar_asistencia: %s", e)
        return jsonify({"error": str(e)}), 500
    finally:
        release(conn)

@app.route("/api/clubs/<int:club_id>/asistencia/resumen")
def resumen_asistencia(club_id):
    """Devuelve {fecha: {total, presentes}} para pintar el calendario."""
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""SELECT TO_CHAR(fecha,'YYYY-MM-DD'),
                              COUNT(*) FILTER (WHERE estado='presente') AS p,
                              COUNT(*) FILTER (WHERE estado='ausente')  AS a,
                              COUNT(*) AS t
                       FROM asistencia WHERE club_id=%s
                         AND estado != 'sin_registro'
                       GROUP BY fecha""", (club_id,))
        rows = {r[0]: {"presentes": r[1], "ausentes": r[2], "total": r[3]}
                for r in cur.fetchall()}
        return jsonify(rows)
    except Exception as e:
        logger.error("resumen_asistencia: %s", e)
        return jsonify({"error": str(e)}), 500
    finally:
        release(conn)

@app.route("/health")
def health():
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        return jsonify({"status": "ok", "db": "ok"}), 200
    except Exception as e:
        logger.error("health: DB no responde: %s", e)
        return jsonify({"status": "ok", "db": "error", "detail": str(e)}), 503
    finally:
        release(conn)

def init_db():
    """Migraciones automáticas al arrancar. Se ejecuta una sola vez por deploy."""
    conn = None
    try:
        conn = get_conn(); cur = conn.cursor()

        # ── Tabla asistencia ────────────────────────────────────────────────
        cur.execute("""CREATE TABLE IF NOT EXISTS asistencia (
            id       SERIAL PRIMARY KEY,
            club_id  INTEGER NOT NULL,
            socio_id INTEGER NOT NULL,
            fecha    DATE    NOT NULL,
            estado   VARCHAR(20) NOT NULL DEFAULT 'sin_registro',
            UNIQUE(club_id, socio_id, fecha)
        )""")

        # ── Columna token en clubs ──────────────────────────────────────────
        # Agrega la columna si no existe (clubs nuevos la reciben al crearse).
        cur.execute("ALTER TABLE clubs ADD COLUMN IF NOT EXISTS token VARCHAR(20)")
        # Genera tokens para clubs que ya existían antes de este deploy.
        cur.execute("""UPDATE clubs SET token = SUBSTR(MD5(cedula_dueno || id::text), 1, 11)
                       WHERE token IS NULL""")
        # Agrega constraint único si todavía no existe.
        cur.execute("""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'clubs_token_unique')
            THEN ALTER TABLE clubs ADD CONSTRAINT clubs_token_unique UNIQUE (token);
            END IF; END $$""")

        conn.commit()
        logger.info("init_db: migraciones aplicadas correctamente.")
    except Exception as e:
        try: conn.rollback()
        except: pass
        logger.error("init_db falló: %s", e)
    finally:
        release(conn)

# Se ejecuta al importar el módulo (gunicorn) y al correr directo.
try:
    init_db()
except Exception as e:
    logger.error("No se pudo inicializar la DB al arrancar: %s", e)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))
