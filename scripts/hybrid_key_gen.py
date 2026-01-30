import hashlib
import hmac
import secrets

def hkdf_extract(salt, input_key_material):
    """
    HKDF-Extract: Extrai uma chave pseudo-aleatória (PRK) do material de entrada.
    """
    
    return hmac.new(salt, input_key_material, hashlib.sha256).digest()

def hkdf_expand(pseudo_random_key, info, length=32):
    """
    HKDF-Expand: Expande a PRK para uma chave do tamanho desejado.
    """
    t = b""
    okm = b""
    i = 0
    while len(okm) < length:
        i += 1
        t = hmac.new(pseudo_random_key, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
    return okm[:length]

def mix_keys(pqc_secret, qkd_key):
    """
    Combina a chave PQC (Kyber) e a chave QKD usando HKDF-SHA256.
    Retorna uma chave de 32 bytes (256 bits) pronta para o AES.
    """

    input_key_material = pqc_secret + qkd_key
    salt = secrets.token_bytes(32) 
    
    prk = hkdf_extract(salt, input_key_material)
    
    final_key = hkdf_expand(prk, b"SDQC-HYBRID-KEY", 32)
    
    return final_key