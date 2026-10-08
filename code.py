"""Controle de despesas com gráficos (matplotlib).

Requisitos: pip install matplotlib
Dados guardados em despesas.json na mesma pasta.
"""
import json
import os
from collections import defaultdict
from datetime import datetime

import matplotlib.pyplot as plt

ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "despesas.json")


def carregar():
    if os.path.exists(ARQUIVO):
        with open(ARQUIVO, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def salvar(despesas):
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(despesas, f, ensure_ascii=False, indent=2)


def ler_valor(texto):
    while True:
        try:
            v = float(input(texto).replace(",", "."))
            if v > 0:
                return v
        except ValueError:
            pass
        print("Valor inválido.")


def ler_data(texto):
    while True:
        s = input(texto).strip()
        if not s:
            return datetime.now().strftime("%Y-%m-%d")
        try:
            return datetime.strptime(s, "%d/%m/%Y").strftime("%Y-%m-%d")
        except ValueError:
            print("Use o formato DD/MM/AAAA ou deixe vazio para hoje.")


def adicionar(despesas):
    descricao = input("Descrição: ").strip()
    categoria = input("Categoria (ex: Alimentação, Transporte): ").strip().title() or "Outros"
    valor = ler_valor("Valor: ")
    data = ler_data("Data (DD/MM/AAAA, vazio = hoje): ")
    despesas.append({"data": data, "descricao": descricao, "categoria": categoria, "valor": valor})
    salvar(despesas)
    print("Despesa adicionada.")


def listar(despesas):
    if not despesas:
        print("Nenhuma despesa registada.")
        return
    print(f"\n{'#':<4}{'Data':<12}{'Categoria':<16}{'Descrição':<24}{'Valor':>10}")
    print("-" * 66)
    for i, d in enumerate(sorted(despesas, key=lambda x: x["data"]), 1):
        data = datetime.strptime(d["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
        print(f"{i:<4}{data:<12}{d['categoria']:<16}{d['descricao'][:22]:<24}{d['valor']:>10.2f}")
    print("-" * 66)
    print(f"{'TOTAL':<56}{sum(d['valor'] for d in despesas):>10.2f}\n")


def remover(despesas):
    ordenadas = sorted(despesas, key=lambda x: x["data"])
    listar(ordenadas)
    try:
        n = int(input("Número da despesa a remover (0 = cancelar): "))
    except ValueError:
        return
    if 1 <= n <= len(ordenadas):
        despesas.remove(ordenadas[n - 1])
        salvar(despesas)
        print("Removida.")


def grafico(despesas):
    if not despesas:
        print("Sem dados para o gráfico.")
        return

    por_categoria = defaultdict(float)
    por_mes = defaultdict(float)
    for d in despesas:
        por_categoria[d["categoria"]] += d["valor"]
        por_mes[d["data"][:7]] += d["valor"]

    meses = sorted(por_mes)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    ax1.pie(
        por_categoria.values(),
        labels=por_categoria.keys(),
        autopct="%1.1f%%",
        startangle=90,
    )
    ax1.set_title("Despesas por categoria")

    barras = ax2.bar(meses, [por_mes[m] for m in meses], color="#4C78A8")
    ax2.bar_label(barras, fmt="%.0f")
    ax2.set_title("Despesas por mês")
    ax2.set_xlabel("Mês")
    ax2.set_ylabel("Valor")
    plt.setp(ax2.get_xticklabels(), rotation=45, ha="right")

    fig.suptitle(f"Total gasto: {sum(por_categoria.values()):.2f}")
    fig.tight_layout()
    plt.show()


def menu():
    despesas = carregar()
    opcoes = {
        "1": ("Adicionar despesa", adicionar),
        "2": ("Listar despesas", listar),
        "3": ("Ver gráficos", grafico),
        "4": ("Remover despesa", remover),
    }
    while True:
        print("\n=== CONTROLE DE DESPESAS ===")
        for k, (nome, _) in opcoes.items():
            print(f"{k} - {nome}")
        print("0 - Sair")
        op = input("Escolha: ").strip()
        if op == "0":
            break
        if op in opcoes:
            opcoes[op][1](despesas)
        else:
            print("Opção inválida.")


if __name__ == "__main__":
    menu()