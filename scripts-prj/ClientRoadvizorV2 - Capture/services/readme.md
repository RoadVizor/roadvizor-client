# 🚘 RoadVizor | Serviços Free Flow

<text>Monitoramento • Simulação • Integração • Data Lake</text>

---

## 📌 Sobre o projeto

O diretório `services` reúne os serviços responsáveis pela geração, armazenamento e disponibilização dos dados de monitoramento do RoadVizor.

A arquitetura utiliza o **MySQL como fonte central das leituras**, a API Flask como camada de comunicação e o Amazon S3 como destino dos dados processados pelo cliente.

O objetivo é manter cada componente com uma responsabilidade bem definida, evitando duplicidade na coleta e garantindo a continuidade do fluxo de dados.

## 🏗️ Arquitetura do projeto

```text
ClientRoadvizorV2 - Capture/
│
├── app.py
├── clientV02.py
│
├── database/
│   └── connection.py
│
├── routes/
│   └── monitoramento.py
│
└── services/
    ├── simulador_free_flow.py
    ├── monitoramento.py
    ├── venv/
    └── README.md
```

### Componentes e responsabilidades

| Arquivo | Responsabilidade |
|---|---|
| `simulador_free_flow.py` | Geração das leituras simuladas do Free Flow. |
| `monitoramento.py` | Consultas de componentes e leituras no MySQL. |
| `connection.py` | Conexão com o banco de dados. |
| `routes/monitoramento.py` | Disponibilização das rotas HTTP. |
| `app.py` | Inicialização da API Flask. |
| `clientV02.py` | Consumo da API, transformação CSV e envio ao S3. |

---

## 🔄 Como funciona o fluxo de dados?

O processo está dividido em cinco etapas:

### 01 — Simulação

**Arquivo:** `services/simulador_free_flow.py`

O simulador representa a operação de um pedágio Free Flow e gera leituras a cada minuto.

As informações contemplam:

- CPU, RAM e disco;
- Indicadores de rede e operações de I/O;
- Processos em execução;
- Quantidade de veículos e transações;
- Latência e perda de pacotes;
- Cenário operacional;
- Tipo de dia e feriados.

### 02 — Armazenamento

**Banco:** `roadvizor`  
**Tabela:** `log`

Cada leitura é armazenada em formato JSON no campo `descricao`.

| Campo | Finalidade |
|---|---|
| `log_id` | Identificação do registro. |
| `servidor_id` | Servidor associado à leitura. |
| `titulo` | Identificação e horário da leitura. |
| `descricao` | Dados completos em JSON. |
| `status` | Situação operacional. |

O endereço MAC permite relacionar as leituras ao servidor cadastrado.

### 03 — API Flask

A API disponibiliza os dados armazenados por meio de duas rotas.

**Consulta de componentes**

```http
GET /api/monitoramento/<endereco_mac>
```

Retorna a configuração dos componentes habilitados para monitoramento.

**Consulta de leituras**

```http
GET /api/monitoramento/<endereco_mac>/leituras?limite=10
```

Retorna as últimas leituras disponíveis para o servidor identificado.

O parâmetro `limite` define a quantidade máxima de registros retornados.

**Exemplo de resposta:**

```json
{
  "endereco_mac": "60FF9E5F0B3B",
  "total": 3,
  "leituras": [
    {
      "log_id": 1,
      "servidor_id": 1,
      "status": "NORMAL",
      "titulo": "Leitura Free Flow - 2026-11-20 14:35:49",
      "leitura": {
        "timestamp": "2026-11-20 14:35:49",
        "tipo_dia": "FERIADO"
      }
    }
  ]
}
```

A resposta completa também contém as métricas, informações operacionais e dados dos processos.

### 04 — Consumo e transformação

**Arquivo:** `clientV02.py`

O cliente realiza as seguintes operações:

1. Identifica o endereço MAC.
2. Consulta os componentes monitorados.
3. Solicita as últimas dez leituras à API.
4. Recebe os registros em JSON.
5. Transforma os dados em colunas CSV.
6. Prepara o arquivo para envio ao S3.

A função `achatar_valor()` transforma estruturas aninhadas em colunas.

| Informação original | Coluna gerada |
|---|---|
| CPU | `cpu` |
| RAM percentual | `ram_percent` |
| Disco percentual | `disco_percent` |
| Veículos | `operacao_veiculos` |
| Latência | `operacao_latencia_ms` |

Os processos são preservados em JSON na coluna `processos`.

Também são adicionados dados estáticos da máquina, como MAC, hostname, sistema operacional e arquitetura.

**Padrão do arquivo:** separador `;`.

### 05 — Envio ao Data Lake

**Destino:** Amazon S3 — Bronze

O CSV é montado em memória e enviado utilizando a biblioteca `boto3`.

**Bucket:**

```text
datalake-bronze-roadvizor-sptech
```

**Padrão de armazenamento:**

```text
raw/
└── roadvizor_YYYYMMDD_HHMMSS.csv
```

Após o envio, os dados ficam disponíveis para processamento pelo ETL Silver.

---

## 🧭 Visão geral da arquitetura

```text
┌──────────────────────────┐
│    SIMULADOR FREE FLOW   │
│  Geração a cada 1 minuto │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│       MYSQL / LOG        │
│   Armazenamento JSON     │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│        API FLASK         │
│ Consulta por endereço   │
│           MAC            │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│       CLIENTV02.PY       │
│   Consumo e conversão    │
│        JSON → CSV        │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│        S3 BRONZE         │
│      Dados brutos        │
└────────────┬─────────────┘
             │
             ▼
       ETL SILVER
             │
             ▼
        S3 SILVER
             │
             ▼
        ETL GOLD
             │
             ▼
         S3 GOLD
```

---

## ⚙️ Executando o ambiente

Todos os comandos abaixo devem ser executados a partir da raiz do projeto.

### 1. Ativar o ambiente virtual

```bash
source services/venv/bin/activate
```

### 2. Instalar dependências

**API e banco de dados:**

```bash
python3 -m pip install flask mysql-connector-python
```

**Cliente e integração:**

```bash
python3 -m pip install boto3 requests psutil holidays getmac
```

### 3. Iniciar a API

```bash
python3 app.py
```

<text color="secondary">Endereço local: http://127.0.0.1:5000</text>

Mantenha o terminal aberto.

### 4. Iniciar o simulador

Em outro terminal:

```bash
source services/venv/bin/activate
python3 services/simulador_free_flow.py
```

O simulador começará a gerar e armazenar as leituras no MySQL.

### 5. Executar o cliente

Em um terceiro terminal:

```bash
source services/venv/bin/activate
python3 clientV02.py
```

O cliente consultará os registros disponíveis, converterá os dados e realizará o envio ao S3.

---

## 🧪 Testando a API

**Verificar componentes monitorados:**

```bash
curl "http://127.0.0.1:5000/api/monitoramento/60FF9E5F0B3B"
```

**Verificar as últimas dez leituras:**

```bash
curl "http://127.0.0.1:5000/api/monitoramento/60FF9E5F0B3B/leituras?limite=10"
```

Substitua o MAC pelo endereço cadastrado para o servidor.

---

## 📦 Tecnologias utilizadas

| Tecnologia | Aplicação |
|---|---|
| Python | Desenvolvimento dos serviços. |
| Flask | API de monitoramento. |
| MySQL | Armazenamento das leituras. |
| psutil | Métricas e informações do sistema. |
| Requests | Comunicação HTTP com a API. |
| Boto3 | Integração com Amazon S3. |
| Holidays | Identificação de feriados brasileiros. |
| Getmac | Identificação do endereço MAC. |
| Amazon S3 | Armazenamento do Data Lake. |

---

## ⚠️ Observações importantes

- O MySQL é a fonte central das leituras.
- O cliente não realiza acesso direto ao banco de dados.
- A API mantém as duas rotas de monitoramento independentes.
- O simulador gera uma leitura por minuto.
- O cliente busca até dez registros por execução.
- A execução do cliente é independente da frequência do simulador.
- O envio ao S3 depende de credenciais AWS válidas e permissões de escrita no bucket.
- O CSV precisa permanecer compatível com o contrato de dados esperado pelo ETL Silver.

---

## 🎯 Resultado da atualização

A atualização separa as responsabilidades de geração, armazenamento, consulta e envio dos dados.

**O novo fluxo permite:**

- Centralizar as leituras no MySQL;
- Evitar uma segunda coleta de métricas pelo cliente;
- Disponibilizar os registros por meio da API;
- Transformar os dados em CSV padronizado;
- Integrar o monitoramento ao Data Lake do RoadVizor.

**RoadVizor — Monitoramento inteligente para operações Free Flow.**

