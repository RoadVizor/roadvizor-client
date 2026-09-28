import boto3
import json

# Configurações do S3
s3 = boto3.client("s3", region_name="us-east-1")

# Se o seu bucket de destino no script foi diferente, ajuste o nome aqui
BUCKET_GOLD = "datalake-gold-roadvizor-sptech"
MAC_MAQUINA = "6C946647CDD1"
PREFIXO = f"client/{MAC_MAQUINA}/"

def testar_leitura_gold():
    print(f"Buscando arquivos JSON no caminho: s3://{BUCKET_GOLD}/{PREFIXO}...\n")
    
    # 1. Lista os objetos salvos para o seu MAC
    resposta = s3.list_objects_v2(Bucket=BUCKET_GOLD, Prefix=PREFIXO)

    if "Contents" not in resposta or len(resposta["Contents"]) == 0:
        print(f"Nenhum arquivo encontrado no caminho '{PREFIXO}'. Verifique se o upload da ETL realmente finalizou.")
        return

    # 2. Ordena e pega o arquivo gerado mais recentemente
    arquivo_mais_recente = max(resposta["Contents"], key=lambda x: x["LastModified"])
    chave_s3 = arquivo_mais_recente["Key"]

    print(f"✓ Arquivo encontrado: {chave_s3}")
    print(f"✓ Última modificação: {arquivo_mais_recente['LastModified']}")

    # 3. Faz o download do JSON em memória
    objeto_s3 = s3.get_object(Bucket=BUCKET_GOLD, Key=chave_s3)
    conteudo_bytes = objeto_s3["Body"].read()
    
    # 4. Decodifica e converte para dicionário Python
    dados_json = json.loads(conteudo_bytes.decode("utf-8"))

    # 5. Imprime o JSON formatado
    print("\n================================================")
    print("      CONTEÚDO DO JSON CONSUMIDO DA GOLD       ")
    print("================================================")
    print(json.dumps(dados_json, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    testar_leitura_gold()