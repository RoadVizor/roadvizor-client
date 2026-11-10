import os
import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error
import boto3
import pandas as pd
from io import BytesIO
import numpy as np

load_dotenv()

COLUNAS_COMPONENTES = {
    "cpu": "cpu",
    "ram": "ram_used"
}


def buscar_servidores():
    try:
        conexao = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME")
        )

        cursor = conexao.cursor(dictionary=True)

        query = """
            SELECT
                s.id_servidor,
                s.empresa_id,
                s.endereco_mac,
                s.apelido,
                s.modelo,
                c.id_componente,
                c.nome AS componente,
                c.unidade_medida,
                c.depende_de,
                sc.limite_min,
                sc.limite_max,
                sc.capacidade_total
            FROM servidor s
            INNER JOIN servidor_componente sc
                ON sc.servidor_id = s.id_servidor
                AND sc.empresa_id = s.empresa_id
            INNER JOIN componente c
                ON c.id_componente = sc.componente_id
            WHERE sc.monitorado = 1;
        """

        cursor.execute(query)
        servidores = cursor.fetchall()

        return servidores

    except Error as erro:
        print(f"Erro ao consultar o banco: {erro}")
        return []

    finally:
        if 'cursor' in locals():
            cursor.close()

        if 'conexao' in locals() and conexao.is_connected():
            conexao.close()
            

def buscar_dados_silver(mac):
    bucket = os.getenv("SILVER_BUCKET")
    regiao = os.getenv("AWS_REGION", "us-east-1")

    s3 = boto3.client("s3", region_name=regiao)

    prefixo = f"trusted/{mac.replace(':', '-')}/"

    try:
        resposta = s3.list_objects_v2(
            Bucket=bucket,
            Prefix=prefixo
        )

        arquivos = []

        while True:
            for objeto in resposta.get("Contents", []):
                if objeto["Key"].endswith(".csv"):
                    arquivo = s3.get_object(
                        Bucket=bucket,
                        Key=objeto["Key"]
                    )

                    df = pd.read_csv(
                        BytesIO(arquivo["Body"].read()),
                        sep=";"
                    )

                    arquivos.append(df)

            if not resposta.get("IsTruncated"):
                break

            resposta = s3.list_objects_v2(
                Bucket=bucket,
                Prefix=prefixo,
                ContinuationToken=resposta["NextContinuationToken"]
            )

        if not arquivos:
            print(f"Nenhum arquivo Silver encontrado para {mac}.")
            return pd.DataFrame()

        dados = pd.concat(arquivos, ignore_index=True)

        dados["timestamp"] = pd.to_datetime(dados["timestamp"])

        dados = dados.sort_values("timestamp")

        return dados

    except Exception as erro:
        print(f"Erro ao buscar dados Silver de {mac}: {erro}")
        return pd.DataFrame()


def main():
    servidores = buscar_servidores()

    if not servidores:
        print("Nenhum servidor monitorado encontrado.")
        return

    print(f"Servidores e componentes encontrados: {len(servidores)}")

    macs = set(
        servidor["endereco_mac"]
        for servidor in servidores
    )

    for mac in macs:
        dados = buscar_dados_silver(mac)

        print(f"\nHistórico do servidor: {mac}")
        print(f"Registros encontrados: {len(dados)}")

        if dados.empty:
            continue

        componentes_servidor = [
            servidor for servidor in servidores
            if servidor["endereco_mac"] == mac
        ]

        for componente in componentes_servidor:
            nome = componente["componente"].lower()
            coluna = COLUNAS_COMPONENTES.get(nome)

            if coluna is None:
                print(f"Componente sem mapeamento: {nome}")
                continue

            if coluna not in dados.columns:
                print(f"Coluna {coluna} não encontrada no Silver.")
                continue

            limite_max = float(componente["limite_max"])

            dados_analise = dados[["timestamp", coluna]].copy()

            if componente["unidade_medida"] == "GiB":
                dados_analise[coluna] = dados_analise[coluna] / (1024 ** 3)

            dados_analise = dados_analise.rename(
                columns={coluna: "valor"}
            )

            dados_analise["valor"] = pd.to_numeric(
                dados_analise["valor"],
                errors="coerce"
            )

            dados_analise = dados_analise.dropna(subset=["valor"])

            if dados_analise.empty:
                print(f"Nenhum valor válido encontrado para {nome}.")
                continue

            dados_analise["acima_limite"] = (
                dados_analise["valor"] > limite_max
            )

            ocorrencias = dados_analise["acima_limite"].sum()

            primeiro_valor = dados_analise["valor"].iloc[0]
            ultimo_valor = dados_analise["valor"].iloc[-1]
            variacao = ultimo_valor - primeiro_valor

            media = dados_analise["valor"].mean()
            desvio_padrao = dados_analise["valor"].std()

            utilizacao_limite = (media / limite_max) * 100

            if utilizacao_limite < 50:
                risco = "BAIXO"
            elif utilizacao_limite < 75:
                risco = "MODERADO"
            elif utilizacao_limite < 90:
                risco = "ALTO"
            else:
                risco = "CRÍTICO"

            tempo_minutos = (
                dados_analise["timestamp"] - dados_analise["timestamp"].iloc[0]
            ).dt.total_seconds() / 60

            if tempo_minutos.nunique() > 1:
                inclinacao = np.polyfit(
                    tempo_minutos,
                    dados_analise["valor"],
                    1
                )[0]

                if inclinacao > 0:
                    tendencia = "CRESCENTE"
                elif inclinacao < 0:
                    tendencia = "DECRESCENTE"
                else:
                    tendencia = "ESTÁVEL"

            else:
                tendencia = "INDEFINIDA"
                inclinacao = 0

            print(f"\nAnalisando: {nome}")
            print(f"Coluna: {coluna}")
            print(f"Limite máximo: {limite_max:.2f} {componente['unidade_medida']}")
            print(f"Primeiro consumo: {primeiro_valor:.2f}")
            print(f"Último consumo: {ultimo_valor:.2f}")
            print(f"Variação do consumo: {variacao:+.2f}")
            print(f"Média de consumo: {media:.2f}")
            print(f"Desvio padrão: {desvio_padrao:.2f}")
            print(f"Maior valor registrado: {dados_analise['valor'].max():.2f}")
            print(f"Registros acima do limite: {ocorrencias}")
            print(f"Total de registros analisados: {len(dados_analise)}")
            print(f"Utilização média do limite: {utilizacao_limite:.2f}%")
            print(f"Classificação de risco: {risco}")
            print(f"Tendência: {tendencia}")
            print(f"Inclinação: {inclinacao:.4f} por minuto")


if __name__ == "__main__":
    main()
