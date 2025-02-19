FROM alpine:3.13

RUN apk add --update --no-cache python3 py3-pip gcc python3-dev musl-dev
RUN pip3 install --no-cache --upgrade pip setuptools minimalmodbus influxdb influxdb-client[ciso]

WORKDIR /monitor

RUN echo "* * * * * python3 /monitor/monitor.py" >> /etc/crontabs/root && crontab -l
CMD crond -f -l 8