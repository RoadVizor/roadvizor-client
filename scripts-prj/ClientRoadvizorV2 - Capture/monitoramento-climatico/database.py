import mysql.connector


# Faz a conexão com o banco de dados
def conectar():

    conexao = mysql.connector.connect(
        host="localhost",
        port=3306,
        user="root",
        password="sptech",
        database="roadvizor"
    )

    return conexao


# Busca os endereços que possuem coordenadas cadastradas
def buscar_enderecos():

    conexao = conectar()
    cursor = conexao.cursor(dictionary=True)

    cursor.execute("""
        SELECT id_endereco, cep, localidade, uf, latitude, longitude
        FROM endereco
        WHERE latitude IS NOT NULL
        AND longitude IS NOT NULL
    """)

    enderecos = cursor.fetchall()

    cursor.close()
    conexao.close()

    return enderecos
# Salva uma leitura climática no banco de dados
def salvar_clima(endereco_id, clima):

    conexao = conectar()
    cursor = conexao.cursor()

    comando = """
        INSERT INTO monitoramento_climatico (
            endereco_id,
            temperatura,
            umidade,
            velocidade_vento,
            direcao_vento,
            precipitacao,
            condicao_climatica,
            fonte_dados,
            coletado_em
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    valores = (
        endereco_id,
        clima["temperatura"],
        clima["umidade"],
        clima["vento"],
        str(clima["direcao_vento"]),
        clima["precipitacao"],
        clima["condicao"],
        "Open-Meteo",
        clima["timestamp"]
    )

    cursor.execute(comando, valores)

    conexao.commit()

    cursor.close()
    conexao.close()

    print("Leitura climática salva com sucesso!")
