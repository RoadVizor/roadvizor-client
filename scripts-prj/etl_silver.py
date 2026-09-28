import boto3
import pandas as pd
from io import BytesIO


# CONFIGURAÇÕES
s3 = boto3.client(
    "s3",
    region_name="us-east-1"
)

BUCKET_BRONZE = "datalake-bronze-roadvizor-sptech"
BUCKET_SILVER = "datalake-silver-roadvizor-sptech"


# ENCONTRAR CSV MAIS RECENTE
def pegar_key(arquivo):
    return arquivo["Key"]


def encontrar_arquivo_mais_recente():

    resposta = s3.list_objects_v2(
        Bucket=BUCKET_BRONZE
    )

    print("Arquivos encontrados no bucket:")

    for objeto in resposta.get("Contents", []):
        print(objeto["Key"])

    arquivos = []

    for objeto in resposta.get("Contents", []):

        chave = objeto["Key"]

    if chave.endswith(".csv") and chave.startswith("raw/roadvizor_"):
        arquivos.append(objeto)

    if not arquivos:
        raise Exception(
            "Nenhum CSV do Roadvizor encontrado no bucket Bronze."
        )

    arquivo_mais_recente = max(
        arquivos,
        key=pegar_key
    )

    return arquivo_mais_recente["Key"]


# LER CSV DO S3

def ler_csv_s3(chave):

    resposta = s3.get_object(
        Bucket=BUCKET_BRONZE,
        Key=chave
    )

    conteudo = resposta["Body"].read()

    df = pd.read_csv(
        BytesIO(conteudo),
        sep=";"
    )

    return df


# TRANSFORMAÇÕES SILVER

def transformar_dados(df):

    # Converter timestamp
    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        format="%d/%m/%Y %H:%M:%S",
        errors="coerce"
    )

    # Colunas numéricas
    colunas_numericas = [
        "cpu",
        "ram_total",
        "ram_uso",
        "swap_total",
        "swap_uso",
        "proc_rodando",
        "proc_esperando",
        "disco_total",
        "disco_uso",
        "rede_enviada",
        "rede_recebida",
        "pacote_perdido_entrada",
        "pacote_perdido_saida",
        "latencia"
    ]

    for coluna in colunas_numericas:

        df[coluna] = pd.to_numeric(
            df[coluna],
            errors="coerce"
        )

    return df


# VALIDAÇÃO DOS DADOS

def validar_dados(df):

    quantidade_inicial = len(df)

    # Timestamp válido
    df = df[
        df["timestamp"].notna()
    ]

    # CPU entre 0 e 100
    df = df[
        (df["cpu"] >= 0) &
        (df["cpu"] <= 100)
    ]

    # RAM usada <= RAM total
    df = df[
        df["ram_uso"] <= df["ram_total"]
    ]

    # Swap usada <= Swap total
    df = df[
        df["swap_uso"] <= df["swap_total"]
    ]

    # Disco usado <= Disco total
    df = df[
        df["disco_uso"] <= df["disco_total"]
    ]

    # Processos não podem ser negativos
    df = df[
        (df["proc_rodando"] >= 0) &
        (df["proc_esperando"] >= 0)
    ]

    # Latência não pode ser negativa
    df = df[
        df["latencia"] >= 0
    ]

    # Remover medições duplicadas
    df = df.drop_duplicates(
        subset=[
            "id_maquina",
            "timestamp"
        ]
    )

    quantidade_final = len(df)

    print(
        f"Registros antes da validação: "
        f"{quantidade_inicial}"
    )

    print(
        f"Registros depois da validação: "
        f"{quantidade_final}"
    )

    print(
        f"Registros removidos: "
        f"{quantidade_inicial - quantidade_final}"
    )

    return df


# SALVAR SILVER

def salvar_silver(df, arquivo_origem):

    nome_arquivo = arquivo_origem.split("/")[-1]

    csv = df.to_csv(
        sep=";",
        index=False
    )

    s3.put_object(
        Bucket=BUCKET_SILVER,
        Key=nome_arquivo,
        Body=csv.encode("utf-8")
    )

    print(
        f"Silver criada: "
        f"s3://{BUCKET_SILVER}/trusted/{nome_arquivo}"
    )


# ETL


print("=================================")
print("       ETL SILVER - V1")
print("=================================")


print("\nProcurando CSV mais recente...")

arquivo = encontrar_arquivo_mais_recente()

print(f"Arquivo selecionado: {arquivo}")


print("\nLendo CSV do S3...")

df = ler_csv_s3(arquivo)

print(f"Registros encontrados: {len(df)}")


print("\nTransformando dados...")

df = transformar_dados(df)


print("\nValidando dados...")

df = validar_dados(df)


print("\nSalvando Silver...")

salvar_silver(df, arquivo)


print("\n=================================")
print("       ETL FINALIZADO")
print("=================================")