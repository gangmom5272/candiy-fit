# 개발 서버 상태 감시 컨테이너 (S15P31A403-119). 동작은 monitor.sh 맨 위 설명 참고.
FROM alpine:3.22

# curl: /healthz 확인·알림 전송, openssl: 인증서 만료일, tzdata: 알림 시각을 한국 시간으로
RUN apk add --no-cache curl openssl tzdata

COPY monitor.sh /usr/local/bin/monitor.sh
RUN chmod 755 /usr/local/bin/monitor.sh

# 상태 파일은 /tmp에만 쓴다
USER nobody

ENTRYPOINT ["/usr/local/bin/monitor.sh"]
