#!/bin/bash
set -eu

docker build -t clockwork_web clockwork_web
docker build -t clockwork_web_test clockwork_web_test
docker build -t clockwork_tools_test -f clockwork_tools_test/Dockerfile .

. ./env.sh

# This is to ensure that there aren't lingering containers after the test script exits.
trap "docker-compose -f setup_ecosystem/docker-compose.yml down && docker-compose -f setup_ecosystem/docker-compose.yml rm -fv" EXIT

docker-compose -f setup_ecosystem/docker-compose.yml run clockwork_web_test
docker-compose -f setup_ecosystem/docker-compose.yml run clockwork_tools_test

