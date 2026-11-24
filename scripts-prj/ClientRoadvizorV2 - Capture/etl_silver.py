import boto3
import pandas as pd
import json

from io import BytesIO, StringIO
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError


# CONFIGURAÇÕES DA AWS

REGIAO_AWS = "us-east-1"

BUCKET = "datalake-roadvizor-sptech"

PREFIXO_BRONZE = "raw/"
PREFIXO_SILVER = "trusted/"

SEPARADOR_CSV = ";"

CAMINHO_CONTROLE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "ultimo_log_id_enviado.json"
)

s3 = boto3.client("s3", region_name=REGIAO_AWS)


# COLUNAS OBRIGATÓRIAS

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
    "processos"
]

TIPOS_DIA_VALIDOS = [
    "DIA_UTIL",
    "FIM_DE_SEMANA",
    "FERIADO"
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
    "operacao_cenario",
    "clima_condicao_climatica",
    "clima_fonte_dados",
    "status"
]


# BUSCAR O CSV MAIS RECENTE NO BRONZE

def buscar_csv_mais_recente():

    resposta = s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET,
        Prefix=PREFIXO_BRONZE
    )

    arquivos = []

    for pagina in resposta:
        for objeto in pagina.get("Contents", []):

            chave = objeto["Key"]

            if chave.lower().endswith(".csv"):
                arquivos.append(objeto)

    if len(arquivos) == 0:
        raise FileNotFoundError(
            "Nenhum arquivo CSV encontrado no Bronze."
        )

    arquivo_recente = max(
        arquivos,
        key=lambda arquivo: arquivo["LastModified"]
    )

    print("\nArquivo Bronze encontrado:")
    print(arquivo_recente["Key"])

    return arquivo_recente["Key"]


# LER O ARQUIVO DO S3

def ler_csv_bronze(chave):

    resposta = s3.get_object(
        Bucket=BUCKET,
        Key=chave
    )

    conteudo = resposta["Body"].read()

    df = pd.read_csv(
        BytesIO(conteudo),
        sep=SEPARADOR_CSV,
        encoding="utf-8",
        dtype={"mac": "string"}
    )

    print("\nQuantidade de registros:", len(df))
    print("Quantidade de colunas:", len(df.columns))

    return df


# VALIDAR AS COLUNAS

def validar_colunas(df):

    faltantes = []

    for coluna in COLUNAS_FIXAS:

        if coluna not in df.columns:
            faltantes.append(coluna)

    if len(faltantes) > 0:

        raise ValueError(
            f"Colunas obrigatórias ausentes: {faltantes}"
        )

    print("\nValidação das colunas concluída.")

    return df


# TRATAR OS TIPOS DE DADOS

def tratar_tipos_dados(df):

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

    df["tipo_dia"] = (
        df["tipo_dia"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    df["feriado"] = (
        df["feriado"]
        .astype("string")
        .fillna("NENHUM ")
        .str.strip()
    )

    for coluna in df.columns:

        if coluna in COLUNAS_TEXTO:
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

        if (
            serie.notna().sum() > 0
            and convertida.notna().sum() == serie.notna().sum()
        ):
            df[coluna] = convertida

    # A data da coleta climática também precisa ser tratada.

    if "clima_coletado_em" in df.columns:

        df["clima_coletado_em"] = pd.to_datetime(
            df["clima_coletado_em"],
            errors="coerce"
        )

    print("\nTipos de dados tratados.")

    return df


# VALIDAR OS REGISTROS E AS MÉTRICAS

def remover_registros_invalidos(df):

    df = df.dropna(subset=["timestamp", "mac", "tipo_dia"])

    df = df[df["mac"].str.strip() != ""]

    df = df[df["tipo_dia"].isin(TIPOS_DIA_VALIDOS)]

    excecoes_negativas = [
        "clima_temperatura",
        "rede_interface_eth0_duplex"
    ]

    for coluna in df.columns:
        if (
            pd.api.types.is_numeric_dtype(df[coluna])
            and not pd.api.types.is_bool_dtype(df[coluna])
            and coluna not in excecoes_negativas
        ):
            df.loc[df[coluna] < 0, coluna] = pd.NA

    colunas_percentuais = [
        coluna for coluna in df.columns
        if coluna.endswith("_percent")
    ]

    colunas_percentuais.extend([
        "cpu",
        "clima_umidade",
        "operacao_perda_pacotes_pct"
    ])

    for coluna in colunas_percentuais:
        if coluna in df.columns:
            df.loc[
                (df[coluna] < 0) | (df[coluna] > 100),
                coluna
            ] = pd.NA

    pares_recursos = [
        ("ram_used", "ram_total"),
        ("disco_used", "disco_total"),
        ("swap_used", "swap_total")
    ]

    for usado, total in pares_recursos:
        if usado in df.columns and total in df.columns:
            df.loc[df[usado] > df[total], usado] = pd.NA

    df = df.drop_duplicates(
        subset=["mac", "timestamp"],
        keep="last"
    )

    if df.empty:
        raise ValueError("Nenhum registro válido encontrado.")

    return df

       
  
   # Validar métricas percentuais.

    colunas_percentuais = [
        coluna for coluna in df.columns
        if coluna.endswith("_percent")
    ]

    colunas_percentuais.extend([
        "cpu",
        "clima_umidade",
        "operacao_perda_pacotes_pct"
    ])

    for coluna in colunas_percentuais:

        if coluna in df.columns:

            invalido = (
                (df[coluna] < 0)
                | (df[coluna] > 100)
            )

            df.loc[invalido, coluna] = pd.NA

    # Verificar se os valores utilizados não ultrapassam o total.

    pares = [
        ("ram_used", "ram_total"),
        ("disco_used", "disco_total"),
        ("swap_used", "swap_total")
    ]

    for utilizada, total in pares:

        if utilizada in df.columns and total in df.columns:

            invalido = df[utilizada] > df[total]

            df.loc[invalido, utilizada] = pd.NA

    # Evitar duplicidade de leituras do mesmo servidor no mesmo instante.

    df = df.drop_duplicates(
        subset=["mac", "timestamp"],
        keep="last"
    )

    quantidade_final = len(df)

    print("\nValidação dos registros concluída.")
    print("Registros recebidos:", quantidade_inicial)
    print("Registros válidos:", quantidade_final)
    print("Registros descartados:", quantidade_inicial - quantidade_final)

    if df.empty:

        raise ValueError(
            "Nenhum registro válido encontrado."
        )

    return df


# INTERPRETAR O JSON DE PROCESSOS

def interpretar_processos(valor):

    if isinstance(valor, dict):
        return valor

    if not isinstance(valor, str) or valor.strip() == "":
        return {}

    try:

        resultado = json.loads(valor)

        if isinstance(resultado, dict):
            return resultado

        return {}

    except (json.JSONDecodeError, TypeError):

        return {}


# EXTRAIR INFORMAÇÕES DOS PROCESSOS

def criar_colunas_processos(df):

    processos_lista = []

    for valor in df["processos"]:

        processos = interpretar_processos(valor)

        top_cpu = processos.get("top_cpu") or {}
        top_ram = processos.get("top_ram") or {}

        if not isinstance(top_cpu, dict):
            top_cpu = {}

        if not isinstance(top_ram, dict):
            top_ram = {}

        top_lista = processos.get("top_processos_cpu") or []

        if not isinstance(top_lista, list):
            top_lista = []

        processos_lista.append({

            "proc_totais": processos.get("total"),
            "proc_rodando": processos.get("rodando"),

            "proc_cpu_top_nome": top_cpu.get("nome"),
            "proc_cpu_top_pid": top_cpu.get("pid"),
            "proc_cpu_top_pct": top_cpu.get("cpu_pct"),
            "proc_cpu_top_ram_mb": top_cpu.get("ram_mb"),
            "proc_cpu_top_ram_pct": top_cpu.get("ram_pct"),
            "proc_cpu_top_status": top_cpu.get("status"),
            "proc_cpu_top_threads": top_cpu.get("threads"),

            "proc_ram_top_nome": top_ram.get("nome"),
            "proc_ram_top_pid": top_ram.get("pid"),
            "proc_ram_top_cpu_pct": top_ram.get("cpu_pct"),
            "proc_ram_top_mb": top_ram.get("ram_mb"),
            "proc_ram_top_pct": top_ram.get("ram_pct"),
            "proc_ram_top_status": top_ram.get("status"),
            "proc_ram_top_threads": top_ram.get("threads"),

            "proc_top_processos_cpu": json.dumps(
                top_lista,
                ensure_ascii=False
            )

        })

    df_processos = pd.DataFrame(processos_lista)

    # Converter os campos numéricos extraídos do JSON.

    colunas_numericas = [
        "proc_totais",
        "proc_rodando",
        "proc_cpu_top_pid",
        "proc_cpu_top_pct",
        "proc_cpu_top_ram_mb",
        "proc_cpu_top_ram_pct",
        "proc_cpu_top_threads",
        "proc_ram_top_pid",
        "proc_ram_top_cpu_pct",
        "proc_ram_top_mb",
        "proc_ram_top_pct",
        "proc_ram_top_threads"
    ]

    for coluna in colunas_numericas:

        df_processos[coluna] = pd.to_numeric(
            df_processos[coluna],
            errors="coerce"
        )

    # Remover o JSON original depois de extrair suas informações.

    df = df.drop(columns=["processos"])

    df = pd.concat(
        [
            df.reset_index(drop=True),
            df_processos.reset_index(drop=True)
        ],
        axis=1
    )

    print("\nInformações dos processos extraídas.")

    return df


# CRIAR COLUNAS DERIVADAS

def criar_colunas_derivadas(df):

    df["data"] = df["timestamp"].dt.date

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

    df["eh_feriado"] = df["tipo_dia"] == "FERIADO"

    # Criar classificação de utilização dos recursos.

    colunas_recursos = [
        "cpu",
        "ram_percent",
        "disco_percent",
        "swap_percent"
    ]

    for coluna in colunas_recursos:

        if coluna in df.columns:

            nome_classificacao = (
                coluna.replace("_percent", "")
                + "_classificacao"
            )

            df[nome_classificacao] = pd.cut(
                df[coluna],
                bins=[-0.001, 50, 75, 90, 100],
                labels=[
                    "BAIXA",
                    "MODERADA",
                    "ALTA",
                    "CRITICA"
                ],
                include_lowest=True
            )

    print("\nColunas derivadas criadas.")

    return df


# ORGANIZAR AS COLUNAS

def organizar_colunas(df):

    colunas_inicio = [
        "timestamp",
        "data",
        "hora",
        "dia_semana",
        "mac",
        "hostname",
        "so_sistema",
        "arquitetura",
        "cpu_fisicos",
        "cpu_logicos",
        "tipo_dia",
        "feriado",
        "fim_de_semana",
        "eh_feriado",
        "log_id",
        "servidor_id",
        "status"
    ]

    colunas_processos = [
        "proc_totais",
        "proc_rodando",
        "proc_cpu_top_nome",
        "proc_cpu_top_pid",
        "proc_cpu_top_pct",
        "proc_cpu_top_ram_mb",
        "proc_cpu_top_ram_pct",
        "proc_cpu_top_status",
        "proc_cpu_top_threads",
        "proc_ram_top_nome",
        "proc_ram_top_pid",
        "proc_ram_top_cpu_pct",
        "proc_ram_top_mb",
        "proc_ram_top_pct",
        "proc_ram_top_status",
        "proc_ram_top_threads",
        "proc_top_processos_cpu"
    ]

    inicio = [
        coluna for coluna in colunas_inicio
        if coluna in df.columns
    ]

    processos = [
        coluna for coluna in colunas_processos
        if coluna in df.columns
    ]

    outras = [
        coluna for coluna in df.columns
        if coluna not in inicio and coluna not in processos
    ]

    df = df[inicio + outras + processos]

    return df


# SALVAR O CSV NO SILVER

def salvar_csv_silver(df, chave_origem):

    nome_arquivo = chave_origem.split("/")[-1]

    chave_destino = PREFIXO_SILVER + nome_arquivo

    buffer = StringIO()

    df.to_csv(
        buffer,
        sep=SEPARADOR_CSV,
        index=False,
        encoding="utf-8"
    )

    

    s3.put_object(
        Bucket=BUCKET,
        Key=chave_destino,
        Body=buffer.getvalue().encode("utf-8"),
        ContentType="text/csv; charset=utf-8"
    )

    print("\nArquivo Silver enviado com sucesso!")
    print(f"s3://{BUCKET}/{chave_destino}")

    return chave_destino


# EXECUTAR O ETL COMPLETO

def main():

    print("=" * 55)
    print("ROADVIZOR - ETL SILVER")
    print("=" * 55)

    # 1. Encontrar o arquivo mais recente.

    chave = buscar_csv_mais_recente()

    # 2. Ler o arquivo Bronze.

    df = ler_csv_bronze(chave)

    # 3. Validar as colunas.

    df = validar_colunas(df)

    # 4. Tratar os tipos de dados.

    df = tratar_tipos_dados(df)

    # 5. Validar os registros.

    df = remover_registros_invalidos(df)

    # 6. Extrair as informações dos processos.

    df = criar_colunas_processos(df)

    # 7. Criar colunas derivadas.

    df = criar_colunas_derivadas(df)

    # 8. Organizar as colunas.

    df = organizar_colunas(df)

    # 9. Enviar o resultado para o Silver.

    salvar_csv_silver(df, chave)

    print("\n" + "=" * 55)
    print("ETL SILVER FINALIZADO!")
    print("=" * 55)

    print("\nDimensões finais:", df.shape)

    print("\nColunas disponíveis:")
    print(df.columns.tolist())


if __name__ == "__main__":

    try:

        main()

    except NoCredentialsError:

        print("\nErro: credenciais AWS não encontradas.")

    except ClientError as erro:

        print("\nErro na comunicação com a AWS:")
        print(erro)

    except (ValueError, FileNotFoundError) as erro:

        print("\nErro no processamento:")
        print(erro)

    except BotoCoreError as erro:

        print("\nErro no serviço AWS:")
        print(erro)
