from flask import Flask
from routes.monitoramento import monitoramento_bp

# Instância a aplicação
app = Flask(__name__)

app.register_blueprint(monitoramento_bp)



# Exeuta a instância
if __name__ == '__main__':
    app.run(debug=True, port=5000)