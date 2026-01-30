import requests
import json
import sys
import os
import base64
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class QuKayDeeClient:
    def __init__(self, kme_url, cert_path, key_path, ca_cert_path):
        self.base_url = kme_url.rstrip('/')
        self.cert = (cert_path, key_path)
       
        self.verify = False
        print(f"   [QuKayDee] Cliente iniciado para: {self.base_url}")
        print(f"   [AVISO] Verificação SSL DESABILITADA (apenas para testes)")

    def get_enc_key(self, peer_sae_id, number=1):
        """
        Alice pede chaves para falar com o Bob (POST /enc_keys)
        Retorna: dict com keys, status_code, response_time_ms, success
        """
        url = f"{self.base_url}/api/v1/keys/{peer_sae_id}/enc_keys"
        
        payload = {
            "number": number,
            "size": 256
        }
        
        result = {
            'keys': [],
            'status_code': 0,
            'response_time_ms': 0,
            'success': False,
            'error': None
        }
        
        start_time = time.time()
        try:
            response = requests.post(
                url, 
                json=payload, 
                cert=self.cert, 
                verify=self.verify,
                timeout=10
            )
            result['response_time_ms'] = (time.time() - start_time) * 1000
            result['status_code'] = response.status_code
            
            response.raise_for_status()
            
            data = response.json()
            keys_list = data['keys']
            
            clean_keys = []
            for k in keys_list:
                k_id = k.get('key_ID') or k.get('key_id')
                k_val_b64 = k['key']
                k_val_bytes = base64.b64decode(k_val_b64)
                
                clean_keys.append({
                    'key_id': k_id,
                    'key': k_val_bytes
                })
            
            result['keys'] = clean_keys
            result['success'] = True
            print(f"   [QuKayDee] Recebidas {len(clean_keys)} chaves (HTTP {result['status_code']}, {result['response_time_ms']:.1f}ms)")
            return result

        except Exception as e:
            result['response_time_ms'] = (time.time() - start_time) * 1000
            if 'response' in locals() and response is not None:
                result['status_code'] = response.status_code
            result['error'] = str(e)
            print(f"[ERRO QuKayDee] Falha ao obter chaves: {e} (HTTP {result['status_code']})")
            return result

    def get_dec_key(self, peer_sae_id, key_id):
        """
        Bob pede a chave específica pelo ID (POST /dec_keys)
        """
        url = f"{self.base_url}/api/v1/keys/{peer_sae_id}/dec_keys"

        payload = {
            "key_IDs": [{"key_ID": key_id}]
        }

        try:
            response = requests.post(
                url, 
                json=payload, 
                cert=self.cert, 
                verify=self.verify,
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            keys_list = data['keys']
            
            if not keys_list:
                raise Exception("Chave não retornada pelo servidor")

            
            k_val_b64 = keys_list[0]['key']
            return base64.b64decode(k_val_b64)

        except Exception as e:
            print(f"[ERRO QuKayDee-Bob] Falha ao recuperar chave {key_id}: {e}")
            if 'response' in locals() and response is not None:
                print(f"Detalhe: {response.text}")
            raise