import psutil
import datetime
import csv
import time
import speedtest
from getmac import get_mac_address

MAX_NUCLEOS = 16

def titulo():
        title = ["id_maquina","timestamp", "cpu",
    "ram_total","ram_uso","swap_total","swap_uso","proc_rodando","proc_esperando", "disco_total","disco_uso", 
    "rede_enviada", "rede_recebida","pacote_perdido_entrada","pacote_perdido_saida","latencia"]
        with open('./roadvizor.csv', 'a', newline='') as csvfile:
            writer = csv.writer(csvfile,delimiter=';')
            writer.writerow(title)

def monitorar_sistema() :
    # CPU
    porcentagem_cpu = psutil.cpu_percent(interval=1)
    # cpu_por_nucleo = psutil.cpu_percent(interval=None, percpu=True)
    # cpu_times = psutil.cpu_times()
    # qtd_cpu_fisica = psutil.cpu_count(logical=False)
    # qtd_cpu_logica = psutil.cpu_count(logical=True)
    # freq_cpu = psutil.cpu_freq()

    # discretização 
    if porcentagem_cpu <= 50: 
        status_cpu = "Normal"
    elif porcentagem_cpu <= 80:
        status_cpu = "Atenção"
    else : 
        status_cpu = "Crítico"

    # processos e threads 
    processos = list(psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status'])) # todos os processos em execução
    total_processos = len(processos)
    # threads_totais = sum(p.num_threads() for p in processos if hasattr(p, "num_threads")) # soma o numero total de threads de todos os processos

    proc_rodando = sum(1 for p in processos if p.info['status'] == psutil.STATUS_RUNNING) # p.info é um dicionario da psutil e esta pegando o valor da chave status 
    proc_esperando = sum(1 for p in processos if p.info['status'] == psutil.STATUS_SLEEPING)

    # processo por uso de CPU e memoria 
    top_cpu = max(processos, key=lambda p: p.info['cpu_percent'] or 0) # lambda é uma função anonima entao vc coloca argumento e expressao depois 
    top_ram = max(processos, key=lambda p: p.info['memory_info'].rss if p.info['memory_info'] else 0) # se tiver dados de memoria validos usa a info do rss (quant exata de ram fisica que o processo esta utilizando )

    # memoria (ram e swap)
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # disco 
    uso_disco = psutil.disk_usage('/')
    io_disco = psutil.disk_io_counters()

    # rede 
    io_rede = psutil.net_io_counters()
    conexoes_ativas = len(psutil.net_connections(kind='inet'))
    st = speedtest.Speedtest()
    st.get_best_server()
    latencia = st.results.ping

    hora = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    id_maquina = get_mac_address().replace(":","").upper() #pega o id das maquinas, tira os dois pontos para deixar junto e transforma em maiusculo

    mensagem = []

    # mensagem_nucleos = []
    # for i in range(MAX_NUCLEOS):
    #     try:
    #         mensagem_nucleos.append(cpu_por_nucleo[i])
    #     except IndexError:
    #         mensagem_nucleos.append("null")

    mensagem.append([id_maquina,hora, porcentagem_cpu,ram.total, ram.used, swap.total,swap.used,proc_rodando,proc_esperando,uso_disco.total, uso_disco.used,io_rede.bytes_sent,io_rede.bytes_recv,io_rede.dropin,io_rede.dropout,latencia])

    print(f"=== MONITORAMENTO - {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ===")
    print(f"Status da CPU: {status_cpu} ({porcentagem_cpu}%)")
    # print(f"Núcleos: {qtd_cpu_fisica} físicos | {qtd_cpu_logica} lógicos")
    # print(f"Uso por núcleo: {cpu_por_nucleo}")
    # print(f"Frequência CPU: {freq_cpu.current:.2f} MHz" if freq_cpu else "Frequência: N/A")
    # print(f"Tempos de CPU: User={cpu_times.user}s, System={cpu_times.system}s, Idle={cpu_times.idle}s")
        
    print(f"\nProcessos Totais: {total_processos} (Rodando: {proc_rodando} | Esperando: {proc_esperando})")
    # print(f"Threads Totais: {threads_totais}")
    print(f"Maior uso de CPU: {top_cpu.info['name']} ({top_cpu.info['cpu_percent']}%)")
    print(f"Maior uso de RAM: {top_ram.info['name']} ({top_ram.info['memory_info'].rss / (1024**2):.2f} MB)") # converte bytes em mb

    print(f"\nRAM Total: {ram.total / (1024**3):.2f} GB") # converte bytes em gb
    print(f"RAM Usada: {ram.percent}% | Livre: {ram.free / (1024**3):.2f} GB | Disponível: {ram.available / (1024**3):.2f} GB")
    print(f"SWAP Total: {swap.total / (1024**3):.2f} GB | Usado: {swap.percent}%")

    print(f"\nDisco Total: {uso_disco.total / (1024**3):.2f} GB")
    print(f"Disco Usado: {uso_disco.percent}% | Livre: {uso_disco.free / (1024**3):.2f} GB")
    print(f"Leitura Disco: {io_disco.read_bytes / (1024**2):.2f} MB | Escrita: {io_disco.write_bytes / (1024**2):.2f} MB")
    print(f"Tempo de IO: Leitura {io_disco.read_time}ms | Escrita {io_disco.write_time}ms")

    print(f"\nRede Enviada: {io_rede.bytes_sent / (1024**2):.2f} MB | Recebida: {io_rede.bytes_recv / (1024**2):.2f} MB")
    print(f"Pacotes Descartados: Entrada {io_rede.dropin} | Saída {io_rede.dropout}")
    print(f"Erros de Rede: Entrada {io_rede.errin} | Saída {io_rede.errout}")
    print(f"Conexões Ativas: {conexoes_ativas}")

    with open('./roadvizor.csv', 'a', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter=';')
        writer.writerows(mensagem)

    time.sleep(2)

titulo()   

for i in range(10):
    monitorar_sistema()