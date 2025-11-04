
from flask import Flask, render_template, request, redirect, url_for, flash
import json
from pathlib import Path
from datetime import datetime
import re

app = Flask(__name__)
app.secret_key = "financas-secret"  # ajuste conforme necessário

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "gastos.json"


# -------------------- Utilidades --------------------
def normalize_date(s):
    """Aceita 'YYYY-MM-DD' ou 'DD/MM/YYYY' e retorna 'YYYY-MM-DD'. Retorna None se inválida."""
    if not s:
        return None
    s = str(s).strip()
    from datetime import datetime
    try:
        if '-' in s and len(s) >= 10:
            return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        pass
    try:
        if '/' in s and len(s) >= 10:
            return datetime.strptime(s[:10], "%d/%m/%Y").strftime("%Y-%m-%d")
    except Exception:
        pass
    return None

def parse_brl_to_float(val):
    """
    Converte strings como "R$ 1.234,56" ou "1234,56" para float 1234.56.
    Aceita números com vírgula ou ponto como separador decimal.
    """
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    # remove R$, espaços e tudo que não é número, vírgula ou ponto e sinal
    s = re.sub(r"[^0-9,.\-]", "", s)
    # se houver mais de um separador, mantém o último como decimal
    if "," in s and "." in s:
        # assume padrão brasileiro: pontos de milhar e vírgula decimal
        s = s.replace(".", "").replace(",", ".")
    else:
        # troca vírgula por ponto
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0



def load_store():
    """Carrega o arquivo inteiro, podendo ser lista simples ou dict{'gastos': [...], 'categorias': [...]}"""
    if not DB_PATH.exists():
        return {"gastos": [], "categorias": []}
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return {"gastos": data, "categorias": []}
            if isinstance(data, dict):
                data.setdefault("gastos", [])
                data.setdefault("categorias", [])
                return data
            return {"gastos": [], "categorias": []}
    except Exception:
        return {"gastos": [], "categorias": []}


def load_gastos():
    store = load_store()
    gastos = []
    for g in store.get("gastos", []):
        data_raw = g.get("data")
        data_iso = normalize_date(data_raw)
        gastos.append({
            "data": data_raw,
            "data_iso": data_iso,
            "categoria": g.get("categoria"),
            "descricao": g.get("descricao"),
            "valor": parse_brl_to_float(g.get("valor")),
        })
    return gastos


def save_gastos(gastos):
    """Mantém a estrutura original do JSON se ele tiver 'categorias'."""
    store = load_store()
    store["gastos"] = gastos
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

def get_categorias():
    store = load_store()
    cats = store.get("categorias") or []
    # Fallback: infere das despesas
    if not cats:
        cats = sorted(list({(g.get("categoria") or "").strip() for g in store.get("gastos", []) if g.get("categoria")}))
    return cats

    """Mantém a estrutura original do JSON se ele tiver 'categorias'."""
    store = load_store()
    store["gastos"] = gastos
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

def agregacoes(gastos):
    """Retorna total do mês atual, total anual, totais mensais e por categoria"""
    totais_mes = {}
    totais_categoria = {}
    total_mes_atual = 0.0
    total_anual = 0.0

    ano_atual = datetime.now().year
    mes_atual = datetime.now().month

    for g in gastos:
        valor = parse_brl_to_float(g.get("valor"))
        data = g.get("data_iso") or normalize_date(g.get("data"))
        if not data:
            continue
        try:
            d = datetime.strptime(data, "%Y-%m-%d")
        except Exception:
            continue

        ym = d.strftime("%Y-%m")
        totais_mes[ym] = totais_mes.get(ym, 0.0) + valor

        cat = g.get("categoria") or "Sem categoria"
        totais_categoria[cat] = totais_categoria.get(cat, 0.0) + valor

        if d.year == ano_atual:
            total_anual += valor
            if d.month == mes_atual:
                total_mes_atual += valor

    totais_mes = dict(sorted(totais_mes.items(), key=lambda kv: kv[0]))
    totais_categoria = dict(sorted(totais_categoria.items(), key=lambda kv: kv[1], reverse=True))

    return total_mes_atual, total_anual, totais_mes, totais_categoria


# -------------------- Rotas --------------------
@app.route("/")
def dashboard():
    gastos = load_gastos()
    total_geral, total_anual, totais_mes, totais_categoria = agregacoes(gastos)
    return render_template(
        "dashboard.html",
        total_geral=total_geral,
        total_anual=total_anual,
        totais_mes=totais_mes,
        totais_categoria=totais_categoria
    )



@app.route("/listar")
def listar_gastos():
    base = load_gastos()
    # anexa índice original para permitir editar/excluir corretos mesmo após ordenação/filtros
    gastos = []
    for i, g in enumerate(base):
        item = dict(g)
        item["idx"] = i
        gastos.append(item)
    # Ordena por data desc (sem perder o idx)
    try:
        gastos.sort(key=lambda g: g.get("data_iso") or g.get("data") or "", reverse=True)
    except Exception:
        pass
    return render_template("listar.html", gastos=gastos)



@app.route("/adicionar", methods=["GET", "POST"])
def adicionar():
    if request.method == "POST":
        data = request.form.get("data")
        categoria = request.form.get("categoria");
        if categoria == "_outra":
            categoria = request.form.get('cat-outra') or request.form.get('categoria');
        if categoria == "_outra":
            categoria = request.form.get("categoria", type=str) or request.form.get("categoria" )
            # pega do input de texto 'cat-outra' se presente
            categoria = request.form.get('categoria') or request.form.get('cat-outra') or categoria
        descricao = request.form.get("descricao")
        valor_raw = request.form.get("valor")

        if not data or not categoria or not valor_raw:
            flash("Preencha data, categoria e valor.", "warning")
            return redirect(url_for("adicionar"))

        valor = parse_brl_to_float(valor_raw)
        gastos = load_gastos()
        gastos.append({
            "data": data,
            "categoria": categoria,
            "descricao": descricao,
            "valor": valor,
        })
        save_gastos(gastos)
        flash("Gasto adicionado com sucesso!", "success")
        return redirect(url_for("listar_gastos"))

    return render_template("index.html", categorias=get_categorias())


@app.route("/editar/<int:indice>", methods=["GET", "POST"])
def editar(indice):
    gastos = load_gastos()
    if indice < 0 or indice >= len(gastos):
        flash("Item não encontrado.", "danger")
        return redirect(url_for("listar_gastos"))

    if request.method == "POST":
        data = request.form.get("data")
        categoria = request.form.get("categoria")
        descricao = request.form.get("descricao")
        valor_raw = request.form.get("valor")

        if not data or not categoria or not valor_raw:
            flash("Preencha data, categoria e valor.", "warning")
            return redirect(url_for("editar", indice=indice))

        gastos[indice] = {
            "data": data,
            "categoria": categoria,
            "descricao": descricao,
            "valor": parse_brl_to_float(valor_raw),
        }
        save_gastos(gastos)
        flash("Gasto atualizado.", "success")
        return redirect(url_for("listar_gastos"))

    return render_template("editar.html", indice=indice, gasto=gastos[indice], categorias=get_categorias())


@app.route("/excluir/<int:indice>")
def excluir(indice):
    gastos = load_gastos()
    if indice < 0 or indice >= len(gastos):
        flash("Item não encontrado.", "danger")
        return redirect(url_for("listar_gastos"))
    gastos.pop(indice)
    save_gastos(gastos)
    flash("Gasto excluído.", "info")
    return redirect(url_for("listar_gastos"))


if __name__ == "__main__":
    # Para desenvolvimento local
    app.run(debug=True, host="0.0.0.0", port=5000)
