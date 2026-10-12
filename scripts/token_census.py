#!/usr/bin/env python3
"""Token census over the local Claude Code transcripts (~/.claude/projects).

Prints, for every session in the window: spend by model tier and role (main session vs subagent),
the largest sessions, the fixed per-session overhead, tool-result volume, repeat reads, agent
dispatches by (type, model), skill loads, session shape, cache re-write causes, fork inheritance and
the tiny SDK-launched sessions (sections A-K). Tokens are the truth; the dollar column is an API
list-price estimate used as a weight (subscription plans do not bill it).

Usage: python3 scripts/token_census.py --since 2026-08-15 [--until 2026-09-01] [--root DIR] [--csv FILE]
  --since  first day included (UTC), default 21 days ago
  --until  first day NOT included (UTC), default: no upper bound
  --root   transcripts folder, default ~/.claude/projects
  --csv    also write a per-session CSV
"""
import argparse, collections, csv, datetime, glob, json, os

# PRICES: API list-price ESTIMATES in USD per million tokens, one row per model-name substring:
# (input, cache write 1h, cache write 5m, cache read, output). They are examples that go stale:
# check your provider's price page and edit this table. A model whose name matches no key is
# priced as 'opus'. Add a row for any tier you use. A model takes the FIRST key its name contains,
# so a more specific key must come before a broader one ('fable-5-1' before 'fable').
PRICE = {
    'fable-5-1': (10, 20, 12.5, 0.25, 50),  # Fable 5.1: cache read 0.025x input
    'fable': (10, 20, 12.5, 1.0, 50),       # Fable 5: cache read 0.1x input
    'opus-5-5': (4, 8, 5, 0.2, 20),         # Opus 5.5: cache read 0.05x input
    'opus': (5, 10, 6.25, 0.5, 25),
    'sonnet': (2, 4, 2.5, 0.2, 10),
    'haiku': (1, 2, 1.25, 0.1, 5),
}

def tier(m):
    m = m or ''
    for k in PRICE:
        if k in m:
            return k
    return 'opus'

def cost(t, u):
    p = PRICE[t]; cc = u.get('cache_creation') or {}
    w1 = cc.get('ephemeral_1h_input_tokens', 0) or 0; w5 = cc.get('ephemeral_5m_input_tokens', 0) or 0
    if not w1 and not w5:
        w5 = u.get('cache_creation_input_tokens', 0) or 0
    return ((u.get('input_tokens', 0) or 0) * p[0] + w1 * p[1] + w5 * p[2] + (u.get('cache_read_input_tokens', 0) or 0) * p[3] + (u.get('output_tokens', 0) or 0) * p[4]) / 1e6

def ts(d):
    t = d.get('timestamp')
    if not t:
        return None
    try:
        return datetime.datetime.fromisoformat(t.replace('Z', '+00:00'))
    except Exception:
        return None

def new_session():
    return dict(proj='', title='', tiers=collections.Counter(), calls=0, subcalls=0, prompts=0, prompt_chars=0, ctx_sum=0, ctx_max=0,
                first_ctx=None, cr=0, cw=0, out=0, think=0, cost=0.0, subcost=0.0, compactions=0, reread_n=0, reread_chars=0,
                big200=0, big400=0, forks=0, agents=0, entry=None, models=set())

def main():
    ap = argparse.ArgumentParser(description='Token census over local Claude Code transcripts. Reads files only; sends nothing anywhere. '
                                             'Dollar figures are API list-price estimates used as a weight, not a bill.')
    ap.add_argument('--since', default=(datetime.date.today() - datetime.timedelta(days=21)).isoformat(),
                    metavar='YYYY-MM-DD', help='first day included (UTC); default 21 days ago')
    ap.add_argument('--until', default='', metavar='YYYY-MM-DD', help='first day NOT included (UTC); default: no upper bound')
    ap.add_argument('--root', default=os.path.expanduser('~/.claude/projects'), metavar='DIR', help='transcripts folder; default ~/.claude/projects')
    ap.add_argument('--csv', default='', metavar='FILE', help='also write a per-session CSV (it includes session titles)')
    a = ap.parse_args()
    SINCE = datetime.datetime.fromisoformat(a.since).replace(tzinfo=datetime.timezone.utc); since_m = SINCE.timestamp()
    UNTIL = datetime.datetime.fromisoformat(a.until).replace(tzinfo=datetime.timezone.utc) if a.until else None
    home_prefix = os.path.expanduser('~').replace('/', '-')
    S = collections.defaultdict(new_session)
    BY = collections.defaultdict(collections.Counter); TOOL = collections.defaultdict(lambda: [0, 0]); BIG = []
    AG = collections.Counter(); AGP = collections.Counter(); SK = collections.Counter(); RR = collections.Counter()
    RW = collections.defaultdict(lambda: [0, 0, 0.0]); FIRST = [0, 0, 0.0]; SUBFIRST = [0, 0, 0.0]
    sub_inherit = [0, 0, 0.0]; sub_fresh = [0, 0, 0.0]; sub_first_ctx = []

    def title_for(projdir, sid):
        p = os.path.join(projdir, sid, 'custom-title.json')
        if os.path.exists(p):
            try:
                j = json.load(open(p)); return str(j.get('title') or j.get('customTitle') or j)[:60]
            except Exception:
                pass
        return ''

    def parse(path, sid, proj, file_is_sub):
        st = S[sid]; st['proj'] = st['proj'] or proj
        names = {}; seen = {}; reads = set()
        with open(path, errors='ignore') as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                t = ts(d)
                if d.get('type') == 'ai-title' and not st['title']:
                    st['title'] = str(d.get('title', ''))[:60]
                if t is None or t < SINCE or (UNTIL is not None and t >= UNTIL):
                    continue
                st['entry'] = st['entry'] or d.get('entrypoint')
                sub = file_is_sub or bool(d.get('isSidechain')); typ = d.get('type')
                if typ == 'summary':
                    st['compactions'] += 1
                if typ == 'assistant':
                    m = d.get('message') or {}; u = m.get('usage'); mid = m.get('id')
                    for c in m.get('content') or []:
                        if not isinstance(c, dict) or c.get('type') != 'tool_use':
                            continue
                        name = c.get('name') or '?'; inp = c.get('input') if isinstance(c.get('input'), dict) else {}
                        brief = str(inp.get('file_path') or inp.get('command') or inp.get('pattern') or inp.get('skill') or inp.get('description') or '')[:80]
                        names[c.get('id')] = (name, brief, inp)
                        if name == 'Agent':
                            st['agents'] += 1; styp = inp.get('subagent_type') or 'general-purpose'; mdl = inp.get('model') or 'unset'
                            AG[(styp, mdl)] += 1; AGP[(styp, mdl)] += len(str(inp.get('prompt', '')))
                            if styp == 'fork':
                                st['forks'] += 1
                        if name == 'Skill':
                            SK[str(inp.get('skill') or '?')] += 1
                    if u and mid:
                        if mid in seen:
                            seen[mid]['output_tokens'] = max(seen[mid].get('output_tokens', 0) or 0, u.get('output_tokens', 0) or 0); continue
                        u = dict(u); u['_tier'] = tier(m.get('model')); u['_model'] = m.get('model'); u['_sub'] = sub; u['_ts'] = t; seen[mid] = u
                elif typ == 'user':
                    m = d.get('message') or {}; content = m.get('content')
                    if isinstance(content, str):
                        if not d.get('isMeta') and not sub:
                            st['prompts'] += 1; st['prompt_chars'] += len(content)
                        if 'continued from a previous conversation' in content:
                            st['compactions'] += 1
                    elif isinstance(content, list):
                        has_tr = False
                        for c in content:
                            if not isinstance(c, dict):
                                continue
                            if c.get('type') == 'tool_result':
                                has_tr = True; cc = c.get('content')
                                n = len(cc) if isinstance(cc, str) else sum(len(x.get('text', '')) for x in cc if isinstance(x, dict)) if isinstance(cc, list) else 0
                                name, brief, inp = names.get(c.get('tool_use_id'), ('?', '', {}))
                                TOOL[name][0] += 1; TOOL[name][1] += n; BIG.append((n, name, brief, sid[:8]))
                                if name == 'Read':
                                    fp = inp.get('file_path')
                                    if fp in reads:
                                        st['reread_n'] += 1; st['reread_chars'] += n; RR[fp] += 1
                                    else:
                                        reads.add(fp)
                        if not has_tr and not d.get('isMeta') and not sub:
                            st['prompts'] += 1; st['prompt_chars'] += sum(len(c.get('text', '')) for c in content if isinstance(c, dict))
        order = list(seen.values()); prev = None
        for i, u in enumerate(order):
            t = u['_tier']; sub = u['_sub']; k = (t, 'sub' if sub else 'main'); c = cost(t, u)
            cw = u.get('cache_creation_input_tokens', 0) or 0; cr = u.get('cache_read_input_tokens', 0) or 0; inp = u.get('input_tokens', 0) or 0
            ctx = inp + cr + cw; B = BY[k]
            B['calls'] += 1; B['inp'] += inp; B['cr'] += cr; B['cw'] += cw; B['out'] += u.get('output_tokens', 0) or 0
            B['think'] += ((u.get('output_tokens_details') or {}).get('thinking_tokens', 0) or 0); B['ctx'] += ctx; B['cost'] += c
            B['ctx200'] += ctx > 200000; B['ctx400'] += ctx > 400000
            st['tiers'][t] += 1; st['calls'] += 1; st['subcalls'] += int(sub); st['ctx_sum'] += ctx; st['ctx_max'] = max(st['ctx_max'], ctx)
            st['cr'] += cr; st['cw'] += cw; st['out'] += u.get('output_tokens', 0) or 0; st['cost'] += c; st['subcost'] += c if sub else 0
            st['think'] += ((u.get('output_tokens_details') or {}).get('thinking_tokens', 0) or 0)
            st['big200'] += ctx > 200000; st['big400'] += ctx > 400000
            if not sub:
                st['models'].add(u['_model'])
                if st['first_ctx'] is None:
                    st['first_ctx'] = ctx; FIRST[0] += 1; FIRST[1] += cw + inp; FIRST[2] += cost(t, {'cache_creation': {'ephemeral_1h_input_tokens': cw}, 'input_tokens': inp})
                elif cw > 50000 and prev is not None:
                    gap = (u['_ts'] - prev['_ts']).total_seconds() / 60
                    cause = 'idle>55min (cache TTL)' if gap > 55 else ('model switch' if u['_model'] != prev['_model'] else ('idle 10-55min' if gap > 10 else 'prefix change/compaction'))
                    RW[cause][0] += 1; RW[cause][1] += cw; RW[cause][2] += cost(t, {'cache_creation': {'ephemeral_1h_input_tokens': cw}})
                prev = u
        if file_is_sub and order:
            f0 = order[0]; ctx0 = (f0.get('input_tokens', 0) or 0) + (f0.get('cache_read_input_tokens', 0) or 0) + (f0.get('cache_creation_input_tokens', 0) or 0)
            sub_first_ctx.append(ctx0); SUBFIRST[0] += 1; SUBFIRST[1] += (f0.get('cache_creation_input_tokens', 0) or 0) + (f0.get('input_tokens', 0) or 0)
            tgt = sub_inherit if ctx0 > 60000 else sub_fresh
            tgt[0] += 1; tgt[1] += len(order); tgt[2] += sum(cost(x['_tier'], x) for x in order)

    for projdir in sorted(glob.glob(a.root + '/*')):
        proj = os.path.basename(projdir)
        if proj.startswith(home_prefix + '-'):
            proj = '~' + proj[len(home_prefix):]
        proj = proj[:34]
        for fp in glob.glob(projdir + '/*.jsonl'):
            if os.path.getmtime(fp) >= since_m:
                sid = os.path.basename(fp)[:-6]; parse(fp, sid, proj, False); S[sid]['title'] = S[sid]['title'] or title_for(projdir, sid)
        for fp in glob.glob(projdir + '/*/subagents/*.jsonl'):
            if os.path.getmtime(fp) >= since_m:
                sid = fp.split('/')[-3]; parse(fp, sid, proj, True)

    M = lambda x: f"{x / 1e6:8.1f}M"; K = lambda x: f"{x / 1e3:6.0f}k"; act = [st for st in S.values() if st['calls']]
    window = f"{a.since} to {a.until}" if a.until else f"{a.since}"
    print(f"== A. Spend by tier and role since {window} (sessions={len(act)}) ==")
    print(f"{'tier/role':12}{'calls':>7}{'ctx-tok proc':>13}{'cache_read':>11}{'cache_wr':>10}{'output':>9}{'thinking':>10}{'>200k':>7}{'>400k':>7}{'est $':>8}")
    tot = 0
    for k in sorted(BY, key=lambda k: -BY[k]['cost']):
        B = BY[k]; tot += B['cost']
        print(f"{k[0] + '/' + k[1]:12}{B['calls']:7d}{M(B['ctx']):>13}{M(B['cr']):>11}{M(B['cw']):>10}{M(B['out']):>9}{M(B['think']):>10}{B['ctx200']:7d}{B['ctx400']:7d}{B['cost']:8.0f}")
    allctx = sum(B['ctx'] for B in BY.values()); allcr = sum(B['cr'] for B in BY.values())
    print(f"TOTAL est ${tot:.0f} | context processed {allctx / 1e9:.1f}B tokens, {100 * allcr / max(allctx, 1):.1f}% cache re-reads | user prompt tokens {sum(st['prompt_chars'] for st in act) / 4e6:.1f}M")
    print("\n== B. Top 15 sessions by est $ ==")
    print(f"{'proj':22}{'sid':9}{'tier':7}{'calls':>6}{'sub%':>5}{'prompts':>8}{'ctxmax':>8}{'ctxproc':>10}{'out':>7}{'compact':>8}{'reread':>7}{'forks':>6}{'$':>6}  title")
    top = sorted(act, key=lambda s: -s['cost'])
    for st in top[:15]:
        sid = next(k for k, v in S.items() if v is st)
        print(f"{st['proj'][:21]:22}{sid[:8]:9}{st['tiers'].most_common(1)[0][0]:7}{st['calls']:6d}{100 * st['subcost'] / max(st['cost'], 1e-9):5.0f}{st['prompts']:8d}{K(st['ctx_max']):>8}{M(st['ctx_sum']):>10}{K(st['out']):>7}{st['compactions']:8d}{st['reread_n']:7d}{st['forks']:6d}{st['cost']:6.0f}  {st['title'][:40]}")
    print(f"share of top 18 sessions: {100 * sum(s['cost'] for s in top[:18]) / max(tot, 1e-9):.0f}%")
    fc = sorted(st['first_ctx'] for st in act if st['first_ctx'])
    if fc:
        print(f"\n== C. Fixed overhead (first main call context): n={len(fc)} p25={fc[len(fc) // 4] // 1000}k median={fc[len(fc) // 2] // 1000}k p75={fc[3 * len(fc) // 4] // 1000}k ==")
    print("\n== D. Tool-result volume by tool (chars/4 ~ tokens) ==")
    for name, (n, ch) in sorted(TOOL.items(), key=lambda kv: -kv[1][1])[:10]:
        print(f"{name[:28]:29}{n:7d} calls {ch / 4e6:8.2f}M tok  avg {ch / 4 / max(n, 1):7.0f}")
    for n, name, brief, s8 in sorted(BIG, reverse=True)[:5]:
        print(f"  largest: {n / 4e3:6.0f}k tok {name:8} {s8} {brief[:60]}")
    print(f"\n== E. Re-reads of the same file within a transcript: {sum(st['reread_n'] for st in act)} (~{sum(st['reread_chars'] for st in act) / 4e6:.1f}M tok before carry) ==")
    for fpath, n in RR.most_common(5):
        print(f"  {n:4d}  {str(fpath)[-80:]}")
    print("\n== F. Agent dispatches by (subagent_type, model param) ==")
    unset = sum(n for (s, m), n in AG.items() if m == 'unset'); alln = sum(AG.values())
    print(f"  total {alln}, model unset {unset} ({100 * unset / max(alln, 1):.0f}%)")
    for (styp, mdl), n in sorted(AG.items(), key=lambda kv: -kv[1])[:14]:
        print(f"  {n:5d}  {styp[:34]:35} {mdl:8} avg brief {AGP[(styp, mdl)] / max(n, 1) / 4:6.0f} tok")
    print("\n== G. Skill loads ==")
    print('  ' + ', '.join(f"{k} {v}" for k, v in SK.most_common(14)))
    pr = sorted(st['prompts'] for st in act if st['subcalls'] < st['calls']); cl = sorted(st['calls'] for st in act if st['subcalls'] < st['calls'])
    if pr:
        print(f"\n== H. Session shape: main sessions={len(pr)} prompts/session median={pr[len(pr) // 2]} p90={pr[int(len(pr) * .9)]} | calls/session median={cl[len(cl) // 2]} p90={cl[int(len(cl) * .9)]} | sessions with a call >200k: {sum(1 for st in act if st['big200'])}, >400k: {sum(1 for st in act if st['big400'])} | switched model partway: {sum(1 for st in act if len(st['models']) > 1)} ==")
    print("\n== I. Cache re-write events on main calls (cache write >50k, not the first call) ==")
    for k, v in sorted(RW.items(), key=lambda kv: -kv[1][2]):
        print(f"  {k:26} events={v[0]:5d} tokens={v[1] / 1e6:7.0f}M  est ${v[2]:6.0f}")
    print(f"  session-start writes: n={FIRST[0]} tokens={FIRST[1] / 1e6:.0f}M ${FIRST[2]:.0f} | subagent starts: n={SUBFIRST[0]} tokens={SUBFIRST[1] / 1e6:.0f}M")
    s = sorted(sub_first_ctx)
    if s:
        print(f"\n== J. Subagents by inheritance: started >60k ctx (forks/inherited) n={sub_inherit[0]} calls={sub_inherit[1]} ${sub_inherit[2]:.0f} | fresh n={sub_fresh[0]} calls={sub_fresh[1]} ${sub_fresh[2]:.0f} | first-call ctx median={s[len(s) // 2] // 1000}k p90={s[int(len(s) * .9)] // 1000}k ==")
    tiny = collections.defaultdict(lambda: [0, 0.0])
    for st in act:
        if st['prompts'] <= 2 and st['subcalls'] < st['calls']:
            tiny[st['entry'] or '?'][0] += 1; tiny[st['entry'] or '?'][1] += st['cost']
    print("\n== K. Sessions with <=2 prompts, by entrypoint ==")
    for k, v in sorted(tiny.items(), key=lambda kv: -kv[1][1]):
        print(f"  {k:14} n={v[0]:5d} est ${v[1]:6.0f}")
    if a.csv:
        with open(a.csv, 'w') as f:
            w = csv.writer(f); w.writerow(['proj', 'sid', 'title', 'tier', 'calls', 'subcalls', 'prompts', 'ctx_max', 'ctx_sum', 'cache_read', 'cache_write', 'out', 'think', 'cost', 'subcost', 'compactions', 'rereads', 'forks', 'agents', 'entry'])
            for sid, st in S.items():
                if st['calls']:
                    w.writerow([st['proj'], sid, st['title'], st['tiers'].most_common(1)[0][0], st['calls'], st['subcalls'], st['prompts'], st['ctx_max'], st['ctx_sum'], st['cr'], st['cw'], st['out'], st['think'], round(st['cost'], 2), round(st['subcost'], 2), st['compactions'], st['reread_n'], st['forks'], st['agents'], st['entry']])
        print(f"\nper-session CSV: {a.csv}")

if __name__ == '__main__':
    main()
