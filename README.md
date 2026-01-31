# SDKM-PoC: Gestão de Chaves Quânticas Definida por Software

## Descrição
Este repositório contém uma Prova de Conceito (PoC) para um sistema de gestão de chaves híbrido que integra Criptografia Pós-Quântica (PQC) e Distribuição de Chaves Quânticas (QKD) para proteger comunicações via VPN IPsec. A arquitetura utiliza uma abordagem de Redes Definidas por Software (SDN) para orquestrar a segurança de múltiplos nós de forma dinâmica.

## Cenários de Teste
O projeto está estruturado para comparar dois cenários distintos através de uma infraestrutura de testes automatizada:

### 1. Cenário Baseline (Sem SDKM)
Representa a operação padrão de uma VPN IPsec sem a intervenção da camada de gestão quântica.
* **Infraestrutura**: Utiliza contentores com o prefixo `base_` (ex: `base_alice`) definidos num ambiente isolado.
* **Configuração**: Os nós utilizam imagens padrão do StrongSwan com configurações de túnel estáticas carregadas via `swanctl.conf`.
* **Execução**: Ativado no controlador de automação através da flag `--baseline`.

### 2. Cenário com SDKM (Modo SDN/Quantum)
Introduz a orquestração dinâmica de chaves híbridas que combinam segurança pós-quântica e quântica.
* **Controlador SDN**: O orquestrador gera chaves híbridas combinando o algoritmo ML-KEM-768 com chaves QKD obtidas via API compatível com o padrão ETSI GS QKD 014.
* **Injeção Dinâmica**: Os agentes VPN nos nós recebem estas chaves através de uma API segura e injetam-nas no daemon Charon em tempo real usando o protocolo VICI.
* **Segurança do Plano de Controlo**: Todas as mensagens de gestão entre o controlador e os agentes são assinadas com ML-DSA-65, protegidas por encriptação de envelope e validadas contra ataques de replay.

## Metodologia de Comparação
Para garantir resultados precisos, ambos os cenários são submetidos às mesmas condições experimentais:
* **Emulação de Rede**: Aplicação de perfis de rede WAN (como `wan-fiber`) que simulam latência, jitter e perda de pacotes via `tc/netem`.
* **Bateria de Testes**: Execução automatizada de medições de débito TCP (throughput), estabilidade de vídeo (UDP jitter) e transferências de ficheiros de grande escala (ex: 500MB).
* **Métricas de Recursos**: Monitorização contínua do consumo de CPU e memória de todos os contentores para avaliar o impacto computacional da solução SDKM.

## Arquitetura Técnica
* **Nós de Rede**: Contentores baseados em Ubuntu 22.04 equipados com StrongSwan e agentes API Flask.
* **Algoritmo de Mixagem**: Utilização do protocolo HKDF-SHA256 para derivar uma chave final de 256 bits a partir de material PQC e QKD.
* **Componentes Core**:
    * `liboqs`: Biblioteca para algoritmos criptográficos pós-quânticos.
    * `strongSwan`: Implementação de VPN IPsec com suporte a VICI.
    * `iperf3`: Ferramenta principal para geração de tráfego e medição de performance.

## Fluxo de Operação
1. **Inicialização**: O script `entrypoint.sh` configura o ambiente de rede e inicia os processos críticos (Charon e Agente VPN).
2. **Ciclo SDN**: O controlador SDN executa ciclos periódicos onde solicita chaves QKD, gera segredos PQC e coordena a atualização dos túneis nos nós.
3. **Execução de Experiências**: O `automation_controller.py` gere o ciclo de vida dos testes, aplicando condições de rede e arquivando os resultados em formatos CSV e JSON para análise.
