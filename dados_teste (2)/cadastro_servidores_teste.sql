-- Servidores SIMULADOS para testar a ETL Gold (gerado por gerar_csv_teste.py)
USE roadvizor;

-- Teste - Crescimento gradual: CPU sobe ~0,2 ponto/h; RAM estável
INSERT INTO servidor (empresa_id, endereco_id, endereco_mac, apelido, modelo)
VALUES (1, 1, 'AA0000000001', 'Teste - Crescimento gradual', 'SIMULADO')
ON DUPLICATE KEY UPDATE apelido = VALUES(apelido);

-- Teste - Estavel: CPU ~35% e RAM ~6 GiB, sem tendência
INSERT INTO servidor (empresa_id, endereco_id, endereco_mac, apelido, modelo)
VALUES (1, 1, 'AA0000000002', 'Teste - Estavel', 'SIMULADO')
ON DUPLICATE KEY UPDATE apelido = VALUES(apelido);

-- Teste - Oscilante: ciclo diário (pico ~14h), sem tendência de longo prazo
INSERT INTO servidor (empresa_id, endereco_id, endereco_mac, apelido, modelo)
VALUES (1, 1, 'AA0000000003', 'Teste - Oscilante', 'SIMULADO')
ON DUPLICATE KEY UPDATE apelido = VALUES(apelido);

-- Teste - Crescimento acentuado: CPU acelerando e RAM subindo rápido: condição crítica
INSERT INTO servidor (empresa_id, endereco_id, endereco_mac, apelido, modelo)
VALUES (1, 1, 'AA0000000004', 'Teste - Crescimento acentuado', 'SIMULADO')
ON DUPLICATE KEY UPDATE apelido = VALUES(apelido);

-- Limites: CPU em pct (limite/capacidade) e RAM em GiB (limite/capacidade)
INSERT INTO servidor_componente
    (servidor_id, empresa_id, componente_id, limite_min, limite_max, capacidade_total, monitorado)
SELECT s.id_servidor, s.empresa_id, c.id_componente, 0,
    CASE WHEN c.nome = 'cpu' THEN 70 ELSE 12 END,
    CASE WHEN c.nome = 'cpu' THEN 100 ELSE 16 END,
    1
FROM servidor s
CROSS JOIN componente c
WHERE s.endereco_mac IN ('AA0000000001', 'AA0000000002', 'AA0000000003', 'AA0000000004')
  AND s.empresa_id = 1
  AND c.nome IN ('cpu', 'ram')
ON DUPLICATE KEY UPDATE
    limite_min = VALUES(limite_min),
    limite_max = VALUES(limite_max),
    capacidade_total = VALUES(capacidade_total),
    monitorado = 1;

-- Para remover os servidores de teste depois (descomente):
-- DELETE FROM servidor_componente WHERE servidor_id IN
--     (SELECT id_servidor FROM servidor WHERE endereco_mac IN ('AA0000000001', 'AA0000000002', 'AA0000000003', 'AA0000000004'));
-- DELETE FROM servidor WHERE endereco_mac IN ('AA0000000001', 'AA0000000002', 'AA0000000003', 'AA0000000004');
