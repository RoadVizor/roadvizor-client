import boto3
import pandas as pd
import mysql.connector
import json
from io import BytesIO
from datetime import datetime

# CONFIGURAÇÕES AWS S3
s3 = boto3.client("s3", region_name="us-east-1")

BUCKET_SILVER = "datalake-silver-roadvizor-sptech"
BUCKET_GOLD = "datalake-gold-roadvizor-sptech"

# CONFIGURAÇÕES DO BANCO DE DADOS
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
    resposta = s3.list_objects_v2(Bucket=BUCKET_SILVER)
    arquivos = [obj for obj in resposta.get("Contents", []) if obj["Key"].endswith(".csv")]
    
    if not arquivos:
        raise Exception("Nenhum CSV encontrado no bucket Silver.")

    arquivo_mais_recente = max(arquivos, key=lambda x: x["Key"])
    return arquivo_mais_recente["Key"]

def ler_csv_silver(chave):
    resposta = s3.get_object(Bucket=BUCKET_SILVER, Key=chave)
    conteudo = resposta["Body"].read()
    return pd.read_csv(BytesIO(conteudo), sep=";")

def buscar_parametros_servidor(mac_address):
    """Busca dados e limites do servidor no banco de dados"""
    conn = obter_conexao_db()
    try:
        with conn.cursor(dictionary=True) as cursor:
            # Query do servidor e endereço
            sql_servidor = """
                SELECT 
                    s.id_servidor, s.endereco_mac, s.apelido, s.modelo,
                    e.razao_social, e.cnpj,
                    ed.logradouro, ed.numero, ed.bairro, ed.localidade, ed.uf, ed.km, ed.sentido
                FROM servidor s
                JOIN empresa e ON s.empresa_id = e.id_empresa
                LEFT JOIN endereco ed ON s.endereco_id = ed.id_endereco
                WHERE s.endereco_mac = %s
            """
            cursor.execute(sql_servidor, (mac_address,))
            servidor = cursor.fetchone()

            if not servidor:
                return None

            # Query dos componentes e limites
            sql_componentes = """
                SELECT 
                    c.nome AS componente,
                    c.unidade_medida,
                    sc.limite_min,
                    sc.limite_max,
                    sc.capacidade_total,
                    sc.monitorado
                FROM servidor_componente sc
                JOIN componente c ON sc.componente_id = c.id_componente
                WHERE sc.servidor_id = %s AND sc.monitorado = 1
            """
            cursor.execute(sql_componentes, (servidor["id_servidor"],))
            servidor["componentes"] = cursor.fetchall()
            return servidor
    finally:
        conn.close()

def converter_medida(valor_original, nome_componente, unidade_destino):
    if valor_original is None:
        return None
        
    nome_comp_lower = nome_componente.lower()
    unidade = str(unidade_destino).strip().upper()
    
    # Conversões baseadas nas unidades permitidas no banco  (MiB, GiB, TiB, pct)
    if 'ram' in nome_comp_lower or 'disco' in nome_comp_lower:
        if unidade in ['GIB', 'GB']:
            return round(valor_original / (1024 ** 3), 2)
        elif unidade in ['MIB', 'MB']:
            return round(valor_original / (1024 ** 2), 2)
        elif unidade in ['TIB', 'TB']:
            return round(valor_original / (1024 ** 4), 2)
            
    # Para 'pct' (porcentagem), 'Mbps' ou 'ms', mantém o número direto
    return round(float(valor_original), 2)

def montar_json_gold(linha, dados_bd):
    timestamp_str = str(linha["timestamp"]) if pd.notna(linha["timestamp"]) else None

    # O payload base não contém as métricas brutas fixas
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

    # O laço for acontece APENAS para os componentes com monitorado = 1
    for comp in dados_bd.get("componentes", []):
        nome = comp["componente"].lower()
        unidade_medida = comp["unidade_medida"]
        
        valor_bruto = None
        if "cpu" in nome:
            valor_bruto = float(linha["cpu"])
        elif "ram" in nome:
            valor_bruto = float(linha["ram_uso"])
        elif "disco" in nome or "disk" in nome:
            valor_bruto = float(linha["disco_uso"])
        elif "latencia" in nome or "rede" in nome:
            valor_bruto = float(linha["latencia"])

        # Se o valor existir no CSV, aplica a conversão e inclui no JSON
        if valor_bruto is not None:
            valor_convertido = converter_medida(valor_bruto, nome, unidade_medida)
            limite_max = float(comp["limite_max"]) if comp["limite_max"] is not None else None
            limite_min = float(comp["limite_min"]) if comp["limite_min"] is not None else None
            
            em_alerta = False
            if limite_max is not None and valor_convertido > limite_max:
                em_alerta = True
            if limite_min is not None and valor_convertido < limite_min:
                em_alerta = True

            documento_gold["metricas_monitorizadas"].append({
                "componente": comp["componente"],
                "unidade_medida": unidade_medida,
                "valor_medido": valor_convertido,
                "limite_min": limite_min,
                "limite_max": limite_max,
                "em_alerta": em_alerta
            })

    return documento_gold

def executar_etl_gold():
    print("=================================")
    print("       ETL GOLD - V1 (Local)")
    print("=================================")

    print("Procurando CSV mais recente no Silver...")
    arquivo_silver = encontrar_arquivo_mais_recente_silver()
    print(f"Arquivo selecionado: {arquivo_silver}")

    df_silver = ler_csv_silver(arquivo_silver)
    cache_servidores = {}

    for idx, linha in df_silver.iterrows():
        mac = str(linha["id_maquina"]).replace(":", "").upper()

        if mac not in cache_servidores:
            cache_servidores[mac] = buscar_parametros_servidor(mac)

        dados_bd = cache_servidores[mac]

        if dados_bd:
            # Transforma e gera o JSON
            json_gold = montar_json_gold(linha, dados_bd)
            
            # Formata o nome do arquivo de saída
            timestamp_export = datetime.now().strftime("%Y%m%d_%H%M%S")
            chave_gold = f"client/{mac}/dados_{timestamp_export}_{idx}.json"
            
            # Envia para o bucket Gold
            s3.put_object(
                Bucket=BUCKET_GOLD,
                Key=chave_gold,
                Body=json.dumps(json_gold, ensure_ascii=False, indent=2).encode("utf-8"),
                ContentType="application/json"
            )
            print(f"Salvo no Gold: {chave_gold}")
        else:
            print(f"Aviso: Servidor MAC {mac} não encontrado. Registro ignorado.")

    print("\nETL Finalizada com sucesso!")


executar_etl_gold()