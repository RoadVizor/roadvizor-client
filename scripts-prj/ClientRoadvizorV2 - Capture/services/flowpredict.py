import boto3
import json
import os




def buscar_dashboard(empresa_id):
    s3 = boto3.client("s3", region_name="us-east-1")

    chave = f"flowpredict/empresa_{empresa_id}/dashboard.json"

    resposta = s3.get_object(
        Bucket="datalake-gold-roadvizor-sptech",
        Key=chave
    )

    dados = json.loads(resposta["Body"].read().decode("utf-8"))

    return dados