import os
import time
import subprocess
import csv
import threading
import sys
import shutil

# --- CONFIGURAÇÕES ---
BASE_DIR = "baseline_env"
OUTPUT_DIR = os.path.join(BASE_DIR, "metrics")
REKEY_INTERVAL = 20
DURATION_TCP = 180
DURATION_UDP = 180

# --- SIMULAÇÃO WAN ---
NET_DELAY = "20ms"
NET_JITTER = "3ms"
NET_LOSS  = "0.05%"

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content.strip())

def run_cmd(cmd, cwd=None, ignore_errors=False):
    try:
        # Check=True lança exceção se o comando falhar (exit code != 0)
        subprocess.run(cmd, shell=True, check=True, cwd=cwd, 
                       stderr=subprocess.PIPE if ignore_errors else None)
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            print(f"Erro no comando: {cmd}")
            if e.stderr: print(f"Stderr: {e.stderr.decode()}")
            raise e

# --- 1. PREPARAÇÃO DO AMBIENTE ---
print("=== CONFIGURANDO AMBIENTE BASELINE (SLEEP & EXEC) ===")

if os.path.exists(BASE_DIR):
    print(f"Limpar ambiente anterior em {BASE_DIR}...")
    run_cmd("docker compose down", cwd=BASE_DIR, ignore_errors=True)
    try:
        shutil.rmtree(BASE_DIR)
    except: pass

ensure_dir(BASE_DIR)
ensure_dir(os.path.join(BASE_DIR, "alice"))
ensure_dir(os.path.join(BASE_DIR, "bob"))
ensure_dir(OUTPUT_DIR)

# --- 2. CRIAÇÃO DOS ARQUIVOS DE CONFIGURAÇÃO ---

# DOCKER-COMPOSE.YML
# Usamos tail -f /dev/null para manter o container vivo eternamente
docker_compose_content = """
services:
  alice:
    image: strongx509/strongswan
    container_name: baseline_alice
    privileged: true
    stdin_open: true
    tty: true
    entrypoint: []
    command: tail -f /dev/null
    volumes:
      - ./alice/ipsec.conf:/etc/ipsec.conf
      - ./alice/ipsec.secrets:/etc/ipsec.secrets
    networks:
      transport_net:
        ipv4_address: 192.168.20.2

  bob:
    image: strongx509/strongswan
    container_name: baseline_bob
    privileged: true
    stdin_open: true
    tty: true
    entrypoint: []
    command: tail -f /dev/null
    volumes:
      - ./bob/ipsec.conf:/etc/ipsec.conf
      - ./bob/ipsec.secrets:/etc/ipsec.secrets
    networks:
      transport_net:
        ipv4_address: 192.168.20.3

networks:
  transport_net:
    driver: bridge
    ipam:
      config:
        - subnet: 192.168.20.0/24
"""

# IPSEC.CONF (ALICE)
alice_conf = f"""
config setup
    charondebug="ike 1, knl 1, cfg 0"

conn %default
    ikelifetime={REKEY_INTERVAL*2}s
    lifetime={REKEY_INTERVAL}s
    margintime=5s
    rekey=yes
    keyingtries=%forever
    keyexchange=ikev2
    authby=secret
    dpdaction=restart

conn baseline
    left=192.168.20.2
    leftid=alice
    right=192.168.20.3
    rightid=bob
    auto=start
    ike=aes256-sha256-modp3072!
    esp=aes256-sha256-modp3072!
"""

# IPSEC.CONF (BOB)
bob_conf = f"""
config setup
    charondebug="ike 1, knl 1, cfg 0"

conn %default
    ikelifetime={REKEY_INTERVAL*2}s
    lifetime={REKEY_INTERVAL}s
    margintime=5s
    rekey=yes
    keyingtries=%forever
    keyexchange=ikev2
    authby=secret
    dpdaction=restart

conn baseline
    left=192.168.20.3
    leftid=bob
    right=192.168.20.2
    rightid=alice
    auto=add
    ike=aes256-sha256-modp3072!
    esp=aes256-sha256-modp3072!
"""

secrets = ': PSK "baseline_secret_123"'

write_file(os.path.join(BASE_DIR, "docker-compose.yml"), docker_compose_content)
write_file(os.path.join(BASE_DIR, "alice/ipsec.conf"), alice_conf)
write_file(os.path.join(BASE_DIR, "alice/ipsec.secrets"), secrets)
write_file(os.path.join(BASE_DIR, "bob/ipsec.conf"), bob_conf)
write_file(os.path.join(BASE_DIR, "bob/ipsec.secrets"), secrets)

print("[OK] Arquivos gerados em ./baseline_env")

# --- 3. FUNÇÕES AUXILIARES ---

def start_strongswan(container):
    print(f"[{container}] Iniciando StrongSwan manual...")
    # Tenta caminho absoluto padrão
    try:
        run_cmd(f"docker exec {container} /usr/sbin/ipsec start")
        print(f"[{container}] IPsec iniciado (via /usr/sbin/ipsec)")
        return
    except:
        print(f"[{container}] Falha com caminho absoluto. Tentando PATH...")
    
    # Tenta PATH
    try:
        run_cmd(f"docker exec {container} ipsec start")
        print(f"[{container}] IPsec iniciado (via PATH)")
        return
    except:
        print(f"[{container}] ERRO CRÍTICO: Não foi possível iniciar ipsec.")
        # Debug: lista arquivos para ajudar
        run_cmd(f"docker exec {container} ls -l /usr/sbin/", ignore_errors=True)
        raise Exception("IPsec binary not found")

def apply_wan(container):
    print(f"[{container}] Aplicando WAN ({NET_DELAY}, {NET_LOSS})...")
    # Instala iproute2 se necessário (algumas imagens alpine precisam)
    # run_cmd(f"docker exec {container} apk add iproute2", ignore_errors=True) 
    run_cmd(f"docker exec {container} tc qdisc add dev eth0 root netem delay {NET_DELAY} {NET_JITTER} distribution normal loss {NET_LOSS}", ignore_errors=True)

def monitor_resources(stop_event, filepath):
    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['timestamp', 'container', 'cpu_perc', 'mem_mib'])
        while not stop_event.is_set():
            try:
                out = subprocess.check_output("docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}'", shell=True).decode()
                ts = time.time()
                for line in out.strip().split('\n'):
                    parts = line.split(',')
                    if len(parts) < 3: continue
                    if 'baseline' in parts[0]:
                        c_name = 'alice' if 'alice' in parts[0] else 'bob'
                        cpu = parts[1].replace('%','')
                        mem = parts[2].split('/')[0].strip().replace('MiB','').replace('GiB','')
                        writer.writerow([ts, c_name, cpu, mem])
            except: pass
            time.sleep(1)

# --- 4. EXECUÇÃO PRINCIPAL ---

try:
    print("\n=== SUBINDO CONTAINERS (BACKGROUND) ===")
    run_cmd("docker compose up -d", cwd=BASE_DIR)
    
    print("Aguardando containers ficarem online (5s)...")
    time.sleep(5)

    # Inicia o serviço manualmente
    start_strongswan("baseline_bob")  # Bob primeiro (passivo)
    time.sleep(2)
    start_strongswan("baseline_alice") # Alice conecta
    
    print("Aguardando negociação IKE (10s)...")
    time.sleep(10)

    # Verifica Túnel
    print("Verificando túnel...")
    tunnel_up = False
    for i in range(10):
        try:
            # Ping Alice -> Bob
            run_cmd("docker exec baseline_alice ping -c 1 -W 1 192.168.20.3")
            print("[OK] Túnel estabelecido!")
            tunnel_up = True
            break
        except:
            print(f"Tentativa {i+1} falhou...")
            time.sleep(2)
            
            if i == 5:
                print("   -> Forçando 'ipsec up baseline'...")
                try:
                    run_cmd("docker exec baseline_alice /usr/sbin/ipsec up baseline")
                except:
                    run_cmd("docker exec baseline_alice ipsec up baseline", ignore_errors=True)
    
    if not tunnel_up:
        print("[FATAL] Túnel não subiu. Debug Logs:")
        run_cmd("docker exec baseline_alice cat /var/log/charon.log", ignore_errors=True) # Se existir log
        run_cmd("docker exec baseline_alice ipsec status", ignore_errors=True)
        sys.exit(1)

    # Aplica Rede
    apply_wan("baseline_alice")
    apply_wan("baseline_bob")

    # Inicia Monitoramento
    stop_mon = threading.Event()
    mon_thread = threading.Thread(target=monitor_resources, args=(stop_mon, os.path.join(OUTPUT_DIR, "resource_metrics.csv")))
    mon_thread.start()

    # Testes
    print(f"\n=== EXECUTANDO TESTES ({DURATION_TCP}s TCP + {DURATION_UDP}s UDP) ===")
    
    # Server TCP/UDP no Bob
    run_cmd("docker exec -d baseline_bob iperf3 -s")
    time.sleep(3)

    # 1. TCP
    print("-> Teste TCP...")
    try:
        res_tcp = subprocess.check_output(f"docker exec baseline_alice iperf3 -c 192.168.20.3 -t {DURATION_TCP} -J", shell=True).decode()
        with open(os.path.join(OUTPUT_DIR, "throughput_combined.json"), 'w') as f:
            f.write(f"[{res_tcp}]")
    except Exception as e:
        print(f"[ERRO TCP] {e}")

    # 2. UDP
    print("-> Teste UDP (Vídeo)...")
    run_cmd("docker exec baseline_bob pkill iperf3", ignore_errors=True)
    run_cmd("docker exec -d baseline_bob iperf3 -s")
    time.sleep(2)
    
    try:
        res_udp = subprocess.check_output(f"docker exec baseline_alice iperf3 -c 192.168.20.3 -u -b 5M -t {DURATION_UDP} --json", shell=True).decode()
        with open(os.path.join(OUTPUT_DIR, "video_combined_with_jitter.json"), 'w') as f:
            f.write(f"[{res_udp}]")
    except Exception as e:
        print(f"[ERRO UDP] {e}")

    print("\n=== FIM DO TESTE ===")
    stop_mon.set()
    mon_thread.join()
    
    print(f"Dados salvos em: {os.path.abspath(OUTPUT_DIR)}")
    print("Para limpar depois: cd baseline_env && docker compose down")

except Exception as e:
    print(f"\n[ERRO GERAL] {e}")
    try:
        stop_mon.set()
    except: pass