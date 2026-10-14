import csv
import datetime
import io
import json
import platform
import random
import socket
import time

import boto3
import holidays
import psutil

from botocore.exceptions import ClientError, NoCredentialsError
from getmac import get_mac_address



# CONFIGURAÇÕES

COD_MAQUINA = 'COD001'
QTD_LEITURAS = 10

BUCKET_NAME = 'datalake-bronze-roadvizor-sptech'
AWS_REGION = 'us-east-1'

# Tempo de amostragem para identificar o consumo por processo.
INTERVALO_PROCESSOS = 0.3

# Quantidade de processos que serão registrados no JSON.
TOP_N_PROCESSOS = 5



# IDENTIFICAÇÃO DO TIPO DE DIA


def identificar_tipo_dia(data_atual):
    data = data_atual.date()

    feriados = holidays.country_holidays(
        'BR',
        subdiv='SP',
        years=[data.year]
    )

    if data in feriados:
        return 'FERIADO'

    if data.weekday() >= 5:
        return 'FIM_DE_SEMANA'

    return 'DIA_UTIL'



# CHAVE S3


def gerar_chave_s3():
    timestamp_nome = datetime.datetime.now().strftime(
        '%Y%m%d_%H%M%S'
    )

    return f'raw/roadvizor_{timestamp_nome}.csv'



# INFORMAÇÕES ESTÁTICAS


def obter_informacoes_estaticas():
    return {
        'mac': get_mac_address() or '00:00:00:00:00:00',
        'hostname': socket.gethostname(),
        'so_sistema': f'{platform.system()} {platform.release()}',
        'arquitetura': platform.machine(),
        'cpu_fisicos': psutil.cpu_count(logical=False) or 0,
        'cpu_logicos': psutil.cpu_count(logical=True) or 0,
    }



# MONITORAMENTO DE PROCESSOS


def coletar_metricas_processos():
    """
    Identifica:
    - Quantidade total de processos acessíveis.
    - Quantidade de processos em execução.
    - Processo com maior consumo de CPU na amostragem.
    - Processo com maior utilização de RAM.
    - Lista dos cinco principais processos por CPU.

    Processos inacessíveis ou encerrados durante a coleta
    são ignorados quando não é possível consultar suas métricas.
    """

    processos = []
    proc_totais = 0
    proc_rodando = 0

    
    # PRIMEIRA AMOSTRAGEM
    # Inicializa a medição de CPU de cada processo.
    

    for processo in psutil.process_iter(
        ['pid', 'name', 'status']
    ):
        try:
            proc_totais += 1

            status = processo.status()

            if status == psutil.STATUS_RUNNING:
                proc_rodando += 1

            # Inicializa a medição de CPU.
            # O primeiro resultado ainda não é representativo.
            processo.cpu_percent(interval=None)

            processos.append(processo)

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess
        ):
            continue

    # Aguarda o intervalo necessário para comparar
    # o consumo de CPU entre duas amostragens.
    time.sleep(INTERVALO_PROCESSOS)

    
    # SEGUNDA AMOSTRAGEM
    # Obtém as métricas de cada processo.
    

    metricas = []

    for processo in processos:
        try:
            with processo.oneshot():

                nome = processo.name()
                pid = processo.pid

                cpu_pct = processo.cpu_percent(
                    interval=None
                )

                memoria = processo.memory_info()

                ram_mb = round(
                    memoria.rss / (1024 ** 2),
                    2
                )

                ram_pct = round(
                    processo.memory_percent(),
                    2
                )

                threads = processo.num_threads()

                status = processo.status()

            metricas.append({
                'pid': pid,
                'nome': nome or 'desconhecido',
                'cpu_pct': round(cpu_pct, 2),
                'ram_mb': ram_mb,
                'ram_pct': ram_pct,
                'threads': threads,
                'status': status,
            })

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess
        ):
            continue

    
    # PROCESSO COM MAIOR CONSUMO DE CPU
    

    processos_cpu = sorted(
        metricas,
        key=lambda processo: processo['cpu_pct'],
        reverse=True
    )

    if processos_cpu:
        top_cpu = processos_cpu[0]
    else:
        top_cpu = {
            'pid': 0,
            'nome': 'indisponivel',
            'cpu_pct': 0.0,
            'ram_mb': 0.0,
            'ram_pct': 0.0,
            'threads': 0,
            'status': 'indisponivel',
        }

    
    # PROCESSO COM MAIOR CONSUMO DE RAM
    

    processos_ram = sorted(
        metricas,
        key=lambda processo: processo['ram_mb'],
        reverse=True
    )

    if processos_ram:
        top_ram = processos_ram[0]
    else:
        top_ram = {
            'pid': 0,
            'nome': 'indisponivel',
            'cpu_pct': 0.0,
            'ram_mb': 0.0,
            'ram_pct': 0.0,
            'threads': 0,
            'status': 'indisponivel',
        }

    
    # TOP 5 PROCESSOS POR CPU
    

    top_processos = processos_cpu[:TOP_N_PROCESSOS]

    # Serializa os registros como JSON para armazená-los
    # em uma única coluna do CSV.
    top_processos_json = json.dumps(
        top_processos,
        ensure_ascii=False
    )

    
    # EXIBIÇÃO NO TERMINAL
    

    print(
        f'Processos identificados: {proc_totais} | '
        f'Em execução: {proc_rodando}'
    )

    print(
        f'Top CPU: {top_cpu["nome"]} '
        f'(PID {top_cpu["pid"]}) | '
        f'{top_cpu["cpu_pct"]}%'
    )

    print(
        f'Top RAM: {top_ram["nome"]} '
        f'(PID {top_ram["pid"]}) | '
        f'{top_ram["ram_mb"]} MB'
    )

    
    # RETORNO
    

    return {
        'proc_totais': proc_totais,
        'proc_rodando': proc_rodando,

        'proc_cpu_top_nome': top_cpu['nome'],
        'proc_cpu_top_pid': top_cpu['pid'],
        'proc_cpu_top_pct': top_cpu['cpu_pct'],
        'proc_cpu_top_threads': top_cpu['threads'],

        'proc_ram_top_nome': top_ram['nome'],
        'proc_ram_top_pid': top_ram['pid'],
        'proc_ram_top_mb': top_ram['ram_mb'],
        'proc_ram_top_pct': top_ram['ram_pct'],

        'proc_top_lista': top_processos_json,
    }



# COLETA DE HARDWARE E SIMULAÇÃO DO FREE FLOW


def coletar_linha_metrica(info_estatica, tipo_dia_atual):

    
    # PROCESSOS
    

    metricas_processos = coletar_metricas_processos()

    
    # CPU REAL DO SISTEMA
    

    cpu_real = psutil.cpu_percent(interval=1)

    freq_info = psutil.cpu_freq()

    cpu_freq_mhz = (
        round(freq_info.current, 2)
        if freq_info
        else 0.0
    )

    
    # RAM
    

    ram = psutil.virtual_memory()

    ram_total_gib = round(
        ram.total / (1024 ** 3), 2
    )

    ram_uso_gib = round(
        ram.used / (1024 ** 3), 2
    )

    ram_disp_gib = round(
        ram.available / (1024 ** 3), 2
    )

    ram_pct = ram.percent

    
    # SWAP
    

    swap = psutil.swap_memory()

    swap_total_gib = round(
        swap.total / (1024 ** 3), 2
    )

    swap_uso_gib = round(
        swap.used / (1024 ** 3), 2
    )

    swap_pct = swap.percent

    
    # DISCO
    

    disco = psutil.disk_usage('/')

    disco_total_gib = round(
        disco.total / (1024 ** 3), 2
    )

    disco_uso_gib = round(
        disco.used / (1024 ** 3), 2
    )

    disco_livre_gib = round(
        disco.free / (1024 ** 3), 2
    )

    disco_pct = disco.percent

    
    # I/O DE DISCO
    

    io_disco = psutil.disk_io_counters()

    disco_leitura_mb = (
        round(io_disco.read_bytes / (1024 ** 2), 2)
        if io_disco else 0.0
    )

    disco_escrita_mb = (
        round(io_disco.write_bytes / (1024 ** 2), 2)
        if io_disco else 0.0
    )

    
    # REDE
    

    io_rede = psutil.net_io_counters()

    rede_recebida_mb = round(
        io_rede.bytes_recv / (1024 ** 2), 2
    )

    rede_enviada_mb = round(
        io_rede.bytes_sent / (1024 ** 2), 2
    )

    
    # UPTIME
    

    uptime_segundos = int(
        time.time() - psutil.boot_time()
    )

    
    # SIMULAÇÃO DO CENÁRIO FREE FLOW
    

    if tipo_dia_atual == 'FERIADO':

        fluxo_veiculos = random.randint(120, 250)

        cpu_simulada = min(
            100.0,
            cpu_real + random.uniform(35.0, 55.0)
        )

        latencia_sim = round(
            random.uniform(35.0, 85.0), 2
        )

    elif tipo_dia_atual == 'FIM_DE_SEMANA':

        fluxo_veiculos = random.randint(70, 140)

        cpu_simulada = min(
            100.0,
            cpu_real + random.uniform(15.0, 30.0)
        )

        latencia_sim = round(
            random.uniform(20.0, 45.0), 2
        )

    else:

        fluxo_veiculos = random.randint(20, 80)

        cpu_simulada = cpu_real

        latencia_sim = round(
            random.uniform(10.0, 25.0), 2
        )

    
    # TIMESTAMP
    

    timestamp = datetime.datetime.now().strftime(
        '%Y-%m-%d %H:%M:%S'
    )

    
    # RESUMO NO TERMINAL
    

    print(
        f'[{timestamp}] {COD_MAQUINA} | '
        f'Dia: {tipo_dia_atual} | '
        f'Fluxo simulado: {fluxo_veiculos} veic/min | '
        f'CPU real: {cpu_real}% | '
        f'CPU simulada: {round(cpu_simulada, 2)}% | '
        f'RAM: {ram_pct}% | '
        f'Latência simulada: {latencia_sim} ms'
    )

    
    # LINHA DO CSV
    # A ordem deve corresponder ao cabeçalho.
    

    return [
        COD_MAQUINA,
        info_estatica['mac'],
        info_estatica['hostname'],
        info_estatica['so_sistema'],
        info_estatica['arquitetura'],
        timestamp,
        tipo_dia_atual,
        fluxo_veiculos,

        cpu_real,
        round(cpu_simulada, 2),
        cpu_freq_mhz,
        info_estatica['cpu_fisicos'],
        info_estatica['cpu_logicos'],

        ram_total_gib,
        ram_uso_gib,
        ram_disp_gib,
        ram_pct,

        swap_total_gib,
        swap_uso_gib,
        swap_pct,

        disco_total_gib,
        disco_uso_gib,
        disco_livre_gib,
        disco_pct,

        disco_leitura_mb,
        disco_escrita_mb,

        metricas_processos['proc_totais'],
        metricas_processos['proc_rodando'],

        metricas_processos['proc_cpu_top_nome'],
        metricas_processos['proc_cpu_top_pid'],
        metricas_processos['proc_cpu_top_pct'],
        metricas_processos['proc_cpu_top_threads'],

        metricas_processos['proc_ram_top_nome'],
        metricas_processos['proc_ram_top_pid'],
        metricas_processos['proc_ram_top_mb'],
        metricas_processos['proc_ram_top_pct'],

        metricas_processos['proc_top_lista'],

        rede_recebida_mb,
        rede_enviada_mb,

        latencia_sim,
        uptime_segundos,
    ]



# UPLOAD PARA O S3


def enviar_buffer_para_s3(conteudo_csv, bucket_name, chave_s3):

    print(
        f'\n[S3] Enviando para '
        f's3://{bucket_name}/{chave_s3}'
    )

    try:

        s3_client = boto3.client(
            's3',
            region_name=AWS_REGION
        )

        s3_client.put_object(
            Bucket=bucket_name,
            Key=chave_s3,
            Body=conteudo_csv.encode('utf-8'),
            ContentType='text/csv; charset=utf-8'
        )

        print('[S3] Upload concluído com sucesso!')

        return True

    except NoCredentialsError:

        print(
            '[S3 ERRO] Credenciais AWS não encontradas.'
        )

    except ClientError as erro:

        print(
            f'[S3 ERRO] Erro na requisição: {erro}'
        )

    except Exception as erro:

        print(
            f'[ERRO] Erro inesperado: {erro}'
        )

    return False



# MAIN


def main():

    print('\n==============================================')
    print('       ROADVIZOR - CAPTURA DE DADOS')
    print('==============================================')

    data_atual = datetime.datetime.now()

    tipo_dia_atual = identificar_tipo_dia(data_atual)

    print(
        f'Data: {data_atual.strftime("%d/%m/%Y %H:%M:%S")}'
    )

    print(f'Tipo de dia: {tipo_dia_atual}')

    if tipo_dia_atual == 'FERIADO':

        feriados = holidays.country_holidays(
            'BR',
            subdiv='SP',
            years=[data_atual.year]
        )

        print(
            f'Feriado: {feriados.get(data_atual.date())}'
        )

    info_estatica = obter_informacoes_estaticas()

    
    # CABEÇALHO CSV
    

    cabecalho = [
        'cod_maquina',
        'mac_address',
        'hostname',
        'so_sistema',
        'arquitetura',
        'timestamp',
        'tipo_dia',
        'fluxo_veiculos_sim',

        'cpu_pct',
        'cpu_simulada_pct',
        'cpu_freq_mhz',
        'cpu_nucleos_fisicos',
        'cpu_nucleos_logicos',

        'ram_total_gib',
        'ram_uso_gib',
        'ram_disponivel_gib',
        'ram_pct',

        'swap_total_gib',
        'swap_uso_gib',
        'swap_pct',

        'disco_total_gib',
        'disco_uso_gib',
        'disco_livre_gib',
        'disco_pct',

        'disco_leitura_mb',
        'disco_escrita_mb',

        'proc_totais',
        'proc_rodando',

        'proc_cpu_top_nome',
        'proc_cpu_top_pid',
        'proc_cpu_top_pct',
        'proc_cpu_top_threads',

        'proc_ram_top_nome',
        'proc_ram_top_pid',
        'proc_ram_top_mb',
        'proc_ram_top_pct',

        'proc_top_lista',

        'rede_recebida_mb',
        'rede_enviada_mb',

        'latencia_ms',
        'uptime_segundos',
    ]

    buffer_csv = io.StringIO()

    writer = csv.writer(
        buffer_csv,
        delimiter=';'
    )

    writer.writerow(cabecalho)

    
    # CAPTURA
    

    print(
        f'\nIniciando {QTD_LEITURAS} leituras...\n'
    )

    for leitura in range(QTD_LEITURAS):

        print(
            f'\n---------- LEITURA '
            f'{leitura + 1}/{QTD_LEITURAS} ----------'
        )

        linha = coletar_linha_metrica(
            info_estatica,
            tipo_dia_atual
        )

        writer.writerow(linha)

        time.sleep(1)

    
    # UPLOAD
    

    chave_s3 = gerar_chave_s3()

    sucesso = enviar_buffer_para_s3(
        buffer_csv.getvalue(),
        BUCKET_NAME,
        chave_s3
    )

    if sucesso:

        print('\nCaptura concluída!')

        print(
            f'Arquivo: s3://{BUCKET_NAME}/{chave_s3}'
        )

    else:

        print(
            '\nO upload falhou. O CSV não foi salvo localmente; '
            'o conteúdo em memória será perdido ao encerrar '
            'o programa.'
        )


main()