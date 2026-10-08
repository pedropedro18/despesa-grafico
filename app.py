"""Controle de despesas em Streamlit + matplotlib.

Requisitos: pip install streamlit matplotlib pandas
Executar:   streamlit run app.py
"""
import calendar
import hmac
import io
import json
import os
from datetime import date
from uuid import uuid4

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
import streamlit as st

ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "despesas.json")
CATEGORIAS = ["Alimentação", "Transporte", "Moradia", "Saúde", "Educação", "Lazer", "Outros"]

st.set_page_config(page_title="Controle de Despesas", page_icon="💰", layout="wide")


def verificar_senha():
    """Bloqueia a app até a senha correta ser introduzida."""
    try:
        correta = st.secrets["APP_PASSWORD"]
    except Exception:
        st.error(
            "Senha não configurada. Define APP_PASSWORD em .streamlit/secrets.toml (local) "
            "ou em Settings > Secrets (Streamlit Cloud)."
        )
        st.stop()
    if st.session_state.get("autenticado"):
        return
    st.title("🔒 Controle de Despesas")
    senha = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        if hmac.compare_digest(senha.encode(), str(correta).encode()):
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()


verificar_senha()

with st.sidebar:
    if st.button("Sair"):
        st.session_state["autenticado"] = False
        st.rerun()


def usa_db():
    """True se existir a secção [db] nos secrets (modo online com TiDB)."""
    try:
        return "db" in st.secrets
    except Exception:
        return False


def _conn():
    import certifi
    import pymysql

    cfg = st.secrets["db"]
    return pymysql.connect(
        host=cfg["host"],
        port=int(cfg.get("port", 4000)),
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        ssl_ca=certifi.where(),
        ssl_verify_cert=True,
        ssl_verify_identity=True,
        charset="utf8mb4",
    )


def ler_dado(chave, padrao, arquivo):
    """Lê um bloco de dados (JSON) da base de dados ou do ficheiro local."""
    if usa_db():
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS dados "
                    "(chave VARCHAR(50) PRIMARY KEY, valor LONGTEXT NOT NULL)"
                )
                cur.execute("SELECT valor FROM dados WHERE chave=%s", (chave,))
                linha = cur.fetchone()
            return json.loads(linha[0]) if linha else padrao
        finally:
            conn.close()
    if os.path.exists(arquivo):
        with open(arquivo, "r", encoding="utf-8") as f:
            return json.load(f)
    return padrao


def gravar_dado(chave, valor, arquivo):
    """Grava um bloco de dados (JSON) na base de dados ou no ficheiro local."""
    if usa_db():
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "REPLACE INTO dados (chave, valor) VALUES (%s, %s)",
                    (chave, json.dumps(valor, ensure_ascii=False)),
                )
            conn.commit()
        finally:
            conn.close()
    else:
        with open(arquivo, "w", encoding="utf-8") as f:
            json.dump(valor, f, ensure_ascii=False, indent=2)


def carregar():
    return ler_dado("despesas", [], ARQUIVO)


def salvar(despesas):
    gravar_dado("despesas", despesas, ARQUIVO)


ORC_ARQ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orcamento.json")


def carregar_orc():
    return ler_dado("orcamento", {}, ORC_ARQ)


def salvar_orc(orc):
    gravar_dado("orcamento", orc, ORC_ARQ)


REC_ARQ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorrentes.json")


def carregar_rec():
    return ler_dado("recorrentes", [], REC_ARQ)


def salvar_rec(rec):
    gravar_dado("recorrentes", rec, REC_ARQ)


def aplicar_recorrentes(despesas, recorrentes):
    """Lança as despesas fixas de cada mês (desde o início até hoje) que ainda não foram lançadas."""
    hoje = date.today()
    mudou = False
    for r in recorrentes:
        ano, mes = map(int, r["inicio"].split("-"))
        while (ano, mes) <= (hoje.year, hoje.month):
            chave = f"{ano:04d}-{mes:02d}"
            if chave not in r["gerados"]:
                dia = min(r["dia"], calendar.monthrange(ano, mes)[1])
                data = date(ano, mes, dia)
                if data > hoje:
                    break
                despesas.append({
                    "data": data.isoformat(),
                    "descricao": r["descricao"],
                    "categoria": r["categoria"],
                    "valor": r["valor"],
                    "recorrente": True,
                })
                r["gerados"].append(chave)
                mudou = True
            mes += 1
            if mes > 12:
                ano, mes = ano + 1, 1
    return mudou


despesas = carregar()
orcamento = carregar_orc()
recorrentes = carregar_rec()

if aplicar_recorrentes(despesas, recorrentes):
    salvar(despesas)
    salvar_rec(recorrentes)

st.title("💰 Controle de Despesas")

# ---------- Formulário ----------
with st.sidebar:
    st.header("Nova despesa")
    with st.form("nova", clear_on_submit=True):
        descricao = st.text_input("Descrição")
        categoria = st.selectbox("Categoria", CATEGORIAS)
        valor = st.number_input("Valor", min_value=0.0, step=100.0, format="%.2f")
        data = st.date_input("Data", value=date.today())
        if st.form_submit_button("Adicionar"):
            if valor > 0:
                despesas.append({
                    "data": data.isoformat(),
                    "descricao": descricao,
                    "categoria": categoria,
                    "valor": valor,
                })
                salvar(despesas)
                st.success("Despesa adicionada.")
                st.rerun()
            else:
                st.error("O valor deve ser maior que zero.")

with st.sidebar:
    st.header("Despesas fixas (mensais)")
    with st.form("rec", clear_on_submit=True):
        r_desc = st.text_input("Descrição (ex: Renda, Internet)")
        r_cat = st.selectbox("Categoria", CATEGORIAS, key="rec_cat")
        r_valor = st.number_input("Valor", min_value=0.0, step=100.0, format="%.2f", key="rec_valor")
        r_dia = st.number_input("Dia do mês", min_value=1, max_value=31, value=1, step=1)
        if st.form_submit_button("Adicionar despesa fixa"):
            if r_valor > 0 and r_desc.strip():
                recorrentes.append({
                    "id": uuid4().hex[:8],
                    "descricao": r_desc.strip(),
                    "categoria": r_cat,
                    "valor": r_valor,
                    "dia": int(r_dia),
                    "inicio": date.today().strftime("%Y-%m"),
                    "gerados": [],
                })
                salvar_rec(recorrentes)
                st.rerun()
            else:
                st.error("Preenche a descrição e um valor maior que zero.")

    if recorrentes:
        for r in recorrentes:
            c_a, c_b = st.columns([4, 1])
            c_a.caption(f"{r['descricao']} · {r['valor']:,.2f} · dia {r['dia']}")
            if c_b.button("✕", key=f"del_{r['id']}"):
                recorrentes.remove(r)
                salvar_rec(recorrentes)
                st.rerun()

    st.header("Orçamento mensal")
    with st.form("orc"):
        novo = {}
        for cat in CATEGORIAS:
            novo[cat] = st.number_input(
                cat, min_value=0.0, step=1000.0, value=float(orcamento.get(cat, 0.0)), format="%.2f"
            )
        if st.form_submit_button("Guardar orçamento"):
            salvar_orc(novo)
            st.success("Orçamento guardado.")
            st.rerun()

if not despesas:
    st.info("Ainda não há despesas. Adiciona a primeira na barra lateral.")
    st.stop()

df = pd.DataFrame(despesas)
df["data"] = pd.to_datetime(df["data"])
df["mes"] = df["data"].dt.strftime("%Y-%m")

# ---------- Filtro por mês ----------
meses = ["Todos"] + sorted(df["mes"].unique(), reverse=True)
mes_sel = st.selectbox("Filtrar por mês", meses)
dff = df if mes_sel == "Todos" else df[df["mes"] == mes_sel]

c1, c2, c3 = st.columns(3)
c1.metric("Total gasto", f"{dff['valor'].sum():,.2f}")
c2.metric("Nº de despesas", len(dff))
c3.metric("Média por despesa", f"{dff['valor'].mean():,.2f}")

# ---------- Orçamento e alertas ----------
if mes_sel != "Todos" and any(v > 0 for v in orcamento.values()):
    st.subheader(f"Orçamento de {mes_sel}")
    gasto_cat = dff.groupby("categoria")["valor"].sum()
    for cat in CATEGORIAS:
        limite = orcamento.get(cat, 0.0)
        if limite <= 0:
            continue
        gasto = float(gasto_cat.get(cat, 0.0))
        pct = gasto / limite
        st.progress(min(pct, 1.0), text=f"{cat}: {gasto:,.2f} / {limite:,.2f} ({pct:.0%})")
        if pct > 1:
            st.error(f"⚠️ {cat}: ultrapassaste o orçamento em {gasto - limite:,.2f}.")
        elif pct >= 0.8:
            st.warning(f"{cat}: já usaste {pct:.0%} do orçamento.")
elif any(v > 0 for v in orcamento.values()):
    st.caption("Escolhe um mês específico para ver o progresso do orçamento.")

# ---------- Gráficos ----------
g1, g2 = st.columns(2)

with g1:
    por_cat = dff.groupby("categoria")["valor"].sum()
    fig1, ax1 = plt.subplots()
    ax1.pie(por_cat, labels=por_cat.index, autopct="%1.1f%%", startangle=90)
    ax1.set_title("Por categoria")
    st.pyplot(fig1)

with g2:
    por_mes = df.groupby("mes")["valor"].sum().sort_index()
    fig2, ax2 = plt.subplots()
    barras = ax2.bar(por_mes.index, por_mes.values, color="#4C78A8")
    ax2.bar_label(barras, fmt="%.0f")
    ax2.set_title("Por mês")
    plt.setp(ax2.get_xticklabels(), rotation=45, ha="right")
    fig2.tight_layout()
    st.pyplot(fig2)

# ---------- Evolução mensal por categoria ----------
st.subheader("Evolução mensal por categoria")
pivot = (
    df.pivot_table(index="mes", columns="categoria", values="valor", aggfunc="sum", fill_value=0)
    .sort_index()
)
fig3, ax3 = plt.subplots(figsize=(10, 4))
pivot.plot(kind="bar", stacked=True, ax=ax3)
ax3.set_xlabel("Mês")
ax3.set_ylabel("Valor")
ax3.legend(title="Categoria", bbox_to_anchor=(1.01, 1), loc="upper left")
plt.setp(ax3.get_xticklabels(), rotation=45, ha="right")
fig3.tight_layout()
st.pyplot(fig3)

# ---------- Tabela e remoção ----------
st.subheader("Despesas")
tabela = dff.sort_values("data", ascending=False).copy()
tabela["data"] = tabela["data"].dt.strftime("%d/%m/%Y")
st.dataframe(tabela[["data", "descricao", "categoria", "valor"]], use_container_width=True)

st.download_button(
    "Baixar CSV",
    tabela[["data", "descricao", "categoria", "valor"]].to_csv(index=False).encode("utf-8"),
    "despesas.csv",
    "text/csv",
)

def gerar_pdf(dados, titulo):
    """Cria o relatório em PDF (resumo + gráficos + tabela) só com matplotlib."""
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        # Página 1: resumo e gráficos
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.suptitle(f"Relatório de Despesas - {titulo}", fontsize=16, fontweight="bold")
        fig.text(
            0.1, 0.90,
            f"Total: {dados['valor'].sum():,.2f}    "
            f"Despesas: {len(dados)}    "
            f"Média: {dados['valor'].mean():,.2f}",
            fontsize=11,
        )
        ax1 = fig.add_axes([0.1, 0.50, 0.8, 0.35])
        por_cat = dados.groupby("categoria")["valor"].sum()
        ax1.pie(por_cat, labels=por_cat.index, autopct="%1.1f%%", startangle=90)
        ax1.set_title("Por categoria")
        ax2 = fig.add_axes([0.12, 0.08, 0.78, 0.30])
        por_mes_pdf = dados.groupby("mes")["valor"].sum().sort_index()
        barras = ax2.bar(por_mes_pdf.index, por_mes_pdf.values, color="#4C78A8")
        ax2.bar_label(barras, fmt="%.0f")
        ax2.set_title("Por mês")
        plt.setp(ax2.get_xticklabels(), rotation=45, ha="right")
        pdf.savefig(fig)
        plt.close(fig)

        # Páginas seguintes: tabela (40 linhas por página)
        linhas = dados.sort_values("data")[["data", "descricao", "categoria", "valor"]].copy()
        linhas["data"] = linhas["data"].dt.strftime("%d/%m/%Y")
        linhas["valor"] = linhas["valor"].map(lambda v: f"{v:,.2f}")
        for i in range(0, len(linhas), 40):
            fig = plt.figure(figsize=(8.27, 11.69))
            ax = fig.add_axes([0.05, 0.05, 0.9, 0.9])
            ax.axis("off")
            t = ax.table(
                cellText=linhas.iloc[i:i + 40].values,
                colLabels=["Data", "Descrição", "Categoria", "Valor"],
                loc="upper center",
                cellLoc="left",
            )
            t.auto_set_font_size(False)
            t.set_fontsize(9)
            t.scale(1, 1.4)
            pdf.savefig(fig)
            plt.close(fig)
    return buf.getvalue()


st.download_button(
    "Baixar relatório PDF",
    gerar_pdf(dff, "Todos os meses" if mes_sel == "Todos" else mes_sel),
    "relatorio_despesas.pdf",
    "application/pdf",
)

with st.expander("Remover despesa"):
    opcoes = {
        i: f"{d['data']} | {d['categoria']} | {d['descricao']} | {d['valor']:.2f}"
        for i, d in enumerate(despesas)
    }
    escolha = st.selectbox("Escolhe", list(opcoes), format_func=lambda i: opcoes[i])
    if st.button("Remover"):
        despesas.pop(escolha)
        salvar(despesas)
        st.rerun()