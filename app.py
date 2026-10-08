"""Controle de despesas e entradas em Streamlit + matplotlib (v2, um único utilizador).

Requisitos: pip install streamlit matplotlib pandas openpyxl
Opcionais:  pip install pymysql certifi   (TiDB online)
            pip install anthropic          (assistente com IA)
Executar:   streamlit run app.py

Secrets (.streamlit/secrets.toml ou Settings > Secrets no Streamlit Cloud):
    APP_PASSWORD = "..."
    ANTHROPIC_API_KEY = "..."        # opcional, para o assistente com IA
    ANTHROPIC_MODEL = "claude-sonnet-5-5"   # opcional
    [email]                           # opcional, para lembretes por e-mail
    host = "smtp.gmail.com"
    port = 465
    user = "teu@gmail.com"
    password = "senha-de-app"
    [db]                              # opcional, TiDB
    host = "..." ; port = 4000 ; user = "..." ; password = "..." ; database = "..."
"""
import calendar
import hmac
import io
import json
import os
import smtplib
from datetime import date
from email.message import EmailMessage
from urllib.parse import quote

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from matplotlib.backends.backend_pdf import PdfPages

BASE = os.path.dirname(os.path.abspath(__file__))

CATEGORIAS_BASE = ["Alimentação", "Transporte", "Moradia", "Saúde", "Educação", "Lazer", "Outros"]
FONTES = ["Salário", "Freelance", "Investimentos", "Presente", "Outros"]
COLS = ["data", "descricao", "categoria", "conta", "valor"]

# Palavras-chave para categorizar automaticamente o extrato (edita à vontade)
REGRAS_DESPESA = {
    "Alimentação": ["supermerc", "restaur", "mercado", "padaria", "pizza", "ifood", "continente",
                    "lidl", "aldi", "pingo", "café", "cafe", "talho", "pastelaria"],
    "Transporte": ["uber", "bolt", "combust", "gasol", "posto", "metro", "metrô", "via verde",
                   "estacion", "onibus", "ônibus", "autocarro", "comboio"],
    "Moradia": ["aluguel", "renda", "condom", "energia", "água", "agua", "internet", "edp", "gás"],
    "Saúde": ["farmac", "clinica", "clínica", "hospital", "médic", "medic", "dent"],
    "Educação": ["escola", "curso", "udemy", "livraria", "faculdade", "propina", "mensalidade"],
    "Lazer": ["netflix", "spotify", "cinema", "steam", "viagem", "hotel", "airbnb", "disney"],
}
REGRAS_ENTRADA = {
    "Salário": ["salario", "salário", "vencimento", "ordenado"],
    "Freelance": ["freelance", "fatura", "recibo verde", "serviço", "servico"],
    "Investimentos": ["dividend", "juros", "rendimento", "resgate"],
}

st.set_page_config(page_title="Controle de Despesas", page_icon="💰", layout="wide")


# ---------- Acesso (um único utilizador) ----------
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


# ---------- Armazenamento (TiDB ou JSON local) ----------
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


def ler(chave, padrao):
    """Lê um bloco de dados (JSON) da base de dados ou do ficheiro local <chave>.json."""
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
    arquivo = os.path.join(BASE, f"{chave}.json")
    if os.path.exists(arquivo):
        with open(arquivo, "r", encoding="utf-8") as f:
            return json.load(f)
    return padrao


def gravar(chave, valor):
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
        with open(os.path.join(BASE, f"{chave}.json"), "w", encoding="utf-8") as f:
            json.dump(valor, f, ensure_ascii=False, indent=2)


# ---------- Utilitários ----------
def somar_meses(d, n):
    """Soma n meses a uma data, ajustando o dia ao tamanho do mês."""
    m = d.month - 1 + n
    ano, mes = d.year + m // 12, m % 12 + 1
    return date(ano, mes, min(d.day, calendar.monthrange(ano, mes)[1]))


def descrever(d):
    return f"{d['data']} | {d['categoria']} | {d['descricao']} | {float(d['valor']):.2f}"


def expandir_fixas(tipo):
    """Gera os lançamentos mensais dos fixos, do mês de início até hoje."""
    hoje = date.today()
    saida = []
    for f in fixas:
        if f["tipo"] != tipo:
            continue
        ini = date.fromisoformat(f["inicio"])
        atual = date(ini.year, ini.month, 1)
        while atual <= date(hoje.year, hoje.month, 1):
            dia = min(int(f["dia"]), calendar.monthrange(atual.year, atual.month)[1])
            d = date(atual.year, atual.month, dia)
            if ini <= d <= hoje:
                saida.append({
                    "data": d.isoformat(),
                    "descricao": f["descricao"],
                    "categoria": f["categoria"],
                    "conta": f.get("conta", "Carteira"),
                    "valor": float(f["valor"]),
                })
            atual = somar_meses(atual, 1)
    return saida


def montar_df(base, extras):
    """DataFrame com lançamentos guardados (idx = posição) + fixos gerados (idx = -1)."""
    linhas = [{**d, "idx": i, "fixa": False} for i, d in enumerate(base)]
    linhas += [{**d, "idx": -1, "fixa": True} for d in extras]
    df = pd.DataFrame(linhas) if linhas else pd.DataFrame(columns=COLS + ["idx", "fixa"])
    if "conta" not in df:
        df["conta"] = "Carteira"
    df["conta"] = df["conta"].fillna("Carteira")
    df["data"] = pd.to_datetime(df["data"])
    df["valor"] = pd.to_numeric(df["valor"])
    df["mes"] = df["data"].dt.strftime("%Y-%m")
    return df


def converter_valor(x):
    """Converte texto de extrato ('1.234,56', '-12.5', '€ 10,00') em número."""
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("€", "").replace("R$", "").replace(" ", "")
    if not s:
        return float("nan")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def categorizar(descricao, e_entrada):
    d = str(descricao).lower()
    for cat in config["categorias"]:  # categorias personalizadas: nome dentro da descrição
        if not e_entrada and cat.lower() in d:
            return cat
    regras = REGRAS_ENTRADA if e_entrada else REGRAS_DESPESA
    for cat, palavras in regras.items():
        if any(p in d for p in palavras):
            return cat
    return "Outros"


def enviar_email(assunto, corpo, destino):
    cfg = st.secrets["email"]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = assunto, cfg["user"], destino
    msg.set_content(corpo)
    with smtplib.SMTP_SSL(cfg.get("host", "smtp.gmail.com"), int(cfg.get("port", 465))) as s:
        s.login(cfg["user"], cfg["password"])
        s.send_message(msg)


# ---------- Dados ----------
despesas = ler("despesas", [])
entradas = ler("entradas", [])
orcamento = ler("orcamento", {})
fixas = ler("fixas", [])
a_pagar = ler("a_pagar", [])
config = {"meta": 0.0, "categorias": [], "contas": ["Carteira"], "whatsapp": "",
          "email": "", "dias_aviso": 3}
config.update(ler("config", {}))

CATEGORIAS = CATEGORIAS_BASE + [c for c in config["categorias"] if c not in CATEGORIAS_BASE]
CONTAS = config["contas"] if "Carteira" in config["contas"] else ["Carteira"] + config["contas"]
TODAS_CATS = sorted(set(CATEGORIAS) | set(FONTES))

df_d = montar_df(despesas, expandir_fixas("Despesa"))
df_e = montar_df(entradas, expandir_fixas("Entrada"))

st.title("💰 Controle de Despesas")

# ---------- Barra lateral ----------
with st.sidebar:
    st.header("Nova entrada")
    with st.form("nova_entrada", clear_on_submit=True):
        e_desc = st.text_input("Descrição", key="e_desc")
        e_cat = st.selectbox("Fonte", FONTES, key="e_cat")
        e_conta = st.selectbox("Conta / cartão", CONTAS, key="e_conta")
        e_valor = st.number_input("Valor", min_value=0.0, step=100.0, format="%.2f", key="e_valor")
        e_data = st.date_input("Data", value=date.today(), key="e_data")
        if st.form_submit_button("Adicionar entrada"):
            if e_valor > 0:
                entradas.append({"data": e_data.isoformat(), "descricao": e_desc,
                                 "categoria": e_cat, "conta": e_conta, "valor": e_valor})
                gravar("entradas", entradas)
                st.success("Entrada adicionada.")
                st.rerun()
            else:
                st.error("O valor deve ser maior que zero.")

    st.header("Nova despesa")
    with st.form("nova", clear_on_submit=True):
        descricao = st.text_input("Descrição", key="d_desc")
        categoria = st.selectbox("Categoria", CATEGORIAS, key="d_cat")
        conta = st.selectbox("Conta / cartão", CONTAS, key="d_conta")
        valor = st.number_input("Valor (total)", min_value=0.0, step=100.0, format="%.2f", key="d_valor")
        data = st.date_input("Data (1ª parcela)", value=date.today(), key="d_data")
        parcelas = st.number_input("Parcelas", min_value=1, max_value=60, value=1, step=1, key="d_parc")
        if st.form_submit_button("Adicionar"):
            if valor > 0:
                n = int(parcelas)
                parcela = round(valor / n, 2)
                for k in range(n):
                    v = parcela if k < n - 1 else round(valor - parcela * (n - 1), 2)
                    despesas.append({
                        "data": somar_meses(data, k).isoformat(),
                        "descricao": f"{descricao} ({k + 1}/{n})" if n > 1 else descricao,
                        "categoria": categoria, "conta": conta, "valor": v,
                    })
                gravar("despesas", despesas)
                st.success("Despesa adicionada." if n == 1 else f"{n} parcelas adicionadas.")
                st.rerun()
            else:
                st.error("O valor deve ser maior que zero.")

    st.header("Orçamento mensal")
    with st.form("orc"):
        novo = {}
        for cat in CATEGORIAS:
            novo[cat] = st.number_input(
                cat, min_value=0.0, step=1000.0, value=float(orcamento.get(cat, 0.0)),
                format="%.2f", key=f"orc_{cat}",
            )
        if st.form_submit_button("Guardar orçamento"):
            gravar("orcamento", novo)
            st.success("Orçamento guardado.")
            st.rerun()

# ---------- Alertas de contas a pagar ----------
hoje = date.today()
pendentes = sorted([c for c in a_pagar if not c.get("pago")], key=lambda c: c["vencimento"])
urgentes = [c for c in pendentes
            if (date.fromisoformat(c["vencimento"]) - hoje).days <= int(config["dias_aviso"])]
for c in urgentes:
    dias = (date.fromisoformat(c["vencimento"]) - hoje).days
    quando = f"venceu há {-dias} dia(s)" if dias < 0 else ("vence hoje" if dias == 0 else f"vence em {dias} dia(s)")
    (st.error if dias < 0 else st.warning)(f"🔔 {c['descricao']} ({float(c['valor']):,.2f}) {quando}.")

# ---------- Filtro por mês (padrão: mês atual) ----------
mes_atual = hoje.strftime("%Y-%m")
lista_meses = sorted(set(df_d["mes"]) | set(df_e["mes"]) | {mes_atual}, reverse=True)
meses = ["Todos"] + lista_meses
mes_sel = st.selectbox("Mês", meses, index=meses.index(mes_atual))

dm_d = df_d if mes_sel == "Todos" else df_d[df_d["mes"] == mes_sel]
dm_e = df_e if mes_sel == "Todos" else df_e[df_e["mes"] == mes_sel]

# ---------- Busca e filtros ----------
with st.expander("🔎 Busca e filtros"):
    busca = st.text_input("Descrição contém", key="f_busca")
    f_cat = st.multiselect("Categoria / fonte", TODAS_CATS, key="f_cat")
    f_conta = st.multiselect("Conta / cartão", CONTAS, key="f_conta")
    ini = fim = None
    if st.checkbox("Filtrar por intervalo de datas", key="f_usar_int"):
        r = st.date_input("Intervalo", value=(hoje.replace(day=1), hoje), key="f_int")
        if isinstance(r, (tuple, list)) and len(r) == 2:
            ini, fim = r


def filtrar(df):
    if busca:
        df = df[df["descricao"].astype(str).str.contains(busca, case=False, na=False, regex=False)]
    if f_cat:
        df = df[df["categoria"].isin(f_cat)]
    if f_conta:
        df = df[df["conta"].isin(f_conta)]
    if ini and fim:
        df = df[(df["data"].dt.date >= ini) & (df["data"].dt.date <= fim)]
    return df


dff = filtrar(dm_d)
dfe = filtrar(dm_e)

total_e = float(dfe["valor"].sum())
total_d = float(dff["valor"].sum())
saldo = total_e - total_d
media = float(dff["valor"].mean()) if len(dff) else 0.0
periodo = "Todos os meses" if mes_sel == "Todos" else mes_sel


def gerar_pdf(desp, entr, titulo):
    """Cria o relatório em PDF (balanço + gráficos + tabela) só com matplotlib."""
    t_e = float(entr["valor"].sum())
    t_d = float(desp["valor"].sum())
    sld = t_e - t_d
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.suptitle(f"Relatório financeiro - {titulo}", fontsize=16, fontweight="bold")
        fig.text(0.1, 0.90, f"Entradas: {t_e:,.2f}    Despesas: {t_d:,.2f}    Saldo: {sld:,.2f}",
                 fontsize=11)
        if not desp.empty:
            ax1 = fig.add_axes([0.1, 0.50, 0.8, 0.35])
            por_cat = desp.groupby("categoria")["valor"].sum()
            ax1.pie(por_cat, labels=por_cat.index, autopct="%1.1f%%", startangle=90)
            ax1.set_title("Despesas por categoria")
        ax2 = fig.add_axes([0.12, 0.08, 0.78, 0.30])
        barras = ax2.bar(["Entradas", "Despesas", "Saldo"], [t_e, t_d, sld],
                         color=["#2E7D32", "#C62828", "#1565C0" if sld >= 0 else "#EF6C00"])
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
            t = ax.table(cellText=linhas.iloc[i:i + 40].values,
                         colLabels=["Data", "Descrição", "Categoria", "Conta", "Valor"],
                         loc="upper center", cellLoc="left")
            t.auto_set_font_size(False)
            t.set_fontsize(9)
            t.scale(1, 1.4)
            pdf.savefig(fig)
            plt.close(fig)
    return buf.getvalue()


def gerar_excel(desp, entr):
    """Exporta entradas, despesas e resumo por categoria para .xlsx."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for nome, df in (("Entradas", entr), ("Despesas", desp)):
            t = df.sort_values("data")[COLS].copy()
            t["data"] = t["data"].dt.strftime("%d/%m/%Y")
            t.to_excel(xw, sheet_name=nome, index=False)
        resumo = desp.groupby("categoria")["valor"].sum().reset_index()
        resumo.columns = ["Categoria", "Total gasto"]
        resumo.to_excel(xw, sheet_name="Resumo", index=False)
    return buf.getvalue()


tab_res, tab_lanc, tab_pagar, tab_imp, tab_ia, tab_cfg = st.tabs(
    ["📊 Resumo", "🧾 Lançamentos", "🔔 Contas a pagar", "📥 Importar extrato",
     "🤖 Assistente", "⚙️ Configurações"]
)

# =====================================================================
# RESUMO
# =====================================================================
with tab_res:
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

    # Meta de economia
    meta = float(config["meta"])
    if meta > 0 and mes_sel != "Todos":
        guardado = max(saldo, 0.0)
        st.progress(min(guardado / meta, 1.0),
                    text=f"Meta de economia: {guardado:,.2f} / {meta:,.2f} ({guardado / meta:.0%})")
        if saldo >= meta:
            st.success("🎯 Meta de economia atingida!")
        else:
            st.info(f"Faltam {meta - max(saldo, 0.0):,.2f} para a meta deste mês.")

    # Projeção (só no mês atual)
    if mes_sel == mes_atual and total_d > 0:
        dias_mes = calendar.monthrange(hoje.year, hoje.month)[1]
        proj_d = total_d / hoje.day * dias_mes
        p1, p2 = st.columns(2)
        p1.metric("Projeção de despesas no fim do mês", f"{proj_d:,.2f}")
        p2.metric("Saldo projetado no fim do mês", f"{total_e - proj_d:,.2f}")
        st.caption("Estimativa linear: ritmo médio diário de gastos até hoje × dias do mês.")

    # Comparação com mês anterior
    if mes_sel != "Todos":
        ano, m = map(int, mes_sel.split("-"))
        ant = somar_meses(date(ano, m, 1), -1).strftime("%Y-%m")
        atu = df_d[df_d["mes"] == mes_sel].groupby("categoria")["valor"].sum()
        pas = df_d[df_d["mes"] == ant].groupby("categoria")["valor"].sum()
        t_atu, t_pas = float(atu.sum()), float(pas.sum())
        if t_atu > 0 or t_pas > 0:
            st.subheader(f"Comparação com {ant}")
            st.metric("Despesas totais", f"{t_atu:,.2f}", delta=f"{t_atu - t_pas:,.2f}",
                      delta_color="inverse")
            comp = pd.DataFrame({"Mês anterior": pas, "Mês atual": atu}).fillna(0.0)
            comp["Diferença"] = comp["Mês atual"] - comp["Mês anterior"]
            comp["Variação %"] = comp["Diferença"] / comp["Mês anterior"].where(comp["Mês anterior"] > 0) * 100
            st.dataframe(comp.round(2), use_container_width=True)

    # Orçamento e alertas
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

    # Gráficos
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
            plt.close(fig1)
    with g2:
        st.subheader("Entradas vs Despesas")
        fig2, ax2 = plt.subplots()
        barras = ax2.bar(["Entradas", "Despesas", "Saldo"], [total_e, total_d, saldo],
                         color=["#2E7D32", "#C62828", "#1565C0" if saldo >= 0 else "#EF6C00"])
        ax2.bar_label(barras, fmt="%.0f")
        ax2.axhline(0, color="black", linewidth=0.8)
        fig2.tight_layout()
        st.pyplot(fig2)
        plt.close(fig2)

    if not dff.empty and dff["conta"].nunique() > 1:
        st.subheader("Despesas por conta / cartão")
        por_conta = dff.groupby("conta")["valor"].sum().sort_values()
        fig4, ax4 = plt.subplots(figsize=(8, 3))
        b = ax4.barh(por_conta.index, por_conta.values, color="#1565C0")
        ax4.bar_label(b, fmt="%.0f")
        fig4.tight_layout()
        st.pyplot(fig4)
        plt.close(fig4)

    if mes_sel == "Todos" and not dm_d.empty:
        st.subheader("Evolução mensal por categoria")
        pivot = (dff.pivot_table(index="mes", columns="categoria", values="valor",
                                 aggfunc="sum", fill_value=0).sort_index())
        if not pivot.empty:
            fig3, ax3 = plt.subplots(figsize=(10, 4))
            pivot.plot(kind="bar", stacked=True, ax=ax3)
            ax3.set_xlabel("Mês")
            ax3.set_ylabel("Valor")
            ax3.legend(title="Categoria", bbox_to_anchor=(1.01, 1), loc="upper left")
            plt.setp(ax3.get_xticklabels(), rotation=45, ha="right")
            fig3.tight_layout()
            st.pyplot(fig3)
            plt.close(fig3)

# =====================================================================
# LANÇAMENTOS
# =====================================================================
with tab_lanc:
    st.subheader("Entradas")
    if dfe.empty:
        st.caption("Nenhuma entrada neste período.")
    else:
        tab_e = dfe.sort_values("data", ascending=False).copy()
        tab_e["data"] = tab_e["data"].dt.strftime("%d/%m/%Y")
        tab_e.insert(0, "🔁", tab_e["fixa"].map({True: "🔁", False: ""}))
        st.dataframe(tab_e[["🔁"] + COLS], use_container_width=True)

    st.subheader("Despesas")
    tabela = dff.sort_values("data", ascending=False).copy()
    tabela["data"] = tabela["data"].dt.strftime("%d/%m/%Y")
    if tabela.empty:
        st.caption("Nenhuma despesa neste período.")
    else:
        tabela.insert(0, "🔁", tabela["fixa"].map({True: "🔁", False: ""}))
        st.dataframe(tabela[["🔁"] + COLS], use_container_width=True)
    st.caption("🔁 = lançamento fixo gerado automaticamente (gere-os em Configurações).")

    d1, d2, d3 = st.columns(3)
    d1.download_button("Baixar CSV", tabela[COLS].to_csv(index=False).encode("utf-8"),
                       "despesas.csv", "text/csv")
    try:
        d2.download_button(
            "Baixar Excel", gerar_excel(dff, dfe), "financas.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except ImportError:
        d2.caption("Para exportar Excel: pip install openpyxl")
    d3.download_button("Baixar relatório PDF", gerar_pdf(dff, dfe, periodo),
                       "relatorio_financeiro.pdf", "application/pdf")

    # Editar
    with st.expander("✏️ Editar lançamento"):
        tipo_ed = st.radio("Tipo", ["Despesa", "Entrada"], horizontal=True, key="ed_tipo")
        lista = despesas if tipo_ed == "Despesa" else entradas
        cats = CATEGORIAS if tipo_ed == "Despesa" else FONTES
        if not lista:
            st.caption("Nada para editar.")
        else:
            ids = list(range(len(lista)))[::-1]
            esc = st.selectbox("Escolhe", ids, format_func=lambda i: descrever(lista[i]), key="ed_sel")
            it = lista[esc]
            k = f"{tipo_ed}_{esc}"
            opts_cat = cats if it["categoria"] in cats else cats + [it["categoria"]]
            conta_it = it.get("conta", "Carteira")
            opts_conta = CONTAS if conta_it in CONTAS else CONTAS + [conta_it]
            with st.form("form_editar"):
                n_desc = st.text_input("Descrição", it.get("descricao", ""), key=f"ed_d_{k}")
                n_cat = st.selectbox("Categoria / fonte", opts_cat,
                                     index=opts_cat.index(it["categoria"]), key=f"ed_c_{k}")
                n_conta = st.selectbox("Conta / cartão", opts_conta,
                                       index=opts_conta.index(conta_it), key=f"ed_ct_{k}")
                n_valor = st.number_input("Valor", min_value=0.0, value=float(it["valor"]),
                                          step=10.0, format="%.2f", key=f"ed_v_{k}")
                n_data = st.date_input("Data", value=date.fromisoformat(it["data"]), key=f"ed_dt_{k}")
                if st.form_submit_button("Guardar alterações"):
                    if n_valor > 0:
                        it.update({"descricao": n_desc, "categoria": n_cat, "conta": n_conta,
                                   "valor": n_valor, "data": n_data.isoformat()})
                        gravar("despesas" if tipo_ed == "Despesa" else "entradas", lista)
                        st.success("Lançamento atualizado.")
                        st.rerun()
                    else:
                        st.error("O valor deve ser maior que zero.")

    # Remover
    with st.expander("🗑️ Remover lançamento"):
        tipo_rem = st.radio("Tipo", ["Despesa", "Entrada"], horizontal=True, key="rem_tipo")
        lista = despesas if tipo_rem == "Despesa" else entradas
        if not lista:
            st.caption("Nada para remover.")
        else:
            ids = list(range(len(lista)))[::-1]
            escolha = st.selectbox("Escolhe", ids, format_func=lambda i: descrever(lista[i]),
                                   key="rem_sel")
            if st.button("Remover", key="rem_btn"):
                lista.pop(escolha)
                gravar("despesas" if tipo_rem == "Despesa" else "entradas", lista)
                st.rerun()

# =====================================================================
# CONTAS A PAGAR + LEMBRETES
# =====================================================================
with tab_pagar:
    st.subheader("Contas a pagar")
    with st.form("nova_conta_pagar", clear_on_submit=True):
        p_desc = st.text_input("Descrição", key="p_desc")
        p_cat = st.selectbox("Categoria", CATEGORIAS, key="p_cat")
        p_valor = st.number_input("Valor", min_value=0.0, step=10.0, format="%.2f", key="p_valor")
        p_venc = st.date_input("Vencimento", value=hoje, key="p_venc")
        if st.form_submit_button("Adicionar conta"):
            if p_valor > 0 and p_desc:
                a_pagar.append({"descricao": p_desc, "categoria": p_cat, "valor": p_valor,
                                "vencimento": p_venc.isoformat(), "pago": False})
                gravar("a_pagar", a_pagar)
                st.rerun()
            else:
                st.error("Indica descrição e um valor maior que zero.")

    if not pendentes:
        st.caption("Nenhuma conta pendente.")
    for c in pendentes:
        i = a_pagar.index(c)
        dias = (date.fromisoformat(c["vencimento"]) - hoje).days
        estado = "🔴 vencida" if dias < 0 else ("🟠 hoje" if dias == 0 else f"em {dias} dia(s)")
        a, b, d = st.columns([5, 2, 2])
        a.write(f"*{c['descricao']}* — {float(c['valor']):,.2f} · {c['vencimento']} ({estado})")
        if b.button("✅ Paga", key=f"pg_{i}"):
            despesas.append({"data": hoje.isoformat(), "descricao": c["descricao"],
                             "categoria": c["categoria"], "conta": CONTAS[0],
                             "valor": float(c["valor"])})
            gravar("despesas", despesas)
            c["pago"] = True
            gravar("a_pagar", a_pagar)
            st.rerun()
        if d.button("🗑️", key=f"pgdel_{i}"):
            a_pagar.pop(i)
            gravar("a_pagar", a_pagar)
            st.rerun()

    st.subheader("Lembretes")
    alvo = urgentes or pendentes[:5]
    texto = "Contas a pagar:\n" + "\n".join(
        f"- {c['descricao']}: {float(c['valor']):,.2f} (vence {c['vencimento']})" for c in alvo
    ) if alvo else ""
    if not texto:
        st.caption("Sem contas pendentes para lembrar.")
    else:
        l1, l2 = st.columns(2)
        if config["whatsapp"]:
            l1.link_button("📲 Abrir no WhatsApp",
                           f"https://wa.me/{config['whatsapp']}?text={quote(texto)}")
        else:
            l1.caption("Define o número de WhatsApp em Configurações.")
        if config["email"] and "email" in st.secrets:
            if l2.button("✉️ Enviar por e-mail"):
                try:
                    enviar_email("Contas a pagar", texto, config["email"])
                    st.success("E-mail enviado.")
                except Exception as e:
                    st.error(f"Não foi possível enviar: {e}")
        else:
            l2.caption("Para e-mail: define o destinatário em Configurações e a secção [email] nos secrets.")
    st.caption("Os envios são manuais (botões). Envio automático em horário fixo exige um agendador externo.")

# =====================================================================
# IMPORTAR EXTRATO
# =====================================================================
with tab_imp:
    st.subheader("Importar extrato bancário (CSV)")
    up = st.file_uploader("Ficheiro CSV", type=["csv"], key="imp_file")
    if up is not None:
        bruto = up.getvalue()
        try:
            txt = bruto.decode("utf-8-sig")
        except UnicodeDecodeError:
            txt = bruto.decode("latin-1")
        try:
            ext = pd.read_csv(io.StringIO(txt), sep=None, engine="python")
        except Exception as e:
            st.error(f"Não consegui ler o CSV: {e}")
            ext = None
        if ext is not None and not ext.empty:
            st.dataframe(ext.head(5), use_container_width=True)
            colunas = list(ext.columns)
            i1, i2, i3 = st.columns(3)
            c_data = i1.selectbox("Coluna da data", colunas, key="imp_cd")
            c_desc = i2.selectbox("Coluna da descrição", colunas,
                                  index=min(1, len(colunas) - 1), key="imp_cs")
            c_val = i3.selectbox("Coluna do valor", colunas,
                                 index=min(2, len(colunas) - 1), key="imp_cv")
            dia_primeiro = st.checkbox("Datas no formato dia/mês/ano", value=True, key="imp_df")
            st.caption("Valores negativos viram despesas; positivos viram entradas.")

            prev = pd.DataFrame({
                "data": pd.to_datetime(ext[c_data], dayfirst=dia_primeiro, errors="coerce"),
                "descricao": ext[c_desc].astype(str),
                "valor": ext[c_val].map(converter_valor),
            }).dropna(subset=["data", "valor"])
            prev = prev[prev["valor"] != 0]
            prev["tipo"] = prev["valor"].map(lambda v: "Entrada" if v > 0 else "Despesa")
            prev["valor"] = prev["valor"].abs()
            prev["categoria"] = [categorizar(d, t == "Entrada")
                                 for d, t in zip(prev["descricao"], prev["tipo"])]
            prev["conta"] = CONTAS[0]
            prev["data"] = prev["data"].dt.date

            editado = st.data_editor(
                prev[["data", "tipo", "descricao", "categoria", "conta", "valor"]],
                num_rows="dynamic", use_container_width=True, key=f"imp_ed_{up.name}",
                column_config={
                    "tipo": st.column_config.SelectboxColumn("tipo", options=["Despesa", "Entrada"]),
                    "categoria": st.column_config.SelectboxColumn("categoria", options=TODAS_CATS),
                    "conta": st.column_config.SelectboxColumn("conta", options=CONTAS),
                },
            )
            if st.button(f"Importar {len(editado)} lançamentos", key="imp_btn"):
                existentes = {("Despesa", d["data"], d["descricao"], round(float(d["valor"]), 2)) for d in despesas}
                existentes |= {("Entrada", d["data"], d["descricao"], round(float(d["valor"]), 2)) for d in entradas}
                novos_d = novos_e = ignorados = 0
                for _, r in editado.iterrows():
                    iso = pd.to_datetime(r["data"]).date().isoformat()
                    chave = (r["tipo"], iso, r["descricao"], round(float(r["valor"]), 2))
                    if chave in existentes:
                        ignorados += 1
                        continue
                    reg = {"data": iso, "descricao": r["descricao"], "categoria": r["categoria"],
                           "conta": r["conta"], "valor": float(r["valor"])}
                    if r["tipo"] == "Entrada":
                        entradas.append(reg)
                        novos_e += 1
                    else:
                        despesas.append(reg)
                        novos_d += 1
                if novos_d:
                    gravar("despesas", despesas)
                if novos_e:
                    gravar("entradas", entradas)
                st.success(f"Importadas {novos_d} despesas e {novos_e} entradas "
                           f"({ignorados} duplicadas ignoradas).")
                st.rerun()

# =====================================================================
# ASSISTENTE COM IA
# =====================================================================
with tab_ia:
    st.subheader("Assistente financeiro")
    chave_api = st.secrets.get("ANTHROPIC_API_KEY") if hasattr(st.secrets, "get") else None
    if not chave_api:
        st.info("Define ANTHROPIC_API_KEY nos secrets para ativar o assistente.")
    else:
        st.caption(f"Os lançamentos de «{periodo}» são enviados à API da Anthropic para responder.")
        pergunta = st.text_input("Pergunta", placeholder="Quanto gastei com lazer este mês?", key="ia_perg")
        cA, cB = st.columns(2)
        dicas = cB.button("💡 Dicas de economia", key="ia_dicas")
        perguntar = cA.button("Perguntar", key="ia_btn")
        if dicas:
            pergunta = "Analisa as minhas despesas e dá-me dicas práticas e concretas de economia."
        if (perguntar or dicas) and pergunta.strip():
            def csv_curto(df):
                t = df.sort_values("data").tail(300)[COLS].copy()
                t["data"] = t["data"].dt.strftime("%Y-%m-%d")
                return t.to_csv(index=False)

            contexto = (
                f"Período: {periodo}. Data de hoje: {hoje.isoformat()}.\n"
                f"Entradas: {float(dm_e['valor'].sum()):.2f}; Despesas: {float(dm_d['valor'].sum()):.2f}.\n"
                f"Orçamento mensal por categoria: {json.dumps(orcamento, ensure_ascii=False)}.\n"
                f"Meta de economia mensal: {config['meta']}.\n\n"
                f"DESPESAS (CSV):\n{csv_curto(dm_d)}\nENTRADAS (CSV):\n{csv_curto(dm_e)}"
            )
            try:
                import anthropic

                cliente = anthropic.Anthropic(api_key=chave_api)
                with st.spinner("A pensar..."):
                    resp = cliente.messages.create(
                        model=st.secrets.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
                        max_tokens=1000,
                        system=("És um assistente de finanças pessoais. Responde em português, de forma "
                                "curta e prática, usando apenas os dados fornecidos. Se os dados não "
                                "chegarem para responder, di-lo."),
                        messages=[{"role": "user", "content": f"{contexto}\n\nPergunta: {pergunta}"}],
                    )
                st.markdown("".join(b.text for b in resp.content if b.type == "text"))
            except ImportError:
                st.error("Instala o pacote: pip install anthropic")
            except Exception as e:
                st.error(f"Erro ao contactar a API: {e}")

# =====================================================================
# CONFIGURAÇÕES
# =====================================================================
with tab_cfg:
    st.subheader("Meta e lembretes")
    with st.form("cfg_geral"):
        n_meta = st.number_input("Meta de economia mensal", min_value=0.0, step=100.0,
                                 value=float(config["meta"]), format="%.2f", key="cfg_meta")
        n_dias = st.number_input("Avisar contas a pagar com antecedência (dias)", min_value=0,
                                 max_value=30, value=int(config["dias_aviso"]), key="cfg_dias")
        n_wa = st.text_input("WhatsApp com indicativo, só números (ex.: 351912345678)",
                             value=config["whatsapp"], key="cfg_wa")
        n_mail = st.text_input("E-mail de destino dos lembretes", value=config["email"], key="cfg_mail")
        if st.form_submit_button("Guardar"):
            config.update({"meta": n_meta, "dias_aviso": int(n_dias),
                           "whatsapp": "".join(ch for ch in n_wa if ch.isdigit()), "email": n_mail.strip()})
            gravar("config", config)
            st.rerun()

    st.subheader("Categorias personalizadas")
    cc1, cc2 = st.columns([3, 1])
    nova_cat = cc1.text_input("Nova categoria de despesa", key="cfg_nova_cat")
    if cc2.button("Adicionar", key="cfg_add_cat"):
        nome = nova_cat.strip()
        if nome and nome not in CATEGORIAS:
            config["categorias"].append(nome)
            gravar("config", config)
            st.rerun()
    if config["categorias"]:
        rem = st.multiselect("Remover categorias personalizadas", config["categorias"], key="cfg_rem_cat")
        if rem and st.button("Remover selecionadas", key="cfg_rem_cat_btn"):
            config["categorias"] = [c for c in config["categorias"] if c not in rem]
            gravar("config", config)
            st.rerun()
        st.caption("Lançamentos antigos mantêm o nome da categoria removida.")

    st.subheader("Contas e cartões")
    ct1, ct2 = st.columns([3, 1])
    nova_conta = ct1.text_input("Nova conta / cartão", key="cfg_nova_conta")
    if ct2.button("Adicionar", key="cfg_add_conta"):
        nome = nova_conta.strip()
        if nome and nome not in CONTAS:
            config["contas"] = CONTAS + [nome]
            gravar("config", config)
            st.rerun()
    extras = [c for c in CONTAS if c != "Carteira"]
    if extras:
        rem_c = st.multiselect("Remover contas", extras, key="cfg_rem_conta")
        if rem_c and st.button("Remover contas selecionadas", key="cfg_rem_conta_btn"):
            config["contas"] = [c for c in CONTAS if c not in rem_c]
            gravar("config", config)
            st.rerun()

    st.subheader("Lançamentos fixos (repetem todo mês)")
    with st.form("form_fixa", clear_on_submit=True):
        f_tipo = st.radio("Tipo", ["Entrada", "Despesa"], horizontal=True, key="fx_tipo")
        f_desc = st.text_input("Descrição (ex.: Salário, Renda da casa)", key="fx_desc")
        f_catg = st.selectbox("Categoria / fonte", TODAS_CATS, key="fx_cat")
        f_cont = st.selectbox("Conta / cartão", CONTAS, key="fx_conta")
        f_val = st.number_input("Valor", min_value=0.0, step=100.0, format="%.2f", key="fx_val")
        f_dia = st.number_input("Dia do mês", min_value=1, max_value=31, value=1, key="fx_dia")
        f_ini = st.date_input("A partir de", value=hoje, key="fx_ini")
        if st.form_submit_button("Adicionar fixo"):
            if f_val > 0 and f_desc:
                fixas.append({"tipo": f_tipo, "descricao": f_desc, "categoria": f_catg,
                              "conta": f_cont, "valor": f_val, "dia": int(f_dia),
                              "inicio": f_ini.isoformat()})
                gravar("fixas", fixas)
                st.rerun()
            else:
                st.error("Indica descrição e um valor maior que zero.")
    if fixas:
        for i, f in enumerate(fixas):
            x, y = st.columns([6, 1])
            x.write(f"*{f['tipo']}* · {f['descricao']} · {float(f['valor']):,.2f} · "
                    f"dia {f['dia']} · desde {f['inicio']}")
            if y.button("🗑️", key=f"fx_del_{i}"):
                fixas.pop(i)
                gravar("fixas", fixas)
                st.rerun()
        st.caption("Cada fixo entra no saldo quando a data do mês chega; apagar um fixo remove também o histórico gerado por ele.")