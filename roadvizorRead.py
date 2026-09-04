import pandas as pd

cpuMaior = 0
ramMaior = 0
discMaior = 0

nomeCPU = ""
nomeRAM = ""
nomeDisc = ""

df = pd.read_csv("roadvizor.csv", sep=";")

df["timestamp"] = pd.to_datetime(
    df["timestamp"],
    format="%d/%m/%Y %H:%M:%S"
)

df["id_maquina"] = df["id_maquina"].astype("string")

for mac, maquina in df.groupby("id_maquina"):

    fim = maquina["timestamp"].max()

    ram = maquina[
        maquina["timestamp"] >= fim - pd.Timedelta(minutes=60)
    ]

    cpu = maquina[
        maquina["timestamp"] >= fim - pd.Timedelta(minutes=60)
    ]

    disco = maquina[
        maquina["timestamp"] >= fim - pd.Timedelta(minutes=30)
    ]

    ram_media = (
        ram["ram_uso"] / ram["ram_total"] * 100
    ).mean()

    cpu_max = cpu["cpu"].max()

    disco_media = (
        disco["disco_uso"] / disco["disco_total"] * 100
    ).mean()

    if cpu_max > cpuMaior:
        cpuMaior = cpu_max
        nomeCPU = mac

    if ram_media > ramMaior:
        ramMaior = ram_media
        nomeRAM = mac

    if disco_media > discMaior:
        discMaior = disco_media
        nomeDisc = mac

    print(f"""
        ===============================
            Máquina: {mac}
            CPU: {cpu_max:.2f}%
            RAM: {ram_media:.2f}%
            Disco: {disco_media:.2f}%
            Data: {fim}
        ===============================
    """)

print(f"""
    ==============================================
                Maiores Índices:                 
        Máquina com maior uso da CPU: {nomeCPU}    
        Máquina com maior uso da RAM: {nomeRAM}    
        Máquina com maior uso do disco: {nomeDisc} 
    ==============================================
""")