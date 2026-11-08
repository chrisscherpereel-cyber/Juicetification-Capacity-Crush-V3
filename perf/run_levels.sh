#!/bin/bash
# Run the load test at several class sizes against a fresh LOCAL server each time.
#   perf/run_levels.sh APP_DIR OUT_DIR [CPU_QUOTA] [LEVELS]
# CPU_QUOTA (optional) caps the server's CPU, e.g. 0.25 = a quarter of one core, to imitate a busy
# Streamlit Community Cloud container (needs Linux cgroup v1 write access; skipped if unavailable).
# The server is pinned to CPU 0 and the load generator to CPU 1 so they don't compete.
set -e
APP=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd); QUOTA=${3:-}; LEVELS=${4:-"1 5 10 20 30 40"}
HERE=$(cd "$(dirname "$0")" && pwd); PORT=8799; CG=/sys/fs/cgroup/cpu/jcc_perf
for N in $LEVELS; do
  (cd "$APP" && nohup taskset -c 0 python -m streamlit run juicetification.py --server.headless true \
     --server.port $PORT > "$OUT/server_$N.log" 2>&1 & echo $! > "$OUT/.pid")
  PID=$(cat "$OUT/.pid")
  if [ -n "$QUOTA" ] && mkdir -p $CG 2>/dev/null; then
    python3 -c "print(int(float('$QUOTA')*100000))" > $CG/cpu.cfs_quota_us; echo $PID > $CG/cgroup.procs
  fi
  sleep 20
  taskset -c 1 python "$HERE/loadtest.py" $PORT $N 90 $PID "$OUT/students_$N.json"
  kill $PID; sleep 4
done
