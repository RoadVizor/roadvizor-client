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

CREATE TABLE componente (
    id_componente INT AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(60) NOT NULL UNIQUE,
    unidade_medida VARCHAR(20) NOT NULL,
    depende_de VARCHAR(120) NOT NULL,

    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT chk_componente_nome
        CHECK (
            nome IN ('cpu', 'ram', 'disco', 'placa_rede')
        ),

    CONSTRAINT chk_componente_unidade
        CHECK (
            (nome = 'cpu' AND unidade_medida = 'pct')
            OR
            (nome = 'ram' AND unidade_medida IN ('MiB', 'GiB', 'TiB'))
            OR
            (nome = 'disco' AND unidade_medida IN ('MiB', 'GiB', 'TiB'))
            OR
            (nome = 'placa_rede' AND unidade_medida IN ('Mbps', 'Gbps'))
        )
);

CREATE TABLE servidor_componente (
    servidor_id INT NOT NULL,
    empresa_id INT NOT NULL,
    componente_id INT NOT NULL,

    limite_max DECIMAL(12, 3) NOT NULL,
    limite_min DECIMAL(12, 3) NULL,
    capacidade_total DECIMAL(12, 3) NOT NULL,

    monitorado TINYINT NOT NULL DEFAULT 1,

    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT pk_servidor_componente
        PRIMARY KEY (servidor_id, empresa_id, componente_id),

    CONSTRAINT fk_servidor_componente_servidor
        FOREIGN KEY (servidor_id, empresa_id)
        REFERENCES servidor (id_servidor, empresa_id),

    CONSTRAINT fk_servidor_componente_componente
        FOREIGN KEY (componente_id)
        REFERENCES componente (id_componente),

    CONSTRAINT chk_servidor_componente_monitorado
        CHECK (monitorado IN (0, 1)),

    CONSTRAINT chk_servidor_componente_limites
        CHECK (
            limite_max >= 0
            AND capacidade_total >= 0
            AND (limite_min IS NULL OR limite_min >= 0)
            AND (limite_min IS NULL OR limite_min <= limite_max)
            AND limite_max <= capacidade_total
        )
);

 CREATE TABLE `log`(
	log_id INT NOT NULL AUTO_INCREMENT,
	servidor_id INT NOT NULL,
    titulo VARCHAR(255),
    descricao VARCHAR(255),
	`status` VARCHAR(45),
    
    PRIMARY KEY(log_id, servidor_id),
    CONSTRAINT fk_log_servidor FOREIGN KEY (servidor_id)
		REFERENCES servidor(id_servidor)
);

-- | Stored Procedure(s) | --

DELIMITER $$
CREATE PROCEDURE sp_cadastrar_empresa(
	IN in_cnpj CHAR(14),
    IN in_razao_social VARCHAR(120),
    IN in_nome_fantasia VARCHAR(120),
    IN in_email VARCHAR(255),
    IN in_senha VARCHAR(255),    
    IN in_domain_jira VARCHAR(255),
    IN in_email_jira VARCHAR(255),
    IN in_api_token_jira VARCHAR(255),
    In in_project_key VARCHAR(255)
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
		INSERT INTO empresa(cnpj, razao_social, nome_fantasia, email, domain_jira, email_jira, api_token_jira, project_key)
			VALUES (in_cnpj, in_razao_social, in_nome_fantasia, in_email, in_domain_jira, in_email_jira, in_api_token_jira, in_project_key);
			
		-- 2. Criar Usuário ADM da Empresa criada
		INSERT INTO usuario(empresa_id, nome, email, senha, nivel_acesso)
			VALUES (LAST_INSERT_ID(), in_nome_fantasia, in_email, SHA2(in_senha, 256), 1);
    COMMIT;

END$$
DELIMITER ;

-- | Inserts-padrão | --
INSERT INTO componente (nome, unidade_medida, depende_de)
VALUES
    ('cpu', 'pct', 'psutil.cpu_percent(interval=1)'),
    ('ram', 'GiB', 'psutil.virtual_memory()'),
    ('disco', 'GiB', 'psutil.disk_usage("/")'),
    ('placa_rede', 'Mbps', 'psutil.net_io_counters()');
    
CALL roadvizor.sp_cadastrar_empresa("0000000/200-1", "Roadvizor", "Roadvizor", "admin@adm.com", "Dado1807#", "b", "b", "b", "b");
UPDATE roadvizor.usuario SET nivel_acesso = 0, email = "adm@adm.com", senha = SHA2("Dado1807#", 256) WHERE id_usuario = 1;

-- [!] Rodar scripts abaixo APENAS com o root do BD.
-- CREATE USER 'roadvizor_user' IDENTIFIED BY 'sptech';
-- GRANT INSERT, SELECT, UPDATE, DELETE ON roadvizor.* TO 'roadvizor_user';
-- SHOW GRANTS FOR 'roadvizor_user';