# Failed commands and long-running jobs, into ambient.
# Copy to ~/.config/fish/conf.d/. Set DUSHA_URL and DUSHA_TOKEN if the server isn't local and open.

set -q DUSHA_URL; or set -g DUSHA_URL http://127.0.0.1:8765

function __dusha_observe --argument-names source text
    set -l body (python3 -c 'import json, sys; print(json.dumps({"source": sys.argv[1], "text": sys.argv[2][:300]}))' $source $text)
    curl -s --max-time 2 -o /dev/null -X POST $DUSHA_URL/state/v1/ambient \
        -H 'content-type: application/json' -H "x-companion-token: $DUSHA_TOKEN" -d $body &
    disown 2>/dev/null
end

function __dusha_ambient --on-event fish_postexec
    set -l code $status
    set -l seconds (math -s0 $CMD_DURATION / 1000)
    # 130 is Ctrl-C, not worth mentioning.
    if test $code -ne 0 -a $code -ne 130
        __dusha_observe shell "`$argv[1]` failed (exit $code) in "(prompt_pwd)
    else if test $seconds -ge 300
        __dusha_observe shell "`$argv[1]` finished after "(math -s0 $seconds / 60)" min in "(prompt_pwd)
    end
end
