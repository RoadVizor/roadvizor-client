import random
import time
import uuid
import os
import sys

# Permite acessar os módulos da pasta principal do projeto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import conectar


# Intervalo entre a criação de cada servidor
INTERVALO_SEGUNDOS = 300


# Lista de locais que podem ser sorteados
LOCAIS = [
    {
        "cep": "01001000",
        "logradouro": "Praca da Se",
        "bairro": "Se",
        "cidade": "Sao Paulo",
        "uf": "SP",
        "latitude": -23.5505,
        "longitude": -46.6333
    },
    {
        "cep": "13010000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Campinas",
        "uf": "SP",
        "latitude": -22.9056,
        "longitude": -47.0608
    },
    {
        "cep": "11010000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Santos",
        "uf": "SP",
        "latitude": -23.9608,
        "longitude": -46.3336
    },
    {
        "cep": "07010000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Guarulhos",
        "uf": "SP",
        "latitude": -23.4543,
        "longitude": -46.5337
    },
    {
        "cep": "18010000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Sorocaba",
        "uf": "SP",
        "latitude": -23.5015,
        "longitude": -47.4526
    },
    {
        "cep": "12210000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Sao Jose dos Campos",
        "uf": "SP",
        "latitude": -23.1896,
        "longitude": -45.8841
    },
    {
        "cep": "14010000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Ribeirao Preto",
        "uf": "SP",
        "latitude": -21.1775,
        "longitude": -47.8103
    },
    {
        "cep": "17010000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Bauru",
        "uf": "SP",
        "latitude": -22.3145,
        "longitude": -49.0587
    },
    {
        "cep": "09710000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Sao Bernardo do Campo",
        "uf": "SP",
        "latitude": -23.6914,
        "longitude": -46.5646
    },
    {
        "cep": "13201000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Jundiai",
        "uf": "SP",
        "latitude": -23.1857,
        "longitude": -46.8978
    },
    {
        "cep": "13400000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Piracicaba",
        "uf": "SP",
        "latitude": -22.7253,
        "longitude": -47.6492
    },
    {
        "cep": "12010000",
        "logradouro": "Centro",
        "bairro": "Centro",
        "cidade": "Taubate",
        "uf": "SP",
        "latitude": -23.0264,
        "longitude": -45.5553
    }
]


# Procura o endereço ou cadastra um novo
def buscar_ou_criar_endereco(cursor, local):

    cursor.execute("""
        SELECT id_endereco
        FROM endereco
        WHERE localidade = %s
        AND uf = %s
        AND latitude = %s
        AND longitude = %s
        LIMIT 1
    """, (
        local["cidade"],
        local["uf"],
        local["latitude"],
        local["longitude"]
    ))

    endereco = cursor.fetchone()

    if endereco:
        return endereco[0]

    cursor.execute("""
        INSERT INTO endereco (
            cep,
            logradouro,
            bairro,
            localidade,
            uf,
            numero,
            latitude,
            longitude
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        local["cep"],
        local["logradouro"],
        local["bairro"],
        local["cidade"],
        local["uf"],
        "S/N",
        local["latitude"],
        local["longitude"]
    ))

    return cursor.lastrowid


# Cria um servidor com empresa e localização sorteadas
def criar_servidor():

    conexao = conectar()
    cursor = conexao.cursor()

    try:

        # Confere se as duas empresas existem
        cursor.execute("""
            SELECT id_empresa
            FROM empresa
            WHERE id_empresa IN (1, 2)
        """)

        empresas = [registro[0] for registro in cursor.fetchall()]

        if len(empresas) < 2:
            raise Exception("Roadvizor e EcoRodovias precisam estar cadastradas.")

        # Sorteia a empresa e o local
        empresa_id = random.choice(empresas)
        local = random.choice(LOCAIS)

        # Procura o endereço ou cria um novo
        endereco_id = buscar_ou_criar_endereco(cursor, local)

        # Gera um MAC exclusivo
        endereco_mac = uuid.uuid4().hex[:12].upper()

        # Cria um nome para o servidor
        codigo = random.randint(1000, 9999)

        apelido = (
            f"Servidor Free Flow {local['cidade']} {codigo}"
        )

        # Cadastra o servidor
        cursor.execute("""
            INSERT INTO servidor (
                empresa_id,
                endereco_id,
                endereco_mac,
                apelido,
                modelo
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            empresa_id,
            endereco_id,
            endereco_mac,
            apelido,
            "Simulador Free Flow"
        ))

        servidor_id = cursor.lastrowid

        conexao.commit()

        nome_empresa = (
            "Roadvizor" if empresa_id == 1 else "EcoRodovias"
        )

        print("\nNOVO SERVIDOR CADASTRADO")
        print("ID:", servidor_id)
        print("Nome:", apelido)
        print("Empresa:", nome_empresa)
        print("Cidade:", local["cidade"])
        print("Endereço ID:", endereco_id)
        print("MAC:", endereco_mac)
        print("Latitude:", local["latitude"])
        print("Longitude:", local["longitude"])

    except Exception:
        conexao.rollback()
        raise

    finally:
        cursor.close()
        conexao.close()


# Executa continuamente
if __name__ == "__main__":

    print("GERADOR DE SERVIDORES ROADVIZOR")
    print("Criação automática a cada 5 minutos.")
    print("Pressione Ctrl + C para encerrar.\n")

    try:

        while True:

            try:
                criar_servidor()

            except Exception as erro:
                print("Erro ao criar servidor:", erro)

            time.sleep(INTERVALO_SEGUNDOS)

    except KeyboardInterrupt:
        print("\nGerador encerrado pelo usuário.")
