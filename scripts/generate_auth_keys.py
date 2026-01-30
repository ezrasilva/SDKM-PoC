import oqs
import binascii

ALGO = "ML-DSA-65"

def main():
    print(f"Gerando par de chaves de Autoridade SDN ({ALGO})...")
    with oqs.Signature(ALGO) as sig:
        public_key = sig.generate_keypair()
        private_key = sig.export_secret_key()
        
        with open("/scripts/orchestrator_auth.key", "wb") as f:
            f.write(private_key)
            
        with open("/scripts/orchestrator_auth.pub", "wb") as f:
            f.write(public_key)
            
    print("Sucesso! Chaves salvas em /scripts/orchestrator_auth.*")

if __name__ == "__main__":
    main()