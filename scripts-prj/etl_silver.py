import json
import logging
from io import BytesIO
from pathlib import PurePosixPath

import boto3
import pandas as pd


REGIAO_AWS = "us-east-1"

BUCKET_BRONZE = "datalake-bronze-roadvizor-sptech"
BUCKET_SILVER = "datalake-silver-roadvizor-sptech"

PREFIXO_BRONZE = "raw/"
PREFIXO_SILVER = "trusted/"

SEPARADOR = ";"

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
]

TIPOS_DIA_VALIDOS = {
    "DIA_UTIL",
    "FIM_DE_SEMANA",
    "FERIADO",
}

COLUNAS_PERCENTUAIS_SIMPLES = ["cpu"]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

s3 = boto3.client(
    "s3",
    region_name=REGIAO_AWS,
)


def buscar_arquivos_bronze():
    """Lista os arquivos CSV disponíveis no Bronze."""

    arquivos = []

    paginador = s3.get_paginator("list_objects_v2")

    for pagina in paginador.paginate(
        Bucket=BUCKET_BRONZE,
        Prefix=PREFIXO_BRONZE,
    ):
        for arquivo in pagina.get("Contents", []):
            chave = arquivo["Key"]

            if chave.lower().endswith(".csv"):
                arquivos.append(chave)

    arquivos.sort()

    return arquivos


def ler_csv_bronze(chave):
    """Lê um CSV armazenado no Bronze."""

    resposta = s3.get_object(
        Bucket=BUCKET_BRONZE,
        Key=chave,
    )

    conteudo = resposta["Body"].read()

    df = pd.read_csv(
        BytesIO(conteudo),
        sep=SEPARADOR,
        encoding="utf-8",
        dtype={"mac": "string"},
    )

    logger.info("Arquivo lido: %s", chave)
    logger.info("Registros encontrados: %s", len(df))

    return df


def validar_colunas(df):
    """Verifica se as colunas fixas estão presentes."""

    ausentes = [
        coluna
        for coluna in COLUNAS_FIXAS
        if coluna not in df.columns
    ]

    if ausentes:
        raise ValueError(
            "Colunas obrigatórias ausentes: "
            + ", ".join(ausentes)
        )

    componentes = [
        coluna
        for coluna in df.columns
        if coluna not in COLUNAS_FIXAS
    ]

    logger.info(
        "Componentes encontrados: %s",
        len(componentes),
    )


def tratar_tipos_dados(df):
    """Converte os dados para os tipos adequados."""

    df = df.copy()

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
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
        .astype("string")
        .fillna("")
    )

    for coluna in df.columns:
        if coluna in COLUNAS_TEXTO:
            continue

        convertida = pd.to_numeric(
            df[coluna],
            errors="coerce",
        )

        if convertida.notna().sum() == df[coluna].notna().sum():
            df[coluna] = convertida

    return df


def colunas_percentuais(df):
    """Identifica as colunas que representam percentuais."""

    candidatas = [
        coluna
        for coluna in df.columns
        if coluna.endswith("_percent")
    ]

    candidatas += [
        coluna
        for coluna in COLUNAS_PERCENTUAIS_SIMPLES
        if coluna in df.columns and coluna not in candidatas
    ]

    return [
        coluna
        for coluna in candidatas
        if pd.api.types.is_numeric_dtype(df[coluna])
    ]


def remover_registros_invalidos(df):
    """Remove registros incompletos, inválidos e duplicados."""

    quantidade_inicial = len(df)

    df = df.dropna(
        subset=["timestamp", "mac", "tipo_dia"]
    ).copy()

    df = df[
        (df["mac"].str.len() > 0)
        & (df["tipo_dia"].isin(TIPOS_DIA_VALIDOS))
    ].copy()

    for coluna in colunas_percentuais(df):
        df = df[
            df[coluna].isna()
            | df[coluna].between(0, 100)
        ].copy()

    colunas_numericas = df.select_dtypes(
        include="number"
    ).columns

    if len(colunas_numericas) > 0:
        mascara = (
            df[colunas_numericas].isna()
            | (df[colunas_numericas] >= 0)
        ).all(axis=1)

        df = df[mascara].copy()

    for coluna in df.columns:
        if not coluna.endswith("_total"):
            continue

        coluna_usada = (
            coluna[:-len("_total")] + "_used"
        )

        if coluna_usada in df.columns:
            df = df[
                df[coluna].isna()
                | df[coluna_usada].isna()
                | (df[coluna_usada] <= df[coluna])
            ].copy()

    df = df.drop_duplicates(
        subset=["mac", "timestamp"],
        keep="last",
    )

    logger.info(
        "Registros removidos: %s",
        quantidade_inicial - len(df),
    )

    logger.info(
        "Registros válidos: %s",
        len(df),
    )

    if df.empty:
        raise ValueError(
            "Nenhum registro válido restou após a validação."
        )

    return df


def interpretar_processos(valor):
    """Interpreta o JSON armazenado na coluna processos."""

    if not isinstance(valor, str) or not valor.strip():
        return {}

    try:
        dados = json.loads(valor)
    except json.JSONDecodeError:
        return {}

    return dados if isinstance(dados, dict) else {}


def criar_colunas_processos(df):
    """Transforma as informações de processos em colunas."""

    linhas = []

    for valor in df["processos"]:
        dados = interpretar_processos(valor)

        top_cpu = dados.get("top_cpu") or {}
        top_ram = dados.get("top_ram") or {}

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
                ensure_ascii=False,
            ),
        })

    detalhes = pd.DataFrame(
        linhas,
        index=df.index,
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
            errors="coerce",
        )

    df = df.drop(columns=["processos"])

    return pd.concat(
        [df, detalhes],
        axis=1,
    )


def criar_colunas_derivadas(df):
    """Cria informações complementares para análises."""

    df = df.copy()

    df["data"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    df["hora"] = df["timestamp"].dt.strftime("%H:%M:%S")

    dias_semana = {
        0: "SEGUNDA",
        1: "TERCA",
        2: "QUARTA",
        3: "QUINTA",
        4: "SEXTA",
        5: "SABADO",
        6: "DOMINGO",
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

    for coluna in colunas_percentuais(df):
        nome_faixa = (
            coluna.removesuffix("_percent") + "_faixa"
        )

        df[nome_faixa] = pd.cut(
            df[coluna],
            bins=[-0.001, 50, 75, 90, 100],
            labels=[
                "BAIXA",
                "MODERADA",
                "ALTA",
                "CRITICA",
            ],
        )

    return df


def organizar_colunas(df):
    """Organiza as colunas do arquivo tratado."""

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


def normalizar_mac(mac):
    """Prepara o MAC para ser utilizado no caminho do S3."""

    mac = str(mac).strip().upper()
    mac = mac.replace(":", "-")

    if not mac or "/" in mac or "\\" in mac:
        raise ValueError(f"MAC inválido: {mac}")

    return mac


def salvar_csv_silver(df, chave_origem):
    """Salva os dados separados por máquina na Silver."""

    nome_arquivo = PurePosixPath(chave_origem).name

    for mac, dados_servidor in df.groupby("mac", sort=True):
        mac_prefixo = normalizar_mac(mac)

        chave_destino = (
            f"{PREFIXO_SILVER}"
            f"{mac_prefixo}/"
            f"{nome_arquivo}"
        )

        conteudo = dados_servidor.to_csv(
            index=False,
            sep=SEPARADOR,
        )

        s3.put_object(
            Bucket=BUCKET_SILVER,
            Key=chave_destino,
            Body=conteudo.encode("utf-8"),
            ContentType="text/csv; charset=utf-8",
        )

        logger.info(
            "Salvo: s3://%s/%s (%s registros)",
            BUCKET_SILVER,
            chave_destino,
            len(dados_servidor),
        )


def processar_arquivo(chave_origem):
    """Executa todas as etapas do ETL em um arquivo."""

    logger.info("Iniciando: %s", chave_origem)

    df = ler_csv_bronze(chave_origem)

    validar_colunas(df)

    df = tratar_tipos_dados(df)
    df = remover_registros_invalidos(df)
    df = criar_colunas_processos(df)
    df = criar_colunas_derivadas(df)
    df = organizar_colunas(df)

    salvar_csv_silver(df, chave_origem)

    logger.info(
        "Processamento concluído: %s registros",
        len(df),
    )


def main():
    """Executa o ETL Silver localmente."""

    logger.info("Iniciando ETL Silver")

    arquivos = buscar_arquivos_bronze()

    if not arquivos:
        logger.warning("Nenhum CSV encontrado no Bronze.")
        return

    logger.info(
        "Arquivos encontrados: %s",
        len(arquivos),
    )

    for arquivo in arquivos:
        try:
            processar_arquivo(arquivo)
        except Exception:
            logger.exception(
                "Erro ao processar o arquivo: %s",
                arquivo,
            )

    logger.info("ETL Silver finalizado.")


if __name__ == "__main__":
    main()