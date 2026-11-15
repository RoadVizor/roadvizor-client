"""
ETL Gold - FlowPredict (v1)

Essa ETL lê o histórico de cada servidor na camada Silver, aplica uma regressão
linear para a CPU e outra para a RAM (cada uma separada) e calcula em quanto
tempo cada componente deve chegar no limite configurado no banco.

O resultado vai para o bucket Gold em um JSON por empresa, que é o arquivo que a
dashboard (FlowPredict) lê:
    flowpredict/empresa_<id>/dashboard.json

A chave do arquivo é sempre a mesma e ele é sobrescrito a cada execução, então se
eu rodar a ETL duas vezes não gera dado duplicado.

Obs: a regressão linear parte da ideia de que o consumo cresce em linha reta.
Picos de horário e feriados ela não captura (mais detalhes no comentário do R²,
dentro da função ajustar_regressao).
"""

import json
import os
from datetime import datetime, timezone
from io import BytesIO

import boto3
import mysql.connector
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from mysql.connector import Error
from scipy import stats

load_dotenv()

# Componente do banco -> coluna do Silver que eu uso na análise.
# A CPU já vem em %; a RAM vem em bytes (ram_used) e eu converto depois.
COMPONENTES_ANALISADOS = {
    "cpu": "cpu",
    "ram": "ram_used",
}

# Nome do componente no banco -> nome que o front usa no JSON ('CPU' e 'RAM')
ROTULOS_COMPONENTES = {
    "cpu": "CPU",
    "ram": "RAM",
}

# Fator para converter bytes na unidade que está cadastrada no banco (GiB, etc).
# A CPU ('pct') não precisa, porque já está na unidade do banco.
BYTES_POR_UNIDADE = {
    "MiB": 1024 ** 2,
    "GiB": 1024 ** 3,
    "TiB": 1024 ** 4,
}

# Só as colunas do Silver que eu preciso (assim não carrego o JSON de processos)
COLUNAS_SILVER = ["timestamp", "cpu", "ram_used"]

# As leituras são a cada ~2 s. Antes da regressão eu agrupo em médias de 15 min
# para reduzir o ruído e deixar os pontos igualmente espaçados.
INTERVALO_AGREGACAO = "15min"

# Histórico mínimo: 48 pontos de 15 min = 12 h. Com menos que isso não dá para
# confiar na reta.
MIN_PONTOS = 48

# Eu só considero que existe tendência se ela for significativa (p-valor menor
# que ALPHA), relevante (inclinação de pelo menos 0,05 ponto % por hora) e se o
# R² for pelo menos 0,3. O R² mínimo evita tratar o ciclo diário (pico à tarde,
# vale de madrugada) como se fosse uma tendência de crescimento.
ALPHA = 0.05
INCLINACAO_MINIMA = 0.05
R2_MINIMO = 0.3

# Se o estouro estiver a mais de 30 dias, considero "sem estouro previsto"
HORIZONTE_MAXIMO_H = 24 * 30

# Faixas de risco pelo tempo até estourar: até 6h crítico, até 12h alto e até
# 24h médio. Usei esses valores para bater com os exemplos do front, mas dá para
# ajustar.
FAIXAS_RISCO = [(6, "critico"), (12, "alto"), (24, "medio")]

# Dados do gráfico: 5 pontos do passado (-12h até agora) e 5 do futuro
# (agora até +12h), de 3 em 3 horas
PASSO_GRAFICO_H = 3
DESLOCAMENTOS_REAL = [-12, -9, -6, -3, 0]
DESLOCAMENTOS_PREVISTO = [0, 3, 6, 9, 12]

NIVEL_CONFIANCA_FAIXA = 0.95

# Pasta no bucket Gold e versão do formato do JSON (se eu mudar o formato, o front
# consegue perceber)
PREFIXO_GOLD = "flowpredict"
VERSAO_SCHEMA = 1


def buscar_servidores():
    """Busca no banco os servidores e componentes monitorados, com os limites.
    Vem uma linha para cada combinação servidor + componente."""
    try:
        conexao = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME")
        )

        cursor = conexao.cursor(dictionary=True)

        query = """
            SELECT
                s.id_servidor,
                s.empresa_id,
                s.endereco_mac,
                s.apelido,
                s.modelo,
                c.id_componente,
                c.nome AS componente,
                c.unidade_medida,
                c.depende_de,
                sc.limite_min,
                sc.limite_max,
                sc.capacidade_total
            FROM servidor s
            INNER JOIN servidor_componente sc
                ON sc.servidor_id = s.id_servidor
                AND sc.empresa_id = s.empresa_id
            INNER JOIN componente c
                ON c.id_componente = sc.componente_id
            WHERE sc.monitorado = 1;
        """

        cursor.execute(query)
        return cursor.fetchall()

    except Error as erro:
        print(f"Erro ao consultar o banco: {erro}")
        return []

    finally:
        if 'cursor' in locals():
            cursor.close()

        if 'conexao' in locals() and conexao.is_connected():
            conexao.close()


def normalizar_mac(mac):
    """Deixa o MAC igual ao que a ETL Silver usa na pasta do S3 (maiúsculo e
    com '-'), senão a Gold procura uma pasta que não existe."""
    return str(mac).strip().upper().replace(":", "-")


def buscar_dados_silver(mac):
    """Lê todos os CSVs da pasta trusted/<MAC>/ (não só o mais recente) e junta
    tudo em um histórico só."""
    bucket = os.getenv("SILVER_BUCKET")
    regiao = os.getenv("AWS_REGION", "us-east-1")

    s3 = boto3.client("s3", region_name=regiao)

    prefixo = f"trusted/{normalizar_mac(mac)}/"

    try:
        # O S3 lista no máximo 1000 arquivos por vez; o paginator vai passando
        # pelas páginas até acabar
        paginador = s3.get_paginator("list_objects_v2")
        arquivos = []

        for pagina in paginador.paginate(Bucket=bucket, Prefix=prefixo):
            for objeto in pagina.get("Contents", []):
                if not objeto["Key"].endswith(".csv"):
                    continue

                corpo = s3.get_object(Bucket=bucket, Key=objeto["Key"])["Body"].read()

                # Leio só as colunas que preciso; a condição com lambda evita erro
                # se alguma coluna não existir em um CSV mais antigo
                df = pd.read_csv(
                    BytesIO(corpo),
                    sep=";",
                    usecols=lambda coluna: coluna in COLUNAS_SILVER
                )
                arquivos.append(df)

        if not arquivos:
            print(f"Nenhum arquivo Silver encontrado para {mac}.")
            return pd.DataFrame()

        dados = pd.concat(arquivos, ignore_index=True)
        dados["timestamp"] = pd.to_datetime(dados["timestamp"])

        # Se o mesmo horário aparecer em dois arquivos, não pode contar duas vezes
        dados = dados.drop_duplicates(subset="timestamp", keep="last")

        return dados.sort_values("timestamp").reset_index(drop=True)

    except Exception as erro:
        print(f"Erro ao buscar dados Silver de {mac}: {erro}")
        return pd.DataFrame()


def limite_em_percentual(componente):
    """Transforma o limite do banco em % da capacidade. Como limite_max e
    capacidade_total estão na mesma unidade, a divisão funciona para qualquer uma
    (CPU em pct, RAM em GiB...)."""
    return float(componente["limite_max"]) / float(componente["capacidade_total"]) * 100


def converter_para_percentual(valores, componente):
    """Converte a leitura bruta para % da capacidade cadastrada no banco.
    CPU: já está na unidade do banco, só divido pela capacidade.
    RAM: bytes -> unidade do banco (ex.: GiB) -> % da capacidade."""
    fator = BYTES_POR_UNIDADE.get(componente["unidade_medida"], 1)
    return valores / fator / float(componente["capacidade_total"]) * 100


def preparar_serie(dados, componente):
    """Monta a série de um componente para a regressão: horas desde a primeira
    leitura (eixo X) e utilização em % (eixo Y).

    Agrupo as leituras de ~2 s em médias de 15 min, o que reduz o ruído e deixa
    os pontos igualmente espaçados."""
    coluna = COMPONENTES_ANALISADOS[componente["componente"].lower()]

    # Se não tem histórico (ou a coluna não existe), devolvo uma série vazia e o
    # servidor cai em "dados_insuficientes"
    if dados.empty or coluna not in dados.columns:
        return pd.DataFrame(columns=["timestamp", "horas", "valor"])

    serie = (
        dados.set_index("timestamp")[coluna]
        .pipe(pd.to_numeric, errors="coerce")
        .resample(INTERVALO_AGREGACAO)
        .mean()
        .dropna()
    )

    if serie.empty:
        return pd.DataFrame(columns=["timestamp", "horas", "valor"])

    horas = (serie.index - serie.index[0]).total_seconds() / 3600

    return pd.DataFrame({
        "timestamp": serie.index,
        "horas": horas.to_numpy(),
        "valor": converter_para_percentual(serie, componente).to_numpy(),
    })


def ajustar_regressao(serie):
    """Regressão linear simples: valor = intercepto + inclinação * horas.
    É o mesmo que o lm(valor ~ horas) que usei no R na faculdade.

    O que a função devolve:
        inclinacao      quantos pontos % o consumo sobe (ou desce) por hora
        r2              quanto da variação dos dados a reta explica (de 0 a 1)
        p_valor         chance de aparecer essa inclinação se na verdade não
                        houvesse tendência; quanto menor, mais confiável
        erro_residual   o quanto os pontos costumam ficar longe da reta (em %)

    Sobre o R² (importante não vender como garantia):
        - Ele mede o quanto a reta se ajusta ao histórico que ela já viu, não o
          quanto a previsão vai acertar.
        - Série estável dá R² perto de 0 mesmo com previsão certa, porque não
          tem tendência para explicar. R² baixo não quer dizer "modelo ruim".
        - Dados de série temporal são muito parecidos entre si de um instante
          para o outro, o que pode inflar o R² e deixar o p-valor otimista.
        - Pico de horário, feriado e mudança de comportamento quebram a ideia
          de que o consumo segue uma reta.
    """
    x = serie["horas"].to_numpy()
    y = serie["valor"].to_numpy()

    reta = stats.linregress(x, y)

    n = len(x)
    residuos = y - (reta.intercept + reta.slope * x)

    # n - 2 porque são dois valores estimados (intercepto e inclinação)
    erro_residual = float(np.sqrt(np.sum(residuos ** 2) / (n - 2)))

    r2 = reta.rvalue ** 2
    p_valor = reta.pvalue

    return {
        "inclinacao": float(reta.slope),
        "intercepto": float(reta.intercept),
        "r2": 0.0 if np.isnan(r2) else float(r2),
        "p_valor": 1.0 if np.isnan(p_valor) else float(p_valor),
        "erro_residual": erro_residual,
        "n": n,
        "x_medio": float(x.mean()),
        "sxx": float(np.sum((x - x.mean()) ** 2)),
    }


def prever(modelo, x):
    """Valor que a reta dá para os instantes x (horas desde a primeira leitura)."""
    return modelo["intercepto"] + modelo["inclinacao"] * np.asarray(x, dtype=float)


def margem_previsao(modelo, x):
    """Calcula a margem da faixa de previsão de 95% (a área verde do gráfico).

    Obs: essa função eu fiz com auxílio de IA, porque ainda não vi a fórmula do
    intervalo de previsão na faculdade. A ideia que eu entendi é: quanto mais
    longe do histórico a gente prevê, maior a incerteza, então a faixa vai
    abrindo para o futuro. O t_critico vem da distribuição t de Student, que é a
    usada quando se tem uma quantidade limitada de pontos."""
    x = np.asarray(x, dtype=float)
    t_critico = stats.t.ppf((1 + NIVEL_CONFIANCA_FAIXA) / 2, df=modelo["n"] - 2)

    fator = np.sqrt(
        1 + 1 / modelo["n"] + (x - modelo["x_medio"]) ** 2 / modelo["sxx"]
    )

    return t_critico * modelo["erro_residual"] * fator


def classificar_tendencia(modelo):
    """Diz se o consumo está CRESCENTE, DECRESCENTE ou SEM_TENDENCIA.

    Só considero tendência quando as três condições são verdadeiras:
        significativa  p-valor menor que ALPHA (não parece acaso)
        relevante      inclinação de pelo menos INCLINACAO_MINIMA (mudança que importa)
        bem ajustada   R² de pelo menos R2_MINIMO (a reta explica uma parte real)
    SEM_TENDENCIA vale tanto para a série estável quanto para a oscilante: nos
    dois casos uma reta não serve para prever."""
    significativa = modelo["p_valor"] < ALPHA
    relevante = abs(modelo["inclinacao"]) >= INCLINACAO_MINIMA
    bem_ajustada = modelo["r2"] >= R2_MINIMO

    if not (significativa and relevante and bem_ajustada):
        return "SEM_TENDENCIA"

    return "CRESCENTE" if modelo["inclinacao"] > 0 else "DECRESCENTE"


def estimar_estouro(modelo, tendencia, x_agora, valor_atual, limite):
    """Calcula em quantas horas a reta chega no limite, a partir de agora.

    Retornos possíveis:
        0.0    já está acima do limite (pela leitura atual ou pela reta)
        float  horas estimadas (só quando a tendência é CRESCENTE)
        None   sem estouro previsto (sem crescimento ou muito longe no futuro)

    A conta vem da equação da reta:
        limite = ajustado_agora + inclinação * horas
        horas  = (limite - ajustado_agora) / inclinação"""
    ajustado_agora = float(prever(modelo, x_agora))

    if valor_atual >= limite or ajustado_agora >= limite:
        return 0.0

    if tendencia != "CRESCENTE":
        return None

    horas = (limite - ajustado_agora) / modelo["inclinacao"]

    return round(horas, 2) if horas <= HORIZONTE_MAXIMO_H else None


def classificar_risco(estouro_horas):
    """Define o nível de risco pelo tempo até estourar (usa FAIXAS_RISCO)."""
    if estouro_horas is None:
        return "baixo"

    for limite_horas, nivel in FAIXAS_RISCO:
        if estouro_horas <= limite_horas:
            return nivel

    return "baixo"


def montar_real(serie, x_agora):
    """Monta os 5 pontos reais do gráfico (-12h até agora). Cada ponto é a média
    de uma janela de 3 horas. Se a janela não tem dados (histórico menor que
    12h), o ponto fica None, que vira null no JSON."""
    valores = []

    for deslocamento in DESLOCAMENTOS_REAL:
        centro = x_agora + deslocamento
        inicio = centro - PASSO_GRAFICO_H / 2
        fim = min(centro + PASSO_GRAFICO_H / 2, x_agora)

        janela = serie[(serie["horas"] > inicio) & (serie["horas"] <= fim)]
        valores.append(round(float(janela["valor"].mean()), 1) if not janela.empty else None)

    return valores


def montar_previsao(modelo, x_agora):
    """Monta os 5 pontos previstos (agora até +12h) e a faixa de 95%.

    Obs: usei auxílio de IA aqui, principalmente no np.clip e na parte da lista
    interna. Os valores são limitados entre 0 e 100% porque a utilização não
    pode passar disso, mesmo que a reta continue subindo."""
    x = x_agora + np.array(DESLOCAMENTOS_PREVISTO)

    previsto = prever(modelo, x)
    margem = margem_previsao(modelo, x)

    def arredondar(valores):
        return [round(float(v), 1) for v in np.clip(valores, 0, 100)]

    return {
        "previsto": arredondar(previsto),
        "banda_superior": arredondar(previsto + margem),
        "banda_inferior": arredondar(previsto - margem),
    }


def analisar_componente(dados, componente):
    """Faz a análise completa de um componente (CPU ou RAM) de um servidor:
    prepara a série, ajusta a regressão, estima o estouro e classifica o risco.

    Devolvo um dicionário no formato que o front espera. As chaves "modelo" e
    "estatisticas" levam os detalhes que aparecem embaixo do gráfico e nos KPIs."""
    serie = preparar_serie(dados, componente)
    limite = limite_em_percentual(componente)

    # Com pouco histórico não dá para fazer a previsão
    if len(serie) < MIN_PONTOS:
        return {
            "status": "dados_insuficientes",
            "limite": round(limite, 1),
            "pontos": len(serie),
            "pontos_minimos": MIN_PONTOS,
            "estouro_horas": None,
            "nivel": None,
        }

    modelo = ajustar_regressao(serie)
    tendencia = classificar_tendencia(modelo)

    x_agora = float(serie["horas"].iloc[-1])
    valor_atual = float(serie["valor"].iloc[-1])

    estouro = estimar_estouro(modelo, tendencia, x_agora, valor_atual, limite)

    return {
        "status": "ok",
        "limite": round(limite, 1),
        "valor_atual": round(valor_atual, 1),
        "real": montar_real(serie, x_agora),
        **montar_previsao(modelo, x_agora),
        "estouro_horas": estouro,
        "nivel": classificar_risco(estouro),
        "tendencia": tendencia,
        "modelo": {
            "inclinacao_pct_por_hora": round(modelo["inclinacao"], 4),
            "r2": round(modelo["r2"], 4),
            "p_valor": round(modelo["p_valor"], 6),
            "erro_residual_pct": round(modelo["erro_residual"], 2),
            "pontos": modelo["n"],
            "horas_historico": round(x_agora, 1),
        },
        "estatisticas": {
            "media": round(float(serie["valor"].mean()), 1),
            "desvio_padrao": round(float(serie["valor"].std()), 2),
            "maximo": round(float(serie["valor"].max()), 1),
            "pontos_acima_limite": int((serie["valor"] > limite).sum()),
        },
    }


def agrupar_por_servidor(linhas):
    """O banco devolve uma linha por servidor + componente. Aqui eu agrupo por
    servidor, assim leio o histórico do S3 uma vez só por servidor (e não uma
    vez para a CPU e outra para a RAM)."""
    servidores = {}

    for linha in linhas:
        if linha["componente"].lower() not in COMPONENTES_ANALISADOS:
            continue

        servidor = servidores.setdefault(
            (linha["empresa_id"], linha["id_servidor"]),
            {
                "id": linha["id_servidor"],
                "empresa_id": linha["empresa_id"],
                "nome": linha["apelido"],
                "mac": linha["endereco_mac"],
                "linhas": [],
            },
        )
        servidor["linhas"].append(linha)

    return list(servidores.values())


def analisar_servidor(servidor):
    """Lê o histórico do servidor e analisa cada componente dele (CPU e/ou RAM)."""
    dados = buscar_dados_silver(servidor["mac"])

    print(f"\nServidor {servidor['nome']} ({servidor['mac']}): "
          f"{len(dados)} registros no Silver")

    componentes = {}

    for linha in servidor["linhas"]:
        rotulo = ROTULOS_COMPONENTES[linha["componente"].lower()]
        componentes[rotulo] = analisar_componente(dados, linha)

    return {
        "id": servidor["id"],
        "empresa_id": servidor["empresa_id"],
        "nome": servidor["nome"],
        "mac": servidor["mac"],
        "componentes": componentes,
    }


def chave_ordenacao(analise):
    """Chave que uso para ordenar do mais urgente para o menos urgente:
    1º quem tem estouro previsto vem antes de quem não tem (None vai para o fim)
    2º menor tempo até estourar primeiro (0 = já está acima do limite)
    3º no empate, quem está mais perto do limite"""
    estouro = analise["estouro_horas"]
    utilizacao = analise["valor_atual"] / analise["limite"]

    return (estouro is None, estouro or 0, -utilizacao)


def pior_componente(servidor):
    """Devolve o componente mais urgente do servidor (nome e análise), ou None
    se nenhum tiver dados suficientes."""
    validos = [
        (rotulo, analise)
        for rotulo, analise in servidor["componentes"].items()
        if analise["status"] == "ok"
    ]

    if not validos:
        return None

    return min(validos, key=lambda item: chave_ordenacao(item[1]))


def montar_ranking(servidores):
    """Monta o ranking de risco: um item por servidor, mostrando o pior
    componente dele, do mais urgente para o menos urgente. Servidores sem dados
    suficientes ficam de fora (eles entram na contagem servidores_sem_dados)."""
    itens = []

    for servidor in servidores:
        pior = pior_componente(servidor)

        if pior is None:
            continue

        rotulo, analise = pior

        itens.append((
            chave_ordenacao(analise),
            {
                "servidor_id": servidor["id"],
                "componente": rotulo,
                "estouro_horas": analise["estouro_horas"],
                "nivel": analise["nivel"],
            },
        ))

    itens.sort(key=lambda item: item[0])

    return [item for _, item in itens]


def montar_resumo(servidores, ranking):
    """Calcula os KPIs do topo da dashboard.

    em_risco, criticos e altos   contam servidores, pelo pior nível de cada um
    menor_estouro                menor tempo maior que zero entre todas as análises
                                 (quem já passou do limite não entra, já estourou)
    confianca_modelo             média do R² (em %) só das análises com tendência.
                                 É qualidade do ajuste, não chance de acertar
    janela_horas                 maior histórico usado em uma análise
    anomalias_hoje               ainda não tem na v1 (fica null)"""
    analises = [
        (servidor["nome"], rotulo, analise)
        for servidor in servidores
        for rotulo, analise in servidor["componentes"].items()
        if analise["status"] == "ok"
    ]

    criticos = sum(1 for item in ranking if item["nivel"] == "critico")
    altos = sum(1 for item in ranking if item["nivel"] == "alto")

    # Quem já está acima do limite tem estouro_horas == 0.0, que conta como falso
    # no if, então fica de fora de propósito
    futuros = [
        (analise["estouro_horas"], nome, rotulo)
        for nome, rotulo, analise in analises
        if analise["estouro_horas"]
    ]

    menor_estouro = None
    if futuros:
        horas, nome, rotulo = min(futuros)
        menor_estouro = {"servidor": nome, "componente": rotulo, "horas": horas}

    r2_com_tendencia = [
        analise["modelo"]["r2"]
        for _, _, analise in analises
        if analise["tendencia"] != "SEM_TENDENCIA"
    ]

    confianca = round(float(np.mean(r2_com_tendencia)) * 100) if r2_com_tendencia else None

    janela = max(
        (analise["modelo"]["horas_historico"] for _, _, analise in analises),
        default=None,
    )

    return {
        "total_servidores": len(servidores),
        "em_risco": criticos + altos,
        "criticos": criticos,
        "altos": altos,
        "servidores_sem_dados": len(servidores) - len(ranking),
        "menor_estouro": menor_estouro,
        "anomalias_hoje": None,
        "confianca_modelo": confianca,
        "janela_horas": round(janela) if janela is not None else None,
    }


def montar_dashboard(servidores):
    """Monta o JSON final de uma empresa, no formato que o front consome:
        versao_schema  versão do formato do JSON
        gerado_em      quando a Gold rodou (UTC)
        resumo         KPIs do topo
        ranking        servidores do mais para o menos urgente
        servidores     detalhe de cada servidor e componente (usado no gráfico)"""
    ranking = montar_ranking(servidores)

    return {
        "versao_schema": VERSAO_SCHEMA,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resumo": montar_resumo(servidores, ranking),
        "ranking": ranking,
        "servidores": [
            {
                "id": s["id"],
                "nome": s["nome"],
                "mac": s["mac"],
                "componentes": s["componentes"],
            }
            for s in servidores
        ],
    }


def salvar_gold(dashboard, empresa_id):
    """Grava o dashboard.json da empresa no bucket Gold e devolve o caminho s3://.

    A chave é sempre a mesma, então cada execução sobrescreve o arquivo anterior
    (por isso não duplica se eu rodar de novo). Como é um arquivo só, quem lê pega
    a versão antiga ou a nova, nunca um arquivo pela metade."""
    bucket = os.getenv("GOLD_BUCKET")

    if not bucket:
        raise ValueError("Variável de ambiente GOLD_BUCKET não definida.")

    chave = f"{PREFIXO_GOLD}/empresa_{empresa_id}/dashboard.json"

    # allow_nan=False: NaN não existe em JSON. Se aparecer algum, dá erro aqui em
    # vez de gravar um arquivo que o navegador não consegue ler
    corpo = json.dumps(dashboard, ensure_ascii=False, indent=2, allow_nan=False)

    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))

    s3.put_object(
        Bucket=bucket,
        Key=chave,
        Body=corpo.encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )

    return f"s3://{bucket}/{chave}"


def imprimir_resumo(empresa_id, destino, dashboard):
    """Mostra no console um resumo do que foi gerado, só para eu acompanhar."""
    resumo = dashboard["resumo"]

    print(f"\nEmpresa {empresa_id}: JSON gravado em {destino}")
    print(f"  Servidores: {resumo['total_servidores']} | em risco: {resumo['em_risco']} "
          f"({resumo['criticos']} críticos, {resumo['altos']} altos) | "
          f"sem dados: {resumo['servidores_sem_dados']}")

    for item in dashboard["ranking"]:
        print(f"  {item['nivel']:8} servidor {item['servidor_id']} · "
              f"{item['componente']} · estouro: {item['estouro_horas']}")


def main():
    linhas = buscar_servidores()

    if not linhas:
        print("Nenhum servidor monitorado encontrado.")
        return

    print(f"Servidores e componentes encontrados: {len(linhas)}")

    # Na v1 só analiso CPU e RAM; disco e rede ficam para depois
    servidores = [analisar_servidor(s) for s in agrupar_por_servidor(linhas)]

    # Faço um dashboard por empresa, para cada cliente ver só os seus servidores
    por_empresa = {}
    for servidor in servidores:
        por_empresa.setdefault(servidor["empresa_id"], []).append(servidor)

    for empresa_id, lista in por_empresa.items():
        dashboard = montar_dashboard(lista)

        try:
            destino = salvar_gold(dashboard, empresa_id)
        except Exception as erro:
            print(f"Erro ao gravar o Gold da empresa {empresa_id}: {erro}")
            continue

        imprimir_resumo(empresa_id, destino, dashboard)


if __name__ == "__main__":
    main()