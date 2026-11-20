# Bom, aqui é o simulador do pedagio
# é melhor pra gente para parecer que estamos pegando dados reais

import datetime
import random

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


