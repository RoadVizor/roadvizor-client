
import requests


def buscar_coordenadas(cep, cidade, uf):

    url = "https://nominatim.openstreetmap.org/search"

    parametros = {
        "postalcode": cep,
        "city": cidade,
        "state": uf,
        "country": "Brazil",
        "format": "jsonv2",
        "limit": 3
    }

    cabecalho = {
        "User-Agent": "RoadVizor-MonitoramentoClimatico/1.0"
    }

    resposta = requests.get(
        url,
        params=parametros,
        headers=cabecalho,
        timeout=20
    )

    resposta.raise_for_status()

    resultados = resposta.json()

    if not resultados:
        return None

    return resultados
