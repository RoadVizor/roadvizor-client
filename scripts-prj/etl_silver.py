import boto3
import pandas as pd

from io import BytesIO
from datetime import datetime
from botocore.exceptions import ClientError, NoCredentialsError


REGIAO_AWS = "us-east-1"

BUCKET_BRONZE = "datalake-bronze-roadvizor-sptech"
BUCKET_SILVER = "datalake-silver-roadvizor-sptech"

PREFIXO_BRONZE = "raw/"
PREFIXO_SILVER = "trusted/"

s3 = boto3.client("s3", region_name=REGIAO_AWS)


def buscar_csv_mais_recente():
    """Localiza o CSV mais recente na camada Bronze."""

    arquivos = []
    paginador = s3.get_paginator("list_objects_v2")

    paginas = paginador.paginate(
        Bucket=BUCKET_BRONZE,
        Prefix=PREFIXO_BRONZE
    )

    for pagina in paginas:
        for arquivo in pagina.get("Contents", []):
            chave = arquivo["Key"]

            if (
                chave.lower().endswith(".csv")
                and chave.startswith(PREFIXO_BRONZE)
            ):
                arquivos.append(arquivo)

    if not arquivos:
        raise FileNotFoundError(
            "Nenhum arquivo CSV foi encontrado na camada Bronze."
        )

    arquivo_mais_recente = max(
        arquivos,
        key=lambda arquivo: arquivo["LastModified"]
    )

    return arquivo_mais_recente["Key"]


def ler_csv_bronze(chave):
    """Lê o CSV armazenado na camada Bronze."""

    resposta = s3.get_object(
        Bucket=BUCKET_BRONZE,
        Key=chave
    )

    conteudo = resposta["Body"].read()

    df = pd.read_csv(
        BytesIO(conteudo),
        sep=";",
        encoding="utf-8"
    )

    print(f"Arquivo lido: {chave}")
    print(f"Registros encontrados: {len(df)}")

    return df


def validar_colunas(df):
    """Verifica se o CSV possui as colunas necessárias."""

    colunas_obrigatorias = [
        "cod_maquina",
        "timestamp",
        "tipo_dia",
        "fluxo_veiculos_sim",
        "cpu_pct",
        "cpu_simulada_pct",
        "ram_total_gib",
        "ram_uso_gib",
        "ram_disponivel_gib",
        "ram_pct",
        "swap_total_gib",
        "swap_uso_gib",
        "swap_pct",
        "disco_total_gib",
        "disco_uso_gib",
        "disco_livre_gib",
        "disco_pct",
        "proc_totais",
        "proc_rodando",
        "rede_recebida_mb",
        "rede_enviada_mb",
        "latencia_ms",
        "uptime_segundos"
    ]

    colunas_ausentes = [
        coluna
        for coluna in colunas_obrigatorias
        if coluna not in df.columns
    ]

    if colunas_ausentes:
        raise ValueError(
            "O CSV não possui todas as colunas necessárias: "
            + ", ".join(colunas_ausentes)
        )


def padronizar_unidades(df):
    """Padroniza unidades sem converter valores duas vezes."""

    conversoes = {
        "ram_total_bytes": ("ram_total_gib", 1024 ** 3),
        "ram_uso_bytes": ("ram_uso_gib", 1024 ** 3),
        "ram_disponivel_bytes": ("ram_disponivel_gib", 1024 ** 3),
        "swap_total_bytes": ("swap_total_gib", 1024 ** 3),
        "swap_uso_bytes": ("swap_uso_gib", 1024 ** 3),
        "disco_total_bytes": ("disco_total_gib", 1024 ** 3),
        "disco_uso_bytes": ("disco_uso_gib", 1024 ** 3),
        "disco_livre_bytes": ("disco_livre_gib", 1024 ** 3),
        "rede_recebida_bytes": ("rede_recebida_mib", 1024 ** 2),
        "rede_enviada_bytes": ("rede_enviada_mib", 1024 ** 2),
        "disco_leitura_bytes": ("disco_leitura_mib", 1024 ** 2),
        "disco_escrita_bytes": ("disco_escrita_mib", 1024 ** 2)
    }

    for coluna_origem, dados in conversoes.items():
        coluna_destino, divisor = dados

        if coluna_origem in df.columns:
            if coluna_destino not in df.columns:
                df[coluna_destino] = df[coluna_origem] / divisor

            df.drop(columns=[coluna_origem], inplace=True)

    return df


def tratar_tipos_dados(df):
    """Converte as colunas para os tipos adequados."""

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        errors="coerce",
        dayfirst=True
    )

    colunas_numericas = [
        "fluxo_veiculos_sim",
        "cpu_pct",
        "cpu_simulada_pct",
        "cpu_freq_mhz",
        "cpu_nucleos_fisicos",
        "cpu_nucleos_logicos",
        "ram_total_gib",
        "ram_uso_gib",
        "ram_disponivel_gib",
        "ram_pct",
        "swap_total_gib",
        "swap_uso_gib",
        "swap_pct",
        "disco_total_gib",
        "disco_uso_gib",
        "disco_livre_gib",
        "disco_pct",
        "disco_leitura_mb",
        "disco_escrita_mb",
        "proc_totais",
        "proc_rodando",
        "proc_cpu_top_pid",
        "proc_cpu_top_pct",
        "proc_cpu_top_threads",
        "proc_ram_top_pid",
        "proc_ram_top_mb",
        "proc_ram_top_pct",
        "rede_recebida_mb",
        "rede_enviada_mb",
        "latencia_ms",
        "uptime_segundos"
    ]

    for coluna in colunas_numericas:
        if coluna in df.columns:
            df[coluna] = pd.to_numeric(
                df[coluna],
                errors="coerce"
            )

    return df


def remover_registros_invalidos(df):
    """Remove registros incompletos, inválidos ou duplicados."""

    quantidade_inicial = len(df)

    colunas_essenciais = [
        "cod_maquina",
        "timestamp",
        "tipo_dia",
        "cpu_pct",
        "cpu_simulada_pct",
        "ram_total_gib",
        "ram_uso_gib",
        "ram_pct",
        "disco_total_gib",
        "disco_uso_gib",
        "latencia_ms"
    ]

    df = df.dropna(subset=colunas_essenciais).copy()

    df = df[
        (df["cpu_pct"].between(0, 100))
        & (df["cpu_simulada_pct"].between(0, 100))
        & (df["ram_total_gib"] >= 0)
        & (df["ram_uso_gib"] >= 0)
        & (df["ram_uso_gib"] <= df["ram_total_gib"])
        & (df["ram_pct"].between(0, 100))
        & (df["disco_total_gib"] >= 0)
        & (df["disco_uso_gib"] >= 0)
        & (df["disco_uso_gib"] <= df["disco_total_gib"])
        & (df["latencia_ms"] >= 0)
    ].copy()

    colunas_nao_negativas = [
        "fluxo_veiculos_sim",
        "ram_disponivel_gib",
        "swap_total_gib",
        "swap_uso_gib",
        "swap_pct",
        "disco_livre_gib",
        "disco_pct",
        "disco_leitura_mb",
        "disco_escrita_mb",
        "proc_totais",
        "proc_rodando",
        "proc_cpu_top_pct",
        "proc_cpu_top_threads",
        "proc_ram_top_mb",
        "proc_ram_top_pct",
        "rede_recebida_mb",
        "rede_enviada_mb",
        "uptime_segundos"
    ]

    for coluna in colunas_nao_negativas:
        if coluna in df.columns:
            df = df[
                df[coluna].isna() | (df[coluna] >= 0)
            ].copy()

    # Mantém apenas uma medição por máquina e instante.
    df = df.drop_duplicates(
        subset=["cod_maquina", "timestamp"],
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

    df["dia_semana"] = df["timestamp"].dt.dayofweek.map(
        dias_semana
    )

    df["fim_de_semana"] = (
        df["timestamp"].dt.dayofweek >= 5
    )

    # Classifica a utilização real da CPU.
    df["cpu_faixa"] = pd.cut(
        df["cpu_pct"],
        bins=[-0.001, 40, 70, 90, 100],
        labels=["BAIXA", "MODERADA", "ALTA", "CRITICA"]
    )

    # Classifica a utilização da RAM.
    df["ram_faixa"] = pd.cut(
        df["ram_pct"],
        bins=[-0.001, 50, 75, 90, 100],
        labels=["BAIXA", "MODERADA", "ALTA", "CRITICA"]
    )

    # Classifica a latência em milissegundos.
    df["latencia_faixa"] = pd.cut(
        df["latencia_ms"],
        bins=[-0.001, 20, 50, float("inf")],
        labels=["BAIXA", "MODERADA", "ALTA"]
    )

    # Identifica medições coletadas em feriados.
    df["eh_feriado"] = (
        df["tipo_dia"].astype(str).str.upper() == "FERIADO"
    )

    return df


def organizar_colunas(df):
    """Organiza as colunas para facilitar a leitura do CSV."""

    colunas_prioritarias = [
        "cod_maquina",
        "mac_address",
        "hostname",
        "so_sistema",
        "arquitetura",
        "timestamp",
        "data",
        "hora",
        "dia_semana",
        "tipo_dia",
        "fim_de_semana",
        "eh_feriado",
        "fluxo_veiculos_sim",
        "cpu_pct",
        "cpu_simulada_pct",
        "cpu_faixa",
        "cpu_freq_mhz",
        "cpu_nucleos_fisicos",
        "cpu_nucleos_logicos",
        "ram_total_gib",
        "ram_uso_gib",
        "ram_disponivel_gib",
        "ram_pct",
        "ram_faixa",
        "swap_total_gib",
        "swap_uso_gib",
        "swap_pct",
        "disco_total_gib",
        "disco_uso_gib",
        "disco_livre_gib",
        "disco_pct",
        "disco_leitura_mb",
        "disco_escrita_mb",
        "proc_totais",
        "proc_rodando",
        "proc_cpu_top_nome",
        "proc_cpu_top_pid",
        "proc_cpu_top_pct",
        "proc_cpu_top_threads",
        "proc_ram_top_nome",
        "proc_ram_top_pid",
        "proc_ram_top_mb",
        "proc_ram_top_pct",
        "proc_top_lista",
        "rede_recebida_mb",
        "rede_enviada_mb",
        "latencia_ms",
        "latencia_faixa",
        "uptime_segundos"
    ]

    colunas_existentes = [
        coluna
        for coluna in colunas_prioritarias
        if coluna in df.columns
    ]

    outras_colunas = [
        coluna
        for coluna in df.columns
        if coluna not in colunas_existentes
    ]

    return df[colunas_existentes + outras_colunas]


def salvar_csv_silver(df, chave_origem):
    """Salva o CSV tratado na camada Silver."""

    nome_arquivo = chave_origem.split("/")[-1]

    chave_destino = f"{PREFIXO_SILVER}{nome_arquivo}"

    conteudo_csv = df.to_csv(
        index=False,
        sep=";",
        encoding="utf-8"
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


def main():
    """Executa o processo ETL da camada Silver."""

    print("Iniciando ETL Silver...")

    chave_origem = buscar_csv_mais_recente()

    df = ler_csv_bronze(chave_origem)

    validar_colunas(df)

    df = padronizar_unidades(df)

    df = tratar_tipos_dados(df)

    df = remover_registros_invalidos(df)

    df = criar_colunas_derivadas(df)

    df = organizar_colunas(df)

    salvar_csv_silver(df, chave_origem)

    print("ETL Silver finalizado com sucesso!")


try:
    main()
except NoCredentialsError:
        print(
            "Erro: credenciais AWS não encontradas. "
            "Verifique suas credenciais ou permissões IAM."
        )
except ClientError as erro:
        print(f"Erro ao acessar o S3: {erro}")
except (ValueError, FileNotFoundError) as erro:
        print(f"Erro no processamento: {erro}")