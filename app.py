"""
ADmilkS — Backend Flask + SQLite  v3
Novas features: API Brasília, PDF, cotas mensais, NIS 11d, telefone
"""

import sqlite3, os, io, re, json, base64, zoneinfo, urllib.request
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash, g, Response)

# PDF
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, Image as RLImage,
                                 HRFlowable, KeepTogether)
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from PIL import Image as PILImage

app = Flask(__name__)
app.secret_key = "admilks-secret-key-2024"
DATABASE = os.path.join(os.path.dirname(__file__), "admilks.db")
TZ_BRASILIA = zoneinfo.ZoneInfo("America/Sao_Paulo")

# Cotas mensais (litros)
COTA_INDIVIDUAL = 15
COTA_DUPLA      = 30

# ══════════════════════════════════════════════
# HORÁRIO OFICIAL DE BRASÍLIA
# ══════════════════════════════════════════════

def get_brasilia_time():
    """
    Tenta obter o horário oficial de Brasília via APIs externas.
    Fallback: horário local ajustado para UTC-3 (America/Sao_Paulo).
    Retorna: (timestamp_str, fonte)  fonte = 'api' | 'local'
    """
    apis = [
        ("worldtimeapi", "http://worldtimeapi.org/api/timezone/America/Sao_Paulo"),
        ("timeapi",      "https://timeapi.io/api/Time/current/zone?timeZone=America/Sao_Paulo"),
    ]
    for nome, url in apis:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "ADmilkS/3.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read())
            # worldtimeapi: chave 'datetime'
            if "datetime" in data:
                ts = data["datetime"][:19].replace("T", " ")
                return ts, "api"
            # timeapi.io: chave 'dateTime'
            if "dateTime" in data:
                ts = data["dateTime"][:19].replace("T", " ")
                return ts, "api"
        except Exception:
            continue

    # Fallback: relógio local convertido para America/Sao_Paulo
    ts = datetime.now(TZ_BRASILIA).strftime("%Y-%m-%d %H:%M:%S")
    return ts, "local"


# ══════════════════════════════════════════════
# BANCO DE DADOS
# ══════════════════════════════════════════════

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_database", None)
    if db:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nome       TEXT    NOT NULL,
                email      TEXT    NOT NULL UNIQUE,
                senha      TEXT    NOT NULL,
                nivel      TEXT    NOT NULL DEFAULT 'operador',
                ativo      INTEGER NOT NULL DEFAULT 1,
                created_at TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS beneficiarios (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nome       TEXT    NOT NULL,
                codigo     TEXT    NOT NULL UNIQUE,
                telefone   TEXT,
                cota_dupla INTEGER DEFAULT 0,
                ativo      INTEGER DEFAULT 1,
                created_at TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS retiradas (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                id_beneficiario   INTEGER NOT NULL,
                nome_beneficiario TEXT    NOT NULL,
                data_entrega      TEXT    DEFAULT (datetime('now','localtime')),
                litros            INTEGER NOT NULL,
                cota_dupla        INTEGER DEFAULT 0,
                assinatura_base64 TEXT,
                id_usuario        INTEGER,
                nome_usuario      TEXT,
                fonte_timestamp   TEXT    DEFAULT 'local',
                FOREIGN KEY (id_beneficiario) REFERENCES beneficiarios(id),
                FOREIGN KEY (id_usuario)      REFERENCES usuarios(id)
            );
        """)

        # Migrações automáticas (tolerante a versões antigas)
        for stmt in [
            "ALTER TABLE beneficiarios ADD COLUMN ativo    INTEGER DEFAULT 1",
            "ALTER TABLE beneficiarios ADD COLUMN telefone TEXT",
            "ALTER TABLE retiradas     ADD COLUMN id_usuario      INTEGER",
            "ALTER TABLE retiradas     ADD COLUMN nome_usuario    TEXT",
            "ALTER TABLE retiradas     ADD COLUMN fonte_timestamp TEXT DEFAULT 'local'",
        ]:
            try:
                db.execute(stmt)
            except Exception:
                pass

        db.execute("UPDATE beneficiarios SET ativo=1 WHERE ativo IS NULL")

        # Migração: admins → usuarios
        try:
            old = db.execute("SELECT * FROM admins").fetchall()
            for a in old:
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO usuarios (nome,email,senha,nivel) VALUES (?,?,?,?)",
                        ("Administrador", a["email"], a["senha"], "admin")
                    )
                except Exception:
                    pass
            db.execute("DROP TABLE IF EXISTS admins")
        except Exception:
            pass

        # Admin padrão
        if not db.execute("SELECT id FROM usuarios WHERE email='admin@admilks.com'").fetchone():
            db.execute(
                "INSERT INTO usuarios (nome,email,senha,nivel) VALUES (?,?,?,?)",
                ("Administrador", "admin@admilks.com", "admin123", "admin")
            )
        db.commit()


# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════

def _calcular_alerta(ultima_retirada_str, created_at_str, hoje):
    ref = ultima_retirada_str or created_at_str
    if not ref:
        return "critico"
    try:
        ref_dt = datetime.strptime(ref[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return "ok"
    dias = (hoje - ref_dt).days
    if dias < 7:   return "ok"
    if dias < 14:  return "amarelo"
    if dias < 21:  return "vermelho"
    return "critico"

def _saldo_mes(db, id_beneficiario, cota_dupla):
    """Retorna dict com cota total, retirado no mês e saldo disponível."""
    cota_total = COTA_DUPLA if cota_dupla else COTA_INDIVIDUAL
    row = db.execute(
        """SELECT COALESCE(SUM(litros), 0) AS retirado
           FROM retiradas
           WHERE id_beneficiario = ?
             AND strftime('%Y-%m', data_entrega) = strftime('%Y-%m', 'now', 'localtime')""",
        (id_beneficiario,)
    ).fetchone()
    retirado = int(row["retirado"])
    saldo    = max(0, cota_total - retirado)
    return {"cota_total": cota_total, "retirado": retirado, "saldo": saldo}

def _validar_nis(codigo: str) -> bool:
    """NIS deve ter ao menos 11 dígitos numéricos."""
    apenas_digitos = re.sub(r"\D", "", codigo)
    return len(apenas_digitos) >= 11


# ══════════════════════════════════════════════
# DECORADORES E CONTEXTO
# ══════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def dec(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("login"))
        if session.get("nivel") != "admin":
            return jsonify({"ok": False, "erro": "Acesso restrito a administradores."}), 403
        return f(*args, **kwargs)
    return dec

def admin_page_required(f):
    @wraps(f)
    def dec(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("login"))
        if session.get("nivel") != "admin":
            flash("Acesso restrito a administradores.", "error")
            return redirect(url_for("beneficiados"))
        return f(*args, **kwargs)
    return dec

@app.context_processor
def inject_globals():
    return {
        "is_admin":      session.get("nivel") == "admin",
        "session_nome":  session.get("nome", ""),
        "session_email": session.get("email", ""),
        "session_nivel": session.get("nivel", "operador"),
        "session_id":    session.get("uid"),
        "now_mes":       datetime.now().strftime("%Y-%m"),
    }


# ══════════════════════════════════════════════
# AUTENTICAÇÃO
# ══════════════════════════════════════════════

@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if "uid" in session:
        return redirect(url_for("beneficiados"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "").strip()
        db = get_db()
        u = db.execute(
            "SELECT * FROM usuarios WHERE email=? AND senha=? AND ativo=1",
            (email, senha)
        ).fetchone()
        if u:
            session.update(uid=u["id"], email=u["email"],
                           nome=u["nome"], nivel=u["nivel"])
            return redirect(url_for("beneficiados"))
        error = "E-mail ou senha incorretos."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/esqueceu-senha")
def esqueceu_senha():
    flash("Contate o administrador do sistema para redefinir sua senha.", "info")
    return redirect(url_for("login"))


# ══════════════════════════════════════════════
# BENEFICIADOS
# ══════════════════════════════════════════════

@app.route("/beneficiados")
@login_required
def beneficiados():
    q = request.args.get("q", "").strip()
    db = get_db()
    base_sql = """
        SELECT b.*, MAX(r.data_entrega) AS ultima_retirada, COUNT(r.id) AS total_retiradas
        FROM beneficiarios b
        LEFT JOIN retiradas r ON r.id_beneficiario = b.id
        WHERE b.ativo = 1
    """
    params = (f"%{q}%", f"%{q}%") if q else ()
    cond   = " AND (b.nome LIKE ? OR b.codigo LIKE ?)" if q else ""
    rows   = db.execute(base_sql + cond + " GROUP BY b.id ORDER BY b.nome", params).fetchall()

    hoje = datetime.now()
    beneficiarios = []
    for b in rows:
        d = dict(b)
        d["alerta"]  = _calcular_alerta(d["ultima_retirada"], d["created_at"], hoje)
        d["saldo"]   = _saldo_mes(db, d["id"], d["cota_dupla"])
        beneficiarios.append(d)

    return render_template("beneficiados.html", beneficiarios=beneficiarios, query=q)


@app.route("/api/buscar")
@login_required
def api_buscar():
    q  = request.args.get("q", "").strip()
    db = get_db()
    sql = """
        SELECT b.*, MAX(r.data_entrega) AS ultima_retirada, COUNT(r.id) AS total_retiradas
        FROM beneficiarios b
        LEFT JOIN retiradas r ON r.id_beneficiario = b.id
        WHERE b.ativo = 1
    """
    params = (f"%{q}%", f"%{q}%") if q else ()
    cond   = " AND (b.nome LIKE ? OR b.codigo LIKE ?)" if q else ""
    rows   = db.execute(sql + cond + " GROUP BY b.id ORDER BY b.nome", params).fetchall()

    hoje = datetime.now()
    result = []
    for b in rows:
        d = dict(b)
        d["alerta"] = _calcular_alerta(d["ultima_retirada"], d["created_at"], hoje)
        d["saldo"]  = _saldo_mes(db, d["id"], d["cota_dupla"])
        result.append(d)
    return jsonify(result)


@app.route("/api/saldo/<int:bid>")
@login_required
def api_saldo(bid):
    """Retorna saldo do mês atual para um beneficiário."""
    db    = get_db()
    benef = db.execute("SELECT * FROM beneficiarios WHERE id=? AND ativo=1", (bid,)).fetchone()
    if not benef:
        return jsonify({"ok": False, "erro": "Beneficiário não encontrado."}), 404
    saldo = _saldo_mes(db, bid, benef["cota_dupla"])
    return jsonify({"ok": True, **saldo})


@app.route("/beneficiarios/adicionar", methods=["POST"])
@admin_required
def adicionar_beneficiario():
    nome     = request.form.get("nome",     "").strip()
    cod      = request.form.get("codigo",   "").strip()
    telefone = request.form.get("telefone", "").strip() or None
    dupla    = 1 if request.form.get("cota_dupla") else 0

    if not nome or not cod:
        return jsonify({"ok": False, "erro": "Nome e código são obrigatórios."}), 400
    if not _validar_nis(cod):
        return jsonify({"ok": False,
                        "erro": "Código NIS inválido: deve conter pelo menos 11 dígitos."}), 400

    db = get_db()
    try:
        db.execute(
            "INSERT INTO beneficiarios (nome,codigo,telefone,cota_dupla) VALUES (?,?,?,?)",
            (nome, cod, telefone, dupla)
        )
        db.commit()
        novo = db.execute("SELECT * FROM beneficiarios WHERE codigo=?", (cod,)).fetchone()
        d = dict(novo)
        d["ultima_retirada"] = None
        d["total_retiradas"] = 0
        d["alerta"] = "ok"
        d["saldo"]  = _saldo_mes(db, d["id"], d["cota_dupla"])
        return jsonify({"ok": True, "beneficiario": d})
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "erro": f"Código '{cod}' já está em uso."}), 409


@app.route("/beneficiarios/editar/<int:bid>", methods=["POST"])
@admin_required
def editar_beneficiario(bid):
    nome     = request.form.get("nome",     "").strip()
    cod      = request.form.get("codigo",   "").strip()
    telefone = request.form.get("telefone", "").strip() or None
    dupla    = 1 if request.form.get("cota_dupla") else 0

    if not nome or not cod:
        return jsonify({"ok": False, "erro": "Nome e código são obrigatórios."}), 400
    if not _validar_nis(cod):
        return jsonify({"ok": False,
                        "erro": "Código NIS inválido: deve conter pelo menos 11 dígitos."}), 400

    db = get_db()
    try:
        db.execute(
            "UPDATE beneficiarios SET nome=?,codigo=?,telefone=?,cota_dupla=? WHERE id=?",
            (nome, cod, telefone, dupla, bid)
        )
        db.commit()
        atualizado = db.execute("SELECT * FROM beneficiarios WHERE id=?", (bid,)).fetchone()
        return jsonify({"ok": True, "beneficiario": dict(atualizado)})
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "erro": f"Código '{cod}' já está em uso."}), 409


@app.route("/beneficiarios/excluir/<int:bid>", methods=["POST"])
@admin_required
def excluir_beneficiario(bid):
    get_db().execute("UPDATE beneficiarios SET ativo=0 WHERE id=?", (bid,))
    get_db().commit()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════
# OPERAÇÃO / ENTREGA
# ══════════════════════════════════════════════

@app.route("/api/confirmar-entrega", methods=["POST"])
@login_required
def confirmar_entrega():
    data       = request.get_json()
    id_benef   = data.get("id_beneficiario")
    litros     = int(data.get("litros", 1))
    cota_dup   = 1 if data.get("cota_dupla") else 0
    assinatura = data.get("assinatura_base64", "")

    if not assinatura:
        return jsonify({"ok": False, "erro": "Assinatura é obrigatória."}), 400
    if litros <= 0:
        return jsonify({"ok": False, "erro": "Quantidade de litros inválida."}), 400

    db    = get_db()
    benef = db.execute("SELECT * FROM beneficiarios WHERE id=? AND ativo=1", (id_benef,)).fetchone()
    if not benef:
        return jsonify({"ok": False, "erro": "Beneficiário não encontrado."}), 404

    # Operadores não podem alterar cota_dupla
    if session.get("nivel") != "admin":
        cota_dup = benef["cota_dupla"]

    # Validação de cota mensal
    saldo_info = _saldo_mes(db, id_benef, cota_dup)
    if saldo_info["saldo"] <= 0:
        return jsonify({
            "ok": False,
            "erro": f"Cota mensal esgotada. Este beneficiário já retirou "
                    f"{saldo_info['retirado']}L de {saldo_info['cota_total']}L neste mês."
        }), 400
    if litros > saldo_info["saldo"]:
        return jsonify({
            "ok": False,
            "erro": f"Quantidade excede o saldo disponível. "
                    f"Disponível: {saldo_info['saldo']}L de {saldo_info['cota_total']}L/mês."
        }), 400

    # ── Timestamp oficial de Brasília ──
    timestamp, fonte = get_brasilia_time()

    db.execute(
        """INSERT INTO retiradas
           (id_beneficiario, nome_beneficiario, data_entrega, litros, cota_dupla,
            assinatura_base64, id_usuario, nome_usuario, fonte_timestamp)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (id_benef, benef["nome"], timestamp, litros, cota_dup,
         assinatura, session["uid"], session["nome"], fonte)
    )
    db.commit()

    saldo_novo = _saldo_mes(db, id_benef, cota_dup)
    return jsonify({
        "ok":      True,
        "mensagem": "Entrega registrada com sucesso!",
        "fonte_timestamp": fonte,
        "saldo_restante":  saldo_novo["saldo"],
    })


# ══════════════════════════════════════════════
# HISTÓRICO + ALERTAS
# ══════════════════════════════════════════════

@app.route("/historico")
@login_required
def historico():
    db   = get_db()
    modo = request.args.get("modo", "retiradas")
    mes  = request.args.get("mes", datetime.now().strftime("%Y-%m"))  # filtro PDF
    hoje = datetime.now()

    retiradas = db.execute(
        """SELECT r.*, u.nome AS nome_op
           FROM retiradas r
           LEFT JOIN usuarios u ON u.id = r.id_usuario
           ORDER BY r.data_entrega DESC LIMIT 200"""
    ).fetchall()

    todos = db.execute(
        """SELECT b.*, MAX(r.data_entrega) AS ultima_retirada, COUNT(r.id) AS total_retiradas
           FROM beneficiarios b
           LEFT JOIN retiradas r ON r.id_beneficiario = b.id
           WHERE b.ativo=1
           GROUP BY b.id ORDER BY b.nome"""
    ).fetchall()

    alertas = []
    for b in todos:
        d = dict(b)
        d["alerta"] = _calcular_alerta(d["ultima_retirada"], d["created_at"], hoje)
        ref = d["ultima_retirada"] or d["created_at"]
        try:
            ref_dt = datetime.strptime(ref[:19], "%Y-%m-%d %H:%M:%S")
            d["dias_sem_retirada"] = (hoje - ref_dt).days
        except Exception:
            d["dias_sem_retirada"] = 0
        if d["alerta"] != "ok":
            alertas.append(d)

    alertas.sort(key=lambda x: x["dias_sem_retirada"], reverse=True)

    return render_template("historico.html",
        retiradas=retiradas, alertas=alertas, modo=modo, mes=mes,
        total_ok      =sum(1 for b in todos if _calcular_alerta(b["ultima_retirada"], b["created_at"], hoje) == "ok"),
        total_amarelo =sum(1 for b in todos if _calcular_alerta(b["ultima_retirada"], b["created_at"], hoje) == "amarelo"),
        total_vermelho=sum(1 for b in todos if _calcular_alerta(b["ultima_retirada"], b["created_at"], hoje) in ("vermelho","critico")),
    )


# ══════════════════════════════════════════════
# GERAÇÃO DE PDF
# ══════════════════════════════════════════════

@app.route("/pdf/relatorio")
@admin_page_required
def gerar_pdf():
    """
    Gera relatório PDF das retiradas para prestação de contas ao governo.
    Parâmetros: ?mes=2026-05  (padrão: mês atual)
    """
    mes     = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    db      = get_db()

    retiradas = db.execute(
        """SELECT r.*, b.codigo AS nis, b.telefone
           FROM retiradas r
           JOIN beneficiarios b ON b.id = r.id_beneficiario
           WHERE strftime('%Y-%m', r.data_entrega) = ?
           ORDER BY r.nome_beneficiario, r.data_entrega""",
        (mes,)
    ).fetchall()

    # Parse mês para exibição
    try:
        mes_dt  = datetime.strptime(mes, "%Y-%m")
        mes_str = mes_dt.strftime("%B/%Y").capitalize()
    except Exception:
        mes_str = mes

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=1.5*cm, bottomMargin=2*cm,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        title=f"ADmilkS – Relatório {mes_str}",
    )

    styles = getSampleStyleSheet()
    s_titulo   = ParagraphStyle("titulo",   fontSize=16, fontName="Helvetica-Bold",
                                 alignment=TA_CENTER, spaceAfter=4)
    s_subtit   = ParagraphStyle("subtit",   fontSize=10, fontName="Helvetica",
                                 alignment=TA_CENTER, spaceAfter=2, textColor=colors.HexColor("#555555"))
    s_periodo  = ParagraphStyle("periodo",  fontSize=9,  fontName="Helvetica-Bold",
                                 alignment=TA_CENTER, spaceAfter=12, textColor=colors.HexColor("#29B6D8"))
    s_cell     = ParagraphStyle("cell",     fontSize=8,  fontName="Helvetica",   leading=10)
    s_cell_b   = ParagraphStyle("cellb",    fontSize=8,  fontName="Helvetica-Bold", leading=10)
    s_rodape   = ParagraphStyle("rodape",   fontSize=7,  fontName="Helvetica",
                                 alignment=TA_CENTER, textColor=colors.grey)

    story = []

    # ── Cabeçalho ──
    story.append(Paragraph("ADmilkS", s_titulo))
    story.append(Paragraph("Igreja Evangélica Comunidade Livre em Cristo", s_subtit))
    story.append(Paragraph(f"Relatório de Distribuição de Leite — {mes_str}", s_periodo))
    story.append(HRFlowable(width="100%", thickness=1.5,
                             color=colors.HexColor("#29B6D8"), spaceAfter=10))

    if not retiradas:
        story.append(Paragraph("Nenhuma retirada registrada neste período.", s_cell))
    else:
        # ── Tabela ──
        col_widths = [1*cm, 5.5*cm, 3.2*cm, 1.5*cm, 2.5*cm, 4.3*cm]
        header = [
            Paragraph("Nº",            s_cell_b),
            Paragraph("Nome",          s_cell_b),
            Paragraph("Código NIS",    s_cell_b),
            Paragraph("Litros",        s_cell_b),
            Paragraph("Data",          s_cell_b),
            Paragraph("Assinatura",    s_cell_b),
        ]
        table_data = [header]

        total_litros = 0
        for idx, r in enumerate(retiradas, 1):
            total_litros += r["litros"]
            try:
                dt = datetime.strptime(r["data_entrega"][:19], "%Y-%m-%d %H:%M:%S")
                data_fmt = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                data_fmt = str(r["data_entrega"])[:16]

            # Assinatura: base64 → imagem reportlab
            sig_cell = Paragraph("", s_cell)
            if r["assinatura_base64"]:
                try:
                    b64_data = r["assinatura_base64"]
                    if "," in b64_data:
                        b64_data = b64_data.split(",", 1)[1]
                    img_bytes = base64.b64decode(b64_data)
                    pil_img   = PILImage.open(io.BytesIO(img_bytes)).convert("RGBA")
                    # Fundo branco
                    bg = PILImage.new("RGBA", pil_img.size, (255, 255, 255, 255))
                    bg.paste(pil_img, mask=pil_img.split()[3])
                    final_img = bg.convert("RGB")
                    img_buf   = io.BytesIO()
                    final_img.save(img_buf, format="PNG")
                    img_buf.seek(0)
                    # Calcular altura proporcional
                    w_orig, h_orig = final_img.size
                    w_cell = 3.8 * cm
                    h_cell = (h_orig / w_orig) * w_cell
                    h_cell = min(h_cell, 2.0 * cm)  # limite
                    sig_cell = RLImage(img_buf, width=w_cell, height=h_cell)
                except Exception:
                    sig_cell = Paragraph("Erro ao carregar", s_cell)

            table_data.append([
                Paragraph(str(idx),         s_cell),
                Paragraph(r["nome_beneficiario"], s_cell),
                Paragraph(r["nis"] or "",   s_cell),
                Paragraph(f"{r['litros']}L", s_cell),
                Paragraph(data_fmt,         s_cell),
                sig_cell,
            ])

        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            # Cabeçalho
            ("BACKGROUND",   (0,0), (-1,0),  colors.HexColor("#29B6D8")),
            ("TEXTCOLOR",    (0,0), (-1,0),  colors.white),
            ("FONTNAME",     (0,0), (-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0,0), (-1,0),  8),
            ("ROWBACKGROUND",(0,1), (-1,-1), [colors.white, colors.HexColor("#f0fbfd")]),
            # Linhas
            ("GRID",         (0,0), (-1,-1), 0.4, colors.HexColor("#cccccc")),
            ("LINEBELOW",    (0,0), (-1,0),  1.5, colors.HexColor("#29B6D8")),
            # Alinhamento
            ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN",        (0,0), (0,-1),  "CENTER"),
            ("ALIGN",        (3,0), (3,-1),  "CENTER"),
            ("TOPPADDING",   (0,0), (-1,-1), 6),
            ("BOTTOMPADDING",(0,0), (-1,-1), 6),
            ("LEFTPADDING",  (0,0), (-1,-1), 5),
        ]))

        story.append(tbl)
        story.append(Spacer(1, 0.5*cm))

        # ── Totais ──
        total_data = [[
            Paragraph(f"Total de registros: <b>{len(retiradas)}</b>", s_cell),
            Paragraph(f"Total distribuído: <b>{total_litros} litros</b>", s_cell),
            Paragraph(f"Gerado em: <b>{datetime.now(TZ_BRASILIA).strftime('%d/%m/%Y %H:%M')}</b>", s_cell),
        ]]
        tbl_total = Table(total_data, colWidths=[6*cm, 6*cm, 6*cm])
        tbl_total.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#e0f7fd")),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#aaccdd")),
            ("TOPPADDING", (0,0), (-1,-1), 8),
            ("BOTTOMPADDING",(0,0),(-1,-1),8),
            ("LEFTPADDING",(0,0), (-1,-1), 8),
        ]))
        story.append(tbl_total)

    story.append(Spacer(1, 1.2*cm))

    # ── Assinaturas ──
    ass_data = [[
        Paragraph("_________________________________<br/>Responsável pela distribuição", s_cell),
        Paragraph("_________________________________<br/>Coordenador / Supervisor", s_cell),
        Paragraph("_________________________________<br/>Carimbo e Assinatura", s_cell),
    ]]
    tbl_ass = Table(ass_data, colWidths=[6*cm, 6*cm, 6*cm])
    tbl_ass.setStyle(TableStyle([
        ("ALIGN",  (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1), 8),
        ("BOTTOMPADDING", (0,0),(-1,-1), 8),
    ]))
    story.append(tbl_ass)
    story.append(Spacer(1, 0.6*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"ADmilkS — Documento gerado automaticamente em "
        f"{datetime.now(TZ_BRASILIA).strftime('%d/%m/%Y às %H:%M')} | "
        "Este relatório é válido como comprovante de distribuição.",
        s_rodape
    ))

    doc.build(story)
    buffer.seek(0)

    filename = f"ADmilkS_Relatorio_{mes}.pdf"
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ══════════════════════════════════════════════
# GESTÃO DE USUÁRIOS
# ══════════════════════════════════════════════

@app.route("/usuarios")
@admin_page_required
def usuarios():
    db   = get_db()
    rows = db.execute("SELECT * FROM usuarios ORDER BY nivel, nome").fetchall()
    return render_template("usuarios.html", usuarios=rows)

@app.route("/usuarios/adicionar", methods=["POST"])
@admin_required
def adicionar_usuario():
    nome  = request.form.get("nome",  "").strip()
    email = request.form.get("email", "").strip()
    senha = request.form.get("senha", "").strip()
    nivel = request.form.get("nivel", "operador")
    if nivel not in ("admin", "operador"): nivel = "operador"
    if not nome or not email or not senha:
        return jsonify({"ok": False, "erro": "Nome, e-mail e senha são obrigatórios."}), 400
    db = get_db()
    try:
        db.execute("INSERT INTO usuarios (nome,email,senha,nivel) VALUES (?,?,?,?)",
                   (nome, email, senha, nivel))
        db.commit()
        novo = db.execute("SELECT * FROM usuarios WHERE email=?", (email,)).fetchone()
        return jsonify({"ok": True, "usuario": dict(novo)})
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "erro": f"E-mail '{email}' já cadastrado."}), 409

@app.route("/usuarios/editar/<int:uid>", methods=["POST"])
@admin_required
def editar_usuario(uid):
    nome  = request.form.get("nome",  "").strip()
    email = request.form.get("email", "").strip()
    senha = request.form.get("senha", "").strip()
    nivel = request.form.get("nivel", "operador")
    ativo = 1 if request.form.get("ativo") else 0
    if nivel not in ("admin", "operador"): nivel = "operador"
    if not nome or not email:
        return jsonify({"ok": False, "erro": "Nome e e-mail são obrigatórios."}), 400
    if uid == session["uid"] and nivel != "admin":
        return jsonify({"ok": False, "erro": "Você não pode rebaixar sua própria conta."}), 400
    db = get_db()
    if senha:
        db.execute("UPDATE usuarios SET nome=?,email=?,senha=?,nivel=?,ativo=? WHERE id=?",
                   (nome, email, senha, nivel, ativo, uid))
    else:
        db.execute("UPDATE usuarios SET nome=?,email=?,nivel=?,ativo=? WHERE id=?",
                   (nome, email, nivel, ativo, uid))
    db.commit()
    return jsonify({"ok": True, "usuario": dict(db.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone())})

@app.route("/usuarios/excluir/<int:uid>", methods=["POST"])
@admin_required
def excluir_usuario(uid):
    if uid == session["uid"]:
        return jsonify({"ok": False, "erro": "Você não pode excluir sua própria conta."}), 400
    get_db().execute("UPDATE usuarios SET ativo=0 WHERE id=?", (uid,))
    get_db().commit()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════
# INIT
# ══════════════════════════════════════════════
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
