from flask import Blueprint, jsonify
from services.monitoramento import listar_componentes_monitorados

# O Blueprint permite organizar as rotas em arquivos separados, 
# sem concentrar tudo no arquivo principal.
# Só a nível de arquitetura a mudança

monitoramento_bp = Blueprint('monitoramento', __name__)

@monitoramento_bp.route('/api/monitoramento/<string:endereco_mac>', methods=['GET'])
def consultar_monitoramento(endereco_mac):

    componentes = listar_componentes_monitorados(endereco_mac)

    return jsonify({
        "endereco_mac": endereco_mac,
        "componentes": componentes
    })