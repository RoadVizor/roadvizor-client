import json
from database.connection import conectar

def listar_componentes_monitorados(endereco_mac):
    conexao = conectar()
    cursor = conexao.cursor(dictionary=True)

    sql = """
        SELECT
            s.id_servidor,
            s.endereco_mac,
            c.id_componente,
            c.nome,
            c.depende_de,
            sc.limite_max,
            sc.limite_min
        FROM servidor s
        JOIN servidor_componente sc
            ON s.id_servidor = sc.servidor_id
        JOIN componente c
            ON c.id_componente = sc.componente_id
        WHERE sc.monitorado = 1
        AND s.endereco_mac = %s
    """

    cursor.execute(sql, (endereco_mac,))
    componentes = cursor.fetchall()

    cursor.close()
    conexao.close()

    return componentes


def listar_leituras_servidor(endereco_mac, limite=10, ultimo_log_id=None):

    conexao = conectar()
    cursor = conexao.cursor(dictionary=True)

    filtro_log = ""
    parametros = [endereco_mac]
    ordem_sql = "ASC" if ultimo_log_id is not None else "DESC"

    if ultimo_log_id is not None:
        filtro_log = " AND l.log_id > %s"
        parametros.append(ultimo_log_id)

    sql = f"""
        SELECT
            l.log_id,
            l.servidor_id,
            l.titulo,
            l.descricao,
            l.status,

            mc.id_clima,
            mc.temperatura,
            mc.umidade,
            mc.velocidade_vento,
            mc.direcao_vento,
            mc.precipitacao,
            mc.condicao_climatica,
            mc.fonte_dados,
            mc.coletado_em

        FROM log l

        JOIN servidor s
            ON s.id_servidor = l.servidor_id

        LEFT JOIN monitoramento_climatico mc
            ON mc.id_clima = (
                SELECT mc2.id_clima
                FROM monitoramento_climatico mc2
                WHERE mc2.endereco_id = s.endereco_id
                ORDER BY mc2.coletado_em DESC, mc2.id_clima DESC
                LIMIT 1
            )

        WHERE s.endereco_mac = %s
        {filtro_log}

        ORDER BY l.log_id {ordem_sql}
        LIMIT %s
    """

    parametros.append(limite)

    try:
        cursor.execute(sql, tuple(parametros))
        registros = cursor.fetchall()

        leituras = []

        for registro in registros:

            descricao = registro["descricao"]

            if isinstance(descricao, str):
                descricao = json.loads(descricao)

            clima = None

            if registro["id_clima"] is not None:
                clima = {
                    "temperatura": float(registro["temperatura"]) if registro["temperatura"] is not None else None,
                    "umidade": float(registro["umidade"]) if registro["umidade"] is not None else None,
                    "velocidade_vento": float(registro["velocidade_vento"]) if registro["velocidade_vento"] is not None else None,
                    "direcao_vento": registro["direcao_vento"],
                    "precipitacao": float(registro["precipitacao"]) if registro["precipitacao"] is not None else None,
                    "condicao_climatica": registro["condicao_climatica"],
                    "fonte_dados": registro["fonte_dados"],
                    "coletado_em": registro["coletado_em"].strftime("%Y-%m-%d %H:%M:%S") if registro["coletado_em"] else None
                }

            leituras.append({
                "log_id": registro["log_id"],
                "servidor_id": registro["servidor_id"],
                "titulo": registro["titulo"],
                "status": registro["status"],
                "leitura": descricao,
                "clima": clima
            })

        if ultimo_log_id is None:
            leituras.reverse()

        return leituras

    finally:
        cursor.close()
        conexao.close()

