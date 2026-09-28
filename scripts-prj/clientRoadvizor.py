import psutil
import datetime
import csv
import time     
import speedtest
import boto3
from getmac import get_mac_address

dadosCSV = 'roadvizor.csv'
qtd = 3


BUCKET_BRONZE = 'datalake-bronze-roadvizor-sptech'

def titulo():
    try:
        with open(dadosCSV, "r", encoding="utf-8") as f:
            print(f"O arquivo '{dadosCSV}' foi encontrado. Iniciando leituras...")
    except FileNotFoundError:
        print(f"Criando arquivo '{dadosCSV}' e iniciando leituras...")
        title = ["id_maquina","timestamp", "cpu","ram_total","ram_uso","swap_total","swap_uso","proc_rodando","proc_esperando", "disco_total","disco_uso","rede_enviada", "rede_recebida","pacote_perdido_entrada","pacote_perdido_saida","latencia"]
        with open(dadosCSV, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile, delimiter=';')
            writer.writerow(title)
    finally:
        for i in range(qtd):
            monitorar_sistema()
            
        
        enviar_para_bronze(dadosCSV)
        
def monitorar_sistema():
    # CPU
    processos = list(psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status']))
    porcentagem_cpu = psutil.cpu_percent(interval=1)
    proc_rodando = sum(1 for p in processos if p.info['status'] == psutil.STATUS_RUNNING)
    proc_esperando = sum(1 for p in processos if p.info['status'] == psutil.STATUS_SLEEPING)

    # Memoria (ram e swap)
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Disco
    uso_disco = psutil.disk_usage('/')

    # Rede
    io_rede = psutil.net_io_counters()
    
    # Speedtest 
    st = speedtest.Speedtest()
    st.get_best_server()
    latencia = st.results.ping

    hora = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    id_maquina = get_mac_address().replace(":","").upper()

    mensagem = [id_maquina, hora, porcentagem_cpu, ram.total, ram.used, swap.total, swap.used, proc_rodando, proc_esperando, uso_disco.total, uso_disco.used, io_rede.bytes_sent, io_rede.bytes_recv, io_rede.dropin, io_rede.dropout, latencia]

    print(f"=== REALIZANDO MONITORAMENTO DA MÁQUINA {id_maquina} - {hora} ===")

    with open(dadosCSV, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter=';')
        writer.writerow(mensagem) 

    time.sleep(2)

def enviar_para_bronze(nome_arquivo_local):
    
    print("\nIniciando conexão com a AWS para envio dos dados...")
    
    
    s3_client = boto3.client('s3', region_name='us-east-1')
    
    # Cria um nome de arquivo único para o S3 usando o timestamp atual
    timestamp_arquivo = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    chave_s3 = f'raw/roadvizor_{timestamp_arquivo}.csv'
    
    try:
        s3_client.upload_file(nome_arquivo_local, BUCKET_BRONZE, chave_s3)
        print(f"Sucesso! Arquivo '{nome_arquivo_local}' salvo no S3 como '{chave_s3}'.")
    except Exception as e:
        print(f"Erro ao enviar arquivo para o S3: {e}")


titulo()