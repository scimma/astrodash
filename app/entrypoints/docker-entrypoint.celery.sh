#!/bin/env bash

set -e

bash entrypoints/wait-for-it.sh ${DB_HOST}:${DB_PORT} --timeout=0
bash entrypoints/wait-for-it.sh ${MESSAGE_BROKER_HOST}:${MESSAGE_BROKER_PORT} --timeout=0
bash entrypoints/wait-for-it.sh ${WEB_APP_HOST}:${WEB_APP_PORT} --timeout=0

if [[ $DEV_MODE == 1 ]]; then
  watchmedo auto-restart --directory=./ --pattern=*.py --recursive -- \
  celery -A astrodash_project worker -l DEBUG \
    --queues ${CELERY_QUEUES:-celery} \
    --max-memory-per-child ${CELERY_MAX_MEMORY_PER_CHILD:-12000} \
    --concurrency ${CELERY_CONCURRENCY:-4}
else
  celery -A astrodash_project worker -l WARNING \
    --queues ${CELERY_QUEUES:-celery} \
    --max-memory-per-child ${CELERY_MAX_MEMORY_PER_CHILD:-12000} \
    --concurrency ${CELERY_CONCURRENCY:-4}
fi
