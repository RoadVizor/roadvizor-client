
import requests

URL_API = "https://api.open-meteo.com/v1/forecast"


# Traduz o código meteorológico da Open-Meteo
def identificar_clima(codigo):

    if codigo == 0:
        return "CEU_LIMPO"

    elif codigo in [1, 2]:
        return "PARCIALMENTE_NUBLADO"

    elif codigo == 3:
        return "NUBLADO"

    elif codigo in [45, 48]:
        return "NEBLINA"

    elif codigo in [51, 53, 55, 56, 57]:
        return "GAROA"

    elif codigo in [61, 63, 65, 66, 67]:
        return "CHUVOSO"

    elif codigo in [71, 73, 75, 77, 85, 86]:
        return "NEVE"

    elif codigo in [80, 81, 82]:
        return "PANCADAS_DE_CHUVA"

    elif codigo in [95, 96, 99]:
        return "TEMPESTADE"

    return "INDEFINIDO"


# Consulta as condições climáticas pelas coordenadas
def consultar_clima(latitude, longitude):

    parametros = {
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "wind_speed_10m,"
            "wind_direction_10m,"
            "precipitation,"
            "weather_code"
        ),
        "timezone": "America/Sao_Paulo"
    }

    try:

        resposta = requests.get(
            URL_API,
            params=parametros,
            timeout=15
        )

        resposta.raise_for_status()

        dados = resposta.json()

        clima = dados["current"]

        leitura = {
            "timestamp": clima["time"].replace("T", " "),
            "temperatura": clima["temperature_2m"],
            "umidade": clima["relative_humidity_2m"],
            "vento": clima["wind_speed_10m"],
            "direcao_vento": clima["wind_direction_10m"],
            "precipitacao": clima["precipitation"],
            "codigo_clima": clima["weather_code"],
            "condicao": identificar_clima(clima["weather_code"])
        }

        return leitura

    except (requests.RequestException, KeyError, ValueError) as erro:

        print("Erro ao consultar o clima:", erro)

        return None

