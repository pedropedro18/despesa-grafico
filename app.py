"""Controle de despesas e entradas em Streamlit + matplotlib.

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

BASE = os.path.dirname(os.path.abspath(__file__))
ARQUIVO = os.path.join(BASE, "despesas.json")
ORC_ARQ = os.path.join(BASE, "orcamento.json")
REC_ARQ = os.path.join(BASE, "recorrentes.json")
ENT_ARQ = os.path.join(BASE, "entradas.json")

CATEGORIAS = ["Alimentação", "Transporte", "Moradia", "Saúde", "Educação", "Lazer", "Outros"]
FONTES = ["Salário", "Freelance", "Investimentos", "Presente", "Outros"]
COLS = ["data", "descricao", "categoria", "valor"]

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


def carregar_ent():
    return ler_dado("entradas", [], ENT_ARQ)


def salvar_ent(entradas):
    gravar_dado("entradas", entradas, ENT_ARQ)


def carregar_orc():
    return ler_dado("orcamento", {}, ORC_ARQ)


def salvar_orc(orc):
    gravar_dado("orcamento", orc, ORC_ARQ)


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


def montar_df(lista):
    """Converte a lista de lançamentos num DataFrame (mesmo se estiver vazia)."""
    df = pd.DataFrame(lista) if lista else pd.DataFrame(columns=COLS)
    df["data"] = pd.to_datetime(df["data"])
    df["valor"] = pd.to_numeric(df["valor"])
    df["mes"] = df["data"].dt.strftime("%Y-%m")
    return df


despesas = carregar()
entradas = carregar_ent()
orcamento = carregar_orc()
recorrentes = carregar_rec()

if aplicar_recorrentes(despesas, recorrentes):
    salvar(despesas)
    salvar_rec(recorrentes)

st.title("💰 Controle de Despesas")

# ---------- Barra lateral ----------
with st.sidebar:
    st.header("Nova entrada")
    with st.form("nova_entrada", clear_on_submit=True):
        e_desc = st.text_input("Descrição")
        e_cat = st.selectbox("Fonte", FONTES)
        e_valor = st.number_input("Valor", min_value=0.0, step=100.0, format="%.2f", key="e_valor")
        e_data = st.date_input("Data", value=date.today(), key="e_data")
        if st.form_submit_button("Adicionar entrada"):
            if e_valor > 0:
                entradas.append({
                    "data": e_data.isoformat(),
                    "descricao": e_desc,
                    "categoria": e_cat,
                    "valor": e_valor,
                })
                salvar_ent(entradas)
                st.success("Entrada adicionada.")
                st.rerun()
            else:
                st.error("O valor deve ser maior que zero.")

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

# ---------- Dados ----------
df_d = montar_df(despesas)
df_e = montar_df(entradas)

# ---------- Filtro por mês (padrão: mês atual) ----------
mes_atual = date.today().strftime("%Y-%m")
lista_meses = sorted(set(df_d["mes"]) | set(df_e["mes"]) | {mes_atual}, reverse=True)
meses = ["Todos"] + lista_meses
mes_sel = st.selectbox("Mês", meses, index=meses.index(mes_atual))

dff = df_d if mes_sel == "Todos" else df_d[df_d["mes"] == mes_sel]
dfe = df_e if mes_sel == "Todos" else df_e[df_e["mes"] == mes_sel]

total_e = float(dfe["valor"].sum())
total_d = float(dff["valor"].sum())
saldo = total_e - total_d
media = float(dff["valor"].mean()) if len(dff) else 0.0

# ---------- Indicadores e balanço ----------
c1, c2, c3 = st.columns(3)
c1.metric("Entradas", f"{total_e:,.2f}")
c2.metric("Despesas", f"{total_d:,.2f}")
c3.metric("Saldo final", f"{saldo:,.2f}")

c4, c5 = st.columns(2)
c4.metric("Nº de despesas", len(dff))
c5.metric("Média por despesa", f"{media:,.2f}")

if saldo < 0:
    st.error(f"⚠️ Gastaste mais do que recebeste: saldo de {saldo:,.2f}.")
elif total_e > 0:
    pct = total_d / total_e
    st.progress(min(pct, 1.0), text=f"{pct:.0%} das entradas já foram gastas")
    st.success(f"Sobram {saldo:,.2f}.")
elif total_d > 0:
    st.warning("Há despesas, mas nenhuma entrada neste período.")

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
    st.subheader("Despesas por categoria")
    if dff.empty:
        st.info("Sem despesas neste período.")
    else:
        por_cat = dff.groupby("categoria")["valor"].sum()
        fig1, ax1 = plt.subplots()
        ax1.pie(por_cat, labels=por_cat.index, autopct="%1.1f%%", startangle=90)
        st.pyplot(fig1)

with g2:
    st.subheader("Entradas vs Despesas")
    fig2, ax2 = plt.subplots()
    barras = ax2.bar(
        ["Entradas", "Despesas", "Saldo"],
        [total_e, total_d, saldo],
        color=["#2E7D32", "#C62828", "#1565C0" if saldo >= 0 else "#EF6C00"],
    )
    ax2.bar_label(barras, fmt="%.0f")
    ax2.axhline(0, color="black", linewidth=0.8)
    fig2.tight_layout()
    st.pyplot(fig2)

# ---------- Evolução mensal (só quando "Todos") ----------
if mes_sel == "Todos" and not df_d.empty:
    st.subheader("Evolução mensal por categoria")
    pivot = (
        df_d.pivot_table(index="mes", columns="categoria", values="valor", aggfunc="sum", fill_value=0)
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

# ---------- Tabelas ----------
st.subheader("Entradas")
if dfe.empty:
    st.caption("Nenhuma entrada neste período.")
else:
    tab_e = dfe.sort_values("data", ascending=False).copy()
    tab_e["data"] = tab_e["data"].dt.strftime("%d/%m/%Y")
    st.dataframe(tab_e[COLS], use_container_width=True)

st.subheader("Despesas")
tabela = dff.sort_values("data", ascending=False).copy()
tabela["data"] = tabela["data"].dt.strftime("%d/%m/%Y")
if tabela.empty:
    st.caption("Nenhuma despesa neste período.")
else:
    st.dataframe(tabela[COLS], use_container_width=True)

st.download_button(
    "Baixar CSV",
    tabela[COLS].to_csv(index=False).encode("utf-8"),
    "despesas.csv",
    "text/csv",
)


def gerar_pdf(desp, entr, titulo):
    """Cria o relatório em PDF (balanço + gráficos + tabela) só com matplotlib."""
    t_e = float(entr["valor"].sum())
    t_d = float(desp["valor"].sum())
    sld = t_e - t_d
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.suptitle(f"Relatório financeiro - {titulo}", fontsize=16, fontweight="bold")
        fig.text(
            0.1, 0.90,
            f"Entradas: {t_e:,.2f}    Despesas: {t_d:,.2f}    Saldo: {sld:,.2f}",
            fontsize=11,
        )
        if not desp.empty:
            ax1 = fig.add_axes([0.1, 0.50, 0.8, 0.35])
            por_cat = desp.groupby("categoria")["valor"].sum()
            ax1.pie(por_cat, labels=por_cat.index, autopct="%1.1f%%", startangle=90)
            ax1.set_title("Despesas por categoria")
        ax2 = fig.add_axes([0.12, 0.08, 0.78, 0.30])
        barras = ax2.bar(
            ["Entradas", "Despesas", "Saldo"],
            [t_e, t_d, sld],
            color=["#2E7D32", "#C62828", "#1565C0" if sld >= 0 else "#EF6C00"],
        )
        ax2.bar_label(barras, fmt="%.0f")
        ax2.axhline(0, color="black", linewidth=0.8)
        ax2.set_title("Entradas vs Despesas")
        pdf.savefig(fig)
        plt.close(fig)

        linhas = desp.sort_values("data")[COLS].copy()
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
    gerar_pdf(dff, dfe, "Todos os meses" if mes_sel == "Todos" else mes_sel),
    "relatorio_financeiro.pdf",
    "application/pdf",
)

# ---------- Remoção ----------
with st.expander("Remover lançamento"):
    tipo_rem = st.radio("Tipo", ["Despesa", "Entrada"], horizontal=True)
    lista = despesas if tipo_rem == "Despesa" else entradas
    if not lista:
        st.caption("Nada para remover.")
    else:
        opcoes = {
            i: f"{d['data']} | {d['categoria']} | {d['descricao']} | {d['valor']:.2f}"
            for i, d in enumerate(lista)
        }
        escolha = st.selectbox("Escolhe", list(opcoes), format_func=lambda i: opcoes[i])
        if st.button("Remover"):
            lista.pop(escolha)
            if tipo_rem == "Despesa":
                salvar(despesas)
            else:
                salvar_ent(entradas)
            st.rerun()