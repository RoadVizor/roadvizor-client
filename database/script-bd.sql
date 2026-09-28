CREATE DATABASE IF NOT EXISTS roadvizor;
USE roadvizor;
-- DROP DATABASE roadvizor;

-- OBS: muitas das decisões tomadas ao criar esta tabela foram motivadas com base na API viaCEP
-- disponível em: https://viacep.com.br.
-- e no entendimento de que ambos os endereços de CCOs e Pedágios poderão ser guardados nesta tabela.

CREATE TABLE IF NOT EXISTS endereco(
	id_endereco INT PRIMARY KEY AUTO_INCREMENT,
    
    -- campos úteis para o viaCEP
    cep CHAR(8) NOT NULL,
    logradouro VARCHAR(120) NULL,      	-- ex: CCO: "Rua XPTO" | Pórtico: "Rodovia Presidente Dutra"
    bairro VARCHAR(60) NULL,           	-- ex: CCO: "Bela Vista" | Pórtico: ""
    localidade VARCHAR(60) NOT NULL,   	-- o mesmo que 'cidade'
    uf CHAR(2) NOT NULL,				-- pode ser convertido em ENUM ou tabela depois
    
    -- campos úteis no contexto Empresa/CCO
    numero VARCHAR(20) NULL,
    complemento VARCHAR(60) NULL,
    
    -- campos úteis no contexto Pórtico/Servidor
    km VARCHAR(20) NULL,
    sentido VARCHAR(30) NULL 
);

INSERT INTO endereco (cep, logradouro, bairro, localidade, uf, numero, km, sentido
) VALUES (
    '01001000', 
    'Praça da Sé', 
    'Sé', 
    'São Paulo', 
    'SP', 
    '100', 
    'KM 15', 
    'Norte'
);

CREATE TABLE IF NOT EXISTS empresa(
	id_empresa INT PRIMARY KEY AUTO_INCREMENT,
    -- endereco_id INT NOT NULL,
    
    cnpj CHAR(14) NOT NULL UNIQUE,
    razao_social VARCHAR(120) NOT NULL,
    nome_fantasia VARCHAR(120) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,		-- campo usado para contato
    
    domain_jira VARCHAR(255) NOT NULL UNIQUE,
    email_jira VARCHAR(255) NOT NULL UNIQUE,
    api_token_jira VARCHAR(255) NOT NULL UNIQUE,
    project_key VARCHAR(255) NOT NULL UNIQUE,
    
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    
    -- CONSTRAINT fk_empresa_endereco FOREIGN KEY (endereco_id)
	-- 	REFERENCES endereco(id_endereco)
);

CREATE TABLE IF NOT EXISTS usuario(
	id_usuario INT PRIMARY KEY AUTO_INCREMENT,
    empresa_id INT NOT NULL,
    supervisor_id INT NULL,
    
    nome VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    senha VARCHAR(255) NOT NULL,
    nivel_acesso TINYINT NOT NULL DEFAULT 2,	-- assumindo: 2 - usuário comum | 1 - administrador | 0 - desenvolvedor
    
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
	CONSTRAINT fk_usuario_empresa FOREIGN KEY (empresa_id)
		REFERENCES empresa(id_empresa),
	CONSTRAINT fk_usuario_usuario FOREIGN KEY (supervisor_id)
		REFERENCES usuario(id_usuario)
);

CREATE TABLE IF NOT EXISTS servidor(
	id_servidor INT NOT NULL AUTO_INCREMENT,
    empresa_id INT NOT NULL,
    endereco_id INT NOT NULL,
    
    endereco_mac CHAR(12) NOT NULL UNIQUE,	-- ex: 11:22:33:44:55
    apelido VARCHAR(60) NOT NULL,			-- ex: hostname ou outro definido pelo usuario
    modelo VARCHAR(60) NOT NULL,			-- ex: 'PADR-S501-I980' 
        
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    CONSTRAINT pk_servidor PRIMARY KEY (id_servidor, empresa_id),
	CONSTRAINT fk_servidor_empresa FOREIGN KEY (empresa_id)
		REFERENCES empresa(id_empresa),
	CONSTRAINT fk_servidor_endereco FOREIGN KEY (endereco_id)
		REFERENCES endereco(id_endereco)
);

CREATE TABLE IF NOT EXISTS componente(
	id_componente INT PRIMARY KEY AUTO_INCREMENT,
    
    nome VARCHAR(60) NOT NULL,
    unidade_medida VARCHAR(20) NOT NULL,
    depende_de VARCHAR(60) NOT NULL,		-- informa a biblioteca e o método cuja captura depende; ex: <lib>.<metodo>(<argumentos...>)
    
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_componente_nome CHECK(nome IN('cpu', 'ram', 'disco', 'placa_rede')),
    CONSTRAINT chk_componente_unidade_medida CHECK(unidade_medida IN(
		'pct',					-- percentual (%) | CPU
        'MiB', 'GiB', 'TiB',	-- armazenamento  | RAM, Disco
        'bps', 'Mbps', 'Gbps'	-- velocidade de transferência | I/O de Rede
    ))
);

CREATE TABLE IF NOT EXISTS servidor_componente(
	servidor_id INT NOT NULL,
    empresa_id INT NOT NULL,
    componente_id INT NOT NULL,
    
    -- assumindo que 'limite_max', 'limite_min' e 'capacidade_total'
    -- serão medidos com a mesma unidade de medida definida no componente.
    limite_max DECIMAL(10, 3) NOT NULL,
    limite_min DECIMAL(10, 3) NULL,
    capacidade_total DECIMAL(10, 3) NOT NULL,
    monitorado TINYINT NOT NULL DEFAULT 1,	-- 0 deixou de ser monitorado | 1 está sendo monitorado
    
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    CONSTRAINT pk_servidor_componente PRIMARY KEY(servidor_id, empresa_id, componente_id),
    CONSTRAINT fk_servidor_componente_servidor FOREIGN KEY (servidor_id, empresa_id)
		REFERENCES servidor(id_servidor, empresa_id),
	CONSTRAINT fk_servidor_componente_componente FOREIGN KEY (componente_id)
		REFERENCES componente(id_componente)
);

-- | Stored Procedure(s) | --
DELIMITER $$
CREATE PROCEDURE sp_cadastrar_empresa(
	IN in_cnpj CHAR(14),
    IN in_razao_social VARCHAR(120),
    IN in_nome_fantasia VARCHAR(120),
    IN in_email VARCHAR(255),
    IN in_senha VARCHAR(255)
) BEGIN

	-- Tratamento de Erro na Procedure
	DECLARE EXIT HANDLER FOR SQLEXCEPTION
	BEGIN
		ROLLBACK;
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Erro no Cadastro de Empresa.';
	END;

	-- Corpo da Procedure
	START TRANSACTION;
		-- 1. Criar Empresa
		INSERT INTO empresa(cnpj, razao_social, nome_fantasia, email)
			VALUES (in_cnpj, in_razao_social, in_nome_fantasia, in_email);
			
		-- 2. Criar Usuário ADM da Empresa criada
		INSERT INTO usuario(empresa_id, nome, email, senha, nivel_acesso)
			VALUES (LAST_INSERT_ID(), in_nome_fantasia, in_email, SHA2(in_senha, 256), 1);
    COMMIT;

END$$
DELIMITER ;


-- | Inserts-padrão | --
INSERT INTO componente(nome, unidade_medida, depende_de)
	VALUES ('cpu', 'pct', 'psutil.cpu_percent()'),
    ('ram', 'MiB', 'psutil.virtual_memory()'),
    ('disco', 'GiB', 'psutil.disk_usage("/")'),
    ('placa_rede', 'Mbps', 'psutil.net_if_stats()');
    

-- [!] Rodar scripts abaixo APENAS com o root do BD.
-- CREATE USER 'roadvizor_user' IDENTIFIED BY 'sptech';
-- GRANT INSERT, SELECT, UPDATE, DELETE ON roadvizor.* TO 'roadvizor_user';
-- SHOW GRANTS FOR 'roadvizor_user';

-- 1. Cadastra a Empresa (necessária para vincular ao servidor)
INSERT INTO empresa (id_empresa, cnpj, razao_social, nome_fantasia, email, domain_jira, email_jira, api_token_jira, project_key)
VALUES (
    1, 
    '12345678000199', 
    'Concessionaria Rodovias SpTech S.A.', 
    'SpTech FreeFlow', 
    'operacoes@sptech.com.br',
    'https://sptech-team-j3dwrfgb.atlassian.net',
    'ancelmo.ssousa@sptech.school',
    'ATATT3xFfGF0zocbTvWujUOSXfuwBCucAKR41eaiWPdPcED9jPucuusg6WIdtkTYCSirYefyL6jEA4kz75sO_lIqCH6_-VB9rwhXdDZGFVXJGGpi89mi2ZSIDFUIUQWKaOS3QnwAeDTUjXS9GbOUKETT6iOlu1vy4G6JQDcPclBF0ipj84cf6kQ=6FC11B33',
    'MG0'
);


-- 2. Cadastra o Servidor com o seu Endereço MAC (6C946647CDD1)
-- Usa empresa_id = 1 e endereco_id = 1 (criado no seu script DDL)
INSERT INTO servidor (id_servidor, empresa_id, endereco_id, endereco_mac, apelido, modelo)
VALUES (
    1, 
    1, 
    1, 
    '6C946647CDD1', 
    'Servidor FreeFlow Edge Km 15', 
    'PADR-S501-I980'
);

-- 3. Configura os limites e o monitoramento para o seu Servidor
-- Respeita os nomes/unidades cadastrados na tabela 'componente' (pct, MiB, GiB, Mbps)
INSERT INTO servidor_componente (servidor_id, empresa_id, componente_id, limite_max, limite_min, capacidade_total, monitorado)
VALUES 
-- Componente 1: 'cpu' (unidade: 'pct') -> Alerta se passar de 80%
(1, 1, 1, 80.000, 0.000, 100.000, 1),

-- Componente 2: 'ram' (unidade: 'MiB') -> Capacidade 16384 MiB (16 GB), Alerta acima de 14336 MiB (14 GB)
(1, 1, 2, 14336.000, 0.000, 16384.000, 1),

-- Componente 3: 'disco' (unidade: 'GiB') -> Capacidade 256 GiB, Alerta acima de 200 GiB
(1, 1, 3, 200.000, 0.000, 256.000, 1),

-- Componente 4: 'placa_rede' (unidade: 'Mbps') -> Alerta acima de 100 Mbps
(1, 1, 4, 100.000, 0.000, 1000.000, 1);



SELECT 
    e.id_empresa,
    e.razao_social,
    e.nome_fantasia,
    s.id_servidor,
    s.apelido
FROM servidor s
JOIN empresa e ON s.empresa_id = e.id_empresa
WHERE s.endereco_mac = "6C946647CDD1";

SELECT 
    e.domain_jira,
    e.email_jira,
    e.api_token_jira,
    e.project_key
FROM servidor s
JOIN empresa e ON s.empresa_id = e.id_empresa
WHERE s.endereco_mac = "6C946647CDD1";

SELECT * FROM empresa;
