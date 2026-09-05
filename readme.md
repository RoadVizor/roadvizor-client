# ROADVIZOR 
Roadvizor-Client é a aplicação realizada em Python para a leitura dos componentes de hardware no sistema instalado.

Roadvizor.py - Realiza as leituras da máquina e cria um arquivo CSV
RoadvizorRead.py - Realiza a leitura do arquivo CSV posteriormente criado após execução do Roadvizor.py 

# AVISO:
Antes de inciar a aplicação se certifique em estar utilizando um ambiente virtual (.venv) do python. Você pode ativar pelo terminal ou utilizando a Extensão Python (que irá fazer o trabalho automáticamente)

- Comando para criar o ambiente virtual:
```bash
    python -m venv <nome-ambiente-virtual>
```

- Comando para ativar o ambiente virtual - Windows:
```bash
    .\<nome-ambiente-virtual>\Scripts\activate
```

- Comando para ativar o ambiente virtual - Linux:
```bash
    source <nome-ambiente-virtual>/bin/activate
```

# Requisitos:
- Python 3.14
- VS Code

A aplicação necessita de algumas bibliotecas python instaladas, você pode realizar a instalação executando o ccomando a seguir:
```bash
    pip install -r requirements.txt
```