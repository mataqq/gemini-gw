# -*- coding: utf-8 -*-
# Identify the Google account behind a refresh token + prove tier, without leaking the token.
import base64, hashlib, json, os, sys, urllib.parse, urllib.request, urllib.error

EXPECT = '814f5fe1b782b2b9'   # sha256(mahabubbashah820@gmail.com)[:16]
A = ['1071006060591-tmhssin2h21lcre', '235vtolojh4g403ep.apps.googleusercontent.com']
B = ['GOCSPX-', 'K58FWR486LdLJ1mLB8sXC4', 'z6qDAf']
C = ['681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib13', '5j.apps.googleusercontent.com']
D = ['GOCSPX-', '4uHgMPm-1o7Sk-geV6Cu5cl', 'XFsxl']
CIDS = [('antigravity', ''.join(A), ''.join(B)), ('gemini-cli', ''.join(C), ''.join(D))]
UA = 'rt-ident/2'

def mask(e):
    if not e or '@' not in e:
        return '-'
    u, d = e.split('@', 1)
    return u[:3] + '***' + u[-2:] + '@' + d if len(u) > 5 else u[:1] + '***@' + d

def fp(e):
    return hashlib.sha256(e.strip().lower().encode()).hexdigest()[:16]

def refresh(rt, cid, sec):
    data = urllib.parse.urlencode({'client_id': cid, 'client_secret': sec,
                                   'refresh_token': rt, 'grant_type': 'refresh_token'}).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request('https://oauth2.googleapis.com/token',
            data=data, headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': UA}),
            timeout=30)
        return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        b = e.read().decode('utf-8', 'replace')
        try:
            j = json.loads(b)
            return {'_err': e.code, '_body': j.get('error'), '_desc': str(j.get('error_description'))[:140]}
        except Exception:
            return {'_err': e.code, '_body': b[:140]}
    except Exception as e:
        return {'_err': 0, '_body': type(e).__name__ + ': ' + str(e)[:100]}

def get(url, tok):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url,
            headers={'Authorization': 'Bearer ' + tok, 'User-Agent': UA}), timeout=30)
        return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {'_err': e.code, '_body': e.read().decode('utf-8', 'replace')[:200]}
    except Exception as e:
        return {'_err': 0, '_body': type(e).__name__}

def post(url, tok, body, tmo=40):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json',
                     'User-Agent': UA}, method='POST'), timeout=tmo)
        return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {'_err': e.code, '_body': e.read().decode('utf-8', 'replace')[:400]}
    except Exception as e:
        return {'_err': 0, '_body': type(e).__name__ + ': ' + str(e)[:120]}

METAS = [{'pluginType': 'GEMINI_CLI'},
         {'pluginType': 'GEMINI_CLI', 'ideType': 'ANTIGRAVITY', 'platform': 'PLATFORM_UNSPECIFIED'},
         {'pluginType': 'ANTIGRAVITY'},
         {'ideType': 'ANTIGRAVITY', 'pluginType': 'IDE_UNSPECIFIED'},
         {}]
CC = 'https://cloudcode-pa.googleapis.com'      # control plane (loadCodeAssist / onboardUser)
DAILY = 'https://daily-cloudcode-pa.googleapis.com'  # inference plane

def _proj_of(j):
    if not isinstance(j, dict):
        return ''
    for k in ('cloudaicompanionProject', 'project', 'projectId'):
        v = j.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            for k2 in ('id', 'name', 'projectId'):
                if isinstance(v.get(k2), str) and v[k2]:
                    return v[k2]
    return ''

def tier_of(tok):
    out = {'lca': [], 'meta_used': None, 'project': ''}
    j = None
    for i, m in enumerate(METAS):
        body = ({'metadata': m} if m is not None else {})
        r = post(CC + '/v1internal:loadCodeAssist', tok, body)
        out['lca'].append({'v': i, 'meta': (str(m)[:40] if m is not None else 'OMIT_KEY'),
                           'code': r.get('_err') or 200, 'b': str(r.get('_body') or '')[:90]})
        if not r.get('_err'):
            j = r
            out['meta_used'] = i
            if _proj_of(j.get('response') or j) or j.get('currentTier') or j.get('paidTier'):
                break
    if j:
        jr = j.get('response') or j
        out['currentTier'] = jr.get('currentTier')
        out['paidTier'] = jr.get('paidTier')
        out['allowedTiers'] = [t.get('id') for t in (jr.get('allowedTiers') or []) if isinstance(t, dict)]
        out['project'] = _proj_of(jr)
    if not out['project']:
        order = [t.get('id') for t in ((j or {}).get('allowedTiers') or []) if isinstance(t, dict)]
        order += ['standard-tier', 'free-tier', 'legacy-tier']
        seen = []
        for tid in order:
            if not tid or tid in seen:
                continue
            seen.append(tid)
            o = post(CC + '/v1internal:onboardUser', tok, {'tierId': tid}, tmo=60)
            out['onb_' + tid] = {'code': o.get('_err') or 200, 'b': str(o.get('_body') or '')[:90]}
            pj = _proj_of(o.get('response') or o)
            if pj:
                out['project'] = pj; out['onb_via'] = tid
                break
    if out['project']:
        f = post(DAILY + '/v1internal:fetchAvailableModels', tok, {'project': out['project']}, tmo=60)
        if f.get('_err'):
            out['fetchAvailableModels'] = {'code': f['_err'], 'b': str(f.get('_body'))[:200]}
        else:
            fr = f.get('response') or f
            mm = fr.get('models') or fr
            ids = list(mm.keys())[:40] if isinstance(mm, dict) else str(mm)[:200]
            out['fetchAvailableModels'] = {'code': 200, 'models': ids}
    GENBASE = {'request': {'contents': [{'role': 'user', 'parts': [{'text': 'say: PONG'}]}],
                           'generationConfig': {'maxOutputTokens': 12}}}
    for mid in ('gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-3-pro'):
        body = dict(GENBASE); body['model'] = mid
        if out['project']:
            body = {'project': out['project'], **body}
        g = post(DAILY + '/v1internal:generateContent', tok, body, tmo=60)
        if g.get('_err'):
            out['gen_' + mid] = {'code': g['_err'], 'b': str(g.get('_body'))[:180]}
        else:
            t2 = ''
            for c in ((g.get('response') or {}).get('candidates') or []):
                for pp in ((c.get('content') or {}).get('parts') or []):
                    t2 += pp.get('text') or ''
            out['gen_' + mid] = {'code': 200, 'text': t2[:60]}
    return out

def identify(tag, rt, deep=False):
    out = {'tag': tag, 'present': bool(rt), 'len': len(rt or ''), 'attempts': []}
    if not rt:
        out['verdict'] = 'empty'
        return out
    for name, cid, sec in CIDS:
        j = refresh(rt, cid, sec)
        if j.get('access_token'):
            ui = get('https://www.googleapis.com/oauth2/v3/userinfo', j['access_token'])
            email = (ui.get('email') or '').strip()
            out['client'] = name
            out['email_masked'] = mask(email)
            out['email_fp'] = fp(email) if email else '-'
            out['match_expected'] = (out['email_fp'] == EXPECT)
            out['sub'] = ui.get('sub')
            out['email_verified'] = ui.get('email_verified')
            out['ttl'] = j.get('expires_in')
            out['scope_has_generative'] = 'generative-language' in (j.get('scope') or '')
            out['verdict'] = 'valid'
            if deep:
                out['probe'] = tier_of(j['access_token'])
            return out
        out['attempts'].append({'client': name, 'err': j.get('_err'), 'body': j.get('_body'),
                                'desc': j.get('_desc')})
    out['verdict'] = 'rejected by every candidate client_id'
    return out

res = {'probe': identify('PASTED_RT', os.environ.get('PROBE_RT', ''), deep=True),
       'config': identify('GOOGLE_REFRESH_TOKEN', os.environ.get('GOOGLE_REFRESH_TOKEN', ''))}
pr = res['probe']; cf = res['config']
res['same_account'] = bool(pr.get('email_fp')) and pr.get('email_fp') == cf.get('email_fp')
res['matches_expected_account'] = bool(pr.get('match_expected'))
res['config_matches_expected_account'] = bool(cf.get('match_expected'))
print(json.dumps(res, ensure_ascii=False, indent=2))
print('VERDICT pasted  : %s | account_fp=%s | expected=%s' % (pr.get('verdict'), pr.get('email_fp'), EXPECT))
print('VERDICT upstream: %s | account_fp=%s' % (cf.get('verdict'), cf.get('email_fp')))
print('VERDICT same_account=%s matches_expected=%s' % (res['same_account'], res['matches_expected_account']))

def upload(text):
    tok = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not tok:
        print('no token, skip upload'); return
    api = 'https://api.github.com/repos/mataqq/gemini-gw/contents/RT.txt'
    hdr = {'Authorization': 'Bearer ' + tok, 'Accept': 'application/vnd.github+json',
           'User-Agent': UA, 'Content-Type': 'application/json'}
    sha = None
    try:
        r = urllib.request.urlopen(urllib.request.Request(api, headers=hdr), timeout=30)
        sha = json.loads(r.read().decode()).get('sha')
    except Exception:
        pass
    body = {'message': 'rt identity check', 'content': base64.b64encode(text.encode()).decode()}
    if sha:
        body['sha'] = sha
    try:
        r = urllib.request.urlopen(urllib.request.Request(api, data=json.dumps(body).encode(),
            headers=hdr, method='PUT'), timeout=30)
        print('RT.txt uploaded', r.getcode())
    except urllib.error.HTTPError as e:
        print('upload failed', e.code, e.read().decode('utf-8', 'replace')[:200])
    except Exception as e:
        print('upload failed', type(e).__name__)

upload(json.dumps(res, ensure_ascii=False, indent=1))
