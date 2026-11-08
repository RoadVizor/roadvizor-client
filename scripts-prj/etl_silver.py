import json
from io import BytesIO

import boto3
import pandas as pd
from botocore.exceptions import ClientError, NoCredentialsError


REGIAO_AWS = "us-east-1"

BUCKET_BRONZE = "datalake-bronze-roadvizor-sptech"
BUCKET_SILVER = "datalake-silver-roadvizor-sptech"

PREFIXO_BRONZE = "raw/"
PREFIXO_SILVER = "trusted/"

# O clientV02 grava o CSV com ";" (constante SEPARADOR_CSV no client).
SEPARADOR_BRONZE = ";"
# Mesmo padrão do Bronze, compatível com a leitura atual da Gold.
SEPARADOR_SILVER = ";"

# Colunas que o clientV02 SEMPRE grava, independente dos componentes da API.
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

# Colunas de texto que nunca devem ser convertidas para número.
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

TIPOS_DIA_VALIDOS = {"DIA_UTIL", "FIM_DE_SEMANA", "FERIADO"}

# As colunas dos componentes são dinâmicas (dependem do campo "nome" cadastrado
# na tabela componente). Regras genéricas aplicadas na Silver:
#   - toda coluna terminada em "_percent" (ram, disco, swap...) fica entre 0 e 100;
#   - componentes que retornam um número simples (ex.: CPU) viram uma coluna só
#     com o nome do componente. Liste aqui o nome já sanitizado desses componentes
#     (minúsculo, sem acento, com "_") para validar 0-100 também.
COLUNAS_PERCENTUAIS_SIMPLES = ["cpu"]

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
        sep=SEPARADOR_BRONZE,
        encoding="utf-8",
        # MAC como texto: sem isso, um MAC só com dígitos (ou com "E" no meio)
        # seria lido como número e perderia zeros à esquerda.
        dtype={"mac": str}
    )

    print(f"Arquivo lido: {chave}")
    print(f"Registros encontrados: {len(df)}")

    return df


def validar_colunas(df):
    """Verifica se o CSV possui as colunas fixas do client."""

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
        f"Colunas de componentes encontradas: {len(colunas_componentes)}"
    )


def tratar_tipos_dados(df):
    """Converte as colunas para os tipos adequados."""

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce"
    )

    df["mac"] = df["mac"].astype("string").str.strip().str.upper()
    df["tipo_dia"] = (
        df["tipo_dia"].astype("string").str.strip().str.upper()
    )
    df["feriado"] = df["feriado"].astype("string").fillna("")

    # Colunas dinâmicas lidas como texto: converte para número quando TODOS
    # os valores preenchidos forem numéricos (ex.: duplex/flags continuam texto).
    for coluna in df.columns:
        if coluna in COLUNAS_TEXTO:
            continue

        serie = df[coluna]

        if pd.api.types.is_numeric_dtype(serie) or pd.api.types.is_bool_dtype(serie):
            continue

        convertida = pd.to_numeric(serie, errors="coerce")

        if convertida.notna().sum() == serie.notna().sum():
            df[coluna] = convertida

    return df


def colunas_percentuais(df):
    """Retorna as colunas numéricas que representam percentual de uso."""

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
    """Remove registros incompletos, inválidos ou duplicados."""

    quantidade_inicial = len(df)

    # Campos essenciais sempre presentes
    df = df.dropna(subset=["timestamp", "mac", "tipo_dia"]).copy()
    df = df[df["tipo_dia"].isin(TIPOS_DIA_VALIDOS)].copy()

    # Percentuais entre 0 e 100 (valores vazios são mantidos)
    for coluna in colunas_percentuais(df):
        df = df[df[coluna].isna() | df[coluna].between(0, 100)].copy()

    # Nenhuma métrica numérica coletada pode ser negativa
    colunas_numericas = df.select_dtypes(include="number").columns

    if len(colunas_numericas) > 0:
        mascara = (
            df[colunas_numericas].isna() | (df[colunas_numericas] >= 0)
        ).all(axis=1)
        df = df[mascara].copy()

    # Coerência: uso nunca pode passar do total (ram, swap, disco...)
    for coluna in list(df.columns):
        if not coluna.endswith("_total"):
            continue

        coluna_usada = coluna[: -len("_total")] + "_used"

        if coluna_usada in df.columns:
            df = df[
                df[coluna].isna()
                | df[coluna_usada].isna()
                | (df[coluna_usada] <= df[coluna])
            ].copy()

    # Mantém apenas uma medição por máquina e instante.
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


def interpretar_processos(valor):
    """Converte o JSON da coluna 'processos' em dicionário (vazio se inválido)."""

    if not isinstance(valor, str) or not valor.strip():
        return {}

    try:
        dados = json.loads(valor)
    except json.JSONDecodeError:
        return {}

    return dados if isinstance(dados, dict) else {}


def criar_colunas_processos(df):
    """Abre o JSON de processos em colunas próprias."""

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
                ensure_ascii=False
            ),
        })

    detalhes = pd.DataFrame(linhas, index=df.index)

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
        detalhes[coluna] = pd.to_numeric(detalhes[coluna], errors="coerce")

    df = df.drop(columns=["processos"])

    return pd.concat([df, detalhes], axis=1)


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

    df["dia_semana"] = df["timestamp"].dt.day_of_week.map(dias_semana)
    df["fim_de_semana"] = df["timestamp"].dt.day_of_week >= 5
    df["eh_feriado"] = df["tipo_dia"] == "FERIADO"

    # Faixa de uso para cada percentual (ex.: ram_percent -> ram_faixa).
    for coluna in colunas_percentuais(df):
        nome_faixa = coluna.removesuffix("_percent") + "_faixa"

        df[nome_faixa] = pd.cut(
            df[coluna],
            bins=[-0.001, 50, 75, 90, 100],
            labels=["BAIXA", "MODERADA", "ALTA", "CRITICA"]
        )

    return df


def organizar_colunas(df):
    """Organiza as colunas para facilitar a leitura do CSV."""

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

    inicio = [coluna for coluna in colunas_inicio if coluna in df.columns]

    meio = [
        coluna
        for coluna in df.columns
        if coluna not in inicio and coluna not in colunas_fim
    ]

    return df[inicio + meio + colunas_fim]


def salvar_csv_silver(df, chave_origem):
    """Salva o CSV tratado na camada Silver."""

    nome_arquivo = chave_origem.split("/")[-1]

    chave_destino = f"{PREFIXO_SILVER}{nome_arquivo}"

    conteudo_csv = df.to_csv(
        index=False,
        sep=SEPARADOR_SILVER,
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

    df = tratar_tipos_dados(df)

    df = remover_registros_invalidos(df)

    df = criar_colunas_processos(df)

    df = criar_colunas_derivadas(df)

    df = organizar_colunas(df)

    salvar_csv_silver(df, chave_origem)

    print("ETL Silver finalizado com sucesso!")


if __name__ == "__main__":
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