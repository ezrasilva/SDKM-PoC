FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# 1. Instalar dependências
RUN apt-get update && apt-get install -y \
    strongswan \
    strongswan-pki \
    strongswan-swanctl \
    libcharon-extra-plugins \
    iproute2 \
    iputils-ping \
    iperf3 \
    kmod \
    nano \
    python3 \
    python3-pip \
    python3-venv \
    git \
    cmake \
    gcc \
    libssl-dev \
    ninja-build \
    tcpdump \
    tshark \
    && rm -rf /var/lib/apt/lists/*

# 2. Compilar e instalar liboqs (Biblioteca C)
WORKDIR /opt
RUN git clone --depth 1 --branch main https://github.com/open-quantum-safe/liboqs.git \
    && cd liboqs \
    && mkdir build && cd build \
    && cmake -GNinja -DBUILD_SHARED_LIBS=ON -DOQS_USE_OPENSSL=ON -DCMAKE_INSTALL_PREFIX=/usr/local .. \
    && ninja \
    && ninja install \
    && ln -sf /usr/local/lib/liboqs.so /usr/lib/liboqs.so \
    && ln -sf /usr/local/lib/liboqs.so.0 /usr/lib/liboqs.so.0 \
    && ldconfig

# Configurar variáveis de ambiente para liboqs
ENV LD_LIBRARY_PATH="/usr/local/lib"
ENV PKG_CONFIG_PATH="/usr/local/lib/pkgconfig"
RUN ldconfig

# 3. Instalar liboqs-python (Wrapper Python Oficial)
WORKDIR /opt
RUN git clone --depth 1 --branch main https://github.com/open-quantum-safe/liboqs-python.git \
    && cd liboqs-python \
    && pip3 install .

# 4. Instalar outras libs Python (Cryptography e VICI)
RUN pip3 install --no-cache-dir cryptography vici flask requests

COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 5. Voltar para o diretório padrão e rodar o Charon
WORKDIR /
CMD ["/usr/lib/ipsec/charon"]