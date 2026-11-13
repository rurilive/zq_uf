#!/usr/bin/env bash
# 重新生成 demo/tests 用的本地证书（仅用于演示，勿用于生产）。
set -euo pipefail
cd "$(dirname "$0")"

# 根 CA
openssl req -x509 -newkey rsa:2048 -keyout rootCA.key -out rootCA.pem \
  -days 3650 -nodes -subj "/CN=tlssafe-demo-root" 2>/dev/null

# 中间 CA（由根签发）
openssl req -newkey rsa:2048 -keyout intermediate.key -out intermediate.csr \
  -nodes -subj "/CN=tlssafe-demo-intermediate" 2>/dev/null
openssl x509 -req -in intermediate.csr -CA rootCA.pem -CAkey rootCA.key \
  -CAcreateserial -out intermediate.pem -days 3650 2>/dev/null

# 链不完整：叶子由中间 CA 签发，服务器只下发叶子
openssl req -newkey rsa:2048 -keyout chain-leaf.key -out chain-leaf.csr \
  -nodes -subj "/CN=localhost" 2>/dev/null
openssl x509 -req -in chain-leaf.csr -CA intermediate.pem -CAkey intermediate.key \
  -CAcreateserial -out chain-leaf.pem -days 3650 \
  -extfile <(printf 'subjectAltName=DNS:localhost') 2>/dev/null

# 主机名不匹配：CN/SAN 是 wrong-host.example
openssl req -newkey rsa:2048 -keyout mismatch.key -out mismatch.csr \
  -nodes -subj "/CN=wrong-host.example" 2>/dev/null
openssl x509 -req -in mismatch.csr -CA rootCA.pem -CAkey rootCA.key \
  -CAcreateserial -out mismatch.pem -days 3650 \
  -extfile <(printf 'subjectAltName=DNS:wrong-host.example') 2>/dev/null

# 已过期：有效期 2024-01-01 ~ 2025-01-01，需要 openssl ca 指定日期
openssl req -newkey rsa:2048 -keyout expired.key -out expired.csr \
  -nodes -subj "/CN=localhost" 2>/dev/null
rm -rf ca-db && mkdir -p ca-db && touch ca-db/index.txt && echo 1000 > ca-db/serial
cat > ca.cnf <<'CNF'
[ ca ]
default_ca = CA_default
[ CA_default ]
dir = ./ca-db
database = $dir/index.txt
new_certs_dir = $dir
serial = $dir/serial
certificate = ./rootCA.pem
private_key = ./rootCA.key
default_md = sha256
policy = policy_any
x509_extensions = leaf_ext
[ policy_any ]
commonName = supplied
[ leaf_ext ]
subjectAltName = DNS:localhost
CNF
openssl ca -batch -config ca.cnf -in expired.csr -out expired.pem \
  -startdate 20240101000000Z -enddate 20250101000000Z 2>/dev/null

rm -rf ca-db ca.cnf *.csr *.srl
echo "certs regenerated in $(pwd)"
