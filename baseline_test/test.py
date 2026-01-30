import os
import subprocess
import time
import sys

# --- CONFIGURAÇÕES ---
BASE_DIR = "." 
CONTAINERS = ["baseline_alice", "baseline_bob"]

# Simulação WAN
NET_DELAY = "20ms"
NET_JITTER = "3ms"
NET_LOSS  = "0.05%"

def run_cmd(cmd, ignore_errors=False):
    try:
        subprocess.run(cmd, shell=True, check=True, cwd=BASE_DIR, 
                       stderr=subprocess.PIPE if ignore_errors else None)
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            print(f"[ERRO] Falha no comando: {cmd}")
            if e.stderr: print(f"Detalhes: {e.stderr.decode()}")
            sys.exit(1)

def apply_wan_simulation(container):
    print(f"[{container}] Aplicando WAN ({NET_DELAY}, {NET_LOSS})...")
    run_cmd(f"docker exec {container} tc qdisc del dev eth0 root", ignore_errors=True)
    cmd = f"docker exec {container} tc qdisc add dev eth0 root netem delay {NET_DELAY} {NET_JITTER} distribution normal loss {NET_LOSS}"
    run_cmd(cmd)

def find_charon_binary(container):
    """Procura onde o binário charon está escondido na imagem"""
    possible_paths = [
        "/usr/libexec/ipsec/charon", # Padrão moderno (Alpine/StrongSwan 6)
        "/usr/lib/ipsec/charon",     # Padrão antigo (Debian/Ubuntu)
        "/usr/sbin/charon",          # Alternativo
        "charon"                     # Tenta via PATH
    ]
    
    for path in possible_paths:
        try:
            # Usa 'ls' para verificar se o arquivo existe (exceto se for só 'charon')
            if path.startswith("/"):
                check_cmd = f"docker exec {container} ls {path}"
                subprocess.check_call(check_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return path
        except:
            continue
    
    return None

def start_swanctl_service(container):
    print(f"[{container}] Iniciando StrongSwan (Charon)...")
    
    # 1. Encontra o binário
    charon_path = find_charon_binary(container)
    if not charon_path:
        print(f"[{container}] ERRO CRÍTICO: Não encontrei o binário 'charon' na imagem.")
        sys.exit(1)
        
    print(f"[{container}] Usando binário: {charon_path}")

    # 2. Inicia o daemon em background
    # O '&' é importante aqui para desprender o processo
    if charon_path == "charon":
        cmd = f"docker exec -d {container} sh -c 'charon &'"
    else:
        cmd = f"docker exec -d {container} {charon_path}"
        
    subprocess.run(cmd, shell=True)
    
    # 3. Espera o daemon criar o socket VICI
    print(f"[{container}] Aguardando daemon subir...")
    for i in range(10):
        try:
            # Tenta listar configurações para ver se o socket responde
            subprocess.check_call(f"docker exec {container} swanctl --stats", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break
        except:
            time.sleep(1)
    else:
        print(f"[{container}] AVISO: Daemon demorou para responder, tentando carregar mesmo assim...")

    # 4. Carrega as configurações
    print(f"[{container}] Carregando configurações swanctl...")
    run_cmd(f"docker exec {container} swanctl --load-all")

def main():
    if not os.path.exists(os.path.join(BASE_DIR, "docker-compose.yml")):
        print(f"[ERRO] Não encontrei 'docker-compose.yml' na pasta atual.")
        sys.exit(1)

    print("=== 1. SUBINDO CONTAINERS DOCKER ===")
    run_cmd("docker compose up -d")
    
    print("   -> Aguardando containers estabilizarem (3s)...")
    time.sleep(3)

    print("\n=== 2. INICIANDO SERVIÇOS VPN (SWANCTL) ===")
    for c in CONTAINERS:
        start_swanctl_service(c)
        apply_wan_simulation(c)

    print("\n=== 3. ESTABELECENDO CONEXÃO ===")
    print("   -> Alice iniciando conexão 'baseline'...")
    try:
        run_cmd("docker exec baseline_alice swanctl --initiate --child net-net")
    except:
        print("   [INFO] Talvez já esteja conectado ou em negociação.")
    
    time.sleep(3)

    print("\n=== 4. VERIFICAÇÃO FINAL ===")
    print("   -> Testando Ping (Alice -> Bob)...")
    try:
        run_cmd("docker exec baseline_alice ping -c 3 192.168.20.3")
        print("\n✅ [SUCESSO] O Baseline Environment está pronto!")
        print("   Agora você pode rodar os testes de iperf3.")
    except:
        print("\n❌ [FALHA] O Ping falhou.")
        print("   Verifique se o charon está rodando: docker exec baseline_alice ps aux")
        print("   Verifique logs: docker logs baseline_alice")

if __name__ == "__main__":
    main()