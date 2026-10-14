import boto3
import pandas as pd
import mysql.connector
import json

from io import BytesIO
from datetime import datetime

# Configurações AWS
s3 = boto3.client("s3", region_name="us-east-1")

BUCKET_SILVER = "datalake-silver-roadvizor-sptech"
BUCKET_GOLD = "datalake-gold-roadvizor-sptech"

# Configurações do banco
DB_CONFIG = {
    "host": "localhost",
    "user": "aluno",
    "password": "sptech",
    "database": "roadvizor",
    "port": 3306
}


def obter_conexao_db():
    return mysql.connector.connect(**DB_CONFIG)


def encontrar_arquivo_mais_recente_silver():
    arquivos = []
    paginator = s3.get_paginator("list_objects_v2")

    for pagina in paginator.paginate(
        Bucket=BUCKET_SILVER,
        Prefix="trusted/"
    ):
        for obj in pagina.get("Contents", []):
            if obj["Key"].lower().endswith(".csv"):
                arquivos.append(obj)

    if not arquivos:
        raise Exception("Nenhum CSV encontrado no bucket Silver.")

    arquivo_mais_recente = max(
        arquivos,
        key=lambda obj: obj["LastModified"]
    )

    return arquivo_mais_recente["Key"]


def ler_csv_silver(chave):
    resposta = s3.get_object(
        Bucket=BUCKET_SILVER,
        Key=chave
    )

    conteudo = resposta["Body"].read()

    return pd.read_csv(
        BytesIO(conteudo),
        sep=";",
        encoding="utf-8"
    )


def buscar_parametros_servidor(mac_address):
    conn = obter_conexao_db()

    try:
        with conn.cursor(dictionary=True) as cursor:
            sql_servidor = """
                SELECT
                    s.id_servidor,
                    s.empresa_id,
                    s.endereco_mac,
                    s.apelido,
                    s.modelo,
                    e.razao_social,
                    ed.km,
                    ed.sentido
                FROM servidor s
                INNER JOIN empresa e
                    ON s.empresa_id = e.id_empresa
                LEFT JOIN endereco ed
                    ON s.endereco_id = ed.id_endereco
                WHERE UPPER(
                    REPLACE(REPLACE(s.endereco_mac, ':', ''), '-', '')
                ) = %s
            """

            cursor.execute(sql_servidor, (mac_address,))
            servidor = cursor.fetchone()

            if not servidor:
                return None

            sql_componentes = """
                SELECT
                    c.nome AS componente,
                    c.unidade_medida,
                    c.depende_de,
                    sc.limite_min,
                    sc.limite_max,
                    sc.capacidade_total
                FROM servidor_componente sc
                INNER JOIN componente c
                    ON sc.componente_id = c.id_componente
                WHERE sc.servidor_id = %s
                  AND sc.empresa_id = %s
                  AND sc.monitorado = 1
            """

            cursor.execute(
                sql_componentes,
                (
                    servidor["id_servidor"],
                    servidor["empresa_id"]
                )
            )

            servidor["componentes"] = cursor.fetchall()

            return servidor

    finally:
        conn.close()


def obter_valor_metrica(linha, componente):
    nome = componente.strip().lower()

    # Associação entre os componentes do banco e as colunas da Silver
    colunas = {
        "cpu": "cpu_pct",
        "ram": "ram_uso_gib",
        "disco": "disco_uso_gib",
        "placa_rede": "velocidade_rede_mbps"
    }

    coluna = colunas.get(nome)

    if coluna is None or coluna not in linha.index:
        return None

    valor = pd.to_numeric(
        pd.Series([linha[coluna]]),
        errors="coerce"
    ).iloc[0]

    if pd.isna(valor):
        return None

    return float(valor)


def converter_medida(valor, componente, unidade_destino):
    if valor is None:
        return None

    # Os campos da Silver já estão em GiB, Mbps ou porcentagem.
    # Não converter novamente valores que já estão na unidade esperada.
    unidade = unidade_destino.strip().lower()

    if unidade == "pct":
        return round(float(valor), 2)

    if unidade == "gib" and componente in ("ram", "disco"):
        return round(float(valor), 2)

    if unidade == "mbps" and componente == "placa_rede":
        return round(float(valor), 2)

    if unidade == "gbps" and componente == "placa_rede":
        return round(float(valor) / 1000, 3)

    return round(float(valor), 2)


def montar_json_gold(linha, dados_bd):
    timestamp = pd.to_datetime(
        linha["timestamp"],
        errors="coerce"
    )

    timestamp_str = (
        timestamp.strftime("%d/%m/%Y %H:%M:%S")
        if pd.notna(timestamp)
        else None
    )

    documento_gold = {
        "id_servidor": dados_bd["id_servidor"],
        "endereco_mac": dados_bd["endereco_mac"],
        "apelido": dados_bd["apelido"],
        "empresa": dados_bd["razao_social"],
        "localizacao_km": dados_bd["km"],
        "sentido": dados_bd["sentido"],
        "timestamp": timestamp_str,
        "metricas_monitorizadas": []
    }

    for comp in dados_bd["componentes"]:
        nome = comp["componente"].lower()

        valor_bruto = obter_valor_metrica(
            linha,
            nome
        )

        if valor_bruto is None:
            continue

        valor_convertido = converter_medida(
            valor_bruto,
            nome,
            comp["unidade_medida"]
        )

        limite_max = (
            float(comp["limite_max"])
            if comp["limite_max"] is not None
            else None
        )

        limite_min = (
            float(comp["limite_min"])
            if comp["limite_min"] is not None
            else None
        )

        em_alerta = (
            limite_max is not None
            and valor_convertido > limite_max
        ) or (
            limite_min is not None
            and valor_convertido < limite_min
        )

        nomes_exibicao = {
            "cpu": "CPU",
            "ram": "Memória RAM",
            "disco": "Disco",
            "placa_rede": "Placa de rede"
        }

        unidades_exibicao = {
            "cpu": "%",
            "ram": "GiB",
            "disco": "GiB",
            "placa_rede": comp["unidade_medida"]
        }

        documento_gold["metricas_monitorizadas"].append({
            "componente": nomes_exibicao.get(
                nome,
                comp["componente"]
            ),
            "unidade_medida": unidades_exibicao.get(
                nome,
                comp["unidade_medida"]
            ),
            "valor_medido": valor_convertido,
            "limite_min": limite_min,
            "limite_max": limite_max,
            "em_alerta": em_alerta
        })

    return documento_gold


def executar_etl_gold():
    print("Iniciando ETL Gold v01...")

    arquivo_silver = encontrar_arquivo_mais_recente_silver()
    print(f"Arquivo Silver: {arquivo_silver}")

    df_silver = ler_csv_silver(arquivo_silver)

    
    if "mac_address" in df_silver.columns:
        coluna_mac = "mac_address"
    elif "id_maquina" in df_silver.columns:
        coluna_mac = "id_maquina"
    else:
        raise ValueError(
            "A Silver não possui a coluna mac_address nem id_maquina."
        )

    df_silver[coluna_mac] = (
        df_silver[coluna_mac]
        .astype(str)
        .str.replace(":", "", regex=False)
        .str.replace("-", "", regex=False)
        .str.strip()
        .str.upper()
    )

    cache_servidores = {}

    timestamp_export = datetime.now().strftime("%Y%m%d_%H%M%S")

    for idx, linha in df_silver.iterrows():
        mac = linha[coluna_mac]

        if mac not in cache_servidores:
            cache_servidores[mac] = buscar_parametros_servidor(mac)

        dados_bd = cache_servidores[mac]

        if not dados_bd:
            print(f"Servidor MAC {mac} não encontrado. Registro ignorado.")
            continue

        json_gold = montar_json_gold(linha, dados_bd)

        chave_gold = (
            f"client/{mac}/"
            f"dados_{timestamp_export}_{idx}.json"
        )

        s3.put_object(
            Bucket=BUCKET_GOLD,
            Key=chave_gold,
            Body=json.dumps(
                json_gold,
                ensure_ascii=False,
                indent=2,
                allow_nan=False
            ).encode("utf-8"),
            ContentType="application/json"
        )

        print(f"Salvo no Gold: {chave_gold}")

    print("ETL Gold v01 finalizada!")


executar_etl_gold()