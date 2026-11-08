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