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
def listar_leituras_servidor(endereco_mac, limite=10):

    conexao = conectar()
    cursor = conexao.cursor(dictionary=True)

    sql = """
        SELECT
            l.log_id,
            l.servidor_id,
            l.titulo,
            l.descricao,
            l.status
        FROM log l
        JOIN servidor s ON s.id_servidor = l.servidor_id
        WHERE s.endereco_mac = %s
        ORDER BY l.log_id DESC
        LIMIT %s
    """

    try:
        cursor.execute(sql, (endereco_mac, limite))
        registros = cursor.fetchall()

        leituras = []

        for registro in registros:

            descricao = registro["descricao"]

            if isinstance(descricao, str):
                descricao = json.loads(descricao)

            leituras.append({
                "log_id": registro["log_id"],
                "servidor_id": registro["servidor_id"],
                "titulo": registro["titulo"],
                "status": registro["status"],
                "leitura": descricao
            })

        leituras.reverse()

        return leituras

    finally:
        cursor.close()
        conexao.close()
