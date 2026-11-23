import csv
import os
from datetime import datetime


# Pasta onde os arquivos CSV serão armazenados
PASTA_DADOS = "dados/clima"


# Salva a leitura climática no arquivo CSV do dia
def salvar_csv(clima):

    os.makedirs(PASTA_DADOS, exist_ok=True)

    data_atual = datetime.now().strftime("%Y-%m-%d")

    nome_arquivo = f"clima_{data_atual}.csv"

    caminho_arquivo = os.path.join(PASTA_DADOS, nome_arquivo)

    arquivo_existe = os.path.exists(caminho_arquivo)

    campos = [
        "endereco_id",
        "cidade",
        "uf",
        "latitude",
        "longitude",
        "timestamp",
        "temperatura",
        "umidade",
        "vento",
        "direcao_vento",
        "precipitacao",
        "condicao"
    ]

    with open(caminho_arquivo, "a", newline="", encoding="utf-8-sig") as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos,
            delimiter=";"
        )

        # Cria o cabeçalho somente na primeira gravação
        if not arquivo_existe:
            escritor.writeheader()

        escritor.writerow(clima)

    print("Leitura salva no CSV:", caminho_arquivo)
