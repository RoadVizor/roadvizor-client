from database import buscar_enderecos, salvar_clima
from weather_api import consultar_clima


# Executa uma coleta climática para cada endereço cadastrado
def lambda_handler(event=None, context=None):

    enderecos = buscar_enderecos()

    if not enderecos:
        print("Nenhum endereço com coordenadas foi encontrado.")
        return []

    resultados = []

    for endereco in enderecos:

        latitude = float(endereco["latitude"])
        longitude = float(endereco["longitude"])

        clima = consultar_clima(latitude, longitude)

        if clima is not None:

            resultado = {
                "endereco_id": endereco["id_endereco"],
                "cidade": endereco["localidade"],
                "uf": endereco["uf"],
                "latitude": latitude,
                "longitude": longitude,
                "timestamp": clima["timestamp"],
                "temperatura": clima["temperatura"],
                "umidade": clima["umidade"],
                "vento": clima["vento"],
                "direcao_vento": clima["direcao_vento"],
                "precipitacao": clima["precipitacao"],
                "condicao": clima["condicao"]
            }

            salvar_clima(endereco["id_endereco"], clima)

            resultados.append(resultado)

            print("Coleta realizada:", resultado)
    return resultados
