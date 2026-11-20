

# O Blueprint permite organizar as rotas em arquivos separados, 
# sem concentrar tudo no arquivo principal.
# Só a nível de arquitetura a mudança

from flask import Blueprint, jsonify, request
from services.monitoramento import listar_componentes_monitorados, listar_leituras_servidor

monitoramento_bp = Blueprint('monitoramento', __name__)


@monitoramento_bp.route('/api/monitoramento/<string:endereco_mac>', methods=['GET'])
def consultar_monitoramento(endereco_mac):
    componentes = listar_componentes_monitorados(endereco_mac)

    return jsonify({
        "endereco_mac": endereco_mac,
        "componentes": componentes
    })


@monitoramento_bp.route('/api/monitoramento/<string:endereco_mac>/leituras', methods=['GET'])
def consultar_leituras(endereco_mac):

    limite = request.args.get("limite", default=10, type=int)
    limite = max(1, min(limite, 100))

    leituras = listar_leituras_servidor(endereco_mac, limite)

    return jsonify({
        "endereco_mac": endereco_mac,
        "total": len(leituras),
        "leituras": leituras
    })
