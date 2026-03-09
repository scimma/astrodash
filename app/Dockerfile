FROM python:3.11.13 AS deps

RUN pip install --upgrade pip
COPY ./requirements.txt /requirements.txt
RUN pip install -r /requirements.txt --no-cache-dir

FROM python:3.11.13
ENV PYTHONUNBUFFERED=1

RUN apt-get update && DEBIAN_FRONTEND=noninteractive && apt-get install -y --no-install-recommends \
    jq \
    && rm -rf /var/lib/apt/list/*

COPY --from=deps /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=deps /usr/local/bin/ /usr/local/bin/

RUN mkdir /app
COPY . /app
WORKDIR /app
