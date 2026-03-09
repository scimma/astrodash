#!/bin/env bash
set -e

cd "$(dirname "$(readlink -f "$0")")"/..

if [[ ! -f "env/.env.dev" ]]; then
  touch env/.env.dev
fi

PROFILE=$1
source run/get_compose_args.sh $PROFILE

set -x
docker compose ${COMPOSE_CONFIG} exec -it ${TARGET_SERVICE} bash -c ' \
  coverage run manage.py test astrodash.tests users.tests -v 2 && \
  coverage report -i --omit=astrodash/tests/*,astrodash/migrations/*,astrodash_project/*,manage.py'
