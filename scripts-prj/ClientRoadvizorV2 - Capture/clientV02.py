import csv
import datetime
import io
import json
import os
import platform
import random
import re
import socket
import time
import unicodedata

import boto3
import holidays
import psutil
import requests
from getmac import get_mac_address


# Configurações
API_BASE_URL = os.getenv("ROADVIZOR_API_URL", "http://127.0.0.1:5000")
BUCKET_NAME = "datalake-bronze-roadvizor-sptech"
AWS_REGION = "us-east-1"
QTD_LEITURAS = 10
INTERVALO_LEITURAS = 1
INTERVALO_PROCESSOS = 0.3
TOP_N_PROCESSOS = 5
SEPARADOR_CSV = ";"  # padrão do projeto: Bronze, Silver e Gold usam ";"

# Cada identificador recebido da API aponta para uma função autorizada.
# SENHORES! EU PREFERI NÃO EXECUTAR DIRETAMENTE O TEXTO, AI MONTEI UM DICIONÁRIO
# QUE CONVERTE OS VALORES PARA SEUS VALORES COMO PARÂMETROS
COLETORES = {
    "psutil.cpu_percent(interval=1)": lambda: psutil.cpu_percent(interval=1),
    "psutil.virtual_memory()": lambda: psutil.virtual_memory(),
    'psutil.disk_usage("/")': lambda: psutil.disk_usage("/"),
    "psutil.net_if_stats()": lambda: psutil.net_if_stats(),
    "psutil.net_io_counters()": lambda: psutil.net_io_counters(),
    "psutil.swap_memory()": lambda: psutil.swap_memory(),
    "psutil.disk_io_counters()": lambda: psutil.disk_io_counters(),
}

# ---------------------------------------------------------------------------
# SIMULAÇÃO 
# Em vez de criar colunas "simuladas", elevamos o PRÓPRIO valor coletado,
# para que o ETL e o dashboard consumam o dado já "alto" e consigam alertar.
# ---------------------------------------------------------------------------

# Liga/desliga a simulação. Coloque False para enviar só os valores reais.
SIMULAR_VALORES = True

# Qual tipo de métrica cada coletor representa.
# Coletores que não estão aqui (rede, I/O de disco) NÃO são alterados.
TIPO_METRICA = {
    "psutil.cpu_percent()": "cpu",
    "psutil.virtual_memory()": "ram",
    'psutil.disk_usage("/")': "disco",
    "psutil.swap_memory()": "swap",
}

# Intervalo (mínimo, máximo) do percentual ALVO de uso, por tipo de dia e métrica.
# Cada leitura sorteia um valor dentro do intervalo; se o valor real da máquina
# já for maior que o sorteado, o real é mantido (a simulação nunca diminui).
# Quanto mais "especial" o dia, maior o uso: feriado > fim de semana > dia útil.
INTERVALOS_ALVO = {
    "DIA_UTIL":      {"cpu": (50.0, 60.0), "ram": (50.0, 60.0), "disco": (40.0, 50.0), "swap": (20.0, 30.0)},
    "FIM_DE_SEMANA": {"cpu": (65.0, 80.0), "ram": (60.0, 75.0), "disco": (50.0, 60.0), "swap": (30.0, 45.0)},
    "FERIADO":       {"cpu": (85.0, 99.0), "ram": (80.0, 95.0), "disco": (60.0, 75.0), "swap": (50.0, 70.0)},
}


def normalizar_dados(dados):
    """Converte estruturas do psutil em tipos que podem ser serializados em JSON."""
    if hasattr(dados, "_asdict"):
        return normalizar_dados(dados._asdict())

    if isinstance(dados, dict):
        return {
            str(chave): normalizar_dados(valor)
            for chave, valor in dados.items()
        }

    if isinstance(dados, (list, tuple)):
        return [normalizar_dados(valor) for valor in dados]

    if isinstance(dados, (str, int, float, bool)) or dados is None:
        return dados

    return str(dados)


def sanitizar_nome_coluna(texto):
    """
    Transforma qualquer texto em um nome de coluna seguro:
    sem acento, minúsculo e só com letras, números e underline.
    Ex.: "Memória RAM" -> "memoria_ram" 
    """
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "_", texto.lower())
    return texto.strip("_")


def achatar_valor(prefixo, valor):
    """
    Transforma um valor em um
    dicionário "plano", onde cada chave vira o nome de uma coluna do CSV.
(que pode ser número ou dicionário aninhado) 
    Exemplos:
      achatar_valor("cpu", 12.5)
          -> {"cpu": 12.5}
      achatar_valor("ram", {"total": 100, "percent": 40})
          -> {"ram_total": 100, "ram_percent": 40}
      achatar_valor("rede", {"eth0": {"isup": True, "speed": 1000}})
          -> {"rede_eth0_isup": True, "rede_eth0_speed": 1000}
    """
    if isinstance(valor, dict):
        plano = {}
        for chave, subvalor in valor.items():
            novo_prefixo = f"{prefixo}_{sanitizar_nome_coluna(chave)}"
            plano.update(achatar_valor(novo_prefixo, subvalor))
        return plano

    # Listas não cabem bem em uma única coluna numérica, então viram texto JSON.
    if isinstance(valor, list):
        return {prefixo: json.dumps(valor, ensure_ascii=False)}

    return {prefixo: valor}


def obter_mac():
    mac = get_mac_address()

    if not mac:
        raise RuntimeError("Não foi possível identificar o endereço MAC da máquina.")

    return mac.replace(":", "").upper()


def obter_configuracao(mac):
    url = f"{API_BASE_URL.rstrip('/')}/api/monitoramento/{mac}"

    resposta = requests.get(url, timeout=10)
    resposta.raise_for_status()

    configuracao = resposta.json()
    if "componentes" not in configuracao:
        raise ValueError("A resposta da API não contém a lista de componentes.")

    return configuracao["componentes"]


def simular_carga(identificador, valor, tipo_dia):
    """
    Eleva o uso do componente até o percentual alvo do tipo do dia
    (regra de negócio free flow), sem nunca ficar abaixo do valor real.

    - CPU (número simples): usa o maior entre o valor real e o alvo sorteado.
    - RAM / disco / swap (dicionário com total e percent): ajusta o percent e
      recalcula used/free/available para os valores continuarem coerentes.
    """
    tipo_metrica = TIPO_METRICA.get(identificador)

    # Simulação desligada ou coletor que não deve ser alterado
    if not SIMULAR_VALORES or tipo_metrica is None:
        return valor

    minimo, maximo = INTERVALOS_ALVO[tipo_dia][tipo_metrica]
    alvo = random.uniform(minimo, maximo)

    # Caso simples: valor numérico (ex.: cpu_percent)
    if isinstance(valor, (int, float)):
        return round(min(100.0, max(valor, alvo)), 2)

    # Caso composto: dicionário com total e percent (ex.: memória, disco)
    if isinstance(valor, dict) and "percent" in valor and "total" in valor:
        total = valor["total"]

        # Sem swap configurado (total = 0) não há o que simular
        if not total:
            return valor

        percent = round(min(100.0, max(valor["percent"], alvo)), 2)
        usado = round(total * percent / 100)

        valor = dict(valor)  # prefiri copiar para não alterar o original
        valor["percent"] = percent
        valor["used"] = usado

        if "free" in valor:
            valor["free"] = total - usado
        if "available" in valor:
            valor["available"] = total - usado

    return valor


def coletar_metricas_dinamicas(componentes, tipo_dia):
    metricas = {}
    componentes_nao_suportados = []

    for componente in componentes:
        identificador = componente.get("depende_de")
        nome = componente.get("nome", identificador)

        coletor = COLETORES.get(identificador)

        if coletor is None:
            componentes_nao_suportados.append(identificador)
            continue

        try:
            valor = coletor()
            # Os limites (limite_min / limite_max) NÃO são coletados aqui:
            # o ETL Silver/Gold busca esses parâmetros depois, pelo MAC.
            valor = normalizar_dados(valor)

            # Eleva o uso até o alvo do dia (feriado > fim de semana > dia útil)
            valor = simular_carga(identificador, valor, tipo_dia)

            metricas[nome] = {
                "valor": valor,
            }
        except (KeyError, TypeError, ValueError) as erro:
            print(f"Não foi possível coletar {nome}: {erro}")

    if componentes_nao_suportados:
        print("Coletores não cadastrados no Client:", componentes_nao_suportados)

    return metricas


def metricas_para_colunas(metricas):
    """
    Converte o dicionário de métricas em colunas planas do CSV.

    Para CADA componente monitorado são geradas:
      - <nome>                 -> valor (quando o coletor retorna um número simples)
      - <nome>_<campo>         -> um campo por coluna (quando retorna vários valores,
                                  ex.: ram_total, ram_used, ram_percent)

    Os limites não entram no CSV: são tratados depois no ETL Silver/Gold.
    """
    colunas = {}

    for nome, componente in metricas.items():
        base = sanitizar_nome_coluna(nome)

        # Valores coletados doos componentes
        colunas.update(achatar_valor(base, componente["valor"]))

    return colunas


def obter_valor_metrica(metricas, nome, campo="valor"):
    """Obtém uma métrica dinâmica quando ela estiver habilitada na API."""
    componente = metricas.get(nome)
    if not componente:
        return None

    valor = componente.get(campo)

    if isinstance(valor, (int, float)):
        return float(valor)

    if isinstance(valor, dict) and "percent" in valor:
        return float(valor["percent"])

    return None


def identificar_tipo_dia(data_atual):
    feriados = holidays.country_holidays(
        "BR",
        subdiv="SP",
        years=[data_atual.year]
    )

    if data_atual.date() in feriados:
        return "FERIADO", feriados.get(data_atual.date())

    if data_atual.weekday() >= 5:
        return "FIM_DE_SEMANA", None

    return "DIA_UTIL", None


def obter_informacoes_estaticas(mac):
    return {
        "mac": mac,
        "hostname": socket.gethostname(),
        "so_sistema": f"{platform.system()} {platform.release()}",
        "arquitetura": platform.machine(),
        "cpu_fisicos": psutil.cpu_count(logical=False) or 0,
        "cpu_logicos": psutil.cpu_count(logical=True) or 0,
    }


def coletar_metricas_processos():
    processos = []
    total_processos = 0
    processos_rodando = 0

    # A primeira leitura inicializa a medição de CPU de cada processo.
    for processo in psutil.process_iter(["pid", "name", "status"]):
        try:
            total_processos += 1

            if processo.status() == psutil.STATUS_RUNNING:
                processos_rodando += 1

            processo.cpu_percent(interval=None)
            processos.append(processo)

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess
        ):
            continue

    time.sleep(INTERVALO_PROCESSOS)

    metricas_processos = []

    for processo in processos:
        try:
            with processo.oneshot():
                memoria = processo.memory_info()

                metricas_processos.append({
                    "pid": processo.pid,
                    "nome": processo.name() or "desconhecido",
                    "cpu_pct": round(processo.cpu_percent(interval=None), 2),
                    "ram_mb": round(memoria.rss / (1024 ** 2), 2),
                    "ram_pct": round(processo.memory_percent(), 2),
                    "threads": processo.num_threads(),
                    "status": processo.status(),
                })

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess
        ):
            continue

    processos_por_cpu = sorted(
        metricas_processos,
        key=lambda processo: processo["cpu_pct"],
        reverse=True
    )

    processos_por_ram = sorted(
        metricas_processos,
        key=lambda processo: processo["ram_mb"],
        reverse=True
    )

    return {
        "total": total_processos,
        "rodando": processos_rodando,
        "top_cpu": processos_por_cpu[0] if processos_por_cpu else None,
        "top_ram": processos_por_ram[0] if processos_por_ram else None,
        "top_processos_cpu": processos_por_cpu[:TOP_N_PROCESSOS],
    }


def gerar_chave_s3():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"raw/roadvizor_{timestamp}.csv"


def gerar_csv(linhas):
    """
    Monta o CSV em memória.

    Como as colunas dos componentes dependem do que a API devolveu para
    cada máquina, o cabeçalho NÃO é fixo: ele é montado juntando as chaves
    de todas as linhas, mantendo a ordem em que aparecem.
    """
    colunas = []
    for linha in linhas:
        for chave in linha:
            if chave not in colunas:
                colunas.append(chave)

    arquivo = io.StringIO()
    escritor = csv.DictWriter(
        arquivo,
        fieldnames=colunas,
        delimiter=SEPARADOR_CSV,
        restval=""  # se alguma linha não tiver a coluna, grava vazio
    )

    escritor.writeheader()
    escritor.writerows(linhas)

    return arquivo.getvalue()


def enviar_buffer_para_s3(conteudo_csv, bucket_name, chave_s3):
    s3 = boto3.client("s3", region_name=AWS_REGION)

    s3.put_object(
        Bucket=bucket_name,
        Key=chave_s3,
        Body=conteudo_csv.encode("utf-8"),
        ContentType="text/csv; charset=utf-8"
    )

    print(f"Arquivo enviado para s3://{bucket_name}/{chave_s3}")


def coletar_linha(info_estatica, componentes, tipo_dia, nome_feriado=None):
    agora = datetime.datetime.now()
 
    # Os valores já saem simulados (elevados) conforme o tipo do dia
    metricas = coletar_metricas_dinamicas(componentes, tipo_dia)
    processos = coletar_metricas_processos()
 
    linha = {
        "timestamp": agora.strftime("%Y-%m-%d %H:%M:%S"),
        **info_estatica,
        "tipo_dia": tipo_dia,
        "feriado": nome_feriado or "",
        # Uma (ou mais) colunas para cada componente monitorado
        **metricas_para_colunas(metricas),
        "processos": json.dumps(processos, ensure_ascii=False),
    }
 
    # Mostra TODAS as colunas da linha que será gravada no CSV (uma por linha)
    for coluna, valor in linha.items():
        print(f"  {coluna}: {valor}")
 
    return linha
 
 
def main():
    mac = obter_mac()
    print("MAC identificado:", mac)
 
    # A configuração é consultada antes do início do lote de leituras.
    componentes = obter_configuracao(mac)
    print("Componentes recebidos da API:")
    for componente in componentes:
        print(componente)
    print(f"Componentes habilitados recebidos da API: {len(componentes)}")
 
    agora = datetime.datetime.now()
    tipo_dia, nome_feriado = identificar_tipo_dia(agora)
    info_estatica = obter_informacoes_estaticas(mac)
 
    linhas = []
 
    for numero_leitura in range(QTD_LEITURAS):
        print(f"\nLeitura {numero_leitura + 1}/{QTD_LEITURAS}")
 
        linha = coletar_linha(
            info_estatica,
            componentes,
            tipo_dia,
            nome_feriado
        )
        linhas.append(linha)
 
        if numero_leitura < QTD_LEITURAS - 1:
            time.sleep(INTERVALO_LEITURAS)
 
    conteudo_csv = gerar_csv(linhas)
    chave_s3 = gerar_chave_s3()
 
    enviar_buffer_para_s3(
        conteudo_csv,
        BUCKET_NAME,
        chave_s3
    )
 
 
if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as erro:
        print(f"Erro ao consultar a API: {erro}")
    except Exception as erro:
        print(f"Erro durante a captura: {erro}")