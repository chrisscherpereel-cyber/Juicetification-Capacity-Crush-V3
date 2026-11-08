"""Streamlit-aware load test for Capacity Crush.

Simulates N students, each a real websocket session speaking Streamlit's own protobuf protocol
(the same messages a browser sends), working through the Operations lab: Set up & run -> answer
-> Next, with 4-12 s of think time between clicks. Ordinary HTTP load tools cannot do this,
because a Streamlit page is one long-lived websocket session per user.

Usage:  python perf/loadtest.py PORT N_STUDENTS DURATION_S SERVER_PID OUT.json
Reports per-action latency (median / 95th percentile), server CPU use and memory (RSS).
Run it against a LOCAL server (see perf/README.md), never against the live class app."""
import asyncio, json, random, sys, time, statistics, os
import websockets
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
PORT, N, DUR, PID, OUT = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
THINK=(4.0, 12.0)
lat = {}       # action -> list of (first_response, complete)
errors = []; bytes_in=[0]

class Student:
    def __init__(s, idx): s.idx=idx; s.widgets=[]; s.page_hash=""; s.rng=random.Random(idx)
    async def run_script(s, ws, widget_states=None):
        m=BackMsg(); cs=m.rerun_script; cs.query_string=""; cs.page_script_hash=s.page_hash
        for w in (widget_states or []): cs.widget_states.widgets.append(w)
        t0=time.perf_counter(); first=None; widgets=[]
        await ws.send(m.SerializeToString())
        while True:
            raw=await asyncio.wait_for(ws.recv(), timeout=180)
            bytes_in[0]+=len(raw)
            f=ForwardMsg(); f.ParseFromString(raw)
            kind=f.WhichOneof("type")
            if kind=="new_session":
                s.page_hash=f.new_session.page_script_hash or s.page_hash
            elif kind=="delta":
                if first is None: first=time.perf_counter()-t0
                d=f.delta
                if d.WhichOneof("type")=="new_element":
                    el=d.new_element; et=el.WhichOneof("type")
                    if et in ("button","radio","number_input"):
                        w=getattr(el,et); widgets.append((et,w))
                    if et=="exception": errors.append(el.exception.message[:120])
            elif kind=="script_finished":
                if f.script_finished in (1,2) or True:
                    s.widgets=widgets or s.widgets
                    return (first if first is not None else time.perf_counter()-t0), time.perf_counter()-t0
    def find(s, et, pred):
        for t,w in s.widgets:
            if t==et and pred(w): return w
    async def act(s, ws, name, states):
        try:
            r=await s.run_script(ws, states); lat.setdefault(name,[]).append(r)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__} {e}"[:150])
    async def main(s, t_end):
        from streamlit.proto.WidgetStates_pb2 import WidgetState
        await asyncio.sleep(s.rng.uniform(0, min(10, DUR/4)))   # students arrive over ~10 s
        try:
            ws=await websockets.connect(f"ws://127.0.0.1:{PORT}/_stcore/stream", subprotocols=["streamlit"], max_size=None)
        except Exception as e:
            errors.append(f"connect: {e}"); return
        async with ws:
            await s.act(ws,"open app (first load)",[])
            while time.time()<t_end:
                await asyncio.sleep(s.rng.uniform(*THINK))
                run=s.find("button", lambda w: "Set up & run" in w.label or "Run my design" in w.label)
                if run is None: break
                await s.act(ws,"Set up & run this step",[WidgetState(id=run.id, trigger_value=True)])
                await asyncio.sleep(s.rng.uniform(*THINK))
                r=s.find("radio", lambda w: "Predict" in w.label or "Diagnose" in w.label)
                n=s.find("number_input", lambda w: "Estimate" in w.label)
                if r is not None and not r.disabled:
                    await s.act(ws,"answer a question",[WidgetState(id=r.id, int_value=0)])
                elif n is not None:
                    await s.act(ws,"answer a question",[WidgetState(id=n.id, double_value=5000.0)])
                await asyncio.sleep(s.rng.uniform(*THINK))
                nx=s.find("button", lambda w: w.label.strip().startswith("Next") and not w.disabled)
                if nx is not None:
                    await s.act(ws,"Next step",[WidgetState(id=nx.id, trigger_value=True)])

def proc_stats(pid):
    with open(f"/proc/{pid}/stat") as f: p=f.read().split()
    cpu=(int(p[13])+int(p[14]))/os.sysconf("SC_CLK_TCK")
    with open(f"/proc/{pid}/status") as f:
        rss=[int(l.split()[1]) for l in f if l.startswith("VmRSS")][0]/1024
    return cpu, rss

async def sampler(t_end, samples):
    while time.time()<t_end+5:
        samples.append((time.time(),)+proc_stats(PID)); await asyncio.sleep(1)

async def main():
    t_end=time.time()+DUR; samples=[]
    c0,r0=proc_stats(PID); w0=time.time()
    await asyncio.gather(sampler(t_end,samples), *[Student(i).main(t_end) for i in range(N)])
    c1,r1=proc_stats(PID); w1=time.time()
    def pct(xs,q): xs=sorted(xs); return xs[min(len(xs)-1,int(q*len(xs)))]
    res={"students":N,"duration_s":round(w1-w0,1),"server_cpu_util":round((c1-c0)/(w1-w0),3),
         "rss_start_mb":round(r0),"rss_peak_mb":round(max(s[2] for s in samples)),"rss_end_mb":round(r1),
         "mb_received":round(bytes_in[0]/1e6,1),"errors":len(errors),"error_samples":errors[:3],"actions":{}}
    for k,v in lat.items():
        fr=[a for a,b in v]; done=[b for a,b in v]
        res["actions"][k]={"n":len(v),"first_resp_median":round(statistics.median(fr),2),"first_resp_p95":round(pct(fr,.95),2),
                           "complete_median":round(statistics.median(done),2),"complete_p95":round(pct(done,.95),2)}
    json.dump(res,open(OUT,"w"),indent=1); print(json.dumps(res))
asyncio.run(main())
