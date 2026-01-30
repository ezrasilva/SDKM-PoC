from flask import Flask, request, jsonify
import vici
import socket
import logging
import time
import subprocess
import oqs
import base64
import os
import json
import sys
from hashlib import sha256
from collections import deque

app = Flask(__name__)
VICI_SOCKET = "/var/run/charon.vici"
AUTH_ALGO = "ML-DSA-65"
KEM_ALGO = "ML-KEM-768"
PUB_KEY_PATH = "/scripts/orchestrator_auth.pub"
TLS_CERT_PATH = "/scripts/certs/agent_cert.pem"
TLS_KEY_PATH = "/scripts/certs/agent_key.pem"
MAX_MESSAGE_AGE_SECONDS = 30

logging.basicConfig(level=logging.INFO, format='[AGENT] %(asctime)s - %(message)s')
logger = logging.getLogger("VPN-Agent")


MAX_NONCE_CACHE = 10000
USED_NONCES = deque(maxlen=MAX_NONCE_CACHE)

try:
    with open(PUB_KEY_PATH, "rb") as f:
        ORCHESTRATOR_PUB_KEY = f.read()
    logger.info("Chave Pública ML-DSA-65 carregada com sucesso.")
except Exception as e:
    logger.critical(f"ERRO FATAL: Nao foi possivel ler a chave publica: {e}")
    sys.exit(1)


try:
    kem = oqs.KeyEncapsulation(KEM_ALGO)
    KEM_PUBLIC_KEY = kem.generate_keypair()
    KEM_SECRET_KEY = kem.export_secret_key()
    logger.info(f"Par de chaves {KEM_ALGO} gerado para descriptografia de envelope.")
except Exception as e:
    logger.warning(f"Falha ao gerar chaves KEM: {e}. Criptografia de envelope desabilitada.")
    KEM_PUBLIC_KEY = None
    KEM_SECRET_KEY = None

def verify_signature(payload_bytes, signature_b64):
    """Verifica se o payload foi assinado pelo Orquestrador"""
    try:
        signature = base64.b64decode(signature_b64)
        verifier = oqs.Signature(AUTH_ALGO)
        return verifier.verify(payload_bytes, signature, ORCHESTRATOR_PUB_KEY)
    except Exception as e:
        logger.error(f"Erro na verificacao da assinatura: {e}")
        return False

def decrypt_payload(encrypted_bytes, kem_ciphertext_b64):
    """Descriptografa payload usando ML-KEM"""
    try:
        if not KEM_SECRET_KEY:
            logger.warning("KEM não disponível. Assumindo payload não criptografado.")
            return encrypted_bytes
        
        kem_ciphertext = base64.b64decode(kem_ciphertext_b64)
        
        with oqs.KeyEncapsulation(KEM_ALGO, secret_key=KEM_SECRET_KEY) as kem_server:
            shared_secret = kem_server.decap_secret(kem_ciphertext)
            
            key = sha256(shared_secret).digest()
            decrypted = bytes(a ^ b for a, b in zip(encrypted_bytes, (key * ((len(encrypted_bytes) // 32) + 1))[:len(encrypted_bytes)]))
            return decrypted
    except Exception as e:
        logger.error(f"Erro na descriptografia KEM: {e}")
        return None

def check_replay_protection(payload_dict):
    """Verifica timestamp e nonce para prevenir replay attacks"""
    try:
        timestamp = payload_dict.get('_timestamp')
        nonce = payload_dict.get('_nonce')
        
        if not timestamp or not nonce:
            logger.warning("Mensagem sem timestamp/nonce. Possível ataque de replay.")
            return False
        
        
        age = int(time.time()) - timestamp
        if age > MAX_MESSAGE_AGE_SECONDS or age < -5:  # -5 para tolerar pequeno clock skew
            logger.warning(f"Mensagem expirada ou com timestamp futuro. Idade: {age}s")
            return False
        
    
       # Verificação corrigida
        if nonce in USED_NONCES:
            logger.error(f"REPLAY ATTACK: Nonce duplicado {nonce[:8]}...")
            return False
        
        # Adiciona no fim e remove automaticamente do início se atingir maxlen
        USED_NONCES.append(nonce)
        
       
        return True
    except Exception as e:
        logger.error(f"Erro na verificação de replay: {e}")
        return False


@app.before_request
def authenticate_and_decrypt():
   
    if request.path in ['/health', '/public-key']:
        return
    
    try:
        signature_header = request.headers.get('X-PQC-Signature')
        if not signature_header:
            return jsonify({"error": "Autenticacao PQC obrigatoria"}), 401
        
        # 1. Obter payload (pode estar criptografado)
        encrypted_payload = request.get_data()
        
        # 2. Verificar assinatura no payload criptografado
        if not verify_signature(encrypted_payload, signature_header):
            logger.warning(f"Tentativa de comando NAO AUTORIZADO de {request.remote_addr}")
            return jsonify({"error": "Assinatura Digital Invalida"}), 403
        
        # 3. Descriptografar se necessário
        payload_bytes = encrypted_payload
        if request.headers.get('X-KEM-Encrypted') == 'true':
            kem_ct_header = request.headers.get('X-KEM-Ciphertext')
            if not kem_ct_header:
                return jsonify({"error": "KEM ciphertext ausente"}), 400
            
            payload_bytes = decrypt_payload(encrypted_payload, kem_ct_header)
            if payload_bytes is None:
                return jsonify({"error": "Falha na descriptografia"}), 400
        
        # 4. Parsear JSON
        try:
            payload_dict = json.loads(payload_bytes.decode('utf-8'))
        except Exception as e:
            logger.error(f"Erro ao parsear JSON: {e}")
            return jsonify({"error": "JSON inválido"}), 400
        
        # 5. Verificar proteção contra replay
        if not check_replay_protection(payload_dict):
            return jsonify({"error": "Replay attack detectado ou mensagem expirada"}), 403
        
        # 6. Armazenar payload descriptografado para as rotas
        request.decrypted_json = payload_dict
        
    except Exception as e:
        logger.error(f"Erro no middleware de segurança: {e}")
        return jsonify({"error": "Erro na validação de segurança"}), 500


def get_vici_session():
    for i in range(5): 
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(VICI_SOCKET)
            return vici.Session(s)
        except: time.sleep(1)
    return None


def resolve_tls_paths():
    """Escolhe cert/key específicos do container (alice/bob/carol/dave) ou fallback genérico"""
    cert_env = os.environ.get("TLS_CERT_PATH")
    key_env = os.environ.get("TLS_KEY_PATH")
    if cert_env and key_env:
        return cert_env, key_env

    hostname = socket.gethostname().lower()
    base_dir = "/scripts/certs"
    host_cert = os.path.join(base_dir, f"{hostname}_cert.pem")
    host_key = os.path.join(base_dir, f"{hostname}_key.pem")

    if os.path.exists(host_cert) and os.path.exists(host_key):
        return host_cert, host_key

    return TLS_CERT_PATH, TLS_KEY_PATH

def initialize_vpn():
    session = get_vici_session()
    if not session: return False
    subprocess.run(["swanctl", "--load-all"], capture_output=True)
    return True



@app.route('/public-key', methods=['GET'])
def get_public_key():
    """Expõe a chave pública KEM para o orquestrador"""
    if KEM_PUBLIC_KEY:
        return jsonify({
            "kem_public_key": base64.b64encode(KEM_PUBLIC_KEY).decode('utf-8'),
            "algorithm": KEM_ALGO
        }), 200
    else:
        return jsonify({"error": "KEM não disponível"}), 503

@app.route('/inject-key', methods=['POST'])
def inject_key():
    data = request.decrypted_json  
    inject_start = time.time()
    try:
        session = get_vici_session()
        if not session: return jsonify({"error": "VICI off"}), 500
        
       
        try: session.unload_shared({'id': data['key_id']})
        except: pass
        
        session.load_shared({
            'type': 'ike',
            'data': bytes.fromhex(data['key_hex']),
            'owners': [data['key_id']]
        })
        inject_time_ms = (time.time() - inject_start) * 1000
        logger.info(f"Chave injetada para {data['key_id']} em {inject_time_ms:.2f}ms")
        return jsonify({
            "status": "verified_and_injected",
            "inject_time_ms": inject_time_ms,
            "timestamp": time.time()
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/terminate', methods=['POST'])
def terminate():
    data = request.decrypted_json
    try:
        session = get_vici_session()
        if not session: return jsonify({"error": "VICI off"}), 500
        session.terminate({'ike': data.get('ike', 'alice-to-bob')})
        return jsonify({"status": "terminated"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/rekey', methods=['POST'])
def rekey():
    data = request.decrypted_json
    try:
        session = get_vici_session()
        if not session: return jsonify({"error": "VICI off"}), 500
        res = list(session.initiate({'child': data.get('ike', 'net-traffic'), 'timeout': '20000'}))
        return jsonify({"status": "rekeyed", "details": str(res)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "online", "auth": "ML-DSA-65", "encryption": KEM_ALGO if KEM_PUBLIC_KEY else "disabled"}), 200

if __name__ == '__main__':
    if initialize_vpn():
        cert_path, key_path = resolve_tls_paths()
        logger.info(f"Iniciando HTTPS com cert={cert_path} key={key_path}")
        app.run(host='0.0.0.0', port=5000, ssl_context=(cert_path, key_path))