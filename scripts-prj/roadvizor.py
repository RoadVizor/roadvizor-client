import psutil
import datetime
import csv
import time
import speedtest
from getmac import get_mac_address

dadosCSV = 'roadvizor.csv'
qtd = 10

def titulo():
    try:
        with open(dadosCSV, "r", encoding="utf-8") as f:
            print(f"O arquivo '{dadosCSV}' encontrado e aberto com sucesso. Iniciando leituras")
    except FileNotFoundError:
        print("Criando arquivo 'roadvizor.csv' e iniciando leituras")
        title = ["id_maquina","timestamp", "cpu","ram_total","ram_uso","swap_total","swap_uso","proc_rodando","proc_esperando", "disco_total","disco_uso","rede_enviada", "rede_recebida","pacote_perdido_entrada","pacote_perdido_saida","latencia"]
        with open('./roadvizor.csv', 'a', newline='') as csvfile:
            writer = csv.writer(csvfile,delimiter=';')
            writer.writerow(title)
    finally:
        for i in range(qtd):
            monitorar_sistema()
        
def monitorar_sistema() :

    # CPU
    processos = list(psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status'])) # todos os processos em execução
    porcentagem_cpu = psutil.cpu_percent(interval=1)
    proc_rodando = sum(1 for p in processos if p.info['status'] == psutil.STATUS_RUNNING) # p.info é um dicionario da psutil e esta pegando o valor da chave status
    proc_esperando = sum(1 for p in processos if p.info['status'] == psutil.STATUS_SLEEPING)

    # memoria (ram e swap)
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # disco
    uso_disco = psutil.disk_usage('/')

    # rede
    io_rede = psutil.net_io_counters()
    st = speedtest.Speedtest()
    st.get_best_server()
    latencia = st.results.ping

    hora = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    id_maquina = get_mac_address().replace(":","").upper() #pega o id das maquinas, tira os dois pontos para deixar junto e transforma em maiusculo

    mensagem = []
    mensagem.append([id_maquina,hora, porcentagem_cpu,ram.total, ram.used, swap.total,swap.used,proc_rodando,proc_esperando,uso_disco.total, uso_disco.used,io_rede.bytes_sent,io_rede.bytes_recv,io_rede.dropin,io_rede.dropout,latencia])

    print(f"=== REALIZANDO MONITORAMENTO DA MÁQUINA {id_maquina} - {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ===")

    with open('./roadvizor.csv', 'a', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter=';')
        writer.writerows(mensagem)

    time.sleep(2)

titulo()  