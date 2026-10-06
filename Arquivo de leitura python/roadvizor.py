import csv
import datetime
import os
import platform
import random
import socket
import time
from getmac import get_mac_address
import psutil


COD_MAQUINA = 'COD001'
QTD_LEITURAS = 10
TIPO_DIA_ATUAL = 'FERIADO'


def gerar_caminho_csv_local():
  """Gera o arquivo local na estrutura raw/roadvizor_YYYYMMDD_HHMMSS.csv"""
  pasta = 'raw'
  os.makedirs(pasta, exist_ok=True)
  timestamp_nome = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
  return os.path.join(pasta, f'roadvizor_{timestamp_nome}.csv')


def obter_informacoes_estaticas():
  """Coleta dados de hardware e sistema operacional que não mudam durante a execução."""
  mac = get_mac_address() or '00:00:00:00:00:00'
  hostname = socket.gethostname()
  so_sistema = f'{platform.system()} {platform.release()}'
  arch = platform.machine()
  cpu_fisicos = psutil.cpu_count(logical=False) or 0
  cpu_logicos = psutil.cpu_count(logical=True) or 0

  return {
      'mac': mac,
      'hostname': hostname,
      'so_sistema': so_sistema,
      'arch': arch,
      'cpu_fisicos': cpu_fisicos,
      'cpu_logicos': cpu_logicos,
  }


def inicializar_csv(caminho_csv):
  """Cria o CSV local e grava o cabeçalho."""
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
      'rede_recebida_mb',
      'rede_enviada_mb',
      'latencia_ms',
      'uptime_segundos',
  ]

  with open(caminho_csv, 'w', newline='', encoding='utf-8') as csvfile:
    writer = csv.writer(csvfile, delimiter=';')
    writer.writerow(cabecalho)


def monitorar_e_salvar_local(info_estatica, caminho_csv):
  """Coleta métricas e grava direto no arquivo local."""
  processos_todos = list(psutil.process_iter(['status']))
  proc_totais = len(processos_todos)
  proc_rodando = sum(
      1 for p in processos_todos if p.info['status'] == psutil.STATUS_RUNNING
  )

  cpu_real = psutil.cpu_percent(interval=1)
  freq_info = psutil.cpu_freq()
  cpu_freq_mhz = round(freq_info.current, 2) if freq_info else 0.0

  ram = psutil.virtual_memory()
  ram_total_gib = round(ram.total / (1024**3), 2)
  ram_uso_gib = round(ram.used / (1024**3), 2)
  ram_disp_gib = round(ram.available / (1024**3), 2)
  ram_pct = ram.percent

  swap = psutil.swap_memory()
  swap_total_gib = round(swap.total / (1024**3), 2)
  swap_uso_gib = round(swap.used / (1024**3), 2)
  swap_pct = swap.percent

  disco = psutil.disk_usage('/')
  disco_total_gib = round(disco.total / (1024**3), 2)
  disco_uso_gib = round(disco.used / (1024**3), 2)
  disco_livre_gib = round(disco.free / (1024**3), 2)
  disco_pct = disco.percent

  io_disco = psutil.disk_io_counters()
  disco_leitura_mb = (
      round(io_disco.read_bytes / (1024**2), 2) if io_disco else 0.0
  )
  disco_escrita_mb = (
      round(io_disco.write_bytes / (1024**2), 2) if io_disco else 0.0
  )

  io_rede = psutil.net_io_counters()
  rede_recebida_mb = round(io_rede.bytes_recv / (1024**2), 2)
  rede_enviada_mb = round(io_rede.bytes_sent / (1024**2), 2)
  uptime_segundos = int(time.time() - psutil.boot_time())

  if TIPO_DIA_ATUAL == 'FERIADO':
    fluxo_veiculos = random.randint(120, 250)
    cpu_calculada = min(100.0, cpu_real + random.uniform(35.0, 55.0))
    latencia_sim = round(random.uniform(35.0, 85.0), 2)
  elif TIPO_DIA_ATUAL == 'FIM_DE_SEMANA':
    fluxo_veiculos = random.randint(70, 140)
    cpu_calculada = min(100.0, cpu_real + random.uniform(15.0, 30.0))
    latencia_sim = round(random.uniform(20.0, 45.0), 2)
  else:
    fluxo_veiculos = random.randint(20, 80)
    cpu_calculada = cpu_real
    latencia_sim = round(random.uniform(10.0, 25.0), 2)

  timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

  linha = [
      COD_MAQUINA,
      info_estatica['mac'],
      info_estatica['hostname'],
      info_estatica['so_sistema'],
      info_estatica['arch'],
      timestamp,
      TIPO_DIA_ATUAL,
      fluxo_veiculos,
      round(cpu_calculada, 2),
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
      proc_totais,
      proc_rodando,
      rede_recebida_mb,
      rede_enviada_mb,
      latencia_sim,
      uptime_segundos,
  ]

  with open(caminho_csv, 'a', newline='', encoding='utf-8') as csvfile:
    writer = csv.writer(csvfile, delimiter=';')
    writer.writerow(linha)

  print(
      f'[{timestamp}] {COD_MAQUINA} | Dia: {TIPO_DIA_ATUAL} | Fluxo:'
      f' {fluxo_veiculos} veic/min | CPU: {round(cpu_calculada, 2)}% | RAM:'
      f' {ram_pct}%'
  )
  time.sleep(1)


def main():
  info_estatica = obter_informacoes_estaticas()
  caminho_csv = gerar_caminho_csv_local()

  inicializar_csv(caminho_csv)

  print(f'Iniciando captura local para {COD_MAQUINA}...')
  print(f'Salvando dados em: {caminho_csv}\n')

  for _ in range(QTD_LEITURAS):
    monitorar_e_salvar_local(info_estatica, caminho_csv)

  print(f'\nCaptura concluída. Arquivo salvo localmente em "{caminho_csv}".')


main()
