#!/bin/sh
# 개발 서버 상태 감시 (S15P31A403-119). compose의 monitor 컨테이너가 계속 돌린다.
# 5분마다 세 가지를 보고, 문제가 생기면 Mattermost 알림 채널로 보낸다.
#
# | 확인 | 방법 | 실패 조건 |
# | 서버 상태 | nginx를 거쳐 HTTPS /healthz (nginx·인증서·backend·DB를 한 번에) | 200이 아니거나 연결 실패 |
# | 인증서 만료 | nginx가 내주는 인증서의 만료일 | MONITOR_CERT_MIN_DAYS(14)일 안에 만료. certbot은 30일 전에 갱신하므로 갱신 실패 |
# | 디스크 | 컨테이너 루트(= Docker가 쓰는 서버 디스크) 사용률 | MONITOR_DISK_MAX_PERCENT(85)% 이상 |
#
# 알림은 파이프라인 알림(data/pipeline/src/candiy_pipeline/alert.py)과 같은 방식이다.
# 정상 → 실패면 한 번, 실패가 계속되면 6시간마다 다시, 알림을 보낸 실패가 정상이 되면 회복 알림.
# 서버 상태는 2번 연속 실패해야 알린다. 배포할 때 backend가 잠깐 내려가는 것은 알리지 않는다.
#
# 메시지에는 서버 이름·항목·상태 코드·오류 종류만 넣는다. 응답 본문과 웹훅 URL은 출력하지 않는다.
# 직접 한 번 확인: docker exec candiy-fit-monitor monitor.sh --once
set -u

DOMAIN=${MONITOR_DOMAIN:?MONITOR_DOMAIN이 없습니다}
TARGET=${MONITOR_TARGET:-nginx}
NAME=${MONITOR_NAME:-개발 서버}
WEBHOOK=${MONITOR_WEBHOOK_URL:-}
INTERVAL=${MONITOR_INTERVAL:-300}
REPEAT_EVERY=${MONITOR_REPEAT_EVERY:-21600}
CERT_MIN_DAYS=${MONITOR_CERT_MIN_DAYS:-14}
DISK_MAX_PERCENT=${MONITOR_DISK_MAX_PERCENT:-85}
STATE=/tmp/monitor

now() { date +%s; }
stamp() { TZ=Asia/Seoul date '+%m-%d %H:%M'; }

send() {
    if [ -z "$WEBHOOK" ]; then
        echo "알림 생략 (웹훅 없음): $1"
        return
    fi
    text=$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        -H 'Content-Type: application/json' -d "{\"text\":\"$text\"}" "$WEBHOOK")
    [ "$code" = 200 ] || echo "알림 전송 실패 (HTTP $code)"
}

# observe 이름 항목 결과(1=정상) 내용 연속실패기준
observe() {
    f=$STATE/$1
    label=$2
    fails=$(cat "$f.fails" 2>/dev/null || echo 0)
    if [ "$3" = 1 ]; then
        if [ -f "$f.sent" ]; then
            minutes=$(( ($(now) - $(cat "$f.since")) / 60 ))
            send "[$NAME] 회복: $label 정상 (${fails}번 연속 실패, 약 ${minutes}분)"
        fi
        rm -f "$f.fails" "$f.since" "$f.sent"
        return
    fi
    fails=$((fails + 1))
    echo "$fails" > "$f.fails"
    [ -f "$f.since" ] || now > "$f.since"
    [ "$fails" -ge "$5" ] || return
    if [ ! -f "$f.sent" ]; then
        send "[$NAME] 실패: $label - $4 ($(stamp) KST)"
        now > "$f.sent"
    elif [ $(( $(now) - $(cat "$f.sent") )) -ge "$REPEAT_EVERY" ]; then
        send "[$NAME] 실패 계속: $label - $4 (${fails}번째, $(stamp) KST)"
        now > "$f.sent"
    fi
}

check_health() {
    # 실제 주소·인증서로 요청하되 연결만 compose 안의 nginx로 보낸다 (서버가 자기 공인 IP로 돌아 나가지 않게)
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        --connect-to "$DOMAIN:443:$TARGET:443" "https://$DOMAIN/healthz")
    rc=$?
    if [ "$code" = 200 ]; then
        observe health "서버 상태(/healthz)" 1 "" 2
    elif [ "$rc" -ne 0 ]; then
        observe health "서버 상태(/healthz)" 0 "연결 실패 (curl 오류 $rc)" 2
    else
        observe health "서버 상태(/healthz)" 0 "HTTP $code" 2
    fi
    HEALTH=$code
}

check_cert() {
    cert=$(timeout 10 openssl s_client -connect "$TARGET:443" -servername "$DOMAIN" </dev/null 2>/dev/null |
        openssl x509 2>/dev/null)
    # 인증서를 못 읽는 건 nginx가 내려간 것이라 서버 상태 알림이 맡는다. 같은 장애를 두 번 알리지 않는다
    if [ -z "$cert" ]; then
        CERT=unknown
        return
    fi
    CERT=$(printf '%s\n' "$cert" | openssl x509 -noout -enddate | cut -d= -f2)
    if printf '%s\n' "$cert" | openssl x509 -noout -checkend $((CERT_MIN_DAYS * 86400)) >/dev/null; then
        observe cert "인증서 만료" 1 "" 1
    else
        observe cert "인증서 만료" 0 "${CERT_MIN_DAYS}일 안에 만료 (만료: $CERT). certbot 갱신 확인 필요" 1
    fi
}

check_disk() {
    DISK=$(df -P / | awk 'NR == 2 { sub("%", "", $5); print $5 }')
    if [ "$DISK" -lt "$DISK_MAX_PERCENT" ]; then
        observe disk "디스크" 1 "" 1
    else
        observe disk "디스크" 0 "사용률 ${DISK}% (기준 ${DISK_MAX_PERCENT}%)" 1
    fi
}

run_checks() {
    check_health
    check_cert
    check_disk
    echo "$(stamp) health=$HEALTH cert_until=\"$CERT\" disk=${DISK}%"
}

mkdir -p "$STATE"
if [ "${1:-}" = "--once" ]; then
    run_checks
    exit 0
fi

trap 'exit 0' TERM INT
echo "monitor 시작: ${INTERVAL}초마다 $DOMAIN 확인 (알림 $([ -n "$WEBHOOK" ] && echo 켜짐 || echo 꺼짐))"
while :; do
    run_checks
    sleep "$INTERVAL" &
    wait $!
done
