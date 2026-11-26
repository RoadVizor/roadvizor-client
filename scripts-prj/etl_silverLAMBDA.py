import json
from io import BytesIO
from urllib.parse import unquote_plus

import boto3
import pandas as pd


# CONFIGURAÇÕES AWS

REGIAO_AWS = "us-east-1"

BUCKET_BRONZE = "datalake-roadvizor-sptech"
BUCKET_SILVER = "datalake-roadvizor-sptech"

PREFIXO_BRONZE = "raw/"
PREFIXO_SILVER = "trusted/"

SEPARADOR_BRONZE = ";"
SEPARADOR_SILVER = ";"

s3 = boto3.client("s3", region_name=REGIAO_AWS)


# CONFIGURAÇÕES DAS COLUNAS

COLUNAS_FIXAS = [
    "timestamp",
    "mac",
    "hostname",
    "so_sistema",
    "arquitetura",
    "cpu_fisicos",
    "cpu_logicos",
    "tipo_dia",
    "feriado",
    "processos",
]

COLUNAS_TEXTO = [
    "timestamp",
    "mac",
    "hostname",
    "so_sistema",
    "arquitetura",
    "tipo_dia",
    "feriado",
    "processos",
    "clima_coletado_em",
    "clima_condicao_climatica",
    "clima_fonte_dados",
    "status",
]

COLUNAS_NUMERICAS_CLIMA = [
    "clima_temperatura",
    "clima_umidade",
    "clima_velocidade_vento",
    "clima_precipitacao",
]

COLUNAS_PERCENTUAIS_SIMPLES = [
    "cpu",
    "clima_umidade",
]

TIPOS_DIA_VALIDOS = {
    "DIA_UTIL",
    "FIM_DE_SEMANA",
    "FERIADO"
}


# 1. LEITURA DA BRONZE

def ler_csv_bronze(bucket, chave):
    """Lê o arquivo CSV recebido pelo evento S3."""

    resposta = s3.get_object(
        Bucket=bucket,
        Key=chave
    )

    conteudo = resposta["Body"].read()

    df = pd.read_csv(
        BytesIO(conteudo),
        sep=SEPARADOR_BRONZE,
        encoding="utf-8",
        dtype={"mac": str}
    )

    print(f"Arquivo lido: {chave}")
    print(f"Registros encontrados: {len(df)}")

    return df


# 2. VALIDAÇÃO DAS COLUNAS

def validar_colunas(df):
    """Verifica se o CSV possui as colunas obrigatórias."""

    colunas_ausentes = [
        coluna
        for coluna in COLUNAS_FIXAS
        if coluna not in df.columns
    ]

    if colunas_ausentes:
        raise ValueError(
            "O CSV não possui todas as colunas necessárias: "
            + ", ".join(colunas_ausentes)
        )

    colunas_componentes = [
        coluna
        for coluna in df.columns
        if coluna not in COLUNAS_FIXAS
    ]

    print(
        f"Colunas adicionais encontradas: {len(colunas_componentes)}"
    )

    print(
        "Colunas climáticas encontradas:",
        [
            coluna
            for coluna in df.columns
            if coluna.startswith("clima_")
        ]
    )


# 3. TRATAMENTO DOS TIPOS DE DADOS

def tratar_tipos_dados(df):
    """Converte as colunas para os tipos adequados."""

    df = df.copy()

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        errors="coerce"
    )

    if "clima_coletado_em" in df.columns:
        df["clima_coletado_em"] = pd.to_datetime(
            df["clima_coletado_em"],
            errors="coerce"
        )

    df["mac"] = (
        df["mac"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    df["tipo_dia"] = (
        df["tipo_dia"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    df["feriado"] = (
        df["feriado"]
        .fillna("")
        .astype("string")
    )

    # Métricas climáticas.

    for coluna in COLUNAS_NUMERICAS_CLIMA:

        if coluna in df.columns:

            df[coluna] = pd.to_numeric(
                df[coluna],
                errors="coerce"
            )

    # Colunas dinâmicas dos componentes.

    for coluna in df.columns:

        if coluna in COLUNAS_TEXTO:
            continue

        if coluna in COLUNAS_NUMERICAS_CLIMA:
            continue

        serie = df[coluna]

        if (
            pd.api.types.is_numeric_dtype(serie)
            or pd.api.types.is_bool_dtype(serie)
        ):
            continue

        convertida = pd.to_numeric(
            serie,
            errors="coerce"
        )

        if convertida.notna().sum() == serie.notna().sum():
            df[coluna] = convertida

    return df


# 4. IDENTIFICAÇÃO DAS COLUNAS PERCENTUAIS

def colunas_percentuais(df):
    """Identifica as colunas que representam percentuais."""

    candidatas = [
        coluna
        for coluna in df.columns
        if coluna.endswith(("_percent", "_pct"))
    ]

    candidatas += [
        coluna
        for coluna in COLUNAS_PERCENTUAIS_SIMPLES
        if coluna in df.columns
    ]

    return [
        coluna
        for coluna in candidatas
        if pd.api.types.is_numeric_dtype(df[coluna])
    ]


# 5. REMOÇÃO DE REGISTROS INVÁLIDOS

def remover_registros_invalidos(df):
    """Trata inconsistências sem descartar leituras válidas."""

    quantidade_inicial = len(df)

    df = df.copy()

    # Timestamp, MAC e tipo de dia são obrigatórios.

    df["mac"] = df["mac"].replace("", pd.NA)

    df = df.dropna(
        subset=["timestamp", "mac", "tipo_dia"]
    ).copy()

    df = df[
        df["tipo_dia"].isin(TIPOS_DIA_VALIDOS)
    ].copy()

    # Percentuais devem estar entre 0 e 100.

    for coluna in colunas_percentuais(df):

        invalido = (
            df[coluna].notna()
            & ~df[coluna].between(0, 100)
        )

        df.loc[invalido, coluna] = float("nan")

    # Métricas numéricas não podem ser negativas.
    # Temperatura é uma exceção.

    colunas_numericas = df.select_dtypes(
        include="number"
    ).columns

    for coluna in colunas_numericas:

        if coluna == "clima_temperatura":
            continue

        invalido = (
            df[coluna].notna()
            & (df[coluna] < 0)
        )

        df.loc[invalido, coluna] = float("nan")

    # Valores utilizados não podem ultrapassar o total.

    for coluna in list(df.columns):

        if not coluna.endswith("_total"):
            continue

        coluna_usada = (
            coluna[:-len("_total")] + "_used"
        )

        if coluna_usada not in df.columns:
            continue

        invalido = (
            df[coluna].notna()
            & df[coluna_usada].notna()
            & (df[coluna_usada] > df[coluna])
        )

        df.loc[invalido, coluna_usada] = float("nan")

    # Remove duplicidades por máquina e instante.

    df = df.drop_duplicates(
        subset=["mac", "timestamp"],
        keep="last"
    )

    quantidade_final = len(df)

    removidos = quantidade_inicial - quantidade_final

    print(f"Registros removidos: {removidos}")
    print(f"Registros válidos: {quantidade_final}")

    if df.empty:
        raise ValueError(
            "Nenhum registro válido restou após o tratamento."
        )

    return df


# 6. INTERPRETAÇÃO DOS PROCESSOS

def interpretar_processos(valor):
    """Converte o JSON dos processos em um dicionário."""

    if not isinstance(valor, str) or not valor.strip():
        return {}

    try:
        dados = json.loads(valor)

    except json.JSONDecodeError:
        return {}

    return dados if isinstance(dados, dict) else {}


# 7. CRIAÇÃO DAS COLUNAS DOS PROCESSOS

def criar_colunas_processos(df):
    """Separa as informações dos processos em colunas."""

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
            ),

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
        "proc_ram_top_pct",
    ]

    for coluna in colunas_numericas:

        detalhes[coluna] = pd.to_numeric(
            detalhes[coluna],
            errors="coerce"
        )

    df = df.drop(columns=["processos"])

    return pd.concat(
        [df, detalhes],
        axis=1
    )


# 8. CRIAÇÃO DAS COLUNAS DERIVADAS

def criar_colunas_derivadas(df):
    """Cria colunas úteis para análises posteriores."""

    df["data"] = df["timestamp"].dt.strftime("%Y-%m-%d")

    df["hora"] = df["timestamp"].dt.strftime("%H:%M:%S")

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
        df["timestamp"].dt.day_of_week.map(dias_semana)
    )

    df["fim_de_semana"] = (
        df["timestamp"].dt.day_of_week >= 5
    )

    df["eh_feriado"] = (
        df["tipo_dia"] == "FERIADO"
    )

    # Faixas de utilização.

    for coluna in colunas_percentuais(df):

        nome_faixa = (
            coluna.removesuffix("_percent")
            .removesuffix("_pct")
            + "_faixa"
        )

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


# 9. ORGANIZAÇÃO DAS COLUNAS

def organizar_colunas(df):
    """Organiza as colunas para facilitar a leitura."""

    colunas_inicio = [
        "mac",
        "hostname",
        "so_sistema",
        "arquitetura",
        "cpu_fisicos",
        "cpu_logicos",
        "timestamp",
        "data",
        "hora",
        "dia_semana",
        "tipo_dia",
        "feriado",
        "fim_de_semana",
        "eh_feriado",
    ]

    colunas_fim = [
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
        and coluna not in colunas_fim
    ]

    return df[inicio + meio + colunas_fim]


# 10. SALVAMENTO NA SILVER

def salvar_csv_silver(df, chave_origem):
    """Salva o CSV tratado na camada Silver."""

    nome_arquivo = chave_origem.split("/")[-1]

    chave_destino = (
        f"{PREFIXO_SILVER}{nome_arquivo}"
    )

    conteudo_csv = df.to_csv(
        index=False,
        sep=SEPARADOR_SILVER,
        encoding="utf-8",
        na_rep=""
    )

    s3.put_object(
        Bucket=BUCKET_SILVER,
        Key=chave_destino,
        Body=conteudo_csv.encode("utf-8"),
        ContentType="text/csv; charset=utf-8"
    )

    print(f"Arquivo salvo na Silver: {chave_destino}")
    print(f"Total de registros: {len(df)}")
    print(f"Total de colunas: {len(df.columns)}")


# 11. HANDLER DA AWS LAMBDA

def lambda_handler(event, context):
    """Executa a ETL Silver após um novo arquivo na Bronze."""

    print("Iniciando ETL Silver...")

    registros = event.get("Records", [])

    if not registros:
        raise ValueError(
            "O evento recebido não contém registros do S3."
        )

    arquivos_processados = []

    for registro in registros:

        if registro.get("eventSource") != "aws:s3":
            print("Evento ignorado: origem diferente de S3.")
            continue

        bucket_origem = registro["s3"]["bucket"]["name"]

        chave_origem = unquote_plus(
            registro["s3"]["object"]["key"]
        )

        print(f"Bucket: {bucket_origem}")
        print(f"Arquivo recebido: {chave_origem}")

        # Verifica se o arquivo pertence à Bronze.

        if bucket_origem != BUCKET_BRONZE:
            print("Bucket diferente do configurado. Ignorando.")
            continue

        if not chave_origem.startswith(PREFIXO_BRONZE):
            print("Arquivo fora da Bronze. Ignorando.")
            continue

        if not chave_origem.lower().endswith(".csv"):
            print("Arquivo não é CSV. Ignorando.")
            continue

        # Executa a ETL.

        df = ler_csv_bronze(
            bucket_origem,
            chave_origem
        )

        validar_colunas(df)

        df = tratar_tipos_dados(df)

        df = remover_registros_invalidos(df)

        df = criar_colunas_processos(df)

        df = criar_colunas_derivadas(df)

        df = organizar_colunas(df)

        salvar_csv_silver(
            df,
            chave_origem
        )

        arquivos_processados.append(chave_origem)

        print(
            f"ETL Silver finalizada: {chave_origem}"
        )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "mensagem": "ETL Silver executada com sucesso!",
            "arquivos_processados": arquivos_processados
        }, ensure_ascii=False)
    }