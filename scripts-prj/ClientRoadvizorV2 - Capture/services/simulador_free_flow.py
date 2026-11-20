# Bom, aqui é o simulador do pedagio
# é melhor pra gente para parecer que estamos pegando dados reais

import datetime
import random
import csv
import json

# Esse aq serve pra gente poder ver os dias 
import holidays


# Leituras que ele vai produzir
QUANTIDADE_LEITURAS = 6000

# Um dia tem 24 horas. Cada leitura ganha um horário dentro desse período.
DURACAO_DIA_SEGUNDOS = 24 * 60 * 60

# Valores fictícios para representar um servidor de pedágio.
TOTAL_RAM_BYTES = 16 * 1024 ** 3
TOTAL_DISCO_BYTES = 500 * 1024 ** 3
TOTAL_SWAP_BYTES = 4 * 1024 ** 3
TOTAL_REDE_MBPS = 1000

# Pega os feriados do Brasil considerando SP.
# E identifica se é feriado ou n
def identificar_tipo_dia(data):

    calendario = holidays.country_holidays(
        "BR",
        subdiv="SP",
        years=[data.year]
    )

    if data.date() in calendario:
        return "FERIADO", calendario.get(data.date())

    if data.weekday() >= 5:
        return "FIM_DE_SEMANA", ""

    return "DIA_UTIL", ""

#Dá mais peso aos horários movimentados e menos peso à madrugada

def obter_peso_horario(hora):

    if 0 <= hora < 5:
        return 0.20

    if 5 <= hora < 7:
        return 0.55

    if 7 <= hora < 10:
        return 1.25

    if 10 <= hora < 16:
        return 0.85

    if 16 <= hora < 20:
        return 1.45

    if 20 <= hora < 23:
        return 0.75

    return 0.40

# Segura os valores dentro de uma faixa válida
# Até pq é uma simulação, n bagunça
def limitar(valor, minimo, maximo):

    return max(minimo, min(valor, maximo))


# Faz a métrica se aproximar do alvo sem dar saltos absurdos
# Para a gente conseguir ir medindo
def variar_suavemente(valor_anterior, alvo, intensidade=0.18):

    diferenca = (alvo - valor_anterior) * intensidade
    oscilacao = random.uniform(-1.5, 1.5)

    return valor_anterior + diferenca + oscilacao


# Distribui o total diário entre as leituras
# O movimento fica concentrado nos horários de pico. ,
# Em feriadoso total diário é dobrado 
def distribuir_veiculos(data_inicio, tipo_dia):

    # Escolhe um volume diário
    total_diario = random.randint(1500, 25000)

    if tipo_dia == "FERIADO":
        total_diario *= 2

    pesos = []

    for numero in range(QUANTIDADE_LEITURAS):
        segundos = numero * DURACAO_DIA_SEGUNDOS / QUANTIDADE_LEITURAS
        horario = data_inicio + datetime.timedelta(seconds=segundos)

        peso = obter_peso_horario(horario.hour)

        # Uma variação pequena deixa cada dia com uma distribuição diferente.
        peso *= random.uniform(0.80, 1.20)
        pesos.append(peso)

    soma_pesos = sum(pesos)

    # Primeiro distribui proporcionalmente
    # depois corrige a diferença
    # de arredondamento para fechar exatamente o total diário
    veiculos = [
        int(total_diario * peso / soma_pesos)
        for peso in pesos
    ]

    diferenca = total_diario - sum(veiculos)

    for indice in range(diferenca):
        veiculos[indice % QUANTIDADE_LEITURAS] += 1

    return veiculos, total_diario

#Transforma o movimento de veículos em métricas coerentes do servidor
def gerar_metricas(fluxo, estado, numero_leitura):

    # Cada registro cobre cerca de 14,4 segundos. Convertendo para uma taxa
    # por minuto, conseguimos comparar o movimento com uma carga de servidor
    fluxo_por_minuto = fluxo * 60 / (
        DURACAO_DIA_SEGUNDOS / QUANTIDADE_LEITURAS
    )

    # Mais veículos = mais transações para processar
    transacoes = max(0, fluxo + random.randint(-2, 3))

    # Às vezes o servidor sofre uma sobrecarga
    #  N acontece em toda leitura
    sobrecarga = random.random() < 0.012

    # Falhas podem acontecer independentemente do volume de veículos.
    falha_comunicacao = random.random() < 0.008

    fator_sobrecarga = 1.45 if sobrecarga else 1.0

    # O fluxo influencia a CPU e a RAM; a sobrecarga dá um empurrão extra.
    cpu_alvo = 18 + fluxo_por_minuto * 0.75 * fator_sobrecarga
    ram_alvo = 30 + fluxo_por_minuto * 0.40 * fator_sobrecarga

    if falha_comunicacao:
        latencia_alvo = random.uniform(150, 500)
        perda_pacotes = random.uniform(3, 18)
    else:
        latencia_alvo = 12 + fluxo * 0.12
        latencia_alvo += random.uniform(0, 12)
        perda_pacotes = random.uniform(0, 0.4)

    estado["cpu"] = limitar(
        variar_suavemente(estado["cpu"], cpu_alvo),
        5,
        99
    )

    estado["ram"] = limitar(
        variar_suavemente(estado["ram"], ram_alvo),
        15,
        96
    )

    estado["latencia"] = limitar(
        variar_suavemente(estado["latencia"], latencia_alvo, 0.35),
        5,
        500
    )

    # O disco cresce aos poucos conforme o servidor acumula registros
    # Eu nem me lembro se  agente monitora isso mas eu coloquei 
    # Por preucaução 
    estado["disco"] = limitar(
        estado["disco"] + random.uniform(0.0001, 0.0015),
        35,
        90
    )

    # Contadores de rede acumulados, como os que uma ferramenta de
    # monitoramento encontraria ao consultar as estatísticas 
    bytes_rede = transacoes * random.randint(1800, 4500)
    estado["bytes_enviados"] += bytes_rede
    estado["bytes_recebidos"] += bytes_rede * random.uniform(1.2, 2.0)

    ram_usada = int(TOTAL_RAM_BYTES * estado["ram"] / 100)
    disco_usado = int(TOTAL_DISCO_BYTES * estado["disco"] / 100)

    swap_percent = limitar((estado["ram"] - 65) * 1.2, 0, 85)
    swap_usada = int(TOTAL_SWAP_BYTES * swap_percent / 100)

    # Mantemos os identificadores iguais aos que o clientV02 já conhece
    # Assim, a API continua escolhendo quais componentes entram no CSV
    metricas = {
        "psutil.cpu_percent()": round(estado["cpu"], 2),

        "psutil.virtual_memory()": {
            "total": TOTAL_RAM_BYTES,
            "available": TOTAL_RAM_BYTES - ram_usada,
            "percent": round(estado["ram"], 2),
            "used": ram_usada,
            "free": TOTAL_RAM_BYTES - ram_usada
        },

        'psutil.disk_usage("/")': {
            "total": TOTAL_DISCO_BYTES,
            "used": disco_usado,
            "free": TOTAL_DISCO_BYTES - disco_usado,
            "percent": round(estado["disco"], 2)
        },

        "psutil.net_if_stats()": {
            "eth0": {
                "isup": not falha_comunicacao,
                "duplex": 2,
                "speed": TOTAL_REDE_MBPS,
                "mtu": 1500
            }
        },

        "psutil.net_io_counters()": {
            "bytes_sent": int(estado["bytes_enviados"]),
            "bytes_recv": int(estado["bytes_recebidos"]),
            "packets_sent": numero_leitura * 8 + transacoes,
            "packets_recv": numero_leitura * 10 + transacoes,
            "errin": 1 if falha_comunicacao else 0,
            "errout": 1 if falha_comunicacao else 0,
            "dropin": 1 if falha_comunicacao else 0,
            "dropout": 1 if falha_comunicacao else 0
        },

        "psutil.swap_memory()": {
            "total": TOTAL_SWAP_BYTES,
            "used": swap_usada,
            "free": TOTAL_SWAP_BYTES - swap_usada,
            "percent": round(swap_percent, 2),
            "sin": 0,
            "sout": 0
        },

        "psutil.disk_io_counters()": {
            "read_count": numero_leitura * 3 + transacoes,
            "write_count": numero_leitura * 5 + transacoes,
            "read_bytes": numero_leitura * 1024 * 120,
            "write_bytes": numero_leitura * 1024 * 180
        }
    }

    # A estrutura de processos segue o formato que o ETL Silver já espera
    processos = {
        "total": random.randint(35, 55),
        "rodando": random.randint(8, 18),
        "top_cpu": {
            "pid": 120,
            "nome": "freeflow-engine",
            "cpu_pct": round(estado["cpu"] * 0.35, 2),
            "ram_mb": round(ram_usada / (1024 ** 2) * 0.12, 2),
            "ram_pct": round(estado["ram"] * 0.12, 2),
            "threads": random.randint(4, 12),
            "status": "running"
        },
        "top_ram": {
            "pid": 145,
            "nome": "transaction-worker",
            "cpu_pct": round(estado["cpu"] * 0.20, 2),
            "ram_mb": round(ram_usada / (1024 ** 2) * 0.18, 2),
            "ram_pct": round(estado["ram"] * 0.18, 2),
            "threads": random.randint(3, 10),
            "status": "running"
        },
        "top_processos_cpu": []
    }

    operacao = {
        "cenario": identificar_cenario(fluxo_por_minuto),
        "veiculos": fluxo,
        "transacoes": transacoes,
        "sobrecarga": sobrecarga,
        "falha_comunicacao": falha_comunicacao,
        "latencia_ms": round(estado["latencia"], 2),
        "perda_pacotes_pct": round(perda_pacotes, 2)
    }

    return metricas, processos, operacao


# Só classifica o movimento para facilitar a análise depois
# Até pq analise exploratoria é osso
def identificar_cenario(fluxo_por_minuto):

    if fluxo_por_minuto <= 3:
        return "MADRUGADA"

    if fluxo_por_minuto >= 15:
        return "HORARIO_PICO"

    return "NORMAL"


# Gera o dia inteiro de uma vez em 24 h
# cada registro já recebe seu tempo correspondente
def gerar_leituras(data_inicio=None):

    if data_inicio is None:
        agora = datetime.datetime.now()
        data_inicio = agora.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    tipo_dia, nome_feriado = identificar_tipo_dia(data_inicio)

    veiculos_por_leitura, total_diario = distribuir_veiculos(
        data_inicio,
        tipo_dia
    )

    # As métricas começam em níveis plausíveis e vão mudando
    # gradualmente durante o dia, em vez de sortear tudo do zero a cada vez
    # E assim temos um servidor funcionando bonitin
    estado = {
        "cpu": 25.0,
        "ram": 35.0,
        "disco": 45.0,
        "latencia": 25.0,
        "bytes_enviados": 0,
        "bytes_recebidos": 0
    }

    leituras = []

    for numero in range(QUANTIDADE_LEITURAS):
        # Distribui os registros ao longo das 24 horas completas
        segundos = numero * DURACAO_DIA_SEGUNDOS / QUANTIDADE_LEITURAS
        timestamp = data_inicio + datetime.timedelta(seconds=segundos)

        fluxo = veiculos_por_leitura[numero]

        metricas, processos, operacao = gerar_metricas(
            fluxo,
            estado,
            numero
        )

        leituras.append({
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "tipo_dia": tipo_dia,
            "feriado": nome_feriado or "",
            "metricas": metricas,
            "processos": processos,
            "operacao": operacao
        })

    print(f"Leituras geradas: {len(leituras)}")
    print(f"Passagens de veículos no dia: {total_diario}")
    print(f"Tipo de dia: {tipo_dia}")

    return leituras


def achatar_dicionario(dados, prefixo=""):
    resultado = {}

    for chave, valor in dados.items():
        nome_coluna = f"{prefixo}_{chave}" if prefixo else chave

        if isinstance(valor, dict):
            resultado.update(
                achatar_dicionario(valor, nome_coluna)
            )
        else:
            resultado[nome_coluna] = valor

    return resultado

# Aq é para transformar em csv
def salvar_csv(leituras):
    nome_arquivo = (
        "leituras_free_flow_"
        + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )

    linhas = []

    for leitura in leituras:
        linha = {
            "timestamp": leitura["timestamp"],
            "tipo_dia": leitura["tipo_dia"],
            "feriado": leitura["feriado"],
            "processos": json.dumps(
                leitura["processos"],
                ensure_ascii=False
            )
        }

        linha.update(
            achatar_dicionario(leitura["metricas"], "metricas")
        )

        linha.update(
            achatar_dicionario(leitura["operacao"], "operacao")
        )

        linhas.append(linha)

    colunas = list(linhas[0].keys())

    with open(
        nome_arquivo,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=colunas,
            delimiter=";"
        )

        escritor.writeheader()
        escritor.writerows(linhas)

    print(f"\nArquivo CSV criado: {nome_arquivo}")
    print(f"Total de registros salvos: {len(linhas)}")

#  Executando este arquivo diretamente, ele gera as leituras
# e mostra alguns registros para conferirmos se está tudo em ordem
if __name__ == "__main__":
    dados = gerar_leituras()

    salvar_csv(dados)

    print("\nPrimeira leitura:")
    print(dados[0])

    print("\nÚltima leitura:")
    print(dados[-1])