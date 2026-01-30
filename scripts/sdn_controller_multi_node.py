#!/usr/bin/env python3
import requests
import time
import os
import binascii
import logging
import hmac
import hashlib
import json
import base64
import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Tenta importar liboqs de forma segura
try:
    import oqs
    HAS_OQS = True
except ImportError:
    HAS_OQS = False
    print("ERRO CRITICO: liboqs nao encontrado. A autenticacao PQC vai falhar.")

# Certifique-se de que estes arquivos existem no diretório /scripts
from hybrid_key_gen import mix_keys, hkdf_extract, hkdf_expand
from qukaydee_client import QuKayDeeClient

# --- CONFIGURAÇÕES ---
AGENT_ALICE_URL = "https://192.168.100.10:5000"
AGENT_BOB_URL   = "https://192.168.100.11:5000"
AGENT_CAROL_URL = "https://192.168.100.12:5000"
AGENT_DAVE_URL  = "https://192.168.100.13:5000"

AUTH_ALGO = "ML-DSA-65"
PRIV_KEY_PATH = "/scripts/orchestrator_auth.key"
HTTP_TIMEOUT = 10

PQC_ALGO = "ML-KEM-768"  # Algoritmo PQ para chave híbrida
KEM_ALGO = "ML-KEM-768"  # Algoritmo para criptografia de envelope

MAX_MESSAGE_AGE_SECONDS = 30

ACCOUNT_ID = "2992"
URL_KME_ALICE = f"https://kme-1.acct-{ACCOUNT_ID}.etsi-qkd-api.qukaydee.com"
URL_KME_BOB   = f"https://kme-2.acct-{ACCOUNT_ID}.etsi-qkd-api.qukaydee.com"
URL_KME_CAROL = f"https://kme-3.acct-{ACCOUNT_ID}.etsi-qkd-api.qukaydee.com"
URL_KME_DAVE  = f"https://kme-4.acct-{ACCOUNT_ID}.etsi-qkd-api.qukaydee.com"

CERT_DIR = "/scripts/certs"
CA_CERT  = f"{CERT_DIR}/account-{ACCOUNT_ID}-server-ca-qukaydee-com.crt"
_agent_ca_env = os.environ.get("AGENT_CA_BUNDLE", "false").strip().lower()
if _agent_ca_env in ("false", "0", "no", "none", ""):
    AGENT_CA_BUNDLE = False
else:
    AGENT_CA_BUNDLE = os.environ.get("AGENT_CA_BUNDLE")

CONNECTIONS = {
    'alice-bob': {
        'nodes': ('alice', 'bob'),
        'urls': (AGENT_ALICE_URL, AGENT_BOB_URL),
        'ike_name': 'alice-to-bob',
        'child_name': 'net-traffic',
        'initiator': 'alice'
    },
    'carol-dave': {
        'nodes': ('carol', 'dave'),
        'urls': (AGENT_CAROL_URL, AGENT_DAVE_URL),
        'ike_name': 'carol-to-dave',
        'child_name': 'net-traffic',
        'initiator': 'carol'
    }
}

# --- CONFIGURAÇÕES DE MÉTRICAS ---
METRICS_FILE = "/scripts/experiment_metrics.csv"
METRICS_LOCK = threading.Lock()

def init_metrics():
    """Inicializa o CSV com cabeçalhos"""
    try:
        with open(METRICS_FILE, mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "cycle", "connection", "event_type", 
                "value_ms", "status_code", "details"
            ])
        print(f"[METRICS] Arquivo {METRICS_FILE} criado.")
    except Exception as e:
        print(f"[METRICS] Erro ao criar arquivo CSV: {e}")

def log_metric(cycle, conn, event, value_ms, status_code=0, details=""):
    """Escreve uma linha de métrica no CSV (thread-safe)"""
    try:
        timestamp = time.time()  # Timestamp Unix absoluto
        with METRICS_LOCK:
            with open(METRICS_FILE, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    f"{timestamp:.6f}", cycle, conn, event, 
                    f"{value_ms:.4f}", status_code, details
                ])
    except Exception as e:
        print(f"[METRICS] Erro ao salvar métrica: {e}")

logging.basicConfig(level=logging.INFO, format='[SDN-Multi] %(asctime)s - %(message)s')
logger = logging.getLogger("SDN-Multi")

# Carregamento da Chave de Assinatura
try:
    with open(PRIV_KEY_PATH, "rb") as f:
        SIGNING_KEY = f.read()
    logger.info("Chave Privada ML-DSA-65 carregada.")
except Exception as e:
    logger.error(f"Nao foi possivel carregar chave privada: {e}")
    exit(1)

# Geração/Carregamento das Chaves KEM do Controlador
try:
    if HAS_OQS:
        kem = oqs.KeyEncapsulation(KEM_ALGO)
        KEM_PUBLIC_KEY = kem.generate_keypair()
        KEM_SECRET_KEY = kem.export_secret_key()
        logger.info(f"Par de chaves {KEM_ALGO} gerado para criptografia de envelope.")
    else:
        raise ImportError("liboqs ausente")
except Exception as e:
    logger.warning(f"Falha ao gerar chaves KEM: {e}")
    KEM_PUBLIC_KEY = None
    KEM_SECRET_KEY = None

def send_encrypted_signed_request(url, endpoint, payload_dict, agent_kem_public_key):
    """
    Envia JSON criptografado com ML-KEM e assinado com ML-DSA-65
    CORRIGIDO: Verifica HAS_OQS antes de operações criptográficas.
    """
    try:
        payload_dict['_timestamp'] = int(time.time())
        payload_dict['_nonce'] = base64.b64encode(os.urandom(16)).decode('utf-8')
        
        payload_json = json.dumps(payload_dict)
        payload_bytes = payload_json.encode('utf-8')

        encrypted_payload = payload_bytes
        kem_ciphertext = None
        
        # Criptografia KEM (Apenas se liboqs estiver disponível)
        if agent_kem_public_key and HAS_OQS:
            try:
                with oqs.KeyEncapsulation(KEM_ALGO) as kem_client:
                    kem_ciphertext, shared_secret = kem_client.encap_secret(agent_kem_public_key)
                    
                    from hashlib import sha256
                    key = sha256(shared_secret).digest()
                    # Garante que a chave repetida cubra todo o payload
                    keystream = (key * ((len(payload_bytes) // 32) + 1))[:len(payload_bytes)]
                    encrypted_payload = bytes(a ^ b for a, b in zip(payload_bytes, keystream))
            except Exception as e:
                logger.warning(f"Falha na criptografia KEM: {e}. Enviando em texto claro.")
        
        # Assinatura Digital (CORREÇÃO: Checagem HAS_OQS para evitar crash)
        signature = b""
        if HAS_OQS:
            try:
                with oqs.Signature(AUTH_ALGO, secret_key=SIGNING_KEY) as signer:
                    signature = signer.sign(encrypted_payload)
            except Exception as e:
                logger.error(f"Erro ao assinar payload: {e}")
                return False, "Signature Error"
        else:
            logger.critical("Impossível assinar: liboqs não disponível.")
            return False, "Signing Unavailable"
        
        headers = {
            'Content-Type': 'application/octet-stream',
            'X-PQC-Signature': base64.b64encode(signature).decode('utf-8'),
            'X-KEM-Encrypted': 'true' if kem_ciphertext else 'false'
        }
        
        if kem_ciphertext:
            headers['X-KEM-Ciphertext'] = base64.b64encode(kem_ciphertext).decode('utf-8')
        
        resp = requests.post(
            f"{url}/{endpoint}",
            data=encrypted_payload,
            headers=headers,
            timeout=HTTP_TIMEOUT,
            verify=AGENT_CA_BUNDLE
        )
        
        if resp.status_code == 200:
            return True, resp.json()
        elif resp.status_code == 403:
            logger.critical(f"FALHA DE AUTENTICACAO: O Agente {url} rejeitou nossa assinatura!")
            return False, resp.text
        else:
            logger.error(f"Erro {resp.status_code}: {resp.text}")
            return False, resp.text
            
    except Exception as e:
        logger.error(f"Excecao de rede: {e}")
        return False, str(e)

AGENT_PUBLIC_KEYS = {}

def register_agent_public_key(url):
    """Obtém a chave pública KEM do agente"""
    try:
        resp = requests.get(f"{url}/public-key", timeout=5, verify=AGENT_CA_BUNDLE)
        if resp.status_code == 200:
            data = resp.json()
            pk_b64 = data.get('kem_public_key')
            if pk_b64:
                AGENT_PUBLIC_KEYS[url] = base64.b64decode(pk_b64)
                logger.debug(f"Chave pública KEM do agente {url} registrada.")
                return True
    except:
        pass
    
    logger.debug(f"Agente {url} não possui chave KEM. Usando apenas assinatura.")
    AGENT_PUBLIC_KEYS[url] = None
    return False

def push_key(url, owner, key_bytes):
    register_agent_public_key(url)
    payload = {
        "key_id": owner, 
        "key_hex": binascii.hexlify(key_bytes).decode()
    }
    success, _ = send_encrypted_signed_request(url, "inject-key", payload, AGENT_PUBLIC_KEYS.get(url))
    return success

def terminate_tunnel(url, ike_name):
    if url not in AGENT_PUBLIC_KEYS:
        register_agent_public_key(url)
    success, _ = send_encrypted_signed_request(url, "terminate", {"ike": ike_name}, AGENT_PUBLIC_KEYS.get(url))
    return success

def initiate_tunnel(url, child_name):
    if url not in AGENT_PUBLIC_KEYS:
        register_agent_public_key(url)
    success, resp = send_encrypted_signed_request(url, "rekey", {"ike": child_name}, AGENT_PUBLIC_KEYS.get(url))
    if success and ("rekeyed" in str(resp) or "established" in str(resp)):
        return True
    return False

def generate_pqc_key(algo=PQC_ALGO):
    try:
        if not HAS_OQS:
            logger.critical("ERRO CRÍTICO: liboqs não disponível.")
            exit(1)
        
        start = time.time()
        with oqs.KeyEncapsulation(algo) as kem:
            pk = kem.generate_keypair()
            ct, shared_secret = kem.encap_secret(pk)
        
        elapsed = (time.time() - start) * 1000
        return shared_secret[:32], elapsed 
    except Exception as e:
        logger.critical(f"ERRO CRÍTICO no PQC: {e}")
        exit(1)

def request_qkd_key(kme_url, peer_sae_id, cert_tuple=None, size=32):
    result = {'key': None, 'time_ms': 0, 'status_code': 0, 'success': False, 'error': None}
    try:
        if not os.path.exists(CERT_DIR):
            result['error'] = "cert_dir_not_found"
            return result
        if not cert_tuple:
            result['error'] = "no_cert_configured"
            return result
        
        client = QuKayDeeClient(kme_url, cert_tuple[0], cert_tuple[1], CA_CERT)
        api_result = client.get_enc_key(peer_sae_id, number=1)
        
        result['time_ms'] = api_result['response_time_ms']
        result['status_code'] = api_result['status_code']
        result['success'] = api_result['success']
        result['error'] = api_result['error']
        
        if api_result['success'] and api_result['keys']:
            result['key'] = api_result['keys'][0]['key'][:size]
        return result
    except Exception as e:
        result['error'] = str(e)
        return result

def generate_hybrid_key(cycle, conn_name, node1, node2, kme_urls, certs, node_to_sae=None):
    try:
        logger.info(f"[{conn_name}] Gerando chave híbrida (QKD+PQC)...")
        
        qkd_key = None
        qkd_time = 0
        qkd_status = 0
        
        kme_url = kme_urls.get(node1)
        cert = certs.get(node1)
        peer_sae_id = node_to_sae.get(node2, node2) if node_to_sae else node2
        
        if kme_url and cert:
            qkd_result = request_qkd_key(kme_url, peer_sae_id, cert)
            qkd_time = qkd_result['time_ms']
            qkd_status = qkd_result['status_code']
            qkd_key = qkd_result['key']
            
            availability = 1 if qkd_result['success'] else 0
            log_metric(cycle, conn_name, "KEY_AVAILABILITY", availability, 
                      qkd_status, f"qkd_fetch_ms={qkd_time:.2f}")
            
            if qkd_result['success']:
                logger.info(f"  [QKD] Chave disponível (HTTP {qkd_status}, {qkd_time:.2f}ms)")
            else:
                logger.warning(f"  [QKD] Chave INDISPONÍVEL (HTTP {qkd_status}): {qkd_result['error']}")
        
        log_metric(cycle, conn_name, "QKD_FETCH", qkd_time, qkd_status, 
                  "success" if qkd_key else f"failed:{qkd_result.get('error', 'unknown')}")
        
        pqc_secret, pqc_time = generate_pqc_key(PQC_ALGO)
        logger.info(f"  [PQC] {PQC_ALGO} gerado ({pqc_time:.2f}ms)")
        log_metric(cycle, conn_name, "PQC_GEN", pqc_time, 200, PQC_ALGO)
        
        start_mix = time.time()
        if qkd_key:
            final_key = mix_keys(pqc_secret, qkd_key)
            mix_status = "hybrid_qkd_pqc"
            logger.info(f"  ✓ Chave Final: PQC({PQC_ALGO}) + QKD + HKDF-SHA256")
        else:
            final_key = mix_keys(pqc_secret, b'\x00' * 32)
            mix_status = "fallback_pqc_only"
            logger.warning(f"  ✓ Chave Final: PQC({PQC_ALGO}) PURO (QKD indisponível)")
        
        mix_time = (time.time() - start_mix) * 1000
        log_metric(cycle, conn_name, "HKDF_MIX", mix_time, 200, mix_status)

        total_hybrid_time = qkd_time + pqc_time + mix_time
        log_metric(cycle, conn_name, "HYBRIDIZATION_OVERHEAD", total_hybrid_time, 
                  qkd_status, f"qkd={qkd_time:.2f}|pqc={pqc_time:.2f}|hkdf={mix_time:.2f}|mode={mix_status}")
        
        return final_key
        
    except Exception as e:
        logger.critical(f"ERRO ao gerar chave híbrida: {e}")
        log_metric(cycle, conn_name, "HYBRIDIZATION_OVERHEAD", 0, 500, f"critical_error:{e}")
        exit(1)

def process_connection_keys(cycle, conn_name, conn_info, kme_urls, certs, node_to_sae):
    try:
        node1, node2 = conn_info['nodes']
        logger.info(f"\n[{conn_name}] Processando conexão {node1} <-> {node2}...")
        final_key = generate_hybrid_key(cycle, conn_name, node1, node2, kme_urls, certs, node_to_sae)
        return conn_name, final_key
    except Exception as e:
        logger.error(f"[{conn_name}] Erro ao gerar chave: {e}")
        return conn_name, None

def main():
    logger.info(f"SDN Seguro Multi-Nós Iniciado (Autenticado via {AUTH_ALGO})")
    init_metrics()
    
    logger.info(f"Gerenciando {len(CONNECTIONS)} conexões: {list(CONNECTIONS.keys())}")
    
    kme_urls = {
        'alice': URL_KME_ALICE, 'bob': URL_KME_BOB,
        'carol': URL_KME_CAROL, 'dave': URL_KME_DAVE
    }
    
    certs = {
        'alice': (f"{CERT_DIR}/sae-1.crt", f"{CERT_DIR}/sae-1.key"),
        'bob': (f"{CERT_DIR}/sae-2.crt", f"{CERT_DIR}/sae-2.key"),
        'carol': (f"{CERT_DIR}/sae-3.crt", f"{CERT_DIR}/sae-3.key"),
        'dave': (f"{CERT_DIR}/sae-4.crt", f"{CERT_DIR}/sae-4.key")
    }
    
    node_to_sae = {'alice': 'sae-1', 'bob': 'sae-2', 'carol': 'sae-3', 'dave': 'sae-4'}
    
    cycle = 0
    while True:
        cycle += 1
        logger.info(f"\n{'='*60}\n--- Ciclo {cycle} ---\n{'='*60}")
        
        # FASE 1: GERAR TODAS AS CHAVES EM PARALELO
        connection_keys = {}
        with ThreadPoolExecutor(max_workers=len(CONNECTIONS)) as executor:
            future_to_conn = {
                executor.submit(process_connection_keys, cycle, c_name, c_info, kme_urls, certs, node_to_sae): c_name
                for c_name, c_info in CONNECTIONS.items()
            }
            for future in as_completed(future_to_conn):
                conn_name = future_to_conn[future]
                try:
                    res_name, key = future.result()
                    connection_keys[res_name] = key
                except Exception as e:
                    logger.error(f"  ✗ [{conn_name}] Falha na thread: {e}")
                    connection_keys[conn_name] = None
        
        # FASE 2: INJEÇÃO SEQUENCIAL
        for conn_name, conn_info in CONNECTIONS.items():
            node1, node2 = conn_info['nodes']
            url1, url2 = conn_info['urls']
            ike_name = conn_info['ike_name']
            child_name = conn_info['child_name']
            initiator = conn_info['initiator']
            
            final_key = connection_keys.get(conn_name)
            if not final_key: continue
            
            e2e_start = time.time()
            
            # Injeção Node 1
            t0 = time.time()
            ok1 = push_key(url1, node2, final_key)
            push1_time = (time.time() - t0) * 1000
            log_metric(cycle, conn_name, "PUSH_KEY", push1_time, 200 if ok1 else 500, f"target={node1}")
            
            # Injeção Node 2
            t0 = time.time()
            ok2 = push_key(url2, node1, final_key)
            push2_time = (time.time() - t0) * 1000
            log_metric(cycle, conn_name, "PUSH_KEY", push2_time, 200 if ok2 else 500, f"target={node2}")
            
            if ok1 and ok2:
                initiator_url = url1 if initiator == node1 else url2
                if cycle > 1:
                    terminate_tunnel(initiator_url, ike_name)
                    time.sleep(2)
                
                t_start = time.time()
                success = initiate_tunnel(initiator_url, child_name)
                tunnel_time = (time.time() - t_start) * 1000
                e2e_total = (time.time() - e2e_start) * 1000
                
                status = "success" if success else "failed"
                log_metric(cycle, conn_name, "TUNNEL_ESTABLISH", tunnel_time, 200 if success else 500, status)
                log_metric(cycle, conn_name, "E2E_REKEY_LATENCY", e2e_total, 200 if success else 500, status)
                logger.info(f"  ✓ {conn_name}: {status.upper()} (E2E: {e2e_total:.2f}ms)")
            else:
                logger.error(f"  ✗ {conn_name}: Falha na injeção de chaves")
            
            time.sleep(1)
        
        logger.info(f"Ciclo {cycle} completo. Aguardando...")
        time.sleep(10)

if __name__ == "__main__":
    main()