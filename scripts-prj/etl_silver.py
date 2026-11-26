import json
import boto3
import pandas as pd

from io import BytesIO
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError


REGIAO_AWS = "us-east-1"

BUCKET_BRONZE = "datalake-roadvizor-sptech"
BUCKET_SILVER = "datalake-roadvizor-sptech"

PREFIXO_BRONZE = "raw/"
PREFIXO_SILVER = "trusted/"

SEPARADOR = ";"

s3 = boto3.client("s3", region_name=REGIAO_AWS)


# Nomes alternativos que podem chegar do Client V2
ALIASES = {
    "mac": [
        "mac_address",
        "mac_adress"
    ],
    "cpu": [
        "cpu_pct",
        "cpu_percent",
        "cpu_percentual",
        "metricas_psutil.cpu_percent()"
    ],
    "ram": [
        "ram_pct",
        "ram_percent",
        "ram_percentual",
        "metricas_psutil.virtual_memory()_percent"
    ],
    "rede_bytes_recv": [
        "bytes_recv",
        "bytes_received",
        "metricas_psutil.net_io_counters()_bytes_recv",
        "rede_interface_eth0_bytes_recv",
        "rede_io_bytes_recv"
    ],
    "rede_bytes_sent": [
        "bytes_sent",
        "bytes_sended",
        "metricas_psutil.net_io_counters()_bytes_sent",
        "rede_interface_eth0_bytes_sent",
        "rede_io_bytes_sent"
    ]
}


COLUNAS_TEXTO = [
    "timestamp",
    "mac",
    "hostname",
    "so_sistema",
    "arquitetura",
    "tipo_dia",
    "feriado",
    "processos",
    "status",
    "rede_interface_eth0_isup",
    "rede_interface_eth0_duplex"
]


TIPOS_DIA_VALIDOS = [
    "DIA_UTIL",
    "FIM_DE_SEMANA",
    "FERIADO"
]


def buscar_csv_mais_recente():

    arquivos = []

    paginador = s3.get_paginator("list_objects_v2")

    paginas = paginador.paginate(
        Bucket=BUCKET_BRONZE,
        Prefix=PREFIXO_BRONZE
    )

    for pagina in paginas:

        for arquivo in pagina.get("Contents", []):

            chave = arquivo["Key"]

            if chave.lower().endswith(".csv"):
                arquivos.append(arquivo)

    if not arquivos:
        raise FileNotFoundError(
            "Nenhum CSV de infraestrutura encontrado na Bronze."
        )

    arquivo_mais_recente = max(
        arquivos,
        key=lambda arquivo: arquivo["LastModified"]
    )

    return arquivo_mais_recente["Key"]


def ler_csv_bronze(chave):

    resposta = s3.get_object(
        Bucket=BUCKET_BRONZE,
        Key=chave
    )

    conteudo = resposta["Body"].read()

    df = pd.read_csv(
        BytesIO(conteudo),
        sep=SEPARADOR,
        encoding="utf-8-sig",
        dtype={"mac": str}
    )

    print("Arquivo lido:", chave)
    print("Registros encontrados:", len(df))

    if df.empty:
        raise ValueError("O CSV da Bronze está vazio.")

    return df


def padronizar_colunas(df):

    df = df.copy()

    df.columns = [
        coluna.strip().lower()
        for coluna in df.columns
    ]

    for destino, alternativas in ALIASES.items():

        if destino not in df.columns:

            for origem in alternativas:

                if origem in df.columns:

                    df.rename(
                        columns={origem: destino},
                        inplace=True
                    )

                    break

    obrigatorias = [
        "timestamp",
        "mac",
        "cpu",
        "ram"
    ]

    for coluna in obrigatorias:

        if coluna not in df.columns:

            raise ValueError(
                "Coluna obrigatória ausente: " + coluna
            )

    return df


def validar_colunas(df):

    print("Validando estrutura do CSV...")

    print("Total de colunas:", len(df.columns))

    print(
        "Colunas de rede:",
        [
            coluna
            for coluna in df.columns
            if "rede" in coluna or "bytes_recv" in coluna
            or "bytes_sent" in coluna
        ]
    )


def tratar_tipos_dados(df):

    df = df.copy()

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        errors="coerce"
    )

    df["mac"] = (
        df["mac"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    if "hostname" in df.columns:

        df["hostname"] = (
            df["hostname"]
            .astype("string")
            .str.strip()
        )

    if "tipo_dia" in df.columns:

        df["tipo_dia"] = (
            df["tipo_dia"]
            .astype("string")
            .str.strip()
            .str.upper()
        )

    if "feriado" in df.columns:

        df["feriado"] = (
            df["feriado"]
            .fillna("")
            .astype("string")
        )

    # Converte CPU, RAM e rede para números.
    colunas_metricas = [
        "cpu",
        "ram",
        "rede_bytes_recv",
        "rede_bytes_sent"
    ]

    for coluna in colunas_metricas:

        if coluna in df.columns:

            df[coluna] = pd.to_numeric(
                df[coluna],
                errors="coerce"
            )

    # Converte as demais métricas numéricas.
    for coluna in df.columns:

        if coluna in COLUNAS_TEXTO:
            continue

        serie = df[coluna]

        if pd.api.types.is_numeric_dtype(serie):
            continue

        convertida = pd.to_numeric(
            serie,
            errors="coerce"
        )

        if convertida.notna().sum() == serie.notna().sum():

            df[coluna] = convertida

    return df


def identificar_percentuais(df):

    candidatas = [
        coluna
        for coluna in df.columns
        if coluna.endswith("_percent")
        or coluna.endswith("_pct")
    ]

    candidatas += [
        "cpu",
        "ram"
    ]

    return [
        coluna
        for coluna in candidatas
        if coluna in df.columns
        and pd.api.types.is_numeric_dtype(df[coluna])
    ]


def remover_registros_invalidos(df):

    quantidade_inicial = len(df)

    df = df.copy()

    df["mac"] = df["mac"].replace("", pd.NA)

    df = df.dropna(
        subset=[
            "timestamp",
            "mac",
            "cpu",
            "ram"
        ]
    ).copy()

    if "tipo_dia" in df.columns:

        df = df[
            df["tipo_dia"].isin(TIPOS_DIA_VALIDOS)
        ].copy()

    # Percentuais precisam estar entre 0 e 100.
    for coluna in identificar_percentuais(df):

        invalido = (
            df[coluna].notna()
            & ~df[coluna].between(0, 100)
        )

        df.loc[invalido, coluna] = float("nan")

    # Métricas numéricas não podem ser negativas.
    colunas_numericas = df.select_dtypes(
        include="number"
    ).columns

    for coluna in colunas_numericas:

        invalido = (
            df[coluna].notna()
            & (df[coluna] < 0)
        )

        df.loc[invalido, coluna] = float("nan")

    # Valores usados não podem ultrapassar os totais.
    for coluna in df.columns:

        if not coluna.endswith("_total"):
            continue

        coluna_usada = (
            coluna[:-6] + "_used"
        )

        if coluna_usada not in df.columns:
            continue

        invalido = (
            df[coluna].notna()
            & df[coluna_usada].notna()
            & (df[coluna_usada] > df[coluna])
        )

        df.loc[invalido, coluna_usada] = float("nan")

    # Mantém uma leitura por servidor e instante.
    df = df.drop_duplicates(
        subset=["mac", "timestamp"],
        keep="last"
    )

    df = df.sort_values(
        by=["mac", "timestamp"]
    ).copy()

    quantidade_final = len(df)

    print("Registros removidos:", quantidade_inicial - quantidade_final)
    print("Registros válidos:", quantidade_final)

    if df.empty:
        raise ValueError(
            "Nenhum registro válido restou após o tratamento."
        )

    return df


def interpretar_processos(valor):

    if not isinstance(valor, str) or not valor.strip():
        return {}

    try:

        dados = json.loads(valor)

    except json.JSONDecodeError:

        return {}

    return dados if isinstance(dados, dict) else {}


def criar_colunas_processos(df):

    if "processos" not in df.columns:
        return df

    linhas = []

    for valor in df["processos"]:

        dados = interpretar_processos(valor)

        top_cpu = dados.get("top_cpu") or {}
        top_ram = dados.get("top_ram") or {}

        if not isinstance(top_cpu, dict):
            top_cpu = {}

        if not isinstance(top_ram, dict):
            top_ram = {}

        linhas.append({

            "proc_totais": dados.get("total"),

            "proc_rodando": dados.get("rodando"),

            "proc_cpu_top_nome": top_cpu.get("nome"),

            "proc_cpu_top_pid": top_cpu.get("pid"),

            "proc_cpu_top_pct": top_cpu.get("cpu_pct"),

            "proc_cpu_top_threads": top_cpu.get("threads"),

            "proc_ram_top_nome": top_ram.get("nome"),

            "proc_ram_top_pid": top_ram.get("pid"),

            "proc_ram_top_mb": top_ram.get("ram_mb"),

            "proc_ram_top_pct": top_ram.get("ram_pct"),

            "proc_top_lista": json.dumps(
                dados.get("top_processos_cpu", []),
                ensure_ascii=False
            )

        })

    detalhes = pd.DataFrame(
        linhas,
        index=df.index
    )

    colunas_numericas = [
        "proc_totais",
        "proc_rodando",
        "proc_cpu_top_pid",
        "proc_cpu_top_pct",
        "proc_cpu_top_threads",
        "proc_ram_top_pid",
        "proc_ram_top_mb",
        "proc_ram_top_pct"
    ]

    for coluna in colunas_numericas:

        detalhes[coluna] = pd.to_numeric(
            detalhes[coluna],
            errors="coerce"
        )

    df = df.drop(
        columns=["processos"]
    )

    return pd.concat(
        [df, detalhes],
        axis=1
    )


def criar_colunas_derivadas(df):

    df = df.copy()

    df["data"] = df["timestamp"].dt.strftime(
        "%Y-%m-%d"
    )

    df["hora"] = df["timestamp"].dt.strftime(
        "%H:%M:%S"
    )

    dias_semana = {
        0: "SEGUNDA",
        1: "TERCA",
        2: "QUARTA",
        3: "QUINTA",
        4: "SEXTA",
        5: "SABADO",
        6: "DOMINGO"
    }

    df["dia_semana"] = (
        df["timestamp"]
        .dt.day_of_week
        .map(dias_semana)
    )

    df["fim_de_semana"] = (
        df["timestamp"].dt.day_of_week >= 5
    )

    if "tipo_dia" in df.columns:

        df["eh_feriado"] = (
            df["tipo_dia"] == "FERIADO"
        )

    # Cria classificações para os percentuais.
    for coluna in identificar_percentuais(df):

        nome_faixa = coluna + "_faixa"

        df[nome_faixa] = pd.cut(
            df[coluna],
            bins=[-0.001, 50, 75, 90, 100],
            labels=[
                "BAIXA",
                "MODERADA",
                "ALTA",
                "CRITICA"
            ]
        )

    return df


def organizar_colunas(df):

    colunas_inicio = [
        "mac",
        "hostname",
        "so_sistema",
        "arquitetura",
        "timestamp",
        "data",
        "hora",
        "dia_semana",
        "tipo_dia",
        "feriado",
        "fim_de_semana",
        "eh_feriado",
        "cpu",
        "ram",
        "rede_bytes_recv",
        "rede_bytes_sent"
    ]

    colunas_processos = [
        coluna
        for coluna in df.columns
        if coluna.startswith("proc_")
    ]

    inicio = [
        coluna
        for coluna in colunas_inicio
        if coluna in df.columns
    ]

    meio = [
        coluna
        for coluna in df.columns
        if coluna not in inicio
        and coluna not in colunas_processos
    ]

    return df[
        inicio + meio + colunas_processos
    ]


def salvar_csv_silver(df, chave_origem):

    nome_arquivo = chave_origem.split("/")[-1]

    chave_destino = (
        PREFIXO_SILVER + nome_arquivo
    )

    conteudo_csv = df.to_csv(
        index=False,
        sep=SEPARADOR,
        encoding="utf-8",
        na_rep=""
    )

    s3.put_object(
        Bucket=BUCKET_SILVER,
        Key=chave_destino,
        Body=conteudo_csv.encode("utf-8"),
        ContentType="text/csv; charset=utf-8"
    )

    print("Arquivo salvo na Silver:", chave_destino)
    print("Total de registros:", len(df))
    print("Total de colunas:", len(df.columns))


def main():

    print("Iniciando ETL Silver RoadVizor...")

    print("Lendo dados de infraestrutura...")

    chave_origem = buscar_csv_mais_recente()

    df = ler_csv_bronze(chave_origem)

    df = padronizar_colunas(df)

    validar_colunas(df)

    df = tratar_tipos_dados(df)

    df = remover_registros_invalidos(df)

    df = criar_colunas_processos(df)

    df = criar_colunas_derivadas(df)

    df = organizar_colunas(df)

    salvar_csv_silver(df, chave_origem)

    print("\nETL Silver RoadVizor finalizada!")


if __name__ == "__main__":
    main()
