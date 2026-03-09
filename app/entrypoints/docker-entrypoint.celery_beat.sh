#!/bin/env bash

set -e

if [[ $DISABLE_CELERY_BEAT == "true" ]]; then
    echo "Celery Beat is disabled. Terminating."
    exit 0
fi

bash entrypoints/wait-for-it.sh ${DB_HOST}:${DB_PORT} --timeout=0
bash entrypoints/wait-for-it.sh ${MESSAGE_BROKER_HOST}:${MESSAGE_BROKER_PORT} --timeout=0
bash entrypoints/wait-for-it.sh ${WEB_APP_HOST}:${WEB_APP_PORT} --timeout=0

if [[ $DEV_MODE == 1 ]]; then
  watchmedo auto-restart --directory=./ --pattern=*.py --recursive -- \
  celery -A astrodash_project beat -l DEBUG
else
  celery -A astrodash_project beat -l INFO
fi
