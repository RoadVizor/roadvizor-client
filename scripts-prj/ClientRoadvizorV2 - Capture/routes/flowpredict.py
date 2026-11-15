from flask import Blueprint, jsonify, request
from services.flowpredict import buscar_dashboard

flowpredict_bp = Blueprint('flowpredict', __name__)

@flowpredict_bp.route('/api/flowpredict/<int:empresa_id>', methods=['GET'])
def consultar_flowpredict(empresa_id):
    try:
        token = request.headers.get("X-Internal-Token")

        if token != "roadvizor-internal-token":
            return jsonify({
                "erro": "Acesso não autorizado."
            }), 401

        dados = buscar_dashboard(empresa_id)

        return jsonify(dados), 200

    except Exception as erro:
        print(f"Erro ao consultar FlowPredict: {erro}")

        return jsonify({
            "erro": "Não foi possível consultar os dados do dashboard."
        }), 500