import mysql.connector

def conectar():

    conexao = mysql.connector.connect(
        host="localhost",
        port=3306,
        user="root",
        password="sptech",
        database="roadvizor"
    )

    return conexao