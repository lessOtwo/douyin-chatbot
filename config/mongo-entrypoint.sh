#!/usr/bin/env bash
set -e
if [ ! -f /data/keys/mongodb.key ]; then
  openssl rand -base64 128 > /data/keys/mongodb.key
fi
chmod 400 /data/keys/mongodb.key
chown 999:999 /data/keys/mongodb.key
exec docker-entrypoint.sh mongod --keyFile /data/keys/mongodb.key --replSet rs0 --bind_ip_all

