
import boto3
import pandas as pd
from io import StringIO
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError

REGIAO_AWS = "us-east-1"

BUCKET_SILVER = "datalake-roadvizor-sptech"
BUCKET_GOLD = "datalake-gold-duda-sptech"

PREFIXO_SILVER = "trusted/"
PREFIXO_GOLD = "gold/"

SEPARADOR = ";"

s3 = boto3.client("s3", region_name=REGIAO_AWS)


def ler_silver():
    arquivos = []
    paginador = s3.get_paginator("list_objects_v2")

    paginas = paginador.paginate(
        Bucket=BUCKET_SILVER,
        Prefix=PREFIXO_SILVER
    )

    for pagina in paginas:
        for objeto in pagina.get("Contents", []):
            chave = objeto["Key"]

            if chave.endswith(".csv"):
                resposta = s3.get_object(
                    Bucket=BUCKET_SILVER,
                    Key=chave
                )

                conteudo = resposta["Body"].read().decode("utf-8-sig")

                if conteudo.strip():
                    df = pd.read_csv(
                        StringIO(conteudo),
                        sep=SEPARADOR
                    )

                    if not df.empty:
                        arquivos.append(df)

    if not arquivos:
        raise ValueError("Nenhum arquivo CSV encontrado na Silver.")

    return pd.concat(arquivos, ignore_index=True)


def padronizar_colunas(df):
    df.columns = [
        coluna.strip().lower()
        for coluna in df.columns
    ]

    aliases = {
        "mac_address": "mac",
        "mac_adress": "mac",
        "cpu_pct": "cpu",
        "cpu_percent": "cpu",
        "cpu_percentual": "cpu",
        "ram_pct": "ram",
        "ram_percent": "ram",
        "ram_percentual": "ram"
    }

    for origem, destino in aliases.items():
        if origem in df.columns and destino not in df.columns:
            df.rename(columns={origem: destino}, inplace=True)

    obrigatorias = ["mac", "timestamp", "cpu", "ram"]

    for coluna in obrigatorias:
        if coluna not in df.columns:
            raise ValueError(
                "Coluna obrigatoria ausente: " + coluna
            )

    df["id_servidor"] = (
        df["mac"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    return df


def identificar_colunas_rede(df):
    recebida = [
        "rede_bytes_recv",
        "rede_interface_eth0_bytes_recv",
        "rede_io_bytes_recv",
        "bytes_recv",
        "bytes_received",
        "rede_recebida_bytes",
        "rede_recebida_mb"
    ]

    enviada = [
        "rede_bytes_sent",
        "rede_interface_eth0_bytes_sent",
        "rede_io_bytes_sent",
        "bytes_sent",
        "bytes_sended",
        "rede_enviada_bytes",
        "rede_enviada_mb"
    ]

    coluna_recebida = None
    coluna_enviada = None

    for coluna in recebida:
        if coluna in df.columns:
            coluna_recebida = coluna
            break

    for coluna in enviada:
        if coluna in df.columns:
            coluna_enviada = coluna
            break

    return coluna_recebida, coluna_enviada


def tratar_dados(df):
    df = padronizar_colunas(df)

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        errors="coerce"
    )

    df["cpu"] = pd.to_numeric(
        df["cpu"],
        errors="coerce"
    )

    df["ram"] = pd.to_numeric(
        df["ram"],
        errors="coerce"
    )

    coluna_recebida, coluna_enviada = identificar_colunas_rede(df)

    if coluna_recebida:
        df[coluna_recebida] = pd.to_numeric(
            df[coluna_recebida],
            errors="coerce"
        )

    if coluna_enviada:
        df[coluna_enviada] = pd.to_numeric(
            df[coluna_enviada],
            errors="coerce"
        )

    df.dropna(
        subset=["timestamp", "cpu", "ram"],
        inplace=True
    )

    df = df[df["id_servidor"] != ""]
    df = df[df["id_servidor"] != "NAN"]

    df.drop_duplicates(
        subset=["id_servidor", "timestamp"],
        inplace=True
    )

    df.sort_values(
        by=["id_servidor", "timestamp"],
        inplace=True
    )

    if coluna_recebida:
        df["rede_recebida_mb"] = (
            df.groupby("id_servidor")[coluna_recebida]
            .diff()
        )

        if "bytes" in coluna_recebida:
            df["rede_recebida_mb"] = (
                df["rede_recebida_mb"] / (1024 * 1024)
            )

        df.loc[
            df["rede_recebida_mb"] < 0,
            "rede_recebida_mb"
        ] = None
    else:
        df["rede_recebida_mb"] = 0
        print("Aviso: coluna de rede recebida nao encontrada.")

    if coluna_enviada:
        df["rede_enviada_mb"] = (
            df.groupby("id_servidor")[coluna_enviada]
            .diff()
        )

        if "bytes" in coluna_enviada:
            df["rede_enviada_mb"] = (
                df["rede_enviada_mb"] / (1024 * 1024)
            )

        df.loc[
            df["rede_enviada_mb"] < 0,
            "rede_enviada_mb"
        ] = None
    else:
        df["rede_enviada_mb"] = 0
        print("Aviso: coluna de rede enviada nao encontrada.")

    df["rede_recebida_mb"] = df["rede_recebida_mb"].fillna(0)
    df["rede_enviada_mb"] = df["rede_enviada_mb"].fillna(0)

    return df


def gerar_cpu(df):
    dados = df.copy()

    dados["intervalo"] = dados["timestamp"].dt.floor("10min")

    resultado = dados.groupby(
        ["id_servidor", "intervalo"]
    ).agg(
        cpu_media=("cpu", "mean"),
        cpu_maxima=("cpu", "max"),
        quantidade_leituras=("cpu", "count")
    ).reset_index()

    resultado.rename(
        columns={"intervalo": "timestamp"},
        inplace=True
    )

    return resultado


def gerar_ram(df):
    dados = df.copy()

    dados["intervalo"] = dados["timestamp"].dt.floor("10min")

    resultado = dados.groupby(
        ["id_servidor", "intervalo"]
    ).agg(
        ram_media=("ram", "mean"),
        ram_maxima=("ram", "max"),
        quantidade_leituras=("ram", "count")
    ).reset_index()

    resultado.rename(
        columns={"intervalo": "timestamp"},
        inplace=True
    )

    return resultado


def gerar_rede(df):
    dados = df.copy()

    dados["intervalo"] = dados["timestamp"].dt.floor("10min")

    resultado = dados.groupby(
        ["id_servidor", "intervalo"]
    ).agg(
        rede_recebida_mb=("rede_recebida_mb", "sum"),
        rede_enviada_mb=("rede_enviada_mb", "sum"),
        quantidade_leituras=("timestamp", "count")
    ).reset_index()

    resultado.rename(
        columns={"intervalo": "timestamp"},
        inplace=True
    )

    return resultado


def gerar_horario(df):
    dados = df.copy()

    dados["intervalo"] = dados["timestamp"].dt.floor("h")

    resultado = dados.groupby(
        ["id_servidor", "intervalo"]
    ).agg(
        cpu_media=("cpu", "mean"),
        cpu_maxima=("cpu", "max"),
        ram_media=("ram", "mean"),
        ram_maxima=("ram", "max"),
        rede_recebida_mb=("rede_recebida_mb", "sum"),
        rede_enviada_mb=("rede_enviada_mb", "sum"),
        quantidade_leituras=("timestamp", "count")
    ).reset_index()

    resultado.rename(
        columns={"intervalo": "timestamp"},
        inplace=True
    )

    return resultado


def gerar_kpis_8h(df):
    dados = df.copy()

    dados["periodo_8h"] = dados["timestamp"].dt.floor("8h")

    resultado = dados.groupby(
        ["id_servidor", "periodo_8h"]
    ).agg(
        cpu_media=("cpu", "mean"),
        cpu_maxima=("cpu", "max"),
        ram_media=("ram", "mean"),
        ram_maxima=("ram", "max"),
        rede_recebida_mb=("rede_recebida_mb", "sum"),
        rede_enviada_mb=("rede_enviada_mb", "sum"),
        quantidade_leituras=("timestamp", "count")
    ).reset_index()

    resultado.rename(
        columns={"periodo_8h": "timestamp"},
        inplace=True
    )

    return resultado


def enviar_gold(df, nome_arquivo):
    arquivo_csv = df.to_csv(
        index=False,
        sep=SEPARADOR
    )

    chave = PREFIXO_GOLD + nome_arquivo

    s3.put_object(
        Bucket=BUCKET_GOLD,
        Key=chave,
        Body=arquivo_csv.encode("utf-8"),
        ContentType="text/csv"
    )

    print("Arquivo enviado: " + chave)
    print("Quantidade de registros:", len(df))


def main():
    print("Iniciando ETL Gold RoadVizor...")

    print("Lendo dados da Silver...")
    df = ler_silver()

    print("Leituras encontradas:", len(df))

    print("Tratando dados...")
    df = tratar_dados(df)

    print("Leituras validas:", len(df))

    print("Gerando indicadores de CPU...")
    gold_cpu = gerar_cpu(df)

    print("Gerando indicadores de RAM...")
    gold_ram = gerar_ram(df)

    print("Gerando indicadores de rede...")
    gold_rede = gerar_rede(df)

    print("Gerando consolidacao por hora...")
    gold_hora = gerar_horario(df)

    print("Gerando KPIs de 8 horas...")
    gold_kpis_8h = gerar_kpis_8h(df)

    print("Enviando arquivos para a Gold...")

    enviar_gold(gold_cpu, "gold_cpu.csv")
    enviar_gold(gold_ram, "gold_ram.csv")
    enviar_gold(gold_rede, "gold_rede.csv")
    enviar_gold(gold_hora, "gold_hora.csv")
    enviar_gold(gold_kpis_8h, "gold_kpis_8h.csv")

    print("ETL Gold finalizada!")


if __name__ == "__main__":
    try:
        main()

    except NoCredentialsError:
        print("Credenciais AWS nao encontradas.")

    except ClientError as erro:
        print("Erro AWS:", erro)

    except BotoCoreError as erro:
        print("Erro de comunicacao AWS:", erro)

    except Exception as erro:
        print("Erro na ETL:", erro)
