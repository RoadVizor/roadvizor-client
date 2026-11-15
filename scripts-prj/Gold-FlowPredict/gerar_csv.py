"""
Gerador de dados simulados para testar a ETL Gold (FlowPredict).

NÃO faz parte da pipeline: é uma ferramenta de teste. Gera, para servidores
FICTÍCIOS, CSVs no mesmo formato que a ETL Silver produz, mais um SQL para
cadastrar esses servidores no banco. Nenhum dado real é alterado.

Cenários (um servidor para cada):
    gradual     CPU sobe devagar e constante; RAM estável
    estavel     CPU e RAM sem tendência
    oscilante   ciclo diário (pico à tarde, vale de madrugada), sem tendência
    acentuado   CPU acelera e RAM sobe rápido: condição crítica

Saída (pasta dados_teste/):
    trusted/<MAC>/roadvizor_AAAAMMDD_HHMMSS.csv   um arquivo por dia (histórico
                                                  em vários arquivos, como no Silver)
    cadastro_servidores_teste.sql                 INSERTs (e DELETEs comentados)

Uso:
    python gerar_csv_teste.py
    aws s3 sync dados_teste/trusted s3://datalake-silver-roadvizor-sptech/trusted/

Observação: as leituras são geradas a cada 60 s (o client real coleta a cada
~2 s). Como a Gold agrupa tudo em médias de 15 min, o resultado é equivalente
e os arquivos ficam leves. Colunas de apoio (ram_active, ram_cached, processos
etc.) são aproximações: a Gold só usa `timestamp`, `cpu` e `ram_used`.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

DIAS_HISTORICO = 7
INTERVALO_SEGUNDOS = 60
PASTA_SAIDA = Path("dados_teste")
SEMENTE = 42                      # mesma semente = mesmos dados (reprodutível)

# Capacidade e limites cadastrados no banco (iguais para todos os servidores de teste).
# A RAM física simulada é igual à capacidade cadastrada, para o % bater com o banco.
RAM_TOTAL_GIB = 16
LIMITE_CPU_PCT = 70
LIMITE_RAM_GIB = 12

EMPRESA_ID = 1
ENDERECO_ID = 1

# Mesmas colunas e ordem do CSV produzido pela ETL Silver.
COLUNAS_SILVER = [
    "mac", "hostname", "so_sistema", "arquitetura", "cpu_fisicos", "cpu_logicos",
    "timestamp", "data", "hora", "dia_semana", "tipo_dia", "feriado",
    "fim_de_semana", "eh_feriado", "cpu", "ram_total", "ram_available",
    "ram_percent", "ram_used", "ram_free", "ram_active", "ram_inactive",
    "ram_buffers", "ram_cached", "ram_shared", "ram_slab", "ram_faixa",
    "cpu_faixa", "proc_totais", "proc_rodando", "proc_cpu_top_nome",
    "proc_cpu_top_pid", "proc_cpu_top_pct", "proc_cpu_top_threads",
    "proc_ram_top_nome", "proc_ram_top_pid", "proc_ram_top_mb",
    "proc_ram_top_pct", "proc_top_lista",
]

DIAS_SEMANA = {0: "SEGUNDA", 1: "TERCA", 2: "QUARTA", 3: "QUINTA",
               4: "SEXTA", 5: "SABADO", 6: "DOMINGO"}


# ---------------------------------------------------------------------------
# Cenários
# Cada função recebe:
#   h         horas desde o início do histórico (array)
#   hora_dia  hora do dia, 0 a 24 (array), para padrões diários
#   rng       gerador aleatório com semente fixa
# e devolve a série (CPU em %, RAM em GiB).
# ---------------------------------------------------------------------------

def ruido(rng, n, desvio):
    return rng.normal(0, desvio, n)


CENARIOS = [
    {
        "mac": "AA0000000001",
        "apelido": "Teste - Crescimento gradual",
        "hostname": "teste-gradual",
        "descricao": "CPU sobe ~0,2 ponto/h; RAM estável",
        "cpu": lambda h, d, rng: 33 + 0.2 * h + ruido(rng, len(h), 2),
        "ram_gib": lambda h, d, rng: 7 + ruido(rng, len(h), 0.1),
    },
    {
        "mac": "AA0000000002",
        "apelido": "Teste - Estavel",
        "hostname": "teste-estavel",
        "descricao": "CPU ~35% e RAM ~6 GiB, sem tendência",
        "cpu": lambda h, d, rng: 35 + ruido(rng, len(h), 2),
        "ram_gib": lambda h, d, rng: 6 + ruido(rng, len(h), 0.1),
    },
    {
        "mac": "AA0000000003",
        "apelido": "Teste - Oscilante",
        "hostname": "teste-oscilante",
        "descricao": "ciclo diário (pico ~14h), sem tendência de longo prazo",
        "cpu": lambda h, d, rng: 45 + 18 * np.sin(2 * np.pi * (d - 8) / 24) + ruido(rng, len(h), 2),
        "ram_gib": lambda h, d, rng: 7 + 0.8 * np.sin(2 * np.pi * (d - 8) / 24) + ruido(rng, len(h), 0.1),
    },
    {
        "mac": "AA0000000004",
        "apelido": "Teste - Crescimento acentuado",
        "hostname": "teste-acentuado",
        "descricao": "CPU acelerando e RAM subindo rápido: condição crítica",
        "cpu": lambda h, d, rng: 25 + 0.0016 * h ** 2 + ruido(rng, len(h), 2),
        "ram_gib": lambda h, d, rng: 5 + 0.04 * h + ruido(rng, len(h), 0.1),
    },
]


# ---------------------------------------------------------------------------
# Geração dos CSVs
# ---------------------------------------------------------------------------

def faixa(valores):
    """Mesmas faixas do ETL Silver (BAIXA, MODERADA, ALTA, CRITICA)."""
    return pd.cut(
        valores,
        bins=[-0.001, 50, 75, 90, 100],
        labels=["BAIXA", "MODERADA", "ALTA", "CRITICA"],
    )


def gerar_leituras(cenario, fim, rng):
    """DataFrame com as leituras de um servidor, no formato do Silver."""
    n = DIAS_HISTORICO * 24 * 3600 // INTERVALO_SEGUNDOS + 1

    ts = pd.date_range(end=fim, periods=n, freq=f"{INTERVALO_SEGUNDOS}s")
    h = ((ts - ts[0]).total_seconds() / 3600).to_numpy()
    hora_dia = (ts.hour + ts.minute / 60).to_numpy()

    cpu = np.clip(cenario["cpu"](h, hora_dia, rng), 0, 100).round(1)

    ram_total = RAM_TOTAL_GIB * 1024 ** 3
    ram_gib = np.clip(cenario["ram_gib"](h, hora_dia, rng), 0.5, RAM_TOTAL_GIB * 0.98)
    ram_used = (ram_gib * 1024 ** 3).astype("int64")

    ram_percent = (ram_used / ram_total * 100).round(1)
    ram_livre = ram_total - ram_used

    dia = ts.dayofweek
    tipo_dia = np.where(dia >= 5, "FIM_DE_SEMANA", "DIA_UTIL")

    return pd.DataFrame({
        "mac": cenario["mac"],
        "hostname": cenario["hostname"],
        "so_sistema": "Linux 6.8.0-simulado",
        "arquitetura": "x86_64",
        "cpu_fisicos": 4,
        "cpu_logicos": 8,
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "data": ts.strftime("%Y-%m-%d"),
        "hora": ts.strftime("%H:%M:%S"),
        "dia_semana": pd.Series(dia).map(DIAS_SEMANA).to_numpy(),
        "tipo_dia": tipo_dia,
        "feriado": "",
        "fim_de_semana": dia >= 5,
        "eh_feriado": False,
        "cpu": cpu,
        "ram_total": ram_total,
        "ram_available": ram_livre,
        "ram_percent": ram_percent,
        "ram_used": ram_used,
        "ram_free": ram_livre,
        "ram_active": (ram_used * 0.7).astype("int64"),
        "ram_inactive": (ram_used * 0.45).astype("int64"),
        "ram_buffers": int(ram_total * 0.02),
        "ram_cached": (ram_used * 0.25).astype("int64"),
        "ram_shared": (ram_used * 0.1).astype("int64"),
        "ram_slab": int(ram_total * 0.05),
        "ram_faixa": faixa(ram_percent),
        "cpu_faixa": faixa(cpu),
        "proc_totais": 300,
        "proc_rodando": 2,
        "proc_cpu_top_nome": "python",
        "proc_cpu_top_pid": 1234,
        "proc_cpu_top_pct": (cpu * 0.8).round(1),
        "proc_cpu_top_threads": 1,
        "proc_ram_top_nome": "java",
        "proc_ram_top_pid": 4321,
        "proc_ram_top_mb": (ram_used / 1024 ** 2 * 0.3).round(2),
        "proc_ram_top_pct": (ram_percent * 0.3).round(2),
        "proc_top_lista": json.dumps([]),
    })[COLUNAS_SILVER]


def salvar_csvs(df, mac):
    """Um CSV por dia em trusted/<MAC>/, como o histórico real do Silver."""
    pasta = PASTA_SAIDA / "trusted" / mac
    pasta.mkdir(parents=True, exist_ok=True)

    arquivos = 0
    for data, parte in df.groupby("data"):
        ultimo = pd.Timestamp(parte["timestamp"].iloc[-1])
        nome = f"roadvizor_{data.replace('-', '')}_{ultimo:%H%M%S}.csv"
        parte.to_csv(pasta / nome, index=False, sep=";")
        arquivos += 1

    return arquivos


# ---------------------------------------------------------------------------
# SQL de cadastro
# ---------------------------------------------------------------------------

def gerar_sql():
    """INSERTs dos servidores de teste (re-executável) e DELETEs para limpar."""
    macs = ", ".join(f"'{c['mac']}'" for c in CENARIOS)

    linhas = [
        "-- Servidores SIMULADOS para testar a ETL Gold (gerado por gerar_csv_teste.py)",
        "USE roadvizor;",
        "",
    ]

    for c in CENARIOS:
        linhas += [
            f"-- {c['apelido']}: {c['descricao']}",
            "INSERT INTO servidor (empresa_id, endereco_id, endereco_mac, apelido, modelo)",
            f"VALUES ({EMPRESA_ID}, {ENDERECO_ID}, '{c['mac']}', '{c['apelido']}', 'SIMULADO')",
            "ON DUPLICATE KEY UPDATE apelido = VALUES(apelido);",
            "",
        ]

    linhas += [
        "-- Limites: CPU em pct (limite/capacidade) e RAM em GiB (limite/capacidade)",
        "INSERT INTO servidor_componente",
        "    (servidor_id, empresa_id, componente_id, limite_min, limite_max, capacidade_total, monitorado)",
        "SELECT s.id_servidor, s.empresa_id, c.id_componente, 0,",
        f"    CASE WHEN c.nome = 'cpu' THEN {LIMITE_CPU_PCT} ELSE {LIMITE_RAM_GIB} END,",
        f"    CASE WHEN c.nome = 'cpu' THEN 100 ELSE {RAM_TOTAL_GIB} END,",
        "    1",
        "FROM servidor s",
        "CROSS JOIN componente c",
        f"WHERE s.endereco_mac IN ({macs})",
        f"  AND s.empresa_id = {EMPRESA_ID}",
        "  AND c.nome IN ('cpu', 'ram')",
        "ON DUPLICATE KEY UPDATE",
        "    limite_min = VALUES(limite_min),",
        "    limite_max = VALUES(limite_max),",
        "    capacidade_total = VALUES(capacidade_total),",
        "    monitorado = 1;",
        "",
        "-- Para remover os servidores de teste depois (descomente):",
        "-- DELETE FROM servidor_componente WHERE servidor_id IN",
        f"--     (SELECT id_servidor FROM servidor WHERE endereco_mac IN ({macs}));",
        f"-- DELETE FROM servidor WHERE endereco_mac IN ({macs});",
        "",
    ]

    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------

def main():
    rng = np.random.default_rng(SEMENTE)
    fim = pd.Timestamp.now().floor("min")

    for cenario in CENARIOS:
        df = gerar_leituras(cenario, fim, rng)
        arquivos = salvar_csvs(df, cenario["mac"])

        print(f"{cenario['mac']}  {cenario['apelido']}: "
              f"{len(df)} leituras em {arquivos} arquivos ({cenario['descricao']})")

    PASTA_SAIDA.mkdir(exist_ok=True)
    (PASTA_SAIDA / "cadastro_servidores_teste.sql").write_text(gerar_sql(), encoding="utf-8")

    print(f"\nArquivos em: {PASTA_SAIDA.resolve()}")


if __name__ == "__main__":
    main()