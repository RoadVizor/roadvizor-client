# RoadVizor — Client de Captura e Monitoramento

O RoadVizor é um sistema de monitoramento de infraestrutura para operações Free Flow. O projeto coleta métricas dos servidores, informações operacionais e condições climáticas relacionadas à localização de cada servidor.

Os dados são armazenados no MySQL, disponibilizados por uma API Flask e preparados em arquivos CSV para envio ao Data Lake na AWS.

## 1. Arquitetura do projeto

O funcionamento está dividido em seis etapas:

1. Coleta dos dados Free Flow.
2. Coleta das condições climáticas.
3. Armazenamento das informações no MySQL.
4. Consulta e integração dos dados pela API Flask.
5. Conversão das leituras em CSV pelo client.
6. Envio dos arquivos para o Amazon S3 (Bronze).

O processamento posterior utiliza as camadas Silver e Gold.

## 2. Tecnologias utilizadas

- Python
- Flask
- MySQL
- Docker
- psutil
- Open-Meteo API
- Requests
- Boto3
- AWS S3
- CSV

## 3. Organização dos componentes

### Simulador Free Flow

Arquivo: `services/simulador_free_flow.py`

Responsável por gerar leituras de monitoramento dos servidores.

As informações incluem:

- CPU;
- memória RAM;
- utilização de disco;
- tráfego de rede;
- operações de disco;
- processos;
- quantidade de veículos;
- transações;
- latência;
- perda de pacotes;
- falhas de comunicação.

O simulador identifica os servidores cadastrados no banco e gera leituras individualizadas.

As informações são armazenadas na tabela `log`, utilizando JSON no campo `descricao`.

### Monitoramento climático

Diretório: `monitoramento-climatico`

Responsável por consultar as condições climáticas utilizando a Open-Meteo API.

A consulta utiliza as coordenadas cadastradas na tabela `endereco`.

São armazenadas as seguintes informações:

- temperatura;
- umidade;
- velocidade do vento;
- direção do vento;
- precipitação;
- condição climática;
- fonte dos dados;
- horário da coleta.

As informações são armazenadas na tabela `monitoramento_climatico`, relacionadas ao endereço correspondente.

O coletor funciona independentemente do simulador Free Flow. A execução periódica automática por AWS Lambda e EventBridge ainda precisa ser configurada.

### Banco de dados MySQL

O banco utilizado é o `roadvizor`.

As principais tabelas envolvidas nesta integração são:

| Tabela | Responsabilidade |
|---|---|
| `servidor` | Cadastro dos servidores e seus endereços |
| `endereco` | Informações de localização e coordenadas |
| `log` | Histórico das leituras Free Flow |
| `monitoramento_climatico` | Histórico das leituras climáticas |

O relacionamento entre as informações ocorre por meio do `endereco_id`.

Dessa forma, cada servidor pode receber as informações climáticas correspondentes à sua localização.

### API Flask

Arquivos principais:

- `app.py`
- `routes/monitoramento.py`
- `services/monitoramento.py`

A API disponibiliza os dados armazenados no MySQL para o script de captura.

Existem dois endpoints principais:

**Consultar componentes monitorados**

`GET /api/monitoramento/<MAC>`

Retorna os componentes habilitados para monitoramento de determinado servidor.

**Consultar leituras**

`GET /api/monitoramento/<MAC>/leituras?limite=10`

Retorna as últimas leituras Free Flow e associa a leitura climática mais recente do endereço daquele servidor.

O retorno contém dois objetos principais:

- `leitura`: informações operacionais e métricas Free Flow.
- `clima`: informações climáticas relacionadas ao endereço.

Quando ainda não existe leitura climática para determinado endereço, o campo `clima` retorna `null`.

Importante: a leitura climática mais recente é associada às leituras Free Flow, não sendo necessário que os dois registros tenham o mesmo horário de coleta.

### Client de captura

Arquivo: `clientV02.py`

Responsável por consumir os dados disponibilizados pela API e preparar os registros para envio ao Data Lake.

Seu funcionamento ocorre da seguinte maneira:

1. Identifica o MAC da máquina.
2. Consulta a configuração do servidor na API.
3. Recupera as últimas dez leituras armazenadas.
4. Converte os dados JSON em colunas.
5. Incorpora as informações climáticas.
6. Gera um CSV em memória.
7. Envia o arquivo para o Amazon S3.

O arquivo utiliza ponto e vírgula (`;`) como separador, mantendo o padrão utilizado nas camadas Bronze, Silver e Gold.

## 4. Configuração do ambiente

### Pré-requisitos

Antes de executar o projeto, é necessário possuir:

- Python 3 instalado;
- Docker instalado;
- MySQL configurado;
- Banco `roadvizor` disponível;
- Credenciais AWS com permissão para escrita no bucket;
- Acesso à internet para consultar a Open-Meteo API.

### Criando o ambiente virtual

Na raiz do projeto, execute:

```bash
python3 -m venv venv
```

Ative o ambiente:

```bash
source venv/bin/activate
```

Instale as dependências utilizadas:

```bash
python3 -m pip install flask mysql-connector-python boto3 holidays psutil requests getmac
```

### Configuração do banco

Verifique se o MySQL está em execução e se o banco `roadvizor` está disponível.

As configurações de conexão utilizadas pelo projeto estão em:

`database/connection.py`

É necessário garantir que as credenciais e os parâmetros de conexão correspondam ao ambiente utilizado.

## 5. Como executar o projeto

A execução deve respeitar a ordem das etapas para garantir que os dados estejam disponíveis antes da captura.

### Etapa 1 — Iniciar o MySQL

Inicie o container responsável pelo banco de dados, caso esteja utilizando Docker.

Confirme que o banco `roadvizor` está acessível.

### Etapa 2 — Executar o coletor climático

Execute o coletor responsável pela consulta à Open-Meteo:

```bash
python3 monitoramento-climatico/lambda_function.py
```

O processo consulta os endereços cadastrados e armazena as informações climáticas no MySQL.

Nesta implementação, a execução é manual. O agendamento automático será configurado posteriormente.

### Etapa 3 — Executar o simulador Free Flow

Em outro terminal, execute:

```bash
python3 services/simulador_free_flow.py
```

O simulador identifica os servidores cadastrados e gera leituras continuamente.

As informações são registradas na tabela `log`.

### Etapa 4 — Iniciar a API Flask

Em outro terminal, execute:

```bash
python3 app.py
```

A API ficará disponível localmente em:

`http://127.0.0.1:5000`

Mantenha esse terminal aberto durante a captura.

### Etapa 5 — Testar a API

Consulte as leituras de um servidor utilizando seu MAC:

```bash
curl "http://127.0.0.1:5000/api/monitoramento/SEU_MAC/leituras?limite=2"
```

A resposta deverá apresentar os objetos `leitura` e `clima`.

### Etapa 6 — Configurar o acesso à AWS

Configure as credenciais AWS no ambiente em que o client será executado.

Com AWS CLI instalado, utilize:

```bash
aws configure
```

Informe as credenciais, região `us-east-1` e formato de saída desejado.

Caso utilize credenciais temporárias, configure também o `AWS_SESSION_TOKEN` correspondente.

Verifique a identidade autenticada:

```bash
aws sts get-caller-identity
```

O usuário ou perfil utilizado precisa ter permissão `s3:PutObject` no bucket de destino.

O bucket utilizado pelo client é:

`datalake-bronze-roadvizor-sptech`

### Etapa 7 — Executar a captura

Com a API ativa e as credenciais AWS configuradas, execute:

```bash
python3 clientV02.py
```

O client recuperará as últimas dez leituras disponíveis, converterá os dados para CSV e enviará o arquivo ao S3.

## 6. Organização dos dados no S3

Os arquivos são enviados para a camada Bronze, utilizando o seguinte prefixo:

`raw/`

O nome dos arquivos segue o padrão:

`roadvizor_AAAAMMDD_HHMMSS.csv`

Exemplo:

`raw/roadvizor_20261123_132548.csv`

O CSV contém informações de identificação do servidor, métricas de infraestrutura, informações operacionais, processos e condições climáticas.

O conteúdo é gerado em memória e enviado diretamente ao S3, sem necessidade de criar um arquivo CSV intermediário no computador.

## 7. Pipeline de dados

Após a captura, os dados seguem para as próximas etapas do Data Lake:

**Bronze**

Armazena os dados recebidos da captura em seu formato bruto, preservando as informações coletadas.

**Silver**

Responsável pela limpeza, padronização e organização dos dados.

**Gold**

Responsável por disponibilizar dados tratados e métricas consolidadas para análises e consumo pelo dashboard.

A integração completa das etapas posteriores depende da configuração e execução dos respectivos processos ETL.

## 8. Solução de problemas

**Erro de conexão com o MySQL**

Verifique se o container está ativo e se as configurações em `database/connection.py` estão corretas.

**Erro ao iniciar o Flask**

Confira se o ambiente virtual está ativado e se o Flask está instalado.

**Erro ao consultar a API**

Verifique se o Flask está em execução e se o MAC utilizado está cadastrado na tabela `servidor`.

**API retorna zero componentes monitorados**

Verifique os relacionamentos e os registros da tabela `servidor_componente`, principalmente o campo `monitorado`.

A ausência de componentes habilitados não significa necessariamente que não existam leituras históricas na tabela `log`.

**Erro InvalidToken no S3**

Verifique se as credenciais AWS estão válidas e se o token de sessão corresponde às credenciais temporárias utilizadas.

**Erro AccessDenied no S3**

Verifique se a identidade AWS possui permissão para gravar objetos no bucket configurado.

## 9. Fluxo resumido de execução

1. MySQL é iniciado.
2. Coletor climático consulta a Open-Meteo e salva os dados.
3. Simulador Free Flow gera e salva as leituras.
4. API Flask disponibiliza os registros do banco.
5. Client consulta a API pelo MAC do servidor.
6. Dados Free Flow e climáticos são convertidos para CSV.
7. Arquivo é enviado para o S3 Bronze.
8. Processos ETL Silver e Gold realizam o tratamento posterior.

## 10. Status da implementação

| Componente | Situação |
|---|---|
| Simulador Free Flow | Implementado e testado |
| Coletor climático | Implementado e testado |
| Persistência no MySQL | Testada |
| API Flask | Implementada e testada |
| Integração Free Flow + clima | Testada |
| Conversão para CSV | Testada |
| Autenticação AWS | Pendente de configuração |
| Envio ao S3 Bronze | Pendente de validação |
| Processamento Silver e Gold | Etapa posterior |
| Agendamento climático com Lambda/EventBridge | Pendente |

---

**RoadVizor — Monitoramento inteligente de infraestrutura Free Flow.**