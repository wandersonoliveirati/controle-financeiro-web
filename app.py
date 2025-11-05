import os
from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import datetime, timedelta
from pathlib import Path
import json, re

app = Flask(__name__)
app.secret_key = "financas-secret"

# --- DB (SQLAlchemy) ---
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, declarative_base, Mapped, mapped_column
from sqlalchemy import String, Integer, Date, Numeric

# Render/Heroku podem expor postgres://
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://")
engine = create_engine(DATABASE_URL or "sqlite:///local.db", future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

class Gasto(Base):
    __tablename__ = "gastos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    categoria: Mapped[str] = mapped_column(String(100), nullable=False)
    descricao: Mapped[str] = mapped_column(String(500), nullable=True)
    valor: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Em aberto")

class Categoria(Base):
    __tablename__ = "categorias"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

Base.metadata.create_all(engine)

def ensure_status_column():
    """Compatível com SQLite e Postgres (sem IF NOT EXISTS)."""
    insp = inspect(engine)
    try:
        cols = [c["name"] for c in insp.get_columns("gastos")]
    except Exception:
        cols = []
    if "status" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE gastos ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'Em aberto'")
            )
ensure_status_column()

# --- JSON legado (import inicial) ---
BASE_DIR = Path(__file__).resolve().parent
LEGACY_JSON = BASE_DIR / "gastos.json"

def parse_brl_to_float(val):
    if val is None: return 0.0
    if isinstance(val, (int, float)): return float(val)
    s = re.sub(r"[^0-9,.\-]", "", str(val).strip())
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0

def normalize_date(s):
    if not s: return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except:
            pass
    return None

def monthly_until_year_end(date_iso):
    if not date_iso: return []
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d")
    except:
        return []
    year, month, day = d.year, d.month, d.day

    def last_day(y, m):
        return (datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)) - timedelta(days=1)

    dates = []
    for m in range(month, 13):
        ld = last_day(year, m).day
        use = min(day, ld)
        dates.append(datetime(year, m, use).date())
    return dates

def import_legacy_if_empty():
    """Importa dados do gastos.json na primeira execução."""
    with SessionLocal() as db:
        if db.query(Gasto).count() > 0:
            return
        if not LEGACY_JSON.exists():
            return
        try:
            raw = json.loads(LEGACY_JSON.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                gastos_raw = raw.get("gastos", [])
                categorias_raw = raw.get("categorias", [])
            else:
                gastos_raw = raw
                categorias_raw = []
            nomes = set([c for c in categorias_raw if c]) | {
                (g.get("categoria") or "").strip() for g in gastos_raw if g.get("categoria")
            }
            for n in sorted({x for x in nomes if x}):
                db.add(Categoria(nome=n))
            for g in gastos_raw:
                data_iso = normalize_date(g.get("data"))
                if not data_iso:
                    continue
                db.add(
                    Gasto(
                        data=datetime.strptime(data_iso, "%Y-%m-%d").date(),
                        categoria=g.get("categoria") or "Sem categoria",
                        descricao=g.get("descricao") or "",
                        valor=parse_brl_to_float(g.get("valor")),
                        status=(g.get("status") or "Em aberto"),
                    )
                )
            db.commit()
        except Exception as e:
            print("Import JSON skipped:", e)
import_legacy_if_empty()

def get_categorias():
    with SessionLocal() as db:
        nomes = [c.nome for c in db.query(Categoria).order_by(Categoria.nome.asc()).all()]
        if not nomes:
            nomes = ["Fixo", "Lazer", "Itens Casa"]
        return nomes

# --------- ROTAS ---------
@app.route("/")
def dashboard():
    with SessionLocal() as db:
        rows = db.query(Gasto).all()
        now = datetime.now()

        # totais principais
        total_mes = 0.0
        total_ano = 0.0
        totais_mes = {}
        totais_categoria = {}

        # breakdown do mês atual por status
        total_mes_pago = 0.0
        total_mes_aberto = 0.0
        qtd_mes_pago = 0
        qtd_mes_aberto = 0

        for r in rows:
            valor = float(r.valor)
            ym = r.data.strftime("%Y-%m")
            totais_mes[ym] = totais_mes.get(ym, 0.0) + valor
            totais_categoria[r.categoria] = totais_categoria.get(r.categoria, 0.0) + valor

            if r.data.year == now.year:
                total_ano += valor
                if r.data.month == now.month:
                    total_mes += valor
                    status = (r.status or "Em aberto").lower()
                    if status == "pago":
                        total_mes_pago += valor
                        qtd_mes_pago += 1
                    else:
                        total_mes_aberto += valor
                        qtd_mes_aberto += 1

        totais_mes = dict(sorted(totais_mes.items()))
        totais_categoria = dict(sorted(totais_categoria.items(), key=lambda kv: kv[1], reverse=True))

    return render_template(
        "dashboard.html",
        total_geral=total_mes,          # total do mês atual (como você já usa)
        total_anual=total_ano,          # total do ano
        totais_mes=totais_mes,
        totais_categoria=totais_categoria,
        # novos campos para o resumo
        total_mes_pago=total_mes_pago,
        total_mes_aberto=total_mes_aberto,
        qtd_mes_pago=qtd_mes_pago,
        qtd_mes_aberto=qtd_mes_aberto,
    )

@app.route("/listar")
def listar_gastos():
    with SessionLocal() as db:
        gastos = db.query(Gasto).order_by(Gasto.data.desc(), Gasto.id.desc()).all()
        view = []
        for g in gastos:
            view.append(
                {
                    "id": g.id,
                    "data": g.data.strftime("%d/%m/%Y"),
                    "data_iso": g.data.strftime("%Y-%m-%d"),
                    "categoria": g.categoria,
                    "descricao": g.descricao,
                    "valor": float(g.valor),
                    "status": g.status or "Em aberto",
                }
            )
    return render_template("listar.html", gastos=view)

@app.route("/adicionar", methods=["GET", "POST"])
def adicionar():
    if request.method == "POST":
        data = request.form.get("data")
        categoria = request.form.get("categoria")
        descricao = request.form.get("descricao") or ""
        valor = parse_brl_to_float(request.form.get("valor"))
        if not data or not categoria:
            flash("Preencha data e categoria.", "warning")
            return redirect(url_for("adicionar"))
        iso = normalize_date(data)
        if not iso:
            flash("Data inválida.", "warning")
            return redirect(url_for("adicionar"))

        with SessionLocal() as db:
            if not db.query(Categoria).filter_by(nome=categoria).first():
                db.add(Categoria(nome=categoria))
                db.commit()
            replicate = (request.form.get("replicar_fim_ano") == "1") or (categoria.strip().lower() == "fixo")
            if replicate:
                for d in monthly_until_year_end(iso):
                    db.add(Gasto(data=d, categoria=categoria, descricao=descricao, valor=valor, status="Em aberto"))
                flash("Gastos fixos adicionados mensalmente até dezembro.", "success")
            else:
                d = datetime.strptime(iso, "%Y-%m-%d").date()
                db.add(Gasto(data=d, categoria=categoria, descricao=descricao, valor=valor, status="Em aberto"))
                flash("Gasto adicionado com sucesso!", "success")
            db.commit()
        return redirect(url_for("listar_gastos"))

    return render_template("index.html", categorias=get_categorias())

@app.route("/editar/<int:gid>", methods=["GET", "POST"])
def editar(gid):
    with SessionLocal() as db:
        gasto = db.get(Gasto, gid)
        if not gasto:
            flash("Item não encontrado.", "danger")
            return redirect(url_for("listar_gastos"))

        if request.method == "POST":
            iso = normalize_date(request.form.get("data"))
            if not iso:
                flash("Data inválida.", "warning")
                return redirect(url_for("editar", gid=gid))
            gasto.data = datetime.strptime(iso, "%Y-%m-%d").date()
            gasto.categoria = request.form.get("categoria")
            gasto.descricao = request.form.get("descricao") or ""
            gasto.valor = parse_brl_to_float(request.form.get("valor"))
            if not db.query(Categoria).filter_by(nome=gasto.categoria).first():
                db.add(Categoria(nome=gasto.categoria))
            db.commit()
            flash("Gasto atualizado.", "success")
            return redirect(url_for("listar_gastos"))

        gview = {
            "id": gasto.id,
            "data": gasto.data.strftime("%d/%m/%Y"),
            "data_iso": gasto.data.strftime("%Y-%m-%d"),
            "categoria": gasto.categoria,
            "descricao": gasto.descricao,
            "valor": float(gasto.valor),
            "status": gasto.status or "Em aberto",
        }
        return render_template("editar.html", indice=gasto.id, gasto=gview, categorias=get_categorias())

@app.route("/excluir/<int:gid>")
def excluir(gid):
    with SessionLocal() as db:
        gasto = db.get(Gasto, gid)
        if not gasto:
            flash("Item não encontrado.", "danger")
            return redirect(url_for("listar_gastos"))
        db.delete(gasto)
        db.commit()
        flash("Gasto excluído.", "info")
    return redirect(url_for("listar_gastos"))

@app.route("/toggle_pago/<int:gid>")
def toggle_pago(gid):
    referer = request.headers.get("Referer")
    with SessionLocal() as db:
        gasto = db.get(Gasto, gid)
        if not gasto:
            flash("Item não encontrado.", "danger")
            return redirect(url_for("listar_gastos"))
        gasto.status = "Pago" if (gasto.status or "Em aberto") != "Pago" else "Em aberto"
        db.commit()
        flash(f'Status alterado para "{gasto.status}".', "success")
    return redirect(referer or url_for("listar_gastos"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
