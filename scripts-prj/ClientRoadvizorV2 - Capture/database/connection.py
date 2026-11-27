import mysql.connector

def conectar():

    conexao = mysql.connector.connect(
        host="localhost",
        port=3306,
        user="aluno",
        password="sptech",
        database="roadvizor"
    )

    return conexao