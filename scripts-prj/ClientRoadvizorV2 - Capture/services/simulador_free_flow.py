# Bom, aqui é o simulador do pedagio
# é melhor pra gente para parecer que estamos pegando dados reais

import datetime
import random
import csv
import json

#Simular em tempo real
import time
import os

import sys

# Permite acessar os módulos da pasta principal do projeto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import conectar


# Busca todos os servidores cadastrados no banco
# Assim, o simulador não fica preso a um ID fixo

def buscar_servidores():

    conexao = conectar()
    cursor = conexao.cursor()

    try:
        cursor.execute("""
            SELECT id_servidor, apelido
            FROM servidor
            ORDER BY id_servidor
        """)

        return cursor.fetchall()

    finally:
        cursor.close()
        conexao.close()

# Esse aq serve pra gente poder ver os dias 
import holidays


# Leituras que ele vai produzir
QUANTIDADE_LEITURAS = 6000

# Um dia tem 24 horas. Cada leitura ganha um horário dentro desse período.
DURACAO_DIA_SEGUNDOS = 24 * 60 * 60

# Capacidades de referência da máquina
TOTAL_RAM_BYTES = 7 * 1024 ** 3
TOTAL_DISCO_BYTES = 468 * 1024 ** 3
TOTAL_SWAP_BYTES = 8 * 1024 ** 3
TOTAL_REDE_MBPS = 195

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

def gerar_metricas(fluxo, estado, numero_leitura, hora):

    # Agora cada registro representa um minuto real
    fluxo_por_minuto = fluxo

    # Mais veículos = mais transações para processar
    transacoes = max(0, fluxo + random.randint(-2, 3))

    # 20% de chance de sobrecarga por leitura
        # A sobrecarga é mais frequente nos horários de maior movimento
    if 0 <= hora < 5:
        chance_sobrecarga = 0.10

    elif (7 <= hora < 10) or (16 <= hora < 20):
        chance_sobrecarga = 0.25

    else:
        chance_sobrecarga = 0.22

    sobrecarga = random.random() < chance_sobrecarga

    # Falhas de comunicação podem acontecer independentemente do fluxo
    falha_comunicacao = random.random() < 0.008

    fator_sobrecarga = 1.45 if sobrecarga else 1.0

    # O fluxo influencia a CPU e a RAM
    cpu_alvo = 18 + fluxo_por_minuto * 0.75 * fator_sobrecarga
    ram_alvo = 30 + fluxo_por_minuto * 0.40 * fator_sobrecarga

    # Define latência e perda de pacotes
    if falha_comunicacao:
        latencia_alvo = random.uniform(150, 500)
        perda_pacotes = random.uniform(3, 18)
    else:
        latencia_alvo = 12 + fluxo * 0.12
        latencia_alvo += random.uniform(0, 12)
        perda_pacotes = random.uniform(0, 0.4)

    # Sobrecarga coloca CPU e RAM em estado crítico
    if sobrecarga:
        estado["cpu"] = random.uniform(90, 98)
        estado["ram"] = random.uniform(90, 96)

    # Sem sobrecarga, as métricas variam suavemente
    else:
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

    # Atualiza a latência independentemente da sobrecarga
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

# Prepara os dados do dia para calcular o fluxo por minuto

def inicializar_dia(data):
    tipo_dia, nome_feriado = identificar_tipo_dia(data)

    total_diario = random.randint(1500, 25000)

    if tipo_dia == "FERIADO":
        total_diario *= 2

    soma_pesos = 0

    for minuto in range(1440):
        horario = data.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        ) + datetime.timedelta(minutes=minuto)

        soma_pesos += obter_peso_horario(horario.hour)

    return tipo_dia, nome_feriado, total_diario, soma_pesos


# Calcula quantos veículos passam durante aquele minuto

def calcular_fluxo_minuto(hora, total_diario, soma_pesos):
    peso = obter_peso_horario(hora)

    fluxo = round(total_diario * peso / soma_pesos)

    return max(0, fluxo)


# Gera somente uma leitura usando o horário atual

def gerar_leitura(estado, numero_leitura, dados_dia):
    agora = datetime.datetime.now()

    tipo_dia, nome_feriado, total_diario, soma_pesos = dados_dia

    fluxo = calcular_fluxo_minuto(
        agora.hour,
        total_diario,
        soma_pesos
    )

    metricas, processos, operacao = gerar_metricas(
        fluxo,
        estado,
        numero_leitura,
        agora.hour
    )

    leitura = {
        "timestamp": agora.strftime("%Y-%m-%d %H:%M:%S"),
        "tipo_dia": tipo_dia,
        "feriado": nome_feriado or "",
        "metricas": metricas,
        "processos": processos,
        "operacao": operacao
    }

    return leitura


# Transforma uma leitura no formato de colunas do CSV

# Transforma dicionários dentro de dicionários em colunas do CSV
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

# Transforma dicionários dentro de dicionários em colunas do CSV
# Organiza uma leitura em uma linha do CSV
def preparar_linha(leitura, servidor_id, apelido):
    linha = {
        "servidor_id": servidor_id,
        "servidor": apelido,
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

    return linha


# Salva cada leitura no mesmo arquivo CSV

def salvar_leitura_csv(leitura, nome_arquivo, primeira_leitura, servidor_id, apelido):
    linha = preparar_linha(leitura, servidor_id, apelido)

    with open(
        nome_arquivo,
        "a",
        newline="",
        encoding="utf-8-sig" if primeira_leitura else "utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=list(linha.keys()),
            delimiter=";"
        )

        if primeira_leitura:
            escritor.writeheader()

        escritor.writerow(linha)


# Salva a leitura no banco de dados
def salvar_leitura_log(leitura, servidor_id):
    conexao = conectar()
    cursor = conexao.cursor()

    try:
        titulo = f"Leitura Free Flow - {leitura['timestamp']}"
        descricao = json.dumps(leitura, ensure_ascii=False, default=str)
        status = leitura["operacao"]["cenario"]

        sql = """
            INSERT INTO log (servidor_id, titulo, descricao, status)
            VALUES (%s, %s, %s, %s)
        """

        cursor.execute(sql, (
            servidor_id,
            titulo,
            descricao,
            status
        ))

        conexao.commit()

    except Exception:
        conexao.rollback()
        raise

    finally:
        cursor.close()
        conexao.close()


# Executa a captura continuamente até receber Ctrl + C

if __name__ == "__main__":

    agora = datetime.datetime.now()

    # Cada servidor terá seu próprio estado de métricas e fluxo diário
    estados_servidores = {}

    servidores = buscar_servidores()

    if not servidores:
        print("Nenhum servidor cadastrado foi encontrado.")
        sys.exit()

    nome_arquivo = (
        "leituras_free_flow_"
        + agora.strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )

    print("MONITORAMENTO FREE FLOW INICIADO")
    print("Servidores encontrados:", len(servidores))
    print("Arquivo:", nome_arquivo)
    print("Captura a cada 1 minuto.")
    print("Pressione Ctrl + C para encerrar.\n")

    primeira_leitura = True

    try:
        while True:

            agora = datetime.datetime.now()

            # Consulta novamente para identificar servidores cadastrados durante a execução
            servidores = buscar_servidores()

            for servidor in servidores:

                servidor_id = servidor[0]
                apelido = servidor[1] or f"Servidor {servidor_id}"

                # Inicializa um estado independente para cada servidor novo
                if servidor_id not in estados_servidores:

                    estados_servidores[servidor_id] = {
                        "estado": {
                            "cpu": 25.0,
                            "ram": 35.0,
                            "disco": 45.0,
                            "latencia": 25.0,
                            "bytes_enviados": 0,
                            "bytes_recebidos": 0
                        },
                        "dados_dia": inicializar_dia(agora),
                        "data": agora.date(),
                        "numero_leitura": 0
                    }

                    print("Novo servidor no monitoramento:", apelido)

                dados_servidor = estados_servidores[servidor_id]

                # Atualiza o cenário diário quando a data mudar
                if dados_servidor["data"] != agora.date():
                    dados_servidor["dados_dia"] = inicializar_dia(agora)
                    dados_servidor["data"] = agora.date()

                dados_servidor["numero_leitura"] += 1

                try:
                    leitura = gerar_leitura(
                        dados_servidor["estado"],
                        dados_servidor["numero_leitura"],
                        dados_servidor["dados_dia"]
                    )

                    # Inclui a identificação do servidor dentro do JSON
                    leitura["servidor_id"] = servidor_id
                    leitura["servidor"] = apelido

                    # Salva a leitura vinculada ao servidor correto
                    salvar_leitura_log(leitura, servidor_id)

                    salvar_leitura_csv(
                        leitura,
                        nome_arquivo,
                        primeira_leitura,
                        servidor_id,
                        apelido
                    )

                    primeira_leitura = False

                    print(
                        f"Servidor {servidor_id} - {apelido} | "
                        f"Leitura {dados_servidor['numero_leitura']} | "
                        f"{leitura['timestamp']} | "
                        f"Veículos: {leitura['operacao']['veiculos']} | "
                        f"CPU: {leitura['metricas']['psutil.cpu_percent()']}% | "
                        f"RAM: {leitura['metricas']['psutil.virtual_memory()']['percent']}% | "
                        f"Cenário: {leitura['operacao']['cenario']}"
                    )

                except Exception as erro:
                    print(
                        f"Erro ao processar o servidor {servidor_id} "
                        f"({apelido}): {erro}"
                    )

            # Aguarda até o próximo minuto do relógio
            proxima_leitura = (
                datetime.datetime.now().replace(
                    second=0,
                    microsecond=0
                ) + datetime.timedelta(minutes=1)
            )

            espera = (
                proxima_leitura - datetime.datetime.now()
            ).total_seconds()

            time.sleep(max(0, espera))

    except KeyboardInterrupt:
        print("\n\nCaptura interrompida pelo usuário.")

        total_leituras = sum(
            dados["numero_leitura"]
            for dados in estados_servidores.values()
        )

        print("Total de leituras por servidor:")

        for servidor_id, dados in estados_servidores.items():
            print(
                f"Servidor {servidor_id}: "
                f"{dados['numero_leitura']} leituras"
            )

        print("Total geral de leituras:", total_leituras)
        print("Arquivo salvo:", nome_arquivo)
