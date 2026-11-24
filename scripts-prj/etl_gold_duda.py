import boto3
import pandas as pd
from io import StringIO
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError


# CONFIGURACOES AWS

REGIAO_AWS = "us-east-1"

BUCKET_SILVER = "datalake-roadvizor-sptech"
BUCKET_GOLD = "datalake-gold-duda-sptech"

PREFIXO_SILVER = "trusted/"
PREFIXO_GOLD = "gold/"

SEPARADOR = ";"

s3 = boto3.client(
    "s3",
    region_name=REGIAO_AWS
)


# LEITURA DOS ARQUIVOS SILVER

def ler_silver():

    print("\nIniciando leitura da camada Silver...")

    arquivos = []

    paginador = s3.get_paginator("list_objects_v2")

    paginas = paginador.paginate(
        Bucket=BUCKET_SILVER,
        Prefix=PREFIXO_SILVER
    )

    for pagina in paginas:

        for objeto in pagina.get("Contents", []):

            caminho = objeto["Key"]

            if caminho.lower().endswith(".csv"):

                print(f"Lendo arquivo: {caminho}")

                resposta = s3.get_object(
                    Bucket=BUCKET_SILVER,
                    Key=caminho
                )

                conteudo = resposta["Body"].read().decode("utf-8-sig")

                df_arquivo = pd.read_csv(
                    StringIO(conteudo),
                    sep=SEPARADOR
                )

                arquivos.append(df_arquivo)

    if not arquivos:
        raise Exception("Nenhum arquivo CSV encontrado na Silver.")

    df = pd.concat(
        arquivos,
        ignore_index=True
    )

    print(f"Total de registros encontrados: {len(df)}")

    return df


# PADRONIZAR COLUNAS

def padronizar_colunas(df):

    print("\nPadronizando as colunas...")

    df.columns = (
        df.columns
        .str.strip()
        .str.lower()
    )

    aliases = {
        "mac_address": "mac",
        "cpu_pct": "cpu",
        "cpu_percent": "cpu",
        "cpu_percentual": "cpu",
        "ram_pct": "ram",
        "ram_percent": "ram",
        "ram_percentual": "ram",
        "rede_recebida": "rede_recebida_mb",
        "rede_enviada": "rede_enviada_mb"
    }

    for nome_antigo, nome_novo in aliases.items():

        if nome_antigo in df.columns and nome_novo not in df.columns:

            df = df.rename(
                columns={nome_antigo: nome_novo}
            )

    if "mac" not in df.columns:
        raise Exception("A coluna 'mac' não foi encontrada na Silver.")

    df["id_servidor"] = (
        df["mac"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    colunas_obrigatorias = [
        "timestamp",
        "id_servidor",
        "cpu",
        "ram"
    ]

    for coluna in colunas_obrigatorias:

        if coluna not in df.columns:

            raise Exception(
                f"Coluna obrigatória não encontrada na Silver: {coluna}"
            )

    return df


# IDENTIFICAR AS COLUNAS DE REDE

def identificar_colunas_rede(df):

    recebida = None
    enviada = None

    candidatos_recebida = [
        "rede_recebida_mb",
        "rede_bytes_recv",
        "rede_interface_eth0_bytes_recv",
        "bytes_recv",
        "bytes_received",
        "rede_recebida_bytes"
    ]

    candidatos_enviada = [
        "rede_enviada_mb",
        "rede_bytes_sent",
        "rede_interface_eth0_bytes_sent",
        "bytes_sent",
        "bytes_transmitted",
        "rede_enviada_bytes"
    ]

    for coluna in candidatos_recebida:

        if coluna in df.columns:
            recebida = coluna
            break

    for coluna in candidatos_enviada:

        if coluna in df.columns:
            enviada = coluna
            break

    return recebida, enviada


# TRATAR OS DADOS

def tratar_dados(df):

    print("\nTratando os dados...")

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        errors="coerce"
    )

    colunas_numericas = ["cpu", "ram"]

    recebida, enviada = identificar_colunas_rede(df)

    if recebida is not None:
        colunas_numericas.append(recebida)

    if enviada is not None:
        colunas_numericas.append(enviada)

    for coluna in colunas_numericas:

        df[coluna] = pd.to_numeric(
            df[coluna],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "timestamp",
            "id_servidor",
            "cpu",
            "ram"
        ]
    )

    df = df[df["id_servidor"].str.strip() != ""]

    df = df.drop_duplicates(
        subset=[
            "id_servidor",
            "timestamp"
        ],
        keep="last"
    )

    df = df.sort_values(
        by=[
            "id_servidor",
            "timestamp"
        ]
    ).copy()

    # Calcula a diferenca entre os contadores acumulados de rede.

    if recebida is not None:

        df["rede_recebida_mb_delta"] = (
            df.groupby("id_servidor")[recebida].diff()
        )

        if "bytes" in recebida.lower():
            df["rede_recebida_mb_delta"] = (
                df["rede_recebida_mb_delta"] / (1024 ** 2)
            )

        df.loc[
            df["rede_recebida_mb_delta"] < 0,
            "rede_recebida_mb_delta"
        ] = pd.NA

    else:

        df["rede_recebida_mb_delta"] = pd.NA

        print("Aviso: contador de recebimento da rede não encontrado.")

    if enviada is not None:

        df["rede_enviada_mb_delta"] = (
            df.groupby("id_servidor")[enviada].diff()
        )

        if "bytes" in enviada.lower():
            df["rede_enviada_mb_delta"] = (
                df["rede_enviada_mb_delta"] / (1024 ** 2)
            )

        df.loc[
            df["rede_enviada_mb_delta"] < 0,
            "rede_enviada_mb_delta"
        ] = pd.NA

    else:

        df["rede_enviada_mb_delta"] = pd.NA

        print("Aviso: contador de envio da rede não encontrado.")

    print(f"Capturas válidas: {len(df)}")

    return df


# CPU A CADA 10 MINUTOS

def gerar_cpu(df):

    print("\nCalculando métricas de CPU...")

    dados = df.copy()

    dados["intervalo_10min"] = (
        dados["timestamp"].dt.floor("10min")
    )

    gold = dados.groupby(
        [
            "id_servidor",
            "intervalo_10min"
        ]
    ).agg(
        cpu_media=("cpu", "mean"),
        cpu_maxima=("cpu", "max"),
        quantidade_capturas=("cpu", "count")
    ).reset_index()

    return gold


# RAM A CADA 10 MINUTOS

def gerar_ram(df):

    print("\nCalculando métricas de RAM...")

    dados = df.copy()

    dados["intervalo_10min"] = (
        dados["timestamp"].dt.floor("10min")
    )

    gold = dados.groupby(
        [
            "id_servidor",
            "intervalo_10min"
        ]
    ).agg(
        ram_media=("ram", "mean"),
        ram_maxima=("ram", "max"),
        quantidade_capturas=("ram", "count")
    ).reset_index()

    return gold


# REDE A CADA 10 MINUTOS

def gerar_rede(df):

    print("\nCalculando métricas de rede...")

    dados = df.copy()

    dados["intervalo_10min"] = (
        dados["timestamp"].dt.floor("10min")
    )

    gold = dados.groupby(
        [
            "id_servidor",
            "intervalo_10min"
        ]
    ).agg(
        rede_recebida_mb=(
            "rede_recebida_mb_delta",
            "sum"
        ),
        rede_enviada_mb=(
            "rede_enviada_mb_delta",
            "sum"
        ),
        quantidade_capturas=("timestamp", "count")
    ).reset_index()

    return gold


# CALCULO POR HORA

def gerar_horario(df):

    print("\nCalculando médias por hora...")

    dados = df.copy()

    dados["intervalo_hora"] = (
        dados["timestamp"].dt.floor("h")
    )

    gold = dados.groupby(
        [
            "id_servidor",
            "intervalo_hora"
        ]
    ).agg(
        cpu_media=("cpu", "mean"),
        cpu_maxima=("cpu", "max"),
        ram_media=("ram", "mean"),
        ram_maxima=("ram", "max"),
        rede_recebida_mb=(
            "rede_recebida_mb_delta",
            "sum"
        ),
        rede_enviada_mb=(
            "rede_enviada_mb_delta",
            "sum"
        ),
        quantidade_capturas=("timestamp", "count")
    ).reset_index()

    return gold


# KPIs DE 8 HORAS

def gerar_kpis_8h(df):

    print("\nCalculando KPIs de 8 horas...")

    dados = df.copy()

    dados["periodo_8h"] = (
        dados["timestamp"].dt.floor("8h")
    )

    gold = dados.groupby(
        [
            "id_servidor",
            "periodo_8h"
        ]
    ).agg(
        cpu_media=("cpu", "mean"),
        cpu_maxima=("cpu", "max"),
        ram_media=("ram", "mean"),
        ram_maxima=("ram", "max"),
        rede_recebida_mb=(
            "rede_recebida_mb_delta",
            "sum"
        ),
        rede_enviada_mb=(
            "rede_enviada_mb_delta",
            "sum"
        ),
        quantidade_capturas=("timestamp", "count")
    ).reset_index()

    return gold


# ENVIAR ARQUIVOS PARA A GOLD

def enviar_gold(df, nome_arquivo):

    arquivo_csv = df.to_csv(
        index=False,
        sep=SEPARADOR
    )

    caminho = PREFIXO_GOLD + nome_arquivo

    s3.put_object(
        Bucket=BUCKET_GOLD,
        Key=caminho,
        Body=arquivo_csv.encode("utf-8"),
        ContentType="text/csv; charset=utf-8"
    )

    print(f"Arquivo enviado: s3://{BUCKET_GOLD}/{caminho}")


# EXECUCAO DA ETL GOLD

def main():

    print("\n======================================")
    print("          ETL GOLD - ROADVIZOR")
    print("======================================")

    df = ler_silver()

    df = padronizar_colunas(df)

    df = tratar_dados(df)

    gold_cpu = gerar_cpu(df)
    gold_ram = gerar_ram(df)
    gold_rede = gerar_rede(df)
    gold_hora = gerar_horario(df)
    gold_kpis = gerar_kpis_8h(df)

    enviar_gold(gold_cpu, "gold_cpu.csv")
    enviar_gold(gold_ram, "gold_ram.csv")
    enviar_gold(gold_rede, "gold_rede.csv")
    enviar_gold(gold_hora, "gold_hora.csv")
    enviar_gold(gold_kpis, "gold_kpis_8h.csv")

    print("\n======================================")
    print("          ETL GOLD FINALIZADA!")
    print("======================================")

    print("\nRegistros gerados:")

    print(f"CPU: {len(gold_cpu)}")
    print(f"RAM: {len(gold_ram)}")
    print(f"Rede: {len(gold_rede)}")
    print(f"Por hora: {len(gold_hora)}")
    print(f"KPIs de 8 horas: {len(gold_kpis)}")


if __name__ == "__main__":

    try:
        main()

    except NoCredentialsError:
        print("\nErro: credenciais AWS não encontradas.")

    except ClientError as erro:
        print("\nErro na comunicação com a AWS:")
        print(erro)

    except BotoCoreError as erro:
        print("\nErro no serviço AWS:")
        print(erro)

    except Exception as erro:
        print("\nERRO DURANTE A ETL GOLD:")
        print(erro)
