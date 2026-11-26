"""
ETL Gold - Dashboard "Saúde da Infraestrutura" (Roadvizor) - versão final
 
A camada Silver é SOMENTE LIDA, exatamente como está hoje (prefixo plano `trusted/`,
um CSV por coleta, `mac` dentro do arquivo). Toda adaptação acontece aqui:
  - o servidor é identificado pelo `mac` DENTRO do CSV (o nome do arquivo não é usado);
  - a taxa de rede (Mbps) é calculada aqui a partir dos contadores acumulados;
  - as colunas `*_faixa` da Silver são ignoradas (limites vêm do banco).
 
Saída (1 arquivo por empresa+região, mais consolidado e índice):
  {PREFIXO_GOLD}empresa={id}/indice.json   (gravado por último)
  {PREFIXO_GOLD}empresa={id}/todas.json
  {PREFIXO_GOLD}empresa={id}/{slug-da-regiao}.json
 
Dependências: boto3, pandas (+ numpy, que já vem com ele) e PyMySQL
(único extra: driver para ler servidores/limites/regiões direto do MySQL).
Requer pandas >= 2.0.
 
Regras:
  - Limites do banco são convertidos para % da capacidade; o uso (%) é comparado com eles.
  - Status do componente: média na janela (últimos JANELA_STATUS_MIN min de dados do
    servidor) >= limite de atenção / crítico. Status do servidor = pior componente.
  - Sem leitura há mais de OFFLINE_APOS_MIN min = OFFLINE (fora do % e das 3 categorias).
  - Pontuação 0-100: 100 até o limite de atenção, 50 no crítico, 0 em 100% de uso;
    servidor = pior componente; saúde geral = média dos servidores online.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
 
import boto3
import numpy as np
import pandas as pd
import pymysql
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError
 
 
# ============================== CONFIGURAÇÃO ==============================
 
REGIAO_AWS = "us-east-1"
 
BUCKET_SILVER = "04261104-gustavo-silver"
PREFIXO_SILVER = "trusted/"
SEPARADOR_SILVER = ";"
 
# AJUSTAR: bucket/prefixo da Gold no seu ambiente.
BUCKET_GOLD = "04261104-gustavo-gold"
PREFIXO_GOLD = "saude_infraestrutura/"
 
# Fuso em que o "timestamp" dos CSVs foi gravado (sem offset nos arquivos).
FUSO = "America/Sao_Paulo"
MAX_THREADS_S3 = 32
 
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "roadvizor_user"),
    "password": os.getenv("DB_PASSWORD", "sptech"),
    "database": os.getenv("DB_NAME", "roadvizor"),
}
 
# Janelas e coleta
HORAS_HISTORICO = 24          # horas calculadas
HORAS_GRAFICO = 10            # horas enviadas no gráfico
JANELA_STATUS_MIN = 10        # média dos últimos N min de dados do servidor
OFFLINE_APOS_MIN = 20         # maior que 5 min de coleta + atraso do pipeline
COLETA_MIN = 5                # 1 arquivo a cada 5 min...
LEITURAS_POR_COLETA = 10      # ...com 10 leituras
 
# Atenção: usa a coluna `limite_atencao` de servidor_componente quando existir;
# senão, atenção = crítico * FATOR_ATENCAO_PADRAO.
FATOR_ATENCAO_PADRAO = 0.85
 
PONTUACAO_NO_CRITICO = 50.0
 
# componente.nome (banco) -> coluna de uso na Silver.
# CONFIRMAR 'disco_percent' (segue a regra "_percent" do ETL Silver).
METRICAS = {
    "cpu": {"coluna": "cpu"},
    "ram": {"coluna": "ram_percent"},
    "disco": {"coluna": "disco_percent"},
    "placa_rede": {"coluna": "rede_mbps"},   # em Mbps (não em %)
}
CHAVE_JSON = {"cpu": "cpu", "ram": "ram", "disco": "disco", "placa_rede": "rede"}
 
# Rede: se a Silver já trouxer `rede_mbps`, ela é usada. Se não, calcula-se a taxa
# pela diferença dos contadores acumulados do psutil.net_io_counters().
# CONFIRMAR os nomes das colunas de contador na Silver:
REDE_COLUNAS_CONTADOR = ["rede_bytes_sent", "rede_bytes_recv"]
REDE_LACUNA_MAX_S = 900       # ignora taxa entre leituras separadas por mais que isso
REDE_TOLERANCIA_CAPACIDADE = 1.2  # taxa > 120% da capacidade do link é descartada
 
# Região vem da tabela `regiao` + servidor.regiao_id. Enquanto elas não existirem
# no banco, o script cai no SQL abaixo e usa "Sem região".
SQL_SERVIDORES = """
    SELECT s.id_servidor  AS servidor_id,
           s.empresa_id   AS empresa_id,
           s.endereco_mac AS mac,
           s.apelido      AS apelido,
           COALESCE(r.nome, 'Sem região') AS regiao,
           COALESCE(r.slug, 'sem-regiao') AS regiao_slug
    FROM servidor s
    LEFT JOIN regiao r ON r.id_regiao = s.regiao_id
"""
SQL_SERVIDORES_SEM_REGIAO = """
    SELECT s.id_servidor  AS servidor_id,
           s.empresa_id   AS empresa_id,
           s.endereco_mac AS mac,
           s.apelido      AS apelido,
           'Sem região'   AS regiao,
           'sem-regiao'   AS regiao_slug
    FROM servidor s
"""
SQL_LIMITES = """
    SELECT sc.*, c.nome AS componente, c.unidade_medida
    FROM servidor_componente sc
    JOIN componente c ON c.id_componente = sc.componente_id
    WHERE sc.monitorado = 1
"""
 
SLUGS_RESERVADOS = {"todas", "indice"}
CHAVE = ["empresa_id", "servidor_id"]
NOME_NIVEL = {0: "SAUDAVEL", 1: "ATENCAO", 2: "CRITICO"}
 
s3 = boto3.client(
    "s3",
    region_name=REGIAO_AWS,
    config=Config(max_pool_connections=MAX_THREADS_S3),
)
 
 
# ============================== UTILITÁRIOS ==============================
 
def normalizar_mac(serie):
    """Só os dígitos hex, maiúsculo (aceita 'aa:bb:...' e 'AABB...')."""
    return (
        serie.astype("string")
        .str.upper()
        .str.replace(r"[^0-9A-F]", "", regex=True)
    )
 
 
def _num(valor, casas=1):
    if valor is None or pd.isna(valor):
        return None
    return round(float(valor), casas)
 
 
def _int(valor):
    if valor is None or pd.isna(valor):
        return None
    return int(round(float(valor)))
 
 
def _iso(ts):
    """ISO-8601 com offset do fuso de operação (None se vazio)."""
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).tz_localize(FUSO).isoformat()
 
 
def _vazio(colunas):
    """DataFrame vazio com tipos compatíveis para merge/comparações."""
    df = pd.DataFrame(columns=colunas)
    df = df.astype({c: "int64" for c in CHAVE if c in colunas})
    for coluna in ("hora", "timestamp", "ultima_leitura"):
        if coluna in colunas:
            df[coluna] = pd.to_datetime(df[coluna])
    return df
 
 
# ============================== BANCO DE DADOS ==============================
 
def consultar(sql):
    conexao = pymysql.connect(
        **DB_CONFIG,
        charset="utf8mb4",
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conexao.cursor() as cursor:
            cursor.execute(sql)
            return pd.DataFrame(cursor.fetchall())
    finally:
        conexao.close()
 
 
def carregar_servidores():
    try:
        df = consultar(SQL_SERVIDORES)
    except pymysql.MySQLError as erro:
        codigo = erro.args[0] if erro.args else None
        if codigo not in (1146, 1054):  # tabela/coluna inexistente
            raise
        print("[aviso] tabela 'regiao' / coluna servidor.regiao_id ainda não existem; usando 'Sem região'.")
        df = consultar(SQL_SERVIDORES_SEM_REGIAO)
 
    if df.empty:
        raise ValueError("Nenhum servidor cadastrado no banco.")
 
    df["mac"] = normalizar_mac(df["mac"])
    df["empresa_id"] = df["empresa_id"].astype("int64")
    df["servidor_id"] = df["servidor_id"].astype("int64")
 
    return df
 
 
def _para_pct(valor, eh_pct, capacidade):
    """Limite absoluto (MiB/GiB/TiB/Mbps/Gbps) -> % da capacidade; 'pct' fica como está."""
    return np.where(eh_pct, valor, valor / capacidade.where(capacidade > 0) * 100)
 
 
def carregar_limites():
    """Limites por servidor/componente já em % de uso (+ capacidade da rede em Mbps)."""
 
    colunas_saida = CHAVE + ["componente", "critico_pct", "atencao_pct",
                             "origem_atencao", "capacidade_mbps"]
    df = consultar(SQL_LIMITES)
 
    if df.empty:
        return _vazio(colunas_saida)
 
    df = df[df["componente"].isin(METRICAS)].copy()
 
    numericas = ["limite_max", "capacidade_total"]
    if "limite_atencao" in df.columns:
        numericas.append("limite_atencao")
    for coluna in numericas:
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
 
    unidade = df["unidade_medida"].astype(str).str.lower()
    eh_pct = unidade == "pct"
 
    df["critico_pct"] = _para_pct(df["limite_max"], eh_pct, df["capacidade_total"])
 
    if "limite_atencao" in df.columns:
        df["atencao_pct"] = _para_pct(df["limite_atencao"], eh_pct, df["capacidade_total"])
    else:
        df["atencao_pct"] = np.nan
 
    df["origem_atencao"] = np.where(df["atencao_pct"].notna(), "banco", "fator_padrao")
    df["atencao_pct"] = df["atencao_pct"].fillna(df["critico_pct"] * FATOR_ATENCAO_PADRAO)
    df["atencao_pct"] = np.minimum(df["atencao_pct"], df["critico_pct"])
 
    fator = np.where(unidade == "gbps", 1000.0, 1.0)
    df["capacidade_mbps"] = np.where(
        df["componente"] == "placa_rede", df["capacidade_total"] * fator, np.nan
    )
 
    df = df.dropna(subset=["critico_pct"])
    df["critico_pct"] = df["critico_pct"].clip(0, 100)
    df["atencao_pct"] = df["atencao_pct"].clip(0, 100)
    df["empresa_id"] = df["empresa_id"].astype("int64")
    df["servidor_id"] = df["servidor_id"].astype("int64")
 
    return df[colunas_saida].reset_index(drop=True)
 
 
# ============================== LEITURA DA SILVER ==============================
 
def listar_chaves_silver(inicio):
    """Lista CSVs do prefixo plano da Silver modificados desde `inicio` (-2h de margem)."""
 
    corte_utc = (
        pd.Timestamp(inicio).tz_localize(FUSO).tz_convert("UTC") - pd.Timedelta(hours=2)
    ).to_pydatetime()
 
    chaves = []
    paginador = s3.get_paginator("list_objects_v2")
 
    for pagina in paginador.paginate(Bucket=BUCKET_SILVER, Prefix=PREFIXO_SILVER):
        for objeto in pagina.get("Contents", []):
            if (
                objeto["Key"].lower().endswith(".csv")
                and objeto["LastModified"] >= corte_utc
            ):
                chaves.append(objeto["Key"])
 
    return chaves
 
 
def ler_csv_silver(chave):
    """Lê só as colunas necessárias. Devolve None se o arquivo estiver inválido."""
 
    colunas_uteis = (
        {"mac", "timestamp"}
        | {cfg["coluna"] for cfg in METRICAS.values()}
        | set(REDE_COLUNAS_CONTADOR)
    )
 
    try:
        corpo = s3.get_object(Bucket=BUCKET_SILVER, Key=chave)["Body"].read()
        df = pd.read_csv(
            BytesIO(corpo),
            sep=SEPARADOR_SILVER,
            encoding="utf-8",
            usecols=lambda c: c in colunas_uteis,
            dtype={"mac": str},
        )
    except Exception as erro:  # um arquivo ruim não derruba o ciclo
        print(f"[aviso] falha ao ler {chave}: {erro}")
        return None
 
    if not {"mac", "timestamp"} <= set(df.columns):
        print(f"[aviso] {chave} sem colunas mac/timestamp")
        return None
 
    return df
 
 
def ler_silver(chaves):
    with ThreadPoolExecutor(max_workers=MAX_THREADS_S3) as pool:
        resultados = list(pool.map(ler_csv_silver, chaves))
 
    validos = [df for df in resultados if df is not None and not df.empty]
    com_erro = sum(1 for df in resultados if df is None)
 
    if not validos:
        return pd.DataFrame(columns=["mac", "timestamp"]), com_erro
 
    return pd.concat(validos, ignore_index=True), com_erro
 
 
# ============================== TRANSFORMAÇÃO ==============================
 
def preparar_leituras(df, inicio, agora):
    df = df.copy()
    df["mac"] = normalizar_mac(df["mac"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["mac", "timestamp"])
    df = df[(df["timestamp"] >= inicio) & (df["timestamp"] <= agora)]
    return df.drop_duplicates(subset=["mac", "timestamp"], keep="last")
 
 
def calcular_rede_mbps(df):
    """Cria `rede_mbps` a partir dos contadores acumulados (se a Silver não trouxer a taxa)."""
 
    if "rede_mbps" in df.columns:
        df["rede_mbps"] = pd.to_numeric(df["rede_mbps"], errors="coerce")
        return df
 
    colunas = [c for c in REDE_COLUNAS_CONTADOR if c in df.columns]
    if len(colunas) != len(REDE_COLUNAS_CONTADOR):
        return df
 
    df = df.sort_values(["mac", "timestamp"]).copy()
    total = df[colunas].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=len(colunas))
 
    delta_bytes = total.groupby(df["mac"]).diff()
    delta_seg = df.groupby("mac")["timestamp"].diff().dt.total_seconds()
 
    mbps = delta_bytes * 8 / delta_seg / 1e6
    # contador reiniciado (delta negativo) ou lacuna longa entre leituras -> sem valor
    valido = (delta_bytes >= 0) & (delta_seg > 0) & (delta_seg <= REDE_LACUNA_MAX_S)
    df["rede_mbps"] = mbps.where(valido)
 
    return df
 
 
def avisar_colunas_ausentes(leituras):
    for nome, cfg in METRICAS.items():
        if cfg["coluna"] not in leituras.columns:
            print(f"[aviso] coluna '{cfg['coluna']}' não encontrada na Silver: "
                  f"componente '{nome}' será ignorado.")
 
 
def checar_cobertura(leituras, agora):
    """Detecta servidores que chegam à Silver com bem menos leituras que o esperado
    (sintoma de arquivos descartados/sobrescritos no ETL Silver)."""
 
    esperado = (60 // COLETA_MIN) * LEITURAS_POR_COLETA
    recentes = leituras[leituras["timestamp"] > agora - pd.Timedelta(hours=1)]
    contagem = recentes.groupby("mac").size()
    baixos = contagem[contagem < esperado * 0.5]
 
    if len(baixos):
        print(f"[aviso] {len(baixos)} servidor(es) com menos de 50% das leituras esperadas "
              f"({esperado}/h) na última hora: a Silver pode estar perdendo arquivos. "
              f"MACs: {list(baixos.index[:10])}")
 
 
def montar_base_longa(leituras, servidores, limites):
    """Wide (1 linha por leitura) -> long (leitura x componente), com limites."""
 
    colunas_longo = CHAVE + ["timestamp", "hora", "componente", "valor",
                             "uso_pct", "atencao_pct", "critico_pct"]
 
    colunas = {
        cfg["coluna"]: nome
        for nome, cfg in METRICAS.items()
        if cfg["coluna"] in leituras.columns
    }
 
    if leituras.empty or not colunas:
        return _vazio(colunas_longo), []
 
    desconhecidos = sorted(set(leituras["mac"]) - set(servidores["mac"]))
 
    longo = leituras.melt(
        id_vars=["mac", "timestamp"],
        value_vars=list(colunas),
        var_name="componente",
        value_name="valor",
    )
    longo["componente"] = longo["componente"].map(colunas)
    longo["valor"] = pd.to_numeric(longo["valor"], errors="coerce")
    longo = longo.dropna(subset=["valor"])
 
    longo = longo.merge(servidores[["mac"] + CHAVE], on="mac", how="inner")
    longo = longo.merge(
        limites[CHAVE + ["componente", "atencao_pct", "critico_pct", "capacidade_mbps"]],
        on=CHAVE + ["componente"],
        how="inner",
    )
 
    # Rede vem em Mbps: converte para % da capacidade; os demais já são %.
    capacidade = longo["capacidade_mbps"].where(longo["capacidade_mbps"] > 0)
 
    # Taxa acima da capacidade do link é impossível (contador inconsistente): descarta.
    impossivel = (longo["componente"] == "placa_rede") & (longo["valor"] > capacidade * REDE_TOLERANCIA_CAPACIDADE)
    longo = longo[~impossivel].copy()
    capacidade = capacidade[longo.index]
 
    longo["uso_pct"] = np.where(
        longo["componente"] == "placa_rede", longo["valor"] / capacidade * 100, longo["valor"]
    )
    longo["uso_pct"] = longo["uso_pct"].clip(0, 100)
    longo = longo.dropna(subset=["uso_pct"])
 
    longo["hora"] = longo["timestamp"].dt.floor("h")
 
    return longo[colunas_longo], desconhecidos
 
 
def classificar(uso, atencao, critico):
    """0 = saudável, 1 = atenção, 2 = crítico (limite é 'não pode atingir': >=)."""
    return np.select([uso >= critico, uso >= atencao], [2, 1], default=0)
 
 
def pontuar(uso, atencao, critico):
    """Pontuação contínua 0-100 (ver docstring do módulo)."""
    folga_atencao = np.maximum(critico - atencao, 1e-9)
    folga_critico = np.maximum(100.0 - critico, 1e-9)
 
    pontos = np.where(
        uso <= atencao,
        100.0,
        np.where(
            uso < critico,
            100.0 - (100.0 - PONTUACAO_NO_CRITICO) * (uso - atencao) / folga_atencao,
            PONTUACAO_NO_CRITICO * (100.0 - uso) / folga_critico,
        ),
    )
    return np.clip(pontos, 0.0, 100.0)
 
 
def avaliar(longo, chave):
    """Avalia cada componente por grupo (servidor ou servidor+hora)."""
 
    saida = chave + ["componente", "valor_medio", "uso_medio", "atencao_pct",
                     "critico_pct", "nivel", "pontuacao"]
 
    if longo.empty:
        return _vazio(saida)
 
    g = longo.groupby(chave + ["componente"], as_index=False).agg(
        valor_medio=("valor", "mean"),
        uso_medio=("uso_pct", "mean"),
        atencao_pct=("atencao_pct", "first"),
        critico_pct=("critico_pct", "first"),
    )
 
    uso = g["uso_medio"].to_numpy(dtype=float)
    atencao = g["atencao_pct"].to_numpy(dtype=float)
    critico = g["critico_pct"].to_numpy(dtype=float)
 
    g["nivel"] = classificar(uso, atencao, critico)
    g["pontuacao"] = pontuar(uso, atencao, critico)
 
    return g[saida]
 
 
def consolidar(comp, chave):
    """Servidor = pior componente (maior nível; empate -> menor pontuação)."""
 
    saida = chave + ["nivel", "pontuacao", "componente"]
 
    if comp.empty:
        return _vazio(saida)
 
    ordenado = comp.sort_values(["nivel", "pontuacao"], ascending=[False, True])
    return ordenado.drop_duplicates(chave)[saida]
 
 
def montar_status_atual(servidores_mon, longo, agora):
    """Status de cada servidor monitorado em `agora`. Retorna (status, componentes)."""
 
    if longo.empty:
        ultima = _vazio(CHAVE + ["ultima_leitura"])
    else:
        ultima = (
            longo.groupby(CHAVE, as_index=False)["timestamp"]
            .max()
            .rename(columns={"timestamp": "ultima_leitura"})
        )
 
    base = servidores_mon.merge(ultima, on=CHAVE, how="left")
    base["ultima_leitura"] = pd.to_datetime(base["ultima_leitura"])
 
    limite_online = agora - pd.Timedelta(minutes=OFFLINE_APOS_MIN)
    base["online"] = base["ultima_leitura"].notna() & (base["ultima_leitura"] >= limite_online)
 
    # Janela ancorada na última leitura de cada servidor: o atraso do pipeline
    # não faz um servidor saudável "sumir" da janela.
    janela = longo.merge(ultima, on=CHAVE, how="inner")
    janela = janela[
        janela["timestamp"] > janela["ultima_leitura"] - pd.Timedelta(minutes=JANELA_STATUS_MIN)
    ]
    comp = avaliar(janela, CHAVE)
    status = consolidar(comp, CHAVE)
 
    base = base.merge(status, on=CHAVE, how="left")
    base["status"] = np.where(base["online"], base["nivel"].map(NOME_NIVEL), "OFFLINE")
    base.loc[~base["online"], ["nivel", "pontuacao", "componente"]] = np.nan
 
    return base, comp
 
 
def montar_status_horario(longo):
    chave = CHAVE + ["hora"]
    return consolidar(avaliar(longo, chave), chave)
 
 
# ============================== MONTAGEM DO JSON ==============================
 
def filtrar_escopo(df, servidores_escopo):
    return df.merge(servidores_escopo[CHAVE], on=CHAVE, how="inner")
 
 
def resumir(atual):
    online = atual[atual["status"] != "OFFLINE"]
 
    return {
        "percentual": _int(online["pontuacao"].mean()) if len(online) else None,
        "totais": int(len(online)),
        "criticos": int((online["status"] == "CRITICO").sum()),
        "atencao": int((online["status"] == "ATENCAO").sum()),
        "saudaveis": int((online["status"] == "SAUDAVEL").sum()),
        "offline": int((atual["status"] == "OFFLINE").sum()),
    }
 
 
def calcular_variacao(hoje, ontem):
    """Diferença absoluta vs. mesmo horário de ontem (pp para saúde; contagem para o resto)."""
    chaves = ["percentual", "totais", "criticos", "atencao", "saudaveis"]
    nomes = {"percentual": "saude_percent"}
 
    # Sem nenhum servidor avaliável ontem não há base de comparação.
    if ontem["totais"] == 0:
        return {nomes.get(k, k): None for k in chaves}
 
    return {
        nomes.get(k, k): (None if hoje[k] is None or ontem[k] is None else hoje[k] - ontem[k])
        for k in chaves
    }
 
 
def montar_grafico(horario_escopo, slots):
    if horario_escopo.empty:
        medias = pd.Series(dtype=float)
    else:
        medias = horario_escopo.groupby("hora")["pontuacao"].mean()
 
    ultimos = slots[-HORAS_GRAFICO:]
 
    return {
        "horas": [f"{s:%H}:00" for s in ultimos],
        "saude_percents": [
            _int(medias[s]) if s in medias.index else None for s in ultimos
        ],
    }
 
 
def mapa_componentes(comp):
    mapa = {}
    for r in comp.itertuples(index=False):
        mapa.setdefault((int(r.empresa_id), int(r.servidor_id)), {})[
            CHAVE_JSON[r.componente]
        ] = _num(r.valor_medio)
    return mapa
 
 
def montar_servidores(atual_escopo, mapa):
    ordem = {"CRITICO": 0, "ATENCAO": 1, "OFFLINE": 2, "SAUDAVEL": 3}
    df = atual_escopo.assign(_ordem=atual_escopo["status"].map(ordem))
    df = df.sort_values(["_ordem", "apelido"])
 
    itens = []
    for r in df.itertuples(index=False):
        valores = {} if r.status == "OFFLINE" else mapa.get((int(r.empresa_id), int(r.servidor_id)), {})
        itens.append({
            "id": int(r.servidor_id),
            "apelido": r.apelido,
            "status": r.status,
            "componentes": {k: valores.get(k) for k in CHAVE_JSON.values()},
            "ultima_atualizacao": _iso(r.ultima_leitura),
        })
    return itens
 
 
def gravar_json(chave, conteudo):
    corpo = json.dumps(
        conteudo, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
 
    s3.put_object(
        Bucket=BUCKET_GOLD,
        Key=chave,
        Body=corpo,
        ContentType="application/json; charset=utf-8",
        CacheControl="no-cache",
    )
 
    print(f"Gold gravado: s3://{BUCKET_GOLD}/{chave} ({len(corpo)} bytes)")
 
 
# ============================== ORQUESTRAÇÃO ==============================
 
def main():
    print("Iniciando ETL Gold - Saúde da Infraestrutura...")
 
    agora = pd.Timestamp.now(tz=FUSO).tz_localize(None)  # relógio de parede local
    hora_atual = agora.floor("h")
    slots = list(pd.date_range(hora_atual - pd.Timedelta(hours=HORAS_HISTORICO - 1),
                               periods=HORAS_HISTORICO, freq="h"))
    # Lê um pouco além de 24h para também montar a comparação "vs. ontem".
    inicio_leitura = (hora_atual - pd.Timedelta(hours=HORAS_HISTORICO)
                      - pd.Timedelta(minutes=JANELA_STATUS_MIN))
 
    servidores = carregar_servidores()
    limites = carregar_limites()
    monitorados = servidores.merge(limites[CHAVE].drop_duplicates(), on=CHAVE, how="inner")
    print(f"Servidores cadastrados: {len(servidores)} | com componentes monitorados: {len(monitorados)}")
 
    chaves = listar_chaves_silver(inicio_leitura)
    bruto, com_erro = ler_silver(chaves)
    print(f"Arquivos Silver lidos: {len(chaves) - com_erro} | com erro: {com_erro}")
 
    if bruto.empty:
        leituras = bruto
    else:
        leituras = preparar_leituras(bruto, inicio_leitura, agora)
        leituras = calcular_rede_mbps(leituras)
        avisar_colunas_ausentes(leituras)
        checar_cobertura(leituras, agora)
 
    longo, desconhecidos = montar_base_longa(leituras, servidores, limites)
 
    if desconhecidos:
        print(f"[aviso] {len(desconhecidos)} MAC(s) na Silver sem cadastro no banco: {desconhecidos[:10]}")
 
    ultima_global = longo["timestamp"].max() if not longo.empty else pd.NaT
    dados_defasados = bool(
        pd.isna(ultima_global)
        or (agora - ultima_global) > pd.Timedelta(minutes=OFFLINE_APOS_MIN)
    )
    if dados_defasados:
        print("[aviso] Nenhuma leitura recente: verifique Bronze/Silver antes de confiar no 'offline'.")
 
    # Estado agora, estado no mesmo horário de ontem e histórico por hora.
    atual, comp_atual = montar_status_atual(monitorados, longo, agora)
    agora_ontem = agora - pd.Timedelta(hours=24)
    atual_ontem, _ = montar_status_atual(
        monitorados, longo[longo["timestamp"] <= agora_ontem], agora_ontem
    )
    horario = montar_status_horario(longo)
    horario = horario[horario["hora"] >= slots[0]]
    mapa = mapa_componentes(comp_atual)
 
    gerado_em = pd.Timestamp.now(tz=FUSO).isoformat()
 
    for empresa_id, srv_empresa in monitorados.groupby("empresa_id"):
        atual_e = filtrar_escopo(atual, srv_empresa)
        ontem_e = filtrar_escopo(atual_ontem, srv_empresa)
        horario_e = filtrar_escopo(horario, srv_empresa)
 
        escopos = [("Todas as regiões", "todas", srv_empresa)]
        for (nome, slug), srv_r in srv_empresa.groupby(["regiao", "regiao_slug"]):
            if slug in SLUGS_RESERVADOS:
                print(f"[aviso] slug de região '{slug}' é reservado; usando '{slug}-regiao'.")
                slug = f"{slug}-regiao"
            escopos.append((nome, slug, srv_r))
 
        indice = []
 
        for nome, slug, srv in escopos:
            atual_s = filtrar_escopo(atual_e, srv)
            hoje = resumir(atual_s)
            ontem = resumir(filtrar_escopo(ontem_e, srv))
 
            conteudo = {
                "regiao": {"slug": slug, "nome": nome},
                "gerado_em": gerado_em,
                "dados_defasados": dados_defasados,
                "kpis": {
                    "saude_percent": hoje["percentual"],
                    "servidores_totais": hoje["totais"],
                    "servidores_criticos": hoje["criticos"],
                    "servidores_atencao": hoje["atencao"],
                    "servidores_saudaveis": hoje["saudaveis"],
                    "servidores_offline": hoje["offline"],
                    "variacao_vs_ontem": calcular_variacao(hoje, ontem),
                },
                "grafico_horas": montar_grafico(filtrar_escopo(horario_e, srv), slots),
                "servidores": montar_servidores(atual_s, mapa),
            }
 
            gravar_json(f"{PREFIXO_GOLD}empresa={empresa_id}/{slug}.json", conteudo)
 
            indice.append({
                "slug": slug,
                "nome": nome,
                "servidores": int(len(srv)),
                "saude_percent": hoje["percentual"],
                "criticos": hoje["criticos"],
                "atencao": hoje["atencao"],
                "offline": hoje["offline"],
            })
 
        # O índice vai por último: nunca aponta para arquivos que ainda não existem.
        gravar_json(
            f"{PREFIXO_GOLD}empresa={empresa_id}/indice.json",
            {"gerado_em": gerado_em, "regioes": indice},
        )
 
    print("ETL Gold finalizado com sucesso!")
 
 
if __name__ == "__main__":
    try:
        main()
    except NoCredentialsError:
        print("Erro: credenciais AWS não encontradas. Verifique credenciais/IAM.")
    except ClientError as erro:
        print(f"Erro ao acessar o S3: {erro}")
    except pymysql.MySQLError as erro:
        print(f"Erro ao acessar o banco: {erro}")
    except (ValueError, FileNotFoundError) as erro:
        print(f"Erro no processamento: {erro}")
 
